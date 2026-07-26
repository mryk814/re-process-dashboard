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


# 宣言済み training_range が実データからずれているTask。
#
# training_range を1つ変えると、task_input_contract_digest が input_groups 全体を
# 対象にしているため、そのTaskの**全Model Package**を作り直すことになる
# （active だけでなく models/available-packages.json のものも）。
# 詳細は docs/architecture/extensibility-inventory.md §1.4 を参照。
KNOWN_TRAINING_RANGE_DRIFT: set[str] = set()

# 1つのTaskDefinitionが複数のデータセットでPackageを持つ。training_range は
# そのすべてを含む必要があるので、同梱している両方のWorkbookに対して検証する。
SHIPPED_WORKBOOKS = (
    "data/source/material_workbench_tutorial_v2.xlsx",
    "data/source/material_workbench_process_v1.xlsx",
)
PRIMARY_SOURCE_TASKS = ("annealed-properties-v1", "hot-rolled-properties-v1")


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


def test_allowed_range_holds_for_every_shipped_dataset_of_the_task() -> None:
    """同じTaskの別データセットで学習したPackageも allowed_range に収まること。

    allowed_range は候補が取り得る範囲なので、どのデータセットでも守られる必要がある。
    """
    from pathlib import Path

    from material_workbench.data.importer import load_workbook_data

    root = Path(__file__).resolve().parents[2]
    for relative in SHIPPED_WORKBOOKS:
        data = load_workbook_data(root / relative)
        for task_id in PRIMARY_SOURCE_TASKS:
            validate_training_rows_within_allowed_range(
                data, load_task_contracts()[task_id]
            )


def test_training_range_describes_the_active_dataset_not_every_dataset() -> None:
    """training_range が「どのデータセットの範囲か」を記録する。

    training_range は応答曲線の掃引軸を決める（hot_rolling.py の
    response_curve_result）。activeなPackageの学習範囲より広く宣言すると、
    曲線が支持の外まで伸びる。そのため**activeなPackageのデータセット**に
    合わせてあり、別データセットで学習したPackageの範囲とは一致しない。

    Task単位の宣言でPackage単位の事実を表しているのが本来の問題で、
    正しい解決はPackage側のsupport範囲を使うこと。
    """
    from pathlib import Path

    from material_workbench.data.importer import load_workbook_data

    root = Path(__file__).resolve().parents[2]
    active = load_workbook_data(root / SHIPPED_WORKBOOKS[0])
    other = load_workbook_data(root / SHIPPED_WORKBOOKS[1])

    for task_id in PRIMARY_SOURCE_TASKS:
        contract = load_task_contracts()[task_id]
        assert training_range_drift(active, contract) == {}, task_id
    # 別データセットは宣言範囲を超える。これは既知の設計上の制約。
    assert training_range_drift(other, load_task_contracts()["hot-rolled-properties-v1"])


def test_range_check_ignores_rows_belonging_to_another_task() -> None:
    """1つのWorkbookが複数Taskの観測を持つので、他Taskの行を混ぜないこと。"""

    task_id = "concrete-strength-v1"
    contract = load_task_contracts()[task_id]
    field = _numeric_field(task_id)
    assert field.allowed_range is not None
    group, key = field.path.split(".", 1)
    bucket = "composition" if group == "composition" else "features"

    foreign = _Descriptor([
        {
            "eligible": True,
            "task_id": "annealed-properties-v1",
            bucket: {key: field.allowed_range.max * 10.0},
        },
    ])

    validate_training_rows_within_allowed_range(foreign, contract)
    assert training_range_drift(foreign, contract) == {}


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
