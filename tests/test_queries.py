"""The reads over the kept states: a block with its edges,
its reach, blocks by filter and the changes between states or since the
latest, each equal to a direct reading of the kept export and bounded.

A rich graph on the closure tests' synthetic paper, kept as a state; the
synthetic paper built and updated in place by a fake host for the
commands; synthetic states for identity's classes. No model is
called.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import replace
from itertools import takewhile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from axiomation import versions
from axiomation.claim_status import ClaimStatus
from axiomation.cli import BUILD_STAGES, main
from axiomation.closure import EdgeGraph, Link, from_export, from_records, reach
from axiomation.commands import Exit
from axiomation.cutter.model import Role, Segment
from axiomation.driver import ReviewSettings
from axiomation.exchange import cut_payload, dumps, edges_payload
from axiomation.judgments import StatusKind
from axiomation.queries import (
    LIMIT,
    TEXT_CHARACTERS,
    Filter,
    QueryError,
    Section,
    View,
    _step_line,
    blocks_report,
    changes_report,
    clipped,
    evidence_lines,
    matching,
    past_asked,
    reach_report,
    render,
    show,
    text_changes,
)
from axiomation.questions.edges_file import handles
from axiomation.reader import document_hash, read_paper
from axiomation.records import (
    AnswerRecord,
    EdgeRecords,
    Frame,
    RecordStore,
    SelfMark,
    Standing,
    Status,
    TemplateRef,
)
from axiomation.resolver import Resolver
from axiomation.runner import Runner
from axiomation.update import UpdateSettings
from axiomation.update import run as run_update
from axiomation.versions import Command
from questions.review_paper import make_paper
from test_closure import (
    CLOSING,
    DEFINITION,
    GROUP,
    OTHER,
    PROOF,
    RECORDS,
    ROLES,
    SETTING,
    SHARP,
    THEOREM,
    edge,
)
from test_update import EDITED_SETTING, FILES, FLAT, answering, stepping
from test_versions import V1, V2, _grown, built, export, keep, two_versions

# --- a rich graph, kept ---------------------------------------------------------------

EVIDENCE = {
    DEFINITION: "The gain is the ratio",
    THEOREM: "The output is bounded.",
    PROOF: "By Definition",
    GROUP: "Hence the output is bounded.",
    SHARP: "the bound is sharp",
    CLOSING: "Closing words",
    OTHER: "A closing paragraph",
}
RICH = EdgeRecords(
    entries=RECORDS.entries,
    edges=tuple(
        replace(e, evidence=(EVIDENCE[e.source],))
        for e in (*RECORDS.edges, edge(11, CLOSING, "uses", SHARP))
    ),
    selfs=(SelfMark("s1", "a", SHARP, ("the bound is sharp",)),),
    standings=(
        Standing("t1", "a", THEOREM, "The output is bounded.", Status.JUSTIFIED),
        Standing("t2", "a", SHARP, "the bound is sharp", Status.UNSUPPORTED, "none"),
        Standing("t3", "a", CLOSING, "Closing words of the results.", Status.RESTATED),
    ),
    frame=(Frame("f1", "a", SETTING),),
)
ANSWER = AnswerRecord(
    key="k" * 64,
    kind="edges",
    stage="edges",
    template=TemplateRef("edges-file", "t1"),
    attempt=1,
    host="fake",
    model="model",
    time="",
    seconds=0.0,
    cost=0.0,
)


def _roled(found):
    blocks = []
    for block in found.cut.blocks:
        roles, segments = ROLES.get(block.id, ((), ()))
        blocks.append(
            replace(
                block,
                roles=roles,
                segments=segments or ((Segment("", roles[0]),) if roles else ()),
            )
        )
    return replace(found.cut, blocks=tuple(blocks))


@pytest.fixture
def rich(tmp_path: Path):
    """The closure paper with roles and a graph of every edge kind and
    target, kept as one state: the view, the cut and the payload."""
    found = make_paper(tmp_path / "paper")
    graph_cut = _roled(found)
    payload = {
        **cut_payload(found.document, graph_cut),
        **edges_payload(
            found.document, graph_cut, RICH, ANSWER, handles(found.document, graph_cut)
        ),
    }
    store = RecordStore(tmp_path / "store")
    state, _ = versions.record(
        store,
        versions.snapshot(found.root, found.document),
        document_hash(found.document),
        found.document.root,
        dumps(payload).encode(),
        Command.BUILD,
    )
    return View(store, state), graph_cut, payload


def test_a_kept_graph_reads_as_its_records_do(rich) -> None:
    view, graph_cut, payload = rich
    exported, recorded = from_export(payload), from_records(graph_cut, RICH)
    assert exported.blocks == recorded.blocks
    assert exported.groups == recorded.groups
    assert exported.ambient == recorded.ambient == {SETTING}
    assert sorted(exported.links, key=repr) == sorted(recorded.links, key=repr)
    for start in (*recorded.blocks, GROUP):
        assert reach(exported, start) == reach(recorded, start)
        found = reach(recorded, start)
        shown = reach_report(view, start)
        assert [i.data["block"] for i in shown.sections["reached"]] == [
            r.handle for r in found.reached
        ]
        assert [i.data["meaning_moved"] for i in shown.sections["reached"]] == [
            r.changed for r in found.reached
        ]


def test_a_block_s_edges_are_what_it_rests_on_and_what_rests_on_it(rich) -> None:
    """A uses or about edge's source rests on its target, a
    qualifies or justifies edge's target on its source; each side in the
    paper's order of the edge's other end, a self mark at the block's own."""
    view, _, payload = rich
    order = [b["id"] for b in payload["blocks"]]
    groups = {g["id"]: g["blocks"][0] for g in payload["groups"]}

    def at(ident):
        ident = groups.get(ident, ident)
        return order.index(ident) if ident in order else len(order)

    for block in [b["id"] for b in payload["blocks"]] + [GROUP]:
        report = show(view, block)
        up, down = [], []
        for e in payload["edges"]:
            target = e["target"].get("block") or e["target"].get("group")
            reversed_ = e["kind"] in ("qualifies", "justifies")
            if e["source"] == block:
                (down if reversed_ else up).append((at(target), e))
            elif target == block:
                (up if reversed_ else down).append((at(e["source"]), e))
        up += [(at(block), m) for m in payload["self"] if m["block"] == block]
        assert [i.data for i in report.sections["rests_on"]] == [
            e for _, e in sorted(up, key=lambda x: x[0])
        ]
        assert [i.data for i in report.sections["rested_on_by"]] == [
            e for _, e in sorted(down, key=lambda x: x[0])
        ]
        # each standing as the export holds it, with its completeness and
        # its support (no ruling here)
        #: every claim here argued in full or by itself
        shown = [dict(i.data) for i in report.sections["standings"]]
        support = [(d.pop("support") or {}).get("status") for d in shown]
        statuses = [d.pop("claim_status")["status"] for d in shown]
        assert all(s in [c.value for c in ClaimStatus] for s in statuses)
        assert shown == [
            {
                **s,
                "completeness": "given"
                if s["status"] in ("justified", "self")
                else None,
                "unnamed": False,
            }
            for s in payload["standings"]
            if s["block"] == block
        ]
        # the closing words stand justified, yet no justifies edge reaches
        # them: a gap of their own
        gap = {"Closing words of the results."}
        assert support == [
            "unsupported"
            if d["claim"]["text"] in gap
            else {"unsupported": "unsupported", "conjectured": None}.get(
                d["status"], "supported"
            )
            for d in shown
        ]
    # a theorem rests on the proof that justifies it, the proof on nothing
    # by that edge
    justified = show(view, THEOREM).sections["rests_on"]
    assert any(i.data["kind"] == "justifies" for i in justified)
    assert not any(
        i.data["kind"] == "justifies" for i in show(view, GROUP).sections["rests_on"]
    )
    lines = render(show(view, DEFINITION)).splitlines()
    setting = view.handle(SETTING)
    assert f'  → uses {setting} [x] · "The gain is the ratio"' in lines
    sharp = render(show(view, SHARP))
    assert (
        f'{view.handle(SHARP)} → about {view.handle(DEFINITION)} where-used · "the'
        in (render(show(view, DEFINITION)))
    )
    assert '→ self · "the bound is sharp"' in sharp
    assert '⊢ unsupported · "the bound is sharp" — none' in sharp
    assert render(show(view, DEFINITION, kinds=["about"])).count("→ uses") == 0


def test_an_edge_line_prints_its_generalizes_flag(rich) -> None:
    view, _, payload = rich
    about = next(
        e for e in payload["edges"] if e["kind"] == "about" and "block" in e["target"]
    )
    line = view.edge_line({**about, "where_used": False, "generalizes": True})
    assert line.split(" · ")[0].endswith(" generalizes")
    assert "generalizes" not in view.edge_line(about)


def test_each_filter_alone_and_combined_is_a_direct_scan(rich) -> None:
    view, _, payload = rich
    blocks = [b["id"] for b in payload["blocks"]]

    def found(**given: Any) -> set[str]:
        return set(matching(view, Filter(**given)))

    def roles(b: str) -> set[str]:
        block = next(x for x in payload["blocks"] if x["id"] == b)
        return {s["role"] for s in block["segments"]} | set(block["roles"])

    assert found() == set(blocks)
    assert found(standings=("unsupported",)) == {SHARP}
    assert found(standings=("unsupported", "restated")) == {SHARP, CLOSING}
    assert found(roles=("claim",)) == {b for b in blocks if "claim" in roles(b)}
    assert found(roles=("none",)) == {b for b in blocks if not roles(b) - {"none"}}
    assert found(forms=("theorem",)) == {THEOREM}
    results = {
        b for b in blocks if b.startswith("main.tex:") and int(b.split(":")[1]) >= 11
    }
    assert found(sections=("Results",)) == results
    assert found(sections=("2",)) == results
    assert found(sections=("2", "3")) == found(sections=("results",)) | {
        b for b in blocks if b.startswith("extra.tex")
    }
    assert found(groups=(GROUP,)) == {PROOF, "main.tex:16:48", SHARP}
    assert found(onto=(DEFINITION,)) == {THEOREM, SHARP}
    assert found(onto=(DEFINITION,), kinds=("uses",)) == {THEOREM}
    assert found(onto=(GROUP,)) == {CLOSING}
    assert found(reached_from=(CLOSING,)) == {PROOF, SHARP, "main.tex:16:48"}
    assert found(reached_from=(CLOSING,), kinds=("uses",)) == {PROOF, SHARP}
    assert found(text="ratio of OUTPUT") == {DEFINITION}
    assert found(uses_unsupported=True) == {CLOSING}
    assert found(roles=("claim",), sections=("2",), standings=("restated",)) == {
        CLOSING
    }
    # what the edges say, against a direct count of the export
    assert found(standings=("unsupported",)) == {
        s["block"] for s in payload["standings"] if s["status"] == "unsupported"
    }
    with pytest.raises(QueryError, match="is a block, not a group"):
        found(groups=(THEOREM,))


def test_a_block_is_named_by_node_handle_id_or_a_unique_prefix(rich) -> None:
    view, _, _ = rich
    handle = view.handle(THEOREM)
    assert view.resolve(view.node(THEOREM)) == THEOREM
    assert view.resolve(handle) == THEOREM
    assert view.resolve(THEOREM) == THEOREM
    assert view.resolve(view.node(GROUP)) == GROUP
    assert view.resolve(handle[:-1]) == THEOREM
    # a node id by the prefix a line shows it by
    assert view.shown(THEOREM) != view.node(THEOREM)
    assert view.node(THEOREM).startswith(view.shown(THEOREM))
    assert view.resolve(view.shown(THEOREM)) == THEOREM
    with pytest.raises(QueryError, match="'n' names .* blocks"):
        view.resolve("n")
    with pytest.raises(QueryError) as refused:
        view.resolve("s")
    assert str(refused.value) == "'s' names 2 blocks: setting, small-means-below-one"
    with pytest.raises(QueryError, match="names no block"):
        view.resolve("nothing-like-it")


def test_a_header_line_names_node_handle_form_roles_section_group_standing(
    rich,
) -> None:
    view, _, _ = rich
    line = view.header(SHARP)
    assert line.split("  ")[:4] == [
        view.shown(SHARP),
        view.handle(SHARP),
        SHARP,
        view.form(SHARP),
    ]
    assert "§2" in line and f"in {view.handle(GROUP)}" in line
    assert line.endswith("⊢ unsupported")
    assert view.header(SETTING).split("  ")[5] == "§1"


# --- bounds ------------------------------------------------------------------------


def test_every_section_stays_within_its_bound_on_a_long_paper(tmp_path) -> None:
    """230 definitions, each using the ten before it: a dense
    stand-in for a long paper; then a state of the same version
    with no edge."""
    long = "word " * 120
    main_text = (
        "\\documentclass{article}\n\\begin{document}\n\\section{All}\n"
        + "\n\n".join(f"Paragraph {i} defines t{i}. {long}" for i in range(230))
        + "\n\\end{document}\n"
    )
    found = make_paper(tmp_path / "paper", main=main_text, extra="Nothing.\n")
    blocks = []
    for block in found.cut.blocks:
        blocks.append(
            replace(
                block,
                roles=(Role.DEFINITION,),
                segments=(Segment("", Role.DEFINITION),),
            )
        )
    dense_cut = replace(found.cut, blocks=tuple(blocks))
    ids = [
        b.id for b in dense_cut.blocks if "Paragraph" in found.document.text(b.owned[0])
    ]
    assert len(ids) == 230
    edges = []
    for i, source in enumerate(ids):
        for target in ids[max(0, i - 10) : i]:
            n = len(edges) + 1
            edges.append(
                replace(
                    edge(n, source, "uses", target),
                    evidence=(f"Paragraph {i} defines",),
                )
            )
    records = EdgeRecords(edges=tuple(edges))
    store = RecordStore(tmp_path / "store")
    named = handles(found.document, dense_cut)
    payload = {
        **cut_payload(found.document, dense_cut),
        **edges_payload(found.document, dense_cut, records, ANSWER, named),
    }
    files = versions.snapshot(found.root, found.document)
    version = document_hash(found.document)
    first, _ = versions.record(
        store,
        files,
        version,
        found.document.root,
        dumps(payload).encode(),
        Command.BUILD,
    )
    bare = {**payload, "edges": [], "entries": []}
    second, _ = versions.record(
        store,
        files,
        version,
        found.document.root,
        dumps(bare).encode(),
        Command.EDIT,
        started_from=first,
    )
    view = View(store, first)
    reports = [
        blocks_report(view, Filter()),
        reach_report(view, ids[0]),
        show(view, ids[100]),
        changes_report(store, view, View(store, second)),
    ]
    assert len(reports[1].sections["reached"]) == 229
    assert len(reports[3].sections["edges"]) == len(edges)
    for report in reports:
        text = render(report)
        for name, items in report.sections.items():
            heading = f"{name.replace('_', ' ')} ({len(items)}):"
            after = text.split(heading, 1)[1].split("\n")[1:]
            lines = takewhile(lambda line: line.startswith("  "), after)
            items_shown = [
                line for line in lines if not line.startswith(("    ", "  …"))
            ]
            assert len(items_shown) == min(len(items), LIMIT), name
            if len(items) > LIMIT:
                assert f"  … {len(items) - LIMIT} more" in text
        as_json = json.loads(render(report, as_json=True))
        for name, items in report.sections.items():
            assert len(as_json[name]["items"]) == min(len(items), LIMIT)
            assert as_json[name]["more"] == max(0, len(items) - LIMIT)
        unbounded = json.loads(render(report, limit=0, as_json=True))
        for name, items in report.sections.items():
            assert len(unbounded[name]["items"]) == len(items)
            assert unbounded[name]["more"] == 0
    text = show(view, ids[0]).data["text"]
    assert len(text) <= TEXT_CHARACTERS + len(" … (999 characters)")
    assert len(show(view, ids[0], whole=True).data["text"]) > TEXT_CHARACTERS


# --- changes between kept states ------------------------------------------------------


def test_the_text_s_changes_are_identity_s_classes_old_beside_new(tmp_path) -> None:
    """The history case: a changed, a moved, a split, an added and a
    deleted block between a build and its update."""
    old_text = "Aa. Bb. Cc. Dd. Ee ff."
    new_text = "Aa. Cc. Bb narrowed. Ee. Ff. New."
    store, first, second = two_versions(
        tmp_path,
        old_text,
        export(
            old_text,
            [("a", "Aa."), ("b", "Bb."), ("c", "Cc."), ("d", "Dd."), ("e", "Ee ff.")],
        ),
        new_text,
        export(
            new_text,
            [
                ("a", "Aa."),
                ("c", "Cc."),
                ("b", "Bb narrowed."),
                ("e1", "Ee."),
                ("e2", "Ff."),
                ("n", "New."),
            ],
        ),
        [
            (("a",), ("a",), "same", None),
            (("b",), ("b",), "changed", None),
            (("c",), ("c",), "moved", None),
            (("d",), (), "deleted", None),
            (("e",), ("e1", "e2"), "split", "e1"),
            ((), ("n",), "added", None),
        ],
    )
    found = text_changes(store, View(store, first), View(store, second))
    by_class = {
        (i.data["old"] or {}).get("block", "-")
        + ">"
        + (i.data["new"] or {}).get("block", "-"): i.data["classes"]
        for i in found
    }
    assert by_class == {
        "c>c": ["moved"],
        "b>b": ["changed"],
        "e>e1": ["split"],
        "->e2": ["split"],
        "->n": ["added"],
        "d>-": ["deleted"],
    }
    changed = next(i for i in found if i.data["classes"] == ["changed"])
    assert "was: Bb." in changed.line and "now: Bb narrowed." in changed.line
    moved = next(i for i in found if i.data["classes"] == ["moved"])
    assert "was:" not in moved.line
    with pytest.raises(QueryError, match="is not before"):
        text_changes(store, View(store, second), View(store, first))
    third = keep(
        store,
        V2,
        new_text,
        json.loads(versions.kept_export(store, second)),
        Command.EDIT,
        started_from=second,
        answers=["k1"],
    )
    assert text_changes(store, View(store, second), View(store, third)) == []


def _updated(tmp_path: Path, capsys):
    """The synthetic paper built into its own store, edited in place: the
    paper, its store and the files seen."""
    root, directory, seen = built(tmp_path, capsys)
    (root / "main.tex").write_text(EDITED_SETTING, encoding="utf-8")
    return root, directory, seen


def _run(args: list[str], capsys) -> tuple[int, str, str]:
    capsys.readouterr()
    code = main(args)
    out = capsys.readouterr()
    return code, out.out, out.err


def test_an_edit_not_yet_updated_reopens_what_the_dry_run_counts(
    tmp_path, capsys
) -> None:
    root, directory, seen = _updated(tmp_path, capsys)
    code, out, err = _run(["changes", str(root), "--json"], capsys)
    assert code == 0, err
    found = json.loads(out)
    assert found["pending"] is True
    code, dry, err = _run(
        ["update", str(root), "--host", "opencode", "--dry-run", "--json"], capsys
    )
    assert code == 0, err
    planned = json.loads(dry)
    assert found["reopened"]["total"] == planned["reopened"]
    assert found["on_condition"]["total"] == planned["conditional"]
    (changed,) = found["text"]["items"]
    assert changed["class"] == "changed"
    # a read writes nothing
    assert _grown(directory, seen) == seen

    code, out, _ = _run(["show", str(root), changed["old"][0]["node"]], capsys)
    assert code == 0
    assert "note: the paper's files are not this state's version" in out
    code, out, _ = _run(
        ["show", str(root), changed["old"][0]["node"], "--state", "0"], capsys
    )
    assert "note:" not in out


@pytest.mark.usefixtures("develop_mode")
def test_changes_between_a_build_and_its_update(tmp_path, capsys) -> None:
    root, directory, _ = _updated(tmp_path, capsys)
    store = RecordStore(directory)
    assert main(["update", str(root), "--host", "none"]) == 5
    (key,) = [a.key for a in store.authoritative().values() if a.kind == "identity"]
    answered = run_update(
        read_paper(root),
        store,
        UpdateSettings(
            files=FILES,
            review=ReviewSettings(stages=BUILD_STAGES, files=FILES),
            paper=root.resolve(),
        ),
        Runner(
            stepping(answering({"we-fix-a-field": ["Δ reworded — a clause added"]})),
            Resolver(store),
            10.0,
            FLAT,
        ),
        identity=key,
    )
    assert answered.complete, answered.unanswered
    assert main(["update", str(root), "--host", "none"]) == 0
    first, second = versions.states(store)

    code, out, err = _run(["changes", str(root), "--json"], capsys)
    assert code == 0, err
    found = json.loads(out)
    assert (found["from"], found["to"]) == (first.id, second.id)
    (edited,) = found["text"]["items"]
    assert edited["classes"] == ["changed"]
    assert edited["node"] == first.node_of(edited["old"]["block"])
    assert "edited" in edited["new"]["text"]
    old_export = json.loads(versions.kept_export(store, first))
    new_export = json.loads(versions.kept_export(store, second))
    for item in found["edges"]["items"]:
        kept = old_export if item["change"] == "ended" else new_export
        if "edge" in item:
            assert item["edge"] in kept["edges"]
    code, text, _ = _run(["changes", str(root)], capsys)
    assert re.search(
        rf"{edited['node'][:5]}\w*  \S+  changed\n    was: .*\n    now: .*edited", text
    )

    # a node id names the same block at both states
    node = edited["node"]
    for state in (first, second):
        code, out, _ = _run(["show", str(root), node, "--state", state.id], capsys)
        assert (
            node.startswith(out.split("  ")[0])
            and state.block_of(node) in out.split("\n")[0]
        )
    code, out, _ = _run(["show", str(root), node, "--history"], capsys)
    assert "history (2):" in out and " changed " in out
    code, out, _ = _run(
        ["blocks", str(root), "--changed-since", first.id, "--json"], capsys
    )
    assert [b["node"] for b in json.loads(out)["blocks"]["items"]] == [node]


def test_a_person_s_edit_no_state_applied_yet_is_noted(tmp_path, capsys) -> None:
    """The state shown does not hold a roles edit
    whose re-ask is not answered yet."""
    root, _, _ = built(tmp_path, capsys)
    sheet = tmp_path / "roles.md"
    assert main(["sheet", str(root), "--stage", "roles", "-o", str(sheet)]) == 0
    text = sheet.read_text(encoding="utf-8")
    sheet.write_text(text.replace("⟦b5 definition⟧", "⟦b5 claim⟧"), encoding="utf-8")
    code, _, err = _run(
        ["edit", str(root), "--stage", "roles", "--file", str(sheet)], capsys
    )
    assert code == Exit.UNANSWERED, err
    code, out, _ = _run(["blocks", str(root), "--role", "claim"], capsys)
    assert out.startswith("0 blocks")
    assert re.search(
        r"note: 1 edits on this version no state applied yet \(roles ", out
    )


def test_a_declared_environment_is_found_by_what_it_stands_for(tmp_path) -> None:
    """A `\\newtheorem{lem}{Lemma}`: `--form lemma` finds `lem`."""
    main_text = (
        "\\documentclass{article}\n\\newtheorem{lem}{Lemma}\n\\begin{document}\n"
        "\\section{Main Results}\n\\begin{lem}\nEvery x is y.\n\\end{lem}\n"
        "Some prose.\n\\end{document}\n"
    )
    found = make_paper(tmp_path / "paper", main=main_text, extra="Nothing.\n")
    payload = cut_payload(found.document, found.cut)
    store = RecordStore(tmp_path / "store")
    state, _ = versions.record(
        store,
        versions.snapshot(found.root, found.document),
        document_hash(found.document),
        found.document.root,
        dumps(payload).encode(),
        Command.BUILD,
    )
    view = View(store, state)
    (lemma,) = matching(view, Filter(forms=("lemma",)))
    assert matching(view, Filter(forms=("LEM",))) == [lemma]
    assert view.form(lemma) == "statement lem (lemma)"
    assert "§1" in view.header(lemma)
    # a section by its title's words, never by part of a word
    assert lemma in matching(view, Filter(sections=("main results",)))
    assert matching(view, Filter(sections=("ain",))) == []
    with pytest.raises(QueryError, match="--text"):
        matching(view, Filter(text="(unclosed"))


APPENDIX_MAIN = (
    "\\documentclass{article}\n\\begin{document}\n"
    "\\section{Main Results}\nThe main text.\n"
    "\\section{Bounds in L2}\nThe square bound.\n"
    "\\section*{Appendix A1 · Drift and Rigidity}\nThe appendix opens.\n"
    "\\subsection*{A1.1 Setup}\nThe setup.\n"
    "\\subsection*{A1.2 Rigidity of the bound}\nThe rigidity.\n"
    "\\subsection*{A1.3–A1.4 Two steps}\nThe steps.\n"
    "\\section*{Appendix A2 · Horizons}\nThe second appendix.\n"
    "\\end{document}\n"
)


def appendix_view(tmp_path: Path) -> View:
    """A paper whose appendices are unnumbered sections their titles
    number, one holding two subsections."""
    found = make_paper(tmp_path / "paper", main=APPENDIX_MAIN, extra="Nothing.\n")
    store = RecordStore(tmp_path / "store")
    state, _ = versions.record(
        store,
        versions.snapshot(found.root, found.document),
        document_hash(found.document),
        found.document.root,
        dumps(cut_payload(found.document, found.cut)).encode(),
        Command.BUILD,
    )
    return View(store, state)


def test_an_unnumbered_section_is_named_by_the_number_its_title_gives(
    tmp_path,
) -> None:
    """`--section A1` takes the appendix its title numbers and its
    subsections, as `--section 2` takes 2.1; `A1.1` that subsection
    alone; title words still name a section alone."""
    assert [
        s.given_number
        for s in (
            Section("3.2", "Bounds", None),
            Section("", "Appendix A1 · Drift and Rigidity", None),
            Section("", "A1.2 Object stationarity", None),
            Section("", "2.0 Standing Assumptions", None),
            Section("", "A1.1–A1.7 Drift inequality", None),
            Section("", "Introduction", None),
            Section("", "Appendix B: Proofs", None),
            Section("", "A1. Setup", None),
            Section("", "A.1 Setup", None),
            Section("", "A Note on Bounds", None),
        )
    ] == ["3.2", "A1", "A1.2", "2.0", "", "", "B", "A1", "A.1", ""]
    view = appendix_view(tmp_path)

    def texts(*sections: str) -> list[str]:
        """The prose of the blocks these sections take, headings left out."""
        found = matching(view, Filter(sections=sections))
        return [view.texts[b] for b in found if view.blocks[b]["form"] != "heading"]

    # with what lies within, a subsection whose title gives no number too
    assert texts("A1") == [
        "The appendix opens.",
        "The setup.",
        "The rigidity.",
        "The steps.",
    ]
    assert texts("a1") == texts("A1")
    assert texts("A1.1") == ["The setup."]
    assert texts("Horizons") == ["The second appendix."]
    # a name that numbers no section is read as title words
    assert texts("L2") == ["The square bound."]


def test_an_edge_onto_a_definition_beside_an_unsupported_claim_uses_no_claim(
    rich,
) -> None:
    view, _, payload = rich
    sharp = view.handle(SHARP)
    part = {
        "id": f"{sharp} [d]",
        "block": SHARP,
        "anchor": "the bound",
        "marker": False,
        "role": "definition",
    }
    view.export["entries"].append(part)
    for e in view.edges:
        if e["source"] == CLOSING and e["target"].get("block") == SHARP:
            e["target"]["entries"] = [part["id"]]
    assert matching(view, Filter(uses_unsupported=True)) == []
    part["role"] = "claim"
    assert matching(view, Filter(uses_unsupported=True)) == [CLOSING]


# --- lines across a split or a merge ----------------------------------------


def _viewable(payload: dict[str, Any]) -> dict[str, Any]:
    """A synthetic export as `View` reads it: each block's roles."""
    for b in payload["blocks"]:
        b.setdefault("segments", [])
        b.setdefault("roles", [])
    return payload


def _uses(source: str, target: str, quote: str, part: str | None = None) -> dict:
    target_data: dict[str, Any] = {"block": target}
    if part is not None:
        target_data["entries"] = [part]
    return {
        "id": f"{source}>{target}>{part}",
        "source": source,
        "kind": "uses",
        "target": target_data,
        "evidence": [{"text": quote}],
    }


def _edited(tmp_path: Path, text: str, before: dict, after: dict):
    """A build of `before` and an edit of the same version to `after`."""
    store = RecordStore(tmp_path / "store")
    first = keep(store, V1, text, _viewable(before))
    second = keep(store, V1, text, _viewable(after), Command.EDIT, started_from=first)
    return store, changes_report(store, View(store, first), View(store, second))


def test_a_merge_folds_two_whole_lines_into_one(tmp_path) -> None:
    text = "See both. One. Two."
    blocks = [("x", "See both."), ("a", "One."), ("b", "Two.")]
    _, report = _edited(
        tmp_path,
        text,
        export(
            text,
            blocks,
            edges=[_uses("x", "a", "See both."), _uses("x", "b", "See both.")],
        ),
        export(
            text,
            [("x", "See both."), ("a", "One. Two.")],
            edges=[_uses("x", "a", "See both.")],
        ),
    )

    assert report.sections["edges"] == [] and report.sections["to_look_at"] == []
    (folded,) = report.sections["carried"]
    assert folded.data["change"] == "folded"
    assert folded.data["old"]["edge"]["target"] == {"block": "b"}
    assert "folded in: x → uses b" in folded.line


def test_lines_onto_parts_of_merged_blocks_stay_two_lines_both_carried(
    tmp_path,
) -> None:
    text = "See both. One. Two."
    parts = [{"id": "a [one]", "anchor": "One"}, {"id": "b [two]", "anchor": "Two"}]
    _, report = _edited(
        tmp_path,
        text,
        export(
            text,
            [("x", "See both."), ("a", "One."), ("b", "Two.")],
            edges=[
                _uses("x", "a", "See both.", "a [one]"),
                _uses("x", "b", "See both.", "b [two]"),
            ],
            entries=parts,
        ),
        export(
            text,
            [("x", "See both."), ("a", "One. Two.")],
            edges=[
                _uses("x", "a", "See both.", "a [one]"),
                _uses("x", "a", "See both.", "a [two]"),
            ],
            entries=[parts[0], {"id": "a [two]", "anchor": "Two"}],
        ),
    )

    assert report.sections["edges"] == []
    (carried,) = report.sections["carried"]
    assert carried.data["change"] == "carried"
    assert carried.data["new"]["edge"]["target"] == {
        "block": "a",
        "entries": ["a [two]"],
    }


def test_a_part_whose_label_a_split_moved_is_carried_across_versions(
    tmp_path,
) -> None:
    """The split's first piece keeps the node; the line onto the
    labelled part, now in the second piece, is carried, not ended and
    begun."""
    old_text = "See it. First half. Second half labelled."
    new_text = "See it. First half. Second half labelled!"
    store, first, second = two_versions(
        tmp_path,
        old_text,
        _viewable(
            export(
                old_text,
                [("x", "See it."), ("a", "First half. Second half labelled.")],
                edges=[_uses("x", "a", "See it.", "a [l]")],
                entries=[{"id": "a [l]", "anchor": "Second half labelled"}],
            )
        ),
        new_text,
        _viewable(
            export(
                new_text,
                [
                    ("x", "See it."),
                    ("a", "First half."),
                    ("a2", "Second half labelled!"),
                ],
                edges=[_uses("x", "a2", "See it.", "a2 [l]")],
                entries=[{"id": "a2 [l]", "anchor": "Second half labelled"}],
            )
        ),
        [(("x",), ("x",), "same", None), (("a",), ("a", "a2"), "split", "a2")],
    )
    report = changes_report(store, View(store, first), View(store, second))

    assert report.sections["edges"] == [] and report.sections["to_look_at"] == []
    (carried,) = report.sections["carried"]
    assert carried.data["new"]["edge"]["target"]["block"] == "a2"


def test_a_line_onto_a_block_identity_reads_as_added_is_not_carried(
    tmp_path,
) -> None:
    """A split whose words changed: identity pairs the first piece by word
    ratio and reads the second as added. Nothing came from the old block
    into it, so a line onto it begins, and the one it replaced ends."""
    old_text = "See it. One sentence. Second words here."
    new_text = "See it. One sentence. Brand new words."
    store, first, second = two_versions(
        tmp_path,
        old_text,
        _viewable(
            export(
                old_text,
                [("x", "See it."), ("a", "One sentence. Second words here.")],
                edges=[_uses("x", "a", "See it.", "a [w]")],
                entries=[{"id": "a [w]", "anchor": "Second words"}],
            )
        ),
        new_text,
        _viewable(
            export(
                new_text,
                [("x", "See it."), ("a", "One sentence."), ("n", "Brand new words.")],
                edges=[_uses("x", "n", "See it.", "n [w]")],
                entries=[{"id": "n [w]", "anchor": "Brand new"}],
            )
        ),
        [
            (("x",), ("x",), "same", None),
            (("a",), ("a",), "changed", None),
            ((), ("n",), "added", None),
        ],
    )
    report = changes_report(store, View(store, first), View(store, second))

    assert [i.line[0] for i in report.sections["edges"]] == ["-", "+"]
    assert report.sections["carried"] == [] and report.sections["to_look_at"] == []


def test_a_line_from_a_whole_block_that_split_is_to_look_at(tmp_path) -> None:
    """A line with no quote of its source relied on the whole source; once
    the source split, it is listed with what stands in its place."""
    text = "Claims one. Claims two. Target."
    blocks = [("s", "Claims one. Claims two."), ("t", "Target.")]
    unquoted = {**_uses("s", "t", ""), "evidence": []}
    _, report = _edited(
        tmp_path,
        text,
        export(text, blocks, edges=[unquoted]),
        export(
            text,
            [("s", "Claims one."), ("s2", "Claims two."), ("t", "Target.")],
            edges=[unquoted, {**_uses("s2", "t", ""), "evidence": []}],
        ),
    )

    (look,) = report.sections["to_look_at"]
    assert look.data["why"] == "its source split"
    assert [e["edge"]["source"] for e in look.data["now"]] == ["s", "s2"]
    assert report.sections["edges"] == [] and report.sections["carried"] == []


def test_a_reworded_claim_of_two_is_the_same_standing_and_its_split_piece_carried(
    tmp_path,
) -> None:
    """A part continues by shared characters within its node, then into a
    piece of its split: a claim reworded in a two-claim block
    is no change of the graph; split into its own block, it is carried; a
    status changed on it is ended and begun."""
    text = "One holds. Two holds well. Two holds."
    two = [("b", "One holds. Two holds well.")]
    before = export(
        text, two, standings=[("b", "One holds."), ("b", "Two holds well.")]
    )
    reworded = export(text, two, standings=[("b", "One holds."), ("b", "Two holds.")])
    _, report = _edited(tmp_path / "a", text, before, reworded)
    assert report.sections["standings"] == [] and report.sections["carried"] == []

    split = export(
        text,
        [("b", "One holds."), ("c", "Two holds well.")],
        standings=[("b", "One holds."), ("c", "Two holds well.")],
    )
    _, report = _edited(tmp_path / "b", text, before, split)
    assert report.sections["standings"] == []
    (carried,) = report.sections["carried"]
    assert carried.data["new"]["standing"]["block"] == "c"

    unsupported = export(
        text, two, standings=[("b", "One holds."), ("b", "Two holds.")]
    )
    unsupported["standings"][1]["status"] = "unsupported"
    _, report = _edited(tmp_path / "c", text, before, unsupported)
    assert [i.data["change"] for i in report.sections["standings"]] == [
        "ended",
        "began",
    ]


# --- what the kept graph got wrong -----------------------------------------


def soundness_view(tmp_path: Path) -> View:
    """`test_soundness`'s paper kept as a state its document reads back
    from, one condition unused."""
    from test_judgments import edge
    from test_soundness import GAIN, LEMMA, POSITIVE, cut_paper

    paper = tmp_path / "paper"
    paper.mkdir()
    document, payload, used, _ = cut_paper(
        paper,
        [
            edge(LEMMA, "uses", GAIN),
            edge(POSITIVE, "qualifies", GAIN),
        ],
    )
    payload["edges"].append(edge(LEMMA, "uses", used))
    store = RecordStore(tmp_path / "store")
    state, _ = versions.record(
        store,
        versions.snapshot(paper, document),
        document_hash(document),
        document.root,
        dumps(payload).encode(),
        Command.BUILD,
    )
    return View(store, state)


def test_open_lists_what_the_kept_graph_got_wrong(tmp_path) -> None:
    from axiomation.queries import OpenKind, open_report
    from test_soundness import LEMMA, PROSE, SEE

    view = soundness_view(tmp_path)
    report = open_report(view)
    assert not [n for n in report.notes if n.startswith("the labels")]
    assert report.data["counts"] == {
        **{k.value: 0 for k in OpenKind},
        "dangling-reference": 2,
        "duplicate-label": 1,
        "unused-premise": 2,
    }
    dangling = [i.data for i in report.sections["dangling_reference"]]
    assert [(d["block"], d["anchor"]["kind"], d["status"]) for d in dangling] == [
        (LEMMA, "span", "defined nowhere"),
        (SEE, "span", "defined nowhere"),
    ]
    assert dangling[0]["anchor"]["words"] == "\\ref{lem:missing}"
    (duplicate,) = report.sections["duplicate_label"]
    assert len(duplicate.data["anchors"]) == 2
    unused = report.sections["unused_premise"]
    assert [i.data["block"] for i in unused][1] == PROSE
    assert unused[0].data["clears"].startswith("a uses or qualifies edge")

    only = open_report(view, kinds=["duplicate-label"])
    assert list(only.sections) == ["duplicate_label"]
    assert only.sections["duplicate_label"][0].line == duplicate.line


def test_open_says_where_it_could_not_read_the_labels(tmp_path) -> None:
    from axiomation.queries import open_report

    store = RecordStore(tmp_path / "store")
    named = export("Then f is bounded.", [])
    # a file the export names that today's reader does not read
    named["document"]["files"].append(
        {"path": "q.md", "sha256": hashlib.sha256(b"Q.").hexdigest()}
    )
    kept, _ = versions.record(
        store,
        {"p.md": b"Then f is bounded.", "q.md": b"Q."},
        V1,
        "p.md",
        json.dumps(named).encode(),
        Command.BUILD,
    )
    report = open_report(View(store, kept))
    (note,) = [n for n in report.notes if n.startswith("the labels")]
    assert note.startswith(f"the labels and environments of state {kept.id} were not")
    assert report.data["counts"]["dangling-reference"] == 0


def test_reach_says_asked_stops_the_walk_and_walks_past_it_when_asked(rich) -> None:
    """Without the flag the walk stops at an asked block and the head says
    so; `--through-claims` walks on past each asked block as though its
    meaning moved: the first walk's lines unchanged, every block beyond
    naming an asked block it came past, at a depth beyond it."""
    view, _, payload = rich
    graph = from_export(payload)
    grew = 0
    for start in [b["id"] for b in payload["blocks"]] + [GROUP]:
        plain = reach_report(view, start)
        assert "the walk stops there (--through-claims walks on" in plain.head[1]
        through = reach_report(view, start, through_claims=True)
        lines = [i.data for i in through.sections["reached"]]
        first = [i.data for i in plain.sections["reached"]]
        assert lines[: len(first)] == first
        depth = {d["block"]: d["depth"] for d in lines}
        handle_of = {d["handle"]: d["block"] for d in lines}
        for d in lines[len(first) :]:
            past = handle_of[d["past"]]
            assert not next(x for x in lines if x["block"] == past)["meaning_moved"]
            assert depth[past] < d["depth"]
            assert d["block"] in {r.handle for r in reach(graph, past).reached}
        grew += len(lines) > len(first)
    assert grew


def test_a_judgment_s_evidence_is_cut_as_a_block_s_text_unless_whole() -> None:
    """`show` prints the evidence of the readings that speak: the held one,
    each live one of a dispute, a lapsed subject's latest."""
    long = "the gain bounds it " * 30
    first = SimpleNamespace(judgment=SimpleNamespace(id="j1", evidence=long), live=True)
    second = SimpleNamespace(
        judgment=SimpleNamespace(id="j2", evidence="no"), live=False
    )
    held = SimpleNamespace(status=StatusKind.HELD, held=first, reads=[first, second])
    cut = evidence_lines(held)
    assert cut == f"\n  evidence j1: {clipped(long)}" and cut.endswith(
        f"({len(long)} characters)"
    )
    assert evidence_lines(held, whole=True) == f"\n  evidence j1: {long}"
    lapsed = SimpleNamespace(status=StatusKind.LAPSED, held=None, reads=[first, second])
    assert evidence_lines(lapsed) == "\n  evidence j2: no"
    disputed = SimpleNamespace(
        status=StatusKind.DISPUTED, held=None, reads=[first, second]
    )
    assert evidence_lines(disputed).startswith("\n  evidence j1: ")
    unjudged = SimpleNamespace(status=StatusKind.UNJUDGED, held=None, reads=[])
    assert evidence_lines(unjudged) == ""


def test_blocks_names_its_filters_by_the_edge_s_direction(
    rich, tmp_path, capsys
) -> None:
    """`--onto X` is the blocks with an edge onto X, `--from X` the blocks
    X's edges reach; the old names `--uses` and `--used-by` are gone."""
    view, _, _ = rich
    paper = tmp_path / "paper"
    where = [str(paper), "--store", str(view.store.directory), "--json", "--limit", "0"]
    main(["blocks", *where, "--onto", GROUP])
    assert [
        i["block"] for i in json.loads(capsys.readouterr().out)["blocks"]["items"]
    ] == [CLOSING]
    capsys.readouterr()
    for old in ("--uses", "--used-by"):
        with pytest.raises(SystemExit):
            main(["blocks", *where, old, GROUP])
        assert f"unrecognized arguments: {old} " in capsys.readouterr().err
    # nor is a prefix of a flag that stays read as it
    with pytest.raises(SystemExit):
        main(["blocks", *where, "--uses"])


def test_the_walk_past_a_claim_moves_its_meaning_never_edits_its_text() -> None:
    """Past an asked block the walk goes on as though its meaning moved:
    not as though its text was edited, so no `where-used` edge of a block
    it uses is asked and no ambient frame moves the blocks after it; each
    block beyond at its shortest depth, past the asked block it came by."""
    graph = EdgeGraph(
        blocks=("s", "a", "c", "b", "p1", "p2", "x", "lemma", "w", "y"),
        groups={},
        links=(
            Link("a", "uses", "s", "claim"),
            Link("c", "uses", "s", "claim"),
            Link("b", "uses", "c", "claim"),
            Link("p1", "uses", "a", "claim"),
            Link("p2", "uses", "p1", "claim"),
            Link("x", "uses", "p2", "claim"),
            Link("x", "uses", "b", "claim"),
            Link("a", "uses", "lemma", "definition"),
            Link("w", "uses", "lemma", "definition", where_used=True),
        ),
        ambient=frozenset({"a"}),
    )
    found = reach(graph, "s")
    assert {r.handle for r in found.reached} == {"a", "c"}
    beyond, _ = past_asked(graph, "s", found)
    assert {r.handle: (r.depth, past) for r, past in beyond} == {
        "b": (2, "c"),
        "p1": (2, "a"),
        "p2": (3, "a"),
        "x": (3, "c"),
    }


def test_a_history_line_says_a_rule_s_causes_in_words() -> None:
    """A step's moves by the rule read as their reasons and the edge each
    came by, never as a list's repr."""
    step = versions.Step(
        "s2",
        Command.UPDATE,
        "v2",
        "p.md:3:1",
        "t",
        "same",
        causes=(
            {
                "moved_by_rule": [
                    {
                        "why": "its meaning moved by rule",
                        "from": "d",
                        "via": "c → uses d",
                    },
                    {"why": "asked by the rule", "from": "e", "via": None},
                ]
            },
        ),
    )
    line = _step_line(step)
    assert line.endswith(
        "moved_by_rule: its meaning moved by rule (via c → uses d); asked by the rule"
    )
    assert "{" not in line and "[" not in line
