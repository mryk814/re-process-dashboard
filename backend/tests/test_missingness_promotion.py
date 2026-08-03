from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from decision_workbench.contracts.candidate_project_contracts import Candidate
from decision_workbench.contracts.missingness_contracts import (
    MissingnessOperationCapability,
)
from decision_workbench.modeling.missingness import (
    assess_input_missingness,
    classify_missingness_pattern_support,
    pattern_digest,
    pattern_support_policy_document,
    require_operation_allowed,
    resolve_missingness_operation_capability,
)
from decision_workbench.modeling.packages.contracts import (
    PipelineMissingPolicySpec,
)


ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / "docs/reports/mpea-missingness-promotion.json"


def _report() -> dict:
    return json.loads(REPORT.read_text(encoding="utf-8"))


def _input(
    path: str,
    *,
    kind: str = "number",
    choices: tuple[str, ...] = (),
) -> SimpleNamespace:
    return SimpleNamespace(
        path=path,
        kind=kind,
        choices=choices,
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


def _candidate(*, x: float | None, route: str = "yes") -> Candidate:
    now = datetime.now(UTC)
    return Candidate.model_validate({
        "id": "candidate",
        "project_id": "project",
        "revision": 1,
        "created_at": now,
        "updated_at": now,
        "name": "candidate",
        "inputs": {
            "composition": {},
            "process": {} if x is None else {"x": x},
            "categorical": {"route": route},
        },
    })


def _training_stats(training_count: int = 20) -> dict:
    digest = pattern_digest((("process.x", "not_measured"),))
    return {
        "missing_policy": {
            "policy_digest": "sha256:" + "a" * 64,
            "training_rows": 100,
            "missing_by_input": {"process.x": training_count},
            "imputation_values": {"process.x": 1.5},
            "pattern_support_policy": pattern_support_policy_document(),
            "pattern_evidence": [{
                "pattern_digest": digest,
                "training_count": training_count,
                "evaluation_count": 10,
                "metrics_by_target": {
                    "y": {
                        "evaluation_count": 10,
                        "prediction_failure_rate": 0.0,
                        "rmse": 0.2,
                    }
                },
            }],
        }
    }


def _capability() -> MissingnessOperationCapability:
    return MissingnessOperationCapability(
        preview="warn",
        comparison="warn",
        snapshot="allow",
        proposal="allow_with_quota",
        export="allow_provisional",
        completion_uncertainty="available",
    )


def test_mpea_protocol_uses_group_holdout_and_verified_evaluation_package() -> None:
    report = _report()
    protocol = report["fixed_protocol"]
    authority = report["package_authority"]

    assert report["task"]["task_id"] == "mpea-room-tensile-v1"
    assert report["task"]["target"] == "TYS"
    assert protocol["train_rows"] == 75
    assert protocol["train_groups"] == 30
    assert protocol["holdout_rows"] == 24
    assert protocol["holdout_groups"] == 9
    assert protocol["same_holdout_for_masked_patterns"] is True
    assert protocol["target_excluded_from_completion"] is True
    assert protocol["train_group_digest"] != protocol["holdout_group_digest"]

    active = authority["active_recipe_reference_only"]
    evaluation = authority["evaluation_package"]
    assert active["package_id"] == "mpea-room-tensile-ridge-v2"
    assert "full public source" in active["reason_not_used_for_holdout"]
    assert evaluation["package_id"] != active["package_id"]
    assert evaluation["verified"] is True
    assert evaluation["training_code_revision"] == (
        "standard-model-training/v1:ridge.v1"
    )
    assert evaluation["runtime_class"] == "TabularRegressionRuntime"
    assert evaluation["manifest_digest"].startswith("sha256:")
    assert evaluation["capability_source"].startswith(
        "feature-pipeline/pipeline.json"
    )


def test_mixed_missingness_patterns_share_identity_and_support_classifier() -> None:
    report = _report()
    patterns = {
        item["pattern_id"]: item for item in report["patterns"]
    }
    assert set(patterns) == {
        "complete",
        "low-impact-numeric-single",
        "high-impact-numeric-single",
        "correlated-process-pair",
        "frequent-process-multi",
        "sparse-process-multi",
        "unseen-mixed-mode",
    }
    assert (
        patterns["sparse-process-multi"][
            "observed_training_pattern_count"
        ]
        == 1
    )
    assert patterns["sparse-process-multi"]["support"] == "sparse"
    assert (
        patterns["unseen-mixed-mode"][
            "observed_training_pattern_count"
        ]
        == 0
    )
    assert patterns["unseen-mixed-mode"]["support"] == "unseen"
    for pattern_id in (
        "low-impact-numeric-single",
        "high-impact-numeric-single",
        "correlated-process-pair",
    ):
        assert patterns[pattern_id]["masking_cohort_count"] > 0
        assert patterns[pattern_id]["observed_training_pattern_count"] == 0
        assert patterns[pattern_id]["support"] == "unseen"
    assert (
        patterns["frequent-process-multi"][
            "observed_training_pattern_count"
        ]
        == 8
    )
    assert patterns["frequent-process-multi"]["support"] == "supported"
    assert (
        patterns["low-impact-numeric-single"]["metrics"][
            "package_imputation"
        ]["mean_absolute_point_shift_vs_complete"]
        < patterns["high-impact-numeric-single"]["metrics"][
            "package_imputation"
        ]["mean_absolute_point_shift_vs_complete"]
    )

    expected_evaluation_counts = {
        "complete": 24,
        "low-impact-numeric-single": 24,
        "high-impact-numeric-single": 11,
        "correlated-process-pair": 13,
        "frequent-process-multi": 24,
        "sparse-process-multi": 24,
        "unseen-mixed-mode": 2,
    }
    for pattern_id, pattern in patterns.items():
        assert pattern["masking_holdout_evaluation_count"] == (
            expected_evaluation_counts[pattern_id]
        )
        assert pattern["pattern_digest"].startswith("sha256:")
        if pattern_id == "complete":
            continue
        assert pattern["generation_policy"]["digest"].startswith("sha256:")
        assert pattern["metrics"]["package_imputation"]["failure_rate"] == 0
        for generator in ("empirical_rows", "knn_local"):
            metrics = pattern["metrics"]["conditional_completion"][generator]
            assert metrics["evaluation_count"] == (
                expected_evaluation_counts[pattern_id]
            )
            assert metrics["seed"] == report["fixed_protocol"]["seed"]
            assert metrics["sample_count"] == (
                report["fixed_protocol"]["completion_sample_count"]
            )
            assert metrics["generator_version"] == "1.0.0"
            assert metrics["model_uncertainty"]["available"] is True
            assert (
                metrics["input_missingness_uncertainty"]["available"]
                is True
            )
            assert metrics["completion_sample_identity_digest"].startswith(
                "sha256:"
            )


def test_design_prior_and_real_category_semantics_are_explicit() -> None:
    report = _report()
    prior = report["design_prior_authority"]
    categories = report["category_semantics"]

    assert prior["rows"] == 75
    assert prior["manifest_digest"].startswith("sha256:")
    assert prior["dataset_view_digest"].startswith("sha256:")
    assert {item["id"] for item in prior["generators"]} == {
        "empirical_rows",
        "knn_local",
    }
    assert all(item["version"] == "1.0.0" for item in prior["generators"])

    alias = categories["alias"]
    assert alias["source_alias_count"] > 0
    assert alias["status"] == "alias_resolved"
    assert alias["unknown_category_coercion"] is False
    assert all(
        values == ["no", "yes"]
        for values in alias["canonical_values_after_curation"].values()
    )
    assert categories["structural_inactive"]["support"] == "incompatible"
    assert categories["structural_inactive"]["masking_cohort_count"] == 40
    assert (
        categories["structural_inactive"][
            "observed_training_pattern_count"
        ]
        == 0
    )
    unknown = categories["true_unknown"]
    assert unknown["path"] == "categorical.aged"
    assert unknown["value"] == "sometimes"
    assert unknown["masking_cohort_count"] == 75
    assert unknown["observed_training_pattern_count"] == 0
    assert unknown["support"] == "incompatible"
    assert unknown["prediction_status"] == "blocked"
    assert unknown["applied_policy"] == "reject"
    assert unknown["coerced_to_missing"] is False
    assert report["decision"]["production_promotion"] == "not_promoted"


def test_package_capability_is_explicit_and_cannot_promote_sparse_patterns() -> None:
    capability = _capability()
    package_policy = PipelineMissingPolicySpec(
        imputation_values={"process.x": 1.5},
        digest="sha256:" + "b" * 64,
        operation_capability=capability,
    )
    assert resolve_missingness_operation_capability(None) is None
    assert resolve_missingness_operation_capability(package_policy) == capability

    supported = assess_input_missingness(
        _candidate(x=None),
        (_input("process.x"),),
        _training_stats(training_count=20),
        operation="snapshot",
        operation_capability=capability,
    )
    assert supported.missingness_support == "supported"
    require_operation_allowed(supported)

    sparse = assess_input_missingness(
        _candidate(x=None),
        (_input("process.x"),),
        _training_stats(training_count=1),
        operation="snapshot",
        operation_capability=capability,
    )
    assert sparse.missingness_support == "sparse"
    assert sparse.prediction_status == "blocked"
    with pytest.raises(ValueError, match="process.x"):
        require_operation_allowed(sparse)

    policy = pattern_support_policy_document()
    evidence = {
        "training_count": 2,
        "evaluation_count": 2,
        "metrics_by_target": {
            "y": {"prediction_failure_rate": 0.0}
        },
    }
    assert classify_missingness_pattern_support(
        evidence,
        support_policy=policy,
    ) == "supported"
    assert classify_missingness_pattern_support(
        {**evidence, "training_count": 1},
        support_policy=policy,
    ) == "sparse"
    assert classify_missingness_pattern_support(
        None,
        support_policy=policy,
    ) == "unseen"


def test_true_unknown_category_is_incompatible_not_ordinary_missing() -> None:
    evidence = assess_input_missingness(
        _candidate(x=1.0, route="sometimes"),
        (
            _input("process.x"),
            _input(
                "categorical.route",
                kind="categorical",
                choices=("no", "yes"),
            ),
        ),
        _training_stats(),
        operation="preview",
        operation_capability=_capability(),
    )

    assert evidence.fields[0].kind == "unknown_category"
    assert evidence.fields[0].applied_policy == "reject"
    assert evidence.missingness_support == "incompatible"
    assert evidence.input_completeness == "blocked"
