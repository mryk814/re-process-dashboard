from __future__ import annotations

import csv
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
from threading import Event
import time

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from material_workbench.app import create_app
import material_workbench.bootstrap.contributions as contributions_module
import material_workbench.bootstrap.startup as startup_module
from material_workbench.bootstrap.resources import prepare_app_resources
from material_workbench.bootstrap.startup import (
    _preserve_live_sqlite_generation,
    _restore_live_sqlite_generation,
)
from material_workbench.application.catalog import CatalogUseCases
from material_workbench.application.dataset_registration import (
    register_managed_dataset,
)
from material_workbench.data.file_integrity import file_sha256
from material_workbench.developer_experience.task_scaffolding import (
    ScaffoldField,
    create_task_scaffold,
    inspect_task_source,
    link_promoted_package,
)
from material_workbench.task_composition.external_tasks import (
    external_task_bundles,
)
from material_workbench.task_composition.catalog import (
    registered_task_modules,
)
from material_workbench.task_composition.builtin.annealed import ANNEALED_TASK_ID
from material_workbench.tasks.task_registry import load_task_contracts
from material_workbench.persistence.welding_chain_bootstrap import (
    WeldingChainBootstrapError,
)


ROOT = Path(__file__).resolve().parents[2]
OPERATIONS = ROOT / "backend" / "scripts" / "operations"
if str(OPERATIONS) not in sys.path:
    sys.path.insert(0, str(OPERATIONS))

from model_workflow import build_package, promote_package  # noqa: E402
from task_scaffold import main as task_scaffold_main  # noqa: E402


TASK_ID = "demo-strength-v1"


def _source(path: Path) -> Path:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=("carbon", "temperature", "route", "strength"),
            lineterminator="\n",
        )
        writer.writeheader()
        for index in range(30):
            writer.writerow({
                "carbon": 0.1 + index * 0.01,
                "temperature": 700 + index * 4,
                "route": "A" if index % 2 == 0 else "B",
                "strength": 300 + index * 7 + (12 if index % 2 else 0),
            })
    return path


def _fields() -> list[ScaffoldField]:
    return [
        ScaffoldField(
            "carbon", "composition", "carbon_pct", "C", "%",
            allowed_range=(0.0, 2.0),
            default_range=(0.05, 0.5),
            training_range=(0.1, 0.39),
        ),
        ScaffoldField(
            "temperature", "process", "temperature_c", "温度", "°C",
            allowed_range=(20.0, 1500.0),
            default_range=(650.0, 900.0),
            training_range=(700.0, 816.0),
        ),
        ScaffoldField("route", "categorical", "route", "工程", None),
        ScaffoldField(
            "strength",
            "output",
            "strength_mpa",
            "強度",
            "MPa",
            "at_least",
            plausible_range=(0.0, 2000.0),
            display_range=(250.0, 600.0),
        ),
    ]


def test_inspect_new_excel_selects_sheet_without_modifying_source(
    tmp_path: Path,
) -> None:
    source = tmp_path / "new-source.xlsx"
    workbook = Workbook()
    workbook.active.title = "説明"
    measurements = workbook.create_sheet("測定")
    measurements.append(["carbon", "strength"])
    measurements.append([0.1, 320])
    measurements.append([0.2, 380])
    measurements.append([0.3, 430])
    workbook.save(source)
    workbook.close()
    before = source.read_bytes()

    inspection = inspect_task_source(source, sheet="測定")

    assert inspection.selected_sheet == "測定"
    assert inspection.row_count == 3
    assert [column.name for column in inspection.columns] == [
        "carbon",
        "strength",
    ]
    assert source.read_bytes() == before


def test_live_sqlite_generation_restore_preserves_main_wal_and_shm_bytes(
    tmp_path: Path,
) -> None:
    live = tmp_path / "workspace.db"
    preserved = tmp_path / ".workspace.rollback.db"
    original = {
        live: b"original-main",
        Path(f"{live}-wal"): b"original-wal",
        Path(f"{live}-shm"): b"original-shm",
        Path(f"{live}-journal"): b"original-journal",
    }
    for path, payload in original.items():
        path.write_bytes(payload)

    _preserve_live_sqlite_generation(live, preserved)
    live.write_bytes(b"replacement-main")
    Path(f"{live}-wal").write_bytes(b"replacement-wal")
    _restore_live_sqlite_generation(preserved, live)

    for path, payload in original.items():
        assert path.read_bytes() == payload
    assert not any(
        path.exists()
        for path in (
            preserved,
            Path(f"{preserved}-wal"),
            Path(f"{preserved}-shm"),
            Path(f"{preserved}-journal"),
        )
    )


@pytest.mark.parametrize(
    "contents, message",
    [
        ("", "no header"),
        ("carbon,carbon\n0.1,0.2\n0.2,0.3\n0.3,0.4\n", "unique"),
        (",strength\n0.1,300\n0.2,320\n0.3,340\n", "non-empty"),
    ],
)
def test_inspect_csv_rejects_missing_or_ambiguous_headers(
    tmp_path: Path,
    contents: str,
    message: str,
) -> None:
    source = tmp_path / "invalid.csv"
    source.write_text(contents, encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        inspect_task_source(source)


def test_scaffold_rejects_bundled_task_identity(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="同梱Task ID"):
        create_task_scaffold(
            source=_source(tmp_path / "source.csv"),
            task_id=ANNEALED_TASK_ID,
            label="上書き禁止",
            fields=_fields(),
            grain_confirmation="one-row-one-observation",
            relation_confirmation="no-relations",
            store=tmp_path / "personal-tasks",
        )


def test_cli_requires_explicit_ranges_and_review_contract(
    tmp_path: Path,
    capsys,
) -> None:
    source = _source(tmp_path / "source.csv")
    store = tmp_path / "tasks"
    common = [
        "create",
        str(source),
        "--task-id",
        "cli-property-v1",
        "--label",
        "CLI確認",
        "--input",
        "carbon:composition:carbon_pct:C:%",
        "--input",
        "temperature:process:temperature_c:温度:°C",
        "--input",
        "route:categorical:route:工程:",
        "--output",
        "strength:strength_mpa:強度:MPa:at_least",
        "--grain-confirmation",
        "one-row-one-observation",
        "--relation-confirmation",
        "no-relations",
        "--store",
        str(store),
    ]
    assert task_scaffold_main(common) == 0
    draft = json.loads(capsys.readouterr().out)
    assert draft["state"] == "draft"
    assert any("許容範囲" in item for item in draft["unresolved"])

    assert task_scaffold_main([
        *common,
        "--input-range",
        "carbon:0:2:0.05:0.5:0.1:0.39",
        "--input-range",
        "temperature:20:1500:650:900:700:816",
        "--output-range",
        "strength:0:2000:250:600",
    ]) == 0
    ready = json.loads(capsys.readouterr().out)
    assert ready["state"] == "ready"


def test_task_store_validation_applies_to_load_and_link(
    tmp_path: Path,
    monkeypatch,
) -> None:
    unsafe = ROOT / ".unsafe-personal-tasks"
    monkeypatch.setenv("WORKBENCH_TASK_STORE_PATH", str(unsafe))

    with pytest.raises(ValueError, match="outside the repository"):
        external_task_bundles()
    with pytest.raises(ValueError, match="outside the repository"):
        link_promoted_package("anything-v1", tmp_path / "package")
    model_store = tmp_path / "models"
    with pytest.raises(ValueError, match="outside the repository"):
        promote_package(
            "anything-v1",
            tmp_path / "missing-package",
            tmp_path / "missing-source.csv",
            model_store,
        )
    assert not model_store.exists()


def test_scaffold_keeps_unresolved_meaning_out_of_the_runtime_store(
    tmp_path: Path,
) -> None:
    source = _source(tmp_path / "source.csv")
    result = create_task_scaffold(
        source=source,
        task_id="draft-property-v1",
        label="意味未確定",
        fields=[
            ScaffoldField(
                "carbon",
                "composition",
                "carbon_pct",
                "C",
                None,
            ),
            ScaffoldField(
                "strength",
                "output",
                "strength_mpa",
                "強度",
                "MPa",
            ),
        ],
        grain_confirmation="one-row-one-observation",
        relation_confirmation="no-relations",
        store=tmp_path / "personal-tasks",
    )

    assert result.state == "draft"
    assert result.task_definition_path is None
    assert "単位を明示してください: carbon" in result.unresolved
    assert "物理的な許容範囲を明示してください: carbon" in result.unresolved
    assert "出力の妥当範囲を明示してください: strength" in result.unresolved
    assert not (result.root / "bundle.json").exists()
    safety = json.loads((result.root / "scaffold.json").read_text(encoding="utf-8"))
    assert safety["safety"] == {
        "meaning_and_units_confirmed": False,
        "grain_confirmation": "one-row-one-observation",
        "relation_confirmation": "no-relations",
        "ranges_explicitly_confirmed": False,
        "loads_python_code": False,
        "adapter_family": "tabular-regression",
        "store_scope": "personal",
    }
    resolved = create_task_scaffold(
        source=source,
        task_id="draft-property-v1",
        label="意味を確定",
        fields=_fields(),
        grain_confirmation="one-row-one-observation",
        relation_confirmation="no-relations",
        store=tmp_path / "personal-tasks",
    )
    assert resolved.state == "ready"
    assert (resolved.root / "bundle.json").is_file()


def test_new_csv_scaffold_build_promote_and_project_golden_path(
    tmp_path: Path,
    monkeypatch,
) -> None:
    task_store = tmp_path / "personal-tasks"
    monkeypatch.setenv("WORKBENCH_TASK_STORE_PATH", str(task_store))
    database = tmp_path / "workspace.db"
    # The application starts before the new Task exists. The golden path must
    # not rely on restarting this process after scaffold or promotion.
    resources = prepare_app_resources(task_ids=frozenset({ANNEALED_TASK_ID}))

    def unavailable_chain(**_kwargs):
        raise WeldingChainBootstrapError("not part of this Task smoke")

    monkeypatch.setattr(
        contributions_module,
        "bootstrap_welding_chain",
        unavailable_chain,
    )
    app = create_app(
        db_path=database,
        data_library_path=tmp_path / "data-library",
        model_store_path=tmp_path / "personal-models",
        _resources=resources,
    )
    held_request_started = Event()
    release_held_request = Event()
    real_health = CatalogUseCases.health

    def held_health(use_cases: CatalogUseCases) -> dict[str, object]:
        if not held_request_started.is_set():
            held_request_started.set()
            assert release_held_request.wait(timeout=60)
        return real_health(use_cases)

    monkeypatch.setattr(CatalogUseCases, "health", held_health)

    with TestClient(app) as client:
        assert TASK_ID not in client.get("/api/task-definitions").json()
        result = create_task_scaffold(
            source=_source(tmp_path / "new-source.csv"),
            task_id=TASK_ID,
            label="デモ強度",
            fields=_fields(),
            grain_confirmation="one-row-one-observation",
            relation_confirmation="no-relations",
            store=task_store,
        )
        assert result.state == "ready"
        assert TASK_ID in registered_task_modules()
        assert TASK_ID in load_task_contracts()
        assert result.profile_path is not None

        candidate = tmp_path / "candidate"
        dataset_output = tmp_path / "feature-dataset.json"
        built = build_package(
            TASK_ID,
            result.source_path,
            candidate,
            dataset_output,
            package_id="demo-strength-personal-1",
            package_version="1.0.0",
            replace=False,
            profile=result.profile_path,
        )
        assert built["package"]["task_id"] == TASK_ID
        promoted = promote_package(
            TASK_ID,
            candidate,
            result.source_path,
            tmp_path / "personal-models",
            profile=result.profile_path,
        )
        assert promoted["restart_required"] is False
        assert Path(promoted["trusted_package"]).is_dir()

        registration = register_managed_dataset(
            database=database,
            source=result.source_path,
            library_root=tmp_path / "data-library",
            profile_path=result.profile_path,
            name="新しいデモデータ",
        )
        database_digest_before_failed_refresh = file_sha256(database)
        real_bootstrap = startup_module.bootstrap_workspace_catalog

        def fail_staged_bootstrap(*_args, **_kwargs):
            raise RuntimeError("injected staged refresh failure")

        monkeypatch.setattr(
            startup_module,
            "bootstrap_workspace_catalog",
            fail_staged_bootstrap,
        )
        failed = client.post("/api/data-library/tasks/refresh")
        assert failed.status_code == 409, failed.text
        assert app.state.resources_promoting is False
        assert file_sha256(database) == database_digest_before_failed_refresh
        task_definitions_after_failure = client.get("/api/task-definitions")
        assert task_definitions_after_failure.status_code == 200
        assert TASK_ID not in task_definitions_after_failure.json()
        monkeypatch.setattr(
            startup_module,
            "bootstrap_workspace_catalog",
            real_bootstrap,
        )
        catalog_ids_before_live_failure = {
            item.id
            for item in app.state.workspace_catalog.list_model_package_refs(
                include_archived=True,
            )
        }
        database_digest_before_live_failure = file_sha256(database)
        real_workspace_catalog = startup_module.WorkspaceCatalog

        def fail_live_catalog(*_args, **_kwargs):
            raise RuntimeError("injected live context failure")

        monkeypatch.setattr(
            startup_module,
            "WorkspaceCatalog",
            fail_live_catalog,
        )
        failed_after_staging = client.post(
            "/api/data-library/tasks/refresh",
        )
        assert failed_after_staging.status_code == 409
        assert app.state.resources_promoting is False
        assert file_sha256(database) == database_digest_before_live_failure
        assert {
            item.id
            for item in app.state.workspace_catalog.list_model_package_refs(
                include_archived=True,
            )
        } == catalog_ids_before_live_failure
        assert TASK_ID not in app.state.runtime_context.task_registry.task_ids
        task_definitions_after_live_failure = client.get(
            "/api/task-definitions"
        )
        assert task_definitions_after_live_failure.status_code == 200
        assert TASK_ID not in task_definitions_after_live_failure.json()
        assert not list(
            database.parent.glob(
                f".{database.name}.task-refresh-rollback-*.db*"
            )
        )
        monkeypatch.setattr(
            startup_module,
            "WorkspaceCatalog",
            real_workspace_catalog,
        )

        with ThreadPoolExecutor(max_workers=3) as executor:
            held = executor.submit(
                client.get,
                "/api/health",
            )
            if not held_request_started.wait(timeout=10):
                response = held.result(timeout=1)
                pytest.fail(
                    f"held request did not start: {response.status_code} "
                    f"{response.text}"
                )
            refreshing = executor.submit(
                client.post,
                "/api/data-library/tasks/refresh",
            )
            deadline = time.monotonic() + 60
            while (
                not app.state.resources_promoting
                and time.monotonic() < deadline
            ):
                time.sleep(0.05)
            assert app.state.resources_promoting
            assert not refreshing.done()
            next_health = executor.submit(client.get, "/health")
            time.sleep(0.1)
            assert not next_health.done()
            readiness = client.get("/api/readiness")
            assert readiness.status_code == 200
            assert TASK_ID not in readiness.json()["available_tasks"]
            release_held_request.set()
            held_response = held.result(timeout=10)
            assert held_response.status_code == 200
            assert TASK_ID not in held_response.json()["tasks"]
            refreshed = refreshing.result(timeout=60)
            next_health_response = next_health.result(timeout=10)
            assert next_health_response.status_code == 200
            assert TASK_ID in next_health_response.json()["tasks"]

        assert refreshed.status_code == 200, refreshed.text
        assert TASK_ID in refreshed.json()["added_task_ids"]
        assert refreshed.json()["model_package_ids"] == sorted(
            item["id"]
            for item in client.get(
                "/api/data-library/model-packages?include_gallery=true",
            ).json()
        )
        assert refreshed.json()["added_model_package_ids"]
        assert refreshed.json()["warnings"] == []
        assert app.state.data is app.state.runtime_context.data
        assert app.state.data is app.state.task_registry.runtime_for(
            ANNEALED_TASK_ID
        ).data
        assert not list(
            database.parent.glob(
                f".{database.name}.task-refresh-rollback-*.db*"
            )
        )

        options = client.get("/api/project-creation-options")
        assert options.status_code == 200, options.text
        payload = options.json()
        dataset = next(
            item
            for item in payload["datasets"]
            if item["dataset_revision"]["id"] == registration.dataset_revision_id
        )
        matches = [
            item for item in payload["model_packages"]
            if item["task_id"] == TASK_ID
        ]
        assert matches, json.dumps(payload["model_packages"], ensure_ascii=False)
        package = matches[0]
        created = client.post("/api/projects", json={
            "name": "完全新規Task smoke",
            "task_id": TASK_ID,
            "dataset_view_revision_id": dataset["dataset_views"][0]["id"],
            "model_package_ref_id": package["id"],
        })
        assert created.status_code == 201, created.text


def test_csv_onboarding_api_creates_a_reloadable_personal_task(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """The browser route does not require a CLI hand-off or app restart."""

    task_id = "csv-ui-strength-v1"
    task_store = tmp_path / "personal-tasks"
    monkeypatch.setenv("WORKBENCH_TASK_STORE_PATH", str(task_store))
    resources = prepare_app_resources(task_ids=frozenset({ANNEALED_TASK_ID}))

    def unavailable_chain(**_kwargs):
        raise WeldingChainBootstrapError("not part of this Task smoke")

    monkeypatch.setattr(contributions_module, "bootstrap_welding_chain", unavailable_chain)
    app = create_app(
        db_path=tmp_path / "workspace.db",
        data_library_path=tmp_path / "data-library",
        model_store_path=tmp_path / "personal-models",
        _resources=resources,
    )
    source = _source(tmp_path / "new-source.csv")
    fields = [
        {
            "column": field.column,
            "role": field.role,
            "key": field.key,
            "label": field.label,
            "unit": field.unit,
            "goal_direction": field.goal_direction,
            "allowed_range": field.allowed_range,
            "default_range": field.default_range,
            "training_range": field.training_range,
            "plausible_range": field.plausible_range,
            "display_range": field.display_range,
        }
        for field in _fields()
    ]

    with TestClient(app) as client:
        missing_csv = client.post("/api/data-library/csv-onboarding/inspect")
        assert missing_csv.status_code == 422
        before = client.get("/api/project-creation-options")
        assert before.status_code == 200, before.text
        assert not any(item["task_id"] == task_id for item in before.json()["model_packages"])
        with source.open("rb") as stream:
            inspection = client.post(
                "/api/data-library/csv-onboarding/inspect",
                files={"file": (source.name, stream, "text/csv")},
            )
        assert inspection.status_code == 200, inspection.text
        assert inspection.json()["rows"] == 30
        assert inspection.json()["relations"] == 0
        assert inspection.json()["notice"].startswith("観測最小値")

        with source.open("rb") as stream:
            prepared = client.post(
                "/api/data-library/csv-onboarding/prepare",
                files={"file": (source.name, stream, "text/csv")},
                data={
                    "task_id": task_id,
                    "label": "CSV UI 強度",
                    "fields_json": json.dumps(fields),
                    "grain_confirmation": "one-row-one-observation",
                    "relation_confirmation": "no-relations",
                },
            )
        assert prepared.status_code == 200, prepared.text
        response = prepared.json()
        assert response["state"] == "ready", response
        assert response["task_id"] == task_id
        assert response["dataset_view_revision_id"]
        assert response["model_package_ref_id"]

        options = client.get("/api/project-creation-options")
        assert options.status_code == 200, options.text
        payload = options.json()
        assert any(
            item["dataset_views"][0]["id"] == response["dataset_view_revision_id"]
            for item in payload["datasets"]
        )
        assert any(item["id"] == response["model_package_ref_id"] for item in payload["model_packages"])
        created = client.post("/api/projects", json={
            "name": "CSV UIから作成",
            "task_id": task_id,
            "dataset_view_revision_id": response["dataset_view_revision_id"],
            "model_package_ref_id": response["model_package_ref_id"],
        })
        assert created.status_code == 201, created.text
        project = created.json()
        assert project["dataset_view_revision_id"] == response["dataset_view_revision_id"]
        assert project["model_package_ref_id"] == response["model_package_ref_id"]
        snapshot_candidate = client.post(
            f"/api/projects/{project['id']}/candidates",
            json={
                "name": "CSV UI snapshot",
                "inputs": {
                    "composition": {"carbon_pct": 0.2},
                    "process": {"temperature_c": 760.0},
                    "categorical": {"route": "A"},
                },
            },
        )
        assert snapshot_candidate.status_code == 201, snapshot_candidate.text
        snapshot = client.post(
            f"/api/projects/{project['id']}/candidates/{snapshot_candidate.json()['id']}/snapshots"
        )
        assert snapshot.status_code == 201, snapshot.text
        provenance = snapshot.json()["payload"]["provenance"]
        assert provenance["training_data"]["source_sha256"] == response["source_sha256"]
        assert provenance["training_data"]["source_sha256"]
