"""The fieldresults session as an inventory and a queue (docs/architecture.md
§ 2, § 7): the inventory lists the results of the field the paper uses, a
line each `to source` or why none is owed, and no helper; the queue draws
a task each line to source; the finder points at pages with passages found
on them; the reader is served those pages whole and settles each result,
its findings filed on pages it read; both steps search the web. On the
review tests' synthetic paper, its setting citing a work on
`test_citations`' shelf; no model is called.
"""

from __future__ import annotations

import re
from dataclasses import astuple
from pathlib import Path
from types import SimpleNamespace

import pytest

from axiomation import edits, queries, queue, review, versions
from axiomation.judgments import Author, answer_finding
from axiomation.questions import load_template
from axiomation.questions import review_file as rf
from axiomation.queue import Accepted, Refused, Working
from axiomation.records import AGENT, Aspect, FindingStatus
from axiomation.resolver import Resolver
from axiomation.run_state import RunFile
from axiomation.runner import Runner, claude_code
from axiomation.runner.hosts import Rates
from axiomation.runner.queued import run_queue
from axiomation.sources import fieldresults_queue as fq
from questions.review_paper import MAIN, make_paper
from runner.fakes import FakeHost, FakeQueueHost, working_through
from test_citations import shelves
from test_edits import built_state

#: Words the synthetic shelf's page holds (`test_evidence.PAGE`).
HELD = "The ratio of the output to the input"
PAGE = "able1998:12"
#: The synthetic paper, its setting citing a work for something else.
CITING = MAIN.replace(
    "A second sentence of the setting.",
    "A second sentence of the setting \\cite{able1998}.",
)
SETTING = "A second sentence of the setting"
SHARP = "Next, the bound is sharp."


@pytest.fixture
def paper(tmp_path, capsys, monkeypatch):
    import test_person

    monkeypatch.setattr(
        test_person, "make_paper", lambda root: make_paper(root, CITING)
    )
    store, _ = built_state(tmp_path, capsys)
    state = versions.latest(store)
    assert state is not None
    with edits.opened(store, state) as kept:
        yield store, state, kept


def inventory_brief(kept, tmp_path) -> review.Brief:
    return review.brief(
        kept,
        fq.SESSION,
        load_template("review-fieldresults"),
        shelves(),
        None,
        review.Selection.OPEN,
        tmp_path / "paper",
    )


def handle_of(found: review.Brief, words: str) -> str:
    """The handle of the block of `paper.md` holding `words`."""
    assert found.question.workspace is not None
    handle = None
    for line in found.question.workspace.files[rf.PAPER_FILE].split("\n"):
        if m := re.match(r"^⟦(\S+) ", line):
            handle = m.group(1)
        elif words in line:
            assert handle is not None
            return handle
    raise AssertionError(words)


def inventory(found: review.Brief) -> str:
    """An inventory by block: the setting's result to source, the
    unsupported claim the paper's own."""
    return (
        f"@@ {handle_of(found, SETTING)}\n"
        f'checked "{SETTING}" — the gain bound — '
        f"{fq.TO_SOURCE}\n"
        f"@@ {handle_of(found, SHARP)}\n"
        f'checked "{SHARP}" — read its block — the paper\'s own claim\n'
        f"{rf.DONE}\n"
    )


def fielding(paper, tmp_path) -> fq.Fielding:
    store, state, kept = paper
    found = inventory_brief(kept, tmp_path)
    sheet = rf.checked(found.spec, inventory(found)).text
    return fq.Fielding(
        kept,
        queries.View(store, state),
        found,
        fq.items(found, sheet),
        shelves(),
        edits.agent("model-a", None),
    )


def test_the_inventory_lists_and_sources_nothing(paper, tmp_path) -> None:
    """The session's own brief by block is its inventory: no helper, no web,
    its unsupported claims held for its check, its lines `to source` or
    why none is owed, and those lines its check takes as they are."""
    _, _, kept = paper
    found = inventory_brief(kept, tmp_path)
    workspace = found.question.workspace
    assert workspace is not None and workspace.helpers == ()
    assert "WebSearch" not in (workspace.tools or ())
    brief = workspace.instructions
    assert "make\nits inventory" in brief and "to source" in brief
    assert "forager" not in brief and "pending" not in brief
    assert [p[1] for p in found.spec[rf.POINTERS]] == [SHARP]
    assert rf.checked(found.spec, inventory(found)).problems == ()
    unread = inventory(found).replace(f"@@ {handle_of(found, SHARP)}\n", "")
    unread = unread.replace(
        f'checked "{SHARP}" — read its block — the paper\'s own claim\n', ""
    )
    assert any(p.startswith("unread:") for p in rf.checked(found.spec, unread).problems)


def test_the_queue_s_tasks_are_the_inventory_s_lines_to_source(paper, tmp_path) -> None:
    workflow = fielding(paper, tmp_path)
    working = Working(workflow)
    (task,) = workflow.draw(working)
    item = workflow.listed[task.id]
    assert (task.id, item.words, item.result) == (
        "r1",
        SETTING,
        "the gain bound",
    )
    finder, reader = workflow.steps
    served = finder.render(task, working)
    assert served.startswith("find the work that states this result\n⟦r1 result⟧")
    assert "↳ cites able1998" in served and "its block:" in served


def test_a_session_answers_five_results_and_each_is_priced_a_start(
    paper, tmp_path, monkeypatch
) -> None:
    """A fresh session takes the queue on every five results, its context
    carried no further; a run is priced a start for each."""
    assert fq.SHARE == 5
    assert fielding(paper, tmp_path).serving.session_tasks == fq.SHARE
    store, _, _ = paper
    price = fq.price(store, fq.READER_KIND, "claude-opus-5-5")
    assert price.cost(0, fq.sessions(0)) == 0.0
    assert fq.sessions(5) == 1 and fq.sessions(6) == 2
    assert price.cost(6, fq.sessions(6)) == pytest.approx(
        2 * price.start + 6 * price.task
    )


def test_the_finder_points_with_passages_found_on_their_pages(paper, tmp_path) -> None:
    workflow = fielding(paper, tmp_path)
    working = Working(workflow)
    (task,) = workflow.draw(working)
    finder, _ = workflow.steps

    def found(**args) -> Accepted | Refused:
        return finder.answer(
            task, "found", {"task": "r1", "searched": "its name", **args}, working, True
        )

    taken = found(pages=[{"page": PAGE, "quote": HELD}])
    assert isinstance(taken, Accepted) and taken.hand_on is not None
    assert taken.hand_on["found"] == (("able1998", 12, HELD),)
    off = found(pages=[{"page": PAGE, "quote": "words on no page"}])
    assert isinstance(off, Refused) and "not on that page" in off.problems[0]
    unfetched = found(pages=[{"page": "arxiv:2101.00001:2", "quote": HELD}])
    assert isinstance(unfetched, Refused) and "fetched first" in unfetched.problems[0]
    assert isinstance(found(pages=[]), Refused)
    pointer = found(pages=[], unread=[{"work": "doi:10.1000/x", "why": "no copy"}])
    assert isinstance(pointer, Accepted)
    none = finder.answer(
        task, "not_found", {"task": "r1", "searched": "s"}, working, True
    )
    assert isinstance(none, Accepted)


def test_the_reader_files_its_findings_on_pages_it_read(paper, tmp_path) -> None:
    """The reader is served the finder's pages whole; a finding on one is
    filed with it, one on a page no one read is refused, a result left to
    source is refused, and a work named by its identifier with no pages is
    a pointer."""
    workflow = fielding(paper, tmp_path)
    working = Working(workflow)
    (task,) = workflow.draw(working)
    _, reader = workflow.steps
    task = SimpleNamespace(
        id=task.id,
        block=task.block,
        subject={**task.subject, "found": [["able1998", 12, HELD]], "searched": "s"},
    )
    served = reader.render(task, working)  # type: ignore[arg-type]
    assert "the finder's pages, read for you here whole:" in served
    assert 'page-index="12"' in served and HELD in served

    def settle(**args) -> Accepted | Refused:
        given = {"task": "r1", "searched": "the cited works", **args}
        return reader.answer(task, "settle", given, working, True)  # type: ignore[arg-type]

    finding = {
        "works": ["able1998"],
        "severity": 1,
        "pages": [PAGE],
        "problem": "the work states the bound the setting uses",
        "fix": "cite it at the setting, p. 12",
    }
    filed = settle(found="able1998 states it on p. 12", finds=[finding])
    assert isinstance(filed, Accepted), filed
    (made,) = filed.judgments.findings
    assert made.aspect is Aspect.LITERATURE
    assert [p.name for p in made.pages] == [PAGE]
    unread = settle(found="f", finds=[{**finding, "pages": ["able1998:13"]}])
    assert isinstance(unread, Refused) and "able1998:13" in unread.problems[0]
    assert isinstance(settle(found=fq.TO_SOURCE), Refused)
    pointer = {**finding, "works": ["doi:10.1000/x"], "pages": []}
    assert isinstance(settle(found="named, not read", finds=[pointer]), Accepted)
    assert isinstance(settle(found="nothing a reader needs"), Accepted)


def test_both_steps_search_the_web_and_their_host_lets_them() -> None:
    """A step whose `web` is true is a queue session given WebSearch: its
    command lists it, its settings allow it, and its confinement check
    lets it run; a session without is breached by it."""
    assert fq.Finder.web and fq.Reader.web
    step = SimpleNamespace(
        kickoff="k", template=SimpleNamespace(render=lambda: "b"), web=True
    )
    session = queue.queue_session(step, SimpleNamespace(name="q"), None)  # type: ignore[arg-type]
    assert session.web
    host = claude_code.ClaudeCodeHost(model="claude-haiku-5-5", effort="low")
    argv = host.queue_argv(Path("/tmp/x"), session)
    assert argv[argv.index("--tools") + 1] == "WebSearch"
    allowed = host.settings(Path("/tmp/x"), Path("/tmp/y"), web=claude_code.QUEUE_WEB)
    assert "WebSearch" in allowed["permissions"]["allow"]
    used = ({"tool": "WebSearch", "status": "completed"},)
    assert claude_code._queue_breach(used, "q", None, True) == ""
    assert "WebSearch ran" in claude_code._queue_breach(used, "q", None, False)


# --- a run --------------------------------------------------------------------------


def _found(served: str) -> tuple[str, dict]:
    task = served.split()[1]
    return "found", {
        "task": task,
        "pages": [{"page": PAGE, "quote": HELD}],
        "searched": "the bound's name",
    }


def _settled(served: str) -> tuple[str, dict]:
    task = served.split("⟦", 1)[1].split()[0]
    return "settle", {
        "task": task,
        "searched": "the finder's page",
        "found": "able1998 states it on p. 12, cited here for something else",
        "finds": [
            {
                "works": ["able1998"],
                "severity": 1,
                "pages": [PAGE],
                "problem": "the work states the bound the setting uses",
                "fix": "cite it at the setting, p. 12",
            }
        ],
    }


def test_review_asks_the_inventory_then_the_queue(
    paper, tmp_path, monkeypatch, capsys
) -> None:
    """`review --session fieldresults`: a dry run prices the inventory and
    the queue's steps a result; asked, on fake hosts, the inventory is
    filed, then its result settled and its finding filed in the round;
    asked again, the round replays and asks nothing."""
    from axiomation import cli, evidence
    from axiomation.commands import seating

    store, _, kept = paper
    monkeypatch.setattr(evidence, "sources_for", lambda paper, store: shelves())
    args = ["review", str(tmp_path / "paper"), "--store", str(store.directory)]
    args += ["--session", "fieldresults"]
    capsys.readouterr()
    # priced without --prompts: nothing written
    assert cli.main([*args, "--dry-run"]) == 0
    assert "then review-fieldresults-finder" in capsys.readouterr().out
    assert not (tmp_path / "dry").exists()
    assert cli.main([*args, "--dry-run", "--prompts", str(tmp_path / "dry")]) == 0
    out = capsys.readouterr().out
    assert "then review-fieldresults-finder" in out
    assert "then review-fieldresults-reader" in out
    assert "its queue: about 1 results (one a claim standing unsupported)" in out
    written = sorted(p.name for p in (tmp_path / "dry" / "fieldresults").iterdir())
    assert "review-fieldresults-finder.md" in written
    assert "review-fieldresults-reader.md" in written
    found = inventory_brief(kept, tmp_path)
    host = FakeHost(lambda p: "", edits={rf.REVIEW_FILE: lambda _: inventory(found)})
    monkeypatch.setattr(seating, "host_of", lambda agent, executable, tools=None: host)
    hosts = [
        FakeQueueHost(working_through(_found), model="claude-haiku-5-5"),
        FakeQueueHost(working_through(_settled), model="claude-opus-5-5"),
    ]
    monkeypatch.setattr(seating, "queue_hosts", lambda seated: hosts)
    assert cli.main([*args, "--budget", "5"]) == 0
    out = capsys.readouterr().out
    assert "its queue: 1 results to source" in out
    assert "1 tasks settled, 0 open" in out and "0 judgments, 1 findings" in out
    (finding,) = store.judgment_records().findings
    assert finding.aspect is Aspect.LITERATURE
    assert [a.work for a in finding.anchors if a.work] == ["able1998"]
    assert cli.main([*args, "--budget", "5"]) == 0
    assert [len(h.sessions) for h in hosts] == [1, 1]


def test_a_queue_step_s_answer_is_its_session_s_review() -> None:
    assert review.of_session(fq.READER_KIND, rf.Session.FIELDRESULTS)
    assert review.of_session(fq.FINDER_KIND, rf.Session.FIELDRESULTS)
    assert not review.of_session(fq.READER_KIND, rf.Session.CITATIONCHECK)


def test_a_run_files_its_results_in_its_round(paper, tmp_path, monkeypatch) -> None:
    """Both steps on fake hosts, after an inventory filed: the result
    settled, its finding filed in the run's review round."""
    store, state, kept = paper
    monkeypatch.setattr(fq.evidence, "sources_for", lambda paper, store: shelves())
    found = inventory_brief(kept, tmp_path)
    host = FakeHost(lambda p: "", edits={rf.REVIEW_FILE: lambda _: inventory(found)})
    # as `review` asks it: in its round, naming the state its brief read
    held = RunFile(store.directory, "review", state.id, "")
    ran = Runner(
        host,
        Resolver(store, run=held),
        10.0,
        Rates(0.0, 1.0),
        review_round="r1",
        review_state=state.id,
    ).run(
        [found.question],
        review.checker(kept, found, shelves(), edits.agent(host.model, None)),
    )
    assert ran.answered == (found.question.key,), ran
    options = {
        "profile": "standard",
        "profiles": None,
        "paper": str(tmp_path / "paper"),
        "round": "r1",
        "results": [
            list(astuple(i)) for i in fq.items(found, held.text(found.question.key))
        ],
        "model": "fake/queue",
    }
    run = queue.Run(store, state, fq.FACTORY, options)
    hosts = [
        FakeQueueHost(working_through(_found)),
        FakeQueueHost(working_through(_settled)),
    ]
    runner = Runner(
        hosts[-1], Resolver(store), 10.0, Rates(0.0, 1.0), review_round="r1"
    )
    report = run_queue(runner, run, hosts, (1.0, 9.0), wait_seconds=5.0)
    assert (report.settled, report.open) == (1, 0), report
    readers = [a for a in store.authoritative().values() if a.kind == fq.READER_KIND]
    assert [a.review_round for a in readers] == ["r1"]
    (finding,) = store.judgment_records().findings
    assert [p.name for p in finding.pages] == [PAGE]
    # its finding proposed answered is the inventory's to settle in the next
    # round; the queue's reader still settles its results on one block
    answer_finding(
        store,
        finding.id,
        FindingStatus.FIXED,
        note="the setting cites it now",
        author=Author(AGENT, "model-b", "s9"),
    )
    again = queue.Run(store, state, fq.FACTORY, {**options, "round": "r2"})
    with queue.opened(again) as workflow:
        assert isinstance(workflow, fq.Fielding)
        assert workflow.found.questions
        working = Working(workflow)
        (task,) = workflow.draw(working)
        _, reader = workflow.steps
        settled = reader.answer(
            task,
            "settle",
            {"task": task.id, "searched": "s", "found": "nothing a reader needs"},
            working,
            True,
        )
        assert isinstance(settled, Accepted), settled


def test_a_page_read_before_its_work_is_fetched_reads_after(paper, tmp_path) -> None:
    """A found work's page named before it is fetched is refused, and taken
    once it is: a read that found no page is not kept."""
    from test_contribution import FOUND, shelf_and_fetcher

    store, state, kept = paper
    _, _, sources = shelf_and_fetcher()
    found = inventory_brief(kept, tmp_path)
    sheet = rf.checked(found.spec, inventory(found)).text
    workflow = fq.Fielding(
        kept,
        queries.View(store, state),
        found,
        fq.items(found, sheet),
        sources,
        edits.agent("model-a", None),
    )
    working = Working(workflow)
    (task,) = workflow.draw(working)
    finder, _ = workflow.steps
    page = {"page": f"{FOUND}:2", "quote": "Lemma 2. The output of every gain map"}
    args = {"task": "r1", "searched": "s", "pages": [page]}
    early = finder.answer(task, "found", args, working, True)
    assert isinstance(early, Refused) and "fetched first" in early.problems[0]
    (fetch,) = (r for r in workflow.reads if r.tool.name == "fetch")
    assert str(fetch.answer(working, {"work": FOUND})).startswith(f"fetched {FOUND}")
    assert isinstance(finder.answer(task, "found", args, working, True), Accepted)


def test_a_line_to_source_with_a_word_after_is_still_a_task(paper, tmp_path) -> None:
    _, _, kept = paper
    found = inventory_brief(kept, tmp_path)
    worded = inventory(found).replace(
        f"— {fq.TO_SOURCE}\n", "— to source (a textbook)\n"
    )
    (item,) = fq.items(found, rf.checked(found.spec, worded).text)
    assert item.words == SETTING


def test_a_settling_brief_owes_no_inventory_and_a_part_its_own(paper, tmp_path) -> None:
    """Settling its findings alone, the session's check holds no claim
    standing unsupported against it; reading a part of the text, those of
    the blocks it reads alone."""
    _, _, kept = paper
    template = load_template("review-fieldresults")
    found = review.brief(
        kept,
        fq.SESSION,
        template,
        shelves(),
        None,
        review.Selection.OPEN,
        tmp_path / "paper",
    )
    assert found.spec[rf.POINTERS]
    laid = review._laid(
        kept, fq.SESSION, shelves(), None, review.Selection.OPEN, tmp_path / "paper"
    )

    def owed(**given) -> dict:
        spec: dict = {}
        files = {rf.PAPER_FILE: kept.sheet}
        review._plain_by_block(laid, template, (), files, spec, [], **given)
        return spec

    assert rf.POINTERS not in owed(settles=True)
    handle = found.spec[rf.POINTERS][0][0]
    node = laid.view.node(laid.view.resolve(handle))
    part = owed(read=[node])[rf.POINTERS]
    assert part and {p[0] for p in part} == {handle}
    assert owed(read=[])[rf.POINTERS] == []


def test_a_claim_standing_unsupported_no_answer_read_is_read_again(
    paper, tmp_path
) -> None:
    """The inventory is owed on each claim standing unsupported that no
    answer read standing so: an answer that read every block covers them;
    one that read none of their blocks leaves them owed, their text
    unchanged."""
    _, state, kept = paper
    view = queries.View(kept.store, kept.state)
    claims = {view.node(view.resolve(h)) for h, _, _ in review.pointers(view)}
    every = frozenset(n.node for n in kept.state.nodes)
    assert claims
    assert review._owed(view, fq.SESSION, [(kept.state.id, every)]) == frozenset()
    rest = every - claims
    assert review._owed(view, fq.SESSION, [(kept.state.id, rest)]) == claims
    assert review._owed(view, fq.SESSION, []) == claims


def test_review_takes_no_scope_and_skips_without_sources(
    paper, tmp_path, monkeypatch, capsys
) -> None:
    """Its inventory reads the whole paper and its queue sources each result
    once: `--scope` is refused. With no cited work to read it is not asked,
    and named, refused."""
    from axiomation import cli, evidence

    store, _, _ = paper
    monkeypatch.setattr(evidence, "sources_for", lambda paper, store: shelves())
    args = ["review", str(tmp_path / "paper"), "--store", str(store.directory)]
    scoped = [*args, "--session", "fieldresults", "--scope", "Setting"]
    assert cli.main([*scoped, "--budget", "5"]) != 0
    assert "it takes no --scope or --by-section" in capsys.readouterr().err

    def none(paper, store):
        raise evidence.EvidenceError("no library here")

    monkeypatch.setattr(evidence, "sources_for", none)
    assert cli.main([*args, "--session", "fieldresults", "--budget", "5"]) != 0
    assert "the fieldresults session reads the cited works" in capsys.readouterr().err
