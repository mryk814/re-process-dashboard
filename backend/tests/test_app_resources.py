from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from material_workbench.app import _AppResources, create_app
from material_workbench.schemas import ProjectCreateInput


def test_prepared_resources_keep_each_app_database_and_work_graph_isolated(
    tmp_path: Path,
    app_resources: _AppResources,
) -> None:
    first_app = create_app(db_path=tmp_path / "first.db", _resources=app_resources)
    second_app = create_app(db_path=tmp_path / "second.db", _resources=app_resources)

    with TestClient(first_app), TestClient(second_app):
        assert first_app.state.task_registry is second_app.state.task_registry
        assert first_app.state.data is second_app.state.data
        assert first_app.state.store is not second_app.state.store
        assert first_app.state.inference_work_graph is not second_app.state.inference_work_graph

        created = first_app.state.store.create_project(
                ProjectCreateInput(name="分離確認", task_id="annealed-properties-v1")
        )
        assert second_app.state.store.get_project(created.id) is None


@pytest.mark.parametrize(
    "override",
    [
        {"source_path": "source.xlsx"},
        {"flank_wear_source_path": "flank.xlsx"},
        {"package_roots": {"annealed-properties-v1": "package"}},
        {"active_packages_path": "active-packages.json"},
    ],
)
def test_prepared_resources_reject_source_and_package_overrides(
    app_resources: _AppResources,
    override: dict,
) -> None:
    with pytest.raises(ValueError, match="preloaded resources"):
        create_app(_resources=app_resources, **override)
