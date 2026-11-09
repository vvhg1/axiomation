"""An update: a text edit on a given cut, on the
synthetic paper with a first roles pass, its edges written by a fake host
on OLD and the update session answered by another on NEW. No model is
called.

OLD's store is built as a run builds it (`review_cut` on the given cut, its
export written), its identity answer made by `axiomation diff --cut
--store`, and the update asked on NEW's directory alone.
"""

from __future__ import annotations

import dataclasses
import json
import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

import record_file
from axiomation import run_state
from axiomation import versions as versions_
from axiomation.cli import BUILD_STAGES, main
from axiomation.closure import impact_reach
from axiomation.cutter.model import Role
from axiomation.driver import ReviewSettings, review_cut, review_payload
from axiomation.exchange import dumps
from axiomation.questions import Stage
from axiomation.questions import edge_sheet as es
from axiomation.questions.edges_file import (
    EDGES_FILE,
    LINES_KICKOFF,
    UPDATE_KICKOFF,
    _requoted,
    check,
    update_reach,
    written_lines,
)
from axiomation.reader import document_hash
from axiomation.records import (
    IDENTITY,
    AnswerRecord,
    CutOp,
    ImpactClass,
    RecordError,
    RecordStore,
    TemplateRef,
)
from axiomation.resolver import Resolver
from axiomation.run_state import RunFile
from axiomation.runner import Runner
from axiomation.runner.hosts import Rates
from axiomation.update import (
    PRODUCER,
    Reason,
    UpdateError,
    UpdateSettings,
    dropped_start_flags,
    followup,
    followup_question,
    mechanical_reopen,
    plan,
    run,
    update_payload,
)
from axiomation.update_queue import Seats, block_line, numbered_lines
from questions.review_paper import HIDDEN, MAIN, Paper, make_paper
from questions.test_edges_file import LEGEND, WRITTEN, done, written
from questions.test_roles_file import first_pass
from runner.fakes import FakeHost, FakeQueueHost, as_lines, working_through

FLAT = Rates(input=0.0, output=1.0)
FILES = ("extra.tex", "main.tex")
SETTINGS = ReviewSettings(
    chunk=None, overlap=0, stages=(Stage.EDGES,), files=FILES, given_cut=True
)
UPDATE = UpdateSettings(files=FILES, limit=None)
#: The command's settings without a profile: from the cutter,
#: every step's stage, the profile's defaults.
CUTTER = UpdateSettings(
    files=FILES, review=ReviewSettings(stages=BUILD_STAGES, files=FILES)
)

SETTING = "we-fix-a-field"
DEFINITION = "def:gain"
PROOF_BLOCK = "by-definition-def:gain-the"
SHARP = "next-the-bound-is"
CLOSING = "closing-words-of-the"
PROOF = "group:by-definition-def:gain-the"

#: OLD's sheet for the conditional reach: the definition uses the setting's
#: part [K] (assumption content), the proof uses the definition's [gain]
#: (definition content), and the bound's sharpness uses the proof's
#: paragraph whole (a justification: asked). No ambient frame.
CHAIN = {
    **WRITTEN,
    DEFINITION: ['→ uses we-fix-a-field [K] · "The gain is the ratio"'],
    SHARP: [
        f'→ uses {PROOF_BLOCK} · "Next, the bound is sharp."',
        '⊢ unsupported · "Next, the bound is sharp."',
    ],
}
CHAIN_LEGEND = [
    '  we-fix-a-field [K]: "We fix a field of scalars."',
    '  def:gain [gain]: "The gain is the ratio of output to input."',
]
#: The setting's second sentence edited: its block changed, its part [K]
#: found again.
EDITED_SETTING = MAIN.replace(
    "A second sentence of the setting.", "A second sentence of the setting, edited."
)


@dataclass
class Versions:
    old: Paper
    new: Paper
    store: RecordStore
    old_sheet: str


def build_old(
    root: Path, lines=WRITTEN, header=LEGEND, old_main: str = MAIN
) -> tuple[Paper, RecordStore]:
    """OLD, its edges written by a fake host on its first-pass cut, the
    export beside the store."""
    paper = make_paper(root / "old", main=old_main)
    store = RecordStore(root / "store")
    fake = FakeHost(
        lambda p: "", edits={EDGES_FILE: lambda t: written(t, lines, header)}
    )
    review = review_cut(
        paper.document,
        first_pass(paper),
        store,
        SETTINGS,
        Runner(fake, Resolver(store), 10.0, FLAT),
    )
    assert review.complete and review.edges_answer is not None
    (root / "old.json").write_text(
        dumps(review_payload(paper.document, review, SETTINGS)), encoding="utf-8"
    )
    return paper, store


def versions(
    tmp_path: Path, main_text: str, lines=WRITTEN, header=LEGEND, old_main: str = MAIN
) -> Versions:
    """OLD built, NEW written, and the identity answer made as `diff --cut
    --store` makes it."""
    old, store = build_old(tmp_path, lines, header, old_main)
    new = make_paper(tmp_path / "new", main=main_text)
    args = ["diff", str(old.root), str(new.root), "--displays", "in-paragraph"]
    args += ["--cut", str(tmp_path / "old.json"), "--store", str(store.directory)]
    assert main(args) == 0
    (sheet,) = [a.raw for a in store.authoritative().values() if a.kind == "edges"]
    return Versions(old, new, store, sheet)


def add_lines(text: str, extra: dict[str, list[str]]) -> str:
    """`text` with `extra`'s lines added at the end of each owner's own."""
    out: list[str] = []
    owner: str | None = None

    def flush() -> None:
        if owner in extra:
            at = len(out)
            while at and out[at - 1] == "":
                at -= 1
            out[at:at] = extra[owner]

    for line in text.split("\n"):
        if m := (es.BLOCK.match(line) or es.GROUP.match(line) or es.END.match(line)):
            flush()
            owner = None if es.GROUP.match(line) else m.group(1)
        out.append(line)
    flush()
    return "\n".join(out)


def answering(
    deltas: dict[str, list[str]] | None = None,
    edit: Callable[[str], str] = lambda t: t,
) -> Callable[[str], str]:
    """The update session's edit: `edit`, then the Δ lines, then every
    owner marked done."""

    def answer(text: str) -> str:
        return done(add_lines(edit(text), deltas or {}))

    return answer


def host(answer: Callable[[str], str]) -> FakeHost:
    return FakeHost(lambda p: "", edits={EDGES_FILE: answer})


def assumptions(text: str) -> str:
    """A roles session's edit: every block asked an assumption."""
    return re.sub(r"^⟦(b\d+)⟧", r"⟦\1 assumption⟧", text, flags=re.MULTILINE)


def stepping(
    answer: Callable[[str], str],
    cut: Callable[[str], str] = lambda t: t,
    roles: Callable[[str], str] = assumptions,
) -> FakeHost:
    """A host for an update from the cutter: step 1's cut.md as
    `cut` (unchanged: no correction), the roles as `roles`, the groups as
    shown, the edges as `answer`."""
    return FakeHost(
        lambda p: "",
        edits={
            "cut.md": cut,
            "roles.md": roles,
            "groups.md": lambda t: t,
            EDGES_FILE: answer,
        },
    )


def update(
    v: Versions,
    fake: FakeHost | None = None,
    settings=UPDATE,
    working: RunFile | None = None,
    **options,
):
    """The update through `fake`; an update of one session, through a queue
    host answering each task as `fake` edits the whole sheet
    (`QueueAnswers`): complete, every block that edit changed was served.
    Its run holds its answers' texts, as the command's does, so a replay
    reads them back."""
    if working is None:
        working = RunFile(
            v.store.directory, "update", document_hash(v.new.document), ""
        )
    runner = (
        None
        if fake is None
        else Runner(fake, Resolver(v.store, run=working), 10.0, FLAT)
    )
    if settings.per_stage_update_above_recut_blocks is not None:
        # the one session's queue reads the paper, as the command gives it;
        # a replay reads it too, so its questions are the run's
        settings = dataclasses.replace(settings, paper=settings.paper or v.new.root)
    answers = None
    if fake is not None and EDGES_FILE in fake.edits:
        answers = queue_answers(v, settings, fake.edits[EDGES_FILE], **options)
    if answers is not None:
        fake_queue = FakeQueueHost(working_through(answers))
        settings = dataclasses.replace(settings, edges_queue=Seats((fake_queue,)))
    made = run(v.new.document, v.store, settings, runner, run=working, **options)
    if answers is not None and made.complete:
        assert not answers.unserved(), f"edits no task served: {answers.unserved()}"
    return made


def queue_answers(
    v: Versions,
    settings: UpdateSettings,
    answer: Callable[[str], str],
    identity: str | None = None,
    **_,
) -> QueueAnswers | None:
    """The one session's queue answered as `answer` edits the carried sheet
    (`QueueAnswers`), where the update is one session; None where it is
    not."""
    _, made = plan(v.new.document, v.store, settings, identity)
    if not made.single_session or made.question is None:
        return None
    return QueueAnswers(shown(made.question), answer)


#: What an update's queue task asks, by its kind: its served text's
#: second line starts so.
UPDATE_TASKS = (
    "a changed block",
    "an added block",
    "does it rely on what the edit wrote?",
    "a block whose meaning moved by rule",
    "read its lines again",
)


class QueueThen:
    """One queue host's answers for `axiomation update`, which asks the
    update's queue and then the queues it ends with: the update's tasks,
    told by what they ask (`UPDATE_TASKS`), as `answer` edits the carried
    sheet (`update`, a `QueueAnswers`); every other task as `then` answers
    it."""

    def __init__(
        self,
        v: Versions,
        answer: Callable[[str], str],
        then: Callable[[str], tuple[str, dict]],
    ) -> None:
        one = UpdateSettings(
            files=FILES, limit=None, per_stage_update_above_recut_blocks=1000
        )
        _, made = plan(v.new.document, v.store, one)
        self.update = QueueAnswers(shown(made.question), answer)
        self.then = then

    def __call__(self, served: str) -> tuple[str, dict]:
        asks = served.split("\n")[1]
        return (
            self.update(served) if asks.startswith(UPDATE_TASKS) else self.then(served)
        )


class QueueAnswers:
    """The update's queue answered as `answer` edits the whole `carried`
    sheet: each task's block given the lines, the Δs, the parts and the
    roles `answer` writes for it, as a typed answer on what the task
    shows; `unserved`, the blocks `answer` changed that no task served."""

    def __init__(self, carried: str, answer: Callable[[str], str]) -> None:
        self.carried, self.target = carried, answer(carried)
        self.wanted, self.was = es.parse(self.target), es.parse(carried)
        self.served: set[str] = set()

    def unserved(self) -> list[str]:
        def written(sheet: str, h: str) -> tuple:
            sheet_of = es.parse(sheet)
            return (
                numbered_lines(sheet, h),
                block_line(sheet, h),
                sorted(w for (t, _), w in sheet_of.legend.items() if t == h),
            )

        changed = {
            h
            for h in self.wanted.texts
            if written(self.target, h) != written(self.carried, h)
        }
        return sorted(changed - self.served)

    def __call__(self, served: str) -> tuple[str, dict]:
        target, wanted, was = self.target, self.wanted, self.was
        ident = served.split()[1]
        h = ident.split("#")[0]
        self.served.add(h)
        rows = served.split("\n")
        start = rows.index("its lines:") + 1 if "its lines:" in rows else len(rows)
        now = []
        for row in rows[start:]:
            m = re.match(r"^L\d+ (.*)$", row)
            if m is None:
                break
            now.append(m.group(1))
        own = [x for x in numbered_lines(target, h) if not es.DELTA.match(x)]
        args: dict = {"task": ident}
        off = [
            f"L{n}"
            for n, x in enumerate(now, 1)
            if not es.DELTA.match(x) and x not in own
        ]
        on = [x for x in own if x not in now]
        if off:
            args["take_off"] = off
        if on:
            args["put_on"] = on
        kind = rows[1]
        if kind.startswith(
            ("a changed block", "a block whose meaning moved")
        ) and not any(es.DELTA.match(x) for x in now):
            deltas = [d for d in wanted.deltas if d.source == h]
            whole = [d for d in deltas if not d.label]
            if whole:
                args["delta"] = {"class": whole[0].cls, "note": whole[0].note}
            parts = [
                {"part": d.label, "class": d.cls, "note": d.note}
                for d in deltas
                if d.label
            ]
            if parts:
                args["part_deltas"] = parts
        if "it was re-cut" in served:
            line = block_line(target, h) or ""
            args["roles"] = line[len(f"⟦{h} ") : line.index("⟧")]
        added = [
            {"label": label, "words": words}
            for (t, label), words in wanted.legend.items()
            if t == h and (t, label) not in was.legend
        ]
        if added:
            args["parts"] = added
        dropped = [
            label
            for (t, label) in was.legend
            if t == h and (t, label) not in wanted.legend
        ]
        if dropped:
            args["drop_parts"] = dropped
        return "answer", args


def shown(question) -> str:
    return question.workspace.files[EDGES_FILE]


def refused(question, text: str, document) -> tuple[str, ...]:
    found = check(question, text, document)
    assert not found.valid
    return found.problems


def without_qualifies(text: str) -> str:
    """The setting's qualifies line taken off: the rebased cut leaves the
    changed setting without its assumption role until the command asks it."""
    return text.replace('→ qualifies def:gain · "We fix a field of scalars."\n', "")


# --- what session 1 reopens --------------------------------------------------------


def test_an_insertion_asks_only_its_reopen_set_and_keeps_the_rest_verbatim(
    tmp_path,
) -> None:
    main_text = MAIN.replace(
        "\\section{Setting}", "An opening line.\n\n\\section{Setting}"
    )
    v = versions(tmp_path, main_text)
    _, made = plan(v.new.document, v.store, UPDATE)

    assert list(made.reopened) == ["an-opening-line", CLOSING]
    assert made.reopened["an-opening-line"].why == "it is new in this version"
    assert made.reopened[CLOSING].why == "its scope:paper edge: the paper changed"
    assert made.conditional == {}
    sheet = shown(made.question)
    before, after = written_lines(v.old_sheet), written_lines(sheet)
    for handle, lines in before.items():
        if handle != CLOSING:
            assert after[handle] == lines, handle  # the done mark among them
    assert f"⟦done {CLOSING}⟧" not in sheet
    # every id after the insertion shifted; the handles did not
    assert v.new.id_at("We fix a field") != v.old.id_at("We fix a field")

    # a same block's lines are fixed
    moved_line = answering(
        edit=lambda t: t.replace("⊢ justified ·", "⊢ conjectured ·")
    )(sheet)
    assert any(
        p.startswith("thm:bound: its lines are fixed")
        for p in refused(made.question, moved_line, v.new.document)
    )
    fake = host(answering())
    found = update(v, fake)
    assert found.complete and fake.calls == 1
    assert fake.prompts[0].startswith(UPDATE_KICKOFF)


def test_a_markup_edit_asks_nothing(tmp_path) -> None:
    v = versions(tmp_path, MAIN.replace("\\section{Setting}", "\\section*{Setting}"))
    fake = host(answering())

    found = update(v, fake)

    assert found.plan.question is None and fake.calls == 0
    assert found.complete and found.answer is None
    payload = update_payload(v.new.document, found, v.store)
    assert payload["review"]["update"]["reopened"] == []
    exported = json.loads((tmp_path / "old.json").read_text(encoding="utf-8"))
    # the edge half is the identity answer's carried sheet; each edge OLD's
    # author's
    assert payload["edges_answer"] == found.identity.key
    assert [e["provenance"] for e in payload["edges"]] == [
        e["provenance"] for e in exported["edges"]
    ]
    assert [e["kind"] for e in payload["edges"]] == [
        e["kind"] for e in exported["edges"]
    ]
    assert payload["paper_version"] == document_hash(v.new.document)


def test_a_generalizes_flag_is_carried_onto_the_new_state(tmp_path) -> None:
    lines = {
        **WRITTEN,
        "we-fix-a-field": [
            *WRITTEN["we-fix-a-field"],
            '→ about def:gain generalizes · "We fix a field of scalars."',
        ],
    }
    markup = MAIN.replace("\\section{Setting}", "\\section*{Setting}")
    v = versions(tmp_path, markup, lines)

    found = update(v, host(answering()))

    assert found.plan.question is None and found.complete
    payload = update_payload(v.new.document, found, v.store)
    (wider,) = [e for e in payload["edges"] if e.get("generalizes")]
    assert wider["kind"] == "about"


def test_a_markup_edit_under_a_quote_requotes_it_and_asks_nothing(tmp_path) -> None:
    """Markup changed inside an edge's evidence and a legend part's words:
    the carried lines read the new text, and nothing is asked (as `*every*`
    to `_every_`)."""
    v = versions(
        tmp_path,
        MAIN.replace("a field of scalars", "a \\textbf{field} of scalars").replace(
            "The gain is the ratio", "The \\emph{gain} is the ratio"
        ),
    )
    fake = host(answering())

    found = update(v, fake)

    assert found.plan.question is None and fake.calls == 0
    assert found.complete
    payload = update_payload(v.new.document, found, v.store)
    assert payload["review"]["update"]["reopened"] == []
    texts = {e["evidence"][0]["text"] for e in payload["edges"] if e["evidence"]}
    assert "We fix a \\textbf{field} of scalars." in texts
    assert "We fix a field of scalars." not in texts
    assert [e["anchor"] for e in payload["entries"]] == [
        "The \\emph{gain} is the ratio of output to input."
    ]


def test_a_part_whose_words_cannot_be_carried_across_markup_is_asked(tmp_path) -> None:
    """A legend part's words stand twice in OLD and the markup changed both:
    no image is sure, so the part's block and every edge naming it are
    asked, its legend line open to be quoted anew, rather
    than a carried sheet that cannot read."""
    said = "The gain is the ratio of output to input."
    twice = (
        "The \\emph{gain} is the ratio of output to input, the \\emph{gain} a ratio."
    )
    old_main = MAIN.replace(said, twice)
    header = ['  def:gain [gain]: "\\emph{gain}"', "Pervasive: we-fix-a-field"]
    v = versions(
        tmp_path,
        old_main.replace("\\emph{gain}", "\\textbf{gain}"),
        header=header,
        old_main=old_main,
    )
    made = update(v).plan
    assert "cannot be carried across its markup change" in made.reopened[DEFINITION].why
    assert PROOF_BLOCK in made.reopened

    fake = host(
        answering(
            edit=lambda t: t.replace(
                '[gain]: "\\emph{gain}"', '[gain]: "\\textbf{gain}"'
            )
        )
    )
    found = update(v, fake)

    assert found.complete and fake.calls == 1
    payload = update_payload(v.new.document, found, v.store)
    assert [e["anchor"] for e in payload["entries"]] == ["\\textbf{gain}"]


@pytest.mark.parametrize(
    ("words", "old", "new", "now"),
    [
        ("a b c", "x a b c y", "x a b c z", "a b c"),
        ("*every* set", "that *every* set is", "that _every_ set is", "_every_ set"),
        ("show *every*", "show *every* set", "show _every_ set", "show _every_"),
        ("every* set", "show *every* set", "show _every_ set", "every_ set"),
        ("$a$ and", "if $a$ and b", "if $\\alpha$ and b", "$\\alpha$ and"),
        ("gone", "a gone b", "a b", None),
        ("twice*", "twice* and twice*", "twice_ and twice_", None),
        # maths beside the quote, gained, lost or swapped
        (
            "is compact",
            "Then $K$ \\emph{$K$} is compact.",
            "Then \\emph{$K$} $K$ is compact.",
            "is compact",
        ),
        ("\\emph{$a$}", "$a$ \\emph{$a$} _x_", "\\emph{$a$} $a$ _x_", None),
        (
            "$b$ ba $a$",
            "x $b$ ba $a$ y",
            "x \\emph{$b$ ba} $a$ y",
            "\\emph{$b$ ba} $a$",
        ),
        ("x and y", "a x and y z", "\\emph{a x} and \\emph{y z}", None),
        ("open map $a$", "x open map $a$", "x **open** map $a$", "**open** map $a$"),
        # an image the alignment would get wrong
        (
            "\\emph{compact}",
            "We call a set \\emph{open} if it is compact, and \\emph{compact} if it "
            "is complete; an open set is \\emph{complete} when it is open.",
            "We call a set \\emph{open} if it is compact, and compact if it is "
            "\\emph{complete}; an open set is \\emph{complete} when it is open.",
            None,
        ),
        (
            "\\emph{and}",
            "for map \\emph{every} for and \\emph{and} \\emph{let}",
            "for map every for and and \\emph{let}",
            None,
        ),
        ("\\emph{b} a", "a \\emph{b} a b", "a b a \\emph{b}", None),
        (
            "the value x",
            "the value x is here",
            "the value *x* is here",
            "the value *x*",
        ),
        (
            "\\emph{the} set of",
            "Let \\emph{the} set of points be the set of limits.",
            "Let the set of points be \\emph{the} set of limits.",
            None,
        ),
        (
            "open open",
            "of open open open and",
            "of open open \\emph{open} and",
            "open open",
        ),
        ("and y", "x and y y $y$ of", "x and $y$ $y$ y of", None),
        # a quote inside one run of maths, the run rewritten: its image is
        # the same run's (an earlier edit)
        (
            "g(x)=h(x,{(a^{2}+b^{2})^{1/2}})",
            "we take the norm\n$$g(x)=h(x,{(a^{2}+b^{2})^{1/2}}),$$\nwhere",
            "we take the norm\n$$g(x)=h(x,\\sqrt{a^{2}+b^{2}}),$$\nwhere",
            "g(x)=h(x,\\sqrt{a^{2}+b^{2}})",
        ),
        ("a^{1/2}", "let $a^{1/2}$ be", "let $\\sqrt{a}$ be", "\\sqrt{a}"),
        # ... but never into another run
        ("a^{2}", "let $a^{2}$ and $b$ be", "let $c$ and $a^{2}$ be", None),
    ],
)
def test_a_quote_is_carried_through_the_alignment(words, old, new, now) -> None:
    assert _requoted(words, old, new) == now


def test_a_segment_start_the_rebase_dropped_is_a_rule_flag(tmp_path) -> None:
    v = versions(tmp_path, MAIN.replace("\\section{Setting}", "\\section*{Setting}"))
    found = update(v, host(answering()))
    block = found.plan.cut.blocks[0].id
    found.plan.raw["dropped_starts"] = [
        {"block": block, "start": "We assume", "cls": "markup"}
    ]

    (flag,) = update_payload(v.new.document, found, v.store)["review"]["flags"]

    assert flag["id"] == "rule-part-1" and flag["signal"] == "part"
    assert flag["stage"] == IDENTITY and flag["by"] == "rule"
    assert [b["id"] for b in flag["blocks"]] == [block]
    assert flag["note"] == "segment start not re-found after a markup edit"
    assert flag["evidence"] == [{"text": "We assume", "at": None}]
    assert dropped_start_flags({}) == ()


def test_a_moved_block_reopens_only_its_position_sources(tmp_path) -> None:
    definition = (
        "\\begin{definition}\\label{def:gain}\n"
        "The gain is the ratio of output to input.\n\\end{definition}\n"
    )
    main_text = MAIN.replace(definition, "").replace(
        "Closing words of the results.\n",
        "Closing words of the results.\n\n" + definition,
    )
    lines = {
        **WRITTEN,
        CLOSING: [],
        "thm:bound": [
            '→ uses def:gain position · "The output is bounded."',
            *WRITTEN["thm:bound"],
        ],
    }
    v = versions(tmp_path, main_text, lines)
    _, made = plan(v.new.document, v.store, UPDATE)

    assert {link.cls.value for link in made.lineage.links} == {"same", "moved"}
    assert list(made.reopened) == ["thm:bound"]
    assert (
        made.reopened["thm:bound"].why
        == "its [position] edge onto def:gain, which moved"
    )
    assert made.reopened["thm:bound"].origin == DEFINITION
    assert made.conditional == {}


def test_mechanical_reopen_names_blocks_by_id_for_the_lab(tmp_path) -> None:
    v = versions(
        tmp_path,
        MAIN.replace("\\section{Setting}", "An opening line.\n\n\\section{Setting}"),
    )
    _, made = plan(v.new.document, v.store, UPDATE)
    from axiomation.questions.edges_file import records

    found = mechanical_reopen(
        made.lineage, records(made.carried.sheet, made.handles), made.cut
    )
    assert set(found) == {v.new.id_at("An opening line."), v.new.id_at("Closing words")}


# --- a changed block, old beside new ---------------------------------------------


def test_a_changed_block_is_shown_old_beside_new_and_owes_its_deltas(tmp_path) -> None:
    v = versions(tmp_path, EDITED_SETTING, CHAIN, CHAIN_LEGEND)
    _, made = plan(v.new.document, v.store, UPDATE)
    question = made.question
    sheet = shown(question)
    lines = sheet.split("\n")
    marker = next(n for n, line in enumerate(lines) if line.startswith(f"⟦{SETTING} "))
    # the old text as OLD's sheet showed it: owned runs, no comment
    assert lines[marker + 1] == (
        "⟦was⟧ We fix a field of scalars. A second sentence of the setting."
    )
    assert HIDDEN not in sheet
    assert "A second sentence of the setting, edited." in lines[marker + 2 :][:3]
    assert (
        "### The Δ lines owed\n\n- we-fix-a-field: [K]"
        in question.workspace.instructions
    )

    good = answering(
        {SETTING: ["Δ [K] reworded — a clause added after it"]}, without_qualifies
    )
    text = good(sheet)
    assert check(question, text, v.new.document).valid

    missing = answering({}, without_qualifies)(sheet)
    assert any(
        p.startswith("no Δ line (we-fix-a-field [K])")
        for p in refused(question, missing, v.new.document)
    )
    ninth = answering(
        {SETTING: ["Δ [K] rephrased — a ninth class"]}, without_qualifies
    )(sheet)
    assert any("no such class" in p for p in refused(question, ninth, v.new.document))
    empty = answering({SETTING: ["Δ [K] reworded —"]}, without_qualifies)(sheet)
    assert any(
        "say how its meaning changed" in p
        for p in refused(question, empty, v.new.document)
    )
    was = text.replace("⟦was⟧ We fix a field", "⟦was⟧ We fix a ring")
    assert any(
        "⟦was⟧ lines are not as shown" in p
        for p in refused(question, was, v.new.document)
    )


# --- the conditional reach ------------------------------------------------------


def chain(tmp_path: Path) -> tuple[Versions, object]:
    v = versions(tmp_path, EDITED_SETTING, CHAIN, CHAIN_LEGEND)
    _, made = plan(v.new.document, v.store, UPDATE)
    return v, made


def test_the_conditions_stand_under_their_reaching_edges(tmp_path) -> None:
    _, made = chain(tmp_path)
    sheet = shown(made.question)

    assert list(made.reopened) == [SETTING, CLOSING]
    assert made.conditional == {
        DEFINITION: [
            es.moved_by_rule("qualifies", SETTING),
            es.moved_by_rule("uses", SETTING, ("K",)),
        ],
        PROOF_BLOCK: [es.moved_by_rule("uses", DEFINITION, ("gain",))],
        SHARP: [es.asked_if(PROOF_BLOCK)],
    }
    assert (
        '→ uses def:gain [gain] ref · "By Definition~\\ref{def:gain}"\n'
        + es.moved_by_rule("uses", DEFINITION, ("gain",))
    ) in sheet
    assert (
        f'→ uses {PROOF_BLOCK} · "Next, the bound is sharp."\n'
        + es.asked_if(PROOF_BLOCK)
    ) in sheet
    for owner in (DEFINITION, PROOF_BLOCK, SHARP, SETTING, CLOSING):
        assert f"⟦done {owner}⟧" not in sheet
    assert f"⟦done {PROOF}⟧" in sheet and "⟦done thm:bound⟧" in sheet
    # the key binds the conditions
    parsed = es.parse(sheet)
    assert [c.handle for c in parsed.conditions] == [
        SETTING,
        SETTING,
        DEFINITION,
        PROOF_BLOCK,
    ]


def test_reworded_leaves_every_conditional_owner_fixed(tmp_path) -> None:
    v, made = chain(tmp_path)
    question, sheet = made.question, shown(made.question)
    reworded = {SETTING: ["Δ [K] reworded — a clause added after it"]}

    text = answering(reworded, without_qualifies)(sheet)
    assert check(question, text, v.new.document).valid
    spec = json.loads(question.workspace.gate.spec)
    opened, impacts = update_reach(spec, es.parse(text))
    assert opened == {SETTING, CLOSING} and impacts == []

    changed = answering(
        reworded,
        lambda t: without_qualifies(t).replace(
            '"The gain is the ratio"', '"The gain is the ratio of output"'
        ),
    )(sheet)
    assert any(
        p.startswith(f"{DEFINITION}: its lines are fixed")
        for p in refused(question, changed, v.new.document)
    )
    # a Δ on a block left closed is refused too
    closed = answering(
        {**reworded, DEFINITION: ['Δ unchanged — "The gain is the ratio": nothing']},
        without_qualifies,
    )(sheet)
    assert any(
        "a Δ line stands only on a block open" in p
        for p in refused(question, closed, v.new.document)
    )


def test_narrowed_opens_the_asked_ones_and_the_moved_ones_owe_deltas(tmp_path) -> None:
    v, made = chain(tmp_path)
    question, sheet = made.question, shown(made.question)
    narrowed = {SETTING: ["Δ [K] narrowed — scalars of a subfield only"]}

    alone = answering(narrowed, without_qualifies)(sheet)
    found = refused(question, alone, v.new.document)
    # the definition's meaning moved: it owes a Δ; walked wholly, the proof
    # moves too and owes one, and the sharpness is asked
    assert (
        f"no Δ line ({DEFINITION}): the rule moved its meaning, by its "
        f"qualifies of {SETTING}; write its Δ line" in found
    )
    assert any(p.startswith(f"no Δ line ({PROOF_BLOCK})") for p in found)
    spec = json.loads(question.workspace.gate.spec)
    opened, _ = update_reach(spec, es.parse(alone))
    assert opened == {SETTING, CLOSING, DEFINITION, PROOF_BLOCK, SHARP}

    # the definition's own Δ closes the change there: the proof stays fixed
    closing = answering(
        {
            **narrowed,
            DEFINITION: [
                'Δ [gain] unchanged — "The gain is the ratio": over the same field'
            ],
        },
        without_qualifies,
    )(sheet)
    assert check(question, closing, v.new.document).valid
    opened, _ = update_reach(spec, es.parse(closing))
    assert opened == {SETTING, CLOSING, DEFINITION}

    carried_on = answering(
        {
            **narrowed,
            DEFINITION: ["Δ [gain] narrowed — a ratio over a subfield"],
            PROOF_BLOCK: ["Δ weakened — holds over the subfield only"],
        },
        without_qualifies,
    )(sheet)
    assert check(question, carried_on, v.new.document).valid
    opened, _ = update_reach(spec, es.parse(carried_on))
    assert opened == {SETTING, CLOSING, DEFINITION, PROOF_BLOCK, SHARP}


def test_impact_reach_and_the_gate_agree(tmp_path) -> None:
    """One fixture, both ways: the gate's open set is the reopened owners
    and what `impact_reach` reaches from the changed block by the Δs."""
    v, made = chain(tmp_path)
    question, sheet = made.question, shown(made.question)
    spec = json.loads(question.workspace.gate.spec)
    graph = update_graph(spec)
    for deltas in (
        {SETTING: {"K": "reworded"}},
        {SETTING: {"K": "narrowed"}},
        {SETTING: {"K": "narrowed"}, DEFINITION: {"gain": "unchanged"}},
        {SETTING: {"K": "removed"}, DEFINITION: {None: "widened"}},
        {SETTING: {"K": "narrowed"}, DEFINITION: {"gain": "widened"}},
        {
            SETTING: {"K": "narrowed"},
            DEFINITION: {"gain": "removed"},
            PROOF_BLOCK: {None: "reworded"},
        },
    ):
        reached = {i.block for i in impact_reach(graph, deltas, [SETTING])}
        lines = {
            h: [f"Δ{f' [{e}]' if e else ''} {cls} — a note" for e, cls in held.items()]
            for h, held in deltas.items()
        }
        opened, _ = update_reach(
            spec, es.parse(answering(lines, without_qualifies)(sheet))
        )
        assert opened == {SETTING, CLOSING} | reached, deltas
        # and the gate opens exactly the owners whose shown conditions hold
        assert opened == {SETTING, CLOSING} | true_owners(made, deltas), deltas

    # every condition true: the gate opens every owner open on condition
    every = answering({SETTING: ["Δ [K] removed — gone"]}, without_qualifies)(sheet)
    opened, _ = update_reach(spec, es.parse(every))
    assert opened == set(made.reopened) | set(made.conditional)


def true_owners(made, deltas) -> set[str]:
    """The conditional owners whose shown conditions the Δs make true, read
    from the conditions' text alone: a condition holds where the block it
    names is changed (a root) or open with its meaning moved, and its Δ for
    the parts named carries (a part's own, else the whole block's; onto the
    whole, the whole Δ where given, else any part's; no Δ: whole)."""
    closing = {"unchanged", "reworded"}

    def carries(handle: str, parts: tuple[str, ...]) -> bool:
        held = deltas.get(handle)
        if held is None:
            return True
        whole = held.get(None)
        if not parts:
            if whole is not None:
                return whole not in closing
            return any(c not in closing for c in held.values())
        return any(held.get(p, whole) not in closing for p in parts)

    moving, opened = {SETTING}, set()
    grown = True
    while grown:
        grown = False
        for owner, texts in made.conditional.items():
            for text in texts:
                m = es.CONDITION.match(text)
                moved = m.group("moved") is not None
                handle = m.group("moved") if moved else m.group("asked")
                named = m.group("moved_parts" if moved else "asked_parts") or ""
                parts = tuple(p.strip() for p in named.split(",") if p.strip())
                if handle in moving and carries(handle, parts):
                    if owner not in opened:
                        opened.add(owner)
                        grown = True
                    if moved and owner not in moving:
                        moving.add(owner)
                        grown = True
    return opened


def update_graph(spec):
    from axiomation.closure import EdgeGraph, Link

    shown_graph = spec["update"]["graph"]
    return EdgeGraph(
        tuple(shown_graph["blocks"]),
        {g: tuple(m) for g, m in shown_graph["groups"].items()},
        tuple(
            Link(s, k, t, c, w, tuple(p)) for s, k, t, c, w, p in shown_graph["links"]
        ),
        frozenset(shown_graph["ambient"]),
    )


def test_a_deleted_definition_reaches_its_users_by_the_rule(tmp_path) -> None:
    definition = (
        "\\begin{definition}\\label{def:gain}\n"
        "The gain is the ratio of output to input.\n\\end{definition}\n\n"
    )
    assert definition in MAIN
    v = versions(tmp_path, MAIN.replace(definition, ""), CHAIN, CHAIN_LEGEND)
    _, made = plan(v.new.document, v.store, UPDATE)
    deleted = f"deleted:{v.old.id_at('\\begin{definition}')}"

    assert made.rule_deltas == ((deleted, "gain", "removed"),)
    # carry reopened the proof and the setting, whose edges onto it are gone,
    # each from the deleted definition
    assert {PROOF_BLOCK, SETTING} <= set(made.reopened)
    assert made.reopened[PROOF_BLOCK].origin == deleted
    assert made.reopened[SETTING].origin == deleted
    by_rule = [i for i in made.impacts if i.handle == deleted]
    assert {(i.block, i.meaning_moved) for i in by_rule} == {
        (PROOF_BLOCK, True),  # it used the definition's content: it moved
        (SETTING, False),  # it qualified it: asked
    }
    # an open block still carries the change on: the sharpness is asked
    assert made.conditional == {SHARP: [es.asked_if(PROOF_BLOCK)]}

    question, sheet = made.question, shown(made.question)
    assert "def:gain [gain]" not in sheet  # its part gone with it
    unsaid = answering()(sheet)
    assert any(
        p.startswith(f"no Δ line ({PROOF_BLOCK}): the rule moved its meaning")
        for p in refused(question, unsaid, v.new.document)
    )
    said = answering({PROOF_BLOCK: ["Δ weakened — the gain is no longer defined"]})(
        sheet
    )
    found = check(question, said, v.new.document)
    assert found.valid, found.problems


# --- transactional, replay, the export ---------------------------------------------


def test_an_unanswered_session_writes_no_export_and_the_same_call_resumes(
    tmp_path,
) -> None:
    v, made = chain(tmp_path)
    stuck = update(v, host(lambda t: t))  # never marks a block done

    assert not stuck.complete and stuck.unanswered == (made.question.key,)
    with pytest.raises(UpdateError, match="not whole"):
        update_payload(v.new.document, stuck, v.store)

    reworded = {SETTING: ["Δ [K] reworded — a clause added after it"]}
    fake = host(answering(reworded, without_qualifies))
    resumed = update(v, fake)
    assert resumed.complete and fake.calls == 1
    assert resumed.answer is not None and resumed.answer.key == made.question.key
    assert update(v).complete  # the store alone, no host


@pytest.mark.usefixtures("develop_mode")
def test_update_replays_byte_equal_without_old_s_directory(tmp_path, capsys) -> None:
    v, _ = chain(tmp_path)
    narrowed = {
        SETTING: ["Δ [K] narrowed — scalars of a subfield only"],
        DEFINITION: [
            'Δ [gain] unchanged — "The gain is the ratio": over the same field'
        ],
    }
    # the command asks from the cutter: its steps are in the store
    made = update(v, stepping(answering(narrowed, without_qualifies)), CUTTER)
    expected = dumps(update_payload(v.new.document, made, v.store))
    shutil.rmtree(v.old.root)
    output = tmp_path / "update.json"
    capsys.readouterr()

    code = main(
        [
            "update",
            "--json",
            str(v.new.root),
            "--store",
            str(v.store.directory),
            "--host",
            "none",
            "-o",
            str(output),
        ]
    )

    printed = capsys.readouterr()
    assert code == 0, printed.err
    assert output.read_text(encoding="utf-8") == expected
    summary = json.loads(printed.out)
    assert summary["complete"] is True and summary["unanswered"] == []
    # kept whole, the update's working state is gone
    assert not run_state.path(v.store.directory, "update").exists()


@pytest.mark.usefixtures("develop_mode")
def test_an_update_s_session_is_held_by_its_run_and_its_export_reads_it(
    tmp_path,
) -> None:
    """The session's text is the run's, and the export read from it is
    the store's (`docs/architecture.md` § 4)."""
    v, _ = chain(tmp_path)
    narrowed = {
        SETTING: ["Δ [K] narrowed — scalars of a subfield only"],
        DEFINITION: [
            'Δ [gain] unchanged — "The gain is the ratio": over the same field'
        ],
    }
    working = RunFile(v.store.directory, "update", "v", "made")

    made = update(v, stepping(answering(narrowed, without_qualifies)), CUTTER, working)

    assert made.complete and made.answer is not None
    assert made.sheets[made.answer.key] == record_file.text(
        v.store.directory, made.answer
    )
    held = run_state.read(v.store.directory, "update", "v", "made")
    assert held is not None and made.answer.key in held.texts
    # the export from the run's texts is the export from the store's
    held_export = dumps(update_payload(v.new.document, made, v.store))
    stored = dataclasses.replace(made, sheets={})
    assert held_export == dumps(update_payload(v.new.document, stored, v.store))


def test_update_exits_non_zero_naming_what_is_unanswered(tmp_path, capsys) -> None:
    v, made = chain(tmp_path)
    output = tmp_path / "update.json"
    capsys.readouterr()

    code = main(
        [
            "update",
            "--json",
            str(v.new.root),
            "--store",
            str(v.store.directory),
            "--host",
            "none",
            "-o",
            str(output),
        ]
    )

    assert code == 5 and not output.exists()
    # from the cutter, the first question unanswered is step 1's
    (bounded,) = plan(v.new.document, v.store, CUTTER)[1].steps.unanswered
    assert f"{bounded[:12]} (decide, step 1)" in capsys.readouterr().err


def test_two_identity_answers_for_one_new_are_refused_without_identity(
    tmp_path, capsys
) -> None:
    v, _ = chain(tmp_path)
    (first,) = [a for a in v.store.authoritative().values() if a.kind == IDENTITY]
    second_key = "f" * 64
    Resolver(v.store).resolve_identity(
        second_key,
        first.template.version,
        json.dumps(versions_.identity_raw(v.store, first)),
        v.store.lineage_of(first.key),
    )

    with pytest.raises(RecordError, match="--identity"):
        plan(v.new.document, v.store, UPDATE)
    assert plan(v.new.document, v.store, UPDATE, first.key)[0] == first
    capsys.readouterr()
    assert (
        main(
            [
                "update",
                "--json",
                str(v.new.root),
                "--store",
                str(v.store.directory),
                "--host",
                "none",
            ]
        )
        == 1
    )
    assert "name one with --identity" in capsys.readouterr().err


def test_review_update_s_fields(tmp_path) -> None:
    v, made = chain(tmp_path)
    answers = {
        SETTING: ["Δ [K] narrowed — scalars of a subfield only"],
        DEFINITION: [
            'Δ [gain] unchanged — "The gain is the ratio": over the same field'
        ],
    }
    found = update(v, host(answering(answers, without_qualifies)))
    payload = update_payload(v.new.document, found, v.store)
    review = payload["review"]
    data = review["update"]

    assert payload["producer"] == PRODUCER
    assert list(payload)[:5] == [
        "format",
        "version",
        "paper",
        "producer",
        "paper_version",
    ]
    assert payload["paper_version"] == document_hash(v.new.document) == data["new"]
    assert list(data) == [
        "old",
        "new",
        "previous",
        "mode",
        "fresh",
        "reopened",
        "not_asked",
        "deltas",
        "hints",
        "excerpts",
        "cost",
    ]
    assert data["mode"] == "hold-cut"  # the library's default asks no step
    assert data["fresh"] is False and data["not_asked"] == []
    assert data["old"] == document_hash(v.old.document)
    setting_id = v.new.id_at("We fix a field")
    assert data["previous"][setting_id] == [v.old.id_at("We fix a field")]
    assert data["reopened"] == [
        {"handle": SETTING, "why": "its words changed", "session": 1, "from": SETTING},
        {
            "handle": DEFINITION,
            "why": "; ".join(made.conditional[DEFINITION]),
            "session": 1,
            "from": SETTING,
        },
        {
            "handle": CLOSING,
            "why": "its scope:paper edge: the paper changed",
            "session": 1,
            "from": None,
        },
    ]
    assert data["deltas"] == [
        {
            "block": setting_id,
            "handle": SETTING,
            "entry": "K",
            "words": "We fix a field of scalars.",
            "class": "narrowed",
            "note": "scalars of a subfield only",
            "by": "writer",
        },
        {
            "block": v.new.id_at("\\begin{definition}"),
            "handle": DEFINITION,
            "entry": "gain",
            "words": "The gain is the ratio of output to input.",
            "class": "unchanged",
            "note": '"The gain is the ratio": over the same field',
            "by": "writer",
        },
    ]
    assert data["hints"] == []
    assert data["cost"] == {
        found.identity.key: {"stage": "identity", "cost": 0.0},
        found.answer.key: {"stage": "edges", "cost": 0.01},
    }
    assert review["templates"] == {"edges-update": made.question.template.version}
    assert payload["edges_answer"] == found.answer.key
    old = json.loads((tmp_path / "old.json").read_text(encoding="utf-8"))
    assert {e["provenance"]["question"] for e in payload["edges"]} <= {
        found.answer.key,
        old["edges_answer"],
    }
    assert [d.cls for d in found.edges.deltas] == [
        ImpactClass.NARROWED,
        ImpactClass.UNCHANGED,
    ]


def test_a_deleted_block_s_rule_delta_is_in_the_export(tmp_path) -> None:
    definition = (
        "\\begin{definition}\\label{def:gain}\n"
        "The gain is the ratio of output to input.\n\\end{definition}\n\n"
    )
    v = versions(tmp_path, MAIN.replace(definition, ""), CHAIN, CHAIN_LEGEND)
    found = update(
        v,
        host(answering({PROOF_BLOCK: ["Δ weakened — the gain is no longer defined"]})),
    )
    data = update_payload(v.new.document, found, v.store)["review"]["update"]
    deleted = f"deleted:{v.old.id_at('\\begin{definition}')}"
    assert data["deltas"][-1] == {
        "block": deleted,
        "handle": deleted,
        "entry": "gain",
        # OLD's anchor words: the block is gone from NEW
        "words": "The gain is the ratio of output to input.",
        "class": "removed",
        "note": "deleted in this version",
        "by": "rule",
    }
    assert [d["by"] for d in data["deltas"]] == ["writer", "rule"]


def test_a_build_s_export_has_no_paper_version(tmp_path) -> None:
    build_old(tmp_path)
    exported = json.loads((tmp_path / "old.json").read_text(encoding="utf-8"))
    assert "paper_version" not in exported and "update" not in exported["review"]
    assert exported["version"] == 1  # the format's own


def test_the_gate_on_a_write_leaves_out_a_delta_still_owed(tmp_path) -> None:
    from axiomation.questions.edges_file import main as gate

    _, made = chain(tmp_path)
    spec = tmp_path / "spec.json"
    spec.write_text(made.question.workspace.gate.spec, encoding="utf-8")
    edited = tmp_path / EDGES_FILE
    edited.write_text(
        answering(edit=without_qualifies)(shown(made.question)), encoding="utf-8"
    )

    import contextlib
    import io

    for mode, owed in (["write"], False), ([], True):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            gate([str(spec), str(edited), *mode])
        reason = json.loads(out.getvalue())["reason"] if out.getvalue() else ""
        assert ("no Δ line (we-fix-a-field [K])" in reason) is owed, mode


def test_update_from_old_makes_the_identity_answer_diff_makes(tmp_path, capsys) -> None:
    old, store = build_old(tmp_path, CHAIN, CHAIN_LEGEND)
    new = make_paper(tmp_path / "new", main=EDITED_SETTING)
    given = [
        "update",
        "--json",
        str(new.root),
        "--store",
        str(store.directory),
        "--host",
        "none",
    ]
    given += ["--from", str(old.root), "--cut", str(tmp_path / "old.json")]
    capsys.readouterr()

    assert main([*given, "--dry-run", "--json", "--rates", "1,5"]) == 0
    priced = json.loads(capsys.readouterr().out)
    assert priced["questions"] == 1 and priced["conditional"] == 3
    assert not any(a.kind == IDENTITY for a in store.authoritative().values())
    # --prompts: each question the update would ask, as build writes them
    prompts = tmp_path / "prompts"
    assert (
        main(
            [*given, "--dry-run", "--json", "--rates", "1,5", "--prompts", str(prompts)]
        )
        == 0
    )
    capsys.readouterr()
    assert "edges-1.md" in {p.name for p in prompts.iterdir()}
    assert not any(a.kind == IDENTITY for a in store.authoritative().values())

    assert main(given) == 5  # --host none: the identity answer, no session
    (made,) = [a for a in store.authoritative().values() if a.kind == IDENTITY]
    capsys.readouterr()
    args = ["diff", str(old.root), str(new.root), "--displays", "in-paragraph"]
    assert (
        main(
            [
                *args,
                "--cut",
                str(tmp_path / "old.json"),
                "--store",
                str(store.directory),
            ]
        )
        == 0
    )
    assert [a.key for a in store.authoritative().values() if a.kind == IDENTITY] == [
        made.key
    ]
    assert main([*given, "--identity", made.key]) == 1
    assert "--identity names a stored" in capsys.readouterr().err


# --- the review's findings ---------------------------------------


def test_a_part_whose_words_the_edit_removed_takes_a_removed_delta(tmp_path) -> None:
    """The setting's second sentence rewritten, its part [S] gone: the
    writer deletes its legend line and writes `Δ [S] removed`."""
    legend = [
        *CHAIN_LEGEND,
        '  we-fix-a-field [S]: "A second sentence of the setting."',
    ]
    main_text = MAIN.replace("A second sentence of the setting.", "Another line here.")
    v = versions(tmp_path, main_text, CHAIN, legend)
    _, made = plan(v.new.document, v.store, UPDATE)
    question, sheet = made.question, shown(made.question)
    spec = json.loads(question.workspace.gate.spec)
    assert spec["update"]["owed"][SETTING] == ["K", "S"]
    assert spec["update"]["gone"] == [[SETTING, "S"]]
    assert "- we-fix-a-field: [K], [S] (its words are gone)" in (
        question.workspace.instructions
    )

    def dropped(text: str) -> str:
        return without_qualifies(text).replace(
            '  we-fix-a-field [S]: "A second sentence of the setting."\n', ""
        )

    # [S] removed carries the change on through the setting's qualifies
    # edge: the definition's own Δ closes it there
    closed = ['Δ [gain] unchanged — "The gain is the ratio": over the same field']
    removed = {
        SETTING: ["Δ [K] reworded — kept", "Δ [S] removed — rewritten"],
        DEFINITION: closed,
    }
    found = check(question, answering(removed, dropped)(sheet), v.new.document)
    assert found.valid, found.problems
    assert {(d.entry, d.cls.value) for d in found.edges.deltas} == {
        ("K", "reworded"),
        ("S", "removed"),
        ("gain", "unchanged"),
    }
    reworded = {
        SETTING: ["Δ [K] reworded — kept", "Δ [S] reworded — rewritten"],
        DEFINITION: closed,
    }
    assert any(
        "write `Δ [S] removed`" in p
        for p in refused(question, answering(reworded, dropped)(sheet), v.new.document)
    )
    kept = answering(removed, without_qualifies)(sheet)
    assert any(
        "part we-fix-a-field [S]: not verbatim" in p
        for p in refused(question, kept, v.new.document)
    )


def test_from_names_the_root_whose_delta_made_the_condition_true(tmp_path) -> None:
    """The proof's paragraph reached from two changed blocks; only the
    definition's Δ carries the change, so it alone is why and from."""
    lines = {
        **WRITTEN,
        PROOF_BLOCK: [
            *WRITTEN[PROOF_BLOCK],
            '→ uses we-fix-a-field [K] · "By Definition"',
        ],
    }
    legend = [
        '  we-fix-a-field [K]: "We fix a field of scalars."',
        '  def:gain [gain]: "The gain is the ratio"',
    ]
    main_text = EDITED_SETTING.replace(
        "of output to input.", "of output to input, in decibels."
    )
    v = versions(tmp_path, main_text, lines, legend)
    _, made = plan(v.new.document, v.store, UPDATE)
    assert [i.handle for i in made.impacts if i.block == PROOF_BLOCK] == [
        SETTING,
        DEFINITION,
    ]
    answer = {
        SETTING: ["Δ [K] reworded — a clause added after it"],
        DEFINITION: ["Δ [gain] narrowed — in decibels only"],
        PROOF_BLOCK: ["Δ weakened — the gain in decibels"],
    }
    found = update(v, host(answering(answer, without_qualifies)))
    assert found.complete

    data = update_payload(v.new.document, found, v.store)["review"]["update"]
    (proof,) = [r for r in data["reopened"] if r["handle"] == PROOF_BLOCK]
    assert proof == {
        "handle": PROOF_BLOCK,
        "why": es.moved_by_rule("uses", DEFINITION, ("gain",)),
        "session": 1,
        "from": DEFINITION,
    }


def test_two_chained_updates_keep_the_edges(tmp_path) -> None:
    """A markup edit asks nothing, its export's edges name the identity
    answer; the next update, a deleted definition, follows that answer's
    carried sheet and its records, the deleted block's reach among them."""
    markup = MAIN.replace("\\section{Setting}", "\\section*{Setting}")
    v = versions(tmp_path, markup, CHAIN, CHAIN_LEGEND)
    first = update(v)
    assert first.plan.question is None
    (tmp_path / "v2.json").write_text(
        dumps(update_payload(v.new.document, first, v.store)), encoding="utf-8"
    )
    definition = (
        "\\begin{definition}\\label{def:gain}\n"
        "The gain is the ratio of output to input.\n\\end{definition}\n\n"
    )
    third = make_paper(tmp_path / "v3", main=markup.replace(definition, ""))
    args = ["diff", str(v.new.root), str(third.root), "--displays", "in-paragraph"]
    args += ["--cut", str(tmp_path / "v2.json"), "--store", str(v.store.directory)]
    assert main(args) == 0

    next_v = Versions(v.new, third, v.store, v.old_sheet)
    _, made = plan(third.document, v.store, UPDATE)
    assert "records" in made.raw["carried"]
    assert made.raw["carried"]["from"] == first.identity.key
    assert written_lines(made.carried.sheet)[SHARP][0].startswith("→ uses")
    deleted = f"deleted:{v.new.id_at('\\begin{definition}')}"
    assert made.rule_deltas == ((deleted, "gain", "removed"),)
    assert (PROOF_BLOCK, True) in {
        (i.block, i.meaning_moved) for i in made.impacts if i.handle == deleted
    }
    second = update(
        next_v, host(answering({PROOF_BLOCK: ["Δ weakened — the gain is undefined"]}))
    )
    assert second.complete
    payload = update_payload(third.document, second, v.store)
    assert payload["edges"] and payload["edges_answer"] == second.answer.key
    # every edge named by the edges answer that first wrote it, across both
    # updates
    answered = v.store.authoritative()
    assert {answered[e["provenance"]["question"]].kind for e in payload["edges"]} == {
        "edges"
    }


def test_an_answer_of_another_kind_cannot_give_the_edges(tmp_path, capsys) -> None:
    v = versions(tmp_path, EDITED_SETTING)
    exported = json.loads((tmp_path / "old.json").read_text(encoding="utf-8"))
    (identity,) = [a for a in v.store.authoritative().values() if a.kind == IDENTITY]
    # an identity answer is followed; one carrying no sheet is refused
    raw = versions_.identity_raw(v.store, identity)
    del raw["carried"]
    Resolver(v.store).resolve_identity(
        "e" * 64, identity.template.version, json.dumps(raw), ()
    )
    # and an answer of another kind, a roles answer, gives no edges
    v.store.append(
        AnswerRecord(
            key="r" * 64,
            kind="roles",
            stage="roles",
            template=TemplateRef("roles-file", "0123456789ab"),
            attempt=1,
            host="fake",
            model="fake/model",
            time="2026-10-04T00:00:00+00:00",
            seconds=0.0,
            cost=0.0,
            valid=True,
        )
    )
    args = ["diff", str(v.old.root), str(v.new.root), "--displays", "in-paragraph"]
    args += ["--cut", str(tmp_path / "bad.json"), "--store", str(v.store.directory)]
    for key, refusal in (
        ("e" * 64, "carries no edges sheet"),
        ("r" * 64, "of kind roles"),
    ):
        exported["edges_answer"] = key
        (tmp_path / "bad.json").write_text(json.dumps(exported), encoding="utf-8")
        capsys.readouterr()
        assert main(args) == 1
        assert refusal in capsys.readouterr().err


def test_a_qualifies_edge_from_a_deleted_block_moves_its_target(tmp_path) -> None:
    """The setting deleted: its qualifies edge onto the definition
    moves the definition's meaning, by the rule's Δ `removed`."""
    paragraph = (
        "We fix a field of scalars. % a hidden remark about the draft\n"
        "A second sentence of the setting.\n\n"
    )
    assert paragraph in MAIN
    lines = {**CHAIN, DEFINITION: []}
    v = versions(tmp_path, MAIN.replace(paragraph, ""), lines, CHAIN_LEGEND)
    _, made = plan(v.new.document, v.store, UPDATE)
    old = v.old.id_at("We fix a field")
    deleted = f"deleted:{old}"
    assert (deleted, "K", "removed") in made.rule_deltas
    qualified = [i for i in made.impacts if i.via.kind == "qualifies"]
    assert [(i.block, i.handle, i.meaning_moved) for i in qualified] == [
        (DEFINITION, deleted, True)
    ]
    # no sheet line holds the edge, and its condition is never false: the
    # definition is reopened outright, with no condition line, and owes a Δ
    assert made.reopened[DEFINITION].why == (
        f"a qualifies edge from deleted {old} moves its meaning"
    )
    assert made.reopened[DEFINITION].origin == deleted
    assert DEFINITION not in made.conditional
    sheet = shown(made.question)
    assert deleted not in sheet and f"⟦done {DEFINITION}⟧" not in sheet
    question = made.question
    owed = answering({PROOF_BLOCK: ["Δ weakened — n"]})(sheet)
    assert any(
        p.startswith(f"no Δ line ({DEFINITION}): the rule moved its meaning")
        for p in refused(question, owed, v.new.document)
    )


def test_a_delta_line_outside_an_update_is_refused(tmp_path) -> None:
    """A Δ line in a first pass is refused, and a first pass's records
    hold no Δ."""
    from axiomation.questions import load_template
    from axiomation.questions.edges_file import (
        SESSION_TEMPLATE,
        records,
        session_question,
    )

    paper = make_paper(tmp_path / "paper")
    roled = first_pass(paper)
    question = session_question(
        paper.document, roled, load_template(SESSION_TEMPLATE), FILES
    )
    text = written(
        shown(question),
        {**WRITTEN, "thm:bound": ["Δ unchanged — n", *WRITTEN["thm:bound"]]},
    )
    assert any(
        "a Δ line is an update's" in p
        for p in refused(question, as_lines(text), paper.document)
    )
    assert records(text, dict(question.handles)).deltas == ()


def test_an_insertion_reopens_its_neighbours_position_sources_and_its_section(
    tmp_path,
) -> None:
    """A lemma added in the results: a `[position]` edge onto the heading
    beside it, and an about edge onto a block of its section, reopen their
    sources."""
    lines = {
        **WRITTEN,
        "thm:bound": [
            '→ uses results position · "The output is bounded."',
            *WRITTEN["thm:bound"],
        ],
        CLOSING: ['→ about thm:bound · "Closing words of the results."'],
    }
    main_text = MAIN.replace(
        "\\section{Results}\n", "\\section{Results}\nA new lemma paragraph.\n\n"
    )
    v = versions(tmp_path, main_text, lines)
    _, made = plan(v.new.document, v.store, UPDATE)

    assert made.reopened["thm:bound"].why == (
        "its [position] edge onto results, beside a block added"
    )
    assert made.reopened[CLOSING].why == (
        "its about edge onto thm:bound, in the section where a block was added"
    )
    assert made.reopened[CLOSING].origin == "a-new-lemma-paragraph"


def test_a_changed_block_reopens_the_where_used_edges_onto_what_it_uses(
    tmp_path,
) -> None:
    lines = {
        **WRITTEN,
        DEFINITION: ['→ uses we-fix-a-field · "The gain is the ratio"'],
        "a-closing-paragraph-in": [
            '→ about thm:bound · "A closing paragraph"',
            '→ about we-fix-a-field where-used · "A closing paragraph"',
            '⊢ restated · "A closing paragraph in another file."',
        ],
    }
    main_text = MAIN.replace("of output to input.", "of output to input, in decibels.")
    v = versions(tmp_path, main_text, lines)
    _, made = plan(v.new.document, v.store, UPDATE)

    reason = made.reopened["a-closing-paragraph-in"]
    assert reason.why == (
        "its where-used edge onto we-fix-a-field, which def:gain uses, changed"
    )
    assert reason.origin == DEFINITION


def test_an_edge_naming_a_part_split_by_a_comment_follows_its_delta(tmp_path) -> None:
    """A comment inserted inside the setting's part [K] splits its words
    across two runs: the legend line no longer quotes within one run, so
    [K] is gone. The definition's edge naming it is not opened outright: it
    follows the setting's Δ on [K] by the rule, on condition. [K] quoted anew and
    reworded closes it; [K] removed opens it, and its edge must then leave
    the part."""
    main_text = EDITED_SETTING.replace(
        "We fix a field of scalars.", "We fix a field % split here\nof scalars."
    )
    v = versions(tmp_path, main_text, CHAIN, CHAIN_LEGEND)
    _, made = plan(v.new.document, v.store, UPDATE)
    question, sheet = made.question, shown(made.question)
    spec = json.loads(question.workspace.gate.spec)

    assert spec["update"]["gone"] == [[SETTING, "K"]]
    assert DEFINITION not in made.reopened
    assert DEFINITION in made.conditional

    old_k = '  we-fix-a-field [K]: "We fix a field of scalars."\n'
    closed = ['Δ [gain] unchanged — "The gain is the ratio": over the same field']

    def requoted(text: str) -> str:
        return without_qualifies(text).replace(
            old_k, '  we-fix-a-field [K]: "We fix a field"\n'
        )

    reworded = {SETTING: ["Δ [K] reworded — a comment inside"]}
    found = check(question, answering(reworded, requoted)(sheet), v.new.document)
    assert found.valid, found.problems

    def dropped(text: str) -> str:
        return without_qualifies(text).replace(old_k, "")

    removed = {SETTING: ["Δ [K] removed — split"], DEFINITION: closed}
    assert any(
        "names [K], whose words the edit removed" in p
        for p in refused(question, answering(removed, dropped)(sheet), v.new.document)
    )


def test_a_part_inside_maths_is_requoted_and_opens_no_edge(tmp_path) -> None:
    """The setting's part [K] is its maths, and
    a maths-only edit rewrites it. Carry re-quotes [K] on the one heir,
    so nothing is lost: the definition's edge naming [K] stays on
    condition, to follow the setting's Δ, and is not opened outright."""
    old_main = MAIN.replace(
        "A second sentence of the setting.",
        "A second sentence of the setting, $s=k^{1/2}$.",
    )
    main_text = old_main.replace("$s=k^{1/2}$", "$s=\\sqrt{k}$")
    legend = ['  we-fix-a-field [K]: "s=k^{1/2}"', *CHAIN_LEGEND[1:]]
    v = versions(tmp_path, main_text, CHAIN, legend, old_main=old_main)
    _, made = plan(v.new.document, v.store, UPDATE)
    spec = json.loads(made.question.workspace.gate.spec)

    assert '  we-fix-a-field [K]: "s=\\sqrt{k}"' in shown(made.question)
    assert spec["update"]["gone"] == []
    assert made.reopened[SETTING].why == "its maths changed"
    assert made.reopened[SETTING].origin == SETTING
    assert DEFINITION not in made.reopened
    assert DEFINITION in made.conditional


def test_a_part_whose_words_an_edit_removed_stays_on_its_heir(tmp_path) -> None:
    """A word edit removes the setting's part [K] from its text: the part
    stays on the setting, its one heir, under its label, the fuzzy hint
    beside it, never placed; the setting quotes it anew or gives it
    `removed`, and the definition's edge naming [K] follows that Δ on
    condition."""
    main_text = MAIN.replace("We fix a field of scalars.", "We fix a field of vectors.")
    v = versions(tmp_path, main_text, CHAIN, CHAIN_LEGEND)
    _, made = plan(v.new.document, v.store, UPDATE)
    spec = json.loads(made.question.workspace.gate.spec)

    assert spec["update"]["gone"] == [[SETTING, "K"]]
    assert "- we-fix-a-field: [K] (its words are gone)" in (
        made.question.workspace.instructions
    )
    assert DEFINITION not in made.reopened
    assert DEFINITION in made.conditional
    assert made.reopened[SETTING].origin == SETTING


def test_carry_within_a_version_drops_an_update_s_delta_lines(tmp_path) -> None:
    """A Δ line describes one edit: a later pass's carry, within one
    version as across two, takes none, since its gate refuses them."""
    from axiomation.questions import load_template
    from axiomation.questions.edges_file import (
        SESSION_TEMPLATE,
        carry,
        session_question,
    )

    paper = make_paper(tmp_path / "paper")
    roled = first_pass(paper)
    question = session_question(
        paper.document, roled, load_template(SESSION_TEMPLATE), FILES
    )
    previous = written(
        shown(question),
        {**WRITTEN, "thm:bound": ["Δ unchanged — n", *WRITTEN["thm:bound"]]},
    )
    carried = carry(paper.document, previous, roled, roled, FILES)
    assert written_lines(carried.sheet)["thm:bound"] == [
        *WRITTEN["thm:bound"],
        "⟦done thm:bound⟧",
    ]


# --- session 2: what session 1's answer reveals -----------------

CLOSING_PARAGRAPH = "a-closing-paragraph-in"
#: The chain, and the paragraph in the other file using the closing words
#: (no role: asked by the rule, never moved).
CITED = {
    **CHAIN,
    CLOSING_PARAGRAPH: [
        *WRITTEN[CLOSING_PARAGRAPH],
        '→ uses closing-words-of-the · "A closing paragraph"',
    ],
}
REWORDED = {SETTING: ["Δ [K] reworded — a clause added after it"]}
#: Session 1's answer with a Δ on the closing words, an asked block whose
#: text did not change.
NARROWED_CLOSING = {
    **REWORDED,
    CLOSING: ["Δ narrowed — the closing words now say less"],
}


def by_session(
    first: Callable[[str], str], second: Callable[[str], str] | None = None
) -> Callable[[str], str]:
    """A host's edit for an update of two sessions, session 2's every owner
    marked done unless `second` is given: session 2's sheet is the first
    that holds Δ lines (carry drops them from session 1's)."""
    then = second or answering()
    return lambda text: then(text) if es.parse(text).deltas else first(text)


def closing_cited(tmp_path: Path) -> Versions:
    return versions(tmp_path, EDITED_SETTING, CITED, CHAIN_LEGEND)


def test_a_writer_s_delta_on_an_unchanged_block_opens_session_2_on_its_reach(
    tmp_path,
) -> None:
    v = closing_cited(tmp_path)
    fake = host(by_session(answering(NARROWED_CLOSING, without_qualifies)))

    found = update(v, fake)

    assert found.complete and fake.calls == 2
    follow = found.follow
    assert follow is not None and follow.conditional == {}
    assert follow.reopened == {
        CLOSING_PARAGRAPH: Reason(
            f"its uses edge onto {CLOSING}, and {CLOSING}'s Δ is not unchanged or "
            "reworded: the rule asks it",
            None,  # the closing words were opened by the paper's scope
        )
    }
    # session 2 shows session 1's answer: its Δ lines kept on their owners
    # and fixed, its ⟦was⟧ and condition lines gone, only the reach open
    second = shown(found.second_question)
    assert "Δ [K] reworded — a clause added after it" in second
    assert es.WAS not in second
    assert [c.owner for c in es.parse(second).conditions] == [CLOSING_PARAGRAPH]
    for owner in (SETTING, DEFINITION, PROOF_BLOCK, SHARP, CLOSING):
        assert f"⟦done {owner}⟧" in second
    assert f"⟦done {CLOSING_PARAGRAPH}⟧" not in second
    instructions = fake.workspaces[1].instructions
    assert "### A follow-up session" in instructions
    assert "### The Δ lines owed" not in instructions
    assert found.second_question.key != found.plan.question.key

    data = update_payload(v.new.document, found, v.store)["review"]["update"]
    assert [(r["handle"], r["session"]) for r in data["reopened"]] == [
        (SETTING, 1),
        (CLOSING, 1),
        (CLOSING_PARAGRAPH, 2),
    ]
    assert data["not_asked"] == []
    # the last session's Δs: session 1's, carried on its sheet
    assert [(d["entry"], d["class"]) for d in data["deltas"]] == [
        ("K", "reworded"),
        (None, "narrowed"),
    ]
    assert set(data["cost"]) == {
        found.identity.key,
        found.answer.key,
        found.second.key,
    }


def test_nothing_to_follow_up_asks_one_session(tmp_path) -> None:
    v = closing_cited(tmp_path)
    fake = host(answering(REWORDED, without_qualifies))

    found = update(v, fake)

    assert found.complete and fake.calls == 1
    assert found.follow is not None and found.follow.owners == {}
    assert found.second_question is None and found.not_asked == {}


@pytest.mark.usefixtures("develop_mode")
def test_followup_sessions_max_0_reports_the_owners_unasked(tmp_path) -> None:
    v = closing_cited(tmp_path)
    fake = host(by_session(answering(NARROWED_CLOSING, without_qualifies)))
    runner = Runner(fake, Resolver(v.store), 10.0, FLAT)
    settings = dataclasses.replace(UPDATE, followup_sessions_max=0)

    found = run(v.new.document, v.store, settings, runner)

    assert found.complete and fake.calls == 1
    assert found.second_question is None and list(found.not_asked) == [
        CLOSING_PARAGRAPH
    ]
    data = update_payload(v.new.document, found, v.store)["review"]["update"]
    assert data["not_asked"] == [
        {
            "handle": CLOSING_PARAGRAPH,
            "why": found.not_asked[CLOSING_PARAGRAPH].why,
            "from": None,
        }
    ]
    assert CLOSING_PARAGRAPH not in {r["handle"] for r in data["reopened"]}


@pytest.mark.usefixtures("develop_mode")
def test_an_unanswered_session_2_writes_no_export_and_the_same_call_resumes(
    tmp_path, capsys
) -> None:
    v = closing_cited(tmp_path)
    first = answering(NARROWED_CLOSING, without_qualifies)
    # the command's settings: its steps answered, then session 2 not
    stuck = update(v, stepping(by_session(first, lambda t: t)), CUTTER)

    assert not stuck.complete and stuck.answer is not None
    assert stuck.unanswered == (stuck.second_question.key,)
    with pytest.raises(UpdateError, match="not whole"):
        update_payload(v.new.document, stuck, v.store)
    output = tmp_path / "update.json"
    args = [
        "update",
        "--json",
        str(v.new.root),
        "--store",
        str(v.store.directory),
        "--host",
        "none",
    ]
    capsys.readouterr()
    assert main([*args, "-o", str(output)]) == 5 and not output.exists()
    assert f"{stuck.second_question.key[:12]} (edges, session 2)" in (
        capsys.readouterr().err
    )

    fake = stepping(by_session(first))
    resumed = update(v, fake, CUTTER)
    assert resumed.complete and fake.calls == 1  # session 2 alone
    assert resumed.second.key == stuck.second_question.key
    expected = dumps(update_payload(v.new.document, resumed, v.store))
    replayed = update(v, settings=CUTTER)
    assert dumps(update_payload(v.new.document, replayed, v.store)) == expected

    shutil.rmtree(v.old.root)
    capsys.readouterr()
    assert main([*args, "-o", str(output)]) == 0
    assert output.read_text(encoding="utf-8") == expected
    summary = json.loads(capsys.readouterr().out)
    assert summary["session_2"] == resumed.second.key
    assert summary["not_asked"] == 0 and summary["fresh"] is False


def test_a_delta_of_session_1_on_a_fixed_owner_stands_in_session_2(tmp_path) -> None:
    v = closing_cited(tmp_path)
    found = update(v, host(by_session(answering(NARROWED_CLOSING, without_qualifies))))
    question = found.second_question
    sheet = shown(question)

    assert check(question, answering()(sheet), v.new.document).valid
    changed = answering()(
        sheet.replace("Δ narrowed — the closing", "Δ widened — the closing")
    )
    assert any(
        p.startswith(f"{CLOSING}: its lines are fixed")
        for p in refused(question, changed, v.new.document)
    )
    dropped = answering()(
        sheet.replace("Δ [K] reworded — a clause added after it\n", "")
    )
    assert any(
        p.startswith(f"{SETTING}: its lines are fixed")
        for p in refused(question, dropped, v.new.document)
    )
    # a Δ on a block session 2 left closed is refused as in session 1
    closed = answering({SHARP: ['Δ unchanged — "the bound is sharp": nothing']})(sheet)
    assert any(
        "a Δ line stands only on a block open" in p
        for p in refused(question, closed, v.new.document)
    )


#: The setting's maths edited: a 1:1 MATH block keeps OLD's roles, so its
#: assumption role, and may write a qualifies edge.
MATH_OLD = MAIN.replace(
    "A second sentence of the setting.", "A second sentence of the setting, $x > 0$."
)
MATH_NEW = MAIN.replace(
    "A second sentence of the setting.",
    "A second sentence of the setting, $x \\geq 0$.",
)
THEOREM = "thm:bound"


def test_a_new_qualifies_edge_from_a_changed_block_moves_a_closed_target(
    tmp_path,
) -> None:
    """Session 1 writes a qualifies edge from the
    changed block onto the theorem, which session 1 left closed; with a Δ
    that carries, the theorem's meaning moved: it opens in session 2 and
    owes its Δ, and the proof that justifies it is asked on its Δ."""
    v = versions(tmp_path, MATH_NEW, CHAIN, CHAIN_LEGEND, old_main=MATH_OLD)
    _, made = plan(v.new.document, v.store, UPDATE)
    assert THEOREM not in made.reopened and THEOREM not in made.conditional

    def first(text: str) -> str:
        return answering(
            {
                SETTING: [
                    f'→ qualifies {THEOREM} · "We fix a field of scalars."',
                    "Δ [K] narrowed — scalars of a subfield only",
                ],
                DEFINITION: [
                    'Δ [gain] unchanged — "The gain is the ratio": over the same field'
                ],
            }
        )(text)

    stuck = update(v, host(by_session(first, lambda t: t)))
    follow = stuck.follow
    assert follow.reopened[THEOREM] == Reason(
        f"a qualifies edge from {SETTING}, and {SETTING}'s Δ is not unchanged or "
        "reworded: the rule moves its meaning",
        SETTING,
    )
    # the closing paragraph restates the theorem: it is asked on its Δ too
    assert follow.conditional == {
        PROOF: [es.asked_if(THEOREM)],
        "a-closing-paragraph-in": [es.asked_if(THEOREM)],
    }
    question = stuck.second_question
    sheet = shown(question)
    assert (
        f'→ justifies {THEOREM} name · "Hence the output is bounded."\n'
        + (es.asked_if(THEOREM))
        in sheet
    )

    owed = answering()(sheet)
    assert (
        f"no Δ line ({THEOREM}): the rule moved its meaning, by its qualifies of "
        f"{SETTING}; write its Δ line" in refused(question, owed, v.new.document)
    )
    # its Δ carries the change on: the proof is asked
    narrowed = answering({THEOREM: ["Δ narrowed — over a subfield only"]})
    found = update(v, host(by_session(first, narrowed)))
    assert found.complete
    data = update_payload(v.new.document, found, v.store)["review"]["update"]
    # the proof, and the closing paragraph restating the theorem
    reopened = [(r["handle"], r["session"], r["from"]) for r in data["reopened"]]
    assert reopened[-3:] == [
        (THEOREM, 2, SETTING),
        ("a-closing-paragraph-in", 2, SETTING),
        (PROOF, 2, SETTING),
    ]
    assert data["not_asked"] == []


def test_a_qualifies_edge_from_an_added_block_moves_a_closed_claim(tmp_path) -> None:
    """An added block is a root of the follow-up's walk, changed whole: a
    qualifies edge it writes onto a claim session 1 left closed moves the
    claim, which opens in session 2 owing its Δ. (Until the command asks an
    added block's roles it holds none, so the gate refuses its qualifies:
    the answer is given to `followup` directly.)"""
    main_text = MAIN.replace(
        "\\section{Results}\n",
        "\\section{Results}\nWe assume the input is positive.\n\n",
    )
    v = versions(tmp_path, main_text, CHAIN, CHAIN_LEGEND)
    _, made = plan(v.new.document, v.store, UPDATE)
    added = "we-assume-the-input"
    assert made.reopened[added].why == "it is new in this version"
    assert THEOREM not in made.reopened and THEOREM not in made.conditional
    answer = answering(
        {added: [f'→ qualifies {THEOREM} · "We assume the input is positive."']}
    )(shown(made.question))

    follow = followup(made, made.question, answer)

    assert follow.reopened[THEOREM] == Reason(
        f"a qualifies edge from {added}, and {added} is new in this version: the "
        "rule moves its meaning",
        added,
    )
    question = followup_question(v.new.document, UPDATE, made, follow, answer)
    spec = json.loads(question.workspace.gate.spec)
    assert added in spec["update"]["roots"]
    found = refused(question, answering()(shown(question)), v.new.document)
    assert any(p.startswith(f"no Δ line ({THEOREM}): the rule moved") for p in found)


#: The theorem's part the definition uses, whose words the proof writes:
#: content, as a claim is named by its handle.
MIRROR_LEGEND = [*CHAIN_LEGEND, '  thm:bound [ob]: "output is bounded" (definition)']


def mirrored(tmp_path: Path, evidence: str) -> Versions:
    lines = {
        **CHAIN,
        DEFINITION: [
            *CHAIN[DEFINITION],
            '→ uses thm:bound [ob] · "The gain is the ratio"',
        ],
        PROOF_BLOCK: [
            f'→ uses def:gain [gain] ref · "{evidence}"',
            *WRITTEN[PROOF_BLOCK][1:],
        ],
    }
    return versions(tmp_path, EDITED_SETTING, lines, MIRROR_LEGEND)


#: Session 1: the setting narrowed, the definition open by it, unchanged in
#: its own part, and dropping its uses edge onto the theorem's part.
MIRROR_ANSWER = answering(
    {
        SETTING: ["Δ [K] narrowed — scalars of a subfield only"],
        DEFINITION: [
            'Δ [gain] unchanged — "The gain is the ratio": over the same field'
        ],
    },
    lambda t: without_qualifies(t).replace(
        '→ uses thm:bound [ob] · "The gain is the ratio"\n', ""
    ),
)


def test_the_mirror_check_opens_a_closed_citer_that_quotes_the_dropped_words(
    tmp_path,
) -> None:
    v = mirrored(tmp_path / "quoting", "Hence the output is bounded.")
    found = update(v, host(by_session(MIRROR_ANSWER)))

    assert found.complete and found.second is not None
    assert found.follow.reopened == {
        PROOF_BLOCK: Reason(
            f"it cites {DEFINITION}, which dropped its edge onto {THEOREM} [ob], "
            f"for {THEOREM} [ob]'s words",
            SETTING,
        )
    }

    # the citer's edge without the words: nothing follows
    v = mirrored(tmp_path / "plain", "By Definition~\\ref{def:gain}")
    fake = host(by_session(MIRROR_ANSWER))
    found = update(v, fake)
    assert found.complete and fake.calls == 1 and found.follow.owners == {}


#: Session 1: a new part on the definition, open by the setting's Δ.
NEW_TERM = answering(
    {
        SETTING: ["Δ [K] narrowed — scalars of a subfield only"],
        DEFINITION: [
            'Δ [gain] unchanged — "The gain is the ratio": over the same field'
        ],
    },
    lambda t: without_qualifies(t).replace(
        '  def:gain [gain]: "The gain is the ratio of output to input."\n',
        '  def:gain [gain]: "The gain is the ratio of output to input."\n'
        '  def:gain [out]: "output"\n',
    ),
)


@pytest.mark.usefixtures("develop_mode")
def test_the_reverse_prefilter_opens_a_closed_block_writing_a_new_term(
    tmp_path,
) -> None:
    v = chain(tmp_path)[0]
    fake = host(by_session(NEW_TERM))
    found = update(v, fake)

    assert found.complete and fake.calls == 2
    why = (
        'it writes "output", def:gain [out]\'s words, new in this version, and has '
        "no edge onto it"
    )
    # the theorem and the proof write "output"; the proof's edge onto the
    # definition names [gain], not [out]
    assert found.follow.reopened == {
        THEOREM: Reason(why, SETTING),
        PROOF_BLOCK: Reason(why, SETTING),
    }

    off = dataclasses.replace(UPDATE, reverse_prefilter=False)
    fake = host(by_session(NEW_TERM))
    v2 = chain(tmp_path / "off")[0]
    runner = Runner(fake, Resolver(v2.store), 10.0, FLAT)
    found = run(v2.new.document, v2.store, off, runner)
    assert found.complete and fake.calls == 1 and found.follow.owners == {}


# --- the switch ------------------------------------------------------------------


def test_the_switch_unset_changes_nothing(tmp_path) -> None:
    _, made = chain(tmp_path)
    assert not made.fresh and made.question.prompt.startswith(UPDATE_KICKOFF)


@pytest.mark.usefixtures("develop_mode")
def test_the_switch_set_low_asks_the_edges_fresh(tmp_path) -> None:
    v, made = chain(tmp_path)
    share = (len(made.reopened) + len(made.conditional)) / len(made.cut.blocks)
    settings = dataclasses.replace(
        UPDATE, fresh_edges_above_reopened_fraction=share - 0.01
    )
    fresh = {**WRITTEN, SETTING: []}
    fake = host(lambda t: written(t, fresh, LEGEND))
    runner = Runner(fake, Resolver(v.store), 10.0, FLAT)

    found = run(v.new.document, v.store, settings, runner)

    assert found.plan.fresh and found.complete and fake.calls == 1
    assert fake.prompts[0].startswith(LINES_KICKOFF)
    assert found.follow is None and found.second_question is None
    payload = update_payload(v.new.document, found, v.store)
    data = payload["review"]["update"]
    assert data["fresh"] is True and data["deltas"] == []
    assert payload["review"]["templates"] == {
        "edges-file": found.plan.question.template.version
    }
    # at the share itself, the update asks as before
    same = dataclasses.replace(UPDATE, fresh_edges_above_reopened_fraction=share)
    assert not plan(v.new.document, v.store, same)[1].fresh


# --- the dry run's prices -----------------------------------------------------------


@pytest.mark.usefixtures("develop_mode")
def test_the_dry_run_prices_session_2_apart(tmp_path, capsys) -> None:
    v = closing_cited(tmp_path)
    args = ["update", "--json", str(v.new.root), "--store", str(v.store.directory)]
    args += ["--host", "opencode", "--dry-run", "--json", "--rates", "1,5"]
    capsys.readouterr()

    assert main(args) == 0
    priced = json.loads(capsys.readouterr().out)
    assert priced["estimate"] > 0 and priced["fresh"] is False
    # a first attempt the gate refuses, and its re-ask, priced in
    assert priced["attempts_priced"] == 2
    assert priced["estimate"] == pytest.approx(2 * priced["attempt_estimate"], abs=1e-4)
    assert priced["followup_if_it_fires"] == priced["estimate"]
    assert "followup_estimate" not in priced

    first = answering(NARROWED_CLOSING, without_qualifies)
    stuck = update(v, host(by_session(first, lambda t: t)))
    assert not stuck.complete
    capsys.readouterr()
    assert main(args) == 0
    priced = json.loads(capsys.readouterr().out)
    assert priced["estimate"] == 0 and priced["followup_owners"] == 1
    assert priced["followup_estimate"] > 0
    assert "followup_if_it_fires" not in priced

    assert update(v, host(by_session(first))).complete
    capsys.readouterr()
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out)["followup_estimate"] == 0


@pytest.mark.usefixtures("develop_mode")
def test_a_uses_edge_that_still_names_its_part_is_not_dropped(tmp_path) -> None:
    """The definition's edge onto the theorem's [ob] rewritten to name a
    second part beside it: [ob] is still named, so no mirror check fires."""
    v = mirrored(tmp_path, "Hence the output is bounded.")
    kept = answering(
        {
            SETTING: ["Δ [K] narrowed — scalars of a subfield only"],
            DEFINITION: [
                'Δ [gain] unchanged — "The gain is the ratio": over the same field'
            ],
        },
        lambda t: (
            without_qualifies(t)
            .replace(
                '→ uses thm:bound [ob] · "The gain is the ratio"',
                '→ uses thm:bound [ob, th] · "The gain is the ratio"',
            )
            .replace(
                '  thm:bound [ob]: "output is bounded" (definition)\n',
                '  thm:bound [ob]: "output is bounded" (definition)\n'
                '  thm:bound [th]: "The output" (definition)\n',
            )
        ),
    )
    fake = host(by_session(kept))
    runner = Runner(fake, Resolver(v.store), 10.0, FLAT)
    settings = dataclasses.replace(UPDATE, reverse_prefilter=False)

    found = run(v.new.document, v.store, settings, runner)

    assert found.complete and fake.calls == 1 and found.follow.owners == {}


def test_what_session_2_s_own_deltas_reach_is_reported_not_asked(tmp_path) -> None:
    """Session 2 gives the block the mirror check opened a Δ that the rule
    follows to a block closed in both sessions: one follow-up session at
    most, so it is reported in `not_asked`."""
    v = mirrored(tmp_path, "Hence the output is bounded.")
    second = answering({PROOF_BLOCK: ["Δ narrowed — only for small gains"]})
    fake = host(by_session(MIRROR_ANSWER, second))

    found = update(v, fake)

    assert found.complete and fake.calls == 2
    assert list(found.not_asked) == [SHARP]
    assert found.not_asked[SHARP].origin == SETTING
    data = update_payload(v.new.document, found, v.store)["review"]["update"]
    assert [r["handle"] for r in data["not_asked"]] == [SHARP]
    assert SHARP not in {r["handle"] for r in data["reopened"]}


GOLDEN = Path(__file__).parent / "questions" / "golden" / "edges-update.md"


def test_session_1_matches_its_golden(tmp_path) -> None:
    """Session 1 as the chain's narrowed setting asks it: its prompt, its
    brief with the notes, and the sheet shown, pinned so a later change
    cannot change it unseen. The key binds the document,
    whose temporary path varies, so it is not pinned here."""
    question = update(chain(tmp_path)[0]).plan.question
    assert question is not None and question.workspace is not None
    text = "\n".join(
        [
            "# prompt",
            question.prompt,
            "# instructions",
            question.workspace.instructions,
            f"# {EDGES_FILE}",
            question.workspace.files[EDGES_FILE],
        ]
    )
    assert GOLDEN.exists(), f"missing golden {GOLDEN}; write it deliberately, reviewed"
    assert text == GOLDEN.read_text(encoding="utf-8"), (
        "session 1 differs from its golden; review the change before updating it"
    )


def test_a_cut_line_s_point_is_requoted_across_markup(tmp_path) -> None:
    """A `✂ split` line OLD's round did not weigh, its point and evidence in
    words a markup edit ran through: both are re-quoted, so the carried
    sheet reads and nothing is asked."""
    lines = {
        **WRITTEN,
        SETTING: [
            *WRITTEN[SETTING],
            '✂ split "A second" · "A second sentence of the setting." — two claims',
        ],
    }
    v = versions(
        tmp_path, MAIN.replace("A second sentence", "A \\emph{second} sentence"), lines
    )

    found = update(v)

    assert found.plan.question is None and found.complete
    assert (
        '✂ split "A \\emph{second}" · "A \\emph{second} sentence of the setting." '
        "— two claims"
    ) in found.plan.carried.sheet


def test_a_fixed_line_a_comment_splits_is_opened_before_the_session(tmp_path) -> None:
    """A comment added inside a same block's
    quoted words leaves its line unreadable (the words now run across two
    runs); a gate that fixed it could never pass, so the owner is opened
    with why before session 1, and the session can re-quote it."""
    main_text = MAIN.replace(
        "The output is bounded.\n\\end{theorem}",
        "The output % a note\nis bounded.\n\\end{theorem}",
    )
    v = versions(tmp_path, main_text)
    _, made = plan(v.new.document, v.store, UPDATE)

    assert made.reopened["thm:bound"].why.startswith(
        "its lines no longer read on this version: thm:bound ⊢ justified: claim not in"
    )
    requote = answering(
        edit=lambda t: t.replace(
            '⊢ justified · "The output is bounded."', '⊢ justified · "is bounded."'
        )
    )
    found = update(v, host(requote))
    assert found.complete, found.unanswered


def test_a_part_a_comment_splits_opens_its_block_and_the_edges_naming_it(
    tmp_path,
) -> None:
    main_text = MAIN.replace(
        "The gain is the ratio of output to input.",
        "The gain is the ratio of output % a note\nto input.",
    )
    v = versions(tmp_path, main_text)
    _, made = plan(v.new.document, v.store, UPDATE)

    assert {DEFINITION, PROOF_BLOCK} <= set(made.reopened)
    assert made.reopened[DEFINITION].why.startswith(
        "its lines no longer read on this version: part def:gain [gain]: not verbatim"
    )


@pytest.mark.parametrize(("rounds", "kept"), [(0, True), (1, False)])
def test_a_cut_line_old_s_round_weighed_and_dropped_does_not_come_back(
    tmp_path, rounds, kept
) -> None:
    """OLD's edge half proposes merging the
    footnote across two nesting levels. With a round, the assembler refused
    it, so it is not carried onto NEW; without one, it was never weighed
    and NEW's round may weigh it."""
    merge = '✂ merge next-the-bound-is · "Small means below one." — one note'
    lines = {**WRITTEN, "small-means-below-one": [merge]}
    paper = make_paper(tmp_path / "old")
    store = RecordStore(tmp_path / "store")
    settings = dataclasses.replace(SETTINGS, cut_rounds=rounds)
    fake = FakeHost(
        lambda p: "", edits={EDGES_FILE: lambda t: written(t, lines, LEGEND)}
    )
    review = review_cut(
        paper.document,
        first_pass(paper),
        store,
        settings,
        Runner(fake, Resolver(store), 10.0, FLAT),
    )
    (proposal,) = review.proposals
    assert proposal.applied is False
    (tmp_path / "old.json").write_text(
        dumps(review_payload(paper.document, review, settings)), encoding="utf-8"
    )
    new = make_paper(tmp_path / "new", main=EDITED_SETTING)
    args = ["diff", str(paper.root), str(new.root), "--displays", "in-paragraph"]
    args += ["--cut", str(tmp_path / "old.json"), "--store", str(store.directory)]

    assert main(args) == 0

    (identity,) = [a for a in store.authoritative().values() if a.kind == IDENTITY]
    carried = versions_.identity_raw(store, identity)["carried"]
    assert (merge in carried["sheet"]) is kept


# --- the edges' role correction ------------------------------------------


def test_an_update_s_standing_on_a_block_with_no_claim_adds_it_as_a_build_does(
    tmp_path,
) -> None:
    """Session 1 writes a standing on the reopened closing paragraph, which
    OLD's roles give none: the update applies the role correction with the
    answer, asks nothing more for it, lists it by the edges, and replays
    the same export from the store."""
    main_text = MAIN.replace(
        "\\section{Setting}", "An opening line.\n\n\\section{Setting}"
    )
    v = versions(tmp_path, main_text)
    claim = '⊢ unsupported · "Closing words of the results."'
    fake = host(answering(edit=lambda t: add_lines(t, {CLOSING: [claim]})))

    found = update(v, fake)

    assert found.complete and fake.calls == 1
    closing = v.new.id_at("Closing words")
    assert found.corrected is not None
    assert found.corrected.cut.block(closing).roles == (Role.CLAIM,)
    (proposal,) = found.proposals
    assert proposal.correction.op is CutOp.ROLES and proposal.applied
    assert (proposal.by, proposal.round, proposal.stage) == ("edges", 0, "edges")
    assert proposal.correction.answer == found.answer.key
    assert found.cut_rounds == () and Stage.ROLES not in found.questions
    payload = update_payload(v.new.document, found, v.store)
    (entry,) = payload["review"]["cut_corrections"]
    assert (entry["op"], entry["by"], entry["block"]) == ("roles", "edges", closing)
    (block,) = [b for b in payload["blocks"] if b["id"] == closing]
    assert block["roles"] == ["claim"]
    assert closing in {s["block"] for s in payload["standings"]}

    again = update(v)
    assert dumps(update_payload(v.new.document, again, v.store)) == dumps(payload)


def test_a_line_naming_a_part_the_legend_lost_reads_as_the_block_whole() -> None:
    """At either end: the closure reads the line onto, or from, the block
    whole, and a part the legend holds stays named."""
    from axiomation.records import (
        Edge,
        EdgeKind,
        EdgeRecords,
        EdgeTarget,
        Entry,
        Role,
        TargetKind,
    )
    from axiomation.update import _with_known_parts

    line = Edge(
        "e1", None, "d", EdgeKind.USES, EdgeTarget(TargetKind.BLOCK, "t"), ("w",)
    )
    records = EdgeRecords(
        entries=(Entry("n1", None, "d", "g", "Let g be.", Role.DEFINITION),),
        edges=(
            dataclasses.replace(line, source_parts=("g",), parts=("x",)),
            dataclasses.replace(line, id="e2", source_parts=("h",)),
        ),
    )
    found = _with_known_parts(records).edges
    assert [(e.source_parts, e.parts) for e in found] == [(("g",), ()), ((), ())]
