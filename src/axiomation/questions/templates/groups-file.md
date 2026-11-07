`groups.md` is a paper cut into blocks, or a part of one. Each block starts at a marker line such as `⟦b12 claim⟧`: its handle, and the role it plays in the paper's argument. A line `⟦+ justification⟧` marks where a later part of a block starts, with that part's role. Blocks under `⟦context⟧` lie around the part, for reading only.

The paper becomes an argument graph, each block a node. A group is a run of two or more consecutive blocks judged as one whole: "is this proof correct?" is one verdict on the whole run. A single block is a node of its own and needs no group.

A group starts with a line `⟦group <kind> <name>⟧` just before the marker of its first block, and ends with a line `⟦end group <name>⟧` just after the text of its last block. The name is a short one in snake case, such as `compactness_proof`; each group in the file has its own. For instance:

```
⟦group proof bound_proof⟧

⟦b13 justification⟧
Proof. Since the gain is below one, ...

⟦b14 justification⟧
Hence the output is bounded.

⟦end group bound_proof⟧
```

The kinds:

- `proof`: a proof, whether a proof environment or the verification that follows a claim in an example;
- `proof-section`: a proof written as a section or a run of sections;
- `derivation`: a derivation that establishes a result stated after it, or an argument for a result given only in the running text.

A program proposed the groups in the file from the layout alone, and named them by their first words. Correct them:

- Group a run that is judged as one, where no group does.
- Move a group's lines where its run starts or ends elsewhere.
- Remove a group that is not judged as one, such as a run of step headings inside a proof that is already a group, or a run that justifies nothing.
- A result the text only promises, or proves only "analogously", gets no group.
- A group starts at the first block of its argument, not at a heading.
- A figure or a table and the text that reads it are not a group.
- A group holds whole blocks, two or more, every block from its first to its last, and no block under `⟦context⟧`. Groups may nest; they may not partly overlap.

You may flag what you see about the cut or the groups and cannot settle by a group line, on its own line under a block's marker, for instance `⟦? two-block "<the block's own words>" — <what you read there>⟧`. The signal is `two-block` where a group's two blocks may be one block, `run-unclear` where a run's start or end is hard to tell, or no word for anything else. A flag changes nothing here; a later step reads it.

Change nothing else: not the text, not the marker lines, not any other line. A flag is a line you add under a block's marker, never a change to one. When you finish, `groups.md` is checked; if the check reports problems, fix them.
