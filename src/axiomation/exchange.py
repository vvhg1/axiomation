"""The exchange format: what tools outside the package read.

This module writes its document half, the document layer, and its
node half, the cut, and the
cut as corrected by the records, and its edge half, the
edge stage's records, and the cut corrections a later stage
proposed, and the flags a build raised, and an
update's export, its NEW version (`paper_version`) and `review.update`. The output
is JSON with its keys in a fixed order, and holds no absolute path and no
time, so the same paper gives the same bytes. It reads a node half back as
a stage's base cut (`review-cut --cut`), its roles left out.

A span is written as `{"file": …, "start": [line, column], "end": [line,
column]}`: 1-based, columns counted in characters, the end exclusive. A
file's hidden spans leave out the file, which is theirs.
"""

from __future__ import annotations

import bisect
import hashlib
import json
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from axiomation.assembler import Corrected
from axiomation.cutter.blocks import BlockIndex
from axiomation.cutter.candidates import edge_candidates
from axiomation.cutter.groups import label_holders
from axiomation.cutter.hashing import block_hash
from axiomation.cutter.model import (
    Block,
    Cut,
    CutRules,
    DisplayRule,
    Doubt,
    Form,
    GivenHandles,
    Group,
    GroupKind,
    MarkedPart,
    Naming,
    PartKind,
    Role,
    Segment,
)
from axiomation.flags import Raised
from axiomation.identity import (
    IDENTITY_VERSION,
    BlockClass,
    Lineage,
    Rebased,
    changed_macros,
    vocabulary_moved,
)
from axiomation.reader.document import (
    Document,
    EnvEntry,
    FileRole,
    MapAnswer,
    RefEntry,
    Span,
    Vocabulary,
    VocabularyKind,
    document_hash,
)

if TYPE_CHECKING:
    from axiomation.questions.edges_file import Carried
from axiomation.records import (
    PERSON,
    RESTATES_NONE,
    AnswerRecord,
    CutCorrection,
    Edge,
    EdgeRecords,
    TargetKind,
)

FORMAT = "axiomation-exchange"
VERSION = 1


class _Lines:
    """Offsets to `[line, column]` and back, for one file's text."""

    def __init__(self, text: str) -> None:
        self.starts = [0]
        for index, char in enumerate(text):
            if char == "\n":
                self.starts.append(index + 1)

    def position(self, offset: int) -> list[int]:
        line = bisect.bisect_right(self.starts, offset) - 1
        return [line + 1, offset - self.starts[line] + 1]

    def offset(self, line: int, column: int) -> int:
        return self.starts[line - 1] + column - 1


def position(text: str, offset: int) -> list[int]:
    """The 1-based `[line, column]` of an offset into `text`."""
    return _Lines(text).position(offset)


def offset(text: str, line: int, column: int) -> int:
    """The offset of a 1-based `[line, column]` in `text`."""
    return _Lines(text).offset(line, column)


class _Spans:
    """Spans to their written form, over one document's files."""

    def __init__(self, document: Document) -> None:
        self.lines = {source.path: _Lines(source.text) for source in document.files}

    def __call__(self, at: Span | None) -> dict[str, Any] | None:
        if at is None:
            return None
        found = self.lines[at.file]
        return {
            "file": at.file,
            "start": found.position(at.start),
            "end": found.position(at.end),
        }


def _value(item: Any) -> Any:
    """An enum as its value; `None` stays `None`."""
    return None if item is None else item.value


def document_payload(document: Document) -> dict[str, Any]:
    """The exchange header and the document half, as plain data."""
    span = _Spans(document)
    lines = span.lines

    files = []
    for source in document.files:
        found = lines[source.path]
        files.append(
            {
                "path": source.path,
                "role": source.role.value,
                "format": source.format.value,
                "sha256": source.sha256,
                "hidden": [
                    {
                        "kind": hidden.kind.value,
                        "start": found.position(hidden.span.start),
                        "end": found.position(hidden.span.end),
                    }
                    for hidden in source.hidden
                ],
            }
        )
    return {
        "format": FORMAT,
        "version": VERSION,
        "paper": {"root": document.root, "format": document.format.value},
        "document": {
            "files": files,
            "includes": [
                {
                    "command": item.command,
                    "target": item.target,
                    "at": span(item.at),
                    "resolved": item.resolved,
                    "role": item.role.value,
                }
                for item in document.includes
            ],
            "never_included": list(document.never_included),
            "declarations": [
                {
                    "name": item.name,
                    "printed": item.printed,
                    "meaning": item.meaning,
                    "kind": item.kind.value,
                    "at": span(item.at),
                }
                for item in document.declarations
            ],
            "title": span(document.title),
            "sections": [
                {
                    "level": item.level,
                    "title": item.title,
                    "label": item.label,
                    "starred": item.starred,
                    "heading": span(item.heading),
                }
                for item in document.sections
            ],
            "environments": [
                {
                    "name": item.name,
                    "meaning": item.meaning,
                    "kind": item.kind.value,
                    "title": item.title,
                    "closed": item.closed,
                    "span": span(item.span),
                    "body": span(item.body),
                }
                for item in document.environments
            ],
            "labels": [
                {"name": item.name, "at": span(item.at)} for item in document.labels
            ],
            "references": [
                {
                    "command": item.command,
                    "word": item.word,
                    "targets": list(item.targets),
                    "at": span(item.at),
                }
                for item in document.references
            ],
            "citations": [
                {
                    "command": item.command,
                    "keys": list(item.keys),
                    "locator": item.locator,
                    "at": span(item.at),
                }
                for item in document.citations
            ],
            "quotations": [
                {
                    "at": span(item.at),
                    "text": item.text,
                    "citation": span(item.citation),
                    "keys": list(item.keys),
                    "locator": item.locator,
                }
                for item in document.quotations
            ],
            "footnotes": [
                {"at": span(item.at), "text": span(item.text)}
                for item in document.footnotes
            ],
            "macros": [
                {
                    "command": item.command,
                    "name": item.name,
                    "arguments": item.arguments,
                    "definition": item.definition,
                    "at": span(item.at),
                }
                for item in document.macros
            ],
            "comments": [
                {"kind": item.kind.value, "text": item.text, "at": span(item.at)}
                for item in document.comments
            ],
            "directives": [
                {"name": item.name, "body": item.body, "at": span(item.at)}
                for item in document.directives
            ],
            **vocabulary_payload(document.vocabulary),
        },
    }


def vocabulary_payload(vocabulary: Vocabulary) -> dict[str, Any]:
    """The vocabulary map a document was read with, as the document half's
    `vocabulary`; nothing when it was read with none."""
    if vocabulary == Vocabulary():
        return {}
    return {
        "vocabulary": {
            "environments": [
                {"name": e.name, "kind": e.kind.value, "base": e.base}
                for e in vocabulary.environments
            ],
            "references": [
                {
                    "command": r.command,
                    "list": r.takes_list,
                    "prefix": r.prefix,
                    "word": r.word,
                }
                for r in vocabulary.references
            ],
            "labels": list(vocabulary.labels),
            "answers": [
                {
                    "namespace": a.namespace,
                    "name": a.name,
                    "declaration": a.declaration,
                    "kind": a.kind,
                    "evidence": a.evidence,
                    **({"by": a.by} if a.by is not None else {}),
                }
                for a in vocabulary.answers
            ],
        }
    }


def vocabulary_of(payload: Mapping[str, Any]) -> Vocabulary:
    """The vocabulary map an export's document half was read with: made
    again from its answers (`Vocabulary.of`), so it carries onto the next
    version; its reading fields alone where it lists no answer."""
    found = payload.get("document", {}).get("vocabulary")
    if found is None:
        return Vocabulary()
    if found.get("answers"):
        return Vocabulary.of(
            [
                MapAnswer(
                    a["namespace"],
                    a["name"],
                    a["declaration"],
                    a["kind"],
                    a["evidence"],
                    a.get("by"),
                )
                for a in found["answers"]
            ]
        )
    return Vocabulary(
        tuple(
            EnvEntry(e["name"], VocabularyKind(e["kind"]), e["base"])
            for e in found["environments"]
        ),
        tuple(
            RefEntry(r["command"], r["list"], r["prefix"], r["word"])
            for r in found["references"]
        ),
        tuple(found["labels"]),
    )


def cut_payload(document: Document, cut: Cut) -> dict[str, Any]:
    """The exchange header, the document half and the node half."""
    span = _Spans(document)
    payload = document_payload(document)
    payload["rules"] = {"displays": cut.rules.displays.value}
    payload["blocks"] = [
        {
            "id": block.id,
            "extent": span(block.extent),
            "owned": [span(run) for run in block.owned],
            "within": block.within,
            "form": block.form.value,
            "environment": block.environment,
            "meaning": block.meaning,
            "roles": [role.value for role in block.roles],
            # An empty `segments` with roles set is an unsegmented mixed
            # block: a merge of blocks with different roles, its boundaries
            # not placed yet.
            "segments": [
                {
                    "starts": segment.starts,
                    "role": segment.role.value if segment.role else "none",
                }
                for segment in block.segments
            ],
            "hash": block.hash,
            "doubts": [doubt.value for doubt in block.doubts],
        }
        for block in cut.blocks
    ]
    payload["parts"] = [
        {
            "block": part.block,
            "kind": part.kind.value,
            "marker": part.marker,
            "ordinal": part.ordinal,
            "label": part.label,
            "at": span(part.at),
        }
        for part in cut.parts
    ]
    payload["groups"] = [
        {
            "id": group.id,
            "kind": group.kind.value,
            "blocks": list(group.blocks),
            "names": group.names,
            "named_label": group.named_label,
            "named_by": _value(group.named_by),
            "role": _value(group.role),
        }
        for group in cut.groups
    ]
    payload["candidates"] = [
        {
            "source": candidate.source,
            "kind": candidate.kind.value,
            "command": candidate.command,
            "target": candidate.target,
            "at": span(candidate.at),
            "resolved": candidate.resolved,
        }
        for candidate in cut.candidates
    ]
    return payload


def corrected_payload(
    document: Document,
    corrected: Corrected,
    producer: str,
    review: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The corrected cut in the cutter's shape: `cut_payload`
    with `"producer"` after `"paper"`, and at the end `"review"`, the
    caller's fields with the corrections `"applied"` and the `"problems"`
    of those that were not. It is the product's output, not an answer key,
    and carries no sentinel."""
    payload: dict[str, Any] = {}
    for key, value in cut_payload(document, corrected.cut).items():
        payload[key] = value
        if key == "paper":
            payload["producer"] = producer
    payload["review"] = {
        **(review or {}),
        "applied": list(corrected.applied),
        "problems": [
            {"correction": problem.correction, "message": problem.message}
            for problem in corrected.problems
        ],
    }
    return payload


def _placed(document: Document, words: str, spans: Sequence[Span], where: str) -> Span:
    """The first place `words` (whitespace aside) stand in `spans`' text."""
    pattern = re.compile(r"\s+".join(re.escape(w) for w in words.split()))
    for span in spans:
        found = pattern.search(document.file(span.file).text, span.start, span.end)
        if found and found.end() <= span.end:
            return Span(span.file, found.start(), found.end())
    raise ExchangeError(f"{where}: {words!r} is not in one run of its block's text")


def correction_payload(
    document: Document,
    correction: CutCorrection,
    *,
    round_: int,
    stage: str,
    applied: bool,
    problem: str | None,
    before: Sequence[Block],
    after: Sequence[str],
    reverses: Sequence[str] = (),
    by: str | None = None,
) -> dict[str, Any]:
    """A cut correction a later stage proposed, for the
    viewer: its words and evidence placed in the blocks it names, as
    they stood before it (`before`), and the blocks it left (`after`,
    empty where it was not applied). A placement that fails is the
    entry's problem, never a guess. Where it is part of a decision, or
    changed a block start back, it says so, and where a
    person gave it, `by` says so."""
    span = _Spans(document)
    runs = [run for block in before for run in block.owned]
    problems = [problem] if problem else []

    def placed(words: str) -> dict[str, Any]:
        try:
            at = span(_placed(document, words, runs, "not placed"))
        except ExchangeError as error:
            problems.append(str(error))
            at = None
        return {"text": words, "at": at}

    named: dict[str, Any] = (
        {"block": correction.block}
        if correction.block is not None
        else {"group": correction.group}
        if correction.group is not None and not correction.blocks
        else {"blocks": list(correction.blocks)}
    )
    at = [placed(words) for words in correction.at]
    evidence = [placed(words) for words in correction.evidence]
    return {
        "id": correction.id,
        "round": round_,
        "op": correction.op.value,
        "stage": stage,
        **named,
        "at": at,
        "evidence": evidence,
        "why": correction.why,
        "applied": applied,
        "problem": "; ".join(problems) or None,
        "changed": {
            "before": [
                {"id": block.id, "owned": [span(run) for run in block.owned]}
                for block in before
            ],
            "after": list(after),
        },
        **({"decision": correction.decision} if correction.decision else {}),
        **({"reverses": list(reverses)} if reverses else {}),
        **({"by": by} if by else {}),
    }


def person_review(
    *,
    applied: Sequence[Mapping[str, Any]],
    stale: Sequence[str],
    pins: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """`review.person`, its fields in a fixed order: the given answers applied, each
    `{key, stage, round}`, `round` its person round, and `by: "agent"` for an agent's (a
    person's carries no `by`, so every export before is as it was); the keys of those
    that match no state (stale, applying nothing); and every pin, each `{layer, block,
    kind, line, state}`: `block` the block's or group's id (on the edges sheet, the
    line's owner), `kind` what it holds (`added`/ `removed` a block start, `changed`
    roles, `made`/`changed`/`dissolved` a group, `written`/`deleted` a line, `kept` one
    by the keep mark), `line` the line or the change in words, `state` `held`, `held,
    meaning moved (Δ <class>)`, `held, meaning moved (by rule)` or `lapsed (<why>)`, and
    `rank` its answer's place in store order, which a later version's pins keep (the
    later answer holding where a check names two)."""
    return {
        "applied": [
            {
                "key": a["key"],
                "stage": a["stage"],
                "round": a["round"],
                **({"by": a["by"]} if a.get("by", PERSON) != PERSON else {}),
            }
            for a in applied
        ],
        "stale": list(stale),
        "pins": [
            {
                "layer": p["layer"],
                "block": p["block"],
                "kind": p["kind"],
                "line": p["line"],
                "state": p["state"],
                "rank": p["rank"],
            }
            for p in pins
        ],
    }


def flag_payload(
    document: Document,
    raised: Raised,
    handles: Mapping[str, str],
    decided: str | None = None,
) -> dict[str, Any]:
    """A flag a build raised, for the viewer, in the shape of
    a proposed correction: its blocks by id and by the edge half's handle
    (`handles`, by id; null where the edge half has none), with their owned
    text and roles as they stood on the cut it was raised on (null where
    that cut lacks the block), its words placed in those blocks, and the
    correction it led to, if any. A placement that fails leaves its `at`
    null, never a guess."""
    span = _Spans(document)
    by_id = {block.id: block for block in raised.cut.blocks}
    runs = [
        run
        for block_id in raised.flag.blocks
        if block_id in by_id
        for run in by_id[block_id].owned
    ]

    def placed(words: str) -> dict[str, Any]:
        try:
            at = span(_placed(document, words, runs, "not placed"))
        except ExchangeError:
            at = None
        return {"text": words, "at": at}

    return {
        "id": raised.flag.id,
        "stage": raised.stage,
        "by": raised.by,
        "blocks": [
            {
                "id": block_id,
                "handle": handles.get(block_id),
                "owned": [span(run) for run in by_id[block_id].owned]
                if block_id in by_id
                else None,
                "roles": [_value(r) for r in by_id[block_id].roles]
                if block_id in by_id
                else None,
            }
            for block_id in raised.flag.blocks
        ],
        "signal": raised.flag.signal.value,
        "evidence": [placed(words) for words in raised.flag.evidence],
        "note": raised.flag.note,
        "decided": decided,
    }


#: An `assumed` edge's name holds this beside its claims: the flag says
#: what the edge relies on, a hypothesis, so a reading made without it
#: rests on another edge.
ASSUMED = "⊨ assumed"
#: A source part's words in an edge's name: after `⊣ ` as a source claim's,
#: so a reading that leaves the claims an edge names aside leaves them too.
SOURCE_PART = "⊣ ⟦part⟧"
#: An edge named as it is named across states, within one cut: its
#: source, kind, target's kind and name, and its parts' and claims' words,
#: normalised (`named_ends_key`).
EdgeKey = tuple[str, str, str, str, tuple[str, ...]]
#: A line of the edges sheet named within one cut: its
#: kind (`edge`, `self`, `standing`), then what names it.
LineKey = tuple[Any, ...]


def named_ends_key(
    claims: Sequence[str],
    source_claims: Sequence[str] = (),
    restates_none: bool = False,
    assumed: bool = False,
    source_parts: Sequence[str] = (),
) -> tuple[str, ...]:
    """What an edge names beside its target's parts, as its name across
    states holds it: each target claim's words after `⊢ `, each source
    claim's (an about edge's, or an assumed edge's) after `⊣ `, each
    source part's words (a uses edge's) after `SOURCE_PART`, normalised
    and sorted, after its parts' words; `⊣ -` where its source end
    restates none; `ASSUMED` where it takes its target as a hypothesis."""
    return (
        tuple(sorted(f"⊢ {' '.join(c.split())}" for c in claims))
        + tuple(sorted(f"⊣ {' '.join(c.split())}" for c in source_claims))
        + tuple(sorted(f"{SOURCE_PART} {' '.join(w.split())}" for w in source_parts))
        + ((f"⊣ {RESTATES_NONE}",) if restates_none else ())
        + ((ASSUMED,) if assumed else ())
    )


def edge_key(edge: Edge, words: Mapping[tuple[str, str], str]) -> EdgeKey:
    """An edge's name within one cut: no evidence, no note, so a re-quote
    changes no edge; `words` gives each `(block, label)` part's words."""
    return (
        edge.source,
        edge.kind.value,
        edge.target.kind.value,
        edge.target.name,
        tuple(
            sorted(
                " ".join(words.get((edge.target.name, p), p).split())
                for p in edge.parts
            )
        )
        + named_ends_key(
            edge.claims,
            edge.source_claims,
            edge.restates_none,
            edge.assumed,
            [words.get((edge.source, p), p) for p in edge.source_parts],
        ),
    )


def part_words(edges: EdgeRecords) -> dict[tuple[str, str], str]:
    return {(e.block, e.label): e.words for e in edges.entries}


def _words(text: str) -> str:
    return " ".join(text.split())


def line_keys(records: EdgeRecords) -> dict[str, list[LineKey]]:
    """Each edge, self mark and standing of `records`, in its list's order,
    named as it is named across states: an edge by `edge_key`; a self
    mark by its block, and its evidence only where the block holds several;
    a standing by its block and status, and its claim's words only where
    the block holds several. So a re-quote changes no line, and a changed
    status is a new standing."""
    words = part_words(records)
    selfs = Counter(m.block for m in records.selfs)
    standings = Counter(s.block for s in records.standings)
    return {
        "edges": [("edge", *edge_key(e, words)) for e in records.edges],
        "self": [
            (
                "self",
                m.block,
                _words(" ".join(m.evidence)) if selfs[m.block] > 1 else "",
            )
            for m in records.selfs
        ],
        "standings": [
            (
                "standing",
                s.block,
                s.status.value,
                _words(s.claim) if standings[s.block] > 1 else "",
            )
            for s in records.standings
        ],
    }


def line_key_from_json(data: Sequence[Any]) -> LineKey:
    """A `LineKey` as JSON holds it, its lists back to tuples."""
    return tuple(tuple(x) if isinstance(x, list) else x for x in data)


def provenance(answer: AnswerRecord) -> dict[str, Any]:
    """A line's provenance as the answer that wrote it gives it: `by` the
    person, or `agent` for a model's or an agent's answer; its
    model, its key, its stage and template."""
    return {
        "by": PERSON if answer.host == PERSON else "agent",
        "model": answer.model,
        "question": answer.key,
        "detail": {"stage": answer.stage, "template": answer.template.version},
    }


#: One link of a chain of edges answers: each line it holds, by its key,
#: with the provenance it gives that line.
Link = Mapping[LineKey, dict[str, Any]]
#: Each edge's, self mark's and standing's author, by kind, in the records'
#: order; None where the last link's answer is its author.
Authors = Mapping[str, Sequence[dict[str, Any] | None]]


def answer_link(records: EdgeRecords, answer: AnswerRecord) -> Link:
    """An answer's link: each of its lines its own."""
    given = provenance(answer)
    return {key: given for keys in line_keys(records).values() for key in keys}


def line_authors(records: EdgeRecords, chain: Sequence[Link]) -> Authors:
    """Each line of `records`, the last link's, with the provenance of the
    earliest link that held it without a break: who
    first wrote it."""

    def first(key: LineKey) -> dict[str, Any] | None:
        author = chain[-1].get(key)
        if author is None:
            return None
        for link in reversed(chain[:-1]):
            if key not in link:
                break
            author = link[key]
        return author

    return {
        kind: [first(key) for key in keys] for kind, keys in line_keys(records).items()
    }


def edges_payload(
    document: Document,
    cut: Cut,
    edges: EdgeRecords,
    answer: AnswerRecord,
    handles: Mapping[str, str],
    authors: Authors | None = None,
) -> dict[str, Any]:
    """The edge half (`docs/architecture.md`'s exchange format): the entries,
    edges, self marks, standings, ambient frame and handles of one answer
    of the edge stage, on `cut`, the cut it was asked on, and that answer
    (`edges_answer`). Each excerpt is placed at its first place,
    whitespace aside, in its block's owned text, or its group's blocks'
    for a group. An entry's id is `<handle> [<label>]`, an edge's its
    record's id. Each edge, self mark and standing names its author
    (`provenance`: `authors`, by kind in the records' order, the answer
    that first wrote its line; else `answer`). `handles` maps each handle
    the sheet used to its block or group id."""
    span = _Spans(document)
    handle_of = {ident: handle for handle, ident in handles.items()}
    owned = {block.id: block.owned for block in cut.blocks}
    members = {group.id: group.blocks for group in cut.groups}

    def spans_of(ident: str) -> list[Span]:
        if ident in members:
            return [run for block in members[ident] for run in owned[block]]
        return list(owned[ident])

    def evidence(words: str, source: str) -> dict[str, Any]:
        where = f"the edge half: {handle_of.get(source, source)}"
        at = _placed(document, words, spans_of(source), where)
        return {"text": words, "at": span(at)}

    def entry_id(block: str, label: str) -> str:
        return f"{handle_of[block]} [{label}]"

    def target(e: Edge) -> dict[str, Any]:
        t = e.target
        found: dict[str, Any] = {t.kind.value: t.name}
        if t.kind is TargetKind.BLOCK and e.parts:
            found["entries"] = [entry_id(t.name, p) for p in e.parts]
        if e.claims:
            found["claims"] = list(e.claims)
        return found

    written = provenance(answer)

    def author(kind: str, n: int) -> dict[str, Any]:
        found = (authors or {}).get(kind)
        return (found[n] if found else None) or written

    payload: dict[str, Any] = {
        "entries": [
            {
                "id": entry_id(e.block, e.label),
                "block": e.block,
                "anchor": e.words,
                "marker": False,
                "at": evidence(e.words, e.block)["at"],
                "role": _value(e.role) or "none",
                **({"standard": True} if e.standard else {}),
            }
            for e in edges.entries
        ],
        "edges": [
            {
                "id": e.id,
                "source": e.source,
                "evidence": [evidence(words, e.source) for words in e.evidence],
                "target": target(e),
                "kind": e.kind.value,
                "provenance": author("edges", n),
                **({} if e.mark is None else {"mark": e.mark.value}),
                **({"where_used": True} if e.where_used else {}),
                **({"generalizes": True} if e.generalizes else {}),
                **({} if e.partial is None else {"partial": e.partial.value}),
                **({"source_claims": list(e.source_claims)} if e.source_claims else {}),
                **({"restates_none": True} if e.restates_none else {}),
                **({"assumed": True} if e.assumed else {}),
                **(
                    {"source_entries": [entry_id(e.source, p) for p in e.source_parts]}
                    if e.source_parts
                    else {}
                ),
                **({} if e.note is None else {"note": e.note}),
            }
            for n, e in enumerate(edges.edges)
        ],
        "self": [
            {
                "block": m.block,
                "evidence": [evidence(words, m.block) for words in m.evidence],
                **({} if m.partial is None else {"partial": m.partial.value}),
                **({"claims": list(m.claims)} if m.claims else {}),
                **({} if m.note is None else {"note": m.note}),
                "provenance": author("self", n),
            }
            for n, m in enumerate(edges.selfs)
        ],
        "standings": [
            {
                "block": s.block,
                "claim": evidence(s.claim, s.block),
                "status": s.status.value,
                **({} if s.declared is None else {"declared": s.declared.value}),
                **({} if s.handle is None else {"handle": s.handle}),
                **({} if s.note is None else {"note": s.note}),
                "provenance": author("standings", n),
            }
            for n, s in enumerate(edges.standings)
        ],
        "ambient": [f.block for f in edges.frame],
        "handles": dict(handle_of),
        "edges_answer": answer.key,
    }
    return payload


def edges_answer_of(export: Mapping[str, Any]) -> str | None:
    """The answer whose sheet is an export's edge half: `edges_answer`,
    else, in an export that names none (a cut file given to `diff --cut`),
    the one answer its edges all name; None where it has no edges. Refused
    where such an export's edges name several."""
    if "edges_answer" in export:
        return export["edges_answer"]
    keys = {e.get("provenance", {}).get("question") for e in export.get("edges", [])}
    if not keys:
        return None
    if len(keys) != 1 or None in keys:
        raise ExchangeError(
            f"the export's edges name {len(keys - {None})} answers and it names no "
            "edges_answer"
        )
    (key,) = keys
    return key


class ExchangeError(ValueError):
    """A payload that cannot be read as a cut of the document."""


def files_apart(payload: Mapping[str, Any], document: Document) -> list[str]:
    """The files that keep a payload off `document`: each its document half
    names (the files its reader read) that `document` does not hold byte
    for byte, and each `document` reads beyond them into its text. A file
    read for its declarations alone (a preamble, a package) is no block's,
    so one read on one side only leaves the payload's cut covering the
    text: a later reader that follows one more, or one fewer, opens a state
    kept before it (`docs/architecture.md` § 9)."""
    files = payload["document"]["files"]
    written = {f["path"]: f["sha256"] for f in files}
    held = {source.path: source.sha256 for source in document.files}
    declaring = (FileRole.PREAMBLE, FileRole.PACKAGE)
    return sorted(
        {
            f["path"]
            for f in files
            if held.get(f["path"]) != f["sha256"]
            and not (f["path"] not in held and f.get("role") in declaring)
        }
        | {
            source.path
            for source in document.files
            if source.path not in written and source.role not in declaring
        }
    )


def cut_from_payload(
    document: Document, payload: dict[str, Any], rules: CutRules, roles: bool = False
) -> Cut:
    """A node half read back as a cut of `document`: the product's corrected
    cut, or a hand-written key's `cut.json`, which writes no hash, doubt,
    candidate or rule. Its files must be the document's, byte for byte.
    Every block's roles and segments are left out, so a key's roles never
    reach the roles question, unless `roles` is set: the
    groups stage alone is asked on a cut whose roles are settled.
    A missing hash is computed, missing doubts are none, and the candidates
    are found again from the blocks, as the assembler does. `rules` stand
    where the payload gives none. Its sheet names its blocks by the
    payload's `handles` where it holds them, the names its edges answer
    was written with, past its `reserved`."""
    try:
        return _cut_from_payload(document, payload, rules, roles)
    except ExchangeError:
        raise
    except (KeyError, TypeError, ValueError, IndexError) as error:
        raise ExchangeError(f"the payload is not a cut: {error!r}") from None


def _cut_from_payload(
    document: Document, payload: dict[str, Any], rules: CutRules, roles: bool
) -> Cut:
    if payload.get("format") != FORMAT or payload.get("version") != VERSION:
        raise ExchangeError(f"not an {FORMAT} file of version {VERSION}")
    changed = files_apart(payload, document)
    if changed:
        raise ExchangeError(
            "the payload's files are not the paper's: " + ", ".join(changed)
        )
    lines = {source.path: _Lines(source.text) for source in document.files}

    def span(data: dict[str, Any]) -> Span:
        found = lines[data["file"]]
        return Span(
            data["file"], found.offset(*data["start"]), found.offset(*data["end"])
        )

    blocks = []
    for item in payload["blocks"]:
        owned = tuple(span(run) for run in item["owned"])
        hash_ = item.get("hash") or block_hash(
            document.file(run.file).text[run.start : run.end] for run in owned
        )
        blocks.append(
            Block(
                id=item["id"],
                extent=span(item["extent"]),
                owned=owned,
                within=item["within"],
                form=Form(item["form"]),
                environment=item["environment"],
                meaning=item["meaning"],
                roles=tuple(Role(r) for r in item["roles"]) if roles else (),
                segments=tuple(
                    Segment(
                        segment.get("starts", ""),
                        None if segment["role"] == "none" else Role(segment["role"]),
                    )
                    for segment in item["segments"]
                )
                if roles
                else (),
                hash=hash_,
                doubts=tuple(Doubt(d) for d in item.get("doubts", ())),
            )
        )
    parts = tuple(
        MarkedPart(
            block=item["block"],
            kind=PartKind(item["kind"]),
            marker=item["marker"],
            ordinal=item["ordinal"],
            label=item["label"],
            at=span(item["at"]),
        )
        for item in payload["parts"]
    )
    groups = tuple(
        Group(
            id=item["id"],
            kind=GroupKind(item["kind"]),
            blocks=tuple(item["blocks"]),
            names=item["names"],
            named_label=item["named_label"],
            named_by=Naming(item["named_by"]) if item["named_by"] else None,
            role=Role(item["role"]) if item["role"] else None,
        )
        for item in payload["groups"]
    )
    if "rules" in payload:
        rules = CutRules(DisplayRule(payload["rules"]["displays"]))
    index = BlockIndex(tuple(blocks))
    return Cut(
        rules=rules,
        blocks=tuple(blocks),
        parts=parts,
        groups=groups,
        candidates=edge_candidates(document, index, label_holders(document, index)),
        handles=GivenHandles(payload["handles"], frozenset(payload.get("reserved", ())))
        if payload.get("handles")
        else None,
    )


def given_handles_payload(cut: Cut) -> dict[str, Any]:
    """A cut's given handles as an identity answer's cut holds them beside
    its blocks, read back by `cut_from_payload`; none where it has none."""
    if cut.handles is None:
        return {}
    return {
        "handles": dict(cut.handles.by_id),
        "reserved": sorted(cut.handles.reserved),
    }


def placed_blocks(document: Document, payload: dict[str, Any]) -> tuple[Block, ...]:
    """A payload's blocks placed on `document`: where each stands and what
    it owns, its form and environment where the payload gives them, never
    its roles. Where a block lies is all a key's export carries beside its
    edges (its roles are its role key), and all a label's holder needs;
    a cut is `cut_from_payload`'s. Its files must be the document's."""
    try:
        changed = files_apart(payload, document)
        if changed:
            raise ExchangeError(
                "the payload's files are not the paper's: " + ", ".join(changed)
            )
        lines = {source.path: _Lines(source.text) for source in document.files}

        def span(data: dict[str, Any]) -> Span:
            found = lines[data["file"]]
            return Span(
                data["file"], found.offset(*data["start"]), found.offset(*data["end"])
            )

        return tuple(
            Block(
                id=item["id"],
                extent=span(item["extent"]),
                owned=tuple(span(run) for run in item["owned"]),
                within=item.get("within"),
                form=Form(item["form"]),
                environment=item.get("environment"),
                meaning=item.get("meaning"),
                roles=(),
                segments=(),
                hash=item.get("hash") or "",
                doubts=(),
            )
            for item in payload["blocks"]
        )
    except ExchangeError:
        raise
    except (KeyError, TypeError, ValueError, IndexError) as error:
        raise ExchangeError(f"the payload's blocks are not placed: {error!r}") from None


def dumps(payload: dict[str, Any]) -> str:
    """The payload as the exchange file's text: indented JSON, UTF-8 kept,
    one trailing newline."""
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


# --- an update's diff ---------------------------------------


#: The carried sheet's form in an identity answer: a change to how
#: `carry` carries across versions makes new keys. 2: a part
#: lost on its one heir stays there, its edges on condition, and each
#: reopen names its cause. 3: an edge line lost onto a deleted block is
#: quoted in its source's reason, whatever reopened it first, and kept
#: (`gone`).
CARRY_VERSION = "carry-across-3"


def identity_key(
    old: Document,
    new: Document,
    old_cut: Cut,
    version: str,
    edges_key: str | None = None,
    pins: Sequence[Mapping[str, Any]] = (),
) -> str:
    """An identity answer's key: its kind, identity's version, both
    versions' hashes, OLD's cut, and the edges answer it carries with the
    carry's form, so a changed tier or threshold, either text, OLD's
    corrections or another edges answer make a new key; and
    OLD's person pins where it has any, and both maps where an entry moved,
    so every key before them stands."""
    found = {
        "kind": "identity",
        "version": version,
        "edges": edges_key,
        "carry": CARRY_VERSION if edges_key else None,
        "old": document_hash(old),
        "new": document_hash(new),
        "old_cut": hashlib.sha256(
            json.dumps(cut_payload(old, old_cut), sort_keys=True).encode("utf-8")
        ).hexdigest(),
        **({"pins": [dict(p) for p in pins]} if pins else {}),
        # the map entries that moved, where any did: the same text read with
        # another map is another identity
        **_moved_entries(old, new),
    }
    return hashlib.sha256(json.dumps(found, sort_keys=True).encode("utf-8")).hexdigest()


def _moved_entries(old: Document, new: Document) -> dict[str, Any]:
    """Each map entry that differs, as both versions' maps read it."""
    names = vocabulary_moved(old, new).get("vocabulary")
    if not names:
        return {}
    return {
        "vocabulary": {
            "names": names,
            "old": vocabulary_payload(old.vocabulary).get("vocabulary"),
            "new": vocabulary_payload(new.vocabulary).get("vocabulary"),
        }
    }


def diff_payload(
    old: Document,
    old_cut: Cut,
    new: Document,
    rebased: Rebased,
    lineage: Lineage,
    carried: Carried | None = None,
    edges_key: str | None = None,
    carried_records: dict[str, Any] | None = None,
    pins: Sequence[Mapping[str, Any]] = (),
    origins: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """What `axiomation diff` writes, and an identity answer's raw:
    everything an update's replay needs from OLD, whose text is
    gone after an edit in place. Both versions' hashes; the changed macros
    and map entries;
    each link; NEW's rebased cut, as the cut reader reads a cut back, the
    blocks to re-cut, the segment starts dropped from a block that kept its
    roles, OLD's corrected boundaries the rebase could not carry (by OLD
    block, with why) and OLD's groups it could not carry (`touched`, by OLD
    id; `groups_lost`, with why); with a store, the carried
    sheet, its reopened blocks with why, the fuzzy hints, every quote
    carried across with how, and the key of OLD's edges
    answer it was carried from; and, for each OLD block that is
    not the same or moved, its owned text, roles, segment starts and
    groups, for the old-beside-new view and the closure from a deleted
    block. `carried_records`, where the sheet came from an identity answer
    (an update that asked nothing), holds OLD's edges for the closure.
    `pins`, OLD's person pins carried onto NEW, each `{layer,
    owner, kind, line, state, rank}` (`rank` its answer's
    place in store order), only where there are any. `origins`, each carried
    line (edge, self mark, standing) OLD's export named an author for,
    `{key, provenance}`, `key` its `LineKey` on NEW."""
    kept = (BlockClass.SAME, BlockClass.MOVED)
    groups_of: dict[str, list[str]] = {}
    for group in old_cut.groups:
        for block in group.blocks:
            groups_of.setdefault(block, []).append(group.id)
    shown = [b for link in lineage.links if link.cls not in kept for b in link.old]
    payload: dict[str, Any] = {
        "identity": IDENTITY_VERSION,
        "old": document_hash(old),
        "new": document_hash(new),
        "macros": sorted(changed_macros(old, new)),
        **vocabulary_moved(old, new),
        "counts": lineage.counts(),
        "links": [
            {
                "old": list(link.old),
                "new": list(link.new),
                "cls": link.cls.value,
                "tier": link.tier.value,
                **({"heir": link.heir} if link.heir else {}),
                **({"moved": True} if link.moved else {}),
            }
            for link in lineage.links
        ],
        "cut": cut_payload(new, rebased.cut) | given_handles_payload(rebased.cut),
        "recut": list(rebased.recut),
        "touched": list(rebased.touched),
        "dropped_starts": [
            {"block": d.block, "start": d.starts, "cls": d.cls.value}
            for d in rebased.dropped_starts
        ],
        "corrections_lost": [
            {"block": c.block, "op": c.op.value, "words": c.words, "why": c.why.value}
            for c in rebased.corrections_lost
        ],
        "groups_lost": [
            {"group": g.group, "why": g.why.value} for g in rebased.groups_lost
        ],
        "old_blocks": [
            {
                "id": block.id,
                "text": " ".join(old.text(run) for run in block.owned),
                "roles": [role.value for role in block.roles],
                "segments": [s.starts for s in block.segments],
                "groups": groups_of.get(block.id, []),
                **({"meaning": block.meaning} if block.meaning else {}),
            }
            for block in old_cut.blocks
            if block.id in shown
        ],
    }
    if carried is not None:
        payload["carried"] = {
            "from": edges_key,
            "sheet": carried.sheet,
            "reopened": list(carried.reopened),
            "why": dict(carried.why),
            "hints": [
                {
                    "handle": h.handle,
                    "excerpt": h.excerpt,
                    "hint": h.words,
                    "block": h.block,
                }
                for h in carried.hints
            ],
            # Every quote carried across, for an update's export,
            # so its replay needs no OLD.
            "excerpts": [
                {"block": x.block, "excerpt": x.excerpt, "cls": x.cls.value}
                for x in carried.excerpts
            ],
            # The changed block each reopen comes from, its `from` in the
            # update's export.
            "causes": dict(carried.causes),
            # Each edge line dropped onto a deleted block, shown in its
            # source's task in the update; only where any was.
            **(
                {
                    "gone": [
                        {
                            "source": g.source,
                            "line": g.line,
                            "target": g.target,
                            "old": g.old,
                        }
                        for g in carried.gone
                    ]
                }
                if carried.gone
                else {}
            ),
            # Only where carried from an identity answer, whose edges are
            # its carried sheet's and no store holds.
            **({"records": carried_records} if carried_records is not None else {}),
            **({"pins": [dict(p) for p in pins]} if pins else {}),
            # Each carried edge OLD's export named an author for, by its key
            # on NEW; only where made.
            **({"origins": [dict(o) for o in origins]} if origins is not None else {}),
        }
    return payload


# --- an update's export -------------------------------------


def update_export(
    document: Document,
    cut: Cut | Corrected,
    producer: str,
    paper_version: str,
    review: dict[str, Any],
    edge_half: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """An update's export: `cut`, the cut it was
    asked on, in the corrected cut's shape with no correction applied (or,
    corrected by the update's steps or rounds, the rebased cut corrected),
    its `paper_version` (NEW's document hash, which a
    build's export leaves out; the format's own `version` is taken) after
    `producer`, the edge half before `review`, and `review` the caller's
    fields, `update` among them."""
    corrected = cut if isinstance(cut, Corrected) else Corrected(cut, (), ())
    payload: dict[str, Any] = {}
    for key, value in corrected_payload(document, corrected, producer, review).items():
        payload[key] = value
        if key == "producer":
            payload["paper_version"] = paper_version
    if edge_half is None:
        return payload
    held = payload.pop("review")
    return {**payload, **edge_half, "review": held}


def update_review(
    *,
    old: str,
    new: str,
    previous: Mapping[str, Sequence[str]],
    reopened: Sequence[Mapping[str, Any]],
    deltas: Sequence[Mapping[str, Any]],
    hints: Sequence[Mapping[str, Any]],
    cost: Mapping[str, Mapping[str, Any]],
    not_asked: Sequence[Mapping[str, Any]] = (),
    fresh: bool = False,
    mode: str = "hold-cut",
    excerpts: Sequence[Mapping[str, Any]] = (),
    shape: str | None = None,
    moved_by_rule: Sequence[Mapping[str, Any]] = (),
    stale: Sequence[Mapping[str, Any]] = (),
    reread: Sequence[Mapping[str, Any]] = (),
    lost: Sequence[Mapping[str, Any]] = (),
    closed_by_delta: Sequence[str] = (),
    corrections_lost: Sequence[Mapping[str, Any]] = (),
    groups_lost: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """`review.update`, its fields in
    a fixed order: both versions' hashes; each NEW block's OLD blocks; the
    mode, "cutter" or "hold-cut"; whether the edges were asked
    fresh (the switch); the owners left open, each `{handle, why, session,
    from}`, `session` 1 or 2; the owners a follow-up would open but none
    asked, each `{handle, why, from}`; the Δs, each `{block, handle, entry,
    words, class, note, by}`, `by` "writer" or "rule", `words` the part's
    anchor words (null for the block whole), and `root` on a writer's Δ
    on a block the rule moved, the edit its move began at; the fuzzy hints, each
    `{excerpt, hint, kept}`; every quote carried across, each `{block,
    excerpt, cls}`; each question's cost, by its key, with its stage; and,
    where any, `corrections_lost`, OLD's corrected boundaries the rebase
    could not carry, each `{block, op, words, why}`, `block` OLD's, and
    `groups_lost`, OLD's groups it could not carry, each `{group, why}`,
    `group` OLD's.
    An update of one session adds its `shape`, "single-session",
    the owners whose meaning the rule moved, each `{handle, causes}`, and
    the owners stale, each `{handle, causes, cleared_by}`, a cause `{why,
    from, via, root}` (`root` the edited block the change began at, null
    where none did) and `cleared_by` the key of a later
    question of the update that re-checked the owner, else null, and,
    where any, `closed_by_delta`, the blocks the rule would have moved
    whose own Δ closed the change; an update per stage adds none.
    Either adds `reread` where an answer after the session re-read an owner,
    each `{handle, by}`, `by` the first such answer's
    key; a Δ's `by` is also "person" or "agent" where one corrected it.
    Either adds `lost` where an edge onto a deleted block was lost and the
    sessions drew none in its place, each `{handle, causes, cleared_by}`
    as `stale`'s, `cleared_by` the key of a person's or an agent's answer
    that re-read the source."""
    found = {
        "old": old,
        "new": new,
        "previous": {block: list(olds) for block, olds in previous.items()},
        "mode": mode,
        "fresh": fresh,
        "reopened": [
            {
                "handle": r["handle"],
                "why": r["why"],
                "session": r["session"],
                "from": r["from"],
            }
            for r in reopened
        ],
        "not_asked": [
            {"handle": r["handle"], "why": r["why"], "from": r["from"]}
            for r in not_asked
        ],
        "deltas": [
            {
                "block": d["block"],
                "handle": d["handle"],
                "entry": d["entry"],
                "words": d["words"],
                "class": d["class"],
                "note": d["note"],
                "by": d["by"],
                **({"root": d["root"]} if d.get("root") else {}),
            }
            for d in deltas
        ],
        "hints": [
            {"excerpt": h["excerpt"], "hint": h["hint"], "kept": h["kept"]}
            for h in hints
        ],
        "excerpts": [
            {"block": x["block"], "excerpt": x["excerpt"], "cls": x["cls"]}
            for x in excerpts
        ],
        "cost": {
            key: {"stage": c["stage"], "cost": c["cost"]} for key, c in cost.items()
        },
        **(
            {
                "corrections_lost": [
                    {k: c[k] for k in ("block", "op", "words", "why")}
                    for c in corrections_lost
                ]
            }
            if corrections_lost
            else {}
        ),
        **(
            {"groups_lost": [{k: g[k] for k in ("group", "why")} for g in groups_lost]}
            if groups_lost
            else {}
        ),
    }

    def causes(entry: Mapping[str, Any]) -> list[dict[str, Any]]:
        return [
            {
                "why": c["why"],
                "from": c["from"],
                "via": c["via"],
                "root": c["root"],
            }
            for c in entry["causes"]
        ]

    # `lost` before `reread`, which a later answer's fold appends
    read = (
        {
            "lost": [
                {
                    "handle": s["handle"],
                    "causes": causes(s),
                    "cleared_by": s["cleared_by"],
                }
                for s in lost
            ]
        }
        if lost
        else {}
    ) | (
        {"reread": [{"handle": r["handle"], "by": r["by"]} for r in reread]}
        if reread
        else {}
    )
    if shape is None:
        return {**found, **read}

    return {
        **found,
        "shape": shape,
        "moved_by_rule": [
            {"handle": m["handle"], "causes": causes(m)} for m in moved_by_rule
        ],
        "stale": [
            {"handle": s["handle"], "causes": causes(s), "cleared_by": s["cleared_by"]}
            for s in stale
        ],
        **({"closed_by_delta": list(closed_by_delta)} if closed_by_delta else {}),
        **read,
    }
