"""A store's record as written (`docs/architecture.md` § 4), for the tests
that read a record's written form, or write one as an earlier version or a
crash would have left it."""

from __future__ import annotations

import contextlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from axiomation import develop, keys
from axiomation.records import (
    RECORD,
    TIPS,
    AnswerRecord,
    EntryKind,
    RecordStore,
    _answer_body,
    _answer_from,
    _Answered,
    _encoded,
    _entry_line,
    _grant_body,
    _rebuilt,
    _Where,
    _written_line,
)


def entries(store: Path) -> list[dict[str, Any]]:
    """Every entry of the store's record but its grants, opened, in the
    order written."""
    return [e for e in _all(store) if e["kind"] != EntryKind.GRANT]


def _all(store: Path) -> list[dict[str, Any]]:
    return RecordStore(Path(store)).opened()


def _lines(store: Path) -> list[str]:
    return (Path(store) / RECORD).read_text(encoding="utf-8").splitlines(True)


def _sealing(store: Path) -> tuple[str, int, bytes]:
    """The writer, epoch and paper key a new entry of the store is
    written with; a store with no grant yet is given its first, as the
    store's first append would."""
    held = RecordStore(Path(store))
    with held._locked():
        loaded = held._load()
        mine = held._mine()
        writing = loaded.writing()
        if writing is not None:
            return mine.id, *writing
        key = keys.paper_key()
        body = _grant_body(1, key, [mine.member], keys.public_text(mine.recovery))
        grant = _entry_line(EntryKind.GRANT, [], mine.id, body, 1, None)[1]
        with (Path(store) / RECORD).open("a", encoding="utf-8") as file:
            file.write(grant)
        return mine.id, 1, key


def sealed_line(store: Path, kind: EntryKind, parents: list[str], written: str) -> str:
    """An entry's line holding `written` as its body's text, unchecked,
    sealed as the store seals its entries."""
    writer, epoch, key = _sealing(store)
    return _written_line(kind, parents, writer, written, epoch, key)[1]


def _line(
    store: Path, kind: EntryKind, parents: list[str], body: Any
) -> tuple[str, str]:
    writer, epoch, key = _sealing(store)
    return _entry_line(kind, parents, writer, body, epoch, key)


def plain(store: Path) -> list[dict[str, Any]]:
    """Every entry, an answer's body as given: its data with its raw text
    and record ids, its records by kind, each a list (an entry holds them as
    their change of an earlier answer's, `records._answer_body`)."""
    found = []
    earlier: dict[str, _Answered] = {}
    for n, entry in enumerate(entries(store), 1):
        if entry["kind"] == EntryKind.ANSWER:
            at = _Where(Path(store) / RECORD, n)
            data, records, bare = _rebuilt(
                entry["body"]["answer"], entry["body"]["records"], earlier, at
            )
            # an answer a test wrote wrong: no later one is its change
            with contextlib.suppress(ValueError):
                earlier[entry["hash"]] = _Answered(
                    _answer_from(data, at), records, at, bare
                )
            entry = entry | {"body": {"answer": data, "records": records}}
        found.append(entry)
    return found


def written(store: Path, kind: str) -> list[dict[str, Any]]:
    """Every record of `kind` (a `RecordKind`) as written, in order."""
    return [
        record
        for entry in plain(store)
        if entry["kind"] == EntryKind.ANSWER
        for record in entry["body"]["records"].get(kind, [])
    ]


def answers(store: Path) -> list[dict[str, Any]]:
    """Every answer as written, in order."""
    return [
        entry["body"]["answer"]
        for entry in plain(store)
        if entry["kind"] == EntryKind.ANSWER
    ]


def rewrite(
    store: Path, change: Callable[[dict[str, Any]], dict[str, Any] | None]
) -> None:
    """The record written again with each entry's body as `change` gives it
    (None drops the entry), an answer's as given (`plain`) and written
    whole, each entry hashed again and named again by the entries after it:
    what an earlier writer would have left."""
    # each entry's hash, and the hashes that name it now: a dropped entry's
    # children follow its parents
    renamed: dict[str, list[str]] = {}
    sealing = _sealing(store)
    lines = [
        line
        for line, entry in zip(_lines(store), _all(store), strict=True)
        if entry["kind"] == EntryKind.GRANT
    ]
    for entry in plain(store):
        body = change(entry["body"] | {"kind": entry["kind"]})
        parents = list(
            dict.fromkeys(q for p in entry["parents"] for q in renamed.get(p, [p]))
        )
        if body is None:
            renamed[entry["hash"]] = parents
            continue
        kind = EntryKind(body.pop("kind"))
        if kind is EntryKind.ANSWER:
            body = _answer_body(body["answer"], body["records"], {})
        writer, epoch, key = sealing
        digest, line = _entry_line(kind, parents, writer, body, epoch, key)
        renamed[entry["hash"]] = [digest]
        lines.append(line)
    (Path(store) / RECORD).write_text("".join(lines), encoding="utf-8")
    forget_tip(store)


def forget_tip(store: Path) -> None:
    """The tip this machine held of `store`'s record removed, as its
    person does who wrote the record over by hand: a record written over
    is one set back, refused against a tip held of it."""
    for pin in (keys.directory() / TIPS).glob("*.json"):
        if json.loads(pin.read_text(encoding="utf-8"))["record"] == str(
            Path(store).resolve()
        ):
            pin.unlink()


def rewrite_records(
    store: Path, kind: str, change: Callable[[dict[str, Any]], dict[str, Any] | None]
) -> None:
    """Each record of `kind` written again as `change` gives it (None
    drops it), as `rewrite` writes entries."""

    def records(body: dict[str, Any]) -> dict[str, Any]:
        if body["kind"] != EntryKind.ANSWER or kind not in body["records"]:
            return body
        found = [change(r) for r in body["records"][kind]]
        kept = [r for r in found if r is not None]
        return body | {"records": body["records"] | {kind: kept}}

    rewrite(store, records)


def rewrite_answers(
    store: Path, change: Callable[[dict[str, Any]], dict[str, Any] | None]
) -> None:
    """Each answer written again as `change` gives it (None drops its
    entry), as `rewrite` writes entries."""

    def answer(body: dict[str, Any]) -> dict[str, Any] | None:
        if body["kind"] != EntryKind.ANSWER:
            return body
        found = change(body["answer"])
        return None if found is None else body | {"answer": found}

    rewrite(store, answer)


def append(store: Path, kind: EntryKind, body: dict[str, Any]) -> None:
    """An entry written after the record's heads, as `body` says it (an
    answer's as given, its records by kind each a list, written whole), with
    no check of what it holds: what a writer that made a mistake would
    have left."""
    if kind is EntryKind.ANSWER and isinstance(body.get("records"), dict):
        body = _answer_body(body["answer"], body["records"], {})
    _sealing(store)  # a first entry stands on the record's first grant
    parents = {e["hash"] for e in _all(store)}
    for entry in _all(store):
        parents -= set(entry["parents"])
    line = _line(store, kind, sorted(parents), body)[1]
    with (Path(store) / RECORD).open("a", encoding="utf-8") as file:
        file.write(line)


def rewrite_blob(store: Path, name: str, data: bytes | None) -> None:
    """The blob `name` as held by the state entry that keeps it, written
    again as `data` (None drops it): bytes its name does not name, or none."""

    def change(body: dict[str, Any]) -> dict[str, Any]:
        if body["kind"] != EntryKind.STATE or name not in body["blobs"]:
            return body
        blobs = {k: v for k, v in body["blobs"].items() if k != name}
        if data is not None:
            blobs[name] = _encoded(data)
        return body | {"blobs": blobs}

    rewrite(store, change)


def rewrite_lines(
    store: Path, state: str, change: Callable[[dict[str, Any]], dict[str, Any]]
) -> None:
    """The lines' change the state `state` keeps written again as `change`
    gives it, as `rewrite` writes entries."""

    def lines(body: dict[str, Any]) -> dict[str, Any]:
        if body["kind"] != EntryKind.STATE or body["state"]["id"] != state:
            return body
        return body | {"lines": change(body["lines"])}

    rewrite(store, lines)


def text(store: Path, answer: AnswerRecord) -> str:
    """An answer's text: as the answer in hand carries it (one just asked),
    else as develop mode kept it; the record holds none."""
    return answer.raw or develop.text(store, answer.key, answer.attempt)
