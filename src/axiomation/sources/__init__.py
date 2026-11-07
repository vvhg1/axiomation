"""Source corpora: building one, and serving one to agents.

Two halves that never import each other.
`sources.ingest` builds a collection from a bibliography and is a batch
command a human runs. `sources.read` serves it. Below both sit the contract
the collection must satisfy and the settings that name it; the Qdrant
plumbing and the two embedding models are the `library` extra's
(`sources.library`).

This package is the core's, behind its evidence contract
(`axiomation.evidence`): `providers` gives the library (or, without it,
each work's PDF here) and the fetched copies to the core's `Sources`,
which stamps every page; `cli` is `axiomation sources`; `recheck` checks
the cited works' files again; `verify`
holds the quotation and locator verifiers. It may import the rest of the
core; the rest imports it only at its named sites, and it reaches the
`library` extra only at its own (`tests/test_boundary.py`).

A collection another tool built to the same contract is read as it
stands: axiomation reads it without re-ingesting anything, and never
writes to it.
"""
