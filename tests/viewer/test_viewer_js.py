"""The viewer's JavaScript: its pure modules under `node --test`
inside `make test` (a missing or old Node
fails the run, naming the remedy, since a skipped suite would hide a
regression); and the card and the open view drawn from the queries' own
JSON, every line they give on the page.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from axiomation import check
from axiomation.queries import open_report, render, show
from axiomation.reader.environments import DISPLAY_ENVS
from test_queries import rich  # noqa: F401 - the fixture

HERE = Path(__file__).resolve().parent
PAGE = HERE.parents[1] / "src" / "axiomation" / "viewer" / "page" / "src"
MINIMUM = 20
REMEDY = (
    "the viewer's JavaScript tests (tests/viewer/*.test.js) need Node "
    f"{MINIMUM} or later on PATH. Install it with nvm (`nvm install 24`) or "
    "from https://nodejs.org, and make sure `node` is on the PATH that "
    "`make test` runs with."
)


def _node() -> str:
    node = shutil.which("node")
    if node is None:
        pytest.fail(f"`node` is not on PATH: {REMEDY}", pytrace=False)
    version = subprocess.run(
        [node, "--version"], capture_output=True, text=True, check=True
    ).stdout.strip()
    found = re.match(r"v(\d+)\.", version)
    if found is None or int(found.group(1)) < MINIMUM:
        pytest.fail(f"`node` is {version} at {node}: {REMEDY}", pytrace=False)
    return node


def test_the_pure_modules_pass_under_node() -> None:
    tests = sorted(str(path) for path in HERE.glob("*.test.js"))
    assert tests, "no JavaScript tests found"
    result = subprocess.run(
        [_node(), "--test", *tests], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_palette_colours_every_group_kind() -> None:
    from axiomation.cutter.model import GroupKind

    palette = json.dumps(str(PAGE / "palette.js"))
    script = f"console.log(JSON.stringify(Object.keys(require({palette}).GROUP)))"
    result = subprocess.run(
        [_node(), "-e", script], capture_output=True, text=True, check=True
    )
    assert sorted(json.loads(result.stdout)) == sorted(k.value for k in GroupKind)


def test_without_node_the_failure_names_the_remedy(monkeypatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: None)
    with pytest.raises(pytest.fail.Exception, match="nvm install 24"):
        _node()


def _drawn(script: str, data: object, tmp_path: Path) -> str:
    """What `script` prints, `DATA` the JSON given, the page's modules
    loaded as `C` (the card), `O` (the open view), `K` (the verdict), `I`
    (the inbox) and `P` (the palette)."""
    given = tmp_path / "data.json"
    given.write_text(json.dumps(data), encoding="utf-8")
    head = (
        f"const C = require({json.dumps(str(PAGE / 'card.js'))});"
        f"const O = require({json.dumps(str(PAGE / 'open.js'))});"
        f"const K = require({json.dumps(str(PAGE / 'check.js'))});"
        f"const I = require({json.dumps(str(PAGE / 'inbox.js'))});"
        f"const P = require({json.dumps(str(PAGE / 'palette.js'))});"
        "const DATA = JSON.parse(require('fs').readFileSync("
        f"{json.dumps(str(given))}, 'utf-8'));"
    )
    return subprocess.run(
        [_node(), "-e", head + script], capture_output=True, text=True, check=True
    ).stdout


#: The lines of a report's filings refiled since, whose refiling it holds:
#: the card draws the filing they became, which names them, never both.
REFILED = (
    "  const fs = r.findings || { items: [], lines: [] };"
    "  const ids = new Set(fs.items.map((f) => f.id));"
    "  const refiled = new Set(fs.lines.filter((_, i) =>"
    "    fs.items[i].refiled_as && ids.has(fs.items[i].refiled_as)));"
)


def test_the_card_draws_every_line_show_gives(rich, tmp_path) -> None:  # noqa: F811
    view, _, _ = rich
    reports = {
        ident: json.loads(render(show(view, ident, whole=True), 0, as_json=True))
        for ident in [*view.blocks, *view.groups]
    }
    handles = sorted(set(view.handles.values()))
    drawn = json.loads(
        _drawn(
            "const out = {}; const h = new Set(DATA.handles);"
            "const attr = (l) => l.replace(/&/g, '&amp;').replace(/</g, '&lt;')"
            "  .replace(/>/g, '&gt;').replace(/\"/g, '&quot;');"
            "for (const [id, r] of Object.entries(DATA.reports)) {"
            "  const html = C.html(r, h, '');"
            + REFILED
            + "  out[id] = Object.entries(r).filter(([, s]) => s && s.lines)"
            "    .flatMap(([, s]) => s.lines)"
            "    .filter((l) => !html.includes('data-line=\"' + attr(l) + '\"'))"
            "    .filter((l) => !refiled.has(l));"
            "} console.log(JSON.stringify(out));",
            {"reports": reports, "handles": handles},
            tmp_path,
        )
    )
    assert drawn == {ident: [] for ident in reports}
    assert any(r["rests_on"]["total"] for r in reports.values())
    assert any(r["rested_on_by"]["total"] for r in reports.values())


def test_the_open_view_lists_every_item_open_gives(rich, tmp_path) -> None:  # noqa: F811
    """Every item `open` gives has its row, naming its block or saying
    its passage, and no field drawn as a bare null or object."""
    view, _, _ = rich
    report = json.loads(render(open_report(view), 0, as_json=True))
    total = sum(report["counts"].values())
    assert total
    out = json.loads(
        _drawn(
            "const all = O.items(DATA);"
            "const groups = O.families(all, 'reading');"
            "const html = O.html(all, groups, P, null, 'reading');"
            "const bare = /undefined|\\[object Object\\]|null/;"
            "console.log(JSON.stringify({n: all.length, missing: all.filter((i, k) =>"
            "  !html.includes('data-item=\"' + k + '\"')"
            "  || !(i.handle || O.passage(i)) || bare.test(O.entry(i, k, P)))"
            "  .map((i) => i.line)}));",
            report,
            tmp_path,
        )
    )
    assert out == {"n": total, "missing": []}


def test_the_check_tab_links_every_gate_and_open_draws_its_items(
    rich,  # noqa: F811
    tmp_path,
) -> None:
    """The page's gates are `check.Gate`, each cause has a tone (an open
    kind's, or its own); every gate with items is a link in the check tab,
    and the open tab's view of that gate draws each of its items, by its
    index in the check's items, its cause never as JSON."""
    view, _, _ = rich
    verdict = check.gates(view, {g: check.Mode.BLOCK for g in check.Gate}).data()
    assert sum(g["count"] for g in verdict["gates"])
    out = json.loads(
        _drawn(
            "const html = K.html(DATA.verdict, O, P, null);"
            "let at = 0; const drawn = []; let json = html.includes('{&quot;');"
            "for (const g of DATA.verdict.gates) {"
            "  if (g.items.length) {"
            "    const h = O.gateHtml(g, at, P, null, {toneOf: (i) => K.tone(i, P)});"
            "    json = json || h.includes('{&quot;');"
            "    g.items.forEach((_, k) => {"
            "      if (h.includes('data-check=\"' + (at + k) + '\"'))"
            "        drawn.push(at + k); });"
            "  }"
            "  at += g.items.length;"
            "}"
            "console.log(JSON.stringify({gates: K.GATES,"
            "  toned: DATA.causes.filter((c) => P.OPEN[c] || P.CAUSE_TONE[c]),"
            "  n: at, drawn: drawn.length, json,"
            "  unlinked: DATA.verdict.gates.filter((g) => g.items.length"
            "    && !html.includes('data-gate-link=\"' + g.gate + '\"'))"
            "    .map((g) => g.gate)}));",
            {"verdict": verdict, "causes": [c.value for c in check.Cause]},
            tmp_path,
        )
    )
    assert out["gates"] == [g.value for g in check.Gate]
    assert out["toned"] == [c.value for c in check.Cause]
    total = sum(g["count"] for g in verdict["gates"])
    assert out == {**out, "n": total, "drawn": total, "json": False, "unlinked": []}


def test_the_inbox_tab_draws_every_item_inbox_gives(rich, tmp_path) -> None:  # noqa: F811
    """The page's sections are `inbox.Section`, in its order, and every
    item the command gives is drawn, a row each."""
    from axiomation.inbox import Section, inbox

    view, _, _ = rich
    report = json.loads(render(inbox(view), 0, as_json=True))
    total = sum(report["counts"].values())
    assert total
    out = json.loads(
        _drawn(
            "const all = I.items(DATA);"
            "const html = I.html(DATA, all, O, P, null);"
            "console.log(JSON.stringify({sections: Object.keys(I.SECTIONS),"
            "  n: all.length, missing: all.filter((i, k) =>"
            "    !html.includes('data-inbox=\"' + k + '\"')).map((i) => i.line)}));",
            report,
            tmp_path,
        )
    )
    assert out == {"sections": [s.value for s in Section], "n": total, "missing": []}


def test_the_card_draws_every_judgment_and_finding_show_gives(tmp_path) -> None:
    """On `test_citations`' paper, a cited judgment read off a
    transient copy, a `true` judgment and a finding on two blocks: the card
    of each block holds every line `show` gives, each reading's evidence and
    each page with its read status."""
    from axiomation.evidence import Origin
    from axiomation.judgments import Author, anchored, find, judge, seen, subject
    from axiomation.queries import View
    from axiomation.records import AnchorKind, Aspect, Depth, Verdict
    from test_citations import CODE, built, cited, shelves

    store, kept = built(tmp_path)
    copy = shelves(origin=Origin.TRANSIENT)
    cited(store, kept, "s", "able1998", [("able1998", 12)], copy)
    at = seen(store, kept)
    judge(
        store,
        kept,
        Aspect.TRUE,
        subject(at, Aspect.TRUE, "u"),
        Verdict.NOT_SHOWN,
        Depth.INFORMAL,
        "nothing bounds f",
        Author("agent", "model-b", "s2"),
    )
    find(
        store,
        kept,
        Aspect.TRUE,
        [anchored(at, AnchorKind.BLOCK, "s"), anchored(at, AnchorKind.BLOCK, "u")],
        3,
        "f is not small",
        "bound f",
        CODE,
    )
    view = View(store, kept)
    reports = {
        ident: json.loads(render(show(view, ident, whole=True), 0, as_json=True))
        for ident in view.blocks
    }
    drawn = json.loads(
        _drawn(
            "const out = {}; const h = new Set(DATA.handles);"
            "const attr = (l) => l.replace(/&/g, '&amp;').replace(/</g, '&lt;')"
            "  .replace(/>/g, '&gt;').replace(/\"/g, '&quot;');"
            "for (const [id, r] of Object.entries(DATA.reports)) {"
            "  const html = C.html(r, h, '');"
            + REFILED
            + "  const missing = Object.entries(r).filter(([, s]) => s && s.lines)"
            "    .flatMap(([, s]) => s.lines)"
            "    .filter((l) => !html.includes('data-line=\"' + attr(l) + '\"'))"
            "    .filter((l) => !refiled.has(l));"
            "  const readings = ((r.judgments || {}).items || [])"
            "    .flatMap((j) => j.judgments);"
            "  for (const g of readings) {"
            "    if (!html.includes('“' + g.evidence + '”')) missing.push(g.evidence);"
            "    for (const p of g.pages) if (!html.includes(p.name + ' <span'"
            "      + ' class=\"rs\">' + p.read_status)) missing.push(p.name);"
            "    if (g.provisional && !html.includes('>provisional<'))"
            "      missing.push('provisional');"
            "  }"
            "  out[id] = missing;"
            "} console.log(JSON.stringify(out));",
            {"reports": reports, "handles": sorted(set(view.handles.values()))},
            tmp_path,
        )
    )
    assert drawn == {ident: [] for ident in reports}
    found = [r["findings"]["total"] for r in reports.values()]
    assert sorted(found) == [0, 0, 1, 1], "the finding on each block it anchors"
    assert any(
        p["read_status"] == "transient"
        for r in reports.values()
        for j in r["judgments"]["items"]
        for g in j["judgments"]
        for p in g["pages"]
    )


def test_the_page_finds_the_reader_s_display_environments() -> None:
    written = (PAGE / "maths.js").read_text("utf-8")
    found = re.search(r"const DISPLAY_ENVIRONMENTS = \[([^\]]*)\]", written)
    assert found is not None
    assert set(re.findall(r'"([a-z]+)"', found.group(1))) == DISPLAY_ENVS


def test_the_card_names_every_aspect_as_a_noun(tmp_path) -> None:
    """Each `records.Aspect` has its noun on the card, and only those."""
    from axiomation.records import Aspect

    out = json.loads(
        _drawn("console.log(JSON.stringify(Object.keys(C.ASPECTS)));", {}, tmp_path)
    )
    assert out == [a.value for a in Aspect]
