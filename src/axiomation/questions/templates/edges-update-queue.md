## This session: the paper changed, one block at a time

The rules above say how a line reads and how the sheet writes it. In this
session you write no file: you answer through your tools, one task at a
time.

The paper was edited after its edges were written. Every line was carried
onto the new version, under the block that holds its words now. Each task
is one block the edit may have made untrue, served with what you need to
answer it: its text (before and now, where it changed), its lines as
carried, numbered (`L1`, `L2`, …), the blocks that qualify it, those that
rely on it, and the edit's other changed blocks. A task is one of five
kinds:

- **a changed block**: write its Δ and correct its lines so they read true
  of its new text. Where it was re-cut, its roles are open: give them in
  `roles`. Where it now holds two units (an example and the claim it
  leads to, a definition inside a claim), put on a split line
  (`✂ split "<words>" · "<evidence>" — <reason>`): it is applied at once,
  and the blocks it makes come back to you as changed blocks.
- **a block whose meaning moved by rule**: its text did not change, but it
  relies on a definition or an assumption whose Δ carries the change.
  Read it with the edit and write its Δ; correct a line only where the
  edit makes it untrue.
- **a block that may rely on what the edit wrote**: its text writes the
  words of a part a changed block now has, or cites a block that dropped
  its edge onto a part, and it has no edge onto that part's block. Put on
  the edge its text shows; where it relies on none, answer with nothing
  but the task.
- **a block to read again**: an edit near it, or anywhere for a block
  about the whole paper, may make one of its lines untrue. Correct such a
  line; where every line holds, answer with nothing but the task.
- **a block the check still finds a problem on**, once every other task
  is settled: mend it as the problems the task shows say.

A task may show a line its block lost onto a block the edit deleted: put
it on again onto the block that now says what it pointed at, or leave it
gone. It may show words a line quoted that the paper no longer holds, and
similar words where they may stand now: a hint, never a quote; quote the
paper as it reads now.

A line the edit does not touch stands: this is not a new reading of the
paper.

**The answer.** One `answer` a task:

- `take_off`: the numbers of the lines that no longer read true;
  `put_on`: each line to put on, as the sheet writes it. To correct a
  line, take it off and put the corrected one on.
- `delta` (a changed block, a moved block): `class` and `note`, how the
  edit changed what the block says, for the block whole; `part_deltas`
  beside it where its parts changed differently, each with its `part`,
  the label the legend gives it (`model` for `[model]`), not a role.
- `parts`: a part of the block that a line you put on names
  (`[<label>]`) and the legend does not hold yet, its `label` and its
  `words` as they stand; `drop_parts`: the labels of its parts whose words
  the edit removed.
- `roles` (a re-cut block only): its first part's role, then each later
  part's with the words it starts at: `justification | claim
  "Hence the bound holds"`; one role where it holds one.

**The Δ classes.** `unchanged`: it says what it said. `reworded`: the words
changed, the meaning did not. `narrowed` / `widened`: it covers fewer
cases, or more. `strengthened` / `weakened`: it asks or claims more, or
less. `removed`: what it said is gone. `moved`: it says the same, elsewhere
or of something else.

A corrected typo or a rephrasing is `reworded`, and what relies on the
block is not looked at again. Any other class carries the change on: what
relies on the block's claims is marked for a later check, and what relies
on its definitions or assumptions comes back to you as a moved block. A
block whose text did not change is never `reworded`. Read a block with the
blocks that qualify it, each a premise of what it says: an edit that
writes into a block what a qualifier already assumed changes nothing it
says with them.

`unchanged` stops the change, so write it only beyond doubt, and quote its
evidence in the note: three words or more of the block, or of a block that
qualifies it and that the edit did not change, that show the edit does
not move it: `"every measure here is finite": the cases the edit excludes
never arise here`. In doubt, write the class that carries the change on.

**What you read.** `paper.md`, in your folder, is the paper as it reads
now, each block under its handle, `⟦<handle> <roles>⟧`: read around a
block there, and search it for a term, a symbol or a label. It holds the
text alone; a block's lines are in its task, and `block` gives any
block's text before the edit and now, and its lines as they stand in
this run. Read only what the task needs.

Call one tool at a time. An `answer` is checked at once: accepted, it
returns your next task, so call `next` only to start, or when a reply
tells you to; refused, it says what to fix. When `next` says to call it
again, call it again; when it says to stop, stop and say done.
