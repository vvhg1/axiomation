"""OLD's corrected cut rebased onto NEW on the
cut review's synthetic paper with a first roles pass and one correction, a
heading merged with its paragraph: what the text map carries keeps its
boundaries and roles; changed regions are the cutter's, to re-cut; a group
stands while its members do, and is lost, with why, where its run is cut."""

from __future__ import annotations

import itertools
from pathlib import Path

import pytest

from axiomation.assembler import corrected_cut
from axiomation.cutter import CutRules, DisplayRule, cut
from axiomation.cutter.coverage import coverage
from axiomation.cutter.model import Role, Segment
from axiomation.identity import (
    BlockClass,
    GroupLapse,
    Lapse,
    LostCorrection,
    LostGroup,
    Version,
    pair,
    rebase_cut,
    vocabulary_marks,
)
from axiomation.reader import (
    EnvEntry,
    MapAnswer,
    RefEntry,
    Vocabulary,
    VocabularyKind,
    read_paper,
)
from axiomation.records import CorrectionClass, CutCorrection, CutOp
from questions.review_paper import MAIN, make_paper
from questions.test_roles_file import first_pass

SETTING, PARAGRAPH = "main.tex:3:1", "main.tex:4:1"


@pytest.fixture
def old(tmp_path: Path) -> Version:
    paper = make_paper(tmp_path / "v1")
    merge = CutCorrection(
        "x1",
        CutOp.MERGE,
        CorrectionClass.OTHER,
        "a heading and its text",
        blocks=(SETTING, PARAGRAPH),
    )
    found = corrected_cut(paper.document, first_pass(paper), [merge])
    assert found.problems == ()
    return Version(paper.document, found.cut)


def rebased(old: Version, tmp_path: Path, main: str):
    new = make_paper(tmp_path / "v2", main=main)
    found = rebase_cut(old, new.document, new.cut)
    covered = coverage(new.document, found.cut)
    assert covered.unowned == () and covered.owned_twice == (), covered
    return new, found


def block(cut, words: str, document):
    (found,) = [
        b
        for b in cut.blocks
        if words in " ".join(document.text(run) for run in b.owned)
    ]
    return found


def _sound(found, new) -> None:
    """Every character owned once, and no two top-level extents overlapping."""
    covered = coverage(new.document, found.cut)
    assert covered.unowned == () and covered.owned_twice == (), covered
    tops = [b for b in found.cut.blocks if b.within is None]
    for a, b in itertools.combinations(tops, 2):
        assert a.extent.file != b.extent.file or (
            a.extent.end <= b.extent.start or b.extent.end <= a.extent.start
        ), (a.id, a.extent, b.id, b.extent)


def test_an_insertion_keeps_every_correction_shifted(old, tmp_path) -> None:
    main = MAIN.replace("\\section{Setting}", "An opening line.\n\n\\section{Setting}")
    new, found = rebased(old, tmp_path, main)
    merged = block(found.cut, "We fix a field", new.document)
    was = block(old.cut, "We fix a field", old.document)
    assert merged.id == "main.tex:5:1" and was.id == SETTING
    assert (merged.roles, merged.segments) == (was.roles, was.segments)
    assert merged.hash == was.hash
    assert [new.document.text(r) for r in merged.owned] == [
        old.document.text(r) for r in was.owned
    ]
    assert found.recut == (block(found.cut, "An opening", new.document).id,)
    assert found.touched == () and found.proposed == ()
    assert len(found.cut.groups) == len(old.cut.groups)
    lineage = pair(old, Version(new.document, found.cut))
    assert {link.cls for link in lineage.links} == {BlockClass.SAME, BlockClass.ADDED}


def test_an_edit_inside_a_corrected_merge_keeps_the_merge(old, tmp_path) -> None:
    """The merged block's paragraph edited away from the join: the words
    either side of it stand, so heading and paragraph stay one block,
    listed to re-cut, its roles the roles question's again."""
    main = MAIN.replace("We fix a field of scalars.", "We fix a ring of scalars.")
    new, found = rebased(old, tmp_path, main)
    merged = block(found.cut, "We fix a ring", new.document)
    assert "\\section{Setting}" in new.document.text(merged.extent)
    assert found.recut == (merged.id,) and found.corrections_lost == ()
    assert merged.roles == ()
    lineage = pair(old, Version(new.document, found.cut))
    assert lineage.link_of_old(SETTING).cls is BlockClass.CHANGED


def test_an_edit_at_a_join_lapses_it_and_says_why(old, tmp_path) -> None:
    """The first words after the join changed: its region is the cutter's
    again, heading and paragraph apart, and the lapse is listed."""
    main = MAIN.replace("We fix a field of scalars.", "Fix a field of scalars.")
    new, found = rebased(old, tmp_path, main)
    heading = block(found.cut, "\\section{Setting}", new.document)
    paragraph = block(found.cut, "Fix a field", new.document)
    assert heading.id != paragraph.id
    assert set(found.recut) == {heading.id, paragraph.id}
    assert found.corrections_lost == (
        LostCorrection(SETTING, CutOp.MERGE, "We fix a", Lapse.WORDS),
    )


def test_a_join_whose_earlier_side_changed_names_its_words(old, tmp_path) -> None:
    """The heading before the join rewritten, the words after it as they
    were: the lapse names the heading's words, the ones that changed."""
    main = MAIN.replace("\\section{Setting}", "\\section{The setting}")
    _, found = rebased(old, tmp_path, main)
    assert found.corrections_lost == (
        LostCorrection(SETTING, CutOp.MERGE, "\\section{Setting}", Lapse.WORDS),
    )


def test_a_join_whose_two_sides_changed_names_the_earlier(old, tmp_path) -> None:
    main = MAIN.replace("\\section{Setting}", "\\section{The setting}").replace(
        "We fix a field of scalars.", "Fix a field of scalars."
    )
    _, found = rebased(old, tmp_path, main)
    assert found.corrections_lost == (
        LostCorrection(SETTING, CutOp.MERGE, "\\section{Setting}", Lapse.WORDS),
    )


def test_words_prepended_to_a_join_s_second_side_keep_it(old, tmp_path) -> None:
    """A sentence put at the start of the joined paragraph, in the same
    paragraph: the cutter gives it to the paragraph, so the heading keeps
    its paragraph, the new sentence with it."""
    main = MAIN.replace("We fix a field", "An inserted opening.\nWe fix a field")
    new, found = rebased(old, tmp_path, main)
    merged = block(found.cut, "We fix a field", new.document)
    text = new.document.text(merged.extent)
    assert "\\section{Setting}" in text and "An inserted opening." in text
    assert found.corrections_lost == ()
    _sound(found, new)


def test_a_block_inserted_inside_a_join_lapses_it(old, tmp_path) -> None:
    """A paragraph of the author's between the join's two sides, both
    unchanged: joining over it would swallow it, so the join lapses."""
    main = MAIN.replace("We fix a field", "A paragraph of its own.\n\nWe fix a field")
    new, found = rebased(old, tmp_path, main)
    heading = block(found.cut, "\\section{Setting}", new.document)
    assert "We fix" not in " ".join(new.document.text(r) for r in heading.owned)
    assert found.corrections_lost == (
        LostCorrection(SETTING, CutOp.MERGE, "We fix a", Lapse.BETWEEN),
    )


def test_words_appended_to_a_kept_merge_overlap_nothing(
    version,
) -> None:
    """A sentence appended on the line of a kept merge: the merge keeps its
    one run, which reaches into the cutter's paragraph, and the new
    sentence is what is left of that paragraph, re-cut, its extent
    overlapping the merge's nowhere."""
    text = _latex(OTHER, "\\section{Setting}\nWe fix a field of scalars.", "Closing.")
    old = version(text)
    merge = CutCorrection(
        "m1",
        CutOp.MERGE,
        CorrectionClass.OTHER,
        "a heading and its text",
        blocks=(
            block(old.cut, "\\section{Setting}", old.document).id,
            block(old.cut, "We fix", old.document).id,
        ),
    )
    found = corrected_cut(old.document, old.cut, [merge])
    assert found.problems == ()
    old = Version(old.document, found.cut)
    new = version(text.replace("of scalars.", "of scalars. Appended words here."))

    rebased = rebase_cut(old, new.document, new.cut)

    _sound(rebased, new)


def test_an_edit_to_a_member_s_words_keeps_its_group(old, tmp_path) -> None:
    """The proof's first block `changed` (one word): OLD's group stands on
    its members' heirs, naming the theorem, and the cutter's group over the
    same blocks is no proposal beside it."""
    (proof,) = old.cut.groups
    main = MAIN.replace("the gain is small", "the gain is tall")
    new, found = rebased(old, tmp_path, main)
    lineage = pair(old, Version(new.document, found.cut))
    first = lineage.link_of_old(proof.blocks[0])
    assert first is not None and first.cls is BlockClass.CHANGED
    assert found.groups_lost == () and found.touched == () and found.proposed == ()
    (group,) = found.cut.groups
    assert (group.kind, group.names) == (proof.kind, proof.names)
    assert group.role == proof.role
    assert group.blocks == tuple(
        block(found.cut, words, new.document).id
        for words in ("By Definition", "Small means", "Next, the bound")
    )


def test_an_edit_to_the_named_block_keeps_the_group_naming_its_heir(
    old, tmp_path
) -> None:
    (proof,) = old.cut.groups
    main = MAIN.replace(
        "The output is bounded.\n\\end{theorem}",
        "The output is bounded above.\n\\end{theorem}",
    )
    new, found = rebased(old, tmp_path, main)
    theorem = block(found.cut, "bounded above", new.document)
    assert found.groups_lost == ()
    (group,) = found.cut.groups
    assert group.names == theorem.id and group.named_label == proof.named_label


def test_a_member_deleted_loses_its_group(old, tmp_path) -> None:
    """The last step's words written into the one before: OLD's last
    member is deleted, so the group is lost, and the cutter's group over
    what stands is proposed in its place."""
    (proof,) = old.cut.groups
    main = MAIN.replace("bounded.\n\nNext, the bound", "bounded, and next the bound")
    new, found = rebased(old, tmp_path, main)
    assert found.groups_lost == (LostGroup(proof.id, GroupLapse.DELETED),)
    (group,) = found.cut.groups
    assert found.proposed == (group.id,)


def test_a_member_split_loses_its_group(old, tmp_path) -> None:
    (proof,) = old.cut.groups
    main = MAIN.replace("Next, the bound is sharp.", "Next, the bound\n\nis sharp.")
    new, found = rebased(old, tmp_path, main)
    lineage = pair(old, Version(new.document, found.cut))
    split = lineage.link_of_old(proof.blocks[-1])
    assert split is not None and split.cls is BlockClass.SPLIT
    assert found.groups_lost == (LostGroup(proof.id, GroupLapse.SPLIT),)
    assert GroupLapse.SPLIT.said == "a member was split"


def test_the_named_block_deleted_loses_the_group(old, tmp_path) -> None:
    (proof,) = old.cut.groups
    theorem = (
        "\\begin{theorem}\\label{thm:bound}\nThe output is bounded.\n\\end{theorem}\n"
    )
    assert theorem in MAIN
    new, found = rebased(old, tmp_path, MAIN.replace(theorem, ""))
    assert found.groups_lost == (LostGroup(proof.id, GroupLapse.NAMES),)


#: The proof titled after the theorem it proves, with a second theorem
#: before it.
TITLED = MAIN.replace(
    "\\begin{proof}\n",
    "\\begin{theorem}\\label{thm:other}\nAnother bound.\n\\end{theorem}\n"
    "\\begin{proof}[Proof of Theorem~\\ref{thm:bound}]\n",
)


def test_a_title_that_names_another_result_loses_its_group(tmp_path) -> None:
    """The title now names the other theorem: OLD's group, which named the
    first by its title, is lost, and the cutter's, naming the other, is
    proposed in its place. A word changed in the same block keeps it."""
    paper = make_paper(tmp_path / "v1", main=TITLED)
    old = Version(paper.document, paper.cut)
    (proof,) = old.cut.groups
    assert proof.named_label == "thm:bound"

    _, found = rebased(old, tmp_path, TITLED.replace("gain is small", "gain is tall"))
    assert found.groups_lost == ()
    (group,) = found.cut.groups
    assert group.names == proof.names

    other = TITLED.replace("Theorem~\\ref{thm:bound}]", "Theorem~\\ref{thm:other}]")
    new, found = rebased(old, tmp_path / "other", other)
    assert found.groups_lost == (LostGroup(proof.id, GroupLapse.NAMES),)
    (group,) = found.cut.groups
    assert found.proposed == (group.id,)
    assert group.names == block(found.cut, "Another bound", new.document).id


def test_a_member_moved_before_the_others_loses_its_group(old, tmp_path) -> None:
    """The proof's last step moved to its start: every member stands, by
    the text map or a `moved` link, but no longer in order."""
    (proof,) = old.cut.groups
    step = "Next, the bound is sharp.\n"
    main = MAIN.replace(step, "").replace(
        "\\begin{proof}\n", "\\begin{proof}\n" + step + "\n"
    )
    new, found = rebased(old, tmp_path, main)
    lineage = pair(old, Version(new.document, found.cut))
    moved = lineage.link_of_old(proof.blocks[-1])
    assert moved is not None and moved.cls is BlockClass.MOVED
    assert found.groups_lost == (LostGroup(proof.id, GroupLapse.RUN),)


def test_a_block_moved_whole_keeps_its_roles_and_is_not_re_cut(old, tmp_path) -> None:
    definition = (
        "\\begin{definition}\\label{def:gain}\n"
        "The gain is the ratio of output to input.\n\\end{definition}\n"
    )
    assert definition in MAIN
    main = MAIN.replace(definition, "").replace(
        "Closing words of the results.\n",
        "Closing words of the results.\n\n" + definition,
    )
    new, found = rebased(old, tmp_path, main)
    moved = block(found.cut, "The gain is the ratio", new.document)
    was = block(old.cut, "The gain is the ratio", old.document)
    assert moved.roles == was.roles and moved.roles
    assert moved.id not in found.recut
    lineage = pair(old, Version(new.document, found.cut))
    link = lineage.link_of_old(was.id)
    assert link is not None and link.cls is BlockClass.MOVED


def _no_block_inside_another(found) -> None:
    """No two extents overlap unless one block is nested in the other."""
    for one in found.cut.blocks:
        for other in found.cut.blocks:
            if one is other or other.within == one.id or one.within == other.id:
                continue
            overlap = (
                one.extent.file == other.extent.file
                and one.extent.start < other.extent.end
                and other.extent.start < one.extent.end
            )
            assert not overlap or other.within is not None or one.within is not None, (
                one.id,
                other.id,
            )


def test_a_block_inserted_inside_a_proof_s_run_touches_the_group(old, tmp_path) -> None:
    """Every member maps, but a new block stands between
    two: OLD's group is touched, and the one group is the cutter's, with
    the new block in it; no two groups share an id."""
    (proof,) = old.cut.groups
    main = MAIN.replace(
        "Hence the output is bounded.\n\n",
        "Hence the output is bounded.\n\nAn inserted step.\n\n",
    )
    new, found = rebased(old, tmp_path, main)
    assert found.groups_lost == (LostGroup(proof.id, GroupLapse.RUN),)
    (group,) = found.cut.groups
    step = block(found.cut, "An inserted step", new.document)
    assert step.id in group.blocks


def test_text_inserted_between_a_block_s_runs_re_cuts_it(old, tmp_path) -> None:
    """A paragraph inserted after the hidden remark, so
    between the setting's two runs: the block keeps no place around it."""
    main = MAIN.replace(
        "% a hidden remark about the draft\n",
        "% a hidden remark about the draft\n\nInserted para.\n\n",
    )
    new, found = rebased(old, tmp_path, main)
    _no_block_inside_another(found)
    inserted = block(found.cut, "Inserted para.", new.document)
    assert inserted.id in found.recut
    assert block(found.cut, "We fix a field", new.document).id in found.recut


def test_what_is_left_of_a_cutter_block_never_merges_across_a_kept_one(
    tmp_path,
) -> None:
    """OLD split a paragraph in three; NEW edits the
    first and the last. The middle stands; the two others stay two."""
    three = "Alpha one here.\nBeta two here.\nGamma three here.\n"
    m = MAIN.replace("Closing words of the results.\n", three)
    paper = make_paper(tmp_path / "v1", main=m)
    split = CutCorrection(
        "s1",
        CutOp.SPLIT,
        CorrectionClass.OTHER,
        "three",
        block=paper.id_at("Alpha one"),
        at=("Beta two", "Gamma three"),
    )
    corrected = corrected_cut(paper.document, paper.cut, [split])
    assert corrected.problems == ()
    old = Version(paper.document, corrected.cut)
    edited = m.replace("Alpha one", "Alpha uno").replace("Gamma three", "Gamma tres")
    new, found = rebased(old, tmp_path, edited)
    _no_block_inside_another(found)
    pieces = [
        block(found.cut, words, new.document).id
        for words in ("Alpha uno", "Beta two", "Gamma tres")
    ]
    assert len(set(pieces)) == 3
    assert set(found.recut) == {pieces[0], pieces[2]}


def test_an_edit_on_a_line_of_several_blocks_re_cuts_only_its_own(tmp_path) -> None:
    """A paragraph on one line, split
    by a correction in three; NEW edits the middle one. The text map
    refines the edited line to words, so the first and the last keep their
    place and roles, and only the middle is the cutter's again."""
    three = "Alpha one here. Beta two here. Gamma three here.\n"
    m = MAIN.replace("Closing words of the results.\n", three)
    paper = make_paper(tmp_path / "v1", main=m)
    split = CutCorrection(
        "s1",
        CutOp.SPLIT,
        CorrectionClass.OTHER,
        "three",
        block=paper.id_at("Alpha one"),
        at=("Beta two", "Gamma three"),
    )
    corrected = corrected_cut(paper.document, paper.cut, [split])
    assert corrected.problems == ()
    old = Version(paper.document, corrected.cut)
    new, found = rebased(old, tmp_path, m.replace("Beta two", "Beta deux"))
    _no_block_inside_another(found)
    alpha, beta, gamma = (
        block(found.cut, words, new.document)
        for words in ("Alpha one", "Beta deux", "Gamma three")
    )
    assert found.recut == (beta.id,)
    assert [new.document.text(r) for r in alpha.owned] == ["Alpha one here."]
    assert [new.document.text(r) for r in gamma.owned] == ["Gamma three here."]
    assert new.document.text(alpha.extent) == "Alpha one here."
    assert new.document.text(gamma.extent) == "Gamma three here."
    assert (alpha.roles, gamma.roles) == (
        block(old.cut, "Alpha one", old.document).roles,
        block(old.cut, "Gamma three", old.document).roles,
    )
    lineage = pair(old, Version(new.document, found.cut))
    classes = sorted(link.cls for link in lineage.links if link.cls != BlockClass.SAME)
    assert classes == [BlockClass.CHANGED]


def _split_in_three(tmp_path):
    three = "Alpha one here. Beta two here. Gamma three here.\n"
    m = MAIN.replace("Closing words of the results.\n", three)
    paper = make_paper(tmp_path / "v1", main=m)
    split = CutCorrection(
        "s1",
        CutOp.SPLIT,
        CorrectionClass.OTHER,
        "three",
        block=paper.id_at("Alpha one"),
        at=("Beta two", "Gamma three"),
    )
    corrected = corrected_cut(paper.document, paper.cut, [split])
    assert corrected.problems == ()
    return m, Version(paper.document, corrected.cut)


def test_a_sentence_inserted_after_a_kept_block_stays_out_of_it(tmp_path) -> None:
    """Words inserted right after a kept block, on its
    line. Its extent ends where its words end, and the new words are a
    block of their own, overlapping none."""
    m, old = _split_in_three(tmp_path)
    edited = m.replace("Alpha one here. Beta", "Alpha one here. Inserted words. Beta")
    new, found = rebased(old, tmp_path, edited)
    _no_block_inside_another(found)
    for words in ("Alpha one here.", "Beta two here.", "Gamma three here."):
        kept = block(found.cut, words, new.document)
        assert new.document.text(kept.extent) == words
        assert kept.id not in found.recut
    inserted = block(found.cut, "Inserted words.", new.document)
    assert found.recut == (inserted.id,)


def test_paragraphs_joined_are_re_cut_and_merged(tmp_path) -> None:
    """A paragraph break gone: the run is not refined to words, so the two
    blocks either side are the cutter's again and identity merges them; a
    word edit inside a paragraph keeps its neighbours (the test above)."""
    main = MAIN.replace(
        "Closing words of the results.\n",
        "Alpha one here.\n\nBeta two here.\n\nClosing words of the results.\n",
    )
    before = make_paper(tmp_path / "v0", main=main)
    old = Version(before.document, before.cut)
    joined = main.replace(
        "Alpha one here.\n\nBeta two here.", "Alpha one here. Beta two here."
    )
    new, found = rebased(old, tmp_path, joined)
    merged = block(found.cut, "Alpha one here. Beta two here.", new.document)
    assert found.recut == (merged.id,)
    lineage = pair(old, Version(new.document, found.cut))
    (link,) = [x for x in lineage.links if x.cls is not BlockClass.SAME]
    assert link.cls is BlockClass.MERGED and link.new == (merged.id,)


def test_words_inserted_before_an_extent_s_tail_stay_out_of_it(tmp_path) -> None:
    """An extent reaches past its last run over a
    comment no run owns. Words inserted between the run and the comment
    are a block of their own; the kept block's extent ends at its run."""
    tail = "Alpha one here. Beta two here.\n% a trailing note\n"
    m = MAIN.replace("Closing words of the results.\n", tail)
    paper = make_paper(tmp_path / "v1", main=m)
    old = Version(paper.document, paper.cut)
    was = block(old.cut, "Beta two here.", old.document)
    assert old.document.text(was.extent).endswith("% a trailing note")
    edited = m.replace("Beta two here.\n", "Beta two here. Gamma added.\n")
    new, found = rebased(old, tmp_path, edited)
    _no_block_inside_another(found)
    gamma = block(found.cut, "Gamma added.", new.document)
    assert found.recut == (gamma.id,)
    kept = block(found.cut, "Beta two here.", new.document)
    assert new.document.text(kept.extent) == "Alpha one here. Beta two here."


def test_a_comment_edited_at_an_extent_s_tail_keeps_the_block(tmp_path) -> None:
    """Only the comment the extent holds changed: the block keeps its place
    and roles, its extent ending at its run."""
    tail = "Alpha one here. Beta two here.\n% a trailing note\n"
    m = MAIN.replace("Closing words of the results.\n", tail)
    paper = make_paper(tmp_path / "v1", main=m)
    old = Version(paper.document, paper.cut)
    new, found = rebased(old, tmp_path, m.replace("trailing note", "trailing remark"))
    kept = block(found.cut, "Beta two here.", new.document)
    assert kept.id not in found.recut and found.recut == ()
    assert new.document.text(kept.extent) == "Alpha one here. Beta two here."


def test_a_line_made_a_heading_is_re_cut(version) -> None:
    """`# ` put before a paragraph's first line.
    The words are unchanged, but the line is structure now, so the run is
    not refined to words and the cutter's heading and paragraph stand."""
    text = "# T\n\nA gain bound is a bound on a ratio.\nIt combines results.\n\nEnd.\n"
    old = version(text, "paper.md")
    new = version(text.replace("\nA gain", "\n# A gain"), "paper.md")
    found = rebase_cut(old, new.document, new.cut)
    heading = block(found.cut, "# A gain bound", new.document)
    rest = block(found.cut, "It combines results.", new.document)
    assert heading.id != rest.id
    assert {heading.id, rest.id} <= set(found.recut)


def _with_roles(version: Version, words: str, segments) -> Version:
    """`version` with the block holding `words` given roles in segments."""
    from dataclasses import replace

    from axiomation.cutter.model import Role, Segment, role_set

    target = block(version.cut, words, version.document)
    held = tuple(Segment(s, None if r == "none" else Role(r)) for s, r in segments)
    roles = role_set(s.role for s in held if s.role is not None)
    blocks = tuple(
        replace(b, roles=roles, segments=held) if b.id == target.id else b
        for b in version.cut.blocks
    )
    return Version(version.document, replace(version.cut, blocks=blocks))


CLAIM = "# T\n\nWe prove that *p* holds for all n. Since q holds, it follows.\n\nEnd.\n"


def test_a_markup_edit_keeps_the_roles_and_the_starts_found_once(version) -> None:
    """A block whose maths or markup alone changed keeps
    OLD's roles; its words are the
    same, so it is not re-cut, and pairs as MARKUP."""
    old = _with_roles(
        version(CLAIM, "paper.md"),
        "We prove",
        [("", "claim"), ("Since q holds", "justification")],
    )
    new = version(CLAIM.replace("*p*", "\\emph{p}"), "paper.md")
    found = rebase_cut(old, new.document, new.cut)
    kept = block(found.cut, "We prove", new.document)
    was = block(old.cut, "We prove", old.document)
    assert (kept.roles, kept.segments) == (was.roles, was.segments)
    assert kept.id not in found.recut and found.dropped_starts == ()
    link = pair(old, Version(new.document, found.cut)).link_of_old(was.id)
    assert link is not None and link.cls is BlockClass.MARKUP


def test_a_start_the_edit_changed_is_dropped_and_recorded(version) -> None:
    """A start no longer found once is dropped as a boundary; the block
    keeps its role set, and the start is listed for the `part` flag."""
    from axiomation.identity import DroppedStart

    claim = CLAIM.replace("*p*", "$x$")
    old = _with_roles(
        version(claim, "paper.md"),
        "We prove",
        [("", "claim"), ("$x$ holds for", "justification")],
    )
    new = version(claim.replace("$x$", "$y$"), "paper.md")
    found = rebase_cut(old, new.document, new.cut)
    kept = block(found.cut, "We prove", new.document)
    was = block(old.cut, "We prove", old.document)
    assert kept.roles == was.roles and kept.segments == ()
    assert found.dropped_starts == (
        DroppedStart(kept.id, "$x$ holds for", BlockClass.MATH),
    )


def test_a_markup_edit_in_a_grouped_block_keeps_the_group(old, tmp_path) -> None:
    """The block keeps its place in OLD's group, which
    stands; nothing is touched, re-cut or proposed."""
    (proof,) = old.cut.groups
    main = MAIN.replace("the gain is small", "the gain is \\emph{small}")
    new, found = rebased(old, tmp_path, main)
    edited = block(found.cut, "By Definition", new.document)
    was = block(old.cut, "By Definition", old.document)
    assert (edited.roles, edited.segments) == (was.roles, was.segments)
    assert found.touched == () and found.proposed == () and found.recut == ()
    (group,) = found.cut.groups
    assert (group.id, group.kind) == (proof.id, proof.kind)
    assert edited.id in group.blocks


def test_a_markup_edit_in_a_block_a_correction_split_off_keeps_its_roles(
    tmp_path,
) -> None:
    """OLD's block is a split correction's, so the
    cutter's block of NEW spans its neighbours too; the lineage carries the
    roles to what is left of it."""
    m, old = _split_in_three(tmp_path)
    old = _with_roles(old, "Beta two", [("", "claim"), ("two here", "justification")])
    new, found = rebased(old, tmp_path, m.replace("Beta two", "Beta \\emph{two}"))
    beta = block(found.cut, "Beta", new.document)
    was = block(old.cut, "Beta two", old.document)
    assert beta.roles == was.roles and beta.id not in found.recut
    (lost,) = found.dropped_starts
    assert (lost.block, lost.starts, lost.cls) == (
        beta.id,
        "two here",
        BlockClass.MARKUP,
    )


LATEX = (
    "\\documentclass{article}\n\\begin{document}\n\n"
    "We prove the main bound here. Since the gain is small, it follows.\n\n"
    "End.\n\n\\end{document}\n"
)


def test_a_start_is_placed_as_the_assembler_places_it(version) -> None:
    """A comment now between a start's words: the owned text still reads
    them, but the assembler cannot place the start, so it is dropped and
    listed, and the payload names it."""
    from axiomation.exchange import diff_payload

    old = _with_roles(
        version(LATEX),
        "We prove",
        [("", "claim"), ("Since the gain", "justification")],
    )
    edited = LATEX.replace("We prove", "We \\emph{prove}").replace(
        "Since the gain", "Since % a note\nthe gain"
    )
    new = version(edited)
    found = rebase_cut(old, new.document, new.cut)
    kept = block(found.cut, "We \\emph{prove}", new.document)
    assert kept.roles == block(old.cut, "We prove", old.document).roles
    assert kept.segments == ()
    lineage = pair(old, Version(new.document, found.cut))
    payload = diff_payload(old.document, old.cut, new.document, found, lineage)
    assert payload["dropped_starts"] == [
        {"block": kept.id, "start": "Since the gain", "cls": "markup"}
    ]


def test_a_start_found_twice_is_dropped_and_one_role_keeps_its_segment(
    version,
) -> None:
    """A comment removed inside a block the text map keeps makes a start
    occur twice; a single-role block whose first span plays none keeps its
    role as one segment."""
    text = LATEX.replace(
        "We prove the main bound here.", "We note the % x\ngain is here."
    )
    old = _with_roles(
        version(text), "We note", [("", "none"), ("the gain is", "claim")]
    )
    new = version(text.replace("% x\n", ""))
    found = rebase_cut(old, new.document, new.cut)
    kept = block(found.cut, "We note", new.document)
    assert [(d.starts, d.cls) for d in found.dropped_starts] == [
        ("the gain is", BlockClass.SAME)
    ]
    assert kept.segments == (Segment("", Role.CLAIM),)


def test_a_block_moved_and_marked_up_is_new_by_the_lineage(version) -> None:
    """Moved elsewhere with a markup edit, identity says deleted and added:
    the roles are not carried and the block is re-cut."""
    text = "# T\n\nAlpha one.\n\n## S\n\nWe prove *p* holds. Since q, done.\n\nEnd.\n"
    old = _with_roles(version(text, "paper.md"), "We prove", [("", "claim")])
    moved = text.replace("\n\nWe prove *p* holds. Since q, done.", "").replace(
        "Alpha one.", "We prove \\emph{p} holds. Since q, done.\n\nAlpha one."
    )
    new = version(moved, "paper.md")
    found = rebase_cut(old, new.document, new.cut)
    lineage = pair(old, Version(new.document, found.cut))
    was = block(old.cut, "We prove", old.document)
    link = lineage.link_of_old(was.id)
    assert link is not None and link.cls is BlockClass.DELETED
    now = block(found.cut, "We prove", new.document)
    assert now.roles == () and now.id in found.recut


PARTS = (
    "# T\n\nDefine the map with rule\n\\begin{equation}\n"
    "g(x)=x+c,\\label{eq:m}\n\\end{equation}\nThis map for shifting holds."
    "\n\nThe steps are:\n\n1. First we *fit* it.\n2. Then we test it.\n\n"
    "A long closing paragraph of the paper, with words enough to stand alone.\n"
)


def _in_paragraph(tmp_path: Path, name: str, text: str) -> Version:
    from axiomation.cutter import CutRules, DisplayRule, cut
    from axiomation.reader import read_paper

    root = tmp_path / name
    root.mkdir()
    (root / "paper.md").write_text(text, encoding="utf-8")
    document = read_paper(root)
    return Version(document, cut(document, CutRules(DisplayRule.IN_PARAGRAPH)))


def test_a_maths_edit_in_a_labelled_display_keeps_its_roles_and_part(
    tmp_path,
) -> None:
    """A maths edit in a labelled display: the display's part
    cannot be mapped through the edit, so it is placed on the cutter's
    labelled display, keeping its label."""
    old = _with_roles(
        _in_paragraph(tmp_path, "v1", PARTS),
        "Define the map",
        [("", "definition"), ("This map for", "claim")],
    )
    new = _in_paragraph(tmp_path, "v2", PARTS.replace("x+c", "x+2c"))
    found = rebase_cut(old, new.document, new.cut)
    kept = block(found.cut, "Define the map", new.document)
    was = block(old.cut, "Define the map", old.document)
    assert (kept.roles, kept.segments) == (was.roles, was.segments)
    assert kept.id not in found.recut
    (part,) = [p for p in found.cut.parts if p.block == kept.id]
    assert part.label == "eq:m" and "x+2c" in new.document.text(part.at)


def test_a_markup_edit_in_an_item_and_an_item_moved_keep_their_roles(
    version,
) -> None:
    old = version(PARTS, "paper.md")
    old = _with_roles(old, "First we", [("", "claim")])
    old = _with_roles(old, "Then we", [("", "claim")])
    marked = PARTS.replace("*fit*", "_fit_")
    moved = marked.replace("\n2. Then we test it.", "").replace(
        "stand alone.\n", "stand alone.\n\n2. Then we test it.\n"
    )
    new = version(moved, "paper.md")
    found = rebase_cut(old, new.document, new.cut)
    for words, marker in (("First we", "1."), ("Then we", "2.")):
        kept = block(found.cut, words, new.document)
        assert kept.roles == block(old.cut, words, old.document).roles
        assert kept.id not in found.recut
        (part,) = [p for p in found.cut.parts if p.block == kept.id]
        assert part.marker == marker


def test_an_inline_maths_run_added_is_a_maths_edit_that_keeps_roles(version) -> None:
    """Only displays must agree in number; an inline run added is a plain
    maths edit."""
    old = _with_roles(version(CLAIM, "paper.md"), "We prove", [("", "claim")])
    new = version(CLAIM.replace("for all n", "for all n $\\geq 1$"), "paper.md")
    found = rebase_cut(old, new.document, new.cut)
    kept = block(found.cut, "We prove", new.document)
    assert kept.roles == block(old.cut, "We prove", old.document).roles
    assert kept.id not in found.recut


ALIGN = (
    "# T\n\nWe have\n\\begin{align}\na &= b + 1 \\label{x}\\\\\n"
    "c &= d + 2 \\label{y}\n\\end{align}\nwhich ends it.\n\n"
    "A long closing paragraph of the paper, with words enough to stand alone.\n"
)
ROW_X, ROW_Y = "a &= b + 1 \\label{x}", "c &= d + 2 \\label{y}"


@pytest.mark.parametrize(
    "edit",
    [
        lambda t: t.replace(ROW_X, "@").replace(ROW_Y, ROW_X).replace("@", ROW_Y),
        lambda t: (
            t.replace(ROW_X, "@")
            .replace(ROW_Y, ROW_X)
            .replace("@", "c &= d + 3 \\label{y}")
        ),
    ],
    ids=["rows-swapped", "row-edited-and-moved"],
)
def test_each_label_stays_on_its_own_row(tmp_path, edit) -> None:
    """Rows of an align reordered are a maths
    edit; each carried part is the cutter's, so its label is the row's."""
    old = _with_roles(_in_paragraph(tmp_path, "v1", ALIGN), "We have", [("", "claim")])
    new = _in_paragraph(tmp_path, "v2", edit(ALIGN))
    found = rebase_cut(old, new.document, new.cut)
    kept = block(found.cut, "We have", new.document)
    assert kept.roles == block(old.cut, "We have", old.document).roles
    held = [p for p in found.cut.parts if p.block == kept.id]
    assert len({p.at for p in held}) == len(held) == 2
    for part in held:
        assert f"\\label{{{part.label}}}" in new.document.text(part.at)


def test_a_part_an_edit_makes_leaves_the_block_to_the_cutter(version) -> None:
    """Bold made a heading of a block's first words: the cutter finds a
    part OLD did not hold, so nothing is carried and the block is re-cut."""
    text = LATEX.replace("We prove", "Step one. We prove")
    old = _with_roles(version(text), "Step one", [("", "claim")])
    new = version(text.replace("Step one.", "\\textbf{Step one.}"))
    found = rebase_cut(old, new.document, new.cut)
    now = block(found.cut, "We prove", new.document)
    if not [p for p in new.cut.parts if p.block == now.id]:
        pytest.skip("the cutter marks no headed part here")
    assert now.roles == () and now.id in found.recut


DISPLAY = (
    "\\documentclass{article}\n\\begin{document}\n\n"
    "We claim that\n\\[\na = b + 1\n\\]\nholds for every case we meet.\n\n"
    "End.\n\n\\end{document}\n"
)


def _latex_in_paragraph(tmp_path: Path, name: str, text: str) -> Version:
    from axiomation.cutter import CutRules, DisplayRule, cut
    from axiomation.reader import read_paper

    root = tmp_path / name
    root.mkdir()
    (root / "paper.tex").write_text(text, encoding="utf-8")
    document = read_paper(root)
    return Version(document, cut(document, CutRules(DisplayRule.IN_PARAGRAPH)))


@pytest.mark.parametrize(
    ("edit", "carried"),
    [
        (lambda t: t.replace("\\[\na = b + 1\n\\]\n", ""), False),
        (lambda t: t.replace("\\[", "$$").replace("\\]", "$$"), True),
        (
            lambda t: t.replace(
                "\\[\na = b + 1\n\\]",
                "$$\\begin{aligned}\na &= b + 1\n\\end{aligned}$$",
            ),
            True,
        ),
    ],
    ids=["display-deleted", "delimiters-swapped", "nested-display"],
)
def test_a_block_carries_only_with_as_many_displays(tmp_path, edit, carried) -> None:
    """A display deleted is a maths edit by the lineage, but the block now
    holds fewer displays: re-cut. Delimiters swapped or a display nested in
    another: one display on each side, carried."""
    old = _with_roles(
        _latex_in_paragraph(tmp_path, "v1", DISPLAY), "We claim", [("", "claim")]
    )
    new = _latex_in_paragraph(tmp_path, "v2", edit(DISPLAY))
    found = rebase_cut(old, new.document, new.cut)
    now = block(found.cut, "We claim", new.document)
    assert (now.roles != ()) is carried
    assert (now.id in found.recut) is not carried


def test_a_list_restyled_keeps_its_items_roles(version) -> None:
    """`1.` made `1)`: an item's marker is how it is written, not which
    part it is."""
    old = _with_roles(version(PARTS, "paper.md"), "First we", [("", "claim")])
    new = version(PARTS.replace("1. First", "1) First"), "paper.md")
    found = rebase_cut(old, new.document, new.cut)
    kept = block(found.cut, "First we", new.document)
    if kept.id in found.recut and not [p for p in new.cut.parts if p.block == kept.id]:
        pytest.skip("the cutter reads `1)` as no item here")
    assert kept.roles == block(old.cut, "First we", old.document).roles
    assert kept.id not in found.recut


#: A paragraph of three sentences, as `test_identity`'s re-wrap case: the
#: same words broken at 60 columns, then at 45.
WRAPPED = (
    "Our first result bounds the deviation of the empirical mean.\n"
    "Its proof uses a martingale argument. The constant in the\n"
    "bound is not sharp.",
    "Our first result bounds the deviation of the\n"
    "empirical mean. Its proof uses a martingale\n"
    "argument. The constant in the bound is not\n"
    "sharp.",
)


def test_a_corrected_paragraph_re_wrapped_keeps_its_pieces_and_roles(
    version,
) -> None:
    """A paragraph
    a correction cut in three, only re-wrapped, keeps the three blocks and
    their roles; nothing is re-cut, and each piece pairs SAME."""

    def paper(paragraph: str) -> str:
        return (
            "\\documentclass{article}\n\\begin{document}\n\n"
            f"{paragraph}\n\nAn untouched closing paragraph.\n\n\\end{{document}}\n"
        )

    old = version(paper(WRAPPED[0]))
    split = CutCorrection(
        "s1",
        CutOp.SPLIT,
        CorrectionClass.OTHER,
        "a claim, its proof, a remark",
        block=block(old.cut, "Our first result", old.document).id,
        at=("Its proof uses", "The constant in"),
    )
    found = corrected_cut(old.document, old.cut, [split])
    assert found.problems == ()
    old = Version(old.document, found.cut)
    for words, role in (
        ("Our first result", "claim"),
        ("Its proof uses", "justification"),
        ("The constant in", "claim"),
    ):
        old = _with_roles(old, words, [("", role)])
    new = version(paper(WRAPPED[1]))

    rebased = rebase_cut(old, new.document, new.cut)

    assert rebased.recut == ()
    for words, role in (
        ("Our first result", Role.CLAIM),
        ("Its proof uses", Role.JUSTIFICATION),
        ("The constant in", Role.CLAIM),
    ):
        piece = block(rebased.cut, words, new.document)
        assert piece.roles == (role,), words
    lineage = pair(old, Version(new.document, rebased.cut))
    assert {link.cls for link in lineage.links} == {BlockClass.SAME}


# --- a corrected boundary carried by exact words ----

FIRST = "Our first result bounds the deviation of the empirical mean."
PROOF_OF_IT = "Its proof uses a martingale argument."
REMARK = "The constant in the bound is not sharp."
OTHER = "An untouched paragraph that stands between the sections."


def _split_three(version, text: str, name: str = "paper.tex", extra=None):
    """`text` read, cut, and its three-sentence paragraph split by a
    correction into a claim, its proof and a remark, each with its role."""
    old = version(text, name, extra)
    split = CutCorrection(
        "s1",
        CutOp.SPLIT,
        CorrectionClass.OTHER,
        "a claim, its proof, a remark",
        block=block(old.cut, FIRST[:30], old.document).id,
        at=(PROOF_OF_IT[:14], REMARK[:15]),
    )
    found = corrected_cut(old.document, old.cut, [split])
    assert found.problems == ()
    old = Version(old.document, found.cut)
    for words, role in (
        (FIRST, "claim"),
        (PROOF_OF_IT, "justification"),
        (REMARK, "claim"),
    ):
        old = _with_roles(old, words[:30], [("", role)])
    return old


def _latex(*paragraphs: str) -> str:
    return (
        "\\documentclass{article}\n\\begin{document}\n\n"
        + "\n\n".join(paragraphs)
        + "\n\n\\end{document}\n"
    )


THREE_SENTENCES = f"{FIRST} {PROOF_OF_IT} {REMARK}"


def _three_kept(rebased, new, allowed: set[BlockClass], old) -> None:
    for words, role in (
        (FIRST, Role.CLAIM),
        (PROOF_OF_IT, Role.JUSTIFICATION),
        (REMARK, Role.CLAIM),
    ):
        piece = block(rebased.cut, words, new.document)
        assert piece.roles == (role,), words
        assert piece.id not in rebased.recut, words
    lineage = pair(old, Version(new.document, rebased.cut))
    classes = {
        x.cls
        for x in lineage.links
        for words in (FIRST, PROOF_OF_IT, REMARK)
        if block(rebased.cut, words, new.document).id in x.new
    }
    assert classes <= allowed and len(classes) == 1


def test_a_corrected_paragraph_moved_keeps_its_pieces_and_roles(version) -> None:
    """A section moved within its file. The text map does
    not follow moved lines, but the words are exactly OLD's three blocks':
    the correction is carried, the pieces kept (SAME or MOVED, as the
    order's longest run decides which section moved), nothing re-cut."""
    longer = [f"{OTHER} It is paragraph {n} of the longer section." for n in range(4)]
    old = _split_three(
        version, _latex("\\section{A}", THREE_SENTENCES, "\\section{B}", *longer)
    )
    new = version(_latex("\\section{B}", *longer, "\\section{A}", THREE_SENTENCES))

    rebased = rebase_cut(old, new.document, new.cut)

    _three_kept(rebased, new, {BlockClass.SAME, BlockClass.MOVED}, old)


def test_a_corrected_paragraph_moved_into_another_file_keeps_its_pieces(
    version,
) -> None:
    """The paragraph's file inlined into the root. The
    text map is per file; the words carry the correction across."""
    part = f"{THREE_SENTENCES}\n"
    old = _split_three(
        version, _latex(OTHER, "\\input{part}"), extra={"part.tex": part}
    )
    new = version(_latex(OTHER, THREE_SENTENCES))

    rebased = rebase_cut(old, new.document, new.cut)

    for words in (FIRST, PROOF_OF_IT, REMARK):
        assert block(rebased.cut, words, new.document).id not in rebased.recut


def test_a_maths_edit_in_a_corrected_paragraph_keeps_its_pieces(version) -> None:
    """Only the maths changed, so the words are OLD's: the
    pieces stand, MATH where the maths changed."""
    with_maths = THREE_SENTENCES.replace("empirical mean.", "empirical mean $\\bar x$.")
    old = _split_three(version, _latex(with_maths, OTHER))
    new = version(_latex(with_maths.replace("\\bar x", "\\hat\\mu"), OTHER))

    rebased = rebase_cut(old, new.document, new.cut)

    first = block(rebased.cut, FIRST[:30], new.document)
    assert first.id not in rebased.recut and first.roles == (Role.CLAIM,)
    for words in (PROOF_OF_IT, REMARK):
        assert block(rebased.cut, words, new.document).id not in rebased.recut


def test_a_word_changed_in_a_corrected_piece_goes_to_the_boundary_question(
    version,
) -> None:
    """A word edit in one piece: the piece whose words
    changed is the cutter's again and re-cut; the pieces whose words stand
    on the same line are kept by the text map's words."""
    old = _split_three(version, _latex(THREE_SENTENCES, OTHER))
    new = version(_latex(THREE_SENTENCES.replace("martingale", "coupling"), OTHER))

    rebased = rebase_cut(old, new.document, new.cut)

    assert block(rebased.cut, "Its proof uses", new.document).id in rebased.recut
    for words in (FIRST, REMARK):
        assert block(rebased.cut, words, new.document).id not in rebased.recut


def test_an_author_s_join_of_two_layout_paragraphs_is_not_parted_again(
    version,
) -> None:
    """Only a correction's boundary is carried: two paragraphs the cutter
    itself parted, joined by the author, stay one block, re-cut."""
    old = version(_latex(FIRST, PROOF_OF_IT, OTHER))
    new = version(_latex(f"{FIRST} {PROOF_OF_IT}", OTHER))

    rebased = rebase_cut(old, new.document, new.cut)

    joined = block(rebased.cut, FIRST, new.document)
    assert PROOF_OF_IT in " ".join(new.document.text(r) for r in joined.owned)
    assert joined.id in rebased.recut


def test_a_maths_edit_in_a_corrected_merge_keeps_the_merge(version) -> None:
    """A correction joined a paragraph and the display
    paragraph after it; a maths edit in the second leaves the merged block
    unkept by the text map and the cutter's two blocks in its place. Their
    joined words are the merged block's: it is one block again, its role
    kept, MATH."""
    lead = "The bound reads as follows for every sample size."
    shown = "It holds with $c = 2$ for every bounded family."
    old = version(_latex(OTHER, lead, shown))
    merge = CutCorrection(
        "m1",
        CutOp.MERGE,
        CorrectionClass.OTHER,
        "one claim and its constant",
        blocks=(
            block(old.cut, lead, old.document).id,
            block(old.cut, shown, old.document).id,
        ),
    )
    found = corrected_cut(old.document, old.cut, [merge])
    assert found.problems == ()
    old = _with_roles(Version(old.document, found.cut), lead, [("", "claim")])
    new = version(_latex(OTHER, lead, shown.replace("c = 2", "c = 3")))

    rebased = rebase_cut(old, new.document, new.cut)

    merged = block(rebased.cut, lead, new.document)
    assert "c = 3" in " ".join(new.document.text(r) for r in merged.owned)
    assert merged.id not in rebased.recut and merged.roles == (Role.CLAIM,)
    lineage = pair(old, Version(new.document, rebased.cut))
    (link,) = [x for x in lineage.links if merged.id in x.new]
    assert link.cls is BlockClass.MATH


def test_a_footnote_added_to_a_split_paragraph_after_an_edited_one(version) -> None:
    """A correction split a paragraph; its last piece gains a footnote and
    the paragraph before it is edited. The kept pieces part the cutter's
    paragraph, so only its new footnote, nested, stands among the cutter's
    blocks no kept block touches, right after the edited paragraph: it is
    re-cut, the kept pieces keep their roles, and the edited paragraph is
    re-cut."""
    old = _split_three(version, _latex(OTHER, THREE_SENTENCES))
    edited = OTHER.replace("untouched", "edited")
    footed = THREE_SENTENCES.replace(REMARK, REMARK + "\\footnote{Only for n > 1.}")
    new = version(_latex(edited, footed))

    rebased = rebase_cut(old, new.document, new.cut)

    for words, role in ((FIRST, Role.CLAIM), (PROOF_OF_IT, Role.JUSTIFICATION)):
        piece = block(rebased.cut, words, new.document)
        assert piece.roles == (role,) and piece.id not in rebased.recut, words
    footnote = block(rebased.cut, "Only for n", new.document)
    assert footnote.within is not None and footnote.id in rebased.recut
    assert block(rebased.cut, "An edited paragraph", new.document).id in rebased.recut


# --- a correction carried by its words ----------------------------------------------


def test_two_corrected_pieces_both_edited_keep_their_boundary(version) -> None:
    """A correction split a paragraph in three; the second and third pieces
    are both edited, the third gaining a footnote, the words where the
    third starts unchanged. The cutter's stretch over the two is parted
    there again: each continues its OLD block, changed, both re-cut, the
    footnote nested in the third."""
    old = _split_three(version, _latex(OTHER, THREE_SENTENCES))
    proof = PROOF_OF_IT.replace("martingale", "coupling")
    remark = REMARK.replace("not sharp.", "far from sharp.\\footnote{For n > 1.}")
    new = version(_latex(OTHER, f"{FIRST} {proof} {remark}"))

    rebased = rebase_cut(old, new.document, new.cut)

    first = block(rebased.cut, FIRST, new.document)
    assert first.roles == (Role.CLAIM,) and first.id not in rebased.recut
    middle = block(rebased.cut, "Its proof uses a coupling", new.document)
    last = block(rebased.cut, "The constant in the bound", new.document)
    assert middle.id != last.id
    assert {middle.id, last.id} <= set(rebased.recut)
    footnote = block(rebased.cut, "For n > 1", new.document)
    assert footnote.within == last.id
    _sound(rebased, new)
    assert rebased.corrections_lost == ()
    lineage = pair(old, Version(new.document, rebased.cut))
    for words, now in ((PROOF_OF_IT, middle), (REMARK, last)):
        link = lineage.link_of_old(block(old.cut, words, old.document).id)
        assert link is not None and link.new == (now.id,), words
        assert link.cls is BlockClass.CHANGED, words


def test_a_split_whose_words_changed_lapses_and_says_why(version) -> None:
    """Both pieces edited and the words the third started at rewritten: the
    stretch stays the cutter's one block, and the lapse is listed."""
    old = _split_three(version, _latex(OTHER, THREE_SENTENCES))
    proof = PROOF_OF_IT.replace("martingale", "coupling")
    remark = REMARK.replace("The constant in", "Its constant in")
    new = version(_latex(OTHER, f"{FIRST} {proof} {remark}"))

    rebased = rebase_cut(old, new.document, new.cut)

    joined = block(rebased.cut, "Its proof uses a coupling", new.document)
    assert "Its constant" in " ".join(new.document.text(r) for r in joined.owned)
    _sound(rebased, new)
    remark_id = block(old.cut, REMARK, old.document).id
    assert rebased.corrections_lost == (
        LostCorrection(remark_id, CutOp.SPLIT, "The constant in", Lapse.WORDS),
    )


def test_a_join_carries_past_a_line_inserted_before_it(version) -> None:
    """A correction kept a note's lead with the list item after it; a
    layout line is inserted before the note and the item edited: the
    words either side of the join stand, so the note keeps its item."""
    item = "* *Exact cases:* the weight is the one the domain fixes."
    text = f"Before the note.\n\n*Note.*\n\n{item}\n\n* *Other cases:* loose.\n"
    old = version(text, "paper.md")
    merge = CutCorrection(
        "m1",
        CutOp.MERGE,
        CorrectionClass.OTHER,
        "a note and its first case",
        blocks=(
            block(old.cut, "*Note.*", old.document).id,
            block(old.cut, "Exact cases", old.document).id,
        ),
    )
    found = corrected_cut(old.document, old.cut, [merge])
    assert found.problems == ()
    old = Version(old.document, found.cut)
    edited = text.replace("Before the note.\n", "Before the note.\n\n\\FloatBarrier\n")
    new = version(edited.replace("domain fixes", "domain fixes exactly"), "paper.md")

    rebased = rebase_cut(old, new.document, new.cut)

    note = block(rebased.cut, "*Note.*", new.document)
    assert "Exact cases" in " ".join(new.document.text(r) for r in note.owned)
    _sound(rebased, new)
    assert note.id in rebased.recut and rebased.corrections_lost == ()


def test_a_paragraph_break_at_a_split_keeps_the_other_split(version) -> None:
    """The author parts the paragraph where the correction's first split
    was, and edits the second and third pieces: the run's shape changed, so
    no word of it maps, and the third piece's first words, found once,
    place its split. Each piece continues its OLD block, none lost."""
    old = _split_three(version, _latex(OTHER, THREE_SENTENCES))
    proof = PROOF_OF_IT.replace("martingale", "coupling")
    remark = REMARK.replace("not sharp.", "far from sharp.")
    new = version(_latex(OTHER, FIRST, f"{proof} {remark}"))

    rebased = rebase_cut(old, new.document, new.cut)

    middle = block(rebased.cut, "Its proof uses a coupling", new.document)
    last = block(rebased.cut, "The constant in the bound", new.document)
    assert middle.id != last.id and rebased.corrections_lost == ()
    lineage = pair(old, Version(new.document, rebased.cut))
    for words, now in ((PROOF_OF_IT, middle), (REMARK, last)):
        link = lineage.link_of_old(block(old.cut, words, old.document).id)
        assert link is not None and link.new == (now.id,), words
    _sound(rebased, new)


def test_a_paragraph_deleted_with_its_corrections_lists_no_lapse(version) -> None:
    """The corrected paragraph deleted whole: its blocks end, and their
    corrections with them, which is no lapse to list."""
    old = _split_three(version, _latex(OTHER, THREE_SENTENCES, "A closing paragraph."))
    new = version(_latex(OTHER, "A closing paragraph."))

    rebased = rebase_cut(old, new.document, new.cut)

    assert rebased.corrections_lost == ()


def test_a_kept_footnote_does_not_part_its_changed_paragraph(version) -> None:
    """A correction split a paragraph after its first sentence; the rest
    holds a footnote, kept while the words either side of it change. What
    is left of the cutter's paragraph is one block, the footnote nested in
    it, not two blocks either side of it."""
    noted = PROOF_OF_IT.replace("argument.", "argument.\\footnote{An aside.}")
    text = _latex(OTHER, f"{FIRST} {noted} {REMARK}")
    old = version(text)
    split = CutCorrection(
        "s1",
        CutOp.SPLIT,
        CorrectionClass.OTHER,
        "the claim apart",
        block=block(old.cut, FIRST[:30], old.document).id,
        at=(PROOF_OF_IT[:14],),
    )
    found = corrected_cut(old.document, old.cut, [split])
    assert found.problems == ()
    old = Version(old.document, found.cut)
    new = version(
        text.replace("martingale", "coupling").replace("not sharp", "far from sharp")
    )

    rebased = rebase_cut(old, new.document, new.cut)

    rest = block(rebased.cut, "Its proof uses a coupling", new.document)
    assert "far from sharp" in " ".join(new.document.text(r) for r in rest.owned)
    assert block(rebased.cut, "An aside", new.document).within == rest.id
    _sound(rebased, new)


# --- a changed vocabulary map ------------------------------------------------------

BOXED = (
    "\\documentclass{article}\n\\begin{document}\n"
    "First we set the scene.\n\n"
    "\\begin{lawbox}\nThe first law holds.\n\nThe second law holds.\n\\end{lawbox}\n\n"
    "By \\lemref{key} we are done.\n\n"
    "\\begin{lemma}\\label{lem:key}\nThe key step.\n\\end{lemma}\n"
    "\\end{document}\n"
)


def _mapped(tmp_path: Path, name: str, vocabulary: Vocabulary) -> Version:
    root = tmp_path / name
    root.mkdir()
    (root / "main.tex").write_text(BOXED, encoding="utf-8")
    document = read_paper(root, vocabulary=vocabulary)
    return Version(document, cut(document, CutRules(DisplayRule.OWN_BLOCK)))


def _words(version: Version, block) -> str:
    return " ".join(version.document.text(run) for run in block.owned)


@pytest.mark.parametrize(
    ("before", "after"),
    [
        (VocabularyKind.NONE, VocabularyKind.CONTAINER),
        (VocabularyKind.CONTAINER, VocabularyKind.NONE),
    ],
)
def test_a_changed_map_entry_re_cuts_what_reads_it_and_keeps_the_rest(
    tmp_path, before, after
) -> None:
    old = _mapped(tmp_path, "v1", Vocabulary((EnvEntry("lawbox", before),)))
    new = _mapped(tmp_path, "v2", Vocabulary((EnvEntry("lawbox", after),)))

    found = rebase_cut(old, new.document, new.cut)

    # the box is cut as the new map reads it, whatever the kept cut held
    assert [b.id for b in found.cut.blocks] == [b.id for b in new.cut.blocks]
    boxed = [b for b in found.cut.blocks if "law holds" in _words(new, b)]
    assert len(boxed) == (2 if after is VocabularyKind.CONTAINER else 1)
    assert {b.id for b in boxed} <= set(found.recut)
    # every other block is kept: not re-cut
    assert not any(
        "scene" in _words(new, b) for b in found.cut.blocks if b.id in found.recut
    )
    lineage = pair(old, Version(new.document, found.cut))
    classes = {
        link.cls
        for link in lineage.links
        if any(
            "law holds" in _words(new, b) for b in found.cut.blocks if b.id in link.new
        )
    }
    assert BlockClass.SAME not in classes


def test_a_changed_reference_entry_marks_the_blocks_that_use_it(tmp_path) -> None:
    old = _mapped(tmp_path, "v1", Vocabulary())
    new = _mapped(
        tmp_path, "v2", Vocabulary(references=(RefEntry("lemref", prefix="lem:"),))
    )

    marks = vocabulary_marks(old.document, new.document)
    lineage = pair(old, new)

    assert marks.names == ("command lemref",)
    changed = [
        _words(new, b)
        for link in lineage.links
        if link.cls is BlockClass.CHANGED
        for b in new.cut.blocks
        if b.id in link.new
    ]
    assert changed == ["By \\lemref{key} we are done."]
    # the new map's reference resolves: its candidate is found again
    assert [c.target for c in new.cut.candidates if c.command == "lemref"] == [
        "lem:key"
    ]


def test_a_redefinition_s_entry_changed_marks_the_name_s_uses(tmp_path) -> None:
    """`lemref` defined, then redefined: an answer changed for the second
    declaration alone marks the name, as one for its only declaration
    would."""
    first = "\\newcommand{\\lemref}[1]{Lemma~\\ref{lem:#1} on page~\\pageref{lem:#1}}"
    again = "\\renewcommand{\\lemref}[1]{#1}"
    text = (
        f"\\documentclass{{article}}\n{first}\n\\begin{{document}}\n"
        "\\begin{lemma}\\label{lem:key}\nA gap shrinks.\n\\end{lemma}\n\n"
        f"By \\lemref{{key}} it does.\n\n{again}\n\nAgain \\lemref{{key}}.\n"
        "\\end{document}\n"
    )

    def version(name: str, second: str) -> Version:
        root = tmp_path / name
        root.mkdir()
        (root / "main.tex").write_text(text, encoding="utf-8")
        mapped = Vocabulary.of(
            [
                MapAnswer("command", "lemref", first, "reference prefix=lem:", "Lemma"),
                MapAnswer("command", "lemref", again, second, "#1"),
            ]
        )
        document = read_paper(root, vocabulary=mapped)
        return Version(document, cut(document, CutRules(DisplayRule.OWN_BLOCK)))

    old, new = version("v1", "none"), version("v2", "reference prefix=lem:")

    marks = vocabulary_marks(old.document, new.document)

    assert marks.names == ("command lemref",)
    assert [r.targets for r in old.document.references if r.command == "lemref"] == [
        ("lem:key",)
    ]
    assert [r.targets for r in new.document.references if r.command == "lemref"] == [
        ("lem:key",),
        ("lem:key",),
    ]


def test_the_same_map_marks_nothing(tmp_path) -> None:
    same = Vocabulary((EnvEntry("lawbox", VocabularyKind.CONTAINER),))
    old, new = _mapped(tmp_path, "v1", same), _mapped(tmp_path, "v2", same)

    assert not vocabulary_marks(old.document, new.document)
    assert rebase_cut(old, new.document, new.cut).recut == ()


def test_an_entry_for_a_name_code_reads_marks_nothing(tmp_path) -> None:
    """A map kept before a row read its name: code reads it on both sides,
    so the entry the carry drops moved nothing."""
    declared = BOXED.replace(
        "\\begin{document}", "\\newmdenv{lawbox}\n\\begin{document}", 1
    )

    def version(name: str, vocabulary: Vocabulary) -> Version:
        root = tmp_path / name
        root.mkdir()
        (root / "main.tex").write_text(declared, encoding="utf-8")
        document = read_paper(root, vocabulary=vocabulary)
        return Version(document, cut(document, CutRules(DisplayRule.OWN_BLOCK)))

    old = version("v1", Vocabulary((EnvEntry("lawbox", VocabularyKind.NONE),)))
    new = version("v2", Vocabulary())

    assert not vocabulary_marks(old.document, new.document)
    assert rebase_cut(old, new.document, new.cut).recut == ()


def test_a_statement_mapped_anew_marks_the_proof_that_trails_it(tmp_path) -> None:
    text = (
        "\\documentclass{article}\n\\begin{document}\n"
        "\\begin{mythm}\nEvery list merges.\n\\end{mythm}\n"
        "\\begin{proof}\nBy induction.\n\\end{proof}\n"
        "\\end{document}\n"
    )

    def version(name: str, kind: VocabularyKind, base: str | None) -> Version:
        root = tmp_path / name
        root.mkdir()
        (root / "main.tex").write_text(text, encoding="utf-8")
        mapped = Vocabulary((EnvEntry("mythm", kind, base),))
        document = read_paper(root, vocabulary=mapped)
        return Version(document, cut(document, CutRules(DisplayRule.OWN_BLOCK)))

    old = version("v1", VocabularyKind.CONTAINER, None)
    new = version("v2", VocabularyKind.WRAPS, "theorem")

    marks = vocabulary_marks(old.document, new.document)
    found = rebase_cut(old, new.document, new.cut)

    proof = next(b for b in new.cut.blocks if "induction" in _words(new, b))
    assert any("induction" in new.document.text(span) for span in marks.new)
    # re-cut, so the update's groups step sees it beside its statement
    assert proof.id in found.recut
