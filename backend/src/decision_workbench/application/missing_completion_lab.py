from __future__ import annotations

import math
from typing import Literal

import numpy as np

from decision_workbench.contracts.candidate_project_contracts import Candidate
from decision_workbench.contracts.missingness_contracts import (
    CompletionUncertainty,
    MissingCompletionLabReport,
    MissingCompletionSummary,
)
from decision_workbench.design_priors.loader import VerifiedDesignPriorPackage
from decision_workbench.design_priors.sampling import (
    sample_conditional_completions,
)
from decision_workbench.execution.inference_work_graph import semantic_digest
from decision_workbench.modeling.missingness import missing_pattern


def _model_uncertainty(prediction: object) -> float:
    components = getattr(prediction, "uncertainty_components", None) or {}
    if "total_predictive_std" in components:
        return max(float(components["total_predictive_std"]), 0.0)
    if "total_predictive_variance" in components:
        return math.sqrt(max(float(components["total_predictive_variance"]), 0.0))
    raise ValueError(
        "このPredictive Modelはmodel uncertaintyを分解できないため、"
        "Completion Labでcombined uncertaintyを推定できません"
    )


def _validate_runtime_binding(
    runtime: object,
    package: VerifiedDesignPriorPackage,
) -> tuple[str, str]:
    manifest = package.manifest
    task_id = getattr(runtime, "task_id", None)
    task_contract_digest = getattr(runtime, "task_contract_digest", None)
    schema_version = getattr(runtime, "canonical_input_schema_version", None)
    if (
        task_id != manifest.task_id
        or task_contract_digest != manifest.task_contract_digest
        or schema_version != manifest.canonical_input_schema_version
    ):
        raise ValueError(
            "Design Prior PackageのTask、contract、canonical schemaが"
            "Predictive runtimeと一致しません"
        )
    runtime_paths = {
        item.path for item in runtime.missing_policy_inputs  # type: ignore[attr-defined]
    }
    if not set(manifest.canonical_input_paths).issubset(runtime_paths):
        raise ValueError(
            "Design Prior Packageのcanonical inputがPredictive runtimeと一致しません"
        )
    model_package = getattr(runtime, "model_package", None)
    predictive_manifest = getattr(model_package, "manifest", None)
    predictive_manifest_sha256 = getattr(model_package, "manifest_sha256", None)
    predictive_package_id = getattr(predictive_manifest, "package_id", None)
    if not predictive_package_id or not predictive_manifest_sha256:
        raise ValueError(
            "Completion Labには固定されたPredictive Model Package identityが必要です"
        )
    return (
        str(predictive_package_id),
        f"sha256:{predictive_manifest_sha256}",
    )


def run_missing_completion_lab(
    runtime: object,
    candidate: Candidate,
    package: VerifiedDesignPriorPackage,
    *,
    generator_id: Literal["empirical_rows", "knn_local"],
    sample_count: int = 32,
    seed: int = 20260801,
) -> MissingCompletionLabReport:
    if not 2 <= sample_count <= 256:
        raise ValueError("Completion Labのsample_countは2から256の範囲で指定してください")
    predictive_package_id, predictive_manifest_digest = (
        _validate_runtime_binding(runtime, package)
    )
    profile_inputs = runtime.missing_policy_inputs  # type: ignore[attr-defined]
    pattern = missing_pattern(candidate, profile_inputs)
    missing_paths = tuple(
        path
        for path, kind in pattern
        if kind == "not_measured"
    )
    unsupported_missing = tuple(
        path for path, kind in pattern if kind != "not_measured"
    )
    if unsupported_missing:
        raise ValueError(
            "Completion Labはnot_measuredだけを補完できます: "
            + ", ".join(unsupported_missing)
        )
    if not missing_paths:
        raise ValueError("Completion Labには補完対象の入力欠損が必要です")
    observed: dict[str, float | str] = {}
    for path in package.manifest.canonical_input_paths:
        if path in missing_paths:
            continue
        group, key = path.split(".", 1)
        value = getattr(candidate.inputs, group).get(key)
        if isinstance(value, (float, int, str)) and not isinstance(value, bool):
            observed[path] = value
    samples = sample_conditional_completions(
        package,
        generator_id=generator_id,
        count=sample_count,
        seed=seed,
        observed=observed,
        missing_paths=missing_paths,
    )
    results = []
    for sample in samples:
        completed = candidate.model_copy(deep=True)
        for path, value in sample.values.items():
            group, key = path.split(".", 1)
            getattr(completed.inputs, group)[key] = value
        results.append(
            runtime.predict_core(  # type: ignore[attr-defined]
                completed,
                detailed=False,
                _missingness_operation="completion_lab",
            )
        )

    summaries: list[MissingCompletionSummary] = []
    for target in results[0]["predictions"]:
        predictions = [result["predictions"][target] for result in results]
        points = np.asarray([item.value for item in predictions], dtype=float)
        model_stds = np.asarray(
            [_model_uncertainty(item) for item in predictions],
            dtype=float,
        )
        model = float(np.sqrt(np.mean(model_stds**2)))
        input_missingness = float(np.std(points, ddof=1)) if len(points) > 1 else 0.0
        combined = math.sqrt(model**2 + input_missingness**2)
        mean = float(np.mean(points))
        summaries.append(
            MissingCompletionSummary(
                target=target,
                mean=mean,
                uncertainty=CompletionUncertainty(
                    model=model,
                    input_missingness=input_missingness,
                    combined=combined,
                ),
            )
        )
    return MissingCompletionLabReport(
        candidate_id=candidate.id,
        generator_id=generator_id,
        sample_count=sample_count,
        seed=seed,
        task_id=str(runtime.task_id),  # type: ignore[attr-defined]
        task_contract_digest=str(  # type: ignore[attr-defined]
            runtime.task_contract_digest
        ),
        canonical_input_schema_version=str(  # type: ignore[attr-defined]
            runtime.canonical_input_schema_version
        ),
        predictive_package_id=predictive_package_id,
        predictive_manifest_digest=predictive_manifest_digest,
        design_prior_package_id=package.manifest.package_id,
        design_prior_manifest_digest=f"sha256:{package.manifest_sha256}",
        candidate_revision=candidate.revision,
        candidate_input_digest=semantic_digest({
            "inputs": candidate.inputs.model_dump(mode="json"),
            "input_missing_kinds": candidate.input_missing_kinds,
        }),
        missing_paths=missing_paths,
        summaries=tuple(summaries),
        completion_evidence=tuple(
            sample.evidence.model_dump(mode="json")
            for sample in samples
        ),
    )
