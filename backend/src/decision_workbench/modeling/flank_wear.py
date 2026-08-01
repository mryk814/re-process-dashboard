"""Cutting-tool flank wear task: dedicated workbook loader and runtime.

摩耗曲線（横軸=切削距離）そのものが予測対象なので、切削距離は候補入力の
1フィールドとして持ちつつ、応答曲線APIで曲線として提示する。
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Any, Mapping

import numpy as np

from decision_workbench.contracts.feature_contracts import feature_index_families
from decision_workbench.contracts.candidate_project_contracts import (
    Candidate,
    CandidateInput,
    TargetValue,
)
from decision_workbench.contracts.prediction_catalog_contracts import (
    Prediction,
    Support,
)
from decision_workbench.modeling.curve_grid import numeric_domain_grid
from decision_workbench.data.profiles.canonicalization import canonicalize_workbook
from decision_workbench.data.profiles.loading import (
    load_dataset_profile,
    load_task_definitions,
)
from decision_workbench.data.profiles.schema import DatasetInputProfile
from decision_workbench.domain.goal_targets import goal_fields, probability_from_cdf
from decision_workbench.modeling.flank_wear_feature_pipeline import (
    CATEGORICAL_CHOICES,
    COMPOSITION_NAMES,
    FEATURE_DEFINITIONS,
    FEATURE_NAMES,
    INPUT_SCHEMA_VERSION,
    PIPELINE_ID,
    PIPELINE_VERSION,
    PROCESS_NAMES,
    TASK_ID,
    build_flank_wear_features,
    build_flank_wear_features_from_observation,
)
from decision_workbench.modeling.packages.contracts import (
    predictive_interval,
    validate_predictive_summary,
    validate_task_definition_canonical_inputs,
)
from decision_workbench.modeling.packages.loader import ModelPackageLoader
from decision_workbench.modeling.packages.verification import VerifiedModelPackage
from decision_workbench.tasks.task_registry import load_task_contracts

SUPPORT_POLICY_ID = "flank-wear-run-knn-v1"
DEFAULT_SOURCE_ENV = "WORKBENCH_FLANK_WEAR_SOURCE_PATH"
DEFAULT_SOURCE = "data/source/cutting_tool_flank_wear_synthetic_dataset.xlsx"
PROFILE_PATH = Path(__file__).parent.parent / "data" / "dataset-input-profile-flank-wear-v1.json"
FEATURE_GROUP_INDICES = feature_index_families(
    FEATURE_DEFINITIONS,
    {
        "composition": ("composition",),
        "material": ("metallurgy",),
        "tool": ("other",),
        "process": ("process",),
    },
)
_ENTITY_PROCESS_PATHS = tuple(name for name in PROCESS_NAMES if name != "cutting_distance_m")


def resolve_flank_wear_source(override: str | Path | None = None) -> Path:
    configured = Path(override or os.getenv(DEFAULT_SOURCE_ENV, DEFAULT_SOURCE))
    if configured.is_absolute() or configured.exists():
        return configured
    repository_default = Path(__file__).resolve().parents[4] / configured
    return repository_default if repository_default.exists() else configured


@dataclass(frozen=True)
class FlankWearData:
    source_path: str
    source_mtime_ns: int
    source_sha256: str
    profile_path: str
    profile: DatasetInputProfile
    profile_id: str
    measurement_labels: dict[str, str]
    observations: list[dict[str, Any]]
    medians: dict[str, float]
    run_count: int


def load_flank_wear_data(
    path: str | Path,
    profile_path: str | Path | None = None,
    *,
    profile: DatasetInputProfile | None = None,
) -> FlankWearData:
    from openpyxl import load_workbook

    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Flank-wear Excel source not found: {path}")
    if profile is not None and profile_path is not None:
        raise ValueError("profileとprofile_pathは同時に指定できません")
    if profile is None:
        selected_profile_path = Path(profile_path) if profile_path else PROFILE_PATH
        profile = load_dataset_profile(selected_profile_path)
        profile_locator = str(selected_profile_path)
    else:
        profile_locator = f"catalog:{profile.profile_id}"
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        canonical = canonicalize_workbook(workbook, profile)
    finally:
        workbook.close()

    def entity_values(identity: tuple[str, str]) -> Mapping[str, Any]:
        entity = canonical.entities.get(identity)
        return entity.values.get(TASK_ID, {}) if entity else {}

    run_links: dict[str, dict[str, str]] = {}
    for relation in canonical.relations:
        run_identity = relation.get("wear_test")
        if run_identity is None:
            continue
        link = {
            entity_type: relation[entity_type][1]
            for entity_type in ("material", "tool", "condition")
            if entity_type in relation
        }
        run_links.setdefault(run_identity[1], link)

    physical_ranges = profile.shared.physical_ranges.get(TASK_ID, {})
    measurement_labels = {
        target.key: target.column
        for observation in profile.tasks[TASK_ID].observations
        for target in observation.targets
    }
    observations: list[dict[str, Any]] = []
    for row in canonical.observations:
        if row.task_id != TASK_ID:
            continue
        run_key = row.parent_key
        link = run_links.get(run_key, {})
        material = entity_values(("material", link["material"])) if "material" in link else {}
        tool = entity_values(("tool", link["tool"])) if "tool" in link else {}
        condition = entity_values(("condition", link["condition"])) if "condition" in link else {}
        composition = {
            name: float(value)
            for name in COMPOSITION_NAMES
            if isinstance((value := material.get(f"composition.{name}")), (int, float))
        }
        entity_features = {**tool, **condition, **material}
        features: dict[str, Any] = {}
        for name in _ENTITY_PROCESS_PATHS:
            value = entity_features.get(f"process.{name}")
            if isinstance(value, (int, float)):
                features[name] = float(value)
        for name in CATEGORICAL_CHOICES:
            value = entity_features.get(f"categorical.{name}")
            if isinstance(value, str):
                features[name] = value
        distance = row.canonical_measurements.get("distance_m")
        if distance is not None:
            features["cutting_distance_m"] = float(distance)
        outputs = {
            measurement_labels[key]: float(value)
            for key, value in row.targets.items()
        }
        eligibility_reasons: list[str] = []
        if not row.policy_results.get("valid_observation/v1", False):
            eligibility_reasons.append("測定状態が有効ではありません")
        if not link or len(link) < 3:
            eligibility_reasons.append("試験と材料・工具・条件の対応が一意に決まりません")
        if distance is None:
            eligibility_reasons.append("切削距離がありません")
        if len(composition) != len(COMPOSITION_NAMES):
            eligibility_reasons.append("試作材成分が不足しています")
        if any(name not in features for name in PROCESS_NAMES if name != "cutting_distance_m"):
            eligibility_reasons.append("工具・切削条件の数値が不足しています")
        if any(name not in features for name in CATEGORICAL_CHOICES):
            eligibility_reasons.append("工具・切削条件の区分が不足しています")
        if not outputs:
            eligibility_reasons.append("摩耗量の測定値がありません")
        for key, value in row.targets.items():
            bounds = physical_ranges.get(key)
            if bounds and not bounds[0] <= value <= bounds[1]:
                label = measurement_labels[key]
                eligibility_reasons.append(f"{label}が物理範囲外です")
        observations.append({
            "id": row.id,
            "task_id": TASK_ID,
            "source": profile.sheet_for_role(row.source_role),
            "parent_key": run_key,
            "features": features or None,
            "composition": composition or None,
            "outputs": outputs,
            "eligible": not eligibility_reasons,
            "eligibility_reasons": eligibility_reasons,
            "date": row.metadata.get("date"),
            "measurement_order": row.metadata.get("order"),
            "run_context": {
                "material_key": link.get("material", ""),
                "tool_key": link.get("tool", ""),
                "condition_key": link.get("condition", ""),
            },
        })

    element_series: dict[str, list[float]] = defaultdict(list)
    for identity, entity in canonical.entities.items():
        if identity[0] != "material":
            continue
        for name in COMPOSITION_NAMES:
            value = entity.values.get(TASK_ID, {}).get(f"composition.{name}")
            if isinstance(value, (int, float)):
                element_series[name].append(float(value))
    medians = {name: float(median(values)) for name, values in element_series.items() if values}

    with path.open("rb") as source_file:
        source_sha256 = hashlib.file_digest(source_file, "sha256").hexdigest()
    return FlankWearData(
        source_path=str(path),
        source_mtime_ns=path.stat().st_mtime_ns,
        source_sha256=source_sha256,
        profile_path=profile_locator,
        profile=profile,
        profile_id=profile.profile_id,
        measurement_labels=measurement_labels,
        observations=observations,
        medians=medians,
        run_count=len(run_links),
    )


def _normalize_curve_variable(variable: str) -> str:
    if variable.startswith(("composition.", "process.")):
        return variable
    if variable in PROCESS_NAMES:
        return f"process.{variable}"
    raise ValueError(f"この予測タスクで応答曲線にできない変数です: {variable}")


class FlankWearRuntime:
    task_id = TASK_ID
    support_policy_id = SUPPORT_POLICY_ID
    feature_group_indices = FEATURE_GROUP_INDICES

    def __init__(
        self,
        data: FlankWearData,
        package_root: str | Path | VerifiedModelPackage | None = None,
    ) -> None:
        self.data = data
        default = Path(__file__).resolve().parents[4] / "models" / "packages" / "flank-wear-gp-2026-07"
        selected_package = package_root or default
        self.model_package = (
            selected_package
            if isinstance(selected_package, VerifiedModelPackage)
            else ModelPackageLoader().load(selected_package)
        )
        manifest = self.model_package.manifest
        task_definition = load_task_definitions()[TASK_ID]
        validate_task_definition_canonical_inputs(task_definition, manifest)
        contract_choices = {
            field.path.removeprefix("categorical."): tuple(field.choices)
            for group in task_definition.input_groups
            for field in group.fields
            if field.kind == "categorical"
        }
        if contract_choices != CATEGORICAL_CHOICES:
            raise ValueError("Flank-wear categorical choices do not match TaskDefinition")
        if manifest.task_id != TASK_ID:
            raise ValueError(f"Model package task {manifest.task_id} is incompatible with {TASK_ID}")
        if (manifest.feature_pipeline.id, manifest.feature_pipeline.version) != (PIPELINE_ID, PIPELINE_VERSION):
            raise ValueError("Flank-wear model package feature pipeline is incompatible")
        if tuple(manifest.feature_pipeline.output_features) != FEATURE_NAMES:
            raise ValueError("Flank-wear model package feature order is incompatible")
        stats_path = next(path for path in manifest.feature_pipeline.artifacts if path.endswith("training_stats.json"))
        stats = json.loads(self.model_package.artifact_path(stats_path).read_text(encoding="utf-8"))
        self.composition_defaults = {name: float(value) for name, value in stats["composition_defaults"].items()}
        self.training_counts = {name: int(value) for name, value in stats["records"].items()}
        self.predictors = {spec.target: self.model_package.load_predictor(spec.id) for spec in manifest.predictors}
        self._verify_package_smoke()
        self._build_support_reference()

    @property
    def output_keys(self) -> frozenset[str]:
        return frozenset(self.predictors)

    def _verify_package_smoke(self) -> None:
        smoke = self.model_package.manifest.smoke_test
        if not smoke:
            raise ValueError("Flank-wear model package must declare a smoke test")
        candidate = CandidateInput.model_validate(json.loads(self.model_package.artifact_path(smoke.input).read_text(encoding="utf-8")))
        expected = json.loads(self.model_package.artifact_path(smoke.expected).read_text(encoding="utf-8"))
        values = build_flank_wear_features(candidate, self.composition_defaults).as_dict()
        specs = {spec.target: spec for spec in self.model_package.manifest.predictors}
        capabilities = {item.target: item for item in load_task_contracts()[TASK_ID].runtime_capability.targets}
        summaries = {target: predictor.predict(values) for target, predictor in self.predictors.items()}
        for target, summary in summaries.items():
            validate_predictive_summary(summary, specs[target], capabilities[target])
        actual = {target: summary.point_estimate for target, summary in summaries.items()}
        if set(actual) != set(expected) or any(not np.isclose(actual[target], expected[target], rtol=1e-7, atol=1e-7) for target in actual):
            raise ValueError("Flank-wear model package smoke test did not reproduce expected predictions")

    def _build_support_reference(self) -> None:
        eligible = [
            row for row in self.data.observations
            if row["eligible"] and row["features"] and row["composition"]
        ]
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in eligible:
            grouped[str(row["parent_key"])].append(row)
        self.reference_rows = [rows for _, rows in sorted(grouped.items())]
        if not self.reference_rows:
            raise ValueError("No eligible flank-wear observations are available for support estimation")
        run_vectors = []
        for rows in self.reference_rows:
            bundles = [build_flank_wear_features_from_observation(row, self.composition_defaults) for row in rows]
            if any(bundle is None for bundle in bundles):
                raise ValueError("eligible flank-wear observations must convert to candidates")
            run_vectors.append(np.vstack([bundle.values for bundle in bundles if bundle is not None]).mean(axis=0))
        raw = np.vstack(run_vectors)
        self.reference_mean = raw.mean(axis=0)
        self.reference_scale = raw.std(axis=0)
        self.reference_scale[self.reference_scale < 1e-9] = 1.0
        self.reference_vectors = (raw - self.reference_mean) / self.reference_scale
        if len(self.reference_vectors) > 1:
            self.loo_nearest = np.empty(len(self.reference_vectors), dtype=float)
            for index, vector in enumerate(self.reference_vectors):
                distances = self._distance(self.reference_vectors, vector)
                distances[index] = np.inf
                self.loo_nearest[index] = float(distances.min())
        else:
            self.loo_nearest = np.array([0.0])
        self.supported_threshold, self.caution_threshold = (
            float(value)
            for value in np.quantile(self.loo_nearest, (0.80, 0.95))
        )

    def vector(self, candidate: CandidateInput) -> np.ndarray:
        return build_flank_wear_features(candidate, self.composition_defaults).values

    @staticmethod
    def _distance(reference: np.ndarray, point: np.ndarray, columns: tuple[int, ...] | None = None) -> np.ndarray:
        if columns is not None:
            return np.sqrt(((reference[:, columns] - point[list(columns)]) ** 2).mean(axis=1))
        parts = [
            ((reference[:, group] - point[list(group)]) ** 2).mean(axis=1)
            for group in FEATURE_GROUP_INDICES.values()
        ]
        return np.sqrt(np.vstack(parts).mean(axis=0))

    def _run_summary(self, rows: list[dict[str, Any]]) -> dict[str, dict[str, float | int]]:
        values: dict[str, list[tuple[float, float]]] = defaultdict(list)
        for row in rows:
            distance = row["features"].get("cutting_distance_m") if row["features"] else None
            for label, value in row["outputs"].items():
                values[label].append((float(distance if distance is not None else 0.0), float(value)))
        summary: dict[str, dict[str, float | int]] = {}
        for label, points in sorted(values.items()):
            series = [value for _, value in points]
            summary[label] = {
                "mean": round(float(np.mean(series)), 3),
                "std": round(float(np.std(series)), 3),
                "n": len(series),
            }
        return summary

    def _support(self, candidate: CandidateInput, *, include_similarity: bool = True) -> tuple[Support, list[dict[str, Any]]]:
        normalized = (self.vector(candidate) - self.reference_mean) / self.reference_scale
        distances = self._distance(self.reference_vectors, normalized)
        nearest_index = int(np.argmin(distances))
        nearest = float(distances[nearest_index])
        supported_limit = self.supported_threshold
        caution_limit = self.caution_threshold
        if nearest <= supported_limit:
            status, message = "supported", "近い摩耗試験条件に有効な実測があります"
        elif nearest <= caution_limit:
            status, message = "caution", "近傍はありますが、摩耗試験条件の密度が低い領域です"
        else:
            status, message = "extrapolated", "学習済みの摩耗試験条件から外れています。予測値は探索的な参考です"
        nearest_rows: list[dict[str, Any]] = []
        if include_similarity:
            for index in np.argsort(distances)[:3]:
                rows = self.reference_rows[int(index)]
                context = rows[0].get("run_context", {})
                nearest_rows.append({
                    "observation_id": rows[0]["id"],
                    "observation_ids": [row["id"] for row in rows],
                    "parent_key": rows[0]["parent_key"],
                    "source": " / ".join(sorted({
                        key for key in (context.get("material_key"), context.get("tool_key"), context.get("condition_key")) if key
                    })),
                    "distance": round(float(distances[index]), 4),
                    "components": {
                        name: round(float(self._distance(self.reference_vectors, normalized, columns)[int(index)]), 4)
                        for name, columns in FEATURE_GROUP_INDICES.items()
                    },
                    "repeat_summary": self._run_summary(rows),
                })
        return Support(
            status=status,
            distance=round(nearest, 4),
            percentile=round(float((self.loo_nearest <= nearest).mean() * 100), 1),
            message=message,
            components={
                name: round(float(self._distance(self.reference_vectors, normalized, columns)[nearest_index]), 4)
                for name, columns in FEATURE_GROUP_INDICES.items()
            },
            reference_count=len(self.reference_rows),
            supported_threshold=round(supported_limit, 4),
            caution_threshold=round(caution_limit, 4),
        ), nearest_rows

    @staticmethod
    def _goal_probability(summary: Any, goal: TargetValue) -> float | None:
        distribution = summary.distribution
        log_mean = distribution.get("log_mean")
        log_std = distribution.get("log_std")
        if log_mean is None or log_std is None or float(log_std) <= 0:
            return None
        def cdf(value: float) -> float:
            if value < 0:
                return 0.0
            z = (math.log1p(value) - float(log_mean)) / float(log_std)
            return 0.5 * math.erfc(-z / math.sqrt(2.0))
        return probability_from_cdf(goal, cdf, "at_most")

    def predict_core(self, candidate: Candidate, detailed: bool = False, target_values: dict[str, TargetValue] | None = None, **_: Any) -> dict[str, Any]:
        bundle = build_flank_wear_features(candidate, self.composition_defaults)
        values = bundle.as_dict()
        predictions: dict[str, Prediction] = {}
        warnings: list[str] = []
        for target, predictor in self.predictors.items():
            summary = predictor.predict(values)
            warnings.extend(summary.warnings)
            goal = (target_values or {}).get(target)
            goal_probability = None if goal is None else self._goal_probability(summary, goal)
            goal_value, goal_lower, goal_upper, goal_direction = goal_fields(goal, "at_most")
            lower, upper = predictive_interval(summary)
            predictions[target] = Prediction(
                value=round(summary.point_estimate, 3),
                lower=round(lower, 3),
                upper=round(upper, 3),
                unit=summary.unit,
                target_kind=summary.target_kind,
                point_statistic=summary.point_statistic,
                predictive_family=summary.distribution.get("family", "empirical_quantiles"),
                quantiles={level: round(float(item), 6) for level, item in summary.quantiles.items()},
                categories=list(summary.distribution.get("categories", [])),
                goal_value=goal_value,
                goal_lower=goal_lower,
                goal_upper=goal_upper,
                goal_probability=None if goal_probability is None else round(goal_probability, 4),
                goal_direction=goal_direction,
                uncertainty_components=None if summary.uncertainty_components is None else {
                    name: round(float(value), 6) for name, value in summary.uncertainty_components.items()
                },
            )
        return {
            "task_id": self.task_id,
            "candidate_id": candidate.id,
            "mode": "detailed" if detailed else "preview",
            "predictions": predictions,
            "warnings": warnings,
            "canonical_input": {
                "input_schema_version": INPUT_SCHEMA_VERSION,
                "composition_mass_percent": {name: values[name] for name in self.composition_defaults},
                "process": {
                    **{name: float(candidate.inputs.process[name]) for name in PROCESS_NAMES},
                    **candidate.inputs.categorical,
                    "machining_method": "外径旋削",
                },
                "feature_vector": values,
            },
            "model_meta": {
                "task_id": self.task_id,
                "model": {
                    "id": self.model_package.manifest.package_id,
                    "version": self.model_package.manifest.package_version,
                    "method": "Gaussian process regression on log wear",
                },
                "package": {
                    "id": self.model_package.manifest.package_id,
                    "version": self.model_package.manifest.package_version,
                    "manifest_sha256": self.model_package.manifest_sha256,
                    "runtime_types": sorted({item.runtime_type for item in self.model_package.manifest.predictors}),
                },
                "feature_pipeline": {
                    "id": PIPELINE_ID,
                    "version": PIPELINE_VERSION,
                    "input_schema_version": INPUT_SCHEMA_VERSION,
                    "features": list(FEATURE_NAMES),
                },
                "training_data": {
                    "source_path": self.data.source_path,
                    "source_sha256": self.data.source_sha256,
                    "records": self.training_counts,
                },
                "prediction_interval": {
                    "method": "gaussian_process_predictive_distribution_log_space",
                    "coverage": "central 90% predictive interval",
                    "note": "摩耗量はlog(1+VB)空間のガウス過程で予測し、µmへ逆変換しています。",
                },
                "similarity": {"version": SUPPORT_POLICY_ID, "method": "run-level nearest neighbor over composition, material, tool, and process feature groups"},
            },
            "heat_pattern": [],
            "response_curve": None,
        }

    def evidence(self, candidate: Candidate) -> tuple[Support, list[dict[str, Any]]]:
        return self._support(candidate)

    def support_summary(self, candidate: Candidate) -> Support:
        return self._support(candidate, include_similarity=False)[0]

    def support_by_target(self, candidate: Candidate) -> dict[str, Support]:
        support = self.support_summary(candidate)
        return {target: support for target in sorted(self.output_keys)}

    def similarity(self, candidate: Candidate, limit: int = 3, target: str | None = None) -> list[dict[str, Any]]:
        return self.evidence(candidate)[1][:limit]

    def predict(self, candidate: Candidate, detailed: bool = False, **kwargs: Any) -> dict[str, Any]:
        kwargs.pop("include_curve", None)
        result = self.predict_core(candidate, detailed=detailed, **kwargs)
        support, similar = self.evidence(candidate)
        result["support"] = support
        result["similar"] = similar
        if support.status != "supported":
            result["warnings"].append(support.message)
        return result

    def _curve_training_values(self, target: str, variable: str) -> list[float]:
        values: list[float] = []
        measurement_key = self.data.measurement_labels[target]
        for rows in self.reference_rows:
            for row in rows:
                if measurement_key not in row["outputs"]:
                    continue
                if variable.startswith("composition."):
                    value = (row["composition"] or {}).get(variable.removeprefix("composition."))
                else:
                    value = (row["features"] or {}).get(variable.removeprefix("process."))
                if isinstance(value, (int, float)) and math.isfinite(float(value)):
                    values.append(float(value))
        return values

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
            raise ValueError(f"Unsupported response-curve target: {target}")
        values = self._curve_training_values(target, variable)
        if not values:
            raise ValueError(f"{target}の学習実績から{variable}の範囲を解決できません")
        return min(values), max(values)

    def _curve_variable_current(self, candidate: Candidate, variable: str) -> float:
        if variable.startswith("composition."):
            name = variable.removeprefix("composition.")
            if name not in COMPOSITION_NAMES:
                raise ValueError(f"この予測タスクで応答曲線にできない変数です: {variable}")
            return float(candidate.inputs.composition.get(name, self.composition_defaults.get(name, 0.0)))
        name = variable.removeprefix("process.")
        if name not in PROCESS_NAMES:
            raise ValueError(f"この予測タスクで応答曲線にできない変数です: {variable}")
        return float(candidate.inputs.process[name])

    def _curve_variable_meta(
        self,
        candidate: Candidate,
        target: str,
        variable: str,
    ) -> dict[str, Any]:
        definition = load_task_definitions()[TASK_ID]
        field = next((field for group in definition.input_groups for field in group.fields if field.path == variable), None)
        label, unit = (field.label, field.unit or "") if field else (variable.split(".", 1)[1], "")
        training_min, training_max = self.training_range_for(target, variable)
        current = self._curve_variable_current(candidate, variable)
        low, high = training_min, training_max
        padding = max((high - low) * 0.05, 1e-6)
        lower_bound = -30.0 if variable.endswith("rake_angle_deg") else 0.0
        return {
            "id": variable,
            "label": label,
            "unit": unit,
            "min": round(max(lower_bound, low - padding), 4),
            "max": round(high + padding, 4),
            "current": round(current, 4),
            "training_range": {
                "min": round(training_min, 4),
                "max": round(training_max, 4),
            },
        }

    @staticmethod
    def _set_curve_variable(candidate: Candidate, variable: str, value: float) -> None:
        if variable.startswith("composition."):
            candidate.inputs.composition[variable.removeprefix("composition.")] = min(100.0, max(0.0, float(value)))
            return
        name = variable.removeprefix("process.")
        lower = -30.0 if name == "rake_angle_deg" else 0.001 if name in {"cutting_speed_mpm", "feed_mm_rev", "depth_of_cut_mm"} else 0.0
        candidate.inputs.process[name] = max(lower, float(value))

    def _sweep_axis(self, candidate: Candidate, target: str, axis: str, axis_meta: dict[str, Any], points: int) -> list[dict[str, float]]:
        curve: list[dict[str, float]] = []
        for x_value in anchored_curve_grid(
            axis_meta["min"],
            axis_meta["max"],
            points,
            current=axis_meta.get("current"),
        ):
            adjusted = candidate.model_copy(deep=True)
            self._set_curve_variable(adjusted, axis, float(x_value))
            summary = self.predictors[target].predict(build_flank_wear_features(adjusted, self.composition_defaults).as_dict())
            value = summary.point_estimate
            lower, upper = predictive_interval(summary)
            curve.append({
                "x": round(float(x_value), 4),
                "value": round(value, 3),
                "lower": round(lower, 3),
                "upper": round(upper, 3),
                "target_kind": summary.target_kind,
                "point_statistic": summary.point_statistic,
                "predictive_family": summary.distribution.get("family", "empirical_quantiles"),
                "quantiles": {level: round(float(item), 6) for level, item in summary.quantiles.items()},
                "categories": list(summary.distribution.get("categories", [])),
            })
        return curve

    @staticmethod
    def _categorical_field_label(name: str) -> str:
        definition = load_task_definitions()[TASK_ID]
        path = f"categorical.{name}"
        field = next((field for group in definition.input_groups for field in group.fields if field.path == path), None)
        if field is None:
            raise ValueError(f"この予測タスクで応答曲線にできない変数です: {path}")
        return field.label

    def curve_family_result(
        self,
        candidate: Candidate,
        target: str,
        vary_variable: str | None,
        levels: int,
        points: int,
    ) -> dict[str, Any]:
        """摩耗曲線（横軸=curve axis）を、別の設計変数を数水準ふって重ねる。

        vary_variableが `categorical.<name>` のときは、その離散選択肢すべてを
        水準として使う（levelsパラメータは無視する。連続量の水準とは意味が違う）。
        """
        if target not in self.predictors:
            raise ValueError(f"Unsupported curve-family target: {target}")
        definition = load_task_definitions()[TASK_ID]
        axis = definition.curve_axis_path
        assert axis is not None
        axis_meta = self._curve_variable_meta(candidate, target, axis)
        column = self.data.measurement_labels[target]
        observed = [
            float(row["outputs"][column])
            for rows in self.reference_rows for row in rows
            if column in row["outputs"]
        ]
        vary_meta: dict[str, Any] | None = None
        vary_categorical: dict[str, Any] | None = None
        if not vary_variable:
            series = [{
                "level": None,
                "label": "現在の候補",
                "points": self._sweep_axis(candidate, target, axis, axis_meta, points),
            }]
        elif vary_variable.startswith("categorical."):
            name = vary_variable.removeprefix("categorical.")
            choices = CATEGORICAL_CHOICES.get(name)
            if choices is None:
                raise ValueError(f"この予測タスクで応答曲線にできない変数です: {vary_variable}")
            current = candidate.inputs.categorical.get(name, choices[0])
            vary_categorical = {
                "id": vary_variable,
                "label": self._categorical_field_label(name),
                "choices": list(choices),
                "current": current,
            }
            series = []
            for choice in choices:
                adjusted = candidate.model_copy(deep=True)
                adjusted.inputs.categorical[name] = choice
                series.append({
                    "level": choice,
                    "label": choice,
                    "points": self._sweep_axis(adjusted, target, axis, axis_meta, points),
                })
        else:
            vary = _normalize_curve_variable(vary_variable)
            if vary == axis:
                raise ValueError("曲線の横軸と同じ変数は水準にできません")
            vary_meta = self._curve_variable_meta(candidate, target, vary)
            vary_field = next(
                field
                for group in definition.input_groups
                for field in group.fields
                if field.path == vary
            )
            unit = f" {vary_meta['unit']}" if vary_meta["unit"] else ""
            series = []
            for level in numeric_domain_grid(
                vary_meta["min"],
                vary_meta["max"],
                levels,
                field=vary_field,
            ):
                adjusted = candidate.model_copy(deep=True)
                self._set_curve_variable(adjusted, vary, float(level))
                series.append({
                    "level": round(float(level), 4),
                    "label": f"{vary_meta['label']} {round(float(level), 4):g}{unit}",
                    "points": self._sweep_axis(adjusted, target, axis, axis_meta, points),
                })
        return {
            "target": target,
            "axis": axis_meta,
            "vary": vary_meta,
            "vary_categorical": vary_categorical,
            "series": series,
            "output_range": None if not observed else {"min": round(min(observed), 4), "max": round(max(observed), 4)},
            "point_count": points,
            "policy_id": "anchored-axis-grid-v1",
        }

    def response_curve_result(
        self,
        candidate: Candidate,
        target: str,
        variable: str,
        points: int,
        axis_range: tuple[float, float] | None = None,
    ) -> dict[str, Any]:
        if target not in self.predictors:
            raise ValueError(f"Unsupported response-curve target: {target}")
        variable = _normalize_curve_variable(variable)
        meta = self._curve_variable_meta(candidate, target, variable)
        if axis_range is not None:
            meta = {**meta, "min": axis_range[0], "max": axis_range[1]}
        column = self.data.measurement_labels[target]
        observed = [
            float(row["outputs"][column])
            for rows in self.reference_rows for row in rows
            if column in row["outputs"]
        ]
        return {
            "target": target,
            "variable": meta,
            "points": self._sweep_axis(candidate, target, variable, meta, points),
            "output_range": None if not observed else {"min": round(min(observed), 4), "max": round(max(observed), 4)},
            "point_count": points,
            "policy_id": "anchored-grid-v1",
        }
from decision_workbench.modeling.curve_grid import anchored_curve_grid
