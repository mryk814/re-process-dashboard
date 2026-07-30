"""Dependency-light ports shared by task composition consumers."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence, runtime_checkable

from material_workbench.contracts.chain_uncertainty_contracts import StageSampleResult
from material_workbench.contracts.schemas import Candidate, CandidateInput
from material_workbench.data.dataset_profile import DatasetInputProfile

@runtime_checkable
class DataDescriptor(Protocol):
    source_path: str
    source_sha256: str
    profile_path: str
    profile_id: str
    observations: list[dict[str, Any]]
    medians: dict[str, float]


@runtime_checkable
class QualitySurface(Protocol):
    quality: list[dict[str, Any]]
    detected_quality: list[dict[str, Any]]
    technical_columns: dict[tuple[str, str], str]


@runtime_checkable
class ModelPackageDescriptor(Protocol):
    """The verified-package surface needed by task composition."""

    root: Path
    manifest: Any
    manifest_sha256: str


@runtime_checkable
class PredictionRuntime(Protocol):
    task_id: str
    data: DataDescriptor
    model_package: ModelPackageDescriptor | None
    support_policy_id: str

    @property
    def output_keys(self) -> frozenset[str]: ...

    def predict(self, candidate: Any, **kwargs: Any) -> dict[str, Any]: ...

    def predict_core(self, candidate: Any, **kwargs: Any) -> dict[str, Any]: ...


@runtime_checkable
class BatchPredictionRuntime(Protocol):
    @property
    def supports_batch_prediction(self) -> bool: ...

    def predict_batch(
        self,
        candidates: Sequence[Any],
        **kwargs: Any,
    ) -> list[dict[str, Any]]: ...


@runtime_checkable
class StageSampleRuntime(Protocol):
    chain_sampling_method: str
    chain_sample_bounds: Mapping[str, tuple[float | None, float | None]]

    def sample_core(
        self,
        candidate: Any,
        *,
        sample_count: int,
        seed: int,
    ) -> StageSampleResult: ...


@runtime_checkable
class SupportProvider(Protocol):
    def evidence(self, candidate: Any) -> tuple[Any, list[dict[str, Any]]]: ...

    def support_summary(self, candidate: Any) -> Any: ...

    def support_by_target(self, candidate: Any) -> dict[str, Any]: ...

    def similarity(
        self,
        candidate: Any,
        limit: int = 6,
        target: str | None = None,
    ) -> list[dict[str, Any]]: ...


@runtime_checkable
class TrainingRangeProvider(Protocol):
    def training_range_for(
        self,
        target: str,
        variable: str,
        *,
        stage_name: str | None = None,
        stage_position_m: float | None = None,
    ) -> tuple[float, float]: ...


ResponseCurveHandler = Callable[
    [
        PredictionRuntime,
        Candidate,
        str,
        str,
        int,
        tuple[float, float] | None,
        str | None,
        float | None,
    ],
    dict[str, Any],
]
CurveFamilyHandler = Callable[
    [PredictionRuntime, Candidate, str, str | None, int, int],
    dict[str, Any],
]
DataLoader = Callable[[Path, DatasetInputProfile | None], DataDescriptor]
RuntimeFactory = Callable[
    [DataDescriptor, ModelPackageDescriptor],
    PredictionRuntime,
]
FeatureRowBuilder = Callable[[dict[str, Any], dict[str, float]], Any]
SpecializedPackageBuilder = Callable[..., None]
TrainingCandidateBuilder = Callable[
    [dict[str, Any], DataDescriptor],
    CandidateInput | None,
]
