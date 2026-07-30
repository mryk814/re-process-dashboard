"""Profile familyの実装差がloader戻り値の共通境界を越えないことを固定する。

Profile文書はfamilyごとに異なってよい。共通でなければならないのは
「loaderが返す記述子の面」であり、モデルbuilder・学習データInspector・品質集計は
その面だけを読む。
"""
from __future__ import annotations

import inspect
import re

import pytest

from material_workbench.application import data_exploration
from material_workbench.task_composition.ports import (
    DataDescriptor,
    QualitySurface,
)
from material_workbench.task_composition.catalog import (
    registered_task_modules,
)


DECLARED_QUALITY_ATTRIBUTES = ("quality", "detected_quality", "technical_columns")


def test_every_registered_runtime_descriptor_satisfies_the_common_boundary(client) -> None:
    registry = client.app.state.task_registry

    for task_id in registry.available_task_ids:
        data = registry.runtime_for(task_id).data
        assert isinstance(data, DataDescriptor), task_id


def test_quality_capability_is_backed_by_a_declared_quality_surface(client) -> None:
    registry = client.app.state.task_registry

    for task_id, module in registered_task_modules().items():
        if module.data_explorer is None or task_id not in registry.available_task_ids:
            continue
        explorer = registry.data_explorer_for(task_id)
        if not explorer.capability.quality:
            continue
        assert isinstance(explorer.data, QualitySurface), task_id


def test_data_exploration_reads_only_the_declared_quality_attributes() -> None:
    """品質集計が未宣言の属性へ手を伸ばしていないこと。"""

    source = "".join(
        inspect.getsource(method)
        for method in (
            data_exploration.DataExplorationService.quality,
            data_exploration.DataExplorationService.quality_csv,
        )
    )

    touched = set(re.findall(r"\bdata\.([a-z_][a-z_0-9]*)", source))
    declared = set(DataDescriptor.__annotations__) | set(QualitySurface.__annotations__)

    assert touched <= declared, f"未宣言の属性を読んでいます: {sorted(touched - declared)}"
    assert set(QualitySurface.__annotations__) == set(DECLARED_QUALITY_ATTRIBUTES)


def test_a_descriptor_without_the_quality_surface_is_rejected() -> None:
    class WithoutQuality:
        source_path = "source.csv"
        source_sha256 = "0" * 64
        profile_path = "profile.json"
        profile_id = "profile"
        observations: list = []
        medians: dict = {}

    assert isinstance(WithoutQuality(), DataDescriptor)
    assert not isinstance(WithoutQuality(), QualitySurface)


def test_declaring_quality_without_a_surface_fails_at_registry_time(client) -> None:
    from material_workbench.contracts.task_contracts import DataExplorerCapability
    from material_workbench.tasks.task_registry import (
        DataExplorerEntry,
        TaskRegistry,
        TaskRegistryError,
    )

    registry = client.app.state.task_registry
    task_id = next(
        item for item in registry.available_task_ids
        if registered_task_modules()[item].data_explorer is not None
    )
    runtime = registry.runtime_for(task_id)

    class QualitylessDescriptor:
        def __init__(self, data) -> None:
            self.source_path = data.source_path
            self.source_sha256 = data.source_sha256
            self.profile_path = data.profile_path
            self.profile_id = data.profile_id
            self.observations = data.observations
            self.medians = data.medians

    entry = DataExplorerEntry(
        data=QualitylessDescriptor(runtime.data),
        capability=DataExplorerCapability(
            quality=True, lineage=False, candidate_creation=False
        ),
    )

    with pytest.raises(TaskRegistryError, match="quality surface"):
        TaskRegistry._validate_data_explorer(task_id, entry)


def test_training_view_contract_carries_dataset_identity_and_target_eligibility() -> None:
    """Observation familyのTraining View契約が共通到達点の候補として使えること。

    ケースBで、この契約が溶接語彙なしで再利用できることを実測した。共通境界を
    新設するのではなく、この契約を昇格させる方針の根拠になる。
    """

    from material_workbench.data.observation_profile import (
        ObservationTrainingDataset,
        ObservationTrainingRow,
        ObservationTrainingView,
    )

    assert {"profile_id", "profile_digest", "source_sha256", "views"} <= set(
        ObservationTrainingDataset.model_fields
    )
    assert {
        "profile_id", "profile_digest", "source_sha256",
        "family", "feature_names", "rows", "summary",
    } <= set(ObservationTrainingView.model_fields)
    # 入力行の適格性とtargetの適格性が別に表現できること
    assert {"eligible", "exclusion_reasons", "target_status", "provenance"} <= set(
        ObservationTrainingRow.model_fields
    )
