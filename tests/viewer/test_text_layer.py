"""The text layer: a kept state's export placed on its kept
files, every span in UTF-16 code units, read from the state's blobs and
never from the paper's directory.

On the queries' rich graph (`test_queries.rich`), and on a small paper with
an astral character before its blocks. No model is called.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from axiomation import versions
from axiomation.cutter import CutRules, DisplayRule, cut
from axiomation.exchange import cut_payload, dumps
from axiomation.exchange import offset as exchange_offset
from axiomation.queries import View
from axiomation.reader import document_hash, read_paper
from axiomation.records import RecordStore
from axiomation.versions import Command
from axiomation.viewer.payload import text_layer
from test_queries import rich  # noqa: F401 - the fixture

ASTRAL = "# A note\n\nThe set 𝔼 of means is closed.\n\nA second 𝔽 paragraph follows.\n"


def utf16(text: str, offset: int) -> int:
    """A code-point offset as the page's string index."""
    return len(text[:offset].encode("utf-16-le")) // 2


def kept(
    tmp_path: Path,
    text: str,
    filename: str = "p.md",
    others: dict[str, str] | None = None,
) -> View:
    root = tmp_path / "paper"
    root.mkdir()
    (root / filename).write_text(text, encoding="utf-8")
    for name, words in (others or {}).items():
        (root / name).write_text(words, encoding="utf-8")
    document = read_paper(root, Path(filename))
    payload = cut_payload(document, cut(document, CutRules(DisplayRule.IN_PARAGRAPH)))
    store = RecordStore(tmp_path / "store")
    state, _ = versions.record(
        store,
        versions.snapshot(root, document),
        document_hash(document),
        document.root,
        dumps(payload).encode(),
        Command.BUILD,
    )
    return View(store, state)


def test_title_comes_from_the_kept_state_not_the_working_file(tmp_path: Path) -> None:
    view = kept(tmp_path, '---\ntitle: "A finite lattice"\n---\n\nEvery path ends.\n')
    (tmp_path / "paper" / "p.md").write_text("The title is gone.\n")
    assert text_layer(view)["title"] == "A finite lattice"


def test_latex_title_is_read_across_lines(tmp_path: Path) -> None:
    view = kept(
        tmp_path,
        "\\documentclass{article}\n\\title{A finite\n lattice}\n"
        "\\begin{document}\n\\maketitle\nEvery path ends.\n\\end{document}\n",
        "p.tex",
    )
    assert text_layer(view)["title"] == "A finite lattice"


def test_absent_title_leaves_the_filename_available(tmp_path: Path) -> None:
    layer = text_layer(kept(tmp_path, "Every path ends.\n"))
    assert layer["title"] == ""
    assert layer["state"]["root"] == "p.md"


def test_each_block_s_spans_are_the_export_s_on_the_kept_text(rich) -> None:  # noqa: F811
    view, _, payload = rich
    layer = text_layer(view)
    texts = {f["path"]: f["text"] for f in layer["files"]}
    assert [b["id"] for b in layer["blocks"]] == [b["id"] for b in payload["blocks"]]
    for block, raw in zip(layer["blocks"], payload["blocks"], strict=True):
        assert block["node"] == view.node(raw["id"])
        assert block["handle"] == view.handle(raw["id"])
        assert block["roles"] == view.roles(raw["id"])
        for (file, start, end), span in zip(block["owned"], raw["owned"], strict=True):
            body = texts[file]
            assert start == utf16(body, exchange_offset(body, *span["start"]))
            assert end == utf16(body, exchange_offset(body, *span["end"]))
        out = sum(e["source"] == raw["id"] for e in payload["edges"])
        assert block["edges_out"] == out
    for standing, raw in zip(layer["standings"], payload["standings"], strict=True):
        file, start, end = standing["claim"]
        assert standing["words"] == raw["claim"]["text"]
        assert " ".join(texts[file][start:end].split()) == " ".join(
            raw["claim"]["text"].split()
        )
    assert [e["line"] for e in layer["edges"]] == [
        view.edge_line(e) for e in view.edges
    ]


def test_the_runs_cover_each_file_and_each_owner_owns_its_run(rich) -> None:  # noqa: F811
    view, _, _ = rich
    layer = text_layer(view)
    for f in layer["files"]:
        runs = f["runs"]
        assert runs[0][0] == 0 and runs[-1][1] == len(f["text"])
        assert all(a[1] == b[0] for a, b in zip(runs, runs[1:], strict=False))
        for start, end, cls, owner in runs:
            if owner < 0:
                assert cls != "owned"
                continue
            assert any(
                file == f["path"] and a <= start and end <= b
                for file, a, b in layer["blocks"][owner]["owned"]
            )
    assert layer["uncovered"] == 0


def test_sections_hold_the_blocks_the_queries_place_in_them(rich) -> None:  # noqa: F811
    view, _, _ = rich
    layer = text_layer(view)
    assert [s["name"] for s in layer["sections"]] == [s.name for s in view.section_list]
    for section in layer["sections"]:
        assert section["blocks"] == [
            b
            for b in view.blocks
            if view.sections.get(b) is not None
            and view.sections[b].name == section["name"]
        ]


def test_role_spans_lie_in_the_block_and_carry_its_roles(rich) -> None:  # noqa: F811
    view, _, _ = rich
    for block in text_layer(view)["blocks"]:
        assert block["role_spans"] is not None
        for file, start, end, role in block["role_spans"]:
            assert role in block["roles"]
            assert any(
                f == file and a <= start < end <= b for f, a, b in block["owned"]
            )


def test_offsets_are_utf16_past_an_astral_character(tmp_path) -> None:
    view = kept(tmp_path, ASTRAL)
    layer = text_layer(view)
    (f,) = layer["files"]
    text = f["text"]
    assert text == ASTRAL
    as_js = text.encode("utf-16-le")
    for block, raw in zip(layer["blocks"], view.export["blocks"], strict=True):
        ((_, start, end),) = block["owned"]
        span = raw["owned"][0]
        a = exchange_offset(text, *span["start"])
        b = exchange_offset(text, *span["end"])
        # the page's slice of its string is the block's own text
        assert as_js[2 * start : 2 * end].decode("utf-16-le") == text[a:b]
    last = layer["blocks"][-1]["owned"][0]
    assert last[1] > exchange_offset(text, 5, 1)  # two astral characters before it


def test_the_layer_reads_the_kept_files_not_the_paper_s(rich) -> None:  # noqa: F811
    view, _, _ = rich
    before = text_layer(view)
    root = view.store.directory.parent / "paper"
    assert root.is_dir()
    shutil.rmtree(root)
    assert text_layer(View(view.store, view.state)) == before


def test_each_citation_site_is_placed_with_its_keys(tmp_path) -> None:
    view = kept(tmp_path, "# A note\n\nThe set 𝔼 is closed \\citep{gauss1801}.\n")
    layer = text_layer(view)
    (site,) = layer["citations"]
    file, start, end = site["span"]
    (text,) = [f["text"] for f in layer["files"] if f["path"] == file]
    assert site["keys"] == ["gauss1801"]
    # the layer's offsets are UTF-16 code units, the 𝔼 two of them
    units = text.encode("utf-16-le")
    assert units[2 * start : 2 * end].decode("utf-16-le") == "\\citep{gauss1801}"
    assert site["line"] == 3


def test_the_paper_s_macros_reach_the_page_but_a_local_or_optional_one_does_not(
    tmp_path,
) -> None:
    view = kept(
        tmp_path,
        "---\ntitle: A note\n---\n\n"
        "\\newcommand{\\R}{\\mathbb{R}}\n\n"
        "\\newcommand{\\opt}[2][x]{#1 #2}\n\n"
        "The set $\\R$ is closed.\n\n"
        "\\begin{table}\n\\renewcommand{\\arraystretch}{2.2}\n\\end{table}\n",
    )
    macros = text_layer(view)["macros"]
    assert macros == [
        {
            "command": "newcommand",
            "name": "\\R",
            "arguments": 0,
            "definition": "{\\mathbb{R}}",
        }
    ]


def test_the_structure_the_pane_renders_is_placed_in_utf16(tmp_path) -> None:
    """The title, each environment with the name it prints, each reference
    and each footnote, as the text pane renders them in place."""
    view = kept(
        tmp_path,
        "\\documentclass{article}\n\\newtheorem{thm}{Theorem}\n\\title{On 𝔼}\n"
        "\\begin{document}\n\\maketitle\n\\section{Means}\\label{sec:m}\n"
        "\\begin{thm}[Closure]\nThe set 𝔼 is closed\\footnote{As usual.}.\n\\end{thm}\n"
        "By \\ref{sec:m}.\n\\end{document}\n",
        "p.tex",
    )
    layer = text_layer(view)
    (text,) = [f["text"] for f in layer["files"]]
    units = text.encode("utf-16-le")

    def words(span: list) -> str:
        _, start, end = span
        return units[2 * start : 2 * end].decode("utf-16-le")

    assert words(layer["title_at"]) == "On 𝔼"
    (thm,) = [e for e in layer["environments"] if e["name"] == "thm"]
    assert thm["printed"] == "Theorem"
    assert thm["kind"] == "statement"
    assert words(thm["span"]).startswith("\\begin{thm}[Closure]")
    assert words(thm["span"]).endswith("\\end{thm}")
    (ref,) = layer["references"]
    assert (words(ref["span"]), ref["targets"]) == ("\\ref{sec:m}", ["sec:m"])
    (note,) = layer["footnotes"]
    assert (words(note["span"]), words(note["text"])) == (
        "\\footnote{As usual.}",
        "As usual.",
    )


def test_the_footnotes_are_numbered_as_the_paper_reads_across_its_files(
    tmp_path,
) -> None:
    """A footnote's number is its place in reading order: an included
    file's notes between the notes before and after its `\\input`."""
    view = kept(
        tmp_path,
        "\\documentclass{article}\n\\begin{document}\n"
        "First\\footnote{One.}.\n\\input{s}\nLast\\footnote{Three.}.\n"
        "\\end{document}\n",
        "p.tex",
        {"s.tex": "Middle\\footnote{Two.}.\n"},
    )
    layer = text_layer(view)
    texts = {f["path"]: f["text"] for f in layer["files"]}

    def words(span: list) -> str:
        file, start, end = span
        units = texts[file].encode("utf-16-le")
        return units[2 * start : 2 * end].decode("utf-16-le")

    numbered = sorted((n["number"], words(n["text"])) for n in layer["footnotes"])
    assert numbered == [(1, "One."), (2, "Two."), (3, "Three.")]
