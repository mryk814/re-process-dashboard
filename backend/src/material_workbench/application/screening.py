from __future__ import annotations

import math

from pydantic_core import to_jsonable_python

from .projects import ProjectService
from ..schemas import (
    Candidate,
    CandidateInput,
    ScreeningCandidateBatchRequest,
    ScreeningCandidateBatchResponse,
    ScreeningRequest,
    ScreeningRunResponse,
)
from ..services import run_latin_hypercube
from ..store import CandidateLimitError, Store
from ..task_registry import TaskRegistry, TaskRegistryError


class ScreeningNotFoundError(LookupError):
    pass


class ScreeningValidationError(ValueError):
    pass


class ScreeningService:
    def __init__(self, store: Store, registry: TaskRegistry) -> None:
        self.store = store
        self.registry = registry
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
        screenable_fields = {
            field.path: field
            for group in definition.input_groups
            for field in group.fields
            if field.editable and field.kind != "heat_pattern"
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
                self.registry.runtime_for(project.task_id),
                base,
                payload,
                goal_directions={key: item.goal_direction for key, item in outputs.items()},
                probability_available={key: item.goal_probability != "unavailable" for key, item in capabilities.items()},
                candidate_validator=lambda candidate: self.registry.validate_candidate(project.task_id, candidate),
            )
        except ValueError as exc:
            raise ScreeningValidationError(str(exc)) from exc
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
