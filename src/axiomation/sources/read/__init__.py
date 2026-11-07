"""Serving a source collection to agents.

Read-only by construction: no upsert and no delete exists in this package,
and nothing here imports `sources.ingest`. What this read path adds over
a plain hybrid search is a `read_status` on every result, the Zotero
adapter, and tool names of axiomation's own so a monorepo can register
both servers side by side.
"""
