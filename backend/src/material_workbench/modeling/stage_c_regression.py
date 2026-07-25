"""Allow-listed Stage C runtime backed by observation-family training views."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from statistics import median
from typing import Any

import numpy as np

from material_workbench.contracts.feature_contracts import FeatureBundle, FeatureDefinition
from material_workbench.contracts.schemas import Candidate, CandidateInput, Prediction, Support, TargetValue
from material_workbench.data.observation_profile import (
    ObservationDatasetProfile,
    ObservationTrainingDataset,
    build_observation_training_dataset,
    load_observation_profile,
)
from material_workbench.domain.goal_targets import goal_fields
from material_workbench.modeling.curve_grid import anchored_curve_grid
from material_workbench.modeling.model_packages import (
    ModelPackageLoader,
    VerifiedModelPackage,
    predictive_interval,
    validate_predictive_summary,
    validate_task_definition_canonical_inputs,
)
from material_workbench.tasks.task_registry import load_task_contracts


TASK_ID = "welding-stage-c-properties-v1"
PROFILE_PATH = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "observation-profile-welding-consumable-stage-c-v1.json"
)
COMPOSITION_PATHS = tuple(
    f"composition.{name}"
    for name in ("C", "Si", "Mn", "P", "S", "Ni", "Cr", "Mo", "Cu", "Ti", "B", "Nb", "V", "Al", "N", "O")
)
TENSILE_FEATURES = (
    *COMPOSITION_PATHS,
    "process.heat_input_kj_per_mm",
    "process.preheat_temp_c",
)
CHARPY_FEATURES = (*TENSILE_FEATURES, "process.test_temperature_c")
TEST_SOLUTIONS = ("3.5%NaCl", "5%H2SO4")
CORROSION_FEATURES = (
    *COMPOSITION_PATHS,
    *(f"categorical.test_solution::{value}" for value in TEST_SOLUTIONS),
)
PIPELINE_FEATURES = (
    *CHARPY_FEATURES,
    *(f"categorical.test_solution::{value}" for value in TEST_SOLUTIONS),
)
TARGET_FAMILY = {
    "TS": "tensile",
    "YS": "tensile",
    "EL": "tensile",
    "RA": "tensile",
    "CHARPY_ENERGY": "charpy",
    "BRITTLE_FRACTURE": "charpy",
    "CORROSION_RATE": "corrosion",
}
TARGET_FEATURES = {
    **{target: TENSILE_FEATURES for target in ("TS", "YS", "EL", "RA")},
    **{target: CHARPY_FEATURES for target in ("CHARPY_ENERGY", "BRITTLE_FRACTURE")},
    "CORROSION_RATE": CORROSION_FEATURES,
}
OUTPUT_BOUNDS = {
    "TS": (0.0, None),
    "YS": (0.0, None),
    "EL": (0.0, 100.0),
    "RA": (0.0, 100.0),
    "CHARPY_ENERGY": (0.0, None),
    "BRITTLE_FRACTURE": (0.0, 100.0),
    "CORROSION_RATE": (0.0, None),
}


def _definitions() -> tuple[FeatureDefinition, ...]:
    definitions = []
    for name in PIPELINE_FEATURES:
        if name.startswith("composition."):
            group, unit = "composition", "mass% deposited metal"
        elif name.startswith("process."):
            group = "process"
            unit = "kJ/mm" if "heat_input" in name else "°C"
        else:
            group, unit = "categorical", "1"
        definitions.append(FeatureDefinition(name, unit, name, group))
    return tuple(definitions)


FEATURE_DEFINITIONS = _definitions()
FEATURE_DEFINITION_BY_NAME = {item.name: item for item in FEATURE_DEFINITIONS}


def _flat_candidate_inputs(candidate: CandidateInput) -> dict[str, float | str]:
    values: dict[str, float | str] = {}
    values.update({f"composition.{key}": float(value) for key, value in candidate.inputs.composition.items()})
    values.update({f"process.{key}": float(value) for key, value in candidate.inputs.process.items()})
    values.update({f"categorical.{key}": str(value) for key, value in candidate.inputs.categorical.items()})
    return values


def feature_values(inputs: dict[str, Any]) -> dict[str, float]:
    values = {
        name: float(inputs[name])
        for name in CHARPY_FEATURES
        if inputs.get(name) is not None
    }
    solution = inputs.get("categorical.test_solution")
    if solution is not None:
        values.update({
            f"categorical.test_solution::{choice}": float(solution == choice)
            for choice in TEST_SOLUTIONS
        })
    return values


def candidate_feature_values(candidate: CandidateInput) -> dict[str, float]:
    return feature_values(_flat_candidate_inputs(candidate))


def build_stage_c_features_from_observation(
    row: dict[str, Any],
    _medians: dict[str, float],
) -> FeatureBundle:
    values = feature_values(row["canonical_inputs"])
    names = tuple(name for name in PIPELINE_FEATURES if name in values)
    return FeatureBundle(
        "welding-stage-c-observation-transform",
        "1.0.0",
        tuple(FEATURE_DEFINITION_BY_NAME[name] for name in names),
        np.asarray([values[name] for name in names], dtype=float),
    )


@dataclass(frozen=True)
class StageCData:
    source_path: str
    source_mtime_ns: int
    source_sha256: str
    profile_path: str
    profile: ObservationDatasetProfile
    profile_id: str
    profile_digest: str
    observations: list[dict[str, Any]]
    medians: dict[str, float]
    measurement_labels: dict[str, str]
    row_count: int
    quality: list[dict[str, Any]]
    detected_quality: list[dict[str, Any]]
    technical_columns: dict[tuple[str, str], str]
    training_dataset: ObservationTrainingDataset

    def canonical_training_dataset(
        self,
        contract: Any,
        *,
        pipeline_version: str | None = None,
    ) -> dict[str, Any]:
        from material_workbench.modeling.model_lifecycle import (
            task_input_contract_digest,
        )

        rows = []
        for observation in self.observations:
            outputs = {
                target: float(value)
                for target, value in observation["outputs"].items()
                if observation["target_status"][target]["usable"]
            }
            if not outputs:
                continue
            rows.append({
                "observation_id": observation["id"],
                "parent_key": observation["parent_key"],
                "family": observation["family"],
                "features": feature_values(observation["canonical_inputs"]),
                "outputs": outputs,
            })
        rows.sort(key=lambda item: (item["family"], item["parent_key"], item["observation_id"]))
        return {
            "schema_version": "canonical-training-dataset/v1",
            "task_id": TASK_ID,
            "input_contract_digest": task_input_contract_digest(
                contract.task_definition
            ),
            "dataset_profile_digest": self.profile_digest,
            "source_data_digest": f"sha256:{self.source_sha256}",
            "feature_pipeline": {
                "id": "welding-stage-c-observation-transform",
                "version": pipeline_version or "1.0.0",
                "features": [
                    {"name": item.name, "unit": item.unit, "group": item.group}
                    for item in FEATURE_DEFINITIONS
                ],
            },
            "composition_defaults": {
                key.removeprefix("composition."): value
                for key, value in self.medians.items()
                if key.startswith("composition.")
            },
            "rows": rows,
        }


def load_stage_c_data(
    source: str | Path,
    profile: ObservationDatasetProfile | None = None,
) -> StageCData:
    source_path = Path(source)
    selected_profile = profile or load_observation_profile(PROFILE_PATH)
    training = build_observation_training_dataset(source_path, selected_profile)
    observations: list[dict[str, Any]] = []
    numeric_values: dict[str, list[float]] = {}
    labels: dict[str, str] = {}
    family_by_id = {family.id: family for family in selected_profile.families}
    for family_id, view in training.views.items():
        family = family_by_id[family_id]
        labels.update({output.key: output.key for output in family.outputs})
        for row in view.rows:
            for path, value in row.inputs.items():
                if isinstance(value, (int, float)):
                    numeric_values.setdefault(path, []).append(float(value))
            status = {
                target: {
                    "usable": state.usable,
                    "reason": " / ".join(state.reasons) if state.reasons else None,
                    "reasons": list(state.reasons),
                }
                for target, state in row.target_status.items()
            }
            observations.append({
                "id": row.observation_id,
                "parent_key": row.split_group_key,
                "family": family_id,
                "eligible": row.eligible,
                "canonical_inputs": dict(row.inputs),
                "composition": {
                    path.removeprefix("composition."): value
                    for path, value in row.inputs.items()
                    if path.startswith("composition.")
                },
                "features": {
                    path.split(".", 1)[1]: value
                    for path, value in row.inputs.items()
                    if not path.startswith("composition.")
                },
                "outputs": dict(row.outputs),
                "target_status": status,
                "run_context": {
                    "family": family_id,
                    "curation": {
                        "status": "accepted" if row.eligible else "blocked",
                        "reasons": list(row.exclusion_reasons),
                        "target_status": status,
                    },
                    "provenance": row.provenance.model_dump(mode="json"),
                },
            })
    medians = {path: float(median(values)) for path, values in numeric_values.items()}
    return StageCData(
        source_path=str(source_path),
        source_mtime_ns=source_path.stat().st_mtime_ns,
        source_sha256=training.source_sha256,
        profile_path=str(PROFILE_PATH),
        profile=selected_profile,
        profile_id=selected_profile.id,
        profile_digest=training.profile_digest,
        observations=observations,
        medians=medians,
        measurement_labels=labels,
        row_count=len(observations),
        quality=[],
        detected_quality=[],
        technical_columns={},
        training_dataset=training,
    )


class StageCRegressionRuntime:
    support_policy_id = "stage-c-family-group-knn-v1"

    def __init__(self, data: StageCData, package: str | Path | VerifiedModelPackage) -> None:
        self.data = data
        self.profile = data.profile
        self.task_id = TASK_ID
        self.model_package = package if isinstance(package, VerifiedModelPackage) else ModelPackageLoader().load(package)
        manifest = self.model_package.manifest
        contract = load_task_contracts()[TASK_ID]
        validate_task_definition_canonical_inputs(contract.task_definition, manifest)
        if manifest.task_id != TASK_ID:
            raise ValueError(f"Model package task {manifest.task_id} is incompatible with {TASK_ID}")
        if manifest.feature_pipeline.output_features != PIPELINE_FEATURES:
            raise ValueError("Stage C package feature pipeline is incompatible")
        specs = {item.target: item for item in manifest.predictors}
        if set(specs) != set(TARGET_FAMILY):
            raise ValueError("Stage C package predictors are incomplete")
        for target, expected in TARGET_FEATURES.items():
            if specs[target].feature_names != expected:
                raise ValueError(f"Stage C predictor features are incompatible: {target}")
            if specs[target].config.get("observation_family") != TARGET_FAMILY[target]:
                raise ValueError(f"Stage C predictor family is incompatible: {target}")
        self.predictors = {
            target: self.model_package.load_predictor(spec.id)
            for target, spec in specs.items()
        }
        stats_path = next(path for path in manifest.feature_pipeline.artifacts if path.endswith("training_stats.json"))
        self.training_stats = json.loads(self.model_package.artifact_path(stats_path).read_text(encoding="utf-8"))
        self._verify_smoke()
        self._build_support_reference()

    @property
    def output_keys(self) -> frozenset[str]:
        return frozenset(self.predictors)

    def _verify_smoke(self) -> None:
        smoke = self.model_package.manifest.smoke_test
        if smoke is None:
            raise ValueError("Stage C package must declare a smoke test")
        candidate = CandidateInput.model_validate_json(
            self.model_package.artifact_path(smoke.input).read_text(encoding="utf-8")
        )
        expected = json.loads(self.model_package.artifact_path(smoke.expected).read_text(encoding="utf-8"))
        values = candidate_feature_values(candidate)
        specs = {item.target: item for item in self.model_package.manifest.predictors}
        capabilities = {item.target: item for item in load_task_contracts()[TASK_ID].runtime_capability.targets}
        for target, predictor in self.predictors.items():
            summary = predictor.predict(values)
            validate_predictive_summary(summary, specs[target], capabilities[target])
            if not np.isclose(summary.point_estimate, expected[target], rtol=1e-7, atol=1e-7):
                raise ValueError("Stage C package smoke prediction is not reproducible")

    def _target_rows(self, target: str) -> list[dict[str, Any]]:
        return [
            row for row in self.data.observations
            if row["target_status"].get(target, {}).get("usable")
        ]

    def _build_support_reference(self) -> None:
        self.support_references: dict[str, dict[str, Any]] = {}
        for target, names in TARGET_FEATURES.items():
            rows = self._target_rows(target)
            raw = np.asarray([
                [feature_values(row["canonical_inputs"])[name] for name in names]
                for row in rows
            ])
            mean, scale = raw.mean(axis=0), raw.std(axis=0)
            scale[scale < 1e-9] = 1.0
            vectors = (raw - mean) / scale
            sample = vectors
            sample_groups = np.asarray([str(row["parent_key"]) for row in rows])
            if len(sample) > 500:
                indexes = np.linspace(0, len(sample) - 1, 500, dtype=int)
                sample, sample_groups = sample[indexes], sample_groups[indexes]
            distances = np.sqrt(((sample[:, None, :] - sample[None, :, :]) ** 2).mean(axis=2))
            distances[sample_groups[:, None] == sample_groups[None, :]] = np.inf
            self.support_references[target] = {
                "rows": rows,
                "mean": mean,
                "scale": scale,
                "vectors": vectors,
                "loo_nearest": distances.min(axis=1),
            }

    def _support(self, candidate: CandidateInput, target: str, include_similarity: bool) -> tuple[Support, list[dict[str, Any]]]:
        reference = self.support_references[target]
        values = candidate_feature_values(candidate)
        vector = np.asarray([values[name] for name in TARGET_FEATURES[target]])
        normalized = (vector - reference["mean"]) / reference["scale"]
        distances = np.sqrt(((reference["vectors"] - normalized) ** 2).mean(axis=1))
        nearest = float(distances.min())
        supported, caution = (float(value) for value in np.quantile(reference["loo_nearest"], (0.80, 0.95)))
        if nearest <= supported:
            status, message = "supported", "同じ観測familyの近い学習条件があります"
        elif nearest <= caution:
            status, message = "caution", "同じ観測familyの近傍はありますが密度が低い領域です"
        else:
            status, message = "extrapolated", "同じ観測familyの学習条件から外れています"
        similar = []
        if include_similarity:
            used_groups: set[str] = set()
            for index in np.argsort(distances):
                row = reference["rows"][int(index)]
                group = str(row["parent_key"])
                if group in used_groups:
                    continue
                used_groups.add(group)
                similar.append({
                    "observation_id": row["id"],
                    "observation_ids": [row["id"]],
                    "parent_key": group,
                    "source": f"Stage C {TARGET_FAMILY[target]}",
                    "distance": round(float(distances[index]), 4),
                    "outputs": {key: round(float(value), 4) for key, value in row["outputs"].items()},
                })
                if len(similar) == 6:
                    break
        return Support(
            status=status,
            distance=round(nearest, 4),
            percentile=round(float((reference["loo_nearest"] <= nearest).mean() * 100), 1),
            message=message,
            components={TARGET_FAMILY[target]: round(nearest, 4)},
            reference_count=len(reference["rows"]),
            supported_threshold=round(supported, 4),
            caution_threshold=round(caution, 4),
        ), similar

    def evidence(self, candidate: Candidate) -> tuple[Support, list[dict[str, Any]]]:
        return self._support(candidate, "TS", True)

    def support_summary(self, candidate: Candidate) -> Support:
        return self._support(candidate, "TS", False)[0]

    def support_by_target(self, candidate: Candidate) -> dict[str, Support]:
        return {target: self._support(candidate, target, False)[0] for target in self.output_keys}

    def similarity(self, candidate: Candidate, limit: int = 6, target: str | None = None) -> list[dict[str, Any]]:
        selected = target or "TS"
        if selected not in self.output_keys:
            raise ValueError(f"unknown similarity target: {selected}")
        return self._support(candidate, selected, True)[1][:limit]

    def predict_core(
        self,
        candidate: Candidate,
        detailed: bool = False,
        target_values: dict[str, TargetValue] | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        values = candidate_feature_values(candidate)
        definitions = {item.key: item for item in load_task_contracts()[TASK_ID].task_definition.outputs}
        predictions: dict[str, Prediction] = {}
        for target, predictor in self.predictors.items():
            summary = predictor.predict(values)
            lower, upper = predictive_interval(summary)
            point = summary.point_estimate
            minimum, maximum = OUTPUT_BOUNDS[target]
            point, lower, upper = max(minimum, point), max(minimum, lower), max(minimum, upper)
            quantiles = {level: max(minimum, float(value)) for level, value in summary.quantiles.items()}
            if maximum is not None:
                point, lower, upper = min(maximum, point), min(maximum, lower), min(maximum, upper)
                quantiles = {level: min(maximum, value) for level, value in quantiles.items()}
            goal_value, goal_lower, goal_upper, direction = goal_fields(
                (target_values or {}).get(target), definitions[target].goal_direction
            )
            predictions[target] = Prediction(
                value=round(point, 4),
                lower=round(lower, 4),
                upper=round(upper, 4),
                unit=summary.unit,
                target_kind=summary.target_kind,
                point_statistic=summary.point_statistic,
                predictive_family=summary.distribution["family"],
                quantiles={level: round(value, 6) for level, value in quantiles.items()},
                goal_value=goal_value,
                goal_lower=goal_lower,
                goal_upper=goal_upper,
                goal_probability=None,
                goal_direction=direction,
            )
        manifest = self.model_package.manifest
        return {
            "task_id": TASK_ID,
            "candidate_id": candidate.id,
            "mode": "detailed" if detailed else "preview",
            "predictions": predictions,
            "warnings": [],
            "canonical_input": {
                "input_schema_version": "canonical-candidate/v1",
                "composition": candidate.inputs.composition,
                "process": candidate.inputs.process,
                "categorical": candidate.inputs.categorical,
                "feature_vector": values,
            },
            "model_meta": {
                "task_id": TASK_ID,
                "model": {
                    "id": manifest.package_id,
                    "version": manifest.package_version,
                    "method": "family-specific ridge regression with grouped validation",
                },
                "package": {
                    "id": manifest.package_id,
                    "version": manifest.package_version,
                    "manifest_sha256": self.model_package.manifest_sha256,
                    "runtime_types": ["builtin.linear.v1"],
                    "predictors": {
                        item.target: {
                            "feature_names": list(item.feature_names),
                            "observation_family": item.config["observation_family"],
                            "training_cohort": item.config["training_cohort"],
                            "training_rows": item.config["training_rows"],
                            "evaluation_groups": item.config["evaluation_groups"],
                            "profile_digest": item.config["profile_digest"],
                        }
                        for item in manifest.predictors
                    },
                },
                "feature_pipeline": {
                    "id": manifest.feature_pipeline.id,
                    "version": manifest.feature_pipeline.version,
                    "input_schema_version": "canonical-candidate/v1",
                    "features": list(values),
                },
                "training_data": {
                    "source_path": self.data.source_path,
                    "source_sha256": self.data.source_sha256,
                    "records": self.training_stats["records"],
                    "groups_by_target": self.training_stats["groups_by_target"],
                    "profile_digest": self.data.profile_digest,
                },
                "prediction_interval": {
                    "method": "grouped out-of-fold residual quantiles",
                    "coverage": "central 90% empirical interval",
                    "grouping": "weld-run key",
                },
                "similarity": {
                    "version": self.support_policy_id,
                    "method": "nearest observation in target-specific standardized feature space",
                },
            },
            "heat_pattern": [],
            "response_curve": None,
        }

    def predict(self, candidate: Candidate, detailed: bool = False, **kwargs: Any) -> dict[str, Any]:
        kwargs.pop("include_curve", None)
        result = self.predict_core(candidate, detailed=detailed, **kwargs)
        support, similar = self.evidence(candidate)
        result["support"], result["similar"] = support, similar
        return result

    def response_curve_result(
        self,
        candidate: Candidate,
        target: str,
        variable: str,
        points: int,
        axis_range: tuple[float, float] | None = None,
    ) -> dict[str, Any]:
        if target not in self.output_keys:
            raise ValueError(f"unknown response curve target: {target}")
        if variable != "process.test_temperature_c":
            raise ValueError(f"この予測タスクで応答曲線にできない変数です: {variable}")
        rows = self._target_rows(target)
        curve_rows = self._target_rows("CHARPY_ENERGY")
        training = [float(row["canonical_inputs"][variable]) for row in curve_rows]
        current = float(candidate.inputs.process["test_temperature_c"])
        low, high = axis_range or (min(training), max(training))
        curve = []
        for x_value in anchored_curve_grid(low, high, points, current=current):
            adjusted = candidate.model_copy(deep=True)
            if variable in TARGET_FEATURES[target]:
                adjusted.inputs.process["test_temperature_c"] = float(x_value)
            summary = self.predictors[target].predict(candidate_feature_values(adjusted))
            lower, upper = predictive_interval(summary)
            minimum, maximum = OUTPUT_BOUNDS[target]
            value = max(minimum, summary.point_estimate)
            lower, upper = max(minimum, lower), max(minimum, upper)
            if maximum is not None:
                value, lower, upper = min(maximum, value), min(maximum, lower), min(maximum, upper)
            curve.append({
                "x": round(float(x_value), 5),
                "value": round(value, 5),
                "lower": round(lower, 5),
                "upper": round(upper, 5),
                "target_kind": summary.target_kind,
                "point_statistic": summary.point_statistic,
                "predictive_family": summary.distribution["family"],
                "quantiles": {"0.05": round(lower, 5), "0.95": round(upper, 5)},
            })
        definition = load_task_contracts()[TASK_ID].task_definition
        field = next(field for group in definition.input_groups for field in group.fields if field.path == variable)
        observed = [float(row["outputs"][target]) for row in rows]
        return {
            "target": target,
            "variable": {
                "id": variable,
                "label": field.label,
                "unit": field.unit or "",
                "min": low,
                "max": high,
                "current": current,
            },
            "points": curve,
            "output_range": {"min": min(observed), "max": max(observed)},
            "point_count": points,
            "policy_id": "anchored-axis-grid-v1",
        }


def stage_c_starter_candidates(medians: dict[str, float]) -> list[CandidateInput]:
    composition = {
        path.removeprefix("composition."): round(value, 6)
        for path, value in medians.items()
        if path.startswith("composition.")
    }
    heat = medians["process.heat_input_kj_per_mm"]
    preheat = medians["process.preheat_temp_c"]
    return [
        CandidateInput(
            name=label,
            inputs={
                "composition": composition,
                "process": {
                    "heat_input_kj_per_mm": round(heat * factor, 4),
                    "preheat_temp_c": round(preheat, 2),
                    "test_temperature_c": temperature,
                },
                "categorical": {"test_solution": TEST_SOLUTIONS[index % len(TEST_SOLUTIONS)]},
                "heat_pattern": None,
            },
        )
        for index, (label, factor, temperature) in enumerate((
            ("低温靭性の確認", 0.9, -40.0),
            ("代表溶接条件", 1.0, -20.0),
            ("高入熱条件", 1.1, 0.0),
        ))
    ]
