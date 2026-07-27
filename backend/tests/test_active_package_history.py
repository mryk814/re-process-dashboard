from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from material_workbench.modeling.active_package_history import (
    check_active_package_history,
    current_task_input_contract_digests,
    detect_active_package_drift,
)
from material_workbench.modeling.model_lifecycle import ActivePackagesConfig


ROOT = Path(__file__).resolve().parents[2]
TASK = "hot-rolled-properties-v1"
OTHER_TASK = "annealed-properties-v1"
sys.path.insert(0, str(ROOT / "backend" / "scripts"))

from model_workflow import DEFAULT_SOURCE, activate_package, rollback_package  # noqa: E402


def _config(active: str, previous: str | None, *, task_id: str = TASK) -> ActivePackagesConfig:
    return ActivePackagesConfig.model_validate({
        "schema_version": "active-model-packages/v1",
        "tasks": {task_id: {"active": active, "previous": previous}},
    })


def _package(models_root: Path, name: str, *, task_id: str, digest: str) -> str:
    package = models_root / "packages" / name
    package.mkdir(parents=True)
    (package / "manifest.json").write_text(
        json.dumps({"task_id": task_id, "input_contract_digest": digest}),
        encoding="utf-8",
    )
    return f"packages/{name}"


def test_active_pointer_moved_without_recording_previous_is_reported(tmp_path: Path) -> None:
    digests = current_task_input_contract_digests()
    first = _package(tmp_path, "first", task_id=TASK, digest=digests[TASK])
    second = _package(tmp_path, "second", task_id=TASK, digest=digests[TASK])

    report = detect_active_package_drift(
        [("older", _config(first, None)), ("newer", _config(second, None))],
        models_root=tmp_path,
        contract_digests=digests,
    )

    assert not report.ok
    assert [item.task_id for item in report.drift] == [TASK]
    drift = report.drift[0]
    assert (drift.revision, drift.replaced, drift.active, drift.recorded_previous) == ("newer", first, second, None)


def test_recording_previous_through_the_contract_leaves_no_drift(tmp_path: Path) -> None:
    digests = current_task_input_contract_digests()
    first = _package(tmp_path, "first", task_id=TASK, digest=digests[TASK])
    second = _package(tmp_path, "second", task_id=TASK, digest=digests[TASK])

    report = detect_active_package_drift(
        [("older", _config(first, None)), ("newer", _config(second, first))],
        models_root=tmp_path,
        contract_digests=digests,
    )

    assert report.ok
    assert report.drift == ()
    assert report.accepted == ()


@pytest.mark.parametrize("kind", ["missing", "task-mismatch", "contract-mismatch"])
def test_null_previous_is_accepted_when_the_replaced_package_cannot_be_activated(tmp_path: Path, kind: str) -> None:
    digests = current_task_input_contract_digests()
    active = _package(tmp_path, "active", task_id=TASK, digest=digests[TASK])
    if kind == "missing":
        replaced = "packages/deleted"
    elif kind == "task-mismatch":
        replaced = _package(tmp_path, "other-task", task_id=OTHER_TASK, digest=digests[OTHER_TASK])
    else:
        replaced = _package(tmp_path, "superseded", task_id=TASK, digest="sha256:superseded")

    report = detect_active_package_drift(
        [("older", _config(replaced, None)), ("newer", _config(active, None))],
        models_root=tmp_path,
        contract_digests=digests,
    )

    assert report.ok
    assert [item.rollback_target for item in report.accepted] == [kind]
    assert report.accepted[0].replaced == replaced


def test_previous_that_no_longer_exists_is_reported(tmp_path: Path) -> None:
    digests = current_task_input_contract_digests()
    active = _package(tmp_path, "active", task_id=TASK, digest=digests[TASK])

    report = detect_active_package_drift(
        [("only", _config(active, "packages/deleted"))],
        models_root=tmp_path,
        contract_digests=digests,
    )

    assert not report.ok
    assert [(item.task_id, item.previous, item.rollback_target) for item in report.broken_previous] == [
        (TASK, "packages/deleted", "missing")
    ]


def test_previous_superseded_by_a_contract_migration_is_kept_as_history(tmp_path: Path) -> None:
    digests = current_task_input_contract_digests()
    superseded = _package(tmp_path, "superseded", task_id=TASK, digest="sha256:superseded")
    active = _package(tmp_path, "active", task_id=TASK, digest=digests[TASK])

    report = detect_active_package_drift(
        [("older", _config(superseded, None)), ("newer", _config(active, superseded))],
        models_root=tmp_path,
        contract_digests=digests,
    )

    # 契約移行での切替では、previousは履歴として正しい。rollback不能でも失敗にしない。
    assert report.ok
    assert report.broken_previous == ()
    assert [(item.previous, item.rollback_target) for item in report.superseded_previous] == [
        (superseded, "contract-mismatch")
    ]


def test_uncommitted_active_edits_are_compared_against_the_last_commit(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    models = repository / "models"
    models.mkdir(parents=True)
    digests = current_task_input_contract_digests()
    first = _package(models, "first", task_id=TASK, digest=digests[TASK])
    second = _package(models, "second", task_id=TASK, digest=digests[TASK])
    config_path = models / "active-packages.json"

    def run(*arguments: str) -> None:
        subprocess.run(["git", *arguments], cwd=repository, check=True, capture_output=True)

    run("init")
    run("config", "user.email", "test@example.com")
    run("config", "user.name", "test")
    config_path.write_text(_config(first, None).model_dump_json(), encoding="utf-8")
    run("add", "-A")
    run("commit", "-m", "first")

    config_path.write_text(_config(second, None).model_dump_json(), encoding="utf-8")

    report = check_active_package_history(config_path=config_path, repository_root=repository)

    assert report.available
    assert not report.ok
    assert [(item.revision, item.replaced) for item in report.drift] == [("working-tree", first)]


def test_repository_active_packages_agree_with_their_recorded_history() -> None:
    report = check_active_package_history()
    if not report.available:
        pytest.skip("git history for models/active-packages.json is unavailable")
    assert report.drift == (), [item.model_dump(mode="json") for item in report.drift]
    assert report.broken_previous == (), [item.model_dump(mode="json") for item in report.broken_previous]


def test_rollback_restores_the_previous_package_after_an_activate(tmp_path: Path) -> None:
    models = tmp_path / "models"
    packages = models / "packages"
    packages.mkdir(parents=True)
    source_package = ROOT / "models" / "packages" / "hot-rolled-tutorial-v2"
    shutil.copytree(source_package, packages / "first")
    shutil.copytree(source_package, packages / "second")
    config_path = models / "active-packages.json"
    config_path.write_text(_config("packages/first", None).model_dump_json(), encoding="utf-8")

    activated = activate_package(TASK, packages / "second", DEFAULT_SOURCE, config_path)
    assert (activated["active"], activated["previous"]) == ("packages/second", "packages/first")

    rolled_back = rollback_package(TASK, DEFAULT_SOURCE, config_path)
    assert (rolled_back["active"], rolled_back["previous"]) == ("packages/first", "packages/second")
    assert json.loads(config_path.read_text(encoding="utf-8"))["tasks"][TASK] == {
        "active": "packages/first",
        "previous": "packages/second",
    }
