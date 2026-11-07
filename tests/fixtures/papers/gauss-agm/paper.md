---
title: The Arithmetic–Geometric Mean and Its Common Limit
author:
  - A. Example
  - B. Example
date: 2026-10-15
header-includes:
  # a frame for the paper's one-paragraph summaries
  - \newmdenv[linewidth=1pt]{summarybox}
---

<!-- A paper written for axiomation's tests: a modern rewrite of a
public-domain result (C. F. Gauss, on the arithmetic-geometric mean,
1799), in our own words and notation; no source text is copied. It
carries the shapes the reader and the cutter are known to meet in a
Markdown paper (tests/papers/test_whole_papers.py). The work
`lectures2010` is invented, and so are the quotations and the page given
for it. -->

\begin{abstract}
Start from two positive numbers and replace them, again and again, by
their arithmetic and their geometric mean. We show that the two sequences
converge to one common limit, and that they do so quadratically.
<!-- @claim C001 from=thm:agm -->
The limit, the arithmetic–geometric mean, is a symmetric and homogeneous
function of the two starting values.
<!-- @claim C002 from=prop:homogeneous -->
\end{abstract}

## Introduction {#sec:intro}

Gauss computed the common limit of the two means for the pair $(1, \sqrt
2)$ to eleven decimals and recognised its reciprocal in a lemniscatic
integral \citep{gauss1799}. The iteration itself is elementary; what
makes it remarkable is its speed, which \citet[Chapter~1]{lectures2010}
call "the fastest elementary iteration that a pocket calculator can run
by hand".

> **In short.** The two means close on each other so fast that the number of correct
> digits doubles at every step \cite[p.~5]{lectures2010}.

**What we prove.** \Cref{thm:agm} gives the common limit,
\cref{lem:order,lem:gap} the two facts it rests on, and
\cref{sec:proof} the proof.[^history]

[^history]: The history is told in \cite{cox1984}.

*Note.*

The iteration is defined for positive numbers only; the reader who wants
complex starting values should consult \cite{cox1984}, where the choice of
square root matters.\footnote{For complex values the geometric mean has two
  candidates at each step; the \emph{right} choice is the one closer to
  the arithmetic mean \citep{cox1984,lectures2010}.} Here every root is the
positive one.

## Two means
\label{sec:means}

\begin{definition}[The iteration]\label{def:agm}
For $a, b > 0$ let $a_0 = a$, $b_0 = b$ and, for $n \ge 0$,
\begin{align}
  a_{n+1} &= \frac{a_n + b_n}{2}, \label{eq:arith}\\
  b_{n+1} &= \sqrt{a_n b_n}. \label{eq:geom}
\end{align}
\end{definition}

\begin{lemma}\label{lem:order}
For every $n \ge 1$,
$$b_n \le b_{n+1} \le a_{n+1} \le a_n.$$
\end{lemma}

\begin{proof}
**Step 1.** The geometric mean never exceeds the arithmetic one, since
$$a_{n+1} - b_{n+1} = \tfrac12\left(\sqrt{a_n} - \sqrt{b_n}\right)^2 \ge 0.$$

**Step 2.** Both means lie between $b_n$ and $a_n$, which gives the outer
inequalities.
\end{proof}

The gap between the two sequences shrinks fast. To see how fast, write
the difference of the next pair as

$$a_{n+1} - b_{n+1} = \frac{(a_n - b_n)^2}{8\,a_{n+2}},$$

which is the identity behind the next lemma.

\begin{lemma}\label{lem:gap}
For every $n \ge 1$, $0 \le a_{n+1} - b_{n+1} \le (a_n - b_n)^2 / (8 b_1)$.
\end{lemma}

\begin{proof}
By the identity above and $a_{n+2} \ge b_1$, which holds by
\cref{lem:order}. The case $n = 0$ is excluded, since $b_0$ may be the
larger of the two.
\end{proof}

The first iterates for $a = 1$, $b = \sqrt 2$:

| $n$ | $a_n$          | $b_n$          |
|-----|----------------|----------------|
| 0   | 1.000000000    | 1.414213562    |
| 1   | 1.207106781    | 1.189207115    |
| 2   | 1.198156948    | 1.198123521    |

: The two sequences after two steps agree to four digits. \label{tab:iterates}

## The common limit {#sec:limit}

\begin{theorem}[Gauss]\label{thm:agm}
The sequences $(a_n)$ and $(b_n)$ converge to a common limit $M(a, b)$,
and $a_n - b_n \to 0$ quadratically.
\end{theorem}

\begin{proposition}\label{prop:homogeneous}
$M(a, b) = M(b, a)$, and $M(\lambda a, \lambda b) = \lambda M(a, b)$ for
every $\lambda > 0$.
\end{proposition}

The proposition needs three observations:

1. the iteration treats $a$ and $b$ alike after its first step;
2. scaling both by $\lambda$ scales every iterate by $\lambda$, because
   both means are homogeneous of degree one:
   $$\frac{\lambda a + \lambda b}{2} = \lambda \frac{a + b}{2};$$
3. limits respect the scaling.
   - In particular the limit of a scaled pair is the scaled limit.
   - This uses only continuity of multiplication.

The constant
1799 is the year of Gauss's diary entry, not a step of the argument.

\begin{summarybox}
**In short.** Two means, taken again and again, close in on each other.

Each step at least squares the gap, by \cref{lem:gap}.
\end{summarybox}

\begin{mdframed}
A reader who wants only the result may stop at \cref{thm:agm}.
\end{mdframed}

\begin{exercise}
Compute $M(1, 2)$ to six digits with the code below.
\end{exercise}

\begin{remark}
The limit has the integral form
$$\frac{1}{M(a, b)} = \frac{2}{\pi} \int_0^{\pi/2} \frac{d\theta}{\sqrt{a^2\cos^2\theta + b^2\sin^2\theta}},$$
which we do not use.
\end{remark}

```python
# A check of the table above.
# <!-- @claim C999 from=thm:agm -->
a, b = 1.0, 2 ** 0.5
for _ in range(3):
    a, b = (a + b) / 2, (a * b) ** 0.5
```

# Appendix {#appendix .unnumbered}

## Proof of \cref{thm:agm} {#sec:proof}

By \cref{lem:order} the sequence $(a_n)$ decreases and is bounded below
by $b_1$, and $(b_n)$ increases and is bounded above by $a_1$; both
converge.

By \cref{lem:gap} the gaps satisfy $\varepsilon_{n+1} \le
\varepsilon_n^2 / (8 b_1)$, so they tend to zero, and quadratically.
Hence the limits agree. We get that
$a_n - b_n \to 0$ and the limits coincide $$\lim a_n = \lim b_n = M(a, b).$$
That $M$ is positive follows from $b_n \ge b_1 > 0$. % not a comment in Markdown
