"""Cached, rate-limited HTTP for source resolvers."""

from __future__ import annotations

import json
import sqlite3
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

TTL_DAYS = 30
HOST_DELAYS = {
    "api.crossref.org": 0.15,
    "api.openalex.org": 0.15,
    "api.unpaywall.org": 0.15,
    "www.ebi.ac.uk": 0.4,
    "api.core.ac.uk": 0.4,
    # arXiv asks three seconds between calls; OpenReview answers 429 at
    # about one a second
    "export.arxiv.org": 3.0,
    "api.openreview.net": 2.0,
    "api2.openreview.net": 2.0,
}
DEFAULT_DELAY = 0.3
#: A 429's wait, where it names none or more than this, and the tries.
RETRY_AFTER_SECONDS = 60
RETRIES = 3
_last_call: dict[str, float] = {}


def open_cache(path: Path) -> sqlite3.Connection:
    """Open the paper-local resolver cache."""
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS responses (
            url TEXT PRIMARY KEY, body TEXT NOT NULL, fetched_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sources (
            doi TEXT PRIMARY KEY, bib_key TEXT NOT NULL DEFAULT '',
            body TEXT NOT NULL, fetched_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS source_bindings (
            bib_key TEXT PRIMARY KEY, doi TEXT NOT NULL
        );
        """
    )
    columns = {row["name"] for row in connection.execute("PRAGMA table_info(sources)")}
    if "bib_key" not in columns:
        connection.execute(
            "ALTER TABLE sources ADD COLUMN bib_key TEXT NOT NULL DEFAULT ''"
        )
    return connection


def _http_get(url: str, headers: dict[str, str]) -> bytes | None:
    """The body, else None; a 429 waited out as its `Retry-After` asks (at
    most a minute), up to `RETRIES` tries."""
    request = urllib.request.Request(url, headers=headers)
    for _ in range(RETRIES):
        try:
            with urllib.request.urlopen(request, timeout=25) as response:
                return response.read()
        except urllib.error.HTTPError as error:
            if error.code != 429:
                return None
            time.sleep(_retry_after(error.headers.get("Retry-After")))
        except (urllib.error.URLError, TimeoutError, OSError):
            return None
    return None


def _retry_after(value: str | None) -> float:
    try:
        return min(float(value or RETRY_AFTER_SECONDS), RETRY_AFTER_SECONDS)
    except ValueError:
        return RETRY_AFTER_SECONDS


def fetch_json(
    connection: sqlite3.Connection,
    url: str,
    *,
    fresh: bool = False,
    headers: dict[str, str] | None = None,
    cache_key: str | None = None,
) -> dict[str, Any] | None:
    """Return one JSON object. Provider and parse failures are soft."""
    now = datetime.now(UTC)
    key = cache_key or url
    if not fresh:
        row = connection.execute(
            "SELECT body, fetched_at FROM responses WHERE url = ?", (key,)
        ).fetchone()
        if row is not None:
            try:
                age = now - datetime.fromisoformat(row["fetched_at"])
                if age < timedelta(days=TTL_DAYS):
                    value = json.loads(row["body"])
                    return value if isinstance(value, dict) else None
            except (ValueError, TypeError, json.JSONDecodeError):
                pass
    _throttle(url)
    body = _http_get(url, headers or {"User-Agent": "axiomation source resolver"})
    if body is None:
        return None
    try:
        value = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict):
        return None
    connection.execute(
        "INSERT OR REPLACE INTO responses VALUES (?, ?, ?)",
        (key, json.dumps(value, sort_keys=True), now.isoformat()),
    )
    connection.commit()
    return value


def fetch_text(
    connection: sqlite3.Connection,
    url: str,
    *,
    fresh: bool = False,
    headers: dict[str, str] | None = None,
) -> str | None:
    """One text body (an Atom feed), cached as `fetch_json`'s are; None
    where the provider failed."""
    key = f"text:{url}"
    now = datetime.now(UTC)
    if not fresh:
        row = connection.execute(
            "SELECT body, fetched_at FROM responses WHERE url = ?", (key,)
        ).fetchone()
        if row is not None:
            try:
                age = now - datetime.fromisoformat(row["fetched_at"])
                if age < timedelta(days=TTL_DAYS):
                    return str(row["body"])
            except (ValueError, TypeError):
                pass
    _throttle(url)
    body = _http_get(url, headers or {"User-Agent": "axiomation source resolver"})
    if body is None:
        return None
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        return None
    connection.execute(
        "INSERT OR REPLACE INTO responses VALUES (?, ?, ?)",
        (key, text, now.isoformat()),
    )
    connection.commit()
    return text


def _throttle(url: str) -> None:
    host = urlparse(url).netloc.lower()
    delay = HOST_DELAYS.get(host, DEFAULT_DELAY)
    elapsed = time.monotonic() - _last_call.get(host, 0.0)
    if elapsed < delay:
        time.sleep(delay - elapsed)
    _last_call[host] = time.monotonic()
