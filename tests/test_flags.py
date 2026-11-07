"""The rules' flags, computed from a build's records and never stored, and a
flag as the export places it."""

from __future__ import annotations

from dataclasses import replace

import pytest

from axiomation.cutter import cut
from axiomation.cutter.model import Group, GroupKind, Role, segments_of
from axiomation.exchange import flag_payload
from axiomation.flags import (
    BY_AGENT,
    BY_RULE,
    VOCABULARY_STAGE,
    Raised,
    rule_flags,
    vocabulary_flags,
)
from axiomation.reader import (
    EnvEntry,
    RefEntry,
    Vocabulary,
    VocabularyKind,
    read_paper,
)
from axiomation.records import (
    Edge,
    EdgeKind,
    EdgeRecords,
    EdgeTarget,
    Flag,
    Signal,
    TargetKind,
)
from questions.review_paper import make_paper
from questions.test_roles_file import first_pass

SETTING, ASSUMPTION, DEFINITION = "main.tex:3:1", "main.tex:4:1", "main.tex:7:1"
THEOREM, PROOF, CLOSING = "main.tex:12:1", "main.tex:15:1", "main.tex:19:1"


@pytest.fixture
def roled(tmp_path):
    """The synthetic paper and its cut after a roles pass: the setting
    plays none, the assumption, the definition, the theorem a claim, the
    proof a claim and a justification, its last block a claim; one group,
    the proof's three blocks, a footnote inside the first."""
    paper = make_paper(tmp_path / "paper")
    return paper, first_pass(paper)


def edge(source: str, kind: EdgeKind, target: str, *parts: str) -> Edge:
    return Edge(
        id=f"{source}>{target}",
        answer="e1",
        source=source,
        kind=kind,
        target=EdgeTarget(TargetKind.BLOCK, target),
        evidence=("words",),
        parts=parts,
    )


def test_a_group_of_two_top_level_blocks_is_flagged_and_no_other(roled) -> None:
    """The proof's group holds two blocks and the footnote inside the first:
    flagged. A block with its own footnote is one block at the top level
    (a group a deciding step may try to join), and three are three: not
    flagged."""
    paper, cut = roled
    footnote = next(b.id for b in cut.blocks if b.within == PROOF)
    pair = Group(
        "g2", GroupKind.DERIVATION, (SETTING, ASSUMPTION), None, None, None, None
    )
    nested = Group("g3", GroupKind.PROOF, (PROOF, footnote), None, None, None, None)
    three = Group(
        "g4",
        GroupKind.DERIVATION,
        (SETTING, ASSUMPTION, DEFINITION),
        None,
        None,
        None,
        None,
    )
    cut = replace(cut, groups=(*cut.groups, pair, nested, three))

    found = rule_flags(cut)

    assert [r.flag.blocks for r in found] == [cut.groups[0].blocks, pair.blocks]
    raised = found[1]
    assert raised.flag.signal is Signal.TWO_BLOCK_GROUP
    assert raised.flag.answer is None and raised.flag.evidence == ()
    assert (raised.stage, raised.by, raised.cut) == ("groups", BY_RULE, cut)


def test_every_qualifies_and_a_definition_using_a_whole_claim_are_flagged(
    roled,
) -> None:
    _, cut = roled
    edges = EdgeRecords(
        edges=(
            edge(ASSUMPTION, EdgeKind.QUALIFIES, DEFINITION),
            edge(DEFINITION, EdgeKind.USES, THEOREM),
            # Not flagged: a part named, a target that is not a claim alone,
            # a source with no definition, an about edge.
            edge(DEFINITION, EdgeKind.USES, THEOREM, "bound"),
            edge(DEFINITION, EdgeKind.USES, PROOF),
            edge(CLOSING, EdgeKind.USES, THEOREM),
            edge(DEFINITION, EdgeKind.ABOUT, THEOREM),
        )
    )

    found = rule_flags(cut, edges, cut)[1:]  # after the proof's group

    assert [(r.flag.signal, r.flag.blocks) for r in found] == [
        (Signal.QUALIFIES, (ASSUMPTION, DEFINITION)),
        (Signal.DEFINITION_USES_CLAIM, (DEFINITION, THEOREM)),
    ]
    assert all(r.stage == "edges" and r.flag.evidence == ("words",) for r in found)
    assert [r.flag.id for r in found] == [
        "rule-qualifies-1",
        "rule-definition-uses-claim-1",
    ]


def test_without_an_edges_answer_only_the_groups_rule_runs(roled) -> None:
    _, cut = roled
    edges = EdgeRecords(edges=(edge(ASSUMPTION, EdgeKind.QUALIFIES, DEFINITION),))

    assert [r.flag.signal for r in rule_flags(cut, edges)] == [Signal.TWO_BLOCK_GROUP]


def test_the_export_places_a_flag_s_words_in_its_blocks(roled) -> None:
    paper, cut = roled
    flag = Flag(
        "f1", "k1", (DEFINITION,), Signal.SPLIT, ("ratio of output",), "two units"
    )

    found = flag_payload(
        paper.document, Raised(flag, "roles", cut), {DEFINITION: "def:gain"}
    )

    assert found == {
        "id": "f1",
        "stage": "roles",
        "by": BY_AGENT,
        "blocks": [
            {
                "id": DEFINITION,
                "handle": "def:gain",
                "owned": [
                    {"file": "main.tex", "start": [7, 1], "end": [7, 19]},
                    {"file": "main.tex", "start": [8, 1], "end": [9, 17]},
                ],
                "roles": ["definition"],
            }
        ],
        "signal": "split",
        "evidence": [
            {
                "text": "ratio of output",
                "at": {"file": "main.tex", "start": [8, 17], "end": [8, 32]},
            }
        ],
        "note": "two units",
        "decided": None,
    }


def test_a_flag_s_words_that_do_not_place_leave_at_null(roled) -> None:
    paper, cut = roled
    flag = Flag("f1", "k1", (SETTING,), Signal.OTHER, ("ratio of output",), "x")

    found = flag_payload(paper.document, Raised(flag, "roles", cut), {})

    assert found["evidence"] == [{"text": "ratio of output", "at": None}]
    assert [(b["id"], b["handle"], b["roles"]) for b in found["blocks"]] == [
        (SETTING, None, [])
    ]


# --- the vocabulary map's flags ---


def _mapped_paper(tmp_path, body: str, vocabulary: Vocabulary | None):
    """A Markdown paper read with `vocabulary`, and its cut."""
    paper = tmp_path / "mapped"
    paper.mkdir()
    (paper / "paper.md").write_text(body, encoding="utf-8")
    document = read_paper(paper, vocabulary=vocabulary)
    return document, cut(document)


def test_a_mapped_frame_whose_own_blocks_are_statements_is_flagged(tmp_path) -> None:
    vocabulary = Vocabulary(
        environments=(
            EnvEntry("lawbox", VocabularyKind.CONTAINER),
            EnvEntry("aside", VocabularyKind.CONTAINER),
            EnvEntry("thmbox", VocabularyKind.CONTAINER),
        )
    )
    document, found = _mapped_paper(
        tmp_path,
        "\\begin{lawbox}\nWeight is kept.\n\nA law is a rule.\n\\end{lawbox}\n\n"
        "\\begin{aside}\nA claim.\n\nA comment.\n\\end{aside}\n\n"
        "\\begin{thmbox}\n\\begin{theorem}\nA.\n\\end{theorem}\n\\end{thmbox}\n",
        vocabulary,
    )
    # the roles step's answer: the law box's two paragraphs a claim and a
    # definition, the aside's first a claim and its second nothing
    given = {
        "Weight is kept.": (Role.CLAIM,),
        "A law is a rule.": (Role.DEFINITION,),
        "A claim.": (Role.CLAIM,),
    }

    def roled(block):
        text = document.text(block.extent)
        for words, roles in given.items():
            if words in text:
                return replace(block, roles=roles, segments=segments_of(roles))
        return block

    roled_cut = replace(found, blocks=tuple(roled(b) for b in found.blocks))

    (raised,) = vocabulary_flags(document, roled_cut)

    assert (raised.stage, raised.by, raised.flag.signal) == (
        VOCABULARY_STAGE,
        BY_RULE,
        Signal.CONTAINER_STATEMENTS,
    )
    assert raised.flag.id == "rule-container-statements-1"
    held = [document.text(roled_cut.block(b).extent) for b in raised.flag.blocks]
    assert len(held) == 2
    assert "Weight is kept." in held[0] and "A law is a rule." in held[1]
    assert "`lawbox`" in raised.flag.note


def test_a_mapped_reference_whose_targets_never_resolve_is_flagged(tmp_path) -> None:

    vocabulary = Vocabulary(
        references=(RefEntry("lemref", prefix="lem:"), RefEntry("thmref"))
    )
    document, found = _mapped_paper(
        tmp_path,
        "\\begin{lemma}\\label{lem:key}\nA.\n\\end{lemma}\n\n"
        "By \\lemref{key} and \\thmref{nowhere}, it holds.\n",
        vocabulary,
    )

    (raised,) = vocabulary_flags(document, found)

    assert raised.flag.signal is Signal.MAPPED_REFERENCE_DANGLING
    assert raised.flag.evidence == ("\\thmref{nowhere}",)
    (block,) = raised.flag.blocks
    assert "By \\lemref{key}" in document.text(found.block(block).extent)


def test_a_static_frame_raises_no_vocabulary_flag(tmp_path) -> None:

    document, found = _mapped_paper(
        tmp_path,
        "\\begin{mdframed}\n\\begin{theorem}\nA.\n\\end{theorem}\n\\end{mdframed}\n",
        None,
    )

    assert vocabulary_flags(document, found) == ()
