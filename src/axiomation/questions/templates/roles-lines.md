`paper.md` is a paper cut into blocks, or a part of one. Each block starts at a marker line `⟦b12⟧`. Text under a `⟦context⟧` line lies around the part, for reading only. Read `paper.md`; do not change it.

Give each block the role it plays in the paper's argument, in `roles.md`, one line a block, in the order of `paper.md`: the block's handle and its role, for instance `b12 claim`. Where a block plays more than one role, write the role of its first part, then each later part after a `|`: its role, and the block's own words where that part starts, in double quotes, exactly as they stand in `paper.md`:

    b12 claim
    b13 justification | claim "Hence the bound holds"

A role is what a span does in the argument of a paper, not how it is typeset.

- definition: It fixes the meaning of a symbol, term, object or procedure.
- assumption: It restricts where claims hold: a standing assumption, a scope restriction, or a requirement on a chosen parameter. A hypothesis inside a theorem or lemma is that statement's own content, not an assumption here.
- claim: It asserts something true in its own right: a result, a claim about the literature or the method, a recommendation, or an interpretation, rather than serving as the reason for another node.
- justification: It offers support for another node: a proof, a derivation, a citation, evidence from data or a figure, or a reason given for why a claim holds.
- motivation: It argues that something is worth doing or appropriate, as a reason for a choice such as a definition, an assumption or a method, and it confers no support on any claim.
- none: It plays none of these, such as a heading or a transition.

A statement the paper asserts as true is a claim even where it also motivates a choice or gives background: mark it claim, and give a motivation part only to a span that argues for a choice.

You may flag what you see about the cut or a block's role and cannot settle by a role, on a line of its own after the block's line: `b12 ? split "<the block's own words>" — <what you read there>`. The signal is `split` where the block may hold two units, `merge` where it may be one unit with a neighbour, `role-doubt` where its role is hard to tell, or no word for anything else. A flag changes nothing here; a later step reads it.

Every block gets one roles line. Write `roles.md` whole, then finish. When you finish, a check reads `roles.md` and hands back the lines that do not read; fix those, keep the rest, and finish again.
