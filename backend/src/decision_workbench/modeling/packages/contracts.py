"""Safe, data-only model package contracts and a fixed adapter registry.

Packages identify one of the adapters compiled into this application.  They
never contain an import path, Python source, pickle/joblib object, or callback.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from decision_workbench.contracts.inference_policy_contracts import (
    InferenceDiagnostics,
    InferenceIdentity,
)
from decision_workbench.contracts.missingness_contracts import (
    MissingnessOperationCapability,
)
from decision_workbench.contracts.sampling_identity_contracts import SamplingIdentity
from decision_workbench.contracts.series_contracts import SeriesFeatureContract

if TYPE_CHECKING:
    from decision_workbench.contracts.task_contracts import (
        TargetRuntimeCapability,
        TaskDefinition,
    )


MAX_ARTIFACT_BYTES = 256 * 1024 * 1024
MAX_PACKAGE_BYTES = 512 * 1024 * 1024
MAX_PACKAGE_ARTIFACTS = 4096
SNAPSHOT_CHUNK_BYTES = 1024 * 1024
PACKAGE_SCHEMA_VERSION = "model-package/v1"
FEATURE_DATASET_DIGEST_LEGACY = "canonical-json/v1"
FEATURE_DATASET_DIGEST_FLOAT15 = "canonical-json-finite-float15/v2"
FeatureDatasetDigestAlgorithm = Literal[
    "canonical-json/v1",
    "canonical-json-finite-float15/v2",
]
PREDICTOR_RUNTIME_TYPES = {
    "builtin.linear.v1",
    "builtin.exact_gp.v1",
    "builtin.heteroscedastic_exact_gp.v1",
    "builtin.additive_terms.v1",
    "builtin.quantile_linear.v1",
    "builtin.posterior_linear.v1",
    "sklearn.skops.v1",
    "lightgbm.booster.v1",
    "gpytorch.static_exact_rbf.v1",
    "numpyro.dense_posterior.v1",
}
TRANSFORM_RUNTIME_TYPES = {
    "builtin.deterministic_linear.v1",
}
RUNTIME_TYPES = PREDICTOR_RUNTIME_TYPES | TRANSFORM_RUNTIME_TYPES
LIKELIHOOD_IDS = {
    "normal", "student_t", "lognormal", "bernoulli_logit", "poisson_log",
    "negative_binomial_log", "zero_inflated_poisson_log", "ordinal_logit",
}
LIKELIHOOD_TARGET_KINDS = {
    "normal": {"continuous", "continuous_positive"},
    "student_t": {"continuous", "continuous_positive"},
    "lognormal": {"continuous_positive"},
    "bernoulli_logit": {"binary"},
    "poisson_log": {"count"},
    "negative_binomial_log": {"count"},
    "zero_inflated_poisson_log": {"count"},
    "ordinal_logit": {"ordinal"},
}


class PackageContractError(ValueError):
    """A package is malformed, untrusted, or incompatible with this runtime."""


class MissingOptionalDependency(RuntimeError):
    """The package is valid, but this installed Edition lacks its adapter dependency."""


class PackageModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ArtifactSpec(PackageModel):
    path: str
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    bytes: Annotated[int, Field(gt=0, le=MAX_ARTIFACT_BYTES)]
    media_type: str = "application/octet-stream"

    @field_validator("path")
    @classmethod
    def relative_file_path(cls, value: str) -> str:
        path = Path(value)
        if not value or path.is_absolute() or ".." in path.parts or path.name != value.split("/")[-1]:
            raise ValueError("artifact path must be a package-relative file path")
        return value.replace("\\", "/")


class FeaturePipelineSpec(PackageModel):
    id: str
    version: str
    spec: str
    canonical_input_paths: Annotated[tuple[str, ...], Field(min_length=1)]
    output_features: Annotated[tuple[str, ...], Field(min_length=1)]
    artifacts: tuple[str, ...] = ()

    @field_validator("canonical_input_paths")
    @classmethod
    def unique_canonical_input_paths(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not path for path in value):
            raise ValueError("canonical input paths must not be empty")
        if len(value) != len(set(value)):
            raise ValueError("canonical input paths must be unique")
        return value

    @field_validator("output_features")
    @classmethod
    def unique_output_features(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not name for name in value) or len(value) != len(set(value)):
            raise ValueError("output feature names must be unique and non-empty")
        return value


class PipelineFeatureSpec(PackageModel):
    name: Annotated[str, Field(min_length=1)]
    unit: str
    meaning: str
    group: Literal["composition", "process", "categorical", "metallurgy", "heat_pattern", "other"]


class PipelineMissingPolicySpec(PackageModel):
    imputation_values: dict[str, Annotated[float, Field(allow_inf_nan=False)]]
    digest: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    missing_by_input: dict[str, Annotated[int, Field(ge=0)]] = Field(
        default_factory=dict
    )
    policy_by_input: dict[str, dict[str, Any]] = Field(default_factory=dict)
    evaluation_coverage_by_target: dict[
        str,
        Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)],
    ] = Field(default_factory=dict)
    operation_capability: MissingnessOperationCapability | None = None


class FeatureRecipeArtifactSpec(PackageModel):
    schema_version: Literal["feature-recipe-artifacts/v1"] = (
        "feature-recipe-artifacts/v1"
    )
    recipe: str
    recipe_digest: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    state: str
    state_digest: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]

    @field_validator("recipe", "state")
    @classmethod
    def package_relative_file(cls, value: str) -> str:
        path = Path(value)
        if not value or path.is_absolute() or ".." in path.parts:
            raise ValueError("feature recipe artifact path must be package-relative")
        return value.replace("\\", "/")


class FeaturePipelineDocument(PackageModel):
    """Common contract carried by every task-specific pipeline document."""

    id: str
    version: str
    canonical_input_paths: Annotated[tuple[str, ...], Field(min_length=1)]
    features: Annotated[tuple[PipelineFeatureSpec, ...], Field(min_length=1)]
    missing_composition: str | None = None
    heat_interpolation: str | None = None
    series_representations: tuple[SeriesFeatureContract, ...] = ()
    missing_policy: PipelineMissingPolicySpec | None = None
    feature_recipe: FeatureRecipeArtifactSpec | None = None

    @field_validator("canonical_input_paths")
    @classmethod
    def unique_canonical_input_paths(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not path for path in value):
            raise ValueError("canonical input paths must not be empty")
        if len(value) != len(set(value)):
            raise ValueError("canonical input paths must be unique")
        return value

    @model_validator(mode="after")
    def unique_output_features(self) -> FeaturePipelineDocument:
        names = [feature.name for feature in self.features]
        if len(names) != len(set(names)):
            raise ValueError("pipeline output feature names must be unique")
        representation_ids = [
            item.representation_id for item in self.series_representations
        ]
        if len(representation_ids) != len(set(representation_ids)):
            raise ValueError("series representation ids must be unique")
        return self


class PredictorSpec(PackageModel):
    id: str
    target: str
    unit: str
    target_kind: Literal["continuous", "continuous_positive", "binary", "count", "ordinal"]
    runtime_type: str
    artifact: str
    predictive_family: str
    architecture_id: str | None = None
    feature_names: Annotated[tuple[str, ...], Field(min_length=1)]
    inference_identity: InferenceIdentity | None = None
    config: dict[str, Any] = Field(default_factory=dict)

    @field_validator("runtime_type")
    @classmethod
    def known_runtime(cls, value: str) -> str:
        if value not in PREDICTOR_RUNTIME_TYPES:
            raise ValueError(f"unsupported runtime_type: {value}")
        return value

    @field_validator("predictive_family")
    @classmethod
    def known_likelihood(cls, value: str) -> str:
        if value not in LIKELIHOOD_IDS and value != "empirical_quantiles":
            raise ValueError(f"unsupported predictive_family: {value}")
        return value

    @field_validator("feature_names")
    @classmethod
    def unique_feature_names(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not name for name in value) or len(value) != len(set(value)):
            raise ValueError("predictor feature names must be unique and non-empty")
        return value

    @model_validator(mode="after")
    def static_architecture_only(self) -> PredictorSpec:
        if self.inference_identity is not None:
            from decision_workbench.modeling.inference_policy import (
                inference_policy_by_identity,
            )

            try:
                policy = inference_policy_by_identity(
                    self.inference_identity.algorithm_id,
                    self.inference_identity.algorithm_version,
                    self.inference_identity.policy_digest,
                )
            except KeyError as exc:
                raise ValueError(
                    "predictor inference identity does not match a historical "
                    "allow-listed policy"
                ) from exc
            allowed_algorithms = {
                ("builtin.additive_terms.v1", "additive_terms_v1"): {
                    "analytic-gaussian",
                },
                ("builtin.posterior_linear.v1", "posterior_linear_v1"): {
                    "analytic-gaussian",
                    "nuts",
                    "gibbs",
                    "smc-particle",
                    "bounded-variational",
                },
                (
                    "builtin.posterior_linear.v1",
                    "hierarchical_parent_random_intercept_v1",
                ): {
                    "analytic-gaussian",
                    "nuts",
                    "gibbs",
                    "bounded-variational",
                },
                ("numpyro.dense_posterior.v1", "dense_mlp_v1"): {
                    "nuts",
                    "gibbs",
                    "smc-particle",
                    "bounded-variational",
                },
            }.get((self.runtime_type, self.architecture_id), set())
            if self.inference_identity.algorithm_id not in allowed_algorithms:
                raise ValueError(
                    "predictor runtime and architecture do not support the "
                    "declared inference algorithm"
                )
            if (
                policy.role == "approximation"
                and not set(policy.limitations).issubset(
                    self.inference_identity.approximation_limitations
                )
            ):
                raise ValueError(
                    "predictor approximation identity omits policy limitations"
                )
        if self.runtime_type == "numpyro.dense_posterior.v1" and self.architecture_id != "dense_mlp_v1":
            raise ValueError("numpyro adapter only permits architecture_id=dense_mlp_v1")
        if self.runtime_type == "gpytorch.static_exact_rbf.v1" and self.architecture_id != "exact_rbf_v1":
            raise ValueError("gpytorch adapter only permits architecture_id=exact_rbf_v1")
        if (
            self.runtime_type == "builtin.exact_gp.v1"
            and self.architecture_id not in {"exact_rbf_grouped_v1", "exact_rbf_ard_v1"}
        ):
            raise ValueError(
                "built-in exact GP adapter only permits "
                "architecture_id=exact_rbf_grouped_v1 or exact_rbf_ard_v1"
            )
        if (
            self.runtime_type == "builtin.heteroscedastic_exact_gp.v1"
            and self.architecture_id != "heteroscedastic_rbf_individual_v1"
        ):
            raise ValueError(
                "built-in heteroscedastic GP only permits architecture_id=heteroscedastic_rbf_individual_v1"
            )
        if self.runtime_type == "builtin.additive_terms.v1" and self.architecture_id != "additive_terms_v1":
            raise ValueError("built-in additive adapter only permits architecture_id=additive_terms_v1")
        if self.runtime_type == "builtin.quantile_linear.v1" and self.architecture_id != "quantile_linear_v1":
            raise ValueError("built-in quantile adapter only permits architecture_id=quantile_linear_v1")
        if self.runtime_type == "builtin.posterior_linear.v1" and self.architecture_id not in {
            "posterior_linear_v1",
            "hierarchical_parent_random_intercept_v1",
        }:
            raise ValueError("built-in posterior linear adapter received an unsupported architecture_id")
        return self


SourceLifecycleDigest = Annotated[
    str, Field(pattern=r"^sha256:[0-9a-f]{64}$")
]


class SourceLifecycleProvenance(PackageModel):
    connector_id: Annotated[str, Field(min_length=1)]
    connector_configuration_digest: SourceLifecycleDigest
    source_adapter_id: Annotated[str, Field(min_length=1)]
    source_adapter_version: Annotated[str, Field(min_length=1)]
    raw_snapshot_id: Annotated[str, Field(min_length=1)]
    raw_snapshot_digest: SourceLifecycleDigest
    recipe_id: Annotated[str, Field(min_length=1)]
    recipe_digest: SourceLifecycleDigest
    curation_run_id: Annotated[str, Field(min_length=1)]
    curation_digest: SourceLifecycleDigest
    profile_revision_id: Annotated[str, Field(min_length=1)]
    profile_digest: SourceLifecycleDigest
    canonical_dataset_revision_id: Annotated[str, Field(min_length=1)]
    canonical_dataset_digest: SourceLifecycleDigest
    training_snapshot_id: Annotated[str, Field(min_length=1)]
    training_snapshot_digest: SourceLifecycleDigest
    training_selection_policy_digest: SourceLifecycleDigest
    materialization_adapter_id: Annotated[str, Field(min_length=1)]
    materialization_adapter_version: Annotated[str, Field(min_length=1)]
    materialized_training_sha256: Annotated[
        str, Field(pattern=r"^[0-9a-f]{64}$")
    ]
    row_count: Annotated[int, Field(ge=1)]


def _parse_inference_provenance_recipe(
    recipe_id: str,
    recipe_parameters: dict[str, Any],
) -> Any:
    """Parse provenance through the standard training recipe authority.

    ``modeling.training.recipe`` owns the discriminated recipe schema.  This
    import is intentionally local: package contracts are imported by package
    loaders and must remain importable while the training module is being
    initialized.  Manifest validation happens after both modules are loaded,
    so the persisted recipe cannot bypass the typed recipe authority.
    """

    declared_recipe_id = recipe_parameters.get("estimator_id")
    if declared_recipe_id != recipe_id:
        raise ValueError(
            "inference provenance recipe_parameters.estimator_id does not "
            "match recipe_id"
        )
    from decision_workbench.modeling.training.recipe import estimator_recipe

    try:
        return estimator_recipe(recipe_id, recipe_parameters)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "inference provenance recipe_parameters are not a valid typed "
            f"recipe for {recipe_id}"
        ) from exc


class InferenceProvenanceSpec(PackageModel):
    """Strict predictor-scoped provenance for the effective inference run."""

    recipe_id: Annotated[str, Field(min_length=1)]
    recipe_parameters: dict[str, Any] = Field(default_factory=dict)
    inference_identity_digest: Annotated[
        str,
        Field(pattern=r"^sha256:[0-9a-f]{64}$"),
    ]
    diagnostics: InferenceDiagnostics

    @model_validator(mode="after")
    def recipe_parameters_match_recipe(self) -> InferenceProvenanceSpec:
        _parse_inference_provenance_recipe(
            self.recipe_id,
            self.recipe_parameters,
        )
        return self


class ProvenanceSpec(PackageModel):
    training_data_id: str
    feature_dataset_id: str
    feature_dataset_digest_algorithm: FeatureDatasetDigestAlgorithm = (
        FEATURE_DATASET_DIGEST_LEGACY
    )
    training_code_revision: str
    dataset_profile_id: str | None = None
    capacity: dict[str, Any] | None = None
    source_lifecycle: SourceLifecycleProvenance | None = None
    inference_identities: dict[str, InferenceIdentity] = Field(default_factory=dict)
    inference_provenance: dict[str, InferenceProvenanceSpec] = Field(
        default_factory=dict
    )

    @model_validator(mode="after")
    def lifecycle_matches_training_asset(self) -> ProvenanceSpec:
        lifecycle = self.source_lifecycle
        if lifecycle is None:
            return self
        if (
            self.training_data_id
            != f"sha256:{lifecycle.materialized_training_sha256}"
        ):
            raise ValueError(
                "source lifecycle materialization does not match training_data_id"
            )
        if self.dataset_profile_id is None:
            raise ValueError(
                "source lifecycle provenance requires dataset_profile_id"
            )
        if self.dataset_profile_id != lifecycle.profile_digest:
            raise ValueError(
                "source lifecycle profile does not match dataset_profile_id"
            )
        return self


def _validate_predictor_recipe_identity(
    predictor: PredictorSpec,
    provenance: InferenceProvenanceSpec,
) -> None:
    """Cross-check the typed recipe against the effective predictor identity."""

    recipe = _parse_inference_provenance_recipe(
        provenance.recipe_id,
        provenance.recipe_parameters,
    )
    training = predictor.config.get("training")
    if not isinstance(training, dict):
        raise ValueError(
            f"predictor {predictor.id} must declare config.training for "
            "inference provenance validation"
        )
    if training.get("estimator_id") != recipe.estimator_id:
        raise ValueError(
            f"predictor {predictor.id} config.training.estimator_id does not "
            "match inference provenance recipe"
        )

    recipe_parameterization = getattr(recipe, "parameterization", None)
    recipe_policy = getattr(recipe, "regularization_policy", None)
    training_parameters = training.get("parameters")
    if recipe_parameterization is not None:
        if predictor.inference_identity is None:
            raise ValueError(
                f"predictor {predictor.id} must declare inference_identity for "
                "a parameterized inference recipe"
            )
        if predictor.inference_identity.parameterization != recipe_parameterization:
            raise ValueError(
                f"predictor {predictor.id} inference identity parameterization "
                "does not match inference provenance recipe"
            )
        recipe_sampler = getattr(recipe, "sampler", None)
        if (
            recipe_sampler is not None
            and predictor.inference_identity.algorithm_id != recipe_sampler
        ):
            raise ValueError(
                f"predictor {predictor.id} inference algorithm does not match "
                "the recipe sampler"
            )
        for setting in ("chains", "warmup", "draws"):
            expected_setting = getattr(recipe, setting, None)
            if (
                expected_setting is not None
                and getattr(predictor.inference_identity, setting, None)
                != expected_setting
            ):
                raise ValueError(
                    f"predictor {predictor.id} inference identity {setting} "
                    "does not match inference provenance recipe"
                )
        expected_convergence_criteria = {
            "max_r_hat": recipe.max_r_hat,
            "min_ess": recipe.min_effective_sample_size,
            "max_divergences": recipe.max_divergences,
        }
        if (
            predictor.inference_identity.convergence_criteria
            != expected_convergence_criteria
        ):
            raise ValueError(
                f"predictor {predictor.id} inference convergence criteria do "
                "not match inference provenance recipe"
            )
        if (
            not isinstance(training_parameters, dict)
            or training_parameters.get("parameterization")
            != recipe_parameterization
        ):
            raise ValueError(
                f"predictor {predictor.id} training parameterization does not "
                "match inference provenance recipe"
            )
    if recipe_policy is not None and (
        not isinstance(training_parameters, dict)
        or training_parameters.get("regularization_policy") != recipe_policy
    ):
        raise ValueError(
            f"predictor {predictor.id} effective regularization policy does not "
            "match inference provenance recipe"
        )
    expected_training_parameters = recipe.model_dump(
        mode="json",
        exclude={
            "estimator_id",
            "validation_plan",
            "validation_plans_by_target",
        },
        exclude_none=True,
    )
    if training_parameters != expected_training_parameters:
        raise ValueError(
            f"predictor {predictor.id} training.parameters do not match the "
            "typed inference provenance recipe"
        )


class DeterministicTransformSpec(PackageModel):
    id: Annotated[str, Field(min_length=1)]
    runtime_type: str
    artifact: str
    compiler_id: Literal["sparse_blend_whole_wire.v1"]
    scientific_master_digest: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    output_names: Annotated[tuple[str, ...], Field(min_length=1)]
    output_unit: Literal["mass_percent_whole_wire"]
    auxiliary_feature_names: tuple[str, ...] = ()

    @field_validator("runtime_type")
    @classmethod
    def known_runtime(cls, value: str) -> str:
        if value not in TRANSFORM_RUNTIME_TYPES:
            raise ValueError(f"unsupported deterministic runtime_type: {value}")
        return value

    @field_validator("output_names", "auxiliary_feature_names")
    @classmethod
    def unique_names(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not name for name in value) or len(value) != len(set(value)):
            raise ValueError("deterministic transform names must be unique and non-empty")
        return value


class SmokeTestSpec(PackageModel):
    input: str
    expected: str

    @field_validator("input", "expected")
    @classmethod
    def package_relative_file(cls, value: str) -> str:
        path = Path(value)
        if not value or path.is_absolute() or ".." in path.parts:
            raise ValueError("smoke path must be package-relative")
        return value.replace("\\", "/")


class DeterministicGoldenSpec(PackageModel):
    path: str
    schema_version: Literal["stage-a-golden/v1"]
    expected_rows: Literal[120]

    @field_validator("path")
    @classmethod
    def package_relative_file(cls, value: str) -> str:
        path = Path(value)
        if not value or path.is_absolute() or ".." in path.parts:
            raise ValueError("golden path must be package-relative")
        return value.replace("\\", "/")


class ModelPackageManifest(PackageModel):
    schema_version: Literal[PACKAGE_SCHEMA_VERSION]
    package_id: str
    package_version: str
    task_id: str
    input_schema_version: str
    package_kind: Literal["predictive", "deterministic_transform"] = "predictive"
    input_contract_digest: str | None = None
    runtime_capability_digest: str | None = None
    feature_pipeline: FeaturePipelineSpec | None = None
    predictors: tuple[PredictorSpec, ...] = ()
    deterministic_transforms: tuple[DeterministicTransformSpec, ...] = ()
    provenance: ProvenanceSpec
    artifacts: tuple[ArtifactSpec, ...]
    smoke_test: SmokeTestSpec | None = None
    deterministic_golden: DeterministicGoldenSpec | None = None
    quality_report: str | None = None

    @model_validator(mode="after")
    def references_listed_artifacts(self) -> ModelPackageManifest:
        listed = {artifact.path for artifact in self.artifacts}
        if len(listed) != len(self.artifacts):
            raise ValueError("artifact paths must be unique")
        if self.package_kind == "predictive":
            if self.feature_pipeline is None or not self.predictors or self.deterministic_transforms:
                raise ValueError("predictive package requires a feature pipeline and predictors only")
        elif self.feature_pipeline is not None or self.predictors or not self.deterministic_transforms:
            raise ValueError(
                "deterministic-transform package requires deterministic transforms and no predictors"
            )
        if self.package_kind == "predictive" and self.deterministic_golden is not None:
            raise ValueError("predictive package cannot declare a deterministic golden test")
        if self.package_kind == "deterministic_transform" and self.deterministic_golden is None:
            raise ValueError("deterministic-transform package requires a deterministic golden test")
        needed = {
            *(predictor.artifact for predictor in self.predictors),
            *(transform.artifact for transform in self.deterministic_transforms),
        }
        if self.feature_pipeline is not None:
            needed.update((self.feature_pipeline.spec, *self.feature_pipeline.artifacts))
        if self.quality_report:
            needed.add(self.quality_report)
        if self.smoke_test:
            needed.update((self.smoke_test.input, self.smoke_test.expected))
        if self.deterministic_golden:
            needed.add(self.deterministic_golden.path)
        missing = sorted(needed - listed)
        if missing:
            raise ValueError(f"manifest references unlisted artifacts: {', '.join(missing)}")
        ids = [predictor.id for predictor in self.predictors]
        if len(ids) != len(set(ids)):
            raise ValueError("predictor ids must be unique")
        transform_ids = [transform.id for transform in self.deterministic_transforms]
        if len(transform_ids) != len(set(transform_ids)):
            raise ValueError("deterministic transform ids must be unique")
        predictors_with_identity = {
            predictor.id: predictor
            for predictor in self.predictors
            if predictor.inference_identity is not None
        }
        predictor_identities = {
            predictor_id: predictor.inference_identity
            for predictor_id, predictor in predictors_with_identity.items()
        }
        identity_keys = set(self.provenance.inference_identities)
        provenance_keys = set(self.provenance.inference_provenance)
        expected_identity_keys = set(predictor_identities)
        if identity_keys != expected_identity_keys:
            raise ValueError(
                "provenance inference_identities must exactly match predictors "
                "that declare inference_identity"
            )
        if provenance_keys != expected_identity_keys:
            raise ValueError(
                "provenance inference_provenance must exactly match predictors "
                "that declare inference_identity"
            )
        for predictor_id, predictor_identity in predictor_identities.items():
            if predictor_identity is None:
                raise ValueError(
                    f"predictor {predictor_id} has no inference identity"
                )
            recorded_identity = self.provenance.inference_identities[predictor_id]
            if recorded_identity != predictor_identity:
                raise ValueError(
                    f"provenance inference identity does not match predictor "
                    f"{predictor_id}"
                )
            recorded_provenance = self.provenance.inference_provenance[predictor_id]
            _validate_predictor_recipe_identity(
                predictors_with_identity[predictor_id],
                recorded_provenance,
            )
            if (
                recorded_provenance.inference_identity_digest
                != predictor_identity.identity_digest
            ):
                raise ValueError(
                    f"provenance inference identity digest does not match predictor "
                    f"{predictor_id}"
                )
            if recorded_provenance.diagnostics != predictor_identity.diagnostics:
                raise ValueError(
                    f"provenance diagnostics do not match predictor {predictor_id}"
                )
        if self.feature_pipeline is not None:
            expected = self.feature_pipeline.output_features
            positions = {name: index for index, name in enumerate(expected)}
            for predictor in self.predictors:
                if any(name not in positions for name in predictor.feature_names):
                    raise ValueError(
                        "predictor features must be declared by feature pipeline output_features"
                    )
                if (
                    tuple(sorted(predictor.feature_names, key=positions.__getitem__))
                    != predictor.feature_names
                ):
                    raise ValueError(
                        "predictor feature order must follow feature pipeline output_features"
                    )
        return self


def ordered_canonical_input_paths(task_definition: TaskDefinition) -> tuple[str, ...]:
    """Return every canonical input in the sole order declared by a TaskDefinition."""

    return tuple(
        field.path
        for group in sorted(task_definition.input_groups, key=lambda item: item.order)
        for field in sorted(group.fields, key=lambda item: item.order)
    )


def validate_task_definition_canonical_input_paths(
    task_definition: TaskDefinition,
    manifest: object,
) -> None:
    """Validate the Task/input seam available while a Package is still a draft."""

    task_id = getattr(manifest, "task_id", None)
    if task_id != task_definition.id:
        raise PackageContractError(
            f"model package task {task_id} does not match TaskDefinition {task_definition.id}"
        )
    expected = ordered_canonical_input_paths(task_definition)
    feature_pipeline = getattr(manifest, "feature_pipeline", None)
    if feature_pipeline is None:
        raise PackageContractError("predictive TaskDefinition requires a feature pipeline")
    actual = feature_pipeline.canonical_input_paths
    if actual != expected:
        raise PackageContractError(
            "model package canonical input order does not match TaskDefinition: "
            f"expected {expected}, got {actual}"
        )


def validate_task_definition_canonical_inputs(
    task_definition: TaskDefinition,
    manifest: ModelPackageManifest,
) -> None:
    """Validate a completed Package against Task input and output semantics."""

    validate_task_definition_canonical_input_paths(task_definition, manifest)
    task_outputs = {output.key: output for output in task_definition.outputs}
    package_outputs = {predictor.target: predictor for predictor in manifest.predictors}
    if set(package_outputs) != set(task_outputs):
        raise PackageContractError("model package outputs do not match TaskDefinition")
    mismatched_kinds = {
        key: (task_outputs[key].target_kind, package_outputs[key].target_kind)
        for key in task_outputs
        if "target_kind" in task_outputs[key].model_fields_set
        and task_outputs[key].target_kind != package_outputs[key].target_kind
    }
    if mismatched_kinds:
        raise PackageContractError(
            "model package target kinds do not match TaskDefinition: "
            f"{mismatched_kinds}"
        )


class ConformalIntervalCalibration(PackageModel):
    """Evidence attached to an interval, not a predictive distribution."""

    calibration_dataset_digest: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    calibration_sample_count: Annotated[int, Field(ge=1)]
    score_id: Literal["absolute_residual/v1"]
    finite_sample_rule: Literal["ceil_n_plus_1_over_coverage/v1"]


class ConformalWrapperIdentity(PackageModel):
    """Immutable wrapper evidence required to interpret a conformal interval."""

    wrapper_id: str
    wrapper_version: str
    manifest_digest: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    calibration_score_artifact_digest: Annotated[
        str, Field(pattern=r"^sha256:[0-9a-f]{64}$")
    ]


class PredictionInterval(PackageModel):
    """A declared interval whose method must not be inferred from its shape."""

    method: Literal["conformal", "quantile", "parametric", "bayesian"]
    coverage_level: Annotated[float, Field(gt=0, lt=1)]
    lower: float
    upper: float
    calibration: ConformalIntervalCalibration | None = None
    conformal_wrapper: ConformalWrapperIdentity | None = None

    @model_validator(mode="after")
    def ordered_finite_bounds_and_calibration(self) -> "PredictionInterval":
        if not all(math.isfinite(item) for item in (self.lower, self.upper)):
            raise ValueError("prediction interval bounds must be finite")
        if self.lower > self.upper:
            raise ValueError("prediction interval lower bound must not exceed upper bound")
        is_conformal = self.method == "conformal"
        if is_conformal != (self.calibration is not None):
            raise ValueError("only conformal intervals carry calibration evidence")
        if is_conformal != (self.conformal_wrapper is not None):
            raise ValueError("only conformal intervals carry wrapper identity")
        return self


class LatentMeanCredibleInterval(PackageModel):
    """Posterior uncertainty for the fitted mean, not a new observation."""

    method: Literal["bayesian"] = "bayesian"
    estimand: Literal["latent_mean"] = "latent_mean"
    coverage_level: Annotated[float, Field(gt=0, lt=1)]
    lower: float
    upper: float

    @model_validator(mode="after")
    def ordered_finite_bounds(self) -> "LatentMeanCredibleInterval":
        if not all(math.isfinite(item) for item in (self.lower, self.upper)):
            raise ValueError("credible interval bounds must be finite")
        if self.lower > self.upper:
            raise ValueError(
                "credible interval lower bound must not exceed upper bound"
            )
        return self


class PredictiveSummary(PackageModel):
    target: str
    target_kind: Literal["continuous", "continuous_positive", "binary", "count", "ordinal"]
    unit: str
    point_statistic: Literal["mean", "median", "probability", "rate", "expected_category"]
    point_estimate: float
    quantiles: dict[str, float] = Field(default_factory=dict)
    event_probability: float | None = None
    distribution: dict[str, Any]
    uncertainty_components: dict[str, float] | None = None
    latent_mean_credible_interval: LatentMeanCredibleInterval | None = None
    prediction_interval: PredictionInterval | None = None
    sampling_identity: SamplingIdentity | None = None
    warnings: tuple[str, ...] = ()


PredictionIntervalMethod = Literal["conformal", "quantile", "parametric", "bayesian"]


def prediction_interval_semantics(
    summary: PredictiveSummary,
    predictor: PredictorSpec | None = None,
) -> tuple[PredictionIntervalMethod | None, float | None]:
    """Return declared or audited adapter interval meaning without shape inference."""
    if summary.prediction_interval is not None:
        return (
            summary.prediction_interval.method,
            summary.prediction_interval.coverage_level,
        )
    if predictor is None or len(summary.quantiles) < 2:
        return None, None
    runtime_type = predictor.runtime_type
    family = predictor.predictive_family
    if runtime_type in {
        "builtin.exact_gp.v1",
        "builtin.heteroscedastic_exact_gp.v1",
        "gpytorch.static_exact_rbf.v1",
    }:
        return "bayesian", 0.9
    if runtime_type == "builtin.linear.v1":
        return "quantile", 0.9
    if runtime_type == "builtin.quantile_linear.v1":
        return "quantile", None
    if runtime_type in {"builtin.additive_terms.v1", "lightgbm.booster.v1"}:
        return ("parametric", 0.9) if family == "normal" else ("quantile", 0.9)
    if runtime_type == "sklearn.skops.v1" and family == "poisson_log":
        return "parametric", 0.9
    return None, None


def predictive_interval(summary: PredictiveSummary) -> tuple[float, float]:
    """Return an explicit interval before falling back to declared quantiles."""
    if summary.prediction_interval is not None:
        return summary.prediction_interval.lower, summary.prediction_interval.upper
    if not summary.quantiles:
        return summary.point_estimate, summary.point_estimate
    ordered = sorted((float(level), float(value)) for level, value in summary.quantiles.items())
    return ordered[0][1], ordered[-1][1]


class TermContribution(PackageModel):
    term_id: str
    kind: Literal["linear", "bspline_univariate", "categorical_lookup"]
    feature_names: tuple[str, ...]
    contribution: float


class AdditiveExplanation(PackageModel):
    target: str
    link_id: Literal["identity"]
    intercept: float
    terms: tuple[TermContribution, ...]
    link_score: float
    prediction: float


def validate_predictive_summary(
    summary: PredictiveSummary,
    spec: PredictorSpec,
    capability: TargetRuntimeCapability | None = None,
) -> None:
    """Validate the semantic contract shared by every prediction adapter."""
    if (summary.target, summary.target_kind, summary.unit) != (spec.target, spec.target_kind, spec.unit):
        raise PackageContractError(f"predictor {spec.id!r} returned incompatible target metadata")
    if not math.isfinite(summary.point_estimate):
        raise PackageContractError(f"predictor {spec.id!r} returned a non-finite point estimate")
    family = summary.distribution.get("family")
    if family != spec.predictive_family:
        raise PackageContractError(
            f"predictor {spec.id!r} returned distribution family {family!r}, expected {spec.predictive_family!r}"
        )
    expected_kinds = LIKELIHOOD_TARGET_KINDS.get(family)
    if expected_kinds is not None and spec.target_kind not in expected_kinds:
        raise PackageContractError(f"predictor {spec.id!r} uses {family} with incompatible target kind")
    try:
        ordered_quantiles = sorted((float(level), value) for level, value in summary.quantiles.items())
    except ValueError as exc:
        raise PackageContractError(f"predictor {spec.id!r} returned an invalid quantile level") from exc
    levels = [level for level, _ in ordered_quantiles]
    if any(not math.isfinite(level) or not 0 <= level <= 1 for level in levels) or len(levels) != len(set(levels)):
        raise PackageContractError(f"predictor {spec.id!r} returned invalid or duplicate quantile levels")
    if any(not math.isfinite(value) for _, value in ordered_quantiles):
        raise PackageContractError(f"predictor {spec.id!r} returned non-finite quantiles")
    if any(left[1] > right[1] for left, right in zip(ordered_quantiles, ordered_quantiles[1:])):
        raise PackageContractError(f"predictor {spec.id!r} returned unordered quantiles")
    if spec.target_kind == "binary" and not (
        0 <= summary.point_estimate <= 1
        and summary.event_probability is not None
        and 0 <= summary.event_probability <= 1
        and math.isclose(summary.event_probability, summary.point_estimate, rel_tol=0, abs_tol=1e-12)
    ):
        raise PackageContractError(f"predictor {spec.id!r} returned invalid binary probability semantics")
    if spec.target_kind == "binary" and any(not 0 <= value <= 1 for _, value in ordered_quantiles):
        raise PackageContractError(f"predictor {spec.id!r} returned binary quantiles outside probability support")
    values = [summary.point_estimate, *(value for _, value in ordered_quantiles)]
    requires_nonnegative_output = spec.target_kind == "count" or (
        spec.target_kind == "continuous_positive" and summary.distribution.get("support") == "positive"
    )
    if requires_nonnegative_output and any(value < 0 for value in values):
        raise PackageContractError(f"predictor {spec.id!r} returned values outside nonnegative support")
    if spec.target_kind in {"count", "ordinal"} and any(not float(value).is_integer() for _, value in ordered_quantiles):
        raise PackageContractError(f"predictor {spec.id!r} returned non-discrete quantiles")
    if spec.target_kind in {"binary", "ordinal"} and spec.unit not in {"", "1"}:
        raise PackageContractError(f"predictor {spec.id!r} must use a dimensionless unit")
    if spec.target_kind == "ordinal":
        categories = summary.distribution.get("categories")
        if not isinstance(categories, list) or len(categories) < 2 or len(categories) != len(set(categories)):
            raise PackageContractError(f"predictor {spec.id!r} returned invalid ordinal category metadata")
        if not 0 <= summary.point_estimate <= len(categories) - 1 or any(
            value < 0 or value > len(categories) - 1 for _, value in ordered_quantiles
        ):
            raise PackageContractError(f"predictor {spec.id!r} returned values outside ordinal support")
    if spec.runtime_type == "numpyro.dense_posterior.v1":
        if (
            summary.sampling_identity is None
            or summary.sampling_identity.runtime_type != spec.runtime_type
        ):
            raise PackageContractError(
                f"predictor {spec.id!r} did not return its effective sampling identity"
            )
    elif summary.sampling_identity is not None:
        raise PackageContractError(
            f"deterministic predictor {spec.id!r} must not claim a sampling identity"
        )
    credible = summary.latent_mean_credible_interval
    if credible is not None and (
        summary.prediction_interval is None
        or summary.prediction_interval.method != "bayesian"
        or not math.isclose(
            credible.coverage_level,
            summary.prediction_interval.coverage_level,
            rel_tol=0,
            abs_tol=1e-12,
        )
    ):
        raise PackageContractError(
            f"predictor {spec.id!r} latent mean interval requires a "
            "same-coverage Bayesian prediction interval"
        )
    if capability is None:
        return
    if capability.target != spec.target:
        raise PackageContractError(f"predictor {spec.id!r} capability target does not match predictor target")
    if summary.point_statistic not in capability.point_statistics:
        raise PackageContractError(f"predictor {spec.id!r} returned an undeclared point statistic")
    if bool(summary.quantiles) != capability.quantiles:
        raise PackageContractError(f"predictor {spec.id!r} quantile capability does not match its smoke output")
    has_standard_deviation = "std" in summary.distribution
    if has_standard_deviation and (
        not isinstance(summary.distribution["std"], (int, float))
        or not math.isfinite(float(summary.distribution["std"]))
        or float(summary.distribution["std"]) < 0
    ):
        raise PackageContractError(f"predictor {spec.id!r} returned an invalid standard deviation")
    if has_standard_deviation != capability.standard_deviation:
        raise PackageContractError(f"predictor {spec.id!r} standard-deviation capability does not match its smoke output")
    has_parametric_distribution = summary.distribution.get("family") != "empirical_quantiles"
    if has_parametric_distribution != capability.parametric_distribution:
        raise PackageContractError(f"predictor {spec.id!r} distribution capability does not match its smoke output")
    if bool(summary.uncertainty_components) != capability.uncertainty_components:
        raise PackageContractError(f"predictor {spec.id!r} uncertainty-component capability does not match its smoke output")
    if capability.samples:
        raise PackageContractError(f"predictor {spec.id!r} declares samples that PredictiveSummary does not expose")
    if capability.goal_probability == "normal_approximation" and not (
        summary.point_statistic == "mean"
        and family == "normal"
        and has_standard_deviation
    ):
        raise PackageContractError(
            f"predictor {spec.id!r} normal-approximation goal capability does not match its smoke output"
        )
    if capability.goal_probability == "distribution" and not has_parametric_distribution:
        raise PackageContractError(
            f"predictor {spec.id!r} distribution goal capability does not match its smoke output"
        )
