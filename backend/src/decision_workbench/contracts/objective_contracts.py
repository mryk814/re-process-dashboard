"""Immutable decision objectives shared by screening and proposal activities."""
from __future__ import annotations

import math
from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, model_validator

from decision_workbench.contracts.task_contracts import (
    ContractModel,
    NumericRange,
    RuntimeCapability,
    TaskDefinition,
)
from decision_workbench.execution.inference_work_graph import semantic_digest


class ObjectiveIncumbent(ContractModel):
    source: Literal[
        "none",
        "observed_best",
        "candidate_revision",
        "prediction_snapshot",
        "project_decision",
    ] = "none"
    candidate_id: str | None = None
    candidate_revision: int | None = None
    snapshot_id: str | None = None
    observed_scope: Literal["project_actuals"] | None = None

    @model_validator(mode="before")
    @classmethod
    def give_observed_best_an_explicit_scope(cls, value: object) -> object:
        if isinstance(value, dict) and value.get("source") == "observed_best":
            return {**value, "observed_scope": value.get("observed_scope") or "project_actuals"}
        return value

    @model_validator(mode="after")
    def complete_reference(self) -> "ObjectiveIncumbent":
        if self.source == "none" and any(
            (self.candidate_id, self.candidate_revision, self.snapshot_id)
        ):
            raise ValueError("incumbentなしには参照を指定できません")
        if self.source == "candidate_revision" and (
            not self.candidate_id or self.candidate_revision is None
        ):
            raise ValueError("candidate incumbentにはcandidate IDとrevisionが必要です")
        if self.source == "prediction_snapshot" and (
            not self.candidate_id or not self.snapshot_id
        ):
            raise ValueError("snapshot incumbentにはcandidate IDとsnapshot IDが必要です")
        if self.source == "observed_best" and self.observed_scope != "project_actuals":
            raise ValueError("observed bestにはProject実測の母集団指定が必要です")
        if self.source != "observed_best" and self.observed_scope is not None:
            raise ValueError("observed scopeはobserved bestだけに指定できます")
        return self


class ObjectiveTerm(ContractModel):
    output_key: Annotated[str, Field(min_length=1)]
    unit: Annotated[str, Field(min_length=1)]
    role: Literal[
        "primary_objective",
        "hard_outcome_constraint",
        "soft_preference",
        "reporting_only",
    ]
    direction: Literal[
        "maximize",
        "minimize",
        "at_least",
        "at_most",
        "between",
        "target",
    ] | None = None
    lower: float | None = None
    upper: float | None = None
    target: float | None = None
    weight: Annotated[float | None, Field(gt=0, allow_inf_nan=False)] = None
    normalization_range: NumericRange | None = None

    @model_validator(mode="after")
    def complete_term(self) -> "ObjectiveTerm":
        values = (self.lower, self.upper, self.target)
        if any(value is not None and not math.isfinite(value) for value in values):
            raise ValueError("Objectiveの基準値は有限数にします")
        if self.role == "reporting_only":
            if self.direction is not None or any(value is not None for value in values):
                raise ValueError("reporting onlyへ方向や基準値を指定できません")
            return self
        if self.direction is None:
            raise ValueError("判断に使うoutputには方向が必要です")
        if self.direction == "at_least" and (
            self.lower is None or self.upper is not None or self.target is not None
        ):
            raise ValueError("at_leastにはlowerだけを指定します")
        if self.direction == "at_most" and (
            self.upper is None or self.lower is not None or self.target is not None
        ):
            raise ValueError("at_mostにはupperだけを指定します")
        if self.direction == "between" and (
            self.lower is None
            or self.upper is None
            or self.lower >= self.upper
            or self.target is not None
        ):
            raise ValueError("betweenにはlower < upperを指定します")
        if self.direction == "target" and (
            self.target is None or self.lower is not None or self.upper is not None
        ):
            raise ValueError("targetにはtarget値だけを指定します")
        if self.direction in {"maximize", "minimize"} and any(
            value is not None for value in values
        ):
            raise ValueError("maximize/minimizeへ基準値を指定できません")
        if self.role == "soft_preference":
            if self.weight is None or self.normalization_range is None:
                raise ValueError("soft preferenceにはweightと正規化範囲が必要です")
        elif self.weight is not None or self.normalization_range is not None:
            raise ValueError("weightと正規化範囲はsoft preferenceだけに指定します")
        return self


class ObjectiveDefinition(ContractModel):
    schema_version: Literal["objective-definition/v1"] = "objective-definition/v1"
    objective_id: Annotated[str, Field(min_length=1)]
    revision: Annotated[int, Field(ge=1)] = 1
    name: Annotated[str, Field(min_length=1)]
    task_id: Annotated[str, Field(min_length=1)]
    task_contract_digest: Annotated[str, Field(min_length=1)]
    optimization_kind: Literal[
        "single_objective",
        "constrained_single_objective",
        "pareto_multi_objective",
        "legacy_screening",
    ]
    terms: Annotated[tuple[ObjectiveTerm, ...], Field(min_length=1)]
    incumbent: ObjectiveIncumbent = ObjectiveIncumbent()

    @property
    def digest(self) -> str:
        return semantic_digest(self.model_dump(mode="json"))

    @model_validator(mode="after")
    def roles_match_optimization_kind(self) -> "ObjectiveDefinition":
        keys = [term.output_key for term in self.terms]
        if len(keys) != len(set(keys)):
            raise ValueError("Objectiveのoutputは重複できません")
        primary = [term for term in self.terms if term.role == "primary_objective"]
        hard = [term for term in self.terms if term.role == "hard_outcome_constraint"]
        if self.optimization_kind == "single_objective" and (
            len(primary) != 1 or hard
        ):
            raise ValueError("single objectiveは主目的1件でhard constraintなしです")
        if self.optimization_kind == "constrained_single_objective" and (
            len(primary) != 1 or not hard
        ):
            raise ValueError("constrained single objectiveは主目的1件とhard constraintが必要です")
        if self.optimization_kind == "pareto_multi_objective" and len(primary) < 2:
            raise ValueError("Pareto multi objectiveには主目的が2件以上必要です")
        if self.optimization_kind == "legacy_screening" and primary:
            raise ValueError("legacy screeningを最適化Objectiveとして表現できません")
        return self

    def validate_against(
        self,
        task: TaskDefinition,
        runtime: RuntimeCapability,
    ) -> None:
        if self.task_id != task.id or runtime.task_id != task.id:
            raise ValueError("ObjectiveのTaskがTaskDefinitionと一致しません")
        expected_digest = semantic_digest(task.model_dump(mode="json"))
        if self.task_contract_digest != expected_digest:
            raise ValueError("ObjectiveのTaskDefinition digestが一致しません")
        outputs = {output.key: output for output in task.outputs}
        capabilities = {item.target: item for item in runtime.targets}
        for term in self.terms:
            output = outputs.get(term.output_key)
            if output is None:
                raise ValueError(f"ObjectiveのoutputがTaskにありません: {term.output_key}")
            if output.unit != term.unit:
                raise ValueError(f"Objectiveの単位がTaskと一致しません: {term.output_key}")
            capability = capabilities.get(term.output_key)
            if capability is None or not capability.point_statistics:
                raise ValueError(f"ObjectiveのoutputをRuntimeが予測できません: {term.output_key}")
            expected = output.goal_direction
            compatible = (
                term.direction in {"between", None}
                or expected == "at_least" and term.direction in {"at_least", "maximize"}
                or expected == "at_most" and term.direction in {"at_most", "minimize"}
                or expected == "target" and term.direction == "target"
            )
            if not compatible:
                raise ValueError(f"Objectiveの方向がTaskと一致しません: {term.output_key}")


class ObjectiveDefinitionRevision(ContractModel):
    project_id: str
    objective_definition: ObjectiveDefinition
    objective_definition_digest: str
    binding_provenance: Literal[
        "explicit",
        "generated_default",
        "inherited_predecessor",
        "updated_revision",
    ]
    created_at: datetime

    @model_validator(mode="after")
    def digest_matches_definition(self) -> "ObjectiveDefinitionRevision":
        if self.objective_definition.digest != self.objective_definition_digest:
            raise ValueError("Objective revision digestが定義と一致しません")
        return self


def objective_from_project_targets(
    *,
    task: TaskDefinition,
    task_contract_digest: str,
    target_values: dict[str, object],
) -> ObjectiveDefinition | None:
    """Build the immutable default used when a new Project declares targets."""

    if not target_values:
        return None
    outputs = {item.key: item for item in task.outputs}
    terms: list[ObjectiveTerm] = []
    for index, (key, raw) in enumerate(target_values.items()):
        output = outputs[key]
        role = "primary_objective" if index == 0 else "hard_outcome_constraint"
        if hasattr(raw, "lower") and hasattr(raw, "upper"):
            term = ObjectiveTerm(
                output_key=key,
                unit=output.unit,
                role=role,
                direction="between",
                lower=float(getattr(raw, "lower")),
                upper=float(getattr(raw, "upper")),
            )
        elif output.goal_direction == "at_least":
            term = ObjectiveTerm(
                output_key=key,
                unit=output.unit,
                role=role,
                direction="at_least",
                lower=float(raw),
            )
        elif output.goal_direction == "at_most":
            term = ObjectiveTerm(
                output_key=key,
                unit=output.unit,
                role=role,
                direction="at_most",
                upper=float(raw),
            )
        else:
            raise ValueError(f"方向のない目標特性には範囲が必要です: {key}")
        terms.append(term)
    kind = "single_objective" if len(terms) == 1 else "constrained_single_objective"
    identity = semantic_digest(
        {"task_id": task.id, "terms": [term.model_dump(mode="json") for term in terms]}
    )
    return ObjectiveDefinition(
        objective_id=f"{task.id}-project-{identity.removeprefix('sha256:')[:12]}",
        name="Project目標",
        task_id=task.id,
        task_contract_digest=task_contract_digest,
        optimization_kind=kind,
        terms=tuple(terms),
    )


def objective_from_screening(
    *,
    task: TaskDefinition,
    task_contract_digest: str,
    target: str,
    target_goal: object | None,
    secondary_goals: dict[str, object],
) -> ObjectiveDefinition:
    """Convert the legacy ScreeningGoal fields without overstating their meaning."""

    outputs = {item.key: item for item in task.outputs}

    def term_for(key: str, goal: object, role: str) -> ObjectiveTerm:
        direction = str(getattr(goal, "direction"))
        return ObjectiveTerm(
            output_key=key,
            unit=outputs[key].unit,
            role=role,  # type: ignore[arg-type]
            direction=direction,  # type: ignore[arg-type]
            lower=getattr(goal, "lower", None),
            upper=getattr(goal, "upper", None),
        )

    terms: list[ObjectiveTerm] = []
    if target_goal is None:
        terms.append(
            ObjectiveTerm(
                output_key=target,
                unit=outputs[target].unit,
                role="reporting_only",
            )
        )
    else:
        terms.append(term_for(target, target_goal, "primary_objective"))
    terms.extend(
        term_for(key, goal, "hard_outcome_constraint")
        for key, goal in secondary_goals.items()
    )
    kind = (
        "legacy_screening"
        if target_goal is None
        else "constrained_single_objective"
        if secondary_goals
        else "single_objective"
    )
    identity = semantic_digest(
        {
            "task_id": task.id,
            "kind": kind,
            "terms": [term.model_dump(mode="json") for term in terms],
        }
    )
    return ObjectiveDefinition(
        objective_id=f"legacy-screening-{identity.removeprefix('sha256:')[:12]}",
        name="範囲探索の判断基準",
        task_id=task.id,
        task_contract_digest=task_contract_digest,
        optimization_kind=kind,
        terms=tuple(terms),
    )
