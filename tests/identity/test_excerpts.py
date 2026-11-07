"""Excerpts re-found across versions, and the fuzzy hint:
exactly once, in the blocks an OLD block became and then their
neighbours; a fuzzy match only as a hint; the anchor failures
where they bear on excerpts."""

from __future__ import annotations

from axiomation.identity import (
    ExcerptClass,
    Lineage,
    Version,
    norm,
    pair,
    place,
)
from axiomation.questions.edge_sheet import norm as sheet_norm


def body(*paragraphs: str) -> str:
    """A LaTeX paper whose body is `paragraphs`, a blank line between."""
    return (
        "\\documentclass{article}\n\\begin{document}\n\n"
        + "\n\n".join(paragraphs)
        + "\n\n\\end{document}\n"
    )


A = "Alpha paragraph opens the paper with its setting and aims."
B = "Bravo paragraph states the model and fixes the notation used."
C = "Charlie paragraph recalls the known results that we build on."
D = "Delta paragraph closes the section with an outlook on the rest."


def holding(version: Version, fragment: str) -> str:
    (found,) = [
        b.id
        for b in version.cut.blocks
        if fragment in norm(" ".join(version.document.text(s) for s in b.owned))
    ]
    return found


def setup(version, old_text: str, new_text: str) -> tuple[Version, Version, Lineage]:
    old, new = version(old_text), version(new_text)
    return old, new, pair(old, new)


def test_an_excerpt_once_in_its_successor_is_found(version):
    old, new, lineage = setup(
        version, body(A, B, C), body(A, B.replace("fixes", "sets"), C)
    )
    found = place("states the model", holding(old, "Bravo"), lineage, new)
    assert found.cls is ExcerptClass.FOUND
    assert found.block == holding(new, "Bravo")
    assert found.hint is None


def test_one_normaliser_on_both_sides(version):
    """The excerpt as the sheet writes it,
    whitespace collapsed, is found in a block whose source breaks the
    line and doubles a space inside its maths."""
    spaced = "Let $x  +\n  y$ be the sum we study here."
    old, new, lineage = setup(version, body(A, spaced, C), body(A, spaced, D))
    excerpt = sheet_norm("Let $x + y$ be the sum")
    assert norm(spaced) == sheet_norm(spaced)
    found = place(excerpt, holding(old, "the sum"), lineage, new)
    assert found.cls is ExcerptClass.FOUND


def test_an_excerpt_twice_is_ambiguous_and_nothing_is_guessed(version):
    twice = "We bound the error. Later we bound the error again."
    old, new, lineage = setup(
        version,
        body(A, "We bound the error here.", C),
        body(A, twice, C),
    )
    found = place("bound the error", holding(old, "here"), lineage, new)
    assert found.cls is ExcerptClass.AMBIGUOUS
    assert found.block == holding(new, "again")  # demoted, not placed


def test_an_excerpt_moved_to_a_neighbour(version):
    moved = "This sentence moves on."
    old, new, lineage = setup(
        version,
        body(A, f"{B} {moved}", C),
        body(A, B, f"{moved} {C}"),
    )
    found = place(moved, holding(old, "Bravo"), lineage, new)
    assert found.cls is ExcerptClass.MOVED
    assert found.block == holding(new, "Charlie")


def test_a_vanished_excerpt_is_removed_and_demoted_with_a_hint_beside(version):
    """A vanished excerpt is an
    orphan demoted to the block it stood on, never re-anchored by its
    fuzzy hint, which is returned beside it."""
    old, new, lineage = setup(
        version,
        body(A, "The estimator is consistent under mild conditions.", C),
        body(A, "The estimator is consistent under weak conditions.", C),
    )
    found = place(
        "consistent under mild conditions", holding(old, "estimator"), lineage, new
    )
    assert found.cls is ExcerptClass.REMOVED
    assert found.block == holding(new, "estimator")
    assert found.hint is not None
    assert found.hint.words == "consistent under weak conditions"
    assert found.hint.block == found.block


def test_a_tie_between_two_fuzzy_matches_gives_no_hint(version):
    """Two matches at one distance, so no
    hint, never the first in the scan."""
    old, new, lineage = setup(
        version,
        body(A, "We use the red key here.", C),
        body(A, "We use the rod key here, then the rad key there.", C),
    )
    found = place("the red key", holding(old, "red key"), lineage, new)
    assert found.cls is ExcerptClass.REMOVED and found.hint is None


def test_a_distant_match_gives_no_hint(version):
    old, new, lineage = setup(
        version,
        body(A, "A sentence about convergence in measure.", C),
        body(A, "A sentence about something else entirely now.", C),
    )
    found = place("convergence in measure", holding(old, "A sentence"), lineage, new)
    assert found.cls is ExcerptClass.REMOVED and found.hint is None


def test_an_excerpt_of_a_deleted_block_is_looked_for_by_its_neighbours(version):
    old, new, lineage = setup(
        version,
        body(A, "A remark that goes away.", C),
        body(A, f"{C} A remark that goes away."),
    )
    # the remark's block is deleted, its words now end Charlie's paragraph
    remark = holding(old, "A remark")
    assert lineage.new_of(remark) == ()
    found = place("A remark that goes away.", remark, lineage, new)
    assert found.cls is ExcerptClass.MOVED


def test_a_merge_demotes_to_its_new_block_and_a_split_to_its_heir(version):
    """A merge's heir names an OLD block: the excerpt is demoted to the NEW
    one."""
    first = "The first half of the paragraph says one short thing."
    second = "The second half adds a much longer remark with many more words in it."
    old, new, lineage = setup(
        version, body(A, first, second, C), body(A, f"{first} {second}", C)
    )
    merged = lineage.link_of_old(holding(old, "first half"))
    assert merged.heir in merged.old
    found = place("words that are gone", holding(old, "first half"), lineage, new)
    assert found.cls is ExcerptClass.REMOVED
    assert found.block == holding(new, "first half")
    old, new, lineage = setup(
        version, body(A, f"{first} {second}", C), body(A, first, second, C)
    )
    found = place("words that are gone", holding(old, "first half"), lineage, new)
    assert found.block == holding(new, "first half")  # the heir: its first words


def test_once_in_the_successor_is_found_though_a_neighbour_holds_it_too(version):
    old, new, lineage = setup(
        version,
        body(A, "We note the key fact here.", "Other words."),
        body(A, "We note the key fact here!", "And the key fact again."),
    )
    found = place("the key fact", holding(old, "We note"), lineage, new)
    assert found.cls is ExcerptClass.FOUND
    assert found.block == holding(new, "We note")


def test_a_hint_needs_its_margin_not_only_a_win(version):
    """Two different matches, the best one character closer: under the
    margin, so no hint, though it is no tie."""
    old, new, lineage = setup(
        version,
        body(A, "We use the large red key here today.", C),
        body(A, "We use the large rod key here, the large bad key too.", C),
    )
    found = place("the large red key", holding(old, "We use"), lineage, new)
    assert found.cls is ExcerptClass.REMOVED and found.hint is None


def test_a_reworded_excerpt_moved_into_a_neighbour_is_hinted_there(version):
    old, new, lineage = setup(
        version,
        body(A, f"{B} The estimate is tight in every case.", C),
        body(A, B, f"The estimate is sharp in every case. {C}"),
    )
    found = place(
        "The estimate is tight in every case.", holding(old, "Bravo"), lineage, new
    )
    assert found.cls is ExcerptClass.REMOVED
    assert found.block == holding(new, "Bravo")  # demoted to the paired block
    assert found.hint is not None and found.hint.block == holding(new, "Charlie")


def test_an_empty_excerpt_or_an_unknown_block_is_refused(version):
    import pytest

    old, new, lineage = setup(version, body(A, B), body(A, B))
    with pytest.raises(ValueError, match="empty excerpt"):
        place("  ", holding(old, "Bravo"), lineage, new)
    with pytest.raises(ValueError, match="no block"):
        place("Bravo", "paper.tex:99:1", lineage, new)
