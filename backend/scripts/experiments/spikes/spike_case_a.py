"""反証ケースA：通常の表形式Task。

fixtureで新しい標準表形式Taskを作り、既存アプリ機能をTask固有実装なしで
使えるかを測定する。本番ディレクトリへ一時的に置くファイルは finally で必ず戻す。

測定するもの:
  - 既存ファイル変更数 / 新規ファイル数 / registry追加点
  - 新規contract数 / 新規API分岐数 / 新規UI分岐数
  - Workbench（preview / response curve / similarity）、ロバストネス、範囲探索が通るか
  - 欠損targetが学習除外として扱われるか
"""
from __future__ import annotations

import json
import math
import os
import random
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

REPO = Path(os.environ.get("SPIKE_REPO_ROOT") or Path.cwd()).resolve()
if not (REPO / "pyproject.toml").exists():
    raise SystemExit(f"run from the repository root (got {REPO})")


def _work_dir(name: str) -> Path:
    """Spike成果物はリポジトリ外へ書く。SPIKE_WORK_DIRで上書きできる。"""

    root = Path(os.environ.get("SPIKE_WORK_DIR") or Path(tempfile.gettempdir()) / "material-workbench-spikes")
    return root / name

sys.path.insert(0, str(REPO / "backend" / "src"))
sys.path.insert(0, str(REPO / "backend" / "scripts"))

SCRATCH = _work_dir("case-a")
SPIKE_TASK_ID = "spike-injection-molding-v1"
SPIKE_PROFILE_ID = "spike-injection-molding-v1"
SPIKE_PACKAGE_ID = "spike-injection-molding-ridge-v1"

PRODUCTION_TASK_DEFINITIONS = (
    REPO / "backend" / "src" / "decision_workbench" / "tasks" / "task_definitions"
)

NUMERIC_INPUTS = (
    # path suffix, column, unit, allowed, default, decimals
    ("barrel_temperature_c", "barrel_temperature_c", "°C", (150.0, 320.0), (200.0, 280.0), 1),
    ("screw_speed_rpm", "screw_speed_rpm", "rpm", (10.0, 400.0), (40.0, 260.0), 1),
    ("hold_pressure_mpa", "hold_pressure_mpa", "MPa", (5.0, 160.0), (20.0, 120.0), 1),
    ("cooling_time_s", "cooling_time_s", "s", (1.0, 90.0), (5.0, 45.0), 2),
    ("mold_temperature_c", "mold_temperature_c", "°C", (20.0, 160.0), (30.0, 110.0), 1),
)
RESIN_GRADES = ("grade_a", "grade_b", "grade_c")
GATE_TYPES = ("pin", "edge")
ROWS = 300
MISSING_WARPAGE_FRACTION = 0.15


def generate_source(path: Path) -> dict[str, tuple[float, float]]:
    """合成CSVを書き、実測レンジを返す（training_rangeへ入れるため）。"""

    rng = random.Random(20260725)
    observed: dict[str, list[float]] = {name: [] for name, *_ in NUMERIC_INPUTS}
    header = [
        "run_id",
        *(column for _, column, *_ in NUMERIC_INPUTS),
        "resin_grade",
        "gate_type",
        "shrinkage_pct",
        "warpage_mm",
    ]
    lines = [",".join(header)]
    for index in range(ROWS):
        values: dict[str, float] = {}
        for name, _column, _unit, _allowed, (low, high), _decimals in NUMERIC_INPUTS:
            value = rng.uniform(low, high)
            values[name] = value
            observed[name].append(value)
        resin = RESIN_GRADES[index % len(RESIN_GRADES)]
        gate = GATE_TYPES[index % len(GATE_TYPES)]
        resin_offset = {"grade_a": 0.0, "grade_b": 0.18, "grade_c": -0.12}[resin]
        gate_offset = 0.0 if gate == "pin" else 0.09
        shrinkage = (
            0.55
            + 0.0031 * (values["barrel_temperature_c"] - 240.0)
            - 0.0042 * (values["hold_pressure_mpa"] - 70.0)
            + 0.0021 * (values["mold_temperature_c"] - 70.0)
            - 0.0035 * (values["cooling_time_s"] - 25.0)
            + resin_offset
            + gate_offset
            + rng.gauss(0.0, 0.02)
        )
        warpage = (
            0.42
            + 0.0026 * (values["mold_temperature_c"] - 70.0)
            + 0.0012 * (values["screw_speed_rpm"] - 150.0)
            - 0.0031 * (values["cooling_time_s"] - 25.0)
            + 0.6 * resin_offset
            + rng.gauss(0.0, 0.03)
        )
        # 一部行のwarpageを欠損させる（測定していない行）
        warpage_text = (
            "" if rng.random() < MISSING_WARPAGE_FRACTION else f"{max(warpage, 0.01):.4f}"
        )
        lines.append(
            ",".join(
                [
                    f"run-{index:04d}",
                    *(f"{values[name]:.4f}" for name, *_ in NUMERIC_INPUTS),
                    resin,
                    gate,
                    f"{max(shrinkage, 0.01):.4f}",
                    warpage_text,
                ]
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {
        name: (min(series), max(series)) for name, series in observed.items()
    }


def task_definition_document(training: dict[str, tuple[float, float]]) -> dict[str, object]:
    process_fields = [
        {
            "path": f"process.{name}",
            "kind": "number",
            "order": order,
            "label": name,
            "unit": unit,
            "required": True,
            "editable": True,
            "default_range": {"min": default[0], "max": default[1]},
            "allowed_range": {"min": allowed[0], "max": allowed[1]},
            "training_range": {
                "min": math.floor(training[name][0] * 10_000) / 10_000,
                "max": math.ceil(training[name][1] * 10_000) / 10_000,
            },
        }
        for order, (name, _column, unit, allowed, default, _decimals) in enumerate(NUMERIC_INPUTS)
    ]
    return {
        "task_definition": {
            "schema_version": "task-definition/v1",
            "id": SPIKE_TASK_ID,
            "label": "スパイク：射出成形の寸法精度",
            "canonical_candidate_schema_version": "canonical-candidate/v1",
            "input_groups": [
                {"key": "process", "order": 0, "label": "成形条件", "fields": process_fields},
                {
                    "key": "categorical",
                    "order": 1,
                    "label": "材料・金型区分",
                    "fields": [
                        {
                            "path": "categorical.resin_grade",
                            "kind": "categorical",
                            "order": 0,
                            "label": "樹脂グレード",
                            "required": True,
                            "editable": True,
                            "choices": list(RESIN_GRADES),
                        },
                        {
                            "path": "categorical.gate_type",
                            "kind": "categorical",
                            "order": 1,
                            "label": "ゲート形式",
                            "required": True,
                            "editable": True,
                            "choices": list(GATE_TYPES),
                        },
                    ],
                },
            ],
            "outputs": [
                {
                    "key": "shrinkage_pct",
                    "label": "収縮率",
                    "unit": "%",
                    "goal_direction": "at_most",
                    "measurement_keys": ["shrinkage_pct"],
                    "plausibility_range": {"min": 0, "max": 5},
                    "preferred_display_range": {"min": 0, "max": 2},
                },
                {
                    "key": "warpage_mm",
                    "label": "反り量",
                    "unit": "mm",
                    "goal_direction": "at_most",
                    "measurement_keys": ["warpage_mm"],
                    "plausibility_range": {"min": 0, "max": 10},
                    "preferred_display_range": {"min": 0, "max": 3},
                },
            ],
            "display_decimals": {
                **{f"process.{name}": decimals for name, _c, _u, _a, _d, decimals in NUMERIC_INPUTS},
                "output.shrinkage_pct": 3,
                "output.warpage_mm": 3,
            },
            "fixed_context": [
                {"path": "context.source", "order": 0, "label": "データ", "value": "Case A spike fixture"}
            ],
            "response_curve_variables": [
                {"kind": "numeric_input", "order": 0, "label": "金型温度", "path": "process.mold_temperature_c"},
                {"kind": "numeric_input", "order": 1, "label": "保持圧力", "path": "process.hold_pressure_mpa"},
            ],
        },
        "canonical_candidate": {
            "schema_version": "canonical-candidate/v1",
            "task_id": SPIKE_TASK_ID,
            "composition": {},
            "process": {
                name: round((default[0] + default[1]) / 2, 4)
                for name, _c, _u, _a, default, _d in NUMERIC_INPUTS
            },
            "heat_pattern": None,
            "categorical": {"resin_grade": "grade_a", "gate_type": "pin"},
            "provenance": {"source_kind": "direct", "source_ref": None},
        },
        "runtime_capability": {
            "schema_version": "runtime-capability/v1",
            "task_id": SPIKE_TASK_ID,
            "model_package_schema_version": "model-package/v1",
            "targets": [
                {
                    "target": key,
                    "point_statistics": ["mean"],
                    "standard_deviation": False,
                    "quantiles": True,
                    "samples": False,
                    "parametric_distribution": False,
                    "uncertainty_components": False,
                    "support": True,
                    "warnings": True,
                    "goal_probability": "unavailable",
                }
                for key in ("shrinkage_pct", "warpage_mm")
            ],
            "joint_samples": False,
            "operations": {
                "preview": True,
                "detailed_prediction": True,
                "response_curve": True,
                "similarity": True,
                "snapshot": True,
                "actual_measurement": False,
            },
        },
    }


def tabular_profile_document() -> dict[str, object]:
    return {
        "schema_version": "tabular-dataset-profile/v1",
        "profile_id": SPIKE_PROFILE_ID,
        "name": "スパイク：射出成形の寸法精度",
        "task_id": SPIKE_TASK_ID,
        "package_id": SPIKE_PACKAGE_ID,
        "id_column": "run_id",
        "group_column": None,
        "inputs": [
            *(
                {"path": f"process.{name}", "column": column, "kind": "number", "unit": unit}
                for name, column, unit, _a, _d, _dec in NUMERIC_INPUTS
            ),
            {
                "path": "categorical.resin_grade",
                "column": "resin_grade",
                "kind": "categorical",
                "choices": list(RESIN_GRADES),
            },
            {
                "path": "categorical.gate_type",
                "column": "gate_type",
                "kind": "categorical",
                "choices": list(GATE_TYPES),
            },
        ],
        "outputs": [
            {"key": "shrinkage_pct", "column": "shrinkage_pct", "unit": "%", "lower_bound": 0},
            {"key": "warpage_mm", "column": "warpage_mm", "unit": "mm", "lower_bound": 0},
        ],
    }


def main() -> int:
    from types import MappingProxyType

    from decision_workbench import app as app_module
    import decision_workbench.bootstrap.resources as resources_module
    from decision_workbench.modeling.model_lifecycle import (
        ACTIVE_PACKAGES_PATH,
        load_active_packages,
    )
    from decision_workbench.task_composition.builtin.shared import (
        _application_capability,
        _standard_response_curve,
        TABULAR_EXPLORER,
    )
    from decision_workbench.task_composition.builtin.tabular import (
        _TABULAR_PROFILES,
        _tabular_features,
        _tabular_loader,
        _tabular_runtime,
        _tabular_starter,
        _tabular_training_candidate,
    )
    from decision_workbench.task_composition.catalog import registered_task_modules
    import decision_workbench.task_composition.catalog as task_catalog
    from decision_workbench.task_composition.descriptors import (
        StandardModelAuthoring,
        TaskModule,
    )

    SCRATCH.mkdir(parents=True, exist_ok=True)
    source_csv = SCRATCH / "injection_molding_samples.csv"
    profile_json = SCRATCH / "tabular-profile-spike-injection-molding-v1.json"
    contract_json = SCRATCH / f"{SPIKE_TASK_ID}.json"
    installed_contract = PRODUCTION_TASK_DEFINITIONS / f"{SPIKE_TASK_ID}.json"
    package_root = SCRATCH / "package"
    findings: list[str] = []

    training = generate_source(source_csv)
    profile_json.write_text(
        json.dumps(tabular_profile_document(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    contract_json.write_text(
        json.dumps(task_definition_document(training), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    original_modules = resources_module.registered_task_modules
    original_table = task_catalog.TASK_MODULES
    try:
        # 発見1: builderが load_task_contracts() を root注入なしで呼ぶため、
        # TaskDefinition JSONは本番ディレクトリに存在しないとPackageを作れない。
        shutil.copyfile(contract_json, installed_contract)
        findings.append(
            "builder（tabular_model_builder.build_tabular_package_from_data）が "
            "load_task_contracts() をroot注入なしで呼ぶため、TaskDefinition JSONは "
            "本番 task_definitions/ に置かないとPackageを作れない"
        )

        # 既存Taskの追加と同じ経路だけを使う。新しいPython関数は書かず、
        # built-in compositionの既存factoryを task_id でパラメタ化して再利用する。
        _TABULAR_PROFILES[SPIKE_TASK_ID] = profile_json
        spike_module = TaskModule(
            task_id=SPIKE_TASK_ID,
            package_override_env="DECISION_WORKBENCH_SPIKE_INJECTION_MOLDING_MODEL_PACKAGE",
            source_env="WORKBENCH_SPIKE_INJECTION_MOLDING_SOURCE_PATH",
            source_kind="spike_injection_molding",
            default_source=source_csv,
            data_loader=_tabular_loader(SPIKE_TASK_ID),
            runtime_factory=_tabular_runtime,
            feature_row_builder=_tabular_features(SPIKE_TASK_ID),
            standard_model_authoring=StandardModelAuthoring(
                _tabular_training_candidate,
                ("ridge.v1", "lightgbm-regression.v1"),
                default_estimator_id="ridge.v1",
            ),
            application=_application_capability(
                actual_measurement=False,
                response_curve=True,
                similarity=True,
            ),
            starter_project=_tabular_starter(
                SPIKE_TASK_ID, "スパイク：射出成形"
            ),
            response_curve=_standard_response_curve,
            data_explorer=TABULAR_EXPLORER,
        )
        modules = {**registered_task_modules(), SPIKE_TASK_ID: spike_module}
        resources_module.registered_task_modules = lambda: modules
        # 発見2: model_lifecycle.canonical_training_dataset が task_module() 経由で
        # catalogのallow-listを直接読むため、Package構築の前に一時catalogへ
        # Task compositionを追加する必要がある。
        task_catalog.TASK_MODULES = MappingProxyType(modules)
        findings.append(
            "model_lifecycle.canonical_training_dataset が task_module() で "
            "Task catalogを直接読むため、Package構築より先に "
            "task_composition/catalog.py への登録が必要"
        )

        from operations.model_workflow import build_package

        if package_root.exists():
            shutil.rmtree(package_root)
        build_package(
            SPIKE_TASK_ID,
            source_csv,
            package_root,
            SCRATCH / "feature-dataset.json",
            package_id=SPIKE_PACKAGE_ID,
            package_version="1.0.0",
            replace=True,
            estimator="ridge.v1",
            profile=profile_json,
        )

        real = load_active_packages(ACTIVE_PACKAGES_PATH)
        overrides = {
            task_id: str((ACTIVE_PACKAGES_PATH.parent / selection.active).resolve())
            for task_id, selection in real.tasks.items()
        }
        overrides[SPIKE_TASK_ID] = str(package_root.resolve())
        temp_active = SCRATCH / "active-packages.json"
        temp_active.write_text(
            json.dumps(
                {
                    "schema_version": "active-model-packages/v1",
                    "tasks": {
                        **{
                            task_id: {"active": selection.active, "previous": selection.previous}
                            for task_id, selection in real.tasks.items()
                        },
                        SPIKE_TASK_ID: {"active": "packages/spike", "previous": None},
                    },
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        resources = resources_module.prepare_app_resources(
            package_roots=overrides, active_packages_path=temp_active
        )
        registry = resources.task_registry
        availability = registry.availability_for(SPIKE_TASK_ID)
        print(f"[registry] availability={availability.status} stage={availability.stage} {availability.message}")
        if availability.status != "available":
            findings.append(f"起動時にTaskが利用不可: {availability.message}")
            return _report(findings, ok=False)

        data = resources.runtimes[SPIKE_TASK_ID].data
        eligible = [row for row in data.observations if row["eligible"]]
        missing_warpage = [
            row for row in data.observations if "warpage_mm" not in row["eligible_targets"]
        ]
        print(f"[data] rows={len(data.observations)} eligible={len(eligible)} warpage欠損={len(missing_warpage)}")
        if not missing_warpage:
            findings.append("欠損targetがfixtureに入っていない（測定できていない）")

        _exercise_api(app_module, resources, findings)
        return _report(findings, ok=True)
    except Exception:
        traceback.print_exc()
        findings.append("スパイクが例外で停止した（上のtracebackが実測結果）")
        return _report(findings, ok=False)
    finally:
        resources_module.registered_task_modules = original_modules
        task_catalog.TASK_MODULES = original_table
        _TABULAR_PROFILES.pop(SPIKE_TASK_ID, None)
        if installed_contract.exists():
            installed_contract.unlink()


def _exercise_api(app_module, resources, findings: list[str]) -> None:
    from fastapi.testclient import TestClient

    database = SCRATCH / "spike.db"
    if database.exists():
        database.unlink()
    app = app_module.create_app(
        db_path=database,
        data_library_path=SCRATCH / "data-library",
        _resources=resources,
    )
    project_id = f"{SPIKE_TASK_ID}-default"
    with TestClient(app) as client:
        listed = client.get("/api/task-definitions")
        _check(findings, "GET /api/task-definitions", listed.status_code == 200)
        entries = {
            item["definition"]["task_definition"]["id"] for item in listed.json()
        }
        _check(findings, "task-definitionsにspike Taskが載る", SPIKE_TASK_ID in entries)

        installed = client.post(
            "/api/sample-gallery",
            json={"project_ids": [project_id]},
        )
        _check(
            findings,
            "同梱サンプルとしてstarter projectを追加できる",
            installed.status_code == 200,
            detail=installed.text,
        )
        projects = client.get("/api/projects")
        ids = {item["id"] for item in projects.json()} if projects.status_code == 200 else set()
        _check(findings, "追加したstarter projectが一覧に出る", project_id in ids, detail=sorted(ids))
        if project_id not in ids:
            return

        candidates = client.get(f"/api/projects/{project_id}/candidates")
        _check(findings, "候補一覧", candidates.status_code == 200)
        rows = candidates.json()
        _check(findings, "starter候補が3件", len(rows) == 3, detail=len(rows))
        if not rows:
            return
        candidate_id = rows[0]["id"]
        revision = rows[0]["revision"]

        preview = client.post(
            f"/api/projects/{project_id}/candidates/{candidate_id}/preview",
            params={"expected_revision": revision},
        )
        _check(findings, "preview（予測）", preview.status_code == 200, detail=preview.text[:200])
        if preview.status_code == 200:
            keys = sorted(preview.json()["predictions"])
            _check(
                findings, "2出力が返る", keys == ["shrinkage_pct", "warpage_mm"], detail=keys
            )

        curve = client.get(
            f"/api/projects/{project_id}/candidates/{candidate_id}/response-curve",
            params={
                "target": "shrinkage_pct",
                "variable": "process.mold_temperature_c",
                "expected_revision": revision,
            },
        )
        _check(findings, "response curve", curve.status_code == 200, detail=curve.text[:200])

        similar = client.get(
            f"/api/projects/{project_id}/candidates/{candidate_id}/similar",
            params={"expected_revision": revision},
        )
        _check(findings, "類似観測", similar.status_code == 200, detail=similar.text[:200])

        quality = client.get(f"/api/projects/{project_id}/quality")
        _check(findings, "品質表示", quality.status_code == 200, detail=quality.text[:200])

        training = client.get(f"/api/projects/{project_id}/model-package/training-data")
        _check(findings, "学習データInspector", training.status_code == 200, detail=training.text[:200])

        activities = client.get(
            f"/api/projects/{project_id}/decision-activities",
            params={"candidate_id": candidate_id, "expected_revision": revision},
        )
        _check(findings, "Activity一覧", activities.status_code == 200, detail=activities.text[:200])
        available = (
            [item for item in activities.json() if item["available"]]
            if activities.status_code == 200
            else []
        )
        _check(findings, "ロバストネスが利用可能", bool(available), detail=activities.text[:300])
        if available:
            run = client.post(
                f"/api/projects/{project_id}/candidates/{candidate_id}"
                f"/decision-activities/robustness-analysis-v1/runs",
                json={
                    "expected_revision": revision,
                    "parameters": {
                        "schema_version": "robustness-parameters/v1",
                        "sample_count": 32,
                        "seed": 7,
                        "tolerance_profile": {
                            "fields": {
                                "process.mold_temperature_c": {"kind": "absolute", "amount": 3.0},
                                "process.hold_pressure_mpa": {"kind": "relative", "fraction": 0.05},
                            }
                        },
                    },
                },
            )
            _check(findings, "ロバストネス実行", run.status_code == 201, detail=run.text[:300])

        base_inputs = rows[0]["inputs"]
        screening = client.post(
            "/api/screening",
            params={"project_id": project_id},
            json={
                "purpose": "design_space_map",
                "base_candidate_id": candidate_id,
                "base_inputs": base_inputs,
                "variables": {
                    "process.mold_temperature_c": {"mode": "range", "min": 40.0, "max": 100.0},
                    "process.hold_pressure_mpa": {"mode": "range", "min": 30.0, "max": 110.0},
                },
                "samples": 48,
                "seed": 3,
                "target": "shrinkage_pct",
                "proposal": {"support_policy": "allow_with_warning"},
            },
        )
        _check(findings, "範囲探索（screening）", screening.status_code == 201, detail=screening.text[:300])


def _check(findings: list[str], label: str, ok: bool, detail: object = "") -> None:
    mark = "OK  " if ok else "FAIL"
    print(f"[{mark}] {label}" + (f" :: {detail}" if not ok and detail else ""))
    if not ok:
        findings.append(f"{label} が通らない :: {detail}")


def _report(findings: list[str], *, ok: bool) -> int:
    print("\n=== 発見事項 ===")
    if not findings:
        print("なし（既存機能をTask固有実装なしで利用できた）")
    for item in findings:
        print(f"- {item}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
