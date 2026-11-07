## This session: lines marked stale, one owner at a time

The rules above say how a line reads and how the sheet writes it. In this
session you write no file: you answer through your tools, one owner at a
time.

An update changed this paper, and some lines were marked stale: the block
at a line's far end changed, and no one has read the line again since.
Each task is one owner of a marked line. It shows the owner's place and
text, its lines as the sheet holds them, numbered (`L1`, `L2`, …), each
mark (`⟦stale since <state>: <why>⟧`, the line it doubts named in its
`via`), and for each marked line the words of the block at its far end
before that update and now.

Read the owner's lines against what changed, under the rules above, and
answer once:

- `stands`: its lines read true of the paper as it is now. Say in `why`
  what the far end says now, and why the lines still hold.
- `changed`: a line no longer reads true, or one is missing. Give in
  `take_off` the numbers of the lines that no longer hold, and in
  `put_on` each line to put on the owner, in the sheet's notation (`→ …`
  or `⊢ …`). To correct a line, take it off and put the corrected one on.
  Say in `why` what changed and how the lines follow it.

Change only the task's owner, and only its edge and standing lines;
every block's text, roles and cut are fixed. A line stands unless the
change makes it untrue: this is not a new reading of the paper, and a
line no mark doubts stands as it is.

Read only what you need. The task shows the owner and what changed; read
more through the commands, which are capped and cheap. Do not read the
paper whole.

Your tools: `next` (your task), `stands`, `changed` (each returns the
next task when it accepts), and the reads `block`, `view` (100 lines),
`search` (a case-insensitive regex, at most 50 hits), `edges`, `hops`,
`standing`, `relied`, `group`, `cited`. An answer is checked at once; a
refusal says what to fix. When `next` says to call it again, call it
again; when it says to stop, stop and say done.
