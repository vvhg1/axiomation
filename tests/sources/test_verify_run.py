"""`sources verify`: the quotation and locator sites of a
kept state, checked against the cited work's pages, and what settles filed
as code: a quotation printed is a `verified` cited judgment, one a word
away `not-shown` naming the edit, a label printed nowhere a finding; a
label found files nothing. A second run files nothing; a record that
lapsed is checked again.

A synthetic one-line paper read by the reader itself, so the kept state's
version is its text's; its work `knuth1997` a fake library of twelve
pages. No network, no corpus, no model.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("qdrant_client")

from axiomation.evidence import FileStamp, Origin, ReadStatus, Sources  # noqa: E402
from axiomation.exchange import document_payload  # noqa: E402
from axiomation.judgments import (  # noqa: E402
    StatusKind,
    answer_finding,
    readings,
    see_files,
)
from axiomation.reader import document_hash, read_paper  # noqa: E402
from axiomation.records import (  # noqa: E402
    Aspect,
    FindingStatus,
    RecordStore,
    Verdict,
)
from axiomation.sites import SiteKind  # noqa: E402
from axiomation.sources import cli as sources_cli  # noqa: E402
from axiomation.sources.verify import run  # noqa: E402
from axiomation.sources.verify.run import VERIFY, verify, verify_lines  # noqa: E402
from test_evidence import Shelf  # noqa: E402
from test_judgments import graph  # noqa: E402
from test_versions import keep  # noqa: E402

PRINTED = "rate at which the running mean settles on its limit"
BLOCKS = [
    ("d", "Let f be a gain."),
    (
        "q",
        f'They define the "{PRINTED}" of the chain \\autocite[p.~3]{{knuth1997}}.',
    ),
    (
        "c",
        'A second reading says the "rate at which the running mean '
        'closes on its limit" \\autocite[p.~3]{knuth1997}.',
    ),
    ("g", "The general case is \\autocite[Theorem~9.9.9]{knuth1997}."),
    ("t", "The theorem is \\autocite[Theorem~4.1.2]{knuth1997}."),
]
TEXT = " ".join(words for _, words in BLOCKS)
FILLER = "A page of the book that prints nothing this paper looks for, at length."
PAGES = {i: f"{FILLER} Page {i}." for i in range(1, 13)}
PAGES[3] = f"Definition 4.1.1. The {PRINTED} mu is defined as follows, for every x."
PAGES[7] = "Theorem 4.1.2 (Holder) When the gain is positive, each bound holds."


def library(pages=PAGES) -> Sources:
    return Sources([Shelf(Origin.LIBRARY, {"knuth1997": dict(pages)})])


@pytest.fixture
def paper(tmp_path):
    root = tmp_path / "paper"
    root.mkdir()
    (root / "p.md").write_text(TEXT, encoding="utf-8")
    return root


@pytest.fixture
def kept(paper, tmp_path):
    """The paper kept as one state, its version its text's, the document
    half the reader's (its citations and quotations)."""
    document = read_paper(paper)
    payload = graph(TEXT, BLOCKS, [], [])
    payload["document"] = document_payload(document)["document"]
    store = RecordStore(tmp_path / "store")
    return store, keep(store, document_hash(document), TEXT, payload)


@pytest.fixture
def reading(monkeypatch):
    def use(sources: Sources) -> None:
        monkeypatch.setattr(run, "sources_for", lambda paper, store=None: sources)

    use(library())
    return use


def test_a_run_files_what_settles(paper, kept, reading) -> None:
    store, state = kept
    found, at = verify(paper, store.directory)
    assert at == state.id
    exact, changed = found.quotations
    assert (exact.verdict, exact.note, exact.how) == (
        Verdict.VERIFIED,
        "exact",
        "locator",
    )
    assert changed.verdict is Verdict.NOT_SHOWN
    assert '"closes" in the paper, "settles" on the page' in changed.note

    records = store.judgment_records()
    by_block = {n.node: n.block for n in state.nodes}
    judged = {by_block[j.subject.node]: j for j in records.judgments}
    assert set(judged) == {"q", "c"}
    for j in judged.values():
        assert j.aspect is Aspect.CITED and j.depth.value == "computational"
        assert j.subject.work == "knuth1997"
        (stamp,) = j.pages
        assert (stamp.key, stamp.page_index, stamp.read_status) == (
            "knuth1997",
            3,
            ReadStatus.OK,
        )
    assert judged["q"].verdict is Verdict.VERIFIED
    assert judged["c"].verdict is Verdict.NOT_SHOWN
    assert "misquoted" in judged["c"].evidence and "closes" in judged["c"].evidence

    outcomes = {c.site.block: c.outcome.value for c in found.locators}
    assert outcomes == {
        "q": "not-checked",
        "c": "not-checked",
        "g": "not-printed",
        "t": "found",
    }
    (finding,) = records.findings
    assert finding.severity == 2 and finding.aspect is Aspect.CITED
    assert finding.problem.startswith("Theorem~9.9.9 is printed nowhere in knuth1997")
    assert {a.kind.value for a in finding.anchors} == {"span", "work"}

    read = readings(store, state)
    assert {r.author for r in read.reads} == {VERIFY}
    assert {f.author for f in read.findings} == {VERIFY}


def test_a_second_run_files_nothing(paper, kept, reading) -> None:
    store, _ = kept
    verify(paper, store.directory)
    before = store.judgment_records()
    again, _ = verify(paper, store.directory)
    assert again.quotations == []
    assert [c.site.block for c in again.locators] == ["q", "c", "t"]
    assert {s.block for s in again.held} == {"q", "c", "g"}
    after = store.judgment_records()
    assert len(after.judgments) == len(before.judgments)
    assert len(after.findings) == len(before.findings)


def test_a_dismissed_locator_finding_is_not_filed_again(paper, kept, reading) -> None:
    """A person's dismissal holds until the citation changes: the verifier
    holds an answered finding as it holds an open one."""
    store, _ = kept
    verify(paper, store.directory)
    findings = store.judgment_records().findings
    assert findings
    for f in findings:
        answer_finding(store, f.id, FindingStatus.DISMISSED, "not a problem")
    again, _ = verify(paper, store.directory)
    assert len(store.judgment_records().findings) == len(findings)
    assert not any(k is SiteKind.LOCATOR for k, _ in again.filed)


def test_a_dry_run_files_nothing(paper, kept, reading) -> None:
    store, _ = kept
    payload, lines = verify_lines(paper, store.directory, None, None, True)
    assert "dry run: nothing filed" in lines[0]
    assert all(q["filed"] is None for q in payload["quotations"])
    records = store.judgment_records()
    assert records.judgments == () and records.findings == ()


def test_a_judgment_its_file_lapsed_is_checked_again(paper, kept, reading) -> None:
    store, state = kept
    verify(paper, store.directory)
    (first,) = [
        j for j in store.judgment_records().judgments if j.verdict is Verdict.VERIFIED
    ]
    see_files(store, [FileStamp("knuth1997", ReadStatus.OK, "0" * 64, "other")], VERIFY)
    lapsed = [
        s
        for s in readings(store, state).statuses
        if s.aspect is Aspect.CITED and s.status is StatusKind.LAPSED
    ]
    assert lapsed

    again, _ = verify(paper, store.directory)
    assert [q.site.block for q in again.quotations] == ["q", "c"]
    assert len(store.judgment_records().judgments) == 4
    assert first.id in {r.judgment.id for r in readings(store, state).reads}


def test_a_work_no_source_holds_settles_nothing(paper, kept, reading) -> None:
    store, _ = kept
    reading(Sources([]))
    found, _ = verify(paper, store.directory)
    assert [q.verdict for q in found.quotations] == [None, None]
    assert {c.outcome.value for c in found.locators} == {"not-checked", "inconclusive"}
    records = store.judgment_records()
    assert records.judgments == () and records.findings == ()


def test_the_command_prints_and_files(paper, kept, reading, capsys) -> None:
    store, _ = kept
    code = sources_cli.main(
        ["verify", str(paper), "--store", str(store.directory), "--json"]
    )
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert [q["verdict"] for q in payload["quotations"]] == ["verified", "not-shown"]
    assert all(q["filed"] for q in payload["quotations"])
    printed = [c for c in payload["locators"] if c["outcome"] == "not-printed"]
    assert printed[0]["filed"]

    code = sources_cli.main(["verify", str(paper), "--store", str(store.directory)])
    out = capsys.readouterr().out
    assert code == 0 and out.count("checked before") == 3


def test_a_store_with_no_state_is_refused(paper, tmp_path, reading, capsys) -> None:
    code = sources_cli.main(["verify", str(paper), "--store", str(tmp_path / "empty")])
    assert code == 1
    assert "keeps no state" in capsys.readouterr().err
