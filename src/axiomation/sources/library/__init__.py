"""The `[library]` extra: the cited works' corpus in Qdrant.

Qdrant holds each work's pages and their vectors; Voyage and fastembed
make the vectors, and `client` reads and searches them. The rest of
`axiomation.sources` is the core's: it reaches this package only at the
sites `tests/test_boundary.py` names, so a person who installs
`axiomation` alone never imports it.
"""
