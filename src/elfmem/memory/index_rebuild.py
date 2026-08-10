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
reconciliation. Full-rebuild data loss for anything the derived index alone
held (accumulated α/β evidence, graph edges — neither is currently encoded
in the file format) is a known, documented trade-off, not a defect of this
unit: see ``docs/plans/v2_substrate/plan/model.md`` residual risks.

Extension point (``growable by injection``): ``additional_fold_steps`` lets a
caller register further sources to fold into the same rebuild — e.g. U-012's
peer-received log, deduplicated by ``msg_id`` rather than by file identity —
without editing this module.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncConnection

from elfmem.db.queries import add_tags, insert_block, update_block_scoring
from elfmem.exceptions import ElfmemError
from elfmem.memory.blockfile import Block, ParseError, parse_blocks, read_raw
from elfmem.memory.blocks import compute_content_hash
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


async def _write_block(
    conn: AsyncConnection,
    block: Block,
    status: str,
    embedding_service: EmbeddingService,
    embedding_model: str,
) -> None:
    block_id = block.id or compute_content_hash(block.content)[:16]
    await insert_block(
        conn,
        block_id=block_id,
        content=block.content,
        category="knowledge",
        source="file",
        status=status,
    )
    if block.tags:
        await add_tags(conn, block_id, block.tags)
    vec = await embedding_service.embed(block.content.strip().lower())
    await update_block_scoring(
        conn,
        block_id,
        embedding=vec,
        embedding_model=embedding_service.model_name,
    )


async def rebuild_index(
    conn: AsyncConnection,
    memory_dir: Path,
    embedding_service: EmbeddingService,
    embedding_model: str,
    *,
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

    blocks_written = 0
    parse_errors: list[tuple[Path, ParseError]] = []

    for subdir_name, status in _BLOCK_SOURCES:
        subdir = memory_dir / subdir_name
        if not subdir.is_dir():
            continue
        for path in sorted(subdir.glob("**/*.md")):
            text = path.read_text(encoding="utf-8")
            result = parse_blocks(text)
            for err in result.errors:
                parse_errors.append((path, err))
            for block in result.blocks:
                await _write_block(
                    conn, block, status, embedding_service, embedding_model
                )
                blocks_written += 1

    for fold_step in additional_fold_steps:
        blocks_written += await fold_step(conn, embedding_service, embedding_model)

    return RebuildResult(
        blocks_written=blocks_written,
        self_content=self_content,
        parse_errors=parse_errors,
    )
