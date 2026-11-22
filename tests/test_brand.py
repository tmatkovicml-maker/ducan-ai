"""Brand assets fail loudly when missing or altered."""

from __future__ import annotations

import hashlib

import pytest

from ducan.brand import BrandAssetError, load_brand


def test_missing_manifest(tmp_path):
    with pytest.raises(BrandAssetError):
        load_brand(tmp_path)


def test_listed_file_must_exist(tmp_path):
    (tmp_path / "brand.yaml").write_text(
        "files:\n  logo_svg: {path: logo.svg, sha256: abc}\n", encoding="utf-8"
    )
    with pytest.raises(BrandAssetError, match="missing"):
        load_brand(tmp_path)


def test_checksum_must_match(tmp_path):
    (tmp_path / "logo.svg").write_text("<svg/>", encoding="utf-8")
    (tmp_path / "brand.yaml").write_text(
        "files:\n  logo_svg: {path: logo.svg, sha256: deadbeef}\n", encoding="utf-8"
    )
    with pytest.raises(BrandAssetError, match="sha256"):
        load_brand(tmp_path)


def test_valid_and_unlisted(tmp_path):
    (tmp_path / "logo.svg").write_text("<svg/>", encoding="utf-8")
    digest = hashlib.sha256(b"<svg/>").hexdigest()
    (tmp_path / "brand.yaml").write_text(
        f"files:\n  logo_svg: {{path: logo.svg, sha256: {digest}}}\n", encoding="utf-8"
    )
    brand = load_brand(tmp_path)
    assert brand.asset("logo_svg").name == "logo.svg"
    with pytest.raises(BrandAssetError):
        brand.asset("logo_png")


def test_path_outside_brand_dir_rejected(tmp_path):
    brand = tmp_path / "brand"
    brand.mkdir()
    (tmp_path / "secret.txt").write_text("x", encoding="utf-8")
    digest = hashlib.sha256(b"x").hexdigest()
    (brand / "brand.yaml").write_text(
        f"files:\n  x: {{path: ../secret.txt, sha256: {digest}}}\n", encoding="utf-8"
    )
    with pytest.raises(BrandAssetError, match="inside"):
        load_brand(brand)


def test_repository_brand_manifest_loads():
    from pathlib import Path

    load_brand(Path(__file__).parent.parent / "brand")
