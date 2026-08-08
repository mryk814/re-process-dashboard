from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from decision_workbench.api.dependencies import get_model_playground_use_cases
from decision_workbench.application.model_playground import (
    ModelPlaygroundError,
    ModelPlaygroundUseCases,
    _replace_run,
    _with_execution_digest,
)
from decision_workbench.contracts.model_playground_contracts import (
    ModelExplorationAttemptResult,
    ModelExplorationCountCandidateEvidence,
    ModelExplorationCountComparisonEvidence,
    ModelExplorationContext,
    ModelExplorationEnvironment,
    ModelExplorationFeatureIdentity,
    ModelExplorationRecipeAttempt,
    ModelExplorationRecipeSelection,
    ModelExplorationRun,
    ModelExplorationRunDefinition,
    ModelExplorationTargetContext,
    ModelExplorationTargetReadiness,
    ModelExplorationTargetResult,
    semantic_digest,
)
from decision_workbench.contracts.task_contracts import CountOutputSemantics
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


def test_legacy_run_payload_without_count_comparison_fields_still_loads() -> None:
    payload = _run(status="completed").model_dump(mode="json")
    payload.pop("count_comparisons")
    for target in payload["definition"]["context"]["targets"]:
        target.pop("exposure_contract_digest")
    for attempt in payload["attempts"]:
        assert attempt["result"] is not None
        for target in attempt["result"]["targets"]:
            target.pop("exposure_contract_digest")
    payload["execution_payload_digest"] = semantic_digest(
        {
            key: value
            for key, value in payload.items()
            if key != "execution_payload_digest"
        }
    )

    restored = ModelExplorationRun.model_validate_json(json.dumps(payload))

    assert restored.count_comparisons == ()


def test_count_comparison_is_persisted_and_returned_by_run_api_without_adoption(
    client,
) -> None:
    base = _run(status="completed")
    assert base.attempts[0].result is not None
    original_target = base.attempts[0].result.targets[0]
    count_semantics = CountOutputSemantics(count_unit="events")
    exposure_digest = semantic_digest(count_semantics.model_dump(mode="json"))

    def count_attempt(
        estimator_id: str,
        attempt_id: str,
        metrics: dict[str, float | int | str | None],
    ) -> ModelExplorationRecipeAttempt:
        target = original_target.model_copy(
            update={
                "exposure_contract_digest": exposure_digest,
                "metrics": metrics,
            }
        )
        result = base.attempts[0].result.model_copy(
            update={
                "package_id": f"package-{attempt_id}",
                "targets": (target,),
            }
        )
        return ModelExplorationRecipeAttempt(
            attempt_id=attempt_id,
            recipe_id=estimator_id,
            sequence=1,
            status="completed",
            recipe_digest=DIGEST,
            started_at=NOW,
            finished_at=NOW,
            result=result,
        )

    attempts = (
        count_attempt(
            "poisson.v1",
            "poisson-attempt",
            {"mae": 2.0, "parent_conditions": 4, "method": "oof"},
        ),
        count_attempt(
            "negative-binomial-regression.v1",
            "nb-attempt",
            {"mae": 1.5, "mean_log_predictive_density": -1.2},
        ),
    )
    service = object.__new__(ModelPlaygroundUseCases)
    service.registry = SimpleNamespace(
        contract_for=lambda _task_id: SimpleNamespace(
            task_definition=SimpleNamespace(
                outputs=(SimpleNamespace(key="target", count=count_semantics),)
            )
        )
    )

    comparisons = service._count_comparison_evidence("task", attempts)

    assert len(comparisons) == 1
    comparison = comparisons[0]
    assert comparison.cohort_digest == original_target.cohort_digest
    assert comparison.fold_digest == original_target.fold_digest
    assert comparison.exposure_contract_digest == exposure_digest
    assert [item.estimator_id for item in comparison.candidates] == [
        "negative-binomial-regression.v1",
        "poisson.v1",
    ]
    assert comparison.candidates[1].metrics == {
        "mae": 2.0,
        "parent_conditions": 4.0,
    }
    assert comparison.automatic_selection is False
    assert comparison.adoption_decision == "experimental"

    run = _replace_run(
        base,
        run_id="count-comparison-run",
        attempts=attempts,
        count_comparisons=comparisons,
    )
    assert run.adoption_memo is None
    client.app.state.store.create_model_exploration_run(run)

    service.store = client.app.state.store
    service.execution_instance_id = "count-comparison-test"
    client.app.dependency_overrides[get_model_playground_use_cases] = lambda: service
    try:
        response = client.get("/api/model-playground/runs/count-comparison-run")
    finally:
        client.app.dependency_overrides.pop(get_model_playground_use_cases, None)

    assert response.status_code == 200
    payload = response.json()
    assert payload["count_comparisons"][0]["exposure_contract_digest"] == (
        exposure_digest
    )
    assert payload["count_comparisons"][0]["automatic_selection"] is False
    assert payload["adoption_memo"] is None
    restarted = Store(client.app.state.store.path)
    restored = restarted.get_model_exploration_run(run.run_id)
    assert restored is not None
    assert restored.count_comparisons == comparisons


def test_count_comparison_refuses_cross_fold_evidence() -> None:
    comparison = ModelExplorationCountComparisonEvidence
    candidate = ModelExplorationCountCandidateEvidence
    with pytest.raises(ValueError, match="share cohort, fold, and exposure"):
        comparison(
            target_key="target",
            cohort_digest=DIGEST,
            fold_digest=DIGEST,
            exposure_contract_digest=DIGEST,
            candidates=(
                candidate(
                    estimator_id="poisson.v1",
                    cohort_digest=DIGEST,
                    fold_digest=DIGEST,
                    exposure_contract_digest=DIGEST,
                    metrics={"mae": 2.0},
                ),
                candidate(
                    estimator_id="negative-binomial-regression.v1",
                    cohort_digest=DIGEST,
                    fold_digest="sha256:" + "2" * 64,
                    exposure_contract_digest=DIGEST,
                    metrics={"mae": 1.5},
                ),
            ),
            adoption_decision="experimental",
        )


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


def test_running_attempt_blocks_same_run_mutations(tmp_path) -> None:
    store = Store(tmp_path / "workspace.db")
    running = _run(
        status="running",
        execution_instance_id="live-process",
    )
    store.create_model_exploration_run(running)
    service = object.__new__(ModelPlaygroundUseCases)
    service.store = Store(store.path)
    service.execution_instance_id = "live-process"
    attempt = running.attempts[0]

    with pytest.raises(ModelPlaygroundError, match="recipe実行中"):
        service.execute_recipe(
            running.run_id,
            attempt.recipe_id,
            expected_revision=running.execution_revision,
        )
    with pytest.raises(ModelPlaygroundError, match="recipe実行中"):
        service.record_adoption_memo(
            running.run_id,
            expected_revision=running.execution_revision,
            decision="continue_research",
            rationale="build完了後に判断する",
            adopted_recipe_id=None,
        )
    with pytest.raises(ModelPlaygroundError, match="recipe実行中"):
        service.register_attempt(
            running.run_id,
            attempt.attempt_id,
            expected_revision=running.execution_revision,
        )

    assert service.get_run(running.run_id) == running


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
