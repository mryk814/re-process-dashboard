from __future__ import annotations

from typing import Any

from decision_workbench.contracts.sampling_identity_contracts import SamplingRequest
from decision_workbench.modeling.packages.contracts import (
    PredictiveSummary,
    PredictorSpec,
)


SAMPLE_BASED_RUNTIME_TYPES = frozenset({"numpyro.dense_posterior.v1"})


class SamplingIdentityUnsupportedError(ValueError):
    pass


def runtime_uses_sampling(runtime: object) -> bool:
    model_package = getattr(runtime, "model_package", None)
    manifest = getattr(model_package, "manifest", None)
    predictors = getattr(manifest, "predictors", ())
    if any(
        getattr(predictor, "runtime_type", None) in SAMPLE_BASED_RUNTIME_TYPES
        for predictor in predictors
    ):
        return True
    return False


def sampling_request_for_operation(
    runtime: object,
    operation: str,
    *,
    seed: int | None = None,
) -> SamplingRequest | None:
    if not runtime_uses_sampling(runtime):
        return None
    if not getattr(runtime, "supports_effective_sampling_identity", False):
        raise SamplingIdentityUnsupportedError(
            f"{type(runtime).__name__}はsample-based predictionの"
            f"{operation} Sampling Identityを保存できません"
        )
    return SamplingRequest.for_operation(operation, seed=seed)  # type: ignore[arg-type]


def package_verification_sampling_request(
    predictor: Any,
    spec: PredictorSpec,
    *,
    seed: int,
) -> SamplingRequest | None:
    if spec.runtime_type not in SAMPLE_BASED_RUNTIME_TYPES:
        return None
    posterior_draw_count = getattr(predictor, "draws", None)
    if not isinstance(posterior_draw_count, int):
        raise SamplingIdentityUnsupportedError(
            "sample-based predictor does not expose its verified posterior draw count"
        )
    return SamplingRequest.for_package_verification(
        seed=seed,
        posterior_draw_count=posterior_draw_count,
    )


def predict_with_sampling_identity(
    predictor: Any,
    spec: PredictorSpec,
    values: dict[str, float],
    request: SamplingRequest | None,
    *,
    seed: int = 0,
) -> PredictiveSummary:
    if spec.runtime_type == "numpyro.dense_posterior.v1":
        if request is None:
            raise SamplingIdentityUnsupportedError(
                "sample-based predictor requires an explicit versioned SamplingRequest"
            )
        return predictor.predict(
            values,
            sampling_request=request,
        )
    return predictor.predict(values, seed=seed)
