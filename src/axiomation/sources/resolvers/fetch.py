"""Safe download and validation of a transient open copy."""

from __future__ import annotations

import http.client
import ipaddress
import os
import socket
import ssl
import tempfile
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse, urlsplit, urlunsplit

from axiomation.evidence import ReadStatus
from axiomation.sources.bib import BibRecord
from axiomation.sources.config import ResolvedSources, library_on
from axiomation.sources.match import (
    FIRST_PAGES,
    FOUND_TITLE_RUN,
    check,
    expected_found,
    readable,
    scanned_window,
)
from axiomation.sources.read.transient import pdf_path, store_pages, unchecked_path
from axiomation.sources.resolvers.chain import (
    Candidate,
    ResolutionStatus,
    ResolvedSource,
)

MAX_BYTES = 100 * 1024 * 1024
MAX_REDIRECTS = 5


@dataclass(frozen=True, slots=True)
class Fetch:
    bib_key: str
    url: str = ""
    resolver: str = ""
    file_path: str = ""
    pages: int = 0
    read_status: ReadStatus = ReadStatus.INFRASTRUCTURE
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "bib_key": self.bib_key,
            "url": self.url,
            "resolver": self.resolver,
            "file_path": self.file_path,
            "pages": self.pages,
            "read_status": self.read_status.value,
            "detail": self.detail,
        }


#: A found work's key: `arxiv:ID` or `doi:DOI`.
ARXIV = "arxiv:"
DOI = "doi:"


def fetch_found(
    settings: ResolvedSources,
    key: str,
    resolve: Callable[[str], ResolvedSource],
    *,
    ocr_key: str = "",
) -> Fetch:
    """A work found outside the bibliography, fetched as an open
    copy under its key: an arXiv identifier from arXiv's own PDF, a DOI
    through `resolve`'s open-copy candidates, each checked as `fetch_source`
    checks a cited work's."""
    record = BibRecord(key)
    if key.startswith(ARXIV):
        ident = key[len(ARXIV) :]
        work = ResolvedSource(
            "", candidates=(Candidate(f"https://arxiv.org/pdf/{ident}", "arxiv"),)
        )
        if settings.cache_dir is None:
            return Fetch(key, detail="the paper has no source cache directory")
        return _fetch_candidate(
            settings, record, work, work.candidates[0], ocr_key=ocr_key, checked=False
        )
    if key.startswith(DOI):
        work = resolve(key[len(DOI) :])
        fetched = fetch_source(settings, record, work, ocr_key=ocr_key)
        if fetched.read_status is ReadStatus.TRANSIENT:
            return fetched
        # what the DOI names, read or not: a DOI written from memory shows
        # here, before a session names it as a work
        return replace(fetched, detail=f"{resolution(work)}; {fetched.detail}")
    return Fetch(key, detail=f"a found work is `{ARXIV}ID` or `{DOI}DOI`, not {key!r}")


def resolution(work: ResolvedSource) -> str:
    """What a DOI resolved to, as one line: its authors, year, title and
    venue; or that no resolver resolved it, which may be a DOI that names
    nothing or resolvers that did not answer (its detail says which)."""
    if work.status is not ResolutionStatus.EXACT:
        why = f" ({work.detail})" if work.detail else ""
        return f"no resolver resolved the DOI{why}"
    authors = ", ".join(work.creators[:3]) + (
        " et al." if len(work.creators) > 3 else ""
    )
    parts = [f"{authors or 'no authors recorded'} ({work.issued[:4] or 'no year'})"]
    parts += [x for x in (work.title, work.venue) if x]
    return "the DOI resolves to " + ". ".join(parts)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def fetch_source(
    settings: ResolvedSources,
    record: BibRecord,
    work: ResolvedSource,
    *,
    url: str = "",
    ocr_key: str = "",
) -> Fetch:
    """Fetch the first valid PDF. Every failure is a classified result."""
    if settings.cache_dir is None:
        return Fetch(record.key, detail="the paper has no source cache directory")
    candidates = (
        (Candidate(url, "web-search"),)
        if url
        else tuple(item for item in work.candidates if item.kind == "pdf")
    )
    if not candidates:
        return Fetch(record.key, detail="no resolver found an open PDF candidate")
    failures = []
    for candidate in candidates:
        result = _fetch_candidate(settings, record, work, candidate, ocr_key=ocr_key)
        if result.read_status is ReadStatus.TRANSIENT:
            return result
        failures.append(f"{candidate.resolver}: {result.detail}")
    return Fetch(record.key, detail="; ".join(failures))


def _fetch_candidate(
    settings: ResolvedSources,
    record: BibRecord,
    work: ResolvedSource,
    candidate: Candidate,
    *,
    ocr_key: str = "",
    checked: bool = True,
) -> Fetch:
    """`checked`: the PDF must be the work (`_mismatch`: its title and
    authors on a title page, or its DOI); an arXiv copy fetched by its
    identifier is that identifier's. In the plain tier (no library) the
    PDF is kept alone, its pages read from its text layer when asked, and
    one the match cannot decide on from that layer is kept unchecked, as a
    person's own PDF is (`providers.UNCHECKED`)."""
    try:
        body, final_url = download(candidate.url)
    except ValueError as error:
        return Fetch(record.key, candidate.url, candidate.resolver, detail=str(error))
    if settings.cache_dir is None:
        return Fetch(record.key, detail="the paper has no source cache directory")
    target = pdf_path(settings.cache_dir, record.key)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{target.name}.", dir=target.parent
    )
    os.close(descriptor)
    scratch = Path(temporary)
    try:
        scratch.write_bytes(body)
        plain = not library_on(settings)
        extract = _layer_pages if plain else _extract_pages
        pages = extract(
            scratch,
            settings=settings,
            bib_key=record.key,
            title=work.title or record.title,
            ocr_key=ocr_key,
        )
        unchecked = plain and not any(
            readable(str(page["text"]))
            for page in pages
            if int(str(page["page_index"])) <= FIRST_PAGES
        )
        why, undecided = "", unchecked
        if checked and not unchecked:
            scans = _scans(scratch) if plain else frozenset()
            why, undecided = _mismatch(work, record, pages, scans)
        if why:
            return Fetch(
                record.key,
                final_url,
                candidate.resolver,
                detail=f"the fetched PDF is not the entry's work: {why}",
            )
        os.replace(scratch, target)
        for page in pages:
            page["file_path"] = str(target)
        if not plain:
            store_pages(settings.cache_dir, record.key, pages)
        # a copy kept though the match could not decide on it says so on
        # every read: a found work has no entry to check it against again
        mark = unchecked_path(settings.cache_dir, record.key)
        if checked and undecided:
            mark.write_text("the match could not decide on its text layer\n")
        else:
            mark.unlink(missing_ok=True)
    except Exception as error:  # noqa: BLE001 - an upstream document is untrusted
        return Fetch(
            record.key,
            final_url,
            candidate.resolver,
            detail=f"the fetched document is not a readable PDF: {error}",
        )
    finally:
        scratch.unlink(missing_ok=True)
    return Fetch(
        record.key,
        final_url,
        candidate.resolver,
        str(target),
        len(pages),
        ReadStatus.TRANSIENT,
        "open copy, not the version of record: it may differ from it",
    )


def _extract_pages(
    pdf: Path,
    *,
    settings: ResolvedSources,
    bib_key: str,
    title: str,
    ocr_key: str = "",
) -> list[dict[str, object]]:
    """Extract pages with the same OCR policy as a corpus ingest."""
    from axiomation.sources.ingest.extract import extract_pdf
    from axiomation.sources.ingest.ocr import (
        TesseractProvider,
        provider_for,
        tesseract_provider,
    )

    ocr = provider_for(settings, api_key=ocr_key)
    tesseract = ocr if isinstance(ocr, TesseractProvider) else tesseract_provider()
    return [
        {
            "bib_key": bib_key,
            "title": title,
            "page_index": page.page_index,
            "file_path": str(pdf),
            "text": page.text,
            "text_source": page.source.value,
            "disputed": page.disputed or 0,
        }
        for page in extract_pdf(pdf, ocr=ocr, tesseract=tesseract)
    ]


def _layer_pages(
    pdf: Path,
    *,
    settings: ResolvedSources,
    bib_key: str,
    title: str,
    ocr_key: str = "",
) -> list[dict[str, object]]:
    """Every page's text layer, with no OCR: the plain tier's."""
    from axiomation.sources.pdf import open_pdf

    with open_pdf(pdf) as document:
        return [
            {
                "bib_key": bib_key,
                "title": title,
                "page_index": i,
                "file_path": str(pdf),
                "text": document.text(i),
                "text_source": "none",
                "disputed": 0,
            }
            for i in range(1, document.page_count + 1)
        ]


def download(url: str) -> tuple[bytes, str]:
    """Download one public PDF and revalidate every redirect. Each hop
    connects to an address its check found public, never to one the
    hostname resolves to after: a short-lived DNS answer cannot pass the
    check and then point the connection inside the person's network."""
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        addresses = public_addresses(current)
        try:
            status, location, body = _get(current, addresses)
        except (OSError, http.client.HTTPException) as error:
            raise ValueError(f"GET {current} failed: {error}") from error
        if status in REDIRECTS:
            if not location:
                raise ValueError(f"redirect from {current} has no Location")
            current = urljoin(current, location)
            continue
        if not 200 <= status < 300:
            raise ValueError(f"GET {current} returned HTTP {status}")
        if len(body) > MAX_BYTES:
            raise ValueError(f"candidate is larger than {MAX_BYTES} bytes")
        if not body.startswith(b"%PDF-"):
            raise ValueError("candidate did not return a PDF")
        return body, current
    raise ValueError(f"candidate exceeded {MAX_REDIRECTS} redirects")


REDIRECTS = frozenset({301, 302, 303, 307, 308})
HEADERS = {"User-Agent": "axiomation source fetcher", "Accept": "application/pdf"}
TIMEOUT_SECONDS = 30


def _get(url: str, addresses: tuple[str, ...]) -> tuple[int, str, bytes]:
    """One GET: its status, its `Location`, and its body where it is a
    success. Connected to the first of `addresses` that answers, the
    hostname kept for `Host`, SNI and the certificate; through the proxy
    the person configured for the scheme, where one is, as any request of
    theirs goes."""
    parsed = urlsplit(url)
    host = parsed.hostname or ""
    if parsed.scheme in urllib.request.getproxies() and not (
        urllib.request.proxy_bypass(host)
    ):
        return _get_through_proxy(url)
    # the port given, so an IPv6 literal's last group is not read as one
    if parsed.scheme == "https":
        connection: http.client.HTTPConnection = http.client.HTTPSConnection(
            host,
            parsed.port or 443,
            timeout=TIMEOUT_SECONDS,
            context=ssl.create_default_context(),
        )
    else:
        connection = http.client.HTTPConnection(
            host, parsed.port or 80, timeout=TIMEOUT_SECONDS
        )

    def pinned(
        where: tuple[str, int],
        timeout: float | None,
        source: tuple[str, int] | None = None,
    ) -> socket.socket:
        return _connect(addresses, where[1], timeout, source)

    # `http.client` connects through this attribute; the hostname stays
    # the connection's own, for `Host`, SNI and the certificate
    connection._create_connection = pinned  # type: ignore[attr-defined]
    target = urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
    try:
        connection.request("GET", target, headers=HEADERS)
        response = connection.getresponse()
        location = response.getheader("Location", "") or ""
        if not 200 <= response.status < 300:
            return response.status, location, b""
        return response.status, location, _body(response)
    finally:
        connection.close()


def _connect(
    addresses: tuple[str, ...],
    port: int,
    timeout: float | None,
    source: tuple[str, int] | None,
) -> socket.socket:
    """A connection to the first of the checked `addresses` that answers
    on `port`, as `socket.create_connection` tries a hostname's."""
    failure: OSError | None = None
    for address in addresses:
        try:
            return socket.create_connection((address, port), timeout, source)
        except OSError as error:
            failure = error
    raise failure or OSError("no address to connect to")


def _get_through_proxy(url: str) -> tuple[int, str, bytes]:
    """One GET through the configured proxy, which resolves the host
    itself; redirects are returned, not followed."""
    opener = urllib.request.build_opener(_NoRedirect())
    request = urllib.request.Request(url, headers=HEADERS)
    try:
        response = opener.open(request, timeout=TIMEOUT_SECONDS)
    except urllib.error.HTTPError as error:
        return error.code, error.headers.get("Location", "") or "", b""
    with response:
        return response.status, "", _body(response)


def _body(response: Any) -> bytes:
    """A success's body, refused past `MAX_BYTES`."""
    length = response.headers.get("Content-Length")
    if length and int(length) > MAX_BYTES:
        raise ValueError(f"candidate is larger than {MAX_BYTES} bytes")
    return response.read(MAX_BYTES + 1)


def public_addresses(url: str) -> tuple[str, ...]:
    """The addresses a resolver-controlled URL's host resolves to, each
    checked public; a URL that could reach local infrastructure is
    refused."""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("candidate URL must use HTTP or HTTPS")
    if parsed.username or parsed.password or not parsed.hostname:
        raise ValueError("candidate URL has credentials or no host")
    if "." not in parsed.hostname and not _ip_literal(parsed.hostname):
        raise ValueError("candidate URL has a non-public hostname")
    try:
        found = socket.getaddrinfo(parsed.hostname, None)
    except OSError as error:
        raise ValueError(f"candidate host did not resolve: {error}") from error
    addresses: dict[str, None] = {}
    for address in found:
        ip = ipaddress.ip_address(address[4][0].split("%")[0])
        if not ip.is_global or ip.is_multicast:
            raise ValueError(f"candidate host resolves to non-public address {ip}")
        addresses[str(ip)] = None
    return tuple(addresses)


def _ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host.strip("[]"))
        return True
    except ValueError:
        return False


def _scans(pdf: Path) -> frozenset[int]:
    """The indices of a PDF's scanned pages (`pdf.Pdf.page_kinds`)."""
    from axiomation.sources.pdf import PageKind, open_pdf

    with open_pdf(pdf) as document:
        kinds = document.page_kinds()
    return frozenset(i for i, kind in enumerate(kinds, 1) if kind is PageKind.SCAN)


def _mismatch(
    work: ResolvedSource,
    record: BibRecord,
    pages: list[dict[str, object]],
    scans: frozenset[int] = frozenset(),
) -> tuple[str, bool]:
    """Why a fetched PDF is not the work (`sources.match`): the resolved
    title, creators, year and DOI, else the entry's, the title held to a
    stranger's bar; empty when it is. And whether the check could not
    decide: it failed where a page `scans` names stands among those it
    reads (a scan's own layer, read with no OCR: `match.scanned_window`),
    so the copy is kept, unchecked."""
    kind = record.kind or ("book" if "book" in work.item_type else "")
    want = expected_found(
        replace(record, kind=kind), work.title, work.creators, work.issued, work.doi
    )
    first = {
        int(str(page["page_index"])): str(page.get("text", ""))
        for page in pages
        if int(str(page["page_index"])) <= FIRST_PAGES
    }
    if not want.title and not want.surnames:
        return "the resolver gave no title and no authors to check it against", False
    found = check(want, first, FOUND_TITLE_RUN)
    if found.matches:
        return "", False
    if scanned_window(want, first, scans):
        return "", True
    return found.why, False
