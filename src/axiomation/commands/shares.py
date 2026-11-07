"""`share`, `journal` and `review-share`: the paper's graph at its
latest state given to a reader outside its record, a journal: what goes
and what is withheld listed, then sealed to the reader and signed with
the author's one passphrase, and its sending kept in the record; the
journal's side, a share opened against the author's card, sent on to a
reviewer under the journal's label, and the review passed back; the
reviewer's, the share kept as a workspace, findings filed there with
`find`, the review signed."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from axiomation import keys, shares, versions
from axiomation.commands import (
    STORE_HELP,
    Exit,
    Say,
    UsageError,
    requested,
)
from axiomation.paper_argument import directory_argument
from axiomation.records import RecordError, RecordStore, holds_record


@dataclass(frozen=True)
class Share:
    """`share`: the latest state shared with the reader whose card `to`
    is; with `preview`, listed and nothing written."""

    store: Path
    to: str
    round: int | None = None
    preview: bool = False
    output: Path | None = None
    into: Path | None = None
    file: tuple[str, ...] = ()


@dataclass(frozen=True)
class JournalOpen:
    """`journal open`: a share opened with the journal's keys, its writer
    checked against the author's card."""

    share: Path
    author: str


@dataclass(frozen=True)
class JournalSend:
    """`journal send`: a share sent on to the reviewer whose card
    `reviewer` is, under the label `label`."""

    share: Path
    author: str
    reviewer: str
    label: str
    output: Path | None = None


@dataclass(frozen=True)
class JournalReceive:
    """`journal receive`: a review, checked against the reviewer's card,
    passed to the author whose card `author` is."""

    review: Path
    reviewer: str
    author: str
    label: str
    output: Path | None = None


@dataclass(frozen=True)
class ReviewOpen:
    """`review-share open`: a share the journal sent on, kept as the
    workspace `directory`."""

    share: Path
    directory: Path
    journal: str


@dataclass(frozen=True)
class ReviewSign:
    """`review-share sign`: the workspace's review, its text the file
    `text` holds, signed for the journal."""

    directory: Path
    text: Path | None = None
    output: Path | None = None


@dataclass(frozen=True)
class Receive:
    """`receive`: a review the journal passed on, kept in the record."""

    store: Path
    receipt: Path
    journal: str


Request = (
    Share
    | JournalOpen
    | JournalSend
    | JournalReceive
    | ReviewOpen
    | ReviewSign
    | Receive
)


def request(arguments: argparse.Namespace) -> Request:
    if arguments.command == "journal":
        return requested(
            {"open": JournalOpen, "send": JournalSend, "receive": JournalReceive}[
                arguments.action
            ],
            arguments,
        )
    if arguments.command == "review-share":
        return requested(
            {"open": ReviewOpen, "sign": ReviewSign}[arguments.action], arguments
        )
    if arguments.command == "receive":
        return requested(Receive, arguments)
    return requested(Share, arguments)


def run(asked: Request, say: Say) -> int:
    match asked:
        case JournalOpen():
            return journal_open(asked, say)
        case JournalSend():
            return journal_send(asked, say)
        case JournalReceive():
            return journal_receive(asked, say)
        case ReviewOpen():
            return review_open(asked, say)
        case ReviewSign():
            return review_sign(asked, say)
        case Receive():
            return receive(asked, say)
    return share(asked, say)


def share(asked: Share, say: Say) -> int:
    """`share`: the listing, then, unless `--preview`, the share written
    to `--output` (default: the store's `shares/`) and its sending kept."""
    if not holds_record(asked.store):
        raise UsageError(f"{asked.store} holds no record")
    store = RecordStore(asked.store)
    if asked.into is not None and not asked.preview:
        raise UsageError("--into goes with --preview")
    if asked.output is None and not asked.preview:
        raise UsageError(
            "name the share's file with -o: it goes to the journal, not into "
            "the paper's repository"
        )
    if asked.output is not None and asked.output.exists():
        raise UsageError(f"{asked.output} is there already")
    try:
        if asked.preview:
            waiting = store.waiting()
            if waiting:
                say.note(
                    f"{len(waiting)} of your filings wait for your word: the "
                    "share leaves them out until `axiomation confirm`"
                )
        else:
            store.refuse_waiting("the share")
        reader = keys.card_from(asked.to)
        mine = keys.mine()
        kept = versions.latest(store)
        if kept is None:
            raise UsageError(f"{asked.store} keeps no state to share")
        round, continues = shares.next_round(store, reader.id)
        replies = shares.replies_for(store, reader.id)
        if asked.round is not None and asked.round != round:
            # a round given apart from the chain starts one: it continues no
            # share and replies to no review of another submission
            round, continues, replies = asked.round, None, ()
        found = shares.cut(store, kept, round, continues, replies, asked.file)
        full = versions.kept_payload(store, kept)
        for line in shares.listing(found, reader, full, kept):
            say.line(line)
        if asked.into is not None:
            shares.previewed(found, mine.member, asked.into)
            say.line(
                f"kept as a reviewer would get it in {asked.into}: "
                f"`axiomation view {asked.into}`"
            )
        if asked.preview or asked.output is None:
            return Exit.OK
        digest, text = shares.written(mine, found, reader)
        asked.output.write_text(text, encoding="utf-8")
        shares.sent(store, digest, found, reader)
    except (keys.KeysError, RecordError, shares.ShareError) as error:
        raise UsageError(str(error)) from None
    say.line(f"shared ({digest[:12]}): {asked.output}")
    return Exit.OK


def _opened(path: Path, writer: str) -> tuple[shares.Opened, keys.Keys]:
    """The share in `path`, opened with this person's keys and checked
    against its writer's card (the author's, or the journal's)."""
    try:
        mine = keys.mine()
        found = shares.opened(path, mine, keys.card_from(writer))
    except (keys.KeysError, shares.ShareError) as error:
        raise UsageError(str(error)) from None
    return found, mine


def journal_open(asked: JournalOpen, say: Say) -> int:
    """`journal open`: what the share gives, once its signature holds."""
    found = _opened(asked.share, asked.author)[0].share
    export = found.export
    say.line(
        f"share round {found.round} of state {found.state[:12]}, signed by "
        "the author"
        + (f", continuing {found.continues[:12]}" if found.continues else "")
    )
    say.line(
        f"  {len(export.get('blocks', []))} blocks, "
        + ", ".join(f"{len(export.get(s, []))} {s}" for s in shares.LINES)
        + f"; {len(found.files)} files: "
        + ", ".join(sorted(found.files))
    )
    return Exit.OK


def journal_send(asked: JournalSend, say: Say) -> int:
    """`journal send`: the share under the review's label, sealed to the
    reviewer and signed by the journal, written to `--output` (default:
    beside the share, named by the label)."""
    found, mine = _opened(asked.share, asked.author)
    if not shares.LABEL.fullmatch(asked.label):
        raise UsageError(f"{asked.label!r} is not a label: letters, digits, - and _")
    output = asked.output or asked.share.with_name(
        f"{asked.share.stem}-{asked.label}{shares.SUFFIX}"
    )
    if output.exists():
        raise UsageError(f"{output} is there already")
    try:
        digest, text = shares.sent_on(
            mine, found.share, found.entry, asked.label, keys.card_from(asked.reviewer)
        )
    except (keys.KeysError, shares.ShareError) as error:
        raise UsageError(str(error)) from None
    output.write_text(text, encoding="utf-8")
    say.line(f"sent on as {asked.label} ({digest[:12]}): {output}")
    return Exit.OK


def journal_receive(asked: JournalReceive, say: Say) -> int:
    """`journal receive`: the review, once the reviewer's signature holds,
    sealed to the author and signed by the journal, written to `--output`
    (default: beside the review, `.receipt`)."""
    output = asked.output or asked.review.with_suffix(".receipt")
    if output.exists():
        raise UsageError(f"{output} is there already")
    try:
        mine = keys.mine()
        found, sealed = shares.opened_review(
            asked.review, mine, keys.card_from(asked.reviewer)
        )
        digest, text = shares.receipt(
            mine, sealed, asked.label, keys.card_from(asked.author)
        )
    except (keys.KeysError, shares.ShareError) as error:
        raise UsageError(str(error)) from None
    output.write_text(text, encoding="utf-8")
    say.line(
        f"review {found.label}, round {found.round}: {len(found.findings)} "
        f"findings, passed to the author ({digest[:12]}): {output}"
    )
    return Exit.OK


def _say_unsigned(directory: Path, say: Say) -> None:
    """Warn of findings filed on an earlier round and never signed: no
    review sends them."""
    left = shares.unsigned(directory)
    if left:
        say.note(
            f"axiomation review-share: {len(left)} findings you filed on an "
            "earlier round were never signed, and no review sends them; file "
            "them again on this round to send them: "
            + "; ".join(f.problem for f in left)
        )


def review_open(asked: ReviewOpen, say: Say) -> int:
    """`review-share open`: the share kept as the reviewer's workspace."""
    found, _ = _opened(asked.share, asked.journal)
    try:
        got = shares.received(found, keys.card_from(asked.journal), asked.directory)
    except (keys.KeysError, shares.ShareError) as error:
        raise UsageError(str(error)) from None
    say.line(
        f"review {found.share.label}, round {found.share.round}: kept as state "
        f"{got.kept.id[:12]} in {asked.directory}; read it with `axiomation view`, "
        "`show` and `reach`"
        + (", what changed since with `changes`" if got.kept.parent else "")
        + ", file findings with `axiomation find`, give your word with "
        "`axiomation confirm`, then `axiomation review-share sign`"
    )
    _say_unsigned(asked.directory, say)
    if got.unreturned is not None:
        say.line(
            f"the review you signed for round {got.unreturned} did not come back: "
            "the share carries "
            + (
                "no reply to it"
                if got.reply is None
                else f"a reply to round {got.reply['round']}"
            )
        )
    if got.withheld:
        say.line(
            "not written: files the share holds that its export does not name: "
            + ", ".join(got.withheld)
        )
    if got.reply is not None:
        say.line(
            "no review you signed here to hold the one it carries back to"
            if got.held is None
            else "the review it carries back is the one you signed"
            if got.held
            else "the review it carries back is NOT the one you signed: the "
            "journal or the author changed it"
        )
        problems = [f.get("problem") for f in got.reply["review"].get("findings", [])]
        answers = got.reply.get("answers", [])
        if len(answers) != len(problems):
            raise UsageError(
                "the reply answers another number of findings than it names"
            )
        say.line(f"the author's answers to your round {got.reply['round']}:")
        for problem, answer in zip(problems, answers, strict=True):
            said = (
                "unanswered"
                if answer is None
                else answer["status"]
                + (
                    f" of finding {answer['of']}"
                    if isinstance(answer.get("of"), int)
                    else f" of {answer['of']}"
                    if answer.get("of")
                    else ""
                )
                + (f": {answer['note']}" if answer.get("note") else "")
            )
            say.line(f"  {problem}  → {said}")
    return Exit.OK


def review_sign(asked: ReviewSign, say: Say) -> int:
    """`review-share sign`: the review of the latest round, signed for the
    journal, written to `--output` (default: in the workspace)."""
    try:
        text = "" if asked.text is None else asked.text.read_text(encoding="utf-8")
        found = shares.review(asked.directory, text)
        output = asked.output or asked.directory / (
            f"{found.label}-round-{found.round}{shares.REVIEW_SUFFIX}"
        )
        if output.exists():
            raise UsageError(f"{output} is there already")
        digest = shares.signed_review(asked.directory, keys.mine(), found, output)
    except (OSError, keys.KeysError, shares.ShareError) as error:
        raise UsageError(str(error)) from None
    say.line(
        f"review {found.label}, round {found.round}: {len(found.findings)} "
        f"findings signed ({digest[:12]}): {output}"
    )
    _say_unsigned(asked.directory, say)
    return Exit.OK


def receive(asked: Receive, say: Say) -> int:
    """`receive`: the journal's receipt checked and kept, its findings filed
    as the review's, answered as any finding is."""
    if not holds_record(asked.store):
        raise UsageError(f"{asked.store} holds no record")
    try:
        found, key = shares.received_review(
            RecordStore(asked.store),
            keys.mine(),
            asked.receipt,
            keys.card_from(asked.journal),
        )
    except (keys.KeysError, shares.ShareError) as error:
        raise UsageError(str(error)) from None
    say.line(
        f"review {found.label}, round {found.round}: {len(found.findings)} "
        "findings filed"
        + (f" ({key[:12]})" if key else "")
        + "; `axiomation open` and `inbox` list them, `answer-finding` answers each"
    )
    return Exit.OK


def _share_command(commands: Any) -> None:
    # a description, no help: the top-level help leaves the shares out
    found = commands.add_parser(
        "share",
        description="the paper's graph at its latest state, for a reader outside "
        "its record (a journal): listed, then encrypted for them and signed",
    )
    directory_argument(found)
    found.add_argument(
        "--store",
        type=Path,
        help=STORE_HELP,
    )
    found.add_argument(
        "--to",
        required=True,
        metavar="CARD",
        help="the reader's card, as `axiomation keys --card` prints it",
    )
    found.add_argument(
        "--round",
        type=int,
        help="the round (default: one after the last share to this reader); "
        "another starts a new chain, continuing no share and replying to no "
        "review",
    )
    found.add_argument(
        "--preview",
        action="store_true",
        help="list what goes and what is withheld; write nothing",
    )
    found.add_argument(
        "--file",
        action="append",
        default=[],
        metavar="PATH",
        help="a file the state keeps beside the paper, given too (a PDF, a "
        "script); repeat for several",
    )
    found.add_argument(
        "--into",
        type=Path,
        metavar="DIR",
        help="with --preview: also keep the share as a reviewer would get it "
        "in DIR (a new directory), to open with `axiomation view DIR`",
    )
    found.add_argument(
        "-o",
        "--output",
        type=Path,
        help="the share's file, to give the journal (required unless --preview)",
    )


def _journal_command(commands: Any) -> None:
    # a description, no help: the top-level help leaves the shares out
    found = commands.add_parser(
        "journal",
        description="a journal's side of a share: opened against the author's card, "
        "sent on to a reviewer under a label",
    )
    actions = found.add_subparsers(dest="action", required=True)
    opening = actions.add_parser(
        "open", help="a share opened with your keys, its signature checked"
    )
    sending = actions.add_parser(
        "send",
        help="a share sent on to a reviewer under your label for the review, "
        "encrypted for them and signed by you",
    )
    for action in (opening, sending):
        action.add_argument("share", type=Path, help="the author's share file")
        action.add_argument(
            "--author",
            required=True,
            metavar="CARD",
            help="the author's card, as they gave it at submission",
        )
    sending.add_argument(
        "--reviewer",
        required=True,
        metavar="CARD",
        help="the reviewer's card, as they gave it to you",
    )
    sending.add_argument(
        "--label",
        required=True,
        help="your label for the review: the author knows the reviewer by it alone",
    )
    sending.add_argument(
        "-o",
        "--output",
        type=Path,
        help="the file sent (default: beside the share, named by the label)",
    )
    receiving = actions.add_parser(
        "receive",
        help="a reviewer's review, its signature checked, passed to the author",
    )
    receiving.add_argument("review", type=Path, help="the reviewer's review file")
    receiving.add_argument(
        "--reviewer", required=True, metavar="CARD", help="the reviewer's card"
    )
    receiving.add_argument(
        "--author", required=True, metavar="CARD", help="the author's card"
    )
    receiving.add_argument(
        "--label",
        required=True,
        help="the label you gave this reviewer: a review naming another is refused",
    )
    receiving.add_argument(
        "-o",
        "--output",
        type=Path,
        help="the receipt (default: beside the review, .receipt)",
    )


def _receive_command(commands: Any) -> None:
    # a description, no help: the top-level help leaves the shares out
    found = commands.add_parser(
        "receive",
        description="a review the journal passed on, kept in the record, its findings "
        "filed as the review's",
    )
    directory_argument(found)
    found.add_argument("receipt", type=Path, help="the journal's receipt")
    found.add_argument(
        "--store",
        type=Path,
        help=STORE_HELP,
    )
    found.add_argument(
        "--journal", required=True, metavar="CARD", help="the journal's card"
    )


def _review_share_command(commands: Any) -> None:
    # a description, no help: the top-level help leaves the shares out
    found = commands.add_parser(
        "review-share",
        description="a reviewer's side of a share: kept as a workspace to read and "
        "file findings in, the review signed for the journal",
    )
    actions = found.add_subparsers(dest="action", required=True)
    opening = actions.add_parser(
        "open", help="the share the journal sent you, kept as a workspace"
    )
    opening.add_argument("share", type=Path, help="the share the journal sent")
    opening.add_argument("directory", type=Path, help="the workspace's directory")
    opening.add_argument(
        "--journal", required=True, metavar="CARD", help="the journal's card"
    )
    signing = actions.add_parser(
        "sign",
        help="your findings on the latest round and your text, signed for the journal",
    )
    signing.add_argument("directory", type=Path, help="the workspace's directory")
    signing.add_argument("--text", type=Path, help="a file holding your review's text")
    signing.add_argument(
        "-o",
        "--output",
        type=Path,
        help="the review's file (default: in the workspace)",
    )


#: `share`'s, `journal`'s, `review-share`'s and `receive`'s arguments.
ARGUMENTS: dict[str, Callable[[Any], None]] = {
    "share": _share_command,
    "journal": _journal_command,
    "review-share": _review_share_command,
    "receive": _receive_command,
}
