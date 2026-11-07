"""The cut-review question: the cutter's cut, read by a model.

The boundary stage is a session per chunk on one file (`cut_file`); this module holds
what every stage's question shares: the key, the handles, and the validation
of an answer's corrections. The groups are a
session per chunk too (`groups_file`).

A question is given owned text only, never hidden text, never an absolute
path. Its key is the hash of the kind, the stage, the template's name
and version, and everything the prompt shows of the blocks it asks about,
with their ids. An answer is validated by applying its corrections,
alone, to the question's cut with the assembler.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Sequence
from dataclasses import replace
from typing import Any

from axiomation.assembler import corrected_cut
from axiomation.cutter.blocks import BlockIndex
from axiomation.cutter.groups import label_holders
from axiomation.cutter.model import (
    Cut,
    GroupKind,
    PartKind,
    Role,
)
from axiomation.questions import (
    Question,
    Stage,
    Template,
    Validation,
)
from axiomation.reader import Document
from axiomation.records import (
    NONE,
    OP_FIELDS,
    CorrectionClass,
    CutCorrection,
    CutOp,
)

KIND = "cut-review"

#: The operations each stage may answer with. The judge reviews the
#: author's boundary edits, so it answers with the boundary operations.
STAGE_OPS: dict[Stage, frozenset[CutOp]] = {
    Stage.BOUNDARIES: frozenset(
        {CutOp.SPLIT, CutOp.MERGE, CutOp.REBOUND, CutOp.PART, CutOp.UNPART}
    ),
    Stage.JUDGE: frozenset({CutOp.SPLIT, CutOp.MERGE}),
}


# --- what a question shows of a block ------------------------------------------


def labels_by_block(document: Document, cut: Cut) -> dict[str, tuple[str, ...]]:
    """The labels each block holds."""
    held: dict[str, list[str]] = {}
    for label, holder in label_holders(document, BlockIndex(cut.blocks)).items():
        if holder is not None:
            held.setdefault(holder, []).append(label)
    return {block: tuple(labels) for block, labels in held.items()}


def role_key(roles: tuple[Role, ...]) -> list[str]:
    """A block's roles as the boundary and groups questions bind them."""
    return [role.value for role in roles]


def _role_words(names: list[str]) -> str:
    return ", ".join(names) or "none"


def question_key(
    stage: Stage, template: Template, inputs: Any, kind: str = KIND
) -> str:
    """The hash of the kind, the stage, the template (with what a review's
    brief asks, where set) and the key inputs."""
    canonical = json.dumps(
        {
            "kind": kind,
            "stage": stage.value,
            "template": {"name": template.name, "version": template.version}
            | ({"asks": dict(template.asks)} if template.asks else {}),
            "inputs": inputs,
        },
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# --- answers ----------------------------------------------------------------------

_FENCED = re.compile(r"```json[ \t]*\n(.*?)```", re.DOTALL)


def extract(text: str) -> Any | None:
    """The answer's JSON: the last fenced `json` block, else the whole text;
    `None` when neither parses."""
    fenced = _FENCED.findall(text)
    candidate = fenced[-1] if fenced else text
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        return None


def check(question: Question, text: str, document: Document) -> Validation:
    """A host's text, extracted and validated."""
    answer = extract(text)
    if answer is None:
        return Validation(problems=("the answer holds no JSON object",))
    return validate(question, answer, document)


def checker(document: Document) -> Callable[[Question, str], Validation]:
    """`check` for one document, as the runner takes it."""
    return lambda question, text: check(question, text, document)


def validate(question: Question, answer: Any, document: Document) -> Validation:
    """The checks, in order: the shape; each correction's op, fields and
    enums; its handles; then the corrections, mapped to ids, applied alone
    to the question's cut with no problem."""
    if not isinstance(answer, dict) or not isinstance(answer.get("corrections"), list):
        return Validation(
            problems=('the answer is not an object {"corrections": [...]}',)
        )
    problems: list[str] = []
    corrections: list[CutCorrection] = []
    for n, item in enumerate(answer["corrections"], 1):
        found, correction = _correction(question, n, item)
        problems.extend(found)
        if correction is not None:
            corrections.append(correction)
    if problems:
        return Validation(problems=tuple(problems))
    assert question.input_cut is not None
    trial = [replace(c, id=str(n)) for n, c in enumerate(corrections, 1)]
    applied = corrected_cut(document, question.input_cut, trial)
    names = {block: handle for handle, block in question.handles.items()}
    for problem in applied.problems:
        problems.append(
            f"correction {problem.correction}: {by_handle(problem.message, names)}"
        )
    if problems:
        return Validation(problems=tuple(problems))
    return Validation(tuple(corrections))


def applied_alone(
    question: Question, corrections: Sequence[CutCorrection], document: Document
) -> Validation:
    """A session's corrections, applied alone to the question's cut: valid
    with them, or the assembler's problems, with the ids the file shows as
    handles, each after its block's handle where it names one."""
    assert question.input_cut is not None
    trial = [replace(c, id=str(n)) for n, c in enumerate(corrections, 1)]
    applied = corrected_cut(document, question.input_cut, trial)
    names = {block: handle for handle, block in question.handles.items()}
    problems = []
    for problem in applied.problems:
        block = corrections[int(problem.correction) - 1].block
        prefix = f"{names.get(block, '?')}: " if block else ""
        problems.append(prefix + by_handle(problem.message, names))
    if problems:
        return Validation(problems=tuple(problems))
    return Validation(tuple(corrections))


def by_handle(message: str, names: dict[str, str]) -> str:
    """An assembler message with the ids the question shows as handles,
    each id replaced only where it stands whole (`main.tex:1:1` is not in
    `main.tex:1:12`)."""
    if not names:
        return message
    pattern = re.compile(
        "|".join(
            rf"(?<![\w:.@/-]){re.escape(block)}(?!\w|:\d)"
            for block in sorted(names, key=len, reverse=True)
        )
    )
    return pattern.sub(lambda m: names[m.group(0)], message)


def _correction(
    question: Question, n: int, item: Any
) -> tuple[list[str], CutCorrection | None]:
    where = f"correction {n}"
    if not isinstance(item, dict):
        return [f"{where}: not an object"], None
    try:
        op = CutOp(item.get("op"))
    except ValueError:
        return [f"{where}: op {item.get('op')!r} is not an operation"], None
    if op not in STAGE_OPS[question.stage]:
        allowed = ", ".join(sorted(o.value for o in STAGE_OPS[question.stage]))
        return [f"{where}: op {op.value} is not one of this stage's: {allowed}"], None
    where = f"{where} ({op.value})"
    takes = OP_FIELDS[op]
    problems = [
        f"{where}: field {name!r} is unknown"
        for name in item
        if name not in {"op", "class", "why", *takes}
    ]
    problems += [
        f"{where}: field {name!r} is missing"
        for name in ("class", "why", *takes)
        if name not in item
    ]
    if problems:
        return problems, None
    try:
        cls = CorrectionClass(item["class"])
    except ValueError:
        problems.append(f"{where}: class {item['class']!r} is not a class")
        cls = CorrectionClass.OTHER
    if not isinstance(item["why"], str) or not item["why"].strip():
        problems.append(f"{where}: why must be a sentence")
    fields: dict[str, Any] = {}
    for name in takes:
        value, problem = _field(question, op, name, item[name])
        if problem:
            problems.append(f"{where}: {problem}")
        else:
            fields[name] = value
    if problems:
        return problems, None
    correction = CutCorrection(
        id="", op=op, cls=cls, why=item["why"].strip(), answer=None, **fields
    )
    return [f"{where}: {problem}" for problem in correction.check()], correction


def _field(question: Question, op: CutOp, name: str, value: Any) -> tuple[Any, str]:
    """A field's value mapped from the answer's form to the correction's,
    or the problem with it."""
    if name == "at":
        items = [value] if isinstance(value, str) else value
        if not isinstance(items, list) or not items:
            return None, "at must be an excerpt or a list of excerpts"
        if not all(isinstance(x, str) and x.strip() for x in items):
            return None, "at must hold non-empty excerpts"
        return tuple(items), ""
    if name == "blocks":
        if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
            return None, "blocks must be a list of handles"
        ids = []
        for handle in value:
            found, problem = _block(question, handle)
            if problem:
                return None, problem
            ids.append(found)
        return tuple(ids), ""
    if not isinstance(value, str):
        return None, f"{name} must be a string"
    if name in ("block", "first", "last"):
        return _block(question, value)
    if name == "names":
        if value == NONE:
            return NONE, ""
        return _block(question, value)
    if name == "group":
        found = question.handles.get(value) if value.startswith("g") else None
        return (found, "") if found else (None, f"group {value!r} is not a handle")
    if name == "kind":
        kinds = GroupKind if op is CutOp.GROUP else PartKind
        try:
            return kinds(value), ""
        except ValueError:
            allowed = ", ".join(k.value for k in kinds)
            return None, f"kind {value!r} is not one of {allowed}"
    return value, ""  # marker


def _block(question: Question, handle: str) -> tuple[Any, str]:
    found = question.handles.get(handle)
    if found is None or not handle.startswith("b"):
        return None, f"block {handle!r} is not a handle"
    return found, ""
