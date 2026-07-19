from __future__ import annotations

from typing import Any

import numpy as np

from ..model_packages import PackageContractError, PredictorSpec


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


def scalar_config(spec: PredictorSpec, name: str, default: float | None = None) -> float:
    value: Any = spec.config.get(name, default)
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not np.isfinite(float(value)):
        raise PackageContractError(f"{name} must be a finite numeric adapter config")
    return float(value)
