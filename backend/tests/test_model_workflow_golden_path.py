from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "backend" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from model_workflow import diagnose_source  # noqa: E402


def test_model_source_diagnosis_branches_existing_and_new_tasks() -> None:
    source = (
        ROOT
        / "data"
        / "source"
        / "external"
        / "heat_treatment_tradeoff_samples.csv"
    )
    existing = diagnose_source(
        source,
        task_id="heat-treatment-tradeoff-v1",
        profile=None,
    )
    assert existing["route"] == "existing_task_replacement"
    assert existing["eligible_rows"] == 2400
    assert "model:build" in existing["next"]

    new_task = diagnose_source(
        source,
        task_id="unregistered-material-task-v1",
        profile=None,
    )
    assert new_task["route"] == "new_task_or_profile"
    assert "dataset-input-profile.md" in new_task["next"]
