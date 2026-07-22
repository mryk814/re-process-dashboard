from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from material_workbench.app import _AppResources, _prepare_app_resources, create_app


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "data" / "source" / "process_dashboard_realistic_excel_v2.xlsx"


@pytest.fixture(scope="session")
def app_resources() -> _AppResources:
    # Shared source/runtime objects are read-only by contract; tests isolate mutable DB/work-graph state.
    return _prepare_app_resources(SOURCE)


@pytest.fixture()
def client(tmp_path: Path, app_resources: _AppResources):
    app = create_app(db_path=tmp_path / "workbench.db", _resources=app_resources)
    with TestClient(app) as test_client:
        yield test_client
