"""The support search (`docs/model.md` § 8): of each claim standing
`unsupported`, whether the paper argues it after all, before anyone rules
on it. A queue (docs/architecture.md § 2): the worker's step answers each
claim, the settling step confirms its line or answers the claim itself.
The lines settled are the search's edges edit, which stands the claims
argued; a claim the last step read unargued is filed as a search, which
the argument review's rule line needs.

On `test_update`'s chain kept as an update's state, where
`next-the-bound-is` stands unsupported; and `test_rulings`' four blocks
for the lapse. No model is called.
"""

from __future__ import annotations

import json
from collections.abc import Callable

import pytest

from axiomation import edits, queries, versions
from axiomation import support_search as ss
from axiomation.changes import Layer
from axiomation.cli import main
from axiomation.judgments import Author, file_records
from axiomation.queries import View
from axiomation.questions import load_template
from axiomation.queue import Run, Served, Spool, opened
from axiomation.records import JudgmentRecords, Searched, Subject
from axiomation.resolver import Resolver
from axiomation.runner import Runner
from axiomation.runner.queued import SESSION_KIND, Price, run_queue
from runner.fakes import FakeQueueHost, working_through
from test_marks import kept_update
from test_update import FLAT, SHARP, QueueThen

SHARP_CLAIM = "Next, the bound is sharp."
PROOF_BLOCK = "by-definition-def:gain-the"
#: the proof's block argues the sharpness: a line the edges missed
ARGUING = f'→ justifies {SHARP} · "Hence the output is bounded."'
#: the typed answer that gives it
ARGUED = {
    "kind": "justifies",
    "by": PROOF_BLOCK,
    "quote": "Hence the output is bounded.",
    "why": "the proof's last step yields it",
}
UNARGUED = {"closest": PROOF_BLOCK, "why": "it bounds the output, not sharpness"}


def asked_at(store, state, named=(), every=False) -> list[ss.Claim]:
    return ss.asked_claims(View(store, state), named, every)


def answering(tool: str, args: dict) -> Callable[[str], tuple[str, dict]]:
    """A step's answer to each task served: `tool` with `args`, on the
    task's id."""
    return lambda served: (tool, {"task": served.split()[1], **args})


def searched_by(
    store,
    state,
    *answers: Callable[[str], tuple[str, dict]],
    cost: float = 0.01,
    every: bool = False,
) -> tuple[ss.Found, list[FakeQueueHost]]:
    """The search on `state`, a step for each of `answers` (the worker's,
    then the settling step's), each a fake host's session; then its
    edit given, as `support --ask` gives it."""
    hosts = [FakeQueueHost(working_through(a), cost=cost) for a in answers]
    run = Run(
        store,
        state,
        ss.FACTORY,
        {"settle": len(answers) > 1, "model": "fake/queue", "every": every},
    )
    runner = Runner(hosts[0], Resolver(store), 1.0, FLAT)
    found = ss.ask(run, "fake/queue", runner, hosts, wait_seconds=0.2)
    return found, hosts


def standing(store, state, words: str) -> str:
    export = json.loads(versions.kept_export(store, state))
    (found,) = [s for s in export["standings"] if s["claim"]["text"] == words]
    return found["status"]


# --- the worker alone -----------------------------------------------------------


def test_an_argued_answer_is_the_search_s_edit_and_stands_the_claim_argued(
    tmp_path,
) -> None:
    """The proof's justifies line onto the claim is given as the search's
    edges edit, an agent's, a new state where the claim stands justified;
    nothing is left to search there."""
    v, state = kept_update(tmp_path)
    found, _ = searched_by(v.store, state, answering("answer_argued", ARGUED))
    assert found.complete and found.outcome.problems == ()
    (settled,) = found.settled
    assert (settled.claim.words, settled.argued, settled.why) == (
        SHARP_CLAIM,
        True,
        ARGUED["why"],
    )
    assert found.outcome.state is not None and found.outcome.edit is not None
    edit = v.store.authoritative()[found.outcome.edit]
    assert (edit.host, edit.model) == (edits.AGENT, "fake/queue")
    assert edit.reason == f"{SHARP}: {ARGUED['why']}"
    assert standing(v.store, found.outcome.state, SHARP_CLAIM) == "justified"
    assert asked_at(v.store, found.outcome.state) == []
    assert v.store.judgment_records().searches == ()


def test_an_unargued_answer_is_filed_with_it_and_not_asked_again(tmp_path) -> None:
    """Read unargued, the claim is filed as a search with its task's
    answer and asked no more, unless every claim is asked; no edit."""
    v, state = kept_update(tmp_path)
    found, _ = searched_by(v.store, state, answering("answer_unargued", UNARGUED))
    assert found.complete and found.outcome.edit is None and found.outcome.state is None
    (filed,) = v.store.judgment_records().searches
    assert (filed.state, filed.subject.words, filed.reason) == (
        state.id,
        SHARP_CLAIM,
        UNARGUED["why"],
    )
    assert v.store.authoritative()[filed.answer].kind == ss.WORKER_KIND
    (read,) = ss.searched(v.store, state).values()
    assert read.author.host != "person"
    assert asked_at(v.store, state) == []
    assert [c.words for c in asked_at(v.store, state, every=True)] == [SHARP_CLAIM]


def test_a_complete_search_replays_from_the_store_to_its_state(tmp_path) -> None:
    """Asked of no host, the run replays what the store holds: the same
    claims settled, the same edit and state, nothing given again."""
    v, state = kept_update(tmp_path)
    found, _ = searched_by(v.store, state, answering("answer_argued", ARGUED))
    run = Run(v.store, state, ss.FACTORY, {"settle": False, "model": "fake/queue"})
    again = ss.ask(run, "fake/queue")
    assert again.outcome.report is None and again.complete
    assert [(s.claim.n, s.argued) for s in again.settled] == [(1, True)]
    assert (again.outcome.edit, again.outcome.state) == (
        found.outcome.edit,
        found.outcome.state,
    )
    assert len([a for a in v.store.answers() if a.key == found.outcome.edit]) == 1


def test_a_run_with_claims_open_gives_nothing(tmp_path) -> None:
    v, state = kept_update(tmp_path)
    run = Run(v.store, state, ss.FACTORY, {"model": "fake/queue"})
    found = ss.ask(run, "fake/queue")
    assert (found.outcome.open, found.complete, found.outcome.edit) == (1, False, None)
    assert versions.latest(v.store).id == state.id


# --- the gate ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("lines", "refused"),
    [
        ((), "holds no line"),
        ((f"⟦{SHARP}⟧", f'+ ⊢ justified · "{SHARP_CLAIM}"'), "no standing"),
        ((f"⟦{PROOF_BLOCK}⟧", f"= {ARGUING}"), "keeps nothing"),
        (
            (f"⟦{SHARP}⟧", f'- → uses {PROOF_BLOCK} · "{SHARP_CLAIM}"'),
            "no standing",
        ),
        (
            (f"⟦{SHARP}⟧", f'+ → justifies {SHARP} · "Next, the bound"'),
            "comes from another block",
        ),
        (
            (f"⟦{PROOF_BLOCK}⟧", '+ → justifies thm:bound · "Hence the output"'),
            "argues no claim of",
        ),
        (
            ('+ def:gain [r]: "The gain is the ratio"', f"⟦{PROOF_BLOCK}⟧")
            + (f"+ {ARGUING}",),
            "adds no part",
        ),
        # the claim quoted as its own step argues nothing
        ((f"⟦{SHARP}⟧", '+ → self · "Next, the bound is sharp"'), "the claim itself"),
    ],
)
def test_a_line_that_is_no_search_s_is_refused(tmp_path, lines, refused) -> None:
    v, state = kept_update(tmp_path)
    (claim,) = asked_at(v.store, state)
    with edits.opened(v.store, state) as kept:
        problems = ss.line_problems(
            kept,
            claim,
            "\n".join(lines),
            edits.agent("m", "s"),
            edits.shown(kept, Layer.EDGES),
        )
    assert any(refused in p for p in problems), problems


def test_a_search_lapses_only_when_its_claim_s_words_change(tmp_path) -> None:
    from test_rulings import _four, _updated

    def search(store, first) -> None:
        (on,) = ss.standing_unsupported(View(store, first))
        file_records(
            store,
            "search",
            JudgmentRecords(searches=(Searched("", None, first.id, on, "none"),)),
            Author(),
        )

    store, first = _four(tmp_path)
    search(store, first)
    kept = _updated(store, first, "The theorem is sharp.")
    assert len(ss.searched(store, kept)) == 1
    assert ss.asked_claims(View(store, kept)) == []
    store2, first2 = _four(tmp_path / "two")
    search(store2, first2)
    reworded = _updated(store2, first2, "The theorem is tight.")
    assert ss.searched(store2, reworded) == {}
    (again,) = ss.asked_claims(View(store2, reworded))
    assert again.words == "The theorem is tight."


#: `t`'s claim `a` stands unsupported; `j` argues `b` in a sketch; `s`
#: restates `a` by a line a reader said restates none.
GIVE_BACK = "\n".join(
    [
        "⟦t claim⟧ main.tex:1:1 (prose)",
        "A holds. B holds.",
        '⊢ unsupported [a] · "A holds."',
        '⊢ justified [b] · "B holds."',
        "⟦done t⟧",
        "⟦j justification⟧ main.tex:2:1 (prose)",
        "Hence B, and A as well.",
        '→ justifies t [b] sketch · "Hence B"',
        "⟦done j⟧",
        "⟦s claim⟧ main.tex:3:1 (prose)",
        "In short, A holds.",
        '→ about [-] t [a] · "In short"',
        '⊢ unsupported · "In short, A holds."',
        "⟦done s⟧",
    ]
)


@pytest.mark.parametrize(
    ("block", "words", "lines", "refused"),
    [
        (
            "t",
            "A holds.",
            [
                ("j", "-", '→ justifies t [b] sketch · "Hence B"'),
                ("j", "+", '→ justifies t [a, b] · "Hence B"'),
            ],
            "its flag changed",
        ),
        (
            "t",
            "A holds.",
            [
                ("j", "-", '→ justifies t [b] sketch · "Hence B"'),
                ("j", "+", '→ justifies t [a] sketch · "Hence B"'),
            ],
            "names less than the line it gives back named",
        ),
        (
            "s",
            "In short, A holds.",
            [
                ("s", "-", '→ about [-] t [a] · "In short"'),
                ("s", "+", '→ about t [a] · "In short"'),
            ],
            "restates none ([-]), a reader's word",
        ),
    ],
)
def test_a_line_given_back_changes_its_brackets_alone(
    block, words, lines, refused
) -> None:
    from axiomation.questions import edge_sheet as es

    claim = ss.Claim(1, Subject("n", None, words), block, None, None)
    problems = ss.arguing_problems(es.parse(GIVE_BACK), claim, lines)
    assert any(refused in p for p in problems), problems


#: `p` restates `t`'s `x` by its claim `a`; its claim `b` stands unsupported
RESTATING = "\n".join(
    [
        "⟦t claim⟧ main.tex:1:1 (prose)",
        "X holds. Y holds.",
        '⊢ justified [x] · "X holds."',
        '⊢ justified [y] · "Y holds."',
        "⟦done t⟧",
        "⟦p claim⟧ main.tex:2:1 (prose)",
        "In short, X holds. Also Y holds.",
        '→ about [a] t [x] · "In short, X holds."',
        '⊢ restated [a] · "In short, X holds."',
        '⊢ unsupported [b] · "Also Y holds."',
        "⟦done p⟧",
    ]
)


def test_a_claim_restating_the_same_target_shares_its_block_s_about_line() -> None:
    """`p` already restates `t`; its claim `b`, restating `t`'s `y`, is
    that one about line given back naming `b` and `y` too (one about edge
    per source and target), which the gate takes."""
    from axiomation.questions import edge_sheet as es

    given = ss._given_on(RESTATING, "p", '→ about [b] t [y] · "Also Y holds."', "b")
    assert given == "\n".join(
        [
            "⟦p⟧",
            '- → about [a] t [x] · "In short, X holds."',
            '+ → about [a, b] t [x, y] · "In short, X holds."',
        ]
    )
    claim = ss.Claim(1, Subject("n", None, "Also Y holds."), "p", "b", None)
    fragment = edits.parse_fragment(given)
    assert ss.arguing_problems(es.parse(RESTATING), claim, fragment.lines) == []


def test_a_justifies_or_self_line_stands_beside_its_block_s_lines() -> None:
    """A justifies line naming other claims of its target, and a self
    line, are lines of their own: each claim's completeness is its own
    line's (`docs/model.md` § 3)."""
    justifies = '→ justifies t [a] · "and A as well"'
    assert ss._given_on(GIVE_BACK, "j", justifies, "a") == f"⟦j⟧\n+ {justifies}"
    self_line = '→ self [b] · "Also Y holds."'
    assert ss._given_on(RESTATING, "p", self_line, "b") == f"⟦p⟧\n+ {self_line}"


# --- the worker's step ------------------------------------------------------------


def served_step(store, state, tmp_path, step=0, **options) -> Served:
    run = Run(store, state, ss.FACTORY, options)
    workflow = opened(run).__enter__()
    spool = Spool(tmp_path / "spool.jsonl")
    return Served(workflow, {}, spool, step, f"s{step}", wait=0.05, poll=0.01)


def test_the_worker_is_served_the_claim_its_block_and_pointers(tmp_path) -> None:
    v, state = kept_update(tmp_path)
    s = served_step(v.store, state, tmp_path)
    served = s.next()
    head, ask, claim, block, *_ = served.splitlines()
    assert (head, ask, claim) == (
        "task c1 (1 of 1)",
        "is this claim argued?",
        f'claim: "{SHARP_CLAIM}"',
    )
    assert block.startswith(f"block ⟦{SHARP}⟧ ")
    assert served.splitlines()[-1].startswith("pointers: ⟦")
    names = [t.name for t in s.tools]
    assert names[:3] == ["next", "answer_argued", "answer_unargued"]


def test_the_worker_s_argued_answer_is_a_line_by_code_checked_at_once(
    tmp_path,
) -> None:
    """The proof's justifies line, written by code from the typed answer,
    passes the search's gate; settled, it stands the claim argued in the
    working state, and the queue is empty."""
    v, state = kept_update(tmp_path)
    s = served_step(v.store, state, tmp_path)
    s.next()
    text, refused = s.call("answer_argued", {"task": "c1", **ARGUED})
    assert not refused, text
    assert text.endswith("the queue is empty: stop now and say done.")
    assert ARGUING in s.working.sheet
    # the server spools; only the runner files
    assert not [a for a in v.store.answers() if a.kind == ss.WORKER_KIND]


@pytest.mark.parametrize(
    ("args", "why"),
    [
        (
            {"kind": "justifies", "by": SHARP, "quote": "q", "why": "w"},
            "a justifies line comes from another block",
        ),
        ({"kind": "uses", "by": PROOF_BLOCK, "quote": "q", "why": "w"}, "kind 'uses'"),
        ({"kind": "justifies", "quote": "q", "why": "w"}, "names its block (by)"),
        (
            {"kind": "justifies", "by": "nowhere", "quote": "q", "why": "w"},
            "by 'nowhere'",
        ),
        ({"kind": "self", "quote": "", "why": "w"}, "give the quote"),
        ({"kind": "self", "quote": "q", "why": " "}, "give the reason (why)"),
        ({"kind": "self", "quote": SHARP_CLAIM, "why": "w"}, "the claim itself"),
    ],
)
def test_the_worker_s_argued_answer_is_refused_with_why(tmp_path, args, why) -> None:
    v, state = kept_update(tmp_path)
    s = served_step(v.store, state, tmp_path)
    s.next()
    text, refused = s.call("answer_argued", {"task": "c1", **args})
    assert refused and why in text
    assert s.spool.entries() == []


def test_the_worker_s_unargued_answer_names_the_closest_block(tmp_path) -> None:
    v, state = kept_update(tmp_path)
    s = served_step(v.store, state, tmp_path)
    s.next()
    text, refused = s.call("answer_unargued", {"task": "c1", "why": "no step"})
    assert refused and "name the closest block you rejected" in text
    text, refused = s.call(
        "answer_unargued", {"task": "c1", "closest": "nowhere", "why": "no step"}
    )
    assert refused and text.startswith("refused, nothing filed")
    text, refused = s.call("answer_unargued", {"task": "c1", **UNARGUED})
    assert not refused and text.endswith("stop now and say done.")


def test_the_worker_s_run_files_its_typed_answer_and_replays(tmp_path) -> None:
    v, state = kept_update(tmp_path)
    host = FakeQueueHost(working_through(answering("answer_argued", ARGUED)))
    run = Run(v.store, state, ss.FACTORY, {})
    report = run_queue(Runner(host, Resolver(v.store), 1.0, FLAT), run, [host])
    assert report.complete and report.settled == 1
    (filed,) = [a for a in v.store.answers() if a.kind == ss.WORKER_KIND]
    assert filed.call == {"tool": "answer_argued", "args": {"task": "c1", **ARGUED}}
    assert (filed.template.name, filed.state, filed.order) == (
        ss.WORKER_TEMPLATE,
        state.id,
        1,
    )
    assert host.sessions[0].instructions == load_template(ss.WORKER_TEMPLATE).text
    again = run_queue(Runner(host, Resolver(v.store), 1.0, FLAT), run)
    assert again.complete and again.settled == 0


def test_the_worker_s_server_runs_as_the_host_starts_it(tmp_path) -> None:
    """The tool server as its own process, isolated Python on the package,
    answering JSON-RPC a line at a time: it lists the worker's tools and
    serves its task."""
    import os
    import subprocess

    from axiomation.queue import tool_server

    v, state = kept_update(tmp_path)
    run = Run(v.store, state, ss.FACTORY, {})
    server = tool_server(run, 0, "s1", run.directory / "spool-1.jsonl", 1.0)
    messages = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "next", "arguments": {}},
        },
    ]
    done = subprocess.run(
        server.argv,
        input="".join(json.dumps(m) + "\n" for m in messages),
        capture_output=True,
        text=True,
        env={**os.environ, **server.env},
        timeout=120,
        check=True,
    )
    replies = [json.loads(line) for line in done.stdout.splitlines()]
    assert [r["id"] for r in replies] == [1, 2, 3]
    tools = [t["name"] for t in replies[1]["result"]["tools"]]
    assert tools[:3] == ["next", "answer_argued", "answer_unargued"]
    text = replies[2]["result"]["content"][0]["text"]
    assert text.startswith("task c1 (1 of 1)\nis this claim argued?\n")


# --- the settling step ------------------------------------------------------------


def test_the_settling_step_is_served_the_line_and_what_it_names_whole(
    tmp_path,
) -> None:
    """The worker's line is handed on: the settling step is served the
    claim, the line as given and the proof it names read whole, never the
    worker's reason; its confirm settles the line."""
    v, state = kept_update(tmp_path)
    worker = served_step(v.store, state, tmp_path, 0, settle=True)
    settler = served_step(v.store, state, tmp_path, 1, settle=True)
    worker.call("answer_argued", {"task": "c1", **ARGUED})
    served = settler.next()
    assert served.startswith("task c1 (1 of 1)\ndoes this line argue the claim?\n")
    assert f"proposed line:\n⟦{PROOF_BLOCK}⟧\n+ {ARGUING}\n" in served
    assert "Hence the output is bounded." in served.split("proposed line:")[1]
    assert ARGUED["why"] not in served
    assert ARGUING not in settler.working.sheet
    names = [t.name for t in settler.tools]
    assert names[:4] == ["next", "confirm", "answer_argued", "answer_unargued"]
    text, refused = settler.call("confirm", {"task": "c1", "why": "it yields it"})
    assert not refused and text.endswith("stop now and say done.")
    assert ARGUING in settler.working.sheet


def test_a_claim_with_no_line_is_searched_again_by_the_settling_step(
    tmp_path,
) -> None:
    """The worker read it unargued: the settling step is served the claim
    as a search, not told so, and cannot confirm; its own line settles."""
    v, state = kept_update(tmp_path)
    worker = served_step(v.store, state, tmp_path, 0, settle=True)
    settler = served_step(v.store, state, tmp_path, 1, settle=True)
    worker.call("answer_unargued", {"task": "c1", **UNARGUED})
    served = settler.next()
    assert served.startswith("task c1 (1 of 1)\nis this claim argued?\n")
    assert UNARGUED["why"] not in served
    text, refused = settler.call("confirm", {"task": "c1", "why": "w"})
    assert refused and "no line is proposed" in text
    text, refused = settler.call("answer_argued", {"task": "c1", **ARGUED})
    assert not refused, text
    assert ARGUING in settler.working.sheet


def test_both_steps_run_at_once_and_the_settling_step_s_word_is_filed(
    tmp_path,
) -> None:
    """In lockstep, each a session: the worker argues, the settling step
    rejects the line and reads the claim unargued; the search is filed
    with the settling step's answer, no edit is given, and each step's
    session is recorded with its cost."""
    v, state = kept_update(tmp_path)
    found, (worker, settler) = searched_by(
        v.store,
        state,
        answering("answer_argued", ARGUED),
        answering("answer_unargued", UNARGUED),
    )
    assert found.complete and found.outcome.edit is None
    (settled,) = found.settled
    assert (settled.argued, settled.why) == (False, UNARGUED["why"])
    (filed,) = v.store.judgment_records().searches
    assert v.store.authoritative()[filed.answer].kind == ss.SETTLER_KIND
    steps = (ss.WORKER_KIND, ss.SETTLER_KIND)
    assert [a.kind for a in v.store.answers() if a.kind in steps] == list(steps)
    sessions = [
        a
        for a in v.store.answers()
        if a.kind == SESSION_KIND
        and a.template.name in (ss.WORKER_TEMPLATE, ss.SETTLER_TEMPLATE)
    ]
    assert sorted(a.template.name for a in sessions) == [
        ss.SETTLER_TEMPLATE,
        ss.WORKER_TEMPLATE,
    ]
    assert settler.sessions[0].instructions == load_template(ss.SETTLER_TEMPLATE).text
    assert len(worker.sessions) == 1


def test_a_confirmed_line_is_the_search_s_edit(tmp_path) -> None:
    v, state = kept_update(tmp_path)
    found, _ = searched_by(
        v.store,
        state,
        answering("answer_argued", ARGUED),
        answering("confirm", {"why": "the proof's last line yields it"}),
    )
    assert found.outcome.state is not None
    assert [(s.argued, s.why) for s in found.settled] == [
        (True, "the proof's last line yields it")
    ]
    assert standing(v.store, found.outcome.state, SHARP_CLAIM) == "justified"
    assert v.store.judgment_records().searches == ()


# --- a task's price -----------------------------------------------------------------


def test_a_task_is_priced_by_what_the_store_s_sessions_cost(tmp_path) -> None:
    """The fitted default where the store holds no session of the
    step on that model, scaled by another model's input rate; then what
    the store's sessions cost over the tasks they gave."""
    v, state = kept_update(tmp_path)
    fitted = ss.FITTED[ss.WORKER_KIND]
    assert ss.price(v.store, ss.WORKER_KIND, fitted.model) == Price(
        fitted.start, fitted.task
    )
    sonnet = ss.price(v.store, ss.WORKER_KIND, "claude-sonnet-5-5")
    assert sonnet.start > fitted.start and sonnet.task > fitted.task
    with pytest.raises(ss.SearchError, match="no rates are known for nothing"):
        ss.price(v.store, ss.WORKER_KIND, "nothing")
    searched_by(v.store, state, answering("answer_unargued", UNARGUED), cost=0.04)
    assert ss.price(v.store, ss.WORKER_KIND, "fake/queue") == Price(0.0, 0.04, 1, 1)


# --- the command ----------------------------------------------------------------------


def test_support_lists_the_claims_and_its_dry_run_writes_the_briefs(
    tmp_path, capsys
) -> None:
    v, state = kept_update(tmp_path)
    args = [str(v.new.root), "--store", str(v.store.directory)]
    out = tmp_path / "dry"
    capsys.readouterr()
    assert main(["support", *args]) == 0
    printed = capsys.readouterr().out
    assert f"1 claims standing unsupported at state {state.id}, 1 not searched" in (
        printed
    )
    assert SHARP_CLAIM in printed
    assert main(["support", *args, "--ask", "--dry-run"]) == 0
    assert "expected $0.07: 1 claims" in capsys.readouterr().out
    assert not out.exists()
    assert main(["support", *args, "--prompts", str(out)]) == 1
    assert "--prompts goes with --ask" in capsys.readouterr().err
    assert main(["support", *args, "--ask", "--dry-run", "--prompts", str(out)]) == 0
    printed = capsys.readouterr().out
    head, expected, worker, settler = printed.splitlines()[:4]
    assert head.startswith("dry run, nothing asked or recorded: support --ask at")
    assert f"state {state.id}: 1 claims" in head
    # each step one session's start and a claim
    assert expected == "expected $0.07: 1 claims"
    assert worker == (
        f"  {ss.WORKER_KIND} on claude-haiku-5-5: $0.0010 its session's start, "
        "fitted on recorded runs; $0.0014 a claim, fitted on recorded runs"
    )
    assert settler.startswith(
        f"  {ss.SETTLER_KIND} on claude-opus-5-5: $0.0500 its session's start"
    )
    assert "$0.0200 a claim" in settler
    assert {p.name for p in out.iterdir()} == {
        f"{ss.WORKER_KIND}.md",
        f"{ss.SETTLER_KIND}.md",
        "tasks.md",
    }
    assert (out / "tasks.md").read_text().startswith("task c1\nis this claim argued?")
    assert v.store.judgment_records().searches == ()
    assert queries.View(v.store, versions.latest(v.store)).state.id == state.id


def test_support_ask_with_no_host_replays_what_the_store_holds(
    tmp_path, capsys
) -> None:
    """`--host none` asks nothing: with claims open it says so and ends not
    whole; once a run settled them, it rebuilds the same edit and state."""
    v, state = kept_update(tmp_path)
    args = [str(v.new.root), "--store", str(v.store.directory), "--state", state.id]
    ask = ["support", *args, "--ask", "--host", "none", "--profile", "standard"]
    capsys.readouterr()
    assert main(ask) == 0
    assert "1 claims open: nothing given yet; not asked" in capsys.readouterr().out
    worker, settler = (
        FakeQueueHost(working_through(a), model=m)
        for a, m in (
            (answering("answer_argued", ARGUED), "claude-haiku-5-5"),
            (answering("confirm", {"why": "it does"}), "claude-opus-5-5"),
        )
    )
    run = Run(
        v.store,
        state,
        ss.FACTORY,
        {
            "profile": "standard",
            "profiles": None,
            "blocks": [],
            "settle": True,
            "model": "claude-opus-5-5",
        },
    )
    found = ss.ask(
        run,
        "claude-opus-5-5",
        Runner(worker, Resolver(v.store), 1.0, FLAT),
        [worker, settler],
        wait_seconds=0.2,
    )
    assert found.outcome.state is not None
    capsys.readouterr()
    assert main(ask) == 0
    printed = capsys.readouterr().out
    assert f'argued: {SHARP} "{SHARP_CLAIM}" — it does' in printed
    assert (
        f"edges edit {found.outcome.edit[:12]}: state {found.outcome.state.id}"
        in printed
    )


# --- where it runs: at an update's end, after its reread ----------------------


def update_searching(tmp_path, monkeypatch, capsys, *flags: str):
    """`update` from the command line on the chain, its reread standing
    every owner and its search arguing the sharpness. The papers, the
    exit code, the summary, the host and the search's host."""
    from axiomation.commands import seating
    from runner.fakes import FakeHost
    from test_profiles import _profile
    from test_update import (
        CHAIN,
        CHAIN_LEGEND,
        EDGES_FILE,
        EDITED_SETTING,
        SETTING,
    )
    from test_update import (
        answering as answering_update,
    )
    from test_update import versions as two_papers
    from test_update_single import as_assumption

    v = two_papers(tmp_path, EDITED_SETTING, CHAIN, CHAIN_LEGEND)
    profiles = tmp_path / "profiles.toml"
    profiles.write_text(
        _profile(
            "p",
            "per_stage_update_above_recut_blocks = 1000\nmoved_delta_lines_max = 0\n",
        ),
        encoding="utf-8",
    )
    fake = FakeHost(
        lambda _: "",
        edits={
            EDGES_FILE: answering_update(
                {SETTING: ["Δ narrowed — a subfield"]}, as_assumption
            ),
        },
    )
    # one queue host for both: the reread's owners stand, the claim is argued
    stand = answering("stands", {"why": "its lines hold"})
    argue = answering("answer_argued", ARGUED)
    answers = QueueThen(
        v,
        fake.edits[EDGES_FILE],
        lambda served: (stand if "owner's lines" in served else argue)(served),
    )
    searching = FakeQueueHost(working_through(answers))
    monkeypatch.setattr(seating, "host_of", lambda agent, executable, tools=None: fake)
    monkeypatch.setattr(
        seating,
        "executables_of",
        lambda agents: {a.harness: "fake" for a in agents.values()},
    )
    monkeypatch.setattr(seating, "queue_hosts", lambda seated: [searching])
    capsys.readouterr()
    code = main(
        [
            "update",
            "--json",
            *(str(v.new.root), "--store", str(v.store.directory)),
            *("--profiles", str(profiles), "--profile", "p", "--rates", "3,15"),
            *("--budget", "10"),
            *flags,
        ]
    )
    out = capsys.readouterr().out
    assert "{" in out, code
    assert not answers.update.unserved(), answers.update.unserved()
    return v, code, json.loads(out[out.index("{") :]), fake, searching


def test_an_update_ends_with_the_search_after_its_reread(
    tmp_path, monkeypatch, capsys
) -> None:
    """The claim the update leaves standing unsupported is searched after
    the reread, in one step on the edges' agent where the profile names no
    search steps; the line found is the search's edit, the latest state,
    where the claim stands justified."""
    v, code, summary, fake, searching = update_searching(tmp_path, monkeypatch, capsys)
    assert code == 0, summary
    found = summary["support"]
    assert (found["claims"], found["argued"], found["unargued"]) == (1, 1, 0)
    latest = versions.latest(v.store)
    assert found["state"] == latest.id
    assert standing(v.store, latest, SHARP_CLAIM) == "justified"
    # the update's queue, then the reread's and the search's; the edges'
    # file host never
    assert fake.calls == 0 and len(searching.sessions) == 3
    assert [s.context_tokens for s in searching.sessions[1:]] == [100_000, 100_000]


def test_no_search_leaves_the_claim_for_a_later_search(
    tmp_path, monkeypatch, capsys
) -> None:
    v, code, summary, fake, searching = update_searching(
        tmp_path, monkeypatch, capsys, "--no-search"
    )
    assert code == 0 and "support" not in summary
    assert fake.calls == 0 and len(searching.sessions) == 2
    (left,) = asked_at(v.store, versions.latest(v.store))
    assert left.words == SHARP_CLAIM


def test_an_update_dry_run_prices_the_search_if_it_fires(tmp_path, capsys) -> None:
    from test_update import EDITED_SETTING, make_paper

    v, _ = kept_update(tmp_path)
    third = make_paper(
        tmp_path / "third",
        main=EDITED_SETTING.replace(
            "A second sentence of the setting, edited.",
            "A second sentence of the setting, edited again.",
        ),
    )
    args = ["update", "--json", str(third.root), "--store", str(v.store.directory)]
    args += ["--profile", "standard", "--dry-run", "--json"]

    def priced(*flags: str) -> dict:
        capsys.readouterr()
        assert main([*args, *flags]) == 0
        out = capsys.readouterr().out
        return json.loads(out[out.index("{") :])

    # the state it starts from leaves one claim unsearched: each step's
    # session's start and a claim
    fitted = ss.FITTED.values()
    expected = sum(f.start + f.task for f in fitted)
    assert priced()["support_if_it_fires"] == round(expected, 4)
    assert "support_if_it_fires" not in priced("--no-search")


def search_all(store, state=None) -> None:
    """A person's search filed on every claim standing unsupported at
    `state` (the latest by default), read unargued: what a review needs to
    ask its rule line."""
    state = state or versions.latest(store)
    claims = ss.standing_unsupported(View(store, state))
    if claims:
        file_records(
            store,
            "search",
            JudgmentRecords(
                searches=tuple(
                    Searched("", None, state.id, on, "no block argues it")
                    for on in claims
                )
            ),
            Author(),
        )


# --- the review: a rule line only on a claim a search read unargued ----------


def test_the_review_asks_a_rule_line_only_of_a_searched_claim(tmp_path, capsys) -> None:
    """Before a search, the sharp claim's `true` is asked with no rule
    line, and the review's dry run names the search; once a search reads
    it unargued, its subject takes the rule line."""
    from test_edits import built_state
    from test_review import opened_brief

    store, args = built_state(tmp_path, capsys)

    def rules() -> set[str]:
        context, _, found = opened_brief(store)
        try:
            return {s.subject.words for s in found.subjects.values() if s.sheet.rules}
        finally:
            context.__exit__(None, None, None)

    assert rules() == set()
    capsys.readouterr()
    where = [args[0], "--store", str(store.directory), "--session", "argument"]
    assert (
        main(["review", *where, "--dry-run", "--prompts", str(tmp_path / "dry")]) == 0
    )
    assert (
        "1 claims standing unsupported that no search read take no rule line: "
        "`axiomation support PAPER --ask` searches them first"
    ) in capsys.readouterr().out
    search_all(store)
    assert rules() == {SHARP_CLAIM}
    # the dry run counts the subjects by kind, the rule line among them
    capsys.readouterr()
    assert (
        main(["review", *where, "--dry-run", "--prompts", str(tmp_path / "dry2")]) == 0
    )
    kinds = next(
        line
        for line in capsys.readouterr().out.splitlines()
        if line.startswith("  its subjects: ")
    )
    assert "true (1 take a rule line)" in kinds


def test_a_ruling_on_a_claim_the_search_argues_is_moot(tmp_path) -> None:
    """The sharp claim ruled owed, then argued by the search: its ruling
    speaks for nothing, and `show` says it is moot."""
    from axiomation import rulings
    from axiomation.judgments import seen
    from axiomation.records import RulingKind

    v, state = kept_update(tmp_path)
    at = seen(v.store, state)
    rulings.rule(
        v.store,
        state,
        rulings.subject(at, View(v.store, state).resolve(SHARP)),
        RulingKind.OWED,
        "the paper's own",
        severity=2,
        fix="argue it",
    )
    found, _ = searched_by(v.store, state, answering("answer_argued", ARGUED))
    assert found.outcome.state is not None
    view = View(v.store, found.outcome.state)
    (item,) = queries.show(view, SHARP).sections["rulings"]
    assert "owed 2 by person (moot: the claim stands justified)" in item.line
    assert item.data["moot"] == "justified" and not item.data["speaks"]


def test_support_ask_refuses_an_agent_flag_the_search_s_table_overrides(
    tmp_path, capsys
) -> None:
    v, _ = kept_update(tmp_path)
    args = [str(v.new.root), "--store", str(v.store.directory), "--ask"]
    capsys.readouterr()
    code = main(["support", *args, "--profile", "standard", "--model", "sonnet"])
    assert code != 0
    assert "the support search's steps are the profile's [standard.support_search]" in (
        capsys.readouterr().err
    )


# --- what a run may not do ---------------------------------------------------------


def test_a_breached_run_files_nothing_and_gives_nothing(tmp_path) -> None:
    from axiomation.runner import Stop
    from axiomation.runner.hosts import HostBreach

    v, state = kept_update(tmp_path)
    host = FakeQueueHost(
        working_through(answering("answer_argued", ARGUED)), ends=HostBreach
    )
    run = Run(v.store, state, ss.FACTORY, {"model": "fake/queue"})
    found = ss.ask(
        run, "fake/queue", Runner(host, Resolver(v.store), 1.0, FLAT), [host]
    )
    assert (
        found.outcome.report is not None and found.outcome.report.stopped is Stop.BREACH
    )
    assert (found.outcome.edit, found.outcome.open) == (None, 1)
    assert not [a for a in v.store.answers() if a.kind == ss.WORKER_KIND]
    (session,) = [
        a
        for a in v.store.answers()
        if a.kind == SESSION_KIND
        and a.template.name in (ss.WORKER_TEMPLATE, ss.SETTLER_TEMPLATE)
    ]
    assert not session.valid and session.problems[0].startswith("breach: ")
    assert versions.latest(v.store).id == state.id


def test_the_search_s_edit_clears_no_stale_mark(tmp_path) -> None:
    """Its sessions are served tasks, never the sheet's marks: its edit is
    recorded as given without them."""
    v, state = kept_update(tmp_path)
    found, _ = searched_by(v.store, state, answering("answer_argued", ARGUED))
    assert found.outcome.edit is not None
    assert v.store.authoritative()[found.outcome.edit].marks_shown is False


def test_the_settling_step_s_line_settles_a_claim_the_worker_left(tmp_path) -> None:
    """The worker reads the claim unargued; the settling step argues it:
    no search is filed, and its line is the search's edit."""
    v, state = kept_update(tmp_path)
    found, _ = searched_by(
        v.store,
        state,
        answering("answer_unargued", UNARGUED),
        answering("answer_argued", ARGUED),
    )
    assert [(s.argued, s.why) for s in found.settled] == [(True, ARGUED["why"])]
    assert v.store.judgment_records().searches == ()
    assert found.outcome.state is not None
    assert standing(v.store, found.outcome.state, SHARP_CLAIM) == "justified"


def test_another_run_s_search_counts_and_this_run_s_does_not(tmp_path) -> None:
    """A run that read a claim unargued draws it again when it is opened
    again (its tasks stay as drawn); another run on the state takes it as
    searched."""
    v, state = kept_update(tmp_path)
    searched_by(v.store, state, answering("answer_unargued", UNARGUED))
    same = {"settle": False, "model": "fake/queue", "every": False}
    with opened(Run(v.store, state, ss.FACTORY, same)) as search:
        assert [c.words for c in search.asked] == [SHARP_CLAIM]
    other = {**same, "blocks": [SHARP]}
    with opened(Run(v.store, state, ss.FACTORY, other)) as search:
        assert search.asked == ()


def test_an_answer_one_step_settled_does_not_settle_two(tmp_path) -> None:
    """A task's key binds its workflow's steps: the worker's answer in a
    one-step run is not the worker's answer in a two-step run."""
    from axiomation.queue import Working, advance, recorded

    v, state = kept_update(tmp_path)
    searched_by(v.store, state, answering("answer_unargued", UNARGUED), every=True)
    options = {"settle": True, "model": "fake/queue", "every": True}
    with opened(Run(v.store, state, ss.FACTORY, options)) as search:
        position, used = advance(search, Working(search), recorded(v.store, search))
    assert used == [] and position.task is not None and position.step == 0
