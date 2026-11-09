# prompt
Write the Δ lines and the reopened blocks of edges.md, as your instructions say.
# instructions
# Writing a paper's edges

`edges.md`, in your working directory, is a paper cut into blocks. Every
block is a node of the paper's argument graph; your job is the graph's
edges. Edit `edges.md` alone; it is your answer.

## What the graph is for

An edit to the paper must reach every block whose status it could change:
whether the block is true, well-posed, supported, or accurate in what it
says. And a claim nothing in the paper supports must show as unsupported.
An edge is re-checked when either end changes. A change to a definition or
an assumption travels through every uses edge onto it without asking, and
on along the dependents' own uses edges while it runs through definitions
and assumptions; a statement's hypotheses are assumption content. So the
sheet records only what the text writes: a reliance the text leaves to an
intermediary reaches its block by that rule, not by an edge. Recall comes
first: a missing edge breaks propagation, a false one only costs a
re-check.

## The sheet

Each block opens with a line `⟦<handle> <roles>⟧ <id> (<form>)`: its
handle, the roles of its segments joined by `+` (definition, assumption,
claim, justification, motivation; `none` for a heading or a transition),
its place in the paper and its form. Where a block's role changes inside
it, the text carries `⟦+ <role>⟧` at the change. `⟦…⟧` joins the runs of a
block that the paper interrupts. A group, a run of blocks judged as one
(a proof of several paragraphs), lies between `⟦group <handle> <kind>⟧`
and `⟦end group <handle>⟧`. A group's handle is its proof's own label
(`proof:X` names the whole proof, not its first paragraph), else `group:`
and its first block's handle. The cut, the roles and the groups are fixed:
never change a block's text, a block line or a group line.

Under each block, and after each group's end line, write its outgoing
edges, then the standing of each claim it makes, then a done mark:

    → <kind> <target> [<part or claim>, …] [ref|name|position] [where-used|generalizes|sketch|analogy] · "<evidence, verbatim but for whitespace>" — note
    → self [<claim>, …] [sketch|analogy] · "<its reason: the citation or the step>"   the block supports its own claim (no edge)
    ⊢ <status> [omitted|promised] [<handle>] · "<the claim's words>" — note
    ⟦done <handle>⟧              read; no arrows means no edges

- **Kind:** `uses`, `about`, `justifies` or `qualifies` (below).
- **Target:** a block's or a group's handle; `scope:paper` (below); or a
  file of the paper, `file:<path>`: one the header's `Files:` line or a
  `Code:` line names, as it names it, or a path the text writes whole.
- **Part:** in brackets after the target, which part of a target holding
  several the edge relies on. Each part you name gets one legend line in
  the sheet's header, above the `---` line: exactly two spaces, the target's
  handle, the label in brackets, a colon, and the target's own words for
  the part in double quotes, verbatim. For example, with the two leading
  spaces, `  some-block [K_m]: "denote by $K_m$ the kernel"`; a part
  inside a claim takes the role of its content at the end (`definition`,
  `assumption`, `justification` or `motivation`), since a claim itself is
  named by its handle, never by a part:
  `  some-block [K_m]: "denote by $K_m$ the kernel" (definition)`.
- **Claim:** where a block holds several claims, a line names the claim it
  argues, supports, relies on or restates by the claim's handle, in its
  brackets: a justifies or uses edge after its target, a self mark after
  `self`, and an about edge restating one claim of its own block before
  its target (`→ about [r1] <target> [<claim>] · "…"`). The handle is the
  one the claim's ⊢ line shows, or one you give it there,
  `⊢ justified [main] · "…"`, unique in its block. Where the block's
  words do not say which claim, name none: the line then counts for each.
- **Source part:** a uses line may name, in brackets after `uses`, the
  parts of its own block it leaves from, by their labels in the legend:
  `→ uses [<label>] <target> · "…"`, where only that part's words rely on
  the target. A line from one part of a block onto another part of the
  same block, `→ uses [<label>] <its own block> [<other label>] · "…"`,
  says the first part's words write the second. A block one of whose
  lines names a part it leaves from is read by its parts, each line naming
  none leaving from the block whole: name one only where a task asks
  where a block's lines leave from. A line written again keeps the parts
  it leaves from as they stand.
- **Marks:** `ref`, the evidence holds a `\ref` of the target; `name`,
  it names the target in words ("Theorem 3", "the second lemma"); `position`,
  it points at it by place ("the display below", "the previous step"). No mark
  where the edge rests on a symbol or a term.
- **`where-used`**, on an about edge only: the source claims where the
  target is used (below).
- **`generalizes`**, on an about edge between premises only, never with
  `where-used`: the source, an assumption, is wider than the target, an
  assumption or a definition's content written before it, and the target
  is a case of it (below).
- **`sketch` or `analogy`**, on a justifies edge or a self mark only, one
  at most: how completely the justification argues, by its own words
  (below).
- **Evidence:** the source's own words that write the reliance, copied
  from the block, verbatim but for whitespace, without the `⟦+ role⟧`
  markers.
- **Standing (`⊢`):** `justified`, a justifies edge comes in; `self`, its
  own reasons or citation: a self mark on the block whose evidence is that
  reason (the citation, the step, the "since …"), never the claim's own
  words; `restated`, it says no more than what one of its about edges
  points at says, and takes its support from there; `conjectured`, needing
  none; `unsupported`, asserted and supported by nothing in the paper,
  followed by `omitted` or `promised` where the paper says it leaves the
  argument out or gives it elsewhere (below). Every block with a claim
  role gets a line per claim that stands differently; the claim's words
  are copied from the block as evidence is. Two claims of one block never
  share their words: a standing's words are the quote that finds this
  claim in its sentence, not the sentence; quote what singles each
  reading out.
  Where you read a claim in a block whose roles give none, write its
  standing all the same; that adds the claim role to the block.
- **The ambient frame:** one header line, above the `---` line, naming the
  blocks that are the ambient frame (below):

      Pervasive: <handle>, <handle>

The note after ` — ` is optional, free text.

## The rules

**The criterion.** S depends on T when some change to T's content could
change S's status: whether S is true, well-posed, supported, or accurate in
what it says. Every rule below follows from it.

**Four kinds, told apart by how they propagate:**

| kind | when T changes | when S changes | support |
|---|---|---|---|
| **uses:** S relies on T's content | onto a definition or an assumption, S's meaning changes without asking and the change goes on; onto a claim's conclusion, S is asked whether its reliance holds | S's edges are found again | travels: S rests on T |
| **about:** S describes T | is S's description still accurate? | the same, from S's side | does not travel: describing an unsupported lemma is not unsupported; a restated claim takes its standing through it |
| **justifies:** J argues for T | is J still offered for T, and how completely: keep the line, re-flag it, or retract it? | T's support is recomputed | defines it |
| **qualifies:** an assumption A constrains a definition D | — | a change to A is a change to D's meaning, carried on to D's users | A is a premise of every result resting on D |

- **uses** is a definition use, a hypothesis or a result; generalizing and
  instantiating are uses (an example uses the definition it instantiates).
  Onto a definition, the line between uses and about is object against
  text: a claim that asserts something of the defined object uses it,
  since if the definition changes the claim's truth is in question; a
  claim that says something of the text or the argument is about it.
  Propagation through a definition is the same either way; onto a claim
  it is not, since a restated claim takes its support through its about
  edge. A defined term or symbol in descriptive prose (an abstract, an
  introduction, a remark) is a uses edge wherever the prose asserts
  something of the object ("for convex losses the excess risk is O(1/n)"
  uses the paper's definition of the loss). Onto a claim, S that works
  with T's quantity, instance or step uses T; S that speaks of T as a
  statement is about T. A roadmap that numbers steps defines their names:
  the numbering is a part of the roadmap with definition content, and
  "stage three" elsewhere uses that part, beside any about edge to the
  step itself. A symbol abbreviated from a defined object keeps its uses
  edge to the definition. A symbol's definition site is the block that
  writes it in a defining display or sentence. A block that glosses
  symbols in words ("μ is the mean dose") defines their names: a part
  with definition content, which uses the display, not the reverse. A
  block that writes the symbol uses the display; one that writes the name
  uses the gloss.
- **about** is a gloss, a preview, a summary, a roadmap, a proof summary
  outside its proof, a heading that names content, a remark on the
  argument; most `\ref`s in prose are this. Descriptions have their edges
  whatever role the sheet gives them. A restatement is about what it
  restates, from the less authoritative to the more (a gloss to its
  definition, a preview to the full statement, a claim about what the
  method yields to the result that shows it), never about a sibling
  restatement of the same thing: an edit to the shared target re-checks
  both. An assumption that repeats another (a later section restating
  the standing assumptions) is about it: the same premise. One that
  narrows or adds to it, written after it (a further condition, a finite
  set where the earlier one allows any), uses it, and is a premise of its
  own. One that widens it, written after it (an appendix's general
  setting over the text's special one: a measure space where the text
  took a finite set), is about it, flagged `generalizes`, and is a premise
  of its own: `→ about finite-states generalizes · "any measure space"`,
  its evidence the wider block's own words that write the target's object
  in general form. Where the special case sits inside a definition (a
  condition the definition carries), the edge names that part. Where the
  wider block also writes the narrower one's symbols, its uses edge onto
  it stands as well. A claim about where T
  is used ("assumption A is needed only for Theorem 4", "the smoothing
  bound enters only through Proposition 9") is an about edge to T flagged
  `where-used`, re-checked also when T's incoming uses edges change. Onto
  a proof, the target is what the words describe: the whole proof ("the
  proof has two parts") is its group; one step or device ("the truncation
  argument", "the coupling") is the paragraph that holds it. An
  environment cut into several blocks with no group is described block by
  block: a description of it is about each of its blocks. A claim over a
  span of the text, by its form: one that names what it describes ("the
  two remarks above") is about those blocks; one that says where T is or
  is not used ("no result in this section assumes A") is about T, flagged
  `where-used`; one about the whole text with no node to hang on ("the
  paper never uses compactness") is about `scope:paper`. A
  contradiction is an about edge whose check fails; the sheet records the
  edge, never the verdict.
- **justifies** runs from a justification to any node: a proof, a
  derivation, an example offered as evidence, a citation standing in for a
  proof, a claim that discharges a provisional assumption. Its source is a
  block, or a group where the justification is one run (a proof of several
  paragraphs); "the justification of T" is whatever has a justifies edge
  to T, and no group names what it justifies.

  How completely J argues is read from J's own words, and only from them.
  A justifies edge or a self mark carries at most one flag:
  - `sketch`: J's words say the argument as written stops short of T: an
    outline ("Proof sketch", "we only outline"), a heuristic
    ("heuristically"), or a special case written with the rest declared
    to follow ("We give the proof for two samples; more samples change
    only the notation.", the second sentence the declaration). A special
    case taken without such a sentence is no sketch. Where J writes one
    case and declares the rest to follow, T's own statement tells the two
    flags apart: where the rest is T at its full generality (more samples,
    any constant), J's edge onto T is a `sketch`, and the declaring words'
    pointer at the written case is uses edges; where the rest is a case T
    states apart (another quantity, another norm), it is a claim of T with
    its own ⊢ line, and the declaring block's edge onto T names it, an
    `analogy`.
  - `analogy`: J's words say the argument is another one's, carried over,
    and write none of it, naming what they carry over ("as for
    Proposition 3, with the two samples exchanged", "the proof follows
    [12, Thm 2]", "the same estimate, run with the variance in place of
    the mean, gives"). What it carries over is an argument, named by a
    uses edge from J's block onto the proof it carries (the proof's
    block, or its group where the proof is one run; the steps alone
    where the words name only steps), never onto the result that proof
    argues; or, where the argument is a cited work's, by the citation
    inside the flagged line's own quote. "As for Proposition 3" writes
    two uses edges: onto Proposition 3, by the words that name it, and
    onto its proof, the argument carried. The flag never stands in for
    the uses edges J's words write: where they name blocks of the
    paper, by `\ref`, by name or by position ("as in the case above"),
    each is a uses edge from J's block, whatever group J sits in. A
    proof's closing "the other cases are similar, and we leave them
    out" carries the proof it closes: a justifies edge from its block
    onto T naming the other cases' claim, `analogy`, beside the group's
    edge naming the case it writes out, and a uses edge from its block
    onto that group; where the proof is one block, that block's own line
    naming the written case's claim, beside its `analogy` line naming the
    others', is what it carries over. The check refuses an analogy whose block uses no
    argument and whose own quote holds no citation.

  No flag where J writes the argument out, however thin you find it: its
  gaps are a reviewer's, and a fresh reading of the same words gives the
  same flags. A pointer inside an argument ("bounded as in the previous
  step"), the writing going on past it to T, speaks of a step, not of T:
  no flag. Three sentences told apart: "the proof is standard" names no
  argument, so it justifies nothing and T is `unsupported omitted`; "the
  proof follows [12, Thm 2]" names one to carry over, so `analogy`, the
  citation what it carries over; "by [12, Thm 2] the claim holds" applies
  a result, a citation justification in full, no flag.
- **qualifies** runs from a block with an assumption role to the
  definition, or the part of one, it constrains: a standing assumption
  ("from here on, every dose is given in milligrams" qualifies the dose's
  definition), a scope restriction. It is standing only if no user of the
  definition is stated without it: where results state their assumptions
  themselves ("Under A, …"), the reliance travels on their uses edges and
  there is no qualifies edge. The paper's own standing assumption or scope
  restriction is never the ambient frame, however it is worded, as a
  convention or as holding throughout: where standing it qualifies, else
  its users' uses edges carry it. It names every part of its target the
  assumption constrains. A uses edge between the same blocks stands
  beside it where the assumption writes the definition's symbols. A
  positional scope for an assumption is not needed: the definition's
  users are its reach.
- **Not an edge:** navigation ("the proof is in the supplement", "see
  Section 4"), which depends on where T is, not on what it says; citations
  of outside works.

**Sources and targets.** The source is the smallest block that writes the
reliance, never a segment and never a group, but for justifies (above): so
a long proof re-asks only its changed paragraphs. A target is:
- a block, or a group where S relies on a whole argument;
- a part of a block, named by the target's own words where the block
  holds several and S relies on one. A part takes its segment's role
  unless the sheet says otherwise: a claim's formula that later blocks
  write as notation, or define something by, is definition content. An
  edge onto a claim block as a whole is only asked: where S relies on
  definition content inside a claim block, name that part, with its role;
- a file of the paper. A script is listed on a `Code:` line of the
  header, its key and its opening line, which says what it computes: the
  text rarely names it. A block uses the script, `uses file:<key>`, where
  a value or a result it states rests on the script's output (a number it
  computes, a check it runs); it is about the script, `about file:<key>`,
  where it describes the script ("the script recomputes it") and rests
  nothing on it. The evidence is the block's words for the value or the
  result, with no mark;
- `scope:paper`, for a claim about the text as a whole that no finite set
  of edges keeps correct, re-checked on any edit or insertion; never for a
  scope restriction, which qualifies.

No edge lies inside a block, but a part's uses line onto another part of
it (above): a block whose claim carries its own reason or citation
supports it itself, recorded as a self mark so that a missed outward edge
never reads as self-support. Cycles between blocks are allowed (notation
used before its definition, which uses it back).

**What one edge is.** One edge per source block, target and kind. A
paragraph that writes the same defined symbol three times has one uses
edge to its definition; another paragraph that uses it has its own edge.
One exception: a block that argues T's claims apart, one case written out
and another declared similar, writes one justifies line onto T per claim,
each naming its claim, the second flagged `analogy`. So too a block's
uses lines onto T that leave from disjoint parts of it.
Both questions an edge is ever asked are about a whole paragraph: when the
definition changes, does this paragraph's reliance still hold; when the
paragraph changes, all its edges are found again together.

**Direct only, and recall first.** An edge records a reliance S's own text
writes: a symbol, a term in its defined sense, or the definition's content
in other words or evaluated ("the dose at the 95th percentile" relies on
the definition of $d^*$), a reference by `\ref`, by name or by position,
a hypothesis it invokes. A reliance reached only
through a chain of definitions and assumptions is the closure's, not an
edge; a reliance the text writes is an edge even where the closure would
reach it anyway. The costs are not symmetric: a missing direct edge breaks
propagation, an indirect or false one only costs a re-check, so write for
recall.

**Every claim's standing.** The support computation needs each claim's
stance, which neither the roles nor the edges give: a claim with about
edges out is not thereby restated. So every claim a block makes has a
standing: justified (a justifies edge comes in), self (its own reasons or
citation), restated (through an about edge to what it says), conjectured,
or unsupported. A block with two claims standing differently has one per
claim, and a claim proved for one case with the rest "going through
analogously" has one per case, each justified by the edge naming it.
A claim is self when its block carries the step that yields it
(arithmetic, an instance, a reduction to a named standard result or to
standard notation) or a citation; asserted bare it
is unsupported, however standard: "classical" or "well known" is not a
citation. The self mark quotes that step or citation, never the claim; a
claim whose block gives no reason has nothing to quote and is unsupported.
A block that argues its own claim by carrying another argument over has
one self mark, flagged `analogy`, quoting the words that say so, and a
uses edge onto the argument it carries or, where that argument is a
cited work's, the citation in the mark's quote; where the block argues
another of its claims itself and carries that over, its unflagged mark
beside names it. The citation is not a
second, unflagged self mark beside it, which would read as an argument
written out.
A claim is restated only in what the target of its about edge itself
states, so that edge points at a block that holds a claim, and, where the
claim's block holds several, names the claim before its target. A
definition, an assumption, a heading or a proof holds none: a law whose
box only defines ("let the rank of a node be …") is stated by the
sentence that says it ("well-ranked nodes are reached first"), the
original, justified, self or unsupported by its own words, and each
further statement of the law restates that sentence. What the sentence
adds to the result it describes (that it matters, what it explains, what
the paper sets out to do with it) is a claim of its own, on its own ⊢
line: unsupported unless the block gives its reason or citation. A
description of a result is restated through the
result, never through another description of it. The unsupported ones are
the gap the graph exists to show; a reviewer may dismiss one (a textbook
fact), the graph never does. In doubt, unsupported: a standing wrong
toward support hides a gap; one wrong toward unsupported costs a reviewer
a glance.

An unsupported claim carries what the paper declares of the argument it
does not give, wherever those words stand: `omitted`, the argument left
out with none named to carry over ("the proof is left out", "left to
the reader", "an easy exercise", "the proof is standard", "in forthcoming
work"), or `promised`, said to be given elsewhere in the paper and given
nowhere in it. The note quotes the declaring words and names their block
where it is another. A declaration stands on `unsupported` only, and
only on the paper's words: a claim the paper says nothing of has none.

**Pervasive set-up is the ambient frame only:** the probability space, the
field's conventions, standard notation; never the paper's own standing
assumptions, which qualify (above). It has no listed
edges; an edit to it reaches every block after it. The paper's own
notation is listed edge by edge, though it is used everywhere: it is where
an edit breaks the most. A block that glosses a common term of the field
in passing is a reminder of the term, like a gloss of standard notation,
not the paper's own definition: no edge targets it, and a block that uses
the term has no edge onto the gloss.

**The question for every block:** what does this block rely on, and of
which kind? Marked `\ref`s are answered by the same question: the label
names the target; classify each, or set it aside as navigation. A
symbol's occurrences are read, never matched: one symbol can name several
things.

## When the cut is wrong

The cut is fixed in the sheet, but it can be wrong: two units in one block,
or one unit over two blocks. Reading what each block relies on is where
that shows, so you may propose a correction with a cut line under the
block, before its done mark:

    ✂ split "<the words where the new block starts>" · "<evidence>" — reason
    ✂ merge <the next block's handle> · "<evidence>" — reason
    ✂ move <the next block's handle> "<the words where it should start>" · "<evidence>" — reason

The words and the evidence are the block's own, verbatim but for
whitespace; the words stand once in the block. A merge or a move names
the block right after it, in the same file; a merge of three blocks is a
merge on each of the first two. The reason says what in the sheet shows it,
by handle. Keep writing the sheet on the cut as shown: the edges, parts
and standings of the block as it is now.

A signal is a difference inside one block:

- **Users:** one part of the block is relied on by some blocks, the rest by
  others.
- **Standing:** the block makes two claims that stand differently, say one
  justified and one unsupported.
- **Role:** a part's content plays another role than the block's, such as
  a definition inside a claim, and it is not a statement whose own form
  joins its hypotheses and its conclusion.
- **Items:** the items of a list are relied on apart, by different blocks.
- **Inside:** an assumption in the block qualifies a definition in the same
  block.

And for a merge: one unit, such as a definition or a display with the
sentence that introduces it, is spread over blocks that nothing relies on
apart.

Never propose one for a block's size or its number of users, or for two
roles alone: a statement may define and claim at once by design. Where a
part edge already says what the block's users rely on and nothing else
differs, leave the cut.

## Flags

You may flag what you see about a block's cut, its roles or its parts and
cannot say by a line above, among the block's lines before its done mark:

    ? role "<the block's own words>" — <what you read there>

The signal is `role` where a span is given a role its content does not
play, `part` where a part is hard to draw, `cut` where the cut seems wrong
but no cut line says it, or no word for anything else. The words are the
block's own, verbatim but for whitespace. A flag changes nothing in the
sheet; a later step reads it.

## The task

Write every block's outgoing edges and every claim's standing, and mark
every block and every group done. A check runs on `edges.md` after each
change you make and shows you what does not read; when you try to finish,
it runs again over the whole sheet, including what is not yet done. Work
until every block and group is done and the check is clean.


## This pass: the paper changed

A first reader wrote the edges and standings of an earlier version of this
paper. The sheet shows the new version and carries every line over to it:
a line stands under the block that holds its words now, and a block whose
text is unchanged keeps its lines and its done mark. Every block of the new
version is shown as it reads now; a block whose text changed also shows its
old text, under its block line, as `⟦was⟧ <the old text>`, once for each
block of the old version it came from. The `⟦was⟧` lines are fixed, like
the text.

**First, the Δ lines.** Under each changed block, write how the edit
changed what it says, one Δ line for each part listed below (one for the
block where none is listed), among the block's lines:

    Δ [<part>] <class> — what changed, in a few words
    Δ <class> — what changed, for the block as a whole

The class is one of eight:

- `unchanged`: it says what it said;
- `reworded`: the words changed, the meaning did not;
- `narrowed` / `widened`: it covers fewer cases, or more;
- `strengthened` / `weakened`: it asks or claims more, or less;
- `removed`: what it said is gone;
- `moved`: it says the same, elsewhere or of something else.

`unchanged` and `reworded` close the change there: nothing that relies on
it is asked again. Every other class carries the change on. Read a block
as the graph reads it, with the blocks that qualify it, each a premise of
what it says: an edit that writes into a block what one of its
qualifiers already assumed changes nothing it says with them.
`unchanged` stops the change, so write it only beyond doubt: for a block
whole, or on a block whose text did not change, quote its evidence in
the note, three words or more of the block or of a block that qualifies
it (one the edit did not change, for a block whose text did not change)
that show the edit does not move it; a block whose text did not change
is never `reworded` (`Δ unchanged — "every measure here is
finite": the cases the edit excludes never arise here`). In doubt, write
the class that carries the change on. Where the
change lies outside the parts listed, add a Δ line for the block as a
whole as well: an edge onto the block whole, onto its group, and its
qualifies edges go by that line, an edge onto a part by the part's.

A part whose words are gone (so marked below) cannot stay in the legend:
delete its legend line, and write `Δ [<part>] removed`; an edge that named
it names another part or the block whole. If the part still stands in
other words, quote them in its legend line instead, and give its class.

**Then the blocks open on condition.** A block the change may reach has
no done mark and a condition under the edge it would come through:
`⟦asked if <block> [<part>]'s Δ is not unchanged or reworded⟧`, or
`⟦meaning moved by rule: … of <block>, if its Δ is not unchanged or
reworded⟧`. Where your Δ lines make a condition true, check that block's
lines against the paper and the rules above, nearest the change first. A
block whose meaning moved by rule writes a Δ line of its own, as a changed
block does. A condition names a block's Δ: a block with no Δ line (one
whose own conditions are all false) closes every condition that names it,
as a Δ line `unchanged` or `reworded` does. Where no condition of a block
is true, leave its lines as they are and mark it done. The condition lines
are fixed.

The other blocks without a done mark are reopened (listed below, with
why): check every line against the paper and the rules above, give a new
block its edges and its standings, and mark each done when it reads. Every
block with a done mark is fixed: leave its lines as they are. Where a line
quoted words this version no longer holds, the hints below say where
something like them stands; quote the paper as it reads now.


### The Δ lines owed

- we-fix-a-field: [K]

### The blocks reopened

- we-fix-a-field: its words changed
- closing-words-of-the: its scope:paper edge: the paper changed

### The blocks open on condition

- def:gain: ⟦meaning moved by rule: qualifies of we-fix-a-field, if its Δ is not unchanged or reworded⟧; ⟦meaning moved by rule: uses [K] of we-fix-a-field, if its Δ is not unchanged or reworded⟧
- by-definition-def:gain-the: ⟦meaning moved by rule: uses [gain] of def:gain, if its Δ is not unchanged or reworded⟧
- next-the-bound-is: ⟦asked if by-definition-def:gain-the's Δ is not unchanged or reworded⟧

# edges.md
# edges

Parts' legend lines and the `Pervasive:` line go here, above the line below.

  we-fix-a-field [K]: "We fix a field of scalars."
  def:gain [gain]: "The gain is the ratio of output to input."
Files: extra.tex, main.tex

---

⟦setting none⟧ main.tex:3:1 (heading)
\section{Setting}

⟦done setting⟧

⟦we-fix-a-field none⟧ main.tex:4:1 (prose)
⟦was⟧ We fix a field of scalars. A second sentence of the setting.
We fix a field of scalars.
⟦…⟧
A second sentence of the setting, edited.

→ qualifies def:gain · "We fix a field of scalars."
⟦meaning moved by rule: qualifies of we-fix-a-field, if its Δ is not unchanged or reworded⟧

⟦def:gain definition⟧ main.tex:7:1 (statement)
\begin{definition}
⟦…⟧
The gain is the ratio of output to input.
\end{definition}

→ uses we-fix-a-field [K] · "The gain is the ratio"
⟦meaning moved by rule: uses [K] of we-fix-a-field, if its Δ is not unchanged or reworded⟧

⟦results none⟧ main.tex:11:1 (heading)
\section{Results}

⟦done results⟧

⟦thm:bound claim⟧ main.tex:12:1 (statement)
\begin{theorem}
⟦…⟧
The output is bounded.
\end{theorem}

⊢ justified · "The output is bounded."
⟦done thm:bound⟧

⟦group group:by-definition-def:gain-the proof⟧

⟦by-definition-def:gain-the justification+claim⟧ main.tex:15:1 (prose)
\begin{proof}
By Definition~\ref{def:gain}, the gain is small
⟦…⟧
.
⟦+ claim⟧ Hence the output is bounded.

→ uses def:gain [gain] ref · "By Definition~\ref{def:gain}"
⟦meaning moved by rule: uses [gain] of def:gain, if its Δ is not unchanged or reworded⟧
→ uses small-means-below-one · "the gain is small" — the footnote says
→ self · "Hence"
⊢ self · "Hence the output is bounded."

⟦small-means-below-one definition⟧ main.tex:16:48 (footnote)
\footnote{Small means below one.}

⟦done small-means-below-one⟧

⟦next-the-bound-is claim⟧ main.tex:19:1 (prose)
Next, the bound is sharp.
\end{proof}

→ uses by-definition-def:gain-the · "Next, the bound is sharp."
⟦asked if by-definition-def:gain-the's Δ is not unchanged or reworded⟧
⊢ unsupported · "Next, the bound is sharp."

⟦end group group:by-definition-def:gain-the⟧

→ justifies thm:bound name · "Hence the output is bounded."
⟦done group:by-definition-def:gain-the⟧

⟦closing-words-of-the none⟧ main.tex:22:1 (prose)
Closing words of the results.

→ about scope:paper · "Closing words of the results."

⟦more none⟧ extra.tex:1:1 (heading)
\section{More}

⟦done more⟧

⟦a-closing-paragraph-in claim⟧ extra.tex:2:1 (prose)
A closing paragraph in another file.

→ about thm:bound · "A closing paragraph"
→ about file:main.tex where-used · "A closing paragraph"
⊢ restated · "A closing paragraph in another file."
⟦done a-closing-paragraph-in⟧
