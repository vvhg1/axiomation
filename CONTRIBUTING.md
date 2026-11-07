# Contributing

Pull requests and issues are welcome.

## Set up

Python 3.12 or later, [uv](https://docs.astral.sh/uv/), Node 22 or
later on `PATH` for the viewer's tests, and the `tesseract` binary for
the ingest's ([the guide](docs/guide.md) § 1 names the install):

```sh
uv sync          # the package, the `library` extra and the dev tools
make check       # ruff and the offline suite
make coverage    # the suite, with each module's line coverage
```

`uv tool install --editable .` puts the checkout's `axiomation` on
`PATH`. `make test-core` runs the suite without the `library` extra. The suite is
offline: no network and no model calls. A test that needs a live service
carries the `live` marker, and the default run deselects it. CI runs the
suite on every pull request, on Linux and macOS
(`.github/workflows/ci.yml`).

## A change

- **One change a pull request.** It is squash-merged, so its title becomes
  the commit message on `main`: say what the change does, in the
  imperative ("show a claim's reach in the viewer").
- **Docs ship with the change.** A change to the model, a command or its
  flags updates `docs/` and `README.md` in the same pull request;
  `tests/test_docs.py` checks that their commands, flags and closed sets
  match the code. A change a user sees adds a line to `CHANGELOG.md`,
  under the next version at the top (`## 0.1.1 (unreleased)`, added if
  the top one is dated).
- **Tests quote no paper.** A fixture is written for its test, in
  words of our own, or is one of the two papers of our own in
  `tests/fixtures/papers/`. Never copy a published paper's text into a
  test, even one under an open licence (`AGENTS.md` § "The fixture
  rule").
- **Comments state the reason, in the present.** What the code does and
  why; the history of a change belongs in its commit message.

## Agents

An agent working in this repository reads [AGENTS.md](AGENTS.md): the
layout, the code contracts, the docs to keep in step with the code and
the fixture rule.
Changes to `AGENTS.md` go through a pull request like any other.

Put your own instructions in `AGENTS.local.md`. Git ignores it, and
`AGENTS.md` tells agents to read it. Avoid `CLAUDE.md`,
`CLAUDE.local.md` and `AGENTS.override.md` in this repository: Claude
Code stops reading `AGENTS.md` when it finds a `CLAUDE.md` or
`CLAUDE.local.md`, and Codex reads `AGENTS.override.md` instead of it.

## Issues

A bug report names the command, the paper's format (LaTeX or Markdown),
what you expected and what happened. Leave out any paper you may not
share: a few synthetic lines that show the same behavior are best. The
issue form asks for each of these. Report a security issue privately
([SECURITY.md](SECURITY.md)).

## License

By contributing, you agree that your contribution is licensed under the
Apache License 2.0, the license of this repository ([LICENSE](LICENSE)).
