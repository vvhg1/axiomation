"""The contribution session: one input and one output, by block, served
its tools; its brief and its forager; the check's `checked` and novelty
lines, a found work named by its identifier and its pages read where the
`fetch` tool put it; a novelty finding filed with the pages it read,
stamped; a checked line filed as a check of the words it quotes. And the
gate's fetch of a session that still fetches by its sheet's line.

On the review tests' synthetic paper (`thm:bound`, "The output is bounded.")
and the citation tests' (`s` citing `able1998`). No model is called and
nothing is fetched: the paper's sources are a shelf that a fake fetcher
fills.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from axiomation import edits, evidence, queries, review, versions
from axiomation.evidence import Origin, Sources
from axiomation.questions import REVIEW_TOOLS, load_template
from axiomation.questions import review_file as rf
from axiomation.records import Aspect
from axiomation.resolver import Resolver
from axiomation.runner import Runner
from runner.fakes import FakeHost
from test_edits import built_state
from test_evidence import Shelf
from test_update import FLAT

CONTRIBUTION = rf.Session.CONTRIBUTION
FOUND = "arxiv:2101.00001"
CHECKED = (
    'checked "The output is bounded." — bounded output of a gain map — '
    "arxiv:2101.00001 states it"
)
NOVELTY = (
    f'find novelty work:{FOUND} 2 pages {FOUND}:2 "The output is bounded." — '
    "its Lemma 2 states the bound for every gain map — cite it"
)


@pytest.fixture
def opened(tmp_path, capsys):
    store, _ = built_state(tmp_path, capsys)
    kept = versions.latest(store)
    assert kept is not None
    with edits.opened(store, kept) as found:
        yield found


def contribution(opened, sources: Sources | None = None) -> review.Brief:
    paper = Path(opened.store.directory).parent / "paper"
    return review.brief(
        opened,
        CONTRIBUTION,
        load_template("review-contribution"),
        sources,
        directory=paper,
        forager="haiku",
    )


def answer(*lines: str, on: str = "thm:bound") -> str:
    """The session's lines under one block's heading."""
    return f"@@ {on}\n" + "\n".join(lines) + "\n"


def test_the_session_has_one_input_one_output_its_tools_and_its_forager(
    opened,
) -> None:
    found = contribution(opened)
    workspace = found.question.workspace
    assert workspace is not None and workspace.gate is not None
    assert found.subjects == {}
    assert sorted(workspace.files) == [rf.PAPER_FILE, rf.REVIEW_FILE]
    assert workspace.files[rf.REVIEW_FILE] == ""
    paper = workspace.files[rf.PAPER_FILE]
    assert not any(line.startswith(("→ ", "⊢ ")) for line in paper.split("\n"))
    assert workspace.tools == REVIEW_TOOLS
    served = workspace.served
    assert served is not None and served.fetches and served.reads is not None
    assert served.reads.works == ()
    (forager,) = workspace.helpers
    assert forager.name == "forager" and forager.tools[0] == "WebSearch"
    assert "mcp__q__fetch" in forager.tools and "mcp__q__cited_page" in forager.tools
    assert found.spec["fetch"] == {
        "paper": str((Path(opened.store.directory).parent / "paper").resolve()),
        "store": str(Path(opened.store.directory).resolve()),
    }
    brief = workspace.instructions
    assert "<!--" not in brief and "## The cited works" in brief
    assert "`fetch` with `arxiv:ID` or `doi:DOI`" in brief
    assert (
        "what the paper says a cited\n  work states is the citation reader's" in brief
    )
    assert "axiomation" not in brief and "works.md" not in brief


def test_on_a_citing_paper_its_blocks_carry_their_works_and_a_finding_reads_one(
    tmp_path,
) -> None:
    """On a paper that cites, each block carries the works it cites, and
    the session reads them by its tools: a novelty finding by block on a
    cited work's page is filed with the page stamped; a page of a work the
    finding does not name, or one the work does not hold, is refused."""
    from types import SimpleNamespace

    from test_citations import ABLE, BAKER
    from test_citations import built as citing_paper

    store, kept = citing_paper(tmp_path)
    sources = Sources([Shelf(Origin.LIBRARY, {"able1998": ABLE, "baker1984": BAKER})])
    opened = SimpleNamespace(store=store, state=kept, sheet="the paper's sheet")
    found = review.brief(
        opened,
        CONTRIBUTION,
        load_template("review-contribution"),
        sources,
        directory=tmp_path / "paper",
        forager="haiku",
    )
    assert found.spec["laid"] == ["able1998", "baker1984"]
    workspace = found.question.workspace
    assert workspace is not None and workspace.served is not None
    assert workspace.served.reads is not None
    assert workspace.served.reads.works == ("able1998", "baker1984")
    s = queries.View(store, kept).readings.at.handle("s")
    checked = 'checked "Able shows f is small" — small gains — able1998 states it'
    novelty = (
        'find novelty work:able1998 2 pages able1998:12 "Able shows f is '
        'small" — Able states it on p. 12 — say it is known'
    )
    result = rf.checked(found.spec, answer(checked, novelty, rf.DONE, on=s))
    assert result.problems == ()
    records, problems = review.records(
        queries.View(store, kept), found, result.sheet, sources
    )
    assert problems == []
    (finding,) = records.findings
    assert [p.name for p in finding.pages] == ["able1998:12"]

    unnamed = novelty.replace("pages able1998:12", "pages baker1984:4")
    refused = rf.checked(found.spec, answer(checked, unnamed, rf.DONE, on=s))
    assert any("(baker1984 is not)" in p for p in refused.problems)
    unheld = novelty.replace("able1998:12", "able1998:40")
    refused = rf.checked(found.spec, answer(checked, unheld, rf.DONE, on=s))
    assert any("able1998:40" in p for p in refused.problems)


@pytest.mark.parametrize(
    ("line", "wanted"),
    [
        (
            'find novelty 2 "The output is bounded." — known — cite it',
            "then names the work it rests on",
        ),
        (
            f'find novelty work:{FOUND} 2 pages arxiv:9999.99999:1 "The output is '
            'bounded." — known — cite it',
            "named as its `work:KEY` anchor (arxiv:9999.99999 is not)",
        ),
        (
            'checked "the output is unbounded" — searched — nothing',
            "is found 0 times in thm:bound",
        ),
        (f"fetch {FOUND}", "fetched by your `fetch` tool, not a line"),
    ],
)
def test_the_check_refuses_each_bad_line(opened, line, wanted) -> None:
    found = contribution(opened)
    problems = rf.checked(found.spec, answer(CHECKED, line, rf.DONE)).problems
    assert any(wanted in p for p in problems), problems


def _sheet(found: review.Brief, *lines: str) -> str:
    """The lines under `thm:bound` as the sheet reads them."""
    return rf.checked(found.spec, answer(*lines, rf.DONE)).text


def test_the_check_reads_what_was_checked_and_found(opened, monkeypatch) -> None:
    """A found work named by its identifier is one the `fetch` tool
    fetched: the check reads it as fetched, a DOI lower-cased, as DOIs
    compare. A session with no checked line is told so."""
    found = contribution(opened)
    _, _, sources = shelf_and_fetcher()
    assert sources.fetch(FOUND).pages  # as the tool fetched it
    monkeypatch.setattr(evidence, "sources_for", lambda paper, store: sources)
    read = rf.read(found.spec, _sheet(found, CHECKED, NOVELTY))
    assert read.problems == ()
    assert read.fetches == (FOUND,)
    (checked,) = read.checked
    assert (checked.anchor, checked.found) == ("thm:bound", f"{FOUND} states it")
    (novelty,) = read.finds
    assert novelty.aspect is Aspect.NOVELTY and novelty.pages == ((FOUND, 2),)
    doi = rf.read(
        found.spec,
        _sheet(
            found,
            CHECKED,
            'find novelty work:doi:10.1000/Abc 2 "The output is bounded." — known '
            "— cite it",
        ),
    )
    assert doi.problems == () and doi.fetches == ("doi:10.1000/abc",)
    unchecked = rf.checked(found.spec, f"{rf.DONE}\n").problems
    assert any(p.startswith("no checked line") for p in unchecked)


def test_a_found_work_s_page_reads_where_the_tool_fetched_it(
    opened, monkeypatch
) -> None:
    """A finding's page of a found work is read in the paper's cache, where
    the `fetch` tool put it: one the work does not hold, or a work never
    fetched, is refused; nothing is fetched by the check."""
    found = contribution(opened)
    _, asked, sources = shelf_and_fetcher()
    assert sources.fetch(FOUND).pages
    monkeypatch.setattr(evidence, "sources_for", lambda paper, store: sources)

    held = rf.checked(found.spec, answer(CHECKED, NOVELTY, rf.DONE))
    assert held.problems == ()
    missing = NOVELTY.replace(f"{FOUND}:2", f"{FOUND}:9")
    problems = rf.checked(found.spec, answer(CHECKED, missing, rf.DONE)).problems
    assert any(f"{FOUND}:9" in p for p in problems)
    never = NOVELTY.replace(FOUND, "arxiv:2202.00002")
    problems = rf.checked(found.spec, answer(CHECKED, never, rf.DONE)).problems
    assert any("arxiv:2202.00002:2" in p for p in problems)
    assert asked == [FOUND]


#: A page's text long enough to read as a page, not as a stub.
PROSE = "The output of every gain map is bounded by its gain. " * 12


def shelf_and_fetcher() -> tuple[Shelf, list[str], Sources]:
    shelf = Shelf(Origin.TRANSIENT, {})
    asked: list[str] = []

    def fetcher(key: str) -> str:
        asked.append(key)
        if key != FOUND:
            return "no open copy found"
        shelf.texts[key] = {1: "Contents. " + PROSE, 2: "Lemma 2. " + PROSE}
        return ""

    return shelf, asked, Sources([shelf], fetcher=fetcher)


def run_answer(opened, found: review.Brief, sources: Sources, text: str) -> None:
    host = FakeHost(lambda p: "", edits={rf.REVIEW_FILE: lambda _: text})
    report = Runner(host, Resolver(opened.store), 10.0, FLAT).run(
        [found.question],
        review.checker(opened, found, sources, edits.agent(host.model, None)),
    )
    assert report.answered == (found.question.key,), report


def test_a_novelty_finding_is_filed_with_the_fetched_pages_it_read(
    opened, monkeypatch
) -> None:
    found = contribution(opened)
    shelf, _, sources = shelf_and_fetcher()
    assert sources.fetch(FOUND).pages  # as the tool fetched it
    monkeypatch.setattr(evidence, "sources_for", lambda paper, store: sources)
    run_answer(opened, found, sources, answer(CHECKED, NOVELTY, rf.DONE))
    held = opened.store.judgment_records()
    (finding,) = held.findings
    assert finding.aspect is Aspect.NOVELTY
    assert [a.work for a in finding.anchors if a.work] == [FOUND]
    assert [p.name for p in finding.pages] == [f"{FOUND}:2"]
    assert shelf.texts[FOUND][2].startswith("Lemma 2.")
    # `open` shows the pages it rests on
    view = queries.View(opened.store, opened.state)
    (read,) = view.readings.findings
    assert queries.finding_line(read).endswith(f"pages {FOUND}:2  (by fake fake/model)")
    assert queries.finding_data(view, read)["pages"] == [
        queries.stamp_data(finding.pages[0])
    ]
    assert queries.finding_data(view, read)["pages"][0]["name"] == f"{FOUND}:2"


def test_a_checked_line_is_filed_as_a_check_of_the_words_it_quotes(
    opened, monkeypatch
) -> None:
    """The session's checked line under `thm:bound` is filed as a check of
    the theorem's block and the words it quotes, what it searched and what
    it found: a `positioning` claim's reading (`positioning`)."""
    found = contribution(opened)
    _, _, sources = shelf_and_fetcher()
    assert sources.fetch(FOUND).pages  # as the tool fetched it
    monkeypatch.setattr(evidence, "sources_for", lambda paper, store: sources)
    run_answer(opened, found, sources, answer(CHECKED, NOVELTY, rf.DONE))
    (filed,) = opened.store.judgment_records().checks
    at = opened.state
    assert filed.state == at.id
    view = queries.View(opened.store, at)
    assert filed.subject.node == view.node(view.resolve("thm:bound"))
    assert filed.subject.words == "The output is bounded."
    assert (filed.searched, filed.found) == (
        "bounded output of a gain map",
        "arxiv:2101.00001 states it",
    )


def test_a_fetch_that_runs_long_or_raises_is_a_failure() -> None:
    """The `fetch` tool's fetch within its time: one past it is left
    behind (None), one that raises is a failure, never evidence."""
    import threading

    release = threading.Event()

    def fetcher(key: str) -> str:
        if key == FOUND:
            release.wait(5)
            return "late"
        raise RuntimeError("the resolver cache is locked")

    sources = Sources([Shelf(Origin.TRANSIENT, {})], fetcher=fetcher)
    assert rf.fetched_within(sources, FOUND, 0.2) is None
    release.set()
    failed = rf.fetched_within(sources, "doi:10.1000/x", 1)
    assert failed is not None and not failed.pages
    assert failed.detail == "the fetch failed: the resolver cache is locked"


def test_its_sheet_s_header_names_no_fetch_line() -> None:
    """A session by block fetches by its tool: the sheet its answer is
    stored as teaches no fetch line, which its check refuses."""
    header = rf.render(rf.Session.CONTRIBUTION, [])
    assert rf.CHECKED_FORM in header and "fetch arxiv" not in header
