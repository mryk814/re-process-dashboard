from __future__ import annotations

import json
import shutil
from pathlib import Path

from fastapi.testclient import TestClient

from material_workbench.app import _prepare_app_resources, create_app


ROOT = Path(__file__).parents[2]
BROKEN_TASK_ID = "heat-treatment-tradeoff-v1"


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
    assert availability.stage in {"package", "runtime"}
    assert "Model Package" in availability.message or "runtime" in availability.message

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
