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
    cue: str | None = None,
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
            # Still pending: the duplicate it matched is itself un-consolidated,
            # so this content is no more retrievable than it was before.
            return LearnResult(
                block_id=content_id,
                status="duplicate_rejected",
                pending_consolidation=True,
            )
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
        cue=cue,
    )

    if tags:
        await add_tags(conn, block_id, tags)

    # Always pending: this path inserts with status="inbox", so the block is
    # stored but not yet retrievable by frame()/recall(). Saying so here is
    # what stops `status="created"` from reading as "the agent can see it".
    return LearnResult(
        block_id=block_id, status="created", pending_consolidation=True
    )
