from __future__ import annotations

from pathlib import Path
import csv
import json
import sys

from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "backend" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from model_workflow import (  # noqa: E402
    build_package,
    diagnose_source,
    promote_package,
)
from material_workbench.app import create_app  # noqa: E402
from material_workbench.contracts.schemas import ModelPackageRefCreateInput  # noqa: E402
from material_workbench.data.dataset_registration import (  # noqa: E402
    register_managed_dataset,
)
from material_workbench.modeling.model_lifecycle import (  # noqa: E402
    canonical_training_dataset_digest,
    dataset_profile_digest,
    resolve_configured_package,
)
from material_workbench.modeling.model_package_verify import (  # noqa: E402
    verify_model_package,
)
from material_workbench.modeling.model_packages import ModelPackageLoader  # noqa: E402
from material_workbench.persistence.workspace_catalog import WorkspaceCatalog  # noqa: E402
from material_workbench.persistence.workspace_catalog_bootstrap import (  # noqa: E402
    task_definition_digest,
)
from material_workbench.tasks.task_registry import load_task_contracts  # noqa: E402


def test_model_source_diagnosis_branches_existing_and_new_tasks() -> None:
    source = (
        ROOT
        / "data"
        / "source"
        / "external"
        / "heat_treatment_tradeoff_samples.csv"
    )
    existing = diagnose_source(
        source,
        task_id="heat-treatment-tradeoff-v1",
        profile=None,
    )
    assert existing["route"] == "existing_task_replacement"
    assert existing["eligible_rows"] == 2400
    assert "model:build" in existing["next"]

    new_task = diagnose_source(
        source,
        task_id="unregistered-material-task-v1",
        profile=None,
    )
    assert new_task["route"] == "new_task_or_profile"
    assert "dataset-input-profile.md" in new_task["next"]


def test_explicit_profile_flows_from_diagnosis_through_build_and_verify(
    tmp_path: Path,
) -> None:
    original_source = (
        ROOT
        / "data"
        / "source"
        / "external"
        / "heat_treatment_tradeoff_samples.csv"
    )
    original_profile = json.loads(
        (
            ROOT
            / "backend"
            / "src"
            / "material_workbench"
            / "data"
            / "tabular-profile-heat-treatment-v1.json"
        ).read_text(encoding="utf-8")
    )
    renamed = {
        "alloy_family": "材種",
        "carbon_pct": "炭素量_pct",
        "chromium_pct": "クロム量_pct",
        "nickel_pct": "ニッケル量_pct",
        "austenitize_temp_c": "焼入温度_C",
        "tempering_temp_c": "焼戻温度_C",
        "hold_time_h": "保持時間_h",
        "cooling_rate_c_per_s": "冷却速度_C_s",
        "hardness_hv": "硬さ_HV",
        "charpy_j": "シャルピー_J",
    }
    source = tmp_path / "customer-heat-treatment.csv"
    with original_source.open(encoding="utf-8", newline="") as input_stream:
        reader = csv.DictReader(input_stream)
        rows = list(reader)[:80]
    with source.open("w", encoding="utf-8", newline="") as output_stream:
        writer = csv.DictWriter(
            output_stream,
            fieldnames=[renamed[name] for name in reader.fieldnames or ()],
        )
        writer.writeheader()
        writer.writerows([
            {renamed[key]: value for key, value in row.items()}
            for row in rows
        ])

    profile_document = {
        **original_profile,
        "profile_id": "customer-heat-treatment-v1",
        "name": "顧客列名の熱処理データ",
        "package_id": "customer-heat-treatment-ridge-v1",
        "inputs": [
            {**item, "column": renamed[item["column"]]}
            for item in original_profile["inputs"]
        ],
        "outputs": [
            {**item, "column": renamed[item["column"]]}
            for item in original_profile["outputs"]
        ],
    }
    profile = tmp_path / "customer-profile.json"
    profile.write_text(
        json.dumps(profile_document, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    diagnosis = diagnose_source(
        source,
        task_id="heat-treatment-tradeoff-v1",
        profile=profile,
    )
    assert diagnosis["route"] == "existing_task_replacement"
    assert diagnosis["eligible_rows"] == 80
    assert diagnosis["profile_digest"] == dataset_profile_digest(profile)

    package = tmp_path / "package"
    feature_dataset = tmp_path / "feature-dataset.json"
    report = build_package(
        "heat-treatment-tradeoff-v1",
        source,
        package,
        feature_dataset,
        package_id="customer-heat-treatment-ridge-v1",
        package_version="1.0.0",
        replace=False,
        profile=profile,
    )
    manifest = ModelPackageLoader().load(package).manifest
    feature_payload = json.loads(feature_dataset.read_text(encoding="utf-8"))
    assert report["package"]["task_id"] == "heat-treatment-tradeoff-v1"
    assert manifest.provenance.dataset_profile_id == dataset_profile_digest(profile)
    assert feature_payload["dataset_profile_digest"] == dataset_profile_digest(profile)
    assert canonical_training_dataset_digest(
        feature_payload,
        algorithm=manifest.provenance.feature_dataset_digest_algorithm,
    ) == manifest.provenance.feature_dataset_id

    store_root = tmp_path / "personal-model-store"
    store_root.mkdir()
    (store_root / "available-packages.json").write_text(
        json.dumps(
            {
                "schema_version": "available-model-packages/v1",
                "packages": [],
            }
        ),
        encoding="utf-8",
    )
    promotion = promote_package(
        "heat-treatment-tradeoff-v1",
        package,
        source,
        store_root / "active-packages.json",
        activate=False,
        profile=profile,
    )
    promoted_package = Path(promotion["trusted_package"])
    assert promoted_package.is_dir()

    database = tmp_path / "workbench.db"
    library = tmp_path / "data-library"
    app = create_app(db_path=database, data_library_path=library)
    with TestClient(app) as client:
        registered = register_managed_dataset(
            database=database,
            source=source,
            library_root=library,
            profile_path=profile,
        )
        catalog = WorkspaceCatalog(database)
        package_ref = catalog.upsert_model_package_ref(
            ModelPackageRefCreateInput(
                package_id=manifest.package_id,
                task_id=manifest.task_id,
                task_contract_digest=task_definition_digest(
                    client.app.state.task_registry,
                    manifest.task_id,
                ),
                manifest_digest=ModelPackageLoader().load(
                    promoted_package
                ).manifest_sha256,
                locator=str(promoted_package),
                manifest_json=manifest.model_dump(mode="json"),
            )
        )
        project_response = client.post(
            "/api/projects",
            json={
                "name": "リポジトリ外データのProject smoke",
                "task_id": manifest.task_id,
                "dataset_view_revision_id": registered.dataset_view_revision_id,
                "model_package_ref_id": package_ref.id,
            },
        )
        assert project_response.status_code == 201, project_response.text
        project_id = project_response.json()["id"]
        canonical = load_task_contracts()[manifest.task_id].canonical_candidate
        candidate_response = client.post(
            f"/api/projects/{project_id}/candidates",
            json={
                "name": "外部Profile候補",
                "inputs": {
                    "composition": canonical.composition,
                    "process": canonical.process,
                    "categorical": canonical.categorical,
                    "heat_pattern": canonical.heat_pattern,
                },
                "provenance": canonical.provenance.model_dump(),
            },
        )
        assert candidate_response.status_code == 201, candidate_response.text
        candidate = candidate_response.json()
        preview_response = client.post(
            f"/api/projects/{project_id}/candidates/{candidate['id']}/preview",
            params={"expected_revision": candidate["revision"]},
        )
        assert preview_response.status_code == 200, preview_response.text
        assert set(preview_response.json()["predictions"]) == {
            "hardness_hv",
            "charpy_j",
        }


def test_wrong_profile_family_is_rejected_instead_of_using_the_default(
    tmp_path: Path,
) -> None:
    source = (
        ROOT
        / "data"
        / "source"
        / "external"
        / "heat_treatment_tradeoff_samples.csv"
    )
    raw = json.loads(
        (
            ROOT
            / "backend"
            / "src"
            / "material_workbench"
            / "data"
            / "welding-stage-b-profile-v1.json"
        ).read_text(encoding="utf-8")
    )
    raw["task_id"] = "heat-treatment-tradeoff-v1"
    profile = tmp_path / "wrong-family-profile.json"
    profile.write_text(json.dumps(raw), encoding="utf-8")

    diagnosis = diagnose_source(
        source,
        task_id="heat-treatment-tradeoff-v1",
        profile=profile,
    )

    assert diagnosis["route"] == "new_task_or_profile"
    assert "tabular task requires a tabular Dataset Profile" in diagnosis["reason"]


def test_stage_b_default_and_custom_profiles_keep_lifecycle_identity(
    tmp_path: Path,
) -> None:
    task_id = "welding-consumable-stage-b-v1"
    source = (
        ROOT
        / "data"
        / "source"
        / "welding_consumable_multistage_synthetic_dataset.xlsx"
    )
    profile = (
        ROOT
        / "backend"
        / "src"
        / "material_workbench"
        / "data"
        / "welding-stage-b-profile-v1.json"
    )

    diagnosis = diagnose_source(source, task_id=task_id, profile=profile)
    report = verify_model_package(
        resolve_configured_package(task_id),
        task_id=task_id,
        source=source,
        profile=profile,
    )

    assert diagnosis["route"] == "existing_task_replacement"
    assert diagnosis["profile_digest"] == dataset_profile_digest(profile)
    assert report.task_id == task_id

    custom_document = json.loads(profile.read_text(encoding="utf-8"))
    custom_document["id"] = "customer-welding-stage-b-v1"
    custom_profile = tmp_path / "customer-stage-b-profile.json"
    custom_profile.write_text(
        json.dumps(custom_document, ensure_ascii=False),
        encoding="utf-8",
    )
    custom_package = tmp_path / "customer-stage-b-package"
    custom_features = tmp_path / "customer-stage-b-features.json"
    build_package(
        task_id,
        source,
        custom_package,
        custom_features,
        package_id="customer-welding-stage-b-ridge-v1",
        package_version="1.0.0",
        replace=False,
        profile=custom_profile,
    )
    custom_manifest = ModelPackageLoader().load(custom_package).manifest
    feature_payload = json.loads(custom_features.read_text(encoding="utf-8"))

    assert feature_payload["dataset_profile_digest"] == (
        dataset_profile_digest(custom_profile)
    )
    assert custom_manifest.provenance.dataset_profile_id == (
        dataset_profile_digest(custom_profile)
    )
    assert canonical_training_dataset_digest(
        feature_payload,
        algorithm=custom_manifest.provenance.feature_dataset_digest_algorithm,
    ) == custom_manifest.provenance.feature_dataset_id


def test_observation_builder_records_an_explicit_profile_digest(
    tmp_path: Path,
) -> None:
    task_id = "welding-stage-c-properties-v1"
    source = (
        ROOT
        / "data"
        / "source"
        / "welding_consumable_multistage_synthetic_dataset.xlsx"
    )
    original_profile = json.loads(
        (
            ROOT
            / "backend"
            / "src"
            / "material_workbench"
            / "data"
            / "observation-profile-welding-consumable-stage-c-v1.json"
        ).read_text(encoding="utf-8")
    )
    original_profile["id"] = "customer-stage-c-profile-v1"
    profile = tmp_path / "customer-stage-c-profile.json"
    profile.write_text(
        json.dumps(original_profile, ensure_ascii=False),
        encoding="utf-8",
    )

    package = tmp_path / "stage-c-package"
    build_package(
        task_id,
        source,
        package,
        tmp_path / "stage-c-features.json",
        package_id="customer-stage-c-ridge-v1",
        package_version="1.0.0",
        replace=False,
        profile=profile,
    )

    manifest = ModelPackageLoader().load(package).manifest
    assert manifest.provenance.dataset_profile_id == dataset_profile_digest(profile)
