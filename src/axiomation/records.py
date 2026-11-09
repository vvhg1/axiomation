"""The record store: answers, cut corrections and the edge stage's
records, and the kept states, append-only.

One file, `record.jsonl`, in a directory the caller names, per paper and
beside it, never inside its files: one entry a line, an attempt's answer
with every record made from it (`RecordKind`), or a kept state with its
lines' change and the texts and blobs it adds (`docs/architecture.md`
§ 4). The index, the
first valid answer per key, is computed on load and never written:
nothing that can be computed is stored.

A cut correction is one of the operations on the cut as the
corrections before it left it, each with its class (`CorrectionClass`).
The assembler applies them (`axiomation.assembler`). Only the resolver
writes a store; nothing here talks to a model.
"""

from __future__ import annotations

import base64
import copy
import fcntl
import hashlib
import heapq
import json
import os
import re
import shutil
import threading
import zlib
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any, NoReturn

import axiomation.keys as keys
import axiomation.run_state as run_state
from axiomation.cutter.model import (
    GroupKind,
    PartKind,
    Role,
    Segment,
    role_set,
    roles_problem,
)
from axiomation.evidence import FileStamp, PageStamp, ReadStatus, RunStamp, RunStatus
from axiomation.kept_change import (
    ChangeError,
    Counted,
    applied_runs,
    counted,
    counted_ids,
    runs,
    text_applied,
    text_change,
)

#: Where a run's sessions' transcripts go, in the store.
TRANSCRIPTS = "transcripts"
#: Where in a store a session's settings lie while it runs: its tool
#: server's, a queue's.
SESSIONS_DIRECTORY = "sessions"


class RecordKind(StrEnum):
    """The records an answer makes, each kind under its name in the
    answer's entry."""

    CUT_CORRECTIONS = "cut-corrections"
    ENTRIES = "entries"
    EDGES = "edges"
    SELF_MARKS = "self-marks"
    STANDINGS = "standings"
    FRAME = "frame"
    #: An update's Δ lines: how an edit changed a block's meaning.
    DELTAS = "deltas"
    #: An owner's lines in its sheet's order, where kinds interleave.
    LINE_ORDERS = "line-orders"
    FLAGS = "flags"
    #: An identity answer's links between two versions' cuts.
    LINEAGE = "lineage"
    #: A vocabulary answer's entries as its check read them.
    VOCABULARY = "vocabulary"
    #: A judge's declined edits, as the next authors' ledger shows them.
    DECLINES = "declines"
    #: Judgments, findings and a finding's answers, beside the graph.
    JUDGMENTS = "judgments"
    FINDINGS = "findings"
    FINDING_ANSWERS = "finding-answers"
    #: A person's withdrawal of an answer to a finding: it speaks no more.
    FINDING_ANSWER_WITHDRAWALS = "finding-answer-withdrawals"
    #: A cited work's DOI and publication.
    WORKS_SEEN = "works-seen"
    #: A person's word that the paper rests on a cited work as it is.
    WORKS_ACCEPTED = "works-accepted"
    #: A run of a paper's script as code ran it.
    RUNS_SEEN = "runs-seen"
    #: A cited work's file as a review last saw it.
    FILES_SEEN = "files-seen"
    #: Rulings: whether the paper owes an argument for a claim it argues
    #: nothing for.
    RULINGS = "rulings"
    #: A person's withdrawal of a ruling: it speaks no more, and is asked
    #: again.
    RULING_WITHDRAWALS = "ruling-withdrawals"
    #: A person's or an agent's reread of a marked owner: its marks clear.
    REREADS = "rereads"
    #: A circle of what claims rest on, read real: every end named, and it
    #: stays.
    CIRCLES = "circles"
    #: A claim's support searched and none found: the paper argues it
    #: nowhere.
    SEARCHES = "searches"
    #: A block entered at a part whose lines are read whole: its parts
    #: cannot be read apart.
    WHOLE_BLOCKS = "whole-blocks"
    #: Suggestions: a work the paper may want to cite at a place; and their
    #: answers, the author's decision.
    SUGGESTIONS = "suggestions"
    SUGGESTION_ANSWERS = "suggestion-answers"
    #: The contribution session's checks: a contribution read against the
    #: literature, what was searched and what was found.
    CONTRIBUTION_CHECKS = "contribution-checks"


#: An identity answer's kind: code's, no model's.
IDENTITY = "identity"
#: A filing's answer kinds and their one stage: a person's or an
#: agent's reading, beside the graph, which no run asks and no round
#: applies.
JUDGMENT = "judgment"
FINDING = "finding"
FINDING_ANSWER = "finding-answer"
FINDING_ANSWER_WITHDRAWAL = "finding-answer-withdrawal"
RULING = "ruling"
RULING_WITHDRAWAL = "ruling-withdrawal"
REREAD = "reread"
SUGGESTION = "suggestion"
SUGGESTION_ANSWER = "suggestion-answer"
CONTRIBUTION_CHECK = "contribution-check"
#: Code's reading of the cited works, a filing too: the pages a
#: recheck or a verifier read, a work's resolution.
SEEN = "seen"
JUDGMENTS_STAGE = "judgments"
#: A person's answer's host and model: an edit of a
#: stage's own file, asked of no model.
PERSON = "person"
#: An agent's answer's host: an edit of a stage's own file by a
#: model outside the run's questions, its model and session recorded.
AGENT = "agent"
#: The hosts of an answer given on a version rather than asked by a run.
GIVEN_HOSTS = (PERSON, AGENT)
#: A review's findings' host, their model the journal's label for the
#: review: filed as the author received them.
REVIEWER = "reviewer"

#: `names` takes this where it names no block.
NONE = "none"


class CutOp(StrEnum):
    """A cut correction's operations."""

    SPLIT = "split"
    MERGE = "merge"
    REBOUND = "rebound"
    GROUP = "group"
    UNGROUP = "ungroup"
    ATTACH = "attach"
    PART = "part"
    UNPART = "unpart"
    ROLES = "roles"


#: The operations that change blocks.
BLOCK_OPS = frozenset({CutOp.SPLIT, CutOp.MERGE, CutOp.REBOUND})


class CorrectionClass(StrEnum):
    """Why a correction was made; the node keys' correction classes, the
    same values."""

    STATEMENT_DEFINES = "statement-defines"
    SEVERAL_JUSTIFICATIONS = "several-justifications"
    DERIVATION_BEFORE_THEOREM = "derivation-before-theorem"
    PROOF_AS_SECTION = "proof-as-section"
    STEPS_AS_PARAGRAPHS = "steps-as-paragraphs"
    FOOTNOTE = "footnote"
    NEVER_INCLUDED = "never-included"
    ARGUMENT_BOUNDARY = "argument-boundary"
    UNATTACHED_PROOF = "unattached-proof"
    MISSED_PART = "missed-part"
    WRONG_PART = "wrong-part"
    OTHER = "other"


#: The fields each operation takes besides `id`, `op`, `cls`, `why` and
#: `answer`.
OP_FIELDS: dict[CutOp, tuple[str, ...]] = {
    CutOp.SPLIT: ("block", "at"),
    CutOp.MERGE: ("blocks",),
    CutOp.REBOUND: ("blocks", "at"),
    CutOp.GROUP: ("kind", "first", "last", "names"),
    CutOp.UNGROUP: ("group",),
    CutOp.ATTACH: ("group", "names"),
    CutOp.PART: ("block", "kind", "marker", "at"),
    CutOp.UNPART: ("block", "marker"),
    CutOp.ROLES: ("block", "roles", "segments"),
}
#: The fields `check` watches for presence; `roles` and `segments` are
#: checked apart, since an empty set is a value a block may carry.
_OP_FIELD_NAMES = (
    "block",
    "blocks",
    "at",
    "kind",
    "first",
    "last",
    "names",
    "group",
    "marker",
)


class RecordError(ValueError):
    """A record outside its format, naming the file, the line and the field."""


def _roles_shape(roles: tuple[Role, ...], segments: tuple[Segment, ...]) -> list[str]:
    """The ways a `ROLES` correction's roles and segments break the model's
    invariants (`roles_problem`), each as `<field>: <what>`."""
    bad = next((role for role in roles if not isinstance(role, Role)), None)
    if bad is not None:
        return [f"roles: {bad!r} is not a role"]
    if roles != role_set(roles):
        return ["roles: not a set in the enum's order"]
    bad_segment = next(
        (segment for segment in segments if not isinstance(segment, Segment)), None
    )
    if bad_segment is not None:
        return [f"segments: {bad_segment!r} is not a segment"]
    problem = roles_problem(roles, segments)
    if problem is not None:
        return [f"segments: {problem}"]
    return []


@dataclass(frozen=True, slots=True)
class CutCorrection:
    """One operation on the cut. Only the fields its `op` takes are set.

    `at` holds the excerpts: a split's cut points (one or more), a
    rebound's new boundary and a part's text (one each). `names` is a block
    id or `"none"`; `roles` and `segments` a block's roles and their
    partition (an empty `roles` is a block that holds none, an
    empty `segments` with roles set is a mixed block not yet segmented).
    `answer` is the key of the answer the correction came from, `None` for
    one made by hand or by a test. `evidence` is the paper's words that
    show it, verbatim in the blocks it changes: a later stage's proposal
    carries it, the boundary review's does not. `decision`
    names the decision the correction is part of, by its first
    correction's id: an ungroup and the merge that follows it apply
    together or not at all.
    """

    id: str
    op: CutOp
    cls: CorrectionClass
    why: str
    answer: str | None = None
    block: str | None = None
    blocks: tuple[str, ...] = ()
    at: tuple[str, ...] = ()
    kind: GroupKind | PartKind | None = None
    first: str | None = None
    last: str | None = None
    names: str | None = None
    group: str | None = None
    marker: str | None = None
    roles: tuple[Role, ...] = ()
    segments: tuple[Segment, ...] = ()
    evidence: tuple[str, ...] = ()
    decision: str | None = None

    def check(self) -> tuple[str, ...]:
        """The ways the correction breaks its operation's shape, each as
        `<field>: <what>`; empty when it has none."""
        takes = OP_FIELDS[self.op]
        found = []
        for name in _OP_FIELD_NAMES:
            present = getattr(self, name) not in (None, ())
            if name in takes and not present:
                found.append(f"{name}: missing, {self.op.value} takes it")
            elif present and name not in takes:
                found.append(f"{name}: {self.op.value} does not take it")
        if self.op is CutOp.MERGE and 0 < len(self.blocks) < 2:
            found.append("blocks: a merge names two blocks or more")
        if self.op is CutOp.REBOUND and self.blocks and len(self.blocks) != 2:
            found.append("blocks: a rebound names two blocks")
        if self.op in (CutOp.REBOUND, CutOp.PART) and len(self.at) > 1:
            found.append(f"at: a {self.op.value} takes one excerpt")
        kinds = {CutOp.GROUP: GroupKind, CutOp.PART: PartKind}.get(self.op)
        if kinds and self.kind is not None and not isinstance(self.kind, kinds):
            found.append(f"kind: {self.kind!r} is not a {self.op.value} kind")
        if self.op is CutOp.ROLES:
            found.extend(_roles_shape(self.roles, self.segments))
        else:
            if self.roles:
                found.append(f"roles: {self.op.value} does not take it")
            if self.segments:
                found.append(f"segments: {self.op.value} does not take it")
        return tuple(found)


@dataclass(frozen=True, slots=True)
class TemplateRef:
    """The template an answer was asked with: its name and its version,
    and for a review session's brief the version of each question it
    names, by what answers it (`ruling`, or an aspect; written only where
    set)."""

    name: str
    version: str
    asks: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True, slots=True)
class AnswerRecord:
    """One attempt at one question, valid or not. `time` is
    ISO 8601 UTC; `tokens` the host's counts over every model the attempt
    ran (`input`, `output`, `reasoning`, `cache_read`, `cache_write`, and
    `cache_write_1h`, the part of `cache_write` kept an hour, where the
    host names it); `records` the ids of the corrections made from it."""

    key: str
    kind: str
    stage: str
    template: TemplateRef
    attempt: int
    host: str
    model: str
    time: str
    seconds: float
    cost: float
    tokens: dict[str, int] = field(default_factory=dict)
    raw: str = ""
    valid: bool = False
    problems: tuple[str, ...] = ()
    records: tuple[str, ...] = ()
    #: For a judge's answer (its stage says it is one), the key
    #: of the author's answer it judged.
    judges: str | None = None
    #: The host's effort level for the attempt, where it takes one
    #: (Claude Code's `--effort`), else `None`.
    effort: str | None = None
    #: The profile the attempt was asked under, else `None`.
    profile: str | None = None
    #: For a person's answer, the document hash of the
    #: version it was given on: its person round applies it on that
    #: version alone. Written only where set.
    version: str | None = None
    #: For an agent's answer, the session that gave it. Written
    #: only where set.
    session: str | None = None
    #: For a given answer, why, in its author's words: required
    #: for a retraction or a keep. Written only where set.
    reason: str | None = None
    #: False for a given edges answer whose author was shown no stale mark
    #: (`change`'s lines): it re-reads no owner (`docs/architecture.md` § 9).
    #: Written only where False.
    marks_shown: bool | None = None
    #: For a review session's answer, its round: one `review` command's
    #: questions for one session at one state (docs/architecture.md § 7).
    #: Written only where set.
    review_round: str | None = None
    #: For a review session's answer, the kept state its brief read; for
    #: a given answer (an edit), the kept state it was given on, which a
    #: drop of that state takes it back with. Written only where set.
    state: str | None = None
    #: For a review answer filed by `review --file-answer`, the attempt of
    #: the same key whose `raw` it checked again under today's check, at no
    #: cost (docs/architecture.md § 7). Written only where set.
    checked_again: int | None = None
    #: For a given answer, the SHA-256 of the file it was shown: it applies
    #: where the file shown now is the same. Written only where set.
    shown: str | None = None
    #: The same counts per model, where the attempt ran more than one (a
    #: session's subagent on another model). Written only where set.
    tokens_by_model: dict[str, dict[str, int]] = field(default_factory=dict)
    #: The runner's estimate for the attempt, in dollars, made before it
    #: was asked: the store's cost over estimate prices like runs
    #: (docs/architecture.md § 3). Written only where set.
    estimate: float | None = None
    #: For a queue task's answer, the order it was settled in within its
    #: run, from 1 (docs/architecture.md § 2). Written only where set.
    order: int | None = None
    #: For a queue session's record, the keys of the task answers it gave,
    #: in order; the session's cost and tokens are on it, its tasks' cost
    #: 0 (docs/architecture.md § 2). Written only where set.
    tasks: tuple[str, ...] = ()
    #: For a review session's answer that read a part of its state's text
    #: for findings, the nodes of the blocks it read; None, the whole text,
    #: or what its template says (docs/architecture.md § 7). Written only
    #: where set.
    read: tuple[str, ...] | None = None
    #: For a queue step's answer, its call: the tool and its arguments,
    #: which a resumed queue replays (docs/architecture.md § 2). Written
    #: only where set.
    call: dict[str, Any] | None = None


# --- the edge stage's records ------------------------------------------------


class EdgeKind(StrEnum):
    """An edge's four kinds."""

    USES = "uses"
    ABOUT = "about"
    JUSTIFIES = "justifies"
    QUALIFIES = "qualifies"


class Mark(StrEnum):
    """How the text points at an edge's target."""

    REF = "ref"
    NAME = "name"
    POSITION = "position"


class Status(StrEnum):
    """A claim's standing."""

    JUSTIFIED = "justified"
    SELF = "self"
    RESTATED = "restated"
    CONJECTURED = "conjectured"
    UNSUPPORTED = "unsupported"


class Partial(StrEnum):
    """How completely a justification's own words say it argues its target,
    on its justifies edge or self mark; none where it gives
    the argument written out."""

    SKETCH = "sketch"
    ANALOGY = "analogy"


class Declared(StrEnum):
    """What the paper declares of the argument it does not give for an
    `unsupported` claim."""

    OMITTED = "omitted"
    PROMISED = "promised"


class TargetKind(StrEnum):
    """What an edge points at: a block (or parts of it), a group, a file of
    the paper, or the paper as a whole (`scope`, whose one value is
    `paper`)."""

    BLOCK = "block"
    GROUP = "group"
    FILE = "file"
    SCOPE = "scope"


@dataclass(frozen=True, slots=True)
class EdgeTarget:
    kind: TargetKind
    name: str


@dataclass(frozen=True, slots=True)
class Entry:
    """A part of a block an edge names: its label, the block's
    own words for it, and the role of its content, `None` for none.
    `standard` marks a gloss of standard notation, a hand key's only."""

    id: str
    answer: str | None
    block: str
    label: str
    words: str
    role: Role | None
    standard: bool = False


#: An about line's source end that restates none of its block's claims, as
#: the edges sheet writes it (`→ about [-] T`) and an edge's name holds it.
#: Never a handle, which is a slug of words.
RESTATES_NONE = "-"


@dataclass(frozen=True, slots=True)
class Edge:
    """An edge from `source`, a block, or a group for justifies; `parts`
    are labels of entries of the same answer on the target block,
    `source_parts` those on the source block a uses edge leaves from
    (none: the block whole); `evidence` the source's own words. `claims`
    are the words of the target's standings the edge names, `source_claims` those of the
    source's an about edge restates from, or an `assumed` uses edge takes
    its target as a hypothesis of: none where
    the line names none; `restates_none` where an about line says it
    restates none of them (`→ about [-] T`)."""

    id: str
    answer: str | None
    source: str
    kind: EdgeKind
    target: EdgeTarget
    evidence: tuple[str, ...]
    parts: tuple[str, ...] = ()
    mark: Mark | None = None
    where_used: bool = False
    note: str | None = None
    #: on a justifies edge only
    partial: Partial | None = None
    #: on an about edge between premises only: the source is wider, the
    #: target a case of it (`docs/model.md` § 3)
    generalizes: bool = False
    claims: tuple[str, ...] = ()
    #: on an about edge, or an `assumed` uses edge
    source_claims: tuple[str, ...] = ()
    #: on an about edge only, never beside `source_claims`
    restates_none: bool = False
    #: on a uses edge onto a `conjectured` claim only: the source's
    #: statement takes the claim as a hypothesis (`docs/model.md` § 9)
    assumed: bool = False
    #: on a uses edge only (`docs/model.md` § 3)
    source_parts: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SelfMark:
    """A block whose claim carries its own reason or citation; `claims`
    the words of the block's standings it supports, none where it names
    none."""

    id: str
    answer: str | None
    block: str
    evidence: tuple[str, ...]
    note: str | None = None
    partial: Partial | None = None
    claims: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Standing:
    """A claim's standing: `claim` is the block's words for it."""

    id: str
    answer: str | None
    block: str
    claim: str
    status: Status
    note: str | None = None
    #: on an `unsupported` standing only
    declared: Declared | None = None
    #: the sheet's handle for the claim, where its ⊢ line defines one or its
    #: block holds several: shown, never an identity
    handle: str | None = None


@dataclass(frozen=True, slots=True)
class Frame:
    """A block of the ambient frame."""

    id: str
    answer: str | None
    block: str


class ImpactClass(StrEnum):
    """How an edit changed what a block, or a part of it, says: `unchanged` and
    `reworded` close the change there; every other class carries it on by the rule."""

    UNCHANGED = "unchanged"
    REWORDED = "reworded"
    NARROWED = "narrowed"
    WIDENED = "widened"
    STRENGTHENED = "strengthened"
    WEAKENED = "weakened"
    REMOVED = "removed"
    MOVED = "moved"


@dataclass(frozen=True, slots=True)
class Delta:
    """An update's Δ line: how the edit changed what `block`
    says, for its legend part `entry` (its label), or the block as a whole
    where `entry` is None, with the writer's note. Only a writer's are
    stored; the rule's (`removed` on a deleted block) are computed."""

    id: str
    answer: str | None
    block: str
    entry: str | None
    cls: ImpactClass
    note: str


class LineKind(StrEnum):
    """The kinds of an owner's lines on the edges sheet whose order the
    records of each kind do not give between them."""

    EDGE = "edge"
    SELF = "self"
    STANDING = "standing"


@dataclass(frozen=True, slots=True)
class LineOrder:
    """The order of an owner's edge, self mark and standing lines on its
    sheet, by kind: each kind's records keep their own order, so the
    kinds alone give the whole. Written only where it is not every edge,
    then every self mark, then every standing (`docs/architecture.md`
    § 4)."""

    id: str
    answer: str | None
    owner: str
    kinds: tuple[LineKind, ...]


@dataclass(frozen=True, slots=True)
class EdgeRecords:
    """One answer's records of the edge stage, each kind in the sheet's
    order; `ids` gives them in the order the resolver numbers them."""

    entries: tuple[Entry, ...] = ()
    edges: tuple[Edge, ...] = ()
    selfs: tuple[SelfMark, ...] = ()
    standings: tuple[Standing, ...] = ()
    frame: tuple[Frame, ...] = ()
    #: An update's Δ lines, numbered after the edge kinds.
    deltas: tuple[Delta, ...] = ()
    #: Numbered after the Δ lines.
    orders: tuple[LineOrder, ...] = ()

    def all(
        self,
    ) -> tuple[Entry | Edge | SelfMark | Standing | Frame | Delta | LineOrder, ...]:
        return (
            *self.entries,
            *self.edges,
            *self.selfs,
            *self.standings,
            *self.frame,
            *self.deltas,
            *self.orders,
        )

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(r.id for r in self.all())

    def __bool__(self) -> bool:
        return bool(self.all())


# --- flags ------------------------------------------------------


class Signal(StrEnum):
    """What a flag says, short. Each stage has its own, and
    `other`, a flag with no signal word: friction. The rules' are computed
    from a build's records, never stored."""

    # roles
    SPLIT = "split"
    MERGE = "merge"
    ROLE_DOUBT = "role-doubt"
    # groups
    TWO_BLOCK = "two-block"
    RUN_UNCLEAR = "run-unclear"
    # edges
    ROLE = "role"
    PART = "part"
    CUT = "cut"
    # the rules'
    TWO_BLOCK_GROUP = "two-block-group"
    QUALIFIES = "qualifies"
    DEFINITION_USES_CLAIM = "definition-uses-claim"
    CONTAINER_STATEMENTS = "container-statements"
    MAPPED_REFERENCE_DANGLING = "mapped-reference-dangling"
    OTHER = "other"


#: The signals each stage's `?` line may give, by the stage's name.
STAGE_SIGNALS: dict[str, tuple[Signal, ...]] = {
    "roles": (Signal.SPLIT, Signal.MERGE, Signal.ROLE_DOUBT, Signal.OTHER),
    "groups": (Signal.TWO_BLOCK, Signal.RUN_UNCLEAR, Signal.OTHER),
    "edges": (Signal.ROLE, Signal.PART, Signal.CUT, Signal.OTHER),
}
#: The signals computed by a rule (`axiomation.flags`).
RULE_SIGNALS = (
    Signal.TWO_BLOCK_GROUP,
    Signal.QUALIFIES,
    Signal.DEFINITION_USES_CLAIM,
    Signal.CONTAINER_STATEMENTS,
    Signal.MAPPED_REFERENCE_DANGLING,
)


@dataclass(frozen=True, slots=True)
class Flag:
    """A position a stage took on the earlier stages' work:
    the blocks it is about, its signal, the blocks' own words that show it
    and what the stage read there. A flag changes nothing; a later step
    reads it. The stage is its answer's. A rule's flag (`axiomation.flags`)
    has no answer and is never stored."""

    id: str
    answer: str | None
    blocks: tuple[str, ...]
    signal: Signal
    evidence: tuple[str, ...]
    note: str


@dataclass(frozen=True, slots=True)
class VocabularyEntry:
    """One entry of a vocabulary answer, as its check read it: the
    candidate as the file showed it (`vocabulary_file.Entry`), the kind
    written, its words and its evidence. The map is made from these, never
    from the answer's text again. Its shape and kind are the reader's
    (`CandidateShape`, `VocabularyKind`): the records name, the reader
    decides."""

    id: str
    answer: str | None
    namespace: str
    name: str
    shape: str
    declaration: str | None
    opens: str | None
    empty: bool
    labelled: bool
    index: int
    count: int
    kind: str
    words: tuple[str, ...]
    evidence: str


@dataclass(frozen=True, slots=True)
class Decline:
    """One edit a judge declined, settled: the ledger's line the next
    authors' file shows at `offset` in `file` (`cut_file.settled`)."""

    id: str
    answer: str | None
    file: str
    offset: int
    line: str


# --- the lineage -----------------------------------------


@dataclass(frozen=True, slots=True)
class LineageLink:
    """One link of an identity answer's lineage: OLD's blocks `old` became
    NEW's `new`, of class `cls` by tier `tier` (`identity.BlockClass` and
    `Tier` values: the records name, identity decides), with a split's or
    a merge's `heir` and whether the pair `moved`."""

    id: str
    answer: str | None
    old: tuple[str, ...]
    new: tuple[str, ...]
    cls: str
    tier: str
    heir: str | None = None
    moved: bool = False


# --- judgments, findings and their answers -------------------------


class Aspect(StrEnum):
    """What a reading judges: `well-posed` is
    a finding's alone; `computed`, whether a script computes what a block
    says and prints its value; `presentation`, `significance`, `novelty`
    and `literature`, how the paper reads, what it is worth and where it
    stands in its literature, findings' alone."""

    VALID = "valid"
    TRUE = "true"
    ACCURATE = "accurate"
    CITED = "cited"
    WELL_POSED = "well-posed"
    COMPUTED = "computed"
    PRESENTATION = "presentation"
    SIGNIFICANCE = "significance"
    NOVELTY = "novelty"
    LITERATURE = "literature"


#: The aspects a judgment may have.
JUDGED_ASPECTS = (
    Aspect.VALID,
    Aspect.TRUE,
    Aspect.ACCURATE,
    Aspect.CITED,
    Aspect.COMPUTED,
)

#: The aspects that say how a paper reads or what it is worth, never whether
#: it holds: a trivial result is still established, so their findings never
#: block.
NON_BLOCKING_ASPECTS = (
    Aspect.PRESENTATION,
    Aspect.SIGNIFICANCE,
    Aspect.NOVELTY,
    Aspect.LITERATURE,
)


class Verdict(StrEnum):
    """One set for every aspect: shown, not shown
    where evidence is offered, shown false, could not tell."""

    VERIFIED = "verified"
    NOT_SHOWN = "not-shown"
    CONTRADICTED = "contradicted"
    INCONCLUSIVE = "inconclusive"


#: The verdicts that say what a cited work holds: each needs a page read.
READ_VERDICTS = (Verdict.VERIFIED, Verdict.NOT_SHOWN, Verdict.CONTRADICTED)


class Depth(StrEnum):
    """How a judgment was made (the verifier kinds): recorded and shown,
    never ranked."""

    INFORMAL = "informal"
    COMPUTATIONAL = "computational"
    FORMAL = "formal"


class FindingStatus(StrEnum):
    """A finding's answer: an edit never answers one."""

    FIXED = "fixed"
    DISMISSED = "dismissed"
    DUPLICATE = "duplicate"
    POSTPONED = "postponed"


class SuggestionStatus(StrEnum):
    """A suggestion's answer, the author's decision: it is a pointer
    offered, never a problem, so it is taken or declined."""

    TAKEN = "taken"
    DECLINED = "declined"


class RulingKind(StrEnum):
    """Whether the paper owes an argument for a claim standing `unsupported`
    (docs/model.md § 8, "A ruling"): owed, or not, and why not."""

    OWED = "owed"
    KNOWN = "known"
    #: follows at sight from a result of the paper's own, which a uses edge
    #: of its block names
    IMMEDIATE = "immediate"
    INTERPRETATION = "interpretation"
    #: a claim that the paper's work is new, or that other works lack or
    #: did not do something: the literature settles it, and the
    #: contribution session reads it
    POSITIONING = "positioning"
    #: a person's alone: a referee would want it argued, and the author
    #: leaves it unargued, the reason saying why
    LEFT_UNARGUED = "left-unargued"


class Publication(StrEnum):
    """How a cited work is published (docs/model.md § 8): read from the
    resolvers' answers, or set by a person, never from the bibliography's
    entry type."""

    #: a journal article or a proceedings paper
    REFEREED = "refereed"
    BOOK = "book"
    #: a preprint no version of record is known for
    PREPRINT = "preprint"
    #: a preprint whose version of record has been published since
    PREPRINT_PUBLISHED = "preprint-published"
    THESIS_OR_REPORT = "thesis-or-report"
    #: a work with a published correction or erratum
    ERRATUM = "erratum"
    RETRACTED = "retracted"
    UNKNOWN = "unknown"


class Match(StrEnum):
    """How a version of record was matched to the preprint cited."""

    #: a DOI the preprint's record names: its authors' or its server's
    IDENTIFIER = "identifier"
    #: the journal reference its authors gave, with no DOI
    REFERENCE = "reference"
    #: a record whose title and first author are the entry's
    TITLE_AND_AUTHORS = "title-and-authors"


class AnchorKind(StrEnum):
    """What a finding may attach to."""

    BLOCK = "block"
    PART = "part"
    GROUP = "group"
    SPAN = "span"
    PAPER = "paper"
    WORK = "work"
    #: a kept file's lines, by its key and their words
    FILE = "file"


class BasisKind(StrEnum):
    """A basis element's kind: a block's or group's node, a file of the
    paper, the paper whole, or an edge."""

    NODE = "node"
    FILE = "file"
    PAPER = "paper"
    EDGE = "edge"
    #: a cited work's file, named by a file lapse's cause alone: a
    #: judgment's files are recorded beside its basis, never in it
    CITED_FILE = "cited-file"
    #: a run of a script, named by a run lapse's cause alone
    RUN = "run"


@dataclass(frozen=True, slots=True)
class Subject:
    """What a judgment is on: `node`, a block's or a group's node id (J for
    `valid`); `target`, T's node for `valid`; `words`, a claim's words (on
    `node`, or T's for `valid`) or a span's excerpt (`cited`); `work`, the
    cited work's key (`cited`); `file`, the code file a `computed`
    block uses."""

    node: str
    target: str | None = None
    words: str | None = None
    work: str | None = None
    file: str | None = None


@dataclass(frozen=True, slots=True)
class Anchor:
    """A finding's anchor: a node (`block`, `group`), a node and its words
    (`part`, `span`), the paper, a cited work by its key (`work`), or a
    kept file's lines by its key and their words (`file`)."""

    kind: AnchorKind
    node: str | None = None
    words: str | None = None
    work: str | None = None
    file: str | None = None


@dataclass(frozen=True, slots=True)
class BasisElement:
    """One element of what a reading rests on, at the state it was made on.
    `NODE`: `name` the node id, `text` its text's
    hash, `parts` the labels of its parts the basis's edges name (none: the
    node whole). `FILE`: `name` its path, `text` its blob. `PAPER`: `text`
    the hash of every node's text in order. `EDGE`: `name` its source's
    node, `edge` its kind and target (`versions.edge_ids`), `parts` the
    words of the parts it names."""

    kind: BasisKind
    name: str
    text: str | None = None
    parts: tuple[str, ...] = ()
    edge: tuple[str, str] | None = None


@dataclass(frozen=True, slots=True)
class WorkUnread:
    """A cited work a review laid in with no page: its
    key and how its read failed (`mismatch`, `absent`, `infrastructure`)."""

    key: str
    read_status: ReadStatus


@dataclass(frozen=True, slots=True)
class Judgment:
    """A reader's conclusion on one subject and aspect at a kept state,
    its author its answer's. `reaffirms` names the judgment a
    re-ask found standing, `adopts` the one a person adopted; a
    `proposed` one is a person's reading for a review to settle."""

    id: str
    answer: str | None
    state: str
    aspect: Aspect
    subject: Subject
    verdict: Verdict
    depth: Depth
    evidence: str
    basis: tuple[BasisElement, ...]
    reaffirms: str | None = None
    #: the cited pages it read (`cited`; `valid` and `true`): beside the basis, since a
    #: page is not walked along the states; the file it is a page of printing other
    #: content lapses it (`given`)
    pages: tuple[PageStamp, ...] = ()
    #: the agent's judgment a person took as their own (`adopt`)
    adopts: str | None = None
    #: the run of its code file a `computed` judgment read, beside the
    #: basis as its pages are; a later run of the same with
    #: another output lapses it
    run: RunStamp | None = None
    #: each value a `computed` judgment matched: the text's words and the
    #: run's output's
    values: tuple[ValuePair, ...] = ()
    #: the script's lines a `computed` judgment read the values computed by,
    #: quoted from the kept file; checked when filed, never read back
    #: (`judgments._read`)
    code: tuple[str, ...] = ()
    #: each work its subject cites that its review laid in with no page:
    #: a reading given no file of a work names these alone
    unread: tuple[WorkUnread, ...] = ()
    #: the one hop's elements at its state that its review's own edge lines
    #: took away: its basis is the hop as those lines correct it
    retracted: tuple[BasisElement, ...] = ()
    #: each file of each work its subject cites, and of each other work it
    #: read a page of, as it read then: the filing's record, never the
    #: agent's; what a reading that did not verify is asked again against
    #: when its evidence grows
    given: tuple[FileStamp, ...] = ()
    #: a person's reading filed as a proposal: it never speaks, and the
    #: next review of its kind asks its subject again with it in view
    proposed: bool = False


@dataclass(frozen=True, slots=True)
class Finding:
    """A problem and its fix, on one aspect, severity 1 (minor) to 3
    (critical), at one or more anchors, lapsing as a judgment does."""

    id: str
    answer: str | None
    state: str
    aspect: Aspect
    severity: int
    problem: str
    fix: str | None
    anchors: tuple[Anchor, ...]
    basis: tuple[BasisElement, ...]
    #: the pages of a work it read (a `novelty` or `literature` finding's work):
    #: recorded, never walked; the work is not the paper's
    pages: tuple[PageStamp, ...] = ()
    #: the lapsed finding a re-reading found standing, refiled here; and
    #: why it still stands
    reaffirms: str | None = None
    evidence: str | None = None
    #: as a judgment's: the hop's elements its review's own edge lines took away
    retracted: tuple[BasisElement, ...] = ()
    #: refiled by a review that weighed a proposal on it and found the
    #: disagreement one of reading, no case settling it: the person's to
    #: answer
    reading: bool = False


@dataclass(frozen=True, slots=True)
class FindingAnswer:
    """A later record answering a finding; the latest speaks. A `duplicate`
    names the finding it duplicates (`of`). A person's answer filed as a
    proposal (`proposed`) speaks no more than an agent's by hand."""

    id: str
    answer: str | None
    finding: str
    status: FindingStatus
    note: str | None = None
    of: str | None = None
    proposed: bool = False


@dataclass(frozen=True, slots=True)
class AnswerWithdrawal:
    """A person's withdrawal of an answer to a finding (`finding_answer`,
    its id), and why: the answer speaks no more, and the finding reads as
    its other answers leave it."""

    id: str
    answer: str | None
    finding_answer: str
    reason: str


@dataclass(frozen=True, slots=True)
class FileSeen:
    """A cited work's file as a review, a recheck or a filing saw it, filed
    where it differs from the latest: the corpus as the store knows it, and
    what lapses a judgment that read the file where its content changes
    (docs/model.md § 8)."""

    id: str
    answer: str | None
    stamp: FileStamp


@dataclass(frozen=True, slots=True)
class VersionOfRecord:
    """Where a preprint cited was published: its venue and year, its DOI
    or its page, the source that found it and how it was matched."""

    venue: str
    year: str
    doi: str | None
    url: str | None
    source: str
    match: Match


@dataclass(frozen=True, slots=True)
class WorkSeen:
    """A cited work as resolved at one time, or as a person set it: its
    DOI where found, its publication, where that came from in words (a
    resolver's answer, or a person's why) and, for a preprint, the
    versions of record found. A person's latest speaks, else the
    resolvers' latest."""

    id: str
    answer: str | None
    key: str
    doi: str | None
    publication: Publication
    source: str = ""
    records: tuple[VersionOfRecord, ...] = ()

    @property
    def retracted(self) -> bool:
        return self.publication is Publication.RETRACTED


@dataclass(frozen=True, slots=True)
class WorkAccepted:
    """A person's word that the paper rests on a cited work as it is
    (docs/model.md § 11, `cited-publications`), with why; `withdrawn`
    takes an earlier word back. A person's alone; the latest speaks."""

    id: str
    answer: str | None
    key: str
    reason: str
    withdrawn: bool = False


@dataclass(frozen=True, slots=True)
class ValuePair:
    """A value a `computed` judgment sets against the run: the block's
    words for it and the output's words the reader set beside them, each
    found where it lives. Under a `verified` they are one value, the
    judgment's reading; under a `not-shown` or `contradicted` they may
    differ, and the pair shows where."""

    text: str
    output: str


@dataclass(frozen=True, slots=True)
class RunSeen:
    """A run of a paper's script as code ran it: each run a
    filing names or a person asks for. A later one of the same call, run,
    with another output lapses the judgments that read the earlier."""

    id: str
    answer: str | None
    stamp: RunStamp


@dataclass(frozen=True, slots=True)
class Ruling:
    """A reader's call, at a kept state, whether the paper owes an argument
    for a claim standing `unsupported`: its block's node and the claim's
    words, the ruling and its reason. Its author is its answer's.
    `reaffirms` names the ruling under an older question a review kept,
    its kind and reason carried. An `owed` one carries its weight, a
    severity 1 to 3, and its fix, what would discharge it. A `proposed`
    one is a person's ruling for the next review to settle: it never
    speaks. One may rest on pages
    of a cited work (`pages`), stamped and given as a judgment's are; one
    that rests on none, as a result the field knows, names none."""

    id: str
    answer: str | None
    state: str
    subject: Subject
    kind: RulingKind
    reason: str
    reaffirms: str | None = None
    severity: int | None = None
    fix: str | None = None
    proposed: bool = False
    #: the cited pages it rests on: a file of theirs printing other content
    #: lapses it (`given`)
    pages: tuple[PageStamp, ...] = ()
    #: each file of each work it read a page of, as it read then: the
    #: filing's record, never the author's
    given: tuple[FileStamp, ...] = ()


@dataclass(frozen=True, slots=True)
class RulingWithdrawal:
    """A person's withdrawal of a ruling, and why: the ruling speaks no
    more, and the claim is asked again."""

    id: str
    answer: str | None
    ruling: str
    reason: str


@dataclass(frozen=True, slots=True)
class Reread:
    """A person's or an agent's word that it read a stale-marked owner's
    lines against what changed, and they stand: at the kept state `state`,
    the owner's node and why. It clears every mark on the owner made at or
    before that state, pins nothing and changes no line. Its author is its
    answer's."""

    id: str
    answer: str | None
    state: str
    node: str
    reason: str


@dataclass(frozen=True, slots=True)
class Searched:
    """The support search's word that it searched the paper for the
    support of a claim standing `unsupported`, at the kept state `state`,
    and found none: no block argues it, its own block gives no step, and
    it restates no claim the paper states (`docs/model.md` § 8). The claim
    as a `true` subject (its block's node and its words), and why. It
    changes no line; it speaks while the claim stands `unsupported`, and
    lapses as a ruling does. Its author is its answer's."""

    id: str
    answer: str | None
    state: str
    subject: Subject
    reason: str


@dataclass(frozen=True, slots=True)
class RealCircle:
    """An agent's or a person's word that a circle of what claims rest on
    is real (`docs/model.md` § 9): at the kept state `state`, every end on
    it named as the text relies on, and it stays. The circle by its key
    (`established.circle_key`), why, and what a person should change. It
    changes no line and lends no support; a circle whose key changes is
    asked again. Its author is its answer's."""

    id: str
    answer: str | None
    state: str
    circle: str
    reason: str
    proposal: str


@dataclass(frozen=True, slots=True)
class WholeBlock:
    """An agent's or a person's word that a block an edge enters at a part
    is read whole (`docs/model.md` § 9): at the kept state `state`, none of
    its lines can be placed at a part it leaves from, and no part of it
    writes another apart. The block by its key (`placing.block_key`: its
    words, its parts, its lines and the qualifiers onto it), and why. It
    changes no line; a block whose key changes is asked again. Its author
    is its answer's."""

    id: str
    answer: str | None
    state: str
    block: str
    reason: str


@dataclass(frozen=True, slots=True)
class Suggestion:
    """A work the paper may want to cite at a place (docs/model.md § 8):
    its anchors, the block or group first, then each work suggested; why a
    reader of the field might expect it there; the pages read of it,
    recorded and never walked. Never status: it lapses as a finding does,
    on its anchors' text."""

    id: str
    answer: str | None
    state: str
    anchors: tuple[Anchor, ...]
    why: str
    basis: tuple[BasisElement, ...]
    pages: tuple[PageStamp, ...] = ()


@dataclass(frozen=True, slots=True)
class ContributionCheck:
    """A contribution the contribution session checked against the
    literature (docs/model.md § 8), at a kept state: its block's node and
    the words it quotes, what it searched and what it found. It is a
    `positioning` claim's reading, and lapses as a ruling does, when the
    words it quotes change or end."""

    id: str
    answer: str | None
    state: str
    subject: Subject
    searched: str
    found: str


@dataclass(frozen=True, slots=True)
class SuggestionAnswer:
    """A later record answering a suggestion; the latest speaks."""

    id: str
    answer: str | None
    suggestion: str
    status: SuggestionStatus
    note: str | None = None


#: A filed record of any kind.
Filing = (
    Judgment
    | Finding
    | FindingAnswer
    | AnswerWithdrawal
    | WorkSeen
    | WorkAccepted
    | RunSeen
    | FileSeen
    | Ruling
    | RulingWithdrawal
    | Reread
    | RealCircle
    | Searched
    | WholeBlock
    | Suggestion
    | SuggestionAnswer
    | ContributionCheck
)


@dataclass(frozen=True, slots=True)
class JudgmentRecords:
    """One filing's records, or the store's, each kind in file order."""

    judgments: tuple[Judgment, ...] = ()
    findings: tuple[Finding, ...] = ()
    answers: tuple[FindingAnswer, ...] = ()
    answer_withdrawals: tuple[AnswerWithdrawal, ...] = ()
    works: tuple[WorkSeen, ...] = ()
    accepted: tuple[WorkAccepted, ...] = ()
    runs: tuple[RunSeen, ...] = ()
    rulings: tuple[Ruling, ...] = ()
    withdrawals: tuple[RulingWithdrawal, ...] = ()
    rereads: tuple[Reread, ...] = ()
    circles: tuple[RealCircle, ...] = ()
    searches: tuple[Searched, ...] = ()
    wholes: tuple[WholeBlock, ...] = ()
    suggestions: tuple[Suggestion, ...] = ()
    suggestion_answers: tuple[SuggestionAnswer, ...] = ()
    checks: tuple[ContributionCheck, ...] = ()
    files: tuple[FileSeen, ...] = ()

    def all(self) -> tuple[Filing, ...]:
        return (
            *self.judgments,
            *self.findings,
            *self.answers,
            *self.answer_withdrawals,
            *self.works,
            *self.accepted,
            *self.runs,
            *self.rulings,
            *self.withdrawals,
            *self.rereads,
            *self.circles,
            *self.searches,
            *self.wholes,
            *self.suggestions,
            *self.suggestion_answers,
            *self.checks,
            *self.files,
        )

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(r.id for r in self.all())


# --- written form -----------------------------------------------------------


def _correction_data(c: CutCorrection) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": c.id,
        "op": c.op.value,
        "class": c.cls.value,
        "why": c.why,
        "answer": c.answer,
    }
    for name in OP_FIELDS[c.op]:
        value = getattr(c, name)
        if name == "roles":
            data[name] = [role.value for role in value]
        elif name == "segments":
            data[name] = [
                {
                    "starts": segment.starts,
                    "role": segment.role.value if segment.role else NONE,
                }
                for segment in value
            ]
        elif isinstance(value, tuple):
            data[name] = list(value)
        else:
            data[name] = str(value)
    if c.evidence:
        data["evidence"] = list(c.evidence)
    if c.decision is not None:
        data["decision"] = c.decision
    return data


def _answer_data(a: AnswerRecord) -> dict[str, Any]:
    return {
        "key": a.key,
        "kind": a.kind,
        "stage": a.stage,
        "template": {"name": a.template.name, "version": a.template.version}
        | ({"asks": dict(a.template.asks)} if a.template.asks else {}),
        "attempt": a.attempt,
        "host": a.host,
        "model": a.model,
        "time": a.time,
        "seconds": a.seconds,
        "cost": a.cost,
        "tokens": dict(a.tokens),
        "valid": a.valid,
        "problems": list(a.problems),
        "records": list(a.records),
        "judges": a.judges,
        "effort": a.effort,
        "profile": a.profile,
        # each only where set
        **({"version": a.version} if a.version is not None else {}),
        **({"session": a.session} if a.session is not None else {}),
        **({"reason": a.reason} if a.reason is not None else {}),
        **({"marks_shown": False} if a.marks_shown is False else {}),
        **({"review_round": a.review_round} if a.review_round is not None else {}),
        **({"state": a.state} if a.state is not None else {}),
        **({"shown": a.shown} if a.shown is not None else {}),
        **({"checked_again": a.checked_again} if a.checked_again is not None else {}),
        **({"estimate": a.estimate} if a.estimate is not None else {}),
        **({"order": a.order} if a.order is not None else {}),
        **({"tasks": list(a.tasks)} if a.tasks else {}),
        **({"read": list(a.read)} if a.read is not None else {}),
        **({"call": dict(a.call)} if a.call is not None else {}),
        **(
            {"tokens_by_model": {m: dict(c) for m, c in a.tokens_by_model.items()}}
            if a.tokens_by_model
            else {}
        ),
    }


def _role_value(role: Role | None) -> str:
    return NONE if role is None else role.value


def _entry_data(r: Entry) -> dict[str, Any]:
    return {
        "id": r.id,
        "answer": r.answer,
        "block": r.block,
        "label": r.label,
        "words": r.words,
        "role": _role_value(r.role),
        "standard": r.standard,
    }


def _edge_data(r: Edge) -> dict[str, Any]:
    return {
        "id": r.id,
        "answer": r.answer,
        "source": r.source,
        "kind": r.kind.value,
        "target": {r.target.kind.value: r.target.name},
        "parts": list(r.parts),
        "evidence": list(r.evidence),
        "mark": None if r.mark is None else r.mark.value,
        "where_used": r.where_used,
        "note": r.note,
        **({} if r.partial is None else {"partial": r.partial.value}),
        **({"generalizes": True} if r.generalizes else {}),
        **({"claims": list(r.claims)} if r.claims else {}),
        **({"source_claims": list(r.source_claims)} if r.source_claims else {}),
        **({"restates_none": True} if r.restates_none else {}),
        **({"assumed": True} if r.assumed else {}),
        **({"source_parts": list(r.source_parts)} if r.source_parts else {}),
    }


def _self_data(r: SelfMark) -> dict[str, Any]:
    return {
        "id": r.id,
        "answer": r.answer,
        "block": r.block,
        "evidence": list(r.evidence),
        "note": r.note,
        **({} if r.partial is None else {"partial": r.partial.value}),
        **({"claims": list(r.claims)} if r.claims else {}),
    }


def _standing_data(r: Standing) -> dict[str, Any]:
    return {
        "id": r.id,
        "answer": r.answer,
        "block": r.block,
        "claim": r.claim,
        "status": r.status.value,
        "note": r.note,
        **({} if r.declared is None else {"declared": r.declared.value}),
        **({} if r.handle is None else {"handle": r.handle}),
    }


def _frame_data(r: Frame) -> dict[str, Any]:
    return {"id": r.id, "answer": r.answer, "block": r.block}


def _delta_data(r: Delta) -> dict[str, Any]:
    return {
        "id": r.id,
        "answer": r.answer,
        "block": r.block,
        "entry": r.entry,
        "class": r.cls.value,
        "note": r.note,
    }


def _order_data(r: LineOrder) -> dict[str, Any]:
    return {
        "id": r.id,
        "answer": r.answer,
        "owner": r.owner,
        "kinds": [k.value for k in r.kinds],
    }


def _flag_data(r: Flag) -> dict[str, Any]:
    return {
        "id": r.id,
        "answer": r.answer,
        "blocks": list(r.blocks),
        "signal": r.signal.value,
        "evidence": list(r.evidence),
        "note": r.note,
    }


def _vocabulary_data(r: VocabularyEntry) -> dict[str, Any]:
    return {
        "id": r.id,
        "answer": r.answer,
        "namespace": r.namespace,
        "name": r.name,
        "shape": r.shape,
        "declaration": r.declaration,
        "opens": r.opens,
        "empty": r.empty,
        "labelled": r.labelled,
        "index": r.index,
        "count": r.count,
        "kind": r.kind,
        "words": list(r.words),
        "evidence": r.evidence,
    }


def _decline_data(r: Decline) -> dict[str, Any]:
    return {
        "id": r.id,
        "answer": r.answer,
        "file": r.file,
        "offset": r.offset,
        "line": r.line,
    }


def _lineage_data(r: LineageLink) -> dict[str, Any]:
    return {
        "id": r.id,
        "answer": r.answer,
        "old": list(r.old),
        "new": list(r.new),
        "cls": r.cls,
        "tier": r.tier,
        "heir": r.heir,
        "moved": r.moved,
    }


def _subject_data(r: Subject) -> dict[str, Any]:
    data: dict[str, Any] = {"node": r.node, "target": r.target, "words": r.words}
    if r.work is not None:
        data["work"] = r.work
    if r.file is not None:
        data["file"] = r.file
    return data


def _stamp_data(r: PageStamp) -> dict[str, Any]:
    return {
        "key": r.key,
        "page_index": r.page_index,
        "read_status": r.read_status.value,
    }


def _anchor_data(r: Anchor) -> dict[str, Any]:
    data = {"kind": r.kind.value, "node": r.node, "words": r.words, "work": r.work}
    if r.file is not None:
        data["file"] = r.file
    return data


def _basis_data(r: BasisElement) -> dict[str, Any]:
    return {
        "kind": r.kind.value,
        "name": r.name,
        "text": r.text,
        "parts": list(r.parts),
        "edge": None if r.edge is None else list(r.edge),
    }


def _judgment_data(r: Judgment) -> dict[str, Any]:
    return {
        "id": r.id,
        "answer": r.answer,
        "state": r.state,
        "aspect": r.aspect.value,
        "subject": _subject_data(r.subject),
        "verdict": r.verdict.value,
        "depth": r.depth.value,
        "evidence": r.evidence,
        "basis": [_basis_data(b) for b in r.basis],
        "reaffirms": r.reaffirms,
    } | (
        ({"pages": [_stamp_data(p) for p in r.pages]} if r.pages else {})
        | ({"adopts": r.adopts} if r.adopts else {})
        | ({"run": _run_data(r.run)} if r.run is not None else {})
        | (
            {"values": [{"text": v.text, "output": v.output} for v in r.values]}
            if r.values
            else {}
        )
        | ({"code": list(r.code)} if r.code else {})
        | (
            {
                "unread": [
                    {"key": w.key, "read_status": w.read_status.value} for w in r.unread
                ]
            }
            if r.unread
            else {}
        )
        | ({"retracted": [_basis_data(b) for b in r.retracted]} if r.retracted else {})
        | ({"given": [_file_data(f) for f in r.given]} if r.given else {})
        | ({"proposed": True} if r.proposed else {})
    )


def _finding_data(r: Finding) -> dict[str, Any]:
    return {
        "id": r.id,
        "answer": r.answer,
        "state": r.state,
        "aspect": r.aspect.value,
        "severity": r.severity,
        "problem": r.problem,
        "fix": r.fix,
        "anchors": [_anchor_data(a) for a in r.anchors],
        "basis": [_basis_data(b) for b in r.basis],
    } | (
        ({"pages": [_stamp_data(p) for p in r.pages]} if r.pages else {})
        | ({"reaffirms": r.reaffirms} if r.reaffirms is not None else {})
        | ({"evidence": r.evidence} if r.evidence is not None else {})
        | ({"retracted": [_basis_data(b) for b in r.retracted]} if r.retracted else {})
        | ({"reading": True} if r.reading else {})
    )


def _finding_answer_data(r: FindingAnswer) -> dict[str, Any]:
    return (
        {
            "id": r.id,
            "answer": r.answer,
            "finding": r.finding,
            "status": r.status.value,
            "note": r.note,
        }
        | ({"of": r.of} if r.of is not None else {})
        | ({"proposed": True} if r.proposed else {})
    )


def _answer_withdrawal_data(r: AnswerWithdrawal) -> dict[str, Any]:
    return {
        "id": r.id,
        "answer": r.answer,
        "finding_answer": r.finding_answer,
        "reason": r.reason,
    }


def _file_data(r: FileStamp) -> dict[str, Any]:
    return {"key": r.key, "read_status": r.read_status.value, "file": r.file} | (
        {"content": r.content} if r.content else {}
    )


def _file_seen_data(r: FileSeen) -> dict[str, Any]:
    return {"id": r.id, "answer": r.answer, "file": _file_data(r.stamp)}


def _run_data(r: RunStamp) -> dict[str, Any]:
    return {
        "key": r.key,
        "blob": r.blob,
        "runner": r.runner,
        "arguments": list(r.arguments),
        "directory": r.directory,
        "status": r.status.value,
        "output": r.output,
    }


def _run_seen_data(r: RunSeen) -> dict[str, Any]:
    return {"id": r.id, "answer": r.answer, "run": _run_data(r.stamp)}


def _work_seen_data(r: WorkSeen) -> dict[str, Any]:
    return {
        "id": r.id,
        "answer": r.answer,
        "key": r.key,
        "doi": r.doi,
        "publication": r.publication.value,
        "source": r.source,
        "records": [
            {
                "venue": v.venue,
                "year": v.year,
                "doi": v.doi,
                "url": v.url,
                "source": v.source,
                "match": v.match.value,
            }
            for v in r.records
        ],
    }


def _accepted_data(r: WorkAccepted) -> dict[str, Any]:
    return {
        "id": r.id,
        "answer": r.answer,
        "key": r.key,
        "reason": r.reason,
        "withdrawn": r.withdrawn,
    }


def _ruling_data(r: Ruling) -> dict[str, Any]:
    return (
        {
            "id": r.id,
            "answer": r.answer,
            "state": r.state,
            "subject": _subject_data(r.subject),
            "kind": r.kind.value,
            "reason": r.reason,
        }
        | ({"reaffirms": r.reaffirms} if r.reaffirms is not None else {})
        | ({"severity": r.severity} if r.severity is not None else {})
        | ({"fix": r.fix} if r.fix is not None else {})
        | ({"proposed": True} if r.proposed else {})
        | ({"pages": [_stamp_data(p) for p in r.pages]} if r.pages else {})
        | ({"given": [_file_data(f) for f in r.given]} if r.given else {})
    )


def _withdrawal_data(r: RulingWithdrawal) -> dict[str, Any]:
    return {"id": r.id, "answer": r.answer, "ruling": r.ruling, "reason": r.reason}


def _reread_data(r: Reread) -> dict[str, Any]:
    return {
        "id": r.id,
        "answer": r.answer,
        "state": r.state,
        "node": r.node,
        "reason": r.reason,
    }


def _circle_data(r: RealCircle) -> dict[str, Any]:
    return {
        "id": r.id,
        "answer": r.answer,
        "state": r.state,
        "circle": r.circle,
        "reason": r.reason,
        "proposal": r.proposal,
    }


def _searched_data(r: Searched) -> dict[str, Any]:
    return {
        "id": r.id,
        "answer": r.answer,
        "state": r.state,
        "subject": _subject_data(r.subject),
        "reason": r.reason,
    }


def _suggestion_data(r: Suggestion) -> dict[str, Any]:
    return {
        "id": r.id,
        "answer": r.answer,
        "state": r.state,
        "anchors": [_anchor_data(a) for a in r.anchors],
        "why": r.why,
        "basis": [_basis_data(b) for b in r.basis],
    } | ({"pages": [_stamp_data(p) for p in r.pages]} if r.pages else {})


def _whole_data(r: WholeBlock) -> dict[str, Any]:
    return {
        "id": r.id,
        "answer": r.answer,
        "state": r.state,
        "block": r.block,
        "reason": r.reason,
    }


def _check_data(r: ContributionCheck) -> dict[str, Any]:
    return {
        "id": r.id,
        "answer": r.answer,
        "state": r.state,
        "subject": _subject_data(r.subject),
        "searched": r.searched,
        "found": r.found,
    }


def _suggestion_answer_data(r: SuggestionAnswer) -> dict[str, Any]:
    return {
        "id": r.id,
        "answer": r.answer,
        "suggestion": r.suggestion,
        "status": r.status.value,
        "note": r.note,
    }


def judgment_data(r: Filing) -> dict[str, Any]:
    """A filed record's written form, as its file holds it."""
    if isinstance(r, ContributionCheck):
        return _check_data(r)
    if isinstance(r, Suggestion):
        return _suggestion_data(r)
    if isinstance(r, SuggestionAnswer):
        return _suggestion_answer_data(r)
    if isinstance(r, Reread):
        return _reread_data(r)
    if isinstance(r, RealCircle):
        return _circle_data(r)
    if isinstance(r, Searched):
        return _searched_data(r)
    if isinstance(r, WholeBlock):
        return _whole_data(r)
    if isinstance(r, RulingWithdrawal):
        return _withdrawal_data(r)
    if isinstance(r, AnswerWithdrawal):
        return _answer_withdrawal_data(r)
    if isinstance(r, Ruling):
        return _ruling_data(r)
    if isinstance(r, Judgment):
        return _judgment_data(r)
    if isinstance(r, Finding):
        return _finding_data(r)
    if isinstance(r, WorkSeen):
        return _work_seen_data(r)
    if isinstance(r, WorkAccepted):
        return _accepted_data(r)
    if isinstance(r, RunSeen):
        return _run_seen_data(r)
    if isinstance(r, FileSeen):
        return _file_seen_data(r)
    return _finding_answer_data(r)


def _line(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False) + "\n"


# --- reading -----------------------------------------------------------------


class _Where:
    """Where a record stands, for its errors."""

    def __init__(self, path: Path, line: int) -> None:
        self.path = path
        self.line = line

    def error(self, name: str, what: str) -> RecordError:
        return RecordError(f"{self.path}:{self.line}: field {name}: {what}")


def _take(data: dict[str, Any], name: str, kind: type | tuple[type, ...], at: _Where):
    if name not in data:
        raise at.error(name, "missing")
    value = data[name]
    if kind in (int, float, (int, float)) and isinstance(value, bool):
        raise at.error(name, f"expected a number, got {value!r}")
    if not isinstance(value, kind):
        raise at.error(name, f"expected {_kind_name(kind)}, got {value!r}")
    return value


def _kind_name(kind: type | tuple[type, ...]) -> str:
    if isinstance(kind, tuple):
        return " or ".join(_kind_name(k) for k in kind)
    return "null" if kind is type(None) else kind.__name__


def _strings(value: list[Any], name: str, at: _Where) -> tuple[str, ...]:
    if not all(isinstance(item, str) for item in value):
        raise at.error(name, f"expected strings, got {value!r}")
    return tuple(value)


def _enum[E: StrEnum](cls: type[E], value: str, name: str, at: _Where) -> E:
    try:
        return cls(value)
    except ValueError:
        allowed = ", ".join(member.value for member in cls)
        raise at.error(name, f"{value!r} is not one of {allowed}") from None


def _unknown(data: dict[str, Any], known: set[str], at: _Where) -> None:
    for name in data:
        if name not in known:
            raise at.error(name, "unknown")


def _roles(value: list[Any], name: str, at: _Where) -> tuple[Role, ...]:
    found = []
    for item in value:
        if not isinstance(item, str):
            raise at.error(name, f"expected role names, got {item!r}")
        found.append(_enum(Role, item, name, at))
    return tuple(found)


def _segments(value: list[Any], name: str, at: _Where) -> tuple[Segment, ...]:
    found = []
    for item in value:
        if not isinstance(item, dict):
            raise at.error(name, f"expected objects, got {item!r}")
        for key in item:
            if key not in ("starts", "role"):
                raise at.error(f"{name}.{key}", "unknown")
        starts = item.get("starts")
        if not isinstance(starts, str):
            raise at.error(f"{name}.starts", f"expected str, got {starts!r}")
        role = item.get("role")
        if not isinstance(role, str):
            raise at.error(f"{name}.role", f"expected str, got {role!r}")
        found.append(
            Segment(
                starts,
                None if role == NONE else _enum(Role, role, f"{name}.role", at),
            )
        )
    return tuple(found)


def correction_data(c: CutCorrection) -> dict[str, Any]:
    """A correction as the store writes it, as JSON data (the
    sheet of an update's one session keeps the cut lines its gate applied
    in this form)."""
    return _correction_data(c)


def finding_from_data(data: Any, where: str) -> Finding:
    """A finding from its written form (`judgment_data`); `where` names it
    in an error."""
    return _finding_from(data, _Where(Path(where), 0))


def correction_from_data(data: Any, where: str) -> CutCorrection:
    """`correction_data` read back; `where` names its place in errors.
    Raises `RecordError`."""
    return _correction_from(data, _Where(Path(where), 0))


def _correction_from(data: Any, at: _Where) -> CutCorrection:
    if not isinstance(data, dict):
        raise RecordError(f"{at.path}:{at.line}: expected an object, got {data!r}")
    op = _enum(CutOp, _take(data, "op", str, at), "op", at)
    takes = OP_FIELDS[op]
    _unknown(
        data, {"id", "op", "class", "why", "answer", "evidence", "decision", *takes}, at
    )
    answer = data.get("answer")
    if answer is not None and not isinstance(answer, str):
        raise at.error("answer", f"expected str or null, got {answer!r}")
    fields: dict[str, Any] = {}
    for name in takes:
        if name in ("blocks", "at"):
            fields[name] = _strings(_take(data, name, list, at), name, at)
        elif name == "roles":
            fields[name] = _roles(_take(data, name, list, at), name, at)
        elif name == "segments":
            fields[name] = _segments(_take(data, name, list, at), name, at)
        else:
            fields[name] = _take(data, name, str, at)
    if "evidence" in data:
        fields["evidence"] = _strings(_take(data, "evidence", list, at), "evidence", at)
    if "decision" in data:
        fields["decision"] = _take(data, "decision", str, at)
    if "kind" in fields:
        kinds = GroupKind if op is CutOp.GROUP else PartKind
        fields["kind"] = _enum(kinds, fields["kind"], "kind", at)
    correction = CutCorrection(
        id=_take(data, "id", str, at),
        op=op,
        cls=_enum(CorrectionClass, _take(data, "class", str, at), "class", at),
        why=_take(data, "why", str, at),
        answer=answer,
        **fields,
    )
    for problem in correction.check():
        name, _, what = problem.partition(": ")
        raise at.error(name, what)
    return correction


_ANSWER_FIELDS: dict[str, type | tuple[type, ...]] = {
    "key": str,
    "kind": str,
    "stage": str,
    "template": dict,
    "attempt": int,
    "host": str,
    "model": str,
    "time": str,
    "seconds": (int, float),
    "cost": (int, float),
    "tokens": dict,
    "valid": bool,
    "problems": list,
    "records": list,
    "judges": (str, type(None)),
    "effort": (str, type(None)),
    "profile": (str, type(None)),
}

#: The fields written only where set.
_ANSWER_OPTIONAL: dict[str, type | tuple[type, ...]] = {
    # a person's answer's version
    "version": (str, type(None)),
    # an agent's session, a given answer's reason
    "session": (str, type(None)),
    "reason": (str, type(None)),
    # a review session's round and state
    "review_round": (str, type(None)),
    "state": (str, type(None)),
    # a given answer's file shown, its hash
    "shown": (str, type(None)),
}


def _answer_from(data: Any, at: _Where) -> AnswerRecord:
    if not isinstance(data, dict):
        raise RecordError(f"{at.path}:{at.line}: expected an object, got {data!r}")
    _unknown(
        data,
        {
            *_ANSWER_FIELDS,
            *_ANSWER_OPTIONAL,
            "marks_shown",
            "checked_again",
            "estimate",
            "tokens_by_model",
            "order",
            "tasks",
            "read",
            "call",
        },
        at,
    )
    order = data.get("order")
    if order is not None and (
        not isinstance(order, int) or isinstance(order, bool) or order < 1
    ):
        raise at.error("order", f"expected an order from 1 or absent, got {order!r}")
    tasks = data.get("tasks", [])
    if not isinstance(tasks, list):
        raise at.error("tasks", f"expected a list of keys, got {tasks!r}")
    call = data.get("call")
    if call is not None and not (
        isinstance(call, dict)
        and set(call) == {"tool", "args"}
        and isinstance(call["tool"], str)
        and isinstance(call["args"], dict)
    ):
        raise at.error("call", f"expected a tool and its args or absent, got {call!r}")
    read = data.get("read")
    if read is not None and not isinstance(read, list):
        raise at.error("read", f"expected a list of nodes or absent, got {read!r}")
    estimate = data.get("estimate")
    if estimate is not None and (
        not isinstance(estimate, int | float)
        or isinstance(estimate, bool)
        or estimate < 0
    ):
        raise at.error(
            "estimate", f"expected dollars from 0 or absent, got {estimate!r}"
        )
    marks_shown = data.get("marks_shown")
    if marks_shown is not None and marks_shown is not False:
        raise at.error("marks_shown", f"expected false or absent, got {marks_shown!r}")
    checked_again = data.get("checked_again")
    if checked_again is not None and (
        not isinstance(checked_again, int)
        or isinstance(checked_again, bool)
        or checked_again < 1
    ):
        raise at.error(
            "checked_again",
            f"expected an attempt from 1 or absent, got {checked_again!r}",
        )
    values = {
        name: _take(data, name, kind, at) for name, kind in _ANSWER_FIELDS.items()
    }
    optional = {name: data.get(name) for name in _ANSWER_OPTIONAL}
    for name, value in optional.items():
        if value is not None and not isinstance(value, str):
            raise at.error(name, f"expected str or null, got {value!r}")
    template = values["template"]
    for name in template:
        if name not in ("name", "version", "asks"):
            raise at.error(f"template.{name}", "unknown")
    for name in ("name", "version"):
        if not isinstance(template.get(name), str):
            value = template.get(name)
            raise at.error(f"template.{name}", f"expected str, got {value!r}")
    asks = template.get("asks", {})
    if not isinstance(asks, dict) or not all(
        isinstance(v, int) and not isinstance(v, bool) and v >= 1 for v in asks.values()
    ):
        raise at.error("template.asks", f"expected versions from 1, got {asks!r}")
    tokens = values["tokens"]
    for name, count in tokens.items():
        if not isinstance(count, int) or isinstance(count, bool):
            raise at.error(f"tokens.{name}", f"expected int, got {count!r}")
    by_model = data.get("tokens_by_model", {})
    if not isinstance(by_model, dict):
        raise at.error("tokens_by_model", f"expected an object, got {by_model!r}")
    for model, counts in by_model.items():
        if not isinstance(counts, dict):
            raise at.error(
                f"tokens_by_model.{model}", f"expected an object, got {counts!r}"
            )
        for name, count in counts.items():
            if not isinstance(count, int) or isinstance(count, bool):
                raise at.error(
                    f"tokens_by_model.{model}.{name}", f"expected int, got {count!r}"
                )
    return AnswerRecord(
        key=values["key"],
        kind=values["kind"],
        stage=values["stage"],
        template=TemplateRef(
            template["name"], template["version"], tuple(sorted(asks.items()))
        ),
        attempt=values["attempt"],
        host=values["host"],
        model=values["model"],
        time=values["time"],
        seconds=float(values["seconds"]),
        cost=float(values["cost"]),
        tokens=dict(tokens),
        valid=values["valid"],
        problems=_strings(values["problems"], "problems", at),
        records=_strings(values["records"], "records", at),
        judges=values["judges"],
        effort=values["effort"],
        profile=values["profile"],
        version=optional["version"],
        session=optional["session"],
        reason=optional["reason"],
        marks_shown=marks_shown,
        review_round=optional["review_round"],
        state=optional["state"],
        shown=optional["shown"],
        checked_again=checked_again,
        estimate=None if estimate is None else float(estimate),
        tokens_by_model={m: dict(c) for m, c in by_model.items()},
        order=order,
        tasks=_strings(tasks, "tasks", at),
        read=None if read is None else _strings(read, "read", at),
        call=call,
    )


def _optional_str(data: dict[str, Any], name: str, at: _Where) -> str | None:
    value = data.get(name)
    if value is not None and not isinstance(value, str):
        raise at.error(name, f"expected str or null, got {value!r}")
    return value


def _record_start(data: Any, known: set[str], at: _Where) -> tuple[str, str | None]:
    """A record's `id` and `answer`, after its fields are checked known."""
    if not isinstance(data, dict):
        raise RecordError(f"{at.path}:{at.line}: expected an object, got {data!r}")
    _unknown(data, {"id", "answer", *known}, at)
    return _take(data, "id", str, at), _optional_str(data, "answer", at)


def _entry_from(data: Any, at: _Where) -> Entry:
    rid, answer = _record_start(
        data, {"block", "label", "words", "role", "standard"}, at
    )
    role = _take(data, "role", str, at)
    return Entry(
        id=rid,
        answer=answer,
        block=_take(data, "block", str, at),
        label=_take(data, "label", str, at),
        words=_take(data, "words", str, at),
        role=None if role == NONE else _enum(Role, role, "role", at),
        standard=_take(data, "standard", bool, at),
    )


def _target_from(data: Any, at: _Where) -> EdgeTarget:
    if not isinstance(data, dict) or len(data) != 1:
        raise at.error(
            "target", f"expected one of block, group, file or scope, got {data!r}"
        )
    ((kind, name),) = data.items()
    if not isinstance(name, str):
        raise at.error(f"target.{kind}", f"expected str, got {name!r}")
    return EdgeTarget(_enum(TargetKind, kind, "target", at), name)


def _edge_from(data: Any, at: _Where) -> Edge:
    rid, answer = _record_start(
        data,
        {
            "source",
            "kind",
            "target",
            "parts",
            "evidence",
            "mark",
            "where_used",
            "note",
            "partial",
            "generalizes",
            "claims",
            "source_claims",
            "restates_none",
            "assumed",
            "source_parts",
        },
        at,
    )
    mark = _optional_str(data, "mark", at)
    return Edge(
        id=rid,
        answer=answer,
        source=_take(data, "source", str, at),
        kind=_enum(EdgeKind, _take(data, "kind", str, at), "kind", at),
        target=_target_from(_take(data, "target", dict, at), at),
        evidence=_strings(_take(data, "evidence", list, at), "evidence", at),
        parts=_strings(_take(data, "parts", list, at), "parts", at),
        mark=None if mark is None else _enum(Mark, mark, "mark", at),
        where_used=_take(data, "where_used", bool, at),
        note=_optional_str(data, "note", at),
        partial=_partial(data, at),
        generalizes="generalizes" in data and _take(data, "generalizes", bool, at),
        claims=_claims(data, "claims", at),
        source_claims=_claims(data, "source_claims", at),
        restates_none="restates_none" in data
        and _take(data, "restates_none", bool, at),
        assumed="assumed" in data and _take(data, "assumed", bool, at),
        source_parts=_strings(data.get("source_parts", []), "source_parts", at),
    )


def _claims(data: dict[str, Any], key: str, at: _Where) -> tuple[str, ...]:
    """A record's named standings' words, none where it names none."""
    return _strings(data.get(key, []), key, at)


def _partial(data: dict[str, Any], at: _Where) -> Partial | None:
    """A record's `partial`, None where it has none."""
    found = _optional_str(data, "partial", at)
    return None if found is None else _enum(Partial, found, "partial", at)


def _self_from(data: Any, at: _Where) -> SelfMark:
    rid, answer = _record_start(
        data, {"block", "evidence", "note", "partial", "claims"}, at
    )
    return SelfMark(
        id=rid,
        answer=answer,
        block=_take(data, "block", str, at),
        evidence=_strings(_take(data, "evidence", list, at), "evidence", at),
        note=_optional_str(data, "note", at),
        partial=_partial(data, at),
        claims=_claims(data, "claims", at),
    )


def _standing_from(data: Any, at: _Where) -> Confirmed:
    rid, answer = _record_start(
        data, {"block", "claim", "status", "note", "declared", "handle"}, at
    )
    declared = _optional_str(data, "declared", at)
    return Standing(
        id=rid,
        answer=answer,
        block=_take(data, "block", str, at),
        claim=_take(data, "claim", str, at),
        status=_enum(Status, _take(data, "status", str, at), "status", at),
        note=_optional_str(data, "note", at),
        declared=None
        if declared is None
        else _enum(Declared, declared, "declared", at),
        handle=_optional_str(data, "handle", at),
    )


def _frame_from(data: Any, at: _Where) -> Frame:
    rid, answer = _record_start(data, {"block"}, at)
    return Frame(id=rid, answer=answer, block=_take(data, "block", str, at))


def _delta_from(data: Any, at: _Where) -> Delta:
    rid, answer = _record_start(data, {"block", "entry", "class", "note"}, at)
    return Delta(
        id=rid,
        answer=answer,
        block=_take(data, "block", str, at),
        entry=_optional_str(data, "entry", at),
        cls=_enum(ImpactClass, _take(data, "class", str, at), "class", at),
        note=_take(data, "note", str, at),
    )


def _order_from(data: Any, at: _Where) -> LineOrder:
    rid, answer = _record_start(data, {"owner", "kinds"}, at)
    return LineOrder(
        id=rid,
        answer=answer,
        owner=_take(data, "owner", str, at),
        kinds=tuple(
            _enum(LineKind, k, "kinds", at) for k in _take(data, "kinds", list, at)
        ),
    )


def _flag_from(data: Any, at: _Where) -> Flag:
    rid, answer = _record_start(data, {"blocks", "signal", "evidence", "note"}, at)
    return Flag(
        id=rid,
        answer=answer,
        blocks=_strings(_take(data, "blocks", list, at), "blocks", at),
        signal=_enum(Signal, _take(data, "signal", str, at), "signal", at),
        evidence=_strings(_take(data, "evidence", list, at), "evidence", at),
        note=_take(data, "note", str, at),
    )


def _vocabulary_from(data: Any, at: _Where) -> VocabularyEntry:
    rid, answer = _record_start(
        data,
        {
            "namespace",
            "name",
            "shape",
            "declaration",
            "opens",
            "empty",
            "labelled",
            "index",
            "count",
            "kind",
            "words",
            "evidence",
        },
        at,
    )
    return VocabularyEntry(
        id=rid,
        answer=answer,
        namespace=_take(data, "namespace", str, at),
        name=_take(data, "name", str, at),
        shape=_take(data, "shape", str, at),
        declaration=_optional_str(data, "declaration", at),
        opens=_optional_str(data, "opens", at),
        empty=_take(data, "empty", bool, at),
        labelled=_take(data, "labelled", bool, at),
        index=_take(data, "index", int, at),
        count=_take(data, "count", int, at),
        kind=_take(data, "kind", str, at),
        words=_strings(_take(data, "words", list, at), "words", at),
        evidence=_take(data, "evidence", str, at),
    )


def _decline_from(data: Any, at: _Where) -> Decline:
    rid, answer = _record_start(data, {"file", "offset", "line"}, at)
    return Decline(
        id=rid,
        answer=answer,
        file=_take(data, "file", str, at),
        offset=_take(data, "offset", int, at),
        line=_take(data, "line", str, at),
    )


def _lineage_from(data: Any, at: _Where) -> LineageLink:
    rid, answer = _record_start(
        data, {"old", "new", "cls", "tier", "heir", "moved"}, at
    )
    return LineageLink(
        id=rid,
        answer=answer,
        old=_strings(_take(data, "old", list, at), "old", at),
        new=_strings(_take(data, "new", list, at), "new", at),
        cls=_take(data, "cls", str, at),
        tier=_take(data, "tier", str, at),
        heir=_optional_str(data, "heir", at),
        moved=_take(data, "moved", bool, at),
    )


def _object(data: Any, name: str, known: set[str], at: _Where) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise at.error(name, f"expected an object, got {data!r}")
    for key in data:
        if key not in known:
            raise at.error(f"{name}.{key}", "unknown")
    return data


def _subject_from(data: Any, at: _Where) -> Subject:
    data = _object(data, "subject", {"node", "target", "words", "work", "file"}, at)
    return Subject(
        node=_take(data, "node", str, at),
        target=_optional_str(data, "target", at),
        words=_optional_str(data, "words", at),
        work=_optional_str(data, "work", at),
        file=_optional_str(data, "file", at),
    )


def _stamp_from(data: Any, name: str, at: _Where) -> PageStamp:
    data = _object(data, name, {"key", "page_index", "read_status"}, at)
    return PageStamp(
        key=_take(data, "key", str, at),
        page_index=_take(data, "page_index", int, at),
        read_status=_enum(
            ReadStatus, _take(data, "read_status", str, at), f"{name}.read_status", at
        ),
    )


def _anchor_from(data: Any, at: _Where) -> Anchor:
    data = _object(data, "anchors", {"kind", "node", "words", "work", "file"}, at)
    return Anchor(
        kind=_enum(AnchorKind, _take(data, "kind", str, at), "anchors.kind", at),
        node=_optional_str(data, "node", at),
        words=_optional_str(data, "words", at),
        work=_optional_str(data, "work", at),
        file=_optional_str(data, "file", at),
    )


def _basis_from(data: Any, at: _Where) -> BasisElement:
    data = _object(data, "basis", {"kind", "name", "text", "parts", "edge"}, at)
    edge = data.get("edge")
    if edge is not None and (
        not isinstance(edge, list)
        or len(edge) != 2
        or not all(isinstance(e, str) for e in edge)
    ):
        raise at.error("basis.edge", f"expected [kind, target] or null, got {edge!r}")
    return BasisElement(
        kind=_enum(BasisKind, _take(data, "kind", str, at), "basis.kind", at),
        name=_take(data, "name", str, at),
        text=_optional_str(data, "text", at),
        parts=_strings(_take(data, "parts", list, at), "basis.parts", at),
        edge=None if edge is None else (edge[0], edge[1]),
    )


def _judgment_from(data: Any, at: _Where) -> Judgment:
    rid, answer = _record_start(
        data,
        {
            "state",
            "aspect",
            "subject",
            "verdict",
            "depth",
            "evidence",
            "basis",
            "reaffirms",
            "pages",
            "adopts",
            "run",
            "values",
            "code",
            "unread",
            "retracted",
            "given",
            "proposed",
        },
        at,
    )
    aspect = _enum(Aspect, _take(data, "aspect", str, at), "aspect", at)
    if aspect not in JUDGED_ASPECTS:
        raise at.error("aspect", f"{aspect.value!r} is a finding's aspect alone")
    return Judgment(
        id=rid,
        answer=answer,
        state=_take(data, "state", str, at),
        aspect=aspect,
        subject=_subject_from(_take(data, "subject", dict, at), at),
        verdict=_enum(Verdict, _take(data, "verdict", str, at), "verdict", at),
        depth=_enum(Depth, _take(data, "depth", str, at), "depth", at),
        evidence=_take(data, "evidence", str, at),
        basis=tuple(_basis_from(b, at) for b in _take(data, "basis", list, at)),
        reaffirms=_optional_str(data, "reaffirms", at),
        pages=tuple(_stamp_from(p, "pages", at) for p in data.get("pages", [])),
        adopts=_optional_str(data, "adopts", at),
        run=None if data.get("run") is None else _run_from(data["run"], at),
        values=tuple(_value_pair_from(v, at) for v in data.get("values", [])),
        code=tuple(str(c) for c in data.get("code", [])),
        unread=tuple(_unread_from(w, at) for w in data.get("unread", [])),
        retracted=tuple(_basis_from(b, at) for b in data.get("retracted", [])),
        given=tuple(_file_from(f, "given", at) for f in data.get("given", [])),
        proposed="proposed" in data and _take(data, "proposed", bool, at),
    )


def _file_from(data: Any, name: str, at: _Where) -> FileStamp:
    data = _object(data, name, {"key", "read_status", "file", "content"}, at)
    return FileStamp(
        key=_take(data, "key", str, at),
        read_status=_enum(
            ReadStatus, _take(data, "read_status", str, at), f"{name}.read_status", at
        ),
        file=_take(data, "file", str, at),
        content=_optional_str(data, "content", at) or "",
    )


def _file_seen_from(data: Any, at: _Where) -> FileSeen:
    rid, answer = _record_start(data, {"file"}, at)
    return FileSeen(rid, answer, _file_from(_take(data, "file", dict, at), "file", at))


def _unread_from(data: Any, at: _Where) -> WorkUnread:
    data = _object(data, "unread", {"key", "read_status"}, at)
    return WorkUnread(
        key=_take(data, "key", str, at),
        read_status=_enum(
            ReadStatus, _take(data, "read_status", str, at), "unread.read_status", at
        ),
    )


def _finding_from(data: Any, at: _Where) -> Finding:
    rid, answer = _record_start(
        data,
        {
            "state",
            "aspect",
            "severity",
            "problem",
            "fix",
            "anchors",
            "basis",
            "pages",
            "reaffirms",
            "evidence",
            "retracted",
            "reading",
        },
        at,
    )
    severity = _take(data, "severity", int, at)
    if severity not in (1, 2, 3):
        raise at.error("severity", f"expected 1, 2 or 3, got {severity!r}")
    anchors = tuple(_anchor_from(a, at) for a in _take(data, "anchors", list, at))
    if not anchors:
        raise at.error("anchors", "a finding needs one anchor at least")
    return Finding(
        id=rid,
        answer=answer,
        state=_take(data, "state", str, at),
        aspect=_enum(Aspect, _take(data, "aspect", str, at), "aspect", at),
        severity=severity,
        problem=_take(data, "problem", str, at),
        fix=_optional_str(data, "fix", at),
        anchors=anchors,
        basis=tuple(_basis_from(b, at) for b in _take(data, "basis", list, at)),
        pages=tuple(_stamp_from(p, "pages", at) for p in data.get("pages", [])),
        reaffirms=_optional_str(data, "reaffirms", at),
        evidence=_optional_str(data, "evidence", at),
        retracted=tuple(_basis_from(b, at) for b in data.get("retracted", [])),
        reading="reading" in data and _take(data, "reading", bool, at),
    )


def _finding_answer_from(data: Any, at: _Where) -> FindingAnswer:
    rid, answer = _record_start(
        data, {"finding", "status", "note", "of", "proposed"}, at
    )
    return FindingAnswer(
        id=rid,
        answer=answer,
        finding=_take(data, "finding", str, at),
        status=_enum(FindingStatus, _take(data, "status", str, at), "status", at),
        note=_optional_str(data, "note", at),
        of=_optional_str(data, "of", at),
        proposed="proposed" in data and _take(data, "proposed", bool, at),
    )


def _answer_withdrawal_from(data: Any, at: _Where) -> AnswerWithdrawal:
    rid, answer = _record_start(data, {"finding_answer", "reason"}, at)
    reason = _take(data, "reason", str, at)
    if not reason.strip():
        raise at.error("reason", "a withdrawal gives its reason")
    return AnswerWithdrawal(
        id=rid,
        answer=answer,
        finding_answer=_take(data, "finding_answer", str, at),
        reason=reason,
    )


def _ruling_from(data: Any, at: _Where) -> Ruling:
    rid, answer = _record_start(
        data,
        {
            "state",
            "subject",
            "kind",
            "reason",
            "reaffirms",
            "severity",
            "fix",
            "proposed",
            "pages",
            "given",
        },
        at,
    )
    subject = _subject_from(_take(data, "subject", dict, at), at)
    if subject.words is None:
        raise at.error("subject", "a ruling is on a claim: its words are needed")
    reason = _take(data, "reason", str, at)
    if not reason.strip():
        raise at.error("reason", "a ruling gives its reason")
    kind = _enum(RulingKind, _take(data, "kind", str, at), "kind", at)
    severity = _take(data, "severity", int, at) if "severity" in data else None
    fix = _optional_str(data, "fix", at)
    if kind is RulingKind.OWED and (severity is None or fix is None):
        raise at.error("severity", "an owed ruling carries its severity and its fix")
    if kind is not RulingKind.OWED and (severity is not None or fix is not None):
        raise at.error("severity", "only an owed ruling carries a severity and a fix")
    if severity is not None and severity not in (1, 2, 3):
        raise at.error("severity", f"expected 1, 2 or 3, got {severity!r}")
    if fix is not None and not fix.strip():
        raise at.error("fix", "a fix says what would discharge it")
    return Ruling(
        id=rid,
        answer=answer,
        state=_take(data, "state", str, at),
        subject=subject,
        kind=kind,
        reason=reason,
        reaffirms=_optional_str(data, "reaffirms", at),
        severity=severity,
        fix=fix,
        proposed="proposed" in data and _take(data, "proposed", bool, at),
        pages=tuple(_stamp_from(p, "pages", at) for p in data.get("pages", [])),
        given=tuple(_file_from(f, "given", at) for f in data.get("given", [])),
    )


def _check_from(data: Any, at: _Where) -> ContributionCheck:
    rid, answer = _record_start(data, {"state", "subject", "searched", "found"}, at)
    subject = _subject_from(_take(data, "subject", dict, at), at)
    if subject.words is None:
        raise at.error("subject", "a check quotes the contribution's words")
    return ContributionCheck(
        id=rid,
        answer=answer,
        state=_take(data, "state", str, at),
        subject=subject,
        searched=_take(data, "searched", str, at),
        found=_take(data, "found", str, at),
    )


def _suggestion_from(data: Any, at: _Where) -> Suggestion:
    rid, answer = _record_start(data, {"state", "anchors", "why", "basis", "pages"}, at)
    anchors = tuple(_anchor_from(a, at) for a in _take(data, "anchors", list, at))
    if not any(a.kind is AnchorKind.WORK for a in anchors):
        raise at.error("anchors", "a suggestion names the work it suggests")
    why = _take(data, "why", str, at)
    if not why.strip():
        raise at.error("why", "a suggestion says why a reader might expect it")
    return Suggestion(
        id=rid,
        answer=answer,
        state=_take(data, "state", str, at),
        anchors=anchors,
        why=why,
        basis=tuple(_basis_from(b, at) for b in _take(data, "basis", list, at)),
        pages=tuple(_stamp_from(p, "pages", at) for p in data.get("pages", [])),
    )


def _suggestion_answer_from(data: Any, at: _Where) -> SuggestionAnswer:
    rid, answer = _record_start(data, {"suggestion", "status", "note"}, at)
    return SuggestionAnswer(
        id=rid,
        answer=answer,
        suggestion=_take(data, "suggestion", str, at),
        status=_enum(SuggestionStatus, _take(data, "status", str, at), "status", at),
        note=_optional_str(data, "note", at),
    )


def _withdrawal_from(data: Any, at: _Where) -> RulingWithdrawal:
    rid, answer = _record_start(data, {"ruling", "reason"}, at)
    reason = _take(data, "reason", str, at)
    if not reason.strip():
        raise at.error("reason", "a withdrawal gives its reason")
    return RulingWithdrawal(
        id=rid, answer=answer, ruling=_take(data, "ruling", str, at), reason=reason
    )


def _reread_from(data: Any, at: _Where) -> Reread:
    rid, answer = _record_start(data, {"state", "node", "reason"}, at)
    reason = _take(data, "reason", str, at)
    if not reason.strip():
        raise at.error("reason", "a reread gives its reason")
    return Reread(
        id=rid,
        answer=answer,
        state=_take(data, "state", str, at),
        node=_take(data, "node", str, at),
        reason=reason,
    )


def _circle_from(data: Any, at: _Where) -> RealCircle:
    rid, answer = _record_start(data, {"state", "circle", "reason", "proposal"}, at)
    reason = _take(data, "reason", str, at)
    if not reason.strip():
        raise at.error("reason", "a circle read real gives its reason")
    proposal = _take(data, "proposal", str, at)
    if not proposal.strip():
        raise at.error(
            "proposal", "a circle read real gives what a person should change"
        )
    return RealCircle(
        id=rid,
        answer=answer,
        state=_take(data, "state", str, at),
        circle=_take(data, "circle", str, at),
        reason=reason,
        proposal=proposal,
    )


def _searched_from(data: Any, at: _Where) -> Searched:
    rid, answer = _record_start(data, {"state", "subject", "reason"}, at)
    subject = _subject_from(_take(data, "subject", dict, at), at)
    if subject.words is None:
        raise at.error("subject", "a search is on a claim: its words are needed")
    reason = _take(data, "reason", str, at)
    if not reason.strip():
        raise at.error("reason", "a search gives its reason")
    return Searched(
        id=rid,
        answer=answer,
        state=_take(data, "state", str, at),
        subject=subject,
        reason=reason,
    )


def _whole_from(data: Any, at: _Where) -> WholeBlock:
    rid, answer = _record_start(data, {"state", "block", "reason"}, at)
    reason = _take(data, "reason", str, at)
    if not reason.strip():
        raise at.error("reason", "a block read whole gives its reason")
    return WholeBlock(
        id=rid,
        answer=answer,
        state=_take(data, "state", str, at),
        block=_take(data, "block", str, at),
        reason=reason,
    )


def _run_from(data: Any, at: _Where) -> RunStamp:
    data = _object(
        data,
        "run",
        {"key", "blob", "runner", "arguments", "directory", "status", "output"},
        at,
    )
    return RunStamp(
        key=_take(data, "key", str, at),
        blob=_take(data, "blob", str, at),
        runner=_take(data, "runner", str, at),
        arguments=_strings(_take(data, "arguments", list, at), "run.arguments", at),
        directory=_take(data, "directory", str, at),
        status=_enum(RunStatus, _take(data, "status", str, at), "run.status", at),
        output=_take(data, "output", str, at),
    )


def _value_pair_from(data: Any, at: _Where) -> ValuePair:
    data = _object(data, "values", {"text", "output"}, at)
    return ValuePair(_take(data, "text", str, at), _take(data, "output", str, at))


def _run_seen_from(data: Any, at: _Where) -> RunSeen:
    rid, answer = _record_start(data, {"run"}, at)
    return RunSeen(rid, answer, _run_from(_take(data, "run", dict, at), at))


def _work_seen_from(data: Any, at: _Where) -> WorkSeen:
    rid, answer = _record_start(
        data, {"key", "doi", "publication", "source", "records"}, at
    )
    return WorkSeen(
        id=rid,
        answer=answer,
        key=_take(data, "key", str, at),
        doi=_optional_str(data, "doi", at),
        publication=_enum(
            Publication, _take(data, "publication", str, at), "publication", at
        ),
        source=_take(data, "source", str, at),
        records=tuple(_version_from(v, at) for v in _take(data, "records", list, at)),
    )


def _version_from(data: Any, at: _Where) -> VersionOfRecord:
    data = _object(
        data, "records", {"venue", "year", "doi", "url", "source", "match"}, at
    )
    return VersionOfRecord(
        venue=_take(data, "venue", str, at),
        year=_take(data, "year", str, at),
        doi=_optional_str(data, "doi", at),
        url=_optional_str(data, "url", at),
        source=_take(data, "source", str, at),
        match=_enum(Match, _take(data, "match", str, at), "match", at),
    )


def _accepted_from(data: Any, at: _Where) -> WorkAccepted:
    rid, answer = _record_start(data, {"key", "reason", "withdrawn"}, at)
    reason = _take(data, "reason", str, at)
    if not reason.strip():
        raise at.error("reason", "a work accepted gives its reason")
    return WorkAccepted(
        id=rid,
        answer=answer,
        key=_take(data, "key", str, at),
        reason=reason,
        withdrawn=_take(data, "withdrawn", bool, at),
    )


@dataclass(frozen=True, slots=True)
class _Kind:
    """One kind of record: its name in an answer's entry, the field that
    holds it (`EdgeRecords`, `JudgmentRecords`), and its written form both
    ways."""

    kind: RecordKind
    field: str
    data: Any
    read: Any


_CORRECTIONS = _Kind(
    RecordKind.CUT_CORRECTIONS, "corrections", _correction_data, _correction_from
)
#: The edge stage's record kinds, in the order the resolver numbers them.
EDGE_KINDS = (
    _Kind(RecordKind.ENTRIES, "entries", _entry_data, _entry_from),
    _Kind(RecordKind.EDGES, "edges", _edge_data, _edge_from),
    _Kind(RecordKind.SELF_MARKS, "selfs", _self_data, _self_from),
    _Kind(RecordKind.STANDINGS, "standings", _standing_data, _standing_from),
    _Kind(RecordKind.FRAME, "frame", _frame_data, _frame_from),
    _Kind(RecordKind.DELTAS, "deltas", _delta_data, _delta_from),
    _Kind(RecordKind.LINE_ORDERS, "orders", _order_data, _order_from),
)
_FLAGS = _Kind(RecordKind.FLAGS, "flags", _flag_data, _flag_from)
_LINEAGE = _Kind(RecordKind.LINEAGE, "lineage", _lineage_data, _lineage_from)
_VOCABULARY = _Kind(
    RecordKind.VOCABULARY, "vocabulary", _vocabulary_data, _vocabulary_from
)
_DECLINES = _Kind(RecordKind.DECLINES, "declines", _decline_data, _decline_from)
#: The filings' record kinds, in the order the resolver numbers
#: them.
JUDGMENT_KINDS = (
    _Kind(RecordKind.JUDGMENTS, "judgments", _judgment_data, _judgment_from),
    _Kind(RecordKind.FINDINGS, "findings", _finding_data, _finding_from),
    _Kind(
        RecordKind.FINDING_ANSWERS,
        "answers",
        _finding_answer_data,
        _finding_answer_from,
    ),
    _Kind(
        RecordKind.FINDING_ANSWER_WITHDRAWALS,
        "answer_withdrawals",
        _answer_withdrawal_data,
        _answer_withdrawal_from,
    ),
    _Kind(RecordKind.WORKS_SEEN, "works", _work_seen_data, _work_seen_from),
    _Kind(RecordKind.WORKS_ACCEPTED, "accepted", _accepted_data, _accepted_from),
    _Kind(RecordKind.RUNS_SEEN, "runs", _run_seen_data, _run_seen_from),
    _Kind(RecordKind.RULINGS, "rulings", _ruling_data, _ruling_from),
    _Kind(
        RecordKind.RULING_WITHDRAWALS, "withdrawals", _withdrawal_data, _withdrawal_from
    ),
    _Kind(RecordKind.REREADS, "rereads", _reread_data, _reread_from),
    _Kind(RecordKind.CIRCLES, "circles", _circle_data, _circle_from),
    _Kind(RecordKind.SUGGESTIONS, "suggestions", _suggestion_data, _suggestion_from),
    _Kind(
        RecordKind.SUGGESTION_ANSWERS,
        "suggestion_answers",
        _suggestion_answer_data,
        _suggestion_answer_from,
    ),
    _Kind(RecordKind.CONTRIBUTION_CHECKS, "checks", _check_data, _check_from),
    _Kind(RecordKind.FILES_SEEN, "files", _file_seen_data, _file_seen_from),
    _Kind(RecordKind.SEARCHES, "searches", _searched_data, _searched_from),
    _Kind(RecordKind.WHOLE_BLOCKS, "wholes", _whole_data, _whole_from),
)


@dataclass(frozen=True, slots=True)
class _Reach:
    """How far the record was read: the bytes its whole lines took, how
    many lines they were, and those bytes' digest. A file that still begins
    with them was only appended to since (every write appends)."""

    end: int
    lines: int
    digest: str


def _read_on(
    path: Path, reaches: Sequence[_Reach] = ()
) -> tuple[list[tuple[int, str]], _Reach, _Reach | None]:
    """A file's whole lines after the furthest of `reaches` the file still
    begins with, numbered from 1, else all of them; how far its whole lines
    now reach; and the reach it read on from (None: from its start). A last
    line without its newline was cut short by a crash, and is left out."""
    data = path.read_bytes()
    digest = hashlib.sha256()
    begun: _Reach | None = None
    for reach in sorted(reaches, key=lambda r: r.end, reverse=True):
        if len(data) < reach.end:
            continue
        digest = hashlib.sha256(memoryview(data)[: reach.end])
        if digest.hexdigest() == reach.digest:
            begun = reach
            break
    if begun is None:
        digest = hashlib.sha256()
    begin, count = (0, 0) if begun is None else (begun.end, begun.lines)
    end = max(data.rfind(b"\n") + 1, begin)
    digest.update(memoryview(data)[begin:end])
    text = data[begin:end].decode("utf-8")
    if "\r" in text:
        # a checkout that turned the line ends into CRLF (no entry holds a
        # bare CR: JSON escapes it)
        text = text.replace("\r\n", "\n").replace("\r", "\n")
    found = text.split("\n")[:-1]
    lines = [(n, line) for n, line in enumerate(found, count + 1) if line.strip()]
    return lines, _Reach(end, count + len(found), digest.hexdigest()), begun


# --- the store ---------------------------------------------------------------

#: The paper's record: every entry, one a line.
RECORD = "record.jsonl"
#: Where, beside a person's keys, the tip this machine last read of each
#: record is kept (`RecordStore._held_tip`)
TIPS = "tips"
#: How git merges the record: each entry names its parents, and two
#: machines' appends are both kept, in an order nothing reads: what was
#: made apart is read by ancestry (`docs/architecture.md` § 4).
GITATTRIBUTES = f"{RECORD} merge=union\n"
#: The entries' format: each entry names the one it was written in.
FORMAT = 2
LOCK = ".lock"


class EntryKind(StrEnum):
    """What an entry of the record holds."""

    #: One attempt's answer and every record made from it.
    ANSWER = "answer"
    #: One kept state: its row, its lines' change against its parent's
    #: (`state_lines`), and the texts and blobs it keeps first.
    STATE = "state"
    #: One epoch's paper key, wrapped to each member's reading key and to
    #: the recovery key. Its body is not sealed; each stanza is, to its
    #: reader.
    GRANT = "grant"
    #: A person's word on the record as they saw it, signed: its heads
    #: (its parents) and every entry under them stand confirmed, but the
    #: filings it names dropped (`docs/architecture.md` § 4).
    CONFIRMATION = "confirmation"
    #: What a share gives its reader: in a share's own file alone, never in
    #: a paper's record.
    SHARE = "share"
    #: A share its writer sent: the share's entry, its round, its reader
    #: and the state it was cut from.
    SENT = "sent"
    #: A reviewer's review of a share, signed: in a review's own file, and,
    #: as the journal passed it, in the author's record.
    REVIEW = "review"


def is_persons_word(answer: AnswerRecord) -> bool:
    """Whether `answer` is a person's word (a filing, or an edit of a
    stage's file): one that takes effect only under the person's
    signature (`docs/architecture.md` § 4)."""
    return answer.host == PERSON


def _drops(loaded: _Loaded, dropped: Sequence[str]) -> list[str]:
    """The entries of the person's pending answers `dropped` names, each by
    its answer's key or its entry's hash (a prefix of either will do). A
    key names every pending attempt of it: one answer given on two joined
    copies is one answer, dropped whole."""
    pending = loaded.answered().pending
    drops: list[str] = []
    for name in dropped:
        found = [
            d
            for d, x in pending.items()
            if is_persons_word(x.answer)
            and (d.startswith(name) or x.answer.key.startswith(name))
        ]
        if not found or len({pending[d].answer.key for d in found}) != 1:
            raise RecordError(
                f"{name!r} names {len(found) or 'no'} pending answers of yours;"
                " name one of `axiomation confirm`'s list"
            )
        drops += found
    return drops


def _confirmation_message(
    writer: str, heads: Sequence[str], dropped: Sequence[str]
) -> bytes:
    """What a confirmation's signature signs: its writer, its heads and
    the entries it drops, by hash."""
    return (
        "axiomation confirmation\n"
        + _compact({"writer": writer, "heads": list(heads), "dropped": list(dropped)})
    ).encode("utf-8")


def _grant_message(
    writer: str, parents: Sequence[str], body: Mapping[str, Any]
) -> bytes:
    """What a grant's signature signs: its writer, its parents and its
    whole body but the signature; apart from a confirmation's, so that a
    confirmation is never read as a grant's word or a grant's as one."""
    unsigned = {k: v for k, v in body.items() if k != "signature"}
    return (
        "axiomation grant\n"
        + _compact({"writer": writer, "parents": list(parents), "body": unsigned})
    ).encode("utf-8")


def _document_message(writer: str, entry: str) -> bytes:
    """What a document's signature signs (`sealed_document`): apart from
    a record's confirmation, so a document's lines appended to a record
    confirm nothing there."""
    return (
        "axiomation document\n" + _compact({"writer": writer, "entry": entry})
    ).encode("utf-8")


@dataclass(frozen=True)
class Confirmed:
    """Where the record's entries stand under the person's word: those a
    confirmation covers, those it dropped, and each confirmation refused
    with why."""

    covered: frozenset[str] = frozenset()
    dropped: frozenset[str] = frozenset()
    refused: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class PendingEntry:
    """An answer no confirmation covers yet: its entry's hash, the answer,
    and whether it is a person's filing, without effect until one does."""

    entry: str
    answer: AnswerRecord
    binds: bool


def _confirmed(loaded: _Loaded) -> Confirmed:
    """The record's standing. A confirmation is checked against the
    signing key its writer has in the grant it stands on (`_Loaded.on`),
    a grant trusted by its own signature, never by a confirmation over it.
    One that holds covers every entry under it; newest first, so an older
    one it covers is not checked again. A drop holds only for an entry no
    earlier confirmation that held covered."""
    covered: set[str] = set()
    why: dict[str, str] = {}
    for digest, writer, parents, body in reversed(loaded.confirmations):
        if digest in covered:
            continue
        on = [loaded.grant(g) for g in loaded.on(digest)]
        signers = [
            m.signer
            for g in on
            if (m := g.members.get(writer)) is not None and m.signer is not None
        ]
        if not signers:
            why[digest] = f"its writer {writer} is no signing member of its grant"
            continue
        dropped = body.get("dropped", ())
        if not keys.verified(
            signers[0],
            writer,
            _confirmation_message(writer, parents, dropped),
            str(body.get("signature", "")),
        ):
            why[digest] = "its signature does not hold"
            continue
        covered |= _ancestors(loaded, digest)
    refused = {d: w for d, w in why.items() if d not in covered}
    # one a later confirmation covers holds by that one's word
    holding = [
        (digest, _ancestors(loaded, digest), body.get("dropped", ()))
        for digest, _, _, body in loaded.confirmations
        if digest in covered
    ]
    dropped = {
        d
        for digest, under, drops in holding
        for d in drops
        if not any(
            d in earlier and other in under and other != digest
            for other, earlier, _ in holding
        )
    }
    return Confirmed(
        frozenset(covered),
        frozenset(dropped),
        tuple((d, refused[d]) for d, *_ in loaded.confirmations if d in refused),
    )


def _members(loaded: _Loaded, grants: Sequence[str]) -> dict[str, keys.Member]:
    """The members every one of `grants` names, by id."""
    if not grants:
        return {}
    found = dict(loaded.grant(grants[0]).members)
    for other in grants[1:]:
        found = {i: m for i, m in found.items() if i in loaded.grant(other).members}
    return found


def _ancestors(loaded: _Loaded, digest: str) -> set[str]:
    """`digest` and every entry under it."""
    found: set[str] = set()
    stack = [digest]
    while stack:
        at = stack.pop()
        if at not in found:
            found.add(at)
            stack.extend(loaded.parents.get(at, ()))
    return found


@dataclass(frozen=True)
class Grant:
    """A grant as loaded: its entry, its epoch, its members by id and the
    recovery key's public half."""

    entry: str
    epoch: int
    members: dict[str, keys.Member]
    recovery: str


def _grant_body(
    epoch: int,
    paper_key: bytes,
    members: Sequence[keys.Member],
    recovery: str,
    added: Sequence[keys.Member] = (),
    earlier: Sequence[tuple[int, bytes]] = (),
) -> dict[str, Any]:
    """A grant's body: `paper_key` wrapped to each member's reading key and
    to the recovery key, and the members named; and each `earlier` epoch's
    paper key wrapped to each member `added`, who reads the paper's whole
    record."""
    readers = [m.reading for m in members] + [keys.public_from(recovery)]
    return {
        "epoch": epoch,
        "stanzas": [keys.wrapped(paper_key, to) for to in readers],
        "earlier": [
            {"epoch": e, **keys.wrapped(key, m.reading)}
            for e, key in earlier
            for m in added
        ],
        "members": {
            m.id: {"reading": keys.public_text(m.reading), "signer": m.signer}
            for m in members
        },
        "recovery": recovery,
    }


#: How an entry's line begins: its hash, then its format, then the rest
#: of its clear header; its body, sealed, comes last.
_HEAD = re.compile(r'\{"hash":"([0-9a-f]{64})","format":')
_BODY = ',"body":'
#: The header's fields, in the order written.
_HEADER = ["hash", "format", "parents", "writer", "epoch", "nonce"]
#: How an entry's plain body begins: its kind, then its salt; what the
#: entry holds comes last.
_PLAIN = re.compile(r'\{"kind":"([a-z]+)","salt":"[A-Za-z0-9+/=]+","body":')


def _compact(value: Any) -> str:
    """A value as an entry writes it: compact, its keys in the order they
    were given (an order a read may follow)."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _entry_hash(format: int, parents: Sequence[str], writer: str, plain: bytes) -> str:
    """An entry's hash: over its plain body's hash (its kind, salt and
    content, before it is compressed and sealed), its format, its parents
    and its writer, as one object written in that order. A reader checks
    it on opening the body; one who cannot open it still reads the graph
    through the headers."""
    whole = (
        f'{{"body":"{hashlib.sha256(plain).hexdigest()}","format":{format},'
        f'"parents":{_compact(list(parents))},"writer":{_compact(writer)}}}'
    )
    return hashlib.sha256(whole.encode("utf-8")).hexdigest()


def _entry_line(
    kind: EntryKind,
    parents: Sequence[str],
    writer: str,
    body: Any,
    epoch: int,
    paper_key: bytes | None,
) -> tuple[str, str]:
    """An entry's hash and its line: the clear header, then the body,
    salted, compressed and sealed under `paper_key` with the header as
    associated data; a grant's alone is not sealed (no paper key)."""
    return _written_line(kind, parents, writer, _compact(body), epoch, paper_key)


def _written_line(
    kind: EntryKind,
    parents: Sequence[str],
    writer: str,
    written: str,
    epoch: int,
    paper_key: bytes | None,
) -> tuple[str, str]:
    """`_entry_line` for a body as written."""
    plain = (
        f'{{"kind":"{kind.value}","salt":"{keys.salt()}","body":{written}}}'
    ).encode()
    digest = _entry_hash(FORMAT, parents, writer, plain)
    nonce = None if paper_key is None else keys.nonce()
    head = (
        f'{{"hash":"{digest}","format":{FORMAT},"parents":{_compact(list(parents))},'
        f'"writer":{_compact(writer)},"epoch":{epoch},'
        f'"nonce":{_compact(None if nonce is None else _b64(nonce))}'
    )
    packed = zlib.compress(plain, 6)
    if paper_key is not None and nonce is not None:
        packed = keys.sealed(paper_key, nonce, packed, (head + "}").encode("utf-8"))
    return digest, f'{head}{_BODY}"{_b64(packed)}"}}\n'


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


@dataclass(frozen=True)
class OpenedDocument:
    """A document sealed to its readers and signed by its writer, opened:
    its writer as its grant names them, its entry's hash, kind and body."""

    writer: keys.Member
    entry: str
    kind: EntryKind
    body: dict[str, Any]


def sealed_document(
    mine: keys.Keys, kind: EntryKind, body: Any, to: Sequence[keys.Member]
) -> tuple[str, str]:
    """A document apart from any record, in the record's own lines: a
    grant of a paper key of its own to the writer and the readers `to`,
    one entry of `kind` holding `body`, sealed under it, and the writer's
    confirmation of that entry, signed. Its entry's hash and its text."""
    try:
        me = keys.Member(mine.reading.public_key(), mine.signing_key()[1])
    except keys.KeysError as error:
        raise RecordError(str(error)) from None
    paper_key = keys.paper_key()
    grant, granted = _entry_line(
        EntryKind.GRANT,
        [],
        mine.id,
        _grant_body(
            1,
            paper_key,
            [me, *(m for m in to if m.id != me.id)],
            keys.public_text(mine.recovery),
        ),
        1,
        None,
    )
    digest, line = _entry_line(kind, [grant], mine.id, body, 1, paper_key)
    try:
        signature = keys.signed(mine, _document_message(mine.id, digest))
    except keys.KeysError as error:
        raise RecordError(str(error)) from None
    _, confirmation = _entry_line(
        EntryKind.CONFIRMATION,
        [digest],
        mine.id,
        {"dropped": [], "signature": signature},
        1,
        paper_key,
    )
    return digest, granted + line + confirmation


def opened_document(
    text: str, mine: keys.Keys, kind: EntryKind, where: Path, writer: keys.Member
) -> OpenedDocument:
    """A document `sealed_document` wrote, opened with the reader's keys
    and held against its writer's card: refused where it is not one (a
    grant, one entry of `kind` after it, a confirmation of that entry),
    does not open for the reader, is not all three by the card's key, or
    its signature does not hold under the card's signer. The document's
    own grant only unwraps its paper key: no signer it names is trusted."""
    loaded = _Loaded(lambda: mine)
    loaded.extend(list(enumerate(text.splitlines(), 1)), where)
    if loaded.withheld and not loaded.held:
        raise RecordError(
            f"{where}: no grant in it wraps a paper key to your reading key {mine.id}"
        )
    if loaded.withheld:
        # a document is whole or nothing
        raise RecordError(next(iter(loaded.withheld.values())))
    if (
        len(loaded.order) != 3
        or len(loaded.grants) != 1
        or len(loaded.confirmations) != 1
        or len(loaded.notes) != 1
        or loaded.notes[0][1] is not kind
    ):
        raise RecordError(f"{where}: not a {kind.value} file")
    digest, _, by, body = loaded.notes[0]
    grant = loaded.grants[0].entry
    confirmation, confirmer, parents, signed = loaded.confirmations[0]
    if (
        {loaded.writers[grant], by, confirmer} != {writer.id}
        or loaded.parents[digest] != (grant,)
        or parents != (digest,)
        or signed.get("dropped") != []
    ):
        raise RecordError(
            f"{where}: not written by {writer.id}, whose card you hold, alone"
        )
    if writer.signer is None or not keys.verified(
        writer.signer,
        writer.id,
        _document_message(writer.id, digest),
        str(signed.get("signature", "")),
    ):
        raise RecordError(
            f"{where}: its signature does not hold under {writer.id}'s card"
        )
    return OpenedDocument(writer, digest, kind, body)


@dataclass(frozen=True)
class Sent:
    """A share its writer sent, as the record holds it: the entry that
    holds it, the share's entry, its round, its reader's id, the state it
    was cut from and the share it continues."""

    entry: str
    share: str
    round: int
    reader: str
    state: str
    continues: str | None


def _encoded(data: bytes) -> str:
    """A blob as an entry holds it: compressed, then base64."""
    return base64.b64encode(zlib.compress(data, 9)).decode("ascii")


#: A blob held as its change of another: the other's name, and the runs of
#: its lines it replaces.
_FROM = "from"
_LINES = "lines"


def _kept_form(data: bytes, base: tuple[str, bytes] | None) -> str | dict[str, Any]:
    """A blob as an entry holds it: where it and `base` (an earlier blob
    of the same file, by its name) are UTF-8 text, as the runs of `base`'s
    lines it replaces (`kept_change.text_change`), if that is the smaller;
    else whole (`_encoded`)."""
    whole = _encoded(data)
    if base is None:
        return whole
    try:
        changed = text_change(base[1].decode("utf-8"), data.decode("utf-8"))
    except UnicodeDecodeError:
        return whole
    patched = {_FROM: base[0], _LINES: changed}
    return patched if len(_compact(patched)) < len(whole) else whole


def _blob_of(blobs: Mapping[str, Any], name: str) -> bytes | None:
    """A blob's bytes from the forms the entries hold (`_kept_form`); None
    where they hold no such blob or none it was changed from."""
    chain = []
    while isinstance(held := blobs.get(name), dict):
        if name in chain:
            raise RecordError(f"blob {name[:12]} is changed from itself")
        chain.append(name)
        name = held.get(_FROM)
    if held is None:
        return None
    if not isinstance(held, str):
        raise RecordError(f"blob {name[:12]} is not a blob the record writes")
    data = zlib.decompress(base64.b64decode(held))
    for changed in reversed(chain):
        try:
            text = text_applied(data.decode("utf-8"), blobs[changed].get(_LINES))
        except (ChangeError, UnicodeDecodeError) as error:
            raise RecordError(
                f"blob {changed[:12]} is not a change the record writes: {error}"
            ) from None
        data = text.encode("utf-8")
    return data


# --- an answer kept as its change -------------------------------------------------

#: An answer's record ids, left out where they are those of its records,
#: in order. Its text is never in the record (docs/architecture.md § 4).
_RECORD_IDS = "records"
#: A kind's records, kept as the runs of an earlier answer's of that kind
#: (by its entry; none for the first): each record with its id where the ids
#: count up (`kept_change.counted`, the rule kept) and its answer where every
#: record names the entry's answer, both rendered back.
_RUNS = "runs"
_IDS = "ids"
_ANSWER = "answer"


def _bare(
    records: Sequence[Any], key: str | None
) -> tuple[list[Any], Counted | None, bool]:
    """A kind's records as their change is taken: each without its id
    where the ids count up, and without its answer where all name `key`."""
    rule = counted([r.get("id") if isinstance(r, dict) else None for r in records])
    named = bool(records) and all(
        isinstance(r, dict) and r.get(_ANSWER) == key for r in records
    )
    found = [
        {
            **r,
            **({"id": None} if rule else {}),
            **({_ANSWER: None} if named else {}),
        }
        if isinstance(r, dict)
        else r
        for r in records
    ]
    return found, rule, named


def _answer_body(
    data: dict[str, Any],
    records: dict[str, list[Any]],
    record_bases: Mapping[str, tuple[str, str, Sequence[Any]]],
) -> dict[str, Any]:
    """An answer's entry body: its data (`_answer_data`) with its record
    ids left out where they are its records' in order; and each kind of
    its records as their change of that kind's in `record_bases` (the
    latest answer holding it: its entry, key and records). What is held
    gives the answer back as given (`_Loaded`)."""
    data = dict(data)
    if data.get(_RECORD_IDS) == [
        r.get("id") if isinstance(r, dict) else None
        for rs in records.values()
        for r in rs
    ]:
        del data[_RECORD_IDS]
    held = {}
    for kind, rs in records.items():
        items, rule, named = _bare(rs, data.get("key"))
        base = record_bases.get(kind)
        before = [] if base is None else _bare(base[2], base[1])[0]
        held[kind] = {
            **({} if base is None else {_FROM: base[0]}),
            _RUNS: runs(before, items),
            _IDS: None if rule is None else list(rule),
            _ANSWER: named,
        }
    return {"answer": data, "records": held}


def holds_record(directory: Path) -> bool:
    """Whether `directory` holds a record; nothing is made."""
    return (Path(directory) / RECORD).is_file()


def holds(directory: Path, kind: EntryKind) -> bool:
    """Whether the record in `directory` holds an entry of `kind`; nothing
    is made. An entry's kind is sealed with its body, so the record is
    opened."""
    if not holds_record(directory):
        return False
    store = RecordStore(directory)
    with store._locked():
        return kind in store._load().kinds


class _Answered:
    """An answer's entry as loaded: the answer, its records as written by
    kind, their ids, and where it stands; each kind read the first time it
    is wanted."""

    def __init__(
        self,
        answer: AnswerRecord,
        records: dict[str, list[Any]],
        at: _Where,
        bare: dict[str, list[Any]] | None = None,
    ) -> None:
        self.answer = answer
        self.records = records
        self.at = at
        #: its entry's hash, set as it is added (`_Answers.add`)
        self.entry = ""
        #: each kind's records as a later answer's change of them is taken
        #: (`_bare`)
        self.bare = bare or {}
        self.ids = frozenset(_record_ids(records, at))
        self._read: dict[RecordKind, tuple[Any, ...]] = {}

    def read(self, kind: _Kind) -> tuple[Any, ...]:
        found = self._read.get(kind.kind)
        if found is None:
            found = tuple(
                kind.read(d, self.at) for d in self.records.get(kind.kind, ())
            )
            self._read[kind.kind] = found
        return found


def _record_ids(records: dict[str, list[Any]], at: _Where) -> Iterator[str]:
    for name, written in records.items():
        if name not in RecordKind.__members__.values():
            raise at.error("records", f"{name!r} is not a kind of record")
        if not isinstance(written, list):
            raise at.error(name, f"expected a list, got {written!r}")
        for data in written:
            if not isinstance(data, dict):
                raise at.error(name, f"expected an object, got {data!r}")
            yield _take(data, "id", str, at)


def _rebuilt(
    data: dict[str, Any],
    held: dict[str, Any],
    earlier: Mapping[str, _Answered],
    at: _Where,
) -> tuple[dict[str, Any], dict[str, list[Any]], dict[str, list[Any]]]:
    """An answer's data and its records by kind as given (`_answer_body`),
    from what its entry holds and the earlier entries it names; with each
    kind's records as a later change of them is taken (`_bare`)."""
    key = _take(data, "key", str, at)
    records: dict[str, list[Any]] = {}
    bare: dict[str, list[Any]] = {}
    for kind, form in held.items():
        if not isinstance(form, dict):
            raise at.error(kind, "not a kind's records as the record writes them")
        before: list[Any] = []
        if form.get(_FROM) is not None:
            base = earlier.get(form[_FROM])
            if base is None or kind not in base.bare:
                raise at.error(
                    kind,
                    f"a change of entry {str(form[_FROM])[:12]}'s records, which "
                    "the record does not hold before it",
                )
            before = base.bare[kind]
        try:
            items = applied_runs(before, form.get(_RUNS))
            rule = form.get(_IDS)
            ids = None if rule is None else counted_ids(rule, len(items))
        except (ChangeError, TypeError, ValueError) as error:
            raise at.error(kind, f"not a change the record writes: {error}") from None
        if not all(isinstance(r, dict) for r in items):
            raise at.error(kind, "a record that is not an object")
        bare[kind] = items
        records[kind] = [
            {
                **r,
                **({} if ids is None else {"id": ids[n]}),
                **({_ANSWER: key} if form.get(_ANSWER) else {}),
            }
            for n, r in enumerate(items)
        ]
    if _RECORD_IDS not in data:
        data = {
            **data,
            _RECORD_IDS: [r.get("id") for rs in records.values() for r in rs],
        }
    return data, records, bare


class _Answers:
    """The answers' entries as loaded, in the order they stand: each by its
    entry, the latest of each kind of record (what a new answer's change
    is taken of), each by its key and attempt,
    and every record id."""

    def __init__(self) -> None:
        self.answers: list[_Answered] = []
        self.entries: dict[str, _Answered] = {}
        self.latest_records: dict[str, str] = {}
        #: every answer's key and attempt, in effect or not
        self.written: set[tuple[str, int]] = set()
        self.ids: set[str] = set()
        #: the answers no confirmation covers yet, by hash, in order: a
        #: person's word among them read, but without effect (`without_effect`)
        self.pending: dict[str, _Answered] = {}
        #: the person's filings a confirmation dropped, by hash
        self.dropped: dict[str, _Answered] = {}

    def add(
        self, digest: str, body: dict[str, Any], at: _Where, standing: Confirmed
    ) -> None:
        data, records, bare = _rebuilt(
            _take(body, "answer", dict, at),
            _take(body, "records", dict, at),
            self.entries,
            at,
        )
        found = _Answered(_answer_from(data, at), records, at, bare)
        answer = found.answer
        found.entry = digest
        self.entries[digest] = found
        self.latest_records.update(dict.fromkeys(found.records, digest))
        self.written.add((answer.key, answer.attempt))
        self.ids.update(found.ids)
        if digest in standing.dropped and is_persons_word(answer):
            self.dropped[digest] = found
            return
        if digest not in standing.covered:
            self.pending[digest] = found
        self.answers.append(found)


class _Loaded:
    """The record as read: its entries in the order they stand, the heads
    (those no later entry names as its parent), the kept states with their
    lines' changes, texts and blobs, the answers' entries; and what is
    worked out of them, while the record stands. An answer's entry is read
    past its head only when an answer is first wanted (`answered`): a read
    of the kept states alone rebuilds none."""

    def __init__(self, mine: Callable[[], keys.Keys]) -> None:
        #: the reader's keys, asked for when the first sealed entry is
        self._mine = mine
        #: every paper key a trusted grant wraps to the reader, with its
        #: epoch: two copies joined may each have begun an epoch of one
        #: number
        self.held: list[tuple[int, bytes]] = []
        #: each trusted grant's own paper key, where it wraps it to the reader
        self.grant_keys: dict[str, bytes] = {}
        #: the trusted grants by entry, and each grant refused, with why
        self.trusted: dict[str, Grant] = {}
        #: the trusted grants under each entry, itself among them
        self.under: dict[str, frozenset[str]] = {}
        #: each entry left without effect, with why: one sealed under no
        #: key a trusted grant gives, or outside its grant's frontier
        self.withheld: dict[str, str] = {}
        self._frontiers: dict[str, set[str]] = {}
        self.kinds: set[EntryKind] = set()
        self.stamp: tuple[int, int, int] | None = None
        self.reach: _Reach | None = None
        self.torn: str | None = None
        self.order: list[str] = []
        self.named: set[str] = set()
        self.states: list[dict[str, Any]] = []
        self.lines: list[dict[str, Any]] = []
        self.state_hashes: list[str] = []
        #: each update's step by its identity answer: the state that kept
        #: it, and the step
        self.steps: dict[str, list[tuple[str, dict[str, Any]]]] = {}
        self.texts: dict[str, tuple[str, ...]] = {}
        self.blobs: dict[str, str | dict[str, Any]] = {}
        self.whole: _Whole | None = None
        #: each judgment kind's records of the first answers, with their key
        self.judgments: dict[str, tuple[tuple[str, Any], ...]] | None = None
        self._answers = _Answers()
        #: the answers' entries not read yet: each one's hash, body as
        #: written and place
        self._unread: list[tuple[str, str, _Where]] = []
        #: every answer's entry, read again where a confirmation comes
        self._every: list[tuple[str, str, _Where]] = []
        #: each entry's parents and writer, by hash
        self.parents: dict[str, tuple[str, ...]] = {}
        #: whether each entry follows the one before it alone: no two
        #: entries were made apart, and the order is the ancestry
        self.in_line = True
        self.writers: dict[str, str] = {}
        #: the grants in order
        self.grants: list[Grant] = []
        #: the confirmations in order: each one's hash, writer, parents and body
        self.confirmations: list[tuple[str, str, tuple[str, ...], dict[str, Any]]] = []
        self._standing: Confirmed | None = None
        #: each entry's place in `order`, worked out when first wanted
        self._places: dict[str, int] = {}
        #: each entry's place in `ranks`' order, worked out when first wanted
        self._ranks: dict[str, int] = {}
        #: the first valid answers and the conflicts, for the answers read
        #: and how many they were
        self._firsts: tuple[_Answers, int, _Firsts] | None = None
        #: what `taken_back` gave, by `waiting`, at the record's length
        self._taken: dict[bool, tuple[int, tuple[frozenset[str], frozenset[str]]]] = {}
        #: the kept states in effect, by their place in `states`
        self._in_effect: list[int] | None = None
        self._not_dropped: list[int] = []
        #: the entries neither answers, states, grants nor confirmations:
        #: each one's hash, kind, writer and body
        self.notes: list[tuple[str, EntryKind, str, dict[str, Any]]] = []

    def heads(self) -> list[str]:
        return [h for h in self.order if h not in self.named]

    def grant(self, entry: str) -> Grant:
        return self.trusted[entry]

    def on(self, digest: str) -> list[str]:
        """The trusted grants `digest` stands on: the latest under it,
        those no other trusted grant under it follows. More than one where
        two were made apart and none settles them yet."""
        under = self.under.get(digest, frozenset())
        return [
            g for g in under if not any(g != h and g in self.under[h] for h in under)
        ]

    def current(self) -> list[str]:
        """The trusted grants the record stands on now: those its heads
        stand on."""
        heads = self.heads()
        under = frozenset().union(*(self.under.get(h, frozenset()) for h in heads))
        return [
            g for g in under if not any(g != h and g in self.under[h] for h in under)
        ]

    def writing(self) -> tuple[int, bytes] | None:
        """The epoch and paper key a new entry is sealed under: the one
        trusted grant the record stands on, where it wraps its key to the
        reader. Refused where two made apart wait to be settled; None
        where the record holds no grant yet."""
        current = self.current()
        if not current:
            return None
        if len(current) > 1:
            raise RecordError(
                "two grants were made apart ("
                + ", ".join(sorted(g[:12] for g in current))
                + "); settle them first: `axiomation members --settle`"
            )
        key = self.grant_keys.get(current[0])
        if key is None:
            raise RecordError(
                f"the record's grant {current[0][:12]} wraps no paper key to "
                f"your reading key {self._mine().id}"
            )
        return self.trusted[current[0]].epoch, key

    def follows(self, entry: str, earlier: str) -> bool:
        """Whether `earlier` is `entry` or under it: written before it in
        every copy, whatever order two joined copies' lines stand in."""
        if len(self._places) != len(self.order):
            self._places = {d: n for n, d in enumerate(self.order)}
        floor = self._places[earlier]
        if self.in_line:
            return floor <= self._places[entry]
        seen: set[str] = set()
        stack = [entry]
        while stack:
            at = stack.pop()
            if at == earlier:
                return True
            if at in seen or self._places.get(at, -1) < floor:
                continue
            seen.add(at)
            stack.extend(self.parents.get(at, ()))
        return False

    def ranks(self) -> dict[str, int]:
        """Each entry's place in one order every copy reads alike: each
        after its parents, and of those ready together the least hash
        first, so two joined copies' lines in either order give it. What
        reads filings in order reads it in this one (`filed_order`)."""
        if len(self._ranks) != len(self.order):
            waiting = {d: len(self.parents.get(d, ())) for d in self.order}
            after: dict[str, list[str]] = {}
            for d in self.order:
                for p in self.parents.get(d, ()):
                    after.setdefault(p, []).append(d)
            ready = [d for d, n in waiting.items() if n == 0]
            heapq.heapify(ready)
            ranks: dict[str, int] = {}
            while ready:
                d = heapq.heappop(ready)
                ranks[d] = len(ranks)
                for c in after.get(d, ()):
                    waiting[c] -= 1
                    if not waiting[c]:
                        heapq.heappush(ready, c)
            self._ranks = ranks
        return self._ranks

    def uppermost(self, entries: Sequence[str]) -> list[str]:
        """Those of `entries` no other of them was written after, in
        `entries`' order: one where they stand in a line, several where two
        joined copies wrote them apart."""
        if len(entries) < 2:
            return list(entries)
        under = self.below(entries)
        return [e for e in entries if e not in under]

    def below(self, entries: Sequence[str]) -> set[str]:
        """Every entry some one of `entries` was written after."""
        under: set[str] = set()
        stack = [p for e in entries for p in self.parents.get(e, ())]
        while stack:
            at = stack.pop()
            if at not in under:
                under.add(at)
                stack.extend(self.parents.get(at, ()))
        return under

    def firsts(self) -> _Firsts:
        """Each key's first valid answer whose records were all written, by
        ancestry: the one no other valid answer of its key is under. Two
        made apart that hold the same answer are one (either read gives the
        same records); two that differ are a conflict, and the key has no
        first answer (`docs/architecture.md` § 4, "Copies joined")."""
        answers = self.answered()
        held = self._firsts
        if held is not None and held[0] is answers and held[1] == len(answers.entries):
            return held[2]
        # an answer a drop took back with the person's is no key's first:
        # the key is answered again
        taken = self.taken_back(False)[1] if answers.dropped else frozenset()
        by_key: dict[str, list[_Answered]] = {}
        for x in answers.answers:
            if x.entry in taken:
                continue
            if x.answer.valid and all(i in x.ids for i in x.answer.records):
                by_key.setdefault(x.answer.key, []).append(x)
        firsts: dict[str, _Answered] = {}
        twins: dict[str, tuple[str, ...]] = {}
        conflicts: dict[str, tuple[_Answered, ...]] = {}
        for key, found in by_key.items():
            least = [
                x
                for x in found
                if not any(y is not x and self.follows(x.entry, y.entry) for y in found)
            ]
            least.sort(key=lambda x: x.entry)
            if all(
                (x.answer.raw, x.records) == (least[0].answer.raw, least[0].records)
                for x in least
            ):
                firsts[key] = least[0]
                twins[key] = tuple(x.entry for x in least)
            else:
                conflicts[key] = tuple(least)
        ranks = self.ranks()
        ordered = {
            x.answer.key: x
            for x in sorted(firsts.values(), key=lambda x: ranks[x.entry])
        }
        found = _Firsts(ordered, conflicts, twins)
        self._firsts = (answers, len(answers.entries), found)
        self.judgments = None
        return found

    def standing(self) -> Confirmed:
        """Where the entries stand under the person's word (`_confirmed`),
        worked out once while the record stands."""
        if self._standing is None:
            self._standing = _confirmed(self) if self.confirmations else Confirmed()
        return self._standing

    def without_effect(self, waiting: bool = True) -> frozenset[str]:
        """The keys of the answers without effect, every attempt of each
        taken back (`taken_back`): the person's that no confirmation covers
        and what goes with them; without `waiting`, those a drop took back
        alone."""
        answers = self.answered()
        taken = self.taken_back(waiting)[1]
        if not taken:
            return frozenset()
        keys: dict[str, bool] = {}
        for d, x in answers.entries.items():
            keys[x.answer.key] = keys.get(x.answer.key, True) and d in taken
        return frozenset(k for k, gone in keys.items() if gone)

    def taken_back(self, waiting: bool = True) -> tuple[frozenset[str], frozenset[str]]:
        """The kept states and the answers' entries without effect: those
        a drop took back, and with `waiting` the person's no confirmation
        covers yet, with all that goes with them (`_taken_with`)."""
        held = self._taken.get(waiting)
        if held is not None and held[0] == len(self.order):
            return held[1]
        answers = self.answered()
        gone = {d for d, x in answers.dropped.items()}
        if waiting:
            gone |= {d for d, x in answers.pending.items() if is_persons_word(x.answer)}
        found = self._taken_with(frozenset(gone))
        self._taken[waiting] = (len(self.order), found)
        return found

    def _taken_with(
        self, gone: frozenset[str]
    ) -> tuple[frozenset[str], frozenset[str]]:
        """What the answers' entries `gone` take back with them, by
        ancestry, so every join order reads alike: the kept states made
        from them or kept on them, by id where every row of the id is;
        and the answers' entries that go too: `gone`, and each answer given
        on such a state.
        A row of a state is made from the uppermost attempts under it of
        each answer it adds to its parent's, and goes where, for any one
        answer, every one of its attempts is gone; it goes too where every
        row of its parent under it does. A later attempt of a key stands on its own
        (`docs/architecture.md` § 4, "The person's word")."""
        if not gone:
            return frozenset(), frozenset()
        answers = self.answered()
        attempts: dict[str, list[str]] = {}
        for d, x in answers.entries.items():
            attempts.setdefault(x.answer.key, []).append(d)
        held: dict[str, set[str]] = {}
        rows: dict[str, list[int]] = {}
        row_out: list[bool] = []

        def under(entry: str, state: str | None) -> list[int]:
            # the rows of `state` this entry was written on
            return [
                m
                for m in rows.get(state or "", ())
                if self.follows(entry, self.state_hashes[m])
            ]

        # a parent's rows stand before the rows kept on them: one pass
        for n, row in enumerate(self.states):
            state, parent = row["id"], row.get("parent")
            carried = set(row.get("answers", ()))
            own = carried - held.get(parent, set())
            held.setdefault(state, carried)
            entry = self.state_hashes[n]
            out = False
            for key in own:
                tops = self.uppermost(
                    [a for a in attempts.get(key, ()) if self.follows(entry, a)]
                )
                out = out or bool(tops) and all(d in gone for d in tops)
            parents = under(entry, parent)
            row_out.append(out or bool(parents) and all(row_out[m] for m in parents))
            rows.setdefault(state, []).append(n)
        out_ids = {s for s, ns in rows.items() if all(row_out[n] for n in ns)}
        taken = set(gone)
        for d, x in answers.entries.items():
            on = under(d, x.answer.state) if x.answer.state is not None else []
            if on and all(row_out[m] for m in on):
                taken.add(d)
        return frozenset(out_ids), frozenset(taken)

    def in_effect(self, waiting: bool = False) -> list[int]:
        """The kept states in effect, by their place: a state made from a
        person's answer no confirmation covers, or one dropped, waits with
        it, and so does every state built on it (`taken_back`). `waiting`:
        those that wait too, all but what a drop took back."""
        if self._in_effect is None:
            standing = self.standing()
            if not standing.dropped and all(
                d in standing.covered for d, _, _ in self._every
            ):
                # every entry confirmed: no answer need be read to know it
                self._in_effect = self._not_dropped = list(range(len(self.states)))
            else:
                out = self.taken_back()[0]
                dropped = self.taken_back(False)[0]
                self._in_effect = [
                    n for n, row in enumerate(self.states) if row["id"] not in out
                ]
                self._not_dropped = [
                    n for n, row in enumerate(self.states) if row["id"] not in dropped
                ]
        return self._not_dropped if waiting else self._in_effect

    def kept_once(self, waiting: bool = False) -> list[int]:
        """`in_effect`'s states, each id once and in canonical order
        (`ranks`): one state kept alike on two copies is one, read from its
        least-ranked entry, so every join order lists the same rows."""
        ranks = self.ranks()
        first: dict[str, int] = {}
        for n in self.in_effect(waiting):
            sid = self.states[n]["id"]
            held = first.get(sid)
            if (
                held is None
                or ranks[self.state_hashes[n]] < ranks[self.state_hashes[held]]
            ):
                first[sid] = n
        return sorted(first.values(), key=lambda n: ranks[self.state_hashes[n]])

    def answered(self) -> _Answers:
        """The answers' entries, each read the first time any is wanted;
        a person's filing no confirmation covers, and an entry one drops,
        left out of those in effect."""
        if self._unread:
            standing = self.standing()
            for digest, body, at in self._unread:
                self._answers.add(digest, _body(body, at), at, standing)
            self._unread.clear()
        return self._answers

    def extend(self, lines: Sequence[tuple[int, str]], path: Path) -> None:
        seen = set(self.order)
        texts: dict[str, tuple[str, ...]] = {}
        # Every grant is judged, and its keys read, before the entries they
        # open: a grant wraps earlier epochs' keys to a member it adds, and
        # an entry counts only within its grant's frontier.
        first = not self.order
        for number, line in lines:
            at = _Where(path, number)
            digest, parents, *_ = _read_entry(line, at)
            if digest in self.under:
                continue
            self.parents.setdefault(digest, parents)
            under = frozenset().union(
                *(self.under.get(p, frozenset()) for p in parents)
            )
            if '"nonce":null,' in line[: line.find(_BODY) + 1]:
                _, kind, _, writer, written = self._opened(line, at)
                if kind is EntryKind.GRANT and self._granted(
                    digest, parents, writer, _body(written, at), at, first
                ):
                    under |= {digest}
            first = False
            self.under[digest] = under
        for number, line in lines:
            at = _Where(path, number)
            digest, kind, parents, writer, written = self._opened(line, at)
            if digest in seen:
                continue
            unknown = [p for p in parents if p not in seen]
            if unknown:
                raise RecordError(
                    f"{path}:{number}: entry {digest[:12]} follows {unknown[0][:12]}, "
                    "which the record does not hold before it"
                )
            seen.add(digest)
            if tuple(parents) != tuple(self.order[-1:]):
                self.in_line = False
            self.order.append(digest)
            self.named.update(parents)
            self.parents[digest] = parents
            self.writers[digest] = writer
            if kind is None:
                continue
            self.kinds.add(kind)
            if kind is EntryKind.ANSWER:
                self._unread.append((digest, written, at))
                self._every.append((digest, written, at))
                continue
            if kind is EntryKind.GRANT:
                continue
            if kind is EntryKind.CONFIRMATION:
                body = _body(written, at)
                if list(body) != ["dropped", "signature"]:
                    raise RecordError(f"{at.path}:{at.line}: not a confirmation's body")
                self.confirmations.append((digest, writer, parents, body))
                # what stands in effect changes: every answer read again
                self._standing = None
                self._answers = _Answers()
                self._unread = list(self._every)
                continue
            if kind in (EntryKind.SHARE, EntryKind.SENT, EntryKind.REVIEW):
                self.notes.append((digest, kind, writer, _body(written, at)))
                continue
            body = _body(written, at)
            self.states.append(_take(body, "state", dict, at))
            if "step" in body:
                step = _take(body, "step", dict, at)
                self.steps.setdefault(_take(step, "answer", str, at), []).append(
                    (self.states[-1]["id"], step)
                )
            self.lines.append(_take(body, "lines", dict, at))
            self.state_hashes.append(digest)
            for tid, held in _take(body, "texts", dict, at).items():
                texts[tid] = _strings(held, "texts", at)
            self.blobs.update(_take(body, "blobs", dict, at))
        if texts:
            # copied, not changed in place: a reader may hold the last one
            self.texts = {**self.texts, **texts}
        self.whole = None
        self.judgments = None
        self._in_effect = None

    def _opened(
        self, line: str, at: _Where
    ) -> tuple[str, EntryKind | None, tuple[str, ...], str, str]:
        """An entry's hash, kind, parents and writer, and its body as written,
        opened under the paper key of the grant it stands on; refused where
        it does not hold what its hash names. One that does not open under
        that key, or stands outside its grant's frontier, is without effect
        (`withheld`): its kind None."""
        digest, parents, writer, epoch, nonce, data, header = _read_entry(line, at)
        if nonce is not None:
            why = self._outside(digest, epoch)
            opened = None
            if why is None:
                opened = next(
                    (
                        o
                        for e, k in self.held
                        if e == epoch
                        and (o := keys.opened(k, nonce, data, header)) is not None
                    ),
                    None,
                )
                if opened is None:
                    why = (
                        f"it does not open under any paper key of epoch {epoch} "
                        f"a trusted grant wraps to your reading key "
                        f"{self._mine().id}"
                    )
            if opened is None:
                self.withheld.setdefault(
                    digest, f"{at.path}:{at.line}: entry {digest[:12]}: {why}"
                )
                return digest, None, parents, writer, ""
            data = opened
        try:
            plain = zlib.decompress(data)
        except zlib.error:
            raise RecordError(
                f"{at.path}:{at.line}: entry {digest[:12]}'s body is not compressed"
            ) from None
        if _entry_hash(FORMAT, parents, writer, plain) != digest:
            raise RecordError(
                f"{at.path}:{at.line}: entry {digest[:12]} does not hold what its "
                "hash names"
            )
        text = plain.decode("utf-8")
        found = _PLAIN.match(text)
        if found is None or not text.endswith("}"):
            raise RecordError(f"{at.path}:{at.line}: not an entry's body")
        kind = _enum(EntryKind, found.group(1), "kind", at)
        if nonce is None and kind is not EntryKind.GRANT:
            raise RecordError(
                f"{at.path}:{at.line}: entry {digest[:12]} is not sealed, and only "
                "a grant is not"
            )
        return digest, kind, parents, writer, text[found.end() : -1]

    def _outside(self, digest: str, epoch: int) -> str | None:
        """Why an entry sealed in `epoch` is without effect, or None: it
        must stand on one trusted grant, be sealed in that grant's epoch,
        and be under every trusted grant that follows that one removing a
        member (a removal closes its epoch at the entries it names under
        it; what a removed member writes after counts for nothing)."""
        on = self.on(digest)
        if len(on) != 1:
            return (
                "it stands on no trusted grant"
                if not on
                else "it stands on two grants made apart"
            )
        grant = self.trusted[on[0]]
        if grant.epoch != epoch:
            return f"it is sealed in epoch {epoch}, not its grant's {grant.epoch}"
        for later, after in self.trusted.items():
            if (
                later != on[0]
                and on[0] in self.under[later]
                and grant.members.keys() - after.members.keys()
                and digest not in self._frontier(later)
            ):
                return f"grant {later[:12]} closed its epoch before it"
        return None

    def _frontier(self, grant: str) -> set[str]:
        """The entries a grant names under it: those its epoch's
        predecessors may still count."""
        if grant not in self._frontiers:
            self._frontiers[grant] = _ancestors(self, grant)
        return self._frontiers[grant]

    def _granted(
        self,
        digest: str,
        parents: tuple[str, ...],
        writer: str,
        body: dict[str, Any],
        at: _Where,
        first: bool,
    ) -> bool:
        """Whether a grant is trusted, and where it is, its members kept and
        the paper keys it wraps to the reader held. The record's first
        entry, a grant naming its writer, founds it; every other grant is
        signed by its writer, a signing member of each trusted grant it
        stands on (`_grant_message`). A grant refused is without effect."""
        epoch = _take(body, "epoch", int, at)
        members = {}
        for mid, member in _take(body, "members", dict, at).items():
            if not isinstance(member, dict):
                raise at.error("members", f"expected an object, got {member!r}")
            signer = member.get("signer")
            try:
                found = keys.Member(
                    keys.public_from(_take(member, "reading", str, at)),
                    signer if isinstance(signer, str) else None,
                )
            except keys.KeysError as error:
                raise at.error("members", str(error)) from None
            if found.id != mid:
                raise at.error("members", f"member {mid} names another's key")
            members[mid] = found
        grant = Grant(digest, epoch, members, _take(body, "recovery", str, at))
        self.grants.append(grant)
        under = frozenset().union(*(self.under.get(p, frozenset()) for p in parents))
        on = [g for g in under if not any(g != h and g in self.under[h] for h in under)]
        if first and not parents:
            why = None if writer in members else "its writer is none of its members"
        elif not on:
            why = "it stands on no trusted grant"
        else:
            signers = [
                m.signer
                for g in on
                if (m := self.trusted[g].members.get(writer)) is not None
                and m.signer is not None
            ]
            if len(signers) != len(on):
                why = (
                    f"its writer {writer} is no signing member of the grant it follows"
                )
            elif not all(
                keys.verified(
                    signer,
                    writer,
                    _grant_message(writer, parents, body),
                    str(body.get("signature", "")),
                )
                for signer in set(signers)
            ):
                why = "its signature does not hold"
            else:
                why = None
        if why is not None:
            self.withheld[digest] = f"{at.path}:{at.line}: grant {digest[:12]}: {why}"
            return False
        self.trusted[digest] = grant
        self._unwrap(digest, body, at)
        return True

    def _unwrap(self, digest: str, body: dict[str, Any], at: _Where) -> None:
        """The paper keys a grant wraps to the reader's reading key, each
        kept under its epoch: its own, and earlier ones it gives a member
        it adds."""
        epoch = _take(body, "epoch", int, at)
        reading = self._mine().reading
        for stanza in _take(body, "stanzas", list, at):
            if not isinstance(stanza, dict):
                raise at.error("stanzas", f"expected an object, got {stanza!r}")
            key = keys.unwrapped(stanza, reading)
            if key is not None:
                self._hold(epoch, key)
                self.grant_keys[digest] = key
        for stanza in _take(body, "earlier", list, at):
            if not isinstance(stanza, dict):
                raise at.error("earlier", f"expected an object, got {stanza!r}")
            key = keys.unwrapped(stanza, reading)
            if key is not None:
                self._hold(_take(stanza, "epoch", int, at), key)

    def _hold(self, epoch: int, key: bytes) -> None:
        """A paper key a trusted grant wraps to the reader, held."""
        if (epoch, key) not in self.held:
            self.held.append((epoch, key))

    def kept_stamp(self) -> tuple[int, ...]:
        if self.stamp is None:
            return (0,)
        last = int(self.state_hashes[-1][:15], 16) if self.state_hashes else 0
        # a confirmation changes which states are in effect
        return (self.stamp[0], len(self.states), last, len(self.confirmations))


def _read_entry(
    line: str, at: _Where
) -> tuple[str, tuple[str, ...], str, int, bytes | None, bytes, bytes]:
    """An entry's clear header (its hash, parents, writer, epoch and
    nonce), its body's bytes as written, and the header's bytes the
    body is sealed with; refused where its head is not one this code
    writes. Its body is opened apart (`_Loaded._opened`)."""
    head = _HEAD.match(line)
    split = line.find(_BODY)
    if head is None or split < 0 or not line.endswith('"}'):
        raise RecordError(f"{at.path}:{at.line}: not an entry of the record")
    header = line[:split] + "}"
    data = _json(header, at.path, at.line)
    if not isinstance(data, dict) or list(data) != _HEADER:
        raise RecordError(f"{at.path}:{at.line}: not an entry of the record")
    written = _take(data, "format", int, at)
    if written != FORMAT:
        raise RecordError(
            f"{at.path}:{at.line}: an entry of format {written}; this code reads "
            f"format {FORMAT}"
        )
    nonce = data["nonce"]
    if nonce is not None and not isinstance(nonce, str):
        raise at.error("nonce", f"expected str or null, got {nonce!r}")
    try:
        body = base64.b64decode(line[split + len(_BODY) + 1 : -2], validate=True)
        sealed_with = None if nonce is None else base64.b64decode(nonce, validate=True)
    except ValueError:
        raise RecordError(f"{at.path}:{at.line}: not an entry of the record") from None
    return (
        _take(data, "hash", str, at),
        _strings(_take(data, "parents", list, at), "parents", at),
        _take(data, "writer", str, at),
        _take(data, "epoch", int, at),
        sealed_with,
        body,
        header.encode("utf-8"),
    )


def _body(written: str, at: _Where) -> dict[str, Any]:
    """An entry's body from its text as written (`_read_entry`)."""
    data = _json(written, at.path, at.line)
    if not isinstance(data, dict):
        raise at.error("body", f"expected dict, got {data!r}")
    return data


class FiledAt(int):
    """A filing's place: its rank in the order every copy reads alike,
    which sorts and picks a latest; and its answer's entries, by which
    `filed_after` reads whether it was filed after another. Two filings
    joined copies made apart are neither after the other, whatever their
    ranks: a hash never decides which reading came later."""

    entries: frozenset[str]
    _follows: Callable[[str, str], bool]

    def __new__(
        cls, rank: int, entries: frozenset[str], follows: Callable[[str, str], bool]
    ) -> FiledAt:
        found = super().__new__(cls, rank)
        found.entries = entries
        found._follows = follows
        return found

    def __reduce__(self) -> NoReturn:
        # read from the store, never kept: a rank alone would read every
        # filing as before none
        raise TypeError("a filing's place is read from its store, not kept")


#: The place of what the store holds no filing of: under every filing,
#: and after none.
NOT_FILED = FiledAt(-1, frozenset(), lambda entry, earlier: False)


def filed_after(later: FiledAt, earlier: FiledAt) -> bool:
    """Whether the filing at `later` was filed after the one at `earlier`,
    by ancestry (`FiledAt`); a place the store does not hold (`NOT_FILED`) is
    under every filing, and after none."""
    if not later.entries:
        return False
    if not earlier.entries:
        return True
    if later.entries & earlier.entries:
        return False
    return any(later._follows(e, o) for e in later.entries for o in earlier.entries)


def unseen_by(filing: FiledAt, reader: FiledAt) -> bool:
    """Whether the filing at `filing` is one the filing at `reader` did not
    see: neither filed with it nor before it, by ancestry (`FiledAt`). One
    joined copies made apart from `reader` is unseen, as one filed after
    it is; a place the store does not hold (`NOT_FILED`) is seen by every
    filing."""
    if not filing.entries:
        return False
    if not reader.entries:
        return True
    if filing.entries & reader.entries:
        return False
    return not filed_after(reader, filing)


@dataclass(frozen=True, order=True)
class FilingConflict:
    """Filings two joined copies made apart that say differently in one
    place: what was filed, the place, and their entries."""

    kind: str
    place: str
    entries: tuple[str, ...]


@dataclass(frozen=True)
class _Firsts:
    """Each key's first valid answer, in the order they stand, and each key
    whose answers made apart conflict, with them."""

    first: dict[str, _Answered]
    conflicts: dict[str, tuple[_Answered, ...]]
    #: each first answer's entries: its own and those of the equal answers
    #: made apart beside it
    twins: dict[str, tuple[str, ...]]


#: What `RecordStore._whole` gives: the answers, corrections, edge
#: records and flags it keeps, and the problems it found.
_Whole = tuple[
    list[AnswerRecord], list[CutCorrection], frozenset[str], list[Flag], list[str]
]

_LOCKS: dict[Path, threading.RLock] = {}
_LOCKS_LOCK = threading.Lock()
#: How deep each thread holds each store's lock: only the outermost hold
#: takes the file lock, so the store's own reads run under a writer's.
_HELD = threading.local()


def _lock_for(directory: Path) -> threading.RLock:
    with _LOCKS_LOCK:
        return _LOCKS.setdefault(directory, threading.RLock())


class RecordStore:
    """A paper's record, `record.jsonl` in `directory`: its answers with
    their records, and its kept states.

    Every read and every append holds the store's lock: a thread lock
    shared by every store on the same directory in a process, and an
    exclusive `flock` on `.lock` in the directory across processes; a
    thread holding it already holds it again. So a reader never sees half
    an entry, and two writers never interleave. A write is flushed, not
    synced: a crash of the process loses nothing written, a power loss
    may.

    The record is sealed (`axiomation.keys`): it is read and written with
    `keys`, else the person's own, read when first needed. The first
    entry written makes the paper key and its grant.
    """

    def __init__(self, directory: Path, keys: keys.Keys | None = None) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self._key = self.directory.resolve()
        self._lock = _lock_for(self._key)
        self._loaded: _Loaded | None = None
        self._keys = keys
        self._unpinned = False
        self._seen: str | None = None

    def seen_from(self, state: str) -> RecordStore:
        """The store as the kept state `state` (its id) reads it: a key
        two joined copies answered apart and differently reads the answer
        the state was built on, the one under its entry; every other key
        as the store reads it. A state reads the entries it was built from
        whatever was joined after (`docs/architecture.md` § 4, "Copies
        joined")."""
        found = copy.copy(self)
        found._seen = state
        return found

    def _firsts_seen(self) -> Mapping[str, _Answered]:
        """Each key's first answer, as `seen_from`'s state reads them;
        called under the lock."""
        loaded = self._load()
        firsts = loaded.firsts()
        if self._seen is None or not firsts.conflicts:
            return firsts.first
        entries = [
            loaded.state_hashes[n]
            for n, row in enumerate(loaded.states)
            if row["id"] == self._seen
        ]
        found = dict(firsts.first)
        for key, torn in firsts.conflicts.items():
            under = [
                x for x in torn if any(loaded.follows(e, x.entry) for e in entries)
            ]
            if under and all(x.records == under[0].records for x in under):
                found[key] = min(under, key=lambda x: x.entry)
        return found

    def _mine(self) -> keys.Keys:
        if self._keys is None:
            try:
                self._keys = keys.mine()
            except keys.KeysError as error:
                raise RecordError(f"{self.record_path}: {error}") from None
        return self._keys

    @property
    def record_path(self) -> Path:
        return self.directory / RECORD

    @property
    def key(self) -> Path:
        """The store's identity in a process: what a read worked out of it
        is held under this."""
        return self._key

    @property
    def transcripts(self) -> Path:
        """Where a run's sessions' transcripts go."""
        return self.directory / TRANSCRIPTS

    @property
    def sessions(self) -> Path:
        """Where a run's sessions' settings lie while they run."""
        return self.directory / SESSIONS_DIRECTORY

    @property
    def run_directory(self) -> Path:
        """Where a run keeps its own data: its run file (`run_state`),
        develop mode's texts, its code's cache and the cited works'
        caches. Every path a run builds starts here, never at `directory`
        (`tests/test_store_seam.py`)."""
        return self.directory

    def copy_to(self, target: Path) -> RecordStore:
        """The store copied to `target`, its transcripts left out: a dry
        run's scratch store, gone with it."""
        with self._locked():
            shutil.copytree(
                self.directory,
                target,
                ignore=shutil.ignore_patterns(TRANSCRIPTS, LOCK),
            )
        return RecordStore(target, self._keys)

    @contextmanager
    def _locked(self) -> Iterator[None]:
        with self._lock:
            depth: dict[Path, int] = _HELD.__dict__.setdefault("depth", {})
            if depth.get(self._key):
                depth[self._key] += 1
                try:
                    yield
                finally:
                    depth[self._key] -= 1
                return
            with (self.directory / LOCK).open("a") as handle:
                fcntl.flock(handle, fcntl.LOCK_EX)
                depth[self._key] = 1
                try:
                    yield
                finally:
                    depth[self._key] = 0
                    fcntl.flock(handle, fcntl.LOCK_UN)

    def revision(self) -> str:
        """The store's record files as they stand: their names, sizes and
        times, hashed. Every write appends to one, so every write changes
        it; a reader keeps what it read under it while it holds. It reads
        the files' stats only, outside the store's lock: a read torn by an
        append costs one read more, and never hides a write."""
        files = sorted(self.directory.glob("*.jsonl"))
        stats = [(p.name, p.stat()) for p in files if p.is_file()]
        stamp = "\n".join(f"{n} {s.st_size} {s.st_mtime_ns}" for n, s in stats)
        return hashlib.sha256(stamp.encode()).hexdigest()[:16]

    def locked(self) -> AbstractContextManager[None]:
        """The store's lock, for the writer of its kept versions
        (`axiomation.versions`), so a state is never read half
        written."""
        return self._locked()

    def append(
        self,
        answer: AnswerRecord,
        corrections: Sequence[CutCorrection] = (),
        edges: EdgeRecords = EdgeRecords(),  # noqa: B008 - frozen, empty
        flags: Sequence[Flag] = (),
        lineage: Sequence[LineageLink] = (),
        judgments: JudgmentRecords = JudgmentRecords(),  # noqa: B008 - frozen
        vocabulary: Sequence[VocabularyEntry] = (),
        declines: Sequence[Decline] = (),
    ) -> None:
        """One attempt's answer and the records made from it (corrections,
        then the edge stage's, then its flags, then an identity answer's
        lineage, then a filing's judgments, findings and finding answers),
        one entry, under the store's lock. The answer's `records` must be
        their ids in that order, each record's `answer` the answer's key; a
        failed answer makes none; no record id and no key and attempt may be
        in the store already, counting an answer the loader ignores
        (`next_attempt` gives a free attempt number)."""
        ids = (
            *(c.id for c in corrections),
            *edges.ids,
            *(f.id for f in flags),
            *(r.id for r in lineage),
            *judgments.ids,
            *(r.id for r in vocabulary),
            *(r.id for r in declines),
        )
        if ids != answer.records:
            raise ValueError(
                f"answer {answer.key} records {list(answer.records)}, "
                f"the records are {list(ids)}"
            )
        if ids and not answer.valid:
            raise ValueError(f"answer {answer.key} failed, and makes no records")
        for c in corrections:
            if c.answer != answer.key:
                raise ValueError(f"correction {c.id} names answer {c.answer}")
            problems = c.check()
            if problems:
                raise ValueError(f"correction {c.id}: {'; '.join(problems)}")
        for r in (
            *edges.all(),
            *flags,
            *lineage,
            *judgments.all(),
            *vocabulary,
            *declines,
        ):
            if r.answer != answer.key:
                raise ValueError(f"record {r.id} names answer {r.answer}")
        held = (
            (_CORRECTIONS, corrections),
            *((k, getattr(edges, k.field)) for k in EDGE_KINDS),
            (_FLAGS, flags),
            (_LINEAGE, lineage),
            *((k, getattr(judgments, k.field)) for k in JUDGMENT_KINDS),
            (_VOCABULARY, vocabulary),
            (_DECLINES, declines),
        )
        records = {k.kind.value: [k.data(r) for r in rs] for k, rs in held if rs}
        with self._locked():
            loaded = self._load()
            answered = loaded.answered()
            if (answer.key, answer.attempt) in answered.written:
                raise ValueError(
                    f"answer {answer.key} attempt {answer.attempt} is in the "
                    "store already"
                )
            repeated = [i for i in ids if i in answered.ids or ids.count(i) > 1]
            if repeated:
                raise ValueError(f"record id {repeated[0]} is in the store already")
            record_bases = {
                kind: (
                    entry,
                    answered.entries[entry].answer.key,
                    answered.entries[entry].records[kind],
                )
                for kind in records
                if (entry := answered.latest_records.get(kind)) is not None
            }
            self._append_entry(
                loaded,
                EntryKind.ANSWER,
                _answer_body(
                    _answer_data(answer),
                    records,
                    record_bases,
                ),
            )

    def keep_state(
        self,
        state: dict[str, Any],
        lines: dict[str, Any],
        texts: dict[str, Sequence[str]],
        blobs: dict[str, bytes],
        bases: Mapping[str, str] | None = None,
        step: dict[str, Any] | None = None,
    ) -> None:
        """A kept state's row appended, one entry with its lines' change
        against its parent's (`state_lines.change`) and the texts and blobs
        the record does not hold yet; each blob named by its content's
        sha256, and kept as its change of its base where `bases` names one
        the record holds (the parent's blob of the same file) and that is
        the smaller; an update's `step`, its identity answer as the change
        of what the states give (`versions.identity_step`). Under the
        store's lock (`versions.record` holds it)."""
        for name, data in blobs.items():
            if hashlib.sha256(data).hexdigest() != name:
                raise ValueError(f"blob {name[:12]} is not named by its content")
        with self._locked():
            loaded = self._load()
            self._append_entry(
                loaded,
                EntryKind.STATE,
                {
                    "state": state,
                    "lines": lines,
                    "texts": {
                        t: list(runs)
                        for t, runs in texts.items()
                        if t not in loaded.texts
                    },
                    "blobs": {
                        n: _kept_form(d, self._base(loaded, (bases or {}).get(n)))
                        for n, d in blobs.items()
                        if n not in loaded.blobs
                    },
                    **({} if step is None else {"step": step}),
                },
            )

    def hold_identity(self, key: str, new_hash: str, payload: str) -> None:
        """An identity answer's payload, held as run data until its
        update keeps its step (`docs/architecture.md` § 4): one file a
        NEW version, so a `diff` and the `update` after it share it."""
        run_state.RunFile(self.directory, _identity_run(new_hash), new_hash, "").hold(
            key, IDENTITY, payload
        )

    def held_identity(self, key: str) -> dict[str, Any] | None:
        """The payload held for the identity answer `key`, else none."""
        for path in sorted((self.directory / run_state.RUNS).glob("identity-*.json")):
            held = json.loads(path.read_text(encoding="utf-8"))
            text = held.get("texts", {}).get(key)
            if text is not None:
                return json.loads(text)
        return None

    def drop_held_identity(self, new_hash: str) -> None:
        """NEW's held identity payloads dropped: its update kept its step."""
        run_state.remove(self.directory, _identity_run(new_hash))

    def kept_step(self, identity: str) -> tuple[str, dict[str, Any]] | None:
        """The step an update by the identity answer `identity` kept, and
        the id of the state it kept it with; the latest where several
        states kept one, of those not dropped (a step a dropped edit kept
        is gone with it). None where none did."""
        with self._locked():
            loaded = self._load()
            kept = {loaded.states[n]["id"] for n in loaded.in_effect(waiting=True)}
            steps = [s for s in loaded.steps.get(identity, []) if s[0] in kept]
            # seen from a state: the step that state kept, where it kept one
            own = [s for s in steps if s[0] == self._seen]
            return (own or steps or [None])[-1]

    def kept_states(self, waiting: bool = False) -> tuple[dict[str, Any], ...]:
        """Every kept state's row in effect, once each, in the order every
        copy reads alike (`_Loaded.kept_once`); `waiting`: those that wait
        for the person's word too."""
        with self._locked():
            loaded = self._load()
            return tuple(loaded.states[n] for n in loaded.kept_once(waiting))

    def state_tips(self, waiting: bool = False) -> tuple[str, ...]:
        """The ids of the kept states in effect that no other in effect was
        written after: one, or several where joined copies kept them apart
        (`docs/architecture.md` § 4, "Copies joined"); `waiting`: among
        those that wait for the person's word too."""
        with self._locked():
            loaded = self._load()
            effect = loaded.in_effect(waiting)
            if loaded.in_line:
                return tuple(loaded.states[n]["id"] for n in effect[-1:])
            by_entry = {loaded.state_hashes[n]: loaded.states[n]["id"] for n in effect}
            # one state kept alike twice, on two copies or on one, is one
            # state: it is under another state where any of its entries is
            # under one of that state's, never under its own
            tops = loaded.uppermost(list(by_entry))
            below: set[str] = set()
            for top in tops:
                under = loaded.below([top])
                below |= {
                    i for e, i in by_entry.items() if e in under and i != by_entry[top]
                }
            return tuple(
                dict.fromkeys(by_entry[e] for e in tops if by_entry[e] not in below)
            )

    def kept_lines(self, waiting: bool = False) -> tuple[dict[str, Any], ...]:
        """Every kept state's lines' change against its parent's, as
        `kept_states` gives the states."""
        with self._locked():
            loaded = self._load()
            return tuple(loaded.lines[n] for n in loaded.kept_once(waiting))

    def kept_texts(self) -> Mapping[str, tuple[str, ...]]:
        """Every kept text by its id."""
        with self._locked():
            return MappingProxyType(self._load().texts)

    def kept_blob(self, name: str) -> bytes | None:
        """A kept blob's bytes, as written; None where the record holds no
        such blob."""
        with self._locked():
            blobs = self._load().blobs
        return _blob_of(blobs, name)

    @staticmethod
    def _base(loaded: _Loaded, name: str | None) -> tuple[str, bytes] | None:
        if name is None:
            return None
        data = _blob_of(loaded.blobs, name)
        return None if data is None else (name, data)

    def kept_stamp(self) -> tuple[int, ...]:
        """The kept states as they stand: what is read from them alone
        holds while this does, a state being only ever added."""
        with self._locked():
            return self._load().kept_stamp()

    def opened(self) -> list[dict[str, Any]]:
        """Every entry as the reader's keys open it, in the order written:
        its hash, kind, parents, writer, epoch and body. The record's plain
        view, for a diff or a search of it."""
        path = self.record_path
        if not path.is_file():
            return []
        found: list[dict[str, Any]] = []
        with self._locked():
            loaded = _Loaded(self._mine)
            lines = _read_on(path)[0]
            loaded.extend(lines, path)
            for number, line in lines:
                at = _Where(path, number)
                digest, kind, parents, writer, written = loaded._opened(line, at)
                if kind is None:
                    continue
                header = _json(line[: line.find(_BODY)] + "}", path, number)
                body = _body(written, at)
                found.append(
                    {
                        "hash": digest,
                        "kind": kind.value,
                        "parents": list(parents),
                        "writer": writer,
                        "epoch": header["epoch"],
                        "body": body,
                    }
                )
        return found

    def _append_entry(self, loaded: _Loaded, kind: EntryKind, body: Any) -> None:
        """One entry after the heads `loaded` holds, sealed in the latest
        epoch; the record's first is preceded by the first paper key's
        grant, to the writer's reading key and recovery key. Called under
        the lock, `loaded` read under it."""
        mine = self._mine()
        lines: list[str] = []
        parents = loaded.heads()
        try:
            writing = loaded.writing()
        except RecordError as error:
            raise RecordError(f"{self.record_path}: {error}") from None
        if writing is not None:
            epoch, paper_key = writing
        else:
            if loaded.order:
                raise RecordError(
                    f"{self.record_path}: no trusted grant in the record wraps "
                    f"a paper key to your reading key {mine.id}"
                )
            try:
                signer = mine.signing_key()[1]
            except keys.KeysError as error:
                raise RecordError(f"{self.record_path}: {error}") from None
            epoch, paper_key = 1, keys.paper_key()
            digest, line = _entry_line(
                EntryKind.GRANT,
                parents,
                mine.id,
                _grant_body(
                    epoch,
                    paper_key,
                    [keys.Member(mine.reading.public_key(), signer)],
                    keys.public_text(mine.recovery),
                ),
                epoch,
                None,
            )
            lines.append(line)
            parents = [digest]
        lines.append(_entry_line(kind, parents, mine.id, body, epoch, paper_key)[1])
        self._write(self.record_path, lines)

    def _writing(self, loaded: _Loaded) -> tuple[int, bytes]:
        try:
            writing = loaded.writing()
        except RecordError as error:
            raise RecordError(f"{self.record_path}: {error}") from None
        if writing is None:
            raise RecordError(f"{self.record_path}: no record yet")
        return writing

    @staticmethod
    def _write(path: Path, lines: list[str]) -> None:
        with path.open("a+b") as handle:
            # A last line a crash cut short was never an entry: drop it so
            # the next line does not run on from it.
            size = handle.seek(0, 2)
            if size:
                handle.seek(size - 1)
                if handle.read(1) != b"\n":
                    handle.seek(0)
                    handle.truncate(handle.read().rfind(b"\n") + 1)
            handle.write("".join(lines).encode("utf-8"))
            handle.flush()

    @staticmethod
    def _stamp(path: Path) -> tuple[int, int, int] | None:
        """A file as it stands: its inode, size and time; None where it is
        missing. Every write appends, so a file's size and time change with
        every write, by this process or another; its inode changes when it
        is replaced."""
        try:
            stat = path.stat()
        except FileNotFoundError:
            return None
        return (stat.st_ino, stat.st_size, stat.st_mtime_ns)

    def _load(self) -> _Loaded:
        """The record as it stands, read once while it stands (`_stamp`),
        and where it was only appended to since (the same bytes first), its
        new lines alone. Called under the store's lock, so no write runs
        while it reads."""
        path = self.record_path
        stamp = self._stamp(path)
        held = self._loaded
        if held is not None and held.stamp == stamp:
            return held
        if stamp is None:
            self._loaded = _Loaded(self._mine)
            # a record removed whole is held against its tip too
            self._held_tip(self._loaded, path)
            return self._loaded
        reaches = () if held is None or held.reach is None else (held.reach,)
        lines, reach, begun = _read_on(path, reaches)
        if begun is not None and any('"nonce":null,' in line for _, line in lines):
            # a grant may close an epoch at entries read before it: what
            # counts is worked out again from the whole record
            lines, reach, begun = _read_on(path)
            held = None
        loaded = held if held is not None and begun is not None else _Loaded(self._mine)
        loaded.extend(lines, path)
        if loaded.withheld and not loaded.held:
            raise RecordError(
                f"{path}: no trusted grant in the record wraps a paper key to "
                f"your reading key {self._mine().id}"
            )
        self._held_tip(loaded, path)
        loaded.stamp, loaded.reach = stamp, reach
        loaded.torn = (
            f"{path}:{reach.lines + 1}: the last line has no newline; skipped"
            if stamp[1] > reach.end
            else None
        )
        self._loaded = loaded
        return loaded

    def _tip(self, loaded: _Loaded) -> Path | None:
        """Where this machine keeps its tip of the record in this
        directory, by the directory alone, so a record cut to nothing or
        removed whole still finds it; None where it keeps none (keys held
        in memory alone), or no keys are here and the record holds no entry
        (nothing to read)."""
        try:
            home = self._mine().home
        except RecordError:
            if loaded.order:
                raise
            return None
        if home is None:
            return None
        name = hashlib.sha256(str(self._key).encode()).hexdigest()
        return home / TIPS / f"{name[:32]}.json"

    def forget_tip(self) -> None:
        """This directory's tip removed: a scratch copy's, once the copy
        is gone, so a dry run leaves nothing beside the person's keys."""
        pin = self._tip(_Loaded(self._mine))
        if pin is not None:
            pin.unlink(missing_ok=True)

    def _held_tip(self, loaded: _Loaded, path: Path) -> None:
        """The record held against the tip this machine last read of it
        here: refused where it no longer reaches every head of that tip,
        cut short, set back or cut to nothing (a host may serve an older
        record, every line of it still signed and sealed, or none); else
        the tip moved to the record's heads now. The tip is kept beside the
        person's keys, out of the paper's repository a host could set back
        with it, by the record's directory, so a second copy on one machine
        has its own. Without a tip held (a fresh clone, keys held in memory
        alone) nothing can be told: `unpinned` says so. A record that holds
        no entry and no tip held is a new one: no tip is kept for it."""
        pin = self._tip(loaded)
        if pin is None:
            return
        try:
            held = json.loads(pin.read_text(encoding="utf-8"))["heads"]
        except FileNotFoundError:
            held = None
        except (OSError, ValueError, KeyError) as error:
            raise RecordError(
                f"{pin}: the tip held of {path} is unreadable: {error}"
            ) from None
        # a record that holds no entry yet has nothing to pin
        self._unpinned = self._unpinned or (held is None and bool(loaded.order))
        if held is not None:
            gone = [h for h in held if h not in loaded.parents]
            if gone and not loaded.order:
                raise RecordError(
                    f"{path}: the record holds no entry, but this machine read "
                    f"one of it ({', '.join(h[:12] for h in gone)}): it was cut to "
                    "nothing or removed. Where you removed it yourself, remove "
                    f"{pin} and read it again"
                )
            if gone:
                raise RecordError(
                    f"{path}: the record no longer reaches the tip this machine "
                    f"read of it ({', '.join(h[:12] for h in gone)}): it was cut "
                    "short or set back. Where you set it back yourself, remove "
                    f"{pin} and read it again"
                )
        # the uppermost entries read, never a withheld line's parents
        # dropped from it: a line no one can open, appended on top, would
        # leave no tip and let a later cut through
        heads = loaded.uppermost([d for d in loaded.order if d not in loaded.withheld])
        if heads != held and (heads or held is not None):
            pin.parent.mkdir(parents=True, exist_ok=True)
            written = pin.with_suffix(f".{os.getpid()}.part")
            written.write_text(
                json.dumps({"record": str(self._key), "heads": heads}) + "\n",
                encoding="utf-8",
            )
            os.replace(written, pin)

    def unpinned(self) -> bool:
        """Whether no tip of this record was held on this machine before
        this read: a record cut short is caught only from now on."""
        with self._locked():
            self._load()
            return self._unpinned

    def _whole(self) -> _Whole:
        """The answers whose records were all written, their corrections and
        flags, the records of the answers left out, and what was left out.
        An answer is left out by its entry, since a resumed run may ask its
        key again. Worked out once while the record stands; called under
        the store's lock."""
        loaded = self._load()
        if loaded.whole is None:
            loaded.whole = _read_whole(loaded)
        answers, corrections, partial, flags, problems = loaded.whole
        return list(answers), list(corrections), partial, list(flags), list(problems)

    def answers(self) -> tuple[AnswerRecord, ...]:
        with self._locked():
            return tuple(self._whole()[0])

    def corrections(self) -> tuple[CutCorrection, ...]:
        with self._locked():
            return tuple(self._whole()[1])

    def flags(self) -> tuple[Flag, ...]:
        with self._locked():
            return tuple(self._whole()[3])

    def problems(self) -> tuple[str, ...]:
        """What the loader left out: a last line cut short, an answer whose
        records were not all written."""
        with self._locked():
            loaded = self._load()
            torn = loaded.torn
            return (
                *([torn] if torn else []),
                *loaded.withheld.values(),
                *self._whole()[4],
            )

    def next_attempt(self, key: str) -> int:
        """One more than the highest attempt at `key` in the store, counting
        an answer the loader ignores; 1 for a key never asked."""
        with self._locked():
            return 1 + max(
                (n for k, n in self._load().answered().written if k == key),
                default=0,
            )

    def waiting_entries(self) -> frozenset[str]:
        """The answers' entries without effect until the person confirms
        (`_Loaded.taken_back`): theirs no confirmation covers, and what a
        drop or their waiting edit takes with it."""
        with self._locked():
            return self._load().taken_back()[1]

    def without_effect(self) -> frozenset[str]:
        """The keys of the answers without effect until the person confirms:
        theirs no confirmation covers, and what a drop or their waiting
        edit takes with it (`_Loaded.taken_back`): out of
        `judgment_records`, out of the person rounds, and every state made
        from one out of the kept states."""
        with self._locked():
            return self._load().without_effect()

    def pending(self) -> tuple[PendingEntry, ...]:
        """The answers no confirmation covers yet, in the order written:
        a person's word among them without effect until one does."""
        with self._locked():
            return tuple(
                PendingEntry(digest, x.answer, is_persons_word(x.answer))
                for digest, x in self._load().answered().pending.items()
            )

    def waiting(self) -> tuple[PendingEntry, ...]:
        """The person's filings that wait for their word: without effect
        until `confirm` signs it."""
        if not self.record_path.is_file():
            return ()
        return tuple(p for p in self.pending() if p.binds)

    def refuse_waiting(self, doing: str, own: frozenset[str] = frozenset()) -> None:
        """Refused where a person's filing waits for their word: a share
        would leave it out, and a paid run kept on it would be lost with a
        drop. Nothing confirms for the person; they confirm, or drop what
        they do not want."""
        found = [p for p in self.waiting() if p.answer.key not in own]
        if found:
            named = ", ".join(
                f"{p.answer.kind} {p.answer.key[:12]}" for p in found[:5]
            ) + (f" and {len(found) - 5} more" if len(found) > 5 else "")
            raise RecordError(
                f"{len(found)} of your filings wait for your word ({named}): "
                f"{doing} waits for them. Run `axiomation confirm` first "
                "(`--drop` for any you do not want)"
            )

    def refused_confirmations(self) -> tuple[tuple[str, str], ...]:
        """Each confirmation whose signature does not hold, by hash, with
        why: it confirms nothing."""
        with self._locked():
            return self._load().standing().refused

    def taken_by(self, dropped: Sequence[str]) -> tuple[frozenset[str], frozenset[str]]:
        """What a drop takes back once `confirm` with `dropped` signs: the
        kept states' ids and the answers' entries, an earlier drop's
        among them (`_Loaded._taken_with`)."""
        with self._locked():
            loaded = self._load()
            if not dropped:
                return loaded.taken_back(False)
            before = frozenset(loaded.answered().dropped)
            return loaded._taken_with(before | frozenset(_drops(loaded, dropped)))

    def confirm(self, dropped: Sequence[str] = ()) -> str:
        """The person's word on the record as it stands, signed: every
        entry stands confirmed, but the pending filings `dropped` names
        (each by its answer's key or its entry's hash, a prefix of either
        will do), which never take effect. Its entry's hash."""
        with self._locked():
            loaded = self._load()
            pending = loaded.answered().pending
            drops = _drops(loaded, dropped)
            # a filing of the person's built on a dropped one goes with it,
            # or it stands confirmed on a state no view holds: they drop it
            # too; an agent's goes with it (`_Loaded._taken_with`)
            before = frozenset(loaded.answered().dropped)
            taken = loaded._taken_with(before | frozenset(drops))[1]
            built = [
                d
                for d, x in pending.items()
                if d not in drops and d in taken and is_persons_word(x.answer)
            ]
            if built:
                # an earlier drop (one a joined copy made, say) leaves no
                # keeping it
                earlier = loaded.taken_back(False)[1]
                said = []
                for group, why in (
                    (
                        [d for d in built if d in earlier],
                        "are built on an edit dropped already: drop them too",
                    ),
                    (
                        [d for d in built if d not in earlier],
                        "are built on one you drop: drop them too, or keep it",
                    ),
                ):
                    if group:
                        named = ", ".join(
                            dict.fromkeys(pending[d].answer.key[:12] for d in group)
                        )
                        said.append(f"the edits {named} {why}")
                raise RecordError("; ".join(said))
            if not loaded.order:
                raise RecordError(f"{self.record_path}: nothing to confirm")
            mine = self._mine()
            heads = loaded.heads()
            try:
                signature = keys.signed(
                    mine, _confirmation_message(mine.id, heads, drops)
                )
            except keys.KeysError as error:
                raise RecordError(str(error)) from None
            digest, line = _entry_line(
                EntryKind.CONFIRMATION,
                heads,
                mine.id,
                {"dropped": drops, "signature": signature},
                *self._writing(loaded),
            )
            self._write(self.record_path, [line])
            return digest

    def join(self, other: Path) -> int:
        """Every entry the record in `other` holds and this one does not,
        appended in the order written there, as a git union merge of the
        two files reads: two copies of one record, each appended to on its
        own machine, made one. Refused where `other` is not a copy of this
        record (its first entry another), or where the two would not load
        together; nothing is written then. How many entries were appended."""
        theirs = [line for _, line in _read_on(Path(other) / RECORD)[0]]
        with self._locked():
            loaded = self._load()
            if loaded.order and theirs:
                first = _HEAD.match(theirs[0])
                if first is None or first.group(1) != loaded.order[0]:
                    raise RecordError(
                        f"{other} holds no copy of the record in {self.directory}"
                    )
            held = set(loaded.order)
            new = [
                line
                for line in theirs
                if (head := _HEAD.match(line)) is None or head.group(1) not in held
            ]
            if not new:
                return 0
            ours = _read_on(self.record_path)[0] if self.record_path.is_file() else []
            trial = _Loaded(self._mine)
            trial.extend([*ours, *enumerate(new, len(ours) + 1)], self.record_path)
            self._write(self.record_path, [f"{line}\n" for line in new])
            return len(new)

    def sent(self) -> tuple[Sent, ...]:
        """The shares sent, in the order written."""
        with self._locked():
            loaded = self._load()
            return tuple(
                Sent(
                    digest,
                    body["share"],
                    body["round"],
                    body["reader"],
                    body["state"],
                    body.get("continues"),
                )
                for digest, kind, _, body in loaded.notes
                if kind is EntryKind.SENT
            )

    def record_sent(
        self, share: str, round: int, reader: str, state: str, continues: str | None
    ) -> None:
        """A share sent, appended: what a later round continues from."""
        with self._locked():
            self._append_entry(
                self._load(),
                EntryKind.SENT,
                {
                    "share": share,
                    "round": round,
                    "reader": reader,
                    "state": state,
                    "continues": continues,
                },
            )

    def reviews(self) -> tuple[tuple[str, dict[str, Any]], ...]:
        """The reviews received, each its entry's hash and body, in the
        order written."""
        with self._locked():
            loaded = self._load()
            return tuple(
                (digest, body)
                for digest, kind, _, body in loaded.notes
                if kind is EntryKind.REVIEW
            )

    def record_review(self, body: dict[str, Any]) -> str:
        """A review received, appended: its entry's hash."""
        with self._locked():
            loaded = self._load()
            self._append_entry(loaded, EntryKind.REVIEW, body)
            return self._load().order[-1]

    def members(self) -> tuple[keys.Member, ...]:
        """The members of the trusted grant the record stands on; where two
        were made apart, those both name."""
        with self._locked():
            loaded = self._load()
            return tuple(_members(loaded, loaded.current()).values())

    def grant(
        self,
        add: Sequence[keys.Member] = (),
        remove: Sequence[str] = (),
        settle: bool = False,
    ) -> int:
        """A new epoch: a new paper key, wrapped to the members of the grant
        the record stands on with `add` and without those `remove` names
        (by id or a prefix of it), and to the recovery key; every entry
        after it sealed under it. Signed: the person's passphrase is asked
        (`_grant_message`). A removed member keeps what they read before
        and reads nothing after; what they write after counts for nothing.
        Where two grants were made apart, refused unless `settle`: then the
        new grant follows both and names the members both name, a member
        added on one side alone added again by name. The writer cannot
        remove their own key: they add their new key, then remove the old
        one with the new. The new epoch."""
        with self._locked():
            loaded = self._load()
            mine = self._mine()
            current = loaded.current()
            if not current:
                raise RecordError(f"{self.record_path}: no record to grant yet")
            if len(current) > 1 and not settle:
                loaded.writing()
            if len(current) == 1 and settle:
                raise RecordError(f"{self.record_path}: no two grants to settle")
            members = _members(loaded, current)
            for name in remove:
                found = [m for m in members if m.startswith(name)]
                if len(found) != 1:
                    raise RecordError(
                        f"{name!r} names {len(found) or 'no'} members; name one "
                        "of `axiomation members`'s list"
                    )
                if found[0] == mine.id:
                    raise RecordError(
                        "you cannot remove your own reading key: add your new "
                        "key's card, then remove the old key using the new one"
                    )
                del members[found[0]]
            for member in add:
                if member.id in members:
                    raise RecordError(f"{member.id} is a member already")
                members[member.id] = member
            parents = loaded.heads()
            body = _grant_body(
                max(g.epoch for g in loaded.trusted.values()) + 1,
                keys.paper_key(),
                list(members.values()),
                loaded.trusted[current[0]].recovery,
                add,
                loaded.held,
            )
            try:
                body["signature"] = keys.signed(
                    mine, _grant_message(mine.id, parents, body)
                )
            except keys.KeysError as error:
                raise RecordError(str(error)) from None
            _, line = _entry_line(
                EntryKind.GRANT, parents, mine.id, body, body["epoch"], None
            )
            self._write(self.record_path, [line])
            return body["epoch"]

    def authoritative(self) -> dict[str, AnswerRecord]:
        """The first valid answer per key, by ancestry (`_Loaded.firsts`);
        none for a key whose answers conflict (`answer_conflicts`), but
        where the store is seen from a state built on one (`seen_from`)."""
        with self._locked():
            return {k: x.answer for k, x in self._firsts_seen().items()}

    def filed_order(self) -> dict[str, FiledAt]:
        """Each answer's key by its place (`FiledAt`): the entries of the
        answer that speaks for it (its first valid one and the equal ones
        made apart beside it; a conflict's answers; else every attempt),
        their latest in the order every copy reads alike (`_Loaded.ranks`),
        which sorts, and the entries, by which `filed_after` tells whether
        it was filed after another. An attempt refused is no filing: one
        between two attempts of a key would otherwise read as both before
        and after it."""
        with self._locked():
            loaded = self._load()
            ranks = loaded.ranks()
            firsts = loaded.firsts()
            entries: dict[str, list[str]] = {}
            for x in loaded.answered().answers:
                entries.setdefault(x.answer.key, []).append(x.entry)
            for key, twins in firsts.twins.items():
                entries[key] = list(twins)
            for key, torn in firsts.conflicts.items():
                entries[key] = [x.entry for x in torn]
            return {
                key: FiledAt(
                    max(ranks[e] for e in found), frozenset(found), loaded.follows
                )
                for key, found in entries.items()
            }

    def filing_conflicts(self) -> tuple[FilingConflict, ...]:
        """Each place two joined copies' filings spoke apart and
        differently: a judgment's subject and aspect, a ruling's subject, a
        finding's or a suggestion's answer. Neither was filed after the
        other, so neither speaks over the other; an entry after both settles
        nothing (`docs/architecture.md` § 4, "Copies joined")."""
        with self._locked():
            if self._load().in_line:
                return ()
        held = self.judgment_records(waiting=True)
        with self._locked():
            loaded = self._load()
            firsts = loaded.firsts()
            spoken: dict[tuple[str, Any], list[tuple[tuple[str, ...], Any]]] = {}
            for kind, records, place, says in (
                (
                    "judgment",
                    held.judgments,
                    lambda r: (r.subject, r.aspect),
                    "verdict",
                ),
                ("ruling", held.rulings, lambda r: r.subject, "kind"),
                ("finding answer", held.answers, lambda r: r.finding, "status"),
                (
                    "suggestion answer",
                    held.suggestion_answers,
                    lambda r: r.suggestion,
                    "status",
                ),
            ):
                for r in records:
                    if r.answer in firsts.first:
                        spoken.setdefault((kind, place(r)), []).append(
                            (firsts.twins[r.answer], getattr(r, says))
                        )

            def after(later: tuple[str, ...], earlier: tuple[str, ...]) -> bool:
                # a filing made apart as equal twins is after another where
                # any of its entries is
                return any(loaded.follows(e, o) for e in later for o in earlier)

            found = []
            for (kind, place), said in spoken.items():
                torn = sorted(
                    {
                        e
                        for es, word in said
                        for fs, other in said
                        if word != other and not after(es, fs) and not after(fs, es)
                        for e in es
                    }
                )
                if torn:
                    found.append(FilingConflict(kind, str(place), tuple(torn)))
            return tuple(sorted(found))

    def answer_conflicts(self) -> dict[str, tuple[str, ...]]:
        """Each key two joined copies answered apart and differently: its
        answers' entries. Neither is reused, nor is the key asked again,
        until the copies' work is merged."""
        with self._locked():
            return {
                k: tuple(x.entry for x in found)
                for k, found in self._load().firsts().conflicts.items()
            }

    def given_answers(
        self, version: str, waiting: bool = False
    ) -> tuple[AnswerRecord, ...]:
        """The answers given on the version `version` (a document hash) by a
        person or an agent, the authoritative
        ones, in store order; `waiting`: the person's answers that wait
        for their word too, as their next edit reads them
        (`versions.pending_answers`), never a dropped one."""
        with self._locked():
            held_back = self._load().without_effect(waiting=not waiting)
            return tuple(
                a
                for a in self.authoritative().values()
                if a.host in GIVEN_HOSTS
                and a.version == version
                and a.key not in held_back
            )

    def _first_entry(self, key: str) -> _Answered | None:
        """The entry of the key's authoritative answer; called under the
        lock."""
        return self._firsts_seen().get(key)

    def corrections_of(self, key: str) -> tuple[CutCorrection, ...]:
        """The corrections of the key's authoritative answer, in the order
        written; none when it has none."""
        with self._locked():
            first = self._first_entry(key)
            return () if first is None else first.read(_CORRECTIONS)

    def edge_records_of(self, key: str) -> EdgeRecords:
        """The edge stage's records of the key's authoritative answer, each
        kind in the order written; none when it has none."""
        with self._locked():
            first = self._first_entry(key)
            if first is None:
                return EdgeRecords()
            return EdgeRecords(**{k.field: first.read(k) for k in EDGE_KINDS})

    def flags_of(self, key: str) -> tuple[Flag, ...]:
        """The flags of the key's authoritative answer, in the order
        written; none when it has none."""
        with self._locked():
            first = self._first_entry(key)
            return () if first is None else first.read(_FLAGS)

    def vocabulary_of(self, key: str) -> tuple[VocabularyEntry, ...]:
        """The vocabulary entries of the key's authoritative answer, in the
        order written; none when it has none."""
        with self._locked():
            first = self._first_entry(key)
            return () if first is None else first.read(_VOCABULARY)

    def declines_of(self, key: str) -> tuple[Decline, ...]:
        """The declines of the key's authoritative answer, in the order
        written; none when it has none."""
        with self._locked():
            first = self._first_entry(key)
            return () if first is None else first.read(_DECLINES)

    def judgment_records(self, waiting: bool = False) -> JudgmentRecords:
        """Every filing's judgments, findings and finding answers,
        and the cited pages and works seen:
        those of the authoritative answers whose records were all written,
        each kind in the order written; `waiting`: those that wait for
        the person's word too."""
        with self._locked():
            loaded = self._load()
            firsts = loaded.firsts().first
            if loaded.judgments is None:
                # every answer's records read, so one the fold leaves out is
                # still refused where it is not a record the store writes
                for x in loaded.answered().answers:
                    for k in JUDGMENT_KINDS:
                        x.read(k)
                loaded.judgments = {
                    k.field: tuple(
                        (x.answer.key, r) for x in firsts.values() for r in x.read(k)
                    )
                    for k in JUDGMENT_KINDS
                }
            found = loaded.judgments
            held_back = loaded.without_effect(waiting=not waiting)
        return JudgmentRecords(
            **{
                kind.field: tuple(
                    r for key, r in found[kind.field] if key not in held_back
                )
                for kind in JUDGMENT_KINDS
            }
        )

    def lineage_of(self, key: str) -> tuple[LineageLink, ...]:
        """The lineage of the identity answer `key`, in the order written;
        none when it has none."""
        with self._locked():
            first = self._first_entry(key)
            return () if first is None else first.read(_LINEAGE)

    def identity_for(
        self, new_hash: str, key: str | None = None
    ) -> AnswerRecord | None:
        """The identity answer whose NEW version hashes to `new_hash`, found
        by the hash its update's step names (or, before its update kept
        one, its held payload), since recomputing its key needs OLD's
        text, gone after an edit in place; `key` picks
        one when updates from two OLDs reached the same NEW, else that is
        refused. None when the store holds none."""
        found = []
        for answer in self.authoritative().values():
            if answer.kind != IDENTITY:
                continue
            kept = self.kept_step(answer.key)
            named = (
                kept[1].get("raw", {}).get("new")
                if kept is not None
                else (self.held_identity(answer.key) or {}).get("new")
            )
            if named == new_hash:
                found.append(answer)
        if key is not None:
            return next((a for a in found if a.key == key), None)
        if len(found) > 1:
            raise RecordError(
                f"{len(found)} identity answers lead to {new_hash[:12]}: name one "
                f"with --identity ({', '.join(a.key[:12] for a in found)}), or make "
                "it again with --from"
            )
        return found[0] if found else None


def _identity_run(new_hash: str) -> str:
    return f"identity-{new_hash[:16]}"


def _read_whole(loaded: _Loaded) -> _Whole:
    answers: list[AnswerRecord] = []
    corrections: list[CutCorrection] = []
    flags: list[Flag] = []
    problems: list[str] = []
    partial: set[str] = set()
    for x in loaded.answered().answers:
        missing = [i for i in x.answer.records if i not in x.ids]
        if missing:
            problems.append(
                f"answer {x.answer.key} attempt {x.answer.attempt}: records "
                f"{missing} were never written; the answer is ignored"
            )
            partial.update(x.answer.records)
            continue
        answers.append(x.answer)
        corrections.extend(x.read(_CORRECTIONS))
        flags.extend(x.read(_FLAGS))
    return answers, corrections, frozenset(partial), flags, problems


def _json(line: str, path: Path, number: int) -> Any:
    try:
        return json.loads(line)
    except json.JSONDecodeError as error:
        raise RecordError(f"{path}:{number}: not JSON: {error}") from None
