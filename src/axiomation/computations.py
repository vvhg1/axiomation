"""A paper's computations: its code files, where its settings
say they live, each with its opening line.

A code file is a file with a code extension (`CODE_SUFFIXES`) under one
of the directories `[computations].directories` names, each from the
paper's directory (default: the paper's own), the paper's store and every
hidden directory left out. A directory may leave the paper but never its
workspace (the paper, where there is none): nothing outside is read. A
file inside the paper is keyed by its path from there, as a `file:`
target names it; one outside by `workspace:` and its path from the
workspace root. The graph knows a code file as a kept file with a
code extension (`is_code`): the directories say where code is found, the
extension what it is.

A run is the runner for the script's extension, the script's path from
the paper and the arguments a judgment names, as
an argument list, never through a shell, from the paper's directory,
under the timeout. It is stamped (`evidence.RunStamp`) and cached under
the store by its script's blob, runner and arguments, so a gate's run
and a filing's share one; a fresh run replaces the cache. A run happens
only when a filing or a person asks for one: never on a read, a replay
or a build.

A runner is the one `[computations.runners]` declares for the extension.
A Python script with none declared runs in its author's environment: the
one the files nearest the paper name (`ENVIRONMENT_FILES`), looked for
from the paper's directory, where a run starts, up to the workspace's
root (the paper's, outside one), else `python3`: a run uses the
environment the paper's files make, with no setting.

Standard library only.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import signal
import subprocess
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from axiomation import settings
from axiomation.evidence import RunStamp, RunStatus, output_hash
from axiomation.reader.includes import STORE_DIRECTORY
from axiomation.settings import SettingsError
from axiomation.user_settings import UserKey

SECTION = "computations"
#: What a script is written in: a notebook is not code here, since it takes
#: no arguments and prints no stdout.
CODE_SUFFIXES = (".py", ".R", ".jl", ".m", ".sh")
#: The key of a code file outside the paper, before its path from the
#: workspace root.
WORKSPACE = "workspace:"
DEFAULT_RUN_TIMEOUT_SECONDS = 600
#: Where a paper's scripts live, from its directory, where the settings name
#: nowhere: the paper's own.
DEFAULT_DIRECTORIES = (".",)
#: The files that name a Python environment, in the order one directory's
#: are tried, each with the runner it gives; a conda environment file gives
#: `conda run -n <its name> python`, a `.venv/` its own Python.
ENVIRONMENT_FILES: tuple[tuple[str, str], ...] = (
    ("uv.lock", "uv run python"),
    ("poetry.lock", "poetry run python"),
    ("pixi.toml", "pixi run python"),
    ("Pipfile", "pipenv run python"),
)
CONDA_FILES = ("environment.yml", "environment.yaml")
VENV = ".venv"
#: A Python script's runner where no file names an environment.
FALLBACK_PYTHON = "python3"
KEYS = ("directories", "run_timeout_seconds", "runners")
#: The longest opening line the edges sheet shows.
OPENING_LIMIT = 200


@dataclass(frozen=True)
class Computations:
    """A paper's `[computations]`, the paper's keys over its workspace's:
    the directories its scripts live in, resolved; how long a run may
    take; and the commands the settings declare to run a script, by its
    extension without the dot."""

    paper: Path
    workspace_root: Path | None
    directories: tuple[Path, ...]
    run_timeout_seconds: int = DEFAULT_RUN_TIMEOUT_SECONDS
    runners: Mapping[str, str] = field(default_factory=dict)


def is_code(key: str) -> bool:
    """Whether a file's key names code: by its extension."""
    return PurePosixPath(key.removeprefix(WORKSPACE)).suffix in CODE_SUFFIXES


def _checked(table: Mapping[str, Any], where: Path) -> None:
    for key in table:
        if key not in KEYS:
            raise SettingsError(
                f"{where}: [{SECTION}].{key} is unknown (known: {', '.join(KEYS)})"
            )
    directories = table.get("directories", [])
    if not isinstance(directories, list) or not all(
        isinstance(d, str) and d for d in directories
    ):
        raise SettingsError(f"{where}: [{SECTION}].directories must be a list of paths")
    timeout = table.get("run_timeout_seconds", DEFAULT_RUN_TIMEOUT_SECONDS)
    if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout <= 0:
        raise SettingsError(
            f"{where}: [{SECTION}].run_timeout_seconds must be a whole number of "
            "seconds above 0"
        )
    runners = table.get("runners", {})
    if not isinstance(runners, dict):
        raise SettingsError(f"{where}: [{SECTION}.runners] must be a table")
    suffixes = [s.removeprefix(".") for s in CODE_SUFFIXES]
    for suffix, command in runners.items():
        if suffix not in suffixes:
            raise SettingsError(
                f"{where}: [{SECTION}.runners].{suffix} names no code extension "
                f"(known: {', '.join(suffixes)})"
            )
        if not isinstance(command, str) or not command.split():
            raise SettingsError(
                f"{where}: [{SECTION}.runners].{suffix} must be a command"
            )


def load(paper: Path) -> Computations:
    """A paper's `[computations]`; refused, naming the file, where a key is
    unknown or malformed, or where a directory leaves the workspace (the
    paper, where there is no workspace)."""
    read = settings.load(paper)
    own, shared = read.tables(SECTION)
    _checked(own, read.file(False))
    if shared:
        _checked(shared, read.file(True))
    merged = {**shared, **own}
    runners = {**shared.get("runners", {}), **own.get("runners", {})}
    # an empty list declares no directory: the paper has no code
    named = merged.get("directories", list(DEFAULT_DIRECTORIES))
    scope = read.scope.resolve()
    directories = []
    for name in named:
        found = (read.paper / name).resolve()
        if not found.is_relative_to(scope):
            where = "workspace" if read.workspace_root is not None else "paper"
            declared = read.file(shared="directories" not in own)
            raise SettingsError(
                f"{declared}: [{SECTION}].directories "
                f"names {name!r}, which leaves the {where} ({scope}); nothing "
                "outside it is read"
            )
        directories.append(found)
    return Computations(
        read.paper,
        read.workspace_root,
        tuple(dict.fromkeys(directories)),
        merged.get("run_timeout_seconds", DEFAULT_RUN_TIMEOUT_SECONDS),
        runners,
    )


def key_of(path: Path, found: Computations) -> str:
    """A code file's key: its path from the paper, else `workspace:` and its
    path from the workspace root."""
    path = path.resolve()
    if path.is_relative_to(found.paper):
        return path.relative_to(found.paper).as_posix()
    assert found.workspace_root is not None
    return WORKSPACE + path.relative_to(found.workspace_root.resolve()).as_posix()


def _left_out(path: Path, directory: Path) -> bool:
    parts = path.relative_to(directory).parts
    return any(p.startswith(".") or p == STORE_DIRECTORY for p in parts)


def code_files(paper: Path) -> dict[str, Path]:
    """The paper's code files, by key in order, each its path on disk: those
    under its directories, and any at its top level, which is the paper's
    whatever the directories say; refused where one leads outside the
    scope (a link)."""
    found = load(paper)
    scope = (found.workspace_root or found.paper).resolve()
    files: dict[str, Path] = {
        path.name: path
        for path in found.paper.iterdir()
        if path.suffix in CODE_SUFFIXES
        and path.is_file()
        and not path.name.startswith(".")
    }
    for directory in found.directories:
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*")):
            if path.suffix not in CODE_SUFFIXES or _left_out(path, directory):
                continue
            if not path.is_file():
                continue
            if not path.resolve().is_relative_to(scope):
                raise SettingsError(
                    f"{path} leads outside {scope}; nothing outside it is read"
                )
            files.setdefault(key_of(path, found), path)
    return dict(sorted(files.items()))


#: A comment line's marker, by the languages `CODE_SUFFIXES` names.
_COMMENT = re.compile(r"^\s*(#|%)+ ?")
_DOCSTRING = re.compile(r'^\s*[rRuU]?("""|\'\'\')')
_SENTENCE = re.compile(r"[.!?](?=\s|$)")


def opening_line(text: str) -> str:
    """A script's opening line: the first sentence of its leading
    comment or docstring, a shebang and a coding line passed over, capped
    at `OPENING_LIMIT` characters; empty where it opens with neither."""
    lines = text.splitlines()
    i = 0
    while i < len(lines) and (
        not lines[i].strip()
        or lines[i].startswith("#!")
        or re.match(r"^#.*coding[:=]", lines[i])
    ):
        i += 1
    if i == len(lines):
        return ""
    words: list[str] = []
    if m := _DOCSTRING.match(lines[i]):
        quote = m.group(1)
        rest = lines[i][m.end() :]
        for line in [rest, *lines[i + 1 :]]:
            done = quote in line
            line = line.split(quote)[0]
            if not line.strip() and words:
                break
            words.append(line)
            if done:
                break
    elif _COMMENT.match(lines[i]):
        for line in lines[i:]:
            if not _COMMENT.match(line):
                break
            line = _COMMENT.sub("", line, count=1)
            if not line.strip():
                if words:
                    break
                continue
            words.append(line)
    said = " ".join(" ".join(words).split())
    end = _SENTENCE.search(said)
    if end is not None:
        said = said[: end.end()]
    if len(said) > OPENING_LIMIT:
        said = said[: OPENING_LIMIT - 1].rstrip() + "…"
    return said


# --- the run -----------------------------------------------------

#: Where the runs are cached, under the store.
RUN_CACHE = Path("cache") / "runs"
#: How much of a run's stderr its cache keeps, from the end.
STDERR_KEPT = 4000


@dataclass(frozen=True)
class Ran:
    """A run: its stamp, its output, the end of its stderr, and how long
    it took."""

    stamp: RunStamp
    stdout: str
    stderr: str = ""
    seconds: float = 0.0


def _path_of(found: Computations, key: str) -> Path:
    if key.startswith(WORKSPACE):
        assert found.workspace_root is not None
        return found.workspace_root / key.removeprefix(WORKSPACE)
    return found.paper / key


@dataclass(frozen=True)
class Runner:
    """A script's runner and what chose it: the settings (`declared`), the
    environment file found (`chose`), or neither, the fallback."""

    command: str
    declared: bool = False
    chose: Path | None = None


def python_runner(found: Computations) -> Runner:
    """The runner of the paper's Python scripts, none being declared: the
    environment the nearest directory's files name, from the paper's
    directory up to the scope, else `python3`. A run starts in the
    paper's directory, and `uv`, `poetry`, `pixi` and `pipenv` find their
    project from where they start, so a file below it would name an
    environment the run does not use."""
    scope = (found.workspace_root or found.paper).resolve()
    paper = found.paper.resolve()
    for here in (paper, *paper.parents):
        if not here.is_relative_to(scope):
            break
        for name, command in ENVIRONMENT_FILES:
            if (here / name).is_file():
                return Runner(command, chose=here / name)
        for name in CONDA_FILES:
            environment = _conda_name(here / name)
            if environment:
                command = f"conda run -n {shlex.quote(environment)} python"
                return Runner(command, chose=here / name)
        python = _venv_python(here / VENV)
        if python is not None:
            # from the paper's directory, where a run starts, so the run's
            # call is the same wherever the paper and its `.venv` are
            command = shlex.quote(os.path.relpath(python, found.paper))
            return Runner(command, chose=here / VENV)
    return Runner(FALLBACK_PYTHON)


#: A conda environment file's `name:` line.
_CONDA_NAME = re.compile(r"^name:\s*['\"]?([^'\"\s#]+)", re.MULTILINE)


def _conda_name(path: Path) -> str:
    """The environment a conda file names; empty where it is no file or
    names none."""
    if not path.is_file():
        return ""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
    named = _CONDA_NAME.search(text)
    return named.group(1) if named else ""


def _venv_python(venv: Path) -> Path | None:
    for python in (venv / "bin" / "python", venv / "Scripts" / "python.exe"):
        if python.is_file():
            return python
    return None


def runner_found(found: Computations, key: str) -> Runner:
    """A code file's runner: the one the settings declare for its
    extension; for a Python script with none, its environment's; refused,
    naming the setting, for another extension with none."""
    suffix = PurePosixPath(key).suffix.removeprefix(".")
    declared = found.runners.get(suffix)
    if declared is not None:
        return Runner(declared, declared=True)
    if suffix == "py":
        return python_runner(found)
    raise SettingsError(
        f"no runner for .{suffix} files: declare one, "
        f'[{SECTION}.runners] {suffix} = "<command>", in {settings.CONFIG_NAME}'
    )


def runner_of(found: Computations, key: str) -> str:
    """A code file's runner's command (`runner_found`)."""
    return runner_found(found, key).command


def _cached(cache: Path, stamp_call: Sequence[Any]) -> Path:
    name = hashlib.sha256(json.dumps(list(stamp_call)).encode("utf-8")).hexdigest()
    return cache / f"{name}.json"


def _ran_from(path: Path) -> Ran:
    data = json.loads(path.read_text(encoding="utf-8"))
    stamp = data["stamp"]
    return Ran(
        RunStamp(
            stamp["key"],
            stamp["blob"],
            stamp["runner"],
            tuple(stamp["arguments"]),
            stamp["directory"],
            RunStatus(stamp["status"]),
            stamp["output"],
        ),
        data["stdout"],
        data["stderr"],
        data["seconds"],
    )


def _write(path: Path, ran: Ran) -> None:
    stamp = ran.stamp
    data = {
        "stamp": {
            "key": stamp.key,
            "blob": stamp.blob,
            "runner": stamp.runner,
            "arguments": list(stamp.arguments),
            "directory": stamp.directory,
            "status": stamp.status.value,
            "output": stamp.output,
        },
        "stdout": ran.stdout,
        "stderr": ran.stderr,
        "seconds": ran.seconds,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def _script_environment() -> dict[str, str]:
    """The environment a paper's script runs in: the shell's, the
    person's keys for axiomation left out, since a paper's script never
    needs them."""
    secrets = {key.env for key in UserKey if key.secret}
    return {k: v for k, v in os.environ.items() if k not in secrets}


def _executed(
    command: Sequence[str], directory: Path, timeout: int
) -> tuple[RunStatus, str, str]:
    """A command run as an argument list, no shell, in its own process
    group, so a runner's children end with it at the timeout: how it
    ended, its stdout and its stderr."""
    try:
        process = subprocess.Popen(
            list(command),
            cwd=directory,
            env=_script_environment(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
    except OSError as error:
        return RunStatus.FAILED, "", f"the runner did not start: {error}"
    with process:
        try:
            out, err = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            out, _ = process.communicate()
            why = f"ran past {timeout} s ([{SECTION}].run_timeout_seconds)"
            return RunStatus.TIMED_OUT, out.decode("utf-8", "replace"), why
    stdout, stderr = out.decode("utf-8", "replace"), err.decode("utf-8", "replace")
    if process.returncode != 0:
        return RunStatus.FAILED, stdout, f"{stderr}\n(exit {process.returncode})"
    return RunStatus.RAN, stdout, stderr


def run(
    paper: Path,
    key: str,
    arguments: Sequence[str],
    cache: Path,
    fresh: bool = False,
) -> Ran:
    """A run of the paper's code file `key` with `arguments`: from the cache
    `cache` where it ran with this blob, runner and arguments before, else
    (or `fresh`) run now and cached. Refused where `key` is not one of the
    paper's code files, or its extension has no runner."""
    ran = _run(paper, key, arguments, cache, fresh, None)
    assert ran is not None
    return ran


def run_within(
    paper: Path, key: str, arguments: Sequence[str], cache: Path, seconds: float
) -> Ran | None:
    """`run`, where the caller can wait `seconds` at most (a session's gate,
    under its host's hook timeout): None where it ran past them, nothing
    cached, so the run is made again in full by whoever can wait for it."""
    return _run(paper, key, arguments, cache, False, seconds)


def _run(
    paper: Path,
    key: str,
    arguments: Sequence[str],
    cache: Path,
    fresh: bool,
    within: float | None,
) -> Ran | None:
    found = load(paper)
    files = code_files(paper)
    if key not in files:
        raise SettingsError(
            f"{key} is not one of the paper's code files "
            f"({', '.join(files) or 'it has none'}; [{SECTION}].directories)"
        )
    runner = runner_of(found, key)
    path = _path_of(found, key)
    blob = hashlib.sha256(path.read_bytes()).hexdigest()
    arguments = tuple(arguments)
    held = _cached(cache, (key, blob, runner, arguments))
    if held.exists() and not fresh:
        return _ran_from(held)
    if within is not None and within < 1:
        return None  # the caller's time is spent: not started
    script = os.path.relpath(path, found.paper)
    command = [*shlex.split(runner), script, *arguments]
    timeout = found.run_timeout_seconds
    cut_short = within is not None and within < timeout
    if cut_short:
        assert within is not None
        timeout = max(1, int(within))
    started = time.monotonic()
    status, stdout, stderr = _executed(command, found.paper, timeout)
    if cut_short and status is RunStatus.TIMED_OUT:
        return None
    ran = Ran(
        RunStamp(
            key,
            blob,
            runner,
            arguments,
            str(found.paper),
            status,
            output_hash(stdout),
        ),
        stdout,
        stderr[-STDERR_KEPT:],
        round(time.monotonic() - started, 3),
    )
    _write(held, ran)
    return ran
