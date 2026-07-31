"""Neutral predictor and deterministic-transform adapter ports."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from material_workbench.modeling.model_package_contracts import (
    DeterministicTransformSpec,
    PredictiveSummary,
    PredictorSpec,
)

if TYPE_CHECKING:
    from material_workbench.modeling.model_package_verification import (
        VerifiedModelPackage,
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


class Adapter(Protocol):
    runtime_type: str

    def load(
        self,
        package: VerifiedModelPackage,
        predictor: PredictorSpec,
    ) -> LoadedPredictor: ...


class LoadedDeterministicTransform(Protocol):
    def execute(self, *args: Any, **kwargs: Any) -> Any: ...


class DeterministicTransformAdapter(Protocol):
    runtime_type: str

    def load_transform(
        self,
        package: VerifiedModelPackage,
        transform: DeterministicTransformSpec,
    ) -> LoadedDeterministicTransform: ...
