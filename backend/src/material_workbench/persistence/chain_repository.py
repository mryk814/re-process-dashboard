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
from material_workbench.persistence.data_lifecycle_training_audit_migration import (
    migrate_training_snapshot_selection_audit,
)
from material_workbench.domain.candidate_policy import MAX_CANDIDATES_PER_PROJECT
from material_workbench.persistence.store_support import (
    ActiveProjectPurgeError, CandidateArchivedError, CandidateCopyConflictError,
    CandidateLimitError, CandidateRevisionConflictError, ChainCatalogConflictError,
    InvalidProjectDecisionError, PROTECTED_PROJECT_IDS, ProjectGroupConflictError,
    ProjectGroupUnavailableError, ProjectHasDerivedCandidatesError,
    ProjectHasSuccessorsError, ProjectNotFoundError, ProtectedProjectError,
    StoreDataIntegrityError, _now, _single_task_identity_json, _target_values_json,
)


class ChainRepository:
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
