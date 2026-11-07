# axiomation: agent instructions

This file is authoritative for every agent working in this repository.
Where `AGENTS.local.md` exists beside it, read it too: it holds the
instructions of the person you work for, kept out of Git. Where the two disagree on a
rule of the project, this file wins.

## Start here

axiomation builds the argument graph of a given paper, automatically,
and keeps it current as the paper changes. The design is `docs/model.md`
(the model) and `docs/architecture.md` (the architecture); consult the
sections the task touches. `docs/guide.md` is how the product is used.

## Layout

```text
README.md                   what axiomation is, install, first commands
CONTRIBUTING.md             for people: set up, a change, issues
CHANGELOG.md                what each release changes, for users
docs/model.md               the model: blocks, roles, edges, standings, change, kept states, judgments
docs/architecture.md        the components, the question contract, hosts, stores, the exchange format
docs/guide.md               using it on a paper, for a person or an agent
docs/ocr.md                 the OCR contract for the cited works
examples/weak-law/          the quick start's paper, with faults on purpose
examples/README.md          what a run should find; outside the paper, so no agent reads it
src/axiomation/
  reader/                   a paper's files to the document layer
  cutter/                   the document layer to proposed blocks, parts, groups
  questions/                each stage's question: its file, template, check and gate
  runner/                   the question runner and its hosts (Claude Code, OpenCode)
  queue/                    a question as a queue of tasks; its tool server, MCP over stdio on the standard library
  sources/                  the cited works: corpus, providers, ingest; `library/` the `library` extra
  viewer/                   the read-only page over a kept state
  commands/                 the commands as functions, one module per family; output through `Say`, never printed
  cli.py                    the parser and the dispatch, nothing else
  driver.py                 the build: every stage in order
  update.py                 an edit carried onto the graph
  resolver.py               answers and filings to records: the store's only writer
  records.py                the record store's kinds
  versions.py               the kept states
  review.py                 the review sessions
  judgments.py              judgments, findings, the lapse
  check.py                  the gates: whether the paper passes
  settings.py               the paper's settings, `axiomation.toml`
  user_settings.py          the person's settings, `config.toml`; never in a paper's file
  settings_templates/       the settings files' templates `init` writes, every key commented out
  profiles.toml             per stage: harness, model, effort, template
  prices.toml               each model's price, for estimates only
tests/                      offline; a folder per component as in src/
tests/runner/               against fake hosts: no real host is reachable
tests/sources/              a module needing the `library` extra skips without it
tests/fixtures/papers/      two papers of our own (§ "The fixture rule")
.github/workflows/          CI on every push and pull request; release on a `vX.Y.Z` tag
```

Every other module's docstring says what it holds; `rg --files src` lists
them. `docs/architecture.md` names the components and the direction of
dependence between them (§ 1). A change that adds a folder or a new entry
point adds its line here.

## Tooling and code contracts

Python runs through uv: `uv run python ...`, `uv run pytest`. Never bare
`python`. Add dependencies with `uv add` or `uv add --dev`; the lockfile is
checked in. The core's runtime dependencies are `cryptography`, for the record's
cipher (`keys.py`), and the cited works' PDF libraries (pypdfium2, pypdf,
fontTools, Pillow, bibtexparser); the `library` extra (Qdrant, Voyage,
fastembed, pytesseract) is the cited works' corpus in Qdrant and the
ingest's OCR, and the dev group installs it.

The code contracts are `docs/architecture.md`'s: cite its sections, do not
restate them. A comment states its reason itself, in the present: what
the code does and why, never how it came to be. History belongs in the
commit message.

### Coding preferences

- Organize code by responsibility, never by size.
- Prefer refactor over rewrite. Fix in place, then refactor.
- Prefer data classes over dicts and enums over magic strings. Use protocols
  only where a second implementation exists or is planned.

## Docs ship with the change

Update affected documentation in the same diff:

- `AGENTS.md`: these instructions, including the layout.
- `docs/model.md` and `docs/architecture.md`: the model and the contracts,
  edited in place; `README.md` where install or first use changes.
  `tests/test_docs.py` holds their closed sets, commands, flags and links
  to the code.
- `docs/guide.md`: how a person or an agent uses it, where a command's
  workflow changes.
- `CHANGELOG.md`: what the change means for a user, where a user sees it.

The design lives in `docs/`: what holds now, each fact once, no history
or status. A change to the design edits `docs/` in place. Cite the docs
by file and section (`docs/model.md` § 5) instead of restating them.

Flag outdated instructions or documentation when you find them.

## Testing

`make test` is the offline suite, `make lint` is ruff, `make check` is both.
`make test-core` runs the suite in a throwaway environment without the
`library` extra: the core passes alone, a test that needs the extra skips.
`make coverage` is the suite with each module's line coverage: a report,
which CI shows in its summary, and no threshold.

- `uv run pytest` is offline: no network, no model calls. A test that needs
  a live service carries the `live` marker; the default run deselects it.
- Fixtures are synthetic, under the fixture rule below; never a paper's
  text, and never a working paper repository.
- Review golden-output changes deliberately; never regenerate them blindly.

### The fixture rule

A fixture holds no paper's words, so it carries no licence or
attribution:

- **Synthetic.** A case is written for its test, in words of our own: the
  smallest paper that shows the construction under test (a layout, a
  macro, a way of citing). One met in a real paper is rewritten, its
  structure kept and its words replaced.
- **Whole papers of our own.** `tests/fixtures/papers/` holds two papers,
  each a modern rewrite of a public-domain result in our own notation
  and structure, no source text copied: `euler-primes` (LaTeX, several
  files) and `gauss-agm` (Markdown), written apart so each carries the
  constructions its format meets. A construction the reader (the
  parser, `reader/`) or the cutter newly meets goes into one of them
  where it fits, as well as into its own case.
- **Mocks test deterministic code only.** The reader, the cutter, the
  resolver and the assembler are tested with expected outputs. What an
  agent should find is not tested here: the suite tests what code
  decides, never a model's answer.
