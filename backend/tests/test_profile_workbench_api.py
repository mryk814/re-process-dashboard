from __future__ import annotations

from hashlib import sha256
from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient
from openpyxl import load_workbook


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "data" / "source" / "material_workbench_tutorial_v2.xlsx"
PROFILE_SOURCE_NAME = "dataset-input-profile-tutorial"
MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _file_payload(contents: bytes, name: str = "new-source.xlsx") -> dict[str, tuple[str, BytesIO, str]]:
    return {"file": (name, BytesIO(contents), MEDIA_TYPE)}


def _workbook_copy_with_new_digest() -> bytes:
    workbook = load_workbook(SOURCE)
    workbook.properties.title = "Profile Workbench API test"
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


def test_profile_options_only_expose_allowlisted_profiles(client: TestClient) -> None:
    response = client.get("/api/profile-workbench/profiles")

    assert response.status_code == 200
    profiles = response.json()
    assert PROFILE_SOURCE_NAME in {item["source_name"] for item in profiles}
    assert all(item["profile_digest"] and item["task_ids"] for item in profiles)


def test_inspect_auto_detects_profile_without_exposing_server_paths(client: TestClient) -> None:
    response = client.post(
        "/api/profile-workbench/inspect",
        files=_file_payload(SOURCE.read_bytes(), SOURCE.name),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["source_filename"] == SOURCE.name
    profile = next(item for item in client.get("/api/profile-workbench/profiles").json() if item["source_name"] == PROFILE_SOURCE_NAME)
    assert body["selected_profile_digest"] == profile["profile_digest"]
    assert body["auto_detected"] is True
    assert body["validation"]["registration_ready"] is True
    assert body["validation"]["observations"] > 0
    assert body["sheets"]
    assert "source" not in body
    assert "profile" not in body


def test_inspect_rejects_arbitrary_profile_key(client: TestClient) -> None:
    response = client.post(
        "/api/profile-workbench/inspect",
        data={"profile_digest": "../../private.json"},
        files=_file_payload(SOURCE.read_bytes()),
    )

    assert response.status_code == 422
    assert "許可されたDataset Profile" in response.json()["message"]


def test_inspect_reports_invalid_workbook_as_validation_error(client: TestClient) -> None:
    response = client.post(
        "/api/profile-workbench/inspect",
        files=_file_payload(b"not an xlsx archive"),
    )

    assert response.status_code == 422
    assert "Excelファイルを読み取れません" in response.json()["message"]


def test_inspect_keeps_inventory_when_selected_profile_does_not_match(client: TestClient) -> None:
    profiles = client.get("/api/profile-workbench/profiles").json()
    flank_wear = next(item for item in profiles if item["source_name"] == "dataset-input-profile-flank-wear-v1")

    response = client.post(
        "/api/profile-workbench/inspect",
        data={"profile_digest": flank_wear["profile_digest"]},
        files=_file_payload(SOURCE.read_bytes(), SOURCE.name),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["sheets"]
    assert body["selected_profile_digest"] == flank_wear["profile_digest"]
    assert body["validation"] is None
    assert body["profile_error"]


def test_register_adds_managed_dataset_and_reuses_duplicate(client: TestClient) -> None:
    contents = _workbook_copy_with_new_digest()
    profile = next(item for item in client.get("/api/profile-workbench/profiles").json() if item["source_name"] == PROFILE_SOURCE_NAME)
    before = client.get("/api/data-library/datasets").json()

    first = client.post(
        "/api/profile-workbench/register",
        data={"profile_digest": profile["profile_digest"], "expected_source_sha256": sha256(contents).hexdigest(), "name": "設備B 再評価"},
        files=_file_payload(contents),
    )

    assert first.status_code == 200, first.text
    first_body = first.json()
    assert first_body["reused_existing"] is False
    assert first_body["profile_id"] == "thin-sheet-tutorial-v1"
    after_first = client.get("/api/data-library/datasets").json()
    assert len(after_first) == len(before) + 1
    registered = next(
        item for item in after_first
        if item["dataset_revision"]["id"] == first_body["dataset_revision_id"]
    )
    assert registered["data_asset"]["locator_kind"] == "managed"
    assert Path(registered["data_asset"]["locator"]).is_file()
    assert Path(registered["data_asset"]["locator"]).is_relative_to(client.app.state.data_library_root)

    second = client.post(
        "/api/profile-workbench/register",
        data={"profile_digest": profile["profile_digest"], "expected_source_sha256": sha256(contents).hexdigest(), "name": "設備B 再評価"},
        files=_file_payload(contents),
    )

    assert second.status_code == 200, second.text
    assert second.json()["reused_existing"] is True
    assert second.json()["dataset_revision_id"] == first_body["dataset_revision_id"]
    assert len(client.get("/api/data-library/datasets").json()) == len(after_first)


def test_stage_b_profile_inspection_and_managed_registration(client: TestClient) -> None:
    source = (
        ROOT
        / "data/source/welding_consumable_multistage_synthetic_dataset.xlsx"
    )
    profile = next(
        item
        for item in client.get("/api/profile-workbench/profiles").json()
        if item["source_name"] == "welding-stage-b-profile-v1"
    )
    contents = source.read_bytes()
    inspection = client.post(
        "/api/profile-workbench/inspect",
        data={"profile_digest": profile["profile_digest"]},
        files=_file_payload(contents, source.name),
    )
    assert inspection.status_code == 200, inspection.text
    assert inspection.json()["validation"]["observations"] == 300
    assert inspection.json()["validation"]["observations_by_task"] == {
        "welding-consumable-stage-b-v1": 300
    }

    registration = client.post(
        "/api/profile-workbench/register",
        data={
            "profile_digest": profile["profile_digest"],
            "expected_source_sha256": sha256(contents).hexdigest(),
        },
        files=_file_payload(contents, source.name),
    )
    assert registration.status_code == 200, registration.text
    assert registration.json()["profile_id"] == "welding-consumable-stage-b-v1"
    dataset = next(
        item
        for item in client.get("/api/data-library/datasets").json()
        if item["dataset_revision"]["id"]
        == registration.json()["dataset_revision_id"]
    )
    assert dataset["data_asset"]["locator_kind"] == "managed"


def test_register_rejects_file_changed_after_inspection(client: TestClient) -> None:
    contents = _workbook_copy_with_new_digest()
    profile = next(item for item in client.get("/api/profile-workbench/profiles").json() if item["source_name"] == PROFILE_SOURCE_NAME)
    before = len(client.get("/api/data-library/datasets").json())

    response = client.post(
        "/api/profile-workbench/register",
        data={"profile_digest": profile["profile_digest"], "expected_source_sha256": "0" * 64},
        files=_file_payload(contents),
    )

    assert response.status_code == 409
    assert "内容が変わりました" in response.json()["message"]
    assert len(client.get("/api/data-library/datasets").json()) == before


def test_register_does_not_silently_reactivate_archived_dataset(client: TestClient) -> None:
    contents = _workbook_copy_with_new_digest()
    profile = next(item for item in client.get("/api/profile-workbench/profiles").json() if item["source_name"] == PROFILE_SOURCE_NAME)
    payload = {
        "profile_digest": profile["profile_digest"],
        "expected_source_sha256": sha256(contents).hexdigest(),
        "name": "archive test",
    }
    first = client.post("/api/profile-workbench/register", data=payload, files=_file_payload(contents))
    assert first.status_code == 200, first.text
    client.app.state.workspace_catalog.archive_dataset_revision(first.json()["dataset_revision_id"])

    second = client.post("/api/profile-workbench/register", data=payload, files=_file_payload(contents))

    assert second.status_code == 409
    assert "archive" in second.json()["message"]
