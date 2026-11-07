"""Coverage: every rendered character owned exactly once.

On every synthetic paper in the suite, both lists are empty: the `paper`
fixture checks each cut it builds. Here the check itself is checked, on a
planted gap and a planted double ownership.
"""

from __future__ import annotations

from dataclasses import replace

from axiomation.cutter import Form
from axiomation.cutter.coverage import coverage

TEXT = "\\begin{document}\nOne.\n\nTwo words.\n% hidden\n\\end{document}\n"


def test_a_complete_cut_is_complete(paper) -> None:
    cutting = paper(TEXT)

    found = coverage(cutting.document, cutting.cut)
    assert found.complete
    assert (found.unowned, found.owned_twice) == ((), ())


def test_a_planted_gap_is_found(paper) -> None:
    cutting = paper(TEXT)
    one, two = cutting.blocks(Form.PROSE)
    gapped = replace(cutting.cut, blocks=(one,))

    (gap,) = coverage(cutting.document, gapped).unowned
    assert cutting.document.text(gap) == "Two words."


def test_a_planted_double_ownership_is_found(paper) -> None:
    cutting = paper(TEXT)
    one, two = cutting.blocks(Form.PROSE)
    doubled = replace(cutting.cut, blocks=(one, two, replace(two, id="copy")))

    found = coverage(cutting.document, doubled)
    assert found.unowned == ()
    (twice,) = found.owned_twice
    assert cutting.document.text(twice) == "Two words."


def test_hidden_text_needs_no_owner(paper) -> None:
    cutting = paper(TEXT)

    assert all("hidden" not in cutting.text(block) for block in cutting.blocks())
    assert coverage(cutting.document, cutting.cut).complete
