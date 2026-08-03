"""Run the dedicated current-main acceptance API.

The Prediction Graph journey needs one deterministic, test-owned Stage B
failure. It is injected only in this process and only once; production
composition remains untouched.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import Request

from decision_workbench.app import create_app
from decision_workbench.application.chain.plan import ChainExecutionError


def create_acceptance_app(
    *,
    database: Path,
    data_library: Path,
    model_store: Path,
    profile_store: Path,
    task_store: Path,
) -> Any:
    app = create_app(
        db_path=database,
        data_library_path=data_library,
        model_store_path=model_store,
        task_store_path=task_store,
    )
    installed = False
    failed = False

    @app.middleware("http")
    async def inject_one_stage_b_failure(request: Request, call_next):
        nonlocal installed, failed
        if not installed and hasattr(app.state, "prediction_graph_use_cases"):
            use_cases = app.state.prediction_graph_use_cases
            executor = use_cases.execution.stage_executor
            original_run_stage = executor._run_stage
            # The fault must exercise execution, not get hidden by a memo seeded
            # for the same immutable Package/input identity.
            with use_cases.store._connect() as connection:
                connection.execute("DELETE FROM chain_stage_memo")

            def run_stage(stage, canonical_input, stage_candidate, adapter):
                nonlocal failed
                if stage.stage_id == "B" and not failed:
                    failed = True
                    raise ChainExecutionError(
                        "current-main acceptance injected Stage B failure"
                    )
                return original_run_stage(
                    stage,
                    canonical_input,
                    stage_candidate,
                    adapter,
                )

            executor._run_stage = run_stage
            installed = True
        return await call_next(request)

    return app


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--data-library", type=Path, required=True)
    parser.add_argument("--model-store", type=Path, required=True)
    parser.add_argument("--profile-store", type=Path, required=True)
    parser.add_argument("--task-store", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()

    app = create_acceptance_app(
        database=args.db,
        data_library=args.data_library,
        model_store=args.model_store,
        profile_store=args.profile_store,
        task_store=args.task_store,
    )
    uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
