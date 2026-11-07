"""Import hygiene: the two halves, and the startup budget.

The core's boundary with this package is `tests/test_boundary.py`'s (it runs
without the `library` extra). What stays here, each with a reason a comment would not
keep true:

- `sources.read` never imports `sources.ingest`. Agents reach the read half;
  nothing they can call may have a write function one attribute away.
- The contract and the settings import the standard library only, and a
  graph command never loads the vector stack: Qdrant, Voyage and the PDF
  library (`pypdfium2`) are the heaviest things this project can import, and
  they must stay behind a source command.
- Every page an agent reads arrives fenced.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[2] / "src" / "axiomation"
HEAVY = ("qdrant_client", "voyageai", "fastembed", "pypdfium2", "pypdfium2_raw", "mcp")


def imported_modules(path: Path) -> set[str]:
    """Every module a file imports, at module level or inside a function."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def python_files(package: Path) -> list[Path]:
    return sorted(
        path for path in package.rglob("*.py") if "__pycache__" not in str(path)
    )


def test_the_read_half_never_imports_the_ingest_half() -> None:
    for path in python_files(SOURCE / "sources" / "read"):
        offending = {
            name
            for name in imported_modules(path)
            if name.startswith("axiomation.sources.ingest")
        }
        assert not offending, f"{path.name} imports {offending}"


def test_the_ingest_half_never_imports_the_read_half() -> None:
    for path in python_files(SOURCE / "sources" / "ingest"):
        offending = {
            name
            for name in imported_modules(path)
            if name.startswith("axiomation.sources.read")
        }
        assert not offending, f"{path.name} imports {offending}"


def test_the_contract_and_the_settings_stay_standard_library() -> None:
    """Both halves plus `config show` read these; neither may cost a second."""
    for name in ("contract.py", "config.py"):
        assert not imported_modules(SOURCE / "sources" / name) & set(HEAVY)


def test_the_heavy_list_names_the_pdf_library(tmp_path: Path) -> None:
    """The list has to name the libraries the cited works install, or the rule
    above passes for a library nobody named."""
    planted = tmp_path / "planted.py"
    planted.write_text("import pypdfium2\n", encoding="utf-8")

    assert imported_modules(planted) & set(HEAVY) == {"pypdfium2"}


RUN_CLI = (
    "import sys; sys.argv = ['axiomation', *{argv!r}]; "
    "from axiomation.cli import main; "
    "sys.exit(main())"
)


def imported_by_cli(*argv: str) -> set[str]:
    """The top-level packages the console script loads for one command line."""
    return imported_by(RUN_CLI.format(argv=list(argv)))


def imported_by(code: str) -> set[str]:
    """The top-level packages a Python process loads to run `code`."""
    result = subprocess.run(
        [sys.executable, "-X", "importtime", "-c", code],
        capture_output=True,
        text=True,
        check=False,
    )
    loaded = {
        line.rsplit("|", 1)[-1].strip().split(".")[0]
        for line in result.stderr.splitlines()
        if line.startswith("import time:") and "|" in line
    }
    assert loaded, result.stderr
    return loaded


@pytest.mark.parametrize(
    "argv", [("--version",), ("graph", "--help"), ("sources", "status", "--help")]
)
def test_a_command_s_help_never_loads_a_vector_stack(argv: tuple[str, ...]) -> None:
    assert not imported_by_cli(*argv) & set(HEAVY)


def test_the_import_count_sees_the_vector_stack() -> None:
    """The rule has to bite: a process that loads Qdrant is seen to."""
    pytest.importorskip("qdrant_client")
    assert "qdrant_client" in imported_by("import axiomation.sources.library.client")


# --- Every page an agent reads arrives fenced ---

#: The module where source text leaves the tool for an agent, and how its
#: serving points are found: through a subcommand and its renderer.
SERVING_MODULES = {
    "sources/cli.py": lambda name: name.startswith("_run_") or name.endswith("_lines"),
    # a session's reads of its cited works: each read and its renderer
    "sources/cited.py": lambda name: (
        name in {"_grep", "_locate"} or name.endswith("_lines")
    ),
}
#: A module whose serving points render through `sources/cli.py`'s
#: renderers, imported by name: those fence for it too.
RENDERS_THROUGH_CLI = {"sources/cited.py"}

#: What each serving point serves. A function that yields or returns the text
#: of a page is on the left and has to fence it; one that serves an image, a
#: metadata field, a page index or text already fenced is on the right. A
#: serving point that is on neither list fails the test, which is how a new
#: one that forgot the fence is caught.
FENCES = {
    "sources/cli.py": {
        "_run_search",
        "_run_page",
        "_run_locate",
        "_run_grep",
        "_run_toc",
        "_result_lines",
        "_page_lines",
        "_located_lines",
        "_grep_lines",
        # each file of a work through `_grep_lines`
        "_work_grep_lines",
        "_contents_lines",
        # a held file's title page, its opening words
        "_library_lines",
    },
    "sources/cited.py": {
        "_page_lines",
        "_grep",
        "_locate",
        # a search's hits, each its words clipped
        "search_lines",
    },
}

SERVES_NO_PAGE_TEXT = {
    "sources/cli.py": {
        "_run_ingest",
        "_run_image",
        # a held file's title page arrives fenced, from `read.status.said`
        "_run_status",
        "_run_resolve",
        # a held file's check, which names pages, never quotes one
        "_run_confirm",
        "_run_fetch",
        "_run_adopt",
        "_run_recheck",
        # names pages and edits, never a page's text (`verify.run`)
        "_run_verify",
        # a work's publication and versions of record, from the resolvers'
        # metadata; an acceptance's key and reason
        "_run_publications",
        "_run_accept",
        "_plan_lines",
        "_report_lines",
        # a work's file hash and its held check, which names pages, never
        # quotes one
        "_skipped_lines",
        # an entry's files, their paths and hashes, never a page's text
        "_undecided_lines",
        # which file of its work a key is: keys, labels, file names
        "_copy_lines",
        # page numbers whose records an ingest settles, never their text
        "_reader_lines",
        "_resolution_lines",
        "_fetch_lines",
        # the settings, then `_works_lines` or `_library_lines`
        "_status_lines",
        # each entry's key and its file check, which names its entry's
        # authors and a person's word, never a page's text
        "_works_lines",
        "_adopt_plan_lines",
        "_adopt_report_lines",
        # label-value rows
        "field_lines",
    },
    "sources/cited.py": set(),
}


def functions_of(path: Path) -> dict[str, ast.AST]:
    """Every function the module defines, nested ones included."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }


def called_names(node: ast.AST) -> set[str]:
    return {
        call.func.id
        for call in ast.walk(node)
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
    }


def fencing_names(functions: dict[str, ast.AST]) -> set[str]:
    """The fence's own functions, plus every local helper that reaches one.

    A serving point may fence through a one-line helper of its module, so the
    set grows to a fixed point before anything is asserted against it.
    """
    from axiomation import evidence as fence

    names = {name for name in vars(fence) if name.startswith("fenced")}
    assert names, "the evidence module exports nothing that fences"
    while True:
        grown = names | {
            name for name, node in functions.items() if called_names(node) & names
        }
        if grown == names:
            return names
        names = grown


@pytest.mark.parametrize("module", sorted(SERVING_MODULES))
def test_every_serving_point_is_accounted_for(module: str) -> None:
    """A page an agent can read arrives fenced, or the test names the hole.

    The fence is the guard, and a guard that only covers the paths somebody
    remembered is a prompt rule with extra steps. A serving point added
    later lands on neither list and fails here.
    """
    functions = functions_of(SOURCE / module)
    serving = {name for name in functions if SERVING_MODULES[module](name)}
    classified = FENCES[module] | SERVES_NO_PAGE_TEXT[module]
    assert serving - classified == set(), (
        f"{module}: {sorted(serving - classified)} serve(s) a source read and "
        f"is on neither list. Fence it and add it to FENCES, or say why it "
        f"carries no page text in SERVES_NO_PAGE_TEXT."
    )
    assert classified - serving == set(), (
        f"{module}: {sorted(classified - serving)} is listed and no longer exists"
    )


@pytest.mark.parametrize("module", sorted(SERVING_MODULES))
def test_a_serving_point_that_carries_page_text_fences_it(module: str) -> None:
    functions = functions_of(SOURCE / module)
    fences = fencing_names(functions)
    if module in RENDERS_THROUGH_CLI:
        fences |= fencing_names(functions_of(SOURCE / "sources/cli.py"))
    unfenced = [
        name
        for name in sorted(FENCES[module])
        if not called_names(functions[name]) & fences
    ]
    assert not unfenced, (
        f"{module}: {unfenced} serve(s) a page's text and never calls the "
        f"fence. Source text reaching an agent unfenced is the failure the "
        f"fence exists to prevent."
    )
