"""The source contract: what a Qdrant collection must carry.

The contract is the collection — its name, which encodes who built it, which
bibliography it indexes and the vector space it holds, and the payload every
point carries. Another tool may write it and axiomation read it, and
either may build a collection the other can read. The two codebases will
drift; this file is what they must keep agreeing on.

The payload is the half that has to match. The *name* deliberately does not:
axiomation's own rule is `axiomation_<bib>_<model>_<dims>`, not a bare
`papers_<model>_<dims>` another tool may use, because a tool that refuses
to write to somebody else's collection should not default to their name
for it either.

Standard library only, so stating the contract and checking it against a
payload costs nothing.
"""

from __future__ import annotations

import hashlib
import re
import struct
from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path
from typing import Any

# Defined in the core's evidence contract, named here as the wire's.
from axiomation.evidence import OCR_SOURCES as OCR_SOURCES
from axiomation.evidence import TEXT_FLOOR as TEXT_FLOOR
from axiomation.evidence import TextSource as TextSource

#: The payload field that holds a page's position in the PDF, counted from 1.
#:
#: It is called `page_number` on the wire and it is an index: in a book
#: the two differ, because a book numbers its front matter separately.
#: The name cannot change — a collection another tool built carries it,
#: axiomation reads that collection, and the contract is the only thing the
#: two codebases must agree on — so the wire name lives here and nothing above this
#: module says `page_number` to mean a position.
PAGE_INDEX_FIELD = "page_number"

# What a read needs. A point missing any of these cannot answer a citation
# question, so its absence is a contract failure.
READ_FIELDS: tuple[str, ...] = (
    "bib_key",
    "title",
    PAGE_INDEX_FIELD,
    "chunk_index",
    "file_path",
    "text",
)

#: How a chunk's `text` relates to a page. `PAGE` means the text is the
#: page and nothing else, so the vectors are over what a read serves. A point
#: without the field carries the page plus 500 characters of each neighbour
#: (the neighbours' scheme); the reader refuses such a collection and names
#: the repair. Not a `READ_FIELD`: its absence is a scheme, not a hole.
CHUNK_SCHEME_FIELD = "chunk_scheme"


#: The page's kind as the ingest that wrote it read it (`pdf.PageKind`:
#: `scan` or `digital`). Its presence says the recorded reader is the one the
#: page class chose. A page without it (a collection `sources adopt` copied)
#: has its reader checked against its text
#: at the next ingest (`ingest/readers.py`), since a copied record can be
#: wrong and a good OCR of a clean page reads like the page's own layer.
#: Not a `PROVENANCE_FIELD`: another tool's collection need not carry it.
PAGE_KIND_FIELD = "page_kind"

#: How many findings the OCR's witness left on the page (`ingest/witness.py`):
#: 0 where it settled them all, more where a reader should read the page as
#: an image. Present on every page a declared OCR service read since the
#: witness existed; a page that service read without it is witnessed at the
#: next `--reread`. Not a `PROVENANCE_FIELD`: another tool's collection
#: need not carry it.
OCR_DISPUTED_FIELD = "ocr_disputed"

#: How many of the page's figures had their text read by OCR and added after
#: its layer (`ingest/figures.py`), 0 for none. Present on every page an
#: ingest under a declared OCR service keeps as its layer; a stored layer
#: page without it is tested at the next ingest. Not a
#: `PROVENANCE_FIELD`: another tool's collection need not carry it.
OCR_FIGURES_FIELD = "ocr_figures"

#: The version of the witness's reading (`witness.OCR_READING`) a page a
#: declared OCR service read was settled or disputed under. A page an older
#: one left disputed is read afresh at the next `--reread`; a settled one
#: keeps its text. Not a `PROVENANCE_FIELD`: another tool's collection
#: need not carry it.
OCR_READING_FIELD = "ocr_reading"

#: The version of the layer reader (`pdf.LAYER_READER`) whose reading a page
#: kept as its layer holds. A stored layer page with an older one, or none,
#: is read against its PDF at the next ingest: one that reads alike takes
#: the version, its text and vectors untouched; one that reads otherwise is
#: read again under `--reread` (`ingest/readers.py`). Not a
#: `PROVENANCE_FIELD`: another tool's collection need not carry it.
LAYER_READER_FIELD = "layer_reader"


class ChunkScheme(StrEnum):
    """What a stored chunk is. One value; absence is the neighbours' scheme."""

    PAGE = "page"


# What a re-ingest needs. A point another tool wrote (a collection
# `sources adopt` copied, say) may carry none of them. Their absence
# means provenance unknown, which the ingest planner treats as unchanged
# (`--force` rebuilds those), so it is worth reporting and not worth
# failing over.
PROVENANCE_FIELDS: tuple[str, ...] = ("file_sha256", "bib_sha256", "ocr_backend")

PAYLOAD_FIELDS: tuple[str, ...] = READ_FIELDS + PROVENANCE_FIELDS

# The marker point: one point per collection axiomation created, naming the
# creator and the vector space, so `ingest` can refuse a collection it does
# not own. It carries no `bib_key`, so every read filters it out by field.
MARKER_FIELD = "axiomation_marker"
MARKER_VALUE = "collection"
MARKER_SEED = "__axiomation_marker__"


def point_id(bib_key: str, chunk_index: int) -> int:
    """A deterministic u64 point id, so a re-ingest overwrites in place."""
    raw = hashlib.sha256(f"{bib_key}:{chunk_index}".encode()).digest()
    return struct.unpack(">Q", raw[:8])[0]


MARKER_ID = point_id(MARKER_SEED, 0)


#: Qdrant takes letters, digits, `-` and `_`. Anything else in a filename
#: becomes a hyphen, and a run of them collapses.
_UNSAFE_RE = re.compile(r"[^a-z0-9]+")

#: How wide a bibliography's name may be in a collection name before it stops
#: helping anybody read it.
BIB_NAME_LIMIT = 32


def bib_slug(bib: Path | str | None) -> str:
    """A bibliography's file name, as a collection name may carry it."""
    if bib is None:
        return ""
    return _UNSAFE_RE.sub("-", Path(bib).stem.lower()).strip("-")[:BIB_NAME_LIMIT]


def collection_name(
    voyage_model: str, voyage_dims: int, bib: Path | str | None = None
) -> str:
    """What to call a collection nobody named.

    Three things belong in the name, and each one keeps two collections on
    one server from colliding:

    - **`axiomation`**, because a bare `papers_<model>_<dims>` is what another tool
      may name its own, and following it would put the default on top of
      a collection that tool built. A tool that will not write to somebody
      else's collection should not default to their name for it either.
    - **the bibliography**, because the corpus is keyed by bib key and two
      projects with different bibliographies are two corpora. Papers that
      *share* a bibliography — the whole point of a monorepo — still share a
      collection, which is the behaviour worth keeping.
    - **the vector space**, because a model or dimension change invalidates
      every vector in it, and `upsert.guard` refuses such a collection rather
      than half-rewriting it. Keeping it in the name means the refusal never
      has to happen.

    A bibliography named `references.bib` gives the same slug in every project
    that uses that name, so two of them on one Qdrant server still collide;
    the answer there is `[sources].collection`, which beats this entirely.
    """
    parts = ["axiomation", bib_slug(bib), voyage_model, str(voyage_dims)]
    return "_".join(part for part in parts if part)


def is_contextualized(voyage_model: str) -> bool:
    """Whether a model embeds chunks with their document as context.

    Such a model (`voyage-context-*`) has no plain `embed` endpoint. Both
    halves branch on this: the read half sends a query as one document of
    one chunk, the ingest half sends a source's pages in windows.
    """
    return voyage_model.startswith("voyage-context")


def vector_space_hash(voyage_model: str, voyage_dims: int) -> str:
    """What a collection cannot change under: the vector space itself.

    Text, titles and OCR backends may all be re-ingested in place. The model
    and its dimensions may not: every vector already stored would have to be
    thrown away, so a mismatch is a new collection, not an update.
    """
    return hashlib.sha256(f"{voyage_model}\x1f{voyage_dims}".encode()).hexdigest()


def missing_payload_fields(payload: Mapping[str, Any]) -> tuple[str, ...]:
    """The fields a read needs and this point does not carry."""
    return tuple(field for field in READ_FIELDS if field not in payload)


def missing_provenance_fields(payload: Mapping[str, Any]) -> tuple[str, ...]:
    """The provenance fields a point lacks. Not a failure; see above."""
    return tuple(field for field in PROVENANCE_FIELDS if field not in payload)
