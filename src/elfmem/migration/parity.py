"""Migration Phase 4 — retrieval parity gate (plan doc §8).

Verifies that migrating from DB-native storage to the file substrate
(export via U-004, rebuild via U-002) does not change retrieval behaviour.
The plan doc names this gate explicitly: *"do not proceed on the assumption
that the new ranking is probably fine."*

Compares ``hybrid_retrieve()`` output between the pre-migration database and
the rebuilt one, for a caller-supplied set of fixed queries.
``hybrid_retrieve()`` is used deliberately instead of the higher-level
``recall()`` — ``recall()`` reinforces returned blocks and co-retrieved
edges as a side effect (mutating `reinforcement_count`, `last_reinforced_at`,
edge weights), which would both corrupt the "before" database mid-comparison
and make a second call return different results than the first. A parity
*gate* must be read-only; `hybrid_retrieve()` is the pure stage `recall()`
itself calls internally.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from elfmem.context.frames import FrameDefinition
from elfmem.db.queries import get_active_blocks
from elfmem.memory.retrieval import hybrid_retrieve
from elfmem.ports.services import EmbeddingService


@dataclass
class QueryParityCheck:
    query: str | None
    frame_name: str
    before_ids: list[str]
    after_ids: list[str]

    @property
    def matches(self) -> bool:
        return self.before_ids == self.after_ids


@dataclass
class ParityGateResult:
    block_count_before: int
    block_count_after: int
    query_checks: list[QueryParityCheck] = field(default_factory=list)
    # Edges in the LIVE database whose endpoint is not an active block.
    # `update_block_status` deletes a block's edges when it archives it, so
    # these should not exist; they survive from before that behaviour, or
    # from hand-archiving via SQL. They matter here because centrality is
    # normalised against the busiest block in the candidate set, so a stale
    # edge inflates one block's degree and reshuffles everyone else's rank.
    # A rebuild cannot reproduce them (archive/ is never re-read), so the
    # gate fails against a source that is not internally consistent -- which
    # is the source being wrong, not the rebuild.
    stale_edges_in_source: int = 0

    @property
    def block_count_matches(self) -> bool:
        return self.block_count_before == self.block_count_after

    @property
    def passed(self) -> bool:
        """The gate. False means stop and diagnose — never proceed past a
        diverging ranking on the assumption it's probably fine (plan doc §8)."""
        return self.block_count_matches and all(c.matches for c in self.query_checks)

    def diverging_queries(self) -> list[QueryParityCheck]:
        return [c for c in self.query_checks if not c.matches]

    @property
    def diagnosis(self) -> str | None:
        """Why the gate failed, when the cause is known. None if it passed."""
        if self.passed:
            return None
        if not self.block_count_matches:
            return (
                f"Block count differs: {self.block_count_before} -> "
                f"{self.block_count_after}. Check index check for parse errors."
            )
        if self.stale_edges_in_source:
            return (
                f"{self.stale_edges_in_source} edge(s) in the live database "
                "point at a non-active block. Those inflate centrality on the "
                "'before' side and a rebuild cannot reproduce them, so ranking "
                "diverges. This is a pre-existing inconsistency in the source "
                "(archiving is supposed to delete a block's edges). Verified "
                "cause on one real corpus: removing them made the gate pass. "
                "Repair with:\n"
                "    DELETE FROM edges WHERE EXISTS (SELECT 1 FROM blocks b "
                "WHERE b.id IN (edges.from_id, edges.to_id) "
                "AND b.status != 'active');"
            )
        return None


async def check_retrieval_parity(
    conn_before: AsyncConnection,
    conn_after: AsyncConnection,
    embedding_svc: EmbeddingService,
    queries: list[tuple[str | None, FrameDefinition]],
    *,
    current_active_hours: float = 0.0,
    top_k: int = 5,
) -> ParityGateResult:
    """Run the parity gate.

    USE WHEN: migration Phase 4 — after Phase 3's `elfmem index` rebuild,
        before Phase 5/6 (hand-restore + flip authority).
    DON'T USE WHEN: `conn_before`/`conn_after` point at the same live
        database — the whole point is comparing two distinct states
        (original vs. rebuilt), so `conn_after` must be a separate
        connection (fresh index.db, or the original before Phase 6's
        authority flip discards it).
    COST: two `hybrid_retrieve()` calls per query pair — one embedding call
        each if `query` is not None, zero LLM calls either way.
    RETURNS: `ParityGateResult` — check `.passed` before proceeding to
        Phase 5. `.diverging_queries()` names exactly what to diagnose if not.
    NEXT: `.passed` True → Phase 5 (hand-restore). `.passed` False → stop,
        per the plan doc's explicit instruction; do not proceed regardless.
    """
    before_count = len(await get_active_blocks(conn_before))
    after_count = len(await get_active_blocks(conn_after))

    checks: list[QueryParityCheck] = []
    for query, frame_def in queries:
        weights = (
            frame_def.weights
            if query is not None
            else frame_def.weights.renormalized_without_similarity()
        )
        tag_filter = (
            frame_def.filters.tag_patterns[0]
            if frame_def.filters.tag_patterns
            else None
        )

        before_scored = await hybrid_retrieve(
            conn_before,
            embedding_svc=embedding_svc,
            query=query,
            weights=weights,
            current_active_hours=current_active_hours,
            top_k=top_k,
            tag_filter=tag_filter,
        )
        after_scored = await hybrid_retrieve(
            conn_after,
            embedding_svc=embedding_svc,
            query=query,
            weights=weights,
            current_active_hours=current_active_hours,
            top_k=top_k,
            tag_filter=tag_filter,
        )

        checks.append(
            QueryParityCheck(
                query=query,
                frame_name=frame_def.name,
                before_ids=[b.id for b in before_scored[:top_k]],
                after_ids=[b.id for b in after_scored[:top_k]],
            )
        )

    stale_edges = (await conn_before.execute(
        text(
            "SELECT COUNT(*) FROM edges WHERE EXISTS ("
            "  SELECT 1 FROM blocks b "
            "  WHERE b.id IN (edges.from_id, edges.to_id) AND b.status != 'active')"
        )
    )).scalar() or 0

    return ParityGateResult(
        block_count_before=before_count,
        block_count_after=after_count,
        query_checks=checks,
        stale_edges_in_source=int(stale_edges),
    )
