"""A person's settings: the user file in their axiomation directory, the
shell's `AXIOMATION_<KEY>` over it; refused where it holds an unknown key,
a value that is no string, or a secret others may read."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from axiomation import keys, user_settings
from axiomation.user_settings import (
    UserKey,
    UserOrigin,
    UserSettingsError,
    UserValue,
)
from user_files import write_user_file


def test_the_directory_is_the_user_s_configuration_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert user_settings.directory({"XDG_CONFIG_HOME": str(tmp_path)}) == (
        tmp_path / "axiomation"
    )
    monkeypatch.setenv("HOME", str(tmp_path))
    assert user_settings.path({}) == tmp_path / ".config/axiomation/config.toml"


def test_the_keys_live_beside_the_user_file(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(keys.ENV)
    assert keys.directory() == user_settings.directory() / "keys"


def test_each_key_s_shell_name_is_one_rule() -> None:
    assert UserKey.OCR_URL.env == "AXIOMATION_OCR_URL"
    assert UserKey.CONTACT_EMAIL.env == "AXIOMATION_CONTACT_EMAIL"
    assert {k for k in UserKey if k.secret} == {
        UserKey.VOYAGE_API_KEY,
        UserKey.OCR_API_KEY,
        UserKey.CORE_API_KEY,
    }


def test_no_file_and_no_shell_gives_the_defaults(user_file: Path) -> None:
    assert not user_file.exists()
    person = user_settings.load()
    assert person.file == user_file
    assert all(person.get(k) == UserValue() for k in UserKey)


def test_the_shell_speaks_over_the_file_and_a_blank_is_unset(
    user_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_user_file(user_file, ocr_url=" http://file/ocr ", zotero_url="http://z:1")
    monkeypatch.setenv("AXIOMATION_OCR_URL", "http://shell/ocr")
    monkeypatch.setenv("AXIOMATION_ZOTERO_URL", "  ")
    person = user_settings.load()

    assert person.get(UserKey.OCR_URL) == UserValue(
        "http://shell/ocr", UserOrigin.SHELL
    )
    assert person.get(UserKey.ZOTERO_URL) == UserValue("http://z:1", UserOrigin.USER)
    assert not person.get(UserKey.QDRANT_URL)


def test_only_axiomation_s_names_are_kept_and_nothing_is_printed(
    user_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The person's values hold their keys: another tool's variables are not
    kept, and a printed `UserSettings` shows neither."""
    write_user_file(user_file, voyage_api_key="file-secret")
    monkeypatch.setenv("AXIOMATION_OCR_API_KEY", "shell-secret")
    monkeypatch.setenv("OTHER_TOOL_TOKEN", "other-secret")
    person = user_settings.load()

    assert set(person.environ) == {"AXIOMATION_OCR_API_KEY"}
    assert not {"file-secret", "shell-secret", "other-secret"} & set(
        repr(person).split("'")
    )
    assert "secret" not in repr(person)


def test_an_unknown_key_is_refused_naming_the_known(user_file: Path) -> None:
    """A table (`[sources]`) or a paper's key (`collection`) is not the
    person's: the user file is flat."""
    for text in ('collection = "c"\n', '[sources]\nqdrant_url = "q"\n'):
        user_file.parent.mkdir(parents=True, exist_ok=True)
        user_file.write_text(text)
        with pytest.raises(UserSettingsError, match="no key of the user file"):
            user_settings.load()


def test_a_value_that_is_no_string_is_refused(user_file: Path) -> None:
    user_file.parent.mkdir(parents=True, exist_ok=True)
    user_file.write_text("zotero_url = 23119\n")
    with pytest.raises(UserSettingsError, match="zotero_url must be a string"):
        user_settings.load()


def test_the_budget_is_the_one_key_that_is_a_number(user_file: Path) -> None:
    write_user_file(user_file, run_dollars_max=12.5)
    assert user_settings.load().get(UserKey.RUN_DOLLARS_MAX) == UserValue(
        "12.5", UserOrigin.USER
    )
    for text in ('run_dollars_max = "12"\n', "run_dollars_max = 0\n"):
        user_file.write_text(text)
        with pytest.raises(UserSettingsError, match="is dollars above 0"):
            user_settings.load()


def test_a_paper_may_set_the_budget_but_no_fact_of_a_machine() -> None:
    assert {k for k in UserKey if k.paper_may_set} == {UserKey.RUN_DOLLARS_MAX}


@pytest.mark.skipif(os.name != "posix", reason="POSIX permissions")
def test_a_secret_others_may_read_is_refused(user_file: Path) -> None:
    """As `ssh` refuses a private key others can read; a file with no
    secret may be read by anyone."""
    write_user_file(user_file, voyage_api_key="secret")
    user_file.chmod(0o644)
    with pytest.raises(UserSettingsError, match=f"chmod 600 {user_file}"):
        user_settings.load()
    write_user_file(user_file, qdrant_url="http://q:1")
    user_file.chmod(0o644)
    assert user_settings.get(UserKey.QDRANT_URL).value == "http://q:1"


def test_a_file_that_does_not_read_is_refused_naming_it(user_file: Path) -> None:
    user_file.parent.mkdir(parents=True, exist_ok=True)
    user_file.write_text("ocr_url = \n")
    with pytest.raises(UserSettingsError, match=f"{user_file}: cannot read it"):
        user_settings.load()
