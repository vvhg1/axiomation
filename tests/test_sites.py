"""The sites the verifiers check at a kept state: each quotation, and each
citation with a locator, per cited work, placed on the block whose owned text holds it;
one whose words stand more than once in its block is unplaced, with why.

A synthetic one-line paper read by the reader itself, so the kept state's
version is its text's. No sources, no model.
"""

from __future__ import annotations

import pytest

from axiomation.exchange import document_payload
from axiomation.reader import document_hash, read_paper
from axiomation.records import RecordStore
from axiomation.sites import SiteKind, sites
from test_judgments import graph
from test_versions import keep

PRINTED = "ratio of the output to the input of each stage"
BLOCKS = [
    ("d", "Let f be a gain."),
    (
        "q",
        f'They define the "{PRINTED}" of the chain \\autocite[p.~3]{{knuth1997}}.',
    ),
    (
        "c",
        'A second reading says the "ratio of the output to the input '
        'for each stage" \\autocite[p.~3]{knuth1997}.',
    ),
    ("g", "The general case is \\autocite[Theorem~9.9.9]{knuth1997}."),
    ("t", "The theorem is \\autocite[Theorem~4.1.2]{knuth1997}."),
]


def kept(tmp_path, blocks=BLOCKS):
    """The paper kept as one state, its version its text's, the document
    half the reader's (its citations and quotations)."""
    text = " ".join(words for _, words in blocks)
    root = tmp_path / "paper"
    root.mkdir()
    (root / "p.md").write_text(text, encoding="utf-8")
    document = read_paper(root)
    payload = graph(text, blocks, [], [])
    payload["document"] = document_payload(document)["document"]
    store = RecordStore(tmp_path / "store")
    return store, keep(store, document_hash(document), text, payload)


def test_the_sites_are_the_state_s_quotations_and_citations(tmp_path) -> None:
    store, state = kept(tmp_path)
    placed, unplaced = sites(store, state)
    assert [(s.block, s.key) for s in placed if s.kind is SiteKind.QUOTATION] == [
        ("q", "knuth1997"),
        ("c", "knuth1997"),
    ]
    assert [(s.block, s.locator) for s in placed if s.kind is SiteKind.LOCATOR] == [
        ("q", "p.~3"),
        ("c", "p.~3"),
        ("g", "Theorem~9.9.9"),
        ("t", "Theorem~4.1.2"),
    ]
    assert unplaced == []
    assert sites(store, state, key="other2001") == ([], [])


@pytest.mark.parametrize("key", [None, "knuth1997"])
def test_a_site_whose_words_stand_twice_is_unplaced(tmp_path, key) -> None:
    twice = "The theorem is \\autocite[Theorem~4.1.2]{knuth1997}."
    store, state = kept(
        tmp_path, [("d", "Let f be a gain."), ("t", f"{twice} {twice}")]
    )
    placed, unplaced = sites(store, state, key)
    assert placed == []
    assert [(u.kind, u.locator) for u in unplaced] == [
        (SiteKind.LOCATOR, "Theorem~4.1.2"),
        (SiteKind.LOCATOR, "Theorem~4.1.2"),
    ]
    assert unplaced[0].why == "its words stand 2 times in t, not once"
