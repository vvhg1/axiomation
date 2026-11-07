"""Three shapes the reader and the cutter both meet, each a whole small
paper written for these tests in our own words: a `$$…$$` display opening
and closing mid-line inside a proof's sentence, then a `that$` with no
space and a line ending in a space; a footnote whose argument holds
nested braces and a `\\citep{…}` of two keys, its sentence going on after
it; two bold step headings, the second's closing brace on the next line.
"""

INLINE_DISPLAY = (
    "## A bound on the averages\n"
    "\n"
    "\\label{lem:averages}\n"
    "\n"
    "\\begin{proof}\n"
    "Write $c_{n}$ for the running mean. Each term is at most one, so the mean\n"
    "settles on the limit $$\\sum_{j\\in J'}w_{n}^{j}g_{n}^{j}=a^{\\star}.$$\n"
    "That $a^{\\star}\\geq 0$ is clear, every weight being positive. Since the\n"
    "weights sum to one and$\\left| c_{n}\\right| \\le 1$ for all $n$,\n"
    "we get that $\\left| a^{\\star}\\right| \\le 1$ too. \n"
    "\\end{proof}\n"
)

FOOTNOTE = (
    "\\begin{document}\n"
    "\\subsection{Rating a forecast}\\label{sec:rating}\n"
    "We rate each forecast by a \\emph{score}\\footnote{Strictly, a score must"
    " have a \\emph{finite} mean under every forecast in $\\calP$, which we"
    " assume throughout~\\citep{smith2001scores,jones2012proper}.}"
    " $s : \\calP \\times \\calY \\to \\R$, a map from a forecast and an outcome"
    " to a number.\n"
    "\\end{document}\n"
)

STEP_HEADINGS = (
    "\\begin{document}\n"
    "\\section{Proof of the main result}\n"
    "\\textbf{Step 1: A bound for one round.}\n"
    "Fix a round $t$ and compare the new iterate \\(z_{t+1}\\) with the old"
    " one \\(z_t\\).\n"
    "\n"
    "\\textbf{Step 2: Summing over the rounds.\n"
    "}\n"
    "\n"
    "Adding the bounds of the first step over \\(t = 1, \\dots, T\\)"
    " telescopes the left side.\n"
    "\\end{document}\n"
)
