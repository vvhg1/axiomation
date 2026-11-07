"""What the kept graph got wrong or cannot settle: dangling
references, duplicate labels, unused premises, undeclared assumptions.

The labels and premises on a synthetic paper the reader and the cutter
read, its edges written by hand; the undeclared assumptions on a synthetic
kept state, as `established` reads one. No model is called.
"""

from __future__ import annotations

from typing import Any

import pytest

from axiomation.cutter import CutRules, DisplayRule, cut
from axiomation.established import undeclared
from axiomation.exchange import cut_payload
from axiomation.judgments import readings
from axiomation.reader import read_paper
from axiomation.records import RecordStore
from axiomation.soundness import (
    Kept,
    dangling_references,
    duplicate_labels,
    undeclared_assumptions,
    unused_premises,
)
from test_judgments import edge, graph, naming
from test_versions import V1, keep

PAPER = r"""# Setting

\begin{definition}\label{def:gain}
A gain is a positive number.
\end{definition}

\begin{definition}\label{def:loss}
A loss is a negative number.
\end{definition}

\begin{assumption}\label{ass:positive}
Every gain is finite.
\end{assumption}

\begin{condition}\label{cond:used}
Every f is small, with
$$ c_f = 1 $$
for every f.
\end{condition}

\begin{condition}\label{cond:unused}
Every h is small, with
$$ c_h = 1 $$
for every h.
\end{condition}

We assume throughout that the losses are bounded.

\begin{lemma}\label{lem:small}
By \ref{def:gain} and \ref{lem:missing}, f is small.
\end{lemma}

\begin{remark}\label{rem:twice}
The first remark.
\end{remark}

\begin{remark}\label{rem:twice}
The second remark.
\end{remark}

See \cref{lem:small,lem:gone} and \ref{rem:twice}.
"""
#: The blocks by what they hold, the cutter's ids (`file:line:column`).
GAIN, LOSS, POSITIVE = "paper.md:3:1", "paper.md:7:1", "paper.md:11:1"
USED, UNUSED = "paper.md:15:1", "paper.md:21:1"
PROSE, LEMMA, SEE = "paper.md:27:1", "paper.md:29:1", "paper.md:41:1"
FIRST, SECOND = "paper.md:33:1", "paper.md:37:1"


def _split(payload: dict[str, Any], block: str, line: int) -> str:
    """A block cut in two at the start of `line`, as a boundaries correction
    cuts one: the new block's id."""
    blocks = payload["blocks"]
    i = next(n for n, b in enumerate(blocks) if b["id"] == block)
    first = blocks[i]
    at = [line, 1]
    second = {
        **first,
        "id": f"paper.md:{line}:1",
        "extent": {**first["extent"], "start": at},
        "owned": [
            {**r, "start": max(r["start"], at)} for r in first["owned"] if r["end"] > at
        ],
    }
    first["extent"] = {**first["extent"], "end": at}
    first["owned"] = [
        {**r, "end": min(r["end"], at)} for r in first["owned"] if r["start"] < at
    ]
    for b in (first, second):
        b.pop("hash", None)
    blocks.insert(i + 1, second)
    return second["id"]


def cut_paper(tmp_path, edges: list[dict[str, Any]]):
    """The paper read and cut, each condition in two blocks (its display
    the second), the prose sentence an assumption, under `edges`: its
    document, its export, and the conditions' second blocks."""
    (tmp_path / "paper.md").write_text(PAPER, encoding="utf-8")
    document = read_paper(tmp_path)
    payload = cut_payload(document, cut(document, CutRules(DisplayRule.IN_PARAGRAPH)))
    used_display = _split(payload, USED, 17)
    unused_display = _split(payload, UNUSED, 23)
    for b in payload["blocks"]:
        if b["id"] == PROSE:
            b["roles"] = ["assumption"]
    payload["edges"] = edges
    return document, payload, used_display, unused_display


def paper(tmp_path, edges: list[dict[str, Any]]) -> tuple[Kept, str, str]:
    document, payload, used_display, unused_display = cut_paper(tmp_path, edges)
    return Kept(document, payload), used_display, unused_display


def test_a_reference_to_a_label_no_block_holds_dangles(tmp_path) -> None:
    kept, _, _ = paper(tmp_path, [])
    found = dangling_references(kept)
    assert [(i.blocks, i.words, i.cause["label"]) for i in found] == [
        ((LEMMA,), r"\ref{lem:missing}", "lem:missing"),
        ((SEE,), r"\cref{lem:small,lem:gone}", "lem:gone"),
    ]
    assert found[0].cause["why"] == "defined nowhere"
    assert "lem:missing" in found[0].line and "lem:missing" in found[0].clears


def test_a_label_two_blocks_hold_is_listed_on_both(tmp_path) -> None:
    kept, _, _ = paper(tmp_path, [])
    [found] = duplicate_labels(kept)
    assert found.blocks == (FIRST, SECOND)
    assert found.cause == {"label": "rem:twice", "holders": [FIRST, SECOND]}
    assert found.line.startswith("rem:twice held by")


def test_an_assumption_nothing_uses_or_is_qualified_by_is_unused(tmp_path) -> None:
    """The used condition is used through its display block alone; the
    unused one is listed once, both its blocks; the assumption qualifies
    a definition; the prose assumption is unused; the unused definition
    is never a premise here."""
    edges = [
        edge(LEMMA, "uses", GAIN),
        edge(POSITIVE, "qualifies", GAIN),
    ]
    kept, used_display, unused_display = paper(tmp_path, edges)
    kept.export["edges"].append(edge(LEMMA, "uses", used_display))
    found = unused_premises(kept)
    assert [(i.blocks, i.cause["label"]) for i in found] == [
        ((UNUSED, unused_display), "cond:unused"),
        ((PROSE,), None),
    ]
    assert found[0].line.startswith("cond:unused  condition")
    assert LOSS not in {b for i in found for b in i.blocks}


def test_a_group_used_whole_uses_the_premise_it_holds(tmp_path) -> None:
    kept, _, _ = paper(tmp_path, [])
    kept.export["groups"] = [{"id": "g1", "blocks": [PROSE, LEMMA]}]
    kept.export["edges"] = [edge(SEE, "uses", "g1")]
    kept.export["edges"][0]["target"] = {"group": "g1"}
    assert PROSE not in {b for i in unused_premises(kept) for b in i.blocks}


def test_an_ambient_block_is_used_by_every_block_after_it(tmp_path) -> None:
    kept, _, _ = paper(tmp_path, [])
    kept.export["ambient"] = [PROSE]
    assert PROSE not in {b for i in unused_premises(kept) for b in i.blocks}


def test_a_key_s_export_its_blocks_placed_alone_reads_the_same(tmp_path) -> None:
    """A key's export carries where each block lies and no environment,
    role or segment (its roles are its role key): the labels resolve on
    that alone, as they do on a build's."""
    document, payload, _, _ = cut_paper(tmp_path, [])
    for b in payload["blocks"]:
        for key in ("environment", "meaning", "roles", "segments", "hash", "doubts"):
            b.pop(key, None)
    for key in ("parts", "groups", "rules", "candidates"):
        payload.pop(key, None)
    kept = Kept(document, payload)
    assert [i.cause["label"] for i in dangling_references(kept)] == [
        "lem:missing",
        "lem:gone",
    ]
    assert [i.cause["label"] for i in duplicate_labels(kept)] == ["rem:twice"]


# --- undeclared assumptions ---------------------------------------------------------

BLOCKS = [
    ("d", "Let f be a map."),
    ("a", "Assume f is positive."),
    ("t", "Then f is bounded."),
    ("p", "Since f is positive, f is bounded."),
    ("k", "Surely f is finite."),
    ("l", "If f is finite, f is small."),
    ("q", "As f is finite, f is small."),
    ("s", "Then f is tiny."),
    ("r", "As f is finite, f is tiny."),
    ("u", "Under the assumption, f is fine."),
    ("v", "Since f is positive, f is fine."),
]
TEXT = " ".join(w for _, w in BLOCKS)
EDGES = [
    edge("t", "uses", "d"),
    edge("s", "uses", "d"),
    edge("p", "uses", "a"),
    edge("p", "justifies", "t"),
    edge("l", "uses", "k"),
    edge("q", "uses", "k"),
    edge("q", "justifies", "l"),
    edge("r", "uses", "k"),
    edge("r", "justifies", "s"),
    edge("u", "uses", "a"),
    edge("v", "uses", "a"),
    edge("v", "justifies", "u"),
]
STANDINGS = {
    "t": "justified",
    "k": "conjectured",
    "l": "justified",
    "s": "justified",
    "u": "justified",
}


def state(tmp_path):
    words = dict(BLOCKS)
    payload = graph(TEXT, BLOCKS, EDGES, [(b, words[b]) for b in STANDINGS])
    for s in payload["standings"]:
        s["status"] = STANDINGS[s["block"]]
    for b in payload["blocks"]:
        if b["id"] == "a":
            b["roles"] = ["assumption"]
    store = RecordStore(tmp_path / "store")
    return store, keep(store, V1, TEXT, payload)


def test_a_premise_the_j_reaches_and_the_statement_does_not_is_undeclared(
    tmp_path,
) -> None:
    """t's proof uses the assumption t's statement never names; s's proof
    asks the conjecture s never names. l states the conjecture as its
    hypothesis and u the assumption: honest conditional results."""
    store, kept = state(tmp_path)
    found = readings(store, kept)
    block = {n.node: n.block for n in kept.nodes}
    assert [
        (block[u.claim.node], block[u.via], block[u.premise], u.conjecture is not None)
        for u in undeclared(found)
    ] == [("t", "p", "a", False), ("s", "r", "k", True)]
    items = undeclared_assumptions(found)
    assert [i.blocks for i in items] == [("t", "p", "a"), ("s", "r", "k")]
    assert [i.cause["cause"] for i in items] == ["left-out", "left-out"]
    assert items[1].line == (
        's "Then f is tiny.": its J r rests on conjectured k "Surely f is finite.", '
        "which its statement does not: a premise it leaves out\n"
        '  r → uses k · "r"'
    )


def _chain(tmp_path, lemma_uses: str, proof_uses: str, assumed: bool = False):
    """A theorem `t` whose proof `p` uses a lemma `l`; `l`'s statement uses
    `lemma_uses`, its proof `q` uses `proof_uses`: the assumption `a` or
    the conjecture `k`; `assumed`, `l`'s statement takes it as a
    hypothesis."""
    blocks = [
        ("d", "Let f be a map."),
        ("a", "Assume f is positive."),
        ("k", "Surely f is finite."),
        ("l", "If so, f is small."),
        ("q", "As so, f is small."),
        ("t", "Then f is bounded."),
        ("p", "By the lemma, f is bounded."),
    ]
    text = " ".join(w for _, w in blocks)
    words = dict(blocks)
    edges = [
        {**edge("l", "uses", lemma_uses), **({"assumed": True} if assumed else {})},
        edge("q", "uses", proof_uses),
        edge("q", "justifies", "l"),
        edge("p", "uses", "l"),
        edge("p", "justifies", "t"),
        edge("t", "uses", "d"),
    ]
    standings = {"k": "conjectured", "l": "justified", "t": "justified"}
    payload = graph(text, blocks, edges, [(b, words[b]) for b in standings])
    for s in payload["standings"]:
        s["status"] = standings[s["block"]]
    for b in payload["blocks"]:
        if b["id"] == "a":
            b["roles"] = ["assumption"]
    store = RecordStore(tmp_path / "store")
    kept = keep(store, V1, text, payload)
    found = readings(store, kept)
    block = {n.node: n.block for n in kept.nodes}
    return [
        (
            block[u.claim.node],
            block[u.via],
            block[u.premise],
            None if u.through is None else block[u.through.node],
        )
        for u in undeclared(found)
    ], undeclared_assumptions(found)


def test_a_premise_a_lemma_states_is_the_theorem_s_through_it(tmp_path) -> None:
    """`l` states `a` and its proof uses it: `l` is honest, but `t`'s proof
    rests on `a` through `l`, and `t`'s statement never names it."""
    found, items = _chain(tmp_path, lemma_uses="a", proof_uses="a")
    assert found == [("t", "p", "a", "l")]
    assert items[0].blocks == ("t", "p", "l", "a")
    assert "rests on a through l, which its statement does not" in items[0].line
    assert items[0].line.splitlines()[1:] == [
        '  p → uses l · "p"',
        '  l → uses a · "l"',
    ]


def test_a_conjecture_a_lemma_takes_as_a_hypothesis_is_the_theorem_s_through_it(
    tmp_path,
) -> None:
    """`l` takes `k` as its hypothesis: `l` is established under it, and
    `t`'s proof rests on it through `l`; the path says the flag."""
    found, items = _chain(tmp_path, lemma_uses="k", proof_uses="k", assumed=True)
    assert found == [("t", "p", "k", "l")]
    assert items[0].line.splitlines()[1:] == [
        '  p → uses l · "p"',
        '  l → uses k assumed · "l"',
    ]


def test_a_fix_upstream_shows_the_premise_downstream(tmp_path) -> None:
    """`l`'s proof asks `k`, which `l` never states: `l`'s item. Once `l`
    states it, `l` is a conditional result and `t` rests on `k` through
    it: acting on the item moves it, it does not vanish."""
    before, _ = _chain(tmp_path / "before", lemma_uses="a", proof_uses="k")
    assert before == [("l", "q", "k", None), ("t", "p", "a", "l")]
    after, _ = _chain(tmp_path / "after", lemma_uses="k", proof_uses="k")
    assert after == [("t", "p", "k", "l")]


def _copied(
    tmp_path,
    link: str | None,
    stated: str,
    proved: str,
    claimed: bool = False,
    where_used: bool = False,
    generalizes: bool = False,
):
    """An assumption `a` and an appendix's `c` (`link` from `c` onto `a`:
    `about`, a copy; `uses`, a narrowing; or none), a theorem `t` whose
    statement uses `stated` and whose proof `p` uses `proved`. `claimed`:
    `c` holds a claim beside its assumption, which `p` relies on by its
    assumption part; `where_used`, `generalizes`: the link is flagged so."""
    blocks = [
        ("a", "Assume f is positive."),
        ("t", "Then f is bounded."),
        ("c", "Again, assume f is positive. Then f is fine."),
        ("p", "Since f is positive, f is bounded."),
    ]
    text = " ".join(w for _, w in blocks)
    about = edge("c", link, "a") if link else None
    if about is not None and where_used:
        about["where_used"] = True
    if about is not None and generalizes:
        about["generalizes"] = True
    proof_uses = (
        edge("p", "uses", proved, "c [H]") if claimed and proved == "c" else None
    )
    edges = [
        edge("t", "uses", stated),
        proof_uses or edge("p", "uses", proved),
        edge("p", "justifies", "t"),
        *([about] if about else []),
    ]
    standings = [("t", "Then f is bounded.")]
    if claimed:
        standings.append(("c", "Then f is fine."))
    payload = graph(text, blocks, edges, standings)
    for x, status in zip(
        payload["standings"], ("justified", "unsupported"), strict=False
    ):
        x["status"] = status
    if claimed:
        payload["entries"] = [
            {
                "id": "c [H]",
                "anchor": "Again, assume f is positive.",
                "role": "assumption",
            }
        ]
    for b in payload["blocks"]:
        if b["id"] == "a" or (b["id"] == "c" and not claimed):
            b["roles"] = ["assumption"]
        elif b["id"] == "c":
            b["roles"] = ["assumption", "claim"]
    store = RecordStore(tmp_path / "store")
    kept = keep(store, V1, text, payload)
    block = {n.node: n.block for n in kept.nodes}
    return [
        (block[u.claim.node], block[u.premise])
        for u in undeclared(readings(store, kept))
    ]


@pytest.mark.parametrize(("stated", "proved"), [("a", "c"), ("c", "a")])
def test_an_assumption_s_copy_is_the_same_premise(tmp_path, stated, proved) -> None:
    """An appendix restating the standing assumption, about it: a statement
    that declares either declares the other. Without the about edge the
    copy reads as a second premise."""
    assert _copied(tmp_path / "plain", None, stated, proved) == [("t", proved)]
    assert _copied(tmp_path / "about", "about", stated, proved) == []


def test_a_block_holding_a_claim_or_a_where_used_edge_is_no_copy(tmp_path) -> None:
    """A block whose hypothesis sits beside a claim (a preview of a
    theorem) is about the claim, its hypotheses possibly fewer; a
    where-used edge says where its target is used: neither joins two
    premises, so the proof's premise is still listed."""
    assert _copied(tmp_path / "claim", "about", "a", "c", claimed=True) == [("t", "c")]
    assert _copied(tmp_path / "where", "about", "a", "c", where_used=True) == [
        ("t", "c")
    ]


def test_an_assumption_that_narrows_another_stays_its_own_premise(tmp_path) -> None:
    """`c` uses `a` (it narrows or adds to it): the statement declaring `a`
    never declares `c`, which the proof rests on."""
    assert _copied(tmp_path, "uses", "a", "c") == [("t", "c")]


def _corollary(tmp_path, theorem_uses: str | None, proof_uses: str):
    """A lemma `l` stating `a`, a corollary `r` restating it, and a
    theorem `t` whose statement uses `theorem_uses` (or nothing) and whose
    proof `p` uses `proof_uses`."""
    blocks = [
        ("a", "Assume f is positive."),
        ("l", "If so, f is small."),
        ("q", "As so, f is small."),
        ("r", "In other words, f is small."),
        ("t", "Then f is bounded."),
        ("p", "By the lemma, f is bounded."),
    ]
    text = " ".join(w for _, w in blocks)
    words = dict(blocks)
    edges = [
        edge("l", "uses", "a"),
        edge("q", "uses", "a"),
        edge("q", "justifies", "l"),
        edge("r", "about", "l"),
        edge("p", "uses", proof_uses),
        edge("p", "justifies", "t"),
    ]
    if theorem_uses is not None:
        edges.append(edge("t", "uses", theorem_uses))
    standings = {"l": "justified", "r": "restated", "t": "justified"}
    payload = graph(text, blocks, edges, [(b, words[b]) for b in standings])
    for s in payload["standings"]:
        s["status"] = standings[s["block"]]
    for b in payload["blocks"]:
        if b["id"] == "a":
            b["roles"] = ["assumption"]
    store = RecordStore(tmp_path / "store")
    kept = keep(store, V1, text, payload)
    block = {n.node: n.block for n in kept.nodes}
    return [
        (
            block[u.claim.node],
            block[u.via],
            block[u.premise],
            None if u.through is None else block[u.through.node],
        )
        for u in undeclared(readings(store, kept))
    ]


def test_a_corollary_s_statement_is_the_lemma_s_it_restates(tmp_path) -> None:
    """A proof citing the corollary rests on what the lemma states, as one
    citing the lemma does; a statement using the corollary declares it."""
    assert _corollary(tmp_path / "cited", None, "r") == [("t", "p", "a", "r")]
    assert _corollary(tmp_path / "declared", "r", "l") == []


# --- the direction rule: a fallback ask declares nothing --------------


def _state(
    tmp_path, blocks, edges, standings, entries=(), assumptions=("a", "b"), groups=()
):
    """A synthetic state's readings and its blocks by node: `justified`
    claims, `standings` (block, words) in order, `assumptions` (by default
    `a` and `b`) the assumptions."""
    text = " ".join(w for _, w in blocks)
    payload = graph(text, blocks, edges, list(standings))
    for s in payload["standings"]:
        s["status"] = "justified"
    for b in payload["blocks"]:
        if b["id"] in assumptions:
            b["roles"] = ["assumption"]
    payload["entries"] = list(entries)
    payload["groups"] = [
        {"id": g, "kind": "proof", "blocks": list(members)} for g, members in groups
    ]
    store = RecordStore(tmp_path / "store")
    kept = keep(store, V1, text, payload)
    return readings(store, kept), {n.node: n.block for n in kept.nodes}


def _items(tmp_path, blocks, edges, standings, entries=()):
    """`undeclared` over `_state`: each item as (claim's block, J,
    premise, through's block)."""
    found, block = _state(tmp_path, blocks, edges, standings, entries)
    return [
        (
            block[u.claim.node],
            block[u.via],
            block[u.premise],
            None if u.through is None else block[u.through.node],
        )
        for u in undeclared(found)
    ]


def test_a_statement_citing_two_claims_by_none_declares_nothing_through_them(
    tmp_path,
) -> None:
    """t's statement uses m, a block of two lemmas, which states `a`.
    Naming no claim, it is a fallback ask: which lemma t states is
    unknown, so it declares nothing, and t is listed for `a`, which its
    proof reaches through m. Naming the first lemma by its handle, it declares
    what that lemma states; onto a block of one claim, naming none is no
    fallback. The item says what clears it: naming the claim."""
    blocks = [
        ("a", "Assume f is positive."),
        ("m", "Then f is small. Also f is tiny."),
        ("t", "Then f is bounded."),
        ("p", "By the lemma, f is bounded."),
    ]
    edges = [
        edge("m", "uses", "a"),
        edge("p", "uses", "m"),
        edge("p", "justifies", "t"),
    ]
    two = [
        ("m", "Then f is small."),
        ("m", "Also f is tiny."),
        ("t", "Then f is bounded."),
    ]
    entries: list[dict] = []

    def items(name, statement_edge, standings=two):
        return _items(
            tmp_path / name, blocks, [*edges, statement_edge], standings, entries
        )

    assert items("fallback", edge("t", "uses", "m")) == [("t", "p", "a", "m")]
    found, _ = _state(
        tmp_path / "line", blocks, [*edges, edge("t", "uses", "m")], two, entries
    )
    (item,) = undeclared_assumptions(found)
    assert item.cause["cause"] == "unnamed-line"
    assert [x["where"] for x in item.cause["lines"]] == ["statement"]
    assert item.line.splitlines()[0] == (
        't "Then f is bounded.": a line naming no claim of a block that holds '
        "several (t → uses m, so its statement declares nothing through it) "
        "leaves undeclared what its J rests on (p: a through m)"
    )
    assert item.clears.startswith("that line naming the claim")
    assert items("named", naming(edge("t", "uses", "m"), "Then f is small.")) == []
    assert items("one", edge("t", "uses", "m"), [two[0], two[2]]) == []


def test_a_fallback_in_a_lemma_s_statement_still_reaches_the_theorem_s_proof(
    tmp_path,
) -> None:
    """t's proof asks the lemma l by its words; l's statement asks m, a
    block of two claims, by none, and m states `a`. l declares nothing
    through m, but what l rests on still reaches t's proof: t is listed
    for `a` through l. Reading the declaration side's statement on the
    support side as well would lose it."""
    blocks = [
        ("a", "Assume f is positive."),
        ("m", "Then f is small. Also f is tiny."),
        ("l", "If so, f is fine."),
        ("t", "Then f is bounded."),
        ("p", "By the lemma, f is bounded."),
    ]
    edges = [
        edge("m", "uses", "a"),
        edge("l", "uses", "m"),
        edge("p", "uses", "l", "l [L]"),
        edge("p", "justifies", "t"),
    ]
    standings = [
        ("m", "Then f is small."),
        ("m", "Also f is tiny."),
        ("l", "If so, f is fine."),
        ("t", "Then f is bounded."),
    ]
    entries = [{"id": "l [L]", "anchor": "If so, f is fine.", "role": "claim"}]
    assert _items(tmp_path, blocks, edges, standings, entries) == [("t", "p", "a", "l")]


def test_a_circle_s_statements_do_not_depend_on_the_order_read(tmp_path) -> None:
    """x's statement asks y's and y's asks x's; x reaches `a`, y `b`. Each
    statement rests on both, whichever claim is read first, so u, whose
    statement uses y and whose proof v uses x, is listed for nothing."""
    blocks = [
        ("a", "Assume f is positive."),
        ("b", "Assume g is positive."),
        ("x", "Then f is small."),
        ("y", "Then g is small."),
        ("t", "Then f is bounded."),
        ("u", "Then g is bounded."),
        ("v", "By x, g is bounded."),
    ]
    edges = [
        edge("x", "uses", "a"),
        edge("x", "uses", "y"),
        edge("y", "uses", "b"),
        edge("y", "uses", "x"),
        edge("t", "uses", "x"),
        edge("u", "uses", "y"),
        edge("v", "uses", "x"),
        edge("v", "justifies", "u"),
    ]
    words = dict(blocks)
    for order in ("tuxy", "utyx"):
        standings = [(b, words[b]) for b in order]
        assert _items(tmp_path / order, blocks, edges, standings) == [], order


def test_a_j_whose_line_names_no_claim_is_listed_for_each_saying_so(
    tmp_path,
) -> None:
    """p justifies t, a block of two claims, naming neither: it covers
    both, so its premise `a` is listed on each, and the item says that p's
    line may argue the other."""
    blocks = [
        ("d", "Let f be a map."),
        ("a", "Assume f is positive."),
        ("t", "Then f is bounded. Also f is even."),
        ("p", "Since f is positive, f is bounded."),
    ]
    edges = [
        edge("t", "uses", "d"),
        edge("p", "uses", "a"),
        edge("p", "justifies", "t"),
    ]
    standings = [("t", "Then f is bounded."), ("t", "Also f is even.")]
    found, _ = _state(tmp_path, blocks, edges, standings)
    items = undeclared_assumptions(found)
    assert [i.cause["claim"] for i in items] == [w for _, w in standings]
    for i in items:
        assert i.cause["cause"] == "unnamed-line"
        assert "(p → justifies t, so p may argue another of its claims)" in i.line
        assert i.clears.startswith("that line naming the claim it means")


# --- context: a proof's reliance on its own claim ----------------------------


def test_a_step_reused_as_above_takes_no_scope_of_the_lemma_it_proves(
    tmp_path,
) -> None:
    """`w` proves lemma `l`, relying on `l`'s hypotheses (a uses edge onto
    `l`'s block), and `a` qualifies `l` (its scope). `v` proves `m` by
    reusing `w` ("as above"): the step runs under `m`'s hypotheses, so
    `m` is listed for nothing of `l`'s. A proof citing `l` itself, its
    result, still rests on `l`'s scope."""
    blocks = [
        ("d", "Let f be a map."),
        ("a", "Assume f is finite."),
        ("l", "Then f is small."),
        ("w", "Since f is finite, f is small."),
        ("m", "Then f is tiny."),
        ("v", "As above, f is tiny."),
    ]
    edges = [
        edge("a", "qualifies", "l"),
        edge("l", "uses", "d"),
        edge("w", "uses", "l"),
        edge("w", "justifies", "l"),
        edge("m", "uses", "d"),
        edge("v", "justifies", "m"),
    ]
    standings = [("l", "Then f is small."), ("m", "Then f is tiny.")]
    reused = [*edges, edge("v", "uses", "w")]
    assert _items(tmp_path / "reused", blocks, reused, standings) == []
    cited = [*edges, edge("v", "uses", "l")]
    assert _items(tmp_path / "cited", blocks, cited, standings) == [
        ("m", "v", "a", "l")
    ]


def test_a_claim_inside_a_proof_declares_its_theorem_s_hypotheses(
    tmp_path,
) -> None:
    """`c`, a claim stated inside `t`'s proof (the group `g`), holds under
    `t`'s hypothesis `a`: its own proof `n` using `a` lists nothing, though
    `c`'s block names no premise."""
    blocks = [
        ("d", "Let f be a map."),
        ("a", "Assume f is finite."),
        ("t", "If f is finite, f is bounded."),
        ("c", "Claim: f is small."),
        ("n", "Since f is finite, f is small."),
    ]
    edges = [
        edge("t", "uses", "a"),
        edge("c", "uses", "d"),
        edge("g", "justifies", "t"),
        edge("n", "uses", "a"),
        edge("n", "justifies", "c"),
    ]
    standings = [("t", "If f is finite, f is bounded."), ("c", "Claim: f is small.")]
    found, block = _state(tmp_path, blocks, edges, standings, groups=[("g", "cn")])
    assert [(block[u.claim.node], block[u.premise]) for u in undeclared(found)] == []
    # a claim cited whole as the reason is a result, no step of the proof
    cited = [e if e["source"] != "g" else edge("c", "justifies", "t") for e in edges]
    found, block = _state(tmp_path / "cited", blocks, cited, standings)
    assert [(block[u.claim.node], block[u.premise]) for u in undeclared(found)] == [
        ("c", "a")
    ]


# --- a discharge --------------------------------------------------------------


def _discharged(tmp_path, discharger: str | None):
    """`t`'s proof `p` applies lemma `l`, whose statement assumes `a`, and
    `a` narrows `b`; `discharger` (`p`, or `x` outside the proof, or none)
    justifies `a`."""
    blocks = [
        ("d", "Let f be a map."),
        ("b", "Assume f is finite."),
        ("a", "Assume f is finite and even."),
        ("l", "Then f is small."),
        ("t", "Then f is bounded."),
        ("p", "By the lemma, f is bounded, and f is even."),
        ("x", "Here f is even."),
    ]
    edges = [
        edge("a", "uses", "b"),
        edge("l", "uses", "a"),
        edge("t", "uses", "d"),
        edge("p", "uses", "l"),
        edge("p", "justifies", "t"),
    ]
    if discharger is not None:
        edges.append(edge(discharger, "justifies", "a"))
    standings = [("l", "Then f is small."), ("t", "Then f is bounded.")]
    found, _ = _state(tmp_path, blocks, edges, standings)
    return _items(tmp_path / "items", blocks, edges, standings), found


def test_a_discharge_in_the_proof_clears_the_premise_and_what_only_it_rests_on(
    tmp_path,
) -> None:
    """Without the discharge, `t`'s proof rests on `a` and `b` through `l`.
    `p` deriving `a` discharges it for `t`: the proof rests on the
    derivation, so neither `a` nor `b`, reached only through it, is
    listed. A discharge from outside the proof is none for it, and the
    item says who discharges it."""
    before, _ = _discharged(tmp_path / "none", None)
    assert before == [("t", "p", "a", "l"), ("t", "p", "b", "l")]
    within, _ = _discharged(tmp_path / "within", "p")
    assert within == []
    outside, found = _discharged(tmp_path / "outside", "x")
    assert outside == before
    items = undeclared_assumptions(found)
    assert [i.cause["cause"] for i in items] == ["discharged-elsewhere", "left-out"]
    assert "; x discharges it, outside p" in items[0].line


# --- one fault, one item ------------------------------------------------------


def test_a_statement_with_no_uses_edge_is_one_item_naming_each_premise(
    tmp_path,
) -> None:
    """`t`'s statement has no uses edge, and its proof rests on `a` and
    `b`: one item, both premises named, its path printed."""
    blocks = [
        ("a", "Assume f is finite."),
        ("b", "Assume f is even."),
        ("t", "Then f is bounded."),
        ("p", "Since f is finite and even, f is bounded."),
    ]
    edges = [
        edge("p", "uses", "a"),
        edge("p", "uses", "b"),
        edge("p", "justifies", "t"),
    ]
    found, _ = _state(tmp_path, blocks, edges, [("t", "Then f is bounded.")])
    (item,) = undeclared_assumptions(found)
    assert item.cause["cause"] == "missing-edges"
    assert [x["premise"] for x in item.cause["premises"]] == ["a", "b"]
    assert item.line.splitlines() == [
        't "Then f is bounded.": its statement has no uses edge, so it declares '
        "nothing its J rests on (p: a, b): its edges were missed, or it states "
        "nothing it rests on",
        '  p → uses a · "p"',
        '  p → uses b · "p"',
    ]
    assert item.blocks == ("t", "p", "a", "b")


def test_a_j_asking_a_block_of_several_claims_by_none_is_one_item(
    tmp_path,
) -> None:
    """`t`'s proof uses `m`, a block of two lemmas, naming neither: it
    rests on what each states, `a` and `b`, which `t`'s statement does not.
    The line is the fault: one item, naming it."""
    blocks = [
        ("d", "Let f be a map."),
        ("a", "Assume f is finite."),
        ("b", "Assume f is even."),
        ("m", "Then f is small. Also f is tiny."),
        ("t", "Then f is bounded."),
        ("p", "By the lemma, f is bounded."),
    ]
    edges = [
        edge("m", "uses", "a"),
        edge("m", "uses", "b"),
        edge("t", "uses", "d"),
        edge("p", "uses", "m"),
        edge("p", "justifies", "t"),
    ]
    standings = [
        ("m", "Then f is small."),
        ("m", "Also f is tiny."),
        ("t", "Then f is bounded."),
    ]
    found, _ = _state(tmp_path, blocks, edges, standings)
    (item,) = undeclared_assumptions(found)
    assert item.cause["cause"] == "unnamed-line"
    assert item.line.splitlines()[0] == (
        't "Then f is bounded.": a line naming no claim of a block that holds '
        "several (p → uses m, so the J rests on each claim's premises) leaves "
        "undeclared what its J rests on (p: a through m, b through m)"
    )
    assert '  p → uses m · "p" (names no claim)' in item.line.splitlines()


def test_a_premise_narrowing_one_the_statement_declares_says_so(tmp_path) -> None:
    """`b` uses `a` (it narrows or adds to it); `t`'s statement uses `a`,
    its proof `b`: the item names `a`, and an about edge as the way out
    where `b` only restates it."""
    blocks = [
        ("a", "Assume f is finite."),
        ("b", "Assume f is finite and positive."),
        ("t", "Then f is bounded."),
        ("p", "Since f is finite and positive, f is bounded."),
    ]
    edges = [
        edge("b", "uses", "a"),
        edge("t", "uses", "a"),
        edge("p", "uses", "b"),
        edge("p", "justifies", "t"),
    ]
    found, _ = _state(tmp_path, blocks, edges, [("t", "Then f is bounded.")])
    (item,) = undeclared_assumptions(found)
    assert item.cause["cause"] == "narrows"
    assert item.cause["premises"][0]["narrows"] == "a"
    assert "; it narrows or adds to a, which the statement declares" in item.line
    assert item.clears.startswith("an about edge from b onto a where it restates it")


@pytest.mark.parametrize("declares_by", ["qualifies", "context"])
def test_a_statement_declaring_by_a_qualifier_or_its_context_is_not_bare(
    tmp_path, declares_by
) -> None:
    """`c`'s block has no uses edge, but it declares by another edge: `a`
    qualifies it, or it is a claim inside `t`'s proof, which assumes `a`.
    Its proof's further premise `b` is a premise left out, not a statement
    missing its edges."""
    blocks = [
        ("a", "Assume f is finite."),
        ("b", "Assume f is even."),
        ("t", "If f is finite, f is bounded."),
        ("c", "Claim: f is small."),
        ("n", "Since f is finite and even, f is small."),
    ]
    edges = [
        edge("t", "uses", "a"),
        edge("n", "uses", "a"),
        edge("n", "uses", "b"),
        edge("n", "justifies", "c"),
    ]
    edges.append(
        edge("a", "qualifies", "c")
        if declares_by == "qualifies"
        else edge("g", "justifies", "t")
    )
    standings = [("t", "If f is finite, f is bounded."), ("c", "Claim: f is small.")]
    groups = [("g", "cn")] if declares_by == "context" else []
    found, _ = _state(tmp_path, blocks, edges, standings, groups=groups)
    items = [i for i in undeclared_assumptions(found) if i.blocks[0] == "c"]
    assert [(i.cause["cause"], i.blocks) for i in items] == [
        ("left-out", ("c", "n", "b"))
    ]


def test_an_assumption_about_two_restates_both_and_neither_is_it(tmp_path) -> None:
    """`c` is about `a` and about `b`: it restates both. A statement using
    `c` declares each; one using `a` declares `a` alone, so a proof using
    `b`, or `c`, still lists it. A shared copy never makes `a` and `b` one."""
    blocks = [
        ("d", "Let f be a map."),
        ("a", "Assume f is finite."),
        ("b", "Assume f is even."),
        ("c", "As before, f is finite and even."),
        ("t", "Then f is bounded."),
        ("p", "Since f is finite and even, f is bounded."),
    ]

    def items(name, stated, proved):
        edges = [
            edge("c", "about", "a"),
            edge("c", "about", "b"),
            edge("t", "uses", stated),
            edge("p", "uses", proved),
            edge("p", "justifies", "t"),
        ]
        found, block = _state(
            tmp_path / name,
            blocks,
            edges,
            [("t", "Then f is bounded.")],
            assumptions=("a", "b", "c"),
        )
        return [block[u.premise] for u in undeclared(found)]

    assert items("both", "c", "a") == []
    assert items("both-b", "c", "b") == []
    assert items("one", "a", "b") == ["b"]
    assert items("copy", "a", "c") == ["c"]


def test_a_claim_two_proofs_deep_declares_each_containing_claim_s_hypotheses(
    tmp_path,
) -> None:
    """`l` is stated in `t`'s proof, and `t` in `u`'s, which assumes `a`:
    `l`'s proof using `a` lists nothing."""
    blocks = [
        ("d", "Let f be a map."),
        ("a", "Assume f is finite."),
        ("u", "If f is finite, f is bounded."),
        ("t", "Claim: f is small."),
        ("x", "Hence f is bounded."),
        ("l", "Subclaim: f is tiny."),
        ("y", "Hence f is small."),
        ("n", "Since f is finite, f is tiny."),
    ]
    edges = [
        edge("u", "uses", "a"),
        edge("t", "uses", "d"),
        edge("l", "uses", "d"),
        edge("g1", "justifies", "u"),
        edge("g2", "justifies", "t"),
        edge("n", "uses", "a"),
        edge("n", "justifies", "l"),
    ]
    standings = [
        ("u", "If f is finite, f is bounded."),
        ("t", "Claim: f is small."),
        ("l", "Subclaim: f is tiny."),
    ]
    groups = [("g1", ["t", "x"]), ("g2", ["l", "y"])]
    found, block = _state(tmp_path, blocks, edges, standings, groups=groups)
    assert [(block[u.claim.node], block[u.premise]) for u in undeclared(found)] == []


def test_a_discharger_outside_the_proof_is_none_however_it_is_reached(
    tmp_path,
) -> None:
    """`x` justifies `a`, but only `a` reaches `x`: `x` is no part of `p`,
    so `a` is listed, and the item names `x`."""
    blocks = [
        ("d", "Let f be a map."),
        ("a", "Assume f is finite."),
        ("x", "Here f is finite."),
        ("t", "Then f is bounded."),
        ("p", "Since f is finite, f is bounded."),
    ]
    edges = [
        edge("a", "uses", "x"),
        edge("x", "justifies", "a"),
        edge("t", "uses", "d"),
        edge("p", "uses", "a"),
        edge("p", "justifies", "t"),
    ]
    found, _ = _state(tmp_path, blocks, edges, [("t", "Then f is bounded.")])
    (item,) = undeclared_assumptions(found)
    assert item.cause["cause"] == "discharged-elsewhere"
    assert "; x discharges it, outside p" in item.line


# --- a general set-up over a special one -------------------------------


def generalizing(source: str, target: str) -> dict[str, Any]:
    """An about edge from `source`, the wider premise, onto `target`, a
    case of it."""
    return {**edge(source, "about", target), "generalizes": True}


def _general(tmp_path, flagged: bool):
    """A text set-up: a countable space `n` and a law `d` defined on it,
    qualified by `q`; an appendix's general set-up `g` writing the law in
    general form (`g` uses `d`), flagged a generalization of it where
    `flagged`; a lemma `m` under `g`. The theorem `t` declares the law
    and the theorem `s` an unrelated constant `e`; both proofs apply the
    lemma."""
    blocks = [
        ("n", "Let the set of states be countable."),
        ("d", "The law weighs each state by its energy."),
        ("q", "Assume the weights sum to a finite total."),
        ("e", "Fix a positive constant."),
        ("g", "In general, take a measure space and the law as a density."),
        ("m", "Under this, the law is tight."),
        ("mp", "As the density integrates, the law is tight."),
        ("t", "Then the law concentrates."),
        ("p", "By the lemma, the law concentrates."),
        ("s", "Then the constant bounds the law."),
        ("sp", "By the lemma, the constant bounds the law."),
    ]
    edges = [
        edge("d", "uses", "n"),
        edge("q", "qualifies", "d"),
        edge("g", "uses", "d"),
        *([generalizing("g", "d")] if flagged else []),
        edge("m", "uses", "g"),
        edge("mp", "uses", "g"),
        edge("mp", "justifies", "m"),
        edge("t", "uses", "d"),
        edge("p", "uses", "m"),
        edge("p", "justifies", "t"),
        edge("s", "uses", "e"),
        edge("sp", "uses", "m"),
        edge("sp", "justifies", "s"),
    ]
    standings = [
        ("m", "Under this, the law is tight."),
        ("t", "Then the law concentrates."),
        ("s", "Then the constant bounds the law."),
    ]
    found, block = _state(
        tmp_path, blocks, edges, standings, assumptions=("n", "q", "g")
    )
    return {
        (
            block[u.claim.node],
            block[u.premise],
            None if u.through is None else block[u.through.node],
        )
        for u in undeclared(found)
    }


def test_declaring_a_special_set_up_declares_the_general_one_over_it(
    tmp_path,
) -> None:
    """`t` declares the law on the countable space; its proof applies a
    lemma under the general set-up `g`. Flagged as generalizing the law,
    `g` is declared by declaring the law: no item for `t`. Unflagged, `g`
    is a premise `t` leaves out."""
    flagged = _general(tmp_path / "flagged", flagged=True)
    plain = _general(tmp_path / "plain", flagged=False)
    assert ("t", "g", "m") not in flagged
    assert ("t", "g", "m") in plain
    assert not {x for x in flagged if x[0] == "t"}


def test_a_general_premise_takes_none_of_the_special_one_s_premises(
    tmp_path,
) -> None:
    """`s` declares nothing of the law, and its proof applies the lemma:
    flagged, the lemma rests on `g` alone, never on the countable space or
    the qualifier its uses edge onto the law would walk into."""
    assert {x for x in _general(tmp_path / "flagged", True) if x[0] == "s"} == {
        ("s", "g", "m")
    }
    assert {x for x in _general(tmp_path / "plain", False) if x[0] == "s"} == {
        ("s", "g", "m"),
        ("s", "n", "m"),
        ("s", "q", "m"),
    }


def _wider(tmp_path, stated: str, proved: str, paired: bool, flagged: bool = True):
    """A strict assumption `a` and a looser `w` written after it, flagged
    a generalization of it; `paired`: `w` also writes `a`'s symbols (a
    uses edge onto it). The theorem `t` states `stated`, its proof
    `proved`."""
    blocks = [
        ("a", "Assume f is bounded by one."),
        ("w", "More generally, assume f is bounded."),
        ("t", "Then f is integrable."),
        ("p", "As f is bounded, f is integrable."),
    ]
    edges = [
        *([generalizing("w", "a")] if flagged else []),
        *([edge("w", "uses", "a")] if paired else []),
        edge("t", "uses", stated),
        edge("p", "uses", proved),
        edge("p", "justifies", "t"),
    ]
    found, _ = _state(
        tmp_path, blocks, edges, [("t", "Then f is integrable.")], assumptions="aw"
    )
    return undeclared_assumptions(found)


@pytest.mark.parametrize("paired", [False, True])
def test_a_flagged_edge_declares_the_wider_premise_with_or_without_a_use(
    tmp_path, paired
) -> None:
    """The statement declares the strict `a`, the proof rests on the wider
    `w`: declared by the flag, whether or not `w` writes `a`'s symbols.
    Without the flag, `w` is listed: left out, or, where it uses `a`, read
    as narrowing it."""
    assert _wider(tmp_path / "flagged", "a", "w", paired) == []
    (item,) = _wider(tmp_path / "plain", "a", "w", paired, flagged=False)
    assert item.cause["cause"] == ("narrows" if paired else "left-out")


@pytest.mark.parametrize("paired", [False, True])
def test_a_j_resting_on_the_case_of_a_declared_premise_narrows_it(
    tmp_path, paired
) -> None:
    """The reverse: the statement declares the wider `w`, the proof rests
    on the strict `a`. Declaring the wider declares nothing narrower,
    even where `w` uses `a` (the looser statement that once declared what
    it used): one `narrows` item naming the edge."""
    (item,) = _wider(tmp_path, "w", "a", paired)
    assert item.blocks == ("t", "p", "a")
    assert item.cause["cause"] == "narrows"
    assert item.cause["premises"][0]["narrows"] == "w"
    assert item.cause["premises"][0]["case"] == "a"
    assert (
        "; w, which the statement declares, generalizes a (w → about a "
        "generalizes): the J rests on the narrower case"
    ) in item.line
    assert item.clears == (
        "the statement naming a (a uses edge onto it), or p relying on w in its place"
    )


def test_a_j_reaching_a_premise_through_a_case_narrows_too(tmp_path) -> None:
    """The case is a definition: the proof uses the law `d` the declared
    `g` generalizes, and through it rests on the space and the qualifier.
    Each item names the case on its path."""
    blocks = [
        ("n", "Let the set of states be countable."),
        ("d", "The law weighs each state by its energy."),
        ("q", "Assume the weights sum to a finite total."),
        ("g", "In general, take a measure space and the law as a density."),
        ("t", "Then the law concentrates."),
        ("p", "As the weights sum, the law concentrates."),
    ]
    edges = [
        edge("d", "uses", "n"),
        edge("q", "qualifies", "d"),
        edge("g", "uses", "d"),
        generalizing("g", "d"),
        edge("t", "uses", "g"),
        edge("p", "uses", "d"),
        edge("p", "justifies", "t"),
    ]
    found, _ = _state(
        tmp_path,
        blocks,
        edges,
        [("t", "Then the law concentrates.")],
        assumptions=("n", "q", "g"),
    )
    items = undeclared_assumptions(found)
    assert sorted(i.blocks[-1] for i in items) == ["n", "q"]
    assert {(i.cause["cause"], i.cause["premises"][0]["case"]) for i in items} == {
        ("narrows", "d")
    }


def _set_up(tmp_path, flags: list[tuple[str, str]]):
    """A general set-up whose items use each other in a circle (each
    writes the others' symbols), a lemma `l` under it, and a theorem `t`
    declaring the special `a` (an assumption) and `d` (a definition
    carrying the special space and total), its proof applying the lemma;
    `flags` the `generalizes` edges, wider onto narrower."""
    blocks = [
        ("a", "Assume the energy is lower semicontinuous."),
        ("d", "The space is countable and the total is finite."),
        ("g1", "Let the space be Polish."),
        ("g2", "Let the energy on it be lower semicontinuous."),
        ("g3", "Assume the total is finite at some temperature."),
        ("l", "Under these, the law is tight."),
        ("lp", "As the space is Polish, the law is tight."),
        ("t", "Then the law concentrates."),
        ("p", "By the lemma, the law concentrates."),
    ]
    edges = [
        edge("g1", "uses", "g2"),
        edge("g2", "uses", "g3"),
        edge("g3", "uses", "g1"),
        *(generalizing(wide, narrow) for wide, narrow in flags),
        edge("l", "uses", "g1"),
        edge("lp", "uses", "g1"),
        edge("lp", "justifies", "l"),
        edge("t", "uses", "a"),
        edge("t", "uses", "d"),
        edge("p", "uses", "l"),
        edge("p", "justifies", "t"),
    ]
    standings = [("l", "Under these, the law is tight."), ("t", blocks[-2][1])]
    found, _ = _state(
        tmp_path, blocks, edges, standings, assumptions=("a", "g1", "g2", "g3")
    )
    return undeclared_assumptions(found)


def test_one_flagged_edge_declares_its_own_premise_never_its_walk(
    tmp_path,
) -> None:
    """The general set-up's items use each other in a circle; only `g2`
    generalizes what the statement declares. That declares `g2` alone: the
    edge says the special case implies `g2`, nothing of the space and the
    total `g2`'s words write, so `g1` and `g3` stay listed."""
    items = _set_up(tmp_path, [("g2", "a")])
    assert [(i.blocks[-1], i.cause["cause"]) for i in items] == [
        ("g1", "narrows"),
        ("g3", "left-out"),
    ]
    assert items[0].cause["premises"][0]["narrows"] == "g2"
    assert "a `generalizes` edge from g3 onto what the statement declares" in (
        items[1].clears
    )


def test_each_general_item_with_its_own_edge_is_declared(tmp_path) -> None:
    """Each item of the general set-up generalizes a special one the
    statement declares, the space and the total by the definition that
    carries them: nothing is listed."""
    assert _set_up(tmp_path, [("g2", "a"), ("g1", "d"), ("g3", "d")]) == []


def test_a_generalization_is_no_copy(tmp_path) -> None:
    """A `generalizes` edge joins no two premises: the statement declaring
    the wider `c` never declares the strict `a` the proof rests on, where
    a plain about edge (a copy) would."""
    assert _copied(tmp_path / "copy", "about", "c", "a") == []
    assert _copied(tmp_path / "wider", "about", "c", "a", generalizes=True) == [
        ("t", "a")
    ]


def test_a_case_on_a_lemma_s_statement_names_the_lemma_in_the_fix(tmp_path) -> None:
    """The statement declares the wider `w`; the proof applies a lemma whose
    statement rests on the strict `a`: the fix names the lemma's statement,
    not the proof."""
    blocks = [
        ("a", "Assume f is bounded by one."),
        ("w", "More generally, assume f is bounded."),
        ("l", "Then f is small."),
        ("q", "As f is bounded by one, f is small."),
        ("t", "Then f is integrable."),
        ("p", "By the lemma, f is integrable."),
    ]
    edges = [
        generalizing("w", "a"),
        edge("l", "uses", "a"),
        edge("q", "uses", "a"),
        edge("q", "justifies", "l"),
        edge("t", "uses", "w"),
        edge("p", "uses", "l"),
        edge("p", "justifies", "t"),
    ]
    standings = [("l", "Then f is small."), ("t", "Then f is integrable.")]
    found, _ = _state(tmp_path, blocks, edges, standings, assumptions="aw")
    (item,) = undeclared_assumptions(found)
    assert item.cause["premises"][0]["case"] == "a"
    assert item.clears.endswith("or l relying on w in its place")
