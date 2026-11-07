"""A person's word that a file is its entry's work, where the file check
says it is not: a translation, a scan whose title page did not read, a
chapter without its book's front matter; and, where an entry holds
several files, which is the edition it cites and what each other is.

Written beside the bibliography (`references.bib` →
`references.confirmed.json`), since it is the bibliography's fact, shared
by every paper that cites from it, and survives a re-ingest and a new
collection. It names each file by its SHA-256, so a replaced file is
checked again. Only `axiomation sources confirm` writes it.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from axiomation.sources.config import SourcesError
from axiomation.sources.match import Check

SUFFIX = ".confirmed.json"


@dataclass(frozen=True, slots=True)
class Confirmation:
    """One file confirmed as its entry's work, and why: the edition the
    entry cites (`cited`), or another file of it, `why` its label (a
    translation, a supplement)."""

    file_sha256: str
    why: str
    when: str
    cited: bool = True


#: Each key's confirmations, one a file.
Kept = Mapping[str, tuple[Confirmation, ...]]


def path_for(bib: Path) -> Path:
    """Where a bibliography's confirmations are kept: beside it."""
    return bib.with_name(bib.stem + SUFFIX)


def load(bib: Path | None) -> dict[str, tuple[Confirmation, ...]]:
    """Every confirmation of a bibliography, by key; none without one. A
    key's one cited confirmation is kept as one object, its others as a
    list."""
    if bib is None:
        return {}
    path = path_for(bib)
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return {
            key: tuple(
                Confirmation(**item)
                for item in (value if isinstance(value, list) else [value])
            )
            for key, value in raw.items()
        }
    except (OSError, ValueError, TypeError) as error:
        raise SourcesError(f"{path}: cannot read: {error}") from error


def of_file(found: Kept, key: str, file_sha256: str) -> Confirmation | None:
    """A person's confirmation of this file under `key`, cited or not."""
    if not file_sha256:
        return None
    return next((c for c in found.get(key, ()) if c.file_sha256 == file_sha256), None)


def cited(found: Kept, key: str) -> Confirmation | None:
    """The file a person confirmed as the edition `key` cites."""
    return next((c for c in found.get(key, ()) if c.cited), None)


def confirmed(found: Kept, key: str, file_sha256: str) -> bool:
    """Whether this file is confirmed as `key`'s work, the edition it
    cites: a label saying it is another file of the entry is not."""
    held = cited(found, key)
    return held is not None and bool(file_sha256) and held.file_sha256 == file_sha256


def write(bib: Path, key: str, file_sha256: str, why: str, cited: bool = True) -> Path:
    """File one confirmation, replacing the file's earlier one; a cited
    one replaces the key's earlier cited one, whichever file it named."""
    if not why.strip():
        raise SourcesError("a confirmation says why the file is the entry's work")
    found = load(bib)
    kept = [
        c
        for c in found.get(key, ())
        if c.file_sha256 != file_sha256 and not (cited and c.cited)
    ]
    now = datetime.now(UTC).isoformat(timespec="seconds")
    found[key] = (Confirmation(file_sha256, why.strip(), now, cited), *kept)
    path = path_for(bib)
    path.write_text(
        json.dumps(
            {k: _shape(found[k]) for k in sorted(found)}, indent=2, ensure_ascii=False
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def _shape(held: tuple[Confirmation, ...]) -> object:
    """A key's confirmations as the file keeps them: one cited one as the
    object every earlier file holds, else a list, each saying whether it
    is the cited edition."""
    if len(held) == 1 and held[0].cited:
        only = asdict(held[0])
        del only["cited"]
        return only
    return [asdict(c) for c in held]


def hold(key: str, found: Check, kept: Kept, file_sha256: str) -> str:
    """Why a work is held back, unread: its file check's line and what to
    do, unless a person confirmed this very file; empty when it reads."""
    if found.matches or confirmed(kept, key, file_sha256):
        return ""
    before = earlier(kept, key, file_sha256)
    return (
        f"{key}'s file is not its entry's work: {found.why}. "
        + (f"{before} " if before else "")
        + f"A person who finds it is confirms it (`axiomation sources confirm "
        f"DIR {key} --why …`); else the file is replaced (and, where the "
        "library is read, ingested again)"
    )


def earlier(kept: Kept, key: str, file_sha256: str) -> str:
    """A cited confirmation kept for another file under `key`, said as a
    sentence: the file was replaced since a person confirmed the one
    before; empty where there is none."""
    held = cited(kept, key)
    if held is None or not file_sha256 or held.file_sha256 == file_sha256:
        return ""
    return (
        f"A person confirmed another file under {key} ({held.file_sha256[:12]}, "
        f"{held.when[:10]}: {held.why}), not this one ({file_sha256[:12]})."
    )
