"""The cut-review question: keys, prompts, answers.
The boundary stage is a session per chunk."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from axiomation.questions import Stage, Template, load_template, reask
from axiomation.questions.cut_file import (
    FILE_TEMPLATE,
    JUDGE_TEMPLATE,
    file_questions,
    judge_questions,
)
from axiomation.questions.cut_review import check, extract, validate
from axiomation.records import CorrectionClass, CutCorrection, CutOp
from questions.review_paper import HIDDEN, MAIN, make_paper

GOLDEN = Path(__file__).parent / "golden"
BOUNDARIES = load_template(FILE_TEMPLATE)
JUDGE = load_template(JUDGE_TEMPLATE)


def boundaries(paper, budget=300, template=BOUNDARIES, overlap=2):
    """At 300 characters the second chunk is the section "Results": b1 its
    heading, b2 the theorem, b3 and b4 the proof's first paragraph and its
    footnote, b5 its second paragraph, b6 the closing paragraph."""
    return file_questions(paper.document, paper.cut, budget, template, overlap)


def shown(question) -> list[str]:
    """Everything a question shows: its prompt, a session's brief and
    files."""
    found = [question.prompt]
    if question.workspace is not None:
        found += [question.workspace.instructions, *question.workspace.files.values()]
    return found


def prompt_only(paper):
    """A question answered from its prompt alone, in JSON: a boundary
    question with no session, as a host without sessions would ask one."""
    return replace(boundaries(paper)[1], workspace=None, prompt="Answer in JSON.")


# --- templates and keys ------------------------------------------------------------


def test_a_template_s_version_is_a_hash_of_its_text() -> None:
    edited = Template(BOUNDARIES.name, BOUNDARIES.text + "\n")

    assert len(BOUNDARIES.version) == 12
    assert edited.version != BOUNDARIES.version
    assert Template(BOUNDARIES.name, BOUNDARIES.text).version == BOUNDARIES.version


def test_keys_are_stable_across_two_builds(paper, tmp_path) -> None:
    again = make_paper(tmp_path / "again")

    assert [q.key for q in boundaries(paper)] == [q.key for q in boundaries(again)]


def test_a_template_edit_changes_every_key(paper) -> None:
    edited = Template(BOUNDARIES.name, BOUNDARIES.text + "\n")

    before = {q.key for q in boundaries(paper)}
    after = {q.key for q in boundaries(paper, template=edited)}
    assert not before & after


def test_an_edit_changes_its_chunk_s_key_alone(paper, tmp_path):
    """One block per chunk, with context around it: the edited block's key
    changes, and no other, though it is shown beside its neighbours: context
    is not part of the key (`docs/architecture.md` § 2)."""
    edited = make_paper(
        tmp_path / "edited",
        MAIN.replace("Next, the bound is sharp.", "Next, the bound is tight."),
    )

    old = [q.key for q in boundaries(paper, 0)]
    new = [q.key for q in boundaries(edited, 0)]
    changed = [i for i, (a, b) in enumerate(zip(old, new, strict=True)) if a != b]
    chunks = [q.handles["b1"] for q in boundaries(paper, 0)]
    assert [chunks[i] for i in changed] == ["main.tex:19:1"]


def test_a_moved_block_is_asked_again(paper, tmp_path) -> None:
    """A blank line before the closing paragraph moves it (its id is its
    position): it is asked again, and nothing else is."""
    moved = make_paper(
        tmp_path / "moved",
        MAIN.replace("Closing words", "\nClosing words"),
    )

    old = [q.key for q in boundaries(paper, 0)]
    new = [q.key for q in boundaries(moved, 0)]
    assert old[:-3] == new[:-3]
    assert old[-3] != new[-3]  # the closing paragraph, moved a line down
    assert old[-2:] == new[-2:]  # extra.tex: another file, its ids unchanged


def test_a_later_pass_asks_an_unchanged_chunk_again(paper) -> None:
    first = boundaries(paper)
    second = file_questions(paper.document, paper.cut, 300, BOUNDARIES, 2, 2)

    assert not {q.key for q in first} & {q.key for q in second}


# --- prompts ------------------------------------------------------------------------


def _golden(name: str, text: str) -> None:
    path = GOLDEN / name
    assert path.exists(), f"missing golden {path}; write it deliberately, reviewed"
    assert text == path.read_text(encoding="utf-8"), (
        f"{name} differs from its golden; review the change before updating it"
    )


def test_the_boundary_session_s_files_match_their_golden(paper) -> None:
    files = boundaries(paper, 100)[3].workspace.files
    _golden("cut.md", files["cut.md"])
    _golden("outline.md", files["outline.md"])


def test_the_boundary_session_s_brief_matches_its_golden(paper) -> None:
    """The boundary session's brief, pinned: it opens on splitting every
    proof at its steps."""
    brief = boundaries(paper, 100)[3].workspace.instructions

    _golden("boundary-brief.md", brief)
    assert "Above all, split every proof" in brief
    assert BOUNDARIES.version == "eec2015b393b"


def test_prompts_hold_no_absolute_path_and_no_hidden_text(paper) -> None:
    prompts = [
        text
        for budget in (0, 200, None)
        for q in boundaries(paper, budget)
        for text in shown(q)
    ]

    for prompt in prompts:
        assert str(paper.root) not in prompt
        assert str(paper.root.resolve()) not in prompt
        assert HIDDEN not in prompt
        assert "\\label{" not in prompt


def test_handles_name_the_chunk_s_blocks(paper) -> None:
    second = boundaries(paper)[1]

    assert dict(second.handles) == {
        "b1": "main.tex:11:1",
        "b2": "main.tex:12:1",
        "b3": "main.tex:15:1",
        "b4": "main.tex:16:48",
        "b5": "main.tex:19:1",
        "b6": "main.tex:22:1",
    }
    assert "⟦b4 none⟧" in second.workspace.files["cut.md"]


# --- the judge's question -------------------------------------------------


def split_proof(paper) -> CutCorrection:
    """The author's edit the judge tests build on: the proof's first
    paragraph split before its conclusion."""
    at = paper.document.file("main.tex").text.index("Hence the output")
    block = next(
        b
        for b in paper.cut.blocks
        if b.extent.file == "main.tex" and b.extent.start <= at < b.extent.end
    )
    return CutCorrection(
        "c1",
        CutOp.SPLIT,
        CorrectionClass.OTHER,
        "w",
        block=block.id,
        at=("Hence the output",),
    )


def judged(paper, key: str = "author-key", template=JUDGE):
    """The judge's sessions over the paper the author's split left."""
    from axiomation.assembler import corrected_cut

    after = corrected_cut(paper.document, paper.cut, [split_proof(paper)])
    assert not after.problems, after.problems
    return judge_questions(paper.document, after.cut, paper.cut, [key], None, template)


def test_the_judge_s_key_names_the_author_it_reviews(paper) -> None:
    first = judged(paper, key="author-1")
    second = judged(paper, key="author-2")

    assert first[0].key != second[0].key
    assert [q.key for q in judged(paper)] == [q.key for q in judged(paper)]
    assert first[0].stage is Stage.JUDGE


def test_a_judge_template_edit_changes_every_judge_key(paper) -> None:
    edited = Template(JUDGE.name, JUDGE.text + "\n")

    before = {q.key for q in judged(paper)}
    after = {q.key for q in judged(paper, template=edited)}

    assert not before & after


def test_a_chunked_judge_names_the_author_key_of_its_chunk(paper) -> None:
    from axiomation.assembler import corrected_cut

    after = corrected_cut(paper.document, paper.cut, [split_proof(paper)]).cut
    found = judge_questions(
        paper.document, after, paper.cut, ["a1", "a2", "a3"], 300, JUDGE
    )

    assert [q.judges for q in found] == ["a1", "a2", "a3"]


def test_the_judge_s_prompts_hold_no_absolute_path_and_no_hidden_text(paper) -> None:
    for question in judged(paper):
        for text in shown(question):
            assert str(paper.root) not in text
            assert str(paper.root.resolve()) not in text
            assert HIDDEN not in text
            assert "\\label{" not in text


def _rules(template: Template) -> list[str]:
    """The bullets of a brief's `## The rules` section, as paragraphs."""
    section = template.text.split("## The rules\n", 1)[1].split("\n## ", 1)[0]
    return [" ".join(b.split()) for b in section.split("\n- ") if b.strip()]


def test_the_judge_judges_by_the_author_s_rules() -> None:
    """The judge's rules are the author's, word for word, but for
    the author's own tasks (rebound, the cutter's doubts), so a judge
    never undoes a split the author's rules call for."""
    author = _rules(BOUNDARIES)
    judge = _rules(JUDGE)

    assert judge == [
        rule
        for rule in author
        if not rule.startswith("**Rebound**") and "cutter's doubts" not in rule
    ]


def test_the_judge_s_files_match_their_golden(paper) -> None:
    (question,) = judged(paper)

    _golden("judge-cut.md", question.workspace.files["cut.md"])
    _golden("judge-brief.md", question.workspace.instructions)


# --- extract ------------------------------------------------------------------------


def test_extract_takes_a_fenced_block() -> None:
    text = 'Here it is.\n```json\n{"corrections": []}\n```\nDone.'

    assert extract(text) == {"corrections": []}


def test_extract_takes_the_last_of_two_fenced_blocks() -> None:
    text = (
        '```json\n{"corrections": [1]}\n```\n'
        'On reflection:\n```json\n{"corrections": []}\n```'
    )

    assert extract(text) == {"corrections": []}


def test_extract_takes_bare_json() -> None:
    assert extract(' {"corrections": []}\n') == {"corrections": []}


def test_extract_gives_none_for_garbage() -> None:
    assert extract("I think the cut is fine.") is None
    assert extract("```json\n{not json}\n```") is None


# --- validate -----------------------------------------------------------------------


def split(**changes):
    item = {
        "op": "split",
        "block": "b3",
        "at": ["Hence the output is bounded."],
        "class": "argument-boundary",
        "why": "A new step.",
    }
    item.update(changes)
    return item


def test_a_valid_answer_s_corrections_carry_ids(paper) -> None:
    question = boundaries(paper)[1]

    found = validate(question, {"corrections": [split()]}, paper.document)

    assert found.valid, found.problems
    (correction,) = found.corrections
    assert correction.op is CutOp.SPLIT
    assert correction.block == "main.tex:15:1"
    assert correction.at == ("Hence the output is bounded.",)
    assert correction.cls is CorrectionClass.ARGUMENT_BOUNDARY
    assert correction.id == "" and correction.answer is None


def test_an_empty_list_is_a_valid_answer(paper) -> None:
    found = validate(boundaries(paper)[0], {"corrections": []}, paper.document)

    assert found.valid and found.corrections == ()


@pytest.mark.parametrize(
    ("answer", "problem"),
    [
        ([], 'the answer is not an object {"corrections": [...]}'),
        ({"fixes": []}, 'the answer is not an object {"corrections": [...]}'),
        ({"corrections": ["split"]}, "correction 1: not an object"),
        (
            {"corrections": [split(op="cut")]},
            "correction 1: op 'cut' is not an operation",
        ),
        (
            {
                "corrections": [
                    {"op": "ungroup", "group": "g1", "class": "other", "why": "x"}
                ]
            },
            "correction 1: op ungroup is not one of this stage's: "
            "merge, part, rebound, split, unpart",
        ),
        (
            {"corrections": [split(extra=1)]},
            "correction 1 (split): field 'extra' is unknown",
        ),
        (
            {"corrections": [{k: v for k, v in split().items() if k != "why"}]},
            "correction 1 (split): field 'why' is missing",
        ),
        (
            {"corrections": [split(**{"class": "typo"})]},
            "correction 1 (split): class 'typo' is not a class",
        ),
        (
            {"corrections": [split(block="b9")]},
            "correction 1 (split): block 'b9' is not a handle",
        ),
        (
            {"corrections": [split(block="after")]},
            "correction 1 (split): block 'after' is not a handle",
        ),
        (
            {"corrections": [split(at=[])]},
            "correction 1 (split): at must be an excerpt or a list of excerpts",
        ),
        (
            {"corrections": [split(at=["nowhere in the block"])]},
            "correction 1: cut point occurs 0 times in the owned text of b3: "
            "'nowhere in the block'",
        ),
    ],
)
def test_each_check_fails_alone_with_its_problem(paper, answer, problem) -> None:
    found = validate(boundaries(paper)[1], answer, paper.document)

    assert found.problems == (problem,)
    assert found.corrections == ()


def test_enums_are_checked(paper) -> None:
    question = boundaries(paper)[1]
    part = {
        "op": "part",
        "block": "b2",
        "kind": "bullet",
        "marker": "(a)",
        "at": "The output",
        "class": "missed-part",
        "why": "x",
    }

    assert validate(question, {"corrections": [part]}, paper.document).problems == (
        "correction 1 (part): kind 'bullet' is not one of "
        "item, labelled-display, headed",
    )


def test_a_rebound_s_excerpt_may_be_a_string(paper) -> None:
    question = boundaries(paper)[1]
    item = {
        "op": "rebound",
        "blocks": ["b3", "b5"],
        "at": "Hence the output is bounded.",
        "class": "argument-boundary",
        "why": "x",
    }

    found = validate(question, {"corrections": [item]}, paper.document)

    assert found.valid, found.problems
    assert found.corrections[0].at == ("Hence the output is bounded.",)


def test_check_extracts_then_validates(paper) -> None:
    question = boundaries(paper)[1]
    text = "```json\n" + json.dumps({"corrections": [split()]}) + "\n```"

    assert check(question, text, paper.document).valid
    assert check(question, "no", paper.document).problems == (
        "the answer holds no JSON object",
    )


def test_a_reask_holds_the_prompt_the_answer_and_its_problems(paper) -> None:
    question = prompt_only(paper)

    prompt = reask(question, '{"corrections": [}', ["the answer holds no JSON object"])

    assert prompt.startswith(question.prompt)
    assert '{"corrections": [}' in prompt
    assert "- the answer holds no JSON object" in prompt
    assert "Answer again, in full" in prompt


def test_a_long_answer_is_cut_in_its_reask(paper) -> None:
    question = prompt_only(paper)

    prompt = reask(question, "x" * 50_000, ["too long"])

    assert len(prompt) < len(question.prompt) + 21_000
    assert "its first 20,000 characters" in prompt


def test_problems_name_blocks_by_handle_only_where_an_id_stands_whole() -> None:
    from axiomation.questions.cut_review import by_handle

    names = {"main.tex:1:1": "b1", "main.tex:1:12": "b2"}

    assert by_handle("blocks main.tex:1:12 and main.tex:1:1: no", names) == (
        "blocks b2 and b1: no"
    )
    assert by_handle("group proof@main.tex:1:1 exists", names) == (
        "group proof@main.tex:1:1 exists"
    )
    assert by_handle("block other.tex:3:main.tex:1:1", {"main.tex:1:1": "b1"}) == (
        "block other.tex:3:main.tex:1:1"
    )
