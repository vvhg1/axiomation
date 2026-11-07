"""A review session's sheet: `review.md`, read and gated.

A review session reads the paper as its kept state shows it (the edges
sheet, `paper.md`) and writes `review.md`. The sheet opens with a header
naming what the session may write, then `---`, then, for a session that
judges, its subjects: each a fixed line `⟦s3 valid⟧ J → T · "<claim>"`
or `⟦s4 true⟧ B · "<claim>"`, the edges its reading rests on (the one hop,
the basis rule) under it as fixed `  ↳ ` lines. The session adds
lines anywhere below the header:

- `judge <subject> <verdict> <depth> [pages KEY:N, …] — <evidence>`: one per
  subject, every subject of a session that judges (`Column.judges`); a
  `computed` subject's names its run and the values it
  matched instead of pages: `judge <subject> <verdict> <depth> [run <file>
  [args…]] — ["<the text's words>" ↔ "<the output's>"; …] — <evidence>`;
- `find <aspect> <anchor…> <severity> "<quoted words>" — <problem> [—
  <fix>]`: anchors are handles of blocks or groups, `paper`, `work:KEY`,
  or, first, `file:KEY` (a script's lines); the quoted words found once in
  the first block, group or file anchor, or in the paper where none is
  one;
- `edge <owner> → <kind> <target> …`: an edge the edges sheet lacks, written
  as that sheet writes it (the edges sheet's `change`), where the session may;
- for each finding the sheet asks to settle (lapsed, or carrying a
  proposal), a fixed line `⟦f1 finding⟧ <aspect> <severity>
  <anchors> "<words>" — <problem>` with its causes and its proposal under
  it, and one line of the session's: `stands <finding> ["<quoted
  words>"] — <why it still stands>`, the finding refiled at the state (its
  first worded anchor quoted anew where given), or `fixed <finding> — <what
  in the text settles it>`, the answer `fixed`, or, on a finding carrying a
  proposal, `reading <finding> ["<quoted words>"] — <the two readings>`,
  the finding refiled as a disagreement of reading, the person's to
  answer;
- `rule <subject> <ruling> [pages KEY:N, …] — <reason>`, or for `owed`
  `rule <subject> owed <severity> [pages KEY:N, …] — <reason> — <fix>`:
  on a `true` subject marked `⊢ unsupported`, whether the paper owes its
  argument, with the cited pages it rests on where it read some; an
  `owed` one beside a finding on its words that is settled (`fixed`,
  `dismissed`, or `fixed` on this sheet) names that finding;
- `keep <subject>`: in place of a rule line, on a subject whose ruling
  under an older question the sheet shows as a candidate, reinstating it
  under the current question, its kind, reason and pages carried;
- `⟦done⟧`: the end, for a session that judges no subject;
- for the relatedwork session (`Column.suggests`): `topic <anchor> "<quoted
  words>" — <topic> — work:KEY, …`, a topic of the field mapped and the
  works a reader expects there, each cited by the anchor's block or named
  by a `suggest` or `find` line; and `suggest <anchor…> work:KEY [pages
  KEY:N, …] "<quoted words>" — <why>`, a work offered to the author;
- for the contribution, fieldresults and relatedwork sessions
  (`Column.fetches`):
  `checked <anchor> "<quoted words>" — <searched> — <found>`, each
  contribution, each result of the field, or each place, it checked; their
  findings and suggestions may name `work:KEY` for a work of the
  bibliography, cited or not, or for one found outside it by its
  identifier (`arxiv:ID`, `doi:DOI`), fetched by their `fetch` tool
  (`review_tools`), and `pages KEY:N, …` after the severity, the pages
  read of a work they name.

Every other line, a changed fixed line included, is refused. A session's
column (`Column`) says which aspects it judges and finds, so each question
has one owner.

A session that answers by block (`Column.by_block`, the argument,
findings, contribution, fieldresults and relatedwork sessions) writes its lines under
each block's heading, and `checked` reads them as this sheet's lines
(`review_blocks`).

`checked` is the sheet's one check, the gate's and the runner's alike
(docs/architecture.md § 7). It needs only the spec (`spec`), the edited
sheet and the session's directory, so the host runs it as the session's
gate (`main`) at Stop, outside the session's sandbox, and a session by
block runs it itself as it writes, by its tool server's `check`: what it
needs of the store the brief carries in the spec (each laid page's read
status, each finding's settling, the state's edges sheet
and its edges gate's spec). It reads the sheet (`read`), runs each script
a `computed` judge line names (`run_problems`, cached, as the runner's
check runs it after), reads a fetched work's pages where its `fetch` tool
fetched them, and checks the edge lines as an agent's edges edit of the state
(`edge_problems`), so a value whose output side the run did not print
comes back with what it printed, and an edge line the edges refuse comes
back before the session ends. The runner's check (`axiomation.review`)
runs it, then builds the records.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import re
import shlex
import sys
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from axiomation import computations
from axiomation.cutter.model import GroupKind
from axiomation.evidence import READ, normalize, work_of
from axiomation.questions import (
    Stage,
    edges_file,
    gates,
    review_blocks,
)
from axiomation.questions import edge_sheet as es
from axiomation.questions.edge_sheet import norm
from axiomation.records import (
    READ_VERDICTS,
    RULING,
    AnswerRecord,
    Aspect,
    Depth,
    RulingKind,
    Status,
    Verdict,
)
from axiomation.settings import SettingsError

REVIEW_FILE = "review.md"
PAPER_FILE = "paper.md"
HINTS_FILE = "hints.md"
CONVENTIONS_FILE = "conventions.md"
SEPARATOR = "---"
#: The sheet's first line's start, before its session's name.
SHEET_TITLE = "# review: "
DONE = review_blocks.DONE_MARK
PAPER_ANCHOR = "paper"
WORK_PREFIX = "work:"
FILE_PREFIX = "file:"
#: Where a session's code files are laid in, by key.
CODE_DIRECTORY = "code"

#: The edge lines the gate last checked against the edges, and what it
#: found, beside the spec: checked again only when they change.
EDGES_CHECKED = "gate-edges-checked.json"
#: How the edge lines' problems open, the gate's and the runner's alike.
EDGE_LINES = "edge lines: "
GROUP_LINES = "group lines: "
#: How long the gate may spend running scripts in one check: under the
#: host's hook timeout (`runner.opencode.GATE_SECONDS`, 120), with room to
#: read the sheet. A run past it is left to the runner's check, uncached.
GATE_RUN_SECONDS = 90
#: How much of a run's stdout a problem shows the session.
OUTPUT_SHOWN = 1500

SUBJECT = re.compile(r"^⟦(s\d+) (\S+)⟧ (.*)$")
#: A finding the sheet asks to settle.
QUESTION = re.compile(r"^⟦(f\d+) finding⟧ (.*)$")
HOP = re.compile(r"^  ↳ ")
JUDGE = re.compile(
    r"^judge (?P<subject>\S+) (?P<verdict>\S+) (?P<depth>\S+)"
    r"(?: pages (?P<pages>[^—]+?))? — (?P<evidence>.*\S.*)$"
)
FIND = re.compile(
    r"^find (?P<aspect>\S+) (?P<anchors>\S+(?: \S+)*?) (?P<severity>[123]) "
    r'(?:pages (?P<pages>[^"]+?) )?"(?P<words>.+?)" — (?P<rest>.*\S.*)$'
)
CHECKED = re.compile(
    r'^checked (?P<anchor>\S+) "(?P<words>.+?)" — (?P<searched>.*?\S) — '
    r"(?P<found>.*\S.*)$"
)
SUGGEST = re.compile(
    r"^suggest (?P<anchors>\S+(?: \S+)*?) "
    r'(?:pages (?P<pages>[^"]+?) )?"(?P<words>.+?)" — (?P<why>.*\S.*)$'
)
TOPIC = re.compile(
    r'^topic (?P<anchor>\S+) "(?P<words>.+?)" — (?P<topic>.*?\S) — '
    r"(?P<works>work:\S+(?:,\s*work:\S+)*)\s*$"
)
#: A work found outside the bibliography, by its identifier.
FOUND_KEY = re.compile(r"^(?:arxiv:\S+|doi:10\.\S+/\S+)$")
#: A found work with no identifier (an old article, a book), named by a
#: link to a public page of it (the work, its abstract, its record): a
#: pointer, never read, so never a page's.
LINKED_KEY = re.compile(r"^https?://\S+$")
#: The spec's key for where a result of the field may be used bare (the
#: fieldresults session): `[handle, words, why]` each, a claim standing
#: `unsupported`, held against the sheet once it is done
#: (`_unread_pointers`).
POINTERS = "pointers"
#: The spec's mark of a session that reads a part of the text for
#: findings: a part may hold nothing to check, so no `checked` line is
#: owed.
PART = "part"
_WORK_ANCHOR = re.compile(r"(?<!\S)work:(\S+)")
#: An edge line: a uses edge the edges lack, a justifies line or a self
#: mark corrected, or a restated claim's about line.
EDGE = re.compile(r"^edge (?P<owner>\S+) (?P<line>→ (?:uses|justifies|self|about) .+)$")
#: A run of blocks the paper argues as one, which no group holds: given
#: after the run as the agent's groups edit (`docs/model.md` § 2).
GROUP = re.compile(
    r"^group (?P<kind>\S+) (?P<first>\S+) (?P<last>\S+) — (?P<why>.*\S.*)$"
)
#: A finding settled: found standing, fixed in the text, or, against a
#: proposal, a disagreement of reading.
SETTLE = re.compile(
    r'^(?P<settle>stands|fixed|reading) (?P<question>\S+)(?: "(?P<words>.+?)")? — '
    r"(?P<why>.*\S.*)$"
)
#: A ruling under an older question kept: its kind and reason reinstated.
KEEP = re.compile(r"^keep (?P<subject>\S+)\s*$")
#: Words a reason quotes, in straight or curly quotes.
QUOTED = re.compile(r'"([^"]+)"|“([^”]+)”')
#: A `computed` subject's judge line: its run, then the values it matched.
JUDGE_RUN = re.compile(
    r"^judge (?P<subject>\S+) (?P<verdict>\S+) (?P<depth>\S+)"
    r"(?: run (?P<run>[^—]+?))? — "
    r'(?:(?P<values>".+?" ↔ ".+?"(?:; ".+?" ↔ ".+?")*) — )?'
    r'(?:code (?P<code>".+?"(?:; ".+?")*) — )?'
    r"(?P<evidence>.*\S.*)$"
)
PAIR = re.compile(r'^"(?P<text>.+?)" ↔ "(?P<output>.+)"$')
#: A ruling: whether the paper owes an argument for a claim it argues
#: nothing for.
RULE = re.compile(
    r"^rule (?P<subject>\S+) (?P<ruling>\S+)(?: (?!pages )(?P<severity>\S+))?"
    r"(?: pages (?P<pages>[^—]+?))? — (?P<reason>.*\S.*)$"
)
#: The rulings a review's rule line may give: `left-unargued` is the
#: author's alone (`docs/model.md` § 8).
AGENT_RULINGS = tuple(k for k in RulingKind if k is not RulingKind.LEFT_UNARGUED)
#: The hop line that asks a `true` subject's rule line.
RULE_ASKED = (
    "  ↳ ⊢ unsupported: a search of the paper found no argument for it; one rule "
    "line, or the justifies, self or about line the search missed"
)
#: The hop line on a `true` subject whose `restated` claim restates no
#: claim (`docs/model.md` § 4).
RESTATES_NOTHING = (
    "  ↳ ⊢ restated: it restates no claim (no about line of its block names it, "
    "or each points at no claim); an about line onto the claim it restates, "
    "where the paper states it"
)
PART_MARKER = review_blocks.PART_MARKER

JUDGE_FORM = "judge <subject> <verdict> <depth> [pages KEY:N, …] — <evidence>"
RULE_FORM = "rule <subject> <ruling> [pages KEY:N, …] — <reason>"
OWED_FORM = "rule <subject> owed <severity> [pages KEY:N, …] — <reason> — <fix>"
FIND_FORM = 'find <aspect> <anchor…> <severity> "<quoted words>" — <problem> [— <fix>]'
EDGE_FORM = 'edge <owner> → uses <target> · "<the owner\'s words>" — <why>'
GROUP_FORM = "group <kind> <first block> <last block> — <why>"
JUSTIFIES_FORM = (
    "edge <J> → justifies <T> [<claim>] [sketch|analogy] · \"<J's words that say "
    'which claim>" — <why>'
)
ABOUT_FORM = (
    "edge <block> → about [<claim>] <target> [<its claim>] · \"<the block's words "
    'that restate it>" — <why>'
)
SELF_FORM = (
    'edge <block> → self [<claim>, …] [sketch|analogy] · "<its reason, as its '
    'mark quotes it>" — <why>'
)
CHECKED_FORM = (
    'checked <anchor> "<quoted words>" — <what you searched> — <what you found>'
)
SUGGEST_FORM = (
    'suggest <anchor…> work:KEY [pages KEY:N, …] "<quoted words>" — <why a '
    "reader of the field might expect it here>"
)
TOPIC_FORM = 'topic <anchor> "<quoted words>" — <the topic> — work:KEY, work:KEY, …'

STANDS_FORM = 'stands <finding> ["<quoted words>"] — <why it still stands>'
FIXED_FORM = "fixed <finding> — <what in the text settles it>"
READING_FORM = (
    'reading <finding> ["<quoted words>"] — <the two readings, and why no case '
    "settles it>"
)
KEEP_FORM = "keep <subject>"
FOUND_FIND_FORM = (
    'find <aspect> <anchor…> work:KEY <severity> [pages KEY:N, …] "<quoted words>" '
    "— <problem> [— <fix>]"
)
JUDGE_RUN_FORM = (
    "judge <subject> <verdict> <depth> [run <file> [args…]] — "
    '["<the text\'s words>" ↔ "<the output\'s>"; …] — '
    '[code "<the script\'s line>"; … —] <evidence>'
)

#: Depths a reader may give: `formal` needs a proof checker, which no
#: session has.
DEPTHS = (Depth.INFORMAL, Depth.COMPUTATIONAL)


class Settle(StrEnum):
    """How a session settles a finding: the problem still stands,
    refiled; the text no longer has it, answered `fixed`; or, against a
    proposal, the review and the proposal read the text differently and
    no case settles it: refiled as a disagreement of reading, the person's
    to answer. No session dismisses another's finding."""

    STANDS = "stands"
    FIXED = "fixed"
    READING = "reading"


class Session(StrEnum):
    """A review session, by the questions it owns."""

    ARGUMENT = "argument"
    CITATIONCHECK = "citationcheck"
    METHODS = "methods"
    FINDINGS = "findings"
    CONTRIBUTION = "contribution"
    FIELDRESULTS = "fieldresults"
    RELATEDWORK = "relatedwork"


#: A session's earlier names, each read as the session's own wherever a
#: kept store or a profile names it: its answers' kinds, its questions,
#: its profile tables, its runs' costs.
EARLIER_NAMES: dict[str, Session] = {"citation": Session.CITATIONCHECK}


def session_named(name: str) -> Session:
    """The session `name` names, an earlier name (`EARLIER_NAMES`) read as
    the session's own; refused (`ValueError`) where it names none."""
    return EARLIER_NAMES.get(name) or Session(name)


def current_kind(kind: str) -> str:
    """An answer's kind or a template's name, an earlier session name in
    it read as the session's own: `review-citation-reader` is
    `review-citationcheck-reader`."""
    for old, session in EARLIER_NAMES.items():
        own = f"review-{old}"
        if kind == own or kind.startswith((f"{own}-", f"{own}+")):
            return f"review-{session.value}{kind[len(own) :]}"
    return kind


def session_of(kind: str) -> Session | None:
    """The review session an answer of `kind` belongs to: its own kind's
    (`review-citationcheck`), or a step's of its queue
    (`review-citationcheck-reader`), an earlier name read as its own; None
    for no review's."""
    kind = current_kind(kind)
    for session in Session:
        own = f"review-{session.value}"
        if kind == own or kind.startswith(f"{own}-"):
            return session
    return None


class SessionGroup(StrEnum):
    """Sessions asked as one, in order (`review --session field`)."""

    #: the paper against its field: whether its contributions are known,
    #: the field's results it uses bare, then the work beside it
    FIELD = "field"
    #: every session the profile seats, in the profile's order
    ALL = "all"


#: Each fixed group's sessions, in the order they are asked.
GROUPS = {
    SessionGroup.FIELD: (
        Session.CONTRIBUTION,
        Session.FIELDRESULTS,
        Session.RELATEDWORK,
    )
}


def sessions_named(names: Sequence[str], seated: Sequence[Session]) -> list[Session]:
    """The sessions `names` ask, a group's in its order, each once; `all`
    asks the `seated` ones (a caller skips another group's unseated
    members: `named_alone`)."""
    found: list[Session] = []
    for name in names:
        group = next((g for g in SessionGroup if g.value == name), None)
        if group is SessionGroup.ALL:
            found += list(seated)
        elif group is not None:
            found += list(GROUPS[group])
        else:
            found.append(session_named(name))
    return list(dict.fromkeys(found))


def named_alone(names: Sequence[str]) -> list[Session]:
    """The sessions `names` name one by one, not as a group's member: the
    ones a profile must seat, where a group asks only its seated members."""
    groups = {g.value for g in SessionGroup}
    return list(dict.fromkeys(session_named(n) for n in names if n not in groups))


@dataclass(frozen=True, slots=True)
class Column:
    """What a session may write: the aspects it judges (each subject of one
    needs a judge line) and finds, and whether it writes edge lines."""

    judged: tuple[Aspect, ...]
    found: tuple[Aspect, ...]
    edges: bool = False
    #: whether it fetches works found outside the bibliography and lists
    #: what it checked (the contribution, fieldresults and relatedwork sessions)
    fetches: bool = False
    #: whether it offers works as suggestions and maps the field by topic
    #: (the relatedwork session)
    suggests: bool = False
    #: whether it answers by block (`review_blocks`), its lines read as
    #: this sheet's (every session but the citation and methods queues)
    by_block: bool = False
    #: whether its session computes for itself in its sandbox's Bash (the
    #: argument and findings sessions by block); the host's, never its
    #: check's, so its data leaves it out
    computes: bool = False

    @property
    def judges(self) -> bool:
        return bool(self.judged)

    @property
    def reads_code(self) -> bool:
        """Whether it judges code files (`computed`): its judge
        lines name runs, its findings may sit on a script's lines."""
        return Aspect.COMPUTED in self.judged

    @property
    def judge_form(self) -> str:
        return JUDGE_RUN_FORM if self.reads_code else JUDGE_FORM

    def data(self) -> dict[str, Any]:
        return (
            {
                "judged": [a.value for a in self.judged],
                "found": [a.value for a in self.found],
                "edges": self.edges,
                "fetches": self.fetches,
            }
            | ({"suggests": True} if self.suggests else {})
            | ({"by_block": True} if self.by_block else {})
        )

    @classmethod
    def from_data(cls, data: Mapping[str, Any]) -> Column:
        return cls(
            tuple(Aspect(a) for a in data["judged"]),
            tuple(Aspect(a) for a in data["found"]),
            bool(data["edges"]),
            bool(data.get("fetches", False)),
            bool(data.get("suggests", False)),
            bool(data.get("by_block", False)),
        )


#: Each session's column.
COLUMNS = {
    Session.ARGUMENT: Column(
        judged=(Aspect.VALID, Aspect.TRUE),
        found=(Aspect.VALID, Aspect.TRUE, Aspect.WELL_POSED, Aspect.ACCURATE),
        edges=True,
        by_block=True,
        computes=True,
    ),
    # whether a cited work says what the citing sentence says it says
    Session.CITATIONCHECK: Column(
        judged=(Aspect.CITED,), found=(Aspect.CITED, Aspect.ACCURATE)
    ),
    # whether a script computes what its block says it does
    Session.METHODS: Column(judged=(Aspect.COMPUTED,), found=(Aspect.COMPUTED,)),
    # how the paper reads, whether it matches its field, what it is worth,
    # and whether a result it reports as computed shows its script (a
    # `computed` finding on the block): findings alone, never a judgment;
    # by block, on the paper plain
    Session.FINDINGS: Column(
        judged=(),
        found=(
            Aspect.PRESENTATION,
            Aspect.WELL_POSED,
            Aspect.ACCURATE,
            Aspect.TRUE,
            Aspect.SIGNIFICANCE,
            Aspect.COMPUTED,
        ),
        by_block=True,
        computes=True,
    ),
    # whether each contribution is known, by works found on the web and
    # fetched through its gate, and by the cited works, read for prior art;
    # it, fieldresults and relatedwork alone have the network
    Session.CONTRIBUTION: Column(
        judged=(), found=(Aspect.NOVELTY,), fetches=True, by_block=True
    ),
    # whether each result of the field the paper uses carries its source;
    # on the cited works and works found on the web and fetched
    Session.FIELDRESULTS: Column(
        judged=(), found=(Aspect.LITERATURE,), fetches=True, by_block=True
    ),
    # whether the paper is placed in its literature: the parts that need
    # situating, the work a reader of the field expects beside them, offered
    # as suggestions; the paper placed nowhere, a finding
    Session.RELATEDWORK: Column(
        judged=(),
        found=(Aspect.LITERATURE,),
        fetches=True,
        suggests=True,
        by_block=True,
    ),
}

#: What a review's brief asks now, by what answers it (a `ruling`, or a
#: reading of an aspect, by its value), where past version 1: set by hand
#: where a change to a brief alters what such an answer answers, never for
#: its wording. An agent's answer under an older version is asked again
#: (docs/model.md § 8).
QUESTION_VERSIONS: Mapping[str, int] = {
    # what a claim owes is asked by its place: a statement or a step its
    # argument, a restatement its pointer, a gloss nothing rests on nothing;
    # 3: asked only of a claim the support search read unargued, its own
    # step and its restatement the search's, a line the review writes a
    # correction; 4: an owed ruling carries its severity and its fix, its
    # reason names what is not argued, and one beside a settled finding on
    # its words says why it stands
    RULING: 4,
}


def _answered(session: Session) -> set[str]:
    """What `session`'s answers answer: each aspect it judges, and a ruling
    where it judges `true`, whose rule line rides on a `true` subject."""
    judged = COLUMNS[session].judged
    return {a.value for a in judged} | ({RULING} if Aspect.TRUE in judged else set())


def asks(session: Session) -> tuple[tuple[str, int], ...]:
    """The versions past the first of what `session`'s brief asks."""
    return tuple(
        sorted(
            (w, v)
            for w, v in QUESTION_VERSIONS.items()
            if w in _answered(session) and v > 1
        )
    )


def asked_version(answer: AnswerRecord, what: str) -> int | None:
    """The version of the question `what` a review's answer answered, as
    its template records it (the first where it names none); None for an
    answer no review's brief asked, a person's or a filing by hand."""
    if answer.stage != Stage.REVIEW:
        return None
    return dict(answer.template.asks).get(what, 1)


def outdated(answer: AnswerRecord | None, what: str) -> bool:
    """Whether a review's answer answered an older version of the question
    `what` than its brief asks now."""
    version = None if answer is None else asked_version(answer, what)
    return version is not None and version < QUESTION_VERSIONS.get(what, 1)


#: The aspects whose finding names the work it rests on, after the block or
#: group it quotes.
WORK_ASPECTS = (Aspect.NOVELTY, Aspect.LITERATURE)

#: What a session that fetches lists in its `checked` lines.
CHECKED_WHAT = {
    Session.CONTRIBUTION: "each contribution you checked",
    Session.FIELDRESULTS: "each result of the field you checked",
    Session.RELATEDWORK: "each part of the paper you judged needs no situating",
}


@dataclass(frozen=True, slots=True)
class SheetSubject:
    """A subject as the sheet shows it: its id (`s3`), aspect, its line's
    text after the marker, the one-hop lines under it, the handles a
    `verified` `valid` must name (J's uses targets), the works its blocks
    cite that were laid in with pages (a `valid` or `true` judgment may
    read any work of the bibliography, `_stray_pages`), and a `computed`
    one's code file and its block's words (`evidence.normalize`d, as a
    value's text side is found), and the works a `true` claim's own
    sentence cites or a `cited` site's work, never verified from recall."""

    id: str
    aspect: Aspect
    line: str
    hops: tuple[str, ...] = ()
    names: tuple[str, ...] = ()
    works: tuple[str, ...] = ()
    file: str | None = None
    words: str = ""
    cites: tuple[str, ...] = ()
    #: a `valid` subject's J, T and claim handle, as paper.md shows them, so
    #: the gate tells which subjects a justifies edge line takes out of J's
    #: cover
    edge: tuple[str, ...] = ()
    #: a `valid` subject's T's claim handles, all of them; a `rules`
    #: subject's block's
    claims: tuple[str, ...] = ()
    #: a `true` subject on a claim standing unsupported that no person's
    #: ruling speaks for: it takes a rule line too
    rules: bool = False
    #: a `rules` subject's block and claim handle, as paper.md shows them,
    #: so the gate tells which subject a self line stands `self`
    standing: tuple[str, ...] = ()
    #: a `cited` site whose locator names a page: a page number is one
    #: edition's, so a `verified` names a page of the cited edition itself
    page_locator: bool = False
    #: a `rules` subject's ruling under an older question, asked again:
    #: shown as a candidate to test, which `keep` reinstates
    candidate: Candidate | None = None
    #: a `rules` subject's claim: the handles of what relies on it in one
    #: hop, for its weight
    relied: tuple[str, ...] = ()
    #: a `rules` subject's claim: the settled findings on its words, which
    #: an `owed` rule line names
    answered: tuple[Answered, ...] = ()
    #: a `rules` subject's claim: the findings this sheet asks to settle
    #: that sit on its words (`f3`), which an `owed` rule line beside a
    #: `fixed` one names
    settling: tuple[str, ...] = ()
    #: a `rules` subject's person's proposed ruling, which its rule line
    #: settles
    proposal: ProposedRule | None = None

    def notes(self) -> list[str]:
        """What more it asks, as lines under its own: a rule line, a
        candidate, a proposal, its settled findings and those to settle,
        what relies on it, a restated claim that restates none; not the
        edges its one hop shows, which `paper.md` holds."""
        return self.lines()[1 : len(self.lines()) - len(self.hops)] + [
            h for h in self.hops if h == RESTATES_NOTHING
        ]

    def lines(self) -> list[str]:
        asked = [RULE_ASKED] if self.rules else []
        if self.candidate is not None:
            asked.append(self.candidate.line())
        if self.proposal is not None:
            asked.append(self.proposal.line())
        asked += [a.line() for a in self.answered]
        asked += [
            f"  ↳ ⟦{q} finding⟧ below sits on these words: an owed rule line beside "
            f"`fixed {q}` names {q} and says why the argument is still owed"
            for q in self.settling
        ]
        if self.relied:
            asked.append("  ↳ relied on by " + ", ".join(self.relied))
        return [f"⟦{self.id} {self.aspect.value}⟧ {self.line}", *asked, *self.hops]


@dataclass(frozen=True, slots=True)
class Answered:
    """A settled finding on a `rules` subject's words, as the sheet shows
    it: its id, its answer, who gave it and their note."""

    finding: str
    status: str
    by: str
    note: str

    @property
    def name(self) -> str:
        return self.finding[:12]

    def line(self) -> str:
        return (
            f"  ↳ finding {self.name} on these words, answered {self.status} by "
            f'{self.by}: "{self.note}"; an owed rule line names {self.name} and says '
            "why the argument is still owed despite that answer"
        )


@dataclass(frozen=True, slots=True)
class ProposedRule:
    """A person's proposed ruling, as the sheet shows it to settle: who,
    its kind (an `owed` one's severity and fix), reason and the pages it
    rests on (`KEY:N`)."""

    by: str
    kind: RulingKind
    reason: str
    severity: int | None = None
    fix: str | None = None
    pages: tuple[str, ...] = ()

    def line(self) -> str:
        weight = f" {self.severity}" if self.severity is not None else ""
        fix = f' — fix: "{self.fix}"' if self.fix else ""
        pages = (
            f" — on pages {', '.join(self.pages)}, which your `cited_*` tools read"
            if self.pages
            else ""
        )
        return (
            f"  ↳ proposed by {self.by}: {self.kind.value}{weight} — "
            f'"{self.reason}"{fix}{pages}; weigh it against the text, never copy '
            "it: your rule line settles it, and where you rule otherwise your "
            "reason answers theirs"
        )


@dataclass(frozen=True, slots=True)
class Candidate:
    """A ruling under an older question, as the sheet shows it to test:
    its id, kind, reason and the pages it rests on (`KEY:N`)."""

    ruling: str
    kind: RulingKind
    reason: str
    pages: tuple[str, ...] = ()

    def line(self) -> str:
        pages = f", on pages {', '.join(self.pages)}" if self.pages else ""
        if self.kind is RulingKind.OWED:
            # an owed one is ruled again, with the severity and fix the
            # current question asks, which `keep` cannot give
            return (
                "  ↳ ruled owed under an older question: a reading to weigh, "
                "never one to keep; write your own rule line, with its severity "
                f'and fix: "{self.reason}"{pages}'
            )
        return (
            f"  ↳ ruled {self.kind.value} under an older question, a candidate to "
            f'test, never an answer to copy (`keep` where it holds verbatim): "'
            f'{self.reason}"{pages}'
        )

    def data(self) -> dict[str, Any]:
        return {"ruling": self.ruling, "kind": self.kind.value, "reason": self.reason}


@dataclass(frozen=True, slots=True)
class SheetQuestion:
    """A finding to settle as the sheet asks it: its id (`f1`), the finding's
    id, its line's text after the marker, its causes as lines under it, and
    its anchors as a find line names them (handles, `paper`, `work:KEY`,
    `file:KEY`), the first worded one first among them."""

    id: str
    finding: str
    line: str
    hops: tuple[str, ...] = ()
    anchors: tuple[str, ...] = ()
    #: what its settling refuses, as the runner's check would at the state
    #: (`Settling`)
    settling: Settling | None = None
    #: it carries a proposal, which a `reading` line may answer
    proposal: bool = False

    def lines(self) -> list[str]:
        return [f"⟦{self.id} finding⟧ {self.line}", *self.hops]


@dataclass(frozen=True, slots=True)
class Requoted:
    """The first worded anchor of a finding to settle, as a `stands` line's
    words quote it anew: its kind (span, part, file), the handle of the
    block it sits on or the file's key, a span's group's members (the
    member that holds the words takes it), a part's claims, a file's text
    (normalised)."""

    kind: str
    name: str
    members: tuple[str, ...] = ()
    claims: tuple[str, ...] = ()
    text: str | None = None

    def data(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "name": self.name,
            "members": list(self.members),
            "claims": list(self.claims),
            "text": self.text,
        }


@dataclass(frozen=True, slots=True)
class Settling:
    """What settling a finding refuses at the state the sheet reads, as the
    runner's check would (`judgments.reaffirm_refusal`,
    `judgments.reanchored`): a `fixed` line (`fixed`), a `stands` line
    whatever its words (`refused`), one quoting nothing anew (`stands`),
    one quoting words anew, beside its words (`others`, its other anchors;
    `requoted`, the anchor its words quote, none where it has no worded
    anchor). The finding's id and the state's name the anchor's problem."""

    finding: str
    state: str
    fixed: str | None = None
    refused: str | None = None
    stands: str | None = None
    others: str | None = None
    requoted: Requoted | None = None

    def data(self) -> dict[str, Any]:
        return {
            "finding": self.finding,
            "state": self.state,
            "fixed": self.fixed,
            "refused": self.refused,
            "stands": self.stands,
            "others": self.others,
            "requoted": None if self.requoted is None else self.requoted.data(),
        }


@dataclass(frozen=True, slots=True)
class JudgeLine:
    number: int
    subject: str
    verdict: Verdict
    depth: Depth
    pages: tuple[tuple[str, int], ...]
    evidence: str
    #: a `computed` line's run, its file then its arguments, and its values
    run: tuple[str, ...] | None = None
    values: tuple[tuple[str, str], ...] = ()
    #: the script's lines it quotes as computing the values
    code: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RuleLine:
    """A ruling on a `true` subject's claim: owed or not, and why, an
    `owed` one with its severity and fix, and the cited pages it rests on
    where it read some; or a candidate kept (`keeps`, its id), its kind,
    reason and pages carried."""

    number: int
    subject: str
    ruling: RulingKind
    reason: str
    keeps: str | None = None
    severity: int | None = None
    fix: str | None = None
    pages: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True, slots=True)
class FindLine:
    number: int
    aspect: Aspect
    anchors: tuple[str, ...]
    severity: int
    words: str
    problem: str
    fix: str | None
    #: the pages it read of a work it names, fetched or of the bibliography
    #: (the sessions that fetch)
    pages: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True, slots=True)
class CheckedLine:
    """A contribution the session checked, what it searched and found."""

    number: int
    anchor: str
    words: str
    searched: str
    found: str

    @property
    def anchors(self) -> tuple[str, ...]:
        """Its one anchor, as a finding line's anchors are read."""
        return (self.anchor,)


@dataclass(frozen=True, slots=True)
class SuggestLine:
    """A work offered at a place: its anchors (the block or group first),
    the quoted words, why a reader might expect it, the pages read."""

    number: int
    anchors: tuple[str, ...]
    words: str
    why: str
    pages: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True, slots=True)
class TopicLine:
    """A topic of the field mapped: where the paper treats it, the topic,
    and the works a reader expects there."""

    number: int
    anchor: str
    words: str
    topic: str
    works: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SettleLine:
    """A finding settled: the question, how, the words its first
    worded anchor is quoted by anew (`stands` only), and why."""

    number: int
    question: str
    settle: Settle
    words: str | None
    why: str


@dataclass(frozen=True, slots=True)
class EdgeLine:
    number: int
    owner: str
    line: str


@dataclass(frozen=True, slots=True)
class GroupLine:
    """A group the sheet makes: its kind and its first and last blocks by
    handle, the run between them in the paper's order."""

    number: int
    kind: GroupKind
    first: str
    last: str
    why: str


@dataclass(frozen=True, slots=True)
class Read:
    """An edited sheet's lines, and what is wrong with it."""

    judges: tuple[JudgeLine, ...] = ()
    finds: tuple[FindLine, ...] = ()
    edges: tuple[EdgeLine, ...] = ()
    done: bool = False
    problems: tuple[str, ...] = field(default=())
    checked: tuple[CheckedLine, ...] = ()
    #: the found works it asked fetched, by key
    fetches: tuple[str, ...] = ()
    settles: tuple[SettleLine, ...] = ()
    rules: tuple[RuleLine, ...] = ()
    suggests: tuple[SuggestLine, ...] = ()
    topics: tuple[TopicLine, ...] = ()
    groups: tuple[GroupLine, ...] = ()


# --- the sheet, rendered ----------------------------------------------------------


def header(
    session: Session, column: Column, asks: bool = False, rules: bool = False
) -> list[str]:
    """The sheet's header: what this session writes, and in which form;
    with `asks`, how it settles the findings the sheet asks to settle;
    with `rules`, how it rules on the claims the paper argues nothing
    for."""
    lines = [f"{SHEET_TITLE}{session.value}", ""]
    if asks:
        lines += [
            "Each finding below (⟦f…⟧) lapsed, or an agent or a person proposes it "
            "answered (`↳ proposed`), and takes one line: it still stands in the "
            "text as it reads now, or the text no longer has the problem:",
            f"  {STANDS_FORM}",
            f"  {FIXED_FORM}",
            "  quoted words where the finding's own no longer stand once in its "
            "first block",
            "  a proposal's note is reasoning to weigh against the text, never "
            "evidence: `fixed` only where your own reading of the text finds the "
            "problem gone",
            "  against a proposal, `stands` answers its note's argument with a case "
            "or a quote; where you have neither, the disagreement is one of reading, "
            "the person's to answer:",
            f"  {READING_FORM}",
        ]
    if column.judges:
        judged = ", ".join(a.value for a in column.judged)
        lines += [
            f"Each subject below ({judged}) takes one judge line:",
            f"  {column.judge_form}",
            "  verdict: " + ", ".join(v.value for v in Verdict),
            "  depth: " + ", ".join(d.value for d in DEPTHS),
        ]
    if rules:
        lines += [
            "Each subject marked `⊢ unsupported` also takes one rule line: does "
            "the paper owe it an argument?",
            f"  {RULE_FORM}",
            "  ruling: " + ", ".join(k.value for k in AGENT_RULINGS),
            f"  an owed one with its severity (1 to 3) and its fix: {OWED_FORM}; "
            "the fix last, after the last ` — `, so the reason holds no ` — `",
            "  or, where its ruling under an older question (`↳ … a candidate to "
            "test`) holds verbatim of the text now:",
            f"  {KEEP_FORM}",
        ]
        if column.edges:
            lines += [
                "  or, where its block's own words give the step that yields it, a "
                "self line naming it and no rule line: it stands self",
                "  or, where it restates a claim the paper states elsewhere, an "
                "about line onto that claim and no rule line: it stands restated",
            ]
    found = ", ".join(a.value for a in column.found)
    anchors = f"block or group handles, `{PAPER_ANCHOR}`, `{WORK_PREFIX}KEY`"
    if column.reads_code:
        anchors += f", `{FILE_PREFIX}KEY` first (a script's lines)"
    if column.fetches:
        lines += [
            CHECKED_WHAT[session].capitalize() + ", anywhere below the line:",
            f"  {CHECKED_FORM}",
        ]
    if column.suggests:
        lines += [
            "Each topic of the field you mapped, and the works a reader expects "
            "there; each not cited by its block is offered or found below:",
            f"  {TOPIC_FORM}",
            "A work offered to the author at a place, a suggestion, not a problem:",
            f"  {SUGGEST_FORM}",
        ]
    lines += [
        f"Findings ({found}), anywhere below the line:",
        f"  {FOUND_FIND_FORM if column.fetches else FIND_FORM}",
        f"  anchors: {anchors}; severity 1, 2 or 3",
    ]
    if column.edges:
        lines += ["An edge the paper's edges lack:", f"  {EDGE_FORM}"]
        lines += [
            "A run of blocks the paper argues as one that no group holds:",
            f"  {GROUP_FORM}",
            "  kind: " + ", ".join(k.value for k in GroupKind),
        ]
    if column.edges and Aspect.VALID in column.judged:
        lines += [
            "A justifies line corrected, replacing J's line onto T, or a self mark, "
            "replacing the block's mark with the same quote; [<claim>] names the "
            "claim by the handle its ⊢ line shows in paper.md:",
            f"  {JUSTIFIES_FORM}",
            f"  {SELF_FORM}",
        ]
    if column.edges and Aspect.TRUE in column.judged:
        lines += [
            "A restated claim's about line, onto the claim it restates, replacing "
            "its block's line onto that target:",
            f"  {ABOUT_FORM}",
        ]
    if not column.judges:
        lines += [f"End with `{DONE}` on its own line."]
    return [*lines, "", SEPARATOR, ""]


def render(
    session: Session,
    subjects: Sequence[SheetSubject],
    questions: Sequence[SheetQuestion] = (),
) -> str:
    """The sheet as the session starts it: its findings to settle first, then
    its subjects."""
    column = COLUMNS[session]
    lines = header(session, column, bool(questions), any(s.rules for s in subjects))
    for q in questions:
        lines += [*q.lines(), ""]
    for s in subjects:
        lines += [*s.lines(), ""]
    return "\n".join(lines)


def spec(
    session: Session,
    reference: str,
    subjects: Sequence[SheetSubject],
    texts: Mapping[str, str],
    groups: Mapping[str, Sequence[str]],
    works: Sequence[str],
    code: Mapping[str, tuple[str, str]] | None = None,
    runs: Mapping[str, str] | None = None,
    fetch: Mapping[str, str] | None = None,
    questions: Sequence[SheetQuestion] = (),
    lapsed: bool = False,
    laid: Sequence[str] = (),
    cites_at: Mapping[str, Sequence[str]] | None = None,
    pages: Mapping[str, Mapping[int, str]] | None = None,
    edges: Mapping[str, Any] | None = None,
    entries: Sequence[str] = (),
    reads: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """What the gate needs besides the edited sheet: the sheet as shown,
    the column, each subject's names and works (a `computed` one's file and
    words), each block's and group's text by handle (normalised), each
    group's members, the cited works; for a session that reads code, each
    code file's text and blob at the state by key (`code`), and where its
    runs are made (`runs`: the paper's directory and the run cache); each
    `valid` subject's T's claim handles (`handles`, by T's handle); each
    finding asked to settle, its finding's id and anchors (`questions`);
    whether the session settles what lapsed alone (`lapsed`); for a session
    that fetches, the cited works laid in with pages, whose pages its
    findings may name beside a fetched work's (`laid`); for a session that
    maps the field, the works each block cites, by handle (`cites_at`);
    each page laid in of a work a line may name, its read status by index
    (`pages`); for a session that writes edge lines, the state's edges
    sheet as an agent edits it and its edges gate's spec (`edges`:
    `shown`, `spec`); for a session that reads the bibliography, its keys,
    any of which a judge, rule, find or suggest line may name with its
    pages (`entries`), and where a page of one not laid in is read
    (`reads`: the paper's directory and store, `unlaid_page_problems`)."""
    handles = {s.edge[1]: s.claims for s in subjects if s.edge} | {
        s.standing[0]: s.claims for s in subjects if s.standing
    }
    return (
        {
            "session": session.value,
            "column": COLUMNS[session].data(),
            "reference": reference,
            "subjects": {
                s.id: {
                    "aspect": s.aspect.value,
                    "names": list(s.names),
                    "works": list(s.works),
                    "cites": list(s.cites),
                }
                | ({"file": s.file, "words": s.words} if s.file is not None else {})
                | ({"edge": list(s.edge)} if s.edge else {})
                | ({"rules": True} if s.rules else {})
                | ({"standing": list(s.standing)} if s.standing else {})
                | ({"page": True} if s.page_locator else {})
                | ({"candidate": s.candidate.data()} if s.candidate is not None else {})
                | ({"answered": [a.finding for a in s.answered]} if s.answered else {})
                | ({"settling": list(s.settling)} if s.settling else {})
                | ({"proposal": True} if s.proposal is not None else {})
                for s in subjects
            },
            "texts": {h: norm(t) for h, t in texts.items()},
            "groups": {g: list(m) for g, m in groups.items()},
            "works": list(works),
            "code": {
                k: {"text": norm(t), "blob": b} for k, (t, b) in (code or {}).items()
            },
            "runs": dict(runs) if runs is not None else None,
            "fetch": dict(fetch) if fetch is not None else None,
            "handles": {t: list(h) for t, h in handles.items()},
            "questions": {
                q.id: {"finding": q.finding, "anchors": list(q.anchors)}
                | ({"settling": q.settling.data()} if q.settling is not None else {})
                | ({"proposal": True} if q.proposal else {})
                for q in questions
            },
            "lapsed": lapsed,
        }
        | (
            {"pages": {k: {str(i): v for i, v in p.items()} for k, p in pages.items()}}
            if pages is not None
            else {}
        )
        | ({"edges": dict(edges)} if edges is not None else {})
        | ({"entries": list(entries)} if entries else {})
        | ({"reads": dict(reads)} if reads is not None else {})
        | ({"laid": list(laid)} if laid else {})
        | (
            {"cites_at": {h: list(w) for h, w in cites_at.items()}}
            if cites_at is not None
            else {}
        )
    )


def _recalled(
    subject: Mapping[str, Any],
    verdict: Verdict,
    pages: Sequence[tuple[str, int]],
) -> str:
    """Why a judge line verifies a cited work from recall, or empty: a
    `verified` `true` whose claim's own sentence cites works, naming no
    page of any of them, or a `cited` verdict other than `inconclusive`
    naming no page of its work (what a third-party work says is verified
    on its pages or not at all)."""
    cites = list(subject.get("cites") or ())
    if subject["aspect"] == Aspect.CITED.value:
        (work,) = cites
        if (
            verdict is Verdict.VERIFIED
            and subject.get("page")
            and pages
            and not any(key == work for key, _ in pages)
        ):
            return (
                f"a page locator is checked on the cited edition's pages: name one "
                f"of {work}'s (`pages {work}:N`), or judge it inconclusive and say "
                "the locator is unchecked"
            )
        # a page of any file of the work: its cited edition or another
        if verdict is Verdict.INCONCLUSIVE or any(
            work_of(key) == work for key, _ in pages
        ):
            return ""
        if work in subject["works"]:
            return (
                f"a {verdict.value} rests on {work}'s pages: name the ones you "
                f"read (`pages {work}:N`), or judge it inconclusive"
            )
        return (
            f"{work} has no pages here, so what it says is unread: judge it "
            "inconclusive and say why"
        )
    if (
        subject["aspect"] != Aspect.TRUE.value
        or verdict is not Verdict.VERIFIED
        or not cites
        or any(work_of(key) in cites for key, _ in pages)
    ):
        return ""
    named = ", ".join(cites)
    here = [k for k in cites if k in subject["works"]]
    if here:
        return (
            f"the claim's sentence cites {named}: what a cited work says is "
            "never verified from recall; "
            f"{', '.join(here)}'s pages are read by your `cited_*` tools: name "
            "the ones you read (`pages KEY:N`), or judge it inconclusive"
        )
    return (
        f"the claim's sentence cites {named}: what a cited work says is never "
        f"verified from recall, and {named} has no pages here: judge it "
        "inconclusive, the mathematics you checked in the evidence"
    )


# --- the sheet, read --------------------------------------------------------------


#: A locator that names a page: `p.~12`, `pp. 3--5`, `page 7`.
_PAGE_LOCATOR = re.compile(r"^\s*(?:pp?|pages?)\b\.?[\s~]*\d", re.IGNORECASE)


def page_locator(locator: str | None) -> bool:
    """Whether a citation's locator names a page."""
    return bool(locator) and _PAGE_LOCATOR.match(locator or "") is not None


def unmarked(words: str) -> str:
    """A quote with the sheet's part markers dropped, normalised."""
    return norm(PART_MARKER.sub("", words))


def _fixed(lines: Sequence[str]) -> list[str]:
    return [
        line
        for line in lines
        if SUBJECT.match(line) or QUESTION.match(line) or HOP.match(line)
    ]


def _pages(text: str) -> tuple[tuple[str, int], ...] | None:
    found = []
    for ref in text.split(","):
        key, sep, index = ref.strip().rpartition(":")
        if not sep or not key or not index.isdigit() or int(index) < 1:
            return None
        found.append((key, int(index)))
    return tuple(found)


def spec_code(spec: Mapping[str, Any], key: str) -> str | None:
    """A code file's text at the state as the spec holds it (normalised)."""
    held = spec.get("code", {}).get(key)
    return None if held is None else held["text"]


def _anchor_text(spec: Mapping[str, Any], handle: str) -> str | None:
    if handle.startswith(FILE_PREFIX):
        return spec_code(spec, handle[len(FILE_PREFIX) :])
    texts = spec["texts"]
    if handle in spec["groups"]:
        return " ".join(texts.get(m, "") for m in spec["groups"][handle])
    return texts.get(handle)


def _words_problem(spec: Mapping[str, Any], anchors: Sequence[str], words: str) -> str:
    """Why the quoted words do not stand where the finding says, else ''."""
    wanted = unmarked(words)
    if not wanted:
        return "its quoted words are empty"
    # a block, a group or a file: the first of them holds the quoted words
    nodes = [a for a in anchors if a != PAPER_ANCHOR and not a.startswith(WORK_PREFIX)]
    if nodes:
        text = _anchor_text(spec, nodes[0]) or ""
        found = text.count(wanted)
        if found != 1:
            return (
                f'"{words}" is found {found} times in {nodes[0]}, not once: quote '
                "the anchor's own words, enough of them to be found once"
            )
        return ""
    if not any(wanted in t for t in spec["texts"].values()):
        return f'"{words}" is found nowhere in the paper'
    return ""


class _Refused(ValueError):
    """A line refused, with why."""


def _computed_line(
    subject: Mapping[str, Any],
    m: re.Match[str],
    verdict: Verdict,
    script: str | None,
) -> tuple[tuple[str, ...] | None, tuple[tuple[str, str], ...], tuple[str, ...]]:
    """A `computed` judge line's run (its file, then its arguments), its
    values, a text side's part markers dropped, and the script's lines it
    quotes; refused (`_Refused`) for what needs no run: the run of another
    file, an argument reaching outside the paper, a pair not in its form,
    values with no run, a `verified` naming no run, no value or no line of
    code, a value's text side not in the block, a quoted line not in the
    kept script (`script`, normalised)."""
    file = subject["file"]
    ran: tuple[str, ...] | None = None
    if m.group("run"):
        try:
            ran = tuple(shlex.split(m.group("run")))
        except ValueError as error:
            raise _Refused(f"its run's arguments do not read: {error}") from None
        if not ran or ran[0] != file:
            named = ran[0] if ran else "no file"
            raise _Refused(
                f"the run names {named}; this subject's file is {file}: "
                f"`run {file} [args…]`"
            )
        outside = [a for a in ran[1:] if leaves_the_paper(a)]
        if outside:
            raise _Refused(
                f"{', '.join(outside)}: the run is made outside your sandbox, so "
                "an argument never names a path outside the paper (absolute, "
                "`~` or `..`)"
            )
    if not m.group("values") and re.match(r'^".*↔', m.group("evidence")):
        raise _Refused(
            'the values do not read as pairs: `"<the text\'s words>" ↔ "<the '
            "output's>\"`, separated by `; `, each side quoted, then ` — ` and "
            "the evidence"
        )
    values: list[tuple[str, str]] = []
    for pair in re.split(r'(?<=")\s*;\s*(?=")', m.group("values") or ""):
        if not pair:
            continue
        found = PAIR.match(pair)
        if found is None:
            raise _Refused(
                f'a value is `"<the text\'s words>" ↔ "<the output\'s>"`: {pair!r}'
            )
        values.append((PART_MARKER.sub("", found.group("text")), found.group("output")))
    if values and ran is None:
        raise _Refused("a value is matched to a run's output: name the run")
    if verdict is Verdict.VERIFIED and ran is None:
        raise _Refused(f"a verified computed names the run it read: `run {file} …`")
    if verdict is Verdict.VERIFIED and not values:
        raise _Refused(
            "a verified computed names each value it matched, the text's words "
            "beside the output's, after the run's ` — `"
        )
    for text, _ in values:
        if not normalize(text) or normalize(text) not in subject["words"]:
            raise _Refused(
                f"\"{text}\" is not in the block's text: a value's text side is "
                "the block's own words, copied"
            )
    if not m.group("code") and m.group("evidence").startswith("code "):
        raise _Refused(
            'the code does not read: `code "<the script\'s line>"; "<…>"`, each '
            "line quoted, then ` — ` and the evidence"
        )
    code = tuple(
        norm(c[1:-1])
        for c in re.split(r'(?<=")\s*;\s*(?=")', m.group("code") or "")
        if c
    )
    if verdict is Verdict.VERIFIED and not code:
        raise _Refused(
            "a verified computed quotes the script's line that computes the "
            f'values, copied from {file}: `code "<line>"`, after the pairs\' ` — `'
        )
    for line in code:
        if not line or (script is not None and line not in script):
            raise _Refused(
                f'"{line}" is not a line of {file}: copy the code as the file '
                "has it, not its docstring's or your own words for it"
            )
    return ran, tuple(values), code


def leaves_the_paper(argument: str) -> bool:
    """Whether a run's argument (or the value after an option's `=`) is a
    path outside the paper: absolute, from a home, or climbing out."""
    for word in {argument, argument.partition("=")[2]} - {""}:
        if word.startswith(("/", "~", "\\")) or re.match(r"^[A-Za-z]:[\\/]", word):
            return True
        if ".." in re.split(r"[\\/]", word):
            return True
    return False


def _named(evidence: str, handle: str) -> bool:
    return (
        re.search(rf"(?<![\w:.-]){re.escape(handle)}(?![\w:-])", evidence) is not None
    )


def read(spec: Mapping[str, Any], edited: str) -> Read:
    """The edited sheet's lines and every problem with it: its fixed lines
    changed, a malformed line, a form or aspect outside its column, an
    unknown subject or anchor, quoted words not found once in their anchor,
    a page of a work the subject's blocks do not cite, a `verified` `valid`
    naming not every uses target of J, a second judge line on a subject, a
    subject with no judge line, a finding asked to settle with no
    `stands` or `fixed` line or with two, a missing done mark."""
    column = Column.from_data(spec["column"])
    reference: str = spec["reference"]
    head, sep, _ = reference.partition(f"\n{SEPARATOR}\n")
    problems: list[str] = []
    if not edited.startswith(head + sep):
        problems.append(
            "the header above the `---` line changed: leave it as it was shown"
        )
    lines = edited.split("\n")
    shown_fixed = _fixed(reference.split("\n"))
    if _fixed(lines) != shown_fixed:
        problems.append(
            "the subject lines (`⟦s…⟧`, `⟦f…⟧` and the `↳` lines under them) "
            "changed: leave each as it was shown, and write below it"
        )
    # the header's lines and the `---` line: the line after it is the first read
    start = len(head.split("\n")) + 1 if edited.startswith(head + sep) else 0
    subjects: Mapping[str, Any] = spec["subjects"]
    questions: Mapping[str, Any] = spec.get("questions") or {}
    texts: Mapping[str, str] = spec["texts"]
    settles: list[SettleLine] = []
    judges: list[JudgeLine] = []
    rules: list[RuleLine] = []
    finds: list[FindLine] = []
    edges: list[EdgeLine] = []
    grouped: list[GroupLine] = []
    blocks = [h for h in texts if h not in spec["groups"]]
    checked: list[CheckedLine] = []
    suggested: list[SuggestLine] = []
    topics: list[TopicLine] = []
    # the found works, fetched by the session's tool: each a line names by
    # its identifier
    fetched = list(
        dict.fromkeys(
            key
            for line in lines[start:]
            if column.fetches
            for named in _WORK_ANCHOR.findall(line)
            if FOUND_KEY.match(key := found_key(named))
        )
    )
    done = False
    for number, line in enumerate(lines[start:], start + 1):
        if (
            not line.strip()
            or SUBJECT.match(line)
            or QUESTION.match(line)
            or HOP.match(line)
        ):
            continue
        where = f"line {number}"
        if line.strip() == DONE:
            done = True
            continue
        if line.startswith(tuple(f"{s.value} " for s in Settle)):
            m = SETTLE.match(line)
            if m is None:
                problems.append(
                    f"{where}: a finding is settled by `{STANDS_FORM}`, "
                    f"`{FIXED_FORM}` or, against a proposal, `{READING_FORM}`: "
                    f"{line!r}"
                )
                continue
            qid, settle = m.group("question"), Settle(m.group("settle"))
            if qid not in questions:
                problems.append(f"{where}: no finding {qid} to settle on this sheet")
                continue
            if settle is Settle.READING and not questions[qid].get("proposal"):
                problems.append(
                    f"{where}: {qid} carries no proposal: a reading line answers "
                    "one; `stands` or `fixed`"
                )
                continue
            words = m.group("words")
            problem = _settle_problem(spec, questions[qid], settle, words)
            if problem:
                problems.append(f"{where}: {problem}")
                continue
            settles.append(
                SettleLine(number, qid, settle, words, m.group("why").strip())
            )
            continue
        if line.startswith("judge "):
            peek = line.split(" ", 2)[1:2]
            computed = (
                bool(peek)
                and subjects.get(peek[0], {}).get("aspect") == Aspect.COMPUTED.value
            )
            form = JUDGE_RUN_FORM if computed else JUDGE_FORM
            m = (JUDGE_RUN if computed else JUDGE).match(line)
            if m is None:
                problems.append(f"{where}: a judge line is `{form}`: {line!r}")
                continue
            if not column.judges:
                problems.append(
                    f"{where}: the {spec['session']} session files findings only, "
                    "no judge line"
                )
                continue
            sid = m.group("subject")
            if sid not in subjects:
                problems.append(f"{where}: no subject {sid} on this sheet")
                continue
            try:
                verdict, depth = Verdict(m.group("verdict")), Depth(m.group("depth"))
                if depth not in DEPTHS:
                    raise ValueError(depth)
            except ValueError:
                problems.append(
                    f"{where}: verdict is one of "
                    + ", ".join(v.value for v in Verdict)
                    + "; depth one of "
                    + ", ".join(d.value for d in DEPTHS)
                )
                continue
            if computed:
                try:
                    ran, values, code = _computed_line(
                        subjects[sid],
                        m,
                        verdict,
                        spec_code(spec, subjects[sid]["file"]),
                    )
                except _Refused as refused:
                    problems.append(f"{where}: {refused}")
                    continue
                if ran is not None and spec.get("runs") is None:
                    problems.append(f"{where}: {NO_RUNS}")
                    continue
                judges.append(
                    JudgeLine(
                        number,
                        sid,
                        verdict,
                        depth,
                        (),
                        m.group("evidence").strip(),
                        ran,
                        values,
                        code,
                    )
                )
                continue
            pages: tuple[tuple[str, int], ...] = ()
            if m.group("pages"):
                parsed = _pages(m.group("pages"))
                if parsed is None:
                    problems.append(
                        f"{where}: pages are `pages KEY:N, KEY:N`, N the page's "
                        "position in the PDF from 1"
                    )
                    continue
                pages = parsed
                stray = _stray_pages(spec, subjects[sid], pages)
                if stray:
                    problems.append(f"{where}: {stray}")
                    continue
            problem = _pages_problem(
                spec, subjects[sid]["aspect"], verdict, _laid_pages(spec, pages)
            )
            if problem:
                problems.append(f"{where}: {problem}")
                continue
            evidence = m.group("evidence").strip()
            if (
                subjects[sid]["aspect"] == Aspect.VALID.value
                and verdict is Verdict.VERIFIED
            ):
                unnamed = [h for h in subjects[sid]["names"] if not _named(evidence, h)]
                if unnamed:
                    problems.append(
                        f"{where}: a verified valid names each block J uses that it "
                        f"checked; the evidence names not {', '.join(unnamed)}"
                    )
                    continue
            recalled = _recalled(subjects[sid], verdict, pages)
            if recalled:
                problems.append(f"{where}: {recalled}")
                continue
            judges.append(JudgeLine(number, sid, verdict, depth, pages, evidence))
            continue
        if line.startswith("rule "):
            m = RULE.match(line)
            if m is None:
                problems.append(f"{where}: a rule line is `{RULE_FORM}`: {line!r}")
                continue
            sid = m.group("subject")
            if not subjects.get(sid, {}).get("rules"):
                problems.append(
                    f"{where}: {sid} takes no rule line: only a subject marked "
                    "`⊢ unsupported` does"
                )
                continue
            try:
                ruling = RulingKind(m.group("ruling"))
            except ValueError:
                ruling = None
            if ruling is None or ruling is RulingKind.LEFT_UNARGUED:
                problems.append(
                    f"{where}: ruling is one of "
                    + ", ".join(k.value for k in AGENT_RULINGS)
                    + (
                        "; left-unargued is the author's, never an agent's"
                        if ruling is not None
                        else ""
                    )
                )
                continue
            given = m.group("severity")
            pages = ()
            if m.group("pages"):
                parsed = _pages(m.group("pages"))
                if parsed is None:
                    problems.append(
                        f"{where}: pages are `pages KEY:N, KEY:N`, N the page's "
                        "position in the PDF from 1"
                    )
                    continue
                pages = parsed
                # a ruling's pages are read as a `true` reading's
                problem = _stray_pages(spec, {"aspect": Aspect.TRUE.value}, pages)
                if not problem:
                    failed = unread_pages(spec, _laid_pages(spec, pages))
                    problem = failed_read(failed) if failed else ""
                if problem:
                    problems.append(f"{where}: {problem}")
                    continue
            if ruling is not RulingKind.OWED:
                if given is not None:
                    problems.append(
                        f"{where}: only an owed ruling carries a severity: "
                        f"`{RULE_FORM}`"
                    )
                    continue
                rules.append(
                    RuleLine(
                        number, sid, ruling, m.group("reason").strip(), pages=pages
                    )
                )
                continue
            reason, _, fix = m.group("reason").rpartition(" — ")
            if given not in ("1", "2", "3") or not reason.strip() or not fix.strip():
                problems.append(
                    f"{where}: an owed rule line is `{OWED_FORM}`: severity 1 to 3, "
                    "then why, then what would discharge it, the fix last"
                )
                continue
            rules.append(
                RuleLine(
                    number,
                    sid,
                    ruling,
                    reason.strip(),
                    severity=int(given),
                    fix=fix.strip(),
                    pages=pages,
                )
            )
            continue
        if line.startswith("keep "):
            m = KEEP.match(line)
            if m is None:
                problems.append(f"{where}: a keep line is `{KEEP_FORM}`: {line!r}")
                continue
            sid = m.group("subject")
            candidate: Mapping[str, Any] | None = subjects.get(sid, {}).get("candidate")
            if candidate is None:
                problems.append(
                    f"{where}: {sid} shows no ruling under an older question to "
                    "keep: a rule line"
                )
                continue
            if candidate["kind"] not in {k.value for k in AGENT_RULINGS}:
                problems.append(
                    f"{where}: {candidate['kind']} is the author's, never an agent's "
                    "to keep: write your own rule line"
                )
                continue
            if candidate["kind"] == RulingKind.OWED.value:
                problems.append(
                    f"{where}: `keep` gives back a ruling's kind and reason "
                    "alone, and an owed ruling is ruled again with the severity "
                    f"and fix the current question asks: write your own rule "
                    f"line, `{OWED_FORM}`"
                )
                continue
            if subjects.get(sid, {}).get("proposal"):
                problems.append(
                    f"{where}: {sid} carries a person's proposed ruling, which a "
                    "candidate kept answers nothing of: write your own rule line"
                )
                continue
            gone = _gone_quotes(spec, candidate["reason"])
            if gone:
                problems.append(
                    f"{where}: the ruling's reason quotes {gone[0]!r}, which no "
                    "block of the paper holds now: it does not hold verbatim; write "
                    "your own rule line"
                )
                continue
            rules.append(
                RuleLine(
                    number,
                    sid,
                    RulingKind(candidate["kind"]),
                    candidate["reason"],
                    candidate["ruling"],
                )
            )
            continue
        if line.startswith("find "):
            m = FIND.match(line)
            if m is None:
                problems.append(f"{where}: a finding is `{FIND_FORM}`: {line!r}")
                continue
            aspect = next(
                (a for a in column.found if a.value == m.group("aspect")), None
            )
            if aspect is None:
                problems.append(
                    f"{where}: the {spec['session']} session finds "
                    + ", ".join(a.value for a in column.found)
                    + f", not {m.group('aspect')}"
                )
                continue
            anchors = _anchors_read(column, m.group("anchors"))
            problem = _unknown_anchors(spec, column, fetched, anchors)
            if problem:
                problems.append(f"{where}: {problem}")
                continue
            named = _named_works(anchors)
            if column.suggests and int(m.group("severity")) != 3:
                problems.append(
                    f"{where}: this session's finding is the paper placed nowhere "
                    "in its field, severity 3; a work a reader might expect at a "
                    f"place is a suggestion, `{SUGGEST_FORM}`"
                )
                continue
            if aspect in WORK_ASPECTS and (
                not named
                or (anchors[0] not in texts and anchors[0] not in spec["groups"])
            ):
                problems.append(
                    f"{where}: a {aspect.value} finding sits on the block or group "
                    "whose words it quotes, first, then names the work it rests on, "
                    "`work:KEY` (one of the bibliography, cited or not, or one "
                    "found and fetched)"
                )
                continue
            pages, problem = _work_pages(
                spec, column, fetched, named, m.group("pages"), "a finding's"
            )
            if problem:
                problems.append(f"{where}: {problem}")
                continue
            if any(a.startswith(FILE_PREFIX) for a in anchors[1:]):
                problems.append(
                    f"{where}: a `file:KEY` anchor comes first, its lines' words "
                    "quoted from the file"
                )
                continue
            problem = _words_problem(spec, anchors, m.group("words"))
            if problem:
                problems.append(f"{where}: {problem}")
                continue
            what, _, fix = m.group("rest").partition(" — ")
            if not what.strip():
                problems.append(f"{where}: a finding names its problem")
                continue
            finds.append(
                FindLine(
                    number,
                    aspect,
                    anchors,
                    int(m.group("severity")),
                    m.group("words"),
                    what.strip(),
                    fix.strip() or None,
                    pages,
                )
            )
            continue
        if column.suggests and line.startswith("suggest "):
            m = SUGGEST.match(line)
            if m is None:
                problems.append(f"{where}: a suggestion is `{SUGGEST_FORM}`: {line!r}")
                continue
            anchors = _anchors_read(column, m.group("anchors"))
            problem = _unknown_anchors(spec, column, fetched, anchors)
            if problem:
                problems.append(f"{where}: {problem}")
                continue
            named = _named_works(anchors)
            if not named or (
                anchors[0] not in texts and anchors[0] not in spec["groups"]
            ):
                problems.append(
                    f"{where}: a suggestion sits on the block or group whose words "
                    "it quotes, first, then names the work it offers, `work:KEY`"
                )
                continue
            cited = _cited_at(spec, anchors[0])
            if any(w in cited for w in named):
                problems.append(
                    f"{where}: {anchors[0]} cites "
                    + ", ".join(f"work:{w}" for w in named if w in cited)
                    + " already: a work the paper cites at a place is not offered "
                    "there"
                )
                continue
            pages, problem = _work_pages(
                spec, column, fetched, named, m.group("pages"), "a suggestion's"
            )
            if problem:
                problems.append(f"{where}: {problem}")
                continue
            problem = _words_problem(spec, anchors, m.group("words"))
            if problem:
                problems.append(f"{where}: {problem}")
                continue
            suggested.append(
                SuggestLine(number, anchors, m.group("words"), m.group("why"), pages)
            )
            continue
        if column.suggests and line.startswith("topic "):
            m = TOPIC.match(line)
            if m is None:
                problems.append(f"{where}: a topic line is `{TOPIC_FORM}`: {line!r}")
                continue
            anchor = m.group("anchor")
            if anchor not in texts and anchor not in spec["groups"]:
                problems.append(
                    f"{where}: no block or group {anchor} (a handle as paper.md "
                    "shows it)"
                )
                continue
            works = tuple(
                WORK_PREFIX + found_key(w.strip()[len(WORK_PREFIX) :])
                for w in m.group("works").split(",")
            )
            problem = _unknown_anchors(spec, column, fetched, works)
            if problem:
                problems.append(f"{where}: {problem}")
                continue
            problem = _words_problem(spec, (anchor,), m.group("words"))
            if problem:
                problems.append(f"{where}: {problem}")
                continue
            topics.append(
                TopicLine(
                    number,
                    anchor,
                    m.group("words"),
                    m.group("topic"),
                    tuple(_named_works(works)),
                )
            )
            continue
        if column.fetches and line.startswith("checked "):
            m = CHECKED.match(line)
            if m is None:
                problems.append(
                    f"{where}: a checked line is `{CHECKED_FORM}`: {line!r}"
                )
                continue
            anchor = m.group("anchor")
            if anchor not in texts and anchor not in spec["groups"]:
                problems.append(
                    f"{where}: no block or group {anchor} (a handle as paper.md "
                    "shows it)"
                )
                continue
            problem = _words_problem(spec, (anchor,), m.group("words"))
            if problem:
                problems.append(f"{where}: {problem}")
                continue
            checked.append(
                CheckedLine(
                    number,
                    anchor,
                    m.group("words"),
                    m.group("searched").strip(),
                    m.group("found").strip(),
                )
            )
            continue
        if line.startswith("edge "):
            m = EDGE.match(line)
            if m is None:
                problems.append(f"{where}: an edge line is `{EDGE_FORM}`: {line!r}")
                continue
            if not column.edges:
                problems.append(f"{where}: the {spec['session']} session adds no edge")
                continue
            if m.group("owner") not in texts and m.group("owner") not in spec["groups"]:
                problems.append(f"{where}: no block or group {m.group('owner')}")
                continue
            edges.append(EdgeLine(number, m.group("owner"), m.group("line")))
            continue
        if line.startswith("group "):
            m = GROUP.match(line)
            if m is None:
                problems.append(f"{where}: a group line is `{GROUP_FORM}`: {line!r}")
                continue
            if not column.edges:
                problems.append(
                    f"{where}: the {spec['session']} session makes no group"
                )
                continue
            kinds = {k.value for k in GroupKind}
            if m.group("kind") not in kinds:
                problems.append(
                    f"{where}: a group's kind is one of {', '.join(sorted(kinds))}, "
                    f"not {m.group('kind')}"
                )
                continue
            first, last = m.group("first"), m.group("last")
            unknown = [h for h in (first, last) if h not in blocks]
            if unknown:
                problems.append(
                    f"{where}: no block {unknown[0]} (a block's handle as paper.md "
                    "shows it; a group holds blocks)"
                )
                continue
            if blocks.index(first) >= blocks.index(last):
                problems.append(
                    f"{where}: {first} must come before {last}: a group is a run "
                    "of two blocks or more, first to last"
                )
                continue
            grouped.append(
                GroupLine(
                    number, GroupKind(m.group("kind")), first, last, m.group("why")
                )
            )
            continue
        problems.append(
            f"{where}: not a line this sheet takes ("
            + (
                f"{CHECKED_FORM}; {FOUND_FIND_FORM}"
                + (f"; {TOPIC_FORM}; {SUGGEST_FORM}" if column.suggests else "")
                if column.fetches
                else f"{column.judge_form}; {FIND_FORM}"
            )
            + (
                f"; {RULE_FORM}"
                if any(s.get("rules") for s in subjects.values())
                else ""
            )
            + (
                f"; {KEEP_FORM}"
                if any(s.get("candidate") for s in subjects.values())
                else ""
            )
            + (f"; {EDGE_FORM}; {GROUP_FORM}" if column.edges else "")
            + (f"; {STANDS_FORM}; {FIXED_FORM}" if questions else "")
            + (
                f"; {READING_FORM}"
                if any(q.get("proposal") for q in questions.values())
                else ""
            )
            + f"): {line!r}"
        )
    counted: dict[str, int] = {}
    for j in judges:
        counted[j.subject] = counted.get(j.subject, 0) + 1
    problems += [
        f"{sid}: {n} judge lines; one subject takes one"
        for sid, n in counted.items()
        if n > 1
    ]
    ruled: dict[str, int] = {}
    for r in rules:
        ruled[r.subject] = ruled.get(r.subject, 0) + 1
    problems += [
        f"{sid}: {n} rule lines (a keep line is one); one subject takes one"
        for sid, n in ruled.items()
        if n > 1
    ]
    shown = {j.subject for j in judges if j.verdict is Verdict.VERIFIED}
    problems += [
        f"line {r.number}: {r.subject} is ruled {r.ruling.value}, but its judge "
        f"line is not verified: a claim you could not show holds is not "
        f"{r.ruling.value}; rule it owed"
        for r in rules
        if r.ruling in (RulingKind.KNOWN, RulingKind.IMMEDIATE)
        and r.subject in counted
        and r.subject not in shown
    ]
    problems += _unanswered_settlings(subjects, rules, settles)
    stood = _stood_argued(spec, edges)
    problems += [
        f"line {r.number}: {r.subject} takes no rule line: your {stood[r.subject]}"
        for r in rules
        if r.subject in stood
    ]
    unruled = [
        sid
        for sid, s in subjects.items()
        if s.get("rules") and sid not in ruled and sid not in stood
    ]
    if unruled:
        problems.append(f"no rule line on {', '.join(unruled)}")
    settled: dict[str, int] = {}
    for s in settles:
        settled[s.question] = settled.get(s.question, 0) + 1
    problems += [
        f"{qid}: {n} stands, fixed or reading lines; one finding takes one"
        for qid, n in settled.items()
        if n > 1
    ]
    unsettled = [qid for qid in questions if qid not in settled]
    if unsettled:
        problems.append(f"no stands, fixed or reading line on {', '.join(unsettled)}")
    dropped = _dropped(spec, edges)
    missing = [sid for sid in subjects if sid not in counted and sid not in dropped]
    if column.judges:
        if missing:
            problems.append(f"no judge line on {', '.join(missing)}")
    elif not done:
        problems.append(f"no done mark: end the sheet with `{DONE}`")
    problems += _undisposed(spec, topics, suggested, finds)
    if done:
        problems += _unread_pointers(spec, checked, finds)
    if (
        column.fetches
        and not checked
        and not topics
        and not spec.get("lapsed")
        and not spec.get(PART)
    ):
        problems.append(
            f"no checked line: list {CHECKED_WHAT[session_named(spec['session'])]}, "
            f"`{CHECKED_FORM}`"
        )
    return Read(
        tuple(judges),
        tuple(finds),
        tuple(edges),
        done,
        tuple(problems),
        tuple(checked),
        tuple(fetched),
        tuple(settles),
        tuple(rules),
        tuple(suggested),
        tuple(topics),
        tuple(grouped),
    )


def _gone_quotes(spec: Mapping[str, Any], reason: str) -> list[str]:
    """The words a reason quotes that no block or group of the paper holds
    now (`spec`'s texts), compared without case: a reason may quote a
    heading's words in lower case, and they still hold."""
    texts = "\n".join(spec["texts"].values()).casefold()
    quoted = [a or b for a, b in QUOTED.findall(reason)]
    return [q for q in quoted if (w := unmarked(norm(q))) and w.casefold() not in texts]


def _unread_pointers(
    spec: Mapping[str, Any],
    checked: Sequence[CheckedLine],
    finds: Sequence[FindLine],
) -> list[str]:
    """Each claim standing `unsupported` that no `checked` or `find` line on
    its block quotes, once the sheet is done: where a result of the field
    may be used bare, held against a reading made without it."""
    quoted: dict[str, list[str]] = {}
    for line in (*checked, *finds):
        for anchor in line.anchors:
            quoted.setdefault(anchor, []).append(unmarked(line.words).casefold())
    problems = []
    for handle, words, why in spec.get(POINTERS, ()):
        claim = unmarked(words).casefold()
        if any(q and (q in claim or claim in q) for q in quoted.get(handle, ())):
            continue
        problems.append(
            f'unread: `@@ {handle}` "{words}" stands with no argument and no '
            "citation, and no line of yours quotes it: write a `checked` line on "
            "it, a result of the field and its source, or why it is not one (the "
            "paper's own claim, a fact proved at sight)"
            + (f"; another reader {why}" if why else "")
        )
    return problems


def _cited_at(spec: Mapping[str, Any], anchor: str) -> set[str]:
    """The works a block cites, or a group's blocks."""
    cites: Mapping[str, Sequence[str]] = spec.get("cites_at", {})
    members = spec["groups"].get(anchor, [anchor])
    return {w for m in members for w in cites.get(m, ())}


def _undisposed(
    spec: Mapping[str, Any],
    topics: Sequence[TopicLine],
    suggested: Sequence[SuggestLine],
    finds: Sequence[FindLine],
) -> list[str]:
    """Each work a topic line expects that its block does not cite and no
    suggestion or finding names: nothing the map holds drops out unsaid."""
    named = {w for line in (*suggested, *finds) for w in _named_works(line.anchors)}
    found = []
    for t in topics:
        cited = _cited_at(spec, t.anchor)
        for work in t.works:
            if work not in cited and work not in named:
                found.append(
                    f"line {t.number}: the topic expects work:{work}, which "
                    f"{t.anchor} does not cite: offer it (`suggest`), file it "
                    "(`find`), or take it off the map if a reader would not "
                    "expect it there"
                )
    return found


def _dropped(spec: Mapping[str, Any], edges: Sequence[EdgeLine]) -> set[str]:
    """The `valid` subjects the sheet's justifies edge lines take out of
    J's cover: a line naming claims of T by handle
    covers those alone, so a subject on another claim of T needs no judge
    line; the state the line makes offers it no more."""
    handles: Mapping[str, Sequence[str]] = spec.get("handles") or {}
    found: set[str] = set()
    for e in edges:
        m = es.EDGE.match(e.line)
        if m is None or m.group("kind") != "justifies":
            continue
        target = m.group("target") or ""
        tokens = [t.strip() for t in (m.group("parts") or "").split(",")]
        named = [t for t in tokens if t in handles.get(target, ())]
        if not named:
            continue
        found |= {
            sid
            for sid, s in spec["subjects"].items()
            if s.get("edge")
            and s["edge"][:2] == [e.owner, target]
            and s["edge"][2] not in named
        }
    return found


def _self_named(m: re.Match[str], held: Sequence[str]) -> list[str]:
    """The claims a self line stands `self` of those its block holds
    (`held`): the ones it names, else the block's one claim."""
    named = [h.strip() for h in (m.group("own") or "").split(",") if h.strip()]
    return named or (list(held) if len(held) == 1 else [])


#: Why a line a review writes takes its claim's rule line away, by the
#: standing it gives (`edges_file.argued_by`).
_STOOD_WHY = {
    Status.JUSTIFIED: "justifies line stands it justified, so the paper argues it",
    Status.SELF: "self line stands it self, so the paper argues it",
    Status.RESTATED: "about line stands it restated, its support the claim it restates",
}


def _unanswered_settlings(
    subjects: Mapping[str, Mapping[str, Any]],
    rules: Sequence[RuleLine],
    settles: Sequence[SettleLine],
) -> list[str]:
    """An `owed` rule line beside a settled finding on its claim's words
    (one answered `fixed` or `dismissed`, or one this sheet settles `fixed`)
    that does not name it: the two answers contradict each other unless the
    ruling says why it stands despite the other (`docs/model.md` § 8)."""
    fixed = {s.question for s in settles if s.settle is Settle.FIXED}
    problems: list[str] = []
    for r in rules:
        if r.ruling is not RulingKind.OWED:
            continue
        s = subjects.get(r.subject, {})
        names = [f[:12] for f in s.get("answered", ())] + [
            q for q in s.get("settling", ()) if q in fixed
        ]
        said = f"{r.reason} {r.fix or ''}"
        missed = [n for n in names if not re.search(rf"(?<![\w-]){n}(?![\w-])", said)]
        if missed:
            problems.append(
                f"line {r.number}: {r.subject} is ruled owed beside finding "
                f"{', '.join(missed)} on its words, settled: name it in the reason "
                "and say why the argument is still owed despite that answer, or "
                "rule otherwise"
            )
    return problems


def _stood_argued(spec: Mapping[str, Any], edges: Sequence[EdgeLine]) -> dict[str, str]:
    """The `rules` subjects the sheet's lines stand argued (`edges_applied`),
    to why: a justifies line stands one `justified`, a self line `self`,
    an about line onto a claim `restated` (`edges_file.argued_by`). They
    take no rule line, the paper arguing them."""
    handles: Mapping[str, Sequence[str]] = spec.get("handles") or {}
    shown = (spec.get("edges") or {}).get("shown")
    parsed = None if shown is None else es.parse(shown)
    found: dict[str, str] = {}
    for e in edges:
        m = es.EDGE.match(e.line)
        if m is None:
            continue
        if parsed is not None:
            block, status, claims = edges_file.argued_by(
                parsed, e.owner, e.line, restating=True
            )
        elif m.group("kind") == es.SELF:
            block, status = e.owner, Status.SELF
            claims = _self_named(m, handles.get(e.owner, ()))
        else:
            continue
        if not claims:
            continue
        why = _STOOD_WHY[status]
        named = {(block, h) for h in claims}
        found |= {
            sid: why
            for sid, s in spec["subjects"].items()
            if tuple(s.get("standing", ())) in named
        }
    return found


def _anchors_read(column: Column, given: str) -> tuple[str, ...]:
    """A line's anchors, a found work's key as kept."""
    return tuple(
        WORK_PREFIX + found_key(a[len(WORK_PREFIX) :])
        if a.startswith(WORK_PREFIX) and column.fetches
        else a
        for a in given.split()
    )


def _named_works(anchors: Sequence[str]) -> list[str]:
    return [a[len(WORK_PREFIX) :] for a in anchors if a.startswith(WORK_PREFIX)]


def _unknown_anchors(
    spec: Mapping[str, Any],
    column: Column,
    fetched: Sequence[str],
    anchors: Sequence[str],
) -> str | None:
    """Why a line's anchors name what the sheet does not hold, or None: a
    work by its key is one the paper cites, one of its bibliography the
    session reads (`entries`), or one fetched."""
    code = spec.get("code", {})
    works = {*spec["works"], *spec.get("entries", ()), *fetched}
    unknown = [
        a
        for a in anchors
        if a != PAPER_ANCHOR
        and not (a.startswith(WORK_PREFIX) and a[len(WORK_PREFIX) :] in works)
        and not (
            column.fetches
            and a.startswith(WORK_PREFIX)
            and LINKED_KEY.match(a[len(WORK_PREFIX) :])
        )
        and not (a.startswith(FILE_PREFIX) and a[len(FILE_PREFIX) :] in code)
        and a not in spec["texts"]
        and a not in spec["groups"]
    ]
    if not unknown:
        return None
    works_read = "work of the bibliography" if spec.get("entries") else "cited work"
    return (
        f"no block, group or {works_read} {', '.join(unknown)} "
        "(a handle as paper.md shows it, `paper`, or `work:KEY`"
        + (", or `file:KEY`, a code file laid in" if code else "")
        + (
            ", a found work by its identifier (`arxiv:ID`, `doi:DOI`), fetched "
            "by your `fetch` tool, or one with neither by a link to a public page "
            "of it"
            if column.fetches
            else ""
        )
        + ")"
    )


def _work_pages(
    spec: Mapping[str, Any],
    column: Column,
    fetched: Sequence[str],
    named: Sequence[str],
    given: str | None,
    whose: str,
) -> tuple[tuple[tuple[str, int], ...], str | None]:
    """A line's pages of the works it names, and why they cannot be read,
    or None: each of a work fetched, laid in or of the bibliography the
    session reads (`entries`, its other files by `work_of`), and named."""
    if not given:
        return (), None
    parsed = _pages(given)
    if parsed is not None:
        parsed = tuple((found_key(k), i) for k, i in parsed)
    readable = {*fetched, *spec.get("laid", ())}
    entries = set(spec.get("entries", ()))

    def stray_key(key: str) -> bool:
        # a work's other file (`KEY@…`) goes by its work's anchor
        work = work_of(key)
        return not (
            (key in named or work in named) and (key in readable or work in entries)
        )

    if not column.fetches:
        return (), (
            f"{whose} pages: a finding of this session names no pages"
            + (
                "; the pages a reading rests on go on its judge or rule line"
                if column.judges
                else ""
            )
        )
    stray = sorted({k for k, _ in parsed or () if stray_key(k)})
    if parsed is None or stray:
        return (), (
            f"{whose} pages are `pages KEY:N, …` of a work fetched by your "
            "`fetch` tool"
            + (
                " or one of the paper's bibliography"
                if entries
                else " or a cited one the session reads"
                if spec.get("laid")
                else ""
            )
            + " and named as its `work:KEY` anchor"
            + (f" ({', '.join(stray)} is not)" if stray else "")
        )
    # a fetched work's pages are read where its tool fetched them
    # (`cached_page_problems`), one not laid in on demand
    # (`unlaid_page_problems`)
    failed = unread_pages(
        spec, _laid_pages(spec, [(k, i) for k, i in parsed if k not in fetched])
    )
    if failed:
        return (), failed_read(failed)
    return parsed, None


#: Why a run cannot be read where the review names no paper directory.
NO_RUNS = (
    "this review was given no paper directory to run its scripts in: no run can be read"
)


def failed_read(failed: Sequence[str]) -> str:
    """The runner's refusal of pages that failed to read, each `KEY:N status`."""
    return "a failed read is never evidence: " + "; ".join(failed)


def unread_pages(
    spec: Mapping[str, Any], pages: Sequence[tuple[str, int]]
) -> list[str]:
    """Each of `pages` that does not read as the brief laid it in, `KEY:N
    status` (a page the work was not read with, `KEY:N absent`), once; none
    where the spec does not hold the pages' statuses."""
    held: Mapping[str, Mapping[str, str]] | None = spec.get("pages")
    if held is None:
        return []
    readable = {s.value for s in READ}
    found = []
    for key, index in dict.fromkeys(pages):
        status = held.get(key, {}).get(str(index), ABSENT)
        if status not in readable:
            found.append(f"{key}:{index} {status}")
    return found


#: A page's status where the brief laid in no such page.
ABSENT = "absent"


def _stray_pages(
    spec: Mapping[str, Any],
    subject: Mapping[str, Any],
    pages: Sequence[tuple[str, int]],
) -> str:
    """Why a line's pages are of a work it may not read, else '': for a
    `cited` subject, any work but its own; for `valid` and `true`, and a
    rule line, a work neither the paper cites (`works`) nor its
    bibliography holds (`entries`), as the filing refuses it
    (`judgments.read_pages`)."""
    cited = subject["aspect"] == Aspect.CITED.value
    # a cited site's own work, laid in with pages or not: a page that does
    # not read is refused as one (`unread_pages`)
    allowed = (
        (subject.get("cites") or ())
        if cited
        else {*spec["works"], *spec.get("entries", ())}
    )
    stray = sorted({k for k, _ in pages if work_of(k) not in allowed})
    if not stray:
        return ""
    if cited:
        return (
            f"{', '.join(stray)} is not the cited work ({', '.join(allowed) or 'none'})"
        )
    return (
        f"{', '.join(stray)} is no work of the paper's bibliography: read the "
        "works it holds, by their keys"
    )


def _laid_pages(
    spec: Mapping[str, Any], pages: Sequence[tuple[str, int]]
) -> list[tuple[str, int]]:
    """Of a line's pages, those the brief laid in, whose statuses the spec
    holds; all of them where no page is read on demand (no `reads`). The
    others are read by `unlaid_page_problems`."""
    if "reads" not in spec:
        return list(pages)
    held = spec.get("pages", {})
    return [(k, i) for k, i in pages if k in held]


def unlaid_page_problems(spec: Mapping[str, Any], sheet: Read) -> list[str]:
    """Each `valid`, `true`, rule, find or suggest line's page of a work of
    the bibliography the brief did not lay in that does not read, read on
    demand through the paper's sources (`reads`) as the filing reads it
    again and stamps it, refused as the filing refuses it; nothing where no
    page is read on demand. A fetched work's pages are read where its tool
    fetched them (`cached_page_problems`)."""
    where = spec.get("reads")
    if not isinstance(where, Mapping):
        return []
    held = {*spec.get("pages", {}), *sheet.fetches}
    subjects = spec["subjects"]
    lines = [
        *(
            j
            for j in sheet.judges
            if subjects[j.subject]["aspect"] != Aspect.CITED.value
        ),
        *sheet.rules,
        *sheet.finds,
        *sheet.suggests,
    ]
    named = {(k, i) for line in lines for k, i in line.pages if k not in held}
    if not named:
        return []
    from axiomation import evidence

    try:
        sources = evidence.sources_for(Path(where["paper"]), Path(where["store"]))
    except evidence.EvidenceError as error:
        return [f"the cited works' pages cannot be read here: {error}"]
    statuses: dict[str, dict[str, str]] = {}
    for key, index in sorted(named):
        stamp = sources.stamp(key, index)
        statuses.setdefault(key, {})[str(index)] = stamp.read_status.value
    found = []
    for line in lines:
        pages = [(k, i) for k, i in line.pages if k in statuses]
        failed = unread_pages({"pages": statuses}, pages)
        if failed:
            found.append(f"line {line.number}: {failed_read(failed)}")
    return found


def _pages_problem(
    spec: Mapping[str, Any],
    aspect: str,
    verdict: Verdict,
    pages: Sequence[tuple[str, int]],
) -> str:
    """Why a judge line's pages are no evidence, as the runner reads them
    again (`judgments.read_pages`), else '': for `valid` and `true`, a page
    that failed to read; for `cited`, a verdict on what the work holds
    whose pages none read."""
    if not pages:
        return ""
    failed = unread_pages(spec, pages)
    if aspect != Aspect.CITED.value:
        return failed_read(failed) if failed else ""
    if verdict in READ_VERDICTS and len(failed) == len(dict.fromkeys(pages)):
        return (
            f"a {verdict.value} cited judgment rests on a page read: name one that "
            f"reads (`pages KEY:N`), or judge it inconclusive (read: "
            f"{'; '.join(failed)}; a failed read is never evidence)"
        )
    return ""


def _settle_problem(
    spec: Mapping[str, Any],
    question: Mapping[str, Any],
    settle: Settle,
    words: str | None,
) -> str:
    """Why a `stands` or `fixed` line cannot settle its finding, as the
    runner's check refuses it at the state (`Settling`), else ''."""
    if settle is Settle.FIXED and words is not None:
        return "a fixed line quotes nothing"
    held: Mapping[str, Any] | None = question.get("settling")
    if held is None:
        # a spec that holds no settling: the words alone
        return "" if words is None else _words_problem(spec, question["anchors"], words)
    if settle is Settle.FIXED:
        return held["fixed"] or ""
    if held["refused"]:
        return held["refused"]
    if words is None:
        return held["stands"] or ""
    if held["others"]:
        return held["others"]
    requoted: Mapping[str, Any] | None = held["requoted"]
    if requoted is None:
        return (
            f"finding {held['finding']} quotes no words: there is nothing to quote anew"
        )
    wanted = unmarked(words)
    name = requoted["name"]
    if not wanted:
        error = f"a {requoted['kind']} anchor names its words"
    elif requoted["kind"] == "file":
        text = requoted["text"]
        found = 0 if text is None else text.count(wanted)
        error = (
            f"{wanted!r} is found {found} times in file {name}, not once"
            if found != 1
            else ""
        )
    else:
        texts: Mapping[str, str] = spec["texts"]
        holders = [m for m in requoted["members"] if wanted in texts.get(m, "")]
        if len(holders) == 1:
            name = holders[0]
        found = texts.get(name, "").count(wanted)
        if requoted["kind"] == "part" and wanted in requoted["claims"]:
            found = 1
        error = (
            f"{wanted!r} is found {found} times in {name}'s text, not once"
            if found != 1
            else ""
        )
    if not error:
        return ""
    return (
        f"finding {held['finding']}'s anchor at state {held['state']}: {error}; "
        "quote its words as they read now"
    )


def found_key(key: str) -> str:
    """A found work's key as kept: a DOI lower-cased, as DOIs compare."""
    return key.lower() if key.lower().startswith("doi:") else key


# --- the edge lines ----------------------------------------------------------------


class EdgeLinesRefused(ValueError):
    """Edge lines that cannot be applied to the state's edges sheet, each
    problem named."""

    def __init__(self, problems: Sequence[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = tuple(problems)


def _parts(m: re.Match[str]) -> set[str]:
    return {p.strip() for p in (m.group("parts") or "").split(",") if p.strip()}


def _overlap(a: re.Match[str], b: re.Match[str]) -> bool:
    """Whether two justifies lines onto one target cover a claim in common:
    one naming no claim covers each (`docs/model.md` § 3)."""
    left, right = _parts(a), _parts(b)
    return not left or not right or bool(left & right)


def corrected(written: Mapping[str, Sequence[str]], line: EdgeLine) -> list[str]:
    """The lines of the state's edges sheet (`written`, by owner) an edge
    line replaces, one edge per source, kind and target: a justifies
    line's, J's lines onto the same T covering a claim it covers (one
    naming no claim covers each); a self mark's, the block's marks with the
    same quote; an about or a uses line's, the block's line of its kind
    onto the same target."""
    m = es.EDGE.match(line.line)
    if m is None or m.group("kind") not in ("justifies", es.SELF, "about", "uses"):
        return []
    found = []
    for x in written.get(line.owner, []):
        old = es.EDGE.match(x)
        if old is None or old.group("kind") != m.group("kind") or x == line.line:
            continue
        if m.group("kind") == "justifies":
            same = old.group("target") == m.group("target") and _overlap(old, m)
        elif m.group("kind") in ("about", "uses"):
            same = old.group("target") == m.group("target")
        else:
            same = norm(old.group("evidence")) == norm(m.group("evidence"))
        if same:
            found.append(x)
    return found


def narrowed(line: EdgeLine, replaced: Sequence[str]) -> str | None:
    """Why a uses line narrows the line it replaces onto its target, else
    None: a review widens what an edge relies on, so it names each part the
    old line named, and a line naming none relies on all of its target."""
    m = es.EDGE.match(line.line)
    if m is None or m.group("kind") != "uses":
        return None
    target = m.group("target")
    new = _parts(m)
    for x in replaced:
        old = es.EDGE.match(x)
        assert old is not None
        if not new:
            continue
        before = _parts(old)
        if not before:
            return (
                f"{line.owner} → uses {target}: {line.owner}'s line onto {target} "
                f"names no part, so the edge relies on all of {target} already: "
                "a line naming parts would narrow it; leave it, or name no part"
            )
        missing = sorted(before - new)
        if missing:
            return (
                f"{line.owner} → uses {target}: names each part {line.owner}'s line "
                f"onto {target} named: [{', '.join(missing)}]"
            )
    return None


def edges_applied(shown: str, edges: Sequence[EdgeLine]) -> str | None:
    """The state's edges sheet as an agent edits it (`shown`) with the edge
    lines added, each line once, as an agent's edges edit gives them, a
    line replacing the one it corrects (`corrected`), a justifies, self or
    about line standing argued the unsupported claims it names
    (`edges_file.stood_argued`); None where there are none. Refused
    (`EdgeLinesRefused`) where a uses line narrows the line it replaces
    (`narrowed`), or the lines cannot be applied."""
    written = edges_file.written_lines(shown)
    found: list[tuple[str, str, str]] = []
    problems = []
    for e in edges:
        replaced = corrected(written, e)
        problem = narrowed(e, replaced)
        if problem is not None:
            problems.append(problem)
        found += [(e.owner, edges_file.RETRACT, x) for x in replaced]
        found.append((e.owner, edges_file.ADD, e.line))
    if problems:
        raise EdgeLinesRefused(problems)
    lines = tuple(dict.fromkeys(found))
    if not lines:
        return None
    try:
        return edges_file.applied_fragment(
            shown, edges_file.Fragment(lines=lines), restating=True
        )
    except edges_file.FragmentError as error:
        raise EdgeLinesRefused([str(error)]) from None


def edge_lines_problems(
    held: Mapping[str, Any], edges: Sequence[EdgeLine]
) -> list[str]:
    """What the edges check refuses in the state's edges sheet with the
    edge lines applied, as an agent's edges edit of the state is checked
    (`edges_file.answer_problems` under the spec `held` holds)."""
    try:
        edited = edges_applied(held["shown"], edges)
    except EdgeLinesRefused as refused:
        return list(refused.problems)
    if edited is None:
        return []
    return edges_file.answer_problems(dict(held["spec"]), edited)


def edge_problems(
    spec: Mapping[str, Any], sheet: Read, beside: Path | None = None
) -> list[str]:
    """The sheet's edge lines against the state's edges (`spec`'s `edges`),
    each problem opening `edge lines: `. With `beside`, the gate's
    directory, checked again only when the edge lines changed since the
    last check there."""
    held = spec.get("edges")
    if held is None or not sheet.edges:
        return []
    digest = hashlib.sha256(
        "\n".join(f"{e.owner} {e.line}" for e in sheet.edges).encode("utf-8")
    ).hexdigest()
    cache = None if beside is None else beside / EDGES_CHECKED
    if cache is not None:
        with contextlib.suppress(OSError, ValueError, KeyError, TypeError):
            last = json.loads(gates.read_regular(cache))
            if last["digest"] == digest:
                return [str(p) for p in last["problems"]]
    found = [EDGE_LINES + p for p in edge_lines_problems(held, sheet.edges)]
    if cache is not None:
        with contextlib.suppress(OSError):
            gates.write_regular(
                cache,
                json.dumps({"digest": digest, "problems": found}, ensure_ascii=False),
            )
    return found


# --- the runs ----------------------------------------------------------------------


def _shown(stdout: str) -> str:
    text = stdout.strip() or "(nothing)"
    if len(text) > OUTPUT_SHOWN:
        text = text[:OUTPUT_SHOWN] + f"\n… ({len(stdout):,} characters in all)"
    return text


def run_problems(
    spec: Mapping[str, Any], sheet: Read, within: float | None = None
) -> tuple[list[str], list[dict[str, Any]]]:
    """Each run a read sheet's `computed` judge lines name, made where the
    spec says (cached by `computations.run`), and what is wrong with it: a
    file changed since the state, a run that did not run under a verdict
    other than `inconclusive`, a value's output side not in its stdout
    (shown it, so the session can match what was printed). With `within`,
    seconds for them all (`computations.run_within`): a run past them, or
    one not started once they are spent, is left out, uncached, for the
    runner's check. Also each run as the gate log keeps it."""
    where = spec.get("runs")
    if where is None:
        return [], []
    paper, cache = Path(where["paper"]), Path(where["cache"])
    deadline = None if within is None else time.monotonic() + within
    problems: list[str] = []
    log: list[dict[str, Any]] = []
    for j in sheet.judges:
        if j.run is None:
            continue
        key, arguments = j.run[0], j.run[1:]
        try:
            if deadline is None:
                ran = computations.run(paper, key, arguments, cache)
            else:
                ran = computations.run_within(
                    paper, key, arguments, cache, deadline - time.monotonic()
                )
        except SettingsError as error:
            problems.append(f"line {j.number}: {error}")
            continue
        call = shlex.join(j.run)
        if ran is None:
            log.append({"run": call, "status": "past the gate's time", "seconds": None})
            continue
        log.append(
            {"run": call, "status": ran.stamp.status.value, "seconds": ran.seconds}
        )
        if ran.stamp.blob != spec["code"][key]["blob"]:
            problems.append(
                f"line {j.number}: {key} changed on disk since the state this review "
                "reads was kept: its run is no evidence of it; file inconclusive"
            )
            continue
        if not ran.stamp.ran and j.verdict is not Verdict.INCONCLUSIVE:
            problems.append(
                f"line {j.number}: `{call}` {ran.stamp.status.value}, and a run that "
                "did not run is never evidence: file inconclusive, with a finding "
                f"that the script does not run. Its stderr ends:\n{ran.stderr[-500:]}"
            )
            continue
        printed = normalize(ran.stdout)
        missing = [o for _, o in j.values if normalize(o) not in printed]
        if missing:
            problems.append(
                f"line {j.number}: "
                + ", ".join(f'"{o}"' for o in missing)
                + f" not in what `{call}` printed; copy the output's words as "
                f"printed:\n{_shown(ran.stdout)}"
            )
    return problems, log


# --- the found works ----------------------------------------------------------------

#: How long a session's `fetch` tool may spend on one work
#: (`review_tools`): under the host's tool timeout, as `GATE_RUN_SECONDS`
#: is under its hook's. A fetch past it is left unfinished and not asked
#: again.
GATE_FETCH_SECONDS = 80


def fetched_within(sources: Any, key: str, within: float) -> Any:
    """`sources.fetch(key)` within `within` seconds, a failure if it raised;
    None if it ran past them (its thread left to the process's exit)."""
    from axiomation import evidence

    found: list[Any] = []

    def fetch() -> None:
        try:
            found.append(sources.fetch(key))
        except Exception as error:  # noqa: BLE001 - a provider's own failure
            found.append(
                evidence.Results.failed(
                    evidence.ReadStatus.INFRASTRUCTURE, f"the fetch failed: {error}"
                )
            )

    worker = threading.Thread(target=fetch, daemon=True)
    worker.start()
    worker.join(within)
    return found[0] if found else None


def cached_page_problems(spec: Mapping[str, Any], sheet: Read) -> list[str]:
    """Each finding's and suggestion's page of a found work that does not
    read where the session's `fetch` tool fetched it, the paper's cache
    (the runner reads it again and stamps it), refused as the runner
    refuses it; a work never fetched has no page to name. Nothing is
    fetched here."""
    where = spec.get("fetch")
    named = {
        k for line in (*sheet.finds, *sheet.suggests) for k, _ in line.pages
    } & set(sheet.fetches)
    if not named or not isinstance(where, Mapping):
        return []
    from axiomation import evidence

    try:
        sources = evidence.sources_for(Path(where["paper"]), Path(where["store"]))
    except evidence.EvidenceError as error:
        return [f"the found works' pages cannot be read here: {error}"]
    statuses = {
        key: {
            str(p.page_index): p.read_status.value
            for p in sources.source_pages(key).pages
        }
        for key in sorted(named)
    }
    found = []
    for line in (*sheet.finds, *sheet.suggests):
        pages = [(k, i) for k, i in line.pages if k in statuses]
        failed = unread_pages({"pages": statuses}, pages)
        if failed:
            found.append(f"line {line.number}: {failed_read(failed)}")
    return found


@dataclass(frozen=True, slots=True)
class Checked:
    """A sheet as its gate and the runner's check read it: its lines, every
    problem with it, and each run its judge lines named, as the gate log
    keeps it."""

    sheet: Read
    problems: tuple[str, ...]
    runs: tuple[dict[str, Any], ...] = ()
    #: the answer as the store keeps it: the session's own sheet, or its
    #: lines by block written as the sheet's (`review_blocks.translated`)
    #: and any that did not translate after it, as written
    text: str = ""


def checked(
    spec: Mapping[str, Any],
    edited: str,
    beside: Path | None = None,
    within: float | None = None,
) -> Checked:
    """The sheet's check, the gate's and the runner's alike: its lines read
    (`read`), its runs made (`run_problems`, within `within` seconds), its
    fetched works' pages where its tool fetched them
    (`cached_page_problems`), the pages its lines name of a work not laid
    in, read on demand (`unlaid_page_problems`), its edge lines against the
    state's edges (`edge_problems`, `beside` the gate's directory). The runner's check
    builds the records after it; a refusal there that this check does not
    make is a defect (docs/architecture.md § 7). The runner's check returns
    this check's problems before it builds a record, so a rule here
    stricter than the records' voids an answer the records would take:
    refuse here only what they refuse. A session that answers by block
    (`Column.by_block`) has its lines written as the sheet's first, and
    each problem told by its own line."""
    by_block = _answers_by_block(spec, edited)
    blocks = review_blocks.translated(spec, edited) if by_block else None
    sheet = read(spec, edited if blocks is None else blocks.sheet)
    ran, runs = run_problems(spec, sheet, within)
    fetched = cached_page_problems(spec, sheet)
    unlaid = unlaid_page_problems(spec, sheet)
    edges = edge_problems(spec, sheet, beside)
    problems = (*sheet.problems, *ran, *fetched, *unlaid, *edges)
    if blocks is not None:
        problems = (
            *blocks.problems,
            *(review_blocks.told(spec, blocks, p) for p in problems),
        )
    return Checked(
        sheet, problems, tuple(runs), edited if blocks is None else blocks.kept
    )


def _answers_by_block(spec: Mapping[str, Any], edited: str) -> bool:
    """Whether `edited` is lines by block to write as the sheet's: the
    spec's session answers by block, and the text is not its sheet already
    (a kept answer, checked again, is the sheet it was read as)."""
    return review_blocks.SUBJECTS in spec and not edited.startswith(SHEET_TITLE)


def sheet_text(spec: Mapping[str, Any], edited: str) -> str:
    """The sheet an answer is read as: the session's own, or its lines by
    block written as the sheet's."""
    if not _answers_by_block(spec, edited):
        return edited
    return review_blocks.translated(spec, edited).sheet


# --- the gate ----------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    """The session's gate, as Claude Code's Stop hook: `<spec> <edited>`
    when the session tries to end (a session by block checks its file
    itself as it writes, by its tool server's `check`). Nothing when the
    sheet reads, else Claude Code's `block` decision with its problems, at
    most `gates.ROUNDS` times. It never fails the session: a gate that
    cannot run lets it go on, and the runner's check decides."""
    gates.hook_input()
    args = list(argv if argv is not None else sys.argv[1:])
    try:
        spec_path, edited_path = Path(args[0]), Path(args[1])
        edited = gates.read_regular(edited_path)
        spec = gates.spec(spec_path)
        result = checked(spec, edited, spec_path.parent, GATE_RUN_SECONDS)
        found, runs = list(result.problems), list(result.runs)
    except Exception as error:  # noqa: BLE001 - the gate must never trap a session
        print(f"the review gate could not run: {error}", file=sys.stderr)
        return 0
    blocked = bool(found) and gates.round_left(spec_path)
    entry: dict[str, Any] = {"mode": "stop", "problems": found, "blocked": blocked}
    if runs:
        # the scripts' run time, spent inside the session's
        entry["runs"] = runs
    gates.log(spec_path, entry)
    if blocked:
        gates.block(gates.reason(REVIEW_FILE, found))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
