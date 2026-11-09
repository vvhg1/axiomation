"""A review session on a kept state: the brief, the
sheet's gate, the records it files, a replay, its edge lines, `adopt`, the
pile `open` serves and the report.

On the synthetic paper, built by a fake host and kept as a state (the
edits' tests' `built_state`): the theorem `thm:bound`, its proof (the group
`group:by-definition-def:gain-the`, using `def:gain` and the footnote), an
unsupported claim and a restated one. No model is called.
"""

from __future__ import annotations

import json
import re
import types
from dataclasses import replace
from pathlib import Path

import pytest

import record_file
from axiomation import edits, evidence, judgments, queries, review, rulings, versions
from axiomation.changes import Layer
from axiomation.commands import Exit
from axiomation.evidence import PageStamp, ReadStatus
from axiomation.judgments import Author, JudgmentError
from axiomation.questions import REVIEW_TOOLS, load_template, review_blocks
from axiomation.questions import edge_sheet as es
from axiomation.questions import review_file as rf
from axiomation.records import (
    AGENT,
    PERSON,
    AnswerRecord,
    Aspect,
    BasisKind,
    Depth,
    FindingAnswer,
    FindingStatus,
    JudgmentRecords,
    RecordStore,
    RulingKind,
    Subject,
    Verdict,
)
from axiomation.resolver import Resolver
from axiomation.run_state import RunFile
from axiomation.runner import Runner
from questions.test_edges_file import WRITTEN
from runner.fakes import FakeHost
from test_edits import built_state
from test_queries import appendix_view
from test_support_search import search_all
from test_update import FLAT

ARGUMENT = rf.Session.ARGUMENT
OPEN, ALL = review.Selection
HELD = review.Holds.HELD
TEMPLATE = load_template("review-argument")
PROOF = "group:by-definition-def:gain-the"
PROOF_BLOCK = "by-definition-def:gain-the"
#: The proof's valid, naming each block it uses.
VALID = (
    "judge s3 verified informal — def:gain gives the gain as a ratio and "
    "small-means-below-one makes it below one, so the output is bounded"
)
TRUE_LINES = [
    "judge s1 verified informal — the proof s3 checks it",
    "judge s2 verified informal — the closing line of the proof",
    "judge s4 not-shown informal — nothing in the paper argues sharpness",
    "judge s5 inconclusive informal — restates nothing checkable",
]
#: The rule line s4, the unsupported claim, asks.
RULE = (
    "rule s4 owed 3 — sharpness is the paper's own claim, and no block shows a "
    "case where the bound is attained — give the extremal case, or drop sharp"
)
#: A rule line s4 may give whatever its `true` reads: a gloss.
INTERPRETED = "rule s4 interpretation — a gloss of the bound that nothing rests on"
FINDING = (
    'find true next-the-bound-is 2 "the bound is sharp" — sharpness is claimed '
    "and never shown — prove it or drop it"
)


def opened_brief(store):
    kept = versions.latest(store)
    assert kept is not None
    context = edits.opened(store, kept)
    opened = context.__enter__()
    found = review.brief(opened, ARGUMENT, TEMPLATE, None)
    return context, opened, found


def sheet_with(found: review.Brief, *lines: str) -> str:
    assert found.question.workspace is not None
    return found.spec["reference"] + "\n".join(lines) + "\n"


def full(found: review.Brief, *extra: str) -> str:
    return sheet_with(found, VALID, *TRUE_LINES, RULE, *extra)


@pytest.fixture
def state(tmp_path, capsys):
    store, _ = built_state(tmp_path, capsys)
    search_all(store)
    context, opened, found = opened_brief(store)
    yield store, opened, found
    context.__exit__(None, None, None)


# --- the brief ---------------------------------------------------------------


def test_the_subjects_are_each_claim_s_true_and_each_justifies_edge_s_valid(
    state,
) -> None:
    _, _, found = state
    shown = [(sid, s.aspect.value, s.sheet.line) for sid, s in found.subjects.items()]
    assert shown == [
        ("s1", "true", 'thm:bound · "The output is bounded."'),
        ("s2", "true", 'by-definition-def:gain-the · "Hence the output is bounded."'),
        ("s3", "valid", f'{PROOF} → thm:bound · "The output is bounded."'),
        ("s4", "true", 'next-the-bound-is · "Next, the bound is sharp."'),
        (
            "s5",
            "true",
            'a-closing-paragraph-in · "A closing paragraph in another file."',
        ),
    ]
    # a verified valid names what J uses
    assert found.subjects["s3"].sheet.names == ("def:gain", "small-means-below-one")


def test_the_argument_session_answers_by_block(state) -> None:
    """Its workspace: `paper.md`, the edges sheet made lean with its
    subjects' notes, and `review.md` empty; its own check
    and a gate at the end; its tools and its brief alone, cut to what the
    paper needs (its one unsupported claim takes a rule line)."""
    _, _, found = state
    workspace = found.question.workspace
    assert workspace is not None and workspace.gate is not None
    assert workspace.files[rf.REVIEW_FILE] == ""
    assert not workspace.gate.on_write
    assert workspace.served is not None and workspace.served.reads is None
    # Bash, sandboxed, to redo a computation itself
    assert workspace.tools == ("Bash", *REVIEW_TOOLS) and workspace.brief_alone
    brief = workspace.instructions
    assert "<!--" not in brief and "$" not in brief.replace("$TMPDIR", "")
    assert "`python3 -I`" in brief
    # how to read `paper.md` is the session's own: told, it read no better
    assert "slices" not in brief
    # with no hint laid in, the brief tells of none, nor of a person's
    # argument, which read alone would bar every negative reading
    assert "is a\n  hint" not in brief and "the author's argument" not in brief
    assert "@@ <handle>" in brief and "your `check` tool" in brief
    assert "axiomation" not in brief
    assert "takes a rule line" in brief and "## The cited works" not in brief
    paper = workspace.files[rf.PAPER_FILE]
    assert f"\n{rf.RULE_ASKED}\n" in paper and review_blocks.ASKED not in paper
    placed = [s[0] for s in found.spec[review_blocks.SUBJECTS]]
    assert placed == list(found.subjects)


def by_block(found: review.Brief, *lines: str) -> str:
    """Sheet lines written by block: each judge and rule line under its
    subject's heading, by its claim's handle; the rest under the first."""
    placed = {s[0]: s for s in found.spec[review_blocks.SUBJECTS]}
    blocks: dict[str, list[str]] = {}
    for line in lines:
        kind, sid, rest = line.split(" ", 2)
        if sid not in placed:
            blocks.setdefault(next(iter(blocks), placed["s1"][2]), []).append(line)
            continue
        _, aspect, block, target, claim = placed[sid]
        named = f" [{claim}]" if claim else ""
        if kind == "judge":
            onto = f" {target}" if target else ""
            written = f"{aspect}{onto}{named} {rest}"
        else:
            written = f"{kind}{named} {rest}"
        blocks.setdefault(block, []).append(written)
    return "\n\n".join(f"@@ {h}\n" + "\n".join(ls) for h, ls in blocks.items()) + "\n"


def test_an_answer_by_block_files_what_its_sheet_files(state) -> None:
    """The same answer by block and as the sheet: the same records; the
    store keeps the sheet it was read as; its problems are told by block."""
    _, opened, found = state
    check = review.checker(opened, found, None, edits.agent("m", None))
    lines = (VALID, *TRUE_LINES, RULE, FINDING)
    answer = by_block(found, *lines)

    blocks = check(found.question, answer)
    sheet = check(found.question, full(found, FINDING))

    assert blocks.valid and sheet.valid
    assert blocks.judgments == sheet.judgments
    assert blocks.kept is not None and blocks.kept.startswith(found.spec["reference"])
    again = check(found.question, blocks.kept)
    assert again.valid and again.judgments == sheet.judgments
    unruled = by_block(found, VALID, *TRUE_LINES, FINDING)
    (problem,) = check(found.question, unruled).problems
    assert problem.startswith("no rule line on `@@ next-the-bound-is` true")


def test_a_session_checks_its_own_file_and_the_gate_tells_it_by_block(
    state, tmp_path, monkeypatch, capsys
) -> None:
    """The session's `check` tool, on its gate's spec and its directory,
    says what does not read yet by block, and that the file reads once it
    does; the gate at the end blocks the same. It reads `review.md` as a
    file, never through a link (to a file the sandbox closes)."""
    from axiomation.review_tools import ReviewTools

    _, _, found = state
    workspace = found.question.workspace
    assert workspace is not None and workspace.gate is not None
    spec = tmp_path / "gate.json"
    spec.write_text(workspace.gate.spec, encoding="utf-8")
    work = tmp_path / "work"
    work.mkdir()
    edited = work / rf.REVIEW_FILE
    edited.write_text(by_block(found, VALID), encoding="utf-8")
    capsys.readouterr()
    tools = ReviewTools(json.loads(workspace.gate.spec), work)
    assert [t.name for t in tools.tools] == ["check"]

    said, refused = tools.call("check", {})
    assert not refused
    assert "does not read yet" in said and "no verdict line on `@@ thm:bound`" in said
    assert rf.main([str(spec), str(edited)]) == 0
    blocked = json.loads(capsys.readouterr().out)
    assert blocked["decision"] == "block" and "`@@ thm:bound`" in blocked["reason"]

    edited.write_text(
        by_block(found, VALID, *TRUE_LINES, RULE, FINDING), encoding="utf-8"
    )
    assert tools.call("check", {}) == (
        "review.md reads: every line, every subject",
        False,
    )
    assert tools.call("cited_page", {"key": "x", "page": 1})[1]

    closed = tmp_path / "key.md"
    closed.write_text("a line of a key\n", encoding="utf-8")
    edited.unlink()
    edited.symlink_to(closed)
    said, refused = tools.call("check", {})
    assert refused and "a line of a key" not in said


def test_the_brief_s_one_hop_lines_are_one_hop_s_uses_edges(state) -> None:
    store, opened, found = state
    view = queries.View(store, opened.state)
    at = view.readings.at
    for s in found.subjects.values():
        on = s.subject
        basis = judgments.one_hop(
            at, s.aspect, [on.node] + ([on.target] if on.target else [])
        )
        uses = {
            (e.name, e.edge[1])
            for e in basis
            if e.kind is BasisKind.EDGE and e.edge and e.edge[0] == "uses"
        }
        shown = {
            (view.node(view.resolve(line.split()[1])), line.split(" → uses ")[1])
            for line in s.sheet.hops
        }
        assert {source for source, _ in shown} == {source for source, _ in uses}
        assert len(s.sheet.hops) == len(uses)


def test_no_hint_enters_the_evidence_or_the_key(tmp_path, capsys) -> None:
    """What others read is a hint: a finding filed is shown on its hint
    line in `paper.md`, and changes neither the sheet nor the question's
    key, so the question replays while only its hints change."""
    store, _ = built_state(tmp_path, capsys)
    search_all(store)
    context, _, before = opened_brief(store)
    context.__exit__(None, None, None)
    kept = versions.latest(store)
    at = judgments.seen(store, kept)
    judgments.find(
        store,
        kept,
        Aspect.TRUE,
        [judgments.anchored(at, judgments.AnchorKind.PAPER)],
        2,
        "A HINTED PROBLEM",
    )
    context, _, after = opened_brief(store)
    context.__exit__(None, None, None)
    files = after.question.workspace.files
    assert rf.HINTS_FILE not in files
    paper = files[rf.PAPER_FILE]
    hinted = paper[paper.index(review_blocks.HINTS_UNPLACED) :].split("\n\n⟦")[0]
    assert "↳ filed finding" in hinted and "A HINTED PROBLEM" in hinted
    assert "A HINTED PROBLEM" not in files[rf.REVIEW_FILE]
    assert "A HINTED PROBLEM" not in before.question.workspace.files[rf.PAPER_FILE]
    assert after.question.key == before.question.key
    # the brief tells of hints where one is laid in, whatever the key; of a
    # person's argument only where one is
    told, untold = after.question.workspace, before.question.workspace
    assert "is a\n  hint" in told.instructions
    assert "is a\n  hint" not in untold.instructions
    assert "the author's argument" not in told.instructions + untold.instructions


# --- the gate ----------------------------------------------------------------------


def problems(found: review.Brief, text: str) -> list[str]:
    return list(rf.read(found.spec, text).problems)


def test_a_full_sheet_reads(state) -> None:
    _, _, found = state
    read = rf.read(found.spec, full(found, FINDING))
    assert not read.problems
    assert [j.subject for j in read.judges] == ["s3", "s1", "s2", "s4", "s5"]
    (f,) = read.finds
    assert (f.aspect, f.anchors, f.severity, f.fix) == (
        Aspect.TRUE,
        ("next-the-bound-is",),
        2,
        "prove it or drop it",
    )


@pytest.mark.parametrize(
    ("line", "wanted"),
    [
        ("judge s9 verified informal — x", "no subject s9"),
        ("judge s1 proven informal — x", "verdict is one of"),
        ("judge s1 verified informal x", "a judge line is"),
        (
            "judge s3 verified informal — def:gain bounds it",
            "the evidence names not small-means-below-one",
        ),
        (
            "judge s3 verified informal pages carter2001:3 — x",
            "carter2001 is no work of the paper's bibliography",
        ),
        ('find cited thm:bound 2 "The output" — x', "finds valid, true"),
        (
            'find true no-such-block 2 "The output" — x',
            "no block, group or cited work",
        ),
        (
            'find true thm:bound 2 "is unbounded" — x',
            '"is unbounded" is found 0 times in thm:bound',
        ),
        ("find true thm:bound 2 — x", "a finding is"),
        ('edge nowhere → uses def:gain · "x"', "no block or group nowhere"),
        ("the output is bounded", "not a line this sheet takes"),
        ('find true thm:bound 4 "The output" — x', "a finding is"),
        ("judge s1 verified formal — a proof checker", "depth one of informal"),
        (
            'edge thm:bound → qualifies thm:bound · "The output"',
            "an edge line is",
        ),
        ("judge s1 verified informal — again", "s1: 2 judge lines"),
        ("rule s1 known — standard", "s1 takes no rule line"),
        ("rule s4 obvious — standard", "ruling is one of owed, known"),
        (
            "rule s4 left-unargued — kept short",
            "left-unargued is the author's, never an agent's",
        ),
        ("rule s4 known", "a rule line is"),
        ("rule s4 owed 2 — again — argue it", "s4: 2 rule lines"),
        ("rule s4 owed — no weight — argue it", "an owed rule line is"),
        ("rule s4 owed 4 — too heavy — argue it", "an owed rule line is"),
        ("rule s4 owed 2 — no fix", "an owed rule line is"),
        ("rule s4 known 2 — the triangle inequality", "only an owed ruling"),
        ("rule s4 immediate — by the theorem", "s4 is ruled immediate, but its judge"),
    ],
)
def test_the_gate_refuses_each_bad_line(state, line, wanted) -> None:
    _, _, found = state
    found_problems = problems(found, full(found, line))
    assert any(wanted in p for p in found_problems), found_problems


def test_an_unsupported_claim_s_subject_asks_its_ruling(state) -> None:
    """s4, the claim the paper argues nothing for, is marked on the sheet
    and its header says how to rule; a known ruling stands only beside a
    verified judge line."""
    _, _, found = state
    sheet = found.spec["reference"]
    assert (
        '⟦s4 true⟧ next-the-bound-is · "Next, the bound is sharp."\n' + (rf.RULE_ASKED)
        in sheet
    )
    assert f"  {rf.RULE_FORM}" in sheet
    assert [sid for sid, s in found.subjects.items() if s.sheet.rules] == ["s4"]
    lines = [VALID, *TRUE_LINES, "rule s4 known — a textbook fact"]
    assert any(
        "s4 is ruled known, but its judge line is not verified" in p
        for p in problems(found, sheet_with(found, *lines))
    )
    read = rf.read(found.spec, full(found))
    (rule,) = read.rules
    assert (rule.subject, rule.ruling) == ("s4", rf.RulingKind.OWED)


#: s4's block's self line, its one claim named by none: the block's words
#: read as giving the step, so the claim stands self.
SELF_LINE = 'edge next-the-bound-is → self · "Next," — the block gives the step'


def test_a_self_line_stands_an_unsupported_claim_self(state) -> None:
    """A self line on s4's block stands its claim self, as the agent's edit
    of the state gives it: s4 takes no rule line, and one beside it is
    refused."""
    store, opened, found = state
    assert found.spec["subjects"]["s4"]["standing"] == ["next-the-bound-is"] * 2
    assert "a self line naming it and no rule line" in sheet_with(found)
    lines = [VALID, *TRUE_LINES, SELF_LINE]
    assert rf.checked(found.spec, sheet_with(found, *lines)).problems == ()
    assert any(
        "s4 takes no rule line: your self line stands it self" in p
        for p in problems(found, sheet_with(found, *lines, RULE))
    )
    run(store, opened, found, sheet_with(found, *lines))
    answer = store.authoritative()[found.question.key]
    assert not store.judgment_records().rulings
    gave = review.edges_given(
        opened, [(found, answer)], sheets(store), edits.agent("fake/model", "s")
    )
    assert gave.given is not None and gave.given.state is not None
    export = json.loads(versions.kept_export(store, gave.given.state))
    (sharp,) = [
        s for s in export["standings"] if s["claim"]["text"].startswith("Next,")
    ]
    assert sharp["status"] == "self"


#: s4's block's about line onto the theorem, a claim: a summary's pointer
#: to the result it restates.
ABOUT_LINE = 'edge next-the-bound-is → about thm:bound · "the bound" — it restates it'


def test_an_about_line_onto_a_claim_stands_an_unsupported_claim_restated(
    state,
) -> None:
    """An about line from s4's block onto `thm:bound`, which holds a claim,
    stands s4's claim restated, as the agent's edit of the state gives it:
    s4 takes no rule line, and one beside it is refused. Onto a block
    holding no claim, it stands nothing, and the rule line is still owed."""
    store, opened, found = state
    lines = [VALID, *TRUE_LINES, ABOUT_LINE]
    assert rf.checked(found.spec, sheet_with(found, *lines)).problems == ()
    assert any(
        "s4 takes no rule line: your about line stands it restated" in p
        for p in problems(found, sheet_with(found, *lines, RULE))
    )
    onto_definition = ABOUT_LINE.replace("thm:bound", "def:gain")
    assert "no rule line on s4" in problems(
        found, sheet_with(found, VALID, *TRUE_LINES, onto_definition)
    )
    run(store, opened, found, sheet_with(found, *lines))
    answer = store.authoritative()[found.question.key]
    assert not store.judgment_records().rulings
    gave = review.edges_given(
        opened, [(found, answer)], sheets(store), edits.agent("fake/model", "s")
    )
    assert gave.given is not None and gave.given.state is not None
    export = json.loads(versions.kept_export(store, gave.given.state))
    (sharp,) = [
        s for s in export["standings"] if s["claim"]["text"].startswith("Next,")
    ]
    assert sharp["status"] == "restated"


def test_an_about_line_onto_a_conjecture_stands_nothing_restated(
    tmp_path, capsys
) -> None:
    """The closing paragraph made a conjecture: an about line from s4's
    block onto it would assert what the paper only conjectures, so it
    stands nothing, and s4's rule line is still owed."""
    conjecture = dict(WRITTEN) | {
        "a-closing-paragraph-in": [
            '→ about file:main.tex where-used · "A closing paragraph"',
            '⊢ conjectured · "A closing paragraph in another file."',
        ]
    }
    store, _ = built_state(tmp_path, capsys, conjecture)
    search_all(store)
    context, _, found = opened_brief(store)
    try:
        (sid,) = [sid for sid, s in found.subjects.items() if s.sheet.rules]
        onto = (
            'edge next-the-bound-is → about a-closing-paragraph-in · "the bound" '
            "— it restates it"
        )
        judged = [
            VALID.replace("s3", s)
            if x.aspect is Aspect.VALID
            else f"judge {s} verified informal — the block says so"
            for s, x in found.subjects.items()
        ]
        assert f"no rule line on {sid}" in problems(
            found, sheet_with(found, *judged, onto)
        )
    finally:
        context.__exit__(None, None, None)


def test_the_argument_session_asks_no_positioning_claim_s_true(
    tmp_path, capsys
) -> None:
    """A person rules s4's claim positioning: the contribution session's to
    read, so the argument session's next brief asks neither its `true` nor
    a rule line on it."""
    store, _ = built_state(tmp_path, capsys)
    search_all(store)
    kept = versions.latest(store)
    assert kept is not None
    at = judgments.seen(store, kept)
    view = queries.View(store, kept)
    rulings.rule(
        store,
        kept,
        rulings.subject(at, view.resolve("next-the-bound-is")),
        RulingKind.POSITIONING,
        "that no earlier bound is sharp",
    )
    context, _, found = opened_brief(store)
    try:
        asked = [
            s.subject.words for s in found.subjects.values() if s.aspect is Aspect.TRUE
        ]
        assert "Next, the bound is sharp." not in asked
        assert "The output is bounded." in asked
        assert not any(s.sheet.rules for s in found.subjects.values())
    finally:
        context.__exit__(None, None, None)


def test_a_positioning_claim_is_laid_for_the_contribution_session_until_checked(
    tmp_path, capsys
) -> None:
    """A claim ruled positioning stands under its block in the contribution
    session's `paper.md`, keyed, until a checked line quotes its words
    whole; then no brief lays it again."""
    store, _ = built_state(tmp_path, capsys)
    search_all(store)
    kept = versions.latest(store)
    assert kept is not None
    at = judgments.seen(store, kept)
    view = queries.View(store, kept)
    rulings.rule(
        store,
        kept,
        rulings.subject(at, view.resolve("next-the-bound-is")),
        RulingKind.POSITIONING,
        "that no earlier bound is sharp",
    )
    contribution = load_template("review-contribution")
    laid = '↳ ruled positioning "Next, the bound is sharp."'
    with edits.opened(store, versions.latest(store)) as opened:
        found = review.brief(
            opened, rf.Session.CONTRIBUTION, contribution, None, forager="haiku"
        )
        assert found.question.workspace is not None
        paper = found.question.workspace.files[rf.PAPER_FILE]
        block = paper[paper.index("⟦next-the-bound-is ") :].split("\n\n")[0]
        assert laid in block
        run(
            store,
            opened,
            found,
            "@@ next-the-bound-is\n"
            'checked "Next, the bound is sharp." — sharp bounds — nothing close\n'
            f"{rf.DONE}\n",
        )
    with edits.opened(store, versions.latest(store)) as opened:
        # it read every block: `--all-again` builds the whole text's question
        again = review.brief(
            opened,
            rf.Session.CONTRIBUTION,
            contribution,
            None,
            select=ALL,
            forager="haiku",
        )
        assert again.question.workspace is not None
        assert laid not in again.question.workspace.files[rf.PAPER_FILE]


def test_a_finding_filed_is_a_hint_the_findings_key_leaves_out(
    tmp_path, capsys
) -> None:
    """A finding another reader files shows on the findings session's next
    `paper.md` as a hint, and its question's key is the same: it replays."""
    store, _ = built_state(tmp_path, capsys)
    findings = load_template("review-findings")
    with edits.opened(store, versions.latest(store)) as opened:
        before = review.brief(opened, rf.Session.FINDINGS, findings, None)
    kept = versions.latest(store)
    at = judgments.seen(store, kept)
    judgments.find(
        store,
        kept,
        Aspect.PRESENTATION,
        [judgments.anchored(at, judgments.AnchorKind.PAPER)],
        1,
        "A HINTED PROBLEM",
    )
    with edits.opened(store, versions.latest(store)) as opened:
        after = review.brief(opened, rf.Session.FINDINGS, findings, None)
    assert after.question.workspace is not None
    assert "A HINTED PROBLEM" in after.question.workspace.files[rf.PAPER_FILE]
    assert after.question.key == before.question.key


def test_a_review_s_tools_run_no_script(tmp_path) -> None:
    from axiomation.review_tools import ReviewTools

    with pytest.raises(ValueError, match="runs no script"):
        ReviewTools({"runs": {"paper": "p"}}, tmp_path)


def test_a_self_line_stands_one_of_a_block_s_two_claims_self(tmp_path, capsys) -> None:
    """The sharp block holding two unsupported claims: a self line naming
    one passes the gate's edges check, that claim's subject alone takes no
    rule line, and the state the agent's edit makes stands it self and
    leaves the other unsupported."""
    two = dict(WRITTEN) | {
        "next-the-bound-is": [
            '⊢ unsupported [bound] · "the bound"',
            '⊢ unsupported [sharp] · "is sharp"',
        ]
    }
    store, _ = built_state(tmp_path, capsys, two)
    search_all(store)
    context, opened, found = opened_brief(store)
    try:
        ruled = {
            s.subject.words: sid for sid, s in found.subjects.items() if s.sheet.rules
        }
        assert set(ruled) == {"the bound", "is sharp"}
        # each part's line keeps the words of its sentence before it
        assert found.subjects[ruled["is sharp"]].sheet.line.endswith(
            '"[Next, the bound] is sharp"'
        )
        judged = [
            VALID.replace("s3", sid)
            if s.aspect is Aspect.VALID
            else f"judge {sid} verified informal — the block says so"
            for sid, s in found.subjects.items()
        ]
        rule = f"rule {ruled['the bound']} owed 2 — the paper's own claim — argue it"
        self_line = 'edge next-the-bound-is → self [sharp] · "Next," — the step'
        text = sheet_with(found, *judged, rule, self_line)
        assert rf.checked(found.spec, text).problems == ()
        assert (
            f"no rule line on {ruled['the bound']}"
            in rf.read(found.spec, sheet_with(found, *judged, self_line)).problems
        )
        run(store, opened, found, text)
        answer = store.authoritative()[found.question.key]
        gave = review.edges_given(
            opened, [(found, answer)], sheets(store), edits.agent("fake/model", "s")
        )
        assert gave.given is not None and gave.given.state is not None
        export = json.loads(versions.kept_export(store, gave.given.state))
        stood = {
            s["claim"]["text"]: s["status"]
            for s in export["standings"]
            if s["claim"]["text"] in ("the bound", "is sharp")
        }
        assert stood == {"the bound": "unsupported", "is sharp": "self"}
    finally:
        context.__exit__(None, None, None)


def test_scoped_sessions_giving_one_self_line_stand_the_claim_self_once(
    state,
) -> None:
    """Two scoped sessions, s2's and s4's, each write s4's block's self
    line: given together, the line is applied once, the claim stands self,
    and no session's lines are left out."""
    store, _, whole = state
    context, opened, briefs = scoped_one_each(store, ["s2", "s4"])

    def answer(sheet: str) -> str:
        (sid,) = [s for s in ("s2", "s4") if f"⟦{s} " in sheet]
        judge = next(x for x in TRUE_LINES if x.split()[1] == sid)
        return sheet + f"{judge}\n{SELF_LINE}\n"

    try:
        host = FakeHost(lambda p: "", edits={rf.REVIEW_FILE: answer})
        checks = {
            b.question.key: review.checker(
                opened, b, None, edits.agent(host.model, None)
            )
            for b in briefs
        }
        report = Runner(host, Resolver(store, run=held(store)), 10.0, FLAT).run(
            [b.question for b in briefs],
            lambda question, text: checks[question.key](question, text),
        )
        assert sorted(report.answered) == sorted(b.question.key for b in briefs)
        assert not store.judgment_records().rulings
        authoritative = store.authoritative()
        answered = [(b, authoritative[b.question.key]) for b in briefs]
        gave = review.edges_given(
            opened, answered, sheets(store), edits.agent("fake/model", "s")
        )
        assert gave.left_out == ()
        assert gave.given is not None and gave.given.state is not None
        export = json.loads(versions.kept_export(store, gave.given.state))
        (sharp,) = [
            s for s in export["standings"] if s["claim"]["text"].startswith("Next,")
        ]
        assert sharp["status"] == "self"
        assert len([e for e in export["self"] if "Next," in json.dumps(e)]) == 1
    finally:
        context.__exit__(None, None, None)


def test_a_self_line_rewrites_the_unsupported_standings_it_names() -> None:
    """Of a block's two claims, the self line naming one stands that one
    self, its declaration and note dropped and its handle kept; a line
    naming none stands neither, which claim it means being unknown; a
    standing a person kept is refused."""
    shown = "\n".join(
        [
            "⟦b claim⟧ main.tex:1:1 (prose)",
            "So A holds. B holds.",
            '⊢ unsupported omitted [a] · "So A holds." — the paper leaves it',
            '⊢ unsupported [b] · "B holds."',
            "⟦done b⟧",
        ]
    )

    def applied(line: str, text: str = shown) -> str | None:
        return rf.edges_applied(text, [rf.EdgeLine(1, "b", line)])

    named = applied('→ self [a] · "So"')
    assert named is not None
    assert '⊢ self [a] · "So A holds."' in named
    assert "omitted" not in named and '⊢ unsupported [b] · "B holds."' in named
    assert "⊢ self" not in (applied('→ self · "So"') or "")
    kept = shown.replace(
        "⊢ unsupported omitted", es.KEEP_MARK + "⊢ unsupported omitted"
    )
    with pytest.raises(rf.EdgeLinesRefused, match="a person kept its standing"):
        applied('→ self [a] · "So"', kept)


def test_an_about_line_rewrites_the_unsupported_standing_it_names_restated() -> None:
    """An about line from `b` naming its claim `a` onto `t`'s asserted
    claim stands `a` restated; onto `k`'s conjecture it stands nothing; a
    standing a person kept is refused."""
    shown = "\n".join(
        [
            "⟦t claim⟧ main.tex:1:1 (prose)",
            "T holds.",
            '⊢ justified · "T holds."',
            "⟦done t⟧",
            "⟦k claim⟧ main.tex:2:1 (prose)",
            "K may hold.",
            '⊢ conjectured · "K may hold."',
            "⟦done k⟧",
            "⟦b claim⟧ main.tex:3:1 (prose)",
            "So A holds. B holds.",
            '⊢ unsupported [a] · "So A holds."',
            '⊢ unsupported [b] · "B holds."',
            "⟦done b⟧",
        ]
    )

    def applied(line: str, text: str = shown) -> str | None:
        return rf.edges_applied(text, [rf.EdgeLine(1, "b", line)])

    named = applied('→ about [a] t · "So A holds."')
    assert named is not None
    assert '⊢ restated [a] · "So A holds."' in named
    assert '⊢ unsupported [b] · "B holds."' in named
    assert "⊢ restated" not in (applied('→ about [a] k · "So A holds."') or "")
    kept = shown.replace("⊢ unsupported [a]", es.KEEP_MARK + "⊢ unsupported [a]")
    with pytest.raises(rf.EdgeLinesRefused, match="a person kept its standing"):
        applied('→ about [a] t · "So A holds."', kept)


def test_a_uses_line_puts_the_assumed_flag_on_or_takes_it_off() -> None:
    """The argument session's uses line onto a conjecture, from the
    statement that takes it as a hypothesis, replaces the block's line onto
    it with the flag; a line without the flag takes it off. A flag onto a
    claim the paper asserts is the edges check's to refuse."""
    shown = "\n".join(
        [
            "⟦k claim⟧ main.tex:1:1 (prose)",
            "K may hold.",
            '⊢ conjectured · "K may hold."',
            "⟦done k⟧",
            "⟦x claim⟧ main.tex:2:1 (prose)",
            "Assuming K, X holds.",
            '→ uses k · "Assuming K"',
            '⊢ unsupported · "X holds."',
            "⟦done x⟧",
        ]
    )
    flagged = rf.edges_applied(
        shown, [rf.EdgeLine(1, "x", '→ uses k assumed · "Assuming K"')]
    )
    assert flagged is not None
    assert '→ uses k assumed · "Assuming K"' in flagged
    assert '→ uses k · "Assuming K"' not in flagged
    assert es.check(flagged) == []
    plain = rf.edges_applied(flagged, [rf.EdgeLine(1, "x", '→ uses k · "Assuming K"')])
    assert plain is not None and "assumed" not in plain
    asserted = flagged.replace("⊢ conjectured", "⊢ unsupported")
    assert any(
        "assumed targets `conjectured` claims only" in p for p in es.check(asserted)
    )


def test_a_claim_a_person_ruled_on_asks_no_ruling(tmp_path, capsys) -> None:
    store, _ = built_state(tmp_path, capsys)
    search_all(store)
    kept = versions.latest(store)
    at = judgments.seen(store, kept)
    on = rulings.subject(at, queries.View(store, kept).resolve("next-the-bound-is"))
    rulings.rule(
        store,
        kept,
        on,
        rf.RulingKind.OWED,
        "the paper's own claim",
        severity=2,
        fix="argue it",
    )
    context, _, found = opened_brief(store)
    context.__exit__(None, None, None)
    assert not any(s.sheet.rules for s in found.subjects.values())


def test_the_line_just_below_the_rule_is_read(state) -> None:
    """The first line after `---` is the sheet's like any other: a finding
    there is read, a stray line there refused."""
    _, _, found = state
    text = full(found)
    head, rule, rest = text.partition("\n---\n")
    assert rest.startswith("\n")  # the sheet shows a blank line there
    read = rf.read(found.spec, head + rule + FINDING + rest)
    assert not read.problems and len(read.finds) == 1
    assert any(
        "not a line this sheet takes" in p
        for p in problems(found, head + rule + "a stray line" + rest)
    )


def test_a_quote_across_a_part_marker_is_found(state) -> None:
    """A `⟦+ role⟧` inside the text is the sheet's, not the paper's words."""
    _, _, found = state
    line = f'find valid {PROOF} 1 ". ⟦+ claim⟧ Hence the output" — the step is terse'
    assert not problems(found, full(found, line))


def test_the_fixed_lines_and_the_header_stay(state) -> None:
    _, _, found = state
    text = full(found)
    assert any(
        "subject lines" in p
        for p in problems(found, text.replace("⟦s4 true⟧", "⟦s4 valid⟧"))
    )
    assert any("header" in p for p in problems(found, text.replace("severity 1", "")))


def test_every_subject_takes_a_judge_line_and_a_findings_session_none(state) -> None:
    _, _, found = state
    assert problems(found, sheet_with(found, VALID)) == [
        "no rule line on s4",
        "no judge line on s1, s2, s4, s5",
    ]
    findings_only = {
        **found.spec,
        "column": {"judged": [], "found": ["true"], "edges": False},
    }
    found_problems = list(rf.read(findings_only, full(found)).problems)
    assert any("files findings only, no judge line" in p for p in found_problems)
    assert any("no done mark" in p for p in found_problems)


def test_the_gate_hands_problems_back_at_the_end(state, tmp_path, capsys) -> None:
    _, _, found = state
    spec = tmp_path / "gate.json"
    spec.write_text(json.dumps(found.spec), encoding="utf-8")
    sheet = tmp_path / rf.REVIEW_FILE
    sheet.write_text(sheet_with(found, VALID), encoding="utf-8")

    assert rf.main([str(spec), str(sheet)]) == 0
    decision = json.loads(capsys.readouterr().out)
    assert decision["decision"] == "block"
    assert "no judge line on s1" in decision["reason"]

    sheet.write_text(full(found), encoding="utf-8")
    assert rf.main([str(spec), str(sheet)]) == 0
    assert capsys.readouterr().out == ""


# --- what a session files ---------------------------------------------------


def held(store) -> RunFile:
    """The review run's working state, which holds each sheet filed, as
    `review` keeps it."""
    return RunFile(store.directory, "review", "run", "")


def sheets(store) -> dict[str, str]:
    """The sheets the review run holds, by answer key."""
    return dict(held(store).state.texts)


def run(store, opened, found, answer: str) -> Runner:
    host = FakeHost(lambda p: "", edits={rf.REVIEW_FILE: lambda _: answer})
    # as `review` asks it: its answer names the state its brief read
    runner = Runner(
        host, Resolver(store, run=held(store)), 10.0, FLAT, review_state=opened.state.id
    )
    report = runner.run(
        [found.question],
        review.checker(opened, found, None, edits.agent(host.model, None)),
    )
    assert report.answered == (found.question.key,), report
    return runner


def test_a_fake_host_s_sheet_is_filed_as_the_agent_s_and_read_by_show_and_open(
    state,
) -> None:
    store, opened, found = state
    run(store, opened, found, full(found, FINDING))
    (ruling,) = store.judgment_records().rulings
    assert (ruling.subject.words, ruling.kind) == (
        "Next, the bound is sharp.",
        rf.RulingKind.OWED,
    )
    answer = store.authoritative()[found.question.key]
    assert (answer.kind, answer.stage, answer.host) == (
        "review-argument",
        "review",
        "fake",
    )
    held = store.judgment_records()
    assert len(held.judgments) == 5 and len(held.findings) == 1
    assert {j.answer for j in held.judgments} == {found.question.key}

    view = queries.View(store, opened.state)
    by_aspect = {
        (s.aspect, view.handle(view.readings.block_of(s.subject.node) or "")): s
        for s in view.readings.statuses
        if s.reads
    }
    proof = by_aspect[(Aspect.VALID, PROOF_BLOCK)]
    assert proof.status is judgments.StatusKind.HELD
    assert proof.held.author.name == "fake fake/model"
    # the finding's first anchor is its quoted words, a span
    (f,) = view.readings.findings
    assert [(a.kind.value, a.words) for a in f.finding.anchors] == [
        ("span", "the bound is sharp")
    ]
    opened_report = queries.open_report(view, kinds=("finding",))
    assert [i.data["handle"] for i in opened_report.sections["finding"]] == [
        "next-the-bound-is"
    ]
    shown = queries.render(queries.show(view, "next-the-bound-is"), limit=0)
    assert "sharpness is claimed" in shown


def test_the_findings_session_files_findings_alone_and_ends_with_its_mark(
    state,
) -> None:
    """The findings session has no subject: one input, the paper plain
    (no edge, no standing), and one output, `review.md`, empty, its lines
    by block; its findings on how the paper reads and what a result is
    worth are filed, and block nothing; it ends with its mark."""
    store, opened, _ = state
    found = review.brief(
        opened, rf.Session.FINDINGS, load_template("review-findings"), None
    )
    assert found.subjects == {}
    assert found.question.workspace is not None
    # Bash, sandboxed, to redo a computation a finding rests on
    assert found.question.workspace.tools == ("Bash", *REVIEW_TOOLS)
    assert "`python3 -I`" in found.question.workspace.instructions
    files = found.question.workspace.files
    assert sorted(files) == [rf.PAPER_FILE, rf.REVIEW_FILE]
    assert files[rf.REVIEW_FILE] == ""
    paper = files[rf.PAPER_FILE]
    assert "⟦thm:bound " in paper and "The output is bounded." in paper
    assert not any(line.startswith(("→ ", "⊢ ")) for line in paper.split("\n"))
    trivial = (
        '@@ thm:bound\nfind significance 3 "The output is bounded." — follows '
        "in one line from def:gain — call it a remark"
    )
    assert any(
        "no done mark" in p for p in rf.checked(found.spec, f"{trivial}\n").problems
    )
    # a result reported as computed, with no script its block uses
    unscripted = (
        'find computed 2 "The output is bounded." — reported as a run, and no '
        "script computes it — add the script and its uses edge"
    )
    on_a_script = 'find computed file:x.py 2 "x" — a script line — fix it'
    assert any(
        "file:" in p
        for p in rf.checked(found.spec, f"{on_a_script}\n{rf.DONE}\n").problems
    )
    run(store, opened, found, f"{trivial}\n{unscripted}\n{rf.DONE}\n")
    filed = store.judgment_records().findings
    assert sorted(f.aspect for f in filed) == sorted(
        [Aspect.SIGNIFICANCE, Aspect.COMPUTED]
    )
    view = queries.View(store, opened.state)
    assert not any(r.blocking for r in view.readings.findings)
    # read again (`--all-again`: it read every block), what it filed is shown
    # under its block, never asked again
    again = review.brief(
        opened,
        rf.Session.FINDINGS,
        load_template("review-findings"),
        None,
        "Tier 1 says we prove.",
        ALL,
    )
    assert again.question.workspace is not None
    paper = again.question.workspace.files[rf.PAPER_FILE]
    assert paper.startswith(f"{review.CONVENTIONS_HEAD}\n\nTier 1 says we prove.\n")
    under = paper[paper.index("⟦thm:bound ") :].split("\n\n", 1)[0]
    assert "↳ filed finding" in under and "significance" in under
    assert under.count("↳ filed") == 2


def test_a_bibliography_entry_names_its_work_as_a_citation_does() -> None:
    two = """@article{cd87,
  author = {Carr, Lena Jo and Dunn, Jian Tao},
  title = {The {Absence} of Uniform Bounds},
  year = 1987,
}"""
    many = (
        '@book{hb19, author = "Julia Hale and James Thorn and Jacqueline '
        'Chase", title = {Handbook of Bounds}, date = {2019-09-20}}'
    )
    text = f"{two}\n\n{many}\n"

    assert review.bib_entry(text, "cd87") == two
    assert review.bib_label(two) == (
        "Carr and Dunn (1987). The Absence of Uniform Bounds"
    )
    assert review.bib_label(review.bib_entry(text, "hb19")) == (
        "Hale et al. (2019). Handbook of Bounds"
    )
    assert review.bib_entry(text, "absent") == ""
    assert review.bib_label("@misc{x, note = {n}}") == ""


def test_each_block_carries_the_works_it_cites(tmp_path) -> None:
    """On a paper that cites, the paper plain carries under each block
    the works its citations name, each once."""
    from test_citations import built as citing_paper

    store, state = citing_paper(tmp_path)
    view = queries.View(store, state)
    at = view.readings.at

    under = review.laid_lines(view, rf.Session.FINDINGS, "")

    by_node = {at.handle(n): n for n in ("s", "m")}
    assert under[next(h for h, n in by_node.items() if n == "s")] == [
        "↳ cites able1998"
    ]
    assert under[next(h for h, n in by_node.items() if n == "m")] == [
        "↳ cites able1998",
        "↳ cites baker1984",
    ]


def test_a_replay_files_nothing(state) -> None:
    store, opened, found = state
    run(store, opened, found, full(found))
    count = len(store.judgment_records().judgments)
    host = FakeHost(lambda p: "", edits={rf.REVIEW_FILE: lambda _: full(found)})
    report = Runner(host, Resolver(store, run=held(store)), 10.0, FLAT).run(
        [found.question], review.checker(opened, found, None, edits.agent("m", None))
    )
    assert report.skipped == (found.question.key,) and host.calls == 0
    assert len(store.judgment_records().judgments) == count


def test_a_sheet_that_does_not_read_is_asked_again_and_files_nothing(state) -> None:
    store, opened, found = state
    answers = iter([sheet_with(found, VALID), full(found)])
    host = FakeHost(lambda p: "", edits={rf.REVIEW_FILE: lambda _: next(answers)})
    report = Runner(host, Resolver(store, run=held(store)), 10.0, FLAT).run(
        [found.question], review.checker(opened, found, None, edits.agent("m", None))
    )
    assert report.answered == (found.question.key,) and report.attempts == 2
    first, second = [a for a in store.answers() if a.key == found.question.key]
    assert not first.valid and not first.records
    assert second.valid and len(second.records) == 6


def test_an_edge_line_is_given_as_the_agent_s_edges_edit(state) -> None:
    store, opened, found = state
    line = 'edge closing-words-of-the → uses def:gain · "Closing words of the results."'
    run(store, opened, found, full(found, line))
    answer = store.authoritative()[found.question.key]
    gave = review.edges_given(
        opened, [(found, answer)], sheets(store), edits.agent("fake/model", "review s")
    )
    given = gave.given
    assert given is not None and given.state is not None
    export = json.loads(versions.kept_export(store, given.state))
    added = [
        e
        for e in export["edges"]
        if e["source"].startswith("main.tex:22") and e["kind"] == "uses"
    ]
    assert len(added) == 1 and added[0]["provenance"]["by"] == AGENT


#: A use the theorem's `true` (s1) rests on once the line is given.
OWN_LINE = 'edge thm:bound → uses def:gain · "The output is bounded."'


def statuses_at(store, state) -> dict[str, judgments.StatusKind]:
    view = queries.View(store, state)
    return {
        f"{s.aspect.value} {s.subject.node}": s.status
        for s in view.readings.statuses
        if s.reads
    }


def test_a_session_s_own_edge_lines_lapse_none_of_its_readings(state) -> None:
    """A reader is shown the lines it writes: its readings record the hop
    as its own lines correct it (`docs/model.md` § 8), so the state its
    edges edit makes, which gains a use under s1 and replaces the proof's
    justifies line, lapses none of them."""
    store, opened, found = state
    replaced = (
        f'edge {PROOF} → justifies thm:bound name sketch · "Hence the output is '
        'bounded." — the proof outlines the bound only'
    )
    run(store, opened, found, full(found, OWN_LINE, replaced))
    view = queries.View(store, opened.state)
    bound = view.node(view.resolve("thm:bound"))
    (true,) = [
        j
        for j in store.judgment_records().judgments
        if j.aspect is Aspect.TRUE and j.subject.node == bound
    ]
    assert any(
        e.kind is BasisKind.EDGE and e.edge and e.edge[0] == "uses" for e in true.basis
    )
    answer = store.authoritative()[found.question.key]
    gave = review.edges_given(
        opened, [(found, answer)], sheets(store), edits.agent("fake/model", "s")
    )
    assert gave.given is not None and gave.given.state is not None
    after = statuses_at(store, gave.given.state)
    assert after == statuses_at(store, opened.state)
    assert judgments.StatusKind.LAPSED not in after.values()


def test_the_check_reads_the_sheet_a_queue_s_working_state_shows(state) -> None:
    """Given the edges sheet a queue's working state shows (the state's,
    with a line an earlier task settled), the check makes the records as
    that sheet shows the state: s1's reading rests on the use settled
    before it, though its own sheet writes no edge line."""
    store, opened, found = state
    working = rf.edges_applied(
        edits.shown(opened, Layer.EDGES),
        rf.read(found.spec, full(found, OWN_LINE)).edges,
    )
    assert working is not None
    agent = edits.agent("m", None)
    view = queries.View(store, opened.state)
    bound = view.node(view.resolve("thm:bound"))

    def uses_under_s1(shown: str | None) -> bool:
        made = review.checker(opened, found, None, agent, shown)(
            found.question, full(found)
        )
        assert made.valid, made.problems
        (true,) = [
            j
            for j in made.judgments.judgments
            if j.aspect is Aspect.TRUE and j.subject.node == bound
        ]
        return any(
            e.kind is BasisKind.EDGE and e.edge and e.edge[0] == "uses"
            for e in true.basis
        )

    assert not uses_under_s1(None)
    assert uses_under_s1(working)


def test_a_session_s_lines_that_do_not_land_lapse_its_readings(state) -> None:
    """A reading rests on its own lines: where the state an edit makes
    does not hold them (another session's lines given without them), the
    graph never held its basis, and it lapses there."""
    store, opened, found = state
    run(store, opened, found, full(found, OWN_LINE))
    other = (
        'edge closing-words-of-the → uses def:gain · "Closing words of the results."'
    )
    edited = review.edges_sheet(opened, rf.read(found.spec, full(found, other)))
    assert edited is not None
    given = edits.give(opened, Layer.EDGES, edited, edits.agent("fake/model", "t"))
    assert given.state is not None
    view = queries.View(store, given.state)
    bound = view.node(view.resolve("thm:bound"))
    (s1,) = [
        s
        for s in view.readings.statuses
        if s.aspect is Aspect.TRUE and s.subject.node == bound
    ]
    assert s1.status is judgments.StatusKind.LAPSED
    assert "thm:bound → uses def:gain" in s1.cause.why


def test_an_edge_line_the_edges_check_refuses_is_asked_again(state) -> None:
    store, opened, found = state
    bad = 'edge closing-words-of-the → uses def:gain · "words it does not hold"'
    check = review.checker(opened, found, None, edits.agent("m", None))
    validation = check(found.question, full(found, bad))
    assert not validation.valid
    assert all(p.startswith("edge lines: ") for p in validation.problems)


def test_the_default_asks_what_an_edit_made_since_the_review(state) -> None:
    """After the review, an edit gives a justifies line: its `valid`, which
    no reading holds, is no update's, and the default asks it alone."""
    store, opened, found = state
    run(store, opened, found, full(found))
    line = (
        "edge closing-words-of-the → justifies thm:bound · "
        '"Closing words of the results."'
    )
    edited = review.edges_sheet(opened, rf.read(found.spec, full(found, line)))
    assert edited is not None
    given = edits.give(opened, Layer.EDGES, edited, edits.A_PERSON)
    assert given.state is not None
    again = asked_now(store)
    assert lines_of(again) == [
        'closing-words-of-the → thm:bound · "The output is bounded."'
    ]


def test_the_hints_brief_no_lapsed_reading_whose_subject_ended(state) -> None:
    """An edit moves the theorem's justifies line from the proof onto the
    closing words: the proof's `valid` ends, its reading lapsed, and the
    new line's `valid` is made. The default asks the new one; no hint names
    the ended one's reading, since no session asks it."""
    store, opened, found = state
    run(store, opened, found, full(found))
    assert opened.sheet is not None
    line = '→ justifies thm:bound name · "Hence the output is bounded."'
    closing = "Closing words of the results.\n\n"
    edited = opened.sheet.replace(line + "\n", "").replace(
        closing, closing + '→ justifies thm:bound · "Closing words of the results."\n'
    )
    assert edited.count("→ justifies thm:bound") == 1
    given = edits.give(opened, Layer.EDGES, edited, edits.A_PERSON)
    assert given.state is not None
    (valid,) = [
        j for j in store.judgment_records().judgments if j.aspect is Aspect.VALID
    ]
    again = asked_now(store)
    assert lines_of(again) == [
        'closing-words-of-the → thm:bound · "The output is bounded."'
    ]
    assert again.question.workspace is not None
    paper = again.question.workspace.files[rf.PAPER_FILE]
    assert valid.id not in paper and "↳ read before" not in paper


# --- adopt -------------------------------------------------------------------


def test_a_person_adopts_an_agent_s_judgment(state) -> None:
    store, opened, found = state
    run(store, opened, found, full(found))
    kept = opened.state
    agent_s = next(
        j for j in store.judgment_records().judgments if j.aspect is Aspect.VALID
    )
    filed = judgments.adopt(store, kept, agent_s.id[:16], Author(PERSON, "Ada"))
    (record,) = filed.records.judgments
    assert record.adopts == agent_s.id
    assert (record.verdict, record.depth, record.evidence) == (
        agent_s.verdict,
        agent_s.depth,
        agent_s.evidence,
    )
    status = next(
        s
        for s in judgments.readings(store, kept).statuses
        if s.aspect is Aspect.VALID and s.reads
    )
    assert status.held.author.name == "person Ada" and status.held.judgment.adopts

    with pytest.raises(JudgmentError, match="needs no adopting"):
        judgments.adopt(store, kept, record.id, Author())
    with pytest.raises(JudgmentError, match="a person adopts"):
        judgments.adopt(store, kept, agent_s.id, Author(AGENT, "m", "s"))


def test_adopt_refuses_a_lapsed_judgment(tmp_path) -> None:
    from test_judgments import BLOCKS, TEXT, built, graph, same, updated

    store, first = built(tmp_path)
    at = judgments.seen(store, first)
    agent_s = judgments.judge(
        store,
        first,
        Aspect.VALID,
        judgments.subject(at, Aspect.VALID, "p"),
        Verdict.VERIFIED,
        Depth.INFORMAL,
        "d bounds it",
        Author(AGENT, "m", "s"),
    ).records.judgments[0]
    text = TEXT.replace("By the gain", "By the gain alone")
    blocks = [*BLOCKS[:2], ("p", "By the gain alone, f is bounded."), BLOCKS[3]]
    second = updated(
        store,
        first,
        text,
        graph(text, blocks),
        same("d", "t", "p", "r", but={"p": "changed"}),
    )
    with pytest.raises(JudgmentError, match="lapsed at state"):
        judgments.adopt(store, second, agent_s.id)


# --- one session per subject --------------------------------------------------

#: Each subject's judge line, by its id on the whole sheet.
JUDGE_LINES = {"s3": VALID} | {line.split()[1]: line for line in TRUE_LINES}
JUDGE_LINES["s4"] += "\n" + RULE
#: s4's edge line, given in s2's too; s5's, onto the same target from the
#: same source, conflicts with it.
EDGE = 'edge closing-words-of-the → uses def:gain · "Closing words of the results."'
EDGES = {
    "s2": EDGE,
    "s4": EDGE,
    "s5": 'edge closing-words-of-the → uses def:gain · "Closing words"',
}


def scoped_one_each(store, subjects):
    """A scoped session per subject, each named by its sheet id."""
    kept = versions.latest(store)
    assert kept is not None
    context = edits.opened(store, kept)
    opened = context.__enter__()
    scopes = [[s] for s in subjects]
    return (
        context,
        opened,
        review.scoped_briefs(opened, ARGUMENT, TEMPLATE, None, scopes=scopes).briefs,
    )


def one_subject_answer(sheet: str) -> str:
    """A fake reader's answer on a sheet of one subject: its judge line and
    its edge line (`EDGES`)."""
    (sid,) = [s for s in JUDGE_LINES if f"⟦{s} " in sheet]
    edge = EDGES.get(sid)
    return sheet + JUDGE_LINES[sid] + ("\n" + edge if edge else "") + "\n"


def test_a_scope_holds_the_subjects_named(state) -> None:
    """A scope keeps the subjects named by sheet id or by a block's handle,
    in the sheet's order and each under its whole-paper id; a name naming
    no subject is refused."""
    store, opened, whole = state
    view = queries.View(opened.store, opened.state)
    on_bound = [
        sid
        for sid, s in whole.subjects.items()
        if "thm:bound"
        in {
            view.handle(view.readings.at.ident_of[n])
            for n in (s.subject.node, s.subject.target)
            if n is not None
        }
    ]
    assert "s3" in on_bound
    (named,) = review.scoped_briefs(
        opened, ARGUMENT, TEMPLATE, None, scopes=[["thm:bound", "s1"]]
    ).briefs
    assert list(named.subjects) == [s for s in whole.subjects if s in {"s1", *on_bound}]
    with pytest.raises(review.ReviewError, match="no subject is named s9, nope"):
        review.scoped_briefs(
            opened, ARGUMENT, TEMPLATE, None, scopes=[["s1", "s9", "nope"]]
        )


def test_a_scoped_session_holds_its_scope_s_subjects(state) -> None:
    """A session per scope, its sheet the subjects
    whose claim's block lies in it (a proof's `valid` with its theorem),
    under their whole-paper ids; by section, every subject in exactly one
    session, those before the first heading with the first; the paper
    whole in each, the scope's note after the template."""
    store, opened, whole = state
    view = queries.View(opened.store, opened.state)
    placed = {
        sid: review._claim_block(view, s.subject) for sid, s in whole.subjects.items()
    }
    sections = review.by_section(view, list(whole.subjects.values()))
    assert [n for s in sections for n in (x.sheet.id for x in s[1])] == list(
        whole.subjects
    )
    briefs = review.scoped_briefs(
        opened, ARGUMENT, TEMPLATE, None, sections=True
    ).briefs
    assert [b.scope for b in briefs] == [name for name, *_ in sections]
    assert [list(b.subjects) for b in briefs] == [
        [x.sheet.id for x in found] for _, found, *_ in sections
    ]
    files = whole.question.workspace.files

    def unmarked(paper: str) -> str:
        # a scope's paper marks its own subjects, and shows their notes
        lines = paper.replace(review_blocks.ASKED, "").split("\n")
        return "\n".join(line for line in lines if not line.startswith("  ↳ "))

    for b in briefs:
        workspace = b.question.workspace
        paper = workspace.files[rf.PAPER_FILE]
        assert unmarked(paper) == unmarked(files[rf.PAPER_FILE])
        assert paper.count(review_blocks.ASKED) == len(b.subjects)
        assert workspace.instructions.endswith(review.scope_note(b.scope))
    assert len({b.question.key for b in briefs}) == len(briefs)

    (results,) = review.scoped_briefs(
        opened, ARGUMENT, TEMPLATE, None, scopes=[["Results"]]
    ).briefs
    bound = view.handle(placed["s3"])
    assert bound == "thm:bound" and "s3" in results.subjects
    assert all(view.handle(placed[s]) != "def:gain" for s in results.subjects)
    line = view.blocks[placed["s3"]]["extent"]["start"][0]
    file = view.blocks[placed["s3"]]["extent"]["file"]
    (by_lines,) = review.scoped_briefs(
        opened, ARGUMENT, TEMPLATE, None, scopes=[[f"{file}:{line}-{line}"]]
    ).briefs
    assert "s3" in by_lines.subjects and set(by_lines.subjects) <= set(results.subjects)
    with pytest.raises(review.ReviewError, match="no subject is named §9"):
        review.scoped_briefs(opened, ARGUMENT, TEMPLATE, None, scopes=[["§9"]])


def test_by_section_names_the_first_with_the_opening_it_takes(
    state, monkeypatch
) -> None:
    """A subject before the first heading goes with the first section's
    session, which then says so: a scope of that section's name alone
    would select fewer."""
    _, opened, whole = state
    view = queries.View(opened.store, opened.state)
    sections = review.by_section(view, list(whole.subjects.values()))
    assert [name for name, *_ in sections] == ["§2 Results", "§3 More"]

    bound = review._claim_block(view, whole.subjects["s1"].subject)
    within = view.within
    monkeypatch.setattr(view, "within", lambda b: [] if b == bound else within(b))
    sections = review.by_section(view, list(whole.subjects.values()))
    assert [name for name, *_ in sections] == [
        "the opening and §2 Results",
        "§3 More",
    ]
    assert "s1" in [x.sheet.id for x in sections[0][1]]


def test_a_long_scope_s_dry_run_writes_under_a_short_name(state, tmp_path) -> None:
    """A scope of several long titles passes a file name's 255 bytes: its
    directory is cut short and ended by a hash of the whole scope, so two
    such scopes stay apart, and its files are written."""
    _, opened, _ = state
    (found,) = review.scoped_briefs(
        opened, ARGUMENT, TEMPLATE, None, scopes=[["Results"]]
    ).briefs
    title = "Prelude — On Counting Things, First Rule — The Base Case"
    long, longer = f"{title}, " * 5 + "A1", f"{title}, " * 5 + "A2"
    named = review.scope_directory(long)
    assert len(named.encode()) <= review.SCOPE_DIRECTORY_BYTES
    assert named != review.scope_directory(longer)
    assert named.startswith("Prelude-On-Counting-Things")
    assert review.scope_directory("§2 Results") == "§2-Results"

    review.written(tmp_path, replace(found, scope=long))
    root = tmp_path / ARGUMENT.value / named
    assert (root / "instructions.md").is_file() and (root / "gate.json").is_file()


def test_scoped_sessions_file_each_and_give_the_edges_once(state) -> None:
    store, _, whole = state
    context, opened, briefs = scoped_one_each(store, whole.subjects)
    try:
        host = FakeHost(lambda p: "", edits={rf.REVIEW_FILE: one_subject_answer})
        checks = {
            b.question.key: review.checker(
                opened, b, None, edits.agent(host.model, None)
            )
            for b in briefs
        }
        report = Runner(host, Resolver(store, run=held(store)), 10.0, FLAT, jobs=2).run(
            [b.question for b in briefs],
            lambda question, text: checks[question.key](question, text),
        )
        assert sorted(report.answered) == sorted(b.question.key for b in briefs)
        records = store.judgment_records()
        assert len(records.judgments) == 5
        authoritative = store.authoritative()
        answered = [(b, authoritative[b.question.key]) for b in briefs]
        gave = review.edges_given(
            opened, answered, sheets(store), edits.agent("fake/model", "review s")
        )
        # s5's second edge from one source onto one target is left out
        (why,) = gave.left_out
        assert why.startswith("s5: ") and "a second edge" in why
        given = gave.given
        assert given is not None and given.state is not None
        export = json.loads(versions.kept_export(store, given.state))
        added = [
            e
            for e in export["edges"]
            if e["source"].startswith("main.tex:22") and e["kind"] == "uses"
        ]
        assert len(added) == 1
    finally:
        context.__exit__(None, None, None)


# --- what a session asks ---------------------------------------------------------


def by_hand(store, found: review.Brief, sid: str, verdict: Verdict, model="m-a"):
    """A reading of subject `sid` filed by an agent by hand at the latest
    state: no review's, so no round at the state asks it again."""
    s = found.subjects[sid]
    judgments.judge(
        store,
        versions.latest(store),
        s.aspect,
        s.subject,
        verdict,
        Depth.INFORMAL,
        "read by hand",
        Author(AGENT, model, "s"),
    )


def ruled_after(store, found: review.Brief, sid: str = "s4") -> None:
    """The review's ruling on `sid`'s claim filed again by hand at the
    latest state, owed, after a person's answer to the finding on its
    words: what the re-ask's rule line files, which ends the ruling's
    re-ask."""
    rulings.rule(
        store,
        versions.latest(store),
        found.subjects[sid].subject,
        rulings.RulingKind.OWED,
        "the answer settles the example, not the bound's attainment",
        Author(AGENT, "m-a", "s"),
        severity=2,
        fix="show the bound attained",
    )


def lines_of(found: review.Brief) -> list[str]:
    return [s.sheet.line for s in found.subjects.values()]


def asked_now(store, select=OPEN, **given) -> review.Brief:
    with edits.opened(store, versions.latest(store)) as opened:
        return review.brief(opened, ARGUMENT, TEMPLATE, None, select=select, **given)


def test_the_default_asks_what_a_new_reading_can_settle(state) -> None:
    """A held, a disputed and a plainly inconclusive subject are held back,
    each counted by why; the unjudged ones are asked, and so is the
    unsupported claim's `true` though it holds, since no ruling speaks for
    it and its rule line settles that. `--all-again` asks the sheet of every
    subject, the question asked before any was read."""
    store, _, whole = state
    by_hand(store, whole, "s1", Verdict.VERIFIED)
    by_hand(store, whole, "s2", Verdict.CONTRADICTED)
    by_hand(store, whole, "s2", Verdict.VERIFIED, model="m-b")
    by_hand(store, whole, "s4", Verdict.NOT_SHOWN)
    by_hand(store, whole, "s5", Verdict.INCONCLUSIVE)
    found = asked_now(store)
    every = lines_of(whole)
    assert lines_of(found) == [every[2], every[3]]
    assert list(found.subjects) == ["s1", "s2"]
    assert found.subjects["s2"].sheet.rules
    assert sorted((h.holds.value, h.aspect.value) for h in found.held) == [
        ("disputed", "true"),
        ("held", "true"),
        ("inconclusive", "true"),
    ]
    assert review.held_words(found.held) == (
        "holds back 3 (held 1, disputed 1, inconclusive 1): --all-again asks them"
    )
    again = asked_now(store, ALL)
    assert lines_of(again) == every and again.held == ()
    assert again.question.key == whole.question.key


def test_a_session_whose_subjects_all_hold_says_what_it_holds_back(state) -> None:
    store, _, whole = state
    for sid in whole.subjects:
        by_hand(store, whole, sid, Verdict.VERIFIED)
    # the unsupported claim's `true` holds, but asks the rule line no
    # ruling has answered
    assert lines_of(asked_now(store)) == [lines_of(whole)[3]]
    # a ruling on the unsupported claim settles what its rule line would
    rulings.rule(
        store,
        versions.latest(store),
        whole.subjects["s4"].subject,
        rulings.RulingKind.OWED,
        "the paper's own claim",
        Author(AGENT, "m-a", "s"),
        severity=2,
        fix="argue it",
    )
    with pytest.raises(review.NoSubject, match=r"holds back 5 \(held 5\): --all-again"):
        asked_now(store)


def test_a_person_s_dismissal_asks_the_reading_filed_with_it_once(state) -> None:
    """The review files the sharp claim's `true` not-shown and a finding on
    it in one answer; every subject then holds. A person dismisses the
    finding: the reading still holds, and the default asks
    it again, its hints carrying the finding, the answer and its note. A
    reading filed after the dismissal settles it, whatever it reads."""
    store, opened, found = state
    run(store, opened, found, full(found, FINDING))
    with pytest.raises(review.NoSubject):
        asked_now(store)
    (finding,) = store.judgment_records().findings
    judgments.answer_finding(
        store, finding.id, FindingStatus.DISMISSED, "the example shows it sharp"
    )
    sharp = [lines_of(found)[3]]
    again = asked_now(store)
    assert lines_of(again) == sharp
    assert again.question.workspace is not None
    paper = again.question.workspace.files[rf.PAPER_FILE]
    (section,) = re.findall(r"↳ dismissed: .*?(?=\n\n|\n⟦|$)", paper, re.S)
    assert f"finding {finding.id}" in section and "not-shown" in section
    assert "dismissed by person: the example shows it sharp" in section
    brief = again.question.workspace.instructions
    assert "is a\n  hint" in brief and "the author's argument" in brief
    view = queries.View(store, versions.latest(store))
    (status,) = [
        s
        for s in view.readings.statuses
        if s.subject.words == "Next, the bound is sharp."
    ]
    assert status.held is not None
    assert status.held.judgment.verdict is Verdict.NOT_SHOWN
    by_hand(store, found, "s4", Verdict.NOT_SHOWN)
    # the review's owed ruling on the same words stands against the
    # dismissal: the claim is asked again for its rule line, the answer
    # shown under it, until a ruling is filed after the answer
    again = asked_now(store)
    assert lines_of(again) == sharp
    assert again.question.workspace is not None
    sheet = again.spec["reference"]
    assert f"↳ finding {finding.id[:12]} on these words, answered dismissed by " in (
        sheet
    )
    rulings.rule(
        store,
        versions.latest(store),
        found.subjects["s4"].subject,
        rulings.RulingKind.OWED,
        f"the example settles {finding.id[:12]}'s case, not the bound's attainment",
        Author(AGENT, "m-a", "s"),
        severity=2,
        fix="show the bound attained",
    )
    with pytest.raises(review.NoSubject):
        asked_now(store)


#: Before the ruling question's version 2 took effect.
BEFORE = "2026-10-21T19:50:00+00:00"


def run_before(store, opened, found, answer: str) -> None:
    """The review's answer as one filed before its template recorded what
    it asks: under the ruling question's version 1."""
    template = replace(found.question.template, asks=())
    question = replace(found.question, template=template)
    host = FakeHost(lambda p: "", edits={rf.REVIEW_FILE: lambda _: answer})
    runner = Runner(host, Resolver(store, clock=lambda: BEFORE), 10.0, FLAT)
    report = runner.run(
        [question],
        review.checker(
            opened,
            replace(found, question=question),
            None,
            edits.agent(host.model, None),
        ),
    )
    assert report.answered == (question.key,), report


def test_a_review_records_what_its_brief_asks(state) -> None:
    store, opened, found = state
    assert found.question.template.asks == (("ruling", 4),)
    run(store, opened, found, full(found))
    answer = RecordStore(store.directory).authoritative()[found.question.key]
    assert answer.template.asks == (("ruling", 4),)


def test_a_ruling_filed_under_an_older_question_is_asked_again_once(state) -> None:
    """The review ruled the sharp claim owed under the ruling question's
    version 1, and every subject holds. The default asks the
    sharp claim's `true` again with its rule line, the sheet showing the
    ruling asked again under its subject as a candidate to test, which the
    hints leave out. A review under the current question rules it again:
    nothing is asked."""
    store, opened, found = state
    run_before(store, opened, found, full(found))
    sharp = [lines_of(found)[3]]
    again = asked_now(store)
    assert lines_of(again) == sharp and again.subjects["s1"].sheet.rules
    candidate = again.subjects["s1"].sheet.candidate
    assert candidate is not None and candidate.kind is RulingKind.OWED
    assert candidate.line() in again.subjects["s1"].sheet.lines()
    assert again.question.workspace is not None
    paper = again.question.workspace.files[rf.PAPER_FILE]
    assert not [
        h for h in paper.splitlines() if h.startswith("↳ ruled") and "owed by" in h
    ]
    context, reopened, again = opened_brief(store)
    try:
        run(
            store,
            reopened,
            again,
            sheet_with(
                again, TRUE_LINES[2].replace("s4", "s1"), RULE.replace("s4", "s1")
            ),
        )
    finally:
        context.__exit__(None, None, None)
    with pytest.raises(review.NoSubject):
        asked_now(store)


def test_an_agent_s_positioning_under_an_older_question_is_ruled_again(
    state,
) -> None:
    """The review ruled the sharp claim `positioning` under the ruling
    question's version 1: the contribution session reads such a claim, but
    the argument session asks it again, to rule it under its brief."""
    store, opened, found = state
    positioning = "rule s4 positioning — a claim that no other work has the bound"
    run_before(store, opened, found, sheet_with(found, VALID, *TRUE_LINES, positioning))
    view = queries.View(store, versions.latest(store))
    assert [on.words for on in view.positioned] == ["Next, the bound is sharp."]
    again = asked_now(store)
    assert lines_of(again) == [lines_of(found)[3]] and again.subjects["s1"].sheet.rules


def test_a_round_is_named_by_the_versions_its_brief_asks(monkeypatch) -> None:
    """A version makes no state: the same command at the same state after
    one is another round, whose answers are not replayed; a session that
    asks no such question keeps its round."""
    argument = review.review_round("s", ARGUMENT, TEMPLATE, OPEN)
    citation = review.review_round("s", rf.Session.CITATIONCHECK, TEMPLATE, OPEN)
    monkeypatch.setitem(rf.QUESTION_VERSIONS, "ruling", 5)
    assert review.review_round("s", ARGUMENT, TEMPLATE, OPEN) != argument
    assert (
        review.review_round("s", rf.Session.CITATIONCHECK, TEMPLATE, OPEN) == citation
    )


def sharp_status(store) -> judgments.SubjectStatus:
    view = queries.View(store, versions.latest(store))
    (status,) = [
        s
        for s in view.readings.statuses
        if s.subject.words == "Next, the bound is sharp."
    ]
    return status


def test_a_dismissal_s_re_ask_that_concludes_supersedes_the_negative(state) -> None:
    """Another agent reads the sharp claim again after the person's
    dismissal and holds it verified: the review's not-shown, filed with the
    finding dismissed, speaks no more, so the subject is held, not
    disputed, and the dismissal asks nothing again."""
    store, opened, found = state
    run(store, opened, found, full(found, FINDING))
    (finding,) = store.judgment_records().findings
    judgments.answer_finding(
        store, finding.id, FindingStatus.DISMISSED, "the example shows it sharp"
    )
    by_hand(store, found, "s4", Verdict.VERIFIED, model="m-b")
    status = sharp_status(store)
    assert status.status is judgments.StatusKind.HELD
    assert [(r.author.name, r.judgment.verdict) for r in status.reads] == [
        ("agent m-b", Verdict.VERIFIED)
    ]
    assert status.dismissed is None
    assert judgments.lapsed_negative(status) is None


def test_a_superseded_negative_brings_back_no_earlier_reading_of_its_author(
    state,
) -> None:
    """The review's reader held the sharp claim verified under an older
    host before the review's not-shown: once the re-ask supersedes the
    not-shown, the older verified does not speak again."""
    store, opened, found = state
    s4 = found.subjects["s4"]
    judgments.judge(
        store,
        versions.latest(store),
        s4.aspect,
        s4.subject,
        Verdict.VERIFIED,
        Depth.INFORMAL,
        "an older reading",
        Author("fake 0.9", "fake/model", "s"),
    )
    run(store, opened, found, full(found, FINDING))
    (finding,) = store.judgment_records().findings
    judgments.answer_finding(
        store, finding.id, FindingStatus.DISMISSED, "the example shows it sharp"
    )
    by_hand(store, found, "s4", Verdict.VERIFIED, model="m-b")
    assert [r.author.name for r in sharp_status(store).reads] == ["agent m-b"]


def test_a_dismissal_s_re_ask_inconclusive_leaves_the_negative_standing(
    state,
) -> None:
    """The re-ask could not tell: the review's not-shown still holds,
    though the same reader, its host upgraded, filed the re-ask, and the
    dismissal, asked once, asks nothing again."""
    store, opened, found = state
    run(store, opened, found, full(found, FINDING))
    (finding,) = store.judgment_records().findings
    judgments.answer_finding(
        store, finding.id, FindingStatus.DISMISSED, "the example shows it sharp"
    )
    s4 = found.subjects["s4"]
    judgments.judge(
        store,
        versions.latest(store),
        s4.aspect,
        s4.subject,
        Verdict.INCONCLUSIVE,
        Depth.INFORMAL,
        "the note does not settle it",
        Author("fake 2.0", "fake/model", "s"),
    )
    status = sharp_status(store)
    assert status.held is not None
    assert status.held.judgment.verdict is Verdict.NOT_SHOWN
    assert status.held.author.name == "fake fake/model"
    # the re-ask is shown beside the reading it leaves standing
    assert [r.author.name for r in status.reads] == [
        "fake fake/model",
        "fake 2.0 fake/model",
    ]
    # the review's owed ruling on the words, ruled again after the dismissal
    ruled_after(store, found)
    with pytest.raises(review.NoSubject):
        asked_now(store)


def test_a_dismissal_beside_a_person_s_own_negative_asks_nothing(state) -> None:
    """The person read the claim not-shown themselves before dismissing
    the finding: their reading decides it, and no review asks it again."""
    store, opened, found = state
    run(store, opened, found, full(found, FINDING))
    s = found.subjects["s4"]
    judgments.judge(
        store,
        versions.latest(store),
        s.aspect,
        s.subject,
        Verdict.NOT_SHOWN,
        Depth.INFORMAL,
        "I read it: nothing argues it",
    )
    (finding,) = store.judgment_records().findings
    judgments.answer_finding(
        store, finding.id, FindingStatus.DISMISSED, "the example shows it sharp"
    )
    # the reading asks nothing; the review's owed ruling on the same words
    # is asked again until a ruling follows the dismissal
    assert lines_of(asked_now(store)) == [lines_of(found)[3]]
    ruled_after(store, found)
    with pytest.raises(review.NoSubject):
        asked_now(store)


def test_an_agent_s_dismissal_asks_nothing_again(state) -> None:
    """An agent's answer is a proposal: the review settles the finding, and
    the reading stays held back."""
    store, opened, found = state
    run(store, opened, found, full(found, FINDING))
    (finding,) = store.judgment_records().findings
    judgments.answer_finding(
        store,
        finding.id,
        FindingStatus.DISMISSED,
        "the example shows it sharp",
        Author(AGENT, "m-a", "s"),
    )
    again = asked_now(store)
    assert lines_of(again) == [] and len(again.questions) == 1


def whole_sheet_answer(sheet: str) -> str:
    """A fake reader's answer on any sheet of the synthetic paper's
    subjects under their whole-paper ids: each one's judge line, and s2's
    edge line (`EDGES`)."""
    asked = [sid for sid in JUDGE_LINES if f"⟦{sid} " in sheet]
    lines = [JUDGE_LINES[sid] for sid in asked]
    lines += [EDGES["s2"]] if "s2" in asked else []
    return sheet + "\n".join(lines) + "\n"


def review_cli(monkeypatch, store, args, answer, *flags) -> tuple[int, FakeHost]:
    """`review` from the command line on the argument session, the host a
    fake one answering each sheet with `answer`."""
    from axiomation import cli
    from axiomation.commands import seating

    host = FakeHost(lambda p: "", edits={rf.REVIEW_FILE: answer})
    monkeypatch.setattr(seating, "host_of", lambda agent, executable, tools=None: host)
    where = [args[0], "--store", str(store.directory), "--session", "argument"]
    return cli.main(["review", *where, "--budget", "5", *flags]), host


def read_by_line(sheet: str) -> str:
    """A fake reader's answer on any argument sheet of the synthetic paper,
    by each subject's line, whatever its id: the sharp claim not-shown with
    its finding the first time it is asked (its rule line where asked),
    the rest verified."""
    lines = []
    for sid, aspect, line in re.findall(r"^⟦(s\d+) (\w+)⟧ (.*)$", sheet, re.M):
        if aspect == "valid":
            lines.append(VALID.replace("s3", sid, 1))
        elif "Next, the bound is sharp." in line:
            lines.append(f"judge {sid} not-shown informal — nothing argues it")
            if rf.RULE_ASKED in sheet:
                rule = RULE.replace("s4", sid, 1)
                # an owed ruling beside a settled finding on its words names it
                for name in re.findall(r"↳ finding (\S+) on these words", sheet):
                    rule = rule.replace(
                        " — give", f"; {name}'s answer leaves attainment open — give"
                    )
                lines += [rule, FINDING]
        else:
            lines.append(f"judge {sid} verified informal — read in place")
    return sheet + "\n".join(lines) + "\n"


#: The proof's blocks as one run, the group a person dissolved.
GROUP_LINE = (
    "group proof by-definition-def:gain-the next-the-bound-is — the proof "
    "environment's blocks argue the bound as one run"
)


@pytest.mark.parametrize(
    ("line", "wanted"),
    [
        ("group proof thm:bound", "a group line is `group <kind>"),
        ("group lemma thm:bound next-the-bound-is — x", "a group's kind is one of"),
        ("group proof nowhere next-the-bound-is — x", "no block nowhere"),
        (
            "group proof next-the-bound-is thm:bound — x",
            "next-the-bound-is must come before thm:bound",
        ),
    ],
)
def test_the_gate_refuses_a_bad_group_line(state, line, wanted) -> None:
    _, _, found = state
    assert any(wanted in p for p in problems(found, full(found, line)))


def test_a_group_line_the_state_holds_already_gives_nothing(state) -> None:
    """The run is the proof's group already: the line passes, and makes no
    groups edit, so a replay changes nothing."""
    store, opened, found = state
    check = review.checker(opened, found, None, edits.agent("m", None))
    assert check(found.question, full(found, GROUP_LINE)).valid
    read = rf.read(found.spec, full(found, GROUP_LINE))
    assert review.groups_sheet(opened, read) is None


def test_a_group_line_over_a_run_a_group_holds_whole_nests_it(state) -> None:
    """A run from the theorem to the proof's last block holds the proof's
    group whole: the new group's lines stand outside the proof's, so the
    groups check takes the edit."""
    store, opened, found = state
    line = (
        "group derivation thm:bound next-the-bound-is — the theorem and its proof "
        "read as one argument"
    )
    edited = review.groups_sheet(opened, rf.read(found.spec, full(found, line)))
    assert edited is not None
    start = edited.index("⟦group derivation review_1⟧")
    assert start < edited.index("⟦group proof ") < edited.index("⟦end group by_")
    assert edited.index("⟦end group by_") < edited.index("⟦end group review_1⟧")
    assert review._group_problems(opened, edited, edits.agent("m", None)) == ()
    # the run starts where the proof's group starts: it holds it whole all
    # the same
    line = (
        "group derivation by-definition-def:gain-the closing-words-of-the — the "
        "proof and its closing words read as one"
    )
    edited = review.groups_sheet(opened, rf.read(found.spec, full(found, line)))
    assert edited is not None
    assert edited.index("⟦group derivation review_1⟧") < edited.index("⟦group proof ")
    assert edited.index("⟦end group by_") < edited.index("⟦end group review_1⟧")


def test_a_group_line_is_given_as_the_agent_s_groups_edit(tmp_path, capsys) -> None:
    """An agent dissolved the proof's group, leaving its justifies line on
    its first block. The review's group line makes it again, given as the
    agent's groups edit: its re-ask gives the group the line onto the
    theorem and drops the member's, which the gate refuses beside it. Had
    a person dissolved it, the runner's check refuses the line: an agent
    may not change what a person pinned."""
    from axiomation.changes import Layer
    from questions.test_edges_file import done
    from test_edits import PROOF_LINE, asking, given_on, justifying, person_host

    start = "⟦group proof by_definition_the_gain⟧\n\n"
    end = "\n\n⟦end group by_definition_the_gain⟧"

    def dissolve(text: str) -> str:
        return text.replace(start, "").replace(end, "")

    pinned, _ = built_state(tmp_path / "pinned", capsys)
    assert given_on(pinned, Layer.GROUPS, dissolve, asking(pinned)).state is not None
    with edits.opened(pinned, versions.latest(pinned)) as kept:
        found = review.brief(kept, ARGUMENT, TEMPLATE, None)
        sheet = found.spec["reference"]
        check = review.checker(kept, found, None, edits.agent("m", None))
        (refused,) = check(found.question, read_by_line(sheet) + GROUP_LINE).problems
        assert refused.startswith(f"{rf.GROUP_LINES}an agent may not change")

    store, _ = built_state(tmp_path / "agent", capsys)
    search_all(store)
    dissolved = given_on(
        store, Layer.GROUPS, dissolve, asking(store), edits.agent("m", "s")
    )
    assert dissolved.state is not None

    def reask(text: str) -> str:
        closing = re.search(r"⟦end group (\S+)⟧\n", text)
        assert closing is not None
        text = text[: closing.end()] + f"\n{PROOF_LINE}\n" + text[closing.end() :]
        return done(text.replace(PROOF_LINE + "\n", "", 1))

    with edits.opened(store, versions.latest(store)) as kept:
        found = review.brief(kept, ARGUMENT, TEMPLATE, None)
        sheet = found.spec["reference"]
        run(store, kept, found, read_by_line(sheet) + GROUP_LINE + "\n")
        answer = store.authoritative()[found.question.key]
        # with no budget the lines are not given: a pending edit would hold
        # the version, so the command that gives them is printed
        from axiomation.commands import Exit, Paper, Say
        from axiomation.commands import review as reviewing

        capsys.readouterr()
        asked = reviewing.Review(paper=Paper(Path("P"), store.directory))
        assert reviewing.group_lines_given(
            store, [(found, answer)], sheets(store), None, asked, None, "S1", Say()
        ) == (Exit.UNANSWERED, 0.0)
        assert "review P --state S1 --session argument --budget" in (
            capsys.readouterr().out
        )
        assert not versions.pending_answers(store, kept.state.version)
        runner = Runner(
            person_host(reask=reask), Resolver(store, run=held(store)), 10.0, FLAT
        )
        given = review.groups_given(
            kept,
            [(found, answer)],
            sheets(store),
            edits.agent("fake/model", "s"),
            runner,
        ).given
    assert given is not None and given.state is not None, given
    assert justifying(store, given.state) == ["group:by-definition-def:gain-the-2"]


def test_review_gives_its_group_lines_and_the_same_command_resumes_them(
    tmp_path, capsys, monkeypatch
) -> None:
    """`review` files the sheet, then gives its group line as the agent's
    groups edit; a host that leaves the edges re-ask unanswered leaves the
    edit waiting, and the same command, its host answering the re-ask,
    makes the state."""
    from axiomation import cli
    from axiomation.changes import Layer
    from axiomation.commands import seating
    from axiomation.questions.edges_file import EDGES_FILE
    from questions.test_edges_file import done
    from test_edits import PROOF_LINE, asking, given_on, justifying

    store, args = built_state(tmp_path, capsys)
    search_all(store)
    start = "⟦group proof by_definition_the_gain⟧\n\n"
    end = "\n\n⟦end group by_definition_the_gain⟧"
    given_on(
        store,
        Layer.GROUPS,
        lambda t: t.replace(start, "").replace(end, ""),
        asking(store),
        edits.agent("m", "s"),
    )

    def answer(sheet: str) -> str:
        return read_by_line(sheet) + GROUP_LINE + "\n"

    def reask(text: str) -> str:
        closing = re.search(r"⟦end group (\S+)⟧\n", text)
        assert closing is not None
        text = text[: closing.end()] + f"\n{PROOF_LINE}\n" + text[closing.end() :]
        return done(text.replace(PROOF_LINE + "\n", "", 1))

    def asked(*files: tuple[str, object]) -> int:
        host = FakeHost(lambda p: "", edits=dict(files))
        monkeypatch.setattr(
            seating, "host_of", lambda agent, executable, tools=None: host
        )
        where = [args[0], "--store", str(store.directory), "--session", "argument"]
        return cli.main(["review", *where, "--budget", "5"])

    capsys.readouterr()
    assert asked((rf.REVIEW_FILE, answer)) == Exit.UNANSWERED
    assert "group lines given as a groups edit, waiting" in capsys.readouterr().out
    assert asked((rf.REVIEW_FILE, answer), (EDGES_FILE, reask)) == 0
    out = capsys.readouterr().out
    assert "group lines given: state" in out
    assert justifying(store, versions.latest(store)) == [
        "group:by-definition-def:gain-the-2"
    ]


def test_a_dismissal_at_the_same_state_is_another_round(
    tmp_path, capsys, monkeypatch
) -> None:
    """The same command after a person's dismissal, at the same state, is
    another round: it asks the dismissed subject, not a replay. Once read,
    the dismissal asks nothing, and the first round replays."""
    store, args = built_state(tmp_path, capsys)
    search_all(store)
    code, host = review_cli(monkeypatch, store, args, read_by_line)
    assert code == 0 and host.calls == 1
    (finding,) = store.judgment_records().findings
    judgments.answer_finding(
        store, finding.id, FindingStatus.DISMISSED, "the example shows it sharp"
    )
    capsys.readouterr()
    code, host = review_cli(monkeypatch, store, args, read_by_line)
    assert code == 0 and host.calls == 1
    sharp = [
        j
        for j in store.judgment_records().judgments
        if j.subject.words == "Next, the bound is sharp."
    ]
    assert len(sharp) == 2
    code, host = review_cli(monkeypatch, store, args, read_by_line)
    assert code == 0 and host.calls == 0


def test_a_stopped_round_asked_again_replays_and_gives_its_edge_lines(
    tmp_path, capsys, monkeypatch
) -> None:
    """§3's sheet is not answered: §2's answer is filed, its edge line
    waits. The same command again is the same round: §2's question is the
    same and replays, §3 alone is asked, and the edge line is given once."""
    store, args = built_state(tmp_path, capsys)
    search_all(store)
    first = versions.latest(store)

    def all_but_more(sheet: str) -> str:
        return sheet if "⟦s5 " in sheet else whole_sheet_answer(sheet)

    code, host = review_cli(monkeypatch, store, args, all_but_more, "--by-section")
    assert code != 0  # §3 not answered
    assert versions.latest(store).id == first.id
    asked_first = host.calls
    # the filed sheet is read from the run, not from its record
    from axiomation import run_state
    from axiomation.commands import review as review_command

    assert run_state.path(store.directory, "review").is_file()
    monkeypatch.setattr(
        review_command, "text_of", lambda answer, run, _: run.state.text(answer.key)
    )
    code, host = review_cli(
        monkeypatch, store, args, whole_sheet_answer, "--by-section"
    )
    out = capsys.readouterr().out
    assert code == 0 and host.calls == 1 < asked_first
    assert "edge lines given: state" in out
    assert not run_state.path(store.directory, "review").exists()
    rounds = {
        a.review_round
        for a in store.authoritative().values()
        if a.kind == "review-argument"
    }
    assert len(rounds) == 1 and None not in rounds


def test_a_round_that_settled_a_finding_is_the_same_question_again(
    lapsed, capsys, monkeypatch
) -> None:
    """The round settles the lapsed finding and reads what the retraction
    reopened; the same command again holds both on its sheet as before, so
    it replays and asks nothing."""
    store, opened, _, severe = lapsed
    args = [str(Path(opened.store.directory).parent / "paper")]

    def settle(sheet: str) -> str:
        judged = [
            f"judge {sid} inconclusive informal — reread after the change"
            for sid in re.findall(r"⟦(s\d+) ", sheet)
        ]
        return sheet + "\n".join([*judged, "stands f1 — still unshown"]) + "\n"

    code, host = review_cli(monkeypatch, store, args, settle)
    assert code == 0 and host.calls == 1
    assert any(f.reaffirms == severe.id for f in store.judgment_records().findings)
    code, host = review_cli(monkeypatch, store, args, settle)
    assert code == 0 and host.calls == 0


def test_a_review_round_is_its_command_at_its_state() -> None:
    """The same command at the same state is one round; a change to what
    decides its questions is another."""
    from dataclasses import replace as changed

    base = dict(
        state="st1",
        session=ARGUMENT,
        template=TEMPLATE,
        select=OPEN,
        scopes=[["3"]],
        sections=False,
        page_finder=None,
        forager=None,
    )
    one = review.review_round(**base)
    assert one == review.review_round(**base) and len(one) == 12
    others = [
        {"state": "st2"},
        {"session": rf.Session.CITATIONCHECK},
        {"template": changed(TEMPLATE, text=TEMPLATE.text + " ")},
        {"select": ALL},
        {"scopes": [["3.1"]]},
        {"scopes": []},
        {"sections": True, "scopes": []},
        {"page_finder": "m"},
        {"forager": "m"},
    ]
    ids = {review.review_round(**{**base, **o}) for o in others}
    assert len(ids) == len(others) and one not in ids


def _read(verdict: Verdict, pages=(), unread=(), author=None) -> judgments.Read:
    from axiomation.records import Judgment, WorkUnread

    judgment = Judgment(
        "j1",
        None,
        "st",
        Aspect.TRUE,
        Subject("n1", None, "it holds"),
        verdict,
        Depth.INFORMAL,
        "read",
        (),
        pages=tuple(pages),
        unread=tuple(WorkUnread(k, ReadStatus.ABSENT) for k in unread),
    )
    return judgments.Read(
        judgment, author or Author(AGENT, "m", "s"), 0, judgments.Walked((), ("n1",))
    )


def _held(read: judgments.Read) -> judgments.SubjectStatus:
    return judgments.SubjectStatus(
        Aspect.TRUE, read.judgment.subject, judgments.StatusKind.HELD, read, (read,)
    )


def test_a_provisional_reading_and_a_reading_whose_evidence_grew_are_asked():
    """`verified` on a copy not of record is settled by a reading of the
    version of record, once it is laid in; a reading that did not verify is
    asked once its evidence grew (`judgments.grown`), held back while it
    did not."""
    from dataclasses import replace

    copy = PageStamp("w1998", 3, ReadStatus.TRANSIENT)
    record = PageStamp("w1998", 3, ReadStatus.OK)
    provisional = _held(_read(Verdict.VERIFIED, [copy]))
    assert review.held_back(provisional, ["w1998"]) is None
    assert review.held_back(provisional, ["other2001"]) is HELD
    assert review.held_back(_held(_read(Verdict.VERIFIED, [record]))) is HELD
    grew = (judgments.Growth("w1998", "w1998 reads a file it was not given"),)
    unsure = _held(_read(Verdict.INCONCLUSIVE))
    assert review.held_back(unsure) is review.Holds.INCONCLUSIVE
    assert review.held_back(replace(unsure, grown=grew)) is None
    missed = _held(_read(Verdict.CONTRADICTED, [copy]))
    assert review.held_back(missed) is HELD
    assert review.held_back(replace(missed, grown=grew)) is None
    assert review.held_back(None) is None


def test_the_default_asks_a_lapsed_negative_an_inconclusive_left_standing(
    tmp_path, capsys
) -> None:
    """Its status is held (`inconclusive`), yet no reading settles the
    negative that lapsed: the default asks it."""
    store, _ = built_state(tmp_path, capsys)
    search_all(store)
    whole = asked_now(store)
    on = whole.subjects["s2"].subject
    by_hand(store, whole, "s2", Verdict.CONTRADICTED)
    footnote_retracted(store)
    by_hand(store, asked_now(store, ALL), "s2", Verdict.INCONCLUSIVE, model="m-b")
    assert on in [s.subject for s in asked_now(store).subjects.values()]


def test_by_section_leaves_out_a_section_that_holds_and_names_it(state) -> None:
    """§3's one subject holds: it is not asked, and its held-back count is
    told; §2 is asked with its own count."""
    store, _, whole = state
    by_hand(store, whole, "s1", Verdict.VERIFIED)
    by_hand(store, whole, "s5", Verdict.VERIFIED)
    with edits.opened(store, versions.latest(store)) as opened:
        scoped = review.scoped_briefs(opened, ARGUMENT, TEMPLATE, None, sections=True)
    (results,) = scoped.briefs
    assert results.scope == "§2 Results" and len(results.subjects) == 3
    assert [h.subject for h in results.held] == [whole.subjects["s1"].subject]
    (more,) = scoped.unasked
    assert more.scope == "§3 More" and [h.subject for h in more.held] == [
        whole.subjects["s5"].subject
    ]


def test_by_section_asks_a_section_whose_subjects_hold_for_its_finding(
    lapsed,
) -> None:
    """Every subject of §2 read again by a person: the lapsed finding there
    is still a question, so §2 is asked on it alone."""
    store, _, _, severe = lapsed
    plain = asked_now(store)
    for sid in plain.subjects:
        by_hand(store, plain, sid, Verdict.VERIFIED)
    with edits.opened(store, versions.latest(store)) as opened:
        briefs = review.scoped_briefs(
            opened, ARGUMENT, TEMPLATE, None, sections=True
        ).briefs
    (results,) = briefs
    assert results.scope == "§2 Results" and results.subjects == {}
    assert [q.read.finding.id for q in results.questions.values()] == [severe.id]


def test_a_proof_s_handle_selects_its_valid(state) -> None:
    """The `valid` lies with its theorem, and J's names select it too: the
    proof group's handle and a block of the group's."""
    _, opened, whole = state
    valid = whole.subjects["s3"].subject
    for name in (PROOF, "small-means-below-one"):
        (named,) = review.scoped_briefs(
            opened, ARGUMENT, TEMPLATE, None, scopes=[[name]]
        ).briefs
        assert valid in [s.subject for s in named.subjects.values()]
    (bound,) = review.scoped_briefs(
        opened, ARGUMENT, TEMPLATE, None, scopes=[["thm:bound"]]
    ).briefs
    assert valid in [s.subject for s in bound.subjects.values()]


def test_a_name_that_selects_nothing_says_why(state) -> None:
    """A definition holds no subject; a theorem whose subjects all hold is
    held back, `--all-again` named; a name of nothing is told how to name."""
    store, _, whole = state
    by_hand(store, whole, "s1", Verdict.VERIFIED)
    by_hand(store, whole, "s3", Verdict.VERIFIED)
    with edits.opened(store, versions.latest(store)) as opened:

        def refused(*names: str) -> str:
            with pytest.raises(review.ReviewError) as error:
                review.scoped_briefs(
                    opened, ARGUMENT, TEMPLATE, None, scopes=[list(names)]
                )
            return str(error.value)

        assert refused("def:gain") == (
            "def:gain holds no subject the argument session judges"
        )
        assert refused("thm:bound") == (
            "every subject thm:bound selects is held back, 2 (held 2): "
            "--all-again asks them"
        )
        both = refused("nope", "def:gain")
        assert both.startswith("no subject is named nope: name a sheet id")
        assert both.endswith("; def:gain holds no subject the argument session judges")
        (every,) = review.scoped_briefs(
            opened, ARGUMENT, TEMPLATE, None, select=ALL, scopes=[["thm:bound"]]
        ).briefs
        assert len(every.subjects) == 2


def test_a_name_with_a_stale_mark_and_no_subject_names_reread(
    state, monkeypatch
) -> None:
    from axiomation.marks import Mark

    _, opened, _ = state
    view = queries.View(opened.store, opened.state)
    mark = Mark(view.node(view.resolve("def:gain")), "def:gain", "s0", ())
    monkeypatch.setattr(queries.View, "marks", property(lambda self: (mark, mark)))
    with pytest.raises(review.ReviewError) as error:
        review.scoped_briefs(opened, ARGUMENT, TEMPLATE, None, scopes=[["def:gain"]])
    assert str(error.value) == (
        "def:gain holds no subject the argument session judges; its open stale "
        "marks clear by `axiomation reread PAPER def:gain --reason …` once read"
    )


def test_review_s_dry_run_counts_what_it_holds_back(state, capsys) -> None:
    from axiomation.cli import main

    store, opened, whole = state
    by_hand(store, whole, "s1", Verdict.VERIFIED)
    by_hand(store, whole, "s5", Verdict.VERIFIED)
    paper = str(Path(opened.store.directory).parent / "paper")
    where = [paper, "--store", str(store.directory), "--session", "argument"]
    capsys.readouterr()
    dry = ["--dry-run", "--prompts", str(Path(store.directory).parent / "d")]
    assert main(["review", *where, *dry]) == 0
    out = capsys.readouterr().out
    assert ": asks 3 subjects, " in out
    assert "  holds back 2 (held 2): --all-again asks them" in out
    assert main(["review", *where, "--by-section", *dry]) == 0
    out = capsys.readouterr().out
    held = "holds back 2 (held 2): --all-again asks them"
    assert f"  in all: asks 3 subjects; {held}" in out
    assert "  §2 Results: asks 3 subjects, holds back 1 (held 1), " in out
    assert "  §3 More: not asked, holds back 1 (held 1): --all-again asks them" in out
    assert main(["review", *where, "--all-again", *dry]) == 0
    out = capsys.readouterr().out
    assert ": asks 5 subjects, " in out and "holds back" not in out
    for gone in ("--lapsed", "--all"):
        with pytest.raises(SystemExit):
            main(["review", *where, gone, *dry])
        assert f"unrecognized arguments: {gone}" in capsys.readouterr().err


def test_a_dry_run_prices_an_answer_the_store_holds_at_nothing(state) -> None:
    """A run stopped after the session answered, before the command filed
    it: the dry run names the answer the store holds under the brief's key
    and prices it at $0, as the command files it asking nothing."""
    from axiomation.commands import review as reviewing
    from axiomation.profiles import Agent, Harness
    from axiomation.runner import LikeRuns

    store, opened, found = state
    agent = Agent(Harness.CLAUDE_CODE, "claude-opus-5-5", rates=FLAT)
    said: list[str] = []
    say = types.SimpleNamespace(line=said.append)
    view = queries.View(store, opened.state)

    assert reviewing._say_briefs(ARGUMENT, view, [found], agent, LikeRuns(), say) != (
        0.0,
        0.0,
    )
    run(store, opened, found, full(found))
    said.clear()

    assert reviewing._say_briefs(ARGUMENT, view, [found], agent, LikeRuns(), say) == (
        0.0,
        0.0,
    )
    assert said == [
        f"argument: held, answer {found.question.key[:12]}: filed at $0, nothing asked"
    ]


def test_review_asks_the_seats_asked_by_default_and_names_the_rest(
    state, capsys
) -> None:
    """With no `--session`, the seats asked by default, the paper's own
    four, and a line naming the rest; `--session all` asks every seated
    one and names none; as a member of a group under `--scope`, the
    fieldresults session is left out before any is asked."""
    from axiomation.cli import main

    store, opened, _ = state
    paper = str(Path(opened.store.directory).parent / "paper")
    where = [paper, "--store", str(store.directory)]
    dry = ["--dry-run", "--prompts", str(Path(store.directory).parent / "d")]

    def asked(*flags: str) -> tuple[str, set[str]]:
        capsys.readouterr()
        assert main(["review", *where, *flags, *dry]) == 0
        out = capsys.readouterr().out
        lines = [line for line in out.splitlines() if line[0] != " "]
        return out, {line.split()[0].rstrip(":") for line in lines}

    out, sessions = asked()
    left = (
        "not asked by default: contribution, fieldresults, relatedwork "
        "(--session NAME asks one, --session all every seated session)"
    )
    assert left in out
    assert sessions - {"not"} == {"argument", "citationcheck", "methods", "findings"}
    out, sessions = asked("--session", "all")
    assert "not asked by default" not in out
    assert sessions == {s.value for s in rf.Session}
    out, _ = asked("--session", "field", "--scope", "def:gain")
    assert "fieldresults: the fieldresults session's inventory reads" in out
    assert "it takes no --scope or --by-section; not asked" in out


def test_a_group_asks_its_seated_members_and_names_the_unseated(
    state, capsys, tmp_path
) -> None:
    """`--session field` under a profile that seats no contribution reviewer
    asks the other two and names the one it skips; the session named by
    itself is refused."""
    import re
    from importlib import resources

    from axiomation.cli import main

    store, opened, _ = state
    paper = str(Path(opened.store.directory).parent / "paper")
    shipped = resources.files("axiomation").joinpath("profiles.toml").read_text()
    seat = re.compile(r"^\[standard\.review\.contribution\]\n(?:[^\[\n].*\n|\n)*", re.M)
    assert seat.search(shipped)
    without = tmp_path / "profiles.toml"
    without.write_text(seat.sub("", shipped))
    where = [paper, "--store", str(store.directory), "--profiles", str(without)]
    dry = ["--dry-run", "--prompts", str(Path(store.directory).parent / "d")]

    capsys.readouterr()
    assert main(["review", *where, "--session", "field", *dry]) == 0
    out = capsys.readouterr().out
    assert "not seated: contribution (profile standard has no" in out
    asked = {line.split()[0].rstrip(":") for line in out.splitlines() if line[0] != " "}
    assert asked - {"not"} == {"fieldresults", "relatedwork"}
    assert main(["review", *where, "--session", "contribution", *dry]) == 1
    err = capsys.readouterr().err
    assert "profile standard seats no reviewer for contribution" in err


# --- the pile -----------------------------------------------------------------


def test_open_merges_an_exact_duplicate_and_orders_by_severity_then_reach(
    state,
) -> None:
    store, opened, found = state
    kept = opened.state
    at = judgments.seen(store, kept)
    view = queries.View(store, kept)
    by_handle = {h: i for i, h in view.handles.items()}

    def on(handle: str, words: str):
        ident = by_handle[handle]
        return [
            judgments.anchored(
                at, judgments.AnchorKind.SPAN, ident, words, text=view.texts[ident]
            )
        ]

    a, b = Author(AGENT, "m", "a"), Author(AGENT, "n", "b")
    judgments.find(
        store, kept, Aspect.TRUE, on("next-the-bound-is", "sharp"), 1, "x", None, a
    )
    judgments.find(store, kept, Aspect.TRUE, on("def:gain", "ratio"), 2, "y", None, a)
    judgments.find(store, kept, Aspect.TRUE, on("def:gain", "ratio"), 2, "z", None, b)
    judgments.find(
        store, kept, Aspect.TRUE, on("thm:bound", "bounded"), 2, "w", None, a
    )
    piles = queries.finding_piles(queries.View(store, kept))
    assert [[f.finding.problem for f in pile] for pile in piles] == [
        ["y", "z"],  # def:gain reaches more than thm:bound
        ["w"],
        ["x"],
    ]
    items = queries.open_report(queries.View(store, kept), kinds=("finding",))
    assert items.sections["finding"][0].data["same"] == [piles[0][1].finding.id]


def _four_findings(store, kept):
    """y on def:gain, w on thm:bound (both severity 2), x and v on
    next-the-bound-is (severity 1 and 3), each with quoted words."""
    at = judgments.seen(store, kept)
    view = queries.View(store, kept)
    by_handle = {h: i for i, h in view.handles.items()}

    def found(handle: str, words: str, severity: int, problem: str) -> str:
        ident = by_handle[handle]
        anchor = judgments.anchored(
            at, judgments.AnchorKind.SPAN, ident, words, text=view.texts[ident]
        )
        filed = judgments.find(
            store, kept, Aspect.TRUE, [anchor], severity, problem, None
        )
        return filed.records.findings[0].id

    return (
        found("def:gain", "ratio", 2, "y"),
        found("thm:bound", "bounded", 2, "w"),
        found("next-the-bound-is", "sharp", 1, "x"),
        found("next-the-bound-is", "Next", 3, "v"),
    )


def test_a_finding_answered_a_duplicate_folds_under_the_one_it_duplicates(
    state,
) -> None:
    """A person's `duplicate` answer leaves `open`'s list and stands in its
    original's pile, along a chain of them; the original clears the pile."""
    store, opened, _ = state
    kept = opened.state
    y, w, x, v = _four_findings(store, kept)
    judgments.answer_finding(store, x, FindingStatus.DUPLICATE, None, of=w)
    judgments.answer_finding(store, v, FindingStatus.DUPLICATE, "the same", of=x[:14])
    piles = queries.finding_piles(queries.View(store, kept))
    # the pile takes its highest severity, v's 3, so it stands first
    assert [[f.finding.problem for f in pile] for pile in piles] == [
        ["w", "x", "v"],
        ["y"],
    ]
    (folded,) = [
        i
        for i in queries.open_report(
            queries.View(store, kept), kinds=("finding",)
        ).sections["finding"]
        if i.data["same"]
    ]
    assert folded.data["same"] == [x, v]
    assert folded.data["clears"] == f"`axiomation answer-finding` {w}"
    # the original answered, its pile leaves with it
    judgments.answer_finding(store, w, FindingStatus.FIXED)
    piles = queries.finding_piles(queries.View(store, kept))
    assert [[f.finding.problem for f in pile] for pile in piles] == [["y"]]


def test_a_duplicate_names_its_original_and_never_closes_a_cycle(state) -> None:
    store, opened, _ = state
    y, w, x, _ = _four_findings(store, opened.state)
    with pytest.raises(JudgmentError, match="names the finding it duplicates"):
        judgments.answer_finding(store, x, FindingStatus.DUPLICATE)
    with pytest.raises(JudgmentError, match="not a fixed one"):
        judgments.answer_finding(store, x, FindingStatus.FIXED, of=w)
    with pytest.raises(JudgmentError, match="not its own duplicate"):
        judgments.answer_finding(store, x, FindingStatus.DUPLICATE, of=x)
    judgments.answer_finding(store, x, FindingStatus.DUPLICATE, of=w)
    judgments.answer_finding(store, w, FindingStatus.DUPLICATE, of=y)
    with pytest.raises(JudgmentError, match=f"{x} is answered a duplicate of it"):
        judgments.answer_finding(store, y, FindingStatus.DUPLICATE, of=x)
    # an answer that speaks no more closes nothing: x answered again
    judgments.answer_finding(store, x, FindingStatus.POSTPONED)
    judgments.answer_finding(store, y, FindingStatus.DUPLICATE, of=x)
    (answer,) = [a for a in store.judgment_records().answers if a.finding == y]
    assert answer.of == x


def test_findings_without_quoted_words_are_never_merged(state) -> None:
    """Two findings on the paper with different words are two problems."""
    store, opened, found = state
    run(
        store,
        opened,
        found,
        full(
            found,
            'find true paper 2 "The output is bounded" — one problem',
            'find true paper 2 "the bound is sharp" — another problem',
        ),
    )
    piles = queries.finding_piles(queries.View(store, opened.state))
    assert [[f.finding.problem for f in pile] for pile in piles] == [
        ["one problem"],
        ["another problem"],
    ]


def test_the_report_groups_the_findings_under_their_results(state) -> None:
    store, opened, found = state
    run(store, opened, found, full(found, FINDING))
    report = queries.review_report(queries.View(store, opened.state))
    assert report.head == [f"1 open findings at state {opened.state.id}, on 1 results"]
    (name,) = report.sections
    assert name.startswith("next-the-bound-is (claim, not established")


def test_a_scope_names_a_section_by_its_number_or_one_title_s_words(
    tmp_path,
) -> None:
    """`--scope A1` is the appendix its title numbers, with what lies
    within; title words name the one section holding them, by its own
    name; words two titles hold are refused, naming both."""
    view = appendix_view(tmp_path)
    by_text = {view.texts[b]: b for b in view.blocks}
    setup = by_text["The setup."]
    assert review.scope_name(view, "A1") == "A1"
    assert review.in_scope(view, setup, "A1")
    assert review.in_scope(view, setup, "§A1.1")
    assert not review.in_scope(view, by_text["The second appendix."], "A1")
    named = review.scope_name(view, "Drift and Rigidity")
    assert named == "Appendix A1 · Drift and Rigidity"
    assert review.in_scope(view, setup, named)
    assert review._label(view, named) == named
    with pytest.raises(review.ReviewError, match="names 2 sections") as refused:
        review.scope_name(view, "Rigidity")
    assert "A1.2 Rigidity of the bound" in str(refused.value)
    # a name that selects nothing is left for the caller to refuse
    assert review.scope_name(view, "nope") == "nope"


def test_the_report_names_each_finding_s_anchors(state) -> None:
    """A finding under "elsewhere" or a result names the block and the
    words it anchors, so the report reads without `--json`."""
    store, opened, found = state
    run(store, opened, found, full(found, FINDING))
    report = queries.review_report(queries.View(store, opened.state))
    ((item,),) = report.sections.values()
    assert item.line.startswith(
        f"finding {item.data['id']}  severity 2  true  open  on next-the-bound-is · "
        '"the bound is sharp"  "sharpness is claimed and never shown"'
    )


# --- the command ------------------------------------------------------------------


def test_a_review_s_estimate_grows_with_its_subjects(state) -> None:
    """The session's turns read its context again, and each subject adds
    its reading and that context read again each turn, priced at the
    model's rates; a model with no cache price pays input."""
    from axiomation.runner.hosts import Rates

    _, _, found = state
    n = len(found.subjects) + len(found.questions)
    assert n > 0
    opus = Rates(4, 20, 0.20, 8)
    each = (
        review.SUBJECT_OUTPUT_TOKENS * 20
        + review.SUBJECT_CACHE_READ_TOKENS * 0.20
        + review.SUBJECT_CACHE_WRITE_TOKENS * 8
    ) / 1_000_000
    turns = review.SESSION_CACHE_READ_TOKENS * 0.20 / 1_000_000
    assert found.turns_cost(opus) == pytest.approx(turns + n * each)
    assert 0.08 < each < 0.15  # about $0.11 a subject on Opus
    assert 0.4 < turns < 0.7  # about $0.54 a session on Opus
    plain = Rates(1, 1)
    assert found.turns_cost(plain) == pytest.approx(
        (
            review.SESSION_CACHE_READ_TOKENS
            + n
            * (
                review.SUBJECT_OUTPUT_TOKENS
                + review.SUBJECT_CACHE_READ_TOKENS
                + review.SUBJECT_CACHE_WRITE_TOKENS
            )
        )
        / 1_000_000
    )
    fewer = replace(found, subjects={}, questions={})
    assert found.turns_cost(opus) - fewer.turns_cost(opus) == pytest.approx(n * each)


def test_review_asks_no_citation_session_where_no_cited_work_reads(
    tmp_path, capsys, monkeypatch
) -> None:
    """The citationcheck session is read on the cited works' pages alone: where
    no corpus reads, a review of every seated session skips it, saying so,
    and one naming it is refused."""
    from axiomation import evidence
    from axiomation.cli import main

    def unread(paper, store):
        raise evidence.EvidenceError("no corpus here")

    monkeypatch.setattr(evidence, "sources_for", unread)
    store, args = built_state(tmp_path, capsys)
    search_all(store)
    every = ["review", args[0], "--store", str(store.directory)]
    assert main([*every, "--dry-run", "--prompts", str(tmp_path / "d")]) == 0
    assert "citationcheck: no cited work can be read here; not asked" in (
        capsys.readouterr().out
    )
    assert main(
        [
            *every,
            "--session",
            "citationcheck",
            "--dry-run",
            "--prompts",
            str(tmp_path / "e"),
        ]
    )
    assert "`axiomation sources` sets them up" in capsys.readouterr().err


def test_review_s_dry_run_writes_the_files_and_prices_them(tmp_path, capsys) -> None:
    from axiomation.cli import main

    store, args = built_state(tmp_path, capsys)
    search_all(store)
    paper = args[0]
    kept = ["review", paper, "--store", str(store.directory)]
    # priced with no directory: nothing written
    assert main([*kept, "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "argument at state" in out and "5 subjects" in out
    assert not (tmp_path / "d").exists()
    assert main([*kept, "--dry-run", "--prompts", str(tmp_path / "d")]) == 0
    assert "argument at state" in capsys.readouterr().out
    root = tmp_path / "d" / "argument"
    assert {p.name for p in root.iterdir()} >= {
        rf.PAPER_FILE,
        rf.REVIEW_FILE,
        "instructions.md",
        "gate.json",
    }
    assert rf.HINTS_FILE not in {p.name for p in root.iterdir()}
    where = [paper, "--store", str(store.directory), "--session", "argument"]
    chosen = ["--scope", "s2", "--scope", "s4,s5"]
    assert (
        main(["review", *where, *chosen, "--dry-run", "--prompts", str(tmp_path / "c")])
        == 0
    )
    assert "one session per scope: 2 sessions" in capsys.readouterr().out
    assert sorted(p.name for p in (tmp_path / "c" / "argument").iterdir()) == [
        "s2",
        "s4-s5",
    ]
    scoped = ["--by-section", "--dry-run", "--prompts", str(tmp_path / "b")]
    assert main(["review", *where, *scoped]) == 0
    out = capsys.readouterr().out
    assert "one session per scope:" in out and "  §2 Results: " in out
    assert "§2-Results" in {p.name for p in (tmp_path / "b" / "argument").iterdir()}
    one = ["--scope", "Results", "--dry-run", "--prompts", str(tmp_path / "r")]
    assert main(["review", *where, *one]) == 0
    assert "  §2 Results: " in capsys.readouterr().out
    assert main(["review", *where, "--by-section", "--scope", "s1"]) == 1
    assert "--by-section takes no --scope" in capsys.readouterr().err
    unknown = ["--scope", "nope"]
    assert (
        main(
            ["review", *where, *unknown, "--dry-run", "--prompts", str(tmp_path / "n")]
        )
        == 1
    )
    assert "no subject is named nope" in capsys.readouterr().err
    assert main(["review", paper, "--store", str(store.directory)]) == 1
    assert "needs --budget" in capsys.readouterr().err
    assert (
        main(["review", paper, "--store", str(store.directory), "--host", "none"]) == 5
    )
    assert "not answered (nothing asked)" in capsys.readouterr().out
    # --prompts without --dry-run writes, then asks: here nothing
    asked = ["--host", "none", "--prompts", str(tmp_path / "a")]
    assert main([*kept, *asked]) == 5
    assert "not answered (nothing asked)" in capsys.readouterr().out
    assert (tmp_path / "a" / "argument" / "instructions.md").is_file()


def test_a_review_session_s_host_has_the_reading_tools(
    tmp_path, capsys, monkeypatch
) -> None:
    from axiomation import cli
    from axiomation.commands import seating
    from axiomation.runner.claude_code import READING_TOOLS

    store, args = built_state(tmp_path, capsys)
    search_all(store)
    given = []

    def host(agent, executable):
        given.append(agent.tools)
        raise cli.UsageError("no host in a test")

    monkeypatch.setattr(seating, "host_of", host)
    where = [args[0], "--store", str(store.directory), "--session", "argument"]
    assert cli.main(["review", *where, "--budget", "1"]) == 1
    assert given == [READING_TOOLS]


def test_review_and_adopt_from_the_command_line(state, capsys) -> None:
    from axiomation.cli import main

    store, opened, found = state
    run(store, opened, found, full(found, FINDING))
    paper = str(Path(opened.store.directory).parent / "paper")
    where = ["--store", str(store.directory)]
    capsys.readouterr()
    # the findings session, which has no subject to lack, is never skipped:
    # named alone here, the argument session's answer is the one read
    assert (
        main(
            [
                "review",
                paper,
                *where,
                "--session",
                "argument",
                "--all-again",
                "--host",
                "none",
            ]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "filed by fake fake/model" in out and "5 judgments, 1 findings" in out
    assert main(["review", paper, *where, "--report"]) == 0
    assert "sharpness is claimed" in capsys.readouterr().out
    valid = next(
        j for j in store.judgment_records().judgments if j.aspect is Aspect.VALID
    )
    assert main(["adopt", paper, *where, valid.id, "--person", "Ada"]) == 0
    assert f"adopts {valid.id}" in capsys.readouterr().out


def test_a_justifies_edge_line_replaces_j_s_line_onto_t(state) -> None:
    """The argument session corrects a justifies line,
    here its flag: its line replaces J's line onto T, and the state the
    agent's edit makes holds one justifies edge, flagged."""
    store, opened, found = state
    line = (
        f'edge {PROOF} → justifies thm:bound name sketch · "Hence the output is '
        'bounded." — the proof outlines the bound only'
    )
    run(store, opened, found, full(found, line))
    answer = store.authoritative()[found.question.key]
    gave = review.edges_given(
        opened, [(found, answer)], sheets(store), edits.agent("fake/model", "s")
    )
    assert gave.given is not None and gave.given.state is not None
    export = json.loads(versions.kept_export(store, gave.given.state))
    (justifies,) = [e for e in export["edges"] if e["kind"] == "justifies"]
    assert justifies["partial"] == "sketch"


def test_a_subject_a_narrowed_line_takes_out_of_j_s_cover_needs_no_judge_line():
    """A justifies edge line naming one claim of T by its handle covers
    that claim alone, so the subject on T's other claim needs no judge
    line; without the line it does."""
    subjects = [
        rf.SheetSubject(
            f"s{n}",
            Aspect.VALID,
            f'p → t · "{w}"',
            edge=("p", "t", h),
            claims=("a", "b"),
        )
        for n, (h, w) in enumerate((("a", "A holds."), ("b", "B holds.")), 1)
    ]
    texts = {"p": "By the gain, A holds.", "t": "A holds. B holds."}
    sheet = rf.render(ARGUMENT, subjects)
    spec = rf.spec(ARGUMENT, sheet, subjects, texts, {}, [])
    judged = sheet + "judge s1 verified informal — the gain gives it\n"
    assert "no judge line on s2" in rf.read(spec, judged).problems
    narrowed = judged + 'edge p → justifies t [a] · "By the gain" — it argues A only\n'
    assert rf.read(spec, narrowed).problems == ()


def test_a_self_edge_line_replaces_the_mark_with_its_quote() -> None:
    """A review's self edge line replaces its block's mark with the same
    quote, whatever it names or flags; a justifies line J's line onto the
    same T; a uses line nothing."""
    written = {
        "b": [
            '→ self · "by the gain"',
            '→ self · "since f is bounded"',
            '→ justifies t · "Hence"',
            '→ justifies u · "Hence"',
        ]
    }

    def corrected(line: str) -> list[str]:
        owner, rest = line.split(" ", 1)
        return rf.corrected(written, rf.EdgeLine(1, owner, rest))

    assert corrected('b → self [tiny] sketch · "by the gain"') == [
        '→ self · "by the gain"'
    ]
    assert corrected('b → justifies t [main] · "Hence"') == ['→ justifies t · "Hence"']
    assert corrected('b → uses t · "Hence"') == []


def test_a_justifies_line_replaces_only_the_lines_covering_its_claims() -> None:
    """J's two lines onto T, one per claim: a review narrowing one replaces
    it alone; a line naming no claim covers each, so it replaces both."""
    written = {
        "b": [
            '→ justifies t [a] · "Hence"',
            '→ justifies t [c] analogy · "likewise"',
        ]
    }

    def corrected(line: str) -> list[str]:
        owner, rest = line.split(" ", 1)
        return rf.corrected(written, rf.EdgeLine(1, owner, rest))

    assert corrected('b → justifies t [a] sketch · "Hence"') == [
        '→ justifies t [a] · "Hence"'
    ]
    assert corrected('b → justifies t · "Hence"') == written["b"]


# --- a lapsed finding's question ------------------------------------------------

#: A severity-3 finding on the proof block, which its uses line onto the
#: footnote is part of the basis of.
SEVERE = (
    'find true by-definition-def:gain-the 3 "Hence the output is bounded." — the '
    "footnote's smallness is never shown — show the gain is below one"
)
FOOTNOTE_LINE = '→ uses small-means-below-one · "the gain is small" — the footnote says'


def footnote_retracted(store) -> versions.State:
    """The proof's uses line onto the footnote retracted by a person: a new
    state, where every reading resting on that edge lapses."""
    with edits.opened(store, versions.latest(store)) as kept:
        shown = edits.shown(kept, Layer.EDGES)
        edited = edits.applied_fragment(
            shown,
            edits.Fragment(lines=((PROOF_BLOCK, edits.RETRACT, FOOTNOTE_LINE),)),
        )
        given = edits.give(kept, Layer.EDGES, edited, reason="not a reliance")
    assert given.state is not None
    return given.state


def latest_brief(store, session=ARGUMENT, template=TEMPLATE):
    kept = versions.latest(store)
    assert kept is not None
    context = edits.opened(store, kept)
    opened = context.__enter__()
    try:
        found = review.brief(opened, session, template, None)
    except Exception:
        context.__exit__(None, None, None)
        raise
    return context, opened, found


def settled(found: review.Brief, *lines: str) -> str:
    """The lapsed sheet with each lapsed subject read inconclusive and
    `lines` after."""
    judged = [
        f"judge {sid} inconclusive informal — reread after the change"
        for sid in found.subjects
    ]
    return sheet_with(found, *judged, *lines)


@pytest.fixture
def lapsed(tmp_path, capsys):
    """The argument session's severity-3 finding on the proof, lapsed by a
    person's retraction of the proof's uses line onto the footnote."""
    store, _ = built_state(tmp_path, capsys)
    search_all(store)
    context, opened, found = opened_brief(store)
    run(store, opened, found, full(found, SEVERE))
    context.__exit__(None, None, None)
    (severe,) = store.judgment_records().findings
    footnote_retracted(store)
    context, opened, found = latest_brief(store)
    yield store, opened, found, severe
    context.__exit__(None, None, None)


def test_a_lapsed_finding_a_session_filed_is_its_question(
    lapsed,
) -> None:
    _, _, found, severe = lapsed
    (question,) = found.questions.values()
    assert question.sheet.id == "f1" and question.read.finding.id == severe.id
    sheet = found.spec["reference"]
    assert (
        '⟦f1 finding⟧ true 3 by-definition-def:gain-the "Hence the output is '
        "bounded.\" — the footnote's smallness is never shown"
    ) in sheet
    assert "  ↳ lapsed at " in sheet and rf.STANDS_FORM in sheet
    assert found.spec["questions"]["f1"]["anchors"] == [PROOF_BLOCK]


def test_a_plain_review_settles_the_lapsed_findings_it_reads(lapsed) -> None:
    """A plain review reads the lapsed finding's block too, so it settles
    the finding beside its subjects: one session covers both."""
    store, opened, _, severe = lapsed
    plain = review.brief(opened, ARGUMENT, TEMPLATE, None)
    (question,) = plain.questions.values()
    assert question.read.finding.id == severe.id

    def lines(found: review.Brief) -> set[str]:
        return {s.sheet.line for s in found.subjects.values()}

    # what the retraction reopened is all a new reading can settle; `--all-again`
    # asks the readings that hold besides
    every = review.brief(opened, ARGUMENT, TEMPLATE, None, select=ALL)
    assert lines(plain) < lines(every) and len(plain.held) == 3
    sheet = plain.spec["reference"]
    assert "⟦f1 finding⟧" in sheet and rf.STANDS_FORM in sheet
    problems = rf.read(plain.spec, sheet_with(plain)).problems
    assert any("no stands, fixed or reading line on f1" in p for p in problems), (
        problems
    )


def _updated_on(tmp_path, changed: str):
    """A build whose proof's `valid` an agent read, updated to a version
    where block `changed` reads otherwise; its view."""
    from test_judgments import BLOCKS, STANDINGS, TEXT, built, graph, same, updated

    store, first = built(tmp_path)
    at = judgments.seen(store, first)
    judgments.judge(
        store,
        first,
        Aspect.VALID,
        judgments.subject(at, Aspect.VALID, "p"),
        Verdict.VERIFIED,
        Depth.INFORMAL,
        "d bounds it",
        Author(AGENT, "m", "s"),
    )
    old = dict(BLOCKS)[changed]
    new = old.replace(".", " here.")
    text = TEXT.replace(old, new)
    blocks = [(b, new if b == changed else w) for b, w in BLOCKS]
    standings = [(b, new if b == changed else w) for b, w in STANDINGS]
    second = updated(
        store,
        first,
        text,
        graph(text, blocks, standings=standings),
        same("d", "t", "p", "r", but={changed: "changed"}),
    )
    return store, queries.View(store, second)


def test_the_default_asks_what_the_update_changed_beside_what_lapsed(
    tmp_path,
) -> None:
    """The update rewrites the theorem, which nobody had read: the default
    asks its `true` beside the proof's lapsed `valid`, since the proof is
    settled with the new statement read."""
    store, view = _updated_on(tmp_path, "t")
    t = view.node(view.resolve("t"))
    asked = {(s.aspect, s.subject.node) for s in review.subjects(view, ARGUMENT)}
    p = view.node(view.resolve("p"))
    assert asked == {(Aspect.VALID, p), (Aspect.TRUE, t)}


def test_the_default_asks_an_unread_subject_the_update_did_not_touch(
    tmp_path,
) -> None:
    """The update rewrites the remark alone: the proof's `valid` still
    holds, and the theorem's `true`, never read, is asked."""
    store, view = _updated_on(tmp_path, "r")
    t = view.node(view.resolve("t"))
    asked = {(s.aspect, s.subject.node) for s in review.subjects(view, ARGUMENT)}
    assert asked == {(Aspect.TRUE, t)}


# --- what a session that judges nothing reads ------------------------------------


def _answered(store, state, template="review-findings", read=None, **given) -> str:
    """A valid answer of the findings session's kind at `state`, under
    `template`, that read `read`; its key."""
    from axiomation.records import TemplateRef

    key = f"answer-{len(store.answers())}"
    store.append(
        AnswerRecord(
            key=key,
            kind=given.pop("kind", "review-findings"),
            stage="review",
            template=TemplateRef(template, "1"),
            attempt=1,
            host="fake",
            model="m",
            time="2026-11-02T00:00:00+00:00",
            seconds=0.0,
            cost=0.0,
            valid=True,
            state=state.id,
            read=None if read is None else tuple(read),
            **given,
        )
    )
    return key


def test_a_findings_review_read_what_its_state_holds_as_the_text_now_does(
    tmp_path,
) -> None:
    """An update rewrites the theorem: a findings review of the state
    before read every block but the theorem as it now stands, and the
    theorem is the one block unread."""
    store, view = _updated_on(tmp_path, "t")
    first = versions.state(store, view.state.parent)
    findings = rf.Session.FINDINGS
    assert review.text_read(view, findings) == frozenset()
    _answered(store, first)
    view = queries.View(store, view.state)
    t = view.node(view.resolve("t"))
    assert review.text_read(view, findings) == {n.node for n in view.state.nodes} - {t}
    assert review.unread_blocks(view, review.text_read(view, findings)) == [
        view.resolve("t")
    ]


def test_a_findings_review_read_its_part_and_its_settling_none(
    tmp_path,
) -> None:
    """A review that read a part read those blocks; one that settled its
    findings alone read none; another session's answer, a step of its
    queue, and an answer of the round asked again count for nothing."""
    store, view = _updated_on(tmp_path, "t")
    findings = rf.Session.FINDINGS
    d, t = (view.node(view.resolve(b)) for b in ("d", "t"))
    _answered(store, view.state, read=[d, t])
    _answered(store, view.state, "review-findings+lapsed")
    _answered(store, view.state, kind="review-argument")
    _answered(store, view.state, kind="review-findings-step")
    view = queries.View(store, view.state)
    assert review.text_read(view, findings) == {d, t}
    again = _answered(store, view.state, review_round="r1")
    view = queries.View(store, view.state)
    assert review.text_read(view, findings) == {n.node for n in view.state.nodes}
    asked_again = review.round_read(store, "r1")
    assert again in asked_again.answers
    assert review.text_read(view, findings, asked_again) == {d, t}
    assert review.text_read(view, rf.Session.CONTRIBUTION) == frozenset()


def test_a_findings_session_reads_what_a_scoped_review_left_unread(
    tmp_path, capsys
) -> None:
    """A review scoped to §2 reads its blocks, and its answer names them;
    the default then reads the rest alone, its scope naming them, its dry
    run saying so, and names them in turn; after it, nothing is asked."""
    from axiomation import cli

    findings = rf.Session.FINDINGS
    template = load_template("review-findings")
    store, args = built_state(tmp_path, capsys)
    search_all(store)
    with edits.opened(store, versions.latest(store)) as opened:
        view = queries.View(store, opened.state)
        (scoped,) = review.scoped_briefs(
            opened, findings, template, None, scopes=[["2"]]
        ).briefs
        part = [b for b in view.blocks if review.in_scope(view, b, "2")]
        assert scoped.read == tuple(view.node(b) for b in part)
        run(store, opened, scoped, sheet_with(scoped, rf.DONE))
        (filed,) = [a for a in store.answers() if a.kind == "review-findings"]
        assert filed.read == scoped.read
        rest = review.brief(opened, findings, template, None)
        others = [b for b in view.blocks if b not in part]
        assert rest.read == tuple(view.node(b) for b in others)
        assert rest.question.template.name == "review-findings+scope"
        assert rest.scope == review.unread_scope(view, findings, others)
        assert rest.scope.startswith(
            "the blocks no earlier findings review read as they now stand: "
        )
        assert rest.question.workspace is not None
        assert review.scope_note(rest.scope, judges=False) in (
            rest.question.workspace.instructions
        )
        assert review.text_words(rest) == f"reads {len(others)} blocks"
        capsys.readouterr()
        where = [args[0], "--store", str(store.directory), "--session", "findings"]
        assert (
            cli.main(
                ["review", *where, "--dry-run", "--prompts", str(tmp_path / "dry")]
            )
            == 0
        )
        assert f"  reads {len(others)} blocks ({rest.scope})" in capsys.readouterr().out
        run(store, opened, rest, sheet_with(rest, rf.DONE))
        with pytest.raises(review.NoSubject):
            review.brief(opened, findings, template, None)
        every = review.brief(opened, findings, template, None, select=ALL)
    assert every.read is None and every.scope is None
    assert review.text_words(every) == "reads the whole text"
    # asked for every block, not read first: said as no first read
    assert review.text_words(every, findings, ALL) == "reads the whole text"


def test_a_first_read_is_a_subject_no_reading_was_ever_filed_on() -> None:
    """Held, lapsed, disputed, or unjudged after an inconclusive reading:
    read before. Unjudged with no reading, or with no status at all:
    never."""
    from types import SimpleNamespace

    kind = judgments.StatusKind

    def status(subject: str, k: judgments.StatusKind, reads: tuple = ()):
        return SimpleNamespace(
            aspect=Aspect.TRUE, subject=subject, status=k, reads=reads, concluded=()
        )

    statuses = [
        status("held", kind.HELD),
        status("lapsed", kind.LAPSED),
        status("disputed", kind.DISPUTED),
        status("inconclusive", kind.UNJUDGED, reads=("a reading",)),
        status("unjudged", kind.UNJUDGED),
    ]
    view = SimpleNamespace(readings=SimpleNamespace(statuses=statuses))
    names = [s.subject for s in statuses] + ["unseen"]
    found = SimpleNamespace(
        subjects={n: SimpleNamespace(aspect=Aspect.TRUE, subject=n) for n in names}
    )

    assert review.first_reads(view, found) == 2  # unjudged, unseen


def test_a_session_s_first_read_is_said_with_what_a_later_round_asks(
    tmp_path, capsys
) -> None:
    """The dry run of a round no review of its kind read before says so:
    a session that judges nothing reads the whole text as its first read,
    a later round only the blocks changed since; a session that judges
    counts the subjects no reading was ever filed on."""
    from axiomation import cli

    store, args = built_state(tmp_path, capsys)
    search_all(store)
    where = [args[0], "--store", str(store.directory), "--dry-run"]
    capsys.readouterr()
    assert cli.main(["review", *where, "--session", "findings"]) == 0
    assert (
        "reads the whole text: no earlier findings review read it as it now "
        "stands, so a first read; a later round reads only the blocks changed since"
    ) in capsys.readouterr().out
    with edits.opened(store, versions.latest(store)) as opened:
        view = queries.View(store, opened.state)
        found = review.brief(
            opened, rf.Session.ARGUMENT, load_template("review-argument"), None
        )
    assert review.first_reads(view, found) == len(found.subjects) > 0
    assert cli.main(["review", *where, "--session", "argument"]) == 0
    assert (
        f"  {len(found.subjects)} of the {len(found.subjects)} never read: a first "
        "read; a later round asks one again only where its reading lapses"
    ) in capsys.readouterr().out


def test_a_claim_ruled_positioning_after_a_whole_read_is_read_again(
    tmp_path, capsys
) -> None:
    """The contribution session read every block; a claim then ruled
    `positioning`, which no other session reads, makes no state and
    changes no text: its block is read again, alone, until a check reads
    it."""
    contribution = rf.Session.CONTRIBUTION
    template = load_template("review-contribution")
    store, _ = built_state(tmp_path, capsys)
    search_all(store)
    with edits.opened(store, versions.latest(store)) as opened:
        found = review.brief(opened, contribution, template, None, forager="haiku")
        checked = 'checked "The output is bounded." — bounds — nothing close'
        run(store, opened, found, f"@@ thm:bound\n{checked}\n{rf.DONE}\n")
        with pytest.raises(review.NoSubject):
            review.brief(opened, contribution, template, None, forager="haiku")
        view = queries.View(store, opened.state)
        block = view.resolve("next-the-bound-is")
        rulings.rule(
            store,
            opened.state,
            rulings.subject(judgments.seen(store, opened.state), block),
            RulingKind.POSITIONING,
            "that no earlier bound is sharp",
        )
        again = review.brief(opened, contribution, template, None, forager="haiku")
    assert again.read == (view.node(block),)
    assert again.scope is not None and again.scope.endswith(": next-the-bound-is")
    # a part may hold nothing to check
    assert not rf.read(again.spec, sheet_with(again, rf.DONE)).problems


def test_a_scoped_findings_session_with_nothing_anywhere_is_not_asked(
    tmp_path, capsys
) -> None:
    """Every block read and nothing to settle: no scope is asked, and the
    dry run says so for the session."""
    from axiomation import cli

    findings = rf.Session.FINDINGS
    template = load_template("review-findings")
    store, args = built_state(tmp_path, capsys)
    search_all(store)
    with edits.opened(store, versions.latest(store)) as opened:
        found = review.brief(opened, findings, template, None)
        run(store, opened, found, sheet_with(found, rf.DONE))
        with pytest.raises(review.NoSubject, match="in any scope"):
            review.scoped_briefs(opened, findings, template, None, sections=True)
    capsys.readouterr()
    where = [args[0], "--store", str(store.directory), "--session", "findings"]
    dry = ["--by-section", "--dry-run", "--prompts", str(tmp_path / "dry")]
    assert cli.main(["review", *where, *dry]) != 0
    assert "in any scope" in capsys.readouterr().err


@pytest.mark.usefixtures("develop_mode")
def test_a_kept_settling_answer_is_checked_again_as_the_question_it_answered(
    tmp_path, capsys
) -> None:
    """A findings session's answer that settled its finding alone, refused
    when it was asked, is rebuilt as that settling question under its key,
    and filed: it read no text."""
    from axiomation.questions import Validation
    from axiomation.resolver import Attempt

    findings = rf.Session.FINDINGS
    template = load_template("review-findings")
    store, _ = built_state(tmp_path, capsys)
    search_all(store)
    with edits.opened(store, versions.latest(store)) as opened:
        found = review.brief(opened, findings, template, None)
        run(store, opened, found, sheet_with(found, SEVERE, rf.DONE))
    footnote_retracted(store)
    with edits.opened(store, versions.latest(store)) as opened:
        found = review.brief(opened, findings, template, None)
        assert found.settles
        written = "@@ by-definition-def:gain-the\nfixed f1 — gone\n⟦done⟧\n"
        check = review.checker(opened, found, None, edits.agent("m", None))
        sheet = check(found.question, written).kept
        resolver = Resolver(store, run=held(store))
        refused = Validation(problems=("a check since changed",), kept=sheet)
        resolver.record(
            Attempt(
                found.question,
                "fake",
                "m",
                written,
                0.0,
                0.0,
                refused,
                state=opened.state.id,
            )
        )
        kept = review.kept_answer(store, found.question.key[:12])
        again = review.answer_brief(opened, kept, template, None)
        assert again.settles and list(again.questions) == ["f1"]
        validation = review.checker(opened, again, None, edits.agent("m", None))(
            again.question, kept.raw
        )
        assert validation.valid, validation.problems
        filed = resolver.checked_again(kept, validation).answer
    assert filed.key == found.question.key and filed.read is None
    assert filed.template.name == "review-findings+lapsed"


@pytest.mark.parametrize(
    ("lines", "wanted"),
    [
        ((), "no stands, fixed or reading line on f1"),
        (
            ("stands f1 — still", "fixed f1 — gone"),
            "f1: 2 stands, fixed or reading lines",
        ),
        (("stands f2 — still", "fixed f1 — gone"), "no finding f2 to settle"),
        (('fixed f1 "Hence" — gone',), "a fixed line quotes nothing"),
        (('stands f1 "never written" — still',), "is found 0 times"),
    ],
)
def test_each_lapsed_finding_takes_one_stands_or_fixed_line(
    lapsed, lines, wanted
) -> None:
    _, _, found, _ = lapsed
    problems = rf.read(found.spec, settled(found, *lines)).problems
    assert any(wanted in p for p in problems), problems


def test_stands_refiles_the_finding_live_and_it_stays_a_defect(lapsed) -> None:
    from axiomation import check

    store, opened, found, severe = lapsed
    run(store, opened, found, settled(found, "stands f1 — the footnote is no proof"))
    refiled = [f for f in store.judgment_records().findings if f.reaffirms == severe.id]
    assert len(refiled) == 1
    assert refiled[0].evidence == "the footnote is no proof"
    assert refiled[0].state == opened.state.id
    view = queries.View(store, opened.state)
    defects = check.gates(view).results[1]
    assert defects.gate is check.Gate.DEFECTS
    assert [
        (i.data["finding"], i.data.get("lapsed"))
        for i in defects.items
        if "finding" in i.data
    ] == [(refiled[0].id, None)]


def test_fixed_answers_the_finding_and_clears_it(lapsed) -> None:
    store, opened, found, severe = lapsed
    run(store, opened, found, settled(found, "fixed f1 — the step now cites a lemma"))
    (answer,) = store.judgment_records().answers
    assert (answer.finding, answer.status, answer.note) == (
        severe.id,
        FindingStatus.FIXED,
        "the step now cites a lemma",
    )
    view = queries.View(store, opened.state)
    assert not [f for f in view.readings.findings if f.open]
    (read,) = view.readings.findings
    assert "  lapsed, fixed  " in queries.finding_line(read)
    assert queries.finding_data(view, read)["state"] == "lapsed"


def test_a_person_s_lapsed_finding_is_never_a_session_s_question(
    tmp_path, capsys
) -> None:
    """Its filer settles it: the session's sheet never asks it."""
    store, _ = built_state(tmp_path, capsys)
    search_all(store)
    kept = versions.latest(store)
    at = judgments.seen(store, kept)
    judgments.find(
        store,
        kept,
        Aspect.TRUE,
        [
            judgments.anchored(
                at,
                judgments.AnchorKind.BLOCK,
                queries.View(store, kept).resolve(PROOF_BLOCK),
            )
        ],
        3,
        "the footnote is no proof",
    )
    footnote_retracted(store)
    context, _, found = latest_brief(store)
    context.__exit__(None, None, None)
    assert found.questions == {}


def test_a_findings_session_settles_alone_where_it_read_every_block(
    tmp_path, capsys
) -> None:
    """Its first review reads the whole text. With every block read as it
    now stands and nothing to settle, it is not asked; an edit of the edges
    changes no text, and a finding of its own lapsed there, its sheet asks
    that alone, its template saying so, its question keyed as a settling
    one always was."""
    findings = rf.Session.FINDINGS
    template = load_template("review-findings")
    store, _ = built_state(tmp_path, capsys)
    search_all(store)
    with edits.opened(store, versions.latest(store)) as opened:
        found = review.brief(opened, findings, template, None)
        assert found.read is None and not found.settles and found.scope is None
        run(store, opened, found, sheet_with(found, SEVERE, rf.DONE))
        with pytest.raises(review.NoSubject, match="no finding to settle"):
            review.brief(opened, findings, template, None)
        assert review.brief(opened, findings, template, None, select=ALL).read is None
    footnote_retracted(store)
    context, _, found = latest_brief(store, findings, template)
    context.__exit__(None, None, None)
    assert list(found.questions) == ["f1"] and found.subjects == {}
    assert found.settles and found.read is None
    assert found.question.template.name == "review-findings+lapsed"
    assert found.question.workspace.instructions.endswith(review.SETTLING_NOTE)
    assert not rf.read(
        found.spec, sheet_with(found, "fixed f1 — gone", rf.DONE)
    ).problems


def test_a_findings_session_scoped_settles_where_its_findings_lie(
    tmp_path, capsys
) -> None:
    """With every block read, a session that judges nothing settles its
    lapsed findings by the scope their blocks lie in: a section holding none
    is not asked, and its note names its scope, on the paper plain, with no
    works list."""
    findings = rf.Session.FINDINGS
    template = load_template("review-findings")
    store, _ = built_state(tmp_path, capsys)
    search_all(store)
    with edits.opened(store, versions.latest(store)) as opened:
        found = review.brief(opened, findings, template, None)
        run(store, opened, found, sheet_with(found, SEVERE, rf.DONE))
    footnote_retracted(store)
    with edits.opened(store, versions.latest(store)) as opened:
        scoped = review.scoped_briefs(opened, findings, template, None, sections=True)
        (named,) = review.scoped_briefs(
            opened, findings, template, None, scopes=[[PROOF_BLOCK]]
        ).briefs
    briefs = scoped.briefs
    assert [list(b.questions) for b in briefs] == [["f1"]] and scoped.unasked
    assert list(named.questions) == ["f1"]
    for brief in (*briefs, named):
        assert brief.question.workspace is not None and brief.settles
        instructions = brief.question.workspace.instructions
        note = review.scope_note(brief.scope or "", judges=False)
        assert note in instructions and "works.md" not in note
        assert instructions.endswith(review.SETTLING_NOTE)


def test_a_findings_session_settles_a_lapsed_finding_by_block(tmp_path, capsys) -> None:
    """Settling, the findings session is shown its finding under its block
    and taught each settling line, a disagreement of reading included;
    its `fixed` by block files the answer."""
    findings = rf.Session.FINDINGS
    template = load_template("review-findings")
    store, _ = built_state(tmp_path, capsys)
    search_all(store)
    with edits.opened(store, versions.latest(store)) as opened:
        found = review.brief(opened, findings, template, None)
        run(store, opened, found, sheet_with(found, SEVERE, rf.DONE))
    footnote_retracted(store)
    with edits.opened(store, versions.latest(store)) as opened:
        found = review.brief(opened, findings, template, None)
        assert list(found.questions) == ["f1"]
        workspace = found.question.workspace
        assert workspace is not None
        paper = workspace.files[rf.PAPER_FILE]
        block = paper[paper.index("⟦by-definition-def:gain-the ") :].split("\n\n")[0]
        assert "⟦f1 finding⟧" in block and "↳ filed" not in paper
        assert "reading <finding>" in workspace.instructions
        assert "against a proposal, `reading`" in workspace.instructions
        run(
            store,
            opened,
            found,
            "@@ by-definition-def:gain-the\n"
            "fixed f1 — the proof no longer rests on the footnote\n⟦done⟧\n",
        )
    assert any(
        a.note == "the proof no longer rests on the footnote"
        for a in store.judgment_records().answers
    )


def test_each_cited_work_is_named_as_the_kept_bibliography_names_it(
    monkeypatch,
) -> None:
    """The labels come from the state's kept bibliography, each cited key
    once; a key it holds no entry for has none."""
    from types import SimpleNamespace

    bib = "@article{cd87, author = {Carr, L. and Dunn, J.}, year = 1987}\n"
    monkeypatch.setattr(review.versions, "blob", lambda store, blob: bib.encode())
    view = SimpleNamespace(
        store=None,
        state=SimpleNamespace(files={"refs.bib": "b", "paper.md": "p"}),
        export={"document": {"citations": [{"keys": ["cd87", "none99"]}]}},
    )

    assert review.bib_labels(view) == {  # type: ignore[arg-type]
        "cd87": "Carr and Dunn (1987)",
        "none99": "",
    }


def test_a_scoped_session_takes_a_lapsed_finding_where_its_block_lies(
    lapsed,
) -> None:
    _, opened, _, _ = lapsed
    briefs = review.scoped_briefs(
        opened, ARGUMENT, TEMPLATE, None, sections=True
    ).briefs
    assert [list(b.questions) for b in briefs if b.questions] == [["f1"]]
    view = queries.View(opened.store, opened.state)
    (holding,) = [b for b in briefs if b.questions]
    section = view.within(view.resolve(PROOF_BLOCK))[-1]
    assert section.title in (holding.scope or "")
    (named,) = review.scoped_briefs(
        opened, ARGUMENT, TEMPLATE, None, scopes=[["f1"]]
    ).briefs
    assert list(named.questions) == ["f1"] and not named.subjects


def test_reaffirm_finding_from_the_command_line(lapsed, capsys) -> None:
    from axiomation.cli import main

    store, opened, _, severe = lapsed
    paper = str(Path(opened.store.directory).parent / "paper")
    where = ["--store", str(store.directory)]
    capsys.readouterr()
    said = ["--evidence", "the footnote is no proof", "--person", "Ada"]
    assert main(["reaffirm-finding", paper, *where, severe.id[:14], *said]) == 0
    assert f"reaffirms {severe.id}, severity 3 true" in capsys.readouterr().out
    (refiled,) = [
        f for f in store.judgment_records().findings if f.reaffirms == severe.id
    ]
    assert refiled.evidence == "the footnote is no proof"


# --- an agent's proposal, settled by the review of its kind -----------------------

PROPOSER = Author(AGENT, "model-a", "s1")
PROPOSED_WHY = "the step now cites a lemma"


@pytest.fixture
def proposed(tmp_path, capsys):
    """The argument session's severity-3 finding on the proof, live, that
    an agent proposes `fixed` by hand."""
    store, _ = built_state(tmp_path, capsys)
    search_all(store)
    context, opened, found = opened_brief(store)
    run(store, opened, found, full(found, SEVERE))
    context.__exit__(None, None, None)
    (severe,) = store.judgment_records().findings
    judgments.answer_finding(
        store, severe.id, FindingStatus.FIXED, PROPOSED_WHY, PROPOSER
    )
    context, opened, found = opened_brief(store)
    yield store, opened, found, severe
    context.__exit__(None, None, None)


def _defect_findings(store, state) -> list[str]:
    from axiomation import check

    view = queries.View(store, state)
    (defects,) = [r for r in check.gates(view).results if r.gate is check.Gate.DEFECTS]
    return [i.data["finding"] for i in defects.items if "finding" in i.data]


def test_an_agent_s_proposal_leaves_the_finding_open_on_its_session_s_sheet(
    proposed,
) -> None:
    """Live, the finding is asked again because of the proposal, shown
    beside it as the agent's word; it stays a defect meanwhile."""
    store, opened, found, severe = proposed
    (question,) = found.questions.values()
    assert question.read.finding.id == severe.id
    sheet = found.spec["reference"]
    assert (
        f"  ↳ proposed fixed by agent model-a: {PROPOSED_WHY}" in sheet
        and "a proposal's note is reasoning to weigh against the text, never evidence"
        in sheet
    )
    assert _defect_findings(store, opened.state) == [severe.id]


def test_a_review_s_fixed_line_closes_a_proposed_finding(proposed) -> None:
    store, opened, found, severe = proposed
    assert found.subjects == {}  # each holds a reading, its rule line filed
    run(store, opened, found, sheet_with(found, "fixed f1 — the lemma covers the step"))
    assert _defect_findings(store, opened.state) == []
    view = queries.View(store, opened.state)
    (read,) = view.readings.findings
    assert read.answer.note == "the lemma covers the step" and read.proposal is None


def test_a_review_s_stands_line_turns_down_a_proposal_on_a_live_finding(
    proposed,
) -> None:
    """Refiled at the state with why it stands: the refiling is the open
    defect, with no proposal, and no later sheet asks it."""
    store, opened, found, severe = proposed
    run(
        store,
        opened,
        found,
        sheet_with(found, "stands f1 — the lemma is never applied"),
    )
    (refiled,) = [
        f for f in store.judgment_records().findings if f.reaffirms == severe.id
    ]
    assert refiled.evidence == "the lemma is never applied"
    assert _defect_findings(store, opened.state) == [refiled.id]
    again = review.brief(opened, ARGUMENT, TEMPLATE, None, select=ALL)
    assert again.questions == {}


def test_a_lapsed_finding_s_question_shows_its_proposal(lapsed) -> None:
    store, opened, _, severe = lapsed
    judgments.answer_finding(
        store, severe.id, FindingStatus.FIXED, PROPOSED_WHY, PROPOSER
    )
    found = review.brief(opened, ARGUMENT, TEMPLATE, None)
    sheet = found.spec["reference"]
    assert "  ↳ lapsed at " in sheet and "  ↳ proposed fixed by agent" in sheet


def test_a_finding_a_person_answered_is_never_a_session_s_question(
    lapsed,
) -> None:
    """A person's `postponed` is theirs to settle: a review's `fixed` would
    not speak over it, so no sheet asks it, an agent's proposal after it
    or not; it stays a defect."""
    store, opened, _, severe = lapsed
    judgments.answer_finding(store, severe.id, FindingStatus.POSTPONED)
    judgments.answer_finding(
        store, severe.id, FindingStatus.FIXED, PROPOSED_WHY, PROPOSER
    )
    found = review.brief(opened, ARGUMENT, TEMPLATE, None)
    assert found.questions == {}
    assert _defect_findings(store, opened.state) == [severe.id]
    (read,) = queries.View(store, opened.state).readings.findings
    assert queries.finding_clears(read).startswith("a person's `axiomation answer")


def test_a_fixed_line_on_a_finding_a_person_postponed_meanwhile_is_refused(
    proposed,
) -> None:
    store, opened, found, severe = proposed
    judgments.answer_finding(store, severe.id, FindingStatus.POSTPONED)
    _, problems = review.records(
        queries.View(store, opened.state),
        found,
        rf.read(found.spec, full(found, "fixed f1 — the lemma covers the step")),
        None,
    )
    assert any("answered postponed by a person meanwhile" in p for p in problems)


def test_reaffirm_finding_by_hand_refuses_a_live_proposed_finding(proposed) -> None:
    """Only the review of its kind turns a proposal down by refiling it."""
    store, opened, _, severe = proposed
    with pytest.raises(JudgmentError, match="holds at state"):
        judgments.reaffirm_finding(store, opened.state, severe.id, "it still stands")


def test_a_person_s_finding_an_agent_proposes_answered_is_the_person_s(
    tmp_path, capsys
) -> None:
    """Filed by a person (or by `find`), no review settles it: the
    proposal waits for a person, `answer-finding` says so, and the
    person's answer closes it."""
    from axiomation.cli import main

    store, _ = built_state(tmp_path, capsys)
    search_all(store)
    kept = versions.latest(store)
    at = judgments.seen(store, kept)
    (filed,) = judgments.find(
        store,
        kept,
        Aspect.TRUE,
        [
            judgments.anchored(
                at,
                judgments.AnchorKind.BLOCK,
                queries.View(store, kept).resolve(PROOF_BLOCK),
            )
        ],
        3,
        "the footnote is no proof",
    ).records.findings
    paper = str(Path(store.directory).parent / "paper")
    where = ["--store", str(store.directory)]
    agent = ["--agent", "model-a", "--agent-session", "s1"]
    capsys.readouterr()
    assert (
        main(["answer-finding", paper, *where, filed.id, "--as", "fixed", *agent]) != 0
    )
    assert "say why with --note" in capsys.readouterr().err
    said = ["--note", PROPOSED_WHY]
    assert (
        main(
            ["answer-finding", paper, *where, filed.id, "--as", "fixed", *agent, *said]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert f"finding {filed.id}: proposed fixed by agent model-a: " in out
    assert "stays open" in out and "until a person answers it" in out
    with edits.opened(store, kept) as opened:
        assert review.brief(opened, ARGUMENT, TEMPLATE, None).questions == {}
    assert _defect_findings(store, kept) == [filed.id]
    assert main(["answer-finding", paper, *where, filed.id, "--as", "fixed"]) == 0
    assert f"finding {filed.id} answered fixed: " in capsys.readouterr().out
    assert _defect_findings(store, kept) == []


# --- a person's proposal, settled as an agent's --------------------------------

PERSON_WHY = "reworded: a weight, if only a flat one"


def test_a_person_s_proposal_leaves_the_finding_to_the_review_of_its_kind(
    tmp_path, capsys
) -> None:
    """`answer-finding --propose`: the person's answer speaks for nothing,
    the finding stays a defect, its session's sheet asks it with the
    person's note, and the review's `fixed` line closes it."""
    from axiomation.cli import main

    store, _ = built_state(tmp_path, capsys)
    search_all(store)
    context, opened, found = opened_brief(store)
    run(store, opened, found, full(found, SEVERE))
    context.__exit__(None, None, None)
    (severe,) = store.judgment_records().findings
    paper = str(Path(store.directory).parent / "paper")
    where = ["--store", str(store.directory)]
    capsys.readouterr()
    asked = ["answer-finding", paper, *where, severe.id, "--as", "fixed", "--propose"]
    assert main(asked) != 0
    assert "say why with --note" in capsys.readouterr().err
    assert main([*asked, "--note", PERSON_WHY]) == 0
    out = capsys.readouterr().out
    assert f"finding {severe.id}: proposed fixed by person: " in out
    assert "until the review of its kind settles it" in out
    (answer,) = store.judgment_records().answers
    assert answer.proposed
    assert judgments.speaking_answer(store, severe.id) is None
    assert _defect_findings(store, opened.state) == [severe.id]
    context, opened, found = opened_brief(store)
    sheet = found.spec["reference"]
    assert f"  ↳ proposed fixed by person: {PERSON_WHY}" in sheet
    run(store, opened, found, sheet_with(found, "fixed f1 — the weight is flat"))
    context.__exit__(None, None, None)
    assert _defect_findings(store, opened.state) == []
    (read,) = queries.View(store, opened.state).readings.findings
    assert read.answer.note == "the weight is flat" and read.proposal is None
    with pytest.raises(JudgmentError, match="answered fixed: nothing to propose"):
        judgments.answer_finding(
            store, severe.id, FindingStatus.FIXED, PERSON_WHY, propose=True
        )


def test_a_person_s_answer_speaks_over_their_own_proposal(proposed) -> None:
    store, opened, _, severe = proposed
    judgments.answer_finding(
        store, severe.id, FindingStatus.FIXED, PERSON_WHY, propose=True
    )
    (read,) = queries.View(store, opened.state).readings.findings
    assert read.open and read.proposal.author.person
    judgments.answer_finding(store, severe.id, FindingStatus.DISMISSED, "no gap")
    (read,) = queries.View(store, opened.state).readings.findings
    assert not read.open and read.answer.status is FindingStatus.DISMISSED


def test_a_person_proposes_only_what_a_review_settles(proposed) -> None:
    """A finding a person answered, or one a person filed, is the person's
    own to answer: a proposal there would wait for themselves."""
    store, opened, _, severe = proposed
    judgments.answer_finding(store, severe.id, FindingStatus.POSTPONED)
    with pytest.raises(JudgmentError, match="a person's to settle"):
        judgments.answer_finding(
            store, severe.id, FindingStatus.FIXED, PERSON_WHY, propose=True
        )
    at = judgments.seen(store, opened.state)
    (filed,) = judgments.find(
        store,
        opened.state,
        Aspect.TRUE,
        [
            judgments.anchored(
                at,
                judgments.AnchorKind.BLOCK,
                queries.View(store, opened.state).resolve(PROOF_BLOCK),
            )
        ],
        3,
        "the footnote is no proof",
    ).records.findings
    with pytest.raises(JudgmentError, match="a person's to settle"):
        judgments.answer_finding(
            store, filed.id, FindingStatus.FIXED, PERSON_WHY, propose=True
        )


def test_the_default_asks_a_lapsed_negative_another_s_inconclusive_left(
    tmp_path, capsys
) -> None:
    """Its status is held (`inconclusive`), but `check` lists it as a lapsed
    defect: the default asks it again, its re-ask brief in the hints."""
    store, _ = built_state(tmp_path, capsys)
    search_all(store)
    context, opened, found = opened_brief(store)
    on = found.subjects["s2"].subject
    context.__exit__(None, None, None)
    first = versions.latest(store)
    filed = judgments.judge(
        store,
        first,
        Aspect.TRUE,
        on,
        Verdict.CONTRADICTED,
        Depth.INFORMAL,
        "the footnote is no proof",
        Author(AGENT, "model-a", "s1"),
    ).records.judgments[0]
    second = footnote_retracted(store)
    judgments.judge(
        store,
        second,
        Aspect.TRUE,
        on,
        Verdict.INCONCLUSIVE,
        Depth.INFORMAL,
        "could not tell",
        Author(AGENT, "model-b", "s2"),
    )
    context, _, found = latest_brief(store)
    context.__exit__(None, None, None)
    # beside the subjects no reading ever held
    assert on in [s.subject for s in found.subjects.values()]
    paper = found.question.workspace.files[rf.PAPER_FILE]
    assert f"↳ read before: judgment {filed.id}" in paper


def test_an_about_line_replaces_the_block_s_about_line_onto_its_target() -> None:
    """A review's about line, naming the claim it restates, replaces its
    block's about line onto the same target, whatever it named there: one
    edge per source, kind and target; the block's other about lines stand."""
    written = {
        "b": [
            '→ about t · "So"',
            '→ about u · "So"',
            '→ about file:x.tex where-used · "So"',
        ]
    }
    line = rf.EdgeLine(1, "b", '→ about [common] t · "So heavy states are likely"')
    assert rf.corrected(written, line) == ['→ about t · "So"']
    assert rf.EDGE.match(f"edge b {line.line} — it restates t")


def _restating(tmp_path, onto: str) -> list[rf.SheetSubject]:
    """A claim `r` standing restated, its about edge onto `onto`: the
    definition `d` or the claim `c`; the argument session's subjects."""
    from test_judgments import edge, graph
    from test_versions import V1, keep

    blocks = [
        ("d", "Let the mass of a state be its sum."),
        ("c", "Heavy states are likely."),
        ("r", "So heavy states are likely."),
    ]
    text = " ".join(w for _, w in blocks)
    payload = graph(
        text,
        blocks,
        [edge("r", "about", onto)],
        [("c", blocks[1][1]), ("r", blocks[2][1])],
    )
    for s, status in zip(
        payload["standings"], ("unsupported", "restated"), strict=True
    ):
        s["status"] = status
    for b in payload["blocks"]:
        b["roles"] = ["definition"] if b["id"] == "d" else ["claim"]
    store = RecordStore(tmp_path / "store")
    kept = keep(store, V1, text, payload)
    return [s.sheet for s in review.subjects(queries.View(store, kept), ARGUMENT)]


def test_a_restated_claim_that_restates_no_claim_says_so_on_its_subject(
    tmp_path,
) -> None:
    """Restating the definition, the claim restates nothing: its `true`
    subject says so, for an about line onto the claim it restates; restating
    the claim, it says nothing."""
    (_, nothing) = _restating(tmp_path / "d", "d")
    assert nothing.hops[:1] == (rf.RESTATES_NOTHING,)
    (_, something) = _restating(tmp_path / "c", "c")
    assert rf.RESTATES_NOTHING not in something.hops


def test_an_about_line_is_given_as_the_agent_s_edges_edit(state) -> None:
    """The argument session's about line passes the edges check and
    replaces the closing paragraph's line onto the theorem."""
    store, opened, found = state
    line = (
        'edge a-closing-paragraph-in → about thm:bound · "A closing paragraph in '
        'another file." — the paragraph restates the theorem'
    )
    run(store, opened, found, full(found, line))
    answer = store.authoritative()[found.question.key]
    gave = review.edges_given(
        opened, [(found, answer)], sheets(store), edits.agent("fake/model", "s")
    )
    assert gave.given is not None and gave.given.state is not None
    export = json.loads(versions.kept_export(store, gave.given.state))
    abouts = [
        e
        for e in export["edges"]
        if e["kind"] == "about" and e["target"].get("block") is not None
    ]
    (about,) = [e for e in abouts if e["source"].startswith("extra.tex")]
    assert [x["text"] for x in about["evidence"]] == [
        "A closing paragraph in another file."
    ]


def test_a_uses_line_replaces_its_owner_s_line_onto_the_target_and_widens_it() -> None:
    """A review's uses line onto a target its block uses already replaces
    that line, naming each part it named: it widens what the edge relies
    on and never narrows it; a line naming no part relies on all of T."""
    written = {
        "j": [
            '→ uses t [z] · "shifts by log Z"',
            '→ uses u · "by u"',
            '→ justifies t · "Hence"',
        ]
    }

    def line(text: str) -> rf.EdgeLine:
        return rf.EdgeLine(1, "j", text)

    wider = line('→ uses t [pi, phi, z] · "shifts by log Z" — pi is W/Z')
    assert rf.corrected(written, wider) == ['→ uses t [z] · "shifts by log Z"']
    assert rf.narrowed(wider, rf.corrected(written, wider)) is None
    whole = line('→ uses t · "shifts by log Z" — all of t')
    assert rf.narrowed(whole, rf.corrected(written, whole)) is None
    narrower = line('→ uses t [pi] · "shifts by log Z"')
    assert rf.narrowed(narrower, rf.corrected(written, narrower)) == (
        "j → uses t: names each part j's line onto t named: [z]"
    )
    onto_all = line('→ uses u [x] · "by u"')
    assert "names no part, so the edge relies on all of u" in (
        rf.narrowed(onto_all, rf.corrected(written, onto_all)) or ""
    )
    # a uses line onto a target the block does not use replaces nothing
    assert rf.corrected(written, line('→ uses v · "by v"')) == []


def test_a_uses_line_keeps_the_parts_its_block_s_lines_leave_from() -> None:
    """Uses lines onto one target from disjoint parts stand apart: a
    review's line leaving from a part replaces the line leaving from it,
    never its sibling's; one leaving from the block whole replaces each,
    and is given leaving from all their parts. It leaves from each part the
    line it replaces left from, and a line from the block whole is never
    narrowed to a part."""
    written = {
        "j": [
            '→ uses [pi] t · "pi is W/Z"',
            '→ uses [phi] t · "phi is -log W"',
            '→ uses u · "by u"',
            '→ uses [a] v assumed · "if v"',
        ]
    }

    def line(text: str) -> rf.EdgeLine:
        return rf.EdgeLine(1, "j", text)

    one = line('→ uses [pi] t · "pi is W/Z" — W and Z')
    assert rf.corrected(written, one) == ['→ uses [pi] t · "pi is W/Z"']
    assert rf.narrowed(one, rf.corrected(written, one)) is None
    whole = line('→ uses t · "pi is W/Z"')
    assert rf.corrected(written, whole) == [
        '→ uses [pi] t · "pi is W/Z"',
        '→ uses [phi] t · "phi is -log W"',
    ]
    assert rf.narrowed(whole, rf.corrected(written, whole)) is None
    # given, it leaves from both parts the lines it replaces left from
    assert rf.kept_leaving(whole, rf.corrected(written, whole)).line == (
        '→ uses [pi, phi] t · "pi is W/Z"'
    )
    lone = line('→ uses u [x] · "by u"')
    assert rf.kept_leaving(lone, rf.corrected(written, lone)) == lone
    fewer = line('→ uses [pi] t · "pi or phi"')
    both = rf.EdgeLine(1, "j", '→ uses [pi, phi] t · "pi or phi"')
    assert rf.narrowed(fewer, rf.corrected(written, both)) == (
        "j → uses t: leaves from each part j's line onto t left from: [phi]"
    )
    placed = line('→ uses [x] u · "by u"')
    assert "leaves from j whole: a line leaving from parts would narrow it" in (
        rf.narrowed(placed, rf.corrected(written, placed)) or ""
    )
    # an assumed line's source bracket names claims: it leaves from the
    # block whole, and meets every line onto its target
    assumed = line('→ uses [b] v assumed · "if v"')
    assert rf.corrected(written, assumed) == ['→ uses [a] v assumed · "if v"']


#: The proof's uses line onto the definition, widened from its part to the
#: whole: a second line onto one target, which replaces the first.
WIDER = (
    "edge by-definition-def:gain-the → uses def:gain · "
    '"By Definition~\\ref{def:gain}" — the whole definition, not its ratio alone'
)


def test_a_widening_uses_line_passes_the_gate_and_is_given_once(state) -> None:
    store, opened, found = state
    text = full(found, WIDER)
    assert list(rf.checked(found.spec, text).problems) == []
    run(store, opened, found, text)
    answer = store.authoritative()[found.question.key]
    gave = review.edges_given(
        opened, [(found, answer)], sheets(store), edits.agent("fake/model", "s")
    )
    assert gave.given is not None and gave.given.state is not None
    export = json.loads(versions.kept_export(store, gave.given.state))
    view = queries.View(store, gave.given.state)
    onto = [
        e
        for e in export["edges"]
        if e["kind"] == "uses"
        and e["source"].startswith("main.tex:15")
        and view.target(e)[0] == "def:gain"
    ]
    (edge,) = onto
    assert "part" not in edge["target"]


def test_with_the_uses_rule_off_the_gate_hands_back_the_runner_s_refusal(
    state, monkeypatch
) -> None:
    """Without the replace rule, the widening line is a second edge for one
    source, kind and target, which the edges check refuses: the gate
    refuses it while the session can fix it, in the runner's words
    (docs/architecture.md § 7)."""
    _, opened, found = state
    replaces = rf.corrected

    def without_uses(written, line):
        return [] if " → uses " in f" {line.line}" else replaces(written, line)

    monkeypatch.setattr(rf, "corrected", without_uses)
    text = full(found, WIDER)
    gate = list(rf.checked(found.spec, text).problems)
    assert any("a second edge for one source, kind and target" in p for p in gate)
    assert all(p.startswith(rf.EDGE_LINES) for p in gate)
    check = review.checker(opened, found, None, edits.agent("m", None))
    assert list(check(found.question, text).problems) == gate


def test_the_gate_checks_the_edge_lines_again_only_when_they_change(
    state, tmp_path, monkeypatch, capsys
) -> None:
    _, _, found = state
    spec = tmp_path / "gate.json"
    spec.write_text(json.dumps(found.spec), encoding="utf-8")
    sheet = tmp_path / rf.REVIEW_FILE
    checks: list[int] = []
    edge_lines_problems = rf.edge_lines_problems

    def counted(held, edges):
        checks.append(len(edges))
        return edge_lines_problems(held, edges)

    monkeypatch.setattr(rf, "edge_lines_problems", counted)
    sheet.write_text(sheet_with(found, VALID, WIDER), encoding="utf-8")
    assert rf.main([str(spec), str(sheet)]) == 0
    # a sheet that changes only judge lines checks no edge line again
    sheet.write_text(sheet_with(found, VALID, WIDER, *TRUE_LINES), encoding="utf-8")
    assert rf.main([str(spec), str(sheet)]) == 0
    assert checks == [1]
    capsys.readouterr()
    sheet.write_text(full(found, WIDER, OWN_LINE), encoding="utf-8")
    assert rf.main([str(spec), str(sheet)]) == 0
    assert checks == [1, 2]
    assert capsys.readouterr().out == ""


def test_the_argument_brief_says_a_uses_line_widens_its_owner_s_line(state) -> None:
    _, _, found = state
    assert found.question.workspace is not None
    brief = " ".join(found.question.workspace.instructions.split())
    assert (
        "A uses line onto a target its owner uses already replaces the owner's "
        "line onto it: one edge per source, kind and target."
    ) in brief
    assert "it never narrows the line" in brief


# --- a kept answer filed ------------------------------------------------------


def _refused_answer(tmp_path, capsys, monkeypatch, *extra: str, blocks=False):
    """A review answer the runner's check refused for its edge line: asked
    with the uses rule off, so a widening line was a second edge onto one
    target. Its store, the paper's arguments and the answer's key. With
    `blocks`, the session wrote it by block."""
    store, args = built_state(tmp_path, capsys)
    search_all(store)
    replaces = rf.corrected

    def without_uses(written, line):
        return [] if " → uses " in f" {line.line}" else replaces(written, line)

    monkeypatch.setattr(rf, "corrected", without_uses)
    lines = [VALID, *TRUE_LINES, RULE, FINDING, *(extra or (WIDER,))]
    if blocks:
        context, _, found = opened_brief(store)
        context.__exit__(None, None, None)
        answer = by_block(found, *lines)
    # asked again, the session edits its own sheet: the lines are there
    code, _ = review_cli(
        monkeypatch,
        store,
        args,
        (lambda sheet: answer)
        if blocks
        else (
            lambda sheet: sheet if VALID in sheet else sheet + "\n".join(lines) + "\n"
        ),
    )
    monkeypatch.setattr(rf, "corrected", replaces)
    assert code != 0
    (key,) = {a.key for a in store.answers() if a.kind == "review-argument"}
    assert key not in store.authoritative()
    capsys.readouterr()
    return store, args, key


def file_answer(store, args, key: str, *flags: str) -> int:
    from axiomation import cli

    where = [args[0], "--store", str(store.directory)]
    return cli.main(["review", *where, "--file-answer", key, *flags])


@pytest.mark.usefixtures("develop_mode")
def test_a_kept_answer_today_s_check_passes_is_filed_at_no_cost(
    tmp_path, capsys, monkeypatch
) -> None:
    store, args, key = _refused_answer(tmp_path, capsys, monkeypatch)
    before = versions.latest(store)
    kept = [a for a in store.answers() if a.key == key]
    assert file_answer(store, args, key[:8]) == 0
    out = capsys.readouterr().out
    filed = store.authoritative()[key]
    assert filed.attempt == len(kept) + 1 and filed.checked_again == kept[-1].attempt
    assert (filed.cost, filed.seconds, filed.tokens) == (0.0, 0.0, {})
    assert (filed.raw, filed.model, filed.template) == (
        kept[-1].raw,
        kept[-1].model,
        kept[-1].template,
    )
    held = store.judgment_records()
    assert len(held.judgments) == 5 and len(held.findings) == 1
    assert len(held.rulings) == 1
    assert {j.state for j in held.judgments} == {before.id}
    assert "5 judgments, 1 findings, 1 rulings" in out
    assert "edge lines given: state" in out
    after = versions.latest(store)
    assert after.id != before.id
    # filed once: a valid attempt of it holds
    assert file_answer(store, args, key[:8]) == 1
    assert "is filed already" in capsys.readouterr().err


@pytest.mark.usefixtures("develop_mode")
def test_a_refused_answer_by_block_is_kept_as_its_sheet_and_filed_later(
    tmp_path, capsys, monkeypatch
) -> None:
    """Refused, an answer by block is kept as the sheet it was read as, so
    `--file-answer` checks it again once today's check takes it."""
    store, args, key = _refused_answer(tmp_path, capsys, monkeypatch, blocks=True)
    kept = [
        record_file.text(store.directory, a) for a in store.answers() if a.key == key
    ]
    assert all(text.startswith(rf.SHEET_TITLE) for text in kept)
    assert file_answer(store, args, key[:8]) == 0
    assert record_file.text(store.directory, store.authoritative()[key]) == kept[-1]


@pytest.mark.usefixtures("develop_mode")
def test_a_kept_answer_today_s_check_still_refuses_files_nothing(
    tmp_path, capsys, monkeypatch
) -> None:
    bad = 'edge closing-words-of-the → uses def:gain · "words it does not hold"'
    store, args, key = _refused_answer(tmp_path, capsys, monkeypatch, bad)
    answers = len(store.answers())
    assert file_answer(store, args, key) == 1
    out = capsys.readouterr().out
    assert "refused, nothing filed" in out and "- edge lines: " in out
    assert len(store.answers()) == answers
    assert not store.judgment_records().judgments


@pytest.mark.usefixtures("develop_mode")
def test_a_kept_answer_that_read_an_older_state_is_refused(
    tmp_path, capsys, monkeypatch
) -> None:
    """The proof's uses line onto the footnote retracted since: a new state.
    The answer read the older one, whose text and edges its readings rest
    on, so they are asked again, not filed, whether or not its sheet's one
    hops still stand."""
    store, args, key = _refused_answer(tmp_path, capsys, monkeypatch)
    (kept,) = {a.state for a in store.answers() if a.key == key}
    assert kept == versions.latest(store).id  # the answer names what it read
    footnote_retracted(store)
    assert file_answer(store, args, key) == 1
    err = capsys.readouterr().err
    assert f"read state {kept}, not the current kept state" in err
    assert key not in store.authoritative()


@pytest.mark.usefixtures("develop_mode")
def test_a_kept_answer_whose_subject_was_read_since_is_refused(
    tmp_path, capsys, monkeypatch
) -> None:
    """A reading filed after the answer is the later word: the answer,
    filed now, would speak over it."""
    store, args, key = _refused_answer(tmp_path, capsys, monkeypatch)
    context, _, found = opened_brief(store)
    by_hand(store, found, "s1", Verdict.INCONCLUSIVE)
    context.__exit__(None, None, None)
    assert file_answer(store, args, key) == 1
    assert "the answer's s1 hold a reading filed after it" in capsys.readouterr().err


@pytest.mark.usefixtures("develop_mode")
def test_a_kept_answer_naming_a_subject_the_state_does_not_offer_is_refused(
    state, capsys
) -> None:
    from axiomation.questions import Validation
    from axiomation.resolver import Attempt

    store, opened, found = state
    raw = full(found).replace(
        '⟦s4 true⟧ next-the-bound-is · "Next, the bound is sharp."',
        '⟦s4 true⟧ next-the-bound-is · "Next, the bound is loose."',
    )
    Resolver(store, run=held(store)).record(
        Attempt(
            found.question,
            "fake",
            "m",
            raw,
            0.0,
            0.0,
            Validation(problems=("x",)),
            state=opened.state.id,
        )
    )
    with pytest.raises(review.ReviewError, match="the answer's s4 is no subject"):
        review.answer_brief(
            opened, review.kept_answer(store, found.question.key), TEMPLATE, None
        )
    with pytest.raises(review.ReviewError, match="names 0 answers"):
        review.kept_answer(store, "ffffffff")


# --- an objection shows its case: the person's side ---------------------------------

A_PERSON = Author()
READING_LINE = (
    "reading f1 — the review reads the lemma as cited and never applied; the "
    "note reads its citation as the step, and no case settles which"
)


def test_a_reading_line_hands_a_proposed_finding_back_to_the_person(proposed) -> None:
    """The review weighs the agent's proposal and finds the disagreement
    one of reading: the finding is refiled as such, no later sheet asks
    it, it still counts as a defect, a person cannot propose on it, and
    the person's own answer closes it."""
    store, opened, found, severe = proposed
    sheet = found.spec["reference"]
    assert rf.READING_FORM in sheet
    run(store, opened, found, sheet_with(found, READING_LINE))
    (refiled,) = [
        f for f in store.judgment_records().findings if f.reaffirms == severe.id
    ]
    assert refiled.reading and refiled.evidence == READING_LINE.split(" — ", 1)[1]
    assert review.brief(opened, ARGUMENT, TEMPLATE, None, select=ALL).questions == {}
    assert _defect_findings(store, opened.state) == [refiled.id]
    with pytest.raises(JudgmentError, match="disagreement of reading"):
        judgments.answer_finding(
            store, refiled.id, FindingStatus.DISMISSED, "it is cited", propose=True
        )
    judgments.answer_finding(store, refiled.id, FindingStatus.DISMISSED, "it is cited")
    assert _defect_findings(store, opened.state) == []


def test_a_reading_line_answers_a_proposal_only(lapsed) -> None:
    """A lapsed finding carrying no proposal has no reading to differ from:
    `stands` or `fixed`."""
    store, opened, found, severe = lapsed
    said = problems(found, sheet_with(found, READING_LINE))
    assert any("carries no proposal" in p for p in said), said


def test_a_refiled_finding_shows_why_it_stands_and_the_proposal_it_weighed(
    proposed,
) -> None:
    """Turned down by a `stands` line: the refiling's line opens with its
    reason now, its problem as first filed behind it; the filing it
    replaced keeps the proposal its refiling weighed."""
    from axiomation import inbox

    store, opened, found, severe = proposed
    run(
        store,
        opened,
        found,
        sheet_with(found, "stands f1 — the lemma is never applied"),
    )
    view = queries.View(store, opened.state)
    by = {f.finding.id: f for f in view.readings.findings}
    old = by[severe.id]
    assert old.proposal is None and old.weighed is not None
    assert old.weighed.answer.note == PROPOSED_WHY
    (refiled,) = [f for f in by.values() if f.finding.reaffirms == severe.id]
    line = queries.finding_line(refiled)
    assert line.index('stands: "the lemma is never applied"') < line.index(
        "as first filed:"
    )
    assert refiled.turned_down == old.weighed
    assert f"{PROPOSED_WHY} — not taken" in line
    assert queries.finding_data(view, refiled)["turned_down"]["note"] == PROPOSED_WHY
    (item,) = inbox.inbox(view).sections[inbox.Section.STOOD_AGAIN.value]
    assert item.line.count(PROPOSED_WHY) == 1


def proposed_sharp(store, found, verdict=Verdict.VERIFIED) -> None:
    """The person's proposed reading of the sharp claim at the latest
    state."""
    s4 = found.subjects["s4"]
    judgments.judge(
        store,
        versions.latest(store),
        s4.aspect,
        s4.subject,
        verdict,
        Depth.INFORMAL,
        "the example after it shows the bound is attained",
        A_PERSON,
        propose=True,
    )


def test_a_person_s_proposed_reading_asks_the_subject_again_once(state) -> None:
    """The review holds the sharp claim not-shown; the person proposes it
    verified. The proposal changes no status, the default asks the
    subject again with it under its line, and another agent's
    reading that concludes settles it, superseding the not-shown."""
    from axiomation import inbox

    store, opened, found = state
    run(store, opened, found, full(found))
    proposed_sharp(store, found)
    status = sharp_status(store)
    assert status.held is not None
    assert status.held.judgment.verdict is Verdict.NOT_SHOWN
    assert status.proposal is not None and status.proposal.asks
    view = queries.View(store, versions.latest(store))
    assert review.round_answers(view, ARGUMENT) == [
        status.proposal.read.judgment.answer
    ]
    sections = inbox.inbox(view).sections
    assert inbox.Section.READ_NEGATIVE.value not in sections
    (item,) = sections[inbox.Section.AWAITING_REVIEW.value]
    assert "proposed verified by person" in item.line
    sharp = [lines_of(found)[3]]
    again = asked_now(store)
    assert lines_of(again) == sharp
    paper = again.question.workspace.files[rf.PAPER_FILE]
    (proposal,) = [x for x in paper.splitlines() if x.startswith("↳ proposed")]
    assert "proposed verified by person" in proposal
    assert "the example after it shows the bound is attained" in proposal
    by_hand(store, found, "s4", Verdict.VERIFIED, model="m-b")
    status = sharp_status(store)
    assert status.status is judgments.StatusKind.HELD
    assert status.held.judgment.verdict is Verdict.VERIFIED
    assert status.proposal.reask is not None and not status.proposal.asks
    assert "weighed by agent m-b" in queries.status_line(view, status)
    with pytest.raises(review.NoSubject):
        asked_now(store)


def test_a_proposed_reading_s_inconclusive_re_ask_leaves_the_negative(state) -> None:
    """The re-ask could not tell: the not-shown still holds, the decision
    the person's, and the proposal asks nothing again."""
    store, opened, found = state
    run(store, opened, found, full(found))
    proposed_sharp(store, found)
    by_hand(store, found, "s4", Verdict.INCONCLUSIVE, model="m-b")
    status = sharp_status(store)
    assert status.held.judgment.verdict is Verdict.NOT_SHOWN
    assert not status.proposal.asks
    with pytest.raises(review.NoSubject):
        asked_now(store)


def test_only_a_person_proposes_a_reading(state) -> None:
    store, opened, found = state
    s4 = found.subjects["s4"]
    with pytest.raises(JudgmentError, match="a person's"):
        judgments.judge(
            store,
            versions.latest(store),
            s4.aspect,
            s4.subject,
            Verdict.VERIFIED,
            Depth.INFORMAL,
            "by hand",
            Author(AGENT, "m-a", "s"),
            propose=True,
        )


def test_a_ruling_under_an_older_question_kept_verbatim(state) -> None:
    """The sheet shows the old `interpretation` as a candidate; `keep`
    reinstates it under the current question, naming it, its kind and
    reason carried."""
    store, opened, found = state
    run_before(store, opened, found, full(found).replace(RULE, INTERPRETED))
    (old,) = store.judgment_records().rulings
    context, reopened, again = opened_brief(store)
    try:
        run(
            store,
            reopened,
            again,
            sheet_with(again, TRUE_LINES[2].replace("s4", "s1"), "keep s1"),
        )
    finally:
        context.__exit__(None, None, None)
    kept = [r for r in store.judgment_records().rulings if r.id != old.id]
    assert [(r.kind, r.reason, r.reaffirms) for r in kept] == [
        (old.kind, old.reason, old.id)
    ]
    with pytest.raises(review.NoSubject):
        asked_now(store)
    view = queries.View(store, versions.latest(store))
    (ruled,) = [r for r in view.rulings.values() if r.speaks]
    assert f"kept from {old.id}" in queries.ruling_line(ruled.speaks[-1], ruled)


def test_a_keep_line_needs_a_candidate_that_holds_verbatim(state) -> None:
    """`keep` only where the sheet shows a candidate, and not where its
    reason quotes words the paper no longer holds."""
    import copy

    store, opened, found = state
    said = problems(found, full(found).replace(RULE, "keep s4"))
    assert any("no ruling under an older question" in p for p in said), said
    run_before(store, opened, found, full(found).replace(RULE, INTERPRETED))
    again = asked_now(store)
    spec = copy.deepcopy(again.spec)
    spec["subjects"]["s1"]["candidate"]["reason"] = 'the text says "no such words"'
    line = TRUE_LINES[2].replace("s4", "s1")
    edited = sheet_with(again, line, "keep s1")
    said = rf.read(spec, edited).problems
    assert any("no block of the paper holds now" in p for p in said), said
    spec["subjects"]["s1"]["candidate"].update(kind="left-unargued", reason="short")
    said = rf.read(spec, edited).problems
    assert any("never an agent's to keep" in p for p in said), said
    # an owed one carries no severity or fix; nor is a candidate kept where
    # a person proposes a ruling
    spec["subjects"]["s1"]["candidate"].update(kind="owed", reason="its own")
    said = rf.read(spec, edited).problems
    assert any("ruled again with the severity" in p for p in said), said
    spec = copy.deepcopy(again.spec)
    spec["subjects"]["s1"]["proposal"] = True
    said = rf.read(spec, edited).problems
    assert any("carries a person's proposed ruling" in p for p in said), said
    assert not rf.read(again.spec, edited).problems


def test_a_kept_ruling_s_quote_holds_in_another_case(state) -> None:
    """A candidate's reason that quotes a block's words in another case (a
    heading's words written in lower case) quotes words the paper holds:
    `keep` takes it. Words the paper holds in no case are still gone."""
    import copy

    store, opened, found = state
    run_before(store, opened, found, full(found).replace(RULE, INTERPRETED))
    again = asked_now(store)
    spec = copy.deepcopy(again.spec)
    edited = sheet_with(again, TRUE_LINES[2].replace("s4", "s1"), "keep s1")
    spec["subjects"]["s1"]["candidate"]["reason"] = (
        'it glosses "next, the bound is sharp"'
    )
    assert not rf.read(spec, edited).problems
    spec["subjects"]["s1"]["candidate"]["reason"] = (
        'it glosses "next, the bound is tight"'
    )
    said = rf.read(spec, edited).problems
    assert any("no block of the paper holds now" in p for p in said), said


def test_a_reading_finding_is_the_person_s_lapsed_or_proposed_on(proposed) -> None:
    """Handed back, a finding is never a review's again: not when an agent
    proposes on it by hand, and not when its anchors' words change."""
    store, opened, found, severe = proposed
    run(store, opened, found, sheet_with(found, READING_LINE))
    (refiled,) = [
        f for f in store.judgment_records().findings if f.reaffirms == severe.id
    ]
    with pytest.raises(JudgmentError, match="never a proposal"):
        judgments.answer_finding(
            store, refiled.id, FindingStatus.FIXED, "now cited", PROPOSER
        )
    footnote_retracted(store)
    context, reopened, again = opened_brief(store)
    try:
        assert again.questions == {}
    finally:
        context.__exit__(None, None, None)


def test_a_proposal_is_no_reading_to_re_ask_or_adopt(state) -> None:
    store, opened, found = state
    run(store, opened, found, full(found))
    proposed_sharp(store, found)
    view = queries.View(store, versions.latest(store))
    proposal = sharp_status(store).proposal.read.judgment
    with pytest.raises(queries.QueryError, match="proposed reading"):
        queries.reask_report(view, proposal.id)
    with pytest.raises(JudgmentError, match="proposed reading"):
        judgments.adopt(store, versions.latest(store), proposal.id)


def test_a_person_proposes_no_reading_their_own_decides(state) -> None:
    """A person's own reading decides and is never asked again: a proposal
    on it would ask a round for nothing."""
    store, opened, found = state
    run(store, opened, found, full(found))
    s4 = found.subjects["s4"]
    judgments.judge(
        store,
        versions.latest(store),
        s4.aspect,
        s4.subject,
        Verdict.NOT_SHOWN,
        Depth.INFORMAL,
        "my own reading",
        A_PERSON,
    )
    with pytest.raises(JudgmentError, match="a person's reading decides it"):
        proposed_sharp(store, found)


def test_a_proposal_s_re_ask_supersedes_only_the_agents_negatives_before_it(
    state,
) -> None:
    """The re-ask concludes verified: the review's not-shown, filed before
    the proposal, speaks no more; another agent's not-shown filed after
    the proposal still speaks, so the subject is disputed."""
    store, opened, found = state
    run(store, opened, found, full(found))
    proposed_sharp(store, found)
    by_hand(store, found, "s4", Verdict.VERIFIED, model="m-b")
    by_hand(store, found, "s4", Verdict.NOT_SHOWN, model="m-c")
    status = sharp_status(store)
    assert {r.author.name for r in status.reads} == {"agent m-b", "agent m-c"}
    assert status.status is judgments.StatusKind.DISPUTED


def test_a_block_no_review_judges_says_so_and_when_a_review_last_read_it(
    state,
) -> None:
    """`def:gain` holds no claim and is no proof: `show` says what a review
    does with it, first that none read it, then the argument session whose
    proof reading rests on it, then a findings session that read the paper
    whole. A claim's block says nothing of the kind."""
    store, opened, found = state

    def no_claim(handle: str):
        view = queries.View(store, opened.state)
        report = queries.show(view, handle)
        return report.data.get("no_claim"), queries.render(report)

    data, shown = no_claim("def:gain")
    assert data["roles"] == ["definition"] and data["last_read"] is None
    assert (
        "a definition: no claim to judge; a review files a finding on it only "
        "when it is ill-posed (none filed)" in shown
    )
    assert "last read by a review: none; no review reading rests on it" in shown

    run(store, opened, found, full(found))
    data, shown = no_claim("def:gain")
    read = data["last_read"]
    # the proof's `valid` and its closing line's `true`, both using def:gain
    resting = [
        j.id
        for j in store.judgment_records().judgments
        if (j.aspect, j.subject.words)
        in {
            (Aspect.VALID, "The output is bounded."),
            (Aspect.TRUE, "Hence the output is bounded."),
        }
    ]
    assert (read["session"], read["state"], read["readings"]) == (
        "argument",
        opened.state.id,
        resting,
    )
    assert not read["changed_since"]
    assert (
        f"the argument session, 2 of whose readings rest on it ({', '.join(resting)})"
        in shown
    )
    assert no_claim("thm:bound")[0] is None
    assert no_claim("next-the-bound-is")[0] is None, "a claim, though no standing"
    # the footnote is the proof group's second block: the group's readings
    # judge it, shown on the group's first block
    data, shown = no_claim("small-means-below-one")
    assert (data["judged_with"], data["last_read"]) == (PROOF, None)
    assert (
        f"a definition in {PROOF}, which a review judges as one: its readings "
        "are the group's" in shown
    )

    findings = review.brief(
        opened, rf.Session.FINDINGS, load_template("review-findings"), None
    )
    # stamped with the state its brief read, as `review` runs it
    host = FakeHost(
        lambda p: "", edits={rf.REVIEW_FILE: lambda _: sheet_with(findings, rf.DONE)}
    )
    Runner(
        host, Resolver(store, run=held(store)), 10.0, FLAT, review_state=opened.state.id
    ).run(
        [findings.question],
        review.checker(opened, findings, None, edits.agent(host.model, None)),
    )
    read = no_claim("def:gain")[0]["last_read"]
    assert (read["session"], read["readings"]) == ("findings", [])
    assert read["words"].endswith("the findings session, reading the paper whole")


# --- an owed ruling's weight, a settled finding on its words, a proposal -----------


def test_an_owed_rule_line_files_its_severity_and_fix(state) -> None:
    store, opened, found = state
    sheet = found.spec["reference"]
    assert "rule <subject> owed <severity> [pages KEY:N, …] — <reason> — <fix>" in sheet
    run(store, opened, found, full(found))
    (r,) = store.judgment_records().rulings
    assert (r.kind, r.severity) == (rf.RulingKind.OWED, 3)
    assert r.fix == "give the extremal case, or drop sharp"
    assert r.reason.endswith("a case where the bound is attained")


def test_an_owed_rule_line_beside_a_settled_finding_names_it(state) -> None:
    """The review's finding on the sharp claim's words is dismissed by a
    person: the sheet shows the answer under the subject, and an owed rule
    line that does not name it is refused; one naming it is filed."""
    store, opened, found = state
    run(store, opened, found, full(found, FINDING))
    (finding,) = store.judgment_records().findings
    judgments.answer_finding(
        store, finding.id, FindingStatus.DISMISSED, "the example shows it sharp"
    )
    context, reopened, again = opened_brief(store)
    try:
        (sid,) = [s for s, x in again.subjects.items() if x.sheet.rules]
        sheet = again.spec["reference"]
        name = finding.id[:12]
        assert (
            f'↳ finding {name} on these words, answered dismissed by person: "the '
            'example shows it sharp"'
        ) in sheet
        judge = f"judge {sid} not-shown informal — nothing argues it"
        bare = RULE.replace("s4", sid, 1)
        said = problems(again, sheet_with(again, judge, bare))
        assert any(f"beside finding {name}" in p for p in said), said
        named = bare.replace(" — give", f"; {name}'s example is not attainment — give")
        assert problems(again, sheet_with(again, judge, named)) == []
    finally:
        context.__exit__(None, None, None)


def test_a_person_s_proposed_ruling_is_asked_by_the_next_review(state) -> None:
    """A person proposes `interpretation` over the review's owed: the claim,
    held back by the ruling that speaks, is asked again with the proposal
    under it; the review's rule line settles it, and it asks no more."""
    store, opened, found = state
    run(store, opened, found, full(found))
    with pytest.raises(review.NoSubject):
        asked_now(store)
    s4 = found.subjects["s4"].subject
    rulings.rule(
        store,
        versions.latest(store),
        s4,
        rulings.RulingKind.INTERPRETATION,
        "a gloss of the theorem, which nothing rests on",
        propose=True,
    )
    view = queries.View(store, versions.latest(store))
    assert review.round_answers(view, ARGUMENT)
    context, reopened, again = opened_brief(store)
    try:
        assert lines_of(again) == [lines_of(found)[3]]
        sheet = again.spec["reference"]
        assert (
            '↳ proposed by person: interpretation — "a gloss of the theorem, which '
            'nothing rests on"'
        ) in sheet
        run(
            store,
            reopened,
            again,
            sheet_with(
                again,
                "judge s1 not-shown informal — nothing argues it",
                RULE.replace("s4", "s1", 1).replace(
                    " — give", "; the proposal misses that the bound is stated — give"
                ),
            ),
        )
    finally:
        context.__exit__(None, None, None)
    (ruled,) = queries.View(store, versions.latest(store)).rulings.values()
    assert ruled.proposal is not None and not ruled.proposal.asks
    with pytest.raises(review.NoSubject):
        asked_now(store)


def test_an_owed_rule_line_beside_a_finding_settled_fixed_on_the_sheet_names_it() -> (
    None
):
    """A finding the sheet settles `fixed` on the claim's words, and an owed
    rule line beside it: refused unless it names the finding; a `stands`
    line contradicts nothing."""
    subjects = {"s2": {"rules": True, "settling": ["f1"]}}
    owed = rf.RuleLine(9, "s2", rf.RulingKind.OWED, "not argued", severity=2, fix="x")
    fixed = rf.SettleLine(8, "f1", rf.Settle.FIXED, None, "reworded")
    stands = rf.SettleLine(8, "f1", rf.Settle.STANDS, None, "still")
    (said,) = rf._unanswered_settlings(subjects, [owed], [fixed])
    assert "s2 is ruled owed beside finding f1" in said
    named = replace(owed, reason="f1's rewording keeps the claim, still not argued")
    assert rf._unanswered_settlings(subjects, [named], [fixed]) == []
    assert rf._unanswered_settlings(subjects, [owed], [stands]) == []
    # `f10` is not `f1`
    other = replace(owed, reason="as f10 says")
    assert rf._unanswered_settlings(subjects, [other], [fixed]) != []


def test_a_proposal_over_an_agent_s_positioning_is_asked(state) -> None:
    """An agent's `positioning` takes the sharp claim out of the argument
    session; a person's proposal against it brings it back."""
    store, opened, found = state
    positioning = "rule s4 positioning — a claim that no other work has the bound"
    run(store, opened, found, sheet_with(found, VALID, *TRUE_LINES, positioning))
    with pytest.raises(review.NoSubject):
        asked_now(store)
    rulings.rule(
        store,
        versions.latest(store),
        found.subjects["s4"].subject,
        rulings.RulingKind.OWED,
        "the bound's sharpness is the paper's own claim, argued nowhere",
        severity=3,
        fix="give the extremal case",
        propose=True,
    )
    assert lines_of(asked_now(store)) == [lines_of(found)[3]]


def test_a_round_s_own_settling_answers_keep_its_id(state) -> None:
    """A review round's `fixed` on a finding on the sharp claim's words is
    no answer that moves a round; a proposed ruling stays in it once
    settled, so the round that settles it keeps its id."""
    store, opened, found = state
    run(store, opened, found, full(found, FINDING))
    (finding,) = store.judgment_records().findings
    view = queries.View(store, versions.latest(store))
    assert review.round_answers(view, ARGUMENT) == []
    # a review round's own `fixed`, which its sheet asked as a question
    template = store.answers()[-1]
    key = "f" * 64
    records = JudgmentRecords(
        answers=(
            FindingAnswer(
                f"{key[:12]}-1-1", key, finding.id, FindingStatus.FIXED, "reworded"
            ),
        )
    )
    store.append(
        replace(template, key=key, review_round="r1", records=records.ids),
        judgments=records,
    )
    view = queries.View(store, versions.latest(store))
    assert [f.answer.status for f in view.readings.findings] == [FindingStatus.FIXED]
    assert review.round_answers(view, ARGUMENT) == []
    rulings.rule(
        store,
        versions.latest(store),
        found.subjects["s4"].subject,
        rulings.RulingKind.KNOWN,
        "a textbook bound",
        propose=True,
    )
    view = queries.View(store, versions.latest(store))
    (ruled,) = [r for r in view.rulings.values() if r.proposal is not None]
    before = review.round_answers(view, ARGUMENT)
    assert ruled.proposal.read.ruling.answer in before
    ruled_after(store, found)
    view = queries.View(store, versions.latest(store))
    assert ruled.proposal.read.ruling.answer in review.round_answers(view, ARGUMENT)


def test_a_rules_subject_shows_what_relies_on_its_claim() -> None:
    s = rf.SheetSubject("s2", Aspect.TRUE, 'b · "c"', rules=True, relied=("u", "v"))
    assert "  ↳ relied on by u, v" in s.lines()


def test_a_work_not_read_is_named_with_the_read_s_words() -> None:
    from axiomation.commands.review import _unread_words

    absent = evidence.Results.failed(ReadStatus.ABSENT, "no source holds able1998")
    assert _unread_words("able1998", absent) == (
        "  able1998 not read: no source holds able1998"
    )
    assert _unread_words("able1998", evidence.Results(ReadStatus.ABSENT)) == (
        "  able1998 not read: absent"
    )


def test_the_dry_run_names_the_bibliography_its_tools_read() -> None:
    """Beside the works laid in, the dry run counts the bibliography a
    session's tools read on demand; nothing where they read none."""
    from axiomation.commands.review import _bibliography_words

    spec = {"entries": ["able1998", "carter2001"]}
    assert _bibliography_words(spec) == ", its tools read the bibliography's 2 works"
    assert _bibliography_words({}) == ""
