"""Production registry joining task contracts, runtimes, and model packages."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Mapping

from material_workbench.data.importer import WorkbookData
from material_workbench.execution.inference_work_graph import semantic_digest
from material_workbench.modeling.model_packages import FeaturePipelineSpec, VerifiedModelPackage
from material_workbench.modeling.model_lifecycle import validate_lifecycle_metadata, validate_training_provenance
from material_workbench.contracts.schemas import CandidateInput
from material_workbench.contracts.task_contracts import ApplicationCapability, CanonicalCandidate, CanonicalHeatPoint, DataExplorerCapability, ResolvedTaskDefinition, RuntimeCapability, TaskContractFixture, TaskDefinition
from material_workbench.task_modules import (
    CurveFamilyHandler,
    DataDescriptor,
    PredictionRuntime,
    ResponseCurveHandler,
    SupportProvider,
    TaskModule,
    registered_task_modules,
)


class TaskRegistryError(ValueError):
    """A task contract cannot be resolved or disagrees with its runtime package."""


@dataclass(frozen=True)
class TaskRuntimeEntry:
    task_definition: TaskDefinition
    feature_pipeline: FeaturePipelineSpec
    model_package: VerifiedModelPackage
    predictor_runtime: PredictionRuntime
    support_provider: SupportProvider
    capability: RuntimeCapability
    application_capability: ApplicationCapability
    package_digest: str
    pipeline_digest: str
    support_digest: str
    runtime_type: str
    data_explorer: "DataExplorerEntry | None"
    response_curve: ResponseCurveHandler | None
    curve_family: CurveFamilyHandler | None


@dataclass(frozen=True)
class DataExplorerEntry:
    data: WorkbookData
    capability: DataExplorerCapability


def load_task_contracts(root: Path | None = None) -> dict[str, TaskContractFixture]:
    contract_root = root or Path(__file__).with_name("task_definitions")
    contracts: dict[str, TaskContractFixture] = {}
    for path in sorted(contract_root.glob("*.json")):
        fixture = TaskContractFixture.model_validate_json(path.read_text(encoding="utf-8"))
        task_id = fixture.task_definition.id
        if path.stem != task_id:
            raise TaskRegistryError(f"task definition filename must match task id: {path.name}")
        if task_id in contracts:
            raise TaskRegistryError(f"duplicate task definition: {task_id}")
        contracts[task_id] = fixture
    if not contracts:
        raise TaskRegistryError(f"no task definitions found in {contract_root}")
    return contracts


class TaskRegistry:
    def __init__(
        self,
        runtimes: dict[str, PredictionRuntime],
        *,
        contract_root: Path | None = None,
        data_explorers: dict[str, DataExplorerEntry] | None = None,
        modules: Mapping[str, TaskModule] | None = None,
    ) -> None:
        self._contracts = load_task_contracts(contract_root)
        self._modules = dict(registered_task_modules() if modules is None else modules)
        explorers = data_explorers or {}
        registered = set(self._modules)
        if registered != set(self._contracts):
            missing = sorted(set(self._contracts) - registered)
            unknown = sorted(registered - set(self._contracts))
            raise TaskRegistryError(
                f"TaskModule registry must exactly match task definitions; missing={missing}, unknown={unknown}"
            )
        if set(runtimes) != registered:
            missing = sorted(set(self._contracts) - set(runtimes))
            unknown = sorted(set(runtimes) - set(self._contracts))
            raise TaskRegistryError(
                f"runtime registry must exactly match task definitions; missing={missing}, unknown={unknown}"
            )
        unknown_explorers = sorted(set(explorers) - set(self._contracts))
        if unknown_explorers:
            raise TaskRegistryError(f"data explorer registry contains unknown tasks: {unknown_explorers}")
        self._entries: dict[str, TaskRuntimeEntry] = {}
        for task_id, runtime in runtimes.items():
            self._validate_runtime(task_id, runtime)
            module = self._modules[task_id]
            explorer = explorers.get(task_id)
            if explorer is not None and explorer.data is not runtime.data:
                raise TaskRegistryError(f"data explorer source does not match runtime data: {task_id}")
            package = runtime.model_package
            assert package is not None
            contract = self._contracts[task_id]
            self._entries[task_id] = TaskRuntimeEntry(
                task_definition=contract.task_definition,
                feature_pipeline=package.manifest.feature_pipeline,
                model_package=package,
                predictor_runtime=runtime,
                support_provider=runtime,
                capability=contract.runtime_capability,
                application_capability=module.application,
                package_digest=f"sha256:{package.manifest_sha256}",
                pipeline_digest=self._pipeline_digest(package),
                support_digest=semantic_digest({
                    "source_sha256": runtime.data.source_sha256,
                    "pipeline_digest": self._pipeline_digest(package),
                    "policy_id": runtime.support_policy_id,
                }),
                runtime_type="+".join(sorted({item.runtime_type for item in package.manifest.predictors})),
                data_explorer=explorer,
                response_curve=module.response_curve,
                curve_family=module.curve_family,
            )

    @staticmethod
    def _pipeline_digest(package: VerifiedModelPackage) -> str:
        manifest = package.manifest
        pipeline_paths = {manifest.feature_pipeline.spec, *manifest.feature_pipeline.artifacts}
        artifact_digests = {
            item.path: item.sha256
            for item in manifest.artifacts
            if item.path in pipeline_paths
        }
        return semantic_digest({
            "specification": manifest.feature_pipeline.model_dump(mode="json"),
            "artifacts": artifact_digests,
        })

    def _validate_runtime(
        self, task_id: str, runtime: PredictionRuntime, *, validate_training_binding: bool = True
    ) -> None:
        if not isinstance(runtime, PredictionRuntime) or runtime.task_id != task_id:
            raise TaskRegistryError(f"runtime does not implement the task protocol: {task_id}")
        if not isinstance(runtime.data, DataDescriptor):
            raise TaskRegistryError(f"runtime data does not implement the common descriptor: {task_id}")
        if not isinstance(runtime, SupportProvider):
            raise TaskRegistryError(f"runtime does not provide support operations: {task_id}")
        package = runtime.model_package
        if package is None:
            raise TaskRegistryError(f"production runtime has no model package: {task_id}")
        if package.manifest.task_id != task_id:
            raise TaskRegistryError(
                f"model package task {package.manifest.task_id} does not match {task_id}"
            )
        expected = {output.key for output in self._contracts[task_id].task_definition.outputs}
        manifest_outputs = {predictor.target for predictor in package.manifest.predictors}
        if manifest_outputs != expected:
            raise TaskRegistryError(
                f"model package outputs do not match TaskDefinition for {task_id}: "
                f"expected={sorted(expected)}, actual={sorted(manifest_outputs)}"
            )
        output_units = {output.key: output.unit for output in self._contracts[task_id].task_definition.outputs}
        package_units = {predictor.target: predictor.unit for predictor in package.manifest.predictors}
        mismatched_units = {
            key: (output_units[key], package_units[key])
            for key in expected
            if output_units[key] != package_units[key]
        }
        if mismatched_units:
            raise TaskRegistryError(
                f"model package output units do not match TaskDefinition for {task_id}: "
                f"{mismatched_units}"
            )
        if runtime.output_keys != frozenset(expected):
            raise TaskRegistryError(
                f"runtime outputs do not match TaskDefinition for {task_id}: "
                f"expected={sorted(expected)}, actual={sorted(runtime.output_keys)}"
            )
        module = self._modules[task_id]
        declares_curve = self._contracts[task_id].runtime_capability.operations.response_curve
        if declares_curve != (module.response_curve is not None):
            raise TaskRegistryError(
                f"response-curve capability and TaskModule handler disagree: {task_id}"
            )
        if module.response_curve is not None and not callable(getattr(runtime, "response_curve_result", None)):
            raise TaskRegistryError(f"response-curve runtime operation is missing: {task_id}")
        declares_family = self._contracts[task_id].task_definition.curve_axis_path is not None
        if declares_family != (module.curve_family is not None):
            raise TaskRegistryError(
                f"curve-family definition and TaskModule handler disagree: {task_id}"
            )
        if module.curve_family is not None and not callable(getattr(runtime, "curve_family_result", None)):
            raise TaskRegistryError(f"curve-family runtime operation is missing: {task_id}")
        if validate_training_binding:
            validate_lifecycle_metadata(package, self._contracts[task_id], profile_path=Path(runtime.data.profile_path))
            validate_training_provenance(package, runtime.data, self._contracts[task_id])

    def validate_application_runtime(self, task_id: str, runtime: PredictionRuntime) -> None:
        """Validate task/package compatibility without equating application data with training data."""

        self._validate_runtime(task_id, runtime, validate_training_binding=False)

    def module_for(self, task_id: str) -> TaskModule:
        self.contract_for(task_id)
        return self._modules[task_id]

    @property
    def task_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._contracts))

    def contract_for(self, task_id: str) -> TaskContractFixture:
        try:
            return self._contracts[task_id]
        except KeyError as exc:
            raise TaskRegistryError(f"unknown task id: {task_id}") from exc

    def runtime_for(self, task_id: str) -> PredictionRuntime:
        return self.entry_for(task_id).predictor_runtime

    def require_operation(
        self,
        task_id: str,
        operation: Literal[
            "preview", "detailed_prediction", "response_curve", "similarity", "snapshot", "actual_measurement"
        ],
    ) -> None:
        if not getattr(self.entry_for(task_id).capability.operations, operation):
            raise TaskRegistryError(f"{operation} is not available for task: {task_id}")

    def response_curve_for(self, task_id: str) -> ResponseCurveHandler:
        self.require_operation(task_id, "response_curve")
        handler = self.entry_for(task_id).response_curve
        if handler is None:
            raise TaskRegistryError(f"response curve is not available for task: {task_id}")
        return handler

    def curve_family_for(self, task_id: str) -> CurveFamilyHandler:
        handler = self.entry_for(task_id).curve_family
        if handler is None:
            raise TaskRegistryError(f"curve family is not available for task: {task_id}")
        return handler

    def data_explorer_for(self, task_id: str) -> DataExplorerEntry:
        explorer = self.entry_for(task_id).data_explorer
        if explorer is None:
            raise TaskRegistryError(f"data explorer is not available for task: {task_id}")
        return explorer

    def validate_candidate(self, task_id: str, candidate: CandidateInput) -> CanonicalCandidate:
        contract = self.contract_for(task_id)
        inputs = candidate.inputs
        canonical = CanonicalCandidate(
            schema_version=contract.task_definition.canonical_candidate_schema_version,
            task_id=task_id,
            composition=inputs.composition,
            process=inputs.process,
            heat_pattern=None if inputs.heat_pattern is None else tuple(
                CanonicalHeatPoint(time_s=point.time_s, temperature_c=point.temperature_c)
                for point in inputs.heat_pattern
            ),
            categorical=inputs.categorical,
            provenance=candidate.provenance,
        )
        TaskContractFixture(
            task_definition=contract.task_definition,
            canonical_candidate=canonical,
            runtime_capability=contract.runtime_capability,
        )
        return canonical

    def entry_for(self, task_id: str) -> TaskRuntimeEntry:
        self.contract_for(task_id)
        return self._entries[task_id]

    def resolved_definition_for(self, task_id: str) -> ResolvedTaskDefinition:
        entry = self.entry_for(task_id)
        return ResolvedTaskDefinition(
            task_definition=entry.task_definition,
            runtime_capability=entry.capability,
            data_explorer=None if entry.data_explorer is None else entry.data_explorer.capability,
            application=entry.application_capability,
        )
