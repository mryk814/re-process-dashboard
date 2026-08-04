from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProjectPersistenceInventory:
    """Single source of truth for Project-owned and retained persistence."""

    direct_tables: tuple[str, ...]
    candidate_tables: tuple[str, ...]
    scope_tables: tuple[str, ...]
    retained_tables: tuple[str, ...]
    control_tables: tuple[str, ...]
    external_reference_scans: tuple[tuple[str, str], ...]

    @property
    def project_owned_tables(self) -> tuple[str, ...]:
        return self.direct_tables + self.candidate_tables + self.scope_tables

    @property
    def all_tables(self) -> frozenset[str]:
        return frozenset(
            self.project_owned_tables + self.retained_tables + self.control_tables
        )


PROJECT_PERSISTENCE = ProjectPersistenceInventory(
    # Purge order: dependent rows precede candidates and the Project root.
    direct_tables=(
        "prediction_graph_goal_search_runs",
        "prediction_graph_objectives",
        "prediction_graph_decision_output_actuals",
        "chain_analysis_variant_records",
        "chain_distribution_runs",
        "chain_snapshot_records",
        "ai_review_dispositions",
        "ai_review_runs",
        "decision_activity_runs",
        "proposal_lab_reports",
        "screening_runs",
        "lineage_node_reviews",
        "project_objective_revisions",
        "candidate_revisions",
        "candidates",
        "projects",
    ),
    candidate_tables=(
        "actual_measurements",
        "snapshots",
    ),
    scope_tables=(
        "chain_execution_claims",
        "chain_execution_state",
    ),
    # These definitions, source records, Dataset records, and maintenance evidence
    # are Workspace-shared. Purging one Project must not remove them.
    retained_tables=(
        "approved_training_snapshots",
        "canonical_dataset_approvals",
        "canonical_series_revisions",
        "chain_definitions",
        "chain_revisions",
        "chain_stage_contract_surfaces",
        "chain_stage_memo",
        "curation_recipes",
        "data_assets",
        "data_lifecycle_payload_findings",
        "data_lifecycle_row_index",
        "data_lifecycle_row_index_manifests",
        "dataset_profile_revisions",
        "dataset_revisions",
        "dataset_view_members",
        "dataset_view_revisions",
        "model_exploration_runs",
        "model_package_refs",
        "prediction_graph_drafts",
        "project_series",
        "raw_series_assets",
        "raw_source_snapshots",
        "schema_migrations",
        "source_connectors",
        "source_curation_runs",
        "source_fetch_attempts",
        "workspace_maintenance_events",
    ),
    control_tables=("project_purge_authorizations",),
    # JSON references are outside SQLite foreign keys and need semantic scans.
    external_reference_scans=(("candidate_revisions", "payload"),),
)


def project_scoped_tables_from_schema(connection: object) -> frozenset[str]:
    """Discover tables whose columns establish a Project ownership route."""

    execute = getattr(connection, "execute")
    discovered: set[str] = set()
    for row in execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ):
        table = str(row[0])
        escaped = table.replace('"', '""')
        columns = {
            str(column[1])
            for column in execute(f'PRAGMA table_info("{escaped}")')
        }
        if table == "projects" or {"project_id", "candidate_id", "scope_id"} & columns:
            discovered.add(table)
    return frozenset(discovered)


def assert_project_persistence_inventory_complete(connection: object) -> None:
    """Reject schema drift until every new table has an explicit lifecycle."""

    execute = getattr(connection, "execute")
    actual = frozenset(
        str(row[0])
        for row in execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    )
    missing = sorted(actual - PROJECT_PERSISTENCE.all_tables)
    stale = sorted(PROJECT_PERSISTENCE.all_tables - actual)
    if missing or stale:
        details = []
        if missing:
            details.append("unregistered tables: " + ", ".join(missing))
        if stale:
            details.append("missing registered tables: " + ", ".join(stale))
        raise AssertionError("; ".join(details))

    scoped = project_scoped_tables_from_schema(connection)
    registered_scoped = frozenset(
        PROJECT_PERSISTENCE.project_owned_tables
        + PROJECT_PERSISTENCE.control_tables
    )
    if scoped != registered_scoped:
        missing = sorted(scoped - registered_scoped)
        stale = sorted(registered_scoped - scoped)
        raise AssertionError(
            "Project-scoped inventory mismatch; "
            f"unregistered={missing}; no ownership route={stale}"
        )
