"""The gate refuses what the runner's check refuses (docs/architecture.md
§ 7): every refusal the runner's check of a review sheet can make, one row
each, with the sheet that shows the gate making it, or why no sheet
reaches it. A refusal site added later without its rows fails the count.

On the review tests' synthetic paper, its fixtures borrowed from the
session tests (`test_review`, `test_review_methods`, `test_contribution`,
`test_literature`). Works' pages come from `test_evidence`'s shelves. No
model is called and nothing is fetched.
"""

from __future__ import annotations

import ast
import inspect
import json
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import pytest

import test_person
import test_review as tr
import test_review_methods as tm
from axiomation import (
    edits,
    evidence,
    judgments,
    review,
    rulings,
    suggestions,
    versions,
)
from axiomation.evidence import Origin, Sources
from axiomation.questions import edges_file, load_template
from axiomation.questions import review_file as rf
from axiomation.records import Aspect, Subject
from test_contribution import FOUND, PROSE
from test_edits import built_state
from test_evidence import PAGE, Shelf, transient
from test_literature import CHECKED as LITERATURE_CHECKED
from test_literature import PRATT, SHARP
from test_support_search import search_all

# --- the cases --------------------------------------------------------------------


@dataclass
class Case:
    """A brief, the lines a full sheet of it takes, and what its check
    reads: the paper's sources, the gate's directory."""

    opened: edits.Kept
    found: review.Brief
    base: tuple[str, ...]
    sources: Sources | None = None
    directory: Path | None = None
    then: tuple[str, ...] = ()

    def sheet(self, *lines: str, drop: str | None = None) -> str:
        """The full sheet with `lines` added, its lines opening `drop` left
        out."""
        # the sheet as shown, which an answer by block is read as
        shown = self.found.spec["reference"]
        base = [x for x in self.base if drop is None or not x.startswith(drop)]
        return shown + "\n".join([*base, *lines, *self.then]) + "\n"

    def gate(self, text: str) -> list[str]:
        return list(rf.checked(self.found.spec, text, self.directory).problems)

    def runner(self, text: str, found: review.Brief | None = None) -> list[str]:
        check = review.checker(
            self.opened, found or self.found, self.sources, edits.agent("m", None)
        )
        return list(check((found or self.found).question, text).problems)


def _opened(stack: ExitStack, store) -> edits.Kept:
    kept = versions.latest(store)
    assert kept is not None
    return stack.enter_context(edits.opened(store, kept))


def _argument(stack, tmp_path, capsys, monkeypatch) -> Case:
    store, _ = built_state(tmp_path, capsys)
    search_all(store)
    opened = _opened(stack, store)
    found = review.brief(opened, tr.ARGUMENT, tr.TEMPLATE, None)
    return Case(opened, found, (tr.VALID, *tr.TRUE_LINES, tr.RULE))


#: A cited work's pages, held as an open copy: 12 reads, 13 is a plate (a
#: copy's: a plate of the version of record is evidence as its image).
ABLE = {12: PAGE, 13: "A plate."}


def _with_pages(case: Case, aspect: Aspect | None = None) -> Case:
    """`case` with able1998's pages laid in for s1 (the theorem's `true`),
    as a brief over a block citing it gives them; with `aspect` cited, s1
    is a citation of able1998 on the theorem's words instead."""
    s1 = case.found.subjects["s1"]
    sheet = replace(s1.sheet, works=("able1998",))
    on = s1.subject
    if aspect is Aspect.CITED:
        sheet = replace(sheet, aspect=Aspect.CITED, cites=("able1998",))
        on = Subject(on.node, None, on.words, "able1998")
    subjects = {**case.found.subjects, "s1": review.ReviewSubject(sheet, on)}
    spec = json.loads(json.dumps(case.found.spec))
    spec["subjects"]["s1"].update(
        works=["able1998"], aspect=sheet.aspect.value, cites=list(sheet.cites)
    )
    spec["pages"] = {"able1998": {"12": "transient", "13": "content"}}
    spec["works"] = sorted({*spec["works"], "able1998"})
    found = replace(case.found, subjects=subjects, spec=spec)
    s1_line = (
        "judge s1 verified informal pages able1998:12 — the page says it"
        if aspect is Aspect.CITED
        else "judge s1 verified informal pages able1998:12 — the proof s3 checks it"
    )
    base = (
        *(line for line in case.base if not line.startswith("judge s1 ")),
        s1_line,
    )
    return replace(
        case,
        found=found,
        base=base,
        sources=Sources([transient(able1998=ABLE)]),
    )


def _pages(stack, tmp_path, capsys, monkeypatch) -> Case:
    return _with_pages(_argument(stack, tmp_path, capsys, monkeypatch))


def _unlaid(stack, tmp_path, capsys, monkeypatch) -> Case:
    """The argument case with able1998 cited and laid in with no page, as a
    brief over a paper citing it gives it where no source reads."""
    case = _argument(stack, tmp_path, capsys, monkeypatch)
    spec = json.loads(json.dumps(case.found.spec))
    spec["works"] = sorted({*spec["works"], "able1998"})
    spec["pages"] = {}
    return replace(case, found=replace(case.found, spec=spec))


def _cited(stack, tmp_path, capsys, monkeypatch) -> Case:
    return _with_pages(_argument(stack, tmp_path, capsys, monkeypatch), Aspect.CITED)


def _methods(stack, tmp_path, capsys, monkeypatch, directory: bool = True) -> Case:
    made = test_person.make_paper

    def with_script(root, *args, **kwargs):
        paper = made(root, *args, **kwargs)
        (root / "checks").mkdir()
        (root / tm.KEY).write_text(tm.SCRIPT, encoding="utf-8")
        (root / tm.CONFIG_NAME).write_text(
            f"[computations.runners]\npy = {tm.shlex.quote(tm.sys.executable)!r}\n",
            encoding="utf-8",
        )
        return paper

    monkeypatch.setattr(test_person, "make_paper", with_script)
    store, args = built_state(tmp_path, capsys, tm.LINES)
    opened = _opened(stack, store)
    found = review.brief(
        opened,
        tm.METHODS,
        tm.TEMPLATE,
        None,
        directory=Path(args[0]) if directory else None,
    )
    base = (
        (tm.VERIFIED,) if directory else ("judge s1 inconclusive informal — not run",)
    )
    return Case(opened, found, base)


def _no_runs(stack, tmp_path, capsys, monkeypatch) -> Case:
    return _methods(stack, tmp_path, capsys, monkeypatch, directory=False)


def _fetching(
    stack, tmp_path, capsys, monkeypatch, session: rf.Session, sources: Sources | None
) -> Case:
    store, _ = built_state(tmp_path, capsys)
    opened = _opened(stack, store)
    paper = Path(opened.store.directory).parent / "paper"
    found = review.brief(
        opened,
        session,
        load_template(review.template_name(session)),
        None,
        directory=paper,
        forager="haiku",
    )

    def given(paper, store):
        if sources is None:
            raise evidence.EvidenceError("no sources here")
        return sources

    monkeypatch.setattr(evidence, "sources_for", given)
    work = tmp_path / "work"
    work.mkdir()
    # it fetches by its tool, into the paper's cache: named, never laid in
    if sources is not None:
        sources.fetch(FOUND)
    checked = (
        CONTRIBUTION_CHECKED
        if session is rf.Session.CONTRIBUTION
        else LITERATURE_CHECKED
    )
    return Case(opened, found, (checked,), sources, work, then=(rf.DONE,))


#: The contribution session's checked line, as the sheet reads it.
CONTRIBUTION_CHECKED = (
    'checked thm:bound "The output is bounded." — bounded output of a gain map '
    "— arxiv:2101.00001 states it"
)


def _fetcher(pages: dict[int, str]) -> Sources:
    shelf = Shelf(Origin.TRANSIENT, {})

    def fetcher(key: str) -> str:
        shelf.texts[key] = dict(pages)
        return ""

    return Sources([shelf], fetcher=fetcher)


def _contribution(stack, tmp_path, capsys, monkeypatch) -> Case:
    sources = _fetcher({1: "Contents. " + PROSE, 2: "Figure 3."})
    return _fetching(
        stack, tmp_path, capsys, monkeypatch, rf.Session.CONTRIBUTION, sources
    )


def _fieldresults(stack, tmp_path, capsys, monkeypatch) -> Case:
    sources = _fetcher({1: "Contents. " + PROSE, 2: "Lemma 2. " + PROSE})
    case = _fetching(
        stack, tmp_path, capsys, monkeypatch, rf.Session.FIELDRESULTS, sources
    )
    # the claim standing unsupported that its check holds once it is done
    return replace(
        case,
        base=(
            *case.base,
            f'checked next-the-bound-is "{SHARP}" — read its block — the '
            "paper's own claim",
        ),
    )


def _unfetched(stack, tmp_path, capsys, monkeypatch) -> Case:
    return _fetching(
        stack, tmp_path, capsys, monkeypatch, rf.Session.CONTRIBUTION, None
    )


def _relatedwork(stack, tmp_path, capsys, monkeypatch) -> Case:
    sources = _fetcher({1: "Contents. " + PROSE, 2: "Lemma 2. " + PROSE})
    return _fetching(
        stack, tmp_path, capsys, monkeypatch, rf.Session.RELATEDWORK, sources
    )


def _lapsed(stack, tmp_path, capsys, monkeypatch) -> Case:
    store, _ = built_state(tmp_path, capsys)
    search_all(store)
    opened = _opened(stack, store)
    found = review.brief(opened, tr.ARGUMENT, tr.TEMPLATE, None)
    tr.run(store, opened, found, tr.full(found, tr.SEVERE))
    stack.close()
    tr.footnote_retracted(store)
    opened = _opened(stack, store)
    found = review.brief(opened, tr.ARGUMENT, tr.TEMPLATE, None)
    judged = tuple(
        f"judge {sid} inconclusive informal — reread after the change"
        for sid in found.subjects
    )
    return Case(opened, found, (*judged, "stands f1 — the footnote is no proof"))


CASES: dict[str, Callable[..., Case]] = {
    "argument": _argument,
    "pages": _pages,
    "unlaid": _unlaid,
    "cited": _cited,
    "methods": _methods,
    "no-runs": _no_runs,
    "contribution": _contribution,
    "fieldresults": _fieldresults,
    "unfetched": _unfetched,
    "relatedwork": _relatedwork,
    "lapsed": _lapsed,
}


# --- the refusals -------------------------------------------------------------------


@dataclass(frozen=True)
class Shown:
    """A sheet that shows a refusal: the case, the lines it adds (the full
    sheet's lines opening `drop` left out), the words the gate's problem
    holds; `off`, the spec without what the gate checks it by, under which
    the runner's check still refuses it, and refuses with `then` where its
    words are not the gate's."""

    case: str
    lines: tuple[str, ...]
    wanted: str
    off: Callable[[dict[str, Any]], dict[str, Any]] | None = None
    then: str | None = None
    drop: str | None = None


@dataclass(frozen=True)
class Refusal:
    """One refusal the runner's check of a review sheet can make: the site
    that makes it (`module.function`), what it refuses, and where the gate
    makes it, shown on a sheet, or why no sheet reaches it."""

    site: str
    refuses: str
    gate: str
    shown: Shown | None = None
    why: str = ""


def _without(key: str) -> Callable[[dict[str, Any]], dict[str, Any]]:
    return lambda spec: {k: v for k, v in spec.items() if k != key}


EDGES_OFF = _without("edges")
PAGES_OFF = _without("pages")
#: The rule replacing an owner's line onto the same target: unchanged lines
#: would else be applied as written, two edges onto one target.
SECOND_USES = (
    "edge by-definition-def:gain-the → uses def:gain [gain, other] ref · "
    '"By Definition~\\ref{def:gain}" — it uses the whole ratio'
)
STORE_TIME = (
    "the store's: a brief asks only an open finding no answer speaks for "
    "(`review.finding_questions`, `judgments.review_settles`), so this holds "
    "only where the store changed since the brief; the gate refuses it as "
    "the brief's store read it (`review.settling`, by the same function)"
)

REFUSALS: tuple[Refusal, ...] = (
    # judgments.read_pages
    Refusal(
        "judgments.read_pages",
        "a cited judgment's page of another work than the cited one",
        "read: the subject's works",
        Shown(
            "cited",
            ("judge s1 verified informal pages baker1984:4 — the page says it",),
            "baker1984 is not the cited work (able1998)",
        ),
    ),
    Refusal(
        "judgments.read_pages",
        "a valid's or true's page of a work the paper cites nowhere",
        "read: the paper's works",
        Shown(
            "argument",
            ("judge s1 verified informal pages carter2001:3 — x",),
            "carter2001 is no work of the paper's bibliography",
        ),
    ),
    Refusal(
        "judgments.read_pages",
        "a page read with no sources",
        "read: the pages laid in, by their statuses",
        Shown(
            "unlaid",
            ("judge s3 verified informal pages able1998:3 — x",),
            "a failed read is never evidence: able1998:3 absent",
        ),
        why="without sources no work is laid in with pages",
    ),
    Refusal(
        "judgments.read_pages",
        "a valid's or true's page that failed to read",
        "read: `_pages_problem`, the pages' statuses",
        Shown(
            "pages",
            ("judge s1 verified informal pages able1998:13 — the proof s3 checks it",),
            "a failed read is never evidence: able1998:13 content",
            PAGES_OFF,
            drop="judge s1 ",
        ),
    ),
    Refusal(
        "judgments.read_pages",
        "a cited verdict on what the work holds with no page read",
        "read: `_pages_problem`, `_recalled`",
        Shown(
            "cited",
            ("judge s1 verified informal pages able1998:13 — the page says it",),
            "a verified cited judgment rests on a page read",
            PAGES_OFF,
            drop="judge s1 ",
        ),
    ),
    # judgments.judgment_record
    Refusal(
        "judgments.judgment_record",
        "a judgment with no evidence",
        "read: the judge line's form",
        Shown("argument", ("judge s4 not-shown informal — ",), "a judge line is"),
    ),
    Refusal(
        "judgments.judgment_record",
        "a run, values or code on a judgment that is not computed",
        "read: the judge line's form",
        Shown(
            "argument",
            ('judge s4 not-shown informal run x — "a" ↔ "b" — y',),
            "a judge line is",
        ),
    ),
    # judgments._ran
    Refusal(
        "judgments._ran",
        "a run of another file than the subject's",
        "read: `_computed_line`",
        Shown(
            "methods",
            (
                'judge s1 verified computational run checks/other.py — "The output '
                'is bounded" ↔ "bounded" — x',
            ),
            "this subject's file is checks/bound.py",
        ),
    ),
    Refusal(
        "judgments._ran",
        "a run of a file changed since the state was kept",
        "run_problems: the blob",
        why="the paper's file changes outside the sheet; `test_review_methods`' "
        "`test_a_script_changed_on_disk_since_the_state_is_refused` shows the gate "
        "refusing it",
    ),
    Refusal(
        "judgments._ran",
        "a run that did not run, under a verdict other than inconclusive",
        "run_problems",
        Shown(
            "methods",
            (
                f"judge s1 not-shown computational run {tm.KEY} fail — it exits "
                "before printing",
            ),
            "a run that did not run is never evidence",
        ),
    ),
    Refusal(
        "judgments._ran",
        "values with no run",
        "read: `_computed_line`",
        Shown(
            "methods",
            ('judge s1 inconclusive informal — "The output is bounded" ↔ "b" — x',),
            "name the run",
        ),
    ),
    Refusal(
        "judgments._ran",
        "a verified computed naming no run",
        "read: `_computed_line`",
        Shown(
            "methods",
            ("judge s1 verified computational — the script prints it",),
            "names the run",
        ),
    ),
    Refusal(
        "judgments._ran",
        "a verified computed naming no value",
        "read: `_computed_line`",
        Shown(
            "methods",
            (f"judge s1 verified computational run {tm.KEY} — prints it",),
            "each value",
        ),
    ),
    Refusal(
        "judgments._ran",
        "a value's text side not in the block",
        "read: `_computed_line`",
        Shown(
            "methods",
            (
                f'judge s1 verified computational run {tm.KEY} — "The bound is '
                'sharp" ↔ "bounded" — x',
            ),
            "not in the block's text",
        ),
    ),
    Refusal(
        "judgments._ran",
        "a value's output side not in what the run printed",
        "run_problems",
        Shown(
            "methods",
            (
                f'judge s1 verified computational run {tm.KEY} — "The output is '
                f'bounded" ↔ "bounded by 2" — code "{tm.COMPUTES}" — it prints it',
            ),
            "not in what",
        ),
    ),
    # judgments._read
    Refusal(
        "judgments._read",
        "a verified computed quoting no line of its script",
        "read: `_computed_line`",
        Shown(
            "methods",
            (
                f'judge s1 verified computational run {tm.KEY} — "The output is '
                'bounded" ↔ "bounded by 1" — the script prints the bound',
            ),
            "quotes the script's line",
        ),
    ),
    Refusal(
        "judgments._read",
        "a quoted line not in the script",
        "read: `_computed_line`",
        Shown(
            "methods",
            (
                f'judge s1 verified computational run {tm.KEY} — "The output is '
                'bounded" ↔ "bounded by 1" — code "print the bound" — it prints it',
            ),
            "is not a line of checks/bound.py",
        ),
    ),
    # judgments.finding_record
    Refusal(
        "judgments.finding_record",
        "a severity other than 1, 2 or 3",
        "read: the find line's form",
        Shown("argument", ('find true thm:bound 4 "The output" — x',), "a finding is"),
    ),
    Refusal(
        "judgments.finding_record",
        "a finding with no anchor",
        "read: the find line's form",
        Shown("argument", ('find true 2 "The output" — x',), "a finding is"),
    ),
    Refusal(
        "judgments.finding_record",
        "a finding naming no problem",
        "read",
        Shown(
            "argument",
            ('find true thm:bound 2 "The output is bounded." —  — prove it',),
            "a finding names its problem",
        ),
    ),
    # judgments.anchored
    Refusal(
        "judgments.anchored",
        "a file anchor on a file the state does not keep",
        "read: `_unknown_anchors`",
        Shown(
            "methods",
            ('find computed file:checks/nope.py 2 "print" — x — y',),
            "no block, group or cited work file:checks/nope.py",
        ),
    ),
    Refusal(
        "judgments.anchored",
        "a file anchor naming no words",
        "read: the find line's form",
        Shown(
            "methods",
            ('find computed file:checks/bound.py 2 "" — x — y',),
            "a finding is",
        ),
    ),
    Refusal(
        "judgments.anchored",
        "a file anchor's words not once in the file",
        "read: `_words_problem`",
        Shown(
            "methods",
            ('find computed file:checks/bound.py 2 "print" — x — y',),
            "found 2 times",
        ),
    ),
    Refusal(
        "judgments.anchored",
        "a work anchor naming no work",
        "read: `_unknown_anchors`",
        Shown(
            "argument",
            ('find true thm:bound work: 2 "The output" — x',),
            "no block, group or cited work work:",
        ),
    ),
    Refusal(
        "judgments.anchored",
        "an anchor naming no block or group of the state",
        "read: `_unknown_anchors`",
        Shown(
            "argument",
            ('find true no-such-block 2 "The output" — x',),
            "no block, group or cited work no-such-block",
        ),
    ),
    Refusal(
        "judgments.anchored",
        "a group anchor on a block",
        "none needed",
        why="`review._anchors` anchors a node as a group exactly where it is one",
    ),
    Refusal(
        "judgments.anchored",
        "a block or span anchor on a group",
        "none needed",
        why="`review._anchors` anchors a node as a group exactly where it is one",
    ),
    Refusal(
        "judgments.anchored",
        "a span naming no words",
        "read: `_words_problem`",
        Shown(
            "argument",
            ('find true thm:bound 2 "⟦+ claim⟧ " — x',),
            "its quoted words are empty",
        ),
    ),
    Refusal(
        "judgments.anchored",
        "a span's words not once in its block",
        "read: `_words_problem`",
        Shown(
            "argument",
            ('find true thm:bound 2 "is unbounded" — x',),
            '"is unbounded" is found 0 times in thm:bound',
        ),
    ),
    # judgments.reaffirmed_finding and what it calls
    Refusal(
        "judgments.reaffirmed_finding",
        "a reaffirmation saying no why",
        "read: the stands line's form",
        Shown("lapsed", ("stands f1 — ",), "a finding is settled by"),
    ),
    Refusal(
        "judgments.reaffirmed_finding",
        "what `reaffirm_refusal` refuses",
        "read: `Settling.refused`",
        why="its rows are `reaffirm_refusal`'s",
    ),
    Refusal(
        "judgments.reaffirm_refusal",
        "a finding that holds at the state",
        "read: `Settling.refused`",
        why="a brief asks a live finding only where it carries a proposal, which "
        "a stands line turns down",
    ),
    Refusal(
        "judgments.reaffirm_refusal",
        "a finding reaffirmed already",
        "read: `Settling.refused`",
        why=STORE_TIME,
    ),
    Refusal(
        "judgments.reaffirm_refusal",
        "a finding answered",
        "read: `Settling.refused`",
        why=STORE_TIME,
    ),
    Refusal(
        "judgments.reaffirm_refusal",
        "a finding whose anchor's node was deleted",
        "read: `Settling.refused`",
        why="`test_a_stands_line_on_a_deleted_anchor_is_refused_by_the_gate` "
        "below shows the gate refusing it",
    ),
    Refusal(
        "judgments.reanchored",
        "an anchor whose words do not stand once at the state",
        "read: `Settling.stands`, `.others`, `.requoted`",
        Shown(
            "lapsed",
            ('stands f1 "never written" — still',),
            "'never written' is found 0 times in by-definition-def:gain-the's text, "
            "not once; quote its words as they read now",
        ),
    ),
    Refusal(
        "judgments.reanchored",
        "words quoted anew for a finding that quotes none",
        "read: `Settling.requoted`",
        why="`test_words_quoted_anew_for_a_finding_that_quotes_none_are_refused` "
        "below shows the gate refusing it",
    ),
    # review.fixed_refusal
    Refusal(
        "review.fixed_refusal",
        "a fixed line on a finding settled already",
        "read: `Settling.fixed`",
        why=STORE_TIME,
    ),
    Refusal(
        "review.fixed_refusal",
        "a fixed line on a finding a person answered meanwhile",
        "read: `Settling.fixed`",
        why=STORE_TIME,
    ),
    # rulings, suggestions
    Refusal(
        "rulings.ruling_record",
        "a ruling with no reason",
        "read: the rule line's form",
        Shown("argument", ("rule s4 owed — ",), "a rule line is"),
    ),
    Refusal(
        "rulings.ruling_record",
        "an owed ruling with no severity",
        "read: the owed rule line's form",
        Shown("argument", ("rule s4 owed — not argued — argue it",), "an owed rule"),
    ),
    Refusal(
        "rulings.ruling_record",
        "an owed ruling with no fix",
        "read: the owed rule line's form",
        Shown("argument", ("rule s4 owed 2 — not argued",), "an owed rule line"),
    ),
    Refusal(
        "rulings.ruling_record",
        "a ruling of another kind with a severity or a fix",
        "read: the rule line's form",
        Shown("argument", ("rule s4 known 2 — a standard fact",), "only an owed"),
    ),
    Refusal(
        "suggestions.suggestion_record",
        "a suggestion not on a block or group first",
        "read",
        Shown(
            "relatedwork",
            (f'suggest work:{PRATT} thm:bound "The output is bounded." — why',),
            "a suggestion sits on the block or group whose words it quotes",
        ),
    ),
    Refusal(
        "suggestions.suggestion_record",
        "a suggestion naming no work",
        "read",
        Shown(
            "relatedwork",
            ('suggest thm:bound "The output is bounded." — why',),
            "names the work it offers",
        ),
    ),
    Refusal(
        "suggestions.suggestion_record",
        "a suggestion saying no why",
        "read: the suggest line's form",
        Shown(
            "relatedwork",
            (f'suggest thm:bound work:{PRATT} "The output is bounded." — ',),
            "a suggestion is",
        ),
    ),
    # review.records, review._found_pages
    Refusal(
        "review.records",
        "a run where the review names no paper directory",
        "read: `NO_RUNS`",
        Shown(
            "no-runs",
            (
                f'judge s1 verified computational run {tm.KEY} — "The output is '
                f'bounded" ↔ "bounded by 1" — code "{tm.COMPUTES}" — it prints it',
            ),
            rf.NO_RUNS,
        ),
    ),
    Refusal(
        "review._found_pages",
        "a fetched work's page with no sources",
        "cached_page_problems: the pages where the session's tool fetched them",
        Shown(
            "unfetched",
            (
                f'find novelty thm:bound work:{FOUND} 2 pages {FOUND}:2 "The output '
                'is bounded." — its Lemma 2 states it — cite it',
            ),
            # the runner's check is the gate's first: it refuses the same
            "the found works' pages cannot be read here",
        ),
    ),
    Refusal(
        "review._found_pages",
        "a fetched work's page that failed to read",
        "cached_page_problems",
        Shown(
            "contribution",
            (
                f'find novelty thm:bound work:{FOUND} 2 pages {FOUND}:2 "The output '
                'is bounded." — its Lemma 2 states it — cite it',
            ),
            f"a failed read is never evidence: {FOUND}:2 content",
        ),
    ),
    # the edge lines
    Refusal(
        "edges_file.applied_fragment",
        "an edge line's owner not on the sheet",
        "read: the owner",
        Shown(
            "argument",
            ('edge nowhere → uses def:gain · "x"',),
            "no block or group nowhere",
        ),
    ),
    Refusal(
        "edges_file.applied_fragment",
        "a line taken off that its owner does not hold",
        "none needed",
        why="the lines taken off are `rf.corrected`'s, read from the sheet itself",
    ),
    Refusal(
        "edges_file.applied_fragment",
        "a line its owner holds already",
        "edge_problems: `edges_applied`",
        Shown(
            "argument",
            (
                f"edge {tr.PROOF} → justifies thm:bound name · "
                '"Hence the output is bounded."',
            ),
            "holds the line `→ justifies thm:bound name",
            EDGES_OFF,
        ),
    ),
    Refusal(
        "review_file.narrowed",
        "a uses line naming no part its owner's line onto the target named",
        "edge_problems: `edges_applied`",
        Shown(
            "argument",
            (
                "edge by-definition-def:gain-the → uses def:gain [other] ref · "
                '"By Definition~\\ref{def:gain}" — it uses another part',
            ),
            "names each part by-definition-def:gain-the's line onto def:gain "
            "named: [gain]",
            EDGES_OFF,
        ),
    ),
    Refusal(
        "review_file.narrowed",
        "a uses line naming parts where its owner's line relies on all the target",
        "edge_problems: `edges_applied`",
        Shown(
            "argument",
            (
                "edge by-definition-def:gain-the → uses small-means-below-one [x] · "
                '"the gain is small" — a part of it',
            ),
            "names no part, so the edge relies on all of small-means-below-one",
            EDGES_OFF,
        ),
    ),
    Refusal(
        "review_file.edges_applied",
        "a uses line that narrows the line it replaces, or a self or about line "
        "onto a standing a person kept",
        "edge_problems: `edges_applied`",
        why="its rows are `review_file.narrowed`'s, and `review_file.stood_argued` "
        "reads the kept mark the person's sheet holds, the same in both",
    ),
    Refusal(
        "review_file.edges_applied",
        "edge lines the sheet cannot take",
        "edge_problems: `edges_applied`",
        why="its rows are `edges_file.applied_fragment`'s",
    ),
    Refusal(
        "edits.corrected_export",
        "the edges check of the agent's edit, under its own author",
        "edge_problems: the same check on the spec of an agent's edit",
        why="the spec's edges are an agent's edit's, its author's model in no "
        "rule; `edges_file.answer_problems`' row shows the gate refusing it",
    ),
    Refusal(
        "edges_file.answer_problems",
        "the edges check of an agent's edit, on the sheet with the lines applied",
        "edge_problems",
        Shown(
            "argument",
            ('edge closing-words-of-the → uses def:gain · "words it does not hold"',),
            rf.EDGE_LINES,
            EDGES_OFF,
        ),
    ),
    Refusal(
        "person.checked",
        "an edit holding a `?` flag or a claim its roles cannot place",
        "none needed",
        why="an edge line is a uses, justifies, self or about line (`rf.EDGE`), "
        "so it adds no flag, the standing a self or about line rewrites is a claim its "
        "block holds already (`rf.stood_argued`), and a kept state's sheet holds "
        "none unplaced",
    ),
    Refusal(
        "computations.run",
        "a run the paper's settings cannot make",
        "run_problems: the same call",
        why="`run_problems` makes the run by `computations.run`, as the check does",
    ),
)

#: Each site's refusals: its raises of an error that refuses a line, or its
#: returns of a reason.
SITES: dict[str, tuple[Callable[..., Any], str]] = {
    "judgments.read_pages": (judgments.read_pages, "raise"),
    "judgments.judgment_record": (judgments.judgment_record, "raise"),
    "judgments._ran": (judgments._ran, "raise"),
    "judgments._read": (judgments._read, "raise"),
    "judgments.finding_record": (judgments.finding_record, "raise"),
    "judgments.anchored": (judgments.anchored, "raise"),
    "judgments.reaffirmed_finding": (judgments.reaffirmed_finding, "raise"),
    "judgments.reaffirm_refusal": (judgments.reaffirm_refusal, "return"),
    "judgments.reanchored": (judgments.reanchored, "raise"),
    "review.fixed_refusal": (review.fixed_refusal, "return"),
    "rulings.ruling_record": (rulings.ruling_record, "raise"),
    "suggestions.suggestion_record": (suggestions.suggestion_record, "raise"),
    "review.records": (review.records, "raise"),
    "review._found_pages": (review._found_pages, "raise"),
    "edges_file.applied_fragment": (edges_file.applied_fragment, "raise"),
    "review_file.edges_applied": (rf.edges_applied, "raise"),
    "edits.corrected_export": (edits.corrected_export, "raise"),
    "review._anchors": (review._anchors, "raise"),
    "review_file.narrowed": (rf.narrowed, "return"),
}
#: The sites whose check is one call shared with the gate: one row each.
SHARED = ("edges_file.answer_problems", "person.checked", "computations.run")


def _count(function: Callable[..., Any], how: str) -> int:
    tree = ast.parse(inspect.getsource(function).lstrip())
    if how == "raise":
        return sum(
            1
            for node in ast.walk(tree)
            if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call)
        )
    return sum(
        1
        for node in ast.walk(tree)
        if isinstance(node, ast.Return)
        and node.value is not None
        and not (isinstance(node.value, ast.Constant) and node.value.value is None)
    )


def test_every_refusal_site_has_its_rows() -> None:
    """A refusal added to a site the runner's check reaches, without its
    row here, fails this count."""
    counted = {site: _count(f, how) for site, (f, how) in SITES.items()}
    rows: dict[str, int] = {}
    for r in REFUSALS:
        rows[r.site] = rows.get(r.site, 0) + 1
    # a site with no refusal yet takes no row, and is counted so its first
    # one fails here
    assert rows == {
        **{site: n for site, n in counted.items() if n},
        **dict.fromkeys(SHARED, 1),
    }
    assert all(r.shown is not None or r.why for r in REFUSALS)


SHOWN = [r for r in REFUSALS if r.shown is not None]


@pytest.fixture
def case(tmp_path, capsys, monkeypatch):
    with ExitStack() as stack:

        def made(name: str) -> Case:
            return CASES[name](stack, tmp_path, capsys, monkeypatch)

        yield made


@pytest.mark.parametrize("name", sorted(CASES))
def test_a_full_sheet_passes_the_gate_and_files(case, name) -> None:
    """Each case's full sheet reads, and the runner's check builds its
    records without a refusal."""
    found = case(name)
    text = found.sheet()
    assert found.gate(text) == []
    assert found.runner(text) == []


@pytest.mark.parametrize(
    "refusal", SHOWN, ids=[f"{r.site}: {r.refuses}" for r in SHOWN]
)
def test_the_gate_refuses_what_the_runner_s_check_refuses(case, refusal) -> None:
    shown = refusal.shown
    assert shown is not None
    found = case(shown.case)
    text = found.sheet(*shown.lines, drop=shown.drop)
    gate = found.gate(text)
    assert any(shown.wanted in p for p in gate), gate
    runner = found.runner(text)
    if found.directory is None:
        # the runner's check is the gate's first: it refuses the same
        assert runner == gate
    else:
        # it reads a fetched work's pages again through the sources
        assert any((shown.then or shown.wanted) in p for p in runner), runner
    if shown.off is not None:
        # with what the gate checks it by left out, the runner's check
        # still refuses it: the refusal is the runner's
        off = replace(found.found, spec=shown.off(found.found.spec))
        alone = found.runner(text, off)
        assert any((shown.then or shown.wanted) in p for p in alone), alone


def test_a_stands_line_on_a_deleted_anchor_is_refused_by_the_gate() -> None:
    held = rf.Settling("F", "S", refused="an anchor of finding F was deleted")
    question = {"finding": "F", "anchors": [], "settling": held.data()}
    for words in (None, "w"):
        assert (
            rf._settle_problem({"texts": {}}, question, rf.Settle.STANDS, words)
            == "an anchor of finding F was deleted"
        )


def test_words_quoted_anew_for_a_finding_that_quotes_none_are_refused() -> None:
    question = {"finding": "F", "anchors": ["paper"]}
    question["settling"] = rf.Settling("F", "S").data()
    assert rf._settle_problem({"texts": {}}, question, rf.Settle.STANDS, None) == ""
    assert rf._settle_problem({"texts": {}}, question, rf.Settle.STANDS, "w") == (
        "finding F quotes no words: there is nothing to quote anew"
    )


def test_a_reading_and_a_ruling_rest_on_any_work_the_paper_cites(case) -> None:
    """A `valid` line on s3, whose J cites nothing, and s4's rule line each
    name a page of able1998: the gate takes them, and the runner's check
    stamps each page and gives each record able1998's file. A rule line's
    page of a work the paper cites nowhere, or one that failed to read, is
    refused by the gate as by the runner's check."""
    found = case("pages")
    # s3 is a `valid`: its J's step, read on a page of a work J does not cite
    s3_line = tr.VALID.replace(
        "judge s3 verified informal — ",
        "judge s3 verified informal pages able1998:12 — ",
    )
    rule = (
        "rule s4 owed 2 pages able1998:12 — Able proves it only for a gain below "
        "one — state the gain's range"
    )
    text = found.sheet(s3_line, drop="judge s3 ").replace(tr.RULE, rule)
    assert found.gate(text) == []
    # the brief's lay-in holds able1998's file, as a brief over a paper
    # citing it does
    assert found.sources is not None
    stamp, _ = found.sources.file_stamp("able1998")
    brief = replace(found.found, files={"able1998": stamp})
    check = review.checker(found.opened, brief, found.sources, edits.agent("m", None))
    made = check(brief.question, text).judgments
    s3 = next(
        j for j in made.judgments if j.subject == found.found.subjects["s3"].subject
    )
    assert [p.name for p in s3.pages] == ["able1998:12"]
    assert [f.key for f in s3.given] == ["able1998"]
    (ruling,) = made.rulings
    assert [p.name for p in ruling.pages] == ["able1998:12"]
    assert [f.key for f in ruling.given] == ["able1998"]

    for line, wanted in (
        (
            "rule s4 known pages carter2001:3 — x",
            "carter2001 is no work of the paper's bibliography",
        ),
        (
            "rule s4 known pages able1998:13 — x",
            "a failed read is never evidence: able1998:13 content",
        ),
    ):
        text = found.sheet(line, drop="rule s4 ")
        gate = found.gate(text)
        assert any(wanted in p for p in gate), gate
        assert found.runner(text) == gate


def test_a_page_of_a_work_not_laid_in_is_read_on_demand(case, monkeypatch) -> None:
    """The bibliography's `carter2001`, which no subject cites, is not laid
    in: a judge line's page of it is read through the paper's sources by
    the gate as by the filing, which stamps it, gives the reading its file
    and files that file as seen; a page of it that does not read is
    refused by both."""
    found = case("pages")
    assert found.sources is not None
    sources = Sources(
        [*found.sources.providers, Shelf(Origin.LIBRARY, {"carter2001": {1: PAGE}})]
    )
    monkeypatch.setattr(evidence, "sources_for", lambda paper, store: sources)
    spec = json.loads(json.dumps(found.found.spec))
    spec["entries"] = ["able1998", "carter2001"]
    spec["reads"] = {"paper": "paper", "store": "store"}
    # the brief's lay-in holds able1998's file, as a brief over a paper
    # citing it does
    stamp, _ = sources.file_stamp("able1998")
    brief = replace(found.found, spec=spec, files={"able1998": stamp})
    found = replace(found, found=brief, sources=sources)

    def s3_reading(page: str) -> str:
        line = tr.VALID.replace(
            "judge s3 verified informal — ",
            f"judge s3 verified informal pages {page} — ",
        )
        return found.sheet(line, drop="judge s3 ")

    text = s3_reading("carter2001:1")
    assert found.gate(text) == []
    check = review.checker(
        found.opened, found.found, found.sources, edits.agent("m", None)
    )
    made = check(found.found.question, text).judgments
    s3 = next(
        j for j in made.judgments if j.subject == found.found.subjects["s3"].subject
    )
    assert [p.name for p in s3.pages] == ["carter2001:1"]
    assert [f.key for f in s3.given] == ["carter2001"]
    assert [f.stamp.key for f in made.files] == ["carter2001"]

    text = s3_reading("carter2001:9")
    gate = found.gate(text)
    assert any("a failed read is never evidence: carter2001:9" in p for p in gate)
    assert found.runner(text) == gate


#: Per session that looks outward, a line naming the bibliography's
#: `carter2001`, which the paper cites nowhere, with a page of it.
_ENTRY_LINES = {
    "contribution": (
        'find novelty thm:bound work:carter2001 2 pages carter2001:{} "The output '
        'is bounded." — its Lemma 2 states it — cite it'
    ),
    "fieldresults": (
        'find literature thm:bound work:carter2001 2 pages carter2001:{} "The '
        'output is bounded." — its Lemma 2 states it — cite it'
    ),
    "relatedwork": (
        'suggest thm:bound work:carter2001 pages carter2001:{} "The output is '
        'bounded." — the standard bound a reader expects here'
    ),
}


@pytest.mark.parametrize("name", sorted(_ENTRY_LINES))
def test_a_work_the_paper_cites_nowhere_is_named_by_its_key(
    case, monkeypatch, name
) -> None:
    """The bibliography's `carter2001`, which the paper cites nowhere, is
    named by a finding or a suggestion of a session that looks outward,
    its page read through the paper's sources by the gate as by the
    filing, which stamps it; a page of it that does not read, and a key
    the bibliography does not hold, are refused by both."""
    found = case(name)
    assert found.sources is not None
    sources = Sources(
        [*found.sources.providers, Shelf(Origin.LIBRARY, {"carter2001": {1: PAGE}})]
    )
    monkeypatch.setattr(evidence, "sources_for", lambda paper, store: sources)
    spec = json.loads(json.dumps(found.found.spec))
    spec["entries"] = ["able1998", "carter2001"]
    spec["reads"] = {"paper": "paper", "store": "store"}
    found = replace(found, found=replace(found.found, spec=spec), sources=sources)
    line = _ENTRY_LINES[name]

    text = found.sheet(line.format(1))
    assert found.gate(text) == []
    check = review.checker(
        found.opened, found.found, found.sources, edits.agent("m", None)
    )
    made = check(found.found.question, text).judgments
    filed = [*made.findings, *made.suggestions]
    assert [[p.name for p in r.pages] for r in filed] == [["carter2001:1"]]

    text = found.sheet(line.format(9))
    gate = found.gate(text)
    assert any("a failed read is never evidence: carter2001:9" in p for p in gate)
    assert found.runner(text) == gate

    text = found.sheet(line.format(1).replace("carter2001", "nosuch2099"))
    gate = found.gate(text)
    assert any("work of the bibliography work:nosuch2099" in p for p in gate), gate
    assert found.runner(text) == gate


@pytest.mark.parametrize(
    "session",
    [rf.Session.CONTRIBUTION, rf.Session.FIELDRESULTS, rf.Session.RELATEDWORK],
)
def test_a_session_that_looks_outward_reads_the_bibliography(
    tmp_path, capsys, session
) -> None:
    """Its tool server serves every entry of the bibliography, one the
    paper cites nowhere (`carter2001`) too; its spec lists them, and its
    brief says how they are read. The findings session reads no works."""
    store, _ = built_state(tmp_path, capsys)
    sources = Sources(
        [Shelf(Origin.LIBRARY, {"able1998": {1: PAGE}, "carter2001": {1: PAGE}})],
        listed=lambda: ("able1998", "carter2001"),
    )
    with ExitStack() as stack:
        opened = _opened(stack, store)
        paper = Path(opened.store.directory).parent / "paper"

        def asked(s: rf.Session) -> review.Brief:
            return review.brief(
                opened,
                s,
                load_template(review.template_name(s)),
                sources,
                directory=paper,
                forager="haiku",
            )

        found = asked(session)
        workspace = found.question.workspace
        assert workspace is not None and workspace.served.reads is not None
        assert "carter2001" in workspace.served.reads.works
        assert found.spec["entries"] == ["able1998", "carter2001"]
        assert "paper" in found.spec["reads"]
        assert "any other entry\nby its key" in workspace.instructions
        # the fieldresults inventory fetches nothing: its queue does
        fetching = "(`fetch`) is not in the search" in workspace.instructions
        assert fetching == (session is not rf.Session.FIELDRESULTS)
        findings = asked(rf.Session.FINDINGS)
        assert "entries" not in findings.spec


def test_a_page_names_its_work_by_the_work_s_anchor(case, monkeypatch) -> None:
    """A page of an entry's other file (`carter2001@x`) goes by the work's
    anchor, `work:carter2001`, read and stamped by gate and filing alike;
    a page of a work the line does not name is refused by both."""
    found = case("contribution")
    assert found.sources is not None
    shelf = {"carter2001": {1: PAGE}, "carter2001@x": {1: PAGE}}
    sources = Sources([*found.sources.providers, Shelf(Origin.LIBRARY, shelf)])
    monkeypatch.setattr(evidence, "sources_for", lambda paper, store: sources)
    spec = json.loads(json.dumps(found.found.spec))
    spec["entries"] = ["able1998", "carter2001"]
    spec["reads"] = {"paper": "paper", "store": "store"}
    found = replace(found, found=replace(found.found, spec=spec), sources=sources)
    line = _ENTRY_LINES["contribution"].replace("carter2001:{}", "{}")

    text = found.sheet(line.format("carter2001@x:1"))
    assert found.gate(text) == []
    check = review.checker(
        found.opened, found.found, found.sources, edits.agent("m", None)
    )
    (filed,) = check(found.found.question, text).judgments.findings
    assert [p.name for p in filed.pages] == ["carter2001@x:1"]

    text = found.sheet(line.format("able1998:1"))
    gate = found.gate(text)
    assert any("(able1998 is not)" in p for p in gate), gate
    assert found.runner(text) == gate
