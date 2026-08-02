from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from decision_workbench.task_composition.builtin.catalog import BUILTIN_TASK_MODULES
from decision_workbench.task_composition.external_tasks import external_task_bundles


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPOSITORY_ROOT / "backend" / "scripts" / "operations" / "task_inventory.py"


def _inventory_module():
    spec = importlib.util.spec_from_file_location("task_inventory_under_test", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_repository_inventory_ignores_personal_external_task_store(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """A local unfinished Task must not make checked-in inventory drift."""

    bundle = tmp_path / "personal-tasks" / "unfinished-task" / "bundle.json"
    bundle.parent.mkdir(parents=True)
    bundle.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("WORKBENCH_TASK_STORE_PATH", str(bundle.parent.parent))

    inventory = _inventory_module().build_inventory()

    assert {item["task_id"] for item in inventory["tasks"]} == set(BUILTIN_TASK_MODULES)
    with pytest.raises(ValueError, match="unsupported external Task bundle"):
        external_task_bundles()


def test_packaged_source_paths_ignore_personal_external_task_store(
    monkeypatch,
    tmp_path: Path,
) -> None:
    bundle = tmp_path / "personal-tasks" / "unfinished-task" / "bundle.json"
    bundle.parent.mkdir(parents=True)
    bundle.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("WORKBENCH_TASK_STORE_PATH", str(bundle.parent.parent))

    sources = _inventory_module().packaged_source_paths()

    assert len(sources) == len({module.default_source for module in BUILTIN_TASK_MODULES.values()})
    assert all(
        path.startswith(("data/source/", "data/fixtures/prediction-graph/"))
        for path in sources
    )
