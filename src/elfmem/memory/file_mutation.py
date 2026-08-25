"""File-native mutation primitives over ``.elfmem/memory/`` — U-003.

Pure filesystem operations against ``notes/*.md`` and ``log/*.md`` (never
``self.md`` — the constitution isn't a mutable block, per Invariant 2).
No database, no index, no LLM or embedding calls: editing a block is a text
edit, exactly as the plan doc's mutation-API table promises.

Wired into ``MemorySystem`` only when ``substrate.files_authoritative`` is
on (U-006, flip authority); otherwise the live system stays DB-primary and
this module is exercised by migration tooling alone.

Two properties matter once these are on a live write path rather than in a
rehearsal:

- **Every write is atomic.** A block file is rewritten whole, so a crash
  partway through ``write_text`` truncates the corpus. Writes go to a
  sibling temp file and are moved into place with ``os.replace``, which is
  atomic within a filesystem. Same pattern the peer protocol already uses
  for envelopes (ADR 0005).
- **Read-modify-write is locked.** Editing one block rewrites its whole
  file, so two concurrent writers would silently lose one edit. Each
  mutation holds an exclusive lock on the file for the read-modify-write.
  The plan doc accepted concurrent file conflicts as a known trade while
  this was rehearsal-only; making it the live path makes it a real risk,
  and a lock is the cheap end of the mitigation.
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import Iterator
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


@contextlib.contextmanager
def _locked(path: Path) -> Iterator[None]:
    """Hold an exclusive lock for a read-modify-write on *path*.

    The lock lives on a sibling ``.<name>.lock`` file rather than on the
    block file itself, so it survives the ``os.replace`` that swaps the block
    file out from under it. Degrades to no locking where ``fcntl`` is absent
    (Windows) rather than failing: single-writer use is unaffected, and that
    is the only mode available there anyway.
    """
    try:
        import fcntl
    except ImportError:  # pragma: no cover - POSIX is the supported target
        yield
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(f".{path.name}.lock")
    with lock_path.open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _atomic_write(path: Path, text: str) -> None:
    """Replace *path*'s contents atomically.

    A block file holds many blocks, so a partial write does not corrupt one
    block -- it truncates every block after the failure point.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def append_block(
    memory_dir: Path, block: Block, *, subdir: str = "log", category: str = "knowledge",
) -> Path:
    """Add a new block to ``<subdir>/<category>.md``, creating the file if needed.

    USE WHEN: `learn()` under file authority -- a brand-new block entering the
        substrate.
    DON'T USE WHEN: the block already exists; that is `edit_block`.
    COST: one locked read + one atomic write of a single category file. No
        DB, no LLM, no embedding -- `learn()` stays a heartbeat operation.
    RETURNS: the path written.
    NEXT: the caller updates the derived index; a rebuild would recover it
        anyway, since this file is the truth.
    """
    path = memory_dir / subdir / f"{category}.md"
    with _locked(path):
        existing: list[Block] = []
        if path.exists():
            existing = parse_blocks(path.read_text(encoding="utf-8")).blocks
        if any(b.id == block.id for b in existing):
            return path
        existing.append(block)
        _atomic_write(path, write_blocks(existing))
    return path


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

    with _locked(path):
        result = parse_blocks(path.read_text(encoding="utf-8"))
        updated: Block | None = None
        for block in result.blocks:
            if block.id == block_id:
                block.content = new_content
                updated = block
        if updated is None:
            # Another writer removed it between find_block and the lock.
            raise BlockNotFoundInFilesError(block_id)
        _atomic_write(path, write_blocks(result.blocks))
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

    with _locked(path):
        result = parse_blocks(path.read_text(encoding="utf-8"))
        remaining = [b for b in result.blocks if b.id != block_id]
        if len(remaining) == len(result.blocks):
            return False
        _atomic_write(path, write_blocks(remaining))
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

    dest_path = memory_dir / "notes" / to_notes_file
    # Destination first: a crash between the two writes must leave the block
    # duplicated (recoverable, and the index dedups by id) rather than gone.
    with _locked(dest_path):
        dest_blocks: list[Block] = []
        if dest_path.exists():
            dest_blocks = parse_blocks(dest_path.read_text(encoding="utf-8")).blocks
        if not any(b.id == block_id for b in dest_blocks):
            dest_blocks.append(block)
            _atomic_write(dest_path, write_blocks(dest_blocks))

    with _locked(source_path):
        source_result = parse_blocks(source_path.read_text(encoding="utf-8"))
        remaining = [b for b in source_result.blocks if b.id != block_id]
        _atomic_write(source_path, write_blocks(remaining))
    return block


def reconcile_status(
    memory_dir: Path, *, active_categories: dict[str, str],
) -> int:
    """Move blocks between ``log/`` and ``notes/`` to match their status.

    USE WHEN: after `consolidate()` under file authority. Promotion happens in
        the index -- a block moves from inbox to active -- and the substrate
        has to follow, or the block sits in `log/` forever and every rebuild
        returns it to the inbox.
    DON'T USE WHEN: files are not authoritative; there is nothing to reconcile.
    COST: parses every block file, then one atomic write per file that moved.
    RETURNS: how many blocks were relocated.
    NEXT: nothing -- this is idempotent and self-healing, so it is safe to
        call after any operation that may have changed a block's status.

    Deliberately reconciles from a *set of ids*, not from a list of things
    consolidation just did. Threading promoted ids through the pipeline would
    only fix promotion; reconciling the whole substrate also repairs drift
    from an interrupted run, a hand-edit, or a partially-applied migration.
    """
    moved = 0
    log_dir = memory_dir / "log"
    if not log_dir.is_dir():
        return 0
    for path in sorted(log_dir.glob("**/*.md")):
        blocks = parse_blocks(path.read_text(encoding="utf-8")).blocks
        promotable = [
            b for b in blocks if b.id is not None and b.id in active_categories
        ]
        for block in promotable:
            assert block.id is not None
            promote_block(
                memory_dir, block.id, f"{active_categories[block.id]}.md"
            )
            moved += 1
    return moved
