"""Deterministic multi-task (ICM) exact Gaussian process backed by safe NumPy arrays.

One artifact carries the joint model for every target of a task.  The kernel is
the intrinsic coregionalization model K((x, t), (x', t')) = B[t, t'] * rbf(x, x'),
so correlated targets share statistical strength.  Each manifest predictor
selects its own output through ``config.task_index``.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from ..model_packages import PackageContractError, PredictiveSummary, PredictorSpec, VerifiedModelPackage
from .base import feature_vector, normal_predictive_summary

_PSD_TOLERANCE = 1e-8


class _MultitaskGPPredictor:
    def __init__(self, spec: PredictorSpec, arrays: dict[str, np.ndarray]) -> None:
        self.spec = spec
        self.train_x = arrays["train_x"].astype(np.float64, copy=False)
        self.train_task = arrays["train_task"]
        self.train_y = arrays["train_y"].astype(np.float64, copy=False).reshape(-1)
        self.feature_mean = arrays["feature_mean"].astype(np.float64, copy=False).reshape(-1)
        self.feature_scale = arrays["feature_scale"].astype(np.float64, copy=False).reshape(-1)
        self.lengthscale = arrays["lengthscale"].astype(np.float64, copy=False).reshape(-1)
        self.task_covariance = arrays["task_covariance"].astype(np.float64, copy=False)
        self.mean = arrays["mean"].astype(np.float64, copy=False).reshape(-1)
        self.train_noise = arrays["train_noise"].astype(np.float64, copy=False).reshape(-1)
        self.observation_noise = arrays["observation_noise"].astype(np.float64, copy=False).reshape(-1)
        self.precision = arrays["precision"].astype(np.float64, copy=False)
        self.alpha = arrays["alpha"].astype(np.float64, copy=False).reshape(-1)

        if self.train_task.ndim != 1 or not np.issubdtype(self.train_task.dtype, np.integer):
            raise PackageContractError("multi-task GP train_task must be a 1-D integer array")
        self.train_task = self.train_task.astype(np.int64, copy=False)

        feature_count = len(spec.feature_names)
        row_count = len(self.train_y)
        task_count = self.task_covariance.shape[0] if self.task_covariance.ndim == 2 else 0
        if (
            task_count < 2
            or self.task_covariance.shape != (task_count, task_count)
            or self.train_x.ndim != 2
            or self.train_x.shape != (row_count, feature_count)
            or self.train_task.shape != (row_count,)
            or row_count < 2
            or any(len(item) != feature_count for item in (self.feature_mean, self.feature_scale, self.lengthscale))
            or any(len(item) != task_count for item in (self.mean, self.train_noise, self.observation_noise))
            or self.precision.shape != (row_count, row_count)
            or self.alpha.shape != self.train_y.shape
            or not all(
                np.isfinite(item).all()
                for item in (
                    self.train_x, self.train_y, self.feature_mean, self.feature_scale, self.lengthscale,
                    self.task_covariance, self.mean, self.train_noise, self.observation_noise, self.alpha,
                )
            )
            or (self.feature_scale <= 0).any()
            or (self.lengthscale <= 0).any()
            or (self.train_noise <= 0).any()
            or (self.observation_noise < 0).any()
        ):
            raise PackageContractError("invalid multi-task GP artifact schema")

        if (self.train_task < 0).any() or (self.train_task >= task_count).any():
            raise PackageContractError("multi-task GP train_task indices are out of range")
        if not np.allclose(self.task_covariance, self.task_covariance.T, rtol=1e-7, atol=1e-9):
            raise PackageContractError("multi-task GP task covariance must be symmetric")
        if (np.diag(self.task_covariance) <= 0).any():
            raise PackageContractError("multi-task GP task covariance diagonal must be positive")
        if float(np.linalg.eigvalsh(self.task_covariance).min()) < -_PSD_TOLERANCE * float(np.diag(self.task_covariance).max()):
            raise PackageContractError("multi-task GP task covariance must be positive semidefinite")
        if not np.isfinite(self.precision).all() or not np.allclose(self.precision, self.precision.T, rtol=1e-7, atol=1e-9):
            raise PackageContractError("multi-task GP precision matrix is invalid")

        task_index = spec.config.get("task_index")
        if not isinstance(task_index, int) or isinstance(task_index, bool) or not 0 <= task_index < task_count:
            raise PackageContractError("multi-task GP predictors require an in-range integer config.task_index")
        self.task_index = task_index

    def predict(self, values: dict[str, float], *, seed: int = 0) -> PredictiveSummary:
        del seed
        raw = feature_vector(self.spec, values)
        point = (raw - self.feature_mean) / self.feature_scale
        scaled = (self.train_x - point) / self.lengthscale
        base = np.exp(-0.5 * np.sum(scaled * scaled, axis=1))
        cross = self.task_covariance[self.train_task, self.task_index] * base
        estimate = float(self.mean[self.task_index]) + float(cross @ self.alpha)
        prior_variance = float(self.task_covariance[self.task_index, self.task_index])
        model_variance = max(prior_variance - float(cross @ self.precision @ cross), 0.0)
        observation_variance = float(self.observation_noise[self.task_index])
        return normal_predictive_summary(self.spec, estimate, model_variance, observation_variance)


class BuiltinMultitaskGPAdapter:
    runtime_type = "builtin.multitask_gp.v1"

    def __init__(self) -> None:
        # Every predictor of a task shares one joint artifact; cache the loaded
        # arrays so a package load does not re-read the file once per target.
        self._array_cache: dict[tuple[Path, int, int], dict[str, np.ndarray]] = {}

    def load(self, package: VerifiedModelPackage, predictor: PredictorSpec) -> _MultitaskGPPredictor:
        if predictor.predictive_family != "normal" or predictor.architecture_id != "icm_rbf_v1":
            raise PackageContractError("builtin.multitask_gp.v1 requires normal / icm_rbf_v1")
        path = package.artifact_path(predictor.artifact)
        try:
            stat = path.stat()
            key = (path, stat.st_mtime_ns, stat.st_size)
            arrays = self._array_cache.get(key)
            if arrays is None:
                with np.load(path, allow_pickle=False) as artifact:
                    arrays = {name: artifact[name] for name in artifact.files}
        except (OSError, ValueError, KeyError) as exc:
            raise PackageContractError(f"invalid multi-task GP artifact: {exc}") from exc
        required = {
            "train_x", "train_task", "train_y", "feature_mean", "feature_scale", "lengthscale",
            "task_covariance", "mean", "train_noise", "observation_noise", "precision", "alpha",
        }
        if set(arrays) != required:
            raise PackageContractError("multi-task GP artifact has an unexpected array schema")
        predictor_runtime = _MultitaskGPPredictor(predictor, arrays)
        self._array_cache[key] = arrays
        return predictor_runtime
