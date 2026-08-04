from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from decision_workbench.modeling.training.feature_dataset import TargetTrainingSet
from decision_workbench.modeling.training.recipe import ConcreteEstimatorRecipe

from .types import TrainedPredictor


Trainer = Callable[
    [TargetTrainingSet, ConcreteEstimatorRecipe, Path],
    TrainedPredictor,
]

@dataclass(frozen=True)
class EstimatorImplementation:
    trainer: Trainer
    runtime_type: str
    artifact_suffix: str
    artifact_format: str


def estimator_implementation(estimator_id: str) -> EstimatorImplementation:
    if estimator_id == "ridge.v1":
        from . import ridge

        return EstimatorImplementation(
            trainer=cast(Trainer, ridge.train),
            runtime_type=ridge.RUNTIME_TYPE,
            artifact_suffix=ridge.ARTIFACT_SUFFIX,
            artifact_format=ridge.ARTIFACT_FORMAT,
        )
    if estimator_id == "exact-gp-rbf.v1":
        from . import exact_gp

        return EstimatorImplementation(
            trainer=cast(Trainer, exact_gp.train),
            runtime_type=exact_gp.RUNTIME_TYPE,
            artifact_suffix=exact_gp.ARTIFACT_SUFFIX,
            artifact_format=exact_gp.ARTIFACT_FORMAT,
        )
    if estimator_id == "bayesian-additive-spline.v1":
        from . import bayesian_additive

        return EstimatorImplementation(
            trainer=cast(Trainer, bayesian_additive.train),
            runtime_type=bayesian_additive.RUNTIME_TYPE,
            artifact_suffix=bayesian_additive.ARTIFACT_SUFFIX,
            artifact_format=bayesian_additive.ARTIFACT_FORMAT,
        )
    if estimator_id == "quantile-linear-regression.v1":
        from . import quantile_linear

        return EstimatorImplementation(
            trainer=cast(Trainer, quantile_linear.train),
            runtime_type=quantile_linear.RUNTIME_TYPE,
            artifact_suffix=quantile_linear.ARTIFACT_SUFFIX,
            artifact_format=quantile_linear.ARTIFACT_FORMAT,
        )
    if estimator_id in {"lightgbm-regression.v1", "lightgbm-binary.v1"}:
        from . import lightgbm

        return EstimatorImplementation(
            trainer=cast(Trainer, lightgbm.train),
            runtime_type=lightgbm.RUNTIME_TYPE,
            artifact_suffix=lightgbm.ARTIFACT_SUFFIX,
            artifact_format=lightgbm.ARTIFACT_FORMAT,
        )
    if estimator_id in {"logistic.v1", "poisson.v1"}:
        from . import skops_glm

        return EstimatorImplementation(
            trainer=cast(Trainer, skops_glm.train),
            runtime_type=skops_glm.RUNTIME_TYPE,
            artifact_suffix=skops_glm.ARTIFACT_SUFFIX,
            artifact_format=skops_glm.ARTIFACT_FORMAT,
        )
    raise ValueError(f"unknown estimator id: {estimator_id}")


def estimator_trainer(estimator_id: str) -> Trainer:
    return estimator_implementation(estimator_id).trainer
