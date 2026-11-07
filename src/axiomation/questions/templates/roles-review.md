`roles.md` is a paper cut into blocks, with a role given to each. Each block starts at a marker line such as `⟦b12 claim⟧`: the role its first part plays in the paper's argument. A line `⟦+ justification⟧` marks where a part with another role starts.

Review the roles and change them where they are wrong: edit the role in a marker, and add, change or delete `⟦+ <role>⟧` lines.

A role is what a span does in the argument of a paper, not how it is typeset.

- definition: It fixes the meaning of a symbol, term, object or procedure.
- assumption: It restricts where claims hold: a standing assumption, a scope restriction, or a requirement on a chosen parameter. A hypothesis inside a theorem or lemma is that statement's own content, not an assumption here.
- claim: It asserts something true in its own right: a result, a claim about the literature or the method, a recommendation, or an interpretation, rather than serving as the reason for another node.
- justification: It offers support for another node: a proof, a derivation, a citation, evidence from data or a figure, or a reason given for why a claim holds.
- motivation: It argues that something is worth doing or appropriate, as a reason for a choice such as a definition, an assumption or a method, and it confers no support on any claim.
- none: It plays none of these, such as a heading or a transition.

A statement the paper asserts as true is a claim even where it also motivates a choice or gives background: mark it claim, and give a motivation part only to a span that argues for a choice.

You may flag what you see about the cut or a block's role and cannot settle by a role, on its own line under the block's marker, for instance `⟦? split "<the block's own words>" — <what you read there>⟧`. The signal is `split` where the block may hold two units, `merge` where it may be one unit with a neighbour, `role-doubt` where its role is hard to tell, or no word for anything else. A flag changes nothing here; a later step reads it.

Change nothing else: not the text, not the marker lines, not any other line. A flag is a line you add under a block's marker, never a change to one. Every block keeps a role.
