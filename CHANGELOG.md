# Changelog

What each release changes, for the people who use axiomation. A release
is a tag on `main`.

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
