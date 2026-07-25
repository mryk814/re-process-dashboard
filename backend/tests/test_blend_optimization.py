from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from material_workbench.application.blend_optimization import BlendOptimizationService
from material_workbench.contracts.blend_contracts import (
    BlendContractRegistry,
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
from material_workbench.contracts.blend_optimization import BlendOptimizationRequest
from material_workbench.contracts.schemas import Candidate, CandidateInput, CandidateInputs


def _resources():
    master = ScientificMaterialMaster(
        schema_version="scientific-material-master/v1",
        resource_id="optimization-science",
        revision=1,
        materials=(
            ScientificMaterial(
                material_id="iron",
                name="鉄粉",
                material_type="metal",
                group="base",
                d50_um=70,
            ),
            ScientificMaterial(
                material_id="manganese",
                name="Mn粉",
                material_type="alloy",
                group="alloy",
                d50_um=60,
            ),
            ScientificMaterial(
                material_id="rich-manganese",
                name="高Mn粉",
                material_type="alloy",
                group="alloy",
                d50_um=50,
            ),
        ),
        hoops=(ScientificHoop(hoop_id="hoop", name="純鉄フープ"),),
    )
    catalog = CommercialMaterialCatalog(
        schema_version="commercial-material-catalog/v1",
        resource_id="optimization-cost",
        revision=1,
        materials=(
            CommercialMaterial(
                material_id="iron",
                procurement="常用",
                unit_price_yen_per_kg_core=100,
            ),
            CommercialMaterial(
                material_id="manganese",
                procurement="常用",
                unit_price_yen_per_kg_core=300,
            ),
            CommercialMaterial(
                material_id="rich-manganese",
                procurement="条件付",
                unit_price_yen_per_kg_core=500,
            ),
        ),
    )
    space = SparseBlendDesignSpace(
        schema_version="sparse-blend-design-space/v1",
        resource_id="optimization-space",
        revision=1,
        scientific_master=master.ref,
        commercial_catalog=catalog.ref,
        allowed_material_ids=("iron", "manganese", "rich-manganese"),
        material_bounds=(
            MaterialRatioBound(material_id="iron", lower=40, upper=95),
            MaterialRatioBound(material_id="manganese", lower=0, upper=40),
            MaterialRatioBound(material_id="rich-manganese", lower=0, upper=20),
        ),
        group_totals=(GroupTotalConstraint(group="alloy", lower=5, upper=45),),
        group_cardinalities=(
            GroupCardinalityConstraint(group="alloy", minimum=1, maximum=1),
        ),
        selection_count=SelectionCountConstraint(minimum=2, maximum=2),
        fixed_hoop_id="hoop",
        fixed_fill_ratio=25,
        balance_material_id="iron",
    )
    registry = BlendContractRegistry((master,), (catalog,), (space,))
    scientific = SimpleNamespace(
        ref=master.ref,
        components=("Fe", "Mn"),
        materials=(
            SimpleNamespace(
                material_id="iron",
                composition={"Fe": 100.0, "Mn": 0.0},
            ),
            SimpleNamespace(
                material_id="manganese",
                composition={"Fe": 50.0, "Mn": 50.0},
            ),
            SimpleNamespace(
                material_id="rich-manganese",
                composition={"Fe": 0.0, "Mn": 100.0},
            ),
        ),
        hoops=(
            SimpleNamespace(
                hoop_id="hoop",
                composition={"Fe": 100.0, "Mn": 0.0},
            ),
        ),
    )
    transforms = SimpleNamespace(
        entry=lambda _transform_id: SimpleNamespace(
            transform=SimpleNamespace(
                artifact=SimpleNamespace(scientific_master=scientific)
            )
        )
    )
    return master, catalog, space, registry, transforms


def _candidate(master, catalog, space) -> Candidate:
    now = datetime.now(UTC)
    return Candidate(
        id="baseline",
        project_id="project",
        revision=3,
        name="基準",
        inputs=CandidateInputs(composition={}, process={}),
        blend=SparseBlend(
            items=(
                {"material_id": "iron", "ratio": 80.0},
                {"material_id": "manganese", "ratio": 20.0},
            ),
            hoop_id="hoop",
            fill_ratio=25,
            balance_material_id="iron",
            scientific_master=master.ref,
            commercial_catalog=catalog.ref,
            design_space=space.ref,
        ),
        provenance={"source_kind": "direct", "source_ref": None},
        created_at=now,
        updated_at=now,
    )


class _CandidateService:
    def __init__(self, baseline: Candidate, registry: BlendContractRegistry) -> None:
        self.baseline = baseline
        self.registry = registry
        self.created_payload: CandidateInput | None = None

    def get(self, project_id: str, candidate_id: str) -> Candidate:
        assert (project_id, candidate_id) == ("project", "baseline")
        return self.baseline

    def at_revision(
        self, project_id: str, candidate_id: str, expected_revision: int
    ) -> Candidate:
        candidate = self.get(project_id, candidate_id)
        if candidate.revision != expected_revision:
            raise AssertionError("unexpected revision")
        return candidate

    def create(self, project_id: str, payload: CandidateInput) -> Candidate:
        assert project_id == "project"
        assert payload.blend is not None
        contracts = self.registry.resolve(payload.blend)
        validation = validate_sparse_blend(payload.blend, contracts)
        assert validation.status == "valid"
        self.created_payload = payload
        now = datetime.now(UTC)
        return Candidate(
            **payload.model_dump(mode="python", exclude={"blend_validation"}),
            id="optimized",
            project_id=project_id,
            revision=1,
            blend_validation=validation,
            created_at=now,
            updated_at=now,
        )


def _service():
    master, catalog, space, registry, transforms = _resources()
    candidates = _CandidateService(_candidate(master, catalog, space), registry)
    return BlendOptimizationService(candidates, registry, transforms), candidates


def _request(**updates) -> BlendOptimizationRequest:
    payload = {
        "expected_revision": 3,
        "name": "逆算結果",
        "objective": "cost",
        "inclusion_decisions": False,
        "material_ids": ("iron", "manganese"),
        "composition_targets": ({"component": "Mn", "lower": 2.0, "upper": 3.0},),
    }
    payload.update(updates)
    return BlendOptimizationRequest.model_validate(payload)


def test_feasible_lp_obeys_whole_wire_composition_bounds_and_is_saved_as_candidate() -> None:
    service, candidates = _service()

    result = service.run("project", "baseline", _request())

    assert result.status == "feasible"
    assert result.method == "highs-lp"
    assert result.objective_unit == "yen/kg-core"
    assert result.candidate is not None
    assert result.candidate.blend_validation.status == "valid"
    ratio = {
        item.material_id: item.ratio for item in result.candidate.blend.items
    }
    assert sum(ratio.values()) == pytest.approx(100)
    assert ratio["manganese"] == pytest.approx(16.0)
    assert candidates.created_payload is not None
    provenance = candidates.created_payload.provenance
    assert provenance.source_kind == "blend_optimization"
    assert provenance.source_ref.baseline_candidate_revision == 3
    assert provenance.source_ref.design_space_digest.startswith("sha256:")


def test_infeasible_lp_returns_slack_minimization_relaxation_candidates() -> None:
    service, candidates = _service()

    result = service.run(
        "project",
        "baseline",
        _request(
            composition_targets=(
                {"component": "Mn", "lower": 20.0, "upper": 21.0},
            )
        ),
    )

    assert result.status == "infeasible"
    assert result.candidate is None
    assert candidates.created_payload is None
    assert any(
        item.constraint == "composition:Mn" and item.direction == "lower"
        for item in result.relaxation_candidates
    )
    assert "矛盾の証明ではありません" in result.message


def test_feasible_milp_enforces_selection_group_cardinality_and_balance() -> None:
    service, _ = _service()

    result = service.run(
        "project",
        "baseline",
        _request(
            inclusion_decisions=True,
            material_ids=("iron", "manganese", "rich-manganese"),
            composition_targets=(
                {"component": "Mn", "lower": 3.0, "upper": 4.0},
            ),
        ),
    )

    assert result.status == "feasible"
    assert result.method == "highs-milp"
    assert result.candidate is not None
    selected = {item.material_id for item in result.candidate.blend.items}
    assert "iron" in selected
    assert len(selected) == 2
    assert len(selected & {"manganese", "rich-manganese"}) == 1


def test_infeasible_milp_reports_composition_relaxation_without_creating_candidate() -> None:
    service, _ = _service()

    result = service.run(
        "project",
        "baseline",
        _request(
            inclusion_decisions=True,
            material_ids=("iron", "manganese", "rich-manganese"),
            composition_targets=(
                {"component": "Mn", "lower": 30.0, "upper": 31.0},
            ),
        ),
    )

    assert result.status == "infeasible"
    assert result.method == "highs-milp"
    assert result.relaxation_candidates


def test_baseline_l1_objective_keeps_the_closest_feasible_blend() -> None:
    service, _ = _service()

    result = service.run(
        "project",
        "baseline",
        _request(
            objective="baseline_l1",
            composition_targets=(
                {"component": "Mn", "lower": 2.0, "upper": 10.0},
            ),
        ),
    )

    assert result.status == "feasible"
    assert result.candidate is not None
    assert result.objective_unit == "core mass %"
    assert result.objective_value == pytest.approx(0.0)
    ratio = {
        item.material_id: item.ratio for item in result.candidate.blend.items
    }
    assert ratio["manganese"] == pytest.approx(20.0)
