"""One cached resolver chain over the five providers."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any

from axiomation.sources.resolvers import providers
from axiomation.sources.resolvers.base import TTL_DAYS, open_cache


@dataclass(frozen=True, slots=True)
class Candidate:
    """One possible open copy. Fetching still has to validate it."""

    url: str
    resolver: str
    kind: str = "pdf"
    version: str = ""
    host_type: str = ""

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Candidate:
        return cls(**{name: str(value.get(name, "")) for name in cls.__slots__})


class ResolutionStatus(StrEnum):
    """Whether an identifier resolved to one publication."""

    EXACT = "exact"
    UNRESOLVED = "unresolved"


@dataclass(frozen=True, slots=True)
class ResolvedSource:
    """Manager-neutral DOI metadata and ordered open-copy candidates."""

    doi: str
    status: ResolutionStatus = ResolutionStatus.UNRESOLVED
    item_type: str = ""
    title: str = ""
    creators: tuple[str, ...] = ()
    issued: str = ""
    venue: str = ""
    publisher: str = ""
    volume: str = ""
    issue: str = ""
    pages: str = ""
    canonical_url: str = ""
    resolvers: tuple[str, ...] = ()
    resolution_method: str = "exact-identifier"
    retrieved_at: str = ""
    fingerprint: str = ""
    citation_count: int | None = None
    retracted: bool | None = None
    version: str = ""
    candidates: tuple[Candidate, ...] = ()
    detail: str = ""
    #: OpenAlex's type of the source it is published in: `journal`,
    #: `conference`, `repository`, `ebook platform`, …
    source_type: str = ""
    #: whether Crossref records a correction or an erratum of it
    corrected: bool | None = None
    #: the DOIs of the corrections Crossref records, where it names them
    corrections: tuple[str, ...] = ()
    #: whether it was asked with a contact address: one asked without is
    #: asked again once an address is set, Unpaywall with it
    asked_with_contact: bool = False

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["candidates"] = [asdict(item) for item in self.candidates]
        return value

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ResolvedSource:
        return cls(
            doi=str(value.get("doi", "")),
            status=ResolutionStatus(value.get("status", ResolutionStatus.UNRESOLVED)),
            item_type=str(value.get("item_type", "")),
            title=str(value.get("title", "")),
            creators=tuple(str(item) for item in value.get("creators", [])),
            issued=str(value.get("issued", "")),
            venue=str(value.get("venue", "")),
            publisher=str(value.get("publisher", "")),
            volume=str(value.get("volume", "")),
            issue=str(value.get("issue", "")),
            pages=str(value.get("pages", "")),
            canonical_url=str(value.get("canonical_url", "")),
            resolvers=tuple(str(item) for item in value.get("resolvers", [])),
            resolution_method=str(value.get("resolution_method", "exact-identifier")),
            retrieved_at=str(value.get("retrieved_at", "")),
            fingerprint=str(value.get("fingerprint", "")),
            citation_count=value.get("citation_count"),
            retracted=value.get("retracted"),
            version=str(value.get("version", "")),
            candidates=tuple(
                Candidate.from_dict(item) for item in value.get("candidates", [])
            ),
            detail=str(value.get("detail", "")),
            source_type=str(value.get("source_type", "")),
            corrected=value.get("corrected"),
            corrections=tuple(str(item) for item in value.get("corrections", [])),
            asked_with_contact=bool(value.get("asked_with_contact", False)),
        )


class Resolver:
    """Resolve works through one paper-local cache."""

    def __init__(
        self,
        cache_path: Path,
        *,
        email: str = "",
        core_key: str = "",
    ) -> None:
        self.connection = open_cache(cache_path)
        self.email = email
        self.core_key = core_key

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> Resolver:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def resolve(
        self, raw_doi: str, *, bib_key: str = "", fresh: bool = False
    ) -> ResolvedSource:
        doi = providers.normalize_doi(raw_doi)
        if not doi:
            return ResolvedSource("", detail="the bibliography entry has no DOI")
        cached = self._cached(doi) if not fresh else None
        if cached is not None:
            self._bind(doi, bib_key)
            return cached
        work = self._resolve(doi, fresh=fresh)
        self.connection.execute(
            "INSERT OR REPLACE INTO sources VALUES (?, ?, ?, ?)",
            (
                doi,
                bib_key,
                json.dumps(work.as_dict(), sort_keys=True),
                datetime.now(UTC).isoformat(),
            ),
        )
        self.connection.commit()
        self._bind(doi, bib_key)
        return work

    def _bind(self, doi: str, bib_key: str) -> None:
        if not bib_key:
            return
        self.connection.execute(
            "UPDATE sources SET bib_key = ? WHERE doi = ?", (bib_key, doi)
        )
        self.connection.execute(
            "INSERT OR REPLACE INTO source_bindings VALUES (?, ?)", (bib_key, doi)
        )
        self.connection.commit()

    def _cached(self, doi: str) -> ResolvedSource | None:
        row = self.connection.execute(
            "SELECT body, fetched_at FROM sources WHERE doi = ?", (doi,)
        ).fetchone()
        if row is None:
            return None
        try:
            age = datetime.now(UTC) - datetime.fromisoformat(row["fetched_at"])
            if age >= timedelta(days=TTL_DAYS):
                return None
            value = json.loads(row["body"])
        except (ValueError, TypeError, json.JSONDecodeError):
            return None
        if not isinstance(value, dict) or not set(ResolvedSource.__slots__) <= set(
            value
        ):
            # resolved before a field was added: made again from the
            # providers' cached answers
            return None
        work = ResolvedSource.from_dict(value)
        return None if self.email and not work.asked_with_contact else work

    def _resolve(self, doi: str, *, fresh: bool) -> ResolvedSource:
        crossref = providers.crossref(
            self.connection, doi, email=self.email, fresh=fresh
        )
        openalex = providers.openalex(
            self.connection, doi, email=self.email, fresh=fresh
        )
        candidates: list[Candidate] = []
        self._from_unpaywall(candidates, doi, fresh)
        self._from_europe_pmc(candidates, doi, fresh)
        self._from_core(candidates, doi, fresh)
        self._metadata_candidates(candidates, crossref, openalex)
        unique = tuple({item.url: item for item in candidates}.values())
        title = _first(crossref.get("title")) or str(openalex.get("title") or "")
        venue = _first(crossref.get("container-title")) or _venue(openalex)
        creators = _creators(crossref, openalex)
        issued = _issued(crossref, openalex)
        item_type = _item_type(crossref, openalex)
        publisher = str(crossref.get("publisher") or "")
        volume = str(crossref.get("volume") or "")
        issue = str(crossref.get("issue") or "")
        pages = str(crossref.get("page") or "")
        count = openalex.get("cited_by_count")
        if not isinstance(count, int):
            count = crossref.get("is-referenced-by-count")
        if not isinstance(count, int):
            count = None
        said = [
            value
            for value in (
                openalex.get("is_retracted"),
                _crossref_updated(crossref, RETRACTIONS),
            )
            if isinstance(value, bool)
        ]
        retracted = any(said) if said else None
        version = unique[0].version if unique else ""
        unasked = "" if self.email else " (asked without a contact address)"
        failures = []
        if not crossref:
            failures.append(f"Crossref did not answer{unasked}")
        if not openalex:
            failures.append(f"OpenAlex did not answer{unasked}")
        if not self.email:
            failures.append("Unpaywall skipped: no contact address")
        resolvers = tuple(
            name
            for name, value in (("crossref", crossref), ("openalex", openalex))
            if value
        )
        status = ResolutionStatus.EXACT if resolvers else ResolutionStatus.UNRESOLVED
        normalized = {
            "doi": doi,
            "item_type": item_type,
            "title": title,
            "creators": creators,
            "issued": issued,
            "venue": venue,
            "publisher": publisher,
            "volume": volume,
            "issue": issue,
            "pages": pages,
            "canonical_url": f"https://doi.org/{doi}",
        }
        fingerprint = hashlib.sha256(
            json.dumps(normalized, sort_keys=True).encode()
        ).hexdigest()[:16]
        return ResolvedSource(
            doi=doi,
            status=status,
            item_type=item_type,
            title=title,
            creators=creators,
            issued=issued,
            venue=venue,
            publisher=publisher,
            volume=volume,
            issue=issue,
            pages=pages,
            canonical_url=f"https://doi.org/{doi}",
            resolvers=resolvers,
            retrieved_at=datetime.now(UTC).isoformat(),
            fingerprint=fingerprint,
            citation_count=count,
            retracted=retracted,
            version=version,
            candidates=unique,
            detail="; ".join(failures),
            source_type=_source_type(openalex),
            corrected=_crossref_updated(crossref, CORRECTIONS),
            corrections=_crossref_updates(crossref, CORRECTIONS),
            asked_with_contact=bool(self.email),
        )

    def _from_unpaywall(
        self, candidates: list[Candidate], doi: str, fresh: bool
    ) -> None:
        data = providers.unpaywall(self.connection, doi, self.email, fresh=fresh)
        locations = []
        if isinstance(data.get("best_oa_location"), dict):
            locations.append(data["best_oa_location"])
        locations.extend(
            item for item in data.get("oa_locations", []) if isinstance(item, dict)
        )
        for want_pdf in (True, False):
            field = "url_for_pdf" if want_pdf else "url"
            for item in locations:
                url = item.get(field)
                if isinstance(url, str) and url:
                    candidates.append(
                        Candidate(
                            url,
                            "unpaywall",
                            "pdf" if want_pdf else "page",
                            str(item.get("version") or ""),
                            str(item.get("host_type") or ""),
                        )
                    )

    def _from_europe_pmc(
        self, candidates: list[Candidate], doi: str, fresh: bool
    ) -> None:
        data = providers.europe_pmc(self.connection, doi, fresh=fresh)
        results = (data.get("resultList") or {}).get("result") or []
        if not results or not isinstance(results[0], dict):
            return
        record = results[0]
        pmcid = record.get("pmcid")
        if record.get("isOpenAccess") == "Y" and isinstance(pmcid, str) and pmcid:
            candidates.append(
                Candidate(
                    "https://www.ebi.ac.uk/europepmc/webservices/rest/"
                    f"{pmcid}/fullTextXML",
                    "europepmc",
                    "jats",
                    "publishedVersion",
                    "repository",
                )
            )

    def _from_core(self, candidates: list[Candidate], doi: str, fresh: bool) -> None:
        data = providers.core(self.connection, doi, self.core_key, fresh=fresh)
        results = data.get("results") or []
        if not results or not isinstance(results[0], dict):
            return
        record = results[0]
        for field in ("downloadUrl", "sourceFulltextUrls"):
            values = record.get(field) or []
            if isinstance(values, str):
                values = [values]
            for url in values:
                if isinstance(url, str) and url:
                    candidates.append(
                        Candidate(url, "core", "pdf", host_type="repository")
                    )

    @staticmethod
    def _metadata_candidates(
        candidates: list[Candidate], crossref: dict[str, Any], openalex: dict[str, Any]
    ) -> None:
        for link in crossref.get("link") or []:
            if isinstance(link, dict) and link.get("content-type") == "application/pdf":
                url = link.get("URL")
                if isinstance(url, str) and url:
                    candidates.append(Candidate(url, "crossref"))
        for location in (
            openalex.get("best_oa_location"),
            openalex.get("primary_location"),
        ):
            if not isinstance(location, dict):
                continue
            url = location.get("pdf_url")
            if isinstance(url, str) and url:
                candidates.append(
                    Candidate(
                        url,
                        "openalex",
                        version=str(location.get("version") or ""),
                        host_type=str((location.get("source") or {}).get("type") or ""),
                    )
                )


def _first(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return next(
            (item.strip() for item in value if isinstance(item, str) and item.strip()),
            "",
        )
    return ""


def _venue(openalex: dict[str, Any]) -> str:
    location = openalex.get("primary_location")
    source = location.get("source") if isinstance(location, dict) else None
    return str(source.get("display_name") or "") if isinstance(source, dict) else ""


#: Crossref's update types that retract a work, and that correct it.
RETRACTIONS = frozenset({"retraction", "withdrawal", "removal"})
CORRECTIONS = frozenset({"correction", "erratum", "corrigendum", "addendum"})


def _crossref_updated(record: dict[str, Any], types: frozenset[str]) -> bool | None:
    """Whether Crossref records an update of one of `types`: on the work
    itself (`updated-by`) or as the notice it is (`update-to`); None where
    Crossref did not answer."""
    updates = [*(record.get("updated-by") or []), *(record.get("update-to") or [])]
    if any(
        isinstance(item, dict) and str(item.get("type") or "").lower() in types
        for item in updates
    ):
        return True
    return False if record else None


def _crossref_updates(record: dict[str, Any], types: frozenset[str]) -> tuple[str, ...]:
    """The DOIs of the updates of `types` Crossref records of the work."""
    return tuple(
        dict.fromkeys(
            providers.normalize_doi(str(item["DOI"]))
            for item in record.get("updated-by") or []
            if isinstance(item, dict)
            and str(item.get("type") or "").lower() in types
            and item.get("DOI")
        )
    )


def _source_type(openalex: dict[str, Any]) -> str:
    location = openalex.get("primary_location")
    source = location.get("source") if isinstance(location, dict) else None
    return str(source.get("type") or "") if isinstance(source, dict) else ""


def _creators(crossref: dict[str, Any], openalex: dict[str, Any]) -> tuple[str, ...]:
    names = []
    for author in crossref.get("author") or []:
        if not isinstance(author, dict):
            continue
        name = " ".join(
            part for part in (author.get("given"), author.get("family")) if part
        )
        if name:
            names.append(name)
    if names:
        return tuple(names)
    for authorship in openalex.get("authorships") or []:
        author = authorship.get("author") if isinstance(authorship, dict) else None
        name = author.get("display_name") if isinstance(author, dict) else None
        if isinstance(name, str) and name:
            names.append(name)
    return tuple(names)


def _issued(crossref: dict[str, Any], openalex: dict[str, Any]) -> str:
    for field in ("issued", "published", "published-print", "published-online"):
        value = crossref.get(field)
        parts = value.get("date-parts") if isinstance(value, dict) else None
        if isinstance(parts, list) and parts and isinstance(parts[0], list):
            return "-".join(str(part) for part in parts[0] if part is not None)
    year = openalex.get("publication_year")
    return str(year) if isinstance(year, int) else ""


def _item_type(crossref: dict[str, Any], openalex: dict[str, Any]) -> str:
    raw = str(crossref.get("type") or openalex.get("type") or "")
    return {
        "journal-article": "journalArticle",
        "article": "journalArticle",
        "book": "book",
        "book-chapter": "bookSection",
        "proceedings-article": "conferencePaper",
        "dissertation": "thesis",
        "posted-content": "preprint",
    }.get(raw, raw)
