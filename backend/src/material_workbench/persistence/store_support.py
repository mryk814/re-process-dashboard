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
