"""A queue session's tool server: MCP over stdio, JSON-RPC 2.0, one message
a line, on the standard library alone (docs/architecture.md § 2).

    python -I -m axiomation.queue.server SETTINGS

`SETTINGS` names the store, the kept state or the question, the workflow
and its options, the step served, the session, the spool and how long
`next` waits
(`queue.tool_server` writes it). The host starts it outside the session's
sandbox; it reads the store and writes the spool alone. Settings under
`review` serve a review session by block its tools instead
(`review_tools`), which write nothing.
"""

from __future__ import annotations

import base64
import contextlib
import json
import sys
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any, Protocol, TextIO

from axiomation import review_tools, versions
from axiomation.queue import (
    SERVER_NAME,
    Asked,
    Image,
    Run,
    Served,
    Spool,
    Tool,
    opened,
    recorded,
)
from axiomation.records import RecordStore

PROTOCOL_VERSION = "2025-06-18"


class Serves(Protocol):
    """What a tool server serves: its tools, and a call's answer with
    whether it was refused."""

    @property
    def tools(self) -> tuple[Tool, ...]: ...

    def call(self, name: str, args: Mapping[str, Any]) -> tuple[str | Image, bool]: ...


@contextlib.contextmanager
def served(settings: Path) -> Iterator[Serves]:
    """The session `settings` names, served for as long as it is open."""
    given = json.loads(settings.read_text(encoding="utf-8"))
    if review_tools.REVIEW in given:
        yield review_tools.ReviewTools.from_settings(given[review_tools.REVIEW])
        return
    store = RecordStore(Path(given["store"]))
    at = (
        Asked(given["state"])
        if given.get("asked")
        else versions.state(store, given["state"])
    )
    run = Run(store, at, given["factory"], given["options"])
    with opened(run) as workflow:
        yield Served(
            workflow,
            recorded(store, workflow),
            Spool(Path(given["spool"])),
            int(given["step"]),
            str(given["session"]),
            float(given["wait_seconds"]),
        )


def respond(session: Serves, message: Mapping[str, Any]) -> dict[str, Any] | None:
    """The reply to one JSON-RPC message; None for a notification."""
    ident, method = message.get("id"), message.get("method")
    if ident is None:
        return None
    params = message.get("params") or {}
    if method == "initialize":
        result: dict[str, Any] = {
            "protocolVersion": params.get("protocolVersion", PROTOCOL_VERSION),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": "1"},
        }
    elif method == "tools/list":
        result = {"tools": [t.listed() for t in session.tools]}
    elif method == "tools/call":
        try:
            given, refused = session.call(
                str(params.get("name")), params.get("arguments") or {}
            )
        except Exception as error:  # noqa: BLE001 - the session is told, and goes on
            given, refused = f"refused: {error}", True
        result = {"content": content(given), "isError": refused}
    elif method == "ping":
        result = {}
    else:
        return {
            "jsonrpc": "2.0",
            "id": ident,
            "error": {"code": -32601, "message": f"no method {method!r}"},
        }
    return {"jsonrpc": "2.0", "id": ident, "result": result}


def content(given: str | Image) -> list[dict[str, str]]:
    """A call's answer as MCP content: its text, or a picture after the
    line that says what it shows."""
    if isinstance(given, str):
        return [{"type": "text", "text": given}]
    return [
        {"type": "text", "text": given.said},
        {
            "type": "image",
            "data": base64.b64encode(given.png).decode("ascii"),
            "mimeType": "image/png",
        },
    ]


def serve(session: Serves, given: TextIO, out: TextIO) -> None:
    for line in given:
        if not line.strip():
            continue
        reply = respond(session, json.loads(line))
        if reply is not None:
            out.write(json.dumps(reply, ensure_ascii=False) + "\n")
            out.flush()


def main(argv: list[str] | None = None) -> None:
    (settings,) = sys.argv[1:] if argv is None else argv
    with served(Path(settings)) as session:
        serve(session, sys.stdin, sys.stdout)


if __name__ == "__main__":
    main()
