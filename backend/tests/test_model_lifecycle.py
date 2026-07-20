from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from material_workbench.app import create_app
from material_workbench.importer import load_workbook_data
from material_workbench.model_lifecycle import (
    canonical_training_dataset,
    canonical_training_dataset_digest,
    exact_gp_loo_quality,
    load_active_packages,
    resolve_configured_package,
    rollback_active_package,
    set_active_package,
    staged_package_destination,
)
from material_workbench.model_package_verify import verify_model_package
from material_workbench.model_packages import PackageContractError
from material_workbench.task_registry import load_task_contracts


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "data" / "source" / "process_dashboard_realistic_excel_v2.xlsx"


@pytest.mark.parametrize(
    ("task_id", "package_id"),
    [
        ("annealed-properties-v1", "annealed-gp-2026-07"),
        ("hot-rolled-properties-v1", "hot-rolled-gp-2026-07"),
    ],
)
def test_checked_in_package_passes_production_runtime_verification(task_id: str, package_id: str) -> None:
    report = verify_model_package(ROOT / "models" / "packages" / package_id, task_id=task_id, source=SOURCE)
    assert report.task_id == task_id
    assert report.package_id == package_id
    assert report.quality_report["split"] == "leave-one-parent-condition-out"


def test_canonical_training_dataset_is_deterministic_and_task_specific() -> None:
    data = load_workbook_data(SOURCE)
    contracts = load_task_contracts()
    annealed = canonical_training_dataset("annealed-properties-v1", data, contracts["annealed-properties-v1"])
    hot = canonical_training_dataset("hot-rolled-properties-v1", data, contracts["hot-rolled-properties-v1"])
    assert canonical_training_dataset_digest(annealed) == canonical_training_dataset_digest(
        canonical_training_dataset("annealed-properties-v1", data, contracts["annealed-properties-v1"])
    )
    assert {key for row in annealed["rows"] for key in row["outputs"]} == {"TS", "YS", "EL", "lambda"}
    assert {key for row in hot["rows"] for key in row["outputs"]} == {"TS"}


def test_verify_rejects_contract_digest_and_quality_report_corruption(tmp_path: Path) -> None:
    source = ROOT / "models" / "packages" / "hot-rolled-gp-2026-07"
    package = tmp_path / "package"
    import shutil

    shutil.copytree(source, package)
    manifest_path = package / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["input_contract_digest"] = "sha256:wrong"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(PackageContractError, match="input contract digest"):
        verify_model_package(package, task_id="hot-rolled-properties-v1", source=SOURCE)

    manifest["input_contract_digest"] = json.loads((source / "manifest.json").read_text(encoding="utf-8"))["input_contract_digest"]
    quality_path = package / manifest["quality_report"]
    quality_path.write_text(json.dumps({"schema_version": "model-quality-report/v1", "split": "leave-one-parent-condition-out", "targets": []}), encoding="utf-8")
    artifact = next(item for item in manifest["artifacts"] if item["path"] == manifest["quality_report"])
    artifact["bytes"] = quality_path.stat().st_size
    artifact["sha256"] = hashlib.sha256(quality_path.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(PackageContractError, match="quality report"):
        verify_model_package(package, task_id="hot-rolled-properties-v1", source=SOURCE)


def test_active_package_switch_and_rollback_are_atomic_and_reversible(tmp_path: Path) -> None:
    models = tmp_path / "models"
    first = models / "packages" / "first"
    second = models / "packages" / "second"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    config_path = models / "active-packages.json"
    config_path.write_text(json.dumps({
        "schema_version": "active-model-packages/v1",
        "tasks": {"annealed-properties-v1": {"active": "packages/first", "previous": None}},
    }), encoding="utf-8")

    switched = set_active_package("annealed-properties-v1", second, config_path=config_path)
    assert switched.tasks["annealed-properties-v1"].active == "packages/second"
    assert switched.tasks["annealed-properties-v1"].previous == "packages/first"
    rolled_back = rollback_active_package("annealed-properties-v1", config_path=config_path)
    assert rolled_back.tasks["annealed-properties-v1"].active == "packages/first"
    assert load_active_packages(config_path) == rolled_back


def test_failed_staged_build_preserves_existing_package(tmp_path: Path) -> None:
    destination = tmp_path / "package"
    destination.mkdir()
    (destination / "manifest.json").write_text('{"package_id":"current"}', encoding="utf-8")
    (destination / "current.marker").write_text("current", encoding="utf-8")

    with pytest.raises(RuntimeError, match="training failed"):
        with staged_package_destination(destination, replace=True) as staging:
            staging.mkdir()
            (staging / "manifest.json").write_text('{"package_id":"partial"}', encoding="utf-8")
            raise RuntimeError("training failed")

    assert (destination / "current.marker").read_text(encoding="utf-8") == "current"
    assert not destination.with_name(f".{destination.name}.building").exists()
    assert not destination.with_name(f".{destination.name}.previous-swap").exists()


def test_active_package_reference_cannot_escape_models_root(tmp_path: Path) -> None:
    models = tmp_path / "models"
    outside = tmp_path / "outside"
    outside.mkdir()
    config_path = models / "active-packages.json"
    models.mkdir()
    config_path.write_text(json.dumps({
        "schema_version": "active-model-packages/v1",
        "tasks": {"annealed-properties-v1": {"active": "../outside", "previous": None}},
    }), encoding="utf-8")

    with pytest.raises(PackageContractError, match="escapes"):
        resolve_configured_package("annealed-properties-v1", config_path=config_path)
    with pytest.raises(PackageContractError, match="trusted models directory"):
        set_active_package("annealed-properties-v1", outside, config_path=config_path)


def test_parent_mean_loo_coverage_does_not_add_observation_noise() -> None:
    import numpy as np

    metric = exact_gp_loo_quality(
        "TS",
        np.zeros(2),
        np.full(2, 2.0),
        np.eye(2),
    )
    assert metric.interval_coverage_90 == 0


def test_alternate_verified_package_needs_no_api_change_and_snapshot_keeps_old_identity(tmp_path: Path) -> None:
    import shutil

    original = ROOT / "models" / "packages" / "annealed-gp-2026-07"
    alternate = tmp_path / "annealed-gp-alternate"
    shutil.copytree(original, alternate)
    manifest_path = alternate / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["package_id"] = "annealed-gp-alternate"
    manifest["package_version"] = "alternate-test"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    database = tmp_path / "workbench.db"

    with TestClient(create_app(SOURCE, database, package_roots={"annealed-properties-v1": alternate})) as client:
        status = client.get("/api/projects/default/model-package").json()
        assert status["id"] == "annealed-gp-alternate"
        candidate = client.get("/api/candidates?project_id=default").json()[0]
        snapshot = client.post(f"/api/candidates/{candidate['id']}/snapshots").json()
        assert snapshot["payload"]["provenance"]["package"]["id"] == "annealed-gp-alternate"
        old_hash = snapshot["payload"]["provenance"]["package"]["manifest_sha256"]

    with TestClient(create_app(SOURCE, database)) as client:
        current = client.get("/api/projects/default/model-package").json()
        stored = client.get(f"/api/candidates/{candidate['id']}/snapshots").json()[0]
        assert current["id"] == "annealed-gp-2026-07"
        assert stored["payload"]["provenance"]["package"]["manifest_sha256"] == old_hash
        assert old_hash != current["manifest_sha256"]


def test_app_startup_rejects_package_trained_from_a_different_source(tmp_path: Path) -> None:
    import shutil

    source = ROOT / "models" / "packages" / "hot-rolled-gp-2026-07"
    package = tmp_path / "hot-rolled-gp"
    shutil.copytree(source, package)
    manifest_path = package / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["provenance"]["training_data_id"] = "sha256:different-source"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    app = create_app(SOURCE, tmp_path / "workbench.db", package_roots={"hot-rolled-properties-v1": package})
    with pytest.raises(PackageContractError, match="training data digest"):
        with TestClient(app):
            pass
