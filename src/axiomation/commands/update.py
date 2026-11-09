"""An update and what it starts from: the identity answer a kept state or
`--from OLD --cut CUT` makes for a new version, and `diff`, two versions'
identity."""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from axiomation import develop, person, queries, queue, update_queue, versions
from axiomation.commands import (
    DRY_RUN_HELP,
    FOLLOW_UPS,
    STORE_HELP,
    SUMMARY_JSON_HELP,
    Exit,
    Paper,
    Ran,
    Say,
    UsageError,
    asks,
    chosen,
    displays_argument,
    dollars,
    given_export,
    kept_on,
    output_argument,
    own_store,
    paper_arguments,
    paper_of,
    prompts_argument,
    questions_words,
    requested,
    said,
    seating,
    state_summary,
    written,
)
from axiomation.commands.build import (
    no_circles_argument,
    no_place_argument,
    no_search_argument,
    person_counts,
    shown,
    unmapped_summary,
    write_prompts,
)
from axiomation.commands.seating import Asking
from axiomation.cutter import Cut, CutRules, DisplayRule, cut
from axiomation.driver import Mapped, ReviewSettings, map_vocabulary
from axiomation.exchange import (
    ExchangeError,
    cut_from_payload,
    cut_payload,
    diff_payload,
    dumps,
    edges_answer_of,
    identity_key,
    vocabulary_of,
)
from axiomation.identity import (
    IDENTITY_VERSION,
    GroupLapse,
    Version,
    diff_summary,
    pair,
    rebase_cut,
)
from axiomation.profiles import Agent, Harness, Profile, review_settings
from axiomation.questions import Question, Stage
from axiomation.questions import edge_sheet as es
from axiomation.questions.edge_sheet import sheet_files
from axiomation.questions.edges_file import KIND as EDGES_KIND
from axiomation.questions.edges_file import carry, handles, records, state_sheet
from axiomation.reader import Document, Vocabulary, document_hash, read_paper
from axiomation.records import (
    IDENTITY,
    EntryKind,
    LineageLink,
    RecordError,
    RecordStore,
    holds,
)
from axiomation.resolver import Resolver
from axiomation.run_state import RunFile, made_from, text_of
from axiomation.runner import Stop, like_runs
from axiomation.runner.hosts import estimate
from axiomation.runner.opencode import OpenCodeHost
from axiomation.update import (
    Mode,
    Plan,
    UpdateSettings,
    asked_as,
    carried_origins,
    followup,
    followup_question,
    planned,
    priced_steps,
    queue_shown,
    queue_tasks,
    records_payload,
    steps_settings,
    update_payload,
)
from axiomation.update import plan as update_plan
from axiomation.update import run as run_update
from axiomation.versions import Command, State


@dataclass(frozen=True)
class Diff:
    old: Path
    new: Path
    displays: str = DisplayRule.OWN_BLOCK.value
    cut: Path | None = None
    store: Path | None = None
    json: bool = False
    output: Path | None = None


def diff(asked: Diff) -> str:
    """Two versions' identity: the cutter's cuts paired, or with
    `--cut` OLD's corrected cut rebased onto NEW and paired; with `--store`
    the store's edges sheet carried across and the identity answer
    appended. Prints the counts, or the payload with `--json`."""
    rules = CutRules(DisplayRule(asked.displays))
    old_document = read_paper(asked.old)
    new_document = read_paper(asked.new)
    if asked.cut is None:
        if asked.store is not None:
            raise UsageError("--store needs --cut, the export the store wrote")
        if asked.output is not None and not asked.json:
            raise UsageError("-o writes a rebased cut, which needs --cut, or --json")
        old = Version(old_document, cut(old_document, rules))
        new = Version(new_document, cut(new_document, rules))
        summary: dict[str, Any] = diff_summary(old, new, pair(old, new))
        return _diff_written(asked, summary, None)
    try:
        exported = json.loads(asked.cut.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise UsageError(f"--cut {asked.cut} is not JSON: {error}") from None
    old_cut = cut_from_payload(old_document, exported, rules, roles=True)
    if asked.store is None:
        old = Version(old_document, old_cut)
        rebased = rebase_cut(old, new_document, cut(new_document, old_cut.rules))
        lineage = pair(old, Version(new_document, rebased.cut))
        payload = diff_payload(old_document, old_cut, new_document, rebased, lineage)
        return _diff_written(asked, payload, (new_document, rebased.cut))
    store = RecordStore(asked.store)
    kept = versions.parent_by_export(store, asked.cut.read_bytes())
    made = made_identity(
        old_document,
        old_cut,
        exported,
        new_document,
        sheet_files(asked.new, new_document.never_included),
        store,
        kept
        if kept is not None and kept.version == document_hash(old_document)
        else None,
    )
    record_identity(store, made)
    return _diff_written(asked, made.payload, (new_document, made.cut))


def _diff_written(
    asked: Diff,
    found: dict[str, Any],
    rebased: tuple[Document, Cut] | None,
) -> str:
    """With `--json`, the payload (to -o or stdout); else, with a rebased
    cut and -o, the cut to -o; the counts to stdout."""
    if asked.json:
        text = json.dumps(found, indent=2, ensure_ascii=False) + "\n"
        if asked.output is None:
            return text
        asked.output.write_bytes(text.encode("utf-8"))
        return ""
    if rebased is not None and asked.output is not None:
        asked.output.write_bytes(dumps(cut_payload(*rebased)).encode("utf-8"))
    counts = {k: v for k, v in found["counts"].items() if v}
    width = max(len(name) for name in counts) if counts else 0
    lines = [f"{name:<{width}}  {count}" for name, count in counts.items()]
    if found["macros"]:
        lines.append("macros changed: " + " ".join(found["macros"]))
    if found.get("vocabulary"):
        lines.append("map entries changed: " + ", ".join(found["vocabulary"]))
    if rebased is not None:
        lost = found["groups_lost"]
        lines.append(f"to re-cut: {len(found['recut'])}; groups lost: {len(lost)}")
        lines += [f"  {g['group']}: {GroupLapse(g['why']).said}" for g in lost]
    if "carried" in found:
        carried = found["carried"]
        lines.append(
            f"reopened: {len(carried['reopened'])}; hints: {len(carried['hints'])}"
        )
    return "\n".join([found["identity"], *lines]) + "\n"


@dataclass(frozen=True, slots=True, eq=False)
class MadeIdentity:
    """An identity answer as made, before the store holds it: its key, its
    raw (`diff_payload`'s), its lineage's records and NEW's rebased cut."""

    key: str
    payload: dict[str, Any]
    links: tuple[LineageLink, ...]
    cut: Cut


def made_identity(
    old_document: Document,
    old_cut: Cut,
    exported: dict[str, Any],
    new_document: Document,
    new_files: Sequence[str],
    store: RecordStore,
    kept: State | None = None,
) -> MadeIdentity:
    """The identity answer `diff --cut --store` and `update --from` make:
    OLD's corrected cut rebased onto NEW and paired, and the
    sheet of the edges answer `exported` names carried across, with the
    person pins its `review.person` reports. Where OLD is a kept state,
    `kept`, NEW's cut is named by its kept handles (`versions.given_across`)."""
    old = Version(old_document, old_cut)
    rebased = rebase_cut(old, new_document, cut(new_document, old_cut.rules))
    lineage = pair(old, Version(new_document, rebased.cut))
    if kept is not None:
        given = versions.given_across(store, kept, old_cut, rebased.cut, lineage.links)
        rebased = replace(rebased, cut=replace(rebased.cut, handles=given))
    try:
        edges_key = edges_answer_of(exported)
    except ExchangeError as error:
        raise UsageError(
            f"--cut must be the export of one edges answer: {error}"
        ) from None
    if edges_key is None:
        raise UsageError(
            "--cut must be the export of one edges answer; it has no edges"
        )
    # OLD's answers as OLD read them, where joined copies answered apart
    seen = store if kept is None else store.seen_from(kept.id)
    answer = seen.authoritative().get(edges_key)
    if answer is None:
        raise UsageError(f"the store holds no answer {edges_key[:12]}")
    held = None
    if answer.kind == EDGES_KIND:
        # the sheet OLD's export shows, rendered from its lines by today's
        # code: the record holds no answer's text (docs/architecture.md § 4)
        sheet = state_sheet(
            old_document,
            old_cut,
            new_files,
            exported,
            seen.edge_records_of(answer.key).orders,
            seen.corrections_of(answer.key),
        )
    elif answer.kind == IDENTITY:
        # an update that asked nothing: its edges are the sheet its identity
        # answer carried, and their records are held in the new raw
        carried_raw = versions.identity_raw(seen, answer).get("carried")
        if not carried_raw:
            raise UsageError(
                f"--cut names identity answer {edges_key[:12]}, which carries no "
                "edges sheet"
            )
        sheet = carried_raw["sheet"]
        held = records_payload(records(sheet, handles(old_document, old_cut)))
    else:
        raise UsageError(
            f"--cut names answer {edges_key[:12]} of kind {answer.kind}: an edges "
            "answer, or an update's identity answer, gives the edges"
        )
    # cut lines no round weighed stay proposals, for NEW's round; those
    # OLD's rounds weighed and dropped do not come back
    carried = carry(
        old_document,
        _weighed_out(sheet, exported),
        old_cut,
        rebased.cut,
        new_files,
        new=new_document,
        lineage=lineage,
        cuts_weighed=False,
    )
    # OLD's person pins, as its export reports them, carried onto NEW with
    # the sheet
    exported_pins = ((exported.get("review") or {}).get("person") or {}).get("pins", [])
    pins = [
        person.pin_payload(pin)
        for pin in person.carried_pins(
            old_document,
            new_document,
            sheet,
            old_cut,
            rebased.cut,
            new_files,
            lineage,
            exported_pins,
        )
    ]
    found = diff_payload(
        old_document,
        old_cut,
        new_document,
        rebased,
        lineage,
        carried,
        edges_key,
        held,
        pins,
        carried_origins(
            exported,
            lineage,
            records(carried.sheet, handles(new_document, rebased.cut)),
            rebased.cut,
        ),
    )
    key = identity_key(
        old_document, new_document, old_cut, IDENTITY_VERSION, edges_key, pins
    )
    links = tuple(
        LineageLink(
            "",
            None,
            link.old,
            link.new,
            link.cls.value,
            link.tier.value,
            link.heir,
            link.moved,
        )
        for link in lineage.links
    )
    return MadeIdentity(key, found, links, rebased.cut)


#: A sheet's cut operation as a correction's (`move` is a rebound).
_CUT_OPS = {"split": "split", "merge": "merge", "move": "rebound"}


def _weighed_out(sheet: str, exported: dict[str, Any]) -> str:
    """OLD's edge half without the `✂` lines OLD's rounds weighed and
    dropped: those its export lists in
    `review.cut_corrections` not applied for a weighing's reason (refused,
    a reversal, a pin), matched by operation and evidence. A line never
    weighed, or not applied only for want of a round or an answer (a
    problem `not applied: …`), stays, for NEW's round."""
    dropped = {
        (c["op"], e["text"])
        for c in (exported.get("review") or {}).get("cut_corrections", [])
        if not c["applied"]
        and c.get("problem")
        and not c["problem"].startswith("not applied: ")
        for e in c["evidence"]
    }
    if not dropped:
        return sheet
    return "\n".join(
        line
        for line in sheet.split("\n")
        if not (
            (m := es.CUT.match(line))
            and (_CUT_OPS[m.group("op")], m.group("evidence")) in dropped
        )
    )


def record_identity(store: RecordStore, made: MadeIdentity) -> None:
    """The identity answer and its lineage appended, unless the store holds
    it already."""
    resolver = Resolver(store)
    with resolver.claim():
        if made.key in store.authoritative():
            return
        resolver.resolve_identity(
            made.key,
            IDENTITY_VERSION,
            json.dumps(made.payload, ensure_ascii=False),
            made.links,
        )


def diff_request(arguments: Any) -> Diff:
    """`diff`'s request from its parsed arguments."""
    return requested(Diff, arguments)


def _diff_arguments(commands: Any) -> None:
    diff = commands.add_parser(
        "diff",
        help="two versions of a paper: which block of NEW is which of OLD, "
        "and each one's class",
    )
    diff.add_argument("old", type=Path, help="the old version's directory")
    diff.add_argument("new", type=Path, help="the new version's directory")
    displays_argument(diff)
    diff.add_argument(
        "--cut",
        type=Path,
        help="OLD's corrected cut, a given cut or a build's export: rebased onto "
        "NEW (with -o and no --json, the rebased cut is written)",
    )
    diff.add_argument(
        "--store",
        type=Path,
        help="the store that wrote --cut: its edges sheet carried onto NEW, and "
        "the identity answer and its lineage appended",
    )
    diff.add_argument("--json", action="store_true", help="every link, not the counts")
    output_argument(diff)


@dataclass(frozen=True, kw_only=True)
class Update:
    """`update NEW`: the version in the paper's directory, the kept state it
    starts from (`paper.state`, else the latest) or OLD named otherwise
    (`--identity`, `--from OLD --cut CUT`), and who answers."""

    paper: Paper
    root: Path | None = None
    identity: str | None = None
    old: Path | None = None
    cut: Path | None = None
    displays: str = DisplayRule.IN_PARAGRAPH.value
    hold_cut: bool = False
    from_nothing: bool = False
    no_reread: bool = False
    no_search: bool = False
    no_place: bool = False
    no_circles: bool = False
    dry_run: bool = False
    json: bool = False
    prompts: Path | None = None
    output: Path | None = None
    asking: Asking = field(default_factory=Asking)


def request(arguments: argparse.Namespace) -> Update:
    """`update`'s request from its parsed arguments."""
    return requested(
        Update, arguments, paper=paper_of(arguments), asking=seating.asking(arguments)
    )


def run(asked: Update, say: Say, edited: State | None = None) -> Ran:
    """`update NEW --store S`: the identity answer for NEW, the
    store's or one `--from OLD --cut CUT` makes, then the steps on its
    rebased cut (from the cutter, or `--hold-cut`), session 1 and, where its
    answer reveals more, session 2, then the round, then the person rounds
    on NEW (a person's answers by their layers' type rule; OLD's person pins
    carried in the identity answer held in every step, gate and round);
    the export with `-o`.
    Every stage the update may ask is seated from the profile, as build
    seats them, and the budget spans them all. Exits `Exit.UNANSWERED`,
    naming the questions, when the update is not whole: nothing is
    exported, and the same command resumes. A dry run prices the steps,
    session 1 on the plan as it stands (on the cut the answered steps left,
    else the rebased cut) or the fresh session, and session 2 apart:
    exactly where session 1 is answered in the store, else at session 1's
    price if it fires. With `edited`, the kept state a vocabulary map was
    given on, NEW is that state's own files materialized in the directory,
    read with the map given (`edits.edit` on the vocabulary): the update of its
    version onto itself, kept as an `edit` state."""
    if asked.from_nothing:
        # `main` routes `--from-nothing` to the build by its whole spelling;
        # argparse also takes an abbreviation, which would land here and run
        # an update from the store's identity answer instead
        raise UsageError("spell --from-nothing out whole: it runs the build")
    if (asked.old is None) != (asked.cut is None):
        raise UsageError("--from and --cut go together: OLD and the export it gave")
    if asked.old is not None and asked.identity is not None:
        raise UsageError("--identity names a stored identity answer; --from makes one")
    if asked.paper.state is not None and (
        asked.old is not None or asked.identity is not None
    ):
        raise UsageError(
            "--state names a kept state to update from; --from and "
            "--identity name OLD otherwise"
        )
    given, defaulted = seating.default_profile(asked.asking)
    profile = seating.profile_of(given)
    hosted = seating.hosted(given)
    asking = hosted and not asked.dry_run
    if not holds(asked.paper.store, EntryKind.ANSWER):
        raise UsageError(
            f"{asked.paper.store} holds no answers: build the paper first; "
            "update carries its graph onto an edit"
        )
    update_settings = _update_settings(asked, profile)
    # The stages the update may ask: step 1 is the deciding step's question,
    # so it sits in the decide seat.
    seated = ReviewSettings(
        stages=(Stage.VOCABULARY, Stage.ROLES, Stage.GROUPS, Stage.EDGES),
        decide_passes=int(update_settings.mode is Mode.CUTTER),
        given_cut=True,
    )
    agents = seating.seated(
        profile, seated, priced=asking or asked.dry_run, hosted=hosted
    )
    store = RecordStore(asked.paper.store)
    # an update keeps a state, from OLD named or not: refused before it asks
    versions.refuse_apart(store)
    if not asked.dry_run and (asked.old is not None or asked.identity is not None):
        # OLD given otherwise than as a state: never the one that waits (a
        # dry run prices as if the waiting edits stood confirmed)
        versions.refuse_beside_waiting(store, None)
    document = _as_taken(asked, store, read_paper(asked.paper.directory, asked.root))
    update_settings = _with_files(asked, document, update_settings)
    # files changed alone ask nothing, whatever the profile
    alone = None if edited is not None else _files_alone(asked, document, store, say)
    # an edit's update answers the edit's own answers: they never refuse it
    own = (
        frozenset()
        if edited is None
        else frozenset(a.key for a in versions.pending_answers(store, edited.version))
    )
    if alone is not None:
        return Ran(alone)
    # NEW read with its vocabulary map: OLD's carried, the leftovers the
    # store's where answered, else asked first: identity and every step
    # read its cut
    early = None
    old_map = _old_map(asked, store)
    mapped = map_vocabulary(
        document, store, profile.templates, kept=old_map, waiting=True
    )
    if mapped.vocabulary is None and asking:
        given = seating.take_budget(
            given,
            asked.paper.directory,
            asked.paper.store,
            "update",
            seating.budget_needed(given, "update", defaulted),
            say,
            own,
        )
        own_store(asked.paper)
        early = seating.runner(
            given,
            profile,
            seating.seats_of(agents, seating.executables_of(agents)),
            store,
        )
        mapped = map_vocabulary(
            document,
            store,
            profile.templates,
            early,
            kept=old_map,
            waiting=True,
        )
    if mapped.vocabulary is None and not asked.dry_run:
        say.line(said(unmapped_summary(profile, mapped, store), asked.json))
        stopped = next((r for r in mapped.reports if r.stopped is not None), None)
        if stopped is not None and stopped.stopped is Stop.BREACH:
            return Ran(Exit.BREACH)
        return Ran(Exit.STOPPED if stopped is not None else Exit.UNANSWERED)
    if mapped.vocabulary is not None and mapped.vocabulary != Vocabulary():
        document = _read_again(
            document, read_paper(asked.paper.directory, asked.root, mapped.vocabulary)
        )
        update_settings = _with_files(asked, document, update_settings)
    identity, made, started = _update_identity(
        asked, document, store, update_settings, say
    )
    _refuse_pending(store, made, started, identity, mapped.given)
    if asking:
        given = seating.take_budget(
            given,
            asked.paper.directory,
            asked.paper.store,
            "update",
            seating.budget_needed(given, "update", defaulted),
            say,
            own,
        )
    executables = seating.executables_of(agents) if asking else {}
    if asked.prompts is not None:
        _write_update_prompts(
            asked.prompts, document, store, update_settings, made, identity
        )
    if asked.dry_run:
        priced_now = _update_dry_run(
            document,
            store,
            update_settings,
            profile,
            agents,
            made,
            identity,
            started,
            mapped,
            rereads_with=seating.edges_seated(agents[Stage.EDGES], False, "the reread")
            if profile.reread_marks_after_update and not asked.no_reread
            else None,
            support_with=seating.search_seated(profile, hosted=False)
            if profile.search_support_after_edges and not asked.no_search
            else None,
            place_with=seating.edges_seated(agents[Stage.EDGES], False, "the placing")
            if profile.place_lines_after_edges and not asked.no_place
            else None,
            circles_with=seating.edges_seated(
                agents[Stage.EDGES], False, "the circles question"
            )
            if profile.ask_circles_after_edges and not asked.no_circles
            else None,
        )
        say.line(
            json.dumps(priced_now, indent=2)
            if asked.json
            else _update_dry_run_lines(priced_now)
        )
        return Ran(0, (priced_now["round_estimate"], priced_now["round_expected"]))
    files = (
        versions.snapshot(asked.paper.directory, document)
        if edited is None
        else {path: versions.blob(store, name) for path, name in edited.files.items()}
    )
    own_store(asked.paper)
    if made is not None:
        record_identity(store, made)
    # the update's working state, resumed by the same update
    run = RunFile(
        store.run_directory,
        "update" if edited is None else "edit",
        document_hash(document),
        made_from(update_settings),
    )
    runner = None
    if asking:
        seats = seating.seats_of(agents, executables)
        if any(isinstance(seat.host, OpenCodeHost) for seat in seats.values()):
            # every question planned before any is asked, each against its
            # seat's host: the steps' (a step after an unanswered one built
            # on the cut as it stands) and session 1's
            _, planned_now = update_plan(document, store, update_settings, identity)
            assert planned_now.steps is not None
            planned_questions = priced_steps(
                document, update_settings, planned_now.raw, planned_now.steps
            )
            if planned_now.question is not None:
                planned_questions[Stage.EDGES] = (planned_now.question,)
            for stage, questions in planned_questions.items():
                host = seats[stage].host
                if isinstance(host, OpenCodeHost):
                    seating.within_limit(host, questions)
        if early is not None:
            early.resolver.run = run
        runner = (
            early
            if early is not None
            else seating.runner(given, profile, seats, store, run)
        )
    # an update of one session is asked as a queue, which a tool server
    # serves on claude-code alone; elsewhere it is refused when it is asked
    if runner is not None and agents[Stage.EDGES].harness is Harness.CLAUDE_CODE:
        queue_seats = seating.edges_seated(
            agents[Stage.EDGES], True, "the update's queue"
        )
        update_settings = replace(
            update_settings,
            edges_queue=update_queue.Seats(
                tuple(seating.queue_hosts(queue_seats)), queue_seats.autocompact_tokens
            ),
        )
    result = run_update(
        document,
        store,
        update_settings,
        runner,
        identity=identity,
        applied_before=started.answers if started is not None else (),
        run=run,
    )
    summary = {
        "profile": profile.name,
        **({"from_state": started.id} if started is not None else {}),
        "identity": result.identity.key,
        "mode": update_settings.mode.value,
        "complete": result.complete,
        "unanswered": list(result.unanswered),
        "questions": {stage.value: len(qs) for stage, qs in result.questions.items()},
        "reopened": len(result.plan.reopened),
        "conditional": len(result.plan.conditional),
        **_lost_in_rebase(result.plan.raw),
        "fresh": result.plan.fresh,
        **(
            {"session_2": result.second_question.key}
            if result.second_question is not None
            else {}
        ),
        "not_asked": len(result.not_asked),
        # blocks the one session's queue missed, served again at its end
        **({"mended_at_end": list(result.mended)} if result.mended else {}),
        **({"cut_rounds": len(result.cut_rounds)} if result.cut_rounds else {}),
        **(
            {"person": person_counts(result.person, result.plan.pins)}
            if result.person or result.plan.pins
            else {}
        ),
        "spent": round(sum(r.spent for r in (*mapped.reports, *result.reports)), 6),
        "stopped": result.stopped.stopped.value if result.stopped else None,
        **({"detail": result.stopped.detail} if result.stopped else {}),
    }
    if mapped.questions:
        summary["vocabulary"] = [q.key for q in mapped.questions]
    if result.mended:
        say.note(
            "axiomation update: the edges queue's end check served blocks its "
            f"draw missed again ({', '.join(result.mended)}): a defect to report"
        )
    if mapped.lapsed:
        # a person's map answer whose name's declaration changed, is gone, or
        # is now read by code
        summary["vocabulary_lapsed"] = [
            f"{a.namespace} {a.name}" for a in mapped.lapsed
        ]
    reread_stop: Stop | None = None
    support_stop: Stop | None = None
    place_stop: Stop | None = None
    circles_stop: Stop | None = None
    if result.complete:
        payload = update_payload(document, result, store)
        exported = dumps(payload)
        marked = payload["review"]["update"]
        if "shape" in marked:
            # what agents alone closed, and what carried on unread: one
            # audit read each
            summary["reach"] = {
                "closed_by_delta": len(marked.get("closed_by_delta", ())),
                "moved_by_rule_unread": len(marked["moved_by_rule"]),
                # the owners the rule asked; the marks open at the kept
                # state, every version's, once it is kept
                "asked": len(marked["stale"]),
            }
        _refuse_dropped(asked, store, document, result.person.stale)
        if asked.output is not None:
            written(exported, asked.output, say)
        if result.stopped is None:
            kept, _ = versions.record(
                store,
                files,
                document_hash(document),
                document.root,
                exported.encode("utf-8"),
                Command.UPDATE if edited is None else Command.EDIT,
                started_from=started,
                identity=result.identity.key,
                answers=[
                    *(edited.answers if edited is not None else ()),
                    *(r.key for r in result.person.applied),
                    *mapped.given,
                ],
                made_from=run.state.cursor.made_from,
                session=result.session_facts(document),
            )
            run.kept()
            summary["state"] = state_summary(asked.paper, store, kept)
            if "reach" in summary:
                view = queries.View(store, kept)
                summary["reach"] |= {
                    "open_marks": len(view.gate_marks),
                    "open_descriptions": len(view.marks) - len(view.gate_marks),
                    "by_edit": _reach_by_edit(view),
                }
            if started is not None:
                # each edge onto a deleted block that no line replaced
                summary["lost"] = [
                    i.line
                    for i in queries.lost_edges(
                        queries.View(store, started), queries.View(store, kept)
                    )
                ]
            # the state is kept and paid for: an end step that raises still
            # leaves its summary said
            try:
                if profile.reread_marks_after_update and not asked.no_reread:
                    reread = asks.reread_after(
                        store,
                        kept,
                        profile,
                        given.profiles,
                        runner,
                        agents[Stage.EDGES],
                    )
                    if reread is not None:
                        summary["reread"], reread_stop = reread
                        # the run's whole cost, the reread's beside it
                        summary["spent"] = round(
                            summary["spent"] + summary["reread"].get("spent", 0.0), 6
                        )
                reread = summary.get("reread", {})
                whole = not {"unanswered", "refused"} & set(reread)
                last = reread.get("state")
                if profile.search_support_after_edges and not asked.no_search and whole:
                    # after the reread, whose lines may argue a claim
                    searched = asks.support_after(
                        store,
                        versions.state(store, last) if last else kept,
                        profile,
                        given.profiles,
                        runner,
                    )
                    if searched is not None:
                        summary["support"], support_stop = searched
                        summary["spent"] = round(
                            summary["spent"] + summary["support"].get("spent", 0.0), 6
                        )
                        last = summary["support"].get("state", last)
                support = summary.get("support", {})
                searched = not {"unanswered", "refused"} & set(support)
                if (
                    profile.place_lines_after_edges
                    and not asked.no_place
                    and whole
                    and searched
                ):
                    # after the reread and the search, before the circles,
                    # which read what the placed lines change
                    placed = asks.place_after(
                        store,
                        versions.state(store, last) if last else kept,
                        profile,
                        given.profiles,
                        runner,
                        agents[Stage.EDGES],
                    )
                    if placed is not None:
                        summary["place"], place_stop = placed
                        summary["spent"] = round(
                            summary["spent"] + summary["place"].get("spent", 0.0), 6
                        )
                        last = summary["place"].get("state", last)
                place = summary.get("place", {})
                if (
                    profile.ask_circles_after_edges
                    and not asked.no_circles
                    and whole
                    and searched
                    and not {"unanswered", "refused"} & set(place)
                ):
                    # after the reread, the search and the placing, whose
                    # lines the circles read
                    circled = asks.circles_after(
                        store,
                        versions.state(store, last) if last else kept,
                        profile,
                        given.profiles,
                        runner,
                        agents[Stage.EDGES],
                    )
                    if circled is not None:
                        summary["circles"], circles_stop = circled
                        summary["spent"] = round(
                            summary["spent"] + summary["circles"].get("spent", 0.0), 6
                        )
            except BaseException:
                say.line(said(summary, asked.json))
                raise
    say.line(said(summary, asked.json))
    if "unanswered" in summary.get("circles", {}):
        say.note(
            f"axiomation update: state {summary['state']['id']} is kept; the question "
            "on its circles is not answered: `axiomation circles PAPER --ask` asks it"
        )
    if "unanswered" in summary.get("place", {}):
        say.note(
            f"axiomation update: state {summary['state']['id']} is kept; the placing "
            "of its blocks' lines is not answered, nor the question on its circles "
            "asked: `axiomation place PAPER --ask` asks it, then `axiomation "
            "circles PAPER --ask`"
        )
    if "unanswered" in summary.get("support", {}):
        say.note(
            f"axiomation update: state {summary['state']['id']} is kept; the search "
            "for its claims' support is not answered, nor the placing and the "
            "question on its circles asked: `axiomation support PAPER --ask` asks "
            "it, then `axiomation place PAPER --ask` and `axiomation circles PAPER "
            "--ask`"
        )
    if "unanswered" in summary.get("reread", {}):
        say.note(
            f"axiomation update: state {summary['state']['id']} is kept; the reread "
            "of its stale marks is not answered, nor the search for its claims' "
            "support, the placing and the question on its circles asked: "
            "`axiomation reread PAPER --ask` asks it, then `support --ask`, "
            "`place --ask` and `circles --ask`"
        )
    if Stop.BREACH in (reread_stop, support_stop, place_stop, circles_stop):
        return Ran(Exit.BREACH)
    stopped = result.stopped
    if stopped is not None:
        return Ran(Exit.BREACH if stopped.stopped is Stop.BREACH else Exit.STOPPED)
    if not result.complete:
        names = asked_as(result)
        named = ", ".join(
            f"{key[:12]} ({names.get(key, 'unknown')})" for key in result.unanswered
        )
        say.note(
            f"axiomation update: not whole, nothing exported: unanswered {named}; "
            "the same command resumes"
        )
        # an answer the check refused, with why
        for report in result.reports:
            for key in report.failed:
                if report.detail:
                    say.note(f"  {key[:12]} refused: {report.detail}")
        return Ran(Exit.UNANSWERED)
    return Ran(0)


def _reach_by_edit(view: queries.View) -> list[dict[str, Any]]:
    """What the update kept as `view`'s state left, per edit it began at
    (None: the update whole, a retraction's move or one no edit named):
    the blocks it carried unread, its marks the gates ask and its
    descriptions (`docs/model.md` § 5)."""
    found: dict[str | None, dict[str, Any]] = {}

    def entry(edit: str | None) -> dict[str, Any]:
        return found.setdefault(
            edit, {"edit": edit, "unread": 0, "marks": 0, "descriptions": 0}
        )

    for u in view.unread:
        if u.since == view.state.id:
            entry(u.edit)["unread"] += len(u.nodes)
    for m in view.marks:
        if m.since != view.state.id:
            continue
        for root in dict.fromkeys(c["root"] for c in m.causes):
            entry(root)["marks" if m.gate else "descriptions"] += 1
    return list(found.values())


def _refuse_pending(
    store: RecordStore,
    made: MadeIdentity | None,
    started: State | None,
    identity: str | None,
    mapped: Collection[str] = (),
) -> None:
    """Refused where OLD's version holds an edit no kept state applied (a
    cut, roles or groups edit whose step is not answered yet, which its own
    `edit` resumes): an update applies NEW's answers alone, so it would be
    lost. A vocabulary map given on it, `mapped`, is NEW's map, which this
    update applies.
    Only where the store keeps a state of OLD's version, which tells what
    was applied."""
    old = (
        made.payload["old"]
        if made is not None
        else started.version
        if started is not None
        else None
    )
    if old is None and identity is not None:
        answer = store.authoritative().get(identity)
        old = (
            None if answer is None else versions.identity_raw(store, answer).get("old")
        )
    if old is None or not any(s.version == old for s in versions.states(store)):
        return
    pending = [a for a in versions.pending_answers(store, old) if a.key not in mapped]
    if pending:
        named = ", ".join(f"{a.stage} {a.key[:12]} by {a.host}" for a in pending)
        raise UsageError(
            f"version {old[:12]} holds edits no state applied: {named}; an update "
            "applies the new version's edits alone, so these would be lost: "
            "resume each first, by its own `axiomation edit` on that version, "
            "which asks what it reopens"
        )


def _update_settings(asked: Update, profile: Profile) -> UpdateSettings:
    """An update's settings from the profile: every stage's, as
    `build` asks them, the follow-up and the switch, and `--hold-cut`."""
    review = review_settings(profile)
    return UpdateSettings(
        limit=profile.chunk_characters,
        edges=profile.templates.edges,
        edges_update=profile.templates.edges_update,
        followup_sessions_max=profile.followup_sessions_max,
        reverse_prefilter=profile.reverse_prefilter,
        fresh_edges_above_reopened_fraction=profile.fresh_edges_above_reopened_fraction,
        review=review,
        hold_cut=asked.hold_cut,
        per_stage_update_above_recut_blocks=profile.per_stage_update_above_recut_blocks,
        moved_delta_lines_max=profile.moved_delta_lines_max,
    )


def _with_files(
    asked: Update, document: Document, settings: UpdateSettings
) -> UpdateSettings:
    """`settings` with the paper's files, the update's and its steps'."""
    files = tuple(sheet_files(asked.paper.directory, document.never_included))
    return replace(
        settings,
        files=files,
        review=replace(settings.review, files=files),
        paper=Path(asked.paper.directory).resolve(),
    )


def _read_again(document: Document, again: Document) -> Document:
    """`again`, the paper read once more with its vocabulary map, named by
    `document`'s version: refused where its files changed between the reads
    (a map question may take minutes), since the version names the text
    first read."""
    if [(s.path, s.sha256) for s in again.files] != [
        (s.path, s.sha256) for s in document.files
    ]:
        raise UsageError("the paper changed while it was read; run again")
    return replace(again, version=document.version)


def _as_taken(asked: Update, store: RecordStore, document: Document) -> Document:
    """The paper an update reads, named by the version of the kept state it
    starts from (`--state`, else the latest) where today's reader reads its
    files as that state's (`versions.as_kept`): a reader change alone is no
    edit, and the update of a map onto the version stays on it. As read
    where OLD is named otherwise or the store keeps no state."""
    kept = versions.states(store, waiting=True)
    if asked.old is not None or asked.identity is not None or not kept:
        return document
    take = kept_on(asked.paper, store)
    return versions.as_kept(store, take, document)


def _files_alone(
    asked: Update, document: Document, store: RecordStore, say: Say
) -> int | None:
    """An update whose text is the kept state's own and whose kept files
    are not (a script, a figure changed alone): a state of its
    own, the state's export carried unchanged, nothing asked; its exit
    code. None where the text changed, or nothing did, or OLD is named
    otherwise than by `--state`."""
    kept = versions.states(store, waiting=True)
    if asked.old is not None or asked.identity is not None or not kept:
        return None
    take = kept_on(asked.paper, store)
    if take.version != document_hash(document):
        return None
    files = versions.snapshot(asked.paper.directory, document)
    hashes = {k: hashlib.sha256(v).hexdigest() for k, v in files.items()}
    if hashes == dict(take.files):
        return None
    # `record` continues the latest state of the version (of its identity
    # answer, where it has one): carrying an earlier one's export onto it
    # would undo what came between
    latest = [
        s
        for s in kept
        if s.version == take.version
        and (take.identity is None or s.identity == take.identity)
    ][-1]
    if latest.id != take.id:
        raise UsageError(
            f"state {take.id} is not the latest of its version; {latest.id} is: "
            f"update from it (`--state {latest.id}`), so what came between is kept"
        )
    changed = sorted(
        k
        for k in hashes.keys() | take.files.keys()
        if hashes.get(k) != take.files.get(k)
    )
    if asked.dry_run:
        say.line(
            f"the text is state {take.id}'s; its files differ ({', '.join(changed)}): "
            "an update keeps them as a state of their own, asking nothing"
        )
        return 0
    own_store(asked.paper)
    state, new = versions.record(
        store,
        files,
        take.version,
        take.root,
        versions.kept_export(store, take),
        Command.UPDATE,
        started_from=take,
        identity=take.identity,
    )
    say.line(
        f"state {state.id}{'' if new else ' (kept already)'}: the text of state "
        f"{take.id}, its files changed alone ({', '.join(changed)}); nothing asked"
    )
    if asked.output is not None:
        written(versions.kept_export(store, state).decode("utf-8"), asked.output, say)
    return 0


def _old_map(asked: Update, store: RecordStore) -> Vocabulary | None:
    """The vocabulary map of the state an update starts from, carried onto
    NEW (`driver.map_vocabulary`): `--cut`'s export's with `--from OLD`,
    else the kept state `_update_identity` takes; none where the store
    keeps none."""
    if asked.old is not None:
        return vocabulary_of(given_export(asked.cut))
    kept = versions.states(store, waiting=True)
    if asked.identity is not None or not kept:
        return None
    take = chosen(asked.paper, store, waiting=True)
    return vocabulary_of(versions.kept_payload(store, take))


def _map_moved(store: RecordStore, take: State, document: Document) -> bool:
    """Whether `document` is read with another vocabulary map than the kept
    state `take` was, in what the reader reads: a map given on the version
    (`edit --stage vocabulary`), applied by an update of the version onto
    itself."""

    def read(vocabulary: Vocabulary) -> tuple[object, ...]:
        return (vocabulary.environments, vocabulary.references, vocabulary.labels)

    kept = vocabulary_of(versions.kept_payload(store, take))
    return read(kept) != read(document.vocabulary)


def _update_identity(
    asked: Update,
    document: Document,
    store: RecordStore,
    settings: UpdateSettings,
    say: Say,
) -> tuple[str | None, MadeIdentity | None, State | None]:
    """The identity answer an update starts from, and the kept state it
    starts from where the store keeps it: the one `--from OLD
    --cut CUT` makes, not yet recorded; the one `--identity` names; else,
    where the store keeps states, the one made from the state `--state`
    names (the latest without it), its kept files and export as OLD, or,
    where that state is NEW's own version, the identity answer it was
    updated by (a re-run); else the store's for NEW."""
    rules = CutRules(DisplayRule(asked.displays))
    if asked.old is not None:
        exported = given_export(asked.cut)
        old_document = read_paper(asked.old, vocabulary=vocabulary_of(exported))
        old_cut = cut_from_payload(old_document, exported, rules, roles=True)
        given = versions.parent_by_export(store, asked.cut.read_bytes())
        if given is not None and given.version != document_hash(old_document):
            # the export is kept, but of another text than --from's
            given = None
        made = made_identity(
            old_document, old_cut, exported, document, settings.files, store, given
        )
        return made.key, made, given
    kept = versions.states(store, waiting=True)
    if asked.identity is not None or not kept:
        if asked.paper.state is not None:
            raise UsageError(f"{asked.paper.store} keeps no state")
        identity = asked.identity or _stored_identity(store, document)
        found = (
            None if identity is None else versions.parent_by_identity(store, identity)
        )
        return asked.identity, None, found
    take = chosen(asked.paper, store, waiting=True)
    heads = versions.heads(kept)
    if asked.paper.state is None and len(heads) > 1:
        say.note(
            f"axiomation update: {len(heads)} heads ("
            + ", ".join(h.id for h in heads)
            + f"); updating from the latest, {take.id}; --state names another"
        )
    if take.version == document_hash(document) and not _map_moved(
        store, take, document
    ):
        if take.identity is None:
            raise UsageError(
                f"the paper is the version state {take.id} kept ({take.command}): "
                "nothing to update"
            )
        # an edit's state carries its update's identity answer: re-running
        # that update would ask again what the edits since corrected, so it
        # is only a replay, which asks nothing
        if take.command is not Command.UPDATE and asked.asking.host != "none":
            raise UsageError(
                f"the paper is the version state {take.id} kept ({take.command}): "
                "nothing to update (in develop mode, `--host none` replays the "
                "update before it with the edits given since)"
            )
        # a re-run of the update that made it: its identity answer, its parent
        return take.identity, None, versions.parent_by_identity(store, take.identity)
    with tempfile.TemporaryDirectory(prefix="axiomation-old-") as directory:
        old_document = versions.materialize(store, take, Path(directory))
        exported = versions.kept_payload(store, take)
        old_cut = cut_from_payload(old_document, exported, rules, roles=True)
        made = made_identity(
            old_document, old_cut, exported, document, settings.files, store, take
        )
    return made.key, made, take


def _refuse_dropped(
    asked: Update,
    store: RecordStore,
    document: Document,
    stale: Sequence[str],
) -> None:
    """A replay of the update before an edit (`--host none` on an edit's
    version) records nothing that leaves out an edit the state it replays
    holds: refused, naming each answer it could not apply again because the
    sheet it was given on is not the one the replay shows (an update whose
    sheets named blocks by their words, with no kept handles, `docs/model.md`
    § 7, replayed after an edit named by them; an edit given on a state
    another edit had superseded)."""
    kept = versions.states(store, waiting=True)
    if not kept or not stale:
        return
    take = chosen(asked.paper, store, waiting=True)
    if take.version != document_hash(document) or take.command is Command.UPDATE:
        return
    held = {
        key
        for s in versions.chain(store, take)
        if s.version == take.version
        for key in s.answers
    }
    dropped = [key for key in stale if key in held]
    if dropped:
        raise UsageError(
            f"the replay cannot apply again {len(dropped)} edits state {take.id} "
            "holds (" + ", ".join(k[:12] for k in dropped) + "): the sheet each "
            "was given on is not the one the replay shows; nothing recorded. "
            "Update forward from the state instead"
        )


def _stored_identity(store: RecordStore, document: Document) -> str | None:
    """The key of the store's identity answer for the version, where it
    holds exactly one."""
    try:
        found = store.identity_for(document_hash(document))
    except RecordError:
        return None
    return None if found is None else found.key


#: The attempts an update's edges session is priced at: its gate asks a
#: Δ line of every block the edit moves, and a first attempt it refuses,
#: in the session or after it, is asked again (docs/architecture.md § 3).
UPDATE_EDGES_ATTEMPTS_PRICED = 2


def _planned(
    document: Document,
    store: RecordStore,
    settings: UpdateSettings,
    made: MadeIdentity | None,
    identity: str | None,
) -> Plan:
    """The update's plan as it stands, before anything is asked: on an
    identity answer `--from` made in memory where the store holds none."""
    if made is not None and made.key not in store.authoritative():
        return planned(document, store, settings, made.key, made.payload, made.links)
    _, found = update_plan(document, store, settings, identity)
    return found


def _write_update_prompts(
    directory: Path,
    document: Document,
    store: RecordStore,
    settings: UpdateSettings,
    made: MadeIdentity | None,
    identity: str | None,
) -> None:
    """What each agent the update opens is served, under `directory`, on
    the plan as it stands: each step's prompt as build writes it
    (`write_prompts`), and the edges session's, or, where it is one
    session asked as a queue, its brief and tasks (`asks.write_queue_prompts`).
    The queue's working files go to a scratch directory: nothing is kept."""
    plan_now = _planned(document, store, settings, made, identity)
    assert plan_now.steps is not None
    questions = priced_steps(
        document,
        steps_settings(settings) if plan_now.single_session else settings,
        plan_now.raw,
        plan_now.steps,
    )
    question = plan_now.question
    if question is not None and not plan_now.single_session:
        questions[Stage.EDGES] = (*questions.get(Stage.EDGES, ()), question)
    write_prompts(directory, questions)
    if question is None or not plan_now.single_session:
        return
    with tempfile.TemporaryDirectory(prefix="axiomation-prompts-") as scratch:
        run = update_queue.run_of(RecordStore(Path(scratch)), question)
        update_queue.write_asked(
            run, question, document, queue_shown(plan_now, question), settings.edges
        )
        with queue.opened(run) as workflow:
            asks.write_queue_prompts(workflow, directory)


def _update_dry_run(
    document: Document,
    store: RecordStore,
    settings: UpdateSettings,
    profile: Profile,
    agents: dict[Stage, Agent],
    made: MadeIdentity | None,
    identity: str | None,
    started: State | None = None,
    mapped: Mapped | None = None,
    rereads_with: seating.QueueSeats | None = None,
    support_with: seating.QueueSeats | None = None,
    place_with: seating.QueueSeats | None = None,
    circles_with: seating.QueueSeats | None = None,
) -> dict[str, Any]:
    """`update --dry-run`, naming the kept state it starts from
    where there is one: the vocabulary map's first question where it is
    unanswered (the rest then priced on NEW read without its map); the
    steps' questions priced, each at
    its seat's rates (`priced_steps`: a step after an unanswered one built
    on the cut as it stands); session 1 on the plan as it stands, and
    session 2 apart, each at `UPDATE_EDGES_ATTEMPTS_PRICED` attempts (an
    update of one session, its queue at what its first tasks cost,
    `update_queue.price`); and,
    where the update ends with the reread (`rereads_with`, its queue's
    seats), the reread if it fires, on the owners of the state it starts
    from: its marks come from session 1's answer, so no owner is known yet;
    and so, where it ends with the search for its claims' support
    (`support_with`), that, with the placing (`place_with`), that, and
    with the circles' question (`circles_with`), that.
    `round_estimate` sums them, each step at the price it would fire at,
    and `round_expected` each session times what like runs cost in the
    store (`like_runs`), each queue at what its tasks cost. Writes nothing;
    an identity answer `--from` makes is made in memory."""
    planned_now = _planned(document, store, settings, made, identity)
    steps = planned_now.steps
    assert steps is not None
    answers = store.authoritative()
    like = like_runs(store.answers())
    # the round's price, each part as it would fire: its estimate, and that
    # times what like runs cost
    round_price = [0.0, 0.0]

    def counted(template: str, cost: float) -> None:
        round_price[0] += cost
        round_price[1] += like.expected(template, cost)

    def priced(stage: Stage, questions: Sequence[Question], attempts: int = 1) -> float:
        rates = agents[stage].rates
        assert rates is not None
        found = 0.0
        for q in questions:
            if q.key in answers:
                continue
            cost = estimate(
                q.prompt, rates, q.workspace, seating.harness_tokens(agents[stage])
            )
            found += cost
            counted(q.template.name, cost * attempts)
        return found

    stepped = {
        stage: {
            "questions": len(questions),
            "answered": sum(q.key in answers for q in questions),
            "estimate": round(priced(stage, questions), 4),
        }
        for stage, questions in priced_steps(
            document,
            steps_settings(settings) if planned_now.single_session else settings,
            planned_now.raw,
            steps,
        ).items()
    }
    question = planned_now.question
    answered = question is not None and question.key in answers
    # one session is a queue, priced by its first tasks as the other queues
    # are: no attempt asked again, no prompt but its brief
    single = question is not None and planned_now.single_session
    tasks = 0
    if single:
        assert question is not None
        tasks = 0 if answered else queue_tasks(planned_now, question)
        try:
            price = update_queue.price(store, agents[Stage.EDGES].model)
        except update_queue.UpdateQueueError as error:
            raise UsageError(str(error)) from None
        attempt = cost = price.cost(tasks) if tasks else 0.0
        round_price[0] += cost
        round_price[1] += cost
    else:
        attempt = (
            0.0
            if question is None
            else priced(Stage.EDGES, [question], UPDATE_EDGES_ATTEMPTS_PRICED)
        )
        cost = attempt * UPDATE_EDGES_ATTEMPTS_PRICED
    asks_followup = (
        question is not None
        and not planned_now.fresh
        and not planned_now.single_session
        and settings.followup_sessions_max >= 1
    )
    second: dict[str, Any]
    if answered and asks_followup:
        assert question is not None
        facts = versions.session_facts(store, planned_now.identity) or {}
        if "followup" in facts:
            # a kept update: what its session revealed was decided and filed
            # then, and nothing is left to ask
            second = {
                "followup_owners": len(facts["followup"]),
                "followup_estimate": 0.0,
            }
        else:
            held = RunFile(
                store.run_directory,
                "update",
                document_hash(document),
                made_from(settings),
            )
            try:
                raw = text_of(answers[question.key], held, store.run_directory)
            except develop.DevelopError:
                raise UsageError(
                    f"the update's session answer {question.key[:12]} is filed, "
                    "but neither its run holds its sheet nor its step what it "
                    "revealed: run the update again to resume it"
                ) from None
            follow = followup(planned_now, question, raw, settings.reverse_prefilter)
            exact = 0.0
            if follow.owners:
                then = followup_question(document, settings, planned_now, follow, raw)
                exact = (
                    priced(Stage.EDGES, [then], UPDATE_EDGES_ATTEMPTS_PRICED)
                    * UPDATE_EDGES_ATTEMPTS_PRICED
                )
            second = {
                "followup_owners": len(follow.owners),
                "followup_estimate": round(exact, 4),
            }
    else:
        second = {"followup_if_it_fires": round(cost if asks_followup else 0.0, 4)}
        if asks_followup:
            assert question is not None
            counted(question.template.name, cost)
    reread = asks.reread_priced(store, started, rereads_with)
    searched = asks.support_priced(store, started, support_with)
    placed = asks.place_priced(store, started, place_with)
    circled = asks.circles_priced(store, started, circles_with)
    vocabulary = _vocabulary_priced(mapped, agents, answers)
    # the queues are priced by what their tasks cost, as they are expected to
    for queued in (
        reread.get("reread_if_it_fires"),
        searched.get("support_if_it_fires"),
        placed.get("place_if_it_fires"),
        circled.get("circles_if_it_fires"),
    ):
        round_price[0] += queued or 0.0
        round_price[1] += queued or 0.0
    if vocabulary:
        assert mapped is not None
        counted(mapped.questions[0].template.name, vocabulary["vocabulary"]["estimate"])
    return {
        "dry_run": True,
        **({"from_state": started.id} if started is not None else {}),
        "profile": profile.name,
        "mode": settings.mode.value,
        "shape": "single-session" if planned_now.single_session else "per-stage",
        "seats": {stage.value: seating.described(a) for stage, a in agents.items()},
        "steps": {stage.value: found for stage, found in stepped.items()},
        "steps_estimate": round(
            sum(found["estimate"] for found in stepped.values()), 4
        ),
        # the plan as it stands: on the cut the answered steps left
        "session_1_cut": "stepped" if steps.corrections else "rebased",
        "questions": 0 if question is None else 1,
        "answered": int(answered),
        "fresh": planned_now.fresh,
        "reopened": len(planned_now.reopened),
        "conditional": len(planned_now.conditional),
        **_lost_in_rebase(planned_now.raw),
        "prompt_chars": 0 if question is None or single else shown(question),
        "estimate": round(cost, 4),
        "attempt_estimate": round(attempt, 4),
        "attempts_priced": 1 if single else UPDATE_EDGES_ATTEMPTS_PRICED,
        **({"tasks": tasks} if single else {}),
        **second,
        **reread,
        **searched,
        **placed,
        **circled,
        **vocabulary,
        # each part as it would fire, and that by what like runs cost
        "round_estimate": round(round_price[0], 4),
        "round_expected": round(round_price[1], 4),
        "expected_by": seating.like_words(like),
    }


def _update_dry_run_lines(dry: Mapping[str, Any]) -> str:
    """An update's dry run as a person reads it: the steps' questions, the
    session's, each part that asks only if it fires, then the round's
    estimate and what like runs make of it."""
    since = f", from state {dry['from_state']}" if "from_state" in dry else ""
    lines = [f"priced, nothing asked (profile {dry['profile']}{since}):"]
    for stage, found in dry["steps"].items():
        lines.append(
            f"  step {stage}: {questions_words(found)}, {dollars(found['estimate'])}"
        )
    if dry["questions"]:
        session = questions_words(dry)
        if "tasks" in dry:
            priced = f"{dry['tasks']} tasks"
        else:
            priced = f"{dry['attempts_priced']} attempts priced"
        lines.append(f"  session: {session}, {dollars(dry['estimate'])} ({priced})")
    else:
        lines.append("  session: none to ask")
    if "followup_if_it_fires" in dry:
        lines.append(
            "  the follow-up session, if it fires: "
            + dollars(dry["followup_if_it_fires"])
        )
    elif "followup_owners" in dry:
        lines.append(
            f"  the follow-up session: {dry['followup_owners']} blocks, "
            + dollars(dry["followup_estimate"])
        )
    for name in FOLLOW_UPS:
        if f"{name}_if_it_fires" not in dry:
            continue
        price = dry[f"{name}_if_it_fires"]
        words = dry[f"{name}_not_priced"] if price is None else dollars(price)
        lines.append(f"  {name}, if it fires: {words}")
    if "vocabulary" in dry:
        found = dry["vocabulary"]
        lines.append(
            f"  the vocabulary map: {len(found['unanswered'])} questions, "
            f"{dollars(found['estimate'])} (the rest priced on the new text read "
            "without its map)"
        )
    lost = [
        f"{len(dry[key])} {words}"
        for key, words in (
            ("corrections_lost", "corrected boundaries"),
            ("groups_lost", "groups"),
        )
        if dry.get(key)
    ]
    if lost:
        lines.append(f"  the rebase could not carry: {', '.join(lost)}")
    lines.append(
        f"estimate {dollars(dry['round_estimate'])}, expected "
        f"{dollars(dry['round_expected'])}: {dry['expected_by']}"
    )
    return "\n".join(lines)


def _lost_in_rebase(raw: Mapping[str, Any]) -> dict[str, Any]:
    """An update summary's `corrections_lost` and `groups_lost`, each where
    its identity answer lists any: OLD's corrected boundaries and groups
    the rebase could not carry."""
    return {k: raw[k] for k in ("corrections_lost", "groups_lost") if raw.get(k)}


def _vocabulary_priced(
    mapped: Mapped | None, agents: dict[Stage, Agent], answers: Mapping[str, Any]
) -> dict[str, Any]:
    """The vocabulary map's first question, where it is unanswered, and its
    price; the second round, asked only on a `none`, is not priced."""
    if mapped is None or mapped.vocabulary is not None:
        return {}
    unanswered = [q for q in mapped.questions if q.key not in answers]
    rates = agents[Stage.VOCABULARY].rates
    assert rates is not None
    return {
        "vocabulary": {
            "unanswered": [q.key for q in unanswered],
            "estimate": round(
                sum(
                    estimate(
                        q.prompt,
                        rates,
                        q.workspace,
                        seating.harness_tokens(agents[Stage.VOCABULARY]),
                    )
                    for q in unanswered
                ),
                4,
            ),
            "note": "the rest is priced on NEW read without its map",
        }
    }


def _update_command(commands: Any) -> None:
    _update_arguments(
        commands.add_parser(
            "update",
            help="carry a paper's graph onto its edited text, putting to the "
            "agents only what the edit touched",
        )
    )


def _update_arguments(update: argparse.ArgumentParser) -> None:
    """`update`'s arguments: the version, its store and where the identity
    answer comes from, then `build`'s profile, host, budget, rates,
    timeout, dry-run and output options."""
    paper_arguments(update)
    update.add_argument("--store", type=Path, help=STORE_HELP)
    update.add_argument(
        "--state",
        help="the kept state to update from, by its id or a prefix of it "
        "(default: the latest)",
    )
    update.add_argument(
        "--from",
        dest="old",
        type=Path,
        metavar="OLD",
        help="update from another copy of the paper, OLD's directory, in place "
        "of a kept state (with --cut)",
    )
    update.add_argument(
        "--cut",
        type=Path,
        help="with --from: the graph OLD's build exported (`build -o`)",
    )
    update.add_argument(
        "--identity",
        help="where updates from two versions led here, the identity answer to "
        "update from (the refusal names them)",
    )
    # the cutter's display rule, the rebased cut's boundaries held, and the
    # build run as an update: a contributor's tools
    update.add_argument(
        "--displays",
        choices=[rule.value for rule in DisplayRule],
        default=DisplayRule.IN_PARAGRAPH.value,
        help=argparse.SUPPRESS,
    )
    update.add_argument("--hold-cut", action="store_true", help=argparse.SUPPRESS)
    update.add_argument("--from-nothing", action="store_true", help=argparse.SUPPRESS)
    seating.asking_arguments(
        update, "every stage the update asks", "the update", profiled_by_default=True
    )
    no_reread_argument(update)
    no_search_argument(update)
    no_place_argument(update)
    no_circles_argument(update)
    update.add_argument("--dry-run", action="store_true", help=DRY_RUN_HELP)
    update.add_argument("--json", action="store_true", help=SUMMARY_JSON_HELP)
    prompts_argument(update)
    output_argument(update)


def no_reread_argument(command: argparse.ArgumentParser) -> None:
    command.add_argument(
        "--no-reread",
        action="store_true",
        help="skip the agent's reread of the lines the edit marked stale, where "
        "the profile asks it (`reread --ask` asks it later)",
    )


#: The arguments of the commands of an update, by its command.
ARGUMENTS: dict[str, Callable[[Any], None]] = {
    "diff": _diff_arguments,
    "update": _update_command,
}
