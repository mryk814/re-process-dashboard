from __future__ import annotations

import multiprocessing
import os

import uvicorn

from material_workbench.app import app


def main() -> None:
    multiprocessing.freeze_support()
    uvicorn.run(
        app,
        host=os.getenv("WORKBENCH_API_HOST", "127.0.0.1"),
        port=int(os.environ["WORKBENCH_API_PORT"]),
        log_level="info",
    )


if __name__ == "__main__":
    main()
