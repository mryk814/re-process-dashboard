from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from decision_workbench.contracts.candidate_project_contracts import CandidateInput
from decision_workbench.modeling.model_lifecycle import dataset_profile_digest
from decision_workbench.modeling.tabular.data import load_tabular_data
from decision_workbench.modeling.tabular.features import (
    build_tabular_features,
    build_tabular_features_from_observation,
    build_tabular_training_features_from_observation,
    candidate_from_observation,
    feature_definitions,
)
from decision_workbench.modeling.tabular.profile import TabularDatasetProfile
from decision_workbench.modeling.tabular.runtime import TabularRegressionRuntime
from decision_workbench.modeling.tabular_model_builder import (
    _fit_imputation_values,
    _grouped_oof,
)
from decision_workbench.modeling.training.feature_dataset import (
    compile_target_training_set,
    prepared_feature_matrix,
)


def _profile(**input_overrides: object) -> TabularDatasetProfile:
    input_payload = {
        "path": "process.x",
        "column": "x",
        "kind": "number",
        "transform": "linear",
        **input_overrides,
    }
    return TabularDatasetProfile.model_validate({
        "schema_version": "tabular-dataset-profile/v1",
        "profile_id": "missing-policy-fixture",
        "name": "missing policy fixture",
        "task_id": "fixture-task",
        "package_id": "fixture-package",
        "inputs": [input_payload],
        "outputs": [{"key": "y", "column": "y", "unit": "1"}],
    })


def _source(tmp_path: Path, rows: list[str]) -> Path:
    path = tmp_path / "fixture.csv"
    path.write_text("x,y\n" + "\n".join(rows) + "\n", encoding="utf-8")
    return path


def test_omitted_policy_preserves_fail_closed_profile_and_digest(tmp_path: Path) -> None:
    profile = _profile()
    data = load_tabular_data(_source(tmp_path, [",1", "2,2"]), profile)

    assert profile.inputs[0].numeric_missing.strategy == "reject"
    assert data.observations[0]["eligible"] is False
    assert "有限数" in data.observations[0]["eligibility_reasons"][0]

    payload = profile.model_dump(mode="json")
    for key in ("numeric_missing", "categorical_missing", "unknown_category"):
        payload["inputs"][0].pop(key)
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert dataset_profile_digest(profile) == dataset_profile_digest(path)


def test_training_median_keeps_raw_missing_and_declares_indicator(tmp_path: Path) -> None:
    profile = _profile(numeric_missing={
        "strategy": "training_median_with_indicator",
    })
    data = load_tabular_data(_source(tmp_path, ["1,1", ",2", "9,3"]), profile)

    missing = data.observations[1]
    provenance = missing["run_context"]["curation"]["predictor_policies"]["x"]
    assert missing["eligible"] is True
    assert "x" not in missing["features"]
    assert provenance == {
        "raw": "",
        "normalized": None,
        "source_state": "missing",
        "policy": "training_median_with_indicator",
    }
    assert data.feature_imputation_values == {"process.x": 5.0}
    assert [item.name for item in feature_definitions(profile)] == [
        "process.x",
        "process.x__missing",
    ]
    bundle = build_tabular_features_from_observation(
        missing,
        data.feature_imputation_values,
        profile,
    )
    assert bundle.as_dict() == {
        "process.x": 5.0,
        "process.x__missing": 1.0,
    }
    assert data.detected_quality[0]["issue_type"] == "predictor_missing"


def test_training_representation_preserves_nan_without_breaking_feature_bundle(
    tmp_path: Path,
) -> None:
    profile = _profile(numeric_missing={
        "strategy": "training_median_with_indicator",
    })
    data = load_tabular_data(_source(tmp_path, ["1,1", ",2", "9,3"]), profile)

    bundle, training_values = build_tabular_training_features_from_observation(
        data.observations[1],
        data.feature_imputation_values,
        profile,
    )

    assert bundle.as_dict()["process.x"] == 5.0
    assert bundle.as_dict()["process.x__missing"] == 1.0
    assert np.isnan(training_values["process.x"])
    assert training_values["process.x__missing"] == 1.0


def test_fold_imputation_is_fitted_without_held_out_values(tmp_path: Path) -> None:
    profile = _profile(numeric_missing={
        "strategy": "training_median_with_indicator",
    })
    data = load_tabular_data(
        _source(tmp_path, ["1,1", ",2", "100,3", "200,4"]),
        profile,
    )
    rows = data.observations
    x = np.zeros((4, 2), dtype=float)
    seen: list[float] = []

    def fold_matrix(
        train: np.ndarray,
        evaluate: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray]:
        training_rows = [
            row for row, selected in zip(rows, train, strict=True) if selected
        ]
        fitted = _fit_imputation_values(training_rows, profile)
        seen.append(fitted["process.x"])
        return x[train], x[evaluate]

    _grouped_oof(
        x,
        np.asarray([1.0, 2.0, 3.0, 4.0]),
        ["a", "b", "c", "d"],
        folds=2,
        fold_matrix=fold_matrix,
    )

    assert sorted(seen) == [50.5, 200.0]
    assert 100.0 not in seen  # the full-cohort median is never reused in evaluation folds


def test_standard_training_uses_fold_median_but_final_package_value_is_fixed() -> None:
    canonical = {
        "feature_pipeline": {
            "features": [
                {"name": "process.x"},
                {"name": "process.x__missing"},
            ],
            "missing_policy": {
                "imputation_values": {"process.x": 50.0},
            },
        },
        "rows": [
            {
                "observation_id": f"row-{index}",
                "parent_key": f"group-{index}",
                "features": {
                    "process.x": value,
                    "process.x__missing": float(np.isnan(value)),
                },
                "outputs": {"y": float(index)},
            }
            for index, value in enumerate((1.0, np.nan, 100.0, 200.0), start=1)
        ],
    }
    data = compile_target_training_set(
        canonical,
        target="y",
        unit="1",
        folds=2,
    )
    train = np.asarray([False, False, True, True])
    evaluate = ~train

    fold_values = prepared_feature_matrix(
        data,
        fit_rows=train,
        transform_rows=evaluate,
    )
    final_values = prepared_feature_matrix(data)

    assert fold_values[:, 0].tolist() == [1.0, 150.0]
    assert final_values[:, 0].tolist() == [1.0, 50.0, 100.0, 200.0]


def test_batch_support_keeps_candidate_missing_evidence() -> None:
    profile = _profile(numeric_missing={
        "strategy": "training_median_with_indicator",
    })
    missing = CandidateInput.model_validate({
        "name": "missing",
        "inputs": {
            "composition": {},
            "process": {},
            "categorical": {},
        },
    })
    observed = CandidateInput.model_validate({
        "name": "observed zero",
        "inputs": {
            "composition": {},
            "process": {"x": 0.0},
            "categorical": {},
        },
    })
    feature_rows = [
        build_tabular_features(candidate, profile, {"process.x": 5.0})
        for candidate in (missing, observed)
    ]
    runtime = object.__new__(TabularRegressionRuntime)
    runtime.profile = profile
    runtime.support_references = {
        "y": {
            "mean": np.zeros(2),
            "scale": np.ones(2),
            "vectors": np.asarray([[0.0, 0.0], [1.0, 1.0]]),
            "loo_nearest": np.asarray([1.0, 1.0]),
            "supported_threshold": 1.0,
            "caution_threshold": 2.0,
            "rows": [],
        },
    }

    support = runtime._support_batch(feature_rows, [missing, observed], "y")

    assert support[0].components["imputed_inputs"] == 1.0
    assert "実測一致とは扱いません" in support[0].message
    assert support[1].components["imputed_inputs"] == 0.0
    assert "実測一致とは扱いません" not in support[1].message


def test_categorical_mapping_requires_explicit_choice_and_never_uses_zero_vector(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="explicit choice"):
        _profile(
            path="categorical.x",
            kind="categorical",
            transform="quadratic",
            choices=["known", "other"],
            unknown_category={
                "strategy": "map_to_other_category",
                "other_choice": "undeclared",
            },
        )

    profile = _profile(
        path="categorical.x",
        kind="categorical",
        transform="quadratic",
        choices=["known", "other"],
        unknown_category={
            "strategy": "map_to_other_category",
            "other_choice": "other",
        },
        categorical_missing={
            "strategy": "map_to_missing_category",
        },
    )
    data = load_tabular_data(
        _source(tmp_path, ["rare,1", ",2", "known,3"]),
        profile,
    )

    assert data.observations[0]["categorical"]["x"] == "other"
    assert data.observations[1]["categorical"]["x"] == "__missing__"
    assert data.observations[2]["categorical"]["x"] == "known"
    assert data.observations[1]["run_context"]["curation"][
        "predictor_policies"
    ]["x"]["normalized"] == "__missing__"
    unknown_bundle = build_tabular_features_from_observation(
        data.observations[0],
        {},
        profile,
    )
    assert unknown_bundle.as_dict()["categorical.x__other"] == 1.0
    assert sum(unknown_bundle.values) == 1.0
    missing_bundle = build_tabular_features_from_observation(
        data.observations[1],
        {},
        profile,
    )
    assert missing_bundle.as_dict()["categorical.x____missing__"] == 1.0
    assert sum(missing_bundle.values) == 1.0
    with pytest.raises(ValueError, match="reserved missing category"):
        build_tabular_features(
            CandidateInput.model_validate({
                "name": "forged missing sentinel",
                "inputs": {
                    "composition": {},
                    "process": {},
                    "categorical": {"x": "__missing__"},
                },
            }),
            profile,
            {},
        )


def test_raw_unknown_maps_to_reserved_missing_category(tmp_path: Path) -> None:
    profile = _profile(
        path="categorical.x",
        kind="categorical",
        transform="quadratic",
        choices=["known"],
        unknown_category={"strategy": "map_to_missing_category"},
    )
    data = load_tabular_data(
        _source(tmp_path, ["rare-source-level,1", "known,2"]),
        profile,
    )
    row = data.observations[0]

    bundle = build_tabular_features_from_observation(row, {}, profile)
    candidate = candidate_from_observation(row, profile)

    assert row["categorical"]["x"] == "__missing__"
    assert row["run_context"]["curation"]["predictor_policies"]["x"] == {
        "raw": "rare-source-level",
        "normalized": "__missing__",
        "source_state": "unknown_category",
        "policy": "map_to_missing_category",
    }
    assert bundle.as_dict() == {
        "categorical.x__known": 0.0,
        "categorical.x____missing__": 1.0,
    }
    assert "x" not in candidate.inputs.categorical
    with pytest.raises(ValueError, match="reserved missing category"):
        _profile(
            path="categorical.x",
            kind="categorical",
            transform="quadratic",
            choices=["known"],
            unknown_category={
                "strategy": "map_to_missing_category",
                "other_choice": "known",
            },
        )
