"""The settings files' templates: every setting the code reads, commented
out and valid once uncommented; none sets a budget; `init` writes them
and never overwrites."""

from __future__ import annotations

import os
import re
import stat
import tomllib
from pathlib import Path

import pytest

from axiomation import computations, settings, settings_templates, user_settings
from axiomation.check import DEFAULTS, Gate
from axiomation.check import modes as gate_modes
from axiomation.cli import main
from axiomation.settings_templates import Template
from axiomation.sources import config as sources_config

#: A commented-out setting: `# key = value`.
SETTING = re.compile(r"^# ([A-Za-z0-9_-]+) = (.+)$")
HEADER = re.compile(r"^\[([a-z.]+)\]$")
#: Gates the templates leave out, each with its reason: `reviews` lists
#: an outside review's findings, which the public docs do not describe.
LEFT_OUT_GATES = {Gate.REVIEWS}


def commented(text: str) -> dict[str, set[str]]:
    """Each section's commented-out keys; the user file's under ''."""
    found: dict[str, set[str]] = {"": set()}
    section = ""
    for line in text.splitlines():
        if header := HEADER.match(line):
            section = header.group(1)
            found.setdefault(section, set())
        elif setting := SETTING.match(line):
            assert setting.group(1) not in found[section], line
            found[section].add(setting.group(1))
    return found


def uncommented(text: str) -> str:
    """The template with every setting's line uncommented."""
    return "\n".join(
        line[2:] if SETTING.match(line) else line for line in text.splitlines()
    )


def empty(tables: dict) -> bool:
    return all(isinstance(v, dict) and empty(v) for v in tables.values())


@pytest.mark.parametrize("kind", list(Template))
def test_a_template_sets_nothing(kind):
    assert empty(tomllib.loads(settings_templates.text(kind)))


def test_the_user_template_names_every_user_setting_once():
    assert commented(settings_templates.text(Template.USER)) == {
        "": {k.value for k in user_settings.UserKey}
    }


def test_the_paper_template_names_every_paper_setting_once():
    assert commented(settings_templates.text(Template.PAPER)) == {
        "": set(),
        "sources": set(sources_config.SOURCES_KEYS),
        "gates": {g.value for g in Gate} - {g.value for g in LEFT_OUT_GATES},
        settings.BUDGET_SECTION: {settings.RUN_DOLLARS_MAX},
        computations.SECTION: set(computations.KEYS) - {"runners"},
        f"{computations.SECTION}.runners": {
            s.removeprefix(".") for s in computations.CODE_SUFFIXES
        },
    }


def test_the_workspace_template_is_the_paper_s_with_its_table():
    found = commented(settings_templates.text(Template.WORKSPACE))
    assert found.pop("workspace") == {"papers"}
    assert found == commented(settings_templates.text(Template.PAPER))


def test_each_gate_s_example_is_its_default():
    text = uncommented(settings_templates.text(Template.PAPER))
    for gate, mode in tomllib.loads(text)["gates"].items():
        assert DEFAULTS[Gate(gate)].value == mode


def test_the_user_template_uncommented_reads(tmp_path):
    file = tmp_path / "config.toml"
    file.write_text(uncommented(settings_templates.text(Template.USER)))
    file.chmod(0o600)
    held = user_settings.read_file(file)
    assert set(held) == {k.value for k in user_settings.UserKey}


def test_a_workspace_and_a_paper_uncommented_read(tmp_path):
    paper = tmp_path / "papers" / "p"
    paper.mkdir(parents=True)
    (paper / "references.bib").write_text("")
    (tmp_path / settings.CONFIG_NAME).write_text(
        uncommented(settings_templates.text(Template.WORKSPACE))
    )
    (paper / settings.CONFIG_NAME).write_text(
        uncommented(settings_templates.text(Template.PAPER))
    )
    read = settings.load(paper)
    assert read.workspace_root == tmp_path.resolve()
    assert settings.run_dollars_max(read)[0] == 10
    assert computations.load(paper).runners["R"] == "Rscript"
    assert gate_modes(paper)[Gate.UNSUPPORTED_CLAIMS].value == "block"
    for data, path in ((read.own, paper), (read.shared, tmp_path)):
        sources_config._sources_section(data, path / settings.CONFIG_NAME)


def test_no_shipped_file_sets_a_budget():
    package = Path(settings_templates.__file__).parents[1]
    examples = Path(__file__).parents[1] / "examples"
    shipped = [*package.rglob("*.toml"), *examples.rglob("*.toml")]
    assert shipped
    for file in shipped:
        for line in file.read_text(encoding="utf-8").splitlines():
            assert not re.match(r"\s*run_dollars_max\s*=", line), file


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_init_user_writes_the_user_file_for_its_owner_alone(home, capsys):
    assert main(["init", "user"]) == 0
    written = user_settings.path()
    assert written.read_text() == settings_templates.text(Template.USER)
    if os.name == "posix":
        assert stat.S_IMODE(written.stat().st_mode) == 0o600
    assert str(written) in capsys.readouterr().out


def test_init_leaves_an_existing_file_as_it_is(home, capsys):
    file = home / settings.CONFIG_NAME
    file.write_text("[gates]\n")
    assert main(["init", "paper"]) == 1
    assert file.read_text() == "[gates]\n"
    assert "exists" in capsys.readouterr().err


def test_init_workspace_makes_the_directory_a_workspace(home):
    (home / "papers" / "a").mkdir(parents=True)
    assert main(["init", "workspace"]) == 0
    assert main(["init", "paper", "papers/a"]) == 0
    held = settings.members(home / "papers" / "a")
    assert held is not None and held[0] == home.resolve()


def test_init_user_takes_no_directory(home, capsys):
    (home / "elsewhere").mkdir()
    assert main(["init", "user", "elsewhere"]) == 1
    assert "own place" in capsys.readouterr().err
    assert not user_settings.path().exists()
