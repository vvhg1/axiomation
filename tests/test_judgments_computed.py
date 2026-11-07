"""`computed`: the reading of a script a block uses. Its
subject the block and the code file, its basis the block and its uses edge
to the file with the file's blob; a changed script lapses it, and a `true`
whose basis holds the file; a finding on a file's lines lapses on its
blob; a claim, or a justification, resting on a code file is established
only with a held `verified` `computed` there, and an about edge, a figure
or a path the paper does not ship asks nothing. Synthetic kept states of a
one-line paper (`test_judgments`' graph), its scripts kept beside it."""

from __future__ import annotations

from typing import Any

import pytest

import record_file
from axiomation import judgments, versions
from axiomation.computations import Ran
from axiomation.established import Missing, established
from axiomation.evidence import RunStamp, RunStatus, output_hash
from axiomation.exchange import dumps
from axiomation.judgments import (
    CauseKind,
    JudgmentError,
    StatusKind,
    anchored,
    find,
    judge,
    readings,
    seen,
    subject,
)
from axiomation.marks import open_marks
from axiomation.records import (
    AnchorKind,
    Aspect,
    BasisKind,
    Depth,
    RecordKind,
    RecordStore,
    ValuePair,
    Verdict,
)
from axiomation.versions import Command
from test_judgments import AGENT, EDGES, PERSON, TEXT, edge, graph
from test_versions import V1

SCRIPT = b'"""Bounds f."""\nprint("f is bounded")\n'
SCRIPT_KEY = "checks/bound.py"


def file_edge(source: str, kind: str, key: str) -> dict[str, Any]:
    return {
        "id": f"{source}>{kind}>{key}",
        "source": source,
        "kind": kind,
        "target": {"file": key},
        "evidence": [{"text": "f is bounded"}],
    }


def kept(
    store: RecordStore,
    payload: dict[str, Any],
    files: dict[str, bytes] | None = None,
) -> versions.State:
    """A build's state of `TEXT`, its files the paper's and `files`."""
    state, _ = versions.record(
        store,
        {"p.md": TEXT.encode(), SCRIPT_KEY: SCRIPT, **(files or {})},
        V1,
        "p.md",
        dumps(payload).encode(),
        Command.BUILD,
    )
    return state


def standing(payload: dict[str, Any], status: str) -> dict[str, Any]:
    for s in payload["standings"]:
        s["status"] = status
    return payload


def verified(store, state, on: judgments.Subject, author=PERSON):
    """A held `verified` `computed`, on a run of the kept script that printed
    the block's words (`test_runs` runs scripts for real)."""
    stdout = "f is bounded\n"
    stamp = RunStamp(
        SCRIPT_KEY,
        state.files[SCRIPT_KEY],
        "python",
        (),
        "/paper",
        RunStatus.RAN,
        output_hash(stdout),
    )
    return judge(
        store,
        state,
        Aspect.COMPUTED,
        on,
        Verdict.VERIFIED,
        Depth.COMPUTATIONAL,
        "ran it: it prints what the block says",
        author,
        ran=Ran(stamp, stdout),
        values=[ValuePair("f is bounded", "f is bounded")],
        code=['print("f is bounded")'],
    )


def reason_of(store, state, block: str = "t"):
    node = state.node_of(block)
    found = established(readings(store, state), open_marks(store, state))
    (one,) = [e for e in found if e.claim.node == node]
    return one.reason


# --- the subject and its basis ---------------------------------------------------


def test_a_computed_subject_is_a_block_and_the_code_file_it_uses(tmp_path) -> None:
    store = RecordStore(tmp_path / "store")
    state = kept(store, graph(edges=[*EDGES, file_edge("p", "uses", SCRIPT_KEY)]))
    at = seen(store, state)

    on = subject(at, Aspect.COMPUTED, "p")

    assert on == judgments.Subject(state.node_of("p"), file=SCRIPT_KEY)
    for refused, kwargs in (
        ("uses no code file", {"on": "t"}),
        ("not file:other.py", {"on": "p", "file": "other.py"}),
        ("name the printed values", {"on": "p", "span": "f is bounded"}),
    ):
        with pytest.raises(JudgmentError, match=refused):
            subject(at, Aspect.COMPUTED, **kwargs)
    j = judge(
        store, state, Aspect.COMPUTED, on, Verdict.INCONCLUSIVE, Depth.INFORMAL, "read"
    ).records.judgments[0]
    assert {(e.kind, e.name) for e in j.basis} == {
        (BasisKind.NODE, state.node_of("p")),
        (BasisKind.EDGE, state.node_of("p")),
        (BasisKind.FILE, SCRIPT_KEY),
    }
    (script,) = [e for e in j.basis if e.kind is BasisKind.FILE]
    assert script.text == state.files[SCRIPT_KEY]


def test_a_computed_finding_on_a_block_rests_on_its_code_files_alone(
    tmp_path,
) -> None:
    """Its basis the block and its uses edges onto code files, never its
    uses edges onto blocks (the methods session files these)."""
    store = RecordStore(tmp_path / "store")
    state = kept(store, graph(edges=[*EDGES, file_edge("p", "uses", SCRIPT_KEY)]))
    at = seen(store, state)

    f = find(
        store, state, Aspect.COMPUTED, [anchored(at, AnchorKind.BLOCK, "p")], 2, "x"
    ).records.findings[0]

    assert sorted((e.kind.value, e.name) for e in f.basis) == sorted(
        [
            (BasisKind.NODE.value, state.node_of("p")),
            (BasisKind.EDGE.value, state.node_of("p")),
            (BasisKind.FILE.value, SCRIPT_KEY),
        ]
    )


def test_a_verified_computed_without_a_run_is_refused(tmp_path) -> None:
    store = RecordStore(tmp_path / "store")
    state = kept(store, graph(edges=[*EDGES, file_edge("p", "uses", SCRIPT_KEY)]))
    on = subject(seen(store, state), Aspect.COMPUTED, "p")
    with pytest.raises(JudgmentError, match="names the run it read"):
        judge(store, state, Aspect.COMPUTED, on, Verdict.VERIFIED, Depth.INFORMAL, "x")


def test_the_state_offers_a_computed_subject_per_code_file_used(tmp_path) -> None:
    store = RecordStore(tmp_path / "store")
    edges = [
        *EDGES,
        file_edge("p", "uses", SCRIPT_KEY),
        file_edge("r", "about", SCRIPT_KEY),
        file_edge("t", "uses", "fig.pdf"),
        file_edge("d", "uses", "computations/unshipped.py"),
    ]
    state = kept(store, graph(edges=edges), {"fig.pdf": b"%PDF"})

    offered = [
        on
        for aspect, on in judgments.offered(seen(store, state))
        if aspect is Aspect.COMPUTED
    ]

    assert offered == [judgments.Subject(state.node_of("p"), file=SCRIPT_KEY)]


# --- the lapse ---------------------------------------------------------------------


def test_a_changed_script_lapses_computed_and_a_true_that_rests_on_it(tmp_path) -> None:
    store = RecordStore(tmp_path / "store")
    payload = graph(edges=[*EDGES, file_edge("t", "uses", SCRIPT_KEY)])
    first = kept(store, payload, {"checks/other.py": b"print(1)\n"})
    at = seen(store, first)
    judge(
        store,
        first,
        Aspect.COMPUTED,
        subject(at, Aspect.COMPUTED, "t"),
        Verdict.INCONCLUSIVE,
        Depth.INFORMAL,
        "read the code",
        AGENT,
    )
    judge(
        store,
        first,
        Aspect.TRUE,
        subject(at, Aspect.TRUE, "t"),
        Verdict.VERIFIED,
        Depth.INFORMAL,
        "read it",
        AGENT,
    )
    other = kept(store, payload, {"checks/other.py": b"print(2)\n"})
    assert other.parent == first.id
    held = {s.aspect: s.status for s in readings(store, other).statuses}
    assert held[Aspect.COMPUTED] is StatusKind.HELD, "another script lapses nothing"

    second = kept(
        store,
        payload,
        {"checks/other.py": b"print(2)\n", SCRIPT_KEY: SCRIPT + b"# edited\n"},
    )

    found = {s.aspect: s for s in readings(store, second).statuses}
    for aspect in (Aspect.COMPUTED, Aspect.TRUE):
        assert found[aspect].status is StatusKind.LAPSED
        cause = found[aspect].cause
        assert (
            cause.kind is CauseKind.TEXT and cause.why == f"file {SCRIPT_KEY} changed"
        )


def test_a_script_added_lapses_nothing(tmp_path) -> None:
    """A judgment's basis names the code files it cites, each by its blob:
    a script the paper newly ships is in none."""
    store = RecordStore(tmp_path / "store")
    payload = graph(edges=[*EDGES, file_edge("t", "uses", SCRIPT_KEY)])
    first = kept(store, payload)
    judge(
        store,
        first,
        Aspect.COMPUTED,
        subject(seen(store, first), Aspect.COMPUTED, "t"),
        Verdict.INCONCLUSIVE,
        Depth.INFORMAL,
        "read the code",
        AGENT,
    )

    added = kept(store, payload, {"checks/new.py": b"print(3)\n"})

    assert added.parent == first.id and added.version == first.version
    held = {s.aspect: s.status for s in readings(store, added).statuses}
    assert held[Aspect.COMPUTED] is StatusKind.HELD


def test_a_finding_on_a_script_s_lines_lapses_on_its_blob(tmp_path) -> None:
    store = RecordStore(tmp_path / "store")
    payload = graph(edges=[*EDGES, file_edge("t", "uses", SCRIPT_KEY)])
    first = kept(store, payload)
    at = seen(store, first)
    with pytest.raises(JudgmentError, match="found 0 times"):
        anchored(
            at, AnchorKind.FILE, words="nowhere", text=SCRIPT.decode(), file=SCRIPT_KEY
        )
    with pytest.raises(JudgmentError, match="keeps"):
        anchored(at, AnchorKind.FILE, words="print", text="", file="gone.py")
    anchor = anchored(
        at,
        AnchorKind.FILE,
        words='print("f is bounded")',
        text=SCRIPT.decode(),
        file=SCRIPT_KEY,
    )
    f = find(
        store, first, Aspect.COMPUTED, [anchor], 3, "the script echoes the text"
    ).records.findings[0]
    assert [(e.kind, e.name) for e in f.basis] == [(BasisKind.FILE, SCRIPT_KEY)]
    (status,) = [
        s for s in readings(store, first).statuses if s.aspect is Aspect.COMPUTED
    ]
    assert status.findings, "a file anchor bears on the computed subject of its file"

    second = kept(store, payload, {SCRIPT_KEY: SCRIPT.replace(b"bounded", b"finite")})

    (read,) = readings(store, second).findings
    assert not read.live
    assert read.walked.causes[0].why == f"file {SCRIPT_KEY} changed"


# --- established -----------------------------------------------------------------


def test_a_claim_resting_on_a_script_needs_a_held_verified_computed(tmp_path) -> None:
    store = RecordStore(tmp_path / "store")
    payload = standing(graph(edges=[file_edge("t", "uses", SCRIPT_KEY)]), "self")
    state = kept(store, payload)
    judge(
        store,
        state,
        Aspect.TRUE,
        subject(seen(store, state), Aspect.TRUE, "t"),
        Verdict.VERIFIED,
        Depth.INFORMAL,
        "read it",
    )

    reason = reason_of(store, state)
    assert reason.kind is Missing.UNJUDGED
    assert f"`computed` of file:{SCRIPT_KEY}" in reason.why

    on = subject(seen(store, state), Aspect.COMPUTED, "t")
    judge(store, state, Aspect.COMPUTED, on, Verdict.INCONCLUSIVE, Depth.INFORMAL, "?")
    assert reason_of(store, state).kind is Missing.INCONCLUSIVE

    verified(store, state, on)
    assert reason_of(store, state) is None


def test_a_justification_resting_on_a_script_needs_it_too(tmp_path) -> None:
    store = RecordStore(tmp_path / "store")
    payload = graph(edges=[*EDGES, file_edge("p", "uses", SCRIPT_KEY)])
    state = kept(store, payload)
    at = seen(store, state)
    judge(
        store,
        state,
        Aspect.VALID,
        subject(at, Aspect.VALID, "p"),
        Verdict.VERIFIED,
        Depth.INFORMAL,
        "the proof holds",
    )

    reason = reason_of(store, state)
    assert reason.kind is Missing.UNJUDGED and reason.node == state.node_of("p")
    assert SCRIPT_KEY in reason.why

    verified(store, state, subject(at, Aspect.COMPUTED, "p"))
    assert reason_of(store, state) is None


@pytest.mark.parametrize(
    ("kind", "key", "files"),
    [
        ("about", SCRIPT_KEY, {}),
        ("uses", "fig.pdf", {"fig.pdf": b"%PDF"}),
        ("uses", "computations/unshipped.py", {}),
    ],
)
def test_an_about_edge_a_figure_or_an_unshipped_path_asks_nothing(
    tmp_path, kind, key, files
) -> None:
    store = RecordStore(tmp_path / "store")
    payload = standing(graph(edges=[file_edge("t", kind, key)]), "self")
    state = kept(store, payload, files)
    judge(
        store,
        state,
        Aspect.TRUE,
        subject(seen(store, state), Aspect.TRUE, "t"),
        Verdict.VERIFIED,
        Depth.INFORMAL,
        "read it",
    )
    assert reason_of(store, state) is None


def test_a_computed_judgment_and_a_file_anchor_read_back(tmp_path) -> None:
    store = RecordStore(tmp_path / "store")
    state = kept(
        store,
        graph(
            edges=[*EDGES, edge("p", "uses", "d"), file_edge("p", "uses", SCRIPT_KEY)]
        ),
    )
    at = seen(store, state)
    judge(
        store,
        state,
        Aspect.COMPUTED,
        subject(at, Aspect.COMPUTED, "p"),
        Verdict.NOT_SHOWN,
        Depth.INFORMAL,
        "prints a literal",
    )
    anchor = anchored(
        at, AnchorKind.FILE, words="print", text=SCRIPT.decode(), file=SCRIPT_KEY
    )
    find(store, state, Aspect.COMPUTED, [anchor], 2, "an echo")

    again = RecordStore(store.directory).judgment_records()

    assert again.judgments[0].subject.file == SCRIPT_KEY
    assert again.findings[0].anchors[0] == anchor
    written = record_file.written(store.directory, RecordKind.JUDGMENTS)
    assert written[0]["subject"]["file"] == SCRIPT_KEY


def test_the_code_rule_is_a_filing_s_check_never_a_read_s(tmp_path) -> None:
    """The rule is a filing's and a session's check, never a read's: a
    stored `verified` that quotes no code reads, and holds."""
    import shutil

    store = RecordStore(tmp_path / "store")
    state = kept(store, graph(edges=[*EDGES, file_edge("p", "uses", SCRIPT_KEY)]))
    verified(store, state, subject(seen(store, state), Aspect.COMPUTED, "p"))
    old = tmp_path / "old"
    shutil.copytree(store.directory, old)
    record_file.rewrite_records(
        old,
        RecordKind.JUDGMENTS,
        lambda r: {k: v for k, v in r.items() if k != "code"},
    )

    (j,) = RecordStore(old).judgment_records().judgments
    assert j.code == ()
    (status,) = [
        s
        for s in readings(RecordStore(old), state).statuses
        if s.aspect is Aspect.COMPUTED
    ]
    assert status.status is StatusKind.HELD
