"""Seed immutable Chain evidence, then run an API with optional Chain resources broken."""

from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn
from fastapi.testclient import TestClient
from material_workbench.app import create_app
from material_workbench.bootstrap.contributions import (
    WELDING_BLEND_CONTRIBUTION_ID,
    WeldingBlendContributionConfig,
)


def _seed(database: Path, data_library: Path) -> tuple[str, str]:
    with TestClient(
        create_app(db_path=database, data_library_path=data_library)
    ) as client:
        template = next(
            item
            for item in client.get("/api/chains").json()
            if item["definition"]["chain_id"] == "welding-consumable-a-b-c-v1"
        )
        revision = template["revisions"][0]
        project_response = client.post(
            "/api/projects",
            json={
                "name": "保存済み証跡を確認するChain",
                "scientific_identity": {
                    "identity_kind": "chain",
                    "chain_revision_id": (
                        f"{revision['chain_id']}:r{revision['revision']}"
                    ),
                    "chain_revision_digest": revision["revision_digest"],
                },
            },
        )
        project_response.raise_for_status()
        project = project_response.json()
        contract_response = client.get(
            f"/api/projects/{project['id']}/chain/candidate-contract"
        )
        contract_response.raise_for_status()
        candidate_response = client.post(
            f"/api/projects/{project['id']}/chain/candidates",
            json=contract_response.json()["starter_candidate"],
        )
        candidate_response.raise_for_status()
        candidate = candidate_response.json()
        execution_response = client.post(
            f"/api/projects/{project['id']}/chain/candidates/"
            f"{candidate['id']}/executions",
            json={
                "candidate_revision": candidate["revision"],
                "request_id": "degraded-e2e-saved-execution",
                "debounce_ms": 0,
            },
        )
        execution_response.raise_for_status()
        snapshot_response = client.post(
            f"/api/projects/{project['id']}/chain/candidates/"
            f"{candidate['id']}/snapshots",
            json={
                "candidate_revision": candidate["revision"],
                "debounce_ms": 0,
            },
        )
        snapshot_response.raise_for_status()
        return project["id"], candidate["id"]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--broken-transform", type=Path, required=True)
    parser.add_argument("--broken-evaluation", type=Path, required=True)
    args = parser.parse_args()

    args.db.unlink(missing_ok=True)
    data_library = args.db.with_name(f"{args.db.stem}-data-library")
    _seed(args.db, data_library)
    degraded_app = create_app(
        db_path=args.db,
        data_library_path=data_library,
        contribution_configs={
            WELDING_BLEND_CONTRIBUTION_ID: WeldingBlendContributionConfig(
                active_transforms_path=args.broken_transform,
                chain_evaluation_path=args.broken_evaluation,
            )
        },
    )
    uvicorn.run(degraded_app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
