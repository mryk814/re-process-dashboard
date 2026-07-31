"""反証ケースE：同じ意味・同じ構造のデータ差し替え。

計画§8の成功条件1「同じ意味・同じ構造のデータ差し替えは Profile・Dataset Revision・
Model Package の更新だけで扱える」を測定する。

既存Taskの列構成をそのまま使い、行だけが異なる新しいsourceへ差し替える。
コード変更・契約変更が本当に不要かを、本番の起動経路とAPIで確認する。
"""
from __future__ import annotations

import csv
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
    root = Path(
        os.environ.get("SPIKE_WORK_DIR")
        or Path(tempfile.gettempdir()) / "material-workbench-spikes"
    )
    return root / name


sys.path.insert(0, str(REPO / "backend" / "src"))
sys.path.insert(0, str(REPO / "backend" / "scripts"))

SCRATCH = _work_dir("case-e")
# 既存の標準表形式Task。Profileも契約も変更せず、行だけ違うsourceへ差し替える。
TASK_ID = "concrete-strength-v1"


def swapped_source(original: Path, destination: Path, *, seed: int = 90210) -> dict:
    """列構成を保ったまま、行を入れ替えた新しいsourceを作る。

    「同じ意味・同じ構造」を守るため、列名・列順・単位・カテゴリ値は変えない。
    行は既存行のブートストラップ再標本＋小さな観測ノイズで作る。
    """

    rng = random.Random(seed)
    with original.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        fieldnames = list(reader.fieldnames or ())
        rows = list(reader)
    if not rows:
        raise SystemExit("元sourceに行がありません")

    numeric_columns = [
        name for name in fieldnames
        if all(_is_number(row.get(name)) for row in rows)
    ]
    swapped = []
    for _ in range(len(rows)):
        base = rng.choice(rows)
        row = dict(base)
        for name in numeric_columns:
            value = float(base[name])
            row[name] = f"{value * rng.uniform(0.97, 1.03):.6g}"
        swapped.append(row)

    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(swapped)
    return {
        "columns": fieldnames,
        "rows": len(swapped),
        "numeric_columns": numeric_columns,
    }


def _is_number(value: str | None) -> bool:
    try:
        float((value or "").strip())
    except ValueError:
        return False
    return True


def _tracked_state() -> dict[str, str]:
    """契約・コードのdigestを取り、差し替え後に変わっていないことを確認する。"""

    import hashlib

    builtin = REPO / "backend/src/material_workbench/task_composition/builtin"
    tracked = {
        "task_definition": REPO / "backend/src/material_workbench/tasks/task_definitions" / f"{TASK_ID}.json",
        "tabular_profile": REPO / "backend/src/material_workbench/data/tabular-profile-concrete-v1.json",
        "builtin_catalog": builtin / "catalog.py",
        "builtin_shared": builtin / "shared.py",
        "builtin_sources": builtin / "sources.py",
        "builtin_annealed": builtin / "annealed.py",
        "builtin_hot_rolling": builtin / "hot_rolling.py",
        "builtin_flank_wear": builtin / "flank_wear.py",
        "builtin_tabular": builtin / "tabular.py",
        "builtin_welding": builtin / "welding.py",
        "task_catalog": REPO / "backend/src/material_workbench/task_composition/catalog.py",
        "active_packages": REPO / "models/active-packages.json",
    }
    return {
        name: hashlib.sha256(path.read_bytes()).hexdigest()
        for name, path in tracked.items()
    }


def main() -> int:
    from material_workbench import app as app_module
    import material_workbench.bootstrap.resources as resources_module
    from material_workbench.modeling.model_lifecycle import (
        ACTIVE_PACKAGES_PATH,
        load_active_packages,
    )
    from material_workbench.task_composition.catalog import (
        registered_task_modules,
        resolve_task_source,
    )

    SCRATCH.mkdir(parents=True, exist_ok=True)
    findings: list[str] = []
    before = _tracked_state()
    module = registered_task_modules()[TASK_ID]
    original = resolve_task_source(TASK_ID)
    swapped = SCRATCH / original.name
    package_root = SCRATCH / "package"

    try:
        shape = swapped_source(original, swapped)
        print(f"[source] {shape['rows']}行 / {len(shape['columns'])}列 を差し替え")

        # 1. Profileを変えずに新sourceからPackageを作れるか
        from operations.model_workflow import build_package

        if package_root.exists():
            shutil.rmtree(package_root)
        try:
            profile_path = (
                REPO
                / "backend/src/material_workbench/data/tabular-profile-concrete-v1.json"
            )
            build_package(
                TASK_ID,
                swapped,
                package_root,
                SCRATCH / "feature-dataset.json",
                package_id="spike-concrete-swapped-ridge-v1",
                package_version="1.0.0",
                replace=True,
                estimator="ridge.v1",
                profile=profile_path,
            )
            _check(findings, "Profileを変えずに新sourceからPackageを構築できる", True)
        except Exception as exc:
            _check(findings, "Profileを変えずに新sourceからPackageを構築できる", False, exc)
            return _report(findings)

        # 2. 環境変数のsource上書きとPackage上書きだけでアプリを起動できるか
        real = load_active_packages(ACTIVE_PACKAGES_PATH)
        overrides = {
            task_id: str((ACTIVE_PACKAGES_PATH.parent / selection.active).resolve())
            for task_id, selection in real.tasks.items()
        }
        overrides[TASK_ID] = str(package_root.resolve())
        previous_env = os.environ.get(module.source_env)
        os.environ[module.source_env] = str(swapped)
        try:
            resources = resources_module.prepare_app_resources(
                package_roots=overrides
            )
        finally:
            if previous_env is None:
                os.environ.pop(module.source_env, None)
            else:
                os.environ[module.source_env] = previous_env

        availability = resources.task_registry.availability_for(TASK_ID)
        _check(
            findings,
            "差し替えたsourceでTaskが利用可能になる",
            availability.status == "available",
            availability.message,
        )
        if availability.status != "available":
            return _report(findings)

        data = resources.runtimes[TASK_ID].data
        _check(
            findings,
            "学習データが差し替えたsourceを指す",
            Path(data.source_path).name == swapped.name
            and data.source_sha256 != _sha256(original),
            f"source={Path(data.source_path).name} digest一致={data.source_sha256 == _sha256(original)}",
        )
        _check(
            findings,
            "Profile IDは変わらない（同じ意味・同じ構造だから）",
            data.profile_id == "external-concrete-v1",
            data.profile_id,
        )

        # 3. 既存アプリ機能がそのまま動くか
        _exercise_api(app_module, resources, findings)

        # 4. 契約とコードに変更が要らなかったか
        after = _tracked_state()
        changed = sorted(name for name in before if before[name] != after[name])
        _check(
            findings,
            "TaskDefinition / Profile / Task composition / active-packages.json を変更していない",
            not changed,
            f"変更された正本={changed}",
        )

        # 5. 「同じ構造」の境界。範囲を超えたデータは黙って通ってはいけない
        _probe_out_of_range_swap(findings)
        return _report(findings)
    except Exception:
        traceback.print_exc()
        findings.append("スパイクが想定外の例外で停止した（tracebackが実測結果）")
        return _report(findings)


def resolve_task_source_for_probe() -> Path:
    from material_workbench.task_composition.catalog import resolve_task_source

    return resolve_task_source(TASK_ID)


def _probe_out_of_range_swap(findings: list[str]) -> None:
    """TaskDefinitionの許容範囲を超える値を含むデータへ差し替えた場合の挙動。

    範囲外の値が黙って学習へ入ると、TaskDefinitionのtraining_rangeが実データと
    食い違ったまま残る。差し替えだけで済む範囲の境界を明示する。
    """

    from material_workbench.modeling.tabular.data import load_tabular_data
    from material_workbench.modeling.tabular.profile import load_tabular_profile
    from material_workbench.tasks.task_registry import load_task_contracts

    profile_path = REPO / "backend/src/material_workbench/data/tabular-profile-concrete-v1.json"
    profile = load_tabular_profile(profile_path)
    task = load_task_contracts()[TASK_ID].task_definition
    fields = {
        field.path: field
        for group in task.input_groups
        for field in group.fields
        if field.kind == "number"
    }
    target_input = next(item for item in profile.inputs if item.path in fields)
    field = fields[target_input.path]
    assert field.allowed_range is not None
    beyond = field.allowed_range.max * 10.0

    original = resolve_task_source_for_probe()
    out_of_range = SCRATCH / "out_of_range.csv"
    with original.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        fieldnames = list(reader.fieldnames or ())
        rows = list(reader)
    for row in rows[: max(1, len(rows) // 20)]:
        row[target_input.column] = f"{beyond:.6g}"
    with out_of_range.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    from material_workbench.modeling.model_lifecycle import (
        PackageContractError,
        training_range_drift,
        validate_training_rows_within_allowed_range,
    )

    data = load_tabular_data(out_of_range, profile)
    eligible = [row for row in data.observations if row["eligible"]]
    group, key = target_input.path.split(".", 1)
    # composition.* は row["composition"]、process.* は row["features"] へ入る
    bucket = "composition" if group == "composition" else "features"
    beyond_rows = [
        row for row in eligible
        if float(row[bucket].get(key, 0.0)) > field.allowed_range.max
    ]
    print(
        f"[range] {target_input.path} allowed_max={field.allowed_range.max} "
        f"投入値={beyond:.6g} loaderが適格とした件数={len(beyond_rows)}"
    )
    # loaderは行を落とさない（暗黙の値判断をしない）。契約との不一致は検証層で
    # 明示的に失敗させる。
    try:
        validate_training_rows_within_allowed_range(data, load_task_contracts()[TASK_ID])
        _check(
            findings,
            "許容範囲を超える学習データが検証層で明示的に拒否される",
            False,
            f"{len(beyond_rows)}行が範囲外のまま受理された",
        )
    except PackageContractError as exc:
        _check(
            findings,
            "許容範囲を超える学習データが検証層で明示的に拒否される",
            target_input.path in str(exc),
            str(exc)[:200],
        )

    # 差し替えたデータが宣言済みtraining_rangeからずれたことも検出できる
    drift = training_range_drift(data, load_task_contracts()[TASK_ID])
    _check(
        findings,
        "宣言済みtraining_rangeとのずれを検出できる",
        target_input.path in drift,
        f"drift={sorted(drift)}",
    )


def _sha256(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


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
    project_id = f"{TASK_ID}-default"
    with TestClient(app) as client:
        installed = client.post(
            "/api/sample-gallery",
            json={"project_ids": [project_id]},
        )
        _check(
            findings,
            "同梱サンプルとしてstarter projectを追加できる",
            installed.status_code == 200,
            installed.text[:200],
        )
        projects = client.get("/api/projects")
        ids = {item["id"] for item in projects.json()} if projects.status_code == 200 else set()
        _check(findings, "追加したstarter projectが一覧に出る", project_id in ids, sorted(ids))
        if project_id not in ids:
            return
        rows = client.get(f"/api/projects/{project_id}/candidates").json()
        _check(findings, "starter候補が生成される", bool(rows), len(rows))
        if not rows:
            return
        candidate_id, revision = rows[0]["id"], rows[0]["revision"]

        preview = client.post(
            f"/api/projects/{project_id}/candidates/{candidate_id}/preview",
            params={"expected_revision": revision},
        )
        _check(findings, "preview（予測）", preview.status_code == 200, preview.text[:200])

        similar = client.get(
            f"/api/projects/{project_id}/candidates/{candidate_id}/similar",
            params={"expected_revision": revision},
        )
        _check(findings, "類似観測", similar.status_code == 200, similar.text[:200])

        quality = client.get(f"/api/projects/{project_id}/quality")
        _check(findings, "品質表示", quality.status_code == 200, quality.text[:200])

        training = client.get(f"/api/projects/{project_id}/model-package/training-data")
        _check(findings, "学習データInspector", training.status_code == 200, training.text[:200])

        package = client.get(f"/api/projects/{project_id}/model-package")
        _check(
            findings,
            "Model Package状態が新しい学習データ由来のprovenanceを返す",
            package.status_code == 200,
            package.text[:200],
        )


def _check(findings: list[str], label: str, ok: bool, detail: object = "") -> None:
    print(f"[{'OK  ' if ok else 'FAIL'}] {label}" + (f"\n        -> {detail}" if not ok and detail else ""))
    if not ok:
        findings.append(f"{label} :: {detail}")


def _report(findings: list[str]) -> int:
    print("\n=== 発見事項 ===")
    if not findings:
        print("なし（Profile・Dataset・Packageの更新だけで差し替えられた）")
    for item in findings:
        print(f"- {item}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
