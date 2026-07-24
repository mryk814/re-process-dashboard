from __future__ import annotations

import math
import random
import statistics
from collections.abc import Callable
from typing import Any

from material_workbench.application.candidates import CandidateService
from material_workbench.application.projects import ProjectService
from material_workbench.contracts.decision_activity_contracts import (
    AbsoluteTolerance,
    BoundedUniformTolerance,
    CriticalInput,
    DECISION_ACTIVITY_REGISTRY,
    DecisionActivityAvailability,
    DecisionActivityDefinition,
    DecisionActivityProvenance,
    DecisionActivityRun,
    DecisionActivityRunRequest,
    InputVariationInterval,
    ModelUncertaintyInterval,
    RelativeTolerance,
    ROBUSTNESS_ACTIVITY,
    RobustnessFailureExample,
    RobustnessParameters,
    RobustnessSummary,
    RobustnessTargetSummary,
    ToleranceSpec,
    TruncatedNormalTolerance,
)
from material_workbench.contracts.schemas import Candidate, CandidateInputs, ModelMetadata, Prediction, Project, Support, TargetRange
from material_workbench.contracts.task_contracts import CompositionTotalDefinition
from material_workbench.domain.goal_targets import empirical_goal_probability
from material_workbench.domain.heat_time import line_speed_scaled_times
from material_workbench.execution.inference_work_graph import InferenceKey, InferenceWorkGraph, semantic_digest
from material_workbench.persistence.store import Store
from material_workbench.tasks.project_runtime_resolver import ProjectRuntimeResolver
from material_workbench.tasks.task_registry import TaskRegistry, TaskRegistryError


class DecisionActivityNotFoundError(LookupError):
    pass


class DecisionActivityValidationError(ValueError):
    pass


def _quantile(values: list[float], probability: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("quantile requires values")
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _field_value(candidate: Candidate, path: str) -> float:
    group, key = path.split(".", 1)
    values = getattr(candidate.inputs, group, None)
    if not isinstance(values, dict) or key not in values:
        raise DecisionActivityValidationError(f"候補に公差対象の入力がありません: {path}")
    value = values[key]
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise DecisionActivityValidationError(f"公差解析は数値入力だけを扱います: {path}")
    return float(value)


def _bounds(base: float, spec: ToleranceSpec) -> tuple[float, float]:
    if isinstance(spec, AbsoluteTolerance):
        return base - spec.amount, base + spec.amount
    if isinstance(spec, RelativeTolerance):
        lower, upper = sorted((base * (1.0 - spec.fraction), base * (1.0 + spec.fraction)))
        return lower, upper
    return spec.lower, spec.upper


def _sampler(base: float, spec: ToleranceSpec, rng: random.Random) -> Callable[[], float]:
    lower, upper = _bounds(base, spec)
    if isinstance(spec, TruncatedNormalTolerance):
        def sample_normal() -> float:
            for _ in range(10_000):
                value = rng.gauss(base, spec.standard_deviation)
                if lower <= value <= upper:
                    return value
            raise DecisionActivityValidationError("打切り正規分布から値を生成できません")
        return sample_normal
    return lambda: rng.uniform(lower, upper)


def _with_values(candidate: Candidate, values: dict[str, float]) -> Candidate:
    inputs = candidate.inputs.model_copy(deep=True)
    old_line_speed = inputs.process.get("ls_mpm")
    for path, value in values.items():
        group, key = path.split(".", 1)
        mapping = getattr(inputs, group)
        mapping[key] = value
    new_line_speed = inputs.process.get("ls_mpm")
    if (
        "process.ls_mpm" in values
        and inputs.heat_time_basis == "line_speed"
        and inputs.heat_pattern
        and old_line_speed is not None
        and new_line_speed is not None
    ):
        scaled = line_speed_scaled_times(inputs.heat_pattern, float(old_line_speed), float(new_line_speed))
        inputs.heat_pattern = [
            point.model_copy(update={"time_s": time_s})
            for point, time_s in zip(inputs.heat_pattern, scaled)
        ]
    return candidate.model_copy(update={"inputs": CandidateInputs.model_validate(inputs)})


def _with_declared_balance(
    candidate: Candidate,
    values: dict[str, float],
    composition_totals: tuple[CompositionTotalDefinition, ...],
) -> Candidate:
    adjusted = dict(values)
    for constraint in composition_totals:
        balance_path = constraint.balance_path
        varied_components = set(values) & set(constraint.component_paths)
        if not balance_path or not varied_components or balance_path in values:
            continue
        other_total = sum(
            adjusted.get(path, _field_value(candidate, path))
            for path in constraint.component_paths
            if path != balance_path
        )
        adjusted[balance_path] = constraint.total - other_total
    return _with_values(candidate, adjusted)


def _goal_failed(project: Project, direction: str, target: str, value: float) -> bool:
    goal = project.target_values.get(target)
    if goal is None:
        return False
    if isinstance(goal, TargetRange):
        return not goal.lower <= value <= goal.upper
    if direction == "at_most":
        return value > float(goal)
    return value < float(goal)


def _worst(values: list[float], project: Project, direction: str, target: str, base: float) -> float:
    goal = project.target_values.get(target)
    if isinstance(goal, TargetRange):
        return max(values, key=lambda value: max(goal.lower - value, value - goal.upper, 0.0))
    if goal is not None and direction == "at_most":
        return max(values)
    if goal is not None and direction == "at_least":
        return min(values)
    return max(values, key=lambda value: abs(value - base))


def _correlation(xs: list[float], ys: list[float]) -> tuple[float, str]:
    try:
        correlation = statistics.correlation(xs, ys)
    except statistics.StatisticsError:
        return 0.0, "unclear"
    if not math.isfinite(correlation) or abs(correlation) < 0.05:
        return 0.0, "unclear"
    return abs(correlation), "increases_output" if correlation > 0 else "decreases_output"


class DecisionActivityService:
    def __init__(
        self,
        store: Store,
        registry: TaskRegistry,
        graph: InferenceWorkGraph,
        resolver: ProjectRuntimeResolver,
    ) -> None:
        self.store = store
        self.registry = registry
        self.graph = graph
        self.resolver = resolver
        self.projects = ProjectService(store, registry)
        self.candidates = CandidateService(store, registry, resolver)

    def availability(
        self,
        project_id: str,
        candidate_id: str | None = None,
        expected_revision: int | None = None,
    ) -> list[DecisionActivityAvailability]:
        project = self.projects.require(project_id)
        contract = self.registry.contract_for(project.task_id)
        candidate_available = False
        if candidate_id is not None and expected_revision is not None:
            try:
                self.candidates.at_revision(project_id, candidate_id, expected_revision)
            except (LookupError, ValueError):
                candidate_available = False
            else:
                candidate_available = True
        results = []
        for definition in DECISION_ACTIVITY_REGISTRY:
            reasons: list[str] = []
            for operation in definition.required_operations:
                if not getattr(contract.runtime_capability.operations, operation):
                    reasons.append(f"{operation}に対応する予測runtimeがありません")
            if "candidate" in definition.required_resources and not candidate_available:
                reasons.append("保存済みの候補revisionが必要です")
            results.append(DecisionActivityAvailability(
                definition=definition,
                available=not reasons,
                reasons=tuple(reasons),
            ))
        return results

    def run(
        self,
        project_id: str,
        candidate_id: str,
        activity_id: str,
        payload: DecisionActivityRunRequest,
    ) -> DecisionActivityRun:
        definition = next(
            (item for item in DECISION_ACTIVITY_REGISTRY if item.activity_id == activity_id),
            None,
        )
        if definition is None:
            raise DecisionActivityNotFoundError("検討アクティビティが見つかりません")
        if definition != ROBUSTNESS_ACTIVITY:
            raise DecisionActivityNotFoundError("この検討アクティビティはまだ実行できません")
        project = self.projects.require(project_id)
        candidate = self.candidates.at_revision(project_id, candidate_id, payload.expected_revision)
        availability = next(
            item
            for item in self.availability(project_id, candidate_id, payload.expected_revision)
            if item.definition.activity_id == definition.activity_id
        )
        if not availability.available:
            raise DecisionActivityValidationError(" / ".join(availability.reasons))
        parameters = payload.parameters
        samplers = self._validate_tolerances(project, candidate, parameters)
        runtime = self.resolver.runtime_for(project)
        package = runtime.model_package
        if package is None:
            raise DecisionActivityValidationError("Model Packageが解決されていません")
        canonical = self.registry.validate_candidate(project.task_id, candidate).model_dump(
            mode="json", exclude={"provenance"}
        )
        pipeline_digest = self.registry._pipeline_digest(package)
        parameter_payload = parameters.model_dump(mode="json")
        provenance_identity = {
            "project_id": project.id,
            "task_id": project.task_id,
            "task_contract_digest": project.task_contract_digest,
            "candidate_id": candidate.id,
            "candidate_revision": candidate.revision,
            "canonical_input_digest": semantic_digest(canonical),
            "model_package_digest": project.model_package_manifest_digest,
            "feature_pipeline_digest": pipeline_digest,
            "activity_id": definition.activity_id,
            "activity_version": definition.version,
            "parameters": parameter_payload,
        }
        semantic_identity = semantic_digest(provenance_identity)
        existing = self.store.get_decision_activity_run_by_identity(semantic_identity)
        if existing is not None:
            return DecisionActivityRun.model_validate(existing)
        key = InferenceKey.build(
            task_id=project.task_id,
            runtime_type="+".join(sorted({item.runtime_type for item in package.manifest.predictors})),
            canonical_input=canonical,
            package_digest=f"sha256:{package.manifest_sha256}",
            pipeline_digest=pipeline_digest,
            support_digest=semantic_digest({
                "dataset_view_revision_id": project.dataset_view_revision_id,
                "source_sha256": runtime.data.source_sha256,
                "pipeline_digest": pipeline_digest,
                "policy_id": runtime.support_policy_id,
            }),
            operation=definition.activity_id,
            operation_parameters=parameter_payload,
        )
        computed = self.graph.execute(
            key,
            lambda: self._compute_robustness(project, candidate, parameters, samplers),
        )
        provenance = DecisionActivityProvenance(
            task_id=project.task_id,
            task_contract_digest=project.task_contract_digest,
            candidate_id=candidate.id,
            candidate_revision=candidate.revision,
            canonical_input_digest=semantic_digest(canonical),
            model_package_digest=project.model_package_manifest_digest,
            feature_pipeline_digest=pipeline_digest,
            activity_id=definition.activity_id,
            activity_version=definition.version,
            parameters_digest=semantic_digest(parameter_payload),
            model=computed["model"],
        )
        stored = self.store.create_decision_activity_run(
            semantic_identity=semantic_identity,
            project_id=project.id,
            candidate_id=candidate.id,
            activity_id=definition.activity_id,
            activity_version=definition.version,
            payload={
                "definition": definition.model_dump(mode="json"),
                "parameters": parameter_payload,
                "provenance": provenance.model_dump(mode="json"),
                "result": computed["result"].model_dump(mode="json"),
            },
        )
        return DecisionActivityRun.model_validate(stored)

    def list_runs(self, project_id: str, candidate_id: str | None = None) -> list[DecisionActivityRun]:
        self.projects.require(project_id)
        return [
            DecisionActivityRun.model_validate(item)
            for item in self.store.list_decision_activity_runs(project_id, candidate_id)
        ]

    def get_run(self, project_id: str, run_id: str) -> DecisionActivityRun:
        self.projects.require(project_id)
        run = self.store.get_decision_activity_run(run_id, project_id)
        if run is None:
            raise DecisionActivityNotFoundError("保存済みの検討アクティビティが見つかりません")
        return DecisionActivityRun.model_validate(run)

    def _validate_tolerances(
        self,
        project: Project,
        candidate: Candidate,
        parameters: RobustnessParameters,
    ) -> dict[str, Callable[[], float]]:
        fields = {
            field.path: field
            for group in self.registry.contract_for(project.task_id).task_definition.input_groups
            for field in group.fields
        }
        balance_paths = {
            item.balance_path
            for item in self.registry.contract_for(project.task_id).task_definition.composition_totals
            if item.balance_path
        }
        rng = random.Random(parameters.seed)
        samplers: dict[str, Callable[[], float]] = {}
        for path, spec in parameters.tolerance_profile.fields.items():
            field = fields.get(path)
            if field is None or field.kind != "number" or not field.editable:
                raise DecisionActivityValidationError(f"公差解析に使えない入力です: {path}")
            if path in balance_paths:
                raise DecisionActivityValidationError(
                    f"{field.label}は組成合計のbalance項目なので直接は変動させられません"
                )
            base = _field_value(candidate, path)
            lower, upper = _bounds(base, spec)
            assert field.allowed_range is not None
            if lower < field.allowed_range.min or upper > field.allowed_range.max:
                raise DecisionActivityValidationError(
                    f"{field.label}の公差範囲がTaskの許容範囲を超えています"
                )
            if isinstance(spec, TruncatedNormalTolerance) and not lower <= base <= upper:
                raise DecisionActivityValidationError(
                    f"{field.label}の打切り範囲に現在値が含まれていません"
                )
            samplers[path] = _sampler(base, spec, rng)
        return samplers

    def _compute_robustness(
        self,
        project: Project,
        candidate: Candidate,
        parameters: RobustnessParameters,
        samplers: dict[str, Callable[[], float]],
    ) -> dict[str, object]:
        runtime = self.resolver.runtime_for(project)
        definition = self.registry.contract_for(project.task_id).task_definition
        outputs = {item.key: item for item in definition.outputs}
        base_result = runtime.predict_core(
            candidate, detailed=False, target_values=project.target_values
        )
        base_predictions = {
            key: Prediction.model_validate(value)
            for key, value in base_result["predictions"].items()
        }
        accepted_inputs: list[dict[str, float]] = []
        accepted_outputs: dict[str, list[float]] = {key: [] for key in outputs}
        supports: list[Support] = []
        failures: list[RobustnessFailureExample] = []
        rejected = 0
        max_attempts = parameters.sample_count * 20
        for attempt in range(max_attempts):
            if len(accepted_inputs) >= parameters.sample_count:
                break
            varied = {path: sample() for path, sample in samplers.items()}
            sample_candidate = _with_declared_balance(
                candidate, varied, definition.composition_totals
            )
            try:
                self.registry.validate_candidate(project.task_id, sample_candidate)
            except (TaskRegistryError, ValueError):
                rejected += 1
                continue
            result = runtime.predict_core(
                sample_candidate, detailed=False, target_values=project.target_values
            )
            point_outputs = {
                key: Prediction.model_validate(value).value
                for key, value in result["predictions"].items()
            }
            support = Support.model_validate(runtime.support_summary(sample_candidate))
            accepted_inputs.append(varied)
            supports.append(support)
            for key, value in point_outputs.items():
                accepted_outputs[key].append(value)
            failed_targets = tuple(
                key for key, value in point_outputs.items()
                if _goal_failed(project, outputs[key].goal_direction, key, value)
            )
            if (failed_targets or support.status == "extrapolated") and len(failures) < 5:
                failures.append(RobustnessFailureExample(
                    sample_index=attempt,
                    varied_inputs=varied,
                    outputs=point_outputs,
                    failed_targets=failed_targets,
                    support=support,
                ))
        if not accepted_inputs:
            raise DecisionActivityValidationError(
                "Task制約を満たす公差サンプルを生成できませんでした"
            )
        target_summaries = []
        for key, output in outputs.items():
            values = accepted_outputs[key]
            base_prediction = base_predictions[key]
            goal = project.target_values.get(key)
            goal_rate = (
                empirical_goal_probability(values, goal, output.goal_direction)
                if goal is not None
                else None
            )
            target_summaries.append(RobustnessTargetSummary(
                target=key,
                unit=output.unit,
                base_prediction=base_prediction,
                input_variation=InputVariationInterval(
                    median=_quantile(values, 0.5),
                    lower=_quantile(values, 0.05),
                    upper=_quantile(values, 0.95),
                ),
                model_uncertainty=ModelUncertaintyInterval(
                    lower=base_prediction.lower,
                    upper=base_prediction.upper,
                ),
                goal_achievement_rate=goal_rate,
                worst_observed=_worst(
                    values, project, output.goal_direction, key, base_prediction.value
                ),
            ))
        critical: list[CriticalInput] = []
        for path in samplers:
            xs = [item[path] for item in accepted_inputs]
            for target, ys in accepted_outputs.items():
                score, direction = _correlation(xs, ys)
                critical.append(CriticalInput(
                    path=path,
                    target=target,
                    absolute_correlation=score,
                    direction=direction,
                ))
        critical.sort(key=lambda item: (-item.absolute_correlation, item.target, item.path))
        warnings = [
            "入力ばらつきによる出力分布とモデルの予測不確実性は別々に表示しています。",
            "入力との相関は局所的な結び付きであり、因果効果ではありません。",
            "Projectへ固定されたDesign Spaceはないため、TaskDefinitionの制約で検証しています。",
        ]
        if rejected:
            warnings.append(
                f"Task制約を満たさない{rejected}サンプルは結果へ含めていません。値はclipしていません。"
            )
        if len(accepted_inputs) < parameters.sample_count:
            warnings.append(
                f"要求{parameters.sample_count}件のうち{len(accepted_inputs)}件だけを評価できました。"
            )
        summary = RobustnessSummary(
            requested_samples=parameters.sample_count,
            accepted_samples=len(accepted_inputs),
            rejected_samples=rejected,
            target_summaries=tuple(target_summaries),
            critical_inputs=tuple(critical[:8]),
            failure_examples=tuple(failures),
            extrapolated_rate=sum(item.status == "extrapolated" for item in supports) / len(supports),
            caution_rate=sum(item.status == "caution" for item in supports) / len(supports),
            warnings=tuple(warnings),
        )
        return {
            "model": ModelMetadata.model_validate(base_result.get("model_meta", {})),
            "result": summary,
        }
