# The model

What axiomation's graph of a paper is: its nodes, its edges, how a change
travels through it, how it is kept across versions, and the judgments
filed beside it. Each term is defined once, here; § 1 ends with a list
of them. How the parts are built and stored is
[the architecture](architecture.md).

## 1. What the graph is for

A paper's argument graph has every block of the paper as a node and every
direct reliance as an edge. A node exists for three jobs:

1. **It carries a judgment:** is this true, valid, well-posed?
2. **It can be relied on:** a change to it must reach what uses it.
3. **It is a unit of change:** an edit changes it or does not.

**The criterion.** S depends on T when some change to T's content could
change S's status: whether S is true, well-posed, supported, or accurate
in what it says. Every rule below follows from it. A rule is judged by
what it does to two things: the set of blocks an edit reaches, and which
claims read as supported. A question whose answer changes neither is
settled by simplicity.

**Three layers,** sorted by one test: does a build of the text give it,
its recorded answers replayed?

- **The text:** the paper's files, read as the document layer.
- **The graph:** blocks, groups, roles, parts, edges, standings. The
  same recorded answers on the same text give it (§ 6).
- **Judgments:** what a reader concluded about part of the graph: whether
  a proof establishes its claim, whether a citation says what the paper
  says it does. They sit beside the graph (§ 8), with an author and
  evidence; no edge points at one, and the reach rule never follows one.

**Computed, never stored:** support and a claim's support status, a
subject's status, `established`,
the open stale marks, a node's history. They are views over the kept
states, so they always match the kept states.

**Terms**, each defined where its section number points:

- **block**: the argument's unit, the smallest span with one verdict
  (§ 2); a **group**, a run of blocks judged as one; a **part**, the
  part of a block an edge relies on.
- **role**: what a span does: definition, assumption, claim,
  justification, motivation (§ 2).
- **edge**: a direct reliance, `uses`, `about`, `justifies` or
  `qualifies` (§ 3); **J** is a justification, the source of a
  justifies edge; a **self mark** is a block's support of its own claim.
- **standing**: a claim's stance toward support (§ 4); its **handle**
  names it within its block.
- **Δ line**: how an edit changed what a block says (§ 5); the **reach
  rule** is how a change travels; a **stale mark**, the graph's doubt
  about a line after an edit.
- **kept state**: the graph a build, an update or an edit kept, naming
  its version (§ 7); a **sheet** is a stage's lines as a file, which a
  person or an agent edits (§ 6).
- **judgment**, **finding**, **ruling**: what a reader concludes of a
  subject, a problem with a fix, whether the paper owes a claim its
  argument (§ 8); a **subject** is what one judgment is about, a node
  under one aspect and the claim where its block holds several; a
  judgment's **basis** is its subject and **one hop** of the edges its
  aspect relies on; a **session** is one agent reading for one kind of
  question, and a **review session** files judgments and findings.
- **speaks**: a filing speaks, is in force, while it is its author's
  latest on its subject, has not lapsed (it is **live**) and was not
  withdrawn (§ 8); a sheet line's **owner** is the block it is written
  under (§ 5).
- **established**: a claim whose route and everything it rests on hold
  (§ 9).

## 2. Blocks, groups and parts

**The document layer** is what the paper's files say, read
deterministically: the files actually included, in reading order (what
compiles, not every file in the directory); sections; environments;
labels, references and citations; footnotes; floats; macros; comments;
quotations of cited works. Comments are recorded, not rendered.

**A block** is the argument's unit: the smallest span with one verdict and
one set of reliances. The cutter proposes blocks from what the document
delimits (a paragraph, an environment's statement, a proof, a display, a
list item, a footnote, a float); agents correct the cut, because the
document's boundaries are evidence and not the argument's. The cutter
guarantees coverage: every rendered character is owned by exactly one
block. A block may lie inside another and owns only its own text (a
footnote inside its paragraph), and one line may hold two blocks.

The cut's rules:

- A display belongs to the block of the sentence it is set in, even
  across a blank line; a display outside any sentence is a block of its
  own. A paragraph that runs into an environment keeps the environment
  apart.
- A paragraph is split where the argument starts a new unit: a long block
  at its major steps, any block where two units the argument keeps apart
  meet. An author's paragraph of a few short steps stays whole; its grain
  is left to parts.
- A proof whose steps rely on different things is a group of step blocks,
  split where what a step relies on changes; a short proof that relies on
  one thing stays one block. A step heading joins its first paragraph.
- Where the parts of a block are two verdicts on different grounds, each
  with users of its own, the cut splits the block.

A block holds its extent, the characters it owns and their hash, its
form, its environment and what the environment declares it to be (a
theorem, a lemma, a remark), its roles with their segments, and the block
it lies within; its labels and section are read from the document
layer. Its id is `<file>:<line>:<column>` of its
first owned character, so it is a position within one cut; the node id
(§ 7) is what lasts.

**A block's form** is how it is typeset, never what it does:

<!-- closed set: axiomation.cutter.model.Form -->
| form | |
|---|---|
| `title` | the paper's title |
| `heading` | a section heading |
| `prose` | a paragraph |
| `display` | a displayed formula outside any sentence |
| `item` | a list item |
| `statement` | a theorem-like environment's statement |
| `float` | a figure or table |
| `footnote` | a footnote |
| `code` | a code listing |
| `environment` | any other environment |

**A role** is what a span does:

<!-- closed set: axiomation.cutter.model.Role -->
| role | what it is | its verdict | support |
|---|---|---|---|
| `definition` | fixes the meaning of a symbol, term, object or procedure | is it well-posed? | needs none |
| `assumption` | restricts where claims hold, scope statements included | is it acceptable, and in scope? | needs none; a premise of every result that rests on it |
| `claim` | is asserted true | is it true, is it accurate? | needs it, by its standing (§ 4) |
| `justification` | argues for a node | does it establish what it argues for? | gives it |
| `motivation` | argues that something is worth doing or appropriate | is the reason sound and relevant? | gives none |

Roles are intrinsic: read from the span alone, never set by an edge.
Almost no text is commentary: a roadmap is a claim about the paper, and
a limitation of the paper's own results is a claim. A qualifier inside a
claim ("under mild conditions") is its content. Motivation confers no
support, so a claim backed only by a motivation never reads as supported;
facts stated inside a motivation are claims.

**Segments.** Every block is partitioned into segments by the role each
plays: one segment for a single-role block, a boundary wherever the role
changes in a mixed one ("We assume each weight positive, since it counts
observations": an assumption, then a motivation). A span that
plays no role (a transition, signposting) is a segment with no role. A
block's roles are its segments' roles; a block whose every span plays
none has no role. Being relied on is never a reason to cut.

**A group** is an ordered run of two or more blocks judged as one: a proof
of several paragraphs, a proof written as a section, a derivation, a run
of steps. A one-block proof is a block, not a group. A group starts at its
first block of argument, never at a heading, so renaming a heading never
touches a proof's verdict. A figure or table with the text that reads it
is not a group: two verdicts, joined by a uses edge.

<!-- closed set: axiomation.cutter.model.GroupKind -->
| kind | |
|---|---|
| `proof` | a proof environment's blocks |
| `proof-section` | a proof written as a section |
| `derivation` | a derivation over several blocks |
| `step-run` | a run of steps |
| `step` | one step of several blocks |

A group's role is `justification` for a `proof`, `proof-section` or
`derivation`, and none for a `step` or `step-run`; its blocks keep their
own. What a group justifies is never the group's: it is a justifies edge
(§ 3).

**A part** is the part of a block that an edge relies
on: an anchor (the text's own marker, "(ii)" or a label, where there is
one, else a verbatim excerpt unique in the block) with its role. Parts
exist only where an edge needs one, so nothing below the block is cut
exhaustively. A part takes its segment's role, but never `claim`: a claim
is named by its standing's handle (§ 4), and a part an edge relies on
inside a claim is content. A formula that later blocks write as notation
or define something by ("satisfying the conclusion of Theorem 1") is a
definition part, so a change to it moves its users' meaning (§ 5); a
hypothesis is an assumption part, a step a justification part.

## 3. Edges

**Direct only, and recall first.** An edge records a reliance S's own text
writes: a symbol, a term in its defined sense, a definition's content in
other words or evaluated ("the dose at the 95th percentile" relies on the
definition of the dose), a reference by `\ref`, by name or by position, a
hypothesis it invokes. A reliance reached only through a chain of
definitions and assumptions is the reach rule's (§ 5), not an edge; a
reliance the text writes is an edge even where the rule would reach it
anyway. The costs are not symmetric: a missing edge breaks propagation,
an extra one only costs a re-check, so edges are found for recall.

**Four kinds, told apart by how they propagate:**

<!-- closed set: axiomation.records.EdgeKind -->
| kind | reads | when T changes | when S changes | support |
|---|---|---|---|---|
| `uses` | S relies on T's content | onto definition or assumption content, S's meaning changes without asking and the change goes on; onto a claim, S is asked whether its reliance holds | S's edges are found again | travels: S rests on T |
| `about` | S describes T | S is asked whether its description is still accurate | the same, from S's side | does not travel; a restated claim takes its standing through it |
| `justifies` | J, a justification, argues for T | J is asked whether the paper still offers it for T | T's support is recomputed | defines it |
| `qualifies` | assumption A constrains definition D | A is asked whether it still constrains D | a change to A is a change to D's meaning, carried on to D's users | A is a premise of every result resting on D |

**uses** is a definition use, a hypothesis or a result; generalizing and
instantiating are uses. The line between uses and about is object against
text: S that asserts something of the defined object or works with T's
quantity, instance or step ("it is convex, it vanishes at 0") uses T; S
that says something of the text or the argument (what a block says,
shows, is for) is about it. Onto a claim the line decides support: uses
carries it, about does not.

- A symbol's definition site is the block that writes it in a defining
  display or sentence. A block that glosses symbols in words ("μ is the
  common mean of the summands") defines their names: a part with definition
  content that uses the display. A block that writes the symbol uses the
  display; one that writes the name uses the gloss.
- A symbol abbreviated from a defined object keeps its uses edge to the
  definition; the edge to the block that introduces the abbreviation
  stands where the abbreviation fixes something of its own.
- A roadmap that numbers steps defines their names, so "the second step"
  elsewhere is a uses edge onto that numbering, beside any about edge to
  the step itself.
- Where S relies on definition content inside a claim block, the edge
  names that part; an edge onto a claim block as a whole is only asked.
- A step of a proof that relies on its claim's hypotheses ("by the
  hypothesis of the lemma") uses the claim's block: a context edge,
  which relies on the hypotheses of the claim the argument serves, never
  on its result (§ 9). A step that relies on another claim of that
  block, a result, names it.
- A claim's statement that takes a conjecture as its hypothesis
  ("assuming k", "if k", "under k", "conditional on k") uses it, flagged
  `assumed`: it asserts the claim on that condition, never the
  conjecture, and the claim is established under it (§ 9). The flag runs
  from the statement's own block, never from a step of an argument (a
  block of another claim's context, § 9), onto `conjectured` claims only,
  named where the target holds several, never content; on a block of
  several claims it names at its source end those it is a hypothesis for
  (`→ uses [<claim>] k assumed`). Its evidence is the statement's words
  that take k. "By k, y" asserts y on k's strength and is unflagged, as
  is "assuming T" of a claim the paper asserts, which relies on T as "by
  T" does. The flag changes what is established, never what an edit
  reaches (§ 5).

**about** is a gloss, a preview, a summary, a roadmap, a proof summary
outside its proof, a heading that names content, a remark on the argument
("Lemma 3 is crucial in the proof of our main result"); most `\ref`s in
prose are this. Descriptions have their edges whatever their role.

- A restatement is about what it restates, from the less authoritative
  to the more (a gloss to its definition, a preview to the full
  statement), never about a sibling restatement of the same thing: an
  edit to the shared target re-checks both. Whether two restatements
  agree is `accurate` on each.
- An assumption about another assumption restates it: the same premise
  (§ 10). One about several restates them together: declaring it declares
  each, declaring one of them declares that one alone. One that narrows or
  adds to it, written after it (a countable space for a general one, a
  further condition), uses it and is a premise of its own. One that
  widens it, written after it (an appendix's general space over the
  text's countable one), is about it, flagged `generalizes`, and is a
  premise of its own: the narrower one is a case of it. The flag runs
  from an assumption onto an assumption or a definition's content (the
  special case may sit inside a definition), never beside `where-used`;
  its evidence is the wider block's own words that write the narrower
  one's object in general form. Where the wider one also writes the
  narrower one's symbols, that uses edge stands, for the reliance and the
  reach (§ 5), and carries no premise (§ 10). A looser statement of an
  assumption is a gloss (about it, the same premise) or a wider premise,
  never a use. A block holding a claim beside its assumption is about
  the claim.
- A description of a proof as a whole targets the proof's group; one of a
  step or device targets the block that holds it.
- A claim over a span of the text, by its form: one that names what it
  describes ("the two remarks above") is about those blocks; one that says
  where T is or is not used ("assumption A is needed only for Theorem 4",
  "never mentioned again") is about T, flagged `where-used`, and is also
  re-checked when T's incoming uses edges change; one about the whole text
  with no node to hang on ("the paper never uses compactness") is about
  the paper, `scope:paper`, re-checked on any edit.
- An environment cut into several blocks with no group is described block
  by block.
- A contradiction is an about edge whose check fails, a judgment's
  verdict, never a fifth kind.

**justifies** runs from a justification to any node: a proof, a
derivation, an example offered as evidence, a citation standing in for a
proof, a claim that discharges a provisional assumption. Its source is a
block, or a group where the justification is one run. Where a group's
line justifies T, none of its members (a block of it, or a group nested
in it) justifies T as well: the group's line carries theirs ([the architecture](architecture.md) § 9). A
line from one member onto another is the group's own step. "The justification
of T" is whatever has a justifies edge to T. An assumption with a
justifies edge coming in is discharged, but only for the results whose
justification holds the discharging claim; for the others it stays an
open premise.

**How completely a justification argues** is read from its own words,
and only from them: one a reader finds thin but which does not present
itself as partial argues in full, its gaps a judgment's (`valid`, § 8),
so the mark is the text's, not the reader's. A justifies edge
or a self mark carries at most one of:

<!-- closed set: axiomation.records.Partial -->
| partial | its own words say |
|---|---|
| `sketch` | the argument as written stops short of its target: an outline, a heuristic, or a special case written with the rest declared to follow |
| `analogy` | the argument is another one's, carried over, none of it written |

**A sketch** says so in its own words ("Proof sketch", "we only outline",
"We give the case k = 2; the general case follows the same steps."). A
special case taken without such a sentence argues in full, the
reduction a judgment's. Where the rest is the target at its full
generality, the line is a sketch and the written case is named by uses
edges; where it is a case the target states apart, that case is a claim
of the target with its own standing, and the edge naming it an
`analogy`.

**An analogy** ("similar to the proofs of Theorems 1 and 2", "the proof
follows [12, Thm 2]") names what it carries over by a uses edge from its
block onto an argument outside it: a group, a block with a justification
role, a justifies edge's source or a block carrying a self mark (the
proof, never the result it argues; for a closing "the other cases are
similar", the group it closes). The edge names no part, or a claim or
justification one, since an edge naming only what a part defines or
assumes relies on that, not on an argument. So a change to that argument
reaches the claim and lapses the readings on the line (§ 8). A cited
work's argument is named by the citation in the line's own quote; one
its own source writes out for another claim (one block arguing one case
and declaring the other similar), by that claim's unflagged line beside
it, onto the same target (for a self mark, its block's). The flag never
stands in for the uses edges its words write.

A sentence that names no argument to carry over ("the proof is
standard") is no analogy: the claim stands `unsupported`, `omitted`
(§ 4). One that applies a cited result ("by [12, Thm 2] the claim holds")
is a citation justification, in full. How much of a claim a justification
covers is the standing its edge names, the case it argues (§ 4). A
justification's kind is not recorded beyond this: a citation one is a
self mark or a J that cites, a rationale one a justifies edge onto a
definition or an assumption.

**qualifies** runs from a block with an assumption role to the definition,
or the parts of one, it constrains: a standing assumption ("we assume every
weight wᵢ to be positive", of the model's wᵢ), a scope restriction ("we
treat only one-sided deviations"). It names every part of its target the
assumption constrains. It stands only where no user of the definition is
stated without the assumption; where results state their assumptions
themselves ("Under A, …"), each reliance is its own uses edge. A uses edge
between the same blocks stands beside it where the assumption writes the
definition's symbols. A scope restriction is an assumption, never a claim
about the text.

**Targets.** An edge's source is the smallest block that writes the
reliance (a group only for justifies), so a long proof re-asks only its
changed paragraphs. Its target is one of:

<!-- closed set: axiomation.records.TargetKind -->
| target | |
|---|---|
| `block` | a block, or the parts of one it names by the target's own words, or the standings of one it names by their handles (§ 4) |
| `group` | a group, where S relies on a whole argument ("the same argument as in the proof of Lemma 2") |
| `file` | a file of the paper: a figure's image, a script that computes a value the text quotes |
| `scope` | the paper as a whole (`scope:paper`), for a claim about the text that no finite set of edges keeps correct |

**Not an edge:** navigation ("its proof is in the appendix", "see Section
5"), which depends on where T is, not what it says; a cited outside work,
which is a judgment's anchor (§ 8), never a target.

**What one edge is.** One edge per source block, target and kind: a
paragraph that writes μₜ three times has one uses edge to μₜ's part, with
the three places as evidence excerpts. A justification that argues the
target's claims apart, one case written out and another declared similar,
has one justifies line per claim it argues, each naming its claims, none
shared: each claim's completeness is its own line's (§ 4). An edge records how the text points
at its target:

<!-- closed set: axiomation.records.Mark -->
| mark | |
|---|---|
| `ref` | a `\ref` |
| `name` | a name in words ("by Lemma 3") |
| `position` | a position ("the lemma above"), re-asked when its target moves |

and its author: the answer that first wrote its line, by a model, an
agent or a person.

**No edge inside a block.** A block whose claim carries its own reason or
citation supports it itself, recorded as a **self mark** on the block,
so a missed outward edge never reads as self-support. Cycles between
blocks are allowed (notation used before its definition, which uses it
back); a re-check runs each edge once.

**The ambient frame** is the pervasive set-up only: the probability space,
the field's conventions, standard notation (φ, Φ, N(·,·)). It has no listed
edges, and an edit to it reaches every block after it. The paper's own
notation is listed edge by edge, though it is used everywhere: that is
where an edit breaks the most. The paper's own standing assumption or
scope restriction is never the ambient frame, however it is worded.

## 4. Standings

Every claim a block makes has a **standing**, its stance toward support,
which neither its role nor its edges give: a claim with about edges out is
not thereby restated.

<!-- closed set: axiomation.records.Status -->
| standing | |
|---|---|
| `justified` | a justifies edge comes in |
| `self` | its block carries its reason or a citation (a self mark) |
| `restated` | it restates another claim, through an about edge to it that names it |
| `conjectured` | the paper does not assert it |
| `unsupported` | asserted, and nothing supports it |

A block with two claims standing differently has one standing per claim,
each naming its claim's words.

A line and its claim's standing are one fact: a line an edit adds that
argues a claim standing `unsupported` stands it `justified`, `self` or
`restated` in the same edit, whoever adds it, unless the same edit sets
that claim's standing itself. A justifies edge onto the claim's block
and the block's self mark argue the claim they name, or the block's one
claim where they name none. The block's about edge onto a claim argues
the claim its source end names; an about edge is mostly a description,
so one naming none restates the block's one claim only where its writer
writes it as a restatement (the support search, the argument session,
§ 8). A line naming no claim of a block of several argues none, and a
line given back in place of one the edit takes off (the same source,
kind and target) argues only what the line taken off did not: a line
rewritten or narrowed lends no new support.

A `restated` claim borrows its support, so its block has an about edge
that names both claims: the restated one at its source end (none needed
where the block holds one claim), and at its target a claim the edge
relies on (below). A definition, an assumption, a heading, a group, a
file and the paper hold none. So a law whose box only defines is stated
by the sentence that says it outside the box, the original, and each
further statement of the law restates that sentence. The edges sheet's
check refuses any other `restated` line, whoever writes it; a review's
about line may add or correct the edge for a claim it judges.

A claim is `self` when its block carries the step that yields it
(arithmetic, an instance, a reduction to a named standard result) or a
citation; it is `unsupported` when asserted bare,
however standard: "classical" or "well known" is not a citation. The
test is whether the block's own words give the step: an instance of a
result of the paper's own, written out in the block, is `self`, never a
claim for a ruling (§ 8).

The unsupported claims are the gap the graph exists to show. A reader
may judge one true, or rule that the paper owes it no argument (a
textbook fact, § 8); the graph never drops it.

An `unsupported` claim may carry what the paper declares of the argument
it does not give, wherever the declaring words stand ("we relegate the
proofs of Theorems 1 and 2 to the Appendix" may sit in another section):
its note quotes them and names their block.

<!-- closed set: axiomation.records.Declared -->
| declaration | the paper's words |
|---|---|
| `omitted` | the argument is left out, none named to carry over: "omitted", "left to the reader", "it is not difficult to show", "standard", "in a forthcoming paper" |
| `promised` | the argument is said to be given elsewhere in the paper, and nowhere in it is it |

A declaration stands on `unsupported` only: nothing in the paper argues
the claim, and the declaration says only whether the paper admits it. A
promise to give a proof is navigation, never an edge (§ 3). An assumption
to be discharged later ("will be proven later") holds a claim, justified
by what discharges it, else `unsupported promised`.

A line **names** a standing by its **handle**: the name its ⊢ line on
the edges sheet gives it, else one minted from the claim's first words,
unique in its block. No line must name one: which claim a line argues is
read from the text, and any reader that writes the line (the edges
stage, an update, a person or an agent, a review) may name it or correct
the name. The export holds each named standing's words, so a reader
looks the claim up, never compares a writer's quote with it. What each
end names:

- a justifies, uses or about edge names standings of its target; a self
  mark, standings of its own block; an about edge's source end, the
  restating standings of its own block;
- a uses or about edge relies on the standings it names; where it names
  none, on none if its parts are all of another role than `claim` (it
  relies on content), else on every claim of its target;
- a justifies edge **covers** the standings it names, else every
  `justified` claim of its target; a self mark covers the `self`
  standings it names, else every one of its block;
- an about edge restates for the `restated` standings its source end
  names, else, as a fallback (below) where its block holds several
  claims, for every one of its block. An about line that restates none
  of its block's claims says so at its source end, `→ about [-] T`: it
  restates for none, so it is no fallback.

A line's end that names no claim where the block there holds two or
more is a **fallback**: which claim it means is unknown, so it reads
toward the alarm. Where the block holds one claim, an end naming none
means that claim. A fallback:

- never declares (§ 10);
- at a target end asks every claim there, so it only adds to what a
  claim rests on;
- at the source end of an about edge may be another claim's, so it never
  lends a restated claim support;
- counts for what a J reaches.

A `justified` or `self` claim's **completeness** is computed, never
stored, from the lines that cover it, a line `given` where it carries no
partial (§ 3): the fullest of those that cover it alone, else the least
of those that cover it, a line that names the claim covering it as
surely as one alone. Which claim an ambiguous line argues is unknown,
so it reads toward the alarm, never toward support. So a citation naming
the argument an analogy carries over is that analogy's own self mark,
flagged, never a second one unflagged, which would read `given`.

<!-- closed set: axiomation.established.Completeness -->
| completeness | |
|---|---|
| `given` | the line it is read from argues it written out |
| `sketch` | the line it is read from is a sketch |
| `analogy` | the line it is read from carries another argument over |

A standing written on a block whose roles hold no claim adds a claim
part to its roles, unless a person pinned them ([the architecture](architecture.md) § 9).

## 5. Change

**Two things travel, on two channels:**

- **Meaning** travels along edges. A meaning can change without its text
  changing: when a definition changes, every statement that uses it means
  something else, though it reads the same. Through definitions and
  assumptions it travels by rule; elsewhere a reader judges it.
- **Support** is computed where the graph decides it: a justification
  deleted, or a justifies edge retracted, leaves its target without it.
  Whether a changed justification still establishes its claim is
  judgment, surfaced as a stale mark (below).

**Identity across versions.** An update pairs each block of the new
version with the old one's, by the text map, then label, hash, key,
joined words, word ratio and form, and classes each:

<!-- closed set: axiomation.identity.BlockClass -->
| class | |
|---|---|
| `same` | the same text in the same place |
| `moved` | the same hash, in another place |
| `math` | the same words and labels; the mathematics differs |
| `markup` | the same words, labels and mathematics; only the markup differs, or a statement's meaning moved within its role (a lemma become a theorem) |
| `macro` | the same hash, but a macro it uses changed |
| `changed` | its words changed, or it reads a vocabulary map entry that changed, or its statement's meaning moved to another role or to or from a conjecture |
| `split` | one old block became several |
| `merged` | several old blocks became one |
| `added` | new |
| `deleted` | gone |

A vocabulary map entry that changed (what the paper's own name for an
environment or a reference is read as), or a statement's environment
declared otherwise, is a change of the text that reads it ([the architecture](architecture.md) § 9).

**A cut correction carries** onto the new version wherever the words at
its boundary still stand, and **a group** while its run stands; one
that cannot lapses, and the update lists it with its words and why
([the architecture](architecture.md) § 9).

**The Δ line.** For each changed block, the update records how the edit
changed what it says, per part it holds or for the block whole, with a
note ("assumption scope narrowed to bounded weights"):

<!-- closed set: axiomation.records.ImpactClass -->
| class | |
|---|---|
| `unchanged` | closes the change here |
| `reworded` | closes the change here: a corrected typo is reworded |
| `narrowed` | carries on |
| `widened` | carries on |
| `strengthened` | carries on |
| `weakened` | carries on |
| `removed` | carries on; a part whose words the edit removed takes it |
| `moved` | carries on |

The class is a reader's judgment, of the block as the graph reads it:
with the blocks that qualify it, each a premise (§ 3). An edit that
writes into a block what a qualifier already assumed is `unchanged`. A
deleted block's parts take a computed `removed`.

Only a change beyond doubt, as the agent reads it, stops: a Δ `unchanged` for a block whole, or
on a block whose text did not change, quotes in its note three words or
more of the block or of a block that qualifies it (not one the edit
changed, for a block whose text did not change) that show the edit does
not move it; a block whose text did not change is never `reworded`; in
doubt, the class that carries.

**The reach rule.** From a block whose meaning changed:

- **moved without asking, and the walk goes on:** the source of a uses
  edge onto its definition or assumption content (a named part's role; a
  whole block holding such content, unless the edge names another part),
  and the target of a qualifies edge from it;
- **asked, and the chain stops there:** the source of a uses edge onto
  any other content (a claim's); of an about edge, including onto a block the rule moved; of a
  justifies edge; of a qualifies edge onto it; of an edge onto a group
  the block lies in;
- **support recomputed:** what the block, or a group it lies in,
  justifies.

On the edited block alone, also asked: every `where-used` edge onto a
block it uses, and every `scope:paper` edge. An edit to the ambient frame
moves every block after it. A statement's inherited hypotheses ("Under
the hypotheses of Corollary 1") are assumption content, so the walk
passes through them; through a claim's conclusion it stops after one step.

In an update the walk is gated by the Δ lines: an edge carries the change
only where the Δ of the part it names is neither `unchanged` nor
`reworded` (for an edge onto the whole block, its whole Δ, else any of
its parts'); a part with no Δ of its own counts as changed. A block the
rule moves goes on by its own Δ, else wholly. How an update reads what the rule moves is [the architecture](architecture.md) § 9. A markup edit closes by rule;
a markup, maths or macro edit keeps the block's roles.

*An example.* "We assume every weight wᵢ to be positive" qualifies the
model's definition, and three blocks use the model. The sentence becomes
"We assume every weight wᵢ to lie in (0, 1].", its Δ `narrowed`. The definition's
meaning moves (qualifies), each of the three users' meaning moves without
asking (uses onto definition content), and the walk goes on from each:
a block using one of them as a definition moves too, one relying on a
claim among them is asked, and the walk stops there. Had the Δ been
`reworded`, nothing past the edited block would be reached.

An edit's **reach** is the set of blocks the rule reaches, each once.
Most edits reach a few blocks; an edit to the paper's set-up (its model,
its standing assumptions) reaches many. `axiomation reach` shows a
block's reach at a kept state.

**Stale marks.** An update writes the changed blocks' lines afresh and
records the owners the rule would ask, each with its cause (the class,
the edge it came through, and the edited block the change began at). A
**stale mark** is the graph's doubt about one of those lines: an edge
whose far end changed, its owner's line standing until an answer reads it
on the new state. A line is asked where it can be wrong:

- a line no standing rests on (`uses`, `qualifies`, an `about` line, an
  edge onto a group, the edited block's `where-used` and `scope:paper`
  sources) where its far end's **words** changed (identity's class
  changes the text, or its words differ, by more than markup: the
  lapse's `text`, § 8); a far end moved by the rule alone asks nothing,
  since its effect reaches the readings by the lapse;
- a line a standing rests on (a `justifies` line; an `about` line out of
  a block holding a `restated` standing onto one holding a claim) where
  its far end has a **Δ of its own**, a ring Δ included, or its words
  changed: a meaning that moved behind unchanged words can make a
  standing overstate, and nothing lapses on a standing;
- a `uses` line no subject's one hop holds that a review session judges
  (§ 8) where its far end has a Δ or the rule moved it: no reading can
  lapse in its place;
- always, a line the state no longer holds (a retracted or lost edge) and
  a standing left resting on nothing.

A mark clears when an answer reads its line on the new state: the next
update reopening its owner, an edit of the sheet (which shows the marks)
that keeps or changes one of the owner's lines, a **reread** (a filing
that the owner's lines stand against what changed, with its reason), or
a `verified` reading made since whose subject holds the line as its own.
What the rule moved unread is one item per edit (`carried-unread`,
§ 10). An `about` mark on a line no standing rests on, and not flagged
`generalizes`, is a **description**: no session judges `accurate`, so
it is in no gate (§ 10) and off the established walk (§ 9). Marks are
computed, never stored; how each is cleared, and the reread's question,
are [the architecture](architecture.md) § 9.

An edge onto a deleted block is asked again of its source; one the
source does not draw again marks it stale until a person or an agent,
shown the lost line, reads it ([the architecture](architecture.md) § 9).

**A Δ corrected.** A person or an agent may correct a Δ class on an
update's state (`axiomation change --delta`); the marks are then given
again from the corrected classes, and judgments lapse by it.

## 6. Build and update are one flow

The graph is a function of the text and the recorded answers. Every
agent step is a question keyed by the hash of exactly what it was sent,
answered once; the first valid answer for a key is the one the graph
uses. A first build is an update from an empty store. An update asks only
what its edit touched; a replay with the same records gives the same
graph, byte for byte.

Code reads the document, proposes the cut, the groups and the
candidate edges, pairs versions, walks the reach rule, marks stale and
computes support; agents correct the cut, decide the groups' runs, and
write every block's roles, edges and standings, and each changed
block's Δ. The steps, the flags they raise on the cut, and the pins are
[the architecture](architecture.md) § 9.

**A person's or an agent's edit** is an answer to a step's own file on a
kept state (`axiomation sheet`, `edit`, `change`), applied by that step's
type rule: a cut edit asks again the roles of what it changed and the
edges it touches; a roles edit, the justifies and qualifies edges of and
onto the block, its standing, self mark and parts; a groups edit, its
justifies edge and the about edges onto it; an edges edit, nothing, and
the readings whose one hop it changes lapse (§ 8). Whatever reopens a
group reopens each member whose justifies line leaves it, since the
group's line carries theirs (§ 3). An
edit that asks nothing, or whose questions are answered, is a new state
at once; one whose questions go unanswered makes no state and stays
pending, and while a version holds a pending edit no other answer is
given on it and no update starts from it.

**Pins.** What a person's edit changed is pinned: no later model answer or
correction changes it, and only a person's later answer does. An agent's
edit pins nothing and may not change a person's pin. A person may also
keep a line as it stands, by the keep mark `= ` before it. What a pin
holds and when it lapses is [the architecture](architecture.md) § 9.

## 7. Kept states and node ids

Every whole `build` or `update`, and every edit, is kept as a **state** in
the paper's store, naming its version (the document's hash), its parent
and what made it:

<!-- closed set: axiomation.versions.Command -->
| command | |
|---|---|
| `build` | a first build |
| `update` | a new version from an earlier state |
| `edit` | a person's or an agent's edit, a new state of the same version |

A state keeps the files the run read and the export it wrote. Nothing is
rewritten: a version's graph is its kept export, read back byte for byte,
and a re-run that adds nothing keeps nothing. An update whose text is its
parent's but whose files are not (a script changed alone) is a state of
its own.

**A version is the text, not the parser.** Its id is the hash of the
files the parser read, each its path and its text, made when a state
first keeps the text. An edit keeps its version; how a newer axiomation
reads a kept state's files differently is [the architecture](architecture.md) § 9. What the author
adds:

- *a file the paper includes* (an `\input`, a package shipped beside it,
  a new `header-includes` entry): the paper is edited, and the update
  carries the graph over; a body file's blocks are new, a preamble adds
  none;
- *a file it does not include* (a note, a draft, a figure): no edit.
  Every file at the paper's top level and every `.tex` or `.md` file
  below it is kept, so the next update keeps it as a state of its own,
  the export carried and nothing asked; an edge may name a top-level
  file, but no `.tex` or `.md` file the paper does not include;
- *a code file* (a script under the paper's code directories, or at its
  top level): the same, kept by its key. A judgment's basis names the
  code files it cites by their content (§ 8), so a script added lapses
  nothing, and one changed lapses what cites it.

**Node ids.** Each block of a state has a node id, each group a group
id: `n` or `g` and ten random characters (`n4k7qz2mab`), drawn when the
node begins, none the store holds, then stored and never derived again. A person
reads an id by its shortest prefix unique at its state, five characters
at least (`n4k7q`), and names a node by any unique prefix. A node
continues:

- **within a version** (an edit, a run's corrections): a block that
  starts where a block started continues its node; the others continue by
  most shared characters. A split's first piece and a merge's result keep
  the first block's node; a boundary moved between two blocks keeps both;
- **across versions:** a block continues the one identity pairs it with;
  a split continues in its first piece, a merge in its first block, in
  reading order. An added block, and a rewrite identity reads as deleted
  and added, are new nodes;
- **a group** continues the group sharing most of its blocks' nodes.

**Handles.** Each node and group node also has a **handle**, the name a
sheet, a read and a person give it: when the node begins, its first
label, else a slug of its first four words (`fix-t-0-and`). A
node keeps its handle while it lives, whatever its words become: a
handle is a name, not a description, and it follows the node by the
rule above, through every cut correction and every update. So an edit to
a block's opening words leaves its handle, and a name it bore still
names it where no other node bears it; aliases and `history`'s notation
are [the architecture](architecture.md) § 9. A claim's handle within its
block is § 4's.

**Lines across states.** A line of a later state is the same line when
its kind and words are the same and its nodes descend from the earlier
ones ([the architecture](architecture.md) § 9).

*What a split keeps.* A block split in two keeps its node id on the first
piece; the second is a new node. A line on the old block that now reads on
one piece is carried there, neither ended nor begun; a line on the whole
old block, whose words now lie in both pieces, is listed to look at,
beside what stands in its place. Judgments on the old block lapse (its
text changed), and so do those one hop from it.

**Under later rules.** A kept state is read by the installed version from its
export, never derived again; what the installed version's checks find in it is named
(`later-rule`, § 10), never forced, and an answer on it (an edit, the
re-ask an edit reopens, an update's session) is refused only for a
problem it adds ([the architecture](architecture.md) § 9).

## 8. Judgments and findings

**A judgment** is a reader's conclusion on a subject, filed on a kept
state by a person, an agent or a review session (`axiomation judge`,
`review`). It names its aspect, verdict, depth, evidence and author, and
records its **basis**: its subject and one hop of the edges its aspect
relies on. Each node of a hop that relies on edges is read with the
qualifies edges onto it and their sources, each a premise of what rests
on it (§ 3); an aspect whose basis names no edge (`cited`,
`presentation`, `significance`, `novelty`, `literature`) takes none. A review session's reading was shown the edge lines it writes: its
basis is the one hop as its own lines correct the state, and it records
what those lines took away. A review's line onto a target where its block holds a line of the same
kind replaces that line ([the architecture](architecture.md) § 9).

<!-- closed set: axiomation.records.Aspect -->
| aspect | asks | subject | basis beside the subject |
|---|---|---|---|
| `valid` | does J establish T? | a justifies edge: J and a claim of T it covers (§ 4) | T's claim, J's justifies lines onto T, J's uses edges and their targets |
| `true` | does the claim hold? | a claim: its block and its words | its uses edges and their targets |
| `accurate` | does S rightly say what it describes? For `generalizes`, whether the target is a case of S: what the target states (a definition's part with the assumptions that qualify it) implies each condition S's own words assume; S's reasons, and the definitions the symbols it writes rest on, are not asked, each such reliance its own edge | a description | its about edges and their targets; for `where-used`, the target's incoming uses edges; for `scope:paper`, the paper |
| `cited` | does the cited work say it? | a citation site (a block and the cited words) and the work it cites | the pages read, stamped |
| `computed` | does the script compute what the block says, and is the printed value its output? As a finding's aspect also: does a result the paper reports as computed show the script that computes it? | a block and a code file it uses | the block, its uses edge to the file, the file's content |
| `well-posed` | is the definition or statement well-posed? | a finding's aspect only | its uses edges and their targets |
| `presentation` | can it be read as written? | a finding's aspect only | its anchors' text |
| `significance` | does the result say anything, and more than a standard one? | a finding's aspect only | its anchors' text |
| `novelty` | is the result known? | a finding's aspect only | its anchors' text |
| `literature` | is the paper placed in its literature: the results of its field it uses, and the work beside it, cited? | a finding's aspect only | its anchors' text |

"Does the reliance hold" is no aspect: its graph half is the uses edge
kept or retracted, its judgment half `true` or `valid`. Support is
computed, so it is no aspect either. Nor is which claim J argues, or how
completely: a reader who finds a justifies line or a self mark wrong on
either corrects the line, as an edit (§ 4), and a verdict speaks only of
the argument read, `not-shown` meaning offered and not shown. A `true` or
a `valid` that relies on a conjecture verifies only the conditional the
claim's words state: where its statement takes the conjecture as a
hypothesis, what follows from it; where its words assert the conclusion
on the conjecture's strength ("by k, y"), `not-shown` unless the reader
shows the conjecture. So a wrong `assumed` flag (§ 3) is caught on the
claim's own reading. What a `true` asks is read by the claim's place, as
a ruling's question is (below): of a statement or a step, whether it
holds as stated; of a `restated` claim, whether it says no more than the
claim it restates, whose own `true` asks whether that holds; of a gloss
(a remark, an informal reading of the formal part), whether it misleads
a reader of the formal part, never whether it is as exact.

<!-- closed set: axiomation.records.Verdict -->
| verdict | |
|---|---|
| `verified` | shown |
| `not-shown` | evidence is offered and does not show it |
| `contradicted` | shown false |
| `inconclusive` | could not tell, or a read failed |

<!-- closed set: axiomation.records.Depth -->
| depth | |
|---|---|
| `informal` | read |
| `computational` | computed |
| `formal` | proved formally |

Depth is recorded and shown, never ranked.

**Evidence outside the paper.** A `cited` judgment names its work, one
its block cites, and the pages it read; a `valid` or `true` judgment may
rest on pages of any work of the paper's bibliography, one it cites
wherever it cites it or another entry, and a ruling too (below). Each
page is read through the sources and stamped (key, page index, read
status):

<!-- closed set: axiomation.evidence.ReadStatus -->
| read status | |
|---|---|
| `ok` | the version of record, read |
| `image` | a page of the version of record with no usable text (a scan with no text layer, a failed OCR, text too poor to read), read as its image: evidence, which a queue session names only where it read the image; a quote from it is the reader's, as no text holds it to match |
| `transient` | a copy that is not the version of record: the judgment is provisional |
| `content` | the page is there and its text unreadable: a page so read is served for its image; a copy's page so read is never evidence |
| `mismatch` | the file held under the work's key is not its entry's work (its title page prints another title or other authors), and no person confirmed it: a wrong file, never evidence |
| `absent` | no source holds the work, and none failed to say: a miss, never evidence |
| `infrastructure` | the tool failed, or a source that failed may hold the work: never evidence |

A judgment records the evidence it was given: each file of each work its
subject cites, read or not, and each file of another work it read a page
of that the store knows or it read, by its key, its read status, its
file's SHA-256 (which file it is) and its content hash (what it prints);
and each work its subject cites that the review was given with no page,
with that read's status (`mismatch`, `absent` or `infrastructure`): why
its reading could not conclude. The filing records them, never the
reader; one filed by hand is taken as given the files the store last
saw, each file it read a page of stamped then and given as it reads.
What a reading was given is what lapses it or asks it again; what it may
rest on is any page it read. `show` names beside its pages a work its
block does not cite, or the paper cites nowhere: whether the paper
supports the claim at that place is the graph's question and the
rulings'. The store knows the corpus as a review last saw it: each
review, before it decides what to ask, reads every file of each cited
work, and of each other work a reading or a ruling rests on (its holder, the file check, its SHA-256 and its content hash) and
files what differs, whichever command or edit changed the corpus; an
outage files nothing, and a key the work no longer lists as a file is
`absent`.

**The file's content is the evidence.** A page's text is axiomation's reading of
its file: a new ingest, another OCR, a co-author's ingest, a tier
switched or an extractor upgraded reads it otherwise, and the work says
the same. So a reading rests on the files it read, never on the text a
page gave: it lapses where one of them prints other content, or is
absent or held back (§ "When a judgment lapses"). What a file prints is
its content hash, which leaves out what a reader adds or a download
varies: an annotated file, or one downloaded again, prints the same;
a publisher's per-download stamp or annotations flattened into the page
change it, and lapse every reading of the work ([the
architecture](architecture.md) § 9).

A reading that did not `verify` is **asked again** where its evidence
grew: a work it could not read now reads, or gained a file. Asked again
is no lapse: the reading speaks until a new one is filed ([the
architecture](architecture.md) § 9).

A work's file is checked against its entry (its cited edition alone,
where it holds several): one whose first pages
print neither its entry's title with its authors, nor its DOI, nor its
pages as their own page numbers, reads as `mismatch` until a person
confirms it or replaces it ([the architecture](architecture.md) § 9).

An entry may hold several files; one is the cited edition, the edition
the entry names, which keeps the entry's key, unless neither the files
nor a person decided it. A reading rests on the
file, whatever key holds it. A `cited` judgment names pages of any file
of its work: what the work says may be read on any of them, a locator
only on the cited edition, quoted words on the file whose words they
are ([the architecture](architecture.md) § 9).

A `computed` judgment may name a run of its script: code runs it with the
paper's declared runner and the arguments the judgment names, never
through a shell, and stamps the run (the script's content, the runner,
the arguments, how it ended, the output's hash). A `verified` one names
each value it matched, the block's words beside the output's, and quotes
a line of the script that computes them.

<!-- closed set: axiomation.evidence.RunStatus -->
| run status | |
|---|---|
| `ran` | exit 0 |
| `failed` | another exit, or the runner could not start: never evidence |
| `timed-out` | ran past the paper's timeout: never evidence |

**A cited work's publication** is how it is published, read from what
the resolvers answer of its identifier (its DOI, else its arXiv
identifier), never from the bibliography's entry type (`axiomation
sources publications`). A person may set it, and a person's latest
speaks over every lookup, before or after, save a retraction a lookup
files after it, which speaks until the person sets the work again; a
later lookup moves only the resolvers' word:

<!-- closed set: axiomation.records.Publication -->
| publication | |
|---|---|
| `refereed` | a journal article or a proceedings paper |
| `book` | a book or a chapter of one |
| `preprint` | a preprint no version of record is known for |
| `preprint-published` | a preprint whose version of record has been published since: refereed ground |
| `thesis-or-report` | a thesis or a report |
| `erratum` | a work with a published correction or erratum |
| `retracted` | retracted: `established` reads it (§ 9) |
| `unknown` | nothing told: the entry names no identifier, or the resolvers give no type |

A preprint's version of record is found by its own record, else by its
title and first author, a match that can be wrong ([the architecture](architecture.md) § 9). A cited
preprint published since is the lookup's finding, severity 1, aspect
`cited`, on the work and each citation of it: cite the version of
record, and check that what the paper takes is in it and under which
number. It is filed once per work, so a person's dismissal holds until a
citation of it changes. Save a retraction, a publication never moves a
verdict: a weaker work is weaker ground, not false support. `check` warns of it
(`cited-publications`, § 11) and a claim's status says it (§ 11).

**When a judgment lapses.** At any state after the one it was judged on,
the first cause found:

<!-- closed set: axiomation.judgments.CauseKind -->
| cause | |
|---|---|
| `deleted` | its subject is deleted |
| `text` | a basis element other than the subject changed its text (identity's `math`, `macro`, `changed`, `split` or `merged`, or its words differ and identity does not call it `markup`) or was deleted, or a kept file or the paper it names changed; a re-wrap or a move changes no text |
| `meaning` | a basis element has a Δ that carries, or the rule moved it |
| `edge` | a basis edge is gone, or the one hop holds an edge the basis never had: the reading was not shown it (a retarget is both, and so is an edge an edit points at other words of its target; a line a split or a merge carried is neither) |
| `cited-file` | a file of a cited work it read a page of prints other content (its content hash), or is absent or held back, and no other file of its work prints what it printed; a page read again with another text lapses nothing |
| `run` | a later run of the same call printed another output |

An edge added, by an edit or an update, lapses each reading whose one
hop it joins; a line's `sketch` or `analogy` flag lapses nothing by
itself ([the architecture](architecture.md) § 9). Re-verifying something upstream
never clears a lapse: validity upstream is read by `established`. A
retraction of a cited work lapses nothing; `established` reads it.

**A subject's status per aspect:**

<!-- closed set: axiomation.judgments.StatusKind -->
| status | |
|---|---|
| `unjudged` | no judgment |
| `held` | a live judgment speaks: its verdict and depth |
| `disputed` | live judgments by different authors differ, or a `verified` one meets an open, live severity-3 finding no later reading of a person decided over |
| `lapsed` | every judgment on it lapsed; it names its latest reading's first cause, the reading a re-ask is offered for |

An author's later judgment replaces its own earlier one. An author is a
person, by name, or an agent by its host and model, whatever the host's
version. An
`inconclusive` takes away credit, never debit: it never replaces its
author's own live negative judgment, and stands beside it. A person's
judgment decides over the agents' made before it; an agent's later one
that differs disputes it until the person answers; persons who differ
stay disputed; `inconclusive` differs from nothing. A person files by
name, and may adopt an agent's live judgment as their own
(`axiomation adopt`). Pins protect graph lines, never judgments.

A lapse takes away credit, never debit. A subject is a **lapsed
negative** where each author's latest reading that concluded, taken as
live, stands on a negative one that lapsed (held `not-shown` or
`contradicted`, or a person's that a later agent disputes), and no live
reading concluded after it. It is unresolved until one does, by anyone,
under the rules above; a reaffirmation is one, and keeps it negative. An
`inconclusive` reading settles nothing, its author's own included. A
lapsed negative whose subject the state no longer offers (deleted, its
justifies edge gone, its citation reworded) leaves with it: nothing
there can read it again, and what it argued is read anew.

**Recovery.** A lapsed judgment's **re-ask brief** (`axiomation reask`)
lists every cause that lapsed it, the edits they began at (each edited
block's Δ lines and notes, its text then beside its text at the update,
though no element of the basis), and its subject and basis as they read
now, with each element's old text, Δs and gone edges. A re-reading that
finds the conclusion standing files a **reaffirmation** (`axiomation
reaffirm`): a new judgment naming the one it reaffirms, its depth the
weaker of the two. A judgment resting on a run is never reaffirmed: a new
run is a new reading. A lapsed finding whose problem still stands is
refiled (`axiomation reaffirm-finding`): a new finding at the state
naming the one it reaffirms and why, its aspect, severity, problem, fix
and pages the lapsed one's, its anchors walked there and their words
found again (the first worded one may be quoted anew); the lapsed one is
then no longer open.

**A finding** is a problem filed against the paper (`axiomation find`): a
problem, a fix, a severity 1 to 3, the aspect it bears on, and one or more
anchors:

<!-- closed set: axiomation.records.AnchorKind -->
| anchor | |
|---|---|
| `block` | a block |
| `part` | a claim part: a block and words |
| `group` | a group |
| `span` | a block and a verbatim excerpt: a citation site, a quotation |
| `paper` | the paper whole |
| `work` | a work: one of the paper's bibliography by its key, cited or not (one cited nowhere shown so), or one found outside the bibliography by its identifier (`arxiv:ID`, `doi:DOI`), or, with neither, by a link to a public page of it (`https://…`), a pointer whose pages are never read |
| `file` | a kept file's lines |

A finding bears on a claim through its anchors: a `block` or `group`
anchor on every claim of the node it names, a `part` or `span` anchor on the
claim whose words its words meet (they hold the claim's, or the
claim's hold them), not on the block's other claims.

A finding may name the pages of a work it read (a `novelty` or
`literature` finding's work), stamped as a judgment's are and recorded beside it, never
walked: the work is not the paper's. It lapses by a judgment's rule
except `cited-file` and `run`, shown lapsed and never fixed by an edit. A later record answers it (`axiomation answer-finding`):

<!-- closed set: axiomation.records.FindingStatus -->
| answer | |
|---|---|
| `fixed` | the paper was fixed |
| `dismissed` | not a problem |
| `duplicate` | the same as another finding, named; never along a cycle |
| `postponed` | left for later |



Who answers decides whether the answer speaks. A person's latest answer
speaks over every agent's, before or after; where no person answered,
the latest answer of a review speaks. An agent's answer filed by hand is
a **proposal**, of any kind and with its reason: it never closes a
finding, which stays open, its proposal shown beside it, until the
review of the kind that filed it settles it (`fixed`, or refiled
standing) or a person answers it. A person may file their answer as a
proposal too (`answer-finding --propose`), shown as theirs and settled
the same way, the review weighing its reason against the text; only on
a finding a review filed that no answer speaks for. A finding a person
filed, or an agent by hand, or one a person answered, is a person's to
settle. The review settles a proposal one of three ways: `fixed`; the
finding refiled standing, its evidence answering the proposal's reason
with a case, the proposal shown not taken; or, where neither reading of
the text has a case, the finding refiled as a **disagreement of
reading**: no review asks it again, a person's answer closes it (never a
proposal), and until then it counts as its severity says.

A person may withdraw any answer (`answer-finding --withdraw`), with its
reason: it speaks no more, and the finding reads as its other answers
leave it, open where none speaks, for an answer or a proposal to follow.
A proposal the withdrawn answer had settled waits again for the review
of its kind. The store keeps the answer, shown withdrawn.

A person's `dismissed` says the problem is none, not that the reading
filed with the finding is wrong. Where a review filed the finding in the
same answer as a negative reading of its aspect on a subject the finding
bears on, and the subject stands on an agent's negative reading, the
reading holds, but the next review of its kind asks the subject again,
once for each dismissal, its hints carrying the finding, the answer and
its note: until a reading of the subject is filed after the dismissal.
That reading is the dismissal's re-ask, whoever files it. One that
concludes supersedes, while the dismissal speaks, the agents' negative
readings filed with the finding, which speak no more (the store keeps
them): a re-ask still negative stands in their place. An `inconclusive`
re-ask leaves them standing: a dismissal and a reader who could not
tell never clear a negative reading. A person's own reading (`judge`)
decides it, and no review asks it again for a dismissal.

A person may also file a reading as a **proposal** (`judge --propose`),
shown as theirs: it never speaks, and the next review of its kind asks
its subject again, its hints carrying the proposal, until a reading of
the subject is filed after it, whoever files it: its re-ask. One that
concludes supersedes the agents' negative readings filed before the
proposal; an `inconclusive` leaves them, and the person's own reading
decides. A proposal that lapses asks nothing more: the person proposes
again on the subject as it reads now. A person whose own reading decides
the subject proposes none: no review asks it.

An open, live severity-3 finding counts as a negative judgment of its
aspect; severities 1 and 2 are shown, never status. A finding on how the
paper reads, what it is worth or where it stands in its literature
(`presentation`, `significance`, `novelty`, `literature`) is never status: a trivial result is still
established, and an uncited standard result no less true.

**A ruling** answers a referee's question of a claim standing
`unsupported`: would a referee in the field want the paper to argue it?
It is never a judgment: whether the claim holds is its `true`, and a real
gap is often a true claim. A person or an agent files it on a kept state
(`axiomation rule`, the argument session), naming the claim, the ruling
and its reason, always:

<!-- closed set: axiomation.records.RulingKind -->
| ruling | |
|---|---|
| `owed` | a referee would want the paper to argue it: in doubt, owed |
| `known` | common knowledge, or a standard result of the field, named in the reason |
| `immediate` | follows at sight from a result of the paper's own, which a uses edge of its block names, the block's words giving no step |
| `interpretation` | a gloss of what the paper shows that nothing rests on; never what the paper contributes |
| `positioning` | a claim that the paper's work is new, or that other works lack or did not do something, asserting nothing of what a work contains: reading the literature settles it, and the contribution session reads it |
| `left-unargued` | a person's alone: a referee would want it argued, and the author leaves it unargued, the reason saying why (a routine step a reader supplies, a stance kept short) |

An `owed` ruling is the author's to-do item, and says how much hangs on
it, why, and what would discharge it. Its **severity**, 1 to 3, weighs
the claim in the paper, never its truth: 3, a result or a contribution
the paper states rests on it, or it is one; 2, a claim of the paper's
own nothing rests on; 1, a passing sentence that softening or cutting
costs nothing. Beside it, what relies on the claim in one hop is
computed, never asked. The two sort the owed claims, heaviest first,
and change no gate: a severity-1 `owed` keeps its claim in
`unsupported-claims`. Its reason names what is not argued (the step,
the notion, the case left open, or the reading a referee would take),
and its **fix** what would discharge it: the missing step, the pointer
the sentence owes, a softened wording, or a cut. A person's `owed`
gives both too. An `owed` ruling on words a settled finding sits on
(answered `fixed` or `dismissed`, a part or span anchor on the claim's
block whose words hold the claim's, or the claim's hold them) names that
finding and says why the argument is still owed despite the answer;
an agent's `owed` filed before such an answer is asked again by the next
review, until a ruling of the claim is filed after the answer.

Each author's latest ruling speaks while it is live (an author as a
judgment's, above): a person's, whatever an agent rules after it; where
no person's is, each agent's, and any `owed` among them wins. A latest
ruling that lapsed or was withdrawn speaks for its author no more, and
an earlier one of theirs never speaks in its place: the next review asks
the claim again, unless another person's ruling speaks for it. Agents' word alone clears no claim whose `true`
is disputed, and their `known` or `immediate` only beside a `true` held
`verified`: a claim no reading showed holds is neither. Whether a claim
immediate from the paper's own result owes its pointer ("by (i)") is
the reader's call, `owed` or `immediate`; the uses edge onto that result
is the graph's either way, and `immediate` clears only while its block
has one.

A claim the paper argues is no ruling's, wherever the argument
stands: another block's justifies edge, its block's own step (`self`,
§ 4), or the claim it restates (a summary, a synthesis, expository prose
a later part argues, which owes only the pointer). Whether it is argued
is the **support search**'s question, asked before any ruling, one
claim at a time (`support --ask`, [the architecture](architecture.md) § 9).

What a claim owes is asked by its
place: a statement or a step its argument, a restatement
its pointer, a gloss nothing rests on nothing.

A person may rule a set of claims at once (`rule --in`, `--role`): each
claim standing unsupported the set holds takes its own ruling, with the
one reason, read and guarded as a single ruling is; an agent rules each
claim. A person may withdraw any ruling (`rule --withdraw`): it speaks
no more, and the next review that judges the claim's `true` asks it
again; a person's own ruling, by contrast, pins it.

A ruling may rest on **pages** of a work of the paper's bibliography
(`rule --page`, the argument session's rule line): a `known` result read
there names them, one the field knows without a work names none, and
none is required. They are read and stamped as a judgment's, an agent's
from what it read, a person's as given, and a proposal's are read by the
review that settles it.

A ruling lapses only when its claim's words
change or end, or its block is deleted, or a file of a page it rests on
lapses it as it lapses a reading (above): it is a call on those words,
and on those pages.
A ruling or a reading a review filed under an older version of its
question is asked again, and speaks until a new one is filed ([the
architecture](architecture.md) § 9). A
ruling never clears a claim the paper declares `promised`, nor an
`interpretation`, a `positioning` or a `left-unargued` something relies
on: a uses edge
relies on it (§ 4, a fallback included), or its block qualifies or
justifies another. An agent never files `left-unargued`, and an agent's
speaks for nothing.

A person may file a ruling as a **proposal** (`rule
--propose`), shown as theirs: it never speaks, and the next review that
rules the claim asks it again with the proposal in view, until a ruling
of the claim is filed after it, whoever files it: its re-ask, which
settles it. A person whose own ruling decides the claim proposes none,
nor `left-unargued`, which no review files; a proposal that lapses or is
withdrawn asks nothing more.

A ruling takes the claim out of
`unsupported-claims`' count (§ 11) and changes no status: § 9 reads
none, so what rests on a ruled claim still asks its `true`.

What a ruling did is a claim's **support status**: the support the graph
decides (§ 5), read with the claim's rulings, computed, never stored.
`check`, `open` and the viewer read the one answer:

<!-- closed set: axiomation.rulings.Support -->
| support status | |
|---|---|
| `supported` | the paper argues it: `justified` by a justifies edge, `self`, or `restated` restating a claim |
| `no-argument-owed` | it stands `unsupported`, and a ruling clears it: `known`, `immediate`, `interpretation` or `positioning` speaks, under the rules above |
| `left-unargued` | it stands `unsupported`, its argument owed, and a person leaves it so: `left-unargued` speaks, under the rules above |
| `unsupported` | it stands `unsupported` and no ruling clears it (none speaks, an `owed` one does, or the rules above refuse the one that speaks), or its own reason is a gap: `justified` with no justifies edge, `restated` restating nothing or a conjecture, or in a circle, which no ruling clears |

A `restated` claim that restates a `conjectured` one asserts what the
paper only conjectures: its support is `unsupported` whatever its route
reads, as a claim standing `unsupported` is listed by its standing,
never its truth; its route is still the conjecture's (§ 9), so a reading
that shows the conjecture holds establishes it, and the gap stays.

A `conjectured` claim has none: the paper does not assert it. The
support status says nothing of truth: an `unsupported` claim's `true` may be
held `verified`, and a `no-argument-owed` one is established only as § 9
says. Rulings that differ make no state of their own: who speaks is
the rule above, and any `owed` that speaks keeps the claim
`unsupported`.

**A positioning claim** is the contribution session's, one question one
owner: where the paper stands in its literature is never status. A claim
that a work shows or contains something is not one: it owes its citation,
in place or by its pointer to a block that cites. The contribution
session's `checked` line on a claim is its reading, a **check**: the
block and the words it quotes, what it searched and what it found, on a
kept state. A check lapses as a ruling does, when the words it quotes
change or end. A live check reads a claim of its block whose words it
quotes whole; a fragment reads nothing. A person or an agent files one
by hand too (`axiomation check-contribution`). The argument session asks no `true` of a claim
`positioning` clears; `unreviewed` lists the claim until a live check
reads it (§ 11). An objection to it is a `novelty` finding.

**A suggestion** offers the author a work the paper may want to cite at
a place: a pointer, never a problem. A person, an agent or a review
(`axiomation suggest`, the relatedwork session) files it on a kept state,
naming its anchors (the block or group where the work would be cited,
then each work suggested, `work:KEY`: one of the bibliography, cited
elsewhere or nowhere, or one found outside it), why a reader of the
field might expect it there, and the pages read of it, stamped as a
finding's. A
review offers a work once at a place: one a live suggestion or a live
`literature` finding already names there is not filed again. It
is never status: no gate or judgment reads one; `open` lists an
unanswered one as the author's to take or decline (§ 10). It lapses by a
finding's rule on its anchors' text alone, shown lapsed and never fixed
by an edit. A later record of the author's answers it (`axiomation
answer-suggestion`); the latest answer speaks:

<!-- closed set: axiomation.records.SuggestionStatus -->
| answer | |
|---|---|
| `taken` | the paper now cites it |
| `declined` | the author does not want it |

## 9. Established

Whether a claim is **established** is computed per standing at a kept
state (`axiomation show`, `blocks --established`), over the graph, the
judgments and the open stale marks. A claim is established when its
route holds **and** everything it rests on holds. Its standing gives the
route:

- `justified`: some J for it holds a `verified` `valid`;
- `self`: a held `verified` `true`, or each work its block cites holds a
  held `verified` `cited` and none is retracted;
- `restated`: what it restates is established;
- `conjectured`, `unsupported`: only a held `verified` `true`, a
  reader's showing that the claim holds, never the paper's: `show` says
  so where it is the route ("established by a reading; the paper argues
  nothing for it"); an `unsupported` claim's reason names what the paper
  declares of it (§ 4). A ruling (§ 8) is never a route.

On every route, by what it rests on, walked from its block (and the
route's J) along uses edges: each claim relied on (§ 4) is established; a
definition, an assumption or a part with no claim is walked through, a
qualifies edge onto a walked node walks its source, and a uses edge from
a premise onto one it generalizes (§ 3) is not walked (§ 10); each code file a
walked block uses holds a held `verified` `computed` on that block; and
nothing walked holds a negative reading no person decided over (a held
`not-shown` or `contradicted`, or a disputed subject), an open severity-3
finding of an aspect that is status (§ 8) or an open stale mark that is no
description (§ 5). The assumptions reached are listed, never
themselves established; so are the cited works it rests on: each its
block cites, where it stands by its citations; each whose pages the
reading on its route stamped; and those of each claim it asks. Of their
publication (§ 8) only a retraction is read here; the rest is
`check`'s.

**A conditional result.** A conjecture a claim's statement takes as its
hypothesis (an `assumed` uses edge from its block, § 3) is asked by no
edge of its walk, its J's included: the claim holds whatever the
conjecture's truth, and `show` says it is established under it. A claim
stated inside its argument (its context, below) takes its hypotheses
too. What rests on the claim asks the conjecture again, unless it takes
it as its own hypothesis: "by Theorem x, y" asserts y, and is
established only once the conjecture is. A flag from a step of the
argument, the statement silent, is no hypothesis of the claim: the walk
asks the conjecture, and it is an undeclared premise (§ 10). The
hypothesis is a premise all the same: a claim whose J asks the
conditional result rests on it (§ 10). A wrong flag lends support, so
the reading catches it (the claim verified only as the conditional its
words state, § 8) and `check` names each claim established under a
conjecture, and who wrote its flag (§ 11).

A block's **context** is the claims whose arguments hold it as a step:
a member of a group that justifies the claim, or a justifies source that
writes reasons (a justification role) or holds no claim. A block of
claims alone that justifies another is a result offered as the reason,
not a step of it. A claim stated inside another's proof holds under that
claim's hypotheses, and under those of the claims holding that one in
turn: its walk takes in their statements, walked through, never asked.

An edge from a step onto the claim its argument serves relies on that
claim's hypotheses (a context edge, § 3), which the walk has from the
claim's block already, so it asks nothing. Within the claim's own
argument the claims bound so are the claim and those holding it; a
proof that argues two claims and uses one to argue the other relies on
that one's result, asked. A step of another claim's proof reused ("as
above") is bound to the reusing claim: its edges onto the claims its
proof argues read as onto the reusing claim, so it takes none of their
scope. A context edge that names a claim its argument does not argue
relies on that claim's result, and asks it. One naming no claim of a
block that holds several reads, within the claim's own argument, as its
hypotheses, or sibling claims would ask each other in a circle; from a
reused step it asks each claim its argument does not argue, a fallback
(§ 4), since which it means is unknown. One naming only definition or
assumption content walks through it. The rule changes what the walk asks, never what an
edit reaches: the context edge and the reuse stay edges, so an edit to
the claim reaches the reused step, an edit to the step reaches the proof
that reuses it (§ 5), and a stale mark on the step counts against the
reusing claim.

It is a least fixed point, so circular support establishes nothing, and
a fresh build establishes nothing until it is judged.

**A circle** is claims each resting on the others: a strongly connected
part of the graph of what claims rest on, over every way each may be
established (each J, its own route), or a claim resting on itself. A
claim resting on its own result is never support, so no circle is
benign. It is found from the graph alone, readings or none, so a fresh
build shows its circles. It holds back the claims not established
because of it, its own among them, and each names it in its reason; a
circle through a way a claim does not need (another J holds) holds none
back and is still listed, since a line on it is still wrong.

Most circles close because an end names less than the text relies on:
edges are found for recall, and such an end reads toward the alarm (§ 3,
§ 4). Each line on a circle shows where its ends may:

<!-- closed set: axiomation.established.Coarse -->
| candidate | the line that asks |
|---|---|
| `whole` | names no part and no claim of a block holding definition or assumption content |
| `unnamed-target` | names no claim of a block of several |
| `unnamed-source` | an about line naming none of its block's several claims at its source end |
| `sibling` | restates a claim that is itself `restated` |

A candidate is not a verdict: a line onto a claim alone may still rely
on a hypothesis written inside it. No rule breaks a circle, since one
that dropped an edge, relaxed a fallback or skipped an ask would hide a
real circle as readily as a false one: only a line's ends named as the
text relies on, or the paper changed. A circle whose every end is named
and that stays is the paper's: it argues X by Y and Y by X, or its
restatements have no original. A circle is named by its key, its lines'
ends and its claims' standings: a text change that leaves those alone
leaves the circle.

`circles --ask` asks what each end relies on: an agent answers each
circle **refined**, naming its ends more exactly, or **real**, every
end named and the circle still there, with why and what a person
should change. The answer may only narrow, never adds support, and
nothing it does makes a paper pass ([the architecture](architecture.md) § 9).

Where a claim is not established, the reason is the first missing
element; a claim it rests on gives that claim's reason, named:

<!-- closed set: axiomation.established.Missing -->
| reason | |
|---|---|
| `unjudged` | the route's judgment was never filed |
| `lapsed` | it lapsed |
| `disputed` | readings differ |
| `inconclusive` | the reading could not tell |
| `not-shown` | the reading found it not shown |
| `contradicted` | the reading found it false |
| `finding` | an open severity-3 finding sits on what it rests on |
| `stale` | an open stale mark sits on what it rests on; the reason names its line and since when it is unread |
| `unsupported` | its own route has nothing to rest on (`justified` with no justifies edge, `restated` restating nothing, `unsupported` or `conjectured` with no `true` held `verified`), or a claim it rests on stands unsupported |
| `circular` | its support runs in a circle; the reason names the circle |
| `retracted` | it cites a retracted work |

## 10. What is open

`axiomation open` lists what is open at a kept state, a section per kind,
each item with its anchor, its cause and what clears it:

<!-- closed set: axiomation.queries.OpenKind -->
| kind | |
|---|---|
| `unsupported` | a claim `unsupported-claims` lists (§ 11): its status `unsupported`, its support status `unsupported` (§ 8), standing unsupported with no ruling that clears it, or its own reason a gap that is no circle; its line ends with its `true`'s reading |
| `circle` | a circle of what claims rest on (§ 9), one item: its claims, each line on it with its candidate ends, the claims resting on it; those holding the most claims back first, those holding none last |
| `uses-unsupported` | a block that uses one directly; a claim no argument is owed for is no gap to rest on |
| `partial` | a `justified` or `self` claim whose completeness is `sketch` or `analogy`, until a held `verified` `valid` of its J on it (`true`, for a `self` claim) or a fuller justification |
| `flag` | a flag no step decided |
| `stale` | an open stale mark, ranked by the claims whose `stale` reason it is, the most first; then each description (§ 5) |
| `carried-unread` | what an update carried unread, per edit it began at (per update, where its record names none): the blocks the rule moved that neither a Δ of their own nor a `verified` reading has read since (§ 5); in no gate |
| `pending` | an edit not yet applied, or a question unanswered |
| `lapsed` | a lapsed subject, or an open finding or an unanswered suggestion that lapsed |
| `disputed` | a disputed subject |
| `negative` | a live negative reading (§ 8), any aspect: what `defects` lists of readings, a lapsed one aside (`lapsed`) |
| `finding` | an open finding that has not lapsed |
| `suggestion` | a suggestion no one has answered, live: the author's to take or decline |
| `retracted` | a cited work retracted, on every block citing it: what `defects` lists of it (§ 11) |
| `publication` | a cited work a referee would question as ground, none accepting it: what `cited-publications` lists |
| `unplaced` | a citation or a quotation no block can hold |
| `unjudged` | a subject a review session judges that no reading has judged; the `true` of an unsupported claim aside, whose own item names its reading |
| `inconclusive` | a subject whose held reading could not tell, the works it could not read named |
| `provisional` | a subject `verified` on pages that are not the version of record |
| `unchecked` | a claim `positioning` clears that no live contribution check reads |
| `unread` | the state's kept files could not be read, so a gate that reads them cannot pass |
| `dangling-reference` | a reference to a label no block holds: the cut lost the edge it would make |
| `duplicate-label` | a label two or more blocks hold: each reference to it reads the first |
| `unused-premise` | an assumption no uses edge reaches and that qualifies nothing (an assumption or condition environment one unit, used where any of its blocks is; a block of the ambient frame counts as used): dead weight, or an edge the writer missed |
| `undeclared-assumption` | a premise a justified claim's J rests on that the claim's statement does not, with its cause and its path (below) |
| `later-rule` | a problem the installed version's checks find in a stage's file as the state shows it, which no edit is refused for (§ 7); shown on the block an edges problem names first, else on the paper |

**An undeclared assumption.** A premise is an assumption the walk
reaches (§ 9) or a `conjectured` claim it asks, from the J itself or
from the statement of a claim it asks, never from that claim's own J; a
`restated` claim's statement is the one it restates. The rules:

- a fallback ask (§ 4) counts for what the J rests on, never for what a
  statement declares;
- an assumption about another restates it (§ 3): a statement that
  declares the copy declares what it restates, and one about several
  restates each;
- a statement that declares a case of a wider premise declares the wider
  one: where a premise it declares, or a node its walk visits, is the
  target of a `generalizes` edge, the edge's source is declared, and
  what generalizes it in turn, never what the source's walk reaches. The
  edge says the case implies the source, nothing of what the source's
  words write, so each premise declared so is one edge, judged (§ 8);
  declaring the wider declares nothing narrower;
- a uses edge from a premise onto one it generalizes carries notation,
  never premises: no walk goes through it, since were the wider one to
  rest on the narrower one's premises, each would imply the other;
- a premise that a justifies edge discharges, from a block the J holds
  (the J, or a member of it), is no premise of it, and neither is what
  only it rests on (§ 3).

The gate cannot tell whether a reused step holds under the claim's own
hypotheses, nor a reused step from a step's result relied on: both are
read as the step reused (§ 9), and the `valid` reading of the claim's J
judges them; the gate's silence there is no check passed.

An `undeclared-assumption` item says its cause, the first that holds.
One fault is one item: a statement with no uses edge lists its premises
once, and so does a line naming no claim:

<!-- closed set: axiomation.established.UndeclaredCause -->
| cause | the item says | items |
|---|---|---|
| `missing-edges` | the statement has no uses edge: its edges were missed, or it states nothing it rests on | one per claim |
| `unnamed-line` | a line onto a block of several claims that names none made it: the statement's ask, which declares nothing through it; the J's line onto the claim, which may argue another; or the J's ask, which rests on each claim's premises | one per claim and its lines |
| `discharged-elsewhere` | a justifies edge discharges it, from a block outside this J | one per premise |
| `narrows` | it uses an assumption the statement declares: a narrower or added premise, or a restatement missing its about edge; or the J reaches it through a case of a premise the statement declares (the premise, or a node on its path, the target of a `generalizes` edge from it), the edge named | one per premise |
| `left-out` | a premise the statement leaves out | one per premise |

Each item prints its path under its line: every step walked from the J
(or the statement) to its premises, once: an edge with its kind and its
quote, a group's member, or a claim's context.

The eight from `retracted` to `unread` are what a gate (§ 11) lists
that no other kind holds, as each gate under its default mode lists it,
so every item a gate lists is open here, the open view counting it.

The four from `dangling-reference` to `undeclared-assumption` are what
the kept graph got wrong or cannot settle, each
computed from the kept state alone: labels resolve over its kept files as
the installed version parses them, as the cutter resolves them. Where those files
cannot be read, `open` lists it (`unread`); likewise where a state's stage
files cannot be shown for `later-rule`.

## 11. Whether a paper passes

`axiomation check` answers one question of a kept state: does the paper
pass? A **gate** lists the items that stand in the way, each with what
clears it; the paper passes when no gate fails. Every item is computed
from the state, never filed.

<!-- closed set: axiomation.check.Gate -->
| gate | lists | default |
|---|---|---|
| `unsupported-claims` | each claim whose status (below) is `unsupported`: its support status `unsupported` (§ 8), whatever its `true` says, standing `unsupported` with no ruling that clears it, or its own reason a gap; a circle (§ 9) one item where a claim of it is listed, never one per claim in it. Never a `conjectured` claim: the paper does not assert it; a claim whose status is `defect` is `defects`', and one `disputed` is `unreviewed`'s, each counted apart in the gate's line | block |
| `defects` | each negative reading, any aspect, live (held, or a person's that a later agent disputes) or a lapsed negative (§ 8), marked lapsed; each open severity-3 finding of any aspect, how the paper reads included, `postponed` included, live or lapsed (marked so); each retracted work the state cites, on every block citing it. A reading's item carries the open stale marks on lines its basis holds as its own (§ 5), "line E marked at S", which are no items of their own | block |
| `unreviewed` | each subject a review session judges (`valid`, `true`, `computed`; `cited` on each citation and quotation) with no `verified` reading of the version of record: unjudged, lapsed, disputed by agents, inconclusive (carrying the marks on its own lines, as a defect does), or provisional; each claim `positioning` clears that no live check reads (§ 8), in place of its `true`; each site no block can hold; each open stale mark no reading carries and that is no description (§ 5); each flag no step decided | warn |
| `cited-publications` | each work the state cites whose publication (§ 8) is `preprint`, `thesis-or-report` or `erratum`, one item on every block citing it, with how many established claims rest on it (§ 9), those resting on the most first; a work a person accepts resting on (`axiomation sources accept`) listed apart, out of its count; a cited work no publication was filed for counted, never listed | warn |
| `dangling-references` | each `dangling-reference` (§ 10) | block |
| `duplicate-labels` | each `duplicate-label` | block |
| `unused-premises` | each `unused-premise` | warn |
| `undeclared-assumptions` | each `undeclared-assumption` | warn |

**Each gate lists what a person acts on, once.**

- A claim that is not established is listed where its own reason is a
  gap (`unsupported-claims`) or where its own route is unread
  (`unreviewed`). Where its reason sits in a claim it rests on, it is
  not listed: that claim is.
- A claim standing `unsupported` is listed by its standing, not by its
  reason: a `true` held `verified` makes it established and leaves the
  paper's gap where it was.
- Its status (below) decides the gate where it has two: read negative or
  under a blocking finding it is a defect, and disputed it is a
  person's to judge first, `unreviewed`'s; `open`, `check` and the
  viewer count the gate's claims alike.
- `unreviewed` leaves out the `true` of each claim `unsupported-claims`
  lists, and of each `conjectured` claim. A claim a ruling clears leaves
  `unsupported-claims`, and its `true`, where unread, is
  `unreviewed`'s; save a claim `positioning` clears, whose reading is a
  check (§ 8): `unreviewed` lists it `unchecked` until one reads it, and
  its `true`'s readings are in no gate but `defects`.
- A negative reading is a defect and never unread, live or lapsed: a
  lapse takes no item out of `defects`, only a reading that concludes
  or an answer does, so a paper whose remaining problems all lapsed
  still fails.
- `defects` and § 9 overlap on purpose: a severity-3 finding on a proof
  is a defect, and it is also the theorem's reason. A severity-3
  finding on how the paper reads or what it is worth is never status
  (§ 8) and still fails `defects`: its severity says a main result is
  unreadable, vacuous or known, and such a paper does not pass, however
  sound its argument.
- In no gate: findings of severity 1 and 2 (§ 8); pending edits and
  unanswered questions, which belong to a later state; a lapsed or
  disputed `accurate`, since no session judges `accurate`. It still
  stops what it sits on (§ 9), so a paper can pass with such a claim
  not established, until a session judges it.

**The audit.** `check` counts the claims rulings cleared (no argument
owed), and how many of them no person's ruling speaks for, and lists
each after the gates with who spoke and why: the audit of the agents'
rulings is one read. It counts apart the claims the author leaves
unargued and lists each after those, with its reason: out of the gate's
count, never cleared. It counts the claims established under a
conjecture their statement takes as a hypothesis (§ 9), and how many of
them a person did not write every flag for (theirs, and those of the
claims whose argument holds them), and lists each with its conjectures:
a flag lends support, and only its writer speaks for it, so an edge a
person wrote that an agent flagged is the agent's.

<!-- closed set: axiomation.check.Mode -->
| mode | |
|---|---|
| `block` | an item fails the paper |
| `warn` | items are shown; the paper can pass |
| `off` | the gate reads nothing and lists nothing: an unsupported claim's `true` then stands in `unreviewed` |

<!-- closed set: axiomation.check.Outcome -->
| outcome | |
|---|---|
| `pass` | no item |
| `warn` | items, mode `warn` |
| `fail` | items, mode `block` |
| `off` | mode `off` |

A paper sets a gate's mode (`[gates]`, [the guide](guide.md) § 1). There is no
count limit and no per-item exception: every blocking item is fixed in
the text, save an unargued claim a ruling says the paper need not argue
(§ 8), which is the reader's call on the text, never on the claim's truth,
and one the author leaves unargued, which is the author's.

Each item names its cause, the open view's kind (§ 10):

<!-- closed set: axiomation.check.Cause -->
| cause | |
|---|---|
| `unsupported` | a claim standing unsupported, or one with no justification or restatement |
| `circular` | a circle its claims' support runs in, one item |
| `not-shown`, `contradicted` | a live negative reading |
| `finding` | an open severity-3 finding |
| `retracted` | a cited work retracted |
| `unjudged`, `lapsed`, `disputed`, `inconclusive` | a subject's reading (§ 8) |
| `provisional` | `verified` on pages that are not the version of record |
| `unchecked` | a claim `positioning` clears that no live check reads (§ 8) |
| `unplaced` | a citation or a quotation no block can hold |
| `publication` | a cited work a referee would question as ground |
| `stale`, `flag` | as in § 10 |
| `dangling-reference`, `duplicate-label`, `unused-premise`, `undeclared-assumption` | as in § 10 |
| `unread` | the state's kept files could not be read, so a gate that reads them cannot pass |

**A claim's status.** For its author each claim has one status, folded
from its records: its established verdict (§ 9), its support status
(§ 8), the readings on its route (its `true`; the `valid` of each
justification onto it the state offers) and the open findings on them.
The records keep their meanings; the status is read from them, never
filed, and every read that shows a claim (`show`, the viewer) shows this
one. It is the first that holds, in this order, so a status that reads
as settled never comes before one that asks something: a claim with a
dispute, a blocking finding or an `owed` ruling is never shown
established:

<!-- closed set: axiomation.claim_status.ClaimStatus -->
| status | holds when | listed by |
|---|---|---|
| `defect` | a negative reading on its route, live or a lapsed negative unresolved; an open severity-3 finding on it; or its own reason a negative or a retracted work | `defects` |
| `disputed` | a subject on its route disputed, or its own reason `disputed` | `unreviewed` |
| `conjectured` | standing `conjectured`: the paper does not assert it | none |
| `unsupported` | support status `unsupported`: unruled, ruled owed, a clearing ruling guarded, a circle, a restated conjecture, or its own gap | `unsupported-claims` |
| `lapsed`, `stale`, `unjudged`, `inconclusive` | its own reason, on its block or its route | `unreviewed` |
| `rests-on` | its reason on a node it rests on, which the status names | none: that claim is listed |
| `finding-open` | an open finding of severity 1 or 2, of an aspect that is status, on its route | none |
| `left-unargued` | the author's `left-unargued` ruling clears it | none (counted apart) |
| `established` | established: argued, or no argument owed | none |

An established claim resting on a cited work whose publication
`cited-publications` warns of, accepted or not, says so beside its status ("argued, on a preprint: `jones2025`"): its
verdict stands, its ground is weaker.

Each status a gate lists is listed by that gate alone ("each gate lists
what a person acts on, once"). Beside the status is its detail in the
records' words, and under it the next step, read from what is already
answered: a dismissed finding is never offered again. A finding refiled
when it stood again (`reaffirms`) keeps the id it was first filed under:
reads show that id with the filing it became, and every command that
takes a finding acts on its latest filing.
