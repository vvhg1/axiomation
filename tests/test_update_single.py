"""An update of one session: below the cutover the
update asks one session on the carried sheet, as a queue of the blocks the
edit touched, the re-cut blocks' roles open, a Δ line owed per changed
block; what its Δs reach is
marked at $0, moved by rule or stale, from the carried edges. On the chain
of `test_update`: the setting edited and re-cut, the definition qualified by
it and using its part [K], the proof using the definition, the sharpness
using the proof. No model is called.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

from axiomation.cutter.model import Role
from axiomation.exchange import dumps
from axiomation.questions import edge_sheet as es
from axiomation.questions.edges_file import (
    EDGES_FILE,
    PLAIN,
    ROLES_WRITTEN_WHY,
    check,
    plain_cut_applied,
)
from axiomation.records import CutOp
from axiomation.update import (
    MarkContext,
    UpdateSettings,
    dependency_marks,
    mark_context,
    marks_from,
    meaning_notes,
    plan,
    queue_shown,
    update_payload,
)
from axiomation.update_queue import KICKOFF, TEMPLATE
from questions.review_paper import MAIN
from test_update import (
    CHAIN,
    CHAIN_LEGEND,
    CLOSING,
    DEFINITION,
    EDITED_SETTING,
    FILES,
    PROOF,
    PROOF_BLOCK,
    SETTING,
    SHARP,
    answering,
    host,
    refused,
    shown,
    update,
    versions,
)

PLAIN_SETTINGS = UpdateSettings(
    files=FILES, limit=None, per_stage_update_above_recut_blocks=1000
)
#: Every block the session's Δs move carried wholly, unread: a cap of 0 Δ
#: lines on moved blocks, so the marks are the rule's alone.
CARRIED_SETTINGS = dataclasses.replace(PLAIN_SETTINGS, moved_delta_lines_max=0)
#: The setting narrowed, and the definition it qualifies, whose meaning
#: that moves by rule, read and closed with its evidence quoted.
NARROWED_CLOSED = {
    SETTING: ["Δ narrowed — scalars of a subfield only"],
    DEFINITION: ['Δ unchanged — "The gain is the ratio": over the same field'],
}
THEOREM = "thm:bound"


def as_assumption(text: str) -> str:
    """The session's roles on the re-cut setting: an assumption."""
    return text.replace(f"⟦{SETTING} none⟧", f"⟦{SETTING} assumption⟧")


def chain(tmp_path: Path, settings: UpdateSettings = PLAIN_SETTINGS):
    v = versions(tmp_path, EDITED_SETTING, CHAIN, CHAIN_LEGEND)
    return v, plan(v.new.document, v.store, settings)[1]


def review_update(v, made) -> dict:
    return update_payload(v.new.document, made, v.store)["review"]["update"]


def marked(entries: list[dict]) -> dict[str, list[tuple[str, str | None]]]:
    return {e["handle"]: [(c["from"], c["via"]) for c in e["causes"]] for e in entries}


# --- the shape ---------------------------------------------------------------------


def test_the_shape_follows_the_count_of_recut_blocks(tmp_path) -> None:
    v, made = chain(tmp_path)
    question = made.question
    spec = json.loads(question.workspace.gate.spec)

    assert made.single_session and made.roots == (SETTING,)
    # asked as a queue: its brief the queue's, no file beside the sheet
    assert (question.template.name, question.prompt) == (TEMPLATE, KICKOFF)
    assert set(question.workspace.files) == {EDGES_FILE}
    setting = made.handles[SETTING]
    plain = spec[PLAIN]
    assert (plain["owed"], plain["roles_open"], plain["gone"]) == (
        [setting],
        [setting],
        [],
    )
    # no paper given: the gate applies no cut line
    assert (plain["paper"], plain["cut"]) == (None, None)
    assert "update" not in spec
    # the carried sheet as it is: no ⟦was⟧ line, no condition, the changed
    # block without its done mark
    sheet = shown(question)
    assert "⟦was⟧" not in sheet and "⟦asked if" not in sheet
    assert f"⟦done {SETTING}⟧" not in sheet and f"⟦done {DEFINITION}⟧" in sheet

    # one re-cut block, past a cutover of 0: per stage, as is every update
    # with the key unset
    for limit in (0, None):
        settings = UpdateSettings(
            files=FILES, limit=None, per_stage_update_above_recut_blocks=limit
        )
        _, made = plan(v.new.document, v.store, settings)
        assert not made.single_session
        assert "update" in json.loads(made.question.workspace.gate.spec)


def test_a_block_about_the_whole_paper_is_read_again_in_the_session(
    tmp_path,
) -> None:
    """Its done mark off and a task to read it again: the gate holds the
    session until it is marked done again."""
    v, made = chain(tmp_path)
    question, sheet, document = made.question, shown(made.question), v.new.document

    assert f"⟦done {CLOSING}⟧" not in sheet
    assert CLOSING in queue_shown(made, question).reread
    good = answering(NARROWED_CLOSED, as_assumption)(sheet)
    assert check(question, good, document).valid
    unread = good.replace(f"⟦done {CLOSING}⟧\n", "", 1)
    assert refused(question, unread, document)


def test_a_changed_block_about_the_whole_paper_owes_its_delta_not_a_re_read(
    tmp_path,
) -> None:
    """Changed itself, it is the session's to write, Δ and all: never in
    the list of blocks only to read again."""
    edited = EDITED_SETTING.replace(
        "Closing words of the results.", "Closing words of the results, edited."
    )
    v = versions(tmp_path, edited, CHAIN, CHAIN_LEGEND)
    _, made = plan(v.new.document, v.store, PLAIN_SETTINGS)

    plain = json.loads(made.question.workspace.gate.spec)[PLAIN]
    assert made.handles[CLOSING] in plain["owed"]
    assert CLOSING not in queue_shown(made, made.question).reread


def test_marks_walked_from_the_filed_context_are_the_session_s(tmp_path) -> None:
    """The marks' context, read from the session's answer once and kept as
    data, walks to the marks the answer gives; a `scope:paper` source the
    session was shown open is not marked."""
    v, _ = chain(tmp_path)
    done = update(v, host(answering(NARROWED_CLOSED, as_assumption)), PLAIN_SETTINGS)
    raw = done.answer.raw

    read = dependency_marks(done.plan, raw, v.new.document)
    context = mark_context(done.plan, raw, v.new.document)
    kept = MarkContext.of(json.loads(json.dumps(context.data())))

    assert CLOSING not in read.stale
    assert kept == context
    assert marks_from(kept) == read


# --- the gate ----------------------------------------------------------------------


def test_the_gate_leaves_the_recut_roles_open_and_holds_the_rest(tmp_path) -> None:
    v, made = chain(tmp_path)
    question, sheet, document = made.question, shown(made.question), v.new.document
    narrowed = NARROWED_CLOSED

    good = answering(narrowed, as_assumption)(sheet)
    found = check(question, good, document)
    assert found.valid, found.problems
    (roles,) = [c for c in found.corrections if c.op is CutOp.ROLES]
    assert (roles.block, roles.roles, roles.why) == (
        made.handles[SETTING],
        (Role.ASSUMPTION,),
        ROLES_WRITTEN_WHY,
    )

    # a fixed block's roles, and the re-cut block's words, are fixed
    fixed_role = answering(narrowed, as_assumption)(
        sheet.replace(f"⟦{DEFINITION} definition⟧", f"⟦{DEFINITION} claim⟧")
    )
    assert any(
        "the block and group lines are not as shown" in p
        for p in refused(question, fixed_role, document)
    )
    reworded_text = good.replace(
        "A second sentence of the setting, edited.", "A second sentence, edited."
    )
    assert any(
        "the block and group lines are not as shown" in p
        for p in refused(question, reworded_text, document)
    )
    # every other owner's lines are the session's
    elsewhere = good.replace('"Closing words of the results."', '"Closing words"')
    assert check(question, elsewhere, document).valid

    # one Δ line on each changed block, and none on another
    assert any(
        p.startswith(f"no Δ line ({SETTING})")
        for p in refused(question, answering({}, as_assumption)(sheet), document)
    )
    beyond = answering(
        {**narrowed, SHARP: ['Δ unchanged — "the bound is sharp": the same']},
        as_assumption,
    )(sheet)
    assert any(
        f"and {SHARP} is neither" in p for p in refused(question, beyond, document)
    )


def test_a_role_change_inside_a_recut_block_is_placed(tmp_path) -> None:
    v, made = chain(tmp_path)
    question, sheet, document = made.question, shown(made.question), v.new.document
    narrowed = NARROWED_CLOSED

    def two_parts(text: str) -> str:
        return text.replace(
            f"⟦{SETTING} none⟧", f"⟦{SETTING} assumption+motivation⟧"
        ).replace(
            "A second sentence of the setting, edited.",
            "⟦+ motivation⟧ A second sentence of the setting, edited.",
        )

    found = check(question, answering(narrowed, two_parts)(sheet), document)
    assert found.valid, found.problems
    (roles,) = [c for c in found.corrections if c.op is CutOp.ROLES]
    assert roles.roles == (Role.ASSUMPTION, Role.MOTIVATION)
    assert [(s.starts, s.role) for s in roles.segments] == [
        ("", Role.ASSUMPTION),
        ("A second sentence", Role.MOTIVATION),
    ]

    misnamed = answering(narrowed, two_parts)(sheet).replace(
        "assumption+motivation", "assumption"
    )
    assert any(
        "`assumption+motivation`" in p for p in refused(question, misnamed, document)
    )
    unknown = answering(
        narrowed,
        lambda t: t.replace("⟦we-fix-a-field none⟧", "⟦we-fix-a-field premise⟧"),
    )(sheet)
    assert any(
        "'premise' is not a role" in p for p in refused(question, unknown, document)
    )


def test_a_standing_on_a_recut_block_needs_the_claim_role_written(tmp_path) -> None:
    v, made = chain(tmp_path)
    question, sheet, document = made.question, shown(made.question), v.new.document
    standing = {
        SETTING: [
            '⊢ unsupported · "We fix a field of scalars."',
            "Δ narrowed — scalars of a subfield only",
        ],
        DEFINITION: NARROWED_CLOSED[DEFINITION],
    }

    unclaimed = answering(standing, as_assumption)(sheet)
    assert any(
        f"{SETTING} ⊢: a standing on a re-cut block" in p
        for p in refused(question, unclaimed, document)
    )
    claimed = answering(
        standing,
        lambda t: t.replace(
            f"⟦{SETTING} none⟧", f"⟦{SETTING} assumption+claim⟧"
        ).replace(
            "A second sentence of the setting, edited.",
            "⟦+ claim⟧ A second sentence of the setting, edited.",
        ),
    )(sheet)
    found = check(question, claimed, document)
    assert found.valid, found.problems
    # the roles written, not a claim added by the standing
    assert [c.why for c in found.corrections if c.op is CutOp.ROLES] == [
        ROLES_WRITTEN_WHY
    ]


# --- what the Δs reach: moved by rule, stale ---------------------------


def test_a_narrowed_assumption_moves_its_users_and_marks_the_claims_beyond_stale(
    tmp_path,
) -> None:
    """Carried wholly, as past the cap: what the rule moves is marked moved,
    what it asks stale."""
    v, _ = chain(tmp_path)
    narrowed = {SETTING: ["Δ narrowed — scalars of a subfield only"]}

    made = update(v, host(answering(narrowed, as_assumption)), CARRIED_SETTINGS)

    assert made.complete and made.follow is None
    found = review_update(v, made)
    assert found["shape"] == "single-session"
    assert marked(found["moved_by_rule"]) == {
        DEFINITION: [
            (SETTING, f"{SETTING} → qualifies {DEFINITION}"),
            (SETTING, f"{DEFINITION} → uses {SETTING} [K]"),
        ],
        PROOF_BLOCK: [(DEFINITION, f"{PROOF_BLOCK} → uses {DEFINITION} [gain]")],
    }
    # the block about the whole paper was read again in the session: not
    # stale, but reopened by it
    assert marked(found["stale"]) == {
        THEOREM: [(PROOF_BLOCK, f"{PROOF} → justifies {THEOREM}")],
        SHARP: [(PROOF_BLOCK, f"{SHARP} → uses {PROOF_BLOCK}")],
    }
    assert {r["handle"]: r["why"] for r in found["reopened"]}[CLOSING] == (
        "its scope:paper edge: the paper changed"
    )
    assert all(s["cleared_by"] is None for s in found["stale"])
    # the session's roles are the export's
    exported = update_payload(v.new.document, made, v.store)
    (setting,) = [
        b for b in exported["blocks"] if b["id"] == made.plan.handles[SETTING]
    ]
    assert setting["roles"] == ["assumption"]


def test_reworded_and_unchanged_mark_nothing(tmp_path) -> None:
    for cls in ("reworded", "unchanged"):
        v, _ = chain(tmp_path / cls)
        made = update(
            v,
            host(
                answering(
                    {
                        SETTING: [
                            f'Δ {cls} — "We fix a field of scalars": a clause added'
                        ]
                    },
                    as_assumption,
                )
            ),
            PLAIN_SETTINGS,
        )
        found = review_update(v, made)
        assert (found["moved_by_rule"], found["stale"]) == ([], []), cls


def test_a_retracted_qualifies_edge_moves_its_target_whatever_the_delta(
    tmp_path,
) -> None:
    v, _ = chain(tmp_path)
    dropped = answering(
        {SETTING: ["Δ reworded — a clause added"]},
        lambda t: as_assumption(t).replace(
            '→ qualifies def:gain · "We fix a field of scalars."\n', ""
        ),
    )

    found = review_update(v, update(v, host(dropped), PLAIN_SETTINGS))

    moved = marked(found["moved_by_rule"])
    assert moved[DEFINITION] == [
        (f"retracted:{SETTING}", f"retracted:{SETTING} → qualifies {DEFINITION}")
    ]
    assert PROOF_BLOCK in moved and SHARP in marked(found["stale"])


def test_a_retracted_justification_leaves_its_claim_resting_on_nothing(
    tmp_path,
) -> None:
    v, _ = chain(tmp_path)
    dropped = answering(
        {SETTING: ["Δ reworded — a clause added"]},
        lambda t: (
            as_assumption(t)
            .replace(
                '→ justifies thm:bound name · "Hence the output is bounded."\n', ""
            )
            .replace(
                '⊢ justified · "The output is bounded."',
                '⊢ unsupported · "The output is bounded."',
            )
        ),
    )

    # an answer that corrects an owner the edit did not reach: no queue task
    # serves one, so the marks are given on the answer itself
    _, made = plan(v.new.document, v.store, PLAIN_SETTINGS)
    found = dependency_marks(made, dropped(shown(made.question)), v.new.document)

    assert [c.why for c in found.stale[THEOREM]] == [
        "its justification was retracted",
        "no justifies edge reaches it now: it rests on nothing the graph holds",
    ]
    assert found.stale[THEOREM][0].via == f"{PROOF} → justifies {THEOREM}"
    assert not found.moved


def test_a_justification_naming_the_claim_it_covered_is_not_retracted(
    tmp_path,
) -> None:
    """A justifies line rewritten to name its target's one claim covers what
    it covered before: no retraction. A line is lost only where a claim it
    covered is covered by no line from its source onto its target now."""
    v, made = chain(tmp_path)
    (claim,) = es.claim_handles(es.parse(shown(made.question)), THEOREM)
    named = answering(
        {SETTING: ["Δ reworded — a clause added"]},
        lambda t: as_assumption(t).replace(
            '→ justifies thm:bound name · "Hence the output is bounded."',
            f'→ justifies thm:bound [{claim}] name · "Hence the output is bounded."',
        ),
    )

    # an answer that corrects an owner the edit did not reach: no queue task
    # serves one, so the marks are given on the answer itself
    found = dependency_marks(made, named(shown(made.question)), v.new.document)

    assert "its justification was retracted" not in [
        c.why for c in found.stale.get(THEOREM, [])
    ]


# --- the blocks the session's Δs move: read, or carried past the cap --------------


def test_a_moved_definition_owes_its_delta_and_unchanged_closes_it(tmp_path) -> None:
    v, made = chain(tmp_path)
    question, sheet, document = made.question, shown(made.question), v.new.document
    narrowed = {SETTING: ["Δ narrowed — scalars of a subfield only"]}

    # the setting qualifies the definition: its meaning moves, so it owes
    # a Δ line; the proof beyond it is not owed until the definition carries
    found = refused(question, answering(narrowed, as_assumption)(sheet), document)
    assert any(
        p.startswith(f"no Δ line ({DEFINITION}): your Δ lines move its meaning")
        for p in found
    )
    assert not any(PROOF_BLOCK in p for p in found)
    closed = check(question, answering(NARROWED_CLOSED, as_assumption)(sheet), document)
    assert closed.valid, closed.problems

    done = update(v, host(answering(NARROWED_CLOSED, as_assumption)), PLAIN_SETTINGS)

    record = review_update(v, done)
    assert record["closed_by_delta"] == [DEFINITION]
    assert record["moved_by_rule"] == []
    # the setting's own ask, the paper's scope, was read in the session
    assert record["stale"] == []


def test_a_moved_block_whose_delta_carries_owes_the_next(tmp_path) -> None:
    v, made = chain(tmp_path)
    question, sheet, document = made.question, shown(made.question), v.new.document
    carried = {
        SETTING: ["Δ narrowed — scalars of a subfield only"],
        DEFINITION: ["Δ narrowed — the gain over the subfield only"],
    }

    assert any(
        p.startswith(f"no Δ line ({PROOF_BLOCK}): your Δ lines move its meaning")
        for p in refused(question, answering(carried, as_assumption)(sheet), document)
    )
    read = {**carried, PROOF_BLOCK: ['Δ unchanged — "the gain is small": the same']}
    done = update(v, host(answering(read, as_assumption)), PLAIN_SETTINGS)

    record = review_update(v, done)
    # the definition carried and is the session's own read, the proof closed
    assert record["closed_by_delta"] == [PROOF_BLOCK]
    assert record["moved_by_rule"] == []
    assert SHARP not in marked(record["stale"])


def test_a_frontier_past_the_cap_carries_wholly(tmp_path) -> None:
    v, _ = chain(tmp_path)
    carried = {
        SETTING: ["Δ narrowed — scalars of a subfield only"],
        DEFINITION: ["Δ narrowed — the gain over the subfield only"],
    }
    capped = dataclasses.replace(PLAIN_SETTINGS, moved_delta_lines_max=1)

    # the definition's line took the one; the proof is not asked, and its
    # meaning moves unread, its users asked, as every block's did before
    done = update(v, host(answering(carried, as_assumption)), capped)

    assert done.complete
    record = review_update(v, done)
    assert "closed_by_delta" not in record
    assert [m["handle"] for m in record["moved_by_rule"]] == [PROOF_BLOCK]
    assert marked(record["moved_by_rule"])[PROOF_BLOCK] == [
        (DEFINITION, f"{PROOF_BLOCK} → uses {DEFINITION} [gain]")
    ]
    assert [c["root"] for c in record["moved_by_rule"][0]["causes"]] == [SETTING]
    assert SHARP in marked(record["stale"])


def test_a_block_closed_by_its_delta_keeps_what_asks_its_support(tmp_path) -> None:
    """A Δ says what the block now means, not whether what justifies it
    still does: the theorem, moved through the definition it uses and read
    `unchanged`, is not moved, but its proof's meaning moved, so it stays
    stale until its support is read again."""
    lines = {
        **CHAIN,
        THEOREM: [*CHAIN[THEOREM], '→ uses def:gain [gain] · "The output is bounded."'],
    }
    v = versions(tmp_path, EDITED_SETTING, lines, CHAIN_LEGEND)
    read = {
        SETTING: ["Δ narrowed — scalars of a subfield only"],
        DEFINITION: ["Δ narrowed — the gain over the subfield only"],
        PROOF_BLOCK: ['Δ narrowed — "the gain is small": over the subfield'],
        THEOREM: ['Δ unchanged — "The output is bounded": over any field'],
    }

    record = review_update(
        v, update(v, host(answering(read, as_assumption)), PLAIN_SETTINGS)
    )

    assert record["closed_by_delta"] == [THEOREM]
    assert record["moved_by_rule"] == []
    assert marked(record["stale"])[THEOREM] == [
        (PROOF_BLOCK, f"{PROOF} → justifies {THEOREM}")
    ]
    # each read block's Δ names the edit its move began at
    roots = {d["handle"]: d.get("root") for d in record["deltas"]}
    assert roots == {
        SETTING: None,
        DEFINITION: SETTING,
        PROOF_BLOCK: SETTING,
        THEOREM: SETTING,
    }


def test_an_unchanged_that_closes_quotes_its_evidence(tmp_path) -> None:
    v, made = chain(tmp_path)
    question, sheet, document = made.question, shown(made.question), v.new.document

    def answer(note: str) -> str:
        return answering(
            {**NARROWED_CLOSED, DEFINITION: [f"Δ unchanged — {note}"]}, as_assumption
        )(sheet)

    # no quote; words neither block holds; too few words; words of the
    # edited setting, which cannot vouch for what its own edit moved
    for note in (
        "over the same field",
        '"a field of complex numbers": the same',
        '"The gain": the same',
        "“We fix a field of scalars”: the same",
    ):
        assert any(
            f"{DEFINITION} Δ unchanged closes the change, so its note quotes" in p
            for p in refused(question, answer(note), document)
        ), note
    # the definition's own words
    found = check(question, answer('"The gain is the ratio": same'), document)
    assert found.valid, found.problems
    # a moved block's text did not change: `reworded` cannot close it
    reworded_moved = {
        **NARROWED_CLOSED,
        DEFINITION: ["Δ reworded — over the same field"],
    }
    assert any(
        f"{DEFINITION} Δ reworded: its text did not change" in p
        for p in refused(
            question, answering(reworded_moved, as_assumption)(sheet), document
        )
    )
    # the changed block whole needs a quote too; a reworded one does not
    whole = {SETTING: ["Δ unchanged — a clause added"]}
    assert any(
        f"{SETTING} Δ unchanged closes the change" in p
        for p in refused(question, answering(whole, as_assumption)(sheet), document)
    )
    reworded = {SETTING: ["Δ reworded — a clause added"]}
    found = check(question, answering(reworded, as_assumption)(sheet), document)
    assert found.valid, found.problems
    # a part's line alone leaves the edges onto the block whole unread: the
    # changed block owes its line for the block whole
    part_only = {SETTING: ["Δ [K] unchanged — its words stand"]}
    assert any(
        p.startswith(f"no Δ line ({SETTING}): its text or its reading changed")
        for p in refused(question, answering(part_only, as_assumption)(sheet), document)
    )


# --- replay and the brief ---------------------------------------------------------


def test_an_update_of_one_session_replays_byte_equal(tmp_path) -> None:
    v, _ = chain(tmp_path)
    narrowed = NARROWED_CLOSED
    asked = update(v, host(answering(narrowed, as_assumption)), PLAIN_SETTINGS)

    replayed = update(v, None, PLAIN_SETTINGS)

    assert replayed.complete
    assert dumps(update_payload(v.new.document, replayed, v.store)) == dumps(
        update_payload(v.new.document, asked, v.store)
    )


# --- the session's cut lines, applied as they are answered, splits first


def test_a_cut_line_the_assembler_refuses_goes_back_to_the_session(tmp_path) -> None:
    v, _ = chain(tmp_path)
    settings = dataclasses.replace(PLAIN_SETTINGS, paper=v.new.root)
    _, made = plan(v.new.document, v.store, settings)
    spec = json.loads(made.question.workspace.gate.spec)
    text = answering(
        {
            SETTING: [
                '✂ split "We fix a field" · "We fix a field of scalars." — at '
                "its start",
                "Δ narrowed — scalars of a subfield only",
            ]
        },
        as_assumption,
    )(shown(made.question))

    found = plain_cut_applied(spec, text, v.new.document)

    assert found is not None and found.sheet is None
    assert "your cut lines cannot be applied" in found.message


def test_a_deleted_proof_leaves_its_theorem_resting_on_nothing(tmp_path) -> None:
    """The proof deleted whole: its group's justifies edge onto the theorem
    goes with it. The session writes the theorem's standing anew; the
    marks say why: its justification was deleted, and none reaches it now."""
    proof = (
        "\\begin{proof}\n"
        "By Definition~\\ref{def:gain}, the gain is small\\footnote{Small means "
        "below one.}.\nHence the output is bounded.\n\n"
        "Next, the bound is sharp.\n\\end{proof}\n"
    )
    assert proof in MAIN
    v = versions(tmp_path, MAIN.replace(proof, ""), CHAIN, CHAIN_LEGEND)
    unsupported = answering(
        {},
        lambda t: t.replace(
            '⊢ justified · "The output is bounded."',
            '⊢ unsupported · "The output is bounded." — its proof is gone',
        ),
    )

    made = update(v, host(unsupported), PLAIN_SETTINGS)

    assert made.complete
    found = review_update(v, made)
    causes = {e["handle"]: e["causes"] for e in found["stale"]}
    assert [c["why"] for c in causes[THEOREM]] == [
        "its justification was deleted",
        "no justifies edge reaches it now: it rests on nothing the graph holds",
    ]
    assert causes[THEOREM][0]["from"].startswith("deleted:proof@")


def test_an_edit_to_a_proof_step_keeps_its_group_and_marks_its_line(tmp_path) -> None:
    """A word of the proof's first step changed: its group stands, its
    justifies line carried under its handle and owned by no member, and
    the step's Δ marks that line stale on the theorem."""
    v = versions(tmp_path, MAIN.replace("the gain is small", "the gain is tall"))
    _, planned = plan(v.new.document, v.store, PLAIN_SETTINGS)
    assert planned.single_session and planned.raw["groups_lost"] == []
    sheet = es.parse(shown(planned.question))
    assert PROOF in sheet.groups
    lines = [e for e in sheet.edges if e.kind == "justifies"]
    assert [(e.source, e.target) for e in lines] == [(PROOF, THEOREM)]

    def requoted(text: str) -> str:
        """The step's roles written and its quote of the edited words fixed."""
        return text.replace(f"⟦{PROOF_BLOCK} none⟧", f"⟦{PROOF_BLOCK} claim⟧").replace(
            '"the gain is small"', '"the gain is tall"'
        )

    narrowed = {PROOF_BLOCK: ["Δ narrowed — the gain is now tall"]}
    made = update(v, host(answering(narrowed, requoted)), PLAIN_SETTINGS)

    assert made.complete
    found = review_update(v, made)
    assert "groups_lost" not in found
    assert marked(found["stale"])[THEOREM] == [
        (PROOF_BLOCK, f"{PROOF} → justifies {THEOREM}")
    ]


def test_a_changed_block_whose_meaning_moved_is_said_so() -> None:
    """A statement whose declaration now means a conjecture: its words are
    the same, so its task says what moved."""
    from axiomation.identity import BlockClass, Lineage, Link, Tier

    raw = {
        "old_blocks": [
            {
                "id": "main.tex:7:1",
                "text": "Every gain is bounded by one.",
                "roles": ["claim"],
                "segments": [""],
                "groups": [],
                "meaning": "theorem",
            }
        ]
    }
    lineage = Lineage(
        (Link(("main.tex:7:1",), ("main.tex:7:1",), BlockClass.CHANGED, Tier.HASH),)
    )

    noted = meaning_notes(raw, lineage, {"main.tex:7:1": "conjecture"})

    assert noted == {
        "main.tex:7:1": "Its environment now reads as conjecture, where it read "
        "as theorem."
    }
    assert meaning_notes(raw, lineage, None) == {}
    assert meaning_notes(raw, lineage, {"main.tex:7:1": "theorem"}) == {}
