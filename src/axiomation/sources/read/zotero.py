"""The optional Zotero adapter.

The bibliography file is the contract; this is an adapter. Any `.bib` with
`file` fields works, which is what a second author on JabRef or Overleaf
needs. When Zotero is running with Better BibTeX, it can do two more things,
both read-only: resolve a bib key to the attachment on disk when the stored
`file_path` has gone stale, and produce a `zotero://select` link so a human
can jump from a finding to the item.

Agents never write to Zotero or to the bib. No write call exists here.
Standard library only: the JSON-RPC endpoint is one POST.

Where Zotero listens is the person's `zotero_url`. Every
function here takes the address; the CLI and the MCP server pass the
resolved one, and the default is what a stock Zotero answers on.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from axiomation.sources.config import DEFAULT_ZOTERO_URL

PING_PATH = "/connector/ping"
RPC_PATH = "/better-bibtex/json-rpc"
TIMEOUT = 5.0


@dataclass(frozen=True, slots=True)
class Attachment:
    """One file Zotero holds for an item."""

    path: Path
    open_link: str


def available(base_url: str = DEFAULT_ZOTERO_URL, *, timeout: float = TIMEOUT) -> bool:
    """Whether Zotero answers. An unavailable Zotero degrades to bib-only."""
    try:
        with urllib.request.urlopen(f"{base_url}{PING_PATH}", timeout=timeout) as page:
            return page.status == 200
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def _rpc(method: str, params: list[Any], base_url: str, timeout: float) -> Any | None:
    """One JSON-RPC call. Returns None for anything that did not work."""
    body = json.dumps(
        {"jsonrpc": "2.0", "method": method, "params": params, "id": 1}
    ).encode()
    request = urllib.request.Request(
        f"{base_url}{RPC_PATH}",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            answer = json.loads(response.read())
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return None
    return answer.get("result")


def attachments(
    bib_key: str, *, base_url: str = DEFAULT_ZOTERO_URL, timeout: float = TIMEOUT
) -> tuple[Attachment, ...]:
    """The files Zotero holds for a bib key, in the order it lists them."""
    result = _rpc("item.attachments", [bib_key], base_url, timeout)
    if not isinstance(result, list):
        return ()
    return tuple(
        Attachment(path=Path(item["path"]), open_link=item.get("open", ""))
        for item in result
        if isinstance(item, dict) and item.get("path")
    )


def pdf_path(
    bib_key: str, *, base_url: str = DEFAULT_ZOTERO_URL, timeout: float = TIMEOUT
) -> Path | None:
    """The attachment that is a PDF on this machine, where Zotero holds
    one (several of the same bytes are one); None where it holds several:
    which is the edition cited is the bibliography's choice
    (`sources.files`), never the order Zotero lists them in."""
    from axiomation.sources.files import file_sha256

    found = {
        file_sha256(attachment.path): attachment.path
        for attachment in attachments(bib_key, base_url=base_url, timeout=timeout)
        if attachment.path.suffix.lower() == ".pdf" and attachment.path.is_file()
    }
    return next(iter(found.values())) if len(found) == 1 else None


def item_key(
    bib_key: str, *, base_url: str = DEFAULT_ZOTERO_URL, timeout: float = TIMEOUT
) -> str | None:
    """Zotero's own key for a bib key, the last segment of the item URI."""
    result = _rpc("item.search", [bib_key], base_url, timeout)
    if not isinstance(result, list):
        return None
    for item in result:
        if not isinstance(item, dict):
            continue
        if item.get("citekey") not in (None, bib_key):
            continue
        uri = item.get("id") or ""
        key = uri.rsplit("/", 1)[-1] if isinstance(uri, str) else ""
        if key:
            return key
    return None


def select_link(
    bib_key: str, *, base_url: str = DEFAULT_ZOTERO_URL, timeout: float = TIMEOUT
) -> str | None:
    """A `zotero://select` link, so a finding can be opened in one click."""
    key = item_key(bib_key, base_url=base_url, timeout=timeout)
    return f"zotero://select/library/items/{key}" if key else None
