"""Two boundaries, read from the source so they hold without the extra
(`make test-core`).

The cited works (`axiomation.sources`) are the core's, behind the
evidence contract: the rest of the core reaches them only at the named
sites, each inside a function, so a command that reads no cited work
loads no PDF library. The evidence contract itself is the standard
library only.

The `library` extra (`axiomation.sources.library`: Qdrant, Voyage,
fastembed, pytesseract) is reached from the rest of the package only at
its own sites, and no module outside it imports a library the extra
brings but Tesseract, at the OCR client's two (`EXTRA_SITES`).
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import axiomation
from axiomation.sources.config import LIBRARY_MODULES

PACKAGE = Path(axiomation.__file__).parent
SOURCES = PACKAGE / "sources"
LIBRARY = SOURCES / "library"
#: The rest of the core's only ways into the cited works: (module, function).
SITES = {
    ("evidence.py", "sources_for"),
    ("cli.py", "_run_sources"),
    ("commands/review.py", "_citation_queue"),
    ("commands/review.py", "_fieldresults_queue"),
    ("commands/review.py", "_fieldresults_queue_priced"),
    ("commands/review.py", "_inventory_results"),
    ("review_tools.py", "_cited_reads"),
}
#: The libraries the extra brings.
EXTRA = LIBRARY_MODULES
#: Where a module outside the extra's package imports one of its libraries:
#: Tesseract, the full tier's OCR, read beside the OCR services' client.
EXTRA_SITES = {
    ("sources/ingest/ocr.py", "read"),
    ("sources/ingest/ocr.py", "words"),
}
#: The libraries only the cited works load: the extra's and the PDF's.
HEAVY = (
    *EXTRA,
    "pypdfium2",
    "pypdfium2_raw",
    "pypdf",
    "fontTools",
    "PIL",
    "bibtexparser",
    "mcp",
)
#: The rest of the package's only ways into the extra: (module, function),
#: None for a module that imports it whole. The ingest writes its corpus
#: to Qdrant, so it needs the extra whole.
LIBRARY_SITES = {
    ("sources/cli.py", "_run_ingest"),
    ("sources/cli.py", "_report_lines"),
    ("sources/cli.py", "_run_adopt"),
    ("sources/providers.py", "__init__"),
    ("sources/ingest/run.py", None),
    ("sources/read/status.py", "collect"),
}


def _imports(path: Path) -> list[tuple[str, int, str | None]]:
    """Each import in a module that runs: the module it names, its line,
    and the function it sits in (None at module level). One under `if
    TYPE_CHECKING:` names a type and loads nothing."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[tuple[str, int, str | None]] = []

    def visit(node: ast.AST, function: str | None) -> None:
        for child in ast.iter_child_nodes(node):
            if (
                isinstance(child, ast.If)
                and isinstance(child.test, ast.Name)
                and child.test.id == "TYPE_CHECKING"
            ):
                # its `else:` runs
                for branch in child.orelse:
                    visit(ast.Module(body=[branch], type_ignores=[]), function)
                continue
            inside = function
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                inside = child.name
            if isinstance(child, ast.Import):
                found.extend((a.name, child.lineno, inside) for a in child.names)
            elif (
                isinstance(child, ast.ImportFrom) and child.level == 0 and child.module
            ):
                found.append((child.module, child.lineno, inside))
            visit(child, inside)

    visit(tree, None)
    return found


def _core() -> list[Path]:
    return [
        p
        for p in sorted(PACKAGE.rglob("*.py"))
        if SOURCES not in p.parents and "__pycache__" not in p.parts
    ]


def _is(name: str, root: str) -> bool:
    return name == root or name.startswith(root + ".")


def test_the_core_reaches_the_cited_works_only_at_its_sites() -> None:
    wrong = []
    for path in _core():
        shown = str(path.relative_to(PACKAGE))
        for name, line, function in _imports(path):
            if _is(name, "axiomation.sources") and (shown, function) not in SITES:
                wrong.append(f"{shown}:{line}: {name} (in {function or 'the module'})")
            if any(_is(name, heavy) for heavy in HEAVY):
                wrong.append(f"{shown}:{line}: {name}, a library of the cited works")
    said = "the core reaches the cited works outside its sites:\n"
    assert not wrong, said + "\n".join(wrong)


def test_the_package_reaches_the_library_extra_only_at_its_sites() -> None:
    wrong = []
    for path in sorted(SOURCES.rglob("*.py")):
        if LIBRARY in path.parents or path.parent == LIBRARY:
            continue
        shown = str(path.relative_to(PACKAGE))
        for name, line, function in _imports(path):
            if _is(name, "axiomation.sources.library") and (
                (shown, function) not in LIBRARY_SITES
                and (shown, None) not in LIBRARY_SITES
            ):
                wrong.append(f"{shown}:{line}: {name} (in {function or 'the module'})")
            if (
                any(_is(name, extra) for extra in EXTRA)
                and (shown, function) not in EXTRA_SITES
            ):
                wrong.append(f"{shown}:{line}: {name}, a library of the extra")
    assert not wrong, "the package imports the library extra:\n" + "\n".join(wrong)


def test_the_evidence_contract_is_the_standard_library() -> None:
    outside = [
        f"{name}:{line}"
        for name, line, function in _imports(PACKAGE / "evidence.py")
        if function is None
        and name.split(".")[0] not in sys.stdlib_module_names
        and name != "__future__"
    ]
    assert not outside


def test_a_planted_import_is_caught(tmp_path) -> None:
    planted = tmp_path / "planted.py"
    planted.write_text(
        "from axiomation.sources.library.client import SourceReader\n"
        "def f():\n    import qdrant_client\n",
        encoding="utf-8",
    )
    assert [(n, f) for n, _, f in _imports(planted)] == [
        ("axiomation.sources.library.client", None),
        ("qdrant_client", "f"),
    ]
