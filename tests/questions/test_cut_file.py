"""The boundary session's file: `cut.md` rendered, edited and parsed back
No model is called."""

from __future__ import annotations

import pytest

from axiomation.assembler import corrected_cut
from axiomation.questions import Stage, load_template, reask
from axiomation.questions.cut_file import (
    DECLINED,
    FILE_TEMPLATE,
    JOIN,
    JOINED,
    JUDGE_TEMPLATE,
    PROPOSED,
    SPLIT,
    Insertion,
    RenderError,
    check,
    declines,
    judge_questions,
    parse,
    settled,
)
from axiomation.questions.cut_file import file_questions as build_file_questions
from axiomation.records import CorrectionClass, CutCorrection, CutOp
from questions.review_paper import MAIN, file_questions, make_paper


@pytest.fixture
def results(paper):
    """The chunk "Results" at 300 characters, no context: b1 its heading,
    b2 the theorem, b3 and b4 the proof's first paragraph and its
    footnote, b5 its second paragraph, b6 the closing paragraph."""
    return file_questions(paper)[1]


@pytest.fixture
def setting(paper):
    """The chunk "Setting": b1 its heading, b2 its paragraph, b3 the
    definition. No group holds them."""
    return file_questions(paper)[0]


@pytest.fixture
def middle(paper):
    """The proof's first paragraph alone, at 100 characters, with the two
    blocks either side as context."""
    return file_questions(paper, 100)[3]


def text(question) -> str:
    return question.workspace.files["cut.md"]


def marker(question, handle: str) -> str:
    return next(
        line for line in text(question).splitlines() if line.startswith(f"⟦{handle} ")
    )


def corrections(paper, question, edited):
    found = check(question, edited, paper.document)
    assert found.valid, found.problems
    return found.corrections


def problems(paper, question, edited):
    found = check(question, edited, paper.document)
    assert not found.valid
    return found.problems


def test_the_file_unchanged_corrects_nothing(paper) -> None:
    for budget in (0, 100, 300, None):
        for question in file_questions(paper, budget):
            assert corrections(paper, question, text(question)) == ()


def test_a_new_marker_splits_its_block(paper, results) -> None:
    edited = text(results).replace("Hence the output", "⟦new⟧\nHence the output")

    (split,) = corrections(paper, results, edited)

    assert split.op is CutOp.SPLIT
    assert split.block == "main.tex:15:1"
    assert split.at == ("Hence the output",)


def test_a_marker_mid_line_splits_there(paper, results) -> None:
    edited = text(results).replace("the gain is small", "the gain\n⟦new⟧\nis small")

    (split,) = corrections(paper, results, edited)

    # Words from the source, which runs on into the footnote nested there.
    assert split.block == "main.tex:15:1"
    assert split.at == ("is small\\footnote{Small means",)


def test_a_deleted_marker_joins_its_block_to_the_one_before(paper, setting) -> None:
    edited = text(setting).replace(marker(setting, "b3") + "\n", "")

    (merge,) = corrections(paper, setting, edited)

    assert merge.op is CutOp.MERGE
    assert merge.blocks == ("main.tex:4:1", "main.tex:7:1")


def test_a_moved_marker_is_a_join_and_a_split(paper, setting) -> None:
    edited = text(setting).replace(marker(setting, "b3") + "\n", "")
    edited = edited.replace("The gain is", "\n⟦new⟧\nThe gain is")

    merge, split = corrections(paper, setting, edited)

    assert merge.op is CutOp.MERGE
    assert merge.blocks == ("main.tex:4:1", "main.tex:7:1")
    assert split.block == "main.tex:4:1"
    assert split.at == ("The gain is",)


def test_a_new_marker_where_a_deleted_one_stood_puts_it_back(paper, results) -> None:
    edited = text(results).replace(marker(results, "b5"), "⟦new⟧")

    assert corrections(paper, results, edited) == ()


def test_a_changed_role_word_changes_nothing(paper, results) -> None:
    """Only cuts are read back; roles are the roles stage's."""
    line = marker(results, "b5")
    edited = text(results).replace(line, line.replace("⟦b5 none⟧", "⟦b5 lemma⟧"))

    assert corrections(paper, results, edited) == ()


def test_an_invented_handle_is_read_as_a_new_block(paper, results) -> None:
    edited = text(results).replace("Hence the output", "⟦b3b claim⟧\nHence the output")

    (split,) = corrections(paper, results, edited)

    assert split.op is CutOp.SPLIT


def test_a_changed_text_fails_the_answer(paper, results) -> None:
    edited = text(results).replace("the gain is small", "the gain is tiny")

    (problem,) = problems(paper, results, edited)

    assert problem == (
        "line 13: the paper's text changed: it reads “By Definition~\\ref{def:gain}, "
        "the gain is tiny” where it read “By Definition~\\ref{def:gain}, the gain is "
        "small”; put the text back as it was"
    )


def test_line_breaks_and_blank_lines_do_not_matter(paper, results) -> None:
    edited = text(results).replace("Hence the output", "\n\nHence\nthe output")

    assert corrections(paper, results, edited) == ()


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        (
            lambda t: t.replace("##### main.tex", "##### other.tex"),
            "a '##### <file>' line was changed, moved or removed",
        ),
        (
            lambda t: t.replace("⟦b1 continued⟧\n", ""),
            "a '⟦context⟧' or '⟦bN continued⟧' line was changed, moved or removed",
        ),
    ],
)
def test_a_broken_structure_fails_the_answer(paper, middle, change, problem) -> None:
    (found,) = problems(paper, middle, change(text(middle)))

    assert found.startswith(problem)


def test_the_context_is_not_the_session_s_to_cut(paper, middle) -> None:
    edited = text(middle).replace("Closing words", "⟦new⟧\nClosing words")

    (problem,) = problems(paper, middle, edited)

    assert "lies in the context" in problem


def test_the_first_block_cannot_join_the_context(paper, middle) -> None:
    edited = text(middle).replace(marker(middle, "b1") + "\n", "")

    (problem,) = problems(paper, middle, edited)

    assert "it is the first block of cut.md" in problem


def test_a_context_marker_removed_fails_the_answer(paper, middle) -> None:
    edited = text(middle).replace("⟦context⟧\nNext", "Next")

    (problem,) = problems(paper, middle, edited)

    assert "'⟦context⟧'" in problem


def test_parse_gives_the_answer_by_handle(paper, setting) -> None:
    edited = text(setting).replace(marker(setting, "b3") + "\n", "")

    assert parse(setting, edited, paper.document) == {
        "corrections": [
            {
                "op": "merge",
                "blocks": ["b2", "b3"],
                "class": "other",
                "why": "Joined in cut.md.",
            }
        ]
    }


def test_a_session_s_reask_names_the_problems_not_the_edit(paper, results) -> None:
    prompt = reask(results, "the whole edited file", ["the paper's text changed"])

    assert prompt.startswith(results.prompt)
    assert "- the paper's text changed" in prompt
    assert "the whole edited file" not in prompt
    assert "The file is as it was before that session." in prompt


def test_the_assembler_s_refusal_fails_the_whole_answer(paper, results) -> None:
    """Joining the theorem to its heading would move the start of the
    block the proof's group names: refused, and with it the answer's
    other, good, edit."""
    edited = text(results).replace(marker(results, "b2") + "\n", "")
    edited = edited.replace("Hence the output", "⟦new⟧\nHence the output")

    (problem,) = problems(paper, results, edited)

    assert "moves the start of the block group" in problem


def test_the_outline_marks_where_the_chunk_lies(results) -> None:
    outline = results.workspace.files["outline.md"]

    lines = outline.splitlines()
    start = lines.index(
        next(line for line in lines if line.startswith("→ `cut.md` starts here"))
    )
    assert lines[start + 1] == "- Results"
    assert "  - theorem (thm:bound): The output is bounded. …" in lines
    assert lines[lines.index("→ `cut.md` ends here") + 1] == "- More"


def test_a_paper_line_that_reads_as_a_marker_is_refused(tmp_path) -> None:
    paper = make_paper(
        tmp_path / "odd", MAIN.replace("Closing words", "⟦b1⟧ Closing words")
    )

    with pytest.raises(RenderError, match="would read as a marker"):
        file_questions(paper)


def test_without_the_outline_a_session_has_cut_md_alone(paper, results) -> None:
    from axiomation.questions import load_template
    from axiomation.questions.cut_file import FILE_TEMPLATE, file_questions

    bare = file_questions(
        paper.document, paper.cut, 300, load_template(FILE_TEMPLATE), with_outline=False
    )[1]

    assert list(bare.workspace.files) == ["cut.md"]
    assert bare.workspace.files["cut.md"] == text(results)
    assert "outline" not in bare.workspace.instructions
    assert "You are given one part of the paper.\n" in bare.workspace.instructions
    assert bare.key != results.key


def test_an_author_pass_as_lines_reads_paper_md_and_writes_cuts_md(
    paper, results
) -> None:
    """The author's brief up to its part on the file, then the lines'
    section; a split as a line corrects as the same split edited in."""
    from axiomation.questions.cut_file import FILE_PART, LINES_KICKOFF

    lined = build_file_questions(
        paper.document, paper.cut, 300, load_template(FILE_TEMPLATE), lines=True
    )[1]

    workspace = lined.workspace
    assert workspace.files == {
        "paper.md": text(results),
        "cuts.md": "",
        "outline.md": results.workspace.files["outline.md"].replace(
            "`cut.md`", "`paper.md`"
        ),
    }
    assert workspace.edit == "cuts.md" and lined.prompt == LINES_KICKOFF
    assert workspace.gate.module == "axiomation.questions.front_lines"
    brief = workspace.instructions
    assert FILE_PART not in brief and "`cut.md`" not in brief and "$" not in brief
    assert "## The rules" in brief and "Write your corrections in `cuts.md`" in brief
    assert "marks where your part lies. Write your corrections" in brief
    assert lined.key != results.key

    split = "Hence the output is bounded."
    edited = text(results).replace(split, f"⟦new⟧\n{split}")
    by_line = check(lined, f'split b3 "{split}"\n', paper.document)
    by_file = check(results, edited, paper.document)
    assert by_line.valid and by_line.corrections == by_file.corrections
    assert by_line.kept is not None and by_line.kept.split() == edited.split()
    nowhere = check(lined, 'split b3 "not in the paper"\n', paper.document)
    assert nowhere.problems == (
        '\'split b3 "not in the paper"\': the words "not in the paper" stand '
        "nowhere in b3: quote words that stand there once",
    )
    assert nowhere.kept is None


def test_the_brief_names_the_outline_where_the_session_has_one(results) -> None:
    brief = results.workspace.instructions

    assert "$" not in brief
    assert "`outline.md` shows where that part lies in the whole." in brief
    assert "marks where your part lies. `cut.md` is your answer" in brief


# --- the judge, its flags and the ledger ----------------------------------


def split_correction(block: str, *at: str) -> CutCorrection:
    return CutCorrection(
        "c1", CutOp.SPLIT, CorrectionClass.OTHER, "w", block=block, at=at
    )


def merge_correction(*blocks: str) -> CutCorrection:
    return CutCorrection("c1", CutOp.MERGE, CorrectionClass.OTHER, "w", blocks=blocks)


def block_of(paper, words: str, file: str = "main.tex") -> str:
    """The id of the cut's block whose extent holds `words`."""
    at = paper.document.file(file).text.index(words)
    for block in paper.cut.blocks:
        if block.extent.file == file and block.extent.start <= at < block.extent.end:
            return block.id
    raise AssertionError(words)


def applied(paper, *corrections: CutCorrection):
    found = corrected_cut(paper.document, paper.cut, corrections)
    assert not found.problems, found.problems
    return found.cut


def judge(paper, before, after, key: str = "author-key"):
    return judge_questions(
        paper.document, after, before, [key], None, load_template(JUDGE_TEMPLATE)
    )


def proof_block(paper) -> str:
    """The cut's block for the proof's first paragraph (holds the
    footnote)."""
    return block_of(paper, "By Definition")


def test_the_judge_s_file_flags_the_author_s_split(paper) -> None:
    before = paper.cut
    after = applied(paper, split_correction(proof_block(paper), "Hence the output"))

    (question,) = judge(paper, before, after)

    flagged = [
        line
        for line in text(question).splitlines()
        if line.startswith("⟦b") and PROPOSED in line
    ]
    assert len(flagged) == 1
    assert question.stage is Stage.JUDGE and question.judges == "author-key"


def test_the_judge_s_file_shows_the_author_s_merge_as_joined(paper) -> None:
    before = paper.cut
    after = applied(
        paper,
        merge_correction(
            block_of(paper, "We fix a field"), block_of(paper, "The gain is")
        ),
    )

    (question,) = judge(paper, before, after)

    joined = [
        line for line in text(question).splitlines() if line.startswith(f"⟦{JOINED}⟧")
    ]
    assert len(joined) == 1
    assert "took in" in joined[0]


def test_a_judge_who_keeps_everything_corrects_nothing(paper) -> None:
    before = paper.cut
    after = applied(paper, split_correction(proof_block(paper), "Hence the output"))

    (question,) = judge(paper, before, after)

    assert corrections(paper, question, text(question)) == ()


def test_a_judge_declining_a_split_merges_it_back(paper) -> None:
    before = paper.cut
    after = applied(paper, split_correction(proof_block(paper), "Hence the output"))
    (question,) = judge(paper, before, after)
    body = text(question)
    line = next(
        found
        for found in body.splitlines()
        if found.startswith("⟦b") and PROPOSED in found
    )
    edited = body.replace(
        line, line.replace(PROPOSED, f"{DECLINED} — the verification is short")
    )

    (merge,) = corrections(paper, question, edited)

    assert merge.op is CutOp.MERGE
    reverted = corrected_cut(paper.document, after, [merge])
    assert not reverted.problems
    assert [b.id for b in reverted.cut.blocks] == [b.id for b in before.blocks]


def test_a_judge_declining_a_merge_splits_it_back(paper) -> None:
    before = paper.cut
    after = applied(
        paper,
        merge_correction(
            block_of(paper, "We fix a field"), block_of(paper, "The gain is")
        ),
    )
    (question,) = judge(paper, before, after)
    body = text(question)
    line = next(found for found in body.splitlines() if found.startswith(f"⟦{JOINED}⟧"))
    edited = body.replace(line, f"⟦{JOINED} {DECLINED}⟧ — they are separate claims")

    (split,) = corrections(paper, question, edited)

    assert split.op is CutOp.SPLIT
    reverted = corrected_cut(paper.document, after, [split])
    assert not reverted.problems
    assert [b.id for b in reverted.cut.blocks] == [b.id for b in before.blocks]


def test_a_judge_that_adds_a_cut_of_its_own_fails(paper) -> None:
    """The judge keeps or declines the author's edits, nothing else."""
    (question,) = judge(paper, paper.cut, paper.cut)

    for mark in ("⟦new⟧", f"⟦{DECLINED} — its own block⟧"):
        edited = text(question).replace("Hence the output", f"{mark}\nHence the output")
        (problem,) = problems(paper, question, edited)
        assert "only keeps or declines" in problem


def test_a_judge_that_deletes_a_marker_fails(paper) -> None:
    (question,) = judge(paper, paper.cut, paper.cut)
    line = marker(question, "b9")

    (problem,) = problems(paper, question, text(question).replace(f"{line}\n", ""))

    assert "only keeps or declines" in problem


def test_a_judge_cannot_decline_a_boundary_the_author_left(paper) -> None:
    before = paper.cut
    after = applied(paper, split_correction(proof_block(paper), "Hence the output"))
    (question,) = judge(paper, before, after)
    line = marker(question, "b9")

    (problem,) = problems(
        paper, question, text(question).replace(line, f"⟦b9 {DECLINED} — no⟧")
    )

    assert "only a 'proposed' or 'joined' marker" in problem


def test_declines_reads_the_reasons_from_a_judge_s_answer(paper) -> None:
    before = paper.cut
    after = applied(paper, split_correction(proof_block(paper), "Hence the output"))
    (question,) = judge(paper, before, after)
    body = text(question)
    line = next(
        found
        for found in body.splitlines()
        if found.startswith("⟦b") and PROPOSED in found
    )
    edited = body.replace(
        line, line.replace(PROPOSED, f"{DECLINED} — short verification")
    )

    (found,) = declines(question, edited, paper.document)

    assert found.line == settled(SPLIT, "short verification")
    assert found.line == "⟦declined split⟧ — short verification"
    assert found.file == "main.tex"
    assert paper.document.file("main.tex").text[found.offset :].startswith("Hence")


def test_declines_names_a_declined_join_as_one(paper) -> None:
    """A declined join puts the boundary back; its ledger line says so."""
    before = paper.cut
    after = applied(
        paper,
        merge_correction(
            block_of(paper, "We fix a field"), block_of(paper, "The gain is")
        ),
    )
    (question,) = judge(paper, before, after)
    body = text(question)
    line = next(found for found in body.splitlines() if found.startswith(f"⟦{JOINED}⟧"))

    (found,) = declines(
        question,
        body.replace(line, f"⟦{JOINED} {DECLINED} — separate claims⟧"),
        paper.document,
    )

    assert found.line == settled(JOIN, "separate claims")
    assert (
        paper.document.file("main.tex")
        .text[found.offset :]
        .startswith("\\begin{definition}")
    )


def test_the_ledger_is_shown_and_the_parse_ignores_it(paper, setting) -> None:
    at = paper.document.file("main.tex").text.index("the ratio")
    line = settled(SPLIT, "short verification")
    question = build_file_questions(
        paper.document,
        paper.cut,
        300,
        load_template(FILE_TEMPLATE),
        ledger=[Insertion("main.tex", at, line)],
    )[0]

    assert line in text(question)
    assert question.key != setting.key
    # ignored, whether left, deleted, or overridden by an ordinary marker
    assert corrections(paper, question, text(question)) == ()
    assert corrections(paper, question, text(question).replace(f"{line}\n", "")) == ()
    (split,) = corrections(
        paper, question, text(question).replace(line, f"{line}\n⟦new⟧")
    )
    assert split.op is CutOp.SPLIT


def test_markers_stand_at_the_count_of_non_whitespace_characters_before_them() -> None:
    """Every whitespace `str.isspace` names is left out of the count, a
    non-breaking space and an em space among them."""
    from axiomation.questions.cut_file import _stream_lines, read_markers

    text = (
        "##### main.tex\n"
        "We fix a field.\n"
        "⟦b2 none⟧\n"
        "\tIts scalars act.  \n"
        "##### extra.tex\n"
        "⟦b3 none⟧\n"
        "End.\n"
    )
    found = read_markers(text)
    assert found.stream == "Wefixafield.Itsscalarsact.End."
    assert list(found.headers) == [("main.tex", 0), ("extra.tex", 26)]
    assert [m.at for m in found.markers] == [12, 26]
    assert _stream_lines(text) == [2] * 12 + [4] * 14 + [7] * 4
