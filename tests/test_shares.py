"""A share: the graph at a kept state, given to a reader outside the
record, sealed to them and signed by the author."""

from __future__ import annotations

import re
import shutil
from dataclasses import replace
from pathlib import Path

import pytest

from axiomation import keys, records, shares, versions
from axiomation.cli import main
from axiomation.commands import Say, UsageError, seating
from axiomation.commands.seating import Asking
from axiomation.exchange import dumps
from axiomation.records import RECORD, RecordStore
from test_keys import filing
from test_queries import rich  # noqa: F401 (a fixture)

pytestmark = pytest.mark.signed


@pytest.fixture
def journal(tmp_path: Path) -> keys.Keys:
    return keys.made(tmp_path / "journal")[0]


def _share(store: RecordStore, journal: keys.Keys, *more: str) -> int:
    paper = store.directory.parent / "paper"
    return main(
        [
            "share",
            str(paper),
            "--store",
            str(store.directory),
            "--to",
            keys.card(journal.member),
            *more,
        ]
    )


def test_a_share_opens_for_its_reader_as_the_state_s_graph(
    rich,  # noqa: F811
    journal: keys.Keys,
    tmp_path: Path,
    capsys,
) -> None:
    view, _, payload = rich
    store, kept = view.store, view.state
    out = tmp_path / "round1.share"

    assert _share(store, journal, "-o", str(out)) == 0
    said = capsys.readouterr().out
    assert f"share round 1 to {journal.id}" in said
    assert "each line's model, answer, stage and template" in said

    opened = shares.opened(out, journal, keys.mine().member)
    found = opened.share
    assert opened.writer.id == keys.mine().id
    assert found.nodes == {n.block: n.node for n in kept.nodes}
    assert (found.round, found.continues, found.state) == (1, None, kept.id)
    assert found.files == {p: versions.blob(store, b) for p, b in kept.files.items()}
    assert found.export["blocks"] == payload["blocks"]
    assert "edges_answer" not in found.export
    assert {e["provenance"]["by"] for e in found.export["edges"]} == {shares.AGENT}
    assert all(list(e["provenance"]) == ["by"] for e in found.export["edges"])

    # the record keeps its sending; the next round continues it
    (sent,) = RecordStore(store.directory).sent()
    assert (sent.round, sent.reader, sent.state) == (1, journal.id, kept.id)
    assert shares.next_round(RecordStore(store.directory), journal.id) == (
        2,
        opened.entry,
    )
    assert sent.share == opened.entry
    # the share's text never enters the record
    assert out.read_text().splitlines()[1] not in (store.directory / RECORD).read_text()


def test_a_preview_lists_and_writes_nothing(
    rich,  # noqa: F811
    journal: keys.Keys,
    tmp_path: Path,
    capsys,
) -> None:
    store = rich[0].store
    before = (store.directory / RECORD).read_bytes()

    assert _share(store, journal, "--preview") == 0
    said = capsys.readouterr().out
    assert "it gives:" in said and "it withholds:" in said
    assert (store.directory / RECORD).read_bytes() == before
    assert not (store.directory / "shares").exists()

    # the author sees it as a reviewer would: a workspace the viewer opens
    into = tmp_path / "preview"
    assert _share(store, journal, "--preview", "--into", str(into)) == 0
    assert f"axiomation view {into}" in capsys.readouterr().out
    (kept,) = versions.states(RecordStore(into / "axiomation"))
    assert kept.nodes == rich[0].state.nodes
    assert (store.directory / RECORD).read_bytes() == before


def test_a_share_opens_for_no_one_else_and_holds_only_as_signed(
    rich,  # noqa: F811
    journal: keys.Keys,
    tmp_path: Path,
) -> None:
    store = rich[0].store
    out = tmp_path / "round1.share"
    assert _share(store, journal, "-o", str(out)) == 0
    author = keys.mine().member

    stranger = keys.made(tmp_path / "stranger")[0]
    with pytest.raises(shares.ShareError, match="no grant"):
        shares.opened(out, stranger, author)
    # the author opens their own share
    assert shares.opened(out, keys.mine(), author).share.round == 1
    # a reader holding another's card for the author refuses it
    with pytest.raises(shares.ShareError, match="whose card you hold"):
        shares.opened(out, journal, stranger.member)

    # a body changed is caught by its seal
    grant, entry, confirmation = out.read_text().splitlines()
    cut = entry.rindex('"}')
    changed = (
        entry[: cut - 8] + ("A" if entry[cut - 8] != "A" else "B") + entry[cut - 7 :]
    )
    tampered = tmp_path / "tampered.share"
    tampered.write_text("\n".join([grant, changed, confirmation]) + "\n")
    with pytest.raises(shares.ShareError, match="does not open"):
        shares.opened(tampered, journal, author)

    # a share the confirmation of which is left out is not signed
    unsigned = tmp_path / "unsigned.share"
    unsigned.write_text("\n".join([grant, entry]) + "\n")
    with pytest.raises(shares.ShareError, match="not a share file"):
        shares.opened(unsigned, journal, author)


def test_a_share_signed_by_another_key_in_the_author_s_name_is_refused(
    rich,  # noqa: F811
    journal: keys.Keys,
    tmp_path: Path,
) -> None:
    """An agent holds the author's reading key but not their signing key:
    a share it signs with a key of its own does not hold."""
    store, kept = rich[0].store, rich[0].state
    mine = keys.mine()
    other = keys.made(tmp_path / "other")[0]
    agent = keys.Keys(mine.reading, mine.recovery, other.signing, other.signer)
    found = shares.cut(store, kept, 1, None)
    out = tmp_path / "forged.share"
    out.write_text(shares.written(agent, found, journal.member)[1])

    # its grant names the agent's signer: the author's card does not match
    with pytest.raises(shares.ShareError, match="signature does not hold"):
        shares.opened(out, journal, mine.member)


def test_the_journal_opens_a_share_and_sends_it_on_under_its_label(
    rich,  # noqa: F811
    journal: keys.Keys,
    tmp_path: Path,
    person_keys: Path,
    monkeypatch,
    capsys,
) -> None:
    """Three key sets: the author shares with the journal; the journal,
    holding the author's card, sends it on to a reviewer it alone knows."""
    store = rich[0].store
    author = keys.card(keys.mine().member)
    reviewer = keys.made(tmp_path / "reviewer")[0]
    out = tmp_path / "round1.share"
    assert _share(store, journal, "-o", str(out)) == 0

    monkeypatch.setenv(keys.ENV, str(tmp_path / "journal"))
    capsys.readouterr()
    assert main(["journal", "open", str(out), "--author", author]) == 0
    assert "signed by the author" in capsys.readouterr().out
    stranger = keys.card(keys.made(tmp_path / "stranger")[0].member)
    assert main(["journal", "open", str(out), "--author", stranger]) == 1

    card = keys.card(reviewer.member)
    sent = ["journal", "send", str(out), "--author", author, "--reviewer", card]
    assert main([*sent, "--label", "R2"]) == 0
    to_reviewer = tmp_path / "round1-R2.share"
    assert "sent on as R2" in capsys.readouterr().out
    assert main([*sent, "--label", "R2"]) == 1  # never written over

    found = shares.opened(to_reviewer, reviewer, journal.member)
    first = shares.opened(out, journal, keys.mine(person_keys).member)
    assert (found.share.label, found.share.round) == ("R2", 1)
    assert (found.writer.id, found.share.origin) == (journal.id, first.entry)
    assert found.share.export == first.share.export
    # the author cannot open what was sent on
    with pytest.raises(shares.ShareError, match="no grant"):
        shares.opened(to_reviewer, keys.mine(person_keys), journal.member)


def test_a_reviewer_s_findings_reach_the_author_through_the_journal(
    rich,  # noqa: F811
    journal: keys.Keys,
    tmp_path: Path,
    person_keys: Path,
    monkeypatch,
    capsys,
) -> None:
    """The reviewer keeps the share as a workspace, files a finding on the
    author's node there, signs the review; the journal checks it and
    passes it on; the author opens the receipt."""
    store, kept = rich[0].store, rich[0].state
    author = keys.mine().member
    reviewer = keys.made(tmp_path / "reviewer")[0]
    out = tmp_path / "round1.share"
    assert _share(store, journal, "-o", str(out)) == 0
    jcard, rcard, acard = (
        keys.card(k) for k in (journal.member, reviewer.member, author)
    )

    monkeypatch.setenv(keys.ENV, str(tmp_path / "journal"))
    sent = ["journal", "send", str(out), "--author", acard, "--reviewer", rcard]
    assert main([*sent, "--label", "R2"]) == 0
    to_reviewer = tmp_path / "round1-R2.share"

    monkeypatch.setenv(keys.ENV, str(tmp_path / "reviewer"))
    work = tmp_path / "work"
    assert (
        main(["review-share", "open", str(to_reviewer), str(work), "--journal", jcard])
        == 0
    )
    assert "kept as state" in capsys.readouterr().out
    node = kept.nodes[0].node
    assert (
        main(
            [
                "find",
                str(work),
                "--aspect",
                "well-posed",
                "--on",
                node,
                "--severity",
                "2",
                "--problem",
                "its domain is unstated",
            ]
        )
        == 0
    )
    # the same round is never kept twice
    assert (
        main(["review-share", "open", str(to_reviewer), str(work), "--journal", jcard])
        == 1
    )
    text = tmp_path / "text.md"
    text.write_text("A careful paper.")
    # nothing confirms for the reviewer: their word first, then the review
    assert main(["review-share", "sign", str(work), "--text", str(text)]) == 1
    assert "wait for your word" in capsys.readouterr().err
    assert main(["confirm", str(work)]) == 0
    assert main(["review-share", "sign", str(work), "--text", str(text)]) == 0
    assert "1 findings signed" in capsys.readouterr().out
    review = work / f"R2-round-1{shares.REVIEW_SUFFIX}"

    monkeypatch.setenv(keys.ENV, str(tmp_path / "journal"))
    received = ["journal", "receive", str(review), "--author", acard, "--label"]
    assert main([*received, "R3", "--reviewer", rcard]) == 1  # another label
    received.append("R2")
    assert main([*received, "--reviewer", acard]) == 1  # not the reviewer's card
    assert main([*received, "--reviewer", rcard]) == 0
    receipt = review.with_suffix(".receipt")

    monkeypatch.setenv(keys.ENV, str(person_keys))
    found, sealed = shares.opened_review(receipt, keys.mine(), journal.member)
    assert sealed.writer.id == journal.id
    assert (found.label, found.round, found.state, found.text) == (
        "R2",
        1,
        kept.id,
        "A careful paper.",
    )
    (finding,) = found.findings
    assert (finding.state, finding.answer, finding.problem) == (
        kept.id,
        None,
        "its domain is unstated",
    )
    assert finding.anchors[0].node == node
    # the review itself opens for no one but the reviewer and the journal
    with pytest.raises(shares.ShareError, match="no grant"):
        shares.opened_review(review, keys.mine(), reviewer.member)

    # the author keeps it: its findings are the review's, answered as any
    at = ["--store", str(store.directory)]
    paper = str(store.directory.parent / "paper")
    capsys.readouterr()
    assert main(["receive", paper, str(receipt), *at, "--journal", acard]) == 1
    assert main(["receive", paper, str(receipt), *at, "--journal", jcard]) == 0
    assert "1 findings filed" in capsys.readouterr().out
    assert main(["receive", paper, str(receipt), *at, "--journal", jcard]) == 1
    again = RecordStore(store.directory)
    ((_, kept_review),) = again.reviews()
    assert kept_review["receipt"] == receipt.read_text()
    (filed,) = again.judgment_records().findings
    assert (filed.state, filed.problem) == (kept.id, "its domain is unstated")
    assert filed.anchors[0].node == node
    main(["open", paper, *at])
    assert "finding (1):" in capsys.readouterr().out
    main(["inbox", paper, *at])
    assert "to answer (1):" in capsys.readouterr().out
    main(["check", paper, *at])
    assert re.search(r"reviews +block → fail +1", capsys.readouterr().out)
    assert (
        main(
            [
                "answer-finding",
                paper,
                *at,
                filed.id,
                "--as",
                "dismissed",
                "--note",
                "stated in §2",
            ]
        )
        == 0
    )
    # the author's answer is their word: it closes the finding once confirmed
    main(["open", paper, *at])
    assert "finding (1):" in capsys.readouterr().out
    assert main(["confirm", paper, *at]) == 0
    main(["open", paper, *at])
    assert "finding (0):" in capsys.readouterr().out
    main(["check", paper, *at])
    assert re.search(r"reviews +block → pass", capsys.readouterr().out)


def test_round_two_carries_the_author_s_replies_and_a_changed_review_is_found(
    rich,  # noqa: F811
    journal: keys.Keys,
    tmp_path: Path,
    person_keys: Path,
    monkeypatch,
    capsys,
) -> None:
    view, _, payload = rich
    store, kept = view.store, view.state
    author = keys.mine()
    reviewer = keys.made(tmp_path / "reviewer")[0]
    other = keys.made(tmp_path / "other")[0]
    at = ["--store", str(store.directory)]
    paper = str(store.directory.parent / "paper")
    jcard = keys.card(journal.member)

    # round 1, through the library: shared, sent on, reviewed, received
    one = tmp_path / "one.share"
    round_one = shares.cut(store, kept, 1, None)
    digest, text = shares.written(author, round_one, journal.member)
    one.write_text(text)
    shares.sent(store, digest, round_one, journal.member)
    first = shares.opened(one, journal, author.member)
    to_r2 = tmp_path / "one-R2.share"
    to_r2.write_text(
        shares.sent_on(journal, first.share, first.entry, "R2", reviewer.member)[1]
    )
    work = tmp_path / "work"
    monkeypatch.setenv(keys.ENV, str(tmp_path / "reviewer"))
    shares.received(
        shares.opened(to_r2, reviewer, journal.member), journal.member, work
    )
    node = kept.nodes[0].node
    find = ["find", str(work), "--aspect", "well-posed", "--on", node, "--severity"]
    assert main([*find, "2", "--problem", "its domain is unstated"]) == 0
    assert main([*find, "1", "--problem", "a symbol is overloaded"]) == 0
    assert main(["confirm", str(work)]) == 0
    review = tmp_path / "R2.review"
    shares.signed_review(work, reviewer, shares.review(work, "Two points."), review)
    # a finding filed after the round was signed: no review sends it
    capsys.readouterr()
    assert main([*find, "1", "--problem", "filed too late"]) == 0
    assert "not sent yet" in capsys.readouterr().out
    assert main(["confirm", str(work)]) == 0
    receipt = tmp_path / "R2.receipt"
    receipt.write_text(
        shares.receipt(
            journal,
            shares.opened_review(review, journal, reviewer.member)[1],
            "R2",
            author.member,
        )[1]
    )
    monkeypatch.setenv(keys.ENV, str(person_keys))
    shares.received_review(store, author, receipt, journal.member)

    # the author answers one finding, and changes the graph: an edge goes
    domain = next(
        f
        for f in store.judgment_records().findings
        if f.problem == "its domain is unstated"
    )
    note = ["--note", "stated in the setting"]
    assert (
        main(["answer-finding", paper, *at, domain.id, "--as", "dismissed", *note]) == 0
    )
    assert main(["confirm", paper, *at]) == 0
    files = {p: versions.blob(store, b) for p, b in kept.files.items()}
    edited = {**payload, "edges": payload["edges"][1:]}
    versions.record(
        store,
        files,
        kept.version,
        kept.root,
        dumps(edited).encode(),
        versions.Command.EDIT,
        started_from=kept,
    )

    # round 2: the reply goes to the journal; each reviewer gets their own
    two = tmp_path / "two.share"
    capsys.readouterr()
    to = keys.card(journal.member)
    assert main(["share", paper, *at, "--to", to, "-o", str(two)]) == 0
    said = capsys.readouterr().out
    assert "share round 2" in said
    assert "your reply to review R2 (round 1): 1 of 2 findings answered" in said
    second = shares.opened(two, journal, author.member)
    assert second.share.continues == first.entry
    sent = [
        (
            label,
            shares.sent_on(journal, second.share, second.entry, label, card)[1],
        )
        for label, card in (("R2", reviewer.member), ("R3", other.member))
    ]
    for label, text in sent:
        (tmp_path / f"two-{label}.share").write_text(text)
    assert (
        shares.opened(tmp_path / "two-R3.share", other, journal.member).share.replies
        == ()
    )

    # the reviewer's workspace: the reply held against what they signed
    copy = tmp_path / "copy"
    shutil.copytree(work, copy)
    monkeypatch.setenv(keys.ENV, str(tmp_path / "reviewer"))
    opening = ["review-share", "open", str(tmp_path / "two-R2.share"), str(work)]
    assert main([*opening, "--journal", jcard]) == 0
    out = capsys.readouterr()
    said = out.out
    assert "never signed" in out.err and "filed too late" in out.err
    assert "is the one you signed" in said
    assert "its domain is unstated  → dismissed: stated in the setting" in said
    assert "a symbol is overloaded  → unanswered" in said
    # what changed since their last look: the edge the author took away
    assert main(["changes", str(work)]) == 0
    changed_since = capsys.readouterr().out
    assert "edges (1):" in changed_since
    assert "- def:gain → uses we-fix-a-field [x]" in changed_since

    # a journal that changes the review it carries back is found out
    forged = dict(second.share.replies[0])
    forged["review"] = {**forged["review"], "text": "One point."}
    changed = replace(second.share, replies=(forged,))
    (tmp_path / "forged.share").write_text(
        shares.sent_on(journal, changed, second.entry, "R2", reviewer.member)[1]
    )
    opening = ["review-share", "open", str(tmp_path / "forged.share"), str(copy)]
    assert main([*opening, "--journal", jcard]) == 0
    assert "is NOT the one you signed" in capsys.readouterr().out


def test_a_share_gives_the_paper_s_files_and_withholds_the_rest(
    rich,  # noqa: F811
    journal: keys.Keys,
    tmp_path: Path,
    capsys,
) -> None:
    """A state keeps more than the paper reads (an author's notes, a
    script): a share gives the paper, lists the rest as withheld, and
    gives one only where the author names it."""
    view, _, payload = rich
    store, kept = view.store, view.state
    files = {p: versions.blob(store, b) for p, b in kept.files.items()}
    versions.record(
        store,
        {**files, "notes/private.md": b"what the referee must not see"},
        kept.version,
        kept.root,
        dumps(payload).encode(),
        versions.Command.EDIT,
        started_from=kept,
    )

    assert _share(store, journal, "--preview") == 0
    said = capsys.readouterr().out
    assert "1 files the state keeps beside the paper (--file adds one): " in said
    assert "notes/private.md" in said.split("it withholds:")[1]

    out = tmp_path / "round1.share"
    assert _share(store, journal, "-o", str(out)) == 0
    author = keys.mine().member
    assert "notes/private.md" not in shares.opened(out, journal, author).share.files
    with_notes = tmp_path / "with-notes.share"
    assert (
        _share(store, journal, "-o", str(with_notes), "--file", "notes/private.md") == 0
    )
    assert "notes/private.md" in shares.opened(with_notes, journal, author).share.files
    assert _share(store, journal, "--preview", "--file", "nowhere.md") == 1


def test_a_document_whose_entry_names_one_writer_and_whose_word_is_another_s_is_refused(
    tmp_path: Path,
) -> None:
    """A forger holding the author's card writes the entry in the author's
    name and confirms it with their own key: the reader holding the
    author's card refuses it."""
    author = keys.mine()
    forger = keys.made(tmp_path / "forger")[0]
    reader = keys.made(tmp_path / "reader")[0]
    paper_key = keys.paper_key()
    grant, granted = records._entry_line(
        records.EntryKind.GRANT,
        [],
        forger.id,
        records._grant_body(
            1,
            paper_key,
            [forger.member, author.member, reader.member],
            keys.public_text(forger.recovery),
        ),
        1,
        None,
    )
    digest, line = records._entry_line(
        records.EntryKind.SHARE, [grant], author.id, {"forged": True}, 1, paper_key
    )
    signature = keys.signed(
        forger, records._confirmation_message(forger.id, [digest], [])
    )
    _, confirmation = records._entry_line(
        records.EntryKind.CONFIRMATION,
        [digest],
        forger.id,
        {"dropped": [], "signature": signature},
        1,
        paper_key,
    )
    forged = tmp_path / "forged.share"
    forged.write_text(granted + line + confirmation)

    with pytest.raises(shares.ShareError, match="whose card you hold"):
        shares.opened(forged, reader, author.member)


@pytest.mark.parametrize(
    "name",
    [
        ".git/config",
        "sub/.hidden",
        "axiomation/record.jsonl",
        "AXIOMATION/record.jsonl",
        "review-share.json",
        "R2-round-1.review",
        "notes/R2.receipt",
        "round2.share",
        "Axiomation.TOML",
        "sub/conventions.md",
        "../outside.md",
        "/etc/passwd",
        "a\\b.md",
    ],
)
def test_a_share_naming_a_file_a_workspace_refuses_writes_nothing(
    rich,  # noqa: F811
    journal: keys.Keys,
    tmp_path: Path,
    name: str,
) -> None:
    store, kept = rich[0].store, rich[0].state
    found = shares.cut(store, kept, 1, None)
    hostile = replace(
        found, files={**found.files, name: b"x"}, label="R2", origin="o" * 64
    )
    opened = shares.Opened(hostile, journal.member, "e" * 64)

    with pytest.raises(shares.ShareError, match="a workspace refuses"):
        shares.received(opened, journal.member, tmp_path / "work")
    assert not (tmp_path / "work").exists()


def test_a_workspace_writes_only_the_files_the_share_s_export_names(
    rich,  # noqa: F811
    journal: keys.Keys,
    tmp_path: Path,
) -> None:
    """A share holding a file its export does not name (the author's
    settings, a script) writes the paper's files alone and lists the rest:
    the reviewer's own settings stay theirs. The author cannot give the
    settings, and the names of the files the paper's reading left out are
    withheld."""
    store, kept = rich[0].store, rich[0].state
    found = shares.cut(store, kept, 1, None)
    assert "never_included" not in found.export["document"]
    for own in ("axiomation.toml", "Axiomation.TOML", "conventions.md"):
        with pytest.raises(shares.ShareError, match="never gives"):
            shares.cut(store, kept, 1, None, more=[own])
    extra = {"scripts/run.sh": b"rm -rf ~\n"}
    hostile = replace(
        found, files={**found.files, **extra}, label="R2", origin="o" * 64
    )
    got = shares.received(
        shares.Opened(hostile, journal.member, "e" * 64),
        journal.member,
        tmp_path / "work",
    )
    assert got.withheld == ("scripts/run.sh",)
    assert not (tmp_path / "work" / "scripts").exists()
    for name in found.files:
        assert (tmp_path / "work" / name).read_bytes() == found.files[name]
    assert set(got.kept.files) == set(found.files)


def test_a_round_whose_graph_did_not_change_sends_no_finding_again(
    rich,  # noqa: F811
    journal: keys.Keys,
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Round 2 of an unchanged graph keeps round 1's state in the
    workspace: a finding the round-1 review carried is not carried again,
    and a round before the last is refused."""
    store, kept = rich[0].store, rich[0].state
    reviewer = keys.made(tmp_path / "reviewer")[0]
    sent = [tmp_path / f"r{n}.share" for n in (1, 2)]
    for n, path in enumerate(sent, 1):
        share = replace(shares.cut(store, kept, n, None), label="R2", origin="o" * 64)
        path.write_text(
            records.sealed_document(
                journal, records.EntryKind.SHARE, share.body(), [reviewer.member]
            )[1]
        )
    monkeypatch.setenv(keys.ENV, str(tmp_path / "reviewer"))
    work = tmp_path / "work"
    shares.received(
        shares.opened(sent[0], reviewer, journal.member), journal.member, work
    )
    find = ["find", str(work), "--aspect", "well-posed", "--on", kept.nodes[0].node]
    assert main([*find, "--severity", "2", "--problem", "first"]) == 0
    assert main([*find, "--severity", "1", "--problem", "taken back"]) == 0
    assert main(["confirm", str(work)]) == 0
    (back,) = [
        f.id
        for f in RecordStore(versions.store_of(work)).judgment_records().findings
        if f.problem == "taken back"
    ]
    answer = ["answer-finding", str(work), back, "--as", "dismissed"]
    assert main([*answer, "--note", "I misread it"]) == 0
    assert main(["confirm", str(work)]) == 0
    # the finding the reviewer closed is not sent
    first = shares.review(work, "")
    shares.signed_review(work, reviewer, first, tmp_path / "R2-1.review")
    # a round is signed once
    with pytest.raises(shares.ShareError, match="signed already"):
        shares.signed_review(work, reviewer, first, tmp_path / "again.review")
    assert not (tmp_path / "again.review").exists()
    assert [f.problem for f in first.findings] == ["first"]
    assert [f.id for f in first.findings] == [f.id for f in first.findings if f.id]
    assert all(f["id"] == "" for f in first.body()["findings"])
    # one filed in round 1 after its review was signed, and one still
    # waiting for the reviewer's word when round 2 comes: neither is of
    # round 2, though round 2 keeps the same state
    assert main([*find, "--severity", "2", "--problem", "late"]) == 0
    assert main(["confirm", str(work)]) == 0
    assert main([*find, "--severity", "2", "--problem", "unconfirmed"]) == 0

    got = shares.received(
        shares.opened(sent[1], reviewer, journal.member), journal.member, work
    )
    assert got.kept.id == shares._workspace(work)["rounds"][0]["kept"]
    # round 1's review was signed, and round 2 carries no reply to it
    assert got.unreturned == 1
    assert main(["confirm", str(work)]) == 0
    assert shares.review(work, "").findings == ()
    assert [f.problem for f in shares.unsigned(work)] == ["late", "unconfirmed"]
    # filed again in round 2, it is round 2's
    assert main([*find, "--severity", "2", "--problem", "late"]) == 0
    assert main(["confirm", str(work)]) == 0
    assert [f.problem for f in shares.review(work, "").findings] == ["late"]
    with pytest.raises(shares.ShareError, match="is not after round 2"):
        shares.received(
            shares.opened(sent[0], reviewer, journal.member), journal.member, work
        )


def test_a_receive_cut_short_files_its_findings_once(
    rich,  # noqa: F811
    journal: keys.Keys,
    tmp_path: Path,
    monkeypatch,
) -> None:
    """The findings are filed, then the entry fails: the receive again
    takes the filing made, not a second one."""
    store, kept = rich[0].store, rich[0].state
    author = keys.mine()
    reviewer = keys.made(tmp_path / "reviewer")[0]
    share = shares.cut(store, kept, 1, None)
    digest, text = shares.written(author, share, journal.member)
    shares.sent(store, digest, share, journal.member)
    finding = records.Finding(
        "",
        None,
        kept.id,
        records.Aspect.WELL_POSED,
        2,
        "unstated",
        None,
        (records.Anchor(records.AnchorKind.BLOCK, kept.nodes[0].node),),
        (),
    )
    review = shares.Review("R2", 1, digest, kept.id, "", (finding,))
    sealed = tmp_path / "R2.review"
    sealed.write_text(
        records.sealed_document(
            reviewer, records.EntryKind.REVIEW, review.body(), [journal.member]
        )[1]
    )
    receipt = tmp_path / "R2.receipt"
    receipt.write_text(
        shares.receipt(
            journal,
            shares.opened_review(sealed, journal, reviewer.member)[1],
            "R2",
            author.member,
        )[1]
    )

    def cut_short(self, body):
        raise records.RecordError("the disk is full")

    with monkeypatch.context() as patched:
        patched.setattr(records.RecordStore, "record_review", cut_short)
        with pytest.raises(shares.ShareError, match="the disk is full"):
            shares.received_review(store, author, receipt, journal.member)
    shares.received_review(store, author, receipt, journal.member)
    assert len(RecordStore(store.directory).judgment_records().findings) == 1


def test_a_bibliography_goes_cut_to_what_is_cited_without_private_fields() -> None:
    bib = (
        "@article{a,\n  file = {/Users/someone/library/a.pdf},\n  title = {A},\n"
        "  usera = {zotero://select/items/X},\n  year = 2001\n}\n\n"
        "@book{b,\n  title = {B},\n  annote = {my own reading of it}\n}\n\n"
        "@misc{c,\n  title = {Not cited}\n}\n"
    )
    found = shares.cited_bibliography(bib, ["b", "a", "b", "missing"])

    assert (found.entries, found.of, found.dropped) == (2, 3, 3)
    assert found.text == (
        "@book{b,\n  title = {B}\n}\n\n@article{a,\n  title = {A},\n  year = 2001\n}\n"
    )


def test_a_cut_bibliography_keeps_the_macros_and_parents_its_entries_use() -> None:
    """A cited entry naming a `@string` macro, or inheriting from a
    `crossref` parent, reads in the cut as in the whole: the macro and
    the parent go with it; a macro or an entry nothing kept uses does
    not."""
    bib = (
        '@string{jmlr = "Journal of Machine Learning Research"}\n'
        '@string{unused = "Never named"}\n\n'
        "@article{a,\n  journal = jmlr # { (online)},\n  month = jan\n}\n\n"
        "@inproceedings{b,\n  title = {B},\n  crossref = {proc}\n}\n\n"
        "@proceedings{proc,\n  booktitle = {Proceedings},\n  year = 2020\n}\n\n"
        "@misc{c,\n  title = {Not cited}\n}\n"
    )
    found = shares.cited_bibliography(bib, ["a", "b"])

    assert (found.entries, found.of) == (3, 4)
    assert found.text == (
        '@string{jmlr = "Journal of Machine Learning Research"}\n\n'
        "@article{a,\n  journal = jmlr # { (online)},\n  month = jan\n}\n\n"
        "@inproceedings{b,\n  title = {B},\n  crossref = {proc}\n}\n\n"
        "@proceedings{proc,\n  booktitle = {Proceedings},\n  year = 2020\n}\n"
    )


def test_nothing_is_sent_or_asked_while_the_person_s_word_waits(
    rich,  # noqa: F811
    journal: keys.Keys,
    tmp_path: Path,
    capsys,
) -> None:
    """A person's filing waits for their word: `check` and the reads say
    so; a share and a run that asks a model refuse, naming it, until the
    person confirms. Nothing confirms for them."""
    view, _, _ = rich
    store = view.store
    store.append(filing("waits"))
    paper = store.directory.parent / "paper"
    at = ["--store", str(store.directory)]

    main(["check", str(paper), *at])
    assert "1 of your filings wait for your word" in capsys.readouterr().out
    assert _share(store, journal, "-o", str(tmp_path / "out.share")) == 1
    assert "judgment waits" in capsys.readouterr().err
    with pytest.raises(UsageError, match="`sync` waits for them"):
        seating.take_budget(
            Asking(budget=1.0), paper, store.directory, "sync", "", Say()
        )
    # a run answering that filing itself (an edit's own) is not refused
    (waiting,) = store.waiting()
    own = frozenset({waiting.answer.key})
    seating.take_budget(
        Asking(budget=1.0), paper, store.directory, "edit", "", Say(), own
    )

    assert main(["confirm", str(paper), *at]) == 0
    assert _share(store, journal, "-o", str(tmp_path / "out.share")) == 0
