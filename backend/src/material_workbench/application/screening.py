from __future__ import annotations

import math

from pydantic_core import to_jsonable_python

from .projects import ProjectService
from material_workbench.application.proposal_strategy_registry import (
    resolve_strategy,
    strategy_availability,
)
from material_workbench.contracts.schemas import (
    Candidate,
    CandidateInput,
    Project,
    ScreeningCandidateBatchRequest,
    ScreeningCandidateBatchResponse,
    ScreeningRequest,
    ScreeningRunResponse,
)
from material_workbench.contracts.proposal_contracts import ProposalStrategyAvailability
from material_workbench.domain.services import run_proposal
from material_workbench.domain.design_space_validation import (
    validate_candidate_in_design_space,
)
from material_workbench.contracts.design_space_contracts import (
    CategoricalDomain,
    CompositionTotalConstraint,
    DesignSpaceDefinition,
    NumericDomain,
)
from material_workbench.contracts.task_contracts import NumericRange
from material_workbench.contracts.objective_contracts import objective_from_screening
from material_workbench.contracts.objective_contracts import ObjectiveDefinition
from material_workbench.execution.inference_work_graph import semantic_digest
from material_workbench.persistence.store import CandidateLimitError, Store
from material_workbench.tasks.task_registry import TaskRegistry, TaskRegistryError
from material_workbench.tasks.project_runtime_resolver import ProjectRuntimeResolver


class ScreeningNotFoundError(LookupError):
    pass


class ScreeningValidationError(ValueError):
    pass


class ScreeningService:
    def __init__(self, store: Store, registry: TaskRegistry, resolver: ProjectRuntimeResolver) -> None:
        self.store = store
        self.registry = registry
        self.resolver = resolver
        self.projects = ProjectService(store, registry)

    def run(self, payload: ScreeningRequest, project_id: str = "default") -> ScreeningRunResponse:
        project = self.projects.require(project_id)
        contract = self.registry.contract_for(project.task_id)
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
        output = next((item for item in definition.outputs if item.key == payload.target), None)
        if output is None:
            raise ScreeningValidationError("この予測タスクにない目標特性です")
        outputs = {item.key: item for item in definition.outputs}
        unknown_secondary = sorted((set(payload.secondary_goals) - set(outputs)) | ({payload.target} & set(payload.secondary_goals)))
        if unknown_secondary:
            raise ScreeningValidationError(f"副条件の特性を確認してください: {', '.join(unknown_secondary)}")
        derived_objective = objective_from_screening(
            task=definition,
            task_contract_digest=project.task_contract_digest,
            target=payload.target,
            target_goal=payload.target_goal,
            secondary_goals=payload.secondary_goals,
        )
        objective = payload.objective_definition or derived_objective
        objective_provenance = (
            "explicit" if payload.objective_definition is not None else "legacy_screening"
        )
        try:
            objective.validate_against(definition, contract.runtime_capability)
        except ValueError as exc:
            raise ScreeningValidationError(str(exc)) from exc
        if payload.objective_definition is not None and (
            objective.optimization_kind != derived_objective.optimization_kind
            or objective.terms != derived_objective.terms
        ):
            raise ScreeningValidationError(
                "Objective Definitionと範囲探索の主目標・副条件が一致しません"
            )
        incumbent = objective.incumbent
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
        try:
            incumbent_value = (
                payload.proposal.incumbent_value
                if payload.proposal.incumbent_value is not None
                else self._resolve_incumbent_value(project, objective, payload.target)
            )
            proposal_request = payload.proposal.model_copy(
                update={"incumbent_value": incumbent_value}
            )
            payload = payload.model_copy(update={"proposal": proposal_request})
            strategy, fallback_from = resolve_strategy(
                proposal_request,
                contract.runtime_capability,
                target=payload.target,
                objective=objective,
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
            result = run_proposal(
                self.resolver.runtime_for(project),
                base,
                payload,
                probability_available=probability_available,
                candidate_validator=lambda candidate: (
                    self.registry.validate_candidate(project.task_id, candidate),
                    validate_candidate_in_design_space(candidate, project.design_space),
                ),
                design_space=design_space,
                strategy=strategy,
            )
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
        result["schema_version"] = "screening-run/v6"
        result["proposal_strategy"] = {
            "id": strategy.strategy_id,
            "version": strategy.version,
            "seed": result["seed"],
            "requested_count": payload.samples,
            "pool_multiplier": proposal_request.pool_multiplier,
            "generator_id": strategy.generator_id,
            "generator_version": strategy.generator_version,
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
            "support_policy": proposal_request.support_policy,
            "fallback_from": fallback_from,
            "fallback_policy": proposal_request.fallback_policy,
            "incumbent_value": proposal_request.incumbent_value,
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

    def available_strategies(
        self,
        project_id: str,
        target: str,
    ) -> list[ProposalStrategyAvailability]:
        project = self.projects.require(project_id)
        contract = self.registry.contract_for(project.task_id)
        if target not in {
            output.key for output in contract.task_definition.outputs
        }:
            raise ScreeningValidationError("この予測タスクにない目標特性です")
        incumbent_value = None
        if project.objective_definition is not None:
            try:
                incumbent_value = self._resolve_incumbent_value(
                    project,
                    project.objective_definition,
                    target,
                )
            except ValueError:
                incumbent_value = None
        return strategy_availability(
            contract.runtime_capability,
            target=target,
            objective=project.objective_definition,
            incumbent_value=incumbent_value,
        )

    def _resolve_incumbent_value(
        self,
        project: Project,
        objective: ObjectiveDefinition,
        target: str,
    ) -> float | None:
        incumbent = objective.incumbent
        if incumbent.source in {"none", "observed_best"}:
            return None
        if incumbent.source == "prediction_snapshot":
            snapshot = self.store.get_snapshot(incumbent.snapshot_id or "")
            prediction = (
                snapshot.get("payload", {})
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
        candidate_id = (
            project.decision_candidate_id
            if incumbent.source == "project_decision"
            else incumbent.candidate_id
        )
        revision = (
            incumbent.candidate_revision
            if incumbent.source == "candidate_revision"
            else None
        )
        candidate = (
            self.store.get_candidate_revision(
                candidate_id or "",
                revision or 0,
                project.id,
            )
            if revision is not None
            else self.store.get_candidate(candidate_id or "", project.id)
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
        return float(target_prediction.value)

    def list(self, project_id: str = "default") -> list[ScreeningRunResponse]:
        self.projects.require(project_id)
        return [ScreeningRunResponse.model_validate(item) for item in self.store.list_screening_runs(project_id)]

    def get(self, run_id: str, project_id: str = "default") -> ScreeningRunResponse:
        run = self.store.get_screening_run(run_id, project_id)
        if run is None:
            raise ScreeningNotFoundError("スクリーニング結果が見つかりません")
        return ScreeningRunResponse.model_validate(run)

    def promote(
        self,
        run_id: str,
        payload: ScreeningCandidateBatchRequest,
        project_id: str = "default",
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
