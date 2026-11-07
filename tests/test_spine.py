"""The spine imports nothing that talks to a model,
and the direction of dependence holds.

Every module under `src/axiomation/reader/` and `src/axiomation/cutter/`
imports only the standard library, `axiomation.reader` and
`axiomation.cutter`; `axiomation.records` and `axiomation.assembler` only
the standard library and the spine before them. The day one imports a
runner, or any third-party package, this fails. Beyond the spine, the direction
spine → records ← resolver ← runner ← driver, is a table
of what each part may not import.
"""

from __future__ import annotations

import ast
import shutil
import sys
from pathlib import Path

import pytest

import axiomation.assembler
import axiomation.changes
import axiomation.closure
import axiomation.cutter
import axiomation.driver
import axiomation.identity
import axiomation.keys
import axiomation.questions
import axiomation.reader
import axiomation.records
import axiomation.resolver
import axiomation.runner

#: What each part of the spine may import besides the standard library:
#: the reader only itself (it knows nothing about blocks), the
#: cutter itself and the reader; the records the cutter's model (their
#: kinds and roles), the evidence contract (a cited page's stamp;
#: itself only the standard library) and the derived values (how far a
#: record file was checked; itself only the standard library) and the kept
#: changes (a value kept as its change of an earlier one; itself only the
#: standard library) and a run's working state (an identity answer's
#: payload held until its step is kept; itself only the standard
#: library and develop mode's texts, which are as plain) and the keys
#: (the record is sealed at rest; itself only the
#: standard library and `cryptography`); the assembler the reader, the
#: cutter, the records and itself.
ALLOWED: dict[str, tuple[str, ...]] = {
    "axiomation.reader": ("axiomation.reader",),
    "axiomation.cutter": ("axiomation.reader", "axiomation.cutter"),
    "axiomation.records": (
        "axiomation.records",
        "axiomation.cutter.model",
        "axiomation.derived",
        "axiomation.evidence",
        "axiomation.keys",
        "axiomation.kept_change",
        "axiomation.run_state",
    ),
    "axiomation.assembler": (
        "axiomation.reader",
        "axiomation.cutter",
        "axiomation.records",
        "axiomation.assembler",
    ),
}
READER = Path(axiomation.reader.__file__).parent
CUTTER = Path(axiomation.cutter.__file__).parent
RECORDS = Path(axiomation.records.__file__)
KEYS = Path(axiomation.keys.__file__)
ASSEMBLER = Path(axiomation.assembler.__file__)


def outside_imports(target: Path, package: str = "axiomation.reader") -> list[str]:
    """Every import in a package's modules, or in one module's file, from
    outside the standard library and what `package` may import, as
    `file:line: module`."""
    found: list[str] = []
    paths = [target] if target.is_file() else sorted(target.rglob("*.py"))
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        shown = path.name if target.is_file() else path.relative_to(target)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [_absolute(node, path, target, package)]
            else:
                continue
            for name in names:
                if not _allowed(name, ALLOWED[package]):
                    found.append(f"{shown}:{node.lineno}: {name}")
    return found


def _absolute(node: ast.ImportFrom, path: Path, target: Path, name: str) -> str:
    """The module a `from` import names, a relative one resolved against the
    package it sits in, as if the scanned directory were the package `name`
    (or the scanned file the module `name`)."""
    if not node.level:
        return node.module or ""
    if target.is_file():
        package = name.split(".")[:-1]
    else:
        package = [*name.split("."), *path.parent.relative_to(target).parts]
    base = package[: len(package) - (node.level - 1)]
    return ".".join(base + ([node.module] if node.module else []))


def _allowed(module: str, allowed: tuple[str, ...]) -> bool:
    if module.split(".")[0] in sys.stdlib_module_names:
        return True
    return any(module == spine or module.startswith(spine + ".") for spine in allowed)


@pytest.mark.parametrize(
    ("target", "package"),
    [
        (READER, "axiomation.reader"),
        (CUTTER, "axiomation.cutter"),
        (RECORDS, "axiomation.records"),
        (ASSEMBLER, "axiomation.assembler"),
    ],
    ids=["reader", "cutter", "records", "assembler"],
)
def test_the_spine_imports_only_the_standard_library_and_itself(
    target: Path, package: str
) -> None:
    assert outside_imports(target, package) == []


def test_a_planted_import_is_caught(tmp_path: Path) -> None:
    copy = tmp_path / "reader"
    shutil.copytree(READER, copy, ignore=shutil.ignore_patterns("__pycache__"))
    layer = copy / "layer.py"
    layer.write_text(
        layer.read_text(encoding="utf-8") + "\nimport axiomation.runner\n",
        encoding="utf-8",
    )
    (copy / "formats" / "latex.py").write_text(
        "from requests import get\nfrom ...runner import ask\nfrom . import md_latex\n",
        encoding="utf-8",
    )

    assert outside_imports(copy) == [
        "formats/latex.py:1: requests",
        "formats/latex.py:2: axiomation.runner",
        f"layer.py:{len(layer.read_text().splitlines())}: axiomation.runner",
    ]


def test_a_planted_import_in_the_cutter_is_caught(tmp_path: Path) -> None:
    copy = tmp_path / "cutter"
    shutil.copytree(CUTTER, copy, ignore=shutil.ignore_patterns("__pycache__"))
    groups = copy / "groups.py"
    groups.write_text(
        groups.read_text(encoding="utf-8") + "\nfrom ..runner import ask\n",
        encoding="utf-8",
    )
    (copy / "blocks.py").write_text(
        "import httpx\nfrom . import model\nfrom axiomation.reader import Span\n",
        encoding="utf-8",
    )

    assert outside_imports(copy, "axiomation.cutter") == [
        "blocks.py:1: httpx",
        f"groups.py:{len(groups.read_text().splitlines())}: axiomation.runner",
    ]


def test_the_reader_may_not_import_the_cutter(tmp_path: Path) -> None:
    copy = tmp_path / "reader"
    shutil.copytree(READER, copy, ignore=shutil.ignore_patterns("__pycache__"))
    (copy / "layer.py").write_text(
        "from axiomation.cutter import cut\n", encoding="utf-8"
    )

    assert outside_imports(copy) == ["layer.py:1: axiomation.cutter"]


def test_a_planted_import_in_the_assembler_is_caught(tmp_path: Path) -> None:
    copy = tmp_path / "assembler.py"
    source = ASSEMBLER.read_text(encoding="utf-8")
    copy.write_text(
        source + "\nimport axiomation.runner\nfrom .runner import ask\n",
        encoding="utf-8",
    )

    lines = len(source.splitlines())
    assert outside_imports(copy, "axiomation.assembler") == [
        f"assembler.py:{lines + 2}: axiomation.runner",
        f"assembler.py:{lines + 3}: axiomation.runner",
    ]


def test_the_keys_import_only_the_standard_library_and_cryptography() -> None:
    tree = ast.parse(KEYS.read_text(encoding="utf-8"))
    imported = {
        name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import | ast.ImportFrom)
        for name in (
            [a.name for a in node.names]
            if isinstance(node, ast.Import)
            else [node.module or ""]
        )
    }
    assert imported - set(sys.stdlib_module_names) == {"cryptography"}


def test_a_planted_import_in_the_records_is_caught(tmp_path: Path) -> None:
    copy = tmp_path / "records.py"
    source = RECORDS.read_text(encoding="utf-8")
    copy.write_text(
        source + "\nimport httpx\nfrom .cutter.blocks import BlockIndex\n",
        encoding="utf-8",
    )

    lines = len(source.splitlines())
    assert outside_imports(copy, "axiomation.records") == [
        f"records.py:{lines + 2}: httpx",
        f"records.py:{lines + 3}: axiomation.cutter.blocks",
    ]


# --- the direction of dependence ---------------------------------------

QUESTIONS = "axiomation.questions"
RUNNER = "axiomation.runner"
RESOLVER = "axiomation.resolver"
DRIVER = "axiomation.driver"
ABOVE_THE_SPINE = (QUESTIONS, RUNNER, RESOLVER, DRIVER)

#: What each part may not import: the spine and the records nothing that
#: asks or writes, nor identity, the closure and a graph edit's classes,
#: which sit before every question; the questions no runner,
#: resolver or driver; the resolver no runner or driver; the runner no
#: driver.
FORBIDDEN: dict[str, tuple[str, ...]] = {
    "axiomation.reader": ABOVE_THE_SPINE,
    "axiomation.cutter": ABOVE_THE_SPINE,
    "axiomation.records": ABOVE_THE_SPINE,
    "axiomation.assembler": ABOVE_THE_SPINE,
    "axiomation.identity": ABOVE_THE_SPINE,
    "axiomation.closure": ABOVE_THE_SPINE,
    "axiomation.changes": ABOVE_THE_SPINE,
    QUESTIONS: (RUNNER, RESOLVER, DRIVER),
    RESOLVER: (RUNNER, DRIVER),
    RUNNER: (DRIVER,),
}
PARTS: dict[str, Path] = {
    "axiomation.reader": READER,
    "axiomation.cutter": CUTTER,
    "axiomation.records": RECORDS,
    "axiomation.assembler": ASSEMBLER,
    "axiomation.identity": Path(axiomation.identity.__file__),
    "axiomation.closure": Path(axiomation.closure.__file__),
    "axiomation.changes": Path(axiomation.changes.__file__),
    QUESTIONS: Path(axiomation.questions.__file__).parent,
    RESOLVER: Path(axiomation.resolver.__file__),
    RUNNER: Path(axiomation.runner.__file__).parent,
    DRIVER: Path(axiomation.driver.__file__),
}


def forbidden_imports(target: Path, package: str) -> list[str]:
    """Every import in a part that the table forbids it, as
    `file:line: module`."""
    found: list[str] = []
    paths = [target] if target.is_file() else sorted(target.rglob("*.py"))
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        shown = path.name if target.is_file() else path.relative_to(target)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [_absolute(node, path, target, package)]
            else:
                continue
            for name in names:
                if any(
                    name == bad or name.startswith(bad + ".")
                    for bad in FORBIDDEN[package]
                ):
                    found.append(f"{shown}:{node.lineno}: {name}")
    return found


@pytest.mark.parametrize("package", list(FORBIDDEN))
def test_the_direction_of_dependence_holds(package: str) -> None:
    assert forbidden_imports(PARTS[package], package) == []


def test_the_driver_is_free_to_import_them_all() -> None:
    assert DRIVER not in FORBIDDEN


def test_a_planted_backward_import_is_caught(tmp_path: Path) -> None:
    resolver = tmp_path / "resolver.py"
    source = PARTS[RESOLVER].read_text(encoding="utf-8")
    resolver.write_text(
        source + "\nfrom axiomation.runner import Runner\nimport axiomation.driver\n",
        encoding="utf-8",
    )
    runner = tmp_path / "runner"
    shutil.copytree(PARTS[RUNNER], runner, ignore=shutil.ignore_patterns("__pycache__"))
    (runner / "hosts.py").write_text("from ..driver import review_cut\n")
    questions = tmp_path / "questions"
    shutil.copytree(
        PARTS[QUESTIONS], questions, ignore=shutil.ignore_patterns("__pycache__")
    )
    (questions / "cut_review.py").write_text(
        "from axiomation.resolver import Attempt\n"
    )

    lines = len(source.splitlines())
    assert forbidden_imports(resolver, RESOLVER) == [
        f"resolver.py:{lines + 2}: axiomation.runner",
        f"resolver.py:{lines + 3}: axiomation.driver",
    ]
    assert forbidden_imports(runner, RUNNER) == ["hosts.py:1: axiomation.driver"]
    assert forbidden_imports(questions, QUESTIONS) == [
        "cut_review.py:1: axiomation.resolver"
    ]


@pytest.mark.parametrize(
    "gate", ["axiomation.questions.review_file", "axiomation.questions.edges_file"]
)
def test_a_gate_loads_no_runner_resolver_or_driver(gate: str) -> None:
    """A gate runs in a fresh process after each write: what it imports,
    directly or through another module, holds none of them."""
    import subprocess

    loaded = subprocess.run(
        [
            sys.executable,
            "-c",
            f"import sys, {gate}; print(' '.join(sorted(sys.modules)))",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    banned = ("axiomation.runner", "axiomation.resolver", "axiomation.driver")
    assert [m for m in loaded if m.startswith(banned)] == []
