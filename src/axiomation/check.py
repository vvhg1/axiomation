"""Whether a paper passes: the gates over a kept state.

A gate is a policy (`block`, `warn` or `off`) over facts the state already
determines: `established` and its reasons, the subjects' statuses, the open
findings, the stale marks, the flags, the cited works' publications, and
what the kept graph got wrong or cannot settle (`soundness.py`). Each gate
lists what a person acts on, once, never a consequence: a claim not
established is listed where its own reason is a gap (`unsupported-claims`)
or its own route is unread (`unreviewed`), and nowhere where its reason
sits upstream. The paper
passes when no gate fails. `check` files nothing: every item is a pure
function of the kept state, so the open view and the viewer read it as
computed.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import Any

from axiomation import (
    judgments,
    positioning,
    publications,
    records,
    review,
    rulings,
    settings,
    sites,
    versions,
)
from axiomation.claim_status import ClaimStatus, asked_again
from axiomation.established import (
    Circle,
    Completeness,
    Covered,
    Missing,
)
from axiomation.exchange import ExchangeError
from axiomation.judgments import (
    JudgmentError,
    StatusKind,
    SubjectStatus,
)
from axiomation.marks import Cleared, Mark, own_line
from axiomation.queries import (
    CIRCLE_CLEARS,
    GATE_KINDS,
    Item,
    OpenKind,
    Report,
    View,
    cause_data,
    circle_anchors,
    circle_data,
    circle_text,
    completeness_words,
    established_line,
    finding_item,
    open_report,
    part_quote,
    speaks_data,
    status_clears,
    status_line,
    support_clears,
    unresolved_line,
    unsupported_line,
)
from axiomation.questions.review_file import COLUMNS, Session
from axiomation.records import PERSON, Aspect, Status, Subject
from axiomation.versions import VersionError


class Gate(StrEnum):
    """The gates, grouped by the question each answers: does the paper
    argue all it asserts, has anything been found wrong, has everything
    been read, what does it rest on outside itself, and is the graph
    sound."""

    UNSUPPORTED_CLAIMS = "unsupported-claims"
    DEFECTS = "defects"
    REVIEWS = "reviews"
    UNREVIEWED = "unreviewed"
    CITED_PUBLICATIONS = "cited-publications"
    DANGLING_REFERENCES = "dangling-references"
    DUPLICATE_LABELS = "duplicate-labels"
    UNUSED_PREMISES = "unused-premises"
    UNDECLARED_ASSUMPTIONS = "undeclared-assumptions"


class Mode(StrEnum):
    """What a gate's items do: fail the paper, show, or nothing."""

    BLOCK = "block"
    WARN = "warn"
    OFF = "off"


class Outcome(StrEnum):
    """A gate's result: no item, items it warns of, items it fails on, or
    not read."""

    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    OFF = "off"


class Cause(StrEnum):
    """Why an item stands in a gate: the reason a claim is not established
    (`Missing`), a subject's status, or an open kind; an open kind's item
    keeps that kind's word."""

    UNSUPPORTED = "unsupported"
    CIRCULAR = "circular"
    NOT_SHOWN = "not-shown"
    CONTRADICTED = "contradicted"
    FINDING = "finding"
    RETRACTED = "retracted"
    UNJUDGED = "unjudged"
    LAPSED = "lapsed"
    DISPUTED = "disputed"
    INCONCLUSIVE = "inconclusive"
    PROVISIONAL = "provisional"
    UNCHECKED = "unchecked"
    UNPLACED = "unplaced"
    PUBLICATION = "publication"
    STALE = "stale"
    FLAG = "flag"
    DANGLING_REFERENCE = "dangling-reference"
    DUPLICATE_LABEL = "duplicate-label"
    UNUSED_PREMISE = "unused-premise"
    UNDECLARED_ASSUMPTION = "undeclared-assumption"
    UNREAD = "unread"


#: Each gate's mode where the paper's settings name none.
DEFAULTS: Mapping[Gate, Mode] = {
    Gate.UNSUPPORTED_CLAIMS: Mode.BLOCK,
    Gate.DEFECTS: Mode.BLOCK,
    Gate.REVIEWS: Mode.BLOCK,
    Gate.UNREVIEWED: Mode.WARN,
    Gate.CITED_PUBLICATIONS: Mode.WARN,
    Gate.DANGLING_REFERENCES: Mode.BLOCK,
    Gate.DUPLICATE_LABELS: Mode.BLOCK,
    Gate.UNUSED_PREMISES: Mode.WARN,
    Gate.UNDECLARED_ASSUMPTIONS: Mode.WARN,
}
#: The open kind each soundness gate lists.
SOUNDNESS: Mapping[Gate, OpenKind] = {
    Gate.DANGLING_REFERENCES: OpenKind.DANGLING_REFERENCE,
    Gate.DUPLICATE_LABELS: OpenKind.DUPLICATE_LABEL,
    Gate.UNUSED_PREMISES: OpenKind.UNUSED_PREMISE,
    Gate.UNDECLARED_ASSUMPTIONS: OpenKind.UNDECLARED_ASSUMPTION,
}
#: The soundness gates that read the state's document layer.
DOCUMENT_GATES = (
    Gate.DANGLING_REFERENCES,
    Gate.DUPLICATE_LABELS,
    Gate.UNUSED_PREMISES,
)


@dataclass(frozen=True)
class GateResult:
    gate: Gate
    mode: Mode
    outcome: Outcome
    items: tuple[Item, ...]
    #: what a ruling took out of `unsupported-claims`, for the audit
    cleared: tuple[Item, ...] = ()
    #: what the author leaves unargued, out of `unsupported-claims`' count
    left: tuple[Item, ...] = ()
    #: the claims standing unsupported `unsupported-claims` leaves to
    #: `defects` and to `unreviewed`: their status (`claim_status.fold`) is
    #: `defect` or `disputed`
    to_defects: int = 0
    to_unreviewed: int = 0
    #: the works a person accepts resting on, out of `cited-publications`'
    #: count
    accepted: tuple[Item, ...] = ()
    #: the cited works no publication was ever filed for
    unlooked: int = 0
    #: the outside reviewers' findings read at this state, open or addressed
    received: int = 0

    @property
    def shown(self) -> bool:
        """Whether `check`'s text and the viewer show the gate: `reviews`
        only where an outside review was received, since a paper no
        reviewer read has nothing to answer there. `--json` holds every
        gate."""
        return self.gate is not Gate.REVIEWS or self.received > 0


@dataclass(frozen=True)
class Verdict:
    """Each gate's result at a state; the paper passes when none fails."""

    state: str
    results: tuple[GateResult, ...]
    #: the stale marks rereads and readings closed, for one audit read of
    #: the agents'
    cleared: tuple[Cleared, ...] = ()
    #: the open stale mark that holds back the most claims, as `open` lists
    #: it, where one holds back any
    lever: Item | None = None
    #: what the updates carried unread, as `open` lists it: in no gate
    unread: tuple[Item, ...] = ()
    #: the circles of what claims rest on, as `open` lists them
    circles: tuple[Item, ...] = ()
    #: the claims established under a conjecture their statement takes as
    #: a hypothesis, for the audit (`_conditional`)
    conditional: tuple[Item, ...] = ()
    #: what joined copies did apart and differently (`made_apart`): the
    #: paper fails while any stands
    made_apart: tuple[str, ...] = ()

    @property
    def reread(self) -> tuple[Cleared, ...]:
        return tuple(c for c in self.cleared if c.reread is not None)

    @property
    def read(self) -> tuple[Cleared, ...]:
        return tuple(c for c in self.cleared if c.reading is not None)

    @property
    def passes(self) -> bool:
        return not self.made_apart and all(
            r.outcome is not Outcome.FAIL for r in self.results
        )

    def data(self) -> dict[str, Any]:
        """`check --json`: whole, never bounded."""
        return (
            {
                "state": self.state,
                "passes": self.passes,
                **({"made_apart": list(self.made_apart)} if self.made_apart else {}),
                "gates": [
                    {
                        "gate": r.gate.value,
                        "mode": r.mode.value,
                        "outcome": r.outcome.value,
                        "count": len(r.items),
                        "items": [i.data for i in r.items],
                    }
                    | ({"cleared": [i.data for i in r.cleared]} if r.cleared else {})
                    | ({"left": [i.data for i in r.left]} if r.left else {})
                    | (
                        {
                            "accepted": [i.data for i in r.accepted],
                            "unlooked": r.unlooked,
                        }
                        if r.gate is Gate.CITED_PUBLICATIONS
                        else {}
                    )
                    | (
                        {"to_defects": r.to_defects, "to_unreviewed": r.to_unreviewed}
                        if r.gate is Gate.UNSUPPORTED_CLAIMS
                        else {}
                    )
                    | ({"received": r.received} if r.gate is Gate.REVIEWS else {})
                    for r in self.results
                ],
            }
            | (
                {
                    "reread": [
                        {**c.mark.data(), "reread": c.by, "by_person": c.by_person}
                        for c in self.reread
                    ]
                }
                if self.reread
                else {}
            )
            | (
                {
                    "read": [
                        {**c.mark.data(), "judgment": c.by, "by_person": c.by_person}
                        for c in self.read
                    ]
                }
                if self.read
                else {}
            )
            | (
                {"conditional": [i.data for i in self.conditional]}
                if self.conditional
                else {}
            )
        )


# --- settings ------------------------------------------------------------------------


def modes(paper: Path) -> dict[Gate, Mode]:
    """Each gate's mode: the defaults, the workspace's `[gates]` over them,
    the paper's over both; an unknown gate or mode refused, naming the
    known ones."""
    found = dict(DEFAULTS)
    loaded = settings.load(paper)
    own, shared = loaded.tables("gates")
    for is_shared, table in ((True, shared), (False, own)):
        for key, value in table.items():
            where = loaded.file(is_shared)
            try:
                gate = Gate(key)
            except ValueError:
                raise settings.SettingsError(
                    f"{where}: [gates] names no gate {key!r}; the gates: "
                    + ", ".join(g.value for g in Gate)
                ) from None
            try:
                found[gate] = Mode(value)
            except ValueError:
                raise settings.SettingsError(
                    f"{where}: [gates] {key} = {value!r} is no mode; the modes: "
                    + ", ".join(m.value for m in Mode)
                ) from None
    return found


# --- items ---------------------------------------------------------------------------


def _anchor(kind: str, node: str | None = None, words: str | None = None) -> dict:
    return {"kind": kind, "node": node, "words": words, "work": None, "file": None}


def _item(
    view: View,
    gate: Gate,
    cause: Cause,
    line: str,
    block: str | None,
    anchor: dict[str, Any],
    clears: str,
    detail: Any = None,
    anchors: Sequence[dict[str, Any]] = (),
) -> Item:
    """An item in the open view's shape (`queries._open_item`), its kind
    its cause, and its gate."""
    return Item(
        line,
        {
            "gate": gate.value,
            "kind": cause.value,
            "anchor": anchor,
            "anchors": list(anchors) or [anchor],
            "block": block,
            "node": None if block is None else view.node(block),
            "handle": None if block is None else view.handle(block),
            **view.place(block),
            "line": line,
            "status": cause.value,
            "cause": detail,
            "clears": clears,
            "since": None,
        },
    )


def _from_open(gate: Gate, item: Item) -> Item:
    """An open view's item in a gate, its kind kept as its cause."""
    data = dict(item.data)
    data.setdefault("anchors", [data["anchor"]])
    return Item(item.line, {"gate": gate.value, **data})


def _subject_anchor(view: View, on: Subject) -> tuple[str | None, dict[str, Any]]:
    readings = view.readings
    block = readings.block_of(on.node)
    if on.words:
        return block, _anchor("part", on.node, on.words)
    kind = "group" if on.node in readings.at.members else "block"
    return block, _anchor(kind, on.node)


def _subject_item(
    view: View,
    gate: Gate,
    cause: Cause,
    s: SubjectStatus,
    clears: str,
    detail: Any = None,
) -> Item:
    block, anchor = _subject_anchor(view, s.subject)
    handle = "-" if block is None else view.handle(block)
    found = _item(
        view,
        gate,
        cause,
        f"{handle}  {status_line(view, s)}",
        block,
        anchor,
        clears,
        detail,
    )
    on = s.subject
    # the aspect and the argued claim's block, as the open view's items
    # carry them: a `valid` subject's words are that claim's
    target = None if on.target is None else view.readings.block_of(on.target)
    found.data["aspect"] = s.aspect.value
    found.data["target"] = None if target is None else view.handle(target)
    found.data["subject"] = {
        "aspect": s.aspect.value,
        "node": on.node,
        "target": on.target,
        "words": on.words,
        "work": on.work,
        "file": on.file,
    }
    return found


negative = judgments.negative

#: A subject as a gate keys it: its aspect and the subject.
Keyed = tuple[Aspect, Subject]


def lapsed_negatives(
    view: View, reviewed: Sequence[Keyed]
) -> dict[Keyed, judgments.Read]:
    """Each lapsed negative (`judgments.lapsed_negative`) on a subject the
    state still offers (`judgments.offered`, or one a review session
    judges, `reviewed`): one whose subject left the state leaves with it, as
    nothing at the state can read it again."""
    standing = {*judgments.offered(view.readings.at), *reviewed}
    found = {}
    for s in view.readings.statuses:
        if (s.aspect, s.subject) not in standing:
            continue
        read = judgments.lapsed_negative(s)
        if read is not None:
            found[(s.aspect, s.subject)] = read
    return found


def absorbed(
    view: View, lapsed: Mapping[Keyed, judgments.Read]
) -> dict[str, list[Mark]]:
    """The gate's open marks a reading carries, by its judgment's id: a live
    negative, a held `inconclusive` or a lapsed negative whose basis holds
    the mark's line as its subject's own (`marks.own_line`). Such a mark is
    no item of its own: one reading over a marked line is one item."""
    reads = [
        read
        for s in view.readings.statuses
        if (read := negative(s) or _inconclusive(s)) is not None
    ] + list(lapsed.values())
    found: dict[str, list[Mark]] = {}
    at = view.readings.at
    for m in view.gate_marks:
        if m.line is None:
            continue
        read = next((r for r in reads if own_line(r, m.line, at)), None)
        if read is not None:
            found.setdefault(read.judgment.id, []).append(m)
    return found


def _inconclusive(s: SubjectStatus) -> judgments.Read | None:
    held = s.held if s.status is StatusKind.HELD else None
    if held is not None and held.judgment.verdict is records.Verdict.INCONCLUSIVE:
        return held
    return None


def _with_marks(item: Item, carried: Mapping[str, Sequence[Mark]]) -> Item:
    """A reading's item with the marks on its lines under it: "line E
    marked at S"."""
    cause = item.data.get("cause")
    judgment = cause.get("judgment") if isinstance(cause, Mapping) else None
    marks = carried.get(judgment or "", ())
    if not marks:
        return item
    said = "; ".join(f"line {m.edge} marked at {m.since}" for m in marks)
    line = f"{item.line}; {said}"
    marked = [{"edge": m.edge, "since": m.since} for m in marks]
    return Item(line, {**item.data, "line": line, "marked": marked})


def _carried_keys(carried: Mapping[str, Sequence[Mark]]) -> set[tuple[str, str]]:
    """The absorbed marks as `open` names its items: owner and edge."""
    return {(m.node, m.edge) for marks in carried.values() for m in marks}


# --- the gates -----------------------------------------------------------------------


def _ruling_data(ruled: rulings.Ruled) -> dict[str, Any]:
    return {"speaks": speaks_data(ruled), "by_person": ruled.by_person}


@dataclass(frozen=True)
class Unsupported:
    """`unsupported-claims`' read of a state: its items, the `true`
    subjects of the claims it lists, the claims rulings cleared, those the
    author leaves unargued, and how many it left to `defects` and to
    `unreviewed`."""

    items: list[Item]
    listed: set[Subject]
    cleared: list[Item]
    left: list[Item]
    to_defects: int
    to_unreviewed: int


def _unsupported_claims(view: View) -> Unsupported:
    """Each claim whose status is `unsupported` (`docs/model.md` § 11): its
    support status `unsupported` (§ 8), standing `unsupported` with no
    ruling that clears it, whatever its `true`, which says whether it
    holds, never whether the paper owes its argument; or with its own
    reason a gap (a `justified` claim no justifies edge reaches, a
    `restated` one that restates nothing, a circle); with the `true`
    subjects of the claims listed, the claims a ruling cleared (no argument
    owed), those a person leaves unargued, and how many it left to
    `defects` and to `unreviewed`: a claim read negative is its defect, a
    disputed one is a person's to judge first. So `open`'s unsupported
    claims are the items, its circles apart."""
    gate = Gate.UNSUPPORTED_CLAIMS
    items: list[Item] = []
    cleared: list[Item] = []
    left: list[Item] = []
    listed: set[Subject] = set()
    circles = {x.key: x for x in view.circles}
    circled: set[str] = set()
    to_defects = to_unreviewed = 0
    for standing, e, support, folded in zip(
        view.standings,
        view.established,
        view.support,
        view.claim_statuses,
        strict=True,
    ):
        if support is None or support.status is rulings.Support.SUPPORTED:
            continue
        c = e.claim
        on = Subject(c.node, None, c.words)
        block = view.readings.block_of(c.node)
        handle = "-" if block is None else view.handle(block)
        line = f"{handle}  {established_line(view, e)}"
        detail: dict[str, Any] = {
            "standing": c.status.value,
            "why": None if e.reason is None else e.reason.why,
        }
        if support.ruled is not None:
            detail["ruling"] = _ruling_data(support.ruled)
        anchor = _anchor("part", c.node, c.words)
        if support.status is rulings.Support.NO_ARGUMENT_OWED:
            assert support.ruling is not None
            ruling = support.ruling.value
            cleared.append(
                _item(
                    view,
                    gate,
                    Cause.UNSUPPORTED,
                    f"{line}  no argument owed: ruled {ruling}",
                    block,
                    anchor,
                    "",
                    detail,
                )
            )
            continue
        if support.status is rulings.Support.LEFT_UNARGUED:
            left.append(
                _item(
                    view,
                    gate,
                    Cause.UNSUPPORTED,
                    f"{line}  left unargued by the author",
                    block,
                    anchor,
                    "",
                    detail,
                )
            )
            continue
        if folded.status is ClaimStatus.DEFECT:
            # the negative reading or the blocking finding is the defect,
            # and `defects` lists it, whatever the claim's standing; its
            # `true`, unread, is no more `unreviewed`'s than a gap's is
            to_defects += 1
            listed.add(on)
            continue
        if folded.status is ClaimStatus.DISPUTED:
            # `unreviewed` lists the dispute, a person's to judge first
            to_unreviewed += 1
            continue
        listed.add(on)
        if support.gap is Missing.CIRCULAR and e.reason is not None:
            # one item per circle, never one per claim in it, where its
            # first claim the gate lists stands
            key = e.reason.circle
            if key in circles and key not in circled:
                circled.add(key)
                items.append(_circle_item(view, gate, circles[key]))
            continue
        line, reading = unsupported_line(view, standing, e, support)
        items.append(
            _item(
                view,
                gate,
                Cause.UNSUPPORTED,
                line,
                block,
                anchor,
                support_clears(c.status, support),
                detail,
            )
        )
        items[-1].data["reading"] = reading
        items[-1].data["weight"] = list(support.weight)
    # the heaviest owed claims first, a circle's item after them
    items = rulings.heaviest_first(items, lambda i: tuple(i.data.get("weight", (0, 0))))
    return Unsupported(items, listed, cleared, left, to_defects, to_unreviewed)


def _circle_item(view: View, gate: Gate, circle: Circle) -> Item:
    anchors = circle_anchors(view, circle)
    return _item(
        view,
        gate,
        Cause.CIRCULAR,
        circle_text(view, circle),
        view.readings.block_of(circle.claims[0].node),
        anchors[0],
        CIRCLE_CLEARS,
        {"standing": "circle", **circle_data(view, circle)},
        anchors,
    )


def _lapsed(item: Item) -> Item:
    """An item marked as standing on what lapsed, unresolved."""
    item.data["lapsed"] = True
    return item


def _defects(
    view: View,
    opened: Mapping[str, list[Item]],
    lapsed: Mapping[Keyed, judgments.Read],
    carried: Mapping[str, Sequence[Mark]],
) -> list[Item]:
    """Each negative reading, any aspect, live or lapsed and unresolved,
    with the stale marks on its lines under it (`carried`); each open
    severity-3 finding, postponed included, live or lapsed; each retracted
    cited work, on every block citing it. A lapse never takes an item out:
    a reading or an answer does."""
    gate = Gate.DEFECTS
    items = []
    for s in view.readings.statuses:
        read = negative(s)
        if read is not None:
            items.append(
                _subject_item(
                    view,
                    gate,
                    Cause(read.judgment.verdict.value),
                    s,
                    asked_again(s) if s.grown else judgments.NEGATIVE_CLEARS,
                    {"judgment": read.judgment.id, "author": read.author.name},
                )
            )
            continue
        read = lapsed.get((s.aspect, s.subject))
        if read is None:
            continue
        found = _subject_item(
            view,
            gate,
            Cause(read.judgment.verdict.value),
            s,
            f"a reading that concludes (`axiomation reask` {read.judgment.id}, then "
            "a judgment); `axiomation reaffirm` keeps it a defect, live",
            {
                "judgment": read.judgment.id,
                "author": read.author.name,
                "lapsed": cause_data(judgments.first(read.walked.causes)),
            },
        )
        block = found.data["block"]
        handle = "-" if block is None else view.handle(block)
        line = f"{handle}  {unresolved_line(view, s, read)}"
        items.append(_lapsed(Item(line, {**found.data, "line": line})))
    # an outside reviewer's finding stands in `reviews`, at any severity
    theirs = {f.finding.id for f in view.readings.findings if f.author.reviewer}
    items += [
        _from_open(gate, i)
        for i in opened[OpenKind.FINDING.section]
        if i.data["severity"] == 3 and i.data.get("finding") not in theirs
    ]
    for i in opened[OpenKind.LAPSED.section]:
        if i.data.get("severity") == 3 and i.data.get("finding") not in theirs:
            found = _lapsed(_from_open(gate, i))
            found.data["kind"] = Cause.FINDING.value
            items.append(found)
    return [_with_marks(i, carried) for i in items] + _retracted(view, gate)


def _retracted(view: View, gate: Gate) -> list[Item]:
    """Each retracted cited work, on every block citing it."""
    items = []
    readings = view.readings
    for key, work in readings.works.items():
        if not work.retracted:
            continue
        blocks = [
            b
            for node, keys in readings.at.works_of.items()
            if key in keys and (b := readings.block_of(node)) is not None
        ]
        if not blocks:
            # a work the state cites nowhere is nothing a person can fix here
            continue
        blocks.sort(key=view.order)
        anchors = [_anchor("block", view.node(b)) for b in blocks]
        items.append(
            _item(
                view,
                gate,
                Cause.RETRACTED,
                f"{key} is retracted; cited by "
                + ", ".join(view.handle(b) for b in blocks),
                blocks[0],
                anchors[0],
                "each citation of it replaced or removed",
                {"work": key, "doi": work.doi},
                anchors,
            )
        )
    return items


def _reviews(view: View) -> list[Item]:
    """Each outside reviewer's finding not yet addressed, at any severity,
    live or lapsed: a reviewer wants their review answered whole. It
    clears once fixed, dismissed with the author's argument (a note), or a
    duplicate of a finding itself addressed (`judgments.
    reviewer_unaddressed`); a postponed one stands. A finding refiled
    (`reaffirm-finding`) is the reviewer's still: its refiling stands for
    it, whoever refiled it."""
    by_id = {f.finding.id: f for f in view.readings.findings}
    items = []
    for f in view.readings.findings:
        if not f.open or not judgments.reviewer_first(f, by_id).author.reviewer:
            continue
        item = _from_open(Gate.REVIEWS, finding_item(view, [f]))
        item.data["clears"] = (
            f"`axiomation answer-finding … {f.finding.id} --as fixed`, or "
            "`--as dismissed --note` with your argument"
        )
        items.append(item if f.live else _lapsed(item))
    return items


@dataclass(frozen=True)
class Publications:
    """`cited-publications`' items, the works a person accepts resting on,
    and how many cited works no publication was filed for."""

    items: tuple[Item, ...]
    accepted: tuple[Item, ...]
    unlooked: int


#: What clears a cited work the gate lists, by its publication.
_PUBLICATION_CLEARS = {
    records.Publication.PREPRINT: "what the paper takes argued in it, a refereed "
    "work cited for it, or a person's word that it rests on the preprint "
    "(`axiomation sources accept PAPER KEY --why …`)",
    records.Publication.THESIS_OR_REPORT: "what the paper takes argued in it, a "
    "refereed work cited for it, or a person's word that it rests on the work "
    "(`axiomation sources accept PAPER KEY --why …`)",
    records.Publication.ERRATUM: "the erratum read against what the paper takes, "
    "then a person's word that it rests on the work as corrected (`axiomation "
    "sources accept PAPER KEY --why …`)",
}


def _cited_publications(view: View) -> Publications:
    """Each work the state cites whose publication warns (`publications.
    WARNED`), one item on every block citing it, with how many
    established claims rest on it; those resting on the most first. A
    work a person accepts resting on is listed apart, out of the count. A
    work no publication was filed for is counted, never listed."""
    gate = Gate.CITED_PUBLICATIONS
    readings = view.readings
    citing: dict[str, set[str]] = {}
    for node, keys in readings.at.works_of.items():
        block = readings.block_of(node)
        if block is None:
            continue
        for key in keys:
            citing.setdefault(key, set()).add(block)
    resting = Counter(
        key for e in view.established if e.established for key in dict.fromkeys(e.works)
    )
    taken = publications.accepted(view.store)
    items: list[Item] = []
    accepted: list[Item] = []
    unlooked = 0
    for key, cited in citing.items():
        seen = readings.works.get(key)
        if seen is None:
            unlooked += 1
            continue
        if seen.publication not in publications.WARNED:
            continue
        blocks = sorted(cited, key=view.order)
        anchors = [_anchor("block", view.node(b)) for b in blocks]
        n = resting[key]
        line = (
            f"{key} is {publications.WORDS[seen.publication]}"
            + (
                ", no version of record found"
                if seen.publication is records.Publication.PREPRINT
                else ""
            )
            + f"; cited by {', '.join(view.handle(b) for b in blocks)}; "
            + f"{n} established {'claim rests' if n == 1 else 'claims rest'} on it"
        )
        found = _item(
            view,
            gate,
            Cause.PUBLICATION,
            line,
            blocks[0],
            anchors[0],
            _PUBLICATION_CLEARS[seen.publication],
            {
                "work": key,
                "publication": seen.publication.value,
                "source": seen.source,
                "resting": n,
            },
            anchors,
        )
        held = taken.get(key)
        if held is None:
            items.append(found)
            continue
        word, author = held
        found.data["cause"]["accepted"] = {"by": author.name, "reason": word.reason}
        accepted.append(found)

    def rank(i: Item) -> tuple[int, int]:
        return (-i.data["cause"]["resting"], view.order(i.data["block"]))

    return Publications(
        tuple(sorted(items, key=rank)), tuple(sorted(accepted, key=rank)), unlooked
    )


def _review_subjects(view: View) -> tuple[list[tuple[Aspect, Subject]], list[Item]]:
    """The subjects a review session judges at a state, in the paper's
    order: each offered one of an aspect some session judges, each citation
    site (the citationcheck session's placement) and each quotation span
    (`sources verify`'s); and an item for each site no block can hold, or
    for the quotations where the state's document could not be read."""
    gate = Gate.UNREVIEWED
    judged = {a for column in COLUMNS.values() for a in column.judged}
    at = view.readings.at
    found = [
        (aspect, on)
        for aspect, on in judgments.offered(at)
        if aspect in judged and aspect is not Aspect.CITED
    ]
    unplaced: list[Item] = []
    cited, missed = review.citation_sites(view)
    found += [(Aspect.CITED, site.subject) for site in cited]
    unplaced += [
        _item(
            view,
            gate,
            Cause.UNPLACED,
            f"citation of {why}",
            None,
            _anchor("paper"),
            "the citation's words standing once in one block",
        )
        for why in missed
    ]
    try:
        placed, apart = sites.sites(view.store, view.state)
    except (VersionError, ExchangeError) as error:
        unplaced.append(
            _item(
                view,
                gate,
                Cause.UNREAD,
                f"the quotations of state {view.state.id} were not read: {error}",
                None,
                _anchor("paper"),
                "the paper built or updated again under today's reader",
            )
        )
        placed, apart = [], []
    for site in placed:
        if site.kind is not sites.SiteKind.QUOTATION:
            continue
        try:
            on = judgments.subject(
                at,
                Aspect.CITED,
                site.block,
                span=site.words,
                text=view.texts[site.block],
                work=site.key,
            )
        except JudgmentError as error:
            apart.append(
                sites.Unplaced(
                    site.kind, site.key, site.locator, site.file, site.line, str(error)
                )
            )
            continue
        found.append((Aspect.CITED, on))
    unplaced += [
        _item(
            view,
            gate,
            Cause.UNPLACED,
            f"quotation of {u.key} at {u.file}:{u.line}: {u.why}",
            None,
            _anchor("paper"),
            "the quoted words standing once in one block",
        )
        for u in apart
        if u.kind is sites.SiteKind.QUOTATION
    ]
    found = list(dict.fromkeys(found))
    found.sort(key=lambda k: view.order(view.readings.block_of(k[1].node)))
    return found, unplaced


def _clears_unjudged(aspect: Aspect) -> str:
    session = next(
        (s for s, column in COLUMNS.items() if aspect in column.judged),
        Session.ARGUMENT,
    )
    return f"a reading: `axiomation review PAPER --session {session.value}`" + (
        ", or `axiomation sources verify` for a quotation"
        if aspect is Aspect.CITED
        else ""
    )


#: A subject as `_partial` keys it: its aspect, node, target and words.
SubjectKey = tuple[str, str, str | None, str | None]


def _partial(view: View) -> dict[SubjectKey, Covered]:
    """The reading that settles each claim argued only in part: a
    sketch's or an analogy's J's `valid` on it, a `self` claim's `true`,
    by its subject's node, target and words, with its completeness."""
    found: dict[SubjectKey, Covered] = {}
    for covered in view.completeness:
        if covered is None or covered.completeness is Completeness.GIVEN:
            continue
        c = covered.claim
        if c.status is Status.JUSTIFIED:
            for j in covered.by:
                found[(Aspect.VALID.value, j, c.node, c.words)] = covered
        else:
            found[(Aspect.TRUE.value, c.node, None, c.words)] = covered
    return found


def _marked_partial(item: Item, partial: Mapping[SubjectKey, Covered]) -> Item:
    """A subject's item, its line ending in the completeness of the claim
    it reads where that is partial: the J of a sketch read is the sketch
    read."""
    on = item.data.get("subject")
    covered = (
        None
        if on is None
        else partial.get((on["aspect"], on["node"], on["target"], on["words"]))
    )
    if covered is None:
        return item
    return Item(
        f"{item.line} ({completeness_words(covered)})",
        {
            **item.data,
            "completeness": covered.completeness.value,
            "unnamed": covered.unnamed,
        },
    )


def _unreviewed(
    view: View,
    listed: set[Subject],
    opened: Mapping[str, list[Item]],
    reviewed: tuple[list[Keyed], list[Item]],
    lapsed: Mapping[Keyed, judgments.Read],
    carried: Mapping[str, Sequence[Mark]],
) -> list[Item]:
    """What a review has not settled (`_unread_subjects`); each open stale
    mark the gates ask that no reading carries (a description is in no
    gate); each flag no step decided."""
    gate = Gate.UNREVIEWED
    absorbed_marks = _carried_keys(carried)
    return (
        _unread_subjects(view, listed, reviewed, lapsed, carried)
        + [
            _from_open(gate, i)
            for i in opened[OpenKind.STALE.section]
            if i.data["state"] == "gate"
            and (i.data["owner"], i.data["edge"]) not in absorbed_marks
        ]
        + [_from_open(gate, i) for i in opened[OpenKind.FLAG.section]]
    )


def _unread_subjects(
    view: View,
    listed: set[Subject],
    reviewed: tuple[list[Keyed], list[Item]],
    lapsed: Mapping[Keyed, judgments.Read],
    carried: Mapping[str, Sequence[Mark]],
) -> list[Item]:
    """Each subject a session judges that holds no `verified` reading of
    the version of record, by its cause (the `true` of a claim in
    `listed`, or of a `conjectured` one, left out; a negative reading,
    live or lapsed, is a defect); an `inconclusive` reading with the stale
    marks on its lines under it (`carried`); each site no block can hold;
    each claim ruled `positioning` no live contribution check reads, in
    place of its `true`, which is the contribution session's."""
    gate = Gate.UNREVIEWED
    statuses = {(s.aspect, s.subject): s for s in view.readings.statuses}
    positioned = view.positioned
    conjectured = {
        Subject(e.claim.node, None, e.claim.words)
        for e in view.established
        if e.claim.status is Status.CONJECTURED
    }
    subjects, unplaced = reviewed
    partial = _partial(view)
    found: list[Item] = []
    for on in positioning.unchecked(positioned, view.checks):
        block = view.readings.block_of(on.node)
        handle = "-" if block is None else view.handle(block)
        found.append(
            _item(
                view,
                gate,
                Cause.UNCHECKED,
                f"{handle}  positioning · "
                + part_quote(view.texts.get(block or "", ""), on.words or "")
                + "  no contribution check reads it",
                block,
                _anchor("part", on.node, on.words),
                "the contribution session's checked line on it (`review --session "
                "contribution`), or a check by hand (`check-contribution`)",
            )
        )
    for aspect, on in subjects:
        if aspect is Aspect.TRUE and (
            on in listed or on in conjectured or on in positioned
        ):
            continue
        s = statuses.get((aspect, on)) or SubjectStatus(aspect, on, StatusKind.UNJUDGED)
        if (aspect, on) in lapsed:
            continue
        if s.status is StatusKind.UNJUDGED:
            found.append(
                _subject_item(view, gate, Cause.UNJUDGED, s, _clears_unjudged(aspect))
            )
        elif s.status is StatusKind.LAPSED:
            found.append(
                _subject_item(
                    view,
                    gate,
                    Cause.LAPSED,
                    s,
                    status_clears(s) or "",
                    cause_data(s.cause),
                )
            )
        elif s.status is StatusKind.DISPUTED:
            if judgments.to_judge(s):
                found.append(
                    _subject_item(view, gate, Cause.DISPUTED, s, status_clears(s) or "")
                )
        else:
            assert s.held is not None
            verdict = s.held.judgment.verdict
            if verdict is records.Verdict.INCONCLUSIVE:
                found.append(
                    _subject_item(
                        view,
                        gate,
                        Cause.INCONCLUSIVE,
                        s,
                        asked_again(s)
                        if s.grown
                        else "a reading that concludes: what it could not read "
                        "laid in, or a person's judgment",
                        {
                            "judgment": s.held.judgment.id,
                            "unread": [
                                {"key": w.key, "read_status": w.read_status.value}
                                for w in s.held.judgment.unread
                            ],
                            "grown": [{"work": g.work, "why": g.why} for g in s.grown],
                        },
                    )
                )
            elif verdict is records.Verdict.VERIFIED and s.held.provisional:
                found.append(
                    _subject_item(
                        view,
                        gate,
                        Cause.PROVISIONAL,
                        s,
                        "a reading on the version of record's pages",
                        {"judgment": s.held.judgment.id},
                    )
                )
    found = [_with_marks(_marked_partial(i, partial), carried) for i in found]
    return found + unplaced


def _soundness(view: View, gate: Gate) -> list[Item]:
    found = [_from_open(gate, i) for i in view.soundness.items[SOUNDNESS[gate]]]
    if gate in DOCUMENT_GATES:
        found += _document_unread(view, gate)
    return found


def _document_unread(view: View, gate: Gate) -> list[Item]:
    """An item where the state's document could not be read, so the gates
    that read it cannot pass."""
    unread = view.soundness.unread
    if unread is None:
        return []
    return [
        _item(
            view,
            gate,
            Cause.UNREAD,
            unread,
            None,
            _anchor("paper"),
            "the paper built or updated again under today's reader",
        )
    ]


#: The open kinds the gates read: a later rule's problems are in none
#: ("nothing is blocked by it"), and reading them checks every stage file
#: again; the kinds the gates compute themselves are read from them.
GATED = tuple(
    k.value for k in OpenKind if k is not OpenKind.LATER_RULE and k not in GATE_KINDS
)


def unsettled(view: View) -> dict[OpenKind, list[Item]]:
    """What the gates list that no other open kind holds (`GATE_KINDS`),
    by kind, as each gate under its default mode lists it: a retracted
    work, a cited work's publication, a site no block holds, a subject
    no review has settled (unjudged, inconclusive, provisional), a claim
    `positioning` no check reads, what could not be read. The open view
    lists them, so every item a gate lists is open there."""
    reviewed = _review_subjects(view)
    lapsed = lapsed_negatives(view, reviewed[0])
    carried = absorbed(view, lapsed)
    listed = _unsupported_claims(view).listed
    items = (
        _retracted(view, Gate.DEFECTS)
        + list(_cited_publications(view).items)
        + _unread_subjects(view, listed, reviewed, lapsed, carried)
        + _document_unread(view, Gate.DANGLING_REFERENCES)
    )
    found: dict[OpenKind, list[Item]] = {k: [] for k in OpenKind if k in GATE_KINDS}
    for item in items:
        kind = item.data["kind"]
        if kind in found:
            data = {k: v for k, v in item.data.items() if k != "gate"}
            found[OpenKind(kind)].append(Item(item.line, data))
    return found


def gates(view: View, chosen: Mapping[Gate, Mode] = DEFAULTS) -> Verdict:
    """Each gate's items and outcome at the view's state under `chosen`
    (a gate it leaves out takes its default); a gate `off` reads nothing."""
    chosen = {**DEFAULTS, **chosen}
    opened = open_report(view, GATED).sections
    # `unreviewed` leaves out the `true` of the claims `unsupported-claims`
    # lists; an `off` gate lists nothing, so their `true` falls back to it
    reviewed = _review_subjects(view)
    lapsed = lapsed_negatives(view, reviewed[0])
    carried = absorbed(view, lapsed)
    found = _unsupported_claims(view)
    listed = found.listed
    if chosen[Gate.UNSUPPORTED_CLAIMS] is Mode.OFF:
        listed = set()
    cited = (
        _cited_publications(view)
        if chosen[Gate.CITED_PUBLICATIONS] is not Mode.OFF
        else Publications((), (), 0)
    )
    received = sum(f.author.reviewer for f in view.readings.findings)
    results = []
    for gate in Gate:
        mode = chosen[gate]
        if mode is Mode.OFF:
            results.append(
                GateResult(
                    gate,
                    mode,
                    Outcome.OFF,
                    (),
                    received=received if gate is Gate.REVIEWS else 0,
                )
            )
            continue
        if gate is Gate.UNSUPPORTED_CLAIMS:
            items = found.items
        elif gate is Gate.DEFECTS:
            items = _defects(view, opened, lapsed, carried)
        elif gate is Gate.REVIEWS:
            items = _reviews(view)
        elif gate is Gate.UNREVIEWED:
            items = _unreviewed(view, listed, opened, reviewed, lapsed, carried)
        elif gate is Gate.CITED_PUBLICATIONS:
            items = list(cited.items)
        else:
            items = _soundness(view, gate)
        outcome = (
            Outcome.PASS
            if not items
            else Outcome.FAIL
            if mode is Mode.BLOCK
            else Outcome.WARN
        )
        claims = gate is Gate.UNSUPPORTED_CLAIMS
        results.append(
            GateResult(
                gate,
                mode,
                outcome,
                tuple(items),
                tuple(found.cleared) if claims else (),
                tuple(found.left) if claims else (),
                found.to_defects if claims else 0,
                found.to_unreviewed if claims else 0,
                cited.accepted if gate is Gate.CITED_PUBLICATIONS else (),
                cited.unlooked if gate is Gate.CITED_PUBLICATIONS else 0,
                received if gate is Gate.REVIEWS else 0,
            )
        )
    lever = next(
        (
            i
            for i in opened[OpenKind.STALE.section]
            if i.data["state"] == "gate" and i.data["holds_back"]
        ),
        None,
    )
    return Verdict(
        view.state.id,
        tuple(results),
        view.cleared_marks,
        lever,
        tuple(opened[OpenKind.CARRIED_UNREAD.section]),
        tuple(opened[OpenKind.CIRCLE.section]),
        tuple(_conditional(view)),
    )


def _conditional(view: View) -> list[Item]:
    """The claims established under a conjecture their statement takes as
    a hypothesis (an `assumed` edge, `docs/model.md` § 9), in no gate: the
    flag lends support, so the audit names each, its hypotheses, and
    whether a person wrote every flag that declares them (the claim's, and
    those of the claims whose argument holds it) or an agent alone wrote
    some."""
    at = view.readings.at
    found = []
    for e in view.established:
        if not e.established or not e.under:
            continue
        block = view.readings.block_of(e.claim.node)
        quoted = (
            f'"{e.claim.words}"'
            if block is None
            else part_quote(view.texts.get(block, ""), e.claim.words)
        )
        under = [
            {
                "at": at.handle(h.claim.node),
                "claim": h.claim.words,
                "by_person": all(
                    edge["provenance"]["by"] == PERSON for edge in h.edges
                ),
            }
            for h in e.under
        ]
        by_person = all(u["by_person"] for u in under)
        found.append(
            Item(
                f"{at.handle(e.claim.node)}  {quoted} under "
                + ", ".join(f'{u["at"]} "{u["claim"]}"' for u in under)
                + (", by a person" if by_person else ", by an agent alone"),
                {
                    "kind": "conditional",
                    "anchors": [_anchor("part", e.claim.node, e.claim.words)],
                    "claim": e.claim.words,
                    "under": under,
                    "by_person": by_person,
                },
            )
        )
    return found


def verdict(view: View, paper: Path) -> Verdict:
    """The gates at the view's state under the paper's `[gates]`, and what
    joined copies did apart: the command's and the viewer's one path."""
    return replace(gates(view, modes(paper)), made_apart=tuple(made_apart(view.store)))


def made_apart(store: records.RecordStore) -> list[str]:
    """What joined copies did apart and differently, one line each: states
    kept apart, a question answered differently, filings that speak
    differently in one place (`docs/architecture.md` § 4, "Copies
    joined"). None read as the paper's until the copies' work is merged;
    `check` fails while any stands."""
    found = []
    apart = versions.tips(store, waiting=True)
    if len(apart) > 1:
        found.append(versions.apart(apart))
    for key, entries in store.answer_conflicts().items():
        found.append(
            f"question {key[:12]} was answered apart and differently "
            f"({', '.join(e[:12] for e in entries)}): neither answer is read"
        )
    for c in store.filing_conflicts():
        found.append(
            f"{c.kind}s on {c.place} were filed apart and say differently "
            f"({', '.join(e[:12] for e in c.entries)})"
        )
    return found


def _item_key(item: Item) -> str:
    """An item across two states: its kind, anchors and subject (aspect, J
    and T, words, work, file: two subjects on one block share its anchor);
    an item with no subject by its line, which then holds no status words
    (an unplaced or an unread one); an undeclared assumption by its first
    line, the path under it saying how it was reached, not which it is; an
    unsupported claim by its anchors alone, its line saying its rulings and
    its reading, which a ruling or a reading changes."""
    d = item.data
    line = d.get("line")
    if d.get("kind") == Cause.UNSUPPORTED.value:
        line = None
    if d.get("kind") == Cause.PUBLICATION.value:
        # its line counts the claims resting on it, which a reading moves
        line = d["cause"]["work"]
    if d.get("kind") == OpenKind.UNDECLARED_ASSUMPTION.value and line:
        line = line.split("\n")[0]
    return json.dumps(
        [d.get("kind"), d.get("anchors"), d.get("subject") or line], sort_keys=True
    )


def delta(old: Verdict, new: Verdict) -> list[Item]:
    """What an edit did to the gates: for each gate whose items moved, an
    item `<gate> −n +m` with each item gone (`−`) and new (`+`) under it,
    so a count that stays hides nothing."""
    before = {r.gate: {_item_key(i): i for i in r.items} for r in old.results}
    found: list[Item] = []
    for r in new.results:
        held = before.get(r.gate, {})
        now = {_item_key(i): i for i in r.items}
        gone = [i for k, i in held.items() if k not in now]
        added = [i for k, i in now.items() if k not in held]
        if not gone and not added:
            continue
        lines = [
            f"{r.gate.value} −{len(gone)} +{len(added)}",
            *("  − " + i.line.replace("\n", "\n    ") for i in gone),
            *("  + " + i.line.replace("\n", "\n    ") for i in added),
        ]
        found.append(
            Item(
                "\n".join(lines),
                {
                    "gate": r.gate.value,
                    "gone": [i.data for i in gone],
                    "new": [i.data for i in added],
                },
            )
        )
    return found


def report(verdict: Verdict) -> Report:
    """`check`'s text: whether the paper passes and the line of every gate
    it shows (`GateResult.shown`: its mode, outcome and count,
    `unsupported-claims`'s split), together first; then the rulings' and
    rereads' audit lines; then a section per gate shown, its items under
    it."""
    failing = [r.gate.value for r in verdict.results if r.outcome is Outcome.FAIL]
    if verdict.made_apart:
        failing.append("what joined copies made apart (see the notes)")
    head = (
        f"state {verdict.state}: passes"
        if verdict.passes
        else f"state {verdict.state}: fails on {', '.join(failing)}"
    )
    found = Report(head=[head], data={"state": verdict.state, "passes": verdict.passes})
    width = max(len(g.value) for g in Gate)
    for r in verdict.results:
        if not r.shown:
            continue
        line = f"  {r.gate.value:<{width}}  {r.mode.value} → {r.outcome.value}"
        if r.outcome is not Outcome.OFF:
            line += f"  {len(r.items)}"
        if r.gate is Gate.UNSUPPORTED_CLAIMS and r.outcome is not Outcome.OFF:
            line += _split(r)
        if r.gate is Gate.CITED_PUBLICATIONS and r.outcome is not Outcome.OFF:
            line += _publications_split(r)
        found.head.append(line)
    for r in verdict.results:
        if r.cleared:
            alone = sum(not i.data["cause"]["ruling"]["by_person"] for i in r.cleared)
            found.head.append(
                f"{r.gate.value}: {len(r.cleared)} "
                f"{'claim' if len(r.cleared) == 1 else 'claims'} no argument owed, "
                f"by rulings, {alone} of them by agents alone (listed last)"
            )
        if r.left:
            found.head.append(
                f"{r.gate.value}: {len(r.left)} "
                f"{'claim' if len(r.left) == 1 else 'claims'} left unargued by "
                "the author (listed last)"
            )
        if r.accepted:
            found.head.append(
                f"{r.gate.value}: {len(r.accepted)} "
                f"{'work' if len(r.accepted) == 1 else 'works'} the author "
                "accepts resting on (listed last)"
            )
    closers = (("rereads", verdict.reread), ("readings", verdict.read))
    if verdict.reread or verdict.read:
        found.head.append(
            "stale marks: "
            + ", ".join(
                f"{len(held)} cleared by {by}, {sum(not c.by_person for c in held)} "
                "of them by agents alone"
                for by, held in closers
                if held
            )
            + " (`check --json` lists them)"
        )
    if verdict.conditional:
        alone = sum(not i.data["by_person"] for i in verdict.conditional)
        n = len(verdict.conditional)
        found.head.append(
            f"conditional: {n} {'claim' if n == 1 else 'claims'} established under "
            f"a conjecture its statement assumes, {alone} of them by an agent's "
            "flag alone (listed last)"
        )
    found.head += [f"carried unread: {i.line}" for i in verdict.unread]
    if verdict.circles:
        found.head.append(circles_line(verdict.circles))
    if verdict.lever is not None:
        data = verdict.lever.data
        found.head.append(
            f"stale: {data['handle']}'s line `{data['edge']}` holds back "
            f"{data['holds_back']} claims, the most of any open mark "
            f"(`axiomation open --kind stale` ranks them; `axiomation reread "
            f"PAPER {data['handle']} --ask` asks an agent, `--reason …` once "
            "you read it)"
        )
    for r in verdict.results:
        if not r.shown:
            continue
        found.sections[f"{r.gate.value}  {r.mode.value} → {r.outcome.value}"] = list(
            r.items
        )
    for r in verdict.results:
        if r.cleared:
            found.sections[f"{r.gate.value}: no argument owed"] = [
                Item(f"{i.line}, by {_speakers(i)}", i.data) for i in r.cleared
            ]
        if r.left:
            found.sections[f"{r.gate.value}: left unargued"] = [
                Item(f"{i.line}, by {_speakers(i)}", i.data) for i in r.left
            ]
        if r.accepted:
            found.sections[f"{r.gate.value}: accepted"] = [
                Item(
                    f"{i.line}; accepted by {i.data['cause']['accepted']['by']}: "
                    f"{i.data['cause']['accepted']['reason']}",
                    i.data,
                )
                for i in r.accepted
            ]
    if verdict.conditional:
        found.sections["established under a conjecture"] = list(verdict.conditional)
    return found


def _speakers(item: Item) -> str:
    """Who spoke for a claim a ruling took out of the count, and why: the
    audit's read."""
    return "; ".join(
        f"{s['author']}: {s['reason']}" for s in item.data["cause"]["ruling"]["speaks"]
    )


#: Why a claim stands in `unsupported-claims`, by its standing, where its
#: own reason is a gap.
_GAP_WORDS = {
    Status.UNSUPPORTED.value: "standing unsupported",
    Status.JUSTIFIED.value: "justified with no justifies edge onto it",
    Status.RESTATED.value: "restated restating nothing or a conjecture",
}


def circles_line(circles: Sequence[Item]) -> str:
    """The circles counted, as `check`'s head says them: how many, the
    claims in them, those resting on them, and those holding none back."""
    cause = [i.data["cause"] for i in circles]
    inside = sum(len(c["claims"]) for c in cause)
    resting = sum(len(c["held_back"]) for c in cause) - sum(
        sum(k in c["held_back"] for k in c["claims"]) for c in cause
    )
    idle = sum(not c["held_back"] for c in cause)
    real = [c["real"] for c in cause if c["real"] is not None]
    alone = sum(not r["by_person"] for r in real)
    return (
        f"circles: {len(circles)}, {inside} claims in them, {resting} resting on "
        "them"
        + (f", {idle} holding none back" if idle else "")
        + (f", {len(real)} read real, {alone} of them by agents alone" if real else "")
        + " (`open --kind circle` lists them)"
    )


def _publications_split(r: GateResult) -> str:
    """`cited-publications`' count by publication, then the works the
    author accepts and those never looked up, which it does not count."""
    found = Counter(i.data["cause"]["publication"] for i in r.items)
    parts = ", ".join(f"{n} {p}" for p, n in found.most_common())
    apart = [f"{len(r.accepted)} accepted (the author's)"] if r.accepted else []
    if r.unlooked:
        apart.append(
            f"{r.unlooked} cited {'work' if r.unlooked == 1 else 'works'} never "
            "looked up (`axiomation sources publications PAPER`)"
        )
    return (f": {parts}" if parts else "") + (
        f"; not counted: {', '.join(apart)}" if apart else ""
    )


def _split(r: GateResult) -> str:
    """`unsupported-claims`'s count split by each claim's standing, then
    its circles, each one item, and the claims standing unsupported the
    gate does not count: those no argument is owed for, those the author
    leaves unargued, those whose status is `defect` and those `disputed`,
    each listed by its own gate (`docs/model.md` § 11)."""
    circles = [i for i in r.items if i.data["kind"] == Cause.CIRCULAR.value]
    standing = Counter(
        i.data["cause"]["standing"]
        for i in r.items
        if i.data["kind"] != Cause.CIRCULAR.value
    )
    split = [f"{n} {_GAP_WORDS.get(s, s)}" for s, n in standing.most_common()]
    if circles:
        held = sum(len(i.data["cause"]["claims"]) for i in circles)
        split.append(
            f"{len(circles)} {'circle' if len(circles) == 1 else 'circles'} "
            f"({held} claims in them)"
        )
    parts = ", ".join(split)
    return (
        (f": {parts}" if parts else "")
        + f"; not counted: {len(r.cleared)} no argument owed (rulings), "
        + (f"{len(r.left)} left unargued (the author's), " if r.left else "")
        + f"{r.to_defects} defect (in defects)"
        + (f", {r.to_unreviewed} disputed (in unreviewed)" if r.to_unreviewed else "")
    )
