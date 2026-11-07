"""The update driver's cut review: a first build is an update from an empty
store.

It builds the boundary questions, a session per chunk, over the
cutter's cut and, with a runner, asks them; applies their authoritative
answers' corrections, in the questions' order, to the cut; then, with
`judge` set, in the same round, builds the judge's questions over the cut
the author left, with the author's edits flagged, asks them and
applies their reverts after the author's; and goes on to the next round,
up to `rounds`, while every chunk of the round is answered and the round
changed the cut. Each judge's declines are settled: they join the ledger
every later author pass is shown, where they apply. The judge is off by
default. The roles
are then built on the result, asked once the boundary stage is
settled: a session per chunk on `roles.md`, then any review passes; their
corrections give the blocks their roles. The groups are a
session per chunk on `groups.md`, built on the cut they leave and asked
once the stages before them are settled; their corrections are applied
after, and any later pass (`group_passes`) is built on the cut the passes
before it left, while a pass still changes the groups. The edges
are one session per paper on `edges.md`, built on the cut the groups left
when their stage is asked, and asked once the stages before them are
settled; any later pass (`edge_passes`) reviews the sheet the pass
before it left, while a pass still changes the records; the last answered
pass's records are the review's edge half, and the role corrections its
standings imply are applied with it (a standing on a block with
no claim role adds a claim part), never weighed. Its cut corrections are then
weighed: up to `cut_rounds` rounds, each applies those that
neither fail nor reverse an earlier correction, asks the roles of the
blocks they changed and the edges again on the sheet carried onto the new
cut, the blocks they touch reopened, while a round applies any; the
re-ask's records are then the edge half, and its proposals the next
round's.
A given cut (`--cut`) stands in place of the cutter's cut and the
boundary stage: no boundary pass is built on it.
Without a runner it asks nothing: the corrected cut from the store alone,
which must equal the run's byte for byte.
"""

from __future__ import annotations

import dataclasses
from collections import Counter
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field, replace
from functools import partial
from typing import Any

from axiomation import person
from axiomation.assembler import Corrected, corrected_cut
from axiomation.changes import Layer
from axiomation.cutter.model import Block, Cut
from axiomation.exchange import (
    Authors,
    Link,
    answer_link,
    corrected_payload,
    correction_payload,
    edges_payload,
    flag_payload,
    line_authors,
    person_review,
)
from axiomation.flags import Raised, rule_flags, vocabulary_flags
from axiomation.person import EdgePins, Pin, Pinned
from axiomation.questions import (
    Question,
    Stage,
    Template,
    Validation,
    cut_file,
    cut_review,
    decide_file,
    edges_file,
    groups_file,
    load_template,
    roles_file,
    vocabulary_file,
)
from axiomation.questions.chunks import DEFAULT_CHUNK, DEFAULT_OVERLAP, chunks
from axiomation.questions.cut_file import (
    FILE_TEMPLATE,
    JUDGE_TEMPLATE,
    Insertion,
    file_questions,
    judge_questions,
)
from axiomation.questions.roles_file import (
    REVIEW_TEMPLATE,
    SESSION_TEMPLATE,
    session_questions,
)
from axiomation.reader import Document, MapAnswer, Vocabulary, document_hash
from axiomation.records import (
    BLOCK_OPS,
    PERSON,
    AnswerRecord,
    CutCorrection,
    CutOp,
    EdgeRecords,
    RecordStore,
    Signal,
)
from axiomation.run_state import RunFile, text_of
from axiomation.runner import Check, Runner, RunReport


@dataclass(frozen=True, slots=True)
class Templates:
    """The prompt template each stage's sessions are asked under: the
    author's and the judge's, the roles' first pass and its reviews, the
    groups', the edges', the edges' later passes, their re-ask after a
    round of cut corrections and an update's session.
    A profile chooses them; the shipped ones by default.
    A template's version is in its questions' keys, so
    answers under two templates never share a key. The author's passes,
    the roles' first pass and the groups' sessions answer as lines
    (`front_lines`): the roles' and the groups' templates are briefs for
    lines, and the author's is followed by the lines' section."""

    boundaries: Template
    judge: Template
    roles: Template
    roles_review: Template
    groups: Template
    edges: Template
    edges_review: Template
    edges_reask: Template
    decide: Template
    #: An update's edges session.
    edges_update: Template
    #: The vocabulary map's session, and its second round on what it
    #: answered `none`.
    vocabulary: Template = field(
        default_factory=lambda: load_template(vocabulary_file.SESSION_TEMPLATE)
    )
    vocabulary_check: Template = field(
        default_factory=lambda: load_template(vocabulary_file.CHECK_TEMPLATE)
    )

    @classmethod
    def shipped(cls) -> Templates:
        return cls(
            load_template(FILE_TEMPLATE),
            load_template(JUDGE_TEMPLATE),
            load_template(SESSION_TEMPLATE),
            load_template(REVIEW_TEMPLATE),
            load_template(groups_file.SESSION_TEMPLATE),
            load_template(edges_file.SESSION_TEMPLATE),
            load_template(edges_file.REVIEW_TEMPLATE),
            load_template(edges_file.REASK_TEMPLATE),
            load_template(decide_file.TEMPLATE),
            load_template(edges_file.UPDATE_TEMPLATE),
        )


@dataclass(frozen=True, slots=True)
class ReviewSettings:
    """`chunk`: the owned characters a chunk holds at most, `None` for the
    whole paper as one; `overlap`: the context blocks on either side of a
    cut between blocks; `rounds`: rounds of an author pass, then a judge
    pass where `judge` is set, at most (one by default). The
    boundaries alone by default."""

    chunk: int | None = DEFAULT_CHUNK
    overlap: int = DEFAULT_OVERLAP
    rounds: int = 1
    #: Whether each author pass is judged.
    judge: bool = False
    stages: tuple[Stage, ...] = (Stage.BOUNDARIES,)
    #: Whether each session gets `outline.md` beside its `cut.md`.
    outline: bool = True
    #: Passes of the roles session: the first gives the roles, each later
    #: one reviews them (`--role-passes`).
    role_passes: int = 1
    #: Passes of the groups session: each later one corrects the groups the
    #: one before it left, while a pass still changes them (`--group-passes`;
    #: one by default).
    group_passes: int = 1
    #: Passes of the edges session: each later one reviews the sheet the one
    #: before it left, while a pass still changes the records (`--edge-passes`;
    #: one by default).
    edge_passes: int = 1
    #: Rounds of cut corrections after the edges (`--cut-rounds`):
    #: each applies the edge half's proposals, then asks the roles of the
    #: blocks they changed and the edges they touch again. None by default.
    cut_rounds: int = 0
    #: Passes of the deciding step after the groups (`--decide-passes`): 0 or
    #: 1, none by default.
    decide_passes: int = 0
    #: Whether each later edges pass is shown the rules' flags on the
    #: edges the pass before it wrote (off by default).
    show_rule_flags_in_edge_review: bool = False
    #: The paper's files on disk, which an edge may target
    #: (`edge_sheet.paper_files`).
    files: tuple[str, ...] = ()
    #: Whether the cut is given (`--cut`), its boundaries settled: no
    #: boundary pass is built on it.
    given_cut: bool = False
    templates: Templates = field(default_factory=Templates.shipped)


def checker(document: Document) -> Check:
    """Each stage's check: a session's edited file, or a JSON answer."""

    def check(question: Question, text: str) -> Validation:
        if question.stage in (Stage.VOCABULARY, Stage.VOCABULARY_CHECK):
            return vocabulary_file.check(question, text, document)
        if question.stage is Stage.DECIDE:
            return decide_file.check(question, text, document)
        if question.stage is Stage.ROLES:
            return roles_file.check(question, text, document)
        if question.kind == groups_file.KIND:
            return groups_file.check(question, text, document)
        if question.kind == edges_file.KIND:
            return edges_file.check(question, text, document)
        if question.workspace is not None:
            return cut_file.check(question, text, document)
        return cut_review.check(question, text, document)

    return check


#: The rules a later edges pass may be shown.
EDGE_RULES = frozenset({Signal.QUALIFIES, Signal.DEFINITION_USES_CLAIM})
#: How a proposal that reverses an earlier correction without evidence is
#: reported, and one that would change a boundary changed twice already.
DROPPED = "dropped: it reverses an earlier correction of the cut at "
DROPPED_TWICE = "dropped: the boundary has changed twice already at "
#: Why an applied proposal was taken back: its round's roles session or
#: edges re-ask went unanswered.
ROLES_UNANSWERED = "not applied: the round's roles session is unanswered"
EDGES_UNANSWERED = "not applied: the round's edges re-ask is unanswered"
#: Why the deciding step's corrections were taken back: the roles of the
#: blocks they changed went unanswered.
STEP_ROLES_UNANSWERED = "not applied: the deciding step's roles session is unanswered"
#: Why a proposal was not applied where the build asks no round.
NO_ROUND = "not applied: the build asks no round of cut corrections (cut_rounds 0)"
#: Why a model correction was dropped: it would change what a person
#: pinned.
PINNED = "dropped: pinned by a person at "
#: Why a person round's re-ask's cut lines are listed and not applied, and
#: why a person's corrections were taken back.
PERSON_NO_CUT = "not applied: a person's round applies no model correction of the cut"
PERSON_ROLES_UNANSWERED = "not applied: the person round's roles session is unanswered"
PERSON_EDGES_UNANSWERED = "not applied: the person round's edges re-ask is unanswered"
#: Who a role correction an edges standing implied is by, in
#: `review.cut_corrections` (the edges' role correction).
BY_EDGES = Stage.EDGES.value
#: How a person's roles file marks a block whose claim role the edges added.
CLAIM_ADDED_NOTE = "claim added by edges"


@dataclass(frozen=True, slots=True)
class Proposal:
    """A cut correction a later stage proposed: the round
    that weighed it, the stage that proposed it, whether it was applied and
    why not, the blocks it named as they stood on the cut it was proposed
    on, the blocks it left (none where it was not applied), and the block
    starts it changed back, a second time. A person's
    correction is `by` the person, and its round is its
    person round."""

    correction: CutCorrection
    round: int
    stage: str
    applied: bool
    problem: str | None
    before: tuple[Block, ...]
    after: tuple[str, ...] = ()
    reverses: tuple[str, ...] = ()
    by: str | None = None


@dataclass(frozen=True, slots=True)
class PersonRound:
    """A person's answer applied: its person round, its
    key and stage, its pins, the keys its round asked (the touched roles,
    the edges re-ask) and the owners its re-ask reopened, by handle."""

    number: int
    key: str
    stage: str
    pins: tuple[Pin, ...] = ()
    asked: tuple[str, ...] = ()
    reopened: tuple[str, ...] = ()
    #: Who gave it: a person, or an agent, whose answer pins
    #: nothing.
    by: str = PERSON


@dataclass(frozen=True, slots=True, eq=False)
class PersonRounds:
    """A run's person rounds: the keys of the store's
    person answers for the version, the rounds applied, the answers that
    match no state (stale), the round taken back with its question still
    to ask (`pending`), and the pins the applied answers hold on the final
    state, a block's or a group's by its id. False where the store holds no
    person answer for the version."""

    answers: tuple[str, ...] = ()
    applied: tuple[PersonRound, ...] = ()
    stale: tuple[str, ...] = ()
    pending: PersonRound | None = None
    pins: tuple[Pin, ...] = ()
    #: The last model's edges answer, whose flags stand where a person's
    #: answer is the edge half (a person's answer raises none).
    flags_from: AnswerRecord | None = None
    #: The blocks a person's cut edit removed: a pin on one lapses.
    cut_away: frozenset[str] = frozenset()

    def __bool__(self) -> bool:
        return bool(self.answers)


@dataclass(frozen=True, slots=True, eq=False)
class KeptRounds:
    """The given answers a kept state applied already, for a step
    from it: its rounds, each with its pins, its edges pins as pairs of
    sheets on its cut (`EdgePins`), and the answers the step applies after
    them, numbered on from its last round."""

    rounds: tuple[PersonRound, ...]
    sources: tuple[EdgePins, ...]
    answers: tuple[AnswerRecord, ...]
    #: The answers the state found stale, listed again as a replay lists
    #: them.
    stale: tuple[str, ...] = ()
    #: The texts of `answers`, as their author gave them, by key: the step
    #: applies them from these, not from the store.
    texts: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CutRound:
    """A round of cut corrections that applied any: its number, the blocks
    the corrections made or changed, and the sheet's blocks and groups the
    edges' re-ask reopened, by handle."""

    number: int
    touched: tuple[str, ...]
    reopened: tuple[str, ...]


@dataclass(frozen=True, slots=True, eq=False)
class Review:
    """The corrected cut; the questions per stage (the boundaries judge's
    apart, where it was asked); the keys with no authoritative
    answer; the keys that failed in this run; the run's reports, one per
    stage asked; the edge stage's records with the answer they came from,
    when it was asked and answered; the cut corrections the
    edges proposed, in the order weighed, with the rounds that applied any; and the
    flags the stages raised and the rules'."""

    corrected: Corrected
    questions: dict[Stage, tuple[Question, ...]]
    unanswered: tuple[str, ...]
    failed: tuple[str, ...] = ()
    reports: tuple[RunReport, ...] = field(default_factory=tuple)
    edges: EdgeRecords = EdgeRecords()
    edges_answer: AnswerRecord | None = None
    proposals: tuple[Proposal, ...] = ()
    cut_rounds: tuple[CutRound, ...] = ()
    flags: tuple[Raised, ...] = ()
    #: The key of the edges' answer carried onto the deciding step's cut,
    #: where the store held one on the cut before it.
    edges_carried_from: str | None = None
    #: The flags the deciding step was shown.
    decide_flags: tuple[Raised, ...] = ()
    #: The person rounds.
    person: PersonRounds = PersonRounds()
    #: Each line of the edge half with the provenance of the answer that
    #: first wrote it.
    line_authors: Authors = field(default_factory=dict)

    @property
    def complete(self) -> bool:
        return not self.unanswered

    @property
    def stopped(self) -> RunReport | None:
        """The report of the stage the run stopped in, if it stopped."""
        return next((r for r in self.reports if r.stopped is not None), None)


PRODUCER = "axiomation review-cut"


def review_payload(
    document: Document, review: Review, settings: ReviewSettings
) -> dict[str, Any]:
    """The corrected cut in the exchange format, with the
    edge half before its `review` when the edge stage was answered; `review` holds
    nothing a replay could not give: no spend, no failure, no time."""
    # Every template a stage was asked under: a later pass's (the roles'
    # and the edges' review) is its own.
    templates = {
        q.template.name: q.template.version
        for qs in review.questions.values()
        for q in qs
    }
    edges_question = (
        None
        if review.edges_answer is None
        else next(
            q for q in review.questions[Stage.EDGES] if q.key == review.edges_answer.key
        )
    )
    handle_of = (
        {}
        if edges_question is None
        else {ident: handle for handle, ident in edges_question.handles.items()}
    )
    payload = corrected_payload(
        document,
        review.corrected,
        producer=PRODUCER,
        review={
            "complete": review.complete,
            "chunk": "paper" if settings.chunk is None else settings.chunk,
            "overlap": settings.overlap,
            "rounds": settings.rounds,
            "judge": settings.judge,
            "outline": settings.outline,
            "cut": "given" if settings.given_cut else "cutter",
            "role_passes": settings.role_passes,
            "group_passes": settings.group_passes,
            "edge_passes": settings.edge_passes,
            "cut_rounds": settings.cut_rounds,
            **(
                {"decide_passes": settings.decide_passes}
                if settings.decide_passes
                else {}
            ),
            **(
                {"show_rule_flags_in_edge_review": True}
                if settings.show_rule_flags_in_edge_review
                else {}
            ),
            "stages": [stage.value for stage in settings.stages],
            "templates": templates,
            "questions": {
                stage.value: len(qs) for stage, qs in review.questions.items()
            },
            "unanswered": list(review.unanswered),
            **cut_corrections_payload(document, review.proposals),
            **(
                {"edges_carried_from": review.edges_carried_from}
                if review.edges_carried_from
                else {}
            ),
            "flags": [
                flag_payload(
                    document, raised, handle_of, decided_by(raised, review.proposals)
                )
                for raised in review.flags
            ],
            # only where the store holds a person's answer for the version
            **({"person": person_payload(review.person)} if review.person else {}),
        },
    )
    if review.edges_answer is None:
        return payload
    question = edges_question
    assert question is not None and question.input_cut is not None
    edge_half = edges_payload(
        document,
        question.input_cut,
        review.edges,
        review.edges_answer,
        question.handles,
        review.line_authors,
    )
    held = payload.pop("review")
    return {**payload, **edge_half, "review": held}


def cut_corrections_payload(
    document: Document, proposals: Sequence[Proposal]
) -> dict[str, Any]:
    """`review.cut_corrections`, a build's and an update's alike: every
    cut correction a stage proposed or a person gave, in the order weighed
    (and its `by`); nothing where there is none."""
    if not proposals:
        return {}
    return {
        "cut_corrections": [
            correction_payload(
                document,
                p.correction,
                round_=p.round,
                stage=p.stage,
                applied=p.applied,
                problem=p.problem,
                before=p.before,
                after=p.after,
                reverses=p.reverses,
                by=p.by,
            )
            for p in proposals
        ]
    }


def person_payload(rounds: PersonRounds, carried: Sequence[Pin] = ()) -> dict[str, Any]:
    """`review.person`: the rounds of the answers given
    by a person or an agent applied, the answers stale, and every
    pin with its state, `carried` (an update's, from OLD) first."""
    return person_review(
        applied=[
            {"key": r.key, "stage": r.stage, "round": r.number, "by": r.by}
            for r in rounds.applied
        ],
        stale=list(rounds.stale),
        pins=[
            {
                "layer": p.layer.value,
                "block": p.owner,
                "kind": p.kind.value,
                "line": p.line,
                "state": p.state,
                "rank": p.rank,
            }
            for p in (*carried, *rounds.pins)
        ],
    )


def _pass(
    document: Document,
    cut: Cut,
    settings: ReviewSettings,
    number: int,
    ledger: tuple[Insertion, ...] = (),
) -> tuple[Question, ...]:
    return file_questions(
        document,
        cut,
        settings.chunk,
        settings.templates.boundaries,
        settings.overlap,
        number,
        settings.outline,
        ledger,
        lines=True,
    )


def _judge(
    document: Document,
    cut: Cut,
    before: Cut,
    author_keys: Sequence[str],
    settings: ReviewSettings,
) -> tuple[Question, ...]:
    return judge_questions(
        document,
        cut,
        before,
        author_keys,
        settings.chunk,
        settings.templates.judge,
        settings.overlap,
        settings.outline,
    )


def planned_questions(
    document: Document, cut: Cut, settings: ReviewSettings
) -> dict[Stage, tuple[Question, ...]]:
    """The questions a run would start with, for the stages it asks
    (`--dry-run`): the first author pass, the judge's questions on the
    uncorrected cut where the run judges (for pricing; a judge's file shows
    the author's edits once there are any), the roles questions, every
    pass, the groups session on the uncorrected cut, the deciding step and
    what it asks again, and each round of cut corrections at an
    upper bound."""
    found: dict[Stage, tuple[Question, ...]] = {}
    if Stage.VOCABULARY in settings.stages:
        first = vocabulary_file.vocabulary_question(
            document, settings.templates.vocabulary
        )
        if first is not None:
            found[Stage.VOCABULARY] = (first,)
    if Stage.BOUNDARIES in settings.stages and not settings.given_cut:
        found[Stage.BOUNDARIES] = _pass(document, cut, settings, 1)
        if settings.judge:
            found[Stage.JUDGE] = _judge(document, cut, cut, (), settings)
    if Stage.ROLES in settings.stages:
        found[Stage.ROLES] = tuple(
            q
            for n in range(1, settings.role_passes + 1)
            for q in _roles(document, cut, settings, n)
        )
    if Stage.GROUPS in settings.stages:
        found[Stage.GROUPS] = tuple(
            q
            for n in range(1, settings.group_passes + 1)
            for q in _groups(document, cut, settings, n)
        )
    if settings.decide_passes and Stage.GROUPS in settings.stages:
        # The deciding step, its file a boundary pass's with more shown;
        # then, at an upper bound, the roles of a twentieth of the blocks
        # and the groups once more.
        found[Stage.DECIDE] = _decide(document, cut, settings, ())
        some = [b.id for b in cut.blocks[: max(len(cut.blocks) // 20, 1)]]
        found[Stage.ROLES] = (
            *found.get(Stage.ROLES, ()),
            roles_file.touched_question(
                document, cut, some, settings.templates.roles, settings.overlap, 0
            ),
        )
        found[Stage.GROUPS] = (
            *found.get(Stage.GROUPS, ()),
            *_groups(document, cut, settings, settings.group_passes + 1),
        )
    if Stage.EDGES in settings.stages:
        first = _edges(document, cut, settings)
        assert first.workspace is not None
        # A later pass is priced on the sheet as rendered: its own carries
        # the edges the pass before it wrote, so it costs somewhat more.
        sheet = first.workspace.files[edges_file.EDGES_FILE]
        found[Stage.EDGES] = (
            first,
            *(
                _edges_review(document, cut, settings, sheet, first.key, n)
                for n in range(2, settings.edge_passes + 1)
            ),
            # A round's re-ask, priced as a review: the whole sheet is read.
            *(
                edges_file.reask_question(
                    document,
                    cut,
                    settings.templates.edges,
                    settings.templates.edges_reask,
                    settings.files,
                    edges_file.Carried(sheet, (), {}),
                    n,
                    (),
                    settings.chunk,
                )
                for n in range(1, settings.cut_rounds + 1)
            ),
        )
        if settings.cut_rounds:
            # A round's roles, priced on a twentieth of the blocks: a
            # round's size is not known before its proposals.
            some = [b.id for b in cut.blocks[: max(len(cut.blocks) // 20, 1)]]
            found[Stage.ROLES] = (
                *found.get(Stage.ROLES, ()),
                *(
                    roles_file.touched_question(
                        document,
                        cut,
                        some,
                        settings.templates.roles,
                        settings.overlap,
                        n,
                    )
                    for n in range(1, settings.cut_rounds + 1)
                ),
            )
    return found


def _decide(
    document: Document, cut: Cut, settings: ReviewSettings, flags: Sequence[Raised]
) -> tuple[Question, ...]:
    return decide_file.decide_questions(
        document,
        cut,
        chunks(document, cut, settings.chunk, settings.overlap),
        settings.templates.decide,
        flags,
        settings.outline,
    )


def _edges(document: Document, cut: Cut, settings: ReviewSettings) -> Question:
    return edges_file.session_question(
        document, cut, settings.templates.edges, settings.files, settings.chunk
    )


def _edges_review(
    document: Document,
    cut: Cut,
    settings: ReviewSettings,
    previous: str,
    follows: str,
    number: int,
    shown_rules: Sequence[Raised] = (),
) -> Question:
    return edges_file.review_question(
        document,
        cut,
        settings.templates.edges,
        settings.templates.edges_review,
        settings.files,
        previous,
        follows,
        number,
        settings.chunk,
        shown_rules,
    )


def _same_records(a: EdgeRecords, b: EdgeRecords) -> bool:
    """Whether two passes' records say the same, their ids and answers aside."""

    def bare(records: EdgeRecords) -> list[Any]:
        return [dataclasses.replace(r, id="", answer=None) for r in records.all()]

    return bare(a) == bare(b)


def _roles(
    document: Document, cut: Cut, settings: ReviewSettings, number: int = 1
) -> tuple[Question, ...]:
    return session_questions(
        document,
        cut,
        settings.chunk,
        settings.templates.roles if number == 1 else settings.templates.roles_review,
        settings.overlap,
        number,
    )


def _groups(
    document: Document, cut: Cut, settings: ReviewSettings, number: int = 1
) -> tuple[Question, ...]:
    return groups_file.session_questions(
        document,
        cut,
        settings.chunk,
        settings.templates.groups,
        settings.overlap,
        number,
        lines=True,
    )


@dataclass(frozen=True, slots=True)
class Mapped:
    """A paper's vocabulary map as the store holds it: `vocabulary`, `None`
    while a question it needs has no authoritative answer; the questions
    it took, and the reports of those this run asked."""

    vocabulary: Vocabulary | None
    questions: tuple[Question, ...] = ()
    reports: tuple[RunReport, ...] = ()
    #: The given answers on the version whose shown map no longer matches,
    #: by key: not applied.
    stale: tuple[str, ...] = ()
    #: A person's answers the map no longer carries, their names' declaring
    #: text changed or the names gone.
    lapsed: tuple[MapAnswer, ...] = ()
    #: The given answers on the version laid over the map, by key.
    given: tuple[str, ...] = ()


def map_vocabulary(
    document: Document,
    store: RecordStore,
    templates: Templates,
    runner: Runner | None = None,
    kept: Vocabulary | None = None,
    waiting: bool = False,
) -> Mapped:
    """The paper's vocabulary map (`docs/architecture.md` § 2): the answers
    of `kept`, the map of the version an update starts from, that still
    hold (`vocabulary_file.carried`); its session on the candidates of
    `document`, read without a map, that they do not answer; and the second
    round on what that answered `none`. Each asked through `runner` where
    the store has no answer, else read from it. A paper whose every
    candidate is answered asks nothing. Over it, the maps a person or an
    agent gave on the version, in store order (`_given_maps`); `waiting`:
    the person's maps that wait for their word too, as an edit's update
    reads them."""
    carried = vocabulary_file.carried(document, kept)
    lapsed = tuple(
        a
        for a in (kept.answers if kept is not None else ())
        if a.by == PERSON and a not in carried
    )
    first = vocabulary_file.vocabulary_question(
        document, templates.vocabulary, Vocabulary(answers=carried)
    )
    if first is None:
        return _given_maps(
            document, store, Mapped(Vocabulary.of(carried), lapsed=lapsed), waiting
        )
    check = checker(document)
    reports: list[RunReport] = []

    def answered(question: Question) -> AnswerRecord | None:
        if runner is not None and question.key not in store.authoritative():
            reports.append(runner.run([question], check))
        return store.authoritative().get(question.key)

    if answered(first) is None:
        return Mapped(None, (first,), tuple(reports), lapsed=lapsed)
    answers = _vocabulary_read(store, first)
    second = vocabulary_file.check_question(
        document, answers, templates.vocabulary_check
    )
    if second is None:
        found = Mapped(
            vocabulary_file.mapped(answers, carried=carried),
            (first,),
            tuple(reports),
            lapsed=lapsed,
        )
        return _given_maps(document, store, found, waiting)
    if answered(second) is None:
        return Mapped(None, (first, second), tuple(reports), lapsed=lapsed)
    verdicts = _vocabulary_read(store, second)
    found = Mapped(
        vocabulary_file.mapped(answers, verdicts, carried),
        (first, second),
        tuple(reports),
        lapsed=lapsed,
    )
    return _given_maps(document, store, found, waiting)


def _vocabulary_read(
    store: RecordStore, question: Question
) -> tuple[vocabulary_file.Answer, ...]:
    """The entries of `question`'s answer as its check read them when it was
    filed (its `vocabulary` records)."""
    return vocabulary_file.answers_of(store.vocabulary_of(question.key))


def _given_maps(
    document: Document, store: RecordStore, found: Mapped, waiting: bool = False
) -> Mapped:
    """`found` with each map a person or an agent gave on the version laid
    over it, in store order: it applies where the map before it shows the
    file it was shown (`AnswerRecord.shown`), else it is stale. The second
    round never runs over a given map."""
    vocabulary = found.vocabulary
    stale: list[str] = []
    applied: list[str] = []
    for given in store.given_answers(document_hash(document), waiting):
        if given.stage != Stage.VOCABULARY.value:
            continue
        question = person.vocabulary_question(document, vocabulary, "", given.host)
        holds = question is not None and person.shown_hash(question) == given.shown
        if question is None or not holds:
            stale.append(given.key)
            continue
        question = dataclasses.replace(question, key=given.key)
        vocabulary = vocabulary_file.given_map(
            _vocabulary_read(store, question),
            vocabulary,
            given.host,
        )
        applied.append(given.key)
    return dataclasses.replace(
        found, vocabulary=vocabulary, stale=tuple(stale), given=tuple(applied)
    )


def with_vocabulary(review: Review, mapped: Mapped, store: RecordStore) -> Review:
    """The review with the vocabulary map's questions and reports, so its
    summary, its payload's templates and its completeness count them."""
    if not mapped.questions:
        return review
    answered = store.authoritative()
    by_stage = {
        stage: tuple(q for q in mapped.questions if q.stage is stage)
        for stage in (Stage.VOCABULARY, Stage.VOCABULARY_CHECK)
    }
    return replace(
        review,
        questions={**{s: qs for s, qs in by_stage.items() if qs}, **review.questions},
        unanswered=(
            *(q.key for q in mapped.questions if q.key not in answered),
            *review.unanswered,
        ),
        failed=(*(k for r in mapped.reports for k in r.failed), *review.failed),
        reports=(*mapped.reports, *review.reports),
    )


def may_ask(
    required: Sequence[Question], store: RecordStore, reports: Sequence[RunReport]
) -> bool:
    """Whether a later stage may be asked: every question before it answered
    or failed in this run, and the run not stopped."""
    failed = {key for report in reports for key in report.failed}
    answered = store.authoritative()
    stopped = any(report.stopped is not None for report in reports)
    return not stopped and all(q.key in answered or q.key in failed for q in required)


def _ledger(
    store: RecordStore, questions: tuple[Question, ...]
) -> tuple[Insertion, ...]:
    """The judge's declines, from its authoritative answers, for the
    authors' ledger: each answer's as its check settled them (its
    `declines` records)."""
    return tuple(
        Insertion(d.file, d.offset, d.line)
        for question in questions
        for d in store.declines_of(question.key)
    )


def review_cut(
    document: Document,
    cut: Cut,
    store: RecordStore,
    settings: ReviewSettings,
    runner: Runner | None = None,
    run: RunFile | None = None,
) -> Review:
    """The cut review. The author and judge passes and the roles passes are
    always built and their stored answers applied, so the groups session
    is the same whichever stages are asked; `settings.stages` chooses what
    is asked and what the result lists. A correction carries each block's
    handle with its node (`edges_file.named`). `run` is the run's working
    state: the edges sheets the passes stand on are read from it, and its
    cursor moves past each pass applied."""
    cut = edges_file.named(document, cut)
    check = checker(document)
    reports: list[RunReport] = []
    questions: dict[Stage, tuple[Question, ...]] = {}

    asking = runner is not None and Stage.BOUNDARIES in settings.stages
    authors: list[Question] = []
    judges: list[Question] = []
    corrections: list[CutCorrection] = []
    ledger: tuple[Insertion, ...] = ()
    after = cut
    rounds = 0 if settings.given_cut else settings.rounds
    for number in range(1, rounds + 1):
        before = after
        asked = _pass(document, before, settings, number, ledger)
        authors += asked
        if asking and asked:
            assert runner is not None
            reports.append(runner.run(asked, check))
        found = [c for q in asked for c in store.corrections_of(q.key)]
        corrections += found
        after = corrected_cut(document, cut, corrections).cut
        answered = store.authoritative()
        if not found or not all(q.key in answered for q in asked):
            break
        if any(report.stopped is not None for report in reports):
            break
        if not settings.judge:
            continue

        judging = _judge(document, after, before, [q.key for q in asked], settings)
        judges += judging
        if asking and judging:
            reports.append(runner.run(judging, check))
        reverts = [c for q in judging for c in store.corrections_of(q.key)]
        corrections += reverts
        after = corrected_cut(document, cut, corrections).cut
        answered = store.authoritative()
        if not all(q.key in answered for q in judging):
            break
        if any(report.stopped is not None for report in reports):
            break
        # Declines are settled: the ledger keeps every round's, once per place.
        held = {(i.file, i.offset) for i in ledger}
        ledger += tuple(
            i for i in _ledger(store, judging) if (i.file, i.offset) not in held
        )
        if not reverts and after == before:
            break
    if Stage.BOUNDARIES in settings.stages and not settings.given_cut:
        questions[Stage.BOUNDARIES] = tuple(authors)
        if judges:
            questions[Stage.JUDGE] = tuple(judges)
    # The judge's keys are required only where its stage is asked: a `--stage groups`
    # run does not wait on them.
    required: list[Question] = (
        [*authors, *judges] if Stage.BOUNDARIES in settings.stages else [*authors]
    )

    # The roles: a first pass, then any reviews, each built on the cut the
    # passes before it left.
    asked_roles: list[Question] = []
    for number in range(1, settings.role_passes + 1):
        found_roles = _roles(document, after, settings, number)
        asked_roles += found_roles
        if Stage.ROLES in settings.stages:
            if runner is not None and found_roles and may_ask(required, store, reports):
                reports.append(runner.run(found_roles, check))
            required += found_roles
        corrections += [c for q in found_roles for c in store.corrections_of(q.key)]
        after = corrected_cut(document, cut, corrections).cut
        if not all(q.key in store.authoritative() for q in found_roles):
            break
    if Stage.ROLES in settings.stages:
        questions[Stage.ROLES] = tuple(asked_roles)

    # The groups: a pass, then any later ones, each on the cut the passes
    # before it left, while a pass still changes the groups. Applied
    # whenever their keys have answers: a key pins the cut it was asked on,
    # and a replay cannot know what failed in the run.
    asked_groups: list[Question] = []
    group_pass = 0
    for number in range(1, settings.group_passes + 1):
        group_pass = number
        found_groups = _groups(document, after, settings, number)
        asked_groups += found_groups
        if Stage.GROUPS in settings.stages:
            if (
                runner is not None
                and found_groups
                and may_ask(required, store, reports)
            ):
                reports.append(runner.run(found_groups, check))
            required += found_groups
        changed = [c for q in found_groups for c in store.corrections_of(q.key)]
        corrections += changed
        after = corrected_cut(document, cut, corrections).cut
        answered = store.authoritative()
        if not changed or not all(q.key in answered for q in found_groups):
            break
    if Stage.GROUPS in settings.stages:
        questions[Stage.GROUPS] = tuple(asked_groups)

    # The deciding step: a session over the whole cut
    # the groups left, the stages' flags in front of it. Its corrections are
    # weighed as the edges' are; then the roles of the blocks they changed
    # are asked, the groups whole where a changed block lies in or next to
    # a group, and the edges carried from an answer on the cut before it.
    proposals: list[Proposal] = []
    decided: list[Proposal] = []
    carried_from: AnswerRecord | None = None
    before_decide = after
    shown: list[Raised] = []
    if settings.decide_passes:
        # Built and applied whenever the profile has the step, so its stored
        # answer stands whichever stages a run asks; asked with the groups.
        shown = [
            *_raised_by(store, Stage.ROLES, asked_roles),
            *_raised_by(store, Stage.GROUPS, asked_groups),
            *(
                Raised(
                    dataclasses.replace(r.flag, id=f"decide-{r.flag.id}"),
                    r.stage,
                    r.cut,
                )
                for r in rule_flags(after)
            ),
        ]
        asked_decide = _decide(document, after, settings, shown)
        questions[Stage.DECIDE] = asked_decide
        if (
            runner is not None
            and Stage.GROUPS in settings.stages
            and asked_decide
            and may_ask(required, store, reports)
        ):
            reports.append(runner.run(asked_decide, check))
        required += asked_decide
        answered = store.authoritative()
        if all(q.key in answered for q in asked_decide):
            proposed = [c for q in asked_decide for c in store.corrections_of(q.key)]
            decided = weigh(
                document, cut, corrections, after, proposed, 0, Stage.DECIDE.value
            )
            proposals += decided
        applied = [p.correction for p in decided if p.applied]
        if applied:
            held, start = len(corrections), len(proposals) - len(decided)
            corrections += applied
            stepped = corrected_cut(document, cut, corrections).cut
            touched = changed_blocks(after, stepped)
            roles_question = roles_file.touched_question(
                document,
                stepped,
                touched,
                settings.templates.roles,
                settings.overlap,
                0,
            )
            questions[Stage.ROLES] = (*questions.get(Stage.ROLES, ()), roles_question)
            if runner is not None and may_ask(required, store, reports):
                reports.append(runner.run((roles_question,), check))
            required.append(roles_question)
            if roles_question.key not in store.authoritative():
                # Taken back, as a round is: the cut stays the groups', and
                # the same command again resumes the step's roles.
                unwound(corrections, held, proposals, start, STEP_ROLES_UNANSWERED)
                decided = proposals[start:]
            else:
                corrections += store.corrections_of(roles_question.key)
                after = corrected_cut(document, cut, corrections).cut
                ungrouped = any(c.op is CutOp.UNGROUP for c in applied)
                if ungrouped or near_group(after, touched):
                    group_pass += 1
                    regrouped = _groups(document, after, settings, group_pass)
                    asked_groups += regrouped
                    questions[Stage.GROUPS] = tuple(asked_groups)
                    if (
                        runner is not None
                        and regrouped
                        and may_ask(required, store, reports)
                    ):
                        reports.append(runner.run(regrouped, check))
                    required += regrouped
                    corrections += [
                        c for q in regrouped for c in store.corrections_of(q.key)
                    ]
                    after = corrected_cut(document, cut, corrections).cut
                if Stage.EDGES in settings.stages:
                    carried_from = _last_edges(document, before_decide, settings, store)

    # The edges: one session on the cut the stages before it left, then any
    # later passes, each on the sheet the pass before it left, while a pass
    # still changes the records; asked only where its stage is.
    edges = EdgeRecords()
    edges_answer = None
    if Stage.EDGES in settings.stages:
        asked_edges: list[Question] = []
        for number in range(1, settings.edge_passes + 1):
            if number == 1 and carried_from is not None:
                # The answer on the cut before the deciding step, carried
                # onto its cut: its proposals were never weighed.
                carried = edges_file.carry(
                    document,
                    text_of(carried_from, run, store.run_directory),
                    before_decide,
                    after,
                    settings.files,
                    cuts_weighed=False,
                )
                stuck = edges_file.stuck_cut_lines(document, carried, after)
                if stuck:
                    carried = edges_file.carry(
                        document,
                        text_of(carried_from, run, store.run_directory),
                        before_decide,
                        after,
                        settings.files,
                        cuts_weighed=False,
                        reopen=stuck,
                    )
                question = edges_file.reask_question(
                    document,
                    after,
                    settings.templates.edges,
                    settings.templates.edges_reask,
                    settings.files,
                    carried,
                    0,
                    [_described(p) for p in decided if p.applied],
                    settings.chunk,
                )
            elif number == 1:
                question = _edges(document, after, settings)
            elif edges_answer is None:
                break
            else:
                shown_rules = (
                    [
                        r
                        for r in rule_flags(after, edges, after)
                        if r.flag.signal in EDGE_RULES
                    ]
                    if settings.show_rule_flags_in_edge_review
                    else []
                )
                question = _edges_review(
                    document,
                    after,
                    settings,
                    text_of(edges_answer, run, store.run_directory),
                    edges_answer.key,
                    number,
                    shown_rules,
                )
            asked_edges.append(question)
            if runner is not None and may_ask(required, store, reports):
                reports.append(runner.run((question,), check))
            required.append(question)
            answer = store.authoritative().get(question.key)
            if answer is None:
                break
            found_edges = store.edge_records_of(question.key)
            unchanged = edges_answer is not None and _same_records(edges, found_edges)
            edges_answer, edges = answer, found_edges
            if run is not None:
                run.moved(stage=Stage.EDGES.value, step=number, edges_answer=answer.key)
            if unchanged:
                break
        questions[Stage.EDGES] = tuple(asked_edges)
        # The role corrections the last pass's standings imply,
        # applied with it: no round, nothing asked for them.
        if edges_answer is not None:
            after = (
                claims_applied(
                    document,
                    cut,
                    store,
                    edges_answer,
                    _asked_on(asked_edges, edges_answer),
                    0,
                    corrections,
                    proposals,
                )
                or after
            )

    # The rounds of cut corrections.
    after, edges, edges_answer, cut_rounds = run_cut_rounds(
        document,
        cut,
        store,
        settings,
        runner,
        check,
        questions=questions,
        corrections=corrections,
        proposals=proposals,
        reports=reports,
        required=required,
        after=after,
        edges=edges,
        edges_answer=edges_answer,
        run=run,
    )
    # The person rounds: each answer a person gave on
    # this version, by its layer's type rule.
    edges, edges_answer, person_rounds = run_person_rounds(
        document,
        cut,
        store,
        settings,
        runner,
        check,
        questions=questions,
        corrections=corrections,
        proposals=proposals,
        reports=reports,
        required=required,
        edges=edges,
        edges_answer=edges_answer,
        run=run,
    )

    answered = store.authoritative()
    corrected = corrected_cut(document, cut, corrections)
    raised = flags_raised(store, questions, (Stage.ROLES, Stage.GROUPS))
    edges_cut = None
    if edges_answer is not None:
        edges_cut = _asked_on(questions[Stage.EDGES], edges_answer)
        # a person's edge half raises no flag: the last model's stand
        flagged = person_rounds.flags_from or edges_answer
        raised += [
            Raised(flag, Stage.EDGES.value, _asked_on(questions[Stage.EDGES], flagged))
            for flag in store.flags_of(flagged.key)
        ]
    raised += [r for r in shown if r.by == "rule"]
    raised += rule_flags(corrected.cut, edges, edges_cut)
    raised += vocabulary_flags(document, corrected.cut)
    return Review(
        corrected=corrected,
        questions=questions,
        unanswered=tuple(
            q.key for qs in questions.values() for q in qs if q.key not in answered
        ),
        failed=tuple(key for report in reports for key in report.failed),
        reports=tuple(reports),
        edges=edges,
        edges_answer=edges_answer,
        proposals=tuple(proposals),
        cut_rounds=cut_rounds,
        flags=tuple(raised),
        edges_carried_from=(
            carried_from.key
            if carried_from is not None and Stage.EDGES in questions
            else None
        ),
        decide_flags=tuple(shown),
        person=person_rounds,
        line_authors=(
            {}
            if edges_answer is None
            else first_authors(
                store,
                questions.get(Stage.EDGES, ()),
                edges_answer,
                edges,
                (
                    ()
                    if carried_from is None or Stage.EDGES not in questions
                    else (
                        answer_link(
                            store.edge_records_of(carried_from.key), carried_from
                        ),
                    )
                ),
            )
        ),
    )


#: The cursor's stage while the rounds of cut corrections run.
CUT_ROUNDS = "cut-rounds"
#: The cursor's stage once the person rounds are applied.
PERSON_ROUNDS = "person-rounds"


def first_authors(
    store: RecordStore,
    asked: Sequence[Question],
    final: AnswerRecord,
    edges: EdgeRecords,
    before: Sequence[Link] = (),
) -> Authors:
    """Each line of `final`'s records `edges` with the provenance of the
    answer that first wrote it: the chain is `before`,
    then the answers to `asked` in the order asked, up to `final`
    (`exchange.line_authors`)."""
    answered = store.authoritative()
    links = list(before)
    for question in asked:
        answer = answered.get(question.key)
        if answer is None:
            continue
        links.append(answer_link(store.edge_records_of(answer.key), answer))
        if answer.key == final.key:
            break
    return line_authors(edges, links)


def run_cut_rounds(
    document: Document,
    origin: Cut,
    store: RecordStore,
    settings: ReviewSettings,
    runner: Runner | None,
    check: Check,
    *,
    questions: dict[Stage, tuple[Question, ...]],
    corrections: list[CutCorrection],
    proposals: list[Proposal],
    reports: list[RunReport],
    required: list[Question],
    after: Cut,
    edges: EdgeRecords,
    edges_answer: AnswerRecord | None,
    pinned: Pinned | None = None,
    run: RunFile | None = None,
) -> tuple[Cut, EdgeRecords, AnswerRecord | None, tuple[CutRound, ...]]:
    """The rounds of cut corrections after the edges, as a
    build and an update ask them: `origin` is the cut the
    corrections stack on (the cutter's or the given cut; an update's
    rebased cut), `edges_answer` the last edges answer. Extends `questions`,
    `corrections`, `proposals`, `reports` and `required` as the review's
    own steps do, and returns the cut, the edge half and its answer after
    the rounds, with the rounds that applied any. `pinned`, a person's
    pins (an update's, carried from OLD): a correction
    that would change one is dropped, a roles-pinned block is never asked
    its roles (its flag stays a flag), and each re-ask holds the edges
    pins. An edges answer's role corrections are no proposals:
    a re-ask's are applied with it (`claims_applied`), the caller's
    before. `run` holds the sheets the rounds stand on, and its cursor
    moves past each round applied."""
    pinned = pinned or Pinned()
    # The rounds of cut corrections: the edge half's
    # proposals weighed and applied, then the roles of the blocks they
    # changed and the edges of the blocks they touch asked again, while a
    # round applies any, up to `cut_rounds`.
    cut_rounds: list[CutRound] = []
    number = 0
    roles_asked_in_rounds: set[str] = set()
    while edges_answer is not None:
        number += 1
        asked_on = _asked_on(questions[Stage.EDGES], edges_answer)
        proposed = cut_proposals(store, edges_answer)
        # The edges' `? role` flags send their blocks to the roles, the notes of one
        # block joined.
        role_flags: dict[str, str] = {}
        for flag in store.flags_of(edges_answer.key):
            if flag.blocks and flag.blocks[0] in pinned.roles:
                continue  # a person's roles: never asked
            if flag.signal is Signal.ROLE and flag.blocks:
                held_note = role_flags.get(flag.blocks[0])
                role_flags[flag.blocks[0]] = (
                    f"{held_note}; {flag.note}" if held_note else flag.note
                )
        if not proposed and not role_flags:
            break
        if number > settings.cut_rounds:
            proposals += [
                Proposal(
                    c,
                    number,
                    edges_answer.stage,
                    False,
                    _not_asked(settings.cut_rounds),
                    _named(asked_on, c),
                )
                for c in proposed
            ]
            break
        weighed = (
            weigh(
                document,
                origin,
                corrections,
                asked_on,
                proposed,
                number,
                edges_answer.stage,
                pinned,
            )
            if proposed
            else []
        )
        held, start = len(corrections), len(proposals)
        proposals += weighed
        applied = [p.correction for p in weighed if p.applied]
        flagged = {
            b: n for b, n in role_flags.items() if b not in roles_asked_in_rounds
        }
        if not applied and not flagged:
            break
        corrections += applied
        after = corrected_cut(document, origin, corrections).cut
        touched = changed_blocks(asked_on, after)
        present = {block.id for block in after.blocks}
        flagged = {b: n for b, n in flagged.items() if b in present}

        # a roles-pinned block a correction changed is shown as context
        asked_roles = [
            b
            for b in [*touched, *(b for b in flagged if b not in touched)]
            if b not in pinned.roles
        ]
        roles_asked_in_rounds |= {*touched, *flagged}
        if asked_roles:
            roles_question = roles_file.touched_question(
                document,
                after,
                asked_roles,
                settings.templates.roles,
                settings.overlap,
                number,
                notes=flagged,
            )
            questions[Stage.ROLES] = (*questions.get(Stage.ROLES, ()), roles_question)
            if runner is not None and may_ask(required, store, reports):
                reports.append(runner.run((roles_question,), check))
            required.append(roles_question)
            if roles_question.key not in store.authoritative():
                unwound(corrections, held, proposals, start, ROLES_UNANSWERED)
                break
            corrections += store.corrections_of(roles_question.key)
            after = corrected_cut(document, origin, corrections).cut
        # A flagged block whose roles stood reopens nothing; one whose roles
        # changed reopens by the type rule.
        changed_roles = roles_changed(
            asked_on, after, [b for b in flagged if b not in touched]
        )
        reopen = (
            edges_file.reopened_by_roles(
                document,
                text_of(edges_answer, run, store.run_directory),
                asked_on,
                after,
                changed_roles,
            )
            if changed_roles
            else {}
        )
        if not touched and not reopen:
            break

        carried = edges_file.carry(
            document,
            text_of(edges_answer, run, store.run_directory),
            asked_on,
            after,
            settings.files,
            reopen=reopen,
        )
        described = [
            *(_described(p) for p in weighed if p.applied),
            *(f"roles of {b}: {how}" for b, how in changed_roles.items()),
        ]
        question = person.pinned_question(
            partial(
                edges_file.reask_question,
                document,
                after,
                settings.templates.edges,
                settings.templates.edges_reask,
                settings.files,
                carried,
                number,
                described,
                settings.chunk,
                claims_fixed=pinned.roles,
            ),
            pinned.edge_pins(document, after, settings.files),
        )
        questions[Stage.EDGES] = (*questions[Stage.EDGES], question)
        if runner is not None and may_ask(required, store, reports):
            reports.append(runner.run((question,), check))
        required.append(question)
        answer = store.authoritative().get(question.key)
        if answer is None:
            unwound(corrections, held, proposals, start, EDGES_UNANSWERED)
            break
        cut_rounds.append(CutRound(number, touched, carried.reopened))
        edges, edges_answer = store.edge_records_of(question.key), answer
        if run is not None:
            run.moved(stage=CUT_ROUNDS, step=number, edges_answer=answer.key)
        # the re-ask's own role corrections, applied with it
        after = (
            claims_applied(
                document,
                origin,
                store,
                answer,
                after,
                number,
                corrections,
                proposals,
                roles_held=pinned.roles,
            )
            or after
        )
    return after, edges, edges_answer, tuple(cut_rounds)


def run_person_rounds(
    document: Document,
    origin: Cut,
    store: RecordStore,
    settings: ReviewSettings,
    runner: Runner | None,
    check: Check,
    *,
    questions: dict[Stage, tuple[Question, ...]],
    corrections: list[CutCorrection],
    proposals: list[Proposal],
    reports: list[RunReport],
    required: list[Question],
    edges: EdgeRecords,
    edges_answer: AnswerRecord | None,
    carried: tuple[str, Cut] | None = None,
    pinned: Pinned | None = None,
    kept: KeptRounds | None = None,
    applied_before: Collection[str] = (),
    run: RunFile | None = None,
) -> tuple[EdgeRecords, AnswerRecord | None, PersonRounds]:
    """The person rounds, after a build's or an update's
    own stages and rounds of cut corrections, shared by both, and the step
    an edit takes from a kept state (`kept`): the answers given on
    this version, by a person or an agent, in store order, each applied
    where its key is the question its stage shows on the current state
    (`person.person_question`), else stale, applying nothing. An agent's
    answer is applied as a person's, but pins nothing. Asked only on a
    whole run. By the layer's type rule, no closure:

    - **cut:** its corrections join, unweighed (the person's check ran them
      through the assembler); the roles of the blocks they changed, then
      the edges carried onto the new cut and re-asked, as a round of cut
      corrections asks them;
    - **roles:** its corrections join; the blocks whose roles changed
      reopen by `edges_file.reopened_by_roles`, and the edges are re-asked;
    - **groups:** its corrections join; carry's group rule and
      `edges_file.reopened_by_groups` reopen a group's justifies edge, the
      about edges onto it and its members' justifies edges out of it, and
      the edges are re-asked;
    - **edges:** its records are the edge half; nothing is asked; the role
      corrections its standings imply join, by the person.

    A re-ask applies no model correction of the cut and asks no roles: its
    cut lines are listed, not applied (`PERSON_NO_CUT`), its `? role` flags
    stay flags; its role corrections join, but on a block whose
    roles a person pinned. It holds every edges pin, the update's carried ones
    (`pinned`) and each applied person answer's, carried onto its sheet. A
    round left unanswered is taken back (`unwound`) and the rounds stop:
    the run is not whole. `carried` is the edges sheet and its cut where no
    edges answer is (an update that asked nothing). Extends the lists as
    `run_cut_rounds` does, and returns the edge half, its answer and the
    rounds."""
    answers = kept.answers if kept else store.given_answers(document_hash(document))
    # a given vocabulary map is read before the cut, by `map_vocabulary`;
    # an answer the state an update starts from applied is in its cut
    answers = tuple(
        a
        for a in answers
        if a.stage != Stage.VOCABULARY.value and a.key not in applied_before
    )
    if not answers and not kept:
        return edges, edges_answer, PersonRounds()
    keys = tuple(a.key for a in answers)
    answered = store.authoritative()
    if any(q.key not in answered for qs in questions.values() for q in qs):
        return edges, edges_answer, PersonRounds(keys)  # not whole: none applied
    pinned = pinned or Pinned()
    applied: list[PersonRound] = list(kept.rounds) if kept else []
    stale: list[str] = list(kept.stale) if kept else []
    sources: list[EdgePins] = list(kept.sources) if kept else []
    pending: PersonRound | None = None
    flags_from = edges_answer
    cut_away: set[str] = set()
    number = max((r.number for r in applied), default=0)

    def given_text(found: AnswerRecord) -> str:
        """A given answer's text: as the edit applying it was given it,
        else as the run holds it."""
        if kept is not None and found.key in kept.texts:
            return kept.texts[found.key]
        return text_of(found, run, store.run_directory)

    for found in answers:
        by = found.host
        after = corrected_cut(document, origin, corrections).cut
        written = (
            (
                given_text(edges_answer),
                _asked_on(questions[Stage.EDGES], edges_answer),
            )
            if edges_answer is not None
            else carried
        )
        sheet = (
            None
            if written is None
            else edges_file.person_sheet(
                document, written[0], written[1], after, settings.files
            )
        )
        layer = person.LAYER_OF.get(found.stage)
        if layer is None or (layer is Layer.EDGES and sheet is None):
            stale.append(found.key)
            continue
        question = person.person_question(
            document,
            after,
            layer,
            settings,
            sheet,
            given_text(found),
            claim_notes(proposals, after),
            by,
        )
        if question.key != found.key:
            stale.append(found.key)
            continue
        number += 1
        if layer is Layer.EDGES:
            questions[Stage.EDGES] = (*questions.get(Stage.EDGES, ()), question)
            edges, edges_answer = store.edge_records_of(found.key), found
            carried = None
            # a person's standing on a block with no claim adds it,
            # but where a person's roles pin holds the block's roles
            claims_applied(
                document,
                origin,
                store,
                found,
                after,
                number,
                corrections,
                proposals,
                roles_held=pinned.roles
                | {
                    p.owner
                    for r in applied
                    for p in r.pins
                    if p.layer is Layer.ROLES and p.held
                },
                by=by,
            )
            if by == PERSON:
                sources.append(
                    EdgePins(
                        person.shown_file(question), given_text(found), after, number
                    )
                )
            applied.append(PersonRound(number, found.key, found.stage, by=by))
            continue

        held, start = len(corrections), len(proposals)
        given = store.corrections_of(found.key)
        corrections += given
        stepped = corrected_cut(document, origin, corrections).cut
        made = changed_blocks(after, stepped)
        # a roles answer gives every block's roles: those it left as they
        # were change nothing, and are not listed
        standing = {b.id for b in after.blocks}
        proposals += [
            Proposal(c, number, found.stage, True, None, _named(after, c), made, by=by)
            for c in given
            if c.op is not CutOp.ROLES
            or c.block not in standing
            or c.block in roles_changed(after, stepped, [c.block])
        ]
        pins = (
            tuple(
                dataclasses.replace(p, rank=number)
                for p in person.cut_pins(layer, after, stepped)
            )
            if by == PERSON
            else ()
        )
        asked: list[str] = []
        if layer is Layer.CUT:
            # a block the person's cut edit removed takes its pins with it;
            # a roles-pinned block it changed but kept is shown as context
            gone = {b.id for b in after.blocks} - {b.id for b in stepped.blocks}
            cut_away |= gone
            applied = [_lapsed_on(r, gone) for r in applied]
            held_roles = pinned.roles | {
                p.owner
                for r in applied
                for p in r.pins
                if p.layer is Layer.ROLES and p.held
            }
            made = tuple(b for b in made if b not in held_roles)
        if layer is Layer.CUT and made and Stage.ROLES in settings.stages:
            roles_question = roles_file.touched_question(
                document,
                stepped,
                list(made),
                settings.templates.roles,
                settings.overlap,
                number,
            )
            questions[Stage.ROLES] = (*questions.get(Stage.ROLES, ()), roles_question)
            if runner is not None and may_ask(required, store, reports):
                reports.append(runner.run((roles_question,), check))
            required.append(roles_question)
            asked.append(roles_question.key)
            if roles_question.key not in store.authoritative():
                unwound(corrections, held, proposals, start, PERSON_ROLES_UNANSWERED)
                reopened = (
                    ()
                    if written is None
                    else edges_file.carry(
                        document, written[0], written[1], stepped, settings.files
                    ).reopened
                )
                pending = PersonRound(
                    number, found.key, found.stage, pins, tuple(asked), reopened, by
                )
                break
            corrections += store.corrections_of(roles_question.key)
            stepped = corrected_cut(document, origin, corrections).cut
        if written is None or Stage.EDGES not in settings.stages:
            applied.append(
                PersonRound(number, found.key, found.stage, pins, tuple(asked), by=by)
            )
            continue
        raw, on = written
        reopen: dict[str, str] = {}
        if layer is Layer.ROLES:
            standing = {b.id for b in after.blocks}
            changed = roles_changed(
                after, stepped, [b.id for b in stepped.blocks if b.id in standing]
            )
            if changed:
                reopen = edges_file.reopened_by_roles(
                    document, raw, on, stepped, changed
                )
        elif layer is Layer.GROUPS:
            reopen = edges_file.reopened_by_groups(document, raw, on, stepped)
        recarried = edges_file.carry(
            document, raw, on, stepped, settings.files, reopen=reopen
        )
        if not recarried.reopened:
            applied.append(
                PersonRound(number, found.key, found.stage, pins, tuple(asked), by=by)
            )
            continue
        described = [f"a person's edit: {p.kind.value} {p.line}" for p in pins]
        # the blocks whose roles a person gave: a standing adds no claim
        # role there
        roles_held = pinned.roles | {
            p.owner
            for p in (*(q for r in applied for q in r.pins), *pins)
            if p.layer is Layer.ROLES and p.held
        }
        # what the sheet held before this round, under the pins it was
        # kept with, is the state's, not the re-ask's (docs/architecture.md § 9)
        named = {ident: h for h, ident in edges_file.handles(document, stepped).items()}
        held_before = pinned.roles | {
            p.owner
            for r in applied
            for p in r.pins
            if p.layer is Layer.ROLES and p.held
        }
        kept_problems = edges_file.kept_problems(
            raw,
            settings.files,
            onto=edges_file.handles_by_id(document, on, stepped),
            within=edges_file.nested(document, stepped),
            claims_fixed=[named[b] for b in held_before if b in named],
        )
        reask = person.pinned_question(
            partial(
                edges_file.reask_question,
                document,
                stepped,
                settings.templates.edges,
                settings.templates.edges_reask,
                settings.files,
                recarried,
                number,
                described,
                settings.chunk,
                claims_fixed=roles_held,
                kept=kept_problems,
            ),
            pinned.with_edges(sources).edge_pins(document, stepped, settings.files),
        )
        questions[Stage.EDGES] = (*questions.get(Stage.EDGES, ()), reask)
        if runner is not None and may_ask(required, store, reports):
            reports.append(runner.run((reask,), check))
        required.append(reask)
        asked.append(reask.key)
        answer = store.authoritative().get(reask.key)
        if answer is None:
            unwound(corrections, held, proposals, start, PERSON_EDGES_UNANSWERED)
            pending = PersonRound(
                number,
                found.key,
                found.stage,
                pins,
                tuple(asked),
                recarried.reopened,
                by,
            )
            break
        edges, edges_answer, carried = store.edge_records_of(reask.key), answer, None
        flags_from = answer
        proposals += [
            Proposal(c, number, answer.stage, False, PERSON_NO_CUT, _named(stepped, c))
            for c in cut_proposals(store, answer)
        ]
        # its role corrections are no cut's: applied with it, but
        # never on a block whose roles a person pinned
        claims_applied(
            document,
            origin,
            store,
            answer,
            stepped,
            number,
            corrections,
            proposals,
            roles_held=roles_held,
        )
        applied.append(
            PersonRound(
                number,
                found.key,
                found.stage,
                pins,
                tuple(asked),
                recarried.reopened,
                by,
            )
        )

    if run is not None and applied:
        run.moved(
            stage=PERSON_ROUNDS,
            step=len(applied),
            person_rounds=tuple(r.key for r in applied),
            edges_answer=None if edges_answer is None else edges_answer.key,
        )
    # the pins held on the final state: the cut's, roles' and groups' as
    # each round left them and the final cut holds them, the edges' as the
    # last sheet holds them, by id
    final_cut = corrected_cut(document, origin, corrections).cut
    final = (
        _asked_on(questions[Stage.EDGES], edges_answer)
        if edges_answer is not None
        else None
    )
    edge_pins: list[Pin] = []
    if final is not None and edges_answer is not None and sources:
        ids = edges_file.handles(document, final)
        edge_pins = [
            dataclasses.replace(p, owner=ids.get(p.owner, p.owner))
            for p in person.as_left(
                person.merged(
                    [p for e in sources for p in e.on(document, final, settings.files)]
                ),
                given_text(edges_answer),
                edges_file.structure(
                    document,
                    final,
                    settings.files,
                    pinned.roles
                    | {
                        p.owner
                        for r in applied
                        for p in r.pins
                        if p.layer is Layer.ROLES and p.held
                    },
                ),
            )
        ]
    return (
        edges,
        edges_answer,
        PersonRounds(
            keys,
            tuple(applied),
            tuple(stale),
            pending,
            (
                *person.held_on(
                    person.merged([p for r in applied for p in r.pins]), final_cut
                ),
                *edge_pins,
            ),
            flags_from if flags_from is not edges_answer else None,
            frozenset(cut_away),
        ),
    )


def _lapsed_on(done: PersonRound, gone: Collection[str]) -> PersonRound:
    """A round's cut and roles pins on blocks a later person's cut edit
    removed, lapsed: the later edit is the person's own word."""
    return dataclasses.replace(
        done,
        pins=tuple(
            dataclasses.replace(
                p, state=person.lapsed("a person's later cut edit changed its block")
            )
            if p.held and p.layer in (Layer.CUT, Layer.ROLES) and p.owner in gone
            else p
            for p in done.pins
        ),
    )


def _raised_by(
    store: RecordStore, stage: Stage, asked: Sequence[Question]
) -> list[Raised]:
    """The flags the answers to `asked`, one stage's questions, raised."""
    return flags_raised(store, {stage: tuple(asked)}, (stage,))


def _last_edges(
    document: Document, cut: Cut, settings: ReviewSettings, store: RecordStore
) -> AnswerRecord | None:
    """The last answered pass of the edges on `cut` in the store, where
    there is one: the first, then each review of the one before, carried onto the
    deciding step's cut."""
    answers = store.authoritative()
    found = answers.get(_edges(document, cut, settings).key)
    for number in range(2, settings.edge_passes + 1):
        if found is None:
            break
        # a pass is keyed by the answer it follows: no sheet is shown
        later = answers.get(
            _edges_review(document, cut, settings, "", found.key, number).key
        )
        if later is None:
            break
        found = later
    return found


def flags_raised(
    store: RecordStore,
    questions: dict[Stage, tuple[Question, ...]],
    stages: Sequence[Stage],
) -> list[Raised]:
    """The flags the answers to `stages`' questions raised, each on the cut
    its question was asked on."""
    return [
        Raised(flag, stage.value, q.input_cut)
        for stage in stages
        for q in questions.get(stage, ())
        if q.input_cut is not None
        for flag in store.flags_of(q.key)
    ]


def roles_changed(before: Cut, after: Cut, blocks: Collection[str]) -> dict[str, str]:
    """The blocks of `blocks` whose roles or segments differ between two
    cuts, each with how: `claim → definition`."""

    def roles(cut: Cut, block_id: str) -> str:
        block = cut.block(block_id)
        if block.segments:
            return " + ".join(
                s.role.value if s.role else "none" for s in block.segments
            )
        return "+".join(r.value for r in block.roles) or "none"

    found = {}
    for b in blocks:
        old, new = before.block(b), after.block(b)
        if (old.roles, old.segments) != (new.roles, new.segments):
            found[b] = f"{roles(before, b)} → {roles(after, b)}"
    return found


def decided_by(raised: Raised, proposals: Sequence[Proposal]) -> str | None:
    """The applied correction a flag led to: the first whose
    blocks hold every block of the flag's; none where no applied correction
    does. (The steps are shown no flag's id, so no reason names one.)"""
    return decided_on(raised.flag.blocks, proposals)


def decided_on(blocks: Sequence[str], proposals: Sequence[Proposal]) -> str | None:
    """`decided_by` for a flag on `blocks`."""
    for p in proposals:
        if not p.applied:
            continue
        c = p.correction
        named = {c.block} if c.block is not None else set(c.blocks)
        if set(blocks) <= named:
            return c.id
    return None


def near_group(cut: Cut, blocks: Sequence[str]) -> bool:
    """Whether a block of `blocks` lies in a group of two blocks or more, or
    just before or after one."""
    order = {block.id: n for n, block in enumerate(cut.blocks)}
    at = {order[b] for b in blocks if b in order}
    for group in cut.groups:
        held = [order[b] for b in group.blocks if b in order]
        if len(held) > 1 and at & set(range(min(held) - 1, max(held) + 2)):
            return True
    return False


def changed_blocks(before: Cut, after: Cut) -> tuple[str, ...]:
    """The blocks of `after` that `before` does not hold, by id and content,
    in reading order: what a correction to the cut made or changed. A
    function of two cuts, so a later edit to the text can use it too."""
    held = {(block.id, block.hash) for block in before.blocks}
    return tuple(b.id for b in after.blocks if (b.id, b.hash) not in held)


def unwound(
    corrections: list[CutCorrection],
    held: int,
    proposals: list[Proposal],
    start: int,
    problem: str,
) -> None:
    """A round whose roles or edges went unanswered, taken back: its
    corrections off the cut, its applied proposals marked not applied, so
    the export is the one before the round plus a failed round, and the
    same command again resumes it."""
    del corrections[held:]
    proposals[start:] = [
        dataclasses.replace(p, applied=False, problem=problem, after=())
        if p.applied
        else p
        for p in proposals[start:]
    ]


def cut_proposals(store: RecordStore, answer: AnswerRecord) -> list[CutCorrection]:
    """An edges answer's cut lines as the corrections a round weighs, its role
    corrections left out (`claims_applied`)."""
    return [c for c in store.corrections_of(answer.key) if c.op is not CutOp.ROLES]


def claims_applied(
    document: Document,
    origin: Cut,
    store: RecordStore,
    answer: AnswerRecord,
    asked_on: Cut,
    number: int,
    corrections: list[CutCorrection],
    proposals: list[Proposal],
    *,
    roles_held: Collection[str] = (),
    by: str = BY_EDGES,
) -> Cut | None:
    """The role corrections an edges answer's standings imply
    (`edges_file.claims_added`), and the roles an update's one session wrote
    on its re-cut blocks (`edges_file.roles_written`), applied with
    the answer: never weighed as
    a proposal, never a round, nothing asked for them. Each joins
    `corrections` and is listed in `proposals` in round `number`, `by` the
    edges (a person's edges answer's by the person); one on a block in
    `roles_held` (a person's roles pin) or that the assembler refuses is
    listed, not applied. `asked_on` is the cut the answer was asked on.
    Returns the cut they leave, None where the answer implies none."""
    found = [c for c in store.corrections_of(answer.key) if c.op is CutOp.ROLES]
    if not found:
        return None
    for c in found:
        named = _named(asked_on, c)
        if c.block in roles_held:
            proposals.append(
                Proposal(
                    c, number, answer.stage, False, PINNED + str(c.block), named, by=by
                )
            )
            continue
        trial = corrected_cut(document, origin, [*corrections, c])
        problem = next(
            (p.message for p in trial.problems if p.correction == c.id), None
        )
        if problem is not None:
            proposals.append(
                Proposal(c, number, answer.stage, False, problem, named, by=by)
            )
            continue
        corrections.append(c)
        proposals.append(
            Proposal(c, number, answer.stage, True, None, named, (c.block,), by=by)
        )
    return corrected_cut(document, origin, corrections).cut


def claim_notes(proposals: Sequence[Proposal], cut: Cut) -> dict[str, str]:
    """The blocks whose roles stand as an edges standing's role correction
    left them (`claims_applied`), each with `CLAIM_ADDED_NOTE`, for a
    person's roles file: where a later roles answer gave the
    block its roles again, the note goes."""
    held = {block.id: block for block in cut.blocks}
    found = {}
    for p in proposals:
        c = p.correction
        # an update's session writes a re-cut block's roles too:
        # those are its roles, not a claim added
        if not (
            p.applied
            and c.op is CutOp.ROLES
            and p.by == BY_EDGES
            and c.why.startswith(edges_file.CLAIM_ADDED_WHY)
        ):
            continue
        block = held.get(str(c.block))
        if block is not None and (block.roles, block.segments) == (
            c.roles,
            c.segments,
        ):
            found[block.id] = CLAIM_ADDED_NOTE
    return found


def _not_asked(limit: int) -> str:
    if limit == 0:
        return NO_ROUND
    return (
        f"not applied: the build asks {limit} round(s) of cut corrections "
        f"(cut_rounds {limit})"
    )


def weigh(
    document: Document,
    cut: Cut,
    corrections: Sequence[CutCorrection],
    asked_on: Cut,
    proposed: Sequence[CutCorrection],
    number: int,
    stage: str,
    pinned: Pinned | None = None,
) -> list[Proposal]:
    """Each proposal tried in turn on the cut the ones before it left, a
    decision's corrections together: all apply or none. One
    the assembler refuses keeps its problem. A block start is a position,
    and `_flips` counts how often the answers so far changed it since
    `cut`, the cutter's or the given cut: a proposal may change a position
    changed once only with evidence, and never one changed twice. One that would change
    what a person pinned is dropped (`pinned`; `pinned_at`): a pinned start moved or
    removed, any change to a block pinned whole, roles-pinned or in a pinned group, a
    pinned group ungrouped or attached to."""
    flips = _flips(document, cut, corrections)
    held: list[CutCorrection] = []
    current = asked_on
    found: list[Proposal] = []
    for unit in _units(proposed):
        named = {c.id: _named(current, c) for c in unit}
        trial = corrected_cut(document, cut, [*corrections, *held, *unit])
        ids = {c.id for c in unit}
        problem = next((p.message for p in trial.problems if p.correction in ids), None)
        changed = _starts(current) ^ _starts(trial.cut)
        reverses = tuple(sorted(p for p in changed if flips[p] >= 1))
        if problem is None:
            twice = [p for p in reverses if flips[p] >= 2]
            evidence = all(c.evidence for c in unit if c.op in BLOCK_OPS)
            held_by_person = pinned_at(pinned, current, trial.cut, unit)
            if held_by_person:
                problem = PINNED + ", ".join(held_by_person)
            elif twice:
                problem = DROPPED_TWICE + ", ".join(twice)
            elif reverses and not evidence:
                problem = DROPPED + ", ".join(reverses)
        if problem is not None:
            found += [
                Proposal(c, number, stage, False, problem, named[c.id]) for c in unit
            ]
            continue
        after = changed_blocks(current, trial.cut)
        found += [
            Proposal(c, number, stage, True, None, named[c.id], after, reverses)
            for c in unit
        ]
        held += unit
        current = trial.cut
        for position in changed:
            flips[position] += 1
    return found


def pinned_at(
    pinned: Pinned | None,
    current: Cut,
    trial: Cut,
    unit: Sequence[CutCorrection],
) -> list[str]:
    """The pinned places a unit would change: a pinned
    start it moves or removes; a block pinned whole, roles-pinned or a
    member of a pinned group that it changes in any way (a split, a merge,
    a boundary moved); and a pinned group it ungroups or attaches to."""
    if not pinned:
        return []
    after = {(b.id, b.hash) for b in trial.blocks}
    changed = {b.id for b in current.blocks if (b.id, b.hash) not in after}
    starts, now = _starts(current), _starts(trial)
    found = [
        *sorted((starts ^ now) & pinned.starts),
        *sorted(changed & (pinned.whole | pinned.roles | pinned.members)),
        *(c.group for c in unit if c.group is not None and c.group in pinned.groups),
    ]
    return list(dict.fromkeys(found))


def _units(proposed: Sequence[CutCorrection]) -> list[list[CutCorrection]]:
    """The proposals as they are weighed: a decision's corrections, which
    stand together, as one unit; every other alone."""
    units: list[list[CutCorrection]] = []
    for c in proposed:
        if c.decision is not None and units and units[-1][0].decision == c.decision:
            units[-1].append(c)
        else:
            units.append([c])
    return units


def _starts(cut: Cut) -> set[str]:
    """A cut's block starts: its blocks' ids."""
    return {block.id for block in cut.blocks}


def _flips(
    document: Document, cut: Cut, corrections: Sequence[CutCorrection]
) -> Counter[str]:
    """How often the answers behind `corrections` changed each block start
    since `cut`: the cut assembled after each answer's corrections in
    turn, one assembler run per answer. Nothing is stored."""
    flips: Counter[str] = Counter()
    then = _starts(cut)
    for end in [
        n
        for n in range(1, len(corrections) + 1)
        if n == len(corrections)
        or corrections[n].answer is None
        or corrections[n].answer != corrections[n - 1].answer
    ]:
        now = _starts(corrected_cut(document, cut, corrections[:end]).cut)
        flips.update(now ^ then)
        then = now
    return flips


def _described(proposal: Proposal) -> str:
    """One line for the re-ask's brief: what was applied, and why."""
    c = proposal.correction
    if c.group is not None and not proposal.before:
        return f"{c.op.value} of group {c.group}: {c.why}"
    named = ", ".join(block.id for block in proposal.before)
    return f"{c.op.value} of {named}, giving {', '.join(proposal.after)}: {c.why}"


def _asked_on(asked: Sequence[Question], answer: AnswerRecord) -> Cut:
    """The cut the question `answer` answers was asked on."""
    question = next(q for q in asked if q.key == answer.key)
    assert question.input_cut is not None
    return question.input_cut


def _named(cut: Cut, correction: CutCorrection) -> tuple[Block, ...]:
    """The blocks of `cut` a correction names, in its order; a name that is
    not one of them is left out, and the assembler refuses the correction."""
    ids = (correction.block,) if correction.block is not None else correction.blocks
    by_id = {block.id: block for block in cut.blocks}
    return tuple(by_id[i] for i in ids if i in by_id)
