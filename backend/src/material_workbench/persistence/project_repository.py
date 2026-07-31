from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime
from typing import Any, Mapping
from material_workbench.contracts.chain_contracts import (
    SingleTaskProjectIdentity,
)
from material_workbench.contracts.candidate_project_contracts import (
    Project,
    ProjectCreateInput,
    ProjectGroupMoveInput,
    ProjectInput,
)
from material_workbench.contracts.objective_contracts import (
    ObjectiveDefinition,
    ObjectiveDefinitionRevision,
)
from material_workbench.persistence.store_support import (
    PROTECTED_PROJECT_IDS,
    ProjectGroupConflictError,
    ProjectGroupUnavailableError,
    ProjectNotFoundError,
    ProtectedProjectError,
    _now,
    _target_values_json,
)


class ProjectRepository:
    @staticmethod
    def _project(row: sqlite3.Row) -> Project:
        return Project(
            id=row["id"],
            name=row["name"],
            description=row["description"],
            purpose=row["purpose"],
            task_id=row["task_id"],
            scientific_identity=json.loads(row["scientific_identity_json"]),
            target_values=json.loads(row["target_values"]),
            input_ranges=json.loads(row["input_ranges"]),
            response_curve_ranges=json.loads(row["response_curve_ranges"]),
            response_curve_points=row["response_curve_points"],
            heat_stage_positions_m=json.loads(row["heat_stage_positions_m"]),
            display_decimals=json.loads(row["display_decimals"]),
            notes=row["notes"],
            decision_candidate_id=row["decision_candidate_id"],
            decision_snapshot_id=row["decision_snapshot_id"],
            decision_note=row["decision_note"],
            dataset_view_revision_id=row["dataset_view_revision_id"],
            task_contract_digest=row["task_contract_digest"],
            model_package_ref_id=row["model_package_ref_id"],
            model_package_manifest_digest=row["model_package_manifest_digest"],
            project_series_id=row["project_series_id"],
            predecessor_project_id=row["predecessor_project_id"],
            continuation_reason=row["continuation_reason"],
            starter=bool(row["is_starter"]),
            design_space=(
                json.loads(row["design_space_json"])
                if row["design_space_json"]
                else None
            ),
            design_space_digest=row["design_space_digest"],
            design_space_binding_provenance=row["design_space_binding_provenance"],
            objective_definition=(
                json.loads(row["objective_definition_json"])
                if row["objective_definition_json"]
                else None
            ),
            objective_definition_digest=row["objective_definition_digest"],
            objective_binding_provenance=row["objective_binding_provenance"],
            binding_provenance=row["binding_provenance"],
            binding_migrated_at=(
                datetime.fromisoformat(row["binding_migrated_at"])
                if row["binding_migrated_at"]
                else None
            ),
            archived_at=(
                datetime.fromisoformat(row["archived_at"])
                if row["archived_at"]
                else None
            ),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def list_projects(self, *, include_archived: bool = False) -> list[Project]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM projects "
                + ("" if include_archived else "WHERE archived_at IS NULL ")
                + "ORDER BY created_at, id"
            ).fetchall()
        return [self._project(row) for row in rows]

    def get_project(
        self,
        project_id: str = "default",
        *,
        include_archived: bool = False,
    ) -> Project | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM projects WHERE id = ?"
                + ("" if include_archived else " AND archived_at IS NULL"),
                (project_id,),
            ).fetchone()
        return self._project(row) if row else None

    @staticmethod
    def _project_series_id_for_create(
        conn: sqlite3.Connection,
        payload: ProjectCreateInput,
        now: str,
    ) -> str | None:
        if payload.new_project_series is not None:
            series_id = f"project-series-{uuid.uuid4()}"
            conn.execute(
                "INSERT INTO project_series(id,name,description,created_at,updated_at) "
                "VALUES (?,?,?,?,?)",
                (
                    series_id,
                    payload.new_project_series.name,
                    payload.new_project_series.description,
                    now,
                    now,
                ),
            )
            return series_id
        if payload.project_series_id is None:
            return None
        available = conn.execute(
            "SELECT id FROM project_series WHERE id=? AND archived_at IS NULL",
            (payload.project_series_id,),
        ).fetchone()
        if available is None:
            raise ProjectGroupUnavailableError("選択した検討グループを利用できません")
        return payload.project_series_id

    def ensure_project(
        self,
        project_id: str,
        payload: ProjectInput,
        *,
        starter: bool = False,
    ) -> Project:
        existing = self.get_project(project_id)
        if existing is not None:
            if existing.task_id != payload.task_id:
                raise ValueError(
                    f"reserved project {project_id} belongs to another task"
                )
            if starter and not existing.starter:
                with self._connect() as conn:
                    conn.execute(
                        "UPDATE projects SET is_starter=1 WHERE id=?",
                        (project_id,),
                    )
                return self.get_project(project_id)  # type: ignore[return-value]
            return existing
        now = _now()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO projects(id, name, description, purpose, task_id, target_values, input_ranges, response_curve_ranges, response_curve_points, heat_stage_positions_m, display_decimals, notes, decision_candidate_id, decision_snapshot_id, decision_note, scientific_identity_json, is_starter, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                    "",
                    "",
                    "",
                    SingleTaskProjectIdentity(
                        identity_kind="single_task",
                        task_id=payload.task_id,
                        binding_provenance="unbound_legacy",
                    ).model_dump_json(),
                    int(starter),
                    now,
                    now,
                ),
            )
        return self.get_project(project_id)  # type: ignore[return-value]

    def list_project_objective_revisions(
        self,
        project_id: str,
    ) -> list[ObjectiveDefinitionRevision]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT project_id,objective_digest,revision,payload,"
                "binding_provenance,created_at "
                "FROM project_objective_revisions WHERE project_id=? "
                "ORDER BY revision",
                (project_id,),
            ).fetchall()
        return [
            ObjectiveDefinitionRevision(
                project_id=row["project_id"],
                objective_definition=ObjectiveDefinition.model_validate_json(
                    row["payload"]
                ),
                objective_definition_digest=row["objective_digest"],
                binding_provenance=row["binding_provenance"],
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in rows
        ]

    def move_project_to_group(
        self, project_id: str, payload: ProjectGroupMoveInput
    ) -> Project:
        now = _now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            project_row = conn.execute(
                "SELECT project_series_id FROM projects WHERE id=?", (project_id,)
            ).fetchone()
            if project_row is None:
                raise ProjectNotFoundError(project_id)
            current_group_id = project_row["project_series_id"]
            if current_group_id != payload.expected_project_series_id:
                raise ProjectGroupConflictError(
                    "このプロジェクトの所属グループは別の操作で変更されています"
                )
            if payload.project_series_id is not None:
                target_group = conn.execute(
                    "SELECT archived_at FROM project_series WHERE id=?",
                    (payload.project_series_id,),
                ).fetchone()
                if target_group is None or target_group["archived_at"] is not None:
                    raise ProjectGroupUnavailableError(
                        "移動先の検討グループを利用できません"
                    )
            if current_group_id == payload.project_series_id:
                return self._project(
                    conn.execute(
                        "SELECT * FROM projects WHERE id=?", (project_id,)
                    ).fetchone()
                )
            conn.execute(
                "UPDATE projects SET project_series_id=?,updated_at=? WHERE id=?",
                (payload.project_series_id, now, project_id),
            )
            if current_group_id:
                conn.execute(
                    "UPDATE project_series SET archived_at=?,updated_at=? "
                    "WHERE id=? AND archived_at IS NULL "
                    "AND NOT EXISTS (SELECT 1 FROM projects WHERE project_series_id=?)",
                    (now, now, current_group_id, current_group_id),
                )
            row = conn.execute(
                "SELECT * FROM projects WHERE id=?", (project_id,)
            ).fetchone()
        return self._project(row)

    def restore_project(self, project_id: str) -> Project | None:
        if project_id in PROTECTED_PROJECT_IDS:
            raise ProtectedProjectError("予約プロジェクトは復元操作の対象外です")
        now = _now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM projects WHERE id=?", (project_id,)
            ).fetchone()
            if row is None:
                return None
            if row["archived_at"] is not None:
                conn.execute(
                    "UPDATE projects SET archived_at=NULL,updated_at=? WHERE id=?",
                    (now, project_id),
                )
                if row["project_series_id"]:
                    conn.execute(
                        "UPDATE project_series SET archived_at=NULL,updated_at=? "
                        "WHERE id=?",
                        (now, row["project_series_id"]),
                    )
            restored = conn.execute(
                "SELECT * FROM projects WHERE id=?", (project_id,)
            ).fetchone()
        return self._project(restored)

    @staticmethod
    def _candidate_provenance_references_project(
        conn: sqlite3.Connection,
        payload: Mapping[str, Any],
        project_id: str,
    ) -> bool:
        provenance = payload.get("provenance")
        if not isinstance(provenance, dict):
            return False
        source_kind = provenance.get("source_kind")
        source_ref = provenance.get("source_ref")
        if not isinstance(source_ref, dict):
            return False
        if source_kind in {"copy", "blend_optimization"}:
            return source_ref.get("project_id") == project_id
        if source_kind == "screening":
            row = conn.execute(
                "SELECT project_id FROM screening_runs WHERE id=?",
                (source_ref.get("run_id"),),
            ).fetchone()
            return row is not None and row["project_id"] == project_id
        if source_kind == "snapshot":
            row = conn.execute(
                "SELECT candidates.project_id FROM snapshots "
                "JOIN candidates ON candidates.id=snapshots.candidate_id "
                "WHERE snapshots.id=?",
                (source_ref.get("snapshot_id"),),
            ).fetchone()
            return row is not None and row["project_id"] == project_id
        if source_kind == "decision_activity":
            row = conn.execute(
                "SELECT project_id FROM decision_activity_runs WHERE id=?",
                (source_ref.get("run_id"),),
            ).fetchone()
            return row is not None and row["project_id"] == project_id
        return False
