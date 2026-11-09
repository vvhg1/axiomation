## This session: where a block's lines leave from, one block at a time

The rules above say how a line reads and how the sheet writes it. In this
session you write no file: you answer through your tools, one block at a
time.

Each task is one block that another block's line enters at a part: the
line relies on one part's words, a definition or a hypothesis written
there, not on the block whole. What a claim rests on is read through such
a line into the block. Until the block's own lines say which part they
leave from, every line of the block is read as leaving from each part, so
a claim relying on one sentence's definition rests on what another
sentence of the same block relies on.

The task shows the block's text, its parts (label, words, role), the lines
entering it at its parts, its own uses lines numbered (`L1`, `L2`, …), and
the qualifiers onto it. A qualifier naming parts qualifies those parts
alone; one naming none qualifies the block whole.

Read each line of the block in the paper's words and ask: whose words
write this reliance? Then answer once:

- `placed`: name, for each line, the parts it leaves from, `from`: the
  part whose words write the symbol, term or hypothesis the line relies
  on, several where several parts write it, or `[]` where the whole block
  writes it, so every part relies on it. A line from the block whole is
  read with every part, so where a line leaves from words no part holds
  (another sentence's definition or hypothesis), add that part first, in
  `parts`: a label, the block's own words for it, verbatim, never a
  claim's and never words a part holds, and its role, `definition`,
  `assumption` or `justification`; then place the line at it. A line the
  block's claim relies on leaves from the words that give the claim's
  reason ("thus", "since …"): add them as a `justification` part, never
  the claim's own words. The claim's own reading takes its whole block,
  so it keeps the line. Then name, in `writes`, each part whose words write another
  part of the block (a definition written in terms of another: `pi` writes
  `W` where π is defined from W), with the parts it uses and its own words
  that write them. A part entered then carries the lines that leave from
  it, from the parts it writes, and from the block whole. Say in `why`
  where each line's reliance is written. Code writes the lines.
- `whole`: no line leaves from one part apart, or the parts are one thought
  and write each other throughout. Say why. The block is then read
  whole.

A part's words may be its anchor alone, an item's marker or a heading:
such a part stands for the text it heads. Place that text's lines at it,
and cut no new part inside it.

A line quoted outside a part may still leave from it: a writer quotes a
symbol where it first occurs, and a part relies on it where its words
write the symbol. Place by what the words write, never by where the quote
lies. In doubt between a part and the block whole, the block whole: a
line from the block whole is read with every part, so it never drops a
premise; a line placed at the wrong part drops one.

You may only add parts for the words a line leaves from, place lines and
name what each part writes: never change a line's kind, target, quote or
target end, take a line off, or add a line out of the block. A line a
person wrote or kept stays as written (the task says so beside it): leave
it out of `lines`.

Read only what you need. The task shows the block and its lines; read
more through the commands, which are capped and cheap. Do not read the
paper whole.

Your tools: `next` (your task), `placed`, `whole` (each returns the next
task when it accepts), and the reads `block`, `view` (100 lines), `search`
(a case-insensitive regex, at most 50 hits), `edges`, `hops`, `standing`,
`relied`, `group`, `cited`. An answer is checked at once; a refusal says
what to fix. When `next` says to call it again, call it again; when it
says to stop, stop and say done.
