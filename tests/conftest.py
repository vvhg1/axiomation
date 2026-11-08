"""Fixtures shared across the suite."""

from __future__ import annotations

import os
import random
import shutil
import tempfile
from pathlib import Path

import pytest

from axiomation import develop, keys, records, user_settings, versions
from axiomation.derived import CACHE_VARIABLE
from axiomation.runner.claude_code import TMP


@pytest.fixture
def host_scratch():
    """A scratch space for a host's directories, under /tmp: the Claude
    Code host refuses one under the per-user temporary directories, where
    pytest's own lie."""
    base = Path(tempfile.mkdtemp(prefix="axiomation-test-", dir=TMP))
    yield base
    shutil.rmtree(base, ignore_errors=True)


@pytest.fixture
def outside_tmp():
    """A directory outside /tmp, for a store a session must not write: on
    Linux pytest's own folders lie under /tmp, which the Claude Code host
    opens to every session and refuses a store in."""
    base = Path(tempfile.mkdtemp(prefix="axiomation-test-", dir="/var/tmp"))
    yield base
    shutil.rmtree(base, ignore_errors=True)


@pytest.fixture(autouse=True)
def derived_cache(tmp_path_factory, monkeypatch):
    """The user's cache of derived values, a test's own: no test reads or
    writes the user's, and none meets another's values."""
    directory = tmp_path_factory.mktemp("axiomation-cache")
    monkeypatch.setenv(CACHE_VARIABLE, str(directory))
    return directory


@pytest.fixture(autouse=True)
def user_file(tmp_path_factory, monkeypatch) -> Path:
    """The person's user file, a test's own and absent until the test
    writes it, and no person's value in the shell: no test reads the
    user's (`user_settings.py`)."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path_factory.mktemp("config")))
    for key in user_settings.UserKey:
        monkeypatch.delenv(key.env, raising=False)
    return user_settings.path()


@pytest.fixture(autouse=True)
def _develop_off(monkeypatch):
    """Develop mode off, as on an author's machine, whatever the shell
    running the suite sets: the product's own paths never read an
    answer's text again."""
    monkeypatch.delenv(develop.ENV, raising=False)


@pytest.fixture
def develop_mode(_develop_off, monkeypatch):
    """Develop mode on, as a developer runs it, for a test of a develop tool (a
    replay, `update --cut`, `review --file-answer`) or one that reads an
    answer's text as evidence: develop mode keeps the texts."""
    monkeypatch.setenv(develop.ENV, "1")


@pytest.fixture(autouse=True)
def node_ids(monkeypatch):
    """Node ids drawn from a seed, so a test reads the same ids each run;
    calling the fixture's value starts the draws again, for a test that
    holds two stores built alike to the same ids."""

    def again() -> None:
        monkeypatch.setattr(versions, "_draw", random.Random(0).choice)

    again()
    return again


@pytest.fixture(scope="session")
def _person_keys(tmp_path_factory) -> Path:
    directory = tmp_path_factory.mktemp("keys")
    keys.made(directory)
    return directory


@pytest.fixture(autouse=True)
def person_keys(_person_keys, monkeypatch) -> Path:
    """The test person's keys, made once for the session: every store the
    suite writes is sealed to them (`docs/architecture.md` § 4)."""
    monkeypatch.setenv(keys.ENV, str(_person_keys))
    return _person_keys


@pytest.fixture(autouse=True)
def confirmed_word(request, monkeypatch):
    """Every entry of every record stands confirmed, as if the test person
    signed after each: a test of filings reads them in effect at once. A
    test marked `signed` meets the person's word as the product does
    (`docs/architecture.md` § 4)."""
    if request.node.get_closest_marker("signed"):
        return
    monkeypatch.setattr(
        records._Loaded,
        "standing",
        lambda self: records.Confirmed(frozenset(self.order)),
    )


@pytest.fixture(autouse=True)
def claude_config(request, tmp_path_factory, monkeypatch):
    """Claude Code's settings directory, empty: no offline test reads the
    user's `~/.claude` (its effort level). A `live` test keeps the user's,
    where Claude Code keeps its login."""
    if request.node.get_closest_marker("live"):
        return None
    directory = tmp_path_factory.mktemp("claude-config")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(directory))
    return directory


STUB = """#!/bin/sh
echo "an offline test reached the real $(basename "$0"): put a fake on PATH" >&2
exit 97
"""


@pytest.fixture(autouse=True)
def no_real_host(request, tmp_path_factory, monkeypatch):
    """`claude` and `opencode` first on PATH as stubs that refuse, so an
    offline test that forgets its fake never reaches a real model. A test that installs
    a fake puts it before these; a `live` test keeps the real ones."""
    if request.node.get_closest_marker("live"):
        return
    directory = tmp_path_factory.mktemp("no-real-host")
    for name in ("claude", "opencode"):
        stub = directory / name
        stub.write_text(STUB, encoding="utf-8")
        stub.chmod(0o755)
    monkeypatch.setenv("PATH", f"{directory}{os.pathsep}{os.environ['PATH']}")


@pytest.fixture(autouse=True)
def no_real_voyage(request, monkeypatch):
    """Voyage's client refuses to embed: an offline test that forgets its
    fake (`sources status` tries the key) never reaches the service. A test
    fakes the client or the reader's `embed_query`; a `live` test keeps it."""
    if request.node.get_closest_marker("live"):
        return
    try:
        import voyageai
    except ImportError:
        return

    def refuse(*args, **kwargs):
        raise AssertionError("an offline test reached Voyage: fake its client")

    for name in ("embed", "contextualized_embed"):
        monkeypatch.setattr(voyageai.Client, name, refuse)
