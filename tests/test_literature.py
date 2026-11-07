"""The fieldresults and relatedwork sessions (of the field group): their
briefs and subagents, the cited works they are given, the check's
`literature`, `topic` and `suggest` lines (a work named, pages of a cited
work laid in or of a fetched one), a severity-3 `literature` finding filed
and never blocking, a suggestion filed once, and the rulings their hints
carry. Both answer by block, one input and one output, served their tools.

On the review tests' synthetic paper (`thm:bound`, "The output is
bounded."), the citation tests' (`s` and `m` citing `able1998` and
`baker1984`) and the check tests' (`x` standing unsupported). No model is
called and nothing is fetched: the paper's sources are a shelf that a fake
fetcher fills.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from axiomation import edits, queries, review, rulings, versions
from axiomation.judgments import seen
from axiomation.questions import REVIEW_TOOLS, load_template
from axiomation.questions import review_file as rf
from axiomation.records import Aspect, RulingKind
from axiomation.resolver import Resolver
from axiomation.runner import Runner
from runner.fakes import FakeHost
from test_check import AGENT
from test_check import built as unsupported_paper
from test_citations import built as citing_paper
from test_contribution import FOUND, answer, shelf_and_fetcher
from test_edits import built_state
from test_update import FLAT

RELATEDWORK = rf.Session.RELATEDWORK
FIELDRESULTS = rf.Session.FIELDRESULTS
CHECKED = (
    'checked thm:bound "The output is bounded." — the bound for gain maps — '
    "arxiv:2101.00001 states it"
)
LITERATURE = (
    f'find literature thm:bound work:{FOUND} 3 pages {FOUND}:2 "The output is '
    'bounded." — its Lemma 2 states the bound the paper uses bare — cite it '
    "at the claim, Lemma 2"
)
#: The relatedwork session's finding, under its block's heading.
LITERATURE_BY_BLOCK = (
    f'find literature work:{FOUND} 3 pages {FOUND}:2 "The output is bounded." — '
    "its Lemma 2 states the bound the paper uses bare — cite it at the claim, "
    "Lemma 2"
)
#: The fieldresults session's checked line, under its block's heading.
CHECKED_BY_BLOCK = (
    'checked "The output is bounded." — the bound for gain maps — able1998 states it'
)
#: The review tests' paper's one claim standing `unsupported`, and the
#: fieldresults session's line on it: every finished sheet owes one.
SHARP = "Next, the bound is sharp."
SHARP_CHECKED = (
    f'checked "{SHARP}" — read its block — the paper\'s own claim, not one of the field'
)


def done_answer(*lines: str) -> str:
    """The fieldresults session's lines under `thm:bound`, its line on the
    unsupported claim, and the done mark."""
    return answer(*lines) + answer(SHARP_CHECKED, rf.DONE, on="next-the-bound-is")


@pytest.fixture
def opened(tmp_path, capsys):
    store, _ = built_state(tmp_path, capsys)
    kept = versions.latest(store)
    assert kept is not None
    with edits.opened(store, kept) as found:
        yield found


def literature_brief(
    opened, session: rf.Session = RELATEDWORK, select=review.Selection.OPEN
) -> review.Brief:
    paper = Path(opened.store.directory).parent / "paper"
    return review.brief(
        opened,
        session,
        load_template(review.template_name(session)),
        None,
        select=select,
        directory=paper,
        forager="haiku",
    )


def sheet(found: review.Brief, *lines: str) -> str:
    assert found.question.workspace is not None
    return found.question.workspace.files[rf.REVIEW_FILE] + "\n".join(lines) + "\n"


def test_relatedwork_has_one_input_one_output_its_tools_and_its_forager(
    opened,
) -> None:
    """The paper situated in its field by block: the paper plain, nothing
    laid under its blocks, an empty answer, its check, cited reads and
    fetch served, no Bash, and its forager; its brief asks each part of the
    paper by a short list of kinds."""
    found = literature_brief(opened, RELATEDWORK)
    workspace = found.question.workspace
    assert workspace is not None and workspace.gate is not None
    assert found.subjects == {}
    assert sorted(workspace.files) == [rf.PAPER_FILE, rf.REVIEW_FILE]
    assert workspace.files[rf.REVIEW_FILE] == ""
    paper = workspace.files[rf.PAPER_FILE]
    assert not any(line.startswith(("→ ", "⊢ ", "↳ ")) for line in paper.split("\n"))
    assert workspace.tools == REVIEW_TOOLS and "Bash" not in workspace.tools
    served = workspace.served
    assert served is not None and served.fetches
    (forager,) = workspace.helpers
    assert forager.name == "forager" and forager.model == "haiku"
    assert forager.prompt == review.FORAGERS[RELATEDWORK].prompt
    assert "WebSearch" in forager.tools
    assert "cites_at" in found.spec
    brief = workspace.instructions
    assert "<!--" not in brief and "Map the places" in brief
    assert "axiomation" not in brief and "works.md" not in brief
    header = rf.render(RELATEDWORK, [])
    assert "Each part of the paper you judged needs no situating" in header
    assert rf.TOPIC_FORM in header and rf.SUGGEST_FORM in header
    assert "literature" in header and "novelty" not in header


def test_relatedwork_reads_its_citations_in_the_text_and_no_hints(tmp_path) -> None:
    """What each block cites is its check's (`cites_at`), never laid under
    it as the fieldresults session's is; an open `literature` finding is
    not laid in either: what the paper lacks no earlier finding says."""
    store, kept = citing_paper(tmp_path)
    view = queries.View(store, kept)
    assert review.laid_lines(view, RELATEDWORK, "") == {}
    assert any(
        line.startswith("↳ cites able1998")
        for lines in review.laid_lines(view, FIELDRESULTS, "").values()
        for line in lines
    )


def test_fieldresults_inventory_has_one_input_one_output_and_no_helper(
    opened,
) -> None:
    """The session by block is its inventory: the paper plain, an empty
    answer, its check and cited reads served, no Bash (it computes
    nothing) and no helper (its results are sourced by its queue,
    `sources.fieldresults_queue`). The claims standing `unsupported` are
    not laid in the paper, where they would stand for the reading it owes:
    its check holds them against the sheet once it is done."""
    found = literature_brief(opened, FIELDRESULTS)
    workspace = found.question.workspace
    assert workspace is not None and workspace.gate is not None
    assert found.subjects == {}
    assert sorted(workspace.files) == [rf.PAPER_FILE, rf.REVIEW_FILE]
    assert workspace.files[rf.REVIEW_FILE] == ""
    paper = workspace.files[rf.PAPER_FILE]
    assert not any(line.startswith(("→ ", "⊢ ")) for line in paper.split("\n"))
    assert "unsupported" not in paper and SHARP in paper
    view = queries.View(opened.store, opened.state)
    sharp = view.handle(view.resolve("next-the-bound-is"))
    assert found.spec[rf.POINTERS] == [[sharp, SHARP, ""]]
    assert workspace.tools == REVIEW_TOOLS and "Bash" not in workspace.tools
    assert workspace.helpers == ()
    # it searches nothing, so it fetches nothing: its queue does; the toy
    # paper cites nothing, so it reads no cited work either
    served = workspace.served
    assert served is not None and not served.fetches and served.reads is None
    brief = workspace.instructions
    assert "<!--" not in brief
    assert "make\nits inventory" in brief and "to source" in brief
    assert "forager" not in brief and "pending" not in brief
    assert "axiomation" not in brief and "works.md" not in brief
    assert "slices" not in brief and "fetch arxiv" not in brief
    header = rf.render(FIELDRESULTS, [])
    assert rf.CHECKED_FORM in header and "fetch arxiv" not in header


def test_the_pointers_are_each_claim_standing_unsupported_and_its_known_rulings(
    tmp_path,
) -> None:
    """A claim asserted bare is where a result of the field may be used
    with no citation: a pointer, with the `known` rulings that speak for
    it, whose reason names the standard result; a ruling of another kind
    is not named. None is laid in the paper or its hints."""
    store, kept = unsupported_paper(tmp_path)
    view = queries.View(store, kept)
    x = view.handle("x")

    def of_x(view: queries.View) -> list[str]:
        (found,) = [p for p in review.pointers(view) if p[0] == x]
        return found

    assert of_x(view) == [x, "Clearly f is extra.", ""]
    assert x not in review.laid_lines(view, FIELDRESULTS, "")
    on = rulings.subject(seen(store, kept), "x")
    rulings.rule(store, kept, on, RulingKind.INTERPRETATION, "a gloss", AGENT)
    assert of_x(queries.View(store, kept))[2] == ""
    rulings.rule(store, kept, on, RulingKind.KNOWN, "the triangle inequality", AGENT)
    view = queries.View(store, kept)
    assert of_x(view) == [
        x,
        "Clearly f is extra.",
        "ruled known by agent model-a: the triangle inequality",
    ]
    assert x not in review.hint_lines(view, FIELDRESULTS, ())


def test_a_known_ruling_changes_neither_the_paper_nor_the_key(tmp_path, capsys) -> None:
    """Through the brief: a `known` ruling filed on the claim standing
    `unsupported` reaches the check's pointers alone; the paper and the
    question's key are the same: it replays."""
    store, _ = built_state(tmp_path, capsys)

    def asked() -> review.Brief:
        with edits.opened(store, versions.latest(store)) as opened:
            return literature_brief(opened, FIELDRESULTS)

    before = asked()
    kept = versions.latest(store)
    assert kept is not None
    sharp_block = queries.View(store, kept).resolve("next-the-bound-is")
    on = rulings.subject(seen(store, kept), sharp_block)
    rulings.rule(store, kept, on, RulingKind.KNOWN, "a sharpness lemma", AGENT)
    after = asked()
    assert before.question.workspace is not None
    assert after.question.workspace is not None
    assert (
        after.question.workspace.files[rf.PAPER_FILE]
        == before.question.workspace.files[rf.PAPER_FILE]
    )
    assert after.question.key == before.question.key
    (pointer,) = after.spec[rf.POINTERS]
    assert pointer[2] == "ruled known by agent model-a: a sharpness lemma"


def test_once_done_the_check_hands_back_each_unread_pointer(opened) -> None:
    """The inventory is the reader's: while the sheet is unfinished, no
    pointer is a problem. Once it is done, each claim standing
    `unsupported` that no line on its block quotes is handed back, with the
    `known` ruling that speaks for it; a line quoting part of the claim
    reads it, and a line left to source is the queue's, no problem."""
    found = literature_brief(opened, FIELDRESULTS)
    spec = {**found.spec}
    ((sharp, words, _),) = spec[rf.POINTERS]
    spec[rf.POINTERS] = [[sharp, words, "ruled known by agent model-a: a lemma"]]
    noted = 'checked "The output is bounded." — a bound — to source'
    working = rf.checked(spec, answer(noted)).problems
    assert not any(p.startswith("unread") for p in working), working
    (unread,) = rf.checked(spec, answer(noted, rf.DONE)).problems
    assert unread.startswith(f'unread: `@@ {sharp}` "{SHARP}"')
    assert unread.endswith("another reader ruled known by agent model-a: a lemma")
    part = 'checked "the bound is sharp" — read its block — the paper\'s own claim'
    read = rf.checked(spec, answer(noted) + answer(part, rf.DONE, on=sharp))
    assert read.problems == ()


def test_it_is_given_every_work_the_paper_cites(tmp_path) -> None:
    """A result of the field the paper cites elsewhere is pointed at the
    work it already has: the session is given each cited work, as the
    citationcheck session is; the contribution session too, for prior art."""
    store, kept = citing_paper(tmp_path)
    view = queries.View(store, kept)
    assert review.laid_works(view, RELATEDWORK) == ["able1998", "baker1984"]
    assert review.laid_works(view, rf.Session.CONTRIBUTION) == [
        "able1998",
        "baker1984",
    ]


def scoped(opened, **how) -> list[review.Brief]:
    return review.scoped_briefs(
        opened,
        RELATEDWORK,
        load_template("review-relatedwork"),
        None,
        directory=Path(opened.store.directory).parent / "paper",
        forager="haiku",
        **how,
    ).briefs


def test_a_scoped_session_reads_a_part_of_the_text_with_every_file(opened) -> None:
    """A session that judges no subject is scoped by the text: by its
    sections, or by the blocks a scope names; each scope keeps every file,
    its scope bounding only where it files."""
    whole = literature_brief(opened)
    assert whole.question.workspace is not None
    files = whole.question.workspace.files.keys()
    by_section = scoped(opened, sections=True)
    assert len(by_section) > 1
    (named,) = scoped(opened, scopes=[["thm:bound"]])
    assert named.scope == "thm:bound"
    for brief in (*by_section, named):
        workspace = brief.question.workspace
        assert workspace is not None and workspace.files.keys() == files
        assert workspace.instructions.endswith(
            review.scope_note(brief.scope or "", judges=False)
        )
    assert "This session reads thm:bound; other sessions" in review.scope_note(
        "thm:bound", judges=False
    )
    with pytest.raises(review.ReviewError, match="no block is named nowhere"):
        scoped(opened, scopes=[["nowhere"]])


@pytest.mark.parametrize(
    ("line", "wanted"),
    [
        (
            'find literature 2 "The output is bounded." — bare — cite it',
            "then names the work it rests on",
        ),
        (
            'find literature work:baker1984 2 pages baker1984:4 "The output is '
            'bounded." — bare — cite it',
            "named as its `work:KEY` anchor (baker1984 is not)",
        ),
        (
            'find novelty work:able1998 2 "The output is bounded." — known — cite it',
            "the fieldresults session finds literature, not novelty",
        ),
        (f"fetch {FOUND}", "fetched by your `fetch` tool, not a line"),
    ],
)
def test_fieldresults_check_refuses_each_bad_line(opened, line, wanted) -> None:
    found = literature_brief(opened, FIELDRESULTS)
    # able1998 and baker1984 cited, able1998 alone laid in with pages
    spec = {
        **found.spec,
        "works": [*found.spec["works"], "able1998", "baker1984"],
        "laid": ["able1998"],
    }
    problems = rf.checked(spec, done_answer(CHECKED_BY_BLOCK, line)).problems
    assert any(wanted in p for p in problems), problems


def test_fieldresults_reads_a_finding_on_a_cited_work_s_page(opened) -> None:
    found = literature_brief(opened, FIELDRESULTS)
    # laid in with its pages, as the brief gives their statuses
    spec = {
        **found.spec,
        "works": ["able1998"],
        "laid": ["able1998"],
        "pages": {"able1998": {"12": "ok", "13": "content"}},
    }
    finding = (
        'find literature work:able1998 2 pages able1998:{} "The output is '
        'bounded." — Able states it on p. {} — cite it there'
    )
    result = rf.checked(spec, done_answer(CHECKED_BY_BLOCK, finding.format(12, 12)))
    assert result.problems == ()
    (read,) = rf.read(spec, result.text).finds
    assert read.aspect is Aspect.LITERATURE
    assert read.anchors == ("thm:bound", "work:able1998")
    assert read.pages == (("able1998", 12),)
    # a page that failed to read is never evidence, as the runner reads it
    failed = rf.checked(
        spec, done_answer(CHECKED_BY_BLOCK, finding.format(13, 13))
    ).problems
    assert any(
        "a failed read is never evidence: able1998:13 content" in p for p in failed
    ), failed
    unchecked = rf.checked(spec, f"{rf.DONE}\n").problems
    assert any(
        p.startswith("no checked line: list each result of the field")
        for p in unchecked
    ), unchecked


def test_a_literature_finding_is_filed_and_never_blocks(opened, monkeypatch) -> None:
    """The paper placed nowhere: a severity-3 finding, filed and never
    blocking; a later session is not shown it."""
    from axiomation import evidence

    found = literature_brief(opened)
    _, _, sources = shelf_and_fetcher()
    monkeypatch.setattr(evidence, "sources_for", lambda paper, store: sources)
    assert sources.fetch(FOUND).pages  # as its `fetch` tool fetched it
    written = answer(CHECKED_BY_BLOCK, LITERATURE_BY_BLOCK, rf.DONE)
    assert rf.checked(found.spec, written).problems == ()
    host = FakeHost(lambda p: "", edits={rf.REVIEW_FILE: lambda _: written})
    # as `review` asks it: its answer names the state its brief read
    runner = Runner(
        host, Resolver(opened.store), 10.0, FLAT, review_state=opened.state.id
    )
    report = runner.run(
        [found.question],
        review.checker(opened, found, sources, edits.agent(host.model, None)),
    )
    assert report.answered == (found.question.key,), report
    (finding,) = opened.store.judgment_records().findings
    assert (finding.aspect, finding.severity) == (Aspect.LITERATURE, 3)
    assert [a.work for a in finding.anchors if a.work] == [FOUND]
    assert [p.name for p in finding.pages] == [f"{FOUND}:2"]
    (read,) = queries.View(opened.store, opened.state).readings.findings
    assert read.open and not read.blocking
    # it read every block: `--all-again` builds the whole text's question
    asked = literature_brief(opened, select=review.Selection.ALL)
    again = asked.question.workspace
    assert again is not None and "↳" not in again.files[rf.PAPER_FILE]
    # not shown it, the session may name the work there again: neither the
    # finding nor a suggestion of its work is filed twice
    offered = f'suggest work:{FOUND} "The output is bounded." — its Lemma 2'
    lines = (CHECKED_BY_BLOCK, LITERATURE_BY_BLOCK, offered, rf.DONE)
    result = rf.checked(asked.spec, answer(*lines))
    assert result.problems == ()
    view = queries.View(opened.store, opened.state)
    records, problems = review.records(view, asked, result.sheet, sources)
    assert problems == []
    assert records.findings == () and records.suggestions == ()


def test_the_hints_carry_the_rulings_that_speak(tmp_path) -> None:
    """A `known` ruling's reason names the standard result: the hints a
    queue session reads show it, so its reader looks for the work that
    states it."""
    store, kept = unsupported_paper(tmp_path)
    view = queries.View(store, kept)
    column = rf.COLUMNS[rf.Session.CITATIONCHECK]
    assert "## Rulings on claims the paper argues nothing for\n\n(none)" in (
        review.hints_text(view, column, ())
    )
    at = seen(store, kept)
    rulings.rule(
        store,
        kept,
        rulings.subject(at, "x"),
        RulingKind.KNOWN,
        "the triangle inequality",
        AGENT,
    )
    hints = review.hints_text(queries.View(store, kept), column, ())
    assert (
        'x "Clearly f is extra."  ruled known by agent model-a: the triangle inequality'
    ) in hints


def test_a_finding_on_a_cited_work_s_page_is_filed_with_it_stamped(
    opened, monkeypatch
) -> None:
    """The session is served its cited works' reads and names them in the
    spec (`laid`); a finding by block that reads one of their pages is
    filed with the page read again and stamped, as a fetched one's is."""
    from dataclasses import replace

    from axiomation.evidence import Origin, Sources
    from test_evidence import PAGE, Shelf

    sources = Sources([Shelf(Origin.LIBRARY, {"able1998": {12: PAGE}})])
    monkeypatch.setattr(review, "laid_works", lambda view, session: ["able1998"])
    found = review.brief(
        opened,
        FIELDRESULTS,
        load_template("review-fieldresults"),
        sources,
        directory=Path(opened.store.directory).parent / "paper",
        forager="haiku",
    )
    assert found.spec["laid"] == ["able1998"]
    workspace = found.question.workspace
    assert workspace is not None and workspace.served is not None
    reads = workspace.served.reads
    assert reads is not None and reads.works == ("able1998",)
    assert reads.store == str(Path(opened.store.directory).resolve())
    # the toy paper cites nothing: the work is named as a cited one would be
    found = replace(found, spec={**found.spec, "works": ["able1998"]})
    result = rf.checked(
        found.spec,
        done_answer(
            CHECKED_BY_BLOCK,
            'find literature work:able1998 2 pages able1998:12 "The output is '
            'bounded." — Able states it on p. 12 — cite it there',
        ),
    )
    assert result.problems == ()
    view = queries.View(opened.store, opened.state)
    records, problems = review.records(view, found, result.sheet, sources)
    assert problems == []
    (finding,) = records.findings
    assert [p.name for p in finding.pages] == ["able1998:12"]


def test_a_work_it_could_not_fetch_is_filed_as_a_pointer(opened) -> None:
    """A work found by its DOI and not read is still named, with no pages:
    the finding says it was not read."""
    found = literature_brief(opened, FIELDRESULTS)
    pratt = "doi:10.1214/aoms/1177705990"
    result = rf.checked(
        found.spec,
        done_answer(
            CHECKED_BY_BLOCK,
            f'find literature work:{pratt} 2 "The output is bounded." — Pratt\'s '
            "theorem, not read here (no open copy), named by the forager — cite "
            "it at the claim",
        ),
    )
    assert result.problems == ()
    (finding,) = rf.read(found.spec, result.text).finds
    assert finding.anchors[1:] == (f"work:{pratt}",) and finding.pages == ()


# --- the work beside the paper: topics and suggestions --------------------------------

PRATT = "doi:10.1214/aoms/1177705990"
TOPIC = f'topic "The output is bounded." — bounds on gain maps — work:{PRATT}'
SUGGEST = (
    f'suggest work:{PRATT} "The output is bounded." — the standard bound a reader '
    "of the field looks for here"
)


def test_a_topic_s_expected_work_is_offered_or_the_sheet_goes_back(opened) -> None:
    """Each work the map expects at a block is cited there, offered or
    found; until then the sheet is handed back, told by the session's own
    line. Under its heading a line names no anchor: the heading's block
    is it."""
    found = literature_brief(opened)
    (problem,) = rf.checked(found.spec, answer(TOPIC, rf.DONE)).problems
    assert problem.startswith(f"`@@ thm:bound` '{TOPIC}': the topic expects ")
    assert f"expects work:{PRATT}" in problem
    offered = rf.checked(found.spec, answer(TOPIC, SUGGEST, rf.DONE))
    assert offered.problems == ()
    read = rf.read(found.spec, offered.text)
    (topic,) = read.topics
    assert (topic.anchor, topic.topic, topic.works) == (
        "thm:bound",
        "bounds on gain maps",
        (PRATT,),
    )
    (suggestion,) = read.suggests
    assert suggestion.anchors == ("thm:bound", f"work:{PRATT}")
    # a work the block already cites needs nothing more
    cited = {**found.spec, "cites_at": {"thm:bound": [PRATT]}}
    assert rf.checked(cited, answer(TOPIC, rf.DONE)).problems == ()
    # a topic line alone stands for the checked lines
    assert not any(
        "no checked line" in p
        for p in rf.checked(found.spec, answer(TOPIC, SUGGEST, rf.DONE)).problems
    )


def test_a_work_with_no_identifier_is_named_by_a_link(opened) -> None:
    """An old article or a book with neither DOI nor arXiv identifier is
    offered by a link to a public page of it, never read: a page of it is
    refused."""
    found = literature_brief(opened)
    link = "https://example.org/old-monograph"
    topic = f'topic "The output is bounded." — bounds — work:{link}'
    offered = f'suggest work:{link} "The output is bounded." — a monograph'
    result = rf.checked(found.spec, answer(topic, offered, rf.DONE))
    assert result.problems == ()
    (suggestion,) = rf.read(found.spec, result.text).suggests
    assert suggestion.anchors == ("thm:bound", f"work:{link}")
    paged = f'suggest work:{link} pages {link}:2 "The output is bounded." — why'
    problems = rf.checked(found.spec, answer(topic, paged, rf.DONE)).problems
    assert any("a suggestion's pages are" in p for p in problems), problems


def test_a_work_the_block_cites_is_not_offered_there(opened) -> None:
    found = literature_brief(opened)
    spec = {
        **found.spec,
        "works": [*found.spec["works"], "able1998"],
        "cites_at": {"thm:bound": ["able1998"]},
    }
    line = 'suggest work:able1998 "The output is bounded." — why'
    problems = rf.checked(spec, answer(line, rf.DONE)).problems
    assert any("thm:bound cites work:able1998 already" in p for p in problems), problems


@pytest.mark.parametrize(
    ("line", "wanted"),
    [
        ('suggest "The output is bounded." — why', "a suggestion is"),
        (
            f'suggest work:{PRATT} pages arxiv:9999.99999:2 "The output is bounded." '
            "— why",
            "a suggestion's pages are",
        ),
        (
            'topic "The output is bounded." — bounds — work:nowhere1999',
            "no block, group or cited work work:nowhere1999",
        ),
        ("topic — no words — work:x", "a topic line is"),
    ],
)
def test_the_check_refuses_each_bad_suggest_or_topic_line(opened, line, wanted) -> None:
    found = literature_brief(opened)
    problems = rf.checked(found.spec, answer(CHECKED_BY_BLOCK, line, rf.DONE)).problems
    assert any(wanted in p for p in problems), problems


def test_a_topic_or_suggest_line_goes_under_a_heading(opened) -> None:
    found = literature_brief(opened)
    problems = rf.checked(found.spec, f"{SUGGEST}\n{rf.DONE}\n").problems
    assert any("a suggest line goes under its block's heading" in p for p in problems)


def test_the_contribution_session_takes_neither_line(opened) -> None:
    from test_contribution import contribution

    found = contribution(opened)
    problems = rf.checked(
        found.spec, answer(CHECKED_BY_BLOCK, SUGGEST, rf.DONE)
    ).problems
    assert any("not a line this sheet takes" in p for p in problems), problems


def test_the_field_group_asks_its_three_sessions_in_order() -> None:
    """`field` asks the contribution, then the field's results, then the
    work beside it; a session named twice is asked once; `all` asks the
    seated sessions, in their order."""
    contribution = rf.Session.CONTRIBUTION
    seated = [rf.Session.ARGUMENT, RELATEDWORK]
    assert rf.sessions_named(["field"], seated) == [
        contribution,
        FIELDRESULTS,
        RELATEDWORK,
    ]
    assert rf.sessions_named(["relatedwork", "field"], seated) == [
        RELATEDWORK,
        contribution,
        FIELDRESULTS,
    ]
    assert rf.sessions_named(["all"], seated) == seated
    assert rf.sessions_named(["citation"], seated) == [rf.Session.CITATIONCHECK]


def test_fieldresults_takes_neither_a_topic_nor_a_suggestion(opened) -> None:
    found = literature_brief(opened, FIELDRESULTS)
    for line in (TOPIC, SUGGEST):
        problems = rf.checked(
            found.spec, answer(CHECKED_BY_BLOCK, line, rf.DONE)
        ).problems
        assert any("not a line this sheet takes" in p for p in problems), problems


def test_relatedwork_files_a_finding_only_where_the_paper_is_placed_nowhere(
    opened,
) -> None:
    """Below the paper placed nowhere, what a reader might expect is offered
    as a suggestion, never filed."""
    found = literature_brief(opened)
    finding = (
        f'find literature work:{PRATT} {{}} "The output is bounded." — the paper '
        "is placed nowhere in its field — place it"
    )
    low = rf.checked(found.spec, answer(CHECKED_BY_BLOCK, finding.format(2), rf.DONE))
    assert any("a suggestion, `suggest" in p for p in low.problems), low.problems
    placed = rf.checked(
        found.spec, answer(CHECKED_BY_BLOCK, finding.format(3), rf.DONE)
    )
    assert placed.problems == ()
