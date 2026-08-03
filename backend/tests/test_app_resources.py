from dataclasses import replace
from inspect import signature
from pathlib import Path
from threading import Event
import time

import pytest
from fastapi.testclient import TestClient
from decision_workbench.app import create_app
import decision_workbench.bootstrap.resources as resources_module
import decision_workbench.bootstrap.startup as startup_module
from decision_workbench.bootstrap.resources import AppResources
from decision_workbench.contracts.candidate_project_contracts import ProjectCreateInput
from decision_workbench.task_composition.builtin.annealed import (
    ANNEALED_TASK_ID,
    ANNEALED_TASK_MODULE,
)


def test_prepared_resources_keep_each_app_database_and_work_graph_isolated(
    tmp_path: Path,
    app_resources: AppResources,
) -> None:
    first_app = create_app(db_path=tmp_path / "first.db", _resources=app_resources)
    second_app = create_app(db_path=tmp_path / "second.db", _resources=app_resources)

    with TestClient(first_app), TestClient(second_app):
        assert first_app.state.task_registry is second_app.state.task_registry
        assert first_app.state.data is second_app.state.data
        assert first_app.state.data is app_resources.default_data
        assert first_app.state.data is (
            app_resources.data_by_task[ANNEALED_TASK_ID]
        )
        assert first_app.state.store is not second_app.state.store
        assert (
            first_app.state.inference_work_graph
            is not second_app.state.inference_work_graph
        )

        created = first_app.state.store.create_project(
            ProjectCreateInput(name="分離確認", task_id="annealed-properties-v1")
        )
        assert second_app.state.store.get_project(created.id) is None


def test_default_data_projection_requires_exactly_one_task_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unselected = replace(
        ANNEALED_TASK_MODULE,
        default_data_projection=False,
    )
    monkeypatch.setattr(
        resources_module,
        "registered_task_modules",
        lambda _personal_task_store=None: {"personal-a-v1": unselected},
    )
    with pytest.raises(ValueError, match="none declared"):
        resources_module.prepare_app_resources()

    second = replace(
        ANNEALED_TASK_MODULE,
        task_id="personal-b-v1",
        default_data_projection=True,
    )
    monkeypatch.setattr(
        resources_module,
        "registered_task_modules",
        lambda _personal_task_store=None: {
            "personal-a-v1": ANNEALED_TASK_MODULE,
            "personal-b-v1": second,
        },
    )
    with pytest.raises(ValueError, match="multiple declared"):
        resources_module.prepare_app_resources()


def test_default_data_projection_ignores_added_task_registration_order() -> None:
    added_task = replace(
        ANNEALED_TASK_MODULE,
        task_id="personal-a-v1",
        default_data_projection=False,
    )
    default_task = replace(
        ANNEALED_TASK_MODULE,
        task_id="default-v1",
        default_data_projection=True,
    )

    selected = resources_module._default_data_task_id({
        "personal-a-v1": added_task,
        "default-v1": default_task,
    })

    assert selected == "default-v1"


def test_legacy_operator_environment_is_rejected_before_resource_loading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MATERIAL_WORKBENCH_MODEL_PACKAGE", "old-package")

    with pytest.raises(RuntimeError, match="DECISION_WORKBENCH_MODEL_PACKAGE"):
        resources_module.prepare_app_resources(task_ids=frozenset())


def test_deferred_resource_promotion_keeps_default_data_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The initial subset and promoted generation select the same Task data."""

    monkeypatch.setenv("WORKBENCH_DEFER_RESOURCES", "1")
    configured_task_store = tmp_path / "configured-tasks"
    monkeypatch.setenv(
        "WORKBENCH_TASK_STORE_PATH",
        str(tmp_path / "unexpected-default-tasks"),
    )
    original_prepare = startup_module.prepare_app_resources
    promotion_prepare_started = Event()
    allow_promotion_prepare = Event()
    calls = 0
    observed_task_stores = []

    def staged_prepare(*args, **kwargs):
        nonlocal calls
        calls += 1
        observed_task_stores.append(kwargs.get("task_store_path"))
        if calls == 2:
            promotion_prepare_started.set()
            assert allow_promotion_prepare.wait(timeout=60)
        return original_prepare(*args, **kwargs)

    monkeypatch.setattr(startup_module, "prepare_app_resources", staged_prepare)
    app = create_app(
        db_path=tmp_path / "deferred.db",
        data_library_path=tmp_path / "data-library",
        task_store_path=configured_task_store,
    )

    try:
        with TestClient(app) as client:
            assert promotion_prepare_started.wait(timeout=60)
            assert app.state.data is app.state.runtime_context.data
            assert app.state.data is app.state.task_registry.runtime_for(
                ANNEALED_TASK_ID
            ).data
            assert observed_task_stores == [
                configured_task_store.resolve(),
                configured_task_store.resolve(),
            ]

            allow_promotion_prepare.set()
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                readiness = client.get("/api/readiness")
                assert readiness.status_code == 200
                if readiness.json()["ready"]:
                    break
                time.sleep(0.05)
            else:
                pytest.fail("deferred resource promotion did not complete")

            assert app.state.data is app.state.runtime_context.data
            assert app.state.data is app.state.task_registry.runtime_for(
                ANNEALED_TASK_ID
            ).data
    finally:
        allow_promotion_prepare.set()


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
        from decision_workbench.bootstrap.resources import prepare_app_resources

        prepare_app_resources(source_overrides={"primary": "source.xlsx"})


def test_app_resource_overrides_expose_only_task_id_mapping() -> None:
    parameters = signature(create_app).parameters
    assert "source_overrides" in parameters
    assert "source_path" not in parameters
    assert "flank_wear_source_path" not in parameters
    resources_source = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "decision_workbench"
        / "bootstrap"
        / "resources.py"
    ).read_text(encoding="utf-8")
    assert "module.source_kind ==" not in resources_source
