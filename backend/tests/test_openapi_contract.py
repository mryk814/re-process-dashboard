from __future__ import annotations

import json
from pathlib import Path

from decision_workbench.app import app
from decision_workbench.contracts.candidate_project_contracts import CandidateInput
from decision_workbench.contracts.prediction_catalog_contracts import Prediction


ROOT = Path(__file__).resolve().parents[2]


def test_tracked_openapi_schema_matches_fastapi_contract() -> None:
    tracked = json.loads((ROOT / "apps/web/src/generated/openapi.json").read_text(encoding="utf-8"))
    assert tracked == app.openapi()
    candidate_response = tracked["paths"]["/api/projects/{project_id}/candidates"]["get"]["responses"]["200"]
    assert candidate_response["content"]["application/json"]["schema"]["items"]["$ref"].endswith("/Candidate")
    schemas = tracked["components"]["schemas"]
    assert {"target_kind", "point_statistic", "predictive_family", "quantiles", "categories"} <= schemas["Prediction"]["properties"].keys()
    assert {"target_kind", "point_statistic", "predictive_family", "quantiles", "categories"} <= schemas["CurvePoint"]["properties"].keys()
    assert {"revision", "archived_at"} <= schemas["Candidate"]["properties"].keys()
    assert "expected_revision" in schemas["CandidateUpdate"]["required"]
    assert {"revision_conflict", "candidate_archived", "candidate_limit", "data_integrity_error"} <= set(
        schemas["ApiError"]["properties"]["code"]["enum"]
    )
    list_parameters = tracked["paths"]["/api/projects/{project_id}/candidates"]["get"]["parameters"]
    delete_parameters = tracked["paths"]["/api/projects/{project_id}/candidates/{candidate_id}"]["delete"]["parameters"]
    predict_parameters = tracked["paths"]["/api/projects/{project_id}/candidates/{candidate_id}/predict"]["post"]["parameters"]
    preview_parameters = tracked["paths"]["/api/projects/{project_id}/candidates/{candidate_id}/preview"]["post"]["parameters"]
    curve_parameters = tracked["paths"]["/api/projects/{project_id}/candidates/{candidate_id}/response-curve"]["get"]["parameters"]
    similar_parameters = tracked["paths"]["/api/projects/{project_id}/candidates/{candidate_id}/similar"]["get"]["parameters"]
    actual_parameters = tracked["paths"]["/api/projects/{project_id}/candidates/{candidate_id}/actuals"]["post"]["parameters"]
    history_operation = tracked["paths"]["/api/projects/{project_id}/history"]["get"]
    task_catalog_operation = tracked["paths"]["/api/task-definitions"]["get"]
    assert any(item["name"] == "include_archived" for item in list_parameters)
    assert any(item["name"] == "expected_revision" and item.get("required") is True for item in delete_parameters)
    assert any(item["name"] == "expected_revision" and item.get("required") is True for item in predict_parameters)
    assert any(item["name"] == "expected_revision" and item.get("required") is True for item in preview_parameters)
    assert any(item["name"] == "expected_revision" and item.get("required") is True for item in curve_parameters)
    assert {item["name"] for item in curve_parameters if item.get("required") is True} >= {"expected_revision", "target", "variable"}
    assert any(item["name"] == "expected_revision" and item.get("required") is True for item in similar_parameters)
    assert any(item["name"] == "expected_revision" and item.get("required") is True for item in actual_parameters)
    assert history_operation["operationId"] == "getProjectHistory"
    assert task_catalog_operation["operationId"] == "listTaskDefinitions"
    assert {
        "focus_entity_key", "related_entity_keys", "missing_reference_key", "suggested_view",
    } <= schemas["DataQualityIssue"]["properties"].keys()
    quality_csv = tracked["paths"]["/api/projects/{project_id}/quality/export.csv"]["get"]["responses"]["200"]
    assert quality_csv["content"]["text/csv"]["schema"]["type"] == "string"
    validation_responses = [
        operation["responses"]["422"]
        for path in tracked["paths"].values()
        for operation in path.values()
        if "422" in operation.get("responses", {})
    ]
    assert validation_responses
    assert {response["description"] for response in validation_responses} == {"Validation Error"}
    assert {
        response["content"]["application/json"]["schema"]["$ref"]
        for response in validation_responses
    } == {"#/components/schemas/ApiError"}


def test_runtime_validation_error_matches_openapi_api_error(client) -> None:
    response = client.post("/api/screening/run/candidates", json={"point_indices": []})
    assert response.status_code == 422
    payload = response.json()
    assert payload["code"] == "validation_error"
    assert payload["message"] == "入力内容を確認してください"
    assert payload["field_errors"][0]["path"] == "body.point_indices"
    assert payload["field_errors"][0]["message"]
    assert "detail" not in payload


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
    assert {"mpm", "MPa", "%"} <= units


def test_quantile_semantics_survive_prediction_and_snapshot_payload_round_trip() -> None:
    prediction = Prediction(
        value=12.0,
        lower=8.0,
        upper=17.0,
        unit="MPa",
        target_kind="continuous",
        point_statistic="median",
        predictive_family="empirical_quantiles",
        quantiles={"0.05": 8.0, "0.5": 12.0, "0.95": 17.0},
    )
    decoded = Prediction.model_validate_json(prediction.model_dump_json())

    assert decoded.point_statistic == "median"
    assert decoded.predictive_family == "empirical_quantiles"
    assert decoded.quantiles == {"0.05": 8.0, "0.5": 12.0, "0.95": 17.0}


def test_legacy_snapshot_prediction_gets_explicit_read_semantics() -> None:
    decoded = Prediction.model_validate({"value": 12.0, "lower": 8.0, "upper": 17.0, "unit": "MPa"})

    assert decoded.target_kind == "continuous"
    assert decoded.point_statistic == "mean"
    assert decoded.predictive_family == "empirical_quantiles"
    assert decoded.quantiles == {"0.05": 8.0, "0.95": 17.0}
