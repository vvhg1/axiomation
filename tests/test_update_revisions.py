"""Real revision cases through an update's mechanical path:
identity's classes, OLD's cut rebased onto NEW (roles kept, regions re-cut,
groups kept or dropped), the sheet carried, what session 1 reopens and the
rule's conditions, on `test_update`'s synthetic paper. No model is called:
`plan` stops before session 1 is asked."""

from __future__ import annotations

from dataclasses import replace

import pytest

import test_update
from axiomation.assembler import corrected_cut
from axiomation.cli import main
from axiomation.identity import BlockClass
from questions import test_roles_file as roles_file
from questions.review_paper import MAIN, make_paper
from test_update import (
    CLOSING,
    PROOF_BLOCK,
    SETTING,
    SHARP,
    UPDATE,
    WRITTEN,
    build_old,
    plan,
    versions,
)

DEFINITION_ENV = (
    "\\begin{definition}\\label{def:gain}\n"
    "The gain is the ratio of output to input.\n\\end{definition}\n"
)
PROOF_ENV = (
    "\\begin{proof}\n"
    "By Definition~\\ref{def:gain}, the gain is small"
    "\\footnote{Small means below one.}.\n"
    "Hence the output is bounded.\n\nNext, the bound is sharp.\n\\end{proof}\n"
)
SETTING_SECTION = MAIN[
    MAIN.index("\\section{Setting}") : MAIN.index("\\section{Results}")
]
RESULTS_SECTION = MAIN[MAIN.index("\\section{Results}") : MAIN.index("\\input{extra}")]


def holding(cut, document, words: str) -> str:
    """The id of the one block of `cut` whose owned text holds `words`."""
    (found,) = [
        b.id
        for b in cut.blocks
        if words in " ".join(document.text(run) for run in b.owned)
    ]
    return found


def classes(made) -> dict[str, set[str]]:
    """Each class but `same` in place, by the NEW (or, deleted, OLD) ids."""
    found: dict[str, set[str]] = {}
    for link in made.lineage.links:
        if link.cls is BlockClass.SAME and not link.moved:
            continue
        found.setdefault(link.cls.value, set()).update(link.new or link.old)
    return found


def roles(made) -> dict[str, tuple[str, ...]]:
    return {b.id: tuple(r.value for r in b.roles) for b in made.cut.blocks}


def why(made) -> dict[str, str]:
    return {handle: reason.why for handle, reason in made.reopened.items()}


def asked_if(handle: str) -> str:
    return f"⟦asked if {handle}'s Δ is not unchanged or reworded⟧"


def test_a_term_renamed_throughout_reopens_its_blocks_and_conditions_its_qualifier(
    tmp_path,
) -> None:
    """The term "gain" become "yield" in the definition and the proof: both blocks are
    changed and asked; the setting, which qualifies the definition, waits
    on the definition's Δ."""
    new = MAIN.replace("The gain is", "The yield is").replace(
        "the gain is small", "the yield is small"
    )
    v = versions(tmp_path, new)
    _, made = plan(v.new.document, v.store, UPDATE)
    definition = holding(made.cut, v.new.document, "The yield is")
    paragraph = holding(made.cut, v.new.document, "By Def")

    assert classes(made) == {"changed": {definition, paragraph}}
    assert set(made.raw["recut"]) == {definition, paragraph}
    assert why(made) == {
        "def:gain": "its words changed",
        PROOF_BLOCK: "its words changed",
        CLOSING: "its scope:paper edge: the paper changed",
    }
    assert made.conditional == {SETTING: [asked_if("def:gain")]}


def test_a_symbol_renamed_throughout_is_maths_and_the_frame_carries_it(
    tmp_path,
) -> None:
    """$x$ to $\\xi$ in the setting and in the proof's last paragraph: both
    are maths only, their roles and the cut kept, both asked; the setting is
    the ambient frame, so every block after it waits on its Δ."""
    old = MAIN.replace(
        "A second sentence of the setting.",
        "A second sentence of the setting, for $x$.",
    ).replace(
        "Next, the bound is sharp.", "Next, the bound is sharp. It holds for every $x$."
    )
    v = versions(tmp_path, old.replace("$x$", "$\\xi$"), old_main=old)
    _, made = plan(v.new.document, v.store, UPDATE)
    setting, sharp = v.new.id_at("We fix"), v.new.id_at("Next, the bound")

    assert classes(made) == {"math": {setting, sharp}}
    assert made.raw["recut"] == [] and made.raw["touched"] == []
    assert roles(made)[setting] == ("assumption",)
    assert roles(made)[sharp] == ("claim",)
    assert why(made) == {
        SETTING: "its maths changed",
        SHARP: "its maths changed",
        CLOSING: "its scope:paper edge: the paper changed",
    }
    frame = "⟦meaning moved by rule: uses of we-fix-a-field, if its Δ is not unchanged"
    assert {h for h, c in made.conditional.items() if any(frame in x for x in c)} >= {
        "def:gain",
        "thm:bound",
        PROOF_BLOCK,
        "a-closing-paragraph-in",
    }


def test_a_label_renamed_with_its_refs_carries_the_sheet_under_the_new_handle(
    tmp_path,
) -> None:
    """def:gain to def:yield, in its \\label and its \\ref: the definition is
    the same block, its handle and every line naming it follow the label.
    The citing paragraph is changed: its \\ref's argument counts as words
    (a ruling is asked, `tests/identity`'s expected failure)."""
    v = versions(tmp_path, MAIN.replace("def:gain", "def:yield"))
    _, made = plan(v.new.document, v.store, UPDATE)
    paragraph = holding(made.cut, v.new.document, "By Def")

    assert classes(made) == {"changed": {paragraph}}
    assert made.handles["def:yield"] == v.new.id_at("\\begin{definition}")
    sheet = made.carried.sheet
    assert '  def:yield [gain]: "The gain is the ratio of output to input."' in sheet
    assert '→ qualifies def:yield · "We fix a field of scalars."' in sheet
    assert "→ uses def:gain" not in sheet and "qualifies def:gain" not in sheet
    assert why(made) == {
        "by-definition-def:yield-the": "its words changed",
        CLOSING: "its scope:paper edge: the paper changed",
    }
    assert made.conditional == {}


def test_lines_re_wrapped_across_a_section_ask_nothing(tmp_path) -> None:
    """The setting's two sentences and the proof's first paragraph broken
    at other places: every block the same, every role and the group kept,
    nothing asked."""
    new = MAIN.replace(
        "We fix a field of scalars. % a hidden remark about the draft\n"
        "A second sentence of the setting.",
        "We fix a field\nof scalars. % a hidden remark about the draft\n"
        "A second sentence of\nthe setting.",
    ).replace(
        "the gain is small\\footnote{Small means below one.}.\n"
        "Hence the output is bounded.",
        "the gain is small\\footnote{Small means below one.}. Hence the\n"
        "output is bounded.",
    )
    v = versions(tmp_path, new)
    _, made = plan(v.new.document, v.store, UPDATE)

    assert classes(made) == {}
    assert made.raw["recut"] == [] and made.raw["touched"] == []
    assert roles(made)[holding(made.cut, v.new.document, "By Def")] == (
        "claim",
        "justification",
    )
    assert len(made.cut.groups) == 1
    assert made.reopened == {} and made.question is None


def test_a_section_moved_to_the_appendix_is_moved_whole(tmp_path) -> None:
    """The setting's section moved after the results, behind \\appendix:
    its three blocks moved with their roles, nothing re-cut; only the
    paper's scope is asked again."""
    new = MAIN.replace(SETTING_SECTION, "").replace(
        "\\input{extra}", "\\appendix\n" + SETTING_SECTION + "\\input{extra}"
    )
    v = versions(tmp_path, new)
    _, made = plan(v.new.document, v.store, UPDATE)
    moved = {v.new.id_at(w) for w in ("\\section{Setting}", "We fix", "\\begin{def")}

    assert classes(made) == {"moved": moved}
    assert made.raw["recut"] == []
    assert roles(made)[v.new.id_at("We fix")] == ("assumption",)
    assert roles(made)[v.new.id_at("\\begin{def")] == ("definition",)
    assert why(made) == {CLOSING: "its scope:paper edge: the paper changed"}
    assert made.conditional == {}


def test_a_file_split_by_input_keeps_every_block_its_roles_and_its_group(
    tmp_path,
) -> None:
    """The results section moved, unchanged, into `results.tex` behind
    \\input: every block the same (by hash or label across the files), its
    roles and the proof's group kept, nothing asked."""
    old, store = build_old(tmp_path)
    root = tmp_path / "new"
    root.mkdir()
    (root / "results.tex").write_text(RESULTS_SECTION, encoding="utf-8")
    new = make_paper(root, main=MAIN.replace(RESULTS_SECTION, "\\input{results}\n"))
    args = ["diff", str(old.root), str(new.root), "--displays", "in-paragraph"]
    args += ["--cut", str(tmp_path / "old.json"), "--store", str(store.directory)]
    assert main(args) == 0
    _, made = plan(new.document, store, UPDATE)
    paragraph = holding(made.cut, new.document, "By Def")

    assert classes(made) == {}
    assert {link.cls for link in made.lineage.links} == {BlockClass.SAME}
    assert made.raw["recut"] == [] and made.raw["touched"] == []
    assert roles(made)[paragraph] == ("claim", "justification")
    (group,) = made.cut.groups
    assert group.blocks[0] == paragraph
    assert made.reopened == {} and made.question is None


def test_a_whole_proof_deleted_drops_its_group_and_reopens_the_theorem(
    tmp_path,
) -> None:
    """The proof of three blocks deleted: each deleted, its group dropped,
    a removed Δ for each by the rule; the theorem's justified standing no
    longer reads, so the theorem is asked."""
    v = versions(tmp_path, MAIN.replace(PROOF_ENV, ""))
    _, made = plan(v.new.document, v.store, UPDATE)
    deleted = {
        holding(v.old.cut, v.old.document, w)
        for w in ("By Def", "Small means", "Next, the bound")
    }

    assert classes(made) == {"deleted": deleted}
    assert made.cut.groups == ()
    assert [g["why"] for g in made.raw["groups_lost"]] == ["deleted"]
    assert {(d, part, impact) for d, part, impact in made.rule_deltas} == {
        (f"deleted:{d}", None, "removed") for d in deleted
    }
    assert why(made) == {
        "thm:bound": "its lines no longer read on this version: thm:bound "
        "⊢ justified: no justifies edge comes in",
        CLOSING: "its scope:paper edge: the paper changed",
        "a-closing-paragraph-in": "its about edge onto thm:bound, in the section "
        "where a block was deleted",
    }


#: OLD with a table between the closing words and the included file, its
#: block playing no role, and the sharpness relying on it.
TABLE = MAIN.replace(
    "Closing words of the results.\n",
    "\\begin{table}\\label{tab:gains}\n\\begin{tabular}{cc}\n"
    "run & gain \\\\\n1 & 0.5 \\\\\n\\end{tabular}\n"
    "\\caption{Gains of the first run.}\n\\end{table}\n\n"
    "Closing words of the results.\n",
)
TABLE_LINES = {
    **WRITTEN,
    SHARP: ['→ uses tab:gains · "Next, the bound is sharp."', *WRITTEN[SHARP]],
}


@pytest.fixture
def with_a_table(monkeypatch):
    """The first roles pass with the table's block: one more block, which
    plays no role; the included file's two blocks keep theirs."""

    def first_pass(paper):
        assigned = {**roles_file.ROLES, "b10": "none", "b11": "none", "b12": "claim"}
        question = roles_file.session(paper)
        text = (
            roles_file.filled(question, assigned)
            .replace(
                "Hence the output is bounded.",
                "⟦+ claim⟧\nHence the output is bounded.",
            )
            .replace("A second sentence", "⟦+ none⟧\nA second sentence")
        )
        found = roles_file.check(question, text, paper.document)
        assert found.valid, found.problems
        corrections = [
            replace(c, id=str(n)) for n, c in enumerate(found.corrections, 1)
        ]
        return corrected_cut(paper.document, paper.cut, corrections).cut

    monkeypatch.setattr(test_update, "first_pass", first_pass)


@pytest.mark.parametrize(
    "edit",
    [
        ("1 & 0.5", "1 & 0.6"),
        ("Gains of the first run.", "The gains measured in the first run."),
    ],
    ids=["a cell", "the caption"],
)
def test_a_table_edited_is_changed_by_its_label_and_conditions_its_users(
    tmp_path, with_a_table, edit
) -> None:
    v = versions(tmp_path, TABLE.replace(*edit), TABLE_LINES, old_main=TABLE)
    _, made = plan(v.new.document, v.store, UPDATE)
    table = v.new.id_at("\\begin{table}")

    assert classes(made) == {"changed": {table}}
    assert [link.tier.value for link in made.lineage.links if link.new == (table,)] == [
        "label"
    ]
    assert made.raw["recut"] == [table]
    assert why(made) == {
        "tab:gains": "its words changed",
        CLOSING: "its scope:paper edge: the paper changed",
    }
    assert made.conditional == {SHARP: [asked_if("tab:gains")]}


def test_a_theorem_reworded_keeps_its_proof_group(tmp_path) -> None:
    """A group stands
    when the block it names has a one-to-one link of any class, CHANGED
    too. The proof's group is kept; its justifies edge onto the reworded
    theorem waits on the theorem's Δ."""
    v = versions(
        tmp_path,
        MAIN.replace(
            "The output is bounded.\n\\end{theorem}",
            "The output stays bounded.\n\\end{theorem}",
        ),
    )
    _, made = plan(v.new.document, v.store, UPDATE)
    theorem = holding(made.cut, v.new.document, "stays bounded")

    assert classes(made) == {"changed": {theorem}}
    assert made.raw["touched"] == []
    (group,) = made.cut.groups
    assert group.names == theorem
    assert why(made) == {
        "thm:bound": "its words changed",
        CLOSING: "its scope:paper edge: the paper changed",
    }
    assert made.conditional.get("group:by-definition-def:gain-the") == [
        asked_if("thm:bound")
    ]
