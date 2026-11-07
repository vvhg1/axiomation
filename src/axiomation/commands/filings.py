"""The filings on a kept state by a person or an agent: a judgment, a
finding and their answers, a ruling, a suggestion and its answer, a
contribution's check, a reread, a run seen; each recorded by the
resolver and said with its id, and, where it moves them, the gates."""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from axiomation import (
    check,
    computations,
    evidence,
    judgments,
    marks,
    positioning,
    queries,
    review,
    rulings,
    shares,
    suggestions,
    versions,
)
from axiomation.commands import (
    DRY_RUN_HELP,
    STORE_HELP,
    Paper,
    Say,
    UsageError,
    asks,
    chosen,
    kept_arguments,
    kept_on,
    kept_store,
    paper_of,
    prompts_argument,
    requested,
    scratch_copy,
    seating,
)
from axiomation.commands.seating import Asking
from axiomation.cutter.model import Role
from axiomation.paper_argument import directory_argument
from axiomation.records import (
    AGENT,
    JUDGED_ASPECTS,
    PERSON,
    AnchorKind,
    Aspect,
    Depth,
    FindingStatus,
    RecordStore,
    RulingKind,
    Status,
    Subject,
    SuggestionStatus,
    ValuePair,
    Verdict,
    judgment_data,
)
from axiomation.versions import State


@dataclass(frozen=True, kw_only=True)
class Filing:
    """A filing's paper and who files it: an agent by its model and
    session, a person by name, else the one unnamed person; JSON or
    lines."""

    paper: Paper
    agent: str | None = None
    session: str | None = None
    person: str | None = None
    json: bool = False


@dataclass(frozen=True, kw_only=True)
class Judge(Filing):
    aspect: str
    on: str
    verdict: str
    depth: str
    evidence: str
    target: str | None = None
    claim: str | None = None
    span: str | None = None
    work: str | None = None
    file: str | None = None
    run: str | None = None
    value: tuple[tuple[str, str], ...] = ()
    code: tuple[str, ...] = ()
    page: tuple[str, ...] = ()
    propose: bool = False
    run_arguments: tuple[str, ...] = ()


@dataclass(frozen=True, kw_only=True)
class Reaffirm(Filing):
    judgment: str
    depth: str
    evidence: str
    page: tuple[str, ...] = ()


@dataclass(frozen=True, kw_only=True)
class Find(Filing):
    aspect: str
    severity: int
    problem: str
    on: tuple[str, ...] = ()
    part: tuple[tuple[str, str], ...] = ()
    span: tuple[tuple[str, str], ...] = ()
    #: `--paper`: the paper as an anchor
    on_paper: bool = False
    work: tuple[str, ...] = ()
    file: tuple[tuple[str, str], ...] = ()
    fix: str | None = None


@dataclass(frozen=True, kw_only=True)
class Suggest(Filing):
    on: str
    work: tuple[str, ...]
    why: str


@dataclass(frozen=True, kw_only=True)
class AnswerSuggestion(Filing):
    suggestion: str
    status: str
    note: str | None = None


@dataclass(frozen=True, kw_only=True)
class Run(Filing):
    file: str
    fresh: bool = False
    run_arguments: tuple[str, ...] = ()


@dataclass(frozen=True, kw_only=True)
class AnswerFinding(Filing):
    finding: str
    status: str | None = None
    note: str | None = None
    of: str | None = None
    propose: bool = False
    withdraw: bool = False
    reason: str | None = None


@dataclass(frozen=True, kw_only=True)
class ReaffirmFinding(Filing):
    finding: str
    evidence: str
    words: str | None = None


@dataclass(frozen=True, kw_only=True)
class Adopt(Filing):
    judgment: str


@dataclass(frozen=True, kw_only=True)
class Rule(Filing):
    reason: str
    on: str | None = None
    within: tuple[str, ...] = ()
    role: tuple[str, ...] = ()
    dry_run: bool = False
    claim: str | None = None
    ruling: str | None = None
    severity: int | None = None
    fix: str | None = None
    propose: bool = False
    withdraw: str | None = None
    page: tuple[str, ...] = ()


@dataclass(frozen=True, kw_only=True)
class CheckContribution(Filing):
    on: str
    searched: str
    found: str
    claim: str | None = None


@dataclass(frozen=True, kw_only=True)
class Reread(Filing):
    """`reread PAPER OWNER… --reason`: a person's or an agent's reread;
    `--ask` an agent's (`asks.reread_asked`)."""

    owner: tuple[str, ...] = ()
    reason: str | None = None
    ask: bool = False
    dry_run: bool = False
    prompts: Path | None = None
    asking: Asking = field(default_factory=Asking)


#: Each filing's request, by its command.
KINDS: dict[str, type[Filing]] = {
    "judge": Judge,
    "reaffirm": Reaffirm,
    "find": Find,
    "suggest": Suggest,
    "answer-suggestion": AnswerSuggestion,
    "run": Run,
    "answer-finding": AnswerFinding,
    "reaffirm-finding": ReaffirmFinding,
    "adopt": Adopt,
    "rule": Rule,
    "check-contribution": CheckContribution,
    "reread": Reread,
}


def request(arguments: argparse.Namespace) -> Filing:
    """The filing a command's parsed arguments give."""
    kind = KINDS[arguments.command]
    given: dict[str, Any] = {"paper": paper_of(arguments)}
    if kind is Find:
        given["on_paper"] = arguments.paper
    if kind is Reread:
        given["asking"] = seating.asking(arguments)
    return requested(kind, arguments, **given)


def file(asked: Filing, say: Say) -> str:
    """The filing recorded, said with its id."""
    match asked:
        case Reread():
            return reread(asked, say)
        case Run():
            return run_script(asked)
    return FILINGS[type(asked)](asked)


def answer_suggestion(asked: AnswerSuggestion) -> str:
    store = kept_store(asked.paper)
    author = _filer(asked)
    filed = suggestions.answer(
        store,
        asked.suggestion,
        SuggestionStatus(asked.status),
        asked.note,
        author,
    )
    (decided,) = filed.records.suggestion_answers
    return _filed_text(
        asked,
        filed,
        [
            f"suggestion {decided.suggestion} answered {decided.status.value}: "
            f"{decided.id}"
        ],
    )


def answer_finding(asked: AnswerFinding) -> str:
    store = kept_store(asked.paper)
    author = _filer(asked)
    if asked.withdraw:
        if asked.status or asked.note or asked.of or asked.propose:
            raise UsageError(
                "--withdraw names the answer and gives --reason: no --as, --note, "
                "--of or --propose"
            )
        if not asked.reason:
            raise UsageError("a withdrawal gives its --reason")
        filed = judgments.withdraw_answer(store, asked.finding, asked.reason, author)
        (withdrawal,) = filed.records.answer_withdrawals
        (answer,) = [
            a
            for a in store.judgment_records().answers
            if a.id == withdrawal.finding_answer
        ]
        # the finding as it reads now: its latest filing, which a review may
        # have refiled since the answer
        now = judgments.latest_filing(
            store, judgments.finding_named(store, answer.finding)
        )
        speaks, proposal = judgments.spoken(judgments.answers_of(store).get(now.id, []))
        if speaks is not None:
            after = f"answer {speaks.id} speaks now: {speaks.status.value}"
        elif proposal is not None:
            after = f"open, its proposal {proposal.answer.id} waiting to be settled"
        else:
            after = "open: answer it, or propose (--propose --note)"
        lines = [
            f"answer {answer.id} ({answer.status.value}) on finding {answer.finding} "
            f"withdrawn by {author.name}: {withdrawal.id}",
            f"  finding {now.id}: {after}",
        ]
        return _filed_text(asked, filed, lines)
    if asked.status is None:
        raise UsageError("an answer names its --as")
    if asked.reason:
        raise UsageError("--reason is a withdrawal's; an answer's why is --note")
    filed = judgments.answer_finding(
        store,
        asked.finding,
        FindingStatus(asked.status),
        asked.note,
        author,
        asked.of,
        asked.propose,
    )
    (record,) = filed.records.answers
    said = f"{record.status.value}" + (f" of {record.of}" if record.of else "")
    named = judgments.finding_named(store, asked.finding).id
    refiled = f" (refiled from {named})" if named != record.finding else ""
    if not (author.proposes or record.proposed):
        lines = [f"finding {record.finding}{refiled} answered {said}: {record.id}"]
        answered = judgments.finding_named(store, record.finding)
        weighed = judgments.turned_down(store, answered)
        # where a person most easily answers what they meant to propose:
        # their proposal refiled standing, which a proposal answers again
        if (
            author.person
            and not answered.reading
            and weighed is not None
            and weighed.author.identity == author.identity
        ):
            lines.append(
                "  answered, closing it; --propose would have asked the review "
                "to weigh the note. To take it back: axiomation answer-finding "
                f'{asked.paper.directory} {record.id} --withdraw --reason "…"'
            )
        return _filed_text(asked, filed, lines)
    found = judgments.finding_named(store, record.finding)
    filer = judgments.author_of(store.authoritative(), found.answer)
    # a person's `postponed` speaking is theirs to settle, never a review's
    by_review = filer.by_review and (judgments.speaking_answer(store, found.id) is None)
    lines = [
        f"finding {record.finding}{refiled}: proposed {said} by {author.name}: "
        f"{record.id}",
        "  a proposal, not an answer: the finding stays open, in `check` and "
        "`open`, until "
        + (
            "the review of its kind settles it (its `fixed` or `stands` line) "
            "or a person answers it"
            if by_review
            else "a person answers it"
        ),
    ]
    return _filed_text(asked, filed, lines)


def reread(asked: Reread, say: Say) -> str:
    if asked.ask:
        store = kept_store(asked.paper)
        ask = asks.Reread(
            paper=asked.paper,
            owner=asked.owner,
            dry_run=asked.dry_run,
            prompts=asked.prompts,
            agent=asked.agent,
            person=asked.person,
            session=asked.session,
            reason=asked.reason,
            asking=asked.asking,
        )
        # its edges edit is kept on top of the state it reads
        return asks.reread_asked(ask, store, kept_on(asked.paper, store), say).text
    if not asked.owner or not asked.reason:
        raise UsageError(
            "a reread names its owners and gives --reason; --ask has an agent read them"
        )
    for flag, given in (("--dry-run", asked.dry_run), ("--prompts", asked.prompts)):
        if given:
            raise UsageError(f"{flag} goes with --ask: without it nothing is asked")
    store, author, kept, view = _opened(asked)
    before = check.verdict(view, asked.paper.directory)
    owners = [view.resolve(ref) for ref in asked.owner]
    filed = marks.reread(
        store, kept, [view.nodes[o] for o in owners], asked.reason, author
    )
    lines = [
        f"reread {r.id} at state {kept.id} by {author.name}: {view.handle(o)}"
        for r, o in zip(filed.records.rereads, dict.fromkeys(owners), strict=True)
    ]
    after = check.verdict(queries.View(store, kept), asked.paper.directory)
    gates = check.delta(before, after)
    lines += [line for i in gates for line in i.line.splitlines()] or [
        "gates: unchanged"
    ]
    return _filed_text(asked, filed, lines)


def suggest(asked: Suggest) -> str:
    store, author, kept, view = _opened(asked)
    at = judgments.seen(store, kept)
    place = view.resolve(asked.on)
    anchors = [
        judgments.anchored(
            at,
            AnchorKind.GROUP if place in view.groups else AnchorKind.BLOCK,
            place,
        ),
        *(judgments.anchored(at, AnchorKind.WORK, work=w) for w in asked.work),
    ]
    filed = suggestions.suggest(store, kept, anchors, asked.why, author)
    (offered,) = filed.records.suggestions
    return _filed_text(
        asked,
        filed,
        [
            f"suggestion {offered.id} at state {kept.id}: "
            + " ".join(f"work:{w}" for w in asked.work)
            + f" at {view.handle(place)}"
        ],
    )


def rule(asked: Rule) -> str:
    store, author, kept, view = _opened(asked)
    before = check.verdict(view, asked.paper.directory)
    at = judgments.seen(store, kept)
    if asked.withdraw is not None:
        if (
            asked.on
            or asked.ruling
            or asked.claim
            or asked.within
            or asked.role
            or asked.dry_run
            or asked.severity is not None
            or asked.fix is not None
            or asked.propose
            or asked.page
        ):
            raise UsageError(
                "--withdraw names the ruling: no --on, --claim, --in, --role, "
                "--ruling, --severity, --fix, --propose, --page or --dry-run"
            )
        filed = rulings.withdraw(store, asked.withdraw, asked.reason, author)
        (withdrawal,) = filed.records.withdrawals
        lines = [
            f"ruling {withdrawal.ruling} withdrawn by {author.name}: {withdrawal.id}"
        ]
    elif asked.within or asked.role:
        if asked.on or asked.claim:
            raise UsageError(
                "--in and --role select a set: no --on or --claim, which name one"
            )
        if not asked.ruling:
            raise UsageError("a ruling on a set names its --ruling")
        if asked.propose:
            raise UsageError("a proposed ruling names one claim (--on): propose each")
        if not author.person:
            raise UsageError(
                "a ruling on a set is a person's: an agent rules each claim (--on)"
            )
        if asked.dry_run:
            with scratch_copy(asked.paper) as paper:
                text = rule(replace(asked, paper=paper, dry_run=False))
            if asked.json:
                return (
                    json.dumps(
                        {**json.loads(text), "dry_run": True},
                        indent=2,
                        ensure_ascii=False,
                    )
                    + "\n"
                )
            return text + "dry run: nothing was filed\n"
        return _rule_set(asked, store, kept, view, author, before)
    else:
        if asked.dry_run:
            raise UsageError("--dry-run is a ruling on a set's (--in, --role)")
        if not asked.on or not asked.ruling:
            raise UsageError("a ruling names its claim's block (--on) and --ruling")
        on = rulings.subject(at, view.resolve(asked.on), asked.claim)
        pages, sources = _pages(asked)
        filed = rulings.rule(
            store,
            kept,
            on,
            RulingKind(asked.ruling),
            asked.reason,
            author,
            severity=asked.severity,
            fix=asked.fix,
            propose=asked.propose,
            pages=pages,
            sources=sources,
        )
        (ruling,) = filed.records.rulings
        lines = [
            f"{'proposed ruling' if ruling.proposed else 'ruling'} {ruling.id} at "
            f"state {kept.id} by {author.name}: "
            f'{at.handle(on.node)} "{on.words}" {ruling.kind.value}'
            + (f" {ruling.severity}" if ruling.severity is not None else ""),
            *_stamp_lines(ruling),
        ]
        if ruling.proposed:
            lines.append(
                "  it changes no status: the next argument review rules the "
                "claim again with it in view"
            )
    after = check.verdict(queries.View(store, kept), asked.paper.directory)
    gates = check.delta(before, after)
    lines += [line for i in gates for line in i.line.splitlines()] or [
        "gates: unchanged"
    ]
    return _filed_text(asked, filed, lines)


def check_contribution(asked: CheckContribution) -> str:
    store, author, kept, view = _opened(asked)
    before = check.verdict(view, asked.paper.directory)
    at = judgments.seen(store, kept)
    on = judgments.subject(at, Aspect.TRUE, view.resolve(asked.on), claim=asked.claim)
    filed = positioning.check(store, kept, on, asked.searched, asked.found, author)
    (record,) = filed.records.checks
    lines = [
        f"check {record.id} at state {kept.id} by {author.name}: "
        f'{at.handle(on.node)} "{on.words}"'
    ]
    after = check.verdict(queries.View(store, kept), asked.paper.directory)
    lines += [
        line for i in check.delta(before, after) for line in i.line.splitlines()
    ] or ["gates: unchanged"]
    return _filed_text(asked, filed, lines)


def adopt(asked: Adopt) -> str:
    store, author, kept, view = _opened(asked)
    filed = judgments.adopt(store, kept, asked.judgment, author)
    (record,) = filed.records.judgments
    lines = [
        f"judgment {record.id} at state {kept.id} by {author.name}: adopts "
        f"{record.adopts}, {record.aspect.value} {record.verdict.value} "
        f"({record.depth.value})",
        *_stamp_lines(record),
    ]
    return _filed_text(asked, filed, lines)


def reaffirm_finding(asked: ReaffirmFinding) -> str:
    store, author, kept, view = _opened(asked)
    filed = judgments.reaffirm_finding(
        store,
        kept,
        asked.finding,
        asked.evidence,
        author,
        asked.words,
        view.texts.get,
    )
    (found,) = filed.records.findings
    lines = [
        f"finding {found.id} at state {kept.id}: reaffirms {found.reaffirms}, "
        f"severity {found.severity} {found.aspect.value}",
        f"basis ({len(found.basis)}):",
    ]
    lines += [
        "  " + line
        for e in found.basis
        for line in queries.element_line(view, e, None)[:1]
    ]
    return _filed_text(asked, filed, lines)


def reaffirm(asked: Reaffirm) -> str:
    store, author, kept, view = _opened(asked)
    pages, sources = _pages(asked)
    filed = judgments.reaffirm(
        store,
        kept,
        asked.judgment,
        Depth(asked.depth),
        asked.evidence,
        author,
        # a cited span counted in the text `judge` counts it in
        view.texts.get,
        pages,
        sources,
    )
    (record,) = filed.records.judgments
    lines = [
        f"judgment {record.id} at state {kept.id}: reaffirms {record.reaffirms}, "
        f"{record.verdict.value} ({record.depth.value})",
        *_stamp_lines(record),
        f"basis ({len(record.basis)}):",
    ]
    lines += [
        "  " + line
        for e in record.basis
        for line in queries.element_line(view, e, None)[:1]
    ]
    return _filed_text(asked, filed, lines)


def judge(asked: Judge) -> str:
    store, author, kept, view = _opened(asked)
    at = judgments.seen(store, kept)
    aspect = Aspect(asked.aspect)
    on = view.resolve(asked.on)
    if asked.run and asked.file and asked.run != asked.file:
        raise UsageError("--run names the subject's file: drop --file or match it")
    if asked.run_arguments and not asked.run:
        raise UsageError("arguments after `--` are a run's: name it with --run")
    found = judgments.subject(
        at,
        aspect,
        on,
        None if asked.target is None else view.resolve(asked.target),
        asked.claim,
        asked.span,
        view.texts.get(on),
        asked.work,
        asked.run or asked.file,
    )
    pages, sources = _pages(asked)
    ran = (
        None
        if not asked.run
        else computations.run(
            asked.paper.directory,
            asked.run,
            asked.run_arguments,
            RecordStore(asked.paper.store).run_directory / computations.RUN_CACHE,
        )
    )
    filed = judgments.judge(
        store,
        kept,
        aspect,
        found,
        Verdict(asked.verdict),
        Depth(asked.depth),
        asked.evidence,
        author,
        pages=pages,
        sources=sources,
        ran=ran,
        values=[ValuePair(t, o) for t, o in asked.value],
        code=list(asked.code),
        propose=asked.propose,
    )
    (record,) = filed.records.judgments
    head = "\n".join(
        [
            f"judgment {record.id} at state {kept.id}"
            + (
                f": proposed {record.verdict.value} by {author.name}; it changes "
                "no status: the next review of its kind reads the subject again "
                "with it in view (`axiomation sync`)"
                if record.proposed
                else ""
            ),
            *_stamp_lines(record),
            *([] if ran is None else _ran_text(ran)),
        ]
    )
    return _with_basis(asked, filed, view, head, record)


def find(asked: Find) -> str:
    store, author, kept, view = _opened(asked)
    at = judgments.seen(store, kept)
    aspect = Aspect(asked.aspect)
    anchors = [
        judgments.anchored(
            at,
            AnchorKind.GROUP if view.resolve(ref) in view.groups else AnchorKind.BLOCK,
            view.resolve(ref),
        )
        for ref in asked.on
    ]
    for kind, given in (
        (AnchorKind.PART, asked.part),
        (AnchorKind.SPAN, asked.span),
    ):
        for ref, words in given:
            block = view.block(ref)
            anchors.append(
                judgments.anchored(at, kind, block, words, text=view.texts[block])
            )
    if asked.on_paper:
        anchors.append(judgments.anchored(at, AnchorKind.PAPER))
    anchors += [judgments.anchored(at, AnchorKind.WORK, work=w) for w in asked.work]
    for key, words in asked.file:
        held = kept.files.get(key)
        text = None if held is None else versions.blob(store, held).decode()
        anchors.append(
            judgments.anchored(at, AnchorKind.FILE, words=words, text=text, file=key)
        )
    filed = judgments.find(
        store,
        kept,
        aspect,
        anchors,
        asked.severity,
        asked.problem,
        asked.fix,
        author,
    )
    (record,) = filed.records.findings
    head = f"finding {record.id} at state {kept.id}"
    said = _with_basis(asked, filed, view, head, record)
    if (Path(asked.paper.directory) / shares.WORKSPACE).is_file():
        # a reviewer's finding reaches no one until their review is signed
        said += (
            "\nnot sent yet: `axiomation confirm`, then `axiomation "
            "review-share sign` sends this round's findings to the journal"
        )
    return said


def _with_basis(
    asked: Filing, filed: Any, view: queries.View, head: str, record: Any
) -> str:
    lines = [head, f"basis ({len(record.basis)}):"]
    lines += [
        "  " + line
        for e in record.basis
        for line in queries.element_line(view, e, None)[:1]
    ]
    return _filed_text(asked, filed, lines)


def _opened(
    asked: Filing,
) -> tuple[RecordStore, judgments.Author, State, queries.View]:
    """A filing's store, its author, the kept state it is filed on and that
    state read."""
    store = kept_store(asked.paper)
    author = _filer(asked)
    kept = chosen(asked.paper, store)
    return store, author, kept, queries.View(store, kept)


def _rule_set(
    asked: Rule,
    store: RecordStore,
    kept: State,
    view: queries.View,
    author: judgments.Author,
    before: check.Verdict,
) -> str:
    """`rule --in NAMES --role ROLE`: one ruling on each claim standing
    unsupported in the blocks the names select (`review.in_scope`; each
    name must select a block) whose block carries one of the roles, filed
    together; a claim this person already ruled so is left as it is. The
    lines: each claim ruled, the person's earlier ruling each replaces,
    what each guard holds back after (`docs/model.md` § 8), what was
    refused, and the gates' change."""
    kind = RulingKind(asked.ruling)
    try:
        names = [
            review.scope_name(view, n.strip())
            for given in asked.within
            for n in given.split(",")
            if n.strip()
        ]
    except review.ReviewError as error:
        raise UsageError(str(error)) from None
    if asked.within and not names:
        raise UsageError("--in names nothing: give sections, lines or handles")
    chosen: dict[str, None] = {}
    for name in names:
        selected = [b for b in view.blocks if review.in_scope(view, b, name)]
        if not selected:
            raise UsageError(
                f"{name!r} selects no block: a section's number, label or title, "
                "lines (paper.md:29-193) or a handle"
            )
        chosen.update(dict.fromkeys(selected))
    within = set(chosen) if asked.within else set(view.blocks)
    wanted = set(asked.role)
    at = view.readings.at
    claims: list[tuple[Subject, str]] = []
    for e in view.established:
        c = e.claim
        block = at.ident_of.get(c.node)
        if (
            c.status is Status.UNSUPPORTED
            and block in within
            and (not wanted or wanted & set(view.roles(block)))
        ):
            claims.append((Subject(c.node, None, c.words), block))
    lines: list[str] = []
    refused = [
        n for n in names if not any(review.in_scope(view, b, n) for _, b in claims)
    ]
    held_none = "no claim standing unsupported" + (
        " in a block of the roles given" if wanted else ""
    )
    ruling_now: list[Subject] = []
    replaced: list[str] = []
    already: list[str] = []
    for on, _ in claims:
        ruled = view.rulings.get(on)
        own = [
            r
            for r in (ruled.speaks if ruled else ())
            if r.author.identity == author.identity
        ]
        # an owed ruling is the same only with the same weight and fix: a
        # person weighs an old one by ruling it again
        if any(
            r.ruling.kind is kind
            and (r.ruling.severity, r.ruling.fix) == (asked.severity, asked.fix)
            for r in own
        ):
            already.append(f'  {view.handle(at.ident_of[on.node])} "{on.words}"')
            continue
        ruling_now.append(on)
        replaced += [
            f'  {view.handle(at.ident_of[on.node])} "{on.words}": your '
            f"{r.ruling.kind.value} {r.ruling.id}"
            for r in own
        ]
    if not ruling_now:
        lines.append(
            f"nothing to rule: {len(claims)} claims standing unsupported "
            "selected, each ruled so by you already"
            if claims
            else "nothing to rule: no claim standing unsupported is selected"
        )
        lines += [f"refused, {held_none}: {n}" for n in refused]
        if asked.json:
            return json.dumps({"answer": None, "records": []}, indent=2) + "\n"
        return "\n".join(lines) + "\n"
    pages, sources = _pages(asked)
    filed = rulings.rule_each(
        store,
        kept,
        ruling_now,
        kind,
        asked.reason,
        author,
        severity=asked.severity,
        fix=asked.fix,
        pages=pages,
        sources=sources,
    )
    after_view = queries.View(store, kept)
    supports = {
        (e.claim.node, e.claim.words): s
        for e, s in zip(after_view.established, after_view.support, strict=True)
    }
    count = len(filed.records.rulings)
    lines.append(
        f"{count} ruling{'s' if count != 1 else ''} {kind.value} at state {kept.id} "
        f"by {author.name}, filing {filed.answer.key[:12]}:"
    )
    held: list[str] = []
    for record in filed.records.rulings:
        on = record.subject
        where = f'{view.handle(at.ident_of[on.node])} "{on.words}"'
        lines.append(f"  {record.id} {where}")
        found = supports.get((on.node, on.words))
        if found is not None and found.guard is not None:
            relied = (
                ", relied on by "
                + ", ".join(after_view.readings.at.handle(n) for n in found.relied)
                if found.relied
                else ""
            )
            held.append(f"  {where}: {found.guard.value}{relied}")
    if replaced:
        lines.append(f"replaces your earlier ruling on {len(replaced)}:")
        lines += replaced
    if held:
        lines.append(f"held back by a guard, clearing nothing ({len(held)}):")
        lines += held
    if already:
        lines.append(f"ruled {kind.value} by you already, left as is ({len(already)}):")
        lines += already
    lines += [f"refused, {held_none}: {n}" for n in refused]
    gates = check.delta(before, check.verdict(after_view, asked.paper.directory))
    lines += [line for i in gates for line in i.line.splitlines()] or [
        "gates: unchanged"
    ]
    return _filed_text(asked, filed, lines)


def run_script(asked: Run) -> str:
    """`run`: the code file run, or read from the cache, its stamp
    filed as a run seen, its stamp and output printed; on a paper that keeps
    a state, whose judgments a run bears on."""
    kept_store(asked.paper)
    ran = computations.run(
        asked.paper.directory,
        asked.file,
        asked.run_arguments,
        RecordStore(asked.paper.store).run_directory / computations.RUN_CACHE,
        fresh=asked.fresh,
    )
    store = kept_store(asked.paper)
    filed = judgments.see_runs(store, [ran.stamp], _filer(asked))
    if asked.json:
        return _filed_text(asked, filed, [])
    return "\n".join([*_ran_text(ran), "stdout:", ran.stdout.rstrip()])


def _ran_text(ran: computations.Ran) -> list[str]:
    stamp = ran.stamp
    lines = [
        f"run {stamp.name}: {stamp.status.value} in {ran.seconds} s, output "
        f"{stamp.output[:12]}",
        f"  {stamp.runner} {stamp.key} {' '.join(stamp.arguments)}".rstrip()
        + f"  (from {stamp.directory}; blob {stamp.blob[:12]})",
    ]
    if not stamp.ran and ran.stderr.strip():
        lines += ["stderr (its end):", *ran.stderr.strip().splitlines()[-20:]]
    return lines


def _filer(asked: Filing) -> judgments.Author:
    if bool(asked.agent) != bool(asked.session):
        raise UsageError(
            "--agent and --agent-session go together: the model and its session"
        )
    if asked.agent and asked.person is not None:
        raise UsageError("--agent or --person: a filing has one author")
    if asked.agent:
        return judgments.Author(AGENT, asked.agent, asked.session)
    if asked.person is not None:
        name = " ".join(asked.person.split())
        if not name:
            raise UsageError("--person names the person")
        return judgments.Author(PERSON, name)
    return judgments.Author()


def _pages(
    asked: Judge | Reaffirm | Rule,
) -> tuple[list[tuple[str, int]], evidence.Sources | None]:
    """The `--page`s named and the paper's sources to read them through,
    opened only where a page is named."""
    pages = [evidence.parse_page(ref) for ref in asked.page]
    sources = (
        evidence.sources_for(
            asked.paper.directory, RecordStore(asked.paper.store).run_directory
        )
        if pages
        else None
    )
    return pages, sources


def _stamp_lines(record: Any) -> list[str]:
    if not record.pages:
        return []
    lines = [
        f"pages ({len(record.pages)})"
        + (", provisional" if evidence.provisional(record.pages) else "")
        + ":"
    ]
    lines += [f"  {s.name} {s.read_status.value}" for s in record.pages]
    return lines


def _filed_text(asked: Filing, filed: Any, lines: list[str]) -> str:
    if asked.json:
        return (
            json.dumps(
                {
                    "answer": filed.answer.key,
                    "records": [judgment_data(r) for r in filed.records.all()],
                },
                indent=2,
                ensure_ascii=False,
            )
            + "\n"
        )
    return "\n".join(lines) + "\n"


#: Each filing's function, by its request; `reread` and `run` take more.
FILINGS: dict[type[Filing], Callable[[Any], str]] = {
    Judge: judge,
    Reaffirm: reaffirm,
    Find: find,
    Suggest: suggest,
    AnswerSuggestion: answer_suggestion,
    AnswerFinding: answer_finding,
    ReaffirmFinding: reaffirm_finding,
    Adopt: adopt,
    Rule: rule,
    CheckContribution: check_contribution,
}


def _judge_arguments(commands: Any) -> None:
    judge = commands.add_parser(
        "judge",
        help="file a judgment on a kept state: its subject, aspect, verdict, depth "
        "and evidence; it makes no new state",
    )
    kept_arguments(judge)
    judge.add_argument(
        "--aspect",
        required=True,
        choices=[a.value for a in JUDGED_ASPECTS],
        help="what is judged (each aspect: docs/model.md § 8)",
    )
    judge.add_argument(
        "--on",
        required=True,
        metavar="BLOCK",
        help="the subject's block (J, a block or group, for valid)",
    )
    judge.add_argument(
        "--for",
        dest="target",
        metavar="BLOCK",
        help="valid: T, the block whose claim J argues (default: J's one)",
    )
    judge.add_argument(
        "--claim", help="the claim's words where its block holds several"
    )
    judge.add_argument("--span", help="cited: the citation site's words, once")
    judge.add_argument(
        "--work", metavar="KEY", help="cited: the cited work's key, one its block cites"
    )
    judge.add_argument(
        "--file",
        metavar="KEY",
        help="computed: the code file the block uses, as its edge names it "
        "(default: its one)",
    )
    judge.add_argument(
        "--run",
        metavar="KEY",
        help="computed: run this code file, its arguments after `--`, and read "
        "the run (cached by the file's content, runner and arguments)",
    )
    judge.add_argument(
        "--value",
        nargs=2,
        action="append",
        default=[],
        metavar=("TEXT", "OUTPUT"),
        help="computed: a value the judgment matched, the block's words for it and "
        "the run's output's, each found where it lives (repeat; a verified "
        "judgment names one at least)",
    )
    judge.add_argument(
        "--code",
        action="append",
        default=[],
        metavar="LINE",
        help="computed: a line of the script that computes the values, copied "
        "from it (repeat; a verified judgment quotes one at least)",
    )
    _page_argument(judge)
    judge.add_argument(
        "--verdict",
        required=True,
        choices=[v.value for v in Verdict],
        help="what the reading found: shown, not shown, shown false, or could not tell",
    )
    judge.add_argument(
        "--depth",
        required=True,
        choices=[d.value for d in Depth],
        help="how it was read: by reading, by computing, or by a formal proof",
    )
    judge.add_argument("--evidence", required=True, help="what the reading found")
    judge.add_argument(
        "--propose",
        action="store_true",
        help="a person's reading filed as a proposal: it changes no status, and "
        "the next review of its kind reads the subject again with it in view "
        "and settles it",
    )
    _filer_arguments(judge)


def _reaffirm_arguments(commands: Any) -> None:
    reaffirm = commands.add_parser(
        "reaffirm",
        help="file that a lapsed judgment's conclusion stands on its basis as it "
        "reads now: its subject and verdict, the weaker depth",
    )
    kept_arguments(reaffirm)
    reaffirm.add_argument("judgment", help="a judgment's id, or a unique prefix of it")
    reaffirm.add_argument(
        "--depth",
        required=True,
        choices=[d.value for d in Depth],
        help="the re-reading's method; the record takes the weaker of it and the "
        "judgment's",
    )
    reaffirm.add_argument(
        "--evidence", required=True, help="what the re-reading of the change found"
    )
    _page_argument(reaffirm)
    _filer_arguments(reaffirm)


def _find_arguments(commands: Any) -> None:
    find = commands.add_parser(
        "find",
        help="file a finding on a kept state: a problem, its fix, a severity and "
        "its anchors",
    )
    kept_arguments(find)
    find.add_argument(
        "--aspect",
        required=True,
        choices=[a.value for a in Aspect],
        help="the kind of problem, as a judgment's aspect",
    )
    find.add_argument(
        "--on",
        action="append",
        default=[],
        metavar="REF",
        help="a block or group anchor (repeat for several)",
    )
    for flag, what in (("--part", "a claim part"), ("--span", "a span of text")):
        find.add_argument(
            flag,
            nargs=2,
            action="append",
            default=[],
            metavar=("BLOCK", "WORDS"),
            help=f"{what} anchor: its block and its words (repeat for several)",
        )
    find.add_argument("--paper", action="store_true", help="the paper as an anchor")
    find.add_argument(
        "--work",
        action="append",
        default=[],
        help="a work's key as an anchor: one of the bibliography, cited or not",
    )
    find.add_argument(
        "--file",
        nargs=2,
        action="append",
        default=[],
        metavar=("KEY", "WORDS"),
        help="a kept file's lines as an anchor: its key and their words, once "
        "(repeat for several)",
    )
    find.add_argument(
        "--severity",
        required=True,
        type=int,
        choices=(1, 2, 3),
        help="1 to 3, 3 the most serious and the only one that counts against "
        "its aspect",
    )
    find.add_argument("--problem", required=True, help="the problem, in words")
    find.add_argument("--fix", help="what would fix it")
    _filer_arguments(find)


def _suggest_arguments(commands: Any) -> None:
    offer = commands.add_parser(
        "suggest",
        help="suggest a work the paper may want to cite at a place: a pointer "
        "offered to the author, never a problem (docs/model.md § 8)",
    )
    kept_arguments(offer)
    offer.add_argument(
        "--on",
        required=True,
        metavar="REF",
        help="the block or group where it would be cited",
    )
    offer.add_argument(
        "--work",
        action="append",
        required=True,
        help="the work suggested, the key of one of the bibliography (cited or "
        "not) or `arxiv:ID`, `doi:DOI` (repeat for several)",
    )
    offer.add_argument(
        "--why", required=True, help="why a reader of the field might expect it there"
    )
    _filer_arguments(offer)


def _answer_suggestion_arguments(commands: Any) -> None:
    decide = commands.add_parser(
        "answer-suggestion",
        help="answer a suggestion: taken (the paper now cites it) or declined; "
        "the latest answer is the one in force",
    )
    directory_argument(decide)
    decide.add_argument(
        "--store",
        type=Path,
        help=STORE_HELP,
    )
    decide.add_argument(
        "suggestion", help="a suggestion's id, or a unique prefix of it"
    )
    decide.add_argument(
        "--as",
        dest="status",
        required=True,
        choices=[s.value for s in SuggestionStatus],
        help="the answer: the work is cited, or it is not",
    )
    decide.add_argument("--note", help="why, in words")
    _filer_arguments(decide)


def _run_arguments(commands: Any) -> None:
    run = commands.add_parser(
        "run",
        help="run a paper's code file, its arguments after `--`, from the paper's "
        "directory with its declared runner, and file the run and its output",
    )
    directory_argument(run)
    run.add_argument(
        "--store",
        type=Path,
        help=STORE_HELP,
    )
    run.add_argument("file", metavar="KEY", help="the code file, by its key")
    run.add_argument(
        "--fresh",
        action="store_true",
        help="run it again, not from the cache: where the output changes, the "
        "judgments that read the earlier output lapse",
    )
    _filer_arguments(run)


def _answer_finding_arguments(commands: Any) -> None:
    answer = commands.add_parser(
        "answer-finding",
        help="answer a finding: fixed, dismissed, duplicate or postponed; a "
        "person's answer closes it, an agent's (--agent, with --note) proposes, "
        "for a review of its kind or a person to settle; or withdraw an answer",
    )
    directory_argument(answer)
    answer.add_argument(
        "--store",
        type=Path,
        help=STORE_HELP,
    )
    answer.add_argument(
        "finding",
        help="a finding's id, or a unique prefix of it; with --withdraw, an "
        "answer's, or a finding's for the answer in force on it",
    )
    answer.add_argument(
        "--as",
        dest="status",
        choices=[s.value for s in FindingStatus],
        help="the answer: fixed, dismissed (no problem), duplicate (with --of), "
        "or postponed",
    )
    answer.add_argument("--note", help="why, in words")
    answer.add_argument(
        "--of",
        metavar="FINDING",
        help="with --as duplicate: the finding it duplicates, its id or a unique "
        "prefix; `open` folds it under that one",
    )
    answer.add_argument(
        "--propose",
        action="store_true",
        help="a person's answer filed as a proposal, with --note: the finding stays "
        "open until the review of its kind settles it, weighing the note, or a "
        "person answers it",
    )
    answer.add_argument(
        "--withdraw",
        action="store_true",
        help="a person's: withdraw the answer named, with --reason; it is no "
        "longer in force, the answer before it is, and the finding is open where "
        "none is left",
    )
    answer.add_argument("--reason", help="with --withdraw: why it is withdrawn")
    _filer_arguments(answer)


def _reaffirm_finding_arguments(commands: Any) -> None:
    refile = commands.add_parser(
        "reaffirm-finding",
        help="file that a lapsed finding's problem still stands in the text as it "
        "reads now: the finding refiled on the state, naming the one it reaffirms",
    )
    kept_arguments(refile)
    refile.add_argument("finding", help="a finding's id, or a unique prefix of it")
    refile.add_argument(
        "--evidence", required=True, help="why the problem still stands"
    )
    refile.add_argument(
        "--words",
        help="the words of its first anchor that quotes the text, as the text "
        "reads now, where the quoted words no longer appear exactly once in "
        "their block",
    )
    _filer_arguments(refile)


def _adopt_arguments(commands: Any) -> None:
    adopt = commands.add_parser(
        "adopt",
        help="take an agent's judgment as your own: its verdict, depth, "
        "evidence and pages, naming the one it adopts",
    )
    kept_arguments(adopt)
    adopt.add_argument("judgment", help="a judgment's id, or a unique prefix of it")
    adopt.add_argument(
        "--person",
        metavar="NAME",
        help="the person adopting it, by name (default: the one unnamed person)",
    )
    adopt.add_argument("--json", action="store_true", help="JSON, not lines")
    adopt.set_defaults(agent=None, session=None)


def _rule_arguments(commands: Any) -> None:
    rule = commands.add_parser(
        "rule",
        help="file whether a referee would want an unsupported claim argued "
        "(a ruling), and why; or withdraw a ruling",
    )
    kept_arguments(rule)
    rule.add_argument("--on", metavar="BLOCK", help="the claim's block")
    rule.add_argument(
        "--in",
        dest="within",
        action="append",
        default=[],
        metavar="NAMES",
        help="a person's ruling on a set: every claim standing unsupported in the "
        "blocks these names select, as `review --scope` reads them: sections (3, "
        "3.2, A1, or words of one title), lines (paper.md:29-193), handles, "
        "comma-separated (repeat); one ruling each, in one filing, each guarded as "
        "a single ruling is",
    )
    rule.add_argument(
        "--role",
        action="append",
        default=[],
        choices=[r.value for r in Role],
        help="a ruling on a set: only the claims whose block carries this role "
        "(repeat: any of them); alone, across the paper",
    )
    rule.add_argument(
        "--dry-run",
        action="store_true",
        help="with --in or --role: show what the ruling on the set would rule, "
        "hold back and refuse, and the gates after; keep nothing",
    )
    rule.add_argument("--claim", help="the claim's words where its block holds several")
    rule.add_argument(
        "--ruling",
        choices=[k.value for k in RulingKind],
        help="whether the paper owes the claim an argument: owed (in doubt, owed), "
        "or not and why (each kind: docs/model.md § 8)",
    )
    rule.add_argument(
        "--severity",
        type=int,
        choices=(1, 2, 3),
        help="an owed ruling's weight, required with it: 3 a result rests on the "
        "claim, 1 a passing sentence (docs/model.md § 8); it sorts, it changes "
        "no gate",
    )
    rule.add_argument(
        "--fix",
        help="an owed ruling's fix, required with it: what would discharge it, "
        "the missing step, the pointer the sentence owes, a softened wording, or "
        "a cut",
    )
    rule.add_argument(
        "--propose",
        action="store_true",
        help="a person's ruling filed as a proposal: it changes no status, and "
        "the next argument review rules the claim again with it in view and "
        "settles it; one claim (--on), never left-unargued",
    )
    rule.add_argument(
        "--withdraw",
        metavar="RULING",
        help="a person's: withdraw a ruling, by its id or a unique prefix of it; "
        "it is no longer in force, and the next review rules on the claim again",
    )
    rule.add_argument(
        "--reason",
        required=True,
        help="why: what makes it known, what it is immediate from, what it "
        "glosses, why the author leaves it unargued, or why it is withdrawn",
    )
    rule.add_argument(
        "--page",
        action="append",
        default=[],
        metavar="KEY:INDEX",
        help="a page of a work of the paper's bibliography that the ruling rests "
        "on (a known result read there), by its key and its position in the PDF "
        "from 1 (repeat); read again and stamped at filing, and a change to its "
        "file asks the claim again; none for a result the field knows without one",
    )
    _filer_arguments(rule)


def _check_contribution_arguments(commands: Any) -> None:
    contribution = commands.add_parser(
        "check-contribution",
        help="file that you checked a claim against the literature, as the "
        "contribution session's checked line does: what you searched and what you "
        "found; a claim ruled positioning is read by it; then what it did to the "
        "gates",
    )
    kept_arguments(contribution)
    contribution.add_argument(
        "--on", metavar="BLOCK", required=True, help="the claim's block"
    )
    contribution.add_argument(
        "--claim", help="the claim's words where its block holds several"
    )
    contribution.add_argument(
        "--searched", required=True, help="what you searched, and where"
    )
    contribution.add_argument(
        "--found", required=True, help="what you found: the works, or nothing close"
    )
    _filer_arguments(contribution)


def _reread_arguments(commands: Any) -> None:
    reread = commands.add_parser(
        "reread",
        help="file that you read the lines an edit marked stale, block by block, "
        "against what changed, and they stand: their marks clear, nothing else "
        "changes and no state is made; then what it did to the gates. With "
        "--ask, an agent reads them",
    )
    kept_arguments(reread)
    reread.add_argument(
        "owner",
        nargs="*",
        help="a block whose lines carry an open stale mark (their owner): a "
        "handle, node id, block id or a handle's prefix (`axiomation open --kind "
        "stale` lists them); with --ask, default: every such block, those a "
        "gate counts first",
    )
    reread.add_argument(
        "--reason",
        help="why its lines stand: what you read, against which edit (needed "
        "without --ask)",
    )
    reread.add_argument(
        "--ask",
        action="store_true",
        help="ask the profile's edges agent to read those blocks' lines against "
        "what changed: each block it says stands is filed as its reread, and the "
        "lines it changes are given as its edit of the edges, a new state",
    )
    reread.add_argument(
        "--dry-run", action="store_true", help=f"with --ask: {DRY_RUN_HELP}"
    )
    prompts_argument(reread, with_ask=True)
    _filer_arguments(reread)
    seating.asking_arguments(reread, "the reread's session", "the reread")


#: Each filing's arguments, by its command, in the order `--help` lists
#: them.
ARGUMENTS: dict[str, Callable[[Any], None]] = {
    "judge": _judge_arguments,
    "reaffirm": _reaffirm_arguments,
    "find": _find_arguments,
    "suggest": _suggest_arguments,
    "answer-suggestion": _answer_suggestion_arguments,
    "run": _run_arguments,
    "answer-finding": _answer_finding_arguments,
    "reaffirm-finding": _reaffirm_finding_arguments,
    "adopt": _adopt_arguments,
    "rule": _rule_arguments,
    "check-contribution": _check_contribution_arguments,
    "reread": _reread_arguments,
}


def _page_argument(command: argparse.ArgumentParser) -> None:
    command.add_argument(
        "--page",
        action="append",
        default=[],
        metavar="KEY:INDEX",
        help="a page read, by its work's key and its position in the PDF from 1 "
        "(repeat): cited, a page of its work; valid and true, of any work of "
        "the paper's bibliography; read again and stamped at filing",
    )


def _filer_arguments(command: argparse.ArgumentParser) -> None:
    command.add_argument(
        "--agent",
        metavar="MODEL",
        help="an agent's filing, by this model (default: a person's)",
    )
    command.add_argument(
        "--agent-session",
        dest="session",
        metavar="ID",
        help="the agent's session id, recorded with its filing",
    )
    command.add_argument(
        "--person",
        metavar="NAME",
        help="file under this person's name; where two people's judgments "
        "differ, the subject stays disputed (default: unnamed)",
    )
    command.add_argument("--json", action="store_true", help="JSON, not lines")
