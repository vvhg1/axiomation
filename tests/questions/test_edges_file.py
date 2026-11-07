"""The edges as one session on `edges.md` per paper, on the
synthetic paper with a first roles pass applied. No model is called."""

from __future__ import annotations

import io
import json
from dataclasses import replace
from pathlib import Path

import pytest

from axiomation.assembler import corrected_cut
from axiomation.cutter.model import Group, GroupKind, Role, Segment
from axiomation.flags import rule_flags
from axiomation.questions import GATE_LOG, Stage, gates, load_template
from axiomation.questions import edge_sheet as es
from axiomation.questions import edges_file as ef
from axiomation.questions.cut_file import RenderError
from axiomation.questions.edges_file import (
    CHANGES,
    CHANGES_TEMPLATE,
    CLAIM_ADDED_WHY,
    CLAIM_BESIDE_WHY,
    CLAIM_NOT_PLACED,
    CLAIMS_FIXED,
    EDGES_FILE,
    KIND,
    LINES_FILE,
    LINES_HEADER,
    LINES_KICKOFF,
    LINES_TEMPLATE,
    REASK_KICKOFF,
    REASK_TEMPLATE,
    REVIEW_KICKOFF,
    REVIEW_TEMPLATE,
    SESSION_TEMPLATE,
    WRITE,
    FragmentError,
    Gone,
    applied_changes,
    as_changes,
    carry,
    check,
    claim_part,
    laid,
    main,
    person_question,
    reask_question,
    reopen,
    reopened_by_roles,
    review_question,
    session_question,
    stuck_cut_lines,
    written_lines,
)
from axiomation.records import (
    CorrectionClass,
    CutCorrection,
    CutOp,
    EdgeKind,
    LineKind,
    Mark,
    Signal,
    Status,
    TargetKind,
)
from questions.review_paper import EXTRA, MAIN, make_paper
from questions.test_roles_file import first_pass
from runner.fakes import as_lines

GOLDEN = Path(__file__).parent / "golden"
SESSION = load_template(SESSION_TEMPLATE)
REVIEW = load_template(REVIEW_TEMPLATE)
REASK = load_template(REASK_TEMPLATE)
FILES = ("extra.tex", "main.tex")

PROOF = "group:by-definition-def:gain-the"
#: Every kind of line the writer writes, by the handle it goes under.
WRITTEN = {
    "we-fix-a-field": ['→ qualifies def:gain · "We fix a field of scalars."'],
    "thm:bound": ['⊢ justified · "The output is bounded."'],
    "by-definition-def:gain-the": [
        '→ uses def:gain [gain] ref · "By Definition~\\ref{def:gain}"',
        '→ uses small-means-below-one · "the gain is small" — the footnote says',
        '→ self · "Hence"',
        '⊢ self · "Hence the output is bounded."',
    ],
    "next-the-bound-is": ['⊢ unsupported · "Next, the bound is sharp."'],
    PROOF: ['→ justifies thm:bound name · "Hence the output is bounded."'],
    "closing-words-of-the": ['→ about scope:paper · "Closing words of the results."'],
    "a-closing-paragraph-in": [
        '→ about thm:bound · "A closing paragraph"',
        '→ about file:main.tex where-used · "A closing paragraph"',
        '⊢ restated · "A closing paragraph in another file."',
    ],
}
LEGEND = [
    '  def:gain [gain]: "The gain is the ratio of output to input."',
    "Pervasive: we-fix-a-field",
]


@pytest.fixture
def roled(paper):
    return first_pass(paper)


def ask(paper, cut, limit=None, files=FILES):
    return session_question(paper.document, cut, SESSION, files, limit)


def shown(question) -> str:
    return question.workspace.files[EDGES_FILE]


def written(
    sheet: str, lines: dict[str, list[str]] = WRITTEN, header: list[str] = LEGEND
) -> str:
    """`sheet` as a writer leaves it: `header` above the `Files:` line,
    and under each block, and after each group's end line, its `lines`
    and a done mark."""
    out: list[str] = []
    owner: str | None = None

    def close() -> None:
        if owner is not None:
            out.extend([*lines.get(owner, []), f"⟦done {owner}⟧", ""])

    for line in sheet.split("\n"):
        if line.startswith("Files: "):
            out.extend(header)
        if m := (es.BLOCK.match(line) or es.GROUP.match(line) or es.END.match(line)):
            close()
            owner = None if es.GROUP.match(line) else m.group(1)
        out.append(line)
    close()
    return "\n".join(out)


def valid(paper, question, text):
    found = check(question, answered(question, text), paper.document)
    assert found.valid, found.problems
    return found.edges


def sheet_pass(paper, cut):
    """A session that edits the sheet itself, as the re-ask and an
    update's do, shown the sheet as the first pass is."""
    return ef._question(paper.document, cut, SESSION, FILES, None)


def answered(question, sheet: str) -> str:
    """`sheet` as the question's session writes it: a later pass's changes
    to the sheet it was shown, the first pass's lines by owner in
    `lines.md`, else the sheet."""
    spec = json.loads(question.workspace.gate.spec)
    if CHANGES in spec:
        return as_changes(spec[CHANGES], sheet)
    return as_lines(sheet) if question.workspace.edit == LINES_FILE else sheet


def problems(paper, question, text) -> tuple[str, ...]:
    found = check(question, answered(question, text), paper.document)
    assert not found.valid
    return found.problems


# --- the question and its key ------------------------------------------------------


def test_one_session_for_the_whole_paper(paper, roled) -> None:
    question = ask(paper, roled)

    assert question.stage is Stage.EDGES and question.kind == KIND
    assert question.prompt == LINES_KICKOFF
    workspace = question.workspace
    assert workspace.edit == LINES_FILE and workspace.reask_from_edit
    assert workspace.files[LINES_FILE] == "" and EDGES_FILE in workspace.files
    lines = load_template(LINES_TEMPLATE)
    assert workspace.instructions == SESSION.render() + "\n\n" + lines.render()
    assert workspace.gate.module == "axiomation.questions.edges_file"
    assert not workspace.gate.on_write  # checked when it tries to finish
    assert question.handles["def:gain"] == paper.id_at("\\begin{definition}")
    assert question.handles[PROOF] == roled.groups[0].id
    assert len(question.handles) == len(roled.blocks) + len(roled.groups)


def test_the_first_pass_s_lines_are_laid_into_the_sheet_the_answer_kept(
    paper, roled
) -> None:
    question = ask(paper, roled)
    sheet = written(shown(question))

    found = check(question, as_lines(sheet), paper.document)

    assert found.valid, found.problems
    kept = es.parse(found.kept)
    assert kept.edges == es.parse(sheet).edges
    assert kept.supports == es.parse(sheet).supports
    head = found.kept.split("\n---\n")[0]
    assert all(line in head for line in LEGEND)
    assert found.kept.count("⟦done ") == len(roled.blocks) + len(roled.groups)


def test_a_line_before_any_heading_and_a_heading_of_no_owner_are_problems(
    paper, roled
) -> None:
    question = ask(paper, roled)
    lines = as_lines(written(shown(question)))
    text = "a stray line\n" + lines + '\n@@ no-such\n→ uses def:gain · "The gain"'

    found = check(question, text, paper.document).problems

    assert "a line before any `@@` heading: 'a stray line'" in found
    assert (
        "`@@ no-such`: no block or group has this handle; its lines are not read"
        in found
    )


def test_a_problem_names_the_line_not_its_place_in_the_laid_sheet(paper, roled) -> None:
    question = ask(paper, roled)
    text = as_lines(written(shown(question))).replace(
        "@@ more", "@@ more\n→ nowhere at all"
    )

    found = check(question, text, paper.document).problems

    assert found and not any(p.startswith("line ") for p in found)
    assert any("→ nowhere at all" in p for p in found)


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        (lambda t: t.replace("→ uses small-means", "→ uses no-such"), "no such target"),
        (
            lambda t: t.replace('"the gain is small"', '"the gain is large"'),
            "evidence not in by-definition-def:gain-the",
        ),
        (
            lambda t: t.replace("file:main.tex", "file:other.tex"),
            "not a file of the paper",
        ),
        (
            lambda t: t.replace('output to input."', 'output to input." (standard)'),
            "`(standard)` is not a writer's role",
        ),
    ],
    ids=["unknown-target", "evidence", "unknown-file", "legend-under-the-header"],
)
def test_a_line_that_does_not_read_is_named_from_lines_md(
    paper, roled, change, problem
) -> None:
    question = ask(paper, roled)

    found = problems(paper, question, change(written(shown(question))))

    assert any(problem in p for p in found), found


def test_an_owner_s_lines_under_two_headings_are_laid_in_their_order(
    paper, roled
) -> None:
    question = ask(paper, roled)
    extra = '→ uses def:gain · "More"'
    text = as_lines(written(shown(question))) + f"\n@@ more\n{extra}"

    found = check(question, text, paper.document)

    assert found.valid, found.problems
    rows = found.kept.split("\n")
    assert rows.count("⟦done more⟧") == 1
    assert rows[rows.index("⟦done more⟧") - 1] == extra


def test_a_block_named_header_keeps_its_lines_apart_from_the_header_s() -> None:
    sheet = "\n".join(
        ["# edges", "", "---", "", "⟦header none⟧ main.tex:1:1 (heading)", "# Header"]
    )
    written = '@@ the header\nPervasive: header\n@@ header\n→ uses x · "Header"'

    rows = laid(sheet, written).split("\n")

    assert rows[rows.index("---") - 1] == "Pervasive: header"
    assert rows[-2:] == ['→ uses x · "Header"', "⟦done header⟧"]


def test_the_sheet_shows_roles_segments_groups_and_files(paper, roled) -> None:
    text = shown(ask(paper, roled))

    assert "Files: extra.tex, main.tex\n\n---\n" in text
    assert "⟦we-fix-a-field assumption+none⟧ main.tex:4:1 (prose)" in text
    assert "⟦+ claim⟧ Hence the output is bounded." in text
    assert f"⟦group {PROOF} proof⟧\n\n⟦by-definition" in text
    assert f"\\end{{proof}}\n\n⟦end group {PROOF}⟧" in text
    assert es.check(text) and es.parse(text).block_ids == [b.id for b in roled.blocks]


def test_the_sheet_matches_its_golden(paper, roled) -> None:
    path = GOLDEN / "edges-file.md"
    text = shown(ask(paper, roled))
    assert path.exists(), f"missing golden {path}; write it deliberately, reviewed"
    assert text == path.read_text(encoding="utf-8"), (
        "edges.md differs from its golden; review the change before updating it"
    )


def test_the_key_binds_the_text_the_roles_the_groups_the_files_and_template(
    paper, roled, tmp_path
) -> None:
    base = ask(paper, roled).key
    edited = make_paper(tmp_path / "edited", MAIN.replace("small", "tiny"))
    block = roled.block(paper.id_at("\\begin{theorem}"))
    other_role = replace(
        roled,
        blocks=tuple(
            replace(
                b, roles=(Role.ASSUMPTION,), segments=(Segment("", Role.ASSUMPTION),)
            )
            if b.id == block.id
            else b
            for b in roled.blocks
        ),
    )

    assert ask(paper, roled).key == base
    assert ask(edited, first_pass(edited)).key != base
    assert ask(paper, other_role).key != base
    assert ask(paper, replace(roled, groups=())).key != base
    assert ask(paper, roled, files=("main.tex",)).key != base
    changed = replace(SESSION, text=SESSION.text + "\nBe brief.\n")
    assert session_question(paper.document, roled, changed, FILES).key != base


def test_a_paper_over_the_limit_is_refused_when_its_question_is_built(
    paper, roled
) -> None:
    size = len(shown(ask(paper, roled)))

    assert ask(paper, roled, limit=size).key
    with pytest.raises(RenderError, match="one session per paper"):
        ask(paper, roled, limit=size - 1)


# --- the answer --------------------------------------------------------------------


def test_a_written_sheet_gives_its_records_in_order(paper, roled) -> None:
    question = ask(paper, roled)
    ids = question.handles

    found = valid(paper, question, written(shown(question)))

    (entry,) = found.entries
    assert (entry.block, entry.label, entry.role) == (
        ids["def:gain"],
        "gain",
        Role.DEFINITION,
    )
    assert entry.words == "The gain is the ratio of output to input."
    kinds = [(e.source, e.kind, e.target.kind) for e in found.edges]
    assert kinds == [
        (ids["we-fix-a-field"], EdgeKind.QUALIFIES, TargetKind.BLOCK),
        (ids["by-definition-def:gain-the"], EdgeKind.USES, TargetKind.BLOCK),
        (ids["by-definition-def:gain-the"], EdgeKind.USES, TargetKind.BLOCK),
        (ids[PROOF], EdgeKind.JUSTIFIES, TargetKind.BLOCK),
        (ids["closing-words-of-the"], EdgeKind.ABOUT, TargetKind.SCOPE),
        (ids["a-closing-paragraph-in"], EdgeKind.ABOUT, TargetKind.BLOCK),
        (ids["a-closing-paragraph-in"], EdgeKind.ABOUT, TargetKind.FILE),
    ]
    uses = found.edges[1]
    assert uses.parts == ("gain",) and uses.mark is Mark.REF and uses.note is None
    assert found.edges[2].note == "the footnote says"
    assert found.edges[3].mark is Mark.NAME
    assert found.edges[4].target.name == "paper"
    assert found.edges[6].target.name == "main.tex" and found.edges[6].where_used
    assert not any(e.generalizes for e in found.edges)
    (mark,) = found.selfs
    assert mark.block == ids["by-definition-def:gain-the"]
    assert [s.status for s in found.standings] == [
        Status.JUSTIFIED,
        Status.SELF,
        Status.UNSUPPORTED,
        Status.RESTATED,
    ]
    assert [f.block for f in found.frame] == [ids["we-fix-a-field"]]
    assert all(r.id == "" and r.answer is None for r in found.all())


def test_a_generalizes_line_files_its_flag(paper, roled) -> None:
    """A later, wider assumption about the definition it generalizes: the
    line's flag is the record's."""
    question = ask(paper, roled)
    lines = {
        **WRITTEN,
        "we-fix-a-field": [
            *WRITTEN["we-fix-a-field"],
            '→ about def:gain generalizes · "We fix a field of scalars."',
        ],
    }
    found = valid(paper, question, written(shown(question), lines))

    (wider,) = [e for e in found.edges if e.generalizes]
    assert wider.kind is EdgeKind.ABOUT
    assert wider.target.name == question.handles["def:gain"]


def test_cut_lines_become_corrections_with_their_evidence(paper, roled) -> None:
    question = ask(paper, roled)
    ids = question.handles
    lines = {
        **WRITTEN,
        "setting": ['✂ merge we-fix-a-field · "Setting" — the heading and its text'],
        "we-fix-a-field": [
            *WRITTEN["we-fix-a-field"],
            '✂ merge def:gain · "We fix a field" — one set-up',
        ],
        "results": ['✂ move thm:bound "The output" · "Results" — the theorem'],
        "a-closing-paragraph-in": [
            *WRITTEN["a-closing-paragraph-in"],
            '✂ split "another file." · "A closing paragraph" — two claims',
        ],
    }

    found = check(
        question, answered(question, written(shown(question), lines)), paper.document
    )

    assert found.valid, found.problems
    merge, move, split = found.corrections
    assert merge.op is CutOp.MERGE and merge.cls is CorrectionClass.OTHER
    assert merge.blocks == (ids["setting"], ids["we-fix-a-field"], ids["def:gain"])
    assert merge.why == "the heading and its text one set-up"
    assert merge.evidence == ("Setting", "We fix a field")
    assert move.op is CutOp.REBOUND
    assert move.blocks == (ids["results"], ids["thm:bound"])
    assert (move.at, move.evidence) == (("The output",), ("Results",))
    assert split.op is CutOp.SPLIT and split.block == ids["a-closing-paragraph-in"]
    assert (split.at, split.evidence) == (("another file.",), ("A closing paragraph",))
    assert all(c.check() == () for c in found.corrections)
    assert all(c.id == "" and c.answer is None for c in found.corrections)
    assert found.edges == valid(paper, question, written(shown(question)))


def test_a_sheet_without_cut_lines_proposes_nothing(paper, roled) -> None:
    question = ask(paper, roled)
    assert (
        check(
            question, answered(question, written(shown(question))), paper.document
        ).corrections
        == ()
    )


def test_a_part_takes_its_segment_s_role_unless_its_legend_line_says(
    paper, roled
) -> None:
    question = ask(paper, roled)
    header = [
        *LEGEND,
        '  by-definition-def:gain-the [conclusion]: "Hence the output" (motivation)',
        '  by-definition-def:gain-the [step]: "the gain is small" (definition)',
    ]

    found = valid(paper, question, written(shown(question), header=header))

    roles = [e.role for e in found.entries]
    assert roles == [Role.DEFINITION, Role.MOTIVATION, Role.DEFINITION]


def test_a_part_of_a_claim_names_no_claim_its_handle_does(paper, roled) -> None:
    question = ask(paper, roled)
    header = [*LEGEND, '  by-definition-def:gain-the [conclusion]: "Hence the output"']

    found = problems(paper, question, written(shown(question), header=header))

    assert any(
        "part by-definition-def:gain-the [conclusion]: a claim is named by its "
        "handle on its ⊢ line" in p
        for p in found
    )


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        (lambda t: t.replace("small\n", "large\n", 1), "the block's text changed"),
        (
            lambda t: t.replace("⟦thm:bound claim⟧", "⟦thm:bound assumption⟧"),
            "block and group lines are not as shown",
        ),
        (
            lambda t: t.replace(f"⟦end group {PROOF}⟧", ""),
            "block and group lines are not as shown",
        ),
        (
            lambda t: t.replace(
                'scope:paper · "Closing', 'scope:paper optional · "Closing'
            ),
            "`optional` is not a writer's flag",
        ),
        (
            lambda t: t.replace("file:main.tex", "file:other.tex"),
            "not a file of the paper: neither in the header's Files: or Code: "
            "lines nor a path the text writes",
        ),
        (lambda t: t.replace("⟦done more⟧", ""), "not done: more"),
        (lambda t: t.replace("→ uses small-means", "→ uses no-such"), "no such target"),
        (
            lambda t: t.replace('"the gain is small"', '"the gain is large"'),
            "evidence not in by-definition-def:gain-the",
        ),
        (
            lambda t: t.replace('output to input."', 'output to input." (standard)'),
            "`(standard)` is not a writer's role",
        ),
    ],
    ids=[
        "text",
        "block-line",
        "group-line",
        "optional",
        "unknown-file",
        "not-done",
        "unknown-target",
        "evidence",
        "standard",
    ],
)
def test_each_fault_is_a_problem(paper, roled, change, problem) -> None:
    question = sheet_pass(paper, roled)

    found = problems(paper, question, change(written(shown(question))))

    assert any(problem in p for p in found), found


def test_a_sheet_without_any_standing_is_refused(paper, roled) -> None:
    """The sheet's own check lets a first pass through without standings;
    a writer's answer gives every claim's."""
    question = ask(paper, roled)
    lines = {h: [x for x in ls if not x.startswith("⊢")] for h, ls in WRITTEN.items()}

    found = problems(paper, question, written(shown(question), lines))

    assert found == tuple(
        f"no support line: {h}"
        for h in (
            "thm:bound",
            "by-definition-def:gain-the",
            "next-the-bound-is",
            "a-closing-paragraph-in",
        )
    )


def test_evidence_across_two_of_a_group_s_blocks_is_refused(paper, roled) -> None:
    """The export places an excerpt in one owned run:
    evidence spanning the footnote and the next paragraph would pass a
    check on the group's joined text and then fail to export."""
    question = ask(paper, roled)
    spanning = '→ justifies thm:bound · "Small means below one.} Next, the bound"'
    lines = {**WRITTEN, PROOF: [spanning]}

    found = problems(paper, question, written(shown(question), lines))

    assert any(
        "evidence not in group:" in p and "it spans two blocks" in p for p in found
    ), found


@pytest.mark.parametrize(
    ("sentence", "handle"),
    [
        ("scripts/run.py recomputes it.", "scripts-run.py-recomputes-it"),
        ("It is recomputed by scripts/run.py.", "it-is-recomputed-by"),
    ],
    ids=["mid-sentence", "ends-a-sentence"],
)
def test_a_file_the_text_names_by_its_path_is_a_file_of_the_paper(
    tmp_path, sentence, handle
) -> None:
    """A script the paper names but does not ship is a target (`scripts/run.py`
    here); a full stop after it ends the
    sentence, not the path."""
    named = make_paper(
        tmp_path / "named", MAIN.replace("Closing words of the results.", sentence)
    )
    question = ask(named, first_pass(named))
    lines = {
        **WRITTEN,
        handle: [f'→ about file:scripts/run.py · "{sentence}"'],
    }
    lines.pop("closing-words-of-the")

    assert valid(named, question, written(shown(question), lines)).edges


@pytest.mark.parametrize(
    ("target", "problem"),
    [
        ("file:output", "nor a path the text writes"),  # a word of the text
        ("file:main", "write the file's name as the header names it: file:main.tex"),
        ("file:run.py", "nor a path the text writes"),  # part of a path only
    ],
)
def test_a_guessed_file_is_refused(tmp_path, target, problem) -> None:
    """A word of the text is no path, a file on disk is named as listed,
    and a path matches whole."""
    named = make_paper(
        tmp_path / "named",
        MAIN.replace("Closing words of the results.", "scripts/run.py recomputes it."),
    )
    question = ask(named, first_pass(named))
    lines = {
        **WRITTEN,
        "scripts-run.py-recomputes-it": [f'→ about {target} · "recomputes it."'],
    }
    lines.pop("closing-words-of-the")

    found = problems(named, question, written(shown(question), lines))

    assert any(problem in p for p in found), found


def test_a_part_of_a_group_is_refused(paper, roled) -> None:
    question = ask(paper, roled)
    header = [*LEGEND, f'  {PROOF} [all]: "Next, the bound is sharp."']

    found = problems(paper, question, written(shown(question), header=header))

    assert any("a part is a block's, not a group's" in p for p in found), found


def test_the_sheet_as_shown_is_unfinished_not_wrong(paper, roled) -> None:
    question = ask(paper, roled)

    found = problems(paper, question, shown(question))

    assert all(p.startswith(es.UNFINISHED) for p in found), found


# --- the gate -----------------------------------------------------------------------


def gate(question, tmp_path: Path, text: str, monkeypatch, capsys, *mode) -> str:
    spec = tmp_path / "gate.json"
    spec.write_text(question.workspace.gate.spec, encoding="utf-8")
    edited = tmp_path / question.workspace.edit
    edited.write_text(answered(question, text), encoding="utf-8")
    monkeypatch.setattr("sys.stdin", io.StringIO('{"hook_event_name": "Stop"}'))
    assert main([str(spec), str(edited), *mode]) == 0
    return capsys.readouterr().out


def test_the_gate_is_silent_on_a_sheet_that_reads(
    paper, roled, tmp_path, monkeypatch, capsys
) -> None:
    question = ask(paper, roled)
    text = written(shown(question))
    later = sheet_pass(paper, roled)

    assert gate(question, tmp_path, text, monkeypatch, capsys) == ""
    assert gate(later, tmp_path, text, monkeypatch, capsys, WRITE) == ""


def test_the_gate_at_the_end_blocks_with_every_problem(
    paper, roled, tmp_path, monkeypatch, capsys
) -> None:
    question = ask(paper, roled)
    text = written(shown(question)).replace("⟦done more⟧", "")

    decision = json.loads(gate(question, tmp_path, text, monkeypatch, capsys))

    assert decision["decision"] == "block"
    assert (
        f"- not done: more: no `@@ more` heading in {LINES_FILE}" in decision["reason"]
    )
    assert decision["reason"].startswith(f"{LINES_FILE} does not read yet")
    assert decision["reason"].endswith(
        f"Fix these in {LINES_FILE}, keep the rest, then finish."
    )


def test_the_gate_at_the_end_lets_the_session_end_after_its_rounds(
    paper, roled, tmp_path, monkeypatch, capsys
) -> None:
    question = ask(paper, roled)
    text = shown(question)

    outs = [
        gate(question, tmp_path, text, monkeypatch, capsys)
        for _ in range(gates.ROUNDS + 1)
    ]

    assert all(outs[: gates.ROUNDS]) and outs[gates.ROUNDS] == ""
    assert (tmp_path / gates.TRIES).read_text() == str(gates.ROUNDS)
    logged = [json.loads(line) for line in (tmp_path / GATE_LOG).open()]
    assert [e["blocked"] for e in logged] == [True] * gates.ROUNDS + [False]
    assert all(e["mode"] == "stop" and e["problems"] for e in logged)


def test_the_gate_on_a_write_leaves_out_the_unfinished(
    paper, roled, tmp_path, monkeypatch, capsys
) -> None:
    question = sheet_pass(paper, roled)

    assert gate(question, tmp_path, shown(question), monkeypatch, capsys, WRITE) == ""


def test_the_gate_on_a_write_shows_what_is_wrong_and_never_counts_rounds(
    paper, roled, tmp_path, monkeypatch, capsys
) -> None:
    question = sheet_pass(paper, roled)
    text = shown(question).replace(
        "⟦more none⟧", '→ uses no-such · "Results"\n\n⟦more none⟧'
    )

    first = gate(question, tmp_path, text, monkeypatch, capsys, WRITE)
    again = gate(question, tmp_path, text, monkeypatch, capsys, WRITE)
    other = gate(question, tmp_path, text + "\n", monkeypatch, capsys, WRITE)

    decision = json.loads(first)
    assert "no such target" in decision["reason"]
    assert "not done" not in decision["reason"]
    assert decision["reason"].endswith(
        f"Fix these in {EDGES_FILE}, keep the rest, and go on."
    )
    assert again == ""  # the sheet did not change since the last check
    assert other == first
    assert not (tmp_path / gates.TRIES).exists()
    logged = [json.loads(line) for line in (tmp_path / GATE_LOG).open()]
    assert [(e["mode"], e["blocked"]) for e in logged] == [
        ("write", True),
        ("write", True),
    ]  # each check that ran; the unchanged one did not


def test_a_gate_that_cannot_run_lets_the_session_go_on(
    tmp_path, monkeypatch, capsys
) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(""))

    assert main([str(tmp_path / "missing.json"), str(tmp_path / EDGES_FILE)]) == 0
    assert main([]) == 0
    captured = capsys.readouterr()
    assert captured.out == "" and "could not run" in captured.err


# --- a later pass ----------------------------------------------------


def review_of(paper, cut, previous: str, pass_: int = 2, follows: str = "k1"):
    return review_question(
        paper.document, cut, SESSION, REVIEW, FILES, previous, follows, pass_
    )


def test_reopen_takes_off_the_done_marks_it_is_given(paper, roled) -> None:
    sheet = written(shown(ask(paper, roled)))

    every = reopen(sheet)
    one = reopen(sheet, {"thm:bound"})

    assert "⟦done" not in every and "→ qualifies def:gain" in every
    assert "⟦done thm:bound⟧" not in one
    assert one.count("⟦done ") == sheet.count("⟦done ") - 1


def test_a_review_shows_the_last_sheet_and_writes_its_changes_in_lines(
    paper, roled
) -> None:
    sheet = written(shown(ask(paper, roled)))

    question = review_of(paper, roled, sheet)

    assert question.prompt == REVIEW_KICKOFF and question.template == REVIEW
    workspace = question.workspace
    assert shown(question) == es.with_handles(sheet)
    assert workspace.edit == LINES_FILE and workspace.files[LINES_FILE] == ""
    assert workspace.instructions == (
        SESSION.render()
        + "\n\n"
        + REVIEW.render()
        + "\n\n"
        + load_template(CHANGES_TEMPLATE).render()
    )
    assert not workspace.gate.on_write  # checked when it tries to finish
    assert workspace.reask_from_edit
    assert question.handles == ask(paper, roled).handles
    # No change leaves the sheet as shown, which reads as the first pass's.
    found = check(question, "⟦done⟧", paper.document)
    assert found.valid, found.problems
    assert found.kept == shown(question)
    assert [e.kind for e in found.edges.edges] == [
        e.kind for e in valid(paper, ask(paper, roled), sheet).edges
    ]


def test_a_review_s_key_binds_the_answer_it_follows_the_pass_and_both_briefs(
    paper, roled
) -> None:
    """A pass is keyed by the answer it follows, not by the sheet's text:
    a run finds it again without reading that answer's text."""
    sheet = written(shown(ask(paper, roled)))
    base = review_of(paper, roled, sheet).key

    other = sheet.replace("→ qualifies def:gain", "→ uses def:gain")
    longer = replace(SESSION, text=SESSION.text + "\nBe brief.\n")
    assert review_of(paper, roled, sheet).key == base
    assert base != ask(paper, roled).key
    assert review_of(paper, roled, other).key == base
    assert review_of(paper, roled, "").key == base
    assert review_of(paper, roled, sheet, follows="k2").key != base
    assert review_of(paper, roled, sheet, 3).key != base
    assert (
        review_question(
            paper.document, roled, longer, REVIEW, FILES, sheet, "k1", 2
        ).key
        != base
    )


def test_a_review_s_changes_are_applied_to_the_sheet_it_was_shown(paper, roled) -> None:
    """Under an owner, a `-` line by its head and quote and a `+` line; a
    justifies line onto a claim standing `unsupported` gives it its
    standing; under the header, a legend line added (its two spaces left
    out) and the `Pervasive:` line replaced. The owners not named, and
    every done mark, stand."""
    sheet = written(shown(ask(paper, roled)))
    question = review_of(paper, roled, sheet)
    changes = "\n".join(
        [
            f"@@ {LINES_HEADER}",
            '+ def:gain [ratio]: "the ratio of output to input"',
            "- Pervasive: we-fix-a-field",
            "+ Pervasive: we-fix-a-field, def:gain",
            "@@ by-definition-def:gain-the",
            '- → uses small-means-below-one · "the gain is small"',
            '+ → uses small-means-below-one · "the gain is small" — the note says',
            "@@ closing-words-of-the",
            '+ → justifies next-the-bound-is · "Closing words of the results."',
            "⟦done⟧",
        ]
    )

    found = check(question, changes, paper.document)

    assert found.valid, found.problems
    kept = found.kept
    head = kept.split("\n---\n")[0]
    assert '  def:gain [ratio]: "the ratio of output to input"' in head
    assert "Pervasive: we-fix-a-field, def:gain" in head
    assert "Pervasive: we-fix-a-field\n" not in head + "\n"
    lines = written_lines(kept)
    assert lines["by-definition-def:gain-the"][1:3] == [
        '→ self · "Hence"',
        '⊢ self · "Hence the output is bounded."',
    ]
    assert (
        '→ uses small-means-below-one · "the gain is small" — the note says'
        in lines["by-definition-def:gain-the"]
    )
    assert lines["next-the-bound-is"][0] == '⊢ justified · "Next, the bound is sharp."'
    assert lines["we-fix-a-field"] == written_lines(sheet)["we-fix-a-field"]
    assert kept.count("⟦done ") == sheet.count("⟦done ")
    assert applied_changes(shown(question), changes) == kept


def test_every_change_that_does_not_apply_is_a_problem_at_once(paper, roled) -> None:
    sheet = written(shown(ask(paper, roled)))
    question = review_of(paper, roled, sheet)
    changes = "\n".join(
        [
            "a stray line",
            f"@@ {LINES_HEADER}",
            "+ Pervasive: def:gain",
            '- nobody [x]: "words"',
            "@@ no-such-block",
            '+ → uses def:gain · "x"',
            "@@ we-fix-a-field",
            '→ uses def:gain · "We fix"',
            '- → uses def:gain · "We fix"',
            '+ → qualifies def:gain · "We fix a field of scalars."',
        ]
    )

    found = check(question, changes, paper.document).problems

    assert found == (
        "a line before any `@@` heading: 'a stray line'",
        'the header holds no line `nobody [x]: "words"`',
        "`@@ no-such-block`: no block or group has this handle; its lines are not read",
        "`@@ we-fix-a-field`: a change begins with `+ ` or `- `: "
        "'→ uses def:gain · \"We fix\"'",
        '⟦we-fix-a-field⟧ holds no line `→ uses def:gain · "We fix"`; it holds:\n'
        '  → qualifies def:gain · "We fix a field of scalars."',
        '⟦we-fix-a-field⟧ holds the line `→ qualifies def:gain · "We fix a field of '
        'scalars."` already',
        "not finished: end lines.md with `⟦done⟧` on its own line once every change "
        "is written",
        "one `Pervasive:` line: remove the sheet's (`- Pervasive: …`) to write another",
    )
    with pytest.raises(FragmentError):
        applied_changes(shown(question), changes)


def test_a_line_added_twice_and_a_done_mark_are_refused_by_name(paper, roled) -> None:
    sheet = written(shown(ask(paper, roled)))
    question = review_of(paper, roled, sheet)
    line = '+ → uses def:gain · "Closing words of the results."'
    changes = "\n".join(
        [
            "@@ closing-words-of-the",
            line,
            line,
            "+ ⟦done closing-words-of-the⟧",
            "⟦done⟧",
        ]
    )

    found = check(question, changes, paper.document).problems

    assert found == (
        '`@@ closing-words-of-the`: the line `→ uses def:gain · "Closing words of '
        'the results."` is added twice',
        "`@@ closing-words-of-the`: write no done mark; `⟦done⟧` ends the file",
    )


def test_changes_applied_are_read_as_the_first_pass_s_sheet(paper, roled) -> None:
    """Once they apply, the sheet's own check reads the result: an edge
    onto no block is refused as on the first pass."""
    sheet = written(shown(ask(paper, roled)))
    question = review_of(paper, roled, sheet)

    found = problems(
        paper,
        question,
        sheet.replace(
            '→ qualifies def:gain · "We fix a field of scalars."',
            '→ qualifies no-such · "We fix a field of scalars."',
        ),
    )

    assert any("no-such" in p for p in found), found
    assert not any(p.startswith("line ") for p in found)


def test_as_changes_writes_what_makes_the_edited_sheet(paper, roled) -> None:
    sheet = written(shown(ask(paper, roled)))
    edited = sheet.replace(
        '→ uses small-means-below-one · "the gain is small" — the footnote says',
        '→ uses small-means-below-one · "the gain is small" — the note says',
    ).replace("Pervasive: we-fix-a-field", "Pervasive: def:gain")

    changes = as_changes(sheet, edited)

    assert changes.split("\n") == [
        f"@@ {LINES_HEADER}",
        "- Pervasive: we-fix-a-field",
        "+ Pervasive: def:gain",
        "@@ by-definition-def:gain-the",
        '- → uses small-means-below-one · "the gain is small" — the footnote says',
        '+ → uses small-means-below-one · "the gain is small" — the note says',
        "⟦done⟧",
    ]

    def edges(text: str) -> list[tuple[str, ...]]:
        return sorted(
            (e.source, e.kind, e.target, e.evidence, e.note)
            for e in es.parse(text).edges
        )

    assert edges(applied_changes(sheet, changes)) == edges(edited)
    assert as_changes(sheet, sheet) == "⟦done⟧"


def _section(text: str, heading: str) -> str:
    return text.split(f"## {heading}\n", 1)[1].split("\n## ", 1)[0]


def test_the_pass_by_target_states_the_writer_s_purpose_and_its_walk() -> None:
    """The later edges pass by target: its
    brief opens with the writer brief's purpose paragraph and then walks the
    sheet target by target; it takes no placeholder, as the review's
    section does not."""
    by_target = load_template("edges-bytarget")
    walk = _section(by_target.text, "This pass: by target")

    assert _section(by_target.text, "What the graph is for") == _section(
        SESSION.text, "What the graph is for"
    )
    assert "Walk the sheet the other way, target by target." in walk
    assert "Remove no line but one you correct" in walk
    assert by_target.render() == by_target.text
    assert "$" not in by_target.text


#: The rule on a common term glossed in passing, as one sentence.
COMMON_TERM = (
    "A block that glosses a common term of the field in passing is a reminder "
    "of the term, like a gloss of standard notation, not the paper's own "
    "definition: no edge targets it, and a block that uses the term has no "
    "edge onto the gloss."
)


def test_both_edges_briefs_state_the_common_term_ruling_as_tested() -> None:
    """The sentence verbatim, in
    the pass by target's walk as tested, after the forms a reliance takes,
    and in the writer's brief beside the ambient frame, whose standard
    notation it likens the gloss to. The version also carries the
    sentence on a claim's justifications and their flags, with the argument
    an analogy carries over, and the sentence on naming a claim by its
    handle and on quoting each claim apart."""
    by_target = load_template("edges-bytarget")
    walk = " ".join(_section(by_target.text, "This pass: by target").split())
    rules = " ".join(_section(SESSION.text, "The rules").split())

    after_forms = f"a standing assumption it works under. {COMMON_TERM} Where"
    assert after_forms in walk
    assert f"where an edit breaks the most. {COMMON_TERM}" in rules
    assert by_target.version == "cd3cc09d8603"


# --- the re-ask after a cut correction --------------------------------

PROOF_BLOCK = "by-definition-def:gain-the"
#: The sheet before the split: an edge onto the proof's first block as a
#: whole, one onto a part of it, and a cut line, weighed already.
BEFORE_SPLIT = {
    **WRITTEN,
    PROOF_BLOCK: [
        *WRITTEN[PROOF_BLOCK],
        '✂ split "Hence the output" · "Hence the output is bounded." — two claims',
    ],
    "next-the-bound-is": [
        *WRITTEN["next-the-bound-is"],
        f'→ uses {PROOF_BLOCK} · "Next, the bound"',
    ],
    "closing-words-of-the": [
        *WRITTEN["closing-words-of-the"],
        f'→ about {PROOF_BLOCK} [hence] · "Closing words"',
    ],
}
SPLIT_LEGEND = [
    LEGEND[0],
    f'  {PROOF_BLOCK} [hence]: "Hence the output is bounded." (definition)',
    *LEGEND[1:],
]


def corrected(paper, cut, correction: CutCorrection):
    found = corrected_cut(paper.document, cut, [correction])
    assert found.problems == ()
    return found.cut


def split_carried(paper, roled):
    question = ask(paper, roled)
    previous = written(shown(question), BEFORE_SPLIT, SPLIT_LEGEND)
    split = CutCorrection(
        "x1",
        CutOp.SPLIT,
        CorrectionClass.OTHER,
        "two claims",
        block=question.handles[PROOF_BLOCK],
        at=("Hence the output",),
    )
    after = corrected(paper, roled, split)
    return previous, after, carry(paper.document, previous, roled, after, FILES)


def test_carry_moves_lines_by_their_words_and_reopens_what_a_split_touched(
    paper, roled
) -> None:
    previous, _, carried = split_carried(paper, roled)
    tail = "hence-the-output-is"

    assert carried.reopened == (PROOF_BLOCK, tail, "next-the-bound-is")
    assert carried.why["next-the-bound-is"].startswith(
        f"its edge onto {PROOF_BLOCK} needs a target"
    )
    lines = written_lines(carried.sheet)
    # Lines go to the block that holds their words, and no reopened block
    # keeps a done mark.
    assert lines[PROOF_BLOCK] == WRITTEN[PROOF_BLOCK][:2]
    assert lines[tail] == WRITTEN[PROOF_BLOCK][2:]
    # The whole-block target points at the block holding the old one's
    # start; the part follows its words, and its legend line with it.
    assert f'→ uses {PROOF_BLOCK} · "Next, the bound"' in lines["next-the-bound-is"]
    assert f'→ about {tail} [hence] · "Closing words"' in lines["closing-words-of-the"]
    assert f'  {tail} [hence]: "Hence the output is bounded." (definition)' in (
        carried.sheet
    )
    # Every other block and group keeps its lines and its done mark; the cut
    # line, weighed already, is gone.
    before = written_lines(previous)
    for handle in ("we-fix-a-field", "thm:bound", PROOF, "a-closing-paragraph-in"):
        assert lines[handle] == before[handle]
    assert "✂" not in carried.sheet
    assert "Pervasive: we-fix-a-field" in carried.sheet


def test_carry_on_a_merge_takes_both_blocks_lines_to_the_merged_one(
    paper, roled
) -> None:
    question = ask(paper, roled)
    previous = written(shown(question))
    merge = CutCorrection(
        "x1",
        CutOp.MERGE,
        CorrectionClass.OTHER,
        "a heading and its text",
        blocks=(question.handles["setting"], question.handles["we-fix-a-field"]),
    )
    after = corrected(paper, roled, merge)

    carried = carry(paper.document, previous, roled, after, FILES)

    # The merged block's handle is its own first words'.
    merged = "setting-we-fix-a"
    assert carried.reopened == (merged,)
    assert written_lines(carried.sheet)[merged] == WRITTEN["we-fix-a-field"]
    assert f"Pervasive: {merged}" in carried.sheet
    assert "we-fix-a-field" not in carried.sheet


def reasked(paper, after, carried, round_=1):
    return reask_question(
        paper.document, after, SESSION, REASK, FILES, carried, round_, ["split: w"]
    )


def done(text: str) -> str:
    """Every block and group without a done mark marked done."""
    owners = written_lines(text)
    out, owner = [], None

    def close() -> None:
        if owner is not None and not any(
            es.DONE.match(line) for line in owners.get(owner, [])
        ):
            out.extend([f"⟦done {owner}⟧", ""])

    for line in text.split("\n"):
        if m := (es.BLOCK.match(line) or es.GROUP.match(line) or es.END.match(line)):
            close()
            owner = None if es.GROUP.match(line) else m.group(1)
        out.append(line)
    close()
    return "\n".join(out)


def test_a_member_s_line_onto_its_group_s_target_is_refused(paper, roled) -> None:
    """The proof's group justifies the theorem: its first block's own line
    onto the theorem argues it twice, and the writer's gate refuses it."""
    question = ask(paper, roled)
    member = "by-definition-def:gain-the"
    lines = {
        **WRITTEN,
        member: [
            *WRITTEN[member],
            '→ justifies thm:bound · "Hence the output is bounded."',
        ],
    }
    found = problems(paper, question, written(shown(question), lines))
    assert any(
        f"{member} → justifies thm:bound: its group {PROOF} justifies thm:bound too"
        in p
        for p in found
    ), found


def test_the_group_rule_spares_a_person_s_kept_line_and_a_line_inside() -> None:
    """A line kept by the keep mark, or pinned, stands; a line from one
    member onto another is the group's own step, never refused; an owner
    the answer may not change is not refused either."""
    sheet = "\n".join(
        [
            "⟦group g proof⟧",
            "",
            "⟦a justification⟧ main.tex:1:1 (prose)",
            "Hence t.",
            "",
            '→ justifies t · "Hence t."',
            '→ justifies b · "Hence"',
            "",
            "⟦b claim⟧ main.tex:2:1 (prose)",
            "So b.",
            "",
            "⟦end group g⟧",
            "",
            '→ justifies t · "Hence t."',
            "",
        ]
    )
    (refused,) = ef.group_line_problems(sheet)
    assert refused.startswith("line 6: a → justifies t: its group g justifies t")
    assert "a person keeps it with the keep mark" in refused
    kept = sheet.replace(
        '→ justifies t · "Hence t."\n→', '= → justifies t · "Hence t."\n→'
    )
    assert ef.group_line_problems(kept) == []
    pinned = [("a", '→ justifies t · "Hence t."', ef.WRITTEN_PIN)]
    assert ef.group_line_problems(sheet, pins=pinned) == []
    assert ef.group_line_problems(sheet, editable={"g"}) == []


def test_an_update_s_open_group_opens_its_member_and_its_gate_holds_it_open(
    paper, roled
) -> None:
    """An update opens the proof's group, whose first block also justifies
    a block outside it: the sheet shows that member open, and the gate's
    reopened owners hold it, so an answer can close it; the group open on
    condition opens it all the same."""
    member = "by-definition-def:gain-the"
    out = '→ justifies a-closing-paragraph-in · "Hence the output is bounded."'
    lines = {**WRITTEN, member: [*WRITTEN[member], out]}
    question = ask(paper, roled)
    carried = ef.Carried(written(shown(question), lines), (), {})
    for conditional in ((), (PROOF,)):
        sheet = ef.update_sheet(
            paper.document, roled, FILES, carried, {PROOF: "it changed"}, {}, ()
        )
        assert member in sheet.reopened
        assert f"⟦done {member}⟧" not in sheet.sheet
        held = ef.reopened_with(sheet, {PROOF: "it changed"}, conditional)
        assert member in held and held[member].startswith(f"a member of {PROOF}")


def test_a_reask_fixes_every_block_it_does_not_reopen(paper, roled) -> None:
    _, after, carried = split_carried(paper, roled)
    question = reasked(paper, after, carried)
    sheet = shown(question)
    assert sheet == carried.sheet

    fixed = done(sheet).replace(
        '⊢ justified · "The output is bounded."',
        '⊢ unsupported · "The output is bounded."',
    )
    assert any(
        p.startswith("thm:bound: its lines are fixed in this pass")
        for p in problems(paper, question, fixed)
    )
    # The split's first block keeps its claim role, until the roles are
    # asked again, and its standing went with its words.
    gain = '→ uses small-means-below-one · "the gain is small" — the footnote says'
    answered = done(
        sheet.replace(f'→ uses {PROOF_BLOCK} · "Next, the bound"\n', "").replace(
            gain, f'{gain}\n⊢ unsupported · "the gain is small"'
        )
    )
    found = check(question, answered, paper.document)
    assert found.valid, found.problems


def test_a_reask_shows_its_corrections_and_why_each_block_is_open(paper, roled) -> None:
    _, after, carried = split_carried(paper, roled)

    question = reasked(paper, after, carried)

    instructions = question.workspace.instructions
    assert instructions.startswith(SESSION.render() + "\n\n" + REASK.render())
    assert "- split: w" in instructions
    assert f"- next-the-bound-is: its edge onto {PROOF_BLOCK}" in instructions
    assert question.prompt == REASK_KICKOFF and question.template is REASK
    assert question.input_cut is after


def test_a_reask_s_key_binds_its_round_sheet_and_reopened_blocks(paper, roled) -> None:
    _, after, carried = split_carried(paper, roled)
    key = reasked(paper, after, carried).key

    assert reasked(paper, after, carried).key == key
    assert reasked(paper, after, carried, round_=2).key != key
    fewer = replace(carried, reopened=carried.reopened[:2])
    assert reasked(paper, after, fewer).key != key
    other = replace(carried, sheet=carried.sheet.replace("⟦done more⟧", ""))
    assert reasked(paper, after, other).key != key


def carried_by(paper, roled, op, names, lines, legend, at=()):
    """The sheet written as `lines` and `legend`, carried over one
    correction `op` of the blocks `names` (handles)."""
    question = ask(paper, roled)
    previous = written(shown(question), lines, legend)
    ids = tuple(question.handles[h] for h in names)
    fields = {"block": ids[0]} if op is CutOp.SPLIT else {"blocks": ids}
    correction = CutCorrection("x1", op, CorrectionClass.OTHER, "w", at=at, **fields)
    after = corrected(paper, roled, correction)
    return carry(paper.document, previous, roled, after, FILES)


CLOSING = "closing-words-of-the"


def test_carry_reopens_a_part_edge_whose_parts_a_split_parted(paper, roled) -> None:
    lines = {**WRITTEN, CLOSING: [f'→ about {PROOF_BLOCK} [by, hence] · "Closing"']}
    legend = [
        *LEGEND[:1],
        f'  {PROOF_BLOCK} [by]: "By Definition"',
        f'  {PROOF_BLOCK} [hence]: "Hence the output is bounded." (definition)',
        *LEGEND[1:],
    ]

    carried = carried_by(
        paper, roled, CutOp.SPLIT, [PROOF_BLOCK], lines, legend, ("Hence the output",)
    )

    assert CLOSING in carried.reopened
    assert "split between blocks or cut through" in carried.why[CLOSING]
    assert f'  {PROOF_BLOCK} [by]: "By Definition"' in carried.sheet
    assert '  hence-the-output-is [hence]: "Hence the output' in carried.sheet


def test_carry_reopens_a_part_edge_whose_words_a_split_cut_through(
    paper, roled
) -> None:
    lines = {**WRITTEN, CLOSING: [f'→ about {PROOF_BLOCK} [dot] · "Closing"']}
    legend = [*LEGEND[:1], f'  {PROOF_BLOCK} [dot]: ". Hence the output"', LEGEND[1]]

    carried = carried_by(
        paper, roled, CutOp.SPLIT, [PROOF_BLOCK], lines, legend, ("Hence the output",)
    )

    assert CLOSING in carried.reopened


MERGED = "setting-we-fix-a"


def test_carry_on_a_merge_reopens_two_edges_now_onto_one_block(paper, roled) -> None:
    """Two part edges, each placed whole, now share a source, kind and
    target: only the collision reopens their source."""
    lines = {
        **WRITTEN,
        "results": [
            '→ uses setting [a] · "Results"',
            '→ uses we-fix-a-field [b] · "Results"',
        ],
    }
    legend = [
        *LEGEND[:1],
        '  setting [a]: "Setting"',
        '  we-fix-a-field [b]: "We fix a field"',
        LEGEND[1],
    ]

    carried = carried_by(
        paper, roled, CutOp.MERGE, ["setting", "we-fix-a-field"], lines, legend
    )

    assert "results" in carried.reopened
    assert carried.why["results"].startswith("two of its edges now point at one")


def test_carry_on_a_merge_tells_apart_two_parts_of_one_label(paper, roled) -> None:
    lines = {
        **WRITTEN,
        "thm:bound": [*WRITTEN["thm:bound"], '→ uses setting [i] · "The output"'],
        CLOSING: ['→ uses we-fix-a-field [i] · "Closing words"'],
    }
    legend = [
        *LEGEND[:1],
        '  setting [i]: "Setting"',
        '  we-fix-a-field [i]: "We fix a field"',
        LEGEND[1],
    ]

    carried = carried_by(
        paper, roled, CutOp.MERGE, ["setting", "we-fix-a-field"], lines, legend
    )

    assert f'  {MERGED} [i]: "Setting"' in carried.sheet
    assert f'  {MERGED} [i-2]: "We fix a field"' in carried.sheet
    found = written_lines(carried.sheet)
    assert f'→ uses {MERGED} [i] · "The output"' in found["thm:bound"]
    assert f'→ uses {MERGED} [i-2] · "Closing words"' in found[CLOSING]
    assert carried.reopened == (MERGED,)


def test_carry_on_a_move_reopens_both_blocks_and_parts_follow_their_words(
    paper, roled
) -> None:
    lines = {
        **WRITTEN,
        "thm:bound": [
            *WRITTEN["thm:bound"],
            '→ uses we-fix-a-field [second] · "The output"',
        ],
        CLOSING: ['→ uses setting · "Closing words"'],
    }
    legend = [*LEGEND[:1], '  we-fix-a-field [second]: "A second sentence"', LEGEND[1]]

    carried = carried_by(
        paper,
        roled,
        CutOp.REBOUND,
        ["setting", "we-fix-a-field"],
        lines,
        legend,
        ("of scalars.",),
    )

    found = written_lines(carried.sheet)
    first, second, *others = carried.reopened
    assert others == [CLOSING]  # its whole-block edge onto a moved block
    # The words straddle the new boundary: the line goes to the block
    # holding the old one's start.
    assert found[first] == WRITTEN["we-fix-a-field"]
    assert not any(es.DONE.match(line) for line in found.get(second, []))
    # The part follows its words into the second block; its source stays
    # fixed.
    assert f'  {second} [second]: "A second sentence"' in carried.sheet
    assert f'→ uses {second} [second] · "The output"' in found["thm:bound"]
    assert "thm:bound" not in carried.reopened


def test_carry_reopens_a_group_whose_own_words_a_split_cut_through(
    paper, roled
) -> None:
    carried = carried_by(
        paper,
        roled,
        CutOp.SPLIT,
        [PROOF_BLOCK],
        WRITTEN,
        LEGEND,
        ("the output is bounded.",),
    )

    assert PROOF in carried.reopened
    assert carried.why[PROOF] == (
        "a cut correction cut through the words of one of its lines"
    )
    assert "⟦done " + PROOF + "⟧" not in carried.sheet


def test_carry_follows_a_handle_whose_suffix_a_split_shifted(tmp_path) -> None:
    """A split's new block takes the handle a later, unchanged block had:
    that block becomes `-2`, keeps its lines and its done mark, and the
    edges onto it follow."""
    paper = make_paper(
        tmp_path / "paper",
        MAIN.replace(
            "Next, the bound is sharp.",
            "Next, the bound is sharp. Closing words of the proof.",
        ),
    )
    roled = first_pass(paper)
    lines = {
        **WRITTEN,
        "a-closing-paragraph-in": [
            *WRITTEN["a-closing-paragraph-in"],
            f'→ uses {CLOSING} · "A closing paragraph"',
        ],
    }

    carried = carried_by(
        paper,
        roled,
        CutOp.SPLIT,
        ["next-the-bound-is"],
        lines,
        LEGEND,
        ("Closing words of the proof.",),
    )

    found = written_lines(carried.sheet)
    assert f"⟦{CLOSING}-2 none⟧" in carried.sheet
    assert found[f"{CLOSING}-2"] == [*WRITTEN[CLOSING], f"⟦done {CLOSING}-2⟧"]
    assert (
        f'→ uses {CLOSING}-2 · "A closing paragraph"' in found["a-closing-paragraph-in"]
    )
    assert carried.reopened == ("next-the-bound-is", CLOSING)


def test_a_reask_fixes_the_parts_and_frame_of_the_blocks_it_does_not_reopen(
    paper, roled
) -> None:
    _, after, carried = split_carried(paper, roled)
    question = reasked(paper, after, carried)
    gain = '→ uses small-means-below-one · "the gain is small" — the footnote says'
    sound = done(
        shown(question)
        .replace(f'→ uses {PROOF_BLOCK} · "Next, the bound"\n', "")
        .replace(gain, f'{gain}\n⊢ unsupported · "the gain is small"')
    )
    assert check(question, answered(question, sound), paper.document).valid

    legend = '  def:gain [gain]: "The gain is the ratio of output to input."'
    changed = sound.replace(legend, '  def:gain [gain]: "The gain is the ratio"')
    assert any(
        "part def:gain [gain]: fixed in this pass" in p
        for p in problems(paper, question, changed)
    )
    stray = sound.replace(legend, f'{legend}\n  def:gain [ratio]: "the ratio"')
    assert any(
        "a new part on def:gain, which is not reopened" in p
        for p in problems(paper, question, stray)
    )
    # A reopened block's edge may name a new part of a fixed block.
    used = stray.replace(
        '⊢ unsupported · "Next, the bound is sharp."',
        '⊢ unsupported · "Next, the bound is sharp."\n'
        '→ uses def:gain [ratio] · "Next, the bound"',
    )
    assert check(question, answered(question, used), paper.document).valid
    gone = sound.replace(f"{legend}\n", "")
    assert any(
        "part def:gain [gain]: fixed in this pass" in p
        for p in problems(paper, question, gone)
    )
    # The tail is reopened, but a fixed block's edge names its part.
    hence = '  hence-the-output-is [hence]: "Hence the output is bounded." (definition)'
    reworded = sound.replace(
        hence, '  hence-the-output-is [hence]: "Hence" (definition)'
    )
    assert any(
        "part hence-the-output-is [hence]: fixed in this pass" in p
        for p in problems(paper, question, reworded)
    )
    framed = sound.replace(
        "Pervasive: we-fix-a-field", "Pervasive: we-fix-a-field, more"
    )
    assert any(
        "Pervasive: more is fixed in this pass" in p
        for p in problems(paper, question, framed)
    )


def test_a_part_start_that_takes_in_a_label_the_block_does_not_own_is_shown(
    tmp_path,
) -> None:
    """The roles placed a part at `Then \\label{…}`, whose
    label the block does not own; the sheet shows the part where the
    assembler placed it, from the block's own words."""
    paper = make_paper(
        tmp_path / "paper",
        MAIN.replace(
            "Closing words of the results.",
            "Closing words of the results. Then \\label{eq:x} the end.",
        ),
    )
    block = next(
        b for b in paper.cut.blocks if "Closing words" in paper.document.text(b.extent)
    )
    roles = CutCorrection(
        "x1",
        CutOp.ROLES,
        CorrectionClass.OTHER,
        "w",
        block=block.id,
        roles=(Role.DEFINITION, Role.CLAIM),
        segments=(
            Segment("", Role.DEFINITION),
            Segment("Then \\label{eq:x}", Role.CLAIM),
        ),
    )
    roled = corrected(paper, paper.cut, roles)

    sheet = shown(ask(paper, roled))

    assert "Closing words of the results. ⟦+ claim⟧ Then" in " ".join(sheet.split())


# --- flags -----------------------------------------------------------


def test_a_flag_line_is_a_flag_and_changes_no_record(paper, roled) -> None:
    question = ask(paper, roled)
    lines = {
        **WRITTEN,
        "def:gain": ['? role "The gain is the ratio" — a definition read as a claim'],
        "next-the-bound-is": [
            '? "the bound is sharp" — no proof follows',
            *WRITTEN["next-the-bound-is"],
        ],
    }

    found = check(
        question, answered(question, written(shown(question), lines)), paper.document
    )

    assert found.valid, found.problems
    assert found.edges == valid(paper, question, written(shown(question)))
    assert found.corrections == ()
    role, friction = found.flags
    assert role.blocks == (question.handles["def:gain"],)
    assert (role.signal, role.evidence) == (Signal.ROLE, ("The gain is the ratio",))
    assert role.note == "a definition read as a claim"
    assert friction.signal is Signal.OTHER
    assert friction.blocks == (question.handles["next-the-bound-is"],)


@pytest.mark.parametrize(
    ("owner", "line", "problem"),
    [
        ("def:gain", '? role "We fix a field" — not its words', "the words are not in"),
        ("def:gain", '? role "The gain"', "say what you read there"),
        ("def:gain", '? roles "The gain" — a typo', "no such signal"),
        ("def:gain", "? role The gain — no quotes", "not a flag line"),
        (PROOF, '? cut "Hence the output" — under a group', "not a group"),
    ],
)
def test_a_bad_flag_line_is_a_problem(paper, roled, owner, line, problem) -> None:
    question = ask(paper, roled)
    lines = {**WRITTEN, owner: [*WRITTEN.get(owner, []), line]}

    found = problems(paper, question, written(shown(question), lines))

    assert any(problem in p for p in found), found


def test_a_merge_of_a_whole_group_follows_its_ungroup_as_one_decision(
    paper, roled
) -> None:
    """Else the merge would leave a group of one block."""
    blocks = [b.id for b in roled.blocks]
    pair = Group(
        f"derivation@{blocks[9]}",
        GroupKind.DERIVATION,
        (blocks[9], blocks[10]),
        None,
        None,
        None,
        None,
    )
    cut = replace(roled, groups=(*roled.groups, pair))
    question = ask(paper, cut)
    lines = {
        **WRITTEN,
        "more": ['✂ merge a-closing-paragraph-in · "More" — a heading and its text'],
    }

    found = check(
        question, answered(question, written(shown(question), lines)), paper.document
    )

    assert found.valid, found.problems
    ungroup, merge = found.corrections
    assert ungroup.op is CutOp.UNGROUP and ungroup.group == pair.id
    assert merge.op is CutOp.MERGE and merge.blocks == (blocks[9], blocks[10])
    assert ungroup.decision == merge.decision == "d1"


# --- carry -------------------------------------------------------------

CUT_LINE = '✂ split "Hence the output" · "Hence the output is bounded." — two claims'


def test_carry_drops_weighed_cut_lines_and_keeps_the_others(paper, roled) -> None:
    question = ask(paper, roled)
    lines = {**WRITTEN, PROOF_BLOCK: [*WRITTEN[PROOF_BLOCK], CUT_LINE]}
    previous = written(shown(question), lines)

    weighed = carry(paper.document, previous, roled, roled, FILES)
    kept = carry(paper.document, previous, roled, roled, FILES, cuts_weighed=False)

    assert CUT_LINE not in weighed.sheet
    assert CUT_LINE in kept.sheet and kept.reopened == ()


def test_carry_reopens_what_it_is_told_with_why(paper, roled) -> None:
    previous, after, plain = split_carried(paper, roled)

    found = carry(
        paper.document,
        previous,
        roled,
        after,
        FILES,
        reopen={"def:gain": "its roles changed", PROOF_BLOCK: "a flag"},
    )

    assert "def:gain" in found.reopened and found.why["def:gain"] == "its roles changed"
    assert found.why[PROOF_BLOCK] == f"a flag; {plain.why[PROOF_BLOCK]}"
    assert "⟦done def:gain⟧" not in found.sheet


def test_carry_reopens_a_group_the_groups_made_and_one_they_dissolved(
    paper, roled
) -> None:
    question = ask(paper, roled)
    lines = {
        **WRITTEN,
        "closing-words-of-the": [f'→ about {PROOF} · "Closing words of the results."'],
    }
    previous = written(shown(question), lines)
    blocks = [b.id for b in roled.blocks]
    made = Group(
        f"derivation@{blocks[9]}",
        GroupKind.DERIVATION,
        (blocks[9], blocks[10]),
        None,
        None,
        None,
        None,
    )
    after = replace(roled, groups=(made,))

    found = carry(paper.document, previous, roled, after, FILES)

    assert "group:more" in found.reopened
    # The proof's lines stay on its first block, now reopened, and the about
    # edge onto it is reopened too.
    assert written_lines(found.sheet)[PROOF_BLOCK][-1] == WRITTEN[PROOF][0]
    assert PROOF_BLOCK in found.reopened and "closing-words-of-the" in found.reopened
    assert "dissolved" in found.why[PROOF_BLOCK]


# --- the round takes roles -------------------------------------------

FLAG_LINE = '? role "The output is bounded." — a claim read as a definition'


def test_a_reask_may_flag_a_block_it_does_not_reopen(paper, roled) -> None:
    _, after, carried = split_carried(paper, roled)
    question = reasked(paper, after, carried)
    sheet = shown(question)
    gain = '→ uses small-means-below-one · "the gain is small" — the footnote says'
    answered = done(
        sheet.replace(f'→ uses {PROOF_BLOCK} · "Next, the bound"\n', "").replace(
            gain, f'{gain}\n⊢ unsupported · "the gain is small"'
        )
    ).replace("⟦done thm:bound⟧", f"{FLAG_LINE}\n⟦done thm:bound⟧")

    found = check(question, answered, paper.document)

    assert "thm:bound" not in carried.reopened
    assert found.valid, found.problems
    assert [f.note for f in found.flags] == ["a claim read as a definition"]


def test_carry_takes_a_flag_line_with_its_block(paper, roled) -> None:
    question = ask(paper, roled)
    lines = {**WRITTEN, "thm:bound": [*WRITTEN["thm:bound"], FLAG_LINE]}
    previous = written(shown(question), lines)

    found = carry(paper.document, previous, roled, roled, FILES)

    assert written_lines(found.sheet, flags=True)["thm:bound"][1] == FLAG_LINE
    assert FLAG_LINE not in written_lines(found.sheet)["thm:bound"]
    assert found.reopened == ()


def test_a_changed_role_reopens_the_block_and_what_justifies_or_qualifies_it(
    paper, roled
) -> None:
    question = ask(paper, roled)
    previous = written(shown(question))
    gain = question.handles["def:gain"]

    found = reopened_by_roles(
        paper.document, previous, roled, roled, {gain: "definition → claim"}
    )

    assert found == {
        "def:gain": "its roles changed: definition → claim",
        "we-fix-a-field": "its qualifies edge onto def:gain, whose roles changed",
    }


def test_a_changed_role_reopens_a_block_restating_it_and_no_other_about(
    paper, roled
) -> None:
    """A restatement borrows its target's claim, which a role change may
    take away: the closing paragraph, restating the theorem, is reopened;
    the sharpness, about the theorem and unsupported, is not."""
    question = ask(paper, roled)
    sharp = "next-the-bound-is"
    previous = written(
        shown(question),
        {
            **WRITTEN,
            sharp: [
                '→ about thm:bound · "Next, the bound is sharp."',
                *WRITTEN[sharp],
            ],
        },
    )
    theorem = question.handles["thm:bound"]

    found = reopened_by_roles(
        paper.document, previous, roled, roled, {theorem: "claim → motivation"}
    )

    assert found == {
        "thm:bound": "its roles changed: claim → motivation",
        PROOF: "its justifies edge onto thm:bound, whose roles changed",
        "a-closing-paragraph-in": "its about edge onto thm:bound, whose roles changed",
    }


def test_a_review_pass_shown_the_rules_flags_says_what_they_are(paper, roled) -> None:
    question = ask(paper, roled)
    previous = written(shown(question))
    edges = check(question, answered(question, previous), paper.document).edges
    shown_rules = [r for r in rule_flags(roled, edges, roled) if r.stage == "edges"]

    plain = review_of(paper, roled, previous)
    flagged = edges_review(paper, roled, previous, shown_rules)

    assert plain.key == edges_review(paper, roled, previous, []).key
    assert flagged.key != plain.key
    assert "Positions the rules took" in flagged.workspace.instructions
    assert "- we-fix-a-field → def:gain: qualifies" in flagged.workspace.instructions
    assert "Positions the rules took" not in plain.workspace.instructions


def edges_review(paper, cut, previous, rules):
    return review_question(
        paper.document, cut, SESSION, REVIEW, FILES, previous, "k1", 2, None, rules
    )


# --- the sheet carried across two versions -------------------


def across(paper, roled, tmp_path, main: str):
    """The writer's sheet on `roled`, carried onto a new version of the
    paper, its corrected cut rebased."""
    from axiomation.identity import Version, pair, rebase_cut

    previous = written(shown(ask(paper, roled)))
    new = make_paper(tmp_path / "v2", main=main)
    old = Version(paper.document, roled)
    rebased = rebase_cut(old, new.document, new.cut)
    lineage = pair(old, Version(new.document, rebased.cut))
    carried = carry(
        paper.document,
        previous,
        roled,
        rebased.cut,
        FILES,
        new=new.document,
        lineage=lineage,
    )
    return previous, rebased, carried


def test_across_an_insertion_every_block_keeps_its_lines_and_done_mark(
    paper, roled, tmp_path
) -> None:
    main = MAIN.replace(
        "Closing words of the results.",
        "An inserted remark, new here.\n\nClosing words of the results.",
    )
    previous, rebased, carried = across(paper, roled, tmp_path, main)
    assert carried.reopened == ("an-inserted-remark-new",)
    assert carried.why == {"an-inserted-remark-new": "it is new in this version"}
    before, after = written_lines(previous), written_lines(carried.sheet)
    for handle, lines in before.items():
        assert after[handle] == lines, handle  # the done mark among them
    # the inserted paragraph, the only region to re-cut
    assert rebased.recut == ("main.tex:22:1",) and rebased.touched == ()
    for line in LEGEND:
        assert line in carried.sheet


def test_across_a_changed_block_s_lines_follow_their_words_and_a_hint_stays_apart(
    paper, roled, tmp_path
) -> None:
    """An edge whose evidence is
    gone is demoted to the block it stood on, which is reopened; its fuzzy
    hint is returned beside the sheet, and the line keeps its old words."""
    main = MAIN.replace("the gain is small", "the gain is tall")
    previous, rebased, carried = across(paper, roled, tmp_path, main)
    assert carried.reopened == (PROOF_BLOCK,)
    assert carried.why[PROOF_BLOCK] == "its words changed"
    after = written_lines(carried.sheet)
    assert after[PROOF_BLOCK] == WRITTEN[PROOF_BLOCK]  # no done mark: reopened
    (hint,) = carried.hints
    assert (hint.handle, hint.excerpt, hint.words) == (
        PROOF_BLOCK,
        "the gain is small",
        "the gain is tall",
    )
    assert '"the gain is tall"' not in carried.sheet  # the hint, never written
    # the proof keeps its group: the cutter's, over the same blocks
    assert after[PROOF] == [*WRITTEN[PROOF], f"⟦done {PROOF}⟧"]


def test_across_an_edge_onto_a_deleted_block_is_dropped_and_its_source_reopened(
    paper, roled, tmp_path
) -> None:
    """The closing words deleted: their own lines go with them, and an edge
    onto them is dropped, its source reopened."""
    from axiomation.identity import Version, pair, rebase_cut

    previous = written(
        shown(ask(paper, roled)),
        {
            **WRITTEN,
            "a-closing-paragraph-in": [
                *WRITTEN["a-closing-paragraph-in"],
                '→ uses closing-words-of-the · "A closing paragraph"',
            ],
        },
    )
    new = make_paper(
        tmp_path / "v2", main=MAIN.replace("Closing words of the results.\n", "")
    )
    old = Version(paper.document, roled)
    rebased = rebase_cut(old, new.document, new.cut)
    lineage = pair(old, Version(new.document, rebased.cut))
    carried = carry(
        paper.document,
        previous,
        roled,
        rebased.cut,
        FILES,
        new=new.document,
        lineage=lineage,
    )
    assert "closing-words-of-the" not in carried.sheet
    assert "Closing words of the results." not in carried.sheet
    assert carried.reopened == ("a-closing-paragraph-in",)
    assert carried.why["a-closing-paragraph-in"] == (
        "its edge onto closing-words-of-the, deleted in this version, is gone: "
        '`→ uses closing-words-of-the · "A closing paragraph"`'
    )
    assert carried.gone == (
        Gone(
            "a-closing-paragraph-in",
            '→ uses closing-words-of-the · "A closing paragraph"',
            "closing-words-of-the",
            "main.tex:22:1",
        ),
    )
    assert (
        written_lines(carried.sheet)["a-closing-paragraph-in"]
        == WRITTEN["a-closing-paragraph-in"]
    )


def test_across_a_lost_edge_is_said_where_its_source_was_reopened_first(
    paper, roled, tmp_path
) -> None:
    """The source's own words edited too: it is reopened for that first,
    and the edge it lost is still said, so the writer learns of it."""
    from axiomation.identity import Version, pair, rebase_cut

    previous = written(
        shown(ask(paper, roled)),
        {
            **WRITTEN,
            "a-closing-paragraph-in": [
                *WRITTEN["a-closing-paragraph-in"],
                '→ uses closing-words-of-the · "A closing paragraph"',
            ],
        },
    )
    new = make_paper(
        tmp_path / "v2",
        main=MAIN.replace("Closing words of the results.\n", ""),
        extra=EXTRA.replace("another file.", "another file, edited."),
    )
    old = Version(paper.document, roled)
    rebased = rebase_cut(old, new.document, new.cut)
    lineage = pair(old, Version(new.document, rebased.cut))
    carried = carry(
        paper.document,
        previous,
        roled,
        rebased.cut,
        FILES,
        new=new.document,
        lineage=lineage,
    )
    assert carried.why["a-closing-paragraph-in"] == (
        "its words changed; its edge onto closing-words-of-the, deleted in this "
        'version, is gone: `→ uses closing-words-of-the · "A closing paragraph"`'
    )
    assert [g.source for g in carried.gone] == ["a-closing-paragraph-in"]


def test_a_carried_cut_line_a_new_group_now_crosses_is_found_stuck(
    paper, roled
) -> None:
    """A fixed block's `✂ merge` that a group made since now
    crosses would trap the re-ask; it is found, and reopening it frees it."""
    question = ask(paper, roled)
    merge = '✂ merge results · "The gain is the ratio" — one set-up'
    lines = {**WRITTEN, "def:gain": [merge]}
    previous = written(shown(question), lines)
    blocks = [b.id for b in roled.blocks]
    made = Group(
        f"derivation@{blocks[3]}",
        GroupKind.DERIVATION,
        (blocks[3], blocks[4]),
        None,
        None,
        None,
        None,
    )
    after = replace(roled, groups=(*roled.groups, made))

    carried = carry(paper.document, previous, roled, after, FILES, cuts_weighed=False)
    stuck = stuck_cut_lines(paper.document, carried, after)
    freed = carry(
        paper.document,
        previous,
        roled,
        after,
        FILES,
        cuts_weighed=False,
        reopen=stuck,
    )

    assert "def:gain" not in carried.reopened
    assert list(stuck) == ["def:gain"]
    assert "def:gain" in freed.reopened
    assert stuck_cut_lines(paper.document, freed, after) == {}


def test_across_a_deleted_block_s_words_found_in_a_neighbour_are_asked(
    paper, roled, tmp_path
) -> None:
    """A deleted block's part, and its own line, whose
    words a neighbour happens to hold: the edge's source and the receiving
    block are both reopened, never moved silently."""
    from axiomation.identity import Version, pair, rebase_cut

    lines = {
        **WRITTEN,
        "a-closing-paragraph-in": [
            *WRITTEN["a-closing-paragraph-in"],
            '→ uses closing-words-of-the [cw] · "A closing paragraph"',
        ],
        "closing-words-of-the": [
            *WRITTEN["closing-words-of-the"],
            '→ uses def:gain · "the"',
        ],
    }
    previous = written(
        shown(ask(paper, roled)), lines, ['  closing-words-of-the [cw]: "the"', *LEGEND]
    )
    new = make_paper(
        tmp_path / "v2", main=MAIN.replace("Closing words of the results.\n", "")
    )
    old = Version(paper.document, roled)
    rebased = rebase_cut(old, new.document, new.cut)
    lineage = pair(old, Version(new.document, rebased.cut))
    carried = carry(
        paper.document,
        previous,
        roled,
        rebased.cut,
        FILES,
        new=new.document,
        lineage=lineage,
    )
    assert set(carried.reopened) == {"next-the-bound-is", "a-closing-paragraph-in"}
    assert "closing-words-of-the" in carried.why["next-the-bound-is"]


# --- the edges' role correction ------------------------------------------

CLOSING = "closing-words-of-the"
CLOSING_CLAIM = '⊢ unsupported · "Closing words of the results."'


def with_role(cut, block_id: str, roles, segments):
    """`cut` with one block's roles and segments replaced."""
    return replace(
        cut,
        blocks=tuple(
            replace(b, roles=roles, segments=segments) if b.id == block_id else b
            for b in cut.blocks
        ),
    )


def test_a_standing_on_a_block_with_no_claim_role_adds_a_claim(paper, roled) -> None:
    """The gate takes the standing, and the answer records a `ROLES`
    correction: the claim added, its evidence the standing's words, the
    edges stage its source (the answer it comes from, set by the
    resolver), after the cut lines."""
    question = ask(paper, roled)
    lines = {**WRITTEN, CLOSING: [*WRITTEN[CLOSING], CLOSING_CLAIM]}

    found = check(
        question, answered(question, written(shown(question), lines)), paper.document
    )

    assert found.valid, found.problems
    (correction,) = found.corrections
    assert correction.op is CutOp.ROLES
    assert correction.block == paper.id_at("Closing words")
    assert correction.roles == (Role.CLAIM,)
    assert correction.segments == (Segment("", Role.CLAIM),)
    assert correction.evidence == ("Closing words of the results.",)
    assert correction.why == CLAIM_ADDED_WHY
    assert correction.check() == ()
    standing = [s for s in found.edges.standings if s.block == correction.block]
    assert [s.status for s in standing] == [Status.UNSUPPORTED]


def test_a_block_with_a_claim_role_adds_none(paper, roled) -> None:
    question = ask(paper, roled)

    found = check(
        question, answered(question, written(shown(question))), paper.document
    )

    assert found.valid and found.corrections == ()


TWO_SENTENCES = MAIN.replace(
    "Closing words of the results.",
    "Closing words of the results. They end the section here.",
)
THREE_SENTENCES = MAIN.replace(
    "Closing words of the results.",
    "Closing words of the results. They end the section here. A last remark follows.",
)


def closing(tmp_path, main: str, roles=(), segments=()):
    """The closing paragraph of a paper written as `main`, with `roles` and
    `segments`, and the paper."""
    paper = make_paper(tmp_path / "paper-under-test", main)
    block = paper.id_at("Closing words")
    cut = with_role(first_pass(paper), block, tuple(roles), tuple(segments))
    return paper, cut, cut.block(block)


def applies(paper, cut, block, roles, segments) -> None:
    """The correction places each start once (the assembler's own check)."""
    applied = corrected_cut(
        paper.document,
        cut,
        [
            CutCorrection(
                "c1",
                CutOp.ROLES,
                CorrectionClass.OTHER,
                CLAIM_ADDED_WHY,
                block=block.id,
                roles=roles,
                segments=segments,
            )
        ],
    )
    assert applied.problems == ()
    assert applied.cut.block(block.id).segments == segments


MOTIVATION = ((Role.MOTIVATION,), (Segment("", Role.MOTIVATION),))


def test_a_mid_sentence_quote_snaps_to_its_sentence_s_start(tmp_path) -> None:
    """The quote shows which sentence
    claims; the part starts at that sentence's start, ends where the next
    sentence starts, and the enclosing role takes up again after it."""
    paper, cut, block = closing(tmp_path, THREE_SENTENCES, *MOTIVATION)

    roles, segments, used = claim_part(paper.document, block, ["the section here"])

    assert roles == (Role.CLAIM, Role.MOTIVATION)
    assert segments == (
        Segment("", Role.MOTIVATION),
        Segment("They end the", Role.CLAIM),
        Segment("A last remark", Role.MOTIVATION),
    )
    assert used == ("the section here",)
    applies(paper, cut, block, roles, segments)


def test_a_claim_at_the_block_s_start_and_the_role_after_it(tmp_path) -> None:
    paper, cut, block = closing(
        tmp_path, TWO_SENTENCES, (Role.DEFINITION,), (Segment("", Role.DEFINITION),)
    )

    roles, segments, _ = claim_part(paper.document, block, ["of the results"])

    assert roles == (Role.DEFINITION, Role.CLAIM)
    assert segments == (
        Segment("", Role.CLAIM),
        Segment("They end the", Role.DEFINITION),
    )


def test_a_quote_over_two_sentences_claims_both(tmp_path) -> None:
    paper, cut, block = closing(tmp_path, THREE_SENTENCES, *MOTIVATION)

    roles, segments, _ = claim_part(paper.document, block, ["results. They end"])

    assert segments == (
        Segment("", Role.CLAIM),
        Segment("A last remark", Role.MOTIVATION),
    )
    applies(paper, cut, block, roles, segments)


def test_the_start_never_crosses_the_segment_s_own_start(tmp_path) -> None:
    """A segment the roles started mid-sentence: the claim's sentence starts
    before it, and the part starts at the segment's start."""
    paper, cut, block = closing(
        tmp_path,
        THREE_SENTENCES,
        (Role.DEFINITION, Role.MOTIVATION),
        (Segment("", Role.MOTIVATION), Segment("of the results.", Role.DEFINITION)),
    )

    roles, segments, _ = claim_part(paper.document, block, ["the results"])

    assert roles == (Role.DEFINITION, Role.CLAIM, Role.MOTIVATION)
    assert segments == (
        Segment("", Role.MOTIVATION),
        Segment("of the results.", Role.CLAIM),
        Segment("They end the", Role.DEFINITION),
    )
    applies(paper, cut, block, roles, segments)


def test_a_claim_over_one_segment_of_several_stands_beside_every_role(
    tmp_path,
) -> None:
    """The claim covers the motivation's whole text, up to the next
    segment's start: a correction never removes a role, and a
    segment holds one, so the claim stands beside both, unsegmented."""
    paper, cut, block = closing(
        tmp_path,
        THREE_SENTENCES,
        (Role.DEFINITION, Role.MOTIVATION),
        (Segment("", Role.MOTIVATION), Segment("They end the", Role.DEFINITION)),
    )

    roles, segments, used = claim_part(paper.document, block, ["results. They end"])

    assert roles == (Role.DEFINITION, Role.CLAIM, Role.MOTIVATION)
    assert segments == ()
    assert used == ("results. They end",)
    applies(paper, cut, block, roles, segments)


def test_a_claim_over_the_assumption_s_whole_part_keeps_the_assumption(
    paper, roled
) -> None:
    """The setting's `assumption` part is one sentence, then a part that
    plays none: claimed, the assumption would have no text left, so the
    claim stands beside it, unsegmented."""
    block = roled.block(paper.id_at("We fix a field"))
    assert [s.role for s in block.segments] == [Role.ASSUMPTION, None]

    roles, segments, _ = claim_part(paper.document, block, ["field of scalars"])

    assert roles == (Role.ASSUMPTION, Role.CLAIM)
    assert segments == ()


def test_two_standings_on_a_block_with_no_role_make_two_parts(tmp_path) -> None:
    paper, cut, block = closing(tmp_path, THREE_SENTENCES)
    assert block.roles == ()

    roles, segments, used = claim_part(
        paper.document, block, ["A last remark", "the results"]
    )

    assert roles == (Role.CLAIM,)
    assert segments == (
        Segment("", Role.CLAIM),
        Segment("They end the", None),
        Segment("A last remark", Role.CLAIM),
    )
    assert used == ("A last remark", "the results")
    applies(paper, cut, block, roles, segments)


def test_a_second_standing_inside_a_claim_part_adds_none(tmp_path) -> None:
    paper, _, block = closing(tmp_path, THREE_SENTENCES, *MOTIVATION)

    _, segments, used = claim_part(
        paper.document, block, ["They end the section", "the section here"]
    )

    assert used == ("They end the section",)
    assert [s.role for s in segments] == [Role.MOTIVATION, Role.CLAIM, Role.MOTIVATION]


def test_a_quote_that_stands_twice_is_placed_at_its_first(tmp_path) -> None:
    paper, _, block = closing(
        tmp_path,
        MAIN.replace(
            "Closing words of the results.",
            "Closing words of the results. They end the results here.",
        ),
        *MOTIVATION,
    )

    _, segments, _ = claim_part(paper.document, block, ["the results"])

    assert segments == (
        Segment("", Role.CLAIM),
        Segment("They end the", Role.MOTIVATION),
    )


def test_a_block_with_no_role_takes_a_claim_part_and_none_after_it(
    tmp_path,
) -> None:
    paper, _, block = closing(tmp_path, TWO_SENTENCES)

    roles, segments, _ = claim_part(paper.document, block, ["the results"])

    assert roles == (Role.CLAIM,)
    assert segments == (Segment("", Role.CLAIM), Segment("They end the", None))


def test_a_mixed_block_not_yet_segmented_takes_the_claim_role_alone(paper) -> None:
    block = replace(
        paper.cut.block(paper.id_at("Closing words")),
        roles=(Role.DEFINITION, Role.MOTIVATION),
        segments=(),
    )

    roles, segments, _ = claim_part(paper.document, block, ["Closing words"])

    assert roles == (Role.DEFINITION, Role.CLAIM, Role.MOTIVATION)
    assert segments == ()


def test_a_claim_over_a_whole_single_segment_keeps_its_role(paper, roled) -> None:
    """The definition's one sentence claimed: `\\begin{definition}` before
    it and `\\end{definition}` after it are markup, no text for the
    definition, which a span may play beside the claim: both
    roles, unsegmented."""
    block = roled.block(paper.id_at("\\begin{definition}"))
    assert block.roles == (Role.DEFINITION,)

    roles, segments, _ = claim_part(paper.document, block, ["ratio of output"])

    assert roles == (Role.DEFINITION, Role.CLAIM)
    assert segments == ()


@pytest.mark.parametrize("role", [Role.ASSUMPTION, Role.MOTIVATION, Role.JUSTIFICATION])
def test_a_one_sentence_block_claimed_keeps_its_role(tmp_path, role) -> None:
    """Three blocks: one sentence, one role, a standing
    on a clause of it. The block keeps the role it was read with, the
    claim beside it."""
    paper, cut, block = closing(tmp_path, MAIN, (role,), (Segment("", role),))

    roles, segments, used = claim_part(paper.document, block, ["of the results"])

    assert roles == tuple(sorted((role, Role.CLAIM), key=list(Role).index))
    assert segments == ()
    assert used == ("of the results",)
    applies(paper, cut, block, roles, segments)


DISPLAYED = MAIN.replace(
    "Closing words of the results.",
    "Closing words of the results. We note\n\\begin{equation}\nx = 1. Y = 2\n"
    "\\end{equation}\nfor all $x$. Then more follows.",
)


def test_a_quote_in_a_display_snaps_to_the_sentence_it_is_set_in(tmp_path) -> None:
    """A display belongs to its sentence: its full stop ends none, and
    neither its capital after it nor its `\\end{…}` starts one."""
    paper, cut, block = closing(tmp_path, DISPLAYED, *MOTIVATION)

    roles, segments, _ = claim_part(paper.document, block, ["Y = 2"])

    assert roles == (Role.CLAIM, Role.MOTIVATION)
    assert segments == (
        Segment("", Role.MOTIVATION),
        Segment("We note \\begin{equation}", Role.CLAIM),
        Segment("Then more follows.", Role.MOTIVATION),
    )
    applies(paper, cut, block, roles, segments)


def test_a_full_stop_before_a_closing_bracket_starts_no_sentence(tmp_path) -> None:
    """`\\[ x = 1. \\]`: the stop is the display's, so no part starts at
    `\\]` and the claim runs on to the next sentence the text starts."""
    paper, cut, block = closing(
        tmp_path,
        MAIN.replace(
            "Closing words of the results.",
            "Closing words of the results. We note \\[ x = 1. \\] for all $x$. "
            "Then more follows.",
        ),
        *MOTIVATION,
    )

    _, segments, _ = claim_part(paper.document, block, ["We note"])

    assert segments == (
        Segment("", Role.MOTIVATION),
        Segment("We note \\[", Role.CLAIM),
        Segment("Then more follows.", Role.MOTIVATION),
    )
    applies(paper, cut, block, roles=(Role.CLAIM, Role.MOTIVATION), segments=segments)


def test_a_nested_block_s_text_neither_starts_nor_ends_a_sentence(paper, roled) -> None:
    """The proof's first paragraph, its footnote a block of its own: the
    claim runs over the footnote's place to the stop after it."""
    proof = paper.id_at("\\begin{proof}")
    cut = with_role(
        roled, proof, (Role.JUSTIFICATION,), (Segment("", Role.JUSTIFICATION),)
    )
    block = cut.block(proof)
    assert any(b.within == proof for b in cut.blocks)

    roles, segments, _ = claim_part(paper.document, block, ["the gain is small"])

    assert roles == (Role.CLAIM, Role.JUSTIFICATION)
    assert segments == (
        Segment("", Role.CLAIM),
        Segment("Hence the output", Role.JUSTIFICATION),
    )
    applies(paper, cut, block, roles, segments)


def test_a_part_that_cannot_be_placed_is_a_role_flag_not_a_refusal(
    paper, roled, monkeypatch
) -> None:
    """A paid answer is never lost to a claim part that cannot be placed:
    the answer stands, its claim beside the block's roles (a block with
    none takes it whole), and a `role` flag says why."""
    question = ask(paper, roled)
    lines = {**WRITTEN, CLOSING: [*WRITTEN[CLOSING], CLOSING_CLAIM]}
    text = written(shown(question), lines)

    def unplaceable(*args):
        raise RenderError("its part starting 'x' matches 2 times")

    monkeypatch.setattr("axiomation.questions.edges_file.claim_part", unplaceable)
    found = check(question, answered(question, text), paper.document)

    assert found.valid
    (correction,) = found.corrections
    assert correction.block == paper.id_at("Closing words")
    assert correction.roles == (Role.CLAIM,)
    assert correction.segments == (Segment("", Role.CLAIM),)
    assert correction.evidence == ("Closing words of the results.",)
    assert correction.why.startswith(CLAIM_BESIDE_WHY) and "2 times" in correction.why
    (flag,) = found.flags
    assert flag.signal is Signal.ROLE and flag.blocks == (paper.id_at("Closing words"),)
    assert flag.evidence == ("Closing words of the results.",)
    assert flag.note.startswith(CLAIM_NOT_PLACED) and "2 times" in flag.note


def test_any_fault_in_placing_a_part_is_a_role_flag_naming_its_type(
    paper, roled, monkeypatch
) -> None:
    """Not only the expected `ValueError`s: any exception in placing a part
    leaves the paid answer valid, its flag naming the exception's type."""
    question = ask(paper, roled)
    lines = {**WRITTEN, CLOSING: [*WRITTEN[CLOSING], CLOSING_CLAIM]}

    def broken(*args):
        raise KeyError("paper.md")

    monkeypatch.setattr("axiomation.questions.edges_file.claim_part", broken)
    found = check(
        question, answered(question, written(shown(question), lines)), paper.document
    )

    assert found.valid and len(found.corrections) == 1
    (flag,) = found.flags
    assert flag.note == f"{CLAIM_NOT_PLACED}: KeyError: 'paper.md'"


def test_a_person_s_answer_whose_claim_cannot_be_placed_is_taken(
    paper, roled, monkeypatch
) -> None:
    """A person's answer holds no flag: an unplaceable claim part no longer
    refuses it; the claim stands beside the block's roles."""
    sheet = shown(ask(paper, roled))
    question = person_question(paper.document, roled, SESSION, FILES, sheet)
    lines = {**WRITTEN, CLOSING: [*WRITTEN[CLOSING], CLOSING_CLAIM]}

    def unplaceable(*args):
        raise RenderError("its part starting 'x' matches 2 times")

    monkeypatch.setattr("axiomation.questions.edges_file.claim_part", unplaceable)
    found = check(question, answered(question, written(sheet, lines)), paper.document)

    assert found.valid, found.problems
    assert found.flags == ()
    (correction,) = found.corrections
    assert correction.roles == (Role.CLAIM,)
    assert correction.why.startswith(CLAIM_BESIDE_WHY) and "2 times" in correction.why


def test_a_fault_placing_a_person_s_claim_is_raised(paper, roled, monkeypatch) -> None:
    """A person's answer is not paid for: a fault other than a `ValueError`
    in placing its part is raised, never kept as a correction."""
    sheet = shown(ask(paper, roled))
    question = person_question(paper.document, roled, SESSION, FILES, sheet)
    lines = {**WRITTEN, CLOSING: [*WRITTEN[CLOSING], CLOSING_CLAIM]}

    def broken(*args):
        raise KeyError("paper.md")

    monkeypatch.setattr("axiomation.questions.edges_file.claim_part", broken)
    with pytest.raises(KeyError):
        check(question, answered(question, written(sheet, lines)), paper.document)


def test_a_qualifying_block_claimed_whole_passes_its_own_sheet_s_gate(
    paper, roled
) -> None:
    """A standing on the assumption's whole
    sentence. The state the correction leaves keeps the assumption, so the
    sheet written on it, its `qualifies` line included, passes the gate."""
    setting = "we-fix-a-field"
    claimed = '⊢ unsupported · "We fix a field of scalars."'
    lines = {**WRITTEN, setting: [*WRITTEN[setting], claimed]}
    question = ask(paper, roled)
    found = check(
        question, answered(question, written(shown(question), lines)), paper.document
    )
    assert found.valid, found.problems
    (correction,) = found.corrections
    assert correction.roles == (Role.ASSUMPTION, Role.CLAIM)
    assert correction.why == CLAIM_BESIDE_WHY

    applied = corrected_cut(paper.document, roled, [replace(correction, id="c1")])
    assert applied.problems == ()
    kept = ask(paper, applied.cut)

    assert "⟦we-fix-a-field assumption+claim⟧" in shown(kept)
    found = check(kept, answered(kept, written(shown(kept), lines)), paper.document)
    assert found.valid, found.problems
    assert found.corrections == ()


ABBREVIATED = MAIN.replace(
    "Closing words of the results.",
    "Closing words of the results, e.g. $x$ and $y$, see p. \\pageref{thm:bound}. "
    "They end the section here.",
)


def test_an_abbreviation_s_full_stop_ends_no_sentence(tmp_path) -> None:
    """ "e.g. $x$" and "p. \\pageref": the part runs past both to the
    sentence's own end."""
    paper, cut, block = closing(tmp_path, ABBREVIATED, *MOTIVATION)

    roles, segments, _ = claim_part(paper.document, block, ["and $y$"])

    assert segments == (
        Segment("", Role.CLAIM),
        Segment("They end the", Role.MOTIVATION),
    )
    applies(paper, cut, block, roles, segments)


def test_a_block_whose_roles_a_person_gave_takes_no_claim_from_a_standing(
    paper, roled
) -> None:
    """`claims_fixed` (a person's roles pin): the gate
    refuses the standing, as before, and the spec names it only
    where there is one."""
    closing = paper.id_at("Closing words")
    question = session_question(
        paper.document, roled, SESSION, FILES, claims_fixed={closing}
    )
    lines = {**WRITTEN, CLOSING: [*WRITTEN[CLOSING], CLOSING_CLAIM]}

    found = check(
        question, answered(question, written(shown(question), lines)), paper.document
    )

    assert any(
        "a support line on a block with no claim: a person gave its roles" in p
        for p in found.problems
    ), found.problems
    assert json.loads(question.workspace.gate.spec)[CLAIMS_FIXED] == [CLOSING]
    assert CLAIMS_FIXED not in json.loads(ask(paper, roled).workspace.gate.spec)
    assert question.key == ask(paper, roled).key


# --- a kept state's own problems (docs/model.md § 7) ------------------------------


def test_a_kept_sheet_s_problems_are_keyed_without_their_line_and_renamed() -> None:
    """What today's check refuses in a kept sheet, said without the sheet
    line it stands on, and named by the handles of the cut an answer on it
    is given on."""
    from axiomation.questions.edges_file import kept_problems, problem_key
    from questions.test_edge_sheet import SYNTHETIC

    assert kept_problems(SYNTHETIC, ["paper.md"]) == ()
    broken = SYNTHETIC.replace("⊢ justified", "⊢ proven")
    (found,) = kept_problems(broken, ["paper.md"])
    assert found == "b3 ⊢ proven: no such status"
    (renamed,) = kept_problems(broken, ["paper.md"], onto={"b3": "thm:main"})
    assert renamed == "thm:main ⊢ proven: no such status"
    assert problem_key("line 12: b3 → uses b9: no such target") == (
        "b3 → uses b9: no such target"
    )


def test_a_kept_problem_says_the_same_after_an_unrelated_edit() -> None:
    """A kept problem is matched by what it says (docs/model.md § 7), so no
    message may carry what an edit elsewhere changes: a line number, a
    count, a list of other lines or of a block's claims. An edge added above
    every problem, or a claim added to a block holding one, leaves each
    said as before."""
    from collections import Counter

    from axiomation.questions.edges_file import kept_problems
    from questions.test_edge_sheet import SYNTHETIC

    broken = (
        SYNTHETIC.replace("⊢ justified", "⊢ proven")
        .replace("→ uses b3 · ", "→ uses b9 · ")
        .replace("→ qualifies b1 [x]", "→ qualifies b1 [y]")
        .replace('→ self · "with its own reason"', '→ self · "no such words"')
        .replace('"The claim" (definition)', '"The claim"')
        .replace('⊢ self · "A remark on the claim"', "")
    )
    elsewhere = broken.replace(
        "Let $x$ be one.\n⟦done b1⟧",
        'Let $x$ be one.\n→ about scope:paper · "Let $x$ be one"\n⟦done b1⟧',
    )
    on_its_block = broken.replace(
        "⟦done b3⟧", '⊢ unsupported [below] · "nothing below contradicts"\n⟦done b3⟧'
    )
    found = kept_problems(broken, ["paper.md"])

    assert any(p.startswith("part b3 [the claim]") for p in found), found
    assert "no support line: b4" in found, found
    assert kept_problems(elsewhere, ["paper.md"]) == found
    assert not Counter(found) - Counter(kept_problems(on_its_block, ["paper.md"]))


def test_a_kept_block_the_answer_s_cut_does_not_hold_lends_no_problem() -> None:
    """Renamed onto a cut where another block takes `b3`'s handle and `b3`
    has none (a merge, a split's heir holding its label), `b3`'s problem is
    no problem of the block now named so."""
    from axiomation.questions.edges_file import UNHELD, kept_problems, unkept
    from questions.test_edge_sheet import SYNTHETIC

    broken = SYNTHETIC.replace("⊢ justified", "⊢ proven")
    (found,) = kept_problems(broken, ["paper.md"], onto={"b1": "b3"})
    assert found == f"{UNHELD}b3 ⊢ proven: no such status"
    # each kept problem stands for as many times as the kept sheet says it
    assert unkept(["line 3: x", "line 9: x", "y"], ["x"]) == ["line 9: x", "y"]


def test_a_partial_s_problem_implicates_the_flagged_line_alone() -> None:
    """Two justifies lines from one block onto one target: the analogy's
    refusal names the analogy's pin, not the written case's."""
    from axiomation.questions.edges_file import DUPLICATE, implicated

    pins = [
        ("p", '→ justifies t [a] · "Hence"', "written"),
        ("p", '→ justifies t [c] analogy · "likewise"', "written"),
    ]
    refused = "line 9: p → justifies t: an analogy names the argument it carries over"
    assert [n for n, _ in implicated(refused, pins)] == [1]
    twice = f"p → justifies t: {DUPLICATE} (two justifies lines name disjoint claims)"
    assert [why for _, why in implicated(twice, pins)][0].startswith(DUPLICATE)


# --- a line and its claim's standing -----------------------------------------------

#: Two blocks: `t` holds two claims standing unsupported, `j` argues one.
TWO_CLAIMS = "\n".join(
    [
        "⟦t claim⟧ main.tex:1:1 (prose)",
        "A holds. B holds.",
        '⊢ unsupported promised [a] · "A holds." — the paper says later',
        '⊢ unsupported [b] · "B holds."',
        "⟦done t⟧",
        "⟦j justification⟧ main.tex:2:1 (prose)",
        "Hence B, as follows.",
        "⟦done j⟧",
        "⟦s claim⟧ main.tex:3:1 (prose)",
        "In short, B holds.",
        '⊢ unsupported · "In short, B holds."',
        "⟦done s⟧",
    ]
)


def _applied(*lines: tuple[str, str, str], sheet: str = TWO_CLAIMS) -> str:
    from axiomation.questions.edges_file import Fragment, applied_fragment

    return applied_fragment(sheet, Fragment(lines=lines))


def test_a_line_added_stands_the_unsupported_claim_it_names_argued() -> None:
    """A justifies line naming `t`'s claim `b` stands `b` justified, its
    handle kept and `a` left alone; an about line onto `b` stands `s`'s
    one claim restated (`docs/model.md` § 4)."""
    from axiomation.questions.edges_file import ADD

    found = _applied(("j", ADD, '→ justifies t [b] · "Hence B"'))
    assert '⊢ justified [b] · "B holds."' in found
    assert "⊢ unsupported [b]" not in found
    assert "⊢ unsupported promised [a]" in found
    found = _applied(("s", ADD, '→ about [in-short-b-holds] t [b] · "In short"'))
    assert '⊢ restated · "In short, B holds."' in found


def test_an_about_line_naming_no_claim_restates_only_for_a_restating_author() -> None:
    """An about line is mostly a description: one naming no claim at its
    source end stands `s`'s one claim restated only where its author
    writes it as a restatement (the search, the review)."""
    from axiomation.questions.edges_file import ADD, Fragment, applied_fragment

    line = ("s", ADD, '→ about t [b] · "In short"')
    assert "⊢ restated" not in _applied(line)
    assert '⊢ restated · "In short, B holds."' in applied_fragment(
        TWO_CLAIMS, Fragment(lines=(line,)), restating=True
    )


def test_an_about_line_naming_no_claim_is_said_with_the_line_that_would() -> None:
    """A person's about line naming no claim at its source end, onto a
    claim the paper asserts, leaves `s`'s claim unsupported: said, with
    the line naming it and the standing to set; nothing is said where the
    line names it, describes (`where-used`), or the fragment sets the
    standing itself."""
    from axiomation.questions.edges_file import ADD, Fragment, restating_none

    def said(*lines: tuple[str, str, str]) -> list[str]:
        return restating_none(TWO_CLAIMS, Fragment(lines=lines))

    (note,) = said(("s", ADD, '→ about t [b] · "In short"'))
    assert "names no claim at its source end" in note
    assert "[in-short-b-holds] still stands `unsupported`" in note
    assert '`→ about [in-short-b-holds] t [b] · "In short"`' in note
    assert """--standing s '⊢ <status> · "In short, B holds."'""" in note
    assert said(("s", ADD, '→ about [in-short-b-holds] t [b] · "In short"')) == []
    assert said(("s", ADD, '→ about t [b] where-used · "In short"')) == []
    kept = TWO_CLAIMS.replace(
        '⊢ unsupported · "In short, B holds."', '= ⊢ unsupported · "In short, B holds."'
    )
    assert kept != TWO_CLAIMS
    assert (
        restating_none(
            kept, Fragment(lines=(("s", ADD, '→ about t [b] · "In short"'),))
        )
        == []
    )
    assert (
        said(
            ("s", ADD, '→ about t [b] · "In short"'),
            ("s", ADD, '⊢ restated · "In short, B holds."'),
        )
        == []
    )


def test_a_line_given_back_stands_only_what_it_newly_names() -> None:
    """A line rewritten (a reread's new quote) lends no new support; a
    fallback narrowed to name `b` stands `b`, which a circle's answer then
    refuses; a line given back naming `a` too stands `a`."""
    from axiomation.questions.edges_file import ADD, RETRACT, Fragment, stood_argued

    sheet = TWO_CLAIMS.replace("⟦done j⟧", '→ justifies t [b] · "Hence B"\n⟦done j⟧')
    rewritten = Fragment(
        lines=(
            ("j", RETRACT, '→ justifies t [b] · "Hence B"'),
            ("j", ADD, '→ justifies t [b] · "Hence B, as"'),
        )
    )
    assert stood_argued(sheet, rewritten) == []
    fallback = TWO_CLAIMS.replace("⟦done j⟧", '→ justifies t · "Hence B"\n⟦done j⟧')
    narrowed = Fragment(
        lines=(
            ("j", RETRACT, '→ justifies t · "Hence B"'),
            ("j", ADD, '→ justifies t [b] · "Hence B"'),
        )
    )
    assert ("t", ADD, '⊢ justified [b] · "B holds."') in stood_argued(
        fallback, narrowed
    )
    widened = Fragment(
        lines=(
            ("j", RETRACT, '→ justifies t [b] · "Hence B"'),
            ("j", ADD, '→ justifies t [a, b] · "Hence B"'),
        )
    )
    assert ("t", ADD, '⊢ justified [a] · "A holds."') in stood_argued(sheet, widened)


def test_a_line_naming_no_claim_of_several_stands_none_argued() -> None:
    """A fallback end reads toward the alarm: a justifies line naming no
    claim of `t`'s two, and an about line that describes (`where-used`),
    stand nothing."""
    from axiomation.questions.edges_file import ADD

    found = _applied(("j", ADD, '→ justifies t · "Hence B"'))
    assert "⊢ justified" not in found
    found = _applied(("s", ADD, '→ about t [b] where-used · "In short"'))
    assert "⊢ restated" not in found


def test_a_fragment_s_own_standing_wins_and_a_kept_one_is_refused() -> None:
    """A fragment setting the claim's standing itself keeps its own; a
    standing a person kept `unsupported` is refused, unless the fragment
    sets it."""
    from axiomation.questions.edges_file import ADD, RETRACT, FragmentError

    own = (
        ("j", ADD, '→ justifies t [b] · "Hence B"'),
        ("t", RETRACT, '⊢ unsupported [b] · "B holds."'),
        ("t", ADD, '⊢ self [b] · "B holds."'),
    )
    found = _applied(*own)
    assert '⊢ self [b] · "B holds."' in found and "⊢ justified" not in found
    kept = TWO_CLAIMS.replace("⊢ unsupported [b]", es.KEEP_MARK + "⊢ unsupported [b]")
    with pytest.raises(FragmentError, match="a person kept its standing"):
        _applied(("j", ADD, '→ justifies t [b] · "Hence B"'), sheet=kept)
    assert "⊢ self [b]" in _applied(*own, sheet=kept)


# --- a state's sheet ----------------------------------------------------------------


INTERLEAVED = {
    **WRITTEN,
    "by-definition-def:gain-the": [
        '→ uses def:gain [gain] ref · "By Definition~\\ref{def:gain}"',
        '→ self · "Hence"',
        '→ uses small-means-below-one · "the gain is small" — the footnote says',
        '⊢ self · "Hence the output is bounded."',
    ],
}


def test_an_owner_whose_kinds_interleave_files_its_line_order(paper, roled) -> None:
    """Each kind's records keep their sheet order; an owner's order across
    kinds is a record of its own, written only where kinds interleave."""
    question = ask(paper, roled)

    assert valid(paper, question, written(shown(question))).orders == ()
    found = valid(paper, question, written(shown(question), INTERLEAVED))

    (order,) = found.orders
    assert order.owner == question.handles["by-definition-def:gain-the"]
    assert order.kinds == (
        LineKind.EDGE,
        LineKind.SELF,
        LineKind.EDGE,
        LineKind.STANDING,
    )


@pytest.mark.parametrize("lines", [WRITTEN, INTERLEAVED], ids=["plain", "interleaved"])
def test_a_state_s_sheet_renders_back_from_its_export(
    paper, roled, tmp_path, lines
) -> None:
    """The sheet a kept state shows, rendered from its export and its
    answer's line orders, is the answer's sheet line for line."""
    from axiomation.exchange import edges_payload
    from axiomation.records import RecordStore
    from axiomation.resolver import Attempt, Resolver

    question = ask(paper, roled)
    found = check(
        question, answered(question, written(shown(question), lines)), paper.document
    )
    assert found.valid, found.problems
    recorded = Resolver(RecordStore(tmp_path / "store")).record(
        Attempt(question, "fake", "fake/model", found.kept, 0.0, 0.0, found)
    )
    export = edges_payload(
        paper.document, roled, recorded.edges, recorded.answer, question.handles
    )

    sheet = ef.state_sheet(paper.document, roled, FILES, export, recorded.edges.orders)

    assert sheet == ef.carry(paper.document, found.kept, roled, roled, FILES).sheet
