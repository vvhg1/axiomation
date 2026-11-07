"""The viewer's server: the page's static files and
its reads, on 127.0.0.1 only, from the standard library's HTTP server;
a request that names another host than 127.0.0.1 or localhost is refused.

Every read is the command line's: a request names a command and its
flags, which the reads' own parser reads and their own report function
answers (`commands.reads.query_report`), as `--limit 0 --json`; `check`'s verdict
is the command's own (`check.verdict`), the paper's `[gates]` read on each
request. So the page and an agent cannot be told two things. Nothing is
written. A kept state's export never changes, but what is filed against
it does (judgments, findings, marks): each state's read (`queries.View`)
and each answer from the store alone are kept while the store's revision
holds, and worked out again once it moves; the kept states' reader
(`judgments.Reader`) is kept while the kept states stand, since a filing
moves none of them. `/api/now` answers that revision and the paper's
files against the latest state, cheaply and never behind a read, for the
page to ask often.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import mimetypes
import threading
from collections.abc import Mapping, Sequence
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from axiomation import check, judgments, queries, settings, versions
from axiomation.commands import UsageError, reads
from axiomation.records import RecordStore
from axiomation.versions import State, VersionError
from axiomation.viewer.payload import PayloadError, text_layer

#: The page: `index.html`, its stylesheet and scripts, the vendored ones.
PAGE = Path(__file__).parent / "page"
HOST = "127.0.0.1"
#: The reads a state's route takes, with whether they name a block.
READS = {"show": True, "reach": True, "blocks": False, "open": False, "inbox": False}
#: Flags the server sets itself; a request may not. The changes route's
#: `from` and `to` are its own parameters, read before these.
OWN_FLAGS = {"store", "state", "limit", "json"}
#: The latest state's reads the page asks first, on a load and on a read
#: again, worked out ahead when the revision moves (`Viewer.warm`).
WARMED = ("text", "open", "check")


class ApiError(Exception):
    """A request the API cannot answer: its status and the words."""

    def __init__(self, status: HTTPStatus, words: str) -> None:
        super().__init__(words)
        self.status = status


class Viewer:
    """The reads over one store, each kept state read once a store revision.
    `warm`: the latest state's first reads worked out in the background at
    start and whenever `/api/now` first sees the revision move."""

    def __init__(
        self,
        store: RecordStore,
        directory: Path,
        overlays: Sequence[Mapping[str, Any]] = (),
        warm: bool = False,
    ) -> None:
        self.store = store
        self.directory = Path(directory)
        self._warm = warm
        # the revision last warmed, or being warmed, under a lock of its own:
        # the poll that warms never waits behind a read
        self._warmed: str | None = None
        self._warming = threading.Lock()
        self.warm_thread: threading.Thread | None = None
        # the overlays `--overlay` gave, checked at start (`overlays.py`)
        self.overlays = list(overlays)
        # each state's read and each answer, under the store's revision: a
        # filing (a ruling) moves what the layer draws, never the state
        self._revision: str | None = None
        self._views: dict[str, queries.View] = {}
        self._answers: dict[tuple[Any, ...], bytes] = {}
        # one read at a time: a View computes its readings lazily
        self._lock = threading.Lock()

    def view_of(self, store: RecordStore, kept: State) -> queries.View:
        found = self._views.get(kept.id)
        if found is None:
            found = self._views[kept.id] = queries.View(store, kept, self.reader())
        return found

    def reader(self) -> judgments.Reader:
        """The kept states' reader, read again once a state is kept (a
        filing moves none of them): the process's (`kept_reader`)."""
        return judgments.kept_reader(self.store)

    def state(self, name: str) -> State:
        if name == "latest":
            try:
                found = versions.latest(self.store)
            except VersionError as error:
                raise ApiError(HTTPStatus.CONFLICT, str(error)) from error
            if found is None:
                raise ApiError(HTTPStatus.NOT_FOUND, "the store keeps no state")
            return found
        try:
            return versions.state(self.store, name)
        except VersionError as error:
            raise ApiError(HTTPStatus.NOT_FOUND, str(error)) from None

    def answer(self, path: str, query: Mapping[str, Sequence[str]]) -> bytes:
        """The JSON (or the export's bytes) a route answers."""
        parts = [unquote(p) for p in path.strip("/").split("/")]
        if parts == ["api", "now"]:
            # asked every few seconds: it must not wait behind a slow read
            return self._now()
        with self._lock:
            revision = self.store.revision()
            if revision != self._revision:
                self._views.clear()
                self._answers.clear()
                self._revision = revision
            key = self._key(parts, query)
            if key is None:
                return self._answer(parts, query)
            found = self._answers.get(key)
            if found is None:
                found = self._answers[key] = self._answer(parts, query)
            return found

    def _key(
        self, parts: list[str], query: Mapping[str, Sequence[str]]
    ) -> tuple[Any, ...] | None:
        """What an answer kept under the revision is kept by; None for a
        route that reads the paper's files (the states with the files
        against the latest, the pending edit), which a revision does not
        stamp. A state's reads and the changes between two states read the
        store alone; the verdict reads the paper's `[gates]` too, on each
        request."""
        if parts[:2] not in (["api", "state"], ["api", "changes"], ["api", "history"]):
            return None
        asked = tuple(sorted((name, tuple(v)) for name, v in query.items()))
        if parts[:2] == ["api", "state"] and parts[3:] == ["check"]:
            try:
                gates = check.modes(self.directory)
            except settings.SettingsError as error:
                raise ApiError(HTTPStatus.BAD_REQUEST, str(error)) from None
            asked += tuple(sorted((g.value, m.value) for g, m in gates.items()))
        return (tuple(parts), asked)

    def warm(self) -> threading.Thread | None:
        """The latest state's first reads (`WARMED`) worked out on a thread
        of their own, once a revision: a page re-reads once the revision
        holds for a poll, so its reads are then kept answers. None where
        this revision is warmed already. A read that fails here is left to
        the page's own request, which says why."""
        revision = self.store.revision()
        with self._warming:
            if revision == self._warmed:
                return None
            self._warmed = revision

        def run() -> None:
            latest = versions.latest_if_one(self.store)
            if latest is None:
                return
            for route in WARMED:
                try:
                    self.answer(f"/api/state/{latest.id}/{route}", {})
                except Exception:  # noqa: BLE001 - the page's request says why
                    return

        thread = threading.Thread(target=run, name="axiomation-warm", daemon=True)
        self.warm_thread = thread
        thread.start()
        return thread

    def _now(self) -> bytes:
        """The store's revision and the paper's files against the latest
        state: what the page asks to know whether it is behind."""
        found = _json(
            {
                "revision": self.store.revision(),
                "files": reads.files_data(
                    self.store, self.directory, versions.latest_if_one(self.store)
                ),
            }
        )
        if self._warm:
            # after the answer is made: the warm thread would slow it
            self.warm()
        return found

    def _answer(self, parts: list[str], query: Mapping[str, Sequence[str]]) -> bytes:
        match parts:
            case ["api", "states"]:
                return _json(reads.states_and_files(self.store, self.directory))
            case ["api", "overlays"]:
                return _json({"overlays": self.overlays})
            case ["api", "state", name, "export"]:
                return versions.kept_export(self.store, self.state(name))
            case ["api", "state", name, "text"]:
                return _json(text_layer(self.view_of(self.store, self.state(name))))
            case ["api", "state", name, "check"]:
                return self._check(self.state(name))
            case ["api", "state", name, command] if (
                command in READS and not READS[command]
            ):
                return self._read(command, self.state(name), None, query)
            case ["api", "state", name, command, ref] if READS.get(command):
                return self._read(command, self.state(name), ref, query)
            case ["api", "changes"]:
                return self._changes(query)
            case ["api", "pending"]:
                return self._pending(query)
            case ["api", "history", node]:
                kept = self.state(_one(query, "state") or "latest")
                try:
                    return _json(queries.history_data(self.store, node, kept))
                except VersionError as error:
                    raise ApiError(HTTPStatus.BAD_REQUEST, str(error)) from None
        raise ApiError(HTTPStatus.NOT_FOUND, f"no such read: /{'/'.join(parts)}")

    def _pending(self, query: Mapping[str, Sequence[str]]) -> bytes:
        """The paper's files against the latest state, as `changes` with
        neither state named reads them; `{"pending": false}` where the
        files are that state's version, as `changes` would then answer the
        latest state against its parent."""
        if "from" in query or "to" in query:
            raise ApiError(
                HTTPStatus.BAD_REQUEST,
                "pending reads the paper's files against the latest state: it "
                "takes no from or to (/api/changes does)",
            )

        files = reads.files_data(
            self.store, self.directory, versions.latest_if_one(self.store)
        )
        if not files["pending"]:
            return _json(files)
        argv = ["changes", str(self.directory)]
        return self._run(argv + _flags("changes", query))

    def _read(
        self,
        command: str,
        kept: State,
        ref: str | None,
        query: Mapping[str, Sequence[str]],
    ) -> bytes:
        argv = [command, str(self.directory), "--state", kept.id]
        argv += [] if ref is None else [ref]
        return self._run(argv + _flags(command, query))

    def _check(self, kept: State) -> bytes:
        try:
            found = check.verdict(self.view_of(self.store, kept), self.directory)
        except settings.SettingsError as error:
            raise ApiError(HTTPStatus.BAD_REQUEST, str(error)) from None
        return _json(found.data())

    def _changes(self, query: Mapping[str, Sequence[str]]) -> bytes:
        newer = self.state(_one(query, "to") or "latest")
        older = _one(query, "from") or newer.parent
        if older is None:
            raise ApiError(
                HTTPStatus.BAD_REQUEST,
                f"state {newer.id} has no parent: name one with from=",
            )
        argv = ["changes", str(self.directory), "--from", self.state(older).id]
        argv += ["--to", newer.id]
        rest = {k: v for k, v in query.items() if k not in ("from", "to")}
        return self._run(argv + _flags("changes", rest))

    def _run(self, argv: list[str]) -> bytes:
        arguments = _parsed([*argv, "--limit", "0", "--json"])
        arguments.store = self.store.directory
        try:
            report = reads.query_report(
                reads.request(arguments), self.store, self.view_of
            )
        except (queries.QueryError, VersionError, UsageError) as error:
            raise ApiError(HTTPStatus.BAD_REQUEST, str(error)) from None
        return queries.render(report, 0, as_json=True).encode()


def _json(data: Any) -> bytes:
    return (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode()


def _one(query: Mapping[str, Sequence[str]], name: str) -> str | None:
    values = query.get(name) or []
    return values[-1] if values else None


def _subparser(command: str) -> argparse.ArgumentParser:
    commands = next(
        a for a in reads.parser()._actions if isinstance(a, argparse._SubParsersAction)
    )
    return commands.choices[command]


def _flags(command: str, query: Mapping[str, Sequence[str]]) -> list[str]:
    """A request's parameters as the command's flags: `name=value`,
    repeatable, as `--name value`; a switch as `name=1`."""
    options = _subparser(command)._option_string_actions
    argv: list[str] = []
    for name, values in query.items():
        flag = f"--{name.replace('_', '-')}"
        if name in OWN_FLAGS or flag not in options:
            raise ApiError(
                HTTPStatus.BAD_REQUEST, f"{command} takes no parameter {name!r}"
            )
        action = options[flag]
        if action.nargs == 0:
            if any(v not in ("", "1", "true") for v in values):
                raise ApiError(HTTPStatus.BAD_REQUEST, f"{name} is a switch: {name}=1")
            argv.append(flag)
        else:
            for value in values:
                argv += [flag, value]
    return argv


def _parsed(argv: list[str]) -> argparse.Namespace:
    """The reads' parser on `argv`; its refusal as a 400 with its words."""
    words = io.StringIO()
    try:
        with contextlib.redirect_stderr(words):
            arguments = reads.parser().parse_args(argv)
    except SystemExit:
        said = words.getvalue().strip().splitlines()
        raise ApiError(
            HTTPStatus.BAD_REQUEST, said[-1] if said else "a request it cannot read"
        ) from None
    return arguments


class _Handler(BaseHTTPRequestHandler):
    viewer: Viewer

    def do_GET(self) -> None:  # noqa: N802 - the standard library's name
        if not self._asked_of_this_server():
            words = b"the viewer answers requests to 127.0.0.1 or localhost alone\n"
            self._send(HTTPStatus.FORBIDDEN, words, "text")
            return
        url = urlsplit(self.path)
        if url.path.startswith("/api/"):
            try:
                body = self.viewer.answer(url.path, parse_qs(url.query, True))
            except ApiError as error:
                self._send(error.status, _json({"error": str(error)}), "json")
            except PayloadError as error:
                self._send(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    _json({"error": str(error)}),
                    "json",
                )
            else:
                self._send(HTTPStatus.OK, body, "json")
            return
        found = _static(url.path)
        if found is None:
            self._send(HTTPStatus.NOT_FOUND, b"not found\n", "text")
            return
        kind = mimetypes.guess_type(found.name)[0] or "application/octet-stream"
        self._send(HTTPStatus.OK, found.read_bytes(), kind)

    def _asked_of_this_server(self) -> bool:
        """Whether the request's `Host` names this server, as the URL
        `view` prints does: a page elsewhere that points its own name at
        127.0.0.1 (DNS rebinding) names its own, and is refused, so it
        reads nothing of the paper."""
        port = self.server.server_address[1]
        return self.headers.get("Host", "") in {f"{HOST}:{port}", f"localhost:{port}"}

    def _send(self, status: HTTPStatus, body: bytes, kind: str) -> None:
        kinds = {"json": "application/json", "text": "text/plain"}
        content = kinds.get(kind, kind)
        if content.startswith("text/") or content.endswith(("json", "javascript")):
            content += "; charset=utf-8"
        self.send_response(status)
        self.send_header("Content-Type", content)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        try:
            self.end_headers()
            self.wfile.write(body)
        except ConnectionError:
            # the page went away before its answer (a reload cancels what
            # it asked): there is no one to answer, and nothing went wrong
            self.close_connection = True

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        pass


def _static(path: str) -> Path | None:
    """A file of the page, never one outside it."""
    name = unquote(path).lstrip("/") or "index.html"
    root = PAGE.resolve()
    found = (root / name).resolve()
    if not found.is_relative_to(root) or not found.is_file():
        return None
    return found


class _Server(ThreadingHTTPServer):
    """The standard library's server with room for the page's first
    requests: the page asks for some twenty files at once, and the
    standard queue of 5 connections not yet accepted resets the rest while
    a read holds the interpreter."""

    request_queue_size = 128


def serve(
    store: RecordStore,
    directory: Path,
    port: int = 0,
    overlays: Sequence[Mapping[str, Any]] = (),
    warm: bool = False,
) -> ThreadingHTTPServer:
    """The server, bound to 127.0.0.1 on `port` (0: a free one), not yet
    serving; `overlays` the checked overlays it serves; `warm`, the latest
    state's first reads worked out ahead (`Viewer.warm`), from now on."""
    viewer = Viewer(store, directory, overlays, warm)
    handler = type("Handler", (_Handler,), {"viewer": viewer})
    server = _Server((HOST, port), handler)
    server.daemon_threads = True
    if warm:
        viewer.warm()
    return server
