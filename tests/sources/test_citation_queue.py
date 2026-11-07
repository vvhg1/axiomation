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
from axiomation.records import AGENT, FindingStatus, RecordStore
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
