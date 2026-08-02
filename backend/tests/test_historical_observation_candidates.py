from __future__ import annotations


def test_tabular_historical_observation_is_promoted_with_fixed_source_evidence(client) -> None:
    project_response = client.post(
        "/api/projects",
        json={"name": "flat historical evidence", "task_id": "concrete-strength-v1"},
    )
    assert project_response.status_code == 201, project_response.text
    project = project_response.json()

    listed = client.get(
        f"/api/projects/{project['id']}/historical-observations"
    )
    assert listed.status_code == 200, listed.text
    payload = listed.json()
    assert payload["available"]
    row = next(item for item in payload["records"] if item["candidate_eligible"])
    assert row["actual_outputs"]

    created = client.post(
        f"/api/projects/{project['id']}/historical-observations/{row['observation_id']}/candidate"
    )
    assert created.status_code == 201, created.text
    candidate = created.json()["candidate"]
    reference = candidate["provenance"]["source_ref"]
    assert candidate["provenance"]["source_kind"] == "historical_observation"
    assert reference["dataset_view_revision_id"] == project["dataset_view_revision_id"]
    assert reference["observation_id"] == row["observation_id"]
    assert reference["actual_outputs"] == row["actual_outputs"]
    assert candidate["inputs"] == row["inputs"]

    restored = client.get(
        f"/api/projects/{project['id']}/candidates/{candidate['id']}/historical-evidence"
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["reference"] == reference
    assert restored.json()["actual_outputs"] == row["actual_outputs"]

    changed_inputs = {**candidate["inputs"]}
    changed_process = {**changed_inputs["process"]}
    field = next(iter(changed_process))
    changed_process[field] += 0.001
    changed_inputs["process"] = changed_process
    rejected_update = client.put(
        f"/api/projects/{project['id']}/candidates/{candidate['id']}",
        json={
            "name": candidate["name"],
            "inputs": changed_inputs,
            "provenance": candidate["provenance"],
            "expected_revision": candidate["revision"],
        },
    )
    assert rejected_update.status_code == 409
    assert "入力条件は変更できません" in rejected_update.json()["message"]
