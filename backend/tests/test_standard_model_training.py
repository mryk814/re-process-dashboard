from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest
from pydantic import ValidationError

from material_workbench.modeling.model_lifecycle import canonical_training_dataset
from material_workbench.modeling.model_lifecycle import ACTIVE_PACKAGES_PATH
from material_workbench.modeling.training.feature_dataset import (
    compile_target_training_set,
)
from material_workbench.modeling.training.recipe import estimator_recipe
from material_workbench.modeling.training.recipe import validate_recipe_capability
from material_workbench.task_modules import registered_task_modules
from material_workbench.task_modules import resolve_task_source, task_module
from material_workbench.tasks.task_registry import load_task_contracts


ROOT = Path(__file__).resolve().parents[2]
OPERATIONS = ROOT / "backend" / "scripts" / "operations"
GENERATORS = ROOT / "backend" / "scripts" / "generators"
for script_root in (OPERATIONS, GENERATORS):
    if str(script_root) not in sys.path:
        sys.path.insert(0, str(script_root))

from model_workflow import build_package, estimator_inventory  # noqa: E402
from build_default_model_package import _fit_gp_hyperparameters  # noqa: E402


HOT_ROLLING_TASK = "hot-rolled-properties-v1"
SOURCE = ROOT / "data/source/material_workbench_tutorial_v2.xlsx"
HEAT_SOURCE = ROOT / "data/source/external/heat_treatment_tradeoff_samples.csv"


def test_stable_gp_training_is_deterministic_scaled_ard_multistart() -> None:
    rng = np.random.default_rng(7)
    x = rng.normal(size=(28, 5))
    y = 12.0 + 3.5 * x[:, 0] + 0.15 * rng.normal(size=len(x))

    first = _fit_gp_hyperparameters(
        x,
        y,
        train_noise=0.04,
        restarts=3,
        seed=11,
    )
    second = _fit_gp_hyperparameters(
        x,
        y,
        train_noise=0.04,
        restarts=3,
        seed=11,
    )

    np.testing.assert_allclose(first[0], second[0], rtol=0, atol=1e-10)
    assert first[1] == pytest.approx(second[1], abs=1e-10)
    assert first[2] == pytest.approx(second[2], abs=1e-10)
    assert first[0].shape == (x.shape[1],)
    assert np.all(first[0] > 0)
    assert first[0][0] < np.median(first[0][1:])
    diagnostics = first[3]
    assert diagnostics["kernel"] == "ARD-RBF"
    assert diagnostics["restarts"] == 3
    assert diagnostics["input_standardization"] == (
        "per_feature_training_mean_std"
    )
    assert diagnostics["output_standardization"]["scale"] > 0


def test_estimator_recipe_is_allow_listed_and_bounded() -> None:
    gp = estimator_recipe(
        "exact-gp-rbf.v1",
        {"restarts": 2, "seed": 7, "max_rows": 50},
    )
    assert gp.estimator_id == "exact-gp-rbf.v1"
    assert gp.restarts == 2

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        estimator_recipe("ridge.v1", {"import_path": "unsafe.module"})
    with pytest.raises(ValidationError, match="less than or equal to 2000"):
        estimator_recipe("exact-gp-rbf.v1", {"max_rows": 50_000})
    with pytest.raises(ValidationError):
        estimator_recipe("unknown.v1")


def test_hot_rolling_compiles_training_context_not_plain_parent() -> None:
    module = task_module(HOT_ROLLING_TASK)
    data = module.data_loader(SOURCE)
    canonical = canonical_training_dataset(
        HOT_ROLLING_TASK,
        data,
        load_task_contracts()[HOT_ROLLING_TASK],
    )
    training = compile_target_training_set(canonical, target="TS", unit="MPa")

    assert len(canonical["rows"]) == 8
    assert len(training.replicate_contexts) == 7
    assert len(set(training.validation_groups)) == 6
    assert len(set(row["parent_key"] for row in canonical["rows"])) == 6
    assert sorted(training.repeat_counts) == [1, 1, 1, 1, 1, 1, 2]
    assert sorted(len(items) for items in training.observation_ids) == [1, 1, 1, 1, 1, 1, 2]
    assert {
        observation_id
        for items in training.observation_ids
        for observation_id in items
    } == {row["observation_id"] for row in canonical["rows"]}


def test_model_workflow_builds_hot_rolling_gp_without_a_new_task_builder(
    tmp_path: Path,
) -> None:
    package = tmp_path / "hot-rolling-gp"
    dataset = tmp_path / "hot-rolling-feature-dataset.json"
    active_before = ACTIVE_PACKAGES_PATH.read_bytes()
    result = build_package(
        HOT_ROLLING_TASK,
        SOURCE,
        package,
        dataset,
        package_id="hot-rolling-configured-gp-test",
        package_version="1.0.0",
        replace=False,
        estimator="exact-gp-rbf.v1",
        estimator_options={"restarts": 1, "seed": 11},
    )

    manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
    recipe = json.loads(
        (package / "reference/training-recipe.json").read_text(encoding="utf-8")
    )
    assert result["package"]["task_id"] == HOT_ROLLING_TASK
    assert result["dataset"]["rows"] == 8
    assert manifest["predictors"][0]["runtime_type"] == "builtin.exact_gp.v1"
    assert manifest["predictors"][0]["feature_names"] == [
        item["name"]
        for item in json.loads(dataset.read_text(encoding="utf-8"))[
            "feature_pipeline"
        ]["features"]
    ]
    assert recipe["estimator"] == {
        "estimator_id": "exact-gp-rbf.v1",
        "restarts": 1,
        "seed": 11,
        "max_rows": 500,
    }
    assert result["package"]["quality_report"]["targets"][0][
        "parent_conditions"
    ] == 6
    assert manifest["predictors"][0]["config"]["validation_method"] == (
        "leave-one-validation-group-out"
    )
    assert ACTIVE_PACKAGES_PATH.read_bytes() == active_before


def test_model_workflow_builds_tabular_ridge_through_the_same_assembler(
    tmp_path: Path,
) -> None:
    package = tmp_path / "heat-treatment-ridge"
    result = build_package(
        "heat-treatment-tradeoff-v1",
        HEAT_SOURCE,
        package,
        tmp_path / "heat-treatment-feature-dataset.json",
        package_id="heat-treatment-configured-ridge-test",
        package_version="1.0.0",
        replace=False,
        estimator="ridge.v1",
    )
    manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))

    assert result["package"]["task_id"] == "heat-treatment-tradeoff-v1"
    assert {
        predictor["runtime_type"]
        for predictor in manifest["predictors"]
    } == {"builtin.linear.v1"}
    assert {
        predictor["config"]["training_method"]
        for predictor in manifest["predictors"]
    } == {"ridge.v1"}


def test_omitting_estimator_keeps_the_specialized_builder_path(
    tmp_path: Path,
) -> None:
    package = tmp_path / "heat-treatment-specialized"
    result = build_package(
        "heat-treatment-tradeoff-v1",
        HEAT_SOURCE,
        package,
        tmp_path / "heat-treatment-specialized-dataset.json",
        package_id="heat-treatment-specialized-test",
        package_version="1.0.0",
        replace=False,
    )
    manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))

    assert result["package"]["task_id"] == "heat-treatment-tradeoff-v1"
    assert {
        predictor["runtime_type"]
        for predictor in manifest["predictors"]
    } == {"builtin.linear.v1"}
    assert not (package / "reference/training-recipe.json").exists()


def test_exact_gp_row_limit_fails_without_a_partial_package(
    tmp_path: Path,
) -> None:
    package = tmp_path / "too-large-gp"
    with pytest.raises(ValueError, match="recipe max_rows is 3"):
        build_package(
            HOT_ROLLING_TASK,
            SOURCE,
            package,
            tmp_path / "feature-dataset.json",
            package_id="too-large-gp",
            package_version="1.0.0",
            replace=False,
            estimator="exact-gp-rbf.v1",
            estimator_options={"max_rows": 3},
        )
    assert not package.exists()


def test_task_rejects_an_estimator_that_cannot_meet_runtime_capability(
    tmp_path: Path,
) -> None:
    dataset = tmp_path / "feature-dataset.json"
    with pytest.raises(ValueError, match="does not support standard estimator ridge.v1"):
        build_package(
            HOT_ROLLING_TASK,
            SOURCE,
            tmp_path / "invalid-package",
            dataset,
            package_id="invalid-ridge",
            package_version="1.0.0",
            replace=False,
            estimator="ridge.v1",
        )
    assert not dataset.exists()


def test_estimator_inventory_exposes_only_compatible_task_choices() -> None:
    assert estimator_inventory(HOT_ROLLING_TASK)["tasks"] == {
        HOT_ROLLING_TASK: ["exact-gp-rbf.v1"]
    }


def test_capability_validation_rejects_unavailable_standard_outputs() -> None:
    capability = load_task_contracts()[HOT_ROLLING_TASK].runtime_capability
    incompatible_target = capability.targets[0].model_copy(
        update={"samples": True}
    )
    incompatible = capability.model_copy(
        update={"targets": (incompatible_target,)}
    )
    with pytest.raises(ValueError, match="exact GP exposes no samples"):
        validate_recipe_capability(
            estimator_recipe("exact-gp-rbf.v1"),
            incompatible,
        )


@pytest.mark.parametrize(
    "task_id",
    [
        task_id
        for task_id, module in registered_task_modules().items()
        if module.standard_model_authoring is not None
    ],
)
def test_every_standard_estimator_task_compiles_all_targets(task_id: str) -> None:
    module = task_module(task_id)
    source = resolve_task_source(task_id)
    data = module.data_loader(source)
    contract = load_task_contracts()[task_id]
    canonical = canonical_training_dataset(task_id, data, contract)
    positive_targets = module.standard_model_authoring.positive_targets  # type: ignore[union-attr]

    for output in contract.task_definition.outputs:
        training = compile_target_training_set(
            canonical,
            target=output.key,
            unit=output.unit,
            target_kind=(
                "continuous_positive"
                if output.key in positive_targets
                else "continuous"
            ),
        )
        assert len(training.y) >= 3
        assert len(training.validation_groups) == len(training.y)


def test_annealed_standard_gp_preserves_positive_lambda_target(
    tmp_path: Path,
) -> None:
    package = tmp_path / "annealed-gp"
    build_package(
        "annealed-properties-v1",
        resolve_task_source("annealed-properties-v1"),
        package,
        tmp_path / "annealed-feature-dataset.json",
        package_id="annealed-configured-gp-test",
        package_version="1.0.0",
        replace=False,
        estimator="exact-gp-rbf.v1",
        estimator_options={"restarts": 1, "seed": 11},
    )
    manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
    target_kinds = {
        predictor["target"]: predictor["target_kind"]
        for predictor in manifest["predictors"]
    }

    assert target_kinds["lambda"] == "continuous_positive"
    assert {
        target_kind
        for target, target_kind in target_kinds.items()
        if target != "lambda"
    } == {"continuous"}
