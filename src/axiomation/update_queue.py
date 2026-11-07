"""An update's edges session as a queue (docs/architecture.md § 2): what
the edit touched, served one block at a time, each answer laid on the
carried sheet by code.

The update's one session (`edges_file.plain_update_question`) is asked
as this queue: its working state is the carried sheet, and once every
task is settled the sheet, every owner done, is the session's answer,
filed under the question's key, so the round, the marks, the reread and
the export read it as any answer. A task is one of five kinds:

- **changed**: a block the edit changed or added, or one its split made:
  its text before and now, its lines, numbered; its Δ and its lines as
  the paper reads now, its roles where it was re-cut, a split where it
  now holds two units;
- **moved**: a block whose meaning a Δ moved by rule (`plain_moved`),
  drawn as the Δ that moves it settles: its Δ, read with the edit;
- **relies**: a block that writes the words of a part an answer added,
  or cites a block for a part that block's edge dropped, with no edge
  onto that part's block (`edges_file.new_words_owners`,
  `dropped_part_owners`): an edge, or nothing;
- **reread**: a block the update reopened (a block about the whole
  paper, a mechanical reopen): its lines read against what changed;
- **mend**: once every other task is settled, a block the check still
  finds a problem of: the draw missed it, and the update names it.

The queue stands on its question (`queue.Asked`), not on a kept state:
no state holds NEW until the update keeps it. NEW is served from the
carried sheet, the paper beside it as `paper.md`, so the workflow gives
its own read (`block`) in place of the core's. What the factory needs
besides the store lies in the run's directory (`SPEC_FILE`), written
before the run by `ask`: the question's spec, and how the paper was read.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Collection, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from axiomation import queue
from axiomation.exchange import vocabulary_of, vocabulary_payload
from axiomation.questions import (
    Question,
    Stage,
    Template,
    edges_file,
    load_template,
    review_blocks,
)
from axiomation.questions import edge_sheet as es
from axiomation.questions.cut_file import RenderError
from axiomation.queue import Accepted, Read, Refused, Serving, Task, Tool, Working
from axiomation.reader import Document, read_paper
from axiomation.records import RecordError, RecordStore
from axiomation.resolver import Attempt
from axiomation.runner import Runner, RunReport, queued
from axiomation.runner.hosts import Host
from axiomation.runner.queued import QueueReport, run_queue

TEMPLATE = "edges-update-queue"
KIND = "edges-update-task"
FACTORY = "axiomation.update_queue:updating"
KICKOFF = queue.KICKOFF
#: What the queue costs where the store holds none of its sessions: Opus
#: at effort high, about $0.64 for one session of 25 tasks: its first call
#: about $0.07 (the brief written once), the rest over the tasks. Fitted
#: where every task was a first one (`update.queue_tasks`): no split, and
#: no block moved by rule asked; a run with either costs more than its dry
#: run says.
FITTED = queued.Fitted("claude-opus-5-5", start=0.07, task=0.023)
#: The host the queue's filed answer names: its sessions are the hosts'.
HOST = "queue"
#: What `ask` writes in the run's directory for the factory: the update
#: question's gate spec, the owners to read again with why, and the brief.
SPEC_FILE = "update.json"
#: The roles line of a re-cut block, as a task's answer lays it.
ROLES = "roles: "
#: A text a task's served block shows at most of a block beside it.
BESIDE_CHARACTERS = 600
#: What a block's later task is named by, with its count (`h#2`, `h#3`):
#: moved by rule after it was read, or opened again by its own answer's
#: split.
ASKED_AGAIN = "#"
#: The characters of a deleted block's text found in a changed block's
#: that say it was merged there.
MERGED_HEAD = 80
#: The blocks relying on a task's block it names at most.
RELYING_SHOWN = 12


class Kind(StrEnum):
    """What a task asks of its block."""

    CHANGED = "changed"
    MOVED = "moved"
    RELIES = "relies"
    REREAD = "reread"
    #: a block the check still finds a problem of once every other task is
    #: settled: handed on at the end, as an answer's are while the run goes
    MEND = "mend"


class UpdateQueueError(ValueError):
    """An answer that cannot be laid on the sheet, with why."""


# --- the tools -----------------------------------------------------------------------

_DELTA = {
    "type": "object",
    "required": ["class", "note"],
    "properties": {
        "class": {"type": "string", "enum": list(es.IMPACT_CLASSES)},
        "note": {"type": "string"},
    },
}
ANSWER = Tool(
    "answer",
    "Answer the task: the lines you take off, by number, and those you put "
    "on; its Δ where the task asks one; a re-cut block's roles. Checked at "
    "once; accepted, it returns the next task.",
    {
        "type": "object",
        "required": ["task"],
        "properties": {
            "task": {"type": "string"},
            "delta": {
                **_DELTA,
                "description": "how the edit changed what the block says, for the "
                "block whole",
            },
            "part_deltas": {
                "type": "array",
                "description": "a part's Δ, where its parts changed differently: "
                "`part` is the label the legend gives it (`model` for `[model]`), "
                "not a role",
                "items": {
                    **_DELTA,
                    "required": ["part", "class", "note"],
                    "properties": {**_DELTA["properties"], "part": {"type": "string"}},
                },
            },
            "take_off": {
                "type": "array",
                "items": {"type": "string"},
                "description": "the numbers of the block's lines that no longer "
                "read true (L1, L2, …)",
            },
            "put_on": {
                "type": "array",
                "items": {"type": "string"},
                "description": "each line to put on the block, as the sheet writes "
                "it: `→ …`, `⊢ …`, `? …`, or on a changed block `✂ …`",
            },
            "parts": {
                "type": "array",
                "description": "a part of the task's block to add to the legend, "
                "where a line you put on names it: its `label` and its `words`, "
                "quoted from the block as they stand",
                "items": {
                    "type": "object",
                    "required": ["label", "words"],
                    "properties": {
                        "label": {"type": "string"},
                        "words": {"type": "string"},
                    },
                },
            },
            "drop_parts": {
                "type": "array",
                "items": {"type": "string"},
                "description": "the labels of the block's parts whose words the "
                "edit removed",
            },
            "roles": {
                "type": "string",
                "description": "a re-cut block only: its first part's role, then "
                "each later part's with the words it starts at, `justification | "
                'claim "<words>"`; one role where it has one',
            },
        },
    },
)


def _on_handle(name: str, description: str) -> Tool:
    return Tool(
        name,
        description,
        {
            "type": "object",
            "required": ["handle"],
            "properties": {"handle": {"type": "string"}},
        },
    )


BLOCK = _on_handle(
    "block",
    "A block's text before the edit and now, and its lines as they stand in "
    "this run, by its handle.",
)
#: The paper as NEW reads, each block under its handle, read only.
PAPER_FILE = "paper.md"
# --- the sheet as a task reads it ----------------------------------------------------


def _undone(sheet: str) -> set[str]:
    """The blocks a sheet shows without a done mark."""
    parsed = es.parse(sheet)
    return {h for h in parsed.texts if h not in parsed.done}


class _Sheets:
    """The working sheet parsed and checked, the last one kept: a task is
    rendered and answered on the sheet it was drawn on."""

    def __init__(self) -> None:
        self._parsed: tuple[str, es.Sheet] | None = None
        self._shown: es.Sheet | None = None
        self._problems: tuple[str, Counter[str]] | None = None

    def shown(self, text: str) -> es.Sheet:
        """The sheet the queue was shown, parsed once."""
        if self._shown is None:
            self._shown = es.parse(text)
        return self._shown

    def parsed(self, text: str) -> es.Sheet:
        if self._parsed is None or self._parsed[0] is not text:
            self._parsed = (text, es.parse(text))
        return self._parsed[1]

    def problems(
        self, text: str, spec: dict[str, Any], document: Document
    ) -> Counter[str]:
        if self._problems is None or self._problems[0] is not text:
            found = edges_file.spec_problems(spec, text, document)
            self._problems = (text, Counter(_unnumbered(p) for p in found))
        return self._problems[1]


_LINE_NUMBER = re.compile(r"^line \d+: ")


def _unnumbered(problem: str) -> str:
    """A problem as compared across two sheets: without the line number."""
    return _LINE_NUMBER.sub("", problem)


def numbered_lines(sheet: str, handle: str) -> list[str]:
    """A block's or group's lines as the sheet holds them, its done mark
    left out: what a task numbers."""
    return [
        line
        for line in edges_file.written_lines(sheet, flags=True).get(handle, [])
        if not es.DONE.match(line)
    ]


def block_line(sheet: str, handle: str) -> str | None:
    head = f"⟦{handle} "
    return next((x for x in sheet.split("\n") if x.startswith(head)), None)


def paper_of(sheet: str) -> str:
    """The paper as the sheet holds it, each block under its handle
    (`review_blocks.plain`), no line of the graph left: no flag, cut, Δ,
    condition or stale mark line."""
    return "\n".join(
        line
        for line in review_blocks.plain(sheet).split("\n")
        if not line.startswith(_GRAPH_LINES)
    )


#: The sheet's lines that are its graph's, not the paper's.
_GRAPH_LINES = (
    f"{es.FLAG_MARK} ",
    es.CUT_MARK,
    f"{es.DELTA_MARK} ",
    es.WAS,
    "⟦asked if ",
    "⟦meaning moved by rule: ",
    "⟦stale since ",
    edges_file.APPLIED_MARK,
)


def with_done(sheet: str, handle: str) -> str:
    """`sheet` with `handle`'s done mark after its lines, where it has
    none."""
    if f"⟦done {handle}⟧" in sheet.split("\n"):
        return sheet
    return edges_file.with_lines(sheet, {handle: [f"⟦done {handle}⟧"]})


def without_done(sheet: str, handles: Collection[str]) -> str:
    """`sheet` with the done marks of `handles` taken off."""
    return "\n".join(
        line
        for line in sheet.split("\n")
        if not ((m := es.DONE.match(line)) and m.group(1) in handles)
    )


def with_roles(sheet: str, handle: str, roles: str) -> str:
    """`sheet` with a re-cut block's roles written: the first part's on its
    block line, each later part's `⟦+ <role>⟧` before the words it starts
    at. Refused where a role is unknown or the words are not in one line
    of its text."""
    parts = [p.strip() for p in roles.split("|")]
    first, *later = parts
    known = {*es.ROLES, es.NONE}
    named: list[str] = [first]
    starts: list[tuple[str, str]] = []
    for part in later:
        m = re.fullmatch(r'(\w+)\s+"(.+)"', part)
        if m is None:
            raise UpdateQueueError(
                f'roles: a later part is `<role> "<words it starts at>"`: {part!r}'
            )
        named.append(m.group(1))
        starts.append((m.group(1), m.group(2)))
    wrong = [r for r in named if r not in known]
    if wrong:
        raise UpdateQueueError(
            f"roles: {wrong[0]!r} is not a role: one of {', '.join(es.ROLES)}, "
            f"or {es.NONE}"
        )
    lines = sheet.split("\n")
    at = next((n for n, x in enumerate(lines) if x.startswith(f"⟦{handle} ")), None)
    if at is None:
        raise UpdateQueueError(f"⟦{handle}⟧ is no block of the sheet")
    m = es.BLOCK.match(lines[at])
    assert m is not None
    lines[at] = (
        f"⟦{handle} {'+'.join(dict.fromkeys(named))}⟧ {m.group(3)} ({m.group(4)})"
    )
    end = at + 1
    while end < len(lines) and not (
        es.BLOCK.match(lines[end])
        or es.GROUP.match(lines[end])
        or es.END.match(lines[end])
        or lines[end].startswith(edges_file.AFTER_TEXT)
    ):
        end += 1
    # the roles are written whole: a marker the text shows gives way
    text = es.SEGMENT.sub("", "\n".join(lines[at + 1 : end]))
    for role, words in starts:
        pattern = r"\s+".join(re.escape(w) for w in words.split())
        hits = list(re.finditer(pattern, text)) if pattern else []
        if len(hits) != 1:
            raise UpdateQueueError(
                f'roles: "{words}" is not in ⟦{handle}⟧\'s text: quote the words its '
                "part starts at, as they stand"
                if not hits
                else f'roles: "{words}" stands {len(hits)} times in ⟦{handle}⟧\'s '
                "text: quote enough words to place the part once"
            )
        hit = hits[0]
        text = f"{text[: hit.start()]}⟦+ {role}⟧ {text[hit.start() :]}"
    lines[at + 1 : end] = text.split("\n")
    return "\n".join(lines)


# --- the workflow ---------------------------------------------------------------------


class Updating:
    """The update's question as a queue: a task a block the edit touched;
    one step, the edges writer's."""

    restating = False
    by_graph = False

    def __init__(
        self,
        anchor: str,
        files: Path,
        asked: Mapping[str, Any],
        document: Document,
    ) -> None:
        #: no kept state: the queue answers its question, `anchor`
        self.view = None
        self.anchor = anchor
        self.files = files
        self.spec: dict[str, Any] = dict(asked["spec"])
        self.sheet: str = self.spec["shown"]
        self.document = document
        #: each owner to read again: why, and the block whose edit opened it
        self.reread: dict[str, dict[str, str | None]] = dict(asked["reread"])
        self.added = frozenset(asked.get("added", ()))
        self.meanings: dict[str, str] = dict(asked.get("meanings") or {})
        self.vocabulary: tuple[str, ...] = tuple(asked.get("vocabulary") or ())
        self.hints: dict[str, list[list[str]]] = dict(asked.get("hints") or {})
        self.lost: dict[str, list[list[str]]] = dict(asked.get("lost") or {})
        #: each changed block's text before the edit, by handle; each deleted
        #: block's, by its `deleted:` name; the rule's Δ on each of those
        self.was: dict[str, list[str]] = dict(asked["was"])
        self.gone: dict[str, str] = dict(asked["gone"])
        self.by_rule = {
            h: c for h, e, c in self.spec[edges_file.PLAIN]["rule_deltas"] if not e
        }
        self.sheets = _Sheets()
        writer = Template(asked["writer"]["name"], asked["writer"]["text"])
        self.steps = (Writer(self, brief(writer)),)
        self.reads = (
            Read(BLOCK, lambda w, a: self.read_block(w, str(a.get("handle", "")))),
        )
        # the paper as NEW reads, laid in the session's folder; the core's
        # reads would read OLD's kept state, so only its `block` is given,
        # and that one by this workflow. Laid once, it shows a block a cut
        # line splits as it was before: the pieces' tasks serve their own
        # text, and `block` reads them on the working sheet
        self.serving = Serving(
            files={PAPER_FILE: paper_of(self.sheet)},
        )

    # what the tasks are

    def plain(self, sheet: str) -> edges_file.PlainView:
        return edges_file.plain_view(self.spec, sheet, self.document)

    def draw(self, working: Working) -> list[Task]:
        """One task a block, in the paper's order within each kind: the
        changed blocks (each owed one, or a block a split made of it) and
        the added ones (shown without a done mark, owing no Δ); then each
        block the settled Δs move by rule and that owes its Δ; then the
        owners to read again, but one already served as changed or moved."""
        sheet = working.sheet
        view = self.plain(sheet)
        parsed = self.sheets.parsed(sheet)
        found: dict[str, Task] = {}
        served = working.ids

        # a task is named by its block's handle, as the session writes it; a
        # block asked again (moved by rule after it was read, or opened again
        # by its own answer's split) is named a second time
        def add(kind: Kind, h: str, **more: str) -> None:
            if h not in view.handles or h in found:
                return
            name = h
            if h in served:
                if kind not in (Kind.MOVED, Kind.MEND) and h in parsed.done:
                    return
                asked = sum(x.task.subject["handle"] == h for x in working.settled)
                name = f"{h}{ASKED_AGAIN}{asked + 1}"
            found[h] = Task(name, view.handles[h], {"kind": kind, "handle": h, **more})

        for handles in view.owed.values():
            for h in handles:
                add(Kind.CHANGED, h)
        # a block shown without its done mark is written where the edit added
        # it or a correction made it, else read again (a block about the
        # whole paper, one whose edge lost its target, a group's member)
        undone = [h for h in parsed.texts if h not in parsed.done]
        for h in undone:
            if h in self.added or h in view.roles_open:
                add(Kind.CHANGED, h)
        for h in edges_file.plain_moved(self.spec, view, parsed).owed:
            add(Kind.MOVED, h)
        # what the settled answers reveal beyond the tasks: a block writing a
        # new part's words, or citing a block for a part it dropped, with no
        # edge onto that part's block
        opened = {
            *found,
            *self.reread,
            *(s.task.subject["handle"] for s in working.settled),
        }
        shown = self.sheets.shown(self.sheet)

        def closed(h: str) -> bool:
            return h not in opened

        revealed = edges_file.dropped_part_owners(shown, parsed, opened, closed)
        revealed |= edges_file.new_words_owners(shown, parsed, closed)
        for h, (why, source) in revealed.items():
            add(Kind.RELIES, h, why=why, source=source)
        for h in [*self.reread, *undone]:
            if h not in served or h not in parsed.done:
                add(Kind.REREAD, h)
        if not found:
            # every task settled: what the check still finds is handed on to
            # the block it names, as an answer's problems are while the run
            # goes, so the session's answer is never refused at its end; a
            # problem that names no block is the answer's refusal
            for p in sorted(self.sheets.problems(sheet, self.spec, self.document)):
                for h in view.handles:
                    if _names(p, h):
                        add(Kind.MEND, h)
        return list(found.values())

    # a task as served

    def render(self, task: Task, working: Working) -> str:
        sheet = working.sheet
        parsed = self.sheets.parsed(sheet)
        h = str(task.subject["handle"])
        kind = Kind(task.subject["kind"])
        view = self.plain(sheet)
        out: list[str] = []
        if kind is Kind.CHANGED:
            owed = any(h in hs for hs in view.owed.values())
            out.append(
                "a changed block: write its Δ and its lines as the paper reads now"
                if owed
                else "an added block: write its lines"
            )
            if h in view.roles_open:
                out.append(
                    "it was re-cut: where it now holds two units (an example and "
                    "the claim it leads to, a definition inside a claim), put on a "
                    "`✂ split` line; else its roles are open: give them in `roles`"
                )
        elif kind is Kind.RELIES:
            out.append(
                "does it rely on what the edit wrote? "
                f"{task.subject.get('why', '')}: put on the edge its text shows, "
                "or answer with nothing where it relies on none"
            )
        elif kind is Kind.MOVED:
            out.append(
                "a block whose meaning moved by rule: read it with the edit and "
                "write its Δ"
            )
        elif kind is Kind.MEND:
            out.append(
                "the check still finds a problem on this block, every other task "
                "settled: mend it, as the problems below say"
            )
        else:
            why = self.reread.get(h) or {}
            out.append(
                f"read its lines again: {why.get('why') or 'the edit may touch it'}"
            )
        out += self._shown(sheet, parsed, h, with_was=True)
        for line, deleted in self.lost.get(h, ()):
            out.append(
                f"a line it lost onto ⟦{deleted}⟧, which the edit deleted: `{line}`; "
                "put it on again onto the block that now says what it pointed at, "
                "or leave it gone"
            )
        for quoted, words, block in self.hints.get(h, ()):
            out.append(
                f'a line quoted "{quoted}", which this version no longer holds; '
                f'now perhaps "{words}" in ⟦{block}⟧'
            )
        if kind is Kind.CHANGED:
            merged = self.merged_into(parsed.texts.get(h, ""))
            if merged:
                out.append(
                    "its text now holds the words of these blocks of the old "
                    "version, which no longer stand as blocks of their own:"
                )
                for g in merged:
                    out += [f"⟦{g}⟧", f"> {self.gone[g]}"]
        origin = (
            (self.reread.get(h) or {}).get("origin") if kind is Kind.REREAD else None
        )
        if origin and origin != h:
            out.append("the edit there:")
            out += self._shown(sheet, parsed, origin, with_was=True, lines=False)
        if kind is Kind.RELIES and task.subject.get("source"):
            out.append("the block it may rely on:")
            out += self._shown(
                sheet, parsed, str(task.subject["source"]), with_was=True, lines=False
            )
        if kind is Kind.MOVED:
            impact = edges_file.plain_moved(self.spec, view, parsed).owed.get(h)
            if impact is not None:
                named = f" [{', '.join(impact.parts)}]" if impact.parts else ""
                out.append(
                    f"moved by its {impact.via.kind} of ⟦{impact.handle}⟧{named}, "
                    f"whose Δ is {self._delta_of(parsed, impact.handle)}:"
                )
                out += self._shown(
                    sheet, parsed, impact.handle, with_was=True, lines=False
                )
        broken = sorted(
            p
            for p in self.sheets.problems(sheet, self.spec, self.document)
            if _names(p, h)
            and not p.startswith((f"not done: {h}", f"{edges_file.OWED}{h})"))
        )
        if broken:
            out.append("the check finds on its lines now (mend them in your answer):")
            out += [f"- {p}" for p in broken]
        qualifiers = [
            e.source for e in parsed.edges if e.kind == "qualifies" and e.target == h
        ]
        for q in dict.fromkeys(qualifiers):
            out.append(f"qualified by ⟦{q}⟧:")
            out.append(_short(parsed.texts.get(q, "")))
        relying = [
            f"⟦{e.source}⟧ {e.kind}" + (f" [{', '.join(e.parts)}]" if e.parts else "")
            for e in parsed.edges
            if e.target == h and e.source != h
        ]
        relying = list(dict.fromkeys(relying))
        if relying:
            more = len(relying) - RELYING_SHOWN
            out.append(
                "relied on by: "
                + "; ".join(relying[:RELYING_SHOWN])
                + (f"; and {more} more" if more > 0 else "")
            )
        others = [x for hs in view.owed.values() for x in hs if x != h]
        if others:
            out.append(
                "the edit's other changed blocks: "
                + ", ".join(f"⟦{x}⟧ (Δ {self._delta_of(parsed, x)})" for x in others)
            )
        return "\n".join(out) + "\n"

    def _shown(
        self,
        sheet: str,
        parsed: es.Sheet,
        h: str,
        with_was: bool = False,
        lines: bool = True,
    ) -> list[str]:
        if h in self.gone:
            return [f"⟦{h}⟧ deleted by the edit; its text was:", f"> {self.gone[h]}"]
        out = [block_line(sheet, h) or f"⟦{h}⟧"]
        was = self.was.get(h) or parsed.was.get(h)
        if with_was and was:
            out.append("before:")
            out += [f"> {t}" for t in was]
            out.append("now:")
        out.append(f"> {parsed.texts.get(h, '')}")
        if h in self.meanings:
            out.append(f"⟦{h}⟧: {self.meanings[h]}")
        anew = [n for n in self.vocabulary if _uses(parsed.texts.get(h, ""), n)]
        if anew:
            out.append(
                f"⟦{h}⟧ uses names the vocabulary map now reads anew, so it is "
                "re-cut and changed though its words may be the same: "
                + ", ".join(anew)
            )
        parts = [(label, w) for (t, label), w in parsed.legend.items() if t == h]
        if parts:
            out.append("its parts: " + "; ".join(f'[{a}] "{w}"' for a, w in parts))
        if lines:
            own = numbered_lines(sheet, h)
            out.append("its lines:" if own else "its lines: none")
            out += [f"L{n} {x}" for n, x in enumerate(own, 1)]
        return out

    def merged_into(self, text: str) -> list[str]:
        """The deleted blocks whose words `text` now holds: a block the
        edit merged into it, or one the cut now reads inside it."""
        held = _bare(text)
        return [
            g for g, words in self.gone.items() if _bare(words)[:MERGED_HEAD] in held
        ]

    def _delta_of(self, parsed: es.Sheet, h: str) -> str:
        if h in self.by_rule:
            return f"{self.by_rule[h]} (by rule)"
        whole = [d for d in parsed.deltas if d.source == h and not d.label]
        return f"{whole[0].cls} — {whole[0].note}" if whole else "not yet written"

    # the reads

    def read_block(self, working: Working, ref: str) -> str:
        h = ref.strip().strip("⟦⟧").strip()
        parsed = self.sheets.parsed(working.sheet)
        if h in self.gone:
            return "\n".join(self._shown(working.sheet, parsed, h))
        if h not in parsed.texts:
            raise queue.ReadError(f"{ref!r} is no block's handle")
        return "\n".join(self._shown(working.sheet, parsed, h, with_was=True))

    # an answer

    def answered(
        self,
        task: Task,
        tool: str,
        args: Mapping[str, Any],
        working: Working,
        check: bool,
    ) -> Accepted | Refused:
        h = str(task.subject["handle"])
        kind = Kind(task.subject["kind"])
        try:
            lines = self._lines(task, kind, h, args, working.sheet)
            after = self.apply(working.sheet, lines)
        except KeyError as error:
            return Refused((f"no block or part {error} on the sheet",))
        except (
            UpdateQueueError,
            edges_file.FragmentError,
            RecordError,
            RenderError,
            ValueError,
        ) as error:
            return Refused((str(error),))
        if not check:
            return Accepted(lines)
        before = self.sheets.problems(working.sheet, self.spec, self.document)
        now = Counter(
            _unnumbered(p)
            for p in edges_file.spec_problems(self.spec, after, self.document)
        )
        # what the answer adds anywhere, and whatever still names its own
        # block: the lines the edit broke are the task's to mend; another
        # block's Δ owed or done mark is that block's task, and so is every
        # problem of a block this answer's split made or opened again
        # (`apply` marks the block done; only a split leaves it open)
        undone = _undone(after)
        opened = undone - _undone(working.sheet)
        handed = {h, *opened} if h in undone else opened

        def theirs(p: str) -> bool:
            return _handed_on(p) or any(_names(p, x) for x in handed)

        added = now - before
        found = sorted(
            p for p in added.elements() if not _names(p, h) and not theirs(p)
        )
        if h not in handed:
            found += sorted(p for p in now if _names(p, h))
        return Refused(tuple(found)) if found else Accepted(lines)

    def _lines(
        self,
        task: Task,
        kind: Kind,
        h: str,
        args: Mapping[str, Any],
        sheet: str,
    ) -> str:
        """The answer in this workflow's notation: `@@ <handle>`, its roles
        line where given, then its changes as a later pass writes them."""
        own = numbered_lines(sheet, h)
        taken: list[str] = []
        for ref in args.get("take_off") or []:
            n = str(ref).strip().upper().removeprefix("L")
            if not n.isdigit() or not 1 <= int(n) <= len(own):
                raise UpdateQueueError(
                    f"take_off {ref!r}: the block's lines are L1 to L{len(own)}"
                    if own
                    else f"take_off {ref!r}: the block has no line"
                )
            taken.append(own[int(n) - 1])
        put = [" ".join(str(x).split()) for x in args.get("put_on") or []]
        for line in put:
            cut = line.startswith(es.CUT_MARK)
            if cut and kind is not Kind.CHANGED:
                raise UpdateQueueError(
                    f"put_on {line!r}: a cut line stands on a changed block only"
                )
            if not (
                es.EDGE.match(line)
                or es.SUPPORT.match(line)
                or (cut and es.CUT.match(line))
                or es.FLAG_LINE.match(line)
            ):
                raise UpdateQueueError(
                    f"put_on {line!r}: a line as the sheet writes it: `→ …`, `⊢ …`, "
                    f"`? …`, or `{es.CUT_FORM}`"
                )
        deltas = []
        given = args.get("delta")
        parts = args.get("part_deltas") or []
        if kind in (Kind.REREAD, Kind.RELIES) and (given or parts):
            raise UpdateQueueError(
                "a block read again takes no Δ: its text did not change, and no Δ "
                "moved it"
            )
        if given:
            deltas.append(es.delta_line("", str(given.get("class", "")), _one(given)))
        for p in parts:
            deltas.append(
                es.delta_line(
                    str(p.get("part", "")).strip("[] "),
                    str(p.get("class", "")),
                    _one(p),
                )
            )
        roles = str(args.get("roles") or "").strip()
        if roles and h not in self.plain(sheet).roles_open:
            raise UpdateQueueError(
                f"roles: ⟦{h}⟧'s roles are fixed; only a re-cut block's are open"
            )
        body = self._legend(h, args, sheet)
        body.append(f"@@ {h}")
        if roles:
            body.append(ROLES + roles)
        body += [f"- {x}" for x in dict.fromkeys(taken)]
        body += [f"+ {x}" for x in [*deltas, *put]]
        return "\n".join(body)

    def _legend(self, h: str, args: Mapping[str, Any], sheet: str) -> list[str]:
        """The answer's parts of its block added and dropped, as the header's
        changes (`@@ the header`); none, nothing."""
        held = {
            label: line
            for line in sheet.split(f"\n{es.RULE}\n", 1)[0].split("\n")
            if (m := es.LEGEND.match(line)) and m.group(1) == h
            for label in [m.group(2)]
        }
        found: list[str] = []
        for part in args.get("parts") or []:
            label = str(part.get("label") or "").strip("[] ")
            words = " ".join(str(part.get("words") or "").split())
            if not label or not words:
                raise UpdateQueueError(
                    "parts: each part gives its `label` and its `words`, quoted from "
                    "the block as they stand"
                )
            found.append(f'+ {h} [{label}]: "{words}"')
        for label in args.get("drop_parts") or []:
            label = str(label).strip("[] ")
            if label not in held:
                raise UpdateQueueError(
                    f"drop_parts: ⟦{h}⟧ has no part [{label}]"
                    + (f"; its parts: {', '.join(sorted(held))}" if held else "")
                )
            found.append(f"- {held[label].strip()}")
        return [f"@@ {edges_file.LINES_HEADER}", *found] if found else []

    def apply(self, sheet: str, lines: str) -> str:
        """`lines`, one task's answer, laid on `sheet`: its roles, its
        changes (its block's parts among them), its done mark; a cut line it
        wrote then applied with every other (`plain_cut_applied`), the
        blocks still waiting for their tasks kept open."""
        rows = lines.split("\n")
        h = next(
            r.removeprefix("@@").strip()
            for r in rows
            if r.startswith("@@")
            and r.removeprefix("@@").strip() != edges_file.LINES_HEADER
        )
        changes = [r for r in rows if not r.startswith(ROLES)]
        roles = next((r.removeprefix(ROLES) for r in rows if r.startswith(ROLES)), "")
        if roles:
            sheet = with_roles(sheet, h, roles)
        if any(not r.startswith("@@") for r in changes):
            sheet = edges_file.applied_changes(
                sheet, "\n".join([*changes, edges_file.DONE_MARK])
            )
        sheet = with_done(sheet, h)
        if any(r.startswith(f"+ {es.CUT_MARK}") for r in changes):
            waiting = self._open_ids(sheet)
            applied = edges_file.plain_cut_applied(self.spec, sheet, self.document)
            if applied is not None:
                if applied.sheet is None:
                    raise UpdateQueueError(applied.message)
                # carried onto the new cut, every block but the split's
                # pieces comes back done; the blocks still waiting for their
                # tasks stay open
                handles = self.plain(applied.sheet).handles
                sheet = without_done(
                    applied.sheet, {x for x, i in handles.items() if i in waiting}
                )
        return sheet

    def _open_ids(self, sheet: str) -> set[str]:
        """The ids of the blocks `sheet` shows without a done mark."""
        parsed, handles = es.parse(sheet), self.plain(sheet).handles
        return {
            handles[x] for x in parsed.texts if x not in parsed.done and x in handles
        }


def _names(problem: str, handle: str) -> bool:
    """Whether a check's problem is about `handle`'s own lines."""
    return (
        problem.startswith((f"{handle} ", f"{handle}: "))
        or problem.endswith(f": {handle}")
        or problem.startswith(f"{edges_file.OWED}{handle})")
    )


def _uses(text: str, name: str) -> bool:
    """Whether a block's text uses a name as an environment or a command."""
    return (
        f"{{{name}}}" in text
        or re.search(rf"\\{re.escape(name)}(?![A-Za-z])", text) is not None
    )


def _bare(text: str) -> str:
    """A text as compared for its words: no marker, brace or run of
    spaces."""
    return " ".join(es.SEGMENT.sub("", text).replace("{", "").replace("}", "").split())


def _handed_on(problem: str) -> bool:
    """Whether a check's problem is a task the queue serves next: a Δ owed
    on a block the answer moved by rule, or a block not yet done."""
    return problem.startswith((edges_file.OWED, "not done: "))


def _one(delta: Mapping[str, Any]) -> str:
    note = " ".join(str(delta.get("note") or "").split())
    if not note:
        raise UpdateQueueError("a Δ gives its note: what changed, in a few words")
    return note


def _short(text: str) -> str:
    return f"> {text[:BESIDE_CHARACTERS]}" + (
        "…" if len(text) > BESIDE_CHARACTERS else ""
    )


class Writer:
    """The queue's one step: a task served, answered with `answer`."""

    kind = KIND
    stage = Stage.EDGES
    kickoff = KICKOFF
    tools = (ANSWER,)

    def __init__(self, updating: Updating, template: Template) -> None:
        self.updating = updating
        self.template = template

    def render(self, task: Task, working: Working) -> str:
        return self.updating.render(task, working)

    def answer(
        self,
        task: Task,
        tool: str,
        args: Mapping[str, Any],
        working: Working,
        check: bool,
    ) -> Accepted | Refused:
        return self.updating.answered(task, tool, args, working, check)


def brief(writer: Template) -> Template:
    """The step's brief: the edges writer's (the lines' notation), then the
    queue's; one text, so its version binds both."""
    own = load_template(TEMPLATE)
    return Template(TEMPLATE, f"{writer.text}\n\n{own.text}")


# --- the run --------------------------------------------------------------------------


def price(store: RecordStore, model: str) -> queued.Price:
    """What the queue is expected to cost on `model`: its session's start
    and a task's (`runner.queued.price`)."""
    try:
        return queued.price(store, TEMPLATE, model, FITTED)
    except queued.PriceError as error:
        raise UpdateQueueError(str(error)) from None


def run_of(store: RecordStore, question: Question) -> queue.Run:
    """The queue run that answers `question`: on the question itself, since
    no kept state holds NEW until the update keeps it."""
    return queue.Run(store, queue.Asked(question.key), FACTORY)


@dataclass(frozen=True, slots=True)
class Shown:
    """What the queue shows beside the carried sheet: each owner to read
    again with why and the block whose edit opened it (by handle, in the
    paper's order), each changed block's text before the edit, each deleted
    block's text by its `deleted:` name, and the blocks the edit added."""

    reread: Mapping[str, Mapping[str, str | None]]
    was: Mapping[str, Sequence[str]]
    gone: Mapping[str, str]
    added: Sequence[str] = ()
    #: each block whose statement's meaning moved, with the sentence that
    #: says so, by handle; and the names the vocabulary map reads anew
    meanings: Mapping[str, str] = field(default_factory=dict)
    vocabulary: Sequence[str] = ()
    #: each block a line quoted words of that this version no longer
    #: holds, by handle: those words, and where similar words now stand
    #: (the words, the block's handle)
    hints: Mapping[str, Sequence[tuple[str, str, str]]] = field(default_factory=dict)
    #: each block's edge lines lost onto a block the edit deleted, by
    #: handle: the line, and the deleted block by its `deleted:` name
    lost: Mapping[str, Sequence[tuple[str, str]]] = field(default_factory=dict)


def write_asked(
    run: queue.Run,
    question: Question,
    document: Document,
    shown: Shown,
    writer: Template,
) -> None:
    """What the factory reads, in the run's directory: the question's spec,
    how the paper was read (its root and its vocabulary map), what is shown
    beside the sheet, the writer's brief."""
    assert question.workspace is not None and question.workspace.gate is not None
    asked = {
        "question": question.key,
        "spec": json.loads(question.workspace.gate.spec),
        "root": document.root,
        "document": vocabulary_payload(document.vocabulary),
        "reread": [[h, dict(r)] for h, r in shown.reread.items()],
        "added": list(shown.added),
        "meanings": dict(shown.meanings),
        "vocabulary": list(shown.vocabulary),
        "hints": {h: [list(x) for x in found] for h, found in shown.hints.items()},
        "lost": {h: [list(x) for x in found] for h, found in shown.lost.items()},
        "was": {h: list(t) for h, t in shown.was.items()},
        "gone": dict(shown.gone),
        "writer": {"name": writer.name, "text": writer.text},
    }
    run.directory.mkdir(parents=True, exist_ok=True)
    (run.directory / SPEC_FILE).write_text(
        json.dumps(asked, ensure_ascii=False, sort_keys=True), encoding="utf-8"
    )


@contextmanager
def updating(
    store: RecordStore, at: queue.Anchor, options: Mapping[str, Any]
) -> Iterator[Updating]:
    """The update's queue on its question (`queue.Run.factory`), as
    `write_asked` left it in the run's directory: the paper read as the
    update read it."""
    run = queue.Run(store, at, FACTORY, options)
    asked = json.loads((run.directory / SPEC_FILE).read_text(encoding="utf-8"))
    paper = asked["spec"][edges_file.PLAIN]["paper"]
    if paper is None:
        raise UpdateQueueError("the update's queue reads the paper: none was given")
    files = Path(paper)
    document = read_paper(files, Path(asked["root"]), vocabulary_of(asked))
    yield Updating(at.id, files, asked, document)


@dataclass(frozen=True, slots=True)
class Seats:
    """Who answers the queue: its step's host and the context its
    sessions are compacted at."""

    hosts: tuple[Host, ...]
    context_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class Done:
    """A queue run's end: its report (None: asked of no host), the
    sheet it settled once every task is, filed as the question's answer,
    or the problems that answer was refused with, and the tasks left."""

    report: QueueReport | None
    open: int = 0
    filed: str | None = None
    problems: tuple[str, ...] = ()
    #: the blocks the check found a problem of at the end, served again
    #: (`Kind.MEND`): each a block the draw missed
    mended: tuple[str, ...] = ()


def ask(
    question: Question,
    document: Document,
    store: RecordStore,
    shown: Shown,
    writer: Template,
    runner: Runner | None,
    hosts: Sequence[Host] = (),
    context_tokens: int | None = None,
) -> Done:
    """`question`, the update's one session, asked as a queue of `hosts`
    through `runner` (None, or no host: what the store holds replayed);
    complete, the working sheet checked as the session's answer is and
    filed under the question's key. A sheet the check refuses is not
    filed: its problems are returned, with the blocks they name."""
    run = run_of(store, question)
    write_asked(run, question, document, shown, writer)
    report = (
        run_queue(runner, run, hosts, None, context_tokens)
        if runner is not None and hosts
        else None
    )
    with queue.opened(run) as workflow:
        working = queue.Working(workflow)
        position, used = queue.advance(
            workflow, working, queue.recorded(store, workflow)
        )
        if position.task is not None:
            return Done(report, queued.open_tasks(workflow, working))
        sheet = working.sheet
    validation = edges_file.check(question, sheet, document)
    if validation.problems:
        named = sorted(
            {
                h
                for h in es.parse(sheet).texts
                for p in validation.problems
                if _names(p, h)
            }
        )
        return Done(
            report,
            problems=(
                *validation.problems,
                "the blocks they name: "
                + (", ".join(f"⟦{h}⟧" for h in named) or "none"),
            ),
        )
    if runner is None:
        return Done(report)
    model = hosts[-1].model if hosts else "replay"
    with runner.resolver.claim():
        recorded = runner.resolver.record(
            Attempt(
                question=question,
                host=HOST,
                model=model,
                text=sheet,
                cost=0.0,
                seconds=0.0,
                validation=validation,
                profile=runner.profile,
                review_round=runner.review_round,
                session=run.sessions,
                tasks=tuple(u.key for u in used if u.settled),
            )
        )
    mended = tuple(
        str(u.task.subject["handle"])
        for u in used
        if u.task.subject["kind"] == Kind.MEND
    )
    return Done(report, filed=recorded.answer.key, mended=mended)


def as_run_report(question: Question, done: Done) -> RunReport:
    """The queue's end as the update's reports read a session's."""
    report = done.report
    spent = 0.0 if report is None else report.spent
    stopped = None if report is None else report.stopped
    detail = (
        "; ".join(done.problems)
        if done.problems
        else ("" if report is None else report.detail)
    )
    return RunReport(
        asked=(question.key,),
        answered=(question.key,) if done.filed else (),
        failed=(question.key,) if done.problems else (),
        spent=spent,
        stopped=stopped,
        detail=detail,
    )
