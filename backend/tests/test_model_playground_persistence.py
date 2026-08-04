from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

import pytest

from decision_workbench.application.model_playground import (
    ModelPlaygroundUseCases,
    _replace_run,
    _with_execution_digest,
)
from decision_workbench.contracts.model_playground_contracts import (
    ModelExplorationAttemptResult,
    ModelExplorationContext,
    ModelExplorationEnvironment,
    ModelExplorationFeatureIdentity,
    ModelExplorationRecipeAttempt,
    ModelExplorationRecipeSelection,
    ModelExplorationRunDefinition,
    ModelExplorationTargetContext,
    ModelExplorationTargetReadiness,
    ModelExplorationTargetResult,
    semantic_digest,
)
from decision_workbench.modeling.packages.contracts import (
    SourceLifecycleProvenance,
)
from decision_workbench.modeling.training.validation_plan import (
    FixedGroupFoldAssignment,
    ValidationPlan,
)
from decision_workbench.persistence.model_playground_migration import (
    migrate_model_exploration_runs,
)
from decision_workbench.persistence.model_playground_repository import (
    ModelExplorationRunConflictError,
    ModelExplorationRunMutationError,
)
from decision_workbench.persistence.store import Store


DIGEST = "sha256:" + "1" * 64
NOW = datetime(2026, 8, 4, tzinfo=UTC)


def _run(
    *,
    status: str | None = None,
    execution_instance_id: str | None = None,
):
    plan = ValidationPlan(
        strategy="grouped_kfold",
        folds=2,
        group_key="parent_key",
        fixed_group_assignments=(
            FixedGroupFoldAssignment(group_key="a", fold=0),
            FixedGroupFoldAssignment(group_key="b", fold=1),
        ),
        fixed_assignment_source_digest=DIGEST,
    )
    plan_digest = semantic_digest(plan.model_dump(mode="json"))
    fold_digest = semantic_digest(
        {
            "target": "target",
            "validation_plan_digest": plan_digest,
            "assignments": [
                {"validation_key": "a", "fold": 0},
                {"validation_key": "b", "fold": 1},
            ],
        }
    )
    lifecycle = SourceLifecycleProvenance(
        connector_id="connector",
        connector_configuration_digest=DIGEST,
        source_adapter_id="adapter",
        source_adapter_version="1",
        raw_snapshot_id="raw",
        raw_snapshot_digest=DIGEST,
        recipe_id="recipe",
        recipe_digest=DIGEST,
        curation_run_id="curation",
        curation_digest=DIGEST,
        profile_revision_id="profile-revision",
        profile_digest=DIGEST,
        canonical_dataset_revision_id="dataset-revision",
        canonical_dataset_digest=DIGEST,
        training_snapshot_id="training-snapshot",
        training_snapshot_digest=DIGEST,
        training_selection_policy_digest=DIGEST,
        materialization_adapter_id="materializer",
        materialization_adapter_version="1",
        materialized_training_sha256="2" * 64,
        row_count=4,
    )
    context = ModelExplorationContext(
        task_id="task",
        task_contract_digest=DIGEST,
        profile_revision_id="profile-revision",
        profile_digest=DIGEST,
        training_snapshot_id="training-snapshot",
        training_snapshot_digest=DIGEST,
        canonical_dataset_revision_id="dataset-revision",
        canonical_dataset_digest=DIGEST,
        materialized_training_sha256="2" * 64,
        source_lifecycle=lifecycle,
        feature_identity=ModelExplorationFeatureIdentity(
            source="canonical_task_pipeline",
            feature_state_digest=DIGEST,
        ),
        targets=(
            ModelExplorationTargetContext(
                target_key="target",
                row_count=4,
                training_snapshot_cohort_digest=DIGEST,
                cohort_digest=DIGEST,
                split_digest=DIGEST,
                fold_digest=fold_digest,
                validation_plan=plan,
                validation_plan_digest=plan_digest,
            ),
        ),
    )
    parameters = {
        "estimator_id": "ridge.v1",
        "validation_plans_by_target": {
            "target": plan.model_dump(mode="json")
        },
    }
    selection = ModelExplorationRecipeSelection(
        recipe_id="ridge.v1",
        recipe_version="1",
        recipe_digest=semantic_digest(
            {
                "recipe_id": "ridge.v1",
                "recipe_version": "1",
                "effective_parameters": parameters,
            }
        ),
        label="Ridge",
        lifecycle="production",
        availability="ready",
        reasons=("ready",),
        comparison_role="baseline",
        training_cost="light",
        predictive_capabilities=("point",),
        target_readiness=(
            ModelExplorationTargetReadiness(
                target_key="target",
                target_kind="continuous",
                status="ready",
                reasons=("ready",),
                row_count=4,
                independent_group_count=2,
                feature_count=1,
            ),
        ),
        task_structure="standard_independent_targets",
        effective_parameters=parameters,
        inference_unavailable_reason="not posterior inference",
    )
    definition_values = {
        "context": context,
        "selected_recipes": (selection,),
        "compute_budget": "standard",
        "environment": ModelExplorationEnvironment(
            python_version="3.13",
            platform="test",
            optional_dependencies=(),
        ),
    }
    definition = ModelExplorationRunDefinition(
        **definition_values,
        context_digest=semantic_digest(
            ModelExplorationRunDefinition.model_construct(
                **definition_values,
                context_digest=DIGEST,
            ).model_dump(
                mode="json",
                exclude={"context_digest", "warnings"},
            )
        ),
    )
    attempts = ()
    if status is not None:
        attempt_values = {
            "attempt_id": "attempt-1",
            "recipe_id": "ridge.v1",
            "sequence": 1,
            "status": status,
            "recipe_digest": selection.recipe_digest,
            "execution_instance_id": execution_instance_id,
            "started_at": NOW,
        }
        if status == "completed":
            target = ModelExplorationTargetResult(
                target_key="target",
                cohort_digest=DIGEST,
                fold_digest=fold_digest,
                validation_plan_digest=plan_digest,
                metrics={"mae": 1.0},
                inference_unavailable_reason="not posterior inference",
            )
            attempt_values.update(
                finished_at=NOW,
                result=ModelExplorationAttemptResult(
                    package_id="package",
                    package_path="package",
                    manifest_digest=DIGEST,
                    build_seconds=1,
                    peak_memory_bytes=1,
                    artifact_size_bytes=1,
                    capabilities=("point",),
                    targets=(target,),
                    build_receipt_digest=DIGEST,
                ),
            )
        attempts = (ModelExplorationRecipeAttempt(**attempt_values),)
    return _with_execution_digest(
        {
            "run_id": "run-1",
            "definition": definition,
            "attempts": attempts,
            "execution_revision": 1,
            "created_at": NOW,
            "updated_at": NOW,
        }
    )


def test_migration_is_idempotent_and_ignores_unrelated_old_tables(tmp_path) -> None:
    database = tmp_path / "old.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE legacy_note(value TEXT NOT NULL)")
        connection.execute("INSERT INTO legacy_note VALUES ('keep')")

    migrate_model_exploration_runs(database)
    migrate_model_exploration_runs(database)

    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT value FROM legacy_note").fetchone() == (
            "keep",
        )
        assert connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='model_exploration_runs'"
        ).fetchone() is not None


def test_run_survives_store_restart_and_cas_rejects_stale_writer(tmp_path) -> None:
    database = tmp_path / "workspace.db"
    store = Store(database)
    run = _run()
    store.create_model_exploration_run(run)

    restarted = Store(database)
    assert restarted.get_model_exploration_run(run.run_id) == run
    updated = _replace_run(
        run,
        execution_revision=2,
        updated_at=NOW,
    )
    restarted.replace_model_exploration_run(updated, expected_revision=1)
    with pytest.raises(ModelExplorationRunConflictError):
        store.replace_model_exploration_run(updated, expected_revision=1)


def test_restart_marks_running_attempt_interrupted_without_overwriting_it(
    tmp_path,
) -> None:
    store = Store(tmp_path / "workspace.db")
    running = _run(
        status="running",
        execution_instance_id="previous-process",
    )
    store.create_model_exploration_run(running)
    service = object.__new__(ModelPlaygroundUseCases)
    service.store = Store(store.path)
    service.execution_instance_id = "restarted-process"

    recovered = service.get_run(running.run_id)

    assert recovered.execution_revision == 2
    assert recovered.attempts[0].status == "interrupted"
    assert recovered.attempts[0].failure is not None
    assert service.get_run(running.run_id) == recovered


def test_live_get_keeps_running_attempt_owned_by_same_execution_instance(
    tmp_path,
) -> None:
    store = Store(tmp_path / "workspace.db")
    running = _run(
        status="running",
        execution_instance_id="live-process",
    )
    store.create_model_exploration_run(running)
    service = object.__new__(ModelPlaygroundUseCases)
    service.store = Store(store.path)
    service.execution_instance_id = "live-process"

    observed = service.get_run(running.run_id)

    assert observed == running
    assert observed.attempts[0].status == "running"
    assert observed.execution_revision == 1


def test_completed_attempt_evidence_cannot_be_overwritten(tmp_path) -> None:
    store = Store(tmp_path / "workspace.db")
    completed = _run(status="completed")
    store.create_model_exploration_run(completed)
    assert completed.attempts[0].result is not None
    altered_result = completed.attempts[0].result.model_copy(
        update={"build_seconds": 2.0}
    )
    altered = completed.attempts[0].model_copy(
        update={"result": altered_result}
    )
    invalid = _replace_run(
        completed,
        attempts=(altered,),
        execution_revision=2,
        updated_at=NOW,
    )

    with pytest.raises(ModelExplorationRunMutationError):
        store.replace_model_exploration_run(invalid, expected_revision=1)
