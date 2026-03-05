"""Named async query functions for all elfmem database operations."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

import numpy as np
from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from elfmem.db.models import (
    block_outcomes,
    block_tags,
    blocks,
    contradictions,
    edges,
    frames,
    sessions,
    system_config,
)

# ── Helpers ───────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def content_hash(content: str) -> str:
    """Compute content-addressable block ID: sha256(normalised_content)[:16].

    Normalisation: strip leading/trailing whitespace, lowercase.
    """
    normalised = content.strip().lower()
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()[:16]


def embedding_to_bytes(vec: np.ndarray) -> bytes:
    """Convert a numpy float32 vector to bytes for BLOB storage."""
    return vec.astype(np.float32).tobytes()


def bytes_to_embedding(data: bytes) -> np.ndarray:
    """Convert BLOB bytes back to a numpy float32 vector."""
    return np.frombuffer(data, dtype=np.float32)


# ── Block queries ─────────────────────────────────────────────────────────────

async def insert_block(
    conn: AsyncConnection,
    *,
    block_id: str,
    content: str,
    category: str,
    source: str,
    status: str = "inbox",
    confidence: float = 0.50,
    decay_lambda: float = 0.01,
    last_reinforced_at: float = 0.0,
) -> None:
    """Insert a new block. Raises IntegrityError if id already exists."""
    await conn.execute(
        insert(blocks).values(
            id=block_id,
            content=content,
            category=category,
            source=source,
            status=status,
            confidence=confidence,
            reinforcement_count=0,
            decay_lambda=decay_lambda,
            last_reinforced_at=last_reinforced_at,
            created_at=_now_iso(),
        )
    )


async def get_block(conn: AsyncConnection, block_id: str) -> dict[str, Any] | None:
    """Fetch a single block by id. Returns None if not found."""
    result = await conn.execute(select(blocks).where(blocks.c.id == block_id))
    row = result.mappings().first()
    return dict(row) if row is not None else None


async def block_exists(conn: AsyncConnection, block_id: str) -> bool:
    """Check if a block id exists (any status)."""
    result = await conn.execute(
        select(blocks.c.id).where(blocks.c.id == block_id).limit(1)
    )
    return result.first() is not None


async def get_active_blocks(
    conn: AsyncConnection,
    *,
    min_last_reinforced_at: float | None = None,
) -> list[dict[str, Any]]:
    """Fetch all active blocks, optionally filtered by recency cutoff."""
    stmt = select(blocks).where(blocks.c.status == "active")
    if min_last_reinforced_at is not None:
        stmt = stmt.where(blocks.c.last_reinforced_at > min_last_reinforced_at)
    result = await conn.execute(stmt)
    return [dict(row) for row in result.mappings()]


async def get_active_blocks_with_embeddings(
    conn: AsyncConnection,
    *,
    min_last_reinforced_at: float | None = None,
) -> list[dict[str, Any]]:
    """Fetch active blocks that have non-NULL embeddings."""
    stmt = select(blocks).where(
        and_(blocks.c.status == "active", blocks.c.embedding.is_not(None))
    )
    if min_last_reinforced_at is not None:
        stmt = stmt.where(blocks.c.last_reinforced_at > min_last_reinforced_at)
    result = await conn.execute(stmt)
    return [dict(row) for row in result.mappings()]


async def get_inbox_blocks(conn: AsyncConnection) -> list[dict[str, Any]]:
    """Fetch all blocks with status='inbox'. Used by consolidate()."""
    result = await conn.execute(select(blocks).where(blocks.c.status == "inbox"))
    return [dict(row) for row in result.mappings()]


async def get_inbox_count(conn: AsyncConnection) -> int:
    """Count of inbox blocks."""
    result = await conn.execute(
        select(func.count()).select_from(blocks).where(blocks.c.status == "inbox")
    )
    return result.scalar() or 0


async def get_block_counts(conn: AsyncConnection) -> dict[str, int]:
    """Return block counts grouped by status in a single query.

    Returns a dict with keys 'inbox', 'active', 'archived', each defaulting
    to 0 if no blocks of that status exist.
    """
    result = await conn.execute(
        select(blocks.c.status, func.count().label("n")).group_by(blocks.c.status)
    )
    counts: dict[str, int] = {"inbox": 0, "active": 0, "archived": 0}
    for row in result.mappings():
        status = row["status"]
        if status in counts:
            counts[status] = row["n"]
    return counts


async def update_block_status(
    conn: AsyncConnection,
    block_id: str,
    status: str,
    *,
    archive_reason: str | None = None,
) -> None:
    """Transition a block to a new status.

    When archiving, explicitly deletes related tags, edges, and contradictions
    since FK CASCADE only fires on physical DELETE, not on status UPDATE.
    """
    if status == "archived":
        await conn.execute(
            delete(block_tags).where(block_tags.c.block_id == block_id)
        )
        await conn.execute(
            delete(edges).where(
                or_(edges.c.from_id == block_id, edges.c.to_id == block_id)
            )
        )
        await conn.execute(
            delete(contradictions).where(
                or_(
                    contradictions.c.block_a_id == block_id,
                    contradictions.c.block_b_id == block_id,
                )
            )
        )
    values: dict[str, object] = {"status": status}
    if archive_reason is not None:
        values["archive_reason"] = archive_reason
    await conn.execute(update(blocks).where(blocks.c.id == block_id).values(**values))


async def update_block_scoring(
    conn: AsyncConnection,
    block_id: str,
    *,
    confidence: float | None = None,
    self_alignment: float | None = None,
    decay_lambda: float | None = None,
    embedding: np.ndarray | None = None,
    embedding_model: str | None = None,
    token_count: int | None = None,
) -> None:
    """Update scoring-related fields after consolidation (partial update).

    Only updates fields that are not None.
    """
    values: dict[str, object] = {}
    if confidence is not None:
        values["confidence"] = confidence
    if self_alignment is not None:
        values["self_alignment"] = self_alignment
    if decay_lambda is not None:
        values["decay_lambda"] = decay_lambda
    if embedding is not None:
        values["embedding"] = embedding_to_bytes(embedding)
    if embedding_model is not None:
        values["embedding_model"] = embedding_model
    if token_count is not None:
        values["token_count"] = token_count
    if values:
        await conn.execute(
            update(blocks).where(blocks.c.id == block_id).values(**values)
        )


async def update_block_outcome(
    conn: AsyncConnection,
    *,
    block_id: str,
    new_confidence: float,
    new_outcome_evidence: float,
) -> None:
    """Update a block's confidence and outcome_evidence after a Bayesian update."""
    await conn.execute(
        update(blocks)
        .where(blocks.c.id == block_id)
        .values(confidence=new_confidence, outcome_evidence=new_outcome_evidence)
    )


async def insert_block_outcome(
    conn: AsyncConnection,
    *,
    block_id: str,
    signal: float,
    weight: float,
    source: str,
    confidence_before: float,
    confidence_after: float,
) -> None:
    """Append an outcome audit record to block_outcomes."""
    await conn.execute(
        insert(block_outcomes).values(
            id=uuid.uuid4().hex,
            block_id=block_id,
            signal=signal,
            weight=weight,
            source=source,
            confidence_before=confidence_before,
            confidence_after=confidence_after,
            created_at=_now_iso(),
        )
    )


async def reinforce_blocks(
    conn: AsyncConnection,
    block_ids: list[str],
    current_active_hours: float,
) -> None:
    """Reinforce a set of blocks: increment count, update last_reinforced_at."""
    await conn.execute(
        update(blocks)
        .where(blocks.c.id.in_(block_ids))
        .values(
            reinforcement_count=blocks.c.reinforcement_count + 1,
            last_reinforced_at=current_active_hours,
        )
    )


# DURABLE λ=0.001, PERMANENT λ=0.00001 — both at or below this floor are protected.
_DURABLE_LAMBDA_THRESHOLD: float = 0.001


async def accelerate_block_decay(
    conn: AsyncConnection,
    block_ids: list[str],
    penalty_factor: float,
    lambda_ceiling: float,
) -> list[tuple[str, float, float]]:
    """Multiply decay_lambda by penalty_factor for STANDARD/EPHEMERAL tier blocks.

    Skips non-active blocks and DURABLE/PERMANENT tier blocks
    (decay_lambda <= 0.001).

    Returns list of (block_id, lambda_before, lambda_after) for audit.
    """
    audit: list[tuple[str, float, float]] = []
    for block_id in block_ids:
        block = await get_block(conn, block_id)
        if block is None or block["status"] != "active":
            continue
        current_lambda = float(block["decay_lambda"])
        if current_lambda <= _DURABLE_LAMBDA_THRESHOLD:
            continue  # DURABLE or PERMANENT — protected
        new_lambda = min(current_lambda * penalty_factor, lambda_ceiling)
        await update_block_scoring(conn, block_id, decay_lambda=new_lambda)
        audit.append((block_id, current_lambda, new_lambda))
    return audit


# ── Tag queries ───────────────────────────────────────────────────────────────

async def add_tags(
    conn: AsyncConnection,
    block_id: str,
    tags: list[str],
) -> None:
    """Add tags to a block. Silently ignores duplicates (INSERT OR IGNORE)."""
    for tag in tags:
        stmt = insert(block_tags).values(block_id=block_id, tag=tag)
        await conn.execute(stmt.on_conflict_do_nothing())


async def get_tags(conn: AsyncConnection, block_id: str) -> list[str]:
    """Get all tags for a block, sorted alphabetically."""
    result = await conn.execute(
        select(block_tags.c.tag)
        .where(block_tags.c.block_id == block_id)
        .order_by(block_tags.c.tag)
    )
    return [row[0] for row in result]


async def get_blocks_by_tag_pattern(
    conn: AsyncConnection,
    pattern: str,
) -> list[str]:
    """Get block IDs matching a tag pattern (SQL LIKE). Example: 'self/%'."""
    result = await conn.execute(
        select(block_tags.c.block_id)
        .where(block_tags.c.tag.like(pattern))
        .distinct()
        .order_by(block_tags.c.block_id)
    )
    return [row[0] for row in result]


async def remove_tags(
    conn: AsyncConnection,
    block_id: str,
    tags: list[str],
) -> None:
    """Remove specific tags from a block."""
    await conn.execute(
        delete(block_tags).where(
            and_(block_tags.c.block_id == block_id, block_tags.c.tag.in_(tags))
        )
    )


# ── Edge queries ──────────────────────────────────────────────────────────────

async def insert_edge(
    conn: AsyncConnection,
    *,
    from_id: str,
    to_id: str,
    weight: float,
) -> None:
    """Insert an edge. from_id < to_id must be enforced by caller (canonical order)."""
    await conn.execute(
        insert(edges).values(
            from_id=from_id,
            to_id=to_id,
            weight=weight,
            reinforcement_count=0,
            created_at=_now_iso(),
        )
    )


async def get_edges_for_block(conn: AsyncConnection, block_id: str) -> list[dict[str, Any]]:
    """Get all edges where block_id is either endpoint."""
    result = await conn.execute(
        select(edges).where(
            or_(edges.c.from_id == block_id, edges.c.to_id == block_id)
        )
    )
    return [dict(row) for row in result.mappings()]


async def get_edges(conn: AsyncConnection, block_id: str) -> list[dict[str, Any]]:
    """Alias for get_edges_for_block."""
    return await get_edges_for_block(conn, block_id)


async def get_archived_blocks(conn: AsyncConnection) -> list[dict[str, Any]]:
    """Fetch all blocks with status='archived'."""
    result = await conn.execute(select(blocks).where(blocks.c.status == "archived"))
    return [dict(row) for row in result.mappings()]


async def prune_weak_edges(conn: AsyncConnection, threshold: float) -> int:
    """Delete edges with weight < threshold. Returns number of edges deleted."""
    result = await conn.execute(
        delete(edges).where(edges.c.weight < threshold)
    )
    return result.rowcount or 0


async def get_neighbours(conn: AsyncConnection, block_ids: list[str]) -> list[str]:
    """Get 1-hop neighbour block IDs for seed blocks, excluding seeds themselves."""
    from_result = await conn.execute(
        select(edges.c.to_id.label("neighbour")).where(edges.c.from_id.in_(block_ids))
    )
    to_result = await conn.execute(
        select(edges.c.from_id.label("neighbour")).where(edges.c.to_id.in_(block_ids))
    )
    seed_set = set(block_ids)
    neighbours = {row[0] for row in from_result} | {row[0] for row in to_result}
    return list(neighbours - seed_set)


async def reinforce_edges(
    conn: AsyncConnection,
    edge_pairs: list[tuple[str, str]],
) -> None:
    """Reinforce co-retrieval edges: increment reinforcement_count."""
    for from_id, to_id in edge_pairs:
        await conn.execute(
            update(edges)
            .where(and_(edges.c.from_id == from_id, edges.c.to_id == to_id))
            .values(reinforcement_count=edges.c.reinforcement_count + 1)
        )


async def get_weighted_degree(
    conn: AsyncConnection,
    block_ids: list[str],
) -> dict[str, float]:
    """Compute weighted degree (sum of edge weights) for a set of blocks.

    Returns {block_id: total_weight}. Blocks with no edges return 0.0.
    """
    degrees: dict[str, float] = {bid: 0.0 for bid in block_ids}
    from_result = await conn.execute(
        select(edges.c.from_id.label("bid"), func.sum(edges.c.weight).label("total"))
        .where(edges.c.from_id.in_(block_ids))
        .group_by(edges.c.from_id)
    )
    for row in from_result.mappings():
        degrees[row["bid"]] += float(row["total"])
    to_result = await conn.execute(
        select(edges.c.to_id.label("bid"), func.sum(edges.c.weight).label("total"))
        .where(edges.c.to_id.in_(block_ids))
        .group_by(edges.c.to_id)
    )
    for row in to_result.mappings():
        degrees[row["bid"]] += float(row["total"])
    return degrees


# ── Contradiction queries ─────────────────────────────────────────────────────

async def insert_contradiction(
    conn: AsyncConnection,
    *,
    block_a_id: str,
    block_b_id: str,
    score: float,
) -> None:
    """Insert a contradiction record."""
    await conn.execute(
        insert(contradictions).values(
            block_a_id=block_a_id,
            block_b_id=block_b_id,
            score=score,
            resolved=0,
            created_at=_now_iso(),
        )
    )


async def get_contradictions_for_blocks(
    conn: AsyncConnection,
    block_ids: list[str],
) -> list[dict[str, Any]]:
    """Get unresolved contradictions involving any of the given block IDs."""
    result = await conn.execute(
        select(contradictions).where(
            and_(
                contradictions.c.resolved == 0,
                or_(
                    contradictions.c.block_a_id.in_(block_ids),
                    contradictions.c.block_b_id.in_(block_ids),
                ),
            )
        )
    )
    return [dict(row) for row in result.mappings()]


async def resolve_contradiction(
    conn: AsyncConnection,
    block_a_id: str,
    block_b_id: str,
) -> None:
    """Mark a contradiction as resolved."""
    await conn.execute(
        update(contradictions)
        .where(
            and_(
                contradictions.c.block_a_id == block_a_id,
                contradictions.c.block_b_id == block_b_id,
            )
        )
        .values(resolved=1)
    )


# ── Frame queries ─────────────────────────────────────────────────────────────

async def get_frame(conn: AsyncConnection, name: str) -> dict[str, Any] | None:
    """Fetch a frame definition by name. Returns None if not found."""
    result = await conn.execute(select(frames).where(frames.c.name == name))
    row = result.mappings().first()
    return dict(row) if row is not None else None


async def upsert_frame(
    conn: AsyncConnection,
    *,
    name: str,
    weights_json: str,
    filters_json: str,
    guarantees_json: str,
    template: str,
    token_budget: int,
    cache_json: str | None,
    source: str,
) -> None:
    """Insert or update a frame definition."""
    stmt = insert(frames).values(
        name=name,
        weights_json=weights_json,
        filters_json=filters_json,
        guarantees_json=guarantees_json,
        template=template,
        token_budget=token_budget,
        cache_json=cache_json,
        source=source,
        created_at=_now_iso(),
    )
    await conn.execute(
        stmt.on_conflict_do_update(
            index_elements=["name"],
            set_={
                "weights_json": weights_json,
                "filters_json": filters_json,
                "guarantees_json": guarantees_json,
                "template": template,
                "token_budget": token_budget,
                "cache_json": cache_json,
                "source": source,
            },
        )
    )


async def list_frames(conn: AsyncConnection) -> list[dict[str, Any]]:
    """List all frame definitions."""
    result = await conn.execute(select(frames).order_by(frames.c.name))
    return [dict(row) for row in result.mappings()]


# ── Session queries ───────────────────────────────────────────────────────────

async def start_session(
    conn: AsyncConnection,
    *,
    session_id: str,
    task_type: str,
    start_active_hours: float,
) -> None:
    """Record a new session start."""
    await conn.execute(
        insert(sessions).values(
            id=session_id,
            task_type=task_type,
            started_at=_now_iso(),
            start_active_hours=start_active_hours,
        )
    )


async def end_session(conn: AsyncConnection, session_id: str) -> None:
    """Record session end time."""
    await conn.execute(
        update(sessions)
        .where(sessions.c.id == session_id)
        .values(ended_at=_now_iso())
    )


async def get_active_session(conn: AsyncConnection) -> dict[str, Any] | None:
    """Get the currently active session (ended_at IS NULL)."""
    result = await conn.execute(
        select(sessions)
        .where(sessions.c.ended_at.is_(None))
        .order_by(sessions.c.started_at.desc())
        .limit(1)
    )
    row = result.mappings().first()
    return dict(row) if row is not None else None


# ── System config queries ─────────────────────────────────────────────────────

async def get_config(conn: AsyncConnection, key: str) -> str | None:
    """Get a system config value by key. Returns None if not found."""
    result = await conn.execute(
        select(system_config.c.value).where(system_config.c.key == key)
    )
    row = result.first()
    return row[0] if row is not None else None


async def set_config(conn: AsyncConnection, key: str, value: str) -> None:
    """Set a system config value (upsert)."""
    stmt = insert(system_config).values(key=key, value=value)
    await conn.execute(
        stmt.on_conflict_do_update(
            index_elements=["key"],
            set_={"value": value},
        )
    )


async def get_total_active_hours(conn: AsyncConnection) -> float:
    """Get the total_active_hours counter. Returns 0.0 if not set."""
    value = await get_config(conn, "total_active_hours")
    return float(value) if value is not None else 0.0


async def set_total_active_hours(conn: AsyncConnection, hours: float) -> None:
    """Update the total_active_hours counter."""
    await set_config(conn, "total_active_hours", str(hours))


# ── Seed data ─────────────────────────────────────────────────────────────────

_BUILTIN_FRAMES = [
    {
        "name": "self",
        "weights_json": json.dumps(
            {"similarity": 0.10, "confidence": 0.30, "recency": 0.05,
             "centrality": 0.25, "reinforcement": 0.30}
        ),
        "filters_json": json.dumps(
            {"tag_patterns": ["self/%"], "categories": [], "search_window": None}
        ),
        "guarantees_json": "[]",
        "template": "# SELF\n{blocks}",
        "token_budget": 500,
        "cache_json": None,
        "source": "builtin",
    },
    {
        "name": "attention",
        "weights_json": json.dumps(
            {"similarity": 0.35, "confidence": 0.15, "recency": 0.25,
             "centrality": 0.15, "reinforcement": 0.10}
        ),
        "filters_json": json.dumps(
            {"tag_patterns": [], "categories": [], "search_window": None}
        ),
        "guarantees_json": "[]",
        "template": "# ATTENTION\n{blocks}",
        "token_budget": 1000,
        "cache_json": None,
        "source": "builtin",
    },
    {
        "name": "task",
        "weights_json": json.dumps(
            {"similarity": 0.20, "confidence": 0.20, "recency": 0.20,
             "centrality": 0.20, "reinforcement": 0.20}
        ),
        "filters_json": json.dumps(
            {"tag_patterns": [], "categories": [], "search_window": None}
        ),
        "guarantees_json": "[]",
        "template": "# TASK\n{blocks}",
        "token_budget": 800,
        "cache_json": None,
        "source": "builtin",
    },
]

_BUILTIN_CONFIG = [
    ("total_active_hours", "0.0"),
    ("prune_threshold", "0.05"),
    ("top_k", "5"),
]


async def seed_builtin_data(conn: AsyncConnection) -> None:
    """Insert built-in frames and default system_config values.

    Idempotent — uses INSERT OR IGNORE. Safe to call multiple times.
    """
    now = _now_iso()
    for frame_data in _BUILTIN_FRAMES:
        stmt = insert(frames).values(**frame_data, created_at=now)
        await conn.execute(stmt.on_conflict_do_nothing())
    for key, value in _BUILTIN_CONFIG:
        stmt = insert(system_config).values(key=key, value=value)
        await conn.execute(stmt.on_conflict_do_nothing())
