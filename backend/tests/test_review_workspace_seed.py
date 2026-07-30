from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from material_workbench.app import create_app
from backend.scripts.operations.seed_review_workspace import seed_review_workspace


def test_review_workspace_seed_uses_bundle_restore_and_becomes_ready(
    tmp_path: Path,
    app_resources,
) -> None:
    database = tmp_path / "review.db"
    data_library = tmp_path / "review-data-library"

    result = seed_review_workspace(
        database,
        data_library,
        resources=app_resources,
    )

    assert result["status"] == "seeded"
    assert result["restore_status"] == "committed"
    assert result["finalize_status"] == "finalized"
    assert result["bundle_size_bytes"] > 0
    assert str(result["seed_content_digest"]).startswith("sha256:")
    assert database.exists()
    with TestClient(
        create_app(
            db_path=database,
            data_library_path=data_library,
            _resources=app_resources,
        )
    ) as client:
        assert client.get("/api/readiness").json()["ready"] is True
        assert client.get("/api/projects").json()

    reseeded = seed_review_workspace(
        database,
        data_library,
        resources=app_resources,
    )
    assert reseeded["seed_content_digest"] == result["seed_content_digest"]


def test_review_workspace_seed_rolls_back_existing_workspace_on_readiness_failure(
    tmp_path: Path,
    app_resources,
) -> None:
    database = tmp_path / "existing.db"
    data_library = tmp_path / "existing-data-library"
    with TestClient(
        create_app(
            db_path=database,
            data_library_path=data_library,
            _resources=app_resources,
        )
    ):
        pass
    before = sha256(database.read_bytes()).hexdigest()

    def reject_readiness(_: dict[str, object]) -> None:
        raise RuntimeError("forced readiness failure")

    with pytest.raises(RuntimeError, match="forced readiness failure"):
        seed_review_workspace(
            database,
            data_library,
            resources=app_resources,
            validate_readiness=reject_readiness,
        )

    assert sha256(database.read_bytes()).hexdigest() == before
