from __future__ import annotations

import json
import shutil
from dataclasses import replace
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

import material_workbench.app as app_module
from material_workbench.app import _prepare_app_resources, create_app
from material_workbench.tasks.task_registry import TaskRegistry


ROOT = Path(__file__).parents[2]
BROKEN_TASK_ID = "heat-treatment-tradeoff-v1"


class _ContractBrokenRuntime:
    def __init__(self, runtime: Any) -> None:
        self._runtime = runtime
        self.task_id = "wrong-task"
        self.data = runtime.data
        self.model_package = runtime.model_package
        self.support_policy_id = runtime.support_policy_id

    @property
    def output_keys(self):
        return self._runtime.output_keys

    def predict(self, candidate: Any, **kwargs: Any):
        return self._runtime.predict(candidate, **kwargs)

    def predict_core(self, candidate: Any, **kwargs: Any):
        return self._runtime.predict_core(candidate, **kwargs)

    def evidence(self, candidate: Any):
        return self._runtime.evidence(candidate)

    def support_summary(self, candidate: Any):
        return self._runtime.support_summary(candidate)

    def support_by_target(self, candidate: Any):
        return self._runtime.support_by_target(candidate)

    def similarity(
        self, candidate: Any, limit: int = 6, target: str | None = None
    ):
        return self._runtime.similarity(candidate, limit, target)


def _candidate_update(candidate: dict[str, object]) -> dict[str, object]:
    return {
        "name": candidate["name"],
        "inputs": candidate["inputs"],
        "provenance": candidate["provenance"],
        "expected_revision": candidate["revision"],
    }


def test_one_broken_task_keeps_other_tasks_and_saved_history_available(
    tmp_path: Path,
    app_resources,
) -> None:
    database = tmp_path / "workbench.db"
    data_library = tmp_path / "data-library"

    with TestClient(
        create_app(
            db_path=database,
            data_library_path=data_library,
            _resources=app_resources,
        )
    ) as healthy:
        projects = healthy.get("/api/projects").json()
        broken_project = next(item for item in projects if item["task_id"] == BROKEN_TASK_ID)
        broken_candidate = healthy.get(
            f"/api/projects/{broken_project['id']}/candidates"
        ).json()[0]
        saved = healthy.post(
            f"/api/projects/{broken_project['id']}/candidates/{broken_candidate['id']}/predict",
            params={"expected_revision": broken_candidate["revision"]},
        )
        assert saved.status_code == 200
        saved_snapshot_id = saved.json()["snapshot"]["id"]

    active = json.loads(
        (ROOT / "models" / "active-packages.json").read_text(encoding="utf-8")
    )
    selected = ROOT / "models" / active["tasks"][BROKEN_TASK_ID]["active"]
    broken_package = tmp_path / "broken-package"
    shutil.copytree(selected, broken_package)
    (broken_package / "manifest.json").write_text("{}", encoding="utf-8")
    degraded_resources = _prepare_app_resources(
        package_roots={BROKEN_TASK_ID: broken_package}
    )

    availability = degraded_resources.task_registry.availability_for(BROKEN_TASK_ID)
    assert availability.status == "unavailable"
    assert availability.stage == "package"
    assert "Model Package" in availability.message
    assert availability.resource_id == BROKEN_TASK_ID
    assert availability.expected_locator == str(broken_package.resolve())
    assert "active-packages.json" in availability.recovery_hint

    with TestClient(
        create_app(
            db_path=database,
            data_library_path=data_library,
            _resources=degraded_resources,
        )
    ) as degraded:
        health = degraded.get("/api/health")
        assert health.status_code == 200
        assert health.json()["ok"] is True
        assert health.json()["degraded"] is True
        assert health.json()["tasks"][BROKEN_TASK_ID]["availability"]["status"] == "unavailable"

        readiness = degraded.get("/api/readiness")
        assert readiness.status_code == 200
        assert readiness.json()["ready"] is True
        assert readiness.json()["degraded"] is True
        assert BROKEN_TASK_ID in readiness.json()["unavailable_tasks"]

        catalog = degraded.get("/api/task-definitions").json()
        broken_catalog = next(
            item
            for item in catalog
            if item["definition"]["task_definition"]["id"] == BROKEN_TASK_ID
        )
        assert broken_catalog["definition"]["availability"]["status"] == "unavailable"
        assert broken_catalog["definition"]["availability"]["message"]

        project_definition = degraded.get(
            f"/api/projects/{broken_project['id']}/task-definition"
        )
        assert project_definition.status_code == 200
        assert project_definition.json()["availability"]["status"] == "unavailable"

        history = degraded.get(f"/api/projects/{broken_project['id']}/history")
        assert history.status_code == 200
        assert history.json()["project"]["id"] == broken_project["id"]
        assert any(
            snapshot["id"] == saved_snapshot_id
            for item in history.json()["candidates"]
            for snapshot in item["snapshots"]
        )
        assert degraded.get(
            f"/api/projects/{broken_project['id']}/snapshots/{saved_snapshot_id}"
        ).status_code == 200
        assert degraded.get(
            f"/api/projects/{broken_project['id']}/candidates"
        ).status_code == 200

        preview = degraded.post(
            f"/api/projects/{broken_project['id']}/candidates/{broken_candidate['id']}/preview",
            params={"expected_revision": broken_candidate["revision"]},
        )
        assert preview.status_code == 503
        assert preview.json()["code"] == "runtime_unavailable"

        changed = degraded.put(
            f"/api/projects/{broken_project['id']}/candidates/{broken_candidate['id']}",
            json=_candidate_update(broken_candidate),
        )
        assert changed.status_code == 503
        assert changed.json()["code"] == "runtime_unavailable"

        project_change = degraded.put(
            f"/api/projects/{broken_project['id']}",
            json=broken_project,
        )
        assert project_change.status_code == 503

        # 検討グループの所属はmetadataだが、他のProject更新と同じ可用性方針に揃える。
        # 所属解除も同じ経路を通る。
        group_change = degraded.put(
            f"/api/projects/{broken_project['id']}/group",
            json={
                "project_series_id": None,
                "expected_project_series_id": broken_project["project_series_id"],
            },
        )
        assert group_change.status_code == 503

        creation_options = degraded.get("/api/project-creation-options").json()
        assert BROKEN_TASK_ID not in {
            package["task_id"] for package in creation_options["model_packages"]
        }

        healthy_project = next(
            item
            for item in degraded.get("/api/projects").json()
            if item["task_id"] == "annealed-properties-v1"
        )
        healthy_candidate = degraded.get(
            f"/api/projects/{healthy_project['id']}/candidates"
        ).json()[0]
        healthy_preview = degraded.post(
            f"/api/projects/{healthy_project['id']}/candidates/{healthy_candidate['id']}/preview",
            params={"expected_revision": healthy_candidate["revision"]},
        )
        assert healthy_preview.status_code == 200


def test_source_failure_keeps_typed_diagnostics(tmp_path: Path) -> None:
    missing_source = tmp_path / "missing-source.xlsx"

    resources = _prepare_app_resources(source_path=missing_source)

    availability = resources.task_registry.availability_for(
        "annealed-properties-v1"
    )
    assert availability.status == "unavailable"
    assert availability.stage == "source"
    assert availability.resource_id == "primary"
    assert availability.expected_locator == str(missing_source.resolve())
    assert "WORKBENCH_SOURCE_PATH" in availability.recovery_hint


def test_runtime_factory_failure_keeps_package_diagnostics(
    monkeypatch,
) -> None:
    modules = dict(app_module.registered_task_modules())

    def fail_runtime(_data, _package):
        raise ValueError("runtime bootstrap failed")

    modules[BROKEN_TASK_ID] = replace(
        modules[BROKEN_TASK_ID],
        runtime_factory=fail_runtime,
    )
    monkeypatch.setattr(app_module, "registered_task_modules", lambda: modules)

    resources = _prepare_app_resources()
    availability = resources.task_registry.availability_for(BROKEN_TASK_ID)
    active_package = json.loads(
        (ROOT / "models" / "active-packages.json").read_text(encoding="utf-8")
    )["tasks"][BROKEN_TASK_ID]["active"]

    assert availability.status == "unavailable"
    assert availability.stage == "runtime"
    assert availability.resource_id
    assert availability.expected_locator == str(
        (ROOT / "models" / active_package).resolve()
    )
    assert "runtime種別" in availability.recovery_hint


def test_runtime_contract_failure_keeps_package_diagnostics(
    app_resources,
) -> None:
    runtimes = dict(app_resources.runtimes)
    runtime = runtimes[BROKEN_TASK_ID]
    runtimes[BROKEN_TASK_ID] = _ContractBrokenRuntime(runtime)

    registry = TaskRegistry(
        runtimes,
        modules=app_resources.modules,
        degrade_invalid_runtimes=True,
    )
    availability = registry.availability_for(BROKEN_TASK_ID)
    package = runtime.model_package
    assert package is not None

    assert availability.status == "unavailable"
    assert availability.stage == "runtime"
    assert availability.resource_id == package.manifest.package_id
    assert availability.expected_locator == str(package.root)
    assert "TaskDefinition" in availability.recovery_hint
