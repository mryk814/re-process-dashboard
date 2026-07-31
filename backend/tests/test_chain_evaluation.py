from __future__ import annotations

import hashlib
import math
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from decision_workbench.application.chain_evaluation import (
    ChainEvaluationCatalog,
    ChainEvaluationError,
)
from decision_workbench.contracts.chain_evaluation_contracts import (
    ChainEvaluationReport,
)
from decision_workbench.modeling.chain_evaluation_builder import (
    build_chain_evaluation,
)
from decision_workbench.modeling.model_lifecycle import resolve_configured_package
from decision_workbench.modeling.numeric_canonicalization import (
    canonicalize_report_float,
)


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "data/source/welding_consumable_multistage_synthetic_dataset.xlsx"
ARTIFACT = ROOT / "models/evaluations/welding-consumable-a-b-c-v1.json"


def _source_digest() -> str:
    return hashlib.sha256(SOURCE.read_bytes()).hexdigest()


def _build() -> ChainEvaluationReport:
    return build_chain_evaluation(
        source=SOURCE,
        stage_b_profile=ROOT
        / "backend/src/decision_workbench/data/welding-stage-b-profile-v1.json",
        # Stage AはTaskを持たない決定論的transformなのでパスで指す。
        # Stage B・Cは、成果物と同じく使用中Packageから解決する。
        stage_a_package=ROOT
        / "models/packages/welding-stage-a-deterministic-v1",
        stage_b_package=resolve_configured_package("welding-consumable-stage-b-v1"),
        stage_c_package=resolve_configured_package("welding-stage-c-properties-v1"),
    )


def test_committed_chain_evaluation_is_reproducible_and_leakage_safe() -> None:
    before = _source_digest()
    committed = ChainEvaluationReport.model_validate_json(
        ARTIFACT.read_text(encoding="utf-8")
    )
    rebuilt = _build()

    assert rebuilt == committed
    assert _source_digest() == before
    assert committed.split.assignment_policy == "sorted-group-round-robin"
    assert all(
        sorted(set(assignment.values())) == list(range(committed.split.folds))
        for assignment in committed.split.assignments.values()
    )
    assert all(
        item.upstream_training_source == "inner-grouped-oof"
        and item.upstream_test_source == "outer-train-only"
        and item.upstream_self_fit_violations == 0
        and item.outer_test_training_overlap == 0
        for item in committed.fold_evidence
    )


def test_report_float_canonicalization_removes_platform_noise() -> None:
    assert canonicalize_report_float(11.154300133103579) == (
        canonicalize_report_float(11.154300133103591)
    )
    assert canonicalize_report_float(0.05106776556335) == (
        canonicalize_report_float(0.05106776556334)
    )
    assert math.copysign(1.0, canonicalize_report_float(-0.0)) == 1.0
    with pytest.raises(ValueError, match="must be finite"):
        canonicalize_report_float(float("nan"), label="chain evaluation MAE")


def test_output_specific_cohorts_keep_stage_and_end_to_end_on_same_scale() -> None:
    report = ChainEvaluationReport.model_validate_json(
        ARTIFACT.read_text(encoding="utf-8")
    )
    targets = {item.target: item for item in report.targets}

    assert targets["TS"].observations == 600
    assert targets["CHARPY_ENERGY"].observations == 2700
    assert targets["CORROSION_RATE"].observations == 103
    assert targets["TS"].split_groups == 300
    assert targets["CORROSION_RATE"].split_groups == 103
    for target in targets.values():
        assert target.stage_only.rmse >= 0
        assert target.end_to_end.rmse >= 0
        assert target.unit
        assert target.cohort.endswith(":target-usable")
    assert set(report.metric_definitions) == {"mae", "rmse"}


def test_chain_project_evaluation_resolves_exact_revision(client: TestClient) -> None:
    template = next(
        item
        for item in client.get("/api/chains").json()
        if item["definition"]["chain_id"] == "welding-consumable-a-b-c-v1"
    )
    revision = template["revisions"][0]
    created = client.post(
        "/api/projects",
        json={
            "name": "Chain evaluation",
            "scientific_identity": {
                "identity_kind": "chain",
                "chain_revision_id": "welding-consumable-a-b-c-v1:r1",
                "chain_revision_digest": revision["revision_digest"],
            },
        },
    )
    assert created.status_code == 201, created.text

    response = client.get(
        f"/api/projects/{created.json()['id']}/chain/evaluation"
    )
    assert response.status_code == 200, response.text
    resolved = response.json()
    assert resolved["chain_revision_digest"] == revision["revision_digest"]
    assert resolved["artifact_digest"].startswith("sha256:")
    assert set(resolved["dataset_view_revision_ids"]) == {"B", "C"}
    assert len(resolved["report"]["targets"]) == 7


def test_chain_evaluation_rejects_stage_identity_drift(client: TestClient) -> None:
    revision = client.app.state.store.get_chain_revision(
        "welding-consumable-a-b-c-v1:r1"
    )
    assert revision is not None
    report = ChainEvaluationReport.model_validate_json(
        ARTIFACT.read_text(encoding="utf-8")
    )
    drifted = report.model_copy(
        update={
            "stages": (
                report.stages[0],
                report.stages[1].model_copy(
                    update={
                        "package_manifest_digest": "sha256:" + "0" * 64
                    }
                ),
                report.stages[2],
            )
        }
    )
    catalog = ChainEvaluationCatalog(((drifted, "sha256:" + "1" * 64),))

    try:
        catalog.resolve(
            revision_id="welding-consumable-a-b-c-v1:r1",
            revision=revision,
            stage_source_digests={
                "B": {report.source_data_digest},
                "C": {report.source_data_digest},
            },
        )
    except ChainEvaluationError as exc:
        assert "Stage B" in str(exc)
    else:
        raise AssertionError("drifted evaluation identity must be rejected")


def test_chain_evaluation_rejects_chain_binding_identity_drift(
    client: TestClient,
) -> None:
    revision = client.app.state.store.get_chain_revision(
        "welding-consumable-a-b-c-v1:r1"
    )
    assert revision is not None
    report = ChainEvaluationReport.model_validate_json(
        ARTIFACT.read_text(encoding="utf-8")
    )
    catalog = ChainEvaluationCatalog(((report, "sha256:" + "1" * 64),))
    drifted = revision.model_copy(
        update={"binding_digest": "sha256:" + "0" * 64}
    )

    try:
        catalog.resolve(
            revision_id="welding-consumable-a-b-c-v1:r1",
            revision=drifted,
            stage_source_digests={
                "B": {report.source_data_digest},
                "C": {report.source_data_digest},
            },
        )
    except ChainEvaluationError as exc:
        assert "binding identity" in str(exc)
    else:
        raise AssertionError("drifted binding identity must be rejected")


def test_chain_evaluation_rejects_source_identity_drift(client: TestClient) -> None:
    revision = client.app.state.store.get_chain_revision(
        "welding-consumable-a-b-c-v1:r1"
    )
    assert revision is not None
    report = ChainEvaluationReport.model_validate_json(
        ARTIFACT.read_text(encoding="utf-8")
    )
    catalog = ChainEvaluationCatalog(((report, "sha256:" + "1" * 64),))

    try:
        catalog.resolve(
            revision_id="welding-consumable-a-b-c-v1:r1",
            revision=revision,
            stage_source_digests={
                "B": {"sha256:" + "0" * 64},
                "C": {report.source_data_digest},
            },
        )
    except ChainEvaluationError as exc:
        assert "source identity" in str(exc)
    else:
        raise AssertionError("drifted source identity must be rejected")


def test_chain_evaluation_rejects_fold_assignment_digest_drift() -> None:
    payload = ChainEvaluationReport.model_validate_json(
        ARTIFACT.read_text(encoding="utf-8")
    ).model_dump(mode="json")
    payload["split"]["assignment_digest"] = "sha256:" + "0" * 64

    try:
        ChainEvaluationReport.model_validate(payload)
    except ValueError as exc:
        assert "fold assignment digest" in str(exc)
    else:
        raise AssertionError("drifted fold assignment digest must be rejected")
