"""Where a preprint cited was published: its versions of record.

From the preprint's own record first: Crossref's `is-preprint-of`
relation, and the arXiv API's DOI and journal reference, the authors'
own word. Then by title and first author: OpenAlex's title search, and
OpenReview's (its older API, then its newer), which hold the conference
and journal versions that have no DOI. A title hit is kept only where
its title is the entry's and so is its first author's family name; one
whose title matches and whose first author does not is passed, named,
never taken. Every answer is cached as the resolvers' are, and a source
that did not answer is named, so a miss is never read as "none".
"""

from __future__ import annotations

import re
import unicodedata
import xml.etree.ElementTree as ElementTree
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlencode

from axiomation.records import Match, VersionOfRecord
from axiomation.sources.resolvers import providers
from axiomation.sources.resolvers.base import fetch_json, fetch_text

ARXIV_API = "https://export.arxiv.org/api/query?id_list="
ARXIV_NAMESPACE = "{http://arxiv.org/schemas/atom}"
ATOM_NAMESPACE = "{http://www.w3.org/2005/Atom}"
#: OpenReview's older API, then its newer: they hold different venues.
OPENREVIEW_APIS = ("https://api.openreview.net", "https://api2.openreview.net")
OPENREVIEW_FORUM = "https://openreview.net/forum?id="
#: A venue OpenReview records that is no version of record: a submission
#: not accepted or not yet decided, a retracted acceptance, a workshop, or
#: arXiv itself as DBLP lists it. A false match is the dangerous direction:
#: it tells the author to cite a version that does not exist.
NOT_OF_RECORD = (
    "rejected",
    "withdrawn",
    "submitted",
    "submission",
    "under review",
    "under_review",
    "pending",
    "decision",
    "retracted",
    "workshop",
    "/corr/",
    "public_article",
)
#: A journal reference on arXiv that names no publication: the authors'
#: words for where it was sent, not where it appeared.
NOT_A_REFERENCE = (
    "submitted",
    "under review",
    "in review",
    "under submission",
    "in preparation",
    "workshop",
    "preprint",
)
#: OpenAlex's source types a version of record is published in.
OF_RECORD = ("journal", "conference")
#: One venue under two names, as OpenReview's records and DBLP's spell it.
_VENUE_NAMES = {"nips": "neurips"}


@dataclass(frozen=True, slots=True)
class Lookup:
    """What one lookup found: the versions of record, the title hits
    passed (their first author is not the entry's), and the sources that
    did not answer."""

    versions: tuple[VersionOfRecord, ...] = ()
    passed: tuple[str, ...] = ()
    failed: tuple[str, ...] = ()


def words(text: str) -> str:
    """A title or a name compared: TeX's accents and braces, case, accents
    and punctuation aside."""
    text = re.sub(r"\\[a-zA-Z]+\s*|\\.", "", text).replace("{", "").replace("}", "")
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.lower()).split())


def family_name(authors: str) -> str:
    """The first author's family name, from a bibliography's `author`
    field (`Squeakinger, Ottoline and …` or `Ottoline Squeakinger and …`)."""
    first = re.split(r"\s+and\s+", authors.strip(), maxsplit=1)[0].strip()
    family = first.split(",")[0] if "," in first else (first.split() or [""])[-1]
    return words(family)


def same_author(family: str, name: str) -> bool:
    """Whether a record's first author `name` carries the family name."""
    return bool(family) and f" {family} " in f" {words(name)} "


def look_up(
    connection: Any,
    *,
    doi: str,
    arxiv: str,
    title: str,
    authors: str,
    email: str = "",
    fresh: bool = False,
    resolve: Callable[[str], tuple[str, str] | None],
) -> Lookup:
    """A preprint's versions of record, from its DOI's Crossref record, its
    arXiv identifier, its title and its first author. `resolve` gives a
    DOI's venue and year, None where the DOI is published as no refereed
    work or book (a deposit, another preprint): it is passed. The record's
    own word first; a title search only where it names none."""
    found: list[VersionOfRecord] = []
    failed: list[str] = []
    passed: list[str] = []
    record = (
        providers.crossref(connection, doi, email=email, fresh=fresh) if doi else {}
    )
    for item in (record.get("relation") or {}).get("is-preprint-of") or []:
        if isinstance(item, dict) and item.get("id-type") == "doi" and item.get("id"):
            target = providers.normalize_doi(str(item["id"]))
            published = resolve(target)
            if published is None:
                passed.append(f"doi:{target} (crossref): not published as refereed")
                continue
            venue, year = published
            found.append(
                VersionOfRecord(venue, year, target, None, "crossref", Match.IDENTIFIER)
            )
    if arxiv:
        said = _arxiv(connection, arxiv, fresh)
        if said is None:
            failed.append("the arXiv API did not answer")
        else:
            target, reference = said
            published = resolve(target) if target else None
            if target and published is None:
                passed.append(f"doi:{target} (arxiv): not published as refereed")
            if reference and any(w in reference.lower() for w in NOT_A_REFERENCE):
                passed.append(f"{reference} (arxiv): names no publication")
                reference = ""
            if published is not None:
                venue, year = published
                found.append(
                    VersionOfRecord(
                        venue or reference,
                        year or _year(reference),
                        target,
                        None,
                        "arxiv",
                        Match.IDENTIFIER,
                    )
                )
            elif reference:
                found.append(
                    VersionOfRecord(
                        reference,
                        _year(reference),
                        None,
                        None,
                        "arxiv",
                        Match.REFERENCE,
                    )
                )
    if found:
        return Lookup(tuple(_once(found)), tuple(passed), tuple(failed))
    family = family_name(authors)
    hits = _openalex(connection, title, email, fresh)
    if hits is None:
        failed.append(
            "OpenAlex's title search did not answer"
            + ("" if email else " (asked without a contact address)")
        )
    for hit in hits or []:
        _take(hit, title, family, doi, found, passed)
    for api in OPENREVIEW_APIS:
        notes = _openreview(connection, api, title, fresh)
        if notes is None:
            failed.append(f"OpenReview ({api}) did not answer")
            continue
        before = len(found)
        for note in notes:
            _take(note, title, family, doi, found, passed)
        if len(found) > before:
            break
    return Lookup(tuple(_once(found)), tuple(dict.fromkeys(passed)), tuple(failed))


@dataclass(frozen=True, slots=True)
class _Hit:
    """A title search's hit, in one shape for both sources."""

    title: str
    first: str
    version: VersionOfRecord


def _take(
    hit: _Hit,
    title: str,
    family: str,
    doi: str,
    found: list[VersionOfRecord],
    passed: list[str],
) -> None:
    if words(hit.title) != words(title):
        return
    if hit.version.doi and hit.version.doi == providers.normalize_doi(doi):
        return
    if not same_author(family, hit.first):
        passed.append(
            f"{hit.version.venue} ({hit.version.source}): its first author is "
            f"{hit.first or 'not given'}"
        )
        return
    found.append(hit.version)


def _arxiv(connection: Any, arxiv: str, fresh: bool) -> tuple[str, str] | None:
    """The DOI and the journal reference arXiv's record of the preprint
    gives, each empty where it gives none; None where arXiv did not
    answer."""
    text = fetch_text(connection, ARXIV_API + quote(arxiv, safe="/"), fresh=fresh)
    if text is None:
        return None
    try:
        feed = ElementTree.fromstring(text)
    except ElementTree.ParseError:
        return None
    entry = feed.find(f"{ATOM_NAMESPACE}entry")
    if entry is None:
        return "", ""

    def said(name: str) -> str:
        node = entry.find(f"{ARXIV_NAMESPACE}{name}")
        return " ".join((node.text or "").split()) if node is not None else ""

    return providers.normalize_doi(said("doi").split()[0]) if said("doi") else "", (
        said("journal_ref")
    )


def _openalex(
    connection: Any, title: str, email: str, fresh: bool
) -> list[_Hit] | None:
    # the filter's syntax takes neither commas nor colons in its value
    query = " ".join(re.sub(r"[,:|]", " ", title).split())
    url = "https://api.openalex.org/works?per_page=10&filter=title.search:" + quote(
        query, safe=""
    )
    if email:
        url += f"&mailto={quote(email, safe='')}"
    data = fetch_json(connection, url, fresh=fresh)
    if data is None:
        return None
    hits = []
    for work in data.get("results") or []:
        if not isinstance(work, dict) or work.get("type") == "preprint":
            continue
        location = work.get("primary_location") or {}
        source = location.get("source") or {}
        if source.get("type") not in OF_RECORD:
            continue
        authorships = work.get("authorships") or [{}]
        first = ((authorships[0] or {}).get("author") or {}).get("display_name") or ""
        year = work.get("publication_year")
        hits.append(
            _Hit(
                str(work.get("title") or ""),
                str(first),
                VersionOfRecord(
                    str(source.get("display_name") or ""),
                    str(year) if isinstance(year, int) else "",
                    providers.normalize_doi(str(work.get("doi") or "")) or None,
                    None,
                    "openalex",
                    Match.TITLE_AND_AUTHORS,
                ),
            )
        )
    return hits


def _openreview(
    connection: Any, api: str, title: str, fresh: bool
) -> list[_Hit] | None:
    url = f"{api}/notes/search?" + urlencode({"term": title, "limit": 10})
    data = fetch_json(connection, url, fresh=fresh)
    if data is None:
        return None
    hits = []
    for note in data.get("notes") or []:
        if not isinstance(note, dict):
            continue
        content = note.get("content") or {}
        venue = _field(content, "venue")
        venueid = _field(content, "venueid")
        if not venueid or not isinstance(venue, str):
            continue
        if any(word in f"{venueid} {venue}".lower() for word in NOT_OF_RECORD):
            continue
        authors = _field(content, "authors") or [""]
        first = authors[0] if isinstance(authors, list) and authors else ""
        if isinstance(first, dict):
            first = first.get("fullname") or ""
        hits.append(
            _Hit(
                str(_field(content, "title") or ""),
                str(first),
                VersionOfRecord(
                    venue,
                    _year(venue),
                    None,
                    OPENREVIEW_FORUM + str(note.get("id") or ""),
                    "openreview",
                    Match.TITLE_AND_AUTHORS,
                ),
            )
        )
    return hits


def _field(content: dict[str, Any], name: str) -> Any:
    """A note's field: the older API's value, or the newer's `{"value": …}`."""
    value = content.get(name)
    return value.get("value") if isinstance(value, dict) else value


def _year(text: str) -> str:
    found = re.findall(r"\b(?:19|20)\d\d\b", text)
    return found[-1] if found else ""


def _venue_key(version: VersionOfRecord) -> tuple[str, str]:
    """A venue and year, so that one venue under its own name and DBLP's
    (`ICLR 2022 Poster`, `ICLR 2022`) is kept once."""
    if version.doi:
        return (version.doi, "")
    name = words(version.venue).split()
    first = name[0] if name else ""
    return (_VENUE_NAMES.get(first, first), version.year)


def _once(found: Iterable[VersionOfRecord]) -> list[VersionOfRecord]:
    kept: dict[tuple[str, str], VersionOfRecord] = {}
    for version in found:
        kept.setdefault(_venue_key(version), version)
    return list(kept.values())
