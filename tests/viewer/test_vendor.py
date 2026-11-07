"""The viewer's vendored files: each file the
manifest names is there, unchanged, with its licence beside it, and
nothing else is vendored unnamed, in a subdirectory either."""

from __future__ import annotations

import hashlib
import tomllib
from pathlib import Path

VENDOR = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "axiomation"
    / "viewer"
    / "page"
    / "vendor"
)


def _manifest() -> list[dict[str, str]]:
    return tomllib.loads((VENDOR / "MANIFEST.toml").read_text("utf-8"))["file"]


def test_every_vendored_file_has_the_hash_the_manifest_gives() -> None:
    for entry in _manifest():
        found = hashlib.sha256((VENDOR / entry["path"]).read_bytes()).hexdigest()
        assert found == entry["sha256"], entry["path"]
        assert (VENDOR / entry["licence_file"]).is_file(), entry["licence_file"]


def test_nothing_is_vendored_unnamed() -> None:
    named = {"MANIFEST.toml"}
    for entry in _manifest():
        named |= {entry["path"], entry["licence_file"]}
    found = {p.relative_to(VENDOR).as_posix() for p in VENDOR.rglob("*") if p.is_file()}
    assert found == named
