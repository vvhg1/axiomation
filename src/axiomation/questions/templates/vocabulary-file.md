`vocabulary.md` lists names a paper uses that a program reading LaTeX does not know: environments it never declares or declares in a way the program does not read, and commands that may point at labels. Each entry quotes the name's declaration and its first uses in the paper's own words; answer from those.

The paper becomes an argument graph. The program cuts it into blocks: a statement environment (a theorem, a definition) is one block that states something, its proof attached; a container's body is cut as if the container were not there; a list is cut at its items; any other environment stays one block. A reference command becomes an edge to the label it names. Your answer tells the program how to read each name.

Each entry shows what the program found: how it found the name (`environment`, `macro` for a command the paper defines with a reference in its body, `use` for a command whose every argument is one of the paper's labels, `label-definer` for a command around a label that nothing else defines, `redefined` for another definition of a command listed for one of its definitions), its declaration where it found one, and its first uses: an environment whole (a long one cut short at `[...]`), a command with the text around it. Fill in its two last lines.

A name the paper declares more than once with different texts has an entry for each declaration, its heading numbered (``## command `lemref` (definition 2 of 2)``), and the uses shown are those where that declaration is in force: after it and before the next. Answer each from its own declaration and uses; the program reads each use so.

`- kind:` for an environment, one of:

- `statement <base>`: it bounds one statement, and means the standard environment `<base>`, one of theorem, proposition, lemma, corollary, conjecture, definition, remark, assumption, condition, fact; `statement` alone when it bounds a statement that is none of these (an example, an exercise).
- `wraps <environment>`: its declaration opens one environment the program knows and it is read as that one: `\newenvironment{mythm}{\begin{theorem}}{\end{theorem}}` is `wraps theorem`, a proof marked as a sketch is `wraps proof`. The environments the program knows: the standard statements and the paper's declared ones, proof, the lists, the displayed equations, figure, table, the frames and boxes it already reads (center, minipage, mdframed, framed and the like), the code listings.
- `container`: it only wraps its body, a frame, a box, or a layout or font switch around text; what is inside it is read as if it stood alone, whatever it holds (a law, a summary, a list, a table).
- `list <base>`: it lists `\item`s, built on enumerate, itemize or description; `list` alone when you cannot tell which.
- `none`: none of these: it sets its content as one piece, and stays one block (a table, a picture, a quotation).

`- kind:` for a command, one of:

- `reference`, followed by any of: `list` where it takes a comma list of labels; `prefix=<p>` where it puts `<p>` before its argument to make the label (a command `\lemref{main}` defined as `Lemma~\ref{lem:#1}` has `prefix=lem:`); `word=<w>` for the word it prints before the number (`word=Lemma`).
- `label`: it defines a label, as `\label` does, its name the first argument.
- `none`: it does neither.

`- evidence:` the paper's own words your answer rests on, in double quotes, exactly as they stand in one of its files: the declaration, a definition's body, a use. For instance:

````
## environment `summarybox`
- found: environment
- declared: `\newmdenv[linewidth=1pt]{summarybox}` (paper.md line 4)
- used (paper.md line 120):
```
\begin{summarybox}
**Summary.** Each step keeps the total fixed.
\end{summarybox}
```
- kind: container
- evidence: "\newmdenv[linewidth=1pt]{summarybox}"
````

Read what an environment holds before you answer: a box that holds a statement with a title and its parts is a `container` (its parts are read as they stand); a name that holds one statement and nothing else is a `statement`. Where a declaration or a definition says what the name is, answer what it says.

Change nothing else: not a heading, not the lines the program wrote, not their order. When you finish, `vocabulary.md` is checked; if the check reports problems, fix them.
