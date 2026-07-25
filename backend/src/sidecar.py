from __future__ import annotations

import multiprocessing
import os
import sys

import uvicorn


def configure_standard_streams() -> None:
    for stream in (sys.stdout, sys.stderr):
        if stream is not None:
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")


def main() -> None:
    multiprocessing.freeze_support()
    configure_standard_streams()
    from material_workbench.app import app

    uvicorn.run(
        app,
        host=os.getenv("WORKBENCH_API_HOST", "127.0.0.1"),
        port=int(os.environ["WORKBENCH_API_PORT"]),
        log_level="info",
    )


if __name__ == "__main__":
    main()
