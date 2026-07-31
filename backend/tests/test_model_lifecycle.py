from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from material_workbench.app import create_app
from material_workbench.data.importer import load_workbook_data
from material_workbench.modeling.model_lifecycle import (
    QualityReport,
    SamplingDiagnosticsReport,
    canonical_training_dataset,
    canonical_training_dataset_digest,
    ensure_available_packages_config,
    exact_gp_loo_quality,
    load_active_packages,
    load_available_packages,
    personal_model_store_path,
    register_available_package,
    resolve_configured_package,
    rollback_active_package,
    set_active_package,
    staged_package_destination,
    validate_personal_model_store_path,
)
from material_workbench.modeling.model_package_verify import verify_model_package
from material_workbench.modeling.model_package_contracts import PackageContractError
from material_workbench.modeling.model_package_contracts import (
    FEATURE_DATASET_DIGEST_FLOAT15,
    FEATURE_DATASET_DIGEST_LEGACY,
)
from material_workbench.tasks.task_registry import load_task_contracts

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE = ROOT / "data" / "source" / "material_workbench_tutorial_v2.xlsx"
SOURCE = DEFAULT_SOURCE
PROCESS_SOURCE = ROOT / "data" / "source" / "material_workbench_process_v1.xlsx"
MPEA_SOURCE = ROOT / "data" / "source" / "external" / "mpea_ground_truth_18021833.csv"
PROCESS_ANNEALED_PACKAGE = ROOT / "models" / "packages" / "annealed-gp-stable-ard-process-v2"
PROCESS_HOT_PACKAGE = ROOT / "models" / "packages" / "hot-rolled-horseshoe-process-v2"


def test_personal_model_store_defaults_to_local_app_data(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("WORKBENCH_MODEL_STORE_PATH", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    store = personal_model_store_path()
    config = ensure_available_packages_config(store)

    assert store == (tmp_path / "Material Decision Workbench" / "models").resolve()
    assert load_available_packages(config).packages == ()


@pytest.mark.parametrize("relative", [".", "models", "artifacts/personal-models"])
def test_personal_model_store_rejects_repository_paths(relative: str) -> None:
    with pytest.raises(PackageContractError, match="outside the repository"):
        validate_personal_model_store_path(ROOT / relative)


def test_grouped_quality_report_requires_an_explicit_fold_count() -> None:
    with pytest.raises(ValueError, match="require folds"):
        QualityReport.model_validate(
            {
                "schema_version": "model-quality-report/v1",
                "split": "grouped-parent-condition-k-fold",
                "targets": [
                    {
                        "target": "TS",
                        "parent_conditions": 2,
                        "mae": 1,
                        "rmse": 1,
                        "interval_coverage_90": 0.9,
                    }
                ],
            }
        )


def test_trusted_package_can_be_registered_for_live_import(tmp_path: Path) -> None:
    models_root = tmp_path / "models"
    package = models_root / "packages" / "example-v1"
    package.mkdir(parents=True)
    available_path = models_root / "available-packages.json"
    available_path.write_text(
        '{"schema_version":"available-model-packages/v1","packages":[]}\n',
        encoding="utf-8",
    )

    first = register_available_package(package, config_path=available_path)
    second = register_available_package(package, config_path=available_path)

    assert first.packages == ("packages/example-v1",)
    assert second == first
    assert load_available_packages(available_path) == first


def test_available_package_registration_rejects_path_outside_models(
    tmp_path: Path,
) -> None:
    models_root = tmp_path / "models"
    models_root.mkdir()
    available_path = models_root / "available-packages.json"
    available_path.write_text(
        '{"schema_version":"available-model-packages/v1","packages":[]}\n',
        encoding="utf-8",
    )
    outside = tmp_path / "outside-package"
    outside.mkdir()

    with pytest.raises(PackageContractError, match="trusted models directory"):
        register_available_package(outside, config_path=available_path)


def test_sampling_diagnostics_reject_low_effective_sample_size() -> None:
    with pytest.raises(ValueError, match="greater than or equal to 50"):
        SamplingDiagnosticsReport.model_validate(
            {
                "schema_version": "sampling-diagnostics/v1",
                "chains": 2,
                "draws_per_chain": 256,
                "warmup_per_chain": 512,
                "divergences": 0,
                "minimum_effective_sample_size": 18.5,
                "maximum_r_hat": 1.08,
                "finite_export": True,
            }
        )


@pytest.mark.parametrize(
    ("task_id", "package_id"),
    [
        ("annealed-properties-v1", "annealed-gp-stable-ard-tutorial-v2"),
        ("hot-rolled-properties-v1", "hot-rolled-tutorial-v2"),
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
    assert {key for row in annealed["rows"] for key in row["outputs"]} == {
        "TS",
        "YS",
        "EL",
        "lambda",
    }
    assert {key for row in hot["rows"] for key in row["outputs"]} == {"TS"}


def test_canonical_training_digest_normalizes_cross_platform_libm_tail_bits() -> None:
    windows = {"rows": [{"features": {"time__log1p": 1.0986122886681098}}]}
    linux = {"rows": [{"features": {"time__log1p": 1.0986122886681096}}]}

    digest = canonical_training_dataset_digest(
        windows,
        algorithm=FEATURE_DATASET_DIGEST_FLOAT15,
    )
    assert digest == canonical_training_dataset_digest(
        linux,
        algorithm=FEATURE_DATASET_DIGEST_FLOAT15,
    )
    assert digest == "sha256:2bc882df934d2eb2dd6ae50306d8f6943e93b761780fca7ed32084a336a1f9a5"
    assert canonical_training_dataset_digest(
        windows,
        algorithm=FEATURE_DATASET_DIGEST_LEGACY,
    ) != canonical_training_dataset_digest(
        linux,
        algorithm=FEATURE_DATASET_DIGEST_LEGACY,
    )


def test_canonical_training_digest_rejects_non_finite_values() -> None:
    with pytest.raises(ValueError, match="finite floats"):
        canonical_training_dataset_digest(
            {"value": float("nan")},
            algorithm=FEATURE_DATASET_DIGEST_FLOAT15,
        )


@pytest.mark.parametrize(
    ("task_id", "package_id"),
    [
        ("mpea-room-tensile-v1", "mpea-room-tensile-ridge-v2"),
        ("mpea-hardness-process-v1", "mpea-hardness-ridge-v2"),
    ],
)
def test_portable_feature_digest_packages_pass_production_verification(
    task_id: str,
    package_id: str,
) -> None:
    package_root = ROOT / "models" / "packages" / package_id
    manifest = json.loads((package_root / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["provenance"]["feature_dataset_digest_algorithm"] == FEATURE_DATASET_DIGEST_FLOAT15
    report = verify_model_package(package_root, task_id=task_id, source=MPEA_SOURCE)
    assert report.package_id == package_id


def test_verify_rejects_contract_digest_and_quality_report_corruption(
    tmp_path: Path,
) -> None:
    source = ROOT / "models" / "packages" / "hot-rolled-tutorial-v2"
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
    quality_path.write_text(
        json.dumps(
            {
                "schema_version": "model-quality-report/v1",
                "split": "leave-one-parent-condition-out",
                "targets": [],
            }
        ),
        encoding="utf-8",
    )
    artifact = next(item for item in manifest["artifacts"] if item["path"] == manifest["quality_report"])
    artifact["bytes"] = quality_path.stat().st_size
    artifact["sha256"] = hashlib.sha256(quality_path.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(PackageContractError, match="quality report"):
        verify_model_package(package, task_id="hot-rolled-properties-v1", source=SOURCE)


def test_active_package_switch_and_rollback_are_atomic_and_reversible(
    tmp_path: Path,
) -> None:
    models = tmp_path / "models"
    first = models / "packages" / "first"
    second = models / "packages" / "second"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    config_path = models / "active-packages.json"
    config_path.write_text(
        json.dumps(
            {
                "schema_version": "active-model-packages/v1",
                "tasks": {
                    "annealed-properties-v1": {
                        "active": "packages/first",
                        "previous": None,
                    }
                },
            }
        ),
        encoding="utf-8",
    )

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


def test_staged_build_cannot_replace_a_checked_in_package() -> None:
    with pytest.raises(FileExistsError, match="immutable"):
        with staged_package_destination(
            ROOT / "models/packages/annealed-gp-stable-ard-tutorial-v2",
            replace=True,
        ):
            pytest.fail("checked-in package replacement must fail before staging")


def test_active_package_reference_cannot_escape_models_root(tmp_path: Path) -> None:
    models = tmp_path / "models"
    outside = tmp_path / "outside"
    outside.mkdir()
    config_path = models / "active-packages.json"
    models.mkdir()
    config_path.write_text(
        json.dumps(
            {
                "schema_version": "active-model-packages/v1",
                "tasks": {"annealed-properties-v1": {"active": "../outside", "previous": None}},
            }
        ),
        encoding="utf-8",
    )

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


def test_alternate_verified_package_needs_no_api_change_and_snapshot_keeps_old_identity(
    tmp_path: Path,
) -> None:
    import shutil

    original = ROOT / "models" / "packages" / "annealed-gp-stable-ard-tutorial-v2"
    alternate = tmp_path / "annealed-gp-alternate"
    shutil.copytree(original, alternate)
    manifest_path = alternate / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["package_id"] = "annealed-gp-alternate"
    manifest["package_version"] = "alternate-test"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    database = tmp_path / "workbench.db"

    with TestClient(
        create_app(
            database,
            source_overrides={
                "annealed-properties-v1": DEFAULT_SOURCE,
                "hot-rolled-properties-v1": DEFAULT_SOURCE,
            },
            package_roots={"annealed-properties-v1": alternate},
        )
    ) as client:
        status = client.get("/api/projects/default/model-package").json()
        assert status["id"] == "annealed-gp-alternate"
        candidate = client.get("/api/projects/default/candidates").json()[0]
        snapshot = client.post(f"/api/projects/default/candidates/{candidate['id']}/snapshots").json()
        assert snapshot["payload"]["provenance"]["package"]["id"] == "annealed-gp-alternate"
        old_hash = snapshot["payload"]["provenance"]["package"]["manifest_sha256"]

    with TestClient(
        create_app(
            database,
            source_overrides={
                "annealed-properties-v1": DEFAULT_SOURCE,
                "hot-rolled-properties-v1": DEFAULT_SOURCE,
            },
        )
    ) as client:
        current = client.get("/api/projects/default/model-package").json()
        stored = client.get(f"/api/projects/default/candidates/{candidate['id']}/snapshots").json()[0]
        assert current["id"] == "annealed-gp-alternate"
        assert stored["payload"]["provenance"]["package"]["manifest_sha256"] == old_hash
        assert old_hash == current["manifest_sha256"]


def test_app_startup_disables_only_package_trained_from_a_different_source(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    import shutil

    source = ROOT / "models" / "packages" / "hot-rolled-tutorial-v2"
    package = tmp_path / "hot-rolled-horseshoe"
    shutil.copytree(source, package)
    manifest_path = package / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["provenance"]["training_data_id"] = "sha256:different-source"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    app = create_app(
        tmp_path / "workbench.db",
        source_overrides={
            "annealed-properties-v1": DEFAULT_SOURCE,
            "hot-rolled-properties-v1": DEFAULT_SOURCE,
        },
        package_roots={"hot-rolled-properties-v1": package},
    )
    with TestClient(app) as client:
        health = client.get("/api/health").json()
        availability = health["tasks"]["hot-rolled-properties-v1"]["availability"]
        assert health["degraded"] is True
        assert availability["status"] == "unavailable"
        assert "training data digest" in availability["message"]
        assert health["tasks"]["annealed-properties-v1"]["availability"]["status"] == "available"
    assert not [record for record in caplog.records if "WORKBENCH_STARTUP_ERROR" in record.message]


def test_process_source_and_packages_start_and_predict_through_the_api(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("WORKBENCH_DEMO_SEED", "all")
    app = create_app(
        tmp_path / "process-workbench.db",
        source_overrides={
            "annealed-properties-v1": PROCESS_SOURCE,
            "hot-rolled-properties-v1": PROCESS_SOURCE,
        },
        package_roots={
            "annealed-properties-v1": PROCESS_ANNEALED_PACKAGE,
            "hot-rolled-properties-v1": PROCESS_HOT_PACKAGE,
        },
    )
    with TestClient(app) as client:
        assert client.get("/api/health").json()["ok"] is True
        assert client.get("/api/projects/default/model-package").json()["id"] == "annealed-gp-stable-ard-process-v2"
        assert client.get("/api/projects/hot-rolling-default/model-package").json()["id"] == "hot-rolled-horseshoe-process-v2"

        lineage = client.get("/api/projects/default/lineage", params={"query": "AN-00001"})
        assert lineage.status_code == 200
        assert lineage.json()["items"][0]["project"] == ""

        annealed = client.post("/api/projects/default/lineage/AN-00001/candidate").json()
        annealed_preview = client.post(
            f"/api/projects/default/candidates/{annealed['id']}/preview",
            params={"expected_revision": annealed["revision"]},
        )
        assert annealed_preview.status_code == 200
        assert set(annealed_preview.json()["predictions"]) == {
            "TS",
            "YS",
            "EL",
            "lambda",
        }

        hot = client.get("/api/projects/hot-rolling-default/candidates").json()[0]
        hot_preview = client.post(
            f"/api/projects/hot-rolling-default/candidates/{hot['id']}/preview",
            params={"expected_revision": hot["revision"]},
        )
        assert hot_preview.status_code == 200
        assert set(hot_preview.json()["predictions"]) == {"TS"}
