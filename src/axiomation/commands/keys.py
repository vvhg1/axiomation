"""`keys`, `confirm` and `members`: a person's keys, which every record
they write is sealed to, their word on what was filed, and who a record
is sealed to (`docs/architecture.md` § 4): the keys shown, made once
with the recovery key, or given as a member's card; the record's pending
entries listed, and confirmed with one signature; its members listed,
added or removed, each change a new epoch; and two copies of a record
joined."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from axiomation import keys
from axiomation.check import made_apart
from axiomation.commands import (
    STORE_HELP,
    Exit,
    Say,
    UsageError,
    requested,
)
from axiomation.paper_argument import directory_argument
from axiomation.records import PendingEntry, RecordError, RecordStore, holds_record
from axiomation.versions import VersionError


@dataclass(frozen=True)
class Keys:
    """`keys`: this person's keys shown; with `make`, made."""

    make: bool = False
    card: bool = False


@dataclass(frozen=True)
class Members:
    """`members`: the store's members; with `add` (members' cards) or
    `remove` (members' ids), a new epoch without or with them; with
    `settle`, one over two grants made apart."""

    store: Path
    add: tuple[str, ...] = ()
    remove: tuple[str, ...] = ()
    settle: bool = False


@dataclass(frozen=True)
class Join:
    """`join`: the entries of the record in `other` this store lacks,
    appended."""

    store: Path
    other: Path


@dataclass(frozen=True)
class Confirm:
    """`confirm`: the store's pending entries; signed for unless `list`,
    the filings `drop` names left without effect for good."""

    store: Path
    drop: tuple[str, ...] = ()
    list: bool = False


def request(arguments: argparse.Namespace) -> Keys | Confirm | Members | Join:
    if arguments.command == "confirm":
        return requested(Confirm, arguments)
    if arguments.command == "members":
        return requested(Members, arguments)
    if arguments.command == "join":
        return requested(Join, arguments)
    return requested(Keys, arguments)


def _at_a_terminal() -> None:
    """Refused where there is no terminal for `ssh-keygen` to ask the
    signing key's passphrase at."""
    if not sys.stdin.isatty():
        raise UsageError(
            "run `axiomation keys --make` in a terminal: your signing key's "
            "passphrase is asked there"
        )


def run(asked: Keys | Confirm | Members | Join, say: Say) -> int:
    match asked:
        case Confirm():
            return confirm(asked, say)
        case Members():
            return members(asked, say)
        case Join():
            return join(asked, say)
    return keys_run(asked, say)


def keys_run(asked: Keys, say: Say) -> int:
    """`keys`: where this person's keys are and their reading key's id;
    `keys --make`: a reading key, a recovery key and a signing key made,
    the recovery key's secret shown this once and never kept; where the
    reading key is there and no signing key, the signing key alone."""
    at = keys.directory()
    if asked.make:
        if (at / keys.SIGNING).exists():
            raise UsageError(
                f"{at} holds your keys already: nothing to make, and a key in "
                "use is never replaced"
            )
        _at_a_terminal()
        try:
            mine, recovery = keys.made(at, locked=True)
            locked = keys.is_locked(mine)
        except keys.KeysError as error:
            raise UsageError(str(error)) from None
        say.line(f"keys made in {at}: reading key {mine.id}")
        if not locked:
            say.line(
                "Your signing key has no passphrase: any program you run can "
                "sign your word."
            )
        if recovery is None:
            return Exit.OK
        say.line("")
        say.line("Your recovery key, shown this once and kept nowhere:")
        say.line("")
        say.line(f"  {recovery}")
        say.line("")
        say.line(
            "Keep it offline (on paper, or on another device). It opens every "
            "record you write, if your reading key is lost."
        )
        return Exit.OK
    try:
        mine = keys.mine(at)
    except keys.KeysError as error:
        say.line(str(error))
        return Exit.FAILED
    if asked.card:
        say.line(keys.card(mine.member))
        return Exit.OK
    say.line(f"keys in {at}: reading key {mine.id}")
    if mine.signing is None:
        say.line("no signing key: make it with `axiomation keys --make`")
    return Exit.OK


def confirm(asked: Confirm, say: Say) -> int:
    """`confirm`: each answer no confirmation covers yet, a person's filing
    marked as waiting for the person's word; then, unless `--list`, the
    person's one signature over the record as it stands, the filings
    `--drop` names left without effect."""
    if not holds_record(asked.store):
        raise UsageError(f"{asked.store} holds no record")
    store = RecordStore(asked.store)
    try:
        pending = store.pending()
        refused = store.refused_confirmations()
        # what a drop took already, apart from what this one takes
        gone_states, gone = store.taken_by(())
        states, taken = store.taken_by(asked.drop)
        states, taken = states - gone_states, taken - gone
        waits = store.waiting_entries()
    except RecordError as error:
        raise UsageError(str(error)) from None
    for digest, why in refused:
        say.line(f"confirmation {digest[:12]} refused: {why}")
    if not pending:
        say.line("nothing is pending")
    for entry in pending:
        answer = entry.answer
        who = (
            answer.host
            if answer.model == answer.host
            else f"{answer.host} {answer.model}"
        )
        mark = (
            "waits for your word"
            if entry.binds
            else "dropped already"
            if entry.entry in gone
            else "waits with your edits"
            if entry.entry in waits
            else "in effect"
        )
        dropped = (
            " (dropped)"
            if _named(entry, asked.drop)
            else " (drop it too)"
            if entry.binds and (entry.entry in taken or entry.entry in gone)
            else " (goes with the drop)"
            if entry.entry in taken
            else ""
        )
        # a key with two pending attempts (two joined copies) shows each
        # one's entry, which `--drop` names apart
        twice = sum(1 for e in pending if e.answer.key == answer.key) > 1
        named = f"{answer.key[:12]} {entry.entry[:12]}" if twice else answer.key[:12]
        say.line(f"  {named}  {answer.kind}  by {who}  {answer.time}  {mark}{dropped}")
    if states:
        say.line(f"the drop takes back the states {', '.join(sorted(states))}")
    if asked.list:
        return Exit.OK
    try:
        digest = store.confirm(asked.drop)
    except RecordError as error:
        raise UsageError(str(error)) from None
    taking = sum(1 for e in pending if e.binds and not _named(e, asked.drop))
    dropping = sum(1 for e in pending if _named(e, asked.drop))
    say.line(
        f"confirmed ({digest[:12]}): {taking} of your answers take effect, "
        f"{dropping} dropped"
    )
    return Exit.OK


def members(asked: Members, say: Say) -> int:
    """`members`: the latest epoch's members; with `--add` or `--remove`,
    a new epoch first, its paper key wrapped to the members it names."""
    if not holds_record(asked.store):
        raise UsageError(f"{asked.store} holds no record")
    store = RecordStore(asked.store)
    try:
        if asked.add or asked.remove or asked.settle:
            added = [keys.card_from(c) for c in asked.add]
            epoch = store.grant(added, asked.remove, asked.settle)
            say.line(f"epoch {epoch}: a new paper key, signed, for the members below")
        found = store.members()
    except (RecordError, keys.KeysError) as error:
        raise UsageError(str(error)) from None
    for member in found:
        signs = "signs" if member.signer else "reads only"
        say.line(f"  {member.id}  {signs}")
    return Exit.OK


def join(asked: Join, say: Say) -> int:
    """`join`: another copy's entries appended to this store's record."""
    if not holds_record(asked.other):
        raise UsageError(f"{asked.other} holds no record")
    try:
        added = RecordStore(asked.store).join(asked.other)
        torn = made_apart(RecordStore(asked.store))
    except (RecordError, VersionError) as error:
        raise UsageError(str(error)) from None
    say.line(f"joined: {added} entries from {asked.other}")
    for line in torn:
        say.note(line)
    return Exit.OK


def _named(entry: PendingEntry, names: tuple[str, ...]) -> bool:
    """Whether one of `names` names `entry`, by its key or its entry's hash."""
    return entry.binds and any(
        entry.entry.startswith(n) or entry.answer.key.startswith(n) for n in names
    )


def _keys_command(commands: Any) -> None:
    found = commands.add_parser(
        "keys",
        help="your keys, which encrypt every record you write and sign what you "
        "file: shown, or made once with --make",
    )
    found.add_argument(
        "--make",
        action="store_true",
        help="make your reading key, a recovery key (its secret shown once) "
        "and a signing key, locked by a passphrase asked here",
    )
    found.add_argument(
        "--card",
        action="store_true",
        help="print your member's card, the line an author adds you with "
        "(`axiomation members --add`)",
    )


def _confirm_command(commands: Any) -> None:
    found = commands.add_parser(
        "confirm",
        help="sign what was filed since you last confirmed: the pending entries "
        "listed, then signed together",
    )
    directory_argument(found)
    found.add_argument(
        "--store",
        type=Path,
        help=STORE_HELP,
    )
    found.add_argument(
        "--drop",
        action="append",
        default=[],
        metavar="KEY",
        help="a pending filing, by its key or a prefix of it, left without "
        "effect for good (repeat for several)",
    )
    found.add_argument(
        "--list",
        action="store_true",
        help="list the pending entries; sign nothing",
    )


def _members_command(commands: Any) -> None:
    found = commands.add_parser(
        "members",
        help="who can read the paper's record: listed; added or removed, each "
        "change a new paper key",
    )
    directory_argument(found)
    found.add_argument(
        "--store",
        type=Path,
        help=STORE_HELP,
    )
    found.add_argument(
        "--add",
        action="append",
        default=[],
        metavar="CARD",
        help="a member's card, as `axiomation keys --card` prints it (repeat "
        "for several)",
    )
    found.add_argument(
        "--remove",
        action="append",
        default=[],
        metavar="ID",
        help="a member's id or a prefix of it (repeat for several)",
    )
    found.add_argument(
        "--settle",
        action="store_true",
        help="where two copies of the record, joined, each changed the members: "
        "one list of members over both, those both name; add again any added "
        "on one side alone",
    )


def _join_command(commands: Any) -> None:
    found = commands.add_parser(
        "join",
        help="another copy of the paper's record joined into this one (git "
        "joins the committed record by itself)",
    )
    # the other copy's store is a path, so the paper is never left out
    found.add_argument(
        "directory", type=Path, metavar="PAPER", help="the paper's directory"
    )
    found.add_argument("other", type=Path, help="the other copy's store directory")
    found.add_argument(
        "--store",
        type=Path,
        help=STORE_HELP,
    )


#: `keys`', `confirm`'s, `members`' and `join`'s arguments.
ARGUMENTS: dict[str, Callable[[Any], None]] = {
    "join": _join_command,
    "keys": _keys_command,
    "confirm": _confirm_command,
    "members": _members_command,
}
