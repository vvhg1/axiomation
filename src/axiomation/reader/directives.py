"""The five comment directives: their spelling and their parsing.

All tool markup is an HTML comment in Markdown and a `%` comment in
LaTeX:

    <!-- @assumptions ass:bounded,ass:smooth -->
    <!-- @meta rigor=argued assumes=+ass:noise -->
    <!-- @claim C1 from=thm:main -->
    <!-- @claim C2 start -->  ...  <!-- @claim C2 end -->
    <!-- @sorry proof of the lower bound pending -->
    <!-- @node thm:main -->

The tool records them as the author's marks; it does not act on
them.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

DIRECTIVE_RE = re.compile(r"<!--\s*@(?P<name>[a-z_]+)(?P<body>.*?)-->", re.DOTALL)

#: One argument of a directive body: a `key="quoted value"`, a `key=bare`, a
#: `"quoted word"` or a bare word. The quoted forms exist because a label may
#: carry a space; quoting is optional, so a body with no quote in it
#: tokenizes exactly as a whitespace split did.
_TOKEN_RE = re.compile(
    r"""
      (?P<key>[^\s="]+)=(?:"(?P<quoted_value>[^"]*)"|(?P<value>\S*))
    | "(?P<quoted_word>[^"]*)"
    | (?P<word>\S+)
    """,
    re.VERBOSE,
)


class DirectiveKind(StrEnum):
    """The five directives. An `@` comment with any other name is a comment."""

    META = "meta"
    ASSUMPTIONS = "assumptions"
    CLAIM = "claim"
    SORRY = "sorry"
    NODE = "node"


@dataclass(frozen=True, slots=True)
class Directive:
    """One directive occurrence, with where in the line it sat.

    The column matters: `@claim` in sentence form marks the text that comes
    before the marker on the same line.
    """

    kind: DirectiveKind
    line: int
    start: int
    end: int
    raw: str
    words: tuple[str, ...]
    pairs: Mapping[str, str]

    @property
    def first(self) -> str:
        return self.words[0] if self.words else ""

    def flag(self, name: str) -> bool:
        return name in self.words[1:]


def parse_directives(
    lines: Sequence[str], pattern: re.Pattern[str] = DIRECTIVE_RE
) -> list[Directive]:
    """Every directive in the file, in reading order.

    `pattern` is the format's spelling of the comment a directive sits in: an
    HTML comment in Markdown+LaTeX, a `%` comment in LaTeX. It
    must carry the `name` and `body` groups; one tokenizer reads both.
    """
    found: list[Directive] = []
    for index, line in enumerate(lines):
        for match in pattern.finditer(line):
            kind = _kind(match.group("name"))
            if kind is None:
                continue
            body = match.group("body").strip()
            words, pairs = arguments(kind, body)
            found.append(
                Directive(
                    kind=kind,
                    line=index + 1,
                    start=match.start(),
                    end=match.end(),
                    raw=body,
                    words=words,
                    pairs=MappingProxyType(pairs),
                )
            )
    return found


def _kind(name: str) -> DirectiveKind | None:
    try:
        return DirectiveKind(name)
    except ValueError:
        return None


def arguments(kind: DirectiveKind, body: str) -> tuple[tuple[str, ...], dict[str, str]]:
    """Split a directive body into positional words and `key=value` pairs.

    `@sorry` takes free text, so it is never split into pairs; its whole
    body is the author's note about what is missing.

    A value may be quoted, because a label may carry a space. The
    quotes are stripped here, so every reader downstream — `split_list`
    included — gets the value the author meant. A regex and not `shlex`: an
    unbalanced quote is a typing mistake in a manuscript, and a parser that
    raises on it turns that mistake into a crash. Here it stays an ordinary
    character.
    """
    if kind is DirectiveKind.SORRY:
        return ((body,) if body else (), {})
    words: list[str] = []
    pairs: dict[str, str] = {}
    for match in _TOKEN_RE.finditer(body):
        key = match.group("key")
        if key is not None:
            quoted = match.group("quoted_value")
            pairs[key] = quoted if quoted is not None else match.group("value")
            continue
        quoted_word = match.group("quoted_word")
        words.append(quoted_word if quoted_word is not None else match.group("word"))
    return (tuple(words), pairs)


def split_list(value: str) -> tuple[str, ...]:
    """A comma-separated directive value, with empty entries dropped."""
    return tuple(item.strip() for item in value.split(",") if item.strip())


def strip_directives(line: str) -> str:
    """The line with every directive comment blanked out, length preserved."""
    return DIRECTIVE_RE.sub(lambda m: " " * len(m.group(0)), line)
