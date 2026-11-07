"""The question runner: the only door to a model.

It asks questions through one host, or a stage's through a host of its
own (the judge's; any stage's), concurrently, under a
budget cap for the run; checks each answer; re-asks a failed one within a bound; and
hands every attempt to the resolver, which alone writes the store. A
question whose key has an authoritative answer is not asked, so a stopped
run resumes from its records.

Each attempt may spend what is left of the budget, less what the
attempts in flight reserved, where its host takes a limit (Claude Code's
`--max-budget-usd`); one ended there is recorded and stops the run. A run
stops, starting nothing more and letting the attempts in flight finish
and be recorded, when the next attempt could pass the budget, when an
attempt reaches its limit, when the host refuses for billing, or when the
host fails three attempts in a row; on a breach of its directory it
stops at once, and the host ends the attempts in flight. The budget is
the runner's, across every `run` call: the driver's two stages share it.
A runner given what like runs cost (`expect`, for a budget the paper's
settings gave) also asks nothing of a `run` call whose questions are
expected to cost more than is left.

A stop between two attempts at one question loses the re-ask: a resumed
run asks it again from its first prompt, with a bound of three again, and
its answer's attempt number (so its correction ids) differs from an
uninterrupted run's. The byte-identical resume holds for stops between
questions.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path

from axiomation.questions import Question, Stage, Validation, reask
from axiomation.questions.review_file import current_kind
from axiomation.records import AnswerRecord, RecordError
from axiomation.resolver import Attempt, Resolver, torn
from axiomation.runner.hosts import (
    Host,
    HostAnswer,
    HostBreach,
    HostOutOfCredit,
    HostOverBudget,
    HostUncounted,
    PromptTooLong,
    Rates,
    estimate,
)

Check = Callable[[Question, str], Validation]

#: What an attempt is expected to cost over the runner's estimate where the
#: store holds no like answer: the top of what runs under Claude Code cost
#: over it. An estimate a fitted price of its turns completes
#: (`Question.turns_estimate`) is taken as it is.
UNMEASURED_FACTOR = 8.0


def like(template: str) -> str:
    """The runs a template's are like: its name before any `+` variant
    (`review-argument+scope` is `review-argument`), a session's earlier
    name read as its own (`review_file.current_kind`)."""
    return current_kind(template).split("+", 1)[0]


@dataclass(frozen=True)
class LikeRuns:
    """What the store's answers cost over the runner's estimate, by the
    runs they are like (`like`), from the attempts that recorded an
    estimate; for runs it holds none of, `UNMEASURED_FACTOR`, or 1 for an
    estimate with its turns fitted (`fitted`)."""

    factors: Mapping[str, float] = field(default_factory=dict)
    counts: Mapping[str, int] = field(default_factory=dict)

    def factor(self, template: str, fitted: bool = False) -> float:
        return self.factors.get(like(template), 1.0 if fitted else UNMEASURED_FACTOR)

    def expected(self, template: str, estimated: float, fitted: bool = False) -> float:
        return estimated * self.factor(template, fitted)


def like_runs(answers: Iterable[AnswerRecord]) -> LikeRuns:
    """The store's cost over estimate per kind of run: the sum of what the
    attempts cost over the sum of their estimates."""
    cost: dict[str, float] = {}
    estimated: dict[str, float] = {}
    counts: dict[str, int] = {}
    for answer in answers:
        if not answer.estimate:
            continue
        name = like(answer.template.name)
        cost[name] = cost.get(name, 0.0) + answer.cost
        estimated[name] = estimated.get(name, 0.0) + answer.estimate
        counts[name] = counts.get(name, 0) + 1
    return LikeRuns({n: cost[n] / estimated[n] for n in cost}, counts)


class Stop(StrEnum):
    """Why a run stopped before asking everything."""

    #: The next attempt could pass the budget.
    BUDGET = "budget"
    #: The host refused for billing: out of credit, over a usage limit.
    CREDIT = "credit"
    #: The host failed several attempts in a row, without an answer.
    HOST = "host"
    #: The model touched a path outside the host's directory.
    BREACH = "breach"
    #: The host could not say what an attempt cost.
    UNCOUNTED = "uncounted"


@dataclass(frozen=True, slots=True)
class RunReport:
    """One run. `asked` are the keys at least one attempt was made at;
    `answered` those that got an authoritative answer in the run; `failed`
    those that failed within their bound; `skipped` those that had one
    already. `spent` is the host's cost of this call's attempts."""

    asked: tuple[str, ...] = ()
    answered: tuple[str, ...] = ()
    failed: tuple[str, ...] = ()
    skipped: tuple[str, ...] = ()
    attempts: int = 0
    spent: float = 0.0
    stopped: Stop | None = None
    detail: str = ""


class _Outcome(StrEnum):
    ANSWERED = "answered"
    FAILED = "failed"
    UNFINISHED = "unfinished"


@dataclass
class _State:
    lock: threading.Lock = field(default_factory=threading.Lock)
    spent: float = 0.0
    reserved: float = 0.0
    attempts: int = 0
    asked: set[str] = field(default_factory=set)
    stopped: Stop | None = None
    detail: str = ""
    host_failures: int = 0
    too_long: list[str] = field(default_factory=list)

    def stop(self, why: Stop, detail: str) -> None:
        """Called under the lock. A breach overrides any other reason."""
        if self.stopped is None or why is Stop.BREACH:
            self.stopped = why
            self.detail = detail


@dataclass(frozen=True, slots=True)
class Seat:
    """A host and the rates its attempts are estimated at: the default
    one, or a stage's where it sits apart (the judge's; any
    stage's, a profile's)."""

    host: Host
    rates: Rates


class Runner:
    """Runs questions through `host` and records every attempt through
    `resolver`. `budget` is in dollars, for everything this runner asks,
    over all its `run` calls (`spent`); `rates` price the estimate made
    before each attempt; `seats` ask a stage's questions in place of
    `host` and its rates; `profile` is the profile the run is under,
    recorded on every answer, as `review_round`, a review session's round,
    and `review_state`, the kept state its brief read, are; `transcripts`,
    when given, is the directory each attempt's host log is written to;
    `expect`, what like runs cost, where the budget is the paper's
    default: a `run` call expected to cost more than is left asks
    nothing."""

    def __init__(
        self,
        host: Host,
        resolver: Resolver,
        budget: float,
        rates: Rates,
        jobs: int = 4,
        attempts: int = 3,
        timeout: float = 900.0,
        transcripts: Path | None = None,
        host_failures: int = 3,
        seats: Mapping[Stage, Seat] | None = None,
        profile: str | None = None,
        review_round: str | None = None,
        review_state: str | None = None,
        expect: LikeRuns | None = None,
    ) -> None:
        self.host = host
        self.expect = expect
        self.seats = dict(seats or {})
        self.profile = profile
        self.review_round = review_round
        self.review_state = review_state
        self.resolver = resolver
        self.budget = budget
        self.rates = rates
        self.jobs = jobs
        self.attempts = attempts
        self.timeout = timeout
        self.transcripts = transcripts
        self.host_failures = host_failures
        #: What this runner's attempts have cost so far, the host's figure.
        self.spent = 0.0

    def run(self, questions: Sequence[Question], check: Check) -> RunReport:
        """Ask every question without an authoritative answer, holding the
        store for the run (`Resolver.claim`)."""
        with self.resolver.claim():
            return self._run(questions, check)

    def _run(self, questions: Sequence[Question], check: Check) -> RunReport:
        authoritative = self.resolver.store.authoritative()
        # refused before any host is asked, not only when its answer is filed
        refusal = next(
            (r for q in questions if (r := torn(self.resolver.store, q.key))), None
        )
        if refusal is not None:
            raise RecordError(refusal)
        todo: list[Question] = []
        skipped: list[str] = []
        seen: set[str] = set()
        for question in questions:
            if question.key in seen:
                continue
            seen.add(question.key)
            if question.key in authoritative:
                skipped.append(question.key)
            else:
                todo.append(question)
        expected = self._expected(todo)
        if expected is not None and expected > self.budget - self.spent:
            return RunReport(
                skipped=tuple(skipped),
                stopped=Stop.BUDGET,
                detail=f"its {len(todo)} "
                + ("question is" if len(todo) == 1 else "questions are")
                + " expected to cost "
                + f"${expected:.2f}, above the ${self.budget - self.spent:.2f} left "
                f"of the budget of ${self.budget:g}: price it with --dry-run and "
                "give --budget",
            )
        state = _State(spent=self.spent)
        start = self.spent

        def ask(question: Question) -> _Outcome:
            try:
                return self._ask(question, check, state)
            except BaseException as error:
                # Start nothing more; the pool re-raises this once the
                # attempts in flight are done.
                with state.lock:
                    state.stop(Stop.HOST, f"the runner failed: {error!r}")
                raise

        try:
            with ThreadPoolExecutor(max_workers=max(1, self.jobs)) as pool:
                outcomes = list(pool.map(ask, todo))
        finally:
            self.spent = state.spent
        order = [q.key for q in todo]
        return RunReport(
            asked=tuple(key for key in order if key in state.asked),
            answered=tuple(
                q.key
                for q, o in zip(todo, outcomes, strict=True)
                if o is _Outcome.ANSWERED
            ),
            failed=tuple(
                q.key
                for q, o in zip(todo, outcomes, strict=True)
                if o is _Outcome.FAILED
            ),
            skipped=tuple(skipped),
            attempts=state.attempts,
            spent=state.spent - start,
            stopped=state.stopped,
            detail="; ".join(filter(None, [state.detail, *state.too_long])),
        )

    def _expected(self, questions: Sequence[Question]) -> float | None:
        """What `questions` are expected to cost, one attempt each, by what
        like runs cost; None without `expect`."""
        if self.expect is None:
            return None
        found = 0.0
        for question in questions:
            seat = self._seat(question)
            found += self.expect.expected(
                question.template.name,
                self._estimate(question, seat),
                question.turns_estimate > 0,
            )
        return found

    @staticmethod
    def _estimate(question: Question, seat: Seat) -> float:
        return (
            estimate(
                question.prompt,
                seat.rates,
                question.workspace,
                getattr(seat.host, "prompt_tokens", 0),
            )
            + question.turns_estimate
        )

    def _seat(self, question: Question) -> Seat:
        return self.seats.get(question.stage) or Seat(self.host, self.rates)

    def _ask(self, question: Question, check: Check, state: _State) -> _Outcome:
        prompt = question.prompt
        workspace = question.workspace
        seat = self._seat(question)
        host, rates = seat.host, seat.rates
        for _ in range(self.attempts):
            cost = (
                estimate(prompt, rates, workspace, getattr(host, "prompt_tokens", 0))
                + question.turns_estimate
            )
            with state.lock:
                if state.stopped is not None:
                    return _Outcome.UNFINISHED
                if state.spent + state.reserved + cost > self.budget:
                    state.stop(
                        Stop.BUDGET,
                        f"the next attempt could pass the budget of ${self.budget:g}",
                    )
                    return _Outcome.UNFINISHED
                # the attempt may spend what is left, less what the attempts
                # in flight reserved: the budget holds inside a session too
                spend_max = self.budget - state.spent - state.reserved
                state.reserved += cost
                state.attempts += 1
                state.asked.add(question.key)
            try:
                answer = host.ask(prompt, self.timeout, workspace, spend_max)
            except HostOverBudget as over:
                with state.lock:
                    state.reserved -= cost
                    state.spent += over.answer.cost
                    state.stop(Stop.BUDGET, str(over))
                self._record(
                    question,
                    host,
                    over.answer,
                    Validation(problems=(f"stopped at the budget: {over}",)),
                    cost,
                )
                return _Outcome.UNFINISHED
            except HostOutOfCredit as refusal:
                with state.lock:
                    state.reserved -= cost
                    state.spent += refusal.answer.cost
                    state.stop(Stop.CREDIT, str(refusal))
                self._transcript(question, "refused", refusal.answer, keep=True)
                return _Outcome.UNFINISHED
            except HostUncounted as uncounted:
                # Its cost stays out of `spent`, the host's figure: the
                # stop keeps the budget, losing at most the attempts in
                # flight.
                with state.lock:
                    state.reserved -= cost
                    state.stop(Stop.UNCOUNTED, str(uncounted))
                self._transcript(question, "uncounted", uncounted.answer, keep=True)
                return _Outcome.UNFINISHED
            except PromptTooLong as refused:
                # A re-ask the host cannot take: the question fails, with
                # nothing asked and nothing to record.
                with state.lock:
                    state.reserved -= cost
                    state.attempts -= 1
                    state.too_long.append(f"{question.key}: {refused}")
                return _Outcome.FAILED
            except HostBreach as breach:
                with state.lock:
                    state.reserved -= cost
                    state.spent += breach.answer.cost
                    state.stop(Stop.BREACH, str(breach))
                for seat_host in (self.host, *(s.host for s in self.seats.values())):
                    seat_host.abort()
                problem = f"the host was breached: {breach}"
                self._record(
                    question,
                    host,
                    breach.answer,
                    Validation(problems=(problem,)),
                    cost,
                )
                return _Outcome.UNFINISHED
            except BaseException:
                with state.lock:
                    state.reserved -= cost
                raise
            validation = self._check(question, answer, check)
            # No text, and a host error or not one token out: nothing the
            # model wrote, so nothing to re-ask about, and one more host
            # failure in a row (a refusal the adapter did not recognise
            # trips the breaker instead of failing every question).
            without_answer = not answer.text.strip() and (
                answer.failed or not answer.tokens.get("output")
            )
            with state.lock:
                state.reserved -= cost
                state.spent += answer.cost
                if without_answer:
                    state.host_failures += 1
                    if state.host_failures >= self.host_failures:
                        state.stop(
                            Stop.HOST,
                            f"the host failed {state.host_failures} attempts in a "
                            f"row; the last: {answer.error}",
                        )
                else:
                    state.host_failures = 0
            self._record(question, host, answer, validation, cost)
            if validation.valid:
                return _Outcome.ANSWERED
            # A host that failed has nothing for the model to correct.
            if not without_answer:
                prompt = reask(question, answer.text, validation.problems)
                if workspace is not None and workspace.reask_from_edit:
                    workspace = replace(
                        workspace,
                        files={**workspace.files, workspace.edit: answer.text},
                    )
        return _Outcome.FAILED

    @staticmethod
    def _check(question: Question, answer: HostAnswer, check: Check) -> Validation:
        # A session's tools are its host's to confine.
        if answer.tool_uses and question.workspace is None:
            names = ", ".join(
                sorted({str(use.get("tool")) for use in answer.tool_uses})
            )
            return Validation(problems=(f"the host used a tool: {names}",))
        if answer.failed:
            return Validation(problems=(f"the host failed: {answer.error}",))
        if not answer.text.strip():
            return Validation(problems=("the host gave no answer",))
        return check(question, answer.text)

    def _record(
        self,
        question: Question,
        host: Host,
        answer: HostAnswer,
        validation: Validation,
        estimated: float | None = None,
    ):
        recorded = self.resolver.record(
            Attempt(
                question=question,
                host=host.name,
                model=answer.model or host.model,
                text=answer.text,
                cost=answer.cost,
                seconds=answer.seconds,
                validation=validation,
                tokens=answer.tokens,
                tokens_by_model=answer.tokens_by_model,
                effort=host.effort,
                profile=self.profile,
                review_round=self.review_round,
                state=self.review_state,
                estimate=estimated,
            )
        )
        self._transcript(question, str(recorded.answer.attempt), answer)
        return recorded

    def _transcript(
        self, question: Question, name: str, answer: HostAnswer, keep: bool = False
    ) -> None:
        """`keep`: never overwrite, numbering the name (`refused-2`)."""
        if self.transcripts is None:
            return
        self.transcripts.mkdir(parents=True, exist_ok=True)
        stem = f"{question.key[:16]}-{name}"
        path = self.transcripts / f"{stem}.jsonl"
        n = 1
        while keep and path.exists():
            n += 1
            path = self.transcripts / f"{stem}-{n}.jsonl"
        path.write_text(answer.transcript, encoding="utf-8")
