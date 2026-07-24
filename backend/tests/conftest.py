from pathlib import Path
import shutil

import pytest
from fastapi.testclient import TestClient

from material_workbench.app import _AppResources, _prepare_app_resources, create_app
from material_workbench.execution.inference_work_graph import InferenceWorkGraph


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "data" / "source" / "material_workbench_tutorial_v1.xlsx"


@pytest.fixture(scope="session")
def app_resources() -> _AppResources:
    # Shared source/runtime objects are read-only by contract; tests isolate mutable DB/work-graph state.
    return _prepare_app_resources(SOURCE)


@pytest.fixture(scope="session")
def _client_template(tmp_path_factory: pytest.TempPathFactory, app_resources: _AppResources):
    root = tmp_path_factory.mktemp("shared-client")
    database = root / "workbench.db"
    baseline = root / "baseline.db"
    app = create_app(
        db_path=database,
        data_library_path=root / "data-library",
        _resources=app_resources,
    )
    with TestClient(app) as test_client:
        shutil.copyfile(database, baseline)
        yield test_client, database, baseline


@pytest.fixture()
def client(_client_template):
    """Reuse expensive immutable runtimes while restoring a pristine DB per test."""

    test_client, database, baseline = _client_template
    shutil.copyfile(baseline, database)
    test_client.app.state.inference_work_graph = InferenceWorkGraph(max_entries=256)
    yield test_client
