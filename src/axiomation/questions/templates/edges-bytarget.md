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

## This pass: by target

`edges.md` holds the paper's edges as a first reader wrote them, block by block: for each block, what its own text relies on. Walk the sheet the other way, target by target. For every definition, assumption, lemma, theorem and named result in the paper, find every block whose own text relies on it: its symbol or its term used in the defined sense, a \ref or \eqref of its label, its name or number in words, a reference by position ('the lemma above'), a hypothesis it invokes, a standing assumption it works under. A block that glosses a common term of the field in passing is a reminder of the term, like a gloss of standard notation, not the paper's own definition: no edge targets it, and a block that uses the term has no edge onto the gloss. Where the sheet has no edge for that reliance, add one in the sheet's own form, with the block's words that show it, as the lines around it do. Where an existing edge points at the wrong target, correct it and say why in its note. For every claim, read the justifies edges onto it together, one per case the paper argues apart, each flagged `sketch` or `analogy` where its own words say so: correct a flag its words do not bear, and add one they do; an `analogy` uses the argument it carries over (the proof, or the group it closes; never the result's statement alone), else its own quote holds the citation. The ⊢ line shows each claim's handle; where a block holds several claims, name on the line the one it argues, supports or restates; where J's words do not say, name none. Two claims of one block never share their words: a standing's words are the quote that finds this claim in its sentence, not the sentence; quote what singles each reading out. Remove no line but one you correct, and change no standing: a standing a line you add argues follows it. When every target has been walked, finish, and reply with one line: how many edges you added and how many you corrected.
