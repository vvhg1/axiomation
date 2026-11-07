"""The text layer: a kept state's export placed on its kept files,
as the page's text pane and gutter draw it. Reading order, hidden spans, runs
by class and owner, sections, and every span as UTF-16 code units, the
page's string indices.

It reads the state's own files, its blobs, never the paper's directory,
which may have moved on; the export's `[line, column]` positions become
offsets on them. Everything here is placement: what a block holds, how it
is named and what it rests on are the queries' (`queries.View`), copied
through, never derived again.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from axiomation import versions
from axiomation.closure import entry_label
from axiomation.queries import RESTED_ON_BY_SOURCE, View
from axiomation.questions.roles_file import segment_starts

#: A file of these roles is shown; any other (a package, a preamble file)
#: renders nothing and is listed in the header.
RENDERED = ("root", "body")
#: A definition with an optional argument (`\newcommand{\f}[2][x]{…}`):
#: the reader keeps its body without the default, which the renderer would
#: take as one more argument, so such a definition is left out.
_OPTIONAL_RE = re.compile(
    r"\\[a-zA-Z]+\*?\s*(?:\{[^{}]*\}|\\[a-zA-Z@]+)\s*\[\s*\d+\s*\]\s*\["
)


class PayloadError(ValueError):
    """A state whose export does not place on its kept files; the message
    says where."""


@dataclass(frozen=True, slots=True)
class Span:
    """Characters `start` to `end` of one file, in code points."""

    file: str
    start: int
    end: int


class _Text:
    """A state's kept files, decoded as the reader decodes them, with the
    export's positions to offsets and back to lines."""

    def __init__(self, texts: Mapping[str, str]) -> None:
        self.texts = dict(texts)
        self._starts: dict[str, list[int]] = {}

    def text(self, file: str) -> str:
        try:
            return self.texts[file]
        except KeyError:
            raise PayloadError(f"the state keeps no file {file!r}") from None

    def starts(self, file: str) -> list[int]:
        found = self._starts.get(file)
        if found is None:
            found = [0]
            found.extend(i + 1 for i, c in enumerate(self.text(file)) if c == "\n")
            self._starts[file] = found
        return found

    def offset(self, file: str, position: Sequence[int]) -> int:
        line, column = position
        starts = self.starts(file)
        if not 1 <= line <= len(starts):
            raise PayloadError(f"{file} has no line {line}")
        return starts[line - 1] + column - 1

    def span(self, value: Mapping[str, Any], file: str | None = None) -> Span:
        """An export's span (`{file, start, end}`), or a hidden one with
        its file given."""
        path = file if file is not None else value["file"]
        return Span(
            path, self.offset(path, value["start"]), self.offset(path, value["end"])
        )

    def line_of(self, file: str, offset: int) -> int:
        starts = self.starts(file)
        lo, hi = 0, len(starts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if starts[mid] <= offset:
                lo = mid
            else:
                hi = mid - 1
        return lo + 1

    def lines_of(self, span: Span) -> tuple[int, int]:
        """The first and last line a non-empty span touches."""
        last = max(span.start, span.end - 1)
        return self.line_of(span.file, span.start), self.line_of(span.file, last)


# --- the text: reading order and runs ----------------------------------------


@dataclass(frozen=True, slots=True)
class Segment:
    """Characters `start` to `end` of one file, shown in reading order."""

    file: str
    start: int
    end: int


class RunClass(StrEnum):
    HIDDEN = "hidden"
    SPACE = "space"
    OWNED = "owned"
    UNCOVERED = "uncovered"


@dataclass(frozen=True, slots=True)
class Run:
    """A maximal run of one class and one owner. `owner` is the index of the
    owning block, the innermost (fewest owned characters) where blocks
    overlap, or -1."""

    start: int
    end: int
    cls: RunClass
    owner: int


def hidden_spans(document: Mapping[str, Any], text: _Text) -> dict[str, list[Span]]:
    return {
        f["path"]: [text.span(h, f["path"]) for h in f.get("hidden", [])]
        for f in document["files"]
        if f.get("role") in RENDERED
    }


def _past_a_blank_line_end(body: str, at: int) -> int:
    """`at`, moved past the line's end when only whitespace follows it on
    its line: a directive alone on its line closes the segment whole."""
    newline = body.find("\n", at)
    stop = len(body) if newline < 0 else newline + 1
    return stop if body[at:stop].isspace() or at == stop else at


def reading_order(document: Mapping[str, Any], text: _Text) -> list[Segment]:
    """The rendered files in reading order: from the root, each file up to
    the end of each of its body includes, then the included file, then the
    rest. A file included twice is shown where it is first reached."""
    files = document["files"]
    rendered = {f["path"] for f in files if f.get("role") in RENDERED}
    roots = [f["path"] for f in files if f.get("role") == "root"]
    if len(roots) != 1:
        raise PayloadError(f"the document half names {len(roots)} root files: {roots}")
    includes: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for include in document.get("includes", []):
        child = include.get("resolved")
        if include.get("role") != "body" or child not in rendered:
            continue
        at = text.span(include["at"])
        includes[at.file].append((at.end, child))

    segments: list[Segment] = []
    visited: set[str] = set()

    def walk(file: str) -> None:
        visited.add(file)
        body = text.text(file)
        position = 0
        emitted = False
        for end, child in sorted(includes.get(file, [])):
            if child in visited:
                continue
            end = _past_a_blank_line_end(body, end)
            if end > position:
                segments.append(Segment(file, position, end))
                emitted = True
            walk(child)
            position = end
        if position < len(body) or not emitted:
            segments.append(Segment(file, position, len(body)))

    walk(roots[0])
    unreached = sorted(rendered - visited)
    if unreached:
        raise PayloadError(
            f"rendered files no include reaches from {roots[0]}: {unreached}"
        )
    return segments


def text_runs(
    file: str, text: _Text, hidden: Sequence[Span], owned: Sequence[Sequence[Span]]
) -> list[Run]:
    """Maximal runs of one class and one owner over the whole file; `owned`
    is each block's owned spans, by its index."""
    body = text.text(file)
    size = len(body)
    owner = [-1] * size
    sizes = [sum(s.end - s.start for s in spans) for spans in owned]
    for index, spans in enumerate(owned):
        for span in spans:
            if span.file != file:
                continue
            for at in range(span.start, span.end):
                held = owner[at]
                if held < 0 or sizes[index] < sizes[held]:
                    owner[at] = index
    is_hidden = bytearray(size)
    for span in hidden:
        is_hidden[span.start : span.end] = b"\x01" * (span.end - span.start)

    runs: list[Run] = []
    start = 0
    current: tuple[RunClass, int] | None = None
    for at in range(size):
        held = owner[at]
        if is_hidden[at]:
            cls = RunClass.HIDDEN
        elif held >= 0:
            cls = RunClass.OWNED
        elif body[at].isspace():
            cls = RunClass.SPACE
        else:
            cls = RunClass.UNCOVERED
        key = (cls, held)
        if key != current:
            if current is not None:
                runs.append(Run(start, at, *current))
            current, start = key, at
    if current is not None:
        runs.append(Run(start, size, *current))
    return runs


# --- UTF-16 ------------------------------------------------------------------


class _Utf16:
    """Code-point offsets of one text as UTF-16 code units."""

    def __init__(self, body: str) -> None:
        self._table: list[int] | None = None
        if any(ord(char) > 0xFFFF for char in body):
            table = [0]
            for char in body:
                table.append(table[-1] + (2 if ord(char) > 0xFFFF else 1))
            self._table = table

    def __call__(self, offset: int) -> int:
        return offset if self._table is None else self._table[offset]


class _Offsets:
    def __init__(self, text: _Text) -> None:
        self._text = text
        self._files: dict[str, _Utf16] = {}

    def __call__(self, file: str, offset: int) -> int:
        found = self._files.get(file)
        if found is None:
            found = self._files[file] = _Utf16(self._text.text(file))
        return found(offset)

    def span(self, span: Span) -> list[Any]:
        return [span.file, self(span.file, span.start), self(span.file, span.end)]


class _ReadingPosition:
    """A character's place in reading order, through the segments."""

    def __init__(self, segments: Sequence[Segment]) -> None:
        self._by_file: dict[str, list[tuple[int, int, int]]] = defaultdict(list)
        base = 0
        for segment in segments:
            self._by_file[segment.file].append((segment.start, segment.end, base))
            base += segment.end - segment.start

    def __call__(self, file: str, offset: int) -> int | None:
        for start, end, base in self._by_file.get(file, []):
            if start <= offset < end:
                return base + offset - start
        return None


# --- the layer ---------------------------------------------------------------


def _lines(spans: Sequence[Span], text: _Text) -> list[list[Any]]:
    """Each file's line ranges a block's spans touch, consecutive ones
    merged: `[file, first, last]`."""
    ranges: list[list[Any]] = []
    for span in spans:
        if span.end <= span.start:
            continue
        first, last = text.lines_of(span)
        if ranges and ranges[-1][0] == span.file and ranges[-1][2] >= first - 1:
            ranges[-1][2] = max(ranges[-1][2], last)
        else:
            ranges.append([span.file, first, last])
    return ranges


def _group_depths(groups: Sequence[Mapping[str, Any]]) -> list[int]:
    """A group's nesting level: how many other groups hold all its blocks
    (an identical set counts once, for the earlier group)."""
    sets = [frozenset(g["blocks"]) for g in groups]
    return [
        sum(
            1
            for j, other in enumerate(sets)
            if j != i and (own < other or (own == other and j < i))
        )
        for i, own in enumerate(sets)
    ]


def _target(edge: Mapping[str, Any]) -> str:
    """An edge's target as one name: a block's or group's id, `file:PATH`,
    `scope:paper`."""
    t = edge["target"]
    if "block" in t:
        return t["block"]
    if "group" in t:
        return t["group"]
    if "file" in t:
        return f"file:{t['file']}"
    return "scope:paper"


def role_spans(
    block: Mapping[str, Any], owned: Sequence[Span], text: _Text
) -> list[tuple[Span, str]] | None:
    """A block's owned text cut into its role segments, each piece with its
    role (a segment of no role left out): a later segment from its
    excerpt's one match, as the roles sheet places it; None where one does
    not place once."""
    segments = block.get("segments") or []
    if not segments or not owned:
        return []
    extent = text.span(block["extent"])
    body = text.text(extent.file)
    runs = [(s.start, s.end) for s in owned if s.file == extent.file]
    starts = [min(a for a, _ in runs)] if runs else [extent.start]
    for segment in segments[1:]:
        found = segment_starts(body, extent.start, extent.end, runs, segment["starts"])
        if len(found) != 1:
            return None
        starts.append(found[0])
    bounds = [*starts[1:], extent.end]
    pieces = []
    for segment, start, end in zip(segments, starts, bounds, strict=True):
        if segment["role"] in (None, "none"):
            continue
        for span in owned:
            a, b = max(span.start, start), min(span.end, end)
            if span.file == extent.file and a < b:
                pieces.append((Span(span.file, a, b), segment["role"]))
    return pieces


def kept_texts(view: View) -> _Text:
    """The rendered files' texts as the state kept them."""
    store, state = view.store, view.state
    paths = [
        f["path"] for f in view.export["document"]["files"] if f.get("role") in RENDERED
    ]
    missing = [p for p in paths if p not in state.files]
    if missing:
        raise PayloadError(f"state {state.id} keeps no file {missing}")
    return _Text(
        {
            p: versions.blob(store, state.files[p]).decode("utf-8", errors="replace")
            for p in paths
        }
    )


def text_layer(view: View) -> dict[str, Any]:
    """A kept state as the text pane draws it: its rendered files with
    their runs, in reading order; its blocks, groups, parts, entries,
    standings, self marks and edges placed; its sections with their
    blocks; what renders nothing; the uncovered characters' count; the
    paper's macros, for the page's mathematics. All of it is the kept
    state's, laid out once a process, but each claim's support status,
    which a filing moves (`docs/model.md` § 8)."""
    key = (view.store.key, view.state.id)
    stamp = versions.kept_stamp(view.store)
    kept = _LAYERS.get(key, stamp)
    if kept is None:
        kept = _kept_layer(view)
        _LAYERS.put(key, stamp, kept, 1)
    standings = [
        {**s, "support": None if support is None else support.status.value}
        for s, support in zip(kept["standings"], view.support, strict=True)
    ]
    return {**kept, "standings": standings}


#: The text layers laid out, by their store and their state, while the kept
#: states stand: a state's names are its chain's (`docs/model.md` § 7).
#: Never changed once laid out; each read copies what it fills.
LAYERS_HELD = 64
_LAYERS: versions.Held[dict[str, Any]] = versions.Held(LAYERS_HELD)


def _kept_layer(view: View) -> dict[str, Any]:
    """`text_layer` with no claim's support status."""
    export = view.export
    document = export["document"]
    text = kept_texts(view)
    offsets = _Offsets(text)
    segments = reading_order(document, text)
    position = _ReadingPosition(segments)
    hidden = hidden_spans(document, text)

    def placed(value: Mapping[str, Any] | None) -> Span | None:
        if value is None or value.get("file") not in text.texts:
            return None
        return text.span(value)

    def at_line(span: Span | None) -> int | None:
        return None if span is None else text.line_of(span.file, span.start)

    def placed_all(items: Sequence[Mapping[str, Any]]) -> list[list[Any]]:
        return [offsets.span(s) for s in map(placed, items) if s is not None]

    def first(spans: Sequence[Span]) -> int | None:
        places = [
            p
            for span in spans
            if span.end > span.start
            and (p := position(span.file, span.start)) is not None
        ]
        return min(places) if places else None

    raw_blocks = export["blocks"]
    owned = [[s for s in map(placed, b["owned"]) if s is not None] for b in raw_blocks]
    counts = {ident: {"edges_in": 0, "edges_out": 0} for ident in view.blocks}
    counts |= {ident: {"edges_in": 0, "edges_out": 0} for ident in view.groups}
    for edge in view.edges:
        if edge["source"] in counts:
            counts[edge["source"]]["edges_out"] += 1
        target = _target(edge)
        if target in counts:
            counts[target]["edges_in"] += 1

    rendered = [f for f in document["files"] if f.get("role") in RENDERED]
    file_index = {f["path"]: i for i, f in enumerate(rendered)}
    files = []
    uncovered = 0
    for f in rendered:
        path = f["path"]
        runs = text_runs(path, text, hidden[path], owned)
        uncovered += sum(r.end - r.start for r in runs if r.cls is RunClass.UNCOVERED)
        files.append(
            {
                "path": path,
                "format": f.get("format"),
                "text": text.text(path),
                "runs": [
                    [offsets(path, r.start), offsets(path, r.end), r.cls.value, r.owner]
                    for r in runs
                ],
            }
        )

    def roles_placed(b: Mapping[str, Any], spans: Sequence[Span]) -> Any:
        found = role_spans(b, spans, text)
        if found is None:
            return None
        return [[*offsets.span(span), role] for span, role in found]

    blocks = [
        {
            "id": b["id"],
            "node": view.node(b["id"]),
            "handle": view.handle(b["id"]),
            "owned": [offsets.span(s) for s in block_spans],
            "lines": _lines(block_spans, text),
            "reading": first(block_spans),
            "within": b.get("within"),
            "form": b["form"],
            "environment": b.get("environment"),
            "roles": view.roles(b["id"]),
            # its role segments placed, null where one does not place once
            "role_spans": roles_placed(b, block_spans),
            **counts[b["id"]],
        }
        for b, block_spans in zip(raw_blocks, owned, strict=True)
    ]
    groups = [
        {
            "id": g["id"],
            "node": view.node(g["id"]),
            "handle": view.handle(g["id"]),
            "kind": g["kind"],
            "blocks": list(g["blocks"]),
            "names": g.get("names"),
            "named_label": g.get("named_label"),
            "role": g.get("role"),
            "depth": depth,
            **counts[g["id"]],
        }
        for g, depth in zip(
            export["groups"], _group_depths(export["groups"]), strict=True
        )
    ]
    parts = []
    for p in export.get("parts", []):
        at = placed(p["at"])
        if at is not None:
            parts.append(
                {
                    "block": p["block"],
                    "kind": p["kind"],
                    "label": p.get("label"),
                    "at": offsets.span(at),
                    "line": at_line(at),
                }
            )
    entries = []
    for e in export.get("entries", []):
        at = placed(e["at"])
        if at is not None:
            entries.append(
                {
                    "id": e["id"],
                    "block": e["block"],
                    "role": e.get("role"),
                    "at": offsets.span(at),
                    "line": at_line(at),
                }
            )
    entry_at = {e["id"]: e["at"] for e in entries}
    standings = []
    for s in view.standings:
        at = placed(s["claim"]["at"])
        standings.append(
            {
                "block": s["block"],
                "status": s["status"],
                "support": None,
                "words": s["claim"]["text"],
                "claim": None if at is None else offsets.span(at),
                "at_line": at_line(at),
                "line": view.standing_line(s),
            }
        )
    selfs = []
    for m in view.selfs:
        evidence = [placed(e["at"]) for e in m.get("evidence", [])]
        found = [s for s in evidence if s is not None]
        selfs.append(
            {
                "block": m["block"],
                "evidence": [offsets.span(s) for s in found],
                "at_line": at_line(found[0]) if found else None,
                "line": view.self_line(m),
            }
        )
    edges = [
        {
            "id": e["id"],
            "source": e["source"],
            "target": _target(e),
            "kind": e["kind"],
            "by": e["provenance"]["by"],
            "evidence": placed_all([x["at"] for x in e.get("evidence", []) if x["at"]]),
            "anchors": [
                entry_at[x] for x in e["target"].get("entries", []) if x in entry_at
            ],
            "parts": [entry_label(x) for x in e["target"].get("entries", [])],
            "line": view.edge_line(e),
        }
        for e in view.edges
    ]

    # each citation site, where a cited work is anchored (the `work` anchor)
    citations = []
    for c in document.get("citations", []):
        at = placed(c["at"])
        if at is not None:
            citations.append(
                {
                    "keys": list(c["keys"]),
                    "command": c["command"],
                    "span": offsets.span(at),
                    "line": at_line(at),
                }
            )

    # the structure the text pane renders in place (`markup.js`): each
    # environment and the name it prints, each reference and footnote
    printed = {d["name"]: d["printed"] for d in document.get("declarations", [])}
    environments = []
    for e in document.get("environments", []):
        at, body = placed(e["span"]), placed(e["body"])
        if at is not None and body is not None:
            environments.append(
                {
                    "name": e["name"],
                    "meaning": e["meaning"],
                    "kind": e["kind"],
                    "printed": printed.get(e["name"]),
                    "title": e["title"],
                    "span": offsets.span(at),
                    "body": offsets.span(body),
                }
            )
    references = [
        {
            "targets": list(r["targets"]),
            "command": r["command"],
            "span": offsets.span(at),
        }
        for r in document.get("references", [])
        if (at := placed(r["at"])) is not None
    ]
    # each footnote numbered as the paper reads, across its files
    noted = sorted(
        (
            (position(at.file, at.start) or 0, at, inside)
            for n in document.get("footnotes", [])
            if (at := placed(n["at"])) is not None
            and (inside := placed(n["text"])) is not None
        ),
        key=lambda found: found[0],
    )
    footnotes = [
        {"span": offsets.span(at), "text": offsets.span(inside), "number": k}
        for k, (_, at, inside) in enumerate(noted, start=1)
    ]

    # the definitions the page's renderer takes, in the paper's order; one
    # inside an environment other than `document` is local to it in TeX
    # (a table's `\renewcommand{\arraystretch}`), so not the paper's
    scopes = [
        text.span(e["span"])
        for e in document.get("environments", [])
        if e["name"].rstrip("*") != "document" and e["span"]["file"] in text.texts
    ]

    def local(at: Span) -> bool:
        return any(
            s.file == at.file and s.start <= at.start and at.end <= s.end
            for s in scopes
        )

    def optional(macro: Mapping[str, Any]) -> bool:
        path = macro["at"]["file"]
        if path in text.texts:
            source = text
        elif path in view.state.files:
            body = versions.blob(view.store, view.state.files[path])
            source = _Text({path: body.decode("utf-8", errors="replace")})
        else:
            return False
        at = source.span(macro["at"])
        return _OPTIONAL_RE.match(source.text(path), at.start) is not None

    macros = [
        {
            "command": m["command"],
            "name": m["name"],
            "arguments": m["arguments"],
            "definition": m["definition"],
        }
        for m in document.get("macros", [])
        if ((at := placed(m["at"])) is None or not local(at)) and not optional(m)
    ]

    headings = {
        id(section): raw
        for section, raw in zip(
            view.section_list, document.get("sections", []), strict=True
        )
    }
    members: dict[int, list[str]] = defaultdict(list)
    for ident, section in view.sections.items():
        members[id(section)].append(ident)
    sections = []
    for section in view.section_list:
        heading = placed(headings[id(section)]["heading"])
        sections.append(
            {
                "name": section.name,
                "number": section.number,
                "title": section.title,
                "label": section.label,
                "level": headings[id(section)]["level"],
                "heading": None if heading is None else offsets.span(heading),
                "line": at_line(heading),
                "blocks": members[id(section)],
            }
        )

    title = placed(document.get("title"))
    title_words = text.text(title.file)[title.start : title.end] if title else ""
    return {
        "state": {
            "id": view.state.id,
            "parent": view.state.parent,
            "command": view.state.command.value,
            "version": view.state.version,
            "root": view.state.root,
            "at": view.state.at,
        },
        "title": " ".join(title_words.split()),
        # where the title stands, for the text pane's rendering
        "title_at": None if title is None else offsets.span(title),
        "files": files,
        "segments": [
            {
                "file": file_index[s.file],
                "start": offsets(s.file, s.start),
                "end": offsets(s.file, s.end),
                "line": text.line_of(s.file, s.start),
                "resumes": any(p.file == s.file for p in segments[:i]),
            }
            for i, s in enumerate(segments)
        ],
        "unrendered": [
            {"path": f["path"], "role": f.get("role")}
            for f in document["files"]
            if f.get("role") not in RENDERED
        ],
        "never_included": list(document.get("never_included", [])),
        "uncovered": uncovered,
        "sections": sections,
        "blocks": blocks,
        "groups": groups,
        "parts": parts,
        "entries": entries,
        "standings": standings,
        "self": selfs,
        "edges": edges,
        "citations": citations,
        "environments": environments,
        "references": references,
        "footnotes": footnotes,
        "macros": macros,
        # the kinds whose target rests on their source, the query's rule
        # for which way reliance runs (`queries.show`'s `rests_on`)
        "rested_on_by_source": sorted(RESTED_ON_BY_SOURCE),
    }
