from pathlib import Path
import os
import shutil

import pytest
from fastapi.testclient import TestClient

from material_workbench.app import _AppResources, _prepare_app_resources, create_app
from material_workbench.execution.inference_work_graph import InferenceWorkGraph


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "data" / "source" / "material_workbench_tutorial_v2.xlsx"


@pytest.fixture(scope="session", autouse=True)
def isolated_personal_task_store(tmp_path_factory: pytest.TempPathFactory):
    """Repository tests never depend on a developer's personal Task catalog."""

    root = tmp_path_factory.mktemp("personal-task-store")
    previous = os.environ.get("WORKBENCH_TASK_STORE_PATH")
    os.environ["WORKBENCH_TASK_STORE_PATH"] = str(root)
    try:
        yield root
    finally:
        if previous is None:
            os.environ.pop("WORKBENCH_TASK_STORE_PATH", None)
        else:
            os.environ["WORKBENCH_TASK_STORE_PATH"] = previous


@pytest.fixture(scope="session")
def app_resources() -> _AppResources:
    # Shared source/runtime objects are read-only by contract; tests isolate mutable DB/work-graph state.
    return _prepare_app_resources(SOURCE)


@pytest.fixture(scope="session")
def _client_template(tmp_path_factory: pytest.TempPathFactory, app_resources: _AppResources):
    root = tmp_path_factory.mktemp("shared-client")
    database = root / "workbench.db"
    baseline = root / "baseline.db"
    previous_profile_store = os.environ.get("WORKBENCH_PROFILE_STORE_PATH")
    os.environ["WORKBENCH_PROFILE_STORE_PATH"] = str(root / "personal-profiles")
    try:
        app = create_app(
            db_path=database,
            data_library_path=root / "data-library",
            _resources=app_resources,
        )
        with TestClient(app) as test_client:
            installed = test_client.post(
                "/api/sample-gallery",
                json={"project_ids": []},
            )
            installed.raise_for_status()
            shutil.copyfile(database, baseline)
            yield test_client, database, baseline
    finally:
        if previous_profile_store is None:
            os.environ.pop("WORKBENCH_PROFILE_STORE_PATH", None)
        else:
            os.environ["WORKBENCH_PROFILE_STORE_PATH"] = previous_profile_store


@pytest.fixture()
def client(_client_template):
    """Reuse expensive immutable runtimes while restoring a pristine DB per test."""

    test_client, database, baseline = _client_template
    shutil.copyfile(baseline, database)
    test_client.app.state.inference_work_graph = InferenceWorkGraph(max_entries=256)
    yield test_client
