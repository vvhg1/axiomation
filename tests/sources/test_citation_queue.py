"""The citation session as a queue (docs/architecture.md § 2, § 7): a task
each site, a work's sites in a row; the finder points at pages with quotes
found on them, the reader is served them whole and judges, its evidence
written by code from the words that decide; a page named is one read
whole; a quote the stored text does not hold is asked again once. On
the review tests' synthetic paper, two of its sentences citing the works
on `test_citations`' shelf; no model is called.
"""

from __future__ import annotations

import re
from types import SimpleNamespace

import pytest

from axiomation import edits, queries, queue, review, versions
from axiomation import review_queue as rq
from axiomation.judgments import Author, answer_finding
from axiomation.questions import load_template
from axiomation.questions import review_file as rf
from axiomation.queue import Accepted, Refused, Task, Working
from axiomation.records import AGENT, Aspect, FindingStatus, RecordStore
from axiomation.resolver import Resolver
from axiomation.runner import Runner
from axiomation.runner.hosts import Rates
from axiomation.runner.queued import run_queue
from axiomation.sources import citation_queue as cq
from questions.review_paper import MAIN, make_paper
from runner.fakes import FakeQueueHost, working_through
from test_citations import shelves
from test_edits import built_state

#: Words the synthetic shelf's pages hold (`test_evidence.PAGE`).
HELD = "The ratio of the output to the input"
#: Where each work's held words are.
PAGES = {"able1998": "able1998:12", "baker1984": "baker1984:4"}
#: The synthetic paper, its setting citing one work and its footnote two,
#: where no line its build gives quotes them.
CITING = MAIN.replace(
    "A second sentence of the setting.",
    "A second sentence of the setting \\cite{able1998}.",
).replace("Small means below one.", "Small means below one \\cite{able1998,baker1984}.")


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


def citing(paper, tmp_path, find: bool = False) -> cq.Citing:
    store, state, kept = paper
    sources = shelves()
    template = load_template("review-citationcheck")
    found = review.brief(
        kept,
        rf.Session.CITATIONCHECK,
        template,
        sources,
        None,
        review.Selection.ALL,
        tmp_path / "paper",
    )
    return cq.Citing(
        kept,
        queries.View(store, state),
        found,
        template,
        sources,
        edits.agent("model-a", None),
        find,
    )


def judge(task: str, page: str, words: str = HELD, **more) -> dict:
    return {
        "task": task,
        "verdict": "verified",
        "quotes": [{"page": page, "words": words, **more}],
        "bears": "the page states it as the sentence does",
    }


def read(workflow: cq.Citing, working: Working, key: str, page: int) -> str:
    (cited_page,) = (r for r in workflow.reads if r.tool.name == "cited_page")
    return str(cited_page.answer(working, {"key": key, "page": page}))


def test_the_sites_come_by_work_in_the_order_the_paper_first_cites_them() -> None:
    def site(work: str) -> SimpleNamespace:
        return SimpleNamespace(subject=SimpleNamespace(work=work))

    found = SimpleNamespace(
        subjects={"s1": site("a"), "s2": site("b"), "s3": site("a"), "s4": site("c")}
    )
    assert cq.by_work(found) == ["s1", "s3", "s2", "s4"]  # type: ignore[arg-type]


def test_a_site_is_served_with_its_work_its_entry_s_place_and_its_block(
    paper, tmp_path
) -> None:
    workflow = citing(paper, tmp_path)
    working = Working(workflow)
    tasks = workflow.draw(working)
    assert [(t.id, t.subject["site"]) for t in tasks] == [
        ("s1", True),
        ("s2", True),
        ("s3", True),
    ]
    (reader,) = workflow.steps
    served = reader.render(tasks[0], working)
    assert re.match(r"judge this citation site\n⟦s1 cited⟧ \S+ · ", served)
    assert "its work: able1998" in served and "its block:" in served
    # the paper is laid in plain, the core's graph reads withheld
    assert set(workflow.serving.files) == {rf.PAPER_FILE}
    assert {"edges", "standing", "search"} <= workflow.serving.withheld


def test_a_session_judges_its_share_and_each_is_priced_a_start(paper, tmp_path) -> None:
    """A fresh session takes the queue on every `SHARE` sites; a run is
    priced a start for each."""
    assert citing(paper, tmp_path).serving.session_tasks == cq.SHARE == 20
    store, _, _ = paper
    price = cq.price(store, cq.READER_KIND, "claude-opus-5-5", True)
    assert cq.sessions(20) == 1 and cq.sessions(57) == 3
    assert price.cost(57, cq.sessions(57)) == pytest.approx(
        3 * price.start + 57 * price.task
    )


def test_a_judge_names_only_pages_the_session_read_whole(paper, tmp_path) -> None:
    workflow = citing(paper, tmp_path)
    working = Working(workflow)
    (reader,) = workflow.steps
    task = workflow.draw(working)[0]
    found = reader.answer(task, "judge", judge("s1", "able1998:12"), working, True)
    assert isinstance(found, Refused)
    assert "no such page read whole" in found.problems[0]
    read(workflow, working, "able1998", 12)
    found = reader.answer(task, "judge", judge("s1", "able1998:12"), working, True)
    assert isinstance(found, Accepted), found
    (judgment,) = found.judgments.judgments
    assert judgment.evidence.startswith(f'able1998:12 "{HELD}": the page states')


@pytest.mark.parametrize(
    ("args", "said"),
    [
        ({"quotes": []}, "quotes the page's words it rests on"),
        ({"quotes": [{"page": "able1998:12", "words": "w " * 31}]}, "at most 30"),
        ({"quotes": [{"page": "able1998:12", "words": HELD}] * 4}, "at most 3"),
        ({"bears": ""}, "what it bears"),
        ({"pages": ["able1998 p. 12"]}, "write KEY:N"),
    ],
)
def test_a_judge_s_evidence_is_held_to_its_bounds(
    paper, tmp_path, args: dict, said: str
) -> None:
    workflow = citing(paper, tmp_path)
    working = Working(workflow)
    (reader,) = workflow.steps
    task = workflow.draw(working)[0]
    found = reader.answer(
        task, "judge", {**judge("s1", "able1998:12"), **args}, working, True
    )
    assert isinstance(found, Refused) and said in found.problems[0]


def test_a_quote_the_stored_text_lacks_is_asked_again_once_then_taken_confirmed(
    paper, tmp_path
) -> None:
    workflow = citing(paper, tmp_path)
    working = Working(workflow)
    (reader,) = workflow.steps
    task = workflow.draw(working)[0]
    read(workflow, working, "able1998", 12)
    lacking = judge("s1", "able1998:12", "words the page does not print")
    # confirmed before the check asked: asked all the same
    early = judge("s1", "able1998:12", "other words it lacks", confirmed=True)
    found = reader.answer(task, "judge", early, working, True)
    assert isinstance(found, Refused)
    assert "the automatic check did not find these words" in found.problems[0]
    found = reader.answer(task, "judge", lacking, working, True)
    assert isinstance(found, Refused)
    assert found.problems[0].startswith('able1998:12 "words the page does not')
    confirmed = judge(
        "s1", "able1998:12", "words the page does not print", confirmed=True
    )
    found = reader.answer(task, "judge", confirmed, working, True)
    assert isinstance(found, Accepted), found
    (judgment,) = found.judgments.judgments
    assert cq.CONFIRMED in judgment.evidence
    held = reader.answer(
        task, "judge", judge("s1", "able1998:12", confirmed=True), working, True
    )
    assert isinstance(held, Refused) and "leave out `confirmed`" in held.problems[0]


def test_the_finder_points_with_quotes_found_on_their_pages(paper, tmp_path) -> None:
    workflow = citing(paper, tmp_path, find=True)
    working = Working(workflow)
    finder, reader = workflow.steps
    task = workflow.draw(working)[0]
    assert finder.render(task, working).startswith("find the pages for this citation")

    def found(page: str, quote: str = HELD) -> Accepted | Refused:
        args = {
            "task": "s1",
            "pages": [{"page": page, "quote": quote}],
            "searched": "x",
        }
        return finder.answer(task, "found", args, working, True)

    missing = found("able1998:12", "a passage it does not hold")
    assert isinstance(missing, Refused) and "not on that page" in missing.problems[0]
    other = found("baker1984:4")
    assert isinstance(other, Refused)
    assert "not a file of the site's work" in other.problems[0]
    pointed = found("able1998:12")
    assert isinstance(pointed, Accepted) and pointed.hand_on is not None
    assert not pointed.judgments.judgments
    handed = Task(task.id, task.block, {**task.subject, **pointed.hand_on})
    # the reader is served the pages found whole, and may name them
    served = reader.render(handed, working)
    assert '<source-text key="able1998" page-index="12"' in served
    judged = reader.answer(handed, "judge", judge("s1", "able1998:12"), working, True)
    assert isinstance(judged, Accepted), judged


def test_a_finding_to_settle_passes_the_finder_and_takes_a_settling_tool(
    paper, tmp_path
) -> None:
    workflow = citing(paper, tmp_path, find=True)
    working = Working(workflow)
    finder, reader = workflow.steps
    finding = Task("f1", "", {"site": False})
    assert not finder.takes(finding)
    assert finder.takes(workflow.draw(working)[0])
    site = reader.answer(
        workflow.draw(working)[0], "stands", {"task": "s1", "why": "x"}, working, True
    )
    assert isinstance(site, Refused) and "answered by `judge`" in site.problems[0]
    assert rq.settle_line(
        "f1", "stands", {"why": "it still reads so", "quote": "q"}
    ) == ('stands f1 "q" — it still reads so')
    assert rq.settle_line("f1", "fixed", {"why": "gone", "quote": "q"}) == (
        "fixed f1 — gone"
    )


def test_a_finding_s_fields_stay_its_own_on_its_line(paper, tmp_path) -> None:
    """The line parts a problem from its fix at its first dash: a dash in
    the problem is written as a semicolon, and a quote with a double quote
    is refused."""
    found = {
        "aspect": "cited",
        "anchors": ["b", "work:k"],
        "severity": 2,
        "quote": "the words",
        "problem": "the locator is the index — the book prints 26",
        "fix": "cite p. 26 — its printed page",
    }
    assert rq.find_line(found) == (
        'find cited b work:k 2 "the words" — the locator is the index; the book '
        "prints 26 — cite p. 26 — its printed page"
    )
    with pytest.raises(rq.AnswerError, match="no double quote"):
        rq.find_line({**found, "quote": 'the "words"'})


def test_a_page_served_with_a_task_is_read_for_that_task_alone(paper, tmp_path) -> None:
    workflow = citing(paper, tmp_path, find=True)
    working = Working(workflow)
    _, reader = workflow.steps
    first, second, _ = workflow.draw(working)
    found = {"found": (("able1998", 12, HELD),), "searched": "x"}
    reader.render(Task(first.id, first.block, {**first.subject, **found}), working)
    judged = reader.answer(
        Task(second.id, second.block, {**second.subject, "found": ()}),
        "judge",
        judge("s2", "able1998:12"),
        working,
        True,
    )
    assert isinstance(judged, Refused)
    assert "no such page read whole" in judged.problems[0]


def test_a_page_with_no_text_is_named_once_its_image_is_read(paper, tmp_path) -> None:
    """A plate of the version of record stands as its image: read whole as
    text, it is not read until its image is."""
    workflow = citing(paper, tmp_path)
    # the brief lays it in as evidence, which the sheet's check reads
    assert workflow.found.spec["pages"]["able1998"] == {"12": "ok", "13": "image"}
    working = Working(workflow)
    read(workflow, working, "able1998", 13)
    (unread,) = workflow.cited.unread([("able1998", 13)], ())
    assert "its image was not read (`cited_image` able1998 13)" in unread
    workflow.cited.imaged.add(("able1998", 13))  # what `cited_image` keeps
    assert workflow.cited.unread([("able1998", 13)], ()) == []
    assert workflow.cited.unread([("able1998", 12)], ()) == [
        "able1998:12: no such page read whole"
    ]


def test_a_judge_naming_a_page_with_no_text_unseen_as_an_image_is_refused(
    paper, tmp_path
) -> None:
    workflow = citing(paper, tmp_path)
    working = Working(workflow)
    (reader,) = workflow.steps
    task = workflow.draw(working)[0]
    reader.render(task, working)
    read(workflow, working, "able1998", 13)
    judged = reader.answer(
        task, "judge", judge(task.id, "able1998:13", "A plate."), working, True
    )
    assert isinstance(judged, Refused)
    assert "its image was not read (`cited_image` able1998 13)" in judged.problems[0]


def test_a_queue_with_no_cited_work_that_reads_refuses_each_key(
    paper, tmp_path
) -> None:
    from axiomation.evidence import Sources

    store, state, kept = paper
    template = load_template("review-citationcheck")
    found = review.brief(
        kept,
        rf.Session.CITATIONCHECK,
        template,
        Sources([]),
        None,
        review.Selection.ALL,
        tmp_path / "paper",
    )
    citing = cq.Citing(
        kept,
        queries.View(store, state),
        found,
        template,
        Sources([]),
        edits.agent("model-a", None),
    )
    assert citing.cited.reads.works == ()
    with pytest.raises(queue.ReadError, match="they are none"):
        citing.cited.page({"key": "able1998", "page": 1})


def test_a_template_without_the_reader_s_sections_is_refused() -> None:
    from axiomation.questions import Template

    with pytest.raises(cq.CitationError, match="## What to write"):
        cq.reader_brief(Template("mine", "## What to check\n"), False)


def test_a_queue_step_s_answer_is_its_session_s_review() -> None:
    assert review.of_session(cq.READER_KIND, rf.Session.CITATIONCHECK)
    assert review.of_session("review-citationcheck", rf.Session.CITATIONCHECK)
    # its answers kept under its earlier name, `citation`, are its own
    assert review.of_session("review-citation", rf.Session.CITATIONCHECK)
    assert review.of_session("review-citation-reader", rf.Session.CITATIONCHECK)
    assert rf.session_named("citation") is rf.Session.CITATIONCHECK
    assert rf.current_kind("review-citation-finder") == cq.FINDER_KIND
    assert not review.of_session(cq.READER_KIND, rf.Session.CONTRIBUTION)


# --- a run --------------------------------------------------------------------------


def _work(served: str) -> str:
    m = re.search(r"its work: (\S+)", served)
    assert m is not None, served
    return m.group(1)


def _found(served: str) -> tuple[str, dict]:
    task = served.split()[1]
    page = PAGES[_work(served)]
    return "found", {
        "task": task,
        "pages": [{"page": page, "quote": HELD}],
        "searched": "the function's name",
    }


def _judged(served: str) -> tuple[str, dict]:
    """A site judged verified on its work's page; the first also files a
    finding on its block."""
    task = served.split()[1]
    args = judge(task, PAGES[_work(served)])
    if task == "s1":
        m = re.search(r"⟦s1 cited⟧ (\S+) · ", served)
        assert m is not None
        args["finds"] = [
            {
                "aspect": "cited",
                "anchors": [m.group(1), "work:able1998"],
                "severity": 1,
                "quote": "A second sentence of the setting",
                "problem": "the work is cited whole for one claim",
                "fix": "name its page",
            }
        ]
    return "judge", args


def test_a_run_files_each_site_in_its_round_and_draws_the_same_tasks_again(
    paper, tmp_path, monkeypatch
) -> None:
    """Both steps on fake hosts: every site settled, its judgment filed in
    the run's review round; opened again under that round, the run draws
    the sites it filed, as `open` would not. A finding it filed that an
    agent proposes fixed is the next round's first task, the reader's
    alone, settled by `stands`."""
    store, state, _ = paper
    monkeypatch.setattr(cq.evidence, "sources_for", lambda paper, store: shelves())
    options = {
        "profile": "standard",
        "profiles": None,
        "paper": str(tmp_path / "paper"),
        "select": review.Selection.OPEN.value,
        "round": "r1",
        "find": True,
        "model": "fake/queue",
    }
    run = queue.Run(store, state, cq.FACTORY, options)
    hosts = [
        FakeQueueHost(working_through(_found)),
        FakeQueueHost(working_through(_judged)),
    ]
    runner = Runner(
        hosts[-1], Resolver(store), 10.0, Rates(0.0, 1.0), review_round="r1"
    )
    report = run_queue(runner, run, hosts, (1.0, 9.0), wait_seconds=5.0)
    assert (report.settled, report.open) == (3, 0), report
    filed = RecordStore(store.directory).authoritative()
    readers = [a for a in filed.values() if a.kind == cq.READER_KIND]
    assert len(readers) == 3 and {a.review_round for a in readers} == {"r1"}
    held = store.judgment_records().judgments
    assert sorted(j.evidence.split(" ", 1)[0] for j in held) == [
        "able1998:12",
        "able1998:12",
        "baker1984:4",
    ]
    with queue.opened(run) as again:
        assert [t.id for t in again.draw(Working(again))] == ["s1", "s2", "s3"]
    other = queue.Run(store, state, cq.FACTORY, {**options, "round": "r2"})
    with pytest.raises(review.NoSubject), queue.opened(other):
        pass
    (finding,) = store.judgment_records().findings
    answer_finding(
        store,
        finding.id,
        FindingStatus.FIXED,
        note="the citation names its page now",
        author=Author(AGENT, "model-b", "s9"),
    )
    settling = queue.Run(store, state, cq.FACTORY, {**options, "round": "r3"})
    with queue.opened(settling) as workflow:
        assert isinstance(workflow, cq.Citing)
        working = Working(workflow)
        (task,) = workflow.draw(working)
        finder, reader = workflow.steps
        assert task.id == "f1" and not finder.takes(task)
        assert reader.render(task, working).startswith("settle this finding\n⟦f1 ")
        why = {"task": "f1", "why": "the sentence still cites the work whole"}
        stood = reader.answer(task, "stands", why, working, True)
        assert isinstance(stood, Accepted), stood
        (refiled,) = stood.judgments.findings
        assert refiled.reaffirms == finding.id


def test_review_asks_the_citation_session_as_a_queue(
    paper, tmp_path, monkeypatch, capsys
) -> None:
    """`review --session citationcheck`: a dry run prices the queue's steps and
    writes their briefs and the tasks; `--scope` is refused; asked, on fake
    hosts, its sites are filed in its round, and asked again, the same
    round replays and asks nothing."""
    from axiomation import evidence
    from axiomation.cli import main
    from axiomation.commands import seating

    store, _, _ = paper
    monkeypatch.setattr(evidence, "sources_for", lambda paper, store: shelves())
    args = ["review", str(tmp_path / "paper"), "--store", str(store.directory)]
    args += ["--session", "citationcheck"]
    capsys.readouterr()
    assert main([*args, "--dry-run", "--prompts", str(tmp_path / "dry")]) == 0
    out = capsys.readouterr().out
    assert "citationcheck at state" in out and ": 3 sites, 2 cited works" in out
    assert "review-citationcheck-finder" in out and "review-citationcheck-reader" in out
    assert sorted(p.name for p in (tmp_path / "dry" / "citationcheck").iterdir()) == [
        "review-citationcheck-finder.md",
        "review-citationcheck-reader.md",
        "tasks.md",
    ]
    assert main([*args, "--scope", "Setting", "--budget", "5"]) != 0
    assert "it takes no --scope" in capsys.readouterr().err
    hosts = [
        FakeQueueHost(working_through(_found), model="claude-haiku-5-5"),
        FakeQueueHost(working_through(_judged), model="claude-opus-5-5"),
    ]
    monkeypatch.setattr(seating, "queue_hosts", lambda seated: hosts)
    assert main([*args, "--budget", "5"]) == 0
    out = capsys.readouterr().out
    assert "3 tasks settled, 0 open" in out and "3 judgments, 1 findings" in out
    assert main([*args, "--budget", "5"]) == 0
    assert [len(h.sessions) for h in hosts] == [1, 1]


#: The words of each site's block a finding on it quotes.
QUOTED = {"s1": "A second sentence of the setting", "s2": "Small means below one"}


def _judged_finding_both(served: str) -> tuple[str, dict]:
    """A site judged verified; s1 and s2 each file a finding on their
    block."""
    task = served.split()[1]
    args = judge(task, PAGES[_work(served)])
    if task in QUOTED:
        m = re.search(rf"⟦{task} cited⟧ (\S+) · ", served)
        assert m is not None
        args["finds"] = [
            {
                "aspect": "cited",
                "anchors": [m.group(1), f"work:{_work(served)}"],
                "severity": 1,
                "quote": QUOTED[task],
                "problem": "the work is cited whole for one claim",
                "fix": "name its page",
            }
        ]
    return "judge", args


def _filed_finding(
    paper, tmp_path, monkeypatch, judged=_judged, proposed=None
) -> tuple[dict, list[str]]:
    """Round r1 on fake hosts files a finding on s1 (with `judged`, on
    each site it says), which an agent then proposes fixed: the next
    round's tasks, f1 on. Returns the run's options and the findings' ids
    in their order. `proposed`, where given, the indices of the findings
    proposed fixed; else every one."""
    store, state, _ = paper
    monkeypatch.setattr(cq.evidence, "sources_for", lambda paper, store: shelves())
    options = {
        "profile": "standard",
        "profiles": None,
        "paper": str(tmp_path / "paper"),
        "select": review.Selection.OPEN.value,
        "round": "r1",
        "find": True,
        "model": "fake/queue",
    }
    hosts = [
        FakeQueueHost(working_through(_found)),
        FakeQueueHost(working_through(judged)),
    ]
    runner = Runner(
        hosts[-1], Resolver(store), 10.0, Rates(0.0, 1.0), review_round="r1"
    )
    run_queue(runner, queue.Run(store, state, cq.FACTORY, options), hosts)
    findings = store.judgment_records().findings
    for n, finding in enumerate(findings):
        if proposed is not None and n not in proposed:
            continue
        answer_finding(
            store,
            finding.id,
            FindingStatus.FIXED,
            note="the citation names its page now",
            author=Author(AGENT, "model-b", "s9"),
        )
    return options, [f.id for f in findings]


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("fixed", {"task": "f1", "why": "the citation names its page now"}),
        ("stands", {"task": "f1", "why": "the sentence still cites the work whole"}),
    ],
)
def test_a_round_that_settles_a_finding_runs_through_and_resumes(
    paper, tmp_path, monkeypatch, tool, args
) -> None:
    """The round's own answer settles the finding; replayed, between its
    pairs and when the same round is asked again, it is the round's own
    and is taken: the run reports the task settled, and asked again it
    asks nothing and files nothing twice."""
    store, state, _ = paper
    options, (finding,) = _filed_finding(paper, tmp_path, monkeypatch)
    settling = queue.Run(store, state, cq.FACTORY, {**options, "round": "r3"})
    hosts = [
        FakeQueueHost(working_through(_found)),
        FakeQueueHost(working_through(lambda served: (tool, args))),
    ]

    def runner() -> Runner:
        return Runner(
            hosts[-1], Resolver(store), 10.0, Rates(0.0, 1.0), review_round="r3"
        )

    report = run_queue(runner(), settling, hosts, (1.0, 9.0), wait_seconds=5.0)
    assert (report.settled, report.open, report.stopped) == (1, 0, None), report
    again = run_queue(runner(), settling)
    assert (again.settled, again.open, again.stopped) == (0, 0, None), again
    assert [len(h.sessions) for h in hosts] == [1, 1]
    held = store.judgment_records()
    round_answers = {a.key for a in store.answers() if a.review_round == "r3"} & {
        a.key for a in store.answers() if a.kind == cq.READER_KIND
    }
    assert len(round_answers) == 1
    if tool == "fixed":
        settled = [a for a in held.answers if a.answer in round_answers]
        assert [(a.finding, a.status) for a in settled] == [
            (finding, FindingStatus.FIXED)
        ]
    else:
        assert [f.reaffirms for f in held.findings if f.reaffirms] == [finding]


def test_a_review_whose_search_is_refused_stops_before_it_asks(
    paper, tmp_path, monkeypatch, capsys
) -> None:
    """A revoked key: the search of the cited works is refused. Asked, the
    review stops before any session, saying why; its dry run says so and
    prices on."""
    from axiomation import evidence
    from axiomation.cli import main
    from axiomation.commands import seating

    store, _, _ = paper
    refused = "search against c failed: Provided API key is invalid."
    monkeypatch.setattr(
        evidence, "sources_for", lambda paper, store: shelves(refused=refused)
    )
    args = ["review", str(tmp_path / "paper"), "--store", str(store.directory)]
    args += ["--session", "citationcheck"]
    capsys.readouterr()
    assert main([*args, "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert f"the cited works cannot be searched: the library: {refused}" in out
    assert ": 3 sites, 2 cited works" in out
    hosts = [
        FakeQueueHost(working_through(_found)),
        FakeQueueHost(working_through(_judged)),
    ]
    monkeypatch.setattr(seating, "queue_hosts", lambda seated: hosts)
    assert main([*args, "--budget", "5"]) != 0
    err = capsys.readouterr().err
    assert f"the cited works cannot be searched: the library: {refused}" in err
    assert "`AXIOMATION_LIBRARY=off` for one run" in err
    assert [len(h.sessions) for h in hosts] == [0, 0]
    assert not [a for a in store.answers() if a.kind == cq.READER_KIND]


def test_a_person_s_answer_meanwhile_is_not_the_round_s_own(
    paper, tmp_path, monkeypatch
) -> None:
    """The round reads its own answers as its own, never a person's: a
    finding a person answers while the round is open is refused to the
    round's `fixed`, and the round asked again no longer serves it."""
    store, state, _ = paper
    options, (finding,) = _filed_finding(paper, tmp_path, monkeypatch)
    settling = queue.Run(store, state, cq.FACTORY, {**options, "round": "r3"})
    with queue.opened(settling) as workflow:
        working = Working(workflow)
        (task,) = workflow.draw(working)
        answer_finding(store, finding, FindingStatus.POSTPONED)
        _, reader = workflow.steps
        why = {"task": "f1", "why": "the citation names its page now"}
        found = reader.answer(task, "fixed", why, working, True)
        assert isinstance(found, Refused)
        assert "by a person meanwhile" in found.problems[0]
    with pytest.raises(review.NoSubject), queue.opened(settling):
        pass


def test_a_finding_a_person_answers_meanwhile_moves_no_other_task(
    paper, tmp_path, monkeypatch
) -> None:
    """The round settles both findings; a person then answers the first.
    Asked again, the round asks nothing: the second keeps its number, and
    its answer replays."""
    store, state, _ = paper
    options, findings = _filed_finding(
        paper, tmp_path, monkeypatch, _judged_finding_both
    )
    assert len(findings) == 2
    settling = queue.Run(store, state, cq.FACTORY, {**options, "round": "r3"})
    fixed = {"why": "the citation names its page now"}
    hosts = [
        FakeQueueHost(working_through(_found)),
        FakeQueueHost(
            working_through(
                lambda served: ("fixed", {"task": served.split()[1], **fixed})
            )
        ),
    ]

    def runner() -> Runner:
        return Runner(
            hosts[-1], Resolver(store), 10.0, Rates(0.0, 1.0), review_round="r3"
        )

    report = run_queue(runner(), settling, hosts, (1.0, 9.0), wait_seconds=5.0)
    assert (report.settled, report.open) == (2, 0), report
    answer_finding(store, findings[0], FindingStatus.POSTPONED)
    again = run_queue(runner(), settling, hosts, (1.0, 9.0), wait_seconds=5.0)
    assert (again.settled, again.open, again.stopped) == (0, 0, None), again
    assert [len(h.sessions) for h in hosts] == [1, 1]


def test_another_round_asks_a_site_again_though_its_text_is_the_same(
    paper, tmp_path, monkeypatch
) -> None:
    """A person's proposed reading makes another round, which asks the
    site again with the proposal in its hints: the first round's answer to
    the same text is not this round's."""
    from axiomation.judgments import judge as file_judgment
    from axiomation.records import Depth, Verdict

    store, state, _ = paper
    options, _ = _filed_finding(paper, tmp_path, monkeypatch)
    read = next(
        j for j in store.judgment_records().judgments if j.aspect.value == "cited"
    )
    file_judgment(
        store,
        state,
        read.aspect,
        read.subject,
        Verdict.NOT_SHOWN,
        Depth.INFORMAL,
        "the page states another ratio",
        pages=[("able1998", 12)],
        sources=shelves(),
        propose=True,
    )
    asked = queue.Run(store, state, cq.FACTORY, {**options, "round": "r2"})
    with queue.opened(asked) as workflow:
        assert "s1" in [t.id for t in workflow.draw(Working(workflow))]

    def answer(served: str) -> tuple[str, dict]:
        task = served.split()[1]
        if task.startswith("f"):
            return "fixed", {"task": task, "why": "the citation names its page now"}
        return "judge", judge(task, PAGES[_work(served)])

    hosts = [
        FakeQueueHost(working_through(_found)),
        FakeQueueHost(working_through(answer)),
    ]
    runner = Runner(
        hosts[-1], Resolver(store), 10.0, Rates(0.0, 1.0), review_round="r2"
    )
    run_queue(runner, asked, hosts, (1.0, 9.0), wait_seconds=5.0)
    assert [len(h.sessions) for h in hosts] == [1, 1]
    r2 = [
        a.call["args"]["task"]
        for a in store.answers()
        if a.review_round == "r2" and a.kind == cq.READER_KIND
    ]
    assert "s1" in r2, r2


def _propose_fixed(store, finding: str) -> None:
    answer_finding(
        store,
        finding,
        FindingStatus.FIXED,
        note="the citation names its page now",
        author=Author(AGENT, "model-b", "s9"),
    )


def test_a_finding_that_joins_a_round_is_numbered_after_its_own(
    paper, tmp_path, monkeypatch
) -> None:
    """The round settles the one finding it holds; an agent's proposal then
    brings an earlier finding in. That one is numbered after the round's
    own, whose number and answer stay."""
    store, state, _ = paper
    options, findings = _filed_finding(
        paper, tmp_path, monkeypatch, _judged_finding_both, proposed={1}
    )
    settling = queue.Run(store, state, cq.FACTORY, {**options, "round": "r3"})
    fixed = {"why": "the citation names its page now"}
    reader = FakeQueueHost(
        working_through(lambda served: ("fixed", {"task": served.split()[1], **fixed}))
    )
    hosts = [FakeQueueHost(working_through(_found)), reader]
    runner = Runner(reader, Resolver(store), 10.0, Rates(0.0, 1.0), review_round="r3")
    assert run_queue(runner, settling, hosts, (1.0, 9.0), wait_seconds=5.0).settled == 1
    _propose_fixed(store, findings[0])
    with queue.opened(settling) as workflow:
        numbered = {i: q.read.finding.id for i, q in workflow.found.questions.items()}
    assert numbered == {"f1": findings[1], "f2": findings[0]}


def test_a_site_a_person_reads_meanwhile_moves_no_other_site(
    paper, tmp_path, monkeypatch
) -> None:
    """The round settles s1; a person then files their own reading of s2.
    Opened again, the round no longer asks s2, and s3 keeps its number."""
    from axiomation.judgments import judge as file_judgment
    from axiomation.records import Depth, Verdict
    from axiomation.runner.hosts import HostOverBudget

    store, state, _ = paper
    monkeypatch.setattr(cq.evidence, "sources_for", lambda paper, store: shelves())
    options = {
        "profile": "standard",
        "profiles": None,
        "paper": str(tmp_path / "paper"),
        "select": review.Selection.OPEN.value,
        "round": "r1",
        "find": True,
        "model": "fake/queue",
    }
    run = queue.Run(store, state, cq.FACTORY, options)
    with queue.opened(run) as workflow:
        s2 = workflow.found.subjects["s2"].subject
        s3 = workflow.found.subjects["s3"].subject
    reader = FakeQueueHost(working_through(_judged, stop_after=1), ends=HostOverBudget)
    hosts = [FakeQueueHost(working_through(_found)), reader]
    runner = Runner(reader, Resolver(store), 10.0, Rates(0.0, 1.0), review_round="r1")
    assert run_queue(runner, run, hosts, (1.0, 9.0), wait_seconds=5.0).settled == 1
    file_judgment(
        store,
        state,
        Aspect.CITED,
        s2,
        Verdict.VERIFIED,
        Depth.INFORMAL,
        f"able1998:12 {HELD}",
        pages=[("able1998", 12)],
        sources=shelves(),
    )
    with queue.opened(run) as workflow:
        drawn = [t.id for t in workflow.draw(Working(workflow))]
        assert workflow.found.subjects["s3"].subject == s3
    assert drawn == ["s1", "s3"]


def test_a_person_s_answer_between_pairs_loses_no_later_answer(
    paper, tmp_path, monkeypatch
) -> None:
    """One run, a task a pair: after the first pair a person answers the
    other finding. Each later pair's answer is filed, none asked twice:
    the run reads the store as it stands when it files each pair."""
    store, state, _ = paper
    options, findings = _filed_finding(
        paper, tmp_path, monkeypatch, _judged_finding_both
    )
    settling = queue.Run(
        store, state, cq.FACTORY, {**options, "round": "r3", "select": "all"}
    )
    asked: list[str] = []

    def answer(served: str) -> tuple[str, dict]:
        task = served.split()[1]
        asked.append(task)
        if task.startswith("f"):
            return "fixed", {"task": task, "why": "the citation names its page now"}
        return "judge", judge(task, PAGES[_work(served)])

    one = working_through(answer, stop_after=1)
    sessions = []

    def reader(call):
        said = one(call)
        sessions.append(said)
        if len(sessions) == 1:
            # the person answers the round's other finding meanwhile
            answer_finding(store, findings[1], FindingStatus.POSTPONED)
        return said

    hosts = [FakeQueueHost(working_through(_found)), FakeQueueHost(reader)]
    runner = Runner(
        hosts[-1], Resolver(store), 10.0, Rates(0.0, 1.0), review_round="r3"
    )
    report = run_queue(runner, settling, hosts, (1.0, 9.0), wait_seconds=5.0)
    assert (report.open, report.stopped) == (0, None), report
    assert sorted(asked) == ["f1", "s1", "s2", "s3"], asked
