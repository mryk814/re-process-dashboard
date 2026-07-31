"""Neutral predictor and deterministic-transform adapter ports."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from decision_workbench.modeling.packages.contracts import (
    DeterministicTransformSpec,
    PredictiveSummary,
    PredictorSpec,
)

class LoadedPredictor(Protocol):
    def predict(self, values: dict[str, float], *, seed: int = 0) -> PredictiveSummary: ...


@runtime_checkable
class LoadedBatchPredictor(Protocol):
    def predict_batch(
        self,
        values: Sequence[dict[str, float]],
        *,
        seed: int = 0,
    ) -> list[PredictiveSummary]: ...


class VerifiedPackageArtifacts(Protocol):
    """Read-only access to artifacts copied and verified by the Package loader."""

    def artifact_path(self, relative_path: str) -> Path: ...


class Adapter(Protocol):
    runtime_type: str

    def load(
        self,
        package: VerifiedPackageArtifacts,
        predictor: PredictorSpec,
    ) -> LoadedPredictor: ...


class LoadedDeterministicTransform(Protocol):
    def execute(self, *args: Any, **kwargs: Any) -> Any: ...


class DeterministicTransformAdapter(Protocol):
    runtime_type: str

    def load_transform(
        self,
        package: VerifiedPackageArtifacts,
        transform: DeterministicTransformSpec,
    ) -> LoadedDeterministicTransform: ...
