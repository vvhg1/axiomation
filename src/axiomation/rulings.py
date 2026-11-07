"""Rulings: whether the paper owes an argument for a claim it asserts and
argues nothing for (docs/model.md § 8, "A ruling"); filed by a person or
an agent, read at a kept state, and which of them speaks.

A ruling answers a referee's question, never whether the claim holds:
`established` reads no ruling (§ 9), and the `unsupported-claims` gate
reads nothing else for a claim standing `unsupported` (§ 11). What a
ruling did is a claim's support status (§ 8), computed here from what the
caller read, so the gate, the open view and the viewer read one answer.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from functools import partial

from axiomation import judgments
from axiomation.established import Missing
from axiomation.evidence import FileStamp, PageStamp, Sources, work_of
from axiomation.judgments import (
    Author,
    Cause,
    CauseKind,
    FindingRead,
    JudgmentError,
    Reader,
    Seen,
    Walked,
    kept_reader,
)
from axiomation.questions import review_file as rf
from axiomation.questions.edge_sheet import norm
from axiomation.records import (
    NOT_FILED,
    RULING,
    RULING_WITHDRAWAL,
    AnchorKind,
    BasisElement,
    BasisKind,
    Declared,
    FiledAt,
    FileSeen,
    FindingStatus,
    JudgmentRecords,
    RecordStore,
    Ruling,
    RulingKind,
    RulingWithdrawal,
    Status,
    Subject,
    Verdict,
    filed_after,
)
from axiomation.resolver import Filed
from axiomation.versions import State

#: The rulings that say the paper owes no argument.
CLEARING = (
    RulingKind.KNOWN,
    RulingKind.IMMEDIATE,
    RulingKind.INTERPRETATION,
    RulingKind.POSITIONING,
)
#: The rulings an agent may file only beside a `true` it held `verified`:
#: a claim no reading showed holds is neither known nor immediate.
SHOWN = (RulingKind.KNOWN, RulingKind.IMMEDIATE)
#: The rulings that never stand where something relies on the claim: no
#: premise is a gloss, a claim the literature settles, nor an argument the
#: author leaves out.
NO_PREMISE = (
    RulingKind.INTERPRETATION,
    RulingKind.POSITIONING,
    RulingKind.LEFT_UNARGUED,
)


def _standing(at: Seen, on: Subject) -> Status | None:
    for (node, words), s in zip(
        at.standings, at.export.get("standings", []), strict=True
    ):
        if node == on.node and words == on.words:
            return Status(s["status"])
    return None


def subject(at: Seen, on: str, claim: str | None = None) -> Subject:
    """A ruling's subject at a kept state: the claim of block `on` that
    `claim` names (its only one where it holds one), standing
    `unsupported`; refused, with why, otherwise."""
    found = judgments.subject(at, judgments.Aspect.TRUE, on, claim=claim)
    status = _standing(at, found)
    if status is not Status.UNSUPPORTED:
        raise JudgmentError(
            f'{at.handle(found.node)} "{found.words}" stands '
            f"{status.value if status else 'nowhere'}: a ruling is on a claim "
            "standing unsupported, which the paper argues nothing for"
        )
    return found


def ruling_record(
    at: Seen,
    on: Subject,
    kind: RulingKind,
    reason: str,
    severity: int | None = None,
    fix: str | None = None,
    proposed: bool = False,
) -> Ruling:
    """A ruling's record at the state `at` reads, unnumbered: an `owed` one
    with its severity and its fix, any other with neither."""
    if not reason.strip():
        raise JudgmentError("a ruling gives its reason: why it is owed or not")
    if kind is RulingKind.OWED:
        if severity not in (1, 2, 3):
            raise JudgmentError(
                "an owed ruling carries its severity, 1 to 3: 3 a result or a "
                "stated contribution rests on the claim, or it is one; 2 a claim "
                "of the paper's own nothing rests on; 1 a passing sentence"
            )
        if fix is None or not fix.strip():
            raise JudgmentError(
                "an owed ruling carries its fix: the missing step, the pointer "
                "the sentence owes, a softened wording, or a cut"
            )
    elif severity is not None or fix is not None:
        raise JudgmentError(
            f"only an owed ruling carries a severity and a fix, not {kind.value}"
        )
    return Ruling(
        "",
        None,
        at.state.id,
        on,
        kind,
        reason,
        severity=severity,
        fix=None if fix is None else fix.strip(),
        proposed=proposed,
    )


def ruling_pages(
    on: Subject,
    pages: Sequence[tuple[str, int]],
    sources: Sources | None,
    works: Sequence[str],
) -> tuple[PageStamp, ...]:
    """The pages a ruling on `on` rests on, read again through `sources`
    and stamped there as a `true` reading's are (`judgments.read_pages`):
    each of a work it may read (`works`, `judgments.readable_works`), none
    that failed to read."""
    if not pages:
        return ()
    return judgments.read_pages(
        on, Verdict.VERIFIED, pages, sources, judgments.Aspect.TRUE, works
    )


def rule(
    store: RecordStore,
    kept: State,
    on: Subject,
    kind: RulingKind,
    reason: str,
    author: Author = Author(),  # noqa: B008 - frozen
    *,
    severity: int | None = None,
    fix: str | None = None,
    propose: bool = False,
    pages: Sequence[tuple[str, int]] = (),
    sources: Sources | None = None,
) -> Filed:
    """A ruling filed on a kept state; `left-unargued` a person's alone. A
    person's filed as a proposal (`propose`) never speaks: the next review
    that rules the claim settles it (`docs/model.md` § 8). Its `pages`
    (key and index), where it rests on some, are read through `sources`
    and stamped."""
    if propose:
        if not author.person:
            raise JudgmentError(
                "a proposed ruling is a person's, for a review to settle: an "
                "agent's ruling by hand speaks as filed"
            )
        if kind is RulingKind.LEFT_UNARGUED:
            raise JudgmentError(
                "left-unargued is the author's own call, which no review files: "
                "rule it, never propose it"
            )
        ruled = rulings(store, kept).get(on)
        if ruled is not None and ruled.by_person:
            decided = next(r for r in ruled.speaks if r.author.person)
            raise JudgmentError(
                f"a person's ruling decides it ({decided.ruling.id}), and no review "
                "asks it again: rule it yourself, or withdraw that ruling first"
            )
    return rule_each(
        store,
        kept,
        (on,),
        kind,
        reason,
        author,
        severity=severity,
        fix=fix,
        propose=propose,
        pages=pages,
        sources=sources,
    )


def rule_each(
    store: RecordStore,
    kept: State,
    on: Sequence[Subject],
    kind: RulingKind,
    reason: str,
    author: Author = Author(),  # noqa: B008 - frozen
    *,
    severity: int | None = None,
    fix: str | None = None,
    propose: bool = False,
    pages: Sequence[tuple[str, int]] = (),
    sources: Sources | None = None,
) -> Filed:
    """One ruling on each claim, in one filing, each with the same reason
    (and, `owed`, the same severity and fix, and the same `pages`): a
    ruling on a set is a person's, as each of its rulings is read alone
    (`docs/model.md` § 8). `left-unargued` a person's alone; a proposal
    names one claim. A ruling with pages is given the files the store last
    saw of the works it read, each file it read a page of stamped now, as
    a judgment filed by hand is (`judgments.given_files`)."""
    if kind is RulingKind.LEFT_UNARGUED and not author.person:
        raise JudgmentError(
            "left-unargued is the author's: a person files it, never an agent"
        )
    if len(on) > 1 and not author.person:
        raise JudgmentError("a ruling on a set is a person's: an agent rules each")
    if len(on) > 1 and propose:
        raise JudgmentError("a proposed ruling names one claim: propose each")
    if not on:
        raise JudgmentError("a ruling names its claim")
    at = judgments.seen(store, kept)
    records = tuple(
        ruling_record(at, s, kind, reason, severity, fix, propose) for s in on
    )
    stamps = ruling_pages(on[0], pages, sources, judgments.readable_works(at, sources))
    changed: list[FileStamp] = []
    if stamps:
        given, changed = judgments.given_files(
            store, {work_of(p.key) for p in stamps}, stamps, sources
        )
        records = tuple(replace(r, pages=stamps, given=given) for r in records)
    return judgments.file_records(
        store,
        RULING,
        JudgmentRecords(
            rulings=records, files=tuple(FileSeen("", None, f) for f in changed)
        ),
        author,
    )


def ruling_named(store: RecordStore, ref: str) -> Ruling:
    """The ruling an id or a unique prefix of it names; refused otherwise."""
    found = [r for r in store.judgment_records().rulings if r.id.startswith(ref)]
    if len(found) != 1:
        raise JudgmentError(
            f"{ref!r} names {len(found)} rulings: give a ruling's id, or a unique "
            "prefix of it"
        )
    return found[0]


def withdraw(
    store: RecordStore,
    ref: str,
    reason: str,
    author: Author = Author(),  # noqa: B008 - frozen
) -> Filed:
    """A person's withdrawal of a ruling: it speaks no more, and the claim
    is asked again by the next review that judges its `true`. An agent
    never withdraws one: it rules again, and its later ruling replaces its
    own."""
    if not author.person:
        raise JudgmentError("a withdrawal is a person's: an agent rules again")
    if not reason.strip():
        raise JudgmentError("a withdrawal gives its reason")
    ruling = ruling_named(store, ref)
    if any(w.ruling == ruling.id for w in store.judgment_records().withdrawals):
        raise JudgmentError(f"ruling {ruling.id} is withdrawn already")
    record = RulingWithdrawal("", None, ruling.id, reason)
    return judgments.file_records(
        store, RULING_WITHDRAWAL, JudgmentRecords(withdrawals=(record,)), author
    )


# --- read at a state ----------------------------------------------------------------


@dataclass(frozen=True)
class RulingRead:
    """A ruling read at a state: its author, its place in the store, and
    its walk there."""

    ruling: Ruling
    author: Author
    order: FiledAt
    walked: Walked
    #: the person's withdrawal that took it back, if any
    withdrawn: RulingWithdrawal | None = None
    #: the version of the ruling question its review answered; None for a
    #: ruling no review filed, a person's or one filed by hand
    question: int | None = None

    @property
    def live(self) -> bool:
        return not self.walked.lapsed and self.withdrawn is None

    @property
    def proposed(self) -> bool:
        return self.ruling.proposed

    @property
    def outdated(self) -> bool:
        """Filed by a review under an older ruling question than its brief
        asks now: asked again (docs/architecture.md § 9)."""
        return self.question is not None and self.question < rf.QUESTION_VERSIONS.get(
            RULING, 1
        )

    @property
    def subject(self) -> Subject:
        """Its claim at the state read: its node and words as carried."""
        node = self.walked.nodes[0] or self.ruling.subject.node
        return Subject(node, None, self.walked.words or self.ruling.subject.words)


@dataclass(frozen=True)
class ProposedRuling:
    """A person's ruling filed as a proposal (`rule --propose`), and its
    re-ask: the first ruling of its claim filed after it, whoever files it.
    Until one is, the next review that rules the claim asks it again with
    the proposal in view (docs/model.md § 8)."""

    read: RulingRead
    reask: RulingRead | None = None

    @property
    def asks(self) -> bool:
        return self.reask is None and self.read.live


@dataclass(frozen=True)
class Ruled:
    """A claim's rulings at a state, in filing order, and those that speak:
    of each author's latest ruling, those live; each person's where any
    person's is, else each agent's, those under an older ruling question
    left out where a review's under the current one is live; a proposal
    never speaks. `proposal` is the latest one filed, with its re-ask."""

    subject: Subject
    reads: tuple[RulingRead, ...]
    speaks: tuple[RulingRead, ...]
    proposal: ProposedRuling | None = None

    @property
    def by_person(self) -> bool:
        return any(r.author.person for r in self.speaks)

    @property
    def asked_again(self) -> bool:
        """Whether its latest ruling, a proposal aside, was withdrawn or
        lapsed by a file it read: the next review asks the claim again,
        whatever an earlier ruling says (docs/model.md § 8)."""
        filed = [r for r in self.reads if not r.proposed]
        if not filed:
            return False
        last = filed[-1]
        return last.withdrawn is not None or any(
            c.kind is CauseKind.CITED_FILE for c in last.walked.causes
        )

    @property
    def outdated(self) -> bool:
        """Whether an agent's ruling under an older ruling question speaks,
        which the next review asks again."""
        return any(r.outdated for r in self.speaks)

    @property
    def clears(self) -> RulingKind | None:
        """What takes the claim out of the gate's count: none where nothing
        speaks or any speaking ruling is `owed` (in doubt, owed), or an
        agent's `left-unargued` speaks (the author's alone); else
        `left-unargued`, which says the argument is owed, then
        `interpretation`, `positioning`, then `immediate`, the stricter
        first (each has a guard of its own), else `known`."""
        kinds = {r.ruling.kind for r in self.speaks}
        if not kinds or RulingKind.OWED in kinds:
            return None
        if any(
            r.ruling.kind is RulingKind.LEFT_UNARGUED and not r.author.person
            for r in self.speaks
        ):
            return None
        for kind in (
            RulingKind.LEFT_UNARGUED,
            RulingKind.INTERPRETATION,
            RulingKind.POSITIONING,
            RulingKind.IMMEDIATE,
        ):
            if kind in kinds:
                return kind
        return RulingKind.KNOWN


def _latest_each(reads: Sequence[RulingRead]) -> tuple[RulingRead, ...]:
    latest: dict[str, RulingRead] = {}
    for r in reads:
        latest[r.author.identity] = r
    return tuple(sorted(latest.values(), key=lambda r: r.order))


def _speaks(reads: Sequence[RulingRead]) -> tuple[RulingRead, ...]:
    # each author's latest ruling first, then whether it is live: one that
    # lapsed by its file or was withdrawn takes its author's earlier ones
    # with it, never handing the claim back to them
    live = [r for r in _latest_each([r for r in reads if not r.proposed]) if r.live]
    # a review's ruling under the question its brief asks now replaces the
    # agents' under an older one, whoever filed them
    if any(r.question is not None and not r.outdated for r in live):
        live = [r for r in live if not r.outdated]
    persons = [r for r in live if r.author.person]
    return tuple(persons or live)


def rulings(
    store: RecordStore, kept: State, reader: Reader | None = None
) -> dict[Subject, Ruled]:
    """Each claim's rulings that bear on `kept` (made on it or on a state
    along its parents), walked there, by the claim as it reads there. A
    ruling lapses only when its claim ends or its words change, or a file
    it read a page of prints other content, or is absent or held back
    (`judgments.sourced`): it is a call on those words, and on those
    pages. One a review filed under an older ruling question
    than its brief asks now stands until a review's ruling under the
    current one replaces it."""
    reader = reader or kept_reader(store)
    records = store.judgment_records()
    held = records.rulings
    if not held:
        return {}
    withdrawn = {w.ruling: w for w in records.withdrawals}
    answers = store.authoritative()
    filed = store.filed_order()
    seen_files = [(filed.get(f.answer or "", NOT_FILED), f) for f in records.files]
    contents = judgments.file_contents(records.files)
    by: dict[Subject, list[RulingRead]] = {}
    for r in held:
        walked = judgments.walk(
            reader,
            r.state,
            judgments.NO_HOP,
            [r.subject.node],
            (),
            kept,
            r.subject.words,
        )
        if walked is None:
            continue
        if (
            not walked.lapsed
            and walked.words is not None
            and norm(walked.words) != norm(r.subject.words or "")
        ):
            node = walked.nodes[0] or r.subject.node
            walked = replace(
                walked,
                causes=(
                    Cause(
                        CauseKind.TEXT,
                        r.state,
                        kept.id,
                        BasisElement(BasisKind.NODE, node),
                        f'the claim ruled on, "{r.subject.words}", now reads '
                        f'"{walked.words}"',
                        {"words": r.subject.words},
                    ),
                ),
            )
        order = filed.get(r.answer or "", NOT_FILED)
        if r.pages and not r.proposed:
            # a ruling rests on the files it read a page of, as a reading
            # does; a proposal's pages are read again by the review that
            # settles it, so a changed file never drops it unsettled
            walked = replace(
                walked,
                causes=walked.causes
                + tuple(judgments.sourced(r, order, seen_files, contents, kept.id)),
            )
        answer = answers.get(r.answer or "")
        read = RulingRead(
            r,
            judgments.author_of(answers, r.answer),
            order,
            walked,
            withdrawn.get(r.id),
            None if answer is None else rf.asked_version(answer, RULING),
        )
        by.setdefault(read.subject, []).append(read)
    reader.keep()
    found = {}
    for on, reads in by.items():
        reads.sort(key=lambda r: r.order)
        found[on] = Ruled(on, tuple(reads), _speaks(reads), _proposal(reads))
    return found


def _proposal(reads: Sequence[RulingRead]) -> ProposedRuling | None:
    """The latest proposal among a claim's rulings, in filing order, and its
    re-ask: the first ruling filed after it that is no proposal."""
    proposals = [r for r in reads if r.proposed]
    if not proposals:
        return None
    latest = proposals[-1]
    later = (r for r in reads if filed_after(r.order, latest.order) and not r.proposed)
    return ProposedRuling(latest, next(later, None))


# --- a settled finding on a ruled claim's words -------------------------------------

#: The answers that say a finding's problem is gone or none: an `owed`
#: ruling on its words says why it stands despite them.
SETTLING = (FindingStatus.FIXED, FindingStatus.DISMISSED)


def on_words(f: FindingRead, on: Subject) -> bool:
    """Whether a finding sits on a claim's words: a part or a span anchor
    on its block, walked to the state read, whose words hold the claim's,
    or the claim's hold them."""
    words = norm(on.words or "")
    placed = [a for a in f.finding.anchors if a.node is not None]
    for anchor, node in zip(placed, f.walked.nodes, strict=False):
        said = norm(anchor.words or "")
        if (
            anchor.kind in (AnchorKind.PART, AnchorKind.SPAN)
            and node == on.node
            and said
            and words
            and (said in words or words in said)
        ):
            return True
    return False


def settled_on(findings: Sequence[FindingRead], on: Subject) -> tuple[FindingRead, ...]:
    """The live findings settled `fixed` or `dismissed` that sit on the
    claim's words (`on_words`): an `owed` ruling on them says why it
    stands despite that answer (`docs/model.md` § 8)."""
    return tuple(
        f
        for f in findings
        if f.live
        and f.reaffirmed is None
        and f.answer is not None
        and f.answer.status in SETTLING
        and on_words(f, on)
    )


# --- a claim's support --------------------------------------------------------------


class Support(StrEnum):
    """Whether the paper argues a claim it asserts, or owes it no argument
    (`docs/model.md` § 8): computed, never stored."""

    SUPPORTED = "supported"
    NO_ARGUMENT_OWED = "no-argument-owed"
    #: a referee would want it argued, and a person leaves it so
    LEFT_UNARGUED = "left-unargued"
    UNSUPPORTED = "unsupported"


class Guard(StrEnum):
    """Why a clearing ruling that speaks clears nothing (§ 8)."""

    #: the paper promises the argument
    PROMISED = "promised"
    #: agents alone ruled, and readers differ on whether the claim holds
    TRUE_DISPUTED = "true-disputed"
    #: agents alone ruled it `known` or `immediate`, and no reading showed it
    TRUE_NOT_HELD = "true-not-held"
    #: `immediate`, but its block uses no result of the paper's
    NO_RESULT_USED = "no-result-used"
    #: an `interpretation`, a `positioning` or a `left-unargued` something
    #: relies on
    RELIED_ON = "relied-on"


@dataclass(frozen=True)
class ClaimSupport:
    """A claim's support status at a state: its value; for an unsupported one its
    own gap (`unsupported`, or `circular`); its rulings, what they would
    clear it as, and the guard that stopped them; what relies on it, for
    an `interpretation`'s guard."""

    status: Support
    gap: Missing | None = None
    ruled: Ruled | None = None
    ruling: RulingKind | None = None
    guard: Guard | None = None
    relied: tuple[str, ...] = ()
    #: for a restatement of a conjecture, each conjecture it restates, by
    #: handle, and whether only a fallback reaches them all
    conjectures: tuple[str, ...] = ()
    by_fallback: bool = False
    #: for a claim standing unsupported, what the paper declares of the
    #: argument it does not give, and whether its `true` is held verified
    declared: Declared | None = None
    true_verified: bool = False

    @property
    def by_person(self) -> bool:
        return self.ruled is not None and self.ruled.by_person

    @property
    def owed(self) -> RulingRead | None:
        """The weightiest `owed` ruling that speaks for it, where its support
        status is `unsupported`: the highest severity, then the latest."""
        if self.status is not Support.UNSUPPORTED or self.ruled is None:
            return None
        owed = [r for r in self.ruled.speaks if r.ruling.kind is RulingKind.OWED]
        return max(owed, key=lambda r: (r.ruling.severity or 0, r.order), default=None)

    @property
    def weight(self) -> tuple[int, int]:
        """What sorts the owed claims, heaviest first: its owed ruling's
        severity, then how many rely on it in one hop; (0, 0) where no owed
        ruling speaks. It changes no gate (`docs/model.md` § 8)."""
        owed = self.owed
        if owed is None:
            return (0, 0)
        return (owed.ruling.severity or 0, len(self.relied))


def heaviest_first[T](
    items: Sequence[T], weight: Callable[[T], tuple[int, int]]
) -> list[T]:
    """`items` with the heavier owed claims first (`ClaimSupport.weight`),
    their order kept otherwise."""
    return sorted(items, key=lambda i: tuple(-w for w in weight(i)))


def support(
    standing: Status,
    gap: Missing | None,
    ruled: Ruled | None,
    *,
    declared: Declared | None = None,
    true_disputed: bool = False,
    true_verified: bool = False,
    uses_a_result: bool = False,
    relied: tuple[str, ...] = (),
    conjectures: tuple[str, ...] = (),
    by_fallback: bool = False,
) -> ClaimSupport | None:
    """A claim's support status from its standing, its own gap (a justified claim
    no justifies edge reaches, a restatement restating nothing, a circle),
    its rulings, what the paper declares of its argument, its `true` held
    `verified` or disputed, whether its block uses a result, and what
    relies on it. None for a `conjectured` claim: the paper does not assert
    it. A circle is never cleared: it rests on what rests on it. A
    restatement of a conjecture (`conjectures`) asserts what the paper only
    conjectures: unsupported whatever its route reads, as a claim standing
    unsupported is listed by its standing, never by its truth."""
    if standing is Status.CONJECTURED:
        return None
    if standing is not Status.UNSUPPORTED:
        if gap is None and conjectures:
            return ClaimSupport(
                Support.UNSUPPORTED,
                Missing.UNSUPPORTED,
                conjectures=conjectures,
                by_fallback=by_fallback,
            )
        if gap is None:
            return ClaimSupport(Support.SUPPORTED)
        return ClaimSupport(Support.UNSUPPORTED, gap)
    kind = None if ruled is None else ruled.clears
    said = partial(ClaimSupport, declared=declared, true_verified=true_verified)
    if gap is Missing.CIRCULAR:
        return said(Support.UNSUPPORTED, gap, ruled, kind)
    by_agents = ruled is not None and not ruled.by_person
    guard = None
    if kind is None:
        pass
    elif declared is Declared.PROMISED:
        guard = Guard.PROMISED
    elif by_agents and true_disputed:
        guard = Guard.TRUE_DISPUTED
    elif kind in SHOWN and by_agents and not true_verified:
        guard = Guard.TRUE_NOT_HELD
    elif kind is RulingKind.IMMEDIATE and not uses_a_result:
        guard = Guard.NO_RESULT_USED
    elif kind in NO_PREMISE and relied:
        guard = Guard.RELIED_ON
    elif kind is RulingKind.LEFT_UNARGUED:
        return said(Support.LEFT_UNARGUED, None, ruled, kind)
    else:
        return said(Support.NO_ARGUMENT_OWED, None, ruled, kind)
    return said(Support.UNSUPPORTED, Missing.UNSUPPORTED, ruled, kind, guard, relied)
