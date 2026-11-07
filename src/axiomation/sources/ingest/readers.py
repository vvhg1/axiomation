"""Whether a stored page's recorded reader is what read it.

A page the ingest writes records its kind (`PAGE_KIND_FIELD`) beside its
reader, and the kind decided the reader, so the record is true. A page
without its kind has no such guarantee: `sources adopt` copies a
collection's records as they are (a digital work's layer recorded
`paddle`, a scan's OCR layer `none`). Each such page is checked once against its PDF, at
the next ingest, and its record settled.

The check reads the stored text against the page's layer, letter for
letter. That tells a stored layer from an OCR of a page with no layer, the
two shapes a page without its kind may hold. It cannot tell a layer
from a good OCR of a page with a clean layer, which match as closely; so
only pages without the kind are checked this way.

A page kept as its layer also records which version of the layer reader
read it (`contract.LAYER_READER_FIELD`). A page an older reader wrote,
another extractor's included, is read again by today's and compared
whole: one that reads alike takes the version, and one that reads
otherwise is read again with its work (`run.Reason.LAYER`), whatever
judgments that lapses, so the corpus holds what today's reader reads.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from axiomation.evidence import text_hash
from axiomation.sources import pdf
from axiomation.sources.contract import (
    OCR_DISPUTED_FIELD,
    OCR_READING_FIELD,
    OCR_SOURCES,
    TextSource,
)
from axiomation.sources.ingest import figures, witness
from axiomation.sources.ingest import layer as layer_module
from axiomation.sources.ingest.extract import lost_layer
from axiomation.sources.ingest.ocr import OcrProvider

#: The similarity, letter for letter, from which a stored text is its page's
#: layer. On the corpus's pages recorded as OCR that have a layer (11,147),
#: a stored layer scores 0.9 or more against today's extraction of it
#: (another extractor's spacing and symbols aside), an OCR reading 0.2 or
#: less, and one page falls between, at 0.8.
LAYER_MATCH = 0.5

_OCR_VALUES = frozenset(member.value for member in OCR_SOURCES)


def outworn(payload: Mapping[str, Any]) -> bool:
    """Whether a stored page an OCR service read was left disputed by an
    older reading than today's (`witness.OCR_READING`): it is read afresh."""
    return bool(payload.get(OCR_DISPUTED_FIELD)) and (
        payload.get(OCR_READING_FIELD, 1) != witness.OCR_READING
    )


@dataclass(frozen=True, slots=True)
class StoredPage:
    """One stored page whose record has no kind."""

    page_index: int
    chunk_index: int
    reader: str
    text: str


@dataclass(frozen=True, slots=True)
class PageRecord:
    """A stored page's record settled: its kind, and its reader where the
    record named the wrong one."""

    page_index: int
    chunk_index: int
    kind: pdf.PageKind
    reader: TextSource | None = None


@dataclass(frozen=True, slots=True)
class ReaderAudit:
    """One stored work's pages without a kind, checked against its PDF."""

    bib_key: str
    #: the pages only OCR serves whose stored text is their layer: a scan's
    #: old OCR, or a digital layer that may spell no words. A declared
    #: backend reads the work again (`run.Reason.READER`); until then they
    #: stay unsettled.
    reread: tuple[int, ...] = ()
    #: every other page, settled by a payload update, its text untouched
    records: tuple[PageRecord, ...] = ()

    @property
    def relabelled(self) -> tuple[PageRecord, ...]:
        return tuple(record for record in self.records if record.reader)

    def as_dict(self) -> dict[str, Any]:
        return {
            "bib_key": self.bib_key,
            "reread": list(self.reread),
            "recorded_none": [record.page_index for record in self.relabelled],
            "settled": len(self.records),
        }


def audit(
    bib_key: str,
    pdf_path: Path,
    pages: Sequence[StoredPage],
    *,
    tesseract: OcrProvider | None,
) -> ReaderAudit:
    """Each unsettled page against its PDF: re-read, relabelled or settled.

    A scan's page whose stored text is its layer is old OCR read as a
    layer, and only a re-read mends it. So is a digital page stored as a
    layer that spells no words, by the ingest's own test (`extract.
    lost_layer`); without Tesseract to confirm it, such a page is left
    unsettled. A digital page recorded as OCR whose stored text is its
    layer was copied with the wrong record, and takes `none`.
    """
    reread: list[int] = []
    records: list[PageRecord] = []
    with pdf.open_pdf(pdf_path) as document:
        kinds = document.page_kinds()
        for page in pages:
            if not 1 <= page.page_index <= len(kinds):
                continue
            kind = kinds[page.page_index - 1]
            layer = document.text(page.page_index).strip()
            is_layer = bool(layer) and (
                page.reader == TextSource.NONE
                or layer_module.similarity(page.text, layer) >= LAYER_MATCH
            )
            if is_layer and kind is pdf.PageKind.SCAN:
                reread.append(page.page_index)
                continue
            if is_layer and layer_module.may_spell_nothing(layer):
                if tesseract is None:
                    continue
                if lost_layer(document, page.page_index, layer, tesseract) is not None:
                    reread.append(page.page_index)
                    continue
            relabel = kind is pdf.PageKind.DIGITAL and is_layer
            records.append(
                PageRecord(
                    page.page_index,
                    page.chunk_index,
                    kind,
                    TextSource.NONE if relabel and page.reader in _OCR_VALUES else None,
                )
            )
    return ReaderAudit(bib_key, tuple(sorted(reread)), tuple(records))


@dataclass(frozen=True, slots=True)
class LayerAudit:
    """One stored work's digital layer pages an older layer reader wrote,
    read by today's."""

    bib_key: str
    #: the pages today's reader reads otherwise, read again with the work
    outdated: tuple[int, ...] = ()
    #: the chunks of the pages it reads alike, given the version by a
    #: payload update
    alike: tuple[int, ...] = ()
    #: whether reading the work again would read other pages by OCR (a
    #: scan, a page OCR read), which only a declared backend may
    needs_backend: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "bib_key": self.bib_key,
            "outdated": list(self.outdated),
            "alike": len(self.alike),
            "needs_backend": self.needs_backend,
        }


def layer_audit(
    bib_key: str, pdf_path: Path, pages: Sequence[StoredPage]
) -> LayerAudit:
    """Each page against today's reading of its layer, by content hash (its
    whitespace aside); a page's figures' text, which follows its layer, is
    not the layer's."""
    outdated: list[int] = []
    alike: list[int] = []
    with pdf.open_pdf(pdf_path) as document:
        for page in pages:
            if not 1 <= page.page_index <= document.page_count:
                continue
            stored = page.text.split(f"\n\n{figures.FIGURE_LINE}", 1)[0]
            now = document.text(page.page_index).strip()
            if text_hash(stored) == text_hash(now):
                alike.append(page.chunk_index)
            else:
                outdated.append(page.page_index)
    return LayerAudit(bib_key, tuple(sorted(outdated)), tuple(sorted(alike)))
