`vocabulary-check.md` lists names in a paper that an earlier reader answered `none`: an environment it read as neither a statement, a wrapper, a container nor a list, or a command it read as neither a reference nor a label. Each entry shows the earlier answer and its evidence, and the name's uses in the paper's words. Judge each answer.

What the kinds mean. For an environment: `statement <base>` bounds one statement and means the standard environment `<base>` (theorem, proposition, lemma, corollary, conjecture, definition, remark, assumption, condition, fact), `statement` alone one that is none of these (an example, an exercise); `wraps <environment>` is read as the one environment its declaration opens (`wraps proof` for a proof under another name); `container` only wraps its body, a frame, a box, or a layout or font switch around text, whatever it holds; `list <base>` lists `\item`s, built on enumerate, itemize or description; `none` sets its content as one piece (a table, a picture, a quotation). For a command: `reference`, with `list`, `prefix=<p>` and `word=<w>` where they apply, points at labels; `label` defines one; `none` does neither.

Fill in each entry's two last lines:

- `- verdict: confirm` where `none` is right; leave `- evidence: ?` as it is.
- `- verdict: overturn <kind>` where it is not, with the kind as above, and `- evidence:` the paper's own words, in double quotes, exactly as they stand in the uses shown, that show it.

Change nothing else. When you finish, `vocabulary-check.md` is checked; if the check reports problems, fix them.
