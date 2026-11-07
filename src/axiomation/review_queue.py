"""What a review session asked as a queue shares (docs/architecture.md
§ 2, § 7): its tasks as its sheet shows them, each answer checked by the
session's gate on a sheet of that one task, the find line a typed finding
writes, and the tools that settle a finding.

Each review queue (`sources.citation_queue`, `methods_queue`) draws its
tasks from the session's brief (`review.brief`): the findings it settles
(`f…`), then its subjects (`s…`). Code writes a typed answer as the
sheet's lines, and `Sheet.checked` reads them as the runner's check
reads a sheet (`review.checker`), so the queue files what the sheet
would.

The core only: no `library` extra.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from axiomation import edits, review
from axiomation.evidence import Sources
from axiomation.questions import review_blocks as rb
from axiomation.questions import review_file as rf
from axiomation.queue import Accepted, Refused, Tool


class AnswerError(ValueError):
    """An answer code cannot write as the sheet's lines, with why."""


TEXT = {"type": "string"}
TASK = {"type": "string", "description": "the task's id, as `next` heads it: `s3`"}
STANDS = Tool(
    "stands",
    "Settle a finding: it still stands in the text as it reads now.",
    {
        "type": "object",
        "required": ["task", "why"],
        "properties": {
            "task": TASK,
            "quote": {
                "type": "string",
                "description": "words of its first block, where its own no longer "
                "stand once",
            },
            "why": TEXT,
        },
    },
)
FIXED = Tool(
    "fixed",
    "Settle a finding: the text no longer has it.",
    {
        "type": "object",
        "required": ["task", "why"],
        "properties": {"task": TASK, "why": TEXT},
    },
)
READING = Tool(
    "reading",
    "Settle a finding that carries a proposal: the two readings, which no case "
    "settles.",
    {
        "type": "object",
        "required": ["task", "why"],
        "properties": {"task": TASK, "quote": TEXT, "why": TEXT},
    },
)
SETTLING_TOOLS = (STANDS.name, FIXED.name, READING.name)
HINTS = Tool(
    "hints",
    "What others have read at this state: judgments, findings and the "
    "verifiers' records. A hint says where to look, never what is so.",
    {"type": "object", "properties": {}},
)


def oneline(text: Any) -> str:
    return " ".join(str(text or "").split())


#: What parts a find line's problem from its fix.
PARTED = " — "


def find_line(f: Mapping[str, Any]) -> str:
    """A typed finding as its find line. The line parts its problem from
    its fix at the first dash, so one inside the problem is written as a
    semicolon: its fields stay the finding's own."""
    anchors = " ".join(oneline(a) for a in f.get("anchors") or ())
    quote = oneline(f.get("quote"))
    if '"' in quote:
        raise AnswerError(
            f"{quote!r}: a finding's quote holds no double quote; quote the "
            "words around it"
        )
    problem = oneline(f.get("problem")).replace(PARTED, "; ")
    fix = oneline(f.get("fix"))
    return (
        f"find {f.get('aspect')} {anchors} {f.get('severity')} "
        f'"{quote}"{PARTED}{problem}' + (f"{PARTED}{fix}" if fix else "")
    )


def settle_line(fid: str, tool: str, args: Mapping[str, Any]) -> str:
    """A settling answer as its sheet line: `stands`, `fixed` or
    `reading`."""
    why = oneline(args.get("why"))
    if not why:
        raise AnswerError("give the reason (why)")
    quote = oneline(args.get("quote"))
    shown = f' "{quote}"' if quote and tool != FIXED.name else ""
    return f"{tool} {fid}{shown} — {why}"


#: The paper plain: the brief's `paper.md`, each block its handle and text.
plain = rb.plain


@dataclass(frozen=True, slots=True)
class Entry:
    """A task as the sheet shows it: its fixed lines (the subject's or the
    finding's line and the `↳` lines under it) and the handle of the block
    it is on."""

    id: str
    lines: tuple[str, ...]
    handle: str | None


def entries(body: str) -> list[Entry]:
    """The sheet's tasks as it shows them, in its order."""
    found: list[Entry] = []
    for chunk in body.split("\n\n"):
        lines = [x for x in chunk.split("\n") if x]
        if not lines:
            continue
        if m := rf.SUBJECT.match(lines[0]):
            handle = m.group(3).split(" · ", 1)[0].split(" → ", 1)[0].strip()
            found.append(Entry(m.group(1), tuple(lines), handle))
        elif m := rf.QUESTION.match(lines[0]):
            words = m.group(2).split(' "', 1)[0].split()
            # the finding's line: aspect, severity, then its anchors
            anchors = [a for a in words[2:] if a != "paper"]
            found.append(
                Entry(m.group(1), tuple(lines), anchors[0] if anchors else None)
            )
    return found


class Sheet:
    """A session's sheet, as its brief starts it, cut to one task at a
    time: what the queue's gate reads each answer on."""

    def __init__(self, found: review.Brief) -> None:
        self.found = found
        head, sep, body = str(found.spec["reference"]).partition(f"\n{rf.SEPARATOR}\n")
        self.head = head + sep
        self.entries = {e.id: e for e in entries(body)}

    def text(self, task_id: str) -> str:
        return f"{self.head}" + "\n".join(self.entries[task_id].lines) + "\n"

    def cut(self, task_id: str) -> review.Brief:
        """The brief cut to one task: the gate's spec and its subject or
        finding its own."""
        found = self.found
        spec = dict(found.spec)
        spec["reference"] = self.text(task_id)
        held = spec.get("subjects")
        asked = spec.get("questions")
        assert isinstance(held, Mapping) and isinstance(asked, Mapping)
        spec["subjects"] = {i: v for i, v in held.items() if i == task_id}
        spec["questions"] = {i: v for i, v in asked.items() if i == task_id}
        return dataclasses.replace(
            found,
            spec=spec,
            subjects={i: s for i, s in found.subjects.items() if i == task_id},
            questions={i: q for i, q in found.questions.items() if i == task_id},
        )

    def checked(
        self,
        kept: edits.Kept,
        sources: Sources | None,
        author: edits.Author,
        task_id: str,
        lines: Sequence[str],
    ) -> Accepted | Refused:
        """`lines` as the task's answer: the session's gate on a sheet of
        that one task; accepted, the records it files."""
        one = self.cut(task_id)
        check = review.checker(kept, one, sources, author)
        text = self.text(task_id) + "\n" + "".join(f"{x}\n" for x in lines)
        valid = check(one.question, text)
        if valid.problems:
            return Refused(tuple(valid.problems))
        return Accepted("", judgments=valid.judgments)
