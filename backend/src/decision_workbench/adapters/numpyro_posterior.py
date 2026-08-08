"""Pure NumPy inference for finite, exported NumPyro dense-posterior models.

The training model is never deserialized.  The package only supplies arrays for
the fixed dense_mlp_v1 forward pass and one of the finite likelihood ids below.
"""
from __future__ import annotations

import hashlib
import json

import numpy as np

from decision_workbench.contracts.sampling_identity_contracts import (
    SamplingIdentity,
    SamplingRequest,
)
from decision_workbench.modeling.packages.contracts import (
    PackageContractError,
    PredictionInterval,
    PredictiveSummary,
    PredictorSpec,
)
from decision_workbench.modeling.packages.ports import VerifiedPackageArtifacts
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


def _category_order_digest(categories: list[str]) -> str:
    encoded = json.dumps(
        categories,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _bayesian_interval(quantiles: dict[str, float]) -> PredictionInterval:
    return PredictionInterval(
        method="bayesian",
        coverage_level=0.9,
        lower=quantiles["0.05"],
        upper=quantiles["0.95"],
    )


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

    def _scale(
        self, name: str, default: float, draw_indices: np.ndarray
    ) -> np.ndarray:
        if name in self.extras:
            value = np.asarray(self.extras[name], dtype=float).reshape(-1)
            if len(value) not in {1, self.draws}:
                raise PackageContractError(f"{name} must have one value or one value per posterior draw")
            return np.broadcast_to(value, (self.draws,))[draw_indices]
        return np.full(len(draw_indices), scalar_config(self.spec, name, default), dtype=float)

    def _continuous_samples(
        self,
        output: np.ndarray,
        rng: np.random.Generator,
        draw_indices: np.ndarray,
    ) -> tuple[np.ndarray, str]:
        family = self.spec.predictive_family
        location = output[:, 0]
        if family == "normal":
            return location + self._scale("obs_scale", 1.0, draw_indices) * rng.standard_normal(len(draw_indices)), family
        if family == "student_t":
            df = self._scale("df", 5.0, draw_indices)
            if (df <= 2).any():
                raise PackageContractError("student_t df must be greater than 2")
            draws = np.asarray([rng.standard_t(float(item)) for item in df])
            return location + self._scale("obs_scale", 1.0, draw_indices) * draws, family
        if family == "lognormal":
            return np.exp(np.clip(location + self._scale("obs_scale", 1.0, draw_indices) * rng.standard_normal(len(draw_indices)), -50, 50)), family
        raise PackageContractError(f"not a continuous likelihood: {family}")

    def predict(
        self,
        values: dict[str, float],
        *,
        sampling_request: SamplingRequest,
    ) -> PredictiveSummary:
        if sampling_request.method_id != "numpyro-posterior-predictive":
            raise PackageContractError("sampling request method does not match NumPyro")
        seed = sampling_request.seed
        requested_sample_count = sampling_request.requested_sample_count
        assert requested_sample_count is not None
        if requested_sample_count < 2 or requested_sample_count > MAX_POSTERIOR_DRAWS:
            raise PackageContractError(
                f"sample_count must be between 2 and {MAX_POSTERIOR_DRAWS}"
            )
        rng = np.random.default_rng(seed)
        if requested_sample_count == self.draws:
            draw_indices = np.arange(self.draws)
            draw_selection_policy = "all_posterior_draws"
        elif requested_sample_count < self.draws:
            draw_indices = rng.choice(
                self.draws, size=requested_sample_count, replace=False
            )
            draw_selection_policy = "seeded_without_replacement"
        else:
            draw_indices = rng.choice(
                self.draws, size=requested_sample_count, replace=True
            )
            draw_selection_policy = "seeded_with_replacement"
        output = self._forward(feature_vector(self.spec, values))[draw_indices]
        family = self.spec.predictive_family
        expected_kind = _KINDS.get(family)
        if expected_kind != self.spec.target_kind:
            raise PackageContractError(f"{family} requires target_kind={expected_kind}")
        exposure = self.spec.config.get("exposure")
        advanced_contract = self.spec.config.get("advanced_count_contract")
        if advanced_contract is not None and advanced_contract != "advanced-count-contract/v1":
            raise PackageContractError("advanced count contract marker is unsupported")
        if advanced_contract == "advanced-count-contract/v1" and exposure is None:
            raise PackageContractError("advanced count posterior requires explicit exposure semantics")
        if family in {"negative_binomial_log", "zero_inflated_poisson_log"} and exposure is not None:
            if not isinstance(exposure, dict):
                raise PackageContractError("advanced count exposure semantics must be an object")
            mode = exposure.get("mode")
            if mode == "explicit_offset/v1":
                path = exposure.get("input_path")
                if not isinstance(path, str) or path not in values:
                    raise PackageContractError("advanced count exposure input is required")
                value = float(values[path])
                if not np.isfinite(value) or value <= 0:
                    raise PackageContractError("advanced count exposure must be finite and positive")
                output[:, 0] += np.log(value)
            elif mode != "not_applicable_unexposed_count/v1":
                raise PackageContractError("advanced count exposure semantics are unsupported")
        sampling_identity = SamplingIdentity.create(
            request=sampling_request,
            requested_sample_count=requested_sample_count,
            effective_sample_count=len(draw_indices),
            posterior_draw_count=self.draws,
            draw_selection_policy=draw_selection_policy,
            family=family,
            model_inference_policy_id=(
                self.spec.inference_identity.algorithm_id
                if self.spec.inference_identity is not None
                else None
            ),
            model_inference_identity_digest=(
                self.spec.inference_identity.identity_digest
                if self.spec.inference_identity is not None
                else None
            ),
        )
        if family in {"normal", "student_t", "lognormal"}:
            samples, family = self._continuous_samples(output, rng, draw_indices)
            quantiles = quantile_summary(samples)
            statistic = "median" if family == "lognormal" else "mean"
            mixture_moments = (
                family == "student_t"
                and self.spec.config.get("predictive_moment_semantics")
                == "posterior-mixture-location-mean-total-std/v1"
            )
            point = float(
                np.median(samples)
                if statistic == "median"
                else (
                    np.mean(output[:, 0])
                    if mixture_moments
                    else np.mean(samples)
                )
            )
            distribution: dict[str, object] = {
                "family": family,
                "support": "positive" if family == "lognormal" else "real",
            }
            if mixture_moments:
                location = output[:, 0]
                scale = self._scale("obs_scale", 1.0, draw_indices)
                degrees_of_freedom = self._scale("df", 5.0, draw_indices)
                component_variance = (
                    scale**2
                    * degrees_of_freedom
                    / (degrees_of_freedom - 2.0)
                )
                predictive_variance = float(
                    np.mean(component_variance + location**2)
                    - np.mean(location) ** 2
                )
                distribution["std"] = float(
                    np.sqrt(max(0.0, predictive_variance))
                )
            return PredictiveSummary(target=self.spec.target, target_kind=self.spec.target_kind, unit=self.spec.unit, point_statistic=statistic, point_estimate=point, quantiles=quantiles, distribution=distribution, prediction_interval=_bayesian_interval(quantiles), sampling_identity=sampling_identity)
        if family == "bernoulli_logit":
            probabilities = _sigmoid(output[:, 0])
            probability = float(np.mean(probabilities))
            quantiles = quantile_summary(probabilities)
            return PredictiveSummary(target=self.spec.target, target_kind="binary", unit=self.spec.unit, point_statistic="probability", point_estimate=probability, quantiles=quantiles, event_probability=probability, distribution={"family": family, "support": "{0,1}"}, prediction_interval=_bayesian_interval(quantiles), sampling_identity=sampling_identity)
        if family == "poisson_log":
            rate = np.exp(np.clip(output[:, 0], -30, 30))
            samples = rng.poisson(rate)
            point, distribution = float(np.mean(rate)), {"family": family, "support": "nonnegative_integers"}
        elif family == "negative_binomial_log":
            mean = np.exp(np.clip(output[:, 0], -30, 30))
            dispersion = self._scale("dispersion", 5.0, draw_indices)
            if (dispersion <= 0).any():
                raise PackageContractError("negative binomial dispersion must be positive")
            samples = rng.poisson(rng.gamma(shape=dispersion, scale=mean / dispersion))
            point, distribution = float(np.mean(mean)), {
                "family": family, "support": "nonnegative_integers"
            }
            if exposure is not None:
                distribution.update(
                    mean_semantics="expected_count",
                    overdispersion=float(np.mean(dispersion)),
                )
        elif family == "zero_inflated_poisson_log":
            if output.shape[1] != 2:
                raise PackageContractError("zero_inflated_poisson_log requires a two-output dense network")
            rate, zero_probability = np.exp(np.clip(output[:, 0], -30, 30)), _sigmoid(output[:, 1])
            samples = rng.poisson(rate)
            samples[rng.random(len(draw_indices)) < zero_probability] = 0
            point, distribution = float(np.mean((1 - zero_probability) * rate)), {
                "family": family, "support": "nonnegative_integers"
            }
            if exposure is not None:
                distribution.update(
                    mean_semantics="expected_count",
                    zero_probability=float(np.mean(zero_probability)),
                    count_process_mean=float(np.mean(rate)),
                )
        elif family == "ordinal_logit":
            if "ordinal_thresholds" in self.extras:
                cuts = self.extras["ordinal_thresholds"][draw_indices]
            else:
                thresholds = self.spec.config.get("thresholds")
                if not isinstance(thresholds, list) or len(thresholds) < 1 or not all(isinstance(item, (int, float)) for item in thresholds):
                    raise PackageContractError("ordinal_logit requires numeric ordered thresholds")
                cuts = np.broadcast_to(np.asarray(thresholds, dtype=float), (len(draw_indices), len(thresholds)))
            if not np.all(np.diff(cuts, axis=1) > 0):
                raise PackageContractError("ordinal thresholds must be strictly increasing")
            categories = self.spec.config.get("categories")
            if not isinstance(categories, list) or len(categories) != cuts.shape[1] + 1 or not all(isinstance(item, str) and item for item in categories):
                raise PackageContractError("ordinal_logit requires one ordered category label per outcome")
            if len(categories) != len(set(categories)):
                raise PackageContractError("ordinal category labels must be unique")
            cumulative = _sigmoid(cuts - output[:, :1])
            probabilities = np.column_stack([cumulative[:, 0], np.diff(cumulative, axis=1), 1 - cumulative[:, -1]])
            probabilities = np.clip(probabilities, 0, 1)
            probabilities /= probabilities.sum(axis=1, keepdims=True)
            samples = np.asarray([rng.choice(probabilities.shape[1], p=row) for row in probabilities])
            mean_probabilities = probabilities.mean(axis=0)
            point, distribution = float(
                np.dot(np.arange(probabilities.shape[1]), mean_probabilities)
            ), {
                "family": family,
                "support": "ordered_categories",
                "categories": categories,
                "probabilities": mean_probabilities.tolist(),
            }
        else:
            raise PackageContractError(f"unsupported NumPyro likelihood: {family}")
        quantiles = quantile_summary(samples, discrete=True)
        return PredictiveSummary(target=self.spec.target, target_kind=self.spec.target_kind, unit=self.spec.unit, point_statistic="expected_category" if family == "ordinal_logit" else "rate", point_estimate=point, quantiles=quantiles, distribution=distribution, prediction_interval=_bayesian_interval(quantiles), sampling_identity=sampling_identity)


class NumpyroDensePosteriorAdapter:
    runtime_type = "numpyro.dense_posterior.v1"

    def load(self, package: VerifiedPackageArtifacts, predictor: PredictorSpec) -> _DensePosteriorPredictor:
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
            allowed = {*(f"w{index}" for index in layer_indexes), *(f"b{index}" for index in layer_indexes), "obs_scale", "df", "dispersion", "ordinal_thresholds"}
            if not keys.issubset(allowed) or any(f"b{index}" not in keys for index in layer_indexes):
                raise PackageContractError("posterior artifact has an unexpected tensor schema")
            extras = {name: np.asarray(arrays[name], dtype=float) for name in ("obs_scale", "df", "dispersion", "ordinal_thresholds") if name in keys}
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
            if name in extras and not np.isfinite(extras[name]).all():
                raise PackageContractError(f"{name} must be finite")
            if name in extras and np.any(extras[name] <= 0):
                raise PackageContractError(f"{name} must be positive")
        if "df" in extras:
            if not np.isfinite(extras["df"]).all():
                raise PackageContractError("student_t df must be finite")
            if np.any(extras["df"] <= 2):
                raise PackageContractError("student_t df must be greater than 2")
        if predictor.predictive_family == "ordinal_logit":
            thresholds = predictor.config.get("thresholds")
            categories = predictor.config.get("categories")
            posterior_thresholds = extras.get("ordinal_thresholds")
            if posterior_thresholds is not None:
                if posterior_thresholds.ndim != 2 or posterior_thresholds.shape[0] != draws or posterior_thresholds.shape[1] < 1 or not np.isfinite(posterior_thresholds).all() or not np.all(np.diff(posterior_thresholds, axis=1) > 0):
                    raise PackageContractError("ordinal threshold draws must be finite, ordered, and aligned to posterior draws")
                threshold_count = posterior_thresholds.shape[1]
                category_digest = predictor.config.get("category_order_digest")
                if not isinstance(category_digest, str):
                    raise PackageContractError(
                        "posterior ordinal thresholds require category_order_digest"
                    )
            else:
                if not isinstance(thresholds, list) or len(thresholds) < 1 or not all(isinstance(item, (int, float)) and np.isfinite(item) for item in thresholds) or not np.all(np.diff(thresholds) > 0):
                    raise PackageContractError("ordinal thresholds must be finite and strictly increasing")
                threshold_count = len(thresholds)
            if not isinstance(categories, list) or len(categories) != threshold_count + 1 or len(categories) != len(set(categories)) or not all(isinstance(item, str) and item for item in categories):
                raise PackageContractError("ordinal category metadata is invalid")
            category_digest = predictor.config.get("category_order_digest")
            if category_digest is not None and category_digest != _category_order_digest(categories):
                raise PackageContractError(
                    "ordinal category_order_digest does not match categories"
                )
        return _DensePosteriorPredictor(predictor, weights, biases, extras)
