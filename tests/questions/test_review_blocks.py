"""The argument review answered by block (`review_blocks`): its lines
written as the sheet's, each subject by its id; the sheet's problems told
by the session's line and block; `paper.md` made lean, its subjects marked
and its flags and findings under their blocks; the brief cut to what the
paper needs. Synthetic sheets, in words of our own."""

from __future__ import annotations

import pytest

from axiomation.questions import review_blocks as rb
from axiomation.questions import review_file as rf

SHEET = "\n".join(
    [
        "# review: argument",
        "",
        "---",
        "",
        '⟦s1 true⟧ b1 · "x"',
        "",
        '⟦s2 true⟧ b1 · "y"',
        "",
        '⟦s3 valid⟧ p1 → t1 · "z"',
        "",
        "⟦f1 finding⟧ valid p1 2 — a gap",
    ]
)
SPEC = {
    "reference": SHEET,
    rb.SUBJECTS: [
        ["s1", "true", "b1", None, "x-claim"],
        ["s2", "true", "b1", None, "y-claim"],
        ["s3", "valid", "p1", "t1", "z-claim"],
    ],
}


def added(found: rb.Translated) -> list[str]:
    return found.sheet.rstrip("\n").split("\n")[len(SHEET.split("\n")) :]


def test_lines_by_block_are_written_as_the_sheet_s_lines() -> None:
    answer = """@@ b1
true [x-claim] verified informal — it holds
true [y-claim] not-shown informal — a gap
find true 2 "y" — the gap — close it
rule [y-claim] owed 2 — nothing argues it — argue it
keep [x-claim]

@@ p1
valid t1 verified informal — every step
edge p1 → uses b1 · "x" — it uses it
stands f1 — the gap is still there
"""
    found = rb.translated(SPEC, answer)
    assert added(found) == [
        "judge s1 verified informal — it holds",
        "judge s2 not-shown informal — a gap",
        'find true b1 2 "y" — the gap — close it',
        "rule s2 owed 2 — nothing argues it — argue it",
        "keep s1",
        "judge s3 verified informal — every step",
        'edge p1 → uses b1 · "x" — it uses it',
        "stands f1 — the gap is still there",
    ]
    assert found.problems == ()


def test_a_find_line_with_its_anchors_or_its_pages_keeps_them() -> None:
    answer = """@@ b1
find true b1 p1 2 "y" — the gap — close it
find true 2 pages a2020:3 "y" — the gap — close it
"""
    assert added(rb.translated(SPEC, answer)) == [
        'find true b1 p1 2 "y" — the gap — close it',
        'find true b1 2 pages a2020:3 "y" — the gap — close it',
    ]


def test_a_line_read_by_the_form_s_brackets_means_the_same() -> None:
    """The forms show the pages read as `[pages KEY:N, …]`, the optional
    part's brackets, beside the claim's literal ones; and `[<claim>]` is
    left out where one subject fits, so `[]` names none."""
    answer = """@@ b1
true [x-claim] verified informal [pages a2020:3] — read there
true [y-claim] not-shown informal [pages a2020:3, a2020:4] — a gap
find true 2 [pages a2020:3] "y" — the gap — close it

@@ p1
valid t1 [] verified informal — every step
"""
    found = rb.translated(SPEC, answer)
    assert found.problems == ()
    assert added(found) == [
        "judge s1 verified informal pages a2020:3 — read there",
        "judge s2 not-shown informal pages a2020:3, a2020:4 — a gap",
        'find true b1 2 pages a2020:3 "y" — the gap — close it',
        "judge s3 verified informal — every step",
    ]
    owned = 'edge p1 → uses b1 · "see [pages 3]" — it uses it'
    assert added(rb.translated(SPEC, f"@@ p1\n{owned}\n")) == [owned]
    assert found.origin[len(SHEET.split("\n")) + 1] == (
        "b1",
        "true [x-claim] verified informal [pages a2020:3] — read there",
    )


def test_a_line_that_names_no_subject_is_told_why() -> None:
    answer = """true verified informal — before any heading
@@ b1
true verified informal — which claim?
true [w-claim] verified informal — no such claim
valid t1 verified informal — b1 justifies nothing
judge s1 verified informal — the sheet's form
"""
    problems = rb.translated(SPEC, answer).problems
    assert len(problems) == 5
    assert "goes under its block's heading" in problems[0]
    assert "name the claim, one of x-claim, y-claim" in problems[1]
    assert "no true subject names claim [w-claim]" in problems[2]
    assert "no valid subject onto t1 on this block" in problems[3]
    assert "starts with one of" in problems[4]


def test_a_problem_is_told_by_the_session_s_line_and_its_subjects_by_block() -> None:
    found = rb.translated(SPEC, "@@ p1\nvalid t1 verified informal — fine\n")
    line = len(SHEET.split("\n")) + 1
    told = rb.told(SPEC, found, f"line {line}: a verified valid names what J uses")
    assert told == "`@@ p1` 'valid t1 verified informal — fine': " + (
        "a verified valid names what J uses"
    )
    assert rb.told(SPEC, found, "no judge line on s1, s2") == (
        "no verdict line on `@@ b1` true [x-claim], `@@ b1` true [y-claim]"
    )


def test_a_problem_speaks_the_session_s_grammar_not_the_sheet_s() -> None:
    found = rb.translated(SPEC, "")
    ruled = rb.told(
        SPEC, found, "s2 takes no rule line: only a subject marked `⊢ unsupported` does"
    )
    assert ruled == (
        "`@@ b1` true [y-claim] takes no rule line: only a claim with "
        "`↳ ⊢ unsupported: …` under its line does"
    )
    form = rb.told(SPEC, found, f"a judge line is `{rf.JUDGE_FORM}`")
    assert "judge" not in form and "`true [<claim>] <verdict>" in form


def test_a_subject_is_placed_by_its_line_and_its_claim_s_words() -> None:
    claims = {"t1": {"a": "the first", "b": "the second"}, "b1": {"c": "one"}}
    valid = rb.placed("s3", "valid", 'p1 → t1 · "the second"', "the second", claims.get)
    true = rb.placed("s1", "true", 'b1 · "one"', "one", claims.get)
    assert valid == rb.Subject("s3", "valid", "p1", "t1", "b")
    assert true == rb.Subject("s1", "true", "b1", None, "c")


EDGES = """# edges

Parts' legend lines and the `Pervasive:` line go here, above the line below.

  b1 [bound]: "x is bounded"
Files: main.tex, refs.bib
Code: run.py — a check

---

⟦b1 claim⟧ main.tex:3:1 (statement)
Then x is bounded.

⊢ justified [x-claim] · "x is bounded"
⊢ unsupported [y-claim] · "y is small"
⟦done b1⟧

⟦group g1 proof⟧

⟦p1 justification⟧ main.tex:5:1 (prose)
Since x is bounded, y is small.

→ uses b1 [bound] · "Since x is bounded" — the step
→ about b1 · "x is bounded"
⟦done p1⟧

⟦end group g1⟧

→ justifies b1 [y-claim] · "y is small"
⟦done g1⟧
"""


def test_paper_md_is_the_edges_sheet_made_lean() -> None:
    lean = rb.lean(EDGES)
    assert lean.startswith("The parts edge lines name (`[<part>]`):\n\n  b1 [bound]:")
    for gone in ("# edges", "Parts' legend", "Files:", "Code:", "main.tex:", "⟦done"):
        assert gone not in lean
    assert "→ uses b1 [bound] — the step" in lean
    assert "→ about b1\n" in lean
    assert '→ justifies b1 [y-claim] · "y is small"' in lean
    assert "⟦b1 claim⟧ (statement)" in lean


def test_paper_md_marks_its_subjects_and_shows_their_notes_flags_and_findings() -> None:
    shown = [
        (
            rb.Shown(rb.Subject("s2", "true", "b1", None, "y-claim"), ("  ↳ a note",)),
            "y",
        ),
        (rb.Shown(rb.Subject("s3", "valid", "g1", "b1", "y-claim")), "y is small"),
    ]
    hints = '# hints\n\n## Flags\n\n  ? other p1 · "x is bounded" — read it twice\n'
    questions = [("p1", ["⟦f1 finding⟧ valid p1 2 — a gap"])]

    text, every = rb.paper(EDGES, hints, shown, questions)
    some = text.split("\n")

    assert not every
    at = some.index('⊢ unsupported [y-claim] · "y is small" · asked')
    assert some[at + 1] == "  ↳ a note"
    assert '⊢ justified [x-claim] · "x is bounded"' in some  # not asked
    assert '→ justifies b1 [y-claim] · "y is small" · asked' in some
    flag = some.index('⚑ other · "x is bounded" — read it twice')
    assert some[flag - 1] == "→ about b1"
    assert some[flag + 1] == "⟦f1 finding⟧ valid p1 2 — a gap"
    x = (rb.Shown(rb.Subject("s1", "true", "b1", None, "x-claim")), "x is bounded")
    text, every = rb.paper(EDGES, hints, [x, *shown], ())
    assert every and rb.ASKED not in text and "  ↳ a note" in text


def test_what_paper_md_has_no_block_or_line_for_stands_at_its_head() -> None:
    """A finding on the paper whole, a flag on a block the state no longer
    holds, a subject whose line the sheet lacks: at the head, never lost,
    and the subjects then marked."""
    gone = (rb.Shown(rb.Subject("s9", "true", "b9", None, None), ("  ↳ more",)), "z")
    hints = '  ? other old-block · "w" — gone\n'
    text, every = rb.paper(EDGES, hints, [gone], [(None, ["⟦f2 finding⟧ paper"])])
    head = text.split("\n")[:10]
    assert not every and head[0] == rb.UNPLACED
    assert "`@@ b9` true: asked; its line is not shown" in head
    assert "  ↳ more" in head and "⟦f2 finding⟧ paper" in head
    assert '⚑ other · "w" — gone' in head


def test_the_hints_lose_their_flags_and_go_when_nothing_else_is_in_them() -> None:
    """The hints as `review` writes them with nothing read: headings, the
    note, `(none)`, the open report's head and its counts."""
    hints = (
        "# hints\n\nWhat others read at this state. A hint says where to look.\n\n"
        "## Judgments\n\n(none)\n\n"
        "## Flags, stale marks, lapsed and disputed subjects\n\n"
        "1 open at state abc: 1 flag\n"
        'flag (1):\n  ? other p1 · "x" — y\nstale (0):\nlapsed (0):\ndisputed (0):\n'
    )
    flags, rest = rb.flags(hints)
    assert flags == {"p1": ['⚑ other · "x" — y']}
    assert rb.bare(rest) and "flag (1):" not in rest
    assert not rb.bare(rest + "stale (1):\n  p1  ⟦stale since abc⟧\n")


def test_a_problem_keeps_the_session_s_own_words() -> None:
    found = rb.translated(SPEC, "@@ b1\ntrue [x-claim] verified informal — s1 + s2\n")
    line = len(SHEET.split("\n")) + 1
    told = rb.told(SPEC, found, f"line {line}: s2 is not named")
    assert told == (
        "`@@ b1` 'true [x-claim] verified informal — s1 + s2': "
        "`@@ b1` true [y-claim] is not named"
    )


def test_a_line_that_does_not_translate_is_kept_after_the_sheet() -> None:
    found = rb.translated(SPEC, "@@ b1\nthe claim holds\n")
    assert found.kept == found.sheet + "the claim holds\n"
    assert found.kept.startswith(rf.SHEET_TITLE)


def test_the_brief_keeps_the_parts_its_paper_needs() -> None:
    template = (
        "A\n<!-- if rules -->\nR\n<!-- end rules -->\n\n"
        "B, then<!-- if later --> L<!-- end later -->.\n"
    )
    assert rb.instructions(template, {"rules"}) == "A\nR\n\nB, then.\n"
    assert rb.instructions(template, {"later"}) == "A\n\nB, then L.\n"
    paper_md = '→ justifies t1 sketch · "…"\n→ about t2 generalizes\n'
    found = rb.holds(
        paper_md, rules=False, settle=True, later=False, every=True, cited=True
    )
    assert found == {"every", "settle", "flagged", "generalizes", "cited"}
    brief = "# Brief\n\n## How to work\n\nWork.\n"
    assert rb.with_section(brief, "# The works\n\nRead them.\n") == (
        "# Brief\n\n## The works\n\nRead them.\n\n## How to work\n\nWork.\n"
    )
    assert rb.with_section(brief, "") == brief


@pytest.mark.parametrize(
    ("laid", "parts"),
    [
        # a hint at the margin, under a block or at the head
        ("↳ filed f1 presentation 1", {"hinted"}),
        # a person's argument for a reading asked again
        ("↳ dismissed: s3 true not-shown", {"hinted", "argued"}),
        ("↳ proposed: s3 true verified", {"hinted", "argued"}),
        # a subject's own line, indented, is the key's; a flag is told apart
        ("  ↳ relied on by s1", set()),
        ('⚑ other · "words" — a note', set()),
    ],
)
def test_the_brief_tells_of_hints_where_paper_md_lays_them(laid, parts) -> None:
    """The parts on hints follow `paper.md` as the session reads it, not
    the key's: a part on what is not there reads as a rule of its own."""
    keyed = "⟦thm:bound claim⟧ (theorem)\nThe output is bounded.\n"
    read_as = keyed + laid + "\n"
    found = rb.holds(
        keyed,
        rules=False,
        settle=False,
        later=False,
        every=True,
        cited=False,
        read_as=read_as,
    )
    assert found - {"every"} == parts


EDGES_SHEET = """# edges

Files: paper.md, x.py

---

⟦thm:bound claim⟧ paper.md:3:1 (theorem)
The output ⟦+ claim⟧ is bounded, as x.py computes.

→ uses file:x.py · "as x.py computes"
⊢ justified · "is bounded"
⟦done thm:bound⟧

⟦group bound_proof proof⟧
⟦p1 justification⟧ paper.md:5:1 (prose)
By the gain.
⟦end group bound_proof⟧
→ uses file:y.py
"""


def test_the_paper_plain_keeps_each_block_s_handle_and_words_alone() -> None:
    text = rb.plain(EDGES_SHEET)

    assert "⟦thm:bound claim⟧ (theorem)" in text
    assert "The output is bounded, as x.py computes." in text
    assert not any(line.startswith(("→ ", "⊢ ", "⟦done")) for line in text.split("\n"))
    assert "Files:" not in text


def test_the_files_a_block_or_a_group_uses_are_read_off_the_sheet() -> None:
    assert rb.files_used(EDGES_SHEET) == {
        "thm:bound": ["file:x.py"],
        "bound_proof": ["file:y.py"],
    }


def test_lines_laid_under_their_block_never_open_one_and_none_is_lost() -> None:
    """A laid line sits under its block's last line; one whose block the
    paper does not show, or of the paper's whole, stands at the head; a
    `↳` line is never read as a block's opening."""
    text = rb.laid_under(
        rb.plain(EDGES_SHEET),
        {
            "thm:bound": [f"{rb.LAID} uses file:x.py"],
            "gone": [f"{rb.LAID} filed f9"],
            None: [f"{rb.LAID} filed f8"],
        },
        head=["⟦conventions⟧ …", "⟦/conventions⟧", ""],
    )
    lines = text.split("\n")

    assert lines[0] == "⟦conventions⟧ …"
    assert lines[lines.index(rb.UNPLACED) :][:6] == [
        rb.UNPLACED,
        "",
        "on gone, a block not shown:",
        f"{rb.LAID} filed f9",
        "",
        f"{rb.LAID} filed f8",
    ]
    at = lines.index("The output is bounded, as x.py computes.")
    assert lines[at + 1] == f"{rb.LAID} uses file:x.py"
    assert set(rb._sections(lines)) == {"thm:bound", "p1", "bound_proof"}


def test_the_done_mark_passes_through_an_answer_by_block() -> None:
    found = rb.translated(
        {"reference": "head\n", rb.SUBJECTS: []},
        '@@ thm:bound\nfind presentation 1 "The output" — a word — fix\n⟦done⟧\n',
    )

    assert found.problems == ()
    assert found.sheet.endswith(
        'find presentation thm:bound 1 "The output" — a word — fix\n⟦done⟧\n'
    )
