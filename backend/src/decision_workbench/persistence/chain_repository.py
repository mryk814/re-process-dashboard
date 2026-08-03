from __future__ import annotations

import json
import sqlite3
from typing import Any, Callable, Mapping
from decision_workbench.contracts.chain_contracts import (
    ChainDefinition,
    ChainRevision,
    GraphDefinitionRef,
    GraphRevisionRef,
    PredictionGraphDefinition,
    PredictionGraphRevision,
    StageContractSurface,
    parse_graph_definition_json,
    parse_graph_revision_json,
    validate_chain_revision,
    validate_prediction_graph_revision,
)
from decision_workbench.contracts.chain_execution_contracts import (
    ActualConditionedVariant,
    ChainExecution,
    ChainSnapshot,
    PredictionGraphDecisionOutputActual,
    PredictionGraphExecution,
    PredictionGraphSnapshot,
    parse_execution_json,
    parse_snapshot_json,
)
from decision_workbench.contracts.chain_uncertainty_contracts import (
    ChainDistributionRun,
)
from decision_workbench.persistence.store_support import (
    ChainCatalogConflictError,
    StoreDataIntegrityError,
    _now,
)


class ChainRepository:
    @staticmethod
    def _validate_revision_pair(
        definition: GraphDefinitionRef,
        revision: GraphRevisionRef,
        *,
        contracts: Mapping[tuple[str, str], StageContractSurface],
    ) -> None:
        try:
            if isinstance(definition, ChainDefinition) and isinstance(
                revision, ChainRevision
            ):
                validate_chain_revision(
                    definition,
                    revision,
                    contracts=contracts,
                )
            elif isinstance(
                definition, PredictionGraphDefinition
            ) and isinstance(revision, PredictionGraphRevision):
                validate_prediction_graph_revision(
                    definition,
                    revision,
                    contracts=contracts,
                )
            else:
                raise ValueError(
                    "DefinitionとRevisionのschema familyが一致しません"
                )
        except ValueError as exc:
            raise ChainCatalogConflictError(str(exc)) from exc

    @staticmethod
    def _register_chain_definition_in_connection(
        conn: sqlite3.Connection,
        definition: GraphDefinitionRef,
    ) -> str:
        record_id = (
            f"{definition.chain_id}@{definition.digest.removeprefix('sha256:')[:12]}"
        )
        existing = conn.execute(
            "SELECT id,definition_json FROM chain_definitions "
            "WHERE definition_digest=?",
            (definition.digest,),
        ).fetchone()
        if existing is not None:
            if (
                parse_graph_definition_json(existing["definition_json"])
                != definition
            ):
                raise ChainCatalogConflictError(
                    "同じdigestのChain Definitionに異なる内容があります"
                )
            return str(existing["id"])
        conn.execute(
            "INSERT INTO chain_definitions("
            "id,chain_id,definition_digest,definition_json,created_at"
            ") VALUES (?,?,?,?,?)",
            (
                record_id,
                definition.chain_id,
                definition.digest,
                definition.model_dump_json(),
                _now(),
            ),
        )
        return record_id

    def register_chain_definition(self, definition: GraphDefinitionRef) -> str:
        with self._connect() as conn:
            return self._register_chain_definition_in_connection(
                conn,
                definition,
            )

    @staticmethod
    def _register_stage_contract_surfaces(
        conn: sqlite3.Connection,
        *,
        record_id: str,
        revision: GraphRevisionRef,
        contracts: Mapping[tuple[str, str], StageContractSurface],
    ) -> None:
        """Keep the validation surface beside, never inside, immutable revision JSON."""

        for stage in revision.stages:
            surface = contracts[(stage.stage_kind, stage.contract_id)]
            encoded = surface.model_dump_json()
            existing = conn.execute(
                "SELECT surface_json FROM chain_stage_contract_surfaces "
                "WHERE chain_revision_id=? AND stage_id=?",
                (record_id, stage.stage_id),
            ).fetchone()
            if existing is not None:
                if StageContractSurface.model_validate_json(existing["surface_json"]) != surface:
                    raise ChainCatalogConflictError(
                        "同じChain RevisionのStage contract surfaceが異なります"
                    )
                continue
            conn.execute(
                "INSERT INTO chain_stage_contract_surfaces("
                "chain_revision_id,stage_id,surface_json,created_at"
                ") VALUES (?,?,?,?)",
                (record_id, stage.stage_id, encoded, _now()),
            )

    def get_chain_stage_contract_surfaces(
        self, revision_id: str,
    ) -> dict[str, StageContractSurface]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT stage_id,surface_json FROM chain_stage_contract_surfaces "
                "WHERE chain_revision_id=? ORDER BY stage_id",
                (revision_id,),
            ).fetchall()
        return {
            str(row["stage_id"]): StageContractSurface.model_validate_json(row["surface_json"])
            for row in rows
        }

    def list_chain_definitions(self) -> list[GraphDefinitionRef]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT definition_json FROM chain_definitions "
                "ORDER BY chain_id,created_at"
            ).fetchall()
        return [
            parse_graph_definition_json(row["definition_json"]) for row in rows
        ]

    def get_chain_definition(
        self, chain_id: str, definition_digest: str
    ) -> GraphDefinitionRef | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT definition_json FROM chain_definitions "
                "WHERE chain_id=? AND definition_digest=?",
                (chain_id, definition_digest),
            ).fetchone()
        return (
            parse_graph_definition_json(row["definition_json"])
            if row is not None
            else None
        )

    @classmethod
    def _register_chain_revision_in_connection(
        cls,
        conn: sqlite3.Connection,
        revision: GraphRevisionRef,
        *,
        contracts: Mapping[tuple[str, str], StageContractSurface],
        validate: bool = True,
    ) -> str:
        record_id = f"{revision.chain_id}:r{revision.revision}"
        definition_row = conn.execute(
            "SELECT definition_json FROM chain_definitions "
            "WHERE chain_id=? AND definition_digest=?",
            (revision.chain_id, revision.chain_definition_digest),
        ).fetchone()
        if definition_row is None:
            raise ChainCatalogConflictError(
                "Chain Revisionが参照するDefinitionを先に登録してください"
            )
        definition = parse_graph_definition_json(
            definition_row["definition_json"]
        )
        if validate:
            cls._validate_revision_pair(
                definition,
                revision,
                contracts=contracts,
            )
        existing = conn.execute(
            "SELECT id,revision_json FROM chain_revisions "
            "WHERE id=? OR revision_digest=?",
            (record_id, revision.revision_digest),
        ).fetchone()
        if existing is not None:
            if (
                parse_graph_revision_json(existing["revision_json"])
                != revision
            ):
                raise ChainCatalogConflictError(
                    "同じChain revision番号またはdigestに異なる内容があります"
                )
            cls._register_stage_contract_surfaces(
                conn, record_id=record_id, revision=revision, contracts=contracts
            )
            return str(existing["id"])
        conn.execute(
            "INSERT INTO chain_revisions("
            "id,chain_id,revision,revision_digest,revision_json,created_at"
            ") VALUES (?,?,?,?,?,?)",
            (
                record_id,
                revision.chain_id,
                revision.revision,
                revision.revision_digest,
                revision.model_dump_json(),
                _now(),
            ),
        )
        cls._register_stage_contract_surfaces(
            conn, record_id=record_id, revision=revision, contracts=contracts
        )
        return record_id

    def register_chain_revision(
        self,
        revision: GraphRevisionRef,
        *,
        contracts: Mapping[tuple[str, str], StageContractSurface],
    ) -> str:
        with self._connect() as conn:
            return self._register_chain_revision_in_connection(
                conn,
                revision,
                contracts=contracts,
            )

    def register_prediction_graph_bundle(
        self,
        items: tuple[
            tuple[PredictionGraphDefinition, PredictionGraphRevision],
            ...,
        ],
        *,
        contracts: Mapping[tuple[str, str], StageContractSurface],
    ) -> tuple[str, ...]:
        """Register a prebuilt fixture bundle atomically."""

        if not items:
            raise ChainCatalogConflictError(
                "Prediction Graph bundleには1件以上必要です"
            )
        for definition, revision in items:
            self._validate_revision_pair(
                definition,
                revision,
                contracts=contracts,
            )
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            record_ids: list[str] = []
            for definition, revision in items:
                self._register_chain_definition_in_connection(conn, definition)
                record_ids.append(
                    self._register_chain_revision_in_connection(
                        conn,
                        revision,
                        contracts=contracts,
                        validate=False,
                    )
                )
            return tuple(record_ids)

    def publish_prediction_graph(
        self,
        definition: PredictionGraphDefinition,
        *,
        contracts: Mapping[tuple[str, str], StageContractSurface],
        revision_factory: Callable[[int], PredictionGraphRevision],
    ) -> tuple[str, PredictionGraphRevision]:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT COALESCE(MAX(revision), 0) AS latest_revision "
                "FROM chain_revisions WHERE chain_id=?",
                (definition.graph_id,),
            ).fetchone()
            revision = revision_factory(int(row["latest_revision"]) + 1)
            if revision.graph_id != definition.graph_id:
                raise ChainCatalogConflictError(
                    "Prediction Graph DefinitionとRevisionのidentityが一致しません"
                )
            self._register_chain_definition_in_connection(conn, definition)
            record_id = self._register_chain_revision_in_connection(
                conn,
                revision,
                contracts=contracts,
            )
            return record_id, revision

    def list_chain_revisions(self) -> list[GraphRevisionRef]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT revision_json FROM chain_revisions ORDER BY chain_id,revision"
            ).fetchall()
        return [parse_graph_revision_json(row["revision_json"]) for row in rows]

    def get_chain_revision(self, revision_id: str) -> GraphRevisionRef | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT revision_json FROM chain_revisions WHERE id=?",
                (revision_id,),
            ).fetchone()
        return (
            parse_graph_revision_json(row["revision_json"])
            if row is not None
            else None
        )

    def get_chain_stage_memo(self, memo_key: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT canonical_input_json,result_json FROM chain_stage_memo "
                "WHERE memo_key=?",
                (memo_key,),
            ).fetchone()
        if row is None:
            return None
        return {
            "canonical_input": json.loads(row["canonical_input_json"]),
            "result": json.loads(row["result_json"]),
        }

    def put_chain_stage_memo(
        self,
        *,
        memo_key: str,
        stage_id: str,
        input_digest: str,
        contract_digest: str,
        package_manifest_digest: str,
        canonical_input: Mapping[str, Any],
        result: Mapping[str, Any],
    ) -> None:
        encoded_input = json.dumps(
            canonical_input, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        encoded_result = json.dumps(
            result, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT canonical_input_json,result_json FROM chain_stage_memo "
                "WHERE memo_key=?",
                (memo_key,),
            ).fetchone()
            if existing is not None:
                if (
                    existing["canonical_input_json"] != encoded_input
                    or existing["result_json"] != encoded_result
                ):
                    raise StoreDataIntegrityError(
                        "同じChain stage memo identityに異なる内容があります"
                    )
                return
            conn.execute(
                "INSERT INTO chain_stage_memo("
                "memo_key,stage_id,input_digest,contract_digest,package_manifest_digest,"
                "canonical_input_json,result_json,created_at"
                ") VALUES (?,?,?,?,?,?,?,?)",
                (
                    memo_key,
                    stage_id,
                    input_digest,
                    contract_digest,
                    package_manifest_digest,
                    encoded_input,
                    encoded_result,
                    _now(),
                ),
            )

    @staticmethod
    def chain_execution_scope(project_id: str, candidate_id: str) -> str:
        return f"{project_id}:{candidate_id}"

    def get_chain_execution(
        self, project_id: str, candidate_id: str
    ) -> ChainExecution | None:
        scope_id = self.chain_execution_scope(project_id, candidate_id)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT execution_json FROM chain_execution_state WHERE scope_id=?",
                (scope_id,),
            ).fetchone()
        if row is None:
            return None
        execution = parse_execution_json(row["execution_json"])
        return execution if isinstance(execution, ChainExecution) else None

    def get_prediction_graph_execution(
        self,
        project_id: str,
        candidate_id: str,
    ) -> PredictionGraphExecution | None:
        scope_id = self.chain_execution_scope(project_id, candidate_id)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT execution_json FROM chain_execution_state WHERE scope_id=?",
                (scope_id,),
            ).fetchone()
        if row is None:
            return None
        execution = parse_execution_json(row["execution_json"])
        return (
            execution
            if isinstance(execution, PredictionGraphExecution)
            else None
        )

    @staticmethod
    def _claim_chain_execution(
        conn: sqlite3.Connection,
        scope_id: str,
        request_id: str,
    ) -> int:
        row = conn.execute(
            "SELECT generation FROM chain_execution_claims WHERE scope_id=?",
            (scope_id,),
        ).fetchone()
        generation = int(row["generation"]) + 1 if row is not None else 1
        conn.execute(
            "INSERT INTO chain_execution_claims("
            "scope_id,request_id,generation,updated_at"
            ") VALUES (?,?,?,?) "
            "ON CONFLICT(scope_id) DO UPDATE SET "
            "request_id=excluded.request_id,generation=excluded.generation,"
            "updated_at=excluded.updated_at",
            (scope_id, request_id, generation, _now()),
        )
        return generation

    def claim_chain_execution(
        self,
        project_id: str,
        candidate_id: str,
        candidate_revision: int,
        request_id: str,
    ) -> int | None:
        """Claim execution only while the resolved candidate revision is current."""

        scope_id = self.chain_execution_scope(project_id, candidate_id)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            candidate = conn.execute(
                "SELECT 1 FROM candidates "
                "JOIN projects ON projects.id=candidates.project_id "
                "WHERE candidates.id=? AND candidates.project_id=? "
                "AND candidates.revision=? "
                "AND candidates.archived_at IS NULL "
                "AND projects.archived_at IS NULL",
                (candidate_id, project_id, candidate_revision),
            ).fetchone()
            if candidate is None:
                return None
            return self._claim_chain_execution(conn, scope_id, request_id)

    def chain_execution_generation(
        self, project_id: str, candidate_id: str, request_id: str
    ) -> int | None:
        scope_id = self.chain_execution_scope(project_id, candidate_id)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT generation FROM chain_execution_claims "
                "WHERE scope_id=? AND request_id=?",
                (scope_id, request_id),
            ).fetchone()
        return int(row["generation"]) if row is not None else None

    def save_chain_execution_if_current(
        self, execution: ChainExecution, generation: int
    ) -> bool:
        return self._save_execution_if_current(execution, generation)

    def save_prediction_graph_execution_if_current(
        self,
        execution: PredictionGraphExecution,
        generation: int,
    ) -> bool:
        return self._save_execution_if_current(execution, generation)

    def _save_execution_if_current(
        self,
        execution: ChainExecution | PredictionGraphExecution,
        generation: int,
    ) -> bool:
        scope_id = self.chain_execution_scope(
            execution.project_id, execution.candidate_id
        )
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            claim = conn.execute(
                "SELECT 1 FROM chain_execution_claims "
                "WHERE scope_id=? AND request_id=? AND generation=?",
                (scope_id, execution.request_id, generation),
            ).fetchone()
            if claim is None:
                return False
            conn.execute(
                "INSERT INTO chain_execution_state("
                "scope_id,request_id,execution_json,updated_at"
                ") VALUES (?,?,?,?) "
                "ON CONFLICT(scope_id) DO UPDATE SET "
                "request_id=excluded.request_id,execution_json=excluded.execution_json,"
                "updated_at=excluded.updated_at",
                (
                    scope_id,
                    execution.request_id,
                    execution.model_dump_json(),
                    execution.updated_at.isoformat(),
                ),
            )
        return True

    def get_chain_snapshot(
        self,
        snapshot_id: str,
        *,
        project_id: str | None = None,
    ) -> ChainSnapshot | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload_json FROM chain_snapshot_records WHERE id=?"
                + (" AND project_id=?" if project_id is not None else ""),
                (
                    (snapshot_id, project_id)
                    if project_id is not None
                    else (snapshot_id,)
                ),
            ).fetchone()
        if row is None:
            return None
        snapshot = parse_snapshot_json(row["payload_json"])
        return snapshot if isinstance(snapshot, ChainSnapshot) else None

    def get_prediction_graph_snapshot(
        self,
        snapshot_id: str,
        *,
        project_id: str | None = None,
    ) -> PredictionGraphSnapshot | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload_json FROM chain_snapshot_records WHERE id=?"
                + (" AND project_id=?" if project_id is not None else ""),
                (
                    (snapshot_id, project_id)
                    if project_id is not None
                    else (snapshot_id,)
                ),
            ).fetchone()
        if row is None:
            return None
        snapshot = parse_snapshot_json(row["payload_json"])
        return (
            snapshot
            if isinstance(snapshot, PredictionGraphSnapshot)
            else None
        )

    def insert_chain_distribution_run(
        self,
        run: ChainDistributionRun,
        *,
        expected_point: ChainExecution,
    ) -> ChainDistributionRun:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            candidate = conn.execute(
                "SELECT revision,archived_at FROM candidates "
                "WHERE id=? AND project_id=?",
                (run.provenance.candidate_id, run.project_id),
            ).fetchone()
            if (
                candidate is None
                or candidate["archived_at"] is not None
                or int(candidate["revision"]) != run.provenance.candidate_revision
            ):
                raise StoreDataIntegrityError(
                    "分布実行のcandidate revisionは現在値ではありません"
                )
            scope_id = self.chain_execution_scope(
                run.project_id, run.provenance.candidate_id
            )
            point_row = conn.execute(
                "SELECT execution_json FROM chain_execution_state WHERE scope_id=?",
                (scope_id,),
            ).fetchone()
            point = (
                ChainExecution.model_validate_json(point_row["execution_json"])
                if point_row is not None
                else None
            )
            if (
                point is None
                or point != expected_point
                or point.status != "latest"
                or point.request_id != run.provenance.point_execution_request_id
                or point.candidate_revision != run.provenance.candidate_revision
                or point.chain_revision_digest != run.provenance.chain_revision_digest
                or any(stage.status != "latest" for stage in point.stages)
            ):
                raise StoreDataIntegrityError(
                    "分布実行が参照した点推定は現在値ではありません"
                )
            conn.execute(
                "INSERT INTO chain_distribution_runs("
                "id,project_id,candidate_id,candidate_revision,"
                "chain_revision_digest,payload_json,created_at"
                ") VALUES (?,?,?,?,?,?,?)",
                (
                    run.run_id,
                    run.project_id,
                    run.provenance.candidate_id,
                    run.provenance.candidate_revision,
                    run.provenance.chain_revision_digest,
                    run.model_dump_json(),
                    run.created_at.isoformat(),
                ),
            )
        return run

    def get_chain_distribution_run(self, run_id: str) -> ChainDistributionRun | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload_json FROM chain_distribution_runs WHERE id=?",
                (run_id,),
            ).fetchone()
        return (
            ChainDistributionRun.model_validate_json(row["payload_json"])
            if row is not None
            else None
        )

    def latest_chain_distribution_run(
        self, project_id: str, candidate_id: str
    ) -> ChainDistributionRun | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload_json FROM chain_distribution_runs "
                "WHERE project_id=? AND candidate_id=? "
                "ORDER BY created_at DESC,id DESC LIMIT 1",
                (project_id, candidate_id),
            ).fetchone()
        return (
            ChainDistributionRun.model_validate_json(row["payload_json"])
            if row is not None
            else None
        )

    def list_chain_snapshots(
        self, project_id: str, candidate_id: str
    ) -> list[ChainSnapshot]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT payload_json FROM chain_snapshot_records "
                "WHERE project_id=? AND candidate_id=? ORDER BY created_at DESC",
                (project_id, candidate_id),
            ).fetchall()
        snapshots = [
            parse_snapshot_json(row["payload_json"]) for row in rows
        ]
        return [
            snapshot
            for snapshot in snapshots
            if isinstance(snapshot, ChainSnapshot)
        ]

    def list_prediction_graph_snapshots(
        self,
        project_id: str,
        candidate_id: str,
    ) -> list[PredictionGraphSnapshot]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT payload_json FROM chain_snapshot_records "
                "WHERE project_id=? AND candidate_id=? ORDER BY created_at DESC",
                (project_id, candidate_id),
            ).fetchall()
        snapshots = [
            parse_snapshot_json(row["payload_json"]) for row in rows
        ]
        return [
            snapshot
            for snapshot in snapshots
            if isinstance(snapshot, PredictionGraphSnapshot)
        ]

    def insert_prediction_graph_decision_output_actual(
        self,
        actual: PredictionGraphDecisionOutputActual,
    ) -> PredictionGraphDecisionOutputActual:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            candidate = conn.execute(
                "SELECT revision,archived_at FROM candidates "
                "WHERE id=? AND project_id=?",
                (actual.candidate_id, actual.project_id),
            ).fetchone()
            if (
                candidate is None
                or candidate["archived_at"] is not None
                or int(candidate["revision"]) != actual.candidate_revision
            ):
                raise StoreDataIntegrityError(
                    "Graph Actualのcandidate revisionは現在値ではありません"
                )
            snapshot_row = conn.execute(
                "SELECT payload_json FROM chain_snapshot_records "
                "WHERE id=? AND project_id=? AND candidate_id=? "
                "AND candidate_revision=?",
                (
                    actual.snapshot_id,
                    actual.project_id,
                    actual.candidate_id,
                    actual.candidate_revision,
                ),
            ).fetchone()
            if snapshot_row is None:
                raise StoreDataIntegrityError(
                    "Graph Actualの比較元snapshotを固定できません"
                )
            snapshot = parse_snapshot_json(snapshot_row["payload_json"])
            if not isinstance(snapshot, PredictionGraphSnapshot):
                raise StoreDataIntegrityError(
                    "Graph ActualはPrediction Graph snapshotだけを参照できます"
                )
            snapshot_output = next(
                (
                    item
                    for item in snapshot.terminal_outputs
                    if item.output_id == actual.output_id
                ),
                None,
            )
            if (
                snapshot.identity.graph_revision_id != actual.graph_revision_id
                or snapshot.identity.graph_revision_digest
                != actual.graph_revision_digest
                or snapshot.identity.project_binding_revision
                != actual.project_binding_revision
                or snapshot.identity.project_binding_digest
                != actual.project_binding_digest
                or snapshot_output is None
                or snapshot_output.status != "latest"
                or snapshot_output.source_stage_id != actual.source_stage_id
                or snapshot_output.source_output_key != actual.source_output_key
                or snapshot_output.value != actual.prediction_value
            ):
                raise StoreDataIntegrityError(
                    "Graph Actual identityが比較元snapshotと一致しません"
                )
            conn.execute(
                "INSERT INTO prediction_graph_decision_output_actuals("
                "id,project_id,candidate_id,snapshot_id,output_id,"
                "payload_json,created_at) VALUES (?,?,?,?,?,?,?)",
                (
                    actual.actual_id,
                    actual.project_id,
                    actual.candidate_id,
                    actual.snapshot_id,
                    actual.output_id,
                    actual.model_dump_json(),
                    actual.created_at.isoformat(),
                ),
            )
        return actual

    def list_prediction_graph_decision_output_actuals(
        self,
        project_id: str,
        candidate_id: str,
    ) -> list[PredictionGraphDecisionOutputActual]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT payload_json "
                "FROM prediction_graph_decision_output_actuals "
                "WHERE project_id=? AND candidate_id=? "
                "ORDER BY created_at DESC,id DESC",
                (project_id, candidate_id),
            ).fetchall()
        return [
            PredictionGraphDecisionOutputActual.model_validate_json(
                row["payload_json"]
            )
            for row in rows
        ]

    def insert_chain_analysis_variant(
        self, variant: ActualConditionedVariant
    ) -> ActualConditionedVariant:
        with self._connect() as conn:
            snapshot = conn.execute(
                "SELECT 1 FROM chain_snapshot_records WHERE id=? AND project_id=? "
                "AND candidate_id=? AND candidate_revision=?",
                (
                    variant.identity.comparison_snapshot_id,
                    variant.project_id,
                    variant.identity.base_candidate_id,
                    variant.identity.base_candidate_revision,
                ),
            ).fetchone()
            if snapshot is None:
                raise StoreDataIntegrityError(
                    "実測variantの比較元Chain snapshotを固定できません"
                )
            conn.execute(
                "INSERT INTO chain_analysis_variant_records("
                "id,project_id,candidate_id,candidate_revision,"
                "comparison_snapshot_id,payload_json,created_at"
                ") VALUES (?,?,?,?,?,?,?)",
                (
                    variant.variant_id,
                    variant.project_id,
                    variant.identity.base_candidate_id,
                    variant.identity.base_candidate_revision,
                    variant.identity.comparison_snapshot_id,
                    variant.model_dump_json(),
                    variant.created_at.isoformat(),
                ),
            )
        return variant

    def list_chain_analysis_variants(
        self, project_id: str, candidate_id: str
    ) -> list[ActualConditionedVariant]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT payload_json FROM chain_analysis_variant_records "
                "WHERE project_id=? AND candidate_id=? ORDER BY created_at DESC",
                (project_id, candidate_id),
            ).fetchall()
        return [
            ActualConditionedVariant.model_validate_json(row["payload_json"])
            for row in rows
        ]

    def get_chain_analysis_variant(
        self, variant_id: str
    ) -> ActualConditionedVariant | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload_json FROM chain_analysis_variant_records WHERE id=?",
                (variant_id,),
            ).fetchone()
        return (
            ActualConditionedVariant.model_validate_json(row["payload_json"])
            if row is not None
            else None
        )
