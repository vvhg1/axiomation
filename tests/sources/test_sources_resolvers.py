"""DOI resolution and transient open copies."""

from __future__ import annotations

import json
import socket
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from axiomation.evidence import ReadStatus, Sources
from axiomation.sources.bib import BibRecord
from axiomation.sources.providers import Library, Transient
from axiomation.sources.read.grep import grep
from axiomation.sources.read.locate import LabelKind, locate
from axiomation.sources.read.render import render
from axiomation.sources.read.structure import contents
from axiomation.sources.resolvers import (
    ResolutionStatus,
    ResolvedSource,
    Resolver,
    providers,
)
from axiomation.sources.resolvers.base import open_cache
from axiomation.sources.resolvers.fetch import (
    fetch_found,
    fetch_source,
    public_addresses,
)


@pytest.fixture(autouse=True)
def full_tier(monkeypatch: pytest.MonkeyPatch) -> None:
    """The fetch as the library's tier runs it, whatever is installed: the
    plain tier's fetch is `test_sources_plain.py`'s."""
    from axiomation.sources.resolvers import fetch

    monkeypatch.setattr(fetch, "library_on", lambda settings: True)


@pytest.fixture
def provider_payloads(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        providers,
        "crossref",
        lambda *args, **kwargs: {
            "title": ["A Stable Result"],
            "container-title": ["Annals of Tests"],
            "author": [{"given": "Ada", "family": "Example"}],
            "issued": {"date-parts": [[2025, 3]]},
            "type": "journal-article",
            "publisher": "Example Press",
            "volume": "4",
            "issue": "2",
            "page": "10-20",
            "is-referenced-by-count": 7,
        },
    )
    monkeypatch.setattr(
        providers,
        "openalex",
        lambda *args, **kwargs: {
            "title": "A Stable Result",
            "cited_by_count": 9,
            "is_retracted": False,
        },
    )
    monkeypatch.setattr(
        providers,
        "unpaywall",
        lambda *args, **kwargs: {
            "best_oa_location": {
                "url_for_pdf": "https://example.org/result.pdf",
                "url": "https://example.org/result",
                "version": "publishedVersion",
                "host_type": "publisher",
            }
        },
    )
    monkeypatch.setattr(providers, "europe_pmc", lambda *args, **kwargs: {})
    monkeypatch.setattr(providers, "core", lambda *args, **kwargs: {})


def test_doi_forms_resolve_to_one_stable_source(
    tmp_path: Path, provider_payloads: None
) -> None:
    with Resolver(tmp_path / "sources.db", email="contact@example.org") as resolver:
        first = resolver.resolve("doi:10.1234/Example", bib_key="example2025")
        second = resolver.resolve("https://doi.org/10.1234/example")

    assert first == second
    assert first.status is ResolutionStatus.EXACT
    assert first.doi == "10.1234/example"
    assert first.item_type == "journalArticle"
    assert first.creators == ("Ada Example",)
    assert first.issued == "2025-3"
    assert first.citation_count == 9
    assert first.fingerprint
    assert first.candidates[0].url.endswith("result.pdf")


def test_retrieval_time_does_not_change_the_fingerprint(
    tmp_path: Path, provider_payloads: None
) -> None:
    with Resolver(tmp_path / "sources.db") as resolver:
        first = resolver.resolve("10.1234/example", fresh=True)
        second = resolver.resolve("10.1234/example", fresh=True)

    assert first.retrieved_at
    assert second.retrieved_at
    assert first.fingerprint == second.fingerprint


def test_provider_failure_is_unresolved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("crossref", "openalex", "unpaywall", "europe_pmc", "core"):
        monkeypatch.setattr(providers, name, lambda *args, **kwargs: {})

    with Resolver(tmp_path / "sources.db") as resolver:
        result = resolver.resolve("10.1234/missing")

    assert result.status is ResolutionStatus.UNRESOLVED
    assert result.candidates == ()


def test_a_failure_without_a_contact_address_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("crossref", "openalex", "unpaywall", "europe_pmc", "core"):
        monkeypatch.setattr(providers, name, lambda *args, **kwargs: {})

    with Resolver(tmp_path / "sources.db") as resolver:
        result = resolver.resolve("10.1234/missing")

    assert result.detail == (
        "Crossref did not answer (asked without a contact address); "
        "OpenAlex did not answer (asked without a contact address); "
        "Unpaywall skipped: no contact address"
    )
    assert not result.asked_with_contact


def test_a_work_asked_without_a_contact_address_is_asked_again_with_one(
    tmp_path: Path, provider_payloads: None
) -> None:
    path = tmp_path / "sources.db"
    with Resolver(path) as resolver:
        before = resolver.resolve("10.1234/example")
    with Resolver(path, email="contact@example.org") as resolver:
        after = resolver.resolve("10.1234/example")
        again = resolver._cached("10.1234/example")

    assert "Unpaywall skipped" in before.detail
    assert after.detail == "" and after.asked_with_contact
    assert again == after


def test_an_expired_merged_result_is_replaced(
    tmp_path: Path, provider_payloads: None
) -> None:
    path = tmp_path / "sources.db"
    cache = open_cache(path)
    cache.execute(
        "INSERT INTO sources VALUES (?, ?, ?, ?)",
        (
            "10.1234/example",
            "",
            json.dumps({"doi": "10.1234/example", "title": "Old title"}),
            (datetime.now(UTC) - timedelta(days=31)).isoformat(),
        ),
    )
    cache.commit()
    cache.close()

    with Resolver(path, email="contact@example.org") as resolver:
        result = resolver.resolve("10.1234/example")

    assert result.title == "A Stable Result"
    assert result.candidates[0].resolver == "unpaywall"


def _pdf(path: Path, text: str) -> bytes:
    from sources.pdfs import write_pdf

    return write_pdf(path, [text]).read_bytes()


def test_a_fetched_pdf_becomes_a_transient_exact_page_source(
    tmp_path: Path, settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("qdrant_client")  # the library that fails is Qdrant's
    body = _pdf(
        tmp_path / "source.pdf",
        "A Stable Result. Theorem 1.1. A divergent series is here. " * 4,
    )
    monkeypatch.setattr(
        "axiomation.sources.resolvers.fetch.download",
        lambda url: (body, url),
    )
    transient_settings = replace(settings, cache_dir=tmp_path / "cache")
    work = ResolvedSource(
        doi="10.1234/example",
        status=ResolutionStatus.EXACT,
        title="A Stable Result",
    )
    fetched = fetch_source(
        transient_settings,
        BibRecord("example2025", title="A Stable Result", doi=work.doi),
        work,
        url="https://example.org/result.pdf",
    )

    assert fetched.read_status is ReadStatus.TRANSIENT
    library = Library(transient_settings)
    monkeypatch.setattr(
        library.reader,
        "_ensure_readable",
        lambda: (_ for _ in ()).throw(RuntimeError("no collection")),
    )
    reader = Sources([library, Transient(transient_settings.cache_dir)])
    assert reader.page("example2025", 1).read_status is ReadStatus.TRANSIENT
    assert grep(reader, "example2025", "divergent").read_status is ReadStatus.TRANSIENT
    found = locate(
        reader,
        "example2025",
        "Theorem",
        "1.1",
        kind=LabelKind.RESULT,
    )
    assert found.read_status is ReadStatus.TRANSIENT
    assert found.top is not None
    assert contents(reader, "example2025").read_status is ReadStatus.TRANSIENT
    assert render(reader, "example2025", 1).read_status is ReadStatus.TRANSIENT


def test_a_found_doi_with_no_open_copy_says_what_it_names(
    tmp_path: Path, settings
) -> None:
    """A found work's DOI that resolves and has no open copy is named with
    its record, so a session can tell it is the work it means; one that
    resolves to nothing says so, as a DOI written from memory would."""
    transient_settings = replace(settings, cache_dir=tmp_path / "cache")
    work = ResolvedSource(
        doi="10.1234/example",
        status=ResolutionStatus.EXACT,
        title="A Stable Result",
        creators=("Ada Lovelace", "Carl Gauss", "Emmy Noether", "Leonhard Euler"),
        issued="1995-09-01",
        venue="Annals of Examples",
    )
    fetched = fetch_found(transient_settings, "doi:10.1234/example", lambda doi: work)
    assert fetched.read_status is not ReadStatus.TRANSIENT
    assert fetched.detail == (
        "the DOI resolves to Ada Lovelace, Carl Gauss, Emmy Noether et al. (1995). "
        "A Stable Result. Annals of Examples; no resolver found an open PDF "
        "candidate"
    )
    nothing = ResolvedSource(doi="10.1234/nothing", detail="crossref: 404")
    fetched = fetch_found(
        transient_settings, "doi:10.1234/nothing", lambda doi: nothing
    )
    assert fetched.detail.startswith("no resolver resolved the DOI (crossref: 404); ")


def test_a_web_candidate_that_does_not_match_is_not_stored(
    tmp_path: Path, settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = _pdf(tmp_path / "wrong.pdf", "A different publication entirely. " * 8)
    monkeypatch.setattr(
        "axiomation.sources.resolvers.fetch.download",
        lambda url: (body, url),
    )
    transient_settings = replace(settings, cache_dir=tmp_path / "cache")
    work = ResolvedSource(doi="10.1234/example", title="A Stable Result")

    fetched = fetch_source(
        transient_settings,
        BibRecord("example2025", title=work.title, doi=work.doi),
        work,
        url="https://example.org/wrong.pdf",
    )

    assert fetched.read_status is ReadStatus.INFRASTRUCTURE
    assert "is not the entry's work" in fetched.detail
    assert not (tmp_path / "cache" / "oa" / "example2025.pdf").exists()


def test_a_sibling_paper_by_the_same_authors_is_refused(
    tmp_path: Path, settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same authors and year, and half the title's words in a row: a
    library file would pass, a candidate found on the web does not."""
    body = _pdf(
        tmp_path / "sibling.pdf",
        "Plot Based Correcting for Missing Studies in Pooled Trials. "
        "Clara Oduya and Anton Brand. We test for studies left out. " * 3,
    )
    monkeypatch.setattr(
        "axiomation.sources.resolvers.fetch.download",
        lambda url: (body, url),
    )
    transient_settings = replace(settings, cache_dir=tmp_path / "cache")
    work = ResolvedSource(
        doi="10.5555/rank.2000",
        title="Rank Correcting for Missing Studies in Pooled Trials by Imputation",
        creators=("Clara Oduya", "Anton Brand"),
        issued="2000",
    )
    fetched = fetch_source(
        transient_settings,
        BibRecord("oduya2000", title=work.title, doi=work.doi, kind="article"),
        work,
        url="https://example.org/sibling.pdf",
    )
    assert fetched.read_status is ReadStatus.INFRASTRUCTURE
    assert "print no title page" in fetched.detail
    assert not (tmp_path / "cache" / "oa" / "oduya2000.pdf").exists()


def test_a_found_work_with_nothing_to_check_against_is_refused() -> None:
    from axiomation.sources.resolvers.fetch import _mismatch

    pages = [{"page_index": 1, "text": "Any words at all. " * 10}]
    why, undecided = _mismatch(ResolvedSource(""), BibRecord("doi:10.5555/x"), pages)
    assert why == "the resolver gave no title and no authors to check it against"
    assert not undecided


def test_a_private_fetch_target_is_refused() -> None:
    with pytest.raises(ValueError, match="non-public"):
        public_addresses("http://127.0.0.1/paper.pdf")


def test_two_keys_one_file_name_apart_keep_two_copies(tmp_path: Path) -> None:
    """Keys that differ where a character cannot stand in a file name keep
    a copy each; a key that is a file name is its own."""
    from axiomation.sources.read.transient import pdf_path

    slashed, underscored = (pdf_path(tmp_path, k) for k in ("doi:10.1/x", "doi:10.1_x"))
    assert slashed != underscored
    assert slashed.parent == underscored.parent == tmp_path / "oa"
    assert slashed.name.startswith("doi_10.1_x-")
    assert pdf_path(tmp_path, "able1998").name == "able1998.pdf"
    assert pdf_path(tmp_path, "../up").parent == tmp_path / "oa"


PDF_ANSWER = b"HTTP/1.1 200 OK\r\nContent-Length: 9\r\n\r\n%PDF-1.4\n"


def serving(
    monkeypatch: pytest.MonkeyPatch,
    resolve: Callable[[str], str],
    answers: dict[str, bytes],
) -> list[tuple[tuple[str, int], socket.socket]]:
    """The network as a fetch meets it, from memory, with no proxy
    configured: each hostname resolved by `resolve`, each connection to an
    address answered with `answers[address]`. Each connection made: where
    to, and its far end, which holds the request sent."""
    from axiomation.sources.resolvers import fetch

    def getaddrinfo(host: str, port: object, *_: object, **__: object) -> list:
        address = resolve(host)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 0))]

    made: list[tuple[tuple[str, int], socket.socket]] = []

    def create_connection(where, timeout=None, source=None):  # noqa: ANN001, ANN202, ARG001
        # a loopback pair stands for the connection: a TCP socket, as
        # `http.client` sets its options
        with socket.create_server(("127.0.0.1", 0)) as listener:
            ours = socket.socket()
            ours.connect(listener.getsockname())
            theirs = listener.accept()[0]
        theirs.sendall(answers[where[0]])
        made.append((where, theirs))
        return ours

    monkeypatch.setattr(fetch.socket, "getaddrinfo", getaddrinfo)
    monkeypatch.setattr(fetch.socket, "create_connection", create_connection)
    monkeypatch.setattr(fetch.urllib.request, "getproxies", dict)
    return made


def test_a_fetch_connects_to_the_address_its_check_found_public(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A host that resolves to a public address for the check and to
    127.0.0.1 after (a DNS answer that lives a second) is fetched from the
    address the check found public, under its own name."""
    from axiomation.sources.resolvers import fetch

    resolved = iter(["93.184.215.14", "127.0.0.1", "127.0.0.1"])
    made = serving(
        monkeypatch,
        lambda host: next(resolved),
        {"93.184.215.14": PDF_ANSWER, "127.0.0.1": b""},
    )

    body, url = fetch.download("http://papers.example.org/x.pdf")

    assert (body, url) == (b"%PDF-1.4\n", "http://papers.example.org/x.pdf")
    [(where, theirs)] = made
    assert where == ("93.184.215.14", 80)
    assert b"Host: papers.example.org\r\n" in theirs.recv(65536)


def test_a_redirect_into_the_local_network_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from axiomation.sources.resolvers import fetch

    moved = b"HTTP/1.1 302 Found\r\nLocation: http://intranet.example.org/\r\n\r\n"
    addresses = {
        "papers.example.org": "93.184.215.14",
        "intranet.example.org": "10.0.0.7",
    }
    made = serving(monkeypatch, addresses.__getitem__, {"93.184.215.14": moved})

    with pytest.raises(ValueError, match="non-public address 10.0.0.7"):
        fetch.download("http://papers.example.org/x.pdf")
    assert [where for where, _ in made] == [("93.184.215.14", 80)]


def test_an_oversized_fetch_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    from axiomation.sources.resolvers import fetch

    large = f"HTTP/1.1 200 OK\r\nContent-Length: {fetch.MAX_BYTES + 1}\r\n\r\n"
    serving(
        monkeypatch, lambda host: "93.184.215.14", {"93.184.215.14": large.encode()}
    )

    with pytest.raises(ValueError, match="larger"):
        fetch.download("http://example.org/large.pdf")


def test_a_fetch_that_cannot_connect_is_a_refusal_with_its_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from axiomation.sources.resolvers import fetch

    serving(monkeypatch, lambda host: "93.184.215.14", {})

    def refused(*_: object, **__: object) -> socket.socket:
        raise ConnectionRefusedError("connection refused")

    monkeypatch.setattr(fetch.socket, "create_connection", refused)

    with pytest.raises(ValueError, match="GET http://example.org/x.pdf failed"):
        fetch.download("http://example.org/x.pdf")


def test_a_configured_proxy_carries_the_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Where the person configured a proxy for the scheme, the fetch goes
    through it, as their other requests do: the proxy resolves the host."""
    from axiomation.sources.resolvers import fetch

    made = serving(monkeypatch, lambda host: "93.184.215.14", {})
    proxies = {"https": "http://proxy.example.org:3128"}
    monkeypatch.setattr(fetch.urllib.request, "getproxies", lambda: proxies)
    monkeypatch.setattr(fetch.urllib.request, "proxy_bypass", lambda host: False)
    asked: list[str] = []

    class Response:
        status = 200
        headers: dict[str, str] = {}

        def __enter__(self):  # noqa: ANN204
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def read(self, limit: int) -> bytes:  # noqa: ARG002
            return b"%PDF-1.4\n"

    class Opener:
        def open(self, request, timeout):  # noqa: ANN001, ANN202, ARG002
            asked.append(request.full_url)
            return Response()

    monkeypatch.setattr(fetch.urllib.request, "build_opener", lambda *_: Opener())

    assert fetch.download("https://papers.example.org/x.pdf")[0] == b"%PDF-1.4\n"
    assert asked == ["https://papers.example.org/x.pdf"]
    assert made == []


def test_a_fetched_works_disputed_page_stays_disputed(
    tmp_path: Path, settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fetched copy is read as the corpus is, its witness included: the
    pages it left disputed are named so when read back."""
    transient_settings = replace(settings, cache_dir=tmp_path / "cache")
    from axiomation.sources.contract import TextSource
    from axiomation.sources.ingest import extract as extract_module
    from axiomation.sources.ingest import ocr as ocr_module
    from axiomation.sources.ingest.extract import PageText
    from axiomation.sources.pdf import PageKind
    from axiomation.sources.read.transient import store_pages
    from axiomation.sources.resolvers import fetch as fetch_module
    from sources.fakes import StubOcr

    def extracted(path: Path, **_: object) -> list[PageText]:
        return [
            PageText(1, "clean", TextSource.PADDLE, PageKind.SCAN, disputed=0),
            PageText(2, "looped", TextSource.PADDLE, PageKind.SCAN, disputed=2),
        ]

    monkeypatch.setattr(extract_module, "extract_pdf", extracted)
    monkeypatch.setattr(ocr_module, "provider_for", lambda *a, **k: StubOcr())
    monkeypatch.setattr(ocr_module, "tesseract_provider", lambda: StubOcr())
    pages = fetch_module._extract_pages(
        tmp_path / "copy.pdf",
        settings=transient_settings,
        bib_key="example2025",
        title="A Stable Result",
    )
    store_pages(transient_settings.cache_dir, "example2025", pages)

    read = Transient(transient_settings.cache_dir).pages("example2025", 1, 2)

    assert [page.disputed for page in read] == [0, 2]
