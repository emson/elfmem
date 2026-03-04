"""learn() — fast-path block ingestion into the inbox."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncConnection

from elfmem.db.queries import add_tags, get_block, insert_block
from elfmem.memory.blocks import compute_content_hash
from elfmem.types import LearnResult


async def learn(
    conn: AsyncConnection,
    *,
    content: str,
    tags: list[str] | None = None,
    category: str = "knowledge",
    source: str = "api",
) -> LearnResult:
    """Ingest a block into the inbox (fast path — no LLM calls).

    Deduplication rules:
    - If block with same content-hash exists in INBOX → "duplicate_rejected"
    - If block with same content-hash exists in ACTIVE/ARCHIVED → generate UUID id,
      insert fresh into inbox (consolidate will detect near-dup via embeddings)
    - Otherwise → insert with content-hash id → "created"
    """
    content_id = compute_content_hash(content)

    existing = await get_block(conn, content_id)
    if existing is not None:
        if existing["status"] == "inbox":
            return LearnResult(block_id=content_id, status="duplicate_rejected")
        # Already active or archived — re-learn with a new id
        block_id = uuid.uuid4().hex[:16]
    else:
        block_id = content_id

    await insert_block(
        conn,
        block_id=block_id,
        content=content,
        category=category,
        source=source,
        status="inbox",
    )

    if tags:
        await add_tags(conn, block_id, tags)

    return LearnResult(block_id=block_id, status="created")
