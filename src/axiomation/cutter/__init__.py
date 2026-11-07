"""The cutter: a document layer to its proposed cut.

Deterministic, and no model: blocks, marked parts, proposed groups and
marked edge candidates, over the document layer. It imports nothing
outside the standard library, `axiomation.reader` and this package
(`tests/test_spine.py`).
"""

from __future__ import annotations

from axiomation.cutter.blocks import BlockIndex, cut_blocks
from axiomation.cutter.candidates import edge_candidates
from axiomation.cutter.groups import label_holders, proposed_groups
from axiomation.cutter.model import (
    Block,
    CandidateKind,
    Cut,
    CutRules,
    DisplayRule,
    Doubt,
    EdgeCandidate,
    Form,
    GivenHandles,
    Group,
    GroupKind,
    MarkedPart,
    Naming,
    PartKind,
    Role,
    Segment,
)
from axiomation.cutter.parts import marked_parts
from axiomation.reader import Document


def cut(document: Document, rules: CutRules | None = None) -> Cut:
    """The cut of a document layer under `rules` (the default: own-block)."""
    rules = rules or CutRules()
    blocks = cut_blocks(document, rules)
    index = BlockIndex(blocks)
    parts = marked_parts(document, index)
    holders = label_holders(document, index)
    return Cut(
        rules=rules,
        blocks=blocks,
        parts=parts,
        groups=proposed_groups(document, index, holders),
        candidates=edge_candidates(document, index, holders),
    )


__all__ = [
    "Block",
    "CandidateKind",
    "Cut",
    "CutRules",
    "DisplayRule",
    "Doubt",
    "EdgeCandidate",
    "Form",
    "GivenHandles",
    "Group",
    "GroupKind",
    "MarkedPart",
    "Naming",
    "PartKind",
    "Role",
    "Segment",
    "cut",
]
