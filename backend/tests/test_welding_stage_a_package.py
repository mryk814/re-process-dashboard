from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import sys

import pytest
from openpyxl import load_workbook

from material_workbench.adapters.builtin_deterministic_linear import (
    DeterministicLinearArtifact,
    ScientificTransformResult,
)
from material_workbench.contracts.blend_contracts import (
    BlendItem,
    CommercialMaterialCatalog,
    SparseBlend,
)
from material_workbench.modeling.model_package_verify import (
    verify_deterministic_transform_package,
)
from material_workbench.modeling.model_packages import ModelPackageLoader, PackageContractError


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend/scripts"))

import build_welding_stage_a_package as package_builder  # noqa: E402


PACKAGE = ROOT / "models/packages/welding-stage-a-deterministic-v1"
CATALOG = ROOT / "models/catalogs/welding-stage-a-commercial-v1.json"
SOURCE = ROOT / "data/source/welding_consumable_multistage_synthetic_dataset.xlsx"


def _loaded() -> tuple[object, object, CommercialMaterialCatalog]:
    package = ModelPackageLoader().load(PACKAGE)
    transform = package.load_transform("material-composition")
    catalog = CommercialMaterialCatalog.model_validate_json(
        CATALOG.read_text(encoding="utf-8")
    )
    return package, transform, catalog


def _rewrite_artifact(root: Path, mutate: object) -> None:
    artifact_path = root / "transform/stage-a.json"
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    mutate(payload)  # type: ignore[operator]
    artifact_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    artifact_entry = next(
        item for item in manifest["artifacts"] if item["path"] == "transform/stage-a.json"
    )
    artifact_entry["sha256"] = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
    artifact_entry["bytes"] = artifact_path.stat().st_size
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def test_stage_a_builder_is_reproducible_and_keeps_source_read_only(
    tmp_path: Path,
) -> None:
    source_before = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    package = tmp_path / "package"
    catalog = tmp_path / "catalog.json"

    package_builder.build_package(SOURCE, package, catalog)

    assert hashlib.sha256(SOURCE.read_bytes()).hexdigest() == source_before
    for expected in PACKAGE.rglob("*"):
        if not expected.is_file():
            continue
        relative = expected.relative_to(PACKAGE)
        assert (package / relative).read_bytes() == expected.read_bytes()
    assert catalog.read_bytes() == CATALOG.read_bytes()


def test_stage_a_package_smoke_and_all_120_blends_match_golden() -> None:
    report = verify_deterministic_transform_package(PACKAGE)
    package, transform, _ = _loaded()
    golden = json.loads(
        package.artifact_path("reference/stage-a-golden-120.json").read_text(
            encoding="utf-8"
        )
    )

    assert report.runtime_type == "builtin.deterministic_linear.v1"
    assert golden["source_sha256"] == hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    assert len(golden["rows"]) == 120
    for row in golden["rows"]:
        blend = SparseBlend.model_validate(row["blend"])
        expected = ScientificTransformResult.model_validate(row["expected"])
        assert transform.transform(blend) == expected
        assert sum(expected.material_composition.values()) == pytest.approx(
            100.0, abs=2e-5
        )


def test_all_120_golden_rows_match_the_workbook_generation_formula() -> None:
    package = ModelPackageLoader().load(PACKAGE)
    golden = json.loads(
        package.artifact_path("reference/stage-a-golden-120.json").read_text(
            encoding="utf-8"
        )
    )
    workbook = load_workbook(SOURCE, read_only=True, data_only=True)
    try:
        records = {
            key: package_builder._records(workbook, sheet)
            for key, sheet in package_builder.SHEETS.items()
        }
    finally:
        workbook.close()
    component_columns = package_builder._component_columns(
        records["material_composition"][0]
    )
    material_compositions = {
        row[package_builder.COLUMNS["material_id"]]: {
            package_builder._component_name(column): float(row[column] or 0)
            for column in component_columns
        }
        for row in records["material_composition"]
    }
    hoop_compositions = {
        row[package_builder.COLUMNS["hoop_id"]]: {
            package_builder._component_name(column): float(row.get(column) or 0)
            for column in component_columns
        }
        for row in records["hoops"]
    }
    blend_heads = {
        row[package_builder.COLUMNS["blend_id"]]: row for row in records["blends"]
    }
    blend_lines: dict[str, list[dict[str, object]]] = {}
    for row in records["blend_items"]:
        blend_lines.setdefault(row[package_builder.COLUMNS["blend_id"]], []).append(row)

    for golden_row in golden["rows"]:
        blend_id = golden_row["blend_id"]
        head = blend_heads[blend_id]
        fill = float(head[package_builder.COLUMNS["fill_ratio"]]) / 100.0
        hoop = hoop_compositions[head[package_builder.COLUMNS["hoop_id"]]]
        direct = {
            package_builder._component_name(column): (1.0 - fill)
            * hoop[package_builder._component_name(column)]
            for column in component_columns
        }
        for line in blend_lines[blend_id]:
            material_id = line[package_builder.COLUMNS["material_id"]]
            whole_wire_fraction = (
                fill * float(line[package_builder.COLUMNS["ratio"]]) / 100.0
            )
            for component, value in material_compositions[material_id].items():
                direct[component] += whole_wire_fraction * value
        assert golden_row["expected"]["material_composition"] == pytest.approx(
            direct, abs=1e-10
        )


def test_sparse_order_zero_line_and_cost_breakdown_are_stable() -> None:
    _, transform, catalog = _loaded()
    blend = SparseBlend.model_validate_json(
        (PACKAGE / "smoke/input.json").read_text(encoding="utf-8")
    )
    baseline = transform.transform(blend)
    used = {item.material_id for item in blend.items}
    unused = next(item.material_id for item in catalog.materials if item.material_id not in used)
    reordered = blend.model_copy(
        update={
            "items": tuple(reversed(blend.items)) + (BlendItem(material_id=unused, ratio=0),)
        }
    )

    assert transform.transform(reordered) == baseline
    evaluated = transform.execute(blend, catalog)
    assert evaluated.powder_blend_cost_yen_per_kg_core == pytest.approx(
        sum(item.contribution_yen_per_kg_core for item in evaluated.material_cost_contributions)
    )
    assert sum(
        item.share_of_blend_cost for item in evaluated.material_cost_contributions
    ) == pytest.approx(1.0)
    assert set(evaluated.auxiliary_features) == {"alloy_powder_d50_um"}


def test_stage_a_rejects_unknown_material_unit_mismatch_and_artifact_tamper(
    tmp_path: Path,
) -> None:
    _, transform, _ = _loaded()
    blend = SparseBlend.model_validate_json(
        (PACKAGE / "smoke/input.json").read_text(encoding="utf-8")
    )
    unknown = blend.model_copy(
        update={
            "items": blend.items
            + (BlendItem(material_id="RM-NOT-IN-PACKAGE", ratio=0),)
        }
    )
    with pytest.raises(PackageContractError, match="unknown Stage A material"):
        transform.transform(unknown)

    wrong_unit = tmp_path / "wrong-unit"
    shutil.copytree(PACKAGE, wrong_unit)
    _rewrite_artifact(
        wrong_unit,
        lambda payload: payload["compiler"].update(
            {"core_ratio_unit": "fraction_core"}
        ),
    )
    with pytest.raises(PackageContractError, match="invalid deterministic-linear artifact"):
        ModelPackageLoader().load(wrong_unit).load_transform("material-composition")

    tampered = tmp_path / "tampered"
    shutil.copytree(PACKAGE, tampered)
    with (tampered / "transform/stage-a.json").open("ab") as stream:
        stream.write(b" ")
    with pytest.raises(PackageContractError, match="artifact (size|hash) mismatch"):
        ModelPackageLoader().load(tampered)


def test_old_package_remains_reproducible_after_new_scientific_master_revision(
    tmp_path: Path,
) -> None:
    _, old_transform, _ = _loaded()
    old_blend = SparseBlend.model_validate_json(
        (PACKAGE / "smoke/input.json").read_text(encoding="utf-8")
    )
    old_result = old_transform.transform(old_blend)

    new_package = tmp_path / "revision-2"
    shutil.copytree(PACKAGE, new_package)

    def update_master(payload: dict[str, object]) -> None:
        master = payload["scientific_master"]
        master["revision"] = 2
        master["materials"][0]["d50_um"] += 1

    _rewrite_artifact(new_package, update_master)
    artifact = DeterministicLinearArtifact.model_validate_json(
        (new_package / "transform/stage-a.json").read_text(encoding="utf-8")
    )
    manifest_path = new_package / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["package_version"] = "2.0.0"
    manifest["deterministic_transforms"][0][
        "scientific_master_digest"
    ] = artifact.scientific_master.ref.digest
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    new_transform = ModelPackageLoader().load(new_package).load_transform(
        "material-composition"
    )
    with pytest.raises(PackageContractError, match="scientific master"):
        new_transform.transform(old_blend)
    assert old_transform.transform(old_blend) == old_result
