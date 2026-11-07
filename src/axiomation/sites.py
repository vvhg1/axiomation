"""The sites the verifiers check at a kept state: each
quotation, and each citation with a locator, per cited work, placed on the
block whose owned text holds it.

The state's kept files are read by today's reader, so a state finds the
quotations today's reader finds: a version is its files' text
(`document_hash`), and the reader's spans are on that text. A site no block
owns, or whose words stand more than once in its block, cannot be judged
or anchored; it is listed with why.

The core's: `sources verify` checks the sites, and `check` lists
those a quotation's reading has not verified.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from axiomation import versions
from axiomation.exchange import document_payload
from axiomation.identity import norm
from axiomation.queries import View
from axiomation.records import RecordStore
from axiomation.versions import State


class SiteKind(StrEnum):
    """What a site asks of the cited work: that it prints the quoted words,
    or the result the locator names."""

    QUOTATION = "quotation"
    LOCATOR = "locator"


@dataclass(frozen=True, slots=True)
class Site:
    """One site of one cited work at a kept state. `words` stand once in
    the block's text: a quotation's subject span, or the citation a
    locator's finding is anchored on. `text` is what the work is checked
    for: the quoted words, the citations inside them removed."""

    kind: SiteKind
    block: str
    handle: str
    key: str
    locator: str | None
    words: str
    text: str
    file: str
    line: int

    @property
    def where(self) -> str:
        return f"{self.handle} ({self.file}:{self.line})"


@dataclass(frozen=True, slots=True)
class Unplaced:
    """A site that cannot be judged or anchored, and why."""

    kind: SiteKind
    key: str
    locator: str | None
    file: str
    line: int
    why: str


def _owner(blocks: Sequence[Mapping[str, Any]], at: Mapping[str, Any]) -> str | None:
    """The block one of whose owned runs holds the span's start."""
    start = tuple(at["start"])
    for block in blocks:
        for run in block.get("owned", []):
            if run["file"] == at["file"] and tuple(run["start"]) <= start < tuple(
                run["end"]
            ):
                return block["id"]
    return None


def sites(
    store: RecordStore, kept: State, key: str | None = None
) -> tuple[list[Site], list[Unplaced]]:
    """The quotation and locator sites of `kept` (of the work `key` alone,
    where named), in the paper's order, and those no block can hold."""
    document = versions.document_at(store, kept)
    half = document_payload(document)["document"]
    blocks = versions.kept_payload(store, kept).get("blocks", [])
    view = View(store, kept)
    placed: list[Site] = []
    unplaced: list[Unplaced] = []

    def place(
        kind: SiteKind,
        at: Mapping[str, Any],
        keys: Sequence[str],
        locator: str | None,
        words: Sequence[str],
        text: str,
    ) -> None:
        line = at["start"][0]
        for cited in keys:
            if key is not None and cited != key:
                continue
            block = _owner(blocks, at)
            if block is None:
                unplaced.append(
                    Unplaced(kind, cited, locator, at["file"], line, "no block owns it")
                )
                continue
            held = view.texts.get(block, "")
            once = [w for w in words if w and held.count(w) == 1]
            if not once:
                unplaced.append(
                    Unplaced(
                        kind,
                        cited,
                        locator,
                        at["file"],
                        line,
                        f"its words stand {held.count(words[0])} times in "
                        f"{view.handle(block)}, not once",
                    )
                )
                continue
            placed.append(
                Site(
                    kind,
                    block,
                    view.handle(block),
                    cited,
                    locator,
                    once[0],
                    text,
                    at["file"],
                    line,
                )
            )

    for quoted, item in zip(half["quotations"], document.quotations, strict=True):
        place(
            SiteKind.QUOTATION,
            quoted["at"],
            quoted["keys"],
            quoted["locator"],
            [norm(item.text), norm(document.text(item.at))],
            item.text,
        )
    for cited, item in zip(half["citations"], document.citations, strict=True):
        if cited["locator"] is None:
            continue
        place(
            SiteKind.LOCATOR,
            cited["at"],
            cited["keys"],
            cited["locator"],
            [norm(document.text(item.at))],
            "",
        )
    return placed, unplaced
