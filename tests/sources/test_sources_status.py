"""`sources status` and `sources resolve`."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("qdrant_client")

from axiomation.evidence import ReadStatus, Sources  # noqa: E402
from axiomation.sources.config import ResolvedSources, Settings, load_settings  # noqa: E402
from axiomation.sources.contract import TextSource  # noqa: E402
from axiomation.sources.library.client import SourceReader  # noqa: E402
from axiomation.sources.providers import Library  # noqa: E402
from axiomation.sources.read.resolve import resolve  # noqa: E402
from axiomation.sources.read.status import collect  # noqa: E402
from sources.fakes import FakeQdrant, FakeZotero  # noqa: E402

CLOSED_PORT = "http://127.0.0.1:1"


@pytest.fixture
def stocked(reader: SourceReader, fake_qdrant: FakeQdrant, settings, fixture_pdf: Path):
    for page in (1, 2):
        fake_qdrant.add_chunk(
            settings.collection,
            page_number=page,
            chunk_index=page - 1,
            text=f"page {page} " + "y" * 400,
            file_path=str(fixture_pdf),
        )
    return reader


def test_status_names_the_collection_and_the_model(
    sources_config: Settings, settings: ResolvedSources, stocked: SourceReader
) -> None:
    report = collect(sources_config, zotero_url=CLOSED_PORT, reader=stocked)

    assert report.ok
    assert report.settings.collection == settings.collection
    assert report.settings.voyage_model == settings.voyage_model
    assert report.papers[0].bib_key == "hardy1949"
    assert report.chunks == 2


def test_status_fails_when_the_contract_does(
    sources_config: Settings,
    stocked: SourceReader,
    fake_qdrant: FakeQdrant,
    settings: ResolvedSources,
) -> None:
    fake_qdrant.sizes[settings.collection] = 2048

    report = collect(sources_config, zotero_url=CLOSED_PORT, reader=stocked)

    assert not report.ok
    assert "2048" in report.contract.problem
    assert report.papers == ()


def test_status_names_the_sources_with_unreadable_pages(
    sources_config: Settings,
    stocked: SourceReader,
    fake_qdrant: FakeQdrant,
    settings: ResolvedSources,
) -> None:
    fake_qdrant.add_chunk(
        settings.collection,
        bib_key="scan1890",
        chunk_index=0,
        ocr_backend=TextSource.FAILED.value,
    )

    assert collect(
        sources_config, zotero_url=CLOSED_PORT, reader=stocked
    ).unreadable == ("scan1890",)


def test_status_reports_an_unavailable_zotero_as_bib_only(
    sources_config: Settings, stocked: SourceReader
) -> None:
    assert (
        collect(sources_config, zotero_url=CLOSED_PORT, reader=stocked).zotero is False
    )


def test_status_reports_a_running_zotero(
    sources_config: Settings, stocked: SourceReader
) -> None:
    with FakeZotero() as fake:
        assert (
            collect(sources_config, zotero_url=fake.url, reader=stocked).zotero is True
        )


def test_status_reports_a_missing_api_key(
    sources_config: Settings, stocked: SourceReader, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("AXIOMATION_VOYAGE_API_KEY", raising=False)

    assert (
        collect(sources_config, zotero_url=CLOSED_PORT, reader=stocked).api_key is False
    )


def test_status_json_carries_no_secret(
    sources_paper: Path, stocked: SourceReader, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AXIOMATION_VOYAGE_API_KEY", "shell-secret")
    config = load_settings(sources_paper)

    payload = collect(config, zotero_url=CLOSED_PORT, reader=stocked).as_dict()

    assert payload["api_key"] is True
    assert payload["key_source"] == "AXIOMATION_VOYAGE_API_KEY in the shell"
    assert "shell-secret" not in str(payload)


def test_status_names_the_contact_address_or_what_its_want_costs(
    sources_paper: Path,
    stocked: SourceReader,
    monkeypatch: pytest.MonkeyPatch,
    user_file: Path,
) -> None:
    from axiomation.sources.cli import _status_lines

    label = "contact email"
    report = collect(
        load_settings(sources_paper), zotero_url=CLOSED_PORT, reader=stocked
    )
    assert report.as_dict()["contact_email"] is None
    (line,) = [x for x in _status_lines(report, verbose=False) if x[:13] == label]
    assert "MISSING: Unpaywall is skipped" in line
    assert f'contact_email = "…" in {user_file}' in line

    monkeypatch.setenv("AXIOMATION_CONTACT_EMAIL", "me@example.org")
    report = collect(
        load_settings(sources_paper), zotero_url=CLOSED_PORT, reader=stocked
    )
    assert report.as_dict()["contact_source"] == (
        "AXIOMATION_CONTACT_EMAIL in the shell"
    )
    (line,) = [x for x in _status_lines(report, verbose=False) if x[:13] == label]
    assert line.split(maxsplit=2)[2] == (
        "me@example.org  (AXIOMATION_CONTACT_EMAIL in the shell)"
    )


# --- resolve ---


def _library(reader: SourceReader) -> Sources:
    """The library over `reader`, as a paper's sources read it."""
    library = Library(reader.settings)
    library.reader = reader
    return Sources([library])


def test_resolving_an_indexed_source_reports_where_its_pdf_is(
    stocked: SourceReader, fixture_pdf: Path
) -> None:
    found = resolve(_library(stocked), "hardy1949", zotero_url=CLOSED_PORT)

    assert found.indexed
    assert found.chunks == 2
    assert found.file_path == str(fixture_pdf)
    assert found.file_exists
    assert found.read_status is ReadStatus.OK


def test_resolving_a_source_nobody_has_says_where_the_chain_stopped(
    stocked: SourceReader,
) -> None:
    found = resolve(_library(stocked), "nosuch1999", zotero_url=CLOSED_PORT)

    assert not found.indexed
    assert found.read_status is ReadStatus.ABSENT
    assert "nosuch1999" in found.detail


def test_resolving_offers_the_zotero_link_when_zotero_is_running(
    stocked: SourceReader, fixture_pdf: Path
) -> None:
    with FakeZotero(
        {"hardy1949": {"item_key": "L27CBAD8", "paths": [fixture_pdf]}}
    ) as fake:
        found = resolve(_library(stocked), "hardy1949", zotero_url=fake.url)

    assert found.zotero_link == "zotero://select/library/items/L27CBAD8"
    assert found.zotero_pdf == str(fixture_pdf)


def test_a_stale_file_path_is_reported_and_zotero_offers_another(
    stocked: SourceReader, fake_qdrant: FakeQdrant, settings, fixture_pdf: Path
) -> None:
    for point in fake_qdrant.points(settings.collection):
        point.payload["file_path"] = "/gone/hardy1949.pdf"

    with FakeZotero(
        {"hardy1949": {"item_key": "L27CBAD8", "paths": [fixture_pdf]}}
    ) as fake:
        found = resolve(_library(stocked), "hardy1949", zotero_url=fake.url)

    assert not found.file_exists
    assert found.zotero_pdf == str(fixture_pdf)


def test_a_stale_path_is_rendered_from_the_zotero_copy_instead(
    stocked: SourceReader, fake_qdrant: FakeQdrant, settings, fixture_pdf: Path
) -> None:
    from axiomation.sources.read.render import render

    for point in fake_qdrant.points(settings.collection):
        point.payload["file_path"] = "/gone/hardy1949.pdf"

    with FakeZotero(
        {"hardy1949": {"item_key": "L27CBAD8", "paths": [fixture_pdf]}}
    ) as fake:
        rendered = render(stocked, "hardy1949", 1, zotero_url=fake.url)

    assert rendered.read_status is ReadStatus.OK
    assert rendered.file_path == str(fixture_pdf)
