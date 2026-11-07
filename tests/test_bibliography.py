"""A paper's bibliography: resolved by the core as the cited works'
settings resolve it, kept by `snapshot` in every state, one in the workspace by its
`workspace:` key and materialized nowhere, and laid into a review session
as `bib/<its file name>`. Synthetic directories only."""

from __future__ import annotations

from pathlib import Path

from axiomation import review, settings, versions
from axiomation.cutter import CutRules, DisplayRule, cut
from axiomation.exchange import cut_payload, dumps
from axiomation.reader import document_hash, read_paper
from axiomation.records import RecordStore
from axiomation.settings import CONFIG_NAME
from axiomation.versions import Command

BIB = "@article{a, title = {A}}\n"


def a_workspace(root: Path, shared: str = "", own: str = "") -> Path:
    """A workspace at `root` with one paper, `papers/one`, and its tables."""
    paper = root / "papers" / "one"
    paper.mkdir(parents=True)
    (paper / "paper.md").write_text("# One\n\nAs [@a] shows.\n")
    (root / CONFIG_NAME).write_text('[workspace]\npapers = "papers/*"\n' + shared)
    if own:
        (paper / CONFIG_NAME).write_text(own)
    return paper


def bibliography(paper: Path) -> Path | None:
    return settings.bibliography(settings.load(paper))


def test_the_workspace_names_the_bibliography_relative_to_its_file(tmp_path) -> None:
    paper = a_workspace(tmp_path, '\n[sources]\nbib = "references.bib"\n')
    assert bibliography(paper) == tmp_path.resolve() / "references.bib"


def test_the_paper_s_bibliography_wins_over_the_workspace_s(tmp_path) -> None:
    paper = a_workspace(
        tmp_path,
        '\n[sources]\nbib = "references.bib"\n',
        '[sources]\nbib = "own.bib"\n',
    )
    assert bibliography(paper) == paper.resolve() / "own.bib"


def test_with_none_named_it_is_the_one_beside_the_paper(tmp_path) -> None:
    paper = a_workspace(tmp_path)
    assert bibliography(paper) is None
    (paper / "refs.bib").write_text(BIB)
    assert bibliography(paper) == paper.resolve() / "refs.bib"
    (paper / "other.bib").write_text(BIB)
    assert bibliography(paper) is None


def test_a_workspace_bibliography_is_kept_by_its_key_and_materialized_nowhere(
    tmp_path,
) -> None:
    root = tmp_path / "ws"
    paper = a_workspace(root, '\n[sources]\nbib = "references.bib"\n')
    (root / "references.bib").write_text(BIB)
    document = read_paper(paper)

    files = versions.snapshot(paper, document)

    assert files["workspace:references.bib"] == BIB.encode()
    payload = cut_payload(document, cut(document, CutRules(DisplayRule.IN_PARAGRAPH)))
    store = RecordStore(tmp_path / "store")
    state, _ = versions.record(
        store,
        files,
        document_hash(document),
        document.root,
        dumps(payload).encode(),
        Command.BUILD,
    )
    assert "workspace:references.bib" in state.files
    into = tmp_path / "materialized"
    versions.materialize(store, state, into)
    assert not any("workspace" in p.name or p.suffix == ".bib" for p in into.rglob("*"))


def test_a_bibliography_beside_the_paper_is_kept_once_by_its_name(tmp_path) -> None:
    paper = a_workspace(tmp_path, own='[sources]\nbib = "refs.bib"\n')
    (paper / "refs.bib").write_text(BIB)

    files = versions.snapshot(paper, read_paper(paper))

    assert files["refs.bib"] == BIB.encode()
    assert [key for key in files if key.endswith(".bib")] == ["refs.bib"]


def test_a_bibliography_outside_the_workspace_or_missing_is_not_kept(
    tmp_path,
) -> None:
    (tmp_path / "elsewhere.bib").write_text(BIB)
    paper = a_workspace(tmp_path / "ws", '\n[sources]\nbib = "../elsewhere.bib"\n')
    assert not any(
        k.endswith(".bib") for k in versions.snapshot(paper, read_paper(paper))
    )

    missing = a_workspace(tmp_path / "ws2", '\n[sources]\nbib = "missing.bib"\n')
    assert not any(
        k.endswith(".bib") for k in versions.snapshot(missing, read_paper(missing))
    )


def test_a_kept_bibliography_is_laid_in_by_its_file_name() -> None:
    assert review.bib_path("refs.bib") == "bib/refs.bib"
    assert review.bib_path("workspace:references.bib") == "bib/references.bib"
    assert review.bib_path("workspace:shared/lib.bib") == "bib/lib.bib"
