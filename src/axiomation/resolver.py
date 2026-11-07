"""The resolver: answers to records, and the only writer of the store.

The runner hands it every attempt, valid or not. It writes one answer
record per attempt and, for a valid one, the records it holds: the cut
corrections, then the edge stage's entries, edges, self marks, standings
and frame and an update's Δ lines, then the flags, then a review sheet's judgments,
findings and files seen, numbered in that order, their ids `<key[:12]>-<attempt>-<n>`
with the store's attempt number, so ids stay unique across the store and a resumed run
never reuses the attempt of an answer the loader ignored. It asks nothing and imports no
runner (`tests/test_spine.py`). An identity answer, code's, and its lineage are written
by `resolve_identity`. A person's answer is an attempt like any (host and model
`person`, cost 0), which `axiomation edit` hands it, with the version it was given on. A
filing, a person's or an agent's judgments, findings or finding answers, is written by
`file`. A kept review answer found valid on a second check is a new attempt of its key,
the kept one's at no cost, written by `checked_again`."""

from __future__ import annotations

import fcntl
import hashlib
import itertools
import json
import threading
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any

from axiomation import develop
from axiomation.questions import Question, Validation
from axiomation.records import (
    IDENTITY,
    JUDGMENT_KINDS,
    JUDGMENTS_STAGE,
    AnswerRecord,
    CutCorrection,
    Decline,
    Delta,
    Edge,
    EdgeRecords,
    Entry,
    Flag,
    Frame,
    JudgmentRecords,
    LineageLink,
    LineOrder,
    RecordError,
    RecordStore,
    SelfMark,
    Standing,
    TemplateRef,
    VocabularyEntry,
)
from axiomation.run_state import RunFile

#: The file a run's claim on a store is held on.
RUN_LOCK = ".run"


@dataclass(frozen=True, slots=True, eq=False)
class Attempt:
    """One attempt at a question, as the host answered it and as it was
    checked. `text` is the host's answer, whole."""

    question: Question
    host: str
    model: str
    text: str
    cost: float
    seconds: float
    validation: Validation
    tokens: Mapping[str, int] = field(default_factory=dict)
    #: The same counts per model (`AnswerRecord.tokens_by_model`).
    tokens_by_model: Mapping[str, Mapping[str, int]] = field(default_factory=dict)
    effort: str | None = None
    profile: str | None = None
    #: A person's answer's version, its document hash.
    version: str | None = None
    #: An agent's session and a given answer's reason.
    session: str | None = None
    #: A review session's round (`AnswerRecord.review_round`).
    review_round: str | None = None
    #: A review session's state (`AnswerRecord.state`).
    state: str | None = None
    reason: str | None = None
    #: False for a given edges answer shown no stale mark.
    marks_shown: bool | None = None
    #: The runner's estimate for the attempt (`AnswerRecord.estimate`).
    estimate: float | None = None
    #: A queue task's order in its run (`AnswerRecord.order`).
    order: int | None = None
    #: A queue session's task answers (`AnswerRecord.tasks`).
    tasks: tuple[str, ...] = ()
    #: A given answer's file shown, its hash (`AnswerRecord.shown`).
    shown: str | None = None
    #: A queue step's call (`AnswerRecord.call`).
    call: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class Recorded:
    """What the resolver wrote for one attempt."""

    answer: AnswerRecord
    corrections: tuple[CutCorrection, ...]
    edges: EdgeRecords = EdgeRecords()
    flags: tuple[Flag, ...] = ()
    judgments: JudgmentRecords = JudgmentRecords()
    vocabulary: tuple[VocabularyEntry, ...] = ()
    declines: tuple[Decline, ...] = ()


@dataclass(frozen=True, slots=True)
class Filed:
    """What the resolver wrote for one filing: its answer and its records,
    numbered."""

    answer: AnswerRecord
    records: JudgmentRecords


class StoreBusy(RuntimeError):
    """Another run holds the store."""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _decided(corrections: tuple[CutCorrection, ...]) -> tuple[CutCorrection, ...]:
    """Corrections whose `decision` is a tag of the answer's own (`d1`),
    each tag made the id of the first correction that carries it."""
    first: dict[str, str] = {}
    return tuple(
        c
        if c.decision is None
        else replace(c, decision=first.setdefault(c.decision, c.id))
        for c in corrections
    )


def _numbered(
    key: str, number: int, validation: Validation
) -> tuple[
    tuple[CutCorrection, ...],
    EdgeRecords,
    tuple[Flag, ...],
    JudgmentRecords,
    tuple[VocabularyEntry, ...],
    tuple[Decline, ...],
]:
    """A validation's records, each given its id `<key[:12]>-<attempt>-<n>`
    and its answer, in the order the module's docstring says; none where it
    is not valid."""
    valid = validation.valid
    ids = (f"{key[:12]}-{number}-{n}" for n in itertools.count(1))
    corrections = _decided(
        tuple(
            replace(c, id=next(ids), answer=key)
            for c in (validation.corrections if valid else ())
        )
    )
    found = validation.edges if valid else EdgeRecords()

    def numbered[T: (Entry, Edge, SelfMark, Standing, Frame, Delta, LineOrder)](
        records: tuple[T, ...],
    ) -> tuple[T, ...]:
        return tuple(replace(r, id=next(ids), answer=key) for r in records)

    # Numbered in `EdgeRecords.all`'s order.
    edges = EdgeRecords(
        numbered(found.entries),
        numbered(found.edges),
        numbered(found.selfs),
        numbered(found.standings),
        numbered(found.frame),
        numbered(found.deltas),
        numbered(found.orders),
    )
    flags = tuple(
        replace(f, id=next(ids), answer=key)
        for f in (validation.flags if valid else ())
    )
    filed = validation.judgments if valid else JudgmentRecords()
    judged = JudgmentRecords(
        **{
            kind.field: tuple(
                replace(r, id=next(ids), answer=key) for r in getattr(filed, kind.field)
            )
            for kind in JUDGMENT_KINDS
        }
    )
    vocabulary = tuple(
        replace(r, id=next(ids), answer=key)
        for r in (validation.vocabulary if valid else ())
    )
    declines = tuple(
        replace(r, id=next(ids), answer=key)
        for r in (validation.declines if valid else ())
    )
    return corrections, edges, flags, judged, vocabulary, declines


def torn(store: RecordStore, key: str) -> str | None:
    """Why `key` is not to be answered: two joined copies answered it apart
    and differently, so neither answer is read, and a third would settle
    nothing (`docs/architecture.md` § 4, "Copies joined"). None where it may
    be answered."""
    found = store.answer_conflicts().get(key)
    if found is None:
        return None
    return (
        f"question {key[:12]} was answered differently by two joined copies "
        f"({', '.join(e[:12] for e in found)}): its copies' work is to be "
        "merged before it is read"
    )


class Resolver:
    """Writes a store's records from attempts. Thread-safe: taking an
    attempt number and appending hold one lock, so two threads never take
    the same number."""

    def __init__(
        self,
        store: RecordStore,
        clock: Callable[[], str] = _now,
        run: RunFile | None = None,
    ) -> None:
        self.store = store
        self._clock = clock
        #: The run's working state, which holds an answer's text before the
        #: answer is filed.
        self.run = run
        self._lock = threading.Lock()

    def record(self, attempt: Attempt) -> Recorded:
        question = attempt.question
        valid = attempt.validation.valid
        with self._lock:
            refusal = torn(self.store, question.key)
            if refusal is not None:
                raise RecordError(refusal)
            number = self.store.next_attempt(question.key)
            corrections, edges, flags, judged, vocabulary, declines = _numbered(
                question.key, number, attempt.validation
            )
            answer = AnswerRecord(
                key=question.key,
                kind=question.kind,
                stage=question.stage.value,
                template=question.template.ref,
                attempt=number,
                host=attempt.host,
                model=attempt.model,
                time=self._clock(),
                seconds=attempt.seconds,
                cost=attempt.cost,
                tokens=dict(attempt.tokens),
                tokens_by_model=(
                    {m: dict(t) for m, t in attempt.tokens_by_model.items()}
                    if len(attempt.tokens_by_model) > 1
                    else {}
                ),
                raw=attempt.text
                if attempt.validation.kept is None
                else attempt.validation.kept,
                valid=valid,
                problems=attempt.validation.problems,
                records=(
                    *(c.id for c in corrections),
                    *edges.ids,
                    *(f.id for f in flags),
                    *judged.ids,
                    *(r.id for r in vocabulary),
                    *(r.id for r in declines),
                ),
                judges=question.judges,
                effort=attempt.effort,
                profile=attempt.profile,
                version=attempt.version,
                session=attempt.session,
                reason=attempt.reason,
                marks_shown=attempt.marks_shown,
                review_round=attempt.review_round,
                state=attempt.state,
                estimate=attempt.estimate,
                order=attempt.order,
                tasks=attempt.tasks,
                shown=attempt.shown,
                read=attempt.validation.read,
                call=attempt.call,
            )
            if self.run is not None and valid:
                self.run.hold(answer.key, answer.stage, answer.raw)
            self.store.append(
                answer,
                corrections,
                edges,
                flags,
                judgments=judged,
                vocabulary=vocabulary,
                declines=declines,
            )
            develop.keep(
                self.store.run_directory, answer.key, answer.attempt, answer.raw
            )
        return Recorded(answer, corrections, edges, flags, judged, vocabulary, declines)

    def checked_again(self, kept: AnswerRecord, validation: Validation) -> Recorded:
        """A kept attempt checked again under today's check and found
        valid (`review --file-answer`): a new attempt of its key, its raw,
        template, host, model and the rest the kept attempt's, costing
        nothing and taking no time, naming the attempt it checked again;
        its records numbered as any valid answer's."""
        if not validation.valid:
            raise ValueError("only a valid check of a kept answer is filed")
        with self._lock:
            number = self.store.next_attempt(kept.key)
            corrections, edges, flags, judged, vocabulary, declines = _numbered(
                kept.key, number, validation
            )
            answer = replace(
                kept,
                attempt=number,
                time=self._clock(),
                seconds=0.0,
                cost=0.0,
                tokens={},
                raw=kept.raw if validation.kept is None else validation.kept,
                valid=True,
                problems=(),
                records=(
                    *(c.id for c in corrections),
                    *edges.ids,
                    *(f.id for f in flags),
                    *judged.ids,
                    *(r.id for r in vocabulary),
                    *(r.id for r in declines),
                ),
                checked_again=kept.attempt,
            )
            self.store.append(
                answer,
                corrections,
                edges,
                flags,
                judgments=judged,
                vocabulary=vocabulary,
                declines=declines,
            )
            develop.keep(
                self.store.run_directory, answer.key, answer.attempt, answer.raw
            )
        return Recorded(answer, corrections, edges, flags, judged, vocabulary, declines)

    def resolve_identity(
        self, key: str, version: str, raw: str, links: tuple[LineageLink, ...]
    ) -> AnswerRecord:
        """An identity answer and its lineage: code's,
        no model's, asked of no host and costing nothing, `raw` the diff's
        JSON, held until its update keeps its step, numbered as any answer's
        records. `version` is identity's
        (`IDENTITY_VERSION`), its template's version."""
        with self._lock:
            number = self.store.next_attempt(key)
            ids = (f"{key[:12]}-{number}-{n}" for n in itertools.count(1))
            lineage = tuple(replace(r, id=next(ids), answer=key) for r in links)
            answer = AnswerRecord(
                key=key,
                kind=IDENTITY,
                stage=IDENTITY,
                template=TemplateRef(IDENTITY, version),
                attempt=number,
                host="axiomation",
                model="none",
                time=self._clock(),
                seconds=0.0,
                cost=0.0,
                raw=raw,
                valid=True,
                records=tuple(r.id for r in lineage),
            )
            # held before the answer is filed, as a run holds its texts
            self.store.hold_identity(key, json.loads(raw)["new"], raw)
            self.store.append(answer, lineage=lineage)
            develop.keep(self.store.run_directory, key, number, raw)
        return answer

    def file(
        self,
        kind: str,
        version: str,
        raw: str,
        records: JudgmentRecords,
        host: str,
        model: str,
        session: str | None = None,
    ) -> Filed:
        """A filing: a person's or an agent's judgments, findings
        or finding answers, or code's cited pages and works seen
        and its runs of a paper's script,
        asked of no host and costing nothing, its key
        the hash of `raw` (the filing's JSON), its records numbered as any
        answer's. It names no `version`, so no person round applies it and
        no pending list holds it; the records name their state. `version`
        is the filing form's, its template's version. The same filing again
        is refused."""
        key = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        with self._lock:
            refusal = torn(self.store, key)
            if refusal is not None:
                raise ValueError(refusal)
            if key in self.store.authoritative():
                raise ValueError(f"the store holds this filing already ({key[:12]})")
            number = self.store.next_attempt(key)
            ids = (f"{key[:12]}-{number}-{n}" for n in itertools.count(1))
            numbered = JudgmentRecords(
                **{
                    kind.field: tuple(
                        replace(r, id=next(ids), answer=key)
                        for r in getattr(records, kind.field)
                    )
                    for kind in JUDGMENT_KINDS
                }
            )
            answer = AnswerRecord(
                key=key,
                kind=kind,
                stage=JUDGMENTS_STAGE,
                template=TemplateRef(JUDGMENTS_STAGE, version),
                attempt=number,
                host=host,
                model=model,
                time=self._clock(),
                seconds=0.0,
                cost=0.0,
                raw=raw,
                valid=True,
                records=numbered.ids,
                session=session,
            )
            self.store.append(answer, judgments=numbered)
            develop.keep(self.store.run_directory, key, number, raw)
        return Filed(answer, numbered)

    @contextmanager
    def claim(self) -> Iterator[None]:
        """Hold the store for one run: a second run on the same store,
        in this process or another, is refused rather than let collide on
        an attempt number."""
        path = self.store.run_directory / RUN_LOCK
        with path.open("a") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise StoreBusy(
                    f"another run holds the store {self.store.directory}"
                ) from None
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)
