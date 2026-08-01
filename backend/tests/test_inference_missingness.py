from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from decision_workbench.application.missing_completion_lab import (
    _model_uncertainty,
    run_missing_completion_lab,
)
from decision_workbench.contracts.candidate_project_contracts import Candidate
from decision_workbench.contracts.prediction_catalog_contracts import Prediction
from decision_workbench.design_priors.builder import build_design_prior_package
from decision_workbench.design_priors.contracts import (
    DesignPriorObservation,
    DesignPriorSource,
)
from decision_workbench.design_priors.loader import DesignPriorPackageLoader
from decision_workbench.modeling.missingness import (
    assess_input_missingness,
    pattern_digest,
    pattern_support_policy_document,
    require_operation_allowed,
)


def _input(
    path: str,
    *,
    kind: str = "number",
    choices: tuple[str, ...] = (),
    main_effect: bool = True,
) -> SimpleNamespace:
    return SimpleNamespace(
        path=path,
        column=path,
        kind=kind,
        choices=choices,
        main_effect=main_effect,
        numeric_missing=SimpleNamespace(
            strategy="training_median_with_indicator",
            value=None,
        ),
        categorical_missing=SimpleNamespace(
            strategy="map_to_missing_category",
            category=None,
        ),
        unknown_category=SimpleNamespace(
            strategy="reject",
            other_choice=None,
        ),
    )


def _candidate(**process: float) -> Candidate:
    now = datetime.now(UTC)
    return Candidate.model_validate({
        "id": "candidate-missing",
        "project_id": "project",
        "revision": 1,
        "created_at": now,
        "updated_at": now,
        "name": "missing",
        "inputs": {
            "composition": {},
            "process": process,
            "categorical": {"route": "A"},
        },
    })


def test_preview_is_provisional_but_detailed_snapshot_and_proposal_are_blocked() -> None:
    inputs = (_input("process.x"),)
    candidate = _candidate()
    digest = pattern_digest((("process.x", "not_measured"),))
    stats = {
        "missing_policy": {
            "policy_digest": "sha256:" + "a" * 64,
            "training_rows": 10,
            "missing_by_input": {"process.x": 3},
            "imputation_values": {"process.x": 5.0},
            "pattern_evidence": [{
                "pattern_digest": digest,
                "training_count": 3,
                "evaluation_count": 1,
                "metrics_by_target": {"y": {"evaluation_count": 1, "mae": 0.4}},
            }],
        },
    }

    preview = assess_input_missingness(
        candidate, inputs, stats, operation="preview"
    )
    assert preview.input_completeness == "imputed"
    assert preview.prediction_status == "provisional"
    assert preview.missingness_support == "sparse"
    assert preview.fields[0].training_missing_rate == pytest.approx(0.3)
    assert preview.support_policy_digest == (
        pattern_support_policy_document()["policy_digest"]
    )
    assert preview.uncertainty_propagated is False
    require_operation_allowed(preview)

    for operation in ("detailed_prediction", "snapshot", "proposal", "export"):
        evidence = assess_input_missingness(
            candidate, inputs, stats, operation=operation
        )
        assert evidence.prediction_status == "blocked"
        with pytest.raises(ValueError, match="process.x"):
            require_operation_allowed(evidence)


def test_interaction_only_input_is_not_inferred_as_structurally_inactive() -> None:
    route = _input("categorical.route", kind="categorical", choices=("A", "B"))
    inactive = _input("process.inactive", main_effect=False)
    candidate = _candidate()
    candidate.inputs.categorical["route"] = "new-material"

    evidence = assess_input_missingness(
        candidate,
        (route, inactive),
        {},
        operation="preview",
    )

    assert [(item.path, item.kind) for item in evidence.fields] == [
        ("categorical.route", "unknown_category"),
        ("process.inactive", "not_measured"),
    ]
    assert evidence.missingness_support == "incompatible"
    assert evidence.prediction_status == "blocked"

    candidate.input_missing_kinds["process.inactive"] = (
        "structural_not_applicable"
    )
    explicit = assess_input_missingness(
        candidate,
        (inactive,),
        {},
        operation="preview",
    )
    assert explicit.fields[0].kind == "structural_not_applicable"
    assert explicit.fields[0].imputed_value is None
    assert explicit.missingness_support == "incompatible"
    assert explicit.prediction_status == "blocked"

    candidate.input_missing_kinds["process.inactive"] = "redacted"
    redacted = assess_input_missingness(
        candidate,
        (inactive,),
        {},
        operation="preview",
    )
    assert redacted.fields[0].kind == "redacted"
    assert redacted.fields[0].imputed_value is None
    assert redacted.prediction_status == "blocked"


def test_completion_lab_does_not_invent_decomposed_model_uncertainty() -> None:
    prediction = Prediction(
        value=1.0,
        lower=0.0,
        upper=2.0,
        unit="1",
        target_kind="continuous",
        point_statistic="mean",
        predictive_family="empirical_quantiles",
        quantiles={"0.05": 0.0, "0.95": 2.0},
    )

    with pytest.raises(ValueError, match="model uncertainty"):
        _model_uncertainty(prediction)
    normal_without_components = prediction.model_copy(
        update={"predictive_family": "normal"}
    )
    with pytest.raises(ValueError, match="model uncertainty"):
        _model_uncertainty(normal_without_components)


def test_empirical_completion_lab_separates_model_and_input_uncertainty(
    tmp_path,
) -> None:
    package_root = build_design_prior_package(
        tmp_path / "prior",
        package_id="completion-prior",
        package_version="1.0.0",
        task_id="fixture",
        task_contract_digest="sha256:" + "1" * 64,
        canonical_input_schema_version="candidate-v1",
        canonical_input_paths=(
            "process.temperature",
            "process.time",
            "categorical.route",
        ),
        source=DesignPriorSource(dataset_view_digest="sha256:" + "2" * 64),
        observations=(
            DesignPriorObservation(
                sample_id="a",
                inputs={
                    "process.temperature": 700.0,
                    "process.time": 10.0,
                    "categorical.route": "A",
                },
            ),
            DesignPriorObservation(
                sample_id="b",
                inputs={
                    "process.temperature": 710.0,
                    "process.time": 30.0,
                    "categorical.route": "A",
                },
            ),
        ),
        training_code_revision="git:test",
    )
    package = DesignPriorPackageLoader().load(package_root)

    class Runtime:
        task_id = "fixture"
        task_contract_digest = "sha256:" + "1" * 64
        canonical_input_schema_version = "candidate-v1"
        missing_policy_inputs = (
            _input("process.temperature"),
            _input("process.time"),
            _input("categorical.route", kind="categorical", choices=("A",)),
        )

        def predict_core(self, candidate, **_):
            value = float(candidate.inputs.process["time"])
            return {
                "predictions": {
                    "y": Prediction(
                        value=value,
                        lower=value - 1.0,
                        upper=value + 1.0,
                        unit="1",
                        target_kind="continuous",
                        point_statistic="mean",
                        predictive_family="normal",
                        quantiles={"0.05": value - 1.0, "0.95": value + 1.0},
                        uncertainty_components={
                            "total_predictive_std": 1.0,
                        },
                    )
                }
            }

    class WrongTaskRuntime(Runtime):
        task_id = "other"

    with pytest.raises(ValueError, match="Task、contract、canonical schema"):
        run_missing_completion_lab(
            WrongTaskRuntime(),
            _candidate(temperature=705.0),
            package,
            generator_id="empirical_rows",
            sample_count=2,
            seed=9,
        )

    class WrongContractRuntime(Runtime):
        task_contract_digest = "sha256:" + "3" * 64

    with pytest.raises(ValueError, match="Task、contract、canonical schema"):
        run_missing_completion_lab(
            WrongContractRuntime(),
            _candidate(temperature=705.0),
            package,
            generator_id="empirical_rows",
            sample_count=2,
            seed=9,
        )

    report = run_missing_completion_lab(
        Runtime(),
        _candidate(temperature=705.0),
        package,
        generator_id="empirical_rows",
        sample_count=32,
        seed=9,
    )

    assert report.missing_paths == ("process.time",)
    assert report.summaries[0].uncertainty.input_missingness > 0
    assert report.summaries[0].uncertainty.combined > (
        report.summaries[0].uncertainty.model
    )
    assert {
        item["manifest_digest"] for item in report.completion_evidence
    } == {f"sha256:{package.manifest_sha256}"}
