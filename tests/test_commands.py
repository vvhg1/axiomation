"""The commands as functions: the front ends call them, nothing else does,
and each answers its typed request as the command line answers its flags.

`docs/architecture.md` § 1: a front end (`cli.py`, the viewer's server)
builds a command's request and calls it; a command imports no front end;
no component imports a command or the command line.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

from axiomation.cli import main
from axiomation.commands import (
    Paper,
    Say,
    asks,
    build,
    filings,
    reads,
    seating,
    sync,
)
from axiomation.commands import edits as editing
from axiomation.commands import review as reviewing
from axiomation.commands import update as updating
from axiomation.settings import CONFIG_NAME
from test_edits import built_state

SOURCE = Path(__file__).parents[1] / "src" / "axiomation"
#: The front ends: what may import the commands.
FRONT_ENDS = {"axiomation.cli", "axiomation.viewer.server"}


def _module(path: Path) -> str:
    parts = path.relative_to(SOURCE.parent).with_suffix("").parts
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def _imports(path: Path) -> set[str]:
    """Every module `path` imports, at its top or inside a function."""
    package = _module(path)
    if path.name != "__init__.py":
        package = package.rpartition(".")[0]
    found = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                up = package.split(".")[: len(package.split(".")) - node.level + 1]
                base = ".".join([*up, *([base] if base else [])])
            found.add(base)
            found |= {f"{base}.{a.name}" for a in node.names}
    return found


def _modules() -> dict[str, set[str]]:
    return {_module(p): _imports(p) for p in sorted(SOURCE.rglob("*.py"))}


def _reaches(imported: set[str], target: str) -> bool:
    return any(m == target or m.startswith(f"{target}.") for m in imported)


def test_no_module_imports_the_command_line() -> None:
    importers = sorted(
        name
        for name, imported in _modules().items()
        if name != "axiomation.cli" and _reaches(imported, "axiomation.cli")
    )
    assert importers == []


def test_only_a_front_end_imports_a_command() -> None:
    importers = sorted(
        name
        for name, imported in _modules().items()
        if not name.startswith("axiomation.commands")
        and name not in FRONT_ENDS
        and _reaches(imported, "axiomation.commands")
    )
    assert importers == []


def test_a_command_imports_no_front_end() -> None:
    importers = sorted(
        name
        for name, imported in _modules().items()
        if name.startswith("axiomation.commands")
        and any(_reaches(imported, f) for f in FRONT_ENDS)
    )
    assert importers == []


# --- the reads, called with a request -------------------------------------------


def _printed(capsys, argv: list[str]) -> str:
    assert main(argv) == 0
    return capsys.readouterr().out


def test_a_read_answers_its_request_as_the_command_line(tmp_path, capsys) -> None:
    store, args = built_state(tmp_path, capsys)
    paper = Paper(Path(args[0]), store.directory)
    where = [args[0], "--store", str(store.directory)]
    blocks = _printed(capsys, ["blocks", *where, "--json"])
    first = json.loads(blocks)["blocks"]["items"][0]["block"]
    assert reads.query(reads.Blocks(paper=paper, json=True)) == blocks
    assert reads.query(reads.Show(paper=paper, block=first)) == _printed(
        capsys, ["show", *where, first]
    )
    assert reads.states(reads.States(paper)) == _printed(capsys, ["states", *where])


def test_the_viewers_reads_are_the_command_lines() -> None:
    """The reads' own parser is the command line's for each read: the
    viewer's server reads a request's parameters by it."""
    from axiomation.cli import parser

    def flags(parser, command: str) -> list[tuple[tuple[str, ...], object]]:
        sub = next(
            a for a in parser._actions if hasattr(a, "choices") and a.choices
        ).choices[command]
        return [(a.option_strings, a.default) for a in sub._actions]

    for command in reads.QUERIES:
        assert flags(reads.parser(), command) == flags(parser(), command)


# --- the asks and the filings, called with a request ----------------------------


class Said(Say):
    """What a command says, kept."""

    def __init__(self) -> None:
        self.lines: list[str] = []
        self.notes: list[str] = []
        #: stdout as the command line would print it: the lines and the
        #: results written whole, in order
        self.out = ""

    def line(self, text: str, flush: bool = False) -> None:
        self.lines.append(text)
        self.out += text + "\n"

    def note(self, text: str) -> None:
        self.notes.append(text)

    def write(self, text: str) -> None:
        self.out += text


def test_an_ask_answers_its_request_as_the_command_line(tmp_path, capsys) -> None:
    store, args = built_state(tmp_path, capsys)
    paper = Paper(Path(args[0]), store.directory)
    where = [args[0], "--store", str(store.directory)]
    said = Said()
    listed = asks.support(asks.Support(paper=paper), said)
    assert listed.text == _printed(capsys, ["support", *where]).removesuffix("\n")
    circled = asks.circles_of(asks.Circles(paper=paper), said)
    assert circled.text == _printed(capsys, ["circles", *where])
    assert said.lines == said.notes == []


def test_a_budget_taken_from_the_paper_is_said_where_the_command_says(
    tmp_path,
) -> None:
    """`take_budget` says the paper's budget on the request's `Say`, never
    on the process's streams."""
    paper = tmp_path / "paper"
    paper.mkdir()
    (paper / CONFIG_NAME).write_text("[budget]\nrun_dollars_max = 7\n")
    said = Said()
    taken = seating.take_budget(
        seating.Asking(profile="standard"), paper, tmp_path / "store", "x", "", said
    )
    assert taken.budget == 7.0 and taken.expect is not None
    assert said.notes and said.notes[0].startswith("axiomation x: budget $7")


def test_a_filing_answers_its_request_as_the_command_line(
    tmp_path, capsys, node_ids
) -> None:
    """The same judgment filed on two equal stores, by its request and by
    its flags, says the same lines."""
    texts = []
    for name in ("asked", "flagged"):
        node_ids()
        store, args = built_state(tmp_path / name, capsys)
        paper = Paper(Path(args[0]), store.directory)
        claims = reads.Blocks(paper=paper, role=("claim",), json=True)
        block = json.loads(reads.query(claims))["blocks"]["items"][0]["block"]
        flags = {
            "aspect": "true",
            "on": block,
            "verdict": "verified",
            "depth": "informal",
            "evidence": "it holds",
        }
        if name == "asked":
            texts.append(filings.file(filings.Judge(paper=paper, **flags), Said()))
        else:
            argv = ["judge", args[0], "--store", str(store.directory)]
            argv += [f for k, v in flags.items() for f in (f"--{k}", v)]
            texts.append(_printed(capsys, argv))
    asked, flagged = (t.splitlines()[1:] for t in texts)
    assert texts[0].startswith("judgment ") and asked == flagged


# --- the runs, called with a request --------------------------------------------


def test_a_sheet_answers_its_request_as_the_command_line(tmp_path, capsys) -> None:
    store, args = built_state(tmp_path, capsys)
    paper = Paper(Path(args[0]), store.directory)
    said = Said()
    asked = editing.Sheet(paper=paper, layer="edges")
    assert editing.sheet(asked, said) == 0
    where = [args[0], "--store", str(store.directory)]
    assert said.out == _printed(capsys, ["sheet", *where, "--stage", "edges"])


def test_a_review_report_answers_its_request_as_the_command_line(
    tmp_path, capsys
) -> None:
    store, args = built_state(tmp_path, capsys)
    paper = Paper(Path(args[0]), store.directory)
    said = Said()
    assert reviewing.run(reviewing.Review(paper=paper, report=True), said).code == 0
    where = [args[0], "--store", str(store.directory)]
    assert said.out == _printed(capsys, ["review", *where, "--report"])


def test_a_diff_answers_its_request_as_the_command_line(tmp_path, capsys) -> None:
    _, args = built_state(tmp_path, capsys)
    paper = Path(args[0])
    asked = updating.Diff(old=paper, new=paper)
    assert updating.diff(asked) == _printed(capsys, ["diff", str(paper), str(paper)])


def test_a_build_and_a_round_answer_their_requests_as_the_command_line(
    tmp_path, capsys, node_ids
) -> None:
    """A build replayed from the store, and a round's dry run, on two equal
    stores: by request and by flags, the same report."""
    said: dict[str, Said] = {}
    printed: dict[str, str] = {}
    for name in ("by-request", "by-flags"):
        node_ids()
        store, args = built_state(tmp_path / name, capsys)
        paper = Paper(Path(args[0]), store.directory)
        where = [args[0], "--store", str(store.directory)]
        dry = tmp_path / name / "dry"
        if name == "by-request":
            built = build.Build(
                command="build", paper=paper, asking=seating.Asking(host="none")
            )
            said["build"] = Said()
            assert build.run(built, said["build"]) == 0
            said["sync"] = Said()
            round_ = sync.Sync(paper=paper, dry_run=True, prompts=dry)
            assert sync.run(round_, said["sync"]) == 0
        else:
            printed["build"] = _printed(capsys, ["build", *where, "--host", "none"])
            printed["sync"] = _printed(
                capsys, ["sync", *where, "--dry-run", "--prompts", str(dry)]
            )
    assert said["build"].out == printed["build"]
    assert said["sync"].out.replace("by-request", "by-flags") == printed["sync"]


def test_a_run_s_summary_reads_as_lines() -> None:
    """A build's or an update's summary, as a person reads it: the state
    kept or why none was, the questions, the graph, each step it ended
    with, what it spent; `--json` gives the whole."""
    from axiomation.commands import summary_lines

    built = {
        "questions": {"boundaries": 1, "edges": 2},
        "answered": {"boundaries": 1, "edges": 1},
        "failed": [{"key": "k1", "stage": "edges", "first_block": "b1"}],
        "edges": {"edges": 9, "standings": 4, "self_marks": 1},
        "applied": 3,
        "support": {"claims": 2, "argued": 1, "unanswered": ["c1"], "spent": 0.5},
        "spent": {"run": 1.234, "store": 2.5},
        "state": {"id": "s2", "parent": "s1"},
    }
    assert summary_lines(built).splitlines() == [
        "state s2 kept, on s1",
        "  questions: boundaries 1, edges 2; 1 unanswered",
        "  failed: k1 (edges)",
        "  graph: 9 edges, 4 standings, 1 self marks; 3 corrections applied",
        "  support: 2 claims, 1 argued, 1 unanswered, $0.50",
        "spent $1.23; the store's answers, $2.50 in all "
        "(--json gives the whole summary)",
    ]
    stopped = {"spent": 0.004, "failed": ["k2"], "stopped": "budget", "detail": "$1"}
    assert summary_lines(stopped).splitlines() == [
        "no state kept: stopped; the same command resumes",
        "  failed: k2",
        "spent under $0.01 (--json gives the whole summary)",
        "stopped: budget: $1",
    ]
    again = {"built": True, "state": {"id": "s2", "parent": "s1"}}
    assert summary_lines(again).splitlines() == [
        "state s2 of this version is kept already",
        "(--json gives the whole summary)",
    ]
