from __future__ import annotations

import subprocess

from fastapi.testclient import TestClient


def test_change_guide_is_machine_readable_and_requires_human_review(client: TestClient) -> None:
    response = client.get("/api/developer/change-guide")
    assert response.status_code == 200
    items = response.json()
    assert {item["id"] for item in items} >= {"new-excel", "input", "task", "presentation"}
    task = next(item for item in items if item["id"] == "task")
    assert task["risk"] == "specialist"
    assert task["human_review"]
    profile_command = next(
        command
        for item in items
        for command in item["commands"]
        if "profile_workbench.py" in command["display_text"]
    )
    assert profile_command["arguments"][-2:] == ["inspect", "path/to/file.xlsx"]
    assert "--source" not in profile_command["arguments"]


def test_overview_connects_project_to_runtime_contracts(client: TestClient) -> None:
    response = client.get("/api/developer/overview")
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert items
    assert all(item["project_id"] and item["task_id"] and item["package_id"] for item in items)
    assert all(item["feature_pipeline_id"] and item["runtime_type"] for item in items)
    assert all(isinstance(item["active_package"], bool) for item in items)


def test_runtime_diagnostics_does_not_run_repository_commands(
    client: TestClient,
    monkeypatch,
) -> None:
    def reject_subprocess(*_args, **_kwargs):
        raise AssertionError("Runtime Diagnostics must not start repository tools")

    monkeypatch.setattr(subprocess, "run", reject_subprocess)
    response = client.get("/api/developer/diagnostics")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["schema_version"] == "runtime-diagnostics/v1"
    assert {check["id"] for check in payload["checks"]} == {
        "database",
        "project-references",
        "archived-resources",
        "runtime-capabilities",
        "sidecar",
    }
    assert all(
        "toolchain" not in check["id"] and "generated" not in check["id"]
        for check in payload["checks"]
    )
