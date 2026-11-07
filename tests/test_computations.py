"""A paper's code files: found where its settings say,
the store and hidden directories left out, keyed from the paper or, outside
it, from the workspace; each one's opening line; kept by `snapshot` in
every state, a `workspace:` one materialized nowhere; shown on the edges
sheet's `Code:` lines and taken as a `file:` target. Synthetic papers."""

from __future__ import annotations

from pathlib import Path

from axiomation import computations, versions
from axiomation.cutter import CutRules, DisplayRule, cut
from axiomation.exchange import cut_payload, dumps
from axiomation.questions import edge_sheet as es
from axiomation.questions.edges_file import _file_problem
from axiomation.reader import document_hash, read_paper
from axiomation.records import RecordStore
from axiomation.settings import CONFIG_NAME
from axiomation.versions import Command

SCRIPT = '''#!/usr/bin/env python3
"""Bounds implied by the gain. Prints the largest one."""
print(1.25)
'''


def a_paper(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "paper.md").write_text("# One\n\nThe bound is 1.25.\n")
    (root / "checks").mkdir()
    (root / "checks" / "bound.py").write_text(SCRIPT)
    return root


def test_the_default_finds_a_nested_script_and_leaves_the_store_and_hidden_out(
    tmp_path,
) -> None:
    paper = a_paper(tmp_path / "paper")
    for left_out in ("axiomation/cache/run.py", ".venv/lib/site.py", "a/.hid/x.R"):
        (paper / left_out).parent.mkdir(parents=True, exist_ok=True)
        (paper / left_out).write_text("x = 1\n")
    (paper / "notes.ipynb").write_text("{}")
    (paper / "deep" / "er").mkdir(parents=True)
    (paper / "deep" / "er" / "fit.jl").write_text("# Fit the model.\n")

    found = computations.code_files(paper)

    assert list(found) == ["checks/bound.py", "deep/er/fit.jl"]


def test_a_script_outside_the_paper_is_keyed_from_the_workspace(tmp_path) -> None:
    root = tmp_path / "ws"
    paper = a_paper(root / "papers" / "one")
    (root / CONFIG_NAME).write_text("[workspace]\n")
    (paper / CONFIG_NAME).write_text(
        "[computations]\ndirectories = ['checks', '../../shared']\n"
    )
    (root / "shared").mkdir()
    (root / "shared" / "common.sh").write_text("# Shared setup.\necho ok\n")

    found = computations.code_files(paper)

    assert list(found) == ["checks/bound.py", "workspace:shared/common.sh"]
    assert computations.is_code("workspace:shared/common.sh")
    assert not computations.is_code("fig.pdf")


def test_a_script_s_opening_line() -> None:
    line = computations.opening_line
    assert line(SCRIPT) == "Bounds implied by the gain."
    assert (
        line(
            "# Verifies: Paper I prop:x (a support whose rectangles do not\n"
            "# generate the cycle space), and a remark.\n"
            "# Usage: uv run python checks/x.py\nimport numpy\n"
        )
        == "Verifies: Paper I prop:x (a support whose rectangles do not generate "
        "the cycle space), and a remark."
    )
    assert line('"""\nThe 6x6 family.\n\nMore.\n"""\n') == "The 6x6 family."
    assert line("% Octave: the fit\nx = 1;\n") == "Octave: the fit"
    assert line("import os\n") == ""
    long = line("# " + "word " * 80 + "\n")
    assert len(long) == computations.OPENING_LIMIT and long.endswith("…")


def test_a_kept_state_keeps_its_scripts_and_replays_byte_equal(tmp_path) -> None:
    root = tmp_path / "ws"
    paper = a_paper(root / "papers" / "one")
    (root / CONFIG_NAME).write_text("[workspace]\n")
    (paper / CONFIG_NAME).write_text(
        "[computations]\ndirectories = ['checks', '../../shared']\n"
    )
    (root / "shared").mkdir()
    (root / "shared" / "common.sh").write_text("echo ok\n")
    document = read_paper(paper)
    files = versions.snapshot(paper, document)
    assert files["checks/bound.py"] == SCRIPT.encode()
    assert files["workspace:shared/common.sh"] == b"echo ok\n"

    payload = cut_payload(document, cut(document, CutRules(DisplayRule.IN_PARAGRAPH)))
    store = RecordStore(tmp_path / "store")
    state, _ = versions.record(
        store,
        files,
        document_hash(document),
        document.root,
        dumps(payload).encode(),
        Command.BUILD,
    )
    assert "workspace:shared/common.sh" in state.files
    assert versions.kept_export(store, state) == dumps(payload).encode()

    into = tmp_path / "materialized"
    versions.materialize(store, state, into)
    assert (into / "checks" / "bound.py").read_text() == SCRIPT
    assert not (into / "workspace:shared").exists()
    assert not any("workspace" in p.name for p in into.rglob("*"))

    shown = versions.kept_sheet_files(store, state, into, document.never_included)
    code = [f for f in shown if computations.is_code(f)]
    assert code == ["checks/bound.py", "workspace:shared/common.sh"]
    assert code[0].opening == "Bounds implied by the gain."


def test_a_script_changed_alone_is_a_state_of_its_own(tmp_path) -> None:
    paper = a_paper(tmp_path / "paper")
    document = read_paper(paper)
    payload = dumps(
        cut_payload(document, cut(document, CutRules(DisplayRule.IN_PARAGRAPH)))
    ).encode()
    store = RecordStore(tmp_path / "store")

    def kept() -> tuple[versions.State, bool]:
        return versions.record(
            store,
            versions.snapshot(paper, document),
            document_hash(document),
            document.root,
            payload,
            Command.BUILD,
        )

    first, _ = kept()
    again, new = kept()
    assert again == first and not new, "a re-run with nothing changed adds nothing"
    (paper / "checks" / "bound.py").write_text(SCRIPT.replace("1.25", "1.5"))
    second, new = kept()
    assert new and second.parent == first.id and second.version == first.version
    assert second.files["checks/bound.py"] != first.files["checks/bound.py"]


def test_the_sheet_shows_each_script_on_a_code_line(tmp_path) -> None:
    paper = a_paper(tmp_path / "paper")
    (paper / "fig.pdf").write_bytes(b"%PDF")
    document = read_paper(paper)
    files = es.sheet_files(paper, document.never_included)
    assert files == ["fig.pdf", "paper.md", "checks/bound.py"]

    sheet = es.render([], [], files, "# edges\n")

    assert "Files: fig.pdf, paper.md\n" in sheet
    assert "Code: checks/bound.py — Bounds implied by the gain.\n" in sheet
    parsed = es.parse(sheet)
    assert parsed.files == ("fig.pdf", "paper.md", "checks/bound.py")
    assert parsed.files[-1].opening == "Bounds implied by the gain."
    assert es.render([], [], parsed.files, "# edges\n") == sheet, "round trip"


def test_a_code_key_passes_the_gate_as_a_file_target(tmp_path) -> None:
    paper = a_paper(tmp_path / "paper")
    files = es.sheet_files(paper, read_paper(paper).never_included)
    shown = es.parse(es.render([], [], files, "# edges\n"))
    assert _file_problem("checks/bound.py", files, shown) is None
    assert "write the file's name" in (_file_problem("bound", files, shown) or "")
