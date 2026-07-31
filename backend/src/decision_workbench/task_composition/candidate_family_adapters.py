"""Allow-listed operations over persisted candidate families.

Application use cases depend on this surface instead of interpreting the
``canonical-candidate/v1`` composition, process, or heat-pattern layout.
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from types import MappingProxyType
from typing import Protocol

from decision_workbench.contracts.design_space_contracts import (
    DesignSpaceDefinition,
)
from decision_workbench.contracts.candidate_project_contracts import (
    Candidate,
    CandidateInput,
    CandidateInputs,
)
from decision_workbench.contracts.task_contracts import (
    CANONICAL_CANDIDATE_SCHEMA_VERSION,
    TaskDefinition,
)
from decision_workbench.domain.heat_time import line_speed_scaled_times


class CandidateFamilyError(ValueError):
    """The selected family or path cannot be handled safely."""


class CandidateFamilyAdapter(Protocol):
    adapter_id: str

    def value(
        self,
        candidate: CandidateInput,
        path: str,
        *,
        required: bool = True,
    ) -> float | str | None: ...

    def numeric_value(self, candidate: CandidateInput, path: str) -> float: ...

    def time_driver_path(self, definition: TaskDefinition) -> str | None: ...

    def update(
        self,
        candidate: Candidate,
        values: Mapping[str, float | str],
        definition: TaskDefinition,
        *,
        balance: bool = False,
    ) -> Candidate: ...

    def canonicalize_update(
        self,
        existing: Candidate,
        updated: CandidateInput,
        definition: TaskDefinition,
    ) -> None: ...

    def balance_paths(self, definition: TaskDefinition) -> frozenset[str]: ...

    def validate_independent_axes(
        self,
        paths: tuple[str, ...],
        definition: TaskDefinition,
    ) -> None: ...

    def validate_response_axis(
        self,
        variable: str,
        *,
        stage_name: str | None,
        stage_position_m: float | None,
    ) -> None: ...

    def generate_candidates(
        self,
        strategy: str,
        base: Candidate,
        design_space: DesignSpaceDefinition,
        *,
        count: int,
        seed: int,
    ) -> list[tuple[Candidate, dict[str, float | str]]]: ...


class CanonicalCandidateFamilyAdapter:
    adapter_id = CANONICAL_CANDIDATE_SCHEMA_VERSION
    _HEAT_STAGE_AXIS = "heat.stage_temperature_c"

    @staticmethod
    def _heat_path(path: str) -> tuple[int, str] | None:
        parts = path.split(".")
        if (
            len(parts) == 3
            and parts[0] == "heat_pattern"
            and parts[1].isdigit()
            and parts[2] in {"time_s", "temperature_c"}
        ):
            return int(parts[1]), parts[2]
        return None

    def value(
        self,
        candidate: CandidateInput,
        path: str,
        *,
        required: bool = True,
    ) -> float | str | None:
        heat_path = self._heat_path(path)
        if heat_path is not None:
            index, field = heat_path
            points = candidate.inputs.heat_pattern
            if points is not None and index < len(points):
                return float(getattr(points[index], field))
            if required:
                raise CandidateFamilyError(f"候補にヒートパターン点がありません: {path}")
            return None
        parts = path.split(".")
        if len(parts) != 2 or parts[0] not in {
            "composition",
            "process",
            "categorical",
        }:
            raise CandidateFamilyError(f"入力パスを解決できません: {path}")
        values = getattr(candidate.inputs, parts[0], None)
        value = values.get(parts[1]) if isinstance(values, dict) else None
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            if required:
                raise CandidateFamilyError(f"候補に対象の入力がありません: {path}")
            return None
        return value

    def numeric_value(self, candidate: CandidateInput, path: str) -> float:
        value = self.value(candidate, path)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise CandidateFamilyError(f"数値入力ではありません: {path}")
        return float(value)

    @staticmethod
    def time_driver_path(definition: TaskDefinition) -> str | None:
        return next(
            (
                item.path
                for item in definition.response_curve_variables
                if item.time_transform == "inverse_heat_time" and item.path
            ),
            None,
        )

    def _direct_update(
        self,
        candidate: Candidate,
        values: Mapping[str, float | str],
        definition: TaskDefinition,
    ) -> Candidate:
        inputs = candidate.inputs.model_copy(deep=True)
        driver_path = self.time_driver_path(definition)
        previous_driver = (
            self.value(candidate, driver_path, required=False)
            if driver_path is not None
            else None
        )
        for path, value in values.items():
            heat_path = self._heat_path(path)
            if heat_path is not None:
                index, field = heat_path
                if inputs.heat_pattern is None or index >= len(inputs.heat_pattern):
                    raise CandidateFamilyError(
                        f"候補にヒートパターン点がありません: {path}"
                    )
                setattr(inputs.heat_pattern[index], field, float(value))
                continue
            parts = path.split(".")
            if len(parts) != 2 or parts[0] not in {
                "composition",
                "process",
                "categorical",
            }:
                raise CandidateFamilyError(f"入力パスを解決できません: {path}")
            mapping = getattr(inputs, parts[0], None)
            if not isinstance(mapping, dict):
                raise CandidateFamilyError(
                    f"候補に対象の入力グループがありません: {path}"
                )
            mapping[parts[1]] = value
        if driver_path is not None and driver_path in values:
            next_driver = self.value(
                candidate.model_copy(update={"inputs": inputs}),
                driver_path,
                required=False,
            )
            if (
                inputs.heat_time_basis == "line_speed"
                and inputs.heat_pattern
                and isinstance(previous_driver, (int, float))
                and isinstance(next_driver, (int, float))
            ):
                scaled = line_speed_scaled_times(
                    inputs.heat_pattern,
                    float(previous_driver),
                    float(next_driver),
                )
                inputs.heat_pattern = [
                    point.model_copy(update={"time_s": time_s})
                    for point, time_s in zip(
                        inputs.heat_pattern,
                        scaled,
                        strict=True,
                    )
                ]
        return candidate.model_copy(
            update={"inputs": CandidateInputs.model_validate(inputs)}
        )

    def update(
        self,
        candidate: Candidate,
        values: Mapping[str, float | str],
        definition: TaskDefinition,
        *,
        balance: bool = False,
    ) -> Candidate:
        adjusted = dict(values)
        if balance:
            for constraint in definition.composition_totals:
                balance_path = constraint.balance_path
                varied = set(values) & set(constraint.component_paths)
                if not balance_path or not varied or balance_path in values:
                    continue
                other_total = sum(
                    float(adjusted[path])
                    if path in adjusted
                    and isinstance(adjusted[path], (int, float))
                    else self.numeric_value(candidate, path)
                    for path in constraint.component_paths
                    if path != balance_path
                )
                adjusted[balance_path] = constraint.total - other_total
        return self._direct_update(candidate, adjusted, definition)

    def canonicalize_update(
        self,
        existing: Candidate,
        updated: CandidateInput,
        definition: TaskDefinition,
    ) -> None:
        driver_path = self.time_driver_path(definition)
        if driver_path is None:
            return
        current_points = existing.inputs.heat_pattern or []
        requested_points = updated.inputs.heat_pattern or []
        if existing.inputs.heat_time_basis != updated.inputs.heat_time_basis:
            return
        if updated.inputs.heat_time_basis == "elapsed_time":
            return
        old_driver = self.value(existing, driver_path, required=False)
        new_driver = self.value(updated, driver_path, required=False)
        if not isinstance(old_driver, (int, float)) or not isinstance(
            new_driver, (int, float)
        ):
            return
        driver_changed = not math.isclose(
            float(old_driver),
            float(new_driver),
            rel_tol=0.0,
            abs_tol=1e-12,
        )
        if len(current_points) != len(requested_points):
            return
        if not driver_changed:
            if any(
                not math.isclose(
                    current.time_s,
                    requested.time_s,
                    rel_tol=0.0,
                    abs_tol=1e-9,
                )
                for current, requested in zip(
                    current_points,
                    requested_points,
                    strict=True,
                )
            ):
                raise CandidateFamilyError(
                    "ラインスピード基準では時刻だけを変更できません。"
                    "経過時間基準へ切り替えてから変更してください"
                )
            return
        scaled_times = line_speed_scaled_times(
            current_points,
            float(old_driver),
            float(new_driver),
        )
        for requested, time_s in zip(
            requested_points,
            scaled_times,
            strict=True,
        ):
            requested.time_s = time_s

    def balance_paths(self, definition: TaskDefinition) -> frozenset[str]:
        return frozenset(
            item.balance_path
            for item in definition.composition_totals
            if item.balance_path
        )

    def validate_independent_axes(
        self,
        paths: tuple[str, ...],
        definition: TaskDefinition,
    ) -> None:
        for total in definition.composition_totals:
            selected = set(paths) & set(total.component_paths)
            if not selected:
                continue
            if total.balance_path is None:
                raise CandidateFamilyError(
                    "合計を固定したまま独立に動かせない変数は軸にできません"
                )
            if total.balance_path in paths:
                raise CandidateFamilyError("balance項目は軸にできません")

    def validate_response_axis(
        self,
        variable: str,
        *,
        stage_name: str | None,
        stage_position_m: float | None,
    ) -> None:
        is_stage_axis = variable == self._HEAT_STAGE_AXIS
        has_stage_context = stage_name is not None and stage_position_m is not None
        if is_stage_axis != has_stage_context:
            raise CandidateFamilyError(
                "工程温度の応答曲線は工程名と入口からの工程位置をセットで指定してください"
            )
        if stage_name is not None and not stage_name.strip():
            raise CandidateFamilyError("工程名は空白以外の文字を指定してください")

    def generate_candidates(
        self,
        strategy: str,
        base: Candidate,
        design_space: DesignSpaceDefinition,
        *,
        count: int,
        seed: int,
    ) -> list[tuple[Candidate, dict[str, float | str]]]:
        from decision_workbench.domain.proposal_generation import (
            generate_candidates,
        )

        return generate_candidates(
            strategy,
            base,
            design_space,
            count=count,
            seed=seed,
        )


CANONICAL_CANDIDATE_ADAPTER = CanonicalCandidateFamilyAdapter()
_ADAPTERS: Mapping[str, CandidateFamilyAdapter] = MappingProxyType(
    {CANONICAL_CANDIDATE_ADAPTER.adapter_id: CANONICAL_CANDIDATE_ADAPTER}
)


def candidate_family_adapter(adapter_id: str) -> CandidateFamilyAdapter:
    try:
        return _ADAPTERS[adapter_id]
    except KeyError as exc:
        raise CandidateFamilyError(
            f"allow-listにないCandidate familyです: {adapter_id}"
        ) from exc
