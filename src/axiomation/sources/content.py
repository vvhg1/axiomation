"""A PDF's content hash: what it prints, not how it is saved.

A reading of a cited work rests on the work's file; it lapses when what
the file prints changes, never when a reader saves it otherwise
(docs/architecture.md § 9). So the hash covers each page's content streams and
the resources the page lists for them to draw (fonts, images, forms,
colour spaces), read with `pypdf`, and leaves out what a reader adds or
a download varies: the annotations (`/Annots`: highlights, notes, ink),
the metadata, the file's ID, the outline and the byte layout.

A stream's generic compression is undone (Flate, LZW, the ASCII and
run-length filters), which a reader may redo on save. An image codec's
bytes (DCT, JPX, JBIG2 with its globals, CCITT) are hashed as stored:
`pypdf` cannot decode them all, decoding is slow, and a reader rarely
re-encodes them. A file `pypdf` cannot read is hashed whole, by its
SHA-256.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pypdf import PdfReader, filters
from pypdf.errors import LimitReachedError
from pypdf.generic import (
    ArrayObject,
    BooleanObject,
    DictionaryObject,
    FloatObject,
    IndirectObject,
    NameObject,
    NullObject,
    NumberObject,
    StreamObject,
)

#: The hash's form, its tag: a hash of another form (another canonical
#: form, or another pypdf's reading) never compares with it
#: (`evidence.same_content`).
FORM = "c1"
#: The generic filters, undone before hashing, by their names and
#: abbreviations.
_GENERIC: dict[str, Callable[..., bytes]] = {
    "/FlateDecode": filters.FlateDecode.decode,
    "/Fl": filters.FlateDecode.decode,
    "/LZWDecode": filters.LZWDecode.decode,
    "/LZW": filters.LZWDecode.decode,
    "/ASCII85Decode": filters.ASCII85Decode.decode,
    "/A85": filters.ASCII85Decode.decode,
    "/ASCIIHexDecode": filters.ASCIIHexDecode.decode,
    "/AHx": filters.ASCIIHexDecode.decode,
    "/RunLengthDecode": filters.RunLengthDecode.decode,
    "/RL": filters.RunLengthDecode.decode,
}
#: A stream's keys that say how it is stored, not what it holds.
_STORAGE = frozenset({"/Length", "/Filter", "/DecodeParms", "/DL"})
#: The parts of a page's resources a check names: what changed.
FONTS, IMAGES, FORMS, OTHER = "fonts", "images", "forms", "other"
CONTENT = "content streams"


@dataclass(frozen=True, slots=True)
class Parts:
    """A file's content hash, and its parts' hashes over every page: what a
    check compares to name which part a reader changed."""

    hash: str
    parts: dict[str, str]


def content_hash(path: Path) -> str:
    """The content hash of the PDF at `path` (the module's doc), tagged with
    its form (`c1:…`)."""
    return f"{FORM}:{content_parts(path).hash}"


def content_parts(path: Path) -> Parts:
    """The content hash of the PDF at `path`, with its parts' hashes; a
    file `pypdf` cannot read hashed whole, with no parts."""
    data = path.read_bytes()
    try:
        return _parts(path)
    except Exception:  # noqa: BLE001 - any file pypdf cannot read is hashed whole
        return Parts(hashlib.sha256(data).hexdigest(), {})


def _parts(path: Path) -> Parts:
    # pypdf warns on every damaged object it recovers; the hash is all we want
    logging.getLogger("pypdf").setLevel(logging.ERROR)
    reader = PdfReader(path, strict=False)
    if reader.is_encrypted:
        reader.decrypt("")
    canon = _Canon()
    whole = hashlib.sha256()
    parts = {name: hashlib.sha256() for name in (CONTENT, FONTS, IMAGES, FORMS, OTHER)}
    for page in reader.pages:
        contents = page.get("/Contents")
        streams = _resolved(contents)
        items = streams if isinstance(streams, ArrayObject) else [contents]
        printed = hashlib.sha256()
        for item in items:
            stream = _resolved(item)
            if isinstance(stream, StreamObject):
                printed.update(_decoded(stream)[1])
        page_parts = {CONTENT: printed.hexdigest()}
        resources = _resolved(page.get("/Resources"))
        if isinstance(resources, DictionaryObject):
            for name, digest in _resource_parts(canon, resources).items():
                page_parts[name] = digest
        for name, digest in page_parts.items():
            parts[name].update(digest.encode())
        whole.update(repr(sorted(page_parts.items())).encode())
    return Parts(whole.hexdigest(), {k: v.hexdigest() for k, v in parts.items()})


def _resource_parts(canon: _Canon, resources: DictionaryObject) -> dict[str, str]:
    """A page's resources hashed by part: its fonts, its images, its forms
    (an XObject of each subtype) and the rest (colour spaces, patterns,
    shadings, graphics states)."""
    found: dict[str, list[str]] = {FONTS: [], IMAGES: [], FORMS: [], OTHER: []}
    for key in sorted(resources):
        value = _resolved(resources[key])
        if key == "/Font" and isinstance(value, DictionaryObject):
            found[FONTS] += [
                f"{k}={canon.digest(value.raw_get(k))}" for k in sorted(value)
            ]
        elif key == "/XObject" and isinstance(value, DictionaryObject):
            for k in sorted(value):
                xobject = _resolved(value[k])
                kind = FORMS if _subtype(xobject) == "/Form" else IMAGES
                found[kind].append(f"{k}={canon.digest(value.raw_get(k))}")
        else:
            found[OTHER].append(f"{key}={canon.digest(resources.raw_get(key))}")
    return {
        name: hashlib.sha256("\n".join(items).encode()).hexdigest()
        for name, items in found.items()
        if items
    }


def _subtype(value: Any) -> str:
    if isinstance(value, DictionaryObject):
        return str(value.get("/Subtype", ""))
    return ""


def _resolved(value: Any) -> Any:
    while isinstance(value, IndirectObject):
        value = value.get_object()
    return value


def _filters(stream: StreamObject) -> tuple[list[str], list[Any]]:
    """A stream's filters in order, and each one's parameters."""
    named = _resolved(stream.get("/Filter"))
    names = (
        [str(_resolved(n)) for n in named]
        if isinstance(named, ArrayObject)
        else ([] if named is None else [str(named)])
    )
    given = _resolved(stream.get("/DecodeParms"))
    parms = (
        [_resolved(p) for p in given]
        if isinstance(given, ArrayObject)
        else [given] * len(names)
    )
    parms += [None] * (len(names) - len(parms))
    return names, parms


def _decoded(stream: StreamObject) -> tuple[list[str], bytes]:
    """A stream's bytes with its generic filters undone, from the first on
    until an image codec's, and the filters left (that codec's and any
    after it), which name how the bytes stay encoded."""
    names, parms = _filters(stream)
    stored = stream._data  # noqa: SLF001 - the bytes as stored, before any filter
    data = stored
    for i, name in enumerate(names):
        decode = _GENERIC.get(name)
        if decode is None:
            return names[i:], data
        param = parms[i]
        try:
            data = decode(data, param if isinstance(param, DictionaryObject) else None)
        except LimitReachedError:
            # past pypdf's guard against a decompression bomb (a large
            # image): hashed as stored
            return names, stored
    return [], data


class _Canon:
    """Each object's digest, kept by its reference so a font every page
    lists is read once; an object reached again while it is read (a form
    that names its own page) digests as a mark."""

    def __init__(self) -> None:
        self._done: dict[tuple[int, int], str] = {}
        self._open: set[tuple[int, int]] = set()

    def digest(self, value: Any) -> str:
        return hashlib.sha256(self._form(value)).hexdigest()

    def _form(self, value: Any) -> bytes:
        if isinstance(value, IndirectObject):
            ref = (value.idnum, value.generation)
            if ref in self._done:
                return self._done[ref].encode()
            if ref in self._open:
                return b"<cycle>"
            self._open.add(ref)
            digest = self.digest(value.get_object())
            self._open.discard(ref)
            self._done[ref] = digest
            return digest.encode()
        if isinstance(value, StreamObject):
            left, data = _decoded(value)
            head = DictionaryObject(
                {NameObject(k): v for k, v in value.items() if k not in _STORAGE}
            )
            # a codec's parameters (JBIG2's globals, CCITT's shape) are
            # part of what its bytes print
            names, parms = _filters(value)
            kept = parms[len(names) - len(left) :]
            return (
                b"stream"
                + self._form(head)
                + repr(left).encode()
                + b"".join(self._form(p) for p in kept)
                + hashlib.sha256(data).digest()
            )
        if isinstance(value, DictionaryObject):
            return (
                b"<<"
                + b"".join(
                    # its reference, not what `[]` resolves: a shared
                    # object is read once, a cycle met as one
                    str(k).encode() + b" " + self._form(value.raw_get(k))
                    for k in sorted(value)
                    # a dictionary's parent points up the page tree: not
                    # what it prints
                    if k != "/Parent"
                )
                + b">>"
            )
        if isinstance(value, ArrayObject):
            return b"[" + b" ".join(self._form(v) for v in value) + b"]"
        if isinstance(value, BooleanObject | NullObject | NameObject):
            return repr(value).encode()
        if isinstance(value, NumberObject | FloatObject | int | float):
            # a number written back as 1 or 1.0, or 0.5 as .5, is one number
            return f"{float(value):.6g}".encode()
        if isinstance(value, bytes | str):
            data = value if isinstance(value, bytes) else value.encode("utf-8")
            return b"(" + data + b")"
        return repr(value).encode()
