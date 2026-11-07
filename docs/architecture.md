# The architecture

How axiomation is built: its components and the direction of dependence
between them, the question contract every agent step follows, the hosts
that answer, the record store and the kept store, the exchange format and
what a consumer can rely on. What the graph means is
[the model](model.md); each command's flags are its `--help`.

## 1. The pipeline

axiomation is one pipeline with a deterministic spine and one door to the
models.

**The spine** turns a paper and its records into a graph, with no model:

- the **reader** (`reader/`) turns a paper's files into the document
  layer, reading its tables (the standard environments and references,
  the paper's declarations, its YAML header's `header-includes` among
  them) and its vocabulary map, and lists the names neither knows;
- the **cutter** (`cutter/`) proposes blocks, marked parts, groups and
  marked edge candidates (each `\ref` and `\cite`, never what the
  reliance is);
- the **records** (`records.py`) are the store's record kinds, read and
  written;
- the **assembler** (`assembler.py`) applies the recorded cut
  corrections to the cutter's cut;
- **identity** (`identity.py`) pairs two versions' blocks, classes each
  and rebases the old corrected cut onto the new text;
- the **closure** (`closure.py`) is the reach rule;
- **changes** (`changes.py`) classes a graph edit per layer.

**The door:** every agent step is a **question** (`questions/`), asked by
the **runner** (`runner/`) on a **host**, its answer turned into records
by the **resolver** (`resolver.py`), the store's only writer. The
**driver** (`driver.py`) and **update** (`update.py`) decide which
questions a build or an edit requires, in what order.

**Around them:**

| component | does |
|---|---|
| `profiles.py`, `profiles.toml`, `prices.toml` | who answers each stage: harness, model, effort, template; a model's price, for estimates only |
| `exchange.py` | the exchange format, written and read |
| `versions.py` | the kept states: files, exports, block texts, node ids, handles, histories |
| `queries.py` | the bounded reads over kept states: `show`, `reach`, `blocks`, `changes`, `open` |
| `edits.py`, `person.py` | an edit of a kept state by a person or an agent; pins |
| `flags.py` | the flags a build raised, stored and computed |
| `judgments.py` | judgments, findings and their answers; the lapse; a subject's status |
| `established.py` | whether a claim is established; the circles of what claims rest on |
| `rulings.py` | rulings: whether the paper owes an unargued claim its argument; filed, read at a state, and which speaks; a claim's support status, from its standing, its own gap and its rulings |
| `soundness.py` | what the kept graph got wrong or cannot settle: dangling references, duplicate labels, unused premises, undeclared assumptions |
| `claim_status.py` | a claim's one status for its author, folded from its records (model § 11), and its next step; read by `show`, `open`, `check` and the viewer alike |
| `check.py` | `axiomation check`: the gates over a kept state, and whether the paper passes |
| `inbox.py` | `axiomation inbox`: the decisions at a kept state that only a person makes, grouped; each unsupported claim's cheapest route |
| `sites.py` | the quotation and locator sites at a kept state, placed on their blocks |
| `marks.py` | the open stale marks at a state |
| `rereads.py` | a reread asked of an agent, as a queue: a task an owner of an open mark, its lines read against what changed and answered standing (a reread filed) or changed; the lines changed given as one edges edit; `reread --ask` runs it, and an update at its end |
| `update_queue.py` | an update's one session, asked as a queue on Claude Code: a task each block the edit changed or added, each block its Δ lines move by rule, each owner it reopened, each block that may rely on a part an answer added; served on the carried sheet, the paper beside as `paper.md`; the sheet filed as the session's answer |
| `circles.py` | a circle asked of an agent, as a queue: a task a circle, its ends narrowed by code from the typed answer or the circle read real (filed); the lines narrowed given as one edges edit; `circles --ask` runs it, and a build or an update at its end |
| `support_search.py` | a claim's support searched by agents, as a queue: a task a claim standing unsupported, the worker's answer and the settling step's confirm or own answer; the lines settled given as one edges edit, the claims read unargued filed as searches; `support --ask` runs it, and a build or an update at its end |
| `evidence.py` | the evidence contract: a cited page's and a script run's stamp, the read statuses, the provider protocol |
| `settings.py`, `computations.py` | a paper's settings (`axiomation.toml`), and the paper a directory is in, found by walking up; its code files, found, keyed and run |
| `paper_argument.py` | the paper's directory as a command's first argument: left out, the paper the current directory is in, put into the command line before it is parsed; taken by both command lines |
| `review.py` | the review sessions on a kept state |
| `review_queue.py` | what a review session asked as a queue shares: its tasks as its sheet shows them, each answer checked by its gate on a sheet of that one task, the find line, the tools that settle a finding |
| `methods_queue.py` | the methods session as a queue: a task each pair of a block and a script it uses, its run read through `run`, a typed judge whose line the gate checks (§ 7) |
| `viewer/` | `axiomation view`: a read-only page over a kept state, every read the command line's own JSON |
| `sources/` | the cited works: their corpus, its providers and verifiers, `library/` the corpus in Qdrant (the `library` extra); the citationcheck session as a queue (`citation_queue.py`, § 7); the fieldresults session's queue after its inventory (`fieldresults_queue.py`, § 7) |
| `commands/` | the commands as functions, a module a family: each command a typed request and its run, its arguments beside it; what a command says goes to a `Say` sink, never printed |
| `cli.py` | the `axiomation` command: the parser, each family's arguments in turn; the dispatch, the errors and the exit codes; `view` |

**The direction of dependence** is spine → records ← resolver ← runner ←
driver. The spine reads records; only the resolver writes them, and only
from an answer. The spine (reader, cutter, records, assembler, identity,
closure, changes) imports no question, runner, resolver or driver; the
questions import no runner, resolver or driver; the resolver no runner or
driver; the runner no driver. The reader knows nothing of blocks; the
cutter imports only the reader. `tests/test_spine.py` holds this. The
point is testability and reproducibility: the spine alone gives a
skeleton of blocks and marked edges, never the product.

**The front ends and the commands.** A front end (`cli.py`, the
viewer's server) builds a command's request and calls the command; a
command imports no front end, and no component imports a command or the
command line. A command says what it says through the `Say` it is given
and never prints, so a front end that owns its stdout (a tool server
over stdio) calls it as the command line does. `tests/test_commands.py`
holds this.

**The core, the cited works and the `library` extra.** The core's
runtime dependencies are `cryptography` and the cited works' PDF
libraries (pypdfium2, pypdf, fontTools, Pillow, bibtexparser). The cited works (`sources/`) may import the rest of the
core; the rest reaches them only at `evidence.sources_for`, the `sources`
command, the `review` command's citationcheck session and fieldresults
queue (queues whose workflows are the cited works', their tool server
opening them by name) and a review session's cited works' reads
(`review_tools.py`), each inside a function, so a command that reads no
cited work loads no PDF library; a review where no cited work reads asks
no citationcheck session. The `library` extra (Qdrant, Voyage,
fastembed, and Tesseract for the ingest's OCR; `sources/library/`) holds
the corpus in Qdrant: the rest of `sources/` reaches it only at named
sites, and imports none of its libraries but Tesseract, at the OCR
client's two. Installed, with the person's `qdrant_url` set (no Qdrant
is assumed), it is the full tier: a work's pages are read
from the corpus, a search is by meaning, a fetched copy is read by OCR
as an ingest reads it (`sources.config.library_on`; `sources status`
says which tier reads, and why). Unless both are there, the plain tier: a work's pages are
its PDF's text layer on this machine, read when asked and kept nothing
of (its entry's `file`, else `<pdf_dir>/<key>.pdf`, the person's
`pdf_dir`), a page with no usable text read as an image, a fetched copy
kept as its PDF and read so too, with no OCR and no search
(`sources/providers.py`). The two are never read together. `evidence.py` and `settings.py` are the standard library only.

**A provider** (`evidence.Provider`) is a way to a work's pages: the
library, the PDFs on this machine (`Pdfs`), a fetched copy
(`Transient`). `Sources` asks its providers in order and reads a work
from the first that holds it (`holds`); a provider that fails to say is
an outage, never a miss. A provider returns pages (`RawPage`: a page's
text, its index, where it came from) and never a status: `Sources`
stamps each, from the provider's `origin` (`library` and `file` the
version of record, `transient` a copy) and the page's text, so no
provider can call a page read that did not. Besides its pages
(`pages`), a provider says why a work it holds is held back (`held`:
its file is not its entry's work), which of the work's files it reads
(`copy`), the work's other files it holds (`other_files`), and the
file's SHA-256 and content hash (`file`, `content`), what a judgment
rests on; `search` is by meaning where `searches` says it can. A new
provider implements the protocol and is listed in `sources_for`: there
is no entry point until a second implementation outside the package
needs one.
`tests/test_boundary.py` holds both lines, and `make test-core` runs the
suite without the extra.

## 2. The question contract

Every agent step is a question, and each question has:

- **declared inputs, split in two:** **key inputs**, the exact spans and
  records its answer depends on; and **context**, given to help but not
  part of the key (the paper's text around a chunk, an outline). Changes
  elsewhere reach an answer through the reach rule and the re-asks, never
  by re-asking on context;
- **a template** whose version is the first 12 hex digits of the SHA-256
  of its text, so an edit can never reuse an old key;
- **a check:** every quoted excerpt occurs verbatim where it says, every
  target resolves, the file passes its stage's structural rules. An answer
  that fails is re-asked, three attempts in all, then recorded as failed,
  never silently dropped;
- **its key:** the hash of its kind, stage, template name and version, and
  key inputs.

**One authoritative answer per key.** The first valid answer for a key is
the one the graph uses, so the same text with the same records gives the
same graph, although a model is not deterministic. A changed template
makes new keys: re-running under a changed prompt is a deliberate,
costed act, and the old answers stay.

**Sessions on files.** A question may carry a workspace: files by path,
the one the session edits, and a standing brief. Its prompt only starts
the session; its answer is the edited file as the session leaves it,
checked like any answer and applied whole or not at all. A check may lay
the file into the form the store keeps: the build's first edges pass
(and an update's fresh session) reads `edges.md` and writes its lines by
owner in `lines.md` (a heading `@@ <handle>` for each block and group,
its done mark, then its lines; the legend and `Pervasive:` lines under
`@@ the header`), and the check lays them into the sheet, which is the
answer kept, so every later reader reads a sheet; a refused answer is
kept as written, and its re-ask starts from it. A later edges pass reads
the sheet the pass before it left, its lines in it, and writes only its
changes in `lines.md`: under `@@ <handle>`, `+ <line>` adds a line and
`- <line>` removes one (named whole, or an edge's or a standing's by its
head and quote); an owner not named keeps its lines, and `⟦done⟧` ends
the file, alone where nothing changes. The check applies them to the
sheet shown, which is the answer kept. A kept state's sheet, the one a later
pass, an edit, an update and a review read, is rendered from the
state (`edges_file.state_sheet`): the cut's skeleton and handles, the
export's lines under their owners in its answer's line order
(`line-orders`), a done mark on each; what the answer's sheet says on
that cut, line for line. The cut's author passes,
the roles' first pass and a touched roles session, and the groups'
sessions are asked so too: each reads its stage's file as `paper.md` and
writes lines that name blocks by handle and quote the paper's own words
(`split b12 "<words>"`, `join b13`, `move b13 "<words>"`; `b13
justification | claim "<words>"`, one a block; `group proof <name> b13
b14`, every group; a flag `b12 ? <signal> "<words>" — <note>`; the one
line `none` where there is nothing to write, since an empty file is no
answer), which the check lays into the stage's file
(`questions/front_lines.py`); the brief is the session's whole system
prompt and its tools Bash, Read, Edit and Write under Claude Code. A
person's edit of a stage's file stays an edit of the file. A workspace
may carry a **gate**, the stage's check, which the host runs each time
the session tries to end (and, for an edges re-ask, an update's
session and an edit, after each write) and whose problems go back to the
session to fix. The gate runs outside the session's sandbox, as the
host's own isolated Python on the package, on files the session can
reach: it reads and writes none through a link, nor any but a regular
file (`questions/gates.py`), and the host reads no answer file that is a
link. The runner's check stays the answer's.

**A question as a queue of tasks** (`queue/`). Code draws a workflow's
tasks from a kept state, or, for a question no kept state holds yet (an
update's session), from that question (`queue.Asked`): its key is then
what its tasks' keys bind, and the workflow gives its own reads in place
of the core's, which read a kept state. A session pulls them one at a time through
a **tool server**, MCP over stdio, the session's only tools: `next`
serves a task lean (its subject, its place, pointers as handles, and
only the text the workflow gives it: a block's words, or a changed
block's before and now), the reads give more on demand, each capped and
compact (`block`, `view` of 100 lines, `search` of at most 50 hits,
`edges`, `hops`, `standing`, `relied`, `group`, `cited`, and any a
workflow adds of its own, which may answer with a picture, a page's PNG
served as an image; a workflow's read of a core read's name is
served in its place; a handle is found as a reader writes it, in its
brackets, with other punctuation, as a label the paper writes or as the
one handle it is a part of), and a typed
answer, one tool each with its JSON schema, is turned into the
workflow's own lines by code and checked by the workflow's own gate on
that one task. A refused answer files nothing and says what to fix; an
accepted one returns the next task, and `next` serves an open task again
before a new one. The next task is the open one nearest on the graph to
the task settled last, else the first in the paper's order; a workflow
whose run writes the graph it would be near on is served in the paper's
order alone. The server runs outside the session's sandbox, as the
host's own isolated Python on the package, bound to one store, one kept
state or one question, one step and one spool; it reads the store and writes the spool
alone.

A queue session's brief is its whole system prompt, in place of the
host's own, which speaks of a coding agent's work and not of the task; so
is a review session's (`Workspace.brief_alone`).
How a workflow's sessions are **served** is its own: the core reads it
withholds; files laid in a session's folder (the paper's, say), which the
session reads with the host's own Read and Grep, its only other tools, a
read outside the folder a breach; and the tasks a session answers before
`next` tells it its share is done, where it would be served a new task
(so a later step settles the task it answered last), and the runner
starts a fresh one on the rest.

Every task served, every read and every check sees the **working
state**: the kept state with the lines the run settled so far laid over
its sheet. A task another answer settled leaves the queue, and the graph
reads show the lines settled. A task's key is its step's kind, stage,
template and version, its workflow's steps, the kept state, the task as
served and the key settled before it, so the run's answers replay in their order and only
in it.

A workflow may hand an answer to a **second step**, another model's
session, before it settles: both steps' sessions run at once on one
spool, in lockstep. A step is served only where the run stands at it;
its `next` waits otherwise, and returns "call `next` again" before the
host's tool limit. So the working state holds settled answers only.
When one session ends, the run ends for the other at its next call. A
step may pass a task of a kind it does not answer, which the next step is
served as drawn; the last step takes every task.

The **spool**, in the store's `sessions/`, holds each answer accepted,
in order. Once the sessions end the runner files what settled through
the resolver: one answer record per task and step, its `call` the typed
call (the tool and its arguments, which a resumed queue replays), with
its session and its order in the run, costing nothing; and
one record per session (kind `queue-session`) with its cost, its tokens
and the task answers it gave. The judgments a task files (a search's
word) go with the answer that settles it, never with one a later step
settles. A pair of sessions one of which breached files nothing. A
session's id begins with its run's, so a workflow tells its own run's
answers from another's. An answer accepted but not settled is
dropped, and its task served again. A run with tasks open and budget left
starts fresh sessions on the rest; a stopped run resumes after the last
settled task, and one asked of no host replays what the store holds. A
run's tasks stay as drawn: what its own sessions filed (a search, a
reread, a circle read real, a review session's judgment, filed in the
run's review round, § 7) does not take a task out of its queue when it
opens again.

An edges workflow's run ends, once every task is settled, with the
working sheet given as **one edges edit** (`runner/given.py`), the last
step's model's, in the run's session: a new kept state. Given before,
its state is found, not given again, so a replay reaches the same
state; a run with tasks open, or one a breach stopped, gives nothing.
The support search, the reread and the circles end so; the reread's
edit is recorded as shown the stale marks, the others' as shown none.

An update's edges queue (`update_queue.py`) ends otherwise: it answers
the update's one session, so it stands on that question, and serves the
new version from the carried sheet, the paper beside as a file read
only, read as the update read it (its root, its vocabulary map). A
question answered already is not asked again. Each answer is laid on
the sheet by code: its roles, its lines, its block's parts, its done
mark, and a split applied at once, its blocks drawn again as tasks; an
answer is refused for a problem it adds or one naming its own block,
never for a Δ it moves onto another, nor for a block its own split
opened, each that block's task. Once every task is settled, what the
check still finds is handed on so too, each problem to the block it
names, served again: a block the draw missed, which the update's summary
names (`mended_at_end`), a defect to report. The working sheet, every
task settled, is checked as the session's answer and filed under the session's
question, so the round, the marks and the export read it as theirs.

**An update's summary** (its JSON) gives what the reach did under
`reach`: `closed_by_delta`, the blocks a Δ closed, which the agent alone
closed; `moved_by_rule_unread`, the blocks whose meaning moved with no
one reading it (past the profile's `moved_delta_lines_max`, or moved by
a deleted block or a retracted edge); `asked`, the owners the rule
asked; and, once the state is kept, `open_marks`, `open_descriptions`
(the about lines no gate asks) and each of these per edit (`by_edit`).
`lost` lists each edge onto a deleted block that no line replaced;
`corrections_lost` and `groups_lost` the cut corrections and groups that
did not carry, with their words and why. The steps it ends with add `reread`,
`support` and `circles`, each counting what it asked, settled and spent.
Its dry run prices those steps where they would fire
(with `--json`: `reread_if_it_fires`, `support_if_it_fires`, `circles_if_it_fires`,
each on what the state it starts from holds, the reread and the circles
one task at least) and names the like runs its expected cost is fitted
on (`expected_by`). A
profile whose edges agent is on OpenCode updates per stage: its
`per_stage_update_above_recut_blocks` stays unset.

**Stages.** Each question belongs to a stage, recorded with its answer:

<!-- closed set: axiomation.questions.Stage -->
| stage | the session's file and what it answers |
|---|---|
| `boundaries` | `cut.md`: the cut's block boundaries and parts; an author pass reads it as `paper.md` and writes its corrections in `cuts.md`, laid into it |
| `judge` | `cut.md` with the author's edits flagged: keeps or declines each |
| `roles` | `roles.md`: every block's roles, in segments; the first pass and a touched session read the file as `paper.md` and write one line a block in `roles.md`, laid into it |
| `groups` | `groups.md`: the runs judged as one; a session reads the file as `paper.md` and writes every group as a line in `groups.md`, laid into it |
| `decide` | `cut.md` with every stage's flags: the cut's corrections, with evidence |
| `edges` | `edges.md`, the edge sheet: every block's edges, every claim's standing, the parts and the frame; in an update, the Δ lines; the first pass writes them by owner in `lines.md`, laid into the sheet; a later pass its changes there, applied to the sheet |
| `argument` | exploratory sessions outside the build, recorded so they replay |
| `review` | `review.md`: a review session's judgments and findings |
| `vocabulary` | `vocabulary.md`: what each name the reader's tables do not know is for (a statement and its base, a wrapper of an environment the reader knows, read as that one, a container, a list, a reference, a label, or none), with the paper's words as evidence |
| `vocabulary-check` | `vocabulary-check.md`: confirms or overturns each name the map answered `none` |

**The reader's tables.** Code reads the declarations whose meaning a
package fixes in every paper (`reader/declarations.py`): a theorem
environment (`\newtheorem`, `\spnewtheorem`, `\declaretheorem`,
`\newmdtheoremenv`, `\newtcbtheorem`, whose uses' title and label are
read, the label with its prefix), a frame (`\newmdenv`,
`\newtcolorbox`; `mdframed`, `framed` and `tcolorbox` used directly), a
`\newenvironment` that opens one environment the reader knows and closes
it, read as that one, thmtools' `restatable` (a statement of its first
argument's kind, its command a reference to it), a one-argument command
whose body is one reference over `#1`, and the reference commands of
jmlrutils, refstyle, fancyref and prettyref where the paper loads them.
What a paper's use decides is the map's.

**The vocabulary map.** The reader lists every name its tables do not
know (`reader/vocabulary.py`): an environment used outside a display, a
float or a listing, or declared by `\newenvironment`; a command the paper
defines with a reference in its body; a command whose every use names one
of the paper's labels; a command around a target nothing labels. The
`vocabulary` session is shown each one's declaration and first uses in
the paper's words, never the whole paper, and answers each in the
reader's own kinds; its
check refuses a missing or extra entry, evidence that is not the paper's
words, and what code refutes (a command found by its uses or its
definition is a reference; an environment declared over an environment
the reader knows wraps it). The `vocabulary-check` session, on a cheap model, reads only the
`none` answers with their uses, and its overturns are checked the same
way. The map is the first answer with the second's overturns, and the
reader reads it on top of the declarations, a name a table knows keeping
what the table says. A name declared more than once with different texts
(a `\renewcommand`, a `\renewenvironment`, in any file) is a candidate
for each declaring text, its entry numbered and shown the uses where that
declaration is in force: the last before them in reading order, the first
for a use before any (`reader/vocabulary.py`, `in_force`); a later
definition with no reference in its body is listed as `redefined`. The
reader reads each use under the answer of the declaration in force there
(`reader/layer.py`); TeX's grouping is not followed. Its key is the
candidates' names, shapes and declaring texts, never their uses: an edit
inside a box asks nothing again, a new name or declaration does. A paper with no such name asks nothing. A build
or an update that cannot answer the map stops before the cut, since every
later stage reads the cut.

The export keeps each answer with the declaring text it was given under,
and an update carries the map from the state it starts from: it asks
only the declaring texts that state's map does not answer, a changed,
added or removed declaration of a name among them, and drops the names
gone. A person or an agent
corrects the map on a kept state with `edit --stage vocabulary`, the
whole `vocabulary.md` as the state's map fills it, under the first
round's check; the second round never runs over it. It is applied as the
update of its version onto itself: the kept files read with the map
given, every block that reads a changed entry re-cut and what that
reopens asked ([the model](model.md) § 5), kept as an `edit` state. A
build of that version lays the given map over the model's where its file
is the one shown. An answer keeps its author: an agent's correction may
not change a person's answer, nor re-cut a block or group a person
pinned; a person's lapses the pins on what it re-cuts.

A build asks, in order: the vocabulary map and its check where the paper
has a name its tables do not know, the boundaries, the roles, the groups, the
deciding step where the profile asks one, the edges and their later
passes, then the rounds of the edges' cut corrections. Each stage reads
the one before's output. An update asks one edges session on the carried
sheet as a queue (`update_queue.py`): a task each changed block, old
beside new, its lines and Δs its own, and each block it reopened (an
edge lost onto a deleted block, a block about the whole paper) read
again, below the profile's threshold of re-cut blocks; past it, each
stage on what changed.

**Answers given on a kept state.** A person's or an agent's edit is the
stage's own question with the whole paper shown; its key is the stage,
the author's host (`person` or `agent`), the version's document hash and
the hashes of the file shown and the file edited, with no template
version, so no brief change moves it. The marks and Δs shown beside the
lines are left out of the key.

**Every answer is recorded with** its key, kind, stage, template and
version, attempt, host and model, effort, profile, time, seconds, cost,
tokens, validity and problems, and the ids of the records made from
it, never its text (the records are what it says; its text lives in
its run while the run applies it, and in develop mode below); a given answer also its version, an agent's session and its
reason, and lines given without the sheet (`change`) that their author
was shown no stale mark. Its tokens are the whole attempt's, over every
model it ran and every turn it was woken for, its cache writes' 1-hour
part apart (`cache_write_1h`); where it ran more than one model, the
same per model (`tokens_by_model`). A queue task's answer also records
its order in its run; a queue session's record the task answers it gave
(`tasks`). That makes a run resumable, its cost countable and its answers
replayable.

## 3. Hosts and profiles

One host interface: a prompt (and its workspace) goes in, an answer comes
out. Behind it, one adapter per harness:

<!-- closed set: axiomation.profiles.Harness -->
| harness | |
|---|---|
| `claude-code` | Claude Code run headless: a session with its tools but the web, Bash in its sandbox, writing the attempt's directory and its own temporary directory alone (never the rest of `/tmp`, where the host's settings, the gate's spec and the tool server's command lie), no network; a review session on the reading tools alone; a session whose workspace names its tools (a review by block, a stage answered as lines, the vocabulary map) on those alone, whatever the profile gives; a review session's, a stage answered as lines' and the vocabulary map's brief their whole system prompt; a queue session on its tool server's tools alone. Runs a gate at Stop and after each write |
| `opencode` | `opencode run`, one process per attempt: a question with every tool denied, or a session that may only read its directory and edit its answer file; any other tool use fails the attempt, and one outside the directory is a breach. Runs a gate once the session ends. Serves no queue session. Experimental: refused unless `AXIOMATION_EXPERIMENTAL_OPENCODE=1` (`seating.allowed`) |

**Isolation.** A question sees only its declared inputs and context. The
host runs on a scratch copy of the paper outside any repository
(`/private/tmp/axiomation-host`, or `AXIOMATION_HOST_SCRATCH`) and is
given no path outside it. The confinement is the host's: permission
rules or a sandbox; a working directory confines nothing. After each
attempt its tool inputs are checked for paths outside the copy.

**A queue session** (§ 2) under Claude Code has no built-in tool
(`--tools ""`) and no MCP server but its own (`--mcp-config`,
`--strict-mcp-config`), so any other tool that runs is a breach; it
compacts its context at its step's bound (`--autocompact`), and the
host's tool limit (`MCP_TOOL_TIMEOUT`) is set above the server's longest
wait. Its prompt cache is kept short (`CLAUDE_CODE_PROMPT_CACHE_TTL`,
`claude_code.SERVED_CACHE_TTL`), its calls coming seconds apart; so is a
review session's by block, on its tool server. A run's steps share its budget: each session's limit is its
step's share of what is left. A step that searches the web
(`QueueSession.web`, the fieldresults queue's finder and reader) is given
WebSearch beside its server's tools, allowed in its settings and in its
confinement check; never a web fetch. Under OpenCode a queue session is refused,
and a profile whose build or update ends with the search, the reread or
the circles on an OpenCode agent is refused when it is read.
The support search's steps are a profile's table (`support_search`):
its worker and its settling step, each an agent on Claude Code with its
`budget_share`, and the bound its sessions compact at
(`autocompact_tokens`); a profile without one searches in one step on
the edges' agent. Its dry run prices each step as one session's start
and its tasks: the start fitted on recorded runs and scaled to the
model, a task measured on the store's sessions of that step on that
model where it holds any, else fitted (`runner.queued.price`). The reread and the circles are one step on the
edges' agent, compacted at the same bound, priced so, a task an owner or
a circle. A queue run is named by what it asks and by whom: its model
and the profiles file it is read from, so a run resumed or replayed
under another `--model`, or from a copy whose profiles file lies
elsewhere, is another run. The citationcheck session's seats are its review table
(`review.citationcheck`): the reader, an agent on Claude Code with its
`budget_share`, and the finder before it (`review.citationcheck.finder`), one
too; without the finder, the reader finds its pages itself. It is priced
so, a task a site or a finding to settle.

**Profiles.** Who answers each stage is a profile's: per stage a harness,
a model, an effort and a prompt template, and the run's settings (passes,
rounds, thresholds). The shipped `profiles.toml` documents each setting
in place; `--profiles FILE` adds one's own. A model's price
(`prices.toml`) is for the estimate only. A profile is recorded on each
answer but is not in the key: two profiles on the same templates ask the
same questions, so a store filled under one asks nothing under the other.

**Stopping.** Questions run under a budget per run, and a run stops
cleanly and resumably, leaving every finished answer recorded:

<!-- closed set: axiomation.runner.Stop -->
| stop | |
|---|---|
| `budget` | the next attempt could pass the budget, an attempt reached what was left of it, or, under a budget the settings gave, a step is expected to cost more than is left |
| `credit` | the host refused for billing |
| `host` | the host failed several attempts in a row |
| `breach` | the model touched a path outside its directory: the run ends at once |
| `uncounted` | the host could not say what an attempt cost |

Before each attempt the runner reserves its estimated cost against the
budget and stops if it could pass it. The estimate is one attempt: the
harness's own prompt written to the cache once (each host's
`prompt_tokens`; none for OpenCode), the brief and the files read once, a
fixed output. A short session costs about it; a long one, which
reads its growing context again every turn, several times it. So each
attempt is also given what is left of the budget, less what the attempts
in flight reserved, as its limit where its host takes one (Claude Code's
`--max-budget-usd`; OpenCode takes none): an attempt that reaches it is
ended, recorded with what it wrote, and stops the run (`budget`), never
asked again as a failed answer. Claude Code checks the limit after each
turn, so an attempt may pass it by the turn it was in. A run under Claude
Code thus ends near its budget, past it only by that turn and by what
the other attempts in flight spend at once. A
dry run (`--dry-run`) prices a run and asks nothing. A review session's estimate adds what its turns cost at the
model's rates, fitted on recorded sessions: the session's own turns,
and each subject and each finding it settles (`review.py`, carried on
its question as `turns_estimate`); a sheet of a few subjects costs less
than it says.
Each attempt records its estimate on its answer (`estimate`), so the
store holds what like runs cost: a run's expected cost is its estimate
times the store's cost over estimate across the answers of its template
(its name before any `+`), and times a fixed factor
(`runner.UNMEASURED_FACTOR`) where the store holds none, an estimate with its turns fitted taken as
it is (`runner.like_runs`); the
update's and the review's dry runs print it beside the estimate. A run
given no `--budget` takes the budget default, the paper's, the
workspace's or the person's `run_dollars_max` (§ 8), and says so; its runner then asks nothing of a step expected to cost
more than is left, one attempt a question, and stops (`budget`). `sync`
chains `update`, where the paper's text is ahead of the latest state,
`reread --ask`, `support --ask` and `circles --ask` as the profile ends
an update with them, and `review`, within one budget, each its own command's
run, and stops at the first that does not end whole. An update's dry run prices each edges session of an update per stage at two attempts
(`attempts_priced`, one attempt's `attempt_estimate` beside it): its gate
asks a Δ line of every block the edit moves, and a first attempt it
refuses, in the session or after it, is asked again. An update of one
session is a queue: its dry run prices the tasks it serves first
(`tasks`) as the support search's steps are priced, at one attempt; the
blocks its answers move by rule or split come after and are not
counted.

## 4. The record store

A paper's store is `<paper>/axiomation/` unless `--store` names another.
The reader never reads that directory. Its record, `record.jsonl`, is
committed with the paper; a `.gitignore` written on first use (never over
one already there) keeps its transcripts, locks and cache out, and a
`.gitattributes` merges the record by `merge=union`, since the order of
its lines carries nothing.

**A run's working state** (a build's, an update's, an edit's, a
review's) is run
data, not an entry: `runs/<command>.json` in the store's directory
(`run_state.py`), one file a command, kept out of git. It holds the
run's cursor (the version, a hash of the settings and templates it was
made from, the stage and pass it reached, the edges answer it stands
on) and the texts of the edges answers and review sheets it stands on,
by key: a review gives its sheets' edge, group and inventory lines from
there, and the fieldresults queue takes the results to source in its
own options, kept with its spool. An
answer's text is written there before the answer is filed and the
cursor moves after, each write whole through a renamed temporary file:
a run stopped between finds the answer filed and its text held and
applies it; one stopped before filing asks again. The same build on
the same version, made from the same settings and templates, resumes
it; any other starts anew. Kept whole, the state removes it, and
records the settings hash it was made from (`made_from`), as
provenance only: a build of a version with a whole state asks nothing
and returns that state, whatever its settings, and a run file it finds
is a stopped run's that kept it, removed. An identity answer's payload
(the diff's JSON) is held the same way, in `runs/identity-<NEW>.json`,
from its filing until its update keeps its step, so a `diff` and the
`update` after it share it.

**Develop mode**, for contributors (`develop.py`): with
`AXIOMATION_DEVELOP=1`, the resolver appends each filed attempt's key,
attempt and text to `develop.jsonl` in the store's directory, in clear
and local: the store's `.gitignore` keeps it out of the paper's
repository. The tools that read an answer again by its key read it there
and are refused without it: `review --file-answer`, `build --replay`,
and a replay (`--host none`) of answers no run of its own holds. No
other path reads an answer's text once the run that asked it has ended:
a run holds its answers' texts until it ends whole, and a later command
reads what the answer filed, an edges sheet as the kept state's export
renders it (`edges_file.state_sheet`), its unweighed `✂` lines from the
answer's filed corrections.

**The record** is append-only, one entry a line: a clear header (its
hash, its format, its parents (the entries no other named as its parent
when it was written), its writer and the epoch and nonce it is sealed
with) and its body, sealed. The plain body is the entry's kind, a random
salt and what it holds (its keys in the order given, which a read may
follow); the hash is taken over the plain body's hash, the format, the
parents and the writer, as one object written in that order, so one who
cannot open a body still reads the graph through the headers, and the
salt keeps a short body from being guessed and confirmed on its hash.
The loader opens each body, checks it against its hash, refuses one that
does not hold what its hash names, whose parent no entry before it is,
or of a format it does not read, and skips one it has read. One that
does not open under its grant's paper key counts for nothing and is
listed (`RecordStore.problems`): a hostile line appended keeps no one
from the record.

**Keys and the cipher** (`keys.py`). A person makes their reading key
(X25519) once, with `axiomation keys --make`, in `axiomation/keys` in
their configuration directory (`AXIOMATION_KEYS` names another); a
recovery key is made with it, its secret shown that once to be kept
offline and its public half kept. A record is sealed under a paper key:
its first entry is a `grant`, which wraps the paper key to its writer's
reading key and to the recovery key, as `age`'s recipient stanza does
(X25519, HKDF and ChaCha20-Poly1305, each stanza naming its reader's key
by id), and is the one entry not sealed. Every other body is
compressed, then sealed under its epoch's paper key with
ChaCha20-Poly1305, the clear header its associated data: a body sealed
without the paper key, or a header changed, does not open. A seal proves
only that a holder of the paper key wrote the entry: not which member,
nor whether a person or an agent; an entry's writer is a claim, and what
must be a person's is signed (below). The recovery key's secret, set as a reading
key, opens every record its owner wrote. An entry's writer is its
writer's reading key's id, as its writer claims it. `RecordStore.opened` gives every entry
opened, the record's plain view.

**The person's word** (`keys.py`, `records.py`). `keys --make` makes a
signing key beside the reading key, an OpenSSH key locked by a
passphrase `ssh-keygen` asks at the terminal, never on a command line:
an agent's shell can read the key's file, not sign with it. An agent's
work is its person's: it takes effect sealed, and their next
confirmation vouches for it. What only a person does (a grant, a
dismissal, a ruling, any filing of theirs) takes effect only under the
passphrase. The boundary holds against an agent misled by what it reads;
not against one set to deceive, which runs as the person and could
change the code that asks them to sign. A person's answer (its host `person`:
a filing, or an edit of a stage's file, by the person or by an agent in
their name) takes effect only under the person's word: a `confirmation`
entry, its parents the heads it saw, signed with `ssh-keygen -Y` in the
namespace `axiomation-person` over its writer, its heads and the entries
it drops. It covers every entry under it, so a load checks the newest
confirmation first. A person's answer it does not cover is read but
without effect (`RecordStore.without_effect`): its filing's records are
out of `judgment_records`, its edit out of the person rounds, and a kept
state made from it, or built on one, out of the kept states. The
person's own next edit is the exception: `sheet`, `edit` and `change`
open the latest state, waiting or not, so two edits in one sitting
build one on the other. So does every command that keeps a state, and
what it keeps waits with the state it is kept on; one that would keep
a state beside a waiting one (an OLD named otherwise, a build) is
refused before it asks or files
(`versions.refuse_beside_waiting`, in `commands.kept_on`, at
`update`'s start, in `build` before it asks (a version built already
asks nothing), and in `versions.record`); a dry
run prices as if the waiting edits stood confirmed. A drop is
read by ancestry, so either join order reads alike
(`_Loaded.taken_back`): a row of a state is made from the uppermost
attempts under it of each answer it adds to its parent's, and goes
where, for any one answer, every one of those attempts is dropped, or
where every row of its parent under it goes; a state goes where every row of it does. With them go
each answer given on such a state (`AnswerRecord.state`, every row of
it under the answer gone); a person's among them is dropped with it by name
(`confirm` refuses otherwise, saying which edits an earlier drop took
and which this one takes; `confirm --list` shows what this drop takes
back; a key names every pending attempt of it, so one edit given on two
joined copies is dropped whole, and the list shows each attempt's entry
to name one alone), an agent's goes with it. While a person's edit waits, an agent's answer
given on it waits too, out of the reads until the confirmation.
A later attempt of the same key stands on its own. A paid run refuses
while a filing of the person's waits, but for the edits it answers
itself (`seating.take_budget`'s `own`). A node a waiting state mints is never
minted again. One it drops never takes effect. Every other entry is in effect when written,
listed as pending until the next confirmation. A confirmation is checked
against the signing key its writer has in the trusted grant it stands
on; one whose signature does not hold confirms nothing and is listed,
the record still read. A drop takes back only a pending filing of the
person's: never an entry an earlier confirmation covered. Nothing
confirms for the person: while a filing of theirs waits, the reads and
`check` say so, and every run that asks a model refuses, naming it
(`RecordStore.refuse_waiting`). `axiomation confirm` lists the pending
entries and signs once for all of them, `--drop` naming the answers
left out (`RecordStore.pending`, `RecordStore.confirm`).

**Members and epochs.** Each grant names its epoch's members (each one's
reading key and signing key) and the recovery key. The record's first
entry, a grant naming its writer, founds it; every later grant is
signed by its writer (`ssh-keygen -Y` over its writer, parents and body,
apart from a confirmation's message), a signing member of each trusted
grant it stands on; a confirmation never trusts a grant it covers. A
grant not signed so is refused, listed, and gives no key: only a
trusted grant's paper keys are held, and a new entry is sealed under the
trusted grant the record stands on (the latest under its heads, by
ancestry, never by line order or epoch number). `axiomation members
--add CARD` or `--remove ID` writes a new epoch's grant, asking the
passphrase: a new paper key wrapped to its members and the recovery
key, and every earlier epoch's key to a member it adds, who reads the
whole record. A removal closes the epoch it ends at the entries under
it: a removed member reads nothing written after, and what they write
on a copy from before it counts for nothing. An entry sealed in another
epoch than its grant's counts for nothing.
A member's card is one line, `axiomation keys --card`. A key is retired
the same way: its owner adds their new key's card, then removes the old
key using the new one (`RecordStore.grant`).

**Copies joined.** Two machines append to their own copies of a record;
git's union merge (`.gitattributes`, `merge=union`) joins the file, each
side's lines in their order, and `axiomation join DIR OTHER` joins a
copy outside git (`RecordStore.join`: the other's entries this record
lacks, appended in their order, refused where the other is no copy of
this record or the two would not load together). Two grants made apart,
each trusted, stand side by side: each member reads what the grants
they are in give, the rest listed, and nothing is written on the record
until `axiomation members --settle` signs one grant over both, for the
members both name (one added on one side alone is added again by name:
a union could undo a removal).

Whatever else the record holds made apart is read by ancestry, never by
the lines' order, so either join order reads the same values or names
the same conflict (`check.made_apart`, on every read's notes
and on `join`; `check` fails while one stands); merging the two copies'
work is not built:

- **States.** The latest is the state every other was written before
  (`versions.tips`). Two kept apart, waiting ones counted for a person's
  next edit, leave no latest: a read names one with `--state`, and no
  state is kept on top of them: a command that keeps one refuses
  before it asks, whichever is named (`versions.refuse_apart`; a
  command that builds on a state takes it through `commands.kept_on`). The same state kept on both copies, or twice on one, is one.
- **Answers.** A key's answer is its first valid one by ancestry
  (`_Loaded.firsts`): one under the other is first, whatever the
  attempt numbers. Two made apart that hold the same records are one;
  two that differ are a conflict, neither read nor reused, and the key
  is not asked again: no run serves it and no answer to it is filed
  (`RecordStore.answer_conflicts`, `resolver.torn`). A kept state still
  reads the answer it was built on, the one under its entry
  (`RecordStore.seen_from`).
- **Filings.** Filings are listed in one order every copy reads alike:
  each after what it was written on, those made apart by their hash
  (`_Loaded.ranks`, `RecordStore.filed_order`). Whether one was filed
  after another is read by ancestry alone (`records.filed_after`): of
  two made apart neither is, and a person's reading sets aside only the
  readings it saw (`records.unseen_by`). Where filings made apart speak
  in one place and say differently (a judgment's subject and aspect, a
  ruling's subject, a finding's or a suggestion's answer), that place is
  a conflict (`RecordStore.filing_conflicts`).

An entry written after both sides settles none of these, nor does a
confirmation over them. Node ids are drawn at random when made
(`docs/model.md` § 7), fifty random bits, so copies kept apart are all
but certain never to mint one twice; a repeat is not detected.

**What each party can check.** A file alone cannot show that nothing
was cut from its end: an older graph is valid by itself, and one who
writes the file can give a reader an older copy. Removal is found only
against a tip held apart from the file: each reading of a record keeps
its heads as this machine's tip of it, beside the person's keys
(`tips/`, by the record's directory, so a second copy on one machine
holds its own, and a record cut to nothing or removed whole still finds
it), and a record that no longer reaches that tip is refused, cut short,
set back or cut to nothing. Its person, who set it back on purpose,
removes the tip the refusal names. A record that holds no entry yet keeps
no tip. A fresh clone holds no
tip: its first read says so, and a cut is caught from then on, not
before.

| party | can check | cannot check |
|---|---|---|
| a member (a key a trusted grant wraps to) | each entry unchanged (cipher, hash), its grant's signature, a confirmation's signer, its parents; that nothing was cut from the end since its machine last read it | which member, person or agent, sealed an entry not signed; a cut before its first read (a fresh clone) |
| one holding no key | the graph of entries through the headers | that a body matches its hash |

An `answer` entry
holds one attempt at one question, valid or not, and every record made
from it, by kind, each kind of its records kept as its change
(`kept_change.py`) of the latest records of that kind, each without its
id where the ids count up within the answer and without its answer where
all name it; the answer's own text is never in it, and an entry holding
one is refused. The loader rebuilds the records, and the answer's record
ids from them, exactly as given:

<!-- closed set: axiomation.records.RecordKind -->
| kind | holds |
|---|---|
| `cut-corrections` | cut corrections (split, merge, rebound, group, ungroup, attach, part, unpart, roles), each with its answer and evidence |
| `entries` | the parts the edges name |
| `edges` | the edges |
| `self-marks` | the self marks |
| `standings` | the standings |
| `frame` | the ambient frame |
| `deltas` | an update's Δ lines |
| `line-orders` | an owner's edge, self mark and standing lines in its sheet's order, by kind, where kinds interleave |
| `flags` | the stages' flags |
| `lineage` | an identity answer's links between two versions' cuts |
| `vocabulary` | a vocabulary answer's entries as its check read them: the map is made from them |
| `declines` | a judge's declined edits, settled: the next authors' ledger lines |
| `judgments` | judgments |
| `findings` | findings |
| `finding-answers` | findings' answers |
| `finding-answer-withdrawals` | a person's withdrawals of findings' answers |
| `files-seen` | a cited work's files as a review, a recheck or a filing last read them, stamped with their SHA-256 and content hash |
| `works-seen` | a cited work's DOI, publication and versions of record, by a lookup or a person |
| `works-accepted` | a person's word that the paper rests on a cited work as it is |
| `runs-seen` | a script's runs, stamped |
| `rulings` | rulings: whether the paper owes an unargued claim its argument |
| `ruling-withdrawals` | a person's withdrawals of rulings |
| `rereads` | a person's or an agent's rereads of stale-marked owners |
| `circles` | a person's or an agent's word that a circle of what claims rest on is real, by its key |
| `searches` | the support search's word that it searched the paper for a claim's support and found none |
| `suggestions` | suggestions: a work the paper may want to cite at a place |
| `suggestion-answers` | the author's answers to suggestions, taken or declined |
| `contribution-checks` | the contribution session's checks: a contribution read against the literature, a `positioning` claim's reading |

Each record names the answer it came from. The resolver is the only
writer: it numbers each attempt under a lock and appends it with its
records, one line, so a crash leaves at most a last line cut short, which
the loader reports and skips; an answer that names a record its entry
does not hold is reported and ignored. A run holds an exclusive claim on
its store. The index, the first valid answer per key, is computed on load
and never written. An open store reads the record once while it stands
(its inode, size and time, which every append moves, whatever process
writes), so the reads walking many states read it once; a record that
still begins with the bytes it read (their digest) was only appended to,
and its new lines alone are read. Every entry's head and hash are checked
as it is read; the answers' entries are read past their heads the first
time a read wants an answer, so a read of the kept states alone rebuilds
none. An answer's records are read into their kinds where a read wants
them. A filing (a judgment, a finding, an answer
to one, a ruling, a reread, a suggestion or an answer to one, a page or a
run seen) is an answer of its own, keyed by its content's hash. A
record's field is written only where set: an entry without it loads
with the field unset.

**The kept states** (`versions.py`): a `state` entry holds one state
(§ 7 of the model): its id, parent, command, version, files, the name of
its export (its content's SHA-256), identity answer and the answers it
applied; its lines' change against its parent's lines; each block's text
the record does not hold yet, once per distinct text; and each file the
run read that the record does not hold yet, named by its content's
SHA-256 and compressed, or, where it is text and the parent kept the same
file, held as the runs of that file's lines it replaces, where those are
the smaller.

The first state an update keeps holds its **step**
(`versions.identity_step`): its identity answer's raw less what the
states give, the answer it keeps and the state the update started from.
Its links are the `lineage` records; its carried lines' authors
(`carried.origins`) are not kept: NEW's export holds them, and a re-run
after the step was kept reads them from the state kept with it
(`update.carried_origins`); the carried sheet is kept as its own lines
(`edges_file.SheetLines`: the files, the legend and pervasive lines,
each owner's lines but its done mark), which the installed version renders on the
rebased cut on reading (`edges_file.lines_sheet`), or whole where that
rendering does not give it back byte for byte; the rebased cut as its
change of NEW's cut, the carried excerpts as their change of those NEW's
export lists (`kept_change.py`). `versions.step_raw` gives the rest back
exactly; `versions.identity_raw` reads an identity answer's raw from
its step, else, before its update keeps one, from the payload held. A
transition reads the step's rebased cut alone.

The step also keeps what later reads take from the update's sessions'
answers, read from them once (`update.Update.session_facts`,
`versions.session_facts`), never their text: the one session's marks'
context (`update.MarkContext`: the graph walked, its handles and block
ids, the roots, the rule's Δs, the Δ lines in order, the owners marked
before the walk, those shown done, its own blocks, the cut it was asked
on), and the owners a first session's answer opened for a follow-up. A
Δ corrected later walks the context again with the installed version's rule
(`update.marks_from`), the marks filed before standing. A dry run of a kept update reads the
follow-up from the step.

A state's lines (`state_lines.py`) are its node table (a node id per
block, with the block's id and text, and per group) and its export split
into sections, one per list or mapping of it (the blocks, the edges, the
document's environments, the update's excerpts), what is left its
skeleton. A list's items are named by their content, a mapping's by its
keys; in each, a block's or a group's id is written as its node, and a
span inside a line (an edge's evidence, a block's owned runs) is placed
from its owner's start, so an item changes only where its owner's text or
its own words do, not where the text before it moved. An edge's id, its
record's, is rendered from its place where the edges' ids follow one
rule. The export is rendered back from the lines byte for byte and
checked against its name when read; an export its lines do not give back
when it is kept is kept whole beside them. Beside the record:

- `cache/runs/`: a script's runs by content, runner and arguments (not
  committed).

**The derived values**, in the user's cache, never in a paper or its
store (`derived.py`; `AXIOMATION_CACHE`, else `$XDG_CACHE_HOME/axiomation`,
else `~/.cache/axiomation`): what kept states and records named by their
content give, worked out once and kept. Each export rendered from its
state's lines, each export's outline, each
step's transition, each state's handles folded along its chain, each
version's run of marks' first states, each state's later-rule problems
under its open marks (where its files were read) and each state's walks
(below). Each value
is named by what it comes from, so none goes stale and two stores sharing
a state share its values; a directory named by the package (its code,
briefs and tables) holds them, one compressed file a value, so other code
derives them again. Nothing derived travels with a paper: a value is read
only from a directory that is the user's alone (theirs, and written by no
one else), since reading one runs what it holds. A value kept does not
read its states' blobs again: every entry is checked against its hash
as the record is loaded. The cache keeps the values used last, up
to a bound, and may be removed at any time: a command answers the same
without it, slower.

Neither files nor exports can be recomputed (a brief change moves every
key), so both are kept, an export as its lines; everything else is
derived: a version's graph is
its kept export, a node's history is computed from the exports along the
parents. A reading's walk along the states (the model's § 8, "When a
judgment lapses") is a function of its record and the states it crosses,
neither of which changes, so it goes on from the latest state it was
walked to before (`judgments.Carry`): a state kept costs each reading
one step, not the chain. One process reads each kept state once while
the kept states stand (`judgments.kept_reader`), whichever read asks.
States, texts and blobs are appended, never rewritten; a blob already
present is checked against its hash. `versions.py` writes them, under
the store's lock, only after a whole run.

A kept state is read by the installed version: its export is what it holds, read
back without deriving it again, and a rule added since judges only what an
answer on it says (the model's § 7, "Under later rules"). Its kept files,
read by the installed parser, carry the state's version
(`Document.version`, which `document_hash` returns where set): every key
and answer on the state names the id it was kept with, whatever the
installed parser hashes. A paper's files take a kept state's version where
the installed parser reads them as that state's kept files (`versions.as_kept`), which
is what `states`, `blocks`, `changes`, the viewer and `update` compare. Nothing in the
store names the code that wrote it: an export's shape changes only by
fields added, written where set, so a state read back sets aside a value
the code derives and compares what its lines say.

## 5. The exchange format

One JSON document per paper state, `"format": "axiomation-exchange"`,
`"version": 1`, written by `exchange.py`. Its keys, in order:

| key | holds |
|---|---|
| `format`, `version`, `paper`, `producer` | the header: the format, the paper's root file and format, what wrote it |
| `paper_version` | an update's: the new version's document hash |
| `document` | the document half: the files with their hashes, includes, sections, environments, labels, references, citations, footnotes, macros, comments, directives, quotations, and the vocabulary map it was read with (`vocabulary`, only where it has one: what the reader reads, and each answer with its declaring text and, where a person or an agent gave it, its author), which a kept state is read with again and an update carries |
| `rules` | the cut's display rule |
| `blocks` | id, extent, owned spans, the block it lies in, form, environment, meaning, roles, segments, hash, doubts |
| `parts` | the parts the text marks |
| `groups` | id, kind, blocks, role, the result it names and how |
| `candidates` | the marked edge candidates |
| `entries` | the parts edges name: id, block, anchor, span, role, and `standard` where set |
| `edges` | id, source, evidence excerpts with spans, target (with `claims`, the words of the target's standings it names, only where set), kind, mark, `where_used`, `generalizes` (only where set), `partial` (`sketch` or `analogy`, only where set), `source_claims` (an about edge's restating standings' words, or those an `assumed` edge is a hypothesis for, only where set), `assumed` (a uses edge whose source's statement takes its conjectured target as a hypothesis, only where set), provenance (`by`, model, question, detail), note |
| `self`, `standings`, `ambient` | self marks (with `partial` and `claims`, the standings it supports, only where set), standings (block, claim words and span, status, `declared` (`omitted` or `promised`, only where set), `handle` (the sheet's, where its ⊢ line defines one or its block holds several claims; shown, never an identity)), the frame's blocks; each self mark and standing with its provenance |
| `handles` | each block's and group's handle on the sheet its edges answer was written on, which the export's own records name it by: its node's handle (`docs/model.md` § 7) |
| `edges_answer` | the answer whose sheet is the edge half |
| `review` | how the run got here: the questions, the unanswered, the cut corrections placed and applied or not, the flags; an update's `update` (old and new hashes, reopened owners and why, Δs, stale and moved marks, cost); a person's or an agent's edits, `person` |

A span is a character range, 1-based `[line, column]` pairs with the end
exclusive; each file lists the spans it does not render. A block id is
`<file>:<line>:<column>` of its first owned character. A key written only
where it has something (an update's, a person's) is absent otherwise, so
an export that needs none reads as before.

## 6. What a consumer can rely on

- **The export is reproducible:** the same text with the same records gives
  the same export byte for byte, and a kept state's export reads back
  exactly as its run wrote it (`axiomation graph`).
- **A node has an id that lasts:** a block's or group's node id is minted
  once and kept across states and versions by the rules in the model's
  § 7. It is the kept states' and the reads' JSON, not
  the export's: the export names blocks by block id, a position within one
  cut that changes with it.
- **Edges, standings and self marks** carry their kind, target, evidence
  and author; across states they are named by their nodes and words.
- **The reads** (`show`, `reach`, `blocks`, `changes`, `open`, `check`,
  `history`, `states`) are bounded and give JSON with `--json`; the viewer
  reads only these, and the store's revision (its record's size and
  time, which every append moves) to know when to read them again. `check`'s JSON is whole, never bounded, and the
  command exits 1 when a gate fails. A kept state never changes, so what
  is worked out from kept states alone (the steps between two, a state's
  document and its text layer's layout) is kept while the process holds
  it (bounded, the least recently used going first), and the values § 4
  names are kept in the user's cache across commands; what a filing
  moves is worked out again once the revision moves, and the viewer keeps
  each answer read from the store alone while the revision holds. A read
  writes nothing in a paper or its store.
- **A judgment** binds to a kept state's node ids and records its basis;
  a subject's status per aspect and `established` are computed at any
  state, never stored.
- **A reader ignores keys it does not know,** and the format's version
  moves only when a key changes meaning.

## 7. Review sessions

A review session (`axiomation review`) is a question on a kept state whose
answer files judgments and findings. Each session owns its questions:

<!-- closed set: axiomation.questions.review_file.Session -->
| session | judges | finds on | its subjects |
|---|---|---|---|
| `argument` | `valid`, `true`; rules on each claim standing `unsupported` | `valid`, `true`, `well-posed`, `accurate` | the paper's claims and their justifications |
| `citationcheck` | `cited` | `cited`, `accurate` | each citation of each work: its block, its words, the work |
| `methods` | `computed` | `computed` | each block's uses edge onto a code file |
| `findings` | none | `presentation`, `well-posed`, `accurate`, `true`, `significance`, `computed` (a result reported as computed with no script it uses) | none: the paper read whole, in order |
| `contribution` | none | `novelty` | none: the contributions it lists, each checked against works found on the web and the cited works |
| `fieldresults` | none | `literature` | none: the results of its field the paper uses bare, each checked against the cited works and works found on the web |
| `relatedwork` | none | `literature`; files suggestions | none: the places of the paper a reader of its field wants situated, each part asked by a short list of kinds, mapped by topic, the work a reader expects there found on the web and in the cited works |

With no `--session`, `review` and `sync` ask the seats a profile asks by
default (`asked_by_default`, true unless set; `Reviewer`); the shipped
`standard` asks the field's three on request. `--session field` asks
the paper against its field, `contribution`, `fieldresults` then
`relatedwork` (`review_file.GROUPS`), each a seated one, the others
named and skipped; `--session all`, every seated session. A session
named alone that the profile does not seat is refused
(`review_file.named_alone`).

**The argument session answers by block** (`review_blocks`,
`Column.by_block`). Its `paper.md` is the state's edges sheet made lean:
without the edges file's title and its note, the `Files:` and `Code:`
lines, each block's place in the author's files and its done mark, and
the quotes of its uses, about and qualifies lines (the words stand in the
block above). What more a subject asks stands under its line (the sheet's
indented `↳` lines: a rule asked, a candidate, a proposal, a settled
finding, what relies on its claim); each finding to settle stands under
its first block; and where the session asks fewer than every `⊢` and
justifies line, each it asks ends `· asked`. Its hints stand under the
blocks they bear on, at the margin (`review.hint_lines`): each open
finding in its aspects but those it settles (`↳ filed`), each ruling
that speaks but those its subjects test (`↳ ruled`), each flag (`⚑`),
and, of the subjects it asks, each lapsed one's earlier reading and why
it lapsed (`↳ read before`) with each edit those lapses began at under
its block, its Δ lines and its text then (`↳ edited`), and each a
person's dismissal or proposed reading asks again (`↳ dismissed`, `↳
proposed`); what lies on no block shown stands at the head. It has no
`hints.md`. The question's key reads `paper.md` without its hints, so a
question replays while only they change; the brief's parts on hints
follow them, so the brief may change while the key does not. Its
`review.md` starts empty, and it writes each block's
lines under a heading `@@ <handle>`: `true [<claim>] …` on a claim of
the block's, `valid <T> [<claim>] …` on the block or group as J, `rule
[<claim>] …` and `keep [<claim>]` on a claim of the block's, a finding
anchored on the heading's block where it names no anchor, and edge,
group and settling lines as the sheet writes them. The check writes these
as the sheet's lines, each subject by its id, after the sheet the spec
carries (`review_blocks.translated`), and tells each problem by the
session's line and block; the store keeps that sheet (`Validation.kept`),
and `--file-answer` reads it as it is. The session is given no
`conventions.md`, and Read, Grep, Edit and Write
(`Workspace.tools`); the argument and findings sessions Bash too, in its
sandbox, to redo a computation themselves (`Column.computes`), the
contribution, fieldresults and relatedwork sessions none. A tool server serves it the rest
(`Workspace.served`, `review_tools`, on `queue.server`): `check`, the
gate's check on its `review.md`, and, where it reads cited works, their
reads (`cited_search`, `cited_page`, `cited_grep`, `cited_locate`,
`cited_toc`, `cited_image`; its brief says how). The server's settings,
the gate's spec, the session's directory and its reads, lie under the
store's `sessions/` while it runs (`runner.hosts.written_tools`), where
the session cannot write, so the spec it checks against is the one its
host wrote; `review.md` is read as a regular file, never through a link;
the check and the reads run, fetch and write nothing (a session that
fetches has its own tool for that, below), and the host refuses a store
that lies where the session writes (its directory, the temporary one),
and closes the store to the session's reads. OpenCode serves no tool server:
it refuses such a session, as it refuses a queue session. Its gate runs
at the end only. Each subject,
flag or finding to settle that `paper.md` has no block or line for stands
at its head. Its brief is its template cut to what its paper needs
(`review_blocks.instructions`: the rulings, the findings to settle, the
paragraphs of a later review, `analogy` and `sketch`, `generalizes`, the
cited works; and, following `paper.md` with its hints rather than the
key, how hints read where any is laid in and how to weigh a person's
dismissal or proposal where one is: a part on what is not there reads
as a rule of its own).

**The findings session answers by block too, on the paper plain**: one
input and one output. Its `paper.md` is each block's handle and text,
without edges, standings or part markers (`review_blocks.plain`); under
each block, on lines of their own so a quote of the paper's words is
still found there (`↳ …`, `review_blocks.laid_under`), what its question
uses (`review.laid_lines`): each work the block cites, named as its
bibliography names it (`↳ cites KEY: <authors> (<year>). <title>`), and
each file it uses (`↳ uses file:…`, the provenance question's edge); each
open finding in the session's aspects, whoever filed it (`↳ filed …`);
each finding to settle under its first block, and the paper's
conventions at its head where it has them; its open findings are hints,
which the key leaves out, as the argument session's. It has no
`hints.md`, no bibliography and no `conventions.md`. It writes its
findings under `@@ <handle>` and ends with `⟦done⟧`; the check, its own
check and its gate are the argument session's.

Each but the citationcheck and methods sessions, asked as queues (below),
runs over the whole paper on `review.md` (the argument session by
block, and the findings, contribution, fieldresults and relatedwork
sessions by block on the paper plain, above and below;
its subjects, one judge line each, findings and edge lines; in the argument session, a rule line
on each `true` subject marked `⊢ unsupported`, a claim no person's
ruling speaks for, [the model](model.md) § 8 (`review_file.RULE_FORM`
and `OWED_FORM`, the pages it rests on where it read some; the sheet showing under
the subject what relies on the claim, `↳ relied on by …`), or, where its block's own
words give the step, a self line naming it, which stands it `self`
(`review_file.stood_self`)), on the reading tools alone, its gate
checking at its end; the findings, contribution, fieldresults and
relatedwork sessions, which judge no subject, end their sheets with
`⟦done⟧`. `review` asks the sessions in
the order named, else the profile's, each brief built after the last is
filed, so a later session reads an earlier one's findings, and the
rulings that speak, as hints (the findings session, the open findings in
its aspects under their blocks, and no ruling or judgment; the
relatedwork session none). Its answer's records are judgments, findings, rulings, suggestions
and the pages they read, filed as the agent's; the edge lines it writes are given
afterwards as the agent's edges edit, never by its answer. The argument
session may also make a group (`group <kind> <first> <last>`, a run of
blocks the paper argues as one that no group holds): the runner's check
refuses it as the groups edit would be refused, a person's pin included,
and it is given after the edge lines as the agent's groups edit of the
latest state, the edges it reopens asked on the profile's roles and
edges agents within what is left of `--budget`, its cost counted
against it. A group the state holds already gives nothing; a scoped
session's group the groups check refuses beside an earlier one's is left
out, and listed. With no budget, or under `--file-answer`, the lines are
not given (a pending edit would hold the version) and the command that
gives them is printed; a re-ask left unanswered leaves the edit pending,
and that command, `--state` the state the sessions read, resumes it. Its records'
basis is the one hop on the state as its own edge lines correct it,
made without keeping it (`edits.corrected_export`), with what those
lines took away ([the model](model.md) § 8), each line replacing the one
it corrects there (the model's rule, `review_file.corrected`). The gate
and the runner's check are one check (`review_file.checked`): the sheet
read, its runs made, a fetched work's pages read as the gate laid them
in, and its edge lines applied to the state's edges sheet and checked as
an agent's edges edit of the state, by the edges stage's own check on the
spec the brief carries (checked again only when the lines change). What
that needs of the store the brief puts in the gate's spec: each laid
page's read status, each finding's settling as the state reads it. The
runner's check runs it, then builds the records, reading each page again
and stamping it; the gate refuses whatever the records would, so a line
that would be refused goes back to the session while it can still fix
it. A refused answer ends a session only where the gate gave up after its
rounds, a page that read at the brief fails to read at filing, or the
store changed since the brief (a finding answered or settled meanwhile).
The methods session is asked as a queue (`methods_queue.py`, § 2): a
task each finding it settles, then each pair of a block and a script it
uses, in the paper's order, served with its script and its block; the
paper laid in its folder plain beside the scripts (`code/<KEY>`), the
core's graph reads withheld. Its `run` tool makes the pair's script's
run, with the arguments given, from the paper's directory, outside the
sandbox, cached by the script's blob, runner and arguments
(`computations.run`), and returns how it ended and what it printed. A
pair is answered with a typed judge: the verdict, whether it rests on the
run and its arguments, each value the block states beside the output's
words that print it, the script's lines that compute them, the evidence
and its findings; code writes the judge line, and the gate checks it as
a sheet's (the text side in the block, the output side in the run's
stdout, the code line in the script), making the run where `run` did not.
A session's share of the pairs bounds what it carries, and `--scope` and
`--by-section` are refused for it.
An answer the runner's check refused stays in the store; `review PAPER
--file-answer KEY` checks it again under the installed version's check at the paper's
current kept state, asking no model. It is refused, naming why, where the
answer read another state (the state its record names, `state`), where its sheet shows a subject or a finding the state
does not offer or shows otherwise, or where a subject holds a reading
filed after it. Otherwise its brief is rebuilt for the subjects and
findings its sheet shows, and, where it now passes, it is filed as a new attempt
of its key at no cost, naming the attempt it checked again
(`checked_again`), its edge lines given as any answer's.
A review session reads its cited works through a tool server
(`sources/cited.py`'s `QueueReads`): a session by block (argument,
findings, contribution, fieldresults, relatedwork) through its own
(above), a queue session through its queue's (a workflow's reads, § 2);
no review session is asked on a sheet, and no page is laid in. The reads
are tools, capped (`cited_page`, at most three pages; `cited_grep`, at
most 50 lines, over every file of the work the session reads;
`cited_locate`; `cited_toc`; `cited_image`, the page as a picture), and
`cited_search`, the library's hybrid search in one work or over the
session's works (without the library it answers that there is none,
naming `cited_grep` and `cited_locate`), each hit its key, page index and words, a pointer to a
page and never the page read (the query is embedded by the corpus's
embedding provider, the one place it is sent); each page and hit fenced
and stamped as every read is, a page, a pattern, a label or the contents
naming first which of the work's files it is. They take the paper, its
store and the works from the session's reads (`questions.Reads`: the
works laid in, those its subjects and findings cite, `review._works_for`;
for the argument, contribution, fieldresults and relatedwork sessions
also every other entry of the paper's bibliography, read on demand,
`review.READS_BIBLIOGRAPHY`, `review._reads`), refuse a key outside its
works with the bibliography's keys that start as it does, and read the
corpus and the copies already fetched, never fetching. A page a line
names of a work not laid in is read then: by the gate through the
paper's sources, as a fetched work's page is
(`review_file.unlaid_page_problems`), and by the filing, which stamps
it; an argument reading or ruling is given each file of that work it
read, filed as seen where the store had not seen it. The
reads keep each page they served whole (a page, a contents page, an
image): the pages a workflow's gate lets a reading name. A judgment, and
a ruling, names the pages it read (`pages KEY:N`), and the runner's check
reads each again and stamps it, as before. The question's key takes each work's page stamps, not the
list's text, so a session replays while the corpus's pages hold.
The citationcheck session reads every cited work, and is given the paper's
bibliography; under each site, the pages its locator
names where code finds them at no cost (a page locator's, a label's first
places as printed), never a search. It is asked as a queue
(`sources/citation_queue.py`, § 2): a task each finding it settles, then
each site, a work's sites in a row, the works in the order the paper
first cites them, so what a session reads of a work's title page and
edition serves its sites together; the paper laid in its folder plain
(its blocks' handles and text) beside the bibliography, the core's graph
reads withheld. Where the profile seats a finder
(`review.citationcheck.finder`), its step answers each site first: up to
three pages of the site's work, each with words of it the quotation
matcher finds there (`stands_on`), or what it searched; it never judges,
and a finding to settle passes it. The reader is served the pages found,
whole, and answers a site with a typed judge: its verdict, the words of
the work that decide it (at most three quotes of at most 30 words, each
found on its page) and one sentence of how they bear; code writes the
judge line, its evidence `KEY:N "<words>"; …: <bears>`, and its find
lines. A finding to settle it answers `stands`, `fixed` or `reading`.
Each answer is checked by the session's gate on a sheet of that one
task, and a judge names only pages the session read whole (a page, a
contents page, an image, a page served with its task), and a page with
no usable text, evidence as its image (`image`, docs/model.md § 8),
only where it read the image (`QueueReads.unread`). A quote the
stored text does not hold is handed back once, since the text may be
what is wrong (a bad OCR reading, mathematics extracted badly), and
taken after as confirmed on the page, marked so in the evidence. The
run's options carry its review round, and its answers record it, so the
round asks again what its run filed; a session's share of the sites
bounds what it carries, and `--scope` and `--by-section` are refused for
it. Its gate hands back a verdict other than `inconclusive` that names
no page of the site's own work.
The contribution, fieldresults and relatedwork sessions are the ones with
the network: web search, the one web tool a session's host lets through,
is given where a subagent of its workspace lists it, to the session and
that subagent, and to a queue step that searches the web (the
fieldresults queue's two); web fetch is never given. A page comes only through the
paper's sources. **The contribution session answers by block**, as the
findings session does, on the paper plain: under each block, the works
it cites (`↳ cites`) and each claim ruled `positioning` that no live
check reads yet (`↳ ruled positioning "…"`, keyed); its open `novelty`
findings as hints. It writes `checked "<words>" — <searched> — <found>`
and `find novelty work:KEY …` under the block's heading, and `⟦done⟧`.
Its tool server serves it `check`, the `cited_*` reads of every work of
the paper's bibliography, one it cites nowhere by its key (for prior
art: a close predecessor the paper cites or lists; what the paper says a
cited work states is the citationcheck session's, which its brief
says), and `fetch`: an open copy of a work found outside the
bibliography, by its identifier (`arxiv:ID`, `doi:DOI`), fetched through
the paper's sources (`Sources.fetch`, `axiomation.sources`' fetcher,
into the paper's cache as a transient copy, refused where its first pages
do not print the work's title and authors, `sources/match.py`, but kept
unchecked in the plain tier where the check cannot decide on a scan's
layer, `docs/model.md` § 8) within the
gate's time, each work tried once, and read then by the same `cited_*`
tools under that key. Its one helper, the forager (a subagent of its
workspace, `Workspace.helpers`, Claude Code's `--agents`, on the
profile's `forager_model`), is given one contribution and searches the
web and the bibliography, fetches what the bibliography does not hold
and is worth reading, and returns
candidate works with pages and a quote each, on the server's reads and
fetch; the reader reads each page it names before judging. A `novelty`
finding names its work (`work:KEY`, one of the bibliography by its key,
cited or not, a found one by its identifier, which the check takes as
fetched by the tool) and the pages it read (`pages KEY:N` after the
severity): the check reads a found work's pages in the paper's cache,
where the tool put them (`review_file.cached_page_problems`), one of
the bibliography not laid in through the paper's sources
(`review_file.unlaid_page_problems`), and the records read each again
and stamp it on the finding. `show`, `open` and the viewer name a work
the paper cites nowhere and no session found on the web so
(`queries.CITED_NOWHERE`), apart from a cited one and a found one.
**The fieldresults session is an inventory, then a queue**
(`sources/fieldresults_queue.py`). The inventory answers by block, as the
contribution session does, on the paper plain: under each block the works
it cites (`↳ cites`), its open `literature` findings as hints; no helper.
One reading of the whole paper lists each result the paper takes from its
field, a `checked "<words>" — <the result> — to source` line each, or in
place of `to source` why none is owed: a citation the sentence attributes
the result to, a step written out at sight, the paper's own claim. A work
the paper cites for something else does not cover a result. The claims
standing `unsupported` are not laid in its paper, where they would stand
for the reading it owes: its spec carries them (`review.pointers`, each
with the `known` rulings that speak for it, `review_file.POINTERS`), and
once the sheet is done its check hands back each one no line on its block
quotes. Once every inventory of the round is answered, its queue draws a
task each line `to source`. Its finder (the profile's
`review.fieldresults.finder`) is served the result, its block and what
the block cites, searches the bibliography by the queue's reads (the
works the paper cites nowhere too), the web,
and fetches an open copy (`fetch`, as a session by block's), and answers
`found` (up to four pages, each with a passage copied from it, found
there by the check; the works it found named and could not read) or
`not_found`, never a verdict. Its reader (the session's own agent, which
also answers the inventory) is served the finder's pages whole, reads
further and searches the web where they do not settle it, and answers
`settle`: what was searched and found, and its `literature` findings;
code writes them as the inventory's lines under the block's heading, and
the session's own check reads them on an answer of that one block
(`review.checker`). A session of either step answers five results, then
a fresh one takes the queue on: each result stands alone, and a session
reads its whole context again every turn. A page a finding names is one the reader read whole or
was served, and one with no usable text one it read as an image. A
`literature` finding names the work the paper should cite for that
result, one of the bibliography by its key, cited or not, or a found one
by its identifier, and may name its pages.
**The relatedwork session answers by block**, as the contribution
session does, on the paper plain with nothing laid under its blocks: it
reads the citations in the text by their keys, and no hints, since what
the paper lacks no earlier finding says. It is served contribution's
tools (`check`, the `cited_*` reads of the bibliography, `fetch`) and
web search, and one forager on the profile's `forager_model`, given one
place (or several close together) and returning the works that treat its
topic, each with its identifier, a page and a quote, or that it found
nothing relevant. Its brief asks each part of the paper by a short list
of kinds (the problem and its origin, the model and its variants, the
methods and their alternatives, the assumptions and limits and the other
explanations, the results and their kin). It maps the places, a `topic`
line each (the works a reader expects there), and offers what the paper
does not cite there as suggestions (`suggest`, [the model](model.md)
§ 8), not findings, a `literature` finding only where the paper places
itself nowhere. Its check hands back a topic whose expected work its
block does not cite and no `suggest` or `find` line names, so nothing on
the map drops out unsaid, and refuses a `suggest` of a work the block
cites already; a work offered again where a live suggestion offers it,
open or answered, is dropped when filed (`review.records`). A found work
is named by its identifier, or, where it has none (an old article, a
book), by a link to a public page of it, `work:https://…`, a pointer
never read (`review_file.LINKED_KEY`); where `fetch` finds no open copy
of a DOI, it says what the DOI resolves to, its authors, year, title and
venue, or that no resolver resolved it, with why
(`sources/resolvers/fetch.py`), so an identifier written from memory
shows before it is filed. Open suggestions are listed in the hints of a
queue session, in `show` and in `review --report`.
What a cited work says is never verified from recall: every subject's
cited works are given to read where the library holds them, and
the gate hands back a `verified` `true` whose claim's own sentence cites
a work and names no page of it (name the pages, or judge it
`inconclusive`). A session that judges asks, by default, the subjects
in its scope that a new reading can settle: one no reading holds
(unjudged, lapsed, or standing on a lapsed negative no later reading
settled), one held `verified` on pages that are not the version of
record, and one whose reading did not verify and whose evidence grew
([the model](model.md) § 8: the review compares the corpus with the
store first); and one whose one hop holds an open stale mark's line as
its own with no reading since the mark that does, since that reading
closes the mark ([the model](model.md) § 5). It holds back a held reading, which holds until it
lapses ([the model](model.md) § 8), a disputed subject, which a person
settles, and a reading that did not verify on the same evidence, which
would be the same question on the same inputs. `--all-again` asks those too; the dry run
counts what each session holds back, by why. A `true` that holds on a
claim the paper argues nothing for is asked while no ruling speaks for it:
its rule line settles that. A subject a person's dismissal asks again
([the model](model.md) § 8) is asked until a reading of it is filed after the dismissal; the hints carry the
finding, the answer and its note. So is a subject a person's proposed
reading asks again, until a reading of it is filed after the proposal;
the hints carry the proposal, its verdict and evidence. So is a claim a
person's proposed ruling asks again, until a ruling of it is filed after
the proposal; the sheet shows the proposal under the subject (`↳
proposed by …`). So is a claim an agent's `owed` ruling speaks for that a
settled finding on its words was answered after. The sheet shows each
settled finding on a subject's words (`↳ finding … answered …`) and each
it asks to settle there (`↳ ⟦f3 finding⟧ below sits on these words`);
the gate refuses an `owed` rule line beside one settled (or settled
`fixed` on the sheet) whose reason does not name it. So is a subject
a review's reading or ruling filed under an older question speaks for
([the model](model.md) § 8), until a review reads it under the current
one; its earlier reading stands under its block as a hint (`↳ read
before`, its status line naming the older question), and the sheet
shows an earlier ruling under its subject as a
candidate to test (`↳ ruled <kind> under an older question, a candidate
to test`; an `owed` one as a reading to weigh, never one to keep), out of
the hints. Where it holds verbatim, `keep <subject>`
takes the rule line's place and reinstates it under the current
question, naming it; the gate refuses `keep` on a subject with no
candidate, on an `owed` candidate (`keep` gives back its kind and reason
alone, and the current question asks an owed ruling's own severity and
fix), on a
subject a person's proposed ruling asks again, or where the candidate's
reason quotes words the paper no longer holds. A person's own reading
or ruling decides, and is never asked again. A session's brief records with each answer
the version of each question it asks past the first (a ruling, or a
reading of an aspect), set by hand in `review_file.QUESTION_VERSIONS`
where a change to the brief alters what that answer answers, never for
its wording; an answer that records no version of a question answered
its first, and `--file-answer` files a kept answer under the versions it
was asked under. A lapsed subject the
session asks has its earlier reading and why it lapsed under its block
(`↳ read before`), and each edit the lapse began at under the edited
block (`↳ edited`). A **review round** is one `review` command's
questions for one session at one state; its id, a hash of all that decides
which questions it builds (the state, the session, the selection, the
scopes or `--by-section`, the template and the versions of its
questions, the helpers' models, the persons' dismissals and proposed
readings that ask a subject again, each live proposed ruling, and each
answer no review filed that settles a finding on an unsupported claim's
words, which its sheet shows; none of them makes a state, and a round's
own answers never move its id), is kept on
each of its answers. The same command at the same state is the same round:
asked again, it holds on its sheets the subjects its answers read and the
findings they settled as they stood before, so it is the same question, its
answered sessions replay, and its edge lines are given once every session
has answered; any other round holds back what holds. A session may be
scoped (`--scope`, `--by-section`): its sheet holds the subjects whose
claim's block lies in its sections, lines or handles (a proof's `valid`
with its theorem, and named by the proof's handle too), the paper whole
at hand, and it files findings only on its scope's blocks; under
`--by-section`, what lies before the first heading goes with the first
section's session, named "the opening and §n …"; a subject or a
finding two scopes select is the first one's; a scope with nothing to ask
is not asked, and a name that selects nothing is
refused with why (it holds no subject the session judges, or every
subject it selects is held back). A session that judges no subject is
scoped by the text: the blocks its names select, or each top-level
section, every file it is given kept. A session that judges no subject
reads, by default, the blocks of its scope that no review of its kind
read as they now stand: of each valid answer of the session's own kind
outside the round asked, the blocks it read whose text the state it
read holds as the current one does. An answer reads its state's whole
text, or, where it read a part, the blocks its record names
(`AnswerRecord.read`); one that settled its findings alone read none. With every block of its scope unread its question is
the scope's as it always was; with some, its scope is those blocks, its
note naming them ("the blocks … no earlier findings review read as they
now stand"); with none, it settles its findings alone, its template's
note saying so (its template named `+lapsed`, so a kept answer's key
holds); with nothing to settle it is not asked. The fieldresults
session's inventory is owed on the unsupported claims of the blocks it
reads, and none where it settles alone. A block holding what the session
owes a reading of is unread whatever its text: the contribution
session's claims ruled `positioning` that no live check reads, and the
fieldresults session's claims standing unsupported that no answer read
standing so. A part read owes no `checked` line (`rf.PART`). An
earlier answer counts whatever version of the template it was asked
under and whatever the corpus held then: a changed brief or a work that
now reads asks no block again (`--all-again` reads the whole text of its
scope). Every scoped brief is built from
the same state before any is asked, so no session reads another's
filings.
Every session's sheet holds, first, each open finding a session of its
kind filed that lapsed or carries a proposal, an agent's or a person's ([the
model](model.md) § 8), on the blocks its scope reads, as a fixed line
`⟦f1 finding⟧` with its causes and its proposal under it, the header
saying a proposal's note is reasoning to weigh, never evidence. Each takes one
line: `stands f1 ["<quoted words>"] — <why>`, the finding refiled at the
state as `reaffirm-finding` refiles it (a live one carrying a proposal
too, which only a review's `stands` line turns down), or `fixed f1 — <why>`, the answer `fixed`, filed as the
review's, which speaks; or, on one carrying a proposal, `reading f1
["<quoted words>"] — <the two readings>`, refiled as `stands` refiles it
and marked a disagreement of reading, the person's to answer, which no
sheet asks again ([the model](model.md) § 8). The gate refuses a
`reading` line on a finding that carries no proposal. A finding a person filed, one filed by `find`,
or one a person answered, is a person's to settle; the hints list it,
its proposal beside it. A session with no subject to ask, no text to
read and no finding to settle is not asked. A scope takes a finding
where its first anchor's block lies.
No agent dismisses, ranks or merges another's findings: grouping and
ordering them is code's (`review --report`).

## 8. A paper's settings and code

A paper's `axiomation.toml`, and its workspace's, are read through
`settings.py`, the paper's keys over the workspace's per section; each
section's keys are checked by the code that owns it; `axiomation
settings` prints each value in force and where it came from (`paper`,
`workspace`, `default`, `beside` for the one `.bib` beside the paper).
These files are shared with whoever has the paper; a person's facts are
not: where the services run, the keys, the OCR backend and the lookups'
contact address are read from the user file, `config.toml` in the
person's axiomation directory beside their keys
(`$XDG_CONFIG_HOME/axiomation`, else `~/.config/axiomation`), the shell's
`AXIOMATION_<KEY>` over it (`user_settings.py`); `settings` prints them
beside the paper's (`shell`, `user`, `default`), a key as set or unset.
A paper's or a workspace's file naming one of them, or any key ending
`api_key`, `token`, `password` or `secret`, is refused, as is a user
file holding a key that others may read; no `.env` is read.
`axiomation init` writes either file from its template
(`settings_templates/`): every key commented out with what it does, so
the file changes nothing until a line is uncommented, and no budget set;
a test holds each template to the keys the code reads.
`[sources]` configures the cited works' corpus: its
collection, model, dimensions and bibliography. `[budget]`
holds `run_dollars_max`, the budget a run that asks a model takes where
no `--budget` is given (§ 3): the paper's over the workspace's, over the
person's own `run_dollars_max` in the user file (the shell's
`AXIOMATION_RUN_DOLLARS_MAX` over it), the one key of the user file a
paper's file may set too; with none, such a run needs `--budget`. `[gates]`
sets each gate's mode, `gate = "block" | "warn" | "off"` (the model's
§ 11); an unknown gate or mode is refused. `[computations]` names where
the paper's scripts live, never outside its
workspace, a script's runner by its extension and the timeout. A `.py`
script with no runner declared runs in the environment the files nearest
the paper name, looked for from the paper's directory up to the
workspace's root (the paper's, outside one): `uv.lock` (`uv run
python`), `poetry.lock`, `pixi.toml`, `Pipfile` (each its tool's `run
python`), `environment.yml` or `.yaml` (`conda run -n <its name>
python`), `.venv/` (its Python, by its path from the paper), in that
order in one directory; else `python3` (`computations.py`). The search
starts where a run starts: these tools find their project from there, so
a file below the paper's directory would name an environment the run
does not use. A run's environment is the shell's with the person's keys
left out. A code file is kept with
every state by its content (a file ending `.py`, `.R`, `.jl`, `.m` or
`.sh`); its key is its path from the paper, or `workspace:` and its path
from the workspace root. The paper's bibliography (`[sources].bib`, else
the one `.bib` beside the paper) is kept with every state the same way,
so a workspace's shared one is too; a bibliography outside the workspace
is read but not kept. Beside it, `<bib>.confirmed.json` (`references.bib`'s
is `references.confirmed.json`) keeps a person's word that a work's
file, by its SHA-256, is its entry's work where the file check holds it
back, and, where its entry holds several files, which is the edition it
cites (`--cited`) and what each other is (`sources confirm --file`,
`sources/confirm.py`; which file is which, `files.py`; the model's § 8,
`mismatch`): the bibliography's, shared by every paper citing from it,
read where a work is read and never kept with a state. A path the text writes that the paper does not
ship asks nothing. A script runs only
when a filing, a person (`axiomation run`) or the methods session's
`run` tool asks (§ 7, within the time a session's call is given): an argument list, never
a shell, from the paper's directory, in its own process group under the
timeout, cached by content, runner and arguments.

## 9. The model's machinery

How the model's rules are carried out, kept here for a contributor; the
model says what each means. Each part names the model's section it
serves.

### Edges and standings (the model's §§ 3 and 4)

**A standing's handle on a sheet.** Every sheet prepared for a later
pass shows the handles of a block of two or more claims ([the model](model.md)
§ 4).

**A member's line beside its group's.** Every edges
check refuses a member's line onto a target its group's line names
(their claims meeting, or either naming none) where the answer may
change it, save a person's line, pinned or kept by the keep mark; a kept
state that holds such a line stays open to edits, its line listed.

**A standing on a block with no claim.** A standing written on a block whose roles hold no claim corrects the
roles: a claim part is added, from the start of the sentence holding the
standing's quoted words to the end of the sentence holding their end. The
correction adds a role and never removes one: where that part would leave
one of the block's roles no text, or cannot be placed, the claim stands
beside the block's roles with the block unsegmented, its placed
boundaries gone (a part there takes its legend role, else the first of
the block's roles in [the model](model.md) § 2's order), and the correction says so; a block
with no role becomes a claim whole. A block whose roles a person pinned
takes none.

### Change and stale marks (the model's § 5)

**A change the text's words do not show.** A vocabulary map entry that changed (what the paper's own name for an
environment or a reference is read as, § 2) is a change of the text that reads it, though its words are the
same: each use of an environment so named and the proof after it, the
block of each use of a command, and for a command that sets labels also
the blocks that refer to one, are `changed`, and re-cut. A name the paper
declares more than once has an entry for each declaring text, each use
read under the declaration in force there; an edit of any of them is a
change of every block that reads the name.

A statement's meaning is its environment's, so an edit of its
declaration alone (`\newtheorem{claim}{Theorem}` made `{Conjecture}`)
moves it as renaming the environment does. A move within one role (a
lemma become a theorem) is `markup`; a move to another role, or to or
from a conjecture, whose claim stands `conjectured` and needs no
argument, is `changed`, and the update's task on it says what it now
reads as.

**What carries across an edit.** A cut correction carries onto the new version, whoever made it,
wherever the words at its boundary still stand, whatever the edit
changed before or after them: a split where the later block's first
words now are, a join where the words before the cutter's boundary and
after it still end and start two blocks with none between. Words stand
where the text map carries them unchanged, else where they are found
exactly once in what the edit changed. The block it made is then
`changed`, not deleted and added. A boundary lapses where its words are
gone or found more than once, where the author's own block stands
between a join's sides, or where it falls in a block that reads a
changed map entry; the cutter's cut stands there, and the update lists
it with its words and why. One on a block the edit deleted goes with
it. A boundary only the cutter made is the cutter's again, so an
author's join or split of paragraphs stands.

**A group carries** while its run stands: each member, and the block it
names, continues in one block of any class, `changed` too, the
members stand in order with no other block between them, and a proof's
title, where it names the result, still names it. An edit to a step's
words keeps the proof's group, and its justifies line is the group's
still, asked again where the step's Δ carries (marked stale in an update
of one session). A group lapses where a member was split, joined or
deleted, the block it names was, its title now names another, or a block
now stands inside its run or its members changed order; the update lists
it with why, and the groups step, where an update asks it, asks the run
again.

A quoted excerpt of an old block is re-found in the new text only where it
occurs exactly once; else it is demoted, with a fuzzy hint beside it that
never places it.

**An update's reading of what the rule moves.** An update of one session
asks a Δ of its own, for the block whole, of each block its Δ lines move, the
nearest ring first: the walk goes past a block only once its Δ carries.
Past a bounded number of such lines (`moved_delta_lines_max`, a profile
setting) a ring is not asked and carries wholly. An update per stage
opens each block the rule moves in its session, which writes its Δ. Beside the walk, an edited
block reopens by mechanism: the `position` edges onto it when it moved;
on any edit but markup, the `scope:paper` and `where-used` sources; for
an insertion or a deletion, the `position` edges onto its neighbours and
the about edges onto its section's blocks. An update of one session,
a queue (§ 2), reads every block it reopens again as a
task, with why: a `scope:paper` source among them, since an edit
anywhere can make a claim about the whole paper untrue. A task shows
the lines its block lost onto a deleted block, and each line whose
quoted words the edit removed with where similar words now stand. It
asks a block that writes a new part's words, or cites a block for a
part that block's edge dropped, with no edge onto that part's block,
whether it relies on it. What its Δs reach beyond its tasks it marks
stale, walking the edges its answer wrote as well as the carried ones.

**Clearing a stale mark.** What the rule moves unread is marked moved; a block given its own Δ is
the update's read of its meaning, and is never marked moved by the edit.
What an update carried unread is one item per edit (`carried-unread`, [the model](model.md) § 10),
its blocks counted until a later update gives each a Δ of its own, a
`verified` reading made since holds it as its subject's own (the
subject's node, a group's members, a `valid` reading's target, as a
reading closes a mark), or its node ends: a later update that does not
read them leaves the item.
A retracted qualifies edge moves its target's meaning, as a deleted
block's does. One line is one mark, at the latest state that marked it: a
line two updates asked is one question, and only an answer after the
later one reads it. A mark clears when an answer reads its line on the
new state: a later re-ask that reopens its owner, a person's or an
agent's edit of the sheet, which shows the marks, that keeps or changes
one of the owner's lines, the next update reopening it, a **reread**
(`axiomation reread`), a person's or an agent's filing that the owner's
lines stand against what changed, with its reason, or a `verified`
reading, made at or after the mark, whose basis holds the line as its
subject's own (the owner is the subject's node, or for `valid` J, one of
J's members, or T; a qualifier of a hop node is a third block's line).
`review` asks each subject whose one hop holds an open mark's
line as its own and that no reading since the mark holds so
(§ 7); a mark no subject holds as its
own (a qualifier's line, an `about` line a standing rests on, a line out
of a block no session judges) is answered by a reread or a sheet edit
alone. `reread --ask` asks the question that draws lines for them, a
queue of the owners (§ 2): an agent
is served each owner's lines and its marks and, per marked line, its far
end's words before the update and now, and answers that its lines stand
(filed as its reread) or with the lines it takes off and puts on (given,
once every owner is answered, as one edges edit, a new state). An update ends with that
question on the open marks of the state it keeps, every version's,
where the profile's `reread_marks_after_update` is on: a mark is the
question the update left open, answered where the change is made. A
mark a reading cleared stays cleared when the reading later lapses: the
line was read, and what lapses the reading shows as its own. A reread
clears every mark on its owner made at or before its state, pins nothing
and makes no state; a later update's mark stands. Lines given without the
sheet (`axiomation change`) are shown no mark and clear none. An `about`
mark on a line no standing rests on, and not flagged `generalizes`, is a
**description**: no session judges `accurate`, so it is in no gate
([the model](model.md) § 10) and off the established walk ([the model](model.md) § 9); it is listed with the marks,
after the gate's, and on its block, and the reread asks it after
them. Marks are computed from the kept states, the rereads and the
readings, never stored: within a version the latest record speaks;
across versions a mark goes on while its node does.

**An edge onto a deleted block** is gone from the sheet an update
carries: its source is asked again with the line it lost before it, to
draw it onto the block
that now says what it pointed at, or leave it gone. Where the update's
session drew fewer edges of that kind from the source, onto blocks it did
not point at before, than it lost, the source is marked stale with each
lost line as a cause, until a person's or an agent's answer shown it
re-reads it: the edge drawn again or a line kept with its reason on the
sheet, or a reread that leaves the gap. A model's re-ask within the
update does not clear it: it was not shown it. Read the same way on the kept graph, each such edge is
listed to look at ("Lines across states", below).

### Who does what, the flags and the pins (the model's § 6)

**Who does what:**

| step | code | agent |
|---|---|---|
| the document layer | reads it | — |
| blocks and the parts the text marks | proposes | corrects the cut |
| roles and segments | from the environment, where it is one thing | gives every block its roles |
| groups | proposes from proof environments, proof titles, step headings | decides the runs |
| edges, standings, parts, the frame | the marked references as candidates; the reach rule | writes every block's edges and every claim's standing; may propose cut corrections |
| a change | pairs the versions; walks the rule; marks stale; computes support | writes each changed block's Δ and lines; re-reads what is reopened |

The cut is corrected by later steps too. The roles and groups steps only
**flag** what they see about the cut, with the block's words; a deciding
step may review the whole cut with those flags and correct it; the edges
may propose cut lines, applied in rounds that ask again only what they
touched. A proposal that reverses an earlier correction is dropped.
Flags are records of positions, which change nothing; the rules' flags
are computed on a build's state and its edits (an update raises none),
the vocabulary map's two on the final cut and shown to no step, since
they measure the map's rounds:

<!-- closed set: axiomation.records.Signal -->
| signal | raised by |
|---|---|
| `split` | roles: a block holds two verdicts |
| `merge` | roles: two blocks make one verdict |
| `role-doubt` | roles: a role in doubt |
| `two-block` | groups: a group of two blocks |
| `run-unclear` | groups: where a run ends is unclear |
| `role` | edges: a span given a role its content does not play, or a standing's claim part that could not be placed, its claim standing beside the block's roles (above) |
| `part` | edges: a part hard to draw |
| `cut` | edges: the cut seems wrong, and no cut line says how |
| `two-block-group` | the rules: a group of two blocks |
| `qualifies` | the rules: every qualifies edge, to look at |
| `definition-uses-claim` | the rules: a definition using a whole claim block |
| `container-statements` | the rules: an environment the vocabulary map reads as a container or as nothing, whose blocks all took a statement's role |
| `mapped-reference-dangling` | the rules: a command the vocabulary map reads as a reference, none of whose targets is a label |
| `other` | any step: friction with no signal word |

**Pins.** What each holds:

<!-- closed set: axiomation.person.PinKind -->
| pin | what it holds |
|---|---|
| `added` | a block start a person added |
| `removed` | a block start a person removed |
| `changed` | a block's roles, or a group, a person changed |
| `made` | a group a person made |
| `dissolved` | a group a person dissolved |
| `written` | an edges line a person wrote |
| `deleted` | an edges line a person deleted |
| `kept` | an edges line a person kept |

A pin holds while the sheet a step shows, with the held pins in place,
passes the step's structural checks; a pin a failing check names lapses
and is reported, never enforced. Across a version, a pin whose block
changed in words, split, merged or was deleted, or that a person's later
cut edit on the new version changed, lapses, and every edges
pin lapses where the edges are written afresh. A meaning move does not
lapse a pin: it is reported as held with its meaning moved, for the person
to confirm.

The vocabulary map's entries take no pin: each answer keeps its author
instead, a person only on the entries the person's edit changed. An agent
may not change a person's entry, and an update that drops one (its name's
declaration changed, the name is gone, or code now reads the name, which
then no map answers) reports it as
`vocabulary_lapsed`; the entries and their authors show in the export's
`document.vocabulary` (§ 5).

### Kept states' bookkeeping (the model's § 7)

**A version and the installed parser.** The paper, compared with a kept state (the latest
for `states`, `blocks` and the viewer; the one an update starts from), is
that state's version when the installed parser reads both alike: each file it
reads in the paper is a kept file with the same bytes, and the kept
files read by the installed parser are the same files. So a parser that now
reads one more or one fewer file that only declares (a preamble, a
package) moves neither the state's id nor whether the paper is edited,
and an edit made on the state keeps its version. A file it now reads
that the state never kept (a package below the paper's top level) reads
as an edit, which the next update keeps; one more or one fewer holding
text refuses the state ("Under later rules", below).

**Handles and aliases.** A handle's slug takes the first free suffix
(`-2`, `-3`, …) where another node bore that name. A block a split or a
correction begins never takes the handle of the one it came from, nor
any name another node bore. The names a node bore besides its handle,
and the slug its words give now, are its **aliases**: each still names
it where no other node bears it, an alias two nodes bore names neither,
and one another node holds as its handle is that node's. The handle of a
node that ended names no block: a read says when and how it ended and
which nodes took its text. `history` gives each step's name and the
node's edges the step began, ended or changed (an edge named as below:
its parts or what it says of the link changed, its ends and kind kept;
its evidence or note reworded is no change; one a split or a merge took
from it to a node it joined, or brought from one, carried), `show
--history` the aliases.

**Lines across states.** A part is named by its words, normalised. The
same words in its node, or in a node begun from it or taking it in,
continue it; else it continues within its node by most shared
characters, then into such a node. An edge is named by its source node, kind,
target node and its parts' and named standings' words; a standing by its block's node and its
claim's words. Each node names its **origins**, the earlier nodes its text
came from, composed along the parents; a line of a later state is the
same line when its kind and words are the same and its nodes' origins
hold the earlier nodes. So a line a split or a merge carried is neither
ended nor begun; a line on the whole of a block that split, or from the
whole of one with no quote of its source, is listed to look at, as is
an edge an update lost onto a block it deleted ([the model](model.md) § 5). `axiomation history` and `changes` read these.

**Under later rules.** A kept state is read by the installed version, and what
it holds is its export: its cut, roles and lines are read from it, never
derived again, so a reader change reaches only the candidates and the
soundness kinds `check` and `open` read over the kept files ([the model](model.md) § 10), which
judge the state as the installed parser reads it. A state opens whenever its
kept files hold their content and the installed parser reads again, byte for
byte, every file its export names (the files its reader read); a file
read on one side only may declare (a preamble, a package) but hold no
text, which the kept cut would not cover. A value the code derives (a
standing's handle minted from its claim's words, a rule's flags) is not
what a line says: reading a state back sets it aside, and a state an
edit makes takes the installed version's. The installed version's checks judge what is said now. An
answer on a kept state (a person's or an agent's edit, the re-ask an
edit reopens, an update's session) is refused only for a problem the
state's own file does not hold under the installed version's check, compared as the
problem says it, its sheet line aside, once per time the state's file
says it, on the answer's handles: a block the answer's cut does not hold
as the state's (by its id within a version, by its one link across two)
holds none. What the state holds is named to the person (`sheet`, `edit`,
`change`, and `open`'s `later-rule`, [the model](model.md) § 10) and never forced, on the block an edit touches or any other: in
the roles file a block whose markers are as shown, its roles named, is
not judged and keeps its roles. A problem said once for several lines (a
block's claims quoting the same words) is one problem: an answer adding a
line to it is not refused for it. A first pass shows no line before it,
so its checks judge all it writes.

### Judgments (the model's § 8)

**A review's lines.** The edges hold one edge per source, kind and
target, so a review's line onto a target its block has a line of the
same kind onto replaces that line: a justifies line, J's lines onto T
covering a claim it covers (it may name fewer, taking a claim out of J's
cover); a self mark, the mark with the same quote; an about line, which
its brief asks to name each claim the old one named; a uses line, which
names each part the old one named. A review's uses line widens what the
edge relies on and never narrows it: one that drops a part is refused,
and where the old line names no part the edge relies on all of its
target, which a line naming parts would narrow. Narrowing a uses edge is
the edges stage's or a person's edit.

**A judgment's basis.** The basis checked starts as the one hop at the state the reading was
made on, less what its own lines took away, together with the recorded
one, so a later rule that puts more in a hop lapses nothing by itself; it goes on
as each later state holds it, with that state's one hop. An edge added,
by an edit or an update, lapses each reading whose one hop it joins; the
same edge's later changes are further causes. A line's `sketch` or
`analogy` ([the model](model.md) § 3) is no part of its edge: the flag reads J's words, the
reading J's argument, and a flip changes neither, so it lapses nothing by
itself; where a line turned `analogy` adds the uses edge naming what it
carries over, that edge lapses the reading as an edge added.

**A reading asked again.** A reading that did not `verify` is **asked again**
where its evidence grew: of its subject's works and each other work it
read a page of, against the latest live
reading on it, a file reads now whose content it was not given (a work
unread then reads, or gained a file), or the file under a work's key is
one it was given under another (its cited edition changed). A reading
that records no files is asked again where a work it named unread
reads, or a work holds another file that reads and it stamped no page
of. Asked again is no lapse: the reading speaks, as it
was right for what it had, until a new one is filed; a `verified` one
stands while the files it read stand. `sources recheck`, `ingest` and
`confirm` list the readings asked again on the works they name, in every
paper of the workspace that reads the work through the same
bibliography, each named, whichever directory the command was given (the
file and its confirmation are the bibliography's), and the open findings
on those works with what answers them.

**A new version of a question.** Where a change to the argument session's brief alters what a ruling
answers, the brief's ruling question takes a new version: an agent's
ruling a review filed under an older one still speaks, and while the
claim stands `unsupported` the next review asks it again (a
`positioning` one too); a review's ruling under the current
question then replaces it, whoever files it. A person's ruling, and one
filed by hand, answered no brief and is never asked again. A reading a
review filed under an older question of its aspect is asked again the
same way, until a review reads the subject under the current one; one
that concludes replaces it, an `inconclusive` takes no debit away and
leaves it standing. A review that finds an agent's ruling under an
older question holding verbatim (its kind the one the claim's place
asks, each clause of its reason true of the text) may keep it: a ruling
under the current question naming the one it keeps, its kind and reason
carried; never one
on a claim a person's proposal asks again.

**The support search** asks, one claim at a time (`support --ask`):
each claim standing `unsupported`
that no live search read is a task, which a first agent answers argued,
with the line that argues it, or unargued, and a second, where the
profile names one, settles: it confirms the line, read against what it
names whole, or answers the claim itself. The lines settled are given
as the search's one edges edit (they stand the claims argued, [the model](model.md) § 4),
given without the sheet's stale marks, so it clears none; a claim
restating a claim of a target its block already restates is that block's
one about line naming both at each end. A claim the last agent read
unargued is filed as a search. A search speaks while its claim stands
`unsupported`, and lapses as a ruling does; a block that comes to argue
it is the update's, whose session writes a changed block's lines. The
argument session asks a rule line only of a claim a live search read,
and a justifies, self or about line it writes on one corrects the
search, standing it argued. A build and an update end with the search,
after the reread and before the circles' question, unless the profile
turns it off (`search_support_after_edges`).

### A cited work's files as evidence (the model's § 8)

**A file's content hash.** What a file prints is
its content hash: each page's content streams and the fonts, images and
forms the page lists for them, a stream's generic compression undone (Flate, LZW, the
ASCII and run-length filters) and an image codec's bytes (DCT, JPX,
JBIG2, CCITT) taken as stored. It leaves out what a reader adds or a
download varies: the annotations (highlights, notes, ink), the metadata,
the file's ID, the outline and the byte layout; so a file annotated, or
downloaded again, prints the same. A file the PDF reader cannot parse is
known by its SHA-256 alone. The hash depends on the bytes alone, so it
is computed once for each SHA-256 and kept with the file's stamp. A
change to the content that is no change to the work lapses every reading
of it all the same: a publisher's per-download stamp (a date, a cover
page), annotations flattened into the page, a reader that re-encodes the
fonts or images on save.

**The file check.** A work's file is checked against its entry where it enters the corpus
and wherever it is read, where its bibliography is on the machine (with
none, nothing is checked and `sources status` says so): the entry's
title must stand on a title page among its first pages that print text
(a paper's first four, a book's first ten, among its PDF's first thirty:
a scan's blank leaves are not counted), in a row for at least half its
words, and its authors on a page printing it or on the next page with
text after the first that does. Not the year: a reprint or a preprint prints another as often as
a wrong edition does. A file that prints its entry's DOI, or its
entry's pages as its own page numbers (two pages at least in one run,
never its pages' own sequence 1, 2, …, which any preprint prints), passes
whatever its title page says: it is the entry's work, in the edition the
entry names. A file that fails reads as `mismatch` until a person
confirms it or replaces it. Without OCR (the plain tier, § 1), the check reads each page's text layer, and a scan's is none or an
old OCR's: a file whose first pages hold no readable text, or that fails
the check where a scan stands among the pages it reads (counted whether
or not it has a layer), cannot be checked. It is not held back: every
read of it says so, each page with no usable text reads `content`, and
the reader confirms from its title page's image that it is the entry's
work.

**An entry's files.** An entry may hold several files. Each distinct one is in the corpus
(files of the same bytes are one). Its cited edition, the edition the
entry names, keeps the entry's key; each other file has the key `KEY@`
and the first eight characters of its SHA-256 (a translation,
`smith1998@8e3d55d8`), labelled by what a person said it is, else
"another file of the entry", and is never held to the entry's title
page. The cited edition is the file a person named; else the one that
prints the entry's pages as its own page numbers, two pages at least in
one run (a number in brackets is an equation's or a citation's, not a
page's); else the one whose title page the check finds, every other
file's text read and failing it, every listed file on the machine, and
none printing a run of other page numbers (another edition's); else it
is undecided: a file stored under the entry's key before, while the
entry lists it, stays there until a person names the cited edition, and
each other file is held under its own key, labelled "a file of the
entry, the cited edition not decided". A file that changes key moves, its pages and their records
with it, nothing read again: a reading rests on the file, whatever key
holds it, and lapses nothing by the move. A file a person labelled is never the cited
edition by these rules, and a label never clears the check under the
entry's key. An entry with a listed file not on the machine is decided
only by a person or its pages: the absent file may be the cited one. The
order the bibliography lists the files in decides nothing. A key holding
`@` is refused. A `cited`
judgment names pages of any file of its work: what the work says may be
read on any of them, a locator only on the cited edition (or by a
section or theorem number another edition keeps), quoted words on the
file whose words they are; a `verified` on a page locator names a page
of the cited edition itself.

**A preprint's version of record.** A preprint's version of record is found by the preprint's own record
(Crossref's relation, the DOI or the journal reference its authors gave
arXiv), else by a record whose title and first author are the entry's
(OpenAlex, OpenReview, an accepted venue only); a title whose first
author differs is passed, never taken. Each version names where it was
found and how it was matched: a match by title can be wrong.

### The circles' question (the model's § 9)

`circles --ask` asks what each end relies on, a queue of the circles no
word holds real (§ 2): an agent is
served each circle's claims and lines, and answers each once; code
writes the lines it names. **Refined**: it names the part or claim a target
end relies on (adding the part's legend line, in the target's own words,
of a role that is no claim), the claims an about line's source end
restates for, or none (`[-]`), or turns a `restated` claim with no
original `unsupported`, and answers each line showing a candidate,
narrowed or kept; given, once every circle is answered, as one edges
edit, a new state. A circle with a line an earlier answer narrowed is
another circle, asked on that state. **Real**: every
end named and the circle stays, with why and what a person should
change, filed under the circle's key (never a finding: the circle fails
`unsupported-claims` already); shown "read real by" its author, never
asked again, and a changed key is another circle. The answer may only
narrow: it takes no line off unless it gives it back narrowed, widens
no end, adds no line, self mark or support, and never makes a standing
`restated`, `self` or `justified`. A narrowing keeps the edge, so an edit
reaches what it reached ([the model](model.md) § 5); a line a person pinned stays, and its
circle is read real with a proposal. Nothing makes a paper pass: a
refined circle's claims are established once their routes hold, and a
real one fails the gate until a person or the paper changes a line. A
build ends with the question on the circles of the state it keeps, and
an update after its reread and its search, whose lines the circles
read, unless the profile turns it off (`ask_circles_after_edges`): a
circle is asked before any
review spends on claims that cannot be established.
