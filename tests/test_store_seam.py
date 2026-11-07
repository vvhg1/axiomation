"""The store's seam: only `records.py` and `versions.py` know how a store
lies on disk; every other module asks the store (`docs/architecture.md`
§ 4). So the store's layout can change behind them alone.
"""

from __future__ import annotations

import ast
from pathlib import Path

from axiomation import versions
from axiomation.records import RecordStore

SOURCE = Path(__file__).resolve().parents[1] / "src" / "axiomation"
#: The modules that know the store's layout.
LAYOUT = {SOURCE / "records.py", SOURCE / "versions.py"}
#: The store's own files' names, as `records` names them.
STORE_FILES = {"RECORD", "LOCK"}


def _names_a_store(node: ast.expr) -> bool:
    """`<…>store.directory`, `<…>.store.directory`: a store's directory."""
    if not (isinstance(node, ast.Attribute) and node.attr == "directory"):
        return False
    inner = node.value
    name = inner.attr if isinstance(inner, ast.Attribute) else getattr(inner, "id", "")
    return name.endswith("store")


def _names_a_paper_s_store(node: ast.expr) -> bool:
    """`<…>.paper.store`, or `Path(<…>.paper.store)`: a store's place, as
    a paper names it."""
    if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "Path":
        node = node.args[0] if node.args else node
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "store"
        and isinstance(node.value, ast.Attribute)
        and node.value.attr == "paper"
    )


def _paths_inside_a_store(path: Path) -> list[int]:
    """The lines where a path is built inside a store: its directory
    joined with a name, or handed to a call that may join it (a name, as
    `str` makes, excepted: the store's run asks `run_directory`), the
    paper joined with the store's name, or a store file `records` names,
    however it is reached."""
    found = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if (
            isinstance(node, ast.BinOp)
            and isinstance(node.op, ast.Div)
            and (
                _names_a_store(node.left)
                or _names_a_paper_s_store(node.left)
                or (
                    isinstance(node.right, ast.Name)
                    and node.right.id == "STORE_DIRECTORY"
                )
            )
        ):
            found.append(node.lineno)
        if (
            isinstance(node, ast.Call)
            and getattr(node.func, "id", None) != "str"
            and any(
                _names_a_store(a)
                for a in (*node.args, *(k.value for k in node.keywords))
            )
        ):
            found.append(node.lineno)
        if (isinstance(node, ast.Attribute) and node.attr in STORE_FILES) or (
            isinstance(node, ast.Name) and node.id in STORE_FILES
        ):
            found.append(node.lineno)
        if isinstance(node, ast.ImportFrom) and any(
            alias.name in STORE_FILES for alias in node.names
        ):
            found.append(node.lineno)
    return found


def test_no_module_but_the_store_s_own_builds_a_path_inside_a_store() -> None:
    offending = {
        str(path.relative_to(SOURCE)): lines
        for path in sorted(SOURCE.rglob("*.py"))
        if path not in LAYOUT and (lines := _paths_inside_a_store(path))
    }
    assert not offending, f"paths built inside a store outside its seam: {offending}"


def test_a_paper_s_own_store_is_named_without_being_made(tmp_path: Path) -> None:
    named = versions.store_of(tmp_path)
    assert named == tmp_path / "axiomation"
    assert not named.exists()
    assert versions.default_store(tmp_path) == named
    assert (named / ".gitignore").exists()


def test_the_store_answers_for_its_run_s_places(tmp_path: Path) -> None:
    store = RecordStore(tmp_path / "store")
    assert store.key == (tmp_path / "store").resolve()
    assert store.transcripts == tmp_path / "store" / "transcripts"
    assert store.run_directory == tmp_path / "store"


def test_a_copy_leaves_the_transcripts_out(tmp_path: Path) -> None:
    store = RecordStore(tmp_path / "store")
    store.keep_state({"id": "s"}, {}, {}, {})
    store.transcripts.mkdir()
    (store.transcripts / "a-1.jsonl").write_text("{}\n", encoding="utf-8")
    assert versions.keeps_state(store.directory)

    copy = store.copy_to(tmp_path / "copy")

    assert versions.keeps_state(copy.directory)
    assert not copy.transcripts.exists()
    assert copy.key != store.key
    assert not versions.keeps_state(tmp_path / "none")
    assert not (tmp_path / "none").exists()
