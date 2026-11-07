# Deciding a paper's cut

A paper has been cut into blocks, each block given its roles, and runs of
blocks grouped. Each step may also have noted, as flags, what it saw about
the cut and could not settle. You decide the cut in `cut.md`: where a
boundary is wrong, correct it, with the paper's own words to show why.
`cut.md` holds one part of the paper.$outline_intro

## What the cut is for

The paper becomes an argument graph. Every block is a node. Where one part
of the argument relies on another, that is an edge between their blocks.
The graph is used to see exactly what each piece relies on, and to judge
the argument piece by piece: when something is wrong, to say which piece
fails. When the paper changes, it shows which pieces must be checked again.

A block is the argument's unit: the smallest span with one verdict and
one set of reliances.

A verdict is a judgement the argument needs: a claim the paper asserts
and something rests on, or a result it defends. A sentence that can be
judged on its own is not thereby a verdict. An attribution, a citation, a
comparison with the literature or a recommendation to the practitioner
has no verdict of the argument's, and stays with the block it serves.

The parts rely on different things only where an edge would point at one
and not the other. Where everything that relies on one part relies on the
other too, they share one set of reliances.

Two questions decide a boundary:

- **One verdict or two?** Could one part be wrong while the other stands,
  each a verdict the argument needs?
- **Do the parts rely on different things?** Would an edge point at one
  part and not the other?

Where either answer is yes, the parts are two blocks; where both are no,
they are one.

## The rules

- **Split proofs at their steps.** A proof whose steps rely on different
  things (a lemma, an assumption, a definition, an earlier step) is split
  where what a step relies on changes. A short proof that relies on one
  thing stays one block. An example that states a claim and then
  verifies it is split the same way: the claim, then the verification.
- **Keep claims apart.** A claim that is relied on or judged in its own
  right is split from a definition that other parts rely on, and from
  another claim it happens to share a paragraph with. A definition and the
  claim that it is well-formed (the defined thing exists, is unique, has
  the property the definition needs) are one verdict; a claim about the
  defined object that the argument relies on in its own right is another
  block. Inside a statement, a result followed by one numerical instance
  that is used elsewhere is split the same way.
- **Stop there.** Split only where the parts can be relied on or judged
  independently in the sense above: where an edge would point at one part
  and not the other, or one verdict could fail while the other stands. In
  set-up that defines the paper's own objects (its model, notation,
  assumptions), an assumption is a hypothesis the argument rests on; the
  conditions a definition places on what it defines are part of the
  definition, and a definition stated with the quantities it uses is one
  definition.
  Background that reports context (literature, motivation, the state of
  the field) stays whole, even where it makes claims or sets up what it
  reports; split it only where the paper later relies on something it
  defines.
- In a proof, checking a lemma's hypotheses and then applying it is one
  step, and an approximation that continues the same expression is part
  of the same chain.
- **Never cut inside a step.** A sentence and its display, or one chain of
  equalities and inequalities, stay together, even across a blank line. A
  display belongs to the sentence it is set in. Cut only at the start of a
  sentence.
- **Join** by the same two questions that split: two neighbours that are
  one verdict with one set of reliances are one block, whether or not the
  layout broke them. The layout often does: a sentence broken by a
  paragraph break, a display cut from its sentence, a proof's first line
  cut from its body, a step heading cut from its first paragraph, a stray
  brace or mark.
- **A wrong split and a wrong join cost alike.** A wrong split scatters one
  verdict over two blocks and loses the reliances between them; a wrong
  join fuses two verdicts and loses what relied on each.
- **Move** a boundary between two adjacent blocks where it is in the wrong
  place.

What is not wrong in itself:

- A block that plays two roles. This is the usual case, not a split
  waiting to happen: where the role changes inside a block, the block has
  two segments, and an edge can land on a segment. The roles in a marker
  line, and a `⟦+ <role>⟧` line, are no reason to split; split only by the
  two questions.
- A block with many users.
- A block with no role yet: it is new, and its roles are given after you.

## What you are shown

`cut.md` is the cut of your part of the paper, written out: every block's
text in reading order, each under a marker line, such as

    ⟦b12 claim, definition⟧ — form: ...; environment: ...; labels: ...; ...

`b12` is the block's handle, then its roles. What follows ` — ` describes
the block, for your information only. A line `##### <file>` starts a file
of the paper. Where a block sits inside another (a footnote in a
paragraph), the outer block's text resumes under `⟦b12 continued⟧`, which
is not a boundary. A `⟦b12 continued⟧` line, like a `⟦context⟧` line, is
fixed: never delete, move or change it.

Between the lines, you are shown what the steps before you recorded:

- `⟦+ justification⟧`: where a part of the block with that role starts.
- `⟦group proof <name>⟧` and `⟦end group <name>⟧`: a run of blocks judged
  as one, such as a proof.
- `⟦? <step>: <signal> · "<words>" — <note>⟧`: a flag, a step's position
  on the cut or a block, or a rule's.

A flag is a position, not a finding: it may be right or wrong, and a block
without a flag may be wrong too. Review every block of your part, flagged
or not, and take a position on every flag by the two questions. A
`two-block-group` flag asks whether the group's two blocks are one block
by the two questions: if they are, join them, and the group goes with it;
if the two questions keep them apart, leave both. These lines are shown, not
read: leave them as they are.

Text under a line `⟦context⟧` is the paper just before or after your
part, there to read, not to correct: add, delete or move no marker in
it, and do not join your first block to it.

## How you correct it

You correct the cut by editing the marker lines, and only them. Change a
boundary only with words of the paper you can quote:

- **Split** a block: add a line `⟦new — <why>⟧` where the new block starts,
  at the start of a sentence. It may go in the middle of a line: break the
  line there, and put the marker on a line of its own.
- **Join** a block to the one before it: replace its marker line with a
  line that quotes words of both blocks and says why they are one verdict:

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

$outline_note`cut.md` is your answer: the cut as you leave it there is
what is read back. You may use your other tools as well, in this
directory and in `/tmp`.

## When you are done

Go through the whole file, block by block, flagged or not. When every rule
above is applied, stop, and reply with one line: how many blocks you split,
joined and moved.
