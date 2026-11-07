"""A kept state under a later reader, and the files an author adds.

A state kept by a reader that read one file fewer (a preamble it did not
follow) opens under today's: `blocks`, `states`, `check`, `sheet`, an
edit and `update` read it, its version the one it was kept as, and the
paper counts as edited only where the author edited it
(`docs/architecture.md` § 9). Then the three files an author may add: one the
paper includes (an edit), one it does not (kept, no edit), a code file
(the same). The synthetic paper of the edits' tests, built by a fake host
and kept by a replay; the earlier reader is today's with preamble
includes left unfollowed. No model is called.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

import test_person
from axiomation import versions
from axiomation.cli import main
from axiomation.reader import FileRole, files_hash, includes, read_paper
from axiomation.records import RecordStore
from questions.review_paper import MAIN
from test_edits import built_state, edited_edges, give

PREAMBLE = MAIN.replace("\\begin{document}", "\\input{macros}\n\\begin{document}", 1)
MACROS = "\\newcommand{\\gain}{g}\n"


def _run(args: list[str], capsys) -> tuple[int, str, str]:
    capsys.readouterr()
    code = main(args)
    out = capsys.readouterr()
    return code, out.out, out.err


@pytest.fixture
def earlier(tmp_path, capsys, monkeypatch) -> tuple[RecordStore, Path]:
    """The synthetic paper with a preamble file, `macros.tex`, built and
    kept by a reader that did not follow preamble includes: its version
    and its export name `main.tex` and `extra.tex` alone, and it keeps
    `macros.tex` as a file the reader never included."""
    made = test_person.make_paper

    def with_preamble(root, *args, **kwargs):
        root.mkdir(parents=True, exist_ok=True)
        (root / "macros.tex").write_text(MACROS, encoding="utf-8")
        return made(root, PREAMBLE)

    monkeypatch.setattr(test_person, "make_paper", with_preamble)
    real = includes._includes

    def unfollowed(path, source, directory):
        return [
            (include, target)
            for include, target in real(path, source, directory)
            if include.role is not FileRole.PREAMBLE
        ]

    with monkeypatch.context() as m:
        m.setattr(includes, "_includes", unfollowed)
        store, args = built_state(tmp_path, capsys)
    return store, Path(args[0])


def test_a_state_an_earlier_reader_kept_opens_under_today_s(
    earlier, capsys, tmp_path
) -> None:
    store, paper = earlier
    kept = versions.latest(store)
    assert kept is not None
    today = read_paper(paper)
    assert {f.path for f in today.files} == {"main.tex", "macros.tex", "extra.tex"}
    assert files_hash(today) != kept.version and "macros.tex" in kept.files
    where = ["--store", str(store.directory)]

    code, out, err = _run(["blocks", str(paper), *where], capsys)
    assert code == 0, err
    assert "not yet updated" not in out
    code, out, err = _run(["states", str(paper), *where, "--json"], capsys)
    assert json.loads(out)["files"] == {
        "version": kept.version,
        "state": kept.id,
        "pending": False,
    }
    code, out, _ = _run(["check", str(paper), *where], capsys)
    assert "dangling-references" in out and "were not read" not in out
    sheet = tmp_path / "edges.md"
    code, _, err = _run(
        ["sheet", str(paper), *where, "--stage", "edges", "-o", str(sheet)], capsys
    )
    assert code == 0, err
    code, _, err = _run(
        ["update", str(paper), *where, "--dry-run", "--json", "--host", "none"], capsys
    )
    assert code == 1 and "nothing to update" in err
    assert versions.latest(store) == kept


def test_an_edit_on_it_is_a_state_of_its_kept_version(earlier, capsys) -> None:
    store, paper = earlier
    before = versions.latest(store)
    assert before is not None

    given = give(store, edited_edges)

    assert not given.problems and given.state is not None
    assert given.state.version == before.version
    code, out, _ = _run(["blocks", str(paper), "--store", str(store.directory)], capsys)
    assert code == 0 and "not yet updated" not in out


def test_the_author_s_edit_of_it_is_pending_and_the_update_plans_it(
    earlier, capsys
) -> None:
    store, paper = earlier
    main_tex = paper / "main.tex"
    main_tex.write_text(
        main_tex.read_text(encoding="utf-8").replace(
            "A second sentence of the setting.", "A second, longer sentence."
        ),
        encoding="utf-8",
    )
    where = ["--store", str(store.directory)]

    code, out, _ = _run(["blocks", str(paper), *where], capsys)
    assert "an edit not yet updated" in out
    code, out, err = _run(["changes", str(paper), *where, "--json"], capsys)
    assert code == 0, err
    found = json.loads(out)
    assert found["pending"] is True
    # both sides read by today's reader: the preamble it now follows is
    # no change of text
    assert [i["class"] for i in found["text"]["items"]] == ["changed"]
    code, out, err = _run(
        ["update", str(paper), *where, "--dry-run", "--json", "--host", "none"], capsys
    )
    assert code == 0, err
    assert json.loads(out)["from_state"] == versions.latest(store).id


# --- the files an author adds ------------------------------------------------------


@pytest.fixture
def built(tmp_path, capsys) -> tuple[RecordStore, Path]:
    store, args = built_state(tmp_path, capsys)
    return store, Path(args[0])


def test_a_file_the_paper_includes_is_an_edit_its_blocks_added(built, capsys) -> None:
    store, paper = built
    (paper / "more.tex").write_text(
        "\\section{Further}\nA paragraph in the added file.\n", encoding="utf-8"
    )
    main_tex = paper / "main.tex"
    main_tex.write_text(
        main_tex.read_text(encoding="utf-8").replace(
            "\\input{extra}", "\\input{extra}\n\\input{more}"
        ),
        encoding="utf-8",
    )
    where = ["--store", str(store.directory)]

    code, out, err = _run(["changes", str(paper), *where, "--json"], capsys)

    assert code == 0, err
    found = json.loads(out)
    assert found["pending"] is True
    added = [i for i in found["text"]["items"] if i["class"] == "added"]
    assert added and all(
        b["block"].startswith("more.tex:") for i in added for b in i["new"]
    )


def test_a_preamble_the_paper_includes_is_an_edit_with_no_text_changed(
    built, capsys
) -> None:
    store, paper = built
    (paper / "macros.tex").write_text(MACROS, encoding="utf-8")
    (paper / "main.tex").write_text(PREAMBLE, encoding="utf-8")
    where = ["--store", str(store.directory)]

    code, out, err = _run(["changes", str(paper), *where, "--json"], capsys)

    assert code == 0, err
    found = json.loads(out)
    assert found["pending"] is True and found["text"]["items"] == []


def test_a_file_the_paper_does_not_include_is_kept_by_the_next_update_alone(
    built, capsys, tmp_path
) -> None:
    """No edit: the reader reads the same files. The next update keeps
    the new files as a state of their own, asking nothing; a sheet lists a
    file at the top level an edge may name (a figure), never a text file
    the reader does not include."""
    store, paper = built
    before = versions.latest(store)
    assert before is not None
    (paper / "notes").mkdir()
    (paper / "notes" / "todo.md").write_text("A note to self.\n", encoding="utf-8")
    (paper / "draft.tex").write_text("An unused draft.\n", encoding="utf-8")
    (paper / "figure.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    where = ["--store", str(store.directory)]

    code, out, _ = _run(["states", str(paper), *where, "--json"], capsys)
    assert json.loads(out)["files"]["pending"] is False
    code, out, err = _run(
        ["update", str(paper), *where, "--dry-run", "--json", "--host", "none"], capsys
    )
    assert code == 0, err
    assert "its files differ (draft.tex, figure.png, notes/todo.md)" in out
    code, out, err = _run(["update", str(paper), *where, "--host", "none"], capsys)
    assert code == 0, err
    after = versions.latest(store)
    assert after is not None and after.parent == before.id
    assert after.version == before.version
    assert {"draft.tex", "figure.png", "notes/todo.md"} <= set(after.files)
    sheet = tmp_path / "edges.md"
    assert (
        _run(
            ["sheet", str(paper), *where, "--stage", "edges", "-o", str(sheet)], capsys
        )[0]
        == 0
    )
    (files,) = [
        line
        for line in sheet.read_text(encoding="utf-8").split("\n")
        if line.startswith("Files: ")
    ]
    assert "figure.png" in files
    assert "draft.tex" not in files and "todo.md" not in files


def test_a_code_file_added_is_kept_alone_and_listed_with_its_opening_line(
    built, capsys, tmp_path
) -> None:
    store, paper = built
    before = versions.latest(store)
    assert before is not None
    (paper / "checks").mkdir()
    (paper / "checks" / "bound.py").write_text(
        '"""Checks the bound numerically."""\nprint(1)\n', encoding="utf-8"
    )
    where = ["--store", str(store.directory)]

    code, out, err = _run(["update", str(paper), *where, "--host", "none"], capsys)

    assert code == 0, err
    assert "files changed alone (checks/bound.py)" in out
    after = versions.latest(store)
    assert after is not None and after.version == before.version
    sheet = tmp_path / "edges.md"
    assert (
        _run(
            ["sheet", str(paper), *where, "--stage", "edges", "-o", str(sheet)], capsys
        )[0]
        == 0
    )
    assert "Code: checks/bound.py — Checks the bound numerically." in sheet.read_text(
        encoding="utf-8"
    )


def test_a_paper_beside_a_state_today_s_reader_cannot_read_is_not_called_edited(
    built, capsys
) -> None:
    """Where the kept files the paper is compared with do not read (here
    an export naming a file today's reader does not read), nothing is said
    of the paper either way, and the read still runs."""
    store, paper = built
    before = versions.latest(store)
    assert before is not None
    export = json.loads(versions.kept_export(store, before))
    export["document"]["files"].append(
        {"path": "notes.tex", "sha256": hashlib.sha256(b"A note.\n").hexdigest()}
    )
    files = {k: versions.blob(store, v) for k, v in before.files.items()}
    versions.record(
        store,
        {**files, "notes.tex": b"A note.\n"},
        "f" * 64,
        before.root,
        json.dumps(export).encode(),
        versions.Command.BUILD,
    )

    code, out, err = _run(
        ["blocks", str(paper), "--store", str(store.directory)], capsys
    )

    assert code == 0, err
    assert "not yet updated" not in out


def test_a_state_opens_under_a_reader_that_reads_its_preamble_no_more(
    tmp_path, capsys, monkeypatch
) -> None:
    """The other way round: kept by a reader that followed the preamble,
    read by one that does not. The preamble declared and held no block,
    so the state opens; the paper is no edit."""
    made = test_person.make_paper

    def with_preamble(root, *args, **kwargs):
        root.mkdir(parents=True, exist_ok=True)
        (root / "macros.tex").write_text(MACROS, encoding="utf-8")
        return made(root, PREAMBLE)

    monkeypatch.setattr(test_person, "make_paper", with_preamble)
    store, args = built_state(tmp_path, capsys)
    kept = versions.latest(store)
    assert kept is not None
    real = includes._includes
    monkeypatch.setattr(
        includes,
        "_includes",
        lambda path, source, directory: [
            (i, t)
            for i, t in real(path, source, directory)
            if i.role is not FileRole.PREAMBLE
        ],
    )

    found = versions.document_at(store, kept)

    assert "macros.tex" not in {f.path for f in found.files}
    assert found.version == kept.version
    code, out, err = _run(["blocks", args[0], "--store", str(store.directory)], capsys)
    assert code == 0, err
    assert "not yet updated" not in out


def test_a_paper_changed_between_an_update_s_two_reads_is_refused(built) -> None:
    """The update reads the paper again with its map, perhaps minutes after
    a map question: the version first named must still be its text's."""
    from axiomation.commands import UsageError
    from axiomation.commands.update import _read_again

    _, paper = built
    first = replace(read_paper(paper), version="f" * 64)
    assert _read_again(first, read_paper(paper)).version == "f" * 64
    (paper / "extra.tex").write_text("\\section{More}\nChanged.\n", encoding="utf-8")
    with pytest.raises(UsageError, match="changed while it was read"):
        _read_again(first, read_paper(paper))


def test_a_vocabulary_edit_on_it_stays_on_its_kept_version(
    tmp_path, capsys, monkeypatch
) -> None:
    """`edit --stage vocabulary` applies the map by an update of the
    version onto itself: its answer and its identity answer name the
    state's kept version on both sides, though today's reader reads one
    file more."""
    import test_edits
    from axiomation import edits
    from axiomation.commands import Exit

    made = test_edits.make_paper

    def with_preamble(root, *args, **kwargs):
        root.mkdir(parents=True, exist_ok=True)
        (root / "macros.tex").write_text(MACROS, encoding="utf-8")
        return made(root, PREAMBLE, **kwargs)

    monkeypatch.setattr(test_edits, "make_paper", with_preamble)
    real = includes._includes
    with monkeypatch.context() as m:
        m.setattr(
            includes,
            "_includes",
            lambda path, source, directory: [
                (i, t)
                for i, t in real(path, source, directory)
                if i.role is not FileRole.PREAMBLE
            ],
        )
        root, store = test_edits.boxed_state(tmp_path, capsys)
    first = versions.latest(store)
    assert first is not None
    assert files_hash(read_paper(root)) != first.version
    with edits.opened(store, first) as kept:
        shown = edits.vocabulary_shown(kept)
    sheet = tmp_path / "vocabulary.md"
    sheet.write_text(shown.replace("- kind: none", "- kind: container"), "utf-8")

    code, _, err = _run(
        ["edit", str(root), "--stage", "vocabulary", "--file", str(sheet)], capsys
    )

    assert code == Exit.UNANSWERED, err
    given = [a for a in store.answers() if a.kind == "vocabulary"][-1]
    assert given.version == first.version
    identity = [a for a in store.answers() if a.kind == "identity"][-1]
    raw = versions.identity_raw(store, identity)
    assert raw["old"] == raw["new"] == first.version
