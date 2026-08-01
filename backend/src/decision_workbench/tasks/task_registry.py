"""Production registry joining task contracts, runtimes, and model packages."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Mapping

from decision_workbench.execution.inference_work_graph import semantic_digest
from decision_workbench.modeling.packages.contracts import FeaturePipelineSpec
from decision_workbench.modeling.packages.verification import VerifiedModelPackage
from decision_workbench.modeling.package_capabilities import (
    ModelPackageCapabilityMatrix,
    package_capability_matrix,
)
from decision_workbench.modeling.model_lifecycle import validate_lifecycle_metadata, validate_training_provenance
from decision_workbench.contracts.candidate_project_contracts import CandidateInput
from decision_workbench.contracts.task_contracts import ApplicationCapability, CanonicalCandidate, CanonicalHeatPoint, DataExplorerCapability, ModelPackageCapabilityMatrix as ContractModelPackageCapabilityMatrix, ResolvedTaskDefinition, RuntimeCapability, TaskAvailability, TaskContractFixture, TaskDefinition
from decision_workbench.task_composition.ports import (
    CurveFamilyHandler,
    DataDescriptor,
    PredictionRuntime,
    QualitySurface,
    ResponseCurveHandler,
    SupportProvider,
    TrainingInspectorAdapter,
    TrainingRangeProvider,
)
from decision_workbench.task_composition.descriptors import TaskModule
from decision_workbench.task_composition.candidate_family_adapters import (
    CandidateFamilyAdapter,
    candidate_family_adapter,
)
from decision_workbench.task_composition.catalog import (
    registered_task_modules,
)


class TaskRegistryError(ValueError):
    """A task contract cannot be resolved or disagrees with its runtime package."""


class TaskUnavailableError(TaskRegistryError):
    def __init__(self, task_id: str, availability: TaskAvailability) -> None:
        super().__init__(availability.message)
        self.task_id = task_id
        self.availability = availability


@dataclass(frozen=True)
class TaskRuntimeEntry:
    task_definition: TaskDefinition
    feature_pipeline: FeaturePipelineSpec
    model_package: VerifiedModelPackage
    predictor_runtime: PredictionRuntime
    support_provider: SupportProvider
    capability: RuntimeCapability
    capability_matrix: ModelPackageCapabilityMatrix
    application_capability: ApplicationCapability
    candidate_family_adapter: CandidateFamilyAdapter
    training_inspector: TrainingInspectorAdapter
    package_digest: str
    pipeline_digest: str
    support_digest: str
    runtime_type: str
    data_explorer: "DataExplorerEntry | None"
    response_curve: ResponseCurveHandler | None
    curve_family: CurveFamilyHandler | None


@dataclass(frozen=True)
class DataExplorerEntry:
    # Not WorkbookData: Tabular and Observation family descriptors reach here too.
    # The declared boundary is DataDescriptor plus, for quality, QualitySurface.
    # Lineage additionally needs the Workbook-family graph, which is why
    # DataExplorerCapability declares quality and lineage separately.
    data: DataDescriptor
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
    if root is None:
        from decision_workbench.task_composition.external_tasks import (
            external_task_contracts,
        )

        external = external_task_contracts()
        duplicates = sorted(set(contracts) & set(external))
        if duplicates:
            raise TaskRegistryError(
                f"external Task definitions cannot replace bundled Tasks: {duplicates}"
            )
        contracts.update(external)
    return contracts


class TaskRegistry:
    def __init__(
        self,
        runtimes: dict[str, PredictionRuntime],
        *,
        contract_root: Path | None = None,
        data_explorers: dict[str, DataExplorerEntry] | None = None,
        modules: Mapping[str, TaskModule] | None = None,
        unavailable: Mapping[str, TaskAvailability] | None = None,
        degrade_invalid_runtimes: bool = False,
    ) -> None:
        self._contracts = load_task_contracts(contract_root)
        self._modules = dict(registered_task_modules() if modules is None else modules)
        explorers = data_explorers or {}
        unavailable_tasks = dict(unavailable or {})
        registered = set(self._modules)
        if registered != set(self._contracts):
            missing = sorted(set(self._contracts) - registered)
            unknown = sorted(registered - set(self._contracts))
            raise TaskRegistryError(
                f"TaskModule registry must exactly match task definitions; missing={missing}, unknown={unknown}"
            )
        if set(runtimes) & set(unavailable_tasks):
            raise TaskRegistryError("a task cannot be both available and unavailable")
        if set(runtimes) | set(unavailable_tasks) != registered:
            missing = sorted(set(self._contracts) - set(runtimes) - set(unavailable_tasks))
            unknown = sorted(set(runtimes) - set(self._contracts))
            raise TaskRegistryError(
                f"runtime registry must exactly match task definitions; missing={missing}, unknown={unknown}"
            )
        invalid_unavailable = sorted(set(unavailable_tasks) - registered)
        if invalid_unavailable:
            raise TaskRegistryError(f"unavailable registry contains unknown tasks: {invalid_unavailable}")
        unknown_explorers = sorted(set(explorers) - set(self._contracts))
        if unknown_explorers:
            raise TaskRegistryError(f"data explorer registry contains unknown tasks: {unknown_explorers}")
        self._unavailable = unavailable_tasks
        self._entries: dict[str, TaskRuntimeEntry] = {}
        for task_id, runtime in runtimes.items():
            try:
                self._validate_runtime(task_id, runtime)
                module = self._modules[task_id]
                contract = self._contracts[task_id]
                if (
                    module.candidate_family_adapter_id
                    != contract.task_definition.canonical_candidate_schema_version
                ):
                    raise TaskRegistryError(
                        "TaskModuleのCandidate family adapterがTaskDefinitionと"
                        f"一致しません: {task_id}"
                    )
                family_adapter = candidate_family_adapter(
                    module.candidate_family_adapter_id
                )
                explorer = explorers.get(task_id)
                if explorer is not None and explorer.data is not runtime.data:
                    raise TaskRegistryError(f"data explorer source does not match runtime data: {task_id}")
                if explorer is not None:
                    self._validate_data_explorer(task_id, explorer)
            except (OSError, ValueError, KeyError) as exc:
                if not degrade_invalid_runtimes:
                    raise
                package = getattr(runtime, "model_package", None)
                verified_package = (
                    package if isinstance(package, VerifiedModelPackage) else None
                )
                self._unavailable[task_id] = TaskAvailability(
                    status="unavailable",
                    stage="runtime",
                    message=f"予測runtimeの契約を検証できません: {exc}",
                    resource_id=(
                        verified_package.manifest.package_id
                        if verified_package is not None
                        else task_id
                    ),
                    expected_locator=(
                        str(verified_package.root)
                        if verified_package is not None
                        else f"task:{task_id}"
                    ),
                    recovery_hint=(
                        "予測runtimeとTaskDefinition、対象Packageの契約を"
                        "確認して再起動してください。"
                    ),
                )
                continue
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
                capability_matrix=package_capability_matrix(
                    package.manifest,
                    contract.runtime_capability,
                    manifest_digest=package.manifest_sha256,
                ),
                application_capability=module.application,
                candidate_family_adapter=family_adapter,
                training_inspector=module.training_inspector,
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
    def _validate_data_explorer(task_id: str, explorer: "DataExplorerEntry") -> None:
        """A declared Data Explorer capability must be backed by a real surface."""

        if explorer.capability.quality and not isinstance(explorer.data, QualitySurface):
            raise TaskRegistryError(
                "data explorer declares quality but its descriptor has no quality surface: "
                f"{task_id}"
            )
        if explorer.capability.lineage and not hasattr(explorer.data, "lineage"):
            raise TaskRegistryError(
                f"data explorer declares lineage but its descriptor has no lineage: {task_id}"
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
        if not isinstance(module.training_inspector, TrainingInspectorAdapter):
            raise TaskRegistryError(
                f"TaskModule has no TrainingInspectorAdapter: {task_id}"
            )
        declares_curve = self._contracts[task_id].runtime_capability.operations.response_curve
        if declares_curve != (module.response_curve is not None):
            raise TaskRegistryError(
                f"response-curve capability and TaskModule handler disagree: {task_id}"
            )
        if module.response_curve is not None and not callable(getattr(runtime, "response_curve_result", None)):
            raise TaskRegistryError(f"response-curve runtime operation is missing: {task_id}")
        if module.response_curve is not None and not isinstance(runtime, TrainingRangeProvider):
            raise TaskRegistryError(f"response-curve runtime has no Package training-range provider: {task_id}")
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

    def candidate_family_for(self, task_id: str) -> CandidateFamilyAdapter:
        """Resolve only the family adapter verified with this Task runtime."""

        return self.entry_for(task_id).candidate_family_adapter

    def training_inspector_for(self, task_id: str) -> TrainingInspectorAdapter:
        """Resolve only the training presentation verified with this Task."""

        return self.entry_for(task_id).training_inspector

    @property
    def task_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._contracts))

    @property
    def available_task_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._entries))

    def availability_for(self, task_id: str) -> TaskAvailability:
        self.contract_for(task_id)
        return self._unavailable.get(task_id, TaskAvailability())

    def require_available(self, task_id: str) -> None:
        availability = self.availability_for(task_id)
        if availability.status == "unavailable":
            raise TaskUnavailableError(task_id, availability)

    def contract_for(self, task_id: str) -> TaskContractFixture:
        try:
            return self._contracts[task_id]
        except KeyError as exc:
            raise TaskRegistryError(f"unknown task id: {task_id}") from exc

    def runtime_for(self, task_id: str) -> PredictionRuntime:
        return self.entry_for(task_id).predictor_runtime

    def capability_matrix_for(self, task_id: str) -> ModelPackageCapabilityMatrix:
        """The selected Package's capability truth, pinned with the runtime entry."""
        return self.entry_for(task_id).capability_matrix

    def require_operation(
        self,
        task_id: str,
        operation: Literal[
            "preview", "detailed_prediction", "response_curve", "similarity", "snapshot", "actual_measurement"
        ],
    ) -> None:
        self.require_available(task_id)
        self.require_declared_operation(task_id, operation)

    def require_declared_operation(
        self,
        task_id: str,
        operation: Literal[
            "preview", "detailed_prediction", "response_curve", "similarity", "snapshot", "actual_measurement"
        ],
    ) -> None:
        if not getattr(self.contract_for(task_id).runtime_capability.operations, operation):
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
        self.require_available(task_id)
        return self._entries[task_id]

    def resolved_definition_for(self, task_id: str) -> ResolvedTaskDefinition:
        contract = self.contract_for(task_id)
        module = self.module_for(task_id)
        return ResolvedTaskDefinition(
            task_definition=contract.task_definition,
            runtime_capability=contract.runtime_capability,
            model_package_capability=(
                ContractModelPackageCapabilityMatrix.model_validate(
                    self._entries[task_id].capability_matrix.model_dump(mode="json")
                )
                if task_id in self._entries else None
            ),
            data_explorer=module.data_explorer,
            application=module.application,
            availability=self.availability_for(task_id),
        )
