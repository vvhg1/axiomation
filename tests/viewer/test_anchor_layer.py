"""The test layer: a finding at each anchor
kind, filed by `axiomation find`, and an overlay with an item at each kind
on each channel, each placed on the kept text by the page's own
`anchors.js` and listed in the open view; one that does not place kept,
unanchored, at its block. Offline, synthetic, no model."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from test_viewer_js import PAGE, _node

from axiomation import versions
from axiomation.cli import main
from axiomation.cutter import CutRules, DisplayRule, cut
from axiomation.exchange import cut_payload, dumps
from axiomation.queries import View, open_report, render, show
from axiomation.reader import document_hash, read_paper
from axiomation.records import AnchorKind, RecordStore
from axiomation.versions import Command
from axiomation.viewer.overlays import Channel, OverlayError, read_overlay
from axiomation.viewer.payload import text_layer

PAPER = (
    "\\documentclass{article}\n\\begin{document}\n\\section{One}\n"
    "\\begin{theorem}\\label{thm:a}\nEvery set here is finite.\n\\end{theorem}\n\n"
    "\\begin{proof}\nBy \\cite{gauss1801} it is so. Then we conclude.\n\\end{proof}\n\n"
    "Some prose follows the proof.\n\\end{document}\n"
)
SCRIPT = '"""Counts the sets."""\nprint(3)\n'


@pytest.fixture
def kept(tmp_path):
    root = tmp_path / "paper"
    root.mkdir()
    (root / "main.tex").write_text(PAPER, encoding="utf-8")
    (root / "checks").mkdir()
    (root / "checks" / "count.py").write_text(SCRIPT, encoding="utf-8")
    document = read_paper(root)
    found = cut(document, CutRules(DisplayRule.IN_PARAGRAPH))
    payload = cut_payload(document, found)
    # the groups stage's proof group, by hand: the cutter makes none
    proof = [b.id for b in found.blocks if "gauss1801" in document.text(b.owned[0])]
    payload["groups"] = [
        {
            "id": f"proof@{proof[0]}",
            "kind": "proof",
            "blocks": proof,
            "names": None,
            "named_label": None,
            "named_by": None,
            "role": "justification",
        }
    ]
    # the prose rests on the script, the edge laid by hand
    prose = next(
        b.id for b in found.blocks if "Some prose" in document.text(b.owned[0])
    )
    words = {"file": "main.tex", "start": [12, 6], "end": [12, 23]}
    payload["edges"] = [
        {
            "id": "e1",
            "source": prose,
            "evidence": [{"text": "follows the proof", "at": words}],
            "target": {"file": "checks/count.py"},
            "kind": "uses",
            "provenance": {"by": "edges"},
        }
    ]
    store = RecordStore(tmp_path / "store")
    state, _ = versions.record(
        store,
        versions.snapshot(root, document),
        document_hash(document),
        document.root,
        dumps(payload).encode(),
        Command.BUILD,
    )
    return root, store, View(store, state)


def _anchors(view: View) -> dict[str, dict]:
    """One anchor of each kind on the paper: its block, group, part, span,
    the paper and the work it cites."""
    theorem = next(b for b in view.blocks if "finite" in view.texts[b])
    proof_block = next(b for b in view.blocks if "gauss1801" in view.texts[b])
    group = next(iter(view.groups))
    return {
        "block": {"kind": "block", "node": view.node(theorem)},
        "group": {"kind": "group", "node": view.node(group)},
        "part": {
            "kind": "part",
            "node": view.node(theorem),
            "words": "Every set here is finite.",
        },
        "span": {"kind": "span", "node": view.node(proof_block), "words": "it is so"},
        "paper": {"kind": "paper"},
        "work": {"kind": "work", "work": "gauss1801"},
        "file": {"kind": "file", "file": "checks/count.py", "words": "print(3)"},
    }


def _placed(layer: dict, anchors: list[dict | None], tmp_path: Path) -> list[dict]:
    """Each anchor as the page's `anchors.js` places it on the layer."""
    given = tmp_path / "given.json"
    given.write_text(json.dumps({"layer": layer, "anchors": anchors}), encoding="utf-8")
    script = (
        f"const N = require({json.dumps(str(PAGE / 'anchors.js'))});"
        "const D = JSON.parse(require('fs').readFileSync("
        f"{json.dumps(str(given))}, 'utf-8'));"
        "const at = N.index(D.layer);"
        "console.log(JSON.stringify(D.anchors.map((a) => N.resolve(at, a))));"
    )
    out = subprocess.run(
        [_node(), "-e", script], capture_output=True, text=True, check=True
    )
    return json.loads(out.stdout)


def test_a_finding_at_each_anchor_kind_places_on_the_text(
    kept, tmp_path, capsys
) -> None:
    root, store, view = kept
    anchors = _anchors(view)
    handle = {view.node(x): view.handle(x) for x in [*view.blocks, *view.groups]}
    flags = {
        "block": ["--on", handle[anchors["block"]["node"]]],
        "group": ["--on", handle[anchors["group"]["node"]]],
        "part": ["--part", handle[anchors["part"]["node"]], anchors["part"]["words"]],
        "span": ["--span", handle[anchors["span"]["node"]], anchors["span"]["words"]],
        "paper": ["--paper"],
        "work": ["--work", "gauss1801"],
        "file": ["--file", "checks/count.py", "print(3)"],
    }
    for kind, given in flags.items():
        code = main(
            [
                "find",
                str(root),
                "--store",
                str(store.directory),
                "--aspect",
                "accurate",
                *given,
                "--severity",
                "3",
                "--problem",
                f"a problem at the {kind}",
            ]
        )
        assert code == 0, capsys.readouterr().err
    view = View(store, versions.latest(store))
    report = json.loads(render(open_report(view), 0, as_json=True))
    findings = report["finding"]["items"]
    assert sorted(f["anchor"]["kind"] for f in findings) == sorted(
        k.value for k in AnchorKind
    )
    placed = _placed(text_layer(view), [f["anchor"] for f in findings], tmp_path)
    assert all(p["placed"] for p in placed), placed
    assert {p["kind"] for p in placed} == {k.value for k in AnchorKind}

    # one finding on two blocks: its item carries both anchors,
    # the gutter marks both blocks, and each block's card lists it
    theorem, proof = handle[anchors["block"]["node"]], handle[anchors["span"]["node"]]
    code = main(
        [
            "find",
            str(root),
            "--store",
            str(store.directory),
            "--aspect",
            "true",
            "--on",
            theorem,
            "--span",
            proof,
            "it is so",
            "--severity",
            "2",
            "--problem",
            "a problem on two blocks",
        ]
    )
    assert code == 0, capsys.readouterr().err
    view = View(store, versions.latest(store))
    report = json.loads(render(open_report(view), 0, as_json=True))
    (two,) = [f for f in report["finding"]["items"] if len(f["anchors"]) == 2]
    assert two["severity"] == 2 and two["anchor"] == two["anchors"][0]
    marked = _marked(text_layer(view), report, tmp_path)
    blocks = {view.node(b): b for b in view.blocks}
    assert marked["blocks"][two["finding"]] == [
        blocks[anchors["block"]["node"]],
        blocks[anchors["span"]["node"]],
    ]
    # a block's finding mark stands for every open finding placed on it
    on = {}
    for placed_on in marked["blocks"].values():
        for b in placed_on:
            on[b] = on.get(b, 0) + 1
    assert marked["counts"] == on
    for f in report["finding"]["items"]:
        assert f["kind"] == "finding" and f["anchors"], f
    for ref in (theorem, proof):
        shown = json.loads(render(show(view, ref), 0, as_json=True))
        assert two["finding"] in [f["id"] for f in shown["findings"]["items"]], ref


def _marked(layer: dict, report: dict, tmp_path: Path) -> dict:
    """The open findings as the page's `anchors.blocksOf` and
    `gutter.openMarks` place them by block: the blocks each finding stands
    at (`blocks`), and how many findings each block's mark stands for
    (`counts`). The page marks the rows their words start on, measured in
    the browser; the blocks are what the measurement starts from."""
    given = tmp_path / "marked.json"
    given.write_text(json.dumps({"layer": layer, "report": report}), encoding="utf-8")
    script = (
        f"const N = require({json.dumps(str(PAGE / 'anchors.js'))});"
        f"const G = require({json.dumps(str(PAGE / 'gutter.js'))});"
        f"const O = require({json.dumps(str(PAGE / 'open.js'))});"
        "const D = JSON.parse(require('fs').readFileSync("
        f"{json.dumps(str(given))}, 'utf-8'));"
        "const at = N.index(D.layer);"
        "const items = O.items(D.report);"
        "const blocksOf = (item) => N.blocksOf(at, item);"
        "const blocks = {};"
        "for (const item of items) if (item.kind === 'finding')"
        "  blocks[item.finding] = [...new Set(blocksOf(item))];"
        "const marks = G.openMarks(items, blocksOf);"
        "const counts = {};"
        "for (const [id, list] of marks)"
        "  for (const m of list) if (m.kind === 'finding') counts[id] = m.count;"
        "console.log(JSON.stringify({ blocks, counts }));"
    )
    out = subprocess.run(
        [_node(), "-e", script], capture_output=True, text=True, check=True
    )
    return json.loads(out.stdout)


def test_an_overlay_at_each_kind_and_channel_places_and_one_lost_is_kept(
    kept, tmp_path
) -> None:
    _, _, view = kept
    anchors = _anchors(view)
    items = [
        {
            "anchor": anchor,
            "channel": channel.value,
            "label": f"{kind} on {channel.value}",
            "open": True,
        }
        for kind, anchor in anchors.items()
        for channel in Channel
    ]
    lost = {**anchors["part"], "words": "words no block holds"}
    items.append({"anchor": lost, "channel": "custom", "label": "lost", "open": True})
    path = tmp_path / "overlay.json"
    path.write_text(
        json.dumps({"name": "test layer", "items": items}), encoding="utf-8"
    )
    overlay = read_overlay(path)

    placed = _placed(
        text_layer(view), [i["anchor"] for i in overlay["items"]], tmp_path
    )

    assert len(placed) == len(AnchorKind) * len(Channel) + 1
    assert all(p["placed"] for p in placed[:-1])
    assert placed[-1]["placed"] is False
    theorem = next(b for b in view.blocks if "finite" in view.texts[b])
    assert placed[-1]["block"] == theorem, "kept, unanchored, at its block"


def test_an_overlay_the_viewer_cannot_read_is_refused_naming_the_item(tmp_path) -> None:
    path = tmp_path / "bad.json"
    path.write_text(
        json.dumps(
            {
                "name": "x",
                "items": [
                    {
                        "anchor": {"kind": "part", "node": "n1"},
                        "channel": "key",
                        "label": "l",
                    }
                ],
            }
        )
    )
    with pytest.raises(OverlayError, match="item 0: a part anchor names its words"):
        read_overlay(path)
    path.write_text(
        json.dumps(
            {"name": "x", "items": [{"edge": "e1", "channel": "colour", "label": "l"}]}
        )
    )
    with pytest.raises(OverlayError, match="its channel is one of"):
        read_overlay(path)
