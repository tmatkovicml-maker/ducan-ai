"""Brand assets.

The single source of brand assets is the ``brand/`` folder, described by
``brand/brand.yaml``. Code may only reference files listed there; nothing ever
generates a logo or a variation of one. Missing or altered files fail loudly.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

import yaml


class BrandAssetError(RuntimeError):
    """A brand asset is missing, unlisted or does not match its checksum."""


@dataclass(frozen=True)
class Brand:
    """Validated brand manifest."""

    root: Path
    name: str | None
    colors: dict[str, str] = field(default_factory=dict)
    fonts: dict[str, str] = field(default_factory=dict)
    files: dict[str, Path] = field(default_factory=dict)

    def asset(self, key: str) -> Path:
        """Return the path of a listed asset or raise :class:`BrandAssetError`."""
        if key not in self.files:
            raise BrandAssetError(f"Brand asset '{key}' is not listed in brand/brand.yaml")
        return self.files[key]

    def has(self, key: str) -> bool:
        """True if the asset is listed (and therefore verified to exist)."""
        return key in self.files


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_brand(brand_dir: Path) -> Brand:
    """Load ``brand.yaml`` and verify every listed file exists and matches its sha256."""
    manifest = brand_dir / "brand.yaml"
    if not manifest.is_file():
        raise BrandAssetError(f"Missing brand manifest: {manifest}")
    raw = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
    name = raw.get("name") or None

    files: dict[str, Path] = {}
    for key, entry in (raw.get("files") or {}).items():
        if not isinstance(entry, dict) or "path" not in entry or "sha256" not in entry:
            raise BrandAssetError(f"brand.yaml entry '{key}' needs 'path' and 'sha256'")
        path = (brand_dir / entry["path"]).resolve()
        if brand_dir.resolve() not in path.parents:
            raise BrandAssetError(f"Brand asset '{key}' must live inside {brand_dir}")
        if not path.is_file():
            raise BrandAssetError(f"Brand asset '{key}' is missing: {path}")
        if _sha256(path) != entry["sha256"]:
            raise BrandAssetError(f"Brand asset '{key}' does not match its sha256: {path}")
        files[key] = path

    return Brand(
        root=brand_dir,
        name=name,
        colors=dict(raw.get("colors") or {}),
        fonts=dict(raw.get("fonts") or {}),
        files=files,
    )
