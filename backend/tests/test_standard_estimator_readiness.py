import importlib.util
import json
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from decision_workbench.contracts.feature_recipe_contracts import FeatureRecipe
from decision_workbench.contracts.task_contracts import OutputDefinition
from decision_workbench.modeling.training.estimators import estimator_implementation
from decision_workbench.modeling.training.recipe import (
    ESTIMATOR_IDS,
    estimator_recipe,
)
from decision_workbench.modeling.training.readiness import (
    EstimatorReadinessContext,
    resolve_estimator_contract_readiness,
    resolve_estimator_readiness,
    standard_estimator_catalog,
)
from decision_workbench.modeling.training.validation_plan import ValidationPlan

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
        "validation_strategy": (
            "stratified_grouped_kfold"
            if target_kind == "binary"
            else "grouped_kfold"
        ),
        "feature_recipe": "ready",
        "missing_policy": "ready",
        "observed_target_min": 0 if target_kind == "count" else None,
        "observed_targets_are_integers": True if target_kind == "count" else None,
    }
    values.update(changes)
    if values["validation_plan"] != "ready" and "validation_strategy" not in changes:
        values["validation_strategy"] = None
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
        if entry.artifact_status == "ready"
    )
    assert all(entry.limits.max_rows and entry.limits.max_features for entry in catalog.entries)


def test_shipped_catalog_entries_are_derived_from_the_recipe_and_trainer_registries() -> None:
    shipped = [
        entry
        for entry in standard_estimator_catalog().entries
        if entry.builder_status == "standard_builder"
    ]
    assert {entry.estimator_id for entry in shipped} <= set(ESTIMATOR_IDS)
    for entry in shipped:
        implementation = estimator_implementation(entry.estimator_id)
        expected_parameters = estimator_recipe(entry.estimator_id).model_dump(
            mode="json",
            exclude={
                "estimator_id",
                "validation_plan",
                "validation_plans_by_target",
            },
            exclude_none=True,
        )
        assert entry.runtime_type == implementation.runtime_type
        assert entry.artifact_format == implementation.artifact_format
        assert entry.model_dump(mode="json")["fixed_parameters"] == expected_parameters


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

    no_usable_baseline = resolve_estimator_readiness(
        _context(
            "exact-gp-rbf.v1",
            row_count=2,
            independent_group_count=2,
        ),
        available_dependencies=frozenset(),
    )
    assert no_usable_baseline.alternative_baseline_ids == ()


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


def test_binary_estimator_rejects_non_stratified_validation_plan() -> None:
    result = resolve_estimator_readiness(
        _context(
            "lightgbm-binary.v1",
            "binary",
            validation_strategy="grouped_kfold",
        ),
        available_dependencies=frozenset({"lightgbm"}),
    )
    assert result.status == "needs_validation_plan"
    assert "grouped_kfold is not reviewed" in result.reasons[0]


def test_nominal_multiclass_is_not_silently_reduced_to_binary() -> None:
    result = resolve_estimator_readiness(
        _context("logistic.v1", "nominal_multiclass"),
    )
    assert result.status == "out_of_scope"
    assert result.target_kind == "nominal_multiclass"
    assert "never reduced to binary" in result.reasons[0]


def test_current_baseline_states_are_explicit() -> None:
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
    assert logistic.status == "ready"
    assert poisson.status == "ready"
    assert logistic.runtime_type == "sklearn.skops.v1"
    assert poisson.artifact_format == "skops-allow-listed"


def test_contract_resolver_uses_count_semantics_validation_and_feature_recipe() -> None:
    output = OutputDefinition(
        key="defects",
        label="欠陥数",
        unit="個",
        target_kind="count",
        count={"count_unit": "個"},
        goal_direction="at_most",
        plausibility_range={"min": 0, "max": 100},
        preferred_display_range={"min": 0, "max": 20},
    )
    validation = ValidationPlan(
        strategy="grouped_kfold",
        folds=3,
        group_key="parent_key",
    )
    feature_recipe = FeatureRecipe.model_validate(
        {
            "id": "readiness-test",
            "version": "1.0.0",
            "canonical_input_paths": ["process.x"],
            "operations": [
                {
                    "id": "x",
                    "kind": "passthrough",
                    "input": "process.x",
                    "output": "x",
                }
            ],
            "features": [
                {"name": "x", "unit": "1", "meaning": "x", "group": "process"}
            ],
        }
    )

    ready = resolve_estimator_contract_readiness(
        estimator_id="poisson.v1",
        output=output,
        validation_plan=validation,
        feature_recipe=feature_recipe,
        row_count=20,
        independent_group_count=10,
        observed_target_min=0,
        observed_targets_are_integers=True,
        available_dependencies=frozenset({"skops"}),
    )
    assert ready.status == "ready"

    exposed = resolve_estimator_contract_readiness(
        estimator_id="poisson.v1",
        output=OutputDefinition(
            key="defects",
            label="欠陥数",
            unit="個",
            target_kind="count",
            count={"count_unit": "個", "exposure_label": "面積"},
            goal_direction="at_most",
            plausibility_range={"min": 0, "max": 100},
            preferred_display_range={"min": 0, "max": 20},
        ),
        validation_plan=validation,
        feature_recipe=feature_recipe,
        row_count=20,
        independent_group_count=10,
        observed_target_min=0,
        observed_targets_are_integers=True,
        available_dependencies=frozenset({"skops"}),
    )
    assert exposed.status == "out_of_scope"
    assert "exposure" in exposed.reasons[0]


def test_contract_resolver_bounds_legacy_canonical_ridge_and_lightgbm_paths() -> None:
    output = OutputDefinition(
        key="strength",
        label="強度",
        unit="MPa",
        goal_direction="at_least",
        plausibility_range={"min": 0, "max": 2_000},
        preferred_display_range={"min": 200, "max": 1_000},
    )
    validation = ValidationPlan(
        strategy="grouped_kfold",
        folds=5,
        group_key="parent_key",
    )
    ridge = resolve_estimator_contract_readiness(
        estimator_id="ridge.v1",
        output=output,
        validation_plan=validation,
        feature_recipe=None,
        canonical_feature_count=12,
        row_count=100,
        independent_group_count=20,
    )
    assert ridge.status == "ready"

    lightgbm = resolve_estimator_contract_readiness(
        estimator_id="lightgbm-regression.v1",
        output=output,
        validation_plan=validation,
        feature_recipe=None,
        canonical_feature_count=2_049,
        row_count=100,
        independent_group_count=20,
        available_dependencies=frozenset({"lightgbm"}),
    )
    assert lightgbm.status == "out_of_scope"
    assert "feature count 2049 exceeds 2048" in lightgbm.reasons


def test_bayesian_additive_capacity_dimensions_fail_before_build() -> None:
    output = OutputDefinition(
        key="strength",
        label="強度",
        unit="MPa",
        goal_direction="at_least",
        plausibility_range={"min": 0, "max": 2_000},
        preferred_display_range={"min": 200, "max": 1_000},
    )
    validation = ValidationPlan(
        strategy="grouped_kfold",
        folds=5,
        group_key="parent_key",
    )
    resolution = resolve_estimator_contract_readiness(
        estimator_id="bayesian-additive-spline.v1",
        output=output,
        validation_plan=validation,
        feature_recipe=None,
        canonical_feature_count=48,
        smooth_term_count=48,
        total_basis_columns=289,
        maximum_categorical_levels=49,
        row_count=100,
        independent_group_count=20,
    )

    assert resolution.status == "out_of_scope"
    assert "basis columns 289 exceed 288" in resolution.reasons
    assert "categorical levels 49 exceed 48" in resolution.reasons


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
