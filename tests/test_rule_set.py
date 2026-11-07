"""A person's ruling on a set (`rule --in NAMES --role ROLE`): one ruling
on each claim standing unsupported that the names and roles select, in
one filing, each guarded as a single ruling is (docs/model.md § 8).

On `test_check`'s synthetic paper: `c`, `x` and `y` stand unsupported,
`s` uses `c`, `t` stands justified. No model is called.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from axiomation.cli import main
from axiomation.records import RecordStore, RulingKind
from test_check import PERSON, TEXT, built, payload
from test_rulings import ruled
from test_versions import V1, keep


@pytest.fixture
def base(tmp_path: Path) -> list[str]:
    store, _ = built(tmp_path)
    paper = tmp_path / "paper"
    paper.mkdir()
    return [str(paper), "--store", str(store.directory)]


def rulings_in(base: list[str]):
    return RecordStore(Path(base[2])).judgment_records().rulings


def rule(base: list[str], *given: str, reason: str = "kept short") -> list[str]:
    return ["rule", *base, *given, "--reason", reason]


def test_one_filing_rules_each_claim_and_lists_what_a_guard_holds_back(
    base, capsys
) -> None:
    assert main(rule(base, "--in", "c,x", "--ruling", "left-unargued")) == 0
    out = capsys.readouterr().out
    found = rulings_in(base)
    assert sorted(r.subject.words for r in found) == [
        "Clearly f is extra.",
        "Surely f is finite.",
    ]
    assert {r.reason for r in found} == {"kept short"}
    assert len({r.id.rsplit("-", 1)[0] for r in found}) == 1
    assert "2 rulings left-unargued at state" in out
    assert "held back by a guard, clearing nothing (1):" in out
    assert 'c "Surely f is finite.": relied-on, relied on by s' in out
    assert "unsupported-claims −1 +0" in out


def test_a_name_with_no_unsupported_claim_is_refused_and_one_naming_nothing_stops(
    base, capsys
) -> None:
    assert main(rule(base, "--in", "x,t", "--ruling", "known")) == 0
    out = capsys.readouterr().out
    assert "1 ruling known" in out
    assert "refused, no claim standing unsupported: t" in out
    assert main(rule(base, "--in", "no-such-block", "--ruling", "known")) == 1
    assert "'no-such-block' selects no block" in capsys.readouterr().err
    owed = ("--ruling", "owed", "--severity", "2", "--fix", "argue it")
    assert main(rule(base, "--in", " x ", *owed)) == 0
    assert len(rulings_in(base)) == 2


def test_a_claim_ruled_so_already_is_left_and_another_ruling_is_replaced(
    tmp_path, capsys
) -> None:
    store, kept = built(tmp_path)
    ruled(store, kept, "x", RulingKind.OWED, PERSON)
    ruled(store, kept, "y", RulingKind.KNOWN, PERSON)
    base = [str(tmp_path), "--store", str(store.directory)]
    assert main(rule(base, "--in", "x,y", "--ruling", "known")) == 0
    out = capsys.readouterr().out
    assert "1 ruling known" in out
    assert 'x "Clearly f is extra.": your owed' in out
    assert "ruled known by you already, left as is (1):" in out
    assert 'y "Plainly f is young."' in out
    assert main(rule(base, "--in", "x,y", "--ruling", "known")) == 0
    assert "each ruled so by you already" in capsys.readouterr().out


def test_a_role_selects_the_claims_whose_block_carries_it(tmp_path, capsys) -> None:
    data = payload()
    for b in data["blocks"]:
        if b["id"] in ("x", "c"):
            b["roles"] = ["claim", "motivation"]
    store = RecordStore(tmp_path / "store")
    keep(store, V1, TEXT, data)
    base = [str(tmp_path), "--store", str(store.directory)]
    assert main(rule(base, "--role", "motivation", "--ruling", "known")) == 0
    assert sorted(r.subject.words for r in rulings_in(base)) == [
        "Clearly f is extra.",
        "Surely f is finite.",
    ]
    capsys.readouterr()
    given = rule(
        base,
        "--in",
        "x,y",
        "--role",
        "motivation",
        "--ruling",
        "owed",
        "--severity",
        "1",
        "--fix",
        "cut it",
    )
    assert main(given) == 0
    assert [r.kind for r in rulings_in(base)][-1:] == [RulingKind.OWED]
    assert len(rulings_in(base)) == 3


def test_a_dry_run_files_nothing(base, capsys) -> None:
    given = rule(base, "--in", "x,y", "--ruling", "known", "--dry-run")
    assert main(given) == 0
    out = capsys.readouterr().out
    assert "2 rulings known" in out and "dry run: nothing was filed" in out
    assert rulings_in(base) == ()


@pytest.mark.parametrize(
    ("given", "refusal"),
    [
        (
            [
                "--in",
                "x,y",
                "--ruling",
                "known",
                "--agent",
                "m",
                "--agent-session",
                "s",
            ],
            "a ruling on a set is a person's",
        ),
        (["--in", "x", "--on", "x", "--ruling", "known"], "no --on or --claim"),
        (["--in", "x"], "names its --ruling"),
        (["--on", "x", "--ruling", "known", "--dry-run"], "a ruling on a set's"),
        (["--withdraw", "abc", "--in", "x"], "--withdraw names the ruling"),
        (["--in", "", "--ruling", "known"], "--in names nothing"),
        (["--in", ",", "--ruling", "known"], "--in names nothing"),
        (
            ["--in", "x", "--ruling", "known", "--agent", "m", "--agent-session", "s"],
            "a ruling on a set is a person's",
        ),
    ],
)
def test_a_set_ruling_is_refused_where_it_is_not_one(
    base, capsys, given: list[str], refusal: str
) -> None:
    assert main(rule(base, *given)) != 0
    assert refusal in capsys.readouterr().err
    assert rulings_in(base) == ()


def test_a_role_that_selects_none_of_a_name_s_claims_says_so(base, capsys) -> None:
    given = rule(base, "--in", "x", "--role", "assumption", "--ruling", "known")
    assert main(given) == 0
    assert (
        "refused, no claim standing unsupported in a block of the roles given: x"
        in capsys.readouterr().out
    )


def test_a_dry_run_s_json_says_it_is_one(base, capsys) -> None:
    given = rule(base, "--in", "x", "--ruling", "known", "--dry-run", "--json")
    assert main(given) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["dry_run"] is True and len(data["records"]) == 1
    assert rulings_in(base) == ()
