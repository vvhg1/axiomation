`paper.md` is a paper cut into blocks, or a part of one. Each block starts at a marker line such as `⟦b12 claim⟧`: its handle, and the role it plays in the paper's argument. A line `⟦+ justification⟧` marks where a later part of a block starts, with that part's role. Text under a `⟦context⟧` line lies around the part, for reading only. Read `paper.md`; do not change it.

The paper becomes an argument graph, each block a node. A group is a run of two or more consecutive blocks judged as one whole: "is this proof correct?" is one verdict on the whole run. A single block is a node of its own and needs no group.

The kinds:

- `proof`: a proof, whether a proof environment or the verification that follows a claim in an example;
- `proof-section`: a proof written as a section or a run of sections;
- `derivation`: a derivation that establishes a result stated after it, or an argument for a result given only in the running text.

A program proposed the groups `paper.md` shows, from the layout alone: a line `⟦group <kind> <name>⟧` before a group's first block and `⟦end group <name>⟧` after its last. Correct them:

- Group a run that is judged as one, where no group does.
- Move a group where its run starts or ends elsewhere.
- Remove a group that is not judged as one, such as a run of step headings inside a proof that is already a group, or a run that justifies nothing.
- A result the text only promises, or proves only "analogously", gets no group.
- A group starts at the first block of its argument, not at a heading.
- A figure or a table and the text that reads it are not a group.
- A group holds whole blocks, two or more, every block from its first to its last, and no text under `⟦context⟧`. Groups may nest; they may not partly overlap.

Write in `groups.md` every group the paper has, one a line: its kind, its name, its first block and its last block.

    group proof bound_proof b13 b14

Keep a proposed group's name where you keep the group; give a new group a short name in snake case, such as `compactness_proof`. A proposed group you leave out is removed. A paper with no group is the one line `none`.

You may flag what you see about the cut or the groups and cannot settle by a group line, on a line of its own: `b12 ? two-block "<the block's own words>" — <what you read there>`. The signal is `two-block` where a group's two blocks may be one block, `run-unclear` where a run's start or end is hard to tell, or no word for anything else. A flag changes nothing here; a later step reads it.

Write `groups.md` whole, then finish. When you finish, a check reads `groups.md` and hands back the lines that do not read; fix those, keep the rest, and finish again.
