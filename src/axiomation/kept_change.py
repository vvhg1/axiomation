"""A value kept as its change of an earlier one (`docs/architecture.md`
§ 4): a text by the runs of its lines it replaces, a list by the runs of
its items, a JSON value part by part. Each change is applied to the same
earlier value it was taken from, and gives the value back exactly; two
values are the same only where their JSON is (`1`, `1.0` and `true` are
three).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from difflib import SequenceMatcher
from typing import Any

#: A JSON value's change: the value whole, a mapping's changed keys (and
#: its keys in order where they differ), a list's runs of items, a text's
#: runs of lines.
_WHOLE = "="
_MAPPING = "{"
_KEYS = "keys"
_LIST = "["
_TEXT = "|"


class ChangeError(ValueError):
    """A change that does not apply to the value it is applied to."""


def written(value: Any) -> str:
    """A value's JSON, compact, its keys in the order given: what two
    values are compared by."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def runs(before: Sequence[Any], after: Sequence[Any]) -> list[list[Any]]:
    """`after` as the runs of `before` it replaces, each `[first, end,
    items]`, items compared by their JSON."""
    matcher = SequenceMatcher(
        None, [written(x) for x in before], [written(x) for x in after], autojunk=False
    )
    return [
        [i1, i2, list(after[j1:j2])]
        for tag, i1, i2, j1, j2 in matcher.get_opcodes()
        if tag != "equal"
    ]


def applied_runs(before: Sequence[Any], changed: Any) -> list[Any]:
    """`before` with its runs replaced (`runs`)."""
    found: list[Any] = []
    at = 0
    try:
        for first, end, items in changed:
            if not (isinstance(first, int) and isinstance(end, int)) or not (
                at <= first <= end <= len(before)
            ):
                raise ChangeError(f"a run [{first}, {end}] out of place")
            found += before[at:first]
            found += items
            at = end
    except (TypeError, ValueError) as error:
        raise ChangeError(f"not a change of runs: {error}") from None
    return [*found, *before[at:]]


def text_change(before: str, after: str) -> list[list[Any]]:
    """`after` as the runs of `before`'s lines it replaces."""
    return runs(before.splitlines(keepends=True), after.splitlines(keepends=True))


def text_applied(before: str, changed: Any) -> str:
    found = applied_runs(before.splitlines(keepends=True), changed)
    if not all(isinstance(line, str) for _, _, items in changed for line in items):
        raise ChangeError("a run of lines holds something else than lines")
    return "".join(found)


def value_change(before: Any, after: Any) -> Any:
    """`after` as its change of `before`: a mapping by the keys whose
    values changed, a list by its runs, a text of several lines by its
    lines' runs, anything else whole. None where they are the same."""
    if written(before) == written(after):
        return None
    if isinstance(after, dict) and isinstance(before, dict):
        changed = {}
        for key, value in after.items():
            if key not in before:
                changed[key] = {_WHOLE: value}
            else:
                inner = value_change(before[key], value)
                if inner is not None:
                    changed[key] = inner
        found: dict[str, Any] = {_MAPPING: changed}
        if list(after) != list(before):
            found[_KEYS] = list(after)
        return found
    if isinstance(after, list) and isinstance(before, list):
        return {_LIST: runs(before, after)}
    if isinstance(after, str) and isinstance(before, str) and "\n" in after:
        return {_TEXT: text_change(before, after)}
    return {_WHOLE: after}


def value_applied(before: Any, changed: Any) -> Any:
    """`before` with `changed` (`value_change`) applied."""
    if changed is None:
        return before
    if not isinstance(changed, dict) or len(set(changed) - {_KEYS}) != 1:
        raise ChangeError(f"not a change of a value: {written(changed)[:80]}")
    if _WHOLE in changed:
        return changed[_WHOLE]
    if _LIST in changed:
        if not isinstance(before, list):
            raise ChangeError("a list's change applied to another value")
        return applied_runs(before, changed[_LIST])
    if _TEXT in changed:
        if not isinstance(before, str):
            raise ChangeError("a text's change applied to another value")
        return text_applied(before, changed[_TEXT])
    if _MAPPING not in changed or not isinstance(before, dict):
        raise ChangeError("a mapping's change applied to another value")
    keys = changed.get(_KEYS, list(before))
    inner = changed[_MAPPING]
    found = {}
    for key in keys:
        if key in inner:
            found[key] = value_applied(before.get(key), inner[key])
        elif key in before:
            found[key] = before[key]
        else:
            raise ChangeError(f"no value for the key {key!r}")
    return found


#: Ids that count up within one answer: its key's prefix, its attempt and
#: the first one's number (`<prefix>-<attempt>-<number>`).
Counted = tuple[str, int, int]


def counted(ids: Sequence[Any]) -> Counted | None:
    """The rule `ids` follow, each the one before it counted on, where
    they do; else None."""
    if not ids or not isinstance(ids[0], str) or ids[0].count("-") < 2:
        return None
    prefix, attempt, first = ids[0].rsplit("-", 2)
    if not (attempt.isdigit() and first.isdigit()):
        return None
    rule = (prefix, int(attempt), int(first))
    return rule if list(ids) == counted_ids(rule, len(ids)) else None


def counted_ids(rule: Sequence[Any], count: int) -> list[str]:
    """The `count` ids `rule` gives (`counted`)."""
    prefix, attempt, first = rule
    return [f"{prefix}-{attempt}-{first + n}" for n in range(count)]
