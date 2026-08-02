from __future__ import annotations

import argparse
import os
from pathlib import Path

from fastapi.testclient import TestClient

from decision_workbench.app import create_app


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--project-id-file", type=Path, required=True)
    args = parser.parse_args()
    os.environ["WORKBENCH_DEMO_SEED"] = "all"

    with TestClient(create_app(db_path=args.db)) as client:
        options = client.get("/api/project-creation-options").json()
        dataset = next(
            item
            for item in options["datasets"]
            if "heat-treatment-tradeoff-v1" in item["supported_task_ids"]
        )
        package = next(
            item
            for item in options["model_packages"]
            if item["task_id"] == "heat-treatment-tradeoff-v1"
        )
        response = client.post(
            "/api/projects",
            json={
                "name": "利用停止診断E2E",
                "task_id": "heat-treatment-tradeoff-v1",
                "dataset_view_revision_id": dataset["dataset_views"][0]["id"],
                "model_package_ref_id": package["id"],
            },
        )
        response.raise_for_status()
        args.project_id_file.write_text(
            response.json()["id"],
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
