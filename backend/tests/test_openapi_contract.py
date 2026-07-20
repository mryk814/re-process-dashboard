from __future__ import annotations

import json
from pathlib import Path

from material_workbench.app import app
from material_workbench.schemas import CandidateInput


ROOT = Path(__file__).resolve().parents[2]


def test_tracked_openapi_schema_matches_fastapi_contract() -> None:
    tracked = json.loads((ROOT / "apps/web/src/generated/openapi.json").read_text(encoding="utf-8"))
    assert tracked == app.openapi()
    candidate_response = tracked["paths"]["/api/projects/{project_id}/candidates"]["get"]["responses"]["200"]
    assert candidate_response["content"]["application/json"]["schema"]["items"]["$ref"].endswith("/Candidate")


def test_unicode_identifiers_and_units_survive_json_contract_round_trip(client) -> None:
    source = client.get("/api/projects/default/candidates").json()[0]
    payload = CandidateInput.model_validate(
        {
            **source,
            "name": "候補Ａ・日本語キー確認",
            "inputs": {
                **source["inputs"],
                "categorical": {**source["inputs"]["categorical"], "表示識別子": "全角Ａ"},
            },
        }
    )
    encoded = json.dumps(payload.model_dump(mode="json"), ensure_ascii=False)
    decoded = CandidateInput.model_validate_json(encoded)
    assert decoded.name == "候補Ａ・日本語キー確認"
    assert decoded.inputs.categorical["表示識別子"] == "全角Ａ"

    task = client.get("/api/projects/default/task-definition").json()["task_definition"]
    units = {field["unit"] for group in task["input_groups"] for field in group["fields"] if field["unit"]}
    units.update(output["unit"] for output in task["outputs"])
    assert {"mass%", "mm", "m/min", "MPa", "%"} <= units
