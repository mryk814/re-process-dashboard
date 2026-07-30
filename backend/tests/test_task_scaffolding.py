from __future__ import annotations

import csv
import json
from pathlib import Path

from fastapi.testclient import TestClient
from openpyxl import Workbook

from material_workbench.app import _prepare_app_resources, create_app
from material_workbench.application.dataset_registration import (
    register_managed_dataset,
)
from material_workbench.developer_experience.task_scaffolding import (
    ScaffoldField,
    create_task_scaffold,
    inspect_task_source,
)
from material_workbench.task_composition.catalog import (
    registered_task_modules,
)
from material_workbench.task_composition.builtin_tasks import ANNEALED_TASK_ID
from material_workbench.tasks.task_registry import load_task_contracts


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
        ScaffoldField("carbon", "composition", "carbon_pct", "C", "%"),
        ScaffoldField("temperature", "process", "temperature_c", "温度", "°C"),
        ScaffoldField("route", "categorical", "route", "工程", None),
        ScaffoldField(
            "strength",
            "output",
            "strength_mpa",
            "強度",
            "MPa",
            "at_least",
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
        store=tmp_path / "personal-tasks",
    )

    assert result.state == "draft"
    assert result.task_definition_path is None
    assert "単位を明示してください: carbon" in result.unresolved
    assert not (result.root / "bundle.json").exists()
    safety = json.loads((result.root / "scaffold.json").read_text(encoding="utf-8"))
    assert safety["safety"] == {
        "meaning_and_units_confirmed": False,
        "loads_python_code": False,
        "adapter_family": "tabular-regression",
        "store_scope": "personal",
    }
    resolved = create_task_scaffold(
        source=source,
        task_id="draft-property-v1",
        label="意味を確定",
        fields=_fields(),
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
    resources = _prepare_app_resources(task_ids=frozenset({ANNEALED_TASK_ID}))
    import material_workbench.app as app_module

    def unavailable_chain(**_kwargs):
        raise app_module.WeldingChainBootstrapError("not part of this Task smoke")

    monkeypatch.setattr(app_module, "bootstrap_welding_chain", unavailable_chain)
    app = create_app(
        db_path=database,
        data_library_path=tmp_path / "data-library",
        model_store_path=tmp_path / "personal-models",
        _resources=resources,
    )
    with TestClient(app) as client:
        assert TASK_ID not in client.get("/api/task-definitions").json()
        result = create_task_scaffold(
            source=_source(tmp_path / "new-source.csv"),
            task_id=TASK_ID,
            label="デモ強度",
            fields=_fields(),
            store=task_store,
        )
        assert result.state == "ready"
        assert TASK_ID in registered_task_modules()
        assert TASK_ID in load_task_contracts()
        assert result.profile_path is not None

        from backend.scripts.operations.model_workflow import (
            build_package,
            promote_package,
        )

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
        refreshed = client.post("/api/data-library/tasks/refresh")
        assert refreshed.status_code == 200, refreshed.text
        assert TASK_ID in refreshed.json()["added_task_ids"]

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
