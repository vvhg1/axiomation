---
title: A Weak Law of Large Numbers
---

## Introduction {#sec:intro}

The mean of many independent measurements settles near their common
expectation. This note proves the weak law of large numbers from
Chebyshev's inequality, which in turn rests on a one-line bound for the
tail of a random variable. The inequality and the law it gives go back to
Chebyshev \cite{chebyshev1867}.

## Setting {#sec:setting}

Throughout, $X_1, X_2, \dots$ are real random variables on one
probability space, and
$$\bar X_n = \frac{1}{n} \sum_{i=1}^{n} X_i$$
is the mean of the first $n$ of them.

\begin{assumption}[Moments]\label{ass:moments}
Each $X_i$ has mean $\mu$ and finite variance $\sigma^2$, the same for
every $i$.
\end{assumption}

\begin{assumption}[Independence]\label{ass:independent}
The variables $X_1, X_2, \dots$ are independent.
\end{assumption}

\begin{assumption}[Identical laws]\label{ass:identical}
The variables $X_1, X_2, \dots$ all have the same distribution.
\end{assumption}

## Two inequalities {#sec:inequalities}

\begin{lemma}[Markov]\label{lem:markov}
Let $Y$ be a random variable with finite mean. Then for every $a > 0$,
$$P(Y \ge a) \le \frac{E[Y]}{a}.$$
\end{lemma}

\begin{proof}
Write $\mathbf{1}\{Y \ge a\}$ for the indicator of the event $Y \ge a$.
Pointwise, $Y \ge a \, \mathbf{1}\{Y \ge a\}$: where $Y \ge a$ the right
side is $a$, and elsewhere it is $0$. Taking expectations gives
$E[Y] \ge a \, P(Y \ge a)$.
\end{proof}

\begin{lemma}[Chebyshev]\label{lem:chebyshev}
Let $Y$ have mean $m$ and finite variance $v$. Then for every
$\varepsilon > 0$,
$$P(|Y - m| \ge \varepsilon) \le \frac{v}{\varepsilon^2}.$$
\end{lemma}

\begin{proof}
The event $|Y - m| \ge \varepsilon$ is the event
$(Y - m)^2 \ge \varepsilon^2$. Apply \cref{lem:markov} to $(Y - m)^2$,
whose mean is $v$, with $a = \varepsilon^2$.
\end{proof}

## The mean of many variables {#sec:mean}

\begin{lemma}\label{lem:mean-variance}
Under \cref{ass:moments,ass:independent}, $\bar X_n$ has mean $\mu$ and
variance $\sigma^2 / n$.
\end{lemma}

\begin{proof}
The mean follows from the linearity of expectation. For the variance,
$$\operatorname{Var}\Big(\sum_{i=1}^{n} X_i\Big)
  = \sum_{i=1}^{n} \operatorname{Var}(X_i)
  + \sum_{i \ne j} \operatorname{Cov}(X_i, X_j),$$
and each covariance vanishes, because independent variables are
uncorrelated. So the sum has variance $n \sigma^2$, and dividing the sum
by $n$ divides its variance by $n^2$.
\end{proof}

## The weak law {#sec:law}

\begin{theorem}[Weak law of large numbers]\label{thm:weak-law}
Under \cref{ass:moments,ass:independent}, for every $\varepsilon > 0$,
$$P(|\bar X_n - \mu| \ge \varepsilon) \le \frac{\sigma^2}{n \varepsilon^2},$$
which tends to $0$ as $n \to \infty$.
\end{theorem}

\begin{proof}
Apply \cref{lem:chebyshev} to $Y = \bar X_n$, whose mean and variance
\cref{lem:mean-variance} gives.
\end{proof}

\begin{corollary}\label{cor:frequency}
Let $A_1, A_2, \dots$ be independent events, each of probability $p$, and
let $F_n$ be the fraction of $A_1, \dots, A_n$ that occur. Then for every
$\varepsilon > 0$,
$$P(|F_n - p| \ge \varepsilon) \le \frac{1}{4 n \varepsilon^2}.$$
\end{corollary}

\begin{proof}
The indicators $X_i = \mathbf{1}_{A_i}$ satisfy
\cref{ass:moments,ass:independent} with $\mu = p$ and
$\sigma^2 = p(1 - p) \le 1/4$, and $F_n = \bar X_n$. \Cref{thm:weak-law}
gives the bound.
\end{proof}

\begin{remark}\label{rem:exponential}
For variables with values in $[0, 1]$, the probability in
\cref{thm:weak-law} decays exponentially in $n$, so for them the bound of
order $1/n$ is far from the truth.
\end{remark}
