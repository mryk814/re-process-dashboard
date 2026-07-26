"""Allow-listed batch selectors and their Runtime Capability gates."""
from __future__ import annotations

from material_workbench.contracts.batch_proposal_contracts import (
    BatchSelectorAvailability,
    BatchSelectorDefinition,
)
from material_workbench.contracts.task_contracts import RuntimeCapability


BATCH_SELECTORS = (
    BatchSelectorDefinition(
        selector_id="ranked_top_k_v1",
        version="1.0.0",
        label="個別価値の上位",
        production_enabled=True,
    ),
    BatchSelectorDefinition(
        selector_id="greedy_value_diversity_v1",
        version="1.0.0",
        label="個別価値 + 多様性 + 資源",
        production_enabled=True,
    ),
    BatchSelectorDefinition(
        selector_id="cluster_representative_v1",
        version="0.1.0",
        label="クラスタ代表",
        production_enabled=False,
    ),
    BatchSelectorDefinition(
        selector_id="local_penalization_v1",
        version="0.1.0",
        label="Local penalization",
        production_enabled=False,
    ),
    BatchSelectorDefinition(
        selector_id="batch_thompson_v1",
        version="0.1.0",
        label="Batch Thompson Sampling",
        production_enabled=False,
        requires_samples=True,
    ),
    BatchSelectorDefinition(
        selector_id="joint_q_acquisition_v1",
        version="0.1.0",
        label="Joint q-acquisition",
        production_enabled=False,
        requires_joint_samples=True,
    ),
)


def batch_selector_availability(
    capability: RuntimeCapability,
    *,
    target: str,
) -> list[BatchSelectorAvailability]:
    target_capability = next(
        (item for item in capability.targets if item.target == target),
        None,
    )
    result = []
    for definition in BATCH_SELECTORS:
        reasons = []
        if not definition.production_enabled:
            reasons.append("このselectorはまだproduction有効化されていません")
        if definition.requires_samples and (
            target_capability is None or not target_capability.samples
        ):
            reasons.append("予測sampleに対応するRuntimeが必要です")
        if definition.requires_joint_samples and not capability.joint_samples:
            reasons.append("joint sampleに対応するRuntimeだけで利用できます")
        result.append(
            BatchSelectorAvailability(
                definition=definition,
                available=not reasons,
                reasons=tuple(reasons),
            )
        )
    return result


def require_batch_selector(
    selector_id: str,
    capability: RuntimeCapability,
    *,
    target: str,
) -> BatchSelectorDefinition:
    availability = {
        item.definition.selector_id: item
        for item in batch_selector_availability(capability, target=target)
    }
    selected = availability.get(selector_id)
    if selected is None:
        raise ValueError(f"未登録のBatch Selectorです: {selector_id}")
    if not selected.available:
        raise ValueError(" / ".join(selected.reasons))
    return selected.definition
