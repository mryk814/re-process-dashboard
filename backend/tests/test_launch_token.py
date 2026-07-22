from pathlib import Path

from fastapi.testclient import TestClient

from material_workbench.app import create_app


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "data" / "source" / "process_dashboard_realistic_excel_v2.xlsx"


def test_launch_token_protects_api_health_and_downloads(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("WORKBENCH_LAUNCH_TOKEN", "test-launch-token")
    app = create_app(SOURCE, tmp_path / "workbench.db")

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


def test_launch_token_is_disabled_for_plain_web_development(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("WORKBENCH_LAUNCH_TOKEN", raising=False)
    with TestClient(create_app(SOURCE, tmp_path / "workbench.db")) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/api/projects").status_code == 200
