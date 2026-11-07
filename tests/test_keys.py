"""A person's keys and the record sealed to them (`docs/architecture.md`
§ 4)."""

from __future__ import annotations

from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from axiomation import keys, records
from axiomation.cli import main
from axiomation.commands import keys as keying
from axiomation.records import (
    JUDGMENTS_STAGE,
    PERSON,
    RECORD,
    EntryKind,
    RecordError,
    RecordStore,
)
from test_records import answer


def filing(key: str) -> object:
    """A person's filing, as `judge` or `rule` files one: no records needed
    to show where it stands."""
    return answer(
        key, kind="judgment", stage=JUDGMENTS_STAGE, host=PERSON, model=PERSON
    )


def test_keys_are_made_once_and_shown(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv(keys.ENV, str(tmp_path / "keys"))
    made = keys.made
    # no terminal here: the key is made without a passphrase
    monkeypatch.setattr(keying, "_at_a_terminal", lambda: None)
    monkeypatch.setattr(keys, "made", lambda at, locked: made(at))

    assert main(["keys"]) == 1
    assert "axiomation keys --make" in capsys.readouterr().out
    assert main(["keys", "--make"]) == 0
    made = capsys.readouterr().out
    mine = keys.mine()
    assert f"reading key {mine.id}" in made
    (recovery,) = [w for w in made.split() if w.startswith(keys.SECRET_PREFIX)]
    # the recovery key's secret is shown, never kept
    kept = "".join(p.read_text() for p in (tmp_path / "keys").iterdir())
    assert recovery not in kept
    assert (tmp_path / "keys" / keys.READING).stat().st_mode & 0o077 == 0
    # a key in use is never replaced
    assert main(["keys", "--make"]) == 1
    assert keys.mine().id == mine.id
    assert main(["keys"]) == 0
    assert mine.id in capsys.readouterr().out


def test_the_recovery_key_opens_the_record(tmp_path: Path, monkeypatch) -> None:
    """A lost reading key loses nothing: the recovery key's secret, set as
    a reading key, opens every record its owner wrote."""
    author, recovery = keys.made(tmp_path / "author")
    monkeypatch.setenv(keys.ENV, str(tmp_path / "author"))
    RecordStore(tmp_path / "store").append(answer())

    restored = tmp_path / "restored"
    restored.mkdir()
    (restored / keys.READING).write_text(recovery)
    (restored / keys.RECOVERY).write_text(keys.public_text(author.recovery))
    monkeypatch.setenv(keys.ENV, str(restored))
    assert [a.key for a in RecordStore(tmp_path / "store").answers()] == ["k1"]

    stranger = tmp_path / "stranger"
    keys.made(stranger)
    monkeypatch.setenv(keys.ENV, str(stranger))
    with pytest.raises(RecordError, match="no trusted grant in the record wraps"):
        RecordStore(tmp_path / "store").answers()


def test_a_record_read_without_keys_names_how_to_make_them(
    tmp_path: Path, monkeypatch
) -> None:
    RecordStore(tmp_path / "store").append(answer())
    monkeypatch.setenv(keys.ENV, str(tmp_path / "none"))

    with pytest.raises(RecordError, match="axiomation keys --make"):
        RecordStore(tmp_path / "store").answers()


def test_a_secret_key_s_line_is_read_back_and_a_wrong_one_refused() -> None:
    made = keys.secret_from(keys.secret_text(X25519PrivateKey.generate()))
    assert keys.secret_from(keys.secret_text(made)).public_key().public_bytes_raw() == (
        made.public_key().public_bytes_raw()
    )
    with pytest.raises(keys.KeysError, match="does not begin"):
        keys.secret_from("AGE-SECRET-KEY-1")


def test_keys_are_made_only_at_a_terminal(tmp_path: Path, monkeypatch, capsys) -> None:
    """The signing key's passphrase is asked at the terminal: a program
    with none there makes no key."""
    monkeypatch.setenv(keys.ENV, str(tmp_path / "keys"))
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    assert main(["keys", "--make"]) == 1
    assert "in a terminal" in capsys.readouterr().err
    assert not (tmp_path / "keys").exists()


def test_ssh_keygen_asks_the_passphrase_itself(tmp_path: Path, monkeypatch) -> None:
    """A locked key's passphrase is never on a command line, where any
    process of the user's could read it: `ssh-keygen` asks for it."""
    run = []
    monkeypatch.setattr(keys, "_ssh_keygen", lambda *a, **k: run.append(a))
    monkeypatch.setattr(keys, "mine", lambda at: None)

    keys.made(tmp_path / "keys", locked=True)

    assert "-N" not in run[0]


def test_a_signing_key_in_use_is_never_replaced(tmp_path: Path) -> None:
    """Where a signing key is there and no reading key, nothing is made:
    `ssh-keygen` would wait for an answer to overwrite it."""
    (tmp_path / "keys").mkdir()
    (tmp_path / "keys" / keys.SIGNING).write_text("in use")

    with pytest.raises(keys.KeysError, match="already"):
        keys.made(tmp_path / "keys")
    assert not (tmp_path / "keys" / keys.READING).exists()


@pytest.mark.signed
def test_a_person_s_filing_takes_effect_under_their_word(tmp_path: Path) -> None:
    store = RecordStore(tmp_path / "store")
    store.append(answer("agent"))
    store.append(filing("kept"))
    store.append(filing("unwanted"))

    # an agent's answer is in effect at once; a person's filing waits
    assert store.without_effect() == {"kept", "unwanted"}
    assert [(p.answer.key, p.binds) for p in store.pending()] == [
        ("agent", False),
        ("kept", True),
        ("unwanted", True),
    ]

    store.confirm(["unwanted"])
    assert store.without_effect() == {"unwanted"}
    assert [a.key for a in store.answers()] == ["agent", "kept"]
    assert store.pending() == ()
    # a dropped filing never takes effect, nor is its attempt free again
    assert RecordStore(tmp_path / "store").next_attempt("unwanted") == 2
    store.append(filing("later"))
    store.confirm()
    assert RecordStore(tmp_path / "store").without_effect() == {"unwanted"}


@pytest.mark.signed
def test_a_confirmation_not_signed_by_the_person_confirms_nothing(
    tmp_path: Path, person_keys: Path
) -> None:
    """An agent holds the reading key's file but not the signing key: a
    confirmation it signs with a key of its own, in the person's name, is
    refused, and the filing it would confirm stays without effect."""
    store = RecordStore(tmp_path / "store")
    store.append(filing("forged"))
    mine = keys.mine()
    other, _ = keys.made(tmp_path / "other")
    agent = keys.Keys(mine.reading, mine.recovery, other.signing, other.signer)

    RecordStore(tmp_path / "store", agent).confirm()

    again = RecordStore(tmp_path / "store")
    assert again.without_effect() == {"forged"}
    ((_, why),) = again.refused_confirmations()
    assert "signature does not hold" in why
    assert [p.answer.key for p in again.pending()] == ["forged"]


@pytest.mark.signed
def test_confirm_lists_then_signs(tmp_path: Path, capsys) -> None:
    store = RecordStore(tmp_path / "store")
    store.append(filing("aaaa1"))
    store.append(filing("bbbb2"))
    paper = tmp_path / "paper"
    paper.mkdir()

    assert main(["confirm", str(paper), "--store", str(store.directory), "--list"]) == 0
    listed = capsys.readouterr().out
    assert listed.count("waits for your word") == 2
    assert store.without_effect() == {"aaaa1", "bbbb2"}

    assert (
        main(["confirm", str(paper), "--store", str(store.directory), "--drop", "bbbb"])
        == 0
    )
    said = capsys.readouterr().out
    assert "(dropped)" in said
    assert "1 of your answers take effect, 1 dropped" in said
    assert RecordStore(store.directory).without_effect() == {"bbbb2"}
    assert (
        main(["confirm", str(paper), "--store", str(store.directory), "--drop", "zz"])
        == 1
    )


@pytest.mark.signed
def test_a_co_author_added_by_a_signed_grant_reads_and_signs(
    tmp_path: Path, monkeypatch, person_keys: Path, capsys
) -> None:
    store = RecordStore(tmp_path / "store")
    store.append(answer("before"))
    coauthor, _ = keys.made(tmp_path / "coauthor")
    paper = tmp_path / "paper"
    paper.mkdir()
    at = ["--store", str(store.directory)]

    card = keys.card(coauthor.member)
    assert main(["members", str(paper), *at, "--add", card]) == 0
    assert "epoch 2" in capsys.readouterr().out
    store.append(answer("after"))
    assert {m.id for m in store.members()} == {keys.mine().id, coauthor.id}

    # the co-author reads the whole record, the epoch before they joined too
    monkeypatch.setenv(keys.ENV, str(tmp_path / "coauthor"))
    assert [a.key for a in RecordStore(store.directory).answers()] == [
        "before",
        "after",
    ]

    # the grant is the author's signed word: the co-author's binds at once
    theirs = RecordStore(store.directory)
    theirs.append(filing("theirs"))
    theirs.confirm()
    monkeypatch.setenv(keys.ENV, str(person_keys))
    assert RecordStore(store.directory).without_effect() == frozenset()


@pytest.mark.signed
def test_a_key_is_retired_by_a_new_epoch(tmp_path: Path, monkeypatch) -> None:
    """A person's new key is added with the old; the old one removed with
    the new: entries after are sealed to the new key alone."""
    old, _ = keys.made(tmp_path / "old")
    monkeypatch.setenv(keys.ENV, str(tmp_path / "old"))
    store = RecordStore(tmp_path / "store")
    store.append(answer("first"))
    with pytest.raises(RecordError, match="cannot remove your own"):
        store.grant(remove=[old.id])

    new, _ = keys.made(tmp_path / "new")
    store.grant(add=[new.member])
    monkeypatch.setenv(keys.ENV, str(tmp_path / "new"))
    renewed = RecordStore(tmp_path / "store")
    assert renewed.grant(remove=[old.id[:8]]) == 3
    renewed.append(answer("second"))
    assert [m.id for m in renewed.members()] == [new.id]

    # the old key reads what it read before, and nothing after
    monkeypatch.setenv(keys.ENV, str(tmp_path / "old"))
    retired = RecordStore(tmp_path / "store")
    assert [a.key for a in retired.answers()] == ["first"]
    assert any("epoch 3" in p for p in retired.problems())


@pytest.mark.signed
def test_two_copies_of_a_record_join(
    tmp_path: Path, capsys, monkeypatch, person_keys: Path
) -> None:
    """Each machine appends to its own copy; joined, by `join` or by git's
    union merge of the file, the record holds both."""
    here = RecordStore(tmp_path / "here")
    here.append(answer("common"))
    there = here.copy_to(tmp_path / "there")
    here.append(answer("mine"))
    there.append(answer("theirs"))
    # each copy begins an epoch of the same number, under its own key
    here.grant(add=[keys.made(tmp_path / "a")[0].member])
    there.grant(add=[keys.made(tmp_path / "b")[0].member])
    here.append(answer("mine-2"))
    there.append(answer("theirs-2"))

    record = (tmp_path / "here" / RECORD).read_text()
    theirs = (tmp_path / "there" / RECORD).read_text()
    shared = "".join(
        line for line in record.splitlines(keepends=True) if line in theirs
    )
    union = tmp_path / "union"
    union.mkdir()
    (union / RECORD).write_text(
        shared
        + "".join(x for x in record.splitlines(keepends=True) if x not in shared)
        + "".join(x for x in theirs.splitlines(keepends=True) if x not in shared)
    )
    keys_of = {"common", "mine", "theirs", "mine-2", "theirs-2"}
    assert {a.key for a in RecordStore(union).answers()} == keys_of

    paper = tmp_path / "paper"
    paper.mkdir()
    args = ["join", str(paper), str(tmp_path / "there"), "--store", str(here.directory)]
    assert main(args) == 0
    assert "joined: 3 entries" in capsys.readouterr().out
    assert {a.key for a in RecordStore(here.directory).answers()} == keys_of
    assert RecordStore(here.directory).join(tmp_path / "there") == 0

    # a member one copy added reads what that copy's grant gives, and is
    # not locked out by the other's
    monkeypatch.setenv(keys.ENV, str(tmp_path / "a"))
    a = RecordStore(here.directory)
    assert {x.key for x in a.answers()} == keys_of - {"theirs-2"}
    assert any("does not open" in p for p in a.problems())
    monkeypatch.setenv(keys.ENV, str(person_keys))

    # two grants made apart: nothing is written until one settles them, for
    # the members both name
    joined = RecordStore(here.directory)
    with pytest.raises(RecordError, match="members --settle"):
        joined.append(answer("after"))
    with pytest.raises(RecordError, match="members --settle"):
        joined.grant(add=[keys.made(tmp_path / "c")[0].member])
    joined.grant(settle=True)
    assert [m.id for m in joined.members()] == [keys.mine().id]
    joined.append(answer("after"))


def test_a_record_not_a_copy_is_not_joined(tmp_path: Path) -> None:
    here = RecordStore(tmp_path / "here")
    here.append(answer("one"))
    RecordStore(tmp_path / "other").append(answer("two"))
    before = (tmp_path / "here" / RECORD).read_bytes()

    with pytest.raises(RecordError, match="no copy of the record"):
        here.join(tmp_path / "other")
    assert (tmp_path / "here" / RECORD).read_bytes() == before


def _appended_grant(store: Path, members: list[keys.Member], signer: keys.Keys) -> str:
    """A grant appended after the record's heads, naming `members`, written
    and signed by `signer`: what anyone who can write the file can add."""
    loaded = RecordStore(store)
    with loaded._locked():
        held = loaded._load()
        parents = held.heads()
        recovery = held.trusted[held.current()[0]].recovery
    body = records._grant_body(9, keys.paper_key(), members, recovery)
    body["signature"] = keys.signed(
        signer, records._grant_message(signer.id, parents, body)
    )
    digest, line = records._entry_line(
        EntryKind.GRANT, parents, signer.id, body, 9, None
    )
    with (store / RECORD).open("a") as file:
        file.write(line)
    return digest


@pytest.mark.signed
def test_a_grant_no_member_signed_gives_nothing(tmp_path: Path) -> None:
    """One line appended by someone who can write the file, a grant of
    their own key: it is refused, the author's next entries are not
    sealed to it, and nothing sealed under it counts."""
    store = RecordStore(tmp_path / "store")
    store.append(answer("mine"))
    outsider = keys.made(tmp_path / "outsider")[0]
    _appended_grant(tmp_path / "store", [keys.mine().member, outsider.member], outsider)

    again = RecordStore(tmp_path / "store")
    assert any("no signing member" in p for p in again.problems())
    again.append(answer("after"))
    with again._locked():
        loaded = again._load()
        assert loaded.writing()[0] == 1
    assert [m.id for m in again.members()] == [keys.mine().id]
    # a confirmation over it trusts it no more
    again.confirm()
    assert [m.id for m in RecordStore(tmp_path / "store").members()] == [keys.mine().id]


@pytest.mark.signed
def test_a_removed_member_s_later_work_counts_for_nothing(
    tmp_path: Path, monkeypatch, person_keys: Path
) -> None:
    """A removal closes the epoch at the entries it names under it: what a
    removed member writes on a copy from before it counts for nothing."""
    store = RecordStore(tmp_path / "store")
    store.append(answer("first"))
    other = keys.made(tmp_path / "other")[0]
    store.grant(add=[other.member])
    copy = store.copy_to(tmp_path / "copy")
    store.grant(remove=[other.id])
    monkeypatch.setenv(keys.ENV, str(tmp_path / "other"))
    RecordStore(copy.directory).append(answer("late"))
    monkeypatch.setenv(keys.ENV, str(person_keys))

    RecordStore(store.directory).join(copy.directory)
    joined = RecordStore(store.directory)
    assert [a.key for a in joined.answers()] == ["first"]
    assert any("closed its epoch" in p for p in joined.problems())


@pytest.mark.signed
def test_a_drop_never_takes_back_a_confirmed_entry(tmp_path: Path) -> None:
    store = RecordStore(tmp_path / "store")
    store.append(filing("kept"))
    store.confirm()
    # a later confirmation naming it as dropped, as a crafted one would
    store.append(filing("other"))
    with store._locked():
        loaded = store._load()
        heads = loaded.heads()
        kept = next(d for d, x in loaded.answered().entries.items())
        mine = keys.mine()
        body = {
            "dropped": [kept],
            "signature": keys.signed(
                mine, records._confirmation_message(mine.id, heads, [kept])
            ),
        }
        line = records._entry_line(
            EntryKind.CONFIRMATION, heads, mine.id, body, *loaded.writing()
        )[1]
    with (store.record_path).open("a") as file:
        file.write(line)

    assert RecordStore(tmp_path / "store").without_effect() == frozenset()
