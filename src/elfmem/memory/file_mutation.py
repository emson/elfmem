"""File-native mutation primitives over ``.elfmem/memory/`` — U-003.

Pure filesystem operations against ``notes/*.md`` and ``log/*.md`` (never
``self.md`` — the constitution isn't a mutable block, per Invariant 2).
No database, no index, no LLM or embedding calls: editing a block is a text
edit, exactly as the plan doc's mutation-API table promises.

Not yet wired into ``MemorySystem.edit()``/``.forget()``/``.ls()`` — the live
system is still DB-primary until migration completes. That re-pointing
belongs to a later unit (U-006, flip authority); this module is the
file-native primitive layer it will call into.
"""

from __future__ import annotations

from pathlib import Path

from elfmem.exceptions import ElfmemError
from elfmem.memory.blockfile import Block, parse_blocks, write_blocks

# Mirrors index_rebuild.py's source list — notes/ and log/ are the only
# block-mode directories; self.md is never searched (Invariant 2).
_BLOCK_SUBDIRS = ("notes", "log")


class BlockNotFoundInFilesError(ElfmemError):
    """Raised when a block id doesn't exist in any notes/log file."""

    def __init__(self, block_id: str) -> None:
        super().__init__(
            f"Block '{block_id}' not found in .elfmem/memory/.",
            recovery=(
                "Use list_blocks() to see current block ids, or check the "
                "id wasn't mistyped."
            ),
        )


def _block_files(memory_dir: Path) -> list[Path]:
    files: list[Path] = []
    for subdir_name in _BLOCK_SUBDIRS:
        subdir = memory_dir / subdir_name
        if subdir.is_dir():
            files.extend(sorted(subdir.glob("**/*.md")))
    return files


def find_block(memory_dir: Path, block_id: str) -> tuple[Path, Block] | None:
    """Locate a block by id across notes/ and log/.

    USE WHEN: you need a block's file and current content before mutating it.
    COST: parses every block file in `memory_dir` — fine at the corpus sizes
        this project targets (not binding below ~2,000 blocks, same bound
        already named for corpus-level review in model.md).
    RETURNS: `(path, block)`, or `None` if no block with this id exists.
    NEXT: `edit_block`, `forget_block`, `promote_block` all call this first.
    """
    for path in _block_files(memory_dir):
        result = parse_blocks(path.read_text(encoding="utf-8"))
        for block in result.blocks:
            if block.id == block_id:
                return path, block
    return None


def edit_block(memory_dir: Path, block_id: str, new_content: str) -> Block:
    """Replace a block's content in place. Its `id` never changes (Invariant 3).

    USE WHEN: the content of an existing block needs to change.
    DON'T USE WHEN: the block doesn't exist yet — that's a fresh `learn()`
        append, not an edit.
    COST: one file read + one file write.
    RETURNS: the updated `Block`.
    NEXT: `elfmem index` picks up the change on its next rebuild.
    """
    found = find_block(memory_dir, block_id)
    if found is None:
        raise BlockNotFoundInFilesError(block_id)
    path, _ = found

    result = parse_blocks(path.read_text(encoding="utf-8"))
    updated: Block | None = None
    for block in result.blocks:
        if block.id == block_id:
            block.content = new_content
            updated = block
    path.write_text(write_blocks(result.blocks), encoding="utf-8")
    assert updated is not None  # guaranteed by find_block's prior match
    return updated


def forget_block(memory_dir: Path, block_id: str) -> bool:
    """Remove a block from its file. History survives in git, not here.

    USE WHEN: a block should no longer be part of active memory.
    COST: one file read + one file write (or none, if already gone).
    RETURNS: `True` if a block was removed, `False` if it was already absent
        — idempotent, matching the existing DB-native `forget()` convention
        (forgetting twice is not an error).
    NEXT: `elfmem index` reflects the removal on its next rebuild.
    """
    found = find_block(memory_dir, block_id)
    if found is None:
        return False
    path, _ = found

    result = parse_blocks(path.read_text(encoding="utf-8"))
    remaining = [b for b in result.blocks if b.id != block_id]
    path.write_text(write_blocks(remaining), encoding="utf-8")
    return True


def list_blocks(
    memory_dir: Path,
    *,
    tag: str | None = None,
    category_subdir: str | None = None,
) -> list[Block]:
    """List blocks across notes/ and log/, optionally filtered.

    USE WHEN: a deterministic, unscored listing is needed — no LLM or
        embedding calls, unlike `recall()`/`frame()`.
    COST: parses every block file in `memory_dir`.
    RETURNS: blocks matching the filters, in file-then-heading order (stable,
        not relevance-ranked).
    NEXT: `find_block` for one specific block; `recall()`/`frame()` for
        ranked retrieval.
    """
    blocks: list[Block] = []
    for subdir_name in _BLOCK_SUBDIRS:
        if category_subdir is not None and subdir_name != category_subdir:
            continue
        subdir = memory_dir / subdir_name
        if not subdir.is_dir():
            continue
        for path in sorted(subdir.glob("**/*.md")):
            result = parse_blocks(path.read_text(encoding="utf-8"))
            blocks.extend(result.blocks)
    if tag is not None:
        blocks = [b for b in blocks if tag in b.tags]
    return blocks


def promote_block(memory_dir: Path, block_id: str, to_notes_file: str) -> Block:
    """Move a block from `log/` to `notes/<to_notes_file>`. Frontmatter unchanged.

    USE WHEN: unreviewed content in `log/` has earned a place in curated
        `notes/`.
    DON'T USE WHEN: the block is already in `notes/` — this raises rather
        than silently no-op, since promoting an already-promoted block
        signals a caller bug, not a benign retry.
    COST: one read from the source file, one read+write on the destination
        file (or create), one write on the source file.
    RETURNS: the `Block` as written into its new file.
    NEXT: `elfmem index` picks up the move on its next rebuild — the block's
        `id` is unchanged, so its history isn't affected by the move.
    """
    found = find_block(memory_dir, block_id)
    if found is None:
        raise BlockNotFoundInFilesError(block_id)
    source_path, block = found

    if not source_path.is_relative_to(memory_dir / "log"):
        raise ElfmemError(
            f"Block '{block_id}' is not in log/ (found at {source_path}).",
            recovery="promote_block only moves blocks out of log/ into notes/.",
        )

    source_result = parse_blocks(source_path.read_text(encoding="utf-8"))
    remaining = [b for b in source_result.blocks if b.id != block_id]
    source_path.write_text(write_blocks(remaining), encoding="utf-8")

    dest_path = memory_dir / "notes" / to_notes_file
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    dest_blocks: list[Block] = []
    if dest_path.exists():
        dest_blocks = parse_blocks(dest_path.read_text(encoding="utf-8")).blocks
    dest_blocks.append(block)
    dest_path.write_text(write_blocks(dest_blocks), encoding="utf-8")
    return block
