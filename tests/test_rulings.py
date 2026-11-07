"""Rulings: whether the paper owes an argument for a claim it asserts and
argues nothing for (docs/model.md § 8), and what they do to
`unsupported-claims` (§ 11) and nothing else.

On `test_check`'s synthetic paper: `c`, `x` and `y` stand unsupported, `s`
uses `c`; and on `test_judgments`' four-block paper, its remark `r` made
unsupported, for a ruling walked across an update. No model is called.
"""

from __future__ import annotations

import json

import pytest

import record_file
from axiomation import judgments, positioning, queries, rulings
from axiomation.check import Gate
from axiomation.cli import main
from axiomation.judgments import Author, JudgmentError, seen
from axiomation.queries import View
from axiomation.questions import Validation
from axiomation.questions import review_file as rf
from axiomation.records import (
    JUDGMENT,
    Aspect,
    JudgmentRecords,
    RecordError,
    RecordKind,
    RecordStore,
    RulingKind,
    Subject,
    Verdict,
)
from axiomation.resolver import Resolver
from test_check import (
    AGENT,
    OTHER,
    PERSON,
    TEXT,
    built,
    judged,
    listed,
    payload,
    results,
)
from test_check import edge as check_edge
from test_judgments import AFTER, BEFORE, EDGES, graph, reviewed, same, updated
from test_judgments import BLOCKS as FOUR
from test_judgments import TEXT as FOUR_TEXT
from test_versions import V1, keep

KNOWN, OWED, INTERPRETATION = (
    RulingKind.KNOWN,
    RulingKind.OWED,
    RulingKind.INTERPRETATION,
)


def ruled(
    store, kept, on, kind, author=AGENT, reason="a reason", claim=None, severity=None
):
    """A ruling filed; an owed one weighed 2 with a fix unless `severity`
    says otherwise."""
    at = seen(store, kept)
    owed = kind is RulingKind.OWED
    return rulings.rule(
        store,
        kept,
        rulings.subject(at, on, claim),
        kind,
        reason,
        author,
        severity=(severity or 2) if owed else None,
        fix="argue it" if owed else None,
    ).records.rulings[0]


def gaps(store, kept):
    return results(store, kept)[Gate.UNSUPPORTED_CLAIMS]


# --- filing ------------------------------------------------------------------------


def test_a_ruling_is_on_a_claim_standing_unsupported_and_gives_its_reason(
    tmp_path,
) -> None:
    store, kept = built(tmp_path)
    with pytest.raises(JudgmentError, match="stands justified: a ruling is on"):
        ruled(store, kept, "t", KNOWN)
    with pytest.raises(JudgmentError, match="stands conjectured"):
        ruled(store, kept, "k", KNOWN)
    with pytest.raises(JudgmentError, match="gives its reason"):
        ruled(store, kept, "x", KNOWN, reason="  ")
    record = ruled(store, kept, "x", KNOWN, reason="standard")
    assert (record.subject.words, record.kind) == ("Clearly f is extra.", KNOWN)


# --- the gate ----------------------------------------------------------------------


def test_an_agent_s_known_clears_and_is_counted_for_the_audit(tmp_path) -> None:
    """`x` ruled known by an agent leaves the gate; the gate's result
    holds it, its ruling's author and reason, for the audit; `y`, ruled
    owed, stays."""
    store, kept = built(tmp_path)
    judged(store, kept, Aspect.TRUE, "x", Verdict.VERIFIED)
    ruled(store, kept, "x", KNOWN, reason="a textbook fact")
    ruled(store, kept, "y", OWED)
    found = gaps(store, kept)
    # `y`, owed, weighs most: listed first
    assert listed(found) == ["y", "c", "n"]
    (cleared,) = found.cleared
    assert cleared.data["handle"] == "x"
    assert cleared.line.endswith("ruled known")
    (speaks,) = cleared.data["cause"]["ruling"]["speaks"]
    assert (speaks["author"], speaks["reason"]) == ("agent model-a", "a textbook fact")
    assert cleared.data["cause"]["ruling"]["by_person"] is False


def test_a_ruling_never_establishes(tmp_path) -> None:
    """`c` ruled known by a person leaves the gate, yet stays
    unestablished, its `true` unjudged; and `r`, resting on it, is still
    listed nowhere: its reason sits upstream, and `c`'s `true` is now
    unread."""
    store, kept = built(tmp_path)
    ruled(store, kept, "c", KNOWN, PERSON)
    view = View(store, kept)
    (c,) = [e for e in view.established if e.claim.words == "Surely f is finite."]
    assert c.reason is not None and c.reason.kind.value == "unsupported"
    found = results(store, kept)
    assert "c" not in listed(found[Gate.UNSUPPORTED_CLAIMS])
    unread = [i.line for i in found[Gate.UNREVIEWED].items]
    assert any(line.startswith('c  true · "Surely f is finite."') for line in unread)


def test_any_agent_s_owed_wins_and_an_author_s_later_ruling_replaces_its_own(
    tmp_path,
) -> None:
    store, kept = built(tmp_path)
    judged(store, kept, Aspect.TRUE, "x", Verdict.VERIFIED)
    ruled(store, kept, "x", KNOWN, AGENT)
    ruled(store, kept, "x", OWED, OTHER)
    assert "x" in listed(gaps(store, kept))
    ruled(store, kept, "x", KNOWN, OTHER)
    assert "x" not in listed(gaps(store, kept))
    ruled(store, kept, "x", OWED, AGENT)
    assert "x" in listed(gaps(store, kept))


def test_the_same_reader_under_an_upgraded_host_is_one_author(tmp_path) -> None:
    """A host's name carries its version: the reading and the ruling a
    reader files under `2.1.310` replace its own under `2.1.309`, never
    speak beside them; the name keeps the version."""
    store, kept = built(tmp_path)
    old = Author("claude-code 2.1.309", "model-a", "s1")
    new = Author("claude-code 2.1.310", "model-a", "s2")
    judged(store, kept, Aspect.TRUE, "x", Verdict.CONTRADICTED, old)
    ruled(store, kept, "x", OWED, old)
    judged(store, kept, Aspect.TRUE, "x", Verdict.VERIFIED, new)
    ruled(store, kept, "x", KNOWN, new)
    assert "x" not in listed(gaps(store, kept))
    view = View(store, kept)
    (r,) = view.rulings.values()
    assert [s.author.name for s in r.speaks] == ["claude-code 2.1.310 model-a"]
    (status,) = [
        s
        for s in view.readings.statuses
        if s.aspect is Aspect.TRUE and view.readings.at.handle(s.subject.node) == "x"
    ]
    assert status.status is judgments.StatusKind.HELD
    assert [r.judgment.verdict for r in status.reads] == [Verdict.VERIFIED]


def test_a_person_s_ruling_stands_over_every_agent_s(tmp_path) -> None:
    """A person's `owed` keeps `x` listed though an agent later rules it
    known; a person's `known` clears `y` though an agent later rules it
    owed."""
    store, kept = built(tmp_path)
    ruled(store, kept, "x", KNOWN, AGENT)
    ruled(store, kept, "x", OWED, PERSON)
    ruled(store, kept, "x", KNOWN, OTHER)
    ruled(store, kept, "y", KNOWN, PERSON)
    ruled(store, kept, "y", OWED, AGENT)
    found = gaps(store, kept)
    assert "x" in listed(found)
    assert [i.data["handle"] for i in found.cleared] == ["y"]
    assert found.cleared[0].data["cause"]["ruling"]["by_person"] is True


def test_agents_known_needs_a_true_held_verified(tmp_path) -> None:
    """An agent's `known` on `x`, which no reading showed holds, leaves it
    listed, saying why; a verified `true` beside it clears it; a person's
    `known` needs none."""
    store, kept = built(tmp_path)
    ruled(store, kept, "x", KNOWN)
    ruled(store, kept, "y", KNOWN, PERSON)
    found = gaps(store, kept)
    (item,) = [i for i in found.items if i.data["handle"] == "x"]
    assert "(ruled known by agents, but its `true` is not held verified)" in item.line
    assert [i.data["handle"] for i in found.cleared] == ["y"]
    judged(store, kept, Aspect.TRUE, "x", Verdict.VERIFIED)
    assert "x" not in listed(gaps(store, kept))


def test_agents_rulings_never_clear_a_claim_whose_true_they_dispute(
    tmp_path,
) -> None:
    """Two agents differ on `x`'s `true`: no agent's ruling takes it out,
    an interpretation no more than known; a person's does. Disputed, it is
    `unreviewed`'s, a person's to judge first, and the gate counts it
    there (`docs/model.md` § 11)."""
    store, kept = built(tmp_path)
    judged(store, kept, Aspect.TRUE, "x", Verdict.NOT_SHOWN, AGENT)
    judged(store, kept, Aspect.TRUE, "x", Verdict.VERIFIED, OTHER)
    ruled(store, kept, "x", KNOWN, OTHER)
    found = gaps(store, kept)
    assert "x" not in listed(found) and found.to_unreviewed == 1
    disputed = results(store, kept)[Gate.UNREVIEWED]
    assert "x" in [
        i.data["handle"] for i in disputed.items if i.data["kind"] == "disputed"
    ]
    view = View(store, kept)
    (x,) = [
        support
        for s, support in zip(view.standings, view.support, strict=True)
        if view.handle(s["block"]) == "x"
    ]
    assert x.guard is rulings.Guard.TRUE_DISPUTED
    ruled(store, kept, "x", INTERPRETATION, OTHER)
    assert "x" not in [i.data["handle"] for i in gaps(store, kept).cleared]
    ruled(store, kept, "x", KNOWN, PERSON)
    assert "x" in [i.data["handle"] for i in gaps(store, kept).cleared]


def test_a_true_held_verified_and_a_ruling_owed_stays_listed(tmp_path) -> None:
    """The reader showed `x` holds, and the paper owes its argument: a
    gap, established."""
    store, kept = built(tmp_path)
    judged(store, kept, Aspect.TRUE, "x", Verdict.VERIFIED)
    ruled(store, kept, "x", OWED, reason="a result of the paper's own")
    (item,) = [i for i in gaps(store, kept).items if i.data["handle"] == "x"]
    assert item.line.endswith(
        "a result of the paper's own; fix: argue it  · `true` held verified"
    )


def test_an_interpretation_something_relies_on_stays_listed(tmp_path) -> None:
    """`s` uses `c`: ruled an interpretation, `c` stays, naming `s`;
    ruled known, it leaves. A claim whose block qualifies another is
    relied on too."""
    store, kept = built(tmp_path)
    ruled(store, kept, "c", INTERPRETATION)
    (item,) = [i for i in gaps(store, kept).items if i.data["handle"] == "c"]
    assert "(ruled an interpretation, yet s rely on it)" in item.line
    assert "an interpretation is no premise" in item.data["clears"]
    ruled(store, kept, "c", KNOWN, PERSON)
    assert "c" not in listed(gaps(store, kept))

    for kind in ("qualifies", "justifies"):
        data = payload()
        data["edges"].append(check_edge("x", kind, "t"))
        store = RecordStore(tmp_path / kind)
        kept = keep(store, V1, TEXT, data)
        ruled(store, kept, "x", INTERPRETATION)
        assert "x" in listed(gaps(store, kept)), kind
    # a uses edge naming no claim of a block holding two relies on both
    data = payload()
    for s in data["standings"]:
        if s["block"] == "y":
            twin = {**s, "claim": {**s["claim"], "text": "f is young."}}
    data["standings"].append(twin)
    data["edges"].append(check_edge("t", "uses", "y"))
    store = RecordStore(tmp_path / "fallback")
    kept = keep(store, V1, TEXT, data)
    ruled(store, kept, "y", INTERPRETATION, claim="Plainly f is young.")
    (item,) = [
        i
        for i in gaps(store, kept).items
        if i.data["anchor"]["words"] == "Plainly f is young."
    ]
    assert "(ruled an interpretation, yet t rely on it)" in item.line


def test_a_claim_the_paper_promises_to_argue_is_never_cleared(tmp_path) -> None:
    data = payload()
    for s in data["standings"]:
        if s["block"] == "x":
            s["declared"] = "promised"
    store = RecordStore(tmp_path / "store")
    kept = keep(store, V1, TEXT, data)
    ruled(store, kept, "x", KNOWN)
    (item,) = [i for i in gaps(store, kept).items if i.data["handle"] == "x"]
    assert "the paper promises its argument" in item.line


# --- across states -----------------------------------------------------------------


def _four(tmp_path):
    """`test_judgments`' paper, its remark `r` a claim standing
    unsupported."""
    store = RecordStore(tmp_path / "store")
    data = graph(standings=[("r", "The theorem is sharp.")])
    data["standings"][0]["status"] = "unsupported"
    return store, keep(store, V1, FOUR_TEXT, data)


def _updated(store, first, r_text):
    text = FOUR_TEXT.replace("The theorem is sharp.", r_text)
    blocks = [*FOUR[:3], ("r", r_text)]
    data = graph(text, blocks, standings=[("r", r_text)])
    data["standings"][0]["status"] = "unsupported"
    changed = {} if r_text == "The theorem is sharp." else {"r": "changed"}
    return updated(store, first, text, data, same("d", "t", "p", "r", but=changed))


def test_a_ruling_lapses_only_when_its_claim_s_words_change_or_end(
    tmp_path,
) -> None:
    store, first = _four(tmp_path)
    ruled(store, first, "r", KNOWN)
    kept = _updated(store, first, "The theorem is sharp.")
    (live,) = View(store, kept).rulings.values()
    assert live.clears is KNOWN
    store2, first2 = _four(tmp_path / "two")
    ruled(store2, first2, "r", KNOWN)
    reworded = _updated(store2, first2, "The theorem is tight.")
    (gone,) = View(store2, reworded).rulings.values()
    assert gone.clears is None and not gone.speaks
    assert gone.reads[0].walked.causes[0].why == (
        'the claim ruled on, "The theorem is sharp.", now reads "The theorem is tight."'
    )


def test_a_ruling_lapses_when_its_block_is_deleted(tmp_path) -> None:
    store, first = _four(tmp_path)
    ruled(store, first, "r", KNOWN, PERSON)
    text = FOUR_TEXT.replace(" The theorem is sharp.", "")
    data = graph(text, FOUR[:3], EDGES[:3], standings=[])
    kept = updated(store, first, text, data, same("d", "t", "p"))
    (gone,) = View(store, kept).rulings.values()
    assert not gone.speaks and gone.reads[0].walked.causes[0].kind.value == "deleted"


# --- reading them ----------------------------------------------------------------


def test_show_and_open_name_each_claim_s_ruling(tmp_path) -> None:
    store, kept = built(tmp_path)
    ruled(store, kept, "x", OWED, AGENT, reason="the paper's own")
    ruled(store, kept, "x", KNOWN, PERSON, reason="a textbook fact")
    view = View(store, kept)
    lines = [i.line for i in queries.show(view, view.resolve("x")).sections["rulings"]]
    assert [line.split(": ")[0] for line in lines] == [
        '"Clearly f is extra."  owed 2 by agent model-a (replaced)',
        '"Clearly f is extra."  known by person (speaks)',
    ]
    opened = queries.open_report(view).sections["unsupported"]
    # the person's `known` speaks: no argument is owed, so nothing is open
    assert [i for i in opened if i.data["block"] == "x"] == []
    ruled(store, kept, "y", OWED, AGENT, reason="the paper's own result")
    view = View(store, kept)
    opened = queries.open_report(view).sections["unsupported"]
    (y,) = [i for i in opened if i.data["block"] == "y"]
    assert y.line.endswith(
        "owed 2 by agent model-a: the paper's own result; fix: argue it  · `true` "
        "unjudged"
    )


def test_the_rule_command_files_and_check_counts_what_rulings_cleared(
    tmp_path, capsys
) -> None:
    store, kept = built(tmp_path)
    judged(store, kept, Aspect.TRUE, "y", Verdict.VERIFIED)
    paper = tmp_path / "paper"
    paper.mkdir()
    base = [str(paper), "--store", str(store.directory)]
    assert (
        main(["rule", *base, "--on", "x", "--ruling", "known", "--reason", "a fact"])
        == 0
    )
    assert '"Clearly f is extra." known' in capsys.readouterr().out
    assert (
        main(
            ["rule", *base, "--on", "y", "--ruling", "known", "--reason", "plain"]
            + ["--agent", "model-a", "--agent-session", "s1"]
        )
        == 0
    )
    capsys.readouterr()
    main(["check", *base])
    out = capsys.readouterr().out
    assert (
        "unsupported-claims: 2 claims no argument owed, by rulings, 1 of them by "
        "agents alone (listed last)" in out
    )
    assert "; not counted: 2 no argument owed (rulings), 0 defect (in defects)" in out
    # the audit's read: each cleared claim last, with who spoke and why
    audit = out[out.index("unsupported-claims: no argument owed") :]
    assert "no argument owed: ruled known, by person: a fact" in audit
    assert "no argument owed: ruled known, by agent model-a: plain" in audit
    main(["check", *base, "--json"])
    data = json.loads(capsys.readouterr().out)
    cleared = data["gates"][0]["cleared"]
    assert [i["handle"] for i in cleared] == ["x", "y"]
    assert main(["rule", *base, "--on", "t", "--ruling", "known", "--reason", "r"]) != 0
    assert "a ruling is on a claim standing unsupported" in capsys.readouterr().err


def test_an_author_is_whoever_filed(tmp_path) -> None:
    store, kept = built(tmp_path)
    ruled(store, kept, "x", KNOWN, Author("agent", "m", "s9"))
    (r,) = View(store, kept).rulings.values()
    assert r.speaks[0].author.name == "agent m"


# --- immediate, and a withdrawal --------------------------------------------------


def test_immediate_clears_only_while_its_block_uses_a_result(tmp_path) -> None:
    """`x` ruled immediate by a person stays listed while its block uses
    nothing; with its uses edge onto the lemma `l`, it leaves."""
    store, kept = built(tmp_path)
    ruled(store, kept, "x", RulingKind.IMMEDIATE, PERSON, reason="by the lemma")
    (item,) = [i for i in gaps(store, kept).items if i.data["handle"] == "x"]
    assert "(ruled immediate, but its block uses no result)" in item.line
    data = payload()
    data["edges"].append(check_edge("x", "uses", "l"))
    store = RecordStore(tmp_path / "uses")
    kept = keep(store, V1, TEXT, data)
    ruled(store, kept, "x", RulingKind.IMMEDIATE, PERSON, reason="by the lemma")
    assert "x" not in listed(gaps(store, kept))


def test_a_withdrawn_ruling_speaks_no_more_and_is_asked_again(tmp_path) -> None:
    """An agent's known on `x` withdrawn by a person: `x` is listed again,
    no ruling speaks, and `show` says why; an agent may not withdraw."""
    store, kept = built(tmp_path)
    judged(store, kept, Aspect.TRUE, "x", Verdict.VERIFIED)
    record = ruled(store, kept, "x", KNOWN)
    assert "x" not in listed(gaps(store, kept))
    with pytest.raises(JudgmentError, match="an agent rules again"):
        rulings.withdraw(store, record.id, "wrong", AGENT)
    rulings.withdraw(store, record.id[:8], "the paper's own rule, not the field's")
    with pytest.raises(JudgmentError, match="withdrawn already"):
        rulings.withdraw(store, record.id, "again")
    assert "x" in listed(gaps(store, kept))
    view = View(store, kept)
    (ruled_x,) = view.rulings.values()
    assert not ruled_x.speaks and not ruled_x.by_person
    (line,) = [i.line for i in queries.show(view, "x").sections["rulings"]]
    assert "(withdrawn: the paper's own rule, not the field's)" in line


def test_a_withdrawal_never_hands_the_claim_back_to_an_earlier_ruling(
    tmp_path,
) -> None:
    """A person's owed on `x`, then their known; the known withdrawn: the
    earlier owed speaks no more in its place, and the claim is asked
    again."""
    store, kept = built(tmp_path)
    ruled(store, kept, "x", OWED, PERSON)
    known = ruled(store, kept, "x", KNOWN, PERSON)
    rulings.withdraw(store, known.id, "read the page again")
    (ruled_x,) = View(store, kept).rulings.values()
    assert not ruled_x.speaks and ruled_x.asked_again


def test_rule_prints_what_it_did_to_the_gates(tmp_path, capsys) -> None:
    store, kept = built(tmp_path)
    paper = tmp_path / "paper"
    paper.mkdir()
    base = [str(paper), "--store", str(store.directory)]
    main(["rule", *base, "--on", "x", "--ruling", "known", "--reason", "a fact"])
    out = capsys.readouterr().out
    assert "unsupported-claims −1 +0" in out
    (ruling,) = store.judgment_records().rulings
    main(["rule", *base, "--withdraw", ruling.id, "--reason", "asked again"])
    out = capsys.readouterr().out
    assert f"ruling {ruling.id} withdrawn by person" in out
    assert "unsupported-claims −0 +1" in out
    main(
        [
            "rule",
            *base,
            "--on",
            "y",
            "--ruling",
            "owed",
            "--severity",
            "2",
            "--fix",
            "argue it",
            "--reason",
            "its own",
        ]
    )
    assert "gates: unchanged" in capsys.readouterr().out


# --- a claim's support --------------------------------------------------------------


def support_of(store, kept, handle):
    view = View(store, kept)
    return [
        support.status
        for s, support in zip(view.standings, view.support, strict=True)
        if view.handle(s["block"]) == handle
    ]


def test_a_claim_s_support_is_what_the_gate_reads(tmp_path) -> None:
    """`x` ruled known beside a verified `true`: no argument owed, and
    listed apart; `y` ruled owed and `c` unruled: unsupported, listed;
    `n`, justified with no justifies edge: unsupported by its own gap;
    `t`, justified by its proof: supported. `check`, `open` and `show`
    read the one answer."""
    store, kept = built(tmp_path)
    judged(store, kept, Aspect.TRUE, "x", Verdict.VERIFIED)
    ruled(store, kept, "x", KNOWN)
    ruled(store, kept, "y", OWED)
    S = rulings.Support
    assert support_of(store, kept, "x") == [S.NO_ARGUMENT_OWED]
    assert (
        support_of(store, kept, "y") == support_of(store, kept, "c") == [S.UNSUPPORTED]
    )
    assert support_of(store, kept, "n") == [S.UNSUPPORTED]
    assert support_of(store, kept, "t") == [S.SUPPORTED]
    found = gaps(store, kept)
    view = View(store, kept)
    opened = queries.open_report(view).sections["unsupported"]
    # `y`, owed, weighs most: listed first
    assert [i.data["handle"] for i in opened] == listed(found) == ["y", "c", "n"]
    (shown,) = queries.show(view, view.resolve("x")).sections["standings"]
    support = shown.data["support"]
    assert (support["status"], support["ruling"], support["by_person"]) == (
        "no-argument-owed",
        "known",
        False,
    )
    assert [s["author"] for s in support["speaks"]] == ["agent model-a"]


def test_a_guard_is_the_same_words_in_check_and_open(tmp_path) -> None:
    """An agent's `known` beside no held verified `true` clears nothing:
    the claim is unsupported, and `open` and `check` name the guard and
    what clears it alike."""
    store, kept = built(tmp_path)
    ruled(store, kept, "x", KNOWN)
    view = View(store, kept)
    (support,) = [
        sp
        for s, sp in zip(view.standings, view.support, strict=True)
        if view.handle(s["block"]) == "x"
    ]
    assert support.guard is rulings.Guard.TRUE_NOT_HELD
    (in_gate,) = [i for i in gaps(store, kept).items if i.data["handle"] == "x"]
    (in_open,) = [
        i
        for i in queries.open_report(view).sections["unsupported"]
        if i.data["handle"] == "x"
    ]
    note = "(ruled known by agents, but its `true` is not held verified)"
    assert note in in_gate.line and note in in_open.line
    assert in_gate.data["clears"] == in_open.data["clears"]


def test_a_block_using_a_claim_no_argument_is_owed_for_rests_on_no_gap(
    tmp_path,
) -> None:
    """`s` uses `c`: listed under `uses-unsupported` and by `blocks
    --uses-unsupported` until a person rules `c` known."""
    store, kept = built(tmp_path)
    view = View(store, kept)
    users = queries.open_report(view).sections["uses_unsupported"]
    assert "s" in [i.data["handle"] for i in users]
    assert "s" in [
        view.handle(b)
        for b in queries.matching(view, queries.Filter(uses_unsupported=True))
    ]
    ruled(store, kept, "c", KNOWN, PERSON)
    view = View(store, kept)
    users = queries.open_report(view).sections["uses_unsupported"]
    assert "s" not in [i.data["handle"] for i in users]
    assert "s" not in [
        view.handle(b)
        for b in queries.matching(view, queries.Filter(uses_unsupported=True))
    ]


def test_a_claim_cleared_and_read_negative_is_cleared_and_its_reading_a_defect(
    tmp_path,
) -> None:
    """A person rules `x` known; an agent finds it not shown. The ruling
    speaks, so `x` is no argument owed, listed apart and in neither
    `open`'s unsupported nor the gate's split to `defects`, whose reading
    stands on its own: `open` is still the gate's items and its split."""
    store, kept = built(tmp_path)
    ruled(store, kept, "x", KNOWN, PERSON)
    judged(store, kept, Aspect.TRUE, "x", Verdict.NOT_SHOWN)
    found = gaps(store, kept)
    assert [i.data["handle"] for i in found.cleared] == ["x"]
    assert found.to_defects == 0
    opened = queries.open_report(View(store, kept)).sections["unsupported"]
    assert "x" not in [i.data["handle"] for i in opened]
    assert len(opened) == len(found.items) + found.to_defects
    defects = results(store, kept)[Gate.DEFECTS]
    assert "x" in [i.data["handle"] for i in defects.items]


def test_a_ruled_circle_stays_unsupported_by_its_circle(tmp_path) -> None:
    """`x` and `y` stand unsupported and use each other; a person rules `x`
    known: no ruling clears a circle, and `check` and `open` both list it,
    by its circle, with what breaks it."""
    store = RecordStore(tmp_path / "store")
    data = payload()
    data["edges"] += [check_edge("x", "uses", "y"), check_edge("y", "uses", "x")]
    kept = keep(store, V1, TEXT, data)
    judged(store, kept, Aspect.TRUE, "x", Verdict.VERIFIED, PERSON)
    judged(store, kept, Aspect.TRUE, "y", Verdict.VERIFIED, PERSON)
    ruled(store, kept, "x", KNOWN, PERSON)
    assert support_of(store, kept, "x") == [rulings.Support.UNSUPPORTED]
    found = gaps(store, kept)
    assert found.cleared == ()
    (in_gate,) = [i for i in found.items if i.data["handle"] == "x"]
    sections = queries.open_report(View(store, kept)).sections
    assert [i.data["handle"] for i in sections["unsupported"]].count("x") == 0
    (in_open,) = [i for i in sections["circle"] if i.data["handle"] == "x"]
    assert in_gate.data["kind"] == "circular" and in_open.data["status"] == "circular"
    assert in_gate.data["clears"] == in_open.data["clears"] == queries.CIRCLE_CLEARS


# --- left unargued: the author's own disposition -----------------------------------

LEFT = RulingKind.LEFT_UNARGUED


def test_left_unargued_is_a_person_s_alone(tmp_path) -> None:
    store, kept = built(tmp_path)
    with pytest.raises(JudgmentError, match="left-unargued is the author's"):
        ruled(store, kept, "x", LEFT, AGENT)
    assert store.judgment_records().rulings == ()


def test_a_claim_left_unargued_leaves_the_count_and_is_listed_apart(
    tmp_path,
) -> None:
    """A person leaves `x` unargued over an agent's `owed`: out of the
    gate's count, neither cleared nor supported, listed with its reason;
    its `true`, unread, still `unreviewed`'s."""
    store, kept = built(tmp_path)
    ruled(store, kept, "x", OWED, AGENT)
    ruled(store, kept, "x", LEFT, PERSON, reason="a routine step, kept short")
    assert support_of(store, kept, "x") == [rulings.Support.LEFT_UNARGUED]
    found = gaps(store, kept)
    assert "x" not in listed(found)
    assert found.cleared == ()
    (left,) = found.left
    assert left.data["handle"] == "x"
    assert left.line.endswith("left unargued by the author")
    (speaks,) = left.data["cause"]["ruling"]["speaks"]
    assert speaks["reason"] == "a routine step, kept short"
    unread = [i.line for i in results(store, kept)[Gate.UNREVIEWED].items]
    assert any(line.startswith('x  true · "Clearly f is extra."') for line in unread)
    opened = queries.open_report(View(store, kept)).sections["unsupported"]
    assert "x" not in [i.data["handle"] for i in opened]


def test_left_unargued_never_stands_where_something_relies_on_the_claim(
    tmp_path,
) -> None:
    """`s` uses `c`: left unargued, `c` stays listed, naming `s`; and a
    claim the paper promises to argue is never left."""
    store, kept = built(tmp_path)
    ruled(store, kept, "c", LEFT, PERSON)
    (item,) = [i for i in gaps(store, kept).items if i.data["handle"] == "c"]
    assert "(left unargued, yet s rely on it)" in item.line
    assert "a claim left unargued is no premise" in item.data["clears"]
    data = payload()
    for s in data["standings"]:
        if s["block"] == "x":
            s["declared"] = "promised"
    store = RecordStore(tmp_path / "promised")
    kept = keep(store, V1, TEXT, data)
    ruled(store, kept, "x", LEFT, PERSON)
    (item,) = [i for i in gaps(store, kept).items if i.data["handle"] == "x"]
    assert "the paper promises its argument" in item.line


def test_check_counts_and_lists_what_the_author_leaves_unargued(
    tmp_path, capsys
) -> None:
    store, kept = built(tmp_path)
    paper = tmp_path / "paper"
    paper.mkdir()
    base = [str(paper), "--store", str(store.directory)]
    left = ["--on", "x", "--ruling", "left-unargued", "--reason", "kept short"]
    agent = ["--agent", "model-a", "--agent-session", "s1"]
    assert main(["rule", *base, *left, *agent]) != 0
    assert "left-unargued is the author's" in capsys.readouterr().err
    assert main(["rule", *base, *left]) == 0
    assert "unsupported-claims −1 +0" in capsys.readouterr().out
    main(["check", *base])
    out = capsys.readouterr().out
    assert (
        "unsupported-claims: 1 claim left unargued by the author (listed last)" in out
    )
    assert "0 no argument owed (rulings), 1 left unargued (the author's)," in out
    shown = out[out.index("unsupported-claims: left unargued") :]
    assert "left unargued by the author, by person: kept short" in shown
    main(["check", *base, "--json"])
    data = json.loads(capsys.readouterr().out)
    assert [i["handle"] for i in data["gates"][0]["left"]] == ["x"]
    assert "cleared" not in data["gates"][0]


# --- a part's line keeps its subject ------------------------------------------------


def test_a_part_s_quote_carries_its_sentence_around_its_words() -> None:
    text = (
        "Earlier work bounded the gain. Those bounds held for small inputs, "
        "but no single bound covers them all. This paper proves one, as shown below."
    )
    words = "but no single bound covers them all."
    assert queries.part_quote(text, words) == (
        '"[Those bounds held for small inputs,] but no single bound covers them all."'
    )
    # words that open their sentence and end inside it: its rest after them
    assert queries.part_quote(text, "This paper proves one") == (
        '"This paper proves one [, as shown below.]"'
    )
    assert queries.part_quote(text, "Earlier work bounded the gain.") == (
        '"Earlier work bounded the gain."'
    )
    # a sentence opened by markup alone has no opening to show
    bounded = "The output is bounded."
    assert queries.part_quote(r"\begin{theorem} " + bounded, bounded) == f'"{bounded}"'
    # a sentence the reader cut at an abbreviation it does not know
    cut = "If $f$ vanishes a.e. near zero, the detail is lost."
    assert queries.part_quote(cut, "the detail is lost.") == (
        '"[If $f$ vanishes a.e. near zero,] the detail is lost."'
    )
    long = "A " + "very " * 30 + "long opening, but the claim."
    quoted = queries.part_quote(long, "but the claim.")
    assert quoted.startswith('"[A very') and "…] but the claim." in quoted
    assert queries.part_quote("Not here.", "absent words") == '"absent words"'
    # words the block breaks across a line
    broken = "Those bounds held for inputs,\nbut no single\n bound covers them."
    assert queries.part_quote(broken, "but no single bound covers") == (
        '"[Those bounds held for inputs,] but no single bound covers [them.]"'
    )
    # a rest that is only its citation says nothing more
    cited = r"Earlier work sought it \textcite{a1969}. Next."
    assert queries.part_quote(cited, "Earlier work sought it") == (
        '"Earlier work sought it"'
    )


# --- positioning: the contribution session's claim -------------------------------

POSITIONING = RulingKind.POSITIONING


def checked(store, kept, on, words, author=AGENT):
    """A contribution check on block `on` quoting `words`, filed as the
    contribution session files its `checked` line."""
    at = seen(store, kept)
    record = positioning.check_record(
        kept.id, Subject(at.node_of[on], None, words), "searched", "nothing close"
    )
    return judgments.file_records(
        store, JUDGMENT, JudgmentRecords(checks=(record,)), author
    ).records.checks[0]


def unchecked(store, kept):
    return [
        i.data["handle"]
        for i in results(store, kept)[Gate.UNREVIEWED].items
        if i.data["kind"] == "unchecked"
    ]


def test_an_agent_s_positioning_clears_with_no_reading_and_never_a_premise(
    tmp_path,
) -> None:
    """An agent rules `x` positioning with no `true` held: no argument owed;
    ruled on `c`, which `s` uses, it stays listed, naming `s`."""
    store, kept = built(tmp_path)
    ruled(store, kept, "x", POSITIONING, reason="that no prior work does it")
    assert support_of(store, kept, "x") == [rulings.Support.NO_ARGUMENT_OWED]
    found = gaps(store, kept)
    assert [i.data["handle"] for i in found.cleared] == ["x"]
    ruled(store, kept, "c", POSITIONING)
    (item,) = [i for i in gaps(store, kept).items if i.data["handle"] == "c"]
    assert "(ruled positioning, yet s rely on it)" in item.line
    assert "a claim the literature settles is no premise" in item.data["clears"]


def test_a_positioning_claim_is_unread_until_a_contribution_check_quotes_it(
    tmp_path,
) -> None:
    """`x` ruled positioning: `unreviewed` lists it unchecked in place of
    its `true`; a check on another block, or quoting a fragment of its
    words, reads nothing; one quoting its words whole does."""
    store, kept = built(tmp_path)
    ruled(store, kept, "x", POSITIONING)
    assert unchecked(store, kept) == ["x"]
    unread = results(store, kept)[Gate.UNREVIEWED].items
    assert not any(i.line.startswith("x  true ·") for i in unread)
    (item,) = [i for i in unread if i.data["handle"] == "x"]
    assert item.line == (
        'x  positioning · "Clearly f is extra."  no contribution check reads it'
    )
    checked(store, kept, "y", "Plainly f is young.")
    assert unchecked(store, kept) == ["x"]
    checked(store, kept, "x", "f is extra")
    assert unchecked(store, kept) == ["x"]
    checked(store, kept, "x", "Clearly f is extra.")
    assert unchecked(store, kept) == []


def test_a_person_checks_a_positioning_claim_by_hand(tmp_path, capsys) -> None:
    """`check-contribution` files a person's check of `x`'s words: `x`,
    ruled positioning, leaves `unreviewed`; a check that says nothing
    searched is refused."""
    store, kept = built(tmp_path)
    paper = tmp_path / "paper"
    paper.mkdir()
    base = [str(paper), "--store", str(store.directory)]
    ruled(store, kept, "x", POSITIONING, PERSON)
    assert unchecked(store, kept) == ["x"]
    command = ["check-contribution", *base, "--on", "x", "--found", "nothing close"]
    assert main([*command, "--searched", " "]) != 0
    assert "what it searched and what it found" in capsys.readouterr().err
    assert main([*command, "--searched", "the field's surveys"]) == 0
    out = capsys.readouterr().out
    assert 'by person: x "Clearly f is extra."' in out
    assert "unreviewed −1 +0" in out
    assert unchecked(store, kept) == []
    (filed,) = store.judgment_records().checks
    assert (filed.searched, filed.found) == ("the field's surveys", "nothing close")


def test_a_check_lapses_when_the_words_it_quotes_change(tmp_path) -> None:
    store, first = _four(tmp_path)
    ruled(store, first, "r", POSITIONING, PERSON)
    checked(store, first, "r", "The theorem is sharp.")
    kept = _updated(store, first, "The theorem is sharp.")
    assert unchecked(store, kept) == []
    store2, first2 = _four(tmp_path / "two")
    checked(store2, first2, "r", "The theorem is sharp.")
    reworded = _updated(store2, first2, "The theorem is tight.")
    ruled(store2, reworded, "r", POSITIONING, PERSON)
    (check,) = View(store2, reworded).checks
    assert not check.live
    assert unchecked(store2, reworded) == ["r"]


# --- a restatement of a conjecture --------------------------------------------------


def _restating(tmp_path, *pairs):
    """`test_check`'s paper with each (`a`, `b`) of `pairs` making `a`
    restated onto `b` (`k` is the conjecture)."""
    data = payload()
    for s in data["standings"]:
        if s["block"] in {a for a, _ in pairs}:
            s["status"] = "restated"
    data["edges"] += [check_edge(a, "about", b) for a, b in pairs]
    store = RecordStore(tmp_path / "store")
    return store, keep(store, V1, TEXT, data)


def test_a_restatement_of_a_conjecture_is_a_gap_of_its_own(tmp_path) -> None:
    """`x` restated onto the conjecture `k` asserts what the paper only
    conjectures: unsupported, listed by `unsupported-claims` saying so,
    and no ruling clears it; its route is still `k`'s, so once a reader
    shows `k` holds, `x` is established and still listed."""
    store, kept = _restating(tmp_path, ("x", "k"))
    assert support_of(store, kept, "x") == [rulings.Support.UNSUPPORTED]
    (item,) = [i for i in gaps(store, kept).items if i.data["handle"] == "x"]
    assert (
        "(restates k, which the paper only conjectures: restating it asserts it)"
        in item.line
    )
    assert item.data["clears"].startswith("the conjecture argued and asserted")
    assert "k" not in listed(gaps(store, kept))
    with pytest.raises(JudgmentError, match="stands restated"):
        ruled(store, kept, "x", KNOWN, PERSON)
    judged(store, kept, Aspect.TRUE, "k", Verdict.VERIFIED, PERSON)
    (x,) = [
        e for e in View(store, kept).established if e.claim.words.startswith("Clearly")
    ]
    assert x.established
    assert "x" in listed(gaps(store, kept))


def test_a_chain_onto_a_conjecture_is_listed_once_where_it_asserts_it(
    tmp_path,
) -> None:
    """`y` restated onto `x`, `x` onto the conjecture `k`: `x` is listed,
    `y` rests on it and is not."""
    store, kept = _restating(tmp_path, ("y", "x"), ("x", "k"))
    found = listed(gaps(store, kept))
    assert "x" in found and "y" not in found and "k" not in found


def test_a_restatement_of_a_conjecture_read_negative_is_a_defect_only(
    tmp_path,
) -> None:
    """`x` restated onto `k`, its `true` contradicted: `defects` lists the
    reading, and `unsupported-claims` leaves it there."""
    store, kept = _restating(tmp_path, ("x", "k"))
    judged(store, kept, Aspect.TRUE, "x", Verdict.CONTRADICTED)
    found = gaps(store, kept)
    assert "x" not in listed(found) and found.to_defects == 1
    assert "x" in [i.data["handle"] for i in results(store, kept)[Gate.DEFECTS].items]


# --- the question a review answered ------------------------------------------------


def review_ruled(store, kept, on, kind, **given) -> None:
    at = seen(store, kept)
    owed = kind is RulingKind.OWED
    record = rulings.ruling_record(
        at,
        rulings.subject(at, on),
        kind,
        "a reason",
        2 if owed else None,
        "argue it" if owed else None,
    )
    reviewed(store, JudgmentRecords(rulings=(record,)), **given)


def test_the_ruling_question_a_review_s_answer_answered(tmp_path) -> None:
    """Its template's record, the first where it names none; none for a
    ruling filed by hand, a person's or an agent's: it answered no brief,
    and is never asked again."""
    store, kept = built(tmp_path)
    judged(store, kept, Aspect.TRUE, "x", Verdict.VERIFIED)
    review_ruled(store, kept, "x", KNOWN, time=BEFORE, asks=())
    review_ruled(store, kept, "x", KNOWN, time=AFTER, asks=(("ruling", 2),))
    ruled(store, kept, "x", KNOWN, AGENT)
    ruled(store, kept, "x", OWED, PERSON)
    (ruled_x,) = View(store, kept).rulings.values()
    assert [r.question for r in ruled_x.reads] == [1, 2, None, None]
    # each under a version older than the brief's now
    assert [r.outdated for r in ruled_x.reads] == [True] * 2 + [False] * 2


def test_a_ruling_under_an_older_question_speaks_until_a_review_rules_again(
    tmp_path,
) -> None:
    """model-a's review ruled `x` owed under the ruling question's version
    1: it speaks, says it is asked again, and keeps `x` listed beside an
    agent's known filed by hand. model-b's review rules it known under the
    current question: the old ruling speaks no more, and `x` is cleared."""
    store, kept = built(tmp_path)
    judged(store, kept, Aspect.TRUE, "x", Verdict.VERIFIED)
    review_ruled(store, kept, "x", OWED, time=BEFORE, asks=())
    ruled(store, kept, "x", KNOWN, OTHER)
    view = View(store, kept)
    (ruled_x,) = view.rulings.values()
    assert ruled_x.outdated and "x" in listed(gaps(store, kept))
    assert f"owed 2 by claude-code 2.1.310 model-a ({queries.OLDER_QUESTION})" in (
        queries.ruled_words(ruled_x)
    )
    lines = [i.line for i in queries.show(view, "x").sections["rulings"]]
    assert any(f"(speaks, {queries.OLDER_QUESTION})" in line for line in lines)

    review_ruled(store, kept, "x", KNOWN, model="model-b")
    view = View(store, kept)
    (ruled_x,) = view.rulings.values()
    assert not ruled_x.outdated and "x" not in listed(gaps(store, kept))
    assert sorted(r.author.name for r in ruled_x.speaks) == [
        "agent model-b",
        "claude-code 2.1.310 model-b",
    ]
    lines = [i.line for i in queries.show(view, "x").sections["rulings"]]
    assert any(
        "owed 2 by claude-code 2.1.310 model-a (replaced)" in line for line in lines
    )


def test_each_brief_names_what_it_asks_past_version_1() -> None:
    assert rf.asks(rf.Session.ARGUMENT) == (("ruling", 4),)
    assert rf.asks(rf.Session.CITATIONCHECK) == ()


def test_an_answer_filed_again_keeps_the_question_it_answered(tmp_path) -> None:
    """`review --file-answer` files a kept answer anew, at a later time: its
    template names the ruling question it was asked under, so the filing's
    time does not date it."""
    store, kept = built(tmp_path)
    judged(store, kept, Aspect.TRUE, "x", Verdict.VERIFIED)
    review_ruled(store, kept, "x", OWED, time=BEFORE, asks=())
    old = next(a for a in store.answers() if a.stage == "review")
    filed = Resolver(store, clock=lambda: AFTER).checked_again(old, Validation())
    again = filed.answer
    assert (again.attempt, again.time) == (old.attempt + 1, AFTER)
    assert again.template == old.template
    assert rf.asked_version(again, "ruling") == 1
    assert rf.outdated(again, "ruling")
    hand = store.authoritative()[store.judgment_records().judgments[0].answer]
    assert rf.asked_version(hand, "ruling") is None


# --- an owed ruling's weight and fix; a person's proposal ---------------------------


def test_an_owed_ruling_carries_its_severity_and_fix_and_no_other_kind_does(
    tmp_path,
) -> None:
    store, kept = built(tmp_path)
    on = rulings.subject(seen(store, kept), "x")
    with pytest.raises(JudgmentError, match="carries its severity"):
        rulings.rule(store, kept, on, OWED, "not argued", AGENT, fix="argue it")
    with pytest.raises(JudgmentError, match="carries its fix"):
        rulings.rule(store, kept, on, OWED, "not argued", AGENT, severity=2)
    with pytest.raises(JudgmentError, match="only an owed ruling"):
        rulings.rule(store, kept, on, KNOWN, "a fact", AGENT, severity=1)
    record = ruled(store, kept, "x", OWED, severity=3)
    assert (record.severity, record.fix) == (3, "argue it")
    (read,) = store.judgment_records().rulings
    assert (read.severity, read.fix, read.proposed) == (3, "argue it", False)


def test_a_ruling_record_refuses_a_severity_on_another_kind(tmp_path) -> None:
    store, kept = built(tmp_path)
    ruled(store, kept, "x", KNOWN)
    record_file.rewrite_records(
        store.directory, RecordKind.RULINGS, lambda r: {**r, "severity": 2}
    )
    with pytest.raises(ValueError, match="only an owed ruling"):
        RecordStore(store.directory).judgment_records()


def test_an_owed_ruling_without_its_severity_or_fix_is_refused_on_reading(
    tmp_path,
) -> None:
    store, kept = built(tmp_path)
    ruled(store, kept, "x", OWED)
    record_file.rewrite_records(
        store.directory,
        RecordKind.RULINGS,
        lambda r: {k: v for k, v in r.items() if k != "fix"},
    )
    with pytest.raises(RecordError, match="carries its severity and its fix"):
        RecordStore(store.directory).judgment_records()


def test_the_owed_claims_are_listed_heaviest_first(tmp_path) -> None:
    """`y` owed 3 first; then `c` and `x` owed 1, `c` first as `s` relies on
    it; the weight beside each, and no gate changed by it."""
    store, kept = built(tmp_path)
    ruled(store, kept, "x", OWED, severity=1)
    ruled(store, kept, "c", OWED, severity=1)
    ruled(store, kept, "y", OWED, severity=3)
    view = View(store, kept)
    # `n`, unruled, weighs nothing: after them
    assert listed(gaps(store, kept)) == ["y", "c", "x", "n"]
    items = queries.open_report(view).sections["unsupported"]
    assert [i.data["handle"] for i in items] == ["y", "c", "x", "n"]
    assert [i.data["weight"] for i in items] == [[3, 0], [1, 1], [1, 0], [0, 0]]
    lines = {i.data["handle"]: i.line for i in items}
    assert "ruled owed 3 by agent model-a: a reason; fix: argue it" in lines["y"]
    assert "· relied on by s" in lines["c"] and "relied on by" not in lines["x"]


def proposed_on(store, kept, handle: str) -> str | None:
    """The ruling proposed on `handle`'s open unsupported item, if any."""
    (item,) = [
        i
        for i in queries.open_report(View(store, kept)).sections["unsupported"]
        if i.data["handle"] == handle
    ]
    proposal = item.data.get("proposal")
    return None if proposal is None else proposal["ruling"]


def test_a_proposed_ruling_never_speaks_and_asks_until_a_ruling_follows(
    tmp_path,
) -> None:
    """A person's proposed `known` over an agent's `owed` changes nothing
    and asks the claim again; the next ruling filed settles it; a
    withdrawn one asks nothing."""
    store, kept = built(tmp_path)
    ruled(store, kept, "x", OWED)
    on = rulings.subject(seen(store, kept), "x")
    rulings.rule(store, kept, on, KNOWN, "the field's standard fact", propose=True)
    assert "x" in listed(gaps(store, kept))
    (r,) = View(store, kept).rulings.values()
    assert [s.ruling.kind for s in r.speaks] == [OWED]
    assert r.proposal is not None and r.proposal.asks
    (line,) = [
        i.line
        for i in queries.show(View(store, kept), "x").sections["rulings"]
        if "known" in i.line
    ]
    assert "(proposed: the next argument review settles it)" in line
    # its open item carries the proposal, so a reader shows it waiting
    assert proposed_on(store, kept, "x") == "known"
    settled = ruled(store, kept, "x", OWED, OTHER)
    (r,) = View(store, kept).rulings.values()
    assert r.proposal is not None and not r.proposal.asks
    assert r.proposal.reask is not None and r.proposal.reask.ruling.id == settled.id
    assert proposed_on(store, kept, "x") is None
    proposed = rulings.rule(store, kept, on, KNOWN, "again", propose=True)
    rulings.withdraw(store, proposed.records.rulings[0].id, "meant to rule")
    (r,) = View(store, kept).rulings.values()
    assert r.proposal is not None and not r.proposal.asks


def test_a_proposal_is_a_person_s_on_one_claim_never_left_unargued_or_decided(
    tmp_path,
) -> None:
    store, kept = built(tmp_path)
    on = rulings.subject(seen(store, kept), "x")
    with pytest.raises(JudgmentError, match="a proposed ruling is a person's"):
        rulings.rule(store, kept, on, KNOWN, "a fact", AGENT, propose=True)
    with pytest.raises(JudgmentError, match="rule it, never propose it"):
        rulings.rule(store, kept, on, LEFT, "kept short", propose=True)
    other = rulings.subject(seen(store, kept), "y")
    with pytest.raises(JudgmentError, match="names one claim"):
        rulings.rule_each(store, kept, (on, other), KNOWN, "a fact", propose=True)
    decided = ruled(store, kept, "x", KNOWN, PERSON)
    with pytest.raises(JudgmentError, match=f"decides it \\({decided.id}\\)"):
        rulings.rule(store, kept, on, OWED, "r", severity=1, fix="f", propose=True)


def test_rule_propose_from_the_command_line(tmp_path, capsys) -> None:
    store, kept = built(tmp_path)
    paper = tmp_path / "paper"
    paper.mkdir()
    base = [str(paper), "--store", str(store.directory)]
    given = ["rule", *base, "--on", "x", "--ruling", "owed", "--reason", "r"]
    assert main([*given, "--severity", "2"]) == 1
    assert "carries its fix" in capsys.readouterr().err
    proposed = [*given, "--severity", "2", "--fix", "argue it", "--propose"]
    assert main(proposed) == 0
    out = capsys.readouterr().out
    assert "proposed ruling " in out and '"Clearly f is extra." owed 2' in out
    assert "it changes no status" in out and "gates: unchanged" in out
    on_a_set = ["--in", "x", "--ruling", "known", "--propose", "--reason", "r"]
    assert main(["rule", *base, *on_a_set]) == 1
    assert "names one claim" in capsys.readouterr().err


def test_a_proposal_s_state_in_show_settled_and_withdrawn(tmp_path) -> None:
    store, kept = built(tmp_path)
    on = rulings.subject(seen(store, kept), "x")
    first = rulings.rule(store, kept, on, KNOWN, "a fact", propose=True)
    settled = ruled(store, kept, "x", OWED, OTHER)
    second = rulings.rule(store, kept, on, KNOWN, "again", propose=True)
    rulings.withdraw(store, second.records.rulings[0].id, "meant to rule")
    view = View(store, kept)
    lines = {i.data["id"]: i.line for i in queries.show(view, "x").sections["rulings"]}
    assert f"(proposed, settled by {settled.id})" in lines[first.records.rulings[0].id]
    assert (
        "(proposed, withdrawn: meant to rule)" in (lines[second.records.rulings[0].id])
    )


def test_a_person_weighs_their_own_old_owed_rulings_on_a_set(tmp_path, capsys) -> None:
    """`rule --in` leaves a claim the person ruled so already, but an owed
    one ruled again with another weight or fix is ruled anew."""
    store, kept = built(tmp_path)
    paper = tmp_path / "paper"
    paper.mkdir()
    base = [str(paper), "--store", str(store.directory)]
    owed = ["--in", "x", "--ruling", "owed", "--reason", "r", "--fix", "argue it"]
    assert main(["rule", *base, *owed, "--severity", "1"]) == 0
    assert main(["rule", *base, *owed, "--severity", "1"]) == 0
    assert "nothing to rule" in capsys.readouterr().out
    assert main(["rule", *base, *owed, "--severity", "3"]) == 0
    assert [r.severity for r in store.judgment_records().rulings] == [1, 3]
