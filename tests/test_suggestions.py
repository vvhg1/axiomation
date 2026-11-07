"""Suggestions (docs/model.md § 8): a work offered to the author at a
place, filed by the relatedwork session or by `suggest`, read at a state,
answered `taken` or `declined`, and shown by `show`, `open`, `review
--report` and a later session's hints; never status.

On the review tests' synthetic paper (`thm:bound`, "The output is
bounded."). No model is called and nothing is fetched: the paper's sources
are a shelf that a fake fetcher fills.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from axiomation import edits, queries, review, suggestions, versions
from axiomation.cli import main
from axiomation.judgments import Author, JudgmentError, anchored, seen
from axiomation.questions import review_file as rf
from axiomation.records import AnchorKind, SuggestionStatus
from axiomation.resolver import Resolver
from axiomation.runner import Runner
from runner.fakes import FakeHost
from test_contribution import FOUND, answer, shelf_and_fetcher
from test_edits import built_state
from test_literature import literature_brief
from test_update import FLAT

OFFERED = (
    f'suggest work:{FOUND} pages {FOUND}:2 "The output is bounded." — its Lemma 2 '
    "is the bound a reader of the field looks for here"
)
TOPIC = f'topic "The output is bounded." — bounds on gain maps — work:{FOUND}'
PERSON = Author("person", "Ada")


@pytest.fixture
def opened(tmp_path, capsys):
    store, _ = built_state(tmp_path, capsys)
    kept = versions.latest(store)
    assert kept is not None
    with edits.opened(store, kept) as found:
        yield found


def ask(opened) -> tuple[review.Brief, object]:
    """The relatedwork session asked and its answer filed: one topic, its
    work offered, read where its `fetch` tool fetched it."""
    from axiomation import evidence

    found = literature_brief(opened)
    _, _, sources = shelf_and_fetcher()
    assert sources.fetch(FOUND).pages
    written = answer(TOPIC, OFFERED, rf.DONE)
    host = FakeHost(lambda p: "", edits={rf.REVIEW_FILE: lambda _: written})
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(evidence, "sources_for", lambda paper, store: sources)
        # as `review` asks it: its answer names the state its brief read
        runner = Runner(
            host, Resolver(opened.store), 10.0, FLAT, review_state=opened.state.id
        )
        report = runner.run(
            [found.question],
            review.checker(opened, found, sources, edits.agent(host.model, None)),
        )
    return found, report


def filed(opened) -> suggestions.SuggestionRead:
    found, report = ask(opened)
    assert report.answered == (found.question.key,), report
    (read,) = queries.View(opened.store, opened.state).suggestions
    return read


def test_a_work_offered_at_a_place_again_is_not_filed_again(opened) -> None:
    """The session is not told what was offered before: a repeat of a
    suggestion that stands, open or answered, is dropped when filed."""
    from axiomation import evidence

    first = filed(opened)
    suggestions.answer(opened.store, first.suggestion.id, SuggestionStatus.DECLINED)
    # it read every block: `--all-again` builds the whole text's question
    found = literature_brief(opened, select=review.Selection.ALL)
    _, _, sources = shelf_and_fetcher()
    assert sources.fetch(FOUND).pages
    result = rf.checked(found.spec, answer(TOPIC, OFFERED, rf.DONE))
    view = queries.View(opened.store, opened.state)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(evidence, "sources_for", lambda paper, store: sources)
        records, problems = review.records(view, found, result.sheet, sources)
    assert problems == [] and records.suggestions == ()


def test_the_session_files_a_suggestion_with_its_page_and_no_finding(opened) -> None:
    read = filed(opened)
    held = opened.store.judgment_records()
    assert held.findings == ()
    (offered,) = held.suggestions
    assert [a.work for a in offered.anchors if a.work] == [FOUND]
    assert [p.name for p in offered.pages] == [f"{FOUND}:2"]
    assert read.live and read.open and read.works == (FOUND,)
    assert "Lemma 2" in read.suggestion.why
    # never status: no subject's status reads it
    view = queries.View(opened.store, opened.state)
    assert all(not s.findings for s in view.readings.statuses)


def test_an_answer_decides_it_and_the_latest_speaks(opened) -> None:
    read = filed(opened)
    store = opened.store
    suggestions.answer(
        store, read.suggestion.id[:14], SuggestionStatus.DECLINED, "no", PERSON
    )
    suggestions.answer(store, read.suggestion.id, SuggestionStatus.TAKEN, None, PERSON)
    (now,) = suggestions.suggestions(store, opened.state)
    assert not now.open and now.answer is not None
    assert now.answer.status is SuggestionStatus.TAKEN
    # answered, it is the author's no more: `open` lists it nowhere
    view = queries.View(store, opened.state)
    report = queries.open_report(view)
    assert report.sections["suggestion"] == []
    with pytest.raises(JudgmentError, match="names 0 suggestions"):
        suggestions.answer(store, "nothing", SuggestionStatus.TAKEN)


def test_a_suggestion_sits_on_a_place_and_names_a_work(opened) -> None:
    at = seen(opened.store, opened.state)
    view = queries.View(opened.store, opened.state)
    block = anchored(at, AnchorKind.BLOCK, view.resolve("thm:bound"))
    work = anchored(at, AnchorKind.WORK, work=FOUND)
    with pytest.raises(JudgmentError, match="names the work it suggests"):
        suggestions.suggestion_record(at, [block], "why")
    with pytest.raises(JudgmentError, match="sits on a block or group first"):
        suggestions.suggestion_record(at, [work, block], "why")
    with pytest.raises(JudgmentError, match="says why"):
        suggestions.suggestion_record(at, [block, work], "  ")


def test_show_the_report_and_the_hints_list_it(opened) -> None:
    read = filed(opened)
    view = queries.View(opened.store, opened.state)
    shown = queries.show(view, "thm:bound")
    (item,) = shown.sections["suggestions"]
    assert item.line.startswith(f"suggestion {read.suggestion.id}  open  at thm:bound")
    assert f"work:{FOUND}" in item.line
    report = queries.review_report(view)
    assert "1 open suggestions, each the author's to take or decline" in report.head
    (listed,) = report.sections["suggested citations"]
    assert listed.data["works"] == [FOUND]
    hints = review.hints_text(view, rf.COLUMNS[rf.Session.FINDINGS], ())
    assert f"## Open suggestions\n\n{item.line}" in hints
    # `open` lists it as the author's to answer, on its block
    (opened_item,) = queries.open_report(view).sections["suggestion"]
    assert opened_item.line == item.line
    assert opened_item.data["handle"] == "thm:bound"
    assert opened_item.data["suggestion"]["works"] == [FOUND]
    assert "answer-suggestion" in opened_item.data["clears"]


def test_suggest_and_answer_suggestion_from_the_command_line(opened, capsys) -> None:
    store = opened.store
    paper = str(Path(store.directory).parent / "paper")
    where = ["--store", str(store.directory)]
    capsys.readouterr()
    assert (
        main(
            [
                "suggest",
                paper,
                *where,
                "--on",
                "thm:bound",
                "--work",
                FOUND,
                "--why",
                "the standard bound",
                "--person",
                "Ada",
            ]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert f"work:{FOUND} at thm:bound" in out
    (offered,) = store.judgment_records().suggestions
    assert (
        main(
            [
                "answer-suggestion",
                paper,
                *where,
                offered.id[:14],
                "--as",
                "declined",
                "--person",
                "Ada",
            ]
        )
        == 0
    )
    assert f"suggestion {offered.id} answered declined" in capsys.readouterr().out
    assert main(["review", paper, *where, "--report"]) == 0
    assert "open suggestions" not in capsys.readouterr().out


def test_a_suggestion_lapses_on_its_anchor_s_text_alone(tmp_path) -> None:
    """Walked as a finding on how the paper reads: an edge added to its
    block leaves it live; its block's words changed lapse it, shown lapsed,
    still the author's to answer (docs/model.md § 8)."""
    from test_judgments import (
        BLOCKS,
        EDGES,
        TEXT,
        built,
        edge,
        edited,
        graph,
        same,
        updated,
    )

    store, first = built(tmp_path)
    at = seen(store, first)
    on_t = [
        anchored(at, AnchorKind.BLOCK, "t"),
        anchored(at, AnchorKind.WORK, work="doi:10.1000/x"),
    ]
    suggestions.suggest(store, first, on_t, "the standard bound")
    second = edited(store, first, [*EDGES, edge("t", "uses", "r")])
    (read,) = suggestions.suggestions(store, second)
    assert read.live and read.open
    text = TEXT.replace("Then f is bounded.", "Then f is small.")
    blocks = [BLOCKS[0], ("t", "Then f is small."), *BLOCKS[2:]]
    third = updated(
        store,
        first,
        text,
        graph(text, blocks),
        same("d", "t", "p", "r", but={"t": "changed"}),
    )
    (read,) = suggestions.suggestions(store, third)
    assert not read.live and read.open
    # unanswered and lapsed, `open` lists it under `lapsed`, with its cause
    report = queries.open_report(queries.View(store, third))
    assert report.sections["suggestion"] == []
    (lapsed,) = [
        i for i in report.sections["lapsed"] if i.line.startswith("suggestion ")
    ]
    assert lapsed.data["handle"] == "t" and lapsed.data["cause"] is not None
    # an author's pointer, never a reading: `changes` lists no lapse of it,
    # and reads past it
    old, new = queries.View(store, first), queries.View(store, third)
    changed = queries.changes_report(store, old, new)
    assert not any(
        i.line.startswith("suggestion ")
        for items in changed.sections.values()
        for i in items
    )


def test_a_group_member_with_nothing_to_ask_is_skipped(
    opened, capsys, tmp_path
) -> None:
    """`--session field` asks each member that has text to read or a
    finding to settle and skips the others, as seated sessions are; a
    session named by itself is refused. The relatedwork session read every
    block as it stands, and has nothing to settle."""
    # the fieldresults session is asked only where the cited works can be
    # read: the `library` extra, its corpus
    pytest.importorskip("qdrant_client")
    ask(opened)
    store = opened.store
    paper = str(Path(store.directory).parent / "paper")
    where = ["--store", str(store.directory)]
    capsys.readouterr()
    group = ["review", paper, *where, "--session", "field"]
    assert main([*group, "--dry-run", "--prompts", str(tmp_path / "dry")]) == 0
    out = capsys.readouterr().out
    assert "fieldresults: " in out and "relatedwork: state " in out
    assert out.count("; not asked") == 1
    alone = ["review", paper, *where, "--session", "relatedwork"]
    assert main([*alone, "--dry-run", "--prompts", str(tmp_path / "dry2")]) != 0


def test_an_unanswered_suggestion_sits_on_its_first_anchor_that_holds(
    tmp_path,
) -> None:
    """On `p`, the work, then `r`: `p` deleted, its open item sits on `r`,
    a work's anchor carrying no node of the walk."""
    from test_judgments import BLOCKS, EDGES, TEXT, built, graph, same, updated

    store, first = built(tmp_path)
    at = seen(store, first)
    anchors = [
        anchored(at, AnchorKind.BLOCK, "p"),
        anchored(at, AnchorKind.WORK, work="doi:10.1000/x"),
        anchored(at, AnchorKind.BLOCK, "r"),
    ]
    suggestions.suggest(store, first, anchors, "the standard bound")
    text = TEXT.replace(" By the gain, f is bounded.", "")
    blocks = [b for b in BLOCKS if b[0] != "p"]
    edges = [e for e in EDGES if e["source"] != "p"]
    links = [*same("d", "t", "r"), (["p"], [], "deleted", None)]
    second = updated(store, first, text, graph(text, blocks, edges), links)
    report = queries.open_report(queries.View(store, second))
    (item,) = report.sections["lapsed"] + report.sections["suggestion"]
    assert item.data["handle"] == "r"
    assert item.data["anchor"]["kind"] == "block"


def test_a_new_work_or_a_new_place_is_filed_and_one_line_once(opened) -> None:
    """Beside a work offered at a place: another work there is filed, the
    same work at another place is filed, and two lines offering one work
    at one place are filed once."""
    from axiomation import evidence

    bound = filed(opened).suggestion.anchors[0].node
    found = literature_brief(opened, select=review.Selection.ALL)
    _, _, sources = shelf_and_fetcher()
    assert sources.fetch(FOUND).pages
    other = "https://example.org/an-old-monograph"
    twice = f'suggest work:{other} "The output is bounded." — an older account'
    elsewhere = f'suggest work:{FOUND} "Next, the bound is sharp." — its sharpness'
    written = answer(TOPIC, OFFERED, twice, twice) + answer(
        elsewhere, rf.DONE, on="next-the-bound-is"
    )
    view = queries.View(opened.store, opened.state)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(evidence, "sources_for", lambda paper, store: sources)
        result = rf.checked(found.spec, written)
        assert result.problems == ()
        records, problems = review.records(view, found, result.sheet, sources)
    assert problems == []
    offered = {suggestions.works_of(s): s.anchors[0].node for s in records.suggestions}
    assert offered[(other,)] == bound
    assert offered[(FOUND,)] not in (None, bound)
    assert len(records.suggestions) == 2


def test_show_says_a_work_the_paper_cites_nowhere_is_so(opened, capsys) -> None:
    """A suggestion or a finding may name an entry of the bibliography the
    paper cites nowhere (`carter2001`): `show` says so, apart from a work
    found on the web, and its data marks it."""
    store = opened.store
    paper = str(Path(store.directory).parent / "paper")
    where = ["--store", str(store.directory), "--person", "Ada"]
    works = ["carter2001", FOUND]
    offer = ["--on", "thm:bound", "--why", "the standard bound"]
    assert main(["suggest", paper, *where, *offer, *_each("--work", works)]) == 0
    found = ["--aspect", "literature", "--on", "thm:bound", "--severity", "1"]
    found += ["--problem", "uncited", "--fix", "cite it"]
    assert main(["find", paper, *where, *found, *_each("--work", works)]) == 0
    capsys.readouterr()
    view = queries.View(store, opened.state)
    shown = queries.show(view, "thm:bound")
    (offered,) = shown.sections["suggestions"]
    (filed,) = shown.sections["findings"]
    # a finding's anchors are named where it is shown whole
    (read,) = [f for f in view.readings.findings if f.finding.id == filed.data["id"]]
    whole = queries.finding_report(view, read)
    for line in (offered.line, whole.head[0]):
        assert "work:carter2001 (cited nowhere in the paper)" in line
        assert f"work:{FOUND} (" not in line
    assert offered.data["cited_nowhere"] == ["carter2001"]
    assert [a.get("cited_nowhere", False) for a in filed.data["anchors"]] == [
        False,
        True,
        False,
    ]


def _each(flag: str, values: list[str]) -> list[str]:
    return [x for v in values for x in (flag, v)]
