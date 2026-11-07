"""What a read derives and keeps in the user's cache changes no answer
(`docs/architecture.md` § 4): every read's output is the same worked out
whole (nothing kept, nothing held in the process), read from what was
kept, and with a state added, carried on from the parent's walks; and the
cache is read only where it is the user's alone. On the synthetic paper
built, edited and updated by the command line, with a finding on each
state's block."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from axiomation import derived, judgments, queries, versions
from axiomation.cli import main
from axiomation.judgments import WALKS
from axiomation.queries import View
from viewer.test_viewer_server import built_and_updated

#: The process's holds on what kept states give, emptied to read as a new
#: process does.
HELD = (
    (versions, "_BLOBS", versions.BLOB_CACHE_BYTES),
    (versions, "_KEPT_STATES", versions.KEPT_STATES_HELD),
    (versions, "_DOCUMENTS", versions.DOCUMENTS_HELD),
    (versions, "_OUTLINES", versions.OUTLINES_HELD),
    (versions, "_TRANSITIONS", versions.TRANSITIONS_HELD),
    (judgments, "_READERS", judgments.READERS_HELD),
    (queries, "_LATER_RULES", queries.LATER_RULES_HELD),
)


def new_process(monkeypatch: pytest.MonkeyPatch) -> None:
    for module, name, limit in HELD:
        monkeypatch.setattr(module, name, versions.Held(limit))


def asked(paper: Path, nodes: list[str]) -> list[list[str]]:
    """Every read the command line gives on the latest state."""
    whole = ["--limit", "0", "--json"]
    return [
        ["open", str(paper), *whole],
        ["check", str(paper), "--json"],
        ["inbox", str(paper), "--json"],
        ["blocks", str(paper), *whole],
        ["changes", str(paper), *whole],
        *(["show", str(paper), n, "--whole", "--history", *whole] for n in nodes),
        *(["history", str(paper), n] for n in nodes),
    ]


def read(args: list[str], capsys) -> tuple[int, Any]:
    """A read as it prints it (JSON where it gives it), with its exit code."""
    capsys.readouterr()
    code = main(args)
    out = capsys.readouterr()
    assert code in (0, 1), (args, out.err)
    return code, json.loads(out.out) if "--json" in args else out.out


def reads(paper: Path, capsys, nodes: list[str]) -> dict[str, Any]:
    return {" ".join(a[:1] + a[2:]): read(a, capsys) for a in asked(paper, nodes)}


def computed_whole(paper: Path, capsys, monkeypatch, nodes) -> dict[str, Any]:
    """Every read, each worked out with nothing kept and nothing held."""
    found = {}
    for args in asked(paper, nodes):
        shutil.rmtree(derived.root(), ignore_errors=True)
        new_process(monkeypatch)
        found[" ".join(args[:1] + args[2:])] = read(args, capsys)
    shutil.rmtree(derived.root(), ignore_errors=True)
    new_process(monkeypatch)
    return found


def file_findings(paper: Path, state: str, handles: list[str], capsys) -> None:
    for on in handles:
        code = main(
            [
                "find",
                str(paper),
                "--state",
                state,
                "--aspect",
                "presentation",
                "--on",
                on,
                "--severity",
                "2",
                "--problem",
                f"a problem in {on}",
            ]
        )
        assert code == 0, capsys.readouterr().err


@pytest.fixture
def paper(tmp_path, capsys):
    """The two states' store, a finding filed on the build's state on each
    of two blocks (one the update changed, so it lapses), and on the
    latest: the paper, its store and the nodes read."""
    root, store = built_and_updated(tmp_path, capsys)
    latest = versions.latest(store)
    assert latest is not None and latest.parent is not None
    view = View(store, latest)
    handles = [view.handle(b) for b in view.blocks][:3]
    file_findings(root, latest.parent, handles[:2], capsys)
    file_findings(root, latest.id, handles[2:], capsys)
    return root, store, [view.node(view.resolve(h)) for h in handles]


@pytest.mark.usefixtures("develop_mode")
def test_a_read_kept_and_a_read_computed_whole_are_the_same(
    paper, capsys, monkeypatch
) -> None:
    root, store, nodes = paper
    whole = computed_whole(root, capsys, monkeypatch, nodes)

    assert reads(root, capsys, nodes) == whole, "deriving and keeping"
    kept = derived.names()
    latest = versions.latest(store)
    assert latest is not None
    assert f"{WALKS}{latest.id}" in kept
    # (a version's run of marks' first states is kept only where it holds
    # an open stale mark: this paper's update leaves none)
    for kind in ("outline-", "step-", "named-", "later-"):
        assert any(n.startswith(kind) for n in kept), kind
    # the record is hash-checked as it loads: no cache of edge ids is kept
    assert not any(n.startswith("checked-") for n in kept)

    new_process(monkeypatch)
    assert reads(root, capsys, nodes) == whole, "read from what was kept"
    assert reads(root, capsys, nodes) == whole, "held by the process"


@pytest.mark.usefixtures("develop_mode")
def test_a_read_writes_nothing_in_the_paper_or_its_store(paper, capsys) -> None:
    root, _, nodes = paper
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}

    reads(root, capsys, nodes)

    assert {p: p.read_bytes() for p in root.rglob("*") if p.is_file()} == before
    assert derived.names()


@pytest.mark.usefixtures("develop_mode")
def test_a_state_added_reads_on_from_its_parent_s_walks_as_computed_whole(
    paper, capsys, monkeypatch
) -> None:
    root, store, nodes = paper
    reads(root, capsys, nodes)
    parent = versions.latest(store)
    assert parent is not None
    view = View(store, parent)
    # a person's pin on a line as `show` prints it: a new state
    owner, line = next(
        (view.handle(e["source"]), view.edge_line(e))
        for e in view.edges
        if e["source"] in view.blocks
    )
    assert (
        main(["change", str(root), "--keep", owner, line, "--reason", "a pin"]) == 0
    ), capsys.readouterr().err
    made = versions.latest(store)
    assert made is not None and made.parent == parent.id
    # the change's own report read the new state: its walks taken away, a
    # read goes on from the parent's
    assert f"{WALKS}{parent.id}" in derived.names()
    walks = derived.root() / "derived" / derived.CODE / f"{WALKS}{made.id}"
    Path(f"{walks}{derived.SUFFIX}").unlink()

    new_process(monkeypatch)
    carried = reads(root, capsys, nodes)
    assert f"{WALKS}{made.id}" in derived.names()

    assert carried == computed_whole(root, capsys, monkeypatch, nodes)


@pytest.mark.usefixtures("develop_mode")
def test_a_cache_that_cannot_be_written_keeps_nothing_and_reads_the_same(
    paper, capsys, monkeypatch
) -> None:
    root, _, nodes = paper
    whole = computed_whole(root, capsys, monkeypatch, nodes)
    derived.root().mkdir(parents=True, exist_ok=True)
    (derived.root() / "derived").write_text("not a directory", encoding="utf-8")

    assert reads(root, capsys, nodes) == whole
    assert derived.names() == frozenset()


@pytest.mark.usefixtures("develop_mode")
def test_a_value_kept_unreadable_is_computed_again(paper, capsys, monkeypatch) -> None:
    root, _, nodes = paper
    whole = computed_whole(root, capsys, monkeypatch, nodes)
    reads(root, capsys, nodes)
    for path in (derived.root() / "derived").rglob(f"*{derived.SUFFIX}"):
        path.write_bytes(b"torn")
    new_process(monkeypatch)

    assert reads(root, capsys, nodes) == whole


def test_a_value_is_read_only_from_a_cache_no_one_else_writes(
    tmp_path, monkeypatch
) -> None:
    """A value written where another user may write is never read: what
    it holds would run as the reader."""
    shared = tmp_path / "shared"
    monkeypatch.setenv(derived.CACHE_VARIABLE, str(shared))
    derived.save("a-value", {"kept": 1})
    assert derived.load("a-value") == {"kept": 1}

    directory = shared / "derived" / derived.CODE
    directory.chmod(0o777)
    derived._trusted.cache_clear()
    assert derived.load("a-value") is None
    assert derived.names() == frozenset()


def test_a_value_that_cannot_be_kept_is_not_kept(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(derived.CACHE_VARIABLE, str(tmp_path / "cache"))
    derived.save("a-closure", lambda: None)
    assert derived.load("a-closure") is None
    assert derived.held("a-closure", lambda: 3) == 3
