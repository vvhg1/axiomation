"""The reads over the kept states: a block with its edges,
what an edit to it reaches, blocks by filter, and what changed between two
kept states or since the latest one.

Each read gives a `Report`: a head and named sections of items, each item a
line as the edges sheet prints it (`→ uses tgt [part] · "excerpt"`, `⊢
status · "claim"`), so an agent greps it as it greps `edges.md`, and the
item's data for `--json`. `render` bounds every section to `limit` items
and says how many it left out; a block's text is cut at `TEXT_CHARACTERS`
unless asked whole. Nothing here writes to a store: what a read derives
from kept states may be kept in the user's cache (`derived`).
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
import shlex
from collections import Counter
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from heapq import heappop, heappush
from itertools import count
from typing import Any

from axiomation import (
    derived,
    edits,
    judgments,
    positioning,
    publications,
    rulings,
    soundness,
    suggestions,
    versions,
)
from axiomation.changes import Layer
from axiomation.claim_status import (
    CIRCLE_CLEARS,
    ClaimStatus,
    Folded,
    finding_clears,
    fold,
    status_clears,
    support_clears,
)
from axiomation.closure import (
    EdgeGraph,
    Reach,
    Reached,
    from_export,
    reach,
    rule,
)
from axiomation.cutter.model import CutRules, DisplayRule
from axiomation.established import (
    Circle,
    Coarse,
    Completeness,
    Covered,
    Established,
    Missing,
    completeness,
    relied_on,
    restating_conjectures,
    settled,
)
from axiomation.evidence import PageStamp, work_of
from axiomation.exchange import ExchangeError, cut_from_payload
from axiomation.identity import GroupLapse, Lapse
from axiomation.marks import Cleared, Mark, Unread, folded, unread_rings
from axiomation.questions import edges_file
from axiomation.questions.edge_sheet import delta_line, norm
from axiomation.questions.edges_file import DELETED_BLOCK as DELETED
from axiomation.questions.review_file import (
    FOUND_KEY,
    LINKED_KEY,
    Session,
    session_of,
)
from axiomation.reader.sentences import sentence_at
from axiomation.records import (
    PERSON,
    AnchorKind,
    Aspect,
    Finding,
    FindingAnswer,
    RealCircle,
    RecordStore,
    Ruling,
    RulingKind,
    Status,
    Subject,
    Verdict,
    filed_after,
)
from axiomation.versions import Command, State, VersionError

#: Items a section shows by default.
LIMIT = 40
#: Characters of a block's text shown by default.
TEXT_CHARACTERS = 400
RUN_BREAK = " ⟦…⟧ "


class QueryError(ValueError):
    """A read that cannot be answered as asked: a reference that names no
    block or several, a state that is not before another."""


class Unnamed(QueryError):
    """A reference that names no block or group, by any name or prefix."""


# --- reports ------------------------------------------------------------------


@dataclass(frozen=True)
class Item:
    """One line of a section (it may span lines: a change's old and new
    text), and its data for `--json`."""

    line: str
    data: Any


@dataclass
class Report:
    head: list[str] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)
    sections: dict[str, list[Item]] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def render(report: Report, limit: int = LIMIT, as_json: bool = False) -> str:
    """The report as text or JSON, each section at most `limit` items (0:
    all), then how many more; in JSON each section's items' data and their
    lines, as the text prints them (the viewer's card prints those)."""

    def bounded(items: list[Item]) -> tuple[list[Item], int]:
        if limit <= 0 or len(items) <= limit:
            return items, 0
        return items[:limit], len(items) - limit

    if as_json:
        out: dict[str, Any] = dict(report.data)
        for name, items in report.sections.items():
            shown, more = bounded(items)
            out[name] = {
                "total": len(items),
                "items": [i.data for i in shown],
                "lines": [i.line for i in shown],
                "more": more,
            }
        if report.notes:
            out["notes"] = report.notes
        return json.dumps(out, indent=2, ensure_ascii=False) + "\n"
    lines = list(report.head)
    for name, items in report.sections.items():
        shown, more = bounded(items)
        lines.append(f"{name.replace('_', ' ')} ({len(items)}):")
        lines += ["  " + i.line.replace("\n", "\n  ") for i in shown]
        if more:
            lines.append(f"  … {more} more: narrow the query or raise --limit (0: all)")
    lines += [f"note: {n}" for n in report.notes]
    return "\n".join(lines) + "\n"


class GroupBy(StrEnum):
    """What `open --by` and `check --by` group a section's items by: the
    outermost section that holds each, its block's roles, or a claim's
    `true` reading (an item's status where it is no claim)."""

    SECTION = "section"
    ROLE = "role"
    READING = "reading"

    def key(self, item: Item) -> str:
        d = item.data
        if self is GroupBy.SECTION:
            found = d.get("top_section")
            return found or (
                "(the paper)" if d.get("block") is None else "(before any section)"
            )
        if self is GroupBy.ROLE:
            return d.get("roles") or "(the paper)"
        return d.get("reading") or d.get("status") or d.get("kind") or "(none)"


def grouped(report: Report, by: GroupBy) -> Report:
    """The report with each section split by `by`, each group a section of
    its own (`unsupported · §2`), in the order its first item comes."""
    sections: dict[str, list[Item]] = {}
    for name, items in report.sections.items():
        for item in items:
            sections.setdefault(f"{name} · {by.key(item)}", []).append(item)
    return Report(report.head, report.data, sections, report.notes)


def clipped(text: str, whole: bool = False) -> str:
    if whole or len(text) <= TEXT_CHARACTERS:
        return text
    return text[:TEXT_CHARACTERS].rstrip() + f" … ({len(text)} characters)"


# --- a kept state, read -------------------------------------------------------


@dataclass(frozen=True)
class Section:
    """A section of the paper: its number (empty where starred), title and
    label."""

    number: str
    title: str
    label: str | None

    @property
    def name(self) -> str:
        return f"§{self.number}" if self.number else f"§ {self.title[:40]}"

    @property
    def given_number(self) -> str:
        """Its number, or an unnumbered section's as its title gives it: the
        title's first word where it is a number, after an optional
        "Appendix" (`Appendix A1 · …` is `A1`, `A1.2 Setup` `A1.2`); empty
        where it has none."""
        if self.number:
            return self.number
        found = _GIVEN_NUMBER.match(self.title)
        return "" if found is None else found["number"] or found["letter"]


#: A section number as a reader names it: `3.2`, or an appendix's `A1.2`,
#: `A.1` or `B`.
_NUMBER = r"[A-Za-z]?\d+(?:\.\d+)*|[A-Za-z](?:\.\d+)*"
#: A title's own number, before a space, a colon or a closing dot: a bare
#: letter only after "Appendix", a range (`A1.1–A1.7`) never.
_GIVEN_NUMBER = re.compile(
    r"(?:appendix\s+(?P<letter>[A-Za-z])|(?:appendix\s+)?"
    r"(?P<number>[A-Za-z]?\d+(?:\.\d+)*|[A-Za-z](?:\.\d+)+))(?=[:.]?(?:\s|$))",
    re.I,
)


def _numbered(sections: Sequence[Mapping[str, Any]]) -> list[Section]:
    """The export's sections numbered as the paper numbers them, level by
    level, a starred one unnumbered."""
    counters: list[int] = []
    top = min((s["level"] for s in sections), default=1)
    found = []
    for s in sections:
        depth = s["level"] - top + 1
        number = ""
        if not s["starred"]:
            counters = counters[:depth] + [0] * (depth - len(counters))
            counters[depth - 1] += 1
            number = ".".join(str(c) for c in counters)
        found.append(Section(number, s["title"], s["label"]))
    return found


def _block_sections(
    export: Mapping[str, Any], numbered: Sequence[Section]
) -> dict[str, Section]:
    """Each block's section: the last whose heading starts before it in
    reading order (a heading's first block is the first after it in its
    file); blocks before any heading have none."""
    sections = export["document"].get("sections", [])
    blocks = export["blocks"]
    firsts: list[tuple[int, Section]] = []
    at = 0
    for s, section in zip(sections, numbered, strict=True):
        heading = s["heading"]
        for i in range(at, len(blocks)):
            extent = blocks[i]["extent"]
            if (
                extent["file"] == heading["file"]
                and extent["start"] >= heading["start"]
            ):
                firsts.append((i, section))
                at = i
                break
    found: dict[str, Section] = {}
    current: Section | None = None
    pending = iter(firsts)
    following = next(pending, None)
    for i, block in enumerate(blocks):
        while following is not None and following[0] <= i:
            current = following[1]
            following = next(pending, None)
        if current is not None:
            found[block["id"]] = current
    return found


class View:
    """A kept state read for queries: its export indexed, its blocks' node
    ids, handles, texts and sections. `reader`, where given, is one a
    longer-lived caller keeps while the kept states stand
    (`versions.kept_stamp`), so the steps between states are not read again
    for each view."""

    def __init__(
        self,
        store: RecordStore,
        state: State,
        reader: judgments.Reader | None = None,
    ) -> None:
        self.store = store
        self.state = state
        self._reader: judgments.Reader | None = reader
        self._readings: judgments.Readings | None = None
        self._marks: tuple[tuple[Mark, ...], tuple[Cleared, ...]] | None = None
        self._unread: tuple[Unread, ...] | None = None
        self._established: tuple[Established, ...] | None = None
        self._circles: tuple[Circle, ...] = ()
        self._completeness: tuple[Covered | None, ...] | None = None
        self._soundness: Soundness | None = None
        self._later_rule: LaterRule | None = None
        self._rulings: dict[Subject, rulings.Ruled] | None = None
        self._support: tuple[rulings.ClaimSupport | None, ...] | None = None
        self._relied: dict[tuple[str, str], tuple[str, ...]] | None = None
        self._claim_statuses: tuple[Folded, ...] | None = None
        self._using: set[str] | None = None
        self._suggestions: tuple[suggestions.SuggestionRead, ...] | None = None
        self._checks: list[positioning.CheckRead] | None = None
        self._read_real: dict[str, ReadReal] | None = None
        self._lines: edges_file.ExportLines | None = None
        self.export: dict[str, Any] = versions.kept_payload(store, state)
        export = self.export
        self.blocks: dict[str, dict[str, Any]] = {b["id"]: b for b in export["blocks"]}
        self._order = {b: i for i, b in enumerate(self.blocks)}
        self.groups: dict[str, dict[str, Any]] = {g["id"]: g for g in export["groups"]}
        #: id → handle, blocks and groups, as the export names them: how its
        #: own records name its blocks
        self.handles: dict[str, str] = dict(export.get("handles") or {})
        self.nodes: dict[str, str] = {n.block: n.node for n in state.nodes} | dict(
            state.groups
        )
        self._kept: versions.Handles | None = None
        self._names: dict[str, str] | None = None
        self._minted: dict[str, str] | None = None
        self._short: dict[str, str] | None = None
        self.edges: list[dict[str, Any]] = list(export.get("edges", []))
        self.selfs: list[dict[str, Any]] = list(export.get("self") or [])
        self.standings: list[dict[str, Any]] = list(export.get("standings", []))
        #: the paper's sections in its order, numbered as it numbers them
        self.section_list: list[Section] = _numbered(
            export["document"].get("sections", [])
        )
        self.sections = _block_sections(export, self.section_list)
        self.groups_of: dict[str, list[str]] = {}
        for g in export["groups"]:
            for b in g["blocks"]:
                self.groups_of.setdefault(b, []).append(g["id"])
        kept_texts = versions.texts(store)
        self.texts: dict[str, str] = {
            n.block: norm(RUN_BREAK.join(kept_texts[n.text])) for n in state.nodes
        }

    @property
    def kept(self) -> versions.Handles:
        """Each node's handle along the states to this one (`docs/model.md`
        § 7), computed once."""
        if self._kept is None:
            self._kept = versions.handles(self.store, self.state)
        return self._kept

    @property
    def names(self) -> dict[str, str]:
        """Id → its node's kept handle, blocks and groups: the name shown."""
        if self._names is None:
            self._names = self.handles | self.kept.at(self.state)
        return self._names

    def _folded(self) -> tuple[tuple[Mark, ...], tuple[Cleared, ...]]:
        if self._marks is None:
            self._marks = folded(self.store, self.state, self.readings, self.reader)
        return self._marks

    @property
    def marks(self) -> tuple[Mark, ...]:
        """The stale marks open at this state, the gate's and the
        descriptions, computed once."""
        return self._folded()[0]

    @property
    def gate_marks(self) -> tuple[Mark, ...]:
        """The open marks a gate asks: no description."""
        return tuple(m for m in self.marks if m.gate)

    @property
    def unread(self) -> tuple[Unread, ...]:
        """What the updates along this state's chain carried unread, an
        item per update and edit."""
        if self._unread is None:
            self._unread = unread_rings(
                self.store, self.state, self.readings, self.reader
            )
        return self._unread

    @property
    def cleared_marks(self) -> tuple[Cleared, ...]:
        """The marks a reread or a reading closed along this state's
        chain."""
        return self._folded()[1]

    @property
    def reader(self) -> judgments.Reader:
        """The kept states and the steps between them, read once for the
        readings, the suggestions and the rulings."""
        if self._reader is None:
            self._reader = judgments.kept_reader(self.store)
        return self._reader

    @property
    def readings(self) -> judgments.Readings:
        """The judgments and findings read at this state, computed
        once."""
        if self._readings is None:
            self._readings = judgments.readings(self.store, self.state, self.reader)
        return self._readings

    @property
    def suggestions(self) -> tuple[suggestions.SuggestionRead, ...]:
        """The suggestions that bear on this state, walked to it, computed
        once."""
        if self._suggestions is None:
            self._suggestions = suggestions.suggestions(
                self.store, self.state, self.reader
            )
        return self._suggestions

    @property
    def rulings(self) -> dict[Subject, rulings.Ruled]:
        """Each claim's rulings at this state, by the claim, computed
        once."""
        if self._rulings is None:
            self._rulings = rulings.rulings(self.store, self.state, self.reader)
        return self._rulings

    @property
    def checks(self) -> list[positioning.CheckRead]:
        """The contribution session's checks at this state, computed once."""
        if self._checks is None:
            self._checks = positioning.checks(self.store, self.state, self.reader)
        return self._checks

    @property
    def positioned(self) -> list[Subject]:
        """Each claim a `positioning` ruling takes out of
        `unsupported-claims`, in the order of `standings`: the
        contribution session's to read."""
        return [
            Subject(e.claim.node, None, e.claim.words)
            for e, support in zip(self.established, self.support, strict=True)
            if support is not None
            and support.status is rulings.Support.NO_ARGUMENT_OWED
            and support.ruling is RulingKind.POSITIONING
        ]

    @property
    def established(self) -> tuple[Established, ...]:
        """Each claim established or not at this state,
        computed once."""
        return self._settled()[0]

    @property
    def circles(self) -> tuple[Circle, ...]:
        """The circles of what claims rest on at this state, those holding
        the most claims back first, computed with `established`."""
        return self._settled()[1]

    @property
    def read_real(self) -> dict[str, ReadReal]:
        """Each circle's key to the latest word that it is real, read
        once."""
        if self._read_real is None:
            self._read_real = read_real(self.store)
        return self._read_real

    def _settled(self) -> tuple[tuple[Established, ...], tuple[Circle, ...]]:
        if self._established is None:
            self._established, self._circles = settled(self.readings, self.marks)
        return self._established, self._circles

    @property
    def completeness(self) -> tuple[Covered | None, ...]:
        """Each claim's completeness at this state, in the order
        of `standings`, computed once."""
        if self._completeness is None:
            self._completeness = completeness(self.readings)
        return self._completeness

    @property
    def soundness(self) -> Soundness:
        """The four kinds the kept graph got wrong or cannot settle, computed once."""
        if self._soundness is None:
            self._soundness = soundness_items(self)
        return self._soundness

    @property
    def later_rule(self) -> LaterRule:
        """What a rule added since refuses in the state's own files, computed
        once."""
        if self._later_rule is None:
            self._later_rule = later_rule_items(self)
        return self._later_rule

    def completeness_of(self, standing: Mapping[str, Any]) -> Covered | None:
        return self.completeness[self.standings.index(standing)]

    @property
    def relied(self) -> dict[tuple[str, str], tuple[str, ...]]:
        """What relies on each claim in one hop, by its node and words
        (`established.relied_on`), computed once: an `owed` ruling's weight
        beside its severity, and the guard on a clearing one."""
        if self._relied is None:
            self._relied = relied_on(self.readings)
        return self._relied

    @property
    def support(self) -> tuple[rulings.ClaimSupport | None, ...]:
        """Each claim's support status at this state (`docs/model.md` § 8), in the
        order of `standings`, computed once: what `unsupported-claims`, the
        open view and the viewer read alike. None for a `conjectured`
        claim."""
        if self._support is None:
            statuses = {(s.aspect, s.subject): s for s in self.readings.statuses}
            relied = self.relied if self.rulings else {}
            restating = restating_conjectures(self.readings)
            found = []
            for e in self.established:
                c = e.claim
                on = Subject(c.node, None, c.words)
                s = statuses.get((Aspect.TRUE, on))
                found.append(
                    rulings.support(
                        c.status,
                        own_gap(e),
                        self.rulings.get(on),
                        declared=c.declared,
                        true_disputed=s is not None
                        and s.status is judgments.StatusKind.DISPUTED,
                        true_verified=s is not None
                        and s.status is judgments.StatusKind.HELD
                        and s.held is not None
                        and s.held.judgment.verdict is Verdict.VERIFIED,
                        uses_a_result=self.uses_a_result(c.node),
                        relied=relied.get((c.node, c.words), ()),
                        conjectures=tuple(
                            self.readings.at.handle(k.node)
                            for k, _ in restating.get((c.node, c.words), ())
                        ),
                        by_fallback=all(
                            fallback
                            for _, fallback in restating.get((c.node, c.words), ())
                        ),
                    )
                )
            self._support = tuple(found)
        return self._support

    def support_of(self, standing: Mapping[str, Any]) -> rulings.ClaimSupport | None:
        return self.support[self.standings.index(standing)]

    @property
    def claim_statuses(self) -> tuple[Folded, ...]:
        """Each standing's claim status (`claim_status.fold`), in the
        standings' order, computed once."""
        if self._claim_statuses is None:
            statuses = {(s.aspect, s.subject): s for s in self.readings.statuses}
            # the proofs the state still offers: a reading of a justifies
            # line retracted since is on no route
            offered = set(judgments.offered(self.readings.at))
            proofs: dict[tuple[str, str], list[judgments.SubjectStatus]] = {}
            for (aspect, on), s in statuses.items():
                if (
                    aspect is Aspect.VALID
                    and on.target is not None
                    and on.words
                    and (aspect, on) in offered
                ):
                    proofs.setdefault((on.target, on.words), []).append(s)
            warned = publications.warned(
                {k: w.publication for k, w in self.readings.works.items()}
            )
            # a citation's reading asked again, by the block it is on
            cited_again: dict[str, list[judgments.SubjectStatus]] = {}
            for (aspect, on), s in statuses.items():
                if aspect is Aspect.CITED and s.grown:
                    cited_again.setdefault(on.node, []).append(s)
            found = []
            for e, support in zip(self.established, self.support, strict=True):
                c = e.claim
                true = statuses.get((Aspect.TRUE, Subject(c.node, None, c.words)))
                route = ([] if true is None else [true]) + proofs.get(
                    (c.node, c.words), []
                )
                findings = [
                    f
                    for f in self.readings.findings
                    if f.open and any(judgments.bears_on(f, s.subject) for s in route)
                ]
                again = [s for s in route if s.grown] + cited_again.get(c.node, [])
                found.append(
                    fold(
                        e,
                        support,
                        route,
                        findings,
                        self.readings.at.handle,
                        warned,
                        again,
                    )
                )
            self._claim_statuses = tuple(found)
        return self._claim_statuses

    def claim_status_of(self, standing: Mapping[str, Any]) -> Folded:
        return self.claim_statuses[self.standings.index(standing)]

    def uses_a_result(self, node: str) -> bool:
        """Whether a block uses another block or group: the result an
        `immediate` ruling names is one of the paper's own."""
        if self._using is None:
            self._using = {
                e["source"]
                for e in self.edges
                if e["kind"] == "uses"
                and (e["target"].get("block") or e["target"].get("group"))
                not in (None, e["source"])
            }
        return self.readings.at.ident_of.get(node) in self._using

    def established_of(self, ident: str) -> list[Established]:
        node = self.node(ident)
        return [e for e in self.established if e.claim.node == node]

    def statuses_of(self, ident: str) -> list[judgments.SubjectStatus]:
        """The subjects a block's or group's readings show on: a group's
        its own, a block's its own and its groups' where it is their first."""
        if ident in self.groups:
            node = self.node(ident)
            return [s for s in self.readings.statuses if s.subject.node == node]
        return self.readings.statuses_on(ident)

    def order(self, ident: str | None) -> int:
        """A block's place in the paper's order, a group's its first
        block's; after every block for a file or the paper."""
        if ident in self.groups:
            ident = self.groups[ident]["blocks"][0]
        return self._order.get(ident, len(self._order)) if ident else len(self._order)

    # names

    def within(self, block: str) -> list[Section]:
        """A block's section and every section it lies within, innermost
        first."""
        section = self.sections.get(block)
        if section is None:
            return []
        levels = [s["level"] for s in self.export["document"].get("sections", [])]
        at = next(i for i, s in enumerate(self.section_list) if s is section)
        found, level = [section], levels[at]
        for i in range(at - 1, -1, -1):
            if levels[i] < level:
                found.append(self.section_list[i])
                level = levels[i]
        return found

    def place(self, block: str | None) -> dict[str, Any]:
        """Where an item stands, as the open view and the gates group it:
        its block's section, the outermost section that holds it, and its
        roles; all None for the paper's own."""
        if block is None:
            return {"section": None, "top_section": None, "roles": None}
        within = self.within(block)
        return {
            "section": within[0].name if within else None,
            "top_section": within[-1].name if within else None,
            "roles": self.role_names(block),
        }

    def handle(self, ident: str) -> str:
        """A block's or group's handle: its node's kept handle."""
        return self.names.get(ident, ident)

    def node(self, ident: str) -> str:
        return self.nodes.get(ident, "-")

    def shown(self, ident: str) -> str:
        """A block's or group's node id as a person reads it: its shortest
        unique prefix at this state (`versions.short_ids`)."""
        if self._short is None:
            self._short = versions.short_ids(self.nodes.values())
        return self._short.get(self.node(ident), "-")

    def minted(self) -> dict[str, str]:
        """The handle its words give each block and group now, by id, where it
        differs from the kept one, computed once (the state's files read)."""
        if self._minted is None and "rules" not in self.export:
            # a key's export: no run's cut, so no sheet's names
            self._minted = {}
        if self._minted is None:
            document = versions.document_at(self.store, self.state)
            rules = CutRules(DisplayRule(self.export["rules"]["displays"]))
            cut = cut_from_payload(document, self.export, rules, roles=True)
            now = edges_file.handles(document, dataclasses.replace(cut, handles=None))
            self._minted = {i: h for h, i in now.items() if self.names.get(i) != h}
        return self._minted

    def resolve(self, ref: str) -> str:
        """The block or group `ref` names: a node id; else a kept handle,
        refused where its node is not at this state; else a block or group
        id; else an alias (`docs/architecture.md` § 9), a name a node bore before or
        the one its words give now, refused where it names several nodes;
        else a prefix of node ids and kept handles naming one block (a node id
        is shown by its shortest unique prefix, `short_ids`); refused, naming
        the candidates, where it names several or none."""
        by_node = {node: ident for ident, node in self.nodes.items()}
        if ref in by_node:
            return by_node[ref]
        by_handle = {handle: ident for ident, handle in self.names.items()}
        if ref in by_handle:
            return by_handle[ref]
        if ref in self.kept.holder:
            raise QueryError(self._not_here(ref, self.kept.holder[ref]))
        if ref in self.blocks or ref in self.groups:
            return ref
        bearers = self.kept.aliases.get(ref, ())
        if len(bearers) > 1:
            named = ", ".join(f"{n} ({self.kept.kept.get(n, n)})" for n in bearers)
            raise QueryError(f"{ref!r} was a handle of several nodes: {named}")
        if bearers:
            (node,) = bearers
            if node not in by_node:
                raise QueryError(self._not_here(ref, node))
            return by_node[node]
        now = [i for i, h in self.minted().items() if h == ref]
        if len(now) == 1:
            return now[0]
        named = {n: i for n, i in by_node.items() if n.startswith(ref)} | {
            h: i for h, i in by_handle.items() if h.startswith(ref)
        }
        if len(set(named.values())) == 1:
            return next(iter(named.values()))
        if named:
            found = sorted(named)
            shown = ", ".join(found[:10]) + (" …" if len(found) > 10 else "")
            raise QueryError(f"{ref!r} names {len(found)} blocks: {shown}")
        raise Unnamed(
            f"{ref!r} names no block or group of state {self.state.id}: give a "
            "node id or its prefix, a handle or its prefix, or a block id"
        )

    def _not_here(self, ref: str, node: str) -> str:
        """Why `ref`, the handle `node` bore, names no block here: the node
        ended (how, at which state, and the nodes that took its text), or
        it begins after this state."""
        try:
            steps = versions.history(self.store, node, self.state)
        except VersionError:
            steps = ()
        if not steps or steps[-1].block is not None:
            return f"{ref!r} names {node}, which is not at state {self.state.id}"
        ended = steps[-1]
        heirs = ", ".join(f"{n} ({self.kept.kept.get(n, n)})" for n in ended.joined)
        opening = ", ".join(
            f"{self.node(i)} ({self.handle(i)})"
            for i, h in self.minted().items()
            if h == ref
        )
        return (
            f"{ref!r} names {node}, which ended at state {ended.state} "
            f"({ended.change})"
            + (f"; its text went to {heirs}" if heirs else "")
            + (f"; the words that gave it now open {opening}" if opening else "")
        )

    def block(self, ref: str) -> str:
        ident = self.resolve(ref)
        if ident not in self.blocks:
            raise QueryError(f"{ref!r} is a group, not a block")
        return ident

    def group(self, ref: str) -> str:
        ident = self.resolve(ref)
        if ident not in self.groups:
            raise QueryError(f"{ref!r} is a block, not a group")
        return ident

    # a block's facts

    def roles(self, ident: str) -> list[str]:
        block = self.blocks[ident]
        found = dict.fromkeys(s["role"] for s in block["segments"]) or dict.fromkeys(
            block["roles"]
        )
        return [r for r in found if r != "none"]

    def role_names(self, ident: str) -> str:
        if ident in self.groups:
            return "group"
        return "+".join(self.roles(ident)) or "none"

    def form(self, ident: str) -> str:
        """Its form and environment, and the standard environment a
        declared one stands for (`lem (lemma)`)."""
        block = self.blocks[ident]
        environment, meaning = block.get("environment"), block.get("meaning")
        if not environment:
            return block["form"]
        if meaning and meaning != environment:
            return f"{block['form']} {environment} ({meaning})"
        return f"{block['form']} {environment}"

    def standings_of(self, ident: str) -> list[dict[str, Any]]:
        return [s for s in self.standings if s["block"] == ident]

    def members(self, ident: str) -> tuple[str, ...]:
        """A group's blocks, or the block itself."""
        return tuple(self.groups[ident]["blocks"]) if ident in self.groups else (ident,)

    # lines

    @property
    def lines(self) -> edges_file.ExportLines:
        """The export's lines as the edges sheet writes them, by the
        export's handles."""
        if self._lines is None:
            self._lines = edges_file.ExportLines(self.export, self.handle)
        return self._lines

    def target(self, edge: Mapping[str, Any]) -> tuple[str, list[str]]:
        """An edge's target as the sheet names it, and its parts' labels."""
        return self.lines.target(edge)

    def claim_handles(self, ident: str) -> dict[str, str]:
        """Each claim's words on block `ident`, to its handle as the edges
        sheet names it (`edges_file.ExportLines.claim_handles`)."""
        return self.lines.claim_handles(ident)

    def edge_line(self, edge: Mapping[str, Any]) -> str:
        """An edge as the sheet holds it under its source."""
        return self.lines.edge_line(edge)

    def self_line(self, mark: Mapping[str, Any]) -> str:
        partial = f" {mark['partial']}" if mark.get("partial") else ""
        return f"→ self{partial}" + _quoted(mark) + _note(mark)

    def standing_line(self, standing: Mapping[str, Any]) -> str:
        """A standing as the edges sheet shows it."""
        return self.lines.standing_line(standing)

    def header(self, ident: str) -> str:
        """A block's one line: node id, handle, block id, form, roles, its
        section, groups and standings."""
        if ident in self.groups:
            g = self.groups[ident]
            first, last = g["blocks"][0], g["blocks"][-1]
            return (
                f"{self.shown(ident)}  {self.handle(ident)}  group {g['kind']}  "
                f"{len(g['blocks'])} blocks: {self.handle(first)} … {self.handle(last)}"
            )
        fields = [
            self.shown(ident),
            self.handle(ident),
            ident,
            self.form(ident),
            self.role_names(ident),
        ]
        if ident in self.sections:
            fields.append(self.sections[ident].name)
        if ident in self.groups_of:
            fields.append(
                "in " + ", ".join(self.handle(g) for g in self.groups_of[ident])
            )
        statuses = [s["status"] for s in self.standings_of(ident)]
        if statuses:
            fields.append("⊢ " + ", ".join(statuses))
        return "  ".join(fields)

    def header_data(self, ident: str) -> dict[str, Any]:
        if ident in self.groups:
            g = self.groups[ident]
            return {
                "node": self.node(ident),
                "handle": self.handle(ident),
                "group": ident,
                "kind": g["kind"],
                "blocks": [self.handle(b) for b in g["blocks"]],
            }
        section = self.sections.get(ident)
        return {
            "node": self.node(ident),
            "handle": self.handle(ident),
            "block": ident,
            "form": self.blocks[ident]["form"],
            "environment": self.blocks[ident].get("environment"),
            "meaning": self.blocks[ident].get("meaning"),
            "roles": self.roles(ident),
            "section": None
            if section is None
            else {"number": section.number, "title": section.title},
            "groups": [self.handle(g) for g in self.groups_of.get(ident, [])],
            "standings": [s["status"] for s in self.standings_of(ident)],
        }

    def item(self, ident: str) -> Item:
        return Item(self.header(ident), self.header_data(ident))


def _quoted(item: Mapping[str, Any]) -> str:
    evidence = item.get("evidence") or [{"text": ""}]
    return f' · "{evidence[0]["text"]}"'


def _note(item: Mapping[str, Any]) -> str:
    return f" — {item['note']}" if item.get("note") else ""


#: The kinds whose target rests on their source: a
#: definition's meaning on the assumption that qualifies it, a claim's
#: support on what justifies it. A `uses` or `about` edge's source rests
#: on its target.
RESTED_ON_BY_SOURCE = frozenset({"qualifies", "justifies"})


def _end(target: Mapping[str, Any]) -> str | None:
    """An edge's target as a block or group id; None for a file or the
    paper."""
    return target.get("block") or target.get("group")


def _onto(edge: Mapping[str, Any], ident: str) -> bool:
    t = edge["target"]
    return t.get("block") == ident or t.get("group") == ident


# --- the states ---------------------------------------------------------------


def states_data(store: RecordStore) -> dict[str, Any]:
    """Each kept state (its id, parent, command, version, time, node count
    and whether it is a head), oldest first, and the answers given on the
    latest's version that no state of it applied yet."""
    found = versions.states(store)
    heads = {s.id for s in versions.heads(found)}
    # no latest where joined copies kept states apart: nothing waits on it
    tips = versions.tips(store)
    pending = versions.pending_answers(store, tips[0].version) if len(tips) == 1 else ()
    return {
        "states": [
            {
                "id": s.id,
                "parent": s.parent,
                "command": s.command.value,
                "version": s.version,
                "at": s.at,
                "nodes": len(s.nodes),
                "head": s.id in heads,
            }
            for s in found
        ],
        "pending": [{"key": a.key, "stage": a.stage, "host": a.host} for a in pending],
    }


def history_data(store: RecordStore, ref: str, at: State) -> list[dict[str, Any]]:
    """A node's history up to `at`, `ref` its node id, a handle it bore,
    its block's id at `at`, or a finding or a judgment on it (`named`):
    each step with its text's runs and the handle its sheet showed there."""
    texts = versions.texts(store)
    names = versions.handles(store, at)
    bearers = names.aliases.get(ref, ())
    if ref in names.holder:
        node = names.holder[ref]  # an ended node's too
    elif len(bearers) == 1:
        node = bearers[0]
    elif ref in names.kept:
        node = ref
    else:
        view = View(store, at)
        found = named(view, ref)
        node = view.node(found) if isinstance(found, str) else filing_node(found)
    shown: dict[str, Mapping[str, str]] = {}

    def handle(step: versions.Step) -> str | None:
        if step.block is None:
            return None
        if step.state not in shown:
            export = json.loads(
                versions.kept_export(store, versions.state(store, step.state))
            )
            shown[step.state] = export.get("handles") or {}
        return shown[step.state].get(step.block)

    return [
        {
            **step.data(),
            "handle": handle(step),
            "runs": None if step.text is None else list(texts[step.text]),
        }
        for step in versions.history(store, node, at)
    ]


def pending_notes(store: RecordStore, at: State) -> list[str]:
    """What the state shown does not hold: the edits
    on its version, a person's or an agent's, no state applied yet."""
    pending = versions.pending_answers(store, at.version)
    if not pending:
        return []
    return [
        f"{len(pending)} edits on this version no state applied yet ("
        + ", ".join(f"{a.stage} {a.key[:12]} by {a.host}" for a in pending)
        + "): each waits on what it reopens, and its own `axiomation edit` "
        "resumes it; the state shown does not hold them"
    ]


# --- a block ------------------------------------------------------------------


def show(
    view: View,
    ref: str,
    kinds: Collection[str] = (),
    whole: bool = False,
    steps: Sequence[versions.Step] | None = None,
) -> Report:
    """A block (or group): its line and text, what it rests on (with its
    self marks) and what rests on it, each in the paper's order of the
    other end (`rests_on`), its standings, its open stale marks, its
    judgments, the findings and suggestions on it, and its history where
    `steps` are given; edges of `kinds` only where any are named."""
    ident = view.resolve(ref)
    report = Report(head=[view.header(ident)], data={"block": view.header_data(ident)})
    if ident in view.blocks:
        text = clipped(view.texts[ident], whole)
        report.head.append(text)
        report.data["text"] = text

    def kept(edge: Mapping[str, Any]) -> bool:
        return not kinds or edge["kind"] in kinds

    up: list[tuple[int, Item]] = []
    down: list[tuple[int, Item]] = []
    for e in view.edges:
        if not kept(e):
            continue
        if e["source"] == ident:
            other = _end(e["target"])
            item = Item(view.edge_line(e), e)
            (down if e["kind"] in RESTED_ON_BY_SOURCE else up).append(
                (view.order(other), item)
            )
        elif _onto(e, ident):
            item = Item(f"{view.handle(e['source'])} {view.edge_line(e)}", e)
            (up if e["kind"] in RESTED_ON_BY_SOURCE else down).append(
                (view.order(e["source"]), item)
            )
    if not kinds or "self" in kinds:
        up += [
            (view.order(ident), Item(view.self_line(m), m))
            for m in view.selfs
            if m["block"] == ident
        ]
    report.sections["rests_on"] = [i for _, i in sorted(up, key=lambda x: x[0])]
    report.sections["rested_on_by"] = [i for _, i in sorted(down, key=lambda x: x[0])]
    report.sections["standings"] = [
        standing_item(view, s) for s in view.standings_of(ident)
    ]
    node = view.node(ident)
    report.sections["marks"] = [
        Item(
            f"stale since {m.since}: {m.why}"
            + ("" if m.gate else "  (a description: no gate asks it)"),
            m.data(),
        )
        for m in view.marks
        if m.node == node
    ] + [
        Item(
            f"closed: {c.mark.edge or c.mark.why}, stale since {c.mark.since}, "
            f"read by {'reread' if c.reread else 'judgment'} {c.by}",
            {
                **c.mark.data(),
                "closed_by": c.by,
                "read_by": "reread" if c.reread else "judgment",
                "by_person": c.by_person,
            },
        )
        for c in view.cleared_marks
        if c.mark.node == node
    ]
    report.sections["judgments"] = [
        Item(status_line(view, s) + evidence_lines(s, whole), status_data(view, s))
        for s in view.statuses_of(ident)
    ]
    report.sections["established"] = [
        Item(established_line(view, e), established_data(view, e))
        for e in view.established_of(ident)
    ]
    stood = {norm(s["claim"]["text"]): s["status"] for s in view.standings_of(ident)}
    report.sections["rulings"] = [
        Item(ruling_line(r, ruled, moot), ruling_data(r, ruled, moot))
        for on, ruled in view.rulings.items()
        if on.node == view.node(ident)
        # a claim argued since it was ruled on: no ruling of it speaks
        for moot in [_moot(stood.get(norm(on.words or "")))]
        for r in ruled.reads
    ]
    report.sections["findings"] = []
    for f in view.readings.findings:
        if (
            ident not in map(view.readings.block_of, f.nodes)
            and view.node(ident) not in f.nodes
        ):
            continue
        data = finding_data(view, f)
        report.sections["findings"].append(Item(known_line(f, data), data))
    no_claim = no_claim_item(view, ident, len(report.sections["findings"]))
    if no_claim is not None:
        report.head.append(no_claim.line)
        report.data["no_claim"] = no_claim.data
    report.sections["suggestions"] = [
        Item(suggestion_line(view, s), suggestion_data(view, s))
        for s in view.suggestions
        if ident in map(view.readings.block_of, s.nodes) or view.node(ident) in s.nodes
    ]
    if steps is not None:
        report.sections["handles"] = handle_items(view, ident)
        report.sections["history"] = [Item(_step_line(s), s.data()) for s in steps]
    return report


#: A block's roles as the line on a block no review judges names them.
ROLE_NOUNS = {
    "definition": "a definition",
    "assumption": "an assumption",
    "motivation": "a motivation",
    "justification": "a justification",
}
#: The roles whose verdict is whether it is well-posed (docs/model.md § 2).
POSED_ROLES = frozenset({"definition"})
#: A review session's answers' kind, before the session's name
#: (`review-argument`): an unscoped session's template is named so too.
REVIEW_KIND = "review-"


def no_claim_item(view: View, ident: str, findings: int) -> Item | None:
    """What a review does with a block it judges nothing on (no standing
    and no subject of its own), so its card does not read as unreviewed:
    no claim to judge, a finding filed only on a problem (`findings` on it),
    and the latest review that read it (`last_read`); or, in a group a
    review judges, that the group's readings judge it (`judged_with`).
    None for a group, a block a review judges, and a claim with no
    standing."""
    if ident not in view.blocks or "claim" in (roles := view.roles(ident)):
        return None
    if view.standings_of(ident) or view.statuses_of(ident):
        return None
    what = " and ".join(ROLE_NOUNS.get(r, f"a {r}") for r in roles) or "no role"
    # a group is judged as one, its readings shown on its first block
    judged_with = next(
        (g for g in view.groups_of.get(ident, []) if view.statuses_of(g)), None
    )
    if judged_with is not None:
        group = view.handle(judged_with)
        line = (
            f"{what} in {group}, which a review judges as one: its readings are "
            "the group's"
        )
        data = {"roles": roles, "findings": findings, "judged_with": group}
        return Item(line, {**data, "last_read": None, "line": line})
    posed = bool(roles) and set(roles) <= POSED_ROLES
    when = "it is ill-posed" if posed else "it finds a problem in it"
    filed = f"{findings} filed, below" if findings else "none filed"
    line = (
        f"{what}: no claim to judge; a review files a finding on it only when "
        f"{when} ({filed})"
    )
    read = last_read(view, ident)
    if read is None:
        line += "\nlast read by a review: none; no review reading rests on it"
    else:
        line += f"\nlast read by a review: {read['words']}"
    return Item(
        line,
        {
            "roles": roles,
            "findings": findings,
            "judged_with": None,
            "last_read": read,
            "line": line,
        },
    )


def last_read(view: View, ident: str) -> dict[str, Any] | None:
    """The latest review that read a block: a review's readings resting on
    its node (their basis, the lines its sheet showed under their
    subjects), or a findings session that read the paper whole at a state
    holding it; with its time, its state, and whether the block's text
    changed since. None where neither did."""
    node = view.node(ident)
    text_at = {
        s.id: n.text
        for s in versions.chain(view.store, view.state)
        for n in s.nodes
        if n.node == node
    }
    answers = view.store.authoritative()
    found: dict[str, list[judgments.Read]] = {}
    for r in view.readings.reads:
        answer = answers.get(r.judgment.answer or "")
        if (
            answer is not None
            and answer.kind.startswith(REVIEW_KIND)
            and any(
                e.kind is judgments.BasisKind.NODE and e.name == node
                for e in r.judgment.basis
            )
        ):
            found.setdefault(answer.key, []).append(r)
    # the latest by time, then by the store's order (one second may hold two)
    order = {key: n for n, key in enumerate(answers)}
    reads = [
        ((answers[key].time, order[key]), key, resting[0].judgment.state, resting)
        for key, resting in found.items()
    ]
    # a findings session under its own template, neither scoped nor
    # lapsed, read every block
    whole = REVIEW_KIND + Session.FINDINGS.value
    reads += [
        ((a.time, order[key]), key, a.state, [])
        for key, a in answers.items()
        if a.kind == whole and a.template.name == whole and a.state in text_at
    ]
    if not reads:
        return None
    (time, _), key, state, rested = max(reads, key=lambda x: x[0])
    # a queue's step's answer is its session's (`review-citationcheck-reader`)
    found = session_of(answers[key].kind)
    session = found.value if found else answers[key].kind.removeprefix(REVIEW_KIND)
    changed = text_at.get(state) != text_at.get(view.state.id)
    if rested:
        how = (
            f"the {session} session, {len(rested)} of whose readings rest on it "
            f"({', '.join(r.judgment.id for r in rested[:3])}"
            + (", …" if len(rested) > 3 else "")
            + ")"
        )
    else:
        how = f"the {session} session, reading the paper whole"
    words = f"{time[:10]} at state {state}, {how}" + (
        "; its text changed since" if changed else ""
    )
    return {
        "answer": key,
        "session": session,
        "state": state,
        "time": time,
        "readings": [r.judgment.id for r in rested],
        "changed_since": changed,
        "words": words,
    }


def known_line(f: judgments.FindingRead, data: Mapping[str, Any], on: str = "") -> str:
    """A finding's line under the id the author knew, its first filing's,
    the filing it became beside it."""
    line = finding_line(f, on)
    if data["first"] == f.finding.id:
        return line
    return line.replace(
        f"finding {f.finding.id}",
        f"finding {data['first']} (refiled as {f.finding.id})",
        1,
    )


def handle_items(view: View, ident: str) -> list[Item]:
    """A block's names beside its kept handle (`docs/model.md` § 7): each
    alias that resolves to it, the one its words give now among them, and
    each name it bore that another node holds as its kept handle."""
    node = view.node(ident)
    aliases = [a for a in view.kept.aliases_of(node) if len(view.kept.aliases[a]) == 1]
    now = view.minted().get(ident)
    if now is not None and now not in view.kept.holder and now not in aliases:
        aliases.append(now)
    found = [
        Item(
            f"alias {a}" + ("  (its words now)" if a == now else ""),
            {"alias": a, "now": a == now},
        )
        for a in aliases
    ]
    found += [
        Item(
            f"dropped {d}: the handle of {view.kept.holder[d]}",
            {"dropped": d, "holder": view.kept.holder[d]},
        )
        for d in view.kept.dropped.get(node, ())
    ]
    return found


def _step_line(step: versions.Step) -> str:
    line = f"{step.state}  {step.command.value}  {step.change}  {step.block or '-'}"
    if step.joined:
        line += "  with " + ", ".join(step.joined)
    if step.causes:
        line += "  " + "; ".join(
            ", ".join(
                f"{k}: {_cause_words(v)}" for k, v in cause.items() if v is not None
            )
            for cause in step.causes
        )
    if step.edges:
        line += "  edges: " + "; ".join(_edge_words(e) for e in step.edges)
    return line


def _edge_words(edge: versions.EdgeChange) -> str:
    """An edge a step began (+), ended (−), changed (~) or carried across a
    split or a merge (→ to, ← from), from its source's side, its ends their
    nodes: `+ n1 uses n2 (n2 [K])`, `~ n1 about n2 (was: none)`, `− self
    n1`, `→ n1 uses n2 to n3`; by a person noted."""
    sign = {
        versions.EdgeStep.BEGAN: "+",
        versions.EdgeStep.ENDED: "−",
        versions.EdgeStep.CHANGED: "~",
        versions.EdgeStep.CARRIED_TO: "→",
        versions.EdgeStep.CARRIED_FROM: "←",
    }[edge.step]
    said = (
        f"self {edge.source}"
        if edge.kind == versions.SELF_MARK
        else f"{edge.source} {edge.kind} {edge.target or ''}".rstrip()
    )
    if edge.parts:
        said += " (" + ", ".join(edge.parts) + ")"
    if edge.step is versions.EdgeStep.CHANGED:
        said += " (was: " + (", ".join(edge.was) or "none") + ")"
    if edge.piece is not None:
        said += (
            " to " if edge.step is versions.EdgeStep.CARRIED_TO else " from "
        ) + edge.piece
    return f"{sign} {said}" + (f" by {edge.by}" if edge.by == "person" else "")


def _cause_words(value: Any) -> str:
    """A cause's value in words: a rule's or a mark's causes (each its
    why and the edge it came by) as sentences, never a list's repr."""
    if isinstance(value, list):
        return "; ".join(_cause_words(v) for v in value)
    if isinstance(value, dict) and "why" in value:
        via = value.get("via")
        return str(value["why"]) + (f" (via {via})" if via else "")
    if isinstance(value, dict):
        return ", ".join(
            f"{k} {_cause_words(v)}" for k, v in value.items() if v is not None
        )
    return str(value)


# --- a finding or a judgment by its id -----------------------------------


#: A finding or a judgment as read at a state.
Filing = judgments.FindingRead | judgments.Read


def named(view: View, ref: str) -> str | Filing:
    """What `show` and `history` take `ref` to name: a block or group
    (`View.resolve`); else a finding or a judgment by its id or a unique
    prefix of it, as the commands that file on one take it, a refiled
    finding read as the latest filing this state holds."""
    try:
        return view.resolve(ref)
    except Unnamed as error:
        unnamed = error
    held = view.store.judgment_records()
    filed: list[Any] = [*held.findings, *held.judgments]
    found = [r for r in filed if r.id == ref] or [
        r for r in filed if r.id.startswith(ref)
    ]
    if not found:
        raise QueryError(f"{unnamed}, or a finding's or a judgment's id") from None
    if len(found) > 1:
        shown = ", ".join(
            f"{'finding' if isinstance(r, Finding) else 'judgment'} {r.id}"
            for r in found[:10]
        ) + (" …" if len(found) > 10 else "")
        raise QueryError(f"{ref!r} names {len(found)} filings: {shown}")
    (record,) = found
    if isinstance(record, Finding):
        by = {f.finding.id: f for f in view.readings.findings}
        read: Filing | None = by.get(record.id)
        while read is not None and read.reaffirmed in by:
            read = by[read.reaffirmed]
    else:
        reads = (*view.readings.reads, *view.readings.proposals)
        read = next((r for r in reads if r.judgment.id == record.id), None)
    if read is None:
        kind = "finding" if isinstance(record, Finding) else "judgment"
        raise QueryError(
            f"{kind} {record.id} was filed on state {record.state}, which is not "
            f"along state {view.state.id}'s parents"
        )
    return read


def filing_node(found: Filing) -> str:
    """The node a finding or a judgment is on, for its history: a
    finding's first anchor on a node, a judgment's subject; where it was
    deleted, the node as filed, whose history says where it went."""
    if isinstance(found, judgments.Read):
        return found.subject.node
    nodes = [*found.nodes, *judgments.anchor_nodes(found.finding.anchors)]
    if not nodes:
        raise QueryError(
            f"finding {found.finding.id} is on no block (a work, a file or the "
            "paper): it has no history of its own"
        )
    return nodes[0]


def filing_report(
    view: View, found: Filing, whole: bool = False, history: bool = False
) -> Report:
    """`show` of a finding or a judgment by its id; with `history`, the
    history of the node it is on, where it is on one."""
    if isinstance(found, judgments.Read):
        report = judgment_report(view, found, whole)
    else:
        report = finding_report(view, found)
    if history:
        try:
            node = filing_node(found)
        except QueryError as error:
            report.notes.append(str(error))
        else:
            steps = versions.history(view.store, node, view.state)
            report.sections["history"] = [Item(_step_line(s), s.data()) for s in steps]
    return report


def finding_report(view: View, f: judgments.FindingRead) -> Report:
    """A finding whole: its line under the id the author knew, the block
    it shows at (as the open view places it) and what clears it in its
    head; each anchor, each cause that lapsed it, every answer filed on it
    or on a filing it was (the one that speaks marked, a proposal said so,
    and whether it is settled, a withdrawn one so, with why), and each
    filing it was, the first first."""
    data = finding_data(view, f)
    _, block = _placed(view, f, data)
    report = Report(
        head=[known_line(f, data, anchor_words(view, f))]
        + _at_block(view, block)
        + ([f"clears: {data['clears']}"] if data["clears"] else []),
        data={
            "finding": data,
            "block": None if block is None else view.header_data(block),
        },
    )
    on_nodes = iter(f.walked.nodes)
    nowhere = cited_nowhere(view)
    anchors = []
    for a, filed in zip(data["anchors"], f.finding.anchors, strict=True):
        node = None if filed.node is None else next(on_nodes, None)
        at = None if node is None else view.readings.block_of(node)
        where = (
            [a["kind"]]
            + ([] if node is None else [view.readings.at.handle(node)])
            + ([] if a["work"] is None else [work_words(a["work"], nowhere)])
            + ([] if a["file"] is None else [f"file:{a['file']}"])
            + ([f'"{a["words"]}"'] if a["words"] else [])
        )
        if filed.node is not None and at is None:
            where.append(f"(on {filed.node}, not at state {view.state.id})")
        anchors.append(Item(" ".join(where), {**a, "block": at}))
    report.sections["anchors"] = anchors
    report.sections["causes"] = cause_items(f.walked)
    records = view.store.judgment_records().findings
    chain = [f.finding]
    by = {r.id: r for r in records}
    while chain[0].reaffirms in by and by[chain[0].reaffirms] not in chain:
        chain.insert(0, by[chain[0].reaffirms])
    authors = view.store.authoritative()
    answered = judgments.answers_of(view.store, authors=authors, withdrawn=True)
    withdrawn = judgments.answer_withdrawals(view.store)
    spoken = None if f.answer is None else f.answer.id

    def settled(a: FindingAnswer, author: judgments.Author) -> str | None:
        """What became of an answer that is a proposal (a person's filed
        as one, or an agent's by hand); None where it is none."""
        if not (a.proposed or author.proposes):
            return None
        if f.proposal is not None and a.id == f.proposal.answer.id:
            return "proposed, not settled"
        if f.turned_down is not None and a.id == f.turned_down.answer.id:
            return "proposed, not taken: refiled"
        return "proposed"

    report.sections["answers"] = [
        Item(
            f"{a.id}  {a.status.value}"
            + (f" of {a.of}" if a.of else "")
            + f"  by {author.name}"
            + ("" if record.id == f.finding.id else f"  on {record.id}")
            + (f': "{a.note}"' if a.note else "")
            + ("  (speaks)" if a.id == spoken else "")
            + (f"  ({proposal})" if proposal else "")
            + (
                f'  (withdrawn: "{withdrawn[a.id].reason}")'
                if a.id in withdrawn
                else ""
            ),
            {
                "id": a.id,
                "finding": record.id,
                "status": a.status.value,
                "of": a.of,
                "note": a.note,
                "author": author.name,
                "proposed": proposal is not None,
                "settled": proposal,
                "speaks": a.id == spoken,
                "withdrawn": None
                if a.id not in withdrawn
                else {"id": withdrawn[a.id].id, "reason": withdrawn[a.id].reason},
            },
        )
        for record in chain
        for _, author, a in answered.get(record.id, [])
        for proposal in [settled(a, author)]
    ]
    report.sections["filings"] = [
        Item(
            f"{r.id}  at state {r.state}  by "
            + judgments.author_of(authors, r.answer).name
            + (f'  "{r.problem}"' if r.reaffirms is None else f'  "{r.evidence}"')
            + (f"  reaffirms {r.reaffirms}" if r.reaffirms else ""),
            {
                "id": r.id,
                "state": r.state,
                "author": judgments.author_of(authors, r.answer).name,
                "reaffirms": r.reaffirms,
                "problem": r.problem,
                "evidence": r.evidence,
            },
        )
        for r in chain[:-1]
    ]
    return report


def judgment_report(view: View, read: judgments.Read, whole: bool = False) -> Report:
    """A judgment whole: its line (subject, verdict, author, the state it
    was made on), its evidence, live or lapsed and the block its subject
    is on in its head; each cause that lapsed it, and its subject's status
    here, the readings beside it."""
    j = read.judgment
    on = read.subject
    block = view.readings.block_of(on.node)
    report = Report(
        head=_judgment_head(view, read) + _at_block(view, block),
        data={
            "judgment": {
                "aspect": j.aspect.value,
                "proposed": j.proposed,
                **read_data(read),
            },
            "block": None if block is None else view.header_data(block),
        },
    )
    report.sections["causes"] = cause_items(read.walked)
    status = next(
        (
            s
            for s in view.readings.statuses
            if any(r.judgment.id == j.id for r in s.reads)
            or (s.proposal is not None and s.proposal.read.judgment.id == j.id)
        ),
        None,
    )
    report.sections["status"] = (
        []
        if status is None
        else [
            Item(
                status_line(view, status) + evidence_lines(status, whole),
                status_data(view, status),
            )
        ]
    )
    return report


def _at_block(view: View, block: str | None) -> list[str]:
    """The head's line naming the block a filing is on, where it is on one."""
    return [] if block is None else [f"at {view.header(block)}"]


def _judgment_head(view: View, read: judgments.Read) -> list[str]:
    """A judgment's head lines: what it judged, by whom and where, its
    evidence, and whether it lapsed."""
    j = read.judgment
    on = read.subject
    nodes = [n for n in (on.node, on.target) if n is not None]
    return [
        f"judgment {j.id}: {j.aspect.value} on "
        + " → ".join(view.handle(view.readings.at.ident_of.get(n, n)) for n in nodes)
        + (f' · "{on.words}"' if on.words else "")
        + f", {_by_author(view, read)}, judged at state {j.state}"
        + (", proposed" if j.proposed else ""),
        f"evidence: {j.evidence}",
        "lapsed" if not read.live else f"live at state {view.state.id}",
    ]


def cause_items(walked: judgments.Walked) -> list[Item]:
    """Each cause that lapsed a judgment or a finding: its state, kind and
    why, its Δ lines with their notes, and what moved."""
    return [
        Item(
            f"{c.state}  {c.kind.value}  {c.why}"
            + "".join(
                f"\n  Δ {d['class']}"
                + (f" [{d['entry']}]" if d.get("entry") else "")
                + (f" — {d['note']}" if d.get("note") else "")
                for d in c.data.get("deltas", [])
            )
            + "".join(f"\n  {why.get('why', why)}" for why in c.data.get("causes", [])),
            {"kind": c.kind.value, "parent": c.parent, "state": c.state, "why": c.why},
        )
        for c in walked.causes
    ]


# --- judgments -----------------------------------------------------------


def _by_author(view: View, r: judgments.Read) -> str:
    j = r.judgment
    line = f"{j.verdict.value} ({j.depth.value}) by {r.author.name}, {j.id}"
    if j.pages:
        line += ", pages " + " ".join(p.name for p in j.pages)
        line += ", provisional" if r.provisional else ""
        at = view.readings.at
        nowhere = cited_nowhere(view)
        line += "".join(
            f", {w} " + (CITED_NOWHERE if nowhere(w) else "not cited at this block")
            for w in judgments.uncited_works(at, j.aspect, r.subject, j.pages)
        )
    if j.unread:
        line += ", unread " + ", ".join(
            f"{w.key} {w.read_status.value}" for w in j.unread
        )
    if j.run is not None:
        line += f", run {j.run.name} {j.run.status.value}"
        line += "".join(f' · "{v.text}" ↔ "{v.output}"' for v in j.values)
        line += "".join(f" · code `{c}`" for c in j.code)
    if j.reaffirms:
        was = next(
            (x.judgment for x in view.readings.reads if x.judgment.id == j.reaffirms),
            None,
        )
        line += f" reaffirms {j.reaffirms}"
        line += "" if was is None else f" at {was.state}"
    return line


def _subject(view: View, s: judgments.SubjectStatus) -> str:
    on = s.subject
    head = s.aspect.value
    if on.target is not None:
        head += f" {view.handle(view.readings.at.ident_of.get(on.node, on.node))} →"
        head += f" {view.handle(view.readings.at.ident_of.get(on.target, on.target))}"
    if on.work is not None:
        head += f" {on.work}"
    if on.file is not None:
        head += f" file:{on.file}"
    if on.words:
        head += f' · "{on.words}"'
    return head


#: What a reading or a ruling a review filed under an older question than
#: its brief asks now says of itself; the next review asks it again where
#: it still bears (docs/model.md § 8).
OLDER_QUESTION = "filed under an older question"


def status_line(view: View, s: judgments.SubjectStatus) -> str:
    """A subject's status per aspect as a line: unjudged, held (verdict,
    depth, author, judgment), disputed (each live reading), or lapsed (its
    first cause, and how to re-ask)."""
    head = _subject(view, s)
    if s.status is judgments.StatusKind.HELD:
        assert s.held is not None
        line = f"{head}  held: {_by_author(view, s.held)}"
    elif s.status is judgments.StatusKind.DISPUTED:
        live = [_by_author(view, r) for r in s.reads if r.live]
        live += [f"finding {f.finding.id} (severity 3)" for f in s.findings]
        line = f"{head}  disputed: " + "; ".join(live)
    elif s.status is judgments.StatusKind.LAPSED:
        cause = s.cause
        assert cause is not None
        latest = s.reads[-1].judgment.id
        line = (
            f"{head}  lapsed at {cause.state}: {cause.kind.value}, {cause.why} "
            f"(`axiomation reask` {latest})"
        )
    else:
        line = f"{head}  unjudged"
    if s.outdated:
        line += f"  ({OLDER_QUESTION})"
    if s.proposal is not None:
        line += f"  {proposed_words(s.proposal)}"
    return line


def proposed_words(p: judgments.ProposedReading) -> str:
    """A person's proposed reading as the reads show it: its verdict, who
    proposed it and why; where a reading settled it since, that reading,
    else that the next review of its kind asks it."""
    j = p.read.judgment
    line = f"proposed {j.verdict.value} by {p.read.author.name}, {j.id}: {j.evidence}"
    if p.reask is None:
        return line + (
            " (asked by the next review of its kind)" if p.read.live else " (lapsed)"
        )
    r = p.reask.judgment
    said = "taken" if r.verdict is j.verdict else "not taken"
    return line + (
        f" — weighed by {p.reask.author.name}, {r.id}: {r.verdict.value}, {said}"
    )


def reading_words(s: judgments.SubjectStatus | None) -> str:
    """A subject's reading in a word or two, as a claim's line ends with
    its `true`'s: `held inconclusive`, `unjudged`, `lapsed`, `disputed`."""
    if s is None or s.status is judgments.StatusKind.UNJUDGED:
        return "unjudged"
    if s.status is judgments.StatusKind.HELD:
        assert s.held is not None
        return f"held {s.held.judgment.verdict.value}"
    return s.status.value


def unsupported_line(
    view: View,
    standing: Mapping[str, Any],
    e: Established,
    support: rulings.ClaimSupport,
) -> tuple[str, str]:
    """An unsupported claim's line, as `open` and `unsupported-claims` list
    it: its standing and words, the gap where its standing is not
    `unsupported` (a `justified` claim no justifies edge reaches), what a
    ruling did not clear, the rulings that speak, then its `true`'s reading
    beside them; and that reading."""
    b = standing["block"]
    words = standing["claim"]["text"]
    on = Subject(view.node(b), None, words)
    true = next(
        (
            st
            for st in view.readings.statuses
            if st.aspect is Aspect.TRUE and st.subject == on
        ),
        None,
    )
    reading = reading_words(true)
    ruled = support.ruled
    gap = (
        f"  ({e.reason.why})"
        if e.reason is not None
        and e.reason.node == e.claim.node
        and e.claim.status is not Status.UNSUPPORTED
        and e.reason.kind is Missing.UNSUPPORTED
        else ""
    )
    return (
        f"{view.handle(b)}  ⊢ {standing_status(standing)} · "
        f"{part_quote(view.texts.get(b, ''), words)}{_note(standing)}{gap}"
        + support_note(view, support)
        + f"  {'unruled' if ruled is None else ruled_words(ruled)}"
        + relied_note(view, support)
        + f"  · `true` {reading}",
        reading,
    )


def relied_note(view: View, support: rulings.ClaimSupport) -> str:
    """Beside an owed ruling's severity, the graph's part of its weight:
    what relies on the claim in one hop."""
    if support.owed is None or not support.relied:
        return ""
    at = view.readings.at
    return "  · relied on by " + ", ".join(at.handle(n) for n in support.relied)


def evidence_lines(s: judgments.SubjectStatus, whole: bool = False) -> str:
    """The evidence of a subject's readings that speak (held, disputed;
    a lapsed one's latest), a line each under the status line, cut as a
    block's text is unless `whole`."""
    if s.status is judgments.StatusKind.HELD:
        reads = [] if s.held is None else [s.held]
    elif s.status is judgments.StatusKind.LAPSED:
        reads = s.reads[-1:]
    else:
        reads = [r for r in s.reads if r.live]
    return "".join(
        f"\n  evidence {r.judgment.id}: {clipped(r.judgment.evidence, whole)}"
        for r in reads
        if r.judgment.evidence
    )


def stamp_data(p: PageStamp) -> dict[str, Any]:
    """A page read, as the reads give it: its name, its work's
    key and page, and how it was read."""
    return {
        "name": p.name,
        "key": p.key,
        "page_index": p.page_index,
        "read_status": p.read_status.value,
    }


def cause_data(cause: judgments.Cause | None) -> dict[str, Any] | None:
    """A lapse's cause as the reads give it: its kind, the state it is
    named at, why, and the element that changed (a node's or an edge's
    source's node id, a file's path, a page's or a run's name)."""
    if cause is None:
        return None
    return {
        "kind": cause.kind.value,
        "state": cause.state,
        "why": cause.why,
        "element": {"kind": cause.element.kind.value, "name": cause.element.name},
    }


def unresolved_line(
    view: View, s: judgments.SubjectStatus, read: judgments.Read
) -> str:
    """A lapsed subject whose readings stood on a negative one, as `check`
    lists it: the reading, its first cause, and how to re-ask."""
    cause = judgments.first(read.walked.causes)
    assert cause is not None
    return (
        f"{_subject(view, s)}  lapsed, unresolved: {_by_author(view, read)}; "
        f"lapsed at {cause.state}: {cause.kind.value}, {cause.why} "
        f"(`axiomation reask` {read.judgment.id})"
    )


def status_data(view: View, s: judgments.SubjectStatus) -> dict[str, Any]:
    at = view.readings.at
    cause = s.cause if s.status is judgments.StatusKind.LAPSED else None
    return {
        "aspect": s.aspect.value,
        "subject": {
            "node": s.subject.node,
            "handle": view.handle(at.ident_of.get(s.subject.node, s.subject.node)),
            "target": s.subject.target,
            "target_handle": None
            if s.subject.target is None
            else view.handle(at.ident_of.get(s.subject.target, s.subject.target)),
            "words": s.subject.words,
            "work": s.subject.work,
            "file": s.subject.file,
        },
        "status": s.status.value,
        "outdated": s.outdated,
        "held": None if s.held is None else s.held.judgment.id,
        "judgments": [read_data(r) for r in s.reads],
        "findings": [f.finding.id for f in s.findings],
        "cause": cause_data(cause),
        "clears": status_clears(s),
        "proposal": None if s.proposal is None else proposal_data(s.proposal),
    }


def read_data(r: judgments.Read) -> dict[str, Any]:
    j = r.judgment
    return {
        "id": j.id,
        "author": r.author.name,
        "verdict": j.verdict.value,
        "depth": j.depth.value,
        "state": j.state,
        "lapsed": not r.live,
        "reaffirms": j.reaffirms,
        "evidence": j.evidence,
        "pages": [stamp_data(p) for p in j.pages],
        "provisional": r.provisional,
        "run": None
        if j.run is None
        else {
            "file": j.run.key,
            "arguments": list(j.run.arguments),
            "runner": j.run.runner,
            "status": j.run.status.value,
            "output": j.run.output,
        },
        "code": list(j.code),
        "unread": [
            {"key": w.key, "read_status": w.read_status.value} for w in j.unread
        ],
        "values": [{"text": v.text, "output": v.output} for v in j.values],
    }


def proposal_data(p: judgments.ProposedReading) -> dict[str, Any]:
    j = p.read.judgment
    return {
        "id": j.id,
        "author": p.read.author.name,
        "verdict": j.verdict.value,
        "evidence": j.evidence,
        "lapsed": not p.read.live,
        "reask": None
        if p.reask is None
        else {
            "id": p.reask.judgment.id,
            "author": p.reask.author.name,
            "verdict": p.reask.judgment.verdict.value,
            "evidence": p.reask.judgment.evidence,
        },
    }


def standing_status(standing: Mapping[str, Any]) -> str:
    """A standing's status as the sheet writes it, with the paper's
    declaration on an `unsupported` one: `unsupported omitted`."""
    declared = standing.get("declared")
    return standing["status"] + (f" {declared}" if declared else "")


def own_gap(e: Established) -> Missing | None:
    """A claim's own gap, where its reason sits on itself: no support (a
    justified claim no justifies edge reaches, a restatement restating
    nothing, an unsupported claim) or a circle."""
    if e.reason is None or e.reason.node != e.claim.node:
        return None
    if e.reason.kind in (Missing.UNSUPPORTED, Missing.CIRCULAR):
        return e.reason.kind
    return None


#: What a candidate end on a circle's line names less than, in words.
COARSE_WORDS = {
    Coarse.WHOLE: "names no part of a block holding definition or assumption content",
    Coarse.UNNAMED_TARGET: "names no claim of a block of several",
    Coarse.UNNAMED_SOURCE: "names none of its block's claims at its source end",
    Coarse.SIBLING: "restates a restated claim",
}


@dataclass(frozen=True)
class ReadReal:
    """A circle read real: the record, its author's name, and whether a
    person speaks."""

    record: RealCircle
    author: str
    by_person: bool


def read_real(store: RecordStore) -> dict[str, ReadReal]:
    """Each circle's key to the latest word, a person's or an agent's,
    that it is real. Keyed on the circle alone: a circle whose lines' ends
    or claims' standings change is another circle, asked again."""
    found = store.judgment_records().circles
    if not found:
        return {}
    answers = store.authoritative()
    read: dict[str, ReadReal] = {}
    for r in found:
        answer = answers.get(r.answer or "")
        host = PERSON if answer is None else answer.host
        model = PERSON if answer is None else answer.model
        read[r.circle] = ReadReal(r, judgments.Author(host, model).name, host == PERSON)
    return read


def circle_text(view: View, circle: Circle) -> str:
    """A circle as `open` and `check` show it: its claims, then each line
    as the sheet holds it under its source, with its candidate ends."""
    at = view.readings.at
    inside = len(circle.claims)
    resting = len(circle.held_back) - sum(c in circle.held_back for c in circle.claims)
    head = (
        f"circle {circle.key[:8]}: {inside} claims in it, {resting} resting on it"
        + ("" if circle.coarse else "; every end named")
    )
    lines = [head]
    for c in circle.claims:
        block = view.readings.block_of(c.node)
        held = view.claim_handles(block) if block is not None else {}
        named = f" [{held[c.words]}]" if len(held) > 1 and c.words in held else ""
        lines.append(f'  {at.handle(c.node)}{named} "{c.words}" {c.status.value}')
    for line in circle.lines:
        edge = line.step.edge
        assert edge is not None
        said = f"  {view.handle(edge['source'])}  {view.edge_line(edge)}"
        if line.step.source != line.claim.node:
            said += f"  (for {at.handle(line.claim.node)})"
        if line.via:
            said += f"  (on its way through {', '.join(map(at.handle, line.via))})"
        if line.coarse:
            said += "  — " + "; ".join(COARSE_WORDS[c] for c in line.coarse)
        lines.append(said)
    real = view.read_real.get(circle.key)
    if real is not None:
        r = real.record
        lines.append(
            f"  read real by {real.author} at {r.state[:8]}: {r.reason}; proposal: "
            f"{r.proposal}"
        )
    return "\n".join(lines)


def circle_data(view: View, circle: Circle) -> dict[str, Any]:
    """A circle's item cause: its key, claims, lines and what it holds back."""
    at = view.readings.at

    def claim(c: Any) -> dict[str, Any]:
        return {
            "node": c.node,
            "handle": at.handle(c.node),
            "words": c.words,
            "standing": c.status.value,
        }

    return {
        "circle": circle.key,
        "claims": [claim(c) for c in circle.claims],
        "lines": [
            {
                "claim": at.handle(line.claim.node),
                "asks": at.handle(line.asks.node),
                "owner": view.handle(line.step.edge["source"]),
                "line": view.edge_line(line.step.edge),
                "via": [at.handle(v) for v in line.via],
                "coarse": [c.value for c in line.coarse],
            }
            for line in circle.lines
            if line.step.edge is not None
        ],
        "held_back": [claim(c) for c in circle.held_back],
        "real": None
        if (real := view.read_real.get(circle.key)) is None
        else {
            "state": real.record.state,
            "by": real.author,
            "by_person": real.by_person,
            "reason": real.record.reason,
            "proposal": real.record.proposal,
        },
    }


def circle_anchors(view: View, circle: Circle) -> list[dict[str, Any]]:
    return [_anchor("part", c.node, c.words) for c in circle.claims]


def support_note(view: View, support: rulings.ClaimSupport) -> str:
    """What a line adds for a claim's support where a ruling did not clear
    it as it says: the guard that stopped it, in words; for a restatement
    of a conjecture, the conjecture; else nothing."""
    if support.conjectures:
        return (
            f"  (restates {', '.join(support.conjectures)}, which the paper only "
            "conjectures: restating it asserts it"
            + (
                "; no about edge names the claim it restates there "
                "(`→ about T [<claim>]`)"
                if support.by_fallback
                else ""
            )
            + ")"
        )
    kind = None if support.ruling is None else support.ruling.value
    match support.guard:
        case rulings.Guard.PROMISED:
            return "  (ruled, but the paper promises its argument)"
        case rulings.Guard.TRUE_DISPUTED:
            return f"  (ruled {kind} by agents, but its `true` is disputed)"
        case rulings.Guard.TRUE_NOT_HELD:
            return f"  (ruled {kind} by agents, but its `true` is not held verified)"
        case rulings.Guard.NO_RESULT_USED:
            return "  (ruled immediate, but its block uses no result)"
        case rulings.Guard.RELIED_ON:
            shown = ", ".join(view.readings.at.handle(n) for n in support.relied)
            ruled = {
                RulingKind.LEFT_UNARGUED: "left unargued",
                RulingKind.POSITIONING: "ruled positioning",
            }.get(support.ruling, "ruled an interpretation")
            return f"  ({ruled}, yet {shown} rely on it)"
    return ""


def speaks_data(ruled: rulings.Ruled) -> list[dict[str, Any]]:
    """The rulings that speak for a claim, as the reads give them: each
    ruling, its author, a person's or not, its reason and its id."""
    return [
        {
            "ruling": r.ruling.kind.value,
            "author": r.author.name,
            "reason": r.ruling.reason,
            "severity": r.ruling.severity,
            "fix": r.ruling.fix,
            "id": r.ruling.id,
            "person": r.author.person,
            "outdated": r.outdated,
            "pages": [stamp_data(p) for p in r.ruling.pages],
        }
        for r in ruled.speaks
    ]


def proposed_ruling_data(ruled: rulings.Ruled | None) -> dict[str, Any] | None:
    """A person's proposed ruling the next review settles, as JSON carries
    it; None where none asks."""
    if ruled is None or ruled.proposal is None or not ruled.proposal.asks:
        return None
    r = ruled.proposal.read
    return {
        "ruling": r.ruling.kind.value,
        "author": r.author.name,
        "reason": r.ruling.reason,
        "severity": r.ruling.severity,
        "fix": r.ruling.fix,
        "id": r.ruling.id,
        "pages": [stamp_data(p) for p in r.ruling.pages],
    }


def support_data(support: rulings.ClaimSupport | None) -> dict[str, Any] | None:
    """A claim's support as its JSON carries it: the status, its gap, the
    ruling it was cleared as or would be, who spoke, the guard."""
    if support is None:
        return None
    return {
        "status": support.status.value,
        "gap": None if support.gap is None else support.gap.value,
        "ruling": None if support.ruling is None else support.ruling.value,
        "by_person": support.by_person,
        "speaks": [] if support.ruled is None else speaks_data(support.ruled),
        "guard": None if support.guard is None else support.guard.value,
        "conjectures": list(support.conjectures),
        "severity": support.weight[0] or None,
        "relied": list(support.relied) if support.owed is not None else [],
        "proposal": proposed_ruling_data(support.ruled),
    }


def standing_item(view: View, s: Mapping[str, Any]) -> Item:
    """A standing as `show` lists it: its line, ending in its completeness
    where it is not `given`, which its JSON carries; under it the claim's
    status (`claim_status`) and its next step."""
    covered = view.completeness_of(s)
    line = view.standing_line(s)
    if covered is not None and covered.completeness is not Completeness.GIVEN:
        line += f" ({completeness_words(covered)})"
    folded = view.claim_status_of(s)
    line += f"\n  status {folded.status.value} · {folded.detail}"
    if folded.next:
        line += f"\n  next: {folded.next}"
    return Item(
        line,
        {
            **s,
            "completeness": None if covered is None else covered.completeness.value,
            "unnamed": covered is not None and covered.unnamed,
            "support": support_data(view.support_of(s)),
            "claim_status": folded.data()
            | ({} if folded.at is None else {"at": view.readings.at.handle(folded.at)}),
        },
    )


def completeness_words(covered: Covered) -> str:
    """A claim's completeness as a line ends in it: the word, and, where a
    fuller line covers the claim but names no claim of its block, a note
    saying so."""
    word = covered.completeness.value
    return (
        f"{word}; a fuller line names no claim of its block"
        if covered.unnamed
        else word
    )


#: How much of its sentence a part's quote carries on each side of its words.
CONTEXT_CHARS = 80
#: Markup a sentence may open with that is no subject: an environment's
#: begin and its option, an item, a label, a footnote's opening, an
#: elided run.
_OPENING_MARKUP = re.compile(
    r"^(?:\s*(?:\\begin\{[^}]*\}(?:\[[^\]]*\])?|\\item\b|\\label\{[^}]*\}"
    r"|\\footnote\{|⟦…⟧))+"
)
#: A citation command, which says nothing of a sentence's subject or rest.
_CITATION = re.compile(r"\\[a-zA-Z]*cite[a-zA-Z]*\*?(?:\[[^\]]*\])*\{[^}]*\}")


def part_quote(text: str, words: str) -> str:
    """A claim's words quoted as a line shows them, inside their sentence
    of their block's text: the sentence's opening before them in brackets
    where they begin inside it, so the line keeps its subject ("[These
    traditions provided …,] but no single …"), and its rest after them
    where they end inside it; else the words alone."""
    # its words as the block holds them, any run of whitespace between
    found = re.search(r"\s+".join(map(re.escape, words.split())), text)
    if found is None:
        return f'"{words}"'
    at, end = found.span()
    opening = _OPENING_MARKUP.sub("", text[_sentence_start(text, at) : at]).strip()
    tail = sentence_at(text, end - 1) if end > at else None
    rest = "" if tail is None else text[end : tail[1]].strip()
    shown = words
    if any(ch.isalpha() for ch in opening):
        if len(opening) > CONTEXT_CHARS:
            opening = opening[: CONTEXT_CHARS - 1].rstrip() + "…"
        shown = f"[{opening}] {shown}"
    if any(ch.isalpha() for ch in _CITATION.sub("", rest)):
        if len(rest) > CONTEXT_CHARS:
            rest = rest[: CONTEXT_CHARS - 1].rstrip() + "…"
        shown = f"{shown} [{rest}]"
    return f'"{shown}"'


def _sentence_start(text: str, at: int) -> int:
    """Where the sentence holding offset `at` opens; a sentence that opens
    in lower case was cut at an abbreviation the reader does not know
    ("a.e."), so it opens where the one before it does."""
    bounds = sentence_at(text, at)
    if bounds is None:
        return at
    start = bounds[0]
    while start > 0 and text[start:at].lstrip()[:1].islower():
        before = sentence_at(text, start - 1)
        if before is None or before[0] >= start:
            break
        start = before[0]
    return start


def established_line(view: View, e: Established) -> str:
    """A claim's `established` as a line: by its route, under the
    assumptions it reaches and the conjectures its statement takes as
    hypotheses, or the first missing element; its words with their
    sentence's opening where they begin inside one (`part_quote`)."""
    block = view.readings.block_of(e.claim.node)
    quoted = (
        f'"{e.claim.words}"'
        if block is None
        else part_quote(view.texts.get(block, ""), e.claim.words)
    )
    head = f"{quoted} {e.claim.status.value}"
    if e.reason is not None:
        return f"{head}  not established: {e.reason.kind.value}, {e.reason.why}"
    line = f"{head}  established"
    if e.claim.status is Status.UNSUPPORTED:
        line += " by a reading; the paper argues nothing for it"
    elif e.claim.status is Status.CONJECTURED:
        line += " by a reading; the paper does not assert it"
    if e.via is not None:
        line += f" by {view.readings.at.handle(e.via)}"
    under = [view.readings.at.handle(n) for n in e.assumptions] + [
        f'conjecture {view.readings.at.handle(h.claim.node)} "{h.claim.words}"'
        for h in e.under
    ]
    if under:
        line += ", under " + ", ".join(under)
    return line


def established_data(view: View, e: Established) -> dict[str, Any]:
    at = view.readings.at
    return {
        "claim": e.claim.words,
        "standing": e.claim.status.value,
        "established": e.established,
        "via": None if e.via is None else at.handle(e.via),
        "assumptions": [at.handle(n) for n in e.assumptions],
        "under": [
            {"at": at.handle(h.claim.node), "claim": h.claim.words} for h in e.under
        ],
        "reason": None
        if e.reason is None
        else {
            "kind": e.reason.kind.value,
            "at": at.handle(e.reason.node),
            "why": e.reason.why,
        },
    }


def _finding_state(f: judgments.FindingRead) -> str:
    if not f.live:
        return "lapsed"
    return "open" if f.answer is None else f.answer.status.value


def _finding_words(f: judgments.FindingRead) -> str:
    """A finding's state as a line reads it: a lapsed one with the answer
    that settled it, else it reads as open."""
    if not f.live and f.answer is not None:
        return f"lapsed, {f.answer.status.value}"
    return _finding_state(f)


def _suggestion_state(s: suggestions.SuggestionRead) -> str:
    if not s.live:
        return "lapsed"
    return "open" if s.answer is None else s.answer.status.value


#: How a line names a work a reading, finding or suggestion names that the
#: paper cites nowhere and that was not found on the web: as a review names
#: one, an entry of its bibliography (which `show` cannot confirm), apart
#: from a work it cites elsewhere and from one fetched.
CITED_NOWHERE = "cited nowhere in the paper"


def cited_nowhere(view: View) -> Callable[[str], bool]:
    """Whether a work's key names a work the paper cites nowhere at the
    view's state (at a block or at a site no block owns; a file of a work,
    `KEY@…`, by its work), and that was not found on the web (`arxiv:ID`,
    `doi:DOI` in any case, a link)."""
    cited = set(judgments.paper_works(view.readings.at)) | {
        k for c in view.export["document"].get("citations", []) for k in c["keys"]
    }
    return lambda key: (
        not (
            work_of(key) in cited
            or FOUND_KEY.match(key.lower())
            or LINKED_KEY.match(key)
        )
    )


def work_words(key: str, nowhere: Callable[[str], bool]) -> str:
    """A work anchor as a line names it: `work:KEY`, and that the paper
    cites it nowhere where it does not."""
    return f"work:{key}" + (f" ({CITED_NOWHERE})" if nowhere(key) else "")


def suggestion_line(view: View, s: suggestions.SuggestionRead) -> str:
    """A suggestion as the reads list it: its id, state, where it sits,
    the works it offers (one the paper cites nowhere said so) and why, its
    pages, its author."""
    where = (
        ", ".join(dict.fromkeys(view.readings.at.handle(n) for n in s.nodes))
        or "the paper"
    )
    nowhere = cited_nowhere(view)
    line = (
        f"suggestion {s.suggestion.id}  {_suggestion_state(s)}  at {where}  "
        + " ".join(work_words(w, nowhere) for w in s.works)
        + f'  "{s.suggestion.why}"'
    )
    if s.suggestion.pages:
        line += "  pages " + " ".join(p.name for p in s.suggestion.pages)
    return line + f"  (by {s.author.name})"


def suggestion_data(view: View, s: suggestions.SuggestionRead) -> dict[str, Any]:
    nowhere = cited_nowhere(view)
    return {
        "id": s.suggestion.id,
        "state": _suggestion_state(s),
        "works": list(s.works),
        "cited_nowhere": [w for w in s.works if nowhere(w)],
        "why": s.suggestion.why,
        "author": s.author.name,
        "blocks": [view.readings.at.handle(n) for n in s.nodes],
        "pages": [stamp_data(p) for p in s.suggestion.pages],
        "note": None if s.answer is None else s.answer.note,
        "anchors": [
            {
                "kind": a.kind.value,
                "node": a.node,
                "words": a.words,
                "work": a.work,
                "file": a.file,
            }
            for a in s.suggestion.anchors
        ],
    }


#: How a finding a review handed back as a disagreement of reading says so.
READING = "a disagreement of reading, yours to answer"


def weighed_words(f: judgments.FindingRead) -> str:
    """The proposal a refiled finding's refiling weighed, and what came of
    it: not taken, its refiling's evidence the review's answer."""
    assert f.weighed is not None and f.reaffirmed is not None
    return f"{proposal_words(f.weighed)} — not taken: refiled as {f.reaffirmed}"


def proposal_words(p: judgments.Proposal) -> str:
    """A proposal as the reads show it: its answer, who proposed it (an
    agent, or a person) and why."""
    a = p.answer
    return (
        f"proposed {a.status.value}"
        + (f" of {a.of}" if a.of else "")
        + f" by {p.author.name}: {a.note or ''}".rstrip()
    )


def finding_line(f: judgments.FindingRead, on: str = "") -> str:
    """A finding as the reads list it, `on` where it is (`anchor_words`)
    before its problem; a refiled one the reason it stands now first, its
    problem as first filed behind it."""
    found = f.finding
    line = (
        f"finding {found.id}  severity {found.severity}  {found.aspect.value}  "
        f"{_finding_words(f)}{on}  "
    )
    if found.reaffirms is not None and found.evidence:
        line += (
            f'{READING if found.reading else "stands"}: "{found.evidence}"  '
            f'as first filed: "{found.problem}"'
        )
    else:
        line += f'"{found.problem}"'

    if found.fix:
        line += f" — fix: {found.fix}"
    if found.pages:
        line += "  pages " + " ".join(p.name for p in found.pages)
    line += f"  (by {f.author.name})"
    if f.turned_down is not None:
        line += f"  {proposal_words(f.turned_down)} — not taken"
    if f.proposal is not None:
        line += f"  {proposal_words(f.proposal)}"
    return line


def anchor_words(view: View, f: judgments.FindingRead) -> str:
    """Where a finding is, as a line names it: each anchor's block by its
    handle and the words it anchors, a work (one the paper cites nowhere
    said so), a file."""
    named = []
    nowhere = cited_nowhere(view)
    for a in f.finding.anchors:
        on = []
        if a.node is not None:
            on.append(view.readings.at.handle(a.node))
        if a.work is not None:
            on.append(work_words(a.work, nowhere))
        if a.file is not None:
            on.append(f"file:{a.file}")
        if a.words:
            on.append(f'"{a.words}"')
        named.append(" · ".join(on) or a.kind.value)
    return f"  on {'; '.join(named)}" if named else ""


def finding_data(view: View, f: judgments.FindingRead) -> dict[str, Any]:
    found = f.finding
    nowhere = cited_nowhere(view)
    return {
        "id": found.id,
        "severity": found.severity,
        "aspect": found.aspect.value,
        "state": _finding_state(f),
        "problem": found.problem,
        "fix": found.fix,
        "author": f.author.name,
        "anchors": [
            {
                "kind": a.kind.value,
                "node": a.node,
                "words": a.words,
                "work": a.work,
                "file": a.file,
            }
            | ({"cited_nowhere": True} if a.work and nowhere(a.work) else {})
            for a in found.anchors
        ],
        "pages": [stamp_data(p) for p in found.pages],
        "answer": None
        if f.answer is None
        else {"status": f.answer.status.value, "note": f.answer.note},
        "proposal": _proposal_data(f.proposal),
        "cause": None if f.live else cause_data(f.cause),
        "clears": finding_clears(f),
        "first": judgments.first_filing(view.store.judgment_records().findings, found),
        "refiled_as": f.reaffirmed,
        "evidence": found.evidence,
        "reading": found.reading,
        "weighed": _proposal_data(f.weighed),
        "turned_down": _proposal_data(f.turned_down),
    }


def _proposal_data(p: judgments.Proposal | None) -> dict[str, Any] | None:
    if p is None:
        return None
    return {
        "status": p.answer.status.value,
        "of": p.answer.of,
        "note": p.answer.note,
        "author": p.author.name,
    }


def element_line(view: View, e: Any, then: View | None) -> list[str]:
    """A basis element as it reads now: a node's line and text, its text
    then where it differs; a file or the paper by name; an edge's line."""
    at = view.readings.at
    if e.kind is judgments.BasisKind.EDGE:
        for edge in view.edges:
            source, kind, target, parts = at.edge_ids[edge["id"]]
            if (source, (kind, target), tuple(parts)) == (e.name, e.edge, e.parts):
                return [f"{view.handle(edge['source'])} {view.edge_line(edge)}"]
        return [f"edge from {e.name}: not held now"]
    if e.kind is judgments.BasisKind.FILE:
        return [f"file:{e.name}"]
    if e.kind is judgments.BasisKind.PAPER:
        return ["the paper, whole"]
    ident = at.ident_of.get(e.name)
    if ident is None:
        return [f"{e.name}: not held now"]
    parts = f" [{', '.join(e.parts)}]" if e.parts else ""
    lines = [view.header(ident) + parts]
    if ident in view.blocks:
        now = view.texts[ident]
        lines.append(f"  now: {clipped(now)}")
        old_ident = None
        if then is not None:
            old_ident = next((i for i, n in then.nodes.items() if n == e.name), None)
        if then is not None and old_ident in then.blocks:
            was = then.texts[old_ident]
            if was != now:
                lines.append(f"  then: {clipped(was)}")
    return lines


def _read_named(view: View, ref: str) -> judgments.Read:
    try:
        wanted = judgments.judgment_named(view.store, ref)
        judgments.refuse_proposed(wanted)
    except judgments.JudgmentError as error:
        raise QueryError(str(error)) from None
    read = next((r for r in view.readings.reads if r.judgment == wanted), None)
    if read is None:
        raise QueryError(
            f"judgment {wanted.id} was made on state {wanted.state}, which is not "
            f"along state {view.state.id}'s parents"
        )
    return read


def _roots(causes: Sequence[judgments.Cause]) -> list[tuple[str, str, str]]:
    """The edits a lapse's causes began at, each once, in the order found:
    `(parent, state, root)`, the root an update's record names on the
    moved or stale cause a meaning cause carries, or on a Δ that carries
    (a block read by its own Δ names its root; an edited block is its own)."""
    found: list[tuple[str, str, str]] = []
    for c in causes:
        moved = [w for w in c.data.get("causes", ()) if isinstance(w, Mapping)]
        roots = [w["root"] for w in moved] + [
            d.get("root") or d.get("handle") for d in c.data.get("deltas", ())
        ]
        for root in roots:
            if root and (c.parent, c.state, root) not in found:
                found.append((c.parent, c.state, root))
    return found


def _edit_item(view: View, parent: str, state: str, root: str) -> Item:
    """An edit a lapse began at: its block at the update's state, its Δ
    lines with their notes, its text then beside its text there."""
    at = View(view.store, versions.state(view.store, state))
    before = View(view.store, versions.state(view.store, parent))
    update = (at.export.get("review") or {}).get("update") or {}
    lines = [f"{root}'s edit, at state {state}"]
    lines += [
        f"  Δ {d['class']}"
        + (f" [{d['entry']}]" if d.get("entry") else "")
        + (f" — {d['note']}" if d.get("note") else "")
        for d in update.get("deltas", ())
        if d.get("handle") == root
    ]
    ident = next((i for i, h in at.handles.items() if h == root), None)
    gone = root.removeprefix(DELETED)
    if gone != root and gone in before.blocks:
        lines.append(f"  then: {clipped(before.texts[gone])}")
        lines.append("  now: deleted")
    elif ident in at.blocks:
        node = at.nodes.get(ident)
        old = next((i for i, n in before.nodes.items() if n == node), None)
        if old in before.blocks and before.texts[old] != at.texts[ident]:
            lines.append(f"  then: {clipped(before.texts[old])}")
        lines.append(f"  now: {clipped(at.texts[ident])}")
    return Item("\n".join(lines), {"root": root, "state": state})


def lapse_edits(view: View, refs: Sequence[str]) -> list[Item]:
    """The edits the lapses of the judgments `refs` began at, each once."""
    found: list[tuple[str, str, str]] = []
    for ref in refs:
        for root in _roots(_read_named(view, ref).walked.causes):
            if root not in found:
                found.append(root)
    return [_edit_item(view, *root) for root in found]


def reask_report(view: View, ref: str, edits_above: bool = False) -> Report:
    """The re-ask brief: a judgment, every cause that
    lapsed it, the edits they began at (each Δ with its note, its text then
    beside its text at the update; named alone where `edits_above`, a sheet
    showing them once above its briefs), then its subject and basis as
    they read now (the one hop at this state and the elements recorded),
    those a cause names first, each node's text then beside now; the edges
    gone are the causes'."""
    read = _read_named(view, ref)
    j = read.judgment
    on = read.subject
    nodes = [n for n in (on.node, on.target) if n is not None]
    report = Report(
        head=_judgment_head(view, read),
        data={
            "judgment": j.id,
            "state": j.state,
            "at": view.state.id,
            "lapsed": not read.live,
        },
    )
    report.sections["causes"] = cause_items(read.walked)
    roots = _roots(read.walked.causes)
    if roots:
        report.sections["the_edit"] = [
            Item(
                f"{root}'s edit, at state {state}: above",
                {"root": root, "state": state},
            )
            if edits_above
            else _edit_item(view, parent, state, root)
            for parent, state, root in roots
        ]
    then = View(view.store, versions.state(view.store, j.state))
    now = judgments.merged(
        [
            *judgments.judged_hop(j.aspect, j.subject.file)(view.readings.at, nodes),
            *j.basis,
        ]
    )
    # what changed first: the elements a cause names, then the rest
    caused = {(c.element.kind, c.element.name) for c in read.walked.causes}
    now = tuple(sorted(now, key=lambda e: (e.kind, e.name) not in caused))
    report.sections["basis_now"] = [
        Item(
            "\n".join(element_line(view, e, then)),
            {"kind": e.kind.value, "name": e.name},
        )
        for e in now
        if not (
            e.kind is judgments.BasisKind.EDGE
            and element_line(view, e, None)[0].endswith("not held now")
        )
    ]
    report.sections["questions"] = reask_questions(view, read, now)
    return report


def reask_questions(
    view: View, read: judgments.Read, basis: Sequence[judgments.BasisElement]
) -> list[Item]:
    """What a re-ask answers: whether the
    conclusion stands on the basis as it reads now (`reaffirm`), and, for
    each Δ the state's own update record holds on a basis block (the
    rule's aside), whether its meaning moved beyond it (a Δ correction,
    `change --delta`)."""
    j = read.judgment
    found = []
    if not read.live:
        found.append(
            Item(
                f"does the conclusion, {j.verdict.value}, stand on the basis as it "
                f"reads now? yes: `axiomation reaffirm PAPER {j.id} --depth D "
                f"--evidence TEXT`, its depth the weaker of D and {j.depth.value}; "
                "no: `axiomation judge` with the verdict found, or `axiomation find`",
                {"question": "conclusion", "judgment": j.id},
            )
        )
    at = view.readings.at
    nodes = {
        m
        for e in basis
        if e.kind is judgments.BasisKind.NODE
        for m in (e.name, *at.members.get(e.name, ()))
    }
    record = (view.export.get("review") or {}).get("update") or {}
    for d in record.get("deltas", []):
        if d["by"] == "rule" or at.node_of.get(d["block"]) not in nodes:
            continue
        handle = view.handle(d["block"])
        line = delta_line(d["entry"] or "", d["class"], d["note"])
        found.append(
            Item(
                f"does {handle}'s meaning move beyond `{line}`? if so: "
                f"`axiomation change PAPER --delta {handle} {shlex.quote(line)}`, "
                "its class "
                "corrected",
                {"question": "meaning", "block": d["block"], "delta": dict(d)},
            )
        )
    return found


# --- reach --------------------------------------------------------------------


def reach_report(view: View, ref: str, through_claims: bool = False) -> Report:
    """What an edit to a block (or group) reaches by the reach rule (`docs/model.md` §
    5), `closure.reach` on the kept graph, printed a line per reached block:
    each reached block, its depth, whether its meaning moves or it is asked, the edge it
    is reached by, and its standings. An asked block stops the walk; with
    `through_claims` it goes on past each as though its meaning moved, the
    most any answers could reach, each block beyond naming the asked block
    it came past."""
    ident = view.resolve(ref)
    graph = from_export(view.export)
    found = reach(graph, ident)
    reached: list[tuple[Reached, str | None]] = [(r, None) for r in found.reached]
    support = list(found.support)
    if through_claims:
        beyond, more = past_asked(graph, ident, found)
        reached += beyond
        support += [s for s in more if s not in support]
    moved = sum(1 for r, _ in reached if r.changed)
    beyond = sum(1 for _, p in reached if p is not None)
    report = Report(
        head=[
            f"an edit to {view.handle(ident)} ({view.role_names(ident)}) at state "
            f"{view.state.id}: {len(reached)} reached; {moved} change meaning "
            f"without asking, {len(reached) - moved} asked"
            + (
                f"; {beyond} past an asked block, as though each answered that "
                "it changed"
                if through_claims
                else " and stop"
            )
            + f"; support recomputed for {len(support)}",
            "changes: it uses a definition or an assumption that moved, and the walk "
            "goes on from it; asked: it relies on a claim, argues about it or "
            "justifies it, is asked whether it still holds, and "
            + (
                "the walk would stop there; here it goes on as though each changed"
                if through_claims
                else "the walk stops there (--through-claims walks on past each: "
                "the most it can reach)"
            ),
        ],
        data={"start": view.header_data(ident), "through_claims": through_claims},
    )
    items = []
    for r, past in reached:
        e = r.via
        parts = f" [{', '.join(e.parts)}]" if e.parts else ""
        if e.kind == "qualifies":
            how = f"qualified by {view.handle(e.source)}{parts}"
        else:
            how = f"{e.kind} {view.handle(e.target)}{parts}" + (
                f" {e.flag}" if e.flag else ""
            )
        standing = ", ".join(s["status"] for s in view.standings_of(r.handle))
        roles = view.role_names(r.handle) if r.handle in view.blocks else "none"
        items.append(
            Item(
                f"{r.depth}  {view.handle(r.handle)} [{roles}]  "
                f"{'changes' if r.changed else 'asked'}  {how} ({r.content})"
                + (f"  ⊢ {standing}" if standing else "")
                + ("" if past is None else f"  past {view.handle(past)}"),
                {
                    "node": view.node(r.handle),
                    "handle": view.handle(r.handle),
                    "block": r.handle,
                    "depth": r.depth,
                    "meaning_moved": r.changed,
                    "via": {
                        "source": view.handle(e.source),
                        "kind": e.kind,
                        "target": view.handle(e.target),
                        "parts": list(e.parts),
                    },
                    "content": r.content,
                    "standings": standing.split(", ") if standing else [],
                    "past": None if past is None else view.handle(past),
                },
            )
        )
    report.sections["reached"] = items
    report.sections["support_recomputed"] = [
        Item(
            f"{view.handle(target)} (by {view.handle(by)})",
            {"block": view.handle(target), "by": view.handle(by)},
        )
        for target, by in support
    ]
    return report


def past_asked(
    graph: EdgeGraph, start: str, found: Reach
) -> tuple[list[tuple[Reached, str | None]], list[tuple[str, str]]]:
    """The walk past `found`'s asked blocks as though each one's meaning
    moved (never as though its text was edited: no `where-used`,
    `scope:paper` or ambient frame reasons from it), every block beyond
    walked on in turn, nearest first: each at its shortest depth from
    `start`, with the asked block of `found` it came past; and the support
    recomputed beyond. `closure.py` keeps the rule itself."""
    seen = {start, *graph.groups.get(start, ()), *(r.handle for r in found.reached)}
    order = count()
    # nearest first and, at one depth, a block whose meaning moves before one
    # asked, as `closure.reach` lists it
    heap: list[tuple[int, bool, int, str, Reached, str]] = []

    def walk_from(node: str, depth: int, past: str) -> None:
        step = rule(graph, node, ambient=graph.ambient)
        support.extend(s for s in step.support if s not in support)
        for changed, links in ((True, step.goes_on), (False, step.asked)):
            for block, e, content in links:
                if block not in seen:
                    r = Reached(block, e, content, changed, depth + 1)
                    heappush(
                        heap, (depth + 1, not changed, next(order), block, r, past)
                    )

    support: list[tuple[str, str]] = []
    for r in found.reached:
        if not r.changed:
            walk_from(r.handle, r.depth, r.handle)
    beyond: list[tuple[Reached, str | None]] = []
    while heap:
        depth, _, _, block, r, past = heappop(heap)
        if block in seen:
            continue
        seen.add(block)
        beyond.append((r, past))
        walk_from(block, depth, past)
    return beyond, support


# --- blocks by filter ---------------------------------------------------------


@dataclass(frozen=True)
class Filter:
    """Blocks by what they hold: each field's
    values any of them, the fields all of them. `onto` names targets a
    block has an edge onto, `reached_from` sources whose edges reach it,
    both by edges of `kinds` where any are named; `changed` the nodes
    changed since a given state, None for no such filter."""

    standings: tuple[str, ...] = ()
    roles: tuple[str, ...] = ()
    forms: tuple[str, ...] = ()
    sections: tuple[str, ...] = ()
    groups: tuple[str, ...] = ()
    onto: tuple[str, ...] = ()
    reached_from: tuple[str, ...] = ()
    kinds: tuple[str, ...] = ()
    text: str | None = None
    changed: frozenset[str] | None = None
    uses_unsupported: bool = False
    #: a subject's status per aspect, of these aspects where any
    #: are named, and an open, live finding
    judged: tuple[str, ...] = ()
    aspects: tuple[str, ...] = ()
    open_findings: bool = False
    #: a claim (not) established; None for no such filter
    established: bool | None = None
    #: a live reading read off a copy that is not the version of
    #: record (provisional)
    provisional: bool = False


def _in_section(within: Sequence[Section], wanted: str) -> bool:
    """Whether a block lies in the section `wanted` names, `within` its
    sections innermost first: by number (an unnumbered one's as its title
    gives it), any section it lies within; else by label or title words,
    its own section alone. A name such as `L2` that numbers none of them
    is read as title words too."""
    if not within:
        return False
    if re.fullmatch(_NUMBER, wanted) and any(
        s.given_number.casefold() == wanted.casefold() for s in within
    ):
        return True
    if re.fullmatch(r"\d+(?:\.\d+)*", wanted):
        return False
    return wanted == within[0].label or titled(within[0], wanted)


def titled(section: Section, words: str) -> bool:
    """Whether a section's title holds these words, in order, whole."""
    pattern = r"\s+".join(re.escape(w) for w in words.split())
    return (
        bool(pattern)
        and re.search(rf"(?<!\w){pattern}(?!\w)", section.title, re.IGNORECASE)
        is not None
    )


def ruled_words(ruled: rulings.Ruled) -> str:
    """A claim's rulings as words: those that speak, each with its author,
    or that none speaks (each lapsed)."""
    proposal = ruled.proposal
    proposed = (
        f"; proposed by {proposal.read.author.name}, for the next review: "
        f"{_kind_words(proposal.read.ruling)}: {proposal.read.ruling.reason}"
        if proposal is not None and proposal.asks
        else ""
    )
    if not ruled.speaks:
        return (
            "ruled, each ruling lapsed"
            if any(not r.proposed for r in ruled.reads)
            else "unruled"
        ) + proposed
    return (
        "ruled "
        + "; ".join(
            f"{_kind_words(r.ruling)} by {r.author.name}"
            + (f" ({OLDER_QUESTION})" if r.outdated else "")
            + f": {r.ruling.reason}"
            + (f"; fix: {r.ruling.fix}" if r.ruling.fix else "")
            for r in ruled.speaks
        )
        + proposed
    )


def _kind_words(r: Ruling) -> str:
    """A ruling's kind, an owed one's severity beside it (`owed 3`)."""
    if r.kind is not RulingKind.OWED:
        return r.kind.value
    return f"owed {r.severity}"


def _moot(standing: str | None) -> str | None:
    """The standing a claim ruled on holds where it is no longer
    `unsupported`: the paper argues it, so its rulings speak for nothing
    (`docs/model.md` § 8); None while it stands unsupported."""
    if standing is None or standing == Status.UNSUPPORTED.value:
        return None
    return standing


def ruling_line(
    r: rulings.RulingRead, ruled: rulings.Ruled, moot: str | None = None
) -> str:
    """A ruling as `show` lists it: its claim, ruling, author and reason;
    whether it speaks (filed under an older question), lapsed, or
    is live and replaced (a later one of its author's, a person's over an
    agent's, or a review's under the current question); a live one on a
    claim that stands argued since (`moot`, its standing) speaks for
    nothing."""
    state = (
        f"moot: the claim stands {moot}"
        if moot is not None and r.live
        else _proposal_state(r, ruled)
        if r.proposed
        else f"speaks, {OLDER_QUESTION}"
        if r in ruled.speaks and r.outdated
        else "speaks"
        if r in ruled.speaks
        else "replaced"
        if r.live
        else f"withdrawn: {r.withdrawn.reason}"
        if r.withdrawn is not None
        else "lapsed"
    )
    # a lapsed ruling is quoted on the words it was a call on
    words = r.subject.words if r.live else r.ruling.subject.words
    return (
        f'"{words}"  {_kind_words(r.ruling)} by {r.author.name} ({state}): '
        f"{r.ruling.reason}"
        + (f"; fix: {r.ruling.fix}" if r.ruling.fix else "")
        + (
            ", pages " + " ".join(p.name for p in r.ruling.pages)
            if r.ruling.pages
            else ""
        )
        + f", {r.ruling.id}"
        + (f", kept from {r.ruling.reaffirms}" if r.ruling.reaffirms else "")
    )


def _proposal_state(r: rulings.RulingRead, ruled: rulings.Ruled) -> str:
    """A proposed ruling's state: awaiting the next review, or settled by
    the ruling filed after it; lapsed or withdrawn, it asks nothing."""
    if r.withdrawn is not None:
        return f"proposed, withdrawn: {r.withdrawn.reason}"
    if not r.live:
        return "proposed, lapsed"
    later = [x for x in ruled.reads if filed_after(x.order, r.order) and not x.proposed]
    if later:
        return f"proposed, settled by {later[0].ruling.id}"
    if ruled.proposal is not None and ruled.proposal.read is not r:
        return "proposed, replaced"
    return "proposed: the next argument review settles it"


def ruling_data(
    r: rulings.RulingRead, ruled: rulings.Ruled, moot: str | None = None
) -> dict[str, Any]:
    return {
        "id": r.ruling.id,
        "claim": r.subject.words,
        "ruling": r.ruling.kind.value,
        "author": r.author.name,
        "reason": r.ruling.reason,
        "severity": r.ruling.severity,
        "fix": r.ruling.fix,
        "proposed": r.proposed,
        "state": r.ruling.state,
        "live": r.live,
        "speaks": moot is None and r in ruled.speaks,
        "moot": moot,
        "outdated": r.outdated,
        "pages": [stamp_data(p) for p in r.ruling.pages],
        "kept_from": r.ruling.reaffirms,
        "withdrawn": None
        if r.withdrawn is None
        else {"id": r.withdrawn.id, "reason": r.withdrawn.reason},
        "causes": [c.why for c in r.walked.causes],
    }


def unsupported_users(view: View) -> dict[str, list[str]]:
    """Each block that uses an unsupported claim directly, and the claims'
    blocks it uses, in the edges' order: unsupported by its support status
    (`docs/model.md` § 8), since a claim no argument is owed for is no gap
    to rest on. An `assumed` edge rests on no truth of its target."""
    unsupported = {
        s["block"]
        for s, support in zip(view.standings, view.support, strict=True)
        if support is not None and support.status is rulings.Support.UNSUPPORTED
    }
    entries = {e["id"]: e for e in view.export.get("entries", [])}

    def onto_claim(edge: Mapping[str, Any]) -> bool:
        # an edge onto a part relies on that part: a definition beside
        # the claim is not the claim
        parts = edge["target"].get("entries")
        return not parts or any(entries[p]["role"] == "claim" for p in parts)

    found: dict[str, list[str]] = {}
    for e in view.edges:
        target = e["target"].get("block")
        if (
            e["kind"] == "uses"
            and not e.get("assumed")
            and target in unsupported
            and onto_claim(e)
        ):
            claims = found.setdefault(e["source"], [])
            if target not in claims:
                claims.append(target)
    return found


def matching(view: View, chosen: Filter) -> list[str]:
    """The blocks of the state `chosen` keeps, in the paper's order."""
    tests: list[Callable[[str], bool]] = []
    if chosen.standings:
        tests.append(
            lambda b: any(s["status"] in chosen.standings for s in view.standings_of(b))
        )
    if chosen.roles:
        tests.append(
            lambda b: any(r in chosen.roles for r in view.roles(b) or ["none"])
        )
    if chosen.forms:
        forms = {f.lower() for f in chosen.forms}
        tests.append(
            lambda b: bool(
                {
                    view.blocks[b]["form"],
                    (view.blocks[b].get("environment") or "").lower(),
                    (view.blocks[b].get("meaning") or "").lower(),
                }
                & forms
            )
        )
    if chosen.sections:
        tests.append(
            lambda b: any(_in_section(view.within(b), s) for s in chosen.sections)
        )
    if chosen.groups:
        members = {m for g in chosen.groups for m in view.members(view.group(g))}
        tests.append(lambda b: b in members)

    def of_kind(edge: Mapping[str, Any]) -> bool:
        return not chosen.kinds or edge["kind"] in chosen.kinds

    if chosen.onto:
        targets = [view.resolve(r) for r in chosen.onto]
        sources = {
            e["source"]
            for e in view.edges
            if of_kind(e) and any(_onto(e, t) for t in targets)
        }
        tests.append(lambda b: b in sources)
    if chosen.reached_from:
        given = {view.block(r) for r in chosen.reached_from}
        reached: set[str] = set()
        for e in view.edges:
            if e["source"] in given and of_kind(e):
                t = e["target"]
                if "block" in t:
                    reached.add(t["block"])
                elif "group" in t:
                    reached.update(view.members(t["group"]))
        tests.append(lambda b: b in reached)
    if chosen.text is not None:
        try:
            pattern = re.compile(chosen.text, re.IGNORECASE)
        except re.error as error:
            raise QueryError(f"--text {chosen.text!r}: {error}") from None
        tests.append(lambda b: pattern.search(view.texts[b]) is not None)
    if chosen.changed is not None:
        changed = chosen.changed
        tests.append(lambda b: view.node(b) in changed)
    if chosen.uses_unsupported:
        users = unsupported_users(view)
        tests.append(lambda b: b in users)
    if chosen.judged:
        tests.append(
            lambda b: any(
                s.status.value in chosen.judged
                and (not chosen.aspects or s.aspect.value in chosen.aspects)
                for s in view.statuses_of(b)
            )
        )
    if chosen.open_findings:
        shown = {
            view.readings.block_of(n)
            for f in view.readings.findings
            if f.open and f.live
            for n in f.nodes
        }
        tests.append(lambda b: b in shown)
    if chosen.established is not None:
        tests.append(
            lambda b: any(
                e.established is chosen.established for e in view.established_of(b)
            )
        )
    if chosen.provisional:
        tests.append(
            lambda b: any(
                r.live and r.provisional for s in view.statuses_of(b) for r in s.reads
            )
        )
    return [b for b in view.blocks if all(test(b) for test in tests)]


def blocks_report(view: View, chosen: Filter) -> Report:
    found = matching(view, chosen)
    return Report(
        head=[f"{len(found)} blocks of {len(view.blocks)} at state {view.state.id}"],
        data={"state": view.state.id, "count": len(found)},
        sections={"blocks": [view.item(b) for b in found]},
    )


def changed_since(since: State, at: State) -> frozenset[str]:
    """The nodes of `at` whose text differs from theirs at `since`, or that
    `since` does not hold."""
    before = {n.node: n.text for n in since.nodes}
    return frozenset(n.node for n in at.nodes if before.get(n.node) != n.text)


# --- changes between two kept states ------------------------------------------


def _between(store: RecordStore, older: State, newer: State) -> list[State]:
    """The states after `older` up to `newer`, along `newer`'s parents."""
    path = versions.chain(store, newer)
    ids = [s.id for s in path]
    if older.id not in ids:
        raise QueryError(
            f"state {older.id} is not before state {newer.id}: the text's changes "
            "are read along a state's parents"
        )
    return list(path[ids.index(older.id) + 1 :])


def text_changes(
    store: RecordStore, old: View, new: View, whole: bool = False
) -> list[Item]:
    """Each node whose block changed between the two states, by identity's
    class across versions (`changed`, `moved`, `split`, `merged`, `added`,
    `deleted`, …) or by its text within one, its old text beside its new,
    and the nodes a split or a merge joined it with (`from` where it began
    from their text)."""
    after = _between(store, old.state, new.state)
    within = {s.id for s in after}
    nodes = [n.node for n in old.state.nodes] + [n.node for s in after for n in s.nodes]
    by_node_old = {n.node: n.block for n in old.state.nodes}
    by_node_new = {n.node: n.block for n in new.state.nodes}
    shown = versions.short_ids(nodes)
    items = []
    for node, steps in versions.histories(store, nodes, new.state).items():
        was, now = by_node_old.get(node), by_node_new.get(node)
        if was is None and now is None:
            continue
        classes = list(
            dict.fromkeys(
                s.change for s in steps if s.state in within and s.change != "same"
            )
        )
        if not classes:
            continue
        joined = list(
            dict.fromkeys(j for s in steps if s.state in within for j in s.joined)
        )
        handle = new.handle(now) if now is not None else old.handle(was or "")
        lines = [f"{shown[node]}  {handle}  {' then '.join(classes)}"]
        if joined:
            lines[0] += f"  ({'from' if was is None else 'with'} {', '.join(joined)})"
        if was is not None and (now is None or old.texts[was] != new.texts[now]):
            lines.append(f"  was: {clipped(old.texts[was], whole)}")
        if now is not None:
            lines.append(f"  now: {clipped(new.texts[now], whole)}")
        items.append(
            Item(
                "\n".join(lines),
                {
                    "node": node,
                    "handle": handle,
                    "classes": classes,
                    "joined": joined,
                    "old": None
                    if was is None
                    else {"block": was, "text": clipped(old.texts[was], whole)},
                    "new": None
                    if now is None
                    else {"block": now, "text": clipped(new.texts[now], whole)},
                },
            )
        )
    order = {n.node: i for i, n in enumerate(new.state.nodes)}
    items.sort(key=lambda i: order.get(i.data["node"], len(order)))
    return items


def _answer(edge: Mapping[str, Any]) -> str:
    """Who set a line (an edge, a self mark, a standing), and its answer: a
    share's lines name who set them alone (`shares`)."""
    provenance = edge["provenance"]
    question = provenance.get("question") or ""
    by = (
        PERSON
        if provenance["by"] == PERSON
        else f"{provenance['by']} {provenance.get('model', '')}"
    )
    return f"by {by}".rstrip() + (f", answer {question[:12]}" if question else "")


def _by(item: Mapping[str, Any]) -> str:
    """A self mark's or a standing's author, as an edge's is printed."""
    return f"  ({_answer(item)})"


def _diffed(
    old: Iterable[tuple[Any, Item]], new: Iterable[tuple[Any, Item]]
) -> list[Item]:
    """What began and ended between two lists of keyed items: `+` lines for
    the new state's, `-` for the old's, a key counted as often as it
    stands."""
    old_items, new_items = list(old), list(new)
    before = Counter(k for k, _ in old_items)
    after = Counter(k for k, _ in new_items)
    found = []
    for sign, items, others, mine in (
        ("-", old_items, after, before),
        ("+", new_items, before, after),
    ):
        left = dict(mine - others)
        for key, item in items:
            if left.get(key, 0) > 0:
                left[key] -= 1
                found.append(_signed(sign, item))
    return found


def _signed(sign: str, item: Item) -> Item:
    return Item(
        f"{sign} {item.line}",
        {"change": "ended" if sign == "-" else "began", **item.data},
    )


#: A node's names in the earlier state: the nodes its text came from.
Names = Callable[[str], tuple[str, ...]]


@dataclass
class _Line:
    """A line of one state for matching across a split or a merge:
    its keys per pass, from the most to the least specific, each a list of
    the keys it may have had in the earlier state (an earlier line's, its
    own), its own key per pass, and its item."""

    keys: list[list[tuple[Any, ...]]]
    own: list[tuple[Any, ...]]
    item: Item
    nodes: tuple[str, ...] = ()


def _namings(names: Names, *nodes: str) -> list[tuple[str, ...]]:
    """Every naming of `nodes` in the earlier state, their own first."""
    found: list[tuple[str, ...]] = [()]
    for n in nodes:
        found = [(*k, name) for k in found for name in names(n)]
    return list(dict.fromkeys([nodes, *found]))


def _matched(
    old: list[_Line], new: list[_Line], passes: int
) -> tuple[list[tuple[_Line, _Line]], list[_Line], list[_Line]]:
    """Old and new lines paired, pass by pass: first each new line by its
    own key, then by any key it may have had. What is left of each."""
    pairs: list[tuple[_Line, _Line]] = []
    old_left, new_left = list(old), list(new)
    for p in range(passes):
        for exact in (True, False):
            waiting: dict[tuple[Any, ...], list[_Line]] = {}
            for o in old_left:
                waiting.setdefault(o.own[p], []).append(o)
            still = []
            for n in new_left:
                candidates = n.own[p : p + 1] if exact else n.keys[p]
                key = next((k for k in candidates if waiting.get(k)), None)
                if key is None:
                    still.append(n)
                    continue
                o = waiting[key].pop(0)
                old_left.remove(o)
                pairs.append((o, n))
            new_left = still
    return pairs, old_left, new_left


def _carried(o: _Line, n: _Line) -> Item:
    return Item(
        f"~ {n.item.line}\n  was: {o.item.line}",
        {"change": "carried", "old": o.item.data, "new": n.item.data},
    )


def _settled(
    old: list[_Line], new: list[_Line], passes: int, ended: Collection[str]
) -> tuple[list[Item], list[Item], list[_Line]]:
    """The lines that began or ended, and those a split or a merge carried
    (`~`, old beside new): a pair whose nodes changed is carried, a pair
    whose nodes did not is no change. An earlier line left whose node
    ended in a merge, and that a later line may have been, is folded into
    it. The later lines left for the caller to place."""
    pairs, old_left, new_left = _matched(old, new, passes)
    carried = [_carried(o, n) for o, n in pairs if o.nodes != n.nodes]
    changed = []
    for o in old_left:
        into = next(
            (
                n
                for n in new
                if o.own[-1] in n.keys[-1] and any(x in ended for x in o.nodes)
            ),
            None,
        )
        if into is None:
            changed.append(_signed("-", o.item))
        else:
            carried.append(
                Item(
                    f"~ {into.item.line}\n  folded in: {o.item.line}",
                    {"change": "folded", "old": o.item.data, "new": into.item.data},
                )
            )
    return changed, carried, new_left


def graph_changes(
    old: View, new: View, origins: Mapping[str, tuple[str, ...]] | None = None
) -> dict[str, list[Item]]:
    """What a person reading the change should look at, then the edges
    (with self marks), standings, roles and groups that began or ended
    between two kept states, each edge with the answer that set it, then
    what a split or a merge carried; nodes name the blocks across states
    (`versions.edge_ids`, `standing_ids`) and `origins` (`versions.origins`,
    each later node's earlier ones; none given, each node is its own)
    names them through a split or a merge.

    A line carried by a split or a merge is listed once as carried, never
    as ended and begun. A line onto the whole of a block that split, or
    from the whole of one (no quote of its source), relied on text now in
    several blocks: it is listed to look at, beside what the later state
    holds from its source onto the pieces."""
    old_nodes = {n.node for n in old.state.nodes}
    new_nodes = {n.node for n in new.state.nodes}
    ended = old_nodes - new_nodes
    of = dict(origins) if origins is not None else {n: (n,) for n in new_nodes}
    names = versions.naming(of, old_nodes, new_nodes)

    def own(node: str) -> tuple[str, ...]:
        return (node,)

    split = versions.begun_from(of, old.state) if origins is not None else set()

    def edges(view: View, named: Names) -> list[_Line]:
        ids = versions.edge_ids(view.state, view.export)
        found = []
        for e in view.edges:
            source, kind, target, parts = ids[e["id"]]
            quote = norm(_quoted(e))
            namings = _namings(named, source, target)
            found.append(
                _Line(
                    [
                        [(s, kind, t, parts, quote) for s, t in namings],
                        [(s, kind, t, parts) for s, t in namings],
                    ],
                    [
                        (source, kind, target, parts, quote),
                        (source, kind, target, parts),
                    ],
                    Item(
                        f"{view.handle(e['source'])} {view.edge_line(e)}"
                        f"  ({_answer(e)})",
                        {"edge": e, "source_node": view.node(e["source"])},
                    ),
                    (source, target),
                )
            )
        for m in view.selfs:
            node, quote = view.node(m["block"]), norm(_quoted(m))
            key = (node, "self", quote)
            found.append(
                _Line(
                    [[(n, "self", quote) for (n,) in _namings(named, node)]] * 2,
                    [key, key],
                    Item(
                        f"{view.handle(m['block'])} {view.self_line(m)}{_by(m)}",
                        {"self": m, "source_node": node},
                    ),
                    (node,),
                )
            )
        return found

    def standings() -> tuple[list[_Line], list[_Line]]:
        # a claim is its words: exact first, then the earlier claim it
        # continues (`versions.continued_parts`); the third keys
        # only fold a merged-away claim into the claim that took it in
        older = versions.standing_ids(old.state, old.export)
        newer = versions.standing_ids(new.state, new.export)
        continues = versions.continued_parts(older, newer, names)

        def line(view: View, s: Mapping[str, Any], node: str) -> Item:
            return Item(
                f"{view.handle(s['block'])} {view.standing_line(s)}{_by(s)}",
                {"standing": s, "node": node},
            )

        old_lines = [
            _Line(
                [],
                [
                    (node, claim, standing_status(s)),
                    (i, standing_status(s)),
                    (i, standing_status(s)),
                ],
                line(old, s, node),
                (node,),
            )
            for i, ((node, claim), s) in enumerate(
                zip(older, old.standings, strict=True)
            )
        ]
        new_lines = []
        for j, ((node, claim), s) in enumerate(zip(newer, new.standings, strict=True)):
            status = standing_status(s)
            own = continues.get(j, ("new", j))
            named = names(node)
            took_in = [
                i
                for i, (old_node, words) in enumerate(older)
                if old_node in named and versions.shared_characters(words, claim)
            ]
            new_lines.append(
                _Line(
                    [
                        [(n, claim, status) for (n,) in _namings(names, node)],
                        [(own, status)],
                        [(i, status) for i in took_in],
                    ],
                    [(node, claim, status), (own, status), (own, status)],
                    line(new, s, node),
                    (node,),
                )
            )
        return old_lines, new_lines

    def groups(view: View, named: Names) -> list[_Line]:
        found = []
        for g in view.groups:
            node, kind = view.node(g), view.groups[g]["kind"]
            members = tuple(view.node(b) for b in view.groups[g]["blocks"])
            key = (node, kind, members)
            through = (
                node,
                kind,
                tuple(dict.fromkeys(o for m in members for o in named(m))),
            )
            found.append(
                _Line(
                    [[key, through]],
                    [key],
                    Item(view.header(g), {"node": node, **view.header_data(g)}),
                    members,
                )
            )
        return found

    look: list[Item] = []
    old_edges, new_edges = edges(old, own), edges(new, names)
    whole = [
        line
        for line in old_edges
        if "edge" in line.item.data
        and (
            (
                line.nodes[1] in split
                and not line.item.data["edge"]["target"].get("entries")
            )
            or (line.nodes[0] in split and not line.item.data["edge"].get("evidence"))
        )
    ]
    changed, carried, began = _settled(
        [line for line in old_edges if line not in whole], new_edges, 2, ended
    )
    for line in whole:
        source, target = line.nodes
        kind = line.item.data["edge"]["kind"]
        instead = [
            n
            for n in began
            if "edge" in n.item.data
            and n.item.data["edge"]["kind"] == kind
            and source in names(n.nodes[0])
            and target in names(n.nodes[1])
        ]
        began = [n for n in began if n not in instead]
        now = [f"  now: {n.item.line}" for n in instead] or [
            "  now: nothing from its source onto the pieces"
        ]
        look.append(
            Item(
                "\n".join([f"! {line.item.line}", *now]),
                {
                    "change": "look",
                    "why": "its block split" if target in split else "its source split",
                    "old": line.item.data,
                    "now": [n.item.data for n in instead],
                },
            )
        )
    sections: dict[str, list[Item]] = {"to_look_at": look}
    sections["edges"] = changed + [_signed("+", n.item) for n in began]
    s_changed, s_carried, s_began = _settled(*standings(), 2, ended)
    sections["standings"] = s_changed + [_signed("+", n.item) for n in s_began]
    carried += s_carried

    def roles(view: View) -> list[tuple[Any, Item]]:
        return [
            (
                (view.node(b), view.role_names(b)),
                Item(
                    f"{view.shown(b)}  {view.handle(b)}  {view.role_names(b)}",
                    {"node": view.node(b), "roles": view.roles(b)},
                ),
            )
            for b in view.blocks
        ]

    old_roles, new_roles = roles(old), roles(new)
    # a node only one state holds is the text's change, not the roles'
    both = old_nodes & new_nodes
    sections["roles"] = _diffed(
        [(k, i) for k, i in old_roles if k[0] in both],
        [(k, i) for k, i in new_roles if k[0] in both],
    )
    g_changed, g_carried, g_began = _settled(
        groups(old, own), groups(new, names), 1, ended
    )
    sections["groups"] = g_changed + [_signed("+", n.item) for n in g_began]
    sections["carried"] = carried + g_carried
    return sections


def changes_report(
    store: RecordStore, old: View, new: View, whole: bool = False
) -> Report:
    """What changed from one kept state to a later one: the text, then the
    graph, then the judgments and findings that lapsed between them."""
    report = Report(
        head=[
            f"from state {old.state.id} ({old.state.command.value}, version "
            f"{old.state.version[:12]}) to state {new.state.id} "
            f"({new.state.command.value}, version {new.state.version[:12]})"
        ],
        data={"from": old.state.id, "to": new.state.id},
    )
    report.sections["text"] = text_changes(store, old, new, whole)
    report.sections.update(
        graph_changes(old, new, versions.origins(store, old.state, new.state))
    )
    # an edge an update lost is listed to look at once, not again as ended
    looked = {i.data["old"]["edge"]["id"] for i in report.sections["to_look_at"]}
    lost = [
        i for i in lost_edges(old, new) if i.data["old"]["edge"]["id"] not in looked
    ]
    ids = {i.data["old"]["edge"]["id"] for i in lost}
    report.sections["to_look_at"] += lost
    report.sections["edges"] = [
        i
        for i in report.sections["edges"]
        if i.data.get("edge", {}).get("id") not in ids
    ]
    report.sections.update(update_sections(old, new))
    report.sections["lapsed"] = lapsed_between(old, new)
    return report


def marks_after_edit(
    old: View, new: View, touched: Collection[str], shown: bool = True
) -> list[Item]:
    """The stale marks open at `old` whose owner holds none at `new`: re-read
    by an edit `shown` them, else closed by a Δ it corrected (lines given
    without the sheet, by `change`, clear none, `docs/model.md` § 5); then
    the marks open at `new` on the owners it touched (`touched`, handles),
    which a reread clears once read."""
    still = {m.node for m in new.marks}
    gone = "cleared" if shown else "closed by a corrected Δ"
    found = [
        Item(
            f"{gone}: {m.handle}  stale since {m.since}: {m.why}",
            {"change": "cleared" if shown else "closed", **m.data()},
        )
        for m in old.marks
        if m.node not in still
    ]
    short = versions.short_ids(n.node for n in new.state.nodes)
    found += [
        Item(
            f"open: {m.handle}  stale since {m.since}: {m.why}  (`axiomation reread "
            f"PAPER {short.get(m.node, m.node)} --reason …` once read)",
            {"change": "open", **m.data()},
        )
        for m in new.marks
        if m.handle in touched
    ]
    return found


def lapsed_between(old: View, new: View) -> list[Item]:
    """What lapsed from one kept state to a later one: each
    item `open` lists as lapsed at `new` one of whose readings was live at
    `old` or filed since, or whose finding was live or not filed there; a
    subject lapsed at both on the same readings is not. By the readings,
    which keep their ids across states where a subject's words may not,
    whoever re-read it; never by a cause's state: the first cause is
    chosen by kind before time, and a page read again or a script run
    again names the state read."""
    was = {r.judgment.id for r in old.readings.reads if not r.live}
    was_found = {f.finding.id for f in old.readings.findings if not f.live}

    def newly(item: Item) -> bool:
        if "finding" in item.data:
            return item.data["finding"] not in was_found
        return not was.issuperset(item.data["judgments"])

    return [
        i
        for i in _judged_items(new)
        if i.data["kind"] == OpenKind.LAPSED.value and newly(i)
    ]


def update_sections(old: View, new: View) -> dict[str, list[Item]]:
    """Where `new` is an update of `old`, the update's own record: each Δ line its
    writer gave (`deltas`), each block it asked again, with why and from where
    (`reopened`), each corrected boundary of `old` it could not carry
    (`corrections_lost`) and each group of `old` it could not carry
    (`groups_lost`); none between any other two states, whose step is not one
    update."""
    if new.state.command is not Command.UPDATE or new.state.parent != old.state.id:
        return {}
    record = (new.export.get("review") or {}).get("update") or {}
    deltas = []
    for d in record.get("deltas", []):
        line = delta_line(d["entry"] or "", d["class"], d["note"])
        deltas.append(
            Item(
                f"{new.handle(d['block'])}  {line}  (by {d['by']})",
                {
                    "block": d["block"],
                    "node": new.node(d["block"]),
                    "handle": new.handle(d["block"]),
                    "entry": d["entry"],
                    "class": d["class"],
                    "note": d["note"],
                    "by": d["by"],
                },
            )
        )
    by_handle = {handle: ident for ident, handle in new.handles.items()}
    reopened = []
    for r in record.get("reopened", []):
        ident = by_handle.get(r["handle"])
        handle = new.handle(ident) if ident else r["handle"]
        origin = r.get("from")
        if origin in by_handle:
            origin = new.handle(by_handle[origin])
        reopened.append(
            Item(
                f"{handle}  {r['why']}" + (f"  (from {origin})" if origin else ""),
                {
                    "block": ident,
                    "node": new.node(ident) if ident else None,
                    "handle": handle,
                    "why": r["why"],
                    "from": origin,
                    "session": r.get("session"),
                },
            )
        )
    return {
        "deltas": deltas,
        "reopened": reopened,
        "corrections_lost": corrections_lost(old, record.get("corrections_lost", [])),
        "groups_lost": groups_lost(old, record.get("groups_lost", [])),
    }


def lost_edges(old: View, new: View) -> list[Item]:
    """Where `new` is an update of `old`: each edge of `old` onto a block
    the update deleted, or onto a group whose every block it deleted, from
    a source that lives on, with no line in its place: an edge of its kind
    from the source's heirs onto a target the source did not point at with
    that kind in `old`. Where a source lost several edges of one kind and
    fewer stand in their place, each is listed, since which one a new line
    replaces cannot be told. The heirs are the update's `previous`; none
    between any other two states."""
    record = (new.export.get("review") or {}).get("update") or {}
    # by the version it started from: a re-run of an update continues
    # its own state, not the one it started from
    if new.state.command is not Command.UPDATE or record.get("old") != (
        old.state.version
    ):
        return []
    heirs_of: dict[str, set[str]] = {}
    for block, olds in (record.get("previous") or {}).items():
        for o in olds:
            heirs_of.setdefault(o, set()).add(block)

    def heirs(ident: str) -> set[str]:
        group = old.groups.get(ident)
        if group is None:
            return heirs_of.get(ident, set())
        held = {h for b in group["blocks"] for h in heirs_of.get(b, ())}
        return {
            g
            for g, data in new.groups.items()
            if data["kind"] == group["kind"] and held & set(data["blocks"])
        }

    def deleted(ident: str) -> bool:
        group = old.groups.get(ident)
        members = group["blocks"] if group is not None else [ident]
        return not any(heirs_of.get(m) for m in members)

    def target(edge: Mapping[str, Any]) -> str | None:
        return edge["target"].get("block") or edge["target"].get("group")

    dropped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for e in old.edges:
        t = target(e)
        if t is not None and deleted(t) and heirs(e["source"]):
            dropped.setdefault((e["source"], e["kind"]), []).append(e)
    found = []
    for (source, kind), lost in dropped.items():
        before = {
            h
            for e in old.edges
            if e["source"] == source and e["kind"] == kind and (t := target(e))
            for h in heirs(t)
        }
        sources = heirs(source)
        instead = {
            t
            for e in new.edges
            if e["source"] in sources
            and e["kind"] == kind
            and (t := target(e)) is not None
            and t not in before
        }
        if len(instead) >= len(lost):
            continue
        now = new.handle(min(sources, key=new.order))
        for e in lost:
            found.append(
                Item(
                    f"! {old.handle(source)} {old.edge_line(e)}  ({_answer(e)})\n"
                    f"  now: nothing from its source ({now}) in its place",
                    {
                        "change": "look",
                        "why": "its block was deleted",
                        "old": {"edge": e, "source_node": old.node(source)},
                        "now": [],
                    },
                )
            )
    return found


def pending_report(
    old: View,
    payload: Mapping[str, Any],
    new_texts: Mapping[str, str],
    new_handles: Mapping[str, str],
    reopened: Mapping[str, tuple[str, str | None]],
    conditional: Mapping[str, Sequence[str]],
    version: str,
    whole: bool = False,
) -> Report:
    """An edit not yet updated: the paper's files against a kept state.
    Each block identity does not class `same`, by its class, old beside new,
    each with its whole text in the JSON (`payload` the identity answer's
    raw, as `update` makes it), and the owners the rule reopens, outright
    with why and on condition with the conditions, as `update --dry-run`
    counts them. `new_handles` maps
    NEW's block ids to their handles, `reopened` each handle to its why and
    origin."""
    old_texts = {b["id"]: norm(b["text"]) for b in payload["old_blocks"]}
    text = []
    for link in payload["links"]:
        if link["cls"] == "same":
            continue
        handles = [new_handles.get(b, b) for b in link["new"]] or [
            old.handle(b) for b in link["old"]
        ]
        lines = [f"{', '.join(handles)}  {link['cls']}"]
        lines += [
            f"  was: {clipped(old_texts.get(b) or old.texts.get(b, ''), whole)}"
            for b in link["old"]
            if link["cls"] != "moved"
        ]
        lines += [f"  now: {clipped(new_texts[b], whole)}" for b in link["new"]]
        text.append(
            Item(
                "\n".join(lines),
                {
                    "class": link["cls"],
                    "old": [
                        {
                            "block": b,
                            "node": old.node(b),
                            "handle": old.handle(b),
                            "text": old_texts.get(b) or old.texts.get(b, ""),
                        }
                        for b in link["old"]
                    ],
                    "new": [
                        {
                            "block": b,
                            "handle": new_handles.get(b, b),
                            "text": new_texts[b],
                        }
                        for b in link["new"]
                    ],
                },
            )
        )
    report = Report(
        head=[
            f"the paper's files (version {version[:12]}) against state "
            f"{old.state.id} (version {old.state.version[:12]}): an edit not yet "
            "updated"
        ],
        data={"from": old.state.id, "version": version, "pending": True},
    )
    report.sections["text"] = text
    report.sections["reopened"] = [
        Item(
            f"{handle}  {why}" + (f"  (from {origin})" if origin else ""),
            {"handle": handle, "why": why, "from": origin},
        )
        for handle, (why, origin) in reopened.items()
    ]
    report.sections["on_condition"] = [
        Item(
            "\n".join([handle, *(f"  {c}" for c in held)]),
            {"handle": handle, "conditions": list(held)},
        )
        for handle, held in conditional.items()
    ]
    report.sections["corrections_lost"] = corrections_lost(
        old, payload.get("corrections_lost", ())
    )
    report.sections["groups_lost"] = groups_lost(old, payload.get("groups_lost", ()))
    return report


def corrections_lost(old: View, lost: Iterable[Mapping[str, Any]]) -> list[Item]:
    """Each corrected boundary of `old` an update did not carry onto the new
    version, as the identity answer lists it: by the OLD block it names,
    the correction's operation, its words at the boundary and why."""
    return [
        Item(
            f'{old.handle(c["block"])}  its {c["op"]} at "{c["words"]}" not '
            f"carried: {Lapse(c['why']).said}",
            {
                "block": c["block"],
                "node": old.node(c["block"]),
                "handle": old.handle(c["block"]),
                "op": c["op"],
                "words": c["words"],
                "why": c["why"],
                "said": Lapse(c["why"]).said,
            },
        )
        for c in lost
    ]


def groups_lost(old: View, lost: Iterable[Mapping[str, Any]]) -> list[Item]:
    """Each group of `old` an update did not carry onto the new version, as
    the identity answer lists it: by the OLD group and why."""
    return [
        Item(
            f"{old.handle(g['group'])}  not carried: {GroupLapse(g['why']).said}",
            {
                "group": g["group"],
                "node": old.node(g["group"]),
                "handle": old.handle(g["group"]),
                "why": g["why"],
                "said": GroupLapse(g["why"]).said,
            },
        )
        for g in lost
    ]


# --- what is open -------------------------------------------------------------


def finding_piles(view: View) -> list[list[judgments.FindingRead]]:
    """The open, live findings at a state as a person answers them: an exact duplicate
    (the same aspect and anchors, a span or part anchor's quoted words among them; a
    finding with no quoted words is never one) merged under the first filed, and a
    finding answered a duplicate folded under the open one it duplicates; ordered by its
    highest severity, then by how much an edit to its first block reaches
    (`closure.reach`), then by the paper's order and its aspect, so the findings on one
    anchor and aspect stand together, then as filed."""
    readings = view.readings
    piles: dict[tuple[object, ...], list[judgments.FindingRead]] = {}
    for f in readings.findings:
        if not (f.open and f.live):
            continue
        worded = any(
            a.kind in (AnchorKind.SPAN, AnchorKind.PART) for a in f.finding.anchors
        )
        # without quoted words two findings on one anchor may be two
        # problems: each stands alone
        key = (f.finding.aspect, f.finding.anchors) if worded else (f.finding.id,)
        piles.setdefault(key, []).append(f)
    pile_of = {f.finding.id: pile for pile in piles.values() for f in pile}
    latest = {f.finding.id: f.answer for f in readings.findings}
    for f in readings.findings:
        original = judgments.original_of(f.finding.id, latest) if f.live else None
        if original in pile_of:
            pile_of[original].append(f)
    graph = from_export(view.export)
    reached: dict[str | None, int] = {}

    def reach_of(block: str | None) -> int:
        if block not in reached:
            reached[block] = (
                0
                if block is None or block not in view.blocks
                else len(reach(graph, block).reached)
            )
        return reached[block]

    def place(pile: list[judgments.FindingRead]) -> tuple[object, ...]:
        f = pile[0]
        block = readings.block_of(f.nodes[0]) if f.nodes else None
        return (
            -max(g.finding.severity for g in pile),
            -reach_of(block),
            view.order(block),
            f.finding.aspect.value,
            f.order,
        )

    return sorted(piles.values(), key=place)


def lapsed_findings(view: View) -> list[judgments.FindingRead]:
    """The open findings at a state that lapsed: no answer,
    or `postponed`, and not live; by severity, then the paper's order, then
    as filed. A fixed or dismissed one that lapsed is closed already."""
    readings = view.readings

    def place(f: judgments.FindingRead) -> tuple[object, ...]:
        block = readings.block_of(f.nodes[0]) if f.nodes else None
        return (-f.finding.severity, view.order(block), f.order)

    return sorted((f for f in readings.findings if f.open and not f.live), key=place)


def _placed(
    view: View, f: judgments.FindingRead, data: Mapping[str, Any]
) -> tuple[dict[str, Any], str | None]:
    """The anchor a finding's open item names and the block it shows at:
    its first anchor whose node holds at the state, else its first anchor
    on no node (a work, a file, the paper), else its first as filed, at the
    paper; never an anchor on a deleted node beside a living block."""
    on_nodes = [
        a
        for a, filed in zip(data["anchors"], f.finding.anchors, strict=True)
        if filed.node is not None
    ]
    for anchor, node in zip(on_nodes, f.walked.nodes, strict=False):
        if node is not None:
            return anchor, view.readings.block_of(node)
    unnoded = [a for a in data["anchors"] if a["node"] is None]
    if unnoded:
        return unnoded[0], None
    return (data["anchors"][0] if data["anchors"] else _anchor("paper")), None


def _found(data: Mapping[str, Any]) -> dict[str, Any]:
    """What an open item of a finding carries beyond its first anchor: the
    finding, its severity and every anchor, so a reader marks it at each;
    its proposal, where it carries one."""
    return {
        "finding": data["id"],
        "severity": data["severity"],
        "anchors": data["anchors"],
    } | ({"proposal": data["proposal"]} if data["proposal"] else {})


class OpenKind(StrEnum):
    """What the open view lists, in its order."""

    UNSUPPORTED = "unsupported"
    CIRCLE = "circle"
    USES_UNSUPPORTED = "uses-unsupported"
    PARTIAL = "partial"
    FLAG = "flag"
    STALE = "stale"
    CARRIED_UNREAD = "carried-unread"
    PENDING = "pending"
    LAPSED = "lapsed"
    DISPUTED = "disputed"
    NEGATIVE = "negative"
    FINDING = "finding"
    SUGGESTION = "suggestion"
    RETRACTED = "retracted"
    PUBLICATION = "publication"
    UNPLACED = "unplaced"
    UNJUDGED = "unjudged"
    INCONCLUSIVE = "inconclusive"
    PROVISIONAL = "provisional"
    UNCHECKED = "unchecked"
    UNREAD = "unread"
    DANGLING_REFERENCE = "dangling-reference"
    DUPLICATE_LABEL = "duplicate-label"
    UNUSED_PREMISE = "unused-premise"
    UNDECLARED_ASSUMPTION = "undeclared-assumption"
    LATER_RULE = "later-rule"

    @property
    def section(self) -> str:
        return self.value.replace("-", "_")


#: The kinds whose items the gates compute (`check.unsettled`), handed to
#: the open view: what a gate lists that no other kind holds.
GATE_KINDS = frozenset(
    {
        OpenKind.RETRACTED,
        OpenKind.PUBLICATION,
        OpenKind.UNPLACED,
        OpenKind.UNJUDGED,
        OpenKind.INCONCLUSIVE,
        OpenKind.PROVISIONAL,
        OpenKind.UNCHECKED,
        OpenKind.UNREAD,
    }
)


def _anchor(
    kind: str, node: str | None = None, words: str | None = None
) -> dict[str, Any]:
    return {"kind": kind, "node": node, "words": words, "work": None, "file": None}


def partial_cleared(view: View, covered: Covered) -> bool:
    """Whether a claim argued only in part is read through:
    a `justified` one by a held `verified` `valid` of a J at its
    completeness on it, a `self` one by a held `verified` `true`."""
    c = covered.claim
    if c.status is Status.JUSTIFIED:
        wanted = {(Aspect.VALID, Subject(j, c.node, c.words)) for j in covered.by}
    else:
        wanted = {(Aspect.TRUE, Subject(c.node, None, c.words))}
    return any(
        (st.aspect, st.subject) in wanted
        and st.status is judgments.StatusKind.HELD
        and st.held is not None
        and st.held.judgment.verdict is Verdict.VERIFIED
        for st in view.readings.statuses
    )


def _open_item(
    view: View,
    kind: OpenKind,
    line: str,
    anchor: dict[str, Any],
    block: str | None,
    status: str,
    cause: Any = None,
    clears: str = "",
    since: str | None = None,
) -> Item:
    """An item of the open view: its kind, its anchor, its block where it
    shows (None: the paper), its section, its line, status and cause, and
    what clears it."""
    return Item(
        line,
        {
            "kind": kind.value,
            "anchor": anchor,
            "block": block,
            "node": None if block is None else view.node(block),
            "handle": None if block is None else view.handle(block),
            **view.place(block),
            "line": line,
            "status": status,
            "cause": cause,
            "clears": clears,
            "since": since,
        },
    )


#: What settles a subject a person's proposed reading asks again.
PROPOSAL_CLEARS = (
    "the next review of its kind, reading it with your proposal (`axiomation "
    "sync`), or your judgment (`axiomation judge`)"
)
RULING_PROPOSAL_CLEARS = (
    "the next argument review, ruling the claim with your proposal in view "
    "(`axiomation sync`), or your ruling (`axiomation rule`)"
)


def proposal_item(view: View, st: judgments.SubjectStatus) -> Item:
    """A subject a person's proposed reading asks again, as the open view
    lists a negative reading: its block, its status line (the proposal
    on it), and what settles it."""
    assert st.proposal is not None
    b, anchor = _subject_anchor(view, st.subject)
    item = _open_item(
        view,
        OpenKind.NEGATIVE,
        f"{'-' if b is None else view.handle(b)}  {status_line(view, st)}",
        anchor,
        b,
        "proposed",
        cause={
            "judgment": st.proposal.read.judgment.id,
            "author": st.proposal.read.author.name,
        },
        clears=PROPOSAL_CLEARS,
    )
    item.data.update(
        _subject_data(view, st), proposed="reading", proposal=proposal_data(st.proposal)
    )
    return item


def proposed_ruling_item(view: View, ruled: rulings.Ruled) -> Item:
    """A claim a person's proposed ruling asks again, as the inbox lists it
    beside a proposed reading: its block and words, the proposal, and what
    settles it (the next argument review's rule line)."""
    assert ruled.proposal is not None
    r = ruled.proposal.read
    b, anchor = _subject_anchor(view, ruled.subject)
    item = _open_item(
        view,
        OpenKind.NEGATIVE,
        f"{'-' if b is None else view.handle(b)}  "
        f"{part_quote(view.texts.get(b or '', ''), ruled.subject.words or '')}  "
        f"proposed ruling {_kind_words(r.ruling)} by {r.author.name}: "
        f"{r.ruling.reason}"
        + (f"; fix: {r.ruling.fix}" if r.ruling.fix else "")
        + f", {r.ruling.id}",
        anchor,
        b,
        "proposed",
        cause={"ruling": r.ruling.id, "author": r.author.name},
        clears=RULING_PROPOSAL_CLEARS,
    )
    item.data.update(proposed="ruling", proposal=proposed_ruling_data(ruled))
    return item


def _subject_anchor(view: View, on: Subject) -> tuple[str | None, dict[str, Any]]:
    """A subject's block and its anchor: its words where it has some, else
    its block or group."""
    readings = view.readings
    node = on.node
    if on.words:
        return readings.block_of(node), _anchor("part", node, on.words)
    kind = "group" if node in readings.at.members else "block"
    return readings.block_of(node), _anchor(kind, node)


def _subject_data(view: View, st: judgments.SubjectStatus) -> dict[str, Any]:
    """A reading's aspect and, for `valid`, the block of the claim its
    justification is read as arguing: the subject's words are that claim's,
    not the justification's."""
    target = st.subject.target
    block = None if target is None else view.readings.block_of(target)
    return {
        "aspect": st.aspect.value,
        "target": None if block is None else view.handle(block),
    }


def _judged_items(view: View) -> list[Item]:
    """The open view's lapsed and disputed subjects, each with its
    judgments, then each open finding that lapsed."""
    items: list[Item] = []

    def add(kind: OpenKind, *args: Any, **kwargs: Any) -> None:
        items.append(_open_item(view, kind, *args, **kwargs))

    readings = view.readings
    for st in readings.statuses:
        if st.status not in (
            judgments.StatusKind.LAPSED,
            judgments.StatusKind.DISPUTED,
        ):
            continue
        b, anchor = _subject_anchor(view, st.subject)
        lapsed = st.status is judgments.StatusKind.LAPSED
        add(
            OpenKind.LAPSED if lapsed else OpenKind.DISPUTED,
            status_line(view, st),
            anchor,
            b,
            st.status.value,
            cause=cause_data(st.cause)
            if lapsed
            else [r.judgment.id for r in st.reads if r.live]
            + [f.finding.id for f in st.findings],
            clears=status_clears(st) or "",
        )
        items[-1].data["judgments"] = [r.judgment.id for r in st.reads]
        items[-1].data.update(_subject_data(view, st))
    # an open finding that lapsed is answered or reaffirmed, never re-asked:
    # `reask` re-reads judgments
    for f in lapsed_findings(view):
        data = finding_data(view, f)
        anchor, b = _placed(view, f, data)
        add(
            OpenKind.LAPSED,
            finding_line(f),
            anchor,
            b,
            "lapsed",
            cause=data["cause"],
            clears=data["clears"],
        )
        items[-1].data.update(_found(data))
    return items


def suggestion_item(view: View, s: suggestions.SuggestionRead) -> Item:
    """An unanswered suggestion's item, the author's to take or decline:
    `suggestion` where it is live, `lapsed` where its anchors' text
    changed. It sits on its first anchor whose node holds at the state."""
    data = suggestion_data(view, s)
    anchor, b = _anchor("paper"), None
    # the walk carries a node for each anchor on one, in order: a work's none
    on_nodes = [a for a in s.suggestion.anchors if a.node is not None]
    for a, node in zip(on_nodes, s.walked.nodes, strict=False):
        if node is not None:
            kind = "part" if a.words else a.kind.value
            anchor, b = _anchor(kind, node, a.words), view.readings.block_of(node)
            break
    lapsed = not s.live
    cause = s.cause
    item = _open_item(
        view,
        OpenKind.LAPSED if lapsed else OpenKind.SUGGESTION,
        suggestion_line(view, s),
        anchor,
        b,
        "lapsed" if lapsed else "unanswered",
        cause=cause_data(cause) if lapsed and cause is not None else s.suggestion.why,
        clears="the author's answer: `axiomation answer-suggestion "
        f"PAPER {s.suggestion.id} --as taken` (or `--as declined`)",
    )
    item.data["suggestion"] = data
    return item


def finding_item(view: View, pile: Sequence[judgments.FindingRead]) -> Item:
    """An open finding's item, its exact duplicates (`finding_piles`) named
    on it: every anchor, and what clears it and them."""
    f = pile[0]
    data = finding_data(view, f)
    first, b = _placed(view, f, data)
    same = [g.finding.id for g in pile[1:]]
    # a duplicate answered is answered already: the original clears it
    unanswered = [g.finding.id for g in pile[1:] if g.open]
    item = _open_item(
        view,
        OpenKind.FINDING,
        finding_line(f)
        + (
            f"  (the same: {', '.join(same)}, by "
            f"{', '.join(dict.fromkeys(g.author.name for g in pile[1:]))})"
            if same
            else ""
        ),
        first,
        b,
        f"severity {f.finding.severity}",
        cause=f.finding.problem,
        # a finding answered but not cleared (a gate's own rule) has no
        # answer's clearing: its gate says what clears it
        clears=(data["clears"] or "")
        + (f" and {', '.join(unanswered)}" if unanswered else ""),
    )
    item.data.update(_found(data), same=same)
    return item


@dataclass(frozen=True)
class Soundness:
    """What the kept graph got wrong or cannot settle at a state,
    as open items per kind; and why its labels and environments were not
    read, where they could not be (None: read)."""

    items: dict[OpenKind, list[Item]]
    unread: str | None = None


#: The open kinds `soundness.py` lists that read the state's document layer.
DOCUMENT_KINDS = (
    OpenKind.DANGLING_REFERENCE,
    OpenKind.DUPLICATE_LABEL,
    OpenKind.UNUSED_PREMISE,
)


def _unsound_item(
    view: View, kind: OpenKind, found: soundness.Unsound, status: str
) -> Item:
    nodes = [view.node(b) for b in found.blocks]
    anchors = [_anchor("block", n) for n in nodes]
    if found.words:
        worded = "part" if kind is OpenKind.UNDECLARED_ASSUMPTION else "span"
        anchors[0] = _anchor(worded, nodes[0], found.words)
    item = _open_item(
        view,
        kind,
        found.line,
        anchors[0],
        found.blocks[0],
        status,
        cause=found.cause,
        clears=found.clears,
    )
    item.data["anchors"] = anchors
    return item


def soundness_items(view: View) -> Soundness:
    """The four kinds at a view's state: the labels resolved over its kept
    files as today's reader reads them, the premises over its edges, the
    undeclared assumptions over its readings."""
    items: dict[OpenKind, list[Item]] = {k: [] for k in DOCUMENT_KINDS}
    unread = None
    try:
        kept = soundness.Kept(
            versions.document_at(view.store, view.state), view.export, view.names
        )
    except (VersionError, ExchangeError) as error:
        kept = None
        unread = (
            f"the labels and environments of state {view.state.id} were not read: "
            f"{error}"
        )
    if kept is not None:
        items[OpenKind.DANGLING_REFERENCE] = [
            _unsound_item(view, OpenKind.DANGLING_REFERENCE, u, u.cause["why"])
            for u in soundness.dangling_references(kept)
        ]
        items[OpenKind.DUPLICATE_LABEL] = [
            _unsound_item(
                view, OpenKind.DUPLICATE_LABEL, u, f"held by {len(u.blocks)} blocks"
            )
            for u in soundness.duplicate_labels(kept)
        ]
        items[OpenKind.UNUSED_PREMISE] = [
            _unsound_item(view, OpenKind.UNUSED_PREMISE, u, "unused")
            for u in soundness.unused_premises(kept)
        ]
    items[OpenKind.UNDECLARED_ASSUMPTION] = [
        _unsound_item(view, OpenKind.UNDECLARED_ASSUMPTION, u, "undeclared")
        for u in soundness.undeclared_assumptions(view.readings)
    ]
    return Soundness(items, unread)


@dataclass(frozen=True)
class LaterRule:
    """What today's checks refuse in a state's own stage files, as open
    items; and why the files were not read, where they could not be (None:
    read)."""

    items: list[Item]
    unread: str | None = None


#: The stage files a state shows (`axiomation sheet --stage`), in order.
SHEET_LAYERS = (Layer.CUT, Layer.ROLES, Layer.GROUPS, Layer.EDGES)


def later_rule_items(view: View) -> LaterRule:
    """Each problem today's check finds in a stage's file as the state shows
    it: a rule added since the state was kept, which no edit is refused for
    and none is forced to fix (`docs/model.md` § 7)."""
    found, unread = _later_problems(view)
    if unread is not None:
        return LaterRule([], unread)
    return LaterRule([_later_rule_item(view, layer, p) for layer, p in found])


#: The later rule's problems worked out, by their store, their state and
#: its open stale marks: the stage files a kept state shows are its own,
#: with each open mark's line beside its owner, so a filing that leaves the
#: marks as they were leaves the problems too. Today's check is this
#: process's code.
LATER_RULES_HELD = 256
_LATER_RULES: versions.Held[tuple[tuple[tuple[Layer, str], ...], str | None]] = (
    versions.Held(LATER_RULES_HELD)
)


def _later_problems(
    view: View,
) -> tuple[tuple[tuple[Layer, str], ...], str | None]:
    """The later rule's problems at the view's state, each with its stage,
    or why its stage files were not read."""
    marks = tuple((m.handle, m.sheet_line()) for m in view.marks)
    key = (view.store.key, view.state.id, marks)
    held = _LATER_RULES.get(key, ())
    if held is None:
        named = f"later-{view.state.id}-{_digest(json.dumps(marks))}"
        problems = derived.load(named)
        if problems is not None:
            held = (problems, None)
        else:
            held = _later_problems_of(view)
            # kept only where the files were read: a store whose blob is
            # restored reads them on its next command
            if held[1] is None:
                derived.save(named, held[0])
        _LATER_RULES.put(key, (), held, 1)
    return held


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _later_problems_of(
    view: View,
) -> tuple[tuple[tuple[Layer, str], ...], str | None]:
    try:
        with edits.opened(view.store, view.state, marks=view.marks) as kept:
            found = (
                tuple(
                    (layer, p)
                    for layer in SHEET_LAYERS
                    for p in edits.kept_problems(kept, layer)
                ),
                None,
            )
    except (edits.EditError, VersionError, ExchangeError) as error:
        found = (
            (),
            f"the stage files of state {view.state.id} were not read: {error}",
        )
    return found


#: A word of a problem's message that may be a handle: what lies between
#: its spaces, brackets, quotes and punctuation.
_WORD = re.compile(r"[^\s\[\]()'\"·,;]+")


def _later_rule_item(view: View, layer: Layer, problem: str) -> Item:
    """A later rule's problem as the open view shows it: in plain words, on
    the block its edges line names first (a group's first block), else on
    the paper; the check's own message is its cause. Which block is for
    showing only: a message is the sheet's, not a record."""
    block = None
    if layer is Layer.EDGES:
        # the problem is written on the sheet a kept state shows: kept handles
        by_handle = {h: ident for ident, h in view.names.items()}
        for word in _WORD.findall(problem):
            ident = by_handle.get(word.rstrip(":"))
            if ident in view.groups:
                ident = view.groups[ident]["blocks"][0]
            if ident in view.blocks:
                block = ident
                break
    said = "a rule added after this state was kept; nothing is blocked by it"
    return _open_item(
        view,
        OpenKind.LATER_RULE,
        f"{view.handle(block)}: a line of its {layer.value} file breaks {said}"
        if block is not None
        else f"a line of the {layer.value} file breaks {said}",
        _anchor("paper") if block is None else _anchor("block", view.node(block)),
        block,
        "a later rule's",
        cause=problem,
        clears=f"the line rewritten: `axiomation sheet --stage {layer.value}` "
        f"shows it, `axiomation edit --stage {layer.value}` rewrites it",
    )


def unread_line(u: Unread) -> str:
    """An unread ring's line: what carried it, and how many blocks."""
    edit = (
        ""
        if u.edit is None
        else f"{u.edit}'s edit" + (f" (Δ {u.classes})" if u.classes else "") + ", in "
    )
    n = len(u.nodes)
    return (
        f"{edit}update {u.since} carried {n} {'block' if n == 1 else 'blocks'} "
        "unread: moved by the rule, read by no Δ or reading since"
    )


def marks_held_back(view: View) -> dict[str, int]:
    """Each node an open mark sits on, with the claims whose established
    reason is that mark: what reading its lines would free first."""
    found: dict[str, int] = {}
    for e in view.established:
        if e.reason is not None and e.reason.kind is Missing.STALE:
            found[e.reason.node] = found.get(e.reason.node, 0) + 1
    return found


def ranked_marks(view: View, held_back: Mapping[str, int]) -> list[Mark]:
    """The open marks, the gate's by the claims each holds back, the most
    first, then the descriptions, each in the fold's order."""
    order = {id(m): i for i, m in enumerate(view.marks)}
    return sorted(
        view.marks,
        key=lambda m: (not m.gate, -held_back.get(m.node, 0), order[id(m)]),
    )


def open_report(
    view: View,
    kinds: Collection[str] = (),
    sections: Collection[str] = (),
    gated: Mapping[OpenKind, Sequence[Item]] | None = None,
) -> Report:
    """What is open at a state, one section per kind (`OpenKind`): each
    unsupported claim (`docs/model.md` § 8, its support status), each block that
    uses one directly, each claim argued only in part and not read
    through, each flag no correction decided, each open stale mark, what
    each update carried unread, each edit no state applied and each
    question left unanswered, each lapsed
    or disputed subject, each open finding or unanswered suggestion that
    lapsed, each open, live finding, each live suggestion unanswered, and
    what the kept graph got wrong or cannot settle (a dangling
    reference, a duplicate label, an unused premise, an undeclared
    assumption; a note says where the labels could not be read), and each
    problem a rule added since finds in the state's own stage files; and
    what a gate lists that no other kind holds (`GATE_KINDS`), from
    `gated` (`check.unsettled`), none where it is left out. Each item
    names its anchor, its block where it shows
    (None: the paper), its section, its cause and what clears it; a
    finding's item every anchor. Of `kinds` only where any are named; in
    `sections` only where any are named."""
    found: dict[OpenKind, list[Item]] = {k: [] for k in OpenKind}

    def add(kind: OpenKind, *args: Any, **kwargs: Any) -> None:
        found[kind].append(_open_item(view, kind, *args, **kwargs))

    for s, e, support, claim in zip(
        view.standings,
        view.established,
        view.support,
        view.claim_statuses,
        strict=True,
    ):
        # the claims `unsupported-claims` lists: one read negative is a
        # defect, a disputed one unreviewed (`docs/model.md` § 11)
        if support is None or claim.status is not ClaimStatus.UNSUPPORTED:
            continue
        if support.gap is Missing.CIRCULAR:
            # its circle's item lists it
            continue
        b = s["block"]
        line, reading = unsupported_line(view, s, e, support)
        add(
            OpenKind.UNSUPPORTED,
            line,
            _anchor("part", view.node(b), s["claim"]["text"]),
            b,
            "unsupported",
            clears=support_clears(Status(s["status"]), support),
        )
        found[OpenKind.UNSUPPORTED][-1].data["reading"] = reading
        found[OpenKind.UNSUPPORTED][-1].data["weight"] = list(support.weight)
        # a ruling proposed on it waits on a review, as a finding's proposal does
        if (proposal := proposed_ruling_data(support.ruled)) is not None:
            found[OpenKind.UNSUPPORTED][-1].data["proposal"] = proposal
    # the heaviest owed claims first: the author's order of work
    found[OpenKind.UNSUPPORTED] = rulings.heaviest_first(
        found[OpenKind.UNSUPPORTED], lambda i: tuple(i.data["weight"])
    )
    for circle in view.circles:
        first = view.readings.block_of(circle.claims[0].node)
        anchors = circle_anchors(view, circle)
        add(
            OpenKind.CIRCLE,
            circle_text(view, circle),
            anchors[0],
            first,
            "read real"
            if circle.key in view.read_real
            else "circular"
            if circle.held_back
            else "holds nothing back",
            cause=circle_data(view, circle),
            clears=CIRCLE_CLEARS,
        )
        found[OpenKind.CIRCLE][-1].data["anchors"] = anchors
    users = unsupported_users(view)
    for b in view.blocks:
        if b in users:
            claims = [view.handle(c) for c in users[b]]
            add(
                OpenKind.USES_UNSUPPORTED,
                f"{view.handle(b)}  uses unsupported {', '.join(claims)}",
                _anchor("block", view.node(b)),
                b,
                "uses unsupported",
                cause=claims,
                clears="each claim it uses supported, or the use removed",
            )
    for s, covered in zip(view.standings, view.completeness, strict=True):
        if covered is None or covered.completeness is Completeness.GIVEN:
            continue
        if partial_cleared(view, covered):
            continue
        b = s["block"]
        c = covered.claim
        word = covered.completeness.value
        by = [view.readings.at.handle(n) for n in covered.by]
        if covered.unnamed and c.status is Status.JUSTIFIED:
            clears = (
                "the fuller line that argues it naming the claim (its handle "
                "in the line's bracket), or a held verified `valid` of "
                f"{' or '.join(by)} on its claim"
            )
        elif covered.unnamed:
            clears = (
                "the fuller self mark naming the claim (its handle in the "
                "mark's bracket), or a held verified `true` on its claim"
            )
        elif c.status is Status.JUSTIFIED:
            clears = (
                f"a held verified `valid` of {' or '.join(by)} on its claim, or a "
                "fuller justification coming in"
            )
        else:
            clears = "a held verified `true` on its claim, or a fuller self mark"
        words = completeness_words(covered)
        add(
            OpenKind.PARTIAL,
            f"{view.handle(b)}  {view.standing_line(s)} ({words})",
            _anchor("part", c.node, c.words),
            b,
            word,
            cause={"completeness": word, "by": by, "unnamed": covered.unnamed},
            clears=clears,
        )
    for flag in (view.export.get("review") or {}).get("flags", []):
        if flag.get("decided"):
            continue
        held = [x["id"] for x in flag["blocks"] if x["id"] in view.blocks]
        b = held[0] if held else None
        names = " ".join(
            view.handle(x["id"]) if x["id"] in view.names else x["handle"] or x["id"]
            for x in flag["blocks"]
        )
        words = flag["evidence"][0]["text"] if flag["evidence"] else None
        add(
            OpenKind.FLAG,
            f"? {flag['signal']} {names}"
            + (f' · "{words}"' if words else "")
            + (f" — {flag['note']}" if flag.get("note") else ""),
            _anchor("paper")
            if b is None
            else _anchor("span" if words else "block", view.node(b), words),
            b,
            f"{flag['by']}, {flag['stage']}",
            cause=flag.get("note"),
            clears="a correction that holds its blocks, or a person's edit of "
            f"its stage ({flag['stage']})",
        )
    by_node = {node: ident for ident, node in view.nodes.items()}
    held_back = marks_held_back(view)
    for m in ranked_marks(view, held_back):
        b = by_node.get(m.node)
        # a description holds back nothing, whatever its owner's gate marks do
        claims = held_back.get(m.node, 0) if m.gate else 0
        add(
            OpenKind.STALE,
            f"{m.handle}  {m.sheet_line()}"
            + (
                f"  holds back {claims} {'claim' if claims == 1 else 'claims'}"
                if claims
                else ""
            )
            + ("" if m.gate else "  (a description: no gate asks it)"),
            _anchor("block", m.node) if b is not None else _anchor("paper"),
            b,
            "stale",
            cause=[dict(c) for c in m.causes],
            clears=f"an agent's reread (`axiomation reread PAPER {m.handle} "
            f"--ask`), yours once its line is read (`axiomation reread PAPER "
            f"{m.handle} --reason …`), a verified reading made since that holds "
            "the line, an answer that re-reads it (`axiomation edit --stage "
            "edges`), or the next update's session or its reread",
            since=m.since,
        )
        # its owner's node, a group's too, which shows on no one block
        found[OpenKind.STALE][-1].data.update(
            owner=m.node, edge=m.edge, state=m.state.value, holds_back=claims
        )
    by_handle = {handle: ident for ident, handle in view.names.items()}
    for u in view.unread:
        # on the edited block where it stands, else on the paper
        edited = by_handle.get(u.edit or "")
        b = edited if edited in view.blocks else None
        add(
            OpenKind.CARRIED_UNREAD,
            unread_line(u),
            _anchor("paper") if b is None else _anchor("block", view.node(b)),
            b,
            "carried unread",
            cause={
                "edit": u.edit,
                "since": u.since,
                "blocks": [view.handle(by_node[n]) for n in u.nodes if n in by_node],
            },
            clears="a later update that gives these blocks their Δ lines (the "
            "profile's `moved_delta_lines_max` raised past the ring), or a "
            "review's verified reading of a subject a block belongs to "
            "(`axiomation review` asks those resting on them)",
            since=u.since,
        )
    for a in versions.pending_answers(view.store, view.state.version):
        add(
            OpenKind.PENDING,
            f"{a.stage} edit {a.key[:12]} by {a.host}: no state applied it yet",
            _anchor("paper"),
            None,
            "edit not applied",
            cause=a.key,
            clears="its own `axiomation edit` resumed, once what it reopens is "
            "answered",
        )
    for key in (view.export.get("review") or {}).get("unanswered", []):
        add(
            OpenKind.PENDING,
            f"question {key[:12]} left unanswered by the run that made this state",
            _anchor("paper"),
            None,
            "unanswered",
            cause=key,
            clears="the run resumed: the same command again",
        )
    for item in _judged_items(view):
        found[OpenKind(item.data["kind"])].append(item)
    for st in view.readings.statuses:
        read = judgments.negative(st)
        if read is None:
            continue
        b, anchor = _subject_anchor(view, st.subject)
        add(
            OpenKind.NEGATIVE,
            f"{'-' if b is None else view.handle(b)}  {status_line(view, st)}",
            anchor,
            b,
            read.judgment.verdict.value,
            cause={"judgment": read.judgment.id, "author": read.author.name},
            clears=PROPOSAL_CLEARS
            if st.proposal is not None and st.proposal.asks
            else judgments.NEGATIVE_CLEARS,
        )
        found[OpenKind.NEGATIVE][-1].data.update(_subject_data(view, st))
    found[OpenKind.FINDING] = [finding_item(view, pile) for pile in finding_piles(view)]
    # an unanswered suggestion is the author's, never a reading: kept out of
    # `_judged_items`, which `changes` and the gates read
    for s in view.suggestions:
        if s.open:
            item = suggestion_item(view, s)
            found[OpenKind(item.data["kind"])].append(item)
    for kind, items in view.soundness.items.items():
        found[kind] = list(items)
    if not kinds or OpenKind.LATER_RULE.value in kinds:
        found[OpenKind.LATER_RULE] = list(view.later_rule.items)
    for kind, items in (gated or {}).items():
        found[kind] = list(items)

    def kept(item: Item) -> bool:
        if not sections:
            return True
        b = item.data["block"]
        return b is not None and any(_in_section(view.within(b), s) for s in sections)

    shown = {
        k: [i for i in items if kept(i)]
        for k, items in found.items()
        if not kinds or k.value in kinds
    }
    counts = {k.value: len(items) for k, items in shown.items()}
    total = sum(counts.values())
    # where the gates' items are handed, their `unread` item says it
    unread = [None if gated else view.soundness.unread]
    if not kinds or OpenKind.LATER_RULE.value in kinds:
        unread.append(view.later_rule.unread)
    report = Report(
        notes=[n for n in unread if n is not None],
        head=[
            f"{total} open at state {view.state.id}: "
            + ", ".join(f"{n} {k}" for k, n in counts.items() if n)
            if total
            else f"nothing open at state {view.state.id}"
        ],
        data={"state": view.state.id, "counts": counts},
    )
    for k, items in shown.items():
        report.sections[k.section] = items
    return report


# --- the review's report -----------------------------------------------


def _result_of(view: View) -> dict[str, str]:
    """Each block's result: its own where it holds a claim, else the claim's
    block a justifies edge out of it (or out of a group holding it) is
    onto."""
    found = {s["block"]: s["block"] for s in view.standings}
    for e in view.edges:
        if e["kind"] != "justifies":
            continue
        onto = _end(e["target"])
        if onto is None or onto not in view.blocks:
            continue
        for b in view.members(e["source"]):
            found.setdefault(b, onto)
    return found


def review_report(view: View) -> Report:
    """The open findings as a referee report: under
    each result (a block holding a claim), in the paper's order, the piles
    on it and on what justifies it, each as `open` orders them; those on
    nothing a result rests on last, under the paper. Code's rendering:
    nothing is ranked, merged or dismissed but `open`'s exact duplicates."""
    readings = view.readings
    results = _result_of(view)
    claims = {e.claim.node: e for e in view.established}
    by_result: dict[str | None, list[Item]] = {}
    for pile in finding_piles(view):
        f = pile[0]
        block = readings.block_of(f.nodes[0]) if f.nodes else None
        result = results.get(block) if block is not None else None
        same = [g.finding.id for g in pile[1:]]
        by_result.setdefault(result, []).append(
            Item(
                finding_line(f, anchor_words(view, f))
                + (f"  (the same: {', '.join(same)})" if same else ""),
                finding_data(view, f) | {"same": same, "block": block},
            )
        )
    total = sum(len(items) for items in by_result.values())
    report = Report(
        head=[
            f"{total} open findings at state {view.state.id}, on "
            f"{len([r for r in by_result if r is not None])} results"
        ],
        data={"state": view.state.id, "findings": total},
    )
    for result in sorted(by_result, key=view.order):
        if result is None:
            report.sections["elsewhere"] = by_result[result]
            continue
        verdict = claims.get(view.node(result))
        said = (
            ""
            if verdict is None
            else ", established"
            if verdict.established
            else f", not established: {verdict.reason.why}"
            if verdict.reason
            else ""
        )
        report.sections[f"{view.handle(result)} ({view.role_names(result)}{said})"] = (
            by_result[result]
        )
    offered = [s for s in view.suggestions if s.open]
    if offered:
        report.head.append(
            f"{len(offered)} open suggestions, each the author's to take or decline"
        )
        report.data["suggestions"] = len(offered)
        report.sections["suggested citations"] = [
            Item(suggestion_line(view, s), suggestion_data(view, s)) for s in offered
        ]
    return report
