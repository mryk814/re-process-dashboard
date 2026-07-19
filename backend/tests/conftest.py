from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from material_workbench.app import create_app


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "data" / "source" / "process_dashboard_realistic_excel_v2.xlsx"


@pytest.fixture()
def client(tmp_path: Path):
    app = create_app(SOURCE, tmp_path / "workbench.db")
    with TestClient(app) as test_client:
        yield test_client

