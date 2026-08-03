from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest
from decision_workbench.application.chain.graph_goal_search import (
    PredictionGraphGoalSearchUseCase,
)
from decision_workbench.contracts.chain_contracts import (
    ChainBinding,
    ExternalBindingSource,
    UnitConversion,
)
from decision_workbench.contracts.task_contracts import NumericRange
from decision_workbench.persistence.chain_catalog_migration import (
    migrate_chain_catalog,
)
from decision_workbench.persistence.prediction_graph_planning_migration import (
    EXPECTED_COLUMNS,
    MIGRATION_CHECKSUM,
    MIGRATION_ID,
    OBJECTIVES_TABLE,
    PredictionGraphPlanningMigrationError,
    migrate_prediction_graph_planning,
)


def test_prediction_graph_planning_migration_is_additive_and_idempotent(
    tmp_path,
) -> None:
    database = tmp_path / "legacy.db"
    migrate_chain_catalog(database)

    migrate_prediction_graph_planning(database)
    migrate_prediction_graph_planning(database)

    with sqlite3.connect(database) as connection:
        for table, expected in EXPECTED_COLUMNS.items():
            columns = {
                str(row[1])
                for row in connection.execute(f'PRAGMA table_info("{table}")')
            }
            assert columns == expected
        marker = connection.execute(
            "SELECT checksum FROM schema_migrations WHERE id=?",
            (MIGRATION_ID,),
        ).fetchone()
    assert marker == (MIGRATION_CHECKSUM,)


def test_prediction_graph_planning_migration_rejects_checksum_drift(
    tmp_path,
) -> None:
    database = tmp_path / "checksum.db"
    migrate_prediction_graph_planning(database)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE schema_migrations SET checksum='changed' WHERE id=?",
            (MIGRATION_ID,),
        )

    with pytest.raises(
        PredictionGraphPlanningMigrationError,
        match="checksum",
    ):
        migrate_prediction_graph_planning(database)


def test_prediction_graph_planning_migration_rejects_marked_table_drift(
    tmp_path,
) -> None:
    database = tmp_path / "column-drift.db"
    migrate_prediction_graph_planning(database)
    with sqlite3.connect(database) as connection:
        connection.execute(f"ALTER TABLE {OBJECTIVES_TABLE} RENAME TO old_objectives")
        connection.execute(
            f"CREATE TABLE {OBJECTIVES_TABLE} (id TEXT PRIMARY KEY, project_id TEXT)"
        )

    with pytest.raises(
        PredictionGraphPlanningMigrationError,
        match="columns are missing",
    ):
        migrate_prediction_graph_planning(database)


def test_prediction_graph_planning_migration_rejects_unmarked_tables(
    tmp_path,
) -> None:
    database = tmp_path / "unmarked.db"
    migrate_chain_catalog(database)
    with sqlite3.connect(database) as connection:
        connection.execute(f"CREATE TABLE {OBJECTIVES_TABLE} (id TEXT PRIMARY KEY)")

    with pytest.raises(
        PredictionGraphPlanningMigrationError,
        match="without their migration marker",
    ):
        migrate_prediction_graph_planning(database)


def test_graph_design_space_inverts_affine_binding_range_into_source_unit() -> None:
    field = SimpleNamespace(
        path="process.temperature_c",
        editable=True,
        default_range=NumericRange(min=2.0, max=6.0),
        choices=(),
        numeric_domain_kind="continuous",
        search_scale="linear",
        step=None,
    )
    binding = ChainBinding(
        target_stage_id="model",
        target_input_path=field.path,
        source=ExternalBindingSource(
            source_kind="external",
            path="graph.temperature_f",
        ),
        conversion=UnitConversion(
            conversion_id="negative-affine-fixture",
            source_unit="°F",
            target_unit="°C",
            factor=-2.0,
            offset=10.0,
        ),
    )
    definition = SimpleNamespace(
        inputs=(
            SimpleNamespace(
                input_id="graph.temperature_f",
                label="Temperature",
                role="design_variable",
                value_source=SimpleNamespace(
                    source_kind="candidate",
                    candidate_path="process.temperature_f",
                ),
                port=SimpleNamespace(value_kind="number", unit="°F"),
            ),
        ),
        stages=(
            SimpleNamespace(
                stage_id="model",
                stage_kind="task",
                contract_id="task",
            ),
        ),
        bindings=(binding,),
        topology=SimpleNamespace(
            affected_nodes_by_input={
                "graph.temperature_f": ("model",),
            },
        ),
        decision_outputs=(
            SimpleNamespace(
                output_id="result",
                source_stage_id="model",
            ),
        ),
    )
    use_case = PredictionGraphGoalSearchUseCase.__new__(
        PredictionGraphGoalSearchUseCase
    )
    use_case.task_registry = SimpleNamespace(
        contract_for=lambda _contract_id: SimpleNamespace(
            task_definition=SimpleNamespace(
                input_groups=(SimpleNamespace(fields=(field,)),),
            )
        )
    )
    use_case._resolved = lambda _project_id: (
        definition,
        SimpleNamespace(revision_digest="sha256:" + "a" * 64),
        SimpleNamespace(graph_revision_id="graph:r1"),
        None,
    )

    design_space = use_case.design_space("project")

    assert len(design_space.variables) == 1
    variable = design_space.variables[0]
    assert variable.unit == "°F"
    assert variable.numeric_range == NumericRange(min=2.0, max=4.0)
