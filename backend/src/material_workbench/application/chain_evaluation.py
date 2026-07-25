"""Load and resolve code-free Chain evaluation artifacts."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

from material_workbench.contracts.chain_contracts import ChainRevision
from material_workbench.contracts.chain_evaluation_contracts import (
    ChainEvaluationReport,
    ResolvedChainEvaluation,
)


_RESOURCE_ROOT = Path(
    os.getenv(
        "WORKBENCH_RESOURCE_ROOT",
        str(Path(__file__).resolve().parents[4]),
    )
)
DEFAULT_CHAIN_EVALUATION_PATH = (
    _RESOURCE_ROOT
    / "models"
    / "evaluations"
    / "welding-consumable-a-b-c-v1.json"
)


class ChainEvaluationError(ValueError):
    pass


class ChainEvaluationCatalog:
    """An immutable report catalog matched against the exact Chain Revision."""

    def __init__(self, reports: tuple[tuple[ChainEvaluationReport, str], ...]) -> None:
        self._reports = reports

    @classmethod
    def load(
        cls,
        path: str | Path = DEFAULT_CHAIN_EVALUATION_PATH,
    ) -> "ChainEvaluationCatalog":
        artifact = Path(path)
        raw = artifact.read_bytes()
        report = ChainEvaluationReport.model_validate_json(raw)
        return cls(((report, "sha256:" + hashlib.sha256(raw).hexdigest()),))

    def resolve(
        self,
        *,
        revision_id: str,
        revision: ChainRevision,
        stage_source_digests: dict[str, set[str]],
    ) -> ResolvedChainEvaluation:
        matching = [
            (report, digest)
            for report, digest in self._reports
            if report.chain_id == revision.chain_id
        ]
        if len(matching) != 1:
            raise ChainEvaluationError(
                "固定されたChain Revisionに対応する評価成果物がありません"
            )
        report, artifact_digest = matching[0]
        if (
            report.chain_definition_digest != revision.chain_definition_digest
            or report.binding_digest != revision.binding_digest
            or report.unit_conversion_digest != revision.unit_conversion_digest
        ):
            raise ChainEvaluationError(
                "評価成果物のChain Definition・binding identityが一致しません"
            )
        report_stage_order = [item.stage_id for item in report.stages]
        revision_stage_order = [item.stage_id for item in revision.stages]
        if report_stage_order != revision_stage_order:
            raise ChainEvaluationError("評価成果物のStage順序がChain Revisionと一致しません")
        report_stages = {item.stage_id: item for item in report.stages}
        revision_stages = {item.stage_id: item for item in revision.stages}
        if set(report_stages) != set(revision_stages):
            raise ChainEvaluationError("評価成果物のStage集合がChain Revisionと一致しません")
        for stage_id, expected in report_stages.items():
            actual = revision_stages[stage_id]
            if (
                expected.contract_id != actual.contract_id
                or expected.contract_digest != actual.contract_digest
                or expected.package_manifest_digest
                != actual.package_manifest_digest
                or (
                    expected.dataset_profile_digest is not None
                    and expected.dataset_profile_digest
                    != actual.dataset_profile_digest
                )
            ):
                raise ChainEvaluationError(
                    f"評価成果物の固定identityが一致しません: Stage {stage_id}"
                )
            if expected.dataset_profile_digest is not None:
                if stage_source_digests.get(stage_id) != {
                    report.source_data_digest
                }:
                    raise ChainEvaluationError(
                        f"評価成果物のsource identityが一致しません: Stage {stage_id}"
                    )
        return ResolvedChainEvaluation(
            report=report,
            artifact_digest=artifact_digest,
            chain_revision_id=revision_id,
            chain_revision_digest=revision.revision_digest,
            dataset_view_revision_ids={
                stage.stage_id: stage.dataset_view_revision_id
                for stage in revision.stages
                if stage.dataset_view_revision_id is not None
            },
        )
