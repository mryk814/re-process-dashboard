from __future__ import annotations

from copy import deepcopy
from io import BytesIO
from types import SimpleNamespace

import pytest
from openpyxl import load_workbook
from pydantic import ValidationError

from material_workbench.application.candidates import CandidateService
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
from material_workbench.contracts.schemas import Candidate, CandidateInput
from material_workbench.domain.services import candidates_xlsx
from material_workbench.execution.inference_work_graph import semantic_digest
from material_workbench.persistence.snapshot_reader import candidate_input_from_snapshot
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


def test_blend_material_descriptors_pin_scientific_names_and_catalog_prices() -> None:
    _, _, _, registry = _contracts()

    descriptors = registry.describe(_blend())

    assert [item.material_id for item in descriptors] == [
        "RM-0001",
        "RM-0002",
        "RM-0003",
    ]
    assert descriptors[0].name == "鉄粉"
    assert descriptors[0].unit_price_yen_per_kg_core == 100
    assert descriptors[2].group == "純金属粉"


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


def test_zero_ratio_line_does_not_change_model_input_payload_or_hash() -> None:
    source = _blend()
    with_zero = source.model_copy(
        update={
            "items": source.items
            + (BlendItem(material_id="RM-9999", ratio=0),)
        }
    )

    assert with_zero.model_input_payload() == source.model_input_payload()
    assert with_zero.model_input_digest == source.model_input_digest


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


def _blend_capable_service(registry: BlendContractRegistry) -> CandidateService:
    task_registry = SimpleNamespace(
        entry_for=lambda _task_id: SimpleNamespace(
            application_capability=SimpleNamespace(sparse_blend=True)
        ),
        validate_candidate=lambda _task_id, _payload: None,
    )
    return CandidateService(
        store=SimpleNamespace(),
        registry=task_registry,
        resolver=SimpleNamespace(),
        blend_contracts=registry,
    )


def test_blend_validation_is_server_recomputed_and_snapshot_restore_keeps_blend() -> None:
    _, _, _, registry = _contracts()
    blend = _blend(
        items=(
            BlendItem(material_id="RM-0001", ratio=10),
            BlendItem(material_id="RM-0002", ratio=10),
        )
    )
    payload = {
        "name": "Design Space違反draft",
        "inputs": {"composition": {"C": 0.1}, "process": {}},
        "blend": blend.model_dump(mode="json"),
        "editor_state": {"locked_material_ids": ["RM-0002"]},
        # A forged client value must never override server-side validation.
        "blend_validation": {"status": "not_applicable", "issues": []},
    }
    service = _blend_capable_service(registry)
    prepared = service._prepare("blend-task", CandidateInput.model_validate(payload))
    assert prepared.blend_validation.status == "invalid"
    assert {item.code for item in prepared.blend_validation.issues} >= {
        "total",
        "material_bounds",
    }

    restored = candidate_input_from_snapshot(
        "snapshot-1",
        {
            "snapshot_schema_version": "prediction-snapshot-v2",
            "raw_candidate": {
                **payload,
                "blend_validation": {
                    "status": "valid",
                    "issues": [],
                    "design_space_digest": blend.design_space.digest,
                },
            },
        },
    )
    assert restored.blend == blend
    assert restored.editor_state.locked_material_ids == ["RM-0002"]
    assert restored.blend_validation.status == "not_applicable"
    recomputed = service._prepare("blend-task", restored)
    assert recomputed.blend_validation == prepared.blend_validation


def test_task_without_sparse_blend_capability_rejects_blend(client) -> None:
    _, _, _, registry = _contracts()
    client.app.state.blend_contract_registry = registry
    source = client.get("/api/projects/default/candidates").json()[0]
    payload = {
        key: deepcopy(source[key])
        for key in ("name", "inputs", "provenance")
    }
    payload["blend"] = _blend().model_dump(mode="json")

    created = client.post("/api/projects/default/candidates", json=payload)

    assert created.status_code == 422
    assert "sparse blend" in created.json()["message"]


def test_invalid_blend_xlsx_export_leaves_prediction_blank_without_predicting(client) -> None:
    _, _, _, registry = _contracts()
    source = client.get("/api/projects/default/candidates").json()[0]
    blend = _blend(
        items=(
            BlendItem(material_id="RM-0001", ratio=10),
            BlendItem(material_id="RM-0002", ratio=10),
        )
    )
    validation = validate_sparse_blend(blend, registry.resolve(blend))
    candidate = Candidate.model_validate({
        **source,
        "blend": blend.model_dump(mode="json"),
        "blend_validation": validation.model_dump(mode="json"),
    })
    actual_runtime = client.app.state.task_registry.entry_for(
        "annealed-properties-v1"
    ).predictor_runtime

    class RuntimeThatMustNotPredict:
        data = actual_runtime.data

        def predict(self, *_args: object, **_kwargs: object) -> object:
            raise AssertionError("invalid draft must not be predicted")

    workbook = load_workbook(BytesIO(candidates_xlsx(
        [candidate],
        RuntimeThatMustNotPredict(),  # type: ignore[arg-type]
        task_id="annealed-properties-v1",
    )))
    values = [cell.value for cell in workbook["候補"][2]]
    output_count = len(
        load_task_contracts()["annealed-properties-v1"].task_definition.outputs
    )
    assert all(value is None for value in values[-(output_count + 2):])
