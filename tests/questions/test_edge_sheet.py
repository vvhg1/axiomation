"""The edge sheet's format: its render, its parse and its gate, on synthetic
sheets and blocks."""

from __future__ import annotations

from pathlib import Path

import pytest

from axiomation.questions.edge_sheet import (
    IMPACT_CLASSES,
    UNFINISHED,
    RenderError,
    SheetBlock,
    SheetGroup,
    asked_if,
    check,
    counts,
    group_label,
    handle_for,
    handles,
    labels_in,
    moved_by_rule,
    paper_files,
    parse,
    part_role,
    renamed,
    render,
    standings_without_claim,
)
from axiomation.questions.edges_file import records

SYNTHETIC = """# a synthetic sheet

  b1 [x]: "Let $x$ be one"
  b3 [the claim]: "The claim" (definition)

---

⟦b1 definition⟧ paper.md:1:1 (prose)
Let $x$ be one.
⟦done b1⟧

⟦group g1 proof⟧

⟦b2 justification⟧ paper.md:3:1 (prose)
\\begin{proof}
Since $x$ is one, the claim holds.
\\end{proof}
→ uses b1 [x] position · "Since $x$ is one" — a note
⟦done b2⟧

⟦end group g1⟧
→ justifies b3 · "\\begin{proof}"
⟦done g1⟧

⟦b3 claim⟧ paper.md:7:1 (prose)
The claim, which nothing below contradicts.
→ about scope:paper · "nothing below contradicts"
→ about b1 where-used · "The claim" — a claim about where b1 is used
⊢ justified · "The claim"
⟦done b3⟧

⟦b4 claim⟧ paper.md:9:1 (prose)
A remark on the claim, with its own reason.
→ uses b3 · "A remark on the claim"
→ self · "with its own reason"
⊢ self · "A remark on the claim"
⟦done b4⟧

⟦b5 assumption⟧ paper.md:11:1 (prose)
We assume $x$ is known.
→ qualifies b1 [x] · "We assume $x$ is known"
⟦done b5⟧
"""


def test_a_sheet_renamed_names_its_blocks_and_groups_anew():
    """Every line that names a block or a group by its handle takes the new
    one; quoted words and part labels are left as they are."""
    onto = {"b1": "setting", "b3": "thm:main", "g1": "group:proof"}
    text = SYNTHETIC.replace("---", "Pervasive: b1, b5\n\n---", 1)

    out = renamed(text, onto)

    assert '  setting [x]: "Let $x$ be one"' in out
    assert '  thm:main [the claim]: "The claim" (definition)' in out
    assert "Pervasive: setting, b5" in out
    assert "⟦setting definition⟧ paper.md:1:1 (prose)" in out
    assert "⟦done setting⟧" in out
    assert "⟦group group:proof proof⟧" in out and "⟦end group group:proof⟧" in out
    assert '→ uses setting [x] position · "Since $x$ is one" — a note' in out
    assert '→ justifies thm:main · "\\begin{proof}"' in out
    assert (
        '→ about setting where-used · "The claim" — a claim about where b1 is used'
        in out
    )
    assert '→ qualifies setting [x] · "We assume $x$ is known"' in out
    # what a line names it keeps: renamed, the sheet reads as it did
    assert check(out) == []
    assert renamed(out, {v: k for k, v in onto.items()}) == text


def test_a_sound_sheet_passes():
    assert check(SYNTHETIC) == []
    assert counts(SYNTHETIC) == {
        "uses": 2,
        "about": 2,
        "justifies": 1,
        "qualifies": 1,
        "self": 1,
        "claims justified": 1,
        "claims self": 1,
        "claims restated": 0,
        "claims conjectured": 0,
        "claims unsupported": 0,
        "blocks": 5,
        "done": 6,
    }


GENERAL = '→ about b1 [x] generalizes · "We assume $x$ is known"'
WIDER = SYNTHETIC.replace(
    '→ qualifies b1 [x] · "We assume $x$ is known"',
    '→ qualifies b1 [x] · "We assume $x$ is known"\n' + GENERAL,
)


def test_a_generalizes_flag_reads_on_an_about_edge_between_premises():
    """An assumption onto a definition's content: the flag parses and the
    sheet passes; renamed, the line keeps it."""
    assert check(WIDER) == []
    (wider,) = [e for e in parse(WIDER).edges if e.generalizes]
    assert (wider.source, wider.kind, wider.target, wider.parts) == (
        "b5",
        "about",
        "b1",
        ("x",),
    )
    assert not wider.where_used
    assert '→ about setting [x] generalizes · "We' in renamed(WIDER, {"b1": "setting"})


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        ((GENERAL, GENERAL.replace("about", "uses")), "marks an about edge only"),
        (
            ('→ qualifies b1 [x] · "We', '→ qualifies b1 [x] generalizes · "We'),
            "generalizes marks an about edge only",
        ),
        (
            ('→ justifies b3 · "', '→ justifies b3 generalizes · "'),
            "generalizes marks an about edge only",
        ),
        (
            (GENERAL, GENERAL.replace("generalizes", "where-used generalizes")),
            "generalizes and where-used exclude each other",
        ),
        (
            ("→ about b1 where-used ·", "→ about b1 generalizes ·"),
            "generalizes runs from a block with an assumption role",
        ),
        (
            (GENERAL, GENERAL.replace("b1 [x]", "b3")),
            "generalizes targets an assumption or a definition's content",
        ),
        (
            ("⟦b5 assumption⟧", "⟦b5 assumption+claim⟧"),
            "generalizes runs from a block with an assumption role and no claim",
        ),
    ],
)
def test_a_generalizes_flag_out_of_place_is_refused(change, problem):
    old, new = change
    assert old in WIDER
    problems = check(WIDER.replace(old, new, 1))
    assert any(problem in p for p in problems), problems


def test_a_generalizes_flag_onto_a_block_holding_a_claim_is_refused():
    """Onto the whole block, a definition that also holds a claim: the
    edge would be about the claim."""
    whole = WIDER.replace(GENERAL, GENERAL.replace("b1 [x]", "b1"))
    assert check(whole) == []
    problems = check(whole.replace("⟦b1 definition⟧", "⟦b1 definition+claim⟧"))
    assert any(
        "generalizes targets an assumption or a definition's content" in p
        for p in problems
    ), problems


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        (('Since $x$ is one"', 'Since $x$ is two"'), "evidence not in b2"),
        (("→ uses b1 [x]", "→ uses b9 [x]"), "no such target"),
        (("→ uses b1 [x]", "→ uses b1 [y]"), "no part [y]"),
        (("⟦done b3⟧", ""), "not done: b3"),
        (("→ justifies b3", "→ about b3"), "a group is a source for justifies only"),
        (("scope:paper", "scope:after-b3"), "no such target"),
        (("→ uses b1 [x] position", "→ leans b1 [x] position"), "no such kind"),
        (('Let $x$ be one"', 'Let $y$ be one"'), "part b1 [x]: not verbatim"),
        (
            ('→ self · "with its own reason"', '→ uses b3 · "with its own reason"'),
            "a second edge for one source, kind and target",
        ),
        (("→ about b1 where-used", "→ uses b1 where-used"), "marks an about edge only"),
        (('⊢ self · "A remark on the claim"', ""), "no support line: b4"),
        (
            ('⊢ self · "A remark on the claim"', '⊢ justified · "with its own reason"'),
            "no justifies edge comes in",
        ),
        (('→ self · "with its own reason"\n', ""), "no self mark on the block"),
        (
            ('⊢ self · "A remark on the claim"', '⊢ restated · "with its own reason"'),
            "no about edge goes out",
        ),
        (
            ('⊢ self · "A remark on the claim"', '⊢ proven · "with its own reason"'),
            "no such status",
        ),
        (
            ('⊢ self · "A remark on the claim"', '⊢ self · "with its own reasons"'),
            "claim not in b4",
        ),
        (
            ('→ self · "with its own reason"', '→ self · "A remark on the claim"'),
            "b4 ⊢ self: its self mark quotes the claim, not its reason",
        ),
        (
            ("⟦done g1⟧", '⊢ justified · "Since $x$ is one"\n⟦done g1⟧'),
            "a support line stands under a block, not a group",
        ),
        (("→ qualifies b1 [x]", "→ qualifies g1"), "never a group"),
        (("→ qualifies b1 [x]", "→ qualifies scope:paper"), "a block or a part of one"),
        (
            ("⟦b5 assumption⟧", "⟦b5 definition⟧"),
            "from a block with an assumption role",
        ),
        (('"The claim" (definition)', '"The claim" (theorem)'), "no such role"),
        (('\n  b1 [x]: "Let', '\nb1 [x]: "Let'), "not read as a legend line"),
        (('\n  b1 [x]: "Let', '\nb1 [x]: "Let'), "a header line, two spaces"),
        (
            ("\n  b1 [x]:", "\nPervasive: b1, b9\n\n  b1 [x]:"),
            "Pervasive: b9 is not a block",
        ),
        (
            ("\n  b1 [x]:", "\nPervasive: g1\n\n  b1 [x]:"),
            "Pervasive: g1 is not a block",
        ),
    ],
)
def test_the_gate_names_what_is_wrong(change, problem):
    old, new = change
    assert old in SYNTHETIC
    problems = check(SYNTHETIC.replace(old, new, 1))
    assert any(problem in p for p in problems), problems


@pytest.mark.parametrize("cite", ["\\cite", "\\citep", "\\textcite", "\\parencite"])
def test_a_self_mark_holding_its_claim_passes_with_a_citation(cite):
    """A citation is a reason even where the mark's words hold the claim:
    any citation command counts, biblatex's as well."""
    sheet = SYNTHETIC.replace(
        "A remark on the claim, with its own reason.",
        f"A remark on the claim {cite}{{key}}, with its own reason.",
    ).replace(
        '→ self · "with its own reason"',
        f'→ self · "A remark on the claim {cite}{{key}}"',
    )
    assert check(sheet) == []


def test_one_reason_on_the_block_lets_each_self_standing_pass():
    """The check tests the gross form only: a self standing whose claim a
    mark quotes passes while another mark on the block gives a reason."""
    sheet = SYNTHETIC.replace(
        '→ self · "with its own reason"',
        '→ self · "with its own reason"\n→ self · "A remark on the claim"',
    ).replace(
        '⊢ self · "A remark on the claim"',
        '⊢ self · "A remark on the claim"\n'
        '⊢ self · "on the claim, with its own reason"',
    )
    assert check(sheet) == []


def test_a_standing_on_a_block_with_no_claim_role_passes_and_adds_it():
    """The edges' role correction: a support line on a block whose
    roles hold no claim passes the gate, and `standings_without_claim` names the block
    with its lines; a block with a claim role is named by none."""
    text = SYNTHETIC.replace(
        "⟦done b1⟧",
        '→ self · "Let $x$"\n⊢ self · "Let $x$ be one."\n⟦done b1⟧',
        1,
    )
    assert check(text) == []
    found = standings_without_claim(parse(text))
    assert list(found) == ["b1"]
    assert [s.claim for s in found["b1"]] == ["Let $x$ be one."]
    assert standings_without_claim(parse(SYNTHETIC)) == {}


def test_a_standing_on_a_block_with_no_claim_keeps_its_other_checks():
    text = SYNTHETIC.replace("⟦done b1⟧", '⊢ self · "Let $x$ be one."\n⟦done b1⟧', 1)
    assert any("b1 ⊢ self: no self mark on the block" in p for p in check(text))
    words = SYNTHETIC.replace(
        "⟦done b1⟧", '⊢ conjectured · "Let $y$ be one."\n⟦done b1⟧', 1
    )
    assert any("claim not in b1" in p for p in check(words))


def test_a_standing_adds_no_claim_where_a_person_gave_the_roles():
    """A block whose roles a person pinned (`claims_fixed`) takes no claim
    role from a standing: the gate refuses it, as before."""
    text = SYNTHETIC.replace(
        "⟦done b1⟧", '⊢ conjectured · "Let $x$ be one."\n⟦done b1⟧', 1
    )
    assert check(text) == []
    found = check(text, claims_fixed={"b1"})
    assert any(
        "b1 ⊢ conjectured: a support line on a block with no claim: a person gave "
        "its roles" in p
        for p in found
    ), found
    assert check(SYNTHETIC, claims_fixed={"b3"}) == []


def test_a_first_pass_sheet_without_standings_passes():
    """A sheet that records no standing is a first pass, and passes until
    the standings are carried over."""
    first_pass = "\n".join(
        line for line in SYNTHETIC.split("\n") if not line.startswith("⊢ ")
    )
    assert check(first_pass) == []


#: A law whose box is a definition, its claim stated outside the box twice:
#: as written, both restate the box, which holds no claim.
LAW = """# a law

  box [mass]: "the mass of a state"

---

⟦box definition⟧ paper.md:1:1 (prose)
Let the mass of a state be the sum of its parts.
⟦done box⟧

⟦law claim⟧ paper.md:3:1 (prose)
Hence our rule: heavy states are likely.
→ about box · "Hence our rule"
⊢ restated · "heavy states are likely"
⟦done law⟧

⟦words claim⟧ paper.md:5:1 (prose)
Put plainly: heavy states are likely.
→ about box · "Put plainly"
⊢ restated · "heavy states are likely"
⟦done words⟧

⟦both claim⟧ paper.md:7:1 (prose)
So heavy states are likely, and light states are rare.
→ about law · "So heavy states are likely"
⊢ restated [common] · "heavy states are likely"
⊢ unsupported [rare] · "light states are rare"
⟦done both⟧
"""
POINTS = "no about edge of it points at a claim"
SEVERAL = "its block holds several claims, and no about edge names this one"


def _law(*changes: tuple[str, str]) -> list[str]:
    text = LAW
    for old, new in changes:
        assert old in text, old
        text = text.replace(old, new)
    return check(text)


def test_a_restatement_of_a_definition_restates_no_claim():
    """The law's box is a definition: the claim stated outside it is the
    original, not a restatement; the gate refuses each standing restating
    the box, and of a block of two claims, an about line naming none."""
    assert _law() == [
        f"law ⊢ restated: {POINTS}: a definition, an assumption, a heading or a "
        "proof holds none: point it at the claim it restates, or give the claim "
        "the standing its own words have (justified, self, unsupported)",
        f"words ⊢ restated: {POINTS}: a definition, an assumption, a heading or a "
        "proof holds none: point it at the claim it restates, or give the claim "
        "the standing its own words have (justified, self, unsupported)",
        f"both ⊢ restated: {SEVERAL} at its source end (`→ about [<handle>] T`)",
    ]


def test_the_law_stated_outside_its_box_is_the_original_the_rest_restate_it():
    """The law stands unsupported and uses the box; its other statements
    restate the law, one naming its claim at its source end."""
    assert (
        _law(
            (
                '→ about box · "Hence our rule"\n⊢ restated',
                '→ uses box [mass] · "Hence our rule"\n⊢ unsupported',
            ),
            ('→ about box · "Put plainly"', '→ about law · "Put plainly"'),
            ('→ about law · "So', '→ about [common] law · "So'),
        )
        == []
    )


#: A proof of the rule, a group a restatement may point at.
PROOF_OF_RULE = """
⟦group g1 proof⟧

⟦step justification⟧ paper.md:9:1 (prose)
Since each mass adds up, the rule holds.
⟦done step⟧

⟦end group g1⟧
⟦done g1⟧
"""


@pytest.mark.parametrize(
    ("target", "legend", "tail"),
    [
        ("box", "", ""),
        ("law [rule]", '  law [rule]: "Hence our rule" (definition)\n', ""),
        ("scope:paper", "", ""),
        ("file:paper.md", "", ""),
        ("g1", "", PROOF_OF_RULE),
    ],
    ids=["a definition", "a part only", "the paper", "a file", "a group"],
)
def test_a_restatement_points_at_a_block_holding_a_claim(target, legend, tail):
    """A definition, a line naming only a part's content, the paper, a
    file and a proof's group hold no claim: the words restate nothing
    there."""
    box = '  box [mass]: "the mass of a state"\n'
    found = _law(
        (box, box + legend),
        ('→ about box · "Put plainly"', f'→ about {target} · "Put plainly"'),
        ("⟦done both⟧\n", "⟦done both⟧\n" + tail),
    )
    assert any(p.startswith(f"words ⊢ restated: {POINTS}") for p in found), found
    assert not any("no such target" in p for p in found), found


def test_a_restatement_names_its_claim_where_its_block_holds_several():
    """Named at its source end, the line restates that claim; a line naming
    another claim of the block restates none of this one."""
    named = _law(("→ about law · ", "→ about [common] law · "))
    assert not any(p.startswith("both ⊢") for p in named)
    other = _law(("→ about law · ", "→ about [rare] law · "))
    assert any(p.startswith("both ⊢ restated") for p in other)


def test_an_about_line_says_it_restates_none_of_its_block_s_claims():
    """`[-]` is no handle: the line restates none of both's claims, so its
    restatement still has no line of its own; beside a handle, or on a line
    of another kind, it is refused."""
    none = _law(("→ about law · ", "→ about [-] law · "))
    assert (
        "both ⊢ restated: no about edge goes out for it: each names another "
        "claim or restates none ([-])"
    ) in none
    assert not any("handle" in p for p in none), none
    beside = _law(("→ about law · ", "→ about [-, common] law · "))
    assert any("[-] stands alone in its bracket" in p for p in beside), beside
    uses = _law(('→ about box · "Hence', '→ uses [-] box [mass] · "Hence'))
    assert any("no other line takes it" in p for p in uses), uses


def test_a_line_that_restates_none_is_recorded_so():
    text = LAW.replace("→ about law · ", "→ about [-] law · ")
    found = records(text, {h: h for h in ("box", "law", "words", "both")})
    (line,) = [e for e in found.edges if e.source == "both"]
    assert (line.restates_none, line.source_claims) == (True, ())


def test_a_sheet_on_another_cut_is_refused():
    problems = check(SYNTHETIC, ["paper.md:1:1", "paper.md:3:1"])
    assert any("not the cut's" in p for p in problems)


def test_a_handle_is_the_label_or_the_first_words():
    assert handle_for("## The deviation bound") == "the-deviation-bound"
    assert (
        handle_for("\\begin{proof}\nFix $t > 0$ and let $\\lambda > 0$.")
        == "fix-t-0-and"
    )
    title = "\\begin{proof}[Proof of Lemma~\\ref{lem:gain-bound}]\nIf $a = b$"
    assert handle_for(title) == "proof-of-lemma-lem:gain-bound"
    assert (
        handle_for("Let $X_1, \\dots, X_n$ be ⟦+ claim⟧ integrable") == "let-x_1-x_n-be"
    )
    assert (
        handle_for("\\begin{theorem}\n⟦…⟧\nUnder", ["thm:chernoff"]) == "thm:chernoff"
    )
    labelled = "\\begin{proof}\n\\label{proof:main}Let $x$ be one"
    assert handle_for(labelled) == "let-x-be-one"
    assert handles(["A remark.", "A remark.", "Other."]) == [
        "a-remark",
        "a-remark-2",
        "other",
    ]


# --- the render -------------------------------------------------------------------

BLOCKS = [
    SheetBlock("p.md:1:1", "heading", ("# Setting",)),
    SheetBlock(
        "p.md:3:1",
        "statement",
        ("\\begin{theorem}", "Let $x$ be one. Then the bound holds.\n\\end{theorem}"),
        (("", "definition"), ("Then the bound", "claim")),
        ("thm:bound",),
    ),
    SheetBlock("p.md:7:1", "prose", ("First, $x$ is one.",), (("", "justification"),)),
    SheetBlock("p.md:9:1", "prose", ("So it holds.",), (("", "justification"),)),
    SheetBlock("p.md:11:1", "prose", ("Unsegmented.",), roles=("claim", "motivation")),
]
GROUPS = [SheetGroup("proof", ("p.md:7:1", "p.md:9:1"), "proof:bound")]


def test_render_writes_blocks_segments_runs_groups_and_files() -> None:
    text = render(BLOCKS, GROUPS, ["main.tex", "refs.bib"], "# a paper\n")

    assert text.startswith("# a paper\n\nFiles: main.tex, refs.bib\n\n---\n\n")
    assert "⟦setting none⟧ p.md:1:1 (heading)\n# Setting\n" in text
    assert (
        "⟦thm:bound definition+claim⟧ p.md:3:1 (statement)\n\\begin{theorem}\n⟦…⟧\n"
        "Let $x$ be one. ⟦+ claim⟧ Then the bound holds." in text
    )
    assert "⟦group proof:bound proof⟧\n\n⟦first-x-is-one justification⟧" in text
    assert "So it holds.\n\n⟦end group proof:bound⟧\n\n⟦unsegmented" in text
    assert "⟦unsegmented claim+motivation⟧" in text
    sheet = parse(text)
    assert sheet.block_ids == [b.id for b in BLOCKS]
    assert sheet.groups == {"proof:bound": ["first-x-is-one", "so-it-holds"]}
    assert sheet.files == ("main.tex", "refs.bib")
    assert all(p.startswith(UNFINISHED) for p in check(text, sheet.block_ids))


def test_a_group_without_a_label_is_named_by_its_first_block() -> None:
    text = render(BLOCKS, [SheetGroup("proof", GROUPS[0].blocks)], [], "")

    assert "⟦group group:first-x-is-one proof⟧" in text


def test_a_segment_start_that_does_not_place_once_is_refused() -> None:
    block = SheetBlock(
        "p.md:1:1", "prose", ("a b a b",), (("", "claim"), ("a b", "definition"))
    )

    with pytest.raises(RenderError, match="places 2 times"):
        render([block], [], [], "")


def test_a_segment_start_inside_an_earlier_one_s_excerpt_places_on_the_text() -> None:
    # the second part's excerpt runs past the third part's start
    block = SheetBlock(
        "p.md:1:1",
        "prose",
        ("A rule holds \\cite{a} cancels; gains on others \\cite{b}.",),
        (
            ("", "claim"),
            ("\\cite{a} cancels; gains", "justification"),
            ("cancels; gains on", "claim"),
        ),
    )

    text = render([block], [], [], "")

    assert (
        "A rule holds ⟦+ justification⟧ \\cite{a} ⟦+ claim⟧ cancels; gains on "
        "others \\cite{b}." in text
    )


def test_labels_are_read_from_the_extent_and_a_groups_own_is_the_groups() -> None:
    extent = "\\begin{proof}\\label{proof:x}\nLet \\label{eq:one} hold."

    assert group_label(extent) == "proof:x"
    assert group_label("Plain \\label{eq:one}") is None
    assert labels_in(extent, "proof:x") == ["eq:one"]
    assert labels_in(extent) == ["proof:x", "eq:one"]


def test_the_paper_s_files_leave_out_hidden_files_and_the_never_included(
    tmp_path: Path,
) -> None:
    for name in ("paper.md", "LICENCE-NOTE.md", ".hidden", "fig.pdf"):
        (tmp_path / name).write_text("x")
    (tmp_path / "figures").mkdir()

    assert paper_files(tmp_path, ["LICENCE-NOTE.md"]) == ["fig.pdf", "paper.md"]


def test_a_part_s_role_is_its_legend_role_else_its_segment_s() -> None:
    sheet = parse(SYNTHETIC)

    assert part_role(sheet, "b3", "the claim") == "definition"
    assert part_role(sheet, "b1", "x") == "definition"
    edited = SYNTHETIC.replace("⟦b1 definition⟧", "⟦b1 assumption⟧")
    assert part_role(parse(edited), "b1", "x") == "assumption"


def test_an_excerpt_across_a_run_break_is_refused() -> None:
    sheet = SYNTHETIC.replace(
        "Let $x$ be one.\n⟦done b1⟧", "Let $x$ be one.\n⟦…⟧\nAnd two.\n⟦done b1⟧"
    ).replace("⟦done b1⟧", '→ uses b4 · "one. ⟦…⟧ And"\n⟦done b1⟧', 1)

    problems = check(sheet)

    assert any("it spans two blocks or a ⟦…⟧" in p for p in problems), problems


# --- cut lines -------------------------------------------------------

CUTS = """# a sheet with cut lines

---

⟦b1 claim⟧ paper.md:1:1 (prose)
We define $x$ as one. Hence the bound holds.
{b1}
⟦done b1⟧

⟦b2 definition⟧ paper.md:3:1 (prose)
The display
{b2}
⟦done b2⟧

⟦b3 definition⟧ paper.md:5:1 (prose)
below shows it.
{b3}
⟦done b3⟧

⟦b4 none⟧ other.md:1:1 (prose)
Another file.
⟦done b4⟧
"""


def cuts(b1: str = "", b2: str = "", b3: str = "") -> str:
    return CUTS.format(b1=b1, b2=b2, b3=b3).replace("\n\n⟦done", "\n⟦done")


def test_cut_lines_parse_and_pass() -> None:
    text = cuts(
        b1='✂ split "Hence the bound" · "Hence the bound holds" — b5 uses it',
        b2='✂ merge b3 · "The display" — one display',
    )
    assert check(text) == []
    split, merge = parse(text).cuts
    assert (split.source, split.op, split.target, split.at) == (
        "b1",
        "split",
        "",
        "Hence the bound",
    )
    assert (split.evidence, split.why) == ("Hence the bound holds", "b5 uses it")
    assert (merge.source, merge.op, merge.target, merge.at) == ("b2", "merge", "b3", "")
    assert merge.evidence == "The display"


def test_a_move_and_two_splits_on_one_block_pass() -> None:
    text = cuts(
        b1='✂ split "as one." · "We define" — a definition\n'
        '✂ split "Hence the bound" · "Hence" — a claim',
        b2='✂ move b3 "display" · "below shows it" — the display is one',
    )
    assert check(text) == []
    assert [c.op for c in parse(text).cuts] == ["split", "split", "move"]


@pytest.mark.parametrize(
    ("lines", "problem"),
    [
        ({"b1": "✂ chop b2"}, "not a cut line"),
        ({"b1": '✂ split "We define" · "We define" — r'}, "the start of b1"),
        ({"b1": '✂ split "nowhere" · "We define" — r'}, "stand 0 times in b1"),
        ({"b1": '✂ split "e" · "We define" — r'}, "times in b1, not once"),
        ({"b1": '✂ split "Hence" · "not there" — r'}, "evidence not in b1"),
        ({"b1": '✂ split "Hence" · "Hence"'}, "give the reason"),
        ({"b1": '✂ split b2 "Hence" · "Hence" — r'}, "a split names no block"),
        ({"b1": '✂ merge b3 · "We define" — r'}, "names the next block, here b2"),
        ({"b3": '✂ merge b4 · "below" — r'}, "b3 is the last of its file"),
        ({"b2": '✂ merge b3 "below" · "below" — r'}, "a merge takes no words"),
        ({"b2": '✂ merge b3 · "below shows it" — r'}, None),
        ({"b2": '✂ move b3 "below" · "below" — r'}, "b3 starts there already"),
        ({"b2": '✂ move b3 "The display" · "below" — r'}, "the start of b2"),
        (
            {"b1": '✂ split "Hence" · "Hence" — r\n✂ merge b2 · "Hence" — r'},
            "b1: one or more splits, or one merge or move",
        ),
    ],
)
def test_each_misplaced_cut_line_is_a_problem(lines, problem) -> None:
    found = check(cuts(**lines))
    if problem is None:
        assert found == []
    else:
        assert any(problem in p for p in found), found


def test_a_cut_line_under_a_group_is_a_problem() -> None:
    text = SYNTHETIC.replace(
        "⟦end group g1⟧\n", '⟦end group g1⟧\n✂ split "Since" · "Since" — r\n'
    )
    assert any("stands under a block, not a group" in p for p in check(text))


def test_a_merge_passes_over_a_block_nested_in_its_source() -> None:
    """A footnote nested in b2 stands between it and b3 in the sheet; the
    assembler merges b2 and b3 over it, and so does the check."""
    text = cuts(b2='✂ merge b3 · "The display" — one display')
    nested = text.replace(
        "⟦b3 definition⟧",
        "⟦fn none⟧ paper.md:4:1 (footnote)\nA note.\n⟦done fn⟧\n\n⟦b3 definition⟧",
    )

    assert any("names the next block, here fn" in p for p in check(nested))
    assert check(nested, within={"fn": "b2"}) == []


def test_a_merge_or_move_across_a_group_s_edge_is_a_problem() -> None:
    grouped = cuts().replace("⟦b2 definition⟧", "⟦group g proof⟧\n\n⟦b2 definition⟧")
    grouped = grouped.replace("⟦done b2⟧\n", "⟦done b2⟧\n\n⟦end group g⟧\n⟦done g⟧\n")
    assert check(grouped) == []

    for line in ('✂ merge b2 · "We define" — r', '✂ move b2 "Hence" · "We define" — r'):
        found = check(grouped.replace("⟦done b1⟧", f"{line}\n⟦done b1⟧"))
        assert any("on either side of group g's edge" in p for p in found), found


def test_a_move_s_words_across_a_run_break_are_a_problem() -> None:
    text = cuts(b2='✂ move b3 "display ⟦…⟧ below" · "The display" — r')
    broken = text.replace("The display\n", "The display\n⟦…⟧\nbelow it\n")
    found = check(broken)
    assert any("the words span a ⟦…⟧" in p for p in found), found


def test_empty_evidence_or_claim_words_are_a_problem() -> None:
    edge = SYNTHETIC.replace('→ uses b3 · "A remark on the claim"', '→ uses b3 · ""')
    support = SYNTHETIC.replace('⊢ self · "A remark on the claim"', '⊢ self · ""')

    assert any("the evidence is empty" in p for p in check(edge))
    assert any("the claim's words are empty" in p for p in check(support))


# --- an update's lines -------------------------------------------

#: SYNTHETIC as an update shows it: b1 changed (its old text under its
#: line), a condition under the edge onto it, and the writer's Δ lines.
UPDATED = (
    SYNTHETIC.replace(
        "⟦b1 definition⟧ paper.md:1:1 (prose)\n",
        "⟦b1 definition⟧ paper.md:1:1 (prose)\n⟦was⟧ Let $x$ be zero.\n",
    )
    .replace(
        '→ uses b1 [x] position · "Since $x$ is one" — a note\n',
        '→ uses b1 [x] position · "Since $x$ is one" — a note\n'
        "⟦meaning moved by rule: uses [x] of b1, if its Δ is not unchanged or "
        "reworded⟧\n",
    )
    .replace(
        "Let $x$ be one.\n⟦done b1⟧",
        "Let $x$ be one.\nΔ [x] narrowed — one value, not zero\n⟦done b1⟧",
    )
    .replace(
        '→ qualifies b1 [x] · "We assume $x$ is known"\n',
        '→ qualifies b1 [x] · "We assume $x$ is known"\n'
        "⟦asked if b1 [x]'s Δ is not unchanged or reworded⟧\n"
        "Δ reworded — the assumption reads as before\n",
    )
)


def test_an_update_s_lines_parse_and_pass() -> None:
    sheet = parse(UPDATED)

    assert check(UPDATED) == []
    assert sheet.was == {"b1": ["Let $x$ be zero."]}
    assert sheet.texts["b1"] == "Let $x$ be one."  # the old text is not the text
    assert [(d.source, d.label, d.cls, d.note) for d in sheet.deltas] == [
        ("b1", "x", "narrowed", "one value, not zero"),
        ("b5", "", "reworded", "the assumption reads as before"),
    ]
    assert [(c.owner, c.handle, c.parts, c.moved) for c in sheet.conditions] == [
        ("b2", "b1", ("x",), True),
        ("b5", "b1", ("x",), False),
    ]
    assert [c.text for c in sheet.conditions] == [
        moved_by_rule("uses", "b1", ("x",)),
        asked_if("b1", ("x",)),
    ]
    # the written lines are as SYNTHETIC's
    assert parse(SYNTHETIC).edges == sheet.edges
    assert counts(UPDATED) == counts(SYNTHETIC)


def test_the_eight_classes_are_the_records_impact_classes() -> None:
    from axiomation.records import ImpactClass

    assert tuple(c.value for c in ImpactClass) == IMPACT_CLASSES


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        (("Δ [x] narrowed", "Δ [x] rephrased"), "no such class"),
        (("Δ [x] narrowed — one value, not zero", "Δ [x] narrowed —"), "say how"),
        (("Δ [x] narrowed — one value, not zero", "Δ [x] narrowed"), "say how"),
        (("Δ [x] narrowed", "Δ [y] narrowed"), "b1 holds no part [y]"),
        (("Δ reworded", "Δ [x] reworded"), "b5 holds no part [x]"),
        (
            ("⟦done g1⟧", "Δ reworded — the proof\n⟦done g1⟧"),
            "a Δ line stands under a block, not a group",
        ),
        (
            (
                "Δ [x] narrowed — one value, not zero",
                "Δ [x] narrowed — a\nΔ [x] widened — b",
            ),
            "a second Δ line for one part",
        ),
        (("Δ [x] narrowed — one value, not zero", "Δ"), "not a Δ line"),
        (
            ("⟦done g1⟧", "⟦was⟧ The proof before\n⟦done g1⟧"),
            "a ⟦was⟧ line stands under a block's line",
        ),
    ],
)
def test_the_gate_names_a_wrong_update_line(change, problem) -> None:
    old, new = change
    assert old in UPDATED
    problems = check(UPDATED.replace(old, new, 1))
    assert any(problem in p for p in problems), problems


# --- a justification's completeness --------------------------------------

#: SYNTHETIC with each new word: the group's justifies edge a sketch, b4's
#: self mark an analogy (b4 uses g1, the proof it carries over, beside b3,
#: the result it names), and an unsupported claim b6 the paper declares
#: omitted.
PARTIAL = (
    SYNTHETIC.replace(
        '→ justifies b3 · "\\begin{proof}"', '→ justifies b3 sketch · "\\begin{proof}"'
    )
    .replace('→ self · "with its own reason"', '→ self analogy · "with its own reason"')
    .replace(
        '→ uses b3 · "A remark on the claim"\n',
        '→ uses b3 · "A remark on the claim"\n→ uses g1 · "A remark on the claim"\n',
    )
    + """
⟦b6 claim⟧ paper.md:13:1 (prose)
The bound is sharp, as is standard.
⊢ unsupported omitted · "The bound is sharp" — "as is standard"
⟦done b6⟧
"""
)


def test_each_partial_and_declaration_parses_and_passes():
    assert check(PARTIAL) == []
    sheet = parse(PARTIAL)
    by = {(e.source, e.kind): e for e in sheet.edges}
    assert by[("g1", "justifies")].partial == "sketch"
    assert by[("b4", "self")].partial == "analogy"
    assert by[("b4", "self")].target == ""
    assert by[("b2", "uses")].partial == ""
    assert {s.source: s.declared for s in sheet.supports} == {
        "b3": "",
        "b4": "",
        "b6": "omitted",
    }


@pytest.mark.parametrize("word", ["sketch", "analogy"])
def test_a_self_line_s_partial_is_never_read_as_a_target(word):
    sheet = parse(PARTIAL.replace("self analogy", f"self {word}"))
    (mark,) = [e for e in sheet.edges if e.kind == "self"]
    assert (mark.target, mark.partial) == ("", word)


def test_the_records_carry_the_partials_and_the_declaration():
    from axiomation.questions.edges_file import records
    from axiomation.records import Declared, Partial

    handles = {h: h for h in parse(PARTIAL).texts}
    found = records(PARTIAL, handles)
    assert [e.partial for e in found.edges if e.kind.value == "justifies"] == [
        Partial.SKETCH
    ]
    assert [m.partial for m in found.selfs] == [Partial.ANALOGY]
    assert [s.declared for s in found.standings] == [None, None, Declared.OMITTED]


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        (
            ("→ uses b1 [x] position", "→ uses b1 [x] position sketch"),
            "sketch marks a justifies edge or a self mark only",
        ),
        (
            ("→ justifies b3 sketch", "→ justifies b3 sketch analogy"),
            "one of sketch or analogy, not sketch and analogy",
        ),
        (
            (
                '⊢ self · "A remark on the claim"',
                '⊢ self omitted · "A remark on the claim"',
            ),
            "b4 ⊢ self: omitted is declared on an unsupported standing only",
        ),
        (
            ("⊢ unsupported omitted", "⊢ unsupported forgotten"),
            "b6 ⊢ unsupported: no such declaration 'forgotten'",
        ),
        (
            ('→ uses g1 · "A remark on the claim"\n', ""),
            "b4 → self: an analogy names the argument it carries over",
        ),
    ],
)
def test_the_gate_refuses_a_misplaced_partial_or_declaration(change, problem):
    old, new = change
    assert old in PARTIAL
    problems = check(PARTIAL.replace(old, new, 1))
    assert any(problem in p for p in problems), problems


def test_an_analogy_naming_its_argument_by_citation_passes_in_its_quote_only():
    """Without a uses edge onto an argument, an analogy passes on a citation
    in its own quote; one elsewhere in its block names no argument carried
    over."""
    unnamed = PARTIAL.replace('→ uses g1 · "A remark on the claim"\n', "")
    cited = unnamed.replace(
        "A remark on the claim, with its own reason.",
        "A remark on the claim, with its own reason \\citep{k}.",
    )
    assert any("an analogy names the argument" in p for p in check(cited))
    quoted = cited.replace(
        '→ self analogy · "with its own reason"',
        '→ self analogy · "with its own reason \\citep{k}"',
    )
    assert check(quoted) == []


#: SYNTHETIC with a closing analogy in the proof: b2 argues the case it
#: writes and declares the rest similar, its analogy onto b3 beside the
#: group's edge.
CLOSING = SYNTHETIC.replace(
    "Since $x$ is one, the claim holds.",
    "Since $x$ is one, the claim holds. The rest is similar.",
).replace(
    '→ uses b1 [x] position · "Since $x$ is one" — a note',
    '→ uses b1 [x] position · "Since $x$ is one" — a note\n'
    '→ justifies b3 analogy · "The rest is similar"',
)


def test_a_closing_analogy_names_the_proof_it_closes():
    """The group an analogy sits in names nothing by itself: the analogy's
    block uses the group, the argument it closes."""
    assert any("an analogy names the argument" in p for p in check(CLOSING))
    named = CLOSING.replace(
        '→ justifies b3 analogy · "The rest is similar"',
        '→ justifies b3 analogy · "The rest is similar"\n'
        '→ uses g1 position · "The rest is similar"',
    )
    assert check(named) == []


@pytest.mark.parametrize(
    ("onto", "passes"),
    [
        ("g1", True),  # a group
        ("b2", True),  # a block with a justification role
        ("b4", True),  # a block carrying a self mark
        ("b3", False),  # a result's statement: not the argument
        ("b1", False),  # a definition
        ("b4 [term]", False),  # a self-marked block's defined words only
    ],
)
def test_an_analogy_carries_an_argument_never_a_statement(onto, passes):
    """What an analogy carries over is a proof (a group, a justification, a
    justifies edge's source) or a block arguing its own claim; an edge
    naming only a part that defines relies on the term, not the argument."""
    sheet = PARTIAL.replace(
        '  b3 [the claim]: "The claim" (definition)\n',
        '  b3 [the claim]: "The claim" (definition)\n'
        '  b4 [term]: "A remark" (definition)\n',
    ).replace(
        "⟦b6 claim⟧",
        "⟦b7 claim⟧ paper.md:15:1 (prose)\nAs before, so here.\n"
        f'→ uses {onto} · "As before"\n'
        '→ self analogy · "As before"\n'
        '⊢ self · "so here"\n'
        "⟦done b7⟧\n\n⟦b6 claim⟧",
    )
    found = [p for p in check(sheet) if p.startswith("b7 ")]
    if passes:
        assert found == [], found
    else:
        assert any("an analogy names the argument" in p for p in found), found


def test_a_self_mark_s_analogy_carries_the_case_its_block_writes_out():
    """A block argues one claim itself and declares its other likewise: its
    unflagged self mark beside the analogy is what it carries over, the
    block itself, already the reading's subject."""
    sheet = PARTIAL.replace(
        "⟦b6 claim⟧",
        "⟦b7 claim⟧ paper.md:15:1 (prose)\n"
        "First f is small, since x is one. Then g is small likewise.\n"
        '→ self [first-f-is-small] · "since x is one"\n'
        '→ self [then-g-is-small] analogy · "likewise"\n'
        '⊢ self · "First f is small"\n'
        '⊢ self · "Then g is small"\n'
        "⟦done b7⟧\n\n⟦b6 claim⟧",
    )
    assert [p for p in check(sheet) if p.startswith("b7 ")] == []
    alone = sheet.replace('→ self [first-f-is-small] · "since x is one"\n', "")
    found = [p for p in check(alone) if p.startswith("b7 ")]
    assert any("an analogy names the argument" in p for p in found), found


def test_a_group_s_analogy_carries_nothing_of_its_own_steps():
    """A group whose analogy names only its own steps carries no argument
    over: the uses edges among its steps are its own."""
    grouped = PARTIAL.replace(
        "⟦end group g1⟧",
        "⟦b8 justification⟧ paper.md:5:1 (prose)\nAs before, so here.\n"
        '→ uses b2 position · "As before"\n'
        "⟦done b8⟧\n\n⟦end group g1⟧",
    ).replace(
        '→ justifies b3 sketch · "\\begin{proof}"',
        '→ justifies b3 analogy · "\\begin{proof}"',
    )
    found = [p for p in check(grouped) if p.startswith("g1 ")]
    assert any("an analogy names the argument" in p for p in found), found


# --- a claim named by its handle ---------------------------------

HANDLED = """# a sheet naming claims by handle

---

⟦b1 definition⟧ paper.md:1:1 (prose)
Let $f$ be a gain.
⟦done b1⟧

⟦t claim⟧ paper.md:3:1 (prose)
Then f is bounded. Also f is even.
⊢ justified [bounded] · "Then f is bounded."
⊢ justified · "Also f is even."
⟦done t⟧

⟦p justification⟧ paper.md:5:1 (prose)
By the gain, f is bounded.
→ uses b1 · "By the gain"
→ justifies t [bounded] · "By the gain, f is bounded."
⟦done p⟧

⟦q justification⟧ paper.md:7:1 (prose)
By symmetry, f is even.
→ justifies t [also-f-is-even] analogy · "By symmetry, f is even."
→ uses p · "By symmetry"
⟦done q⟧

⟦s claim⟧ paper.md:9:1 (prose)
So f is small since f is bounded, and f is tiny by the gain.
→ uses t [bounded] · "since f is bounded"
→ uses b1 · "by the gain"
→ self [small] · "since f is bounded"
→ self [f-is-tiny] sketch · "by the gain"
⊢ self [small] · "So f is small"
⊢ self · "f is tiny"
⟦done s⟧
"""


#: HANDLED with one block, p, writing t's first claim out and declaring its
#: second likewise: two justifies lines from p onto t, one per claim.
ONE_BLOCK = (
    HANDLED.replace(
        "By the gain, f is bounded.\n",
        "By the gain, f is bounded; f is even likewise.\n",
    )
    .replace(
        '→ justifies t [bounded] · "By the gain, f is bounded."',
        '→ justifies t [bounded] · "By the gain, f is bounded"\n'
        '→ justifies t [also-f-is-even] analogy · "f is even likewise"',
    )
    .replace(
        '→ justifies t [also-f-is-even] analogy · "By symmetry, f is even."\n'
        '→ uses p · "By symmetry"\n',
        "",
    )
)


def test_a_closing_analogy_in_one_block_carries_the_case_it_writes_out():
    """One block writes one claim's case and declares the other similar: two
    lines onto the target, one per claim, the unflagged one naming what the
    analogy carries over, the block itself, already the reading's J."""
    assert check(ONE_BLOCK) == []
    alone = ONE_BLOCK.replace(
        '→ justifies t [bounded] · "By the gain, f is bounded"\n', ""
    )
    assert any("p → justifies t: an analogy names" in p for p in check(alone))


@pytest.mark.parametrize(
    ("second", "passes"),
    [
        ("[also-f-is-even]", True),  # another claim
        ("[bounded]", False),  # the same claim
        ("[bounded, also-f-is-even]", False),  # a shared one
        ("", False),  # none named: it counts for each
    ],
)
def test_two_justifies_lines_onto_one_target_name_disjoint_claims(second, passes):
    sheet = ONE_BLOCK.replace(
        '→ justifies t [also-f-is-even] analogy · "f is even likewise"',
        f'→ justifies t {second} analogy · "f is even likewise"'.replace("  ", " "),
    )
    found = [p for p in check(sheet) if "a second edge for one source" in p]
    assert (found == []) is passes, found


def test_a_line_names_a_claim_by_the_handle_its_line_defines_or_mints() -> None:
    """A ⊢ line's handle is the one it defines, else one minted from its
    words; a bracket names the claim by it, and the records carry the
    claim's words, never the handle."""
    from axiomation.questions.edge_sheet import claim_handles
    from axiomation.questions.edges_file import records

    assert check(HANDLED) == []
    sheet = parse(HANDLED)
    assert claim_handles(sheet, "t") == {
        "bounded": "Then f is bounded.",
        "also-f-is-even": "Also f is even.",
    }
    found = records(HANDLED, {h: h for h in sheet.texts})
    named = {
        (e.source, e.kind.value, e.target.name): (e.parts, e.claims)
        for e in found.edges
    }
    assert named[("p", "justifies", "t")] == ((), ("Then f is bounded.",))
    assert named[("q", "justifies", "t")] == ((), ("Also f is even.",))
    assert named[("s", "uses", "t")] == ((), ("Then f is bounded.",))
    assert named[("p", "uses", "b1")] == ((), ())
    assert [m.claims for m in found.selfs] == [("So f is small",), ("f is tiny",)]


def test_a_minted_handle_steps_around_a_defined_one() -> None:
    """Defined handles first, minted ones around them: a claim whose words
    mint the handle another defines takes a suffix."""
    from axiomation.questions.edge_sheet import claim_handles

    text = HANDLED.replace("⊢ justified [bounded]", "⊢ justified [also-f-is-even]")
    text = text.replace("t [bounded]", "t [also-f-is-even]")
    assert claim_handles(parse(text), "t") == {
        "also-f-is-even": "Then f is bounded.",
        "also-f-is-even-2": "Also f is even.",
    }


def test_a_prepared_sheet_shows_each_claim_s_handle_where_a_block_holds_several():
    """The product writes the minted handle on each ⊢ line that defines
    none, on a block of two or more claims only, the same each time."""
    from axiomation.questions.edge_sheet import with_handles

    shown = with_handles(HANDLED)
    assert '⊢ justified [also-f-is-even] · "Also f is even."' in shown
    assert '⊢ self [f-is-tiny] · "f is tiny"' in shown
    assert '⊢ justified [bounded] · "Then f is bounded."' in shown
    assert with_handles(shown) == shown
    one = HANDLED.replace('⊢ justified · "Also f is even."\n', "").replace(
        '⊢ self · "f is tiny"\n', ""
    )
    assert with_handles(one) == one
    assert check(shown) == []


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        (
            ("→ justifies t [bounded]", "→ justifies t [nope]"),
            "no part [nope] in the legend",
        ),
        (
            ('⊢ justified · "Also f is even."', '⊢ unsupported · "Also f is even."'),
            "[also-f-is-even] is unsupported; a justifies line names a justified claim",
        ),
        (
            ('→ uses b1 · "By the gain"', '→ uses [bounded] b1 · "By the gain"'),
            "no part [bounded] of p in the legend",
        ),
        (
            ("→ uses t [bounded]", "→ uses [bounded] t"),
            "[bounded] is t's: a target's part or claim goes in the bracket after "
            "the target",
        ),
        (
            ("→ self [small]", "→ self [nope]"),
            "[nope] is no claim's handle on s",
        ),
        (
            (
                '⊢ justified · "Also f is even."',
                '⊢ justified [bounded] · "Also f is even."',
            ),
            "t ⊢ [bounded]: a handle names one claim of its block",
        ),
        (
            ("---\n", '  t [bounded]: "f is bounded" (definition)\n\n---\n'),
            "t [bounded]: a legend label and a claim's handle",
        ),
        (
            ("---\n", '  t [B]: "f is bounded"\n\n---\n'),
            "part t [B]: a claim is named by its handle on its ⊢ line, never by a part",
        ),
        (
            (
                "→ justifies t [bounded]",
                "→ justifies t [B]",
            ),
            "names no claim: name the claim it argues by its handle",
        ),
        (
            ("→ justifies t [also-f-is-even]", "→ justifies t [bounded]"),
            "t ⊢ justified: no justifies edge comes in for it: each names another "
            "claim",
        ),
        (
            ("→ self [f-is-tiny] sketch", "→ self [small] sketch"),
            "s ⊢ self: no self mark on the block for it: each names another claim",
        ),
    ],
)
def test_the_gate_refuses_a_reference_that_does_not_resolve_or_fit(change, problem):
    old, new = change
    assert old in HANDLED
    text = HANDLED.replace(old, new, 1)
    if "[B]" in new and "justifies" in new:
        text = text.replace("---\n", '  t [B]: "f is bounded" (definition)\n\n---\n')
    problems = check(text)
    assert any(problem in p for p in problems), problems


def test_two_claims_of_a_block_never_share_their_words() -> None:
    """A claim is its block and its words, so two standings quoting the
    same words are one claim to every reader, whatever their handles:
    refused, teaching the quote."""
    text = HANDLED.replace(
        '⊢ justified · "Also f is even."', '⊢ justified · "Then f is bounded."'
    )
    problems = check(text)
    assert any(
        "t ⊢ 'Then f is bounded.': two claims of a block quote the same words" in p
        for p in problems
    ), problems


HENRY = """# a synthetic sheet

---

⟦k claim⟧ paper.md:1:1 (prose)
Conjecture: all elephants are red.
⊢ conjectured · "all elephants are red"
⟦done k⟧

⟦d definition⟧ paper.md:3:1 (prose)
Henry is an elephant, assuming the conjecture.
⟦done d⟧

⟦x claim⟧ paper.md:5:1 (prose)
Assuming the conjecture, Henry is red.
→ uses k assumed · "Assuming the conjecture"
⊢ justified · "Henry is red"
⟦done x⟧

⟦b2 justification⟧ paper.md:7:1 (prose)
Henry is an elephant, so by the conjecture he is red.
→ uses k · "by the conjecture"
→ justifies x · "Henry is an elephant"
⟦done b2⟧

⟦y claim⟧ paper.md:9:1 (prose)
Assuming the conjecture, Henry is red and Henry is large.
→ uses [red] k assumed · "Assuming the conjecture"
⊢ unsupported [red] · "Henry is red"
⊢ unsupported [large] · "Henry is large"
⟦done y⟧
"""


def test_an_assumed_flag_reads_on_a_statement_s_uses_edge_onto_a_conjecture():
    """x's statement takes the conjecture as its hypothesis, y's for one of
    its two claims, named: the sheet passes, and the records carry the flag
    and the claims it is a hypothesis for."""
    assert check(HENRY) == []
    flagged = [e for e in parse(HENRY).edges if e.assumed]
    assert [(e.source, e.target, e.own) for e in flagged] == [
        ("x", "k", ()),
        ("y", "k", ("red",)),
    ]
    edges = records(HENRY, {h: h for h in ("k", "d", "x", "b2", "y")}).edges
    assert [
        (e.source, e.assumed, e.source_claims) for e in edges if e.kind == "uses"
    ] == [
        ("x", True, ()),
        ("b2", False, ()),
        ("y", True, ("Henry is red",)),
    ]


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        (
            (
                '→ uses k · "by the conjecture"',
                '→ uses k assumed · "by the conjecture"',
            ),
            "assumed runs from a claim's statement, never a step of an argument",
        ),
        (
            ('→ uses k assumed · "Assuming', '→ about k assumed · "Assuming'),
            "assumed marks a uses edge only",
        ),
        (
            ("→ uses [red] k assumed", "→ uses k assumed"),
            "assumed on a block of several claims names those it is a hypothesis for",
        ),
        (
            ("⟦done d⟧", '→ uses k assumed · "assuming the conjecture"\n⟦done d⟧'),
            "assumed runs from a block holding a claim",
        ),
        (
            ("→ uses [red] k assumed", "→ uses [red] x assumed"),
            "assumed targets `conjectured` claims only",
        ),
        (
            ("⊢ conjectured ·", "⊢ unsupported ·"),
            "assumed targets `conjectured` claims only",
        ),
        (
            ("→ uses [red] k assumed", "→ uses [blue] k assumed"),
            "[blue] is no claim's handle on y",
        ),
    ],
)
def test_an_assumed_flag_out_of_place_is_refused(change, problem):
    old, new = change
    assert old in HENRY
    problems = check(HENRY.replace(old, new, 1))
    assert any(problem in p for p in problems), problems


def test_an_assumed_flag_from_a_proof_s_member_is_refused_from_a_result_taken():
    """From a member of a group that justifies a claim, a step of the
    argument: refused. From a block of claims alone that justifies another,
    a result offered as the reason, its statement's flag stands."""
    grouped = HENRY.replace(
        "⟦b2 justification⟧", "⟦group g1 proof⟧\n\n⟦b2 justification⟧"
    ).replace(
        '→ uses k · "by the conjecture"\n→ justifies x · "Henry is an elephant"\n'
        "⟦done b2⟧",
        '→ uses k assumed · "by the conjecture"\n⟦done b2⟧\n\n⟦end group g1⟧\n'
        '→ justifies x · "Henry is an elephant"\n⟦done g1⟧',
    )
    assert "⟦end group g1⟧" in grouped
    problems = check(grouped)
    assert any("never a step of an argument" in p for p in problems), problems
    result = HENRY.replace(
        '⊢ unsupported [large] · "Henry is large"',
        '⊢ unsupported [large] · "Henry is large"\n→ justifies x · "Henry is red"',
    )
    assert not any("never a step of an argument" in p for p in check(result))


def test_an_assumed_flag_onto_a_block_of_two_conjectures_names_one():
    two = (
        HENRY.replace(
            '⊢ conjectured · "all elephants are red"',
            '⊢ conjectured [red] · "all elephants are red"\n'
            '⊢ conjectured [elephants] · "Conjecture"',
        )
        .replace("→ uses k assumed", "→ uses k [red] assumed", 1)
        .replace("→ uses [red] k assumed", "→ uses [red] k [red] assumed")
    )
    assert not any("assumed" in p for p in check(two)), check(two)
    unnamed = two.replace("→ uses k [red] assumed", "→ uses k assumed", 1)
    assert any("names the conjectures it takes" in p for p in check(unnamed))


def test_only_an_assumed_uses_edge_names_its_own_block_s_claims():
    problems = check(HENRY.replace("→ uses [red] k assumed", "→ uses [red] k"))
    assert any(
        "an assumed uses edge names claims of its own block" in p for p in problems
    )


#: A block of two definitions whose lines name the parts they leave from:
#: g's line onto a1, h's onto a2, and h written in terms of g.
LEAVING = """# a sheet whose lines leave from parts

  d [g]: "Let $g$ be a gain."
  d [h]: "Let $h$ be a loss on $g$."

---

⟦a1 assumption⟧ paper.md:1:1 (prose)
Assume g is even.
⟦done a1⟧

⟦a2 assumption⟧ paper.md:3:1 (prose)
Assume h is odd.
⟦done a2⟧

⟦d definition⟧ paper.md:5:1 (prose)
Let $g$ be a gain. Let $h$ be a loss on $g$.
→ uses [g] a1 · "Let $g$ be a gain."
→ uses [h] a2 · "Let $h$ be a loss"
→ uses [h] d [g] · "a loss on $g$"
⟦done d⟧
"""


def test_a_line_leaving_from_a_part_and_a_sibling_line_pass_and_are_recorded():
    """The bracket after a uses line's kind names the parts of its block it
    leaves from; a uses line from a part onto another part of its block is
    the one line inside a block. The records carry each source part."""
    assert check(LEAVING) == []
    found = records(LEAVING, {h: h for h in parse(LEAVING).texts})
    assert [(e.target.name, e.source_parts, e.parts) for e in found.edges] == [
        ("a1", ("g",), ()),
        ("a2", ("h",), ()),
        ("d", ("h",), ("g",)),
    ]


def test_two_uses_lines_onto_one_target_pass_from_disjoint_parts():
    """Each part's line onto one target stands apart; a part named twice
    is a second line."""
    apart = LEAVING.replace("→ uses [h] a2", "→ uses [h] a1")
    assert check(apart) == []
    shared = LEAVING.replace("→ uses [h] a2", "→ uses [g, h] a1")
    assert any(
        "a second edge for one source, kind and target (two uses lines leave from "
        "disjoint parts of the source)" in p
        for p in check(shared)
    )


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        (
            ("→ uses [g] a1", "→ uses [k] a1"),
            "d → uses a1: no part [k] of d in the legend",
        ),
        (
            ("→ uses [h] d [g]", "→ uses d [g]"),
            "d → uses d: an edge inside its own block; only a uses line runs inside "
            "one, from a part onto another",
        ),
        (
            ("→ uses [h] d [g]", "→ uses [h] d"),
            "d → uses d: an edge inside its own block; only a uses line runs inside "
            "one",
        ),
        (
            ("→ uses [h] d [g]", "→ uses [h] d [h]"),
            "d → uses d: [h] at both ends: a part uses another part",
        ),
        (
            (
                '→ uses [g] a1 · "Let $g$ be a gain."',
                '→ about [g] a1 · "Let $g$ be a gain."',
            ),
            "d → about a1: [g] is no claim's handle on d; a part of d is named at "
            "the source end of a uses line only",
        ),
    ],
)
def test_the_gate_refuses_a_source_part_out_of_place(change, problem):
    old, new = change
    assert old in LEAVING
    problems = check(LEAVING.replace(old, new, 1))
    assert any(problem in p for p in problems), problems
