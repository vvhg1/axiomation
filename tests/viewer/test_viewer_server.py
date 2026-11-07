"""The viewer's server: every
read the CLI's own `--json` for the same question, on 127.0.0.1 only, the
page's files and nothing else, and nothing written.

On the queries' synthetic paper built and updated by the command line (two
states, a changed block), and on `test_marks`'s chain updated and kept
(stale marks). No model is called.
"""

from __future__ import annotations

import http.client
import json
import socket
import threading
import urllib.error
import urllib.request
from http import HTTPStatus
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from axiomation import queries, rulings, versions
from axiomation.cli import BUILD_STAGES, main
from axiomation.driver import ReviewSettings
from axiomation.judgments import Author, seen
from axiomation.queries import View
from axiomation.reader import read_paper
from axiomation.records import RecordStore, RulingKind
from axiomation.resolver import Resolver
from axiomation.runner import Runner
from axiomation.update import UpdateSettings
from axiomation.update import run as run_update
from axiomation.viewer.payload import text_layer
from axiomation.viewer.server import WARMED, Viewer, _Handler, serve
from test_queries import (
    EDITED_SETTING,
    _updated,
    built,
    rich,  # noqa: F401 - the fixture
)
from test_update import FILES, FLAT, answering, stepping


def built_and_updated(tmp_path: Path, capsys) -> tuple[Path, RecordStore]:
    """The synthetic paper built, edited and updated by the command line,
    as `test_queries` does: two kept states."""
    root, directory, _ = _updated(tmp_path, capsys)
    store = RecordStore(directory)
    updated(root, store)
    return root, store


def updated(root: Path, store: RecordStore) -> None:
    """The paper's edit carried onto its graph by `update`, the writer
    answering: a new kept state."""
    assert main(["update", str(root), "--host", "none"]) == 5
    (key,) = [a.key for a in store.authoritative().values() if a.kind == "identity"]
    answered = run_update(
        read_paper(root),
        store,
        UpdateSettings(
            files=FILES,
            review=ReviewSettings(stages=BUILD_STAGES, files=FILES),
            paper=root.resolve(),
        ),
        Runner(
            stepping(answering({"we-fix-a-field": ["Δ reworded — a clause added"]})),
            Resolver(store),
            10.0,
            FLAT,
        ),
        identity=key,
    )
    assert answered.complete, answered.unanswered
    assert main(["update", str(root), "--host", "none"]) == 0


@pytest.fixture
def served(tmp_path, capsys):
    """The two states' store served, a finding filed on the build's state on
    the block the update changed, so it lapses, and one on another block,
    which stays open: the base URL, the paper, the store and the
    latest state."""
    paper, store = built_and_updated(tmp_path, capsys)
    state = versions.latest(store)
    built = View(store, next(s for s in versions.states(store) if s.id == state.parent))
    other = next(
        built.handle(b) for b in built.blocks if built.handle(b) != "we-fix-a-field"
    )
    for on in ("we-fix-a-field", other):
        code = main(
            [
                "find",
                str(paper),
                "--state",
                state.parent,
                "--aspect",
                "presentation",
                "--on",
                on,
                "--severity",
                "2",
                "--problem",
                f"a problem in {on}",
            ]
        )
        assert code == 0, capsys.readouterr().err
    server = serve(store, paper)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    yield f"http://{host}:{port}", paper, store, state
    server.shutdown()
    server.server_close()


def get(url: str) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(url) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def cli(capsys, *args: str) -> str:
    capsys.readouterr()
    code = main(list(args))
    out = capsys.readouterr()
    assert code == 0, out.err
    return out.out


def files_of(directory: Path) -> dict[str, tuple[int, int]]:
    return {
        str(p.relative_to(directory)): (p.stat().st_mtime_ns, p.stat().st_size)
        for p in sorted(directory.rglob("*"))
        if p.is_file()
    }


@pytest.mark.usefixtures("develop_mode")
def test_every_read_is_the_command_line_s_json(served, capsys) -> None:
    base, paper, store, state = served
    before = files_of(store.directory)
    assert state.parent is not None
    s = state.id
    kept = ["--store", str(store.directory)]
    whole = ["--limit", "0", "--json"]
    view = View(store, state)
    some = next(e["source"] for e in view.edges if e["source"] in view.blocks)
    node = view.node(some)

    def same(route: str, *args: str) -> None:
        status, body = get(base + route)
        assert status == 200, body
        assert json.loads(body) == json.loads(cli(capsys, *args)), route

    same("/api/states", "states", str(paper), *kept, "--json")
    same(
        f"/api/state/{s}/show/{node}?whole=1&history=1",
        "show",
        str(paper),
        node,
        "--state",
        s,
        "--whole",
        "--history",
        *kept,
        *whole,
    )
    # a finding by its id: the block it is on, as the search selects it
    lapsing = store.judgment_records().findings[0].id
    same(
        f"/api/state/{s}/show/{lapsing}?whole=1&history=1",
        "show",
        str(paper),
        lapsing,
        "--state",
        s,
        "--whole",
        "--history",
        *kept,
        *whole,
    )
    shown = json.loads(get(f"{base}/api/state/{s}/show/{lapsing}?whole=1&history=1")[1])
    assert shown["finding"]["id"] == lapsing and shown["finding"]["state"] == "lapsed"
    assert shown["block"]["handle"] == "we-fix-a-field"
    same(
        f"/api/state/{s}/reach/{node}",
        "reach",
        str(paper),
        node,
        "--state",
        s,
        *kept,
        *whole,
    )
    same(
        f"/api/state/{s}/blocks?role=claim&role=definition&from={node}&kind=uses",
        "blocks",
        str(paper),
        "--state",
        s,
        "--role",
        "claim",
        "--role",
        "definition",
        "--from",
        node,
        "--kind",
        "uses",
        *kept,
        *whole,
    )
    same(f"/api/state/{s}/open", "open", str(paper), "--state", s, *kept, *whole)
    opened = json.loads(get(f"{base}/api/state/{s}/open")[1])
    assert [i["status"] for i in opened["lapsed"]["items"]] == ["lapsed"]
    assert opened["lapsed"]["items"][0]["handle"] == "we-fix-a-field"
    assert opened["finding"]["total"] == 1
    changed = json.loads(get(f"{base}/api/changes?from={state.parent}&to={s}")[1])
    assert changed["lapsed"]["items"] == opened["lapsed"]["items"]
    same(
        f"/api/state/{s}/open?kind=stale",
        "open",
        str(paper),
        "--state",
        s,
        "--kind",
        "stale",
        *kept,
        *whole,
    )
    same(
        f"/api/changes?from={state.parent}&to={s}",
        "changes",
        str(paper),
        "--from",
        state.parent,
        "--to",
        s,
        *kept,
        *whole,
    )
    same(
        f"/api/history/{node}?state={s}",
        "history",
        str(paper),
        node,
        "--state",
        s,
        *kept,
    )
    # `latest` and a prefix name the same state
    assert (
        get(f"{base}/api/state/latest/open")[1]
        == get(f"{base}/api/state/{s[:6]}/open")[1]
    )
    status, body = get(f"{base}/api/state/{s}/export")
    assert body == versions.kept_export(store, state)
    status, body = get(f"{base}/api/state/{s}/text")
    assert json.loads(body) == json.loads(json.dumps(text_layer(view)))
    # nothing under the store was written
    assert files_of(store.directory) == before


@pytest.mark.usefixtures("develop_mode")
def test_the_verdict_is_check_s_json_under_the_paper_s_gates(served, capsys) -> None:
    """The header's chip reads `check --json` over the shown
    state, the paper's `[gates]` as the command reads them; a `[gates]`
    the command refuses is refused with its words."""
    base, paper, store, state = served
    before = files_of(store.directory)

    def command() -> tuple[int, bytes]:
        capsys.readouterr()
        code = main(
            ["check", str(paper), "--state", state.id, "--store", str(store.directory)]
            + ["--json"]
        )
        return code, capsys.readouterr().out.encode()

    code, said = command()
    status, body = get(f"{base}/api/state/{state.id}/check")
    assert status == 200 and body == said
    assert json.loads(body)["passes"] is (code == 0)
    assert get(f"{base}/api/state/latest/check")[1] == body
    assert [g["outcome"] for g in json.loads(body)["gates"]].count("warn") == 1
    # a mode changed in the paper's settings is read on the next request:
    # the unused premise it warned of now fails the paper
    (paper / "axiomation.toml").write_text(
        '[gates]\nunused-premises = "block"\n', encoding="utf-8"
    )
    code, said = command()
    status, body = get(f"{base}/api/state/{state.id}/check")
    assert status == 200 and body == said and code == 1
    verdict = json.loads(body)
    assert verdict["passes"] is False
    failing = [g for g in verdict["gates"] if g["outcome"] == "fail"]
    assert [g["gate"] for g in failing] == ["unused-premises"]
    assert [i["kind"] for i in failing[0]["items"]] == ["unused-premise"]
    (paper / "axiomation.toml").write_text('[gates]\nnosuch = "block"\n')
    status, body = get(f"{base}/api/state/{state.id}/check")
    assert status == 400 and "names no gate 'nosuch'" in json.loads(body)["error"]
    assert files_of(store.directory) == before


@pytest.mark.usefixtures("develop_mode")
def test_what_it_refuses(served) -> None:
    base, _, _, state = served
    status, body = get(f"{base}/api/state/nosuch/open")
    assert status == 404 and "nosuch" in json.loads(body)["error"]
    status, body = get(f"{base}/api/state/{state.id}/show/no-such-block")
    assert status == 400 and "names no block or group" in json.loads(body)["error"]
    status, body = get(f"{base}/api/state/{state.id}/blocks?limit=3")
    assert status == 400 and "no parameter 'limit'" in json.loads(body)["error"]
    status, body = get(f"{base}/api/state/{state.id}/blocks?standing=bogus")
    assert status == 400 and "invalid choice" in json.loads(body)["error"]
    status, _ = get(f"{base}/api/state/{state.id}/graph")
    assert status == 404
    for path in ("/../pyproject.toml", "/%2e%2e/%2e%2e/cli.py", "/nothing.js"):
        assert get(base + path)[0] == 404, path


@pytest.mark.usefixtures("develop_mode")
def test_it_serves_the_page_on_127_0_0_1_only(served) -> None:
    base, *_ = served
    assert base.startswith("http://127.0.0.1:")
    status, body = get(base + "/")
    assert status == 200 and b'<script src="src/app.js">' in body
    for script in ("src/app.js", "view.css", "wordmark.svg"):
        assert get(f"{base}/{script}")[0] == 200


@pytest.mark.usefixtures("develop_mode")
def test_the_overlays_given_at_start_are_served_as_checked(tmp_path, capsys) -> None:
    paper, store = built_and_updated(tmp_path, capsys)
    overlay = {
        "name": "a reviewer's marks",
        "items": [
            {"anchor": {"kind": "paper"}, "channel": "custom", "label": "read me"}
        ],
    }
    server = serve(store, paper, overlays=[overlay])
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        status, body = get(f"http://{host}:{port}/api/overlays")
    finally:
        server.shutdown()
        server.server_close()
    assert status == 200
    assert json.loads(body) == {"overlays": [overlay]}


@pytest.mark.usefixtures("develop_mode")
def test_a_record_filed_while_serving_shows_at_the_next_request(served, capsys) -> None:
    """A state's export never changes, but what is filed against it does:
    a finding filed while the page is open shows at the next request, on
    `open` and on `check`, as the command line reads them then."""
    base, paper, store, state = served
    revision = json.loads(get(base + "/api/now")[1])["revision"]
    opened = json.loads(get(f"{base}/api/state/{state.id}/open")[1])
    assert opened["finding"]["total"] == 1
    get(f"{base}/api/state/{state.id}/check")

    cli(
        capsys,
        *("find", str(paper), "--aspect", "presentation", "--on", "we-fix-a-field"),
        *("--severity", "3", "--problem", "a problem filed while serving"),
    )

    assert json.loads(get(base + "/api/now")[1])["revision"] != revision
    opened = json.loads(get(f"{base}/api/state/{state.id}/open")[1])
    assert opened["finding"]["total"] == 2
    kept = ["--store", str(store.directory)]
    capsys.readouterr()
    main(["check", str(paper), "--state", state.id, *kept, "--json"])
    said = capsys.readouterr().out.encode()
    assert get(f"{base}/api/state/{state.id}/check")[1] == said
    assert b"a problem filed while serving" in said


@pytest.mark.usefixtures("develop_mode")
def test_the_files_edited_and_a_state_made_while_serving_show(tmp_path, capsys) -> None:
    """`/api/now` says when the paper's files are not the latest state's
    version, as `states` does; `/api/pending` is `changes` on the files;
    a state an update makes while serving is listed and is the latest."""
    root, directory, _ = built(tmp_path, capsys)
    store = RecordStore(directory)
    first = versions.latest(store)
    server = serve(store, root)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    base = f"http://{host}:{port}"
    kept = ["--store", str(store.directory)]
    try:
        now = json.loads(get(base + "/api/now")[1])
        assert now["files"] == {
            "version": first.version,
            "state": first.id,
            "pending": False,
        }
        setting = root / "main.tex"
        original = setting.read_text(encoding="utf-8")
        setting.write_text(EDITED_SETTING, encoding="utf-8")
        edited = json.loads(get(base + "/api/now")[1])
        assert edited["revision"] == now["revision"]
        assert edited["files"]["pending"] is True
        assert edited["files"]["version"] != first.version
        states = get(base + "/api/states")[1]
        assert json.loads(states) == json.loads(
            cli(capsys, "states", str(root), *kept, "--json")
        )
        assert json.loads(states)["files"] == edited["files"]
        assert "an edit not yet updated" in cli(capsys, "states", str(root), *kept)
        pending = json.loads(get(base + "/api/pending?whole=1")[1])
        # the files against the latest state: no state of the caller's
        status, body = get(base + "/api/pending?from=x")
        assert status == 400 and b"takes no from or to" in body
        said = cli(capsys, "changes", str(root), *kept, "--whole", "--limit", "0")
        assert pending == json.loads(
            cli(
                capsys,
                *("changes", str(root), *kept, "--whole", "--limit", "0", "--json"),
            )
        )
        (changed,) = pending["text"]["items"]
        assert changed["class"] == "changed"
        assert "edited" in changed["new"][0]["text"]
        assert changed["old"][0]["text"] and changed["old"][0]["text"] in said
        # restored, the paper is its latest state's version again, and the
        # pending edit says so rather than answering another comparison
        setting.write_text(original, encoding="utf-8")
        restored = json.loads(get(base + "/api/now")[1])["files"]
        assert restored["pending"] is False
        assert json.loads(get(base + "/api/pending?whole=1")[1]) == restored

        setting.write_text(EDITED_SETTING, encoding="utf-8")
        updated(root, store)
        after = json.loads(get(base + "/api/now")[1])
        assert after["revision"] != now["revision"]
        assert after["files"]["pending"] is False
        made = versions.latest(store)
        assert made.parent == first.id and after["files"]["state"] == made.id
        listed = json.loads(get(base + "/api/states")[1])["states"]
        assert [s["id"] for s in listed] == [first.id, made.id]
        status, body = get(base + "/api/state/latest/open")
        assert status == 200 and json.loads(body)["state"] == made.id
    finally:
        server.shutdown()
        server.server_close()


def test_a_ruling_filed_while_serving_redraws_its_claim(rich) -> None:  # noqa: F811
    """The text layer draws each claim by its support (`docs/model.md` § 8),
    which a ruling moves without a state: filed while the page is open,
    the next text read shows its claim no argument owed."""
    view, _, _ = rich
    store, kept = view.store, view.state
    viewer = Viewer(store, store.directory.parent / "paper")
    route = f"/api/state/{kept.id}/text"

    def supports() -> list[tuple[str, str, str | None]]:
        layer = json.loads(viewer.answer(route, {}))
        return [(s["block"], s["words"], s["support"]) for s in layer["standings"]]

    before = supports()
    (block, words, support) = next(s for s in before if s[2] == "unsupported")
    status = next(
        s["status"]
        for s in view.standings
        if s["block"] == block and s["claim"]["text"] == words
    )
    assert status == "unsupported"
    on = rulings.subject(seen(store, kept), block, words)
    rulings.rule(store, kept, on, RulingKind.KNOWN, "a fact", Author())
    assert (block, words, "no-argument-owed") in supports()


@pytest.mark.usefixtures("develop_mode")
def test_an_answer_is_kept_while_the_revision_holds(
    served, monkeypatch, capsys
) -> None:
    """A read asked again while the store's revision holds is the answer
    kept, not worked out again; a filing moves the revision, and the next
    read is worked out on what was filed."""
    _, paper, store, state = served
    viewer = Viewer(store, paper)
    asked: list[str] = []
    before = queries.open_report

    def counted(view, *args, **kwargs):
        asked.append(view.state.id)
        return before(view, *args, **kwargs)

    monkeypatch.setattr(queries, "open_report", counted)
    route = f"/api/state/{state.id}/open"
    first = viewer.answer(route, {})
    assert viewer.answer(route, {}) is first and asked == [state.id]

    cli(
        capsys,
        *("find", str(paper), "--aspect", "presentation", "--on", "we-fix-a-field"),
        *("--severity", "3", "--problem", "a problem filed while kept"),
    )

    again = viewer.answer(route, {})
    assert asked == [state.id, state.id]
    assert (
        json.loads(again)["finding"]["total"]
        == json.loads(first)["finding"]["total"] + 1
    )


def test_a_page_gone_before_its_answer_is_no_error(capsys) -> None:
    """A reload cancels what the page asked; the answer then has no one to
    go to. Writing it fails, and the server says nothing and closes."""

    class Gone:
        def write(self, _: bytes) -> None:
            raise BrokenPipeError(32, "Broken pipe")

        def flush(self) -> None:
            pass

    handler = _Handler.__new__(_Handler)
    handler.wfile = Gone()
    handler.request_version = "HTTP/1.1"
    handler.requestline = "GET /api/now HTTP/1.1"
    handler.close_connection = False

    handler._send(HTTPStatus.OK, b"{}\n", "json")

    assert handler.close_connection is True
    assert capsys.readouterr() == ("", "")


@pytest.mark.usefixtures("develop_mode")
def test_a_request_naming_another_host_is_refused(served) -> None:
    """A page elsewhere can point its own name at 127.0.0.1 (DNS
    rebinding); its requests name that host, and the viewer refuses them.
    Named as 127.0.0.1 or localhost, on the server's port, it answers."""
    base, *_ = served
    host, port = urlsplit(base).hostname, urlsplit(base).port

    def status(named: str) -> int:
        connection = http.client.HTTPConnection(host, port, timeout=10)
        try:
            connection.request("GET", "/api/states", headers={"Host": named})
            return connection.getresponse().status
        finally:
            connection.close()

    assert status(f"evil.example:{port}") == HTTPStatus.FORBIDDEN
    assert status(f"127.0.0.1:{port + 1}") == HTTPStatus.FORBIDDEN
    assert status(f"127.0.0.1:{port}") == HTTPStatus.OK
    assert status(f"localhost:{port}") == HTTPStatus.OK


@pytest.mark.usefixtures("develop_mode")
def test_the_latest_state_s_first_reads_are_worked_out_ahead(served, capsys) -> None:
    """A warm viewer works out the latest state's first reads on a thread
    of its own, once a revision: the page's load and its read again after a
    filing find them kept, the same as worked out on request."""
    _, paper, store, state = served
    viewer = Viewer(store, paper, warm=True)
    thread = viewer.warm()
    assert thread is not None
    thread.join()
    assert viewer.warm() is None
    routes = [f"/api/state/{state.id}/{read}" for read in WARMED]
    assert all(
        ((tuple(r.strip("/").split("/")), ())) in viewer._answers for r in routes[:2]
    )
    cold = Viewer(store, paper)
    for route in routes:
        assert viewer.answer(route, {}) == cold.answer(route, {}), route

    cli(
        capsys,
        *("find", str(paper), "--aspect", "presentation", "--on", "we-fix-a-field"),
        *("--severity", "3", "--problem", "a problem filed while warm"),
    )
    get_now = viewer.answer("/api/now", {})
    assert json.loads(get_now)["revision"] == store.revision()
    assert viewer.warm() is None  # the poll began this revision's warming
    assert viewer.warm_thread is not None
    viewer.warm_thread.join()
    opened = viewer.answer(routes[1], {})
    assert b"a problem filed while warm" in opened
    assert opened == Viewer(store, paper).answer(routes[1], {})


@pytest.mark.usefixtures("develop_mode")
def test_the_page_s_files_asked_at_once_are_all_served(tmp_path, capsys) -> None:
    """The page asks for some twenty files at once. Connections made before
    the server accepts any (it is busy with a read) wait their turn: none
    is reset."""
    paper, store = built_and_updated(tmp_path, capsys)
    server = serve(store, paper)
    host, port = server.server_address[:2]
    asked = []
    try:
        for _ in range(40):
            connection = socket.create_connection((host, port), timeout=10)
            connection.sendall(
                b"GET /src/app.js HTTP/1.1\r\n"
                + f"Host: {host}:{port}\r\n".encode()
                + b"Connection: close\r\n\r\n"
            )
            asked.append(connection)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        for connection in asked:
            with connection:
                assert connection.makefile("rb").readline().startswith(b"HTTP/1.0 200")
    finally:
        server.shutdown()
        server.server_close()
