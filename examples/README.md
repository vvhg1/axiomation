# Examples

## `weak-law/`

A one-page paper written for axiomation's quick start: the weak law of
large numbers, proved from Chebyshev's inequality, in Markdown with one
`.bib` entry. Its results are classical and credited to Chebyshev
(1867); the text is our own.

The paper is short so that a first build and review cost little, and it
carries a few faults on purpose, so that each step has something to
show. They are listed here, outside the paper's directory, because the
agents read only that directory: nothing in the paper hints at them.

### What a run should find

- **The build:** the remark `rem:exponential` claims an exponential
  decay for bounded variables and gives no argument: its standing is
  `unsupported`. The assumption `ass:identical` is stated and never
  used: an unused premise, which `open` and `check` name.
- **The review** (`--session argument`): Markov's inequality,
  `lem:markov`, is stated for any random variable with a finite mean.
  It needs a nonnegative one, and the proof's step "elsewhere it is
  $0$" fails without it. Chebyshev's inequality applies it only to a
  square, so nothing downstream is wrong.
- **Not planted, found anyway:** the introduction's first sentence says
  independent measurements settle near their mean, without the finite
  variance the theorem needs; `cor:frequency` states its bound without
  naming the assumptions its proof uses (an undeclared assumption).

### The prepared edit

Weaken independence to pairwise uncorrelated: in `paper.md`, replace
the assumption `ass:independent` with

```latex
\begin{assumption}[Uncorrelated]\label{ass:independent}
The variables $X_1, X_2, \dots$ are pairwise uncorrelated:
$\operatorname{Cov}(X_i, X_j) = 0$ for $i \ne j$.
\end{assumption}
```

The update classifies the edit (the assumption widened, the lemma and
the theorem strengthened), and the judgments that rest on it lapse. Read
again, the lemma, the theorem and the corollary still hold, but two
things broke: the variance step still argues from independence, a
premise no longer in force; and the remark is now false, since
exponential decay needs independence, which pairwise uncorrelated
variables need not have. An edit that looks harmless breaks a claim
further down.

Run it on a copy, never in this checkout: a build writes the paper's
store, `axiomation/`, inside the paper's directory.

```sh
git clone --depth 1 https://github.com/vvhg1/axiomation
cp -R axiomation/examples/weak-law ~/weak-law
```
