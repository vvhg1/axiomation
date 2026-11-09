"""`axiomation read`, `axiomation cut`, `axiomation compare` and `axiomation diff`."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import axiomation
import record_file
from axiomation.cli import main, parser
from axiomation.commands import Exit
from axiomation.cutter import CutRules, DisplayRule, cut
from axiomation.driver import (
    ReviewSettings,
    Templates,
    map_vocabulary,
    review_cut,
    review_payload,
)
from axiomation.exchange import cut_from_payload, cut_payload, document_payload, dumps
from axiomation.paper_argument import PaperNotFound, with_paper
from axiomation.reader import read_paper
from axiomation.records import AnswerRecord, RecordStore, TemplateRef
from axiomation.resolver import Resolver
from axiomation.runner import Runner
from axiomation.runner.hosts import Rates
from questions.review_paper import make_paper
from runner.fakes import (
    FAKE_CLAUDE_ENV,
    FakeHost,
    events,
    fake_claude,
    fake_opencode,
    fenced,
    step_finish,
    text,
)


@pytest.fixture
def directory(tmp_path: Path) -> Path:
    root = tmp_path / "paper"
    root.mkdir()
    (root / "paper.md").write_text(
        "---\ntitle: A paper\n---\n\n# One\n\n\\label{sec:one}\n\nText.\n",
        encoding="utf-8",
    )
    return root


def test_it_writes_what_dumps_gives_to_stdout(directory, capsysbinary) -> None:
    assert main(["read", str(directory)]) == 0

    written = capsysbinary.readouterr().out.decode("utf-8")
    assert written == dumps(document_payload(read_paper(directory)))


def test_it_writes_the_same_to_a_file(directory, tmp_path, capsysbinary) -> None:
    output = tmp_path / "out.json"

    assert main(["read", str(directory), "-o", str(output)]) == 0

    assert capsysbinary.readouterr().out == b""
    assert output.read_text(encoding="utf-8") == dumps(
        document_payload(read_paper(directory))
    )


def test_a_root_can_be_named(directory, capsysbinary) -> None:
    """`paper.md` is what the search would take among two `.md` files."""
    (directory / "other.md").write_text("# Two\n", encoding="utf-8")

    assert main(["read", str(directory)]) == 0
    assert b'"root": "paper.md"' in capsysbinary.readouterr().out
    assert main(["read", str(directory), "--root", "other.md"]) == 0

    assert b'"root": "other.md"' in capsysbinary.readouterr().out


def test_a_paper_without_a_root_is_an_error(tmp_path, capsys) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    (empty / "a.md").write_text("a\n", encoding="utf-8")
    (empty / "b.md").write_text("b\n", encoding="utf-8")

    assert main(["read", str(empty)]) == 1

    error = capsys.readouterr().err
    assert "has no root" in error and "a.md, b.md" in error


def test_the_version_names_the_package_it_runs_from(capsys, monkeypatch) -> None:
    # A narrow terminal: the line stays whole however long the path.
    monkeypatch.setenv("COLUMNS", "20")
    with pytest.raises(SystemExit) as stop:
        main(["--version"])

    assert stop.value.code == 0
    package = Path(axiomation.__file__).resolve().parent
    assert (
        capsys.readouterr().out == f"axiomation {axiomation.__version__} ({package})\n"
    )


def test_an_output_it_cannot_write_is_an_error(directory, tmp_path, capsys) -> None:
    missing = tmp_path / "no" / "such" / "dir" / "out.json"

    assert main(["read", str(directory), "-o", str(missing)]) == 1

    assert "axiomation read:" in capsys.readouterr().err


def test_a_root_outside_the_directory_is_an_error(directory, tmp_path, capsys) -> None:
    outside = tmp_path / "outside.md"
    outside.write_text("# Out\n", encoding="utf-8")

    assert main(["read", str(directory), "--root", str(outside)]) == 1

    assert "is not a file in" in capsys.readouterr().err


# --- `axiomation cut` and `axiomation compare` ---


def test_cut_writes_what_cut_payload_gives(directory, tmp_path, capsysbinary) -> None:
    output = tmp_path / "cut.json"

    assert main(["cut", str(directory), "-o", str(output)]) == 0
    first = output.read_bytes()
    assert main(["cut", str(directory), "-o", str(output)]) == 0

    assert output.read_bytes() == first
    document = read_paper(directory)
    assert first.decode("utf-8") == dumps(cut_payload(document, cut(document)))


def test_cut_takes_the_display_rule(directory, capsysbinary) -> None:
    assert main(["cut", str(directory), "--displays", "in-paragraph"]) == 0

    payload = json.loads(capsysbinary.readouterr().out)
    assert payload["rules"] == {"displays": "in-paragraph"}
    assert [block["form"] for block in payload["blocks"]] == [
        "title",
        "heading",
        "prose",
    ]


def test_compare_prints_the_summary(directory, tmp_path, capsys) -> None:
    other = tmp_path / "other"
    other.mkdir()
    (other / "paper.tex").write_text(
        "\\title{A paper}\n\\begin{document}\n\\maketitle\n"
        "\\section{One}\\label{sec:one}\n\nText.\n\nMore text.\n\\end{document}\n",
        encoding="utf-8",
    )

    assert main(["compare", str(directory), str(other)]) == 0

    lines = dict(line.split() for line in capsys.readouterr().out.splitlines())
    # `# One` and `\\section{One}` share a key; only their markup differs.
    assert lines == {
        "blocks_a": "3",
        "blocks_b": "4",
        "same": "2",
        "math-differs": "0",
        "markup-differs": "1",
        "changed": "0",
        "split": "0",
        "merged": "0",
        "only-a": "0",
        "only-b": "1",
        "hashes_kept": "2",
    }


def test_compare_prints_the_pairs_as_json(directory, capsys) -> None:
    assert main(["compare", str(directory), str(directory), "--json"]) == 0

    pairs = json.loads(capsys.readouterr().out)
    assert [pair["kind"] for pair in pairs] == ["same", "same", "same"]
    assert pairs[1] == {"kind": "same", "a": ["paper.md:5:1"], "b": ["paper.md:5:1"]}


def test_compare_of_a_paper_without_a_root_is_an_error(directory, tmp_path, capsys):
    empty = tmp_path / "empty"
    empty.mkdir()

    assert main(["compare", str(directory), str(empty)]) == 1

    assert "axiomation compare:" in capsys.readouterr().err


# --- `axiomation diff` ---


def test_diff_prints_the_classes_and_writes_the_links(directory, tmp_path, capsys):
    other = tmp_path / "other"
    other.mkdir()
    (other / "paper.md").write_text(
        (directory / "paper.md").read_text(encoding="utf-8") + "\nAdded text.\n",
        encoding="utf-8",
    )

    assert main(["diff", str(directory), str(other)]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("identity-10 ")
    assert dict(line.split() for line in lines[1:]) == {"same": "3", "added": "1"}

    output = tmp_path / "diff.json"
    assert main(["diff", str(directory), str(other), "--json", "-o", str(output)]) == 0
    found = json.loads(output.read_text(encoding="utf-8"))
    assert found["links"][1] == {
        "old": ["paper.md:5:1"],
        "new": ["paper.md:5:1"],
        "cls": "same",
        "tier": "text-map",
    }
    assert found["old"] != found["new"] and found["macros"] == []


def test_diff_of_a_version_without_a_root_is_an_error(directory, tmp_path, capsys):
    empty = tmp_path / "empty"
    empty.mkdir()

    assert main(["diff", str(directory), str(empty)]) == 1

    assert "axiomation diff:" in capsys.readouterr().err


def test_diff_with_a_cut_writes_it_rebased(tmp_path, capsys) -> None:
    """`diff OLD NEW --cut CUT -o NEW_CUT`: OLD's cut, given,
    rebased onto NEW, read back as a cut of NEW."""
    old = make_paper(tmp_path / "old").root
    new = make_paper(
        tmp_path / "new",
        main=(old / "main.tex")
        .read_text(encoding="utf-8")
        .replace("Closing words", "An opening.\n\nClosing words"),
    ).root
    given = tmp_path / "cut.json"
    assert main(["cut", str(old), "--displays", "in-paragraph", "-o", str(given)]) == 0
    rebased = tmp_path / "rebased.json"
    args = ["diff", str(old), str(new), "--displays", "in-paragraph"]
    assert main([*args, "--cut", str(given), "-o", str(rebased)]) == 0
    out = capsys.readouterr().out
    assert "to re-cut: 1; groups lost: 0" in out
    document = read_paper(new)
    read = cut_from_payload(
        document,
        json.loads(rebased.read_text(encoding="utf-8")),
        cut(document).rules,
        roles=True,
    )
    assert len(read.blocks) == len(cut(document).blocks)


def test_the_identity_key_binds_the_edges_answer_it_carries(tmp_path) -> None:
    """A later edges answer on the same cut makes a new
    identity answer, not a stale one."""
    from axiomation.exchange import identity_key

    paper = make_paper(tmp_path / "p")
    keys = {
        identity_key(paper.document, paper.document, paper.cut, "v", edges)
        for edges in (None, "a" * 64, "b" * 64)
    }
    assert len(keys) == 3


def test_diff_with_a_store_needs_the_cut_it_wrote(directory, capsys) -> None:
    assert main(["diff", str(directory), str(directory), "--store", "s"]) == 1
    assert "--store needs --cut" in capsys.readouterr().err


# --- `axiomation review-cut` ---


@pytest.fixture
def reviewed(tmp_path: Path):
    """The synthetic paper of the question's tests."""

    return make_paper(tmp_path / "paper")


def _fake_opencode_on_path(tmp_path: Path, monkeypatch, *items) -> Path:
    bin_directory = tmp_path / "bin"
    fake_opencode(bin_directory)
    scratch = tmp_path / "host-scratch"
    scratch.mkdir(exist_ok=True)
    played = tmp_path / "events.jsonl"
    played.write_text(events(*items), encoding="utf-8")
    monkeypatch.setenv("PATH", f"{bin_directory}:/usr/bin:/bin")
    monkeypatch.setenv("AXIOMATION_EXPERIMENTAL_OPENCODE", "1")
    monkeypatch.setenv("AXIOMATION_HOST_SCRATCH", str(scratch))
    monkeypatch.setenv("FAKE_OPENCODE_LOG", str(tmp_path / "opencode.log"))
    monkeypatch.setenv("FAKE_OPENCODE_EVENTS", str(played))
    monkeypatch.delenv("FAKE_OPENCODE_EXIT", raising=False)
    monkeypatch.delenv("FAKE_OPENCODE_EXPORT", raising=False)
    monkeypatch.delenv("FAKE_OPENCODE_EXPORT_EXIT", raising=False)
    for name in ("EDIT", "EDIT_RE", "FIX", "SESSIONS"):
        monkeypatch.delenv(f"FAKE_OPENCODE_{name}", raising=False)
    return tmp_path / "opencode.log"


def _fake_claude_on_path(tmp_path: Path, monkeypatch, scratch: Path) -> Path:
    bin_directory = tmp_path / "bin"
    fake_claude(bin_directory)
    monkeypatch.setenv("PATH", f"{bin_directory}:/usr/bin:/bin")
    monkeypatch.setenv("AXIOMATION_HOST_SCRATCH", str(scratch))
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(tmp_path / "claude.log"))
    for name in FAKE_CLAUDE_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("FAKE_CLAUDE_PROJECTS", str(tmp_path / "projects"))
    return tmp_path / "claude.log"


EMPTY_ANSWER = '```json\n{"corrections": []}\n```'

CLAUDE = ["--host", "claude-code", "--rates", "1,5", "--budget", "1"]


def test_review_cut_dry_run_prices_and_writes_no_store(
    reviewed, tmp_path, capsys
) -> None:
    store = tmp_path / "store"
    prompts = tmp_path / "prompts"

    code = main(
        [
            "review-cut",
            "--json",
            str(reviewed.root),
            "--store",
            str(store),
            "--chunk",
            "0",
            "--dry-run",
            "--json",
            "--prompts",
            str(prompts),
        ]
    )

    assert code == 0
    summary = json.loads(capsys.readouterr().out)
    assert list(summary["stages"]) == ["boundaries"]  # no judge by default
    assert summary["stages"]["boundaries"]["questions"] == 10
    assert (summary["chunk"], summary["overlap"], summary["rounds"]) == (0, 2, 1)
    assert summary["judge"] is False
    assert summary["estimate"] > 0
    assert summary["profile"] is None
    rates = summary["seats"]["boundaries"]["rates"]
    assert rates == {"input": 0.3, "output": 1.2, "cache_read": 0.006}
    assert not store.exists()
    assert sorted(p.name for p in prompts.iterdir())[:2] == [
        "boundaries-1",
        "boundaries-1.md",
    ]
    assert sorted(p.name for p in (prompts / "boundaries-1").iterdir()) == [
        "brief.md",
        "cuts.md",
        "outline.md",
        "paper.md",
    ]
    assert not (prompts / "groups-1.md").exists()


def test_review_cut_replays_a_store_made_in_process(reviewed, tmp_path, capsys):

    store = RecordStore(tmp_path / "store")
    host = FakeHost(lambda prompt: fenced({"corrections": []}), edit=lambda t: t)
    runner = Runner(host, Resolver(store), 1.0, Rates(0.3, 1.2))
    settings = ReviewSettings(chunk=0)
    made = review_cut(reviewed.document, reviewed.cut, store, settings, runner)
    output = tmp_path / "corrected.json"

    code = main(
        [
            "review-cut",
            "--json",
            str(reviewed.root),
            "--store",
            str(store.directory),
            "--chunk",
            "0",
            "-o",
            str(output),
        ]
    )

    assert code == 0
    assert output.read_text(encoding="utf-8") == dumps(
        review_payload(reviewed.document, made, settings)
    )
    summary = json.loads(capsys.readouterr().out)
    assert summary["complete"] is True and summary["stopped"] is None
    assert summary["spent"] == {"run": 0, "store": 0.1}


def test_review_cut_asking_needs_a_budget(reviewed, tmp_path, capsys) -> None:
    store = tmp_path / "store"

    code = main(
        [
            "review-cut",
            "--json",
            str(reviewed.root),
            "--store",
            str(store),
            "--host",
            "claude-code",
            "--rates",
            "1,5",
        ]
    )

    assert code == 1
    assert "needs --budget" in capsys.readouterr().err
    assert not store.exists()


@pytest.mark.parametrize(("host", "command"), [("claude-code", "claude")])
def test_review_cut_needs_the_host_on_path(
    reviewed, tmp_path, monkeypatch, capsys, host, command
):
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    store = tmp_path / "store"

    code = main(
        [
            "review-cut",
            "--json",
            str(reviewed.root),
            "--store",
            str(store),
            "--host",
            host,
            "--stage",
            "groups",
            "--rates",
            "1,5",
            "--budget",
            "1",
        ]
    )

    assert code == 1
    assert f"needs `{command}` on PATH" in capsys.readouterr().err
    assert not store.exists()


OPENCODE = ["--host", "opencode", "--budget", "1"]
DEEPSEEK = "opencode/deepseek-v4.1-flash"


@pytest.mark.parametrize(
    ("stage", "edit", "given"),
    [
        ("boundaries", "Hence the output|||\n⟦new⟧\nHence the output", False),
        ("roles", r"^⟦(b\d+)⟧$|||⟦\1 claim⟧", True),
        ("groups", "⟦group proof |||⟦group derivation ", True),
    ],
)
def test_review_cut_runs_each_stage_on_a_fake_opencode(
    reviewed, tmp_path, monkeypatch, capsys, host_scratch, stage, edit, given
) -> None:
    """Each stage a session on its file on OpenCode: the edit
    applies, the answer records the host and its model, and the replay is
    the run."""
    _fake_opencode_on_path(
        tmp_path, monkeypatch, text("Done."), step_finish(0.002, 300, 40)
    )
    monkeypatch.setenv("AXIOMATION_HOST_SCRATCH", str(host_scratch))
    monkeypatch.setenv("FAKE_OPENCODE_EDIT", edit)
    if stage == "roles":
        monkeypatch.setenv("FAKE_OPENCODE_EDIT_RE", "1")
    store = tmp_path / "store"
    output = tmp_path / "corrected.json"
    arguments = ["review-cut", "--json", str(reviewed.root), "--store", str(store)]
    arguments += ["--stage", stage, "--chunk", "paper"]
    if given:
        arguments += ["--cut", str(_given_cut(reviewed, tmp_path / "cut.json"))]
    arguments += ["-o", str(output)]

    code = main([*arguments, *OPENCODE])

    assert code == 0, capsys.readouterr().err
    summary = json.loads(capsys.readouterr().out)
    assert summary["complete"] is True and summary["applied"] >= 1
    answers = record_file.answers(store)
    assert [(a["host"], a["model"]) for a in answers] == [
        ("opencode 2.0.16-fake", DEEPSEEK)
    ]
    replayed = tmp_path / "replayed.json"
    assert main([*arguments[:-1], str(replayed)]) == 0
    assert replayed.read_bytes() == output.read_bytes()


def test_the_opencode_host_is_refused_without_its_switch(
    reviewed, tmp_path, monkeypatch, capsys
) -> None:
    """OpenCode is experimental: with its executable on PATH and its switch
    unset, a run on it is refused before any question is asked."""
    _fake_opencode_on_path(tmp_path, monkeypatch, text("Done."))
    monkeypatch.delenv("AXIOMATION_EXPERIMENTAL_OPENCODE")
    store = tmp_path / "store"
    arguments = ["review-cut", "--json", str(reviewed.root), "--store", str(store)]
    arguments += ["--stage", "boundaries", "--chunk", "paper"]

    code = main([*arguments, "-o", str(tmp_path / "out.json"), *OPENCODE])

    assert code == 1
    err = capsys.readouterr().err
    assert "the OpenCode host is experimental" in err
    assert "AXIOMATION_EXPERIMENTAL_OPENCODE=1 turns it on" in err
    assert not (tmp_path / "opencode.log").exists()


def test_review_cut_runs_a_mixed_round(
    reviewed, tmp_path, monkeypatch, capsys, host_scratch
) -> None:
    """The author on OpenCode, the judge on Claude Code: each
    answer records the host and model that gave it."""
    _fake_opencode_on_path(
        tmp_path, monkeypatch, text("Done."), step_finish(0.002, 300, 40)
    )
    _fake_claude_on_path(tmp_path, monkeypatch, host_scratch)
    monkeypatch.setenv(
        "FAKE_OPENCODE_EDIT",
        "Hence the output is bounded.|||\n⟦new⟧\nHence the output is bounded.",
    )
    store = tmp_path / "store"
    output = tmp_path / "corrected.json"
    arguments = ["review-cut", "--json", str(reviewed.root), "--store", str(store)]
    arguments += ["--chunk", "paper", "--judge", "-o", str(output)]

    code = main(
        [*arguments, *OPENCODE, "--judge-host", "claude-code", "--judge-rates", "1,5"]
    )

    assert code == 0, capsys.readouterr().err
    summary = json.loads(capsys.readouterr().out)
    assert summary["questions"] == {"boundaries": 1, "judge": 1}
    assert summary["complete"] is True
    answers = record_file.answers(store)
    assert [(a["stage"], a["host"], a["model"]) for a in answers] == [
        ("boundaries", "opencode 2.0.16-fake", DEEPSEEK),
        ("judge", "claude-code 2.1.285", "haiku"),
    ]
    assert reviewed.id_at("Hence the output is bounded.") in [
        b["id"] for b in json.loads(output.read_text())["blocks"]
    ]
    replayed = tmp_path / "replayed.json"
    assert main([*arguments[:-1], str(replayed)]) == 0
    assert replayed.read_bytes() == output.read_bytes()


def test_review_cut_prices_the_judge_at_its_own_rates(reviewed, tmp_path, capsys):
    arguments = [
        "review-cut",
        "--json",
        str(reviewed.root),
        "--store",
        str(tmp_path / "s"),
    ]
    arguments += ["--dry-run", "--json", "--host", "opencode", "--judge"]

    code = main([*arguments, "--judge-host", "claude-code", "--judge-rates", "0,0"])

    assert code == 0, capsys.readouterr().err
    priced = json.loads(capsys.readouterr().out)
    author, judge = priced["seats"]["boundaries"], priced["seats"]["judge"]
    assert author["host"] == "opencode" and author["model"] == DEEPSEEK
    assert judge["host"] == "claude-code" and judge["model"] == "haiku"
    assert priced["stages"]["judge"]["estimate"] == 0
    assert priced["stages"]["boundaries"]["estimate"] > 0


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        (["--judge-host", "claude-code"], "need --judge"),
        (
            ["--judge", "--judge-model", "glm"],
            "--judge-model: opencode takes <provider>/<id>",
        ),
        (["--model", "opencode/x#high"], "--model: the effort is given apart"),
        (["--judge", "--judge-host", "claude-code"], "give --judge-rates"),
    ],
)
def test_review_cut_refuses_a_judge_it_cannot_seat(
    reviewed, tmp_path, capsys, extra, message
) -> None:
    arguments = [
        "review-cut",
        "--json",
        str(reviewed.root),
        "--store",
        str(tmp_path / "s"),
    ]

    code = main([*arguments, "--dry-run", "--host", "opencode", *extra])

    assert code == 1
    assert message in capsys.readouterr().err


def test_review_cut_gives_the_effort_to_every_stage(reviewed, tmp_path, capsys) -> None:
    arguments = [
        "review-cut",
        "--json",
        str(reviewed.root),
        "--store",
        str(tmp_path / "s"),
    ]
    arguments += [
        "--dry-run",
        "--json",
        "--host",
        "opencode",
        "--judge",
        "--effort",
        "low",
    ]

    code = main([*arguments, "--judge-host", "claude-code", "--judge-rates", "1,5"])

    assert code == 0, capsys.readouterr().err
    priced = json.loads(capsys.readouterr().out)
    seats = priced["seats"]
    assert seats["boundaries"]["effort"] == seats["judge"]["effort"] == "low"


def test_review_cut_needs_rates_for_an_unknown_model(reviewed, tmp_path, capsys):
    arguments = [
        "review-cut",
        "--json",
        str(reviewed.root),
        "--store",
        str(tmp_path / "s"),
    ]
    groups = ["--host", "opencode", "--stage", "groups", "--dry-run", "--json"]

    assert (
        main(
            [*arguments, "--dry-run", "--json", "--model", "opencode-go/glm-5.3-flash"]
        )
        == 1
    )
    assert "give --rates" in capsys.readouterr().err
    assert main([*arguments, *groups, "--model", "glm"]) == 1
    assert "<provider>/<id>" in capsys.readouterr().err
    assert main([*arguments, "--dry-run", "--json", "--host", "claude-code"]) == 1
    assert "no rates are known for haiku" in capsys.readouterr().err
    assert (
        main(
            [
                *arguments,
                "--dry-run",
                "--json",
                "--model",
                "opencode-go/glm-5.3-flash",
                "--rates",
                "0,0,0",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["estimate"] == 0


def test_review_cut_runs_through_a_fake_claude(
    reviewed, tmp_path, monkeypatch, capsys, host_scratch
) -> None:
    """Each chunk a session that splits the proof's paragraph where it
    holds it, then a judge that keeps it (`--judge`); nothing but the cuts
    is asked."""
    log = _fake_claude_on_path(tmp_path, monkeypatch, host_scratch)
    monkeypatch.setenv(
        "FAKE_CLAUDE_EDIT",
        "Hence the output is bounded.|||\n⟦new⟧\nHence the output is bounded.",
    )
    store = tmp_path / "store"
    output = tmp_path / "corrected.json"

    code = main(
        [
            "review-cut",
            "--json",
            str(reviewed.root),
            "--store",
            str(store),
            *CLAUDE,
            "--chunk",
            "300",
            "--judge",
            "-o",
            str(output),
        ]
    )

    assert code == 0, capsys.readouterr().err
    summary = json.loads(capsys.readouterr().out)
    assert summary["complete"] is True
    assert summary["questions"] == {"boundaries": 3, "judge": 3}
    assert summary["spent"]["run"] == pytest.approx(0.06)
    assert len(log.read_text().splitlines()) == 6
    assert len(list((store / "transcripts").iterdir())) == 6
    answers = record_file.answers(store)
    assert {a["model"] for a in answers} == {"haiku"}
    assert {a["host"] for a in answers} == {"claude-code 2.1.285"}
    corrected = json.loads(output.read_text())
    assert corrected["review"]["complete"] is True
    assert reviewed.id_at("Hence the output is bounded.") in [
        b["id"] for b in corrected["blocks"]
    ]


def test_review_cut_stops_cleanly_when_the_credit_runs_out(
    reviewed, tmp_path, monkeypatch, capsys, host_scratch
) -> None:
    """An out-of-credit refusal is exit 3, resumable,
    and never a failed answer."""
    _fake_claude_on_path(tmp_path, monkeypatch, host_scratch)
    monkeypatch.setenv("FAKE_CLAUDE_ERROR", "Claude AI usage limit reached")
    monkeypatch.setenv("FAKE_CLAUDE_EXIT", "1")
    store = tmp_path / "store"
    arguments = [
        "review-cut",
        "--json",
        str(reviewed.root),
        "--store",
        str(store),
        *CLAUDE,
        "--chunk",
        "0",
        "--jobs",
        "1",
    ]

    assert main(arguments) == 3
    summary = json.loads(capsys.readouterr().out)
    assert summary["stopped"] == "credit"
    assert summary["failed"] == []
    assert record_file.answers(store) == []


def test_review_cut_exits_4_on_a_breach(
    reviewed, tmp_path, monkeypatch, capsys, host_scratch
):
    _fake_claude_on_path(tmp_path, monkeypatch, host_scratch)
    monkeypatch.setenv(
        "FAKE_CLAUDE_TOOL",
        json.dumps(
            {"name": "Read", "input": {"file_path": "/etc/passwd"}, "refused": False}
        ),
    )
    arguments = [
        "review-cut",
        "--json",
        str(reviewed.root),
        "--store",
        str(tmp_path / "store"),
        *CLAUDE,
        "--jobs",
        "1",
    ]

    assert main(arguments) == 4
    assert json.loads(capsys.readouterr().out)["stopped"] == "breach"


def test_review_cut_replay_refuses_a_store_without_answers(reviewed, tmp_path, capsys):
    store = tmp_path / "mistyped"

    assert (
        main(["review-cut", "--json", str(reviewed.root), "--store", str(store)]) == 1
    )

    assert "holds no answers" in capsys.readouterr().err
    assert not store.exists()


# --- the roles stage ----------------------------------------------------


def _given_cut(reviewed, path: Path) -> Path:
    """The paper's cut as an exchange file, as a key's would be: the
    theorem's role set to something no question may see."""
    payload = cut_payload(reviewed.document, reviewed.cut)
    theorem = reviewed.id_at(r"\begin{theorem}")
    for block in payload["blocks"]:
        if block["id"] == theorem:
            block["roles"] = ["claim", "justification"]
            block["segments"] = []
    path.write_text(dumps(payload), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        (["--cut", "CUT"], "--cut gives a cut whose boundaries are settled"),
        (["--stage", "both", "--cut", "CUT"], "--cut gives a cut whose"),
        (["--stage", "roles", "--cut", "OTHER"], "the payload's files are not"),
        (["--stage", "roles", "--cut", "BROKEN"], "is not JSON"),
    ],
)
def test_review_cut_refuses_a_roles_command_it_cannot_run(
    reviewed, tmp_path, capsys, extra, message
) -> None:
    given = _given_cut(reviewed, tmp_path / "cut.json")
    other = make_paper(tmp_path / "other", extra="A changed file.\n")
    (tmp_path / "other.json").write_text(
        dumps(cut_payload(other.document, other.cut)), encoding="utf-8"
    )
    (tmp_path / "broken.json").write_text("{", encoding="utf-8")
    paths = {
        "CUT": str(given),
        "OTHER": str(tmp_path / "other.json"),
        "BROKEN": str(tmp_path / "broken.json"),
    }
    extra = [paths.get(item, item) for item in extra]

    code = main(
        [
            "review-cut",
            "--json",
            str(reviewed.root),
            "--store",
            str(tmp_path / "store"),
            "--dry-run",
            *extra,
        ]
    )

    assert code == 1
    assert message in capsys.readouterr().err


def test_review_cut_runs_the_roles_session_through_a_fake_claude(
    reviewed, tmp_path, monkeypatch, capsys, host_scratch
) -> None:
    """One session on roles.md, every block made a claim; the replay is
    the same file."""
    _fake_claude_on_path(tmp_path, monkeypatch, host_scratch)
    monkeypatch.setenv("FAKE_CLAUDE_EDIT", r"^⟦(b\d+)⟧$|||⟦\1 claim⟧")
    monkeypatch.setenv("FAKE_CLAUDE_EDIT_RE", "1")
    given = _given_cut(reviewed, tmp_path / "cut.json")
    store = tmp_path / "store"
    output = tmp_path / "corrected.json"
    arguments = [
        "review-cut",
        "--json",
        str(reviewed.root),
        "--store",
        str(store),
        "--stage",
        "roles",
        "--cut",
        str(given),
        "-o",
        str(output),
    ]

    code = main([*arguments, *CLAUDE, "--chunk", "paper"])

    assert code == 0, capsys.readouterr().err
    summary = json.loads(capsys.readouterr().out)
    assert summary["questions"] == {"roles": 1} and summary["complete"] is True
    corrected = json.loads(output.read_text())
    assert {tuple(b["roles"]) for b in corrected["blocks"]} == {("claim",)}
    replayed = tmp_path / "replayed.json"
    assert main([*arguments[:-1], str(replayed), "--chunk", "paper"]) == 0
    assert replayed.read_bytes() == output.read_bytes()


def test_review_cut_inherits_the_effort_level_and_records_it(
    reviewed, tmp_path, monkeypatch, capsys, host_scratch, claude_config
) -> None:
    """The user's settings give haiku an effort level: it is passed to
    Claude Code and recorded on the answer; --effort overrides it."""
    log = _fake_claude_on_path(tmp_path, monkeypatch, host_scratch)
    monkeypatch.setenv("FAKE_CLAUDE_EDIT", r"^⟦(b\d+)⟧$|||⟦\1 claim⟧")
    monkeypatch.setenv("FAKE_CLAUDE_EDIT_RE", "1")
    (claude_config / "settings.json").write_text(
        json.dumps(
            {"effortLevel": "xhigh", "modelSettings": {"haiku": {"effortLevel": "low"}}}
        )
    )
    given = _given_cut(reviewed, tmp_path / "cut.json")
    arguments = [
        "review-cut",
        "--json",
        str(reviewed.root),
        "--stage",
        "roles",
        "--cut",
        str(given),
    ]

    assert main([*arguments, "--store", str(tmp_path / "a"), *CLAUDE]) == 0, (
        capsys.readouterr().err
    )
    assert (
        main([*arguments, "--store", str(tmp_path / "b"), *CLAUDE, "--effort", "max"])
        == 0
    )

    first, second = [json.loads(line)["argv"] for line in log.read_text().splitlines()]
    assert first[first.index("--effort") + 1] == "low"
    assert second[second.index("--effort") + 1] == "max"
    recorded = [
        record_file.answers(tmp_path / store)[0]["effort"] for store in ("a", "b")
    ]
    assert recorded == ["low", "max"]


def test_review_cut_shows_a_given_cut_s_roles_to_the_groups_alone(
    reviewed, tmp_path, capsys
) -> None:
    """The groups stage alone, on a given cut, shows the roles it carries;
    the roles stage on it shows none."""
    given = _given_cut(reviewed, tmp_path / "cut.json")
    arguments = [
        "review-cut",
        "--json",
        str(reviewed.root),
        "--cut",
        str(given),
        "--dry-run",
    ]

    for stage in ("groups", "roles"):
        prompts = tmp_path / stage
        code = main(
            [*arguments, "--store", str(tmp_path / stage / "s")]
            + ["--stage", stage, "--prompts", str(prompts)]
        )
        assert code == 0, capsys.readouterr().err
    capsys.readouterr()

    groups = (tmp_path / "groups" / "groups-1" / "paper.md").read_text()
    roles = (tmp_path / "roles" / "roles-1" / "paper.md").read_text()
    assert "⟦b5 claim, justification⟧" in groups
    assert "claim" not in roles


def test_review_cut_refuses_no_groups_pass(reviewed, tmp_path, capsys) -> None:
    arguments = [
        "review-cut",
        "--json",
        str(reviewed.root),
        "--store",
        str(tmp_path / "s"),
    ]

    assert main([*arguments, "--stage", "groups", "--group-passes", "0"]) == 1
    assert "--group-passes and --edge-passes take 1 or more" in (
        capsys.readouterr().err
    )
    assert main([*arguments, "--stage", "edges", "--edge-passes", "0"]) == 1
    assert "take 1 or more" in capsys.readouterr().err
    assert main([*arguments, "--stage", "edges", "--cut-rounds", "-1"]) == 1
    assert "--overlap and --cut-rounds 0 or more" in capsys.readouterr().err


def test_review_cut_dry_run_prices_every_groups_pass(reviewed, tmp_path, capsys):
    given = _given_cut(reviewed, tmp_path / "cut.json")
    arguments = [
        "review-cut",
        "--json",
        str(reviewed.root),
        "--store",
        str(tmp_path / "s"),
    ]
    groups = ["--stage", "groups", "--cut", str(given), "--dry-run", "--json"]

    assert main([*arguments, *groups]) == 0
    one = json.loads(capsys.readouterr().out)["stages"]["groups"]
    assert main([*arguments, *groups, "--group-passes", "2"]) == 0
    two = json.loads(capsys.readouterr().out)["stages"]["groups"]

    assert (one["questions"], two["questions"]) == (1, 2)


def test_review_cut_dry_run_prices_every_roles_pass(reviewed, tmp_path, capsys) -> None:
    given = _given_cut(reviewed, tmp_path / "cut.json")
    arguments = [
        "review-cut",
        "--json",
        str(reviewed.root),
        "--store",
        str(tmp_path / "s"),
    ]
    roles = ["--stage", "roles", "--cut", str(given), "--dry-run", "--json"]

    assert main([*arguments, *roles]) == 0
    one = json.loads(capsys.readouterr().out)["stages"]["roles"]
    assert main([*arguments, *roles, "--role-passes", "3"]) == 0
    three = json.loads(capsys.readouterr().out)["stages"]["roles"]

    assert (one["questions"], three["questions"]) == (1, 3)
    assert three["estimate"] > one["estimate"]


# --- profiles ---


#: A profile of the tests' own that mixes hosts: a DeepSeek author, an
#: Opus judge.
MIXED = """[mixed]
judge_boundaries = true
reread_marks_after_update = false
search_support_after_edges = false
place_lines_after_edges = false
ask_circles_after_edges = false
[mixed.boundaries_judge]
harness = "claude-code"
model = "claude-opus-5-5"
effort = "high"
""" + "".join(
    f'[mixed.{table}]\nharness = "opencode"\nmodel = "{DEEPSEEK}"\n'
    for table in ("boundaries", "roles", "groups", "edges")
)


def _mixed(tmp_path: Path) -> list[str]:
    """The flags that seat `MIXED`."""
    path = tmp_path / "mixed.toml"
    path.write_text(MIXED, encoding="utf-8")
    return ["--profiles", str(path), "--profile", "mixed"]


def _priced(reviewed, tmp_path, capsys, *extra: str) -> dict:
    arguments = [
        "review-cut",
        "--json",
        str(reviewed.root),
        "--store",
        str(tmp_path / "s"),
    ]
    code = main([*arguments, "--dry-run", "--json", *extra])
    assert code == 0, capsys.readouterr().err
    return json.loads(capsys.readouterr().out)


OPUS_SEAT = {
    "host": "claude-code",
    "model": "claude-opus-5-5",
    "effort": "high",
    "rates": {
        "input": 4.0,
        "output": 20.0,
        "cache_read": 0.2,
        "cache_write_1h": 8.0,
        "cache_write_5m": 5.0,
    },
}


def test_a_profile_seats_each_stage(reviewed, tmp_path, capsys) -> None:
    standard = _priced(
        reviewed, tmp_path, capsys, "--profile", "standard", "--stage", "both"
    )
    roles = _priced(
        reviewed, tmp_path, capsys, "--profile", "standard", "--stage", "roles"
    )
    mixed = _priced(reviewed, tmp_path, capsys, *_mixed(tmp_path))

    assert standard["profile"] == "standard"
    assert standard["seats"] == {"boundaries": OPUS_SEAT, "groups": OPUS_SEAT}
    assert (standard["chunk"], standard["outline"], standard["judge"]) == (
        500_000,
        False,
        False,
    )
    assert roles["seats"]["roles"]["effort"] == "xhigh"
    assert mixed["judge"] is True
    assert mixed["seats"]["boundaries"]["model"] == DEEPSEEK
    assert mixed["seats"]["judge"] == OPUS_SEAT


def test_review_cut_takes_the_paper_s_own_store_and_no_older_flag_names(
    reviewed, capsys
) -> None:
    """`--store` left out, `review-cut` reads the paper's own store, as
    `build` does; `--rounds` is no flag."""
    root = str(reviewed.root)
    assert (
        main(
            [
                "review-cut",
                "--json",
                root,
                "--profile",
                "standard",
                "--dry-run",
                "--json",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["dry_run"] is True
    with pytest.raises(SystemExit):
        main(["review-cut", "--json", root, "--dry-run", "--json", "--rounds", "2"])
    assert "unrecognized arguments: --rounds" in capsys.readouterr().err


def test_a_flag_overrides_one_value_of_a_profile(reviewed, tmp_path, capsys) -> None:
    """--model moves every stage but the judge's to another model, which
    drops the profile's effort; --outline and --judge turn the
    profile's settings over; --judge-model moves the judge alone."""
    sonnet = _priced(
        reviewed, tmp_path, capsys, "--profile", "standard", "--stage", "both",
        "--model", "claude-sonnet-5-5", "--rates", "3,15", "--outline", "--judge",
    )  # fmt: skip
    mixed = _priced(
        reviewed, tmp_path, capsys, *_mixed(tmp_path),
        "--model", "opencode-go/glm-5.3-flash", "--rates", "1,2",
    )  # fmt: skip
    judged = _priced(
        reviewed, tmp_path, capsys, *_mixed(tmp_path),
        "--judge-model", "haiku", "--judge-rates", "1,5", "--no-judge", "--judge",
    )  # fmt: skip

    seats = sonnet["seats"]
    assert (
        seats["boundaries"]["model"]
        == seats["groups"]["model"]
        == ("claude-sonnet-5-5")
    )
    assert seats["boundaries"]["effort"] is None  # none in the test's settings
    assert seats["judge"] == OPUS_SEAT  # the profile's judge keeps its agent
    assert sonnet["outline"] is True and sonnet["chunk"] == 500_000
    assert mixed["seats"]["boundaries"]["model"] == "opencode-go/glm-5.3-flash"
    assert mixed["seats"]["judge"] == OPUS_SEAT
    assert judged["seats"]["boundaries"]["model"] == DEEPSEEK
    assert judged["seats"]["judge"]["model"] == "haiku"
    assert judged["seats"]["judge"]["effort"] is None


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        (["--profile", "nope"], "no profile 'nope'; there are cheap, standard"),
        (["--profiles", "p.toml"], "--profiles needs --profile"),
        (
            ["--profile", "standard", "--model", "claude-sonnet-4-6"],
            "no rates are known for claude-sonnet-4-6; give --rates",
        ),
        (
            ["--profile", "standard", "--model", "opencode/glm"],
            "--model: claude-code takes",
        ),
        (["--profile", "cheap", "--effort", "hi gh"], "a variant's name"),
        (["--profile", "standard", "--effort", "default"], "effort takes low"),
        (["--profile", "standard", "--boundary-passes", "0"], "take 1 or more"),
    ],
)
def test_review_cut_refuses_a_profile_it_cannot_use(
    reviewed, tmp_path, capsys, extra, message
) -> None:
    arguments = [
        "review-cut",
        "--json",
        str(reviewed.root),
        "--store",
        str(tmp_path / "s"),
    ]

    assert main([*arguments, "--dry-run", *extra]) == 1
    assert message in capsys.readouterr().err


def test_a_profile_asks_and_so_needs_a_budget(reviewed, tmp_path, capsys) -> None:
    arguments = [
        "review-cut",
        "--json",
        str(reviewed.root),
        "--store",
        str(tmp_path / "s"),
    ]

    assert main([*arguments, "--profile", "cheap"]) == 1

    assert "needs --budget" in capsys.readouterr().err
    assert not (tmp_path / "s").exists()


DEEPSEEK_AGENT = 'harness = "opencode"\nmodel = "opencode/deepseek-v4.1-flash"\n'
HAIKU_AGENT = 'harness = "claude-code"\nmodel = "haiku"\neffort = "low"\n'


def _profile_text(name: str, settings: str, **agents: str) -> str:
    # the queues at a build's or an update's end are not these tests' steps,
    # and run on claude-code alone
    text = (
        f"[{name}]\n{settings}reread_marks_after_update = false\n"
        "search_support_after_edges = false\nplace_lines_after_edges = false\n"
        "ask_circles_after_edges = false\n"
    )
    # The edges are not these tests' stage: the groups' agent.
    agents.setdefault("edges", agents["groups"])
    for table in ("boundaries", "boundaries_judge", "roles", "groups", "edges"):
        text += f"[{name}.{table}]\n{agents[table]}"
    return text


JUDGED_PAPER = 'chunk_characters = "paper"\njudge_boundaries = true\n'
TWO_PROFILES = _profile_text(
    "one",
    JUDGED_PAPER,
    boundaries=DEEPSEEK_AGENT,
    boundaries_judge=HAIKU_AGENT,
    roles=DEEPSEEK_AGENT,
    groups=DEEPSEEK_AGENT,
) + _profile_text(
    "two",
    JUDGED_PAPER,
    boundaries=HAIKU_AGENT,
    boundaries_judge=DEEPSEEK_AGENT,
    roles=HAIKU_AGENT,
    groups=HAIKU_AGENT,
)


def test_the_same_run_under_two_profiles_records_each_as_it_says(
    reviewed, tmp_path, monkeypatch, capsys, host_scratch
) -> None:
    """Each answer records the harness, model and effort
    its profile gives the stage, and the profile; the replay under the
    profile is the run."""
    _fake_opencode_on_path(
        tmp_path, monkeypatch, text("Done."), step_finish(0.002, 300, 40)
    )
    _fake_claude_on_path(tmp_path, monkeypatch, host_scratch)
    profiles = tmp_path / "profiles.toml"
    profiles.write_text(TWO_PROFILES, encoding="utf-8")
    edit = "Hence the output is bounded.|||\n⟦new⟧\nHence the output is bounded."
    found = {}
    # Haiku has no price in prices.toml: its rates are given for the run.
    runs = (
        ("one", "FAKE_OPENCODE_EDIT", ["--judge-rates", "1,5"]),
        ("two", "FAKE_CLAUDE_EDIT", ["--rates", "1,5"]),
    )
    for name, author, rates in runs:
        monkeypatch.delenv("FAKE_OPENCODE_EDIT", raising=False)
        monkeypatch.delenv("FAKE_CLAUDE_EDIT", raising=False)
        monkeypatch.setenv(author, edit)
        store = tmp_path / name
        output = tmp_path / f"{name}.json"
        arguments = ["review-cut", "--json", str(reviewed.root), "--store", str(store)]
        arguments += ["--profiles", str(profiles), "--profile", name]

        code = main([*arguments, *rates, "--budget", "1", "-o", str(output)])

        assert code == 0, capsys.readouterr().err
        summary = json.loads(capsys.readouterr().out)
        assert summary["profile"] == name and summary["complete"] is True
        answers = record_file.answers(store)
        found[name] = [
            (a["stage"], a["host"], a["model"], a["effort"], a["profile"])
            for a in answers
        ]
        replayed = tmp_path / f"{name}-replayed.json"
        assert main([*arguments, "--host", "none", "-o", str(replayed)]) == 0
        assert replayed.read_bytes() == output.read_bytes()
        capsys.readouterr()

    assert found == {
        "one": [
            ("boundaries", "opencode 2.0.16-fake", DEEPSEEK, None, "one"),
            ("judge", "claude-code 2.1.285", "haiku", "low", "one"),
        ],
        "two": [
            ("boundaries", "claude-code 2.1.285", "haiku", "low", "two"),
            ("judge", "opencode 2.0.16-fake", DEEPSEEK, None, "two"),
        ],
    }


SPLIT_PROFILE = _profile_text(
    "split",
    'chunk_characters = "paper"\n',
    boundaries=DEEPSEEK_AGENT,
    boundaries_judge=DEEPSEEK_AGENT,
    roles=HAIKU_AGENT,
    groups=DEEPSEEK_AGENT,
)


def test_a_stage_runs_on_its_own_seat(
    reviewed, tmp_path, monkeypatch, capsys, host_scratch
) -> None:
    """The roles on Claude Code while the profile's boundaries are on
    OpenCode: each answer records the roles' agent; the groups, without a
    table, run on the boundaries'."""
    _fake_claude_on_path(tmp_path, monkeypatch, host_scratch)
    monkeypatch.setenv("FAKE_CLAUDE_EDIT", r"^⟦(b\d+)⟧$|||⟦\1 claim⟧")
    monkeypatch.setenv("FAKE_CLAUDE_EDIT_RE", "1")
    profiles = tmp_path / "profiles.toml"
    profiles.write_text(SPLIT_PROFILE, encoding="utf-8")
    store = tmp_path / "store"
    arguments = ["review-cut", str(reviewed.root), "--store", str(store)]
    arguments += ["--profiles", str(profiles), "--profile", "split"]
    arguments += ["--cut", str(_given_cut(reviewed, tmp_path / "cut.json"))]

    code = main(
        [*arguments, "--json", "--stage", "roles", "--rates", "1,5", "--budget", "1"]
    )

    assert code == 0, capsys.readouterr().err
    summary = json.loads(capsys.readouterr().out)
    assert summary["answered_by"] == {"split": 1} and summary["applied"] >= 1
    [answer] = record_file.answers(store)
    assert (answer["stage"], answer["host"], answer["model"], answer["effort"]) == (
        "roles",
        "claude-code 2.1.285",
        "haiku",
        "low",
    )
    groups = _priced(reviewed, tmp_path, capsys, *arguments[4:], "--stage", "groups")
    assert groups["seats"]["groups"]["model"] == DEEPSEEK


# --- the edges ------------------------------------------------------------


def _given_cut_without_claims(reviewed, path: Path) -> Path:
    """The paper's cut with no block a claim, so a sheet needs no standing:
    the fake's one edit, a done mark before every block line and end line,
    then answers it whole."""
    payload = cut_payload(reviewed.document, reviewed.cut)
    for block in payload["blocks"]:
        if "claim" in block["roles"]:
            block["roles"], block["segments"] = [], []
    path.write_text(dumps(payload), encoding="utf-8")
    return path


#: Every block and group done: a done mark before each block line and each
#: end line, by its handle.
DONE_EVERYWHERE = (
    r"^(⟦(?:end group )?(?!…)([^\s⟧]+)(?:⟧| [^⟧]*⟧ \S+ \(\w+\)))$"
    r"|||⟦done \2⟧\n\1"
)


def test_review_cut_dry_run_prices_the_edges_on_a_given_cut(
    reviewed, tmp_path, capsys
) -> None:
    """`--stage edges --cut` keeps the given cut's roles and groups."""
    given = _given_cut(reviewed, tmp_path / "cut.json")
    prompts = tmp_path / "prompts"

    priced = _priced(
        reviewed,
        tmp_path,
        capsys,
        *["--stage", "edges", "--cut", str(given), "--host", "claude-code"],
        *["--rates", "1,5", "--prompts", str(prompts)],
    )

    assert priced["stages"]["edges"]["questions"] == 1
    assert priced["stages"]["edges"]["estimate"] > 0
    assert set(priced["seats"]) == {"edges"}
    sheet = (prompts / "edges-1" / "edges.md").read_text()
    assert "⟦thm:bound claim+justification⟧" in sheet  # the given roles
    assert "⟦group group:by-definition-def:gain-the proof⟧" in sheet
    assert "Files: extra.tex, main.tex" in sheet


def test_review_cut_runs_the_edges_on_a_fake_claude_and_replays_them(
    reviewed, tmp_path, monkeypatch, capsys, host_scratch
) -> None:
    _fake_claude_on_path(tmp_path, monkeypatch, host_scratch)
    monkeypatch.setenv("FAKE_CLAUDE_EDIT", DONE_EVERYWHERE)
    monkeypatch.setenv("FAKE_CLAUDE_EDIT_RE", "1")
    given = _given_cut_without_claims(reviewed, tmp_path / "cut.json")
    store, output = tmp_path / "store", tmp_path / "graph.json"
    arguments = ["review-cut", "--json", str(reviewed.root), "--store", str(store)]
    arguments += ["--stage", "edges", "--cut", str(given), "-o"]

    code = main([*arguments, str(output), *CLAUDE])

    assert code == 0, capsys.readouterr().err
    summary = json.loads(capsys.readouterr().out)
    assert summary["complete"] is True
    assert summary["questions"] == {"edges": 1}
    assert summary["edges"] == {
        "entries": 0,
        "edges": 0,
        "self_marks": 0,
        "standings": 0,
        "frame": 0,
    }
    assert summary["edges_per_pass"] == [summary["edges"]]
    (answer,) = record_file.answers(store)
    assert answer["valid"] and answer["stage"] == "edges"
    graph = json.loads(output.read_text())
    assert graph["edges"] == [] and graph["handles"]
    replayed = tmp_path / "replayed.json"
    assert main([*arguments, str(replayed), "--host", "none"]) == 0
    assert replayed.read_bytes() == output.read_bytes()


def test_review_cut_reports_an_edge_half_it_cannot_write(
    reviewed, tmp_path, monkeypatch, capsys, host_scratch
) -> None:
    """An export error is the command's error, not a traceback."""
    from axiomation.commands import build
    from axiomation.exchange import ExchangeError

    def refuse(*_args, **_kwargs):
        raise ExchangeError("the edge half: b1: 'x' is not in one run")

    _fake_claude_on_path(tmp_path, monkeypatch, host_scratch)
    monkeypatch.setenv("FAKE_CLAUDE_EDIT", DONE_EVERYWHERE)
    monkeypatch.setenv("FAKE_CLAUDE_EDIT_RE", "1")
    monkeypatch.setattr(build, "review_payload", refuse)
    given = _given_cut_without_claims(reviewed, tmp_path / "cut.json")
    arguments = [
        "review-cut",
        "--json",
        str(reviewed.root),
        "--store",
        str(tmp_path / "s"),
    ]
    arguments += ["--stage", "edges", "--cut", str(given), "-o", str(tmp_path / "g")]

    assert main([*arguments, *CLAUDE]) == 1
    assert capsys.readouterr().err == (
        "axiomation review-cut: the edge half: b1: 'x' is not in one run\n"
    )


# --- the whole build ------------------------------------------------------


def test_a_build_s_dry_run_reads_as_lines_and_as_json(
    reviewed, tmp_path, capsys
) -> None:
    """A dry run says a line a stage, its questions, price and seat, then
    the estimate; `--json` gives the whole pricing, the same estimate."""
    arguments = ["build", str(reviewed.root), "--store", str(tmp_path / "s")]
    assert main([*arguments, "--profile", "standard", "--dry-run"]) == 0
    lines = capsys.readouterr().out.splitlines()
    priced = _build_priced(reviewed, tmp_path, capsys, "--profile", "standard")

    assert lines[0] == "priced, nothing asked (profile standard):"
    for stage, found in priced["stages"].items():
        line = next(x for x in lines if x.lstrip().startswith(stage + " "))
        assert f"{found['questions']} question" in line
        assert priced["seats"][stage]["model"] in line
    assert lines[-1].startswith(f"estimate ${priced['estimate']:.2f}, ")


def _build_priced(reviewed, tmp_path, capsys, *extra: str) -> dict:
    arguments = ["build", "--json", str(reviewed.root), "--store", str(tmp_path / "s")]
    code = main([*arguments, "--dry-run", "--json", *extra])
    assert code == 0, capsys.readouterr().err
    return json.loads(capsys.readouterr().out)


def test_build_prices_every_stage_and_every_edges_pass(
    reviewed, tmp_path, capsys
) -> None:
    prompts = tmp_path / "prompts"

    priced = _build_priced(
        reviewed,
        tmp_path,
        capsys,
        *["--profile", "standard", "--edge-passes", "2", "--prompts", str(prompts)],
    )

    # the paper has no name its tables do not know: the map asks nothing,
    # though its two seats are taken before the paper is read
    assert list(priced["stages"]) == ["boundaries", "roles", "groups", "edges"]
    assert set(priced["seats"]) == {
        "vocabulary",
        "vocabulary-check",
        "boundaries",
        "roles",
        "groups",
        "edges",
    }
    assert priced["stages"]["edges"]["questions"] == 2
    assert all(stage["estimate"] > 0 for stage in priced["stages"].values())
    assert (prompts / "edges-2" / "edges.md").exists()


def test_build_prices_each_round_of_cut_corrections(reviewed, tmp_path, capsys) -> None:
    once = _build_priced(reviewed, tmp_path, capsys, "--profile", "standard")
    rounds = _build_priced(
        reviewed, tmp_path, capsys, "--profile", "standard", "--cut-rounds", "2"
    )

    # Each round: one re-ask of the edges and one roles session.
    for stage in ("edges", "roles"):
        assert (
            rounds["stages"][stage]["questions"]
            == once["stages"][stage]["questions"] + 2
        )
        assert rounds["stages"][stage]["estimate"] > once["stages"][stage]["estimate"]


def test_build_on_a_given_cut_starts_at_its_roles(reviewed, tmp_path, capsys) -> None:
    given = _given_cut(reviewed, tmp_path / "cut.json")

    priced = _build_priced(
        reviewed, tmp_path, capsys, "--profile", "standard", "--cut", str(given)
    )

    assert list(priced["stages"]) == ["roles", "groups", "edges"]
    # standard's two edges passes, the second by target
    assert priced["stages"]["edges"]["questions"] == 2


def test_build_has_no_stage_flag(reviewed, tmp_path, capsys) -> None:
    arguments = ["build", "--json", str(reviewed.root), "--store", str(tmp_path / "s")]

    with pytest.raises(SystemExit):
        main([*arguments, "--stage", "edges"])
    assert "unrecognized arguments: --stage" in capsys.readouterr().err


def test_build_replays_a_store_and_names_itself_in_errors(
    reviewed, tmp_path, capsys
) -> None:
    arguments = ["build", "--json", str(reviewed.root), "--store", str(tmp_path / "s")]

    assert main([*arguments, "--host", "none"]) == 1
    assert capsys.readouterr().err.startswith("axiomation build: --host none replays")


def test_build_without_a_profile_or_a_host_asks_under_standard(
    reviewed, tmp_path, capsys
) -> None:
    arguments = ["build", "--json", str(reviewed.root), "--store", str(tmp_path / "s")]

    assert main(arguments) == 1
    assert "build asks under the standard profile by default, and needs --budget" in (
        capsys.readouterr().err
    )
    priced = _build_priced(reviewed, tmp_path, capsys)
    assert priced["profile"] == "standard"
    assert main([*arguments, "--profiles", str(tmp_path / "mine.toml")]) == 1
    assert "--profiles needs --profile" in capsys.readouterr().err


def test_build_prices_the_deciding_step_and_what_it_asks_again(
    reviewed, tmp_path, capsys
) -> None:
    once = _build_priced(reviewed, tmp_path, capsys, "--profile", "standard")
    deciding = _build_priced(
        reviewed, tmp_path, capsys, "--profile", "standard", "--decide-passes", "1"
    )

    assert "decide" not in once["stages"] and "decide" not in once["seats"]
    assert deciding["stages"]["decide"]["questions"] == 1
    assert deciding["stages"]["decide"]["estimate"] > 0
    assert deciding["seats"]["decide"] == deciding["seats"]["boundaries"]
    for stage in ("roles", "groups"):
        assert (
            deciding["stages"][stage]["questions"]
            == once["stages"][stage]["questions"] + 1
        )


def test_review_cut_refuses_a_second_deciding_pass(reviewed, tmp_path, capsys) -> None:
    arguments = [
        "review-cut",
        "--json",
        str(reviewed.root),
        "--store",
        str(tmp_path / "s"),
    ]

    assert main([*arguments, "--stage", "groups", "--decide-passes", "2"]) == 1
    assert "--decide-passes takes 0 or 1" in capsys.readouterr().err


# --- the vocabulary map in a build ---------------------------------------------------


def _store_holding_an_answer(store: RecordStore) -> RecordStore:
    """A store that holds an answer, though none to the question a test
    leaves unanswered: a replay needs a store with answers."""
    store.append(
        AnswerRecord(
            key="old-answer",
            kind="cut-review",
            stage="boundaries",
            template=TemplateRef("cut-review-boundaries", "0123456789ab"),
            attempt=1,
            host="opencode",
            model="m",
            time="2026-09-29T12:00:00Z",
            seconds=1.0,
            cost=0.0,
            tokens={},
            raw="",
            valid=False,
            problems=("a stand-in for OLD's answers",),
            records=(),
        )
    )
    return store


def _framed_paper(directory: Path) -> Path:
    directory.mkdir()
    (directory / "paper.md").write_text(
        "---\ntitle: A box\nheader-includes:\n"
        "  - \\newenvironment{lawbox}{\\hrule}{\\hrule}\n---\n\n"
        "\\begin{lawbox}\nOne law.\n\nIts reading.\n\\end{lawbox}\n",
        encoding="utf-8",
    )
    return directory


def _replayed_build(paper: Path, store: Path, output: Path) -> int:
    return main(
        [
            "build",
            "--json",
            str(paper),
            "--host",
            "none",
            "--store",
            str(store),
            "-o",
            str(output),
        ]
    )


def test_a_build_whose_map_is_unanswered_stops_before_the_cut(tmp_path, capsys) -> None:
    paper = _framed_paper(tmp_path / "paper")
    store = _store_holding_an_answer(RecordStore(tmp_path / "store"))
    output = tmp_path / "graph.json"

    code = _replayed_build(paper, store.directory, output)

    assert code == Exit.UNANSWERED
    summary = json.loads(capsys.readouterr().out)
    assert summary["complete"] is False
    assert len(summary["vocabulary"]["unanswered"]) == 1
    assert not output.exists()


def test_a_build_reads_the_paper_with_its_answered_map(tmp_path, capsys) -> None:
    paper = _framed_paper(tmp_path / "paper")
    store = RecordStore(tmp_path / "store")
    container = (
        '- kind: container\n- evidence: "\\newenvironment{lawbox}{\\hrule}{\\hrule}"'
    )
    host = FakeHost(
        lambda prompt: "",
        edits={
            "vocabulary.md": lambda t: t.replace("- kind: ?\n- evidence: ?", container)
        },
    )
    runner = Runner(host, Resolver(store), 1.0, Rates(0.3, 1.2))
    map_vocabulary(read_paper(paper), store, Templates.shipped(), runner)
    output = tmp_path / "graph.json"

    _replayed_build(paper, store.directory, output)

    exported = json.loads(output.read_text(encoding="utf-8"))
    assert exported["document"]["vocabulary"]["environments"] == [
        {"name": "lawbox", "kind": "container", "base": None}
    ]
    assert [env["kind"] for env in exported["document"]["environments"]] == [
        "container"
    ]
    assert "vocabulary-file" in exported["review"]["templates"]


def test_review_cut_reads_the_paper_with_its_store_s_map(tmp_path, capsys) -> None:
    paper = _framed_paper(tmp_path / "paper")
    store = RecordStore(tmp_path / "store")
    container = (
        '- kind: container\n- evidence: "\\newenvironment{lawbox}{\\hrule}{\\hrule}"'
    )
    host = FakeHost(
        lambda prompt: "",
        edits={
            "vocabulary.md": lambda t: t.replace("- kind: ?\n- evidence: ?", container)
        },
    )
    runner = Runner(host, Resolver(store), 1.0, Rates(0.3, 1.2))
    map_vocabulary(read_paper(paper), store, Templates.shipped(), runner)
    output = tmp_path / "graph.json"

    main(
        [
            "review-cut",
            "--json",
            str(paper),
            "--store",
            str(store.directory),
            "--stage",
            "boundaries",
            "-o",
            str(output),
        ]
    )

    exported = json.loads(output.read_text(encoding="utf-8"))
    assert [env["kind"] for env in exported["document"]["environments"]] == [
        "container"
    ]


class _Captured(Exception):
    pass


def test_an_update_reads_new_with_its_map_and_old_with_its_export_s(
    tmp_path, capsys, monkeypatch
) -> None:
    paper = _framed_paper(tmp_path / "paper")
    store = RecordStore(tmp_path / "store")
    container = (
        '- kind: container\n- evidence: "\\newenvironment{lawbox}{\\hrule}{\\hrule}"'
    )
    host = FakeHost(
        lambda prompt: "",
        edits={
            "vocabulary.md": lambda t: t.replace("- kind: ?\n- evidence: ?", container)
        },
    )
    runner = Runner(host, Resolver(store), 1.0, Rates(0.3, 1.2))
    mapped = map_vocabulary(read_paper(paper), store, Templates.shipped(), runner)
    old = read_paper(paper, vocabulary=mapped.vocabulary)
    export = tmp_path / "old.json"
    export.write_text(
        dumps(cut_payload(old, cut(old, CutRules(DisplayRule.IN_PARAGRAPH)))),
        encoding="utf-8",
    )
    seen = {}

    def identity(old_document, old_cut, exported, document, *args, **kwargs):
        seen["old"], seen["new"] = old_document, document
        raise _Captured

    monkeypatch.setattr("axiomation.commands.update.made_identity", identity)

    with pytest.raises(_Captured):
        main(
            [
                "update",
                "--json",
                str(paper),
                "--store",
                str(store.directory),
                "--from",
                str(paper),
                "--cut",
                str(export),
                "--host",
                "none",
            ]
        )

    for side in ("new", "old"):
        assert [env.kind.value for env in seen[side].environments] == ["container"]


def test_an_update_whose_map_is_unanswered_stops_before_identity(
    tmp_path, capsys
) -> None:
    paper = _framed_paper(tmp_path / "paper")
    store = _store_holding_an_answer(RecordStore(tmp_path / "store"))

    code = main(
        [
            "update",
            "--json",
            str(paper),
            "--store",
            str(store.directory),
            "--host",
            "none",
        ]
    )

    assert code == Exit.UNANSWERED
    summary = json.loads(capsys.readouterr().out)
    assert len(summary["vocabulary"]["unanswered"]) == 1
    assert not any(a.kind == "identity" for a in store.answers())


def test_a_command_given_no_paper_reads_the_one_it_is_run_in(
    tmp_path, monkeypatch, capsys
) -> None:
    """The paper's directory left out: the paper the current directory is
    in, found by walking up; a workspace's paper, or a paper of its own
    (its record, or its `axiomation.toml`); else the directory itself, as
    said. A command whose positionals are all required leaves the paper
    out by their count, so a word holding a path or naming a directory is
    its own (`run`'s key, `receive`'s receipt, a handle); one with a list
    by its first word. Two papers are given as they are; at a workspace's
    root, refused naming its papers."""
    workspace = tmp_path / "workspace"
    paper = workspace / "papers" / "p"
    (paper / "notes").mkdir(parents=True)
    (paper / "appendix").mkdir()
    (workspace / "axiomation.toml").write_text("[workspace]\n", encoding="utf-8")
    loose = tmp_path / "loose"
    (loose / "axiomation").mkdir(parents=True)
    (loose / "axiomation" / "record.jsonl").write_text("", encoding="utf-8")
    (loose / "notes").mkdir()
    commands = parser()

    def given(*argv: str) -> list[str]:
        return with_paper(commands, list(argv)).argv

    monkeypatch.chdir(paper / "notes")
    here = str(paper.resolve())
    assert given("show", "n2", "--json") == ["show", here, "n2", "--json"]
    assert given("support", "b1", "b2") == ["support", here, "b1", "b2"]
    assert given("check", "--state", "abc")[1] == here
    assert given("show", str(paper), "n2") == ["show", str(paper), "n2"]
    # the positional words are read past the options and their values
    assert given("read", "--root", "main.tex", "../p") == [
        "read",
        "--root",
        "main.tex",
        "../p",
    ]
    for store in (["--store=a/b"], ["--store", "a/b"]):
        assert given("check", *store) == ["check", here, *store]
    assert given("show", "--state", "abc", "n2")[1] == here
    # a word holding a path, or naming a directory, is the command's own
    assert given("run", "computations/constant.py")[1:] == [
        here,
        "computations/constant.py",
    ]
    assert given("run", "computations/c.py", "--", "x/y")[1] == here
    assert given("receive", "/tmp/r.receipt", "--journal", "c")[1] == here
    monkeypatch.chdir(paper)
    assert given("show", "appendix") == ["show", here, "appendix"]
    # a paper given with the word after it forgotten stays the paper
    other = workspace / "papers" / "q"
    other.mkdir()
    (other / "axiomation.toml").write_text("", encoding="utf-8")
    assert given("show", str(other)) == ["show", str(other)]
    # a mistyped path given with the block stays the paper, refused as such
    assert given("show", "papers/q", "n2") == ["show", "papers/q", "n2"]
    assert given("compare", "a", "b") == ["compare", "a", "b"]

    monkeypatch.chdir(loose / "notes")
    assert given("check") == ["check", str(loose.resolve())]
    assert with_paper(commands, ["check"]).guessed is None

    monkeypatch.chdir(tmp_path)
    assert with_paper(commands, ["check"]).guessed == tmp_path.resolve()
    capsys.readouterr()
    main(["states"])
    assert "no paper above here" in capsys.readouterr().err

    monkeypatch.chdir(workspace)
    with pytest.raises(PaperNotFound, match="its papers: papers/p"):
        with_paper(commands, ["check"])
    assert given("check", "-h") == ["check", "-h"]
    assert main(["check"]) == 1
