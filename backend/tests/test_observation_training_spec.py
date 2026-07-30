"""Observation-family Taskの学習面が宣言済み契約から導出できることを固定する。

特徴量の並びやtarget→familyを二重に書かないための導出であり、導出結果が
現行Model Packageの特徴量パイプラインと一致することがこのテストの本題である。
一致が崩れると保存済みPackageの検証が通らなくなる。
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from material_workbench.data.observation_profile import load_observation_profile
from material_workbench.modeling import observation_regression
from material_workbench.modeling.model_packages import ModelPackageLoader
from material_workbench.modeling.observation_training_spec import (
    ObservationSpecError,
    observation_training_spec,
)
from material_workbench.task_composition.builtin_tasks import observation_declaration
from material_workbench.tasks.task_registry import load_task_contracts


ROOT = Path(__file__).resolve().parents[2]
TASK_ID = "welding-stage-c-properties-v1"
PACKAGE = ROOT / "models" / "packages" / "welding-stage-c-ridge-v1"

# 現行Packageが固定している並び。導出がこれと一致しなくなったら気付けるようにする。
GOLDEN_COMPOSITION = tuple(
    f"composition.{name}"
    for name in (
        "C", "Si", "Mn", "P", "S", "Ni", "Cr", "Mo",
        "Cu", "Ti", "B", "Nb", "V", "Al", "N", "O",
    )
)
GOLDEN_TENSILE = (
    *GOLDEN_COMPOSITION,
    "process.heat_input_kj_per_mm",
    "process.preheat_temp_c",
)
GOLDEN_CHARPY = (*GOLDEN_TENSILE, "process.test_temperature_c")
GOLDEN_CORROSION = (
    *GOLDEN_COMPOSITION,
    "categorical.test_solution::3.5%NaCl",
    "categorical.test_solution::5%H2SO4",
)
GOLDEN_PIPELINE = (
    *GOLDEN_CHARPY,
    "categorical.test_solution::3.5%NaCl",
    "categorical.test_solution::5%H2SO4",
)


def _spec():
    return observation_regression.resolve_spec(observation_declaration(TASK_ID))


def test_derived_feature_order_matches_the_stored_model_package() -> None:
    spec = _spec()
    manifest = ModelPackageLoader().load(PACKAGE).manifest

    assert spec.pipeline_features == GOLDEN_PIPELINE
    assert manifest.feature_pipeline.output_features == spec.pipeline_features
    predictors = {item.target: item for item in manifest.predictors}
    for target, expected in spec.target_features.items():
        assert predictors[target].feature_names == expected, target
        assert predictors[target].config["observation_family"] == spec.target_family[target]


def test_per_target_feature_sets_and_families_come_from_the_profile() -> None:
    spec = _spec()

    assert spec.target_features["TS"] == GOLDEN_TENSILE
    assert spec.target_features["CHARPY_ENERGY"] == GOLDEN_CHARPY
    assert spec.target_features["CORROSION_RATE"] == GOLDEN_CORROSION
    assert spec.target_family == {
        "TS": "tensile",
        "YS": "tensile",
        "EL": "tensile",
        "RA": "tensile",
        "CHARPY_ENERGY": "charpy",
        "BRITTLE_FRACTURE": "charpy",
        "CORROSION_RATE": "corrosion",
    }
    assert spec.categorical_choices == {
        "categorical.test_solution": ("3.5%NaCl", "5%H2SO4"),
    }


def test_feature_units_come_from_the_profile_canonical_units() -> None:
    spec = _spec()
    units = {item.name: item.unit for item in spec.feature_definitions}

    assert units["composition.C"] == "mass% deposited metal"
    assert units["process.heat_input_kj_per_mm"] == "kJ/mm"
    assert units["process.preheat_temp_c"] == "°C"
    assert units["categorical.test_solution::3.5%NaCl"] == "1"
    groups = {item.group for item in spec.feature_definitions}
    assert groups == {"composition", "process", "categorical"}


def test_spec_rejects_a_profile_that_disagrees_with_the_task_outputs() -> None:
    declaration = observation_declaration(TASK_ID)
    profile = load_observation_profile(declaration.profile_path)
    task = load_task_contracts()[TASK_ID].task_definition
    trimmed = profile.model_copy(update={"families": profile.families[:1]})

    with pytest.raises(ObservationSpecError, match="出力が一致しません"):
        observation_training_spec(declaration, trimmed, task)


def test_spec_requires_declared_output_bounds() -> None:
    declaration = observation_declaration(TASK_ID)
    profile = load_observation_profile(declaration.profile_path)
    task = load_task_contracts()[TASK_ID].task_definition
    without_ts = {
        key: value for key, value in declaration.output_bounds.items() if key != "TS"
    }

    with pytest.raises(ObservationSpecError, match="物理境界が宣言されていません"):
        observation_training_spec(
            declaration.__class__(
                **{**vars(declaration), "output_bounds": without_ts}
            ),
            profile,
            task,
        )


def test_observation_runtime_module_declares_no_task_id_or_profile_path() -> None:
    """Observation family runtimeが1 Task / 1 Profileへ固定されていないこと。"""

    source = inspect.getsource(observation_regression)

    assert "TASK_ID" not in source
    assert "PROFILE_PATH" not in source
    assert TASK_ID not in source
    assert "observation-profile-" not in source
