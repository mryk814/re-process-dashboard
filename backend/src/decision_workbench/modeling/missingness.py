from __future__ import annotations

from typing import Any, Mapping, Sequence

from decision_workbench.contracts.candidate_project_contracts import CandidateInput
from decision_workbench.contracts.missingness_contracts import (
    InputMissingnessEvidence,
    MissingFieldEvidence,
    MissingnessOperation,
    MissingnessOperationCapability,
)
from decision_workbench.execution.inference_work_graph import semantic_digest


PATTERN_SUPPORT_POLICY_ID = "evaluated-missing-pattern"
PATTERN_SUPPORT_POLICY_VERSION = "1.1.0"
PATTERN_SUPPORT_POLICY_CONFIG = {
    "minimum_training_count": 2,
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


def resolve_missingness_operation_capability(
    package_missing_policy: Any | None,
) -> MissingnessOperationCapability | None:
    """Resolve only an explicitly declared verified-Package capability."""

    if package_missing_policy is None:
        return None
    if hasattr(package_missing_policy, "operation_capability"):
        return package_missing_policy.operation_capability
    if not isinstance(package_missing_policy, Mapping):
        raise TypeError("Package missing policy must be a mapping or contract")
    raw = package_missing_policy.get("operation_capability")
    return (
        None
        if raw is None
        else MissingnessOperationCapability.model_validate(raw)
    )


def classify_missingness_pattern_support(
    pattern_evidence: Mapping[str, Any] | None,
    *,
    support_policy: Mapping[str, Any] | None = None,
) -> str:
    """Apply the shared Package/runtime support threshold to fixed evidence."""

    if pattern_evidence is None:
        return "unseen"
    policy = support_policy or pattern_support_policy_document()
    expected = pattern_support_policy_document()
    if policy != expected:
        raise ValueError("Package missing-pattern support policy is incompatible")
    metrics = pattern_evidence.get("metrics_by_target") or {}
    if (
        int(pattern_evidence.get("training_count", 0))
        >= int(policy["config"]["minimum_training_count"])
        and int(pattern_evidence.get("evaluation_count", 0))
        >= int(policy["config"]["minimum_evaluation_count"])
        and metrics
        and all(
            float(item.get("prediction_failure_rate", 1.0))
            <= float(policy["config"]["maximum_prediction_failure_rate"])
            for item in metrics.values()
        )
    ):
        return "supported"
    return "sparse"


def _operation_capability_blocks(
    capability: MissingnessOperationCapability | None,
    *,
    operation: MissingnessOperation,
    support: str,
) -> bool:
    if capability is None:
        return (
            operation == "detailed_prediction" and support != "supported"
        ) or operation in {"snapshot", "proposal", "export"}
    if operation == "preview":
        return capability.preview == "block"
    if operation == "detailed_prediction":
        return capability.comparison == "block" or support != "supported"
    if operation == "snapshot":
        return capability.snapshot != "allow" or support != "supported"
    if operation == "proposal":
        # allow_with_quota requires a quota-bearing proposal request.  The
        # current request contract has no such proof, so it remains blocked.
        return True
    if operation == "export":
        return (
            capability.export == "require_complete"
            or support != "supported"
        )
    return False


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
    operation_capability: MissingnessOperationCapability | None = None,
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
        if kind in {"structural_not_applicable", "redacted"}:
            applied_policy = kind
            imputed_value: float | str | None = None
            effective_missing = True
            incompatible = True
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
    else:
        support = classify_missingness_pattern_support(
            pattern_evidence,
            support_policy=support_policy,
        )

    blocked = incompatible or (
        effective_missing
        and _operation_capability_blocks(
            operation_capability,
            operation=operation,
            support=support,
        )
    )
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
