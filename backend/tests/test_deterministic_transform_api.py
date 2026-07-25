from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil

import pytest

from material_workbench.contracts.blend_contracts import CommercialMaterialCatalog
from material_workbench.contracts.stage_a_contracts import STAGE_A_COMPONENTS
from material_workbench.modeling.model_packages import PackageContractError
from material_workbench.modeling.transform_catalog import (
    load_deterministic_transform_catalog,
)


ROOT = Path(__file__).parents[2]
PACKAGE_ROOT = ROOT / "models" / "packages" / "welding-stage-a-deterministic-v1"
CATALOG_PATH = ROOT / "models" / "catalogs" / "welding-stage-a-commercial-v1.json"
DESIGN_SPACE_PATH = ROOT / "models" / "design-spaces" / "welding-stage-a-v1.json"


def _execution_blend() -> dict[str, object]:
    scientific = json.loads(
        (PACKAGE_ROOT / "smoke" / "input.json").read_text(encoding="utf-8")
    )
    catalog = CommercialMaterialCatalog.model_validate_json(
        CATALOG_PATH.read_text(encoding="utf-8")
    )
    return {
        "schema_version": "sparse-blend/v1",
        "items": scientific["items"],
        "hoop_id": scientific["hoop_id"],
        "fill_ratio": scientific["fill_ratio"],
        "balance_material_id": scientific["items"][0]["material_id"],
        "scientific_master": scientific["scientific_master"],
        "commercial_catalog": catalog.ref.model_dump(mode="json"),
        "design_space": {
            "resource_id": "welding-stage-a-design-space",
            "revision": 1,
            "digest": "sha256:" + "0" * 64,
        },
    }


def test_transform_catalog_exposes_active_package_and_fixed_axes(client) -> None:
    response = client.get("/api/transforms")

    assert response.status_code == 200
    items = response.json()
    assert len(items) == 1
    item = items[0]
    assert item["transform_id"] == "welding-stage-a-v1"
    assert item["package_id"] == "welding-stage-a-deterministic-v1"
    assert item["package_version"] == "1.0.0"
    assert item["package_manifest_digest"].startswith("sha256:")
    assert item["runtime_type"] == "builtin.deterministic_linear.v1"
    assert item["active_locator"] == "packages/welding-stage-a-deterministic-v1"
    assert item["available_locators"] == ["packages/welding-stage-a-deterministic-v1"]
    assert item["commercial_catalog_locator"] == "catalogs/welding-stage-a-commercial-v1.json"
    assert item["design_space_locator"] == "design-spaces/welding-stage-a-v1.json"
    assert item["scientific_master"]["resource_id"] == "welding-stage-a-science"
    assert item["commercial_catalog"] == CommercialMaterialCatalog.model_validate_json(
        CATALOG_PATH.read_text(encoding="utf-8")
    ).ref.model_dump(mode="json")
    assert item["outputs"] == list(STAGE_A_COMPONENTS)
    assert item["auxiliary_features"] == ["alloy_powder_d50_um"]

    editor = client.get("/api/transforms/welding-stage-a-v1/blend-editor")
    assert editor.status_code == 200
    context = editor.json()
    assert len(context["materials"]) == 252
    assert context["materials"][0]["name"] == "還元鉄粉-01"
    assert context["materials"][0]["material_type"] == "還元鉄粉"
    assert context["materials"][0]["main_components"]
    assert context["design_space"]["selection_count"] == {"minimum": 1, "maximum": 20}
    assert context["design_space"]["commercial_catalog"] == item["commercial_catalog"]


def test_transform_execution_returns_science_and_commercial_outputs(client) -> None:
    response = client.post(
        "/api/transforms/welding-stage-a-v1/execute",
        json={"blend": _execution_blend()},
    )

    assert response.status_code == 200
    payload = response.json()
    assert tuple(payload["material_composition"]) == STAGE_A_COMPONENTS
    assert sum(payload["material_composition"].values()) == pytest.approx(100.0)
    assert payload["powder_blend_cost_yen_per_kg_core"] > 0
    assert sum(
        item["contribution_yen_per_kg_core"]
        for item in payload["material_cost_contributions"]
    ) == pytest.approx(payload["powder_blend_cost_yen_per_kg_core"])


def test_transform_execution_rejects_unknown_transform_and_catalog_revision(client) -> None:
    missing = client.post(
        "/api/transforms/missing/execute",
        json={"blend": _execution_blend()},
    )
    assert missing.status_code == 404
    assert missing.json()["code"] == "not_found"

    blend = _execution_blend()
    blend["commercial_catalog"]["digest"] = "sha256:" + "f" * 64
    mismatch = client.post(
        "/api/transforms/welding-stage-a-v1/execute",
        json={"blend": blend},
    )
    assert mismatch.status_code == 422
    assert mismatch.json() == {
        "code": "validation_error",
        "message": "commercial catalog does not match candidate revision",
        "field_errors": [],
    }


@pytest.mark.parametrize(
    "selection",
    [
        {
            "active": "packages/active",
            "available": ["packages/other"],
            "commercial_catalog": "catalogs/commercial.json",
        },
        {
            "active": "../outside",
            "available": ["../outside"],
            "commercial_catalog": "catalogs/commercial.json",
        },
    ],
)
def test_transform_catalog_rejects_invalid_active_locators(
    tmp_path: Path,
    selection: dict[str, object],
) -> None:
    config = tmp_path / "models" / "active-transforms.json"
    config.parent.mkdir()
    config.write_text(
        json.dumps(
            {
                "schema_version": "active-deterministic-transforms/v1",
                "transforms": {"stage-a": selection},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(PackageContractError):
        load_deterministic_transform_catalog(config)


@pytest.mark.parametrize(
    "broken_contract",
    ["artifact_axis", "transform_id", "task_id", "declared_axis"],
)
def test_transform_catalog_rejects_semantically_broken_nonactive_package(
    tmp_path: Path,
    broken_contract: str,
) -> None:
    models = tmp_path / "models"
    active = models / "packages" / "active"
    broken = models / "packages" / "broken"
    shutil.copytree(PACKAGE_ROOT, active)
    shutil.copytree(PACKAGE_ROOT, broken)
    catalog = models / "catalogs" / "commercial.json"
    catalog.parent.mkdir(parents=True)
    shutil.copyfile(CATALOG_PATH, catalog)
    design_space = models / "design-spaces" / "space.json"
    design_space.parent.mkdir(parents=True)
    shutil.copyfile(DESIGN_SPACE_PATH, design_space)

    manifest_path = broken / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if broken_contract == "artifact_axis":
        artifact_path = broken / "transform" / "stage-a.json"
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
        artifact["scientific_master"]["components"][-1] = "その他"
        artifact_path.write_text(
            json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        artifact_entry = next(
            item
            for item in manifest["artifacts"]
            if item["path"] == "transform/stage-a.json"
        )
        artifact_entry["bytes"] = artifact_path.stat().st_size
        artifact_entry["sha256"] = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
    elif broken_contract == "transform_id":
        manifest["deterministic_transforms"][0]["id"] = "other-transform"
    elif broken_contract == "task_id":
        manifest["task_id"] = "other-task"
    else:
        manifest["deterministic_transforms"][0]["output_names"][-1] = "その他"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    config = models / "active-transforms.json"
    config.write_text(
        json.dumps(
            {
                "schema_version": "active-deterministic-transforms/v1",
                "transforms": {
                    "welding-stage-a-v1": {
                        "active": "packages/active",
                        "available": ["packages/active", "packages/broken"],
                        "commercial_catalog": "catalogs/commercial.json",
                        "design_space": "design-spaces/space.json",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(PackageContractError):
        load_deterministic_transform_catalog(config)
