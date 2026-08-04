"""Application service for immutable-context standard model comparison."""

from __future__ import annotations

import json
import platform
import sys
import time
import tracemalloc
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from importlib.util import find_spec
from pathlib import Path
from typing import Any
from uuid import uuid4

from decision_workbench.application.personal_task_packages import (
    promote_personal_package,
)
from decision_workbench.application.training_snapshot_adapter import (
    TrainingSnapshotBuilderInput,
    TrainingSnapshotMaterializationAvailable,
    TrainingSnapshotMaterializationRequest,
    training_snapshot_materializer_registry,
)
from decision_workbench.application.workspace_catalog_bootstrap import (
    register_available_packages,
)
from decision_workbench.contracts.inference_policy_contracts import (
    InferenceDiagnostics,
    InferenceIdentity,
)
from decision_workbench.contracts.model_playground_contracts import (
    ComputeBudgetPreset,
    ModelExplorationAdoptionMemo,
    ModelExplorationAttemptFailure,
    ModelExplorationAttemptResult,
    ModelExplorationContext,
    ModelExplorationFeatureIdentity,
    ModelExplorationEnvironment,
    ModelExplorationOptionalDependency,
    ModelExplorationRecipeAttempt,
    ModelExplorationRecipeSelection,
    ModelExplorationRegistrationReceipt,
    ModelExplorationRun,
    ModelExplorationRunCreateRequest,
    ModelExplorationRunDefinition,
    ModelExplorationTargetContext,
    ModelExplorationTargetReadiness,
    ModelExplorationTargetResult,
    ModelHypothesisIdentity,
    ModelPlaygroundContextPreview,
    semantic_digest,
)
from decision_workbench.data.profile_family_registry import (
    load_training_descriptor,
    restore_profile_document,
)
from decision_workbench.modeling.inference_policy import inference_policy
from decision_workbench.modeling.model_hypothesis_catalog import (
    assess_hypothesis_comparison,
    model_hypothesis_catalog,
)
from decision_workbench.modeling.model_lifecycle import (
    QualityReport,
    canonical_training_dataset,
    task_input_contract_digest,
)
from decision_workbench.modeling.packages.loader import ModelPackageLoader
from decision_workbench.modeling.training.package_assembler import (
    build_standard_model_package,
)
from decision_workbench.modeling.training.feature_dataset import (
    compile_target_training_set,
)
from decision_workbench.modeling.training.readiness import (
    compatible_standard_estimator_ids,
    resolve_estimator_contract_readiness,
    standard_estimator_catalog,
)
from decision_workbench.modeling.training.recipe import estimator_recipe
from decision_workbench.modeling.training.validation_plan import (
    FixedGroupFoldAssignment,
    ValidationPlan,
)
from decision_workbench.persistence.model_playground_repository import (
    ModelExplorationRunConflictError,
    ModelExplorationRunNotFoundError,
)
from decision_workbench.persistence.data_lifecycle_repository import (
    DataLifecycleRepository,
)
from decision_workbench.persistence.store import Store
from decision_workbench.persistence.workspace_catalog import WorkspaceCatalog
from decision_workbench.tasks.task_registry import TaskRegistry


class ModelPlaygroundError(ValueError):
    pass


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _with_execution_digest(values: dict[str, Any]) -> ModelExplorationRun:
    payload = {**values, "execution_payload_digest": "sha256:" + "0" * 64}
    provisional = ModelExplorationRun.model_construct(**payload)
    payload["execution_payload_digest"] = semantic_digest(
        provisional.model_dump(mode="json", exclude={"execution_payload_digest"})
    )
    return ModelExplorationRun.model_validate(payload)


def _replace_run(
    run: ModelExplorationRun,
    **updates: Any,
) -> ModelExplorationRun:
    values = {
        field: getattr(run, field)
        for field in type(run).model_fields
        if field != "execution_payload_digest"
    }
    values.update(updates)
    return _with_execution_digest(values)


def _recipe_options(
    estimator_id: str,
    budget: ComputeBudgetPreset,
    validation_plans: dict[str, ValidationPlan],
    authoring: Any,
) -> dict[str, Any]:
    options = authoring.resolved_options({})
    if estimator_id == "exact-gp-rbf.v1":
        options["restarts"] = {"quick": 1, "standard": 3, "research": 6}[budget]
    elif estimator_id == "bayesian-additive-spline.v1":
        options["max_basis_per_feature"] = {
            "quick": 4,
            "standard": 6,
            "research": 8,
        }[budget]
    elif estimator_id == "lightgbm-regression.v1":
        options["num_boost_round"] = {
            "quick": 80,
            "standard": 200,
            "research": 500,
        }[budget]
    options["validation_plans_by_target"] = validation_plans
    return options


def _card_for_recipe(estimator_id: str) -> Any | None:
    for card in model_hypothesis_catalog().cards:
        identity = card.recipe_identity
        if identity is not None and identity.recipe_id == estimator_id:
            return card
    return None


def _inference_identity(
    estimator_id: str,
) -> tuple[InferenceIdentity | None, str | None]:
    if estimator_id != "bayesian-additive-spline.v1":
        return (
            None,
            "このstandard recipeはposterior inference policyへ対応付けられていません",
        )
    policy = inference_policy("analytic-gaussian")
    return (
        InferenceIdentity.create(
            policy=policy,
            parameterization=(
                "fixed spline basis and smoothing; plugin observation noise; "
                "conditional Gaussian coefficient posterior"
            ),
            diagnostics=InferenceDiagnostics(
                status="not_applicable",
                findings=(
                    "解析的条件付きposteriorのためsampling diagnosticsは対象外",
                ),
            ),
        ),
        None,
    )


def _environment_identity() -> ModelExplorationEnvironment:
    packages = tuple(
        sorted(
            {
                entry.required_dependency
                for entry in standard_estimator_catalog().entries
                if entry.required_dependency is not None
            }
        )
    )
    dependencies: list[ModelExplorationOptionalDependency] = []
    for package in packages:
        available = find_spec(package) is not None
        package_version: str | None = None
        if available:
            try:
                package_version = version(package)
            except PackageNotFoundError:
                package_version = "available-version-unknown"
        dependencies.append(
            ModelExplorationOptionalDependency(
                package=package,
                available=available,
                version=package_version,
            )
        )
    return ModelExplorationEnvironment(
        python_version=sys.version.split()[0],
        platform=platform.platform(),
        optional_dependencies=tuple(dependencies),
    )


class ModelPlaygroundUseCases:
    def __init__(
        self,
        *,
        store: Store,
        workspace_catalog: WorkspaceCatalog,
        task_registry: TaskRegistry,
        model_store_path: Path,
        task_store_path: Path,
        package_origins: dict[str, str],
    ) -> None:
        self.store = store
        self.catalog = workspace_catalog
        self.registry = task_registry
        self.lifecycle_repository = DataLifecycleRepository(store.path)
        self.model_store = model_store_path.resolve()
        self.task_store = task_store_path.resolve()
        self.package_origins = package_origins
        self.run_root = self.model_store / "model-playground" / "runs"

    def preview(
        self,
        *,
        task_id: str,
        profile_revision_id: str,
        training_snapshot_id: str,
        compute_budget: ComputeBudgetPreset = "standard",
    ) -> ModelPlaygroundContextPreview:
        key = semantic_digest(
            {
                "task_id": task_id,
                "profile_revision_id": profile_revision_id,
                "training_snapshot_id": training_snapshot_id,
            }
        ).removeprefix("sha256:")
        builder, profile = self._materialize(
            task_id=task_id,
            profile_revision_id=profile_revision_id,
            training_snapshot_id=training_snapshot_id,
            destination=self.run_root / "_previews" / key / "training.csv",
        )
        context, recipes = self._context_and_recipes(
            builder=builder,
            profile=profile,
            compute_budget=compute_budget,
        )
        return ModelPlaygroundContextPreview(context=context, recipes=recipes)

    def create_run(
        self,
        request: ModelExplorationRunCreateRequest,
    ) -> ModelExplorationRun:
        run_id = str(uuid4())
        root = self.run_root / run_id
        builder, profile = self._materialize(
            task_id=request.task_id,
            profile_revision_id=request.profile_revision_id,
            training_snapshot_id=request.training_snapshot_id,
            destination=root / "training.csv",
        )
        profile_revision = self.catalog.get_profile_revision(
            request.profile_revision_id,
            include_archived=True,
        )
        assert profile_revision is not None
        root.mkdir(parents=True, exist_ok=True)
        (root / "profile.json").write_text(
            json.dumps(
                profile_revision.effective_profile_json,
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
        context, all_recipes = self._context_and_recipes(
            builder=builder,
            profile=profile,
            compute_budget=request.compute_budget,
        )
        by_id = {item.recipe_id: item for item in all_recipes}
        if len(request.selected_recipe_ids) < 2:
            raise ModelPlaygroundError("比較するrecipeを2件以上選択してください")
        try:
            selected = tuple(by_id[item] for item in request.selected_recipe_ids)
        except KeyError as exc:
            raise ModelPlaygroundError(
                f"未定義のstandard recipeです: {exc.args[0]}"
            ) from exc
        if any(
            item.availability not in {"ready", "ready_expensive"}
            for item in selected
        ):
            raise ModelPlaygroundError(
                "unavailableまたはspecialized-only recipeは実行できません"
            )
        cards = tuple(
            card
            for item in selected
            if (card := _card_for_recipe(item.recipe_id)) is not None
        )
        warnings = (
            assess_hypothesis_comparison(cards).warnings
            if cards
            else ("選択したrecipeにreview済みModel Hypothesis Cardがありません",)
        )
        definition_values = {
            "context": context,
            "selected_recipes": selected,
            "compute_budget": request.compute_budget,
            "environment": _environment_identity(),
            "warnings": warnings,
        }
        definition = ModelExplorationRunDefinition(
            **definition_values,
            context_digest=semantic_digest(
                {
                    **ModelExplorationRunDefinition.model_construct(
                        **definition_values,
                        context_digest="sha256:" + "0" * 64,
                    ).model_dump(
                        mode="json",
                        exclude={"context_digest", "warnings"},
                    )
                }
            ),
        )
        now = _utcnow()
        run = _with_execution_digest(
            {
                "run_id": run_id,
                "definition": definition,
                "attempts": (),
                "adoption_memo": None,
                "execution_revision": 1,
                "created_at": now,
                "updated_at": now,
            }
        )
        return self.store.create_model_exploration_run(run)

    def list_runs(self) -> tuple[ModelExplorationRun, ...]:
        return tuple(self._recover_running(run) for run in self.store.list_model_exploration_runs())

    def get_run(self, run_id: str) -> ModelExplorationRun:
        run = self.store.get_model_exploration_run(run_id)
        if run is None:
            raise ModelExplorationRunNotFoundError(run_id)
        return self._recover_running(run)

    def execute_recipe(
        self,
        run_id: str,
        recipe_id: str,
        *,
        expected_revision: int,
    ) -> ModelExplorationRun:
        run = self.get_run(run_id)
        if run.execution_revision != expected_revision:
            raise ModelExplorationRunConflictError(run)
        selection = next(
            (
                item
                for item in run.definition.selected_recipes
                if item.recipe_id == recipe_id
            ),
            None,
        )
        if selection is None:
            raise ModelPlaygroundError("Runで選択していないrecipeです")
        sequence = 1 + sum(
            item.recipe_id == recipe_id for item in run.attempts
        )
        started = _utcnow()
        attempt = ModelExplorationRecipeAttempt(
            attempt_id=str(uuid4()),
            recipe_id=recipe_id,
            sequence=sequence,
            status="running",
            recipe_digest=selection.recipe_digest,
            hypothesis=selection.hypothesis,
            inference_identity=selection.inference_identity,
            started_at=started,
        )
        running = _replace_run(
            run,
            attempts=(*run.attempts, attempt),
            execution_revision=run.execution_revision + 1,
            updated_at=started,
        )
        running = self.store.replace_model_exploration_run(
            running,
            expected_revision=run.execution_revision,
        )
        try:
            result = self._build(running, selection, attempt)
            terminal = attempt.model_copy(
                update={
                    "status": "completed",
                    "finished_at": _utcnow(),
                    "result": result,
                }
            )
        except Exception as exc:
            terminal = attempt.model_copy(
                update={
                    "status": "failed",
                    "finished_at": _utcnow(),
                    "failure": ModelExplorationAttemptFailure(
                        code=type(exc).__name__,
                        message=str(exc) or type(exc).__name__,
                        recovery_hint=(
                            "Runの固定contextは保持されています。原因を確認して"
                            "同じrecipeを再試行してください。"
                        ),
                    ),
                }
            )
        finished = _replace_run(
            running,
            attempts=(*running.attempts[:-1], terminal),
            execution_revision=running.execution_revision + 1,
            updated_at=terminal.finished_at,
        )
        return self.store.replace_model_exploration_run(
            finished,
            expected_revision=running.execution_revision,
        )

    def record_adoption_memo(
        self,
        run_id: str,
        *,
        expected_revision: int,
        decision: str,
        rationale: str,
        adopted_recipe_id: str | None,
    ) -> ModelExplorationRun:
        run = self.get_run(run_id)
        if run.execution_revision != expected_revision:
            raise ModelExplorationRunConflictError(run)
        if adopted_recipe_id is not None and not any(
            item.recipe_id == adopted_recipe_id and item.status == "completed"
            for item in run.attempts
        ):
            raise ModelPlaygroundError("完了していないrecipeは採用候補にできません")
        memo = ModelExplorationAdoptionMemo(
            adopted_recipe_id=adopted_recipe_id,
            decision=decision,
            rationale=rationale,
            recorded_at=_utcnow(),
        )
        updated = _replace_run(
            run,
            adoption_memo=memo,
            execution_revision=run.execution_revision + 1,
            updated_at=memo.recorded_at,
        )
        return self.store.replace_model_exploration_run(
            updated,
            expected_revision=run.execution_revision,
        )

    def register_attempt(
        self,
        run_id: str,
        attempt_id: str,
        *,
        expected_revision: int,
    ) -> ModelExplorationRun:
        run = self.get_run(run_id)
        if run.execution_revision != expected_revision:
            raise ModelExplorationRunConflictError(run)
        index = next(
            (i for i, item in enumerate(run.attempts) if item.attempt_id == attempt_id),
            None,
        )
        if index is None:
            raise ModelPlaygroundError("recipe attemptが見つかりません")
        attempt = run.attempts[index]
        if attempt.status != "completed" or attempt.result is None:
            raise ModelPlaygroundError("完了したattemptだけを登録できます")
        if attempt.registration is not None:
            return run
        root = self.run_root / run.run_id
        promoted = promote_personal_package(
            run.definition.context.task_id,
            Path(attempt.result.package_path),
            root / "training.csv",
            self.model_store,
            profile=root / "profile.json",
            task_store=self.task_store,
            link_task_bundle=False,
        )
        register_available_packages(
            self.catalog,
            self.registry,
            self.model_store / "available-packages.json",
            storage_scope="personal",
            package_origins=self.package_origins,
        )
        ref = next(
            (
                item
                for item in self.catalog.list_model_package_refs(
                    include_archived=True
                )
                if item.package_id == attempt.result.package_id
                and item.manifest_digest
                == attempt.result.manifest_digest.removeprefix("sha256:")
            ),
            None,
        )
        if ref is None:
            raise ModelPlaygroundError(
                "Packageは検証済みですがModel Libraryへ登録できませんでした"
            )
        receipt = ModelExplorationRegistrationReceipt(
            registered_at=_utcnow(),
            reference_id=ref.id,
            manifest_digest=attempt.result.manifest_digest,
        )
        attempts = list(run.attempts)
        attempts[index] = attempt.model_copy(update={"registration": receipt})
        updated = _replace_run(
            run,
            attempts=tuple(attempts),
            execution_revision=run.execution_revision + 1,
            updated_at=receipt.registered_at,
        )
        return self.store.replace_model_exploration_run(
            updated,
            expected_revision=run.execution_revision,
        )

    def _materialize(
        self,
        *,
        task_id: str,
        profile_revision_id: str,
        training_snapshot_id: str,
        destination: Path,
    ) -> tuple[TrainingSnapshotBuilderInput, Any]:
        profile_revision = self.catalog.get_profile_revision(
            profile_revision_id,
            include_archived=True,
        )
        if profile_revision is None:
            raise ModelPlaygroundError("Profile Revisionが見つかりません")
        result = training_snapshot_materializer_registry(
            self.lifecycle_repository,
            self.catalog,
        ).materialize(
            TrainingSnapshotMaterializationRequest(
                task_id=task_id,
                profile_revision_id=profile_revision_id,
                training_snapshot_id=training_snapshot_id,
                destination=destination,
            )
        )
        if not isinstance(result, TrainingSnapshotMaterializationAvailable):
            raise ModelPlaygroundError(result.reason)
        return (
            result.builder_input,
            restore_profile_document(profile_revision.effective_profile_json),
        )

    def _context_and_recipes(
        self,
        *,
        builder: TrainingSnapshotBuilderInput,
        profile: Any,
        compute_budget: ComputeBudgetPreset,
    ) -> tuple[
        ModelExplorationContext,
        tuple[ModelExplorationRecipeSelection, ...],
    ]:
        contract = self.registry.contract_for(builder.task_id)
        module = self.registry.module_for(builder.task_id)
        authoring = module.standard_model_authoring
        if authoring is None:
            raise ModelPlaygroundError(
                "このPrediction Taskにはstandard recipe authoring seamがありません"
            )
        data = load_training_descriptor(builder.path, profile, builder.task_id)
        canonical = canonical_training_dataset(builder.task_id, data, contract)
        feature_pipeline = canonical["feature_pipeline"]
        feature_count = len(feature_pipeline["features"])
        has_categorical_features = any(
            group.key == "categorical" and group.fields
            for group in contract.task_definition.input_groups
        )
        missing_policy_available = "missing_policy" in feature_pipeline
        targets_by_key = {
            item.target_key: item for item in builder.target_cohorts
        }
        outputs = {
            item.key: item for item in contract.task_definition.outputs
        }
        validation_plans: dict[str, ValidationPlan] = {}
        compiled_targets: dict[str, Any] = {}
        target_contexts: list[ModelExplorationTargetContext] = []
        for target_key, cohort in targets_by_key.items():
            plan = ValidationPlan(
                strategy="grouped_kfold",
                folds=builder.split.folds,
                group_key="parent_key",
                fixed_group_assignments=tuple(
                    FixedGroupFoldAssignment(
                        group_key=item.group_key,
                        fold=item.fold,
                    )
                    for item in cohort.split_assignments
                ),
                fixed_assignment_source_digest=cohort.split_digest,
            )
            compiled = compile_target_training_set(
                canonical,
                target=target_key,
                unit=outputs[target_key].unit,
                target_kind=outputs[target_key].target_kind,
                validation_plan=plan,
            )
            compiled_targets[target_key] = compiled
            validation_plans[target_key] = plan
            target_contexts.append(
                ModelExplorationTargetContext(
                    target_key=target_key,
                    row_count=len(cohort.row_keys),
                    training_snapshot_cohort_digest=cohort.cohort_digest,
                    cohort_digest=compiled.cohort_digest,
                    split_digest=cohort.split_digest,
                    fold_digest=compiled.fold_digest,
                    validation_plan=plan,
                    validation_plan_digest=compiled.validation_plan_digest,
                )
            )
        context = ModelExplorationContext(
            task_id=builder.task_id,
            task_contract_digest=task_input_contract_digest(
                contract.task_definition
            ),
            profile_revision_id=builder.profile_revision_id,
            profile_digest=builder.profile_digest,
            training_snapshot_id=builder.provenance.training_snapshot_id,
            training_snapshot_digest=builder.provenance.training_snapshot_digest,
            canonical_dataset_revision_id=(
                builder.provenance.canonical_dataset_revision_id
            ),
            canonical_dataset_digest=builder.provenance.canonical_dataset_digest,
            materialized_training_sha256=builder.source_sha256,
            source_lifecycle=builder.provenance,
            feature_identity=ModelExplorationFeatureIdentity(
                source="canonical_task_pipeline",
                feature_state_digest=semantic_digest(feature_pipeline),
            ),
            targets=tuple(target_contexts),
        )
        compatible = set(
            authoring.allowed_estimator_ids(
                compatible_standard_estimator_ids(
                    contract.task_definition.outputs
                )
            )
        )
        selections: list[ModelExplorationRecipeSelection] = []
        for entry in standard_estimator_catalog().entries:
            reasons: list[str] = []
            statuses: list[str] = []
            target_readiness: list[ModelExplorationTargetReadiness] = []
            if entry.builder_status != "standard_builder":
                effective_parameters = dict(entry.fixed_parameters)
                statuses.append("external_verified_package_only")
                reasons.append(
                    "検証済み外部Package専用でstandard builderは提供されていません"
                )
                target_readiness.extend(
                    ModelExplorationTargetReadiness(
                        target_key=target_key,
                        target_kind=outputs[target_key].target_kind,
                        status="specialized_only",
                        reasons=tuple(reasons),
                        row_count=len(compiled_targets[target_key].y),
                        independent_group_count=len(
                            set(compiled_targets[target_key].validation_groups)
                        ),
                        feature_count=feature_count,
                    )
                    for target_key, _target_context in zip(
                        targets_by_key,
                        target_contexts,
                        strict=True,
                    )
                )
            else:
                options = _recipe_options(
                    entry.estimator_id,
                    compute_budget,
                    validation_plans,
                    authoring,
                )
                recipe = estimator_recipe(entry.estimator_id, options)
                effective_parameters = recipe.model_dump(
                    mode="json",
                    exclude_none=True,
                )
            if (
                entry.builder_status == "standard_builder"
                and entry.estimator_id not in compatible
            ):
                statuses.append("out_of_scope")
                reasons.append(
                    "Prediction Taskのtarget semanticsまたはrecipe policyの対象外です"
                )
                target_readiness.extend(
                    ModelExplorationTargetReadiness(
                        target_key=target_key,
                        target_kind=outputs[target_key].target_kind,
                        status="out_of_scope",
                        reasons=(reasons[-1],),
                        row_count=len(compiled_targets[target_key].y),
                        independent_group_count=len(
                            set(compiled_targets[target_key].validation_groups)
                        ),
                        feature_count=feature_count,
                    )
                    for target_key, _target_context in zip(
                        targets_by_key,
                        target_contexts,
                        strict=True,
                    )
                )
            elif entry.builder_status == "standard_builder":
                for target_key, _target_context in zip(
                    targets_by_key,
                    target_contexts,
                    strict=True,
                ):
                    compiled = compiled_targets[target_key]
                    resolution = resolve_estimator_contract_readiness(
                        estimator_id=entry.estimator_id,
                        output=outputs[target_key],
                        validation_plan=validation_plans[target_key],
                        feature_recipe=None,
                        canonical_feature_count=feature_count,
                        smooth_term_count=feature_count,
                        total_basis_columns=(
                            feature_count
                            * int(
                                getattr(
                                    recipe,
                                    "max_basis_per_feature",
                                    1,
                                )
                            )
                        ),
                        has_categorical_features=has_categorical_features,
                        row_count=len(compiled.y),
                        independent_group_count=len(
                            set(compiled.validation_groups)
                        ),
                        has_missing_features=bool(
                            compiled.imputed_feature_indices
                        ),
                        missing_policy=(
                            "ready"
                            if (
                                not compiled.imputed_feature_indices
                                or missing_policy_available
                            )
                            else "missing"
                        ),
                        observed_target_min=float(compiled.y.min()),
                        observed_targets_are_integers=bool(
                            (compiled.y == compiled.y.astype(int)).all()
                        ),
                    )
                    statuses.append(resolution.status)
                    reasons.extend(resolution.reasons)
                    target_status = resolution.status
                    if (
                        target_status == "out_of_scope"
                        and any(
                            "maximum" in reason
                            or "minimum" in reason
                            or "capacity" in reason
                            for reason in resolution.reasons
                        )
                    ):
                        target_status = "capacity_exceeded"
                    if (
                        target_status == "ready"
                        and entry.training_cost == "high"
                    ):
                        target_status = "ready_expensive"
                    target_readiness.append(
                        ModelExplorationTargetReadiness(
                            target_key=target_key,
                            target_kind=outputs[target_key].target_kind,
                            status=target_status,
                            reasons=resolution.reasons,
                            row_count=len(compiled.y),
                            independent_group_count=len(
                                set(
                                    compiled_targets[
                                        target_key
                                    ].validation_groups
                                )
                            ),
                            feature_count=feature_count,
                        )
                    )
            status = (
                "ready"
                if statuses and all(item == "ready" for item in statuses)
                else (
                    "specialized_only"
                    if "external_verified_package_only" in statuses
                    else statuses[0]
                )
            )
            availability = (
                "ready_expensive"
                if status == "ready" and entry.training_cost == "high"
                else status
            )
            card = _card_for_recipe(entry.estimator_id)
            hypothesis = (
                ModelHypothesisIdentity(
                    card_id=card.id,
                    card_version=card.version,
                    card_digest=semantic_digest(card.model_dump(mode="json")),
                )
                if card is not None
                else None
            )
            inference_identity, inference_reason = _inference_identity(
                entry.estimator_id
            )
            adoption_status = getattr(
                entry,
                "adoption_status",
                "production",
            )
            selections.append(
                ModelExplorationRecipeSelection(
                    recipe_id=entry.estimator_id,
                    recipe_version="1",
                    recipe_digest=semantic_digest(
                        {
                            "recipe_id": entry.estimator_id,
                            "recipe_version": "1",
                            "effective_parameters": effective_parameters,
                        }
                    ),
                    label=entry.label,
                    lifecycle=(
                        adoption_status
                        if status == "ready"
                        else (
                            "specialized"
                            if status == "specialized_only"
                            else "unavailable"
                        )
                    ),
                    availability=availability,
                    reasons=tuple(dict.fromkeys(reasons))
                    or ("readiness resolver returned no reason",),
                    comparison_role=(
                        card.comparison_role
                        if card is not None
                        else "candidate"
                    ),
                    required_dependency=entry.required_dependency,
                    training_cost=entry.training_cost or "moderate",
                    predictive_capabilities=entry.predictive_capabilities,
                    target_readiness=tuple(target_readiness),
                    task_structure=(
                        "task_specific_specialized"
                        if status == "specialized_only"
                        else "standard_independent_targets"
                    ),
                    effective_parameters=effective_parameters,
                    hypothesis=hypothesis,
                    inference_identity=inference_identity,
                    inference_unavailable_reason=inference_reason,
                )
            )
        return context, tuple(selections)

    def _build(
        self,
        run: ModelExplorationRun,
        selection: ModelExplorationRecipeSelection,
        attempt: ModelExplorationRecipeAttempt,
    ) -> ModelExplorationAttemptResult:
        root = self.run_root / run.run_id
        source = root / "training.csv"
        profile_path = root / "profile.json"
        profile = restore_profile_document(
            json.loads(profile_path.read_text(encoding="utf-8"))
        )
        data = load_training_descriptor(
            source,
            profile,
            run.definition.context.task_id,
        )
        contract = self.registry.contract_for(run.definition.context.task_id)
        authoring = self.registry.module_for(
            run.definition.context.task_id
        ).standard_model_authoring
        assert authoring is not None
        recipe = estimator_recipe(
            selection.recipe_id,
            selection.effective_parameters,
        )
        package_id = (
            f"model-playground-{run.run_id}-{selection.recipe_id}"
            f"-attempt-{attempt.sequence}"
        )
        destination = root / "attempts" / attempt.attempt_id / "package"
        tracemalloc.start()
        started = time.perf_counter()
        try:
            build_standard_model_package(
                task_id=run.definition.context.task_id,
                source=source,
                data=data,
                contract=contract,
                candidate_builder=authoring.candidate_builder,
                recipe=recipe,
                destination=destination,
                package_id=package_id,
                package_version="1.0.0",
                replace=False,
                positive_targets=authoring.positive_targets,
                source_lifecycle=run.definition.context.source_lifecycle,
            )
            elapsed = time.perf_counter() - started
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        package = ModelPackageLoader().load(destination)
        if package.manifest.quality_report is None:
            raise ModelPlaygroundError("Model Packageにquality reportがありません")
        quality = QualityReport.model_validate_json(
            package.artifact_path(package.manifest.quality_report).read_text(
                encoding="utf-8"
            )
        )
        evidence = quality.validation_evidence or {}
        predictors = {
            item.target: item for item in package.manifest.predictors
        }
        expected_targets = {
            item.target_key: item for item in run.definition.context.targets
        }
        results: list[ModelExplorationTargetResult] = []
        for metric in quality.targets:
            expected = expected_targets.get(metric.target)
            actual = evidence.get(metric.target)
            if expected is None or actual is None:
                raise ModelPlaygroundError(
                    f"{metric.target}: validation cohort receiptがありません"
                )
            if (
                actual.cohort_digest != expected.cohort_digest
                or actual.fold_digest != expected.fold_digest
                or actual.validation_plan_digest
                != expected.validation_plan_digest
            ):
                raise ModelPlaygroundError(
                    f"{metric.target}: fixed comparison cohortがRun contextと不一致です"
                )
            metrics = metric.model_dump(
                mode="json",
                exclude={"target"},
                exclude_none=True,
            )
            results.append(
                ModelExplorationTargetResult(
                    target_key=metric.target,
                    cohort_digest=actual.cohort_digest,
                    fold_digest=actual.fold_digest,
                    validation_plan_digest=actual.validation_plan_digest,
                    metrics=metrics,
                    inference_identity=(
                        predictors[metric.target].inference_identity
                    ),
                    inference_unavailable_reason=(
                        None
                        if predictors[metric.target].inference_identity
                        is not None
                        else (
                            "このpredictorはposterior inferenceを使用しません"
                        )
                    ),
                )
            )
        artifact_size = sum(
            path.stat().st_size for path in destination.rglob("*") if path.is_file()
        )
        manifest_digest = "sha256:" + package.manifest_sha256
        receipt = {
            "package_id": package.manifest.package_id,
            "manifest_digest": manifest_digest,
            "recipe_digest": selection.recipe_digest,
            "targets": [item.model_dump(mode="json") for item in results],
        }
        return ModelExplorationAttemptResult(
            package_id=package.manifest.package_id,
            package_path=str(destination.resolve()),
            manifest_digest=manifest_digest,
            build_seconds=elapsed,
            peak_memory_bytes=peak,
            artifact_size_bytes=artifact_size,
            capabilities=selection.predictive_capabilities,
            targets=tuple(results),
            build_receipt_digest=semantic_digest(receipt),
        )

    def _recover_running(self, run: ModelExplorationRun) -> ModelExplorationRun:
        running = [item for item in run.attempts if item.status == "running"]
        if not running:
            return run
        now = _utcnow()
        attempts = tuple(
            item.model_copy(
                update={
                    "status": "interrupted",
                    "finished_at": now,
                    "failure": ModelExplorationAttemptFailure(
                        code="process_interrupted",
                        message="前回process終了時に実行中でした",
                        recovery_hint="同じrecipeを再試行できます",
                    ),
                }
            )
            if item.status == "running"
            else item
            for item in run.attempts
        )
        updated = _replace_run(
            run,
            attempts=attempts,
            execution_revision=run.execution_revision + 1,
            updated_at=now,
        )
        try:
            return self.store.replace_model_exploration_run(
                updated,
                expected_revision=run.execution_revision,
            )
        except ModelExplorationRunConflictError as exc:
            return exc.current
