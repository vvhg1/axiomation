"""Take a copy of somebody else's collection and make it ours.

axiomation reads a collection another tool built and will never write to it
(`upsert.guard` enforces it). That is the right relationship for a corpus
several repositories share, and it leaves one thing impossible: owning the same
content. A paper that wants to prune a stale key, add a source of its own, or
re-ingest one PDF has nowhere to do it.

Adopting is the answer, and the reason it is worth a command rather than a
re-ingest is arithmetic. The expensive half of building a corpus is the
embedding, and the vectors already exist. Copying them costs nothing at the
API; re-embedding the same PDFs would cost a full pass over every one of them
and produce the same numbers.

Three properties make it safe to point at a shared corpus:

- **The source is read-only, strictly.** Every call here scrolls or reads
  configuration. Nothing writes to it, and nothing creates a snapshot or any
  other artefact beside it — a server-side copy would have been faster and it
  would have put a file in shared infrastructure to get there.
- **The copy is verbatim.** The target is created with the source's own vector
  configuration, and points arrive with their ids, their payloads and both
  their vectors unchanged. An adopted collection answers a query the way the
  one it came from does, which is the only way to know the adoption worked.
- **The target is ours and says so.** The marker goes on last. Until it does,
  `guard` refuses the target as much as it refuses the source, so an
  interrupted adoption leaves a collection nothing will write to rather than
  a half-copy that looks finished.

What this does not do is decide what belongs in the corpus. After adopting,
`ingest --prune` drops the keys the bibliography no longer names and ingests
the ones it has gained; that is the same command a paper runs every week, and
adopting simply gives it something it is allowed to run against.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from qdrant_client import QdrantClient, models

from axiomation.sources.config import ResolvedSources, SourcesError
from axiomation.sources.contract import MARKER_FIELD
from axiomation.sources.library.qdrant import collection_names, read_marker
from axiomation.sources.library.upsert import CREATOR, write_marker

#: How many points move per round trip. Each one carries a 2048-float dense
#: vector and a page of text, so a larger batch buys little and costs memory.
COPY_BATCH = 256

Progress = Callable[[str], None]


@dataclass(frozen=True, slots=True)
class AdoptPlan:
    """What adopting would copy, and why it may not be allowed to."""

    source: str
    target: str
    points: int = 0
    create: bool = True
    vector_size: int = 0
    sparse: tuple[str, ...] = ()
    refusal: str = ""

    @property
    def blocked(self) -> bool:
        return bool(self.refusal)

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "target": self.target,
            "points": self.points,
            "create": self.create,
            "vector_size": self.vector_size,
            "sparse": list(self.sparse),
            "refusal": self.refusal,
        }


@dataclass(frozen=True, slots=True)
class AdoptReport:
    """What adopting did."""

    source: str
    target: str
    copied: int = 0
    created: bool = False
    marker: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "target": self.target,
            "copied": self.copied,
            "created": self.created,
            "marker": dict(self.marker),
        }


def plan_adopt(
    client: QdrantClient, settings: ResolvedSources, source: str
) -> AdoptPlan:
    """Decide whether this collection can be adopted, and what it holds."""
    existing = collection_names(client)
    target = settings.collection

    if source == target:
        return AdoptPlan(
            source=source,
            target=target,
            refusal=(
                f"{source} is already this paper's collection. Adopting copies "
                f"one collection into another; name a collection of this "
                f"paper's own in [sources].collection first."
            ),
        )
    if source not in existing:
        return AdoptPlan(
            source=source,
            target=target,
            refusal=f"no collection {source!r} to adopt at {settings.qdrant_url}.",
        )

    params = client.get_collection(source).config.params
    size = _dense_size(params)
    if size != settings.voyage_dims:
        return AdoptPlan(
            source=source,
            target=target,
            vector_size=size,
            refusal=(
                f"{source} holds vectors of {size} dimensions and this paper "
                f"asks for {settings.voyage_dims}. The vectors are the thing "
                f"being copied, so adopting across vector spaces would produce "
                f"a collection no query of this paper's can read. Set "
                f"[sources].voyage_model and voyage_dims to what built "
                f"{source}, or re-ingest instead of adopting."
            ),
        )

    refusal = _target_refusal(client, settings, target, existing)
    return AdoptPlan(
        source=source,
        target=target,
        points=client.count(source, exact=True).count,
        create=target not in existing,
        vector_size=size,
        sparse=tuple(sorted(getattr(params, "sparse_vectors", None) or ())),
        refusal=refusal,
    )


def _target_refusal(
    client: QdrantClient,
    settings: ResolvedSources,
    target: str,
    existing: list[str],
) -> str:
    """A target that is not ours is refused exactly as `ingest` refuses one."""
    if target not in existing:
        return ""
    marker = read_marker(client, target)
    if marker is None:
        return (
            f"{target} already exists and carries no axiomation marker, so "
            f"axiomation did not create it and will not write to it. Name a "
            f"collection that does not exist yet."
        )
    if marker.creator != CREATOR:
        return (
            f"{target} was created by {marker.creator!r}, not by axiomation, "
            f"and will not be written to."
        )
    if not marker.matches(settings):
        return (
            f"{target} holds {marker.voyage_model} vectors of "
            f"{marker.voyage_dims} dimensions; this paper asks for "
            f"{settings.voyage_model} of {settings.voyage_dims}."
        )
    return ""


def _dense_size(params: Any) -> int:
    """The size of the unnamed dense vector, whatever shape the config is in.

    A collection built with one unnamed vector reports `VectorParams`; one
    built with named vectors reports a mapping. Both are real, and a corpus
    with no dense vector at all is not one this can adopt.
    """
    vectors = getattr(params, "vectors", None)
    if vectors is None:
        return 0
    if isinstance(vectors, dict):
        chosen = vectors.get("") or next(iter(vectors.values()), None)
        return int(getattr(chosen, "size", 0))
    return int(getattr(vectors, "size", 0))


def adopt(
    client: QdrantClient,
    settings: ResolvedSources,
    plan: AdoptPlan,
    *,
    progress: Progress = lambda _: None,
) -> AdoptReport:
    """Copy the source into the target, then stamp the target as ours."""
    if plan.blocked:
        raise SourcesError(plan.refusal)

    created = False
    if plan.create:
        params = client.get_collection(plan.source).config.params
        client.create_collection(
            collection_name=plan.target,
            vectors_config=params.vectors,
            sparse_vectors_config=getattr(params, "sparse_vectors", None) or {},
        )
        created = True

    copied = _copy_points(client, plan, progress)
    # Last, deliberately: until the marker is on, `guard` refuses the target,
    # so an adoption that dies halfway leaves a collection nothing will write
    # to rather than a half-copy that looks finished.
    marker = write_marker(client, settings)
    progress(f"stamped {plan.target} as {CREATOR}")
    return AdoptReport(
        source=plan.source,
        target=plan.target,
        copied=copied,
        created=created,
        marker=marker.as_dict(),
    )


def _copy_points(client: QdrantClient, plan: AdoptPlan, progress: Progress) -> int:
    """Scroll the source and write each batch into the target, verbatim."""
    copied = 0
    offset = None
    while True:
        points, offset = client.scroll(
            plan.source,
            limit=COPY_BATCH,
            offset=offset,
            with_payload=True,
            with_vectors=True,
        )
        batch = [
            models.PointStruct(
                id=point.id, vector=point.vector, payload=dict(point.payload or {})
            )
            for point in points
            # The source's own creator stamp, if it has one, is a fact about
            # the source. Ours goes on at the end.
            if MARKER_FIELD not in (point.payload or {})
        ]
        if batch:
            client.upsert(collection_name=plan.target, points=batch)
            copied += len(batch)
            progress(f"copied {copied}/{plan.points}")
        if offset is None:
            return copied
