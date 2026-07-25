"""学習データとTaskDefinitionの宣言範囲が食い違ったまま通らないことを固定する。

同じ構造のデータへ差し替えたとき、契約ファイルは変更しなくて済む（ケースE）。
その代わり、差し替えたデータが宣言範囲を超えた場合は黙って通ってはならない。

- `allowed_range` 超過は**失敗させる**。候補は `allowed_range` で検証されるので、
  そこを超える学習行は候補から到達できず、データと契約の不一致を意味する
- `training_range` のずれは**報告する**。宣言はデータの観測結果なので、
  作り直すかどうかは科学的契約に対する人の判断になる
"""
from __future__ import annotations

import pytest

from material_workbench.modeling.model_lifecycle import (
    PackageContractError,
    training_range_drift,
    validate_training_rows_within_allowed_range,
)
from material_workbench.tasks.task_registry import load_task_contracts


# 現行リポジトリで training_range が実データからずれているTask。
# 契約とPackageの作り直しが必要なため、ここでは失敗させず現状を記録する。
KNOWN_TRAINING_RANGE_DRIFT = {
    "annealed-properties-v1",
    "hot-rolled-properties-v1",
}


class _Descriptor:
    def __init__(self, observations: list[dict]) -> None:
        self.source_path = "swapped.csv"
        self.source_sha256 = "0" * 64
        self.profile_path = "profile.json"
        self.profile_id = "profile"
        self.observations = observations
        self.medians: dict[str, float] = {}


def _numeric_field(task_id: str):
    task = load_task_contracts()[task_id].task_definition
    return next(
        field
        for group in task.input_groups
        for field in group.fields
        if field.kind == "number" and field.allowed_range is not None
    )


def test_training_rows_beyond_the_allowed_range_are_rejected() -> None:
    task_id = "concrete-strength-v1"
    contract = load_task_contracts()[task_id]
    field = _numeric_field(task_id)
    assert field.allowed_range is not None
    group, key = field.path.split(".", 1)
    bucket = "composition" if group == "composition" else "features"

    descriptor = _Descriptor([
        {"eligible": True, bucket: {key: field.allowed_range.max * 10.0}},
    ])

    with pytest.raises(PackageContractError, match="allowed_rangeを超えています"):
        validate_training_rows_within_allowed_range(descriptor, contract)


def test_ineligible_rows_do_not_trigger_the_range_check() -> None:
    """不適格行は学習へ入らないので、範囲判定の対象にもしない。"""

    task_id = "concrete-strength-v1"
    contract = load_task_contracts()[task_id]
    field = _numeric_field(task_id)
    assert field.allowed_range is not None
    group, key = field.path.split(".", 1)
    bucket = "composition" if group == "composition" else "features"

    descriptor = _Descriptor([
        {"eligible": False, bucket: {key: field.allowed_range.max * 10.0}},
    ])

    validate_training_rows_within_allowed_range(descriptor, contract)


def test_every_registered_task_keeps_its_training_data_inside_the_allowed_range(
    client,
) -> None:
    registry = client.app.state.task_registry

    for task_id in registry.available_task_ids:
        validate_training_rows_within_allowed_range(
            registry.runtime_for(task_id).data, registry.contract_for(task_id)
        )


def test_declared_training_range_drift_is_reported_not_hidden(client) -> None:
    """training_rangeのずれを検出でき、既知のずれが増えていないこと。"""

    registry = client.app.state.task_registry

    drifted = {
        task_id
        for task_id in registry.available_task_ids
        if training_range_drift(
            registry.runtime_for(task_id).data, registry.contract_for(task_id)
        )
    }

    assert drifted == KNOWN_TRAINING_RANGE_DRIFT, (
        "training_rangeのずれが変化しました。増えた場合は差し替えたデータが宣言を"
        f"超えています: 増={sorted(drifted - KNOWN_TRAINING_RANGE_DRIFT)}, "
        f"減={sorted(KNOWN_TRAINING_RANGE_DRIFT - drifted)}"
    )
