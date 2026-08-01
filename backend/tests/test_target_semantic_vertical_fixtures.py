from __future__ import annotations

from pathlib import Path

import pytest

from decision_workbench.application.records import normalize_actual_measurement
from decision_workbench.contracts.prediction_catalog_contracts import (
    ActualMeasurementInput,
    Prediction,
)
from decision_workbench.contracts.task_contracts import TaskContractFixture
from decision_workbench.modeling.packages.contracts import (
    validate_task_definition_canonical_inputs,
)
from decision_workbench.modeling.packages.loader import ModelPackageLoader


ROOT = Path(__file__).resolve().parents[2]


def _fixture(kind: str) -> TaskContractFixture:
    semantics: dict[str, object] = {
        "binary": {"binary": {"event_label": "fail", "non_event_label": "pass"}},
        "count": {"count": {"count_unit": "items"}},
        "ordinal": {"ordinal": {"categories": ["low", "medium", "high"]}},
    }[kind]
    statistic = {"binary": "probability", "count": "rate", "ordinal": "expected_category"}[kind]
    return TaskContractFixture.model_validate({
        "task_definition": {
            "schema_version": "task-definition/v1", "id": "model-package-example",
            "label": "semantic fixture", "canonical_candidate_schema_version": "canonical-candidate/v1",
            "input_groups": [{"key": "composition", "order": 0, "label": "input", "fields": [
                {"path": "composition.C", "kind": "number", "order": 0, "label": "C", "unit": "1", "default_range": {"min": 0, "max": 1}, "allowed_range": {"min": 0, "max": 2}, "training_range": {"min": 0, "max": 1}},
                {"path": "composition.Mn", "kind": "number", "order": 1, "label": "Mn", "unit": "1", "default_range": {"min": 0, "max": 2}, "allowed_range": {"min": 0, "max": 3}, "training_range": {"min": 0, "max": 2}},
            ]}],
            "outputs": [{"key": "example", "label": kind, "unit": "1", "target_kind": kind, **semantics, "goal_direction": "at_most", "plausibility_range": {"min": 0, "max": 100}, "preferred_display_range": {"min": 0, "max": 10}}],
            "display_decimals": {"composition.C": 2, "composition.Mn": 2, "output.example": 2},
        },
        "canonical_candidate": {"schema_version": "canonical-candidate/v1", "task_id": "model-package-example", "composition": {"C": 0.08, "Mn": 1.5}, "provenance": {"source_kind": "direct", "source_ref": None}},
        "runtime_capability": {"schema_version": "runtime-capability/v1", "task_id": "model-package-example", "model_package_schema_version": "model-package/v1", "targets": [{"target": "example", "target_kind": kind, "point_statistics": [statistic], "standard_deviation": False, "quantiles": True, "samples": False, "parametric_distribution": True, "uncertainty_components": False, "support": True, "warnings": True, "goal_probability": "unavailable"}], "operations": {"preview": True, "detailed_prediction": True, "response_curve": False, "similarity": False, "snapshot": True, "actual_measurement": True}},
    })


@pytest.mark.parametrize("kind", ["binary", "count", "ordinal"])
def test_inactive_target_semantic_fixtures_reach_package_prediction_snapshot_and_actual(kind: str) -> None:
    fixture = _fixture(kind)
    package = ModelPackageLoader().load(ROOT / "examples" / "model-packages" / "numpyro" / {"binary": "bernoulli_logit", "count": "poisson_log", "ordinal": "ordinal_logit"}[kind])
    validate_task_definition_canonical_inputs(fixture.task_definition, package.manifest)
    summary = package.load_predictor("target").predict({"C": 0.08, "Mn": 1.5}, seed=7)
    snapshot_prediction = Prediction(
        value=summary.point_estimate, lower=min(summary.quantiles.values()), upper=max(summary.quantiles.values()),
        unit=summary.unit, target_kind=summary.target_kind, point_statistic=summary.point_statistic,
        predictive_family=summary.distribution["family"], quantiles=summary.quantiles,
        categories=summary.distribution.get("categories", []),
    )
    assert snapshot_prediction.target_kind == kind
    observed = {"binary": "fail", "count": 2, "ordinal": "high"}[kind]
    actual = normalize_actual_measurement(
        ActualMeasurementInput(property="example", value=observed, std=0, replicates=1, unit="1"),
        fixture.task_definition.outputs[0],
    )
    assert actual.mean is not None
    if kind == "count":
        assert all(value >= 0 for value in snapshot_prediction.quantiles.values())
    if kind == "ordinal":
        assert actual.value_label == "high"


def test_task_package_target_kind_mismatch_fails_closed() -> None:
    fixture = _fixture("binary")
    package = ModelPackageLoader().load(
        ROOT / "examples" / "model-packages" / "numpyro" / "poisson_log"
    )
    with pytest.raises(Exception, match="target kinds"):
        validate_task_definition_canonical_inputs(fixture.task_definition, package.manifest)
