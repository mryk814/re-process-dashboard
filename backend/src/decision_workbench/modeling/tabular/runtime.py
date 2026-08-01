"""Package-backed runtime inference for generic tabular regression tasks."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from decision_workbench.contracts.candidate_project_contracts import (
    Candidate,
    CandidateInput,
    TargetValue,
)
from decision_workbench.contracts.feature_contracts import (
    FeatureBundle,
    FeatureDefinition,
)
from decision_workbench.contracts.prediction_catalog_contracts import (
    Prediction,
    Support,
)
from decision_workbench.data.profiles.loading import load_task_definitions
from decision_workbench.domain.goal_targets import goal_fields
from decision_workbench.execution.inference_work_graph import semantic_digest
from decision_workbench.modeling.conformal_intervals import VerifiedConformalWrapper
from decision_workbench.modeling.curve_grid import (
    anchored_curve_grid,
    numeric_domain_grid,
)
from decision_workbench.modeling.missingness import (
    assess_input_missingness,
    require_operation_allowed,
)
from decision_workbench.modeling.package_capabilities import package_capability_matrix
from decision_workbench.modeling.packages.contracts import (
    FeaturePipelineDocument,
    PredictiveSummary,
    predictive_interval,
    validate_predictive_summary,
    validate_task_definition_canonical_inputs,
)
from decision_workbench.modeling.packages.loader import ModelPackageLoader
from decision_workbench.modeling.packages.ports import LoadedBatchPredictor
from decision_workbench.modeling.packages.verification import VerifiedModelPackage
from decision_workbench.modeling.training.feature_recipe import (
    canonical_recipe_inputs,
    load_feature_recipe_artifacts,
    transform_feature_recipe,
    validate_recipe_canonical_inputs,
)
from decision_workbench.tasks.task_registry import load_task_contracts
from decision_workbench.task_composition.ports import NumericSamplingPolicy

from .data import TabularData
from .features import (
    _get_path,
    _set_path,
    build_tabular_features,
    build_tabular_features_from_observation,
    candidate_from_observation,
    feature_definitions,
)


def _interval_method(summary: PredictiveSummary) -> str | None:
    """Use declared interval semantics, not merely the presence of quantiles."""
    if summary.prediction_interval is not None:
        return summary.prediction_interval.method
    if not summary.quantiles:
        return None
    if summary.distribution.get("family") in {"normal", "lognormal"}:
        return "parametric"
    return "quantile"


def _missing_input_paths(
    candidate: CandidateInput,
    policy_inputs: Sequence[Any],
) -> list[str]:
    return [
        item.path
        for item in policy_inputs
        if _get_path(candidate, item.path) in {None, ""}
    ]


def _with_imputation_note(message: str, missing_paths: Sequence[str]) -> str:
    if not missing_paths:
        return message
    return (
        message
        + "。未設定入力はProfile指定の補完値またはmissing categoryとmissing indicatorで"
        "比較しており、補完後の一致を実測一致とは扱いません"
    )


class TabularRegressionRuntime:
    support_policy_id = "tabular-row-knn-v1"
    missing_policy_inputs: tuple[Any, ...] = ()

    def __init__(
        self,
        data: TabularData,
        package_root: str | Path | VerifiedModelPackage,
        *,
        conformal_wrappers: Sequence[VerifiedConformalWrapper] = (),
        missing_policy_inputs: Sequence[Any] = (),
    ) -> None:
        self.data = data
        self.profile = data.profile
        self.missing_policy_inputs = tuple(missing_policy_inputs)
        self.task_id = data.profile.task_id
        self.model_package = (
            package_root
            if isinstance(package_root, VerifiedModelPackage)
            else ModelPackageLoader().load(package_root)
        )
        manifest = self.model_package.manifest
        self.task_definition = load_task_definitions()[self.task_id]
        self.output_definitions = {
            item.key: item for item in self.task_definition.outputs
        }
        self.runtime_capabilities = {
            item.target: item
            for item in load_task_contracts()[
                self.task_id
            ].runtime_capability.targets
        }
        validate_task_definition_canonical_inputs(self.task_definition, manifest)
        if manifest.task_id != self.task_id:
            raise ValueError(f"Model package task {manifest.task_id} is incompatible with {self.task_id}")
        pipeline_document = FeaturePipelineDocument.model_validate_json(
            self.model_package.artifact_path(
                manifest.feature_pipeline.spec
            ).read_text(encoding="utf-8")
        )
        self.feature_recipe = None
        self.feature_recipe_state = None
        if pipeline_document.feature_recipe is not None:
            recipe_ref = pipeline_document.feature_recipe
            self.feature_recipe, self.feature_recipe_state = (
                load_feature_recipe_artifacts(
                    self.model_package.artifact_path(recipe_ref.recipe),
                    self.model_package.artifact_path(recipe_ref.state),
                )
            )
            validate_recipe_canonical_inputs(
                self.feature_recipe,
                manifest.feature_pipeline.canonical_input_paths,
            )
        expected_features = (
            tuple(item.name for item in self.feature_recipe.features)
            if self.feature_recipe is not None
            else tuple(item.name for item in feature_definitions(self.profile))
        )
        if tuple(manifest.feature_pipeline.output_features) != expected_features:
            raise ValueError("Tabular model package feature order is incompatible")
        self.predictors = {
            spec.target: self.model_package.load_predictor(spec.id)
            for spec in manifest.predictors
        }
        self.capability_matrix = package_capability_matrix(
            manifest,
            load_task_contracts()[self.task_id].runtime_capability,
            manifest_digest=self.model_package.manifest_sha256,
        )
        wrapped_targets: set[str] = set()
        for wrapper in conformal_wrappers:
            if wrapper.base_package.manifest_sha256 != self.model_package.manifest_sha256:
                raise ValueError("conformal wrapper base Package does not match runtime Package")
            if wrapper.manifest.target not in self.predictors:
                raise ValueError("conformal wrapper target is not provided by runtime Package")
            if wrapper.manifest.target in wrapped_targets:
                raise ValueError("multiple conformal wrappers target the same runtime output")
            self.predictors[wrapper.manifest.target] = wrapper.load_predictor()
            self.capability_matrix = wrapper.apply_capability(self.capability_matrix)
            wrapped_targets.add(wrapper.manifest.target)
        stats_path = next(path for path in manifest.feature_pipeline.artifacts if path.endswith("training_stats.json"))
        self.training_stats = json.loads(
            self.model_package.artifact_path(stats_path).read_text(encoding="utf-8")
        )
        missing_policy = self.training_stats.get("missing_policy", {})
        self.imputation_values = {
            str(path): float(value)
            for path, value in missing_policy.get("imputation_values", {}).items()
        }
        if missing_policy and missing_policy.get("digest") != semantic_digest(
            self.imputation_values
        ):
            raise ValueError("Package missing-policy artifact digest is incompatible")
        required_imputation = {
            item.path
            for item in self.missing_policy_inputs
            if item.numeric_missing.strategy == "training_median_with_indicator"
        }
        if (
            self.feature_recipe is None
            and set(self.imputation_values) != required_imputation
        ):
            raise ValueError("Package missing-policy artifact does not match Profile")
        self._verify_smoke()
        self._build_support_reference()

    def _recipe_inputs(self, candidate: CandidateInput) -> dict[str, Any]:
        assert self.feature_recipe is not None
        return canonical_recipe_inputs(candidate, self.feature_recipe)

    def _feature_bundle(self, candidate: CandidateInput) -> FeatureBundle:
        if self.feature_recipe is None:
            return build_tabular_features(
                candidate,
                self.profile,
                self.imputation_values,
            )
        assert self.feature_recipe_state is not None
        values = transform_feature_recipe(
            self.feature_recipe,
            self.feature_recipe_state,
            [self._recipe_inputs(candidate)],
        )[0]
        return FeatureBundle(
            pipeline_id=self.feature_recipe.id,
            pipeline_version=self.feature_recipe.version,
            definitions=tuple(
                FeatureDefinition(
                    item.name,
                    item.unit,
                    item.meaning,
                    item.group,
                )
                for item in self.feature_recipe.features
            ),
            values=values,
        )

    def _observation_feature_bundle(self, row: dict[str, Any]) -> FeatureBundle:
        if self.feature_recipe is None:
            return build_tabular_features_from_observation(
                row,
                self.imputation_values,
                self.profile,
            )
        return self._feature_bundle(
            candidate_from_observation(
                row,
                self.profile,
                preserve_normalized_missing=True,
            )
        )

    @property
    def output_keys(self) -> frozenset[str]:
        return frozenset(self.predictors)

    @property
    def supports_batch_prediction(self) -> bool:
        return all(
            isinstance(predictor, LoadedBatchPredictor)
            for predictor in self.predictors.values()
        )

    @property
    def chain_sampling_method(self) -> str:
        from decision_workbench.modeling.stage_sampling import (
            sampling_capability_for_package,
        )

        capability = sampling_capability_for_package(self.model_package)
        return capability.method if capability is not None else ""

    @property
    def chain_sample_bounds(self) -> dict[str, tuple[float | None, float | None]]:
        from decision_workbench.modeling.stage_sampling import (
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
        from decision_workbench.contracts.chain_uncertainty_contracts import (
            StageSampleResult,
        )
        method = self.chain_sampling_method
        if not method:
            raise ValueError("このruntime/packageはStage sampleを提供しません")
        values = self._feature_bundle(candidate).as_dict()
        targets = sorted(self.predictors)
        seeds = np.random.SeedSequence(seed).spawn(len(targets))
        outputs = {
            target: [
                float(value)
                for value in self.predictors[target].sample(
                    values,
                    sample_count=sample_count,
                    seed=int(stage_seed.generate_state(1)[0]),
                )
            ]
            for target, stage_seed in zip(targets, seeds, strict=True)
        }
        return StageSampleResult(
            method=method,
            sample_count=sample_count,
            outputs=outputs,
            reference_points={
                target: float(self.predictors[target].predict(values).point_estimate)
                for target in targets
            },
        )

    def _verify_smoke(self) -> None:
        smoke = self.model_package.manifest.smoke_test
        if smoke is None:
            raise ValueError("Tabular model package must declare a smoke test")
        candidate = CandidateInput.model_validate_json(
            self.model_package.artifact_path(smoke.input).read_text(encoding="utf-8")
        )
        expected = json.loads(
            self.model_package.artifact_path(smoke.expected).read_text(encoding="utf-8")
        )
        values = self._feature_bundle(candidate).as_dict()
        specs = {item.target: item for item in self.model_package.manifest.predictors}
        for target, predictor in self.predictors.items():
            summary = predictor.predict(values)
            validate_predictive_summary(
                summary,
                specs[target],
                self.runtime_capabilities[target],
            )
            if not np.isclose(summary.point_estimate, expected[target], rtol=1e-7, atol=1e-7):
                raise ValueError("Tabular model package smoke prediction is not reproducible")

    def _build_support_reference(self) -> None:
        self.support_references: dict[str, dict[str, Any]] = {}
        for target in self.output_keys:
            eligible = [
                row for row in self.data.observations
                if row["eligible"] and target in row["outputs"]
            ]
            raw = np.vstack([
                self._observation_feature_bundle(row).values
                for row in eligible
            ])
            reference_mean = raw.mean(axis=0)
            reference_scale = raw.std(axis=0)
            reference_scale[reference_scale < 1e-9] = 1.0
            reference_vectors = (raw - reference_mean) / reference_scale
            if len(raw) > 1:
                sample = reference_vectors
                sample_groups = np.asarray([str(row["parent_key"]) for row in eligible])
            # Support calibration is only a robust distance scale estimate.
            # A deterministic 500-row sample avoids an O(n²d) startup allocation
            # (the wear example has more than 14k reference observations).
                if len(sample) > 500:
                    sample_indexes = np.linspace(0, len(sample) - 1, 500, dtype=int)
                    sample = sample[sample_indexes]
                    sample_groups = sample_groups[sample_indexes]
                distances = np.sqrt(
                    ((sample[:, None, :] - sample[None, :, :]) ** 2).mean(axis=2)
                )
                distances[sample_groups[:, None] == sample_groups[None, :]] = np.inf
                loo_nearest = distances.min(axis=1)
            else:
                loo_nearest = np.asarray([0.0])
            supported_threshold, caution_threshold = (
                float(value)
                for value in np.quantile(loo_nearest, (0.80, 0.95))
            )
            self.support_references[target] = {
                "rows": eligible,
                "mean": reference_mean,
                "scale": reference_scale,
                "vectors": reference_vectors,
                "loo_nearest": loo_nearest,
                "supported_threshold": supported_threshold,
                "caution_threshold": caution_threshold,
            }

    def _support(
        self,
        candidate: CandidateInput,
        target: str,
        include_similarity: bool,
    ) -> tuple[Support, list[dict[str, Any]]]:
        reference = self.support_references[target]
        vector = self._feature_bundle(candidate).values
        candidate_missing = _missing_input_paths(
            candidate,
            self.missing_policy_inputs,
        )
        normalized = (vector - reference["mean"]) / reference["scale"]
        distances = np.sqrt(((reference["vectors"] - normalized) ** 2).mean(axis=1))
        nearest = float(distances.min())
        loo_nearest = reference["loo_nearest"]
        supported = float(reference["supported_threshold"])
        caution = float(reference["caution_threshold"])
        if nearest <= supported:
            status, message = "supported", "近い学習条件に実測があります"
        elif nearest <= caution:
            status, message = "caution", "近傍はありますが、学習条件の密度が低い領域です"
        else:
            status, message = "extrapolated", "学習条件から外れています。予測は探索的な参考です"
        message = _with_imputation_note(message, candidate_missing)
        similar: list[dict[str, Any]] = []
        if include_similarity:
            used_groups: set[str] = set()
            for index in np.argsort(distances):
                row = reference["rows"][int(index)]
                parent_key = str(row["parent_key"])
                if self.profile.group_column and parent_key in used_groups:
                    continue
                used_groups.add(parent_key)
                similar.append({
                    "observation_id": row["id"],
                    "observation_ids": [row["id"]],
                    "parent_key": row["parent_key"],
                    "source": self.data.profile.name,
                    "distance": round(float(distances[index]), 4),
                    "outputs": {key: round(float(value), 4) for key, value in row["outputs"].items()},
                })
                if len(similar) == 6:
                    break
        return Support(
            status=status,
            distance=round(nearest, 4),
            percentile=round(float((loo_nearest <= nearest).mean() * 100), 1),
            message=message,
            components={
                "all_inputs": round(nearest, 4),
                "imputed_inputs": float(len(candidate_missing)),
            },
            reference_count=len(reference["rows"]),
            supported_threshold=round(supported, 4),
            caution_threshold=round(caution, 4),
        ), similar

    def _support_batch(
        self,
        feature_rows: Sequence[FeatureBundle],
        candidates: Sequence[CandidateInput],
        target: str,
    ) -> list[Support]:
        if not feature_rows:
            return []
        reference = self.support_references[target]
        raw = np.vstack([item.values for item in feature_rows])
        normalized = (raw - reference["mean"]) / reference["scale"]
        reference_vectors = reference["vectors"]
        dimension = max(reference_vectors.shape[1], 1)
        reference_squared = np.einsum(
            "ij,ij->i", reference_vectors, reference_vectors
        )
        loo_nearest = reference["loo_nearest"]
        supported = float(reference["supported_threshold"])
        caution = float(reference["caution_threshold"])
        results: list[Support] = []
        for start in range(0, len(normalized), 128):
            query = normalized[start : start + 128]
            query_squared = np.einsum("ij,ij->i", query, query)
            squared = (
                query_squared[:, None]
                + reference_squared[None, :]
                - 2.0 * query @ reference_vectors.T
            ) / dimension
            nearest_indices = np.argmin(squared, axis=1)
            nearest_values = np.sqrt(
                np.mean(
                    (
                        reference_vectors[nearest_indices]
                        - query
                    )
                    ** 2,
                    axis=1,
                )
            )
            for offset, nearest_value in enumerate(nearest_values):
                candidate_index = start + offset
                candidate_missing = _missing_input_paths(
                    candidates[candidate_index],
                    self.missing_policy_inputs,
                )
                nearest = float(nearest_value)
                if nearest <= supported:
                    status, message = "supported", "近い学習条件に実測があります"
                elif nearest <= caution:
                    status, message = (
                        "caution",
                        "近傍はありますが、学習条件の密度が低い領域です",
                    )
                else:
                    status, message = (
                        "extrapolated",
                        "学習条件から外れています。予測は探索的な参考です",
                    )
                message = _with_imputation_note(message, candidate_missing)
                results.append(
                    Support(
                        status=status,
                        distance=round(nearest, 4),
                        percentile=round(
                            float((loo_nearest <= nearest).mean() * 100),
                            1,
                        ),
                        message=message,
                        components={
                            "all_inputs": round(nearest, 4),
                            "imputed_inputs": float(len(candidate_missing)),
                        },
                        reference_count=len(reference["rows"]),
                        supported_threshold=round(supported, 4),
                        caution_threshold=round(caution, 4),
                    )
                )
        return results

    def evidence(self, candidate: Candidate) -> tuple[Support, list[dict[str, Any]]]:
        target = self.profile.outputs[0].key
        return self._support(candidate, target, True)

    def support_summary(self, candidate: Candidate) -> Support:
        target = self.profile.outputs[0].key
        return self._support(candidate, target, False)[0]

    def support_by_target(self, candidate: Candidate) -> dict[str, Support]:
        return {
            target: self._support(candidate, target, False)[0]
            for target in self.output_keys
        }

    def similarity(
        self,
        candidate: Candidate,
        limit: int = 6,
        target: str | None = None,
    ) -> list[dict[str, Any]]:
        selected_target = target or self.profile.outputs[0].key
        if selected_target not in self.output_keys:
            raise ValueError(f"unknown similarity target: {selected_target}")
        return self._support(candidate, selected_target, True)[1][:limit]

    def predict_core(
        self,
        candidate: Candidate,
        detailed: bool = False,
        target_values: dict[str, TargetValue] | None = None,
        _prepared_values: dict[str, float] | None = None,
        _summaries: dict[str, PredictiveSummary] | None = None,
        _missingness_operation: str | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        missingness = assess_input_missingness(
            candidate,
            self.missing_policy_inputs,
            self.training_stats,
            operation=(
                _missingness_operation  # type: ignore[arg-type]
                or ("detailed_prediction" if detailed else "preview")
            ),
        )
        require_operation_allowed(missingness)
        values = _prepared_values or self._feature_bundle(candidate).as_dict()
        predictions: dict[str, Prediction] = {}
        warnings: list[str] = []
        for target, predictor in self.predictors.items():
            summary = (
                _summaries[target]
                if _summaries is not None
                else predictor.predict(values)
            )
            lower, upper = predictive_interval(summary)
            output_profile = next(item for item in self.profile.outputs if item.key == target)
            point_estimate = summary.point_estimate
            quantiles = {
                level: float(value)
                for level, value in summary.quantiles.items()
            }
            if output_profile.lower_bound is not None:
                point_estimate = max(output_profile.lower_bound, point_estimate)
                lower = max(output_profile.lower_bound, lower)
                upper = max(output_profile.lower_bound, upper)
                quantiles = {
                    level: max(output_profile.lower_bound, value)
                    for level, value in quantiles.items()
                }
            if output_profile.upper_bound is not None:
                point_estimate = min(output_profile.upper_bound, point_estimate)
                lower = min(output_profile.upper_bound, lower)
                upper = min(output_profile.upper_bound, upper)
                quantiles = {
                    level: min(output_profile.upper_bound, value)
                    for level, value in quantiles.items()
                }
            goal = (target_values or {}).get(target)
            goal_probability = None
            goal_value, goal_lower, goal_upper, goal_direction = goal_fields(
                goal, self.output_definitions[target].goal_direction
            )
            interval = summary.prediction_interval
            calibration = interval.calibration if interval is not None else None
            wrapper_identity = interval.conformal_wrapper if interval is not None else None
            predictions[target] = Prediction(
                value=round(point_estimate, 4),
                lower=round(lower, 4),
                upper=round(upper, 4),
                unit=summary.unit,
                target_kind=summary.target_kind,
                point_statistic=summary.point_statistic,
                predictive_family=summary.distribution["family"],
                quantiles={level: round(value, 6) for level, value in quantiles.items()},
                interval_method=_interval_method(summary),
                interval_coverage_level=(interval.coverage_level if interval is not None else None),
                interval_calibration_dataset_digest=(
                    calibration.calibration_dataset_digest if calibration is not None else None
                ),
                interval_calibration_sample_count=(
                    calibration.calibration_sample_count if calibration is not None else None
                ),
                interval_wrapper_id=(
                    wrapper_identity.wrapper_id if wrapper_identity is not None else None
                ),
                interval_wrapper_version=(
                    wrapper_identity.wrapper_version if wrapper_identity is not None else None
                ),
                interval_wrapper_manifest_digest=(
                    wrapper_identity.manifest_digest if wrapper_identity is not None else None
                ),
                interval_calibration_score_artifact_digest=(
                    wrapper_identity.calibration_score_artifact_digest
                    if wrapper_identity is not None
                    else None
                ),
                goal_value=goal_value,
                goal_lower=goal_lower,
                goal_upper=goal_upper,
                goal_probability=None if goal_probability is None else round(goal_probability, 4),
                goal_direction=goal_direction,
            )
            warnings.extend(summary.warnings)
        runtime_types = sorted({
            item.runtime_type for item in self.model_package.manifest.predictors
        })
        uses_monotone_lightgbm = runtime_types == ["lightgbm.booster.v1"]
        uses_binary_lightgbm = any(
            item.target_kind == "binary"
            for item in self.model_package.manifest.predictors
        )
        uses_conformal_interval = any(
            item.interval_method == "conformal" for item in predictions.values()
        )
        base_model_method = (
            "calibrated gradient-boosted binary classifier with stratified validation"
            if uses_binary_lightgbm
            else "monotonic gradient-boosted trees with grouped validation"
            if uses_monotone_lightgbm
            else (
                "regularized regression with grouped validation"
                if self.profile.group_column
                else "regularized regression with row-wise validation"
            )
        )
        base_interval_method = (
            "point probability with out-of-fold Platt calibration; no probability interval"
            if uses_binary_lightgbm
            else "grouped out-of-fold calibrated normal interval"
            if uses_monotone_lightgbm
            else (
                "grouped out-of-fold residual quantiles"
                if self.profile.group_column
                else "row-wise out-of-fold residual quantiles"
            )
        )
        pipeline = self.model_package.manifest.feature_pipeline
        pipeline_paths = {pipeline.spec, *pipeline.artifacts}
        pipeline_digest = semantic_digest(
            {
                "specification": pipeline.model_dump(mode="json"),
                "artifacts": {
                    item.path: item.sha256
                    for item in self.model_package.manifest.artifacts
                    if item.path in pipeline_paths
                },
            }
        )
        package_provenance = self.model_package.manifest.provenance
        if missingness.prediction_status == "provisional":
            warnings.append(
                "補完を含む暫定予測です。欠損値のばらつきは予測区間へ追加していません"
            )
        return {
            "task_id": self.task_id,
            "candidate_id": candidate.id,
            "mode": "detailed" if detailed else "preview",
            "predictions": predictions,
            "warnings": warnings,
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
                    "id": self.model_package.manifest.package_id,
                    "version": self.model_package.manifest.package_version,
                    "method": (
                        f"{base_model_method}; split conformal prediction interval"
                        if uses_conformal_interval
                        else base_model_method
                    ),
                },
                "package": {
                    "id": self.model_package.manifest.package_id,
                    "version": self.model_package.manifest.package_version,
                    "manifest_sha256": self.model_package.manifest_sha256,
                    "runtime_types": runtime_types,
                },
                "feature_pipeline": {
                    "id": pipeline.id,
                    "version": pipeline.version,
                    "digest": pipeline_digest,
                    "input_schema_version": "canonical-candidate/v1",
                    "features": list(values),
                },
                "training_data": {
                    "source_path": self.data.source_path,
                    "source_sha256": self.data.source_sha256,
                    "training_data_id": package_provenance.training_data_id,
                    "feature_dataset_id": package_provenance.feature_dataset_id,
                    "training_code_revision": (
                        package_provenance.training_code_revision
                    ),
                    "records": self.training_stats["records"],
                    **(
                        {
                            "dataset_profile_id": self.data.profile_id,
                            **self.training_stats["training_contract"],
                        }
                        if "training_contract" in self.training_stats
                        else {}
                    ),
                },
                **(
                    {
                        "source_lifecycle": (
                            self.model_package.manifest.provenance.source_lifecycle.model_dump(
                                mode="json"
                            )
                        )
                    }
                    if self.model_package.manifest.provenance.source_lifecycle
                    is not None
                    else {}
                ),
                "prediction_interval": {
                    "method": "split conformal prediction interval" if uses_conformal_interval else base_interval_method,
                    "coverage": (
                        {
                            target: item.interval_coverage_level
                            for target, item in predictions.items()
                            if item.interval_method == "conformal"
                        }
                        if uses_conformal_interval
                        else
                        "not reported for point probabilities"
                        if uses_binary_lightgbm
                        else "central 90% empirical interval"
                    ),
                    "grouping": (
                        "stratified independent source rows"
                        if uses_binary_lightgbm
                        else self.profile.group_column or "independent source row"
                    ),
                    **(
                        {
                            "calibration": {
                                target: {
                                    "dataset_digest": item.interval_calibration_dataset_digest,
                                    "sample_count": item.interval_calibration_sample_count,
                                    "wrapper": {
                                        "id": item.interval_wrapper_id,
                                        "version": item.interval_wrapper_version,
                                        "manifest_digest": item.interval_wrapper_manifest_digest,
                                        "calibration_score_artifact_digest": (
                                            item.interval_calibration_score_artifact_digest
                                        ),
                                    },
                                }
                                for target, item in predictions.items()
                                if item.interval_method == "conformal"
                            }
                        }
                        if uses_conformal_interval
                        else {}
                    ),
                },
                "similarity": {
                    "version": self.support_policy_id,
                    "method": "nearest row in standardized feature space",
                },
            },
            "heat_pattern": [],
            "response_curve": None,
            "input_completeness": missingness.input_completeness,
            "prediction_status": missingness.prediction_status,
            "input_missingness": missingness,
        }

    def predict(self, candidate: Candidate, detailed: bool = False, **kwargs: Any) -> dict[str, Any]:
        kwargs.pop("include_curve", None)
        result = self.predict_core(candidate, detailed=detailed, **kwargs)
        support, similar = self.evidence(candidate)
        result["support"] = support
        result["similar"] = similar
        if support.status != "supported":
            result["warnings"].append(support.message)
        return result

    def predict_batch(
        self,
        candidates: Sequence[Candidate],
        detailed: bool = False,
        target_values: dict[str, TargetValue] | None = None,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        kwargs.pop("include_curve", None)
        missingness_operation = kwargs.pop("_missingness_operation", None)
        if not candidates:
            return []
        if not self.supports_batch_prediction:
            raise ValueError(
                "このruntimeのpredictorはnative batch predictionを提供しません"
            )
        feature_rows = [
            self._feature_bundle(candidate)
            for candidate in candidates
        ]
        value_rows = [item.as_dict() for item in feature_rows]
        summaries_by_target: dict[str, list[PredictiveSummary]] = {}
        for target, predictor in self.predictors.items():
            summaries = (
                predictor.predict_batch(value_rows)
                if isinstance(predictor, LoadedBatchPredictor)
                else [predictor.predict(values) for values in value_rows]
            )
            if len(summaries) != len(candidates):
                raise ValueError(
                    f"predictor {target} did not preserve batch cardinality"
                )
            summaries_by_target[target] = summaries
        support_rows = self._support_batch(
            feature_rows,
            candidates,
            self.profile.outputs[0].key,
        )
        results: list[dict[str, Any]] = []
        for index, candidate in enumerate(candidates):
            result = self.predict_core(
                candidate,
                detailed=detailed,
                target_values=target_values,
                _prepared_values=value_rows[index],
                _summaries={
                    target: summaries[index]
                    for target, summaries in summaries_by_target.items()
                },
                _missingness_operation=missingness_operation,
            )
            support = support_rows[index]
            result["support"] = support
            result["similar"] = []
            if support.status != "supported":
                result["warnings"].append(support.message)
            results.append(result)
        return results

    def training_range_for(
        self,
        target: str,
        variable: str,
        *,
        stage_name: str | None = None,
        stage_position_m: float | None = None,
    ) -> tuple[float, float]:
        del stage_name, stage_position_m
        if target not in self.predictors:
            raise ValueError(f"Unsupported response-curve target: {target}")
        item = next(
            (
                item
                for item in self.profile.inputs
                if item.path == variable and item.kind == "number"
            ),
            None,
        )
        if item is None:
            raise ValueError(f"この予測タスクで応答曲線にできない変数です: {variable}")
        values = [
            float(
                (
                    row["composition"]
                    if variable.startswith("composition.")
                    else row["features"]
                )[variable.split(".", 1)[1]]
            )
            for row in self.support_references[target]["rows"]
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
        sampling_policy: NumericSamplingPolicy | None = None,
    ) -> dict[str, Any]:
        if target not in self.predictors:
            raise ValueError(f"Unsupported response-curve target: {target}")
        item = next((item for item in self.profile.inputs if item.path == variable and item.kind == "number"), None)
        if item is None:
            raise ValueError(f"この予測タスクで応答曲線にできない変数です: {variable}")
        reference_rows = self.support_references[target]["rows"]
        current = float(_get_path(candidate, variable))
        training_min, training_max = self.training_range_for(target, variable)
        low, high = training_min, training_max
        if axis_range is not None:
            low, high = axis_range
        curve = []
        for x_value in anchored_curve_grid(
            low,
            high,
            points,
            current=current,
            policy=sampling_policy,
        ):
            adjusted = candidate.model_copy(deep=True)
            _set_path(adjusted, variable, float(x_value))
            summary = self.predictors[target].predict(
                self._feature_bundle(adjusted).as_dict()
            )
            lower, upper = predictive_interval(summary)
            output_profile = next(item for item in self.profile.outputs if item.key == target)
            value = summary.point_estimate
            if output_profile.lower_bound is not None:
                value = max(output_profile.lower_bound, value)
                lower = max(output_profile.lower_bound, lower)
                upper = max(output_profile.lower_bound, upper)
            if output_profile.upper_bound is not None:
                value = min(output_profile.upper_bound, value)
                lower = min(output_profile.upper_bound, lower)
                upper = min(output_profile.upper_bound, upper)
            curve.append({
                "x": round(float(x_value), 5),
                "value": round(value, 5),
                "lower": round(lower, 5),
                "upper": round(upper, 5),
                "target_kind": summary.target_kind,
                "point_statistic": summary.point_statistic,
                "predictive_family": summary.distribution.get("family", "empirical_quantiles"),
                "quantiles": (
                    {}
                    if summary.target_kind == "binary"
                    else {
                        "0.05": round(lower, 5),
                        "0.95": round(upper, 5),
                    }
                ),
            })
        field = next(
            field
            for group in self.task_definition.input_groups
            for field in group.fields
            if field.path == variable
        )
        observed = [float(row["outputs"][target]) for row in reference_rows]
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

    def curve_family_result(
        self,
        candidate: Candidate,
        target: str,
        vary_variable: str | None,
        levels: int,
        points: int,
        sampling_policy: NumericSamplingPolicy | None = None,
    ) -> dict[str, Any]:
        axis = self.profile.curve_axis_path
        if axis is None:
            raise ValueError("この予測タスクには曲線軸がありません")
        axis_result = self.response_curve_result(
            candidate,
            target,
            axis,
            points,
            sampling_policy=sampling_policy,
        )
        series: list[dict[str, Any]] = []
        vary_meta: dict[str, Any] | None = None
        vary_categorical: dict[str, Any] | None = None
        if not vary_variable:
            series.append({"level": None, "label": "現在の候補", "points": axis_result["points"]})
        else:
            item = next((item for item in self.profile.inputs if item.path == vary_variable), None)
            if item is None or vary_variable == axis:
                raise ValueError("曲線の水準にできない変数です")
            if item.kind == "categorical":
                current = str(_get_path(candidate, vary_variable))
                vary_categorical = {
                    "id": vary_variable,
                    "label": vary_variable.split(".", 1)[1],
                    "choices": list(item.choices),
                    "current": current,
                }
                for choice in item.choices:
                    adjusted = candidate.model_copy(deep=True)
                    adjusted.inputs.categorical[vary_variable.split(".", 1)[1]] = choice
                    curve = self.response_curve_result(
                        adjusted,
                        target,
                        axis,
                        points,
                        sampling_policy=sampling_policy,
                    )
                    series.append({"level": choice, "label": choice, "points": curve["points"]})
            else:
                task_field = next(
                    field
                    for group in self.task_definition.input_groups
                    for field in group.fields
                    if field.path == vary_variable
                )
                training = [
                    float((row["composition"] if vary_variable.startswith("composition.") else row["features"])[vary_variable.split(".", 1)[1]])
                    for row in self.support_references[target]["rows"]
                ]
                low, high = min(training), max(training)
                vary_meta = {
                    "id": vary_variable,
                    "label": vary_variable.split(".", 1)[1],
                    "unit": item.unit,
                    "min": low,
                    "max": high,
                    "current": float(_get_path(candidate, vary_variable)),
                }
                for level in numeric_domain_grid(
                    low,
                    high,
                    levels,
                    field=task_field,
                ):
                    adjusted = candidate.model_copy(deep=True)
                    _set_path(adjusted, vary_variable, float(level))
                    curve = self.response_curve_result(
                        adjusted,
                        target,
                        axis,
                        points,
                        sampling_policy=sampling_policy,
                    )
                    unit = f" {item.unit}" if item.unit else ""
                    series.append({
                        "level": round(float(level), 5),
                        "label": f"{vary_meta['label']} {float(level):g}{unit}",
                        "points": curve["points"],
                    })
        return {
            "target": target,
            "axis": axis_result["variable"],
            "vary": vary_meta,
            "vary_categorical": vary_categorical,
            "series": series,
            "output_range": axis_result["output_range"],
            "point_count": points,
            "policy_id": "anchored-axis-grid-v1",
        }
