from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from material_workbench.persistence.candidate_migration import HOT_PROJECT_ID
from material_workbench.persistence.sqlite_connection import (
    initialize_sqlite,
    sqlite_connection,
    validate_sqlite_foreign_keys,
)
from material_workbench.persistence.project_lifecycle_migration import (
    install_project_archive_write_guards,
    migrate_project_lifecycle,
    remove_project_archive_write_guards,
)
from material_workbench.persistence.project_persistence_inventory import (
    PROJECT_PERSISTENCE,
)
from material_workbench.contracts.chain_contracts import (
    ChainDefinition,
    ChainProjectIdentity,
    ChainRevision,
    SingleTaskProjectIdentity,
    StageContractSurface,
    validate_chain_revision,
)
from material_workbench.contracts.chain_execution_contracts import (
    ActualConditionedVariant,
    ChainExecution,
    ChainSnapshot,
)
from material_workbench.contracts.chain_uncertainty_contracts import (
    ChainDistributionRun,
)
from material_workbench.contracts.schemas import (
    ActualMeasurement,
    ActualMeasurementInput,
    Candidate,
    CandidateInput,
    Project,
    ProjectCreateInput,
    ProjectGroupMoveInput,
    ProjectInput,
    ProjectUpdateInput,
    LineageNodeReview,
    LineageNodeReviewInput,
)
from material_workbench.contracts.ai_review_contracts import (
    AiReviewDisposition,
    AiReviewRun,
)
from material_workbench.persistence.lineage_review_migration import migrate_lineage_reviews
from material_workbench.persistence.decision_activity_migration import migrate_decision_activity_runs
from material_workbench.persistence.ai_review_migration import migrate_ai_reviews
from material_workbench.persistence.project_design_space_migration import (
    migrate_project_design_spaces,
)
from material_workbench.contracts.objective_contracts import (
    ObjectiveDefinition,
    ObjectiveDefinitionRevision,
)
from material_workbench.persistence.project_objective_migration import (
    migrate_project_objectives,
)
from material_workbench.persistence.project_starter_migration import (
    migrate_project_starter_identity,
)
from material_workbench.persistence.candidate_revision_migration import migrate_candidate_revisions
from material_workbench.persistence.series_asset_migration import migrate_series_assets


def _target_values_json(values: dict[str, object]) -> str:
    serializable = {
        key: value.model_dump() if hasattr(value, "model_dump") else value
        for key, value in values.items()
    }
    return json.dumps(serializable, ensure_ascii=False, sort_keys=True)


def _single_task_identity_json(payload: ProjectCreateInput) -> str:
    bindings = (
        payload.dataset_view_revision_id,
        payload.task_contract_digest,
        payload.model_package_ref_id,
        payload.model_package_manifest_digest,
    )
    if not any(bindings):
        return SingleTaskProjectIdentity(
            identity_kind="single_task",
            task_id=payload.task_id,
            binding_provenance="unbound_legacy",
        ).model_dump_json()
    if not all(bindings):
        raise ValueError("Project single-Task identity has partial immutable bindings")
    identity = SingleTaskProjectIdentity(
        identity_kind="single_task",
        task_id=payload.task_id,
        dataset_view_revision_id=payload.dataset_view_revision_id,
        task_contract_digest=payload.task_contract_digest or None,
        model_package_ref_id=payload.model_package_ref_id,
        model_package_manifest_digest=payload.model_package_manifest_digest or None,
        binding_provenance="explicit",
    )
    return identity.model_dump_json()
from material_workbench.persistence.workspace_catalog_migration import migrate_workspace_catalog
from material_workbench.persistence.workspace_maintenance_migration import (
    migrate_workspace_maintenance_events,
)
from material_workbench.persistence.chain_catalog_migration import migrate_chain_catalog
from material_workbench.persistence.chain_analysis_variant_migration import (
    migrate_chain_analysis_variant,
)
from material_workbench.persistence.chain_execution_cas_migration import (
    migrate_chain_execution_cas,
)
from material_workbench.persistence.chain_uncertainty_migration import (
    migrate_chain_uncertainty,
)
from material_workbench.persistence.data_lifecycle_migration import (
    migrate_data_lifecycle,
)
from material_workbench.persistence.data_lifecycle_payload_migration import (
    migrate_data_lifecycle_payloads,
)
from material_workbench.persistence.data_lifecycle_summary_migration import (
    migrate_data_lifecycle_summaries,
)
from material_workbench.domain.candidate_policy import MAX_CANDIDATES_PER_PROJECT


PROTECTED_PROJECT_IDS = frozenset({"default", HOT_PROJECT_ID})


class ProjectNotFoundError(LookupError):
    pass


class CandidateLimitError(ValueError):
    pass


class InvalidProjectDecisionError(ValueError):
    pass


class CandidateCopyConflictError(ValueError):
    pass


class ProtectedProjectError(ValueError):
    pass


class ActiveProjectPurgeError(ValueError):
    pass


class ProjectHasSuccessorsError(ValueError):
    pass


class ProjectHasDerivedCandidatesError(ValueError):
    pass


class ProjectGroupConflictError(ValueError):
    pass


class ProjectGroupUnavailableError(ValueError):
    pass


class CandidateArchivedError(ValueError):
    pass


class CandidateRevisionConflictError(ValueError):
    def __init__(self, current: Candidate) -> None:
        super().__init__("候補は別の操作で更新されています")
        self.current = current


class StoreDataIntegrityError(RuntimeError):
    pass


class ChainCatalogConflictError(ValueError):
    pass


def _now() -> str:
    return datetime.now(UTC).isoformat()


class Store:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        initialize_sqlite(self.path)
        remove_project_archive_write_guards(self.path)
        self._init()

    def _connect(self):
        return sqlite_connection(self.path)

    def _init(self) -> None:
        migrate_workspace_catalog(self.path)
        migrate_workspace_maintenance_events(self.path)
        migrate_project_lifecycle(self.path)
        migrate_chain_catalog(self.path)
        migrate_chain_analysis_variant(self.path)
        migrate_chain_execution_cas(self.path)
        migrate_chain_uncertainty(self.path)
        migrate_candidate_revisions(self.path)
        migrate_lineage_reviews(self.path)
        migrate_decision_activity_runs(self.path)
        migrate_ai_reviews(self.path)
        migrate_project_design_spaces(self.path)
        migrate_project_objectives(self.path)
        migrate_project_starter_identity(self.path)
        migrate_series_assets(self.path)
        migrate_data_lifecycle(self.path)
        migrate_data_lifecycle_payloads(self.path)
        migrate_data_lifecycle_summaries(self.path)
        install_project_archive_write_guards(self.path)
        validate_sqlite_foreign_keys(self.path)

    def register_chain_definition(self, definition: ChainDefinition) -> str:
        record_id = (
            f"{definition.chain_id}@{definition.digest.removeprefix('sha256:')[:12]}"
        )
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT id,definition_json FROM chain_definitions "
                "WHERE definition_digest=?",
                (definition.digest,),
            ).fetchone()
            if existing is not None:
                if (
                    ChainDefinition.model_validate_json(existing["definition_json"])
                    != definition
                ):
                    raise ChainCatalogConflictError(
                        "同じdigestのChain Definitionに異なる内容があります"
                    )
                return str(existing["id"])
            conn.execute(
                "INSERT INTO chain_definitions("
                "id,chain_id,definition_digest,definition_json,created_at"
                ") VALUES (?,?,?,?,?)",
                (
                    record_id,
                    definition.chain_id,
                    definition.digest,
                    definition.model_dump_json(),
                    _now(),
                ),
            )
        return record_id

    def list_chain_definitions(self) -> list[ChainDefinition]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT definition_json FROM chain_definitions "
                "ORDER BY chain_id,created_at"
            ).fetchall()
        return [
            ChainDefinition.model_validate_json(row["definition_json"])
            for row in rows
        ]

    def get_chain_definition(
        self, chain_id: str, definition_digest: str
    ) -> ChainDefinition | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT definition_json FROM chain_definitions "
                "WHERE chain_id=? AND definition_digest=?",
                (chain_id, definition_digest),
            ).fetchone()
        return (
            ChainDefinition.model_validate_json(row["definition_json"])
            if row is not None
            else None
        )

    def register_chain_revision(
        self,
        revision: ChainRevision,
        *,
        contracts: Mapping[tuple[str, str], StageContractSurface],
    ) -> str:
        record_id = f"{revision.chain_id}:r{revision.revision}"
        with self._connect() as conn:
            definition_row = conn.execute(
                "SELECT definition_json FROM chain_definitions "
                "WHERE chain_id=? AND definition_digest=?",
                (revision.chain_id, revision.chain_definition_digest),
            ).fetchone()
            if definition_row is None:
                raise ChainCatalogConflictError(
                    "Chain Revisionが参照するDefinitionを先に登録してください"
                )
            definition = ChainDefinition.model_validate_json(
                definition_row["definition_json"]
            )
            expected_stages = [
                (stage.stage_id, stage.stage_kind, stage.contract_id)
                for stage in definition.stages
            ]
            actual_stages = [
                (stage.stage_id, stage.stage_kind, stage.contract_id)
                for stage in revision.stages
            ]
            if actual_stages != expected_stages:
                raise ChainCatalogConflictError(
                    "Chain Revisionの順序付きStageがDefinitionと一致しません"
                )
            try:
                validate_chain_revision(
                    definition,
                    revision,
                    contracts=contracts,
                )
            except ValueError as exc:
                raise ChainCatalogConflictError(str(exc)) from exc
            existing = conn.execute(
                "SELECT id,revision_json FROM chain_revisions "
                "WHERE id=? OR revision_digest=?",
                (record_id, revision.revision_digest),
            ).fetchone()
            if existing is not None:
                if (
                    ChainRevision.model_validate_json(existing["revision_json"])
                    != revision
                ):
                    raise ChainCatalogConflictError(
                        "同じChain revision番号またはdigestに異なる内容があります"
                    )
                return str(existing["id"])
            conn.execute(
                "INSERT INTO chain_revisions("
                "id,chain_id,revision,revision_digest,revision_json,created_at"
                ") VALUES (?,?,?,?,?,?)",
                (
                    record_id,
                    revision.chain_id,
                    revision.revision,
                    revision.revision_digest,
                    revision.model_dump_json(),
                    _now(),
                ),
            )
        return record_id

    def list_chain_revisions(self) -> list[ChainRevision]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT revision_json FROM chain_revisions ORDER BY chain_id,revision"
            ).fetchall()
        return [
            ChainRevision.model_validate_json(row["revision_json"])
            for row in rows
        ]

    def get_chain_revision(self, revision_id: str) -> ChainRevision | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT revision_json FROM chain_revisions WHERE id=?",
                (revision_id,),
            ).fetchone()
        return (
            ChainRevision.model_validate_json(row["revision_json"])
            if row is not None
            else None
        )

    def get_chain_stage_memo(self, memo_key: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT canonical_input_json,result_json FROM chain_stage_memo "
                "WHERE memo_key=?",
                (memo_key,),
            ).fetchone()
        if row is None:
            return None
        return {
            "canonical_input": json.loads(row["canonical_input_json"]),
            "result": json.loads(row["result_json"]),
        }

    def put_chain_stage_memo(
        self,
        *,
        memo_key: str,
        stage_id: str,
        input_digest: str,
        contract_digest: str,
        package_manifest_digest: str,
        canonical_input: Mapping[str, Any],
        result: Mapping[str, Any],
    ) -> None:
        encoded_input = json.dumps(
            canonical_input, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        encoded_result = json.dumps(
            result, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT canonical_input_json,result_json FROM chain_stage_memo "
                "WHERE memo_key=?",
                (memo_key,),
            ).fetchone()
            if existing is not None:
                if (
                    existing["canonical_input_json"] != encoded_input
                    or existing["result_json"] != encoded_result
                ):
                    raise StoreDataIntegrityError(
                        "同じChain stage memo identityに異なる内容があります"
                    )
                return
            conn.execute(
                "INSERT INTO chain_stage_memo("
                "memo_key,stage_id,input_digest,contract_digest,package_manifest_digest,"
                "canonical_input_json,result_json,created_at"
                ") VALUES (?,?,?,?,?,?,?,?)",
                (
                    memo_key,
                    stage_id,
                    input_digest,
                    contract_digest,
                    package_manifest_digest,
                    encoded_input,
                    encoded_result,
                    _now(),
                ),
            )

    @staticmethod
    def chain_execution_scope(project_id: str, candidate_id: str) -> str:
        return f"{project_id}:{candidate_id}"

    def get_chain_execution(
        self, project_id: str, candidate_id: str
    ) -> ChainExecution | None:
        scope_id = self.chain_execution_scope(project_id, candidate_id)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT execution_json FROM chain_execution_state WHERE scope_id=?",
                (scope_id,),
            ).fetchone()
        return (
            ChainExecution.model_validate_json(row["execution_json"])
            if row is not None
            else None
        )

    @staticmethod
    def _claim_chain_execution(
        conn: sqlite3.Connection,
        scope_id: str,
        request_id: str,
    ) -> int:
        row = conn.execute(
            "SELECT generation FROM chain_execution_claims WHERE scope_id=?",
            (scope_id,),
        ).fetchone()
        generation = int(row["generation"]) + 1 if row is not None else 1
        conn.execute(
            "INSERT INTO chain_execution_claims("
            "scope_id,request_id,generation,updated_at"
            ") VALUES (?,?,?,?) "
            "ON CONFLICT(scope_id) DO UPDATE SET "
            "request_id=excluded.request_id,generation=excluded.generation,"
            "updated_at=excluded.updated_at",
            (scope_id, request_id, generation, _now()),
        )
        return generation

    def claim_chain_execution(
        self,
        project_id: str,
        candidate_id: str,
        candidate_revision: int,
        request_id: str,
    ) -> int | None:
        """Claim execution only while the resolved candidate revision is current."""

        scope_id = self.chain_execution_scope(project_id, candidate_id)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            candidate = conn.execute(
                "SELECT 1 FROM candidates "
                "JOIN projects ON projects.id=candidates.project_id "
                "WHERE candidates.id=? AND candidates.project_id=? "
                "AND candidates.revision=? "
                "AND candidates.archived_at IS NULL "
                "AND projects.archived_at IS NULL",
                (candidate_id, project_id, candidate_revision),
            ).fetchone()
            if candidate is None:
                return None
            return self._claim_chain_execution(conn, scope_id, request_id)

    def chain_execution_generation(
        self, project_id: str, candidate_id: str, request_id: str
    ) -> int | None:
        scope_id = self.chain_execution_scope(project_id, candidate_id)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT generation FROM chain_execution_claims "
                "WHERE scope_id=? AND request_id=?",
                (scope_id, request_id),
            ).fetchone()
        return int(row["generation"]) if row is not None else None

    def save_chain_execution_if_current(
        self, execution: ChainExecution, generation: int
    ) -> bool:
        scope_id = self.chain_execution_scope(
            execution.project_id, execution.candidate_id
        )
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            claim = conn.execute(
                "SELECT 1 FROM chain_execution_claims "
                "WHERE scope_id=? AND request_id=? AND generation=?",
                (scope_id, execution.request_id, generation),
            ).fetchone()
            if claim is None:
                return False
            conn.execute(
                "INSERT INTO chain_execution_state("
                "scope_id,request_id,execution_json,updated_at"
                ") VALUES (?,?,?,?) "
                "ON CONFLICT(scope_id) DO UPDATE SET "
                "request_id=excluded.request_id,execution_json=excluded.execution_json,"
                "updated_at=excluded.updated_at",
                (
                    scope_id,
                    execution.request_id,
                    execution.model_dump_json(),
                    execution.updated_at.isoformat(),
                ),
            )
        return True

    def insert_chain_snapshot(
        self, project_id: str, snapshot: ChainSnapshot
    ) -> ChainSnapshot:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            candidate = conn.execute(
                "SELECT * FROM candidates WHERE id=? AND project_id=?",
                (
                    snapshot.identity.candidate_id,
                    project_id,
                ),
            ).fetchone()
            if candidate is None:
                raise StoreDataIntegrityError(
                    "Chain snapshotのcandidateが見つかりません"
                )
            current = self._candidate(candidate)
            if (
                current.archived_at is not None
                or current.revision != snapshot.identity.candidate_revision
            ):
                raise CandidateRevisionConflictError(current)
            conn.execute(
                "INSERT INTO chain_snapshot_records("
                "id,project_id,candidate_id,candidate_revision,identity_json,payload_json,created_at"
                ") VALUES (?,?,?,?,?,?,?)",
                (
                    snapshot.snapshot_id,
                    project_id,
                    snapshot.identity.candidate_id,
                    snapshot.identity.candidate_revision,
                    snapshot.identity.model_dump_json(),
                    snapshot.model_dump_json(),
                    snapshot.created_at.isoformat(),
                ),
            )
        return snapshot

    def get_chain_snapshot(
        self,
        snapshot_id: str,
        *,
        project_id: str | None = None,
    ) -> ChainSnapshot | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload_json FROM chain_snapshot_records WHERE id=?"
                + (" AND project_id=?" if project_id is not None else ""),
                (
                    (snapshot_id, project_id)
                    if project_id is not None
                    else (snapshot_id,)
                ),
            ).fetchone()
        return (
            ChainSnapshot.model_validate_json(row["payload_json"])
            if row is not None
            else None
        )

    def insert_chain_distribution_run(
        self,
        run: ChainDistributionRun,
        *,
        expected_point: ChainExecution,
    ) -> ChainDistributionRun:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            candidate = conn.execute(
                "SELECT revision,archived_at FROM candidates "
                "WHERE id=? AND project_id=?",
                (run.provenance.candidate_id, run.project_id),
            ).fetchone()
            if (
                candidate is None
                or candidate["archived_at"] is not None
                or int(candidate["revision"]) != run.provenance.candidate_revision
            ):
                raise StoreDataIntegrityError(
                    "分布実行のcandidate revisionは現在値ではありません"
                )
            scope_id = self.chain_execution_scope(
                run.project_id, run.provenance.candidate_id
            )
            point_row = conn.execute(
                "SELECT execution_json FROM chain_execution_state WHERE scope_id=?",
                (scope_id,),
            ).fetchone()
            point = (
                ChainExecution.model_validate_json(point_row["execution_json"])
                if point_row is not None
                else None
            )
            if (
                point is None
                or point != expected_point
                or point.status != "latest"
                or point.request_id != run.provenance.point_execution_request_id
                or point.candidate_revision != run.provenance.candidate_revision
                or point.chain_revision_digest
                != run.provenance.chain_revision_digest
                or any(stage.status != "latest" for stage in point.stages)
            ):
                raise StoreDataIntegrityError(
                    "分布実行が参照した点推定は現在値ではありません"
                )
            conn.execute(
                "INSERT INTO chain_distribution_runs("
                "id,project_id,candidate_id,candidate_revision,"
                "chain_revision_digest,payload_json,created_at"
                ") VALUES (?,?,?,?,?,?,?)",
                (
                    run.run_id,
                    run.project_id,
                    run.provenance.candidate_id,
                    run.provenance.candidate_revision,
                    run.provenance.chain_revision_digest,
                    run.model_dump_json(),
                    run.created_at.isoformat(),
                ),
            )
        return run

    def get_chain_distribution_run(
        self, run_id: str
    ) -> ChainDistributionRun | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload_json FROM chain_distribution_runs WHERE id=?",
                (run_id,),
            ).fetchone()
        return (
            ChainDistributionRun.model_validate_json(row["payload_json"])
            if row is not None
            else None
        )

    def latest_chain_distribution_run(
        self, project_id: str, candidate_id: str
    ) -> ChainDistributionRun | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload_json FROM chain_distribution_runs "
                "WHERE project_id=? AND candidate_id=? "
                "ORDER BY created_at DESC,id DESC LIMIT 1",
                (project_id, candidate_id),
            ).fetchone()
        return (
            ChainDistributionRun.model_validate_json(row["payload_json"])
            if row is not None
            else None
        )

    def list_chain_snapshots(
        self, project_id: str, candidate_id: str
    ) -> list[ChainSnapshot]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT payload_json FROM chain_snapshot_records "
                "WHERE project_id=? AND candidate_id=? ORDER BY created_at DESC",
                (project_id, candidate_id),
            ).fetchall()
        return [
            ChainSnapshot.model_validate_json(row["payload_json"])
            for row in rows
        ]

    def insert_chain_analysis_variant(
        self, variant: ActualConditionedVariant
    ) -> ActualConditionedVariant:
        with self._connect() as conn:
            snapshot = conn.execute(
                "SELECT 1 FROM chain_snapshot_records WHERE id=? AND project_id=? "
                "AND candidate_id=? AND candidate_revision=?",
                (
                    variant.identity.comparison_snapshot_id,
                    variant.project_id,
                    variant.identity.base_candidate_id,
                    variant.identity.base_candidate_revision,
                ),
            ).fetchone()
            if snapshot is None:
                raise StoreDataIntegrityError(
                    "実測variantの比較元Chain snapshotを固定できません"
                )
            conn.execute(
                "INSERT INTO chain_analysis_variant_records("
                "id,project_id,candidate_id,candidate_revision,"
                "comparison_snapshot_id,payload_json,created_at"
                ") VALUES (?,?,?,?,?,?,?)",
                (
                    variant.variant_id,
                    variant.project_id,
                    variant.identity.base_candidate_id,
                    variant.identity.base_candidate_revision,
                    variant.identity.comparison_snapshot_id,
                    variant.model_dump_json(),
                    variant.created_at.isoformat(),
                ),
            )
        return variant

    def list_chain_analysis_variants(
        self, project_id: str, candidate_id: str
    ) -> list[ActualConditionedVariant]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT payload_json FROM chain_analysis_variant_records "
                "WHERE project_id=? AND candidate_id=? ORDER BY created_at DESC",
                (project_id, candidate_id),
            ).fetchall()
        return [
            ActualConditionedVariant.model_validate_json(row["payload_json"])
            for row in rows
        ]

    def get_chain_analysis_variant(
        self, variant_id: str
    ) -> ActualConditionedVariant | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload_json FROM chain_analysis_variant_records WHERE id=?",
                (variant_id,),
            ).fetchone()
        return (
            ActualConditionedVariant.model_validate_json(row["payload_json"])
            if row is not None
            else None
        )

    @staticmethod
    def _project(row: sqlite3.Row) -> Project:
        return Project(
            id=row["id"],
            name=row["name"],
            description=row["description"],
            purpose=row["purpose"],
            task_id=row["task_id"],
            scientific_identity=json.loads(row["scientific_identity_json"]),
            target_values=json.loads(row["target_values"]),
            input_ranges=json.loads(row["input_ranges"]),
            response_curve_ranges=json.loads(row["response_curve_ranges"]),
            response_curve_points=row["response_curve_points"],
            heat_stage_positions_m=json.loads(row["heat_stage_positions_m"]),
            display_decimals=json.loads(row["display_decimals"]),
            notes=row["notes"],
            decision_candidate_id=row["decision_candidate_id"],
            decision_snapshot_id=row["decision_snapshot_id"],
            decision_note=row["decision_note"],
            dataset_view_revision_id=row["dataset_view_revision_id"],
            task_contract_digest=row["task_contract_digest"],
            model_package_ref_id=row["model_package_ref_id"],
            model_package_manifest_digest=row["model_package_manifest_digest"],
            project_series_id=row["project_series_id"],
            predecessor_project_id=row["predecessor_project_id"],
            continuation_reason=row["continuation_reason"],
            starter=bool(row["is_starter"]),
            design_space=(
                json.loads(row["design_space_json"])
                if row["design_space_json"]
                else None
            ),
            design_space_digest=row["design_space_digest"],
            design_space_binding_provenance=row[
                "design_space_binding_provenance"
            ],
            objective_definition=(
                json.loads(row["objective_definition_json"])
                if row["objective_definition_json"]
                else None
            ),
            objective_definition_digest=row["objective_definition_digest"],
            objective_binding_provenance=row["objective_binding_provenance"],
            binding_provenance=row["binding_provenance"],
            binding_migrated_at=(
                datetime.fromisoformat(row["binding_migrated_at"])
                if row["binding_migrated_at"]
                else None
            ),
            archived_at=(
                datetime.fromisoformat(row["archived_at"])
                if row["archived_at"]
                else None
            ),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def list_projects(self, *, include_archived: bool = False) -> list[Project]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM projects "
                + ("" if include_archived else "WHERE archived_at IS NULL ")
                + "ORDER BY created_at, id"
            ).fetchall()
        return [self._project(row) for row in rows]

    def get_project(
        self,
        project_id: str = "default",
        *,
        include_archived: bool = False,
    ) -> Project | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM projects WHERE id = ?"
                + ("" if include_archived else " AND archived_at IS NULL"),
                (project_id,),
            ).fetchone()
        return self._project(row) if row else None

    def create_project(self, payload: ProjectCreateInput, initial_candidate: CandidateInput | None = None) -> Project:
        project_id, now = str(uuid.uuid4()), _now()
        scientific_identity_json = _single_task_identity_json(payload)
        identity_provenance = json.loads(scientific_identity_json)["binding_provenance"]
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if initial_candidate is not None and initial_candidate.provenance.source_kind == "copy":
                reference = initial_candidate.provenance.source_ref
                source = conn.execute(
                    "SELECT candidate_revisions.revision, projects.task_id "
                    "FROM candidate_revisions "
                    "JOIN projects ON projects.id=candidate_revisions.project_id "
                    "WHERE candidate_revisions.candidate_id=? "
                    "AND candidate_revisions.project_id=? "
                    "AND candidate_revisions.revision=?",
                    (
                        reference.candidate_id,
                        reference.project_id,
                        reference.candidate_revision,
                    ),
                ).fetchone()
                if source is None:
                    raise CandidateCopyConflictError("コピー元候補またはrevisionが一致しません")
                if source["task_id"] != payload.task_id:
                    raise CandidateCopyConflictError("異なる予測タスクの候補はコピーできません")
            project_series_id = self._project_series_id_for_create(
                conn, payload, now
            )
            conn.execute(
                "INSERT INTO projects(id,name,description,purpose,task_id,target_values,input_ranges,"
                "response_curve_ranges,response_curve_points,heat_stage_positions_m,display_decimals,notes,decision_candidate_id,"
                "decision_snapshot_id,decision_note,dataset_view_revision_id,task_contract_digest,"
                "model_package_ref_id,model_package_manifest_digest,project_series_id,predecessor_project_id,"
                "continuation_reason,binding_provenance,scientific_identity_json,"
                "design_space_json,design_space_digest,design_space_binding_provenance,"
                "objective_definition_json,objective_definition_digest,objective_binding_provenance,"
                "created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    project_id, payload.name, payload.description, payload.purpose, payload.task_id,
                    _target_values_json(payload.target_values),
                    json.dumps({key: value.model_dump() for key, value in payload.input_ranges.items()}, ensure_ascii=False, sort_keys=True),
                    json.dumps({axis: {key: value.model_dump() for key, value in ranges.items()} for axis, ranges in payload.response_curve_ranges.items()}, ensure_ascii=False, sort_keys=True),
                    payload.response_curve_points,
                    json.dumps(payload.heat_stage_positions_m, ensure_ascii=False, sort_keys=True),
                    json.dumps(payload.display_decimals, ensure_ascii=False, sort_keys=True), payload.notes,
                    payload.decision_candidate_id, payload.decision_snapshot_id, payload.decision_note,
                    payload.dataset_view_revision_id, payload.task_contract_digest, payload.model_package_ref_id,
                    payload.model_package_manifest_digest, project_series_id, payload.predecessor_project_id,
                    payload.continuation_reason,
                    identity_provenance,
                    scientific_identity_json,
                    (
                        payload.design_space.model_dump_json()
                        if payload.design_space is not None
                        else None
                    ),
                    (
                        payload.design_space.digest
                        if payload.design_space is not None
                        else None
                    ),
                    payload.design_space_binding_provenance or "generated_default",
                    (
                        payload.objective_definition.model_dump_json()
                        if payload.objective_definition is not None
                        else None
                    ),
                    (
                        payload.objective_definition.digest
                        if payload.objective_definition is not None
                        else None
                    ),
                    payload.objective_binding_provenance or "none_configured",
                    now,
                    now,
                ),
            )
            if payload.objective_definition is not None:
                conn.execute(
                    "INSERT INTO project_objective_revisions("
                    "project_id,objective_digest,revision,payload,binding_provenance,created_at"
                    ") VALUES (?,?,?,?,?,?)",
                    (
                        project_id,
                        payload.objective_definition.digest,
                        payload.objective_definition.revision,
                        payload.objective_definition.model_dump_json(),
                        payload.objective_binding_provenance or "generated_default",
                        now,
                    ),
                )
            if initial_candidate is not None:
                candidate_id = str(uuid.uuid4())
                conn.execute(
                    "INSERT INTO candidates(id,project_id,name,payload,created_at,updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (candidate_id, project_id, initial_candidate.name, initial_candidate.model_dump_json(), now, now),
                )
                row = conn.execute(
                    "SELECT * FROM candidates WHERE id=?",
                    (candidate_id,),
                ).fetchone()
                self._record_candidate_revision(conn, row)
        return self.get_project(project_id)  # type: ignore[return-value]

    def create_chain_project(
        self,
        payload: ProjectCreateInput,
        identity: ChainProjectIdentity,
        initial_candidate: CandidateInput | None = None,
    ) -> Project:
        revision = self.get_chain_revision(identity.chain_revision_id)
        if revision is None or revision.revision_digest != identity.chain_revision_digest:
            raise ChainCatalogConflictError(
                "選択したChain RevisionのIDまたはdigestが登録内容と一致しません"
            )
        project_id, now = str(uuid.uuid4()), _now()
        if (
            initial_candidate is not None
            and initial_candidate.provenance.source_kind != "copy"
        ):
            raise CandidateCopyConflictError(
                "Chain Projectの初期候補はコピー由来にしてください"
            )
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if initial_candidate is not None and initial_candidate.provenance.source_kind == "copy":
                reference = initial_candidate.provenance.source_ref
                source = conn.execute(
                    "SELECT projects.scientific_identity_json,candidates.revision "
                    "FROM candidates JOIN projects ON projects.id=candidates.project_id "
                    "WHERE candidates.id=? AND candidates.project_id=?",
                    (reference.candidate_id, reference.project_id),
                ).fetchone()
                source_identity = (
                    json.loads(source["scientific_identity_json"])
                    if source is not None
                    else {}
                )
                if (
                    source is None
                    or source["revision"] != reference.candidate_revision
                    or source_identity.get("identity_kind") != "chain"
                    or ChainProjectIdentity.model_validate(source_identity) != identity
                ):
                    raise CandidateCopyConflictError(
                        "コピー元候補のChain Revisionまたはcandidate revisionが一致しません"
                    )
            project_series_id = self._project_series_id_for_create(
                conn, payload, now
            )
            conn.execute(
                "INSERT INTO projects("
                "id,name,description,purpose,task_id,target_values,input_ranges,"
                "response_curve_ranges,response_curve_points,heat_stage_positions_m,"
                "display_decimals,notes,decision_candidate_id,decision_snapshot_id,"
                "decision_note,dataset_view_revision_id,task_contract_digest,"
                "model_package_ref_id,model_package_manifest_digest,project_series_id,"
                "predecessor_project_id,continuation_reason,binding_provenance,"
                "scientific_identity_json,objective_definition_json,"
                "objective_definition_digest,objective_binding_provenance,"
                "created_at,updated_at"
                ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    project_id,
                    payload.name,
                    payload.description,
                    payload.purpose,
                    "",
                    _target_values_json(payload.target_values),
                    json.dumps(
                        {key: value.model_dump() for key, value in payload.input_ranges.items()},
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    json.dumps(
                        {
                            axis: {key: value.model_dump() for key, value in ranges.items()}
                            for axis, ranges in payload.response_curve_ranges.items()
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    payload.response_curve_points,
                    json.dumps(payload.heat_stage_positions_m, ensure_ascii=False, sort_keys=True),
                    json.dumps(payload.display_decimals, ensure_ascii=False, sort_keys=True),
                    payload.notes,
                    payload.decision_candidate_id,
                    payload.decision_snapshot_id,
                    payload.decision_note,
                    None,
                    "",
                    None,
                    "",
                    project_series_id,
                    payload.predecessor_project_id,
                    payload.continuation_reason,
                    "explicit",
                    identity.model_dump_json(),
                    (
                        payload.objective_definition.model_dump_json()
                        if payload.objective_definition is not None
                        else None
                    ),
                    (
                        payload.objective_definition.digest
                        if payload.objective_definition is not None
                        else None
                    ),
                    payload.objective_binding_provenance or "none_configured",
                    now,
                    now,
                ),
            )
            if payload.objective_definition is not None:
                conn.execute(
                    "INSERT INTO project_objective_revisions("
                    "project_id,objective_digest,revision,payload,"
                    "binding_provenance,created_at"
                    ") VALUES (?,?,?,?,?,?)",
                    (
                        project_id,
                        payload.objective_definition.digest,
                        payload.objective_definition.revision,
                        payload.objective_definition.model_dump_json(),
                        payload.objective_binding_provenance
                        or "generated_default",
                        now,
                    ),
                )
            if initial_candidate is not None:
                candidate_id = str(uuid.uuid4())
                conn.execute(
                    "INSERT INTO candidates(id,project_id,name,payload,created_at,updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        candidate_id,
                        project_id,
                        initial_candidate.name,
                        initial_candidate.model_dump_json(),
                        now,
                        now,
                    ),
                )
        return self.get_project(project_id)  # type: ignore[return-value]

    @staticmethod
    def _project_series_id_for_create(
        conn: sqlite3.Connection,
        payload: ProjectCreateInput,
        now: str,
    ) -> str | None:
        if payload.new_project_series is not None:
            series_id = f"project-series-{uuid.uuid4()}"
            conn.execute(
                "INSERT INTO project_series(id,name,description,created_at,updated_at) "
                "VALUES (?,?,?,?,?)",
                (
                    series_id,
                    payload.new_project_series.name,
                    payload.new_project_series.description,
                    now,
                    now,
                ),
            )
            return series_id
        if payload.project_series_id is None:
            return None
        available = conn.execute(
            "SELECT id FROM project_series WHERE id=? AND archived_at IS NULL",
            (payload.project_series_id,),
        ).fetchone()
        if available is None:
            raise ProjectGroupUnavailableError(
                "選択した検討グループを利用できません"
            )
        return payload.project_series_id

    def ensure_project(
        self,
        project_id: str,
        payload: ProjectInput,
        *,
        starter: bool = False,
    ) -> Project:
        existing = self.get_project(project_id)
        if existing is not None:
            if existing.task_id != payload.task_id:
                raise ValueError(f"reserved project {project_id} belongs to another task")
            if starter and not existing.starter:
                with self._connect() as conn:
                    conn.execute(
                        "UPDATE projects SET is_starter=1 WHERE id=?",
                        (project_id,),
                    )
                return self.get_project(project_id)  # type: ignore[return-value]
            return existing
        now = _now()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO projects(id, name, description, purpose, task_id, target_values, input_ranges, response_curve_ranges, response_curve_points, heat_stage_positions_m, display_decimals, notes, decision_candidate_id, decision_snapshot_id, decision_note, scientific_identity_json, is_starter, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    project_id,
                    payload.name,
                    payload.description,
                    payload.purpose,
                    payload.task_id,
                    _target_values_json(payload.target_values),
                    json.dumps(
                        {key: value.model_dump() for key, value in payload.input_ranges.items()},
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    json.dumps(
                        {
                            axis: {key: value.model_dump() for key, value in ranges.items()}
                            for axis, ranges in payload.response_curve_ranges.items()
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    payload.response_curve_points,
                    json.dumps(payload.heat_stage_positions_m, ensure_ascii=False, sort_keys=True),
                    json.dumps(payload.display_decimals, ensure_ascii=False, sort_keys=True),
                    payload.notes,
                    "",
                    "",
                    "",
                    SingleTaskProjectIdentity(
                        identity_kind="single_task",
                        task_id=payload.task_id,
                        binding_provenance="unbound_legacy",
                    ).model_dump_json(),
                    int(starter),
                    now,
                    now,
                ),
            )
        return self.get_project(project_id)  # type: ignore[return-value]

    def update_project(
        self,
        project_id: str,
        payload: ProjectUpdateInput,
        *,
        objective_definition: ObjectiveDefinition | None,
        objective_binding_provenance: str,
    ) -> Project | None:
        now = _now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._validate_decision(conn, project_id, payload.decision_candidate_id, payload.decision_snapshot_id)
            result = conn.execute(
                "UPDATE projects SET name=?, description=?, purpose=?, target_values=?, "
                "input_ranges=?, response_curve_ranges=?, response_curve_points=?, "
                "heat_stage_positions_m=?, display_decimals=?, notes=?, decision_candidate_id=?, "
                "decision_snapshot_id=?, decision_note=?, objective_definition_json=?, "
                "objective_definition_digest=?, objective_binding_provenance=?, updated_at=? "
                "WHERE id=?",
                (
                    payload.name, payload.description, payload.purpose,
                    _target_values_json(payload.target_values),
                    json.dumps({key: value.model_dump() for key, value in payload.input_ranges.items()}, ensure_ascii=False, sort_keys=True),
                    json.dumps({axis: {key: value.model_dump() for key, value in ranges.items()} for axis, ranges in payload.response_curve_ranges.items()}, ensure_ascii=False, sort_keys=True),
                    payload.response_curve_points,
                    json.dumps(payload.heat_stage_positions_m, ensure_ascii=False, sort_keys=True),
                    json.dumps(payload.display_decimals, ensure_ascii=False, sort_keys=True),
                    payload.notes, payload.decision_candidate_id, payload.decision_snapshot_id,
                    payload.decision_note,
                    objective_definition.model_dump_json() if objective_definition else None,
                    objective_definition.digest if objective_definition else None,
                    objective_binding_provenance,
                    now,
                    project_id,
                ),
            )
            if objective_definition is not None:
                existing_objective = conn.execute(
                    "SELECT payload FROM project_objective_revisions "
                    "WHERE project_id=? AND objective_digest=?",
                    (project_id, objective_definition.digest),
                ).fetchone()
                if existing_objective is None:
                    conn.execute(
                        "INSERT INTO project_objective_revisions("
                        "project_id,objective_digest,revision,payload,binding_provenance,created_at"
                        ") VALUES (?,?,?,?,?,?)",
                        (
                            project_id,
                            objective_definition.digest,
                            objective_definition.revision,
                            objective_definition.model_dump_json(),
                            objective_binding_provenance,
                            now,
                        ),
                    )
                elif (
                    ObjectiveDefinition.model_validate_json(
                        existing_objective["payload"]
                    )
                    != objective_definition
                ):
                    raise StoreDataIntegrityError(
                        "同じObjective digestに異なる定義があります"
                    )
        return self.get_project(project_id) if result.rowcount else None

    def list_project_objective_revisions(
        self,
        project_id: str,
    ) -> list[ObjectiveDefinitionRevision]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT project_id,objective_digest,revision,payload,"
                "binding_provenance,created_at "
                "FROM project_objective_revisions WHERE project_id=? "
                "ORDER BY revision",
                (project_id,),
            ).fetchall()
        return [
            ObjectiveDefinitionRevision(
                project_id=row["project_id"],
                objective_definition=ObjectiveDefinition.model_validate_json(
                    row["payload"]
                ),
                objective_definition_digest=row["objective_digest"],
                binding_provenance=row["binding_provenance"],
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in rows
        ]

    def move_project_to_group(self, project_id: str, payload: ProjectGroupMoveInput) -> Project:
        now = _now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            project_row = conn.execute(
                "SELECT project_series_id FROM projects WHERE id=?", (project_id,)
            ).fetchone()
            if project_row is None:
                raise ProjectNotFoundError(project_id)
            current_group_id = project_row["project_series_id"]
            if current_group_id != payload.expected_project_series_id:
                raise ProjectGroupConflictError(
                    "このプロジェクトの所属グループは別の操作で変更されています"
                )
            if payload.project_series_id is not None:
                target_group = conn.execute(
                    "SELECT archived_at FROM project_series WHERE id=?",
                    (payload.project_series_id,),
                ).fetchone()
                if target_group is None or target_group["archived_at"] is not None:
                    raise ProjectGroupUnavailableError("移動先の検討グループを利用できません")
            if current_group_id == payload.project_series_id:
                return self._project(
                    conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
                )
            conn.execute(
                "UPDATE projects SET project_series_id=?,updated_at=? WHERE id=?",
                (payload.project_series_id, now, project_id),
            )
            if current_group_id:
                conn.execute(
                    "UPDATE project_series SET archived_at=?,updated_at=? "
                    "WHERE id=? AND archived_at IS NULL "
                    "AND NOT EXISTS (SELECT 1 FROM projects WHERE project_series_id=?)",
                    (now, now, current_group_id, current_group_id),
                )
            row = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        return self._project(row)

    def archive_project(self, project_id: str) -> Project | None:
        if project_id in PROTECTED_PROJECT_IDS:
            raise ProtectedProjectError("予約プロジェクトはアーカイブできません")
        now = _now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            project_row = conn.execute(
                "SELECT project_series_id,archived_at FROM projects WHERE id=?",
                (project_id,),
            ).fetchone()
            if project_row is None:
                return None
            if project_row["archived_at"] is None:
                # Revoke in-flight Chain claims while the Project is still writable;
                # archive guards reject every subsequent scope-table mutation.
                conn.execute(
                    "DELETE FROM chain_execution_claims WHERE scope_id LIKE ?",
                    (f"{project_id}:%",),
                )
                conn.execute(
                    "UPDATE projects SET archived_at=?,updated_at=? WHERE id=?",
                    (now, now, project_id),
                )
                series_id = project_row["project_series_id"]
                if series_id:
                    conn.execute(
                        "UPDATE project_series SET archived_at=?,updated_at=? "
                        "WHERE id=? AND archived_at IS NULL "
                        "AND NOT EXISTS ("
                        "SELECT 1 FROM projects "
                        "WHERE projects.project_series_id=project_series.id "
                        "AND projects.archived_at IS NULL"
                        ")",
                        (now, now, series_id),
                    )
            row = conn.execute(
                "SELECT * FROM projects WHERE id=?", (project_id,)
            ).fetchone()
        return self._project(row)

    def restore_project(self, project_id: str) -> Project | None:
        if project_id in PROTECTED_PROJECT_IDS:
            raise ProtectedProjectError("予約プロジェクトは復元操作の対象外です")
        now = _now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM projects WHERE id=?", (project_id,)
            ).fetchone()
            if row is None:
                return None
            if row["archived_at"] is not None:
                conn.execute(
                    "UPDATE projects SET archived_at=NULL,updated_at=? WHERE id=?",
                    (now, project_id),
                )
                if row["project_series_id"]:
                    conn.execute(
                        "UPDATE project_series SET archived_at=NULL,updated_at=? "
                        "WHERE id=?",
                        (now, row["project_series_id"]),
                    )
            restored = conn.execute(
                "SELECT * FROM projects WHERE id=?", (project_id,)
            ).fetchone()
        return self._project(restored)

    @staticmethod
    def _candidate_provenance_references_project(
        conn: sqlite3.Connection,
        payload: Mapping[str, Any],
        project_id: str,
    ) -> bool:
        provenance = payload.get("provenance")
        if not isinstance(provenance, dict):
            return False
        source_kind = provenance.get("source_kind")
        source_ref = provenance.get("source_ref")
        if not isinstance(source_ref, dict):
            return False
        if source_kind in {"copy", "blend_optimization"}:
            return source_ref.get("project_id") == project_id
        if source_kind == "screening":
            row = conn.execute(
                "SELECT project_id FROM screening_runs WHERE id=?",
                (source_ref.get("run_id"),),
            ).fetchone()
            return row is not None and row["project_id"] == project_id
        if source_kind == "snapshot":
            row = conn.execute(
                "SELECT candidates.project_id FROM snapshots "
                "JOIN candidates ON candidates.id=snapshots.candidate_id "
                "WHERE snapshots.id=?",
                (source_ref.get("snapshot_id"),),
            ).fetchone()
            return row is not None and row["project_id"] == project_id
        if source_kind == "decision_activity":
            row = conn.execute(
                "SELECT project_id FROM decision_activity_runs WHERE id=?",
                (source_ref.get("run_id"),),
            ).fetchone()
            return row is not None and row["project_id"] == project_id
        return False

    def purge_project(self, project_id: str) -> bool:
        if project_id in PROTECTED_PROJECT_IDS:
            raise ProtectedProjectError("予約プロジェクトは完全削除できません")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            project_row = conn.execute(
                "SELECT project_series_id,archived_at FROM projects WHERE id=?",
                (project_id,),
            ).fetchone()
            if project_row is None:
                return False
            if project_row["archived_at"] is None:
                raise ActiveProjectPurgeError(
                    "完全削除する前にプロジェクトをアーカイブしてください"
                )
            successor = conn.execute(
                "SELECT id FROM projects WHERE predecessor_project_id=? LIMIT 1", (project_id,)
            ).fetchone()
            if successor is not None:
                raise ProjectHasSuccessorsError(
                    "後続プロジェクトがあるため削除できません。続き元の関係を保持してください"
                )
            for derived_row in conn.execute(
                "SELECT candidate_id AS id,payload FROM candidate_revisions "
                "WHERE project_id<>?",
                (project_id,),
            ):
                try:
                    payload = json.loads(derived_row["payload"])
                except (TypeError, json.JSONDecodeError) as exc:
                    raise StoreDataIntegrityError(
                        f"候補 {derived_row['id']} の派生元を確認できません"
                    ) from exc
                if isinstance(payload, dict) and self._candidate_provenance_references_project(
                    conn, payload, project_id
                ):
                    raise ProjectHasDerivedCandidatesError(
                        "派生候補が別のプロジェクトにある、またはこのProjectの"
                        "証跡を参照する候補revisionがあるため完全削除できません"
                    )
            candidate_ids = [
                row["id"]
                for row in conn.execute("SELECT id FROM candidates WHERE project_id=?", (project_id,)).fetchall()
            ]
            conn.execute(
                "INSERT INTO project_purge_authorizations(project_id) VALUES (?)",
                (project_id,),
            )
            if candidate_ids:
                placeholders = ",".join("?" for _ in candidate_ids)
                for table in PROJECT_PERSISTENCE.candidate_tables:
                    conn.execute(
                        f"DELETE FROM {table} "
                        f"WHERE candidate_id IN ({placeholders})",
                        candidate_ids,
                    )
            for table in PROJECT_PERSISTENCE.scope_tables:
                conn.execute(
                    f"DELETE FROM {table} WHERE scope_id LIKE ?",
                    (f"{project_id}:%",),
                )
            for table in PROJECT_PERSISTENCE.direct_tables:
                column = "id" if table == "projects" else "project_id"
                conn.execute(
                    f"DELETE FROM {table} WHERE {column}=?",
                    (project_id,),
                )
            conn.execute(
                "DELETE FROM project_purge_authorizations WHERE project_id=?",
                (project_id,),
            )
            series_id = project_row["project_series_id"]
            if series_id:
                now = _now()
                conn.execute(
                    "UPDATE project_series SET archived_at=?,updated_at=? "
                    "WHERE id=? AND archived_at IS NULL "
                    "AND NOT EXISTS ("
                    "SELECT 1 FROM projects "
                    "WHERE projects.project_series_id=project_series.id "
                    "AND projects.archived_at IS NULL"
                    ")",
                    (now, now, series_id),
                )
            return True

    def project_has_persisted_evidence(self, project_id: str) -> bool:
        """Return whether removing a starter would discard user-created evidence.

        Project, Candidate, and Candidate Revision baseline rows are checked by
        the caller against the current starter definition. Every other
        Project-owned record is evidence and makes removal unsafe.
        """
        baseline_tables = {
            "projects",
            "candidates",
            "candidate_revisions",
        }
        with self._connect() as conn:
            candidate_ids = [
                row["id"]
                for row in conn.execute(
                    "SELECT id FROM candidates WHERE project_id=?",
                    (project_id,),
                ).fetchall()
            ]
            for table in PROJECT_PERSISTENCE.direct_tables:
                if table in baseline_tables:
                    continue
                row = conn.execute(
                    f"SELECT 1 FROM {table} WHERE project_id=? LIMIT 1",
                    (project_id,),
                ).fetchone()
                if row is not None:
                    return True
            if candidate_ids:
                placeholders = ",".join("?" for _ in candidate_ids)
                for table in PROJECT_PERSISTENCE.candidate_tables:
                    row = conn.execute(
                        f"SELECT 1 FROM {table} "
                        f"WHERE candidate_id IN ({placeholders}) LIMIT 1",
                        candidate_ids,
                    ).fetchone()
                    if row is not None:
                        return True
        return False

    @staticmethod
    def _lineage_review(row: sqlite3.Row) -> LineageNodeReview:
        return LineageNodeReview(
            project_id=row["project_id"],
            entity_key=row["entity_key"],
            entity_type=row["entity_type"],
            status=row["status"],
            note=row["note"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def list_lineage_reviews(self, project_id: str) -> list[LineageNodeReview]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM lineage_node_reviews WHERE project_id=? "
                "ORDER BY updated_at DESC, entity_key",
                (project_id,),
            ).fetchall()
        return [self._lineage_review(row) for row in rows]

    def get_lineage_review(
        self, project_id: str, entity_key: str
    ) -> LineageNodeReview | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM lineage_node_reviews WHERE project_id=? AND entity_key=?",
                (project_id, entity_key),
            ).fetchone()
        return self._lineage_review(row) if row else None

    def upsert_lineage_review(
        self,
        project_id: str,
        entity_key: str,
        payload: LineageNodeReviewInput,
    ) -> LineageNodeReview:
        now = _now()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO lineage_node_reviews("
                "project_id,entity_key,entity_type,status,note,created_at,updated_at"
                ") VALUES (?,?,?,?,?,?,?) "
                "ON CONFLICT(project_id,entity_key) DO UPDATE SET "
                "entity_type=excluded.entity_type,status=excluded.status,"
                "note=excluded.note,updated_at=excluded.updated_at",
                (
                    project_id,
                    entity_key,
                    payload.entity_type,
                    payload.status,
                    payload.note,
                    now,
                    now,
                ),
            )
        return self.get_lineage_review(project_id, entity_key)  # type: ignore[return-value]

    def delete_lineage_review(self, project_id: str, entity_key: str) -> bool:
        with self._connect() as conn:
            result = conn.execute(
                "DELETE FROM lineage_node_reviews WHERE project_id=? AND entity_key=?",
                (project_id, entity_key),
            )
        return bool(result.rowcount)

    @staticmethod
    def _validate_decision(conn: sqlite3.Connection, project_id: str, candidate_id: str, snapshot_id: str) -> None:
        if not candidate_id:
            return
        project_row = conn.execute(
            "SELECT scientific_identity_json FROM projects WHERE id=?",
            (project_id,),
        ).fetchone()
        if project_row is None:
            raise InvalidProjectDecisionError("プロジェクトが見つかりません")
        try:
            identity_kind = json.loads(
                project_row["scientific_identity_json"]
            ).get("identity_kind")
        except (TypeError, json.JSONDecodeError, AttributeError) as exc:
            raise InvalidProjectDecisionError(
                "プロジェクトの固定identityを確認できません"
            ) from exc
        if conn.execute("SELECT 1 FROM candidates WHERE id=? AND project_id=?", (candidate_id, project_id)).fetchone() is None:
            raise InvalidProjectDecisionError("採用候補は同じプロジェクトから選択してください")
        if identity_kind == "chain":
            snapshot = conn.execute(
                "SELECT 1 FROM chain_snapshot_records "
                "WHERE id=? AND project_id=? AND candidate_id=?",
                (snapshot_id, project_id, candidate_id),
            ).fetchone()
        else:
            snapshot = conn.execute(
                "SELECT 1 FROM snapshots WHERE id=? AND candidate_id=?",
                (snapshot_id, candidate_id),
            ).fetchone()
        if snapshot is None:
            raise InvalidProjectDecisionError(
                "プロジェクト種別に対応する判断時点のSnapshotが見つかりません"
            )

    def update_project_decision(self, project_id: str, candidate_id: str, snapshot_id: str, note: str) -> Project | None:
        now = _now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._validate_decision(conn, project_id, candidate_id, snapshot_id)
            result = conn.execute(
                "UPDATE projects SET decision_candidate_id=?, decision_snapshot_id=?, decision_note=?, updated_at=? WHERE id=?",
                (candidate_id, snapshot_id, note, now, project_id),
            )
        return self.get_project(project_id) if result.rowcount else None

    @staticmethod
    def _candidate(row: sqlite3.Row) -> Candidate:
        payload = json.loads(row["payload"])
        return Candidate(id=row["id"], project_id=row["project_id"], revision=row["revision"], archived_at=datetime.fromisoformat(row["archived_at"]) if row["archived_at"] else None, created_at=datetime.fromisoformat(row["created_at"]), updated_at=datetime.fromisoformat(row["updated_at"]), **payload)

    def list_candidates(self, project_id: str = "default", *, include_archived: bool = False) -> list[Candidate]:
        with self._connect() as conn:
            where = "project_id = ?" if include_archived else "project_id = ? AND archived_at IS NULL"
            return [self._candidate(row) for row in conn.execute(f"SELECT * FROM candidates WHERE {where} ORDER BY created_at", (project_id,))]

    def get_candidate(self, candidate_id: str, project_id: str | None = None, *, include_archived: bool = False) -> Candidate | None:
        with self._connect() as conn:
            active = "" if include_archived else " AND archived_at IS NULL"
            if project_id is None:
                row = conn.execute(f"SELECT * FROM candidates WHERE id = ?{active}", (candidate_id,)).fetchone()
            else:
                row = conn.execute(f"SELECT * FROM candidates WHERE id = ? AND project_id = ?{active}", (candidate_id, project_id)).fetchone()
        return self._candidate(row) if row else None

    def get_candidate_revision(
        self,
        candidate_id: str,
        revision: int,
        project_id: str | None = None,
    ) -> Candidate | None:
        with self._connect() as conn:
            if project_id is None:
                row = conn.execute(
                    "SELECT candidate_id AS id,project_id,revision,name,payload,"
                    "archived_at,created_at,updated_at FROM candidate_revisions "
                    "WHERE candidate_id=? AND revision=?",
                    (candidate_id, revision),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT candidate_id AS id,project_id,revision,name,payload,"
                    "archived_at,created_at,updated_at FROM candidate_revisions "
                    "WHERE candidate_id=? AND project_id=? AND revision=?",
                    (candidate_id, project_id, revision),
                ).fetchone()
        return self._candidate(row) if row else None

    @staticmethod
    def _record_candidate_revision(conn: sqlite3.Connection, row: sqlite3.Row) -> None:
        conn.execute(
            "INSERT INTO candidate_revisions("
            "candidate_id,project_id,revision,name,payload,archived_at,created_at,updated_at"
            ") VALUES (?,?,?,?,?,?,?,?)",
            (
                row["id"],
                row["project_id"],
                row["revision"],
                row["name"],
                row["payload"],
                row["archived_at"],
                row["created_at"],
                row["updated_at"],
            ),
        )

    def project_history(self, project_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            conn.execute("BEGIN")
            project_row = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
            if project_row is None:
                return None
            candidate_rows = conn.execute(
                "SELECT * FROM candidates WHERE project_id=? ORDER BY updated_at DESC, created_at DESC",
                (project_id,),
            ).fetchall()
            snapshot_rows = conn.execute(
                "SELECT snapshots.* FROM snapshots JOIN candidates ON candidates.id=snapshots.candidate_id "
                "WHERE candidates.project_id=? ORDER BY snapshots.created_at DESC",
                (project_id,),
            ).fetchall()
            actual_rows = conn.execute(
                "SELECT actual_measurements.* FROM actual_measurements "
                "JOIN candidates ON candidates.id=actual_measurements.candidate_id "
                "WHERE candidates.project_id=? ORDER BY actual_measurements.created_at DESC",
                (project_id,),
            ).fetchall()
            chain_snapshot_rows = conn.execute(
                "SELECT * FROM chain_snapshot_records "
                "WHERE project_id=? ORDER BY created_at DESC,id DESC",
                (project_id,),
            ).fetchall()
            chain_variant_rows = conn.execute(
                "SELECT * FROM chain_analysis_variant_records "
                "WHERE project_id=? ORDER BY created_at DESC,id DESC",
                (project_id,),
            ).fetchall()
            chain_distribution_rows = conn.execute(
                "SELECT * FROM chain_distribution_runs "
                "WHERE project_id=? ORDER BY created_at DESC,id DESC",
                (project_id,),
            ).fetchall()

        project = self._project(project_row)
        candidate_ids = {row["id"] for row in candidate_rows}
        chain_identity = (
            project.scientific_identity
            if project.scientific_identity.identity_kind == "chain"
            else None
        )
        if chain_identity is None and (
            chain_snapshot_rows
            or chain_variant_rows
            or chain_distribution_rows
        ):
            raise StoreDataIntegrityError(
                "single-Task ProjectにChain証拠が混在しています"
            )
        if chain_identity is not None and snapshot_rows:
            raise StoreDataIntegrityError(
                "Chain Projectにsingle-Task Snapshotが混在しています"
            )
        snapshots_by_candidate: dict[str, list[dict[str, Any]]] = {}
        for row in snapshot_rows:
            try:
                payload = json.loads(row["payload"])
            except (TypeError, json.JSONDecodeError) as exc:
                raise StoreDataIntegrityError(f"snapshot {row['id']} を読み取れません") from exc
            version = payload.get("snapshot_schema_version") if isinstance(payload, dict) else None
            if version != "prediction-snapshot-v2":
                raise StoreDataIntegrityError(f"snapshot {row['id']} の形式を解釈できません")
            raw_candidate = payload.get("raw_candidate")
            candidate_revision = raw_candidate.get("revision") if version == "prediction-snapshot-v2" and isinstance(raw_candidate, dict) else None
            prediction = payload.get("prediction")
            if not isinstance(prediction, dict) or not isinstance(prediction.get("predictions"), dict):
                raise StoreDataIntegrityError(f"snapshot {row['id']} の予測要約を読み取れません")
            snapshots_by_candidate.setdefault(row["candidate_id"], []).append({
                "id": row["id"],
                "candidate_id": row["candidate_id"],
                "created_at": row["created_at"],
                "candidate_revision": candidate_revision,
                "prediction_summary": prediction["predictions"],
                "model_ref": payload.get("provenance"),
            })
        actuals_by_candidate: dict[str, list[ActualMeasurement]] = {}
        for row in actual_rows:
            snapshot_ids = {item["id"] for item in snapshots_by_candidate.get(row["candidate_id"], [])}
            if row["snapshot_id"] not in snapshot_ids:
                raise StoreDataIntegrityError(f"actual {row['id']} の固定snapshotが見つかりません")
            actuals_by_candidate.setdefault(row["candidate_id"], []).append(self._actual(row))
        chain_snapshots_by_candidate: dict[str, list[ChainSnapshot]] = {}
        chain_snapshots_by_id: dict[str, ChainSnapshot] = {}
        for row in chain_snapshot_rows:
            try:
                snapshot = ChainSnapshot.model_validate_json(row["payload_json"])
            except (TypeError, ValueError) as exc:
                raise StoreDataIntegrityError(
                    f"Chain snapshot {row['id']} を読み取れません"
                ) from exc
            if (
                snapshot.snapshot_id != row["id"]
                or row["project_id"] != project.id
                or snapshot.identity.candidate_id != row["candidate_id"]
                or snapshot.identity.candidate_revision
                != row["candidate_revision"]
                or row["candidate_id"] not in candidate_ids
                or chain_identity is None
                or snapshot.identity.chain_revision_id
                != chain_identity.chain_revision_id
                or snapshot.identity.chain_revision_digest
                != chain_identity.chain_revision_digest
            ):
                raise StoreDataIntegrityError(
                    f"Chain snapshot {row['id']} の固定参照が一致しません"
                )
            chain_snapshots_by_candidate.setdefault(
                row["candidate_id"], []
            ).append(snapshot)
            chain_snapshots_by_id[snapshot.snapshot_id] = snapshot
        chain_variants_by_candidate: dict[
            str, list[ActualConditionedVariant]
        ] = {}
        for row in chain_variant_rows:
            try:
                variant = ActualConditionedVariant.model_validate_json(
                    row["payload_json"]
                )
            except (TypeError, ValueError) as exc:
                raise StoreDataIntegrityError(
                    f"実測variant {row['id']} を読み取れません"
                ) from exc
            comparison_snapshot = chain_snapshots_by_id.get(
                variant.identity.comparison_snapshot_id
            )
            if (
                variant.variant_id != row["id"]
                or variant.project_id != row["project_id"]
                or row["project_id"] != project.id
                or variant.identity.base_candidate_id != row["candidate_id"]
                or variant.identity.base_candidate_revision
                != row["candidate_revision"]
                or variant.identity.comparison_snapshot_id
                != row["comparison_snapshot_id"]
                or row["candidate_id"] not in candidate_ids
                or chain_identity is None
                or variant.identity.base_chain_revision_id
                != chain_identity.chain_revision_id
                or variant.identity.base_chain_revision_digest
                != chain_identity.chain_revision_digest
                or comparison_snapshot is None
                or comparison_snapshot.identity.candidate_id
                != variant.identity.base_candidate_id
                or comparison_snapshot.identity.candidate_revision
                != variant.identity.base_candidate_revision
            ):
                raise StoreDataIntegrityError(
                    f"実測variant {row['id']} の固定参照が一致しません"
                )
            chain_variants_by_candidate.setdefault(
                row["candidate_id"], []
            ).append(variant)
        chain_distributions_by_candidate: dict[
            str, list[ChainDistributionRun]
        ] = {}
        for row in chain_distribution_rows:
            try:
                run = ChainDistributionRun.model_validate_json(
                    row["payload_json"]
                )
            except (TypeError, ValueError) as exc:
                raise StoreDataIntegrityError(
                    f"Chain分布run {row['id']} を読み取れません"
                ) from exc
            if (
                run.run_id != row["id"]
                or run.project_id != row["project_id"]
                or row["project_id"] != project.id
                or run.provenance.candidate_id != row["candidate_id"]
                or run.provenance.candidate_revision
                != row["candidate_revision"]
                or row["candidate_id"] not in candidate_ids
                or chain_identity is None
                or run.provenance.chain_revision_id
                != chain_identity.chain_revision_id
                or run.provenance.chain_revision_digest
                != row["chain_revision_digest"]
                or run.provenance.chain_revision_digest
                != chain_identity.chain_revision_digest
            ):
                raise StoreDataIntegrityError(
                    f"Chain分布run {row['id']} の固定参照が一致しません"
                )
            chain_distributions_by_candidate.setdefault(
                row["candidate_id"], []
            ).append(run)
        items = []
        for row in candidate_rows:
            candidate = self._candidate(row)
            decision = None
            if project.decision_candidate_id == candidate.id and project.decision_snapshot_id:
                fixed_snapshot_ids = (
                    {
                        item.snapshot_id
                        for item in chain_snapshots_by_candidate.get(
                            candidate.id, []
                        )
                    }
                    if chain_identity is not None
                    else {
                        item["id"]
                        for item in snapshots_by_candidate.get(candidate.id, [])
                    }
                )
                if project.decision_snapshot_id not in fixed_snapshot_ids:
                    raise StoreDataIntegrityError("採用判断の固定snapshotが見つかりません")
                decision = {
                    "candidate_id": candidate.id,
                    "snapshot_id": project.decision_snapshot_id,
                    "note": project.decision_note,
                }
            items.append({
                "candidate": candidate,
                "current": {"revision": candidate.revision, "updated_at": candidate.updated_at},
                "snapshots": snapshots_by_candidate.get(candidate.id, []),
                "chain_snapshots": chain_snapshots_by_candidate.get(
                    candidate.id, []
                ),
                "chain_analysis_variants": chain_variants_by_candidate.get(
                    candidate.id, []
                ),
                "chain_distribution_runs": chain_distributions_by_candidate.get(
                    candidate.id, []
                ),
                "actuals": actuals_by_candidate.get(candidate.id, []),
                "decision": decision,
            })
        return {"project": project, "candidates": items}

    def create_candidate(self, payload: CandidateInput, project_id: str = "default") -> Candidate:
        return self.create_candidates([payload], project_id)[0]

    def create_candidates(self, payloads: list[CandidateInput], project_id: str = "default") -> list[Candidate]:
        if not payloads:
            return []
        records: list[tuple[str, str, str, str, str, str]] = []
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT 1 FROM projects WHERE id = ?", (project_id,)).fetchone() is None:
                raise ProjectNotFoundError(project_id)
            current = int(conn.execute("SELECT COUNT(*) FROM candidates WHERE project_id = ? AND archived_at IS NULL", (project_id,)).fetchone()[0])
            if current + len(payloads) > MAX_CANDIDATES_PER_PROJECT:
                raise CandidateLimitError(f"候補は1プロジェクトにつき最大{MAX_CANDIDATES_PER_PROJECT}件です")
            for payload in payloads:
                candidate_id, now = str(uuid.uuid4()), _now()
                records.append((candidate_id, project_id, payload.name, payload.model_dump_json(), now, now))
            conn.executemany("INSERT INTO candidates(id,project_id,name,payload,created_at,updated_at) VALUES (?, ?, ?, ?, ?, ?)", records)
            for candidate_id, *_ in records:
                row = conn.execute(
                    "SELECT * FROM candidates WHERE id=?",
                    (candidate_id,),
                ).fetchone()
                self._record_candidate_revision(conn, row)
        created = [self.get_candidate(candidate_id) for candidate_id, *_ in records]
        if any(candidate is None for candidate in created):
            raise RuntimeError("作成した候補を再取得できませんでした")
        return created  # type: ignore[return-value]

    def create_screening_candidates(
        self,
        payloads: list[tuple[int, CandidateInput]],
        run_id: str,
        project_id: str,
    ) -> tuple[list[Candidate], list[int]]:
        records: list[tuple[str, str, str, str, str, str]] = []
        skipped: list[int] = []
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT 1 FROM projects WHERE id = ?", (project_id,)).fetchone() is None:
                raise ProjectNotFoundError(project_id)
            active_rows = conn.execute(
                "SELECT * FROM candidates WHERE project_id = ? AND archived_at IS NULL",
                (project_id,),
            ).fetchall()
            existing = set()
            for row in active_rows:
                candidate = self._candidate(row)
                provenance = candidate.provenance.model_dump(mode="json")
                reference = provenance.get("source_ref") or {}
                if provenance.get("source_kind") == "screening" and reference.get("run_id") == run_id:
                    existing.add(int(reference.get("point_index", -1)))
            unique_payloads: list[tuple[int, CandidateInput]] = []
            seen = set(existing)
            for point_index, payload in payloads:
                if point_index in seen:
                    skipped.append(point_index)
                    continue
                seen.add(point_index)
                unique_payloads.append((point_index, payload))
            if len(active_rows) + len(unique_payloads) > MAX_CANDIDATES_PER_PROJECT:
                raise CandidateLimitError(f"候補は1プロジェクトにつき最大{MAX_CANDIDATES_PER_PROJECT}件です")
            for _, payload in unique_payloads:
                candidate_id, now = str(uuid.uuid4()), _now()
                records.append((candidate_id, project_id, payload.name, payload.model_dump_json(), now, now))
            if records:
                conn.executemany("INSERT INTO candidates(id,project_id,name,payload,created_at,updated_at) VALUES (?, ?, ?, ?, ?, ?)", records)
                for candidate_id, *_ in records:
                    row = conn.execute(
                        "SELECT * FROM candidates WHERE id=?",
                        (candidate_id,),
                    ).fetchone()
                    self._record_candidate_revision(conn, row)
        created = [self.get_candidate(candidate_id, project_id) for candidate_id, *_ in records]
        if any(candidate is None for candidate in created):
            raise RuntimeError("作成した候補を再取得できませんでした")
        return created, skipped  # type: ignore[return-value]

    def update_candidate(self, candidate_id: str, project_id: str, payload: CandidateInput, expected_revision: int) -> Candidate | None:
        now = _now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            result = conn.execute(
                "UPDATE candidates SET name = ?, payload = ?, revision = revision + 1, updated_at = ? WHERE id = ? AND project_id = ? AND revision = ? AND archived_at IS NULL",
                (payload.name, payload.model_dump_json(), now, candidate_id, project_id, expected_revision),
            )
            if not result.rowcount:
                row = conn.execute("SELECT * FROM candidates WHERE id=? AND project_id=?", (candidate_id, project_id)).fetchone()
                if row is None:
                    return None
                current = self._candidate(row)
                if current.archived_at is not None:
                    raise CandidateArchivedError("archive済み候補は編集できません")
                raise CandidateRevisionConflictError(current)
            row = conn.execute(
                "SELECT * FROM candidates WHERE id=? AND project_id=?",
                (candidate_id, project_id),
            ).fetchone()
            self._record_candidate_revision(conn, row)
        return self.get_candidate(candidate_id, project_id)

    def update_chain_candidate(
        self,
        candidate_id: str,
        project_id: str,
        payload: CandidateInput,
        expected_revision: int,
        invalidation_request_id: str,
    ) -> tuple[Candidate | None, int]:
        """Update a Chain candidate and invalidate older execution writes atomically."""

        now = _now()
        scope_id = self.chain_execution_scope(project_id, candidate_id)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            result = conn.execute(
                "UPDATE candidates SET name=?,payload=?,revision=revision+1,updated_at=? "
                "WHERE id=? AND project_id=? AND revision=? AND archived_at IS NULL",
                (
                    payload.name,
                    payload.model_dump_json(),
                    now,
                    candidate_id,
                    project_id,
                    expected_revision,
                ),
            )
            if not result.rowcount:
                row = conn.execute(
                    "SELECT * FROM candidates WHERE id=? AND project_id=?",
                    (candidate_id, project_id),
                ).fetchone()
                if row is None:
                    return None, 0
                current = self._candidate(row)
                if current.archived_at is not None:
                    raise CandidateArchivedError("archive済み候補は編集できません")
                raise CandidateRevisionConflictError(current)
            row = conn.execute(
                "SELECT * FROM candidates WHERE id=? AND project_id=?",
                (candidate_id, project_id),
            ).fetchone()
            self._record_candidate_revision(conn, row)
            generation = self._claim_chain_execution(
                conn, scope_id, invalidation_request_id
            )
            updated = self._candidate(row)
        return updated, generation

    def delete_candidate(self, candidate_id: str, project_id: str, expected_revision: int) -> bool:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM candidates WHERE id=? AND project_id=?", (candidate_id, project_id)).fetchone()
            if row is None:
                return False
            current = self._candidate(row)
            if current.archived_at is not None:
                raise CandidateArchivedError("候補はすでにarchiveされています")
            if current.revision != expected_revision:
                raise CandidateRevisionConflictError(current)
            now = _now()
            updated = conn.execute(
                "UPDATE candidates SET archived_at=?, revision=revision+1, updated_at=? "
                "WHERE id=? AND project_id=? AND revision=? AND archived_at IS NULL",
                (now, now, candidate_id, project_id, expected_revision),
            )
            if not updated.rowcount:
                latest = conn.execute("SELECT * FROM candidates WHERE id=? AND project_id=?", (candidate_id, project_id)).fetchone()
                if latest is None:
                    return False
                raise CandidateRevisionConflictError(self._candidate(latest))
            archived = conn.execute(
                "SELECT * FROM candidates WHERE id=? AND project_id=?",
                (candidate_id, project_id),
            ).fetchone()
            self._record_candidate_revision(conn, archived)
            return True

    def restore_candidate(self, candidate_id: str, project_id: str) -> Candidate | None:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM candidates WHERE id=? AND project_id=?",
                (candidate_id, project_id),
            ).fetchone()
            if row is None:
                return None
            if row["archived_at"] is None:
                return self._candidate(row)
            active_count = int(
                conn.execute(
                    "SELECT COUNT(*) FROM candidates WHERE project_id=? AND archived_at IS NULL",
                    (project_id,),
                ).fetchone()[0]
            )
            if active_count >= MAX_CANDIDATES_PER_PROJECT:
                raise CandidateLimitError(
                    f"候補は1プロジェクトにつき最大{MAX_CANDIDATES_PER_PROJECT}件です"
                )
            now = _now()
            conn.execute(
                "UPDATE candidates SET archived_at=NULL, revision=revision+1, updated_at=? "
                "WHERE id=? AND project_id=? AND archived_at IS NOT NULL",
                (now, candidate_id, project_id),
            )
            restored = conn.execute(
                "SELECT * FROM candidates WHERE id=? AND project_id=?",
                (candidate_id, project_id),
            ).fetchone()
            self._record_candidate_revision(conn, restored)
            return self._candidate(restored)

    def create_snapshot(self, candidate_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        snapshot = {"id": str(uuid.uuid4()), "candidate_id": candidate_id, "created_at": _now(), "payload": payload}
        with self._connect() as conn:
            conn.execute("INSERT INTO snapshots VALUES (?, ?, ?, ?)", (snapshot["id"], candidate_id, json.dumps(payload, ensure_ascii=False, sort_keys=True), snapshot["created_at"]))
        return snapshot

    def list_snapshots(self, candidate_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM snapshots WHERE candidate_id = ? ORDER BY created_at DESC", (candidate_id,)).fetchall()
        return [{"id": row["id"], "candidate_id": row["candidate_id"], "created_at": row["created_at"], "payload": json.loads(row["payload"])} for row in rows]

    def get_snapshot(self, snapshot_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM snapshots WHERE id = ?", (snapshot_id,)).fetchone()
        return {"id": row["id"], "candidate_id": row["candidate_id"], "created_at": row["created_at"], "payload": json.loads(row["payload"])} if row else None

    def create_screening_run(self, payload: dict[str, Any], project_id: str = "default") -> dict[str, Any]:
        run = {"id": str(uuid.uuid4()), "project_id": project_id, "created_at": _now(), **payload}
        with self._connect() as conn:
            if conn.execute("SELECT 1 FROM projects WHERE id = ?", (project_id,)).fetchone() is None:
                raise ProjectNotFoundError(project_id)
            conn.execute("INSERT INTO screening_runs VALUES (?, ?, ?, ?)", (run["id"], project_id, json.dumps(payload, ensure_ascii=False, sort_keys=True), run["created_at"]))
        return run

    def get_screening_run(self, run_id: str, project_id: str | None = None) -> dict[str, Any] | None:
        with self._connect() as conn:
            if project_id is None:
                row = conn.execute("SELECT * FROM screening_runs WHERE id = ?", (run_id,)).fetchone()
            else:
                row = conn.execute("SELECT * FROM screening_runs WHERE id = ? AND project_id = ?", (run_id, project_id)).fetchone()
        return {"id": row["id"], "project_id": row["project_id"], "created_at": row["created_at"], **json.loads(row["payload"])} if row else None

    def list_screening_runs(self, project_id: str = "default") -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM screening_runs WHERE project_id = ? ORDER BY created_at DESC", (project_id,)).fetchall()
        return [{"id": row["id"], "project_id": row["project_id"], "created_at": row["created_at"], **json.loads(row["payload"])} for row in rows]

    @staticmethod
    def _decision_activity_run(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "semantic_identity": row["semantic_identity"],
            "project_id": row["project_id"],
            "created_at": row["created_at"],
            **json.loads(row["payload"]),
        }

    def create_decision_activity_run(
        self,
        *,
        semantic_identity: str,
        project_id: str,
        candidate_id: str,
        activity_id: str,
        activity_version: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        run_id = f"activity-{semantic_identity.removeprefix('sha256:')[:24]}"
        created_at = _now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute(
                "SELECT 1 FROM candidates WHERE id=? AND project_id=?",
                (candidate_id, project_id),
            ).fetchone() is None:
                raise ProjectNotFoundError(project_id)
            conn.execute(
                "INSERT OR IGNORE INTO decision_activity_runs("
                "id,semantic_identity,project_id,candidate_id,activity_id,"
                "activity_version,payload,created_at) VALUES (?,?,?,?,?,?,?,?)",
                (
                    run_id,
                    semantic_identity,
                    project_id,
                    candidate_id,
                    activity_id,
                    activity_version,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    created_at,
                ),
            )
            row = conn.execute(
                "SELECT * FROM decision_activity_runs WHERE semantic_identity=?",
                (semantic_identity,),
            ).fetchone()
        if row is None:
            raise StoreDataIntegrityError("検討アクティビティを保存できませんでした")
        return self._decision_activity_run(row)

    def get_decision_activity_run_by_identity(
        self, semantic_identity: str
    ) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM decision_activity_runs WHERE semantic_identity=?",
                (semantic_identity,),
            ).fetchone()
        return self._decision_activity_run(row) if row else None

    def get_decision_activity_run(
        self, run_id: str, project_id: str
    ) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM decision_activity_runs WHERE id=? AND project_id=?",
                (run_id, project_id),
            ).fetchone()
        return self._decision_activity_run(row) if row else None

    def list_decision_activity_runs(
        self, project_id: str, candidate_id: str | None = None
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM decision_activity_runs WHERE project_id=?"
        parameters: tuple[str, ...] = (project_id,)
        if candidate_id is not None:
            query += " AND candidate_id=?"
            parameters = (project_id, candidate_id)
        query += " ORDER BY created_at DESC"
        with self._connect() as conn:
            rows = conn.execute(query, parameters).fetchall()
        return [self._decision_activity_run(row) for row in rows]

    @staticmethod
    def _ai_review_run(row: sqlite3.Row) -> AiReviewRun:
        return AiReviewRun.model_validate_json(row["payload"])

    def create_ai_review_run(self, run: AiReviewRun) -> AiReviewRun:
        if run.state != "running":
            raise ValueError("new AI review run must start in running state")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            candidate = conn.execute(
                "SELECT revision FROM candidates WHERE id=? AND project_id=?",
                (run.candidate_id, run.project_id),
            ).fetchone()
            if candidate is None:
                raise ProjectNotFoundError(run.project_id)
            if int(candidate["revision"]) != run.provenance.reviewed_candidate_revision:
                raise CandidateRevisionConflictError(
                    self.get_candidate(run.candidate_id, run.project_id)  # type: ignore[arg-type]
                )
            conn.execute(
                "INSERT INTO ai_review_runs("
                "review_run_id,project_id,candidate_id,candidate_revision,state,"
                "payload,started_at,completed_at) VALUES (?,?,?,?,?,?,?,NULL)",
                (
                    run.review_run_id,
                    run.project_id,
                    run.candidate_id,
                    run.provenance.reviewed_candidate_revision,
                    run.state,
                    run.model_dump_json(),
                    run.started_at.isoformat(),
                ),
            )
        return run

    def finalize_ai_review_run(self, run: AiReviewRun) -> AiReviewRun:
        if run.state == "running" or run.completed_at is None:
            raise ValueError("AI review finalization requires a terminal run")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT state,project_id,candidate_id,candidate_revision,payload "
                "FROM ai_review_runs WHERE review_run_id=?",
                (run.review_run_id,),
            ).fetchone()
            if row is None:
                raise StoreDataIntegrityError("AI Review Runが見つかりません")
            if row["state"] != "running":
                raise StoreDataIntegrityError("確定済みAI Review Runは変更できません")
            existing = AiReviewRun.model_validate_json(row["payload"])
            terminal_fields = {
                "state",
                "completed_at",
                "findings",
                "summary",
                "suggested_actions",
                "limitations",
                "failure_reason",
            }
            existing_envelope = existing.model_dump(
                mode="json", exclude=terminal_fields
            )
            submitted_envelope = run.model_dump(
                mode="json", exclude=terminal_fields
            )
            if submitted_envelope != existing_envelope:
                raise StoreDataIntegrityError(
                    "AI Review Runのimmutable envelopeが変わっています"
                )
            if (
                row["project_id"] != run.project_id
                or row["candidate_id"] != run.candidate_id
                or int(row["candidate_revision"])
                != run.provenance.reviewed_candidate_revision
            ):
                raise StoreDataIntegrityError("AI Review Runのidentityが変わっています")
            updated = conn.execute(
                "UPDATE ai_review_runs SET state=?,payload=?,completed_at=? "
                "WHERE review_run_id=? AND state='running'",
                (
                    run.state,
                    run.model_dump_json(),
                    run.completed_at.isoformat(),
                    run.review_run_id,
                ),
            )
            if updated.rowcount != 1:
                raise StoreDataIntegrityError("AI Review Runを確定できませんでした")
        return run

    def get_ai_review_run(
        self, project_id: str, review_run_id: str
    ) -> AiReviewRun | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM ai_review_runs "
                "WHERE project_id=? AND review_run_id=?",
                (project_id, review_run_id),
            ).fetchone()
        return self._ai_review_run(row) if row else None

    def list_ai_review_runs(
        self, project_id: str, candidate_id: str | None = None
    ) -> list[AiReviewRun]:
        query = "SELECT payload FROM ai_review_runs WHERE project_id=?"
        parameters: tuple[str, ...] = (project_id,)
        if candidate_id is not None:
            query += " AND candidate_id=?"
            parameters = (project_id, candidate_id)
        query += " ORDER BY started_at DESC"
        with self._connect() as conn:
            rows = conn.execute(query, parameters).fetchall()
        return [self._ai_review_run(row) for row in rows]

    def append_ai_review_disposition(
        self, disposition: AiReviewDisposition
    ) -> AiReviewDisposition:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            run = conn.execute(
                "SELECT state,project_id FROM ai_review_runs WHERE review_run_id=?",
                (disposition.review_run_id,),
            ).fetchone()
            if run is None or run["project_id"] != disposition.project_id:
                raise StoreDataIntegrityError("AI Review Runが見つかりません")
            if run["state"] == "running":
                raise StoreDataIntegrityError("実行中のAI Reviewへ判断を記録できません")
            conn.execute(
                "INSERT INTO ai_review_dispositions("
                "disposition_id,review_run_id,project_id,payload,recorded_at"
                ") VALUES (?,?,?,?,?)",
                (
                    disposition.disposition_id,
                    disposition.review_run_id,
                    disposition.project_id,
                    disposition.model_dump_json(),
                    disposition.recorded_at.isoformat(),
                ),
            )
        return disposition

    def list_ai_review_dispositions(
        self, project_id: str, review_run_id: str
    ) -> list[AiReviewDisposition]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT payload FROM ai_review_dispositions "
                "WHERE project_id=? AND review_run_id=? ORDER BY recorded_at",
                (project_id, review_run_id),
            ).fetchall()
        return [
            AiReviewDisposition.model_validate_json(row["payload"]) for row in rows
        ]

    @staticmethod
    def _actual(row: sqlite3.Row) -> ActualMeasurement:
        return ActualMeasurement(id=row["id"], candidate_id=row["candidate_id"], snapshot_id=row["snapshot_id"], property=row["property"], mean=row["mean"], std=row["std"], replicates=row["replicates"], unit=row["unit"], experiment_no=row["experiment_no"], measured_at=row["measured_at"], note=row["note"], created_at=datetime.fromisoformat(row["created_at"]))

    def list_actuals(self, candidate_id: str) -> list[ActualMeasurement]:
        with self._connect() as conn:
            return [self._actual(row) for row in conn.execute("SELECT * FROM actual_measurements WHERE candidate_id=? ORDER BY created_at", (candidate_id,))]

    def list_project_actuals(self, project_id: str) -> list[ActualMeasurement]:
        """Return the complete, stable incumbent population for one Project."""

        with self._connect() as conn:
            rows = conn.execute(
                "SELECT actual_measurements.* FROM actual_measurements "
                "JOIN candidates ON candidates.id=actual_measurements.candidate_id "
                "WHERE candidates.project_id=? "
                "ORDER BY actual_measurements.id",
                (project_id,),
            ).fetchall()
        return [self._actual(row) for row in rows]

    def create_snapshot_and_actual(
        self,
        project_id: str,
        candidate_id: str,
        expected_revision: int,
        snapshot_payload: dict[str, Any],
        payload: ActualMeasurementInput,
    ) -> ActualMeasurement:
        snapshot_id, actual_id, now = str(uuid.uuid4()), str(uuid.uuid4()), _now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            candidate_row = conn.execute(
                "SELECT * FROM candidates WHERE id=? AND project_id=?",
                (candidate_id, project_id),
            ).fetchone()
            if candidate_row is None:
                if conn.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone() is None:
                    raise ProjectNotFoundError(project_id)
                raise StoreDataIntegrityError("候補が実測の保存前に削除されました")
            current = self._candidate(candidate_row)
            if current.archived_at is not None:
                raise CandidateArchivedError("archive済み候補には実測を追加できません")
            if current.revision != expected_revision:
                raise CandidateRevisionConflictError(current)
            conn.execute(
                "INSERT INTO snapshots VALUES (?, ?, ?, ?)",
                (
                    snapshot_id,
                    candidate_id,
                    json.dumps(snapshot_payload, ensure_ascii=False, sort_keys=True),
                    now,
                ),
            )
            conn.execute(
                "INSERT INTO actual_measurements(id, candidate_id, snapshot_id, property, mean, std, replicates, unit, experiment_no, measured_at, note, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    actual_id,
                    candidate_id,
                    snapshot_id,
                    payload.property,
                    payload.mean,
                    payload.std,
                    payload.replicates,
                    payload.unit,
                    payload.experiment_no,
                    payload.measured_at.isoformat() if payload.measured_at else None,
                    payload.note,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM actual_measurements WHERE id=?",
                (actual_id,),
            ).fetchone()
            if row is None:
                raise StoreDataIntegrityError("作成した実測を再取得できませんでした")
            actual = self._actual(row)
        return actual

    def delete_actual(self, actual_id: str) -> bool:
        with self._connect() as conn:
            return bool(conn.execute("DELETE FROM actual_measurements WHERE id=?", (actual_id,)).rowcount)
