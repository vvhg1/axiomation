"""A paper's settings: its `axiomation.toml` and its
workspace's, read by the core, each section the paper's over the
workspace's; `[computations]` checked, its directories never leaving the
workspace (the paper, where there is none). Synthetic directories only."""

from __future__ import annotations

from pathlib import Path

import pytest

from axiomation import computations, settings
from axiomation.settings import CONFIG_NAME, Origin, SettingsError
from user_files import write_user_file


def paper_in_workspace(tmp_path: Path, paper_toml: str = "", shared: str = "") -> Path:
    root = tmp_path / "workspace"
    paper = root / "papers" / "one"
    paper.mkdir(parents=True)
    (root / CONFIG_NAME).write_text('[workspace]\npapers = "papers/*"\n' + shared)
    if paper_toml:
        (paper / CONFIG_NAME).write_text(paper_toml)
    return paper


def test_a_paper_without_settings_reads_empty(tmp_path) -> None:
    read = settings.load(tmp_path)
    assert (
        read.own == {}
        and read.workspace_root is None
        and read.scope == tmp_path.resolve()
    )
    found = computations.load(tmp_path)
    assert found.directories == (tmp_path.resolve(),)
    assert found.run_timeout_seconds == computations.DEFAULT_RUN_TIMEOUT_SECONDS
    assert found.runners == {}


def test_the_workspace_is_found_and_the_paper_s_keys_win(tmp_path) -> None:
    paper = paper_in_workspace(
        tmp_path,
        "[computations]\nrun_timeout_seconds = 30\n"
        "[computations.runners]\npy = 'python3'\n",
        "[computations]\ndirectories = ['checks']\nrun_timeout_seconds = 90\n"
        "[computations.runners]\nR = 'Rscript'\n",
    )
    read = settings.load(paper)
    assert read.workspace_root == (tmp_path / "workspace").resolve()
    found = computations.load(paper)
    assert found.directories == ((paper / "checks").resolve(),)
    assert found.run_timeout_seconds == 30
    assert found.runners == {"py": "python3", "R": "Rscript"}


def test_a_directory_outside_the_workspace_is_refused(tmp_path) -> None:
    paper = paper_in_workspace(
        tmp_path, "[computations]\ndirectories = ['../../../x']\n"
    )
    with pytest.raises(SettingsError, match="leaves the workspace"):
        computations.load(paper)


def test_a_directory_outside_the_paper_is_refused_without_a_workspace(tmp_path) -> None:
    paper = tmp_path / "paper"
    paper.mkdir()
    (paper / CONFIG_NAME).write_text("[computations]\ndirectories = ['../shared']\n")
    with pytest.raises(SettingsError, match="leaves the paper"):
        computations.load(paper)


def test_a_directory_in_the_workspace_outside_the_paper_is_taken(tmp_path) -> None:
    paper = paper_in_workspace(
        tmp_path, "[computations]\ndirectories = ['../../shared']\n"
    )
    assert computations.load(paper).directories == (
        (tmp_path / "workspace" / "shared").resolve(),
    )


@pytest.mark.parametrize(
    ("table", "problem"),
    [
        ("directory = ['checks']", "[computations].directory is unknown"),
        ("directories = 'checks'", "must be a list of paths"),
        ("run_timeout_seconds = 0", "above 0"),
        ("[computations.runners]\nipynb = 'jupyter'", "names no code extension"),
        ("[computations.runners]\npy = ''", "must be a command"),
    ],
)
def test_a_malformed_section_is_refused_naming_its_file(
    tmp_path, table, problem
) -> None:
    (tmp_path / CONFIG_NAME).write_text(f"[computations]\n{table}\n")
    with pytest.raises(
        SettingsError, match=problem.replace("[", r"\[").replace("]", r"\]")
    ) as error:
        computations.load(tmp_path)
    assert str(tmp_path / CONFIG_NAME) in str(error.value)


def test_an_unreadable_file_is_refused(tmp_path) -> None:
    (tmp_path / CONFIG_NAME).write_text("[computations\n")
    with pytest.raises(SettingsError, match="cannot read it"):
        settings.load(tmp_path)


def test_an_empty_list_declares_no_directory(tmp_path) -> None:
    """The paper's top level is its own whatever the directories say."""
    (tmp_path / CONFIG_NAME).write_text("[computations]\ndirectories = []\n")
    (tmp_path / "run.py").write_text("print(1)\n")
    (tmp_path / "checks").mkdir()
    (tmp_path / "checks" / "fit.py").write_text("print(2)\n")
    assert list(computations.code_files(tmp_path)) == ["run.py"]


def test_each_value_names_where_it_is_in_force_from(tmp_path) -> None:
    paper = paper_in_workspace(
        tmp_path,
        "[computations.runners]\npy = 'python3'\n[gates]\ndefects = 'warn'\n",
        "[computations]\nrun_timeout_seconds = 90\n[sources]\nbib = 'refs.bib'\n",
    )
    read = settings.load(paper)
    Origin = settings.Origin
    assert settings.origin(read, "computations", "run_timeout_seconds") is (
        Origin.WORKSPACE
    )
    assert settings.origin(read, "computations", "runners", "py") is Origin.PAPER
    assert settings.origin(read, "computations", "runners", "R") is Origin.DEFAULT
    assert settings.origin(read, "gates", "defects") is Origin.PAPER
    assert settings.origin(read, "gates", "unreviewed") is Origin.DEFAULT
    found = (tmp_path / "workspace" / "refs.bib").resolve()
    assert settings.bibliography_from(read) == (found, Origin.WORKSPACE)
    alone = tmp_path / "alone"
    alone.mkdir()
    assert settings.bibliography_from(settings.load(alone)) == (None, Origin.DEFAULT)
    (alone / "only.bib").write_text("")
    assert settings.bibliography_from(settings.load(alone)) == (
        alone.resolve() / "only.bib",
        Origin.BESIDE,
    )


def test_the_settings_command_prints_every_value_in_force(
    tmp_path, capsys, user_file, monkeypatch
) -> None:
    """In `sources status`'s form: each value and where it came from; a
    paper in a workspace with no file of its own takes the workspace's; the
    person's values beside them, a secret shown set or unset."""
    import json

    from axiomation.cli import main

    paper = paper_in_workspace(
        tmp_path,
        shared="[gates]\nunused-premises = 'block'\n[sources]\nbib = 'refs.bib'\n",
    )
    write_user_file(user_file, ocr_backend="paddle", voyage_api_key="file-secret")
    monkeypatch.setenv("AXIOMATION_QDRANT_URL", "http://q:1")
    (tmp_path / "workspace" / ".env").write_text("OCR_URL=http://x\n")
    assert main(["settings", str(paper)]) == 0
    out = capsys.readouterr().out
    workspace = (tmp_path / "workspace").resolve()
    assert f"files      {workspace / CONFIG_NAME}\n" in out
    assert f"user file  {user_file}\n" in out
    assert f"ignored    {workspace / '.env'}: axiomation reads no .env\n" in out
    width = len("undeclared-assumptions")

    def row(key: str, value: object, came: str) -> str:
        return f"  {key:<{width}}  {value}  ({came})\n"

    assert row("unused-premises", "block", "workspace") in out
    assert row("defects", "block", "default") in out
    assert row("runners.py", "python3", "default") in out
    assert row("bib", workspace / "refs.bib", "workspace") in out
    assert "[user]\n" in out
    assert row("ocr_backend", "paddle", "user") in out
    assert row("qdrant_url", "http://q:1", "shell") in out
    assert row("voyage_api_key", "set", "user") in out
    assert row("ocr_api_key", "unset", "default") in out
    assert row("ocr_url", "none", "default") in out
    assert row("zotero_url", "http://localhost:23119", "default") in out
    assert "file-secret" not in out
    assert "`axiomation sources status PAPER`" in out
    assert main(["settings", str(paper), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["workspace"] == str(workspace)
    assert data["sections"]["gates"]["unused-premises"] == {
        "value": "block",
        "from": "workspace",
    }
    assert data["sections"]["computations"]["directories"] == {
        "value": ["."],
        "from": "default",
    }
    (paper / CONFIG_NAME).write_text("[gates]\nnot-a-gate = 'warn'\n")
    assert main(["settings", str(paper)]) == 1
    assert "names no gate 'not-a-gate'" in capsys.readouterr().err
    assert main(["settings", str(tmp_path / "nowhere")]) == 1
    assert "is no directory" in capsys.readouterr().err


def test_a_run_s_budget_default_is_the_paper_s_over_the_workspace_s(tmp_path) -> None:
    paper = paper_in_workspace(tmp_path, shared="[budget]\nrun_dollars_max = 20\n")
    assert settings.run_dollars_max(settings.load(paper)) == (
        20.0,
        settings.Origin.WORKSPACE,
    )
    (paper / CONFIG_NAME).write_text("[budget]\nrun_dollars_max = 7.5\n")
    assert settings.run_dollars_max(settings.load(paper)) == (
        7.5,
        settings.Origin.PAPER,
    )
    assert settings.run_dollars_max(settings.load(tmp_path / "workspace" / "x")) == (
        None,
        settings.Origin.DEFAULT,
    )


@pytest.mark.parametrize(
    ("table", "refusal"),
    [
        ("run_dollars_max = 0", "dollars above 0, not 0"),
        ("run_dollars_max = '5'", "dollars above 0, not '5'"),
        ("run_dollars_max = true", "dollars above 0, not True"),
        ("run_dollars_max = nan", "dollars above 0, not nan"),
        ("run_dollars_max = inf", "dollars above 0, not inf"),
        ("day_dollars_max = 50", "holds only run_dollars_max, not day_dollars_max"),
    ],
)
def test_a_budget_default_that_is_no_amount_is_refused(
    tmp_path, table: str, refusal: str
) -> None:
    paper = paper_in_workspace(tmp_path, f"[budget]\n{table}\n")
    with pytest.raises(SettingsError, match=refusal):
        settings.run_dollars_max(settings.load(paper))


def test_the_person_s_budget_default_is_the_last_one_asked(
    tmp_path, user_file, monkeypatch
) -> None:
    """The person's own default, their shell's over their user file's,
    holds on any paper that sets none; a workspace or a paper sets its
    own over it."""
    paper = paper_in_workspace(tmp_path)
    write_user_file(user_file, run_dollars_max=40)
    assert settings.run_dollars_max(settings.load(paper)) == (40.0, Origin.USER)
    monkeypatch.setenv("AXIOMATION_RUN_DOLLARS_MAX", "25")
    assert settings.run_dollars_max(settings.load(paper)) == (25.0, Origin.SHELL)
    (paper.parent.parent / CONFIG_NAME).write_text(
        '[workspace]\npapers = "papers/*"\n[budget]\nrun_dollars_max = 20\n'
    )
    assert settings.run_dollars_max(settings.load(paper)) == (20.0, Origin.WORKSPACE)
    (paper / CONFIG_NAME).write_text("[budget]\nrun_dollars_max = 50\n")
    assert settings.run_dollars_max(settings.load(paper)) == (50.0, Origin.PAPER)


@pytest.mark.parametrize("shell", ["0", "-3", "ten", "nan", "inf"])
def test_a_person_s_budget_in_the_shell_that_is_no_amount_is_refused(
    tmp_path, monkeypatch, shell: str
) -> None:
    monkeypatch.setenv("AXIOMATION_RUN_DOLLARS_MAX", shell)
    with pytest.raises(SettingsError, match="AXIOMATION_RUN_DOLLARS_MAX in the shell"):
        settings.run_dollars_max(settings.load(paper_in_workspace(tmp_path)))


def test_the_settings_command_prints_the_budget_default(tmp_path, capsys) -> None:
    from axiomation.cli import main

    paper = paper_in_workspace(tmp_path)
    assert main(["settings", str(paper)]) == 0
    assert "[budget]\n  run_dollars_max" in capsys.readouterr().out
    (paper / CONFIG_NAME).write_text("[budget]\nrun_dollars_max = 12\n")
    assert main(["settings", str(paper), "--json"]) == 0
    assert '"run_dollars_max": {\n        "value": 12.0,\n        "from": "paper"' in (
        capsys.readouterr().out
    )


@pytest.mark.parametrize(
    ("paper_toml", "named"),
    [
        ("[sources]\nocr_backend = 'mineru'\n", "sources.ocr_backend"),
        ("[sources]\nqdrant_url = 'http://q'\n", "sources.qdrant_url"),
        ("[sources]\ncontact_email = 'me@example.org'\n", "sources.contact_email"),
        ("OCR_URL = 'http://ocr'\n", "OCR_URL"),
        ("[paper]\nVOYAGE_API_KEY = 'k'\n", "paper.VOYAGE_API_KEY"),
        ("[env]\nAXIOMATION_OCR_API_KEY = 'k'\n", "env.AXIOMATION_OCR_API_KEY"),
    ],
)
def test_a_person_s_fact_in_a_paper_s_file_is_refused_naming_the_user_file(
    tmp_path, user_file, paper_toml: str, named: str
) -> None:
    """A paper's file is shared with whoever has the paper: a fact of one
    person's machine, or a key, has no place in it, in any table."""
    paper = paper_in_workspace(tmp_path, paper_toml)
    with pytest.raises(SettingsError) as refused:
        settings.load(paper)
    message = str(refused.value)
    assert message.startswith(f"{paper.resolve() / CONFIG_NAME}: {named} is a fact")
    assert str(user_file) in message and "in the shell" in message


@pytest.mark.parametrize("name", ["github_token", "db_password", "openai_api_key"])
def test_any_secret_in_a_workspace_s_file_is_refused(
    tmp_path, user_file, name: str
) -> None:
    paper = paper_in_workspace(tmp_path, shared=f"[extra]\n{name} = 'x'\n")
    with pytest.raises(SettingsError, match=f"extra.{name} looks like a secret"):
        settings.load(paper)


class TestARunner:
    """A Python script runs in its author's environment: the one the files
    nearest it name, up to the workspace's root (the paper's, outside
    one), else `python3`; a declared runner wins."""

    def runner(self, paper: Path, key: str = "run.py") -> computations.Runner:
        (paper / key).parent.mkdir(parents=True, exist_ok=True)
        (paper / key).write_text("print(1)\n")
        return computations.runner_found(computations.load(paper), key)

    @pytest.mark.parametrize(
        ("name", "command"),
        [
            ("uv.lock", "uv run python"),
            ("poetry.lock", "poetry run python"),
            ("pixi.toml", "pixi run python"),
            ("Pipfile", "pipenv run python"),
        ],
    )
    def test_each_lock_file_names_its_runner(
        self, tmp_path, name: str, command: str
    ) -> None:
        (tmp_path / name).write_text("")
        assert self.runner(tmp_path) == computations.Runner(
            command, chose=tmp_path.resolve() / name
        )

    def test_a_conda_file_names_its_environment(self, tmp_path) -> None:
        (tmp_path / "environment.yml").write_text(
            "# the paper's\nname: my-paper  # its name\ndependencies:\n  - numpy\n"
        )
        assert self.runner(tmp_path).command == "conda run -n my-paper python"

    def test_a_conda_file_naming_none_is_passed_over(self, tmp_path) -> None:
        (tmp_path / "environment.yml").write_text("dependencies:\n  - numpy\n")
        assert self.runner(tmp_path) == computations.Runner("python3")

    def test_a_venv_s_python_is_named_from_the_paper(self, tmp_path) -> None:
        paper = paper_in_workspace(tmp_path)
        python = tmp_path / "workspace" / ".venv" / "bin" / "python"
        python.parent.mkdir(parents=True)
        python.write_text("")
        assert self.runner(paper, "checks/fit.py") == computations.Runner(
            "../../.venv/bin/python", chose=python.parent.parent.resolve()
        )

    def test_the_nearest_directory_wins_and_in_it_the_order(self, tmp_path) -> None:
        paper = paper_in_workspace(tmp_path)
        (tmp_path / "workspace" / "uv.lock").write_text("")
        assert self.runner(paper).command == "uv run python"
        for name in ("Pipfile", "poetry.lock"):
            (paper / name).write_text("")
        assert self.runner(paper).command == "poetry run python"

    def test_a_file_below_the_paper_s_directory_is_not_read(self, tmp_path) -> None:
        """A run starts in the paper's directory, and the tools find their
        project from where they start: a file below it names an
        environment the run would not use."""
        paper = paper_in_workspace(tmp_path)
        (tmp_path / "workspace" / "uv.lock").write_text("")
        (paper / "checks").mkdir()
        (paper / "checks" / "poetry.lock").write_text("")
        assert self.runner(paper, "checks/fit.py").command == "uv run python"

    def test_nothing_outside_the_scope_is_read(self, tmp_path) -> None:
        (tmp_path / "uv.lock").write_text("")
        paper = tmp_path / "alone"
        paper.mkdir()
        assert self.runner(paper) == computations.Runner("python3")

    def test_a_declared_runner_wins(self, tmp_path) -> None:
        (tmp_path / "uv.lock").write_text("")
        (tmp_path / CONFIG_NAME).write_text(
            "[computations.runners]\npy = 'python3.12'\n"
        )
        assert self.runner(tmp_path) == computations.Runner("python3.12", declared=True)

    def test_another_language_still_needs_its_runner_declared(self, tmp_path) -> None:
        (tmp_path / "uv.lock").write_text("")
        with pytest.raises(SettingsError, match="no runner for .R files"):
            self.runner(tmp_path, "fit.R")

    def test_settings_names_the_file_that_chose(self, tmp_path, capsys) -> None:
        from axiomation.cli import main

        paper = paper_in_workspace(tmp_path)
        (tmp_path / "workspace" / "uv.lock").write_text("")
        assert main(["settings", str(paper)]) == 0
        out = capsys.readouterr().out
        width = len("undeclared-assumptions")
        said = "uv run python  (found ../../uv.lock)"
        assert f"  {'runners.py':<{width}}  {said}\n" in out

    def test_a_script_runs_without_the_person_s_keys(
        self, tmp_path, monkeypatch
    ) -> None:
        """A paper's script never needs axiomation's keys: they stay out of
        its environment; the rest of the shell's reaches it."""
        import shlex
        import sys

        (tmp_path / CONFIG_NAME).write_text(
            f"[computations.runners]\npy = {shlex.quote(sys.executable)!r}\n"
        )
        (tmp_path / "env.py").write_text(
            "import os\n"
            "print(sorted(k for k in os.environ if k.startswith('AXIOMATION_')))\n"
        )
        monkeypatch.setenv("AXIOMATION_VOYAGE_API_KEY", "secret")
        monkeypatch.setenv("AXIOMATION_QDRANT_URL", "http://q:1")
        ran = computations.run(tmp_path, "env.py", (), tmp_path / "cache")
        assert "AXIOMATION_QDRANT_URL" in ran.stdout
        assert "VOYAGE" not in ran.stdout
