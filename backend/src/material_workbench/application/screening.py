from __future__ import annotations

import math

from pydantic_core import to_jsonable_python

from .projects import ProjectService
from material_workbench.contracts.schemas import (
    Candidate,
    CandidateInput,
    ScreeningCandidateBatchRequest,
    ScreeningCandidateBatchResponse,
    ScreeningRequest,
    ScreeningRunResponse,
)
from material_workbench.domain.services import run_latin_hypercube
from material_workbench.contracts.design_space_contracts import (
    CategoricalDomain,
    DesignSpaceDefinition,
    NumericDomain,
)
from material_workbench.contracts.task_contracts import NumericRange
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
        try:
            base = Candidate.model_validate({**base.model_dump(), "inputs": payload.base_inputs.model_dump()})
            self.registry.validate_candidate(project.task_id, CandidateInput.model_validate(base.model_dump()))
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
        )
        try:
            design_space.validate_against(definition)
        except ValueError as exc:
            raise ScreeningValidationError(str(exc)) from exc
        output = next((item for item in definition.outputs if item.key == payload.target), None)
        if output is None:
            raise ScreeningValidationError("この予測タスクにない目標特性です")
        outputs = {item.key: item for item in definition.outputs}
        unknown_secondary = sorted((set(payload.secondary_targets) - set(outputs)) | ({payload.target} & set(payload.secondary_targets)))
        if unknown_secondary:
            raise ScreeningValidationError(f"副条件の特性を確認してください: {', '.join(unknown_secondary)}")
        capabilities = {item.target: item for item in contract.runtime_capability.targets}
        try:
            result = run_latin_hypercube(
                self.resolver.runtime_for(project),
                base,
                payload,
                goal_directions={key: item.goal_direction for key, item in outputs.items()},
                probability_available={key: item.goal_probability != "unavailable" for key, item in capabilities.items()},
                candidate_validator=lambda candidate: self.registry.validate_candidate(project.task_id, candidate),
                design_space=design_space,
            )
        except ValueError as exc:
            raise ScreeningValidationError(str(exc)) from exc
        result["design_space"] = design_space.model_dump(mode="json")
        result["design_space_digest"] = semantic_digest(result["design_space"])
        result["proposal_strategy"] = {
            "id": "latin_hypercube_v1",
            "version": "1.0.0",
            "seed": result["seed"],
            "requested_count": payload.samples,
        }
        result["rejection_summary"] = result.pop("_rejection_summary", {})
        stored = self.store.create_screening_run(to_jsonable_python(result), project_id)
        return ScreeningRunResponse.model_validate(stored)

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
            created, skipped = self.store.create_screening_candidates(candidate_payloads, run_id, project_id)
        except CandidateLimitError:
            raise
        except (TaskRegistryError, ValueError) as exc:
            raise ScreeningValidationError(str(exc)) from exc
        return ScreeningCandidateBatchResponse(candidates=created, skipped_point_indices=skipped)
