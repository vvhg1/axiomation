"""Resolving `[sources]`: what is declared, derived, the person's.

The corpus's identity — collection, model, dimensions — is configuration, and
a paper that declares none of it derives its collection from the default
model and its bibliography. Where a service runs, the keys, the OCR engine
and who asks the lookups are the person's: read from the user file, the
shell over it (`user_settings.py`); no `.env` is read.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from axiomation import settings as core_settings
from axiomation.sources.config import (
    CONFIG_NAME,
    DEFAULT_VOYAGE_DIMS,
    DEFAULT_VOYAGE_MODEL,
    DEFAULT_ZOTERO_URL,
    Contact,
    OcrBackend,
    SettingFrom,
    SourcesError,
    api_key,
    api_key_source,
    contact,
    core_api_key,
    load_settings,
    no_contact,
    ocr_api_key,
    resolve,
)
from axiomation.sources.contract import collection_name
from axiomation.user_settings import UserKey
from user_files import write_user_file

#: A `.env` in the shape another tool's has: what axiomation never reads.
OTHER_ENV = (
    "VOYAGE_API_KEY=env-secret\n"
    "QDRANT_URL=http://localhost:6335\n"
    "OCR_URL=http://from-env-file/ocr\n"
    "AXIOMATION_CONTACT_EMAIL=env@example.org\n"
)


def write_paper(root: Path, sources: str = "", env: str | None = None) -> Path:
    """A paper with a `[sources]` body, and optionally a `.env` beside it."""
    paper = root / "paper"
    paper.mkdir(parents=True, exist_ok=True)
    (paper / "paper.md").write_text("# A paper\n", encoding="utf-8")
    (paper / CONFIG_NAME).write_text(
        '[paper]\nmanuscripts = ["paper.md"]\nbib = "refs.bib"\n\n'
        f"[sources]\n{sources}",
        encoding="utf-8",
    )
    (paper / "refs.bib").write_text("", encoding="utf-8")
    if env is not None:
        (paper / ".env").write_text(env, encoding="utf-8")
    return paper


def _write_paper(root: Path, extra: str = "") -> None:
    (root / "paper.md").write_text("# A paper\n", encoding="utf-8")
    (root / CONFIG_NAME).write_text(
        '[paper]\nmanuscripts = ["paper.md"]\n' + extra, encoding="utf-8"
    )


def test_an_empty_section_falls_back_to_the_module_defaults(tmp_path: Path) -> None:
    paper = write_paper(tmp_path)
    settings = resolve(load_settings(paper))

    assert settings.qdrant_url == ""  # no library unless one is named
    assert settings.voyage_model == DEFAULT_VOYAGE_MODEL
    assert settings.voyage_dims == DEFAULT_VOYAGE_DIMS
    assert settings.ocr_backend is OcrBackend.TESSERACT
    assert settings.collection == collection_name(
        DEFAULT_VOYAGE_MODEL, DEFAULT_VOYAGE_DIMS, paper / "refs.bib"
    )
    assert settings.origin("collection") is SettingFrom.DERIVED
    assert settings.origin("voyage_model") is SettingFrom.DEFAULT
    assert settings.origin("qdrant_url") is SettingFrom.DEFAULT
    assert settings.origin("ocr_backend") is SettingFrom.DEFAULT
    assert settings.origin("bib") is SettingFrom.PAPER


def test_the_default_model_is_the_corpus_s_model() -> None:
    """The default is `voyage-context-4` at 2048; with the naming rule, that is
    what makes an undeclared paper land on the corpus."""
    assert DEFAULT_VOYAGE_MODEL == "voyage-context-4"
    assert DEFAULT_VOYAGE_DIMS == 2048


def test_the_user_file_supplies_the_person_s_values(
    tmp_path: Path, user_file: Path
) -> None:
    write_user_file(
        user_file,
        qdrant_url="http://localhost:6335",
        ocr_backend="http",
        ocr_url="https://ocr.example/v1",
        zotero_url="http://z:1",
    )
    paper = write_paper(tmp_path)

    settings = resolve(load_settings(paper))

    assert settings.qdrant_url == "http://localhost:6335"
    assert settings.ocr_backend is OcrBackend.HTTP
    assert settings.ocr_url == "https://ocr.example/v1"
    assert settings.zotero_url == "http://z:1"
    for name in ("qdrant_url", "ocr_backend", "ocr_url", "zotero_url"):
        assert settings.origin(name) is SettingFrom.USER
    assert settings.collection == collection_name(
        DEFAULT_VOYAGE_MODEL, DEFAULT_VOYAGE_DIMS, paper / "refs.bib"
    )


def test_the_shell_speaks_over_the_user_file(
    tmp_path: Path, user_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_user_file(user_file, qdrant_url="http://from-file:1")
    monkeypatch.setenv("AXIOMATION_QDRANT_URL", "http://from-shell:1")

    settings = resolve(load_settings(write_paper(tmp_path)))

    assert settings.qdrant_url == "http://from-shell:1"
    assert settings.origin("qdrant_url") is SettingFrom.SHELL


def test_no_env_file_and_no_bare_name_is_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `.env` beside the paper or at the workspace's root, and the bare
    names another tool sets (`QDRANT_URL`, `OCR_URL`), steer nothing."""
    workspace = tmp_path / "papers"
    paper = workspace / "papers" / "alpha"
    paper.mkdir(parents=True)
    (workspace / CONFIG_NAME).write_text(
        '[workspace]\npapers = "papers/*"\n', encoding="utf-8"
    )
    _write_paper(paper)
    for where in (paper, workspace):
        (where / ".env").write_text(OTHER_ENV, encoding="utf-8")
    monkeypatch.setenv("QDRANT_URL", "http://bare:1")
    monkeypatch.setenv("VOYAGE_API_KEY", "bare-secret")
    config = load_settings(paper)

    settings = resolve(config)

    assert settings.qdrant_url == ""  # no library unless one is named
    assert settings.ocr_url == ""
    assert api_key_source(config) == ""
    assert contact(config) == Contact()
    assert core_settings.ignored_env_files(paper, workspace) == (
        paper / ".env",
        workspace / ".env",
    )


def test_a_workspace_root_s_sources_reach_the_resolved_settings(
    tmp_path: Path,
) -> None:
    """One `[sources]` at the root serves every paper in it."""
    workspace = tmp_path / "papers"
    paper = workspace / "papers" / "alpha"
    paper.mkdir(parents=True)
    (workspace / CONFIG_NAME).write_text(
        '[workspace]\npapers = "papers/*"\ndefault = "alpha"\n\n'
        '[sources]\nvoyage_model = "voyage-4"\nvoyage_dims = 2048\n',
        encoding="utf-8",
    )
    _write_paper(paper, extra='bib = "../../refs.bib"\n')

    settings = resolve(load_settings(paper))

    assert settings.voyage_model == "voyage-4"
    assert settings.origin("voyage_model") is SettingFrom.WORKSPACE
    assert settings.collection == "axiomation_refs_voyage-4_2048"
    assert settings.origin("collection") is SettingFrom.DERIVED


def test_the_origins_are_in_the_json(tmp_path: Path, user_file: Path) -> None:
    write_user_file(user_file, qdrant_url="http://localhost:6335")

    payload = resolve(load_settings(write_paper(tmp_path))).as_dict()

    assert payload["origins"]["qdrant_url"] == "user"
    assert payload["origins"]["collection"] == "derived"
    assert "env_file" not in payload


def test_the_collection_name_follows_the_model_when_nobody_names_it(
    tmp_path: Path,
) -> None:
    paper = write_paper(
        tmp_path, sources='voyage_model = "voyage-4"\nvoyage_dims = 2048\n'
    )

    assert resolve(load_settings(paper)).collection == "axiomation_refs_voyage-4_2048"


def test_the_ingest_bib_defaults_to_the_paper_bib(tmp_path: Path) -> None:
    paper = write_paper(tmp_path)

    assert resolve(load_settings(paper)).bib == paper / "refs.bib"


def test_a_sources_bib_overrides_the_paper_bib(tmp_path: Path) -> None:
    paper = write_paper(tmp_path, sources='bib = "corpus.bib"\n')

    assert resolve(load_settings(paper)).bib == paper / "corpus.bib"


@pytest.mark.parametrize("key", ["qdrant_url", "ocr_backend", "contact_email"])
def test_a_person_s_value_in_the_paper_s_sources_is_refused(
    tmp_path: Path, key: str
) -> None:
    """The paper's file names where they go."""
    paper = write_paper(tmp_path, sources=f'{key} = "x"\n')

    with pytest.raises(SourcesError, match=rf"\[?sources\]?\.{key}.*config\.toml"):
        load_settings(paper)


def test_an_unknown_ocr_backend_names_where_it_was_set(
    tmp_path: Path, user_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_user_file(user_file, ocr_backend="ocrmypdf")
    config = load_settings(write_paper(tmp_path))

    with pytest.raises(SourcesError, match=f"ocr_backend in {user_file}.*tesseract"):
        resolve(config)
    monkeypatch.setenv("AXIOMATION_OCR_BACKEND", "nope")
    with pytest.raises(SourcesError, match="AXIOMATION_OCR_BACKEND is 'nope'"):
        resolve(load_settings(write_paper(tmp_path)))


@pytest.mark.parametrize("backend", ["http", "paddle", "mineru"])
def test_no_service_has_an_address_of_its_own(
    tmp_path: Path, user_file: Path, backend: str
) -> None:
    """A default would name a server on the person's machine nobody told
    them to run: `provider_for` refuses and names the key."""
    write_user_file(user_file, ocr_backend=backend)

    settings = resolve(load_settings(write_paper(tmp_path)))

    assert settings.ocr_url == ""
    assert settings.origin("ocr_backend") is SettingFrom.USER


def test_zotero_answers_on_its_own_port_when_nobody_says_otherwise(
    tmp_path: Path,
) -> None:
    settings = resolve(load_settings(write_paper(tmp_path)))

    assert settings.zotero_url == DEFAULT_ZOTERO_URL
    assert settings.origin("zotero_url") is SettingFrom.DEFAULT


class TestTheKeys:
    """A secret is the person's, never a config dataclass's field."""

    def test_the_shell_s_key_comes_first(
        self, tmp_path: Path, user_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        write_user_file(user_file, voyage_api_key="file-secret")
        monkeypatch.setenv(UserKey.VOYAGE_API_KEY.env, "shell-secret")
        config = load_settings(write_paper(tmp_path))

        assert api_key(config) == "shell-secret"
        assert api_key_source(config) == "AXIOMATION_VOYAGE_API_KEY in the shell"

    def test_the_user_file_s_key_is_named_by_its_file(
        self, tmp_path: Path, user_file: Path
    ) -> None:
        write_user_file(user_file, voyage_api_key="file-secret")
        config = load_settings(write_paper(tmp_path))

        assert api_key(config) == "file-secret"
        assert api_key_source(config) == str(user_file)

    def test_a_missing_key_says_where_to_put_one(
        self, tmp_path: Path, user_file: Path
    ) -> None:
        config = load_settings(write_paper(tmp_path))

        with pytest.raises(SourcesError) as error:
            api_key(config)

        message = str(error.value)
        assert "voyage_api_key" in message and str(user_file) in message
        assert "AXIOMATION_VOYAGE_API_KEY in the shell" in message
        assert api_key_source(config) == ""

    def test_a_secret_never_enters_the_settings(
        self, tmp_path: Path, user_file: Path
    ) -> None:
        write_user_file(
            user_file, voyage_api_key="voyage-secret", ocr_api_key="bearer-token"
        )
        config = load_settings(write_paper(tmp_path))
        settings = resolve(config)

        assert ocr_api_key(config) == "bearer-token"
        for secret in ("voyage-secret", "bearer-token"):
            assert secret not in repr(settings)
            assert secret not in str(settings.as_dict())
            assert secret not in repr(config)

    def test_the_optional_keys_are_empty_and_no_failure(self, tmp_path: Path) -> None:
        config = load_settings(write_paper(tmp_path))

        assert ocr_api_key(config) == ""
        assert core_api_key(config) == ""

    def test_the_core_key_is_the_person_s(
        self, tmp_path: Path, user_file: Path
    ) -> None:
        write_user_file(user_file, core_api_key="core-secret")

        assert core_api_key(load_settings(write_paper(tmp_path))) == "core-secret"


class TestTheContactAddress:
    """Who asks Crossref, OpenAlex and Unpaywall is the person: their
    `contact_email`, the shell's over the user file's."""

    def test_the_user_file_s_address(self, tmp_path: Path, user_file: Path) -> None:
        write_user_file(user_file, contact_email="me@example.org")

        assert contact(load_settings(write_paper(tmp_path))) == Contact(
            "me@example.org", str(user_file)
        )

    def test_the_shell_speaks_over_the_file(
        self, tmp_path: Path, user_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        write_user_file(user_file, contact_email="me@example.org")
        monkeypatch.setenv("AXIOMATION_CONTACT_EMAIL", "shell@example.org")

        assert contact(load_settings(write_paper(tmp_path))) == Contact(
            "shell@example.org", "AXIOMATION_CONTACT_EMAIL in the shell"
        )

    def test_none_set_says_what_a_lookup_loses_and_where_to_set_it(
        self, tmp_path: Path, user_file: Path
    ) -> None:
        config = load_settings(write_paper(tmp_path))

        assert contact(config) == Contact()
        assert no_contact(config) == (
            "Unpaywall is skipped, and Crossref and OpenAlex are asked outside "
            "their polite pools, where they throttle: set contact_email = "
            f'"…" in {user_file}, or AXIOMATION_CONTACT_EMAIL in the shell'
        )

    def test_the_address_stays_out_of_the_resolved_settings(
        self, tmp_path: Path, user_file: Path
    ) -> None:
        write_user_file(user_file, contact_email="me@example.org")

        resolved = resolve(load_settings(write_paper(tmp_path)))

        assert "me@example.org" not in repr(resolved)
        assert "contact_email" not in resolved.origins


@pytest.mark.parametrize(
    ("shared", "own", "beside"),
    [
        ('\n[sources]\nbib = "references.bib"\n', "", ()),
        ('\n[sources]\nbib = "references.bib"\n', '[sources]\nbib = "own.bib"\n', ()),
        ("", '[paper]\nbib = "refs.bib"\n', ()),
        ("", "", ("refs.bib",)),
        ("", "", ("a.bib", "b.bib")),
        ('\n[sources]\nbib = "references.bib"\n', '[paper]\nbib = "own.bib"\n', ()),
        ("", '[sources]\nbib = "../../references.bib"\n', ("refs.bib",)),
    ],
)
def test_the_core_resolves_the_bibliography_the_extra_reads(
    tmp_path: Path, shared: str, own: str, beside: tuple[str, ...]
) -> None:
    """The core keeps the bibliography with each state; the cited works read it.
    The two must name the same file."""
    paper = tmp_path / "papers" / "one"
    paper.mkdir(parents=True)
    (paper / "paper.md").write_text("# A paper\n", encoding="utf-8")
    (tmp_path / CONFIG_NAME).write_text(
        '[workspace]\npapers = "papers/*"\n' + shared, encoding="utf-8"
    )
    if own:
        (paper / CONFIG_NAME).write_text(own, encoding="utf-8")
    for name in beside:
        (paper / name).write_text("", encoding="utf-8")

    extra = resolve(load_settings(paper)).bib
    core = core_settings.bibliography(core_settings.load(paper))

    assert core == extra


@pytest.mark.parametrize(
    ("user", "paper", "shell", "off"),
    [
        ("off", None, None, 'library = "off" (user)'),
        (None, "off", None, 'library = "off" (paper)'),
        ("off", "on", None, ""),
        ("on", "off", None, 'library = "off" (paper)'),
        (None, "off", "on", ""),
        (None, None, "off", 'library = "off" (shell)'),
    ],
)
def test_the_library_switch_reads_each_work_s_pdf_where_it_says_off(
    tmp_path: Path,
    user_file: Path,
    monkeypatch: pytest.MonkeyPatch,
    user: str | None,
    paper: str | None,
    shell: str | None,
    off: str,
) -> None:
    """With `qdrant_url` set and the extra installed, `library = "off"`
    reads each work's PDF (the plain tier): the shell's for one run over
    the paper's `[sources]` over the user file's, and says where it was
    set."""
    from axiomation.sources.config import library_off

    write_user_file(
        user_file,
        qdrant_url="http://localhost:6335",
        **({"library": user} if user else {}),
    )
    if shell:
        monkeypatch.setenv("AXIOMATION_LIBRARY", shell)
    root = write_paper(tmp_path, sources=f'library = "{paper}"\n' if paper else "")

    reason = library_off(resolve(load_settings(root)))
    if off:
        assert reason == off
    else:
        # on: the library reads, unless the extra is missing (the core's run)
        assert not reason.startswith("library =")


def test_a_library_switch_that_says_neither_on_nor_off_is_refused(
    tmp_path: Path, user_file: Path
) -> None:
    root = write_paper(tmp_path, sources='library = "no"\n')

    with pytest.raises(SourcesError, match=r'\[sources\]\.library is "on" or "off"'):
        resolve(load_settings(root))


def test_with_the_library_off_each_work_is_read_from_its_pdf(
    tmp_path: Path, user_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The sources a run reads are the plain tier's: no search to fail."""
    from axiomation import evidence

    write_user_file(user_file, qdrant_url="http://localhost:6335")
    monkeypatch.setenv("AXIOMATION_LIBRARY", "off")
    sources = evidence.sources_for(write_paper(tmp_path))

    assert [type(p).__name__ for p in sources.providers] == ["Pdfs", "Transient"]
    assert evidence.search_failure(sources) is None
