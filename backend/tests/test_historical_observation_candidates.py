from __future__ import annotations

from decision_workbench.contracts.candidate_project_contracts import CandidateInput


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

    repeated = client.post(
        f"/api/projects/{project['id']}/historical-observations/{row['observation_id']}/candidate"
    )
    assert repeated.status_code == 201, repeated.text
    assert repeated.json()["candidate"]["id"] == candidate["id"]
    historical_candidates = [
        item
        for item in client.get(f"/api/projects/{project['id']}/candidates").json()
        if item["provenance"]["source_kind"] == "historical_observation"
    ]
    assert [item["id"] for item in historical_candidates] == [candidate["id"]]

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

    archived = client.delete(
        f"/api/projects/{project['id']}/candidates/{candidate['id']}?expected_revision={candidate['revision']}"
    )
    assert archived.status_code == 204, archived.text
    archived_repeat = client.post(
        f"/api/projects/{project['id']}/historical-observations/{row['observation_id']}/candidate"
    )
    assert archived_repeat.status_code == 409
    assert archived_repeat.json()["code"] == "candidate_archived"


def test_historical_observation_identity_is_scoped_by_project_and_dataset_reference(client) -> None:
    first_project = client.post(
        "/api/projects",
        json={"name": "first historical identity", "task_id": "concrete-strength-v1"},
    ).json()
    records = client.get(
        f"/api/projects/{first_project['id']}/historical-observations"
    ).json()["records"]
    row = next(item for item in records if item["candidate_eligible"])
    first = client.post(
        f"/api/projects/{first_project['id']}/historical-observations/{row['observation_id']}/candidate"
    )
    assert first.status_code == 201, first.text
    first_candidate = first.json()["candidate"]

    second_project = client.post(
        "/api/projects",
        json={"name": "second historical identity", "task_id": "concrete-strength-v1"},
    ).json()
    second = client.post(
        f"/api/projects/{second_project['id']}/historical-observations/{row['observation_id']}/candidate"
    )
    assert second.status_code == 201, second.text
    second_candidate = second.json()["candidate"]
    assert second_candidate["id"] != first_candidate["id"]
    assert second_candidate["provenance"]["source_ref"] == first_candidate["provenance"]["source_ref"]

    reference = first_candidate["provenance"]["source_ref"]
    alternate_dataset = CandidateInput.model_validate({
        "name": first_candidate["name"],
        "inputs": first_candidate["inputs"],
        "provenance": {
            "source_kind": "historical_observation",
            "source_ref": {
                **reference,
                "dataset_view_revision_id": f"{reference['dataset_view_revision_id']}-other",
                "source_sha256": "b" * 64,
            },
        },
    })
    distinct_dataset = client.app.state.store.create_or_get_historical_observation_candidate(
        alternate_dataset,
        first_project["id"],
    )
    assert distinct_dataset.id != first_candidate["id"]
    assert distinct_dataset.provenance.source_ref.dataset_view_revision_id.endswith("-other")
    assert client.app.state.store.create_or_get_historical_observation_candidate(
        alternate_dataset,
        first_project["id"],
    ).id == distinct_dataset.id
