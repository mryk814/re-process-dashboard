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
from decision_workbench.modeling.missingness import missing_pattern


def _model_uncertainty(prediction: object) -> float:
    components = getattr(prediction, "uncertainty_components", None) or {}
    if "model" in components:
        return max(float(components["model"]), 0.0)
    if getattr(prediction, "predictive_family", None) != "normal":
        raise ValueError(
            "このPredictive Modelはmodel uncertaintyを分解できないため、"
            "Completion Labでcombined uncertaintyを推定できません"
        )
    return max(
        float(prediction.upper - prediction.lower)  # type: ignore[attr-defined]
        / (2 * 1.6448536269514722),
        0.0,
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
    profile_inputs = runtime.missing_policy_inputs  # type: ignore[attr-defined]
    pattern = missing_pattern(candidate, profile_inputs)
    missing_paths = tuple(
        path
        for path, kind in pattern
        if kind in {"not_measured", "unknown_category"}
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
                lower=mean - 1.6448536269514722 * combined,
                upper=mean + 1.6448536269514722 * combined,
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
        design_prior_package_id=package.manifest.package_id,
        design_prior_manifest_digest=f"sha256:{package.manifest_sha256}",
        missing_paths=missing_paths,
        summaries=tuple(summaries),
        completion_evidence=tuple(
            sample.evidence.model_dump(mode="json")
            for sample in samples
        ),
    )
