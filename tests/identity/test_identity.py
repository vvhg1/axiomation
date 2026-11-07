"""Identity across two versions: the tiers on synthetic
two-version papers, the classes, the macros, the lineage's composition and
the document hash."""

from __future__ import annotations

import pytest

from axiomation.identity import (
    BlockClass,
    Lineage,
    Link,
    TextMap,
    Tier,
    Version,
    changed_macros,
    macro_uses,
    pair,
)
from axiomation.questions.edge_sheet import handles
from axiomation.reader import Span, document_hash


def body(*paragraphs: str, preamble: str = "") -> str:
    """A LaTeX paper whose body is `paragraphs`, a blank line between."""
    return (
        "\\documentclass{article}\n"
        + preamble
        + "\\begin{document}\n\n"
        + "\n\n".join(paragraphs)
        + "\n\n\\end{document}\n"
    )


A = "Alpha paragraph opens the paper with its setting and aims."
B = "Bravo paragraph states the model and fixes the notation used."
C = "Charlie paragraph recalls the known results that we build on."
D = "Delta paragraph closes the section with an outlook on the rest."
E = "Echo paragraph begins the second section with the main question."
F = "Foxtrot paragraph ends the paper and thanks the reviewers kindly."


def text(version: Version, block_id: str) -> str:
    block = next(b for b in version.cut.blocks if b.id == block_id)
    return " ".join(" ".join(version.document.text(run) for run in block.owned).split())


def holding(version: Version, fragment: str) -> str:
    (found,) = [b.id for b in version.cut.blocks if fragment in text(version, b.id)]
    return found


def link_of(lineage: Lineage, old: Version, fragment: str) -> Link:
    found = lineage.link_of_old(holding(old, fragment))
    assert found is not None
    return found


def test_an_inserted_paragraph_shifts_every_id_and_changes_no_block(version):
    old = version(body(A, B, C, D))
    new = version(body(A, "A new paragraph, inserted here.", B, C, D))
    lineage = pair(old, new)
    for fragment in (B, C, D):
        link = link_of(lineage, old, fragment)
        assert link.cls is BlockClass.SAME and link.tier is Tier.TEXT_MAP
        assert link.new != link.old  # the positional id moved
        assert text(new, link.new[0]) == fragment
    (added,) = [link for link in lineage.links if link.cls is BlockClass.ADDED]
    assert text(new, added.new[0]) == "A new paragraph, inserted here."


def test_a_block_moved_to_another_place_is_moved_by_its_hash(version):
    old = version(body(A, B, C, "\\section{Two}", D, E))
    new = version(body(A, C, "\\section{Two}", D, B, E))
    link = link_of(pair(old, new), old, B)
    assert (link.cls, link.tier) == (BlockClass.MOVED, Tier.HASH)
    assert text(new, link.new[0]) == B


def test_a_reworded_block_is_changed_by_its_ratio(version):
    old = version(body(A, B, C))
    new = version(body(A, B.replace("fixes the notation", "sets up notation"), C))
    link = link_of(pair(old, new), old, B)
    assert (link.cls, link.tier) == (BlockClass.CHANGED, Tier.RATIO)


def test_a_rewritten_labelled_theorem_is_paired_by_its_label(version):
    theorem = (
        "\\begin{theorem}\\label{thm:main}\nEvery bounded sequence has a "
        "convergent subsequence.\n\\end{theorem}"
    )
    rewritten = (
        "\\begin{theorem}\\label{thm:main}\nA compact metric space is "
        "sequentially compact, and conversely.\n\\end{theorem}"
    )
    old = version(body(A, theorem, "Short note.", C))
    new = version(body(A, rewritten, "Other note.", C))
    link = link_of(pair(old, new), old, "Every bounded sequence")
    assert (link.cls, link.tier) == (BlockClass.CHANGED, Tier.LABEL)


def test_a_gap_of_one_block_each_of_one_form_is_one_changed_block(version):
    old = version(body(A, "Completely different words stand in this place.", C))
    new = version(body(A, "Nothing here resembles what was there before.", C))
    link = link_of(pair(old, new), old, "Completely different")
    assert (link.cls, link.tier) == (BlockClass.CHANGED, Tier.FORM)


def test_a_split_and_a_merge_by_their_words_name_their_heir(version):
    first = "The first half of the paragraph says one short thing."
    second = "The second half adds a much longer remark with many more words in it."
    old = version(body(A, f"{first} {second}", C, D, E))
    new = version(body(A, first, second, C, f"{D} {E}"))
    lineage = pair(old, new)
    split = link_of(lineage, old, first)
    assert (split.cls, split.tier) == (BlockClass.SPLIT, Tier.WORDS)
    assert [text(new, b) for b in split.new] == [first, second]
    assert text(new, split.heir) == first  # its first words, though fewer
    merged = link_of(lineage, old, D)
    assert merged.cls is BlockClass.MERGED and len(merged.old) == 2
    assert text(old, merged.heir) == D  # NEW's first words are D's


def test_a_split_s_heir_falls_to_most_words_where_no_child_opens_alike(version):
    """The heir of a split: its label, else the
    child holding its first words, else the one with most of its words. A
    first sentence under six words gives its first six words, which here
    run across the split: no child holds them, most words decide."""
    first = "Yes it holds."
    second = "Then more words follow here and continue on for a while."
    old = version(body(A, f"{first} {second}", C))
    new = version(body(A, first, second, C))
    lineage = pair(old, new)

    split = link_of(lineage, old, "Yes it holds")
    assert split.cls is BlockClass.SPLIT
    assert text(new, split.heir) == second


def test_a_paragraph_split_by_an_edit_continues_in_its_opening_half(version):
    """The seam reworded: the ratio tier pairs the
    OLD block with the longer half, but the half that starts with its first
    sentence continues it, CHANGED; the other half is ADDED."""
    opening = "The averaging rule has two weaknesses."
    rest = (
        "It weighs every round alike, so an early round counts as much as the last one."
    )
    tail = (
        "it needs the number of rounds in advance, which a forecaster who "
        "stops on a signal does not know, and a running rule may suit such a "
        "forecaster better."
    )
    old = version(body(A, f"{opening} {rest} Second, {tail}", C))
    new = version(body(A, f"{opening} {rest}", f"A second weakness is that {tail}", C))
    lineage = pair(old, new)

    link = link_of(lineage, old, opening)
    assert link.cls is BlockClass.CHANGED
    assert link.new == (holding(new, opening),)
    second = holding(new, "A second weakness")
    (added,) = [x for x in lineage.links if second in x.new]
    assert added.cls is BlockClass.ADDED


def test_an_added_and_a_deleted_block(version):
    old = version(body(A, B, C, "\\section{Two}", D))
    new = version(body(A, C, "\\section{Two}", D, F))
    lineage = pair(old, new)
    assert link_of(lineage, old, B).cls is BlockClass.DELETED
    (added,) = [link for link in lineage.links if link.cls is BlockClass.ADDED]
    assert text(new, added.new[0]) == F


def test_two_blocks_with_one_hash_moved_are_not_guessed_between(version):
    twin = "The same remark, written twice in the paper."
    old = version(body(A, twin, B, twin, C, "\\section{Two}", D))
    new = version(body(A, B, C, "\\section{Two}", twin, D, twin))
    lineage = pair(old, new)
    for link in lineage.links:
        if any(text(old, b) == twin for b in link.old):
            assert link.cls is BlockClass.DELETED


def test_a_tie_in_ratio_pairs_nothing(version):
    old = version(body(A, "one two three four five six seven eight", C))
    new = version(
        body(
            A,
            "one two three four five six nine ten",
            "one two three four five six eleven twelve",
            C,
        )
    )
    link = link_of(pair(old, new), old, "one two three")
    assert link.cls is BlockClass.DELETED


def test_the_ratio_tier_s_thresholds_are_keywords_for_the_lab_s_sweep(version):
    """θ above the pair's ratio leaves it to the form rule; δ_pair 0 lets a
    tie pair (the sweep only, never stored)."""
    old = version(body(A, B, C))
    new = version(body(A, B.replace("fixes the notation", "sets up notation"), C))
    assert link_of(pair(old, new, theta=0.99), old, B).tier is Tier.FORM

    old = version(body(A, "one two three four five six seven eight", C))
    new = version(
        body(
            A,
            "one two three four five six nine ten",
            "one two three four five six eleven twelve",
            C,
        )
    )
    link = link_of(pair(old, new, delta_pair=0.0), old, "one two three")
    assert (link.cls, link.tier) == (BlockClass.CHANGED, Tier.RATIO)


def test_only_the_mathematics_or_only_the_markup_differs(version):
    old = version(body(A, "Let $x+1$ be given here.", "We say \\emph{this} now.", C))
    new = version(body(A, "Let $x+2$ be given here.", "We say \\textbf{this} now.", C))
    lineage = pair(old, new)
    assert link_of(lineage, old, "Let $x+1$").cls is BlockClass.MATH
    assert link_of(lineage, old, "We say").cls is BlockClass.MARKUP


def test_a_handle_s_suffix_shift_rebinds_nothing(version):
    """Handles
    by first words shift their repeat suffix when a twin is inserted
    before; identity pairs by text, so each block keeps its own."""
    first = "See the figure below for the first case."
    second = "See the figure below for the second case."
    inserted = "See the figure below for an earlier case."
    old = version(body(A, first, second, C))
    new = version(body(A, inserted, first, second, C))
    lineage = pair(old, new)
    for fragment in (first, second):
        link = link_of(lineage, old, fragment)
        assert link.cls is BlockClass.SAME
        assert text(new, link.new[0]) == fragment
    # the handles would have rebound: the old handle of `first` names
    # another block in the new version
    old_handles = handles([text(old, b.id) for b in old.cut.blocks])
    new_handles = handles([text(new, b.id) for b in new.cut.blocks])
    at = [text(old, b.id) for b in old.cut.blocks].index(first)
    named = [text(new, b.id) for b in new.cut.blocks][
        new_handles.index(old_handles[at])
    ]
    assert named != first


MACROS = "\\newcommand{\\R}{\\mathbb{R}}\n\\newcommand{\\Rp}{\\R_{+}}\n"


def test_a_changed_macro_marks_its_users_and_their_users_macro(version):
    used = "Let $x \\in \\R$ be real."
    via = "Let $y \\in \\Rp$ be positive."
    old = version(body(A, used, via, C, preamble=MACROS))
    new = version(
        body(A, used, via, C, preamble=MACROS.replace("\\mathbb{R}", "\\mathbf{R}"))
    )
    assert changed_macros(old.document, new.document) == {"\\R", "\\Rp"}
    lineage = pair(old, new)
    for fragment in (used, via):
        link = link_of(lineage, old, fragment)
        assert link.cls is BlockClass.MACRO
        assert text(new, link.new[0]) == fragment
    assert link_of(lineage, old, A).cls is BlockClass.SAME


def test_macro_uses_match_whole_names(version):
    paper = version(body("Here $\\Rp$ only.", "And $\\R$ alone.", preamble=MACROS))
    uses = macro_uses(paper.document, paper.cut, ["\\R", "\\Rp"])
    assert uses[holding(paper, "Here")] == {"\\Rp"}
    assert uses[holding(paper, "And")] == {"\\R"}


def test_a_markdown_paper_without_macros_is_unaffected(version):
    old = version("# One\n\nA first line here.\n\nA second one there.\n", "paper.md")
    new = version("# One\n\nA first line here.\n\nA second one there!\n", "paper.md")
    assert changed_macros(old.document, new.document) == frozenset()
    # "here" to "there!" changes no word outside maths: markup only
    assert [link.cls for link in pair(old, new).links] == [
        BlockClass.SAME,
        BlockClass.SAME,
        BlockClass.MARKUP,
    ]


def test_every_block_is_in_exactly_one_link(version):
    old = version(body(A, B, C, D, E))
    new = version(body(E, B.upper(), "Something new.", D, A))
    lineage = pair(old, new)
    olds = [b for link in lineage.links for b in link.old]
    news = [b for link in lineage.links for b in link.new]
    assert sorted(olds) == sorted(b.id for b in old.cut.blocks)
    assert sorted(news) == sorted(b.id for b in new.cut.blocks)


def test_the_lineage_is_symmetric(version):
    old = version(body(A, B, C, "\\section{Two}", D))
    new = version(body(A, C.replace("known", "classical"), "\\section{Two}", D, B))
    there = {(link.old, link.new, link.cls) for link in pair(old, new).links}
    back = {(link.new, link.old, link.cls) for link in pair(new, old).links}
    mirror = {
        BlockClass.ADDED: BlockClass.DELETED,
        BlockClass.DELETED: BlockClass.ADDED,
    }
    assert there == {(o, n, mirror.get(c, c)) for o, n, c in back}


def test_lineages_compose_over_the_middle_version(version):
    v1 = version(body(A, B, C, D))
    v2 = version(body(A, B.replace("model", "process"), C, "Inserted later.", D))
    v3 = version(body(A, B.replace("model", "process"), "Inserted later.", D))
    composed = pair(v1, v2).compose(pair(v2, v3))
    assert link_of(composed, v1, A).cls is BlockClass.SAME
    assert link_of(composed, v1, B).cls is BlockClass.CHANGED
    assert link_of(composed, v1, C).cls is BlockClass.DELETED
    added = [link for link in composed.links if link.cls is BlockClass.ADDED]
    assert [text(v3, link.new[0]) for link in added] == ["Inserted later."]
    assert {link.tier for link in composed.links} == {Tier.COMPOSED}


def test_a_span_in_an_equal_region_maps_and_one_across_an_edit_does_not(version):
    old = version(body(A, B, C))
    new = version(body("A new first line.", A, B.replace("model", "process"), C))
    text_map = TextMap.build(old.document, new.document)
    at = old.document.file("paper.tex").text.index(A)
    mapped = text_map.span(Span("paper.tex", at, at + len(A)))
    assert mapped is not None
    assert new.document.text(mapped) == A
    at = old.document.file("paper.tex").text.index(B)
    assert text_map.span(Span("paper.tex", at, at + len(B))) is None


#: One paragraph of three sentences, re-wrapped from 60 columns to 45: the
#: same words, its line breaks elsewhere.
WRAPPED_60 = (
    "Our first result bounds the deviation of the empirical mean.\n"
    "Its proof uses a martingale argument. The constant in the\n"
    "bound is not sharp."
)
WRAPPED_45 = (
    "Our first result bounds the deviation of the\n"
    "empirical mean. Its proof uses a martingale\n"
    "argument. The constant in the bound is not\n"
    "sharp."
)
SENTENCES = (
    "Our first result bounds the deviation of the empirical mean.",
    "Its proof uses a martingale argument.",
    "The constant in the bound is not sharp.",
)


def test_a_span_across_a_moved_line_break_maps(version):
    """A re-wrapped paragraph still refines. A run
    whose words are equal maps though a line break inside it moved, or
    became a space; one with a word changed still does not."""
    old, new = version(body(A, WRAPPED_60, C)), version(body(A, WRAPPED_45, C))
    text_map = TextMap.build(old.document, new.document)
    source = old.document.file("paper.tex").text
    for sentence in SENTENCES:
        words = sentence.split()
        at = source.index(words[0] + " " + words[1])
        end = source.index(words[-1], at) + len(words[-1])
        mapped = text_map.span(Span("paper.tex", at, end))
        assert mapped is not None, sentence
        assert " ".join(new.document.text(mapped).split()) == sentence
    edited = version(body(A, WRAPPED_45.replace("martingale", "coupling"), C))
    text_map = TextMap.build(old.document, edited.document)
    at = source.index("Its proof")
    assert text_map.span(Span("paper.tex", at, source.index("argument.") + 9)) is None


def test_the_document_hash_follows_the_text(version):
    one, same = version(body(A, B)), version(body(A, B))
    other = version(body(A, C))
    assert document_hash(one.document) == document_hash(same.document)
    assert document_hash(one.document) != document_hash(other.document)


def test_an_inserted_macro_user_marks_no_other_block_by_its_id(version):
    """NEW's ids shift, so a NEW block that uses the
    changed macro holds an id an OLD block that uses none held."""
    user = "Let $x \\in \\R$ be real."
    old = version(body(A, B, preamble=MACROS))
    new = version(
        body(user, A, B, preamble=MACROS.replace("\\mathbb{R}", "\\mathbf{R}"))
    )
    lineage = pair(old, new)
    for fragment in (A, B):
        assert link_of(lineage, old, fragment).cls is BlockClass.SAME


def test_a_markup_edit_or_a_move_with_a_changed_macro_is_macro(version):
    used = "We say \\emph{this} of $\\R$ now."
    moved = "And $\\R$ moves to the end."
    old = version(body(A, used, moved, C, D, preamble=MACROS))
    new = version(
        body(
            A,
            used.replace("\\emph", "\\textbf"),
            C,
            D,
            moved,
            preamble=MACROS.replace("\\mathbb{R}", "\\mathbf{R}"),
        )
    )
    lineage = pair(old, new)
    assert link_of(lineage, old, "We say").cls is BlockClass.MACRO
    link = link_of(lineage, old, "moves to the end")
    assert link.cls is BlockClass.MACRO and link.moved


def test_a_reorder_the_text_map_cannot_anchor_is_moved(version):
    """One file renamed, so the text map pairs nothing (as when a paper's one
    file becomes eight): the order of the pairs still shows the move."""
    old = version(body(A, B, C, D, E))
    new = version(body(A, C, D, B, E), name="main.tex")
    lineage = pair(old, new)
    assert {link.tier for link in lineage.links} <= {Tier.HASH, Tier.KEY}
    moved = [link for link in lineage.links if link.moved]
    assert [text(old, link.old[0]) for link in moved] == [B]
    assert moved[0].cls is BlockClass.MOVED
    assert link_of(lineage, old, C).cls is BlockClass.SAME


def test_a_labelled_block_moved_is_moved_by_its_label(version):
    theorem = "\\begin{theorem}\\label{thm:one}\nIt holds.\n\\end{theorem}"
    old = version(body(A, theorem, B, "\\section{Two}", C, D))
    new = version(body(A, B, "\\section{Two}", C, theorem, D))
    link = link_of(pair(old, new), old, "It holds.")
    assert (link.cls, link.tier, link.moved) == (BlockClass.MOVED, Tier.LABEL, True)


def test_equal_keys_pair_in_order_only_when_their_counts_agree(version):
    """Tier 3: two markup-only twins pair in order;
    one against two pairs none, and their ratio ties, so none pairs."""
    twin = "We say \\emph{twice} the same."
    marked = twin.replace("\\emph", "\\textbf")
    old = version(body(A, twin, B.upper(), twin, C))
    new = version(body(A, marked, B.upper(), marked, C))
    lineage = pair(old, new)
    pairs = [link for link in lineage.links if link.cls is BlockClass.MARKUP]
    assert [link.tier for link in pairs] == [Tier.KEY, Tier.KEY]
    at_old = {b.id: n for n, b in enumerate(old.cut.blocks)}
    at_new = {b.id: n for n, b in enumerate(new.cut.blocks)}
    order = sorted((at_old[link.old[0]], at_new[link.new[0]]) for link in pairs)
    assert order[0][1] < order[1][1]  # the first twin to the first
    old = version(body(A, twin, C))
    new = version(body(A, marked, marked, C))
    assert link_of(pair(old, new), old, "twice").cls is BlockClass.DELETED


def test_a_composed_split_or_merge_keeps_the_heir_its_steps_chose():
    """The heir identity chose by words in either step follows through v2:
    a split's in v1→v2 to its block in v3, a split's in v2→v3 from the
    block v1's continues as, and a merge's back to v1."""

    def links(*found: Link) -> Lineage:
        return Lineage(tuple(found))

    first_split = links(
        Link(("o1",), ("m1", "m2"), BlockClass.SPLIT, Tier.WORDS, "m2")
    ).compose(
        links(
            Link(("m1",), ("n1",), BlockClass.SAME, Tier.TEXT_MAP),
            Link(("m2",), ("n2",), BlockClass.CHANGED, Tier.RATIO),
        )
    )
    assert first_split.links == (
        Link(("o1",), ("n1", "n2"), BlockClass.SPLIT, Tier.COMPOSED, "n2"),
    )
    then_split = links(Link(("o1",), ("m1",), BlockClass.SAME, Tier.TEXT_MAP)).compose(
        links(Link(("m1",), ("n1", "n2"), BlockClass.SPLIT, Tier.WORDS, "n2"))
    )
    assert then_split.links[0].heir == "n2"
    merged = links(
        Link(("o1", "o2"), ("m1",), BlockClass.MERGED, Tier.WORDS, "o2")
    ).compose(links(Link(("m1",), ("n1",), BlockClass.SAME, Tier.TEXT_MAP)))
    assert merged.links == (
        Link(("o1", "o2"), ("n1",), BlockClass.MERGED, Tier.COMPOSED, "o2"),
    )


def test_a_composition_through_a_split_or_a_merge_is_changed():
    def links(*found: Link) -> Lineage:
        return Lineage(tuple(found))

    split_then_lost = links(
        Link(("o1",), ("m1", "m2"), BlockClass.SPLIT, Tier.WORDS, "m1")
    ).compose(
        links(
            Link(("m1",), ("n1",), BlockClass.SAME, Tier.TEXT_MAP),
            Link(("m2",), (), BlockClass.DELETED, Tier.NONE),
        )
    )
    assert split_then_lost.links == (
        Link(("o1",), ("n1",), BlockClass.CHANGED, Tier.COMPOSED),
    )
    added_then_merged = links(
        Link(("o1",), ("m1",), BlockClass.SAME, Tier.TEXT_MAP),
        Link((), ("m2",), BlockClass.ADDED, Tier.NONE),
    ).compose(links(Link(("m1", "m2"), ("n1",), BlockClass.MERGED, Tier.WORDS, "m1")))
    assert added_then_merged.links == (
        Link(("o1",), ("n1",), BlockClass.CHANGED, Tier.COMPOSED),
    )


# --- the form tier ----------------------


def test_a_heading_renamed_beside_an_edited_paragraph_is_one_changed_block(version):
    """A heading renamed is a changed block of heading form. The form
    tier decides on the blocks the surer tiers left open, not on the gap's
    whole population: beside a paragraph the ratio tier pairs, the two
    headings are left, one a side, and pair as changed."""
    old = version(body(A, "\\section{Experiments}", B, C))
    new = version(body(A, "\\section{Empirical evaluation}", B + " Edited.", C))
    lineage = pair(old, new)

    heading = link_of(lineage, old, "Experiments")
    assert heading.cls is BlockClass.CHANGED and heading.tier is Tier.FORM
    assert link_of(lineage, old, "Bravo").cls is BlockClass.CHANGED


def test_a_heading_renamed_beside_a_deleted_paragraph_still_pairs(version):
    """Counted per form: one heading open a side pairs, though a paragraph
    is left open beside it."""
    old = version(body(A, "\\section{Experiments}", B, C))
    new = version(body(A, "\\section{Empirical evaluation}", C))
    lineage = pair(old, new)

    assert link_of(lineage, old, "Experiments").cls is BlockClass.CHANGED
    assert link_of(lineage, old, "Bravo").cls is BlockClass.DELETED


def test_a_deleted_and_an_added_paragraph_beside_an_edit_do_not_pair(version):
    """For prose the ratio tier has judged every pair already: a paragraph
    deleted and another added beside an edited one stay deleted and added."""
    old = version(body(A, B, C, D))
    new = version(body(A, B + " Edited.", E, D))
    lineage = pair(old, new)

    assert link_of(lineage, old, "Bravo").cls is BlockClass.CHANGED
    assert link_of(lineage, old, "Charlie").cls is BlockClass.DELETED
    echo = holding(new, "Echo")
    (added,) = [link for link in lineage.links if echo in link.new]
    assert added.cls is BlockClass.ADDED


STATEMENT = "Every bounded sequence has a convergent subsequence."


def environment(name: str) -> str:
    return f"\\begin{{{name}}}\n{STATEMENT}\n\\end{{{name}}}"


@pytest.mark.parametrize(
    "was, now, cls",
    [
        ("lemma", "definition", BlockClass.CHANGED),
        ("proof", "lemma", BlockClass.CHANGED),
        ("lemma", "theorem", BlockClass.MARKUP),
        # a conjecture's claim needs no argument; a theorem's does
        ("theorem", "conjecture", BlockClass.CHANGED),
        ("conjecture", "lemma", BlockClass.CHANGED),
    ],
)
def test_an_environment_whose_meaning_changed_is_never_the_same_block(
    version, was, now, cls
):
    """The hash leaves out `\\begin{…}`: a block whose environment's meaning
    changed is changed where the role its meaning says changed or it moves
    to or from a conjecture, else only its markup; never SAME, which would
    reopen nothing and carry the old roles."""
    old = version(body(A, environment(was), C))
    new = version(body(A, environment(now), C))
    lineage = pair(old, new)

    assert link_of(lineage, old, STATEMENT).cls is cls


@pytest.mark.parametrize(
    "was, now, cls",
    [
        ("Theorem", "Conjecture", BlockClass.CHANGED),
        ("Conjecture", "Theorem", BlockClass.CHANGED),
        ("Theorem", "Proposition", BlockClass.MARKUP),
    ],
)
def test_a_declaration_edited_alone_reaches_the_blocks_that_use_it(
    version, was, now, cls
):
    """`\\newtheorem{claim}{…}` edited in the preamble: the blocks' text is
    the same, and their meaning is the declaration's."""
    old = version(
        body(A, environment("claim"), C, preamble=f"\\newtheorem{{claim}}{{{was}}}\n")
    )
    new = version(
        body(A, environment("claim"), C, preamble=f"\\newtheorem{{claim}}{{{now}}}\n")
    )

    assert link_of(pair(old, new), old, STATEMENT).cls is cls


def test_a_proof_become_a_lemma_keeps_none_of_its_roles(version):
    """The rebase leaves a changed block to the cutter, whose roles come from
    the new environment: the proof's justification is not carried onto the
    lemma."""
    from dataclasses import replace

    from axiomation.cutter.model import Role, Segment
    from axiomation.identity import rebase_cut

    old = version(body(A, environment("proof"), C))
    held = holding(old, STATEMENT)
    blocks = tuple(
        replace(
            b, roles=(Role.JUSTIFICATION,), segments=(Segment("", Role.JUSTIFICATION),)
        )
        if b.id == held
        else b
        for b in old.cut.blocks
    )
    old = Version(old.document, replace(old.cut, blocks=blocks))
    new = version(body(A, environment("lemma"), C))

    found = rebase_cut(old, new.document, new.cut)
    lemma = next(b for b in found.cut.blocks if STATEMENT in text(new, b.id))

    assert lemma.id in found.recut
    assert Role.JUSTIFICATION not in lemma.roles


PARKED = "parked: rename-aware refs"


@pytest.mark.xfail(strict=True, reason=PARKED)
def test_a_label_renamed_with_its_refs_leaves_the_citing_words_unchanged(version):
    """The classes are by rendered words: a `\\ref` whose label was
    renamed renders the same, but the words key holds the label's name, so
    the citing paragraph is CHANGED and asked."""
    theorem = "\\begin{theorem}\\label{thm:old}\nThe output is bounded.\n\\end{theorem}"
    citing = "By Theorem~\\ref{thm:old}, the output never exceeds one."
    old = version(body(A, theorem, citing))
    new = version(
        body(
            A,
            theorem.replace("thm:old", "thm:new"),
            citing.replace("thm:old", "thm:new"),
        )
    )
    lineage = pair(old, new)

    assert link_of(lineage, old, "the output never").cls is BlockClass.MARKUP
