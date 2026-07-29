from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import cast

from material_workbench.modeling.training.feature_dataset import TargetTrainingSet
from material_workbench.modeling.training.recipe import (
    ExactGPEstimatorRecipe,
    RidgeEstimatorRecipe,
)

from .types import TrainedPredictor


Trainer = Callable[
    [TargetTrainingSet, RidgeEstimatorRecipe | ExactGPEstimatorRecipe, Path],
    TrainedPredictor,
]


def estimator_trainer(estimator_id: str) -> Trainer:
    if estimator_id == "ridge.v1":
        from .ridge import train

        return cast(Trainer, train)
    if estimator_id == "exact-gp-rbf.v1":
        from .exact_gp import train

        return cast(Trainer, train)
    raise ValueError(f"unknown estimator id: {estimator_id}")
