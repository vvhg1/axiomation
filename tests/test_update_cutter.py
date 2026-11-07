"""An update from the cutter, steps 1 to 3 on the
rebased cut, the edges on the cut they leave, the round after them, and
the export the scorer reads; `update --from-nothing` as `build`. On the
synthetic paper, a fake host answering every stage. No model is called.
"""

from __future__ import annotations

import dataclasses
import json
import shutil
from pathlib import Path

import pytest

from axiomation.assembler import corrected_cut
from axiomation.cli import BUILD_STAGES, main
from axiomation.driver import ReviewSettings, review_cut, review_payload, weigh
from axiomation.exchange import dumps
from axiomation.identity import BlockClass
from axiomation.questions import Stage, load_template
from axiomation.questions.decide_file import OLD_FILE, UPDATE_KICKOFF
from axiomation.questions.edges_file import EDGES_FILE
from axiomation.records import (
    IDENTITY,
    CorrectionClass,
    CutCorrection,
    CutOp,
    RecordStore,
)
from axiomation.resolver import Resolver
from axiomation.runner import Runner
from axiomation.update import (
    STEP_ROLES_UNANSWERED,
    Mode,
    UpdateError,
    plan,
    recut_regions,
    region_before,
    update_payload,
)
from axiomation.versions import identity_raw
from questions.review_paper import MAIN, make_paper
from questions.test_edges_file import WRITTEN, done, written
from questions.test_roles_file import first_pass
from runner.fakes import FakeHost
from test_cli import _fake_opencode_on_path
from test_driver import build_host
from test_update import (
    CHAIN,
    CHAIN_LEGEND,
    CLOSING,
    CUTTER,
    DEFINITION,
    EDITED_SETTING,
    FILES,
    FLAT,
    PROOF_BLOCK,
    SETTING,
    UPDATE,
    Versions,
    answering,
    assumptions,
    stepping,
    update,
    versions,
)

HOLD = dataclasses.replace(CUTTER, hold_cut=True)
REWORDED = {SETTING: ["Δ [K] reworded — a clause added after it"]}
#: A qualifier appended as a paragraph of its own after the setting's
#: claim: an added block beside a same one.
APPENDED = MAIN.replace(
    "A second sentence of the setting.\n",
    "A second sentence of the setting.\n\nProvided the field is finite.\n",
)
#: The closing words' markup changed: a 1:1 markup block, elsewhere.
MARKUP_TOO = EDITED_SETTING.replace(
    "Closing words of the results.", "Closing words of the \\emph{results}."
)


def handles_asked(question) -> set[str]:
    """The blocks a step's session asks, its context (`c1`, …) aside."""
    return {b for h, b in question.handles.items() if h.startswith("b")}


# --- nothing re-cut ------------------------------------------------------------------


def test_nothing_re_cut_asks_no_step_and_exports_as_before(tmp_path) -> None:
    """A markup edit: no block to re-cut, no group touched. From the cutter,
    no step is asked, and the export is the held update's but its mode."""
    v = versions(tmp_path, MAIN.replace("\\section{Setting}", "\\section*{Setting}"))
    fake = stepping(answering())

    cutter = update(v, fake, CUTTER)
    held = update(v)

    assert fake.calls == 0 and cutter.complete
    assert cutter.plan.steps.questions == {} and cutter.plan.mode is Mode.CUTTER
    assert cutter.plan.cut is cutter.plan.steps.origin
    a = update_payload(v.new.document, cutter, v.store)
    b = update_payload(v.new.document, held, v.store)
    assert a["review"]["update"].pop("mode") == "cutter"
    assert b["review"]["update"].pop("mode") == "hold-cut"
    assert dumps(a) == dumps(b)


# --- step 1, step 2 and session 1, from the cutter ---------------------------------


def test_a_changed_paragraph_asks_its_region_then_its_roles_then_session_1(
    tmp_path,
) -> None:
    v = versions(tmp_path, MARKUP_TOO, CHAIN, CHAIN_LEGEND)
    setting = v.new.id_at("We fix a field")
    closing = v.new.id_at("Closing words")
    fake = stepping(answering(REWORDED))

    found = update(v, fake, CUTTER)

    assert found.complete, found.unanswered
    steps = found.plan.steps
    (bounded,) = steps.questions[Stage.DECIDE]
    assert bounded.stage is Stage.DECIDE  # the decide seat's
    (region,) = recut_regions(steps.origin, found.plan.raw["recut"], (), 2)
    assert region.blocks == (setting,)
    assert region.before == (v.new.id_at("\\section{Setting}"),)
    assert region.after[0] == v.new.id_at("\\begin{definition}")
    assert "⟦context⟧" in bounded.workspace.files["cut.md"]
    assert handles_asked(bounded) == {setting}
    # step 2 asks the re-cut block alone; the markup block keeps its roles
    (roles,) = steps.questions[Stage.ROLES]
    assert handles_asked(roles) == {setting}
    links = {link.new[0]: link.cls for link in found.plan.lineage.links if link.new}
    assert links[closing] is BlockClass.MARKUP
    assert found.plan.cut.block(closing).roles == steps.origin.block(closing).roles
    assert [r.value for r in found.plan.cut.block(setting).roles] == ["assumption"]
    assert Stage.GROUPS not in steps.questions  # nothing near a group
    # then session 1, on the cut the steps left
    assert found.plan.question.input_cut == found.plan.cut
    assert [w.edit for w in fake.workspaces] == ["cut.md", "roles.md", EDGES_FILE]
    assert "⟦we-fix-a-field assumption⟧" in fake.workspaces[-1].files[EDGES_FILE]


RECUT_GOLDEN = Path(__file__).parent / "questions" / "golden" / "cut-review-update.md"


def _section(text: str, heading: str) -> str:
    return text.split(f"## {heading}\n", 1)[1].split("\n## ", 1)[0]


def test_step_1_asks_the_update_s_brief_with_the_region_as_it_read_before(
    tmp_path,
) -> None:
    """Step 1 asks under `cut-review-update`, which carries
    `cut-review-file`'s purpose and rules verbatim, and shows the region
    as it read before in `old.md`, bound in the key; the answer is the
    deciding step's form, so its gate and the weighing stand. Pinned whole
    by a golden."""
    v = versions(tmp_path, MARKUP_TOO, CHAIN, CHAIN_LEGEND)
    found = update(v, stepping(answering(REWORDED)), CUTTER)
    (bounded,) = found.plan.steps.questions[Stage.DECIDE]
    assert bounded.workspace is not None

    measured = load_template("cut-review-file").text
    brief = bounded.workspace.instructions
    assert bounded.template.name == "cut-review-update"
    for heading in ("What the cut is for", "The rules"):
        assert _section(brief, heading) == _section(measured, heading)
    assert bounded.prompt == UPDATE_KICKOFF
    was = bounded.workspace.files[OLD_FILE]
    assert "⟦was assumption⟧\nWe fix a field of scalars." in was
    assert "A second sentence of the setting." in was
    assert "edited" not in was
    assert set(bounded.workspace.files) == {"cut.md", "outline.md", OLD_FILE}

    text = "\n".join(
        [
            "# prompt",
            bounded.prompt,
            "# instructions",
            brief,
            "# cut.md",
            bounded.workspace.files["cut.md"],
            f"# {OLD_FILE}",
            was,
        ]
    )
    assert RECUT_GOLDEN.exists(), f"missing golden {RECUT_GOLDEN}; write it, reviewed"
    assert text == RECUT_GOLDEN.read_text(encoding="utf-8"), (
        "step 1's question differs from its golden; review the change first"
    )


def test_an_added_region_s_old_text_says_it_is_new() -> None:
    from axiomation.identity import Lineage, Tier
    from axiomation.identity import Link as LineageLink
    from axiomation.questions.chunks import Chunk

    lineage = Lineage((LineageLink((), ("n1",), BlockClass.ADDED, Tier.NONE),))
    raw = {"old_blocks": []}
    was = region_before(Chunk(("n1",), (), ()), raw, lineage)
    assert was.endswith("Nothing: this part is new in the revision.\n")


def test_an_added_paragraph_joined_to_its_claim_is_one_changed_block(
    tmp_path,
) -> None:
    v = versions(tmp_path, APPENDED, CHAIN, CHAIN_LEGEND)
    setting = v.new.id_at("We fix a field")
    added = v.new.id_at("Provided the field")
    _, made = plan(v.new.document, v.store, CUTTER)
    # the added block's region takes its same neighbours as blocks
    (bounded,) = made.steps.questions[Stage.DECIDE]
    assert handles_asked(bounded) == {
        setting,
        added,
        v.new.id_at("\\begin{definition}"),
    }

    def join(text: str) -> str:
        """Step 1 joins the appended words to the claim's paragraph."""
        lines = text.split("\n")
        at = next(n for n, line in enumerate(lines) if "Provided the field" in line)
        marker = next(n for n in range(at, -1, -1) if lines[n].startswith("⟦b"))
        lines[marker] = (
            '⟦join · "A second sentence of the setting." '
            '"Provided the field is finite." — the qualifier of the setting⟧'
        )
        return "\n".join(lines)

    owed = {
        SETTING: ["Δ [K] narrowed — only finite fields"],
        DEFINITION: ["Δ [gain] narrowed — over finite fields only"],
        "by-definition-def:gain-the": [
            'Δ unchanged — "the gain is small": the same steps'
        ],
    }
    fake = stepping(answering(owed), cut=join)
    found = update(v, fake, CUTTER)

    assert found.complete, found.unanswered
    (joined,) = found.proposals
    assert joined.applied and joined.stage == "decide" and joined.round == 0
    assert added not in {b.id for b in found.plan.cut.blocks}
    (link,) = [link for link in found.plan.lineage.links if link.new == (setting,)]
    assert link.cls is BlockClass.CHANGED and link.old == (v.old.id_at("We fix"),)
    sheet = fake.workspaces[-1].files[EDGES_FILE]
    assert "⟦was⟧ We fix a field of scalars. A second sentence of the setting." in sheet
    spec = json.loads(found.plan.question.workspace.gate.spec)
    assert spec["update"]["owed"][SETTING] == ["K"]
    payload = update_payload(v.new.document, found, v.store)
    (correction,) = payload["review"]["cut_corrections"]
    assert (correction["stage"], correction["round"]) == ("decide", 0)
    assert correction["applied"] is True
    # every excerpt names a block of the cut the steps left, as the export does
    named = set(payload["handles"].values())
    excerpts = payload["review"]["update"]["excerpts"]
    assert excerpts and all(x["block"] in named for x in excerpts if x["block"])


def test_hold_cut_asks_no_boundaries_but_the_roles(tmp_path) -> None:
    v = versions(tmp_path, EDITED_SETTING, CHAIN, CHAIN_LEGEND)
    fake = stepping(answering(REWORDED))

    found = update(v, fake, HOLD)

    assert found.complete and found.plan.mode is Mode.HOLD_CUT
    assert list(found.plan.steps.questions) == [Stage.ROLES]
    assert [w.edit for w in fake.workspaces] == ["roles.md", EDGES_FILE]
    data = update_payload(v.new.document, found, v.store)["review"]["update"]
    assert data["mode"] == "hold-cut"


def split_off(text: str) -> str:
    """Step 1's edit: the setting's edited sentence a block of its own."""
    return text.replace(
        "A second sentence of the setting, edited.",
        "⟦new — a second setting⟧\nA second sentence of the setting, edited.",
    )


def test_step_2_unanswered_takes_step_1_back_and_resumes(tmp_path) -> None:
    v = versions(tmp_path, EDITED_SETTING, CHAIN, CHAIN_LEGEND)

    stuck = update(v, stepping(answering(REWORDED), split_off, lambda t: t), CUTTER)

    assert not stuck.complete and stuck.answer is None
    (roles,) = stuck.questions[Stage.ROLES]
    assert stuck.unanswered == (roles.key,)
    (proposal,) = stuck.proposals
    assert not proposal.applied and proposal.problem == STEP_ROLES_UNANSWERED
    assert stuck.plan.cut == stuck.plan.steps.origin  # taken back
    assert len(handles_asked(roles)) == 2  # the split's two blocks
    with pytest.raises(UpdateError, match="not whole"):
        update_payload(v.new.document, stuck, v.store)

    # both halves of the split setting are changed blocks, each owing a Δ
    halves = {**REWORDED, "a-second-sentence-of": ["Δ reworded — split off"]}
    fake = stepping(answering(halves), split_off)
    resumed = update(v, fake, CUTTER)
    assert resumed.complete, resumed.unanswered
    assert [w.edit for w in fake.workspaces] == ["roles.md", EDGES_FILE]
    assert resumed.proposals[0].applied
    second = v.new.id_at("A second sentence")
    assert second in {b.id for b in resumed.plan.cut.blocks}


def test_boundary_passes_0_asks_no_step_1(tmp_path) -> None:
    """The profile's `boundary_passes` 0 (the review's `rounds`): from the
    cutter's stages, step 1 cannot be asked, so the mode reads hold-cut and
    the update starts at the roles."""
    v = versions(tmp_path, EDITED_SETTING, CHAIN, CHAIN_LEGEND)
    settings = dataclasses.replace(
        CUTTER, review=dataclasses.replace(CUTTER.review, rounds=0)
    )
    fake = stepping(answering(REWORDED))

    found = update(v, fake, settings)

    assert found.complete, found.unanswered
    assert found.plan.mode is Mode.HOLD_CUT
    assert Stage.DECIDE not in found.plan.steps.questions
    assert [w.edit for w in fake.workspaces] == ["roles.md", EDGES_FILE]


def test_a_step_s_new_block_never_takes_the_handle_its_slug_gives(
    tmp_path,
) -> None:
    """The closing paragraph starts with the words of the setting's second
    sentence: on the rebased cut it is `a-second-sentence-of`, and once
    step 1 splits that sentence off, the closing paragraph keeps the
    handle and the new block, whose words give the same slug, is `-2`
    (`docs/model.md` § 7). Carry's reason and the fuzzy hint, both on the
    closing paragraph, stay on it."""
    closing = "\\emph{A} second sentence of the results, which closes them."
    old_main = MAIN.replace("Closing words of the results.", closing)
    new_main = old_main.replace(
        "A second sentence of the setting.", "A second sentence of the setting, edited."
    ).replace("which closes them.", "which close them.")
    lines = {h: ls for h, ls in WRITTEN.items() if h != CLOSING}
    lines["a-second-sentence-of"] = ['→ about scope:paper · "which closes them."']
    v = versions(tmp_path, new_main, lines, old_main=old_main)
    first, renamed = "a-second-sentence-of", "a-second-sentence-of-2"
    _, rebased = plan(v.new.document, v.store, UPDATE)
    assert rebased.handles[first] == v.new.id_at("\\emph{A} second")
    assert rebased.carried.why[first] == "its words changed"
    (hint,) = rebased.carried.hints
    assert hint.handle == hint.block == first

    made = update(v, stepping(answering(), split_off), CUTTER).plan

    assert made.steps.complete and made.steps.proposals[0].applied
    assert made.handles[first] == v.new.id_at("\\emph{A} second")
    assert made.handles[renamed] == v.new.id_at("A second sentence of the setting")
    assert made.carried.why[first].startswith("its words changed")
    assert "its words changed" not in made.carried.why[renamed]
    (hint,) = made.carried.hints
    assert hint.handle == hint.block == first
    assert f"⟦done {first}⟧" not in made.carried.sheet


def test_step_3_asks_the_groups_where_a_member_changed(tmp_path) -> None:
    """A proof's step reworded: the group stands on the rebased cut, and
    step 3 asks the groups near the re-cut block on a cut that holds it."""
    v = versions(
        tmp_path,
        MAIN.replace("Hence the output is bounded.", "Hence the output is small."),
    )
    _, made = plan(v.new.document, v.store, CUTTER)
    assert made.raw["touched"] == [] and made.raw["groups_lost"] == []
    (was,) = v.old.cut.groups
    (group,) = made.steps.origin.groups
    assert (group.kind, group.blocks) == (was.kind, was.blocks)

    fake = stepping(answering(), roles=assumptions)
    found = update(v, fake, CUTTER)

    assert Stage.GROUPS in found.plan.steps.questions
    assert "groups.md" in [w.edit for w in fake.workspaces]
    assert found.plan.steps.complete


# --- step 8, the round --------------------------------------------------------------


def split_setting_store(tmp_path: Path) -> tuple[Versions, Path]:
    """OLD built on a cut whose setting a correction split in two (before
    "A second sentence"), its export written: the rebase carries that
    boundary into NEW, where the first sentence is edited."""
    old = make_paper(tmp_path / "old")
    roled = first_pass(old)
    split = CutCorrection(
        id="s1",
        op=CutOp.SPLIT,
        cls=CorrectionClass.OTHER,
        why="two settings",
        block=old.id_at("We fix a field"),
        at=("A second sentence of the setting.",),
        evidence=("A second sentence of the setting.",),
    )
    given = corrected_cut(old.document, roled, [split]).cut
    store = RecordStore(tmp_path / "store")
    settings = ReviewSettings(
        chunk=None, overlap=0, stages=(Stage.EDGES,), files=FILES, given_cut=True
    )
    fake = FakeHost(lambda p: "", edits={EDGES_FILE: written})
    review = review_cut(
        old.document, given, store, settings, Runner(fake, Resolver(store), 10.0, FLAT)
    )
    assert review.complete
    (tmp_path / "old.json").write_text(
        dumps(review_payload(old.document, review, settings)), encoding="utf-8"
    )
    new = make_paper(
        tmp_path / "new",
        main=MAIN.replace("We fix a field of scalars.", "We fix a field of reals."),
    )
    args = ["diff", str(old.root), str(new.root), "--displays", "in-paragraph"]
    args += ["--cut", str(tmp_path / "old.json"), "--store", str(store.directory)]
    assert main(args) == 0
    return Versions(old, new, store, ""), tmp_path / "old.json"


def reals(text: str) -> str:
    """The setting's qualifies line quoting its words as they read now."""
    return text.replace('"We fix a field of scalars."', '"We fix a field of reals."')


def test_a_round_applies_a_cut_line_and_a_rebased_boundary_flips_first(
    tmp_path,
) -> None:
    v, _ = split_setting_store(tmp_path)
    second = v.new.id_at("A second sentence")
    assert second in {b.id for b in plan(v.new.document, v.store, HOLD)[1].cut.blocks}
    merge = (
        "✂ merge a-second-sentence-of · "
        '"We fix a field of reals." — one setting, as the cutter had it'
    )

    def first(text: str) -> str:
        if "⟦was⟧" not in text:
            return done(text)  # the round's re-ask
        return answering(
            {"we-fix-a-field": [merge, "Δ reworded — reals for scalars"]}, reals
        )(text)

    fake = stepping(first)
    settings = dataclasses.replace(
        HOLD, review=dataclasses.replace(HOLD.review, cut_rounds=1)
    )
    found = update(v, fake, settings)

    assert found.complete, found.unanswered
    (merged,) = found.proposals
    # what the rebase carried is baseline: undoing OLD's split is a first
    # change, not a reversal
    assert merged.applied and merged.reverses == () and merged.round == 1
    # stacked on the cutter's cut instead, the same merge would undo a
    # correction: a reversal, which the rebased origin is not
    cutter = v.new.cut
    resplit = CutCorrection(
        id="r1",
        op=CutOp.SPLIT,
        cls=CorrectionClass.OTHER,
        why="two settings",
        block=v.new.id_at("We fix a field"),
        at=("A second sentence of the setting.",),
        evidence=("A second sentence of the setting.",),
    )
    after_split = corrected_cut(v.new.document, cutter, [resplit]).cut
    (against_cutter,) = weigh(
        v.new.document, cutter, [resplit], after_split, [merged.correction], 1, "edges"
    )
    assert against_cutter.reverses == (second,)
    assert len(found.cut_rounds) == 1 and found.reasked is not None
    assert second not in {b.id for b in found.corrected.cut.blocks}
    payload = update_payload(v.new.document, found, v.store)
    (correction,) = payload["review"]["cut_corrections"]
    assert (correction["stage"], correction["round"]) == ("edges", 1)
    assert correction["applied"] is True and "reverses" not in correction
    assert payload["edges_answer"] == found.reasked.key
    stages = {c["stage"] for c in payload["review"]["update"]["cost"].values()}
    assert stages == {IDENTITY, "roles", "edges"}
    # the export names the cut after the round: the merged block's OLD
    # blocks are the setting's two, and nothing names the block merged away
    data = payload["review"]["update"]
    setting = v.new.id_at("We fix a field")
    assert set(data["previous"]) == {b.id for b in found.corrected.cut.blocks}
    assert data["previous"][setting] == [setting, second]
    named = set(payload["handles"].values())
    assert all(x["block"] in named for x in data["excerpts"] if x["block"])
    assert data["deltas"] and all(d["handle"] in named for d in data["deltas"])
    # the owners opened, named on the cut after the round, as the scorer
    # resolves them through the export's handles
    handles = set(payload["handles"].values())
    assert data["reopened"] and all(
        r["handle"] in handles and (r["from"] is None or r["from"] in handles)
        for r in data["reopened"] + data["not_asked"]
    )
    # replayed from the store alone
    replayed = update(v, settings=settings)
    assert dumps(update_payload(v.new.document, replayed, v.store)) == dumps(payload)


def test_without_a_round_a_cut_line_is_listed_not_applied(tmp_path) -> None:
    v, _ = split_setting_store(tmp_path)
    merge = '✂ merge a-second-sentence-of · "We fix a field of reals." — one setting'
    fake = stepping(
        answering({"we-fix-a-field": [merge, "Δ reworded — reals for scalars"]}, reals)
    )

    found = update(v, fake, HOLD)

    assert found.complete
    (listed,) = update_payload(v.new.document, found, v.store)["review"][
        "cut_corrections"
    ]
    assert listed["applied"] is False and "cut_rounds 0" in listed["problem"]


# --- the export the scorer reads -----------------------------------------------------


def test_the_export_s_excerpts_deltas_and_cost(tmp_path) -> None:
    """The definition's words changed: its quotes are removed, demoted to
    it; every same block's are found. The identity answer holds them, so a
    replay needs no OLD."""
    v = versions(
        tmp_path,
        MAIN.replace(
            "The gain is the ratio of output to input.",
            "The gain is a quotient of output and input.",
        ),
        CHAIN,
        CHAIN_LEGEND,
    )
    requoted = answering(
        {DEFINITION: ["Δ [gain] reworded — a quotient"]},
        lambda t: t.replace(
            '[gain]: "The gain is the ratio of output to input."',
            '[gain]: "The gain is a quotient of output and input."',
        ).replace('"The gain is the ratio"', '"The gain is a quotient"'),
    )
    found = update(v, stepping(requoted), CUTTER)
    assert found.complete, found.unanswered
    data = update_payload(v.new.document, found, v.store)["review"]["update"]

    assert data["mode"] == "cutter"
    excerpts = {(x["block"], x["excerpt"]): x["cls"] for x in data["excerpts"]}
    assert excerpts[(DEFINITION, "The gain is the ratio")] == "removed"
    assert (
        excerpts[(DEFINITION, "The gain is the ratio of output to input.")] == "removed"
    )
    assert excerpts[("thm:bound", "The output is bounded.")] == "found"
    (identity,) = [a for a in v.store.authoritative().values() if a.kind == IDENTITY]
    carried = identity_raw(v.store, identity)["carried"]
    assert carried["excerpts"] == data["excerpts"]
    (delta,) = data["deltas"]
    assert delta["handle"] == DEFINITION and delta["entry"] == "gain"
    assert delta["block"] == v.new.id_at("\\begin{definition}")
    asked = [q for qs in found.questions.values() for q in qs]
    assert list(data["cost"]) == [found.identity.key, *(q.key for q in asked)]
    assert [c["stage"] for c in data["cost"].values()] == [
        IDENTITY,
        "decide",
        "roles",
        "edges",
    ]
    review = update_payload(v.new.document, found, v.store)["review"]
    assert review["questions"] == {"decide": 1, "roles": 1, "edges": 1}
    assert set(review["templates"]) == {
        "cut-review-update",
        "roles-lines",
        "edges-update",
    }


# --- the command ----------------------------------------------------------------------


def test_from_nothing_is_the_build_byte_for_byte(tmp_path, capsys, node_ids) -> None:
    paper = make_paper(tmp_path / "paper")
    store = RecordStore(tmp_path / "store")
    settings = ReviewSettings(stages=BUILD_STAGES, files=FILES)
    review = review_cut(
        paper.document,
        paper.cut,
        store,
        settings,
        Runner(build_host(), Resolver(store), 10.0, FLAT),
    )
    assert review.complete, review.unanswered
    # each its own store: a build on a built version asks nothing
    shutil.copytree(store.directory, tmp_path / "again")
    given = [str(paper.root), "--store", str(store.directory), "--host", "none"]
    again = [str(paper.root), "--store", str(tmp_path / "again"), "--host", "none"]
    built, updated = tmp_path / "build.json", tmp_path / "update.json"
    capsys.readouterr()

    node_ids()
    assert main(["build", "--json", *given, "-o", str(built)]) == 0
    summary = capsys.readouterr().out
    node_ids()
    assert main(["update", "--json", *again, "--from-nothing", "-o", str(updated)]) == 0

    assert capsys.readouterr().out == summary
    assert json.loads(summary)["complete"] is True
    assert updated.read_bytes() == built.read_bytes()
    exported = json.loads(built.read_text(encoding="utf-8"))
    assert "paper_version" not in exported and "update" not in exported["review"]


def test_from_nothing_refuses_a_version_to_update_from(tmp_path, capsys) -> None:
    paper = make_paper(tmp_path / "paper")
    given = [
        "update",
        "--json",
        str(paper.root),
        "--store",
        str(tmp_path),
        "--from-nothing",
    ]
    for extra, named in (
        (["--from", str(paper.root)], "--from"),
        (["--identity", "f" * 64], "--identity"),
        (["--hold-cut"], "--hold-cut"),
    ):
        capsys.readouterr()
        assert main([*given, *extra]) == 1
        err = capsys.readouterr().err
        assert "updates from no version" in err and named in err


def test_an_abbreviated_from_nothing_is_refused(tmp_path, capsys) -> None:
    """argparse takes `--from-n` for `--from-nothing`; `main` routes the
    build by the whole spelling, so the update refuses it rather than run
    from the store's identity answer."""
    paper = make_paper(tmp_path / "paper")
    capsys.readouterr()

    code = main(
        ["update", "--json", str(paper.root), "--store", str(tmp_path), "--from-n"]
    )

    assert code == 1
    assert "spell --from-nothing out whole" in capsys.readouterr().err


def test_opencode_s_prompt_limit_is_checked_on_the_steps(
    tmp_path, monkeypatch, capsys
) -> None:
    """Every question planned before any is asked is held to its seat's
    host's limit, step 1's first, not session 1's alone."""
    v = versions(tmp_path, EDITED_SETTING, CHAIN, CHAIN_LEGEND)
    _fake_opencode_on_path(tmp_path, monkeypatch)
    monkeypatch.setattr("axiomation.runner.opencode.PROMPT_LIMIT", 1)
    answers = len(v.store.authoritative())
    capsys.readouterr()

    code = main(
        ["update", "--json", str(v.new.root), "--store", str(v.store.directory)]
        + ["--host", "opencode", "--budget", "1"]
    )

    assert code == 1
    assert "a decide prompt is" in capsys.readouterr().err
    assert len(v.store.authoritative()) == answers  # nothing asked


@pytest.mark.usefixtures("develop_mode")
def test_the_dry_run_prices_the_steps(tmp_path, capsys) -> None:
    v = versions(tmp_path, EDITED_SETTING, CHAIN, CHAIN_LEGEND)
    args = ["update", "--json", str(v.new.root), "--store", str(v.store.directory)]
    args += ["--host", "opencode", "--dry-run", "--json", "--rates", "1,5"]
    capsys.readouterr()

    assert main(args) == 0
    priced = json.loads(capsys.readouterr().out)
    assert priced["mode"] == "cutter"
    assert set(priced["seats"]) == {
        "vocabulary",
        "vocabulary-check",
        "decide",
        "roles",
        "groups",
        "edges",
    }
    assert priced["steps"]["decide"]["questions"] == 1
    assert priced["steps"]["roles"]["questions"] == 1  # on the cut as it stands
    assert priced["steps_estimate"] > 0 and priced["estimate"] > 0
    assert priced["session_1_cut"] == "rebased"

    assert main([*args, "--hold-cut"]) == 0
    held = json.loads(capsys.readouterr().out)
    assert held["mode"] == "hold-cut" and "decide" not in held["steps"]
    assert "decide" not in held["seats"]

    update(v, stepping(answering(REWORDED)), CUTTER)
    capsys.readouterr()
    assert main(args) == 0
    answered = json.loads(capsys.readouterr().out)
    assert answered["steps_estimate"] == 0 and answered["estimate"] == 0
    assert answered["session_1_cut"] == "stepped"


@pytest.mark.usefixtures("develop_mode")
def test_the_command_runs_from_the_cutter_and_replays(tmp_path, capsys) -> None:
    """The command's update and the library's with the command's settings
    agree; replayed with OLD's directory gone, byte for byte."""
    v = versions(tmp_path, EDITED_SETTING, CHAIN, CHAIN_LEGEND)
    found = update(v, stepping(answering(REWORDED)), CUTTER)
    expected = dumps(update_payload(v.new.document, found, v.store))
    shutil.rmtree(v.old.root)
    output = tmp_path / "update.json"
    capsys.readouterr()

    code = main(
        ["update", "--json", str(v.new.root), "--store", str(v.store.directory)]
        + ["--host", "none", "-o", str(output)]
    )

    printed = capsys.readouterr()
    assert code == 0, printed.err
    assert output.read_text(encoding="utf-8") == expected
    summary = json.loads(printed.out)
    assert summary["mode"] == "cutter"
    assert summary["questions"] == {"decide": 1, "roles": 1, "edges": 1}


# --- resumed --------------------------------------------


def broken(text: str) -> str:
    """An edit no check passes: the file emptied."""
    return ""


def resumes(v: Versions, stuck_host: FakeHost, good_host: FakeHost, settings) -> None:
    """An update left unanswered by `stuck_host` exports nothing; the same
    call with `good_host` resumes it whole, and the store alone replays it
    byte for byte."""
    stuck = update(v, stuck_host, settings)
    assert not stuck.complete and stuck.unanswered
    with pytest.raises(UpdateError, match="not whole"):
        update_payload(v.new.document, stuck, v.store)
    resumed = update(v, good_host, settings)
    assert resumed.complete, resumed.unanswered
    payload = dumps(update_payload(v.new.document, resumed, v.store))
    replayed = update(v, settings=settings)
    assert dumps(update_payload(v.new.document, replayed, v.store)) == payload


def test_an_update_left_at_step_1_resumes(tmp_path) -> None:
    v = versions(tmp_path, EDITED_SETTING, CHAIN, CHAIN_LEGEND)
    resumes(
        v,
        stepping(answering(REWORDED), cut=broken),
        stepping(answering(REWORDED)),
        CUTTER,
    )


def small(text: str) -> str:
    """Session 1 on the proof's paragraph made "small": its self lines go
    (step 2 made it an assumption), the group's evidence is re-quoted, and
    the paragraph owes its Δ."""
    hence = '"Hence the output is bounded."'

    def edit(t: str) -> str:
        return (
            t.replace(f"→ self · {hence}\n", "")
            .replace(f"⊢ self · {hence}\n", "")
            .replace(f"name · {hence}", 'name · "Hence the output is small."')
        )

    return answering({PROOF_BLOCK: ["Δ reworded — small for bounded"]}, edit)(text)


def test_an_update_left_at_step_3_resumes(tmp_path) -> None:
    v = versions(
        tmp_path,
        MAIN.replace("Hence the output is bounded.", "Hence the output is small."),
    )

    def groups(answer):
        return FakeHost(
            lambda p: "",
            edits={
                "cut.md": lambda t: t,
                "roles.md": assumptions,
                "groups.md": answer,
                EDGES_FILE: small,
            },
        )

    resumes(v, groups(broken), groups(lambda t: t), CUTTER)


def test_an_update_left_at_a_round_resumes(tmp_path) -> None:
    v, _ = split_setting_store(tmp_path)
    merge = (
        "✂ merge a-second-sentence-of · "
        '"We fix a field of reals." — one setting, as the cutter had it'
    )

    def first(reask):
        def edit(text: str) -> str:
            if "⟦was⟧" not in text:
                return reask(text)
            return answering(
                {"we-fix-a-field": [merge, "Δ reworded — reals for scalars"]}, reals
            )(text)

        return stepping(edit)

    settings = dataclasses.replace(
        HOLD, review=dataclasses.replace(HOLD.review, cut_rounds=1)
    )
    resumes(v, first(broken), first(done), settings)


def test_an_update_left_at_its_fresh_session_resumes(tmp_path) -> None:
    v = versions(tmp_path, EDITED_SETTING)
    settings = dataclasses.replace(UPDATE, fresh_edges_above_reopened_fraction=0.0)
    lines = {**WRITTEN, SETTING: []}
    resumes(
        v,
        FakeHost(lambda p: "", edits={EDGES_FILE: broken}),
        FakeHost(lambda p: "", edits={EDGES_FILE: lambda t: written(t, lines)}),
        settings,
    )


def test_a_round_s_new_block_never_takes_the_handle_its_slug_gives(
    tmp_path,
) -> None:
    """The closing paragraph starts with the
    words of the setting's second sentence, so on the sessions' cut it is
    `a-second-sentence-of`; session 1 proposes splitting that sentence off
    the setting, and once the round applies it, the closing paragraph keeps
    the handle and the new block is `-2` (`docs/model.md` § 7).
    `review.update.reopened` and `not_asked` name each owner by the final
    cut's handle of its block."""
    closing = "\\emph{A} second sentence of the results, which closes them."
    old_main = MAIN.replace("Closing words of the results.", closing)
    new_main = old_main.replace(
        "A second sentence of the setting.", "A second sentence of the setting, edited."
    ).replace("which closes them.", "which close them.")
    lines = {h: ls for h, ls in WRITTEN.items() if h != CLOSING}
    lines["a-second-sentence-of"] = ['→ about scope:paper · "which closes them."']
    v = versions(tmp_path, new_main, lines, old_main=old_main)
    first, renamed = "a-second-sentence-of", "a-second-sentence-of-2"
    split = (
        '✂ split "A second sentence" · '
        '"A second sentence of the setting, edited." — its own sentence'
    )
    session_1 = answering(
        {
            SETTING: [split, "Δ reworded — a clause added"],
            first: ["Δ reworded — close for closes"],
        },
        lambda t: t.replace(
            '→ qualifies def:gain · "We fix a field of scalars."\n', ""
        ).replace('"which closes them."', '"which close them."'),
    )

    def edges(text: str) -> str:
        return session_1(text) if "⟦was⟧" in text else done(text)

    settings = dataclasses.replace(
        HOLD, review=dataclasses.replace(HOLD.review, cut_rounds=1)
    )
    found = update(v, stepping(edges), settings)

    assert found.complete, found.unanswered
    assert len(found.cut_rounds) == 1
    closing_id = v.new.id_at("\\emph{A} second")
    setting_id = v.new.id_at("We fix a field")
    assert found.plan.handles[first] == closing_id  # the sessions' cut
    payload = update_payload(v.new.document, found, v.store)
    block_of = {handle: ident for ident, handle in payload["handles"].items()}
    assert block_of[first] == closing_id
    assert block_of[renamed] == v.new.id_at("A second sentence of the setting")
    data = payload["review"]["update"]
    reopened = {block_of[r["handle"]]: r for r in data["reopened"]}
    assert set(reopened) == {setting_id, closing_id}
    assert reopened[closing_id]["handle"] == first
    assert reopened[closing_id]["from"] == first
    # the closing paragraph's own reason, not the split-off sentence's
    assert "cut through the words" in reopened[closing_id]["why"]
    assert reopened[setting_id]["from"] == SETTING
    assert all(
        block_of.get(r["handle"]) is not None
        and (r["from"] is None or block_of.get(r["from"]) is not None)
        for r in data["not_asked"]
    )


def test_from_nothing_is_the_build_with_a_role_correction_too(tmp_path, capsys) -> None:
    """Build = update(∅ → v) holds where the edges' standings correct a
    role."""
    paper = make_paper(tmp_path / "paper")
    store = RecordStore(tmp_path / "store")
    settings = ReviewSettings(stages=BUILD_STAGES, files=FILES)
    fake = build_host()
    fake.edits[EDGES_FILE] = lambda text: written(
        text,
        {"closing-words-of-the": ['⊢ unsupported · "Closing words of the results."']},
        [],
    )
    review = review_cut(
        paper.document,
        paper.cut,
        store,
        settings,
        Runner(fake, Resolver(store), 10.0, FLAT),
    )
    assert review.complete, review.unanswered
    assert [p.by for p in review.proposals] == ["edges"]
    shutil.copytree(store.directory, tmp_path / "again")
    given = [str(paper.root), "--store", str(store.directory), "--host", "none"]
    again = [str(paper.root), "--store", str(tmp_path / "again"), "--host", "none"]
    built, updated = tmp_path / "build.json", tmp_path / "update.json"

    assert main(["build", "--json", *given, "-o", str(built)]) == 0
    assert main(["update", "--json", *again, "--from-nothing", "-o", str(updated)]) == 0

    assert updated.read_bytes() == built.read_bytes()
    entry = json.loads(built.read_text(encoding="utf-8"))["review"]["cut_corrections"][
        0
    ]
    assert (entry["op"], entry["by"]) == ("roles", "edges")
