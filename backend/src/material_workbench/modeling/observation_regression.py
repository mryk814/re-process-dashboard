"""Allow-listed runtime for Observation-family Tasks.

Feature order, per-target feature sets, and target-to-family mapping come from
``ObservationTrainingSpec``, which derives them from the Observation Profile and
the TaskDefinition. This module holds no task id and no profile path; both are
declared once in ``task_composition/builtin/welding.py``.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from statistics import median
from typing import Any

import numpy as np

from material_workbench.contracts.feature_contracts import FeatureBundle, FeatureDefinition
from material_workbench.contracts.candidate_project_contracts import (
    Candidate,
    CandidateInput,
    TargetValue,
)
from material_workbench.contracts.prediction_catalog_contracts import (
    Prediction,
    Support,
)
from material_workbench.data.observation_profile import (
    ObservationDatasetProfile,
    ObservationTrainingDataset,
    build_observation_training_dataset,
    load_observation_profile,
)
from material_workbench.domain.goal_targets import goal_fields
from material_workbench.modeling.curve_grid import anchored_curve_grid
from material_workbench.modeling.response_curve_errors import (
    ResponseCurveNotApplicableError,
)
from material_workbench.modeling.observation_training_spec import (
    ObservationRuntimeDeclaration,
    ObservationTrainingSpec,
    observation_training_spec,
)
from material_workbench.modeling.model_package_contracts import (
    predictive_interval,
    validate_predictive_summary,
    validate_task_definition_canonical_inputs,
)
from material_workbench.modeling.model_package_verification import (
    ModelPackageLoader,
    VerifiedModelPackage,
)
from material_workbench.tasks.task_registry import load_task_contracts


def resolve_spec(declaration: ObservationRuntimeDeclaration) -> ObservationTrainingSpec:
    """Build the training spec from the declared Profile and TaskDefinition."""

    return observation_training_spec(
        declaration,
        load_observation_profile(declaration.profile_path),
        load_task_contracts()[declaration.task_id].task_definition,
    )


def _flat_candidate_inputs(candidate: CandidateInput) -> dict[str, float | str]:
    values: dict[str, float | str] = {}
    values.update({f"composition.{key}": float(value) for key, value in candidate.inputs.composition.items()})
    values.update({f"process.{key}": float(value) for key, value in candidate.inputs.process.items()})
    values.update({f"categorical.{key}": str(value) for key, value in candidate.inputs.categorical.items()})
    return values


def candidate_feature_values(
    candidate: CandidateInput, spec: ObservationTrainingSpec
) -> dict[str, float]:
    return spec.feature_values(_flat_candidate_inputs(candidate))


def build_observation_features_from_observation(
    row: dict[str, Any],
    _medians: dict[str, float],
    spec: ObservationTrainingSpec,
) -> FeatureBundle:
    values = spec.feature_values(row["canonical_inputs"])
    names = tuple(name for name in spec.pipeline_features if name in values)
    return FeatureBundle(
        spec.feature_transform_id,
        spec.feature_transform_version,
        tuple(spec.definition_for(name) for name in names),
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
    spec: ObservationTrainingSpec

    def canonical_training_dataset(
        self,
        contract: Any,
        *,
        pipeline_version: str | None = None,
    ) -> dict[str, Any]:
        from material_workbench.modeling.model_lifecycle import (
            task_input_contract_digest,
        )

        spec = self.spec
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
                "features": spec.feature_values(observation["canonical_inputs"]),
                "outputs": outputs,
            })
        rows.sort(key=lambda item: (item["family"], item["parent_key"], item["observation_id"]))
        return {
            "schema_version": "canonical-training-dataset/v1",
            "task_id": spec.task_id,
            "input_contract_digest": task_input_contract_digest(
                contract.task_definition
            ),
            "dataset_profile_digest": self.profile_digest,
            "source_data_digest": f"sha256:{self.source_sha256}",
            "feature_pipeline": {
                "id": spec.feature_transform_id,
                "version": pipeline_version or spec.feature_transform_version,
                "features": [
                    {"name": item.name, "unit": item.unit, "group": item.group}
                    for item in spec.feature_definitions
                ],
            },
            "composition_defaults": {
                key.removeprefix("composition."): value
                for key, value in self.medians.items()
                if key.startswith("composition.")
            },
            "rows": rows,
        }


def load_observation_data(
    source: str | Path,
    declaration: ObservationRuntimeDeclaration,
    profile: ObservationDatasetProfile | None = None,
) -> StageCData:
    source_path = Path(source)
    selected_profile = profile or load_observation_profile(declaration.profile_path)
    spec = observation_training_spec(
        declaration,
        selected_profile,
        load_task_contracts()[declaration.task_id].task_definition,
    )
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
        profile_path=str(declaration.profile_path),
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
        spec=spec,
    )


class ObservationRegressionRuntime:
    def __init__(self, data: StageCData, package: str | Path | VerifiedModelPackage) -> None:
        self.data = data
        self.profile = data.profile
        self.spec = data.spec
        self.task_id = self.spec.task_id
        self.support_policy_id = self.spec.support_policy_id
        self.model_package = package if isinstance(package, VerifiedModelPackage) else ModelPackageLoader().load(package)
        manifest = self.model_package.manifest
        contract = load_task_contracts()[self.task_id]
        self.task_definition = contract.task_definition
        self.output_definitions = {
            item.key: item for item in self.task_definition.outputs
        }
        self.runtime_capabilities = {
            item.target: item for item in contract.runtime_capability.targets
        }
        validate_task_definition_canonical_inputs(self.task_definition, manifest)
        if manifest.task_id != self.task_id:
            raise ValueError(
                f"Model package task {manifest.task_id} is incompatible with {self.task_id}"
            )
        if manifest.feature_pipeline.output_features != self.spec.pipeline_features:
            raise ValueError("Observation package feature pipeline is incompatible")
        specs = {item.target: item for item in manifest.predictors}
        if set(specs) != set(self.spec.target_family):
            raise ValueError("Observation package predictors are incomplete")
        for target, expected in self.spec.target_features.items():
            if specs[target].feature_names != expected:
                raise ValueError(f"Observation predictor features are incompatible: {target}")
            if specs[target].config.get("observation_family") != self.spec.target_family[target]:
                raise ValueError(f"Observation predictor family is incompatible: {target}")
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

    @property
    def chain_sampling_method(self) -> str:
        from material_workbench.modeling.stage_sampling import (
            sampling_capability_for_package,
        )

        capability = sampling_capability_for_package(self.model_package)
        return capability.method if capability is not None else ""

    @property
    def chain_sample_bounds(self) -> dict[str, tuple[float | None, float | None]]:
        from material_workbench.modeling.stage_sampling import (
            sampling_capability_for_package,
        )

        capability = sampling_capability_for_package(self.model_package)
        return dict(capability.output_bounds) if capability is not None else {}

    def sample_core(
        self,
        candidate: Candidate,
        *,
        sample_count: int,
        seed: int,
    ) -> "StageSampleResult":
        from material_workbench.contracts.chain_uncertainty_contracts import (
            StageSampleResult,
        )
        method = self.chain_sampling_method
        if not method:
            raise ValueError("このruntime/packageはStage sampleを提供しません")
        values = candidate_feature_values(candidate, self.spec)
        targets = sorted(self.predictors)
        seeds = np.random.SeedSequence(seed).spawn(len(targets))
        result: dict[str, list[float]] = {}
        for target, target_seed in zip(targets, seeds, strict=True):
            samples = self.predictors[target].sample(
                values,
                sample_count=sample_count,
                seed=int(target_seed.generate_state(1)[0]),
            )
            result[target] = [float(value) for value in samples]
        return StageSampleResult(
            method=method,
            sample_count=sample_count,
            outputs=result,
            reference_points={
                target: float(self.predictors[target].predict(values).point_estimate)
                for target in targets
            },
        )

    def _verify_smoke(self) -> None:
        smoke = self.model_package.manifest.smoke_test
        if smoke is None:
            raise ValueError("Stage C package must declare a smoke test")
        candidate = CandidateInput.model_validate_json(
            self.model_package.artifact_path(smoke.input).read_text(encoding="utf-8")
        )
        expected = json.loads(self.model_package.artifact_path(smoke.expected).read_text(encoding="utf-8"))
        values = candidate_feature_values(candidate, self.spec)
        specs = {item.target: item for item in self.model_package.manifest.predictors}
        for target, predictor in self.predictors.items():
            summary = predictor.predict(values)
            validate_predictive_summary(
                summary,
                specs[target],
                self.runtime_capabilities[target],
            )
            if not np.isclose(summary.point_estimate, expected[target], rtol=1e-7, atol=1e-7):
                raise ValueError("Stage C package smoke prediction is not reproducible")

    def _target_rows(self, target: str) -> list[dict[str, Any]]:
        return [
            row for row in self.data.observations
            if row["target_status"].get(target, {}).get("usable")
        ]

    def _build_support_reference(self) -> None:
        self.support_references: dict[str, dict[str, Any]] = {}
        for target, names in self.spec.target_features.items():
            rows = self._target_rows(target)
            raw = np.asarray([
                [self.spec.feature_values(row["canonical_inputs"])[name] for name in names]
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
            loo_nearest = distances.min(axis=1)
            supported_threshold, caution_threshold = (
                float(value)
                for value in np.quantile(loo_nearest, (0.80, 0.95))
            )
            self.support_references[target] = {
                "rows": rows,
                "mean": mean,
                "scale": scale,
                "vectors": vectors,
                "loo_nearest": loo_nearest,
                "supported_threshold": supported_threshold,
                "caution_threshold": caution_threshold,
            }

    def _support(self, candidate: CandidateInput, target: str, include_similarity: bool) -> tuple[Support, list[dict[str, Any]]]:
        reference = self.support_references[target]
        values = candidate_feature_values(candidate, self.spec)
        vector = np.asarray([values[name] for name in self.spec.target_features[target]])
        normalized = (vector - reference["mean"]) / reference["scale"]
        distances = np.sqrt(((reference["vectors"] - normalized) ** 2).mean(axis=1))
        nearest = float(distances.min())
        supported = float(reference["supported_threshold"])
        caution = float(reference["caution_threshold"])
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
                    "source": f"Stage C {self.spec.target_family[target]}",
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
            components={self.spec.target_family[target]: round(nearest, 4)},
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
        values = candidate_feature_values(candidate, self.spec)
        predictions: dict[str, Prediction] = {}
        for target, predictor in self.predictors.items():
            summary = predictor.predict(values)
            lower, upper = predictive_interval(summary)
            point = summary.point_estimate
            minimum, maximum = self.spec.output_bounds_for(target)
            point, lower, upper = max(minimum, point), max(minimum, lower), max(minimum, upper)
            quantiles = {level: max(minimum, float(value)) for level, value in summary.quantiles.items()}
            if maximum is not None:
                point, lower, upper = min(maximum, point), min(maximum, lower), min(maximum, upper)
                quantiles = {level: min(maximum, value) for level, value in quantiles.items()}
            goal_value, goal_lower, goal_upper, direction = goal_fields(
                (target_values or {}).get(target),
                self.output_definitions[target].goal_direction,
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
            "task_id": self.task_id,
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
                "task_id": self.task_id,
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

    def training_range_for(
        self,
        target: str,
        variable: str,
        *,
        stage_name: str | None = None,
        stage_position_m: float | None = None,
    ) -> tuple[float, float]:
        del stage_name, stage_position_m
        if target not in self.output_keys:
            raise ValueError(f"unknown response curve target: {target}")
        if variable not in self.spec.target_features[target]:
            raise ResponseCurveNotApplicableError(
                f"{target}のモデルは{variable}を入力に使わないため応答曲線を作成できません"
            )
        values = [
            float(row["canonical_inputs"][variable])
            for row in self._target_rows(target)
            if variable in row["canonical_inputs"]
        ]
        if not values:
            raise ValueError(f"{target}の学習実績から{variable}の範囲を解決できません")
        return min(values), max(values)

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
        training_min, training_max = self.training_range_for(target, variable)
        current = float(candidate.inputs.process["test_temperature_c"])
        low, high = axis_range or (training_min, training_max)
        curve = []
        for x_value in anchored_curve_grid(low, high, points, current=current):
            adjusted = candidate.model_copy(deep=True)
            if variable in self.spec.target_features[target]:
                adjusted.inputs.process["test_temperature_c"] = float(x_value)
            summary = self.predictors[target].predict(candidate_feature_values(adjusted, self.spec))
            lower, upper = predictive_interval(summary)
            minimum, maximum = self.spec.output_bounds_for(target)
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
        field = next(
            field
            for group in self.task_definition.input_groups
            for field in group.fields
            if field.path == variable
        )
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
                "training_range": {
                    "min": training_min,
                    "max": training_max,
                },
            },
            "points": curve,
            "output_range": {"min": min(observed), "max": max(observed)},
            "point_count": points,
            "policy_id": "anchored-axis-grid-v1",
        }


def stage_c_starter_candidates(
    medians: dict[str, float], spec: ObservationTrainingSpec
) -> list[CandidateInput]:
    """溶接Stage C固有のstarter fixture。Taskごとの縦スライスとして残す。"""

    composition = {
        path.removeprefix("composition."): round(value, 6)
        for path, value in medians.items()
        if path.startswith("composition.")
    }
    heat = medians["process.heat_input_kj_per_mm"]
    preheat = medians["process.preheat_temp_c"]
    solutions = spec.categorical_choices["categorical.test_solution"]
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
                "categorical": {"test_solution": solutions[index % len(solutions)]},
                "heat_pattern": None,
            },
        )
        for index, (label, factor, temperature) in enumerate((
            ("低温靭性の確認", 0.9, -40.0),
            ("代表溶接条件", 1.0, -20.0),
            ("高入熱条件", 1.1, 0.0),
        ))
    ]
