"""Whether a claim is established.

Computed per claim (each standing) over the graph, the judgments and the
open stale marks at one kept state, never stored. A claim is
established by its standing's route:

- `justified`: some J whose `valid` on the claim is held `verified`;
- `self`: a held `verified` `true`, or by its citations: each
  work its block cites holds a held `verified` `cited` judgment on the
  block, and none is retracted;
- `restated`: each claim it restates is established;
- `conjectured`, `unsupported`: a held `verified` `true`, a reviewer's
  dismissal;

and on every route by what it rests on, walked from its block (and the
route's J) along uses edges: each claim an edge relies on is established
too, each code file a walked block uses holds a held `verified`
`computed` on that block, and nothing walked holds a negative
judgment, an open severity-3 finding or an open stale mark the gates ask
(a description is not walked). A definition,
an assumption or a part with no claim is walked through; a qualifies edge
onto a walked node walks its source; a uses edge from a premise onto one
it generalizes is notation, never walked. A block whose lines name the
parts they leave from (placed) is walked from the parts an edge onto it
names, and the parts they use: their lines, and those of the block
whole. An edge onto a claim whose
argument holds its source relies on that claim's hypotheses, never its
result, and asks nothing (`_Graph._walk`, `docs/model.md` § 9). A
`conjectured` claim the statement takes as a hypothesis (an `assumed`
uses edge from its block) is asked by none of the claim's walk, its J's
included, nor by the walk of a claim its argument holds: the claim is
established under it, a conditional result; what rests on the claim asks
it again, unless it takes it as a hypothesis too (`_Graph.hypotheses`).
The assumptions reached are listed, never established, and so are the
cited works its way rests on: each its block cites, where it stands by
its citations, and each whose pages the reading on its route stamped,
along the claims it asks. The least fixed point: circular support
establishes nothing.

A `justified` or `self` claim's completeness is computed
here too, never stored: the fullest of the lines that cover it alone,
`given` over `sketch` over `analogy`, else the least of those that cover
it. A line covers the standings it names by handle, which the export
writes as their words, else every claim it could argue: an
ambiguous line reads toward the alarm, never toward support.
"""

from __future__ import annotations

import hashlib
import itertools
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any

from axiomation.evidence import work_of
from axiomation.judgments import Readings, StatusKind, SubjectStatus, code_file
from axiomation.marks import Mark
from axiomation.questions.edge_sheet import norm
from axiomation.records import (
    AnchorKind,
    Aspect,
    Declared,
    Status,
    Subject,
    Verdict,
)

#: The verdicts that count against what they sit on.
NEGATIVE = (Verdict.NOT_SHOWN, Verdict.CONTRADICTED)
ASSUMPTION = "assumption"
JUSTIFICATION = "justification"


class Missing(StrEnum):
    """Why a claim is not established: the first missing element's kind."""

    UNJUDGED = "unjudged"
    LAPSED = "lapsed"
    DISPUTED = "disputed"
    INCONCLUSIVE = "inconclusive"
    NOT_SHOWN = "not-shown"
    CONTRADICTED = "contradicted"
    FINDING = "finding"
    STALE = "stale"
    UNSUPPORTED = "unsupported"
    CIRCULAR = "circular"
    RETRACTED = "retracted"


@dataclass(frozen=True)
class Claim:
    """A standing: its block's node, its claim's words, its status, and
    what the paper declares of an `unsupported` one's argument."""

    node: str
    words: str
    status: Status
    declared: Declared | None = None


class Completeness(StrEnum):
    """How completely the paper argues a `justified` or `self` claim, from the fullest
    to the least: the argument written out, a sketch, an analogy."""

    GIVEN = "given"
    SKETCH = "sketch"
    ANALOGY = "analogy"


@dataclass(frozen=True)
class Covered:
    """A claim's completeness and the lines that give it: the nodes of the
    J's at that completeness, or the claim's own node for its self marks;
    `unnamed` where a fuller line covers it too, but names no claim of a
    block that holds several, so the least was taken."""

    claim: Claim
    completeness: Completeness
    by: tuple[str, ...]
    unnamed: bool = False


@dataclass(frozen=True)
class Reason:
    """The first missing element: its kind, the node it sits on, in words;
    for `circular`, the key of the circle it runs into (`Circle.key`)."""

    kind: Missing
    node: str
    why: str
    circle: str | None = None


@dataclass(frozen=True)
class Hypothesis:
    """A conjecture a claim's statement takes as a hypothesis, and every
    `assumed` edge of the export that declares it: the claim's own, and
    those of the claims whose argument holds its block."""

    claim: Claim
    edges: tuple[Mapping[str, Any], ...] = field(compare=False, repr=False)


@dataclass(frozen=True)
class Established:
    """A claim's verdict at a state: established where `reason` is None,
    by `via` (the J, for `justified`), under `assumptions` (nodes) and the
    conjectures its statement takes as hypotheses that its way relies on
    (`under`), resting on the cited `works` its way and the claims it asks
    read (keys)."""

    claim: Claim
    reason: Reason | None
    via: str | None = None
    assumptions: tuple[str, ...] = ()
    under: tuple[Hypothesis, ...] = ()
    works: tuple[str, ...] = ()

    @property
    def established(self) -> bool:
        return self.reason is None


class Hop(StrEnum):
    """How a walk takes a step: along an edge of a kind (a qualifies edge
    from its target back to its source, a restating about edge, a J's
    justifies line onto its claim), from a group to its member, or from a
    claim's block to a claim whose argument holds it (`_Graph.context`)."""

    USES = "uses"
    ABOUT = "about"
    QUALIFIES = "qualifies"
    JUSTIFIES = "justifies"
    MEMBER = "member"
    CONTEXT = "context"


@dataclass(frozen=True)
class PathStep:
    """One step of a walk: the node it came from, how, its source and
    target in the graph's direction (a qualifies step comes from its
    target), the edge's first evidence quote, and whether its line names
    no claim of a block that holds several (`fallback`): an ask that may
    mean any of them, or a justifies line that covers each. `edge` is the
    export's edge it took, where it took one; `via`, where a placed block
    gives the line only at a part entered after the first step into
    `came_from`, the step that entered that part. Neither is compared."""

    came_from: str
    hop: Hop
    source: str
    target: str
    quote: str | None = None
    fallback: bool = False
    edge: Mapping[str, Any] | None = field(default=None, compare=False, repr=False)
    via: PathStep | None = field(default=None, compare=False, repr=False)


@dataclass(frozen=True)
class _Walked:
    """What a walk found (`_Graph._walk`): the first thing walked that
    counts against the claim, the claims asked, each with whether only
    fallback asks reach it, the assumptions reached, the nodes visited,
    the step that reached each node visited past the starts, the step
    that asks each claim (a named one where any), every step that
    asks it (`asking`), and the first step relying on each hypothesis,
    which asks nothing (`under`)."""

    reason: Reason | None
    asks: dict[Claim, bool]
    assumptions: tuple[str, ...]
    visited: frozenset[str]
    came: dict[str, PathStep]
    asked: dict[Claim, PathStep]
    asking: dict[Claim, list[PathStep]] = field(default_factory=dict)
    under: dict[Claim, PathStep] = field(default_factory=dict)

    def path(self, node: str) -> tuple[PathStep, ...]:
        """The steps from a start to `node`."""
        return self.path_by(self.came[node]) if node in self.came else ()

    def path_to(self, claim: Claim) -> tuple[PathStep, ...]:
        """The steps from a start to the ask of `claim`, that ask last."""
        return self.path_by(self.asked[claim])

    def path_by(self, step: PathStep) -> tuple[PathStep, ...]:
        """The steps from a start to `step`, that step last: each step's
        predecessor the one that entered its part (`via`), else the first
        into the node it came from."""
        found = [step]
        while (
            prev := step.via if step.via is not None else self.came.get(step.came_from)
        ) is not None:
            found.append(prev)
            step = prev
        return tuple(reversed(found))


def _ask(
    asks: dict[Claim, bool],
    asked: dict[Claim, PathStep],
    claim: Claim,
    fallback: bool,
    step: PathStep,
    asking: dict[Claim, list[PathStep]] | None = None,
) -> None:
    """`claim` asked by `step`: only by fallback asks while every ask of it
    is one, its step a named ask's where any; every step kept in
    `asking`."""
    asks[claim] = asks.get(claim, True) and fallback
    if claim not in asked or (asked[claim].fallback and not fallback):
        asked[claim] = step
    if asking is not None:
        asking.setdefault(claim, []).append(step)


def _quote(edge: Mapping[str, Any]) -> str | None:
    evidence = edge.get("evidence") or []
    return evidence[0].get("text") if evidence else None


@dataclass(frozen=True)
class _Way:
    """One way a claim may be established: what its route and its walk
    found missing that no other claim decides (None: nothing), the claims
    it asks established, its J and the assumptions it reaches; and its
    walk, whose steps ask them (`_Graph.lines`). A hypothesis of a claim
    it asks that it does not take itself is asked too, `through` that
    claim; its walk's hypotheses relied on are `under`; the cited works
    its route reads, `works`."""

    reason: Reason | None
    asks: tuple[Claim, ...]
    via: str | None
    assumptions: tuple[str, ...]
    walked: _Walked | None = field(default=None, compare=False, repr=False)
    through: Mapping[Claim, Claim] = field(
        default_factory=dict, compare=False, repr=False
    )
    under: tuple[Claim, ...] = ()
    works: tuple[str, ...] = ()


def _read(s: SubjectStatus | None) -> tuple[str, ...]:
    """The cited works a held reading stamped pages of, by key: what a
    route by that reading rests on outside the paper."""
    if s is None or s.status is not StatusKind.HELD or s.held is None:
        return ()
    return tuple(dict.fromkeys(work_of(p.key) for p in s.held.judgment.pages))


class _Graph:
    """The state's graph and readings, indexed for the walk."""

    def __init__(self, readings: Readings, marks: Sequence[Mark]) -> None:
        at = readings.at
        self.at = at
        export = at.export
        standings = export.get("standings", [])
        self.claims = [
            Claim(
                n,
                w,
                Status(s["status"]),
                Declared(s["declared"]) if s.get("declared") else None,
            )
            for (n, w), s in zip(at.standings, standings, strict=True)
        ]
        self.claims_of: dict[str, list[Claim]] = {}
        for c in self.claims:
            self.claims_of.setdefault(c.node, []).append(c)
        self.status: dict[tuple[Aspect, Subject], SubjectStatus] = {
            (s.aspect, s.subject): s for s in readings.statuses
        }
        self.on: dict[str, list[SubjectStatus]] = {}
        for s in readings.statuses:
            self.on.setdefault(s.subject.node, []).append(s)
        # a critical finding a person's later reading decided over, on
        # every subject it bears on, counts no more (as in `_status`)
        decided = {
            f.finding.id
            for s in readings.statuses
            for f in s.findings
            if f not in s.counted
        }
        counted = {f.finding.id for s in readings.statuses for f in s.counted}
        self.findings = [
            f
            for f in readings.findings
            if f.blocking and (f.finding.id not in decided or f.finding.id in counted)
        ]
        # a description is no question a gate asks (`docs/model.md` § 5)
        self.marked: dict[str, list[Mark]] = {}
        for m in marks:
            if m.gate:
                self.marked.setdefault(m.node, []).append(m)
        self.out: dict[str, list[Mapping[str, Any]]] = {}
        self.into: dict[str, list[Mapping[str, Any]]] = {}
        for e in at.edges:
            self.out.setdefault(e["source"], []).append(e)
            t = e["target"]
            onto = t.get("block", t.get("group"))
            if onto is not None:
                self.into.setdefault(onto, []).append(e)
        # each node a `generalizes` about edge widens, to the wider
        # premises written onto it (`docs/model.md` § 3)
        self.generalized_by: dict[str, list[str]] = {}
        for e in at.edges:
            if e["kind"] != "about" or not e.get("generalizes"):
                continue
            source, onto = at.node_of.get(e["source"]), self._onto(e)
            if source is not None and onto is not None:
                self.generalized_by.setdefault(onto, []).append(source)
        self.entries = {e["id"]: e for e in export.get("entries", [])}
        # the blocks whose lines name the parts they leave from, and each
        # such part's sibling parts it uses (`docs/model.md` § 9)
        self.placed: set[str] = set()
        self.siblings: dict[str, dict[str, set[str]]] = {}
        for e in at.edges:
            leaving = e.get("source_entries") or []
            if not leaving:
                continue
            self.placed.add(e["source"])
            if self._sibling(e):
                uses = self.siblings.setdefault(e["source"], {})
                for p in leaving:
                    uses.setdefault(p, set()).update(e["target"].get("entries") or [])
        self._hypotheses: dict[Claim, dict[Claim, list[Mapping[str, Any]]]] = {}
        self.works = readings.works
        self.assumptions = {
            b["id"]
            for b in export.get("blocks", [])
            if ASSUMPTION in {s["role"] for s in b.get("segments", [])}
            or ASSUMPTION in b.get("roles", [])
        }
        self.content = {
            b["id"]
            for b in export.get("blocks", [])
            if {"definition", ASSUMPTION}
            & ({s["role"] for s in b.get("segments", [])} | set(b.get("roles", [])))
        }
        justifying = {
            b["id"]
            for b in export.get("blocks", [])
            if JUSTIFICATION in {s["role"] for s in b.get("segments", [])}
            or JUSTIFICATION in b.get("roles", [])
        }
        # a node's context: each block holding claims that an argument
        # holding the node argues, with those edges. An argument holds the
        # members of a group, and a source block that writes reasons (a
        # justification role) or holds no claim: a block of claims alone
        # whose result is the reason is a result, not a step of the
        # argument. And each assumption's dischargers: the sources of the
        # justifies edges onto it.
        self.context: dict[str, dict[str, list[Mapping[str, Any]]]] = {}
        self.dischargers: dict[str, list[str]] = {}
        for e in at.edges:
            onto = self._onto(e)
            if e["kind"] != "justifies" or onto is None:
                continue
            source = at.node_of.get(e["source"])
            if source is None:
                continue
            if self.claims_of.get(onto):
                held = self._holding(source)
                if self.claims_of.get(source) and e["source"] not in justifying:
                    held = held[1:]
                for n in held:
                    self.context.setdefault(n, {}).setdefault(onto, []).append(e)
            elif at.ident_of.get(onto) in self.assumptions:
                self.dischargers.setdefault(onto, []).append(source)

    def _holding(self, node: str) -> list[str]:
        """`node` and every member of it, a group's members' members too."""
        found = [node]
        for x in found:
            found += [m for m in self.at.members.get(x, ()) if m not in found]
        return found

    def handle(self, node: str) -> str:
        return self.at.handle(node)

    def named(self, c: Claim) -> str:
        return f'{self.handle(c.node)} "{c.words}"'

    # --- the route

    def _verdict(
        self,
        s: SubjectStatus | None,
        what: str,
        node: str,
        unjudged: Missing = Missing.UNJUDGED,
        note: str = "",
    ) -> Reason | None:
        if s is None or s.status is StatusKind.UNJUDGED:
            return Reason(unjudged, node, f"{what} is unjudged{note}")
        if s.status is StatusKind.LAPSED:
            cause = s.cause
            why = "" if cause is None else f": {cause.why}"
            return Reason(Missing.LAPSED, node, f"{what} lapsed{why}{note}")
        if s.status is StatusKind.DISPUTED:
            return Reason(Missing.DISPUTED, node, f"{what} is disputed{note}")
        assert s.held is not None
        verdict = s.held.judgment.verdict
        if verdict is Verdict.VERIFIED:
            return None
        return Reason(
            Missing(verdict.value),
            node,
            f"{what} is held {verdict.value}, read by {s.held.author.name}{note}",
        )

    def ways(self, c: Claim) -> list[_Way]:
        if c.status is Status.JUSTIFIED:
            js = dict.fromkeys(self.at.node_of[e["source"]] for e in self.justifying(c))
            if not js:
                reason = Reason(
                    Missing.UNSUPPORTED,
                    c.node,
                    f"{self.named(c)} is justified, but no justifies edge comes in",
                )
                return [_Way(reason, (), None, ())]
            found = []
            for j in js:
                s = self.status.get((Aspect.VALID, Subject(j, c.node, c.words)))
                route = self._verdict(
                    s, f"`valid` of {self.handle(j)} → {self.named(c)}", j
                )
                found.append(self._way(c, [c.node, j], route, j, works=_read(s)))
            return found
        if c.status is Status.RESTATED:
            restated = self.restates(c)
            route = (
                None
                if restated
                else Reason(
                    Missing.UNSUPPORTED,
                    c.node,
                    f"{self.named(c)} is restated, but restates nothing that holds a "
                    "claim"
                    + (
                        ": its block holds several claims, and no about edge of it "
                        "names this one at its source end (`→ about [<claim>] T`)"
                        if self._restated(c)
                        else ""
                    ),
                )
            )
            return [self._way(c, [c.node], route, None, restated)]
        s = self.status.get((Aspect.TRUE, Subject(c.node, None, c.words)))
        route = self._verdict(
            s,
            f"`true` of {self.named(c)}",
            c.node,
            Missing.UNJUDGED if c.status is Status.SELF else Missing.UNSUPPORTED,
            "" if c.declared is None else f" ({c.declared.value} by the paper)",
        )
        found = [self._way(c, [c.node], route, None, works=_read(s))]
        works = self.at.works_of.get(c.node, ())
        if c.status is Status.SELF and works:
            # by its citations first: where neither way holds, the reason a
            # claim that cites names is its citations'
            found.insert(
                0,
                self._way(c, [c.node], self._cited(c, works), None, works=works),
            )
        return found

    def restates(self, c: Claim) -> tuple[Claim, ...]:
        """The claims a `restated` claim restates, as its route reads them:
        none unless an about edge of its block is its own, naming it at its
        source end or, on a block of one claim, naming none there; then
        those every edge it may restate through relies on (`_relied`), never
        itself. An edge of a block of several
        claims that names none at its source end may be another claim's,
        so it never lends this one support, and one saying it restates
        none (`[-]`) restates for none;
        but where the claim has an edge of its own, it is asked as well, as
        it may be this claim's: an ambiguity never drops a requirement."""
        if not self._restated(c, own=True):
            return ()
        return tuple(self._restated(c))

    def _restated(self, c: Claim, own: bool = False) -> dict[Claim, bool]:
        """`restates`, each claim with whether only a fallback reaches it:
        an edge onto several claims naming none, or, unless `own`, an edge
        of a block of several claims naming none at its source end."""
        ident = self.at.ident_of[c.node]
        several = len(self.claims_of.get(c.node, [])) > 1
        restated: dict[Claim, bool] = {}
        for e in self.out.get(ident, []):
            onto = self._onto(e) if e["kind"] == "about" else None
            if onto is None or e.get("restates_none"):
                continue
            restating = self._named(e.get("source_claims") or [], [c])
            if e.get("source_claims") and not restating:
                continue
            unowned = several and not restating
            if own and unowned:
                continue
            relied, fallback = self._relied(e, onto)
            for k in relied:
                restated[k] = restated.get(k, True) and (fallback or unowned)
        restated.pop(c, None)
        return restated

    def restating_step(self, c: Claim, k: Claim) -> PathStep:
        """The step by which `restated` claim `c` asks `k`: its block's first
        about edge relying on `k`."""
        steps = self.restating_steps(c).get(k)
        return steps[0] if steps else PathStep(c.node, Hop.ABOUT, c.node, k.node)

    def restating_steps(self, c: Claim) -> dict[Claim, list[PathStep]]:
        """Each claim `restated` claim `c` may restate (`_restated`), to
        the about edges of its block that rely on it, in the block's order,
        each step a fallback where its target end names no claim of
        several or its source end none of `c`'s block of several."""
        ident = self.at.ident_of[c.node]
        several = len(self.claims_of.get(c.node, [])) > 1
        found: dict[Claim, list[PathStep]] = {}
        for e in self.out.get(ident, []):
            onto = self._onto(e) if e["kind"] == "about" else None
            if onto is None or e.get("restates_none"):
                continue
            restating = self._named(e.get("source_claims") or [], [c])
            if e.get("source_claims") and not restating:
                continue
            relied, fallback = self._relied(e, onto)
            for k in relied:
                if k != c:
                    step = PathStep(
                        c.node,
                        Hop.ABOUT,
                        c.node,
                        onto,
                        _quote(e),
                        fallback or (several and not restating),
                        e,
                    )
                    found.setdefault(k, []).append(step)
        return found

    def _cited(self, c: Claim, works: Sequence[str]) -> Reason | None:
        """The citation route: each work the block cites holds a
        held `verified` `cited` judgment on it, and none is retracted."""
        for key in works:
            seen = self.works.get(key)
            if seen is not None and seen.retracted:
                return Reason(
                    Missing.RETRACTED,
                    c.node,
                    f"{self.named(c)} cites a retracted work: {key}",
                )
        for key in works:
            judged = [
                s
                for s in self.on.get(c.node, [])
                if s.aspect is Aspect.CITED and s.subject.work == key
            ]
            if any(self._verdict(s, "", c.node) is None for s in judged):
                continue
            # the reading closest to holding speaks: held, then disputed,
            # then lapsed
            order = [StatusKind.HELD, StatusKind.DISPUTED, StatusKind.LAPSED]
            judged.sort(key=lambda s: order.index(s.status))
            return self._verdict(
                judged[0] if judged else None,
                f"`cited` of {key} on {self.handle(c.node)}",
                c.node,
            )
        return None

    def _computed(self, node: str, key: str) -> Reason | None:
        """A uses edge from `node` to a code file: it needs a
        held `verified` `computed` on the block, naming the file, as a
        citing claim needs its `cited`."""
        judged = [
            s
            for s in self.on.get(node, [])
            if s.aspect is Aspect.COMPUTED and s.subject.file == key
        ]
        if any(self._verdict(s, "", node) is None for s in judged):
            return None
        order = [StatusKind.HELD, StatusKind.DISPUTED, StatusKind.LAPSED]
        judged.sort(
            key=lambda s: order.index(s.status) if s.status in order else len(order)
        )
        return self._verdict(
            judged[0] if judged else None,
            f"`computed` of file:{key} on {self.handle(node)}",
            node,
        )

    def _way(
        self,
        c: Claim,
        starts: Sequence[str],
        route: Reason | None,
        via: str | None,
        restated: tuple[Claim, ...] = (),
        works: Sequence[str] = (),
    ) -> _Way:
        walked = self._walk(c, starts)
        asks = dict.fromkeys((*restated, *walked.asks))
        own = self.hypotheses(c)
        through: dict[Claim, Claim] = {}
        for a in list(asks):
            for k in self.hypotheses(a):
                if k not in own and k != c and k not in asks:
                    asks[k] = None
                    through[k] = a
        return _Way(
            route or walked.reason,
            tuple(asks),
            via,
            walked.assumptions,
            walked,
            through,
            tuple(walked.under),
            tuple(works),
        )

    def lines(
        self, c: Claim, way: _Way
    ) -> dict[Claim, tuple[tuple[PathStep, ...], ...]]:
        """Each claim `way` of `c` asks, to the paths that ask it: every
        step of its walk asking it, with the walk's path to that step, and
        each about edge of a `restated` claim's block restating it."""
        found: dict[Claim, list[tuple[PathStep, ...]]] = {}
        if way.walked is not None:
            for k, steps in way.walked.asking.items():
                found[k] = [way.walked.path_by(s) for s in steps]
        if c.status is Status.RESTATED and self.restates(c):
            for k, steps in self.restating_steps(c).items():
                found.setdefault(k, []).extend((s,) for s in steps)
        # a hypothesis asked through a claim: by the paths that ask that claim
        for k, a in way.through.items():
            found.setdefault(k, []).extend(found.get(a, []))
        return {k: tuple(dict.fromkeys(v)) for k, v in found.items() if k in way.asks}

    # --- what it rests on

    def _onto(self, edge: Mapping[str, Any]) -> str | None:
        t = edge["target"]
        ident = t.get("block", t.get("group"))
        return None if ident is None else self.at.node_of.get(ident)

    def justifying(self, c: Claim) -> list[Mapping[str, Any]]:
        """The justifies edges whose cover holds `c` (`cover`)."""
        ident = self.at.ident_of[c.node]
        return [
            e
            for e in self.into.get(ident, [])
            if e["kind"] == "justifies"
            and e["source"] in self.at.node_of
            and c in self.cover(e)
        ]

    def cover(self, edge: Mapping[str, Any]) -> tuple[Claim, ...]:
        """The claims a justifies edge argues: the standings it names
        (`_named`) where it names any, else every `justified` claim of its
        target."""
        onto = self._onto(edge)
        held = self.claims_of.get(onto, []) if onto is not None else []
        named = self._named(edge["target"].get("claims") or [], held)
        if named:
            return tuple(named)
        return tuple(k for k in held if k.status is Status.JUSTIFIED)

    def falls_back(self, edge: Mapping[str, Any]) -> bool:
        """Whether a justifies edge names no standing of a target holding
        two or more `justified` claims, so it covers them all."""
        onto = self._onto(edge)
        held = self.claims_of.get(onto, []) if onto is not None else []
        named = self._named(edge["target"].get("claims") or [], held)
        return not named and len(self.cover(edge)) > 1

    def _named(self, claims: Sequence[str], held: Sequence[Claim]) -> list[Claim]:
        """The standings among `held` a line names: the export writes each named
        standing's words from the handle the line named, so this is a lookup of the
        product's own words, never a writer's quote compared with a claim's."""
        words = {norm(w) for w in claims}
        return [k for k in held if norm(k.words) in words]

    def _relied(self, edge: Mapping[str, Any], onto: str) -> tuple[list[Claim], bool]:
        """The claims of `onto` a uses or about edge relies on, and whether
        the edge falls back: the standings it names where it names any; none
        where it names only parts of another role than `claim` (it relies
        on content); else (the block whole, or a `claim` part) every one, a
        fallback where they are two or more."""
        held = self.claims_of.get(onto, [])
        parts = edge["target"].get("entries") or []
        named = self._named(edge["target"].get("claims") or [], held)
        if named:
            return named, False
        if (
            parts
            and not edge["target"].get("claims")
            and all(self.entries.get(p, {}).get("role") != "claim" for p in parts)
        ):
            return [], False
        return list(held), len(held) > 1

    @staticmethod
    def _sibling(edge: Mapping[str, Any]) -> bool:
        """Whether a line runs inside its block, from a part onto another."""
        return (
            edge["kind"] == "uses"
            and edge["target"].get("block") == edge["source"]
            and bool(edge.get("source_entries"))
        )

    def _entered(self, edge: Mapping[str, Any], onto: str) -> frozenset[str] | None:
        """The parts of `onto` an edge relying on its content enters it at:
        those it names, and the parts they use, where `onto` is placed; else
        None, the block whole, as for an edge naming a claim."""
        ident = self.at.ident_of.get(onto)
        parts = edge["target"].get("entries") or []
        if not parts or edge["target"].get("claims") or ident not in self.placed:
            return None
        uses = self.siblings.get(ident, {})
        found = list(dict.fromkeys(parts))
        for p in found:
            found += [q for q in uses.get(p, ()) if q not in found]
        return frozenset(found)

    @staticmethod
    def _gives(edge: Mapping[str, Any], scope: frozenset[str] | None) -> bool:
        """Whether a node walked at the parts `scope` (None: whole) gives
        a uses line out of it: one leaving from the block whole or from a
        part of `scope`; a line inside the block only widens `scope`."""
        if _Graph._sibling(edge):
            return False
        leaving = edge.get("source_entries")
        return scope is None or not leaving or not scope.isdisjoint(leaving)

    @staticmethod
    def _qualified(edge: Mapping[str, Any], scope: frozenset[str] | None) -> bool:
        """Whether a qualifies line onto a node walked at the parts `scope`
        (None: whole) is taken in: one onto the block whole or a part of
        `scope`."""
        onto = edge["target"].get("entries")
        return scope is None or not onto or not scope.isdisjoint(onto)

    def _containing(self, node: str) -> dict[str, str]:
        """Each node holding claims whose argument holds `node`, and theirs
        in turn, to the node it is reached from (`context`)."""
        within: dict[str, str] = {}
        holding = [node]
        for x in holding:
            for t in self.context.get(x, {}):
                if t != node and t not in within:
                    within[t] = x
                    holding.append(t)
        return within

    def assuming(self, c: Claim) -> dict[Claim, list[Mapping[str, Any]]]:
        """The `conjectured` claims `c`'s statement takes as hypotheses, each
        to the edges that declare it: those an `assumed` uses edge from its
        block relies on, naming them (`_relied`, never a fallback: an
        ambiguity lends no support), the edge naming `c` at its source end,
        or naming none on a block of one claim (`docs/model.md` § 9)."""
        ident = self.at.ident_of.get(c.node)
        several = len(self.claims_of.get(c.node, [])) > 1
        found: dict[Claim, list[Mapping[str, Any]]] = {}
        for e in self.out.get(ident or "", []):
            onto = self._onto(e)
            if e["kind"] != "uses" or not e.get("assumed") or onto is None:
                continue
            named = e.get("source_claims") or []
            if (named and not self._named(named, [c])) or (several and not named):
                continue
            relied, fallback = self._relied(e, onto)
            if fallback:
                continue
            for k in relied:
                if k.status is Status.CONJECTURED and k != c:
                    found.setdefault(k, []).append(e)
        return found

    def hypotheses(self, c: Claim) -> dict[Claim, list[Mapping[str, Any]]]:
        """The conjectures `c` is argued under, each to the edges that
        declare it: its statement's (`assuming`) and those of each claim
        whose argument holds its block, a step of that argument taking its
        scope, never through a line naming none of several claims, which
        may argue another (`docs/model.md` § 9)."""
        if c not in self._hypotheses:
            found = {k: list(edges) for k, edges in self.assuming(c).items()}
            for t, x in self._containing(c.node).items():
                for j in self.context.get(x, {}).get(t, ()):
                    if self.falls_back(j):
                        continue
                    for k in self.cover(j):
                        for h, edges in self.assuming(k).items():
                            held = found.setdefault(h, [])
                            held += [e for e in edges if e not in held]
            found.pop(c, None)
            self._hypotheses[c] = found
        return self._hypotheses[c]

    def _walk(
        self, c: Claim, starts: Sequence[str], stop: frozenset[str] = frozenset()
    ) -> _Walked:
        """From `starts` along uses edges, a target holding claims the edge
        relies on asking them and stopping there, any other walked through,
        a group's members and a qualifies edge's source walked too, never
        a node in `stop`.

        A claim's context is walked from its block: each claim whose
        argument holds the block, as a step of that argument, lends it its
        hypotheses, so its statement's block is walked through, never
        asked. An edge from a node onto a claim of its context, or onto
        `c`'s own block, relies on those hypotheses, never on the result:
        the hypotheses of the claim the walk is for, walked from its block
        already, so it asks nothing. A step of another claim's proof
        reused ("as above") thus takes no scope of that claim's. Only a
        claim of that block it names and does not argue (a proof of (b)
        using (a)) is a result it relies on, and asked.

        A uses edge from a premise onto one it generalizes (`generalizes`)
        writes the narrower one's symbols in general form: notation, never
        a premise, so it is not walked through. Were the wider premise to
        rest on the narrower one's premises, each would imply the other.

        A hypothesis of `c` (`hypotheses`) is asked by no edge: the walk
        keeps the first step relying on it (`under`).

        A placed block is walked at the parts an edge relying on its content
        enters (`_entered`): the uses lines leaving from them or from the
        block whole, the qualifies lines onto them or onto the block whole
        (`_gives`, `_qualified`). Entered again at more parts, or whole (a
        start, a member, a context's statement, a qualifier's source, an
        edge naming no part), it widens and gives the lines it did not
        give before."""
        hypotheses = self.hypotheses(c)
        under: dict[Claim, PathStep] = {}

        def ask(k: Claim, fallback: bool, step: PathStep) -> None:
            if k in hypotheses:
                under.setdefault(k, step)
            else:
                _ask(asks, asked, k, fallback, step, asking)

        queue = list(dict.fromkeys(starts))
        seen = set(queue)
        asks: dict[Claim, bool] = {}
        asked: dict[Claim, PathStep] = {}
        asking: dict[Claim, list[PathStep]] = {}
        came: dict[str, PathStep] = {}
        assumptions: dict[str, None] = {}
        reason: Reason | None = None

        # the parts each node is walked at, None for the block whole; the
        # lines each node gave, so a node widened gives each line once
        scope: dict[str, frozenset[str] | None] = dict.fromkeys(queue)
        given: set[tuple[str, int]] = set()
        # each node's first step in at each part (None: whole), in order
        entered: dict[str, dict[str | None, tuple[int, PathStep]]] = {}
        order = itertools.count()

        def visit(
            node: str, step: PathStep, parts: frozenset[str] | None = None
        ) -> None:
            if node in stop:
                return
            held_at = entered.setdefault(node, {})
            for k in [None] if parts is None else sorted(parts):
                if k not in held_at:
                    held_at[k] = (next(order), step)
            if node not in seen:
                seen.add(node)
                queue.append(node)
                came[node] = step
                scope[node] = parts
                return
            held = scope.get(node)
            if held is not None:
                wider = None if parts is None else held | parts
                if wider != held:
                    scope[node] = wider
                    queue.append(node)

        def once(node: str, edge: Mapping[str, Any]) -> bool:
            if (node, id(edge)) in given:
                return False
            given.add((node, id(edge)))
            return True

        def via(node: str, parts: Sequence[str] | None) -> PathStep | None:
            """The step that entered `node` at one of `parts` (None: a line
            of the block whole) or whole, the earliest, where it is not
            the first step into `node`: a line's path runs through it."""
            if not parts:
                return None
            held_at = entered.get(node, {})
            found = [held_at[k] for k in (*parts, None) if k in held_at]
            if not found:
                return None
            step = min(found, key=lambda f: f[0])[1]
            return None if step is came.get(node) else step

        # the claims whose arguments hold c's block, and theirs in turn:
        # their hypotheses are c's, their statements walked as content
        within: dict[str, str] = {}
        holding = [c.node] if c.node in seen else []
        for x in holding:
            for t in self.context.get(x, {}):
                if t != c.node and t not in within:
                    within[t] = x
                    holding.append(t)
                    visit(t, PathStep(x, Hop.CONTEXT, x, t))
        # what each such claim's argument argues there, never a result
        # within its own proof
        argued_within: dict[str, set[Claim]] = {c.node: {c}}
        for x in [c.node, *within]:
            for t, lines in self.context.get(x, {}).items():
                argued_within.setdefault(t, set()).update(
                    k for j in lines for k in self.cover(j)
                )
        for x in queue:
            reason = reason or self._sits_on(c, x)
            ident = self.at.ident_of.get(x)
            if ident is None:
                continue
            if ident in self.assumptions:
                assumptions[x] = None
            for m in self.at.members.get(x, ()):
                visit(m, PathStep(x, Hop.MEMBER, x, m))
            context = self.context.get(x, {})
            # an argument of c relies on c's hypotheses and its containing
            # claims'; another claim's argument, reused, on c's in place of
            # those of the claims it argues
            bound = argued_within.keys() | (() if c.node in context else context)
            parts = scope.get(x)
            for e in self.out.get(ident, []):
                if e["kind"] != "uses" or not self._gives(e, parts) or not once(x, e):
                    continue
                key = e["target"].get("file")
                if key is not None and code_file(self.at, key):
                    reason = reason or self._computed(x, key)
                onto = self._onto(e)
                if onto is None:
                    continue
                relied, fallback = self._relied(e, onto)
                step = PathStep(
                    x,
                    Hop.USES,
                    x,
                    onto,
                    _quote(e),
                    fallback,
                    e,
                    via(x, e.get("source_entries")),
                )
                if onto in bound and relied:
                    # a context edge: the hypotheses are walked already. A
                    # claim it relies on that the argument does not argue
                    # is a result, asked. Naming none of a block of
                    # several, a proof's own edge reads as the hypotheses,
                    # or siblings would ask each other in a circle; a
                    # reused step's asks each, a fallback: which it means
                    # is unknown, and an ambiguity never lends support
                    argued = argued_within.get(onto, set()) | {
                        k for j in context.get(onto, ()) for k in self.cover(j)
                    }
                    own = onto in argued_within
                    for k in relied:
                        if k not in argued and not (fallback and own):
                            ask(k, fallback, step)
                    continue
                for k in relied:
                    ask(k, fallback, step)
                if not relied and x not in self.generalized_by.get(onto, ()):
                    visit(onto, step, self._entered(e, onto))
            for e in self.into.get(ident, []):
                if (
                    e["kind"] == "qualifies"
                    and e["source"] in self.at.node_of
                    and self._qualified(e, parts)
                    and once(x, e)
                ):
                    source = self.at.node_of[e["source"]]
                    if source in stop:
                        continue
                    assumptions[source] = None
                    visit(
                        source,
                        PathStep(
                            x,
                            Hop.QUALIFIES,
                            source,
                            x,
                            _quote(e),
                            False,
                            e,
                            via(x, e["target"].get("entries")),
                        ),
                    )
        return _Walked(
            reason,
            asks,
            tuple(assumptions),
            frozenset(seen),
            came,
            asked,
            asking,
            under,
        )

    def _sits_on(self, c: Claim, x: str) -> Reason | None:
        """What on node `x` counts against claim `c`: a live negative
        reading no person decided over (a held negative judgment, or a
        disputed subject: a second reading that agrees never lifts a doubt),
        an open, live severity-3 finding, an open stale mark. A claim's
        `true` and a part anchor on a claim count only on `c`'s own (a claim
        it rests on is asked, and answers by its own reason; one walked
        past is not rested on), and a `valid` never: it judges a J for its
        T, which `c` rests on only by its route."""
        own = {k.words for k in self.claims_of.get(x, [])} - (
            {c.words} if x == c.node else set()
        )
        for s in self.on.get(x, []):
            if s.aspect is Aspect.VALID or (
                s.aspect is Aspect.TRUE and s.subject != Subject(c.node, None, c.words)
            ):
                continue
            if s.status is StatusKind.DISPUTED:
                return Reason(
                    Missing.DISPUTED,
                    x,
                    f"{s.aspect.value} on {self.handle(x)}"
                    + (f' "{s.subject.words}"' if s.subject.words else "")
                    + " is disputed",
                )
            if s.status is not StatusKind.HELD or s.held is None:
                continue
            verdict = s.held.judgment.verdict
            if verdict not in NEGATIVE:
                continue
            return Reason(
                Missing(verdict.value),
                x,
                f"{s.aspect.value} on {self.handle(x)}"
                + (f' "{s.subject.words}"' if s.subject.words else "")
                + f" is held {verdict.value}, read by {s.held.author.name}",
            )
        for f in self.findings:
            anchored = [
                a
                for a, n in zip(
                    [a for a in f.finding.anchors if a.node is not None],
                    f.walked.nodes,
                    strict=False,
                )
                if n == x
            ]
            if not anchored:
                continue
            if all(a.kind is AnchorKind.PART and a.words in own for a in anchored):
                continue
            return Reason(
                Missing.FINDING,
                x,
                f"finding {f.finding.id} (severity 3, {f.finding.aspect.value}) on "
                f'{self.handle(x)}: "{f.finding.problem}"',
            )
        held = self.marked.get(x)
        if held:
            mark, more = held[0], len(held) - 1
            whose = "its" if x == c.node else f"{self.handle(x)}'s"
            line = f" (`{mark.edge}`)" if mark.edge else f": {mark.why}"
            return Reason(
                Missing.STALE,
                x,
                f"{whose} line is unread since {mark.since}{line}"
                + (f", and {more} more" if more else ""),
            )
        return None


def restating_nothing(readings: Readings) -> frozenset[tuple[str, str]]:
    """The `restated` claims, by node and words, whose route restates no
    claim (`_Graph.restates`): no about line of theirs names them where
    their block holds several, or points at a claim."""
    graph = _Graph(readings, ())
    return frozenset(
        (c.node, c.words)
        for c in graph.claims
        if c.status is Status.RESTATED and not graph.restates(c)
    )


def restating_conjectures(
    readings: Readings,
) -> dict[tuple[str, str], tuple[tuple[Claim, bool], ...]]:
    """The `restated` claims, by node and words, that restate a
    `conjectured` claim, to each such claim and whether only a fallback
    reaches it (`_Graph._restated`): a restatement of a conjecture asserts
    what the paper only conjectures, a gap in its support (`docs/model.md`
    § 8), whatever its route reads."""
    graph = _Graph(readings, ())
    found = {}
    for c in graph.claims:
        if c.status is not Status.RESTATED or not graph.restates(c):
            continue
        conjectures = tuple(
            (k, fallback)
            for k, fallback in graph._restated(c).items()
            if k.status is Status.CONJECTURED
        )
        if conjectures:
            found[(c.node, c.words)] = conjectures
    return found


def relied_on(readings: Readings) -> dict[tuple[str, str], tuple[str, ...]]:
    """Each claim something rests on in one hop, by its node and words: the
    nodes whose uses edges rely on it (`_Graph._relied`, a fallback ask
    included), those its block qualifies, whose walk takes its block in
    (`_Graph._walk`), and those its block justifies: a claim of a proof is
    one of its steps."""
    graph = _Graph(readings, ())
    found: dict[tuple[str, str], dict[str, None]] = {}
    for e in readings.at.edges:
        onto = graph._onto(e)
        source = graph.at.node_of.get(e["source"])
        if onto is None or source is None or source == onto:
            continue
        if e["kind"] == "uses":
            for c in graph._relied(e, onto)[0]:
                found.setdefault((c.node, c.words), {})[source] = None
        elif e["kind"] in ("qualifies", "justifies"):
            for c in graph.claims_of.get(source, []):
                found.setdefault((c.node, c.words), {})[onto] = None
    return {k: tuple(v) for k, v in found.items()}


class UndeclaredCause(StrEnum):
    """Why an undeclared premise is listed, the first that holds: the
    statement has no uses edge; a line naming no claim of a block that
    holds several made it; a justifies edge discharges it from outside
    the J; it uses an assumption the statement declares, or the J reaches
    it through a case of one (`generalizes`); else the statement leaves
    it out."""

    MISSING_EDGES = "missing-edges"
    UNNAMED_LINE = "unnamed-line"
    DISCHARGED_ELSEWHERE = "discharged-elsewhere"
    NARROWS = "narrows"
    LEFT_OUT = "left-out"


class Unnamed(StrEnum):
    """Where a line naming no claim of a block that holds several made an
    item: the statement's ask, which declares nothing through it; the J's
    line onto its claim, which may argue another; or an ask on the J's
    side, which rests on each claim's premises."""

    STATEMENT = "statement"
    LINE = "line"
    ASK = "ask"


@dataclass(frozen=True)
class Undeclared:
    """A premise a justified claim's J rests on that the claim's statement
    does not: an assumption's node, or a `conjectured` claim's
    (`conjecture` set); `through` the claim the J asks whose statement
    names it, where the J does not reach it itself. Its cause, the walk's
    path from the J (or the claim's block) to it, the lines naming no
    claim that made it (`Unnamed`), the assumption the statement declares
    that it uses (`narrows`) or that generalizes the node `case`, the
    premise or one on its path, and the nodes whose justifies edges onto
    it lie outside the J (`discharged_by`)."""

    claim: Claim
    via: str
    premise: str
    conjecture: Claim | None = None
    through: Claim | None = None
    cause: UndeclaredCause = UndeclaredCause.LEFT_OUT
    path: tuple[PathStep, ...] = ()
    unnamed: tuple[tuple[Unnamed, PathStep], ...] = ()
    narrows: str | None = None
    discharged_by: tuple[str, ...] = ()
    case: str | None = None


@dataclass(frozen=True)
class _Rest:
    """What a walk rests on, of one premise: its conjecture, the claim
    asked through which it is reached, and the steps to it."""

    conjecture: Claim | None
    through: Claim | None
    path: tuple[PathStep, ...]


#: What a walk rests on: each premise (an assumption's node, or a
#: `conjectured` claim) → how it is reached.
_Rests = dict[str | Claim, _Rest]


def undeclared(readings: Readings) -> tuple[Undeclared, ...]:
    """Per justified claim and each J, in the export's order of standings:
    each premise `[T, J]` rests on that `[T]` does not. What a walk rests
    on is the assumptions it reaches and the `conjectured` claims it asks,
    and through each claim it asks, what that claim's statement rests on,
    its own J left out: a premise a lemma's proof uses but its statement
    leaves out is the lemma's item, not the theorem's. A `restated`
    claim's statement rests on what it restates. A claim whose
    statement uses the conjecture is a conditional result, and lists
    nothing for it; one taking it as a hypothesis (`_Graph.hypotheses`)
    rests on it all the same, so a claim whose J asks it rests on it too.
    Assumptions joined by about edges, each from one
    assumption onto another, restate one premise (`_originals`): a statement
    that declares a copy declares what it restates. A statement that
    declares a case of a wider premise, by a `generalizes` edge onto a
    premise or a node its walk visits, declares the wider one, never what
    the wider one's walk reaches (`declared`).

    A premise discharged for `[T, J]`, by a justifies edge onto it whose
    source J holds (J, or a member of it), is no premise of it: that walk,
    and the walks of the statements it reaches, stop there, so what only
    it rests on is no premise either (`docs/model.md` § 3).

    A fallback ask (an edge onto two or more claims naming none, `_relied`)
    counts for what a walk rests on and never for what a statement
    declares: which claim it means is unknown, and a missing reference
    must never hide an item. So the two sides read each
    statement apart, and a statement rests on what every claim it reaches
    states, a circle's members each on the circle's whole."""
    graph = _Graph(readings, ())
    walked: dict[tuple[Claim, frozenset[str]], _Walked] = {}

    def statement_walk(c: Claim, stop: frozenset[str]) -> _Walked:
        """`c`'s statement walked: the assumptions it reaches and the
        claims it asks (a `restated` claim's, those it restates too)."""
        if (c, stop) not in walked:
            w = graph._walk(c, [c.node], stop)
            if c.status is Status.RESTATED:
                for k, fallback in graph._restated(c).items():
                    step = replace(graph.restating_step(c, k), fallback=fallback)
                    _ask(w.asks, w.asked, k, fallback, step)
            walked[c, stop] = w
        return walked[c, stop]

    def premises(w: _Walked, asks: Sequence[Claim]) -> _Rests:
        found: _Rests = {a: _Rest(None, None, w.path(a)) for a in w.assumptions}
        for k in asks:
            if k.status is Status.CONJECTURED:
                found.setdefault(k, _Rest(k, None, w.path_to(k)))
        # a hypothesis is a premise the walk rests on, asked or not
        for k, step in w.under.items():
            found.setdefault(k, _Rest(k, None, w.path_by(step)))
        return found

    stated: dict[tuple[Claim, bool, frozenset[str]], _Rests] = {}
    visits: dict[tuple[Claim, bool, frozenset[str]], frozenset[str]] = {}

    def statement(
        c: Claim, declaring: bool, stop: frozenset[str] = frozenset()
    ) -> _Rests:
        """What `c`'s statement rests on: the premises of every statement
        reached from it, itself first, each with the steps from `c`'s
        block; declaring, through named asks only."""
        if (c, declaring, stop) not in stated:
            found: _Rests = {}
            seen: set[str] = set()
            reach: dict[Claim, tuple[PathStep, ...]] = {c: ()}
            queue = [c]
            for x in queue:
                w = statement_walk(x, stop)
                seen |= w.visited
                asked = [k for k, fb in w.asks.items() if not (declaring and fb)]
                for premise, r in premises(w, asked).items():
                    found.setdefault(
                        premise, _Rest(r.conjecture, r.through, reach[x] + r.path)
                    )
                for k in asked:
                    if k not in reach:
                        reach[k] = reach[x] + w.path_to(k)
                        queue.append(k)
            stated[c, declaring, stop] = found
            visits[c, declaring, stop] = frozenset(seen)
        return stated[c, declaring, stop]

    def discharged(j: str) -> frozenset[str]:
        """The premises a block J holds discharges (`docs/model.md` § 3)."""
        held = set(graph._holding(j))
        return frozenset(
            p for p, by in graph.dischargers.items() if held.intersection(by)
        )

    contents = _originals(graph)

    def originals(premise: str | Claim) -> frozenset[str | Claim]:
        if isinstance(premise, Claim):
            return frozenset({premise})
        return contents.get(premise, frozenset({premise}))

    def declared(c: Claim) -> frozenset[str | Claim]:
        """What `c`'s statement declares: the originals of what it rests
        on and, where a premise it declares or a node its walks visit is
        a case a `generalizes` edge widens, the wider premise's originals,
        and what generalizes those in turn (`docs/model.md` § 10). Never
        the wider premise's walk: the edge says the case implies the wider
        premise, nothing of what that one's text writes, so each premise
        declared so is one edge, judged by its `accurate`. Declaring the
        wider declares nothing narrower: its uses edge onto the case is
        not walked (`_Graph._walk`)."""
        found: set[str | Claim] = set()
        queue: list[str] = []
        queued: set[str] = set()

        def take(premises: Iterable[str | Claim], seen: Iterable[str]) -> None:
            for p in premises:
                found.update(originals(p))
            for n in [*seen, *(p for p in found if isinstance(p, str))]:
                if n not in queued:
                    queued.add(n)
                    queue.append(n)

        take(statement(c, declaring=True), visits[c, True, frozenset()])
        for x in queue:
            for g in graph.generalized_by.get(x, ()):
                take([g], ())
        return frozenset(found)

    def narrows(premise: str, own: frozenset[str | Claim]) -> str | None:
        """The first assumption the statement declares that `premise` uses."""
        for e in graph.out.get(graph.at.ident_of[premise], []):
            onto = graph._onto(e)
            if (
                e["kind"] == "uses"
                and onto is not None
                and graph.at.ident_of.get(onto) in graph.assumptions
                and originals(onto) <= own
            ):
                return onto
        return None

    def widens(
        premise: str, path: Sequence[PathStep], own: frozenset[str | Claim]
    ) -> tuple[str, str] | None:
        """A premise the statement declares that generalizes `premise` or
        a node on the path to it, nearest the premise first, and that
        case: the J rests on a narrower premise than the one declared."""
        nodes = [premise, *(n for s in reversed(path) for n in (s.target, s.source))]
        for n in dict.fromkeys(nodes):
            for g in graph.generalized_by.get(n, ()):
                if originals(g) <= own:
                    return g, n
        return None

    def first_fallback(path: Sequence[PathStep]) -> PathStep | None:
        return next((s for s in path if s.fallback), None)

    found: list[Undeclared] = []
    for c in graph.claims:
        if c.status is not Status.JUSTIFIED:
            continue
        own = declared(c)
        reached = statement(c, declaring=False)
        # a statement with no edge to declare by: no uses edge out, no
        # qualifies edge in, no claim whose argument holds it
        ident = graph.at.ident_of[c.node]
        bare = (
            not any(
                e["kind"] == "uses" and not graph._sibling(e)
                for e in graph.out.get(ident, [])
            )
            and not any(e["kind"] == "qualifies" for e in graph.into.get(ident, []))
            and not set(graph.context.get(c.node, {})) - {c.node}
        )
        lines: dict[str, PathStep] = {}
        for e in graph.justifying(c):
            j = graph.at.node_of[e["source"]]
            line = PathStep(j, Hop.JUSTIFIES, j, c.node, _quote(e), graph.falls_back(e))
            if j not in lines or not line.fallback:
                lines[j] = line
        for j, line in lines.items():
            stop = discharged(j)
            w = graph._walk(c, [c.node, j], stop)
            rests = premises(w, list(w.asks))
            for k in w.asks:
                head = w.path_to(k)
                for premise, r in statement(k, False, stop).items():
                    rests.setdefault(premise, _Rest(r.conjecture, k, head + r.path))
            for premise, r in rests.items():
                if originals(premise) <= own:
                    continue
                node = premise.node if isinstance(premise, Claim) else premise
                unnamed: list[tuple[Unnamed, PathStep]] = []
                if line.fallback:
                    unnamed.append((Unnamed.LINE, line))
                stating = (
                    first_fallback(reached[premise].path)
                    if premise in reached
                    else None
                )
                if stating is not None:
                    unnamed.append((Unnamed.STATEMENT, stating))
                # the J's walk starts at the statement's block too: a line
                # of the statement's is its, never the J's
                ask = first_fallback(r.path)
                if ask is not None and (
                    stating is None
                    or (ask.source, ask.target) != (stating.source, stating.target)
                ):
                    unnamed.append((Unnamed.ASK, ask))
                narrowed = case = None
                if not isinstance(premise, Claim):
                    narrowed = narrows(premise, own)
                    if narrowed is None and (wide := widens(premise, r.path, own)):
                        narrowed, case = wide
                by = (
                    ()
                    if isinstance(premise, Claim)
                    else graph.dischargers.get(premise, ())
                )
                cause = (
                    UndeclaredCause.MISSING_EDGES
                    if bare
                    else UndeclaredCause.UNNAMED_LINE
                    if unnamed
                    else UndeclaredCause.DISCHARGED_ELSEWHERE
                    if by
                    else UndeclaredCause.NARROWS
                    if narrowed is not None
                    else UndeclaredCause.LEFT_OUT
                )
                found.append(
                    Undeclared(
                        c,
                        j,
                        node,
                        r.conjecture,
                        r.through,
                        cause,
                        r.path,
                        tuple(unnamed),
                        narrowed,
                        tuple(by),
                        case,
                    )
                )
    return tuple(found)


def _originals(graph: _Graph) -> dict[str, frozenset[str]]:
    """Each assumption's node to the original premises it restates, by
    about edges, each from one assumption onto another: an assumption about
    another restates it, the same premise, while one that narrows or adds
    to it uses it (`docs/model.md` § 3). One about several restates their
    conjunction: declaring it declares each, declaring one of them declares
    that one alone. The originals are those reached by such edges that
    restate nothing further; a circle of copies with none is one premise,
    its first node in the paper's order. A block holding a claim is no
    copy: a preview of a theorem is about the theorem, and its hypotheses
    may say less; nor is a `where-used` edge, which says where its target
    is used, nor a `generalizes` one, whose target is a case of its source,
    another premise. A node no such edge touches is absent: it is its own."""
    at = graph.at
    order = {node: i for i, node in enumerate(at.ident_of)}
    restates: dict[str, list[str]] = {}
    for e in at.edges:
        onto = e["target"].get("block")
        if (
            e["kind"] != "about"
            or e["source"] not in graph.assumptions
            or onto not in graph.assumptions
            or e.get("where_used")
            or e.get("generalizes")
            or not {e["source"], onto} <= at.node_of.keys()
            or any(graph.claims_of.get(at.node_of[x]) for x in (e["source"], onto))
        ):
            continue
        restates.setdefault(at.node_of[e["source"]], []).append(at.node_of[onto])
    touched = set(restates) | {n for ts in restates.values() for n in ts}
    found: dict[str, frozenset[str]] = {}
    for node in touched:
        reached = [node]
        for x in reached:
            reached += [t for t in restates.get(x, ()) if t not in reached]
        originals = frozenset(x for x in reached if x not in restates)
        found[node] = originals or frozenset(
            {min(reached, key=lambda n: order.get(n, len(order)))}
        )
    return found


def completeness(readings: Readings) -> tuple[Covered | None, ...]:
    """Each claim's completeness at the readings' state, in the export's
    order of standings: a `justified` claim's by the justifies edges whose
    cover holds it, a `self` claim's by its block's self marks, each line
    `given` unless it carries a partial; None for another standing or a
    claim no line covers. Where a line names the claim (or its block holds
    no other claim of its standing), the fullest such line; else the least
    of the lines that cover it, since which claim an ambiguous line argues
    is unknown and reads toward the alarm; `unnamed` where a fallback line
    would give a fuller word."""
    graph = _Graph(readings, ())
    order = list(Completeness)
    selfs: dict[str, list[Mapping[str, Any]]] = {}
    for m in graph.at.export.get("self", []):
        selfs.setdefault(m["block"], []).append(m)

    def level(line: Mapping[str, Any]) -> Completeness:
        partial = line.get("partial")
        return Completeness.GIVEN if partial is None else Completeness(partial)

    found: list[Covered | None] = []
    for c in graph.claims:
        if c.status is Status.JUSTIFIED:
            lines = [
                (level(e), graph.at.node_of[e["source"]], graph.cover(e))
                for e in graph.justifying(c)
            ]
            named = [not graph.falls_back(e) for e in graph.justifying(c)]
        elif c.status is Status.SELF:
            # a self mark covers the `self` claims it names, else every
            # `self` claim of its block
            held = [k for k in graph.claims_of[c.node] if k.status is Status.SELF]
            ident = graph.at.ident_of[c.node]
            marks = [
                (m, tuple(graph._named(m.get("claims") or [], held) or held))
                for m in selfs.get(ident, [])
            ]
            marks = [(m, cover) for m, cover in marks if c in cover]
            lines = [(level(m), c.node, cover) for m, cover in marks]
            named = [len(held) == 1 or bool(m.get("claims")) for m, _ in marks]
        else:
            lines, named = [], []
        if not lines:
            found.append(None)
            continue
        # a line that names the claim, or the one claim of its block,
        # argues it as surely as a line covering it alone
        sure = [line for line, n in zip(lines, named, strict=True) if n]
        fallback = [line for line, n in zip(lines, named, strict=True) if not n]
        if sure:
            at = min((a for a, _, _ in sure), key=order.index)
            by_lines = sure
        else:
            at = max((a for a, _, _ in lines), key=order.index)
            by_lines = lines
        fuller = any(order.index(a) < order.index(at) for a, _, _ in fallback)
        by = tuple(dict.fromkeys(node for a, node, _ in by_lines if a is at))
        found.append(Covered(c, at, by, unnamed=fuller))
    return tuple(found)


class Coarse(StrEnum):
    """Where a line on a circle may name less than the text relies on: a
    candidate the question reads, never a verdict (`docs/model.md` § 9).
    Its target end names no part and no claim of a block holding
    definition or assumption content (`whole`), or no claim of a block of
    several (`unnamed-target`); an about line's source end names no claim
    of a block of several (`unnamed-source`); or it restates a claim that
    is itself restated (`sibling`)."""

    WHOLE = "whole"
    UNNAMED_TARGET = "unnamed-target"
    UNNAMED_SOURCE = "unnamed-source"
    SIBLING = "sibling"


@dataclass(frozen=True)
class CircleLine:
    """One ask on a circle: `claim` asks `asks` by `path`, its last step
    the line that asks, on the ways of `claim` through the J's `via` (empty
    where every way of it asks so), with the candidate ends it shows."""

    claim: Claim
    asks: Claim
    path: tuple[PathStep, ...]
    via: tuple[str, ...]
    coarse: tuple[Coarse, ...]

    @property
    def step(self) -> PathStep:
        return self.path[-1]


@dataclass(frozen=True)
class Circle:
    """Claims each resting on the others, in the paper's order: a strongly
    connected part of the graph of what claims rest on, over every way of
    each. Its lines, its key (`circle_key`), and the claims not established
    because of it (`held_back`, its own among them)."""

    key: str
    claims: tuple[Claim, ...]
    lines: tuple[CircleLine, ...]
    held_back: tuple[Claim, ...] = ()

    @property
    def coarse(self) -> bool:
        """Whether a line on it shows a candidate end."""
        return any(line.coarse for line in self.lines)


def _strong(
    nodes: Sequence[Claim], succ: Mapping[Claim, Iterable[Claim]]
) -> list[list[Claim]]:
    """The strongly connected parts of the graph `succ` over `nodes`
    (Tarjan's, without recursion), each in no particular order."""
    index: dict[Claim, int] = {}
    low: dict[Claim, int] = {}
    stack: list[Claim] = []
    on: set[Claim] = set()
    found: list[list[Claim]] = []
    for root in nodes:
        if root in index:
            continue
        index[root] = low[root] = len(index)
        stack.append(root)
        on.add(root)
        work = [(root, iter(succ.get(root, ())))]
        while work:
            v, rest = work[-1]
            for w in rest:
                if w not in index:
                    index[w] = low[w] = len(index)
                    stack.append(w)
                    on.add(w)
                    work.append((w, iter(succ.get(w, ()))))
                    break
                if w in on:
                    low[v] = min(low[v], index[w])
            else:
                work.pop()
                if work:
                    u = work[-1][0]
                    low[u] = min(low[u], low[v])
                if low[v] == index[v]:
                    part: list[Claim] = []
                    while True:
                        w = stack.pop()
                        on.discard(w)
                        part.append(w)
                        if w == v:
                            break
                    found.append(part)
    return found


def _coarse(graph: _Graph, line: CircleLine) -> tuple[Coarse, ...]:
    """The candidate ends of the line that asks (`Coarse`)."""
    edge = line.step.edge
    if edge is None or edge["kind"] not in ("uses", "about"):
        return ()
    target = edge["target"]
    onto = graph._onto(edge)
    found: list[Coarse] = []
    if onto is not None and not target.get("entries") and not target.get("claims"):
        if graph.at.ident_of.get(onto) in graph.content:
            found.append(Coarse.WHOLE)
        if len(graph.claims_of.get(onto, [])) > 1:
            found.append(Coarse.UNNAMED_TARGET)
    if line.step.hop is Hop.ABOUT:
        if (
            not edge.get("source_claims")
            and not edge.get("restates_none")
            and len(graph.claims_of.get(line.claim.node, [])) > 1
        ):
            found.append(Coarse.UNNAMED_SOURCE)
        if line.asks.status is Status.RESTATED:
            found.append(Coarse.SIBLING)
    return tuple(found)


def circle_key(
    graph: _Graph, claims: Iterable[Claim], lines: Iterable[CircleLine]
) -> str:
    """A circle's key: its lines (source, kind, target, the target end's
    parts and claims, the source end's claims or none, and its parts) and
    its claims (node, words, standing). A text change that leaves them
    alone leaves it (`docs/model.md` § 9)."""
    at = graph.at

    def parts(entries: Iterable[str]) -> list[str]:
        return sorted(
            f"{graph.entries.get(p, {}).get('anchor', p)}"
            f" ({graph.entries.get(p, {}).get('role')})"
            for p in entries
        )

    def end(edge: Mapping[str, Any] | None) -> list[Any]:
        if edge is None:
            return []
        target = edge["target"]
        # a line leaving from parts adds them; one from the block whole adds
        # nothing, so a circle read real under its key stays read real
        leaving = edge.get("source_entries")
        return [
            at.node_of.get(edge["source"], edge["source"]),
            edge["kind"],
            graph._onto(edge),
            parts(target.get("entries") or []),
            sorted(norm(w) for w in target.get("claims") or []),
            sorted(norm(w) for w in edge.get("source_claims") or []),
            bool(edge.get("restates_none")),
        ] + ([parts(leaving)] if leaving else [])

    data = {
        "claims": sorted([c.node, norm(c.words), c.status.value] for c in claims),
        "lines": sorted(
            {json.dumps(end(line.step.edge)) for line in lines if line.step.edge}
        ),
    }
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()[:12]


def _circles(graph: _Graph, ways: Mapping[Claim, Sequence[_Way]]) -> list[Circle]:
    """The circles of the graph of what claims rest on, each with its lines,
    in the paper's order of their first claims."""
    order = {c: i for i, c in enumerate(graph.claims)}
    succ = {c: {a for w in ways[c] for a in w.asks} for c in graph.claims}
    found = []
    for part in _strong(graph.claims, succ):
        if len(part) == 1 and part[0] not in succ[part[0]]:
            continue
        members = sorted(part, key=order.__getitem__)
        inside = set(members)
        lines: list[CircleLine] = []
        for c in members:
            # each path that asks a member, with the Js of the ways it is on
            on_ways: dict[tuple[Claim, tuple[PathStep, ...]], list[str | None]] = {}
            for w in ways[c]:
                for k, paths in graph.lines(c, w).items():
                    if k in inside:
                        for path in paths:
                            on_ways.setdefault((k, path), []).append(w.via)
            for (k, path), vias in on_ways.items():
                every = len(vias) == len(ways[c])
                via = () if every else tuple(dict.fromkeys(v for v in vias if v))
                line = CircleLine(c, k, path, via, ())
                lines.append(replace(line, coarse=_coarse(graph, line)))
        found.append(
            Circle(circle_key(graph, members, lines), tuple(members), tuple(lines))
        )
    found.sort(key=lambda x: order[x.claims[0]])
    return found


def established(readings: Readings, marks: Sequence[Mark]) -> tuple[Established, ...]:
    """Each claim at the readings' state, established or with the reason it
    is not, in the export's order of standings."""
    return settled(readings, marks)[0]


def settled(
    readings: Readings, marks: Sequence[Mark]
) -> tuple[tuple[Established, ...], tuple[Circle, ...]]:
    """Each claim at the readings' state, established or with the reason it
    is not, in the export's order of standings; and the circles of what
    claims rest on, those holding the most claims back first, from the same
    ways, so a claim read `circular` names one of them."""
    graph = _Graph(readings, marks)
    ways = {c: graph.ways(c) for c in graph.claims}
    # the least fixed point: a claim is established once some way of it
    # has nothing missing and every claim it asks is established
    by: dict[Claim, _Way] = {}
    grew = True
    while grew:
        grew = False
        for c in graph.claims:
            if c in by:
                continue
            way = next(
                (
                    w
                    for w in ways[c]
                    if w.reason is None and all(a in by for a in w.asks)
                ),
                None,
            )
            if way is not None:
                by[c] = way
                grew = True

    circles = _circles(graph, ways)
    circle_of = {c: x.key for x in circles for c in x.claims}

    def reason(c: Claim, path: tuple[Claim, ...]) -> Reason:
        way = next((w for w in ways[c] if w.reason is None), ways[c][0])
        if way.reason is not None:
            return way.reason
        missing = next(a for a in way.asks if a not in by)
        through = way.through.get(missing)
        if through is not None and missing not in path:
            inner = reason(missing, (*path, c))
            return Reason(
                inner.kind,
                inner.node,
                f"rests on {graph.named(through)}, established under "
                f"{graph.named(missing)}, which it does not take as a hypothesis: "
                f"{inner.why}",
                inner.circle,
            )
        if missing in path or missing == c:
            key = circle_of.get(missing)
            return Reason(
                Missing.CIRCULAR,
                missing.node,
                f"rests on {graph.named(missing)}, which rests on it in turn"
                + (f" (circle {key[:8]})" if key else ""),
                key,
            )
        inner = reason(missing, (*path, c))
        return Reason(
            inner.kind,
            inner.node,
            f"rests on {graph.named(missing)}: {inner.why}",
            inner.circle,
        )

    def reached(c: Claim, seen: set[Claim]) -> dict[str, None]:
        seen.add(c)
        way = by[c]
        found = dict.fromkeys(way.assumptions)
        for a in way.asks:
            if a not in seen:
                found.update(reached(a, seen))
        return found

    def under(c: Claim) -> tuple[Hypothesis, ...]:
        declared = graph.hypotheses(c)
        return tuple(Hypothesis(k, tuple(declared[k])) for k in by[c].under)

    def rests_on(c: Claim, seen: set[Claim]) -> dict[str, None]:
        seen.add(c)
        found = dict.fromkeys(by[c].works)
        for a in by[c].asks:
            if a not in seen:
                found.update(rests_on(a, seen))
        return found

    found = tuple(
        Established(
            c,
            None,
            by[c].via,
            tuple(reached(c, set())),
            under(c),
            tuple(rests_on(c, set())),
        )
        if c in by
        else Established(c, reason(c, ()))
        for c in graph.claims
    )
    held: dict[str, list[Claim]] = {}
    for e in found:
        if e.reason is not None and e.reason.circle is not None:
            held.setdefault(e.reason.circle, []).append(e.claim)
    order = {x.key: i for i, x in enumerate(circles)}
    ranked = sorted(
        (replace(x, held_back=tuple(held.get(x.key, ()))) for x in circles),
        key=lambda x: (-len(x.held_back), order[x.key]),
    )
    return found, tuple(ranked)
