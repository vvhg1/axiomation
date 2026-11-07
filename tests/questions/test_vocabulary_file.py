"""The vocabulary map as a session on `vocabulary.md`, and its second round
on `vocabulary-check.md`: the files, their keys, their checks, the gate
the host runs, and the map they give; then the map asked and found again
in the store (`driver.map_vocabulary`)."""

from __future__ import annotations

import dataclasses
import io
import json
from pathlib import Path

import pytest

import record_file
from axiomation.driver import Templates, map_vocabulary
from axiomation.questions import (
    FILE_TOOLS,
    Stage,
    gates,
    load_template,
    vocabulary_file,
)
from axiomation.questions.vocabulary_file import (
    CHECK_FILE,
    CHECK_KIND,
    CHECK_TEMPLATE,
    KIND,
    SESSION_TEMPLATE,
    VOCABULARY_FILE,
    answers_of,
    check,
    check_question,
    main,
    mapped,
    read_answer,
    vocabulary_question,
)
from axiomation.reader import (
    EnvEntry,
    RefEntry,
    Vocabulary,
    VocabularyKind,
    read_paper,
)
from axiomation.records import RecordStore
from axiomation.resolver import Resolver
from axiomation.runner import Runner
from axiomation.runner.hosts import Rates
from runner.fakes import FakeHost

GOLDEN = Path(__file__).parent / "golden"
SESSION = load_template(SESSION_TEMPLATE)
CHECK = load_template(CHECK_TEMPLATE)
FLAT = Rates(input=0.0, output=1.0)

#: A Markdown paper with a frame its header declares, an environment it
#: never declares, and a reference command of its own.
HEADER = (
    "---\ntitle: Two boxes\nheader-includes:\n"
    "  - \\newenvironment{lawbox}{\\hrule}{\\hrule}\n"
    "  - \\newcommand{\\lemref}[1]{\\lemmaname~\\ref{lem:#1}}\n---\n\n"
)
BODY = (
    "\\begin{lemma}\\label{lem:key}\nThe gap shrinks.\n\\end{lemma}\n\n"
    "\\begin{lawbox}\n**First law.** Weight is kept.\n\nIt holds always.\n"
    "\\end{lawbox}\n\n"
    "\\begin{example}\nTake two coins.\n\\end{example}\n\n"
    "By \\lemref{key} the gap shrinks.\n"
)

#: `lawbox`'s declaration, and the same declaration changed.
LAWBOX_RULE = "{lawbox}{\\hrule}"
LAWBOX_RULES = "{lawbox}{\\hrule\\hrule}"

LAWBOX = "## environment `lawbox`"
EXAMPLE = "## environment `example`"
LEMREF = "## command `lemref`"


def write(tmp_path: Path, header: str = HEADER, body: str = BODY) -> Path:
    paper = tmp_path / "paper"
    paper.mkdir(parents=True, exist_ok=True)
    (paper / "paper.md").write_text(header + body, encoding="utf-8")
    return paper


def question_of(paper: Path):
    found = vocabulary_question(read_paper(paper), SESSION)
    assert found is not None
    return found


def fill(text: str, answers: dict[str, tuple[str, str]], slots=("kind", "evidence")):
    """`text` with each named entry's two slots answered."""
    out = []
    heading = None
    for line in text.split("\n"):
        if line.startswith("## "):
            heading = line
        if heading in answers:
            first, second = answers[heading]
            if line.startswith(f"- {slots[0]}:"):
                line = f"- {slots[0]}: {first}"
            elif line.startswith(f"- {slots[1]}:"):
                line = f"- {slots[1]}: {second}"
        out.append(line)
    return "\n".join(out)


GOOD = {
    LAWBOX: ("container", '"\\newenvironment{lawbox}{\\hrule}{\\hrule}"'),
    EXAMPLE: ("statement", '"Take two coins."'),
    LEMREF: (
        "reference prefix=lem: word=Lemma",
        '"\\newcommand{\\lemref}[1]{\\lemmaname~\\ref{lem:#1}}"',
    ),
}


def answered(question, answers=GOOD) -> str:
    return fill(question.workspace.files[VOCABULARY_FILE], answers)


def problems(question, text, paper) -> tuple[str, ...]:
    return check(question, text, read_paper(paper)).problems


# --- the file and its key ------------------------------------------------------------


def test_the_file_matches_its_golden(tmp_path) -> None:
    question = question_of(write(tmp_path))
    path = GOLDEN / "vocabulary-file.md"

    assert path.exists(), f"missing golden {path}; write it deliberately, reviewed"
    assert question.workspace.files[VOCABULARY_FILE] == path.read_text(
        encoding="utf-8"
    ), "vocabulary.md differs from its golden; review the change before updating it"
    assert (question.kind, question.stage) == (KIND, Stage.VOCABULARY)
    # the uses are quoted in the file: the session gets nothing else
    assert list(question.workspace.files) == [VOCABULARY_FILE]
    assert question.workspace.gate is not None
    # the brief is the whole system prompt, the tools a session's on files
    assert question.workspace.brief_alone
    assert question.workspace.tools == FILE_TOOLS


def test_a_paper_with_no_unknown_name_asks_nothing(tmp_path) -> None:
    paper = write(tmp_path, header="", body="\\begin{lemma}\nA.\n\\end{lemma}\n")

    assert vocabulary_question(read_paper(paper), SESSION) is None


def test_the_key_is_the_names_and_their_declarations_never_their_uses(
    tmp_path,
) -> None:
    first = question_of(write(tmp_path / "a"))
    inside = question_of(write(tmp_path / "b", body=BODY.replace("always", "often")))
    declared = question_of(
        write(tmp_path / "c", header=HEADER.replace(LAWBOX_RULE, LAWBOX_RULES))
    )
    another = question_of(
        write(tmp_path / "d", body=BODY + "\\begin{aside}\nA.\n\\end{aside}\n")
    )

    assert inside.key == first.key
    assert declared.key != first.key
    assert another.key != first.key


# --- the check ----------------------------------------------------------------------


def test_a_whole_answer_reads_and_gives_the_map(tmp_path) -> None:
    paper = write(tmp_path)
    question = question_of(paper)
    text = answered(question)

    assert problems(question, text, paper) == ()
    found = mapped(read_answer(question, text))
    assert (found.environments, found.references, found.labels) == (
        (
            EnvEntry("lawbox", VocabularyKind.CONTAINER),
            EnvEntry("example", VocabularyKind.STATEMENT),
        ),
        (RefEntry("lemref", False, "lem:", "Lemma"),),
        (),
    )
    # each answer kept as written, under the declaring text it answered
    assert [(a.name, a.declaration, a.kind) for a in found.answers] == [
        ("lawbox", "\\newenvironment{lawbox}{\\hrule}{\\hrule}", "container"),
        ("example", None, "statement"),
        (
            "lemref",
            "\\newcommand{\\lemref}[1]{\\lemmaname~\\ref{lem:#1}}",
            "reference prefix=lem: word=Lemma",
        ),
    ]
    assert Vocabulary.of(found.answers) == found


@pytest.mark.parametrize(
    ("change", "says"),
    [
        ({EXAMPLE: ("?", "?")}, "answer its `- kind:` line"),
        ({EXAMPLE: ("theorem", '"Take two coins."')}, "the kind is one of"),
        ({EXAMPLE: ("statement example", '"Take two coins."')}, "at most one standard"),
        ({EXAMPLE: ("statement", '"Take three coins."')}, "the paper's own words"),
        ({LAWBOX: ("reference", '"lawbox"')}, "the kind is one of"),
        ({LEMREF: ("none", '"\\lemref{key}"')}, "it is a reference"),
        ({LEMREF: ("reference sideways", '"\\lemref{key}"')}, "a reference takes"),
    ],
)
def test_a_wrong_answer_is_refused(tmp_path, change, says) -> None:
    paper = write(tmp_path)
    question = question_of(paper)

    found = problems(question, answered(question, {**GOOD, **change}), paper)

    assert any(says in problem for problem in found), found


def test_an_entry_left_out_added_or_rewritten_is_refused(tmp_path) -> None:
    paper = write(tmp_path)
    question = question_of(paper)
    text = answered(question)
    lost = text.replace(LAWBOX, "## environment `lawbox2`")
    rewritten = text.replace("- found: macro", "- found: use")

    assert any("is missing" in p for p in problems(question, lost, paper))
    assert any("names no entry" in p for p in problems(question, lost, paper))
    assert any("change only" in p for p in problems(question, rewritten, paper))


def test_a_command_every_use_of_which_names_a_label_is_a_reference(tmp_path) -> None:
    paper = write(
        tmp_path,
        header="",
        body="\\begin{lemma}\\label{lem:key}\nA.\n\\end{lemma}\n"
        "By \\lemmaref{lem:key}.\n",
    )
    question = question_of(paper)
    none = fill(
        question.workspace.files[VOCABULARY_FILE],
        {"## command `lemmaref`": ("none", '"\\lemmaref{lem:key}"')},
    )

    assert any("it is a reference" in p for p in problems(question, none, paper))


def test_a_command_found_by_a_label_less_its_prefix_may_be_a_word(tmp_path) -> None:
    paper = write(
        tmp_path,
        header="",
        body="\\begin{lemma}\\label{lem:heads}\nA.\n\\end{lemma}\n"
        "The \\emph{heads} are the first elements.\n",
    )
    question = question_of(paper)
    none = fill(
        question.workspace.files[VOCABULARY_FILE],
        {"## command `emph`": ("none", '"The \\emph{heads} are the first"')},
    )

    assert problems(question, none, paper) == ()


def test_an_environment_declared_opening_a_statement_wraps_it(tmp_path) -> None:
    paper = write(
        tmp_path,
        header="---\ntitle: T\nheader-includes:\n"
        "  - \\newenvironment{mythm}{\\begin{theorem}\\begin{center}}"
        "{\\end{center}\\end{theorem}}\n---\n\n",
        body="\\begin{mythm}\nA.\n\\end{mythm}\n",
    )
    question = question_of(paper)
    container = fill(
        question.workspace.files[VOCABULARY_FILE],
        {"## environment `mythm`": ("container", '"\\begin{mythm}"')},
    )

    wraps = fill(
        question.workspace.files[VOCABULARY_FILE],
        {"## environment `mythm`": ("wraps theorem", '"\\begin{mythm}"')},
    )
    unknown = fill(
        question.workspace.files[VOCABULARY_FILE],
        {"## environment `mythm`": ("wraps adjustwidth", '"\\begin{mythm}"')},
    )

    assert any("it wraps it" in p for p in problems(question, container, paper))
    assert problems(question, wraps, paper) == ()
    assert any("one the reader knows" in p for p in problems(question, unknown, paper))


# --- the second round ---------------------------------------------------------------


def none_answer(question) -> str:
    return answered(question, {**GOOD, EXAMPLE: ("none", '"\\begin{example}"')})


def test_no_none_no_second_round(tmp_path) -> None:
    paper = write(tmp_path)
    question = question_of(paper)

    assert (
        check_question(
            read_paper(paper), read_answer(question, answered(question)), CHECK
        )
        is None
    )


def test_the_second_round_shows_each_none_in_its_words(tmp_path) -> None:
    paper = write(tmp_path)
    question = question_of(paper)
    second = check_question(
        read_paper(paper), read_answer(question, none_answer(question)), CHECK
    )
    assert second is not None
    path = GOLDEN / "vocabulary-check.md"

    assert (second.kind, second.stage) == (CHECK_KIND, Stage.VOCABULARY_CHECK)
    assert path.exists(), f"missing golden {path}; write it deliberately, reviewed"
    assert second.workspace.files[CHECK_FILE] == path.read_text(encoding="utf-8")
    assert second.workspace.brief_alone and second.workspace.tools == FILE_TOOLS


def test_a_confirm_keeps_the_none_and_an_overturn_replaces_it(tmp_path) -> None:
    paper = write(tmp_path)
    document = read_paper(paper)
    question = question_of(paper)
    first = none_answer(question)
    second = check_question(document, read_answer(question, first), CHECK)
    assert second is not None
    shown = second.workspace.files[CHECK_FILE]
    confirm = fill(shown, {EXAMPLE: ("confirm", "?")}, ("verdict", "evidence"))
    overturn = fill(
        shown,
        {EXAMPLE: ("overturn statement", '"Take two coins."')},
        ("verdict", "evidence"),
    )
    unfounded = fill(
        shown,
        {EXAMPLE: ("overturn statement", '"Take ten coins."')},
        ("verdict", "evidence"),
    )

    assert check(second, confirm, document).problems == ()
    assert mapped(
        read_answer(question, first), read_answer(second, confirm)
    ).environments[1] == EnvEntry("example", VocabularyKind.NONE)
    assert check(second, overturn, document).problems == ()
    assert mapped(
        read_answer(question, first), read_answer(second, overturn)
    ).environments[1] == EnvEntry("example", VocabularyKind.STATEMENT)
    assert check(second, unfounded, document).problems != ()


# --- the gate -----------------------------------------------------------------------


def gate(tmp_path, question, text, monkeypatch) -> str:
    spec = tmp_path / "spec.json"
    spec.write_text(question.workspace.gate.spec, encoding="utf-8")
    edited = tmp_path / VOCABULARY_FILE
    edited.write_text(text, encoding="utf-8")
    monkeypatch.setattr("sys.stdin", io.StringIO("{}"))
    out = io.StringIO()
    monkeypatch.setattr("sys.stdout", out)
    assert main([str(spec), str(edited)]) == 0
    return out.getvalue()


def test_the_gate_blocks_a_file_that_does_not_read_a_few_times(
    tmp_path, monkeypatch
) -> None:
    paper = write(tmp_path)
    question = question_of(paper)
    shown = question.workspace.files[VOCABULARY_FILE]

    assert gate(tmp_path, question, answered(question), monkeypatch) == ""
    blocked = [
        gate(tmp_path, question, shown, monkeypatch) for _ in range(gates.ROUNDS)
    ]
    assert all(json.loads(b)["decision"] == "block" for b in blocked)
    assert gate(tmp_path, question, shown, monkeypatch) == ""
    assert (tmp_path / gates.TRIES).read_text(encoding="utf-8") == str(gates.ROUNDS)


# --- the map asked and found again --------------------------------------------------


def test_the_map_is_asked_once_then_read_from_the_store(tmp_path) -> None:
    paper = write(tmp_path)
    document = read_paper(paper)
    store = RecordStore(tmp_path / "store")
    edits = {
        VOCABULARY_FILE: lambda text: fill(
            text, {**GOOD, EXAMPLE: ("none", '"\\begin{example}"')}
        ),
        CHECK_FILE: lambda text: fill(
            text,
            {EXAMPLE: ("overturn statement", '"Take two coins."')},
            ("verdict", "evidence"),
        ),
    }
    fake = FakeHost(lambda prompt: "", edits=edits)
    runner = Runner(fake, Resolver(store), 10.0, FLAT, jobs=1)

    asked = map_vocabulary(document, store, Templates.shipped(), runner)
    again = map_vocabulary(document, store, Templates.shipped())

    assert fake.calls == 2
    assert asked.vocabulary == again.vocabulary
    assert asked.vocabulary is not None
    assert (
        EnvEntry("example", VocabularyKind.STATEMENT) in asked.vocabulary.environments
    )
    assert [q.stage for q in again.questions] == [
        Stage.VOCABULARY,
        Stage.VOCABULARY_CHECK,
    ]
    mapped_document = read_paper(paper, vocabulary=asked.vocabulary)
    assert [(r.command, r.targets) for r in mapped_document.references] == [
        ("lemref", ("lem:key",))
    ]


@pytest.mark.usefixtures("develop_mode")
def test_the_map_is_made_from_the_entries_filed_not_from_the_text(
    tmp_path, monkeypatch
) -> None:
    paper = write(tmp_path)
    document = read_paper(paper)
    store = RecordStore(tmp_path / "store")
    edits = {
        VOCABULARY_FILE: lambda text: fill(
            text, {**GOOD, EXAMPLE: ("none", '"\\begin{example}"')}
        ),
        CHECK_FILE: lambda text: fill(
            text,
            {EXAMPLE: ("overturn statement", '"Take two coins."')},
            ("verdict", "evidence"),
        ),
    }
    runner = Runner(
        FakeHost(lambda prompt: "", edits=edits), Resolver(store), 10.0, FLAT, jobs=1
    )
    asked = map_vocabulary(document, store, Templates.shipped(), runner)
    first, second = asked.questions

    # each answer's entries are its records, as its check read them
    filed = store.vocabulary_of(first.key)
    assert answers_of(filed) == read_answer(
        first, record_file.text(store.directory, store.authoritative()[first.key])
    )
    assert store.vocabulary_of(second.key)

    def unread(question, raw):
        raise AssertionError("an answer's text read again")

    monkeypatch.setattr(vocabulary_file, "read_answer", unread)
    again = map_vocabulary(document, store, Templates.shipped())
    assert again.vocabulary == asked.vocabulary


def test_an_unanswered_map_is_none_and_a_paper_without_names_is_empty(
    tmp_path,
) -> None:
    store = RecordStore(tmp_path / "store")
    plain = write(tmp_path / "plain", header="", body="Text.\n")

    unanswered = map_vocabulary(read_paper(write(tmp_path)), store, Templates.shipped())
    empty = map_vocabulary(read_paper(plain), store, Templates.shipped())

    assert unanswered.vocabulary is None and len(unanswered.questions) == 1
    assert empty.vocabulary == Vocabulary() and empty.questions == ()


# --- the map carried onto the next version -------------------------------------------


def test_a_carried_map_asks_only_a_new_name(tmp_path) -> None:
    store = RecordStore(tmp_path / "store")
    old = read_paper(write(tmp_path / "old"))
    first = question_of(write(tmp_path / "old"))
    kept = mapped(read_answer(first, answered(first)))
    body = BODY + "\n\\begin{exercise}\nAdd them.\n\\end{exercise}\n"
    new = read_paper(write(tmp_path / "new", body=body))

    edits = {
        VOCABULARY_FILE: lambda text: fill(
            text, {"## environment `exercise`": ("statement", '"Add them."')}
        )
    }
    fake = FakeHost(lambda prompt: "", edits=edits)
    runner = Runner(fake, Resolver(store), 10.0, FLAT, jobs=1)
    asked = map_vocabulary(new, store, Templates.shipped(), runner, kept=kept)

    (question,) = asked.questions
    # only the new name is shown; the three answered ones are carried
    assert [
        h
        for h in question.workspace.files[VOCABULARY_FILE].split("\n")
        if h.startswith("## ")
    ] == ["## environment `exercise`"]
    assert asked.vocabulary is not None
    assert [a.name for a in asked.vocabulary.answers] == [
        "lawbox",
        "example",
        "lemref",
        "exercise",
    ]
    # the same paper with every name answered asks nothing
    assert map_vocabulary(old, store, Templates.shipped(), kept=kept).questions == ()
    assert map_vocabulary(old, store, Templates.shipped(), kept=kept).vocabulary == kept


def test_a_changed_declaration_is_asked_again_and_a_gone_name_drops(tmp_path) -> None:
    first = question_of(write(tmp_path / "old"))
    kept = mapped(read_answer(first, answered(first)))
    header = HEADER.replace(LAWBOX_RULE, LAWBOX_RULES)
    body = BODY.replace("By \\lemref{key} the gap shrinks.\n", "")
    header = header.replace(
        "  - \\newcommand{\\lemref}[1]{\\lemmaname~\\ref{lem:#1}}\n", ""
    )
    new = read_paper(write(tmp_path / "new", header=header, body=body))

    found = map_vocabulary(
        new, RecordStore(tmp_path / "store"), Templates.shipped(), kept=kept
    )

    (question,) = found.questions
    shown = question.workspace.files[VOCABULARY_FILE]
    # `lawbox`'s declaration changed: asked again; `example` carried;
    # `lemref` is gone, and so is its answer
    assert [h for h in shown.split("\n") if h.startswith("## ")] == [LAWBOX]
    assert found.vocabulary is None
    store = RecordStore(tmp_path / "store")
    edits = {
        VOCABULARY_FILE: lambda text: fill(
            text,
            {
                LAWBOX: (
                    "container",
                    '"\\newenvironment{lawbox}{\\hrule\\hrule}{\\hrule}"',
                )
            },
        )
    }
    runner = Runner(
        FakeHost(lambda prompt: "", edits=edits), Resolver(store), 10.0, FLAT, jobs=1
    )
    again = map_vocabulary(new, store, Templates.shipped(), runner, kept=kept)
    assert again.vocabulary is not None
    assert [(a.name, a.declaration) for a in again.vocabulary.answers] == [
        ("example", None),
        ("lawbox", "\\newenvironment{lawbox}{\\hrule\\hrule}{\\hrule}"),
    ]


def test_a_redefinition_added_asks_its_own_entry_and_keeps_the_first(
    tmp_path,
) -> None:
    """`lemref` redefined after its first definition: an entry for each
    declaring text, numbered; the carried answer keeps the first, and only
    the new one is asked; the map then answers both, each under its own
    declaration."""
    first = question_of(write(tmp_path / "old"))
    kept = mapped(read_answer(first, answered(first)))
    again = "\\renewcommand{\\lemref}[1]{#1}"
    header = HEADER.replace("---\n\n", f"  - {again}\n---\n\n")
    new = read_paper(write(tmp_path / "new", header=header))
    second = "## command `lemref` (definition 2 of 2)"
    store = RecordStore(tmp_path / "store")
    edits = {
        VOCABULARY_FILE: lambda text: fill(text, {second: ("none", f'"{again}"')}),
        # the second round confirms the `none`
        CHECK_FILE: lambda text: fill(
            text, {second: ("confirm", "?")}, slots=("verdict", "evidence")
        ),
    }
    runner = Runner(
        FakeHost(lambda prompt: "", edits=edits), Resolver(store), 10.0, FLAT, jobs=1
    )

    found = map_vocabulary(new, store, Templates.shipped(), runner, kept=kept)

    question, checked = found.questions
    shown = question.workspace.files[VOCABULARY_FILE]
    assert [h for h in shown.split("\n") if h.startswith("## ")] == [second]
    assert checked.kind == CHECK_KIND
    assert "- found: redefined" in shown
    assert found.vocabulary is not None
    assert [
        (a.name, a.declaration, a.kind)
        for a in found.vocabulary.answers
        if a.name == "lemref"
    ] == [
        (
            "lemref",
            "\\newcommand{\\lemref}[1]{\\lemmaname~\\ref{lem:#1}}",
            "reference prefix=lem: word=Lemma",
        ),
        ("lemref", again, "none"),
    ]
    # every use stands after the redefinition: none is read as a reference
    read = read_paper(tmp_path / "new" / "paper", vocabulary=found.vocabulary)
    assert not [r for r in read.references if r.command == "lemref"]


def test_a_person_s_answer_lapses_when_its_declaration_changes(tmp_path) -> None:
    first = question_of(write(tmp_path / "old"))
    model = mapped(read_answer(first, answered(first)))
    kept = Vocabulary.of(
        [
            dataclasses.replace(a, by="person") if a.name == "lawbox" else a
            for a in model.answers
        ]
    )
    header = HEADER.replace(LAWBOX_RULE, LAWBOX_RULES)
    new = read_paper(write(tmp_path / "new", header=header))
    same = read_paper(write(tmp_path / "same"))
    store = RecordStore(tmp_path / "store")

    changed = map_vocabulary(new, store, Templates.shipped(), kept=kept)
    unchanged = map_vocabulary(same, store, Templates.shipped(), kept=kept)

    assert [a.name for a in changed.lapsed] == ["lawbox"]
    assert unchanged.lapsed == () and unchanged.vocabulary == kept
