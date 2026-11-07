"""The author's inbox: the decisions at a kept state that only a person
makes, grouped (`docs/guide.md` § 3).

It reads what `open` lists and keeps a person's part: a dispute to judge,
a negative reading, a finding to answer (one a review handed back as a
disagreement of reading first, then one that stood again, with what was
answered or proposed on its earlier filings and what came of it), a
proposal awaiting its review (a person's proposed reading or ruling
among them), and each unsupported claim in the cheapest route the
records allow. Each claim is listed once: one a person's proposed ruling
awaits a review for is that proposal's, no decision of theirs until the
review settles it, and the head counts it apart. What a machine step
does (`review`, `reread`, `circles`) is not here. A pure function of the
state, like `open`.
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum

from axiomation import judgments, rulings, support_search
from axiomation.claim_status import ClaimStatus
from axiomation.established import Missing
from axiomation.queries import (
    Item,
    OpenKind,
    Report,
    View,
    finding_item,
    finding_piles,
    open_report,
    proposal_item,
    proposal_words,
    proposed_ruling_item,
    support_data,
    weighed_words,
)
from axiomation.records import Declared, Status, Subject


class Section(StrEnum):
    """The inbox's sections, in its order."""

    TO_JUDGE = "to_judge"
    READ_NEGATIVE = "read_negative"
    READING = "reading"
    STOOD_AGAIN = "stood_again"
    TO_ANSWER = "to_answer"
    AWAITING_REVIEW = "awaiting_review"
    BY_A_LINE = "unsupported_by_a_line"
    BY_A_RULING = "unsupported_by_a_ruling"
    BY_AN_ARGUMENT = "unsupported_by_an_argument"


class Route(StrEnum):
    """An unsupported claim's cheapest route the records allow: a line the
    graph lacks, a ruling that decides it, or the text (an argument, or
    the claim cut)."""

    LINE = "line"
    RULING = "ruling"
    ARGUMENT = "argument"

    @property
    def section(self) -> Section:
        return {
            Route.LINE: Section.BY_A_LINE,
            Route.RULING: Section.BY_A_RULING,
            Route.ARGUMENT: Section.BY_AN_ARGUMENT,
        }[self]


def route(standing: Status, support: rulings.ClaimSupport) -> tuple[Route, str]:
    """An unsupported claim's route and its step, from its standing and its
    support status: a line where the graph lacks only one, a ruling where
    one decides it, else an argument."""
    guard = support.guard
    if support.gap is Missing.CIRCULAR:
        return Route.LINE, "narrow an end of its circle to what the text relies on"
    if support.conjectures:
        return Route.LINE, (
            "name the claim it restates (`→ about T [<claim>]`), or stop "
            "asserting the conjecture"
        )
    if guard is rulings.Guard.NO_RESULT_USED:
        return Route.LINE, "add the uses edge onto the result it is immediate from"
    if standing is Status.JUSTIFIED:
        return Route.LINE, "add the justifies line onto it (`axiomation change --add`)"
    if standing is Status.RESTATED:
        return Route.LINE, "add the about line onto the claim it restates"
    if support.declared is Declared.PROMISED:
        # no ruling clears what the paper promises to argue
        return Route.ARGUMENT, (
            "give the argument the paper promises, or cut the promise"
        )
    if guard is rulings.Guard.TRUE_DISPUTED:
        # a disputed `true` makes the claim `disputed`, to judge first: kept
        # so the guard is never read as another's
        return (
            Route.RULING,
            "judge its `true` first (`axiomation judge`), then rule",
        )
    if guard is rulings.Guard.TRUE_NOT_HELD:
        kind = "known" if support.ruling is None else support.ruling.value
        return Route.RULING, (
            f"rule it `{kind}` yourself, which needs no verified `true` "
            "(`axiomation rule`); or wait for a review that reads it verified"
        )
    if guard is rulings.Guard.RELIED_ON:
        return Route.ARGUMENT, (
            "argue it; or name, for what rests on it, the claim it relies on"
        )
    if support.ruled is None or not support.ruled.speaks:
        # unruled, or each ruling lapsed
        return Route.RULING, (
            "rule whether the paper owes it an argument (`axiomation rule`), or "
            "leave it to a review"
        )
    owed = any(r.ruling.kind is rulings.RulingKind.OWED for r in support.ruled.speaks)
    heaviest = support.owed
    if (
        heaviest is not None
        and heaviest.ruling.fix
        and not (not support.by_person and support.true_verified)
    ):
        # the ruling says what would discharge it
        return Route.ARGUMENT, (
            f"{heaviest.ruling.fix} (its ruling's fix)"
            + (
                ""
                if support.by_person
                else "; or your ruling over the agents' `owed` (`axiomation rule`, "
                "`--propose` for the next review to settle)"
            )
        )
    if owed and not support.by_person and support.true_verified:
        return Route.RULING, (
            "its `true` holds: rule whether the paper must argue it (`known` or "
            "`immediate` over the agents' `owed`, `axiomation rule`), else argue it"
        )
    if owed and support.by_person:
        return Route.ARGUMENT, "argue it, or cut it: you ruled it owed"
    return Route.ARGUMENT, (
        "argue it, or cut or soften it; or rule over the agents' `owed` "
        "(`axiomation rule`)"
    )


def _earlier(view: View, f: judgments.FindingRead) -> list[tuple[str, str]]:
    """What was answered or proposed on a finding's earlier filings, the
    latest filing first: each filing's id and its answer, or the proposal
    its refiling weighed and turned down."""
    by = {g.finding.id: g for g in view.readings.findings}
    found: list[tuple[str, str]] = []
    seen = {f.finding.id}
    at = f.finding.reaffirms
    while at is not None and at in by and at not in seen:
        seen.add(at)
        g = by[at]
        if g.answer is not None:
            found.append(
                (at, f"{g.answer.status.value}: {g.answer.note or ''}".rstrip(": "))
            )
        # the proposal the latest filing turned down is on its own line
        if g.weighed is not None and at != f.finding.reaffirms:
            found.append((at, weighed_words(g)))
        elif g.proposal is not None:
            found.append((at, proposal_words(g.proposal)))
        at = g.finding.reaffirms
    return found


def _findings(view: View) -> dict[Section, list[Item]]:
    found: dict[Section, list[Item]] = {
        Section.READING: [],
        Section.STOOD_AGAIN: [],
        Section.TO_ANSWER: [],
        Section.AWAITING_REVIEW: [],
    }
    firsts = [g.finding for g in view.readings.findings]
    for pile in finding_piles(view):
        f = pile[0]
        item = finding_item(view, pile)
        if f.finding.reaffirms is not None:
            first = judgments.first_filing(firsts, f.finding)
            earlier = _earlier(view, f)
            line = (
                item.line
                + f"\n  first filed as {first}"
                + "".join(f"\n  on {at}: {words}" for at, words in earlier)
            )
            item = Item(line, {**item.data, "line": line, "first": first})
            item.data["earlier"] = [{"finding": a, "said": w} for a, w in earlier]
            # a review handed it back: the person's answer closes it
            reading = f.finding.reading and f.proposal is None
            found[Section.READING if reading else Section.STOOD_AGAIN].append(item)
        elif f.proposal is not None:
            found[Section.AWAITING_REVIEW].append(item)
        else:
            found[Section.TO_ANSWER].append(item)
    return found


def _routed(item: Item, way: Route, step: str) -> Item:
    return Item(
        f"{item.line}\n  next: {step}", {**item.data, "route": way.value, "next": step}
    )


def _unsupported(
    view: View, opened: dict[str, list[Item]], asked: set[tuple[str, str | None]]
) -> dict[Section, list[Item]]:
    """The claims `unsupported-claims` lists, by route: `open`'s items with
    their route, its step and the rulings that speak for them; each circle
    that holds a claim back, by a line. A claim in `asked`, a person's
    proposed ruling on it awaiting its review, is listed there alone."""
    found: dict[Section, list[Item]] = {r.section: [] for r in Route}
    claims = {
        (view.node(s["block"]), s["claim"]["text"]): (Status(s["status"]), support)
        for s, support in zip(view.standings, view.support, strict=True)
        if support is not None
    }
    for item in opened[OpenKind.UNSUPPORTED.section]:
        anchor = item.data["anchor"]
        key = (anchor["node"], anchor["words"])
        if key in asked:
            continue
        standing, support = claims[key]
        way, step = route(standing, support)
        routed = _routed(item, way, step)
        routed.data["support"] = support_data(support)
        # ruled before, no ruling speaking now: each lapsed
        routed.data["rulings_lapsed"] = (
            support.ruled is not None and not support.ruled.speaks
        )
        found[way.section].append(routed)
    # a circle the gate lists: one a claim of it stands in with status
    # `unsupported`, never one whose claims are all defects or disputed
    circled = {
        e.reason.circle
        for e, support, claim in zip(
            view.established, view.support, view.claim_statuses, strict=True
        )
        if support is not None
        and support.gap is Missing.CIRCULAR
        and claim.status is ClaimStatus.UNSUPPORTED
        and e.reason is not None
    }
    for item in opened[OpenKind.CIRCLE.section]:
        if item.data["cause"]["circle"] in circled:
            found[Section.BY_A_LINE].append(
                _routed(item, Route.LINE, "narrow an end to what the text relies on")
            )
    return found


def _asked_rulings(view: View) -> list[rulings.Ruled]:
    """The claims a person's proposed ruling asks again, where a review can
    ask them: standing unsupported (a claim argued since is no ruling's)."""
    at = view.readings.at
    standing = {
        support_search.claim_key(Subject(node, None, words))
        for (node, words), s in zip(
            at.standings, at.export.get("standings", []), strict=True
        )
        if s["status"] == Status.UNSUPPORTED.value
    }
    return [
        r
        for r in view.rulings.values()
        if r.proposal is not None
        and r.proposal.asks
        and support_search.claim_key(r.subject) in standing
    ]


def _proposed_rulings(view: View, asked: list[rulings.Ruled]) -> list[Item]:
    """The claims in `asked` (`_asked_rulings`) as the inbox lists them; one
    the support search has not read yet waits for it, and says so."""
    if not asked:
        return []
    searched = support_search.searched(view.store, view.state, view.reader)
    found = []
    for r in asked:
        item = proposed_ruling_item(view, r)
        if support_search.claim_key(r.subject) not in searched:
            step = (
                "the support search reads it first (`axiomation support PAPER "
                "--ask`), then the argument review"
            )
            item = Item(f"{item.line}\n  waits: {step}", {**item.data, "waits": step})
        found.append(item)
    return found


def inbox(view: View) -> Report:
    """The author's inbox at the view's state: a section per `Section`,
    empty ones left out, and a head counting each."""
    opened = open_report(view).sections
    # a dispute a person judges: readings that differ, no negative standing
    # (that is `read negative`), none only a finding disputes (`to answer`)
    judged = {
        tuple(r.judgment.id for r in s.reads)
        for s in view.readings.statuses
        if judgments.to_judge(s)
    }
    # a subject a person's proposed reading asks again awaits its review
    proposed = [
        s for s in view.readings.statuses if s.proposal is not None and s.proposal.asks
    ]
    asked = {(s.aspect, s.subject) for s in proposed}
    negative = {
        r.judgment.id
        for s in view.readings.statuses
        if (s.aspect, s.subject) in asked
        for r in s.reads
    }
    findings = _findings(view)
    ruled = _asked_rulings(view)
    findings[Section.AWAITING_REVIEW] = [
        *findings[Section.AWAITING_REVIEW],
        *(proposal_item(view, s) for s in proposed),
        *_proposed_rulings(view, ruled),
    ]
    sections: dict[Section, Sequence[Item]] = {
        Section.TO_JUDGE: [
            i
            for i in opened[OpenKind.DISPUTED.section]
            if tuple(i.data["judgments"]) in judged
        ],
        Section.READ_NEGATIVE: [
            i
            for i in opened[OpenKind.NEGATIVE.section]
            if i.data["cause"]["judgment"] not in negative
        ],
        **findings,
        **_unsupported(
            view, opened, {(r.subject.node, r.subject.words) for r in ruled}
        ),
    }
    shown = {s: list(sections[s]) for s in Section if sections[s]}
    # what awaits a review is no decision of yours: counted apart
    waiting = len(shown.get(Section.AWAITING_REVIEW, []))
    decided = {s: i for s, i in shown.items() if s is not Section.AWAITING_REVIEW}
    total = sum(len(i) for i in decided.values())
    head = f"inbox at state {view.state.id}: " + (
        f"{total} {'decision' if total == 1 else 'decisions'} of yours: "
        + ", ".join(f"{len(i)} {s.value.replace('_', ' ')}" for s, i in decided.items())
        if total
        else "nothing of yours to decide"
    )
    if waiting:
        head += f"; {waiting} awaiting a review"
    return Report(
        head=[head],
        data={
            "state": view.state.id,
            "counts": {s.value: len(sections[s]) for s in Section},
        },
        sections={s.value: i for s, i in shown.items()},
    )
