## environment `lawbox`
- found: environment
- declared: `\newenvironment{lawbox}{\hrule}{\hrule}` (paper.md line 4)
- used (paper.md line 12):
```
\begin{lawbox}
**First law.** Weight is kept.

It holds always.
\end{lawbox}
```
- kind: ?
- evidence: ?

## environment `example`
- found: environment
- used (paper.md line 18):
```
\begin{example}
Take two coins.
\end{example}
```
- kind: ?
- evidence: ?

## command `lemref`
- found: macro
- declared: `\newcommand{\lemref}[1]{\lemmaname~\ref{lem:#1}}` (paper.md line 5)
- used (paper.md line 22):
```
-includes:
  - \newenvironment{lawbox}{\hrule}{\hrule}
  - \newcommand{\lemref}[1]{\lemmaname~\ref{lem:#1}}
---

\begin{lemma}\label{lem:key}
The gap shrinks.
\end{lemma}

\begin{lawbox}
**First law.** Weight is kept.

It holds always.
\end{lawbox}

\begin{example}
Take two coins.
\end{example}

By \lemref{key} the gap shrinks.

```
- kind: ?
- evidence: ?
