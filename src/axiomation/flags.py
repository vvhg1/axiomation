"""The flags a build raised: the stages' positions on the earlier stages'
work, from the store, and the rules', computed.

A stage's flag is stored (`records.Flag`, from its `?` lines). A rule's is
computed from a build's records whenever it is needed and never stored:
a group of exactly two blocks, every written `qualifies`,
and a definition block that uses a whole claim block. On the keys each
fires on correct structure too, so a rule's flag is a position, never a
refusal.

The vocabulary map's two (`vocabulary_flags`) are computed on the final
cut, listed after the others and shown to no session: they measure
whether the map's two rounds hold, and ask nothing again.
"""

from __future__ import annotations

from dataclasses import dataclass

from axiomation.cutter.model import ROLE_OF_MEANING, Block, Cut, Form, Group, Role
from axiomation.reader import Document, EnvKind, Span, VocabularyKind
from axiomation.records import EdgeKind, EdgeRecords, Flag, Signal, TargetKind

#: A rule's flag's `by`, and a stage's.
BY_RULE = "rule"
BY_AGENT = "agent"
#: The stage the vocabulary map's flags are raised for.
VOCABULARY_STAGE = "vocabulary"
#: The roles a statement's environment gives.
_STATEMENT_ROLES = frozenset(ROLE_OF_MEANING.values())
#: The uses a mapped reference's flag quotes.
_REFERENCES_QUOTED = 3


@dataclass(frozen=True, slots=True, eq=False)
class Raised:
    """A flag as a build raised it: the stage that raised it (a rule's: the
    stage whose records it reads), and the cut it was raised on, where its
    blocks and words stand."""

    flag: Flag
    stage: str
    cut: Cut

    @property
    def by(self) -> str:
        return BY_RULE if self.flag.answer is None else BY_AGENT


def rule_flags(
    cut: Cut,
    edges: EdgeRecords = EdgeRecords(),  # noqa: B008 - frozen, empty
    edges_cut: Cut | None = None,
) -> tuple[Raised, ...]:
    """The rules' flags: each group of `cut` with exactly two top-level
    blocks (a footnote inside one is not a block it could be joined to), and,
    where the edges were answered on `edges_cut`, each written `qualifies`
    and each `uses` with no part from a block whose roles include a
    definition to a block whose roles are a claim alone."""
    found = [
        Raised(
            Flag(
                id=f"rule-{Signal.TWO_BLOCK_GROUP.value}-{n}",
                answer=None,
                blocks=group.blocks,
                signal=Signal.TWO_BLOCK_GROUP,
                evidence=(),
                note="a group of exactly two blocks: one verdict, or one block",
            ),
            "groups",
            cut,
        )
        for n, group in enumerate((g for g in cut.groups if _two_blocks(cut, g)), 1)
    ]
    if edges_cut is None:
        return tuple(found)
    roles = {block.id: block.roles for block in edges_cut.blocks}
    qualifies = [e for e in edges.edges if e.kind is EdgeKind.QUALIFIES]
    found += [
        Raised(
            Flag(
                id=f"rule-{Signal.QUALIFIES.value}-{n}",
                answer=None,
                blocks=(e.source, e.target.name),
                signal=Signal.QUALIFIES,
                evidence=e.evidence,
                note="a qualifies edge: does this assumption constrain that "
                "definition?",
            ),
            "edges",
            edges_cut,
        )
        for n, e in enumerate(qualifies, 1)
    ]
    uses = [
        e
        for e in edges.edges
        if e.kind is EdgeKind.USES
        and e.target.kind is TargetKind.BLOCK
        and not e.parts
        and Role.DEFINITION in roles.get(e.source, ())
        and roles.get(e.target.name) == (Role.CLAIM,)
    ]
    found += [
        Raised(
            Flag(
                id=f"rule-{Signal.DEFINITION_USES_CLAIM.value}-{n}",
                answer=None,
                blocks=(e.source, e.target.name),
                signal=Signal.DEFINITION_USES_CLAIM,
                evidence=e.evidence,
                note="a definition block uses a whole claim block",
            ),
            "edges",
            edges_cut,
        )
        for n, e in enumerate(uses, 1)
    ]
    return tuple(found)


def _two_blocks(cut: Cut, group: Group) -> bool:
    """Whether a group holds exactly two blocks at the top level: the two
    that could be one verdict, or one block."""
    return sum(cut.block(b).within is None for b in group.blocks) == 2


def vocabulary_flags(document: Document, cut: Cut) -> tuple[Raised, ...]:
    """The map's flags on a final cut: each environment the map reads as a
    container or as nothing whose own blocks (a statement environment
    inside it aside), every one, took a statement's role (a statement
    kind would keep each box whole), and each command
    the map reads as a reference none of whose targets is a label."""
    # a name declared more than once has an entry for each declaration:
    # its uses are those read so, each under the declaration in force
    answered: dict[str, list[VocabularyKind]] = {}
    for entry in document.vocabulary.environments:
        if entry.kind in (VocabularyKind.CONTAINER, VocabularyKind.NONE):
            answered.setdefault(entry.name, []).append(entry.kind)
    read_as = {
        VocabularyKind.CONTAINER: EnvKind.CONTAINER,
        VocabularyKind.NONE: EnvKind.OTHER,
    }
    found: list[Raised] = []
    for name, kinds in answered.items():
        spans = [
            env.span
            for env in document.environments
            if env.name == name and env.kind in {read_as[k] for k in kinds}
        ]
        kind = " or ".join(dict.fromkeys(k.value for k in kinds))
        # a statement environment inside the box has its role from its
        # name: only the box's own text says whether the box is one
        blocks = [
            block
            for block in cut.blocks
            if block.within is None
            and block.form is not Form.STATEMENT
            and any(_inside(block.extent, s) for s in spans)
        ]
        if blocks and all(_STATEMENT_ROLES & set(block.roles) for block in blocks):
            found.append(
                _vocabulary_flag(
                    Signal.CONTAINER_STATEMENTS,
                    len(found) + 1,
                    tuple(block.id for block in blocks),
                    (),
                    f"the map reads `{name}` as {kind}, and every block "
                    "in it took a statement's role: is each one statement?",
                    cut,
                )
            )
    labels = {label.name for label in document.labels}
    for entry in {e.command: e for e in document.vocabulary.references}.values():
        uses = [r for r in document.references if r.command == entry.command]
        if not uses or any(t in labels for r in uses for t in r.targets):
            continue
        quoted = uses[:_REFERENCES_QUOTED]
        holders = [_holder(cut, r.at) for r in quoted]
        found.append(
            _vocabulary_flag(
                Signal.MAPPED_REFERENCE_DANGLING,
                sum(f.flag.signal is Signal.MAPPED_REFERENCE_DANGLING for f in found)
                + 1,
                tuple(dict.fromkeys(b.id for b in holders if b is not None)),
                tuple(document.text(r.at) for r in quoted),
                f"the map reads `\\{entry.command}` as a reference, and none of "
                "its targets is a label: is it one?",
                cut,
            )
        )
    return tuple(found)


def _vocabulary_flag(
    signal: Signal,
    n: int,
    blocks: tuple[str, ...],
    evidence: tuple[str, ...],
    note: str,
    cut: Cut,
) -> Raised:
    return Raised(
        Flag(
            id=f"rule-{signal.value}-{n}",
            answer=None,
            blocks=blocks,
            signal=signal,
            evidence=evidence,
            note=note,
        ),
        VOCABULARY_STAGE,
        cut,
    )


def _inside(inner: Span, outer: Span) -> bool:
    return (
        inner.file == outer.file
        and outer.start <= inner.start
        and inner.end <= outer.end
    )


def _holder(cut: Cut, at: Span) -> Block | None:
    """The innermost block whose extent holds `at`."""
    holding = [block for block in cut.blocks if _inside(at, block.extent)]
    return min(holding, key=lambda b: b.extent.end - b.extent.start, default=None)
