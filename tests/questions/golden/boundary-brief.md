# Correcting a paper's cut

A program has cut a paper into blocks from its layout alone:
paragraphs, environments, displays, footnotes. It does not understand the
argument. You do. Correct the cut where it does not serve the argument.
You are given one part of the paper. `outline.md` shows where that part lies in the whole.

Above all, split every proof at its steps. The program never does: each
proof reaches you as one block, or as its paragraphs. Wherever what the
proof relies on changes (a lemma, an assumption, a definition, an earlier
step), start a new block there.

## What the cut is for

The paper becomes an argument graph. Every block is a node. Where one part
of the argument relies on another, that is an edge between their blocks.
The graph is used to see exactly what each piece relies on, and to judge
the argument piece by piece: when something is wrong, to say which piece
fails. When the paper changes, it shows which pieces must be checked again.

## The rules

- **Split proofs at their steps.** A proof whose steps rely on different
  things (a lemma, an assumption, a definition, an earlier step) is split
  where what a step relies on changes. A short proof that relies on one
  thing stays one block. An example that states a claim and then
  verifies it is split the same way: the claim, then the verification.
- **Keep claims apart.** A claim that is relied on or judged in its own
  right is split from a definition that other parts rely on, and from
  another claim it happens to share a paragraph with. This holds inside a
  statement too: a definition followed by a claim about it, or a result
  followed by one numerical instance that is used elsewhere.
- **Stop there.** Split only where the parts can be relied on, judged or
  changed independently: where an edge would point at one part and not the
  other, or one part could be wrong while the other stands. Set-up that
  defines the paper's own objects (its model, notation, assumptions, and
  its claims about them) is split into its definitions, assumptions and
  claims. Background that only reports context (literature, motivation,
  the state of the field) stays whole, even where it makes claims.
- In a proof, checking a lemma's hypotheses and then applying it is one
  step, and an approximation that continues the same expression is part
  of the same chain.
- **Never cut inside a step.** A sentence and its display, or one chain of
  equalities and inequalities, stay together, even across a blank line. A
  display belongs to the sentence it is set in. Cut only at the start of a
  sentence.
- **Merge** blocks where the layout breaks one unit: a sentence broken by a
  paragraph break, a display cut from its sentence, a proof's first line
  cut from its body, a step heading cut from its first paragraph, a stray
  brace or mark.
- **Rebound** two adjacent blocks where the boundary between them is in the
  wrong place.

- The cutter's doubts point at boundaries it could not decide. Look at
  them, and correct only what is wrong.

## The file you edit: `cut.md`

`cut.md` is the program's cut of your part of the paper, written out:
every block's text in reading order, each under a marker line. A marker line looks like

    ⟦b12 claim⟧ — form: ...; environment: ...; labels: ...; ...

`b12` is the block's handle and `claim` its role (`definition`,
`assumption`, `claim`, `justification` or `none`). What follows ` — `
describes the block, for your information only. A line `##### <file>`
starts a file of the paper. Where a block sits inside another (a footnote
in a paragraph), the outer block's text resumes under `⟦b12 continued⟧`,
which is not a boundary: leave those lines as they are.

Text under a line `⟦context⟧` is the paper just before or after your
part, there to read, not to correct: add, delete or move no marker in
it, and do not join your first block to it.

You correct the cut by editing the marker lines, and only them:

- **Split** a block: add a line `⟦new⟧` where the new block starts. It
  may go in the middle of a line: break the line there, and put the marker
  on a line of its own.
- **Join** a block to the one before it: delete its marker line.
- **Move** a boundary: delete the marker line and add `⟦new⟧` where the
  block should start.

Do not change any block's role or description; roles are a separate step.

Never change the paper's text: every character of it must stay, in order.
Line breaks and blank lines you add or remove do not matter.

`outline.md` lists the paper's headings and its labelled statements with
their first words, and marks where your part lies. `cut.md` is your answer: the cut as you leave it there is
what is read back. You may use your other tools as well, in this
directory and in `/tmp`.

## When you are done

A split is right only where the rules call for it. The limit on
splitting, in the words of the rules the cut is judged by: "Stop
splitting where the parts cannot be relied on, judged or changed
independently: split only where an edge would point at one part and not
the other, or one part could be wrong while the other stands."

Go through the whole file, block by block. When every rule above is
applied within that limit, stop, and reply with one line: how many
markers you added and deleted.
