"""An edge onto a block an update deleted: the writer is shown it, in its
block's reason and in its task, and where no line takes its place,
`changes` and the update's summary list it. On the synthetic paper, its
closing paragraph in the other file using the closing words, which the
edit deletes. No model is called.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from axiomation import versions
from axiomation.cli import BUILD_STAGES, main
from axiomation.driver import ReviewSettings, review_cut
from axiomation.questions.edges_file import EDGES_FILE, Gone
from axiomation.reader import read_paper
from axiomation.records import RecordStore
from axiomation.resolver import Resolver
from axiomation.runner import Runner
from axiomation.update import UpdateSettings, folded, lost_marks, plan, queue_shown
from axiomation.update import run as run_update
from axiomation.versions import STORE_DIRECTORY
from questions.review_paper import MAIN, make_paper
from questions.test_edges_file import written
from test_update import (
    CHAIN_LEGEND,
    CITED,
    CLOSING_PARAGRAPH,
    FILES,
    FLAT,
    add_lines,
    answering,
    host,
    stepping,
    update,
)
from test_update import (
    versions as two_papers,
)
from test_update_single import PLAIN_SETTINGS, review_update
from test_versions import build_host

LOST = '→ uses closing-words-of-the · "A closing paragraph"'
ABOUT = '→ about def:gain · "A closing paragraph"'
REDRAWN = '→ uses def:gain · "A closing paragraph"'
#: The edit: the closing words deleted.
DELETED = MAIN.replace("Closing words of the results.\n", "")


def test_the_update_of_one_session_reads_the_lost_line_s_source_again(
    tmp_path,
) -> None:
    v = two_papers(tmp_path, DELETED, CITED, CHAIN_LEGEND)

    _, made = plan(v.new.document, v.store, PLAIN_SETTINGS)

    assert made.single_session
    assert made.reopened[CLOSING_PARAGRAPH].why.endswith(f"is gone: `{LOST}`")
    # its queue task says why, the lost line with it
    reread = queue_shown(made, made.question).reread[CLOSING_PARAGRAPH]
    assert reread["why"].endswith(f"is gone: `{LOST}`")


def test_the_update_of_one_session_marks_a_lost_edge_it_did_not_replace(
    tmp_path,
) -> None:
    owed: dict[str, list[str]] = {}
    left = two_papers(tmp_path / "left", DELETED, CITED, CHAIN_LEGEND)
    drawn = two_papers(tmp_path / "drawn", DELETED, CITED, CHAIN_LEGEND)

    found = update(left, host(answering(owed)), PLAIN_SETTINGS)
    again = update(
        drawn,
        host(answering(owed, lambda t: add_lines(t, {CLOSING_PARAGRAPH: [REDRAWN]}))),
        PLAIN_SETTINGS,
    )

    assert found.complete, [a.problems for a in left.store.answers() if not a.valid]
    assert again.complete, [a.problems for a in drawn.store.answers() if not a.valid]
    (entry,) = review_update(left, found)["lost"]
    assert (entry["handle"], entry["cleared_by"]) == (CLOSING_PARAGRAPH, None)
    assert "lost" not in review_update(drawn, again)


def built(tmp_path: Path, capsys) -> Path:
    """The paper built into its own store, the closing paragraph using the
    closing words, kept as a state by a replay; then the closing words
    deleted in place."""
    paper = make_paper(tmp_path / "paper")
    store = RecordStore(paper.root / STORE_DIRECTORY)
    host = build_host()
    host.edits[EDGES_FILE] = lambda text: written(
        text, {CLOSING_PARAGRAPH: [ABOUT, LOST]}, []
    )
    review = review_cut(
        paper.document,
        paper.cut,
        store,
        ReviewSettings(stages=BUILD_STAGES, files=FILES),
        Runner(host, Resolver(store), 10.0, FLAT),
    )
    assert review.complete, review.unanswered
    capsys.readouterr()
    assert main(["build", str(paper.root), "--host", "none", "--json"]) == 0
    capsys.readouterr()
    (paper.root / "main.tex").write_text(DELETED, encoding="utf-8")
    return paper.root


def updated(tmp_path: Path, capsys, lines: dict[str, list[str]]) -> tuple[Path, dict]:
    """The update answered with `lines` added, then kept by the command's
    replay: the paper and the replay's summary."""
    root = built(tmp_path, capsys)
    store = RecordStore(root / STORE_DIRECTORY)
    assert main(["update", str(root), "--host", "none"]) == 5
    capsys.readouterr()
    (key,) = [a.key for a in store.authoritative().values() if a.kind == "identity"]
    made = run_update(
        read_paper(root),
        store,
        UpdateSettings(
            files=FILES,
            review=ReviewSettings(stages=BUILD_STAGES, files=FILES),
            paper=root.resolve(),
        ),
        Runner(
            stepping(
                answering(
                    {
                        CLOSING_PARAGRAPH: [
                            'Δ unchanged — "A closing paragraph": it reads the same'
                        ]
                    },
                    lambda t: add_lines(t, lines),
                )
            ),
            Resolver(store),
            10.0,
            FLAT,
        ),
        identity=key,
    )
    assert made.complete, [a.problems for a in store.answers() if not a.valid]
    assert main(["update", str(root), "--host", "none", "--json"]) == 0
    return root, json.loads(capsys.readouterr().out)


def changes(root: Path, capsys) -> dict:
    capsys.readouterr()
    assert main(["changes", str(root), "--json"]) == 0
    return json.loads(capsys.readouterr().out)


@pytest.mark.usefixtures("develop_mode")
def test_an_edge_no_line_replaces_is_listed_to_look_at(tmp_path, capsys) -> None:
    root, summary = updated(tmp_path, capsys, {})

    (lost,) = summary["lost"]
    assert lost.startswith(f"! {CLOSING_PARAGRAPH} {LOST}")
    assert lost.endswith(
        f"now: nothing from its source ({CLOSING_PARAGRAPH}) in its place"
    )
    found = changes(root, capsys)
    (look,) = found["to_look_at"]["items"]
    assert look["why"] == "its block was deleted"
    assert look["old"]["edge"]["kind"] == "uses" and look["now"] == []
    # listed once: to look at, not again as ended
    assert all(
        i.get("edge", {}).get("id") != look["old"]["edge"]["id"]
        for i in found["edges"]["items"]
    )


@pytest.mark.usefixtures("develop_mode")
def test_an_edge_drawn_again_elsewhere_is_not_listed(tmp_path, capsys) -> None:
    root, summary = updated(tmp_path, capsys, {CLOSING_PARAGRAPH: [REDRAWN]})

    assert summary["lost"] == []
    assert changes(root, capsys)["to_look_at"]["items"] == []
    states = versions.states(RecordStore(root / STORE_DIRECTORY))
    assert [s.command.value for s in states] == ["build", "update"]
    store = RecordStore(root / STORE_DIRECTORY)
    assert "lost" not in record(store)
    assert opened(root, capsys) == []


# --- the mark: open until a person or an agent settles it ----------------------


def record(store: RecordStore) -> dict:
    latest = versions.latest(store)
    assert latest is not None
    return json.loads(versions.kept_export(store, latest))["review"]["update"]


def opened(root: Path, capsys) -> list[dict]:
    capsys.readouterr()
    assert main(["open", str(root), "--kind", "stale", "--json"]) == 0
    return json.loads(capsys.readouterr().out)["stale"]["items"]


def settled(root: Path, capsys, *change: str) -> dict:
    """A person's `change` on the update's state, then the update run
    again: it gives the edit's state byte for byte. Its report, as JSON."""
    capsys.readouterr()
    code = main(["change", str(root), *change, "--json"])
    out = capsys.readouterr()
    assert code == 0, out.err
    report = json.loads(out.out)
    out = root.parent / "rerun.json"
    capsys.readouterr()
    assert main(["update", str(root), "--host", "none", "-o", str(out)]) == 0
    store = RecordStore(root / STORE_DIRECTORY)
    latest = versions.latest(store)
    assert latest is not None and latest.command.value == "edit"
    assert out.read_bytes() == versions.kept_export(store, latest)
    return report


def reread(root: Path, capsys, owner: str) -> None:
    capsys.readouterr()
    code = main(["reread", str(root), owner, "--reason", "the lost line is settled"])
    assert code == 0, capsys.readouterr().err


@pytest.mark.usefixtures("develop_mode")
def test_a_lost_edge_marks_its_source_stale(tmp_path, capsys) -> None:
    root, _ = updated(tmp_path, capsys, {})

    (entry,) = record(RecordStore(root / STORE_DIRECTORY))["lost"]
    assert entry["handle"] == CLOSING_PARAGRAPH and entry["cleared_by"] is None
    (cause,) = entry["causes"]
    assert cause["why"].endswith(
        "is gone, and the update's session drew no line in its place"
    )
    assert cause["from"].startswith("deleted:main.tex:")
    assert cause["via"] == f"{CLOSING_PARAGRAPH} {LOST}"
    (item,) = opened(root, capsys)
    assert item["handle"] == CLOSING_PARAGRAPH
    capsys.readouterr()
    assert main(["check", str(root), "--json"]) == 0
    gates = json.loads(capsys.readouterr().out)["gates"]
    (unreviewed,) = [g for g in gates if g["gate"] == "unreviewed"]
    assert unreviewed["outcome"] == "warn"
    assert [(i["kind"], i["handle"]) for i in unreviewed["items"]] == [
        ("stale", CLOSING_PARAGRAPH)
    ]


@pytest.mark.usefixtures("develop_mode")
def test_a_lost_edge_drawn_again_by_change_keeps_its_mark_until_a_reread(
    tmp_path, capsys
) -> None:
    """`change` is shown no mark, so it clears none: its report lists the
    mark still open on the owner it touched, and a reread clears it."""
    root, _ = updated(tmp_path, capsys, {})

    report = settled(root, capsys, "--add", CLOSING_PARAGRAPH, REDRAWN)

    (entry,) = record(RecordStore(root / STORE_DIRECTORY))["lost"]
    assert entry["cleared_by"] is None
    (item,) = report["marks"]["items"]
    assert item["change"] == "open" and item["handle"] == CLOSING_PARAGRAPH
    (still,) = opened(root, capsys)
    reread(root, capsys, still["node"])
    assert opened(root, capsys) == []


@pytest.mark.usefixtures("develop_mode")
def test_a_lost_edge_kept_gone_by_change_keeps_its_mark_until_a_reread(
    tmp_path, capsys
) -> None:
    root, _ = updated(tmp_path, capsys, {})

    settled(
        root,
        capsys,
        *("--keep", CLOSING_PARAGRAPH, ABOUT),
        *("--reason", "the closing words are gone, and nothing used them"),
    )

    (still,) = opened(root, capsys)
    reread(root, capsys, still["node"])
    assert opened(root, capsys) == []


@pytest.mark.usefixtures("develop_mode")
def test_a_lost_edge_drawn_again_in_the_sheet_clears_its_mark(tmp_path, capsys) -> None:
    """`edit` shows the sheet with the mark beside its owner: drawing the
    line there clears it, and the report says so."""
    root, _ = updated(tmp_path, capsys, {})
    sheet = root.parent / "edges.md"
    capsys.readouterr()
    assert main(["sheet", str(root), "--stage", "edges", "-o", str(sheet)]) == 0
    text = sheet.read_text(encoding="utf-8")
    head = f"⟦{CLOSING_PARAGRAPH} "
    at = text.index("\n⟦done", text.index(head))
    sheet.write_text(text[:at] + "\n" + REDRAWN + text[at:], encoding="utf-8")
    capsys.readouterr()
    code = main(["edit", str(root), "--stage", "edges", "--file", str(sheet), "--json"])
    out = capsys.readouterr()
    assert code == 0, out.err

    (item,) = json.loads(out.out)["marks"]["items"]
    assert item["change"] == "cleared" and item["handle"] == CLOSING_PARAGRAPH
    assert opened(root, capsys) == []


# --- the count rule and the fold, on their own ------------------------------------


def sheet(*lines: str) -> str:
    """A sheet of one block, `src`, holding `lines`."""
    head = ["⟦src none⟧ x.tex:1:1 (prose)", "Text.", ""]
    return "\n".join([*head, *lines, "⟦done src⟧"])


def lost_of(answer: str) -> dict:
    gone = tuple(
        Gone("src", f'→ uses {t} · "Text."', t, f"x.tex:{n}:1")
        for n, t in enumerate(("gone-a", "gone-b"), 2)
    )
    made = SimpleNamespace(
        carried=SimpleNamespace(gone=gone, sheet=sheet('→ uses kept · "Text."')),
        single_session=False,
    )
    return lost_marks(made, answer, None)


def test_two_edges_lost_and_one_drawn_in_their_place_marks_both() -> None:
    one = lost_of(sheet('→ uses kept · "Text."', '→ uses new-a · "Text."'))
    both = lost_of(
        sheet(
            '→ uses kept · "Text."', '→ uses new-a · "Text."', '→ uses new-b · "Text."'
        )
    )
    about = lost_of(sheet('→ about new-a · "Text."', '→ about new-b · "Text."'))

    assert [c.origin for c in one["src"]] == ["deleted:x.tex:2:1", "deleted:x.tex:3:1"]
    assert both == {}
    # a line of another kind stands in no lost edge's place
    assert len(about["src"]) == 2


def test_a_model_s_re_ask_clears_a_stale_mark_but_not_a_lost_edge_s() -> None:
    entry = {"handle": "src", "causes": [], "cleared_by": None}
    record = {"deltas": [], "stale": [dict(entry)], "lost": [dict(entry)]}
    reask = SimpleNamespace(
        key="re-ask",
        handles={"src": "x.tex:1:1"},
        workspace=SimpleNamespace(
            gate=SimpleNamespace(spec=json.dumps({"reopened": ["src"]}))
        ),
    )

    found, _ = folded(
        record,
        [(reask, SimpleNamespace(marks_shown=None), "")],
        None,
        lambda ident: "src",
        lambda question: lambda block: block,
        None,
    )

    assert found["stale"][0]["cleared_by"] == "re-ask"
    assert found["lost"][0]["cleared_by"] is None
