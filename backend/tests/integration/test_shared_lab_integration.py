from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from io import BytesIO
import os

import pytest


pytestmark = pytest.mark.skipif(
    os.getenv("WORKBENCH_RUN_SHARED_INTEGRATION") != "1",
    reason="requires the isolated PostgreSQL and MinIO Shared Lab fixture",
)


def test_two_actor_shared_workbench_scenario() -> None:
    from fastapi.testclient import TestClient
    from minio.error import S3Error
    from psycopg import connect

    from material_workbench.shared_lab.app import create_shared_lab_app
    from material_workbench.shared_lab.config import SharedLabConfig

    config = SharedLabConfig.from_env()
    app = create_shared_lab_app(config)

    def headers(actor: str, request: str) -> dict[str, str]:
        return {
            "X-Workbench-Workspace": "shared-lab",
            "X-Workbench-Actor": actor,
            "X-Request-ID": request,
            "X-Correlation-ID": "scenario-1",
        }

    with TestClient(app) as client:
        missing_identity = client.get("/api/shared/context")
        assert missing_identity.status_code == 400
        assert missing_identity.json()["code"] == "identity_missing"

        invalid_actor = client.get(
            "/api/shared/context", headers=headers("not-seeded", "invalid-actor")
        )
        assert invalid_actor.status_code == 403
        assert invalid_actor.json()["code"] == "identity_invalid"

        context = client.get(
            "/api/shared/context", headers=headers("human-a", "context-a")
        )
        assert context.status_code == 200
        assert context.json()["mode"] == "shared"
        assert context.json()["actor"]["actor_id"] == "human-a"

        project = client.post(
            "/api/shared/projects",
            headers=headers("human-a", "project-create"),
            json={"project_id": "shared-project", "name": "Shared Project"},
        )
        assert project.status_code == 201
        assert project.json()["created_by"] == "human-a"

        candidate = client.post(
            "/api/shared/projects/shared-project/candidates",
            headers=headers("human-a", "candidate-create"),
            json={
                "candidate_id": "candidate-1",
                "name": "Candidate r1",
                "payload": {"temperature_c": 700, "note": "baseline"},
            },
        )
        assert candidate.status_code == 201
        assert candidate.json()["revision"] == 1
        assert candidate.json()["created_by"] == "human-a"

        read_by_b = client.get(
            "/api/shared/projects/shared-project/candidates/candidate-1",
            headers=headers("human-b", "candidate-read-b"),
        )
        assert read_by_b.status_code == 200
        assert read_by_b.json()["revision"] == 1

    def competing_update(actor: str, temperature: int):
        with TestClient(app) as competing_client:
            return actor, competing_client.put(
                "/api/shared/projects/shared-project/candidates/candidate-1",
                headers=headers(actor, f"update-{actor}"),
                json={
                    "expected_revision": 1,
                    "name": f"Candidate by {actor}",
                    "payload": {"temperature_c": temperature},
                },
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda args: competing_update(*args),
                [("human-a", 710), ("human-b", 720)],
            )
        )

    assert sorted(response.status_code for _, response in results) == [200, 409]
    winner_actor, winner_response = next(
        item for item in results if item[1].status_code == 200
    )
    loser_actor, loser_response = next(
        item for item in results if item[1].status_code == 409
    )
    assert winner_response.json()["revision"] == 2
    assert winner_response.json()["created_by"] == winner_actor
    assert loser_response.json()["code"] == "revision_conflict"
    assert loser_response.json()["current_candidate"]["revision"] == 2

    with TestClient(app) as client:
        history = client.get(
            "/api/shared/projects/shared-project/candidates/candidate-1/revisions",
            headers=headers("human-b", "history-after-conflict"),
        )
        assert history.status_code == 200
        assert [item["revision"] for item in history.json()] == [1, 2]

        retry = client.put(
            "/api/shared/projects/shared-project/candidates/candidate-1",
            headers=headers(loser_actor, "loser-retry"),
            json={
                "expected_revision": 2,
                "name": f"Candidate retried by {loser_actor}",
                "payload": {"temperature_c": 730},
            },
        )
        assert retry.status_code == 200
        assert retry.json()["revision"] == 3
        assert retry.json()["created_by"] == loser_actor

        run = client.post(
            "/api/shared/projects/shared-project/runs",
            headers=headers("human-a", "run-create"),
            json={
                "run_id": "run-1",
                "candidate_id": "candidate-1",
                "candidate_revision": 3,
                "activity_id": "decision-review",
                "payload": {"result": "keep"},
            },
        )
        assert run.status_code == 201
        assert run.json()["created_by"] == "human-a"

        runs_for_b = client.get(
            "/api/shared/projects/shared-project/runs",
            headers=headers("human-b", "run-read-b"),
        )
        assert runs_for_b.status_code == 200
        assert runs_for_b.json()[0]["candidate_revision"] == 3
        assert runs_for_b.json()[0]["created_by"] == "human-a"

        content = b"shared artifact evidence\n"
        artifact = client.post(
            "/api/shared/projects/shared-project/artifacts/artifact-1",
            headers=headers("human-a", "artifact-create"),
            files={"upload": ("evidence.txt", content, "text/plain")},
        )
        assert artifact.status_code == 201
        reference = artifact.json()
        assert reference["status"] == "ready"
        assert reference["created_by"] == "human-a"
        assert reference["content_digest"] == f"sha256:{sha256(content).hexdigest()}"

        artifact_for_b = client.get(
            "/api/shared/artifacts/artifact-1",
            headers=headers("human-b", "artifact-read-b"),
        )
        assert artifact_for_b.status_code == 200
        assert artifact_for_b.content == content
        assert artifact_for_b.headers["digest"] == reference["content_digest"]

        duplicate_content = b"different object with duplicate metadata id\n"
        duplicate_digest = sha256(duplicate_content).hexdigest()
        duplicate = client.post(
            "/api/shared/projects/shared-project/artifacts/artifact-1",
            headers=headers("human-a", "artifact-duplicate"),
            files={"upload": ("different.txt", duplicate_content, "text/plain")},
        )
        assert duplicate.status_code == 409
        with pytest.raises(S3Error) as removed:
            app.state.shared_lab_artifacts.client.stat_object(
                config.s3_bucket, f"artifacts/sha256/{duplicate_digest}"
            )
        assert removed.value.code in {"NoSuchKey", "NoSuchObject"}

        real_storage = app.state.shared_lab_artifacts.client

        class UnavailableStorage:
            def stat_object(self, *_args, **_kwargs):
                raise OSError("injected storage outage")

        app.state.shared_lab_artifacts.client = UnavailableStorage()
        unavailable = client.post(
            "/api/shared/projects/shared-project/artifacts/artifact-outage",
            headers=headers("human-a", "artifact-outage"),
            files={"upload": ("outage.txt", b"must not register", "text/plain")},
        )
        app.state.shared_lab_artifacts.client = real_storage
        assert unavailable.status_code == 503
        assert unavailable.json()["code"] == "object_storage_unavailable"
        with connect(config.database_url) as conn:
            assert (
                conn.execute(
                    """
                    SELECT count(*) FROM workbench_shared.artifact_references
                    WHERE artifact_id='artifact-outage'
                    """
                ).fetchone()[0]
                == 0
            )

        real_storage.put_object(
            config.s3_bucket,
            reference["object_key"],
            BytesIO(b"corrupted artifact"),
            len(b"corrupted artifact"),
            content_type="text/plain",
        )
        mismatch = client.get(
            "/api/shared/artifacts/artifact-1",
            headers=headers("human-b", "artifact-mismatch"),
        )
        assert mismatch.status_code == 409
        assert mismatch.json()["code"] == "artifact_digest_mismatch"

        audit = client.get(
            "/api/shared/projects/shared-project/audit",
            headers=headers("human-b", "audit-read"),
        )
        assert audit.status_code == 200
        events = audit.json()
        assert any(
            event["operation"] == "candidate.update"
            and event["outcome"] == "conflict"
            and event["actor_id"] == loser_actor
            for event in events
        )
        assert any(
            event["operation"] == "run.created"
            and event["actor_id"] == "human-a"
            for event in events
        )
        assert any(
            event["operation"] == "artifact.registered"
            and event["actor_id"] == "human-a"
            for event in events
        )
