"""Task追加時の登録点を自動検査する。

新しいTaskを追加したとき、どこかの登録点だけ埋め忘れると起動時までエラーが出ない
ものがある（生成物、環境変数、実ファイル）。ここでは登録点を一覧としてコード化し、
すべての登録済みTaskがすべての登録点を満たすことを検査する。

同時に、登録点の一覧が
``docs/architecture/extensibility-inventory.md`` の記録と一致することも検査する。
新しい登録点を作った場合は、検査とインベントリの両方を更新しないと落ちる。
これは「気付かないうちに登録点が増えていた」というdriftを防ぐためであり、
拡張性を測る前提を文書と実装で同じに保つ。
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

import pytest

from material_workbench.modeling.model_lifecycle import load_active_packages
from material_workbench.task_composition.catalog import registered_task_modules


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
TASK_DEFINITION_ROOT = (
    REPOSITORY_ROOT / "backend" / "src" / "material_workbench" / "tasks" / "task_definitions"
)
ACTIVE_PACKAGES = REPOSITORY_ROOT / "models" / "active-packages.json"
TASK_INVENTORY = REPOSITORY_ROOT / "docs" / "contracts" / "task-inventory.json"
EXTENSIBILITY_INVENTORY = (
    REPOSITORY_ROOT / "docs" / "architecture" / "extensibility-inventory.md"
)

# 検査する登録点。idは extensibility-inventory.md §1.1 の表と一致させる。
REGISTRATION_POINT_IDS = (
    "task.contract_json",
    "task.module_entry",
    "task.active_package",
    "task.package_artifact",
    "task.default_source",
    "task.source_env",
    "task.package_override_env",
    "task.inventory_generated",
)


def _task_ids() -> tuple[str, ...]:
    return tuple(sorted(registered_task_modules()))


@pytest.mark.parametrize("task_id", _task_ids())
def test_every_registered_task_fills_every_registration_point(task_id: str) -> None:
    module = registered_task_modules()[task_id]
    active = load_active_packages(ACTIVE_PACKAGES)
    inventory = json.loads(TASK_INVENTORY.read_text(encoding="utf-8"))

    # task.contract_json — TaskDefinitionの実体がid名のJSONとして存在する
    contract_path = TASK_DEFINITION_ROOT / f"{task_id}.json"
    assert contract_path.is_file(), f"{task_id}: task.contract_json"

    # task.module_entry — TaskModuleが自分のtask_idを名乗る
    assert module.task_id == task_id, f"{task_id}: task.module_entry"
    assert module.source_kind, f"{task_id}: task.module_entry (source_kind)"

    # task.active_package — active packageの選択が存在する
    assert task_id in active.tasks, f"{task_id}: task.active_package"

    # task.package_artifact — 選択されたPackageのmanifestが実在する
    manifest = ACTIVE_PACKAGES.parent / active.tasks[task_id].active / "manifest.json"
    assert manifest.is_file(), f"{task_id}: task.package_artifact"

    # task.default_source — 既定sourceがリポジトリ内に実在する
    source = REPOSITORY_ROOT / module.default_source
    assert source.is_file(), f"{task_id}: task.default_source"

    # task.source_env / task.package_override_env — 上書き用の環境変数名を持つ
    assert module.source_env, f"{task_id}: task.source_env"
    assert module.package_override_env, f"{task_id}: task.package_override_env"

    # task.inventory_generated — 生成済みinventoryへ反映されている
    assert task_id in {
        item["task_id"] for item in inventory["tasks"]
    }, f"{task_id}: task.inventory_generated（npm run task:inventory を実行）"


def test_package_override_env_is_unique_per_task() -> None:
    """Package上書きは1 Task 1変数。共有すると別Taskへ誤ったPackageが入る。"""

    by_env: dict[str, list[str]] = defaultdict(list)
    for task_id, module in registered_task_modules().items():
        by_env[module.package_override_env].append(task_id)

    shared = {env: sorted(ids) for env, ids in by_env.items() if len(ids) > 1}
    assert shared == {}, f"package_override_envが共有されています: {shared}"


def test_starter_project_ids_are_unique() -> None:
    """starter projectのidが衝突すると、別Taskのdemo候補を上書きする。"""

    by_project: dict[str, list[str]] = defaultdict(list)
    for task_id, module in registered_task_modules().items():
        if module.starter_project is not None:
            by_project[module.starter_project.project_id].append(task_id)

    shared = {key: sorted(ids) for key, ids in by_project.items() if len(ids) > 1}
    assert shared == {}, f"starter projectのidが衝突しています: {shared}"


def test_public_starter_portfolio_is_explicit_and_stays_small() -> None:
    """Task追加は、明示しない限りユーザー向けGalleryを増やさない。"""

    by_distribution: dict[str, set[str]] = defaultdict(set)
    for module in registered_task_modules().values():
        if module.starter_project is not None:
            by_distribution[module.starter_project.distribution].add(
                module.starter_project.project_id
            )

    assert by_distribution["quickstart"] == {"default"}
    assert by_distribution["gallery"] == {
        "battery-degradation-v1-default",
        "mpea-room-tensile-v1-default",
        "welding-stage-b-default",
    }


def test_shared_source_metadata_agrees_on_one_default_source() -> None:
    """同じsource metadataを名乗るTaskは同じ既定sourceを指す。"""

    for attribute in ("source_env", "source_kind"):
        by_key: dict[str, set[str]] = defaultdict(set)
        for module in registered_task_modules().values():
            by_key[getattr(module, attribute)].add(str(module.default_source))

        ambiguous = {key: sorted(paths) for key, paths in by_key.items() if len(paths) > 1}
        assert ambiguous == {}, f"{attribute}が複数の既定sourceを指しています: {ambiguous}"


def test_registration_point_ids_match_the_extensibility_inventory() -> None:
    """検査対象の登録点と、インベントリ文書に記録された登録点が一致する。"""

    documented = set(
        re.findall(r"`(task\.[a-z0-9_]+)`", EXTENSIBILITY_INVENTORY.read_text(encoding="utf-8"))
    )

    assert documented == set(REGISTRATION_POINT_IDS), (
        "登録点の一覧がずれています。"
        f"文書のみ={sorted(documented - set(REGISTRATION_POINT_IDS))}, "
        f"検査のみ={sorted(set(REGISTRATION_POINT_IDS) - documented)}"
    )
