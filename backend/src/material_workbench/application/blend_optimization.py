"""LP/MILP blend optimization inside the Stage A linear boundary."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from typing import Literal

import numpy as np
import scipy
from scipy.optimize import Bounds, LinearConstraint, linprog, milp

from material_workbench.application.candidates import (
    CandidateService,
    CandidateValidationError,
)
from material_workbench.contracts.blend_contracts import (
    BlendItem,
    BlendMaterialDescriptor,
    ResolvedBlendContracts,
    SparseBlend,
)
from material_workbench.contracts.blend_optimization import (
    BlendOptimizationContext,
    BlendOptimizationRequest,
    BlendOptimizationResult,
    RelaxationCandidate,
)
from material_workbench.contracts.candidate_project_contracts import CandidateInput
from material_workbench.modeling.transform_catalog import DeterministicTransformCatalog


_ACTIVATION_RATIO_PERCENT = 1e-4


@dataclass(frozen=True)
class _Row:
    name: str
    coefficients: np.ndarray
    lower: float = -np.inf
    upper: float = np.inf
    unit: str = ""
    display_scale: float = 1.0
    relaxable: bool = True


@dataclass(frozen=True)
class _Problem:
    objective: np.ndarray
    rows: tuple[_Row, ...]
    lower_bounds: np.ndarray
    upper_bounds: np.ndarray
    integrality: np.ndarray
    material_ids: tuple[str, ...]
    z_count: int


def _digest(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


class BlendOptimizationService:
    def __init__(
        self,
        candidates: CandidateService,
        transforms: DeterministicTransformCatalog,
    ) -> None:
        self.candidates = candidates
        self.transforms = transforms

    def context(
        self,
        project_id: str,
        candidate_id: str,
        expected_revision: int | None = None,
    ) -> BlendOptimizationContext:
        baseline = self.candidates.get(project_id, candidate_id)
        if expected_revision is not None and baseline.revision != expected_revision:
            raise CandidateValidationError("基準候補のrevisionが一致しません")
        if baseline.blend is None:
            raise CandidateValidationError("配合を持つ候補だけを逆算できます")
        contracts = self.transforms.resolve_blend(baseline.blend)
        commercial = {
            item.material_id: item for item in contracts.commercial_catalog.materials
        }
        descriptors = tuple(
            BlendMaterialDescriptor(
                material_id=item.material_id,
                name=(
                    commercial[item.material_id].name
                    or getattr(item, "name", item.material_id)
                ),
                material_type=(
                    commercial[item.material_id].material_type
                    or getattr(item, "material_type", item.group)
                ),
                group=commercial[item.material_id].group or item.group,
                d50_um=item.d50_um,
                procurement=commercial[item.material_id].procurement,
                unit_price_yen_per_kg_core=commercial[
                    item.material_id
                ].unit_price_yen_per_kg_core,
                main_components=(
                    commercial[item.material_id].main_components
                    or tuple(
                        component
                        for component, value in sorted(
                            getattr(item, "composition", {}).items(),
                            key=lambda entry: entry[1],
                            reverse=True,
                        )
                        if value > 0
                    )[:3]
                ),
            )
            for item in contracts.scientific_master.materials
            if item.material_id in contracts.design_space.allowed_material_ids
        )
        space = contracts.design_space
        return BlendOptimizationContext(
            baseline_candidate_id=baseline.id,
            baseline_candidate_revision=baseline.revision,
            fixed_hoop_id=space.fixed_hoop_id,
            fixed_fill_ratio=space.fixed_fill_ratio,
            balance_material_id=space.balance_material_id,
            scientific_master=baseline.blend.scientific_master,
            commercial_catalog=baseline.blend.commercial_catalog,
            design_space=baseline.blend.design_space,
            materials=descriptors,
            components=contracts.scientific_master.components,
            material_bounds=space.material_bounds,
            group_totals=space.group_totals,
            group_cardinalities=space.group_cardinalities,
            selection_count=space.selection_count,
        )

    def run(
        self,
        project_id: str,
        candidate_id: str,
        request: BlendOptimizationRequest,
    ) -> BlendOptimizationResult:
        baseline = self.candidates.at_revision(
            project_id, candidate_id, request.expected_revision
        )
        if baseline.blend is None:
            raise CandidateValidationError("配合を持つ候補だけを逆算できます")
        contracts = self.transforms.resolve_blend(baseline.blend)
        structural = self._structural_relaxations(contracts, request)
        method: Literal["highs-lp", "highs-milp"] = (
            "highs-milp" if request.inclusion_decisions else "highs-lp"
        )
        if structural:
            return BlendOptimizationResult(
                status="infeasible",
                solver_name="scipy.optimize",
                solver_version=scipy.__version__,
                method=method,
                objective=request.objective,
                objective_unit=self._objective_unit(request),
                relaxation_candidates=structural,
                message="指定条件の組合せは成立しません。緩和候補を確認してください",
            )

        problem = self._build_problem(contracts, baseline.blend, request)
        solution = self._solve(problem, request.inclusion_decisions)
        if solution is None:
            relaxations = self._relax(problem, request.inclusion_decisions)
            return BlendOptimizationResult(
                status="infeasible",
                solver_name="scipy.optimize",
                solver_version=scipy.__version__,
                method=method,
                objective=request.objective,
                objective_unit=self._objective_unit(request),
                relaxation_candidates=relaxations,
                message=(
                    "指定条件では実行可能な配合がありません。"
                    "表示値は可解性を回復しうる制約緩和候補であり、矛盾の証明ではありません"
                ),
            )

        z = solution[: problem.z_count]
        fill = contracts.design_space.fixed_fill_ratio / 100.0
        ratios = 100.0 * z / fill
        selected = [
            BlendItem(material_id=material_id, ratio=float(ratio))
            for material_id, ratio in zip(problem.material_ids, ratios, strict=True)
            if ratio > contracts.design_space.tolerance
        ]
        total = sum(item.ratio for item in selected)
        balance_id = contracts.design_space.balance_material_id
        selected = [
            item.model_copy(
                update={"ratio": item.ratio + (100.0 - total)}
            )
            if item.material_id == balance_id
            else item
            for item in selected
        ]
        blend = SparseBlend(
            items=tuple(selected),
            hoop_id=contracts.design_space.fixed_hoop_id,
            fill_ratio=contracts.design_space.fixed_fill_ratio,
            balance_material_id=balance_id,
            scientific_master=baseline.blend.scientific_master,
            commercial_catalog=baseline.blend.commercial_catalog,
            design_space=baseline.blend.design_space,
        )
        request_payload = request.model_dump(mode="json")
        provenance = {
            "source_kind": "blend_optimization",
            "source_ref": {
                "project_id": project_id,
                "baseline_candidate_id": baseline.id,
                "baseline_candidate_revision": baseline.revision,
                "solver_name": "scipy.optimize",
                "solver_version": scipy.__version__,
                "method": method,
                "objective": request.objective,
                "design_space_digest": blend.design_space.digest,
                "scientific_master_digest": blend.scientific_master.digest,
                "commercial_catalog_digest": blend.commercial_catalog.digest,
                "request_digest": _digest(request_payload),
            },
        }
        created = self.candidates.create(
            project_id,
            CandidateInput(
                name=request.name,
                inputs=baseline.inputs,
                blend=blend,
                provenance=provenance,
            ),
        )
        objective_value = float(np.dot(problem.objective, solution))
        if request.objective == "baseline_l1":
            fill = contracts.design_space.fixed_fill_ratio / 100.0
            omitted_baseline = sum(
                item.ratio
                for item in baseline.blend.items
                if item.material_id not in problem.material_ids
            )
            objective_value = objective_value * 100.0 / fill + omitted_baseline
        return BlendOptimizationResult(
            status="feasible",
            solver_name="scipy.optimize",
            solver_version=scipy.__version__,
            method=method,
            objective=request.objective,
            objective_value=objective_value,
            objective_unit=self._objective_unit(request),
            candidate=created,
            message="制約を満たす配合を通常候補として保存しました",
        )

    @staticmethod
    def _objective_unit(
        request: BlendOptimizationRequest,
    ) -> Literal["yen/kg-core", "core mass %"]:
        return "yen/kg-core" if request.objective == "cost" else "core mass %"

    @staticmethod
    def _structural_relaxations(
        contracts: ResolvedBlendContracts,
        request: BlendOptimizationRequest,
    ) -> tuple[RelaxationCandidate, ...]:
        space = contracts.design_space
        selected = set(request.material_ids)
        allowed = set(space.allowed_material_ids)
        issues: list[RelaxationCandidate] = []

        def add(name: str, message: str) -> None:
            issues.append(
                RelaxationCandidate(
                    constraint=name,
                    direction="structural",
                    amount=0,
                    unit="",
                    message=message,
                )
            )

        unknown = sorted(selected - allowed)
        if unknown:
            add("available_materials", f"設計空間にない原料を除外してください: {', '.join(unknown)}")
        if space.balance_material_id not in selected:
            add(
                "balance_material",
                f"balance原料 {space.balance_material_id} を使用原料へ含めてください",
            )
        if not request.inclusion_decisions:
            count = len(selected)
            if not space.selection_count.minimum <= count <= space.selection_count.maximum:
                add(
                    "selection_count",
                    (
                        f"LPの固定原料数 {count} を "
                        f"{space.selection_count.minimum}〜{space.selection_count.maximum} にしてください"
                    ),
                )
            group_by_id = {
                item.material_id: item.group
                for item in contracts.scientific_master.materials
            }
            for constraint in space.group_cardinalities:
                group_count = sum(
                    group_by_id.get(material_id) == constraint.group
                    for material_id in selected
                )
                if not constraint.minimum <= group_count <= constraint.maximum:
                    add(
                        f"group_cardinality:{constraint.group}",
                        (
                            f"{constraint.group} の固定原料数 {group_count} を "
                            f"{constraint.minimum}〜{constraint.maximum} にしてください"
                        ),
                    )
        return tuple(issues)

    def _build_problem(
        self,
        contracts: ResolvedBlendContracts,
        baseline: SparseBlend,
        request: BlendOptimizationRequest,
    ) -> _Problem:
        material_ids = tuple(request.material_ids)
        n = len(material_ids)
        use_milp = request.inclusion_decisions
        base_vars = n * (2 if use_milp else 1)
        baseline_vars = n if request.objective == "baseline_l1" else 0
        variable_count = base_vars + baseline_vars
        objective = np.zeros(variable_count)
        fill = contracts.design_space.fixed_fill_ratio / 100.0
        commercial = {
            item.material_id: item for item in contracts.commercial_catalog.materials
        }
        if request.objective == "cost":
            objective[:n] = [
                commercial[material_id].unit_price_yen_per_kg_core / fill
                for material_id in material_ids
            ]
        else:
            objective[base_vars:] = 1.0

        rows: list[_Row] = []

        def vector(values: dict[int, float]) -> np.ndarray:
            result = np.zeros(variable_count)
            for index, value in values.items():
                result[index] = value
            return result

        rows.append(
            _Row(
                "total",
                vector({index: 1.0 for index in range(n)}),
                lower=fill,
                upper=fill,
                unit="whole-wire fraction",
                relaxable=False,
            )
        )
        space = contracts.design_space
        bound_by_id = {item.material_id: item for item in space.material_bounds}
        activation = fill * _ACTIVATION_RATIO_PERCENT / 100.0
        for index, material_id in enumerate(material_ids):
            bound = bound_by_id.get(material_id)
            lower = fill * (bound.lower if bound else 0.0) / 100.0
            upper = fill * (bound.upper if bound else 100.0) / 100.0
            if use_milp:
                y = n + index
                rows.append(
                    _Row(
                        f"material:{material_id}",
                        vector({index: 1.0, y: -max(lower, activation)}),
                        lower=0.0,
                        unit="core mass %",
                        display_scale=100.0 / fill,
                    )
                )
                rows.append(
                    _Row(
                        f"material:{material_id}",
                        vector({index: 1.0, y: -upper}),
                        upper=0.0,
                        unit="core mass %",
                        display_scale=100.0 / fill,
                    )
                )
            else:
                rows.append(
                    _Row(
                        f"material:{material_id}",
                        vector({index: 1.0}),
                        lower=max(lower, activation),
                        upper=upper,
                        unit="core mass %",
                        display_scale=100.0 / fill,
                    )
                )

        scientific = {
            item.material_id: item for item in contracts.scientific_master.materials
        }
        hoop = next(
            item for item in contracts.scientific_master.hoops
            if item.hoop_id == space.fixed_hoop_id
        )
        for target in request.composition_targets:
            if target.component not in contracts.scientific_master.components:
                raise CandidateValidationError(
                    f"Stage Aにない材料成分です: {target.component}"
                )
            constant = (1.0 - fill) * hoop.composition[target.component]
            rows.append(
                _Row(
                    f"composition:{target.component}",
                    vector(
                        {
                            index: scientific[material_id].composition[target.component]
                            for index, material_id in enumerate(material_ids)
                        }
                    ),
                    lower=target.lower - constant,
                    upper=target.upper - constant,
                    unit="mass %",
                )
            )

        group_by_id = {
            item.material_id: item.group
            for item in contracts.scientific_master.materials
        }
        for constraint in space.group_totals:
            coefficients = {
                index: 1.0
                for index, material_id in enumerate(material_ids)
                if group_by_id[material_id] == constraint.group
            }
            rows.append(
                _Row(
                    f"group_total:{constraint.group}",
                    vector(coefficients),
                    lower=fill * constraint.lower / 100.0,
                    upper=fill * constraint.upper / 100.0,
                    unit="core mass %",
                    display_scale=100.0 / fill,
                )
            )

        if use_milp:
            rows.append(
                _Row(
                    "selection_count",
                    vector({n + index: 1.0 for index in range(n)}),
                    lower=float(space.selection_count.minimum),
                    upper=float(space.selection_count.maximum),
                    unit="materials",
                )
            )
            for constraint in space.group_cardinalities:
                coefficients = {
                    n + index: 1.0
                    for index, material_id in enumerate(material_ids)
                    if group_by_id[material_id] == constraint.group
                }
                rows.append(
                    _Row(
                        f"group_cardinality:{constraint.group}",
                        vector(coefficients),
                        lower=float(constraint.minimum),
                        upper=float(constraint.maximum),
                        unit="materials",
                    )
                )
            balance_index = material_ids.index(space.balance_material_id)
            rows.append(
                _Row(
                    "balance_material",
                    vector({n + balance_index: 1.0}),
                    lower=1.0,
                    upper=1.0,
                    unit="selected",
                    relaxable=False,
                )
            )

        if request.objective == "baseline_l1":
            baseline_ratio = {
                item.material_id: item.ratio for item in baseline.items
            }
            for index, material_id in enumerate(material_ids):
                baseline_z = fill * baseline_ratio.get(material_id, 0.0) / 100.0
                deviation = base_vars + index
                rows.extend(
                    (
                        _Row(
                            f"objective_l1:{material_id}",
                            vector({index: 1.0, deviation: -1.0}),
                            upper=baseline_z,
                            relaxable=False,
                        ),
                        _Row(
                            f"objective_l1:{material_id}",
                            vector({index: -1.0, deviation: -1.0}),
                            upper=-baseline_z,
                            relaxable=False,
                        ),
                    )
                )

        lower_bounds = np.zeros(variable_count)
        upper_bounds = np.full(variable_count, np.inf)
        upper_bounds[:n] = fill
        integrality = np.zeros(variable_count)
        if use_milp:
            upper_bounds[n : n * 2] = 1.0
            integrality[n : n * 2] = 1
        return _Problem(
            objective=objective,
            rows=tuple(rows),
            lower_bounds=lower_bounds,
            upper_bounds=upper_bounds,
            integrality=integrality,
            material_ids=material_ids,
            z_count=n,
        )

    @staticmethod
    def _solve(problem: _Problem, use_milp: bool) -> np.ndarray | None:
        matrix = np.vstack([row.coefficients for row in problem.rows])
        lower = np.array([row.lower for row in problem.rows])
        upper = np.array([row.upper for row in problem.rows])
        if use_milp:
            result = milp(
                c=problem.objective,
                integrality=problem.integrality,
                bounds=Bounds(problem.lower_bounds, problem.upper_bounds),
                constraints=LinearConstraint(matrix, lower, upper),
                options={"time_limit": 30.0},
            )
        else:
            equal = np.isclose(lower, upper) & np.isfinite(lower)
            upper_rows: list[np.ndarray] = []
            upper_rhs: list[float] = []
            for coefficients, low, high, is_equal in zip(
                matrix, lower, upper, equal, strict=True
            ):
                if is_equal:
                    continue
                if math.isfinite(high):
                    upper_rows.append(coefficients)
                    upper_rhs.append(high)
                if math.isfinite(low):
                    upper_rows.append(-coefficients)
                    upper_rhs.append(-low)
            result = linprog(
                c=problem.objective,
                A_ub=np.vstack(upper_rows) if upper_rows else None,
                b_ub=np.array(upper_rhs) if upper_rhs else None,
                A_eq=matrix[equal] if equal.any() else None,
                b_eq=lower[equal] if equal.any() else None,
                bounds=list(zip(problem.lower_bounds, problem.upper_bounds, strict=True)),
                method="highs",
                options={"time_limit": 30.0},
            )
        return np.asarray(result.x) if result.success and result.x is not None else None

    @staticmethod
    def _relax(
        problem: _Problem,
        use_milp: bool,
    ) -> tuple[RelaxationCandidate, ...]:
        relaxations: list[tuple[int, Literal["lower", "upper"]]] = []
        for index, row in enumerate(problem.rows):
            if not row.relaxable:
                continue
            if math.isfinite(row.lower):
                relaxations.append((index, "lower"))
            if math.isfinite(row.upper):
                relaxations.append((index, "upper"))
        if not relaxations:
            return ()
        original_count = len(problem.objective)
        total_count = original_count + len(relaxations)
        objective = np.zeros(total_count)
        rows: list[_Row] = []
        slack_index_by_side = {
            side: original_count + offset
            for offset, side in enumerate(relaxations)
        }
        for row_index, row in enumerate(problem.rows):
            coefficients = np.pad(row.coefficients, (0, len(relaxations)))
            lower = row.lower
            upper = row.upper
            lower_key = (row_index, "lower")
            upper_key = (row_index, "upper")
            if lower_key in slack_index_by_side:
                slack_index = slack_index_by_side[lower_key]
                coefficients[slack_index] = 1.0
                objective[slack_index] = 1.0 / max(abs(lower), 1.0)
            if upper_key in slack_index_by_side:
                slack_index = slack_index_by_side[upper_key]
                coefficients[slack_index] = -1.0
                objective[slack_index] = 1.0 / max(abs(upper), 1.0)
            rows.append(
                _Row(
                    row.name,
                    coefficients,
                    lower=lower,
                    upper=upper,
                    unit=row.unit,
                    display_scale=row.display_scale,
                    relaxable=False,
                )
            )
        relaxed = _Problem(
            objective=objective,
            rows=tuple(rows),
            lower_bounds=np.concatenate(
                (problem.lower_bounds, np.zeros(len(relaxations)))
            ),
            upper_bounds=np.concatenate(
                (problem.upper_bounds, np.full(len(relaxations), np.inf))
            ),
            integrality=np.concatenate(
                (problem.integrality, np.zeros(len(relaxations)))
            ),
            material_ids=problem.material_ids,
            z_count=problem.z_count,
        )
        solution = BlendOptimizationService._solve(relaxed, use_milp)
        if solution is None:
            return ()
        result: list[RelaxationCandidate] = []
        for side, slack_index in slack_index_by_side.items():
            solver_amount = float(solution[slack_index])
            if solver_amount <= 1e-8:
                continue
            row_index, direction = side
            row = problem.rows[row_index]
            current = row.lower if direction == "lower" else row.upper
            suggested = (
                current - solver_amount
                if direction == "lower"
                else current + solver_amount
            )
            display_amount = solver_amount * row.display_scale
            display_current = current * row.display_scale
            display_suggested = suggested * row.display_scale
            result.append(
                RelaxationCandidate(
                    constraint=row.name,
                    direction=direction,
                    current=float(display_current),
                    suggested=float(display_suggested),
                    amount=display_amount,
                    unit=row.unit,
                    message=(
                        f"{row.name} の{('下限' if direction == 'lower' else '上限')}を"
                        f" {display_amount:.6g} {row.unit} 緩和すると可解性が回復する可能性があります"
                    ),
                )
            )
        return tuple(sorted(result, key=lambda item: item.amount, reverse=True))
