# prompt
Correct the cut in cut.md, as your instructions say.
# instructions
# Re-cutting the changed parts of a revised paper

A paper was revised. Its cut was settled on the earlier version and kept
wherever the text stayed the same. Where the text changed, a program has
cut it again from its layout alone: paragraphs, environments, displays,
footnotes. It does not understand the argument. You do. Correct the cut
in `cut.md` where it does not serve the argument. `cut.md` holds one
changed part of the paper. `outline.md` shows where that part lies in the whole.

`old.md` shows the same part as it read before the revision, as it was
cut then, each block under its roles. Where the revision kept a block's
words, that boundary was settled by a full reading and is usually right
still; where the words changed, cut by the new text. The old cut is a
reading to learn from, not a constraint.

Above all, split every proof at its steps. The program never does: a
changed proof reaches you as one block, or as its paragraphs. Wherever
what the proof relies on changes (a lemma, an assumption, a definition,
an earlier step), start a new block there.

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
every block's text in reading order, each under a marker line, such as

    ⟦b12 claim⟧ — form: ...; environment: ...; labels: ...; ...

`b12` is the block's handle, then its roles: `none` where it has none
yet, for its roles are given after you. What follows ` — ` describes the
block, for your information only. A line `##### <file>` starts a file of
the paper. Where a block sits inside another (a footnote in a paragraph),
the outer block's text resumes under `⟦b12 continued⟧`, which is not a
boundary. Between the lines, `⟦+ justification⟧` marks where a part of
the block with that role starts, and `⟦group proof <name>⟧` and
`⟦end group <name>⟧` a run of blocks judged as one, such as a proof.
These lines are shown, not read: leave them as they are.

Text under a line `⟦context⟧` is the paper just before or after your
part, its cut settled, there to read, not to correct: add, delete or move
no marker in it, and do not join your first block to it.

## How you correct it

You correct the cut by editing the marker lines, and only them. Change a
boundary only with words of the paper you can quote:

- **Split** a block: add a line `⟦new — <why>⟧` where the new block starts,
  at the start of a sentence. It may go in the middle of a line: break the
  line there, and put the marker on a line of its own.
- **Join** a block to the one before it: replace its marker line with a
  line that quotes words of both blocks and says why they are one unit:

      ⟦join · "<words of the block before>" "<words of this block>" — <why>⟧

  A join never crosses a group's line: blocks on either side of it stay
  apart. Joining every block of a group into one makes that run one
  block, and the group goes with it. A block inside another (a footnote)
  is never joined to the block around it, nor to anything: the cut keeps
  it apart by design, so leave its marker as it is.
- **Move** a boundary: move the block's marker line to where the block
  should start, and give the reason in it: `⟦b12 claim — <why>⟧`.

The words in quotes are the blocks' own, as `cut.md` shows them, line
breaks aside. Do not change any block's role or description; roles are
given after you. When you finish, `cut.md` is checked; if the check
reports problems, fix them.

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
blocks you split, joined and moved.

# cut.md
##### main.tex

⟦context⟧
\section{Setting}

⟦b1 none⟧ — form: prose; environment: none; labels: none; the cutter's doubts: none; marked parts: none
We fix a field of scalars.
A second sentence of the setting, edited.

⟦context⟧
\begin{definition}
The gain is the ratio of output to input.
\end{definition}
\section{Results}

# old.md
# This part before the revision

The blocks of the earlier version that this part of `cut.md` replaces, in
reading order, as they were cut then, each under a line with its roles.
Read it; do not edit it.

⟦was assumption⟧
We fix a field of scalars. A second sentence of the setting.
