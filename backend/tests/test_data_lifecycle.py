from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from pydantic import ValidationError

from material_workbench.application.data_lifecycle import (
    DataLifecycleService,
    SourceFetchFailedError,
)
from material_workbench.contracts.data_lifecycle_contracts import (
    CurationRecipeCreateInput,
    CurationRunCreateInput,
    DatasetApprovalInput,
    ObjectSelection,
    SourceConnectorCreateInput,
    SourceFetchRequest,
    TrainingSnapshotCreateInput,
)
from material_workbench.domain.data_lifecycle import LifecycleConflictError
from material_workbench.execution.inference_work_graph import semantic_digest
from material_workbench.persistence.store import Store
from material_workbench.persistence.data_lifecycle_repository import (
    LifecycleResourceConflictError,
)


def _connector() -> SourceConnectorCreateInput:
    return SourceConnectorCreateInput(
        name="品質確認用object",
        connector_type="object_storage_json_v1",
        source_locator="s3://demo-bucket/quality/latest.json",
        selection=ObjectSelection(
            format="json_array",
            primary_key="id",
        ),
    )


def _recipe() -> CurationRecipeCreateInput:
    return CurationRecipeCreateInput(
        recipe_id="quality-feed",
        version=1,
        name="品質feedの意味正規化",
        steps=(
            {"kind": "trim_strings_v1", "fields": ["id", "x"]},
            {"kind": "coerce_number_v1", "fields": ["x", "target"]},
            {"kind": "required_fields_v1", "fields": ["id", "x"]},
            {"kind": "target_eligibility_v1", "fields": ["target"]},
            {
                "kind": "sum_limit_v1",
                "fields": ["x"],
                "maximum": 10,
                "on_violation": "warning",
            },
        ),
    )


V1 = """[
  {"id":"A","x":" 1 ","target":"10"},
  {"id":"B","x":"2","target":"20"}
]"""
V2 = """[
  {"id":"A","x":"2","target":"11"},
  {"id":"C","x":"","target":"12"},
  {"id":"D","x":"4"},
  {"id":"E","x":"20","target":"14"}
]"""


def test_lifecycle_migration_is_additive_and_idempotent(tmp_path) -> None:
    database = tmp_path / "workbench.db"
    Store(database)
    Store(database)

    with sqlite3.connect(database) as conn:
        marker = conn.execute(
            "SELECT checksum FROM schema_migrations "
            "WHERE id='source-data-lifecycle-v1'"
        ).fetchone()
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }

    assert marker == ("connector-raw-curation-approval-training-v1",)
    assert {
        "source_connectors",
        "source_fetch_attempts",
        "raw_source_snapshots",
        "curation_recipes",
        "source_curation_runs",
        "canonical_dataset_approvals",
        "approved_training_snapshots",
    } <= tables


def test_multiple_raw_snapshots_are_immutable_and_have_keyed_diff(tmp_path) -> None:
    database = tmp_path / "workbench.db"
    Store(database)
    service = DataLifecycleService(database)
    connector = service.create_connector(_connector())

    first, _ = service.fetch(
        connector.id,
        SourceFetchRequest(object_content=V1, object_version="etag-1"),
    )
    second, _ = service.fetch(
        connector.id,
        SourceFetchRequest(object_content=V2, object_version="etag-2"),
    )
    duplicate, attempt = service.fetch(
        connector.id,
        SourceFetchRequest(object_content=V2, object_version="etag-2-repeat"),
    )

    assert first.rows[0]["x"] == " 1 "
    assert first.previous_snapshot_id is None
    assert second.previous_snapshot_id == first.id
    assert second.diff.model_dump() == {
        "comparable": True,
        "reason": "",
        "added_rows": 3,
        "changed_rows": 1,
        "removed_rows": 1,
        "unchanged_rows": 0,
    }
    assert duplicate.id == second.id
    assert attempt.reused_existing_snapshot is True
    assert len(service.detail(connector.id).raw_snapshots) == 2


def test_failure_retry_and_duplicate_fetch_do_not_store_credentials(tmp_path) -> None:
    database = tmp_path / "workbench.db"
    Store(database)
    service = DataLifecycleService(database)
    connector = service.create_connector(_connector())
    secret = "SUPER-SECRET-CONNECTOR-CREDENTIAL"

    with pytest.raises(SourceFetchFailedError) as failure:
        service.fetch(
            connector.id,
            SourceFetchRequest(
                object_content="{bad json",
                object_version="broken",
            ),
        )
    failed = failure.value.attempt
    snapshot, succeeded = service.fetch(
        connector.id,
        SourceFetchRequest(
            object_content=V1,
            object_version="retry",
            retry_of=failed.id,
        ),
    )

    assert failed.status == "failed"
    assert failed.error_code == "invalid_object"
    assert secret not in failed.model_dump_json()
    assert succeeded.retry_of == failed.id
    assert succeeded.snapshot_id == snapshot.id
    assert secret.encode() not in database.read_bytes()
    with pytest.raises(ValidationError, match="credential"):
        SourceConnectorCreateInput(
            name="signed locator",
            connector_type="object_storage_json_v1",
            source_locator="s3://user:password@bucket/key?token=secret",
            selection=ObjectSelection(format="json_array"),
        )


def test_scheduled_fetch_is_separate_from_manual_fetch(tmp_path) -> None:
    database = tmp_path / "workbench.db"
    Store(database)
    service = DataLifecycleService(database)
    connector = service.create_connector(_connector())

    with pytest.raises(SourceFetchFailedError) as failure:
        service.fetch(
            connector.id,
            SourceFetchRequest(
                trigger_kind="scheduled",
                object_content=V1,
                object_version="scheduled-1",
            ),
        )

    assert failure.value.attempt.error_code == "scheduled_trigger_not_allowed"
    assert service.detail(connector.id).raw_snapshots == ()

    scheduled_connector = service.create_connector(
        SourceConnectorCreateInput(
            name="schedule有効object",
            connector_type="object_storage_json_v1",
            source_locator="s3://demo-bucket/scheduled/latest.json",
            selection=ObjectSelection(format="json_array", primary_key="id"),
            trigger_policy="schedulable",
            schedule={
                "schedule_id": "every-hour",
                "interval_minutes": 60,
                "enabled": True,
            },
        )
    )
    scheduled, attempt = service.fetch(
        scheduled_connector.id,
        SourceFetchRequest(
            trigger_kind="scheduled",
            object_content=V1,
            object_version="scheduled-2",
        ),
    )
    assert attempt.status == "succeeded"
    assert scheduled.trigger_kind == "scheduled"


def test_curation_keeps_quarantine_reasons_and_quality_delta(tmp_path) -> None:
    database = tmp_path / "workbench.db"
    Store(database)
    service = DataLifecycleService(database)
    connector = service.create_connector(_connector())
    recipe = service.create_recipe(_recipe())
    first, _ = service.fetch(
        connector.id,
        SourceFetchRequest(object_content=V1, object_version="1"),
    )
    second, _ = service.fetch(
        connector.id,
        SourceFetchRequest(object_content=V2, object_version="2"),
    )
    first_run = service.curate(
        first.id,
        CurationRunCreateInput(
            recipe_resource_id=recipe.id,
            profile_revision_id="profile@1",
            profile_digest="sha256:profile",
        ),
    )
    second_run = service.curate(
        second.id,
        CurationRunCreateInput(
            recipe_resource_id=recipe.id,
            profile_revision_id="profile@1",
            profile_digest="sha256:profile",
        ),
    )

    assert first.rows[0]["x"] == " 1 "
    by_key = {row.row_key: row for row in second_run.rows}
    assert by_key["C"].status == "quarantined"
    assert by_key["C"].reason_codes == ("missing_required",)
    assert by_key["D"].status == "warning"
    assert by_key["D"].target_eligible is False
    assert by_key["E"].reason_codes == ("sum_limit_exceeded",)
    assert second_run.quality.model_dump() == {
        "accepted": 1,
        "warning": 2,
        "quarantined": 1,
        "blocked": 0,
        "target_ineligible": 1,
    }
    assert second_run.quality_delta.comparable is True
    assert second_run.quality_delta.quarantined_delta == 1
    assert first_run.curation_digest != second_run.curation_digest

    with pytest.raises(LifecycleResourceConflictError):
        service.create_recipe(
            CurationRecipeCreateInput(
                recipe_id=recipe.recipe_id,
                version=recipe.version,
                name="同じversionの別定義",
                steps=({"kind": "filter_equal_v1", "field": "id", "value": "A"},),
            )
        )


def test_approval_and_training_snapshot_are_explicit_separate_actions(
    tmp_path,
) -> None:
    database = tmp_path / "workbench.db"
    Store(database)
    service = DataLifecycleService(database)
    connector = service.create_connector(_connector())
    recipe = service.create_recipe(_recipe())
    raw, _ = service.fetch(
        connector.id,
        SourceFetchRequest(object_content=V2, object_version="2"),
    )
    run = service.curate(
        raw.id,
        CurationRunCreateInput(
            recipe_resource_id=recipe.id,
            profile_revision_id="profile@1",
            profile_digest="sha256:profile",
        ),
    )

    assert service.detail(connector.id).canonical_revisions == ()
    assert service.detail(connector.id).training_snapshots == ()
    approved = service.approve(
        run.id,
        DatasetApprovalInput(actor="reviewer", reason="品質確認済み"),
    )
    assert approved.excluded_row_keys == ("C",)
    assert service.detail(connector.id).training_snapshots == ()

    training = service.create_training_snapshot(
        approved.id,
        TrainingSnapshotCreateInput(
            actor="modeler",
            purpose="再学習候補の比較",
            targets=({"target_key": "target", "field": "target"},),
            split={"group_field": "id", "folds": 2},
        ),
    )
    assert training.included_row_keys == ("A", "E")
    assert training.row_count == 2
    assert service.detail(connector.id).training_snapshots == (training,)


def test_training_snapshot_fixes_target_cohorts_and_exact_group_splits(
    tmp_path,
) -> None:
    database = tmp_path / "workbench.db"
    Store(database)
    service = DataLifecycleService(database)
    connector = service.create_connector(_connector())
    recipe = service.create_recipe(
        CurationRecipeCreateInput(
            recipe_id="multi-output",
            version=1,
            name="target別cohort",
            steps=(
                {"kind": "coerce_number_v1", "fields": ["x", "y1", "y2"]},
                {"kind": "required_fields_v1", "fields": ["id", "lot", "x"]},
                {"kind": "target_eligibility_v1", "fields": ["y1", "y2"]},
            ),
        )
    )
    raw, _ = service.fetch(
        connector.id,
        SourceFetchRequest(
            object_content="""[
              {"id":"A","lot":"L1","x":1,"y1":10,"y2":20},
              {"id":"B","lot":"L1","x":2,"y1":11},
              {"id":"C","lot":"L2","x":3,"y2":22},
              {"id":"D","lot":"L3","x":4,"y1":13,"y2":23},
              {"id":"E","lot":"L4","x":5,"y1":14,"y2":24}
            ]""",
            object_version="multi-output-v1",
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
    revision = service.approve(
        run.id,
        DatasetApprovalInput(actor="reviewer", reason="cohort確認"),
    )
    definition = {
        "actor": "modeler",
        "purpose": "multi-output再現性",
        "targets": (
            {"target_key": "strength", "field": "y1"},
            {"target_key": "ductility", "field": "y2"},
        ),
    }
    snapshot = service.create_training_snapshot(
        revision.id,
        TrainingSnapshotCreateInput(
            **definition,
            split={"group_field": "lot", "folds": 2},
        ),
    )
    by_target = {
        cohort.target_key: cohort for cohort in snapshot.target_cohorts
    }

    assert snapshot.schema_version == "approved-training-snapshot/v2"
    assert snapshot.included_row_keys == ("A", "B", "C", "D", "E")
    assert by_target["strength"].row_keys == ("A", "B", "D", "E")
    assert by_target["ductility"].row_keys == ("A", "C", "D", "E")
    assert by_target["strength"].cohort_digest != (
        by_target["ductility"].cohort_digest
    )
    assert [item.model_dump() for item in by_target["strength"].split_assignments] == [
        {"group_key": "L1", "fold": 0},
        {"group_key": "L3", "fold": 1},
        {"group_key": "L4", "fold": 0},
    ]
    repeated = service.create_training_snapshot(
        revision.id,
        TrainingSnapshotCreateInput(
            **definition,
            split={"group_field": "lot", "folds": 2},
        ),
    )
    assert repeated == snapshot

    three_fold = service.create_training_snapshot(
        revision.id,
        TrainingSnapshotCreateInput(
            **definition,
            split={"group_field": "lot", "folds": 3},
        ),
    )
    assert three_fold.target_cohorts[0].cohort_digest == (
        snapshot.target_cohorts[0].cohort_digest
    )
    assert three_fold.target_cohorts[0].split_digest != (
        snapshot.target_cohorts[0].split_digest
    )
    assert three_fold.snapshot_digest != snapshot.snapshot_digest

    with pytest.raises(
        LifecycleConflictError,
        match="target fieldsがCuration Recipeと一致しません",
    ):
        service.create_training_snapshot(
            revision.id,
            TrainingSnapshotCreateInput(
                actor="modeler",
                purpose="片方だけを暗黙に除外",
                targets=({"target_key": "strength", "field": "y1"},),
                split={"group_field": "lot", "folds": 2},
            ),
        )

    forged = snapshot.model_copy(update={"purpose": "改ざんされた目的"})
    with pytest.raises(
        ValueError,
        match="親Curation Runから再現できません",
    ):
        service.repository.save_training_snapshot(forged)


def test_legacy_training_snapshot_keeps_its_original_digest_semantics() -> None:
    payload = {
        "dataset_digest": "sha256:dataset",
        "included_row_keys": ("A", "B"),
        "actor": "legacy-modeler",
        "purpose": "legacy package",
    }
    snapshot = {
        "schema_version": "approved-training-snapshot/v1",
        "id": "training-snapshot-legacy",
        "canonical_dataset_revision_id": "canonical-legacy",
        **payload,
        "row_count": 2,
        "snapshot_digest": semantic_digest(payload),
        "created_at": "2026-07-27T00:00:00Z",
    }

    from material_workbench.contracts.data_lifecycle_contracts import (
        ApprovedTrainingSnapshot,
    )

    restored = ApprovedTrainingSnapshot.model_validate(snapshot)
    assert restored.schema_version == "approved-training-snapshot/v1"
    assert restored.target_cohorts == ()
    assert restored.split is None

    with pytest.raises(ValidationError, match="feature_pipeline"):
        TrainingSnapshotCreateInput.model_validate(
            {
                "actor": "modeler",
                "purpose": "境界確認",
                "targets": [{"target_key": "target", "field": "target"}],
                "split": {"group_field": "lot", "folds": 2},
                "feature_pipeline": {
                    "id": "must-belong-to-package",
                    "version": "1",
                },
            }
        )


def test_override_is_targeted_auditable_and_cannot_include_blocked_rows(
    tmp_path,
) -> None:
    with pytest.raises(ValidationError):
        DatasetApprovalInput(
            actor="reviewer",
            overrides=({"row_key": "C", "reason": "source確認済み"},),
        )

    database = tmp_path / "workbench.db"
    Store(database)
    service = DataLifecycleService(database)
    connector = service.create_connector(_connector())
    recipe = service.create_recipe(_recipe())
    raw, _ = service.fetch(
        connector.id,
        SourceFetchRequest(object_content=V2, object_version="2"),
    )
    run = service.curate(
        raw.id,
        CurationRunCreateInput(
            recipe_resource_id=recipe.id,
            profile_revision_id="profile@1",
            profile_digest="sha256:profile",
        ),
    )
    overridden = service.approve(
        run.id,
        DatasetApprovalInput(
            actor="senior-reviewer",
            reason="source ownerへ確認済み",
            overrides=(
                {"row_key": "C", "reason": "欠損は既知の測定限界"},
            ),
        ),
    )
    assert overridden.actor == "senior-reviewer"
    assert overridden.overrides[0].row_key == "C"
    assert "C" in overridden.approved_row_keys

    duplicate_raw, _ = service.fetch(
        connector.id,
        SourceFetchRequest(
            object_content='[{"id":"X","x":1,"target":2},{"id":"X","x":2,"target":3}]',
            object_version="duplicate",
        ),
    )
    duplicate_run = service.curate(
        duplicate_raw.id,
        CurationRunCreateInput(
            recipe_resource_id=recipe.id,
            profile_revision_id="profile@1",
            profile_digest="sha256:profile",
        ),
    )
    with pytest.raises(LifecycleConflictError, match="重複row key"):
        service.approve(
            duplicate_run.id,
            DatasetApprovalInput(
                actor="reviewer",
                reason="overrideを試行",
                overrides=({"row_key": "X", "reason": "重複を許容"},),
            ),
        )


def test_api_tracks_digests_without_exposing_unapproved_data_to_projects(
    client,
) -> None:
    before = client.get("/api/project-creation-options").json()
    profile = client.get("/api/data-library/datasets").json()[0][
        "profile_revision"
    ]
    connector_response = client.post(
        "/api/data-lifecycle/connectors",
        json=_connector().model_dump(mode="json"),
    )
    assert connector_response.status_code == 201, connector_response.text
    connector = connector_response.json()
    secret = "API-SECRET-MUST-NOT-RETURN"
    failed_response = client.post(
        f"/api/data-lifecycle/connectors/{connector['id']}/fetch",
        json={
            "schema_version": "source-fetch-request/v1",
            "trigger_kind": "manual",
            "object_content": "{invalid",
            "object_version": "api-broken",
        },
        headers={"X-Source-Credential": secret},
    )
    assert failed_response.status_code == 422
    assert secret not in failed_response.text
    fetched_response = client.post(
        f"/api/data-lifecycle/connectors/{connector['id']}/fetch",
        json={
            "schema_version": "source-fetch-request/v1",
            "trigger_kind": "manual",
            "object_content": V2,
            "object_version": "api-etag",
        },
        headers={"X-Source-Credential": secret},
    )
    assert fetched_response.status_code == 201, fetched_response.text
    assert secret not in fetched_response.text
    assert secret.encode() not in Path(client.app.state.store.path).read_bytes()
    snapshot = fetched_response.json()["snapshot"]
    recipe_response = client.post(
        "/api/data-lifecycle/recipes",
        json=_recipe().model_dump(mode="json"),
    )
    assert recipe_response.status_code == 201, recipe_response.text
    recipe = recipe_response.json()
    run_response = client.post(
        f"/api/data-lifecycle/raw-snapshots/{snapshot['id']}/curation-runs",
        json={
            "recipe_resource_id": recipe["id"],
            "profile_revision_id": profile["id"],
            "profile_digest": profile["profile_digest"],
        },
    )
    assert run_response.status_code == 201, run_response.text
    run = run_response.json()

    unapproved = client.get("/api/project-creation-options").json()
    assert unapproved["datasets"] == before["datasets"]
    approved_response = client.post(
        f"/api/data-lifecycle/curation-runs/{run['id']}/approve",
        json={"reason": "API確認", "overrides": []},
    )
    assert approved_response.status_code == 201, approved_response.text
    approved = approved_response.json()
    assert approved["actor"] == "local-workspace-user"
    detail = client.get(
        f"/api/data-lifecycle/connectors/{connector['id']}"
    ).json()
    assert detail["training_snapshots"] == []

    training_response = client.post(
        "/api/data-lifecycle/canonical-dataset-revisions/"
        f"{approved['id']}/training-snapshots",
        json={
            "purpose": "明示的な学習Snapshot",
            "targets": [{"target_key": "target", "field": "target"}],
            "split": {
                "strategy_id": "sorted-group-round-robin-v1",
                "group_field": "id",
                "folds": 2,
            },
        },
    )
    assert training_response.status_code == 201, training_response.text
    assert training_response.json()["actor"] == "local-workspace-user"
    assert training_response.json()["row_count"] == 2
    after = client.get("/api/project-creation-options").json()
    assert after["model_packages"] == before["model_packages"]


def test_api_owns_lifecycle_actor_and_requires_override_reason(client) -> None:
    catalog = client.get("/api/data-lifecycle")
    assert catalog.status_code == 200
    assert catalog.json()["current_actor"] == {
        "id": "local-workspace-user",
        "label": "このローカルワークスペースの利用者",
    }

    actor_in_approval = client.post(
        "/api/data-lifecycle/curation-runs/not-used/approve",
        json={"actor": "spoofed-user", "reason": "", "overrides": []},
    )
    assert actor_in_approval.status_code == 422

    override_without_reason = client.post(
        "/api/data-lifecycle/curation-runs/not-used/approve",
        json={
            "reason": "",
            "overrides": [{"row_key": "A-01", "reason": "個別確認済み"}],
        },
    )
    assert override_without_reason.status_code == 422

    blank_row_reason = client.post(
        "/api/data-lifecycle/curation-runs/not-used/approve",
        json={
            "reason": "全体理由",
            "overrides": [{"row_key": "A-01", "reason": "   "}],
        },
    )
    assert blank_row_reason.status_code == 422

    duplicate_override = client.post(
        "/api/data-lifecycle/curation-runs/not-used/approve",
        json={
            "reason": "全体理由",
            "overrides": [
                {"row_key": "A-01", "reason": "理由1"},
                {"row_key": "A-01", "reason": "理由2"},
            ],
        },
    )
    assert duplicate_override.status_code == 422

    actor_in_training = client.post(
        "/api/data-lifecycle/canonical-dataset-revisions/not-used/training-snapshots",
        json={"actor": "spoofed-user", "purpose": "test"},
    )
    assert actor_in_training.status_code == 422
