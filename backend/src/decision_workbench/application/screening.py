from __future__ import annotations

import math
from threading import RLock
from typing import Any

from pydantic_core import to_jsonable_python

from .projects import ProjectService
from .objective_execution import build_objective_execution_plan
from decision_workbench.application.proposal_strategy_registry import (
    resolve_strategy,
    strategy_availability,
)
from decision_workbench.application.batch_selector_registry import (
    batch_selector_availability,
    require_batch_selector,
)
from decision_workbench.contracts.candidate_project_contracts import (
    Candidate,
    CandidateInput,
    Project,
)
from decision_workbench.contracts.evidence_contracts import (
    ScreeningCandidateBatchRequest,
    ScreeningCandidateBatchResponse,
)
from decision_workbench.contracts.prediction_catalog_contracts import ScreeningRequest
from decision_workbench.contracts.screening_contracts import ScreeningRunResponse
from decision_workbench.contracts.proposal_contracts import (
    ProposalIncumbentResolution,
    ProposalStrategyAvailability,
)
from decision_workbench.contracts.batch_proposal_contracts import (
    BatchProposalDefinition,
    BatchSelectorAvailability,
)
from decision_workbench.application.proposal_service import run_proposal
from decision_workbench.domain.batch_selector import (
    BatchSelectionError,
    candidate_design_values,
    select_experiment_batch,
)
from decision_workbench.domain.design_space_validation import (
    validate_candidate_in_design_space,
)
from decision_workbench.contracts.design_space_contracts import (
    CategoricalDomain,
    CompositionTotalConstraint,
    DesignSpaceDefinition,
    NumericDomain,
)
from decision_workbench.contracts.task_contracts import NumericRange
from decision_workbench.contracts.objective_contracts import objective_from_screening
from decision_workbench.contracts.objective_contracts import ObjectiveDefinition
from decision_workbench.execution.inference_work_graph import semantic_digest
from decision_workbench.persistence.store import CandidateLimitError, Store
from decision_workbench.tasks.task_registry import TaskRegistry, TaskRegistryError
from decision_workbench.application.project_runtime import ProjectRuntimeResolver
from decision_workbench.modeling.model_lifecycle import runtime_capability_digest
from decision_workbench.design_priors.loader import (
    DesignPriorPackageLoader,
    VerifiedDesignPriorPackage,
)
from decision_workbench.design_priors.sampling import lane_parameter_digest


class ScreeningNotFoundError(LookupError):
    pass


class ScreeningValidationError(ValueError):
    pass


class ScreeningReferencedError(RuntimeError):
    pass


class ScreeningBatchSelectionError(ScreeningValidationError):
    def __init__(self, failure_kind: str, message: str) -> None:
        self.failure_kind = failure_kind
        super().__init__(message)


_SCREENING_PROJECT_LOCKS_GUARD = RLock()
_SCREENING_PROJECT_LOCKS: dict[str, RLock] = {}


def _screening_project_lock(project_id: str) -> RLock:
    with _SCREENING_PROJECT_LOCKS_GUARD:
        return _SCREENING_PROJECT_LOCKS.setdefault(project_id, RLock())


class ScreeningService:
    def __init__(self, store: Store, registry: TaskRegistry, resolver: ProjectRuntimeResolver) -> None:
        self.store = store
        self.registry = registry
        self.resolver = resolver
        self.projects = ProjectService(store, registry)

    def run(self, payload: ScreeningRequest, project_id: str = "default") -> ScreeningRunResponse:
        with _screening_project_lock(project_id):
            return self._run_unlocked(payload, project_id)

    def _run_unlocked(self, payload: ScreeningRequest, project_id: str) -> ScreeningRunResponse:
        project = self.projects.require(project_id)
        contract = self.registry.contract_for(project.task_id)
        runtime = self.resolver.runtime_for(project)
        package = runtime.model_package
        if package is None:
            raise ScreeningValidationError("プロジェクトのModel Packageを解決できません")
        definition = contract.task_definition
        base = self.store.get_candidate(payload.base_candidate_id, project_id)
        if base is None:
            raise ScreeningNotFoundError("基準候補が見つかりません")
        if base.blend_validation.status == "invalid":
            reasons = " / ".join(issue.message for issue in base.blend_validation.issues)
            raise ScreeningValidationError(
                f"配合がDesign Spaceを満たしていないため探索できません: {reasons}"
            )
        try:
            base = Candidate.model_validate({**base.model_dump(), "inputs": payload.base_inputs.model_dump()})
            self.registry.validate_candidate(project.task_id, CandidateInput.model_validate(base.model_dump()))
            validate_candidate_in_design_space(base, project.design_space)
        except (TaskRegistryError, ValueError) as exc:
            raise ScreeningValidationError(str(exc)) from exc
        all_scalar_fields = {
            field.path: field
            for group in definition.input_groups
            for field in group.fields
            if field.kind != "heat_pattern"
        }
        screenable_fields = {
            path: field for path, field in all_scalar_fields.items() if field.editable
        }
        heat_pattern_paths = {
            f"heat_pattern.{index}.{field}"
            for index, _ in enumerate(base.inputs.heat_pattern or [])
            for field in ("time_s", "temperature_c")
        } if any(field.editable and field.kind == "heat_pattern" for group in definition.input_groups for field in group.fields) else set()
        unknown_variables = sorted(set(payload.variables) - set(screenable_fields) - heat_pattern_paths)
        if unknown_variables:
            raise ScreeningValidationError(f"この予測タスクで探索できない変数です: {', '.join(unknown_variables)}")
        for path, spec in payload.variables.items():
            field = screenable_fields.get(path)
            values = [spec.value] if spec.mode == "fixed" else spec.values or []
            if field is not None and field.kind == "categorical":
                if spec.mode == "range" or any(not isinstance(value, str) or value not in field.choices for value in values):
                    raise ScreeningValidationError(f"{field.label}は定義済み選択肢から指定してください")
            elif spec.mode in {"fixed", "list"} and any(
                not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(float(value))
                for value in values
            ):
                raise ScreeningValidationError(f"{field.label if field is not None else 'ヒートパターン'}には有限の数値を指定してください")
            elif spec.mode == "range" and (not math.isfinite(float(spec.min)) or not math.isfinite(float(spec.max))):
                raise ScreeningValidationError(f"{field.label if field is not None else 'ヒートパターン'}には有限の範囲を指定してください")
        scalar_specs = {
            path: spec for path, spec in payload.variables.items()
            if path in screenable_fields
        }
        implicit_fixed: dict[str, float | str] = {}
        for path, field in all_scalar_fields.items():
            if path in scalar_specs:
                continue
            group, key = path.split(".", 1)
            values = getattr(base.inputs, group)
            if key in values:
                implicit_fixed[path] = values[key]
        heat_specs = {
            path: spec for path, spec in payload.variables.items()
            if path in heat_pattern_paths
        }
        design_space = DesignSpaceDefinition(
            schema_version="design-space-definition/v1",
            design_space_id=f"screening-{semantic_digest({
                'task_id': project.task_id,
                'variables': {path: spec.model_dump(mode='json') for path, spec in scalar_specs.items()},
            }).removeprefix('sha256:')[:16]}",
            name="範囲探索の設計空間",
            task_id=project.task_id,
            task_contract_digest=project.task_contract_digest,
            fixed_values=implicit_fixed | {
                path: spec.value for path, spec in scalar_specs.items()
                if spec.mode == "fixed" and spec.value is not None
            },
            fixed_heat_pattern=tuple(
                point.model_dump(mode="json") for point in (base.inputs.heat_pattern or ())
            ) or None,
            numeric_domains=tuple(
                NumericDomain(
                    path=path,
                    mode="range" if spec.mode == "range" else "values",
                    range=NumericRange(min=float(spec.min), max=float(spec.max))
                    if spec.mode == "range"
                    else None,
                    values=tuple(float(value) for value in (spec.values or ()))
                    if spec.mode == "list"
                    else (),
                    numeric_domain_kind=screenable_fields[path].numeric_domain_kind,
                    step=screenable_fields[path].step,
                    step_origin=(
                        screenable_fields[path].allowed_range.min
                        if screenable_fields[path].numeric_domain_kind == "step"
                        and screenable_fields[path].allowed_range is not None
                        else None
                    ),
                    search_scale=screenable_fields[path].search_scale,
                )
                for path, spec in scalar_specs.items()
                if screenable_fields[path].kind == "number" and spec.mode != "fixed"
            ),
            categorical_domains=tuple(
                CategoricalDomain(path=path, choices=tuple(str(value) for value in spec.values or ()))
                for path, spec in scalar_specs.items()
                if screenable_fields[path].kind == "categorical" and spec.mode == "list"
            ),
            heat_pattern_domains=tuple(
                NumericDomain(
                    path=path,
                    mode="range" if spec.mode == "range" else "values",
                    range=NumericRange(min=float(spec.min), max=float(spec.max))
                    if spec.mode == "range"
                    else None,
                    values=(float(spec.value),)
                    if spec.mode == "fixed"
                    else tuple(float(value) for value in (spec.values or ())),
                )
                for path, spec in heat_specs.items()
            ),
            composition_constraints=tuple(
                CompositionTotalConstraint(
                    component_paths=item.component_paths,
                    total=item.total,
                    tolerance=item.tolerance,
                    unit=item.unit,
                    balance_path=item.balance_path,
                )
                for item in definition.composition_totals
            ),
        )
        try:
            design_space.validate_against(definition)
            if project.design_space is not None:
                design_space.validate_narrows(project.design_space)
        except ValueError as exc:
            raise ScreeningValidationError(str(exc)) from exc
        outputs = {item.key: item for item in definition.outputs}
        if payload.purpose == "design_space_map":
            objective = objective_from_screening(
                task=definition,
                task_contract_digest=project.task_contract_digest,
                target=payload.target,
                target_goal=None,
                secondary_goals={},
            )
            objective_provenance = "legacy_screening"
        elif payload.objective_definition is not None:
            objective = payload.objective_definition
            objective_provenance = "explicit"
        elif project.objective_definition is not None:
            objective = project.objective_definition
            objective_provenance = "project_revision"
        else:
            unknown_secondary = sorted(
                (set(payload.secondary_goals) - set(outputs))
                | ({payload.target} & set(payload.secondary_goals))
            )
            if payload.target not in outputs:
                raise ScreeningValidationError("この予測タスクにない目標特性です")
            if unknown_secondary:
                raise ScreeningValidationError(
                    f"副条件の特性を確認してください: {', '.join(unknown_secondary)}"
                )
            objective = objective_from_screening(
                task=definition,
                task_contract_digest=project.task_contract_digest,
                target=payload.target,
                target_goal=payload.target_goal,
                secondary_goals=payload.secondary_goals,
            )
            objective_provenance = "legacy_screening"
        try:
            objective.validate_against(definition, contract.runtime_capability)
            execution = (
                None
                if payload.purpose == "design_space_map"
                else build_objective_execution_plan(objective)
            )
        except ValueError as exc:
            raise ScreeningValidationError(str(exc)) from exc
        if execution is not None:
            payload = payload.model_copy(
                update={
                    "target": execution.target,
                    "target_goal": execution.target_goal,
                    "secondary_goals": execution.secondary_goals,
                }
            )
        if payload.purpose == "goal_search" and payload.target_goal is None:
            raise ScreeningValidationError("有望候補を探すには主目標が必要です")
        output = outputs[payload.target]
        incumbent = objective.incumbent
        if payload.purpose != "experiment_batch":
            if incumbent.source == "candidate_revision":
                if self.store.get_candidate_revision(
                    incumbent.candidate_id or "",
                    incumbent.candidate_revision or 0,
                    project_id,
                ) is None:
                    raise ScreeningValidationError("Objectiveのincumbent候補revisionが見つかりません")
            elif incumbent.source == "prediction_snapshot":
                snapshot = self.store.get_snapshot(incumbent.snapshot_id or "")
                if (
                    snapshot is None
                    or snapshot["candidate_id"] != incumbent.candidate_id
                    or self.store.get_candidate(incumbent.candidate_id or "", project_id) is None
                ):
                    raise ScreeningValidationError("Objectiveのincumbent snapshotが見つかりません")
            elif incumbent.source == "project_decision" and (
                not project.decision_candidate_id or not project.decision_snapshot_id
            ):
                raise ScreeningValidationError("Projectに採用済みincumbentがありません")
        proposal_request = payload.proposal
        strategy = None
        fallback_from = None
        incumbent_resolution = None
        design_prior_package: VerifiedDesignPriorPackage | None = None
        try:
            if payload.purpose != "experiment_batch":
                incumbent_resolution = self._resolve_incumbent(
                    project,
                    objective,
                    payload.target,
                    output.unit,
                    objective_provenance,
                    request_override=payload.proposal.incumbent_value,
                )
                proposal_request = payload.proposal.model_copy(
                    update={
                        "strategy_id": (
                            "latin_hypercube_v1"
                            if payload.purpose == "design_space_map"
                            else payload.proposal.strategy_id
                        ),
                        "incumbent_value": incumbent_resolution.value,
                    }
                )
                payload = payload.model_copy(update={"proposal": proposal_request})
                strategy, fallback_from = resolve_strategy(
                    proposal_request,
                    contract.runtime_capability,
                    target=payload.target,
                    target_kind=next(
                        item.target_kind
                        for item in package.manifest.predictors
                        if item.target == payload.target
                    ),
                    objective=objective,
                    design_space=design_space,
                    capability_matrix=self.registry.capability_matrix_for(project.task_id),
                )
                if (
                    strategy.generator_id != "design_prior"
                    and proposal_request.design_prior is not None
                ):
                    raise ScreeningValidationError(
                        "Design Prior Package参照はDesign Prior戦略でのみ指定できます"
                    )
                if strategy.generator_id == "design_prior":
                    reference = proposal_request.design_prior
                    if reference is None:
                        raise ScreeningValidationError(
                            "Design Prior戦略にはPackage参照を明示してください"
                        )
                    expected_lane_digest = lane_parameter_digest(
                        reference.generator_id,
                        reference.lane,
                    )
                    if (
                        reference.lane_parameter_digest is not None
                        and reference.lane_parameter_digest != expected_lane_digest
                    ):
                        raise ScreeningValidationError(
                            "Design Prior lane parameter digestが実行契約と一致しません"
                        )
                    reference = reference.model_copy(
                        update={"lane_parameter_digest": expected_lane_digest}
                    )
                    proposal_request = proposal_request.model_copy(
                        update={"design_prior": reference}
                    )
                    payload = payload.model_copy(update={"proposal": proposal_request})
                    design_prior_package = DesignPriorPackageLoader().load(reference.locator)
                    manifest = design_prior_package.manifest
                    if (
                        manifest.package_id != reference.package_id
                        or manifest.package_version != reference.package_version
                        or f"sha256:{design_prior_package.manifest_sha256}" != reference.manifest_digest
                    ):
                        raise ScreeningValidationError("Design Prior Package参照のidentityが一致しません")
                    if manifest.task_id != project.task_id or manifest.task_contract_digest != project.task_contract_digest:
                        raise ScreeningValidationError("Design Prior PackageのTask契約がProjectと一致しません")
                    sampled_paths = set(manifest.canonical_input_paths)
                    design_paths = {
                        *design_space.fixed_values,
                        *(item.path for item in design_space.numeric_domains),
                        *(item.path for item in design_space.heat_pattern_domains),
                        *(item.path for item in design_space.categorical_domains),
                    }
                    if not design_paths <= sampled_paths:
                        raise ScreeningValidationError("Design Prior PackageがProject Design Spaceの入力を満たしません")
            if payload.batch_definition is not None:
                require_batch_selector(
                    payload.batch_definition.selector_id,
                    contract.runtime_capability,
                    target=payload.target,
                )
        except ValueError as exc:
            raise ScreeningValidationError(str(exc)) from exc
        configured_goals = dict(payload.secondary_goals)
        if payload.target_goal is not None:
            configured_goals[payload.target] = payload.target_goal
        capabilities = {item.target: item for item in contract.runtime_capability.targets}
        probability_available = {
            key: (
                configured_goals.get(key) is not None
                and capabilities.get(key) is not None
                and capabilities[key].goal_probability != "unavailable"
                and (
                    configured_goals[key].direction == "between"
                    or configured_goals[key].direction == outputs[key].goal_direction
                )
            )
            for key in outputs
        }
        try:
            batch_reference_ids = (
                {
                    *payload.batch_definition.pending_candidate_ids,
                    *(
                        item.candidate_id
                        for item in payload.batch_definition.controls
                    ),
                }
                if payload.batch_definition is not None
                else set()
            )
            batch_reference_candidates = {}
            for candidate_id in batch_reference_ids:
                candidate = self.store.get_candidate(candidate_id, project_id)
                if candidate is None:
                    raise ScreeningValidationError(
                        f"batch参照候補が見つかりません: {candidate_id}"
                    )
                if (
                    payload.batch_definition is not None
                    and any(
                        item.candidate_id == candidate_id
                        for item in payload.batch_definition.controls
                    )
                ):
                    requirement = next(
                        item
                        for item in payload.batch_definition.controls
                        if item.candidate_id == candidate_id
                    )
                    if requirement.candidate_revision != candidate.revision:
                        raise ScreeningValidationError(
                            "exact Control候補のrevisionが選択後に変わりました: "
                            f"{candidate_id} / requested {requirement.candidate_revision}, "
                            f"current {candidate.revision}"
                        )
                    try:
                        self.registry.validate_candidate(
                            project.task_id,
                            CandidateInput.model_validate(candidate.model_dump()),
                        )
                        validate_candidate_in_design_space(
                            candidate,
                            design_space,
                        )
                    except (TaskRegistryError, ValueError) as exc:
                        raise ScreeningValidationError(
                            f"exact Control候補がDesign Spaceを満たしません: {candidate_id} / {exc}"
                        ) from exc
                batch_reference_candidates[candidate_id] = candidate
            if payload.purpose == "experiment_batch":
                source_raw = self.store.get_screening_run(
                    payload.source_run_id or "",
                    project_id,
                )
                if source_raw is None:
                    raise ScreeningValidationError(
                        "元の有望候補Runが見つかりません"
                    )
                source = ScreeningRunResponse.model_validate(source_raw)
                current_design_space_digest = semantic_digest(
                    design_space.model_dump(mode="json")
                )
                source_is_goal_search = (
                    source.purpose == "goal_search"
                    or (
                        source.purpose is None
                        and source.batch_proposal is None
                        and (
                            source.target_goal is not None
                            or source.target_value is not None
                            or bool(source.secondary_goals)
                            or bool(source.secondary_targets)
                            or (
                                source.objective_definition is not None
                                and any(
                                    term.role == "primary_objective"
                                    and term.direction is not None
                                    for term in source.objective_definition.terms
                                )
                            )
                        )
                    )
                )
                if not source_is_goal_search:
                    raise ScreeningValidationError(
                        "実験バッチの元には「有望候補を探す」Runを指定してください"
                    )
                if (
                    source.design_space_digest != current_design_space_digest
                    or source.project_design_space_digest
                    != project.design_space_digest
                    or source.objective_definition_digest != objective.digest
                    or source.base_candidate_id != payload.base_candidate_id
                    or source.variables != payload.variables
                    or source.target != payload.target
                    or source.seed != payload.seed
                    or source.samples != payload.samples
                    or source.proposal_strategy is None
                ):
                    raise ScreeningValidationError(
                        "元の有望候補Runと現在の条件が一致しません。"
                        "有望候補をもう一度実行してください"
                    )
                result = self._select_batch_from_saved_run(
                    source_raw=source_raw,
                    source=source,
                    definition=payload.batch_definition,
                    design_space=design_space,
                    reference_candidates=batch_reference_candidates,
                )
            else:
                assert strategy is not None
                result = run_proposal(
                    runtime,
                    base,
                    payload,
                    probability_available=probability_available,
                    candidate_validator=lambda candidate: (
                        self.registry.validate_candidate(project.task_id, candidate),
                        validate_candidate_in_design_space(candidate, project.design_space),
                    ),
                    design_space=design_space,
                    strategy=strategy,
                    batch_reference_candidates=batch_reference_candidates,
                    design_prior_package=design_prior_package,
                    design_prior_generator_id=(
                        proposal_request.design_prior.generator_id
                        if proposal_request.design_prior is not None
                        else None
                    ),
                    design_prior_lane=(
                        proposal_request.design_prior.lane
                        if proposal_request.design_prior is not None
                        else None
                    ),
                )
        except BatchSelectionError as exc:
            raise ScreeningBatchSelectionError(
                exc.failure_kind,
                str(exc),
            ) from exc
        except ValueError as exc:
            raise ScreeningValidationError(str(exc)) from exc
        result["design_space"] = design_space.model_dump(mode="json")
        result["design_space_digest"] = semantic_digest(result["design_space"])
        result["project_design_space_digest"] = project.design_space_digest
        result["project_design_space_binding_provenance"] = (
            project.design_space_binding_provenance
        )
        result["objective_definition"] = objective.model_dump(mode="json")
        result["objective_definition_digest"] = objective.digest
        result["objective_binding_provenance"] = objective_provenance
        result["objective_execution"] = (
            execution.evidence.model_dump(mode="json")
            if execution is not None
            else None
        )
        result["purpose"] = payload.purpose
        result["source_run_id"] = payload.source_run_id
        result["schema_version"] = "screening-run/v8"
        result["design_prior"] = (
            proposal_request.design_prior.model_dump(mode="json")
            if payload.purpose != "experiment_batch" and proposal_request.design_prior is not None
            else (
                source.design_prior.model_dump(mode="json")
                if payload.purpose == "experiment_batch" and source.design_prior is not None
                else None
            )
        )
        if payload.purpose != "experiment_batch":
            assert strategy is not None
            assert incumbent_resolution is not None
            result["proposal_strategy"] = {
                "id": strategy.strategy_id,
                "version": strategy.version,
                "runtime_capability_digest": runtime_capability_digest(
                    contract.runtime_capability
                ),
                "lifecycle_status": strategy.lifecycle_status,
                "required_capabilities": tuple(
                    item.capability for item in strategy.required_capabilities
                ),
                "seed": result["seed"],
                "requested_count": payload.samples,
                "pool_multiplier": proposal_request.pool_multiplier,
                "generator_id": strategy.generator_id,
                "generator_version": strategy.generator_version,
                "generator_parameters": strategy.generator_parameters,
                "distance_id": strategy.distance_id,
                "distance_version": strategy.distance_version,
                "distance_parameters": strategy.distance_parameters,
                "distance_usage": strategy.distance_usage,
                "acquisition_id": strategy.acquisition_id,
                "acquisition_version": strategy.acquisition_version,
                "selector_id": strategy.selector_id,
                "selector_version": strategy.selector_version,
                "exploration_parameter": (
                    proposal_request.exploration_parameter
                    if strategy.acquisition_id
                    in {"upper_confidence_bound", "expected_improvement"}
                    else None
                ),
                "parameter_role": (
                    "confidence_multiplier"
                    if strategy.acquisition_id == "upper_confidence_bound"
                    else "improvement_margin"
                    if strategy.acquisition_id == "expected_improvement"
                    else None
                ),
                "acquisition_representation": (
                    strategy.requires_acquisition_representation
                ),
                "standard_deviation_methods": sorted(
                    {
                        str(method)
                        for point in result.get("proposal_pool", [])
                        if (
                            method := point.get("acquisition_components", {}).get(
                                "standard_deviation_method"
                            )
                        )
                    }
                ),
                "support_policy": proposal_request.support_policy,
                "fallback_from": fallback_from,
                "fallback_policy": proposal_request.fallback_policy,
                "incumbent_value": proposal_request.incumbent_value,
                "incumbent_resolution": incumbent_resolution.model_dump(mode="json"),
                "constraint_treatment": "feasibility_first_then_rank",
                "uncertainty_treatment": (
                    "predictive_standard_deviation"
                    if strategy.requires_standard_deviation
                    else None
                ),
            }
            result["proposal_diagnostics"] = result.pop("_proposal_diagnostics")
        stored = self.store.create_screening_run(to_jsonable_python(result), project_id)
        return ScreeningRunResponse.model_validate(stored)

    @staticmethod
    def _select_batch_from_saved_run(
        *,
        source_raw: dict[str, Any],
        source: ScreeningRunResponse,
        definition: BatchProposalDefinition,
        design_space: DesignSpaceDefinition,
        reference_candidates: dict[str, Candidate],
    ) -> dict[str, Any]:
        if source.proposal_strategy is None:
            raise ScreeningValidationError("元の有望候補Runに提案方法の証跡がありません")
        points = [
            dict(point)
            for point in source_raw.get("points", [])
        ]
        exact_control_points = []
        pool_size = source.samples * source.proposal_strategy.pool_multiplier
        for control_index, requirement in enumerate(definition.controls):
            candidate = reference_candidates[requirement.candidate_id]
            exact_control_points.append(
                {
                    "pool_index": pool_size + control_index,
                    "inputs": candidate_design_values(candidate, design_space),
                    "candidate": CandidateInput.model_validate(
                        candidate.model_dump()
                    ).model_dump(mode="json"),
                    "score": 0.0,
                    "secondary_goal_evaluations": {},
                    "_batch_source": "exact_control",
                    "_candidate_id": candidate.id,
                    "_candidate_revision": candidate.revision,
                }
            )
        batch_proposal = select_experiment_batch(
            [*points, *exact_control_points],
            definition,
            design_space,
            seed=source.seed,
            reference_candidates=reference_candidates,
            distance_id=source.proposal_strategy.distance_id,
            distance_version=source.proposal_strategy.distance_version,
            distance_parameters=source.proposal_strategy.distance_parameters,
        )
        point_index_by_pool = {
            point["pool_index"]: point["index"]
            for point in points
        }
        batch_proposal["selected"] = [
            {
                "point_index": point_index_by_pool.get(item["point"]["pool_index"]),
                "pool_index": item["point"]["pool_index"],
                "order": order,
                "role": item["role"],
                "reason": item["reason"],
                "acquisition_component": item["acquisition_component"],
                "diversity_component": item["diversity_component"],
                "pending_penalty": item["pending_penalty"],
                "resource_penalty": item["resource_penalty"],
                "combined_score": item["combined_score"],
                "estimated_cost": item["estimated_cost"],
                "setup_group": item["setup_group"],
                "source": item["source"],
                "candidate_id": item["candidate_id"],
                "candidate_revision": item["candidate_revision"],
                "canonical_identity_digest": item[
                    "canonical_identity_digest"
                ],
            }
            for order, item in enumerate(batch_proposal["selected"], start=1)
        ]
        result = {
            key: value
            for key, value in source_raw.items()
            if key not in {
                "id",
                "project_id",
                "created_at",
                "proposal_selection",
            }
        }
        diagnostics = dict(result.get("proposal_diagnostics") or {})
        diagnostics["displayed_count"] = len(points)
        diagnostics["proposed_count"] = 0
        result["proposal_diagnostics"] = diagnostics
        result["batch_proposal"] = batch_proposal
        return result

    def available_strategies(
        self,
        project_id: str,
        target: str,
    ) -> list[ProposalStrategyAvailability]:
        project = self.projects.require(project_id)
        contract = self.registry.contract_for(project.task_id)
        package = self.resolver.runtime_for(project).model_package
        if package is None:
            raise ScreeningValidationError("プロジェクトのModel Packageを解決できません")
        if target not in {
            output.key for output in contract.task_definition.outputs
        }:
            raise ScreeningValidationError("この予測タスクにない目標特性です")
        incumbent_value = None
        if project.objective_definition is not None:
            try:
                execution = build_objective_execution_plan(project.objective_definition)
                if execution.target == target:
                    incumbent_value = self._resolve_incumbent(
                        project,
                        project.objective_definition,
                        target,
                        next(
                            output.unit
                            for output in contract.task_definition.outputs
                            if output.key == target
                        ),
                        "project_revision",
                    ).value
            except ValueError:
                incumbent_value = None
        return strategy_availability(
            contract.runtime_capability,
            target=target,
            target_kind=next(
                item.target_kind
                for item in package.manifest.predictors
                if item.target == target
            ),
            objective=project.objective_definition,
            incumbent_value=incumbent_value,
            design_space=project.design_space,
            capability_matrix=self.registry.capability_matrix_for(project.task_id),
        )

    def available_batch_selectors(
        self,
        project_id: str,
        target: str,
    ) -> list[BatchSelectorAvailability]:
        project = self.projects.require(project_id)
        contract = self.registry.contract_for(project.task_id)
        if target not in {
            output.key for output in contract.task_definition.outputs
        }:
            raise ScreeningValidationError("この予測タスクにない目標特性です")
        return batch_selector_availability(
            contract.runtime_capability,
            target=target,
        )

    @staticmethod
    def _snapshot_prediction_value(snapshot: dict[str, object] | None, target: str) -> float:
        prediction = (
            snapshot.get("payload", {})  # type: ignore[union-attr]
            .get("prediction", {})
            .get("predictions", {})
            .get(target)
            if snapshot is not None
            else None
        )
        if isinstance(prediction, dict) and isinstance(
            prediction.get("value"), (int, float)
        ):
            return float(prediction["value"])
        raise ValueError("incumbent snapshotに対象outputの予測値がありません")

    def _resolve_incumbent(
        self,
        project: Project,
        objective: ObjectiveDefinition,
        target: str,
        unit: str,
        objective_source: str,
        *,
        request_override: float | None = None,
    ) -> ProposalIncumbentResolution:
        target_term = next(
            (term for term in objective.terms if term.output_key == target),
            None,
        )
        direction = (
            target_term.direction
            if target_term is not None
            and target_term.direction in {"at_least", "at_most", "between"}
            else None
        )
        common = {
            "objective_source": objective_source,
            "target": target,
            "unit": unit,
            "direction": direction,
        }
        if request_override is not None:
            return ProposalIncumbentResolution(
                source="request_override",
                value=request_override,
                **common,
            )
        incumbent = objective.incumbent
        if incumbent.source == "none":
            return ProposalIncumbentResolution(source="none", value=None, **common)
        if incumbent.source == "observed_best":
            if direction is None:
                raise ValueError("observed bestには方向を持つ主目的が必要です")
            population = [
                actual
                for actual in self.store.list_project_actuals(project.id)
                if actual.property == target and actual.unit == unit
            ]
            if not population:
                raise ValueError(
                    f"Project実測にincumbent候補がありません: {target} [{unit}]"
                )
            if direction == "at_least":
                selected = max(population, key=lambda item: (item.mean, item.id))
            elif direction == "at_most":
                selected = min(population, key=lambda item: (item.mean, item.id))
            else:
                assert target_term is not None
                midpoint = (float(target_term.lower) + float(target_term.upper)) / 2
                selected = min(
                    population,
                    key=lambda item: (abs(item.mean - midpoint), item.id),
                )
            filter_payload = {
                "source": "project_actuals",
                "project_id": project.id,
                "target": target,
                "unit": unit,
                "direction": direction,
            }
            population_payload = [
                {
                    "id": actual.id,
                    "candidate_id": actual.candidate_id,
                    "snapshot_id": actual.snapshot_id,
                    "mean": actual.mean,
                    "std": actual.std,
                    "replicates": actual.replicates,
                    "measured_at": (
                        actual.measured_at.isoformat()
                        if actual.measured_at is not None
                        else None
                    ),
                    "created_at": actual.created_at.isoformat(),
                }
                for actual in population
            ]
            return ProposalIncumbentResolution(
                source="observed_project_actuals",
                value=selected.mean,
                candidate_id=selected.candidate_id,
                snapshot_id=selected.snapshot_id,
                actual_id=selected.id,
                filter_digest=semantic_digest(filter_payload),
                population_digest=semantic_digest(population_payload),
                record_count=len(population),
                **common,
            )
        if incumbent.source == "prediction_snapshot":
            snapshot = self.store.get_snapshot(incumbent.snapshot_id or "")
            return ProposalIncumbentResolution(
                source="objective_prediction_snapshot",
                value=self._snapshot_prediction_value(snapshot, target),
                candidate_id=incumbent.candidate_id,
                snapshot_id=incumbent.snapshot_id,
                **common,
            )
        if incumbent.source == "project_decision":
            snapshot = self.store.get_snapshot(project.decision_snapshot_id)
            return ProposalIncumbentResolution(
                source="objective_project_decision",
                value=self._snapshot_prediction_value(snapshot, target),
                candidate_id=project.decision_candidate_id,
                snapshot_id=project.decision_snapshot_id,
                **common,
            )
        candidate_id = (
            incumbent.candidate_id
        )
        revision = incumbent.candidate_revision
        candidate = self.store.get_candidate_revision(
            candidate_id or "",
            revision or 0,
            project.id,
        )
        if candidate is None:
            raise ValueError("incumbent候補が見つかりません")
        prediction = self.resolver.runtime_for(project).predict(
            candidate,
            detailed=False,
        )
        target_prediction = prediction["predictions"].get(target)
        if target_prediction is None:
            raise ValueError("incumbent候補に対象outputの予測値がありません")
        return ProposalIncumbentResolution(
            source="objective_candidate_revision",
            value=float(target_prediction.value),
            candidate_id=candidate_id,
            candidate_revision=revision,
            **common,
        )

    def list(self, project_id: str = "default") -> list[ScreeningRunResponse]:
        self.projects.require(project_id)
        return [ScreeningRunResponse.model_validate(item) for item in self.store.list_screening_runs(project_id)]

    def get(self, run_id: str, project_id: str = "default") -> ScreeningRunResponse:
        run = self.store.get_screening_run(run_id, project_id)
        if run is None:
            raise ScreeningNotFoundError("スクリーニング結果が見つかりません")
        return ScreeningRunResponse.model_validate(run)

    def delete(self, run_id: str, project_id: str = "default") -> None:
        with _screening_project_lock(project_id):
            self._delete_unlocked(run_id, project_id)

    def _delete_unlocked(self, run_id: str, project_id: str) -> None:
        run = self.store.get_screening_run(run_id, project_id)
        if run is None:
            raise ScreeningNotFoundError("スクリーニング結果が見つかりません")
        derived_candidates = [
            candidate
            for candidate in self.store.list_candidates(project_id, include_archived=True)
            if candidate.provenance.source_kind == "screening"
            and candidate.provenance.source_ref.run_id == run_id
        ]
        derived_runs = [
            item
            for item in self.store.list_screening_runs(project_id)
            if item.get("source_run_id") == run_id
        ]
        proposal_lab_reports = [
            report
            for report in self.store.list_proposal_lab_reports(project_id)
            if any(
                item.get("run_id") == run_id
                for item in report.get("runs", [])
            )
        ]
        if derived_candidates or derived_runs or proposal_lab_reports:
            references = []
            if derived_candidates:
                references.append(f"候補 {len(derived_candidates)}件")
            if derived_runs:
                references.append(f"後続の探索 {len(derived_runs)}件")
            if proposal_lab_reports:
                references.append(
                    f"Proposal Lab report {len(proposal_lab_reports)}件"
                )
            raise ScreeningReferencedError(
                f"この探索は{'、'.join(references)}の作成元なので削除できません"
            )
        if not self.store.delete_screening_run(run_id, project_id):
            raise ScreeningNotFoundError("スクリーニング結果が見つかりません")

    def promote(
        self,
        run_id: str,
        payload: ScreeningCandidateBatchRequest,
        project_id: str = "default",
    ) -> ScreeningCandidateBatchResponse:
        with _screening_project_lock(project_id):
            return self._promote_unlocked(run_id, payload, project_id)

    def _promote_unlocked(
        self,
        run_id: str,
        payload: ScreeningCandidateBatchRequest,
        project_id: str,
    ) -> ScreeningCandidateBatchResponse:
        run = self.store.get_screening_run(run_id, project_id)
        if run is None:
            raise ScreeningNotFoundError("スクリーニング結果が見つかりません")
        points = {item["index"]: item for item in run["points"]}
        unique_indices = list(dict.fromkeys(payload.point_indices))
        missing = [index for index in unique_indices if index not in points]
        if missing:
            raise ScreeningNotFoundError(f"スクリーニング点が見つかりません: {', '.join(map(str, missing))}")
        candidate_payloads = [(index, CandidateInput.model_validate({
            **points[index]["candidate"],
            "name": f"Screen {run_id[:6]} #{index + 1}",
            "provenance": {
                "source_kind": "screening",
                "source_ref": {"run_id": run_id, "point_id": str(index), "point_index": index},
            },
        })) for index in unique_indices]
        project = self.projects.require(project_id)
        try:
            for _, candidate_payload in candidate_payloads:
                self.registry.validate_candidate(project.task_id, candidate_payload)
                validate_candidate_in_design_space(candidate_payload, project.design_space)
            created, skipped = self.store.create_screening_candidates(candidate_payloads, run_id, project_id)
        except CandidateLimitError:
            raise
        except (TaskRegistryError, ValueError) as exc:
            raise ScreeningValidationError(str(exc)) from exc
        return ScreeningCandidateBatchResponse(candidates=created, skipped_point_indices=skipped)
