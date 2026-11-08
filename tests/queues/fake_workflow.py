"""A workflow for the queue's tests, on `test_marks`' kept update: a task
a block (`setting`, the proof, the sharpness claim's), answered by saying
its word. With `steps` 2, the word is handed to a second step that
confirms it, and the task settles there; a step passes the tasks its
option names (`passes`, `confirm_passes`). With `argue`, the proof's task
(or the task `argue_on` names) settles the line that stands the
sharpness claim justified, and the claim's task then leaves the queue.
The tasks `gone` names are not drawn. No model is called."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from axiomation import edits, queries
from axiomation.changes import Layer
from axiomation.questions import Stage, Template
from axiomation.questions import edge_sheet as es
from axiomation.queue import Accepted, Read, Refused, Serving, Task, Tool, Working
from axiomation.queue.reads import ReadError
from axiomation.records import RecordStore, Status
from axiomation.versions import State
from test_support_search import ARGUING, PROOF_BLOCK
from test_update import SHARP

FACTORY = "queues.fake_workflow:words"
BLOCKS = (("t1", "setting", "one"), ("t2", PROOF_BLOCK, "two"), ("t3", SHARP, "three"))
ARGUED = f"⟦{PROOF_BLOCK}⟧\n+ {ARGUING}"


def _word(task: str, word: str) -> dict[str, Any]:
    return {
        "type": "object",
        "required": ["task", word],
        "properties": {"task": {"type": "string"}, word: {"type": "string"}},
    }


class Say:
    kind = "fake-say"
    stage = Stage.EDGES
    template = Template("fake-say", "Say each task's word.\n")
    kickoff = "Call `next`."
    tools = (Tool("say", "Say the task's word.", _word("task", "word")),)

    def __init__(
        self, hand_on: bool, argue: str | None, passes: frozenset[str]
    ) -> None:
        self.hand_on = hand_on
        self.argue = argue
        self.passes = passes

    def takes(self, task: Task) -> bool:
        return task.id not in self.passes

    def render(self, task: Task, working: Working) -> str:
        return f"say {task.subject['word']}\n"

    def answer(
        self,
        task: Task,
        tool: str,
        args: Mapping[str, Any],
        working: Working,
        check: bool,
    ) -> Accepted | Refused:
        if args.get("word") != task.subject["word"]:
            return Refused((f"say {task.subject['word']!r}",))
        if self.hand_on:
            return Accepted(hand_on={"said": args["word"]})
        return Accepted(ARGUED if task.id == self.argue else "")


class Confirm:
    kind = "fake-confirm"
    stage = Stage.EDGES
    template = Template("fake-confirm", "Confirm each word said.\n")
    kickoff = "Call `next`."
    tools = (Tool("confirm", "Confirm the word said.", _word("task", "word")),)

    def __init__(self, argue: str | None, passes: frozenset[str]) -> None:
        self.argue = argue
        self.passes = passes

    def takes(self, task: Task) -> bool:
        return task.id not in self.passes

    def render(self, task: Task, working: Working) -> str:
        # a task the first step passed comes as drawn
        return f"confirm {task.subject.get('said', task.subject['word'])}\n"

    def answer(
        self,
        task: Task,
        tool: str,
        args: Mapping[str, Any],
        working: Working,
        check: bool,
    ) -> Accepted | Refused:
        said = task.subject.get("said", task.subject["word"])
        if args.get("word") != said:
            return Refused((f"confirm {said!r}",))
        return Accepted(ARGUED if task.id == self.argue else "")


class Words:
    restating = True
    reads: tuple[Read, ...] = ()
    by_graph = True
    serving = Serving()

    def __init__(
        self,
        kept: edits.Kept,
        view: queries.View,
        steps: tuple[Any, ...],
        gone: frozenset[str] = frozenset(),
    ) -> None:
        self.gone = gone
        self.view = view
        self.files = kept.directory
        self.sheet = edits.shown(kept, Layer.EDGES)
        self.steps = steps

    def draw(self, working: Working) -> list[Task]:
        argued = any(
            s.source == SHARP and s.status != Status.UNSUPPORTED
            for s in es.parse(working.sheet).supports
        )
        return [
            Task(task, self.view.resolve(block), {"word": word})
            for task, block, word in BLOCKS
            if not (argued and block == SHARP) and task not in self.gone
        ]


@contextmanager
def words(
    store: RecordStore, state: State, options: Mapping[str, Any]
) -> Iterator[Words]:
    two = options.get("steps", 1) == 2
    argue = (options.get("argue_on") or "t2") if options.get("argue") else None
    steps: tuple[Any, ...] = (Say(two, argue, frozenset(options.get("passes", ()))),)
    if two:
        steps += (Confirm(argue, frozenset(options.get("confirm_passes", ()))),)
    view = queries.View(store, state)
    with edits.opened(store, state) as kept:
        found = Words(kept, view, steps, frozenset(options.get("gone", ())))
        if options.get("counted"):
            found.reads = (COUNTED,)
        if options.get("own_block"):
            found.reads = (OWN_BLOCK,)
        found.by_graph = not options.get("paper_order")
        found.serving = Serving(
            frozenset(options.get("withheld", ())),
            dict(options.get("files", {})),
            options.get("session_tasks"),
        )
        yield found


def _counted(working: Working, args: Mapping[str, Any]) -> str:
    if args.get("of") != "settled":
        raise ReadError("count only what is settled")
    return f"{len(working.settled)} settled"


#: A read the workflow adds where its options ask (`counted`).
COUNTED = Read(
    Tool(
        "count",
        "How many tasks are settled.",
        {
            "type": "object",
            "required": ["of"],
            "properties": {"of": {"type": "string"}},
        },
    ),
    _counted,
)


def saying(text: str) -> tuple[str, dict[str, Any]]:
    """A script's answer to a task served: its word said, or confirmed."""
    task = text.split()[1]
    verb, word = text.splitlines()[1].split()
    return ("say" if verb == "say" else "confirm"), {"task": task, "word": word}


#: A read given in place of the core's `block`.
OWN_BLOCK = Read(
    Tool(
        "block",
        "A block as this workflow reads it.",
        {
            "type": "object",
            "required": ["handle"],
            "properties": {"handle": {"type": "string"}},
        },
    ),
    lambda working, args: f"this workflow's {args['handle']}",
)
