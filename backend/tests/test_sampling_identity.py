from __future__ import annotations

from types import SimpleNamespace

import pytest

from decision_workbench.application.chain.stage_execution import ChainStageExecutor
from decision_workbench.contracts.evidence_contracts import SnapshotPayload
from decision_workbench.contracts.chain_execution_contracts import (
    PredictionGraphStageExecution,
)
from decision_workbench.contracts.decision_activity_contracts import (
    CounterfactualTargetEvaluation,
)
from decision_workbench.contracts.prediction_catalog_contracts import Prediction
from decision_workbench.contracts.sampling_identity_contracts import (
    SamplingIdentity,
    SamplingRequest,
)
from decision_workbench.execution.inference_work_graph import (
    InferenceKey,
    semantic_digest,
)
from decision_workbench.modeling.packages.contracts import (
    PredictiveSummary,
    PredictorSpec,
)
from decision_workbench.modeling.sampling_identity import (
    SamplingIdentityUnsupportedError,
    predict_with_sampling_identity,
    sampling_request_for_operation,
)


def _prediction_payload() -> dict[str, object]:
    return {
        "task_id": "task",
        "candidate_id": "candidate",
        "mode": "detailed",
        "predictions": {
            "y": {
                "value": 1.0,
                "lower": 0.5,
                "upper": 1.5,
                "unit": "1",
                "target_kind": "continuous",
                "point_statistic": "mean",
                "predictive_family": "normal",
                "quantiles": {"0.05": 0.5, "0.95": 1.5},
            }
        },
        "support": {
            "status": "supported",
            "distance": 0.1,
            "percentile": 0.2,
            "message": "",
            "components": {},
            "reference_count": 4,
            "supported_threshold": 1.0,
            "caution_threshold": 2.0,
        },
        "warnings": [],
        "model_meta": {
            "package": {
                "id": "posterior-package",
                "version": "1",
                "manifest_sha256": "a" * 64,
                "runtime_types": ["numpyro.dense_posterior.v1"],
            }
        },
        "canonical_input": {},
        "similar": [],
        "heat_pattern": [],
    }


def test_legacy_sample_based_snapshot_is_projected_as_unavailable() -> None:
    payload = SnapshotPayload.model_validate(
        {
            "prediction": _prediction_payload(),
            "provenance": {
                "package": {
                    "id": "posterior-package",
                    "version": "1",
                    "manifest_sha256": "a" * 64,
                    "runtime_types": ["numpyro.dense_posterior.v1"],
                }
            },
        }
    )

    identity = payload.prediction.predictions["y"].sampling_identity
    assert identity is not None
    assert identity.schema_version == "sampling-identity/unavailable-legacy"


def test_mixed_runtime_legacy_snapshot_does_not_mark_deterministic_target() -> None:
    prediction = _prediction_payload()
    prediction["predictions"]["z"] = {
        **prediction["predictions"]["y"],
        "value": 2.0,
        "lower": 1.5,
        "upper": 2.5,
    }
    prediction["model_meta"]["package"]["runtime_types"] = [
        "builtin.linear.v1",
        "numpyro.dense_posterior.v1",
    ]
    prediction["model_meta"]["package"]["predictor_runtime_types"] = {
        "y": "numpyro.dense_posterior.v1",
        "z": "builtin.linear.v1",
    }
    payload = SnapshotPayload.model_validate(
        {
            "prediction": prediction,
            "provenance": prediction["model_meta"],
        }
    )

    assert payload.prediction.predictions["y"].sampling_identity is not None
    assert payload.prediction.predictions["z"].sampling_identity is None
    assert payload.sampling_identity_status is None


def test_mixed_runtime_legacy_without_target_authority_is_snapshot_unknown() -> None:
    prediction = _prediction_payload()
    prediction["model_meta"]["package"]["runtime_types"] = [
        "builtin.linear.v1",
        "numpyro.dense_posterior.v1",
    ]
    payload = SnapshotPayload.model_validate(
        {
            "prediction": prediction,
            "provenance": prediction["model_meta"],
        }
    )

    assert payload.prediction.predictions["y"].sampling_identity is None
    assert payload.sampling_identity_status is not None
    assert (
        payload.sampling_identity_status.schema_version
        == "sampling-identity/unavailable-legacy"
    )


def test_deterministic_predictor_does_not_receive_or_claim_sampling_identity() -> None:
    class DeterministicPredictor:
        def predict(
            self,
            values: dict[str, float],
            *,
            seed: int = 0,
        ) -> PredictiveSummary:
            assert seed == 17
            return PredictiveSummary(
                target="y",
                target_kind="continuous",
                unit="1",
                point_statistic="mean",
                point_estimate=values["x"],
                distribution={"family": "normal"},
            )

    spec = PredictorSpec(
        id="ridge",
        target="y",
        unit="1",
        target_kind="continuous",
        runtime_type="builtin.linear.v1",
        architecture_id="ridge_v1",
        artifact="model.npz",
        predictive_family="normal",
        feature_names=("x",),
    )

    summary = predict_with_sampling_identity(
        DeterministicPredictor(),
        spec,
        {"x": 2.0},
        SamplingRequest.create(
            operation="detailed_prediction",
            policy_id="test-deterministic-ignored/v1",
            seed=99,
            requested_sample_count=16,
        ),
        seed=17,
    )

    assert summary.point_estimate == 2.0
    assert summary.sampling_identity is None


def test_sampling_request_parameters_are_part_of_cache_identity() -> None:
    def key(request: SamplingRequest) -> InferenceKey:
        return InferenceKey.build(
            task_id="task",
            runtime_type="runtime",
            canonical_input={"process": {"x": 1.0}},
            package_digest="package",
            pipeline_digest="pipeline",
            support_digest=None,
            operation="detailed",
            operation_parameters={
                "sampling_request": request.model_dump(mode="json")
            },
        )

    def request(seed: int, sample_count: int) -> SamplingRequest:
        return SamplingRequest.create(
            operation="detailed_prediction",
            policy_id="test-cache-identity/v1",
            seed=seed,
            requested_sample_count=sample_count,
        )

    baseline = key(request(17, 32))

    assert baseline == key(request(17, 32))
    assert baseline != key(request(23, 32))
    assert baseline != key(request(17, 64))


def test_sample_based_runtime_requires_supported_operation_policy() -> None:
    class Predictor:
        runtime_type = "numpyro.dense_posterior.v1"

    class Manifest:
        predictors = (Predictor(),)

    class Package:
        manifest = Manifest()

    class UnsupportedRuntime:
        model_package = Package()

    with pytest.raises(SamplingIdentityUnsupportedError, match="保存できません"):
        sampling_request_for_operation(
            UnsupportedRuntime(), "screening_proposal", seed=17
        )

    class SupportedRuntime(UnsupportedRuntime):
        supports_effective_sampling_identity = True

    request = sampling_request_for_operation(
        SupportedRuntime(), "screening_proposal", seed=17
    )
    assert request is not None
    assert request.method_version == "1.0.0"
    assert request.requested_sample_count == 256
    assert request.policy_digest.startswith("sha256:")

    spec = PredictorSpec(
        id="posterior",
        target="y",
        unit="1",
        target_kind="continuous",
        runtime_type="numpyro.dense_posterior.v1",
        architecture_id="dense_mlp_v1",
        artifact="posterior.npz",
        predictive_family="normal",
        feature_names=("x",),
    )
    with pytest.raises(
        SamplingIdentityUnsupportedError, match="explicit versioned"
    ):
        predict_with_sampling_identity(object(), spec, {"x": 1.0}, None)


def test_graph_stage_input_digest_includes_operation_sampling_policy() -> None:
    class Predictor:
        runtime_type = "numpyro.dense_posterior.v1"

    class Manifest:
        predictors = (Predictor(),)

    class Package:
        manifest = Manifest()

    class Runtime:
        model_package = Package()
        supports_effective_sampling_identity = True

    runtime = Runtime()

    class Registry:
        @staticmethod
        def entry_for(_contract_id: str):
            return SimpleNamespace(predictor_runtime=runtime)

    stage = SimpleNamespace(
        stage_kind="prediction_task",
        contract_id="task",
    )
    executor = ChainStageExecutor(Registry(), None)
    canonical = {"process": {"x": 1.0}}
    request = sampling_request_for_operation(
        runtime, "prediction_graph_stage"
    )

    assert executor.input_digest(stage, canonical) == semantic_digest(
        {
            "canonical_input": canonical,
            "sampling_request": request.model_dump(mode="json"),
        }
    )


def test_graph_stage_input_digest_preserves_legacy_digest_without_sampling() -> None:
    stage = SimpleNamespace(
        stage_kind="deterministic_transform",
        contract_id="transform",
    )
    executor = ChainStageExecutor(None, None)
    canonical = {"process": {"x": 1.0}}

    assert executor.input_digest(stage, canonical) == semantic_digest(canonical)


def test_sampling_identity_rejects_a_forged_parameter_digest() -> None:
    request = SamplingRequest.create(
        operation="detailed_prediction",
        policy_id="test-identity/v1",
        seed=17,
        requested_sample_count=8,
    )
    identity = SamplingIdentity.create(
        request=request,
        requested_sample_count=8,
        effective_sample_count=8,
        posterior_draw_count=12,
        draw_selection_policy="seeded_without_replacement",
        family="normal",
    )

    try:
        SamplingIdentity.model_validate(
            {
                **identity.model_dump(mode="json"),
                "parameter_digest": "sha256:" + "0" * 64,
            }
        )
    except ValueError as exc:
        assert "parameter digest" in str(exc)
    else:
        raise AssertionError("forged Sampling Identity digest was accepted")


def test_sampling_identity_rejects_count_that_conflicts_with_request() -> None:
    request = SamplingRequest.create(
        operation="detailed_prediction",
        policy_id="test-identity/v1",
        seed=17,
        requested_sample_count=8,
    )

    with pytest.raises(ValueError, match="must match the Sampling Request"):
        SamplingIdentity.create(
            request=request,
            requested_sample_count=7,
            effective_sample_count=7,
            posterior_draw_count=12,
            draw_selection_policy="seeded_without_replacement",
            family="normal",
        )


def test_activity_and_graph_stage_preserve_canonical_prediction_sampling() -> None:
    request = SamplingRequest.create(
        operation="decision_activity",
        policy_id="test-activity/v1",
        seed=17,
        requested_sample_count=8,
    )
    identity = SamplingIdentity.create(
        request=request,
        requested_sample_count=8,
        effective_sample_count=8,
        posterior_draw_count=12,
        draw_selection_policy="seeded_without_replacement",
        family="normal",
    )
    prediction = Prediction(
        value=1.0,
        lower=0.5,
        upper=1.5,
        unit="1",
        target_kind="continuous",
        point_statistic="mean",
        predictive_family="normal",
        quantiles={"0.05": 0.5, "0.95": 1.5},
        sampling_identity=identity,
    )
    activity_evidence = CounterfactualTargetEvaluation(
        target="y",
        unit="1",
        predicted_value=1.0,
        prediction=prediction,
        achieved=True,
        normalized_shortfall=0.0,
        shortfall=0.0,
        role="primary_objective",
    )
    digest = "sha256:" + "a" * 64
    graph_stage = PredictionGraphStageExecution(
        stage_id="posterior-stage",
        status="latest",
        requested_input_digest=digest,
        result_input_digest=digest,
        contract_digest="sha256:" + "b" * 64,
        package_manifest_digest="sha256:" + "c" * 64,
        canonical_input={"x": 1.0},
        result={
            "predictions": {
                "y": prediction.model_dump(mode="json"),
            }
        },
    )

    assert (
        activity_evidence.prediction.sampling_identity.parameter_digest
        == identity.parameter_digest
    )
    assert (
        graph_stage.result["predictions"]["y"]["sampling_identity"][
            "parameter_digest"
        ]
        == identity.parameter_digest
    )
