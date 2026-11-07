"""Building a source collection: bib, PDF, chunks, vectors, upsert.

The module-level constants are `ResolvedSources` fields and the OCR backend
is recorded per chunk rather than per document. The collection contract is
the only thing this and another tool writing the same collections must
keep agreeing on.

Ingestion is a batch command a human runs. No agent reaches it, and nothing
in `sources.read` imports this package.
"""
