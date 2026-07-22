from fastapi.testclient import TestClient

from material_workbench.app import _AppResources, create_app


def test_launch_token_protects_api_health_and_downloads(monkeypatch, tmp_path, app_resources: _AppResources) -> None:
    monkeypatch.setenv("WORKBENCH_LAUNCH_TOKEN", "test-launch-token")
    app = create_app(db_path=tmp_path / "workbench.db", _resources=app_resources)

    with TestClient(app) as client:
        assert client.get("/health").status_code == 401
        assert client.get("/api/projects").status_code == 401
        assert client.get("/api/projects/default/candidates/export.xlsx").status_code == 401
        assert client.get("/health", headers={"X-Workbench-Launch-Token": "wrong"}).status_code == 401

        headers = {"X-Workbench-Launch-Token": "test-launch-token"}
        assert client.get("/health", headers=headers).status_code == 200
        assert client.get("/api/projects", headers=headers).status_code == 200
        exported = client.get("/api/projects/default/candidates/export.xlsx", headers=headers)
        assert exported.status_code == 200
        assert exported.headers["content-type"].startswith(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )


def test_launch_token_is_disabled_for_plain_web_development(monkeypatch, tmp_path, app_resources: _AppResources) -> None:
    monkeypatch.delenv("WORKBENCH_LAUNCH_TOKEN", raising=False)
    with TestClient(create_app(db_path=tmp_path / "workbench.db", _resources=app_resources)) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/api/projects").status_code == 200
