## What you read and write

`paper.md` is the program's cut of your part of the paper, written out:
every block's text in reading order, each under a marker line. Read it;
do not change it. A marker line looks like

    ⟦b12 claim⟧ — form: ...; environment: ...; labels: ...; ...

`b12` is the block's handle and `claim` its role (`definition`,
`assumption`, `claim`, `justification` or `none`). What follows ` — `
describes the block, for your information only. A line `##### <file>`
starts a file of the paper. Where a block sits inside another (a footnote
in a paragraph), the outer block's text resumes under `⟦b12 continued⟧`,
which is not a boundary.

Text under a line `⟦context⟧` is the paper just before or after your
part, there to read, not to correct.

${ledger_note}${outline_note}Write your corrections in `cuts.md`, one a line, each naming a block by
its handle and quoting the paper's own words exactly as they stand in
`paper.md`:

    split b12 "the words the new block starts with"
    join b13
    move b13 "the words b13 starts with now"

- `split`: a new block starts at those words, inside b12. It may start
  in the middle of a line. Split one block at several places with several
  lines.
- `join`: b13 joins the block before it.
- `move`: the boundary between b13 and the block before it moves to
  those words, in either block.

Quote enough words that they stand once in the block. A block you leave
as it is takes no line; a cut with nothing to correct is the one line
`none`. Roles are a separate step: correct none here.

## When you are done

A split is right only where the rules call for it. The limit on
splitting, in the words of the rules the cut is judged by: "Stop
splitting where the parts cannot be relied on, judged or changed
independently: split only where an edge would point at one part and not
the other, or one part could be wrong while the other stands."

Go through the whole paper, block by block. When every rule above is
applied within that limit, write `cuts.md` whole, then finish. When you
finish, a check reads `cuts.md` and hands back the lines that do not
read; fix those, keep the rest, and finish again.
