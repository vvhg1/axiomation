"""Crossref, OpenAlex, Unpaywall, Europe PMC and CORE."""

from __future__ import annotations

import sqlite3
from typing import Any
from urllib.parse import quote

from axiomation.sources.resolvers.base import fetch_json


def normalize_doi(value: str) -> str:
    doi = value.strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if doi.startswith(prefix):
            doi = doi[len(prefix) :]
            break
    return doi.rstrip(".,; ")


def crossref(
    connection: sqlite3.Connection, doi: str, *, email: str = "", fresh: bool = False
) -> dict[str, Any]:
    url = f"https://api.crossref.org/works/{quote(doi, safe='')}"
    if email:
        url += f"?mailto={quote(email, safe='')}"
    data = (
        fetch_json(
            connection,
            url,
            fresh=fresh,
            cache_key=f"https://api.crossref.org/works/{quote(doi, safe='')}",
        )
        or {}
    )
    message = data.get("message")
    return message if isinstance(message, dict) else {}


def openalex(
    connection: sqlite3.Connection, doi: str, *, email: str = "", fresh: bool = False
) -> dict[str, Any]:
    url = "https://api.openalex.org/works/https://doi.org/" + quote(doi, safe="")
    if email:
        url += f"?mailto={quote(email, safe='')}"
    return (
        fetch_json(
            connection,
            url,
            fresh=fresh,
            cache_key="https://api.openalex.org/works/https://doi.org/"
            + quote(doi, safe=""),
        )
        or {}
    )


def unpaywall(
    connection: sqlite3.Connection, doi: str, email: str, *, fresh: bool = False
) -> dict[str, Any]:
    if not email:
        return {}
    url = (
        f"https://api.unpaywall.org/v2/{quote(doi, safe='')}"
        f"?email={quote(email, safe='')}"
    )
    return (
        fetch_json(
            connection,
            url,
            fresh=fresh,
            cache_key=f"https://api.unpaywall.org/v2/{quote(doi, safe='')}",
        )
        or {}
    )


def europe_pmc(
    connection: sqlite3.Connection, doi: str, *, fresh: bool = False
) -> dict[str, Any]:
    query = quote(f'DOI:"{doi}"', safe="")
    url = (
        "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
        f"?query={query}&format=json&resultType=core&pageSize=1"
    )
    return fetch_json(connection, url, fresh=fresh) or {}


def core(
    connection: sqlite3.Connection,
    doi: str,
    api_key: str,
    *,
    fresh: bool = False,
) -> dict[str, Any]:
    if not api_key:
        return {}
    query = quote(f'doi:"{doi}"', safe="")
    url = f"https://api.core.ac.uk/v3/search/works/?q={query}&limit=1"
    return (
        fetch_json(
            connection,
            url,
            fresh=fresh,
            headers={
                "Authorization": f"Bearer {api_key}",
                "User-Agent": "axiomation source resolver",
            },
        )
        or {}
    )
