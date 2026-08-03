from __future__ import annotations

from pathlib import Path

from decision_workbench.persistence.ai_review_migration import migrate_ai_reviews
from decision_workbench.persistence.candidate_migration import HOT_PROJECT_ID
from decision_workbench.persistence.candidate_repository import CandidateRepository
from decision_workbench.persistence.candidate_revision_migration import (
    migrate_candidate_revisions,
)
from decision_workbench.persistence.chain_analysis_variant_migration import (
    migrate_chain_analysis_variant,
)
from decision_workbench.persistence.chain_catalog_migration import migrate_chain_catalog
from decision_workbench.persistence.chain_execution_cas_migration import (
    migrate_chain_execution_cas,
)
from decision_workbench.persistence.chain_graph_surface_migration import (
    migrate_chain_graph_surfaces,
)
from decision_workbench.persistence.chain_repository import ChainRepository
from decision_workbench.persistence.chain_uncertainty_migration import (
    migrate_chain_uncertainty,
)
from decision_workbench.persistence.data_lifecycle_migration import (
    migrate_data_lifecycle,
)
from decision_workbench.persistence.data_lifecycle_payload_migration import (
    migrate_data_lifecycle_payloads,
)
from decision_workbench.persistence.data_lifecycle_summary_migration import (
    migrate_data_lifecycle_summaries,
)
from decision_workbench.persistence.data_lifecycle_training_audit_migration import (
    migrate_training_snapshot_selection_audit,
)
from decision_workbench.persistence.decision_activity_migration import (
    migrate_decision_activity_runs,
)
from decision_workbench.persistence.evidence_repository import EvidenceRepository
from decision_workbench.persistence.lineage_review_migration import (
    migrate_lineage_reviews,
)
from decision_workbench.persistence.project_design_space_migration import (
    migrate_project_design_spaces,
)
from decision_workbench.persistence.project_lifecycle_migration import (
    install_project_archive_write_guards,
    migrate_project_lifecycle,
    remove_project_archive_write_guards,
)
from decision_workbench.persistence.project_objective_migration import (
    migrate_project_objectives,
)
from decision_workbench.persistence.project_repository import ProjectRepository
from decision_workbench.persistence.project_starter_migration import (
    migrate_project_starter_identity,
)
from decision_workbench.persistence.prediction_graph_draft_migration import (
    migrate_prediction_graph_drafts,
)
from decision_workbench.persistence.prediction_graph_draft_repository import (
    PredictionGraphDraftRepository,
)
from decision_workbench.persistence.prediction_graph_actual_migration import (
    migrate_prediction_graph_actuals,
)
from decision_workbench.persistence.prediction_graph_planning_migration import (
    migrate_prediction_graph_planning,
)
from decision_workbench.persistence.proposal_lab_migration import (
    migrate_proposal_lab_reports,
)
from decision_workbench.persistence.series_asset_migration import migrate_series_assets
from decision_workbench.persistence.sqlite_connection import (
    initialize_sqlite,
    sqlite_connection,
    validate_sqlite_foreign_keys,
)
from decision_workbench.persistence.store_support import (
    ActiveProjectPurgeError,
    CandidateArchivedError,
    CandidateCopyConflictError,
    CandidateLimitError,
    CandidateRevisionConflictError,
    ChainCatalogConflictError,
    InvalidProjectDecisionError,
    PROTECTED_PROJECT_IDS,
    ProjectGroupConflictError,
    ProjectGroupUnavailableError,
    ProjectHasDerivedCandidatesError,
    ProjectHasSuccessorsError,
    ProjectNotFoundError,
    ProtectedProjectError,
    StoreDataIntegrityError,
)
from decision_workbench.persistence.store_unit_of_work import WorkbenchUnitOfWork
from decision_workbench.persistence.workspace_catalog_migration import (
    migrate_workspace_catalog,
)
from decision_workbench.persistence.workspace_maintenance_migration import (
    migrate_workspace_maintenance_events,
)
from decision_workbench.domain.candidate_policy import MAX_CANDIDATES_PER_PROJECT


class Store(
    WorkbenchUnitOfWork,
    ChainRepository,
    PredictionGraphDraftRepository,
    ProjectRepository,
    CandidateRepository,
    EvidenceRepository,
):
    """Persistence facade and transaction owner for cross-aggregate commands.

    Aggregate-local operations live in the repository mixins. Only
    :class:`WorkbenchUnitOfWork` may own a transaction that spans aggregates.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        initialize_sqlite(self.path)
        remove_project_archive_write_guards(self.path)
        self._init()

    def _connect(self):
        return sqlite_connection(self.path)

    def _init(self) -> None:
        # Migrations remain additive and ordered. Splitting repositories must not
        # change the support floor for existing Workspaces.
        migrate_workspace_catalog(self.path)
        migrate_workspace_maintenance_events(self.path)
        migrate_project_lifecycle(self.path)
        migrate_chain_catalog(self.path)
        migrate_prediction_graph_drafts(self.path)
        migrate_prediction_graph_actuals(self.path)
        migrate_prediction_graph_planning(self.path)
        migrate_chain_graph_surfaces(self.path)
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
        migrate_proposal_lab_reports(self.path)
        migrate_series_assets(self.path)
        migrate_data_lifecycle(self.path)
        migrate_data_lifecycle_payloads(self.path)
        migrate_data_lifecycle_summaries(self.path)
        migrate_training_snapshot_selection_audit(self.path)
        install_project_archive_write_guards(self.path)
        validate_sqlite_foreign_keys(self.path)


__all__ = [
    "ActiveProjectPurgeError",
    "CandidateArchivedError",
    "CandidateCopyConflictError",
    "CandidateLimitError",
    "CandidateRevisionConflictError",
    "ChainCatalogConflictError",
    "HOT_PROJECT_ID",
    "InvalidProjectDecisionError",
    "MAX_CANDIDATES_PER_PROJECT",
    "PROTECTED_PROJECT_IDS",
    "ProjectGroupConflictError",
    "ProjectGroupUnavailableError",
    "ProjectHasDerivedCandidatesError",
    "ProjectHasSuccessorsError",
    "ProjectNotFoundError",
    "ProtectedProjectError",
    "Store",
    "StoreDataIntegrityError",
]
