import json
from pathlib import Path
import subprocess

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

        headers = {
            "Origin": "null",
            "X-Workbench-Launch-Token": "test-launch-token",
        }
        preflight = client.options(
            "/api/projects",
            headers={
                "Origin": "null",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "X-Workbench-Launch-Token",
            },
        )
        assert preflight.status_code == 200
        assert preflight.headers["access-control-allow-origin"] == "null"
        assert client.get("/health", headers=headers).status_code == 200
        assert client.get("/api/projects", headers=headers).status_code == 200
        exported = client.get("/api/projects/default/candidates/export.xlsx", headers=headers)
        assert exported.status_code == 200
        assert exported.headers["content-type"].startswith(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )


def test_without_launch_token_only_non_browser_and_loopback_origins_are_allowed(
    monkeypatch, tmp_path, app_resources: _AppResources
) -> None:
    monkeypatch.delenv("WORKBENCH_LAUNCH_TOKEN", raising=False)
    with TestClient(create_app(db_path=tmp_path / "workbench.db", _resources=app_resources)) as client:
        assert client.get("/health").status_code == 200
        assert client.get(
            "/api/projects", headers={"Origin": "http://127.0.0.1:5180"}
        ).status_code == 200
        assert client.get("/api/projects", headers={"Origin": "null"}).status_code == 403
        assert client.post(
            "/api/projects",
            headers={"Origin": "https://attacker.example"},
            json={},
        ).status_code == 403


def test_dev_launcher_uses_one_ephemeral_token_for_api_and_vite_proxy() -> None:
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        ["node", "scripts/dev-launcher.mjs", "--check"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(result.stdout) == {
        "tokenBytes": 32,
        "apiProtected": True,
        "viteProxyProtected": True,
        "apiUrl": "http://127.0.0.1:8765",
        "webPort": "5180",
    }
