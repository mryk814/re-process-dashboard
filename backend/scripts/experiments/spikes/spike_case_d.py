"""反証ケースD：疎配合を使わない二段Chain。

Task X -> Task Y の2段Chain（決定論的Stageなし、疎配合なし、外部入力はスカラーのみ）を
本番のChain Core経路へ通し、どこで成立しなくなるかを測定する。

測定するもの:
  - ChainDefinition / binding / ChainRevision は再利用できるか
  - Chain Projectを作れるか
  - 候補契約API / 初期候補API / 候補保存 / 実行 / snapshot / 不確かさ伝播はどこで落ちるか
  - 外部入力を welding_context 以外の名前空間で渡せるか
"""
from __future__ import annotations

import json
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

SCRATCH = _work_dir("case-d")
PRODUCTION_TASK_DEFINITIONS = (
    REPO / "backend" / "src" / "decision_workbench" / "tasks" / "task_definitions"
)
CHAIN_ID = "spike-scalar-x-y-v1"
STAGE_X = "spike-stage-x-v1"
STAGE_Y = "spike-stage-y-v1"
ROWS = 260

# Stage X: 成形条件 -> 収縮率
X_NUMERIC = (
    ("barrel_temperature_c", "°C", (150.0, 320.0), (200.0, 280.0), 1),
    ("hold_pressure_mpa", "MPa", (5.0, 160.0), (20.0, 120.0), 1),
    ("mold_temperature_c", "°C", (20.0, 160.0), (30.0, 110.0), 1),
    ("cooling_time_s", "s", (1.0, 90.0), (5.0, 45.0), 2),
)
RESIN_GRADES = ("grade_a", "grade_b", "grade_c")
# Stage Y: 収縮率（上流出力）＋焼鈍温度 -> 平面度
Y_NUMERIC = (
    ("shrinkage_pct", "%", (0.0, 5.0), (0.2, 1.6), 3),
    ("anneal_temperature_c", "°C", (40.0, 220.0), (60.0, 180.0), 1),
)


def _range(name: str, unit: str, allowed, default, order: int, training) -> dict:
    return {
        "path": f"process.{name}",
        "kind": "number",
        "order": order,
        "label": name,
        "unit": unit,
        "required": True,
        "editable": True,
        "default_range": {"min": default[0], "max": default[1]},
        "allowed_range": {"min": allowed[0], "max": allowed[1]},
        "training_range": {"min": training[0], "max": training[1]},
    }


def generate_stage_x(path: Path) -> dict[str, tuple[float, float]]:
    rng = random.Random(1013)
    seen: dict[str, list[float]] = {name: [] for name, *_ in X_NUMERIC}
    lines = [
        "run_id,"
        + ",".join(name for name, *_ in X_NUMERIC)
        + ",resin_grade,shrinkage_pct"
    ]
    for index in range(ROWS):
        values = {}
        for name, _unit, _allowed, (low, high), _dec in X_NUMERIC:
            values[name] = rng.uniform(low, high)
            seen[name].append(values[name])
        resin = RESIN_GRADES[index % len(RESIN_GRADES)]
        offset = {"grade_a": 0.0, "grade_b": 0.18, "grade_c": -0.12}[resin]
        shrinkage = max(
            0.02,
            0.55
            + 0.0031 * (values["barrel_temperature_c"] - 240.0)
            - 0.0042 * (values["hold_pressure_mpa"] - 70.0)
            + 0.0021 * (values["mold_temperature_c"] - 70.0)
            - 0.0035 * (values["cooling_time_s"] - 25.0)
            + offset
            + rng.gauss(0.0, 0.02),
        )
        lines.append(
            f"run-{index:04d},"
            + ",".join(f"{values[name]:.4f}" for name, *_ in X_NUMERIC)
            + f",{resin},{shrinkage:.4f}"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {name: (min(series), max(series)) for name, series in seen.items()}


def generate_stage_y(path: Path) -> dict[str, tuple[float, float]]:
    rng = random.Random(2027)
    seen: dict[str, list[float]] = {name: [] for name, *_ in Y_NUMERIC}
    lines = ["run_id," + ",".join(name for name, *_ in Y_NUMERIC) + ",flatness_um"]
    for index in range(ROWS):
        values = {}
        for name, _unit, _allowed, (low, high), _dec in Y_NUMERIC:
            values[name] = rng.uniform(low, high)
            seen[name].append(values[name])
        flatness = max(
            1.0,
            18.0
            + 42.0 * (values["shrinkage_pct"] - 0.8)
            - 0.11 * (values["anneal_temperature_c"] - 120.0)
            + rng.gauss(0.0, 1.2),
        )
        lines.append(
            f"run-{index:04d},"
            + ",".join(f"{values[name]:.4f}" for name, *_ in Y_NUMERIC)
            + f",{flatness:.3f}"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {name: (min(series), max(series)) for name, series in seen.items()}


def _target_capability(key: str) -> dict:
    return {
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


def _operations() -> dict:
    return {
        "preview": True,
        "detailed_prediction": True,
        "response_curve": False,
        "similarity": True,
        "snapshot": True,
        "actual_measurement": False,
    }


def stage_x_contract(training) -> dict:
    fields = [
        _range(name, unit, allowed, default, order, training[name])
        for order, (name, unit, allowed, default, _dec) in enumerate(X_NUMERIC)
    ]
    return {
        "task_definition": {
            "schema_version": "task-definition/v1",
            "id": STAGE_X,
            "label": "スパイクStage X：成形条件→収縮率",
            "canonical_candidate_schema_version": "canonical-candidate/v1",
            "input_groups": [
                {"key": "process", "order": 0, "label": "成形条件", "fields": fields},
                {
                    "key": "categorical",
                    "order": 1,
                    "label": "材料区分",
                    "fields": [
                        {
                            "path": "categorical.resin_grade",
                            "kind": "categorical",
                            "order": 0,
                            "label": "樹脂グレード",
                            "required": True,
                            "editable": True,
                            "choices": list(RESIN_GRADES),
                        }
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
                }
            ],
            "display_decimals": {
                **{f"process.{name}": dec for name, _u, _a, _d, dec in X_NUMERIC},
                "output.shrinkage_pct": 3,
            },
        },
        "canonical_candidate": {
            "schema_version": "canonical-candidate/v1",
            "task_id": STAGE_X,
            "composition": {},
            "process": {
                name: round((default[0] + default[1]) / 2, 4)
                for name, _u, _a, default, _dec in X_NUMERIC
            },
            "heat_pattern": None,
            "categorical": {"resin_grade": "grade_a"},
            "provenance": {"source_kind": "direct", "source_ref": None},
        },
        "runtime_capability": {
            "schema_version": "runtime-capability/v1",
            "task_id": STAGE_X,
            "model_package_schema_version": "model-package/v1",
            "targets": [_target_capability("shrinkage_pct")],
            "joint_samples": False,
            "operations": _operations(),
        },
    }


def stage_y_contract(training) -> dict:
    fields = [
        _range(name, unit, allowed, default, order, training[name])
        for order, (name, unit, allowed, default, _dec) in enumerate(Y_NUMERIC)
    ]
    return {
        "task_definition": {
            "schema_version": "task-definition/v1",
            "id": STAGE_Y,
            "label": "スパイクStage Y：収縮率→平面度",
            "canonical_candidate_schema_version": "canonical-candidate/v1",
            "input_groups": [
                {"key": "process", "order": 0, "label": "中間状態と後工程", "fields": fields}
            ],
            "outputs": [
                {
                    "key": "flatness_um",
                    "label": "平面度",
                    "unit": "µm",
                    "goal_direction": "at_most",
                    "measurement_keys": ["flatness_um"],
                    "plausibility_range": {"min": 0, "max": 400},
                    "preferred_display_range": {"min": 0, "max": 200},
                }
            ],
            "display_decimals": {
                **{f"process.{name}": dec for name, _u, _a, _d, dec in Y_NUMERIC},
                "output.flatness_um": 2,
            },
        },
        "canonical_candidate": {
            "schema_version": "canonical-candidate/v1",
            "task_id": STAGE_Y,
            "composition": {},
            "process": {
                name: round((default[0] + default[1]) / 2, 4)
                for name, _u, _a, default, _dec in Y_NUMERIC
            },
            "heat_pattern": None,
            "categorical": {},
            "provenance": {"source_kind": "direct", "source_ref": None},
        },
        "runtime_capability": {
            "schema_version": "runtime-capability/v1",
            "task_id": STAGE_Y,
            "model_package_schema_version": "model-package/v1",
            "targets": [_target_capability("flatness_um")],
            "joint_samples": False,
            "operations": _operations(),
        },
    }


def stage_x_profile() -> dict:
    return {
        "schema_version": "tabular-dataset-profile/v1",
        "profile_id": f"{STAGE_X}-profile",
        "name": "スパイクStage X",
        "task_id": STAGE_X,
        "package_id": f"{STAGE_X}-ridge",
        "id_column": "run_id",
        "group_column": None,
        "inputs": [
            *(
                {"path": f"process.{name}", "column": name, "kind": "number", "unit": unit}
                for name, unit, _a, _d, _dec in X_NUMERIC
            ),
            {
                "path": "categorical.resin_grade",
                "column": "resin_grade",
                "kind": "categorical",
                "choices": list(RESIN_GRADES),
            },
        ],
        "outputs": [
            {"key": "shrinkage_pct", "column": "shrinkage_pct", "unit": "%", "lower_bound": 0}
        ],
    }


def stage_y_profile() -> dict:
    return {
        "schema_version": "tabular-dataset-profile/v1",
        "profile_id": f"{STAGE_Y}-profile",
        "name": "スパイクStage Y",
        "task_id": STAGE_Y,
        "package_id": f"{STAGE_Y}-ridge",
        "id_column": "run_id",
        "group_column": None,
        "inputs": [
            {"path": f"process.{name}", "column": name, "kind": "number", "unit": unit}
            for name, unit, _a, _d, _dec in Y_NUMERIC
        ],
        "outputs": [
            {"key": "flatness_um", "column": "flatness_um", "unit": "µm", "lower_bound": 0}
        ],
    }


def main() -> int:
    from types import MappingProxyType

    from decision_workbench import app as app_module
    import decision_workbench.bootstrap.resources as resources_module
    from decision_workbench.task_composition.builtin.shared import (
        _application_capability,
    )
    from decision_workbench.task_composition.builtin.tabular import (
        _TABULAR_PROFILES,
        _tabular_features,
        _tabular_loader,
        _tabular_runtime,
        _tabular_training_candidate,
    )
    import decision_workbench.task_composition.catalog as task_catalog
    from decision_workbench.task_composition.catalog import registered_task_modules
    from decision_workbench.task_composition.descriptors import (
        StandardModelAuthoring,
        TaskModule,
    )
    from decision_workbench.modeling.model_lifecycle import (
        ACTIVE_PACKAGES_PATH,
        load_active_packages,
    )

    SCRATCH.mkdir(parents=True, exist_ok=True)
    findings: list[str] = []
    installed: list[Path] = []
    original_modules = resources_module.registered_task_modules
    original_builtin_modules = resources_module.BUILTIN_TASK_MODULES
    original_table = task_catalog.TASK_MODULES

    x_csv = SCRATCH / "stage_x.csv"
    y_csv = SCRATCH / "stage_y.csv"
    x_training = generate_stage_x(x_csv)
    y_training = generate_stage_y(y_csv)
    specs = (
        (STAGE_X, x_csv, stage_x_profile(), stage_x_contract(x_training)),
        (STAGE_Y, y_csv, stage_y_profile(), stage_y_contract(y_training)),
    )
    try:
        modules = dict(registered_task_modules())
        for task_id, csv_path, profile_doc, contract_doc in specs:
            profile_path = SCRATCH / f"profile-{task_id}.json"
            profile_path.write_text(
                json.dumps(profile_doc, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            target = PRODUCTION_TASK_DEFINITIONS / f"{task_id}.json"
            target.write_text(
                json.dumps(contract_doc, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            installed.append(target)
            _TABULAR_PROFILES[task_id] = profile_path
            modules[task_id] = TaskModule(
                task_id=task_id,
                package_override_env=f"DECISION_WORKBENCH_{task_id.replace('-', '_').upper()}_PACKAGE",
                source_env=f"WORKBENCH_{task_id.replace('-', '_').upper()}_SOURCE_PATH",
                source_kind=task_id,
                default_source=csv_path,
                data_loader=_tabular_loader(task_id),
                runtime_factory=_tabular_runtime,
                feature_row_builder=_tabular_features(task_id),
                standard_model_authoring=StandardModelAuthoring(
                    _tabular_training_candidate,
                    ("ridge.v1", "lightgbm-regression.v1"),
                    default_estimator_id="ridge.v1",
                ),
                application=_application_capability(
                    actual_measurement=False,
                    response_curve=False,
                    similarity=True,
                ),
            )
        resources_module.registered_task_modules = lambda: modules
        resources_module.BUILTIN_TASK_MODULES = modules
        task_catalog.TASK_MODULES = MappingProxyType(modules)

        from operations.model_workflow import build_package

        overrides = {
            task_id: str((ACTIVE_PACKAGES_PATH.parent / selection.active).resolve())
            for task_id, selection in load_active_packages(ACTIVE_PACKAGES_PATH).tasks.items()
        }
        for task_id, csv_path, _profile_doc, _contract_doc in specs:
            package_root = SCRATCH / f"package-{task_id}"
            if package_root.exists():
                shutil.rmtree(package_root)
            build_package(
                task_id,
                csv_path,
                package_root,
                SCRATCH / f"feature-dataset-{task_id}.json",
                package_id=f"{task_id}-ridge-v1",
                package_version="1.0.0",
                replace=True,
                estimator="ridge.v1",
                profile=_TABULAR_PROFILES[task_id],
            )
            overrides[task_id] = str(package_root.resolve())

        temp_active = SCRATCH / "active-packages.json"
        temp_active.write_text(
            json.dumps(
                {
                    "schema_version": "active-model-packages/v1",
                    "tasks": {task_id: {"active": "packages/spike", "previous": None} for task_id in modules},
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        resources = resources_module.prepare_app_resources(
            package_roots=overrides, active_packages_path=temp_active
        )
        for task_id in (STAGE_X, STAGE_Y):
            availability = resources.task_registry.availability_for(task_id)
            print(f"[registry] {task_id}: {availability.status} {availability.message}")
            if availability.status != "available":
                findings.append(f"{task_id} が利用不可: {availability.message}")
                return _report(findings)

        _probe_chain(app_module, resources, findings)
        return _report(findings)
    except Exception:
        traceback.print_exc()
        findings.append("スパイクが想定外の例外で停止した（tracebackが実測結果）")
        return _report(findings)
    finally:
        resources_module.registered_task_modules = original_modules
        resources_module.BUILTIN_TASK_MODULES = original_builtin_modules
        task_catalog.TASK_MODULES = original_table
        for task_id in (STAGE_X, STAGE_Y):
            _TABULAR_PROFILES.pop(task_id, None)
        for path in installed:
            if path.exists():
                path.unlink()


def _probe_chain(app_module, resources, findings: list[str]) -> None:
    from fastapi.testclient import TestClient
    from decision_workbench.contracts.chain_contracts import (
        ChainBinding,
        ChainDefinition,
        ChainPort,
        ChainStage,
        ChainStageLock,
        ExternalBindingSource,
        StageOutputBindingSource,
        build_chain_revision,
        task_contract_surface,
        validate_chain_definition,
    )
    from decision_workbench.execution.inference_work_graph import semantic_digest

    registry = resources.task_registry

    def surface(task_id: str):
        definition = registry.contract_for(task_id).task_definition
        return task_contract_surface(
            definition,
            contract_digest=semantic_digest(definition.model_dump(mode="json")),
        )

    surface_x = surface(STAGE_X)
    surface_y = surface(STAGE_Y)
    contracts = {
        ("task", STAGE_X): surface_x,
        ("task", STAGE_Y): surface_y,
    }

    external = tuple(
        [
            ChainPort(
                path=f"candidate.process.{name}",
                value_kind="number",
                quantity=name,
                unit=unit,
            )
            for name, unit, _a, _d, _dec in X_NUMERIC
        ]
        + [
            ChainPort(
                path="candidate.categorical.resin_grade",
                value_kind="categorical",
                quantity="resin_grade",
            ),
            ChainPort(
                path="candidate.process.anneal_temperature_c",
                value_kind="number",
                quantity="anneal_temperature_c",
                unit="°C",
            ),
        ]
    )
    bindings = tuple(
        [
            ChainBinding(
                target_stage_id="X",
                target_input_path=f"process.{name}",
                source=ExternalBindingSource(
                    source_kind="external", path=f"candidate.process.{name}"
                ),
            )
            for name, *_ in X_NUMERIC
        ]
        + [
            ChainBinding(
                target_stage_id="X",
                target_input_path="categorical.resin_grade",
                source=ExternalBindingSource(
                    source_kind="external", path="candidate.categorical.resin_grade"
                ),
            ),
            ChainBinding(
                target_stage_id="Y",
                target_input_path="process.shrinkage_pct",
                source=StageOutputBindingSource(
                    source_kind="stage_output", stage_id="X", output_key="shrinkage_pct"
                ),
            ),
            ChainBinding(
                target_stage_id="Y",
                target_input_path="process.anneal_temperature_c",
                source=ExternalBindingSource(
                    source_kind="external", path="candidate.process.anneal_temperature_c"
                ),
            ),
        ]
    )
    definition = ChainDefinition(
        chain_id=CHAIN_ID,
        label="スパイク：成形条件→収縮率→平面度",
        stages=(
            ChainStage(stage_id="X", stage_kind="task", contract_id=STAGE_X),
            ChainStage(stage_id="Y", stage_kind="task", contract_id=STAGE_Y),
        ),
        external_inputs=external,
        bindings=bindings,
    )
    try:
        validate_chain_definition(definition, contracts=contracts)
        _check(findings, "ChainDefinition検証（2段task・決定論的Stageなし）", True)
    except Exception as exc:
        _check(findings, "ChainDefinition検証（2段task・決定論的Stageなし）", False, exc)
        return

    fake_profile = "sha256:" + "0" * 64
    try:
        revision = build_chain_revision(
            definition,
            revision=1,
            contracts=contracts,
            stage_locks={
                stage_id: ChainStageLock(
                    contract_digest=contracts[("task", task_id)].contract_digest,
                    package_manifest_digest=(
                        f"sha256:{registry.entry_for(task_id).model_package.manifest_sha256}"
                    ),
                    dataset_view_revision_id=f"spike-view-{stage_id}",
                    dataset_profile_digest=fake_profile,
                )
                for stage_id, task_id in (("X", STAGE_X), ("Y", STAGE_Y))
            },
        )
        _check(findings, "ChainRevision構築", True)
    except Exception as exc:
        _check(findings, "ChainRevision構築", False, exc)
        return

    database = SCRATCH / "spike.db"
    if database.exists():
        database.unlink()
    app = app_module.create_app(
        db_path=database,
        data_library_path=SCRATCH / "data-library",
        _resources=resources,
    )
    with TestClient(app) as client:
        store = client.app.state.store
        try:
            store.register_chain_definition(definition)
            revision_id = store.register_chain_revision(revision, contracts=contracts)
            _check(findings, "Chain Definition / Revisionの登録", True)
        except Exception as exc:
            _check(findings, "Chain Definition / Revisionの登録", False, exc)
            return

        created = client.post(
            "/api/projects",
            json={
                "name": "スパイク二段Chain",
                "scientific_identity": {
                    "identity_kind": "chain",
                    "chain_revision_id": revision_id,
                    "chain_revision_digest": revision.revision_digest,
                },
            },
        )
        _check(findings, "Chain Project作成", created.status_code == 201, created.text[:300])
        if created.status_code != 201:
            return
        project_id = created.json()["id"]

        capability_response = client.get(
            f"/api/projects/{project_id}/chain/candidate-capability"
        )
        _check(
            findings,
            "候補入力capability API（GET chain/candidate-capability）",
            capability_response.status_code == 200,
            capability_response.text[:300],
        )
        if capability_response.status_code == 200:
            capability_body = capability_response.json()
            _check(
                findings,
                "capabilityが疎配合不要のadapterを宣言する",
                capability_body["adapter_id"] == "scalar/v1"
                and capability_body["sparse_blend"] is False,
                capability_body,
            )

        contract_response = client.get(f"/api/projects/{project_id}/chain/candidate-contract")
        _check(
            findings,
            "疎配合契約APIは疎配合を使わないChainで明示的に拒否される",
            contract_response.status_code == 409
            and "疎な配合明細を使いません" in contract_response.text,
            contract_response.text[:300],
        )

        listed = client.get(f"/api/projects/{project_id}/chain/candidates")
        _check(
            findings,
            "Chain候補一覧（GET chain/candidates）",
            listed.status_code == 200,
            listed.text[:300],
        )

        payload = {
            "name": "スカラー候補",
            "inputs": {
                "composition": {},
                "process": {
                    **{
                        name: round((default[0] + default[1]) / 2, 4)
                        for name, _u, _a, default, _dec in X_NUMERIC
                    },
                    "anneal_temperature_c": 120.0,
                },
                "categorical": {"resin_grade": "grade_a"},
                "heat_pattern": None,
            },
        }
        candidate = client.post(f"/api/projects/{project_id}/chain/candidates", json=payload)
        _check(
            findings,
            "Chain候補の保存（POST chain/candidates）",
            candidate.status_code == 201,
            candidate.text[:300],
        )

        service = client.app.state.chain_planning_use_case
        try:
            starter = service.starter_candidate(project_id)
            _check(
                findings,
                "初期候補生成（starter_candidate）",
                starter.blend is None and bool(starter.inputs.process),
                f"blend={starter.blend} process={sorted(starter.inputs.process)}",
            )
        except Exception as exc:
            _check(findings, "初期候補生成（starter_candidate）", False, exc)

        if candidate.status_code == 201:
            saved = candidate.json()
            executed = client.post(
                f"/api/projects/{project_id}/chain/candidates/{saved['id']}/executions",
                json={"candidate_revision": saved["revision"], "debounce_ms": 0},
            )
            _check(
                findings,
                "Chain実行（POST chain/candidates/{id}/executions）",
                executed.status_code == 200
                and executed.json()["status"] == "latest",
                _stage_errors(executed) or executed.text[:400],
            )
            if executed.status_code == 200 and executed.json()["status"] == "latest":
                stage_ids = [stage["stage_id"] for stage in executed.json()["stages"]]
                _check(findings, "2 Stageが順に実行される", stage_ids == ["X", "Y"], stage_ids)
                snapshot = client.post(
                    f"/api/projects/{project_id}/chain/candidates/{saved['id']}/snapshots",
                    json={"candidate_revision": saved["revision"], "debounce_ms": 0},
                )
                _check(
                    findings,
                    "Chain snapshotの保存（疎配合参照なし）",
                    snapshot.status_code == 201,
                    snapshot.text[:400],
                )
                if snapshot.status_code == 201:
                    identity = snapshot.json()["identity"]
                    _check(
                        findings,
                        "snapshot identityがadapterを記録し、domain参照を持たない",
                        identity["schema_version"] == "chain-snapshot-identity/v2"
                        and identity["candidate_adapter_id"] == "scalar/v1"
                        and identity["domain_references"] == [],
                        identity,
                    )
                    predicted_shrinkage = executed.json()["stages"][0]["result"][
                        "predictions"
                    ]["shrinkage_pct"]["value"]
                    variant = client.post(
                        f"/api/projects/{project_id}/chain/candidates/"
                        f"{saved['id']}/analysis-variants",
                        json={
                            "candidate_revision": saved["revision"],
                            "comparison_snapshot_id": snapshot.json()["snapshot_id"],
                            "actual_records": [
                                {
                                    "actual_id": "MOLD-001",
                                    "values": {
                                        "shrinkage_pct": predicted_shrinkage + 0.01
                                    },
                                }
                            ],
                        },
                    )
                    _check(
                        findings,
                        "中間実測をprocess入力へ適用したvariantを作成する",
                        variant.status_code == 201
                        and variant.json()["stage_c_input"]["process"][
                            "shrinkage_pct"
                        ]
                        == predicted_shrinkage + 0.01
                        and "composition" not in variant.json()["stage_c_input"],
                        variant.text[:400],
                    )
                    if variant.status_code == 201:
                        from decision_workbench.persistence.store import Store

                        restored = Store(
                            client.app.state.store.path
                        ).get_chain_analysis_variant(variant.json()["variant_id"])
                        _check(
                            findings,
                            "scalar Chainのactual-conditioned variantをDBから復元する",
                            restored is not None
                            and restored.model_dump(mode="json") == variant.json(),
                            restored,
                        )

        from decision_workbench.contracts.candidate_project_contracts import (
    CandidateInput,
    CandidateInputs,
)

        try:
            service.prepare_candidate(
                project_id,
                CandidateInput(
                    name="スカラー候補",
                    inputs=CandidateInputs(
                        composition={},
                        process={
                            **{
                                name: round((default[0] + default[1]) / 2, 4)
                                for name, _u, _a, default, _dec in X_NUMERIC
                            },
                            "anneal_temperature_c": 120.0,
                        },
                        categorical={"resin_grade": "grade_a"},
                        heat_pattern=None,
                    ),
                ),
            )
            _check(findings, "候補の妥当性検証（prepare_candidate）", True)
        except Exception as exc:
            _check(findings, "候補の妥当性検証（prepare_candidate）", False, exc)

        capability = client.get(f"/api/projects/{project_id}/chain/distribution-capability")
        _check(
            findings,
            "不確かさ伝播capability",
            capability.status_code == 200,
            capability.text[:300],
        )

        # binding解決に使われる外部入力の名前空間をadapter経由で確認する。
        adapter = service.candidate_adapter(project_id)

        scalar_candidate = CandidateInput(
            name="スカラー候補",
            inputs=CandidateInputs(
                composition={},
                process={
                    **{
                        name: round((default[0] + default[1]) / 2, 4)
                        for name, _u, _a, default, _dec in X_NUMERIC
                    },
                    "anneal_temperature_c": 120.0,
                },
                categorical={"resin_grade": "grade_a"},
                heat_pattern=None,
            ),
        )
        produced = set(adapter.external_values(scalar_candidate))
        required = {port.path for port in definition.external_inputs}
        missing = sorted(required - produced)
        _check(
            findings,
            "外部入力を welding_context 以外の名前空間で渡せる",
            not missing,
            f"adapterが生成しないpath={missing} / 生成したpath例={sorted(produced)[:3]}",
        )

        # snapshot identityが疎配合参照を必須にしていないことを契約レベルで確認する
        from decision_workbench.contracts.chain_contracts import ChainSnapshotIdentityV2

        try:
            ChainSnapshotIdentityV2(
                chain_revision_id=revision_id,
                chain_revision_digest=revision.revision_digest,
                candidate_id="spike-candidate",
                candidate_revision=1,
                candidate_adapter_id=adapter.adapter_id,
            )
            _check(findings, "疎配合参照なしでChain snapshot identityを作れる", True)
        except Exception as exc:
            _check(
                findings,
                "疎配合参照なしでChain snapshot identityを作れる",
                False,
                str(exc).splitlines()[0],
            )


def _stage_errors(response) -> str:
    try:
        stages = response.json().get("stages", [])
    except ValueError:
        return ""
    return "; ".join(
        f"{stage['stage_id']}: {stage['error']}"
        for stage in stages
        if stage.get("error")
    )


def _check(findings: list[str], label: str, ok: bool, detail: object = "") -> None:
    print(f"[{'OK  ' if ok else 'FAIL'}] {label}" + (f"\n        -> {detail}" if not ok and detail else ""))
    if not ok:
        findings.append(f"{label} :: {detail}")


def _report(findings: list[str]) -> int:
    print("\n=== 発見事項 ===")
    if not findings:
        print("なし（Chain Coreの変更なしで二段Chainが成立した）")
    for item in findings:
        print(f"- {item}")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
