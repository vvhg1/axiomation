# Using axiomation on a paper

For a person, or an agent, working in a paper's repository: how to set
axiomation up there, and the commands for each task, in the order a paper
meets them. What a term means is [the model](model.md); each command's
flags are its `--help` (`axiomation <command> --help`). The examples run
on the example paper, `examples/weak-law` ([what a run
finds](../examples/README.md)): built, reviewed, and updated after one
edit.

**Terms**, each defined where its link points:

- **paper**: a directory holding a LaTeX or Markdown root file and what
  it includes (§ 1).
- **store**: the record axiomation keeps beside the paper, in its
  `axiomation/` directory (§ 1).
- **block**: the argument's unit, the smallest span of the paper with
  one verdict: a paragraph, a theorem's statement, a proof, a display
  ([the model](model.md) § 2).
- **state**: the graph kept for one version of the paper; a command
  that reads takes the latest unless told otherwise ([the model](model.md)
  § 7).
- **standing**: how a claim stands toward support: `justified`, `self`,
  `restated`, `conjectured` or `unsupported` ([the model](model.md) § 4).
- **judgment**: a conclusion filed on a subject (is the claim true, is
  the proof valid); a **reading** is one judgment of one subject, and a
  **finding** is a problem with a fix ([the model](model.md) § 8).
- **subject**: what one judgment is about: a block under one aspect
  (`true`, `valid`, `cited`, …) ([the model](model.md) § 8).
- **speaks**: a filing speaks, is in force, while it is its author's
  latest on its subject and has neither lapsed nor been withdrawn.
- **lapse**: a judgment lapses when what it rested on changed; a **stale
  mark** is a line an edit may have made wrong ([the model](model.md)
  §§ 5, 8).
- **sheet**: a stage's lines as a file, to read and to edit (§ 8); each
  line is written under its **owner**, the block it belongs to.
- **ask**: put a question to an agent, a paid model call.
- **session**: one agent's run on one kind of question.
- **profile**: which agent, model and effort answers each stage; the
  **host** is the program that runs the agents, Claude Code.
- **gate**: one condition `check` holds the paper to (no unsupported
  claim, no open defect, …) (§ 6).

## 1. Set up

**A paper** is a directory holding a LaTeX or Markdown root file and what
it includes. axiomation finds the root; where it cannot tell, it says so
and `--root FILE` names it. It never writes the paper.

**The store** is `axiomation/` inside the paper's directory, made by the
first `build`. It holds every answer, every kept state and every filing;
commit it with the paper (its own `.gitignore` leaves out transcripts,
caches and locks). Only axiomation's commands write it: never edit a file
in it by hand.

**Settings** are optional. With none, a paper builds, reviews and checks
on the defaults: every gate at its default mode ([the model](model.md)
§ 11), the paper's own directory searched for scripts, a `.py` run in
its author's environment for at most 600 seconds, the bibliography the one `.bib`
beside the paper (`[sources].bib` names it where there are several), and
the cited works' corpus a collection named after that bibliography. A
file `axiomation.toml` changes a default where it says so ([the
architecture](architecture.md) § 8). The paper's workspace is found by
walking up to the nearest directory whose `axiomation.toml` holds a
`[workspace]` table; the paper is in it when that table's `papers`
pattern takes it in, and otherwise in no workspace. A single paper needs
none; a workspace lets several papers share one corpus and one set of
settings:

```toml
# the workspace's axiomation.toml, at its root: makes it a workspace
[workspace]
papers = "papers/*"          # which directories are its papers (the default)

[sources]
collection = "my-library"    # one corpus for every paper here

[gates]                      # the gates' names: the model's § 11
unused-premises = "block"    # block, warn or off

[budget]
run_dollars_max = 30         # what a run that asks a model may spend without --budget
```

```toml
# a paper's own axiomation.toml: its keys over the workspace's, per section
[computations]
directories = ["computations"]
run_timeout_seconds = 120

[computations.runners]
R = "Rscript"
```

`axiomation init workspace` writes a workspace's file in the current
directory, and `axiomation init paper` a paper's, with every setting
commented out and explained; remove a line's `# ` to set it. Neither
overwrites a file that exists.

An unknown key in `[sources]`, `[gates]`, `[budget]` or `[computations]`, or an
unknown gate or mode, is refused, naming the file and the known ones; a
section it does not know (a misspelt `[gate]`) is ignored. A paper in a
workspace needs no file of its own: `axiomation settings PAPER` prints
every value in force for it and where each came from (`paper`,
`workspace`, `default`, or `beside` for the one `.bib` beside the
paper).

A `.py` script runs in the environment the paper's files make, with no
setting: `uv.lock`, `poetry.lock`, `pixi.toml`, `Pipfile`, a conda
`environment.yml` or a `.venv/` in the paper's directory or above it in
the workspace, else `python3` ([the architecture](architecture.md) § 8).
`[computations.runners]` declares another, and an `.R`, `.jl`, `.m` or
`.sh` script's. `settings` names the runner and the file that chose it.

What is yours and not the paper's goes in your user file
(`~/.config/axiomation/config.toml`), never in `axiomation.toml`: where
the services run, your keys, the OCR engine you read scans with, and
your contact address, which the lookups of the cited works ask with
(without it they skip Unpaywall and ask Crossref and OpenAlex outside
their polite pools, where they throttle); they are listed below. A
paper's or a workspace's file that names one, or any secret (a name
ending `api_key`, `token`, `password` or `secret`), is refused, naming
where it goes; `settings` shows your values beside
the paper's, a key as set or unset.

**PAPER**, in every command below but `keys`, `compare`, `diff` and
`join`, is
the paper's directory, given first. Inside a paper's directory, PAPER
may be left out: there `axiomation check` is `axiomation check PAPER`,
`axiomation show n12` shows block `n12` and `axiomation run
computations/constant.py` runs that script. Left out, a command reads
the paper the current directory is in, found by walking up:

- a paper of the workspace that holds it;
- else the nearest directory holding a paper's store or its own
  `axiomation.toml`;
- else the current directory itself, a paper not yet built, which the
  command says.

Where a command's other arguments are a list or optional (`support`,
`reread`, `circles`, `sources recheck`, `verify`, `publications`), its
first word is the paper only when it names a directory or holds a `/`.
In a workspace outside its papers (at its root, say), a `sources`
command reads the workspace's corpus; any other command needs a paper
and names the workspace's papers.

**Install** the command once, as a uv tool (or with pipx); with the
`library` extra for the cited works' corpus
(`'axiomation[library]'`), where you run Qdrant:

```sh
uv tool install axiomation
axiomation --version          # the version and where it is installed
```

The agents run on
Claude Code (the `claude` command on `PATH`, logged in); the
`standard` profile names it for every stage. The cited works, and the
services they use, are at the end of this section.

**Make your keys** once on each machine, before the first build:

```sh
axiomation keys --make        # your reading key, a recovery key shown once, a signing key
axiomation keys               # where your keys are, and your key's id
```

A paper's store is sealed to your reading key and to the recovery key:
the record committed with the paper reads as ciphertext to anyone
without them (a git host, a leaked clone). Keep the recovery key's line
offline, on paper or another device; put it in a keys directory's
`reading.key` and it opens every store you wrote. Your keys live in
`~/.config/axiomation/keys/` (`AXIOMATION_KEYS` names another
directory); they are never in a paper.

`keys --make` asks a passphrase for your signing key. It signs your
word: a judgment, a dismissal, a ruling, an answer or an edit you file
(or an agent files in your name) waits until you confirm it, once a
sitting:

```sh
axiomation confirm PAPER --list         # what waits for your word
axiomation confirm PAPER                # confirm it all, with your passphrase
axiomation confirm PAPER --drop 3f2a    # confirm all but the filing 3f2a…
```

While a filing of yours waits:

- `check` and `inbox` do not read it yet, and say so; an edit's state is
  the latest only once it is confirmed.
- Your next `sheet`, `edit` or `change` builds on your waiting edits, so
  several edits in one sitting stack; so does an `update` that asks no
  model (a script added, say), and its state waits with them.
- Every run that asks a model refuses and names it, so a run never pays
  for work a drop would take back; an edit's own run is the exception,
  as its drop takes its run back with it. A `--dry-run` prices the work
  as if your waiting edits stood confirmed.

A filing you drop never takes effect, and neither does what was built
or kept on it, an agent's edit too: `confirm` refuses to drop a filing
alone where an edit of yours is built on it, and names those edits;
`confirm --drop ... --list` shows what a drop takes back before anything
is signed. An agent can file in
your name but cannot confirm: the passphrase is asked at your terminal.

**A read keeps what it worked out** from the kept states in your cache
(`~/.cache/axiomation/`, or where `AXIOMATION_CACHE` names), never in the
paper: the next command reads it there. The first read after the command
is updated works it all out again, so it is slower once. Removing the
directory is safe.

**The cited works** are read from their PDFs on your machine, with
no online service: each `.bib` entry's `file` field, else `<pdf_dir>/<key>.pdf`
(`pdf_dir` a user setting, below), else an open copy fetched for the
occasion. A page with no usable text, a scan's, is read as an image.

The cited works' corpus is the `library` extra (`uv tool install
'axiomation[library]'`), read where a Qdrant server is named (`qdrant_url`): OCR at
ingest, a corpus kept, and search by meaning. Unless both are there,
the PDFs are read as above. It does not install on Python 3.15: onnxruntime, which its local
embeddings need, has no wheel for 3.15; use 3.12 to 3.14 (`uv tool
install --python 3.14 'axiomation[library]'`). With the extra and
`qdrant_url`, the corpus is
built from the paper's bibliography by `axiomation sources ingest PAPER`
(embedding with Voyage costs money: `--dry-run` first), and kept current
by running it again. Each service it uses is needed only when the
command that uses it runs:

| for | needed | setting |
|---|---|---|
| the cited works' PDFs, where an entry names no file | a directory of `<key>.pdf` files | `pdf_dir` |
| the indexed corpus (the `library` extra) | a [Qdrant](https://qdrant.tech/documentation/quickstart/) server, its storage folder kept outside any project folder: it holds every page read and its embeddings, which take hours to make again | `qdrant_url`; unset, no corpus is read |
| embedding and searching it | a [Voyage](https://voyageai.com) API key | `voyage_api_key` |
| ingesting the corpus | the `tesseract` binary (`brew install tesseract`, `apt install tesseract-ocr`); for a model to read scans, PaddleOCR-VL's or MinerU's own server, a vision model behind an OpenAI-compatible endpoint, or any service answering [the OCR contract](ocr.md) | `ocr_backend`, `ocr_url`, `ocr_model`, `ocr_api_key` |
| looking up a cited work (`sources publications`, `fetch`, `resolve`) | a contact address, which Unpaywall needs and Crossref and OpenAlex serve more reliably | `contact_email` |
| open copies from CORE | a [CORE](https://core.ac.uk) API key (optional) | `core_api_key` |
| a work whose `.bib` file path is stale, and a link to it | Zotero with Better BibTeX (optional; the `.bib` is the contract) | `zotero_url`, else `http://localhost:23119` |

These are yours, not the paper's: they go in the user file,
`~/.config/axiomation/config.toml` (`$XDG_CONFIG_HOME/axiomation/` where
that is set), beside your keys, readable by you alone
(`chmod 600`):

```toml
pdf_dir = "~/papers/pdfs"
# the library's, with the `library` extra:
qdrant_url = "http://localhost:6333"
voyage_api_key = "pa-…"
contact_email = "name@example.org"
run_dollars_max = 20   # what a run may spend on any paper without --budget
```

`axiomation init user` writes this file with every setting commented out
and explained, readable by you alone. Nothing sets a budget until you do:
without one, a run that asks a model needs `--budget`.
`AXIOMATION_` and the setting's name in capitals
(`AXIOMATION_VOYAGE_API_KEY`) sets one in the shell, over the file; no
`.env` is read. The budget alone may also be set by a paper or a
workspace (`[budget] run_dollars_max`), which takes precedence over the
user file's. `axiomation sources status DIR` says whether the library
or each work's PDF reads the cited works, and why, and lists each `.bib` entry by where its PDF is
read from (or asks the library's services whether they answer).

**Under the library, a scanned page**, or one whose text layer spells no
words, is read at ingest by the OCR backend you set, Tesseract where
none is ([the OCR contract](ocr.md)). The ingest
prints, per work, the pages that need reading again (read before the
current text extraction, or left disputed, `DISPUTED`: read such a page as an
image), and `axiomation sources ingest PAPER --reread` reads them again:
hours on a large corpus, so `--dry-run` first, which names the pages of
each work and the judgments that read them. A page read again with
another text lapses nothing: a judgment rests on its work's file, not on
axiomation's reading of it.

A cited work's files (a wrong file under a key, an annotated PDF, an
entry with several files) are in § 9.

## 2. Build the graph

```sh
axiomation build PAPER --profile standard --dry-run             # price it; asks nothing
axiomation build PAPER --profile standard --budget 20 -o graph.json
```

A build reads the paper, cuts it into blocks, then asks the profile's
agents for the cut's corrections, the roles, the groups and the edges,
and keeps the result as the store's first state. Where the paper uses an
environment or a reference command the parser does not know (a frame
drawn by its own macros, an `example` it never declares, a reference
command of its own that is more than a word and one `\ref`), the
build first asks what each is for, once, in one small session, and reads
the paper with the answer ([the architecture](architecture.md) § 2, the
vocabulary map).

**Costs.** The dry run's estimate is rough: a build costs five to eight
times it; an update's or a review's dry run prints an expected cost
beside it, scaled by what like runs cost. Give `--budget` at least twice
what the run should cost, or twice what a like run cost.
Under Claude Code a session may spend only what is left of the budget:
one that reaches it is ended, its partial answer recorded, and the run
stops. Where the settings hold `run_dollars_max` (§ 1), a run given no
`--budget` takes that as its budget and says so; with neither, a run
that would ask needs `--budget`. On that default, before each step a
run compares what the step is expected to cost with what is left; above
it, it asks nothing and stops (`budget`).

**A run that stops** (its budget spent, a timeout, an interruption)
keeps every answer it was given. The same command, under the same
settings, resumes it and asks only what is missing. This holds for
every command that asks: `build`, `update`, `sync`, `review`, and the
`--ask` of `reread`, `support` and `circles`; each but `sync` also takes
`--host none`, which asks nothing and goes on from what the store
holds.

`standard` is the profile when neither `--profile` nor `--host` is
given. A build of a version already built asks nothing: it names the
kept state (and writes its export with `-o`). Its graph is never
rebuilt because settings changed; what goes on from it is a step:
`review`, `reread`, `support`, `circles`, or an `edit`. A build under
other settings from nothing is a store of its own (`--store`).

`--prompts DIR`, on `build`, `review-cut`, `update`, `sync`, `review`
and the `--ask` of `reread`, `support` and `circles`, writes what each
agent is served under DIR: with `--dry-run`, for prompt work without a
run; without it, before the run asks.

The profile sets how a build asks, and a flag given overrides one value
of it for one run. These flags, most of them profile values, are left
out of `--help`:

- `--chunk N` (or `paper`), `--overlap N`: how much of the paper a
  session holds, and the context around a cut.
- `--outline`/`--no-outline`: whether a session is shown the paper's
  outline.
- `--boundary-passes`, `--role-passes`, `--group-passes`,
  `--edge-passes`: how many times each stage is asked over its own
  answer.
- `--cut-rounds`, `--decide-passes`: the rounds of cut corrections after
  the edges, and the deciding step after the groups.
- `--rule-flags-in-edge-review`: show a later edges pass the rules'
  flags.
- `--judge`, `--judge-host`, `--judge-model`, `--judge-rates`: a judge
  of each boundaries pass, and who answers it.
- `--displays`: whether a display is its own block or part of its
  paragraph.
- `--jobs`, `--timeout`: questions asked at a time, and the seconds an
  attempt may take.

## 3. Read the graph

Every read takes a kept state (`--state ID`, a prefix is enough; the
latest by default), prints bounded lines (`--limit`, `--whole`) or
`--json`, and costs nothing. A block is named by its handle (its label,
else a slug of its first words when it began: `thm:weak-law`,
`apply-lem:chebyshev-to-y`), its node id or a unique prefix of it
(`nrtt7`) or its block id (`paper.md:79:1`).
A handle stays with its block across edits and updates, so notes and
scripts may keep it; a handle whose block was deleted or merged away
names nothing, and the read says where its text went ([the
model](model.md) § 7). A line you write on a sheet names its targets by
the handles the sheet shows.

| to see | run |
|---|---|
| the states kept | `axiomation states PAPER` |
| a block: its text, its edges both ways, standings, marks, judgments, findings | `axiomation show PAPER BLOCK` |
| the most an edit to a block reaches: what rests on it, no edit made | `axiomation reach PAPER BLOCK` |
| … and past every claim it asks about: the most the edit can reach | `axiomation reach PAPER BLOCK --through-claims` |
| the blocks that pass filters (standing, role, section, edges, changed since, judgment, established …) | `axiomation blocks PAPER --role claim --standing unsupported` |
| what a block proves: the blocks its justifies edges reach | `axiomation blocks PAPER --from BLOCK --kind justifies` |
| its proofs: the blocks with a justifies edge onto it | `axiomation blocks PAPER --onto BLOCK --kind justifies` |
| what is open: unsupported claims, stale marks, lapsed judgments, findings …, in a section or all | `axiomation open PAPER [--kind KIND] [--section 2.3]` |
| whether the paper passes | `axiomation check PAPER` |
| your decisions, grouped | `axiomation inbox PAPER` |
| the settings in force | `axiomation settings PAPER` |
| a settings file to fill in, every setting commented out | `axiomation init user`, `init paper`, `init workspace` |
| a node's history across states | `axiomation show PAPER BLOCK --history` |
| a finding or a judgment by the id a list printed: where it is, its answers, its earlier filings | `axiomation show PAPER ID` |
| the whole state, in the exchange format | `axiomation graph PAPER -o graph.json` |
| all of it, in a browser | `axiomation view PAPER --open` |

`axiomation history` names each step's edges: `+` begun, `−` ended, `~`
other parts named, `→`/`←` carried to or from the other piece of a split
or merge (`edges: + n1 uses n2`).

`show` gives each claim one status under its standing line, folded from
its records, with the next step where one is asked: `defect`,
`disputed`, `conjectured`, `unsupported`, `lapsed`, `stale`, `unjudged`,
`inconclusive`, `rests-on` (and where), `finding-open`, `left-unargued`
or `established` ([the model](model.md) § 11, "A claim's status").
The viewer's card shows the same status above the claim.

A block a review judges nothing on (a definition, an assumption, a
motivation: no claim of its own) says so under its text, in `show` and
on the card: a review files a finding on it only when it finds a
problem (a definition only when ill-posed). It also names the latest
review that read it, and whether its text changed since.

**The viewer** (`axiomation view PAPER --open`) shows the paper
rendered in place, its formulas with the paper's own macros and its
markup as it prints, each source line still its own row under its own
number; turning `¶` off above the text shows the source as written. The header
names the paper, the kept state shown (the latest is followed; an
earlier one can be chosen, or compared with another, which marks the
words added, deleted and moved), a search and the `check` verdict. The
inspector beside the paper holds the commands' views: Block (the
selected block's card, as `show` gives it), Open, Check and Inbox. In
the text, an open item's words are boxed in its kind's tone (a finding
fainter as it is less severe), a dashed box waits on the review of a
proposal, a grey `!` marks an item only to read again or not yet
reviewed, and words no block holds are filled yellow. Selecting a block
draws its connection lines to the blocks at their other ends; there is
no separate node graph.

An agent reads the graph through these commands, not through the store's
files or a whole export: each answers a bounded question and names what
it cut. In `reach`, a block that **changes** takes the edit's meaning
without asking (it uses a definition or an assumption that moved), and
the walk goes on from it; one **asked** (it relies on a claim of the
block, argues about it or justifies it) is asked whether it still holds,
and the walk stops there ([the model](model.md)
§ 5); `--through-claims` walks on past each as though it changed, each
block beyond naming the asked block it came past.

`check`, `open` and the viewer's open tab list and count the same unsupported claims: those
whose status is `unsupported` ([the model](model.md) § 11), standing
unsupported with no ruling that clears it, or a `justified` or
`restated` claim whose own reason is a gap (no justifies edge, nothing
restated, a conjecture restated, which asserts it). Each line ends with
the claim's `true` reading beside its rulings
(`` ruled owed by … · `true` held inconclusive ``). A claim a ruling
cleared, no argument owed, is in neither list: `check` counts it and
lists it after the gates. A claim read negative is a defect and one
disputed is `unreviewed`'s; `check`'s gate line counts both apart, and
`open` lists the reading under `negative` or `disputed`.

`inbox` lists what only you decide at a state, a section each:

- the disputes to judge, and the readings that came back negative;
- the findings a review handed back as a disagreement of reading: your
  answer closes each;
- the findings that stood again, each with the reason it stands now,
  then its problem as first filed, the proposal it turned down, and
  what was answered or proposed on its earlier filings;
- the other findings to answer;
- the proposals awaiting a review, yours among them (a proposed ruling
  on a claim the support search has not read waits for it, and says
  so);
- the unsupported claims by their cheapest route, each with its next
  step: **by a line** where the graph lacks only one (a justifies line,
  an about line onto what it restates, the uses edge an `immediate`
  ruling needs, a circle's end narrowed); **by a ruling** where one
  decides it (unruled; an agents' `known` or `immediate` with no
  verified `true`, which yours needs none of; an agents' `owed` beside a
  `true` held verified: it holds, and whether the paper must argue it is
  yours); else **by an argument**, or the claim cut.

Each claim is listed once: one you proposed a ruling on awaits its
review and is in no route. What a review, a reread or a circles session
does is not in the inbox. The viewer's inbox tab lists the same.

`open --by` and `check --by` group each kind's or gate's items by
`section` (the outermost section that holds them), `role` or `reading`
(a claim's `true`, else the item's status): `axiomation open PAPER
--kind unsupported --by reading` puts the claims a reading showed hold
apart from those it could not tell. The viewer's open and check tabs
list the same items and gates.

A circle, claims each resting on the others ([the model](model.md)
§ 9), is one item, never one per claim: `open --kind circle` lists every
circle with its claims and every line on it, the most claims held back
first; `check`'s head counts them ("circles: 2, 4 claims in them, 30
resting on them"). A circle often closes because an end is coarse, not
because the paper argues in a circle: the end names a whole block
holding a definition, or no claim of several. An edit naming the part
or claim the line relies on breaks it, and an about line of a block of
several claims that restates none of them says so, `→ about [-] T`.

`circles --ask` has the profile's edges agent take every circle no word
holds real (or those named by their key's prefix) as a task, one at a
time, and answer each: `refined`, naming the ends more exactly (given
at the end as one edges edit), or `real`, filed with why and what you
should change. The answer may only narrow: it never takes a line off,
widens an end or adds support. `check` counts the circles agents alone
read real, for you to audit. A build ends with this queue, and an
update after its reread and its search, from what is left of
`--budget`, unless the profile turns it off; `--no-circles` skips it
once.

```sh
axiomation open PAPER --kind circle                  # each circle once, the heaviest first
axiomation circles PAPER --ask --dry-run              # the question on every circle not read real, priced
axiomation circles PAPER --ask --budget 4            # its narrowed ends an edges edit; a real circle filed
axiomation change PAPER --part 'gain [g]: "g = the output over the input" (definition)' \
  --retract bound '→ uses gain · "the gain"' --add bound '→ uses gain [g] · "the gain"' \
  --reason "the bound relies on the gain's definition, not on its claim"
```

A section is named by its number, an unnumbered one by the number its
title opens with (`Appendix A1 · …` is `A1`), each with all that lies
within it (`2` takes `2.1` too); or by its label or words of its title,
that section alone in `--section`, with what lies within in `review
--scope`, where the words must name one section. `blocks --onto X` and `--from X` follow the edge
as the sheet writes it, `source → kind target`: with `--kind uses`,
`--onto X` gives X's users and `--from X` what X uses.

`--json` gives the head's data and each section, by the name its lines
print (a space as `_`), as `{total, items, lines, more}`, bounded as the
lines are (`--limit 0` for all); each item's data is the line's:

```text
$ axiomation blocks weak-law --role claim --standing unsupported --json --limit 1
{
  "state": "f9ac973c5c2b1d69",
  "count": 1,
  "blocks": {
    "total": 1,
    "items": [{"node": "nabmlvvvtcl", "handle": "rem:exponential", "block": "paper.md:104:1", …}],
    "lines": ["nabml  rem:exponential  paper.md:104:1  statement remark  claim  §5  ⊢ unsupported, self"],
    "more": 0
  }
}
```

`check --json` is whole: `{state, passes, made_apart?, gates: [{gate,
mode, outcome, count, items, cleared?, left?, to_defects?,
to_unreviewed?, accepted?, unlooked?}], reread?, read?, conditional?}`,
`cleared`, `left`, `to_defects` and `to_unreviewed` on
`unsupported-claims`, `accepted` and `unlooked` on
`cited-publications`; `made_apart`
what joined copies did apart; `reread` and `read` the stale marks
rereads and readings cleared; `conditional` the claims established under
a conjecture their statement assumes. `inbox --json` is `{state, counts,
<section>: {total, items, lines, more}}`, its sections `to_judge`,
`read_negative`, `reading`, `stood_again`, `to_answer`, `awaiting_review`,
`unsupported_by_a_line`, `unsupported_by_a_ruling` and
`unsupported_by_an_argument`, an unsupported claim's item carrying its
`route` and `next`. `settings --json` is `{paper, workspace, files,
user_file, ignored, sections: {SECTION: {KEY: {value, from}}}}`.
`show --json` carries a claim's status as `claim_status` and a block with
no claim as `no_claim`; `open` and `check` items carry `top_section`,
`roles` and `reading`, with `--by` or without.

```text
$ axiomation show weak-law thm:weak-law --limit 5
nrtt7  thm:weak-law  paper.md:79:1  statement theorem  claim  §5  ⊢ justified, self
⋮
rests on (5):
  → uses ass:moments ref · "Under \cref{ass:moments,ass:independent}"
  → uses ass:independent ref · "Under \cref{ass:moments,ass:independent}"
  ⋮
  apply-lem:chebyshev-to-y → justifies thm:weak-law [bound] · "Apply \cref{lem:chebyshev} to $Y = \bar X_n$, whose mean and variance \cref{lem:mean-variance} gives."
rested on by (5):
  ⋮
established (2):
  "…" justified  not established: not-shown, rests on lem:chebyshev "…": rests on lem:markov "…": `valid` of group:write-1-y-a → lem:markov "…" is held not-shown, …
```

## 4. Review

A review session is an agent reading the paper whole for one kind of
question and filing what it concludes ([the architecture](architecture.md)
§ 7): `argument` (is each claim true, does each proof establish it),
`citationcheck` (does each cited work say it), `methods` (does each script
compute what the text prints), `findings` (problems a reader of the whole
paper meets), `contribution` (is the result known), `fieldresults` (does
each result of the field the paper uses carry its source) and
`relatedwork` (is the paper placed in its field: the work a reader expects
beside the parts that need situating, offered as suggestions). The last
three read the paper against its field, and their answers change with
the field more than with an edit: the shipped profile asks them on
request, as the group `field` (`contribution`, `fieldresults`, then
`relatedwork`; a member the profile seats no reviewer for is skipped,
and named); the group `all` asks every session the profile seats. A
session named by itself that the profile does not seat is refused.

```sh
axiomation review PAPER --session argument --dry-run          # priced; --prompts DIR writes its files
axiomation review PAPER --session argument --budget 15
axiomation review PAPER --session argument --scope 3.2 --budget 5   # one section's subjects
axiomation review PAPER --session argument --scope A1 --budget 5    # an appendix, by its title's number
axiomation review PAPER --session argument --by-section --all-again --dry-run  # every subject again, priced
axiomation review PAPER --session field --budget 15          # contribution, fieldresults, then relatedwork
axiomation review PAPER --session all --dry-run              # every seated session, priced
axiomation review PAPER --session relatedwork --by-section --budget 20  # a session per section
axiomation review PAPER --report                               # the open findings as a referee report
```

Without `--session`, `review` asks the sessions the profile asks by
default, in order, and names the seated ones it left out; the shipped
profile's are the paper's own four, `argument`, `citationcheck`,
`methods` and `findings`.

**A session asks only what needs asking:**

- One that judges asks the subjects no reading holds; a `verified` read
  off a copy that is not the version of record; a reading that did not
  verify whose evidence grew (a work it had unread reads, or gained a
  file); and the subjects whose own line carries an open stale mark,
  since a reading over it clears the mark.
- One that judges nothing reads the blocks no review of its kind read as
  they now stand: the whole paper the first time, then what was added or
  changed since, and the blocks it owes a reading of though their text
  stands (the contribution session's claims ruled `positioning` no check
  read yet, the fieldresults session's claims standing unsupported no
  inventory read yet). With none, it
  settles its open findings alone; with nothing to settle it is not
  asked, and named alone it is refused.
- Every session holds back what holds: a reading that has not lapsed, a
  dispute a person settles, a reading that did not verify on the same
  evidence. `--all-again` asks them too.
- Each asks again what a review filed under an older version of its
  question than the brief asks now (`show` says "filed under an older
  question"); the earlier answer speaks until then.

The dry run says what each session reads ("reads 3 blocks") and counts
what it holds back, by section under `--by-section`. Before it asks, a
review reads each cited work's files and files what changed (`cited
works: …`); its dry run does so on a scratch copy of the store.

Under `--by-section` the first section's session also takes what lies
before the first heading (the abstract, the front matter) and is named
for it, "the opening and §1 …"; a `--scope` of that section's name alone
selects fewer, and its lines do (`--scope paper.md:1-236`, the first
line to the section's last). A section with nothing to ask is left out;
a `--scope` name that selects nothing says why. The file a session
writes is checked by the same check that files it, so what would be
refused goes back to the session while it works.

`citationcheck`, `contribution`, `fieldresults` and `relatedwork` read
the cited works' pages: each work's PDF on your machine, or, with the
`library` extra, its corpus; the last three also use the network. A
person reads a work with `axiomation sources page`.

The methods session is a queue of the pairs of a block and a script it
uses: the agent runs each script through its own tool, outside its
sandbox, from the paper's directory, and answers with the values it
matched and the lines that compute them, each checked against the block,
the output and the script. It takes no `--scope` or `--by-section`.

The citationcheck session is a queue of its sites, a work's sites in a row:
a first agent points each citation (a site) at the pages that hold it,
and the agent that judges is shown those pages and judges, its evidence the words of the work that decide, each checked on
its page. Each session takes a share of the sites, so it takes no
`--scope` or `--by-section`.

The fieldresults session reads the whole paper first and lists each
result it takes from its field whose source is missing (its inventory);
once that is answered, a queue sources them: a finder searches the paper's
bibliography and the web for the work that states each, and the reader is served its
pages and decides what the paper owes. Its dry run prices the inventory,
and the queue a result. A `fieldresults` finding names a field result
the paper uses with no citation, and the work to add. For
the work beside the paper `relatedwork` files **suggestions**: works a reader of the
field might expect at a place, each the author's to take or decline,
never a problem. Each names its work by its key where the paper's
bibliography holds it, cited elsewhere or nowhere (`show` says "cited
nowhere in the paper"), else by an identifier (`doi:…`, `arxiv:…`), or,
for an old article or a book with neither, by a link to a public page of
it; a work offered at a place is not offered there again once it
stands, open or answered. The contribution, fieldresults and
relatedwork sessions read every work of the bibliography, those the
paper cites nowhere too, before the web. They are listed under "suggested citations" in
`review --report`, in `show BLOCK` and the viewer's card, and in `open`
until answered (`--kind suggestion`; one whose place's words changed
under `lapsed`):

```sh
axiomation answer-suggestion PAPER SUGGESTION --as taken      # the paper now cites it
axiomation answer-suggestion PAPER SUGGESTION --as declined --note "not this paper's field"
axiomation suggest PAPER --on BLOCK --work doi:10.1000/x --why "the standard reference for …"
```

The author adds a taken work to the bibliography by hand. A review files as its agent, so a person's judgment decides
over it. A review may also correct a line of the graph it read wrongly;
that makes a new state, which its output names (`edge lines given`). The
argument session may also make a group of blocks the paper argues as one
(two paragraphs of one illustration, a proof no group holds); it is
given as the agent's groups edit, whose edges re-ask runs on the
profile's agents within what is left of `--budget` (`group lines
given`). Where it cannot be asked, the output prints the command that
gives the lines (`review PAPER --state S --budget …`, S the state the
session read). It never makes again a group you dissolved. A
finding's severity runs 1 to 3: only a 3 counts against what it bears
on, 1 and 2 are shown.

The argument session also rules on each claim the paper asserts and
argues nothing for: would a referee in the field want the paper to argue
it? `owed` keeps it in `unsupported-claims`; `known` (common knowledge, a
standard result, beside a `true` the reviewer showed), `immediate` (at
sight from a result of the paper's own, with the uses edge onto it) and
`interpretation` (a gloss nothing rests on) take it out, unless the paper promises the
argument, or something rests on what was ruled an interpretation. A
ruling is never about truth: a claim the reviewer had to argue from
scratch, rather than name a known result for, is `owed`, since the paper
still does not argue it. A claim the paper argues takes no ruling,
wherever the argument stands: another block's proof, its block's own
step, or the claim it restates (an abstract or conclusion sentence, a
synthesis, expository prose a later part argues). Whether it is argued
is the support search's to say, first: a ruling is asked only of a
claim a search read unargued, and the review's dry run counts those
not yet searched. The session asks what a claim owes by its place: a
statement or a step its argument, a restatement its pointer.

```sh
axiomation support PAPER                         # each claim standing unsupported, searched or not
axiomation support PAPER --ask --dry-run         # the search on those no search read, priced
axiomation support PAPER --ask --budget 2        # the lines it finds an edges edit; the rest filed unargued
axiomation support PAPER --ask --host none       # what the store holds replayed, nothing asked
```

`support --ask` takes each claim standing unsupported that no live
search read (or those of the blocks named) as a task, one at a time, in
two steps: a worker answers each `argued`, with the justifies, self or
about line the edges stage missed, or `unargued`, naming the closest
block it read; a settling step confirms the worker's line, or answers
the claim itself. Each answer is checked as it is given. Once every
claim is settled, the lines are given as the search's edges edit, which
stands the claims argued, and each claim read unargued is filed with
why. A search lapses when its claim's words change. A build and an
update end with it, after the reread and before the circles, unless the
profile turns it off; `--no-search` skips it once. Where the profile
names the steps' models, `support --ask` refuses `--model`, `--effort`
and `--rates`.

A claim that the paper's work is new, or that other works lack or did
not do something, is ruled `positioning`: reading the literature settles
it, so it leaves `unsupported-claims`, and the contribution session
reads it. Until its `checked` line quotes the claim, `unreviewed` lists
it unchecked; a person who checked the
literature on it says so by hand (`check-contribution`, § 5). An
objection is a `novelty` finding, which the author may answer
`dismissed`. A claim that a work
shows or contains something is no positioning: it owes its citation, in
place or by its pointer to a block that cites.

## 5. Judge and find by hand

A person, or an agent working in the paper, files on a kept state the
same records a review does. A judgment is a conclusion on a subject; a
finding is a problem with a fix. Neither changes the graph.

```sh
axiomation judge PAPER --aspect valid --on group:fix-t-0-and \
  --verdict verified --depth informal --evidence "each step follows"
axiomation find PAPER --aspect presentation --on fix-t-0-and --severity 1 \
  --problem "λ is chosen before it is defined" --fix "define λ first"
axiomation answer-finding PAPER FINDING --as fixed  # a person's answer closes it
axiomation run PAPER computations/constant.py      # run a script and file the run
axiomation adopt PAPER JUDGMENT                    # take an agent's judgment as your own
axiomation rule PAPER --on nh3ve --ruling known \
  --reason "the Radon–Nikodym chain rule"         # the paper owes it no argument
axiomation check-contribution PAPER --on nx72p \
  --searched "the surveys of the field since 1990" \
  --found "no earlier bound of this form"          # a positioning claim read
```

These commands ask no model and cost nothing. Each prints the id of
what it filed (`judgment 786c5cdb0b8a-1-1 at state a3d5c1ec61f36e79`);
`show`, `open` and the inbox name a judgment or a finding by that id,
and any command taking one accepts a unique prefix of it. `show PAPER
ID` prints it whole: a finding with the block it is on, its anchors,
every answer (the one that speaks marked) and the filings it was; a
judgment with its subject's block, what lapsed it and its subject's
status. `history PAPER ID` and the viewer's search take the id too. A
finding that stood again is refiled under a new id; `show` keeps the one
you knew ("refiled as" the new one beside it), and `show`, `history`,
`answer-finding` and `reaffirm-finding` take either, acting on the
latest filing.

**Who files** is recorded: a person by default (`--person NAME` for one
of several), an agent with `--agent MODEL --agent-session ID`, `MODEL`
the agent's model name as it gives it. A person's word decides over the
agents' ([the model](model.md) § 8):

- a person's judgment decides over the agents' before it; an agent's
  later one that differs disputes it;
- a person's ruling stands over every agent's, before it or after;
- a person's answer closes a finding and stands over every agent's and
  review's (below).

`adopt`, taking an agent's judgment as your own, is a person's alone. To
put a claim an agent ruled `known` back in the gate, rule it `owed`; to
have the next review ask it again, withdraw the agent's ruling:
`axiomation rule PAPER --withdraw RULING --reason "…"`, a filing that
makes no state.

A ruling you make by policy goes to a set at once: `--in` names
sections, lines or handles as `review --scope` reads them, `--role`
the blocks' roles, and every claim standing unsupported they select is
ruled, each its own ruling in one filing, with one reason:

```sh
axiomation rule PAPER --in paper.md:29-193 --ruling left-unargued \
  --reason "Part I is expository prose that Part II argues" --dry-run
axiomation rule PAPER --in 1,2.3 --role motivation --ruling owed --reason "…"
```

Each is guarded as a single ruling is: the report lists the claims
ruled, those the rules for a single ruling hold back (with what relies
on each), your earlier
ruling each replaces, those ruled so already, and each name that holds
no claim standing unsupported; then the gates. `--dry-run` files on a
scratch copy and keeps nothing. A ruling on a set is a person's; an
agent rules each claim. A part that is no heading (a `\part` in a
Markdown paper) is named by its lines.

An `owed` ruling says how much hangs on the claim and what would
discharge it: its severity, 1 to 3 (3, a result or a stated contribution
rests on it; 1, a passing sentence), with what relies on it beside, and
its fix. `inbox`, `open` and `check` list the owed claims heaviest
first; the inbox's next step is the ruling's fix. The weight changes no
gate. Your own `owed` gives both:

```sh
axiomation rule PAPER --on nqd5k --ruling owed --severity 3 \
  --reason "the matching lower bound is argued nowhere" \
  --fix "prove the lower bound, or call the bound an upper bound"
```

A ruling that rests on a work of the paper's bibliography names the
pages, with
`--page KEY:INDEX` (repeat), as a judgment does: they are read and
stamped, and a change to their file asks the claim again. A result the
field knows without a work needs none:

```sh
axiomation rule PAPER --on nh3ve --ruling known --page smith1996:4 \
  --reason "Smith's Theorem 2"
```

To argue against an agents' ruling without deciding it yourself,
propose yours: add `--propose` to `rule` on one claim. It changes no
status and is shown as yours; the next argument review (`sync`) rules
the claim with it in view, reads the pages it names, and its ruling
settles it. A plain `rule` decides instead, and a claim your own ruling
decides takes no proposal.

A claim ruled `owed` has cheaper answers than an argument in the text,
in this order: cut the claim, or narrow it to what the paper shows; point
it at the result it restates, or cite the work that states it; move the
argument to an appendix. Where the author still leaves it unargued (a
routine step a reader of the field supplies, a stance kept short), the
author says so, with the reason:

```sh
axiomation rule PAPER --on nqd5k --ruling left-unargued \
  --reason "a routine step a reader of the field supplies"
```

That is the author's alone, never an agent's: the claim leaves the
gate's count, `check` lists it apart with its reason, and its argument
is still owed. It never clears a claim the paper promises to argue, or
one something rests on: the claim stays in the gate, its line saying why.

An agent proposes; a review or a person closes ([the model](model.md)
§ 8). An agent's `answer-finding`, of any kind, is a proposal and says
why (`--note`): the finding stays open, in `check`, `open` and `show`,
the proposal beside it, until the review of its kind settles it, on its
next sheet: once the paper is fixed, propose `fixed` with what changed,
update, and run `review`, whose `fixed` line closes the finding
or whose `stands` line refiles it.

A person's answer closes a finding
(above); a finding a person filed,
or an agent by hand, or one a person answered, waits for a person. To
have the review weigh your reasoning rather than close the finding,
propose too, on a finding a review filed:
`axiomation answer-finding PAPER FINDING --as fixed --propose --note
"reworded to say it is a flat weight"`. The proposal is shown as
yours, and the next review of its kind settles it as an agent's. An
answer you decided (a dismissal, a postponement, a `fixed` after
your yes) is yours: you file it yourself, in Claude Code as
`! axiomation answer-finding …` in the chat, never an agent for you.

An answer filed by mistake (a dismissal you meant to propose, say) is
withdrawn by you, never deleted from the store: `axiomation
answer-finding PAPER ANSWER --withdraw --reason "meant to propose"`, by
the answer's id, or the finding's for the answer that speaks for it
(else its proposal). The finding reads as its other answers leave it,
open where none speaks; then answer or propose again. `show` keeps the
withdrawn answer, marked so. Where a review refiled a finding against
your own proposal, an answer without `--propose` says that it closed
the finding and how to take it back (not on a disagreement of reading,
which only your answer closes).

A dismissal answers the finding; the negative reading a review filed
with it still holds, and `defects` lists it, until the next review of
its kind reads it again with the dismissal's note in its hints (the
default asks it). Give the note the reason the reader
needs: what the passage does and why it is no problem. Each dismissal
asks it once; a reading that concludes replaces the one the review
filed with the finding, the earlier one stands where the reader could
not tell (`inconclusive`), and your own `judge` decides it.

A review that weighs your proposal answers its note: a `stands` refiling
shows its case first (a counterexample inside the statement's setting,
the step that fails, or, for a charge of circularity, an account without
what it presupposes), its problem as first filed behind it, and your
proposal as not taken. Where it has no case, it hands the finding back as
a disagreement of reading: no review asks it again, and your own
`answer-finding` closes it; until then it counts as its severity says.

To hand a reading, not a finding, to the next review with your argument,
propose it: `axiomation judge PAPER --aspect true --on BLOCK --verdict
verified --depth informal --evidence "…" --propose`. It changes no
status and is shown as yours; the next review of its kind (`sync`) reads
the subject again with it in view. Its reading settles it: one that
concludes replaces the agents' negative readings before it, an
`inconclusive` leaves them, and your plain `judge` decides. A proposal
whose subject's words change lapses and asks nothing more: propose again
on the new text.

## 6. Check

```sh
axiomation check PAPER
```

`check` runs the gates over a kept state: first every gate's line, its
mode, outcome and count, then per gate each item in the way and what
clears it; it files nothing and asks nothing. It exits 1 when a gate in
`block` mode has an item, so it can stand in a script or a hook. A
gate's mode is the paper's `[gates]`. A blocking item is fixed in the
text, never excepted, save a claim a ruling says the paper need not
argue or the author leaves unargued.

After the gates, `check` lists what they do not count, so the agents'
word is audited in one read:

- the claims rulings cleared (no argument owed), with the rulings that
  speak and their reasons, and how many no person ruled on;
- the claims the author leaves unargued, each with its reason;
- the claims established under a conjecture their statement takes as a
  hypothesis, each with its conjectures and whether a person wrote the
  flag or an agent alone (§ 8).

`open --kind unsupported` shows the ruling of each claim still listed:
an `owed` one, or one a guard refused. Where a claim's words begin
inside a sentence, its line quotes the sentence's opening before them in
brackets, so the line keeps its subject: `"[Earlier methods gave weaker
bounds,] but none holds for every n …"`.

**What the paper rests on outside itself.** `cited-publications` warns
of each cited work a referee would question as ground: a preprint no
version of record is known for, a thesis or a report, a work with an
erratum. Its publication is looked up, a network pass and no model
call, cached for thirty days:

```sh
axiomation sources publications PAPER            # every work the paper cites
axiomation sources publications PAPER KEY --dry-run
```

Each work's line names its publication, where it came from, and a
preprint's versions of record with how each was matched; a source that
did not answer is named, and a pass without a contact address (§ 1)
says so first. A preprint
published since is a finding, severity 1: cite the version of record,
and check that what the paper takes is in it and under which number (a
conference version is shorter, its theorems renumbered). A match by
title and first author can be wrong: the finding says so, and a
dismissal (`axiomation answer-finding PAPER ID --as dismissed`) holds. Where a lookup is wrong, or the entry names no DOI
or arXiv identifier (most books), set it:

```sh
axiomation sources publications PAPER KEY --set refereed --why "Annals 2019, cited as such"
```

For each work the gate lists, argue what the paper takes from it, cite
refereed work for it, or accept resting on it, out of the gate's count
and listed apart with your reason:

```sh
axiomation sources accept PAPER KEY --why "a standard construction, checked by hand"
axiomation sources accept PAPER KEY --why "now cited from the book" --withdraw
```

A claim resting on such a work stays established; its status says
"argued, on a preprint: KEY".

## 7. Edit the paper, then update

Edit the paper's files as usual; then one command does the machine's
part of the round and stops where yours begins:

```sh
axiomation sync PAPER --dry-run         # the round priced: the update's dry run, then the review's
axiomation sync PAPER                   # the budget default (run_dollars_max), or --budget USD
axiomation sync PAPER --session argument --session citationcheck   # those review sessions only
```

`sync` runs `update` where the paper's text is ahead of the latest
state; then, as the profile ends an update with them, `reread --ask` on
the marks still open, `support --ask` on the claims standing unsupported
no search read and `circles --ask` on the circles no word holds real
(each asks nothing where there are none, and asks again what an
update left unanswered); then `review` over the sessions the profile
asks by default, or those `--session` names (a group too, `field` or
`all`), all on one budget; then prints your inbox (`axiomation
inbox`, § 3). It stops at the first step that does not end whole,
saying what it spent; the same command resumes, each step asking only
what the store has not answered. A change to a script or a figure alone
is kept by `update`, not by `sync`.
It never judges, rules or answers for you.

A session's first round on a paper costs most; a later round reads only
what changed and asks only what lapsed, grew or a person asked again.
Before the update is kept, the dry run prices the review without what
the update will reopen. `review --all-again` and the single commands
below stay for control: before updating, see what the edit touches;
then update, which asks only what changed:

```sh
axiomation changes PAPER                                  # the edit against the latest state, not yet updated
axiomation update PAPER --profile standard --dry-run
axiomation update PAPER --profile standard --budget 5
axiomation changes PAPER                                  # what the update changed
axiomation open PAPER --kind stale --kind lapsed
```

Where the paper's files are its latest state's, `update` says there is
nothing to update.

`axiomation states PAPER` ends with a line while the paper's files are
not the latest state's version: while you have edited the paper since
its latest state.

A file you add counts as follows ([the model](model.md) § 7):

- **included** (an `\input`, a new `header-includes` entry): an edit
  like any other; `changes` shows a new body file's blocks as added, and
  the update carries the rest over;
- **not included** (a note, a draft, a figure): no edit. `update` keeps
  it as a state of its own, asking nothing ("its files differ … asking
  nothing" under `--dry-run`); after it, a sheet lists a figure at the
  top level as a `file:` an edge may name, never a `.tex` or `.md` file
  the paper does not include;
- **a script** (`checks/new.py`): the same, and the sheet lists it with
  its opening line. A script added lapses no judgment; a script changed
  lapses those that cite it.

The viewer says when the paper's files are ahead in its header
("the paper changed; no update has run", the state in its tooltip), and its `edit`
tab shows the edit as `changes` reads it.

An update keeps a new state, its parent the one before. Each node keeps
its id; its agent classes each edit (`reworded`, `strengthened`, …), and
reads each block whose meaning the edit moves through a definition or an
assumption, giving it a Δ line of its own: `unchanged`, with the words
that show it quoted, stops the change there ([the model](model.md) § 5).
Past those, the reach rule asks the blocks the edit may reach, as the
agents read it; a line of one it asked stays marked **stale** where it
can be wrong: where the words at its far end changed, or, for a line a
standing rests on, where its far end took a Δ of its own ([the
model](model.md) § 5). The update's summary (its lines, or the whole
with `--json`) counts what the reach did:
the blocks a Δ closed (what the agent alone closed, for you to audit),
those whose meaning moved with no one reading it, the owners it asked
and the marks left open.

What an update carried unread stays one item per edit, in `open`
(`--kind carried-unread`), `check`'s head and the viewer, until a later
update gives those blocks their Δ lines or a review reads them (a
`verified` reading of a subject they belong to); a profile setting
raises how many blocks an update reads so.

An update of one session asks the edges one block at a time: each block
the edit changed, added or moved, each owner it reopened, and each
block that may rely on a part an answer added; a block it settled with
a problem still in it is served again at the end. Where the edit
deleted a block an edge pointed at, the update asks the edge's source
again with the lost line; one it did not draw again is listed in the
update's summary and by `changes` under "to look at", and marks its
source stale until settled: draw it again (on the sheet with `edit`, or
with `change --add` and then a reread, § 8), or leave it gone with a
reread (below).

A cut correction (the build's, a person's or an agent's) carries across
your edit wherever the words at its boundary still stand, whatever you
changed before or after them ([the model](model.md) § 5). One whose
words you rewrote, or across which you put a paragraph of your own,
lapses, and the cutter's cut stands there: `changes`, `update
--dry-run` and the update's summary list it (`corrections_lost`, with
its words and why); cut it again with `edit --stage cut` (§ 8) where it
still holds. A group carries while its run stands, however you reword
its steps; one whose step you split, joined or deleted, or inside which
you put a paragraph, is listed the same way, with why:
make it again with `edit --stage groups` where it still holds.

A stale mark is one line, marked once however many updates asked it, at
the latest. It clears when its line is read on the new state:

- a line changed, added or kept on the sheet, which shows the marks
  (`edit`, § 8);
- the next update re-reading it;
- a reread, which says you read the owner's lines against what changed
  and they stand;
- a review's `verified` reading whose subject owns the line (a claim's
  `true` its block's lines, a proof's `valid` its steps' lines and the
  justifies line onto its claim).

`change` is shown no mark and clears none: it lists the marks still open
on the blocks it touched, for a reread once you have read them. A
negative reading over a marked line is one defect, the mark under it.
An about line no standing rests on is a description: `open` lists it
after the marks a gate asks, `show` on its block, and no gate counts it.
A reread pins nothing and makes no state; it prints what it did to the
gates, and `check` counts the marks agents alone cleared, by rereads and
by readings. `open --kind stale` ranks the marks by the claims each
holds back, and `check`'s head names the one that holds back the most:
reading that line first frees the most.

```sh
axiomation open PAPER --kind stale                   # the line holding back the most claims first
axiomation reread PAPER nc6m2 nt4wb --reason "every use already holds under the new assumption"
axiomation reread PAPER --ask --dry-run              # an agent's reread of every marked owner, priced
axiomation reread PAPER --ask --budget 8             # each owner's lines stand (its reread) or change (its edges edit)
```

`reread --ask` has the profile's edges agent take the marked owners
(those named, else every owner of an open mark: a gate mark's first,
then a description's, each in the paper's order) as tasks, one at a
time, each served its lines, its marks, each marked line's far end
before the update and now, and the edit the mark began at where its
far end's words stand. Each owner whose lines it says stand is its
reread, by an agent, filed as it answers; the lines it changes are
given, once every owner is answered, as one edges edit, a new state,
and the readings that rest on them lapse. `check` counts both as
cleared by agents alone, for you to audit.

An update ends with that queue itself, on the open marks of the
state it keeps, from what is left of `--budget`; its summary's `reread`
says what it asked, what stood and what changed. Where it is not
answered (the budget spent, a stop), the update's state stands and
`reread --ask` asks it. `update --no-reread` skips it once; a profile
setting turns it off.

A judgment lapses when what it rested on changed ([the model](model.md)
§ 8). Filed on the build's state, the remark's `true` was verified; at
the update's state it is lapsed, because the theorem it uses now means
something else (`open --kind lapsed` lists it while it stands):

```text
$ axiomation changes weak-law
⋮
lapsed (14):
  ⋮
  true · "the probability in \cref{thm:weak-law} decays exponentially in $n$"  lapsed at f9ac973c5c2b1d69: meaning, thm:weak-law has a Δ that carries: strengthened (`axiomation reask` db11a03e0535-1-18)
```

The id at the end is the judgment's. To settle it, read what changed
(`reask` reads, at no cost) and either file again or reaffirm:

```sh
axiomation reask PAPER JUDGMENT          # every cause, and the basis as it reads now
axiomation reaffirm PAPER JUDGMENT --depth informal --evidence "the step still holds for a_i ≥ 0"
axiomation reaffirm-finding PAPER FINDING --evidence "the gap is still there"   # or answer-finding
axiomation review PAPER --budget 5    # what lapsed, what the update changed, the marked lines; --session all for the field's too
```

A lapsed negative reading, and a lapsed open severity-3 finding, stay in
`check`'s `defects`, marked lapsed, until a reading concludes or the
finding is answered: a lapse never makes a paper pass. Every review
settles the findings a session of its kind filed on the blocks it reads
that lapsed or carry a proposal (§ 5): each still stands, or is fixed.
`review` asks what needs asking (§ 4), so one round after an update
covers what it touched; a mark no session's subject holds as its own,
such as a qualifier's line, waits for a `reread`. `review --all-again`
asks every subject and reads the whole text. A finding you filed is
yours to reaffirm or answer.

## 8. Correct the graph

Where the graph is wrong (a missing edge, a wrong role, a block cut in
the wrong place), correct the kept state; nothing is re-run. A
correction that asks nothing is a new state at once.

```sh
axiomation change PAPER --add cor:frequency '→ uses ass:independent · "independent events"' --dry-run
axiomation change PAPER --add cor:frequency '→ uses ass:independent · "independent events"'
axiomation change PAPER --retract BLOCK 'LINE' --reason "…"   # LINE as `show` prints it, its ` — why` tail optional
axiomation sheet PAPER --stage roles -o roles.md     # a stage's whole file, to edit
axiomation edit PAPER --stage roles --file roles.md --profile standard --budget 2
axiomation sheet PAPER --stage vocabulary -o vocabulary.md   # the paper's own names, as read
```

Where the paper's own name for a box or a reference was read wrong (a
frame read as one block, `\lemref` not followed), correct its line in
`vocabulary.md` and give it with `edit --stage vocabulary`: every block
that reads the name is re-cut and asked again, the rest kept, and later
versions keep the correction while the name's declaration stands. A name
you define twice (a `\renewcommand` later in the paper) has a line for
each definition, each read where that definition is in force.

Where a claim's statement takes a conjecture as its hypothesis
("assuming Conjecture 1, …"), its uses line onto the conjecture carries
the flag `assumed`: the claim is then established under the conjecture,
`show` says so, and what rests on the claim asks the conjecture again
([the model](model.md) §§ 3, 9). Only the statement's own line takes it,
onto a `conjectured` claim, naming the claims it is a hypothesis for
where the block holds several. Replace the line in one change:

```sh
axiomation change PAPER --retract thm:x '→ uses conj:k · "Assuming \ref{conj:k}"' \
  --reason "the statement assumes it" --add thm:x '→ uses conj:k assumed · "Assuming \ref{conj:k}"'
```

The argument review session flags such a line, or takes a wrong flag
off, by its edge lines; `check` names each claim established so.

A line you add that argues a claim standing unsupported stands it
argued in the same change, so `unsupported-claims` moves with the edge.
Such a line is:

- a justifies line onto the claim, or its block's self line, naming the
  claim where its block holds several;
- its block's about line onto the claim it restates, naming it at its
  source end, `→ about [<claim>] T`.

An about line that names no claim at its source end describes its
target and restates nothing, even from a block of one claim: `change`
and its dry run say so where that leaves a claim unsupported, and give
the line that would (`→ about [in-short] thm:main · "In short, …"` in
place of `→ about thm:main · "In short, …"`). A line you only rewrite
lends no new support.

```sh
axiomation change PAPER --add sec:proof '→ justifies thm:main · "Hence the theorem holds"' --dry-run
```

To set a standing yourself, give `--standing` in the same change: it
replaces the standing on the same claim (its quoted words) and keeps the
claim's handle, the bracket `show` and the sheet print after its status
on a block of several claims, unless the line writes one of its own.

A uses or about line is one per source and target: to
name a second claim of its block on it, the line names both (`→ about
[a, b] T`); a change that adds a second is refused with the line it
holds and, where one line can say what both say, the one to file in its
place. Two justifies lines may stand apart, each naming claims of the
target the other does not ([the model](model.md) § 3).

An edges `change` asks nothing, but each reading that rests on a line it
adds or retracts lapses: the reader was not shown the graph as it now
stands ([the model](model.md) § 8). `change` and `edit` print what lapsed
and, per gate whose items moved, `<gate> −n +m` with each item gone and
new, so a count that stays the same hides nothing; `review` prints the
same under the state its edge lines make, its own readings left standing.
Re-read a lapsed reading, or `reaffirm` it where it still holds (§ 7).
`--dry-run` on `change` or `edit` gives the edit on a scratch copy of the
store and prints the same report, recording nothing; for a cut, roles or
groups edit it also prices each question the edit would ask (under
`--profile`, else `standard`), and for the vocabulary map the update it
runs.
A cut, roles or groups `edit` asks again
what it reopens, under the profile's agents. Left unanswered (no
`--profile`, or the budget spent), it makes no state: it stays pending,
no other edit or update starts from that version, and the same command
with `--profile` and `--budget` resumes it ([the model](model.md) § 6). A
group's justifies line carries its members' onto the same target: an
edit that writes both is refused, and a groups edit that joins blocks
into one argument asks the group's line and drops its members' onto that
target. A member's line you want beside the group's, keep with the keep
mark `= `. A person's line is pinned: later questions
keep it. An agent's correction (`--agent MODEL --agent-session ID`) pins
nothing and may not change a person's pin. The sheet's notation is the
model's ([the model](model.md) §§ 3, 4).

A line a check refuses that a kept state already holds is named as a
note by `sheet`, `edit` and `change`, and listed by `open` as
`later-rule`; it refuses no edit, and stands until someone corrects it.
An edit is refused only for a problem it adds ([the model](model.md)
§ 7).

## 9. A cited work's files

**A wrong file under a key** is held back: a work whose first pages
print neither its entry's title with its authors on a title page, nor
its DOI, nor its pages as their own page numbers, reads as `mismatch`,
and no review reads it. `ingest` lists each one it held, and
`axiomation sources status PAPER` lists them all with what each title
page says. For each:

- the wrong work (another author's book of the same title, a sibling
  paper): replace the file in the bibliography's `file` field, then
  `axiomation sources ingest PAPER --key KEY`;
- the right work the check cannot see (a translation, a scan whose title
  page did not read, a chapter without its book's front matter):
  `axiomation sources confirm PAPER KEY --why "…"`, written to
  `references.confirmed.json` beside the bibliography (commit it with
  it).

Either way, the readings on the work that did not verify are listed to
ask again, in every paper of the workspace citing from the same
bibliography, and the
next review asks them; `axiomation sources recheck PAPER KEY` lists them
again. A reading lapses by itself where a file it read a page of prints
other content, or is gone or held back.

**Annotating a cited PDF** lapses nothing: a reading rests on what the
file prints (its pages' text, fonts, images and figures), not on a
highlight, a note or ink saved into it. A PDF app that flattens
annotations into the page ("print to PDF", some tablet apps) changes
what it prints and lapses every reading of the work: annotate a copy
there, not the file the bibliography names.

**An entry with several files** (a translation beside the original, a
supplement beside the paper, two copies of a book) has each distinct
file in the corpus. One is the cited edition, the edition the entry
names, and keeps the entry's key (`smith1998`); each other file has a
key of its own, the entry's key and the first eight characters of its
SHA-256 (`smith1998@8e3d55d8`). The files tell which is the cited
edition where they can (the one printing the entry's `pages` as its own
page numbers, say); `axiomation sources status PAPER` lists each such
entry, its files and which is the cited edition. Where the files cannot
tell, a person names it or labels a file:

- `axiomation sources confirm PAPER KEY --file PATH --cited --why "…"`:
  the file is the cited edition, whatever the file check finds;
- `axiomation sources confirm PAPER KEY --file PATH --why "the English
  translation"`: the file is another file of the entry, so labelled.

Then `axiomation sources ingest PAPER --key KEY` holds each file under
its key: a file the corpus already holds under another key of its work
moves, nothing read or embedded again. Every read of a key of such a
work (`sources page`, `grep`, `locate`, `toc`, `search --key`) starts
with a line naming each of the work's files and its key; `grep` on the
work's key searches every file. A review may read each file: a cited
work's content from any file, a locator only from the cited edition.
`--prune` drops a key whose file left its entry.

## 10. Share the record with a co-author

A co-author runs `axiomation keys --card` and sends you the line; you
run `axiomation members PAPER --add
'<their card>'`, which asks your passphrase: a grant is your signed
word, and one nobody signed gives no one anything. To retire a key of
yours, add your new key's card, then, with the new key, `members PAPER
--remove <old id>`. Git joins two copies of the committed record by
itself; for a copy outside git, `axiomation join PAPER OTHER_STORE`.
Where each copy changed the members, nothing is written until you run
`axiomation members PAPER --settle`: one grant for the members both
copies name (add again anyone only one added).

Work done on two copies at once is not merged: keep working on one
copy at a time. Where both copies did work apart, the join keeps both
and every read names what differs; no state is the latest (read either
with `--state <id>`), and every command that keeps a state refuses
until one copy's record is restored (from git, say). Each machine
remembers the last tip of the record it read, in a file beside your
keys: a record that no longer reaches it (a host served an older copy,
or lines were cut from its end) is refused, naming that file. Where you
set the record back yourself, remove the file it names and read again.

## 11. For an agent in the paper's repository

- Read through the bounded commands (§ 3); never the store's files.
- Never edit a file in `axiomation/`: its commands are its only writers.
- Every command that asks a model (`build`, `update`, `sync`, `review`,
  an `edit` with a host) or embeds (`axiomation sources ingest`) costs
  money: price it with `--dry-run` where it has one, and run it only on
  the person's go, with the `--budget` they gave (or, where they said
  so, the budget default, `run_dollars_max`). `change --dry-run` shows what a
  change would do before it is made.
- A `change` clears no stale mark: read the marks it lists, then
  `reread` the owner with your reason where its lines stand.
- A build, an update or a review runs for minutes to an hour, longer
  than a host's command timeout: run it in the background and wait for
  it to end; do not cut it off. Every answer it finished is kept, and
  the same command asks only the rest.
- File as yourself: `--agent MODEL --agent-session ID` on `judge`, `reaffirm`,
  `find`, `answer-finding`, `reaffirm-finding`, `rule`, `run`, `change` and `edit`. Never run `adopt`:
  it files as a person, and never file the person's answer to a
  finding for them. Your `answer-finding` is a proposal, with
  `--note`: the finding stays open until a review or the person settles
  it (§ 5).
- What a cited work says is read from its pages (`axiomation sources page`,
  `axiomation sources search`), never from memory.
- Before calling a paper done, run `axiomation check` and report what it
  lists.
