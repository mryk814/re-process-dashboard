"""Keep pytest collection independent from a developer's Personal Task store."""
from __future__ import annotations

import csv
import os
from pathlib import Path
import subprocess
import sys

from material_workbench.developer_experience.task_scaffolding import (
    ScaffoldField,
    create_task_scaffold,
)
from material_workbench.task_composition.catalog import registered_task_modules


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PERSONAL_TASK_ID = "collection-isolation-personal-v1"
COLLECTION_TARGETS = (
    "backend/tests/test_task_registry.py",
    "backend/tests/test_extensibility_registration_points.py",
    "backend/tests/test_standard_model_training.py",
)


def _ready_personal_task_store(tmp_path: Path) -> Path:
    source = tmp_path / "personal-source.csv"
    with source.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=("carbon", "strength"),
            lineterminator="\n",
        )
        writer.writeheader()
        for index in range(4):
            writer.writerow(
                {
                    "carbon": 0.1 + index * 0.1,
                    "strength": 300 + index * 25,
                }
            )

    store = tmp_path / "personal-tasks"
    result = create_task_scaffold(
        source=source,
        task_id=PERSONAL_TASK_ID,
        label="収集隔離の個人Task",
        fields=(
            ScaffoldField(
                "carbon",
                "composition",
                "carbon_pct",
                "C",
                "%",
                allowed_range=(0.0, 1.0),
                default_range=(0.1, 0.4),
                training_range=(0.1, 0.4),
            ),
            ScaffoldField(
                "strength",
                "output",
                "strength_mpa",
                "強度",
                "MPa",
                "at_least",
                plausible_range=(0.0, 1_000.0),
                display_range=(250.0, 500.0),
            ),
        ),
        grain_confirmation="one-row-one-observation",
        relation_confirmation="no-relations",
        store=store,
    )
    assert result.state == "ready"
    return store


def _pytest_with_personal_store(
    store: Path,
    *arguments: str,
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["WORKBENCH_TASK_STORE_PATH"] = str(store)
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-n", "0", *arguments],
        cwd=REPOSITORY_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )


def test_pytest_collection_and_execution_ignore_ready_personal_task_store(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Collection happens before fixtures, so parameter matrices must be bundled-only."""

    store = _ready_personal_task_store(tmp_path)
    monkeypatch.setenv("WORKBENCH_TASK_STORE_PATH", str(store))
    assert PERSONAL_TASK_ID in registered_task_modules()

    collected = _pytest_with_personal_store(
        store,
        "--collect-only",
        "-q",
        *COLLECTION_TARGETS,
    )
    collection_output = collected.stdout + collected.stderr
    assert collected.returncode == 0, collection_output
    assert PERSONAL_TASK_ID not in collection_output

    executed = _pytest_with_personal_store(
        store,
        "-q",
        "backend/tests/test_task_registry.py::test_allow_list_contracts_active_packages_and_runtimes_share_one_task_set",
    )
    execution_output = executed.stdout + executed.stderr
    assert executed.returncode == 0, execution_output
    assert PERSONAL_TASK_ID not in execution_output
