"""A block's handle across kept states (`docs/model.md` § 7, its aliases
`docs/architecture.md` § 9): kept while
its node lives, its other names aliases, never given to another node. The
synthetic paper built into its own store, then updated in place with a
block's opening words changed and another block deleted; by today's rule,
and from a cut file `diff --cut` is given, which no state keeps (its
update's sheets named by the words). No model is called.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

import pytest

from axiomation import edits, versions
from axiomation.changes import Layer
from axiomation.cli import BUILD_STAGES, main
from axiomation.driver import ReviewSettings
from axiomation.queries import QueryError, View, show
from axiomation.questions.edges_file import EDGES_FILE, plain_cut_applied
from axiomation.reader import read_paper
from axiomation.records import RecordStore
from axiomation.resolver import Resolver
from axiomation.runner import Runner
from axiomation.update import UpdateSettings, plan
from axiomation.update import run as run_update
from questions.review_paper import MAIN
from test_update import FILES, FLAT, answering, stepping
from test_versions import built

KEPT, NOW = "we-fix-a-field", "now-we-fix-a"
CLOSING = "closing-words-of-the"
REWORDED = MAIN.replace("We fix a field of scalars.", "Now we fix a field of scalars.")
#: the closing paragraph deleted, and the setting reworded
SECOND = REWORDED.replace("Closing words of the results.\n", "")
#: the closing paragraph's words back, a new block
THIRD = SECOND.replace(
    "\\input{extra}", "Closing words of the results.\n\\input{extra}"
)
DELTA = ["Δ reworded — it now says when"]


def _updated(
    root: Path,
    store: RecordStore,
    text: str,
    reworded: bool = True,
    lines: Sequence[str] = (),
) -> versions.State:
    """`root` edited to `text` and updated from its latest state, the
    session answering, where the setting is `reworded`, a Δ on it whatever
    its sheet names it, and `lines` under its kept handle."""
    (root / "main.tex").write_text(text, encoding="utf-8")
    assert main(["update", str(root), "--host", "none"]) == 5
    (key,) = [
        a.key
        for a in store.authoritative().values()
        if a.kind == "identity"
        and a.key not in {s.identity for s in versions.states(store)}
    ]
    settings = UpdateSettings(
        files=FILES,
        review=ReviewSettings(stages=BUILD_STAGES, files=FILES),
        paper=root.resolve(),
    )
    deltas = {KEPT: [*lines, *DELTA], NOW: DELTA} if reworded else {}
    host = stepping(answering(deltas))
    answered = run_update(
        read_paper(root),
        store,
        settings,
        Runner(host, Resolver(store), 10.0, FLAT),
        identity=key,
    )
    assert answered.complete, answered.unanswered
    assert main(["update", str(root), "--host", "none"]) == 0
    return versions.latest(store)


def _kept_from_a_cut_file(tmp_path: Path, root: Path, store: RecordStore) -> None:
    """The update's identity answer made from an export no state keeps byte
    for byte: no kept handle is carried, its sheets named by the words."""
    exported = json.loads((tmp_path / "old.json").read_text(encoding="utf-8"))
    other = tmp_path / "old-elsewhere.json"
    other.write_text(json.dumps(exported, indent=1), encoding="utf-8")
    (root / "main.tex").write_text(SECOND, encoding="utf-8")
    args = ["diff", str(tmp_path / "old"), str(root), "--cut", str(other)]
    assert main([*args, "--store", str(store.directory)]) == 0


@pytest.fixture
def paper(tmp_path, capsys):
    root, directory, _ = built(tmp_path, capsys)
    return root, RecordStore(directory)


@pytest.mark.usefixtures("develop_mode")
def test_an_update_keeps_a_reworded_block_s_handle(paper) -> None:
    """The setting's opening words reworded: they now give `now-we-fix-a`;
    the update's sheets and its export name it `we-fix-a-field`, which an
    agent's notes from before still name, and the new slug resolves to it."""
    root, store = paper
    first = versions.latest(store)
    setting = first.node_of(View(store, first).resolve(KEPT))

    second = _updated(root, store, SECOND)

    view = View(store, second)
    ident = view.resolve(KEPT)
    assert second.node_of(ident) == setting
    assert view.handles[ident] == KEPT and view.handle(ident) == KEPT
    assert view.resolve(NOW) == ident
    with pytest.raises(QueryError, match=r"ended at state \w+ \(deleted\)"):
        view.resolve(CLOSING)


@pytest.mark.usefixtures("develop_mode")
def test_a_new_block_never_takes_an_ended_node_s_handle(paper) -> None:
    root, store = paper
    first = versions.latest(store)
    ended = first.node_of(View(store, first).resolve(CLOSING))
    _updated(root, store, SECOND)

    third = _updated(root, store, THIRD, reworded=False)

    view = View(store, third)
    began = view.resolve(f"{CLOSING}-2")
    assert third.node_of(began) != ended
    with pytest.raises(QueryError, match=f"names {ended}, which ended") as refused:
        view.resolve(CLOSING)
    assert f"now open {third.node_of(began)} ({CLOSING}-2)" in str(refused.value)
    assert versions.handles(store).holder[CLOSING] == ended


@pytest.mark.usefixtures("develop_mode")
def test_a_state_kept_from_a_cut_file_is_read_and_edited_by_its_kept_handles(
    tmp_path, paper, capsys
) -> None:
    """Its export names the setting `now-we-fix-a`; every read and the
    sheet name it `we-fix-a-field`, and an edit naming that lands on it."""
    root, store = paper
    _kept_from_a_cut_file(tmp_path, root, store)
    second = _updated(root, store, SECOND)
    view = View(store, second)
    ident = view.resolve(KEPT)
    assert view.handles[ident] == NOW and view.handle(ident) == KEPT
    assert view.resolve(NOW) == ident
    with edits.opened(store, second) as kept:
        sheet = edits.shown(kept, Layer.EDGES)
    assert f"⟦{KEPT} " in sheet and f"⟦{NOW} " not in sheet

    line = '→ uses def:gain · "Now we fix a field of scalars."'
    args = [str(root), "--add", KEPT, line]
    assert main(["change", *args]) == 0, capsys.readouterr()

    third = versions.latest(store)
    assert third.id != second.id
    export = json.loads(versions.kept_export(store, third))
    assert export["handles"][ident] == KEPT
    assert any(e["source"] == ident and e["kind"] == "uses" for e in export["edges"])
    with edits.opened(store, third) as kept:
        assert f"⟦{KEPT} " in edits.shown(kept, Layer.EDGES)


@pytest.mark.usefixtures("develop_mode")
def test_history_names_each_step_s_handle_and_show_its_aliases(
    tmp_path, paper, capsys
) -> None:
    root, store = paper
    _kept_from_a_cut_file(tmp_path, root, store)
    second = _updated(root, store, SECOND)

    capsys.readouterr()
    assert main(["history", str(root), KEPT]) == 0
    steps = json.loads(capsys.readouterr().out)
    assert [(s["change"], s["handle"]) for s in steps] == [
        ("built", KEPT),
        ("changed", NOW),
    ]
    view = View(store, second)
    found = show(
        view, KEPT, steps=versions.history(store, view.node(view.resolve(KEPT)))
    )
    assert [i.data for i in found.sections["handles"]] == [{"alias": NOW, "now": True}]


@pytest.mark.usefixtures("develop_mode")
def test_a_replay_that_cannot_apply_an_edit_again_records_nothing(
    tmp_path, paper, capsys
) -> None:
    """An update made from a cut no state keeps named the setting by its
    words; an edit since named it by its kept handle. The replay of that
    update with the edits given since shows another sheet than the edit was
    given on, so it cannot apply it: it is refused, not recorded without it."""
    root, store = paper
    _kept_from_a_cut_file(tmp_path, root, store)
    _updated(root, store, SECOND)
    line = '→ uses def:gain · "Now we fix a field of scalars."'
    assert main(["change", str(root), "--add", KEPT, line]) == 0
    before = versions.states(store)
    capsys.readouterr()

    assert main(["update", str(root), "--host", "none"]) != 0

    assert "cannot apply again 1 edits" in capsys.readouterr().err
    assert versions.states(store) == before


def test_an_alias_two_nodes_bore_resolves_to_neither(tmp_path) -> None:
    from test_versions import V1, export, keep

    text = "Pp. Qq."
    blocks = [("a", "Pp."), ("b", "Qq.")]
    store = RecordStore(tmp_path / "store")
    first = keep(store, V1, text, export(text, blocks, handles={"a": "p", "b": "q"}))
    renamed = export(text, blocks, handles={"a": "x", "b": "q"})
    second = keep(store, V1, text, renamed, versions.Command.EDIT, first)
    again = export(text, blocks, handles={"a": "p", "b": "x"})
    third = keep(store, V1, text, again, versions.Command.EDIT, second)

    view = View(store, third)
    assert view.resolve("p") == "a" and view.handle("b") == "q"
    with pytest.raises(QueryError, match="'x' was a handle of several nodes"):
        view.resolve("x")


@pytest.mark.usefixtures("develop_mode")
def test_an_update_session_s_cut_line_keeps_its_sheet_s_handles(
    tmp_path, paper
) -> None:
    """From a state its update kept with no handle carried (its export
    names the setting `now-we-fix-a`), the next update's one session is shown the
    setting as `we-fix-a-field`; its gate reads a split under that name,
    and carries the sheet past it, by the names it showed."""
    root, store = paper
    _kept_from_a_cut_file(tmp_path, root, store)
    _updated(root, store, SECOND)
    edited = SECOND.replace(
        "A second sentence of the setting.", "A second sentence of the setting, edited."
    )
    (root / "main.tex").write_text(edited, encoding="utf-8")
    assert main(["update", str(root), "--host", "none"]) == 5
    settings = UpdateSettings(
        files=FILES, limit=None, per_stage_update_above_recut_blocks=1000, paper=root
    )
    _, made = plan(read_paper(root), store, settings)
    assert made.question is not None
    shown = made.question.workspace.files[EDGES_FILE]
    assert f"⟦{KEPT} " in shown and f"⟦{NOW} " not in shown
    split = (
        '✂ split "A second sentence" · '
        '"A second sentence of the setting, edited." — its own sentence'
    )
    text = answering({KEPT: [split, *DELTA]})(shown)

    found = plain_cut_applied(
        json.loads(made.question.workspace.gate.spec), text, read_paper(root)
    )

    assert found is not None and found.sheet is not None, found and found.message
    assert f"⟦{KEPT} " in found.sheet and f"⟦{NOW} " not in found.sheet
    assert "⟦a-second-sentence-of " in found.sheet
