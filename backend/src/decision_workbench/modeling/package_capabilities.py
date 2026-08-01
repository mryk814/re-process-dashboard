"""One capability matrix for a verified Model Package.

The Task contract declares what a selected Package is allowed to expose.  This
module binds that declaration to the Package's predictor metadata without
changing the Package bytes (and therefore without changing pinned Project or
Snapshot identity).  Consumers ask for named capabilities; they never infer
them from a Task id, runtime class, or an interval shape.
"""
from __future__ import annotations

from decision_workbench.contracts.model_capability_contracts import (
    CapabilityAvailability,
    CapabilityRequirement,
    ModelPackageCapabilityMatrix,
    TargetCapabilityMatrix,
)
from decision_workbench.contracts.task_contracts import RuntimeCapability
from decision_workbench.modeling.packages.contracts import ModelPackageManifest


def package_capability_matrix(
    manifest: ModelPackageManifest,
    runtime_capability: RuntimeCapability,
    *,
    manifest_digest: str,
) -> ModelPackageCapabilityMatrix:
    """Bind a verified manifest to its declared RuntimeCapability fail-closed."""
    if manifest.task_id != runtime_capability.task_id:
        raise ValueError("model package capability task does not match runtime capability")
    declared = {item.target: item for item in runtime_capability.targets}
    predictors = {item.target: item for item in manifest.predictors}
    if set(declared) != set(predictors):
        raise ValueError("model package predictors do not match runtime capability targets")
    targets = []
    for target, predictor in predictors.items():
        item = declared[target]
        targets.append(TargetCapabilityMatrix(
            target=target, target_kind=predictor.target_kind,
            predictive_family=predictor.predictive_family,
            point_statistics=item.point_statistics, quantiles=item.quantiles,
            standard_deviation=item.standard_deviation,
            predictive_samples=item.samples,
            parametric_distribution=item.parametric_distribution,
            uncertainty_components=item.uncertainty_components,
            support=item.support, warnings=item.warnings,
            goal_probability=item.goal_probability,
            explanation=item.explanation,
        ))
    return ModelPackageCapabilityMatrix(
        task_id=manifest.task_id, package_id=manifest.package_id,
        package_manifest_digest=f"sha256:{manifest_digest}",
        targets=tuple(sorted(targets, key=lambda item: item.target)),
        joint_samples=runtime_capability.joint_samples,
    )


_LABELS = {
    "mean_point": "平均による点予測", "median_point": "中央値による点予測",
    "quantiles": "予測分位点", "standard_deviation": "予測標準偏差",
    "predictive_samples": "予測sample", "joint_samples": "joint sample",
    "parametric_distribution": "パラメトリック分布", "goal_probability": "目標達成確率",
    "support": "学習支持範囲", "explanation": "局所説明", "normal_mean_std": "normal mean/std",
}


def resolve_capabilities(
    matrix: ModelPackageCapabilityMatrix,
    *,
    target: str | None,
    requirements: tuple[CapabilityRequirement, ...],
) -> CapabilityAvailability:
    selected = matrix.targets if target is None else (() if matrix.target(target) is None else (matrix.target(target),))
    reasons: list[str] = []
    if not selected:
        reasons.append("選択したoutputをModel Packageが予測できません")
    for requirement in requirements:
        if requirement.capability == "joint_samples":
            supported = matrix.joint_samples
        else:
            supported = bool(selected) and all(item.supports(requirement.capability) for item in selected)
        if not supported:
            suffix = f"。代替: {requirement.alternative}" if requirement.alternative else ""
            reasons.append(f"{_LABELS[requirement.capability]}に対応するModel Packageが必要です{suffix}")
    return CapabilityAvailability(available=not reasons, reasons=tuple(reasons))
