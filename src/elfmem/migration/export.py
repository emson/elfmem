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

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from elfmem.db.queries import (
    get_active_blocks,
    get_archived_blocks,
    get_inbox_blocks,
    get_tags_batch,
)
from elfmem.memory import ledger as _ledger
from elfmem.memory.blockfile import Block, Link, write_blocks
from elfmem.memory.blocks import determine_volatility_class

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
    blocks_seeded: int = 0
    edges_seeded: int = 0


def _block_to_frontmatter_block(
    row: dict[str, Any], tags: list[str], links: list[Link] | None = None
) -> Block:
    # pinned is ADDITIVE, not a replacement for the self/constitutional tag:
    # frame('self', ...) currently filters by tag pattern (self/%), so
    # stripping the tag on export would silently break that retrieval path.
    # Both the tag and the new pinned:true guard carry the same fact.
    pinned = bool(row.get("pinned")) or "self/constitutional" in tags
    # confidence / alpha / beta are NOT written here any more. They are derived
    # state, and derived state belongs in the ledger, which carries the full
    # event history rather than a rolled-up aggregate a hand-edit could
    # silently corrupt. `export_to_markdown` seeds them as a ledger `seed`
    # event instead — an exact reconstruction, not an approximation.
    # created_at goes the same way, and is kept in frontmatter only as a
    # human-readable convenience for a file nobody has a ledger for yet.
    first_line = row["content"].strip().splitlines()[0] if row["content"].strip() else "Untitled"
    title = first_line[:60]
    return Block(
        title=title,
        content=row["content"],
        id=row["id"],
        tags=tags,
        pinned=pinned,
        created=row["created_at"],
        cue=row.get("cue"),
        volatility=row.get("volatility_class")
        or determine_volatility_class(tags, row["category"] or ""),
        links=links or [],
    )


async def _declared_links(conn: AsyncConnection) -> dict[str, list[Link]]:
    """Typed links to write into the file substrate, keyed by declaring block.

    Only deliberate edges are exported. Similarity and co-retrieval edges are
    *learned*: the first is recomputable from embeddings, the second replays
    from the ledger, and neither is something a human authored. Writing them
    into hand-editable files would invite editing a number the system owns.
    """
    rows = (await conn.execute(
        text(
            "SELECT from_id, to_id, relation_type, declared_by FROM edges "
            "WHERE origin = 'agent'"
        )
    )).mappings().all()
    by_block: dict[str, list[Link]] = {}
    for row in rows:
        # declared_by is NULL for edges created before schema v8; fall back to
        # the canonical from_id so the link is still written, just possibly
        # pointing the other way than it originally did.
        source = row["declared_by"] or row["from_id"]
        target = row["to_id"] if source == row["from_id"] else row["from_id"]
        by_block.setdefault(source, []).append(
            Link(relation=row["relation_type"], target=target)
        )
    return by_block


def _seed_ledger(ledger_dir: Path, rows: list[dict[str, Any]]) -> int:
    """Write one `seed` event per block: the pre-ledger history, carried over.

    alpha/beta are reproduced exactly rather than approximated. Replay computes
    `alpha = 0.5 + sig*w`, so choosing `w = (a-0.5) + (b-0.5)` and
    `sig = (a-0.5)/w` inverts it precisely. A block with no accumulated
    evidence (a = b = 0.5, so w = 0) needs no event at all.
    """
    def _num(value: Any, default: float) -> float:
        # Explicit None check, never `value or default`: beta is legitimately
        # 0.0 for any block promoted at confidence 1.0, and `0.0 or 0.5` is
        # 0.5. That silently invented evidence for 27 of 145 real blocks.
        return default if value is None else float(value)

    seeded = 0
    for row in rows:
        last_reinforced = _num(row.get("last_reinforced_at"), 0.0)
        _ledger.append(
            ledger_dir,
            _ledger.KIND_SEED,
            active_hours=last_reinforced,
            id=row["id"],
            created=row["created_at"],
            n=int(_num(row.get("reinforcement_count"), 0.0)),
            lah=last_reinforced,
            # Not rounded: JSON round-trips a double exactly via repr, and
            # rounding to 6 places loses up to 5e-7 of accumulated evidence.
            a=_num(row.get("success_count"), 0.5),
            b=_num(row.get("failure_count"), 0.5),
            # decay_lambda is only *partly* derived from tags. `outcome()`
            # with a negative signal multiplies it (accelerate_block_decay),
            # so a penalised block's lambda is accumulated history that no
            # tag lookup can reconstruct -- a peer instance carries lambdas
            # like 0.02 that correspond to no tier at all.
            lam=_num(row.get("decay_lambda"), 0.01),
            # The LLM distillation. Carried, not regenerated: recovering it
            # otherwise costs one LLM call per block (210 on one real corpus)
            # and every rebuilt block would sit unscored until that ran.
            **({"sum": row["summary"]} if row.get("summary") else {}),
        )
        seeded += 1
    return seeded


async def _seed_ledger_edges(conn: AsyncConnection, ledger_dir: Path) -> int:
    """Write one `link` event per existing edge: the graph, carried over.

    Nothing here is recomputed on the far side. Similarity edges were scored
    against summary embeddings and temporal proximity at promotion time, and
    co-retrieval edges are retrieval history — neither is a function of the
    content a rebuild has access to. Losing them zeroed `centrality`, one of
    the five terms in the retrieval composite, on every rebuild.
    """
    rows = (await conn.execute(
        text(
            "SELECT from_id, to_id, relation_type, origin, weight, "
            "reinforcement_count, last_active_hours, declared_by, note FROM edges"
        )
    )).mappings().all()
    for row in rows:
        _ledger.append(
            ledger_dir,
            _ledger.KIND_LINK,
            active_hours=float(row["last_active_hours"] or 0.0),
            **{"from": row["from_id"], "to": row["to_id"]},
            rel=row["relation_type"],
            o=row["origin"],
            w=float(row["weight"]),
            rc=int(row["reinforcement_count"] or 0),
            lah=row["last_active_hours"],
            by=row["declared_by"],
        )
    return len(rows)


async def export_to_markdown(
    conn: AsyncConnection, memory_dir: Path, *, ledger_dir: Path | None = None,
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
    links_by_block = await _declared_links(conn)
    blocks_seeded = 0
    edges_seeded = 0
    if ledger_dir is not None:
        edges_seeded = await _seed_ledger_edges(conn, ledger_dir)
        # The session-aware activity clock. Every block's recency is measured
        # against it; an index rebuilt without it computes recency from zero,
        # which makes `hours_since` negative and inverts the whole scale.
        clock = (await conn.execute(
            text("SELECT value FROM system_config WHERE key = 'total_active_hours'")
        )).scalar()
        if clock is not None:
            _ledger.append(
                ledger_dir, _ledger.KIND_INSTANCE,
                active_hours=float(clock), total_ah=float(clock),
            )

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
            block = _block_to_frontmatter_block(
                row, tags_by_id.get(row["id"], []), links_by_block.get(row["id"])
            )
            by_category.setdefault(category, []).append(block)
        if ledger_dir is not None:
            blocks_seeded += _seed_ledger(ledger_dir, list(rows))

        for category, category_blocks in by_category.items():
            dest = memory_dir / target_subdir / f"{category}.md"
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(write_blocks(category_blocks), encoding="utf-8")
            files_written.append(dest)
            blocks_exported += len(category_blocks)

    return ExportResult(
        blocks_exported=blocks_exported,
        files_written=files_written,
        blocks_seeded=blocks_seeded,
        edges_seeded=edges_seeded,
    )
