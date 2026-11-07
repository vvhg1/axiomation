"""Resolving the `[sources]` section: what the module will use.

A project names as much of the corpus as it wants to, in the paper's
`[sources]` or the workspace root's. What it leaves out this module
fills in, and the line between two kinds of fact decides how:

- **The corpus's identity is configuration.** Which collection a paper reads,
  under which model and how many dimensions, is the same kind of statement
  as which bibliography it cites. A paper that declares none of it gets the
  default model and a collection named by the rule in `contract.py`, from the
  model, the dimensions and the bibliography — so every paper in a workspace
  that shares one bibliography lands on one corpus with nothing declared.
- **Where a service runs, the keys, the OCR engine and who asks are the
  person's.** They are read from the user file, the shell over it
  (`user_settings.py`): the Qdrant URL, the OCR backend and its address,
  Zotero's address here; the Voyage, OCR and CORE keys through `api_key`,
  `ocr_api_key` and `core_api_key`, and the lookups' address through
  `contact`. A paper's file that declares one is refused
  (`settings.refuse_personal`).

`ResolvedSources.origins` records where each value came from, because "which
collection am I talking to and why" is the first question every source
failure raises.

The paper's `[sources]` is read here. The files are found
and read by the core's `settings.py`, which reads `[computations]`
too. It holds `Settings` (the paper's root, its workspace's, its
bibliography, its `[sources]`, the store's cache and the person's
settings), `load_settings`, the `[sources]` table read and merged,
`OcrBackend` and `SettingFrom` (named apart from `evidence.Origin`). A
paper with no `axiomation.toml` still resolves: its bibliography the one
`.bib` beside it.

Standard library only: resolving the settings must not import Qdrant.
"""

from __future__ import annotations

import importlib.util
from collections.abc import Mapping
from dataclasses import dataclass, field, fields, replace
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any

from axiomation import settings, user_settings, versions
from axiomation.sources.contract import collection_name
from axiomation.user_settings import UserKey, UserOrigin, UserSettings

CONFIG_NAME = settings.CONFIG_NAME

#: The model and dimension a corpus is built with when nobody names them. The
#: pair has to be one the API accepts (`voyage-3-lite` takes 512 and nothing
#: else), and it should be the pair a corpus worth keeping is built with.
#: `voyage-context-4` at 2048 embeds each page with its neighbours as
#: context, which is what keeps a sentence across a page break retrievable
#: once a chunk is one page. With the
#: naming rule, the default is what makes an undeclared paper land on
#: `axiomation_<bib>_voyage-context-4_2048`.
DEFAULT_VOYAGE_MODEL = "voyage-context-4"
DEFAULT_VOYAGE_DIMS = 2048
#: Where Zotero's connector listens when nobody says otherwise.
DEFAULT_ZOTERO_URL = user_settings.DEFAULTS[UserKey.ZOTERO_URL]


class SourcesError(Exception):
    """The source corpus cannot be reached, or is not what it claims."""


class OcrBackend(StrEnum):
    """The OCR backend a source collection was built with.

    `tesseract` runs in this process. The others send a page to the
    service at `ocr_url`, and the name is recorded on the chunk, so each
    page says which engine read it: `paddle` PaddleOCR-VL as PaddleX serves
    it, `mineru` MinerU as its API server serves it, `http` any service that
    answers the contract (`docs/ocr.md`), `chat` a vision model behind an
    OpenAI-compatible chat-completions endpoint, `ocr_model`. Every
    member here must also be a `TextSource` member, because the value goes
    on the wire.
    """

    TESSERACT = "tesseract"
    PADDLE = "paddle"
    MINERU = "mineru"
    HTTP = "http"
    CHAT = "chat"


class SettingFrom(StrEnum):
    """Where a resolved `[sources]` value came from: `PAPER`
    and `WORKSPACE` for a declared field, `SHELL` and `USER` for a
    person's value (`user_settings.py`), `DERIVED` for a collection named
    by the naming rule, `DEFAULT` for the rest."""

    PAPER = "paper"
    WORKSPACE = "workspace"
    SHELL = "shell"
    USER = "user"
    DERIVED = "derived"
    DEFAULT = "default"


#: A person's value's origin as a `[sources]` value's.
_FROM_USER = {
    UserOrigin.SHELL: SettingFrom.SHELL,
    UserOrigin.USER: SettingFrom.USER,
    UserOrigin.DEFAULT: SettingFrom.DEFAULT,
}


@dataclass(frozen=True, slots=True)
class SourcesConfig:
    """The `[sources]` a paper and its workspace declare, the paper's fields
    over the workspace's; an empty value leaves the choice open. `origins`
    names the file each declared field came from."""

    collection: str = ""
    voyage_model: str = ""
    voyage_dims: int = 0
    bib: Path | None = None
    origins: Mapping[str, SettingFrom] = field(
        default=MappingProxyType({}), compare=False
    )

    def origin(self, name: str) -> SettingFrom:
        return self.origins.get(name, SettingFrom.DEFAULT)


@dataclass(frozen=True, slots=True)
class Settings:
    """What this package reads of a paper: its directory, its workspace's (None
    outside one), its bibliography, its declared `[sources]`, where the
    caches go, the paper's store's `cache/sources/`, and the person's
    settings, read when the paper's are."""

    root: Path
    workspace_root: Path | None = None
    bib: Path | None = None
    sources: SourcesConfig = SourcesConfig()
    cache_dir: Path | None = None
    #: holds the person's keys: never printed
    user: UserSettings = field(kw_only=True, repr=False)


SOURCES_KEYS = ("collection", "voyage_model", "voyage_dims", "bib")


def _sources_section(data: Mapping[str, Any], path: Path) -> SourcesConfig:
    """One file's `[sources]`, each declared field's origin the file; paths
    resolve against the file's directory."""
    table = data.get("sources", {})
    if not isinstance(table, dict):
        raise SourcesError(f"{path}: [sources] must be a table")
    for key in table:
        if key not in SOURCES_KEYS:
            known = ", ".join(SOURCES_KEYS)
            raise SourcesError(f"{path}: [sources].{key} is unknown (known: {known})")
    origin = SettingFrom.WORKSPACE if "workspace" in data else SettingFrom.PAPER
    values: dict[str, Any] = {}
    for key in ("collection", "voyage_model"):
        if key in table:
            if not isinstance(table[key], str):
                raise SourcesError(f"{path}: [sources].{key} must be a string")
            values[key] = table[key].strip()
    if "voyage_dims" in table:
        dims = table["voyage_dims"]
        if not isinstance(dims, int) or isinstance(dims, bool) or dims < 0:
            raise SourcesError(f"{path}: [sources].voyage_dims must be a count")
        values["voyage_dims"] = dims
    if "bib" in table:
        values["bib"] = path.parent / str(table["bib"])
    section = SourcesConfig(**values)
    declared = {name: origin for name in values if getattr(section, name)}
    return replace(section, origins=MappingProxyType(declared))


def _merged(paper: SourcesConfig, shared: SourcesConfig) -> SourcesConfig:
    """The paper's fields over the workspace's, field by field."""
    values: dict[str, Any] = {}
    origins: dict[str, SettingFrom] = {}
    for name in (item.name for item in fields(SourcesConfig)):
        if name == "origins":
            continue
        chosen = (
            paper
            if name in paper.origins
            else shared
            if name in shared.origins
            else None
        )
        if chosen is None:
            values[name] = getattr(paper, name)
            continue
        values[name] = getattr(chosen, name)
        origins[name] = chosen.origins[name]
    return SourcesConfig(**values, origins=MappingProxyType(origins))


def load_settings(paper: Path, store: Path | None = None) -> Settings:
    """A paper's settings: its `axiomation.toml`'s `[sources]` over its
    workspace's, its bibliography the one declared (`[sources].bib`, then
    `[paper].bib`), else the only `.bib` beside it; the caches under
    `store` (default: the paper's own store)."""
    try:
        read = settings.load(paper)
        user = user_settings.load()
    except settings.SettingsError as error:
        raise SourcesError(str(error)) from None
    paper, data, workspace_root = read.paper, read.own, read.workspace_root
    declared = _sources_section(data, paper / CONFIG_NAME)
    if workspace_root is not None:
        declared = _merged(
            declared, _sources_section(read.shared, workspace_root / CONFIG_NAME)
        )
    paper_table = data.get("paper", {})
    bib = (
        paper / str(paper_table["bib"])
        if isinstance(paper_table, dict) and "bib" in paper_table
        else None
    )
    if bib is None:
        beside = sorted(paper.glob("*.bib"))
        bib = beside[0] if len(beside) == 1 else None
    store = versions.store_of(paper) if store is None else store
    return Settings(
        paper, workspace_root, bib, declared, store / "cache" / "sources", user=user
    )


@dataclass(frozen=True, slots=True)
class ResolvedSources:
    """What the sources module will actually use, and where it came from."""

    qdrant_url: str
    collection: str
    voyage_model: str
    voyage_dims: int
    bib: Path | None
    ocr_backend: OcrBackend
    #: Where the OCR provider posts a page: the service's endpoint, its path
    #: included. Empty until the person sets `ocr_url`: no service has an
    #: address of its own, and `tesseract` needs none.
    ocr_url: str = ""
    #: The model a `chat` service is asked to read with, by the name the
    #: service gives it. Empty until the person sets `ocr_model`.
    ocr_model: str = ""
    zotero_url: str = DEFAULT_ZOTERO_URL
    cache_dir: Path | None = None
    #: Where a work's PDF is found by its key (`<pdf_dir>/<key>.pdf`) when
    #: its entry names none on this machine; None where the person set none.
    pdf_dir: Path | None = None
    #: One entry per field above, naming where its value came from.
    origins: Mapping[str, SettingFrom] = MappingProxyType({})

    def origin(self, name: str) -> SettingFrom:
        return self.origins.get(name, SettingFrom.DEFAULT)

    def as_dict(self) -> dict[str, object]:
        return {
            "qdrant_url": self.qdrant_url,
            "collection": self.collection,
            "voyage_model": self.voyage_model,
            "voyage_dims": self.voyage_dims,
            "bib": str(self.bib) if self.bib else None,
            "ocr_backend": self.ocr_backend.value,
            "ocr_url": self.ocr_url,
            "ocr_model": self.ocr_model,
            "zotero_url": self.zotero_url,
            "cache_dir": str(self.cache_dir) if self.cache_dir else None,
            "pdf_dir": str(self.pdf_dir) if self.pdf_dir else None,
            "origins": {name: origin.value for name, origin in self.origins.items()},
        }


def resolve(config: Settings) -> ResolvedSources:
    """Merge the paper's `[sources]`, the person's values and the defaults.

    The declared table already has the paper's fields over the workspace
    root's (`config.py`), so only what nobody declared is filled in here.
    """
    declared = config.sources
    origins = dict(declared.origins)

    def declared_or(name: str, fallback: object, origin: SettingFrom) -> object:
        value = getattr(declared, name)
        if value:
            return value
        origins[name] = origin
        return fallback

    def personal(key: UserKey, default: str) -> str:
        found = config.user.get(key)
        origins[key.value] = _FROM_USER[found.origin]
        return found.value or default

    qdrant_url = personal(UserKey.QDRANT_URL, "")
    voyage_model = declared_or(
        "voyage_model", DEFAULT_VOYAGE_MODEL, SettingFrom.DEFAULT
    )
    voyage_dims = declared_or("voyage_dims", DEFAULT_VOYAGE_DIMS, SettingFrom.DEFAULT)
    bib = declared.bib
    if bib is None:
        bib = config.bib
        origins["bib"] = SettingFrom.PAPER if bib is not None else SettingFrom.DEFAULT
    collection = declared_or(
        "collection",
        collection_name(voyage_model, voyage_dims, bib),
        SettingFrom.DERIVED,
    )
    ocr_backend = ocr_backend_of(config.user)
    origins["ocr_backend"] = _FROM_USER[config.user.get(UserKey.OCR_BACKEND).origin]
    ocr_url = personal(UserKey.OCR_URL, "")
    ocr_model = personal(UserKey.OCR_MODEL, "")
    zotero_url = personal(UserKey.ZOTERO_URL, DEFAULT_ZOTERO_URL)
    pdf_dir = _pdf_dir(config.user, personal(UserKey.PDF_DIR, ""))

    return ResolvedSources(
        qdrant_url=qdrant_url,
        collection=str(collection),
        voyage_model=str(voyage_model),
        voyage_dims=int(voyage_dims),
        bib=bib,
        ocr_backend=ocr_backend,
        ocr_url=ocr_url,
        ocr_model=ocr_model,
        zotero_url=zotero_url,
        cache_dir=config.cache_dir,
        pdf_dir=pdf_dir,
        origins=MappingProxyType(origins),
    )


def _pdf_dir(user: UserSettings, value: str) -> Path | None:
    """The person's directory of PDFs, `~` expanded; refused, naming where
    it was set, where it is not absolute: a relative path would name
    another directory in each paper."""
    if not value:
        return None
    path = Path(value).expanduser()
    if not path.is_absolute():
        where = _where(user, UserKey.PDF_DIR)
        raise SourcesError(
            f"pdf_dir, set in {where}, is {value!r}: it must be an absolute path"
        )
    return path


#: The libraries the `[library]` extra brings (`sources.library`).
LIBRARY_MODULES = ("qdrant_client", "voyageai", "fastembed", "pytesseract")


def library_on(settings: ResolvedSources) -> bool:
    """Whether the cited works are read from the library (the full tier):
    the person set `qdrant_url`, and the `[library]` extra is installed.
    Else each work's PDF is read when asked (the plain tier), and a fetched
    copy with no OCR."""
    return not library_off(settings)


def library_off(settings: ResolvedSources) -> str:
    """Why the library is not read, empty where it is: no `qdrant_url`
    set, or the `[library]` extra not installed."""
    installed = all(importlib.util.find_spec(name) for name in LIBRARY_MODULES)
    if not settings.qdrant_url:
        return "no qdrant_url is set" + (
            "" if installed else ", and the `[library]` extra is not installed"
        )
    if not installed:
        return "qdrant_url is set, and the `[library]` extra is not installed"
    return ""


def ocr_backend_of(user: UserSettings) -> OcrBackend:
    """The person's OCR backend, Tesseract where none is set; refused,
    naming where it was set, where it names none."""
    found = user.get(UserKey.OCR_BACKEND)
    if not found:
        return OcrBackend.TESSERACT
    try:
        return OcrBackend(found.value)
    except ValueError:
        where = (
            UserKey.OCR_BACKEND.env
            if found.origin is UserOrigin.SHELL
            else f"{UserKey.OCR_BACKEND.value} in {user.file}"
        )
        allowed = ", ".join(b.value for b in OcrBackend)
        raise SourcesError(
            f"{where} is {found.value!r}: it must be one of {allowed}"
        ) from None


def _where(user: UserSettings, key: UserKey) -> str:
    """Where a person's value was read: the shell or the user file."""
    found = user.get(key)
    if found.origin is UserOrigin.SHELL:
        return f"{key.env} in the shell"
    return str(user.file) if found else ""


def api_key(config: Settings) -> str:
    """The Voyage key, the person's; refused, naming where it goes, where
    none is set. Secrets never enter `ResolvedSources`."""
    found = config.user.get(UserKey.VOYAGE_API_KEY)
    if found:
        return found.value
    raise SourcesError(
        f"no Voyage key: {user_settings.where_to_put(UserKey.VOYAGE_API_KEY)}"
    )


def optional_api_key(config: Settings) -> str:
    """The Voyage key, or an empty string where none is set: for a read
    that needs it only to search."""
    return config.user.get(UserKey.VOYAGE_API_KEY).value


def api_key_source(config: Settings) -> str:
    """Where the Voyage key was found: the shell or the user file; empty
    for nowhere."""
    return _where(config.user, UserKey.VOYAGE_API_KEY)


def ocr_api_key(config: Settings) -> str:
    """The optional bearer token for a remote OCR service, or an empty string.
    Secrets never enter `ResolvedSources`, which is why this is not a field
    beside the address."""
    return config.user.get(UserKey.OCR_API_KEY).value


@dataclass(frozen=True, slots=True)
class Contact:
    """The address the lookups are asked with, and where it was read;
    both empty where none is set."""

    address: str = ""
    source: str = ""


def contact(config: Settings) -> Contact:
    """The address Crossref, OpenAlex and Unpaywall are asked with: the
    person's `contact_email`."""
    found = config.user.get(UserKey.CONTACT_EMAIL)
    if not found:
        return Contact()
    return Contact(found.value, _where(config.user, UserKey.CONTACT_EMAIL))


def no_contact(config: Settings) -> str:
    """What a lookup without a contact address loses, and where to set
    it."""
    return (
        "Unpaywall is skipped, and Crossref and OpenAlex are asked outside "
        "their polite pools, where they throttle: "
        + user_settings.where_to_put(UserKey.CONTACT_EMAIL)
    )


def core_api_key(config: Settings) -> str:
    """The optional CORE key, or an empty string when CORE is disabled."""
    return config.user.get(UserKey.CORE_API_KEY).value
