from __future__ import annotations

from fastapi.testclient import TestClient

from material_workbench.developer_experience.diagnostics import run_developer_doctor


def test_change_guide_is_machine_readable_and_requires_human_review(client: TestClient) -> None:
    response = client.get("/api/developer/change-guide")
    assert response.status_code == 200
    items = response.json()
    assert {item["id"] for item in items} >= {"new-excel", "input", "task", "presentation"}
    task = next(item for item in items if item["id"] == "task")
    assert task["risk"] == "specialist"
    assert task["human_review"]


def test_overview_connects_project_to_runtime_contracts(client: TestClient) -> None:
    response = client.get("/api/developer/overview")
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert items
    assert all(item["project_id"] and item["task_id"] and item["package_id"] for item in items)
    assert all(item["feature_pipeline_id"] and item["runtime_type"] for item in items)


def test_diagnostics_reuses_doctor_json(
    client: TestClient,
    monkeypatch,
) -> None:
    expected = run_developer_doctor(include_generated_checks=False)
    monkeypatch.setattr(
        "material_workbench.api.developer.run_developer_doctor",
        lambda **_: expected,
    )
    response = client.get("/api/developer/diagnostics")
    assert response.status_code == 200
    assert response.json()["schema_version"] == "developer-doctor/v1"
    assert response.json()["task_ids"] == expected.task_ids
