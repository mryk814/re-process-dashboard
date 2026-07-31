from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from psycopg import Connection, connect
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from decision_workbench.shared_lab.config import SharedLabConfig
from decision_workbench.shared_lab.contracts import (
    ActivityRun,
    ActivityRunCreate,
    Actor,
    ArtifactReference,
    AuditEvent,
    CandidateCreate,
    CandidateRevision,
    CandidateUpdate,
    Project,
    ProjectCreate,
)


@dataclass(frozen=True)
class RequestIdentity:
    workspace_id: str
    actor: Actor
    request_id: str
    correlation_id: str


class SharedLabError(RuntimeError):
    code = "persistence_unavailable"


class SharedResourceNotFound(SharedLabError):
    code = "not_found"


class SharedIdentityInvalid(SharedLabError):
    code = "identity_invalid"


class SharedCapabilityDenied(SharedLabError):
    code = "capability_denied"


class SharedRevisionConflict(SharedLabError):
    code = "revision_conflict"

    def __init__(self, current: CandidateRevision):
        super().__init__(f"candidate is already at revision {current.revision}")
        self.current = current


def _event_id() -> str:
    return f"audit-{uuid4().hex}"


class SharedLabRepository:
    """Small PostgreSQL repository for the isolated collaboration experiment."""

    def __init__(
        self,
        config: SharedLabConfig,
        connection_factory: Callable[[], Connection[Any]] | None = None,
    ):
        self.config = config
        self._connection_factory = connection_factory or (
            lambda: connect(config.database_url, row_factory=dict_row)
        )

    def _connection(self) -> Connection[Any]:
        return self._connection_factory()

    @staticmethod
    def _require(identity: RequestIdentity, capability: str) -> None:
        if capability not in identity.actor.capabilities:
            raise SharedCapabilityDenied(
                f"actor does not have the required {capability} capability"
            )

    def resolve_identity(
        self,
        workspace_id: str,
        actor_id: str,
        request_id: str,
        correlation_id: str,
    ) -> RequestIdentity:
        if (
            workspace_id != self.config.allowed_workspace_id
            or actor_id not in self.config.allowed_actor_ids
        ):
            raise SharedIdentityInvalid("workspace or actor is not enabled for this shared lab")
        with self._connection() as conn:
            row = conn.execute(
                """
                SELECT actor_id, actor_kind, label, workspace_id, capabilities, created_at
                FROM workbench_shared.actors
                WHERE workspace_id=%s AND actor_id=%s
                """,
                (workspace_id, actor_id),
            ).fetchone()
        if row is None:
            raise SharedIdentityInvalid("workspace or actor is not seeded for this shared lab")
        return RequestIdentity(
            workspace_id=workspace_id,
            actor=Actor.model_validate(row),
            request_id=request_id,
            correlation_id=correlation_id,
        )

    @staticmethod
    def _candidate(row: dict[str, Any]) -> CandidateRevision:
        return CandidateRevision.model_validate(row)

    @staticmethod
    def _audit(
        conn: Connection[Any],
        identity: RequestIdentity,
        *,
        project_id: str | None,
        target_type: str,
        target_id: str,
        operation: str,
        outcome: str = "succeeded",
        expected_revision: int | None = None,
        resulting_revision: int | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        conn.execute(
            """
            INSERT INTO workbench_shared.audit_events(
                event_id, workspace_id, actor_id, project_id, target_type,
                target_id, operation, outcome, expected_revision,
                resulting_revision, request_id, correlation_id, detail
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                _event_id(),
                identity.workspace_id,
                identity.actor.actor_id,
                project_id,
                target_type,
                target_id,
                operation,
                outcome,
                expected_revision,
                resulting_revision,
                identity.request_id,
                identity.correlation_id,
                Jsonb(detail or {}),
            ),
        )

    def create_project(
        self, identity: RequestIdentity, payload: ProjectCreate
    ) -> Project:
        self._require(identity, "project:write")
        with self._connection() as conn:
            row = conn.execute(
                """
                INSERT INTO workbench_shared.projects(
                    project_id, workspace_id, name, created_by
                ) VALUES (%s,%s,%s,%s)
                RETURNING project_id, workspace_id, name, created_by, created_at
                """,
                (
                    payload.project_id,
                    identity.workspace_id,
                    payload.name,
                    identity.actor.actor_id,
                ),
            ).fetchone()
            self._audit(
                conn,
                identity,
                project_id=payload.project_id,
                target_type="project",
                target_id=payload.project_id,
                operation="project.created",
            )
        return Project.model_validate(row)

    def list_projects(self, identity: RequestIdentity) -> list[Project]:
        self._require(identity, "project:read")
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT project_id, workspace_id, name, created_by, created_at
                FROM workbench_shared.projects
                WHERE workspace_id=%s
                ORDER BY created_at, project_id
                """,
                (identity.workspace_id,),
            ).fetchall()
        return [Project.model_validate(row) for row in rows]

    def get_project(self, identity: RequestIdentity, project_id: str) -> Project:
        self._require(identity, "project:read")
        with self._connection() as conn:
            row = conn.execute(
                """
                SELECT project_id, workspace_id, name, created_by, created_at
                FROM workbench_shared.projects
                WHERE workspace_id=%s AND project_id=%s
                """,
                (identity.workspace_id, project_id),
            ).fetchone()
        if row is None:
            raise SharedResourceNotFound("project was not found in this workspace")
        return Project.model_validate(row)

    def create_candidate(
        self,
        identity: RequestIdentity,
        project_id: str,
        payload: CandidateCreate,
    ) -> CandidateRevision:
        self._require(identity, "candidate:write")
        with self._connection() as conn:
            project = conn.execute(
                """
                SELECT 1 FROM workbench_shared.projects
                WHERE workspace_id=%s AND project_id=%s
                """,
                (identity.workspace_id, project_id),
            ).fetchone()
            if project is None:
                raise SharedResourceNotFound("project was not found in this workspace")
            conn.execute(
                """
                INSERT INTO workbench_shared.candidates(
                    project_id, candidate_id, current_revision, name, created_by
                ) VALUES (%s,%s,1,%s,%s)
                """,
                (
                    project_id,
                    payload.candidate_id,
                    payload.name,
                    identity.actor.actor_id,
                ),
            )
            row = conn.execute(
                """
                INSERT INTO workbench_shared.candidate_revisions(
                    project_id, candidate_id, revision, name, payload, created_by
                ) VALUES (%s,%s,1,%s,%s,%s)
                RETURNING project_id, candidate_id, revision, name, payload,
                          created_by, created_at
                """,
                (
                    project_id,
                    payload.candidate_id,
                    payload.name,
                    Jsonb(payload.payload),
                    identity.actor.actor_id,
                ),
            ).fetchone()
            self._audit(
                conn,
                identity,
                project_id=project_id,
                target_type="candidate",
                target_id=payload.candidate_id,
                operation="candidate.revision_created",
                resulting_revision=1,
            )
        return self._candidate(row)

    def get_candidate(
        self, identity: RequestIdentity, project_id: str, candidate_id: str
    ) -> CandidateRevision:
        self._require(identity, "candidate:read")
        with self._connection() as conn:
            row = conn.execute(
                """
                SELECT r.project_id, r.candidate_id, r.revision, r.name,
                       r.payload, r.created_by, r.created_at
                FROM workbench_shared.candidates AS c
                JOIN workbench_shared.projects AS p
                  ON p.project_id=c.project_id
                JOIN workbench_shared.candidate_revisions AS r
                  ON r.project_id=c.project_id
                 AND r.candidate_id=c.candidate_id
                 AND r.revision=c.current_revision
                WHERE p.workspace_id=%s AND c.project_id=%s AND c.candidate_id=%s
                """,
                (identity.workspace_id, project_id, candidate_id),
            ).fetchone()
        if row is None:
            raise SharedResourceNotFound("candidate was not found in this workspace")
        return self._candidate(row)

    def list_candidate_history(
        self, identity: RequestIdentity, project_id: str, candidate_id: str
    ) -> list[CandidateRevision]:
        self._require(identity, "candidate:read")
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT r.project_id, r.candidate_id, r.revision, r.name,
                       r.payload, r.created_by, r.created_at
                FROM workbench_shared.candidate_revisions AS r
                JOIN workbench_shared.projects AS p
                  ON p.project_id=r.project_id
                WHERE p.workspace_id=%s AND r.project_id=%s AND r.candidate_id=%s
                ORDER BY r.revision
                """,
                (identity.workspace_id, project_id, candidate_id),
            ).fetchall()
        if not rows:
            raise SharedResourceNotFound("candidate was not found in this workspace")
        return [self._candidate(row) for row in rows]

    def update_candidate(
        self,
        identity: RequestIdentity,
        project_id: str,
        candidate_id: str,
        payload: CandidateUpdate,
    ) -> CandidateRevision:
        self._require(identity, "candidate:write")
        conflict: CandidateRevision | None = None
        result: CandidateRevision | None = None
        with self._connection() as conn:
            next_row = conn.execute(
                """
                UPDATE workbench_shared.candidates AS c
                SET current_revision=c.current_revision + 1,
                    name=%s,
                    updated_at=now()
                FROM workbench_shared.projects AS p
                WHERE p.project_id=c.project_id
                  AND p.workspace_id=%s
                  AND c.project_id=%s
                  AND c.candidate_id=%s
                  AND c.current_revision=%s
                RETURNING c.current_revision
                """,
                (
                    payload.name,
                    identity.workspace_id,
                    project_id,
                    candidate_id,
                    payload.expected_revision,
                ),
            ).fetchone()
            if next_row is None:
                current_row = conn.execute(
                    """
                    SELECT r.project_id, r.candidate_id, r.revision, r.name,
                           r.payload, r.created_by, r.created_at
                    FROM workbench_shared.candidates AS c
                    JOIN workbench_shared.projects AS p
                      ON p.project_id=c.project_id
                    JOIN workbench_shared.candidate_revisions AS r
                      ON r.project_id=c.project_id
                     AND r.candidate_id=c.candidate_id
                     AND r.revision=c.current_revision
                    WHERE p.workspace_id=%s
                      AND c.project_id=%s
                      AND c.candidate_id=%s
                    """,
                    (identity.workspace_id, project_id, candidate_id),
                ).fetchone()
                if current_row is None:
                    raise SharedResourceNotFound(
                        "candidate was not found in this workspace"
                    )
                conflict = self._candidate(current_row)
                self._audit(
                    conn,
                    identity,
                    project_id=project_id,
                    target_type="candidate",
                    target_id=candidate_id,
                    operation="candidate.update",
                    outcome="conflict",
                    expected_revision=payload.expected_revision,
                    resulting_revision=conflict.revision,
                )
            else:
                revision = int(next_row["current_revision"])
                revision_row = conn.execute(
                    """
                    INSERT INTO workbench_shared.candidate_revisions(
                        project_id, candidate_id, revision, name, payload, created_by
                    ) VALUES (%s,%s,%s,%s,%s,%s)
                    RETURNING project_id, candidate_id, revision, name, payload,
                              created_by, created_at
                    """,
                    (
                        project_id,
                        candidate_id,
                        revision,
                        payload.name,
                        Jsonb(payload.payload),
                        identity.actor.actor_id,
                    ),
                ).fetchone()
                result = self._candidate(revision_row)
                self._audit(
                    conn,
                    identity,
                    project_id=project_id,
                    target_type="candidate",
                    target_id=candidate_id,
                    operation="candidate.revision_created",
                    expected_revision=payload.expected_revision,
                    resulting_revision=revision,
                )
        if conflict is not None:
            raise SharedRevisionConflict(conflict)
        if result is None:
            raise SharedLabError("candidate update did not return a result")
        return result

    def create_run(
        self,
        identity: RequestIdentity,
        project_id: str,
        payload: ActivityRunCreate,
    ) -> ActivityRun:
        self._require(identity, "run:write")
        with self._connection() as conn:
            candidate_revision = conn.execute(
                """
                SELECT 1
                FROM workbench_shared.candidate_revisions AS r
                JOIN workbench_shared.projects AS p ON p.project_id=r.project_id
                WHERE p.workspace_id=%s AND r.project_id=%s
                  AND r.candidate_id=%s AND r.revision=%s
                """,
                (
                    identity.workspace_id,
                    project_id,
                    payload.candidate_id,
                    payload.candidate_revision,
                ),
            ).fetchone()
            if candidate_revision is None:
                raise SharedResourceNotFound(
                    "candidate revision was not found in this workspace"
                )
            row = conn.execute(
                """
                INSERT INTO workbench_shared.activity_runs(
                    run_id, project_id, candidate_id, candidate_revision,
                    activity_id, payload, created_by
                )
                SELECT %s,%s,%s,%s,%s,%s,%s
                FROM workbench_shared.projects AS p
                WHERE p.project_id=%s AND p.workspace_id=%s
                RETURNING run_id, project_id, candidate_id, candidate_revision,
                          activity_id, payload, created_by, created_at
                """,
                (
                    payload.run_id,
                    project_id,
                    payload.candidate_id,
                    payload.candidate_revision,
                    payload.activity_id,
                    Jsonb(payload.payload),
                    identity.actor.actor_id,
                    project_id,
                    identity.workspace_id,
                ),
            ).fetchone()
            if row is None:
                raise SharedResourceNotFound("project was not found in this workspace")
            self._audit(
                conn,
                identity,
                project_id=project_id,
                target_type="activity_run",
                target_id=payload.run_id,
                operation="run.created",
                detail={
                    "candidate_id": payload.candidate_id,
                    "candidate_revision": payload.candidate_revision,
                },
            )
        return ActivityRun.model_validate(row)

    def list_runs(
        self, identity: RequestIdentity, project_id: str
    ) -> list[ActivityRun]:
        self._require(identity, "run:read")
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT r.run_id, r.project_id, r.candidate_id,
                       r.candidate_revision, r.activity_id, r.payload,
                       r.created_by, r.created_at
                FROM workbench_shared.activity_runs AS r
                JOIN workbench_shared.projects AS p ON p.project_id=r.project_id
                WHERE p.workspace_id=%s AND r.project_id=%s
                ORDER BY r.created_at, r.run_id
                """,
                (identity.workspace_id, project_id),
            ).fetchall()
        return [ActivityRun.model_validate(row) for row in rows]

    def register_artifact(
        self,
        identity: RequestIdentity,
        *,
        artifact_id: str,
        project_id: str,
        object_key: str,
        content_digest: str,
        content_type: str,
        size_bytes: int,
        metadata: dict[str, Any],
    ) -> ArtifactReference:
        self._require(identity, "artifact:write")
        with self._connection() as conn:
            row = conn.execute(
                """
                INSERT INTO workbench_shared.artifact_references(
                    artifact_id, project_id, object_key, content_digest,
                    content_type, size_bytes, metadata, created_by, status,
                    verified_at
                )
                SELECT %s,%s,%s,%s,%s,%s,%s,%s,'ready',now()
                FROM workbench_shared.projects AS p
                WHERE p.project_id=%s AND p.workspace_id=%s
                RETURNING artifact_id, project_id, object_key, content_digest,
                          content_type, size_bytes, metadata, created_by, status,
                          verified_at, created_at
                """,
                (
                    artifact_id,
                    project_id,
                    object_key,
                    content_digest,
                    content_type,
                    size_bytes,
                    Jsonb(metadata),
                    identity.actor.actor_id,
                    project_id,
                    identity.workspace_id,
                ),
            ).fetchone()
            if row is None:
                raise SharedResourceNotFound("project was not found in this workspace")
            reference = ArtifactReference.model_validate(row)
            self._audit(
                conn,
                identity,
                project_id=project_id,
                target_type="artifact",
                target_id=artifact_id,
                operation="artifact.registered",
                detail={
                    "content_digest": content_digest,
                    "size_bytes": size_bytes,
                },
            )
        return reference

    def get_artifact(
        self, identity: RequestIdentity, artifact_id: str
    ) -> ArtifactReference:
        self._require(identity, "artifact:read")
        with self._connection() as conn:
            row = conn.execute(
                """
                SELECT a.artifact_id, a.project_id, a.object_key,
                       a.content_digest, a.content_type, a.size_bytes,
                       a.metadata, a.created_by, a.status, a.verified_at,
                       a.created_at
                FROM workbench_shared.artifact_references AS a
                JOIN workbench_shared.projects AS p ON p.project_id=a.project_id
                WHERE p.workspace_id=%s AND a.artifact_id=%s
                  AND a.status='ready'
                """,
                (identity.workspace_id, artifact_id),
            ).fetchone()
        if row is None:
            raise SharedResourceNotFound("artifact was not found in this workspace")
        return ArtifactReference.model_validate(row)

    def list_audit_events(
        self,
        identity: RequestIdentity,
        project_id: str,
        limit: int = 100,
    ) -> list[AuditEvent]:
        self._require(identity, "audit:read")
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT event_id, workspace_id, actor_id, project_id, target_type,
                       target_id, operation, outcome, expected_revision,
                       resulting_revision, request_id, correlation_id, detail,
                       created_at
                FROM workbench_shared.audit_events
                WHERE workspace_id=%s AND project_id=%s
                ORDER BY created_at DESC, event_id DESC
                LIMIT %s
                """,
                (identity.workspace_id, project_id, limit),
            ).fetchall()
        return [AuditEvent.model_validate(row) for row in rows]
