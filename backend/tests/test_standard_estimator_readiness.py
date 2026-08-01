import importlib.util
import json
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from decision_workbench.modeling.training.readiness import (
    EstimatorReadinessContext,
    resolve_estimator_readiness,
    standard_estimator_catalog,
)

ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = (
    ROOT
    / "backend"
    / "scripts"
    / "operations"
    / "standard_estimator_readiness.py"
)


def _context(
    estimator_id: str,
    target_kind: str = "continuous",
    **changes: object,
) -> EstimatorReadinessContext:
    values: dict[str, object] = {
        "estimator_id": estimator_id,
        "target_kind": target_kind,
        "row_count": 100,
        "independent_group_count": 20,
        "feature_count": 12,
        "target_contract": "ready",
        "validation_plan": "ready",
        "feature_recipe": "ready",
        "missing_policy": "ready",
    }
    values.update(changes)
    return EstimatorReadinessContext.model_validate(values)


def test_catalog_covers_production_target_kinds_and_safe_artifacts() -> None:
    catalog = standard_estimator_catalog()
    assert catalog.schema_version == "standard-estimator-readiness/v1"
    assert catalog.promotion_policy == "explicit_only"
    assert {
        kind
        for entry in catalog.entries
        for kind in entry.target_kinds
    } >= {"continuous", "continuous_positive", "binary", "count", "ordinal"}
    assert all(
        entry.artifact_format
        in {"bounded-npz", "lightgbm-native-text", "skops-allow-listed"}
        for entry in catalog.entries
    )
    assert all(entry.limits.max_rows and entry.limits.max_features for entry in catalog.entries)


def test_resolver_is_fail_closed_and_never_selects_or_builds() -> None:
    missing_dependency = resolve_estimator_readiness(
        _context("lightgbm-regression.v1"),
        available_dependencies=frozenset(),
    )
    assert missing_dependency.status == "unavailable_missing_dependency"
    assert missing_dependency.estimator_id == "lightgbm-regression.v1"
    assert missing_dependency.alternative_baseline_ids == ("ridge.v1",)
    assert missing_dependency.starts_build is False
    assert missing_dependency.promotes_package is False

    too_large = resolve_estimator_readiness(
        _context("exact-gp-rbf.v1", row_count=501),
        available_dependencies=frozenset(),
    )
    assert too_large.status == "out_of_scope"
    assert "501" in too_large.reasons[0]


def test_resolver_distinguishes_missing_contracts_and_external_builder() -> None:
    assert resolve_estimator_readiness(
        _context("ridge.v1", target_contract="missing"),
    ).status == "needs_target_contract"
    assert resolve_estimator_readiness(
        _context("ridge.v1", validation_plan="missing"),
    ).status == "needs_validation_plan"
    assert resolve_estimator_readiness(
        _context("ridge.v1", feature_recipe="missing"),
    ).status == "needs_feature_recipe"
    assert resolve_estimator_readiness(
        _context(
            "ridge.v1",
            has_missing_features=True,
            missing_policy="missing",
        ),
    ).status == "needs_feature_recipe"
    assert resolve_estimator_readiness(
        _context("numpyro-ordinal-external.v1", "ordinal"),
    ).status == "external_verified_package_only"


def test_nominal_multiclass_is_not_silently_reduced_to_binary() -> None:
    result = resolve_estimator_readiness(
        _context("logistic.v1", "nominal_multiclass"),
    )
    assert result.status == "out_of_scope"
    assert result.target_kind == "nominal_multiclass"
    assert "never reduced to binary" in result.reasons[0]


def test_current_and_pending_baseline_states_are_explicit() -> None:
    ridge = resolve_estimator_readiness(_context("ridge.v1"))
    logistic = resolve_estimator_readiness(
        _context("logistic.v1", "binary"),
        available_dependencies=frozenset({"skops"}),
    )
    poisson = resolve_estimator_readiness(
        _context("poisson.v1", "count"),
        available_dependencies=frozenset({"skops"}),
    )

    assert ridge.status == "ready"
    assert logistic.status == "out_of_scope"
    assert poisson.status == "out_of_scope"
    assert "builder is not shipped" in logistic.reasons[0]


def test_catalog_artifact_is_current() -> None:
    expected = standard_estimator_catalog().model_dump(mode="json")
    spec = importlib.util.spec_from_file_location(
        "standard_estimator_readiness_under_test",
        SCRIPT_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    assert json.loads(module.DEFAULT_OUTPUT.read_text(encoding="utf-8")) == expected


def test_developer_api_exposes_catalog_and_read_only_resolution(
    client: TestClient,
) -> None:
    catalog = client.get("/api/developer/estimator-readiness/catalog")
    assert catalog.status_code == 200
    assert catalog.json()["schema_version"] == "standard-estimator-readiness/v1"

    resolution = client.post(
        "/api/developer/estimator-readiness/resolve",
        json=_context("ridge.v1").model_dump(mode="json"),
    )
    assert resolution.status_code == 200
    assert resolution.json()["status"] == "ready"
    assert resolution.json()["starts_build"] is False
    assert resolution.json()["promotes_package"] is False
