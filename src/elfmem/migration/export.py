"""Migration export — serializes existing DB-native blocks into the
``.elfmem/memory/`` file substrate (plan doc Migration Phases 0-2).

One file per category value: active blocks land in ``notes/<category>.md``,
inbox blocks in ``log/<category>.md``, archived blocks in
``archive/<category>.md`` — recoverable, not deleted (plan doc §8 Phase 1).

Each block's *existing* DB row id becomes its permanent frontmatter `id:`.
It is not recomputed: the row id is already a stable identifier assigned at
creation (content-hash-derived for a first `learn()`, uuid-derived for a
re-learned duplicate — either way, already permanent in the sense Invariant 3
requires). ``self/constitutional``-tagged blocks are exported with
`pinned: true`, migrating today's tag-based supersession guard into the new
frontmatter-native one.

Evidence fields with no frontmatter home in the base schema (`confidence`,
`success_count`/`failure_count` — the α/β sufficient statistics) are carried
through in `Block.extra`, since U-001's frontmatter schema is deliberately
open rather than fixed.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection

from elfmem.db.queries import (
    get_active_blocks,
    get_archived_blocks,
    get_inbox_blocks,
    get_tags_batch,
)
from elfmem.memory.blockfile import Block, write_blocks

# (status, target subdirectory) -- mirrors index_rebuild.py's inverse mapping
# (notes/ -> active, log/ -> inbox), plus archive/ for status='archived',
# which index_rebuild.py deliberately does not read back in (archived
# content stays recoverable in git history, not re-entered into the index).
_STATUS_TARGETS: tuple[tuple[str, str], ...] = (
    ("active", "notes"),
    ("inbox", "log"),
    ("archived", "archive"),
)


@dataclass
class ExportResult:
    blocks_exported: int
    files_written: list[Path]


def _block_to_frontmatter_block(row: dict[str, Any], tags: list[str]) -> Block:
    # pinned is ADDITIVE, not a replacement for the self/constitutional tag:
    # frame('self', ...) currently filters by tag pattern (self/%), so
    # stripping the tag on export would silently break that retrieval path.
    # Both the tag and the new pinned:true guard carry the same fact.
    pinned = "self/constitutional" in tags
    extra = {
        "confidence": f"{row['confidence']:.4f}",
        "alpha": f"{row['success_count']:.4f}",
        "beta": f"{row['failure_count']:.4f}",
    }
    # created_at is the row's title-worthy identity; the block "title" in
    # file form has no DB equivalent, so the first line of content stands
    # in, truncated to keep headings scannable.
    first_line = row["content"].strip().splitlines()[0] if row["content"].strip() else "Untitled"
    title = first_line[:60]
    return Block(
        title=title,
        content=row["content"],
        id=row["id"],
        tags=tags,
        pinned=pinned,
        created=row["created_at"],
        extra=extra,
    )


async def export_to_markdown(
    conn: AsyncConnection, memory_dir: Path
) -> ExportResult:
    """Export every DB-native block to `.elfmem/memory/`.

    USE WHEN: migration Phase 1 — the one-time move from DB-primary to
        file-primary storage.
    DON'T USE WHEN: ongoing operation — after migration, files are written
        directly by `learn()`/`file_mutation.py`, not exported from a DB
        that is no longer authoritative.
    COST: one query per status (three total) + one tags-batch query; no LLM,
        no embedding calls.
    RETURNS: `ExportResult` — block count and every file path written.
    NEXT: `git add .elfmem/memory/ && git commit` (Phase 2), then
        `elfmem index` (Phase 3) to prove the round trip.
    """
    memory_dir.mkdir(parents=True, exist_ok=True)
    files_written: list[Path] = []
    blocks_exported = 0

    fetchers = {
        "active": get_active_blocks,
        "inbox": get_inbox_blocks,
        "archived": get_archived_blocks,
    }

    for status, target_subdir in _STATUS_TARGETS:
        rows = await fetchers[status](conn)
        if not rows:
            continue
        tags_by_id = await get_tags_batch(conn, [r["id"] for r in rows])

        by_category: dict[str, list[Block]] = {}
        for row in rows:
            category = row["category"] or "uncategorised"
            block = _block_to_frontmatter_block(row, tags_by_id.get(row["id"], []))
            by_category.setdefault(category, []).append(block)

        for category, category_blocks in by_category.items():
            dest = memory_dir / target_subdir / f"{category}.md"
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(write_blocks(category_blocks), encoding="utf-8")
            files_written.append(dest)
            blocks_exported += len(category_blocks)

    return ExportResult(blocks_exported=blocks_exported, files_written=files_written)
