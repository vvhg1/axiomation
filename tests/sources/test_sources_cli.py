"""`axiomation sources` through the CLI."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

pytest.importorskip("qdrant_client")

from axiomation.cli import main  # noqa: E402
from axiomation.sources.config import ResolvedSources  # noqa: E402
from axiomation.sources.library import client as client_module  # noqa: E402
from axiomation.sources.library import qdrant as qdrant_module  # noqa: E402
from axiomation.sources.library.upsert import ensure_collection  # noqa: E402
from sources.fakes import FakeQdrant  # noqa: E402
from user_files import write_user_file  # noqa: E402


@pytest.fixture
def wired(
    monkeypatch: pytest.MonkeyPatch,
    fake_qdrant: FakeQdrant,
    settings: ResolvedSources,
    fixture_pdf: Path,
) -> FakeQdrant:
    """Every route to a Qdrant client hands back the same fake."""
    monkeypatch.setenv("AXIOMATION_VOYAGE_API_KEY", "fixture-key")
    monkeypatch.setattr(qdrant_module, "connect", lambda _: fake_qdrant)
    monkeypatch.setattr(client_module, "connect", lambda _: fake_qdrant)
    monkeypatch.setattr(
        client_module.SourceReader,
        "embed_query",
        lambda self, text: ([0.0] * 8, {"indices": [1], "values": [1.0]}),
    )
    for page in (1, 2):
        fake_qdrant.add_chunk(
            settings.collection,
            page_number=page,
            chunk_index=page - 1,
            text=f"page {page} " + "y" * 400,
            file_path=str(fixture_pdf),
        )
    return fake_qdrant


EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2


def run(root: Path, *argv: str) -> int:
    """`axiomation sources ACTION DIR …`; `-j` is `--json`."""
    _, action, *rest = argv
    store = root.parent / "store"
    rest = ["--json" if word == "-j" else word for word in rest]
    return main(["sources", action, str(root), *rest, "--store", str(store)])


def test_the_command_table_lists_sources(
    capsys: pytest.CaptureFixture,
) -> None:
    with pytest.raises(SystemExit) as exit_code:
        main(["--help"])

    assert exit_code.value.code == EXIT_OK
    assert "sources" in capsys.readouterr().out


def test_sources_needs_an_action(sources_paper: Path) -> None:
    with pytest.raises(SystemExit) as exit_code:
        main(["sources"])

    assert exit_code.value.code == EXIT_USAGE


def test_status_prints_the_collection_and_the_model(
    sources_paper: Path, wired: FakeQdrant, capsys: pytest.CaptureFixture
) -> None:
    assert run(sources_paper, "sources", "status") == EXIT_OK

    output = capsys.readouterr().out
    assert "testpaper_voyage-3-lite_8  (paper)" in output
    assert "voyage-3-lite / 8 dims  (paper)" in output
    assert "contract" in output


def test_status_says_where_each_value_came_from(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys, user_file: Path
) -> None:
    """A paper that declares nothing derives its collection and takes the
    URL and the key from the person's user file, which is named; the `.env`
    beside it is named as ignored, and its values are not read."""
    write_user_file(
        user_file, qdrant_url="http://localhost:65535", voyage_api_key="file-key"
    )
    paper = tmp_path / "undeclared"
    paper.mkdir()
    (paper / "paper.md").write_text("# A paper\n", encoding="utf-8")
    (paper / "refs.bib").write_text("", encoding="utf-8")
    (paper / "axiomation.toml").write_text(
        '[paper]\nmanuscripts = ["paper.md"]\nbib = "refs.bib"\n', encoding="utf-8"
    )
    (paper / ".env").write_text(
        "QDRANT_URL=http://localhost:6335\nOTHER_COLLECTION=papers_v2\n",
        encoding="utf-8",
    )
    fake = FakeQdrant()
    fake.make("axiomation_refs_voyage-context-4_2048", size=2048)
    monkeypatch.delenv("AXIOMATION_VOYAGE_API_KEY", raising=False)
    monkeypatch.delenv("AXIOMATION_QDRANT_URL", raising=False)
    monkeypatch.setattr(qdrant_module, "connect", lambda _: fake)
    monkeypatch.setattr(client_module, "connect", lambda _: fake)

    assert run(paper, "sources", "status") == EXIT_OK

    output = capsys.readouterr().out
    assert "axiomation_refs_voyage-context-4_2048  (derived)" in output
    assert "voyage-context-4 / 2048 dims  (default)" in output
    assert "http://localhost:65535  (user)" in output
    assert f"found  ({user_file})" in output
    assert f"user file      {user_file}\n" in output
    assert f"ignored        {paper / '.env'}: axiomation reads no .env" in output
    assert "file-key" not in output
    assert "papers_v2" not in output


def test_status_names_the_shell_when_the_url_comes_from_it(
    sources_paper: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """The shell speaks over the user file, and is named."""
    monkeypatch.setenv("AXIOMATION_QDRANT_URL", "http://localhost:65535")
    fake = FakeQdrant()
    fake.make("testpaper_voyage-3-lite_8", size=8)
    monkeypatch.setenv("AXIOMATION_VOYAGE_API_KEY", "fixture-key")
    monkeypatch.setattr(qdrant_module, "connect", lambda _: fake)
    monkeypatch.setattr(client_module, "connect", lambda _: fake)

    assert run(sources_paper, "sources", "status") == EXIT_OK

    output = capsys.readouterr().out
    assert "http://localhost:65535  (shell)" in output
    assert "found  (AXIOMATION_VOYAGE_API_KEY in the shell)" in output


def test_status_prints_the_context_window_a_marker_carries(
    sources_paper: Path,
    wired: FakeQdrant,
    settings: ResolvedSources,
    capsys: pytest.CaptureFixture,
) -> None:
    from dataclasses import replace

    from axiomation.sources.library.upsert import write_marker

    write_marker(wired, replace(settings, voyage_model="voyage-context-4"))

    assert run(sources_paper, "sources", "status") == EXIT_OK

    assert "context window  32 pages" in capsys.readouterr().out


def test_status_json_is_one_document(
    sources_paper: Path, wired: FakeQdrant, capsys: pytest.CaptureFixture
) -> None:
    assert run(sources_paper, "sources", "status", "-j") == EXIT_OK

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["settings"]["voyage_dims"] == 8
    assert payload["papers"] == 1


def test_status_exits_non_zero_when_the_contract_fails(
    sources_paper: Path,
    wired: FakeQdrant,
    settings: ResolvedSources,
    capsys: pytest.CaptureFixture,
) -> None:
    wired.sizes[settings.collection] = 2048

    assert run(sources_paper, "sources", "status") == EXIT_ERROR
    assert "contract FAILED" in capsys.readouterr().out


def test_status_says_a_collection_not_built_yet_is_no_failure(
    sources_paper: Path,
    wired: FakeQdrant,
    settings: ResolvedSources,
    capsys: pytest.CaptureFixture,
) -> None:
    """A new paper's collection does not exist until its first ingest: the
    normal state, said so, not a failure."""
    wired.collections.pop(settings.collection, None)

    assert run(sources_paper, "sources", "status") == EXIT_OK
    out = capsys.readouterr().out
    assert "not built yet: `axiomation sources ingest` builds" in out
    assert "FAILED" not in out


def test_status_says_a_collection_holding_only_its_marker_is_no_failure(
    sources_paper: Path,
    wired: FakeQdrant,
    settings: ResolvedSources,
    capsys: pytest.CaptureFixture,
) -> None:
    """A first ingest stopped before its first work leaves the collection
    and its marker: empty, not broken."""
    wired.collections.pop(settings.collection, None)
    ensure_collection(wired, settings)

    assert run(sources_paper, "sources", "status") == EXIT_OK
    out = capsys.readouterr().out
    assert "FAILED" not in out
    assert re.search(r"^papers +0$", out, re.MULTILINE)
    assert "neighbours' scheme" not in out


def test_search_prints_a_preview_per_hit(
    sources_paper: Path, wired: FakeQdrant, capsys: pytest.CaptureFixture
) -> None:
    assert run(sources_paper, "sources", "search", "series") == EXIT_OK

    output = capsys.readouterr().out
    assert "hardy1949 p1" in output


def test_search_json_carries_the_read_status(
    sources_paper: Path, wired: FakeQdrant, capsys: pytest.CaptureFixture
) -> None:
    assert run(sources_paper, "sources", "search", "series", "-j") == EXIT_OK

    payload = json.loads(capsys.readouterr().out)
    assert payload["read_status"] == "ok"
    assert payload["results"][0]["read_status"] == "ok"


def test_page_prints_the_whole_page(
    sources_paper: Path, wired: FakeQdrant, capsys: pytest.CaptureFixture
) -> None:
    assert run(sources_paper, "sources", "page", "hardy1949", "2") == EXIT_OK
    assert "page 2" in capsys.readouterr().out


def test_a_page_range_needs_only_the_end(
    sources_paper: Path, wired: FakeQdrant, capsys: pytest.CaptureFixture
) -> None:
    code = run(sources_paper, "sources", "page", "hardy1949", "1", "--to", "2", "-j")

    assert code == EXIT_OK
    assert len(json.loads(capsys.readouterr().out)["results"]) == 2


def test_a_missing_source_exits_non_zero(
    sources_paper: Path, wired: FakeQdrant, capsys: pytest.CaptureFixture
) -> None:
    code = run(sources_paper, "sources", "page", "nosuch1999", "1")

    assert code == EXIT_ERROR
    assert "absent" in capsys.readouterr().out


@pytest.fixture
def labelled(
    wired: FakeQdrant, settings: ResolvedSources, fixture_pdf: Path
) -> FakeQdrant:
    """A third page that prints a label, so the navigation verbs have a hit."""
    wired.add_chunk(
        settings.collection,
        page_number=3,
        chunk_index=2,
        text="Theorem 1.1 (Hardy) A divergent series may be summed. " + "y" * 400,
        file_path=str(fixture_pdf),
    )
    return wired


def test_locate_prints_the_ranked_pages(
    sources_paper: Path, labelled: FakeQdrant, capsys: pytest.CaptureFixture
) -> None:
    code = run(sources_paper, "sources", "locate", "hardy1949", "Theorem~1.1")

    assert code == EXIT_OK
    output = capsys.readouterr().out
    assert "hardy1949  theorem 1.1  [ok]  form word-number" in output
    assert "  line-initial" in output
    assert '<source-text key="hardy1949" page-index="3" status="ok"' in output
    assert "Theorem 1.1 (Hardy)" in output
    assert "text  3 pages" in output


def test_locate_json_carries_the_candidates_and_the_quality(
    sources_paper: Path, labelled: FakeQdrant, capsys: pytest.CaptureFixture
) -> None:
    code = run(sources_paper, "sources", "locate", "hardy1949", "Thm 1.1", "-j")

    assert code == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["candidates"][0]["page_index"] == 3
    assert payload["form"] == "word-number"
    assert payload["quality"]["pages"] == 3


def test_locate_refuses_a_page_locator_and_names_the_page_command(
    sources_paper: Path, labelled: FakeQdrant, capsys: pytest.CaptureFixture
) -> None:
    code = run(sources_paper, "sources", "locate", "hardy1949", "p.~3")

    assert code == EXIT_ERROR
    assert "sources page <key> 3" in capsys.readouterr().err


def test_a_miss_exits_zero_because_a_negative_is_an_answer(
    sources_paper: Path, labelled: FakeQdrant, capsys: pytest.CaptureFixture
) -> None:
    code = run(sources_paper, "sources", "locate", "hardy1949", "Lemma 7.7")

    assert code == EXIT_OK
    assert "no page of hardy1949 prints lemma 7.7" in capsys.readouterr().out


def test_locate_on_a_key_nobody_has_exits_non_zero(
    sources_paper: Path, labelled: FakeQdrant, capsys: pytest.CaptureFixture
) -> None:
    code = run(sources_paper, "sources", "locate", "nosuch1999", "Theorem 1")

    assert code == EXIT_ERROR
    assert "absent" in capsys.readouterr().out


def test_grep_prints_the_matching_lines_with_their_pages(
    sources_paper: Path, labelled: FakeQdrant, capsys: pytest.CaptureFixture
) -> None:
    code = run(sources_paper, "sources", "grep", "hardy1949", "divergent")

    assert code == EXIT_OK
    output = capsys.readouterr().out
    assert "1 line(s) on 1 page(s)" in output
    assert '<source-text key="hardy1949" page-index="3" status="ok"' in output
    assert "Theorem 1.1 (Hardy)" in output


def test_grep_json_carries_the_quality_of_the_text(
    sources_paper: Path, labelled: FakeQdrant, capsys: pytest.CaptureFixture
) -> None:
    code = run(sources_paper, "sources", "grep", "hardy1949", "nothing here", "-j")

    assert code == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["matches"] == []
    assert payload["read_status"] == "ok"
    assert payload["quality"]["text_layer"] == 3


def test_grep_refuses_a_pattern_that_is_not_a_regex(
    sources_paper: Path, labelled: FakeQdrant, capsys: pytest.CaptureFixture
) -> None:
    assert run(sources_paper, "sources", "grep", "hardy1949", "[") == EXIT_ERROR
    assert "regular expression" in capsys.readouterr().err


def test_toc_says_when_a_source_has_no_contents_list(
    sources_paper: Path, labelled: FakeQdrant, capsys: pytest.CaptureFixture
) -> None:
    code = run(sources_paper, "sources", "toc", "hardy1949", "-j")

    assert code == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["kind"] == "none"
    assert payload["outline"] == []


def test_toc_on_a_key_nobody_has_exits_non_zero(
    sources_paper: Path, labelled: FakeQdrant, capsys: pytest.CaptureFixture
) -> None:
    assert run(sources_paper, "sources", "toc", "nosuch1999") == EXIT_ERROR


def test_image_writes_a_png(
    sources_paper: Path, wired: FakeQdrant, tmp_path: Path
) -> None:
    out = tmp_path / "page.png"

    code = run(
        sources_paper, "sources", "image", "hardy1949", "1", "--output", str(out)
    )

    assert code == EXIT_OK
    assert out.read_bytes().startswith(b"\x89PNG")


def test_resolve_reports_where_a_source_lives(
    sources_paper: Path, wired: FakeQdrant, capsys: pytest.CaptureFixture
) -> None:
    code = run(sources_paper, "sources", "resolve", "hardy1949", "-j")

    assert code == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["indexed"] is True
    assert payload["chunks"] == 2


def test_the_resolved_zotero_address_reaches_the_adapter(
    sources_paper: Path, wired: FakeQdrant, monkeypatch, capsys
) -> None:
    """Where Zotero listens is a machine fact, and a second person
    who runs it elsewhere says so once."""
    from sources.fakes import FakeZotero

    with FakeZotero({"hardy1949": {"item_key": "ABCD1234", "paths": []}}) as library:
        monkeypatch.setenv("AXIOMATION_ZOTERO_URL", library.url)

        code = run(sources_paper, "sources", "resolve", "hardy1949", "-j")

        assert code == EXIT_OK
        payload = json.loads(capsys.readouterr().out)
        assert payload["zotero_link"] == "zotero://select/library/items/ABCD1234"
        assert ("item.search", ["hardy1949"]) in library.calls


def test_status_names_the_zotero_address_and_where_it_came_from(
    sources_paper: Path, wired: FakeQdrant, monkeypatch, capsys
) -> None:
    monkeypatch.setenv("AXIOMATION_ZOTERO_URL", "http://127.0.0.1:1")

    assert run(sources_paper, "sources", "status") == EXIT_OK

    output = capsys.readouterr().out
    assert "unavailable (bib only) at http://127.0.0.1:1  (shell)" in output
    assert "ocr backend" in output


def test_ingest_dry_run_refuses_a_collection_axiomation_did_not_create(
    sources_paper: Path, wired: FakeQdrant, capsys: pytest.CaptureFixture
) -> None:
    code = run(sources_paper, "sources", "ingest", "--dry-run")

    assert code == EXIT_ERROR
    output = capsys.readouterr().out
    assert "REFUSED" in output
    assert "nothing to do" in output


def test_ingest_dry_run_on_our_own_collection_reports_nothing_to_do(
    sources_paper: Path,
    wired: FakeQdrant,
    settings: ResolvedSources,
    capsys: pytest.CaptureFixture,
) -> None:
    ensure_collection(wired, settings)

    code = run(sources_paper, "sources", "ingest", "--dry-run", "-j")

    assert code == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["refusal"] == ""
    assert payload["pending"] == []


def test_ingest_refuses_to_write_to_a_foreign_collection(
    sources_paper: Path, wired: FakeQdrant, capsys: pytest.CaptureFixture
) -> None:
    code = run(sources_paper, "sources", "ingest")

    assert code == EXIT_ERROR
    assert "did not create it" in capsys.readouterr().err


def test_a_collection_override_targets_another_collection(
    sources_paper: Path, wired: FakeQdrant, capsys: pytest.CaptureFixture
) -> None:
    code = run(
        sources_paper,
        "sources",
        "ingest",
        "--dry-run",
        "-j",
        "--collection",
        "mine_voyage-3-lite_8",
    )

    assert code == EXIT_OK
    assert json.loads(capsys.readouterr().out)["collection"] == "mine_voyage-3-lite_8"


def test_ingest_without_a_bibliography_says_so(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    monkeypatch.setenv("AXIOMATION_QDRANT_URL", "http://localhost:65535")
    root = tmp_path / "nobib"
    root.mkdir()
    (root / "paper.md").write_text("# x\n", encoding="utf-8")
    (root / "axiomation.toml").write_text(
        '[paper]\nmanuscripts = ["paper.md"]\n', encoding="utf-8"
    )

    assert run(root, "sources", "ingest") == EXIT_ERROR
    assert "no bibliography" in capsys.readouterr().err


def test_an_ingest_that_ran_reports_what_it_cost(
    sources_paper: Path,
    wired: FakeQdrant,
    settings: ResolvedSources,
    monkeypatch: pytest.MonkeyPatch,
    fixture_pdf: Path,
    capsys: pytest.CaptureFixture,
) -> None:
    from axiomation.sources.ingest import run as run_module

    ensure_collection(wired, settings)
    (sources_paper / "references.bib").write_text(
        "@book{hardy1949,\n  title = {Divergent Series},\n"
        f"  file = {{{fixture_pdf}}}\n}}\n",
        encoding="utf-8",
    )
    ingested = (3, 1000, "", None)
    monkeypatch.setattr(run_module, "_ingest_entry", lambda *a, **k: ingested)

    assert run(sources_paper, "sources", "ingest") == EXIT_OK

    output = capsys.readouterr()
    assert "papers ingested   1" in output.out
    assert "chunks            3" in output.out
    assert "cost" in output.out
    assert "hardy1949" in output.err  # progress goes to stderr, JSON stays clean


def test_progress_stays_off_stdout_under_json(
    sources_paper: Path,
    wired: FakeQdrant,
    settings: ResolvedSources,
    monkeypatch: pytest.MonkeyPatch,
    fixture_pdf: Path,
    capsys: pytest.CaptureFixture,
) -> None:
    from axiomation.sources.ingest import run as run_module

    ensure_collection(wired, settings)
    (sources_paper / "references.bib").write_text(
        f"@book{{hardy1949,\n  title = {{D}},\n  file = {{{fixture_pdf}}}\n}}\n",
        encoding="utf-8",
    )
    ingested = (3, 1000, "", None)
    monkeypatch.setattr(run_module, "_ingest_entry", lambda *a, **k: ingested)

    assert run(sources_paper, "sources", "ingest", "-j") == EXIT_OK

    output = capsys.readouterr()
    assert json.loads(output.out)["ingested"] == 1
    assert output.err == ""


def test_image_without_an_out_path_writes_the_png_to_stdout(
    sources_paper: Path, wired: FakeQdrant, capsysbinary
) -> None:
    """Raw bytes, so `axiomation sources image k 1 > page.png` works."""
    assert run(sources_paper, "sources", "image", "hardy1949", "1") == EXIT_OK

    assert capsysbinary.readouterr().out.startswith(b"\x89PNG")


def test_image_of_a_page_nobody_has_exits_non_zero(
    sources_paper: Path, wired: FakeQdrant, capsys: pytest.CaptureFixture
) -> None:
    code = run(sources_paper, "sources", "image", "nosuch1999", "1")

    assert code == EXIT_ERROR
    assert capsys.readouterr().out.startswith("absent: no source holds nosuch1999")


def test_verbose_status_lists_every_source(
    sources_paper: Path, wired: FakeQdrant, capsys: pytest.CaptureFixture
) -> None:
    assert run(sources_paper, "sources", "status", "-v") == EXIT_OK

    assert "hardy1949" in capsys.readouterr().out


def test_status_says_when_a_collection_lacks_the_hashes(
    sources_paper: Path,
    wired: FakeQdrant,
    settings: ResolvedSources,
    capsys: pytest.CaptureFixture,
) -> None:
    for point in wired.points(settings.collection):
        for legacy in ("file_sha256", "bib_sha256", "ocr_backend"):
            point.payload.pop(legacy, None)

    assert run(sources_paper, "sources", "status") == EXIT_OK

    output = capsys.readouterr().out
    assert "lacks" in output
    assert "--force" in output


def test_status_names_a_source_with_unreadable_pages(
    sources_paper: Path,
    wired: FakeQdrant,
    settings: ResolvedSources,
    capsys: pytest.CaptureFixture,
) -> None:
    wired.add_chunk(
        settings.collection, bib_key="scan1890", chunk_index=0, ocr_backend="failed"
    )

    assert run(sources_paper, "sources", "status") == EXIT_OK

    assert "scan1890" in capsys.readouterr().out


def test_status_counts_the_chunks_each_text_source_read(
    sources_paper: Path,
    wired: FakeQdrant,
    settings: ResolvedSources,
    capsys: pytest.CaptureFixture,
) -> None:
    """A page its layer serves is `none`; a scan's pages are the OCR's. The
    most first."""
    for index in range(3):
        wired.add_chunk(
            settings.collection,
            bib_key="scan1890",
            chunk_index=index,
            ocr_backend="mineru",
        )

    assert run(sources_paper, "sources", "status", "-j") == EXIT_OK
    read_by = json.loads(capsys.readouterr().out)["read_by"]
    assert run(sources_paper, "sources", "status") == EXIT_OK

    assert read_by["mineru"] == 3
    assert set(read_by) == {"mineru", "none"}
    assert f"read by     mineru 3, none {read_by['none']}" in capsys.readouterr().out


def test_a_refused_plan_says_so_at_the_bottom_too(monkeypatch, sources_paper, capsys):
    """`--force` on a real bibliography makes the plan hundreds of lines long,
    so the refusal at the top scrolls away. A reader piping it through `tail`
    must not see a plan and conclude it is going to run."""
    from axiomation.sources import cli as command

    class Plan:
        collection = "somebody_elses"
        exists = True
        refusal = "somebody_elses carries no axiomation marker."
        prune_refusal = ""
        stale: list[str] = []
        metadata_only: list[object] = []
        skipped: list[object] = []
        readers: list[object] = []
        unwitnessed: list[object] = []
        untested: list[object] = []
        layers: list[object] = []
        undecided: list[object] = []
        moves: list[object] = []
        empty = False

        class _Item:
            class reason:
                value = "forced"

            class entry:
                key = "hardy1949"
                title = "Divergent Series"
                label = ""
                choice = None

            pages: tuple[int, ...] = ()
            count = 0
            refreshed: tuple[int, ...] = ()

        pending = [_Item()]

    lines = list(command._plan_lines(Plan()))

    assert lines[1].startswith("REFUSED:")
    assert lines[-1].startswith("REFUSED:")
    assert "Nothing above would be written" in lines[-1]


def test_a_plan_with_nothing_refused_does_not_repeat_itself(sources_paper):
    from axiomation.sources import cli as command

    class Plan:
        collection = "ours"
        exists = True
        refusal = ""
        prune_refusal = ""
        stale: list[str] = []
        pending: list[object] = []
        metadata_only: list[object] = []
        skipped: list[object] = []
        readers: list[object] = []
        unwitnessed: list[object] = []
        untested: list[object] = []
        layers: list[object] = []
        undecided: list[object] = []
        moves: list[object] = []
        empty = True

    lines = list(command._plan_lines(Plan()))

    assert lines == ["collection ours", "nothing to do"]


def test_a_disputed_page_is_shown_with_its_note() -> None:
    """A reader is told to read the page as an image before its text."""
    from axiomation.evidence import Page, collected
    from axiomation.sources.cli import _page_lines

    disputed = Page("hardy1949", "Divergent Series", 4, "", "a reading", disputed=2)

    lines = list(_page_lines(collected([disputed])))

    assert lines[0] == (
        "hardy1949 page index 4: 2 findings its OCR could not settle; "
        "read the page as an image"
    )
    assert "a reading" in lines[1]


def test_sources_at_a_workspace_s_root_reads_its_corpus(tmp_path, monkeypatch) -> None:
    """`sources` given no paper at a workspace's root: the root, as if
    given (the workspace's corpus); inside a paper, the paper."""
    import argparse

    from axiomation.paper_argument import with_paper
    from axiomation.sources.cli import add_arguments

    workspace = tmp_path / "workspace"
    paper = workspace / "papers" / "p"
    paper.mkdir(parents=True)
    (workspace / "axiomation.toml").write_text("[workspace]\n", encoding="utf-8")
    commands = argparse.ArgumentParser()
    add_arguments(commands)

    monkeypatch.chdir(workspace)
    assert with_paper(commands, ["page", "key", "3"], workspace=True).argv == [
        "page",
        str(workspace.resolve()),
        "key",
        "3",
    ]
    monkeypatch.chdir(paper)
    assert with_paper(commands, ["publications", "a", "b"], workspace=True).argv == [
        "publications",
        str(paper.resolve()),
        "a",
        "b",
    ]
