"""One capability matrix for a verified Model Package.

The Task contract declares what a selected Package is allowed to expose.  This
module binds that declaration to the Package's predictor metadata without
changing the Package bytes (and therefore without changing pinned Project or
Snapshot identity).  Consumers ask for named capabilities; they never infer
them from a Task id, runtime class, or an interval shape.
"""
from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from decision_workbench.contracts.task_contracts import ContractModel, RuntimeCapability
from decision_workbench.modeling.packages.contracts import ModelPackageManifest


CapabilityName = Literal[
    "mean_point", "median_point", "quantiles", "standard_deviation",
    "predictive_samples", "joint_samples", "parametric_distribution",
    "goal_probability", "support", "explanation", "normal_mean_std",
]


class CapabilityRequirement(ContractModel):
    capability: CapabilityName
    alternative: str | None = None


class TargetCapabilityMatrix(ContractModel):
    target: str
    target_kind: Literal["continuous", "continuous_positive", "binary", "count", "ordinal"]
    predictive_family: str
    point_statistics: tuple[Literal["mean", "median", "probability", "rate", "expected_category"], ...]
    quantiles: bool
    standard_deviation: bool
    predictive_samples: bool
    parametric_distribution: bool
    uncertainty_components: bool
    support: bool
    warnings: bool
    goal_probability: Literal["native", "samples", "distribution", "normal_approximation", "unavailable"]
    explanation: bool = False

    def supports(self, capability: CapabilityName) -> bool:
        return {
            "mean_point": "mean" in self.point_statistics,
            "median_point": "median" in self.point_statistics,
            "quantiles": self.quantiles,
            "standard_deviation": self.standard_deviation,
            "predictive_samples": self.predictive_samples,
            "parametric_distribution": self.parametric_distribution,
            "goal_probability": self.goal_probability != "unavailable",
            "support": self.support,
            "explanation": self.explanation,
            # UCB/EI deliberately require this exact interpretation.  A median
            # or a q05/q95 band is not silently promoted to a normal mean/std.
            "normal_mean_std": self.target_kind == "continuous" and "mean" in self.point_statistics and self.standard_deviation and self.predictive_family == "normal",
            "joint_samples": False,
        }[capability]


class ModelPackageCapabilityMatrix(ContractModel):
    schema_version: Literal["model-package-capability-matrix/v1"] = "model-package-capability-matrix/v1"
    task_id: str
    package_id: str
    package_manifest_digest: str
    targets: tuple[TargetCapabilityMatrix, ...] = Field(min_length=1)
    joint_samples: bool = False

    @model_validator(mode="after")
    def unique_targets(self) -> "ModelPackageCapabilityMatrix":
        if len({item.target for item in self.targets}) != len(self.targets):
            raise ValueError("capability matrix targets must be unique")
        if self.joint_samples and any(not item.predictive_samples for item in self.targets):
            raise ValueError("joint samples require predictive samples for every target")
        return self

    def target(self, target: str) -> TargetCapabilityMatrix | None:
        return next((item for item in self.targets if item.target == target), None)


class CapabilityAvailability(ContractModel):
    available: bool
    reasons: tuple[str, ...] = ()

    @model_validator(mode="after")
    def reasons_match_availability(self) -> "CapabilityAvailability":
        if self.available == bool(self.reasons):
            raise ValueError("capability availabilityと理由が一致しません")
        return self


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
