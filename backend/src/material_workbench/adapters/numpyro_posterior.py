"""Pure NumPy inference for finite, exported NumPyro dense-posterior models.

The training model is never deserialized.  The package only supplies arrays for
the fixed dense_mlp_v1 forward pass and one of the finite likelihood ids below.
"""
from __future__ import annotations

import numpy as np

from ..model_packages import PackageContractError, PredictiveSummary, PredictorSpec, VerifiedModelPackage
from .base import feature_vector, quantile_summary, scalar_config
from .safe_npz import MAX_NPZ_COMPRESSION_RATIO, safe_npz_arrays


_KINDS = {
    "normal": "continuous", "student_t": "continuous", "lognormal": "continuous_positive",
    "bernoulli_logit": "binary", "poisson_log": "count", "negative_binomial_log": "count",
    "zero_inflated_poisson_log": "count", "ordinal_logit": "ordinal",
}
MAX_POSTERIOR_DRAWS = 4096
MAX_DENSE_LAYERS = 8
MAX_TENSOR_ELEMENTS = 4_000_000


def _sigmoid(value: np.ndarray) -> np.ndarray:
    clipped = np.clip(value, -50, 50)
    return 1.0 / (1.0 + np.exp(-clipped))


class _DensePosteriorPredictor:
    def __init__(self, spec: PredictorSpec, weights: tuple[np.ndarray, ...], biases: tuple[np.ndarray, ...], extras: dict[str, np.ndarray]) -> None:
        self.spec, self.weights, self.biases, self.extras = spec, weights, biases, extras
        self.draws = weights[0].shape[0]

    def _forward(self, vector: np.ndarray) -> np.ndarray:
        values = np.broadcast_to(vector, (self.draws, len(vector))).copy()
        activation = self.spec.config.get("activation", "tanh")
        for index, (weight, bias) in enumerate(zip(self.weights, self.biases)):
            values = np.einsum("di,dio->do", values, weight) + bias
            if index != len(self.weights) - 1:
                values = np.tanh(values) if activation == "tanh" else np.maximum(values, 0)
        return values

    def _scale(self, name: str, default: float) -> np.ndarray:
        if name in self.extras:
            value = np.asarray(self.extras[name], dtype=float).reshape(-1)
            if len(value) not in {1, self.draws}:
                raise PackageContractError(f"{name} must have one value or one value per posterior draw")
            return np.broadcast_to(value, (self.draws,))
        return np.full(self.draws, scalar_config(self.spec, name, default), dtype=float)

    def _continuous_samples(self, output: np.ndarray, seed: int) -> tuple[np.ndarray, str]:
        family = self.spec.predictive_family
        rng = np.random.default_rng(seed)
        location = output[:, 0]
        if family == "normal":
            return location + self._scale("obs_scale", 1.0) * rng.standard_normal(self.draws), family
        if family == "student_t":
            df = self._scale("df", 5.0)
            if (df <= 2).any():
                raise PackageContractError("student_t df must be greater than 2")
            draws = np.asarray([rng.standard_t(float(item)) for item in df])
            return location + self._scale("obs_scale", 1.0) * draws, family
        if family == "lognormal":
            return np.exp(np.clip(location + self._scale("obs_scale", 1.0) * rng.standard_normal(self.draws), -50, 50)), family
        raise PackageContractError(f"not a continuous likelihood: {family}")

    def predict(self, values: dict[str, float], *, seed: int = 0) -> PredictiveSummary:
        output = self._forward(feature_vector(self.spec, values))
        family = self.spec.predictive_family
        expected_kind = _KINDS.get(family)
        if expected_kind != self.spec.target_kind:
            raise PackageContractError(f"{family} requires target_kind={expected_kind}")
        if family in {"normal", "student_t", "lognormal"}:
            samples, family = self._continuous_samples(output, seed)
            quantiles = quantile_summary(samples)
            statistic = "median" if family == "lognormal" else "mean"
            point = float(np.median(samples) if statistic == "median" else np.mean(samples))
            return PredictiveSummary(target=self.spec.target, target_kind=self.spec.target_kind, unit=self.spec.unit, point_statistic=statistic, point_estimate=point, quantiles=quantiles, distribution={"family": family, "support": "positive" if family == "lognormal" else "real"})
        if family == "bernoulli_logit":
            probabilities = _sigmoid(output[:, 0])
            probability = float(np.mean(probabilities))
            return PredictiveSummary(target=self.spec.target, target_kind="binary", unit=self.spec.unit, point_statistic="probability", point_estimate=probability, quantiles=quantile_summary(probabilities), event_probability=probability, distribution={"family": family, "support": "{0,1}"})
        rng = np.random.default_rng(seed)
        if family == "poisson_log":
            rate = np.exp(np.clip(output[:, 0], -30, 30))
            samples = rng.poisson(rate)
            point, distribution = float(np.mean(rate)), {"family": family, "support": "nonnegative_integers"}
        elif family == "negative_binomial_log":
            mean = np.exp(np.clip(output[:, 0], -30, 30))
            dispersion = self._scale("dispersion", 5.0)
            if (dispersion <= 0).any():
                raise PackageContractError("negative binomial dispersion must be positive")
            samples = rng.poisson(rng.gamma(shape=dispersion, scale=mean / dispersion))
            point, distribution = float(np.mean(mean)), {"family": family, "support": "nonnegative_integers"}
        elif family == "zero_inflated_poisson_log":
            if output.shape[1] != 2:
                raise PackageContractError("zero_inflated_poisson_log requires a two-output dense network")
            rate, zero_probability = np.exp(np.clip(output[:, 0], -30, 30)), _sigmoid(output[:, 1])
            samples = rng.poisson(rate)
            samples[rng.random(self.draws) < zero_probability] = 0
            point, distribution = float(np.mean((1 - zero_probability) * rate)), {"family": family, "support": "nonnegative_integers"}
        elif family == "ordinal_logit":
            thresholds = self.spec.config.get("thresholds")
            if not isinstance(thresholds, list) or len(thresholds) < 1 or not all(isinstance(item, (int, float)) for item in thresholds):
                raise PackageContractError("ordinal_logit requires numeric ordered thresholds")
            cuts = np.asarray(thresholds, dtype=float)
            if not np.all(np.diff(cuts) > 0):
                raise PackageContractError("ordinal thresholds must be strictly increasing")
            categories = self.spec.config.get("categories")
            if not isinstance(categories, list) or len(categories) != len(cuts) + 1 or not all(isinstance(item, str) and item for item in categories):
                raise PackageContractError("ordinal_logit requires one ordered category label per outcome")
            if len(categories) != len(set(categories)):
                raise PackageContractError("ordinal category labels must be unique")
            cumulative = _sigmoid(cuts[None, :] - output[:, :1])
            probabilities = np.column_stack([cumulative[:, 0], np.diff(cumulative, axis=1), 1 - cumulative[:, -1]])
            probabilities = np.clip(probabilities, 0, 1)
            probabilities /= probabilities.sum(axis=1, keepdims=True)
            samples = np.asarray([rng.choice(probabilities.shape[1], p=row) for row in probabilities])
            point, distribution = float(np.mean(np.arange(probabilities.shape[1]) * probabilities.mean(axis=0))), {"family": family, "support": "ordered_categories", "categories": categories}
        else:
            raise PackageContractError(f"unsupported NumPyro likelihood: {family}")
        return PredictiveSummary(target=self.spec.target, target_kind=self.spec.target_kind, unit=self.spec.unit, point_statistic="expected_category" if family == "ordinal_logit" else "rate", point_estimate=point, quantiles=quantile_summary(samples, discrete=True), distribution=distribution)


class NumpyroDensePosteriorAdapter:
    runtime_type = "numpyro.dense_posterior.v1"

    def load(self, package: VerifiedModelPackage, predictor: PredictorSpec) -> _DensePosteriorPredictor:
        if predictor.architecture_id != "dense_mlp_v1" or predictor.predictive_family not in _KINDS:
            raise PackageContractError("unsupported NumPyro posterior architecture or likelihood")
        activation = predictor.config.get("activation", "tanh")
        if activation not in {"tanh", "relu"}:
            raise PackageContractError("dense_mlp_v1 activation must be tanh or relu")
        arrays = safe_npz_arrays(
            package.artifact_path(predictor.artifact),
            max_entries=32,
            max_tensor_elements=MAX_TENSOR_ELEMENTS,
        )
        keys = set(arrays)
        try:
            layer_indexes = sorted(int(key[1:]) for key in keys if key.startswith("w") and key[1:].isdigit())
            if layer_indexes != list(range(len(layer_indexes))) or not layer_indexes or len(layer_indexes) > MAX_DENSE_LAYERS:
                raise PackageContractError("posterior artifact must contain contiguous w0..wN tensors")
            weights = tuple(np.asarray(arrays[f"w{index}"], dtype=float) for index in layer_indexes)
            biases = tuple(np.asarray(arrays[f"b{index}"], dtype=float) for index in layer_indexes)
            allowed = {*(f"w{index}" for index in layer_indexes), *(f"b{index}" for index in layer_indexes), "obs_scale", "df", "dispersion"}
            if not keys.issubset(allowed) or any(f"b{index}" not in keys for index in layer_indexes):
                raise PackageContractError("posterior artifact has an unexpected tensor schema")
            extras = {name: np.asarray(arrays[name], dtype=float) for name in ("obs_scale", "df", "dispersion") if name in keys}
        except KeyError as exc:
            raise PackageContractError("posterior artifact is missing a required layer bias") from exc
        draws = weights[0].shape[0] if weights[0].ndim == 3 else 0
        if draws < 2 or draws > MAX_POSTERIOR_DRAWS or weights[0].shape[1] != len(predictor.feature_names):
            raise PackageContractError("posterior draw count or input width is invalid")
        for index, (weight, bias) in enumerate(zip(weights, biases)):
            if weight.ndim != 3 or bias.shape != (draws, weight.shape[2]) or weight.shape[0] != draws:
                raise PackageContractError("posterior layer tensors have incompatible shapes")
            if index and weight.shape[1] != weights[index - 1].shape[2]:
                raise PackageContractError("posterior layer widths are incompatible")
            if not np.isfinite(weight).all() or not np.isfinite(bias).all():
                raise PackageContractError("posterior tensors must be finite")
        output_width = weights[-1].shape[2]
        expected_width = 2 if predictor.predictive_family == "zero_inflated_poisson_log" else 1
        if output_width != expected_width:
            raise PackageContractError(f"{predictor.predictive_family} requires output width {expected_width}")
        for name in ("obs_scale", "dispersion"):
            if name in extras and np.any(extras[name] <= 0):
                raise PackageContractError(f"{name} must be positive")
        if "df" in extras and np.any(extras["df"] <= 2):
            raise PackageContractError("student_t df must be greater than 2")
        if predictor.predictive_family == "ordinal_logit":
            thresholds = predictor.config.get("thresholds")
            categories = predictor.config.get("categories")
            if not isinstance(thresholds, list) or not all(isinstance(item, (int, float)) and np.isfinite(item) for item in thresholds) or not np.all(np.diff(thresholds) > 0):
                raise PackageContractError("ordinal thresholds must be finite and strictly increasing")
            if not isinstance(categories, list) or len(categories) != len(thresholds) + 1 or len(categories) != len(set(categories)) or not all(isinstance(item, str) and item for item in categories):
                raise PackageContractError("ordinal category metadata is invalid")
        return _DensePosteriorPredictor(predictor, weights, biases, extras)
