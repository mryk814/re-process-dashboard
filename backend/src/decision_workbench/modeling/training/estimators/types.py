from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import numpy as np

from decision_workbench.modeling.model_lifecycle import TargetQualityMetric
from decision_workbench.modeling.training.feature_dataset import TargetTrainingSet


@dataclass(frozen=True)
class TrainedPredictor:
    predictor: dict[str, Any]
    artifact: Path
    quality: TargetQualityMetric
    diagnostics: dict[str, Any]
    predict: Callable[[np.ndarray], float]


def standard_training_metadata(
    data: TargetTrainingSet,
    *,
    estimator_id: str,
    uncertainty: str,
    parameters: dict[str, Any],
) -> dict[str, Any]:
    """Reader-facing, estimator-independent training identity."""

    return {
        "schema_version": "standard-training-metadata/v1",
        "estimator_id": estimator_id,
        "training_unit": "replicate_context_mean",
        "validation": {
            "method": data.validation_plan.strategy,
            "folds": data.folds,
            "cohort_digest": data.cohort_digest,
            "fold_digest": data.fold_digest,
            "plan": data.validation_plan.model_dump(mode="json"),
            "plan_digest": data.validation_plan_digest,
        },
        "uncertainty": uncertainty,
        "parameters": parameters,
    }
