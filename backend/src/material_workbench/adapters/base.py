from __future__ import annotations

import math
from typing import Any

import numpy as np

from ..model_packages import PackageContractError, PredictiveSummary, PredictorSpec

Z90 = 1.6448536269514722


def feature_vector(spec: PredictorSpec, values: dict[str, float]) -> np.ndarray:
    missing = [name for name in spec.feature_names if name not in values]
    if missing:
        raise PackageContractError(f"missing model features: {', '.join(missing)}")
    vector = np.asarray([values[name] for name in spec.feature_names], dtype=float)
    if not np.isfinite(vector).all():
        raise PackageContractError("model features must be finite")
    return vector


def quantile_summary(samples: np.ndarray) -> dict[str, float]:
    values = np.asarray(samples, dtype=float).reshape(-1)
    if not len(values) or not np.isfinite(values).all():
        raise PackageContractError("predictive samples must be finite and nonempty")
    return {"0.05": float(np.quantile(values, 0.05)), "0.50": float(np.quantile(values, 0.50)), "0.95": float(np.quantile(values, 0.95))}


def normal_predictive_summary(
    spec: PredictorSpec,
    estimate: float,
    model_variance: float,
    observation_variance: float,
) -> PredictiveSummary:
    """Normal predictive contract shared by the exact GP adapters."""
    predictive_variance = model_variance + observation_variance
    predictive_std = math.sqrt(predictive_variance)
    return PredictiveSummary(
        target=spec.target,
        target_kind=spec.target_kind,
        unit=spec.unit,
        point_statistic="mean",
        point_estimate=estimate,
        quantiles={
            "0.05": estimate - Z90 * predictive_std,
            "0.50": estimate,
            "0.95": estimate + Z90 * predictive_std,
        },
        distribution={"family": "normal", "support": "real", "mean": estimate, "std": predictive_std},
        uncertainty_components={
            "latent_model_variance": model_variance,
            "latent_model_std": math.sqrt(model_variance),
            "observation_noise_variance": observation_variance,
            "observation_noise_std": math.sqrt(observation_variance),
            "total_predictive_variance": predictive_variance,
            "total_predictive_std": predictive_std,
        },
    )


def scalar_config(spec: PredictorSpec, name: str, default: float | None = None) -> float:
    value: Any = spec.config.get(name, default)
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not np.isfinite(float(value)):
        raise PackageContractError(f"{name} must be a finite numeric adapter config")
    return float(value)
