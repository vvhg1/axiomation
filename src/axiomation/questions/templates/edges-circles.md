## This session: circles of what claims rest on, one circle at a time

The rules above say how a line reads and how the sheet writes it. In this
session you write no file: you answer through your tools, one circle at a
time.

Some claims of this paper rest on each other in a circle: one claim's
lines rely on a second, whose lines rely on the first, so neither can be
established. Most such circles are not circular arguments in the paper.
They close because a line's end names less than the text relies on: a
line pointing at a whole block where it needs only a definition or a
hypothesis written in it, or one naming no claim of a block that holds
several, so it is read as relying on each of them.

Each task is one circle. It shows its claims, numbered (`C1`, `C2`, …),
and each line on it as the sheet holds it under its owner, numbered
(`L1`, `L2`, …), with where an end may name less than the text relies on:

- "names no part of a block holding definition or assumption content":
  the line points at the block whole; it may rely only on a definition
  or an assumption written there, not on the block's claim;
- "names no claim of a block of several": the line relies on every
  claim of its target;
- "names none of its block's claims at its source end": an about line
  of a block of several claims, read as possibly restating for each;
- "restates a restated claim": the line points at another restatement,
  not at what both restate.

These marks are candidates, never verdicts. Read each line on the circle
in the paper's words, and the blocks at both its ends, and ask: what does
this line's source rely on, by the text? Then answer once:

- `refined`: some end names less than the text relies on. Name every end
  on the circle you can name more exactly, not only the first that breaks
  it; code writes the lines:
  - `narrow`: a line by its number, with `target_end`, the labels of the
    parts or the handles of the claims its target end relies on (`→ uses
    T [<label>]`, `→ about T [<claim's handle>]`), or `own_end`, an about
    line's own block's claims it restates for, or `["-"]` for none, or,
    where its block's lines name the parts they leave from already, a
    uses line's parts of its own block it leaves from (`→ uses [<label>]
    T`);
  - `parts`: a part a narrowed end names that its block lacks: the block,
    a label, the block's own words holding the definition or hypothesis
    (never a claim), and its role, `definition`, `assumption` or
    `justification`;
  - `keep`: the numbers of the lines with a candidate end that relies on
    what it names as it stands;
  - `unsupported`: the numbers of the circle's `restated` claims that
    restate no original.
  Say in `why` what the text relies on, line by line.
- `real`: every end names what the text relies on and the circle stays:
  the paper argues one claim by the other and the other by the first, or
  its restatements have no original. Give in `proposal` what a person
  should change: the line you think is wrong, or what the paper must add.
  Answer `real` too where the words a line relies on cannot be a part (a
  formula holding the definition and the claim together), and say in the
  proposal where the block should be split.

You may only narrow an end: never widen one, add a line or support, or
change a line's kind, target, mark or quote. Every block's text, roles
and cut are fixed. A line a person wrote or kept stays as written (the
task says so beside it): where the circle closes through it, answer
`real` and say in the proposal what the person should change.

Read only what you need. The task shows the circle; read more through the
commands, which are capped and cheap. Do not read the paper whole.

Your tools: `next` (your task), `refined`, `real` (each returns the next
task when it accepts), and the reads `block`, `view` (100 lines),
`search` (a case-insensitive regex, at most 50 hits), `edges`, `hops`,
`standing`, `relied`, `group`, `cited`. An answer is checked at once; a
refusal says what to fix. When `next` says to call it again, call it
again; when it says to stop, stop and say done.
