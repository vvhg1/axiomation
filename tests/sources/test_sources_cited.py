"""The reads of a paper's cited works a session's tool server serves it
(`sources.cited.QueueReads`): a page fenced and kept as served, a disputed
page's image named, a work outside the session's refused, a grep over every
file of a work it reads, a search of its works alone; nothing is fetched.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("qdrant_client")

from axiomation import queue  # noqa: E402
from axiomation.queue import ReadError  # noqa: E402
from axiomation.sources import cited as cited_module  # noqa: E402
from axiomation.sources.config import ResolvedSources  # noqa: E402
from axiomation.sources.library import client as client_module  # noqa: E402
from axiomation.sources.library import qdrant as qdrant_module  # noqa: E402
from sources.fakes import FakeQdrant  # noqa: E402

KEY = "hardy1949"


@pytest.fixture
def wired(
    monkeypatch: pytest.MonkeyPatch,
    fake_qdrant: FakeQdrant,
    settings: ResolvedSources,
    fixture_pdf: Path,
) -> FakeQdrant:
    """The corpus: two pages of one work, the second disputed."""
    monkeypatch.setattr(qdrant_module, "connect", lambda _: fake_qdrant)
    monkeypatch.setattr(client_module, "connect", lambda _: fake_qdrant)
    for page in (1, 2):
        fake_qdrant.add_chunk(
            settings.collection,
            page_number=page,
            chunk_index=page - 1,
            text=f"page {page} " + "y" * 400,
            file_path=str(fixture_pdf),
        )
    fake_qdrant.points(settings.collection)[-1].payload["ocr_disputed"] = 2
    return fake_qdrant


# --- a queue session's reads ------------------------------------------------------


@pytest.fixture
def queued(
    sources_paper: Path,
    wired: FakeQdrant,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> cited_module.QueueReads:
    """A queue session's reads of the one work, its search's query embedded
    by nothing."""
    from axiomation.questions import Reads
    from axiomation.sources.providers import sources_for

    monkeypatch.setenv("AXIOMATION_VOYAGE_API_KEY", "fixture-key")
    monkeypatch.setattr(
        client_module.SourceReader,
        "embed_query",
        lambda self, text: ([0.0] * 8, {"indices": [1], "values": [1.0]}),
    )
    store = tmp_path / "store"
    store.mkdir()
    sources = sources_for(sources_paper, store, fetch=False)
    return cited_module.QueueReads(
        sources, Reads(str(sources_paper), str(store), (KEY,))
    )


def _read(reads: cited_module.QueueReads, name: str, **args: object) -> object:
    (read,) = (r for r in reads.tools if r.tool.name == name)
    return read.answer(None, args)  # type: ignore[arg-type]


def test_a_queue_session_reads_a_page_fenced_and_it_is_kept_as_served(
    queued: cited_module.QueueReads,
) -> None:
    text = _read(queued, "cited_page", key=KEY, page=1)

    assert f'<source-text key="{KEY}" page-index="1" status="ok"' in str(text)
    assert queued.served == {(KEY, 1)}


def test_a_queue_session_s_disputed_page_names_its_tool(
    queued: cited_module.QueueReads,
) -> None:
    text = str(_read(queued, "cited_page", key=KEY, page=2))

    assert f"read the page as an image (`cited_image` {KEY} 2)" in text


def test_a_queue_session_reads_no_more_pages_at_once_than_the_cap(
    queued: cited_module.QueueReads,
) -> None:
    with pytest.raises(ReadError, match="at most 3 at a time"):
        _read(queued, "cited_page", key=KEY, page=1, to=4)


def test_a_queue_session_reads_no_work_outside_its_own(
    queued: cited_module.QueueReads,
) -> None:
    with pytest.raises(
        ReadError, match=f"nosuch1999 is not a work this session reads: they are {KEY}"
    ):
        _read(queued, "cited_page", key="nosuch1999", page=1)
    with pytest.raises(ReadError, match="not a work this session reads"):
        _read(queued, "cited_search", query="series", key="nosuch1999")


def test_a_search_points_at_pages_of_the_session_s_works_and_reads_none_whole(
    queued: cited_module.QueueReads, wired: FakeQdrant, settings: ResolvedSources
) -> None:
    wired.add_chunk(
        settings.collection,
        bib_key="other2001",
        page_number=1,
        chunk_index=0,
        text="page 1 of a work the session does not read",
    )

    text = str(_read(queued, "cited_search", query="page"))

    assert text.splitlines()[0].startswith(f"1. {KEY}:")
    assert "other2001" not in text
    assert queued.served == set()
    long_hit = next(x for x in text.splitlines() if "yyy" in x)
    assert long_hit.endswith("…") and len(long_hit) < cited_module.HIT_CHARS + 40


def test_a_queue_session_s_grep_is_capped(
    queued: cited_module.QueueReads, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cited_module, "GREP_LINES_MAX", 2)

    text = str(_read(queued, "cited_grep", key=KEY, pattern="page"))

    assert text.splitlines()[-1].endswith("lines more: narrow the pattern")
    assert queued.served == set()


def test_a_queue_session_s_image_is_a_picture_and_its_page_is_served(
    queued: cited_module.QueueReads,
) -> None:
    pytest.importorskip("pypdfium2")

    shown = _read(queued, "cited_image", key=KEY, page=1)

    assert isinstance(shown, queue.Image) and shown.png.startswith(b"\x89PNG")
    assert queued.served == {(KEY, 1)}
    # seen as an image, so a page with no usable text may be named
    assert queued.imaged == {(KEY, 1)}


def test_the_corpus_is_read_without_its_fetcher(sources_paper: Path) -> None:
    from axiomation.sources.providers import sources_for

    assert sources_for(sources_paper, fetch=False).fetcher is None


def test_a_grep_reads_every_file_of_the_work_the_session_reads(
    sources_paper: Path,
    wired: FakeQdrant,
    settings: ResolvedSources,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One grep on a work's key searches its other files too, each named
    by its key; a file the session was not given stays out."""
    from axiomation.evidence import Sources
    from axiomation.questions import Reads
    from axiomation.sources.providers import sources_for

    other = f"{KEY}@0a1b2c3d"
    wired.add_chunk(
        settings.collection,
        bib_key=other,
        page_number=1,
        chunk_index=0,
        text="page 1 of the translation " + "y" * 400,
    )
    monkeypatch.setattr(Sources, "other_files", lambda self, key: (other,))
    store = tmp_path / "store"
    store.mkdir()
    sources = sources_for(sources_paper, store, fetch=False)

    def grep(works: tuple[str, ...]) -> str:
        reads = cited_module.QueueReads(
            sources, Reads(str(sources_paper), str(store), works)
        )
        return str(_read(reads, "cited_grep", key=KEY, pattern="page 1"))

    alone = grep((KEY,))
    assert f"{KEY}  /page 1/  [ok]" in alone and other not in alone  # not given
    both = grep((KEY, other))
    assert f"{KEY}  /page 1/  [ok]" in both
    assert f"{other}  /page 1/  [ok]  1 line(s) on 1 page(s)" in both


def test_a_queue_session_s_locate_names_its_own_page_tool_for_a_page(
    queued: cited_module.QueueReads,
) -> None:
    with pytest.raises(ReadError, match=f"`cited_page` {KEY} 2 reads it"):
        _read(queued, "cited_locate", key=KEY, label="p. 2")


def test_a_queue_session_s_toc_says_a_work_has_no_outline_and_serves_no_page(
    queued: cited_module.QueueReads,
) -> None:
    text = str(_read(queued, "cited_toc", key=KEY))

    assert f"{KEY} has no outline and no Contents heading" in text
    assert queued.served == set()


def test_a_refused_key_among_many_names_those_that_start_as_it_does() -> None:
    """Beyond a few works, the refusal counts them and names the keys that
    start as the refused one does, so a work is found by its author's name
    with no search."""
    from axiomation.sources.cited import KEYS_LISTED, _listed

    works = tuple(f"able{i}" for i in range(KEYS_LISTED)) + (
        "squeakinger2105",
        "squeakinger2107",
    )
    assert _listed(works, "squeakinger2106") == (
        f"they are {KEYS_LISTED + 2} works; those whose key starts `squeakinger`: "
        "squeakinger2105, squeakinger2107"
    )
    assert _listed(works, "zeno2000").endswith("none whose key starts `zeno`")
    assert _listed(("a", "b"), "c") == "they are a, b"
