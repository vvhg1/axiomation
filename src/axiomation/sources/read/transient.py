"""Pages extracted from an open copy that is not in the corpus."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from axiomation.evidence import ReadStatus, classify_text, detail_for
from axiomation.sources.contract import TextSource

_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")


def stem_for(bib_key: str) -> str:
    """The key as a file name: itself where it is one, else its safe
    characters with the first 8 hex of its SHA-256, so two keys that
    differ only where a character is unsafe (`doi:10.1/x`,
    `doi:10.1_x`) keep two copies."""
    stem = _SAFE.sub("_", bib_key).strip("._") or "source"
    if stem == bib_key:
        return stem
    return f"{stem}-{hashlib.sha256(bib_key.encode()).hexdigest()[:8]}"


def pdf_path(cache_dir: Path, bib_key: str) -> Path:
    return cache_dir / "oa" / f"{stem_for(bib_key)}.pdf"


def unchecked_path(cache_dir: Path, bib_key: str) -> Path:
    """The mark beside a copy the fetch kept though the match could not
    decide on it from its text layer, with no OCR to read it again."""
    return cache_dir / "oa" / f"{stem_for(bib_key)}.unchecked"


def pages_path(cache_dir: Path, bib_key: str) -> Path:
    return cache_dir / "oa" / f"{stem_for(bib_key)}.pages.json"


def store_pages(cache_dir: Path, bib_key: str, pages: list[dict[str, object]]) -> Path:
    path = pages_path(cache_dir, bib_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(pages, ensure_ascii=False), encoding="utf-8")
    return path


def load_pages(cache_dir: Path | None, bib_key: str) -> list[dict[str, object]]:
    if cache_dir is None:
        return []
    path = pages_path(cache_dir, bib_key)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def status_for(text: str, bib_key: str, page_index: int) -> tuple[ReadStatus, str]:
    status = classify_text(text, TextSource.NONE.value)
    if status is ReadStatus.CONTENT:
        return status, detail_for(status, bib_key, page_index)
    return (
        ReadStatus.TRANSIENT,
        "open copy, not the version of record: it may differ from it",
    )
