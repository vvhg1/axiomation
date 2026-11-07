"""A share: what an author gives a reader outside the paper's record (a
journal, and a reviewer through it): the paper's graph at one kept
state, with the files it was read from, who wrote each line and with
which model withheld; in the record's own lines, sealed to its reader
and signed by the author. The record never leaves; the share sent is an entry of it."""

from __future__ import annotations

import base64
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath
from typing import Any

from axiomation import judgments, keys, versions
from axiomation.exchange import dumps
from axiomation.questions.review_file import CONVENTIONS_FILE
from axiomation.reader.includes import STORE_DIRECTORY
from axiomation.records import (
    FINDING,
    PERSON,
    REVIEWER,
    EntryKind,
    Finding,
    FindingStatus,
    JudgmentRecords,
    OpenedDocument,
    RecordError,
    RecordStore,
    finding_from_data,
    judgment_data,
    opened_document,
    sealed_document,
)
from axiomation.review import bib_entry, bib_field_spans
from axiomation.settings import CONFIG_NAME
from axiomation.versions import State

#: A share's file name ends so.
SUFFIX = ".share"
#: Who a shared line names as its writer: the author, or an agent.
AUTHOR = "author"
AGENT = "agent"
#: The export's sections a share withholds: the person rounds and the
#: update's record (`review`), the answer its edges were read from, the
#: tool that cut it.
WITHHELD = ("review", "edges_answer", "producer")
#: The export's sections that hold lines, each with its provenance.
LINES = ("edges", "self", "standings")


class ShareError(ValueError):
    """A share that cannot be cut, written or opened."""


@dataclass(frozen=True)
class Share:
    """What a share holds: its round, the share it continues (its entry's
    hash, none in round 1), the state it was cut from and its version, the
    paper's root, the export as shared, the state's files by path, and the
    node of each block and group as the author's store minted them; from
    round 2, a reply to each review of an earlier round (`replies_for`);
    the files the author named to give besides the paper's (`--file`);
    and, as the journal sends it on, its label for the review asked and
    the author's share it came from."""

    round: int
    continues: str | None
    state: str
    version: str
    root: str
    export: dict[str, Any]
    files: dict[str, bytes]
    nodes: dict[str, str]
    groups: dict[str, str]
    label: str | None = None
    origin: str | None = None
    replies: tuple[dict[str, Any], ...] = ()
    given: tuple[str, ...] = ()
    #: how each bibliography was cut, for the author's listing alone
    bibliographies: dict[str, str] = field(default_factory=dict, compare=False)

    def body(self) -> dict[str, Any]:
        return {
            "round": self.round,
            "continues": self.continues,
            "state": self.state,
            "version": self.version,
            "root": self.root,
            "export": self.export,
            "files": {
                p: base64.b64encode(d).decode("ascii")
                for p, d in sorted(self.files.items())
            },
            "nodes": self.nodes,
            "groups": self.groups,
            **({} if self.label is None else {"label": self.label}),
            **({} if self.origin is None else {"origin": self.origin}),
            **({"replies": list(self.replies)} if self.replies else {}),
            **({"given": list(self.given)} if self.given else {}),
        }

    def named(self) -> frozenset[str]:
        """The files its export names: those the paper's document reads,
        its bibliographies, and those the author named to give; what a
        reader's workspace writes, and no other."""
        read = {f["path"] for f in self.export["document"]["files"]}
        return frozenset(
            p
            for p in self.files
            if p in read or p in self.given or PurePosixPath(p).suffix == ".bib"
        )


def _share_from(body: dict[str, Any], where: Path) -> Share:
    try:
        return Share(
            int(body["round"]),
            body["continues"],
            str(body["state"]),
            str(body["version"]),
            str(body["root"]),
            dict(body["export"]),
            {p: base64.b64decode(d, validate=True) for p, d in body["files"].items()},
            {str(b): str(n) for b, n in body["nodes"].items()},
            {str(g): str(n) for g, n in body["groups"].items()},
            body.get("label"),
            body.get("origin"),
            tuple(dict(r) for r in body.get("replies", ())),
            tuple(str(p) for p in body.get("given", ())),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ShareError(f"{where}: not a share's body ({error})") from None


def shared_export(export: dict[str, Any]) -> dict[str, Any]:
    """`export` as a share gives it: each line's provenance cut to who
    wrote it, the author or an agent (no model, answer, stage or
    template), the sections `WITHHELD` left out, and the document without
    the names of the files the paper's reading left out (`never_included`:
    files the share withholds, named nowhere)."""
    shared = {k: v for k, v in export.items() if k not in WITHHELD}
    if "document" in shared:
        shared["document"] = {
            k: v for k, v in shared["document"].items() if k != "never_included"
        }
    for section in LINES:
        shared[section] = [
            {
                **line,
                "provenance": {
                    "by": AUTHOR if line["provenance"]["by"] == PERSON else AGENT
                },
            }
            for line in export.get(section, [])
        ]
    return shared


#: A bibliography's fields a share drops: where the author keeps the
#: work, and what they wrote about it for themselves.
PRIVATE_FIELDS = frozenset(
    {
        "file",
        "pdf",
        "localfile",
        "local-url",
        "annote",
        "annotation",
        "comment",
        "comments",
        "keywords",
        "groups",
        "mendeley-tags",
        "owner",
        "timestamp",
        "date-added",
        "date-modified",
    }
)
#: A value naming a place on the author's machine, or in their library.
_LOCAL_PATH = re.compile(
    r"(?:^|[\s{:;])(?:/Users/|/home/|/Volumes/|~/|[A-Za-z]:[\\/])|zotero://|file://"
)


@dataclass(frozen=True)
class Cited:
    """A bibliography as a share gives it: the cited entries alone, how
    many it held, and the fields dropped."""

    text: str
    entries: int
    of: int
    dropped: int


#: A `@string` definition's opening, and its name.
_BIB_STRING = re.compile(r"@string\s*\{\s*([A-Za-z][\w-]*)\s*=", re.IGNORECASE)
#: A value's quoted or braced parts, which name no macro.
_BIB_LITERAL = re.compile(r'"[^"]*"|\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}')


def _braced(text: str, start: int) -> str:
    """The `@…{…}` block of `text` opening at `start`, to its closing
    brace."""
    depth, end = 0, start
    for end in range(text.index("{", start), len(text)):
        depth += {"{": 1, "}": -1}.get(text[end], 0)
        if depth == 0:
            break
    return text[start : end + 1]


def _needed(text: str, keys_cited: Sequence[str]) -> tuple[list[str], list[str]]:
    """The entries a cut of `text` must keep for `keys_cited` to read as
    in the whole: the cited ones, then each `crossref` parent they inherit
    fields from (after them, as BibTeX reads it); and the `@string` macros
    those entries' values name, in the file's order."""
    keys = list(dict.fromkeys(keys_cited))
    seen = set(keys)
    macros: set[str] = set()
    at = 0
    while at < len(keys):
        entry = bib_entry(text, keys[at])
        at += 1
        for name, _, _, value in bib_field_spans(entry):
            if name == "crossref":
                parent = value.strip().strip('{}"').strip()
                if parent and parent not in seen:
                    seen.add(parent)
                    keys.append(parent)
            for part in _BIB_LITERAL.sub(" ", value).split("#"):
                word = part.strip()
                if re.fullmatch(r"[A-Za-z][\w-]*", word):
                    macros.add(word.casefold())
    strings = [
        _braced(text, m.start())
        for m in _BIB_STRING.finditer(text)
        if m.group(1).casefold() in macros
    ]
    return keys, strings


def cited_bibliography(text: str, keys_cited: Sequence[str]) -> Cited:
    """The entries of the bibliography `text` the paper cites, in its
    order, with the `crossref` parents they inherit from and the `@string`
    macros they use, each without the fields `PRIVATE_FIELDS` names or a
    value naming a place on the author's machine."""
    total = len(re.findall(r"@(?!string\b|comment\b|preamble\b)\w+\s*\{", text, re.I))
    kept: list[str] = []
    dropped = 0
    keys, strings = _needed(text, keys_cited)
    for key in keys:
        entry = bib_entry(text, key)
        if not entry:
            continue
        spans = [
            (start, end)
            for name, start, end, value in bib_field_spans(entry)
            if name in PRIVATE_FIELDS or _LOCAL_PATH.search(value)
        ]
        dropped += len(spans)
        for start, end in reversed(spans):
            # the first field follows the key's comma: its own comma after
            # it goes with it
            if entry[:start].rstrip().endswith(","):
                after = re.match(r"\s*,", entry[end:])
                end += after.end() if after else 0
            entry = entry[:start] + entry[end:]
        kept.append(entry)
    blocks = [*strings, *kept] if kept else []
    return Cited(
        "\n\n".join(blocks) + ("\n" if blocks else ""), len(kept), total, dropped
    )


def _cited_keys(export: dict[str, Any]) -> list[str]:
    return [
        k for c in export["document"].get("citations", []) for k in c.get("keys", ())
    ]


def shared_files(
    export: dict[str, Any], kept: State, more: Sequence[str] = ()
) -> list[str]:
    """The kept files a share gives whole: those the paper's document
    reads, and `more`, each one the state keeps; never the rest a state
    keeps unless named: the author's notes, scripts, a PDF. A bibliography
    goes cut to what is cited (`cut`), whole only where named. Never the
    paper's settings: a reader's own settings are theirs."""
    read = [f["path"] for f in export["document"]["files"]]
    refused = [p for p in sorted({*read, *more}) if _refused_name(p)]
    if refused:
        raise ShareError(
            f"a share never gives {refused[0]!r}: a reader's workspace refuses "
            "it (a hidden file, a workspace's own, or the reader's settings "
            "or conventions)"
        )
    missing = [p for p in more if p not in kept.files]
    if missing:
        raise ShareError(
            f"state {kept.id[:12]} keeps no file {missing[0]!r}; it keeps: "
            + ", ".join(sorted(kept.files))
        )
    return sorted({*read, *more})


def cut(
    store: RecordStore,
    kept: State,
    round: int,
    continues: str | None,
    replies: tuple[dict[str, Any], ...] = (),
    more: Sequence[str] = (),
) -> Share:
    """The share of `kept`, round `round`, with `replies`; its files those
    `shared_files` gives."""
    try:
        export = versions.kept_payload(store, kept)
        files = {
            p: versions.blob(store, kept.files[p])
            for p in shared_files(export, kept, more)
        }
        cited = {
            p: cited_bibliography(
                versions.blob(store, b).decode("utf-8", "replace"),
                _cited_keys(export),
            )
            for p, b in kept.files.items()
            if p.endswith(".bib") and p not in files
        }
    except versions.VersionError as error:
        raise ShareError(str(error)) from None
    files |= {p: c.text.encode("utf-8") for p, c in cited.items()}
    return Share(
        round,
        continues,
        kept.id,
        kept.version,
        kept.root,
        shared_export(export),
        files,
        {n.block: n.node for n in kept.nodes},
        dict(kept.groups),
        replies=replies,
        given=tuple(sorted(set(more))),
        bibliographies={
            p: f"{c.entries} cited entries of {c.of}, {c.dropped} private or "
            "local fields dropped"
            for p, c in cited.items()
        },
    )


def next_round(store: RecordStore, reader: str) -> tuple[int, str | None]:
    """The round a share to `reader` is next, and the share it continues:
    the last one sent to them."""
    sent = [s for s in store.sent() if s.reader == reader]
    return (sent[-1].round + 1, sent[-1].share) if sent else (1, None)


def replies_for(store: RecordStore, reader: str) -> tuple[dict[str, Any], ...]:
    """The author's reply to each review of a share sent to `reader`, the
    latest round of each label: the review as the reviewer signed it, and
    the answer that speaks for each of its findings, in its order (its
    status and note; none where unanswered or the answer waits for the
    author's word)."""
    to_reader = {s.share for s in store.sent() if s.reader == reader}
    latest: dict[str, dict[str, Any]] = {}
    for _, body in store.reviews():
        held = latest.get(body["label"])
        if body["origin"] in to_reader and (
            held is None or body["round"] > held["round"]
        ):
            latest[body["label"]] = body
    if not latest:
        return ()
    records = store.judgment_records()
    answered = judgments.answers_of(store)
    replies = []
    for label, body in latest.items():
        filed = [f for f in records.findings if f.answer == body.get("filing")]
        # a finding refiled (`reaffirms`) is answered under its latest filing
        speaking = [
            judgments.spoken(answered.get(judgments.latest_filing(store, f).id, []))[0]
            for f in filed
        ]
        place = {f.id: n for n, f in enumerate(filed, 1)}
        # a duplicate names the finding it duplicates by its place in the
        # review; one outside it is named as such, its words the author's
        answers = [
            None
            if a is None
            else {
                "status": a.status.value,
                **({"note": a.note} if a.note else {}),
                **(
                    {"of": place.get(a.of, "a finding outside this review")}
                    if a.of is not None
                    else {}
                ),
            }
            for a in speaking
        ]
        replies.append(
            {
                "label": label,
                "round": body["round"],
                "review": body["review"],
                "answers": answers,
            }
        )
    return tuple(replies)


def listing(
    share: Share, reader: keys.Member, full: dict[str, Any], kept: State
) -> list[str]:
    """What a share gives besides the graph, and what it withholds, as
    lines for the author to read before it is sent; `full` the state's
    export as the record holds it, `kept` the state."""
    export = share.export
    size = sum(len(d) for d in share.files.values())
    by = [line["provenance"]["by"] for section in LINES for line in export[section]]
    comments = len(export.get("document", {}).get("comments", []))
    withheld = [k for k in WITHHELD if k in full]
    lines = [
        f"share round {share.round} to {reader.id}, of state {share.state[:12]}"
        + (f", continuing {share.continues[:12]}" if share.continues else ""),
        "it gives:",
        f"  the graph: {len(export.get('blocks', []))} blocks, "
        f"{len(export.get('groups', []))} groups, "
        + ", ".join(f"{len(export.get(s, []))} {s}" for s in LINES),
        f"  {len(share.files)} files, {size} bytes: " + ", ".join(sorted(share.files)),
        f"  who wrote each line: {by.count(AUTHOR)} by the author, "
        f"{by.count(AGENT)} by an agent",
    ]
    for reply in share.replies:
        answered = sum(
            a is not None and a["status"] != FindingStatus.POSTPONED.value
            for a in reply["answers"]
        )
        lines.append(
            f"  your reply to review {reply['label']} (round {reply['round']}): "
            f"{answered} of {len(reply['answers'])} findings answered; the "
            "journal sends each reviewer their own"
        )
    if comments:
        lines.append(
            f"  {comments} comments in the files, which go with them as written"
        )
    lines += [
        "it withholds:",
        "  each line's model, answer, stage and template",
        "  judgments, findings, rulings and suggestions",
    ]
    lines += [f"  the export's {k!r}" for k in withheld]
    others = sorted(set(kept.files) - set(share.files))
    if others:
        lines.append(
            f"  {len(others)} files the state keeps beside the paper (--file "
            "adds one): " + ", ".join(others)
        )
    for path, how in share.bibliographies.items():
        lines.insert(4, f"  {path}: {how}")
    given = [
        p for p in share.files if p.endswith(".bib") and p not in share.bibliographies
    ]
    if given:
        lines.append(
            "warning: a bibliography goes whole, every entry and field as "
            "written (local paths too): " + ", ".join(given)
        )
    return lines


def written(mine: keys.Keys, share: Share, reader: keys.Member) -> tuple[str, str]:
    """The share sealed to `reader` (and its writer) and signed: its
    entry's hash and its text. Its sending is the caller's to keep
    (`sent`), once the share is written where it goes."""
    try:
        return sealed_document(mine, EntryKind.SHARE, share.body(), [reader])
    except RecordError as error:
        raise ShareError(str(error)) from None


def sent(store: RecordStore, digest: str, share: Share, reader: keys.Member) -> None:
    """A share written kept as sent: what the next round continues."""
    try:
        store.record_sent(digest, share.round, reader.id, share.state, share.continues)
    except RecordError as error:
        raise ShareError(str(error)) from None


def sent_on(
    mine: keys.Keys, share: Share, origin: str, label: str, reviewer: keys.Member
) -> tuple[str, str]:
    """The share as the journal sends it on to a reviewer: under its label
    (`LABEL`)
    for the review, naming the author's share it came from (`origin`, its
    entry), sealed to the reviewer and signed by the journal; the author's
    card and signature stay with the journal. Its entry's hash and its
    text."""
    if not LABEL.fullmatch(label):
        raise ShareError(f"{label!r} is not a label: letters, digits, - and _")
    try:
        return sealed_document(
            mine,
            EntryKind.SHARE,
            replace(
                share,
                label=label,
                origin=origin,
                replies=tuple(r for r in share.replies if r["label"] == label),
            ).body(),
            [reviewer],
        )
    except RecordError as error:
        raise ShareError(str(error)) from None


@dataclass(frozen=True)
class Opened:
    """A share opened: what it holds, who wrote it, and its entry's hash."""

    share: Share
    writer: keys.Member
    entry: str


def _document(
    path: Path, mine: keys.Keys, kind: EntryKind, writer: keys.Member
) -> OpenedDocument:
    """The document in `path` opened and held against its writer's card."""
    try:
        return opened_document(
            Path(path).read_text(encoding="utf-8"), mine, kind, Path(path), writer
        )
    except (OSError, RecordError) as error:
        raise ShareError(str(error)) from None


def opened(path: Path, mine: keys.Keys, writer: keys.Member) -> Opened:
    """The share in `path` opened with the reader's keys; refused where it
    does not open, or `writer` (the author's card, or the journal's) did
    not write and sign it."""
    found = _document(path, mine, EntryKind.SHARE, writer)
    return Opened(_share_from(found.body, path), found.writer, found.entry)


# --- the reviewer's side -----------------------------------------------------

#: The reviewer's workspace's own file, beside the paper's files: the
#: journal's card, the review's label, and each round received.
WORKSPACE = "review-share.json"
#: A review's file name ends so.
REVIEW_SUFFIX = ".review"
#: The names a workspace's own documents end in, which no shared file takes:
#: a review signed there, the journal's receipt, a share.
_OWN_SUFFIXES = (REVIEW_SUFFIX, ".receipt", SUFFIX)


@dataclass(frozen=True)
class Review:
    """A review of a share: the journal's label, the round, the author's
    share it reviews (`origin`) and the state it was cut from, the
    reviewer's text and their findings, anchored on the author's nodes."""

    label: str
    round: int
    origin: str
    state: str
    text: str
    findings: tuple[Finding, ...]

    def body(self) -> dict[str, Any]:
        # a finding's id is its filing's key, a hash over the reviewer's
        # name and time: never sent; nor the id a refiling names, which
        # names nothing in the author's record
        return {
            "label": self.label,
            "round": self.round,
            "origin": self.origin,
            "state": self.state,
            "text": self.text,
            "findings": [
                judgment_data(replace(f, id="", reaffirms=None)) for f in self.findings
            ],
        }


def review_from(body: dict[str, Any], where: Path) -> Review:
    try:
        return Review(
            str(body["label"]),
            int(body["round"]),
            str(body["origin"]),
            str(body["state"]),
            str(body["text"]),
            tuple(
                finding_from_data(f, f"{where}: finding {n}")
                for n, f in enumerate(body["findings"], 1)
            ),
        )
    except (KeyError, TypeError, ValueError, RecordError) as error:
        raise ShareError(f"{where}: not a review's body ({error})") from None


def _workspace(directory: Path) -> dict[str, Any]:
    path = Path(directory) / WORKSPACE
    if not path.is_file():
        raise ShareError(f"{directory} is no review workspace: no {WORKSPACE}")
    return json.loads(path.read_text(encoding="utf-8"))


#: A label, as a journal names a review: it names files too.
LABEL = re.compile(r"[A-Za-z0-9_-]{1,64}")


#: The files a reader's own axiomation reads from a paper's directory as
#: theirs: their settings, and the conventions their review sessions read.
_READERS_OWN = (CONFIG_NAME.casefold(), CONVENTIONS_FILE.casefold())


def _refused_name(name: str) -> bool:
    """Whether a reader's workspace refuses a shared file named so: outside
    it, a hidden file or directory (`.git/config` would run a command at
    the reviewer's next `git status`), the workspace's own store or file,
    a name its own documents take (a review the reviewer signed there), or
    a file the reader's axiomation reads as theirs (`_READERS_OWN`). Names
    are compared case-folded: a case-insensitive file system reads
    `Axiomation.toml` as `axiomation.toml`."""
    parts = PurePosixPath(name).parts
    return (
        not parts
        or PurePosixPath(name).is_absolute()
        or any(p in ("", ".", "..") or p.startswith(".") for p in parts)
        or "\\" in name
        or "\0" in name
        or parts[0].casefold() in (STORE_DIRECTORY.casefold(), WORKSPACE.casefold())
        or parts[-1].casefold().endswith(_OWN_SUFFIXES)
        or parts[-1].casefold() in _READERS_OWN
    )


def _paper_path(directory: Path, name: str) -> Path:
    """Where a shared file is written: under `directory`; refused where
    `_refused_name` says so."""
    if _refused_name(name):
        raise ShareError(f"the share names a file a workspace refuses: {name!r}")
    return Path(directory).joinpath(*PurePosixPath(name).parts)


@dataclass(frozen=True)
class Received:
    """A round kept in the reviewer's workspace: its state, the author's
    reply to the reviewer's last review, and whether the review it carries
    back is the one they signed (none where there is no reply)."""

    kept: State
    reply: dict[str, Any] | None
    #: none where there is no reply, or no review signed here to hold it to
    held: bool | None
    #: the files the share holds that its export does not name: not written
    withheld: tuple[str, ...] = ()
    #: the last round whose review was signed here, where the reply the
    #: share carries is not to it: a signed review that did not come back
    unreturned: int | None = None


def received(found: Opened, journal: keys.Member, directory: Path) -> Received:
    """The share sent on to this reviewer, kept in their workspace: the
    files its export names written under `directory` (any other listed in
    `withheld`, not written), its graph kept as a state of the
    workspace's own store under the author's nodes (so `show`, `reach`,
    `view` and `find` work there), after the last round's state (so
    `changes` gives what changed since), the round noted. The reply it
    carries is held against the review the reviewer signed: a review the
    journal or the author changed is found, not prevented. Refused where
    another label's workspace is there, or the round is there already."""
    share = found.share
    if share.label is None or share.origin is None:
        raise ShareError("this share was not sent on by a journal: it has no label")
    if not LABEL.fullmatch(share.label):
        raise ShareError(f"the share's label {share.label!r} is not a label")
    directory = Path(directory)
    path = directory / WORKSPACE
    noted = (
        _workspace(directory)
        if path.is_file()
        else {"journal": keys.card(journal), "label": share.label, "rounds": []}
    )
    if noted["label"] != share.label or noted["journal"] != keys.card(journal):
        raise ShareError(
            f"{directory} is the workspace of review {noted['label']}, not this one"
        )
    if noted["rounds"] and share.round <= noted["rounds"][-1]["round"]:
        raise ShareError(
            f"round {share.round} is not after round {noted['rounds'][-1]['round']}, "
            f"the last in {directory}"
        )
    if not path.is_file() and directory.exists() and any(directory.iterdir()):
        raise ShareError(f"{directory} is not empty")
    # only what the export names is written: any other file (the author's
    # settings, a script) would act in the reviewer's workspace as theirs
    for name in share.files:
        # a name no workspace takes is a share made to harm: refused whole
        _paper_path(directory, name)
    named = share.named()
    files = {p: d for p, d in share.files.items() if p in named}
    targets = {name: _paper_path(directory, name) for name in files}
    # a round writes over the files the last wrote, and no file of the
    # reviewer's own; those the last wrote and this one does not hold go
    last = set(noted["rounds"][-1].get("files", ())) if noted["rounds"] else set()
    theirs = [n for n, p in targets.items() if p.exists() and n not in last]
    if theirs:
        raise ShareError(
            f"the share would write over files of yours in {directory}: "
            + ", ".join(sorted(theirs))
        )
    store = RecordStore(versions.default_store(directory))
    if versions.keeps_state(versions.default_store(directory)):
        # what the round keeps is refused before any file is written
        versions.refuse_apart(store)
        versions.refuse_beside_waiting(
            store,
            versions.state(store, noted["rounds"][-1]["kept"], waiting=True)
            if noted["rounds"]
            else None,
        )
    for name in sorted(last - set(files)):
        _paper_path(directory, name).unlink(missing_ok=True)
    for name, target in targets.items():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(files[name])
    try:
        before = (
            versions.state(store, noted["rounds"][-1]["kept"])
            if noted["rounds"]
            else None
        )
        kept, _ = versions.record(
            store,
            files,
            share.version,
            share.root,
            dumps(share.export).encode("utf-8"),
            versions.Command.BUILD,
            started_from=before,
            given=(share.nodes, share.groups),
        )
    except versions.VersionError as error:
        raise ShareError(str(error)) from None
    reply = next((r for r in share.replies if r["label"] == share.label), None)
    signed = next(
        (
            r["review"]["body"]
            for r in noted["rounds"]
            if reply is not None and r["review"] and r["round"] == reply["round"]
        ),
        None,
    )
    held = None if reply is None or signed is None else reply["review"] == signed
    signed_rounds = [r["round"] for r in noted["rounds"] if r["review"]]
    unreturned = (
        signed_rounds[-1]
        if signed_rounds and (reply is None or reply["round"] != signed_rounds[-1])
        else None
    )
    noted["rounds"].append(
        {
            "round": share.round,
            "share": found.entry,
            "origin": share.origin,
            "state": share.state,
            "kept": kept.id,
            "review": None,
            "reply": reply,
            "held": held,
            "files": sorted(files),
            # the findings filed before this round came: no review of it
            # sends them, so none appears in a round it was not filed in
            "before": sorted(_filed(store)),
        }
    )
    _note(directory, noted)
    return Received(
        kept,
        reply,
        held,
        tuple(sorted(set(share.files) - set(files))),
        unreturned,
    )


#: The label a preview's workspace is kept under.
PREVIEW = "preview"


def previewed(share: Share, author: keys.Member, directory: Path) -> State:
    """The share kept as a reviewer's workspace would keep it, under the
    label `preview`, before it is sent: the author's own view of what a
    reader gets (`axiomation view DIRECTORY`)."""
    return received(
        Opened(replace(share, label=PREVIEW, origin=PREVIEW), author, PREVIEW),
        author,
        directory,
    ).kept


def _note(directory: Path, noted: dict[str, Any]) -> None:
    (Path(directory) / WORKSPACE).write_text(
        json.dumps(noted, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def _unsent(directory: Path, waiting: bool) -> tuple[list[Finding], list[Finding]]:
    """The reviewer's findings no signed review carried and they did not
    close themselves (dismissed, a duplicate, fixed): those on the latest
    round's state, and those on an earlier round's, which no review sends
    (a review is of one round). `waiting`: refused while a filing waits
    for the reviewer's word."""
    noted = _workspace(directory)
    last = noted["rounds"][-1]
    store = RecordStore(versions.store_of(Path(directory)))
    try:
        if waiting:
            store.refuse_waiting("signing the review")
        findings = store.judgment_records().findings
    except RecordError as error:
        raise ShareError(str(error)) from None
    # a round whose graph did not change keeps the last round's state: a
    # finding a review signed before is not filed again
    sent = {i for r in noted["rounds"] if r["review"] for i in r["review"]["filed"]}
    answered = judgments.answers_of(store)
    closed = {
        i
        for i, a in answered.items()
        if (said := judgments.spoken(a)[0]) is not None
        and said.status is not FindingStatus.POSTPONED
    }
    # a finding filed before the latest round came is of an earlier round,
    # whatever state it is on: never sent, so it cannot appear later as if
    # the author had it before
    before = set(last.get("before", ()))
    unsent = [f for f in findings if f.id not in sent | closed]
    return (
        [f for f in unsent if f.state == last["kept"] and f.id not in before],
        [f for f in unsent if f.state != last["kept"] or f.id in before],
    )


def _filed(store: RecordStore) -> set[str]:
    """Every finding the reviewer's store holds, waiting for their word
    or not."""
    try:
        return {f.id for f in store.judgment_records(waiting=True).findings}
    except RecordError as error:
        raise ShareError(str(error)) from None


def unsigned(directory: Path) -> list[Finding]:
    """The findings the reviewer filed in an earlier round, on any state,
    and never signed: no review sends them; filed again in the latest
    round, the next review does."""
    return _unsent(directory, waiting=False)[1]


def review(directory: Path, text: str) -> Review:
    """The review of the workspace's latest round: the reviewer's text and
    every finding they filed on its state that no review signed and they
    did not close, each on the author's state. A finding waits for the
    reviewer's word as any person's filing does: refused while one waits,
    never confirmed for them. A finding on an earlier round's state is not
    in it (`unsigned`)."""
    noted = _workspace(directory)
    last = noted["rounds"][-1]
    current, _ = _unsent(directory, waiting=True)
    return Review(
        noted["label"],
        last["round"],
        last["origin"],
        last["state"],
        text,
        tuple(replace(f, answer=None, state=last["state"]) for f in current),
    )


def signed_review(directory: Path, mine: keys.Keys, found: Review, output: Path) -> str:
    """`found` sealed to the workspace's journal, signed by the reviewer
    and written to `output`; then noted in the workspace as signed, so a
    later round's reply is held against it (a write that fails notes
    nothing). Refused where the round was signed already. Its entry's
    hash."""
    noted = _workspace(directory)
    if noted["rounds"][-1]["review"]:
        raise ShareError(
            f"round {noted['rounds'][-1]['round']}'s review is signed already: "
            f"{noted['rounds'][-1]['review']['entry'][:12]}"
        )
    journal = keys.card_from(noted["journal"])
    try:
        digest, text = sealed_document(mine, EntryKind.REVIEW, found.body(), [journal])
    except RecordError as error:
        raise ShareError(str(error)) from None
    Path(output).write_text(text, encoding="utf-8")
    noted["rounds"][-1]["review"] = {
        "entry": digest,
        "body": found.body(),
        "filed": [f.id for f in found.findings],
    }
    _note(directory, noted)
    return digest


def opened_review(
    path: Path, mine: keys.Keys, writer: keys.Member
) -> tuple[Review, OpenedDocument]:
    """A review, or the journal's receipt of one, opened with the reader's
    keys: what it holds, and the document as opened (its writer, its entry
    and its body as signed)."""
    found = _document(path, mine, EntryKind.REVIEW, writer)
    return review_from(found.body, path), found


def received_review(
    store: RecordStore, mine: keys.Keys, path: Path, journal: keys.Member
) -> tuple[Review, str]:
    """A receipt the journal passed on, kept in the author's record: one
    `review` entry holding the review and the receipt as written (the
    journal's signature checkable again), and its findings filed as the
    reviewer's (host `reviewer`, model the label), answered as any finding
    is, the filing named in the entry. Refused where it reviews no share
    this record sent, or this review's round is kept already. The review,
    and the filing's key."""
    found, sealed = opened_review(path, mine, journal)
    sent = next((s for s in store.sent() if s.share == found.origin), None)
    if sent is None or sent.reader != journal.id:
        raise ShareError(
            f"{path}: it reviews a share this record never sent to this journal"
        )
    if (found.round, found.state) != (sent.round, sent.state):
        raise ShareError(
            f"{path}: it names round {found.round} of state {found.state[:12]}; the "
            f"share it reviews was round {sent.round} of state {sent.state[:12]}"
        )
    elsewhere = [f.problem for f in found.findings if f.state != found.state]
    if elsewhere:
        raise ShareError(
            f"{path}: a finding names another state than the review's "
            f"{found.state[:12]}: {elsewhere[0]!r}"
        )
    if any(
        b["label"] == found.label and b["round"] == found.round
        for _, b in store.reviews()
    ):
        raise ShareError(
            f"review {found.label}'s round {found.round} is in the record already"
        )
    # a receive cut short between its filing and its entry left the filing:
    # taken again, never filed twice
    key = next(
        (
            a.key
            for a in store.answers()
            if a.host == REVIEWER
            and a.model == found.label
            and [
                (f.state, f.problem)
                for f in store.judgment_records().findings
                if f.answer == a.key
            ]
            == [(found.state, f.problem) for f in found.findings]
            and not any(b.get("filing") == a.key for _, b in store.reviews())
        ),
        "",
    )
    try:
        if found.findings and not key:
            key = judgments.file_records(
                store,
                FINDING,
                JudgmentRecords(
                    findings=tuple(replace(f, id="") for f in found.findings)
                ),
                judgments.Author(REVIEWER, found.label),
            ).answer.key
        store.record_review(
            {
                "label": found.label,
                "round": found.round,
                "origin": found.origin,
                "filing": key,
                "review": sealed.body,
                "receipt": Path(path).read_text(encoding="utf-8"),
            }
        )
    except (RecordError, judgments.JudgmentError) as error:
        raise ShareError(str(error)) from None
    return found, key


def receipt(
    mine: keys.Keys, review: OpenedDocument, label: str, author: keys.Member
) -> tuple[str, str]:
    """A review as the journal passes it to the author: its body as the
    reviewer signed it, sealed to the author and signed by the journal;
    the reviewer's card stays with the journal. Refused where the review
    names another label than the one the journal gave this reviewer."""
    if review.body.get("label") != label:
        raise ShareError(
            f"the review names label {review.body.get('label')!r}; you gave this "
            f"reviewer {label!r}"
        )
    try:
        return sealed_document(mine, EntryKind.REVIEW, review.body, [author])
    except RecordError as error:
        raise ShareError(str(error)) from None
