"""観測が参照する画像を配信する経路の安全境界を固定する。

画像のパスは元データ由来で信頼できない。データセットの配置場所の外へ出る参照、
画像以外の拡張子、存在しないファイルを、それぞれ別々に扱えることを固定する。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from decision_workbench.data.evidence_images import (
    EvidenceImageError,
    declared_evidence_image,
    resolve_evidence_image,
)


@pytest.fixture()
def dataset(tmp_path: Path) -> Path:
    source = tmp_path / "dataset.xlsx"
    source.write_bytes(b"not really a workbook")
    images = tmp_path / "images" / "anneal"
    images.mkdir(parents=True)
    (images / "AN-01_100x.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (tmp_path / "secret.txt").write_text("private", encoding="utf-8")
    return source


def test_declared_image_inside_the_dataset_directory_is_served(dataset: Path) -> None:
    resolved = resolve_evidence_image("images/anneal/AN-01_100x.png", dataset)

    assert resolved.available is True
    assert resolved.media_type == "image/png"
    assert resolved.resolved is not None and resolved.resolved.is_file()


def test_windows_style_separators_resolve_to_the_same_file(dataset: Path) -> None:
    resolved = resolve_evidence_image(r"images\anneal\AN-01_100x.png", dataset)

    assert resolved.available is True
    assert resolved.declared_path == "images/anneal/AN-01_100x.png"


@pytest.mark.parametrize(
    "declared",
    [
        "../outside.png",
        "images/../../outside.png",
        "/etc/hosts.png",
        r"C:\Windows\System32\drivers\etc\hosts.png",
        r"..\outside.png",
        "",
        "   ",
    ],
)
def test_paths_that_escape_the_dataset_directory_are_refused(
    dataset: Path, declared: str
) -> None:
    with pytest.raises(EvidenceImageError):
        resolve_evidence_image(declared, dataset)


@pytest.mark.parametrize("declared", ["secret.txt", "images/anneal/AN-01_100x.svg", "notes"])
def test_only_allow_listed_image_suffixes_are_served(dataset: Path, declared: str) -> None:
    with pytest.raises(EvidenceImageError):
        resolve_evidence_image(declared, dataset)


def test_a_declared_but_missing_file_is_reported_not_substituted(dataset: Path) -> None:
    resolved = resolve_evidence_image("images/anneal/AN-99_100x.png", dataset)

    assert resolved.available is False
    assert resolved.resolved is None
    assert resolved.reason


def test_pointer_column_comes_from_the_profile_not_from_code() -> None:
    columns = {("anneal_microstructure", "evidence_image"): "画像path"}
    row = {"画像path": " images/anneal/AN-01_100x.png "}

    assert declared_evidence_image(columns, "anneal_microstructure", row) == (
        "images/anneal/AN-01_100x.png"
    )
    # 宣言のないroleでは画像を探しに行かない。
    assert declared_evidence_image(columns, "hot_microstructure", row) is None
    assert declared_evidence_image(columns, "anneal_microstructure", {"画像path": "  "}) is None


def test_lineage_exposes_and_serves_the_declared_micrograph(client) -> None:
    detail = client.get("/api/projects/default/lineage/AM-01")
    assert detail.status_code == 200, detail.text
    reference = detail.json()["node"]["evidence_image"]
    assert reference["available"] is True
    assert reference["declared_path"].endswith(".png")

    image = client.get("/api/projects/default/lineage/AM-01/evidence-image")
    assert image.status_code == 200
    assert image.headers["content-type"] == "image/png"
    assert image.headers["x-content-type-options"] == "nosniff"
    assert image.content.startswith(b"\x89PNG\r\n\x1a\n")


def test_a_node_without_a_declared_image_reports_it_instead_of_guessing(client) -> None:
    detail = client.get("/api/projects/default/lineage/AN-01")
    assert detail.status_code == 200
    assert detail.json()["node"]["evidence_image"] is None

    image = client.get("/api/projects/default/lineage/AN-01/evidence-image")
    assert image.status_code == 404


def test_a_row_whose_image_file_is_missing_stays_visible_as_missing(client) -> None:
    """取り込み漏れを、画像が無いこととして正直に見せる。"""

    missing = [
        item["key"]
        for item in client.get("/api/projects/default/lineage").json()["items"]
        if item["entity_type"].endswith("組織")
    ]
    references = {
        key: client.get(f"/api/projects/default/lineage/{key}").json()["node"]["evidence_image"]
        for key in missing
    }
    unavailable = {
        key: value for key, value in references.items() if value and not value["available"]
    }

    assert unavailable, "画像未取込の行が1件は必要（画面の扱いを確かめるため）"
    for key, value in unavailable.items():
        assert value["declared_path"]
        assert client.get(
            f"/api/projects/default/lineage/{key}/evidence-image"
        ).status_code == 404
