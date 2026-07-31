from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager

import pytest

from decision_workbench.application.data_lifecycle import DataLifecycleService
from decision_workbench.contracts.data_lifecycle_contracts import (
    CurationRecipeCreateInput,
    CurationRunCreateInput,
    ObjectSelection,
    SourceConnectorCreateInput,
    SourceFetchRequest,
)
from decision_workbench.persistence.store import Store
from decision_workbench.persistence.data_lifecycle_payload_storage import (
    LifecyclePayloadUnavailableError,
    StoredLifecycleRowResource,
)
from decision_workbench.persistence.data_lifecycle_row_index import (
    rebuild_row_index,
)


def _connector(name: str) -> SourceConnectorCreateInput:
    return SourceConnectorCreateInput(
        name=name,
        connector_type="object_storage_json_v1",
        source_locator=f"fixture://{name}",
        selection=ObjectSelection(format="json_array", primary_key="id"),
    )


def _recipe() -> CurationRecipeCreateInput:
    return CurationRecipeCreateInput(
        recipe_id="pagination",
        version=1,
        name="Pagination",
        steps=(
            {"kind": "coerce_number_v1", "fields": ["x", "target"]},
            {"kind": "required_fields_v1", "fields": ["id", "x"]},
            {"kind": "target_eligibility_v1", "fields": ["target"]},
        ),
    )


def test_summary_excludes_rows_and_pages_keep_stable_identity(
    tmp_path, monkeypatch
) -> None:
    database = tmp_path / "workbench.db"
    Store(database)
    service = DataLifecycleService(database)
    connector = service.create_connector(_connector("paged"))
    recipe = service.create_recipe(_recipe())
    content = json.dumps(
        [
            {"id": f"row-{index:03d}", "x": index, "target": index * 2}
            for index in range(205)
        ]
    )
    raw, _ = service.fetch(
        connector.id,
        SourceFetchRequest(object_content=content, object_version="205"),
    )
    run = service.curate(
        raw.id,
        CurationRunCreateInput(
            recipe_resource_id=recipe.id,
            profile_revision_id="profile@1",
            profile_digest="sha256:profile",
        ),
    )

    summary = service.detail(connector.id)
    encoded = summary.model_dump_json()
    assert '"rows"' not in encoded
    assert summary.raw_snapshots[0].snapshot_digest == raw.snapshot_digest
    assert summary.curation_runs[0].curation_digest == run.curation_digest
    assert summary.curation_runs[0].quality == run.quality

    raw_page = service.raw_row_page(raw.id, offset=200, limit=50)
    assert raw_page.snapshot_digest == raw.snapshot_digest
    assert raw_page.total == 205
    assert raw_page.has_more is False
    assert [row["id"] for row in raw_page.rows] == [
        f"row-{index:03d}" for index in range(200, 205)
    ]
    curation_page = service.curation_row_page(run.id, offset=100, limit=50)
    assert curation_page.raw_snapshot_digest == raw.snapshot_digest
    assert curation_page.curation_digest == run.curation_digest
    assert [row.raw_row_index for row in curation_page.rows] == list(
        range(100, 150)
    )
    boundary = service.curation_row_page(run.id, offset=99, limit=2)
    assert [
        (row.raw_row_index, row.row_key) for row in boundary.rows
    ] == [(99, "row-099"), (100, "row-100")]

    def reject_full_scan(*_args, **_kwargs):
        raise AssertionError("page reads must not scan the complete CAS object")

    monkeypatch.setattr(service.repository.row_payloads, "_scan", reject_full_scan)
    statements: list[str] = []
    original_connect = service.repository._connect

    @contextmanager
    def traced_connection():
        with original_connect() as connection:
            connection.set_trace_callback(statements.append)
            yield connection

    monkeypatch.setattr(service.repository, "_connect", traced_connection)
    assert service.raw_row_page(raw.id, offset=200, limit=5).rows[0]["id"] == (
        "row-200"
    )
    assert service.curation_row_page(run.id, offset=200, limit=5).rows[
        0
    ].row_key == "row-200"
    assert not any("COUNT(" in statement.upper() for statement in statements)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "DELETE FROM data_lifecycle_row_index "
            "WHERE resource_kind='curation_run' AND resource_id=? "
            "AND sort_ordinal=100",
            (run.id,),
        )
    with pytest.raises(
        LifecyclePayloadUnavailableError,
        match="positions are not contiguous",
    ):
        service.curation_row_page(run.id, offset=99, limit=2)


def test_curation_status_filter_finds_quarantine_after_first_hundred_rows(
    tmp_path,
) -> None:
    database = tmp_path / "workbench.db"
    Store(database)
    service = DataLifecycleService(database)
    connector = service.create_connector(_connector("filtered"))
    recipe = service.create_recipe(_recipe())
    content = json.dumps(
        [
            {"id": f"accepted-{index:03d}", "x": index, "target": index * 2}
            for index in range(100)
        ]
        + [{"id": "quarantined-101", "target": 202}]
    )
    raw, _ = service.fetch(
        connector.id,
        SourceFetchRequest(object_content=content, object_version="101"),
    )
    run = service.curate(
        raw.id,
        CurationRunCreateInput(
            recipe_resource_id=recipe.id,
            profile_revision_id="profile@1",
            profile_digest="sha256:profile",
        ),
    )

    page = service.curation_row_page(
        run.id,
        offset=0,
        limit=50,
        status="quarantined",
    )
    assert page.status_filter == "quarantined"
    assert page.total == 1
    assert page.has_more is False
    assert [row.row_key for row in page.rows] == ["quarantined-101"]
    reasoned = service.curation_row_page(
        run.id,
        offset=0,
        limit=50,
        reasoned_only=True,
    )
    assert reasoned.reasoned_only is True
    assert reasoned.total == 1
    assert [row.row_key for row in reasoned.rows] == ["quarantined-101"]
    with sqlite3.connect(database) as connection:
        accepted_pair = connection.execute(
            "SELECT ordinal,status_ordinal FROM data_lifecycle_row_index "
            "WHERE resource_kind='curation_run' AND resource_id=? "
            "AND status='accepted' ORDER BY status_ordinal LIMIT 2",
            (run.id,),
        ).fetchall()
        connection.execute(
            "UPDATE data_lifecycle_row_index SET status_ordinal=NULL "
            "WHERE resource_kind='curation_run' AND resource_id=? "
            "AND ordinal=?",
            (run.id, accepted_pair[0][0]),
        )
        connection.execute(
            "UPDATE data_lifecycle_row_index SET status_ordinal=? "
            "WHERE resource_kind='curation_run' AND resource_id=? "
            "AND ordinal=?",
            (accepted_pair[0][1], run.id, accepted_pair[1][0]),
        )
        connection.execute(
            "UPDATE data_lifecycle_row_index SET status_ordinal=? "
            "WHERE resource_kind='curation_run' AND resource_id=? "
            "AND ordinal=?",
            (accepted_pair[1][1], run.id, accepted_pair[0][0]),
        )
    with pytest.raises(
        LifecyclePayloadUnavailableError,
        match="stable sort does not match",
    ):
        service.curation_row_page(
            run.id,
            offset=1,
            limit=1,
            status="accepted",
        )
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE data_lifecycle_row_index SET status_ordinal=NULL "
            "WHERE resource_kind='curation_run' AND resource_id=? "
            "AND ordinal=?",
            (run.id, accepted_pair[0][0]),
        )
        connection.execute(
            "UPDATE data_lifecycle_row_index SET status_ordinal=? "
            "WHERE resource_kind='curation_run' AND resource_id=? "
            "AND ordinal=?",
            (accepted_pair[1][1], run.id, accepted_pair[1][0]),
        )
        connection.execute(
            "UPDATE data_lifecycle_row_index SET status_ordinal=? "
            "WHERE resource_kind='curation_run' AND resource_id=? "
            "AND ordinal=?",
            (accepted_pair[0][1], run.id, accepted_pair[0][0]),
        )
    with sqlite3.connect(database) as connection:
        accepted = connection.execute(
            "SELECT ordinal,status_ordinal FROM data_lifecycle_row_index "
            "WHERE resource_kind='curation_run' AND resource_id=? "
            "AND status='accepted' ORDER BY status_ordinal LIMIT 1",
            (run.id,),
        ).fetchone()
        quarantined = connection.execute(
            "SELECT ordinal,status_ordinal FROM data_lifecycle_row_index "
            "WHERE resource_kind='curation_run' AND resource_id=? "
            "AND status='quarantined' ORDER BY status_ordinal LIMIT 1",
            (run.id,),
        ).fetchone()
        connection.execute(
            "UPDATE data_lifecycle_row_index "
            "SET status='__swap__',status_ordinal=NULL "
            "WHERE resource_kind='curation_run' AND resource_id=? "
            "AND ordinal=?",
            (run.id, accepted[0]),
        )
        connection.execute(
            "UPDATE data_lifecycle_row_index "
            "SET status='accepted',status_ordinal=? "
            "WHERE resource_kind='curation_run' AND resource_id=? "
            "AND ordinal=?",
            (accepted[1], run.id, quarantined[0]),
        )
        connection.execute(
            "UPDATE data_lifecycle_row_index "
            "SET status='quarantined',status_ordinal=? "
            "WHERE resource_kind='curation_run' AND resource_id=? "
            "AND ordinal=?",
            (quarantined[1], run.id, accepted[0]),
        )
    with pytest.raises(
        LifecyclePayloadUnavailableError,
        match="metadata does not match",
    ):
        service.curation_row_page(
            run.id,
            offset=0,
            limit=50,
            status="quarantined",
        )


def test_curation_index_defines_global_order_across_physical_line_order(
    tmp_path,
) -> None:
    database = tmp_path / "workbench.db"
    Store(database)
    service = DataLifecycleService(database)
    connector = service.create_connector(_connector("global-order"))
    recipe = service.create_recipe(_recipe())
    raw, _ = service.fetch(
        connector.id,
        SourceFetchRequest(
            object_content=json.dumps(
                [
                    {"id": f"row-{index}", "x": index, "target": index}
                    for index in range(4)
                ]
            ),
            object_version="4",
        ),
    )
    run = service.curate(
        raw.id,
        CurationRunCreateInput(
            recipe_resource_id=recipe.id,
            profile_revision_id="profile@1",
            profile_digest="sha256:profile",
        ),
    )
    reference = service.repository.row_payloads.write(
        reversed(run.rows),
        record_kind="curated-row/v1",
    )
    with sqlite3.connect(database) as connection:
        stored = StoredLifecycleRowResource.model_validate_json(
            connection.execute(
                "SELECT payload FROM source_curation_runs WHERE id=?",
                (run.id,),
            ).fetchone()[0]
        ).model_copy(update={"row_payload": reference})
        connection.execute(
            "UPDATE source_curation_runs SET payload=?,row_payload_sha256=?,"
            "row_payload_bytes=?,row_count=? WHERE id=?",
            (
                stored.model_dump_json(),
                reference.sha256,
                reference.size_bytes,
                reference.row_count,
                run.id,
            ),
        )
        rebuild_row_index(
            connection,
            service.repository.row_payloads,
            resource_kind="curation_run",
            resource_id=run.id,
            reference=reference,
        )

    with pytest.raises(sqlite3.IntegrityError):
        with sqlite3.connect(database) as connection:
            connection.execute(
                "UPDATE data_lifecycle_row_index SET sort_ordinal=0 "
                "WHERE resource_kind='curation_run' AND resource_id=? "
                "AND sort_ordinal=1",
                (run.id,),
            )
    first = service.curation_row_page(run.id, offset=0, limit=2)
    second = service.curation_row_page(run.id, offset=2, limit=2)
    assert [row.raw_row_index for row in (*first.rows, *second.rows)] == [
        0,
        1,
        2,
        3,
    ]


def test_page_rejects_manifest_for_another_cas_and_records_finding(
    tmp_path,
) -> None:
    database = tmp_path / "workbench.db"
    Store(database)
    service = DataLifecycleService(database)
    connector = service.create_connector(_connector("manifest"))
    raw, _ = service.fetch(
        connector.id,
        SourceFetchRequest(
            object_content='[{"id":"row-1","x":1,"target":2}]',
            object_version="1",
        ),
    )
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE data_lifecycle_row_index_manifests "
            "SET payload_sha256=? WHERE resource_kind='raw_source_snapshot' "
            "AND resource_id=?",
            ("0" * 64, raw.id),
        )

    with pytest.raises(
        LifecyclePayloadUnavailableError,
        match="manifest does not match",
    ):
        service.raw_row_page(raw.id, offset=0, limit=1)
    with sqlite3.connect(database) as connection:
        finding = connection.execute(
            "SELECT reason FROM data_lifecycle_payload_findings "
            "WHERE resource_kind='raw_source_snapshot' AND resource_id=?",
            (raw.id,),
        ).fetchone()
    assert finding is not None
    assert "manifest does not match" in finding[0]


def test_summary_filters_connector_in_sql_before_decoding(tmp_path) -> None:
    database = tmp_path / "workbench.db"
    Store(database)
    service = DataLifecycleService(database)
    target = service.create_connector(_connector("target"))
    unrelated = service.create_connector(_connector("unrelated"))
    target_raw, _ = service.fetch(
        target.id,
        SourceFetchRequest(
            object_content='[{"id":"A","x":1,"target":2}]',
            object_version="target",
        ),
    )
    unrelated_raw, _ = service.fetch(
        unrelated.id,
        SourceFetchRequest(
            object_content='[{"id":"B","x":3,"target":4}]',
            object_version="unrelated",
        ),
    )
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE raw_source_snapshots SET summary_payload='not-json' "
            "WHERE id=?",
            (unrelated_raw.id,),
        )

    summary = service.detail(target.id)
    assert tuple(item.id for item in summary.raw_snapshots) == (target_raw.id,)
