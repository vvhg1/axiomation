"""A person's edit: an answer by `person` to its stage's
own file, applied after a build's own stages by its layer's type rule, its
lines pinned in every later gate and across a version. On the synthetic
paper, a build of its roles and its edges answered by a fake host; a
person's answers recorded as `axiomation edit` records them. No model is
called.
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
from axiomation import person, run_state, versions
from axiomation.changes import Layer
from axiomation.cli import main
from axiomation.commands import Exit
from axiomation.cutter.model import Role
from axiomation.driver import (
    CLAIM_ADDED_NOTE,
    PERSON_NO_CUT,
    PINNED,
    Review,
    ReviewSettings,
    checker,
    claim_notes,
    review_cut,
    review_payload,
    weigh,
)
from axiomation.exchange import cut_payload, dumps
from axiomation.person import Pinned
from axiomation.questions import Question, Stage
from axiomation.questions.edges_file import EDGES_FILE, check, person_sheet
from axiomation.questions.edges_file import structure as edges_structure
from axiomation.reader import document_hash
from axiomation.records import (
    PERSON,
    RECORD,
    AnswerRecord,
    CorrectionClass,
    CutCorrection,
    CutOp,
    RecordStore,
)
from axiomation.resolver import Attempt, Resolver
from axiomation.run_state import RunFile
from axiomation.runner import Runner
from axiomation.runner.hosts import Rates
from axiomation.update import (
    UpdateError,
    _unpinned_groups,
    asked_as,
    plan,
    run,
    update_payload,
)
from axiomation.versions import identity_raw
from questions.review_paper import MAIN, Paper, make_paper
from questions.test_edges_file import LEGEND, WRITTEN, done, written
from questions.test_roles_file import ROLES
from runner.fakes import FakeHost
from test_update import (
    EDITED_SETTING,
    SETTING,
    UPDATE,
    add_lines,
    answering,
    build_old,
    without_qualifies,
)
from test_update import SETTINGS as OLD_SETTINGS

FLAT = Rates(input=0.0, output=1.0)
FILES = ("extra.tex", "main.tex")
#: A build of the roles and the edges on the cutter's cut, given.
SETTINGS = ReviewSettings(
    chunk=None,
    overlap=0,
    stages=(Stage.ROLES, Stage.EDGES),
    files=FILES,
    given_cut=True,
)
DEFINITION = "def:gain"
PROOF_BLOCK = "by-definition-def:gain-the"
CLOSING = "closing-words-of-the"
PROOF = "group:by-definition-def:gain-the"
#: The setting's second sentence, made a block of its own in cut.md.
SPLIT = (
    "A second sentence of the setting.",
    "⟦new — its own sentence⟧\nA second sentence of the setting.",
)
WROTE = '→ uses thm:bound · "Closing words of the results."'
DROPPED = '→ uses small-means-below-one · "the gain is small" — the footnote says'


def roles_answer(text: str) -> str:
    """The roles session: the first pass as the synthetic paper's roles,
    with its two segments; a touched question's blocks none, but the
    setting's, an assumption."""
    if "⟦b11⟧" in text:
        for handle, role in ROLES.items():
            text = text.replace(f"⟦{handle}⟧", f"⟦{handle} {role}⟧", 1)
        return text.replace(
            "Hence the output is bounded.",
            "⟦+ claim⟧\nHence the output is bounded.",
        ).replace("A second sentence", "⟦+ none⟧\nA second sentence")
    text = re.sub(r"^⟦(b\d+)⟧\nWe fix", r"⟦\1 assumption⟧\nWe fix", text, flags=re.M)
    return re.sub(r"^⟦(b\d+)⟧", r"⟦\1 none⟧", text, flags=re.MULTILINE)


def edges_with(lines: dict[str, list[str]]) -> Callable[[str], str]:
    """The edges: a first pass writing `lines`, a re-ask (its sheet holds
    edges already) marking what it reopened done."""

    def answer(text: str) -> str:
        return done(text) if "→ " in text else written(text, lines, LEGEND)

    return answer


def host(
    lines: dict[str, list[str]] = WRITTEN, reask: Callable[[str], str] | None = None
) -> FakeHost:
    edges = edges_with(lines)
    if reask is not None:
        first = edges

        def edges(text: str) -> str:
            return reask(text) if "→ " in text else first(text)

    return FakeHost(lambda p: "", edits={"roles.md": roles_answer, EDGES_FILE: edges})


@dataclass
class Built:
    paper: Paper
    store: RecordStore
    root: Path


def build(tmp_path: Path, lines: dict[str, list[str]] = WRITTEN) -> Built:
    paper = make_paper(tmp_path / "paper")
    store = RecordStore(tmp_path / "store")
    review = replay(Built(paper, store, tmp_path), host(lines))
    assert review.complete, review.unanswered
    return Built(paper, store, tmp_path)


def replay(b: Built, fake: FakeHost | None = None) -> Review:
    runner = None if fake is None else Runner(fake, Resolver(b.store), 10.0, FLAT)
    return review_cut(b.paper.document, b.paper.cut, b.store, SETTINGS, runner)


def exported(b: Built, review: Review) -> str:
    return dumps(review_payload(b.paper.document, review, SETTINGS))


def edges_written(b: Built, review: Review) -> tuple[str, object]:
    question = next(
        q for q in review.questions[Stage.EDGES] if q.key == review.edges_answer.key
    )
    return record_file.text(b.store.directory, review.edges_answer), question.input_cut


def give(
    b: Built,
    layer: Layer,
    edit: Callable[[str], str],
    review: Review | None = None,
    settings: ReviewSettings = SETTINGS,
) -> AnswerRecord:
    """A person's edit of the stage's file on the state `review` left (the
    build replayed where None), recorded as `axiomation edit` records it."""
    review = review or replay(b)
    assert review.complete, review.unanswered
    question, edited = shown_edit(b, layer, edit, review, settings)
    found = person.checked(question, edited, checker(b.paper.document))
    assert found.valid, found.problems
    return (
        Resolver(b.store)
        .record(
            Attempt(
                question,
                PERSON,
                PERSON,
                edited,
                0.0,
                0.0,
                found,
                version=document_hash(b.paper.document),
            )
        )
        .answer
    )


def shown_edit(
    b: Built,
    layer: Layer,
    edit: Callable[[str], str],
    review: Review,
    settings: ReviewSettings = SETTINGS,
) -> tuple[Question, str]:
    """The person's question and file for `edit` on the state `review`
    left."""
    cut = review.corrected.cut
    sheet = None
    if layer is Layer.EDGES:
        raw, on = edges_written(b, review)
        sheet = person_sheet(b.paper.document, raw, on, cut, settings.files)
    shown = person.stage_question(b.paper.document, cut, layer, settings, sheet)
    edited = edit(person.shown_file(shown))
    return (
        person.person_question(b.paper.document, cut, layer, settings, sheet, edited),
        edited,
    )


def reask_of(review: Review) -> Question:
    (question,) = [
        q for q in review.questions[Stage.EDGES] if q.key in review.unanswered
    ]
    return question


def reopened(question: Question) -> set[str]:
    """The owners a re-ask reopened: every other owner's lines are fixed."""
    return set(json.loads(question.workspace.gate.spec)["reopened"])


def edited_edges(text: str) -> str:
    """A person's edges: the footnote's uses edge deleted, the closing
    words made to use the theorem."""
    return text.replace(DROPPED + "\n", "").replace(
        '→ about scope:paper · "Closing words of the results."',
        '→ about scope:paper · "Closing words of the results."\n' + WROTE,
    )


# --- the edges: the edge half, nothing asked ---------------------------------------


@pytest.mark.usefixtures("develop_mode")
def test_a_person_s_edges_answer_asks_nothing_and_becomes_the_edge_half(
    tmp_path,
) -> None:
    b = build(tmp_path)
    given = give(b, Layer.EDGES, edited_edges)
    assert (given.host, given.model, given.cost) == (PERSON, PERSON, 0.0)
    assert given.version == document_hash(b.paper.document)

    fake = host()
    review = replay(b, fake)

    assert fake.calls == 0 and review.complete
    assert review.edges_answer is not None and review.edges_answer.key == given.key
    assert [r.key for r in review.person.applied] == [given.key]
    payload = json.loads(exported(b, review))
    # the edge half is the person's; the line the person wrote theirs, the
    # rest the model's
    assert payload["edges_answer"] == given.key
    wrote = [e for e in payload["edges"] if e["provenance"]["question"] == given.key]
    assert [(e["kind"], e["provenance"]["by"]) for e in wrote] == [("uses", "person")]
    assert wrote[0]["evidence"][0]["text"] == "Closing words of the results."
    assert "small" not in json.dumps([e["target"] for e in payload["edges"]])
    pins = payload["review"]["person"]["pins"]
    assert [(p["kind"], p["line"], p["state"]) for p in pins] == [
        ("deleted", DROPPED, "held"),
        ("written", WROTE, "held"),
    ]
    assert payload["review"]["person"]["applied"] == [
        {"key": given.key, "stage": "edges", "round": 1}
    ]
    assert payload["review"]["person"]["stale"] == []
    assert exported(b, replay(b)) == exported(b, review)


# --- the type rule, per layer ------------------------------------------------------


@pytest.mark.usefixtures("develop_mode")
def test_a_role_edit_reopens_exactly_the_type_rule_s_owners(tmp_path) -> None:
    """The definition made an assumption: itself (its standing, self mark
    and parts), and the source of the qualifies edge onto it; the proof's
    uses edge onto it stands."""
    b = build(tmp_path)
    given = give(
        b, Layer.ROLES, lambda t: t.replace("⟦b3 definition⟧", "⟦b3 assumption⟧")
    )

    review = replay(b)

    question = reask_of(review)
    assert reopened(question) == {DEFINITION, "we-fix-a-field"}
    assert PROOF_BLOCK not in reopened(question)
    assert review.person.pending is not None
    assert review.person.pending.asked == (question.key,)
    # taken back: the export is the state before, the re-ask unanswered
    cut = review.corrected.cut
    assert [r.value for r in cut.block(b.paper.id_at("\\begin{definition}")).roles] == [
        "definition"
    ]

    fake = host(reask=done)
    answered = replay(b, fake)
    assert answered.complete and fake.calls == 1
    assert [r.reopened for r in answered.person.applied] == [
        ("we-fix-a-field", DEFINITION)
    ]
    definition = answered.corrected.cut.block(b.paper.id_at("\\begin{definition}"))
    assert [r.value for r in definition.roles] == ["assumption"]
    data = json.loads(exported(b, answered))
    (correction,) = data["review"]["cut_corrections"]
    assert (correction["by"], correction["stage"], correction["round"]) == (
        PERSON,
        "roles",
        1,
    )
    assert correction["applied"]
    assert data["review"]["person"]["applied"] == [
        {"key": given.key, "stage": "roles", "round": 1}
    ]
    (pin,) = data["review"]["person"]["pins"]
    assert pin == {
        "layer": "roles",
        "block": b.paper.id_at("\\begin{definition}"),
        "kind": "changed",
        "line": "definition → assumption",
        "state": "held",
        "rank": 1,
    }
    assert exported(b, replay(b)) == exported(b, answered)


@pytest.mark.usefixtures("develop_mode")
def test_a_group_edit_reopens_its_justifies_edge_and_the_about_edges_onto_it(
    tmp_path,
) -> None:
    lines = {
        **WRITTEN,
        CLOSING: [
            '→ about scope:paper · "Closing words of the results."',
            f'→ about {PROOF} · "Closing words of the results."',
        ],
    }
    b = build(tmp_path, lines)
    end = "⟦end group by_definition_the_gain⟧"
    sharp = "⟦b8 claim⟧\nNext, the bound is sharp.\n\\end{proof}"
    give(
        b,
        Layer.GROUPS,
        lambda t: t.replace(f"{sharp}\n\n{end}", f"{end}\n\n{sharp}"),
    )

    review = replay(b)

    assert reopened(reask_of(review)) == {PROOF, CLOSING}


@pytest.mark.usefixtures("develop_mode")
def test_a_cut_edit_asks_the_touched_roles_then_the_edges_it_touches(
    tmp_path,
) -> None:
    b = build(tmp_path)
    give(b, Layer.CUT, lambda t: t.replace(*SPLIT))

    review = replay(b)

    (roles,) = [q for q in review.questions[Stage.ROLES] if q.key in review.unanswered]
    assert [h for h in roles.handles if h.startswith("b")] == ["b1", "b2"]
    assert review.person.pending.reopened == ("we-fix-a-field", "a-second-sentence-of")
    assert len(review.corrected.cut.blocks) == len(b.paper.cut.blocks)

    fake = host(reask=done)
    answered = replay(b, fake)
    assert answered.complete and fake.calls == 2
    (round_,) = answered.person.applied
    assert round_.reopened == ("we-fix-a-field", "a-second-sentence-of")
    assert len(answered.corrected.cut.blocks) == len(b.paper.cut.blocks) + 1
    (pin,) = round_.pins
    assert (pin.layer, pin.kind, pin.owner) == (
        Layer.CUT,
        person.PinKind.ADDED,
        b.paper.id_at("A second sentence"),
    )


@pytest.mark.usefixtures("develop_mode")
def test_a_cut_edit_shows_a_roles_pinned_block_it_kept_as_context(tmp_path) -> None:
    """A person's roles on the setting, then a person's split of it: the
    setting keeps its start and its pin, and only the new block is asked
    its roles."""
    b = build(tmp_path)
    give(
        b,
        Layer.ROLES,
        lambda t: t.replace(
            "⟦+ none⟧\nA second sentence", "⟦+ motivation⟧\nA second sentence"
        ),
    )
    give(b, Layer.CUT, lambda t: t.replace(*SPLIT), replay(b, host(reask=done)))

    review = replay(b)

    (roles,) = [q for q in review.questions[Stage.ROLES] if q.key in review.unanswered]
    assert [roles.handles[h] for h in roles.handles if h.startswith("b")] == [
        b.paper.id_at("A second sentence")
    ]
    answered = replay(b, host(reask=done))
    assert answered.complete, answered.unanswered
    (pin,) = [p for p in answered.person.pins if p.layer is Layer.ROLES]
    assert pin.owner == b.paper.id_at("We fix a field") and pin.held


@pytest.mark.usefixtures("develop_mode")
def test_a_person_s_edge_half_keeps_the_model_s_flags(tmp_path) -> None:
    flag = '? role "Closing words of the results." — reads as a claim'
    b = build(tmp_path, {**WRITTEN, CLOSING: [*WRITTEN[CLOSING], flag]})
    assert "role" in [r.flag.signal.value for r in replay(b).flags]
    given = give(b, Layer.EDGES, edited_edges)

    review = replay(b)

    assert review.edges_answer.key == given.key
    assert "role" in [r.flag.signal.value for r in review.flags]


@pytest.mark.usefixtures("develop_mode")
def test_a_person_round_s_reask_applies_no_cut_line_and_asks_no_roles(
    tmp_path,
) -> None:
    b = build(tmp_path)
    give(b, Layer.ROLES, lambda t: t.replace("⟦b3 definition⟧", "⟦b3 assumption⟧"))

    def proposing(text: str) -> str:
        return done(
            add_lines(
                text,
                {
                    DEFINITION: [
                        '✂ split "The gain" · "The gain is the ratio" — two things',
                        '? role "The gain is the ratio" — reads as a definition',
                    ]
                },
            )
        )

    review = replay(b, host(reask=proposing))

    assert review.complete
    (proposal,) = [p for p in review.proposals if p.by is None]
    assert not proposal.applied and proposal.problem == PERSON_NO_CUT
    assert len(review.questions.get(Stage.ROLES, ())) == 1  # the build's own
    assert any(r.flag.signal.value == "role" for r in review.flags)


@pytest.mark.usefixtures("develop_mode")
def test_a_run_s_cursor_names_the_person_rounds_it_applied(tmp_path) -> None:
    """The person rounds are applied by the run, its cursor after them
    (`docs/architecture.md` § 4)."""
    b = build(tmp_path)
    given = give(b, Layer.EDGES, edited_edges)
    run = RunFile(b.store.directory, "build", "v", "made")

    review = review_cut(b.paper.document, b.paper.cut, b.store, SETTINGS, None, run)

    assert review.edges_answer.key == given.key
    held = run_state.read(b.store.directory, "build", "v", "made")
    assert held is not None and held.cursor.stage == "person-rounds"
    assert held.cursor.person_rounds == (given.key,)
    assert held.cursor.edges_answer == given.key


# --- pins --------------------------------------------------------------------------


@pytest.mark.usefixtures("develop_mode")
def test_a_pinned_line_survives_a_reask_that_reopens_its_owner(tmp_path) -> None:
    b = build(tmp_path)
    give(b, Layer.EDGES, edited_edges)
    give(
        b,
        Layer.ROLES,
        lambda t: t.replace("⟦b9 none⟧", "⟦b9 motivation⟧").replace(
            "⟦b6 justification⟧", "⟦b6 motivation⟧"
        ),
    )

    review = replay(b)

    question = reask_of(review)
    assert {CLOSING, PROOF_BLOCK} <= set(reopened(question))
    sheet = question.workspace.files[EDGES_FILE]
    assert WROTE in sheet and DROPPED not in sheet
    assert check(question, done(sheet), b.paper.document).valid
    removed = check(question, done(sheet.replace(WROTE + "\n", "")), b.paper.document)
    assert any("is a person's, pinned" in p for p in removed.problems)
    back = done(
        sheet.replace(
            '⊢ self · "Hence the output is bounded."',
            '⊢ self · "Hence the output is bounded."\n' + DROPPED,
        )
    )
    found = check(question, back, b.paper.document)
    assert any("was deleted by a person" in p for p in found.problems)


CONJECTURED = '⊢ conjectured · "Next, the bound is sharp."'
QUALIFIES = '→ qualifies thm:bound · "We fix a field of scalars."'


@pytest.mark.usefixtures("develop_mode")
@pytest.mark.parametrize(
    ("edges_edit", "roles_edit", "line", "why"),
    [
        (
            lambda t: t.replace(
                '⊢ unsupported · "Next, the bound is sharp."', CONJECTURED
            ),
            lambda t: t.replace("⟦b8 claim⟧", "⟦b8 motivation⟧"),
            CONJECTURED,
            "a support line on a block with no claim",
        ),
        (
            lambda t: t.replace(
                '→ qualifies def:gain · "We fix a field of scalars."', QUALIFIES
            ),
            lambda t: t.replace("⟦b2 assumption⟧", "⟦b2 motivation⟧"),
            QUALIFIES,
            "qualifies runs from a block with an assumption role",
        ),
    ],
)
def test_a_pinned_line_the_roles_no_longer_allow_lapses_rather_than_deadlock(
    tmp_path, edges_edit, roles_edit, line, why
) -> None:
    """A person's line, then a person's roles edit under which the gate
    refuses it: the pin lapses, the re-ask may take the line off, and the
    export says why."""
    b = build(tmp_path)
    give(b, Layer.EDGES, edges_edit)
    give(b, Layer.ROLES, roles_edit)

    review = replay(b)
    spec = json.loads(reask_of(review).workspace.gate.spec)
    assert line not in [pin[1] for pin in spec.get("pins", [])]

    def without(text: str) -> str:
        return done(text.replace(line + "\n", ""))

    answered = replay(b, host(reask=without))
    assert answered.complete, answered.unanswered
    data = json.loads(exported(b, answered))
    (pin,) = [p for p in data["review"]["person"]["pins"] if p["line"] == line]
    assert pin["state"] == f"lapsed (structural: {why})"


@pytest.mark.usefixtures("develop_mode")
@pytest.mark.parametrize(
    ("line", "why"),
    [
        (WROTE, None),
        (
            '→ uses closing-words-of-the · "Closing words"',
            "an edge inside its own block",
        ),
        ('→ uses thm:bound · "Not there."', f"evidence not in {CLOSING}"),
        (
            '→ qualifies scope:paper · "Closing words of the results."',
            "qualifies targets a block or a part of one",
        ),
        (
            f'→ qualifies {PROOF} · "Closing words of the results."',
            "qualifies runs between blocks, never a group",
        ),
        (
            '→ about file:notes.txt · "Closing words of the results."',
            "not a file of the paper",
        ),
        ('→ uses thm:bound [nope] · "Closing words of the results."', "no part [nope]"),
    ],
)
def test_a_pin_the_gate_s_structure_refuses_lapses_before_the_session(
    tmp_path, line, why
) -> None:
    """The whole-sheet rule: the sheet shown, with the
    held pins in place, through the edges gate's own check; the pins a
    failing check names lapse, `lapsed (structural: <check>)`."""
    b = build(tmp_path)
    review = replay(b)
    sheet = record_file.text(b.store.directory, review.edges_answer)
    spec = edges_structure(b.paper.document, review.corrected.cut, FILES)
    pin = person.Pin(Layer.EDGES, CLOSING, person.PinKind.WRITTEN, line)

    (found,) = person.held_against([pin], sheet, None, spec)

    if why is None:
        assert found.held
    else:
        assert found.state.startswith("lapsed (structural: "), found.state
        assert why in found.state


@pytest.mark.usefixtures("develop_mode")
def test_a_fixed_owner_s_pin_holds_as_shown_through_a_rename(tmp_path) -> None:
    """Under an owner the gate fixes, a pin that differs from the shown
    line only by a part's collision suffix is that line; one the sheet does
    not show lapses, so the fixed lines and the pins never contradict."""
    b = build(tmp_path)
    review = replay(b)
    sheet = record_file.text(b.store.directory, review.edges_answer)
    spec = edges_structure(b.paper.document, review.corrected.cut, FILES)
    shown_line = '→ uses def:gain [gain] ref · "By Definition~\\ref{def:gain}"'
    renamed = shown_line.replace("[gain]", "[gain-2]")
    pins = [
        person.Pin(
            Layer.EDGES,
            PROOF_BLOCK,
            person.PinKind.WRITTEN,
            renamed,
            words=("The gain is the ratio of output to input.",),
        ),
        person.Pin(Layer.EDGES, CLOSING, person.PinKind.WRITTEN, WROTE),
    ]

    found = person.held_against(pins, sheet, set(), spec)

    assert (found[0].line, found[0].held) == (shown_line, True)
    assert found[1].state == "lapsed (the sheet shows it otherwise)"


def test_weigh_drops_a_correction_that_moves_a_pinned_start(tmp_path) -> None:
    b = build(tmp_path)
    cut = replay(b).corrected.cut
    head, tail = b.paper.id_at("\\begin{proof}"), b.paper.id_at("Next, the bound")
    merge = CutCorrection(
        id="m",
        op=CutOp.MERGE,
        cls=CorrectionClass.OTHER,
        why="one paragraph",
        answer="a",
        blocks=(head, tail),
        evidence=("Next, the bound is sharp.",),
    )

    held = weigh(b.paper.document, cut, [], cut, [merge], 1, "edges")
    pinned = weigh(
        b.paper.document, cut, [], cut, [merge], 1, "edges", Pinned(starts={tail})
    )
    whole = weigh(
        b.paper.document, cut, [], cut, [merge], 1, "edges", Pinned(whole={head})
    )

    assert held[0].applied
    assert not pinned[0].applied and pinned[0].problem == PINNED + tail
    assert not whole[0].applied and whole[0].problem == PINNED + head


def test_weigh_drops_a_rebound_an_ungroup_and_a_start_made_again(tmp_path) -> None:
    b = build(tmp_path)
    cut = replay(b).corrected.cut
    (group,) = cut.groups
    head, tail = b.paper.id_at("\\begin{proof}"), b.paper.id_at("Next, the bound")
    setting = b.paper.id_at("We fix a field")
    rebound = made(
        "r",
        CutOp.REBOUND,
        blocks=(head, tail),
        at=("Hence the output is bounded.",),
        evidence=("Hence the output is bounded.",),
    )
    split = made(
        "s",
        CutOp.SPLIT,
        block=setting,
        at=("A second sentence",),
        evidence=("A second sentence of the setting.",),
    )
    ungroup = made("u", CutOp.UNGROUP, group=group.id)
    merge = made(
        "m", CutOp.MERGE, blocks=(head, tail), evidence=("Next, the bound is sharp.",)
    )

    def weighed(correction: CutCorrection, pinned: Pinned | None = None):
        (found,) = weigh(
            b.paper.document, cut, [], cut, [correction], 1, "edges", pinned
        )
        return found

    for correction in (rebound, split, ungroup, merge):
        assert weighed(correction).applied, correction.id
    for correction, pinned, at in (
        (rebound, Pinned(roles=frozenset({tail})), tail),
        (ungroup, Pinned(groups=frozenset({group.id})), group.id),
        (merge, Pinned(members=frozenset(group.blocks)), head),
    ):
        found = weighed(correction, pinned)
        assert not found.applied and found.problem.startswith(PINNED), correction.id
        assert at in found.problem


# --- what the person round refuses and reports -------------------------------------


@pytest.mark.usefixtures("develop_mode")
@pytest.mark.parametrize(
    ("layer", "edit", "problem"),
    [
        (
            Layer.EDGES,
            lambda t: t.replace(
                f"⟦done {DEFINITION}⟧",
                '✂ split "The gain" · "The gain is the ratio" — two\n'
                f"⟦done {DEFINITION}⟧",
            ),
            "a cut line is not a person's",
        ),
        (
            Layer.EDGES,
            lambda t: t.replace(
                f"⟦done {DEFINITION}⟧",
                f'? role "The gain is the ratio" — a claim\n⟦done {DEFINITION}⟧',
            ),
            "a `?` flag asks a later step",
        ),
        (
            Layer.EDGES,
            lambda t: t.replace(
                WRITTEN[CLOSING][0], '→ uses thm:bound · "No such words."'
            ),
            "No such words",
        ),
        (
            Layer.ROLES,
            lambda t: t.replace("⟦b3 definition⟧", "⟦b3 theorem⟧"),
            "theorem",
        ),
    ],
)
def test_a_person_s_answer_failing_its_stage_s_check_is_refused(
    tmp_path, layer, edit, problem
) -> None:
    b = build(tmp_path)
    question, edited = shown_edit(b, layer, edit, replay(b))

    found = person.checked(question, edited, checker(b.paper.document))

    assert not found.valid
    assert any(problem in p for p in found.problems), found.problems


@pytest.mark.usefixtures("develop_mode")
def test_a_stale_person_answer_is_reported_and_applies_nothing(tmp_path) -> None:
    b = build(tmp_path)
    review = replay(b)
    first = give(
        b, Layer.ROLES, lambda t: t.replace("⟦b9 none⟧", "⟦b9 motivation⟧"), review
    )
    # given on the same state as the first, which no longer stands after it
    second = give(
        b, Layer.ROLES, lambda t: t.replace("⟦b3 definition⟧", "⟦b3 claim⟧"), review
    )

    answered = replay(b, host(reask=done))

    assert answered.complete
    assert [r.key for r in answered.person.applied] == [first.key]
    assert answered.person.stale == (second.key,)
    definition = answered.corrected.cut.block(b.paper.id_at("\\begin{definition}"))
    assert [r.value for r in definition.roles] == ["definition"]
    data = json.loads(exported(b, answered))
    assert data["review"]["person"]["stale"] == [second.key]


def test_an_export_without_a_person_s_answer_is_as_before(tmp_path) -> None:
    b = build(tmp_path)

    data = json.loads(exported(b, replay(b)))

    assert "person" not in data["review"]
    assert not replay(b).person


@pytest.mark.usefixtures("develop_mode")
def test_a_person_s_answer_on_another_version_is_neither_applied_nor_stale(
    tmp_path,
) -> None:
    b = build(tmp_path)
    given = give(b, Layer.EDGES, edited_edges)
    record_file.rewrite_answers(
        tmp_path / "store",
        lambda a: (
            {**a, "version": "0" * 64} if a.get("version") == given.version else a
        ),
    )

    review = replay(b)

    assert not review.person and review.edges_answer.key != given.key


# --- across versions ---------------------------------------------------------------

#: A person's edges on OLD: a line written under the closing words (a same
#: block in NEW), the setting's qualifies line deleted (the setting changes
#: in NEW), and a uses line written under the definition (whose meaning the
#: rule moves when the setting narrows).
USES_SETTING = '→ uses we-fix-a-field · "The gain is the ratio"'


def old_person_edges(text: str) -> str:
    return (
        text.replace('→ qualifies def:gain · "We fix a field of scalars."\n', "")
        .replace(f"⟦done {DEFINITION}⟧", f"{USES_SETTING}\n⟦done {DEFINITION}⟧")
        .replace(
            '→ about scope:paper · "Closing words of the results."',
            '→ about scope:paper · "Closing words of the results."\n' + WROTE,
        )
    )


def versions_with_pins(
    tmp_path: Path,
    main_text: str = EDITED_SETTING,
    layer: Layer = Layer.EDGES,
    edit: Callable[[str], str] = old_person_edges,
    reask: FakeHost | None = None,
    old_main: str = MAIN,
    lines: dict[str, list[str]] = WRITTEN,
    header: list[str] = LEGEND[:1],
):
    """OLD built as the update tests build it (`old_main`, its edges
    `lines` under `header`), a person's edit given on it (its re-ask
    answered by `reask`), its export written; NEW written, and the
    identity answer made by `diff --cut --store`. No ambient frame by
    default: a narrowed setting moves only what uses it."""
    old, store = build_old(tmp_path, lines, header, old_main)
    b = Built(old, store, tmp_path)

    def old_replay(fake=None) -> Review:
        from questions.test_roles_file import first_pass

        runner = None if fake is None else Runner(fake, Resolver(store), 10.0, FLAT)
        return review_cut(old.document, first_pass(old), store, OLD_SETTINGS, runner)

    give(b, layer, edit, old_replay(), OLD_SETTINGS)
    review = old_replay(reask)
    assert review.complete and review.person.applied
    (tmp_path / "old.json").write_text(
        dumps(review_payload(old.document, review, OLD_SETTINGS)), encoding="utf-8"
    )
    new = make_paper(tmp_path / "new", main=main_text)
    args = ["diff", str(old.root), str(new.root), "--displays", "in-paragraph"]
    args += ["--cut", str(tmp_path / "old.json"), "--store", str(store.directory)]
    assert main(args) == 0
    return old, new, store


@pytest.mark.usefixtures("develop_mode")
def test_a_pinned_line_survives_an_update_and_a_pin_on_a_changed_block_lapses(
    tmp_path,
) -> None:
    old, new, store = versions_with_pins(tmp_path)
    (identity,) = [a for a in store.authoritative().values() if a.kind == "identity"]
    pins = versions.identity_raw(store, identity)["carried"]["pins"]
    assert [(p["owner"], p["kind"], p["state"]) for p in pins] == [
        (SETTING, "deleted", "lapsed (its block changed)"),
        (DEFINITION, "written", "held"),
        (CLOSING, "written", "held"),
    ]

    _, made = plan(new.document, store, UPDATE)
    question = made.question
    spec = json.loads(question.workspace.gate.spec)
    assert [p[:2] for p in spec["pins"]] == [
        [DEFINITION, USES_SETTING],
        [CLOSING, WROTE],
    ]
    sheet = question.workspace.files[EDGES_FILE]
    assert f"⟦done {CLOSING}⟧" not in sheet  # opened: its scope:paper edge
    answer = answering(
        {
            SETTING: ["Δ narrowed — the setting now says less"],
            DEFINITION: ['Δ unchanged — "The gain is the ratio": it reads the same'],
            CLOSING: ["Δ narrowed — it now closes less"],
        },
        without_qualifies,
    )
    assert check(question, answer(sheet), new.document).valid
    lost = answer(sheet).replace(WROTE + "\n", "")
    assert any(
        "is a person's, pinned" in p
        for p in check(question, lost, new.document).problems
    )

    fake = FakeHost(lambda p: "", edits={EDGES_FILE: answer})
    found = run(new.document, store, UPDATE, Runner(fake, Resolver(store), 10.0, FLAT))
    assert found.complete, found.unanswered
    payload = update_payload(new.document, found, store)
    reported = {
        (p["block"], p["kind"]): p["state"] for p in payload["review"]["person"]["pins"]
    }
    assert reported == {
        (SETTING, "deleted"): "lapsed (its block changed)",
        (new.id_at("\\begin{definition}"), "written"): "held, meaning moved (by rule)",
        (new.id_at("Closing words"), "written"): "held, meaning moved (Δ narrowed)",
    }
    replayed = update_payload(new.document, run(new.document, store, UPDATE), store)
    assert dumps(replayed) == dumps(payload)


@pytest.mark.usefixtures("develop_mode")
@pytest.mark.parametrize(
    ("closing", "why"),
    [
        ("Closing words of\n\nthe results.", "its block was split"),
        ("", "its block was deleted"),
    ],
)
def test_an_edges_pin_on_a_block_split_or_deleted_lapses(
    tmp_path, closing, why
) -> None:
    text = EDITED_SETTING.replace(
        "Closing words of the results.\n", closing and closing + "\n"
    )
    _, _, store = versions_with_pins(tmp_path, text)
    (identity,) = [a for a in store.authoritative().values() if a.kind == "identity"]
    pins = versions.identity_raw(store, identity)["carried"]["pins"]

    (pin,) = [p for p in pins if p["line"] == WROTE]

    assert pin["state"] == f"lapsed ({why})"


SHARP = "next-the-bound-is"
SHARP_UNSUPPORTED = '⊢ unsupported · "Next, the bound is sharp."'
SHARP_CONJECTURED = '⊢ conjectured · "Next, the bound is sharp."'


@pytest.mark.usefixtures("develop_mode")
def test_a_deletion_pin_on_an_unchanged_block_holds_beside_a_line_on_its_claim(
    tmp_path,
) -> None:
    """A person replaced a standing by another on the same claim, on a block
    NEW leaves as it was: the deletion pin holds. Put back to be carried,
    the deleted line would stand beside the written one on one claim, and
    the carry would mint both a handle, which no diff of the two carries
    may read as the person's edit."""

    def restanded(text: str) -> str:
        return text.replace(SHARP_UNSUPPORTED, SHARP_CONJECTURED)

    _, _, store = versions_with_pins(tmp_path, edit=restanded)
    (identity,) = [a for a in store.authoritative().values() if a.kind == "identity"]
    pins = versions.identity_raw(store, identity)["carried"]["pins"]

    assert sorted((p["owner"], p["kind"], p["line"], p["state"]) for p in pins) == [
        (SHARP, "deleted", SHARP_UNSUPPORTED, "held"),
        (SHARP, "written", SHARP_CONJECTURED, "held"),
    ]


@pytest.mark.parametrize(
    ("cls", "state"),
    [
        ("same", "held"),
        ("markup", "held"),
        ("changed", "lapsed (its block changed)"),
        ("split", "lapsed (its block was split)"),
        ("merged", "lapsed (its block was merged)"),
        ("deleted", "lapsed (its block was deleted)"),
    ],
)
def test_a_roles_pin_follows_a_one_to_one_block_and_lapses_on_another(
    tmp_path, cls, state
) -> None:
    from axiomation.identity import BlockClass, Lineage, Link, Tier

    b = build(tmp_path)
    cut = replay(b).corrected.cut
    block = b.paper.id_at("Closing words")
    new = {
        "split": ("n1", "n2"),
        "merged": ("n1",),
        "deleted": (),
    }.get(cls, ("n1",))
    lineage = Lineage((Link((block,), new, BlockClass(cls), Tier.HASH),))
    exported_pins = [
        {
            "layer": "roles",
            "block": block,
            "kind": "changed",
            "line": "",
            "state": "held",
        }
    ]

    (pin,) = person.carried_pins(
        b.paper.document, b.paper.document, "", cut, cut, FILES, lineage, exported_pins
    )

    assert pin.state == state
    assert pin.owner == ("n1" if state == "held" else block)


@pytest.mark.usefixtures("develop_mode")
def test_a_roles_pinned_block_is_never_asked_its_roles_in_an_update(
    tmp_path,
) -> None:
    """A person made the closing words a motivation on OLD; on NEW the
    update session flags their role: the round would ask it, but a
    person's roles are never asked, and the flag stays a flag."""
    old, new, store = versions_with_pins(
        tmp_path,
        layer=Layer.ROLES,
        edit=lambda t: t.replace("⟦b9 none⟧", "⟦b9 motivation⟧"),
        reask=FakeHost(lambda p: "", edits={EDGES_FILE: done}),
    )
    _, made = plan(new.document, store, UPDATE)
    closing = new.id_at("Closing words")
    assert closing in made.pinned.roles and closing in made.pinned.whole
    flag = '? role "Closing words of the results." — reads as a claim'
    answer = answering(
        {SETTING: ["Δ reworded — a clause added"], CLOSING: [flag]},
        without_qualifies,
    )
    settings = dataclasses.replace(
        UPDATE,
        review=ReviewSettings(stages=(Stage.EDGES,), cut_rounds=1, files=FILES),
    )
    fake = FakeHost(lambda p: "", edits={EDGES_FILE: answer, "roles.md": roles_answer})

    found = run(
        new.document, store, settings, Runner(fake, Resolver(store), 10.0, FLAT)
    )

    assert found.complete and fake.calls == 1
    assert Stage.ROLES not in found.questions and not found.cut_rounds
    assert [f.signal.value for f in store.flags_of(found.answer.key)] == ["role"]
    payload = update_payload(new.document, found, store)
    assert [p["kind"] for p in payload["review"]["person"]["pins"]] == ["changed"]
    assert payload["review"]["person"]["pins"][0]["block"] == closing


@pytest.mark.usefixtures("develop_mode")
def test_a_person_s_edit_on_the_new_version_applies_after_the_update(
    tmp_path,
) -> None:
    from test_update import versions

    v = versions(tmp_path, EDITED_SETTING)
    first = answering({SETTING: ["Δ reworded — a clause added"]}, without_qualifies)
    found = run(
        v.new.document,
        v.store,
        UPDATE,
        Runner(
            FakeHost(lambda p: "", edits={EDGES_FILE: first}),
            Resolver(v.store),
            10.0,
            FLAT,
        ),
    )
    assert (
        found.complete
        and "person" not in update_payload(v.new.document, found, v.store)["review"]
    )
    settings = dataclasses.replace(UPDATE.review, files=UPDATE.files)
    answer = found.edge_answer
    on = next(q for q in found.questions[Stage.EDGES] if q.key == answer.key).input_cut
    sheet = person_sheet(v.new.document, found.sheet(answer), on, found.plan.cut, FILES)

    def recorded(layer: Layer, edit: Callable[[str], str], sheet=None) -> AnswerRecord:
        shown = person.stage_question(
            v.new.document, found.plan.cut, layer, settings, sheet
        )
        edited = edit(person.shown_file(shown))
        question = person.person_question(
            v.new.document, found.plan.cut, layer, settings, sheet, edited
        )
        valid = person.checked(question, edited, checker(v.new.document))
        assert valid.valid, valid.problems
        attempt = Attempt(
            question,
            PERSON,
            PERSON,
            edited,
            0.0,
            0.0,
            valid,
            version=document_hash(v.new.document),
        )
        return Resolver(v.store).record(attempt).answer

    given = recorded(Layer.EDGES, edited_edges, sheet)
    again = run(v.new.document, v.store, UPDATE)
    assert again.complete and again.edge_answer.key == given.key
    payload = update_payload(v.new.document, again, v.store)
    assert payload["review"]["person"]["applied"] == [
        {"key": given.key, "stage": "edges", "round": 1}
    ]
    assert payload["edges_answer"] == given.key

    # a roles edit after it: its re-ask unanswered, the update is not whole
    roles = recorded(Layer.ROLES, lambda t: t.replace("⟦b9 none⟧", "⟦b9 motivation⟧"))
    pending = run(v.new.document, v.store, UPDATE)
    assert not pending.complete and pending.person.pending.key == roles.key
    (key,) = pending.unanswered
    assert asked_as(pending)[key] == "edges, person round 2"
    with pytest.raises(UpdateError):
        update_payload(v.new.document, pending, v.store)


def made(cid: str, op: CutOp, **fields) -> CutCorrection:
    return CutCorrection(
        id=cid, op=op, cls=CorrectionClass.OTHER, why="w", answer="a", **fields
    )


def test_step_three_drops_a_groups_session_s_change_of_a_pinned_group(
    tmp_path,
) -> None:
    """A groups session changes a group as an ungroup and a group: both are
    dropped where the group is a person's, and a group that takes in one of
    its blocks too."""
    b = build(tmp_path)
    cut = replay(b).corrected.cut
    (group,) = cut.groups
    head, footnote = group.blocks[0], group.blocks[1]
    pinned = person.pinned_of(
        [person.Pin(Layer.GROUPS, group.id, person.PinKind.CHANGED, "")], cut=cut
    )
    ungroup = made("u", CutOp.UNGROUP, group=group.id)
    regroup = made(
        "g", CutOp.GROUP, kind=group.kind, first=head, last=footnote, names="none"
    )
    other = made(
        "o",
        CutOp.GROUP,
        kind=group.kind,
        first=b.paper.id_at("Closing words"),
        last=b.paper.id_at("Closing words"),
        names="none",
    )

    kept, dropped = _unpinned_groups([ungroup, regroup, other], pinned, cut)

    assert kept == [other]
    assert [(p.correction.id, p.problem) for p in dropped] == [
        ("u", PINNED + group.id),
        ("g", PINNED + group.id),
    ]


def test_an_update_without_a_pin_reports_no_person(tmp_path) -> None:
    from test_update import versions

    v = versions(tmp_path, EDITED_SETTING)
    _, made = plan(v.new.document, v.store, UPDATE)
    assert made.pins == () and not made.pinned
    assert (
        "pins"
        not in identity_raw(
            v.store,
            next(a for a in v.store.authoritative().values() if a.kind == "identity"),
        )["carried"]
    )


# --- the commands ------------------------------------------------------------------


def cli_build(
    tmp_path: Path, lines: dict[str, list[str]] = WRITTEN
) -> tuple[Built, list[str]]:
    """A store built as `build PAPER --cut CUT --chunk paper` replays it:
    its settings the command's own, its edges `lines`."""
    from axiomation.cli import parser
    from axiomation.commands import build, seating

    paper = make_paper(tmp_path / "paper")
    (tmp_path / "cut.json").write_text(
        dumps(cut_payload(paper.document, paper.cut)), encoding="utf-8"
    )
    store = RecordStore(tmp_path / "store")
    args = [str(paper.root), "--store", str(store.directory)]
    args += ["--cut", str(tmp_path / "cut.json"), "--chunk", "paper", "--overlap", "0"]
    parsed = parser().parse_args(["build", *args])
    asked = build.request(parsed)
    profile = seating.profile_of(asked.asking)
    settings = build.review_settings_of(asked, profile, build=True)
    document, found, settings = build.review_input(asked, settings)
    fake = FakeHost(
        lambda p: "",
        edits={
            "roles.md": roles_answer,
            "groups.md": lambda t: t,
            EDGES_FILE: edges_with(lines),
        },
    )
    review = review_cut(
        document, found, store, settings, Runner(fake, Resolver(store), 10.0, FLAT)
    )
    assert review.complete, review.unanswered
    return Built(paper, store, tmp_path), args


def kept_build(tmp_path: Path, capsys) -> tuple[Built, list[str], list[str]]:
    """`cli_build`'s store, its build kept as a state by the command's
    replay: the store, the build's arguments, and the kept state's."""
    b, args = cli_build(tmp_path)
    capsys.readouterr()
    assert main(["build", *args, "--host", "none"]) == 0
    capsys.readouterr()
    return b, args, [str(b.paper.root), "--store", str(b.store.directory)]


def test_sheet_refuses_a_store_that_keeps_no_state(tmp_path, capsys) -> None:
    _, args = cli_build(tmp_path)
    capsys.readouterr()

    assert main(["sheet", *args[:3], "--stage", "edges"]) == 1

    assert "keeps no state" in capsys.readouterr().err


@pytest.mark.usefixtures("develop_mode")
def test_sheet_edit_and_a_build_replay_round_trip(tmp_path, capsys) -> None:
    b, args, kept = kept_build(tmp_path, capsys)
    sheet = tmp_path / "edges.md"
    assert main(["sheet", *kept, "--stage", "edges", "-o", str(sheet)]) == 0
    text = sheet.read_text(encoding="utf-8")
    assert "⟦done we-fix-a-field⟧" in text and "Files: extra.tex, main.tex" in text
    answers = (b.store.directory / RECORD).read_text(encoding="utf-8")

    bad = tmp_path / "bad.md"
    bad.write_text(
        text.replace(
            f"⟦done {DEFINITION}⟧",
            f'? role "The gain is the ratio" — a claim\n⟦done {DEFINITION}⟧',
        ),
        encoding="utf-8",
    )
    capsys.readouterr()
    assert main(["edit", *kept, "--stage", "edges", "--file", str(bad)]) == 1
    assert "refused, nothing recorded" in capsys.readouterr().err
    assert (b.store.directory / RECORD).read_text(encoding="utf-8") == answers

    sheet.write_text(edited_edges(text), encoding="utf-8")
    capsys.readouterr()
    assert (
        main(["edit", *kept, "--stage", "edges", "--file", str(sheet), "--json"]) == 0
    )
    printed = json.loads(capsys.readouterr().out)
    assert printed["edges"]["total"] == 2
    assert {i["change"] for i in printed["edges"]["items"]} == {"began", "ended"}
    # the new state shows the edit: given again, it changes nothing
    assert main(["edit", *kept, "--stage", "edges", "--file", str(sheet)]) == 1

    out = tmp_path / "out.json"
    capsys.readouterr()
    assert main(["build", *args, "--host", "none", "--replay", "-o", str(out)]) == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["review"]["person"]["applied"][0]["key"] == printed["recorded"]
    assert data["edges_answer"] == printed["recorded"]
    latest = versions.latest(b.store)
    assert latest.id == printed["state"]
    assert out.read_bytes() == versions.kept_export(b.store, latest)


def test_a_roles_edit_left_unanswered_is_pending_and_an_update_refuses(
    tmp_path, capsys
) -> None:
    """A roles edit whose re-ask nothing answers (no host): recorded, no
    state made, the same command resumes it."""
    b, args, kept = kept_build(tmp_path, capsys)
    roles = tmp_path / "roles.md"
    assert main(["sheet", *kept, "--stage", "roles", "-o", str(roles)]) == 0
    roles.write_text(
        roles.read_text(encoding="utf-8").replace("⟦b3 definition⟧", "⟦b3 assumption⟧"),
        encoding="utf-8",
    )
    capsys.readouterr()

    code = main(["edit", *kept, "--stage", "roles", "--file", str(roles)])

    assert code == Exit.UNANSWERED
    out = capsys.readouterr().out
    assert "the same command resumes it" in out and "roles changed" in out
    states = versions.states(b.store)
    assert len(states) == 1  # no state until its re-ask is answered
    capsys.readouterr()
    assert main(["states", *kept]) == 0
    assert "pending on the latest version" in capsys.readouterr().out
    # an update would apply NEW's edits alone: it refuses, naming the edit
    (b.paper.root / "main.tex").write_text(EDITED_SETTING, encoding="utf-8")
    capsys.readouterr()
    assert main(["update", *kept, "--host", "none"]) == 1
    assert "holds edits no state applied: roles" in capsys.readouterr().err


@pytest.mark.usefixtures("develop_mode")
def test_a_person_s_record_round_trips_through_the_store(tmp_path) -> None:
    b = build(tmp_path)
    given = give(b, Layer.EDGES, edited_edges)

    (read,) = [
        a for a in RecordStore(b.store.directory).answers() if a.key == given.key
    ]

    # all but its text, which the record never holds
    assert read == dataclasses.replace(given, raw="")
    assert dataclasses.replace(read, version=None) != dataclasses.replace(given, raw="")


@pytest.mark.usefixtures("develop_mode")
def test_sheet_and_edit_on_an_update_round_trip(tmp_path, capsys) -> None:
    """The command's update from the cutter kept as a state, a person's
    edges on it through `sheet` and `edit`, and the update re-run: the
    export the edit's state holds, and no state added."""
    from test_update import CHAIN, CHAIN_LEGEND, CUTTER, stepping, versions
    from test_update import update as updated
    from test_update_cutter import REWORDED

    v = versions(tmp_path, EDITED_SETTING, CHAIN, CHAIN_LEGEND)
    assert updated(v, stepping(answering(REWORDED)), CUTTER).complete
    args = [str(v.new.root), "--store", str(v.store.directory)]
    capsys.readouterr()
    assert main(["update", *args, "--host", "none"]) == 0  # keeps the update's state
    sheet = tmp_path / "edges.md"
    capsys.readouterr()

    assert main(["sheet", *args, "--stage", "edges", "-o", str(sheet)]) == 0
    text = sheet.read_text(encoding="utf-8")
    # the update's Δ shown beside its block, no ⟦was⟧
    assert "\nΔ [K] reworded — a clause added after it\n" in text
    assert "⟦was⟧" not in text
    sheet.write_text(
        text.replace(
            '→ about scope:paper · "Closing words of the results."',
            '→ about scope:paper · "Closing words of the results."\n' + WROTE,
        ),
        encoding="utf-8",
    )
    capsys.readouterr()
    assert (
        main(["edit", *args, "--stage", "edges", "--file", str(sheet), "--json"]) == 0
    )
    printed = json.loads(capsys.readouterr().out)

    out = tmp_path / "update.json"
    shutil.rmtree(v.old.root)  # the command's re-run reads NEW and the store
    kept = versions_of(v.store)
    assert main(["update", *args, "--host", "none", "-o", str(out)]) == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["review"]["person"]["applied"] == [
        {"key": printed["recorded"], "stage": "edges", "round": 1}
    ]
    assert [p["line"] for p in data["review"]["person"]["pins"]] == [WROTE]
    assert data["edges_answer"] == printed["recorded"]
    assert versions_of(v.store) == kept
    assert out.read_bytes() == kept_export_of(v.store, printed["state"])


def versions_of(store: RecordStore) -> tuple:
    from axiomation import versions as kept_versions

    return kept_versions.states(store)


def kept_export_of(store: RecordStore, state: str) -> bytes:
    from axiomation import versions as kept_versions

    return kept_versions.kept_export(store, kept_versions.state(store, state))


# --- the fresh switch, merges, a standing's edge ---------------------------------


def give_on_new(
    document, store: RecordStore, found, settings, layer: Layer, edit
) -> AnswerRecord:
    """A person's edit on the state an update left, recorded as `edit
    update` records it."""
    review = dataclasses.replace(settings.review, files=settings.files)
    cut = found.corrected.cut if found.corrected is not None else found.plan.cut
    sheet = None
    if layer is Layer.EDGES:
        answer = found.edge_answer
        on = next(q for q in found.questions[Stage.EDGES] if q.key == answer.key)
        sheet = person_sheet(
            document, found.sheet(answer), on.input_cut, cut, settings.files
        )
    shown = person.stage_question(document, cut, layer, review, sheet)
    edited = edit(person.shown_file(shown))
    question = person.person_question(document, cut, layer, review, sheet, edited)
    valid = person.checked(question, edited, checker(document))
    assert valid.valid, valid.problems
    attempt = Attempt(
        question,
        PERSON,
        PERSON,
        edited,
        0.0,
        0.0,
        valid,
        version=document_hash(document),
    )
    return Resolver(store).record(attempt).answer


@pytest.mark.usefixtures("develop_mode")
def test_edges_written_fresh_hold_no_carried_pin(tmp_path) -> None:
    """Past the switch the edges are written fresh, so the carried
    edges pins lapse; a person round on NEW that reopens one block while
    the pinned owners stay fixed asks nothing the gate cannot give."""
    _, new, store = versions_with_pins(tmp_path)
    fresh = dataclasses.replace(UPDATE, fresh_edges_above_reopened_fraction=0.0)
    lines = {**WRITTEN, SETTING: []}
    fake = FakeHost(lambda p: "", edits={EDGES_FILE: edges_with(lines)})
    found = run(new.document, store, fresh, Runner(fake, Resolver(store), 10.0, FLAT))
    assert found.complete and found.plan.fresh
    give_on_new(
        new.document,
        store,
        found,
        fresh,
        Layer.ROLES,
        lambda t: t.replace("⟦b3 definition⟧", "⟦b3 assumption⟧"),
    )

    pending = run(new.document, store, fresh)
    (reask,) = [
        q for q in pending.questions[Stage.EDGES] if q.key in pending.unanswered
    ]
    assert "pins" not in json.loads(reask.workspace.gate.spec)
    fake = FakeHost(lambda p: "", edits={EDGES_FILE: done})
    answered = run(
        new.document, store, fresh, Runner(fake, Resolver(store), 10.0, FLAT)
    )

    assert answered.complete, answered.unanswered
    payload = update_payload(new.document, answered, store)
    states = {
        p["line"]: p["state"]
        for p in payload["review"]["person"]["pins"]
        if p["layer"] == "edges"
    }
    assert states[WROTE] == states[USES_SETTING] == "lapsed (written fresh)"


#: A person's two edges onto the proof's paragraphs, and one from the second
#: onto the first: a join of the two makes the first two one edge, and the
#: third an edge inside its own block.
ONTO_HEAD = '→ uses by-definition-def:gain-the · "Closing words of the results."'
ONTO_TAIL = '→ uses next-the-bound-is · "Closing words of the results."'
TAIL_ONTO_HEAD = '→ uses by-definition-def:gain-the · "Next, the bound is sharp."'


@pytest.mark.usefixtures("develop_mode")
def test_a_join_lapses_the_pins_it_makes_one_edge_or_an_edge_inside_a_block(
    tmp_path,
) -> None:
    b = build(tmp_path)
    give(
        b,
        Layer.EDGES,
        lambda t: t.replace(
            '→ about scope:paper · "Closing words of the results."',
            '→ about scope:paper · "Closing words of the results."\n'
            f"{ONTO_HEAD}\n{ONTO_TAIL}",
        ).replace(
            '⊢ unsupported · "Next, the bound is sharp."',
            f'{TAIL_ONTO_HEAD}\n⊢ unsupported · "Next, the bound is sharp."',
        ),
    )
    give(
        b,
        Layer.CUT,
        lambda t: re.sub(r"⟦b8 claim⟧ — [^\n]*\n", "", t),
        replay(b),
    )

    def fixed(text: str) -> str:
        out, seen = [], set()
        for line in text.split("\n"):
            if line == TAIL_ONTO_HEAD or (line == ONTO_HEAD and line in seen):
                continue
            seen.add(line)
            out.append(line)
        return done("\n".join(out))

    def roles(text: str) -> str:
        return text.replace("⟦b1⟧", "⟦b1 justification⟧", 1).replace(
            "Hence the output is bounded.", "⟦+ claim⟧\nHence the output is bounded."
        )

    fake = FakeHost(lambda p: "", edits={"roles.md": roles, EDGES_FILE: fixed})
    answered = replay(b, fake)
    assert answered.complete, answered.unanswered
    reask = answered.questions[Stage.EDGES][-1]
    held = [pin[1] for pin in json.loads(reask.workspace.gate.spec).get("pins", [])]
    assert held == [ONTO_HEAD]
    pins = [
        (p["kind"], p["line"], p["state"])
        for p in json.loads(exported(b, answered))["review"]["person"]["pins"]
        if p["layer"] == "edges"
    ]
    # the two onto the proof's paragraphs read as one line now: the first
    # holds, the second, carried into the same line, is reported lapsed
    assert sorted(pins) == sorted(
        [
            (
                "written",
                TAIL_ONTO_HEAD,
                "lapsed (structural: an edge inside its own block)",
            ),
            ("written", ONTO_HEAD, "held"),
            (
                "written",
                ONTO_TAIL,
                "lapsed (structural: a carry made it one line with another)",
            ),
        ]
    )


@pytest.mark.usefixtures("develop_mode")
def test_a_pinned_standing_whose_proof_is_deleted_lapses(tmp_path) -> None:
    """A person's `⊢ justified` on the theorem, its proof deleted in
    NEW: no justifies edge comes in, so the pin lapses rather than ask the
    writer for one."""
    proof = MAIN[MAIN.index("\\begin{proof}") : MAIN.index("Closing words")]
    justified = '⊢ justified · "The output is bounded." — the proof shows it'

    _, _, store = versions_with_pins(
        tmp_path,
        MAIN.replace(proof, ""),
        edit=lambda t: t.replace('⊢ justified · "The output is bounded."', justified),
    )

    (identity,) = [a for a in store.authoritative().values() if a.kind == "identity"]
    (pin,) = [
        p
        for p in versions.identity_raw(store, identity)["carried"]["pins"]
        if p["line"] == justified
    ]
    assert pin["state"] == ("lapsed (structural: no justifies edge comes in)")


@pytest.mark.usefixtures("develop_mode")
def test_a_person_s_pin_on_new_takes_the_place_of_the_carried_one(tmp_path) -> None:
    _, new, store = versions_with_pins(tmp_path)
    answer = answering(
        {SETTING: ["Δ reworded — a clause added"]},
        without_qualifies,
    )
    fake = FakeHost(lambda p: "", edits={EDGES_FILE: answer})
    found = run(new.document, store, UPDATE, Runner(fake, Resolver(store), 10.0, FLAT))
    assert found.complete, found.unanswered
    give_on_new(
        new.document,
        store,
        found,
        UPDATE,
        Layer.EDGES,
        lambda t: t.replace(WROTE + "\n", ""),
    )

    again = run(new.document, store, UPDATE)

    assert again.complete
    pins = update_payload(new.document, again, store)["review"]["person"]["pins"]
    assert [(p["kind"], p["state"]) for p in pins if p["line"] == WROTE] == [
        ("deleted", "held")
    ]


@pytest.mark.usefixtures("develop_mode")
def test_pins_of_two_answers_a_join_makes_one_edge_the_later_holds(tmp_path) -> None:
    """Two person answers each pin an edge from the closing words, onto
    each of the proof's paragraphs; a person's join of the paragraphs makes
    them one edge: the later answer's holds, the earlier lapses."""
    later = '→ uses next-the-bound-is · "Closing words"'
    b = build(tmp_path)
    scope = '→ about scope:paper · "Closing words of the results."'
    give(b, Layer.EDGES, lambda t: t.replace(scope, f"{scope}\n{ONTO_HEAD}"))
    give(b, Layer.EDGES, lambda t: t.replace(scope, f"{scope}\n{later}"))
    give(b, Layer.CUT, lambda t: re.sub(r"⟦b8 claim⟧ — [^\n]*\n", "", t), replay(b))

    def roles(text: str) -> str:
        return text.replace("⟦b1⟧", "⟦b1 justification⟧", 1).replace(
            "Hence the output is bounded.", "⟦+ claim⟧\nHence the output is bounded."
        )

    joined = later.replace("next-the-bound-is", PROOF_BLOCK)

    def reask(text: str) -> str:
        return done(text.replace(ONTO_HEAD + "\n", ""))

    fake = FakeHost(lambda p: "", edits={"roles.md": roles, EDGES_FILE: reask})
    answered = replay(b, fake)

    assert answered.complete, answered.unanswered
    spec = json.loads(answered.questions[Stage.EDGES][-1].workspace.gate.spec)
    assert [pin[1] for pin in spec["pins"]] == [joined]
    states = {
        p["line"]: p["state"]
        for p in json.loads(exported(b, answered))["review"]["person"]["pins"]
        if p["layer"] == "edges"
    }
    assert states == {
        ONTO_HEAD: "lapsed (structural: a second edge for one source, kind and target)",
        joined: "held",
    }


@pytest.mark.usefixtures("develop_mode")
def test_a_pinned_standing_whose_edge_a_later_round_dropped_lapses(tmp_path) -> None:
    """A person's `⊢ justified` on the theorem, resting on the proof's
    justifies edge; a person makes the theorem a motivation (the pin lapses,
    the re-ask drops the edge and the standing), then a claim again: on the
    sheet then shown no justifies edge comes in, so the pin stays lapsed and
    the writer gives the claim another standing. The closing paragraph,
    restating the theorem, is reopened with it: it restates no claim while
    the theorem is a motivation."""
    standing = '⊢ justified · "The output is bounded."'
    pinned = f"{standing} — by its proof"
    justifies = '→ justifies thm:bound name · "Hence the output is bounded."'
    restated = '⊢ restated · "A closing paragraph in another file."'

    def reask(text: str) -> str:
        if "⟦thm:bound motivation⟧" in text:
            assert restated in text
            text = text.replace(standing + "\n", "").replace(pinned + "\n", "")
            text = text.replace(restated, restated.replace("restated", "unsupported"))
            return done(text.replace(justifies + "\n", ""))
        if "⟦thm:bound claim⟧" in text and '⊢ unsupported · "The output' not in text:
            text = add_lines(
                text, {"thm:bound": ['⊢ unsupported · "The output is bounded."']}
            )
        return done(text)

    b = build(tmp_path)
    give(b, Layer.EDGES, lambda t: t.replace(standing, pinned))
    give(
        b,
        Layer.ROLES,
        lambda t: t.replace("⟦b5 claim⟧", "⟦b5 motivation⟧"),
        replay(b),
    )
    give(
        b,
        Layer.ROLES,
        lambda t: t.replace("⟦b5 motivation⟧", "⟦b5 claim⟧"),
        replay(b, host(reask=reask)),
    )

    answered = replay(b, host(reask=reask))

    assert answered.complete, answered.unanswered
    spec = json.loads(answered.questions[Stage.EDGES][-1].workspace.gate.spec)
    assert pinned not in [pin[1] for pin in spec.get("pins", [])]
    (pin,) = [
        p
        for p in json.loads(exported(b, answered))["review"]["person"]["pins"]
        if p["line"] == pinned
    ]
    assert pin["state"].startswith("lapsed (structural: ")


@pytest.mark.usefixtures("develop_mode")
def test_a_pinned_edge_onto_a_part_a_maths_edit_removed_lapses(tmp_path) -> None:
    """The definition's formula is a part; a person pins the proof's
    edge onto it; NEW changes the formula past re-quoting. The pin lapses
    (its part is not verbatim), so the update session may delete the part
    and write `Δ [eq] removed`."""
    formula = "The gain is the ratio $g = o / i$ of output to input."
    old_main = MAIN.replace("The gain is the ratio of output to input.", formula)
    new_main = old_main.replace("$g = o / i$", "$g$ = $o / i$")
    edge = '→ uses def:gain [eq] · "Closing words of the results."'
    legend = [
        '  def:gain [gain]: "The gain is the ratio"',
        '  def:gain [eq]: "$g = o / i$"',
    ]

    def pin_it(text: str) -> str:
        scope = '→ about scope:paper · "Closing words of the results."'
        return text.replace(scope, f"{scope}\n{edge}")

    _, new, store = versions_with_pins(
        tmp_path, new_main, edit=pin_it, old_main=old_main, header=legend
    )
    (identity,) = [a for a in store.authoritative().values() if a.kind == "identity"]
    (pin,) = [
        p
        for p in versions.identity_raw(store, identity)["carried"]["pins"]
        if p["line"] == edge
    ]
    assert pin["state"].startswith("lapsed (structural: "), pin["state"]
    assert "not verbatim" in pin["state"]

    _, made = plan(new.document, store, UPDATE)
    assert "pins" not in json.loads(made.question.workspace.gate.spec)
    answer = answering(
        {
            DEFINITION: [
                'Δ [gain] unchanged — "The gain is the ratio": its words stand',
                "Δ [eq] removed — the formula reads otherwise",
            ],
            CLOSING: [
                'Δ unchanged — "Closing words of the results": it closes as before'
            ],
        },
        lambda t: t.replace('  def:gain [eq]: "$g = o / i$"\n', "").replace(
            edge + "\n", ""
        ),
    )
    sheet = made.question.workspace.files[EDGES_FILE]
    assert check(made.question, answer(sheet), new.document).valid


@pytest.mark.usefixtures("develop_mode")
def test_an_answer_that_breaks_a_held_pin_is_refused_naming_it(tmp_path) -> None:
    """The writer's answer never lapses a pin: with the proof group open,
    dropping the justifies edge a pinned `⊢ justified` rests on is refused,
    and the refusal names the pin."""
    standing = '⊢ justified · "The output is bounded."'
    pinned = f"{standing} — by its proof"
    justifies = '→ justifies thm:bound name · "Hence the output is bounded."'
    b = build(tmp_path)
    give(b, Layer.EDGES, lambda t: t.replace(standing, pinned))
    end = "⟦end group by_definition_the_gain⟧"
    sharp = "⟦b8 claim⟧\nNext, the bound is sharp.\n\\end{proof}"
    give(b, Layer.GROUPS, lambda t: t.replace(f"{sharp}\n\n{end}", f"{end}\n\n{sharp}"))

    question = reask_of(replay(b))

    assert ["thm:bound", pinned, "written"] in json.loads(question.workspace.gate.spec)[
        "pins"
    ]
    sheet = question.workspace.files[EDGES_FILE]
    assert check(question, done(sheet), b.paper.document).valid
    found = check(question, done(sheet.replace(justifies + "\n", "")), b.paper.document)
    assert any(
        p.startswith(f"thm:bound: this breaks the person's pinned line `{pinned}`")
        for p in found.problems
    ), found.problems


# --- lines the sheet refuses, and pins beside a model's lines -----------------------


def with_legend(*lines: str) -> Callable[[str], str]:
    def edit(text: str) -> str:
        return text.replace("Files: ", "\n".join(lines) + "\nFiles: ", 1)

    return edit


@pytest.mark.usefixtures("develop_mode")
@pytest.mark.parametrize(
    ("edit", "owner", "line", "why"),
    [
        (
            lambda t: t,
            CLOSING,
            '→ self ref · "Closing words of the results."',
            "a self mark has no target, part or mark",
        ),
        (
            lambda t: t,
            CLOSING,
            '→ uses thm:bound where-used · "Closing words of the results."',
            "where-used marks an about edge only",
        ),
        (
            lambda t: t,
            PROOF,
            '→ uses thm:bound · "Hence the output is bounded."',
            "a group is a source for justifies only",
        ),
        (
            with_legend('  thm:bound [b]: "The output is bounded." (bogus)'),
            CLOSING,
            '→ uses thm:bound [b] · "Closing words of the results."',
            "no such role",
        ),
        (
            with_legend('  thm:bound [b]: "The output is bounded." (standard)'),
            CLOSING,
            '→ uses thm:bound [b] · "Closing words of the results."',
            "`(standard)` is not a writer's role",
        ),
        (
            with_legend(f'  {PROOF} [g]: "Hence the output is bounded."'),
            CLOSING,
            f'→ about {PROOF} [g] · "Closing words of the results."',
            "a part is a block's, not a group's",
        ),
        (
            lambda t: t,
            CLOSING,
            '→ uses thm:bound optional · "Closing words of the results."',
            "`optional` is not a writer's flag",
        ),
        (
            lambda t: t,
            "thm:bound",
            '⊢ self · "The output is bounded."',
            "no self mark on the block",
        ),
        (
            lambda t: t,
            "thm:bound",
            '⊢ restated · "The output is bounded."',
            "no about edge goes out",
        ),
    ],
)
def test_every_structural_check_a_pin_takes_part_in_lapses_it(
    tmp_path, edit, owner, line, why
) -> None:
    b = build(tmp_path)
    review = replay(b)
    sheet = edit(record_file.text(b.store.directory, review.edges_answer))
    spec = edges_structure(b.paper.document, review.corrected.cut, FILES)
    pin = person.Pin(Layer.EDGES, owner, person.PinKind.WRITTEN, line)

    (found,) = person.held_against([pin], sheet, None, spec)

    assert found.state.startswith("lapsed (structural: "), found.state
    assert why in found.state


@pytest.mark.usefixtures("develop_mode")
def test_a_model_line_s_problem_on_a_pin_s_key_leaves_the_pin_held(tmp_path) -> None:
    """A model's line with the pin's owner, kind and target but other
    words fails its evidence check and duplicates the edge: the pin is not
    the line the evidence check names, and one pin beside a copy holds."""
    b = build(tmp_path)
    review = replay(b)
    model = '→ uses thm:bound · "Not there."'
    scope = '→ about scope:paper · "Closing words of the results."'
    sheet = record_file.text(b.store.directory, review.edges_answer).replace(
        scope, f"{scope}\n{model}"
    )
    spec = edges_structure(b.paper.document, review.corrected.cut, FILES)
    pin = person.Pin(Layer.EDGES, CLOSING, person.PinKind.WRITTEN, WROTE)

    (found,) = person.held_against([pin], sheet, None, spec)

    assert found.held, found.state


@pytest.mark.usefixtures("develop_mode")
def test_a_writer_may_not_change_what_a_pinned_line_means(tmp_path) -> None:
    """With the owners open, an answer that rewrites the legend line of
    a part a pinned edge names, or gives a pinned standing's claim a second
    standing, is refused, naming the pin."""
    b = build(tmp_path)
    pinned = '→ uses def:gain [gain] · "Closing words of the results."'
    scope = '→ about scope:paper · "Closing words of the results."'
    give(b, Layer.EDGES, lambda t: t.replace(scope, f"{scope}\n{pinned}"))
    give(
        b,
        Layer.ROLES,
        lambda t: t.replace("⟦b9 none⟧", "⟦b9 motivation⟧").replace(
            "⟦b3 definition⟧", "⟦b3 assumption⟧"
        ),
    )
    question = reask_of(replay(b))
    sheet = question.workspace.files[EDGES_FILE]
    assert check(question, done(sheet), b.paper.document).valid

    words = '  def:gain [gain]: "The gain is the ratio of output to input."'
    reworded = done(sheet.replace(words, '  def:gain [gain]: "The gain is the ratio"'))
    found = check(question, reworded, b.paper.document)
    assert any("keep its legend line as shown" in p for p in found.problems)


@pytest.mark.usefixtures("develop_mode")
def test_session_2_holds_the_carried_pins(tmp_path) -> None:
    from test_update import (
        CHAIN_LEGEND,
        CITED,
        CLOSING_PARAGRAPH,
        NARROWED_CLOSING,
        by_session,
    )

    line = '→ uses thm:bound · "A closing paragraph"'
    _, new, store = versions_with_pins(
        tmp_path,
        edit=lambda t: t.replace(
            '⊢ restated · "A closing paragraph in another file."',
            f'{line}\n⊢ restated · "A closing paragraph in another file."',
        ),
        lines=CITED,
        header=CHAIN_LEGEND,
    )
    fake = FakeHost(
        lambda p: "",
        edits={EDGES_FILE: by_session(answering(NARROWED_CLOSING, without_qualifies))},
    )

    found = run(new.document, store, UPDATE, Runner(fake, Resolver(store), 10.0, FLAT))

    assert found.complete and found.second_question is not None
    spec = json.loads(found.second_question.workspace.gate.spec)
    assert [CLOSING_PARAGRAPH, line, "written"] in spec["pins"]
    assert (
        f"⟦done {CLOSING_PARAGRAPH}⟧"
        not in found.second_question.workspace.files[EDGES_FILE]
    )


@pytest.mark.usefixtures("develop_mode")
def test_a_round_s_reask_holds_the_carried_pins(tmp_path) -> None:
    """After a correction a round applied, its re-ask holds the pins OLD
    carried: the definition's pinned line stands; the closing words' pin,
    whose evidence the split cut through, lapses before the session."""
    old, new, store = versions_with_pins(tmp_path)
    split = '✂ split "of the results" · "Closing words of the results." — two parts'
    first = answering(
        {
            SETTING: ["Δ reworded — a clause added"],
            CLOSING: [split],
        },
        without_qualifies,
    )

    def reask(text: str) -> str:
        return done(text.replace('"Closing words of the results."', '"Closing words"'))

    settings = dataclasses.replace(
        UPDATE,
        review=ReviewSettings(stages=(Stage.EDGES,), cut_rounds=1, files=FILES),
    )
    fake = FakeHost(
        lambda p: "",
        edits={
            EDGES_FILE: lambda t: reask(t) if es_cut_round(t) else first(t),
            "roles.md": roles_answer,
        },
    )

    found = run(
        new.document, store, settings, Runner(fake, Resolver(store), 10.0, FLAT)
    )

    assert found.complete, found.unanswered
    assert found.cut_rounds
    spec = json.loads(found.questions[Stage.EDGES][-1].workspace.gate.spec)
    assert [DEFINITION, USES_SETTING, "written"] in spec["pins"]
    assert WROTE not in [pin[1] for pin in spec["pins"]]
    # replayed from the store alone, OLD's directory gone: pins, the round
    payload = dumps(update_payload(new.document, found, store))
    shutil.rmtree(old.root)
    replayed = run(new.document, store, settings)
    assert dumps(update_payload(new.document, replayed, store)) == payload


def es_cut_round(text: str) -> bool:
    """Whether an update's edges sheet is a round's re-ask: the closing
    words already split."""
    return "⟦of-the-results" in text or "of-the-results " in text


@pytest.mark.usefixtures("develop_mode")
def test_a_person_s_cut_edit_on_new_keeps_the_handle_the_export_names(
    tmp_path,
) -> None:
    """A person's split of the closing words on NEW keeps their handle on
    the first piece, which keeps their node (`docs/model.md` § 7);
    `review.update.reopened` names them as the export's handles do, as the
    scorer resolves them."""
    from test_update import versions

    v = versions(tmp_path, EDITED_SETTING)
    first = answering({SETTING: ["Δ reworded — a clause added"]}, without_qualifies)
    fake = FakeHost(lambda p: "", edits={EDGES_FILE: first})
    found = run(
        v.new.document, v.store, UPDATE, Runner(fake, Resolver(v.store), 10.0, FLAT)
    )
    assert found.complete
    give_on_new(
        v.new.document,
        v.store,
        found,
        UPDATE,
        Layer.CUT,
        lambda t: t.replace(
            "Closing words of the results.",
            "Closing words\n⟦new — two parts⟧\nof the results.",
        ),
    )

    def reask(text: str) -> str:
        return done(text.replace('"Closing words of the results."', '"Closing words"'))

    fake = FakeHost(lambda p: "", edits={EDGES_FILE: reask, "roles.md": roles_answer})
    again = run(
        v.new.document, v.store, UPDATE, Runner(fake, Resolver(v.store), 10.0, FLAT)
    )

    assert again.complete, again.unanswered
    payload = update_payload(v.new.document, again, v.store)
    handles = set(payload["handles"].values())
    data = payload["review"]["update"]
    block_of = {h: ident for ident, h in payload["handles"].items()}
    # the first piece keeps the node and its handle; the second is minted
    assert block_of[CLOSING] == v.new.id_at("Closing words")
    assert block_of["of-the-results"] == v.new.id_at("of the results.")
    assert "closing-words" not in handles
    named = [r["handle"] for r in data["reopened"]]
    assert CLOSING in named
    assert all(
        r["handle"] in handles and (r["from"] is None or r["from"] in handles)
        for r in data["reopened"] + data["not_asked"]
    )


@pytest.mark.usefixtures("develop_mode")
def test_a_chained_update_carries_session_1_s_sheet_and_the_pins(tmp_path) -> None:
    """The first version with a person's pins, the second its
    update (session 1's raw holding Δ, ⟦was⟧ and condition lines), the third from
    the second's export: the sheet carried from session 1's raw, the pins with it."""
    import shutil

    old, v2, store = versions_with_pins(tmp_path)
    narrowed = answering(
        {
            SETTING: ["Δ narrowed — the setting now says less"],
            DEFINITION: ['Δ unchanged — "The gain is the ratio": it reads the same'],
        },
        without_qualifies,
    )
    fake = FakeHost(lambda p: "", edits={EDGES_FILE: narrowed})
    second = run(v2.document, store, UPDATE, Runner(fake, Resolver(store), 10.0, FLAT))
    assert second.complete
    raw = record_file.text(store.directory, second.answer)
    assert "Δ narrowed" in raw and "⟦was⟧" in raw
    assert "⟦asked if" in raw or "⟦meaning moved by rule" in raw
    (tmp_path / "v2.json").write_text(
        dumps(update_payload(v2.document, second, store)), encoding="utf-8"
    )
    shutil.rmtree(old.root)  # the first version's directory is never read again
    v3 = make_paper(
        tmp_path / "v3",
        main=EDITED_SETTING.replace(
            "The output is bounded.", "The output stays bounded."
        ),
    )
    args = ["diff", str(v2.root), str(v3.root), "--displays", "in-paragraph"]
    args += ["--cut", str(tmp_path / "v2.json"), "--store", str(store.directory)]
    assert main(args) == 0
    (identity,) = [
        a
        for a in store.authoritative().values()
        if a.kind == "identity"
        and versions.identity_raw(store, a)["new"] == document_hash(v3.document)
    ]
    carried = versions.identity_raw(store, identity)["carried"]
    assert "Δ " not in carried["sheet"] and "⟦was⟧" not in carried["sheet"]
    assert "⟦asked if" not in carried["sheet"]
    assert "⟦meaning moved by rule" not in carried["sheet"]
    held = {p["line"]: p["state"] for p in carried["pins"] if p["layer"] == "edges"}
    assert held[WROTE] == "held"

    stays = answering(
        {"thm:bound": ["Δ reworded — stays for is"]},
        lambda t: t.replace('"The output is bounded."', '"The output stays bounded."'),
    )
    fake = FakeHost(lambda p: "", edits={EDGES_FILE: stays})
    third = run(v3.document, store, UPDATE, Runner(fake, Resolver(store), 10.0, FLAT))
    assert third.complete, third.unanswered
    pins = update_payload(v3.document, third, store)["review"]["person"]["pins"]
    assert {p["line"]: p["state"] for p in pins}[WROTE] == "held"
    replayed = run(v3.document, store, UPDATE)
    assert dumps(update_payload(v3.document, replayed, store)) == dumps(
        update_payload(v3.document, third, store)
    )


# --- the edges' role correction ------------------------------------------

CLOSING_CLAIM = '⊢ unsupported · "Closing words of the results."'


@pytest.mark.usefixtures("develop_mode")
def test_the_roles_file_a_person_is_shown_names_a_claim_the_edges_added(
    tmp_path,
) -> None:
    """The closing paragraph, which the roles leave without a role, takes a
    standing: its claim role is the edges', and a person's roles file says
    so by its marker. A person's roles answer on that file is applied, its
    pin holds the block's roles, and the re-ask's gate refuses the standing
    there now, so the writer takes it off."""
    lines = {**WRITTEN, CLOSING: [*WRITTEN[CLOSING], CLOSING_CLAIM]}
    b = build(tmp_path, lines)
    review = replay(b)
    cut = review.corrected.cut
    closing = b.paper.id_at("Closing words")
    assert cut.block(closing).roles == (Role.CLAIM,)
    notes = claim_notes(review.proposals, cut)
    assert notes == {closing: CLAIM_ADDED_NOTE}

    shown = person.stage_question(
        b.paper.document, cut, Layer.ROLES, SETTINGS, None, notes
    )
    text = person.shown_file(shown)
    (handle,) = [h for h, i in shown.handles.items() if i == closing]
    marker = f"⟦{handle} claim — claim added by edges⟧"
    assert marker in text
    plain = person.stage_question(b.paper.document, cut, Layer.ROLES, SETTINGS)
    assert marker not in person.shown_file(plain)

    edited = text.replace(marker, f"⟦{handle} motivation⟧")
    question = person.person_question(
        b.paper.document, cut, Layer.ROLES, SETTINGS, None, edited, notes
    )
    found = person.checked(question, edited, checker(b.paper.document))
    assert found.valid, found.problems
    given = (
        Resolver(b.store)
        .record(
            Attempt(
                question,
                PERSON,
                PERSON,
                edited,
                0.0,
                0.0,
                found,
                version=document_hash(b.paper.document),
            )
        )
        .answer
    )

    def without_the_standing(text: str) -> str:
        return done(text.replace(CLOSING_CLAIM + "\n", ""))

    again = replay(b, host(lines, reask=without_the_standing))

    assert again.complete, again.unanswered
    assert [r.key for r in again.person.applied] == [given.key]
    assert again.corrected.cut.block(closing).roles == (Role.MOTIVATION,)
    reask = again.questions[Stage.EDGES][-1]
    assert json.loads(reask.workspace.gate.spec)["claims_fixed"] == [CLOSING]
    refused = check(
        reask, done(reask.workspace.files[EDGES_FILE]), b.paper.document
    ).problems
    assert any("a person gave its roles" in p for p in refused), refused
    assert closing not in {s.block for s in again.edges.standings}


def with_closing_claim(text: str) -> str:
    """A person's edges: a standing on the closing paragraph, whose roles
    give no claim."""
    scope = '→ about scope:paper · "Closing words of the results."'
    return text.replace(scope, f"{scope}\n{CLOSING_CLAIM}")


@pytest.mark.usefixtures("develop_mode")
def test_a_person_s_standing_on_a_block_with_no_claim_adds_it_by_the_person(
    tmp_path,
) -> None:
    b = build(tmp_path)
    closing = b.paper.id_at("Closing words")
    assert replay(b).corrected.cut.block(closing).roles == ()
    given = give(b, Layer.EDGES, with_closing_claim)

    fake = host()
    review = replay(b, fake)

    assert fake.calls == 0 and review.complete, review.unanswered
    assert review.corrected.cut.block(closing).roles == (Role.CLAIM,)
    (proposal,) = review.proposals
    assert (proposal.by, proposal.applied) == (PERSON, True)
    assert proposal.correction.answer == given.key
    assert claim_notes(review.proposals, review.corrected.cut) == {}
    entry = json.loads(exported(b, review))["review"]["cut_corrections"][0]
    assert (entry["op"], entry["by"]) == ("roles", "person")


@pytest.mark.usefixtures("develop_mode")
def test_a_person_s_standing_does_not_override_a_person_s_roles_pin(
    tmp_path,
) -> None:
    """A person's roles answer first (the closing paragraph a motivation),
    then a person's edges answer with a standing there: the roles pin
    holds, the correction is listed, not applied."""
    b = build(tmp_path)
    closing = b.paper.id_at("Closing words")

    def motivation(text: str) -> str:
        (handle,) = re.findall(r"^⟦(b\d+) none⟧\nClosing words", text, re.M)
        return text.replace(
            f"⟦{handle} none⟧\nClosing", f"⟦{handle} motivation⟧\nClosing"
        )

    give(b, Layer.ROLES, motivation)
    pinned = replay(b, host(reask=done))
    assert pinned.complete, pinned.unanswered
    give(b, Layer.EDGES, with_closing_claim, pinned)

    review = replay(b, host(reask=done))

    assert review.complete, review.unanswered
    assert review.corrected.cut.block(closing).roles == (Role.MOTIVATION,)
    (held,) = [
        p
        for p in review.proposals
        if p.correction.op is CutOp.ROLES and p.stage == Stage.EDGES.value
    ]
    assert held.by == PERSON and not held.applied
    assert held.problem.startswith(PINNED)
