from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import subprocess


ROOT = Path(__file__).resolve().parents[2]


def test_dev_launcher_moves_from_an_occupied_default_port() -> None:
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        requested = occupied.getsockname()[1]
        environment = {
            **os.environ,
            "WORKBENCH_DEV_API_PORT": str(requested),
            "WORKBENCH_DEV_WEB_PORT": str(requested),
        }
        completed = subprocess.run(
            ["node", "scripts/dev-launcher.mjs", "--resolve-ports"],
            cwd=ROOT,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    report = json.loads(completed.stdout.splitlines()[-1])
    assert report["apiUrl"] != f"http://127.0.0.1:{requested}"
    assert report["webPort"] != str(requested)
    assert "[port]" in completed.stdout
