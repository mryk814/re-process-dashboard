"""Production registry joining task contracts, runtimes, and model packages."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .importer import WorkbookData
from .model_packages import FeaturePipelineSpec, VerifiedModelPackage
from .model_lifecycle import validate_lifecycle_metadata, validate_training_provenance
from .schemas import CandidateInput
from .task_contracts import CanonicalCandidate, CanonicalHeatPoint, ResolvedTaskDefinition, RuntimeCapability, TaskContractFixture, TaskDefinition


class TaskRegistryError(ValueError):
    """A task contract cannot be resolved or disagrees with its runtime package."""


@runtime_checkable
class RuntimeProtocol(Protocol):
    task_id: str
    data: WorkbookData
    model_package: VerifiedModelPackage | None

    @property
    def output_keys(self) -> frozenset[str]: ...

    def predict(self, candidate: Any, **kwargs: Any) -> dict[str, Any]: ...


@dataclass(frozen=True)
class TaskRuntimeEntry:
    task_definition: TaskDefinition
    feature_pipeline: FeaturePipelineSpec
    model_package: VerifiedModelPackage
    predictor_runtime: RuntimeProtocol
    capability: RuntimeCapability


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
        runtimes: dict[str, RuntimeProtocol],
        *,
        contract_root: Path | None = None,
    ) -> None:
        self._contracts = load_task_contracts(contract_root)
        if set(runtimes) != set(self._contracts):
            missing = sorted(set(self._contracts) - set(runtimes))
            unknown = sorted(set(runtimes) - set(self._contracts))
            raise TaskRegistryError(
                f"runtime registry must exactly match task definitions; missing={missing}, unknown={unknown}"
            )
        self._entries: dict[str, TaskRuntimeEntry] = {}
        for task_id, runtime in runtimes.items():
            self._validate_runtime(task_id, runtime)
            package = runtime.model_package
            assert package is not None
            contract = self._contracts[task_id]
            self._entries[task_id] = TaskRuntimeEntry(
                task_definition=contract.task_definition,
                feature_pipeline=package.manifest.feature_pipeline,
                model_package=package,
                predictor_runtime=runtime,
                capability=contract.runtime_capability,
            )

    def _validate_runtime(self, task_id: str, runtime: RuntimeProtocol) -> None:
        if not isinstance(runtime, RuntimeProtocol) or runtime.task_id != task_id:
            raise TaskRegistryError(f"runtime does not implement the task protocol: {task_id}")
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
        if runtime.output_keys != frozenset(expected):
            raise TaskRegistryError(
                f"runtime outputs do not match TaskDefinition for {task_id}: "
                f"expected={sorted(expected)}, actual={sorted(runtime.output_keys)}"
            )
        validate_lifecycle_metadata(package, self._contracts[task_id])
        validate_training_provenance(package, runtime.data, self._contracts[task_id])

    @property
    def task_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._contracts))

    def contract_for(self, task_id: str) -> TaskContractFixture:
        try:
            return self._contracts[task_id]
        except KeyError as exc:
            raise TaskRegistryError(f"unknown task id: {task_id}") from exc

    def runtime_for(self, task_id: str) -> RuntimeProtocol:
        return self.entry_for(task_id).predictor_runtime

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
        )
