# Changelog

What each release changes, for the people who use axiomation. A release
is a tag on `main`.

## 0.1.1 (2026-11-09)

Fixes to the review queues, the cited works' search and the README's
banner, a switch to read the cited works from their PDFs, lines that
leave from a part of their block, fewer findings about a result's
worth that the paper itself answers, and a clean stop on Ctrl-C.

- **A review queue resumes.** A `citationcheck`, `methods` or
  `fieldresults` session that settled a finding could not be resumed:
  every later run stopped at that answer. It resumes now, and asks
  nothing it has answered.
- **What you answer during a review round moves no other task.**
  Answering a finding, or filing your own reading of a citation site,
  while a round was open made the round ask every task after it again,
  and pay for each. Now that task alone leaves the round.
- **A queue that cannot go on stops cleanly**, with `stopped: queue:`
  and the reason, not a traceback; what it filed stays filed.
- **Ctrl-C stops a run cleanly.** It ended in a traceback, and a
  session in flight could go on, and cost, after it. Now it ends the
  sessions in flight and prints `stopped: interrupted` with what was
  spent, and a script running the command stops too. The same command
  resumes, asking again only what a session was cut off in.
- **A dry run after a stop prices only what is left.** It priced a
  session whose answer the store already held, and a queue's tasks
  that a run had settled. Now the first is $0 and the second are left
  out.
- **A paper named by a relative path.** With the paper named `.`, a
  queue session's tools did not start, and the run said only that it
  settled no task. They start now, and a tool server that does not
  start stops the run with its error before any session is paid for.
- **The cited works' search is tried before a review pays for it.**
  `sources status` tries the Voyage key on one word and says
  `accepted`, or `FAILED` with the reason. A `review` whose sessions
  read the cited works stops before asking any when the search fails,
  and its `--dry-run` says so.
- **The library can be switched off.** `library = "off"`, in the user
  file or a paper's `[sources]`, reads each cited work from its PDF
  though a Qdrant server is set; `AXIOMATION_LIBRARY=off` does so for one
  run.
- **The README's banner** shows on PyPI, and on GitHub follows the theme
  GitHub is set to. It says review, not verification.
- **A line can leave from a part of its block.** An edge naming a part
  still took in the lines of the block's other sentences, so a claim
  could rest on premises that part never wrote. A uses line may now name
  the part it leaves from, `→ uses [<part>] T`, and an edge onto that part
  takes in its lines alone. `place --ask` has the edges agent place a
  block's lines; a build and an update run it before the circles
  (`--no-place` skips it). A profile of your own whose edges agent is not
  on Claude Code must set `place_lines_after_edges = false`. A store
  holding such a line is not read by 0.1.0.
- **A result's worth is held to what the paper claims.** The findings
  session filed results as "a standard fact, so it adds nothing"
  although the paper named them standard itself, or claimed no new
  result of that kind, and filed definitions as saying nothing. It files
  no worth finding on a definition or a sentence that restates one, nor
  on a result the paper says follows from another of its own. Whether a
  result is a standard fact is now the `contribution` session's
  question, held to the paper's own statement of its contribution and
  shown in a work it read. That session is not asked by default: ask it
  with `--session contribution` (or `--session field`).

## 0.1.0 (2026-11-07)

The first public release.

- **The graph.** `build` reads a paper in LaTeX or Markdown and builds
  its argument graph: its blocks, their roles, and what each claim rests
  on. Every claim gets a standing (justified by another block, supported
  in its own block, a restatement of another claim, conjectured, or
  unsupported); an unsupported one is shown as a gap. `show`, `reach`, `open` and the viewer (`view`) read the graph.
- **Edits.** `update` carries an edit onto the graph, putting to the
  agents only what the edit touched, and marks what it may have undone; `changes`
  shows what changed and which judgments lapsed.
- **Reviews.** `review` has agents read the paper: each proof, each
  citation against the cited work, each script against the text it
  supports. Three more reviews read the paper against its field
  (its contribution, the field's results, the related work), on request
  (`--session field`). `check` says whether the paper passes its gates
  and what stands in the way; `inbox` lists the decisions left to the
  person. `sync` runs the update, the reviews and the inbox as
  one round.
- **Corrections.** Blocks, edges and judgments can be edited by hand; a
  person's judgment decides over an agent's. What a person files is
  signed with their key (`keys`, `confirm`).
- **The cited works** are read from their PDFs on the person's machine,
  with no online service; a page with no text layer is read as an image.
  A proof or a claim is checked on the pages of any work in the paper's
  bibliography. A ruling, the answer to whether a referee would want an
  unsupported claim argued, may name the pages it rests on (`rule
  --page`). The sessions that look outward (prior work, a result's
  source, the related work) read the whole bibliography before the web,
  and may name a work it lists but the paper cites nowhere; `show` says
  so. A judgment or a ruling that rests on a cited work lapses when that
  work's PDF is replaced by one with other content. The `library` extra adds a corpus in Qdrant, OCR and search
  by meaning.
- **Settings.** A person's settings (the services, keys, OCR engine,
  contact address, a default budget) are in one user file,
  `~/.config/axiomation/config.toml`, with the shell's
  `AXIOMATION_<KEY>` over it. A paper's `axiomation.toml`, or a
  workspace's (several papers sharing one corpus), may set its own
  budget; no budget is set until someone sets one. `axiomation init`
  writes the user file or a paper's or a workspace's, with every setting
  commented out and explained, and
  `axiomation settings` shows every value in force and where it came
  from.
- **Scripts.** A paper's Python scripts run in the environment its
  files declare (a uv, Poetry, Pixi, Pipenv or conda project, or a
  `.venv/`), else `python3`.
- **Hosts.** The agents run on Claude Code. OpenCode is experimental,
  refused unless `AXIOMATION_EXPERIMENTAL_OPENCODE=1` is set.
