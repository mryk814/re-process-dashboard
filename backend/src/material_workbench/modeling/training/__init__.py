"""Configuration-driven training for ordinary fixed-feature estimators."""

from .recipe import (
    ESTIMATOR_IDS,
    ExactGPEstimatorRecipe,
    RidgeEstimatorRecipe,
    estimator_recipe,
)

__all__ = [
    "ESTIMATOR_IDS",
    "ExactGPEstimatorRecipe",
    "RidgeEstimatorRecipe",
    "estimator_recipe",
]
