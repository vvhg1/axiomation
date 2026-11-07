"""The exchange format: its document half and its
node half."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from axiomation.assembler import corrected_cut
from axiomation.cutter import CutRules, DisplayRule, cut
from axiomation.exchange import (
    FORMAT,
    VERSION,
    ExchangeError,
    corrected_payload,
    correction_payload,
    cut_from_payload,
    cut_payload,
    document_payload,
    dumps,
    offset,
    position,
    update_review,
    vocabulary_of,
)
from axiomation.reader import (
    EnvEntry,
    MapAnswer,
    RefEntry,
    Vocabulary,
    VocabularyKind,
    read_paper,
)
from axiomation.records import CorrectionClass, CutCorrection, CutOp

MAIN = (
    "\\documentclass{article}\n"
    "\\newtheorem{thm}{Theorem}\n"
    "\\title{A café result}\n"
    "\\begin{document}\n"
    "\\maketitle\n"
    "\\section{One}\\label{sec:one}\n"
    "\\input{part}\n"
    "\\end{document}\n"
)
PART = (
    "\\begin{thm}\\label{thm:a}\n"
    "It holds, by Section~\\ref{sec:one} and \\cite[p.~2]{knuth}.% a note\n"
    "\\end{thm}\n"
    "% café au lait\n"
)


def paper(root: Path) -> Path:
    root.mkdir()
    (root / "main.tex").write_text(MAIN, encoding="utf-8")
    (root / "part.tex").write_text(PART, encoding="utf-8")
    return root


def test_the_same_paper_gives_the_same_bytes(tmp_path: Path) -> None:
    directory = paper(tmp_path / "p")

    first = dumps(document_payload(read_paper(directory)))
    second = dumps(document_payload(read_paper(directory)))

    assert first == second
    assert first.endswith("}\n") and not first.endswith("\n\n")


def test_the_header_names_the_format_the_version_and_the_root(tmp_path) -> None:
    payload = json.loads(dumps(document_payload(read_paper(paper(tmp_path / "p")))))

    assert list(payload) == ["format", "version", "paper", "document"]
    assert payload["format"] == FORMAT == "axiomation-exchange"
    assert payload["version"] == VERSION == 1
    assert payload["paper"] == {"root": "main.tex", "format": "latex"}
    assert [entry["path"] for entry in payload["document"]["files"]] == [
        "main.tex",
        "part.tex",
    ]


def test_no_absolute_path_is_written(tmp_path: Path) -> None:
    directory = paper(tmp_path / "p")

    text = dumps(document_payload(read_paper(directory)))

    assert str(tmp_path) not in text
    assert str(directory.resolve()) not in text


def test_text_outside_ascii_is_written_as_itself(tmp_path: Path) -> None:
    text = dumps(document_payload(read_paper(paper(tmp_path / "p"))))

    assert '"text": " café au lait"' in text
    assert "\\u00e9" not in text


def test_a_span_is_line_and_column_and_comes_back_to_its_offset(tmp_path) -> None:
    document = read_paper(paper(tmp_path / "p"))
    payload = document_payload(document)
    reference = payload["document"]["references"][0]
    source = document.file("part.tex")

    assert reference["at"] == {"file": "part.tex", "start": [2, 22], "end": [2, 35]}
    start = offset(source.text, *reference["at"]["start"])
    end = offset(source.text, *reference["at"]["end"])
    assert source.text[start:end] == "\\ref{sec:one}"
    assert document.references[0].at.start == start


def test_position_and_offset_are_inverse() -> None:
    text = "ab\n\ncd é\nlast"

    for index in range(len(text) + 1):
        assert offset(text, *position(text, index)) == index
    assert position(text, 0) == [1, 1]
    assert position(text, 3) == [2, 1]
    assert position(text, len(text)) == [4, 5]


def test_a_file_s_hidden_spans_carry_no_file(tmp_path: Path) -> None:
    payload = document_payload(read_paper(paper(tmp_path / "p")))
    part = payload["document"]["files"][1]

    comment = next(entry for entry in part["hidden"] if entry["kind"] == "comment")
    assert comment == {"kind": "comment", "start": [2, 59], "end": [2, 67]}


# --- The node half ---


def cut_of(directory: Path, displays: DisplayRule = DisplayRule.OWN_BLOCK):
    document = read_paper(directory)
    return document, cut(document, CutRules(displays))


def test_the_same_cut_gives_the_same_bytes(tmp_path: Path) -> None:
    directory = paper(tmp_path / "p")

    first = dumps(cut_payload(*cut_of(directory)))
    second = dumps(cut_payload(*cut_of(directory)))

    assert first == second


def test_the_node_half_follows_the_document_half(tmp_path: Path) -> None:
    document, found = cut_of(paper(tmp_path / "p"), DisplayRule.IN_PARAGRAPH)
    payload = json.loads(dumps(cut_payload(document, found)))

    assert list(payload) == [
        "format",
        "version",
        "paper",
        "document",
        "rules",
        "blocks",
        "parts",
        "groups",
        "candidates",
    ]
    assert (
        payload["document"] == json.loads(dumps(document_payload(document)))["document"]
    )
    assert payload["rules"] == {"displays": "in-paragraph"}


def test_a_block_s_fields(tmp_path: Path) -> None:
    document, found = cut_of(paper(tmp_path / "p"))
    payload = cut_payload(document, found)

    title, heading, statement = payload["blocks"]
    assert title["form"] == "title" and heading["form"] == "heading"
    assert statement == {
        "id": "part.tex:1:1",
        "extent": {"file": "part.tex", "start": [1, 1], "end": [3, 10]},
        "owned": [
            {"file": "part.tex", "start": [1, 1], "end": [1, 12]},
            {"file": "part.tex", "start": [2, 1], "end": [2, 59]},
            {"file": "part.tex", "start": [3, 1], "end": [3, 10]},
        ],
        "within": None,
        "form": "statement",
        "environment": "thm",
        "meaning": "theorem",
        "roles": ["claim"],
        "segments": [{"starts": "", "role": "claim"}],
        "hash": found.blocks[2].hash,
        "doubts": [],
    }


def test_candidates_and_groups_are_written_with_their_enums(tmp_path: Path) -> None:
    directory = tmp_path / "q"
    directory.mkdir()
    (directory / "paper.md").write_text(
        "# S\n\n\\begin{lemma}\\label{lem:a}\nA.\n\\end{lemma}\n\n"
        "\\begin{proof}\nBy \\ref{lem:a} and \\cite{knuth}.\n\nHence A.\n"
        "\\end{proof}\n",
        encoding="utf-8",
    )
    document, found = cut_of(directory)
    payload = cut_payload(document, found)

    (group,) = payload["groups"]
    assert group == {
        "id": "proof@paper.md:7:1",
        "kind": "proof",
        "blocks": ["paper.md:7:1", "paper.md:10:1"],
        "names": "paper.md:3:1",
        "named_label": "lem:a",
        "named_by": "position",
        "role": "justification",
    }
    reference, citation = payload["candidates"]
    assert reference == {
        "source": "paper.md:7:1",
        "kind": "reference",
        "command": "ref",
        "target": "lem:a",
        "at": {"file": "paper.md", "start": [8, 4], "end": [8, 15]},
        "resolved": "paper.md:3:1",
    }
    assert (citation["kind"], citation["target"], citation["resolved"]) == (
        "citation",
        "knuth",
        None,
    )
    assert payload["parts"] == []


# --- the corrected cut -------------------------------------------


def _split_of(document, found) -> CutCorrection:
    """A split of the theorem before its reference."""
    [block] = [
        b
        for b in found.blocks
        if "by Section" in " ".join(document.text(run) for run in b.owned)
    ]
    return CutCorrection(
        id="C1",
        op=CutOp.SPLIT,
        cls=CorrectionClass.ARGUMENT_BOUNDARY,
        why="a test",
        block=block.id,
        at=("by Section",),
    )


def test_corrected_payload_with_no_correction_is_the_cut_plus_producer(
    tmp_path: Path,
) -> None:
    document, found = cut_of(paper(tmp_path / "p"))

    payload = corrected_payload(document, corrected_cut(document, found, []), "t")

    expected = cut_payload(document, found)
    assert list(payload) == [
        "format", "version", "paper", "producer", "document", "rules",
        "blocks", "parts", "groups", "candidates", "review",
    ]  # fmt: skip
    assert payload["producer"] == "t"
    assert payload["review"] == {"applied": [], "problems": []}
    del payload["producer"], payload["review"]
    assert payload == expected


def test_corrected_payload_carries_the_review_and_its_problems(
    tmp_path: Path,
) -> None:
    document, found = cut_of(paper(tmp_path / "p"))
    split = _split_of(document, found)
    absent = CutCorrection(
        id="C2",
        op=CutOp.UNGROUP,
        cls=CorrectionClass.OTHER,
        why="a test",
        group="proof@nowhere",
    )
    corrected = corrected_cut(document, found, [split, absent])

    payload = corrected_payload(
        document, corrected, "axiomation review-cut", {"questions": 3, "cost": 0.01}
    )

    assert payload["review"] == {
        "questions": 3,
        "cost": 0.01,
        "applied": ["C1"],
        "problems": [
            {"correction": "C2", "message": "group proof@nowhere does not exist"}
        ],
    }
    assert len(payload["blocks"]) == len(found.blocks) + 1
    assert "answer_key" not in payload


def test_corrected_payload_is_stable_and_holds_no_absolute_path(
    tmp_path: Path,
) -> None:
    directory = paper(tmp_path / "p")

    def written() -> str:
        document, found = cut_of(directory)
        corrected = corrected_cut(document, found, [_split_of(document, found)])
        return dumps(corrected_payload(document, corrected, "t", {"n": 1}))

    first = written()
    assert written() == first
    assert str(tmp_path) not in first
    assert str(directory.resolve()) not in first


# --- reading a cut back (`review-cut --cut`) ------------------------------


def _without_roles(found):
    return replace(
        found,
        blocks=tuple(replace(b, roles=(), segments=()) for b in found.blocks),
    )


def test_a_cut_reads_back_as_itself_less_its_roles(tmp_path: Path) -> None:
    document, found = cut_of(paper(tmp_path / "p"), DisplayRule.IN_PARAGRAPH)
    assert any(b.roles for b in found.blocks)
    written = json.loads(dumps(cut_payload(document, found)))

    back = cut_from_payload(document, written, CutRules())

    assert back == _without_roles(found)
    assert back.rules.displays is DisplayRule.IN_PARAGRAPH  # the payload's rule


def test_a_cut_reads_back_with_its_roles_when_asked(tmp_path: Path) -> None:
    """The groups stage alone is asked on a cut whose roles are settled:
    its roles and segments are kept."""
    document, found = cut_of(paper(tmp_path / "p"), DisplayRule.IN_PARAGRAPH)
    assert any(b.roles for b in found.blocks)
    written = json.loads(dumps(cut_payload(document, found)))

    back = cut_from_payload(document, written, CutRules(), roles=True)

    assert back == found


def test_a_key_s_cut_reads_back_without_hash_doubts_candidates_or_rules(
    tmp_path: Path,
) -> None:
    """A node key's `cut.json` writes none of these: the hash is computed,
    the doubts are none, the candidates are found again, and the rules are
    the caller's."""
    document, found = cut_of(paper(tmp_path / "p"))
    written = json.loads(dumps(cut_payload(document, found)))
    for block in written["blocks"]:
        del block["hash"], block["doubts"]
    del written["candidates"], written["rules"]

    back = cut_from_payload(document, written, CutRules(DisplayRule.OWN_BLOCK))

    expected = _without_roles(found)
    assert back == replace(
        expected, blocks=tuple(replace(b, doubts=()) for b in expected.blocks)
    )


def test_a_cut_of_another_text_is_refused(tmp_path: Path) -> None:
    document, found = cut_of(paper(tmp_path / "p"))
    written = json.loads(dumps(cut_payload(document, found)))
    written["document"]["files"][1]["sha256"] = "0" * 64

    with pytest.raises(ExchangeError, match="not the paper's: part.tex"):
        cut_from_payload(document, written, CutRules())


@pytest.mark.parametrize(
    "spoil",
    [
        lambda p: p.update(format="other"),
        lambda p: p.pop("blocks"),
        lambda p: p["blocks"][0].update(form="sonnet"),
    ],
)
def test_a_payload_that_is_not_a_cut_is_refused(tmp_path: Path, spoil) -> None:
    document, found = cut_of(paper(tmp_path / "p"))
    written = json.loads(dumps(cut_payload(document, found)))
    spoil(written)

    with pytest.raises(ExchangeError):
        cut_from_payload(document, written, CutRules())


# --- the edge half ---------------------------------------------------


def _edges_of_the_synthetic_paper(tmp_path: Path):
    """The review paper's written sheet, checked and recorded."""
    from axiomation.questions.edges_file import check, session_question
    from axiomation.records import RecordStore
    from axiomation.resolver import Attempt, Resolver
    from questions.review_paper import make_paper
    from questions.test_edges_file import FILES, SESSION, written
    from questions.test_roles_file import first_pass
    from runner.fakes import as_lines

    paper = make_paper(tmp_path / "paper")
    cut = first_pass(paper)
    question = session_question(paper.document, cut, SESSION, FILES)
    lines = as_lines(written(question.workspace.files["edges.md"]))
    found = check(question, lines, paper.document)
    assert found.valid, found.problems
    # the runner keeps the sheet the check laid the lines into
    recorded = Resolver(RecordStore(tmp_path / "store")).record(
        Attempt(question, "fake", "fake/model", found.kept, 0.0, 0.0, found)
    )
    return paper, cut, question, recorded.edges, recorded.answer


def test_the_edge_half_places_every_excerpt_and_names_entries_by_handle(
    tmp_path: Path,
) -> None:
    from axiomation.exchange import edges_payload

    paper, cut, question, records, asked = _edges_of_the_synthetic_paper(tmp_path)

    data = edges_payload(paper.document, cut, records, asked, question.handles)

    assert list(data) == [
        "entries",
        "edges",
        "self",
        "standings",
        "ambient",
        "handles",
        "edges_answer",
    ]
    (entry,) = data["entries"]
    assert entry["id"] == "def:gain [gain]" and entry["role"] == "definition"
    uses = data["edges"][1]
    assert uses["target"] == {
        "block": paper.id_at("\\begin{definition}"),
        "entries": ["def:gain [gain]"],
    }
    assert uses["mark"] == "ref" and "where_used" not in uses
    for item in [*data["edges"], *data["self"]]:
        for evidence in item["evidence"]:
            text = paper.document.file(evidence["at"]["file"]).text
            start, end = (
                offset(text, *evidence["at"]["start"]),
                offset(text, *evidence["at"]["end"]),
            )
            assert " ".join(text[start:end].split()) == " ".join(
                evidence["text"].split()
            )
    justifies = next(e for e in data["edges"] if e["kind"] == "justifies")
    assert justifies["source"] == cut.groups[0].id  # placed over the group's blocks
    assert {"scope": "paper"} in [e["target"] for e in data["edges"]]
    assert data["handles"][cut.groups[0].id] == "group:by-definition-def:gain-the"
    assert dumps(data) == dumps(
        edges_payload(paper.document, cut, records, asked, question.handles)
    )


def test_the_edge_half_carries_a_generalizes_flag_only_when_set(
    tmp_path: Path,
) -> None:
    from axiomation.exchange import edges_payload

    paper, cut, question, records, asked = _edges_of_the_synthetic_paper(tmp_path)
    flagged = replace(
        records, edges=(replace(records.edges[0], generalizes=True), *records.edges[1:])
    )

    data = edges_payload(paper.document, cut, flagged, asked, question.handles)

    assert data["edges"][0]["generalizes"] is True
    assert not any("generalizes" in e for e in data["edges"][1:])


def test_the_edge_half_carries_a_line_restating_none_only_when_set(
    tmp_path: Path,
) -> None:
    from axiomation.exchange import edges_payload

    paper, cut, question, records, asked = _edges_of_the_synthetic_paper(tmp_path)
    flagged = replace(
        records,
        edges=(replace(records.edges[0], restates_none=True), *records.edges[1:]),
    )

    data = edges_payload(paper.document, cut, flagged, asked, question.handles)

    assert data["edges"][0]["restates_none"] is True
    assert not any("restates_none" in e for e in data["edges"][1:])


def test_the_edge_half_carries_a_partial_and_a_declaration_only_when_set(
    tmp_path: Path,
) -> None:
    """`partial` on an edge and a self mark, `declared` on a
    standing, written only when set, so a graph without them exports as
    before (the test above: no key holds them)."""
    from axiomation.exchange import edges_payload
    from axiomation.records import Declared, Partial, Status

    paper, cut, question, records, asked = _edges_of_the_synthetic_paper(tmp_path)
    plain = edges_payload(paper.document, cut, records, asked, question.handles)
    assert not any(
        "partial" in item for item in [*plain["edges"], *plain["self"]]
    ) and not any("declared" in s for s in plain["standings"])
    j = next(n for n, e in enumerate(records.edges) if e.kind.value == "justifies")
    edges = list(records.edges)
    edges[j] = replace(edges[j], partial=Partial.ANALOGY)
    marked = replace(
        records,
        edges=tuple(edges),
        selfs=(replace(records.selfs[0], partial=Partial.SKETCH), *records.selfs[1:]),
        standings=(
            replace(
                records.standings[0],
                status=Status.UNSUPPORTED,
                declared=Declared.OMITTED,
            ),
            *records.standings[1:],
        ),
    )
    data = edges_payload(paper.document, cut, marked, asked, question.handles)
    assert data["edges"][j]["partial"] == "analogy"
    assert data["self"][0]["partial"] == "sketch"
    assert data["standings"][0]["declared"] == "omitted"


def test_an_excerpt_not_in_its_block_is_refused(tmp_path: Path) -> None:
    from axiomation.exchange import edges_payload

    paper, cut, question, records, asked = _edges_of_the_synthetic_paper(tmp_path)
    moved = replace(records.edges[0], evidence=("Closing words",))
    broken = replace(records, edges=(moved, *records.edges[1:]))

    with pytest.raises(ExchangeError, match="'Closing words' is not in"):
        edges_payload(paper.document, cut, broken, asked, question.handles)


def test_a_proposal_places_its_words_and_says_where_one_is_not(
    tmp_path: Path,
) -> None:
    """The words and evidence placed in the blocks named, the
    blocks before with their owned spans; a placement that fails is the
    entry's problem."""
    document, found = cut_of(paper(tmp_path / "p"))
    block = next(b for b in found.blocks if "It holds" in document.text(b.extent))
    proposal = CutCorrection(
        id="P1",
        op=CutOp.SPLIT,
        cls=CorrectionClass.OTHER,
        why="two claims",
        block=block.id,
        at=("by Section",),
        evidence=("It holds", "not in the block"),
    )

    entry = correction_payload(
        document,
        proposal,
        round_=1,
        stage="edges",
        applied=True,
        problem=None,
        before=(block,),
        after=(block.id, "part.tex:2:11"),
    )

    assert entry["at"] == [
        {
            "text": "by Section",
            "at": {"file": "part.tex", "start": [2, 11], "end": [2, 21]},
        }
    ]
    assert entry["evidence"][0]["at"]["start"] == [2, 1]
    assert entry["evidence"][1] == {"text": "not in the block", "at": None}
    assert entry["problem"] == (
        "not placed: 'not in the block' is not in one run of its block's text"
    )
    assert entry["changed"] == {
        "before": [
            {
                "id": block.id,
                "owned": [
                    {"file": "part.tex", "start": [1, 1], "end": [1, 12]},
                    {"file": "part.tex", "start": [2, 1], "end": [2, 59]},
                    {"file": "part.tex", "start": [3, 1], "end": [3, 10]},
                ],
            }
        ],
        "after": [block.id, "part.tex:2:11"],
    }


def test_a_line_is_the_answer_s_that_first_wrote_it_without_a_break(
    tmp_path: Path,
) -> None:
    """Walking back from the edge half's answer, an
    edge, a self mark or a standing is the earliest answer's that held it
    without a break; one a later answer wrote again after a gap is the
    later one's; a re-quote names the same line, a standing's new status a
    new one."""
    import dataclasses

    from axiomation.exchange import (
        answer_link,
        edges_payload,
        line_authors,
        line_keys,
        provenance,
    )
    from axiomation.records import Status

    paper, cut, question, records, asked = _edges_of_the_synthetic_paper(tmp_path)
    first = dataclasses.replace(asked, key="1" * 64, host="person", model="person")
    second = dataclasses.replace(asked, key="2" * 64)
    last = dataclasses.replace(asked, key="3" * 64)
    gone, kept = records.edges[0], records.edges[1:]
    (standing, *others) = records.standings
    assert records.selfs and standing.status is not Status.UNSUPPORTED
    # the second answer dropped `gone`; the last wrote it again, re-quoted,
    # re-quoted the self marks and gave the first standing another status
    without = dataclasses.replace(records, edges=tuple(kept))
    rewritten = dataclasses.replace(
        records,
        edges=(
            dataclasses.replace(gone, evidence=(gone.evidence[0].split()[0],)),
            *kept,
        ),
        selfs=tuple(
            dataclasses.replace(m, evidence=(m.evidence[0].split()[0],))
            for m in records.selfs
        ),
        standings=(
            dataclasses.replace(standing, status=Status.UNSUPPORTED),
            *others,
        ),
    )

    found = line_authors(
        rewritten,
        [
            answer_link(records, first),
            answer_link(without, second),
            answer_link(rewritten, last),
        ],
    )

    assert found["edges"] == [provenance(last), *(provenance(first) for _ in kept)]
    assert found["self"] == [provenance(first) for _ in records.selfs]
    assert found["standings"] == [
        provenance(last),
        *(provenance(first) for _ in others),
    ]
    assert {a["by"] for a in found["edges"][1:]} == {"person"}
    assert line_keys(records)["self"] == line_keys(rewritten)["self"]
    data = edges_payload(paper.document, cut, rewritten, last, question.handles, found)
    assert data["edges_answer"] == last.key
    assert [e["provenance"]["question"] for e in data["edges"]] == [
        last.key,
        *(first.key for _ in kept),
    ]
    assert [x["provenance"]["question"] for x in data["standings"]] == [
        last.key,
        *(first.key for _ in others),
    ]
    assert {x["provenance"]["question"] for x in data["self"]} == {first.key}


def test_the_vocabulary_map_a_document_was_read_with_reads_back(tmp_path) -> None:
    paper = tmp_path / "paper"
    paper.mkdir()
    (paper / "paper.md").write_text("Text.\n", encoding="utf-8")
    mapped = Vocabulary(
        environments=(EnvEntry("lawbox", VocabularyKind.CONTAINER),),
        references=(RefEntry("lemref", True, "lem:", "Lemma"),),
        labels=("customlabel",),
    )

    plain = document_payload(read_paper(paper))
    payload = document_payload(read_paper(paper, vocabulary=mapped))

    assert "vocabulary" not in plain["document"]
    assert vocabulary_of(plain) == Vocabulary()
    assert vocabulary_of(json.loads(json.dumps(payload))) == mapped
    answered = Vocabulary.of(
        [
            MapAnswer("environment", "lawbox", "\\newmdenv{lawbox}", "container", "x"),
            MapAnswer("command", "emph", None, "none", "y"),
        ]
    )
    again = document_payload(read_paper(paper, vocabulary=answered))
    # a map's answers read back whole, a command's `none` included
    assert vocabulary_of(json.loads(json.dumps(again))) == answered


def test_an_update_s_record_lists_what_it_lost_only_where_any() -> None:
    """`review.update` names each corrected boundary and each group the
    rebase could not carry; an update that lost none writes no key, so
    earlier exports replay byte for byte."""
    base = dict(
        old="a", new="b", previous={}, reopened=(), deltas=(), hints=(), cost={}
    )
    assert "corrections_lost" not in update_review(**base)
    lost = {
        "block": "p.md:3:1",
        "op": "split",
        "words": "Taking g",
        "why": "its words changed",
    }
    found = update_review(**base, corrections_lost=[{**lost, "extra": 1}])
    assert found["corrections_lost"] == [lost]
    assert "groups_lost" not in found
    group = {"group": "proof@p.md:9:1", "why": "run"}
    found = update_review(**base, groups_lost=[{**group, "extra": 1}])
    assert found["groups_lost"] == [group] and "corrections_lost" not in found


def test_an_assumed_flag_is_its_writer_s_line(tmp_path: Path) -> None:
    """A person's edge an agent's later answer flags `assumed` is that
    answer's line, as is the flag taken off again: who wrote the flag is
    who speaks for the hypothesis it lends. The flag is in the edge's
    name: it says what the edge relies on."""
    import dataclasses

    from axiomation.exchange import (
        answer_link,
        edge_key,
        edges_payload,
        line_authors,
        part_words,
        provenance,
    )

    paper, cut, question, records, asked = _edges_of_the_synthetic_paper(tmp_path)
    person = dataclasses.replace(asked, key="1" * 64, host="person", model="person")
    agent = dataclasses.replace(asked, key="2" * 64)
    later = dataclasses.replace(asked, key="3" * 64, host="person", model="person")
    first, *rest = records.edges
    flagged = dataclasses.replace(
        records, edges=(dataclasses.replace(first, assumed=True), *rest)
    )
    chain = [answer_link(records, person), answer_link(flagged, agent)]
    assert line_authors(flagged, chain)["edges"][0] == provenance(agent)
    chain.append(answer_link(records, later))
    assert line_authors(records, chain)["edges"][0] == provenance(later)
    words = part_words(records)
    assert edge_key(first, words) != edge_key(flagged.edges[0], words)
    data = edges_payload(paper.document, cut, flagged, agent, question.handles)
    assert data["edges"][0]["assumed"] is True
    assert not any("assumed" in e for e in data["edges"][1:])


def test_an_update_carries_the_author_of_an_edge_naming_claims() -> None:
    """OLD's edges, named on NEW by one-to-one links, keep the author OLD's
    export names: an about edge naming its source's claim, and an assumed
    edge, by the same name `line_keys` gives them."""
    from types import SimpleNamespace

    from axiomation.exchange import line_key_from_json, line_keys
    from axiomation.questions.edges_file import records
    from axiomation.update import carried_origins

    sheet = "\n".join(
        [
            "⟦k claim⟧ main.tex:1:1 (prose)",
            "K may hold.",
            '⊢ conjectured · "K may hold."',
            "⟦done k⟧",
            "⟦t claim⟧ main.tex:2:1 (prose)",
            "T holds.",
            '⊢ justified · "T holds."',
            "⟦done t⟧",
            "⟦x claim⟧ main.tex:3:1 (prose)",
            "Assuming K, X holds. So T holds.",
            '→ uses [x-holds] k assumed · "Assuming K"',
            '→ about [so-t-holds] t · "So T holds."',
            '⊢ unsupported [x-holds] · "X holds."',
            '⊢ restated [so-t-holds] · "So T holds."',
            "⟦done x⟧",
        ]
    )
    carried = records(sheet, {"k": "B1", "t": "B2", "x": "B3"})
    person = {"by": "person", "model": None, "question": "q", "detail": {}}
    exported = {
        "entries": [],
        "edges": [
            {
                "source": "A3",
                "kind": "uses",
                "target": {"block": "A1"},
                "source_claims": ["X holds."],
                "assumed": True,
                "provenance": person,
            },
            {
                "source": "A3",
                "kind": "about",
                "target": {"block": "A2"},
                "source_claims": ["So T holds."],
                "provenance": person,
            },
        ],
    }
    links = {"A1": "B1", "A2": "B2", "A3": "B3"}
    lineage = SimpleNamespace(
        link_of_old=lambda ident: SimpleNamespace(new=(links[ident],))
    )
    found = carried_origins(exported, lineage, carried, SimpleNamespace(groups=[]))
    keys = [line_key_from_json(o["key"]) for o in found]
    assert keys == line_keys(carried)["edges"]
    assert all(o["provenance"] == person for o in found)
