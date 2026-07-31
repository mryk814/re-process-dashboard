from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
import sqlite3

from decision_workbench.contracts.chain_contracts import (
    ChainBinding,
    ChainDefinition,
    ChainPort,
    ChainProjectIdentity,
    ChainRevision,
    ChainSnapshotIdentityV2,
    ChainStage,
    ChainStageLock,
    ExternalBindingSource,
    StageContractSurface,
    build_chain_revision,
)
from decision_workbench.contracts.chain_execution_contracts import (
    ChainSnapshot,
    ChainStageExecution,
)
from decision_workbench.persistence.chain_catalog_migration import (
    migrate_chain_catalog,
)
from decision_workbench.persistence.project_lifecycle_migration import (
    migrate_project_lifecycle,
)
from decision_workbench.persistence.store import Store
from decision_workbench.persistence.workspace_catalog_migration import (
    migrate_workspace_catalog,
)
from decision_workbench.persistence.workspace_maintenance_migration import (
    migrate_workspace_maintenance_events,
)


NOW = datetime(2026, 7, 1, tzinfo=UTC)
DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
DIGEST_C = "sha256:" + "c" * 64


def _legacy_chain() -> tuple[ChainDefinition, StageContractSurface, ChainRevision]:
    definition = ChainDefinition(
        chain_id="legacy-one-stage",
        label="旧版一段Chain",
        stages=(
            ChainStage(
                stage_id="A",
                stage_kind="deterministic_transform",
                contract_id="legacy-transform",
            ),
        ),
        external_inputs=(
            ChainPort(
                path="candidate.value",
                value_kind="number",
                quantity="value",
                unit="mass%",
            ),
        ),
        bindings=(
            ChainBinding(
                target_stage_id="A",
                target_input_path="value",
                source=ExternalBindingSource(
                    source_kind="external",
                    path="candidate.value",
                ),
            ),
        ),
    )
    contract = StageContractSurface(
        stage_kind="deterministic_transform",
        contract_id="legacy-transform",
        contract_digest=DIGEST_A,
        input_ports=(
            ChainPort(
                path="value",
                value_kind="number",
                quantity="value",
                unit="mass%",
            ),
        ),
        output_ports=(
            ChainPort(
                path="result",
                value_kind="number",
                quantity="result",
                unit="MPa",
            ),
        ),
    )
    revision = build_chain_revision(
        definition,
        revision=1,
        contracts={(contract.stage_kind, contract.contract_id): contract},
        stage_locks={
            "A": ChainStageLock(
                contract_digest=contract.contract_digest,
                package_manifest_digest=DIGEST_B,
            ),
        },
    )
    return definition, contract, revision


def _create_legacy_workspace(path: Path) -> tuple[str, str, str]:
    """Create the last catalog-era schema before later additive migrations."""

    migrate_workspace_catalog(path)
    migrate_workspace_maintenance_events(path)
    migrate_project_lifecycle(path)
    migrate_chain_catalog(path)

    candidate_id = "legacy-candidate"
    chain_candidate_id = "legacy-chain-candidate"
    prediction_snapshot_id = "legacy-prediction-snapshot"
    chain_snapshot_id = "legacy-chain-snapshot"
    definition, _contract, revision = _legacy_chain()
    candidate_payload = {
        "name": "保持対象候補",
        "inputs": {
            "composition": {"C": 0.08},
            "process": {"temperature_c": 800.0},
            "categorical": {},
            "heat_pattern": None,
        },
        "provenance": {"source_kind": "manual", "source_ref": None},
    }
    stage = ChainStageExecution(
        stage_id="A",
        status="latest",
        requested_input_digest=DIGEST_C,
        result_input_digest=DIGEST_C,
        contract_digest=DIGEST_A,
        package_manifest_digest=DIGEST_B,
        canonical_input={"value": 0.08},
        result={"result": 510.0},
        started_at=NOW,
        completed_at=NOW,
    )
    chain_snapshot = ChainSnapshot(
        snapshot_id=chain_snapshot_id,
        identity=ChainSnapshotIdentityV2(
            chain_revision_id=f"{revision.chain_id}:r{revision.revision}",
            chain_revision_digest=revision.revision_digest,
            candidate_id=chain_candidate_id,
            candidate_revision=1,
            candidate_adapter_id="legacy-acceptance/v1",
        ),
        request_id="legacy-request",
        external_input={"candidate.value": 0.08},
        stages=(stage,),
        created_at=NOW,
    )

    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO projects("
            "id,name,task_id,created_at,updated_at,scientific_identity_json"
            ") VALUES (?,?,?,?,?,?)",
            (
                "legacy-chain-project",
                "保持対象Chain Project",
                "",
                NOW.isoformat(),
                NOW.isoformat(),
                ChainProjectIdentity(
                    identity_kind="chain",
                    chain_revision_id=f"{revision.chain_id}:r{revision.revision}",
                    chain_revision_digest=revision.revision_digest,
                ).model_dump_json(),
            ),
        )
        connection.execute(
            "INSERT INTO candidates("
            "id,project_id,name,payload,created_at,updated_at,revision,archived_at"
            ") VALUES (?,?,?,?,?,?,?,NULL)",
            (
                candidate_id,
                "default",
                candidate_payload["name"],
                json.dumps(candidate_payload, ensure_ascii=False),
                NOW.isoformat(),
                NOW.isoformat(),
                1,
            ),
        )
        connection.execute(
            "INSERT INTO candidates("
            "id,project_id,name,payload,created_at,updated_at,revision,archived_at"
            ") VALUES (?,?,?,?,?,?,?,NULL)",
            (
                chain_candidate_id,
                "legacy-chain-project",
                "保持対象Chain候補",
                json.dumps(
                    {**candidate_payload, "name": "保持対象Chain候補"},
                    ensure_ascii=False,
                ),
                NOW.isoformat(),
                NOW.isoformat(),
                1,
            ),
        )
        connection.execute(
            "INSERT INTO snapshots(id,candidate_id,payload,created_at) VALUES (?,?,?,?)",
            (
                prediction_snapshot_id,
                candidate_id,
                json.dumps(
                    {
                        "snapshot_schema_version": "prediction-snapshot-v2",
                        "marker": "preserve-exactly",
                        "raw_candidate": {
                            "id": candidate_id,
                            "revision": 1,
                            **candidate_payload,
                        },
                        "prediction": {
                            "predictions": {
                                "TS": {
                                    "mean": 510.0,
                                    "std": 3.0,
                                    "unit": "MPa",
                                },
                            },
                        },
                    },
                    ensure_ascii=False,
                ),
                NOW.isoformat(),
            ),
        )
        connection.execute(
            "INSERT INTO chain_definitions("
            "id,chain_id,definition_digest,definition_json,created_at"
            ") VALUES (?,?,?,?,?)",
            (
                f"{definition.chain_id}@{definition.digest.removeprefix('sha256:')[:12]}",
                definition.chain_id,
                definition.digest,
                definition.model_dump_json(),
                NOW.isoformat(),
            ),
        )
        connection.execute(
            "INSERT INTO chain_revisions("
            "id,chain_id,revision,revision_digest,revision_json,created_at"
            ") VALUES (?,?,?,?,?,?)",
            (
                f"{revision.chain_id}:r{revision.revision}",
                revision.chain_id,
                revision.revision,
                revision.revision_digest,
                revision.model_dump_json(),
                NOW.isoformat(),
            ),
        )
        connection.execute(
            "INSERT INTO chain_snapshot_records("
            "id,project_id,candidate_id,candidate_revision,"
            "identity_json,payload_json,created_at"
            ") VALUES (?,?,?,?,?,?,?)",
            (
                chain_snapshot.snapshot_id,
                "legacy-chain-project",
                chain_candidate_id,
                1,
                chain_snapshot.identity.model_dump_json(),
                chain_snapshot.model_dump_json(),
                NOW.isoformat(),
            ),
        )
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "candidate_revisions" not in tables
        assert "chain_analysis_variant_records" not in tables
    return candidate_id, chain_candidate_id, prediction_snapshot_id


def test_catalog_era_workspace_migrates_with_decision_history_readable(
    tmp_path: Path,
) -> None:
    database = tmp_path / "legacy-catalog-workspace.db"
    candidate_id, chain_candidate_id, prediction_snapshot_id = (
        _create_legacy_workspace(database)
    )
    with sqlite3.connect(database) as connection:
        migrations_before = connection.execute(
            "SELECT COUNT(*) FROM schema_migrations"
        ).fetchone()[0]

    store = Store(database)

    assert store.get_project("default") is not None
    candidate = store.get_candidate(candidate_id, project_id="default")
    assert candidate is not None
    assert candidate.name == "保持対象候補"
    candidate_revision = store.get_candidate_revision(
        candidate_id, 1, project_id="default"
    )
    assert candidate_revision is not None
    assert candidate_revision.name == "保持対象候補"
    prediction = store.get_snapshot(prediction_snapshot_id)
    assert prediction is not None
    assert prediction["payload"]["marker"] == "preserve-exactly"
    assert [
        item["payload"]["marker"] for item in store.list_snapshots(candidate_id)
    ] == ["preserve-exactly"]
    assert [item.chain_id for item in store.list_chain_revisions()] == [
        "legacy-one-stage"
    ]
    chain_history = store.list_chain_snapshots(
        "legacy-chain-project", chain_candidate_id
    )
    assert [item.snapshot_id for item in chain_history] == [
        "legacy-chain-snapshot"
    ]
    assert chain_history[0].stages[0].result == {"result": 510.0}
    single_task_history = store.project_history("default")
    assert single_task_history is not None
    assert [
        item["candidate"].id for item in single_task_history["candidates"]
    ] == [
        candidate_id
    ]
    assert [
        item["snapshots"][0]["prediction_summary"]
        for item in single_task_history["candidates"]
    ] == [{"TS": {"mean": 510.0, "std": 3.0, "unit": "MPa"}}]
    chain_project_history = store.project_history("legacy-chain-project")
    assert chain_project_history is not None
    assert [
        item["candidate"].id for item in chain_project_history["candidates"]
    ] == [
        chain_candidate_id
    ]
    assert [
        snapshot.snapshot_id
        for item in chain_project_history["candidates"]
        for snapshot in item["chain_snapshots"]
    ] == ["legacy-chain-snapshot"]

    with sqlite3.connect(database) as connection:
        migrations_after = connection.execute(
            "SELECT COUNT(*) FROM schema_migrations"
        ).fetchone()[0]
        tables_after = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert migrations_after > migrations_before
    assert "candidate_revisions" in tables_after
    assert "chain_analysis_variant_records" in tables_after
