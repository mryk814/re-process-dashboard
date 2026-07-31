from inspect import signature
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from material_workbench.app import create_app
from material_workbench.bootstrap.resources import AppResources
from material_workbench.contracts.candidate_project_contracts import ProjectCreateInput


def test_prepared_resources_keep_each_app_database_and_work_graph_isolated(
    tmp_path: Path,
    app_resources: AppResources,
) -> None:
    first_app = create_app(db_path=tmp_path / "first.db", _resources=app_resources)
    second_app = create_app(db_path=tmp_path / "second.db", _resources=app_resources)

    with TestClient(first_app), TestClient(second_app):
        assert first_app.state.task_registry is second_app.state.task_registry
        assert first_app.state.data is second_app.state.data
        assert first_app.state.store is not second_app.state.store
        assert (
            first_app.state.inference_work_graph
            is not second_app.state.inference_work_graph
        )

        created = first_app.state.store.create_project(
            ProjectCreateInput(name="分離確認", task_id="annealed-properties-v1")
        )
        assert second_app.state.store.get_project(created.id) is None


@pytest.mark.parametrize(
    "override",
    [
        {"source_overrides": {"annealed-properties-v1": "source.xlsx"}},
        {"package_roots": {"annealed-properties-v1": "package"}},
        {"active_packages_path": "active-packages.json"},
    ],
)
def test_prepared_resources_reject_source_and_package_overrides(
    app_resources: AppResources,
    override: dict,
) -> None:
    with pytest.raises(ValueError, match="preloaded resources"):
        create_app(_resources=app_resources, **override)


def test_source_overrides_are_keyed_only_by_registered_task_id() -> None:
    with pytest.raises(ValueError, match="unknown Task IDs"):
        from material_workbench.bootstrap.resources import prepare_app_resources

        prepare_app_resources(source_overrides={"primary": "source.xlsx"})


def test_app_resource_overrides_expose_only_task_id_mapping() -> None:
    parameters = signature(create_app).parameters
    assert "source_overrides" in parameters
    assert "source_path" not in parameters
    assert "flank_wear_source_path" not in parameters
    resources_source = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "material_workbench"
        / "bootstrap"
        / "resources.py"
    ).read_text(encoding="utf-8")
    assert "module.source_kind ==" not in resources_source
