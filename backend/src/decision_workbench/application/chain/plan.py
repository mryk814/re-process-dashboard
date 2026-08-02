"""Resolve immutable Chain plans and candidate inputs."""
from __future__ import annotations

from typing import Any, Mapping

from decision_workbench.application.chain_candidate_adapters import (
    ChainCandidateAdapter,
    ChainCandidateAdapterError,
    SparseBlendChainAdapter,
    candidate_adapter_for,
    candidate_adapter_shape_for,
    candidate_path_for_revision,
)
from decision_workbench.contracts.chain_contracts import (
    ChainBinding,
    ChainDefinition,
    ChainProjectIdentity,
    ChainRevision,
    ChainStageRevision,
)
from decision_workbench.contracts.chain_execution_contracts import (
    ChainCandidateCapability,
    ChainCandidateInputDefinition,
)
from decision_workbench.contracts.candidate_project_contracts import (
    Candidate,
    CandidateInput,
    CandidateInputs,
)
from decision_workbench.contracts.task_contracts import (
    NumericRange,
    persisted_task_definition_payload,
)
from decision_workbench.execution.inference_work_graph import semantic_digest
from decision_workbench.modeling.transform_catalog import DeterministicTransformCatalog
from decision_workbench.persistence.store import Store
from decision_workbench.tasks.task_registry import TaskRegistry


class ChainExecutionError(ValueError):
    """Raised when the pinned Chain plan cannot be resolved or executed."""


def set_path(target: dict[str, Any], path: str, value: Any) -> None:
    current = target
    parts = path.split(".")
    for part in parts[:-1]:
        child = current.setdefault(part, {})
        if not isinstance(child, dict):
            raise ChainExecutionError(f"binding target path conflicts at {path}")
        current = child
    current[parts[-1]] = value


class ChainPlanningUseCase:
    def __init__(
        self,
        store: Store,
        registry: TaskRegistry,
        transform_catalog: DeterministicTransformCatalog | None,
    ) -> None:
        self.store = store
        self.registry = registry
        self.transform_catalog = transform_catalog

    def _chain(
        self, project_id: str
    ) -> tuple[ChainDefinition, ChainRevision, ChainProjectIdentity]:
        project = self.store.get_project(project_id)
        if project is None:
            raise ChainExecutionError("Chain Projectが見つかりません")
        identity = project.scientific_identity
        if identity.identity_kind != "chain":
            raise ChainExecutionError("このAPIはChain Project専用です")
        revision = self.store.get_chain_revision(identity.chain_revision_id)
        if revision is None or revision.revision_digest != identity.chain_revision_digest:
            raise ChainExecutionError("固定されたChain Revisionを解決できません")
        if not isinstance(revision, ChainRevision):
            raise ChainExecutionError(
                "Prediction Graph Revisionは現行Chain実行経路では実行できません"
            )
        definition = self.store.get_chain_definition(
            revision.chain_id, revision.chain_definition_digest
        )
        if definition is None:
            raise ChainExecutionError("固定されたChain Definitionを解決できません")
        if not isinstance(definition, ChainDefinition):
            raise ChainExecutionError(
                "Prediction Graph Definitionは現行Chain実行経路では実行できません"
            )
        return definition, revision, identity

    def candidate_adapter(self, project_id: str) -> ChainCandidateAdapter:
        _definition, revision, _identity = self._chain(project_id)
        return self.adapter_for(revision)

    def adapter_for(self, revision: ChainRevision) -> ChainCandidateAdapter:
        if self.transform_catalog is None:
            raise ChainExecutionError(
                "決定論的Transformを利用できないためChain候補を編集・実行できません"
            )
        try:
            return candidate_adapter_for(revision, self.transform_catalog)
        except ChainCandidateAdapterError as exc:
            raise ChainExecutionError(str(exc)) from exc

    def candidate_capability(self, project_id: str) -> ChainCandidateCapability:
        """Declare which candidate surface this Chain needs, before any editing."""

        definition, revision, _identity = self._chain(project_id)
        shape = candidate_adapter_shape_for(revision)
        return ChainCandidateCapability(
            adapter_id=shape.adapter_id,
            sparse_blend=shape.sparse_blend,
            external_input_paths=tuple(
                port.path for port in definition.external_inputs
            ),
        )

    def sparse_blend_adapter(self, project_id: str) -> SparseBlendChainAdapter:
        """Resolve the adapter for a Chain that declares a sparse-blend candidate."""

        adapter = self.candidate_adapter(project_id)
        if not isinstance(adapter, SparseBlendChainAdapter):
            raise ChainExecutionError(
                "このChainは疎な配合明細を使いません。"
                "候補の入力面は chain/candidate-capability で判断してください"
            )
        return adapter

    @staticmethod
    def _external_numeric_range(
        value: NumericRange,
        binding: ChainBinding,
    ) -> NumericRange:
        conversion = binding.conversion
        if conversion is None:
            return value
        if conversion.factor == 0:
            raise ChainExecutionError(
                f"外部入力の単位変換factorが0です: {binding.source.path}"
            )
        bounds = (
            (value.min - conversion.offset) / conversion.factor,
            (value.max - conversion.offset) / conversion.factor,
        )
        return NumericRange(min=min(bounds), max=max(bounds))

    @staticmethod
    def _intersect_ranges(
        ranges: list[NumericRange],
        *,
        external_path: str,
        range_name: str,
        required: bool = True,
    ) -> NumericRange | None:
        lower = max(item.min for item in ranges)
        upper = min(item.max for item in ranges)
        if lower >= upper:
            if not required:
                return None
            raise ChainExecutionError(
                f"{range_name}がStage間で重なりません: {external_path}"
            )
        return NumericRange(min=lower, max=upper)

    @staticmethod
    def _input_label(
        fields: list[Any],
        *,
        fallback: str,
    ) -> str:
        """Prefer a human label when one Stage still exposes a machine key."""

        machine_labels = {
            fallback,
            *(field.path.rsplit(".", 1)[-1] for field in fields),
        }
        return next(
            (
                field.label
                for field in reversed(fields)
                if field.label not in machine_labels
            ),
            fields[-1].label,
        )

    def candidate_input_definitions(
        self,
        project_id: str,
        *,
        require_runtime_identity: bool = True,
    ) -> tuple[ChainCandidateInputDefinition, ...]:
        """Resolve every external port into one canonical candidate editor field.

        Presentation metadata is derived at request time from the exact pinned
        Chain revision and its TaskDefinitions. It therefore does not mutate the
        immutable ChainDefinition digest used by saved Projects and Snapshots.
        """

        definition, revision, _identity = self._chain(project_id)
        stage_order = {
            stage.stage_id: index for index, stage in enumerate(revision.stages)
        }
        stage_ids = tuple(stage_order)
        downstream_edges = tuple(
            (binding.source.stage_id, binding.target_stage_id)
            for binding in definition.bindings
            if binding.source.source_kind == "stage_output"
        )
        stage_contracts = {
            stage.stage_id: self.registry.contract_for(
                stage.contract_id
            ).task_definition
            for stage in revision.stages
            if stage.stage_kind == "task"
        }
        for stage in revision.stages:
            if stage.stage_kind != "task":
                continue
            task = stage_contracts[stage.stage_id]
            if (
                semantic_digest(persisted_task_definition_payload(task))
                != stage.contract_digest
            ):
                raise ChainExecutionError(
                    f"Stage {stage.stage_id}のcontract digestが"
                    "Chain Revisionと一致しません"
                )
            if (
                require_runtime_identity
                and self.registry.entry_for(stage.contract_id).package_digest
                != stage.package_manifest_digest
            ):
                raise ChainExecutionError(
                    f"Stage {stage.stage_id}のPackage digestが"
                    "Chain Revisionと一致しません"
                )
        resolved: list[ChainCandidateInputDefinition] = []
        for order, port in enumerate(definition.external_inputs):
            bindings = sorted(
                (
                    binding
                    for binding in definition.bindings
                    if (
                        binding.source.source_kind == "external"
                        and binding.source.path == port.path
                    )
                ),
                key=lambda binding: stage_order[binding.target_stage_id],
            )
            if not bindings:
                raise ChainExecutionError(
                    f"外部入力を使うStageがありません: {port.path}"
                )
            affected = {
                binding.target_stage_id for binding in bindings
            }
            changed = True
            while changed:
                changed = False
                for source_stage_id, target_stage_id in downstream_edges:
                    if (
                        source_stage_id in affected
                        and target_stage_id not in affected
                    ):
                        affected.add(target_stage_id)
                        changed = True
            affected_stage_ids = tuple(
                stage_id for stage_id in stage_ids if stage_id in affected
            )
            try:
                candidate_path = candidate_path_for_revision(
                    revision,
                    port.path,
                    port.value_kind,
                    port.quantity,
                )
            except ChainCandidateAdapterError as exc:
                raise ChainExecutionError(str(exc)) from exc
            if port.value_kind == "sparse_blend":
                resolved.append(
                    ChainCandidateInputDefinition(
                        external_path=port.path,
                        order=order,
                        candidate_path=candidate_path,
                        kind=port.value_kind,
                        label="配合",
                        unit=port.unit,
                        required=True,
                        editable=True,
                        affected_stage_ids=affected_stage_ids,
                        first_affected_stage_id=affected_stage_ids[0],
                    )
                )
                continue

            field_bindings = []
            for binding in bindings:
                task = stage_contracts.get(binding.target_stage_id)
                field = (
                    next(
                        (
                            field
                            for group in task.input_groups
                            for field in group.fields
                            if field.path == binding.target_input_path
                        ),
                        None,
                    )
                    if task is not None
                    else None
                )
                if field is None:
                    raise ChainExecutionError(
                        "外部入力のTaskDefinition fieldを解決できません: "
                        f"{port.path} → {binding.target_stage_id}."
                        f"{binding.target_input_path}"
                    )
                if field.kind != port.value_kind:
                    raise ChainExecutionError(
                        f"外部入力型がStage契約と一致しません: {port.path}"
                    )
                candidate_group, candidate_key = candidate_path.split(".", 1)
                if (
                    field.path.rsplit(".", 1)[-1] != candidate_key
                    or field.path.split(".", 1)[0] != candidate_group
                ):
                    raise ChainExecutionError(
                        "外部入力の候補保存pathがStage契約と一致しません: "
                        f"{port.path} → {field.path}"
                    )
                if binding.conversion is None:
                    if field.unit != port.unit:
                        raise ChainExecutionError(
                            f"外部入力単位がStage契約と一致しません: {port.path}"
                        )
                elif (
                    binding.conversion.source_unit != port.unit
                    or binding.conversion.target_unit != field.unit
                ):
                    raise ChainExecutionError(
                        f"外部入力の単位変換がStage契約と一致しません: {port.path}"
                    )
                field_bindings.append((field, binding, task))
            fields = [item[0] for item in field_bindings]
            editable = all(field.editable for field in fields)
            common = {
                "external_path": port.path,
                "order": order,
                "candidate_path": candidate_path,
                "kind": port.value_kind,
                "label": self._input_label(fields, fallback=port.quantity),
                "unit": port.unit,
                "required": True,
                "editable": editable,
                "read_only_reason": (
                    None
                    if editable
                    else "固定されたStage契約で編集不可に設定されています"
                ),
                "affected_stage_ids": affected_stage_ids,
                "first_affected_stage_id": affected_stage_ids[0],
            }
            if port.value_kind == "number":
                numeric_ranges: dict[str, NumericRange | None] = {}
                for attribute, label, required in (
                    ("default_range", "既定範囲", True),
                    ("allowed_range", "許容範囲", True),
                    ("training_range", "学習範囲", False),
                ):
                    values = [
                        self._external_numeric_range(
                            getattr(field, attribute),
                            binding,
                        )
                        for field, binding, _task in field_bindings
                    ]
                    numeric_ranges[attribute] = self._intersect_ranges(
                        values,
                        external_path=port.path,
                        range_name=label,
                        required=required,
                    )
                resolved.append(
                    ChainCandidateInputDefinition(
                        **common,
                        **numeric_ranges,
                        display_decimals=max(
                            task.display_decimals[field.path]
                            for field, _binding, task in field_bindings
                        ),
                    )
                )
            else:
                allowed = set(fields[0].choices)
                for field in fields[1:]:
                    allowed &= set(field.choices)
                choices = tuple(
                    choice for choice in fields[0].choices if choice in allowed
                )
                if not choices:
                    raise ChainExecutionError(
                        f"選択肢がStage間で重なりません: {port.path}"
                    )
                resolved.append(
                    ChainCandidateInputDefinition(
                        **common,
                        choices=choices,
                    )
                )
        if len(resolved) != len(definition.external_inputs):
            raise ChainExecutionError(
                "Chain外部入力と候補入力契約の件数が一致しません"
            )
        candidate_paths = [item.candidate_path for item in resolved]
        if len(candidate_paths) != len(set(candidate_paths)):
            raise ChainExecutionError(
                "複数のChain外部入力が同じ候補保存pathを共有しています"
            )
        return tuple(resolved)

    def starter_candidate(self, project_id: str) -> CandidateInput:
        """Build a usable first candidate from the exact pinned Chain contracts."""

        _definition, revision, _identity = self._chain(project_id)
        adapter = self.adapter_for(revision)
        try:
            domain_payload = adapter.initial_domain_payload()
        except ChainCandidateAdapterError as exc:
            raise ChainExecutionError(str(exc)) from exc
        composition: dict[str, float] = {}
        process: dict[str, float] = {}
        categorical: dict[str, str] = {}
        for field in self.candidate_input_definitions(project_id):
            if field.kind == "sparse_blend":
                continue
            group, key = field.candidate_path.split(".", 1)
            if field.kind == "number":
                assert field.default_range is not None
                value = (field.default_range.min + field.default_range.max) / 2
                target = composition if group == "composition" else process
                target[key] = value
            else:
                categorical[key] = field.choices[0]
        return self.prepare_candidate(
            project_id,
            CandidateInput(
                name="基準候補",
                inputs=CandidateInputs(
                    composition=composition,
                    process=process,
                    categorical=categorical,
                    heat_pattern=None,
                    heat_time_basis="line_speed",
                ),
                **domain_payload,
            ),
        )

    def prepare_candidate(
        self, project_id: str, payload: CandidateInput
    ) -> CandidateInput:
        definition, revision, _identity = self._chain(project_id)
        adapter = self.adapter_for(revision)
        try:
            prepared = adapter.prepare_candidate(payload)
        except ChainCandidateAdapterError as exc:
            raise ChainExecutionError(str(exc)) from exc
        external = adapter.external_values(prepared)
        missing = sorted(
            port.path for port in definition.external_inputs if port.path not in external
        )
        if missing:
            raise ChainExecutionError(
                "Chain候補の外部contextが不足しています: " + ", ".join(missing)
            )
        return prepared

    def resolve(
        self, project_id: str, candidate_id: str, candidate_revision: int
    ) -> tuple[Candidate, ChainDefinition, ChainRevision, ChainProjectIdentity]:
        definition, revision, identity = self._chain(project_id)
        candidate = self.store.get_candidate_revision(
            candidate_id, candidate_revision, project_id
        )
        if candidate is None:
            raise ChainExecutionError("指定したcandidate revisionが見つかりません")
        # 候補の形状はadapterが保存前に検証済み。Coreはここで形状を仮定しない。
        return candidate, definition, revision, identity
