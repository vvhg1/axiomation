# Judging edits to a paper's cut

A program has cut a paper into blocks from its layout alone,
and an author has proposed edits to the cut. You judge each edit: keep it,
or decline it. `cut.md` holds one part of the paper. `outline.md` shows where that part lies in the whole.

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

## The proposed edits

The author's edits are marked in `cut.md`:

- a marker line flagged `proposed` is a boundary the author added (a
  split);
- a line `⟦joined⟧ — …` marks a boundary the author removed (a join): the
  text on either side of it is one block now.

Keep an edit the rules call for: change nothing at all. Decline one they
do not: change its flag to `declined`, and write your reason after ` — `,
on the same line, in five words at most:

    ⟦b12 claim declined — short proof, one reliance⟧
    ⟦joined declined — steps rely on different things⟧

Declining a split joins the block back to the one before it. Declining a
join puts the boundary back. You judge the author's edits only: add,
delete or move no marker, and change nothing else, not the paper's text,
not the other markers, not a block's role.

Judge every edit by what the cut is for. Take the two sides of the
boundary as two nodes of the graph and ask: would a reader judging the
argument give them two verdicts, so that one could be wrong while the
other stands? Do they rely on different things, so that an edge would
point at one and not the other? Where either answer is yes, the boundary
serves the graph: keep the split, or decline the join. Where both are no,
the two sides are one piece of the argument: decline the split, or keep
the join. Weigh both ways alike: a needless block is a node that cannot
be judged on its own, and a missing one hides which piece fails.

## The file

`cut.md` is the cut with the author's edits made. A line `##### <file>`
starts a file of the paper. A marker line looks like

    ⟦b12 claim proposed⟧ — form: ...; environment: ...; labels: ...; ...

`b12` is the block's handle, `claim` its role, and `proposed` the flag.
What follows ` — ` describes the block, for your information only. Text
under a line `⟦context⟧` is the paper just before or after your part,
there to read, not to change. Leave the `⟦b12 continued⟧` lines as they
are.

Never change the paper's text: every character of it must stay, in order.
Line breaks and blank lines you add or remove do not matter.

`outline.md` lists the paper's headings and its labelled statements with
their first words, and marks where your part lies. `cut.md` is your answer: the cut as you leave it is what is
read back. You may use your other tools as well, in this directory and in
`/tmp`.

## When you are done

Judge every proposed edit. Then stop, and reply with one line: how many
you kept and how many you declined.
