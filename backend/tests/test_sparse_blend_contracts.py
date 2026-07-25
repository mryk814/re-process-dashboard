from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from material_workbench.contracts.blend_contracts import (
    BlendContractRegistry,
    BlendItem,
    CommercialMaterial,
    CommercialMaterialCatalog,
    GroupCardinalityConstraint,
    GroupTotalConstraint,
    MaterialRatioBound,
    ScientificHoop,
    ScientificMaterial,
    ScientificMaterialMaster,
    SelectionCountConstraint,
    SparseBlend,
    SparseBlendDesignSpace,
    validate_sparse_blend,
)
from material_workbench.contracts.schemas import CandidateInput
from material_workbench.execution.inference_work_graph import semantic_digest
from material_workbench.tasks.task_registry import load_task_contracts


def _contracts() -> tuple[
    ScientificMaterialMaster,
    CommercialMaterialCatalog,
    SparseBlendDesignSpace,
    BlendContractRegistry,
]:
    master = ScientificMaterialMaster(
        schema_version="scientific-material-master/v1",
        resource_id="welding-science",
        revision=1,
        materials=(
            ScientificMaterial(
                material_id="RM-0001",
                name="鉄粉",
                material_type="アトマイズ鉄粉",
                group="鉄粉",
                d50_um=75,
            ),
            ScientificMaterial(
                material_id="RM-0002",
                name="FeMn",
                material_type="フェロマンガン",
                group="合金鉄",
                d50_um=90,
            ),
            ScientificMaterial(
                material_id="RM-0003",
                name="Ni",
                material_type="ニッケル粉",
                group="純金属粉",
                d50_um=55,
            ),
            ScientificMaterial(
                material_id="RM-9999",
                name="将来原料",
                material_type="試験粉",
                group="試作",
                d50_um=50,
            ),
        ),
        hoops=(ScientificHoop(hoop_id="HP-01", name="軟鋼フープ"),),
    )
    catalog = CommercialMaterialCatalog(
        schema_version="commercial-material-catalog/v1",
        resource_id="welding-commercial",
        revision=3,
        materials=tuple(
            CommercialMaterial(
                material_id=material.material_id,
                procurement="常用" if material.material_id != "RM-9999" else "試作限定",
                unit_price_yen_per_kg_core=100 + index * 50,
            )
            for index, material in enumerate(master.materials)
        ),
    )
    space = SparseBlendDesignSpace(
        schema_version="sparse-blend-design-space/v1",
        resource_id="welding-core-space",
        revision=2,
        scientific_master=master.ref,
        commercial_catalog=catalog.ref,
        allowed_material_ids=("RM-0001", "RM-0002", "RM-0003"),
        material_bounds=(
            MaterialRatioBound(material_id="RM-0001", lower=18, upper=100),
            MaterialRatioBound(material_id="RM-0002", lower=0, upper=25),
            MaterialRatioBound(material_id="RM-0003", lower=0, upper=15),
        ),
        group_totals=(
            GroupTotalConstraint(group="合金鉄", lower=5, upper=25),
        ),
        group_cardinalities=(
            GroupCardinalityConstraint(group="純金属粉", minimum=0, maximum=1),
        ),
        selection_count=SelectionCountConstraint(minimum=2, maximum=3),
        fixed_hoop_id="HP-01",
        fixed_fill_ratio=24,
        balance_material_id="RM-0001",
    )
    return master, catalog, space, BlendContractRegistry((master,), (catalog,), (space,))


def _blend(**updates: object) -> SparseBlend:
    master, catalog, space, _ = _contracts()
    payload = {
        "items": (
            {"material_id": "RM-0001", "ratio": 75.0},
            {"material_id": "RM-0002", "ratio": 15.0},
            {"material_id": "RM-0003", "ratio": 10.0},
        ),
        "hoop_id": "HP-01",
        "fill_ratio": 24.0,
        "balance_material_id": "RM-0001",
        "scientific_master": master.ref,
        "commercial_catalog": catalog.ref,
        "design_space": space.ref,
    }
    payload.update(updates)
    return SparseBlend.model_validate(payload)


def test_sparse_blend_rejects_duplicate_and_non_finite_items_structurally() -> None:
    with pytest.raises(ValidationError, match="unique"):
        _blend(items=(
            {"material_id": "RM-0001", "ratio": 60},
            {"material_id": "RM-0001", "ratio": 40},
        ))
    with pytest.raises(ValidationError, match="finite"):
        _blend(items=(
            {"material_id": "RM-0001", "ratio": float("nan")},
            {"material_id": "RM-0002", "ratio": 100},
        ))


def test_unknown_material_is_a_structural_error_not_a_draft_violation() -> None:
    _, _, _, registry = _contracts()
    blend = _blend(items=(
        {"material_id": "RM-0001", "ratio": 80},
        {"material_id": "RM-NOT-FOUND", "ratio": 20},
    ))

    with pytest.raises(ValueError, match="unknown material"):
        registry.resolve(blend)


def test_design_space_violations_are_returned_together_with_reasons() -> None:
    _, _, _, registry = _contracts()
    blend = _blend(
        items=(
            {"material_id": "RM-0001", "ratio": 10},
            {"material_id": "RM-0002", "ratio": 40},
            {"material_id": "RM-9999", "ratio": 5},
        ),
        fill_ratio=30,
    )

    state = validate_sparse_blend(blend, registry.resolve(blend))

    assert state.status == "invalid"
    codes = {issue.code for issue in state.issues}
    assert {"fixed_fill", "material_not_allowed", "material_bounds", "total", "group_total"} <= codes
    assert all(issue.message for issue in state.issues)


def test_editor_state_and_commercial_revision_do_not_change_model_input_hash() -> None:
    master, catalog, space, _ = _contracts()
    blend = _blend()
    changed_catalog = catalog.model_copy(update={"revision": 4})
    changed_space = space.model_copy(
        update={
            "revision": 3,
            "commercial_catalog": changed_catalog.ref,
        }
    )
    repriced = blend.model_copy(
        update={
            "commercial_catalog": changed_catalog.ref,
            "design_space": changed_space.ref,
        }
    )
    source = {
        "name": "配合",
        "inputs": {"composition": {"C": 0.1}, "process": {}},
        "blend": blend.model_dump(mode="json"),
        "editor_state": {"locked_material_ids": ["RM-0002"]},
    }
    locked = CandidateInput.model_validate(source)
    unlocked = CandidateInput.model_validate(
        {**source, "editor_state": {"locked_material_ids": []}}
    )

    assert locked.blend is not None and unlocked.blend is not None
    assert locked.blend.model_input_digest == unlocked.blend.model_input_digest
    assert repriced.model_input_digest == blend.model_input_digest
    assert master.ref == repriced.scientific_master


def test_legacy_fixed_form_candidate_defaults_to_no_blend() -> None:
    candidate = CandidateInput.model_validate(
        {
            "name": "legacy",
            "inputs": {
                "composition": {"C": 0.1},
                "process": {},
            },
        }
    )

    assert candidate.blend is None
    assert candidate.editor_state.locked_material_ids == []
    assert candidate.blend_validation.status == "not_applicable"


def test_adding_a_material_does_not_change_fixed_task_contract_dimensions() -> None:
    master, _, _, _ = _contracts()
    task = load_task_contracts()["annealed-properties-v1"].task_definition
    task_digest = semantic_digest(task.model_dump(mode="json"))
    input_paths = tuple(
        field.path for group in task.input_groups for field in group.fields
    )

    expanded = master.model_copy(
        update={
            "revision": master.revision + 1,
            "materials": master.materials
            + (
                ScientificMaterial(
                    material_id="RM-10000",
                    name="追加原料",
                    material_type="新原型",
                    group="試作",
                    d50_um=42,
                ),
            ),
        }
    )

    assert len(expanded.materials) == len(master.materials) + 1
    assert tuple(
        field.path for group in task.input_groups for field in group.fields
    ) == input_paths
    assert semantic_digest(task.model_dump(mode="json")) == task_digest


def test_invalid_blend_is_saved_as_draft_and_preview_is_blocked(client) -> None:
    _, _, _, registry = _contracts()
    client.app.state.blend_contract_registry = registry
    source = client.get("/api/projects/default/candidates").json()[0]
    payload = {
        key: deepcopy(source[key])
        for key in ("name", "inputs", "provenance")
    }
    payload["name"] = "Design Space違反draft"
    payload["blend"] = _blend(
        items=(
            BlendItem(material_id="RM-0001", ratio=10),
            BlendItem(material_id="RM-0002", ratio=10),
        )
    ).model_dump(mode="json")
    payload["editor_state"] = {"locked_material_ids": ["RM-0002"]}
    # A forged client value must never override server-side validation.
    payload["blend_validation"] = {"status": "not_applicable", "issues": []}

    created = client.post("/api/projects/default/candidates", json=payload)

    assert created.status_code == 201
    candidate = created.json()
    assert candidate["blend_validation"]["status"] == "invalid"
    assert {item["code"] for item in candidate["blend_validation"]["issues"]} >= {
        "total",
        "material_bounds",
    }
    fetched = client.get(
        f"/api/projects/default/candidates/{candidate['id']}"
    ).json()
    assert fetched["blend_validation"] == candidate["blend_validation"]
    preview = client.post(
        f"/api/projects/default/candidates/{candidate['id']}/preview",
        params={"expected_revision": candidate["revision"]},
    )
    assert preview.status_code == 422
    assert "Design Space" in preview.json()["message"]
