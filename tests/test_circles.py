"""The circles of what claims rest on (`docs/model.md` § 9): found from the
graph alone, each line with the ends that may name less than the text
relies on, ranked by the claims held back, keyed on their lines' ends and
their claims' standings, and broken only by an end named.

Synthetic one-line papers; each claim stands unsupported or restated, an
unsupported one held true by a person, so only what it rests on decides
it. No model is called.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest

from axiomation import check, edits, queries, versions
from axiomation import circles as circles_
from axiomation.changes import Layer
from axiomation.cli import main
from axiomation.established import Circle, Coarse, Missing, settled
from axiomation.judgments import readings
from axiomation.marks import open_marks
from axiomation.queries import OpenKind, View
from axiomation.questions import Stage, edges_file
from axiomation.queue import Run, Served, Spool, opened
from axiomation.records import Aspect, RecordStore, Verdict
from axiomation.resolver import Resolver
from axiomation.runner import Runner
from axiomation.runner.queued import Price
from runner.fakes import FakeHost, FakeQueueHost, working_through
from test_check import PERSON, judged
from test_judgments import edge, graph, naming
from test_marks import NARROWED, given, kept_update
from test_profiles import _profile
from test_rereads import SINGLE
from test_update import (
    CHAIN,
    CHAIN_LEGEND,
    DEFINITION,
    EDGES_FILE,
    EDITED_SETTING,
    FLAT,
    SETTING,
    SHARP,
    QueueThen,
    answering,
    make_paper,
)
from test_update import versions as two_papers
from test_update_single import as_assumption
from test_versions import V1, keep


def kept_state(tmp_path, blocks, edges, standings, roles=None, entries=()):
    """`standings` as (block, words, status); each unsupported claim held
    true by a person; `roles` per block where it has any."""
    text = " ".join(w for _, w in blocks)
    data = graph(text, blocks, edges, [s[:2] for s in standings])
    for s, (_, _, status) in zip(data["standings"], standings, strict=True):
        s["status"] = status
    for b in data["blocks"]:
        b["roles"] = (roles or {}).get(b["id"], ["claim"])
    data["entries"] = list(entries)
    store = RecordStore(tmp_path / "store")
    kept = keep(store, V1, text, data)
    for block, words, status in standings:
        if status == "unsupported":
            judged(
                store, kept, Aspect.TRUE, block, Verdict.VERIFIED, PERSON, claim=words
            )
    return store, kept


def circles(store, kept) -> tuple[Circle, ...]:
    return settled(readings(store, kept), open_marks(store, kept))[1]


def handles(kept, circle: Circle) -> list[str]:
    by_node = {n.node: n.block for n in kept.nodes}
    return [by_node[c.node] for c in circle.claims]


# a lemma that uses a block defining g and deriving a bound from the lemma
WHOLE = [
    ("l", "Then f is small."),
    ("d", "Let g be the gain. So f is smaller."),
    ("u", "Hence f is tiny."),
]
WHOLE_STANDINGS = [
    ("l", "Then f is small.", "unsupported"),
    ("d", "So f is smaller.", "unsupported"),
    ("u", "Hence f is tiny.", "unsupported"),
]
WHOLE_ROLES = {"d": ["definition", "claim"]}
GAIN = {"id": "d [g]", "anchor": "g", "role": "definition"}


def test_a_line_onto_a_whole_block_holding_a_definition_closes_a_circle(
    tmp_path,
) -> None:
    """l uses d whole, where it needs only d's definition of g, and d's
    claim uses l: one circle of two claims, holding back the claim that
    rests on it too, its line onto d a `whole` candidate."""
    edges = [edge("l", "uses", "d"), edge("d", "uses", "l"), edge("u", "uses", "l")]
    store, kept = kept_state(tmp_path, WHOLE, edges, WHOLE_STANDINGS, WHOLE_ROLES)
    (circle,) = circles(store, kept)
    assert handles(kept, circle) == ["l", "d"]
    assert [len(circle.held_back), circle.coarse] == [3, True]
    coarse = {
        (line.step.source, line.step.target): line.coarse for line in circle.lines
    }
    assert coarse == {
        (kept.node_of("l"), kept.node_of("d")): (Coarse.WHOLE,),
        (kept.node_of("d"), kept.node_of("l")): (),
    }
    found = settled(readings(store, kept), open_marks(store, kept))[0]
    reasons = {e.claim.words: e.reason for e in found}
    assert {r.kind for r in reasons.values() if r} == {Missing.CIRCULAR}
    assert {r.circle for r in reasons.values() if r} == {circle.key}
    assert f"(circle {circle.key[:8]})" in reasons["Hence f is tiny."].why


def test_naming_the_definition_part_breaks_the_circle(tmp_path) -> None:
    """l names d's part `g`, which holds no claim: it rests on the
    definition alone, the circle is gone and every claim is established."""
    edges = [
        edge("l", "uses", "d", "d [g]"),
        edge("d", "uses", "l"),
        edge("u", "uses", "l"),
    ]
    store, kept = kept_state(
        tmp_path, WHOLE, edges, WHOLE_STANDINGS, WHOLE_ROLES, [GAIN]
    )
    assert circles(store, kept) == ()
    found = settled(readings(store, kept), open_marks(store, kept))[0]
    assert all(e.established for e in found)


# a restatement about a block of three claims, which points back at it
THREE = [
    ("s", "The aggregate need not be Markov."),
    ("h", "Prop P is about objects. Its energy can rise. The account is a rule."),
    ("p", "Prop P holds."),
]
THREE_STANDINGS = [
    ("s", "The aggregate need not be Markov.", "restated"),
    ("h", "Prop P is about objects.", "restated"),
    ("h", "Its energy can rise.", "unsupported"),
    ("h", "The account is a rule.", "unsupported"),
    ("p", "Prop P holds.", "unsupported"),
]


def three_lines(
    forward: dict[str, Any] | None = None, back: dict[str, Any] | None = None
):
    own = {**edge("h", "about", "p"), "source_claims": ["Prop P is about objects."]}
    return [
        forward or edge("s", "about", "h"),
        own,
        back or edge("h", "about", "s"),
    ]


def test_two_about_lines_through_ends_naming_no_claim_close_a_circle(
    tmp_path,
) -> None:
    """s names no claim of h's three, so it restates each, h's restatement
    among them; h's line back names none of its claims at its source end,
    so it is read as possibly that restatement's: a circle, each line's
    end a candidate."""
    store, kept = kept_state(tmp_path, THREE, three_lines(), THREE_STANDINGS)
    (circle,) = circles(store, kept)
    assert [c.words for c in circle.claims] == [
        "The aggregate need not be Markov.",
        "Prop P is about objects.",
    ]
    assert sorted(line.coarse for line in circle.lines) == [
        (Coarse.UNNAMED_SOURCE, Coarse.SIBLING),
        (Coarse.UNNAMED_TARGET, Coarse.SIBLING),
    ]


def test_naming_the_claim_restated_breaks_the_circle(tmp_path) -> None:
    forward = naming(edge("s", "about", "h"), "Its energy can rise.")
    store, kept = kept_state(tmp_path, THREE, three_lines(forward), THREE_STANDINGS)
    assert circles(store, kept) == ()


def test_a_line_that_restates_none_breaks_the_circle(tmp_path) -> None:
    """h's line back says it restates none of h's claims (`[-]`): it is no
    fallback for h's restatement, which rests on p alone."""
    back = {**edge("h", "about", "s"), "restates_none": True}
    store, kept = kept_state(tmp_path, THREE, three_lines(back=back), THREE_STANDINGS)
    assert circles(store, kept) == ()
    found = settled(readings(store, kept), open_marks(store, kept))[0]
    (stmt,) = [e for e in found if e.claim.words == "Prop P is about objects."]
    assert stmt.established, stmt.reason


def test_two_restatements_of_each_other_are_siblings(tmp_path) -> None:
    """x and y each restate the other, every end named: the circle shows
    each line as restating a restated claim, and no original."""
    blocks = [("x", "So f is bounded."), ("y", "Thus f is bounded.")]
    standings = [
        ("x", "So f is bounded.", "restated"),
        ("y", "Thus f is bounded.", "restated"),
    ]
    edges = [edge("x", "about", "y"), edge("y", "about", "x")]
    store, kept = kept_state(tmp_path, blocks, edges, standings)
    (circle,) = circles(store, kept)
    assert [line.coarse for line in circle.lines] == [(Coarse.SIBLING,)] * 2


def test_a_circle_through_a_way_not_needed_holds_none_back_and_ranks_last(
    tmp_path,
) -> None:
    """t is justified by p, whose step uses l, which uses t; and by q,
    which uses nothing. t holds by q, so the circle through p holds
    nothing back: listed after the one that does, its line named by its
    way."""
    blocks = [
        *WHOLE,
        ("t", "Then f is even."),
        ("p", "By the lemma, f is even."),
        ("q", "Plainly, f is even."),
        ("k", "Then f is odd."),
    ]
    standings = [
        *WHOLE_STANDINGS,
        ("t", "Then f is even.", "justified"),
        ("k", "Then f is odd.", "unsupported"),
    ]
    edges = [
        edge("l", "uses", "d"),
        edge("d", "uses", "l"),
        edge("u", "uses", "l"),
        edge("p", "uses", "k"),
        edge("p", "justifies", "t"),
        edge("q", "justifies", "t"),
        edge("k", "uses", "t"),
    ]
    roles = {**WHOLE_ROLES, "p": ["justification"], "q": ["justification"]}
    store, kept = kept_state(tmp_path, blocks, edges, standings, roles)
    for j in ("p", "q"):
        judged(store, kept, Aspect.VALID, j, Verdict.VERIFIED, PERSON)
    first, last = circles(store, kept)
    assert handles(kept, first) == ["l", "d"]
    assert handles(kept, last) == ["t", "k"] and last.held_back == ()
    (through,) = [line for line in last.lines if line.claim.words == "Then f is even."]
    assert through.via == (kept.node_of("p"),)
    found = queries.open_report(View(store, kept)).sections[OpenKind.CIRCLE.section]
    assert [i.data["status"] for i in found] == ["circular", "holds nothing back"]


def test_a_circle_s_key_moves_with_an_end_and_a_standing_not_with_a_quote(
    tmp_path, node_ids
) -> None:
    """A line requoted leaves the key; an end named, or a claim in the
    circle standing otherwise, moves it: a new circle, asked again."""

    def key(name, blocks, lines, standings, roles=None):
        node_ids()
        store, kept = kept_state(tmp_path / name, blocks, lines, standings, roles)
        (circle,) = circles(store, kept)
        return circle.key

    base = key("base", THREE, three_lines(), THREE_STANDINGS)
    requoted = three_lines()
    requoted[0] = {**requoted[0], "evidence": [{"text": "need not be"}]}
    assert key("requoted", THREE, requoted, THREE_STANDINGS) == base
    renamed = naming(edge("s", "about", "h"), "Prop P is about objects.")
    assert key("named", THREE, three_lines(renamed), THREE_STANDINGS) != base
    lines = [edge("l", "uses", "d"), edge("d", "uses", "l")]
    whole = key("whole", WHOLE, lines, WHOLE_STANDINGS, WHOLE_ROLES)
    standings = [
        WHOLE_STANDINGS[0],
        ("d", "So f is smaller.", "self"),
        WHOLE_STANDINGS[2],
    ]
    assert key("standing", WHOLE, lines, standings, WHOLE_ROLES) != whole


def test_open_lists_a_circle_once_and_its_claims_under_no_other_kind(
    tmp_path,
) -> None:
    edges = [edge("l", "uses", "d"), edge("d", "uses", "l"), edge("u", "uses", "l")]
    store, kept = kept_state(tmp_path, WHOLE, edges, WHOLE_STANDINGS, WHOLE_ROLES)
    sections = queries.open_report(View(store, kept)).sections
    (item,) = sections[OpenKind.CIRCLE.section]
    assert item.line.splitlines()[0].endswith(": 2 claims in it, 1 resting on it")
    assert "l  → uses d · " in item.line
    assert "names no part of a block holding definition or assumption" in item.line
    assert [c["handle"] for c in item.data["cause"]["claims"]] == ["l", "d"]
    # u stands unsupported itself, listed by its standing; l and d only by
    # their circle
    assert [i.data["handle"] for i in sections[OpenKind.UNSUPPORTED.section]] == ["u"]


# --- the question: what each end on a circle relies on ----------------------

GAIN_CLAIM = "The gain is the ratio of output to input."
#: next-the-bound-is uses def:gain whole, where it needs only [gain]
WHOLE_LINE = '→ uses def:gain · "Next, the bound is sharp."'
NARROWED_LINE = '→ uses def:gain [gain] · "Next, the bound is sharp."'
GAIN_LINE = '→ uses next-the-bound-is · "The gain is the ratio"'


def circular(tmp_path, author=None):
    """The chain of `test_update`, kept as an update's state, then made
    circular by an agent's edit (a person's lines are pinned): def:gain
    holds a claim that uses next-the-bound-is, which uses def:gain whole
    in place of the proof's first step."""
    v, _ = kept_update(tmp_path)

    def edit(sheet: str) -> str:
        sheet = sheet.replace(
            '→ uses we-fix-a-field [K] · "The gain is the ratio"\n',
            '→ uses we-fix-a-field [K] · "The gain is the ratio"\n'
            f"{GAIN_LINE}\n"
            f'⊢ unsupported · "{GAIN_CLAIM}"\n',
        )
        return sheet.replace(
            '→ uses by-definition-def:gain-the · "Next, the bound is sharp."\n',
            f"{WHOLE_LINE}\n",
        )

    made = given(v.store, edit, author or edits.agent("model-a", "s1"))
    assert made.problems == () and made.state is not None, made.problems
    return v, made.state


def asked_at(store, state, named=()) -> list[circles_.AskedCircle]:
    view = View(store, state)
    with edits.opened(store, state) as kept:
        return circles_.asked_circles(view, kept, named)


def number(a: circles_.AskedCircle, text: str) -> str:
    """A line's number, as the task numbers the circle's lines."""
    return f"L{[line.text for line in a.lines].index(text) + 1}"


def task_of(served: str) -> str:
    return served.split()[1]


def refine(served: str) -> tuple[str, dict]:
    """A circle's answer: its whole line named [gain]."""
    (whole,) = [x.split()[0] for x in served.splitlines() if WHOLE_LINE in x]
    return "refined", {
        "task": task_of(served),
        "why": "the bound uses the gain's definition, not its claim",
        "narrow": [{"line": whole, "target_end": ["gain"]}],
    }


def real(served: str) -> tuple[str, dict]:
    return "real", {
        "task": task_of(served),
        "why": "the definition states the bound it is used for",
        "proposal": "split the bound out of the definition",
    }


def circled(
    store, state, answer: Callable[[str], tuple[str, dict]] = refine, named=(), **host
) -> tuple[circles_.Done, FakeQueueHost]:
    """The circles' queue on `state`, a fake host's session answering each
    task with `answer`; then its edit given, as `circles --ask` gives it."""
    fake = FakeQueueHost(working_through(answer), **host)
    run = Run(store, state, circles_.FACTORY, {"circles": list(named), "model": "m"})
    runner = Runner(fake, Resolver(store), 1.0, FLAT)
    return circles_.ask(run, "m", runner, [fake], wait_seconds=0.2), fake


def served(store, state, tmp_path, **options) -> Served:
    workflow = opened(Run(store, state, circles_.FACTORY, options)).__enter__()
    spool = Spool(tmp_path / "spool.jsonl")
    return Served(workflow, {}, spool, 0, "s0", wait=0.05, poll=0.01)


def test_a_circle_is_served_its_claims_and_lines_numbered(tmp_path) -> None:
    v, state = circular(tmp_path)
    (a,) = asked_at(v.store, state)
    assert [owner for owner, _ in a.claims] == ["def:gain", SHARP]
    assert {(line.owner, line.text, line.coarse) for line in a.lines} == {
        (SHARP, WHOLE_LINE, (Coarse.WHOLE,)),
        ("def:gain", GAIN_LINE, ()),
    }
    s = served(v.store, state, tmp_path)
    task = s.next().splitlines()
    assert task[:4] == [
        f"task k{a.key} (1 of 1)",
        "does each line on this circle name what the text relies on?",
        f"circle {a.key}: 2 claims in it, 0 resting on it",
        "its claims:",
    ]
    assert task[4].startswith("C1 def:gain") and task[5].startswith(f"C2 {SHARP}")
    whole = f"{number(a, WHOLE_LINE)} ⟦{SHARP}⟧ {WHOLE_LINE}  — names no part of a"
    assert any(line.startswith(whole) for line in task), task
    assert task[-1].startswith("pointers: ⟦")
    assert [t.name for t in s.tools][:3] == ["next", "refined", "real"]
    brief = s.workflow.steps[0].template.text
    assert "## This session: circles of what claims rest on" in brief


def test_a_refined_answer_is_the_circles_edit_and_breaks_the_circle(tmp_path) -> None:
    v, state = circular(tmp_path)
    (a,) = asked_at(v.store, state)
    done, _ = circled(v.store, state)
    out = done.outcome
    assert [(s.circle.key, s.real) for s in done.said] == [(a.key, False)]
    assert out.state is not None and out.problems == (), out.problems
    edit = v.store.authoritative()[out.edit]
    assert edit.host != "person"
    assert edit.reason == (
        f"circle {a.key}: the bound uses the gain's definition, not its claim"
    )
    view = View(v.store, out.state)
    assert view.circles == ()
    with edits.opened(v.store, out.state) as kept:
        assert NARROWED_LINE in edits.shown(kept, Layer.EDGES)
    run = Run(v.store, state, circles_.FACTORY, {"circles": [], "model": "m"})
    again = circles_.ask(run, "m")
    assert (again.outcome.edit, again.outcome.state) == (out.edit, out.state)


def test_a_real_answer_is_filed_shown_and_not_asked_again(tmp_path) -> None:
    """Read real, the circle is filed under its key with the answer's
    author, shown with its reason and proposal, and not asked again; a
    line on it named otherwise makes another circle, asked."""
    v, state = circular(tmp_path)
    (a,) = asked_at(v.store, state)
    done, _ = circled(v.store, state, real)
    assert done.outcome.state is None
    assert [(s.circle.key, s.real, s.proposal) for s in done.said] == [
        (a.key, True, "split the bound out of the definition")
    ]
    (filed,) = v.store.judgment_records().circles
    assert (filed.state, filed.circle) == (state.id, a.circle.key)
    view = View(v.store, state)
    (item,) = queries.open_report(view).sections[OpenKind.CIRCLE.section]
    assert item.data["status"] == "read real"
    assert "read real by fake" in item.line
    assert "proposal: split the bound out of the definition" in item.line
    assert asked_at(v.store, state) == []
    with pytest.raises(circles_.CircleError, match="is read real by"):
        asked_at(v.store, state, [a.key])
    # the run that read it real still draws it, settled
    run = Run(v.store, state, circles_.FACTORY, {"circles": [], "model": "m"})
    assert circles_.ask(run, "m").complete
    head_lines = check.report(check.gates(view)).head
    assert any("1 read real, 1 of them by agents alone" in h for h in head_lines)
    renamed = given(
        v.store,
        lambda sheet: sheet.replace(
            GAIN_LINE,
            '→ uses next-the-bound-is [next-the-bound-is] · "The gain is the ratio"',
        ),
    )
    assert renamed.state is not None, renamed.problems
    (again,) = asked_at(v.store, renamed.state)
    assert again.circle.key != a.circle.key


def test_a_part_added_and_named_is_written_by_code(tmp_path) -> None:
    """A refined answer adding a part of def:gain and naming it: code writes
    the legend line and the narrowed line, given as the circles' edit."""
    v, state = circular(tmp_path)

    def add_part(served: str) -> tuple[str, dict]:
        tool, args = refine(served)
        (narrow,) = args["narrow"]
        return tool, {
            **args,
            "parts": [
                {
                    "block": "def:gain",
                    "label": "out",
                    "words": "ratio of output to input",
                    "role": "definition",
                }
            ],
            "narrow": [{**narrow, "target_end": ["out"]}],
        }

    done, _ = circled(v.store, state, add_part)
    assert done.outcome.state is not None, done.outcome.problems
    with edits.opened(v.store, done.outcome.state) as kept:
        sheet = edits.shown(kept, Layer.EDGES)
    assert '  def:gain [out]: "ratio of output to input"' in sheet
    assert '→ uses def:gain [out] · "Next, the bound is sharp."' in sheet
    assert View(v.store, done.outcome.state).circles == ()


def test_a_narrowed_line_is_written_by_its_brackets_alone() -> None:
    line = '→ uses t [a, b] ref assumed · "q" — a note'
    assert circles_.with_ends(line, ["a"], None) == (
        '→ uses t [a] ref assumed · "q" — a note'
    )
    assert circles_.with_ends('→ about t · "q"', None, ["-"]) == ('→ about [-] t · "q"')
    assert circles_.with_ends('→ about [x, y] t [c] · "q"', ["c"], ["x"]) == (
        '→ about [x] t [c] · "q"'
    )


def test_a_uses_line_s_source_end_names_the_parts_it_leaves_from() -> None:
    """Narrowed at its source end, a uses line names the parts of its block
    it leaves from, some of those it named, never none (`[-]`, an about
    line's)."""
    assert circles_._narrowed('→ uses t · "q"', '→ uses [p] t · "q"', "o") == []
    assert circles_._narrowed('→ uses [p, r] t · "q"', '→ uses [p] t · "q"', "o") == []
    (refused,) = circles_._narrowed('→ uses t · "q"', '→ uses [-] t · "q"', "o")
    assert "[-] is an about line's" in refused


def _placing_gain(served: str) -> tuple[str, dict]:
    """A circle's answer: the whole line named [gain], and the definition's
    line onto the bound placed at its part [gain]."""
    tool, args = refine(served)
    (gain,) = [x.split()[0] for x in served.splitlines() if GAIN_LINE in x]
    return tool, {
        **args,
        "narrow": [*args["narrow"], {"line": gain, "own_end": ["gain"]}],
    }


def test_a_refined_answer_places_no_line_of_a_block_not_placed(tmp_path) -> None:
    """The definition's lines name no part they leave from: placing one is
    the placing's word on every line of the block, never a circle's."""
    v, state = circular(tmp_path)
    (a,) = asked_at(v.store, state)
    s = served(v.store, state, tmp_path)
    task = s.next()
    text, refused = s.call("refined", _placing_gain(task)[1])
    assert refused and "`place --ask`'s; keep this end as it is" in text, text
    assert a.key in text


def test_a_refined_answer_may_place_a_line_of_a_placed_block(tmp_path) -> None:
    """Where the definition's other line leaves from [gain] already, the
    block is read by its parts: its line onto the bound may name [gain]
    too, a source part being no claim of the circle."""
    v, state = circular(tmp_path)
    placed = given(
        v.store,
        lambda sheet: sheet.replace(
            '→ uses we-fix-a-field [K] · "The gain is the ratio"',
            '→ uses [gain] we-fix-a-field [K] · "The gain is the ratio"',
        ),
        edits.agent("model-a", "s1"),
    )
    assert placed.state is not None, placed.problems
    done, _ = circled(v.store, placed.state, _placing_gain)
    assert done.outcome.state is not None, done.outcome.problems
    with edits.opened(v.store, done.outcome.state) as kept:
        sheet = edits.shown(kept, Layer.EDGES)
    assert '→ uses [gain] next-the-bound-is · "The gain is the ratio"' in sheet


@pytest.mark.parametrize(
    ("args", "refused"),
    [
        (lambda a: {"keep": [number(a, WHOLE_LINE)]}, "narrows no line"),
        (
            lambda a: {
                "parts": [
                    {
                        "block": "def:gain",
                        "label": "ratio",
                        "words": "ratio of output",
                        "role": "definition",
                    }
                ],
                "keep": [number(a, WHOLE_LINE)],
            },
            "narrows no line",
        ),
        (
            lambda a: {
                "narrow": [
                    {"line": number(a, WHOLE_LINE), "target_end": ["gain"]},
                    {"line": number(a, WHOLE_LINE), "target_end": ["ratio"]},
                ]
            },
            "a line is narrowed once",
        ),
        (
            lambda a: {
                "parts": [
                    {
                        "block": "def:gain",
                        "label": "ratio",
                        "words": GAIN_CLAIM,
                        "role": "definition",
                    }
                ],
                "narrow": [{"line": number(a, WHOLE_LINE), "target_end": ["ratio"]}],
            },
            "the words hold a claim of def:gain",
        ),
        (
            lambda a: {
                "parts": [
                    {
                        "block": "def:gain",
                        "label": "ratio",
                        "words": "the ratio of input",
                        "role": "definition",
                    }
                ],
                "narrow": [{"line": number(a, WHOLE_LINE), "target_end": ["ratio"]}],
            },
            "not verbatim",
        ),
        (
            lambda a: {
                "parts": [
                    {
                        "block": "def:gain",
                        "label": "ratio",
                        "words": "the ratio of output",
                        "role": "claim",
                    }
                ],
                "narrow": [{"line": number(a, WHOLE_LINE), "target_end": ["ratio"]}],
            },
            "never a claim",
        ),
        (
            lambda a: {
                "narrow": [
                    {
                        "line": number(a, GAIN_LINE),
                        "target_end": ["next-the-bound-is"],
                    }
                ]
            },
            "shows a candidate end: narrow it",
        ),
        (lambda a: {"narrow": [{"line": "L9"}]}, "narrow 'L9'"),
        (
            lambda a: {"narrow": [{"line": number(a, WHOLE_LINE)}]},
            "name its target end, its own end or both",
        ),
        (lambda a: {"unsupported": ["C2"]}, "only from restated to unsupported"),
        (lambda a: {"why": " "}, "give the reason (why)"),
    ],
    ids=[
        "narrows nothing",
        "a part named by nothing",
        "given twice",
        "a part holding the claim",
        "words not the target's",
        "a claim part",
        "a candidate left",
        "no such line",
        "no end named",
        "a claim made unsupported that is not restated",
        "no reason",
    ],
)
def test_a_refined_answer_that_is_no_narrowing_is_refused(
    tmp_path, args, refused
) -> None:
    v, state = circular(tmp_path)
    (a,) = asked_at(v.store, state)
    s = served(v.store, state, tmp_path)
    s.next()
    given_args = {"task": f"k{a.key}", "why": "narrowed", **args(a)}
    text, was_refused = s.call("refined", given_args)
    assert was_refused and refused in text, text
    assert s.spool.entries() == []


@pytest.mark.parametrize(
    ("lines", "refused"),
    [
        ([f"⟦{SHARP}⟧", f"- {WHOLE_LINE}"], "is taken off and not given back"),
        (
            [
                f"⟦{SHARP}⟧",
                f"- {WHOLE_LINE}",
                '+ → uses def:gain [gain] · "Next, the bound"',
            ],
            "takes no line off",
        ),
        (
            [
                f"⟦{SHARP}⟧",
                '- ⊢ unsupported · "Next, the bound is sharp."',
                '+ ⊢ restated · "Next, the bound is sharp."',
            ],
            "only from restated to unsupported",
        ),
        (
            [
                "⟦def:gain⟧",
                '- → uses we-fix-a-field [K] · "The gain is the ratio"',
                '+ → uses we-fix-a-field [K] · "The gain is the ratio"',
            ],
            "not a line on the circle",
        ),
    ],
    ids=["deleted", "requoted", "made restated", "off the circle"],
)
def test_the_gate_refuses_what_no_narrowing_does(tmp_path, lines, refused) -> None:
    """What code never writes from a typed answer, the gate refuses still."""
    v, state = circular(tmp_path)
    (a,) = asked_at(v.store, state)
    keeps = [(SHARP, WHOLE_LINE)] if "⟦def:gain⟧" in lines else []
    with edits.opened(v.store, state) as kept:
        problems = circles_.refined_problems(
            kept,
            a,
            "\n".join(lines),
            keeps,
            edits.agent("m", "s"),
            edits.shown(kept, Layer.EDGES),
        )
    assert any(refused in p for p in problems), problems


def test_a_narrowing_that_would_stand_a_claim_argued_is_refused(
    tmp_path, monkeypatch
) -> None:
    """Whether the paper argues a claim is the search's: a circle's answer
    whose lines would stand one argued is refused."""
    v, state = circular(tmp_path)
    (a,) = asked_at(v.store, state)
    stood = [(SHARP, "+", '⊢ restated · "Next, the bound is sharp."')]
    monkeypatch.setattr(
        edges_file, "stood_argued", lambda sheet, fragment, restating=False: stood
    )
    lines = "\n".join([f"⟦{SHARP}⟧", f"- {WHOLE_LINE}", f"+ {NARROWED_LINE}"])
    with edits.opened(v.store, state) as kept:
        problems = circles_.refined_problems(
            kept, a, lines, [], edits.agent("m", "s"), edits.shown(kept, Layer.EDGES)
        )
    assert any("a narrowing adds no support" in p for p in problems), problems


def test_a_line_a_person_pinned_stays_and_its_circle_may_be_read_real(
    tmp_path,
) -> None:
    """Made circular by a person's edit, the whole line is the person's,
    pinned: shown so, never asked to be narrowed, and the circle read
    real with a proposal for the person."""
    v, state = circular(tmp_path, edits.A_PERSON)
    (a,) = asked_at(v.store, state)
    (whole,) = [line for line in a.lines if line.coarse]
    assert (whole.text, whole.pinned) == (WHOLE_LINE, True)
    s = served(v.store, state, tmp_path)
    assert "(a person's line, pinned: it stays)" in s.next()
    narrowed = {"line": number(a, WHOLE_LINE), "target_end": ["gain"]}
    text, refused = s.call(
        "refined", {"task": f"k{a.key}", "why": "w", "narrow": [narrowed]}
    )
    assert refused and "is a person's, pinned: keep it as written" in text
    text, refused = s.call("real", real(f"task k{a.key}")[1])
    assert not refused, text


def test_a_circle_is_named_by_a_prefix_of_its_key(tmp_path) -> None:
    v, state = circular(tmp_path)
    (a,) = asked_at(v.store, state)
    assert [x.key for x in asked_at(v.store, state, [a.key[:5]])] == [a.key]
    with pytest.raises(circles_.CircleError, match="names no circle"):
        asked_at(v.store, state, ["ffffffff"])


def test_circles_ask_dry_run_writes_the_brief_and_tasks(tmp_path, capsys) -> None:
    v, state = circular(tmp_path)
    args = [str(v.new.root), "--store", str(v.store.directory)]
    out = tmp_path / "dry"
    capsys.readouterr()
    assert main(["circles", *args]) == 0
    assert f"1 circles at state {state.id}" in capsys.readouterr().out
    assert main(["circles", *args, "--ask", "--dry-run"]) == 0
    assert "1 circles, 2 lines" in capsys.readouterr().out and not out.exists()
    assert main(["circles", *args, "--dry-run"]) == 1
    assert "--dry-run goes with --ask" in capsys.readouterr().err
    assert main(["circles", *args, "--ask", "--dry-run", "--prompts", str(out)]) == 0
    printed = capsys.readouterr().out
    assert f"circles --ask at state {state.id}: 1 circles, 2 lines" in printed
    fitted = circles_.FITTED
    assert f"${fitted.task:.4f} a circle, fitted on recorded runs" in printed
    assert {p.name for p in out.iterdir()} == {f"{circles_.KIND}.md", "tasks.md"}
    assert v.store.judgment_records().circles == ()


def test_a_circle_is_priced_by_what_the_store_s_sessions_cost(tmp_path) -> None:
    v, state = circular(tmp_path)
    fitted = circles_.FITTED
    assert circles_.price(v.store, fitted.model) == Price(fitted.start, fitted.task)
    circled(v.store, state, cost=0.05)
    assert circles_.price(v.store, "fake/queue") == Price(0.0, 0.05, 1, 1)


# --- where it runs: after a build's edges, after an update's reread ----------


#: The chain with def:gain and next-the-bound-is a circle.
CIRCLE_CHAIN = {
    **CHAIN,
    DEFINITION: [*CHAIN[DEFINITION], GAIN_LINE, f'⊢ unsupported · "{GAIN_CLAIM}"'],
    SHARP: [WHOLE_LINE, *CHAIN[SHARP][1:]],
}


def stand_or_refine(served: str) -> tuple[str, dict]:
    """The update's end: each stale owner's lines stand, each circle is
    refined."""
    if "owner's lines" in served:
        return "stands", {"task": task_of(served), "why": "its lines hold"}
    return refine(served)


def update_with_a_circle(tmp_path, monkeypatch, capsys, *flags: str, settings=""):
    """`axiomation update` on the chain whose edges make a circle (def:gain
    and next-the-bound-is), the setting narrowed, every stale owner's lines
    standing, then the circles refined. The papers, the exit code, the
    summary and both hosts."""
    from axiomation.commands import seating

    v = two_papers(tmp_path, EDITED_SETTING, CIRCLE_CHAIN, CHAIN_LEGEND)
    profiles = tmp_path / "profiles.toml"
    profiles.write_text(_profile("p", SINGLE + settings), encoding="utf-8")
    fake = FakeHost(
        lambda _: "",
        edits={EDGES_FILE: answering({SETTING: [NARROWED]}, as_assumption)},
    )
    answers = QueueThen(v, fake.edits[EDGES_FILE], stand_or_refine)
    queued = FakeQueueHost(working_through(answers))
    monkeypatch.setattr(seating, "host_of", lambda agent, executable, tools=None: fake)
    monkeypatch.setattr(seating, "queue_hosts", lambda seated: [queued])
    monkeypatch.setattr(
        seating,
        "executables_of",
        lambda agents: {a.harness: "fake" for a in agents.values()},
    )
    capsys.readouterr()
    code = main(
        [
            "update",
            "--json",
            *(str(v.new.root), "--store", str(v.store.directory)),
            *("--profiles", str(profiles), "--profile", "p", "--rates", "3,15"),
            *("--budget", "10"),
            *flags,
        ]
    )
    out = capsys.readouterr().out
    assert "{" in out, code
    assert not answers.update.unserved(), answers.update.unserved()
    return v, code, json.loads(out[out.index("{") :]), fake, queued


def test_an_update_ends_with_the_question_on_its_circles(
    tmp_path, monkeypatch, capsys
) -> None:
    """After the update and its reread, the circle its state holds is
    asked; the narrowed line is the circles' edit, a state after them."""
    v, code, summary, fake, queued = update_with_a_circle(tmp_path, monkeypatch, capsys)
    assert code == 0, summary
    asked = summary["circles"]
    assert (asked["circles"], asked["lines"], asked["refined"], asked["real"]) == (
        1,
        2,
        1,
        0,
    )
    assert asked["open_circles"] == 0
    latest = versions.latest(v.store)
    assert asked["state"] == latest.id
    assert View(v.store, latest).circles == ()
    # the update's queue, then the reread's and the circles'; the edges'
    # file host never
    assert fake.calls == 0 and len(queued.sessions) == 3


def test_a_build_whose_circles_step_raises_still_prints_what_it_kept(
    tmp_path, monkeypatch, capsys
) -> None:
    from axiomation.commands import asks
    from test_lost_edges import built

    def raises(*_: object) -> None:
        raise RuntimeError("the sheet does not render")

    monkeypatch.setattr(asks, "circles_after", raises)

    # its build's replay, kept as a state, then its circles step
    with pytest.raises(RuntimeError, match="does not render"):
        built(tmp_path, capsys)

    out = capsys.readouterr().out
    summary = json.loads(out[out.index("{") :])
    store = RecordStore(tmp_path / "paper" / "axiomation")
    assert summary["state"]["id"] == versions.latest(store).id


@pytest.mark.parametrize(
    ("flags", "settings"),
    [(("--no-circles",), ""), ((), "ask_circles_after_edges = false\n")],
)
def test_no_circles_or_the_setting_off_leaves_the_circle(
    tmp_path, monkeypatch, capsys, flags, settings
) -> None:
    v, code, summary, fake, queued = update_with_a_circle(
        tmp_path, monkeypatch, capsys, *flags, settings=settings
    )
    assert code == 0 and "circles" not in summary
    assert fake.calls == 0 and len(queued.sessions) == 2
    (circle,) = View(v.store, versions.latest(v.store)).circles
    assert len(circle.claims) == 2


def test_a_run_s_question_on_its_circles_is_asked_on_the_state_it_kept(
    tmp_path, monkeypatch
) -> None:
    """The build's and the update's step (`circles_after`) on a kept state
    with a circle; with no runner, what the store does not hold is left
    unanswered; on a state with none, nothing is asked."""
    from axiomation.commands import asks, seating
    from axiomation.profiles import read_profiles

    v, state = circular(tmp_path)
    profile = read_profiles()["standard"]
    agent = profile.agent(Stage.EDGES)
    queued = FakeQueueHost(working_through(refine))
    monkeypatch.setattr(seating, "queue_hosts", lambda seated: [queued])
    runner = Runner(queued, Resolver(v.store), 10.0, FLAT)
    unanswered, _ = asks.circles_after(v.store, state, profile, None, None, agent)
    assert unanswered["unanswered"] == 1 and queued.sessions == []
    counts, stop = asks.circles_after(v.store, state, profile, None, runner, agent)
    assert stop is None and (counts["refined"], counts["open_circles"]) == (1, 0)
    after = versions.state(v.store, counts["state"])
    assert asks.circles_after(v.store, after, profile, None, runner, agent) is None


def test_an_update_dry_run_prices_the_question_if_it_fires(tmp_path, capsys) -> None:
    """Priced on the circles at the state it starts from, one at least."""
    v, _ = kept_update(tmp_path)
    third = make_paper(
        tmp_path / "third",
        main=EDITED_SETTING.replace(
            "A second sentence of the setting, edited.",
            "A second sentence of the setting, edited again.",
        ),
    )
    args = ["update", "--json", str(third.root), "--store", str(v.store.directory)]
    args += ["--profile", "standard", "--dry-run", "--json"]

    def priced(*flags: str) -> dict:
        capsys.readouterr()
        assert main([*args, *flags]) == 0
        out = capsys.readouterr().out
        return json.loads(out[out.index("{") :])

    fitted = circles_.FITTED
    assert priced()["circles_if_it_fires"] == round(fitted.start + fitted.task, 4)
    assert "circles_if_it_fires" not in priced("--no-circles")
