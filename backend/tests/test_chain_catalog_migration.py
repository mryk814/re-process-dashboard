from __future__ import annotations

import json
from pathlib import Path
import sqlite3

import pytest

from material_workbench.contracts.chain_contracts import (
    ChainBinding,
    ChainDefinition,
    ChainPort,
    ChainStage,
    ChainStageLock,
    ExternalBindingSource,
    StageContractSurface,
    build_chain_revision,
)
from material_workbench.persistence.chain_catalog_migration import (
    MIGRATION_ID,
    ChainCatalogMigrationError,
    migrate_chain_catalog,
)
from material_workbench.persistence.store import (
    ChainCatalogConflictError,
    Store,
)
from material_workbench.persistence.workspace_catalog_migration import (
    migrate_workspace_catalog,
)


def _workspace(path: Path) -> None:
    migrate_workspace_catalog(path)


def test_chain_catalog_migration_backfills_explicit_identity_without_rewriting_snapshots(
    tmp_path: Path,
) -> None:
    path = tmp_path / "workspace.db"
    _workspace(path)
    old_snapshot = json.dumps(
        {"snapshot_schema_version": "prediction-snapshot-v1", "marker": "keep-byte-exact"},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    with sqlite3.connect(path) as conn:
        conn.execute(
            "UPDATE projects SET dataset_view_revision_id=?,task_contract_digest=?,"
            "model_package_ref_id=?,model_package_manifest_digest=?,binding_provenance='explicit' "
            "WHERE id='default'",
            ("view-r1", "task-digest", "package-ref", "package-digest"),
        )
        conn.execute(
            "INSERT INTO candidates(id,project_id,name,payload,created_at,updated_at) "
            "VALUES ('candidate-1','default','候補',?,'2026-01-01','2026-01-01')",
            (
                json.dumps(
                    {
                        "name": "候補",
                        "inputs": {
                            "composition": {},
                            "process": {},
                            "categorical": {},
                            "heat_pattern": None,
                        },
                        "provenance": {
                            "source_kind": "manual",
                            "source_ref": None,
                        },
                    },
                    ensure_ascii=False,
                ),
            ),
        )
        conn.execute(
            "INSERT INTO snapshots(id,candidate_id,payload,created_at) "
            "VALUES ('snapshot-1','candidate-1',?,'2026-01-01')",
            (old_snapshot,),
        )

    assert migrate_chain_catalog(path) == 1
    assert migrate_chain_catalog(path) == 0

    with sqlite3.connect(path) as conn:
        identity_json = conn.execute(
            "SELECT scientific_identity_json FROM projects WHERE id='default'"
        ).fetchone()[0]
        identity = json.loads(identity_json)
        assert identity == {
            "binding_provenance": "explicit",
            "dataset_view_revision_id": "view-r1",
            "identity_kind": "single_task",
            "model_package_manifest_digest": "package-digest",
            "model_package_ref_id": "package-ref",
            "task_contract_digest": "task-digest",
            "task_id": "annealed-properties-v1",
        }
        assert conn.execute(
            "SELECT payload FROM snapshots WHERE id='snapshot-1'"
        ).fetchone()[0] == old_snapshot
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert {
            "chain_definitions",
            "chain_revisions",
            "chain_snapshot_records",
        } <= tables
        assert conn.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE id=?",
            (MIGRATION_ID,),
        ).fetchone()[0] == 1


def test_chain_catalog_migration_preserves_unbound_legacy_identity(
    tmp_path: Path,
) -> None:
    path = tmp_path / "legacy.db"
    _workspace(path)
    migrate_chain_catalog(path)

    with sqlite3.connect(path) as conn:
        identity = json.loads(
            conn.execute(
                "SELECT scientific_identity_json FROM projects WHERE id='default'"
            ).fetchone()[0]
        )
    assert identity == {
        "binding_provenance": "unbound_legacy",
        "dataset_view_revision_id": None,
        "identity_kind": "single_task",
        "model_package_manifest_digest": None,
        "model_package_ref_id": None,
        "task_contract_digest": None,
        "task_id": "annealed-properties-v1",
    }


def test_chain_catalog_migration_rejects_partial_binding_and_rolls_back(
    tmp_path: Path,
) -> None:
    path = tmp_path / "partial.db"
    _workspace(path)
    with sqlite3.connect(path) as conn:
        conn.execute(
            "UPDATE projects SET dataset_view_revision_id='view-r1',"
            "binding_provenance='explicit' WHERE id='default'"
        )

    with pytest.raises(ChainCatalogMigrationError, match="partial"):
        migrate_chain_catalog(path)

    with sqlite3.connect(path) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "chain_definitions" not in tables
        assert "scientific_identity_json" not in {
            row[1] for row in conn.execute("PRAGMA table_info(projects)")
        }
        assert conn.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE id=?",
            (MIGRATION_ID,),
        ).fetchone()[0] == 0


def test_store_registers_immutable_definition_and_revision_idempotently(
    tmp_path: Path,
) -> None:
    store = Store(tmp_path / "catalog.db")
    definition = ChainDefinition(
        chain_id="one-stage",
        label="一段",
        stages=(
            ChainStage(
                stage_id="A",
                stage_kind="deterministic_transform",
                contract_id="transform-a",
            ),
        ),
        external_inputs=(
            ChainPort(
                path="candidate.blend",
                value_kind="sparse_blend",
                unit="sparse-blend/v1",
            ),
        ),
        bindings=(
            ChainBinding(
                target_stage_id="A",
                target_input_path="blend",
                source=ExternalBindingSource(
                    source_kind="external",
                    path="candidate.blend",
                ),
            ),
        ),
    )
    contract = StageContractSurface(
        stage_kind="deterministic_transform",
        contract_id="transform-a",
        contract_digest="sha256:" + "a" * 64,
        input_ports=(
            ChainPort(
                path="blend",
                value_kind="sparse_blend",
                unit="sparse-blend/v1",
            ),
        ),
        output_ports=(
            ChainPort(path="C", value_kind="number", unit="mass% whole wire"),
        ),
    )
    revision = build_chain_revision(
        definition,
        revision=1,
        contracts={(contract.stage_kind, contract.contract_id): contract},
        stage_locks={
            "A": ChainStageLock(
                contract_digest=contract.contract_digest,
                package_manifest_digest="sha256:" + "b" * 64,
            )
        },
    )

    definition_id = store.register_chain_definition(definition)
    revision_id = store.register_chain_revision(revision)
    assert store.register_chain_definition(definition) == definition_id
    assert store.register_chain_revision(revision) == revision_id
    assert store.list_chain_definitions() == [definition]
    assert store.list_chain_revisions() == [revision]
    assert store.get_chain_revision(revision_id) == revision

    conflicting = revision.model_copy(
        update={"revision_digest": "sha256:" + "c" * 64}
    )
    with pytest.raises(ChainCatalogConflictError, match="異なる内容"):
        store.register_chain_revision(conflicting)
