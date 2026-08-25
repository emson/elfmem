"""``elfmem index`` rebuild — derives ``.elfmem/index.db`` (L2) from
``.elfmem/memory/**.md`` (L1) with zero LLM calls (Invariant 1).

Embedding calls DO happen here — embeddings are a distinct, cheap service
from LLM curation, and re-embedding on rebuild is the intended cost, not the
52x amplification the v2 redesign eliminates (that was pairwise LLM
consolidation, not vector embedding).

``self.md`` is read via ``blockfile.read_raw()`` and returned separately in
``RebuildResult.self_content`` — it never becomes a row in ``blocks``
(Invariant 2). ``notes/*.md`` files land as ``status="active"`` (already
curated); ``log/*.md`` files land as ``status="inbox"`` (not yet reviewed) —
mirroring the existing status semantics ``learn()`` already uses.

Precondition: the caller has ensured ``blocks``/``block_tags``/``edges`` are
empty before calling ``rebuild_index`` (e.g. a fresh ``index.db``, or an
explicit wipe) — this module does pure INSERT, not incremental
reconciliation. ``confidence``/``alpha``/``beta`` (the α/β sufficient
statistics — see ``export.py``) round-trip via each block's frontmatter
``extra`` fields; a block with none of the three falls back to
``insert_block``'s neutral default (confidence=0.50). ``created`` and
``pinned`` round-trip from frontmatter, and ``decay_lambda`` is re-derived
from tags + category (all three were silently lost before v2 Phase 0).

Reinforcement count, recency, and the α/β posterior round-trip through the
**ledger** (``memory/ledger.py``) rather than through the file format — they
are history, not content, and belong in an append-only log rather than in
hand-editable frontmatter. Pass ``ledger_dir`` to restore them; omit it and
the rebuild degrades to frontmatter aggregates exactly as before.

Declared graph edges round-trip through the block format's typed links.
Similarity edges are recomputable from embeddings and are deliberately not
stored anywhere; regenerating them at rebuild time is the remaining piece
(see ``docs/research/block_ledger_synthesis_research.md`` §6.2 and
``docs/plans/v2_substrate/plan/dry_run_2026-08-10.md``).

Extension point (``growable by injection``): ``additional_fold_steps`` lets a
caller register further sources to fold into the same rebuild — e.g. U-012's
peer-received log, deduplicated by ``msg_id`` rather than by file identity —
without editing this module.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from elfmem.db.queries import (
    add_tags,
    insert_block,
    insert_edge,
    set_config,
    update_block_scoring,
)
from elfmem.exceptions import ElfmemError
from elfmem.memory.blockfile import Block, Link, ParseError, parse_blocks, read_raw
from elfmem.memory.blocks import (
    compute_content_hash,
    decay_lambda_for_tier,
    determine_decay_tier,
)
from elfmem.memory.ledger import BlockState, EdgeState, ReplayResult, replay
from elfmem.ports.services import EmbeddingService

# (source subdirectory, block status it lands as) — mirrors learn()'s
# inbox/active status semantics: log/ is unreviewed, notes/ is curated.
_BLOCK_SOURCES: tuple[tuple[str, str], ...] = (
    ("notes", "active"),
    ("log", "inbox"),
)

FoldStep = Callable[
    [AsyncConnection, EmbeddingService, str], Awaitable[int]
]


class MemoryDirNotFoundError(ElfmemError):
    """Raised when the memory directory to rebuild from does not exist."""

    def __init__(self, memory_dir: Path) -> None:
        super().__init__(
            f"Memory directory not found: {memory_dir}",
            recovery=(
                "Run 'elfmem init' to create .elfmem/memory/, or check "
                "the configured project root is correct."
            ),
        )


@dataclass
class RebuildResult:
    """What one ``rebuild_index`` call produced.

    ``self_content`` is ``None`` when no ``self.md`` file exists — not an
    error; a fresh project may not have written one yet.
    """

    blocks_written: int
    self_content: str | None
    parse_errors: list[tuple[Path, ParseError]] = field(default_factory=list)
    ledger: ReplayResult | None = None
    edges_written: int = 0
    edges_dropped: int = 0
    dangling_links: list[tuple[str, str, str]] = field(default_factory=list)


def _parse_float(raw: str | None) -> float | None:
    """Parse a frontmatter `extra` numeric field. Returns None if absent or malformed
    — a malformed evidence field should fall back to insert_block's own defaults,
    not abort the rebuild (parse_errors already covers frontmatter-shape problems;
    this is a narrower, best-effort read of a value insert_block treats as optional).
    """
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


async def _write_block(
    conn: AsyncConnection,
    block: Block,
    status: str,
    category: str,
    embedding_service: EmbeddingService,
    embedding_model: str,
    history: BlockState | None = None,
) -> None:
    block_id = block.id or compute_content_hash(block.content)[:16]
    confidence = _parse_float(block.extra.get("confidence"))
    success_count = _parse_float(block.extra.get("alpha"))
    failure_count = _parse_float(block.extra.get("beta"))
    # decay_lambda is derived, not stored: the tier is a pure function of
    # tags + category, so re-deriving it is exact rather than a guess. Before
    # Phase 0 this fell through to insert_block's STANDARD default (0.01),
    # which silently demoted every PERMANENT constitutional block to a 69-hour
    # half-life and perturbed `recency` on every subsequent frame() call.
    # Tier derivation is the fallback, not the source of truth. Re-deriving
    # unconditionally demoted 20 of 235 blocks on a peer instance -- 19 of them
    # from PERMANENT to DURABLE, a 100x faster decay clock -- because their
    # stored lambda reflected accumulated history (outcome penalties, tags
    # that changed after promotion) rather than their current tags.
    tier_lambda = decay_lambda_for_tier(determine_decay_tier(block.tags, category))
    resolved_lambda = (
        history.decay_lambda
        if history is not None and history.decay_lambda is not None
        else tier_lambda
    )
    # Ledger replay wins over frontmatter wherever it has anything to say:
    # it carries the full event history, where the frontmatter fields carry
    # only a rolled-up aggregate. A block with no ledger events (anything
    # predating the ledger) falls back to frontmatter, so the two coexist
    # through the migration rather than needing a flag day.
    if history is not None:
        success_count = history.alpha
        failure_count = history.beta
        confidence = history.alpha / (history.alpha + history.beta)
    await insert_block(
        conn,
        block_id=block_id,
        content=block.content,
        category=category,
        source="file",
        status=status,
        confidence=confidence if confidence is not None else 0.50,
        success_count=success_count,
        failure_count=failure_count,
        decay_lambda=resolved_lambda,
        pinned=block.pinned,
        cue=block.cue,
        volatility_class=block.volatility,
        created_at=(history.created_at if history else None) or block.created,
        reinforcement_count=history.reinforcement_count if history else 0,
        last_reinforced_at=history.last_reinforced_at if history else 0.0,
    )
    if block.tags:
        await add_tags(conn, block_id, block.tags)
    vec = await embedding_service.embed(block.content.strip().lower())
    await update_block_scoring(
        conn,
        block_id,
        embedding=vec,
        embedding_model=embedding_service.model_name,
        summary=(history.summary if history else None),
    )


async def _apply_learned_edges(
    conn: AsyncConnection,
    links: dict[tuple[str, str], EdgeState],
    known_ids: set[str],
) -> tuple[int, int]:
    """Restore the learned graph from the ledger. Returns (written, dropped).

    An edge whose endpoint is not in the rebuilt index is dropped, not raised:
    ``archive/`` is deliberately never re-read, so every edge touching an
    archived block is expected to fall away here.
    """
    written = 0
    dropped = 0
    for (a, b), edge in links.items():
        if a not in known_ids or b not in known_ids:
            dropped += 1
            continue
        await insert_edge(
            conn,
            from_id=a,
            to_id=b,
            weight=edge.weight,
            relation_type=edge.relation,
            origin=edge.origin,
            last_active_hours=edge.last_active_hours,
            note=edge.note,
            declared_by=edge.declared_by,
        )
        if edge.reinforcement_count:
            await conn.execute(
                text(
                    "UPDATE edges SET reinforcement_count = :rc "
                    "WHERE from_id = :a AND to_id = :b"
                ),
                {"rc": edge.reinforcement_count, "a": a, "b": b},
            )
        written += 1
    return written, dropped


async def _apply_declared_links(
    conn: AsyncConnection,
    declared: list[tuple[str, Link]],
    known_ids: set[str],
) -> tuple[int, list[tuple[str, str, str]]]:
    """Turn typed links from the file substrate into graph edges.

    This is the piece the build plan recorded as a genuine unassigned gap:
    *"the block format has no way to encode an edge in frontmatter"*, so the
    entire ``edges`` table was lost on every rebuild. Typed links are that
    encoding, and this is where they land.

    A link whose target does not exist is reported, not raised. Pointing at a
    block you have not written yet is ordinary practice in a vault, and it is
    also what a hand-deleted block leaves behind.
    """
    from elfmem.operations.connect import (
        _DEFAULT_WEIGHT_FALLBACK,
        _RELATION_DEFAULT_WEIGHTS,
    )

    written = 0
    dangling: list[tuple[str, str, str]] = []
    for source_id, link in declared:
        if link.target not in known_ids:
            dangling.append((source_id, link.relation, link.target))
            continue
        if link.target == source_id:
            continue
        from_id, to_id = sorted((source_id, link.target))
        await insert_edge(
            conn,
            from_id=from_id,
            to_id=to_id,
            weight=_RELATION_DEFAULT_WEIGHTS.get(
                link.relation, _DEFAULT_WEIGHT_FALLBACK
            ),
            relation_type=link.relation,
            origin="agent",
            declared_by=source_id,
        )
        # insert_edge is OR IGNORE and endpoints are unique, so a pair the
        # ledger already restored keeps its accumulated weight and
        # reinforcement count. What a declared link must still win is the
        # *relation*: a human or agent asserted "refines", and that outranks
        # whatever similarity called it. Counts are left alone deliberately —
        # they are earned history, not part of the assertion.
        await conn.execute(
            text(
                "UPDATE edges SET relation_type = :rel, origin = 'agent', "
                "declared_by = :by WHERE from_id = :a AND to_id = :b"
            ),
            {"rel": link.relation, "by": source_id, "a": from_id, "b": to_id},
        )
        written += 1
    return written, dangling


async def rebuild_index(
    conn: AsyncConnection,
    memory_dir: Path,
    embedding_service: EmbeddingService,
    embedding_model: str,
    *,
    ledger_dir: Path | None = None,
    additional_fold_steps: Sequence[FoldStep] = (),
) -> RebuildResult:
    """Rebuild the derived index from the authoritative file substrate.

    USE WHEN: `.elfmem/index.db` needs to be (re)built from
        `.elfmem/memory/` — fresh install, corruption recovery, or any time
        the derived index is deleted.
    DON'T USE WHEN: ingesting one new block at write time — that's `learn()`.
    COST: one embedding call per block (via `embedding_service`), zero LLM
        calls.
    RETURNS: `RebuildResult` — block count, `self.md` content (if any), and
        any per-block frontmatter parse errors (collected, not raised, so
        one malformed block doesn't abort the whole rebuild).
    NEXT: wire `self_content` into the `self` frame (not this module's
        responsibility — see `results/U-002.md` "Missing context").
    """
    if not memory_dir.is_dir():
        raise MemoryDirNotFoundError(memory_dir)

    self_content: str | None = None
    self_path = memory_dir / "self.md"
    if self_path.is_file():
        self_content = read_raw(self_path.read_text(encoding="utf-8"))

    history = replay(ledger_dir) if ledger_dir is not None else None

    blocks_written = 0
    parse_errors: list[tuple[Path, ParseError]] = []
    declared_links: list[tuple[str, Link]] = []
    known_ids: set[str] = set()

    for subdir_name, status in _BLOCK_SOURCES:
        subdir = memory_dir / subdir_name
        if not subdir.is_dir():
            continue
        for path in sorted(subdir.glob("**/*.md")):
            # The category a block lands under is the file it came from
            # (export.py writes one file per category: notes/<category>.md,
            # log/<category>.md) — mirrored back on rebuild rather than
            # collapsed to a single hardcoded value, since category drives
            # real behaviour downstream (mind_list/mind_show/ls(category=)
            # all filter on it).
            #
            # Relative to the subdir, not `path.stem`: a category containing
            # a slash ("pattern/strategy", "self/constitutional") exports to a
            # nested file, and taking only the stem drops the prefix. On a
            # peer instance that silently merged three "pattern/strategy"
            # blocks into the unrelated "strategy" category. Same bug class
            # as ADR 0011's hardcoded "knowledge"; this is the case it missed.
            category = path.relative_to(subdir).with_suffix("").as_posix()
            text = path.read_text(encoding="utf-8")
            result = parse_blocks(text)
            for err in result.errors:
                parse_errors.append((path, err))
            for block in result.blocks:
                block_history = (
                    history.blocks.get(block.id) if history and block.id else None
                )
                await _write_block(
                    conn, block, status, category, embedding_service,
                    embedding_model, block_history,
                )
                resolved_id = block.id or compute_content_hash(block.content)[:16]
                known_ids.add(resolved_id)
                declared_links.extend((resolved_id, ln) for ln in block.links)
                blocks_written += 1

    for fold_step in additional_fold_steps:
        blocks_written += await fold_step(conn, embedding_service, embedding_model)

    # Links resolve only after every block exists: edges carry a foreign key
    # to both endpoints, and files are parsed in directory order, not
    # dependency order.
    if history is not None and history.total_active_hours is not None:
        await set_config(
            conn, "total_active_hours", str(history.total_active_hours)
        )

    learned_edges, dropped_edges = (
        await _apply_learned_edges(conn, history.links, known_ids)
        if history is not None else (0, 0)
    )
    declared_edges, dangling = await _apply_declared_links(
        conn, declared_links, known_ids
    )
    edges_written = learned_edges + declared_edges

    return RebuildResult(
        blocks_written=blocks_written,
        self_content=self_content,
        parse_errors=parse_errors,
        ledger=history,
        edges_written=edges_written,
        edges_dropped=dropped_edges,
        dangling_links=dangling,
    )
