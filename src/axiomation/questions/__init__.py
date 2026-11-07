"""Questions: what an agent step asks, under `docs/architecture.md` § 2's contract.

A question is its declared inputs, a template with a version, a validator
and a key. Defined here, never run: the runner (`axiomation.runner`) asks
them and the resolver (`axiomation.resolver`) records the answers. Nothing
here imports either, or talks to a model (`tests/test_spine.py`).
"""

from __future__ import annotations

import hashlib
import re
import string
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from importlib import resources

from axiomation.cutter.model import Cut
from axiomation.records import (
    CutCorrection,
    Decline,
    EdgeRecords,
    Flag,
    JudgmentRecords,
    TemplateRef,
    VocabularyEntry,
)


class Stage(StrEnum):
    """The stages a question belongs to: the vocabulary map, before the
    cut, and the cut review's. The judge and the deciding step answer
    boundary corrections, but each as its own stage so the store and the
    payload tell their answers from the author's."""

    BOUNDARIES = "boundaries"
    JUDGE = "judge"
    ROLES = "roles"
    GROUPS = "groups"
    DECIDE = "decide"
    EDGES = "edges"
    #: an argument question asked by a script outside a review session:
    #: its sessions are recorded under it, so their runs replay
    ARGUMENT = "argument"
    #: a review session's sheet of judgments and findings
    REVIEW = "review"
    #: a paper's vocabulary map: what each name its tables do not know is for
    VOCABULARY = "vocabulary"
    #: the map's second round, on the names it answered `none`
    VOCABULARY_CHECK = "vocabulary-check"


@dataclass(frozen=True, slots=True)
class Template:
    """A prompt template. Its version is a hash of its text, so an edit can
    never reuse an old key by a forgotten bump. Placeholders
    are `string.Template`'s `$name`; a literal dollar is written `$$`.
    `asks` is a review brief's version of each question it asks past the
    first (`review_file.asks`), recorded with its answers and keying its
    questions with the text."""

    name: str
    text: str
    asks: tuple[tuple[str, int], ...] = ()

    @property
    def version(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()[:12]

    @property
    def ref(self) -> TemplateRef:
        return TemplateRef(self.name, self.version, self.asks)

    def render(self, **values: str) -> str:
        return string.Template(self.text).substitute(values)


def load_template(name: str) -> Template:
    """The template `templates/<name>.md`, shipped with the package."""
    text = (
        resources.files("axiomation.questions")
        .joinpath("templates", f"{name}.md")
        .read_text(encoding="utf-8")
    )
    return Template(name, text)


#: A gate may log each check it runs here, one JSON object a line, beside
#: its spec; the host adds each to the attempt's transcript as an event of
#: type `gate_check`.
GATE_LOG = "gate-checks.jsonl"


@dataclass(frozen=True, slots=True)
class Gate:
    """A check a host runs on the edited file each time the session tries
    to end: `python -m <module> <spec file> <edited file>`, run
    by the host, outside the session's sandbox, so the session never reads
    its code. `spec` is what the check needs besides the file, as text.
    The check's problems go back to the session, which fixes them; the
    runner's check stays the answer's gate. With `on_write`, the host also
    runs it after each of the session's tool uses, as `python -m <module>
    <spec file> <edited file> write`: a check of what is wrong so far, the
    unfinished left out, that never stops the session."""

    module: str
    spec: str
    on_write: bool = False


#: The tools of a session on files that names its own (`Workspace.tools`):
#: Read for its files, Edit and Write for its answer, Bash for `grep`, a
#: slice of a long file and the commands its brief gives it.
FILE_TOOLS = ("Bash", "Read", "Edit", "Write")
#: The tools of a review session by block that computes nothing, besides its
#: tool server's (`Workspace.served`): Read and Grep for `paper.md`, Edit and
#: Write for its answer; no Bash.
REVIEW_TOOLS = ("Read", "Grep", "Edit", "Write")
#: A review session by block's that computes for itself (a case, a limit, a
#: sum): Bash too, in its sandbox, which writes only in its directory and
#: /tmp, reads nothing the host closes, and reaches no network.
REVIEW_COMPUTE_TOOLS = ("Bash", *REVIEW_TOOLS)

#: The variables that name a session's gate spec to `axiomation
#: review-check`, the check it runs itself (`Gate.self_check`), and the
#: spec's SHA-256 as its host wrote it.
GATE_ENV = "AXIOMATION_GATE"
GATE_DIGEST_ENV = "AXIOMATION_GATE_DIGEST"


@dataclass(frozen=True, slots=True)
class Reads:
    """What a session reads of the works its paper cites, through its tool
    server: the paper's directory and store, which give its sources, and
    the cited works it may read (docs/architecture.md § 7)."""

    paper: str
    store: str
    works: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Helper:
    """A subagent a session may hand a narrow task to, on its own model and
    tools (the citationcheck session's page finder): its name, what the session
    is told it does, its own instructions."""

    name: str
    description: str
    prompt: str
    model: str
    tools: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Workspace:
    """The files an agent session works on: each by its path in
    the session's directory, with its text. The session may change `edit`
    alone, and that file's text when it ends is its answer; it reads the
    others. `instructions` is its standing brief, given as the host's
    system prompt."""

    files: Mapping[str, str]
    edit: str
    instructions: str
    #: Whether a re-ask starts from the refused session's edit, to fix what
    #: was wrong and keep the rest (the roles session), rather
    #: than from the files as they were.
    reask_from_edit: bool = False
    #: A check the host runs before the session may end, where it can.
    gate: Gate | None = None
    #: The files the session searches in place rather than reads whole (a
    #: cited work's pages): the estimate leaves them out.
    searched: frozenset[str] = frozenset()
    #: The subagents the session may hand a task to.
    helpers: tuple[Helper, ...] = ()
    #: The tools it is given, in place of its host's (a session that
    #: answers by block or as lines); None, the host's.
    tools: tuple[str, ...] | None = None
    #: Whether its brief is its whole system prompt, in place of the
    #: host's own, which speaks of a coding agent's work (a review
    #: session); else the brief is appended to the host's.
    brief_alone: bool = False
    #: The tools a tool server serves it (a review session; a queue's brief
    #: carries them for its queue's server): its check and its cited works'
    #: reads; None, no tool server.
    served: SessionTools | None = None


@dataclass(frozen=True, slots=True)
class SessionTools:
    """What a review session is served by its tool server
    (docs/architecture.md § 7): `check`, its gate's check on its answer,
    with `reads` the `cited_*` reads of its cited works, and with
    `fetches` a `fetch` of a work found outside them, read then as they
    are. The server's settings lie under the store's `sessions/` (`store`)
    while it runs, where the session cannot write, so the spec it checks
    against is the one its host wrote."""

    store: str
    reads: Reads | None = None
    fetches: bool = False


@dataclass(frozen=True, slots=True)
class ToolServer:
    """A tool server a queue session is given, MCP over stdio: the command
    that starts it, the environment it runs in, and how long its longest
    call may take, which the host's tool limit must pass
    (docs/architecture.md § 2)."""

    #: its name in the session's configuration: its tools are
    #: `mcp__<name>__<tool>` to the host
    name: str
    argv: tuple[str, ...]
    env: Mapping[str, str]
    call_seconds: float


@dataclass(frozen=True, slots=True)
class QueueSession:
    """A queue session: the message that starts it, its brief as the
    host's whole system prompt, its tool server, its only tools besides the
    host's reads of `files`, the context it compacts at (None: the host's
    own bound), and the files laid in its folder."""

    prompt: str
    instructions: str
    server: ToolServer
    context_tokens: int | None = None
    #: files laid in the session's folder by path, read with the host's own
    #: read tools, confined there
    files: Mapping[str, str] = field(default_factory=dict)
    #: whether it searches the web: WebSearch, the one web tool a host lets
    #: through, given beside its server's tools (its step's `web`)
    web: bool = False


@dataclass(frozen=True, slots=True, eq=False)
class Question:
    """One question, ready to ask. `handles` maps each name the prompt uses
    for a block or a group (`b1`, `g1`, `after`) to its id; `input_cut` is
    the cut its answer is validated and applied on. A question with a
    `workspace` is an agent session on files, and `prompt` only starts it;
    one without is answered from its prompt alone. A judge's question
    (stage `judge`) names in `judges` the author question it
    reviews. `turns_estimate` is what a session's turns add to the
    runner's one-attempt estimate, in dollars, where a price fitted on
    recorded sessions gives it (a review session's, docs/architecture.md
    § 3); not in the key."""

    key: str
    kind: str
    stage: Stage
    template: Template
    prompt: str
    handles: Mapping[str, str] = field(default_factory=dict)
    input_cut: Cut | None = None
    workspace: Workspace | None = None
    judges: str | None = None
    turns_estimate: float = 0.0


@dataclass(frozen=True, slots=True)
class Validation:
    """An answer checked: its corrections, or the edge stage's records, and
    the flags its `?` lines raise, or a review
    sheet's judgments, findings and the pages they read, named by
    id with their own `id` left for the resolver to set, and its problems.
    Valid when it has no problem; a valid answer may hold no record."""

    corrections: tuple[CutCorrection, ...] = ()
    problems: tuple[str, ...] = ()
    edges: EdgeRecords = EdgeRecords()
    flags: tuple[Flag, ...] = ()
    judgments: JudgmentRecords = JudgmentRecords()
    #: The answer's text as the store keeps it, where the check lays what
    #: the session wrote into another form (the edges' lines by owner into
    #: their sheet); None, as written. The resolver records it.
    kept: str | None = None
    #: For a review answer that read a part of the text for findings, the
    #: nodes of the blocks it read (`AnswerRecord.read`), refused or not.
    read: tuple[str, ...] | None = None
    #: A vocabulary answer's entries as the check read them.
    vocabulary: tuple[VocabularyEntry, ...] = ()
    #: A judge's answer's declined edits, settled.
    declines: tuple[Decline, ...] = ()

    @property
    def valid(self) -> bool:
        return not self.problems


#: A re-ask shows at most this much of the answer it corrects, so a re-ask
#: stays within a host's prompt limit whatever the model wrote.
REASK_ANSWER_LIMIT = 20_000
#: And at most this many of its problems, each cut to this many characters.
PROBLEMS_SHOWN = 30
PROBLEM_LIMIT = 500


def fence(text: str, info: str = "") -> str:
    """`text` in a code fence longer than any backtick run inside it."""
    longest = max((len(m) for m in re.findall(r"`+", text)), default=0)
    ticks = "`" * max(3, longest + 1)
    return f"{ticks}{info}\n{text}\n{ticks}"


def reask(question: Question, raw: str, problems: Sequence[str]) -> str:
    """The prompt again, the answer it gave and its problems, and the
    instruction to answer again in full. A session starts
    again from its files as they were, told what was wrong with the last
    one's edit."""
    shown = raw
    note = ""
    if len(raw) > REASK_ANSWER_LIMIT:
        shown = raw[:REASK_ANSWER_LIMIT]
        note = f" (its first {REASK_ANSWER_LIMIT:,} characters)"
    shown_problems = [
        problem if len(problem) <= PROBLEM_LIMIT else problem[:PROBLEM_LIMIT] + " …"
        for problem in problems[:PROBLEMS_SHOWN]
    ]
    if len(problems) > PROBLEMS_SHOWN:
        shown_problems.append(f"and {len(problems) - PROBLEMS_SHOWN} more")
    listed = "\n".join(f"- {problem}" for problem in shown_problems)
    if question.workspace is not None:
        if question.workspace.reask_from_edit:
            return (
                f"{question.prompt}\n\n"
                f"An earlier session's edit of `{question.workspace.edit}` could "
                f"not be used:\n\n{listed}\n\n"
                "The file is as that session left it. Correct these problems, "
                "and keep the rest.\n"
            )
        return (
            f"{question.prompt}\n\n"
            f"An earlier session's edit of `{question.workspace.edit}` could "
            f"not be used:\n\n{listed}\n\n"
            "The file is as it was before that session. Correct it again, "
            "in full.\n"
        )
    return (
        f"{question.prompt}\n\n"
        f"## Your previous answer{note}\n\n{fence(shown)}\n\n"
        f"## What is wrong with it\n\n{listed}\n\n"
        "Answer again, in full: the whole JSON object with every correction "
        "you propose, not only the ones you change.\n"
    )
