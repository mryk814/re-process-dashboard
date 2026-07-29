from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import numpy as np

from material_workbench.modeling.model_lifecycle import TargetQualityMetric


@dataclass(frozen=True)
class TrainedPredictor:
    predictor: dict[str, Any]
    artifact: Path
    quality: TargetQualityMetric
    diagnostics: dict[str, Any]
    predict: Callable[[np.ndarray], float]
