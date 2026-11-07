"""A paper's vocabulary map as a session on one file, and its check.

Code lists every name the reader's tables do not know
(`axiomation.reader.vocabulary.candidates`); the session answers what
each one is for, in the reader's own kinds, and the reader reads the
answer as it reads its tables. The file is `vocabulary.md`: one entry per
candidate, under a heading that names it, with what code found (its
shape, its declaration, its first uses) and two lines the session fills,
`- kind:` and `- evidence:`. Each use is quoted in its own words: an
environment whole, up to `BODY_CHARACTERS`, a command with the text
around it. The session reads nothing else: what a name is for shows in
its declaration and its uses, and the paper's other words would only
cost.

A kind is one of:

- for an environment: `statement` and the standard environment it means,
  or `statement` alone when it means none; `container`; `list` and the
  list it is built on; `none`;
- for a command: `reference`, with `list` where it takes a comma list,
  `prefix=<p>` where it puts `p` before its argument to make the label,
  and `word=<w>` for what it prints; `label`, a command that defines a
  label; `none`.

The evidence is the paper's words the answer rests on, quoted, and must
occur in one of its files. Code refuses what it can refute: a command
found by its uses or its definition is a reference whatever the session
says, and an environment declared over a statement is one.

The second round (`check_question`) shows a cheap model each entry
answered `none`, with its uses in their words, and asks it to confirm or
overturn each; the map is the first answer with the second's overturns.

Both checks need only the shown file, the facts code found and the
paper's texts, so the host runs the first as the session's gate (`main`).

The key is the kind, the stage, the template and the candidates: each
one's name, shape and declaring text, never its uses, so an edit inside
a box asks nothing again and a new name does. A name declared more than
once with different texts is an entry for each, its heading numbered
(`(definition 2 of 2)` after the name), and keyed so. The second round's key is
the entries answered `none` and their evidence.

The map is carried from version to version (`carried`): each answer holds
while its name is a candidate under the declaring text it was answered
under, and only the names it does not answer are asked. A person or an
agent gives the whole map on a kept state (`given_question`, `given_map`):
an entry left as shown keeps its answer and author, an entry changed is
the giver's.
"""

from __future__ import annotations

import dataclasses
import json
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from axiomation.questions import (
    FILE_TOOLS,
    Gate,
    Question,
    Stage,
    Template,
    Validation,
    Workspace,
    gates,
)
from axiomation.questions.cut_review import question_key
from axiomation.reader import (
    Candidate,
    CandidateShape,
    Document,
    MapAnswer,
    Span,
    Vocabulary,
    VocabularyKind,
)
from axiomation.reader.declarations import environment_table
from axiomation.reader.document import COMMAND, ENVIRONMENT
from axiomation.reader.environments import known_environments
from axiomation.reader.formats.md_latex import LIST_ENVS, STATEMENT_ENVS
from axiomation.reader.vocabulary import candidates
from axiomation.records import VocabularyEntry

KIND = "vocabulary"
CHECK_KIND = "vocabulary-check"
SESSION_TEMPLATE = "vocabulary-file"
CHECK_TEMPLATE = "vocabulary-check"
VOCABULARY_FILE = "vocabulary.md"
CHECK_FILE = "vocabulary-check.md"
KICKOFF = "Answer every entry in vocabulary.md, as your instructions say."
CHECK_KICKOFF = "Judge every entry in vocabulary-check.md, as your instructions say."
#: How much of an environment's use a file shows, from its `\begin`.
BODY_CHARACTERS = 800
#: How much of the text around a command's use a file shows, each side.
CONTEXT_CHARACTERS = 300

UNANSWERED = "?"
_HEADING = re.compile(
    r"^## (environment|command) `([^`]+)`(?: \(definition \d+ of \d+\))?$"
)
_SLOT = re.compile(r"^- (kind|evidence|verdict): ?(.*)$")
_ENV_KINDS = {
    VocabularyKind.STATEMENT,
    VocabularyKind.WRAPS,
    VocabularyKind.CONTAINER,
    VocabularyKind.LIST,
    VocabularyKind.NONE,
}
_COMMAND_KINDS = {VocabularyKind.REFERENCE, VocabularyKind.LABEL, VocabularyKind.NONE}
_BEGIN = re.compile(r"\\begin\{([^}]+)\}")


@dataclass(frozen=True, slots=True)
class Entry:
    """One candidate as the file shows it and the checks read it:
    `opens`, the environment the reader knows that its declaration opens,
    if any;
    `empty`, whether every use has an empty body; `labelled`, whether every
    use shown names the paper's labels as they are, no prefix added (a use found
    by a prefix, `\\emph{heads}` beside `lem:heads`, may be a word)."""

    namespace: str
    name: str
    shape: CandidateShape
    declaration: str | None
    opens: str | None = None
    empty: bool = False
    labelled: bool = False
    #: Which of the name's declaring texts this is, in reading order, of
    #: how many (`Candidate.index`, `Candidate.count`).
    index: int = 0
    count: int = 1

    @property
    def heading(self) -> str:
        numbered = (
            f" (definition {self.index + 1} of {self.count})" if self.count > 1 else ""
        )
        return f"## {self.namespace} `{self.name}`{numbered}"


@dataclass(frozen=True, slots=True)
class Answer:
    """One entry answered: its kind as written and its evidence."""

    entry: Entry
    kind: VocabularyKind
    words: tuple[str, ...]
    evidence: str

    @property
    def written(self) -> str:
        return " ".join((self.kind.value, *self.words))

    def mapped(self) -> MapAnswer:
        return MapAnswer(
            self.entry.namespace,
            self.entry.name,
            self.entry.declaration,
            self.written,
            self.evidence,
        )


@dataclass(frozen=True, slots=True)
class Facts:
    """What the checks read of the paper besides the file: its texts, for
    the evidence; the environments the reader knows by name, which a
    `wraps` names; and the statements among them."""

    texts: tuple[str, ...]
    known: frozenset[str]
    statements: frozenset[str]

    def payload(self) -> dict[str, Any]:
        return {
            "texts": list(self.texts),
            "known": sorted(self.known),
            "statements": sorted(self.statements),
        }

    @classmethod
    def of(cls, payload: Mapping[str, Any]) -> Facts:
        return cls(
            tuple(payload["texts"]),
            frozenset(payload["known"]),
            frozenset(payload["statements"]),
        )


def _facts(document: Document) -> Facts:
    table = environment_table(document.declarations)
    return Facts(
        tuple(source.text for source in document.files),
        known_environments(table),
        frozenset(table.names),
    )


# --- the questions ------------------------------------------------------------------


def entries(document: Document, kept: Vocabulary | None = None) -> tuple[Entry, ...]:
    """Every candidate `kept` does not answer, with the facts the checks
    read."""
    return tuple(entry for entry, _ in _found(document, kept))


def _found(
    document: Document, kept: Vocabulary | None = None
) -> list[tuple[Entry, Candidate]]:
    known = _facts(document).known
    found = []
    for candidate in candidates(document, kept):
        environment = candidate.shape is CandidateShape.ENVIRONMENT
        declaration = (
            document.text(candidate.declaration) if candidate.declaration else None
        )
        opens = None
        if environment and declaration is not None:
            opens = next(
                (
                    m.group(1)
                    for m in _BEGIN.finditer(declaration)
                    if m.group(1) in known
                ),
                None,
            )
        entry = Entry(
            ENVIRONMENT if environment else COMMAND,
            candidate.name,
            candidate.shape,
            declaration,
            opens,
            environment and _all_empty(document, candidate.name),
            candidate.shape is CandidateShape.USE and _labelled(document, candidate),
            candidate.index,
            candidate.count,
        )
        found.append((entry, candidate))
    return found


def vocabulary_question(
    document: Document, template: Template, kept: Vocabulary | None = None
) -> Question | None:
    """The session that maps the paper's candidates `kept`, the map carried
    from the version before, does not answer; `None` when none is left."""
    pairs = _found(document, kept)
    if not pairs:
        return None
    shown = [entry for entry, _ in pairs]
    rendered = render(document, pairs)
    key_inputs = [
        {
            "namespace": e.namespace,
            "name": e.name,
            "shape": e.shape.value,
            "declaration": e.declaration,
            **({"definition": [e.index, e.count]} if e.count > 1 else {}),
        }
        for e in sorted(shown, key=lambda e: (e.namespace, e.name, e.index))
    ]
    spec = _spec(document, shown, rendered)
    return Question(
        key=question_key(Stage.VOCABULARY, template, key_inputs, KIND),
        kind=KIND,
        stage=Stage.VOCABULARY,
        template=template,
        prompt=KICKOFF,
        workspace=Workspace(
            files={VOCABULARY_FILE: rendered},
            edit=VOCABULARY_FILE,
            instructions=template.render(),
            reask_from_edit=True,
            tools=FILE_TOOLS,
            brief_alone=True,
            gate=Gate(module=__name__, spec=json.dumps(spec, ensure_ascii=False)),
        ),
    )


def check_question(
    document: Document, answers: Sequence[Answer], template: Template
) -> Question | None:
    """The second round on the first answer's `none` entries (`answers`,
    the first answer's, `answers_of`), or `None` when it has none."""
    unmapped = [a for a in answers if a.kind is VocabularyKind.NONE]
    if not unmapped:
        return None
    uses = {entry.heading: c.uses for entry, c in _found(document)}
    rendered = render_check(document, unmapped, uses)
    key_inputs = [
        {
            "namespace": a.entry.namespace,
            "name": a.entry.name,
            "evidence": a.evidence,
            **(
                {"definition": [a.entry.index, a.entry.count]}
                if a.entry.count > 1
                else {}
            ),
        }
        for a in unmapped
    ]
    spec = {
        "round": CHECK_KIND,
        "reference": rendered,
        "unmapped": [
            {**_entry_payload(a.entry), "evidence": a.evidence} for a in unmapped
        ],
        **_facts(document).payload(),
    }
    return Question(
        key=question_key(Stage.VOCABULARY_CHECK, template, key_inputs, CHECK_KIND),
        kind=CHECK_KIND,
        stage=Stage.VOCABULARY_CHECK,
        template=template,
        prompt=CHECK_KICKOFF,
        workspace=Workspace(
            files={CHECK_FILE: rendered},
            edit=CHECK_FILE,
            instructions=template.render(),
            reask_from_edit=True,
            tools=FILE_TOOLS,
            brief_alone=True,
            gate=Gate(module=__name__, spec=json.dumps(spec, ensure_ascii=False)),
        ),
    )


def given_question(
    document: Document, vocabulary: Vocabulary | None, template: Template
) -> Question | None:
    """The whole map as a person or an agent edits it on a kept state: every
    candidate of `document`, read without a map, each slot filled from
    `vocabulary`'s answer where it answers the name under the same
    declaring text; `None` when the paper has no candidate. Its key is the
    stage's; `person.vocabulary_question` gives it its own."""
    pairs = _found(document)
    if not pairs:
        return None
    shown = [entry for entry, _ in pairs]
    rendered = _filled(render(document, pairs), shown, vocabulary)
    return Question(
        key=question_key(Stage.VOCABULARY, template, [rendered], KIND),
        kind=KIND,
        stage=Stage.VOCABULARY,
        template=template,
        prompt=KICKOFF,
        workspace=Workspace(
            files={VOCABULARY_FILE: rendered},
            edit=VOCABULARY_FILE,
            instructions=template.render(),
            reask_from_edit=True,
            tools=FILE_TOOLS,
            brief_alone=True,
            gate=Gate(
                module=__name__,
                spec=json.dumps(_spec(document, shown, rendered), ensure_ascii=False),
            ),
        ),
    )


def given_map(
    given: Sequence[Answer], vocabulary: Vocabulary | None, by: str
) -> Vocabulary:
    """The map a given answer to `given_question` makes (`given`, its
    entries, `answers_of`): each entry as it was shown keeps the answer it
    showed, its author too; each one it changed is `by`'s."""
    before = {} if vocabulary is None else {a.place: a for a in vocabulary.answers}
    answers = []
    for answer in given:
        mapped = answer.mapped()
        shown = before.get(mapped.place)
        same = shown is not None and _said(shown) == _said(mapped)
        answers.append(shown if same else dataclasses.replace(mapped, by=by))
    return Vocabulary.of(answers)


def _said(answer: MapAnswer) -> tuple[str | None, str, str]:
    return (answer.declaration, answer.kind, answer.evidence)


def changed_entries(
    before: Vocabulary | None, after: Vocabulary
) -> tuple[MapAnswer, ...]:
    """The answers of `after` whose kind differs from `before`'s for the
    same name, or that `before` does not hold."""
    shown = {} if before is None else {a.place: a for a in before.answers}
    return tuple(
        a
        for a in after.answers
        if a.place not in shown or shown[a.place].kind != a.kind
    )


def _filled(
    rendered: str, shown: Sequence[Entry], vocabulary: Vocabulary | None
) -> str:
    """`rendered` with each entry's slots as `vocabulary` answers it."""
    if vocabulary is None:
        return rendered
    slots = {}
    for entry in shown:
        found = vocabulary.answer(entry.namespace, entry.name, entry.declaration)
        if found is not None:
            slots[entry.heading] = (found.kind, f'"{found.evidence}"')
    lines, heading = [], None
    for line in rendered.split("\n"):
        if _HEADING.match(line):
            heading = line
        if heading in slots and line == f"- kind: {UNANSWERED}":
            line = f"- kind: {slots[heading][0]}"
        elif heading in slots and line == f"- evidence: {UNANSWERED}":
            line = f"- evidence: {slots[heading][1]}"
        lines.append(line)
    return "\n".join(lines)


# --- the files ----------------------------------------------------------------------


def render(document: Document, found: Sequence[tuple[Entry, Candidate]]) -> str:
    """`vocabulary.md`: each entry, what code found, and its two slots."""
    parts = []
    for entry, candidate in found:
        lines = [entry.heading, f"- found: {entry.shape.value}"]
        if candidate.declaration is not None:
            lines.append(
                f"- declared: `{_one_line(document.text(candidate.declaration))}` "
                f"({_where(document, candidate.declaration)})"
            )
        lines += _uses(document, entry, candidate.uses)
        lines += [f"- kind: {UNANSWERED}", f"- evidence: {UNANSWERED}"]
        parts.append("\n".join(lines))
    return "\n\n".join(parts) + "\n"


def render_check(
    document: Document,
    unmapped: Sequence[Answer],
    uses: Mapping[str, tuple[Span, ...]],
) -> str:
    """`vocabulary-check.md`: each entry answered `none`, the answer's
    evidence, its uses in their words, and the verdict's slots."""
    parts = []
    for answer in unmapped:
        entry = answer.entry
        lines = [entry.heading, f"- found: {entry.shape.value}"]
        if entry.declaration is not None:
            lines.append(f"- declared: `{_one_line(entry.declaration)}`")
        lines.append(f'- answered: none, "{answer.evidence}"')
        lines += _uses(document, entry, uses.get(entry.heading, ()))
        lines += [f"- verdict: {UNANSWERED}", f"- evidence: {UNANSWERED}"]
        parts.append("\n".join(lines))
    return "\n\n".join(parts) + "\n"


def _uses(document: Document, entry: Entry, uses: Sequence[Span]) -> list[str]:
    """Each use, where it stands and in its own words."""
    lines = []
    for use in uses:
        lines.append(f"- used ({_where(document, use)}):")
        lines.append(_fenced(_shown(document, use, entry.namespace == ENVIRONMENT)))
    return lines


def _shown(document: Document, use: Span, environment: bool) -> str:
    """An environment's use whole, cut at `BODY_CHARACTERS`; a command's use
    with `CONTEXT_CHARACTERS` either side."""
    if environment:
        whole = next(
            (
                env.span
                for env in document.environments
                if env.span.file == use.file and env.span.start == use.start
            ),
            use,
        )
        text = document.text(whole)
        if len(text) > BODY_CHARACTERS:
            return text[:BODY_CHARACTERS] + " [...]"
        return text
    return _around(document, use)


def _spec(document: Document, shown: Sequence[Entry], rendered: str) -> dict[str, Any]:
    return {
        "reference": rendered,
        "entries": [_entry_payload(e) for e in shown],
        **_facts(document).payload(),
    }


def _entry_payload(entry: Entry) -> dict[str, Any]:
    return {
        "namespace": entry.namespace,
        "name": entry.name,
        "shape": entry.shape.value,
        "declaration": entry.declaration,
        "opens": entry.opens,
        "empty": entry.empty,
        "labelled": entry.labelled,
        **({"definition": [entry.index, entry.count]} if entry.count > 1 else {}),
    }


def _entry_of(payload: Mapping[str, Any]) -> Entry:
    return Entry(
        payload["namespace"],
        payload["name"],
        CandidateShape(payload["shape"]),
        payload["declaration"],
        payload["opens"],
        payload["empty"],
        payload["labelled"],
        *payload.get("definition", (0, 1)),
    )


# --- reading an answer --------------------------------------------------------------


class AnswerError(ValueError):
    """An edited file that does not read: its problems, one a line."""


def read_vocabulary(
    reference: str,
    edited: str,
    shown: Sequence[Entry],
    facts: Facts,
) -> tuple[Answer, ...]:
    """The answers an edited `vocabulary.md` gives, each checked; raises
    `AnswerError` with every problem."""
    problems: list[str] = []
    answers = []
    filled = _slots(reference, edited, ("kind", "evidence"), problems)
    for entry in shown:
        slots = filled.get(entry.heading)
        if slots is None:
            continue
        answer = _answer(entry, slots["kind"], slots["evidence"], facts, problems)
        if answer is not None:
            answers.append(answer)
    if problems:
        raise AnswerError("\n".join(problems))
    return tuple(answers)


def read_verdicts(
    reference: str,
    edited: str,
    unmapped: Sequence[Answer],
    facts: Facts,
) -> tuple[Answer, ...]:
    """The second round's overturns, each a new answer; raises
    `AnswerError` with every problem."""
    problems: list[str] = []
    overturned = []
    filled = _slots(reference, edited, ("verdict", "evidence"), problems)
    for answer in unmapped:
        slots = filled.get(answer.entry.heading)
        if slots is None:
            continue
        verdict = slots["verdict"].split(maxsplit=1)
        if verdict == ["confirm"]:
            continue
        if not verdict or verdict[0] != "overturn" or len(verdict) < 2:
            problems.append(
                f"{answer.entry.heading}: the verdict is `confirm` or "
                f"`overturn <kind>`, not `{slots['verdict']}`"
            )
            continue
        new = _answer(answer.entry, verdict[1], slots["evidence"], facts, problems)
        if new is not None and new.kind is not VocabularyKind.NONE:
            overturned.append(new)
    if problems:
        raise AnswerError("\n".join(problems))
    return tuple(overturned)


def _slots(
    reference: str, edited: str, names: tuple[str, str], problems: list[str]
) -> dict[str, dict[str, str]]:
    """Each entry's filled slots, by heading; every other line as shown."""
    shown = _by_heading(reference)
    found = _by_heading(edited)
    for heading in (h for h in shown if h not in found):
        problems.append(f"{heading} is missing: answer every entry, keep its heading")
    for heading in (h for h in found if h not in shown):
        problems.append(f"{heading} names no entry: add none, rename none")
    filled: dict[str, dict[str, str]] = {}
    for heading, lines in found.items():
        if heading not in shown:
            continue
        fixed = [line for line in shown[heading] if not _is_slot(line, names)]
        kept = [line for line in lines if not _is_slot(line, names)]
        if [line.rstrip() for line in kept] != [line.rstrip() for line in fixed]:
            problems.append(
                f"{heading}: change only its `- {names[0]}:` and `- {names[1]}:` lines"
            )
            continue
        slots: dict[str, str] = {}
        for line in lines:
            match = _SLOT.match(line)
            if match is not None and match.group(1) in names:
                if match.group(1) in slots:
                    problems.append(f"{heading}: one `- {match.group(1)}:` line")
                slots[match.group(1)] = match.group(2).strip()
        unanswered = [
            name for name in names if slots.get(name, UNANSWERED) == UNANSWERED
        ]
        if unanswered and not (
            names[0] == "verdict" and slots.get("verdict") == "confirm"
        ):
            problems.append(f"{heading}: answer its `- {unanswered[0]}:` line")
            continue
        filled[heading] = slots
    return filled


def _answer(
    entry: Entry,
    written: str,
    evidence: str,
    facts: Facts,
    problems: list[str],
) -> Answer | None:
    where = entry.heading
    words = tuple(written.split())
    try:
        kind = VocabularyKind(words[0]) if words else None
    except ValueError:
        kind = None
    allowed = _ENV_KINDS if entry.namespace == ENVIRONMENT else _COMMAND_KINDS
    if kind is None or kind not in allowed:
        names = ", ".join(sorted(k.value for k in allowed))
        problems.append(f"{where}: the kind is one of {names}, not `{written}`")
        return None
    rest = words[1:]
    problem = _kind_problem(entry, kind, rest, facts)
    if problem is not None:
        problems.append(f"{where}: {problem}")
        return None
    quote = evidence.strip()
    if len(quote) >= 2 and quote[0] == quote[-1] == '"':
        quote = quote[1:-1]
    if not quote or not any(quote in text for text in facts.texts):
        problems.append(
            f"{where}: the evidence is the paper's own words, quoted as they stand "
            f"in one of its files; `{evidence}` is not"
        )
        return None
    return Answer(entry, kind, rest, quote)


def _kind_problem(
    entry: Entry, kind: VocabularyKind, rest: tuple[str, ...], facts: Facts
) -> str | None:
    """What code refutes in a kind, or `None`."""
    if kind is VocabularyKind.STATEMENT:
        if len(rest) > 1 or (rest and rest[0] not in STATEMENT_ENVS):
            return (
                "a statement names at most one standard environment it means, one of "
                + ", ".join(STATEMENT_ENVS)
            )
        if entry.empty:
            return "every use of it is empty: it bounds no statement"
    elif kind is VocabularyKind.LIST:
        if len(rest) > 1 or (rest and rest[0] not in LIST_ENVS):
            return "a list names at most the list it is built on, one of " + ", ".join(
                LIST_ENVS
            )
    elif kind is VocabularyKind.WRAPS:
        if len(rest) != 1 or rest[0] not in facts.known:
            return (
                "`wraps` names the one environment it wraps, one the reader knows: "
                + ", ".join(sorted(facts.known))
            )
    elif kind is VocabularyKind.REFERENCE:
        for word in rest:
            if word != "list" and not word.startswith(("prefix=", "word=")):
                return (
                    "a reference takes `list`, `prefix=<p>` and `word=<w>`, "
                    f"not `{word}`"
                )
    elif rest:
        return f"`{kind.value}` takes nothing after it"
    if entry.opens is not None:
        wraps_it = kind is VocabularyKind.WRAPS and rest == (entry.opens,)
        states = kind is VocabularyKind.STATEMENT and entry.opens in facts.statements
        if not (wraps_it or states):
            return f"its declaration opens `{entry.opens}`: it wraps it"
    if entry.labelled and kind is not VocabularyKind.REFERENCE:
        return "every use of it names one of the paper's labels: it is a reference"
    if entry.shape is CandidateShape.MACRO and kind is not VocabularyKind.REFERENCE:
        return "its definition uses a reference command: it is a reference"
    return None


def _by_heading(text: str) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    current: list[str] | None = None
    for line in text.split("\n"):
        if _HEADING.match(line.rstrip()):
            current = found.setdefault(line.rstrip(), [])
            continue
        if current is not None and line.strip():
            current.append(line)
    return found


def _is_slot(line: str, names: tuple[str, str]) -> bool:
    match = _SLOT.match(line)
    return match is not None and match.group(1) in names


# --- the map ------------------------------------------------------------------------


def carried(document: Document, kept: Vocabulary | None) -> tuple[MapAnswer, ...]:
    """The answers of `kept` that still hold on `document`, read without a
    map: each one's name is still a candidate, under the declaring text it
    was answered under. A name gone drops its answer; a changed
    declaration, or one added or removed beside it, is asked again for the
    texts the map does not answer."""
    if kept is None or not kept.answers:
        return ()
    standing = {
        (entry.namespace, entry.name, entry.declaration) for entry in entries(document)
    }
    return tuple(a for a in kept.answers if a.place in standing)


def as_vocabulary(answers: Sequence[Answer]) -> Vocabulary:
    """The map the answers give (`Vocabulary.of`)."""
    return Vocabulary.of([answer.mapped() for answer in answers])


def mapped(
    first: Sequence[Answer],
    verdicts: Sequence[Answer] = (),
    carried: Sequence[MapAnswer] = (),
) -> Vocabulary:
    """The map: the answers `carried` from the version before, and the
    first answer's entries with the second round's overturns (`verdicts`;
    each `answers_of`)."""
    overturns = {a.entry.heading: a for a in verdicts}
    answers = [overturns.get(a.entry.heading, a) for a in first]
    return Vocabulary.of([*carried, *(a.mapped() for a in answers)])


def entries_of(answers: Sequence[Answer]) -> tuple[VocabularyEntry, ...]:
    """An answer's entries as the store records them, their ids and answer
    left for the resolver."""
    return tuple(
        VocabularyEntry(
            id="",
            answer=None,
            namespace=a.entry.namespace,
            name=a.entry.name,
            shape=a.entry.shape.value,
            declaration=a.entry.declaration,
            opens=a.entry.opens,
            empty=a.entry.empty,
            labelled=a.entry.labelled,
            index=a.entry.index,
            count=a.entry.count,
            kind=a.kind.value,
            words=a.words,
            evidence=a.evidence,
        )
        for a in answers
    )


def answers_of(entries: Sequence[VocabularyEntry]) -> tuple[Answer, ...]:
    """An answer's entries as its check read them, from their records."""
    return tuple(
        Answer(
            Entry(
                e.namespace,
                e.name,
                CandidateShape(e.shape),
                e.declaration,
                e.opens,
                e.empty,
                e.labelled,
                e.index,
                e.count,
            ),
            VocabularyKind(e.kind),
            e.words,
            e.evidence,
        )
        for e in entries
    )


def read_answer(question: Question, raw: str) -> tuple[Answer, ...]:
    return _answers_of(json.loads(_gate(question).spec), raw)


def _verdicts_of(spec: Mapping[str, Any], edited: str) -> tuple[Answer, ...]:
    unmapped = [
        Answer(_entry_of(e), VocabularyKind.NONE, (), e["evidence"])
        for e in spec["unmapped"]
    ]
    return read_verdicts(spec["reference"], edited, unmapped, Facts.of(spec))


def _answers_of(spec: Mapping[str, Any], edited: str) -> tuple[Answer, ...]:
    if spec.get("round") == CHECK_KIND:
        return _verdicts_of(spec, edited)
    return read_vocabulary(
        spec["reference"],
        edited,
        [_entry_of(e) for e in spec["entries"]],
        Facts.of(spec),
    )


def _gate(question: Question) -> Gate:
    assert question.workspace is not None and question.workspace.gate is not None
    return question.workspace.gate


# --- the checks ---------------------------------------------------------------------


def check(question: Question, text: str, document: Document) -> Validation:
    """An edited `vocabulary.md` or `vocabulary-check.md`, read and
    checked; an overturn is held to the first round's checks."""
    try:
        answers = read_answer(question, text)
    except AnswerError as error:
        return Validation(problems=tuple(str(error).split("\n")))
    return Validation(vocabulary=entries_of(answers))


# --- the gate -----------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    """The session's gate, as Claude Code's Stop hook: nothing when the
    edited file reads, else Claude Code's `block` decision with its
    problems, at most `gates.ROUNDS` times. It never fails the session: a
    gate that cannot run lets it end, and the runner's check decides."""
    gates.hook_input()
    try:
        spec_path, edited_path = map(Path, argv if argv is not None else sys.argv[1:3])
        _answers_of(gates.spec(spec_path), gates.read_regular(edited_path))
    except AnswerError as error:
        problems = str(error).split("\n")
    except Exception as error:  # noqa: BLE001 - the gate must never trap a session
        print(f"the vocabulary gate could not run: {error}", file=sys.stderr)
        return 0
    else:
        return 0
    if gates.round_left(spec_path):
        gates.block(gates.reason(edited_path.name, problems))
    return 0


# --- helpers ------------------------------------------------------------------------


def _labelled(document: Document, candidate: Candidate) -> bool:
    """Whether every use shown takes a comma list of the paper's labels."""
    labels = {label.name for label in document.labels}
    for use in candidate.uses:
        text = document.text(use)
        argument = text[text.rfind("{") + 1 : -1]
        items = [item.strip() for item in argument.split(",")]
        if not items or not all(item in labels for item in items):
            return False
    return bool(candidate.uses)


def _all_empty(document: Document, name: str) -> bool:
    bodies = [
        document.text(env.body).strip()
        for env in document.environments
        if env.name == name
    ]
    return bool(bodies) and not any(bodies)


def _where(document: Document, span: Span) -> str:
    text = document.file(span.file).text
    return f"{span.file} line {text.count(chr(10), 0, span.start) + 1}"


def _one_line(text: str) -> str:
    return " ".join(text.split()).replace("`", "'")


def _around(document: Document, span: Span) -> str:
    text = document.file(span.file).text
    start = max(0, span.start - CONTEXT_CHARACTERS)
    end = min(len(text), span.end + CONTEXT_CHARACTERS)
    return text[start:end]


def _fenced(text: str) -> str:
    longest = max((len(m) for m in re.findall(r"`+", text)), default=0)
    ticks = "`" * max(3, longest + 1)
    return f"{ticks}\n{text}\n{ticks}"


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "CHECK_KIND",
    "KIND",
    "Answer",
    "AnswerError",
    "Entry",
    "as_vocabulary",
    "check",
    "check_question",
    "entries",
    "mapped",
    "read_verdicts",
    "read_vocabulary",
    "render",
    "vocabulary_question",
]
