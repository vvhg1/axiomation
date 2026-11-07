"""Marked edges as candidates.

One per target of every reference and one per key of every citation. Where a
reference points is recorded; whether it is a use, an aside or navigation is
the forward question's. Citations stay unresolved: targets
outside the paper are out of scope.
"""

from __future__ import annotations

from axiomation.cutter.blocks import BlockIndex
from axiomation.cutter.model import CandidateKind, EdgeCandidate
from axiomation.reader import Document, ReadingOrder


def edge_candidates(
    document: Document, index: BlockIndex, holders: dict[str, str | None]
) -> tuple[EdgeCandidate, ...]:
    """Every candidate in the order its command stands, a command's targets
    in the order written."""
    found: list[EdgeCandidate] = []
    for reference in document.references:
        source = index.owner(reference.at.file, reference.at.start)
        found.extend(
            EdgeCandidate(
                source=source,
                kind=CandidateKind.REFERENCE,
                command=reference.command,
                target=target,
                at=reference.at,
                resolved=holders.get(target),
            )
            for target in reference.targets
        )
    for citation in document.citations:
        source = index.owner(citation.at.file, citation.at.start)
        found.extend(
            EdgeCandidate(
                source=source,
                kind=CandidateKind.CITATION,
                command=citation.command,
                target=key,
                at=citation.at,
                resolved=None,
            )
            for key in citation.keys
        )
    order = ReadingOrder.of(document)
    return tuple(sorted(found, key=lambda candidate: order.key(candidate.at)))
