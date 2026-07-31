"""Static exact-RBF GP adapter backed only by safetensors.

The adapter recreates one audited GP formula; it never unpickles a torch module
or imports a model class named by a package.
"""
from __future__ import annotations

from decision_workbench.modeling.packages.contracts import (
    MissingOptionalDependency,
    PackageContractError,
    PredictiveSummary,
    PredictorSpec,
)
from decision_workbench.modeling.packages.ports import VerifiedPackageArtifacts
from .base import feature_vector

try:
    import gpytorch  # noqa: F401  # Declares the compatible training/runtime family.
    import torch
    from safetensors.torch import load_file as load_safetensors
except ModuleNotFoundError:  # Optional runtime profile.
    torch = None
    load_safetensors = None


class _ExactRBFPredictor:
    def __init__(self, spec: PredictorSpec, tensors: dict[str, object]) -> None:
        self.spec, self.tensors = spec, tensors

    def predict(self, values: dict[str, float], *, seed: int = 0) -> PredictiveSummary:
        assert torch is not None
        train_x = self.tensors["train_x"].double()
        train_y = self.tensors["train_y"].double().reshape(-1)
        lengthscale = self.tensors["lengthscale"].double().reshape(1, -1)
        outputscale = float(self.tensors["outputscale"].double().reshape(()))
        noise = float(self.tensors["noise"].double().reshape(()))
        mean = float(self.tensors["mean"].double().reshape(()))
        x = torch.tensor(feature_vector(self.spec, values), dtype=torch.float64).reshape(1, -1)
        if train_x.shape[1] != x.shape[1] or (lengthscale <= 0).any() or outputscale <= 0 or noise <= 0:
            raise PackageContractError("invalid static GP tensor schema")
        def kernel(left: object, right: object) -> object:
            scaled = (left[:, None, :] - right[None, :, :]) / lengthscale
            return outputscale * torch.exp(-0.5 * (scaled * scaled).sum(dim=2))
        covariance = kernel(train_x, train_x) + noise * torch.eye(len(train_x), dtype=torch.float64)
        cross = kernel(train_x, x)
        solved = torch.linalg.solve(covariance, train_y - mean)
        prediction = mean + float((cross[:, 0] * solved).sum())
        latent_model_variance = max(
            outputscale - float((cross[:, 0] * torch.linalg.solve(covariance, cross[:, 0])).sum()),
            0.0,
        )
        total_predictive_variance = latent_model_variance + noise
        total_predictive_std = total_predictive_variance ** 0.5
        return PredictiveSummary(
            target=self.spec.target,
            target_kind=self.spec.target_kind,
            unit=self.spec.unit,
            point_statistic="mean",
            point_estimate=prediction,
            quantiles={
                "0.05": prediction - 1.644854 * total_predictive_std,
                "0.50": prediction,
                "0.95": prediction + 1.644854 * total_predictive_std,
            },
            distribution={"family": "normal", "support": "real"},
            uncertainty_components={
                "latent_model_variance": latent_model_variance,
                "latent_model_std": latent_model_variance ** 0.5,
                "observation_noise_variance": noise,
                "observation_noise_std": noise ** 0.5,
                "total_predictive_variance": total_predictive_variance,
                "total_predictive_std": total_predictive_std,
            },
        )


class GPyTorchStaticAdapter:
    runtime_type = "gpytorch.static_exact_rbf.v1"

    def load(self, package: VerifiedPackageArtifacts, predictor: PredictorSpec) -> _ExactRBFPredictor:
        if torch is None or load_safetensors is None:
            raise MissingOptionalDependency("install runtime-gpytorch to load gpytorch.static_exact_rbf.v1")
        tensors = load_safetensors(str(package.artifact_path(predictor.artifact)), device="cpu")
        required = {"train_x", "train_y", "lengthscale", "outputscale", "noise", "mean"}
        if set(tensors) != required:
            raise PackageContractError("static GP artifact has an unexpected tensor schema")
        return _ExactRBFPredictor(predictor, tensors)
