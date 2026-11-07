"""The cut: what the cutter proposes over a document layer.

The blocks, groups and marked parts that entries are made from, and the
marked edges as candidates. Nothing here is a verdict: a block
boundary the document does not decide is proposed and doubted, never chosen.

Every tuple on `Cut` is in reading order. Ids are positions within one cut
and claim nothing across versions.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum

from axiomation.reader import Span


class DisplayRule(StrEnum):
    """What a display outside a statement is."""

    #: A display is its own block.
    OWN_BLOCK = "own-block"
    #: A display with no blank line on a side joins the prose on that side.
    IN_PARAGRAPH = "in-paragraph"


@dataclass(frozen=True, slots=True)
class CutRules:
    displays: DisplayRule = DisplayRule.OWN_BLOCK


class Form(StrEnum):
    TITLE = "title"
    HEADING = "heading"
    PROSE = "prose"
    DISPLAY = "display"
    ITEM = "item"
    STATEMENT = "statement"
    FLOAT = "float"
    FOOTNOTE = "footnote"
    CODE = "code"
    ENVIRONMENT = "environment"


class Role(StrEnum):
    """The five intrinsic roles."""

    DEFINITION = "definition"
    ASSUMPTION = "assumption"
    CLAIM = "claim"
    JUSTIFICATION = "justification"
    MOTIVATION = "motivation"


#: The standard environments whose name says their role. A remark, an
#: example and a declared name without a meaning carry none.
ROLE_OF_MEANING: dict[str, Role] = {
    "definition": Role.DEFINITION,
    "assumption": Role.ASSUMPTION,
    "condition": Role.ASSUMPTION,
    "theorem": Role.CLAIM,
    "lemma": Role.CLAIM,
    "proposition": Role.CLAIM,
    "corollary": Role.CLAIM,
    "fact": Role.CLAIM,
    "conjecture": Role.CLAIM,
}


@dataclass(frozen=True, slots=True)
class Segment:
    """One segment of a block `starts` is
    a verbatim excerpt unique in the block where the role changes, empty for
    the block's first segment. `role` is `None` for a span that plays none,
    such as a transition inside a block."""

    starts: str
    role: Role | None


def role_set(roles: Iterable[Role]) -> tuple[Role, ...]:
    """Roles as a block carries them: each once, in the enum's order, so the
    same set always has the same payload and key."""
    held = set(roles)
    return tuple(role for role in Role if role in held)


def roles_problem(roles: tuple[Role, ...], segments: tuple[Segment, ...]) -> str | None:
    """What is wrong with a block's roles, or None. The roles are a set in
    the enum's order; segments, when placed, cover exactly the roles (a
    segment that plays none adds none), start with the block (`starts`
    empty) and then at distinct excerpts. A mixed
    block may have none placed yet. Whether an excerpt is
    unique in the block's text is checked where the text is."""
    if roles != role_set(roles):
        return f"roles {[r.value for r in roles]} are not a set in canonical order"
    if not roles and segments:
        return "segments on a block with no role"
    if not segments:
        return None if len(roles) != 1 else "a single-role block is one segment"
    if {s.role for s in segments if s.role is not None} != set(roles):
        return "the segments' roles are not the block's roles"
    if segments[0].starts or any(not s.starts for s in segments[1:]):
        return "only the first segment starts with the block"
    if len({s.starts for s in segments}) != len(segments):
        return "two segments start at the same excerpt"
    return None


def segments_of(roles: tuple[Role, ...]) -> tuple[Segment, ...]:
    """A block's segments from its roles alone: one, the whole block, when
    there is a single role; none when several are known but no
    boundary is placed yet."""
    if len(roles) != 1:
        return ()
    return (Segment(starts="", role=roles[0]),)


class Doubt(StrEnum):
    """Why a block's boundary is proposed rather than decided."""

    #: Prose that follows a display with no paragraph break.
    DISPLAY_IN_SENTENCE = "display-in-sentence"
    #: The block begins on a line that holds rendered text of the block
    #: before it (`\\end{theorem} Next …`), footnotes excepted.
    MID_LINE_BOUNDARY = "mid-line-boundary"
    #: An environment the reader could not class.
    UNKNOWN_ENVIRONMENT = "unknown-environment"
    #: An environment nothing closes.
    UNCLOSED_ENVIRONMENT = "unclosed-environment"


@dataclass(frozen=True, slots=True)
class Block:
    """One block: `extent` is where it stands, `owned` the characters that
    are its own, which is the extent less its nested blocks and less hidden
    text, each run trimmed of whitespace. `within` is the block a nested
    one (a footnote) sits in."""

    id: str
    extent: Span
    owned: tuple[Span, ...]
    within: str | None
    form: Form
    environment: str | None
    meaning: str | None
    roles: tuple[Role, ...]
    segments: tuple[Segment, ...]
    hash: str
    doubts: tuple[Doubt, ...]

    def __post_init__(self) -> None:
        problem = roles_problem(self.roles, self.segments)
        if problem is not None:
            raise ValueError(f"block {self.id}: {problem}")


class PartKind(StrEnum):
    ITEM = "item"
    LABELLED_DISPLAY = "labelled-display"
    HEADED = "headed"


@dataclass(frozen=True, slots=True)
class MarkedPart:
    """A part the text marks, which an entry can be anchored to.
    `block` is the block it lies in."""

    block: str
    kind: PartKind
    marker: str | None
    ordinal: int | None
    label: str | None
    at: Span


class GroupKind(StrEnum):
    PROOF = "proof"
    PROOF_SECTION = "proof-section"
    #: Proposed only by a correction; the cutter never emits
    #: these three.
    DERIVATION = "derivation"
    STEP_RUN = "step-run"
    STEP = "step"


class Naming(StrEnum):
    """How a group came to name the result it names."""

    TITLE = "title"
    POSITION = "position"
    HEADING = "heading"


@dataclass(frozen=True, slots=True)
class Group:
    """A proposed run of blocks judged as one. `names` is the
    block of the result it names; `named_label` the label it named it by,
    kept when that label holds no statement."""

    id: str
    kind: GroupKind
    blocks: tuple[str, ...]
    names: str | None
    named_label: str | None
    named_by: Naming | None
    role: Role | None


class CandidateKind(StrEnum):
    REFERENCE = "reference"
    CITATION = "citation"


@dataclass(frozen=True, slots=True)
class EdgeCandidate:
    """A marked edge: where a `\\ref` or a `\\cite` points, never what the
    reliance is. `source` is the block that owns the command,
    `None` only when hidden text holds it. `resolved` is the block that
    holds the label; a citation's is always `None`."""

    source: str | None
    kind: CandidateKind
    command: str
    target: str
    at: Span
    resolved: str | None


@dataclass(frozen=True, slots=True)
class GivenHandles:
    """The handles a cut's sheet shows where they are given, not minted
    from its blocks' words (`docs/model.md` § 7): by block or group id,
    and the names `reserved` for nodes not on the cut, which no handle
    minted on it takes."""

    by_id: Mapping[str, str]
    reserved: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class Cut:
    rules: CutRules
    blocks: tuple[Block, ...]
    parts: tuple[MarkedPart, ...]
    groups: tuple[Group, ...]
    candidates: tuple[EdgeCandidate, ...]
    #: The handles its sheet names its blocks by, given where the cut
    #: begins (a build's by its words, an update's and a kept state's by
    #: their nodes' kept handles) and carried through every correction;
    #: never the cutter's.
    handles: GivenHandles | None = None

    def block(self, block_id: str) -> Block:
        for block in self.blocks:
            if block.id == block_id:
                return block
        raise KeyError(block_id)
