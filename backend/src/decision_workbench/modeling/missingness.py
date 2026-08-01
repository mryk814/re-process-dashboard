from __future__ import annotations

from typing import Any, Mapping, Sequence

from decision_workbench.contracts.candidate_project_contracts import CandidateInput
from decision_workbench.contracts.missingness_contracts import (
    InputMissingnessEvidence,
    MissingFieldEvidence,
    MissingnessOperation,
)
from decision_workbench.execution.inference_work_graph import semantic_digest


PATTERN_SUPPORT_POLICY_ID = "evaluated-missing-pattern"
PATTERN_SUPPORT_POLICY_VERSION = "1.0.0"
PATTERN_SUPPORT_POLICY_CONFIG = {
    "minimum_evaluation_count": 2,
    "maximum_prediction_failure_rate": 0.0,
}


def pattern_support_policy_document() -> dict[str, Any]:
    identity = {
        "policy_id": PATTERN_SUPPORT_POLICY_ID,
        "policy_version": PATTERN_SUPPORT_POLICY_VERSION,
        "config": PATTERN_SUPPORT_POLICY_CONFIG,
    }
    return {
        **identity,
        "policy_digest": semantic_digest(identity),
    }


def _value(candidate: CandidateInput, path: str) -> object:
    group, key = path.split(".", 1)
    return getattr(candidate.inputs, group).get(key)


def missing_pattern(
    candidate: CandidateInput,
    inputs: Sequence[Any],
) -> tuple[tuple[str, str], ...]:
    pattern: list[tuple[str, str]] = []
    for item in inputs:
        value = _value(candidate, item.path)
        if value is None or value == "":
            pattern.append(
                (
                    item.path,
                    candidate.input_missing_kinds.get(item.path, "not_measured"),
                )
            )
        elif item.kind == "categorical" and str(value) not in item.choices:
            pattern.append((item.path, "unknown_category"))
    return tuple(sorted(pattern))


def pattern_digest(pattern: Sequence[tuple[str, str]]) -> str:
    return semantic_digest(
        [{"path": path, "kind": kind} for path, kind in pattern]
    )


def assess_input_missingness(
    candidate: CandidateInput,
    inputs: Sequence[Any],
    training_stats: Mapping[str, Any],
    *,
    operation: MissingnessOperation,
) -> InputMissingnessEvidence:
    pattern = missing_pattern(candidate, inputs)
    digest = pattern_digest(pattern)
    missing_policy = training_stats.get("missing_policy") or {}
    support_policy = missing_policy.get(
        "pattern_support_policy"
    ) or pattern_support_policy_document()
    expected_support_policy = pattern_support_policy_document()
    if support_policy != expected_support_policy:
        raise ValueError("Package missing-pattern support policy is incompatible")
    policy_by_input = missing_policy.get("policy_by_input") or {}
    policy_digest = str(
        missing_policy.get("policy_digest")
        or semantic_digest(policy_by_input)
    )
    training_rows = int(missing_policy.get("training_rows") or 0)
    missing_by_input = missing_policy.get("missing_by_input") or {}
    pattern_evidence = next(
        (
            item
            for item in missing_policy.get("pattern_evidence", ())
            if item.get("pattern_digest") == digest
        ),
        None,
    )
    fields: list[MissingFieldEvidence] = []
    incompatible = False
    effective_missing = False
    for path, kind in pattern:
        item = next(profile_input for profile_input in inputs if profile_input.path == path)
        if kind == "structural_not_applicable":
            applied_policy = "structural_not_applicable"
            imputed_value: float | str | None = None
        elif kind == "unknown_category":
            policy = item.unknown_category
            applied_policy = policy.strategy
            imputed_value = (
                "__missing__"
                if policy.strategy == "map_to_missing_category"
                else policy.other_choice
            )
            effective_missing = True
            incompatible = incompatible or policy.strategy == "reject"
        elif item.kind == "number":
            policy = item.numeric_missing
            applied_policy = policy.strategy
            imputed_value = (
                policy.value
                if policy.strategy == "constant"
                else missing_policy.get("imputation_values", {}).get(path)
            )
            effective_missing = True
            incompatible = incompatible or policy.strategy == "reject"
        else:
            policy = item.categorical_missing
            applied_policy = policy.strategy
            imputed_value = (
                "__missing__"
                if policy.strategy == "map_to_missing_category"
                else policy.category
            )
            effective_missing = True
            incompatible = incompatible or policy.strategy == "reject"
        fields.append(
            MissingFieldEvidence(
                path=path,
                kind=kind,
                applied_policy=applied_policy,
                imputed_value=imputed_value,
                policy_digest=policy_digest,
                training_missing_rate=(
                    float(missing_by_input.get(path, 0)) / training_rows
                    if training_rows
                    else None
                ),
                evaluation_count=(
                    int(pattern_evidence.get("evaluation_count", 0))
                    if pattern_evidence is not None
                    else None
                ),
            )
        )
    if incompatible:
        support = "incompatible"
    elif not effective_missing:
        support = "supported"
    elif pattern_evidence is None:
        support = "unseen"
    elif (
        int(pattern_evidence.get("evaluation_count", 0))
        >= int(support_policy["config"]["minimum_evaluation_count"])
        and bool(pattern_evidence.get("metrics_by_target"))
        and all(
            float(metrics.get("prediction_failure_rate", 1.0))
            <= float(
                support_policy["config"]["maximum_prediction_failure_rate"]
            )
            for metrics in pattern_evidence.get(
                "metrics_by_target", {}
            ).values()
        )
    ):
        support = "supported"
    else:
        support = "sparse"

    blocked = incompatible or (
        operation == "detailed_prediction" and support != "supported"
    ) or (operation in {"snapshot", "proposal", "export"} and effective_missing)
    return InputMissingnessEvidence(
        input_completeness=(
            "blocked"
            if blocked
            else "imputed"
            if effective_missing
            else "complete"
        ),
        prediction_status=(
            "blocked"
            if blocked
            else "provisional"
            if effective_missing
            else "final"
        ),
        operation=operation,
        missingness_support=support,
        pattern_digest=digest,
        support_policy_digest=str(support_policy["policy_digest"]),
        fields=tuple(fields),
        uncertainty_propagated=False,
        uncertainty_method=None,
        pattern_training_count=(
            int(pattern_evidence.get("training_count", 0))
            if pattern_evidence is not None
            else None
        ),
        pattern_evaluation_count=(
            int(pattern_evidence.get("evaluation_count", 0))
            if pattern_evidence is not None
            else None
        ),
        pattern_metrics=(
            dict(pattern_evidence.get("metrics_by_target", {}))
            if pattern_evidence is not None
            else {}
        ),
    )


def require_operation_allowed(evidence: InputMissingnessEvidence) -> None:
    if evidence.prediction_status == "blocked":
        paths = ", ".join(item.path for item in evidence.fields)
        raise ValueError(
            f"{evidence.operation}では現在の欠損入力を利用できません: {paths}"
        )


def require_runtime_operation_allowed(
    runtime: Any,
    candidate: CandidateInput,
    *,
    operation: MissingnessOperation,
) -> None:
    inputs = getattr(runtime, "missing_policy_inputs", None)
    training_stats = getattr(runtime, "training_stats", None)
    if inputs is None or training_stats is None:
        return
    require_operation_allowed(
        assess_input_missingness(
            candidate,
            inputs,
            training_stats,
            operation=operation,
        )
    )
