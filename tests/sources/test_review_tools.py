"""A review session's tools, served by block: `fetch` of a found work, read
then by the same `cited_*` tools under its key; each work tried once; an
identifier of another form, or a work the session reads already, refused
or told so. On a shelf a fake fetcher fills: nothing is fetched."""

from __future__ import annotations

from pathlib import Path

import pytest

from axiomation import evidence, review_tools
from axiomation.evidence import Origin, Sources
from axiomation.questions import Reads
from axiomation.sources.cited import QueueReads
from test_evidence import Shelf

FOUND = "arxiv:2101.00001"
PROSE = "The output of every gain map is bounded by its gain. " * 12


@pytest.fixture
def tools(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> review_tools.ReviewTools:
    shelf = Shelf(Origin.TRANSIENT, {"cited2020": {1: PROSE}})

    def fetcher(key: str) -> str:
        if key != FOUND:
            return "no open copy found"
        shelf.texts[key] = {1: "Contents. " + PROSE, 2: "Lemma 2. " + PROSE}
        return ""

    sources = Sources([shelf], fetcher=fetcher)
    monkeypatch.setattr(evidence, "sources_for", lambda paper, store: sources)
    monkeypatch.setattr(
        review_tools, "_cited_reads", lambda reads: QueueReads(sources, reads)
    )
    reads = Reads(str(tmp_path / "paper"), str(tmp_path / "store"), ("cited2020",))
    return review_tools.ReviewTools({}, tmp_path, reads, fetches=True)


def test_a_found_work_fetched_is_read_by_the_cited_tools(tools) -> None:
    names = [t.name for t in tools.tools]
    assert names[0] == "check" and names[-1] == "fetch"
    assert "cited_page" in names
    refused = tools.call("cited_page", {"key": FOUND, "page": 2})
    assert "not a work this session reads" in str(refused[0])

    said, refused = tools.call("fetch", {"work": FOUND})
    assert not refused and said.startswith(f"fetched {FOUND}: 2 pages")
    page, refused = tools.call("cited_page", {"key": FOUND, "page": 2})
    assert not refused and "Lemma 2." in str(page)
    assert tools.call("fetch", {"work": FOUND}) == (said, False)


def test_a_fetch_is_told_why_it_could_not_and_each_is_tried_once(tools) -> None:
    said, refused = tools.call("fetch", {"work": "doi:10.1000/Gone"})
    assert not refused
    assert said == "could not fetch doi:10.1000/gone: no open copy found"
    assert tools.call("fetch", {"work": "doi:10.1000/gone"}) == (said, False)
    said, refused = tools.call("fetch", {"work": "https://example.org/x.pdf"})
    assert refused and "`arxiv:ID` or `doi:DOI`" in said
    said, _ = tools.call("fetch", {"work": "cited2020"})
    assert "a work is fetched by" in said


def test_a_session_that_fetches_nothing_has_no_fetch(tmp_path) -> None:
    plain = review_tools.ReviewTools({}, tmp_path)
    assert [t.name for t in plain.tools] == ["check"]
    assert plain.call("fetch", {"work": FOUND})[1]
