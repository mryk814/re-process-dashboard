from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest
from decision_workbench.application.personal_task_packages import (
    build_standard_package,
)
from decision_workbench.contracts.candidate_project_contracts import Candidate
from decision_workbench.contracts.feature_recipe_contracts import (
    FeatureRecipe,
)
from decision_workbench.modeling.packages.contracts import (
    FeaturePipelineDocument,
    ModelPackageManifest,
)
from decision_workbench.modeling.packages.verification import (
    _validate_feature_pipeline,
)
from decision_workbench.modeling.tabular.runtime import TabularRegressionRuntime
from decision_workbench.modeling.training.feature_dataset import (
    compile_target_training_set,
    prepared_feature_matrix,
)
from decision_workbench.modeling.training.feature_recipe import (
    fit_feature_recipe,
    inspect_feature_recipe,
    recipe_digest,
    save_feature_recipe_artifacts,
    transform_feature_recipe,
    validate_recipe_canonical_inputs,
)
from decision_workbench.task_composition.catalog import (
    resolve_task_source,
    task_module,
)
from decision_workbench.tasks.task_registry import load_task_contracts
from pydantic import ValidationError


def _recipe() -> FeatureRecipe:
    return FeatureRecipe.model_validate(
        {
            "schema_version": "feature-recipe/v1",
            "id": "tabular-safe-transform",
            "version": "1.0.0",
            "canonical_input_paths": [
                "process.x",
                "process.z",
                "process.angle",
                "categorical.route",
            ],
            "operations": [
                {
                    "id": "x-missing",
                    "kind": "missing_indicator",
                    "input": "process.x",
                    "output": "x_missing",
                },
                {
                    "id": "x-impute",
                    "kind": "impute",
                    "input": "process.x",
                    "output": "x_imputed",
                    "strategy": "median",
                },
                {
                    "id": "x-scale",
                    "kind": "standardize",
                    "input": "x_imputed",
                    "output": "x_scaled",
                },
                {
                    "id": "z-robust",
                    "kind": "robust_scale",
                    "input": "process.z",
                    "output": "z_robust",
                },
                {
                    "id": "z-log",
                    "kind": "log1p",
                    "input": "process.z",
                    "output": "z_log",
                },
                {
                    "id": "z-polynomial",
                    "kind": "polynomial_degree_2",
                    "input": "process.z",
                    "linear_output": "z",
                    "square_output": "z_square",
                },
                {
                    "id": "route",
                    "kind": "one_hot",
                    "input": "categorical.route",
                    "choices": ["A", "other"],
                    "outputs": ["route_A", "route_other"],
                    "unknown_policy": "map_to_other",
                    "other_choice": "other",
                },
                {
                    "id": "x-z",
                    "kind": "pairwise_interaction",
                    "inputs": ["x_imputed", "process.z"],
                    "output": "x_z",
                },
                {
                    "id": "angle",
                    "kind": "cyclic",
                    "input": "process.angle",
                    "period": 24,
                    "sin_output": "angle_sin",
                    "cos_output": "angle_cos",
                },
            ],
            "features": [
                {
                    "name": name,
                    "unit": "1",
                    "meaning": name,
                    "group": "process",
                }
                for name in (
                    "x_missing",
                    "x_scaled",
                    "z_robust",
                    "z_log",
                    "z",
                    "z_square",
                    "route_A",
                    "route_other",
                    "x_z",
                    "angle_sin",
                    "angle_cos",
                )
            ],
        }
    )


def _rows() -> list[dict[str, object]]:
    return [
        {
            "process.x": 1.0,
            "process.z": 1.0,
            "process.angle": 0.0,
            "categorical.route": "A",
        },
        {
            "process.x": None,
            "process.z": 2.0,
            "process.angle": 6.0,
            "categorical.route": "unknown",
        },
        {
            "process.x": 5.0,
            "process.z": 5.0,
            "process.angle": 12.0,
            "categorical.route": "A",
        },
    ]


def test_allow_list_runner_fits_and_traces_p0_operations(tmp_path: Path) -> None:
    recipe = _recipe()
    state = fit_feature_recipe(recipe, _rows())
    values = transform_feature_recipe(recipe, state, _rows())

    assert values.shape == (3, 11)
    assert np.isfinite(values).all()
    assert values[1, 0] == 1
    assert values[1, 7] == 1
    assert values[1, 9] == pytest.approx(1)
    assert state.recipe_digest == recipe_digest(recipe)

    recipe_path = tmp_path / "recipe.json"
    state_path = tmp_path / "state.json"
    save_feature_recipe_artifacts(recipe, state, recipe_path, state_path)
    assert json.loads(recipe_path.read_text(encoding="utf-8"))["schema_version"] == (
        "feature-recipe/v1"
    )
    trace = inspect_feature_recipe(recipe, state, _rows()[1])
    assert [item["kind"] for item in trace["steps"]] == [
        item.kind for item in recipe.operations
    ]
    assert tuple(trace["features"]) == tuple(item.name for item in recipe.features)


def test_fit_state_is_derived_from_training_fold_only() -> None:
    recipe = FeatureRecipe.model_validate(
        {
            "id": "fold-local",
            "version": "1",
            "canonical_input_paths": ["process.x"],
            "operations": [
                {
                    "id": "scale",
                    "kind": "standardize",
                    "input": "process.x",
                    "output": "x",
                }
            ],
            "features": [
                {"name": "x", "unit": "1", "meaning": "scaled x", "group": "process"}
            ],
        }
    )
    canonical = {
        "feature_pipeline": {"features": [{"name": "x"}]},
        "rows": [
            {
                "observation_id": f"o{index}",
                "parent_key": f"g{index}",
                "canonical_inputs": {"process.x": value},
                "features": {"x": value},
                "outputs": {"y": float(index)},
            }
            for index, value in enumerate((0.0, 2.0, 100.0))
        ],
    }
    data = compile_target_training_set(
        canonical,
        target="y",
        unit="1",
        folds=3,
        feature_recipe=recipe,
        feature_recipe_state=fit_feature_recipe(
            recipe,
            [row["canonical_inputs"] for row in canonical["rows"]],
        ),
    )
    held_out = int(np.flatnonzero(data.fold_ids == 0)[0])
    training = data.fold_ids != 0
    evaluation = data.fold_ids == 0
    matrix = prepared_feature_matrix(
        data,
        fit_rows=training,
        transform_rows=evaluation,
    )
    training_values = np.asarray(
        [
            data.recipe_rows[index]["process.x"]
            for index in np.flatnonzero(training)
        ],
        dtype=float,
    )
    expected = (
        float(data.recipe_rows[held_out]["process.x"]) - training_values.mean()
    ) / training_values.std()
    assert matrix[0, 0] == pytest.approx(expected)


def test_passthrough_and_constant_imputation_are_data_only() -> None:
    recipe = FeatureRecipe.model_validate(
        {
            "id": "fixed-operations",
            "version": "1",
            "canonical_input_paths": ["process.x", "process.y"],
            "operations": [
                {
                    "id": "x",
                    "kind": "passthrough",
                    "input": "process.x",
                    "output": "x",
                },
                {
                    "id": "y",
                    "kind": "impute",
                    "input": "process.y",
                    "output": "y",
                    "strategy": "constant",
                    "value": -1,
                },
            ],
            "features": [
                {"name": "x", "unit": "1", "meaning": "x", "group": "process"},
                {"name": "y", "unit": "1", "meaning": "y", "group": "process"},
            ],
        }
    )
    rows = [{"process.x": 2.0, "process.y": None}]
    state = fit_feature_recipe(recipe, rows)
    assert transform_feature_recipe(recipe, state, rows).tolist() == [[2.0, -1.0]]


def test_package_verification_rejects_recipe_order_or_state_drift(
    tmp_path: Path,
) -> None:
    recipe = _recipe()
    state = fit_feature_recipe(recipe, _rows())
    recipe_path = tmp_path / "recipe.json"
    state_path = tmp_path / "state.json"
    save_feature_recipe_artifacts(recipe, state, recipe_path, state_path)
    pipeline_path = tmp_path / "pipeline.json"
    output_features = [item.name for item in recipe.features]
    pipeline_path.write_text(
        json.dumps(
            {
                "id": recipe.id,
                "version": recipe.version,
                "canonical_input_paths": list(recipe.canonical_input_paths),
                "features": [
                    item.model_dump(mode="json") for item in recipe.features
                ],
                "feature_recipe": {
                    "recipe": "recipe.json",
                    "recipe_digest": state.recipe_digest,
                    "state": "state.json",
                    "state_digest": state.state_digest,
                },
            }
        ),
        encoding="utf-8",
    )
    manifest = ModelPackageManifest.model_validate(
        {
            "schema_version": "model-package/v1",
            "package_id": "recipe-test",
            "package_version": "1",
            "task_id": "task",
            "input_schema_version": "canonical-candidate/v1",
            "feature_pipeline": {
                "id": recipe.id,
                "version": recipe.version,
                "spec": "pipeline.json",
                "canonical_input_paths": list(recipe.canonical_input_paths),
                "output_features": output_features,
                "artifacts": ["recipe.json", "state.json"],
            },
            "predictors": [
                {
                    "id": "p",
                    "target": "y",
                    "unit": "1",
                    "target_kind": "continuous",
                    "runtime_type": "builtin.linear.v1",
                    "artifact": "predictor.npz",
                    "predictive_family": "normal",
                    "feature_names": output_features,
                }
            ],
            "provenance": {
                "training_data_id": "sha256:data",
                "feature_dataset_id": "sha256:features",
                "training_code_revision": "test",
            },
            "artifacts": [
                {"path": name, "sha256": "0" * 64, "bytes": 1}
                for name in (
                    "pipeline.json",
                    "recipe.json",
                    "state.json",
                    "predictor.npz",
                )
            ],
        }
    )
    artifacts = {
        "pipeline.json": pipeline_path,
        "recipe.json": recipe_path,
        "state.json": state_path,
    }
    _validate_feature_pipeline(manifest, artifacts)

    tampered = state.model_copy(
        update={"output_features": tuple(reversed(state.output_features))}
    )
    state_path.write_text(tampered.model_dump_json(), encoding="utf-8")
    with pytest.raises(ValueError, match="output order"):
        _validate_feature_pipeline(manifest, artifacts)


def test_unknown_operations_and_legacy_pipeline_shape_are_explicit() -> None:
    with pytest.raises(ValidationError):
        FeatureRecipe.model_validate(
            {
                "id": "unsafe",
                "version": "1",
                "canonical_input_paths": ["process.x"],
                "operations": [
                    {
                        "id": "callback",
                        "kind": "python_callback",
                        "input": "process.x",
                        "output": "x",
                    }
                ],
                "features": [
                    {"name": "x", "unit": "1", "meaning": "x", "group": "process"}
                ],
            }
        )

    legacy = FeaturePipelineDocument.model_validate(
        {
            "id": "legacy",
            "version": "1.0.0",
            "canonical_input_paths": ["process.x"],
            "features": [
                {"name": "x", "unit": "1", "meaning": "x", "group": "process"}
            ],
        }
    )
    assert legacy.feature_recipe is None


def test_recipe_rejects_undeclared_task_paths_and_duplicate_outputs() -> None:
    recipe = _recipe().model_copy(
        update={
            "canonical_input_paths": (
                *_recipe().canonical_input_paths,
                "categorical.not_declared_by_task",
            )
        }
    )
    with pytest.raises(ValueError, match="outside the Task contract"):
        validate_recipe_canonical_inputs(
            recipe,
            ("process.x", "process.z", "process.angle", "categorical.route"),
        )

    for operation in (
        {
            "id": "duplicate-polynomial",
            "kind": "polynomial_degree_2",
            "input": "process.x",
            "linear_output": "same",
            "square_output": "same",
        },
        {
            "id": "duplicate-cyclic",
            "kind": "cyclic",
            "input": "process.x",
            "period": 24,
            "sin_output": "same",
            "cos_output": "same",
        },
    ):
        with pytest.raises(ValidationError, match="output names must be unique"):
            FeatureRecipe.model_validate(
                {
                    "id": "duplicate-output",
                    "version": "1",
                    "canonical_input_paths": ["process.x"],
                    "operations": [operation],
                    "features": [
                        {
                            "name": "same",
                            "unit": "1",
                            "meaning": "same",
                            "group": "process",
                        }
                    ],
                }
            )


def test_developer_trace_reports_missing_canonical_paths() -> None:
    recipe = _recipe()
    state = fit_feature_recipe(recipe, _rows())
    incomplete = dict(_rows()[0])
    incomplete.pop("process.angle")
    with pytest.raises(ValueError, match="missing paths: process.angle"):
        inspect_feature_recipe(recipe, state, incomplete)


def test_standard_tabular_package_uses_recipe_state_at_runtime(
    tmp_path: Path,
) -> None:
    task_id = "heat-treatment-tradeoff-v1"
    module = task_module(task_id)
    source = resolve_task_source(task_id)
    data = module.data_loader(source, None)
    authoring = module.standard_model_authoring
    assert authoring is not None
    recipe = FeatureRecipe.model_validate(
        {
            "id": "heat-treatment-feature-recipe",
            "version": "1.0.0",
            "canonical_input_paths": [
                "composition.carbon_pct",
                "process.tempering_temp_c",
            ],
            "operations": [
                {
                    "id": "carbon",
                    "kind": "standardize",
                    "input": "composition.carbon_pct",
                    "output": "carbon_scaled",
                },
                {
                    "id": "tempering",
                    "kind": "robust_scale",
                    "input": "process.tempering_temp_c",
                    "output": "tempering_scaled",
                },
                {
                    "id": "interaction",
                    "kind": "pairwise_interaction",
                    "inputs": ["carbon_scaled", "tempering_scaled"],
                    "output": "carbon_x_tempering",
                },
            ],
            "features": [
                {
                    "name": name,
                    "unit": "1",
                    "meaning": name,
                    "group": "process",
                }
                for name in (
                    "carbon_scaled",
                    "tempering_scaled",
                    "carbon_x_tempering",
                )
            ],
        }
    )
    destination = tmp_path / "package"
    recipe_path = tmp_path / "feature-recipe.json"
    recipe_path.write_text(recipe.model_dump_json(indent=2), encoding="utf-8")
    dataset_path = tmp_path / "feature-dataset.json"
    result = build_standard_package(
        task_id=task_id,
        source=source,
        output=destination,
        dataset_output=dataset_path,
        package_id="heat-treatment-feature-recipe-test",
        package_version="1",
        replace=False,
        estimator="ridge.v1",
        estimator_options={"folds": 3},
        feature_recipe_path=recipe_path,
    )
    assert result["dataset"]["feature_dataset_id"] == (
        json.loads((destination / "manifest.json").read_text(encoding="utf-8"))[
            "provenance"
        ]["feature_dataset_id"]
    )
    runtime = TabularRegressionRuntime(data, destination)
    canonical_candidate = load_task_contracts()[task_id].canonical_candidate
    now = datetime.now(UTC)
    candidate = Candidate(
        id="feature-recipe-runtime-smoke",
        project_id="feature-recipe-test",
        revision=1,
        created_at=now,
        updated_at=now,
        name="feature recipe runtime smoke",
        inputs={
            "composition": canonical_candidate.composition,
            "process": canonical_candidate.process,
            "categorical": canonical_candidate.categorical,
            "heat_pattern": canonical_candidate.heat_pattern,
        },
        provenance=canonical_candidate.provenance,
    )
    prediction = runtime.predict_core(candidate)
    assert set(prediction["predictions"]) == {"hardness_hv", "charpy_j"}
    pipeline = FeaturePipelineDocument.model_validate_json(
        (destination / "feature-pipeline/pipeline.json").read_text(
            encoding="utf-8"
        )
    )
    assert pipeline.feature_recipe is not None
