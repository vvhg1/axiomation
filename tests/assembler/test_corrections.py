"""Cut corrections applied to the cutter's cut.

Each operation and its refusals through `apply`, on the product's types,
and the changes' hashes. The split in three pins the ids and owned runs
a split gives.
"""

from __future__ import annotations

import pytest

from axiomation.assembler import Problem
from axiomation.cutter import (
    Block,
    Cut,
    Doubt,
    Form,
    GroupKind,
    PartKind,
    Role,
    Segment,
)
from axiomation.cutter.hashing import block_hash
from axiomation.cutter.model import role_set, segments_of
from axiomation.records import CorrectionClass, CutCorrection, CutOp


def C(cid: str, op: CutOp, **fields) -> CutCorrection:
    """A correction by a test: `at` may be one excerpt or several."""
    at = fields.pop("at", ())
    return CutCorrection(
        id=cid,
        op=op,
        cls=fields.pop("cls", CorrectionClass.ARGUMENT_BOUNDARY),
        why="a test",
        at=(at,) if isinstance(at, str) else tuple(at),
        **fields,
    )


def R(cid: str, block_id: str, *roles: Role) -> CutCorrection:
    """A `ROLES` correction from roles alone: one role is one
    segment covering the block, several are unsegmented, none is no
    role."""
    held = role_set(roles)
    return CutCorrection(
        id=cid,
        op=CutOp.ROLES,
        cls=CorrectionClass.OTHER,
        why="a test",
        block=block_id,
        roles=held,
        segments=segments_of(held),
    )


def ids(found: Cut) -> list[str]:
    return [b.id for b in found.blocks]


def block(found: Cut, block_id: str) -> Block:
    return found.block(block_id)


# --- the operations ----------------------------------------------------------


def test_a_split_in_two(latex) -> None:
    corrected = latex.apply(
        C("C1", CutOp.SPLIT, block="main.tex:4:1", at="Second sentence")
    )

    assert corrected.problems == ()
    assert corrected.applied == ("C1",)
    second = latex.id_at("Second sentence")
    assert second == "main.tex:4:30"
    assert ids(corrected.cut)[1:3] == ["main.tex:4:1", second]
    assert latex.owned(corrected.cut, "main.tex:4:1") == "First sentence of the intro."


def test_a_split_in_three_gives_its_ids_and_owned_runs(latex) -> None:
    """Cut points in any order; each piece after the first takes the id of
    its first owned character, and owns the trimmed runs of its span."""
    corrected = latex.apply(
        C(
            "C1",
            CutOp.SPLIT,
            block="main.tex:4:1",
            at=["Third sentence", "Second sentence"],
        )
    )

    assert corrected.problems == ()
    assert ids(corrected.cut) == [
        "main.tex:3:1",
        "main.tex:4:1",
        "main.tex:4:30",
        "main.tex:5:1",
        *ids(latex.cut)[2:],
    ]
    assert [latex.owned(corrected.cut, i) for i in ids(corrected.cut)[1:4]] == [
        "First sentence of the intro.",
        "Second sentence names a constant $C$.",
        "Third sentence starts a new claim.",
    ]
    first, second, third = (block(corrected.cut, i) for i in ids(corrected.cut)[1:4])
    original = block(latex.cut, "main.tex:4:1")
    text = latex.document.file("main.tex").text
    # each piece starts at its cut point; all but the last end at their last
    # owned character, the last where the block ended
    assert first.extent.start == original.extent.start
    assert second.extent.start == text.index("Second sentence")
    assert third.extent.start == text.index("Third sentence")
    assert first.extent.end == first.owned[-1].end
    assert second.extent.end == second.owned[-1].end
    assert third.extent.end == original.extent.end
    assert (first.form, second.form, third.form) == (Form.PROSE,) * 3


def test_a_merge_joins_runs_only_where_whitespace_alone_lies_between(latex):
    """The closing paragraph and the definition after it: the blank line
    between them joins their runs; the definition's hidden label still
    keeps two apart."""
    paragraph = latex.id_at("Final words.")
    definition = latex.id_at(r"\begin{definition}")
    before = [len(block(latex.cut, b).owned) for b in (paragraph, definition)]
    assert before == [1, 2]

    corrected = latex.apply(C("C1", CutOp.MERGE, blocks=(paragraph, definition)))

    assert corrected.problems == ()
    first, second = block(corrected.cut, paragraph).owned
    text = latex.document.file("main.tex").text
    assert text[first.start : first.end] == (
        "Final words. More final words.\n\n\\begin{definition}"
    )
    assert text[second.start : second.end].startswith("A term is defined.")


def test_a_merge_keeps_the_first_id(latex) -> None:
    corrected = latex.apply(
        C("C1", CutOp.MERGE, blocks=("main.tex:18:1", "main.tex:20:1"))
    )

    assert corrected.problems == ()
    assert "main.tex:20:1" not in ids(corrected.cut)
    merged = block(corrected.cut, "main.tex:18:1")
    assert merged.form is Form.PROSE
    assert merged.extent.end == block(latex.cut, "main.tex:20:1").extent.end
    # Nothing but a blank line lies between the two: one run, not two.
    assert len(merged.owned) == 1
    assert latex.owned(corrected.cut, "main.tex:18:1") == (
        "Closing words.\n\nFinal words. More final words."
    )


def test_a_merge_of_differing_forms_is_prose_and_of_differing_roles_none(
    latex,
) -> None:
    corrected = latex.apply(
        C("C1", CutOp.MERGE, blocks=("main.tex:20:1", "main.tex:22:1"))
    )

    assert corrected.problems == ()
    merged = block(corrected.cut, "main.tex:20:1")
    assert (merged.form, merged.roles, merged.environment, merged.meaning) == (
        Form.PROSE,
        (),
        None,
        None,
    )


def test_a_stray_mark_merges_without_taking_the_statement_s_form(
    latex,
) -> None:
    """A stray brace: a block with no letter
    or digit carries no form or role into a merge. The fixture's mark is a
    `;`: the cutter joins a bare brace itself."""
    brace = "main.tex:24:17"
    assert block(latex.cut, brace).doubts == (Doubt.MID_LINE_BOUNDARY,)

    corrected = latex.apply(C("C1", CutOp.MERGE, blocks=("main.tex:22:1", brace)))

    assert corrected.problems == ()
    merged = block(corrected.cut, "main.tex:22:1")
    assert (merged.form, merged.roles, merged.environment) == (
        Form.STATEMENT,
        (Role.DEFINITION,),
        "definition",
    )
    assert brace not in ids(corrected.cut)


def test_a_rebound_both_ways(markdown) -> None:
    a, b = "paper.md:3:1", "paper.md:5:1"
    earlier = markdown.apply(
        C("C1", CutOp.REBOUND, blocks=(a, b), at="Second sentence here.")
    )

    assert earlier.problems == ()
    new_b = markdown.id_at("Second sentence here.")
    assert ids(earlier.cut) == ["paper.md:1:1", a, new_b, "paper.md:7:1"]
    assert markdown.owned(earlier.cut, a) == "First sentence here."
    assert markdown.owned(earlier.cut, new_b) == (
        "Second sentence here. Another paragraph. It carries on."
    )

    later = markdown.apply(C("C1", CutOp.REBOUND, blocks=(a, b), at="It carries on."))

    assert later.problems == ()
    assert markdown.owned(later.cut, a).endswith("Another paragraph.")
    assert ids(later.cut)[2] == markdown.id_at("It carries on.")


def test_a_markdown_split_and_merge(markdown) -> None:
    corrected = markdown.apply(
        C("C1", CutOp.SPLIT, block="paper.md:3:1", at="Second sentence here."),
        C("C2", CutOp.MERGE, blocks=("paper.md:5:1", "paper.md:7:1")),
    )

    assert corrected.problems == ()
    assert ids(corrected.cut) == [
        "paper.md:1:1",
        "paper.md:3:1",
        markdown.id_at("Second sentence here."),
        "paper.md:5:1",
    ]


@pytest.mark.parametrize(
    ("kind", "role"),
    [
        (GroupKind.PROOF, Role.JUSTIFICATION),
        (GroupKind.PROOF_SECTION, Role.JUSTIFICATION),
        (GroupKind.DERIVATION, Role.JUSTIFICATION),
        (GroupKind.STEP_RUN, None),
        (GroupKind.STEP, None),
    ],
)
def test_a_group_of_each_kind(latex, kind: GroupKind, role: Role | None):
    corrected = latex.apply(
        C(
            "C1",
            CutOp.GROUP,
            kind=kind,
            first="main.tex:18:1",
            last="main.tex:20:1",
            names="main.tex:9:1",
        )
    )

    assert corrected.problems == ()
    group = next(g for g in corrected.cut.groups if g.id != "proof@main.tex:12:1")
    assert group.id == f"{kind.value}@main.tex:18:1"
    assert (group.kind, group.role) == (kind, role)
    assert group.blocks == ("main.tex:18:1", "main.tex:20:1")
    assert (group.names, group.named_label, group.named_by) == (
        "main.tex:9:1",
        None,
        None,
    )


def test_groups_are_ordered_by_first_block_then_longer_first(latex) -> None:
    corrected = latex.apply(
        C("C1", CutOp.GROUP, kind=GroupKind.STEP, first="main.tex:12:1",
          last="main.tex:12:1", names="none"),
        C("C2", CutOp.GROUP, kind=GroupKind.STEP_RUN, first="main.tex:4:1",
          last="main.tex:7:1", names="none"),
    )  # fmt: skip

    assert corrected.problems == ()
    assert [g.id for g in corrected.cut.groups] == [
        "step-run@main.tex:4:1",
        "proof@main.tex:12:1",
        "step@main.tex:12:1",
    ]


def test_ungroup_and_attach(latex) -> None:
    attached = latex.apply(
        C("C1", CutOp.ATTACH, group="proof@main.tex:12:1", names="none")
    )

    [group] = attached.cut.groups
    assert (group.names, group.named_label, group.named_by) == (None, None, None)
    assert group.role is Role.JUSTIFICATION

    moved = latex.apply(
        C("C1", CutOp.ATTACH, group="proof@main.tex:12:1", names="main.tex:22:1")
    )
    assert moved.cut.groups[0].names == "main.tex:22:1"

    ungrouped = latex.apply(C("C1", CutOp.UNGROUP, group="proof@main.tex:12:1"))
    assert ungrouped.problems == () and ungrouped.cut.groups == ()


def test_part_unpart_and_role(latex) -> None:
    lemma = "main.tex:9:1"
    corrected = latex.apply(
        C("C1", CutOp.PART, block=lemma, kind=PartKind.ITEM, marker="(a)",
          at="(a) It holds."),
        C("C2", CutOp.PART, block=lemma, kind=PartKind.ITEM, marker="(b)",
          at="(b) It also"),
        C("C3", CutOp.UNPART, block=lemma, marker="(b)"),
        R("C4", lemma, Role.DEFINITION),
    )  # fmt: skip

    assert corrected.problems == ()
    [part] = corrected.cut.parts
    assert (part.block, part.kind, part.marker, part.ordinal, part.label) == (
        lemma,
        PartKind.ITEM,
        "(a)",
        None,
        None,
    )
    assert latex.document.text(part.at) == "(a) It holds."
    assert block(corrected.cut, lemma).roles == (Role.DEFINITION,)
    assert block(corrected.cut, lemma).segments == (Segment("", Role.DEFINITION),)

    none = latex.apply(R("C1", lemma))
    assert block(none.cut, lemma).roles == ()
    assert block(none.cut, lemma).segments == ()


def test_a_split_through_a_part_trims_it_to_its_piece(latex) -> None:
    """A part that runs past a split's cut point stays with the piece that
    holds its start, trimmed to that piece: untrimmed, it would reach into the
    next piece, and identity's text map of the whole part would fail on an edit
    in the tail."""
    lemma = "main.tex:9:1"
    corrected = latex.apply(
        C("C1", CutOp.PART, block=lemma, kind=PartKind.ITEM, marker="(a)",
          at="(a) It holds. (b) It also"),
        C("C2", CutOp.SPLIT, block=lemma, at="(b) It also holds."),
    )  # fmt: skip

    assert corrected.problems == ()
    [part] = corrected.cut.parts
    head = block(corrected.cut, lemma)
    assert part.block == lemma
    assert part.at.end <= head.extent.end
    assert latex.document.text(part.at) == "(a) It holds."


def test_a_boundary_moved_through_a_part_trims_it_too(markdown) -> None:
    """The other block operation that re-homes parts: a part of the first
    block running past the moved boundary ends with the first block, and
    the second keeps none."""
    first = markdown.id_at("Another paragraph.")
    second = markdown.id_at("Last paragraph.")
    corrected = markdown.apply(
        C("C1", CutOp.PART, block=first, kind=PartKind.ITEM, marker="A",
          at="Another paragraph. It carries"),
        C("C2", CutOp.REBOUND, blocks=(first, second), at="It carries on."),
    )  # fmt: skip

    assert corrected.problems == ()
    [part] = corrected.cut.parts
    assert part.block == first
    assert markdown.document.text(part.at) == "Another paragraph."


def test_a_segment_boundary_must_be_found_once_in_the_block(latex) -> None:
    block_id = "main.tex:22:1"
    missing = CutCorrection(
        id="C1", op=CutOp.ROLES, cls=CorrectionClass.OTHER, why="a test",
        block=block_id, roles=(Role.DEFINITION, Role.CLAIM),
        segments=(Segment("", Role.CLAIM),
                  Segment("nowhere in it", Role.DEFINITION)),
    )  # fmt: skip
    twice = CutCorrection(
        id="C1", op=CutOp.ROLES, cls=CorrectionClass.OTHER, why="a test",
        block="main.tex:20:1", roles=(Role.DEFINITION, Role.CLAIM),
        segments=(Segment("", Role.CLAIM), Segment("words", Role.DEFINITION)),
    )  # fmt: skip

    refused = latex.apply(missing)
    assert refused.problems == (
        Problem(
            "C1",
            f"segment start occurs 0 times in the owned text of "
            f"{block_id}: 'nowhere in it'",
        ),
    )
    assert refused.cut == latex.cut
    twice_refused = latex.apply(twice)
    assert twice_refused.problems[0].message.startswith(
        "segment start occurs 2 times in the owned text of main.tex:20:1"
    )


def test_a_split_sends_the_footnote_to_its_piece(latex) -> None:
    footnote = "main.tex:7:28"
    corrected = latex.apply(
        C("C1", CutOp.SPLIT, block="main.tex:7:1", at="A second paragraph")
    )

    assert corrected.problems == ()
    piece = latex.id_at("A second paragraph")
    order = ids(corrected.cut)
    assert order.index("main.tex:7:1") < order.index(piece) < order.index(footnote)
    assert block(corrected.cut, footnote).within == piece

    before = latex.apply(C("C1", CutOp.SPLIT, block="main.tex:7:1", at="Later it"))
    assert block(before.cut, footnote).within == "main.tex:7:1"


def test_a_group_follows_a_split_inside_it(latex) -> None:
    corrected = latex.apply(
        C("C1", CutOp.SPLIT, block="main.tex:12:1", at="Hence done.")
    )

    assert corrected.problems == ()
    [group] = corrected.cut.groups
    assert group.blocks == (
        "main.tex:12:1",
        latex.id_at("Hence done."),
        "main.tex:15:1",
    )
    assert group.names == "main.tex:9:1"


# --- hashes, doubts and candidates -----------------------------------------


def test_a_changed_block_is_hashed_again_and_loses_its_doubts(latex) -> None:
    brace = "main.tex:24:17"
    corrected = latex.apply(
        C("C1", CutOp.SPLIT, block="main.tex:4:1", at="Second sentence"),
        C("C2", CutOp.MERGE, blocks=("main.tex:22:1", brace)),
    )

    for block_id in ("main.tex:4:1", "main.tex:4:30", "main.tex:22:1"):
        found = block(corrected.cut, block_id)
        runs = [latex.document.text(run) for run in found.owned]
        assert found.hash == block_hash(runs)
        assert found.doubts == ()
    assert block(corrected.cut, "main.tex:4:1").hash != (
        block(latex.cut, "main.tex:4:1").hash
    )


def test_a_rebound_s_two_blocks_are_hashed_again(markdown) -> None:
    a, b = "paper.md:3:1", "paper.md:5:1"
    corrected = markdown.apply(
        C("C1", CutOp.REBOUND, blocks=(a, b), at="Second sentence here.")
    )

    new_b = markdown.id_at("Second sentence here.")
    for block_id in (a, new_b):
        found = block(corrected.cut, block_id)
        runs = [markdown.document.text(run) for run in found.owned]
        assert found.hash == block_hash(runs)
        assert found.doubts == ()
    assert block(corrected.cut, a).hash != block(markdown.cut, a).hash
    last = "paper.md:7:1"
    assert block(corrected.cut, last).hash == block(markdown.cut, last).hash


def test_an_untouched_block_keeps_its_hash_and_doubts(latex) -> None:
    brace = "main.tex:24:17"
    corrected = latex.apply(
        C("C1", CutOp.SPLIT, block="main.tex:4:1", at="Second sentence"),
        R("C2", "main.tex:22:1", Role.CLAIM),
    )

    for block_id in (brace, "main.tex:22:1", "main.tex:7:1", "main.tex:9:1"):
        assert block(corrected.cut, block_id).hash == block(latex.cut, block_id).hash
        assert block(corrected.cut, block_id).doubts == (
            block(latex.cut, block_id).doubts
        )
    assert block(corrected.cut, brace).doubts == (Doubt.MID_LINE_BOUNDARY,)


def test_a_reference_a_split_moves_names_its_new_block(latex) -> None:
    [before] = latex.cut.candidates
    assert (before.source, before.resolved) == ("main.tex:7:1", "main.tex:9:1")

    corrected = latex.apply(C("C1", CutOp.SPLIT, block="main.tex:7:1", at="Later it"))

    [after] = corrected.cut.candidates
    assert after.source == latex.id_at("Later it")
    assert (after.target, after.resolved, after.at) == (
        "lem:a",
        "main.tex:9:1",
        before.at,
    )


def test_no_correction_gives_the_cut_back(latex, markdown) -> None:
    for paper in (latex, markdown):
        corrected = paper.apply()
        assert corrected.cut == paper.cut
        assert (corrected.applied, corrected.problems) == ((), ())


# --- refusals ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("refused", "message"),
    [
        (C("C1", CutOp.SPLIT, block="main.tex:4:1", at="not in it"), "occurs 0 times"),
        (C("C1", CutOp.SPLIT, block="main.tex:20:1", at="words"), "occurs 2 times"),
        (C("C1", CutOp.SPLIT, block="main.tex:18:1", at="Final words"), "0 times"),
        (C("C1", CutOp.SPLIT, block="main.tex:99:1", at="x"), "does not exist"),
        (
            C("C1", CutOp.SPLIT, block="main.tex:4:1", at="First sentence"),
            "nothing to split",
        ),
        (
            C("C1", CutOp.MERGE, blocks=("main.tex:3:1", "main.tex:7:1")),
            "not adjacent: main.tex:4:1 lies between",
        ),
        (
            C("C1", CutOp.MERGE, blocks=("main.tex:7:1", "main.tex:7:28")),
            "at two nesting levels",
        ),
        (
            C("C1", CutOp.MERGE, blocks=("main.tex:20:1", "main.tex:18:1")),
            "not in reading order",
        ),
        (C("C1", CutOp.UNGROUP, group="proof@main.tex:1:1"), "does not exist"),
        (
            C("C1", CutOp.ATTACH, group="proof@main.tex:12:1", names="main.tex:1:1"),
            "block main.tex:1:1 does not exist",
        ),
        (C("C1", CutOp.UNPART, block="main.tex:9:1", marker="(z)"), "0 parts"),
        (
            C("C1", CutOp.PART, block="main.tex:9:1", kind=PartKind.ITEM,
              marker="(c)", at="(c)"),
            "part occurs 0 times",
        ),
        (
            C("C1", CutOp.GROUP, kind=GroupKind.PROOF, first="main.tex:12:1",
              last="main.tex:15:1", names="none"),
            "group proof@main.tex:12:1 exists already",
        ),
        (
            C("C1", CutOp.GROUP, kind=GroupKind.STEP, first="main.tex:20:1",
              last="main.tex:18:1", names="none"),
            "main.tex:18:1 comes before main.tex:20:1",
        ),
        (C("C1", CutOp.SPLIT, block="main.tex:4:1"), "at: missing, split takes it"),
        (
            C("C1", CutOp.MERGE, blocks=("main.tex:9:1", "main.tex:12:1")),
            "leaves block main.tex:9:1 partly inside group proof@main.tex:12:1",
        ),
        (
            C("C1", CutOp.REBOUND, blocks=("main.tex:7:1", "main.tex:9:1"),
              at="Let $x$ be positive."),
            "moves the start of the block group proof@main.tex:12:1 names",
        ),
        (
            C("C1", CutOp.REBOUND, blocks=("main.tex:12:1", "main.tex:15:1"),
              at="A second step"),
            "main.tex:15:1 already begins there",
        ),
    ],
)  # fmt: skip
def test_each_refusal_is_named_and_changes_nothing(
    latex, refused, message: str
) -> None:
    later = C("C2", CutOp.MERGE, blocks=("main.tex:18:1", "main.tex:20:1"))
    corrected = latex.apply(refused, later)

    assert [p.correction for p in corrected.problems] == ["C1"]
    assert message in corrected.problems[0].message
    assert corrected.applied == ("C2",)
    alone = latex.apply(later)
    assert corrected.cut == alone.cut


def test_a_merge_across_two_files_is_refused(two_files) -> None:
    first, second = (b.id for b in two_files.cut.blocks)
    assert (first, second) == ("main.tex:3:1", "second.tex:1:1")

    corrected = two_files.apply(C("C1", CutOp.MERGE, blocks=(first, second)))

    assert corrected.problems == (
        Problem("C1", "blocks main.tex:3:1, second.tex:1:1 are in two files"),
    )
    assert corrected.cut == two_files.cut


def test_a_block_an_earlier_correction_merged_away_no_longer_exists(
    latex,
) -> None:
    corrected = latex.apply(
        C("C1", CutOp.MERGE, blocks=("main.tex:18:1", "main.tex:20:1")),
        C("C2", CutOp.SPLIT, block="main.tex:20:1", at="More final words."),
    )

    assert corrected.problems == (Problem("C2", "block main.tex:20:1 does not exist"),)


def test_a_merge_of_two_known_roles_is_a_mixed_block_unsegmented(latex) -> None:
    corrected = latex.apply(
        R("C1", "main.tex:20:1", Role.CLAIM),
        R("C2", "main.tex:22:1", Role.DEFINITION),
        C("C3", CutOp.MERGE, blocks=("main.tex:20:1", "main.tex:22:1")),
    )

    assert corrected.problems == ()
    merged = block(corrected.cut, "main.tex:20:1")
    # The roles in the enum's order, the boundary left to the roles pass.
    assert (merged.roles, merged.segments) == ((Role.DEFINITION, Role.CLAIM), ())


def test_a_merge_of_one_role_keeps_it_as_one_segment(latex) -> None:
    corrected = latex.apply(
        R("C1", "main.tex:20:1", Role.CLAIM),
        R("C2", "main.tex:22:1", Role.CLAIM),
        C("C3", CutOp.MERGE, blocks=("main.tex:20:1", "main.tex:22:1")),
    )

    merged = block(corrected.cut, "main.tex:20:1")
    assert merged.roles == (Role.CLAIM,)
    assert len(merged.segments) == 1
