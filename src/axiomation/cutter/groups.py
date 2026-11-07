"""Proposed groups: runs of blocks the text marks as one.

A label's holder is the innermost block whose extent holds it, a section's
label its heading's block; where a label is defined twice, the first in
reading order holds it. A titled proof in another file names its result,
since the paper is read whole.

- **Proof:** every proof environment over two blocks or more. It names a
  result by its title's first reference, else by standing right after a
  provable statement, or right after a command that restates one (a
  `restatable`'s, which names the label its statement defines).
- **Proof section:** a section whose title says it is a proof
  (`PROOF_HEADING_RE`) and holds a reference; everything after its heading to
  the section's end.

Step headings are marked parts, not groups: a run of steps is
judged as a whole only where the proof or section that holds it already is.
A proposal of one block is dropped: a group holds two or more, and a
one-block proof is a block whose role is justification.
"""

from __future__ import annotations

from collections.abc import Sequence

from axiomation.cutter.blocks import BlockIndex
from axiomation.cutter.model import (
    Form,
    Group,
    GroupKind,
    Naming,
    Role,
)
from axiomation.cutter.text import views
from axiomation.reader import Document, Environment, EnvKind, Label, Span
from axiomation.reader.formats.md_latex import (
    PROVABLE_ENVS,
    RESTATABLE_ENVS,
    MdLatexFormat,
)
from axiomation.reader.layer import PROOF_HEADING_RE


def label_holders(document: Document, index: BlockIndex) -> dict[str, str | None]:
    """Each label's holding block, the first definition in reading order
    winning. A label no block holds maps to `None`."""
    holders: dict[str, str | None] = {}
    for label, holder in definition_holders(document, index):
        holders.setdefault(label.name, holder)
    return holders


def definition_holders(
    document: Document, index: BlockIndex
) -> list[tuple[Label, str | None]]:
    """Each definition of a label and the block holding it, in reading
    order: a section's label its heading's block, any other the innermost
    block whose extent holds it (None: no block)."""
    heading_labels: dict[int, str | None] = {}
    for section in document.sections:
        if section.label is None:
            continue
        for label in document.labels:
            at = label.at
            if (
                label.name == section.label
                and at.file == section.heading.file
                and at.start >= section.heading.start
                and id(label) not in heading_labels
            ):
                heading_labels[id(label)] = index.owner(
                    section.heading.file, section.heading.start
                )
                break
    return [
        (
            label,
            heading_labels[id(label)]
            if id(label) in heading_labels
            else index.holder(label.at),
        )
        for label in document.labels
    ]


def proposed_groups(
    document: Document,
    index: BlockIndex,
    holders: dict[str, str | None],
) -> tuple[Group, ...]:
    groups: list[_Proposal] = []
    proofs = _proofs(document, index, holders)
    groups.extend(proofs)
    groups.extend(_proof_sections(document, index, holders))
    groups = [group for group in groups if len(group.blocks) >= 2]
    groups.sort(key=lambda group: (index.position[group.blocks[0]], -len(group.blocks)))
    return _with_ids(groups)


class _Proposal:
    __slots__ = ("kind", "blocks", "names", "named_label", "named_by", "role")

    def __init__(
        self,
        kind: GroupKind,
        blocks: Sequence[str],
        names: str | None = None,
        named_label: str | None = None,
        named_by: Naming | None = None,
        role: Role | None = None,
    ) -> None:
        self.kind = kind
        self.blocks = tuple(blocks)
        self.names = names
        self.named_label = named_label
        self.named_by = named_by
        self.role = role


def _with_ids(proposals: Sequence[_Proposal]) -> tuple[Group, ...]:
    seen: dict[str, int] = {}
    groups: list[Group] = []
    for proposal in proposals:
        base = f"{proposal.kind.value}@{proposal.blocks[0]}"
        seen[base] = seen.get(base, 0) + 1
        group_id = base if seen[base] == 1 else f"{base}#{seen[base]}"
        groups.append(
            Group(
                id=group_id,
                kind=proposal.kind,
                blocks=proposal.blocks,
                names=proposal.names,
                named_label=proposal.named_label,
                named_by=proposal.named_by,
                role=proposal.role,
            )
        )
    return tuple(groups)


def _statement(index: BlockIndex, block: str | None) -> str | None:
    if block is None or index.by_id[block].form is not Form.STATEMENT:
        return None
    return block


def _first_reference(document: Document, span: Span) -> str | None:
    for reference in document.references:
        at = reference.at
        if at.file == span.file and span.start <= at.start and at.end <= span.end:
            return reference.targets[0] if reference.targets else None
    return None


# --- Proofs ---


def _proofs(
    document: Document, index: BlockIndex, holders: dict[str, str | None]
) -> list[_Proposal]:
    trailing = _trailing_proofs(document)
    restated = _restated_proofs(document)
    found: list[_Proposal] = []
    for environment in document.environments:
        if environment.kind is not EnvKind.PROOF:
            continue
        overlapping = index.overlapping(environment.span)
        if not overlapping:
            continue
        # From its first block to its last in reading order, so a file its
        # body includes is inside it too.
        first = index.position[overlapping[0]]
        last = index.position[overlapping[-1]]
        blocks = [block.id for block in index.blocks[first : last + 1]]
        opening = Span(
            environment.span.file, environment.span.start, environment.body.start
        )
        target = _first_reference(document, opening)
        names = named_label = None
        named_by: Naming | None = None
        if target is not None:
            named_label = target
            names = _statement(index, holders.get(target))
            named_by = Naming.TITLE
        elif environment.span in trailing:
            statement = trailing[environment.span]
            names = _statement(
                index, index.owner(statement.span.file, statement.span.start)
            )
            named_label = _own_label(document, statement)
            named_by = Naming.POSITION
        elif environment.span in restated:
            named_label = restated[environment.span]
            names = _statement(index, holders.get(named_label))
            named_by = Naming.POSITION
        found.append(
            _Proposal(
                GroupKind.PROOF,
                blocks,
                names=names,
                named_label=named_label,
                named_by=named_by,
                role=Role.JUSTIFICATION,
            )
        )
    return found


def _trailing_proofs(document: Document) -> dict[Span, Environment]:
    """Each proof that follows a provable statement with only blank lines
    between, within a fixed gap: its span, and the statement."""
    following = _ProofsAfter(document)
    found: dict[Span, Environment] = {}
    for environment in document.environments:
        if (
            environment.kind is not EnvKind.STATEMENT
            or environment.meaning not in PROVABLE_ENVS
        ):
            continue
        proof = following.after(environment.span.file, environment.span.end)
        if proof is not None:
            found.setdefault(proof.span, environment)
    return found


def _restated_proofs(document: Document) -> dict[Span, str]:
    """Each proof that follows a restating command as a trailing proof
    follows its statement: its span, and the label the command points at,
    the one its `restatable` defines in its opening."""
    handles = {
        label.name
        for label in document.labels
        for environment in document.environments
        if environment.name in RESTATABLE_ENVS
        and label.at.file == environment.span.file
        and environment.span.start <= label.at.start < environment.body.start
    }
    if not handles:
        return {}
    following = _ProofsAfter(document)
    found: dict[Span, str] = {}
    for reference in document.references:
        if reference.command not in handles:
            continue
        proof = following.after(reference.at.file, reference.at.end)
        if proof is not None:
            found.setdefault(proof.span, reference.command)
    return found


class _ProofsAfter:
    """The proof that is the first thing after a place in the paper."""

    def __init__(self, document: Document) -> None:
        self.viewed = views(document)
        self.lines = {
            path: view.rendered.split("\n") for path, view in self.viewed.items()
        }
        self.proofs = {
            (environment.span.file, environment.span.start): environment
            for environment in document.environments
            if environment.kind is EnvKind.PROOF
        }
        self.names = sorted({env.name for env in self.proofs.values()} | {"proof"})

    def after(self, file: str, end: int) -> Environment | None:
        view = self.viewed[file]
        end_line = view.line_of(end)
        end_column = end - view.starts[end_line]
        trailing = MdLatexFormat.find_trailing_proof(
            self.lines[view.path],
            end_line,
            None,
            end_column=end_column,
            proofs=self.names,
        )
        if trailing is None:
            return None
        offset = view.starts[trailing.line] + trailing.column
        return self.proofs.get((view.path, offset))


def _own_label(document: Document, statement: Environment) -> str | None:
    """A statement's own label: the first in it outside the environments it
    holds (an equation's label names the equation)."""
    span = statement.span
    inner = [
        (other.span.start, other.span.end)
        for other in document.environments
        if other.span.file == span.file
        and other is not statement
        and span.start <= other.span.start
        and other.span.end <= span.end
    ]
    for label in document.labels:
        at = label.at
        if at.file != span.file or not (span.start <= at.start and at.end <= span.end):
            continue
        if not any(start <= at.start < end for start, end in inner):
            return label.name
    return None


# --- Proof sections ---


def _proof_sections(
    document: Document, index: BlockIndex, holders: dict[str, str | None]
) -> list[_Proposal]:
    found: list[_Proposal] = []
    sections = document.sections
    for number, section in enumerate(sections):
        if PROOF_HEADING_RE.match(section.title) is None:
            continue
        target = _first_reference(document, section.heading)
        if target is None:
            continue
        heading = index.owner(section.heading.file, section.heading.start)
        if heading is None:
            continue
        end: int | None = None
        for later in sections[number + 1 :]:
            if later.level <= section.level:
                end = _position(
                    index, index.owner(later.heading.file, later.heading.start)
                )
                break
        start = index.position[heading] + 1
        blocks = [block.id for block in index.blocks[start:end]]
        if not blocks:
            continue
        found.append(
            _Proposal(
                GroupKind.PROOF_SECTION,
                blocks,
                names=_statement(index, holders.get(target)),
                named_label=target,
                named_by=Naming.HEADING,
                role=Role.JUSTIFICATION,
            )
        )
    return found


def _position(index: BlockIndex, block: str | None) -> int | None:
    return index.position[block] if block is not None else None
