"""File authority (U-006): writes land in `.elfmem/memory/` first.

The property these protect is that the index is *disposable* — deleting the
database entirely and rebuilding from files plus ledger must lose nothing.
That is the whole point of the v2 substrate; until it holds, files are not
really the source of truth.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from elfmem.config import ElfmemConfig
from elfmem.memory.blockfile import Block, parse_blocks
from elfmem.memory.file_mutation import (
    _atomic_write,
    append_block,
    edit_block,
    forget_block,
    reconcile_status,
)


@pytest.fixture
def memory_dir(tmp_path: Path) -> Path:
    root = tmp_path / ".elfmem" / "memory"
    for sub in ("notes", "log", "archive"):
        (root / sub).mkdir(parents=True)
    return root


class TestOptInIsOffByDefault:
    def test_database_stays_authoritative_unless_asked(self):
        """Flipping authority is the irreversible half of the migration. It
        must never happen because someone upgraded."""
        assert ElfmemConfig().substrate.files_authoritative is False

    def test_flag_is_explicit_opt_in(self):
        cfg = ElfmemConfig.model_validate(
            {"substrate": {"files_authoritative": True}}
        )
        assert cfg.substrate.files_authoritative is True


class TestAtomicWrite:
    def test_replaces_contents_without_leaving_a_temp_file(self, tmp_path: Path):
        target = tmp_path / "notes" / "knowledge.md"
        _atomic_write(target, "first\n")
        _atomic_write(target, "second\n")
        assert target.read_text(encoding="utf-8") == "second\n"
        assert [p.name for p in tmp_path.rglob("*") if p.name.startswith(".")] == []

    def test_original_survives_a_failed_write(self, tmp_path: Path, monkeypatch):
        """A block file holds many blocks, so a partial write does not corrupt
        one block — it truncates every block after the failure point."""
        target = tmp_path / "notes" / "knowledge.md"
        _atomic_write(target, "original\n")

        def boom(*args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(os, "replace", boom)
        with pytest.raises(OSError):
            _atomic_write(target, "replacement\n")
        assert target.read_text(encoding="utf-8") == "original\n"


class TestAppendBlock:
    def test_creates_the_file_and_adds_the_block(self, memory_dir: Path):
        path = append_block(
            memory_dir,
            Block(title="A fact", content="Something true.", id="aaa11111",
                  tags=["billing"]),
            subdir="log", category="knowledge",
        )
        assert path == memory_dir / "log" / "knowledge.md"
        blocks = parse_blocks(path.read_text(encoding="utf-8")).blocks
        assert [b.id for b in blocks] == ["aaa11111"]
        assert blocks[0].tags == ["billing"]

    def test_appending_the_same_id_twice_is_a_no_op(self, memory_dir: Path):
        block = Block(title="A", content="Something.", id="aaa11111")
        append_block(memory_dir, block, category="knowledge")
        append_block(memory_dir, block, category="knowledge")
        blocks = parse_blocks(
            (memory_dir / "log" / "knowledge.md").read_text(encoding="utf-8")
        ).blocks
        assert len(blocks) == 1

    def test_existing_blocks_in_the_file_are_preserved(self, memory_dir: Path):
        append_block(memory_dir, Block(title="A", content="First.", id="aaa11111"))
        append_block(memory_dir, Block(title="B", content="Second.", id="bbb22222"))
        blocks = parse_blocks(
            (memory_dir / "log" / "knowledge.md").read_text(encoding="utf-8")
        ).blocks
        assert [b.id for b in blocks] == ["aaa11111", "bbb22222"]


class TestReconcileStatus:
    def test_promoted_blocks_move_from_log_to_notes(self, memory_dir: Path):
        """Promotion happens in the index. Without this the block sits in
        log/ forever and every rebuild returns it to the inbox."""
        append_block(memory_dir, Block(title="A", content="First.", id="aaa11111"))
        append_block(memory_dir, Block(title="B", content="Second.", id="bbb22222"))

        moved = reconcile_status(
            memory_dir, active_categories={"aaa11111": "knowledge"}
        )
        assert moved == 1
        notes = parse_blocks(
            (memory_dir / "notes" / "knowledge.md").read_text(encoding="utf-8")
        ).blocks
        log = parse_blocks(
            (memory_dir / "log" / "knowledge.md").read_text(encoding="utf-8")
        ).blocks
        assert [b.id for b in notes] == ["aaa11111"]
        assert [b.id for b in log] == ["bbb22222"]

    def test_is_idempotent(self, memory_dir: Path):
        append_block(memory_dir, Block(title="A", content="First.", id="aaa11111"))
        active = {"aaa11111": "knowledge"}
        assert reconcile_status(memory_dir, active_categories=active) == 1
        assert reconcile_status(memory_dir, active_categories=active) == 0

    def test_block_lands_in_the_file_named_for_its_category(self, memory_dir: Path):
        append_block(memory_dir, Block(title="A", content="First.", id="aaa11111"))
        reconcile_status(memory_dir, active_categories={"aaa11111": "decision"})
        assert (memory_dir / "notes" / "decision.md").exists()

    def test_no_log_directory_is_not_an_error(self, tmp_path: Path):
        assert reconcile_status(tmp_path, active_categories={"x": "y"}) == 0


class TestEditAndForgetKeepIdentity:
    def test_edit_changes_content_but_never_the_id(self, memory_dir: Path):
        append_block(memory_dir, Block(title="A", content="Old.", id="aaa11111"))
        updated = edit_block(memory_dir, "aaa11111", "New content.")
        assert updated.id == "aaa11111"
        blocks = parse_blocks(
            (memory_dir / "log" / "knowledge.md").read_text(encoding="utf-8")
        ).blocks
        assert blocks[0].content == "New content."
        assert blocks[0].id == "aaa11111"

    def test_forget_removes_only_the_named_block(self, memory_dir: Path):
        append_block(memory_dir, Block(title="A", content="First.", id="aaa11111"))
        append_block(memory_dir, Block(title="B", content="Second.", id="bbb22222"))
        assert forget_block(memory_dir, "aaa11111") is True
        blocks = parse_blocks(
            (memory_dir / "log" / "knowledge.md").read_text(encoding="utf-8")
        ).blocks
        assert [b.id for b in blocks] == ["bbb22222"]

    def test_forget_is_idempotent(self, memory_dir: Path):
        append_block(memory_dir, Block(title="A", content="First.", id="aaa11111"))
        assert forget_block(memory_dir, "aaa11111") is True
        assert forget_block(memory_dir, "aaa11111") is False


class TestDerivedStateReachesTheLedger:
    """The flip made the index disposable, which means anything written only
    to the index is written to something that gets deleted. These are the
    three paths that produce derived state."""

    def test_promotion_creates_the_posterior_absolutely(self, tmp_path: Path):
        """Promotion is where the Beta posterior comes into being
        (alpha = confidence, beta = 1 - confidence). Later outcome and rescore
        events accumulate on top of it, so it must set, not add."""
        from elfmem.memory.ledger import KIND_PROMOTE, append, replay

        d = tmp_path / "ledger"
        append(d, KIND_PROMOTE, active_hours=1.0, id="aaa11111",
               conf=0.8, sig=0.8, lam=0.001, sum="A distillation.")
        state = replay(d).blocks["aaa11111"]
        assert state.alpha == pytest.approx(0.8)
        assert state.beta == pytest.approx(0.2)
        assert state.decay_lambda == pytest.approx(0.001)
        assert state.summary == "A distillation."

    def test_rescore_accumulates_on_top_of_promotion(self, tmp_path: Path):
        from elfmem.memory.ledger import (
            KIND_PROMOTE,
            KIND_RESCORE,
            append,
            replay,
        )

        d = tmp_path / "ledger"
        append(d, KIND_PROMOTE, active_hours=1.0, id="aaa11111", conf=0.8)
        append(d, KIND_RESCORE, active_hours=2.0, id="aaa11111",
               sig=1.0, w=0.5, sum="Refreshed.")
        state = replay(d).blocks["aaa11111"]
        assert state.alpha == pytest.approx(1.3)   # 0.8 + 1.0*0.5
        assert state.beta == pytest.approx(0.2)    # 0.2 + 0.0*0.5
        assert state.summary == "Refreshed."

    def test_inferred_tags_are_written_back_into_the_file(self, memory_dir: Path):
        """Tags are declared state, so an LLM-inferred tag has to reach the
        file. Left in the index alone, a rebuild drops every derived tag,
        taking frame('self')'s tag filter and the decay tier with it."""
        from elfmem.memory.file_mutation import sync_tags

        append_block(
            memory_dir,
            Block(title="A", content="Something.", id="aaa11111", tags=["manual"]),
        )
        changed = sync_tags(memory_dir, {"aaa11111": ["manual", "self/value"]})
        assert changed == 1
        blocks = parse_blocks(
            (memory_dir / "log" / "knowledge.md").read_text(encoding="utf-8")
        ).blocks
        assert blocks[0].tags == ["manual", "self/value"]

    def test_sync_tags_rewrites_nothing_when_tags_already_match(
        self, memory_dir: Path
    ):
        from elfmem.memory.file_mutation import sync_tags

        append_block(
            memory_dir,
            Block(title="A", content="Something.", id="aaa11111", tags=["billing"]),
        )
        assert sync_tags(memory_dir, {"aaa11111": ["billing"]}) == 0
