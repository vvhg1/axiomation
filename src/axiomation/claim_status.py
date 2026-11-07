"""A claim's one status for its author, folded from its records
(`docs/model.md` § 11, "A claim's status"): its established verdict, its
support, the readings on its route and the open findings on it, with the
next step from what is already answered; and what clears each item the
reads list.

The records stay as they are: this reads them, and `show`, `open`, `check`
and the viewer all read this, so they never disagree. Nothing here
imports a query or a gate.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from axiomation import judgments, publications, rulings
from axiomation.established import Established, Missing
from axiomation.judgments import FindingRead, StatusKind, SubjectStatus
from axiomation.records import (
    NON_BLOCKING_ASPECTS,
    Aspect,
    Declared,
    Publication,
    Status,
)


class ClaimStatus(StrEnum):
    """A claim's status, the first that holds, in this order: a status
    that reads as settled never comes before one that asks something."""

    DEFECT = "defect"
    DISPUTED = "disputed"
    CONJECTURED = "conjectured"
    UNSUPPORTED = "unsupported"
    LAPSED = "lapsed"
    STALE = "stale"
    UNJUDGED = "unjudged"
    INCONCLUSIVE = "inconclusive"
    RESTS_ON = "rests-on"
    FINDING_OPEN = "finding-open"
    LEFT_UNARGUED = "left-unargued"
    ESTABLISHED = "established"


@dataclass(frozen=True)
class Folded:
    """A claim's status, its detail in words, the node it stops at
    (`rests-on`'s), the next step (None where nothing is asked), the
    open findings on its route, by id, and who read the reading its
    detail names, where it names one."""

    status: ClaimStatus
    detail: str
    next: str | None = None
    at: str | None = None
    findings: tuple[str, ...] = ()
    by: str | None = None

    def data(self) -> dict[str, object]:
        return {
            "status": self.status.value,
            "detail": self.detail,
            "next": self.next,
            "at": self.at,
            "findings": list(self.findings),
            "by": self.by,
        }


# --- what clears an item --------------------------------------------------------------


#: What clears a claim that is unsupported by its own gap, by its standing.
GAP_CLEARS = {
    Status.UNSUPPORTED: "a justifies edge onto it, a self mark, or a ruling that "
    "the paper owes it no argument (`rule`)",
    Status.JUSTIFIED: "a justifies edge onto it, or its standing corrected",
    Status.RESTATED: "an about edge onto the claim it restates, or its standing "
    "corrected",
}
CIRCLE_CLEARS = (
    "each line's ends named as far as the text relies on (an edges edit), or "
    "an argument for one of its claims that does not rest on the others"
)


#: What answers a reading that did not verify whose evidence grew.
ASKED_AGAIN = "asked again by the next round (`axiomation sync`)"


def asked_again(*statuses: SubjectStatus) -> str:
    """Grown subjects' next step: the round asks them, and why."""
    why = dict.fromkeys(g.why for s in statuses for g in s.grown)
    return f"{ASKED_AGAIN}: " + "; ".join(why)


def status_clears(s: SubjectStatus) -> str | None:
    """What clears a lapsed or a disputed subject; None for another. A
    disputed subject names the open findings that dispute it, never one
    answered: a dismissed finding is a step already taken."""
    if s.status is StatusKind.LAPSED:
        return (
            f"`axiomation reask` {s.reads[-1].judgment.id}, then a judgment or "
            "`axiomation reaffirm`"
        )
    if s.status is StatusKind.DISPUTED:
        if s.counted:
            named = ", ".join(f.finding.id for f in s.counted)
            return f"a person's judgment after them, or finding {named} answered"
        return "a person's judgment after them (`axiomation judge`)"
    return None


def finding_clears(f: FindingRead) -> str | None:
    """What clears an open finding: its answer; a lapsed one's, or its
    reaffirmation (`reask` re-reads judgments). One carrying a proposal,
    a review of its kind or a person's answer (docs/model.md
    § 8). None for an answered one."""
    if not f.open:
        return None
    answer = f"`axiomation answer-finding` {f.finding.id}"
    if f.finding.reading:
        # a review handed it back: no review asks it again while it stands
        return (
            f"a person's {answer}: a disagreement of reading, yours to answer"
            if f.live
            else f"a person's {answer}, or `axiomation reaffirm-finding` {f.finding.id}"
        )
    if f.proposal is not None:
        # the session that filed it, by name: the field's are not asked by
        # default
        review = "axiomation review" + (
            f" --session {f.filed_by.value}" if f.filed_by is not None else ""
        )
        answer = (
            f"a review of its kind settling the proposal (`{review}`), "
            f"or a person's {answer}"
            if judgments.review_settles(f)
            else f"a person's {answer}"
        )
    return (
        answer
        if f.live
        else answer + f", or `axiomation reaffirm-finding` {f.finding.id}"
    )


def support_clears(standing: Status, support: rulings.ClaimSupport) -> str:
    """What clears an unsupported claim: its circle broken, what its guard
    asks, or the argument its standing lacks; a ruling where only an
    agent's `owed` speaks, which a person's ruling replaces."""
    if support.gap is Missing.CIRCULAR:
        return CIRCLE_CLEARS
    if support.conjectures:
        return (
            "the conjecture argued and asserted, the sentence asserting it no "
            "more, or the claim it restates named (`→ about T [<claim>]`)"
        )
    if support.declared is Declared.PROMISED:
        # a ruling never clears what the paper promises to argue
        return "the argument the paper promises, or the promise taken out"
    match support.guard:
        case rulings.Guard.TRUE_DISPUTED:
            return (
                GAP_CLEARS[Status.UNSUPPORTED]
                + ", by a person where its `true` is disputed"
            )
        case rulings.Guard.TRUE_NOT_HELD:
            return (
                "a justifies edge onto it, a self mark, a held verified `true` "
                "beside the ruling, or a person's ruling"
            )
        case rulings.Guard.NO_RESULT_USED:
            return (
                "the uses edge onto the result it is immediate from, an "
                "argument for it, or another ruling"
            )
        case rulings.Guard.RELIED_ON:
            what = {
                rulings.RulingKind.LEFT_UNARGUED: "a claim left unargued",
                rulings.RulingKind.POSITIONING: "a claim the literature settles",
            }.get(support.ruling, "an interpretation")
            return (
                "an argument for it, or the edges that rely on it retracted, or "
                f"naming the claim they rely on: {what} is no premise"
            )
    if standing is Status.UNSUPPORTED and _owed(support):
        owed = support.owed
        fix = (
            f"{owed.ruling.fix} (its ruling's fix); or "
            if owed is not None and owed.ruling.fix
            else ""
        )
        return fix + (
            "a justifies edge onto it or a self mark, or a person's ruling "
            "(`rule`), which replaces the agents' owed"
            if not support.by_person
            else "a justifies edge onto it or a self mark: you ruled it owed"
        )
    return GAP_CLEARS.get(standing, "")


def _owed(support: rulings.ClaimSupport) -> bool:
    ruled = support.ruled
    return ruled is not None and any(
        r.ruling.kind is rulings.RulingKind.OWED for r in ruled.speaks
    )


# --- the fold ---------------------------------------------------------------------


#: Why a clearing ruling clears nothing (`rulings.Guard`), in the status's
#: detail.
GUARD_WORDS = {
    rulings.Guard.PROMISED: "the paper promises its argument",
    rulings.Guard.TRUE_DISPUTED: "agents alone, `true` disputed",
    rulings.Guard.TRUE_NOT_HELD: "agents alone, `true` not held verified",
    rulings.Guard.NO_RESULT_USED: "its block uses no result",
    rulings.Guard.RELIED_ON: "relied on",
}
#: A claim's own reason, where it is the status, by its kind.
_OWN = {
    Missing.LAPSED: ClaimStatus.LAPSED,
    Missing.STALE: ClaimStatus.STALE,
    Missing.UNJUDGED: ClaimStatus.UNJUDGED,
    Missing.INCONCLUSIVE: ClaimStatus.INCONCLUSIVE,
}
_DEFECTS = (
    Missing.NOT_SHOWN,
    Missing.CONTRADICTED,
    Missing.FINDING,
    Missing.RETRACTED,
)


def _subject_words(s: SubjectStatus) -> str:
    return "`valid` of its proof" if s.aspect is Aspect.VALID else f"`{s.aspect}`"


def _by(support: rulings.ClaimSupport) -> str:
    return "a person" if support.by_person else "agents alone"


def _unsupported(standing: Status, support: rulings.ClaimSupport) -> str:
    """An unsupported claim's detail: why, in the records' terms."""
    if support.gap is Missing.CIRCULAR:
        return "circular"
    if support.conjectures:
        return "restates " + ", ".join(support.conjectures) + ", conjectured"
    if support.guard is not None:
        assert support.ruling is not None
        return f"ruled {support.ruling.value}, guarded: {GUARD_WORDS[support.guard]}"
    if standing is Status.JUSTIFIED:
        return "no justifies edge reaches it"
    if standing is Status.RESTATED:
        return "restates nothing"
    if _owed(support):
        owed = support.owed
        weight = (
            f" {owed.ruling.severity}"
            if owed is not None and owed.ruling.severity is not None
            else ""
        )
        return f"ruled owed{weight} by {_by(support)}"
    return "unruled"


def fold(
    e: Established,
    support: rulings.ClaimSupport | None,
    route: Sequence[SubjectStatus],
    findings: Sequence[FindingRead],
    handle: Callable[[str], str],
    warned: Mapping[str, Publication] | None = None,
    again: Sequence[SubjectStatus] = (),
) -> Folded:
    """A claim's status from its established verdict `e`, its `support`
    (None: `conjectured`), the subjects on its route (its `true`, the
    `valid` of each J onto it) and the open findings that bear on them;
    `handle` names a node. The first status that holds
    (`ClaimStatus`'s order). An established claim resting on a cited work
    whose publication warns (`warned`, by key), accepted or not, says so
    in its detail. A reading the next round asks again because its
    evidence grew (`again`, on its route or its citations) keeps its
    status; its next step says so."""
    claim = e.claim
    reason = e.reason
    own_nodes = {claim.node, *(s.subject.node for s in route)}
    own = reason is not None and reason.node in own_nodes
    opened = tuple(f for f in findings if f.open)
    ids = tuple(f.finding.id for f in opened)
    # a negative reading on its route, live or lapsed and unresolved, is its
    # defect, as `defects` lists it
    for s in route:
        read = judgments.negative(s) or judgments.lapsed_negative(s)
        if read is not None:
            verdict = read.judgment.verdict.value
            lapsed = "" if read.live else " (lapsed)"
            return Folded(
                ClaimStatus.DEFECT,
                f"{_subject_words(s)} {verdict}{lapsed}, read by {read.author.name}",
                asked_again(s)
                if s.grown
                else f"{ASKED_AGAIN}: your proposed reading"
                if s.proposal is not None and s.proposal.asks
                else judgments.NEGATIVE_CLEARS
                if read.live
                else f"`axiomation reask` {read.judgment.id}, then a judgment",
                findings=ids,
                by=read.author.name,
            )
    blocking = [f for f in opened if f.finding.severity == 3]
    if blocking:
        f = blocking[0]
        return Folded(
            ClaimStatus.DEFECT,
            f"finding {f.finding.id} (severity 3, {f.finding.aspect.value})",
            finding_clears(f),
            findings=ids,
        )
    if own and reason is not None and reason.kind in _DEFECTS:
        return Folded(ClaimStatus.DEFECT, reason.why, findings=ids)
    disputed = [s for s in route if s.status is StatusKind.DISPUTED]
    if disputed:
        s = disputed[0]
        return Folded(
            ClaimStatus.DISPUTED,
            f"{_subject_words(s)}, {len(s.reads)} readings",
            status_clears(s),
            findings=ids,
        )
    if own and reason is not None and reason.kind is Missing.DISPUTED:
        return Folded(
            ClaimStatus.DISPUTED,
            reason.why,
            "a person's judgment (`axiomation judge`)",
            findings=ids,
        )
    if support is None:
        return Folded(
            ClaimStatus.CONJECTURED, "the paper does not assert it", findings=ids
        )
    if support.status is rulings.Support.UNSUPPORTED:
        return Folded(
            ClaimStatus.UNSUPPORTED,
            _unsupported(claim.status, support),
            support_clears(claim.status, support),
            findings=ids,
        )
    # a claim a ruling cleared whose `true` is unjudged has its own reason
    # `unsupported` (`established`: nothing yet to rest on): unread, not a gap
    kind = (
        Missing.UNJUDGED
        if own and reason is not None and reason.kind is Missing.UNSUPPORTED
        else None
        if reason is None
        else reason.kind
    )
    if own and reason is not None and kind in _OWN:
        status = _OWN[kind]
        lapsed = next((s for s in route if s.status is StatusKind.LAPSED), None)
        then = {
            ClaimStatus.LAPSED: "`axiomation review`"
            if lapsed is None
            else f"`axiomation review`, or {status_clears(lapsed)}",
            ClaimStatus.STALE: "a reread of the marked line (`axiomation reread`) "
            "or a reading of it",
            ClaimStatus.UNJUDGED: "a review (`axiomation review`), or a person's "
            "judgment (`axiomation judge`)",
            ClaimStatus.INCONCLUSIVE: asked_again(*again)
            if again
            else "a person's judgment (`axiomation judge`), "
            "or a review that concludes; it only warns",
        }[status]
        return Folded(status, reason.why, then, findings=ids)
    if reason is not None:
        return Folded(
            ClaimStatus.RESTS_ON,
            f"{handle(reason.node)}: {reason.kind.value}",
            at=reason.node,
            findings=ids,
        )
    holding = [f for f in opened if f.finding.aspect not in NON_BLOCKING_ASPECTS]
    if holding:
        f = holding[0]
        return Folded(
            ClaimStatus.FINDING_OPEN,
            f"finding {f.finding.id} (severity {f.finding.severity}, "
            f"{f.finding.aspect.value})",
            finding_clears(f),
            findings=ids,
        )
    if support.status is rulings.Support.LEFT_UNARGUED:
        return Folded(ClaimStatus.LEFT_UNARGUED, "your ruling", findings=ids)
    if support.status is rulings.Support.NO_ARGUMENT_OWED:
        assert support.ruling is not None
        detail = f"no argument owed: ruled {support.ruling.value} by {_by(support)}"
    else:
        detail = "argued"
    on = [k for k in e.works if k in (warned or {})]
    if on:
        assert warned is not None
        detail += ", on " + "; ".join(
            f"{publications.WORDS[warned[k]]}: {k}" for k in on
        )
    return Folded(ClaimStatus.ESTABLISHED, detail, findings=ids)
