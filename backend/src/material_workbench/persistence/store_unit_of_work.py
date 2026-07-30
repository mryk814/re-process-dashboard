from __future__ import annotations

import json
import uuid
from typing import Any
from material_workbench.persistence.project_persistence_inventory import (
    PROJECT_PERSISTENCE,
)
from material_workbench.contracts.chain_contracts import (
    ChainProjectIdentity,
)
from material_workbench.contracts.chain_execution_contracts import (
    ActualConditionedVariant,
    ChainSnapshot,
)
from material_workbench.contracts.chain_uncertainty_contracts import (
    ChainDistributionRun,
)
from material_workbench.contracts.schemas import (
    ActualMeasurement,
    ActualMeasurementInput,
    Candidate,
    CandidateInput,
    Project,
    ProjectCreateInput,
    ProjectUpdateInput,
)
from material_workbench.contracts.ai_review_contracts import (
    AiReviewRun,
)
from material_workbench.contracts.objective_contracts import (
    ObjectiveDefinition,
)
from material_workbench.persistence.store_support import (
    ActiveProjectPurgeError,
    CandidateArchivedError,
    CandidateCopyConflictError,
    CandidateRevisionConflictError,
    ChainCatalogConflictError,
    PROTECTED_PROJECT_IDS,
    ProjectHasDerivedCandidatesError,
    ProjectHasSuccessorsError,
    ProjectNotFoundError,
    ProtectedProjectError,
    StoreDataIntegrityError,
    _now,
    _single_task_identity_json,
    _target_values_json,
)


class WorkbenchUnitOfWork:
    def create_project(
        self,
        payload: ProjectCreateInput,
        initial_candidate: CandidateInput | None = None,
    ) -> Project:
        project_id, now = str(uuid.uuid4()), _now()
        scientific_identity_json = _single_task_identity_json(payload)
        identity_provenance = json.loads(scientific_identity_json)["binding_provenance"]
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if (
                initial_candidate is not None
                and initial_candidate.provenance.source_kind == "copy"
            ):
                reference = initial_candidate.provenance.source_ref
                source = conn.execute(
                    "SELECT candidate_revisions.revision, projects.task_id "
                    "FROM candidate_revisions "
                    "JOIN projects ON projects.id=candidate_revisions.project_id "
                    "WHERE candidate_revisions.candidate_id=? "
                    "AND candidate_revisions.project_id=? "
                    "AND candidate_revisions.revision=?",
                    (
                        reference.candidate_id,
                        reference.project_id,
                        reference.candidate_revision,
                    ),
                ).fetchone()
                if source is None:
                    raise CandidateCopyConflictError(
                        "コピー元候補またはrevisionが一致しません"
                    )
                if source["task_id"] != payload.task_id:
                    raise CandidateCopyConflictError(
                        "異なる予測タスクの候補はコピーできません"
                    )
            project_series_id = self._project_series_id_for_create(conn, payload, now)
            conn.execute(
                "INSERT INTO projects(id,name,description,purpose,task_id,target_values,input_ranges,"
                "response_curve_ranges,response_curve_points,heat_stage_positions_m,display_decimals,notes,decision_candidate_id,"
                "decision_snapshot_id,decision_note,dataset_view_revision_id,task_contract_digest,"
                "model_package_ref_id,model_package_manifest_digest,project_series_id,predecessor_project_id,"
                "continuation_reason,binding_provenance,scientific_identity_json,"
                "design_space_json,design_space_digest,design_space_binding_provenance,"
                "objective_definition_json,objective_definition_digest,objective_binding_provenance,"
                "created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    project_id,
                    payload.name,
                    payload.description,
                    payload.purpose,
                    payload.task_id,
                    _target_values_json(payload.target_values),
                    json.dumps(
                        {
                            key: value.model_dump()
                            for key, value in payload.input_ranges.items()
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    json.dumps(
                        {
                            axis: {
                                key: value.model_dump() for key, value in ranges.items()
                            }
                            for axis, ranges in payload.response_curve_ranges.items()
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    payload.response_curve_points,
                    json.dumps(
                        payload.heat_stage_positions_m,
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    json.dumps(
                        payload.display_decimals, ensure_ascii=False, sort_keys=True
                    ),
                    payload.notes,
                    payload.decision_candidate_id,
                    payload.decision_snapshot_id,
                    payload.decision_note,
                    payload.dataset_view_revision_id,
                    payload.task_contract_digest,
                    payload.model_package_ref_id,
                    payload.model_package_manifest_digest,
                    project_series_id,
                    payload.predecessor_project_id,
                    payload.continuation_reason,
                    identity_provenance,
                    scientific_identity_json,
                    (
                        payload.design_space.model_dump_json()
                        if payload.design_space is not None
                        else None
                    ),
                    (
                        payload.design_space.digest
                        if payload.design_space is not None
                        else None
                    ),
                    payload.design_space_binding_provenance or "generated_default",
                    (
                        payload.objective_definition.model_dump_json()
                        if payload.objective_definition is not None
                        else None
                    ),
                    (
                        payload.objective_definition.digest
                        if payload.objective_definition is not None
                        else None
                    ),
                    payload.objective_binding_provenance or "none_configured",
                    now,
                    now,
                ),
            )
            if payload.objective_definition is not None:
                conn.execute(
                    "INSERT INTO project_objective_revisions("
                    "project_id,objective_digest,revision,payload,binding_provenance,created_at"
                    ") VALUES (?,?,?,?,?,?)",
                    (
                        project_id,
                        payload.objective_definition.digest,
                        payload.objective_definition.revision,
                        payload.objective_definition.model_dump_json(),
                        payload.objective_binding_provenance or "generated_default",
                        now,
                    ),
                )
            if initial_candidate is not None:
                candidate_id = str(uuid.uuid4())
                conn.execute(
                    "INSERT INTO candidates(id,project_id,name,payload,created_at,updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        candidate_id,
                        project_id,
                        initial_candidate.name,
                        initial_candidate.model_dump_json(),
                        now,
                        now,
                    ),
                )
                row = conn.execute(
                    "SELECT * FROM candidates WHERE id=?",
                    (candidate_id,),
                ).fetchone()
                self._record_candidate_revision(conn, row)
        return self.get_project(project_id)  # type: ignore[return-value]

    def create_chain_project(
        self,
        payload: ProjectCreateInput,
        identity: ChainProjectIdentity,
        initial_candidate: CandidateInput | None = None,
    ) -> Project:
        revision = self.get_chain_revision(identity.chain_revision_id)
        if (
            revision is None
            or revision.revision_digest != identity.chain_revision_digest
        ):
            raise ChainCatalogConflictError(
                "選択したChain RevisionのIDまたはdigestが登録内容と一致しません"
            )
        project_id, now = str(uuid.uuid4()), _now()
        if (
            initial_candidate is not None
            and initial_candidate.provenance.source_kind != "copy"
        ):
            raise CandidateCopyConflictError(
                "Chain Projectの初期候補はコピー由来にしてください"
            )
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if (
                initial_candidate is not None
                and initial_candidate.provenance.source_kind == "copy"
            ):
                reference = initial_candidate.provenance.source_ref
                source = conn.execute(
                    "SELECT projects.scientific_identity_json,candidates.revision "
                    "FROM candidates JOIN projects ON projects.id=candidates.project_id "
                    "WHERE candidates.id=? AND candidates.project_id=?",
                    (reference.candidate_id, reference.project_id),
                ).fetchone()
                source_identity = (
                    json.loads(source["scientific_identity_json"])
                    if source is not None
                    else {}
                )
                if (
                    source is None
                    or source["revision"] != reference.candidate_revision
                    or source_identity.get("identity_kind") != "chain"
                    or ChainProjectIdentity.model_validate(source_identity) != identity
                ):
                    raise CandidateCopyConflictError(
                        "コピー元候補のChain Revisionまたはcandidate revisionが一致しません"
                    )
            project_series_id = self._project_series_id_for_create(conn, payload, now)
            conn.execute(
                "INSERT INTO projects("
                "id,name,description,purpose,task_id,target_values,input_ranges,"
                "response_curve_ranges,response_curve_points,heat_stage_positions_m,"
                "display_decimals,notes,decision_candidate_id,decision_snapshot_id,"
                "decision_note,dataset_view_revision_id,task_contract_digest,"
                "model_package_ref_id,model_package_manifest_digest,project_series_id,"
                "predecessor_project_id,continuation_reason,binding_provenance,"
                "scientific_identity_json,objective_definition_json,"
                "objective_definition_digest,objective_binding_provenance,"
                "created_at,updated_at"
                ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    project_id,
                    payload.name,
                    payload.description,
                    payload.purpose,
                    "",
                    _target_values_json(payload.target_values),
                    json.dumps(
                        {
                            key: value.model_dump()
                            for key, value in payload.input_ranges.items()
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    json.dumps(
                        {
                            axis: {
                                key: value.model_dump() for key, value in ranges.items()
                            }
                            for axis, ranges in payload.response_curve_ranges.items()
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    payload.response_curve_points,
                    json.dumps(
                        payload.heat_stage_positions_m,
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    json.dumps(
                        payload.display_decimals, ensure_ascii=False, sort_keys=True
                    ),
                    payload.notes,
                    payload.decision_candidate_id,
                    payload.decision_snapshot_id,
                    payload.decision_note,
                    None,
                    "",
                    None,
                    "",
                    project_series_id,
                    payload.predecessor_project_id,
                    payload.continuation_reason,
                    "explicit",
                    identity.model_dump_json(),
                    (
                        payload.objective_definition.model_dump_json()
                        if payload.objective_definition is not None
                        else None
                    ),
                    (
                        payload.objective_definition.digest
                        if payload.objective_definition is not None
                        else None
                    ),
                    payload.objective_binding_provenance or "none_configured",
                    now,
                    now,
                ),
            )
            if payload.objective_definition is not None:
                conn.execute(
                    "INSERT INTO project_objective_revisions("
                    "project_id,objective_digest,revision,payload,"
                    "binding_provenance,created_at"
                    ") VALUES (?,?,?,?,?,?)",
                    (
                        project_id,
                        payload.objective_definition.digest,
                        payload.objective_definition.revision,
                        payload.objective_definition.model_dump_json(),
                        payload.objective_binding_provenance or "generated_default",
                        now,
                    ),
                )
            if initial_candidate is not None:
                candidate_id = str(uuid.uuid4())
                conn.execute(
                    "INSERT INTO candidates(id,project_id,name,payload,created_at,updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        candidate_id,
                        project_id,
                        initial_candidate.name,
                        initial_candidate.model_dump_json(),
                        now,
                        now,
                    ),
                )
        return self.get_project(project_id)  # type: ignore[return-value]

    def update_project(
        self,
        project_id: str,
        payload: ProjectUpdateInput,
        *,
        objective_definition: ObjectiveDefinition | None,
        objective_binding_provenance: str,
    ) -> Project | None:
        now = _now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._validate_decision(
                conn,
                project_id,
                payload.decision_candidate_id,
                payload.decision_snapshot_id,
            )
            result = conn.execute(
                "UPDATE projects SET name=?, description=?, purpose=?, target_values=?, "
                "input_ranges=?, response_curve_ranges=?, response_curve_points=?, "
                "heat_stage_positions_m=?, display_decimals=?, notes=?, decision_candidate_id=?, "
                "decision_snapshot_id=?, decision_note=?, objective_definition_json=?, "
                "objective_definition_digest=?, objective_binding_provenance=?, updated_at=? "
                "WHERE id=?",
                (
                    payload.name,
                    payload.description,
                    payload.purpose,
                    _target_values_json(payload.target_values),
                    json.dumps(
                        {
                            key: value.model_dump()
                            for key, value in payload.input_ranges.items()
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    json.dumps(
                        {
                            axis: {
                                key: value.model_dump() for key, value in ranges.items()
                            }
                            for axis, ranges in payload.response_curve_ranges.items()
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    payload.response_curve_points,
                    json.dumps(
                        payload.heat_stage_positions_m,
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    json.dumps(
                        payload.display_decimals, ensure_ascii=False, sort_keys=True
                    ),
                    payload.notes,
                    payload.decision_candidate_id,
                    payload.decision_snapshot_id,
                    payload.decision_note,
                    objective_definition.model_dump_json()
                    if objective_definition
                    else None,
                    objective_definition.digest if objective_definition else None,
                    objective_binding_provenance,
                    now,
                    project_id,
                ),
            )
            if objective_definition is not None:
                existing_objective = conn.execute(
                    "SELECT payload FROM project_objective_revisions "
                    "WHERE project_id=? AND objective_digest=?",
                    (project_id, objective_definition.digest),
                ).fetchone()
                if existing_objective is None:
                    conn.execute(
                        "INSERT INTO project_objective_revisions("
                        "project_id,objective_digest,revision,payload,binding_provenance,created_at"
                        ") VALUES (?,?,?,?,?,?)",
                        (
                            project_id,
                            objective_definition.digest,
                            objective_definition.revision,
                            objective_definition.model_dump_json(),
                            objective_binding_provenance,
                            now,
                        ),
                    )
                elif (
                    ObjectiveDefinition.model_validate_json(
                        existing_objective["payload"]
                    )
                    != objective_definition
                ):
                    raise StoreDataIntegrityError(
                        "同じObjective digestに異なる定義があります"
                    )
        return self.get_project(project_id) if result.rowcount else None

    def archive_project(self, project_id: str) -> Project | None:
        """Archive a Project and revoke Chain claims in one transaction."""
        if project_id in PROTECTED_PROJECT_IDS:
            raise ProtectedProjectError("予約プロジェクトはアーカイブできません")
        now = _now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            project_row = conn.execute(
                "SELECT project_series_id,archived_at FROM projects WHERE id=?",
                (project_id,),
            ).fetchone()
            if project_row is None:
                return None
            if project_row["archived_at"] is None:
                # Revocation and archive state are indivisible: a writer may
                # never observe an archived Project with a live claim.
                conn.execute(
                    "DELETE FROM chain_execution_claims WHERE scope_id LIKE ?",
                    (f"{project_id}:%",),
                )
                conn.execute(
                    "UPDATE projects SET archived_at=?,updated_at=? WHERE id=?",
                    (now, now, project_id),
                )
                series_id = project_row["project_series_id"]
                if series_id:
                    conn.execute(
                        "UPDATE project_series SET archived_at=?,updated_at=? "
                        "WHERE id=? AND archived_at IS NULL "
                        "AND NOT EXISTS ("
                        "SELECT 1 FROM projects "
                        "WHERE projects.project_series_id=project_series.id "
                        "AND projects.archived_at IS NULL"
                        ")",
                        (now, now, series_id),
                    )
            row = conn.execute(
                "SELECT * FROM projects WHERE id=?", (project_id,)
            ).fetchone()
        return self._project(row)

    def purge_project(self, project_id: str) -> bool:
        if project_id in PROTECTED_PROJECT_IDS:
            raise ProtectedProjectError("予約プロジェクトは完全削除できません")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            project_row = conn.execute(
                "SELECT project_series_id,archived_at FROM projects WHERE id=?",
                (project_id,),
            ).fetchone()
            if project_row is None:
                return False
            if project_row["archived_at"] is None:
                raise ActiveProjectPurgeError(
                    "完全削除する前にプロジェクトをアーカイブしてください"
                )
            successor = conn.execute(
                "SELECT id FROM projects WHERE predecessor_project_id=? LIMIT 1",
                (project_id,),
            ).fetchone()
            if successor is not None:
                raise ProjectHasSuccessorsError(
                    "後続プロジェクトがあるため削除できません。続き元の関係を保持してください"
                )
            for derived_row in conn.execute(
                "SELECT candidate_id AS id,payload FROM candidate_revisions "
                "WHERE project_id<>?",
                (project_id,),
            ):
                try:
                    payload = json.loads(derived_row["payload"])
                except (TypeError, json.JSONDecodeError) as exc:
                    raise StoreDataIntegrityError(
                        f"候補 {derived_row['id']} の派生元を確認できません"
                    ) from exc
                if isinstance(
                    payload, dict
                ) and self._candidate_provenance_references_project(
                    conn, payload, project_id
                ):
                    raise ProjectHasDerivedCandidatesError(
                        "派生候補が別のプロジェクトにある、またはこのProjectの"
                        "証跡を参照する候補revisionがあるため完全削除できません"
                    )
            candidate_ids = [
                row["id"]
                for row in conn.execute(
                    "SELECT id FROM candidates WHERE project_id=?", (project_id,)
                ).fetchall()
            ]
            conn.execute(
                "INSERT INTO project_purge_authorizations(project_id) VALUES (?)",
                (project_id,),
            )
            if candidate_ids:
                placeholders = ",".join("?" for _ in candidate_ids)
                for table in PROJECT_PERSISTENCE.candidate_tables:
                    conn.execute(
                        f"DELETE FROM {table} WHERE candidate_id IN ({placeholders})",
                        candidate_ids,
                    )
            for table in PROJECT_PERSISTENCE.scope_tables:
                conn.execute(
                    f"DELETE FROM {table} WHERE scope_id LIKE ?",
                    (f"{project_id}:%",),
                )
            for table in PROJECT_PERSISTENCE.direct_tables:
                column = "id" if table == "projects" else "project_id"
                conn.execute(
                    f"DELETE FROM {table} WHERE {column}=?",
                    (project_id,),
                )
            conn.execute(
                "DELETE FROM project_purge_authorizations WHERE project_id=?",
                (project_id,),
            )
            series_id = project_row["project_series_id"]
            if series_id:
                now = _now()
                conn.execute(
                    "UPDATE project_series SET archived_at=?,updated_at=? "
                    "WHERE id=? AND archived_at IS NULL "
                    "AND NOT EXISTS ("
                    "SELECT 1 FROM projects "
                    "WHERE projects.project_series_id=project_series.id "
                    "AND projects.archived_at IS NULL"
                    ")",
                    (now, now, series_id),
                )
            return True

    def project_has_persisted_evidence(self, project_id: str) -> bool:
        """Return whether removing a starter would discard user-created evidence.

        Project, Candidate, and Candidate Revision baseline rows are checked by
        the caller against the current starter definition. Every other
        Project-owned record is evidence and makes removal unsafe.
        """
        baseline_tables = {
            "projects",
            "candidates",
            "candidate_revisions",
        }
        with self._connect() as conn:
            candidate_ids = [
                row["id"]
                for row in conn.execute(
                    "SELECT id FROM candidates WHERE project_id=?",
                    (project_id,),
                ).fetchall()
            ]
            for table in PROJECT_PERSISTENCE.direct_tables:
                if table in baseline_tables:
                    continue
                row = conn.execute(
                    f"SELECT 1 FROM {table} WHERE project_id=? LIMIT 1",
                    (project_id,),
                ).fetchone()
                if row is not None:
                    return True
            if candidate_ids:
                placeholders = ",".join("?" for _ in candidate_ids)
                for table in PROJECT_PERSISTENCE.candidate_tables:
                    row = conn.execute(
                        f"SELECT 1 FROM {table} "
                        f"WHERE candidate_id IN ({placeholders}) LIMIT 1",
                        candidate_ids,
                    ).fetchone()
                    if row is not None:
                        return True
        return False

    def update_project_decision(
        self, project_id: str, candidate_id: str, snapshot_id: str, note: str
    ) -> Project | None:
        now = _now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._validate_decision(conn, project_id, candidate_id, snapshot_id)
            result = conn.execute(
                "UPDATE projects SET decision_candidate_id=?, decision_snapshot_id=?, decision_note=?, updated_at=? WHERE id=?",
                (candidate_id, snapshot_id, note, now, project_id),
            )
        return self.get_project(project_id) if result.rowcount else None

    def project_history(self, project_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            conn.execute("BEGIN")
            project_row = conn.execute(
                "SELECT * FROM projects WHERE id=?", (project_id,)
            ).fetchone()
            if project_row is None:
                return None
            candidate_rows = conn.execute(
                "SELECT * FROM candidates WHERE project_id=? ORDER BY updated_at DESC, created_at DESC",
                (project_id,),
            ).fetchall()
            snapshot_rows = conn.execute(
                "SELECT snapshots.* FROM snapshots JOIN candidates ON candidates.id=snapshots.candidate_id "
                "WHERE candidates.project_id=? ORDER BY snapshots.created_at DESC",
                (project_id,),
            ).fetchall()
            actual_rows = conn.execute(
                "SELECT actual_measurements.* FROM actual_measurements "
                "JOIN candidates ON candidates.id=actual_measurements.candidate_id "
                "WHERE candidates.project_id=? ORDER BY actual_measurements.created_at DESC",
                (project_id,),
            ).fetchall()
            chain_snapshot_rows = conn.execute(
                "SELECT * FROM chain_snapshot_records "
                "WHERE project_id=? ORDER BY created_at DESC,id DESC",
                (project_id,),
            ).fetchall()
            chain_variant_rows = conn.execute(
                "SELECT * FROM chain_analysis_variant_records "
                "WHERE project_id=? ORDER BY created_at DESC,id DESC",
                (project_id,),
            ).fetchall()
            chain_distribution_rows = conn.execute(
                "SELECT * FROM chain_distribution_runs "
                "WHERE project_id=? ORDER BY created_at DESC,id DESC",
                (project_id,),
            ).fetchall()

        project = self._project(project_row)
        candidate_ids = {row["id"] for row in candidate_rows}
        chain_identity = (
            project.scientific_identity
            if project.scientific_identity.identity_kind == "chain"
            else None
        )
        if chain_identity is None and (
            chain_snapshot_rows or chain_variant_rows or chain_distribution_rows
        ):
            raise StoreDataIntegrityError(
                "single-Task ProjectにChain証拠が混在しています"
            )
        if chain_identity is not None and snapshot_rows:
            raise StoreDataIntegrityError(
                "Chain Projectにsingle-Task Snapshotが混在しています"
            )
        snapshots_by_candidate: dict[str, list[dict[str, Any]]] = {}
        for row in snapshot_rows:
            try:
                payload = json.loads(row["payload"])
            except (TypeError, json.JSONDecodeError) as exc:
                raise StoreDataIntegrityError(
                    f"snapshot {row['id']} を読み取れません"
                ) from exc
            version = (
                payload.get("snapshot_schema_version")
                if isinstance(payload, dict)
                else None
            )
            if version != "prediction-snapshot-v2":
                raise StoreDataIntegrityError(
                    f"snapshot {row['id']} の形式を解釈できません"
                )
            raw_candidate = payload.get("raw_candidate")
            candidate_revision = (
                raw_candidate.get("revision")
                if version == "prediction-snapshot-v2"
                and isinstance(raw_candidate, dict)
                else None
            )
            prediction = payload.get("prediction")
            if not isinstance(prediction, dict) or not isinstance(
                prediction.get("predictions"), dict
            ):
                raise StoreDataIntegrityError(
                    f"snapshot {row['id']} の予測要約を読み取れません"
                )
            snapshots_by_candidate.setdefault(row["candidate_id"], []).append(
                {
                    "id": row["id"],
                    "candidate_id": row["candidate_id"],
                    "created_at": row["created_at"],
                    "candidate_revision": candidate_revision,
                    "prediction_summary": prediction["predictions"],
                    "model_ref": payload.get("provenance"),
                }
            )
        actuals_by_candidate: dict[str, list[ActualMeasurement]] = {}
        for row in actual_rows:
            snapshot_ids = {
                item["id"]
                for item in snapshots_by_candidate.get(row["candidate_id"], [])
            }
            if row["snapshot_id"] not in snapshot_ids:
                raise StoreDataIntegrityError(
                    f"actual {row['id']} の固定snapshotが見つかりません"
                )
            actuals_by_candidate.setdefault(row["candidate_id"], []).append(
                self._actual(row)
            )
        chain_snapshots_by_candidate: dict[str, list[ChainSnapshot]] = {}
        chain_snapshots_by_id: dict[str, ChainSnapshot] = {}
        for row in chain_snapshot_rows:
            try:
                snapshot = ChainSnapshot.model_validate_json(row["payload_json"])
            except (TypeError, ValueError) as exc:
                raise StoreDataIntegrityError(
                    f"Chain snapshot {row['id']} を読み取れません"
                ) from exc
            if (
                snapshot.snapshot_id != row["id"]
                or row["project_id"] != project.id
                or snapshot.identity.candidate_id != row["candidate_id"]
                or snapshot.identity.candidate_revision != row["candidate_revision"]
                or row["candidate_id"] not in candidate_ids
                or chain_identity is None
                or snapshot.identity.chain_revision_id
                != chain_identity.chain_revision_id
                or snapshot.identity.chain_revision_digest
                != chain_identity.chain_revision_digest
            ):
                raise StoreDataIntegrityError(
                    f"Chain snapshot {row['id']} の固定参照が一致しません"
                )
            chain_snapshots_by_candidate.setdefault(row["candidate_id"], []).append(
                snapshot
            )
            chain_snapshots_by_id[snapshot.snapshot_id] = snapshot
        chain_variants_by_candidate: dict[str, list[ActualConditionedVariant]] = {}
        for row in chain_variant_rows:
            try:
                variant = ActualConditionedVariant.model_validate_json(
                    row["payload_json"]
                )
            except (TypeError, ValueError) as exc:
                raise StoreDataIntegrityError(
                    f"実測variant {row['id']} を読み取れません"
                ) from exc
            comparison_snapshot = chain_snapshots_by_id.get(
                variant.identity.comparison_snapshot_id
            )
            if (
                variant.variant_id != row["id"]
                or variant.project_id != row["project_id"]
                or row["project_id"] != project.id
                or variant.identity.base_candidate_id != row["candidate_id"]
                or variant.identity.base_candidate_revision != row["candidate_revision"]
                or variant.identity.comparison_snapshot_id
                != row["comparison_snapshot_id"]
                or row["candidate_id"] not in candidate_ids
                or chain_identity is None
                or variant.identity.base_chain_revision_id
                != chain_identity.chain_revision_id
                or variant.identity.base_chain_revision_digest
                != chain_identity.chain_revision_digest
                or comparison_snapshot is None
                or comparison_snapshot.identity.candidate_id
                != variant.identity.base_candidate_id
                or comparison_snapshot.identity.candidate_revision
                != variant.identity.base_candidate_revision
            ):
                raise StoreDataIntegrityError(
                    f"実測variant {row['id']} の固定参照が一致しません"
                )
            chain_variants_by_candidate.setdefault(row["candidate_id"], []).append(
                variant
            )
        chain_distributions_by_candidate: dict[str, list[ChainDistributionRun]] = {}
        for row in chain_distribution_rows:
            try:
                run = ChainDistributionRun.model_validate_json(row["payload_json"])
            except (TypeError, ValueError) as exc:
                raise StoreDataIntegrityError(
                    f"Chain分布run {row['id']} を読み取れません"
                ) from exc
            if (
                run.run_id != row["id"]
                or run.project_id != row["project_id"]
                or row["project_id"] != project.id
                or run.provenance.candidate_id != row["candidate_id"]
                or run.provenance.candidate_revision != row["candidate_revision"]
                or row["candidate_id"] not in candidate_ids
                or chain_identity is None
                or run.provenance.chain_revision_id != chain_identity.chain_revision_id
                or run.provenance.chain_revision_digest != row["chain_revision_digest"]
                or run.provenance.chain_revision_digest
                != chain_identity.chain_revision_digest
            ):
                raise StoreDataIntegrityError(
                    f"Chain分布run {row['id']} の固定参照が一致しません"
                )
            chain_distributions_by_candidate.setdefault(row["candidate_id"], []).append(
                run
            )
        items = []
        for row in candidate_rows:
            candidate = self._candidate(row)
            decision = None
            if (
                project.decision_candidate_id == candidate.id
                and project.decision_snapshot_id
            ):
                fixed_snapshot_ids = (
                    {
                        item.snapshot_id
                        for item in chain_snapshots_by_candidate.get(candidate.id, [])
                    }
                    if chain_identity is not None
                    else {
                        item["id"]
                        for item in snapshots_by_candidate.get(candidate.id, [])
                    }
                )
                if project.decision_snapshot_id not in fixed_snapshot_ids:
                    raise StoreDataIntegrityError(
                        "採用判断の固定snapshotが見つかりません"
                    )
                decision = {
                    "candidate_id": candidate.id,
                    "snapshot_id": project.decision_snapshot_id,
                    "note": project.decision_note,
                }
            items.append(
                {
                    "candidate": candidate,
                    "current": {
                        "revision": candidate.revision,
                        "updated_at": candidate.updated_at,
                    },
                    "snapshots": snapshots_by_candidate.get(candidate.id, []),
                    "chain_snapshots": chain_snapshots_by_candidate.get(
                        candidate.id, []
                    ),
                    "chain_analysis_variants": chain_variants_by_candidate.get(
                        candidate.id, []
                    ),
                    "chain_distribution_runs": chain_distributions_by_candidate.get(
                        candidate.id, []
                    ),
                    "actuals": actuals_by_candidate.get(candidate.id, []),
                    "decision": decision,
                }
            )
        return {"project": project, "candidates": items}

    def update_chain_candidate(
        self,
        candidate_id: str,
        project_id: str,
        payload: CandidateInput,
        expected_revision: int,
        invalidation_request_id: str,
    ) -> tuple[Candidate | None, int]:
        """Update a Chain candidate and invalidate older execution writes atomically."""

        now = _now()
        scope_id = self.chain_execution_scope(project_id, candidate_id)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            result = conn.execute(
                "UPDATE candidates SET name=?,payload=?,revision=revision+1,updated_at=? "
                "WHERE id=? AND project_id=? AND revision=? AND archived_at IS NULL",
                (
                    payload.name,
                    payload.model_dump_json(),
                    now,
                    candidate_id,
                    project_id,
                    expected_revision,
                ),
            )
            if not result.rowcount:
                row = conn.execute(
                    "SELECT * FROM candidates WHERE id=? AND project_id=?",
                    (candidate_id, project_id),
                ).fetchone()
                if row is None:
                    return None, 0
                current = self._candidate(row)
                if current.archived_at is not None:
                    raise CandidateArchivedError("archive済み候補は編集できません")
                raise CandidateRevisionConflictError(current)
            row = conn.execute(
                "SELECT * FROM candidates WHERE id=? AND project_id=?",
                (candidate_id, project_id),
            ).fetchone()
            self._record_candidate_revision(conn, row)
            generation = self._claim_chain_execution(
                conn, scope_id, invalidation_request_id
            )
            updated = self._candidate(row)
        return updated, generation

    def insert_chain_snapshot(
        self, project_id: str, snapshot: ChainSnapshot
    ) -> ChainSnapshot:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            candidate = conn.execute(
                "SELECT * FROM candidates WHERE id=? AND project_id=?",
                (
                    snapshot.identity.candidate_id,
                    project_id,
                ),
            ).fetchone()
            if candidate is None:
                raise StoreDataIntegrityError(
                    "Chain snapshotのcandidateが見つかりません"
                )
            current = self._candidate(candidate)
            if (
                current.archived_at is not None
                or current.revision != snapshot.identity.candidate_revision
            ):
                raise CandidateRevisionConflictError(current)
            conn.execute(
                "INSERT INTO chain_snapshot_records("
                "id,project_id,candidate_id,candidate_revision,identity_json,payload_json,created_at"
                ") VALUES (?,?,?,?,?,?,?)",
                (
                    snapshot.snapshot_id,
                    project_id,
                    snapshot.identity.candidate_id,
                    snapshot.identity.candidate_revision,
                    snapshot.identity.model_dump_json(),
                    snapshot.model_dump_json(),
                    snapshot.created_at.isoformat(),
                ),
            )
        return snapshot

    def create_ai_review_run(self, run: AiReviewRun) -> AiReviewRun:
        if run.state != "running":
            raise ValueError("new AI review run must start in running state")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            candidate = conn.execute(
                "SELECT revision FROM candidates WHERE id=? AND project_id=?",
                (run.candidate_id, run.project_id),
            ).fetchone()
            if candidate is None:
                raise ProjectNotFoundError(run.project_id)
            if int(candidate["revision"]) != run.provenance.reviewed_candidate_revision:
                raise CandidateRevisionConflictError(
                    self.get_candidate(run.candidate_id, run.project_id)  # type: ignore[arg-type]
                )
            conn.execute(
                "INSERT INTO ai_review_runs("
                "review_run_id,project_id,candidate_id,candidate_revision,state,"
                "payload,started_at,completed_at) VALUES (?,?,?,?,?,?,?,NULL)",
                (
                    run.review_run_id,
                    run.project_id,
                    run.candidate_id,
                    run.provenance.reviewed_candidate_revision,
                    run.state,
                    run.model_dump_json(),
                    run.started_at.isoformat(),
                ),
            )
        return run

    def create_snapshot_and_actual(
        self,
        project_id: str,
        candidate_id: str,
        expected_revision: int,
        snapshot_payload: dict[str, Any],
        payload: ActualMeasurementInput,
    ) -> ActualMeasurement:
        snapshot_id, actual_id, now = str(uuid.uuid4()), str(uuid.uuid4()), _now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            candidate_row = conn.execute(
                "SELECT * FROM candidates WHERE id=? AND project_id=?",
                (candidate_id, project_id),
            ).fetchone()
            if candidate_row is None:
                if (
                    conn.execute(
                        "SELECT 1 FROM projects WHERE id=?", (project_id,)
                    ).fetchone()
                    is None
                ):
                    raise ProjectNotFoundError(project_id)
                raise StoreDataIntegrityError("候補が実測の保存前に削除されました")
            current = self._candidate(candidate_row)
            if current.archived_at is not None:
                raise CandidateArchivedError("archive済み候補には実測を追加できません")
            if current.revision != expected_revision:
                raise CandidateRevisionConflictError(current)
            conn.execute(
                "INSERT INTO snapshots VALUES (?, ?, ?, ?)",
                (
                    snapshot_id,
                    candidate_id,
                    json.dumps(snapshot_payload, ensure_ascii=False, sort_keys=True),
                    now,
                ),
            )
            conn.execute(
                "INSERT INTO actual_measurements(id, candidate_id, snapshot_id, property, mean, std, replicates, unit, experiment_no, measured_at, note, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    actual_id,
                    candidate_id,
                    snapshot_id,
                    payload.property,
                    payload.mean,
                    payload.std,
                    payload.replicates,
                    payload.unit,
                    payload.experiment_no,
                    payload.measured_at.isoformat() if payload.measured_at else None,
                    payload.note,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM actual_measurements WHERE id=?",
                (actual_id,),
            ).fetchone()
            if row is None:
                raise StoreDataIntegrityError("作成した実測を再取得できませんでした")
            actual = self._actual(row)
        return actual
