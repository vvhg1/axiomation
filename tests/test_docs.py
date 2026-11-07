"""The docs' data, held level with the code.

`README.md` and `docs/` are prose, which is not checked, around data,
which drifts first and is cheap to hold level: each closed set they list
(a table under a `<!-- closed set: … -->` comment) equals its enum, every
command they name is the parser's and every flag its command's, and every
relative link resolves. Every doc the code cites exists.
"""

from __future__ import annotations

import argparse
import importlib
import re
from pathlib import Path

import pytest

from axiomation.cli import parser

ROOT = Path(__file__).parents[1]
DOCS = [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))]
#: Every Markdown file a reader of the repository meets, its links checked.
LINKED = [*DOCS, ROOT / "CONTRIBUTING.md", ROOT / "CHANGELOG.md"]
SOURCE = ROOT / "src" / "axiomation"
#: The closed sets the docs must list, each once.
REQUIRED = (
    "axiomation.cutter.model.Form",
    "axiomation.cutter.model.Role",
    "axiomation.cutter.model.GroupKind",
    "axiomation.records.EdgeKind",
    "axiomation.records.TargetKind",
    "axiomation.records.Mark",
    "axiomation.records.Status",
    "axiomation.records.Partial",
    "axiomation.records.Declared",
    "axiomation.records.ImpactClass",
    "axiomation.records.Signal",
    "axiomation.records.Aspect",
    "axiomation.records.Verdict",
    "axiomation.records.RulingKind",
    "axiomation.records.Publication",
    "axiomation.records.SuggestionStatus",
    "axiomation.records.Depth",
    "axiomation.records.AnchorKind",
    "axiomation.records.FindingStatus",
    "axiomation.identity.BlockClass",
    "axiomation.person.PinKind",
    "axiomation.versions.Command",
    "axiomation.evidence.ReadStatus",
    "axiomation.evidence.RunStatus",
    "axiomation.judgments.CauseKind",
    "axiomation.judgments.StatusKind",
    "axiomation.established.Missing",
    "axiomation.established.Coarse",
    "axiomation.established.Completeness",
    "axiomation.established.UndeclaredCause",
    "axiomation.queries.OpenKind",
    "axiomation.rulings.Support",
    "axiomation.claim_status.ClaimStatus",
    "axiomation.check.Gate",
    "axiomation.check.Mode",
    "axiomation.check.Outcome",
    "axiomation.check.Cause",
    "axiomation.questions.Stage",
    "axiomation.questions.review_file.Session",
    "axiomation.profiles.Harness",
    "axiomation.runner.Stop",
    "axiomation.sources.config.OcrBackend",
    "axiomation.records.RecordKind",
)
#: Values a closed set's table leaves out on purpose: the outside review
#: (`share`, `journal`, `review-share`, `receive`) ships as code, out of
#: the public docs, and its gate with it.
LEFT_OUT = {
    "axiomation.check.Gate": {"reviews"},
}
MARKER = re.compile(r"<!-- closed set: (.+?) -->")


def closed_sets() -> list[tuple[str, str, list[str]]]:
    """Each closed set the docs list: (its doc, its name, the backticked
    values of its table's first column, in order)."""
    found = []
    for doc in DOCS:
        lines = doc.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            marker = MARKER.fullmatch(line.strip())
            if marker is None:
                continue
            rows = []
            for row in lines[i + 3 :]:  # past the header and its rule
                if not row.startswith("|"):
                    break
                first = row.split("|")[1]
                rows += re.findall(r"`([^`]+)`", first)
            found.append((doc.name, marker.group(1), rows))
    return found


def members(name: str) -> list[str]:
    module, _, enum = name.rpartition(".")
    return [m.value for m in getattr(importlib.import_module(module), enum)]


def test_each_required_closed_set_is_listed_once():
    names = [name for _, name, _ in closed_sets()]
    assert sorted(names) == sorted(REQUIRED)


@pytest.mark.parametrize(
    ("doc", "name", "listed"),
    closed_sets(),
    ids=lambda v: v if isinstance(v, str) else "",
)
def test_a_closed_set_equals_the_code(doc, name, listed):
    left_out = LEFT_OUT.get(name, set())
    assert left_out <= set(members(name)), f"{name}: LEFT_OUT names no member"
    expected = set(members(name)) - left_out
    assert sorted(listed) == sorted(expected), f"{doc}: {name}"


def subcommands(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    actions = [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]
    return {name: sub for action in actions for name, sub in action.choices.items()}


def commands() -> set[str]:
    return set(subcommands(parser()))


def named_commands(text: str) -> set[str]:
    """`axiomation <command>` wherever code is quoted: inline or fenced."""
    code = re.findall(r"```.*?```", text, re.S) + re.findall(r"`[^`\n]+`", text)
    return {
        m.group(1)
        for span in code
        for m in re.finditer(r"\baxiomation ([a-z][a-z-]*)", span)
    }


@pytest.mark.parametrize("doc", DOCS, ids=lambda d: d.name)
def test_every_command_named_is_the_parser_s(doc):
    named = named_commands(doc.read_text(encoding="utf-8"))
    assert named, f"{doc.name} names no command"
    assert not named - commands()


def named_runs(text: str) -> list[tuple[str, set[str]]]:
    """Each line that runs `axiomation <command>` where code is quoted, a
    `\\`-continued line joined: its command (`sources ACTION` for the
    cited works') and the flags it gives, quotes and comments left out."""
    code = re.findall(r"```.*?```", text, re.S) + re.findall(r"`[^`\n]+`", text)
    lines = [line for span in code for line in re.sub(r"\\\n", " ", span).split("\n")]
    runs = []
    for line in lines:
        run = re.search(r"\baxiomation ((?:sources )?[a-z][a-z-]*)(.*)", line)
        if run is not None:
            command, rest = run.groups()
            rest = re.sub(r"'[^']*'|\"[^\"]*\"|#.*", "", rest)
            runs.append((command, set(re.findall(r"(?<!\S)--[a-z-]+", rest))))
    return runs


@pytest.mark.parametrize("doc", DOCS, ids=lambda d: d.name)
def test_every_flag_named_is_its_command_s(doc):
    parsers = all_parsers()
    unknown = []
    for command, flags in named_runs(doc.read_text(encoding="utf-8")):
        if command == "sources":
            continue
        assert command in parsers, f"{doc.name}: axiomation {command}"
        options = {o for a in parsers[command]._actions for o in a.option_strings}
        unknown += [(command, flag) for flag in flags - options]
    assert not unknown


def all_parsers() -> dict[str, argparse.ArgumentParser]:
    """Every command's parser, `sources ACTION`'s among them, by its name."""
    from axiomation.sources.cli import add_arguments

    parsers = subcommands(parser())
    sources = argparse.ArgumentParser()
    add_arguments(sources)
    return parsers | {f"sources {n}": p for n, p in subcommands(sources).items()}


def test_no_help_names_a_flag_it_hides():
    """A flag hidden from `--help` still works, but no help line points a
    reader at it: they could not find it there."""
    named = []
    for command, found in all_parsers().items():
        hidden = {
            o
            for a in found._actions
            if a.help == argparse.SUPPRESS
            for o in a.option_strings
        }
        text = found.format_help()
        named += [
            (command, flag)
            for flag in hidden
            if re.search(rf"(?<![\w-]){re.escape(flag)}(?![\w-])", text)
        ]
    assert not named


def test_every_flag_has_a_help_line():
    # `sources` hands its words on to its own parser, which helps each action
    missing = [
        (command, a.dest)
        for command, found in all_parsers().items()
        if command != "sources"
        for a in found._actions
        if a.help is None and not isinstance(a, argparse._SubParsersAction)
    ]
    assert not missing


def test_the_top_level_help_leaves_the_shares_out():
    from axiomation.cli import UNLISTED_COMMANDS

    text = parser().format_help()
    listed = set(re.findall(r"^    ([a-z][a-z-]*) ", text, re.M))
    assert listed and not listed & set(UNLISTED_COMMANDS)
    assert set(UNLISTED_COMMANDS) <= commands()


def test_the_guide_s_runs_and_their_flags_are_found():
    runs = named_runs((ROOT / "docs" / "guide.md").read_text(encoding="utf-8"))
    assert len(runs) > 20 and sum(bool(flags) for _, flags in runs) > 10


@pytest.mark.parametrize("doc", LINKED, ids=lambda d: d.name)
def test_every_relative_link_resolves(doc):
    text = doc.read_text(encoding="utf-8")
    links = re.findall(r"\]\(([^)\s]+)\)", text)
    for target in links + re.findall(r'\b(?:src|srcset)="([^"]+)"', text):
        if re.match(r"[a-z]+:", target) or target.startswith("#"):
            continue
        assert (doc.parent / target.split("#")[0]).exists(), f"{doc.name}: {target}"


def test_sources_prints_its_help():
    pytest.importorskip("axiomation.sources.cli")
    from axiomation.cli import main

    with pytest.raises(SystemExit) as exit_:
        main(["sources", "-h"])
    assert exit_.value.code == 0


def test_every_doc_the_code_cites_exists():
    cited = {
        (path.relative_to(ROOT), target)
        for path in SOURCE.rglob("*.py")
        for target in re.findall(r"\bdocs/[\w./-]+\.md", path.read_text("utf-8"))
    }
    assert not [(p, t) for p, t in cited if not (ROOT / t).exists()]
