from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ModelCapabilityContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


CapabilityName = Literal[
    "mean_point",
    "median_point",
    "quantiles",
    "standard_deviation",
    "predictive_samples",
    "joint_samples",
    "parametric_distribution",
    "goal_probability",
    "support",
    "explanation",
    "normal_mean_std",
]


class CapabilityRequirement(ModelCapabilityContract):
    capability: CapabilityName
    alternative: str | None = None


class TargetCapabilityMatrix(ModelCapabilityContract):
    target: str
    target_kind: Literal[
        "continuous",
        "continuous_positive",
        "binary",
        "count",
        "ordinal",
    ]
    predictive_family: str
    point_statistics: tuple[
        Literal["mean", "median", "probability", "rate", "expected_category"],
        ...,
    ]
    quantiles: bool
    standard_deviation: bool
    predictive_samples: bool
    parametric_distribution: bool
    uncertainty_components: bool
    support: bool
    warnings: bool
    goal_probability: Literal[
        "native",
        "samples",
        "distribution",
        "normal_approximation",
        "unavailable",
    ]
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
            "normal_mean_std": (
                self.target_kind == "continuous"
                and "mean" in self.point_statistics
                and self.standard_deviation
                and self.predictive_family == "normal"
            ),
            "joint_samples": False,
        }[capability]


class ModelPackageCapabilityMatrix(ModelCapabilityContract):
    schema_version: Literal["model-package-capability-matrix/v1"] = (
        "model-package-capability-matrix/v1"
    )
    task_id: str
    package_id: str
    package_manifest_digest: str
    targets: tuple[TargetCapabilityMatrix, ...] = Field(min_length=1)
    joint_samples: bool = False

    @model_validator(mode="after")
    def unique_targets(self) -> "ModelPackageCapabilityMatrix":
        if len({item.target for item in self.targets}) != len(self.targets):
            raise ValueError("capability matrix targets must be unique")
        if self.joint_samples and any(
            not item.predictive_samples for item in self.targets
        ):
            raise ValueError(
                "joint samples require predictive samples for every target"
            )
        return self

    def target(self, target: str) -> TargetCapabilityMatrix | None:
        return next((item for item in self.targets if item.target == target), None)


class CapabilityAvailability(ModelCapabilityContract):
    available: bool
    reasons: tuple[str, ...] = ()

    @model_validator(mode="after")
    def reasons_match_availability(self) -> "CapabilityAvailability":
        if self.available == bool(self.reasons):
            raise ValueError("capability availabilityと理由が一致しません")
        return self
