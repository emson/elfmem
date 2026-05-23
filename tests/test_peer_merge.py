"""Tests for v0.17 arithmetic peer merge — BUNDLE_VERSION 2.

Verifies:
- BUNDLE_VERSION is 2; v1 bundles remain readable
- v2 exports include (success_count, failure_count)
- v1 imports bootstrap (α, β) from confidence (no special-case branches)
- v2 imports use trust-scaled remote evidence
- Re-import of known content merges arithmetically (no longer skips)
- trust=0.0 ignores peer entirely; trust=1.0 fully accepts
- The ``merge_peer_evidence`` pure function is symmetric in remote (α, β)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text

from elfmem.api import MemorySystem
from elfmem.config import ElfmemConfig, MemoryConfig
from elfmem.operations.peer import (
    BUNDLE_VERSION,
    merge_peer_evidence,
)

# ── Fixture ───────────────────────────────────────────────────────────────────


@pytest.fixture
async def system(test_engine, mock_llm, mock_embedding):
    cfg = ElfmemConfig(memory=MemoryConfig(inbox_threshold=3))
    sys = MemorySystem(
        engine=test_engine,
        llm_service=mock_llm,
        embedding_service=mock_embedding,
        config=cfg,
    )
    await sys.peer_init("Self")
    return sys


def _bundle(version: int, blocks: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "version": version,
        "exported_at": "2026-05-23T00:00:00Z",
        "from_did": "elf:sender",
        "block_count": len(blocks),
        "blocks": blocks,
        "edges": [],
    }


async def _fetch(system: MemorySystem, block_id: str) -> dict[str, Any]:
    from elfmem.db.queries import get_block
    async with system._engine.connect() as conn:
        block = await get_block(conn, block_id)
    assert block is not None, f"block {block_id} missing"
    return block


# ── BUNDLE_VERSION + export ───────────────────────────────────────────────────


class TestBundleVersion:
    def test_bundle_version_is_2(self):
        assert BUNDLE_VERSION == 2

    async def test_v2_export_includes_alpha_beta(
        self, system: MemorySystem, tmp_path: Path,
    ):
        # A block that will reach the export — public share, knowledge category.
        async with system.session():
            await system.learn("Exportable knowledge")
            await system.learn("filler 2")
            await system.learn("filler 3")
            await system.consolidate()

        async with system._engine.begin() as conn:
            await conn.execute(text(
                "UPDATE blocks SET share = 'public' WHERE status = 'active'"
            ))

        out = tmp_path / "bundle.json"
        await system.export_blocks(share_level="public", output_path=str(out))

        bundle = json.loads(out.read_text())
        assert bundle["version"] == 2
        assert bundle["blocks"], "bundle should contain at least one block"
        for block in bundle["blocks"]:
            assert "success_count" in block
            assert "failure_count" in block
            # And the canonical denormalised view ships too:
            assert "confidence" in block


# ── Import: v1 bootstrap vs v2 trust-scaled ───────────────────────────────────


class TestImportSeeding:
    async def test_v1_message_imports_via_confidence_bootstrap(
        self, system: MemorySystem, tmp_path: Path,
    ):
        """v1 payload (no α/β): bootstrap from confidence, then trust-scale."""
        await system.peer_add("elf:sender", "Sender")
        await system.peer_trust("elf:sender", set_value=0.5)

        bundle = _bundle(1, [
            {"id": "ignored", "content": "v1 block", "category": "knowledge",
             "tags": [], "confidence": 0.8, "created_at": "2026-05-23T00:00:00Z"},
        ])
        path = tmp_path / "v1.json"
        path.write_text(json.dumps(bundle))
        result = await system.import_blocks(str(path), from_peer="elf:sender")
        assert result.blocks_imported == 1

        from elfmem.memory.blocks import compute_content_hash
        block_id = compute_content_hash("v1 block")
        block = await _fetch(system, block_id)
        # Bootstrap: remote_α = 0.8, remote_β = 0.2; trust = 0.5.
        # Local: α = 0.5 + 0.8 * 0.5 = 0.9; β = 0.5 + 0.2 * 0.5 = 0.6.
        assert abs(float(block["success_count"]) - 0.9) < 1e-9
        assert abs(float(block["failure_count"]) - 0.6) < 1e-9

    async def test_v2_message_imports_with_trust_scaling(
        self, system: MemorySystem, tmp_path: Path,
    ):
        """v2 payload (α=10, β=2): trust=0.5 ⇒ local α = 0.5 + 5.0 = 5.5."""
        await system.peer_add("elf:sender", "Sender")
        await system.peer_trust("elf:sender", set_value=0.5)

        bundle = _bundle(2, [
            {"id": "x", "content": "v2 block", "category": "knowledge",
             "tags": [], "confidence": 10.0 / 12.0,
             "success_count": 10.0, "failure_count": 2.0,
             "created_at": "2026-05-23T00:00:00Z"},
        ])
        path = tmp_path / "v2.json"
        path.write_text(json.dumps(bundle))
        await system.import_blocks(str(path), from_peer="elf:sender")

        from elfmem.memory.blocks import compute_content_hash
        block = await _fetch(system, compute_content_hash("v2 block"))
        assert abs(float(block["success_count"]) - 5.5) < 1e-9
        assert abs(float(block["failure_count"]) - 1.5) < 1e-9


class TestTrustExtremes:
    async def test_trust_zero_ignores_peer_completely(
        self, system: MemorySystem, tmp_path: Path,
    ):
        await system.peer_add("elf:sender", "Sender")
        await system.peer_trust("elf:sender", set_value=0.0)
        bundle = _bundle(2, [
            {"id": "x", "content": "ignored content", "category": "knowledge",
             "tags": [], "confidence": 0.99,
             "success_count": 100.0, "failure_count": 1.0,
             "created_at": "2026-05-23T00:00:00Z"},
        ])
        path = tmp_path / "t0.json"
        path.write_text(json.dumps(bundle))
        await system.import_blocks(str(path), from_peer="elf:sender")

        from elfmem.memory.blocks import compute_content_hash
        block = await _fetch(system, compute_content_hash("ignored content"))
        # Pure Jeffreys prior — peer evidence weighted to zero.
        assert abs(float(block["success_count"]) - 0.5) < 1e-9
        assert abs(float(block["failure_count"]) - 0.5) < 1e-9

    async def test_trust_one_fully_accepts_peer(
        self, system: MemorySystem, tmp_path: Path,
    ):
        await system.peer_add("elf:sender", "Sender")
        await system.peer_trust("elf:sender", set_value=1.0)
        bundle = _bundle(2, [
            {"id": "x", "content": "trusted content", "category": "knowledge",
             "tags": [], "confidence": 0.9,
             "success_count": 9.0, "failure_count": 1.0,
             "created_at": "2026-05-23T00:00:00Z"},
        ])
        path = tmp_path / "t1.json"
        path.write_text(json.dumps(bundle))
        await system.import_blocks(str(path), from_peer="elf:sender")

        from elfmem.memory.blocks import compute_content_hash
        block = await _fetch(system, compute_content_hash("trusted content"))
        # Jeffreys + full remote: α = 0.5 + 9.0 = 9.5; β = 0.5 + 1.0 = 1.5.
        assert abs(float(block["success_count"]) - 9.5) < 1e-9
        assert abs(float(block["failure_count"]) - 1.5) < 1e-9


# ── Re-import is now an arithmetic merge, not a skip ─────────────────────────


class TestReimportMerge:
    async def test_reimport_folds_evidence_arithmetically(
        self, system: MemorySystem, tmp_path: Path,
    ):
        """v0.17: re-importing the same content folds (α, β) in additively."""
        await system.peer_add("elf:sender", "Sender")
        await system.peer_trust("elf:sender", set_value=1.0)

        first = _bundle(2, [
            {"id": "ignored", "content": "shared knowledge",
             "category": "knowledge", "tags": [],
             "confidence": 10.0 / 12.0,
             "success_count": 10.0, "failure_count": 2.0,
             "created_at": "2026-05-23T00:00:00Z"},
        ])
        path1 = tmp_path / "first.json"
        path1.write_text(json.dumps(first))
        r1 = await system.import_blocks(str(path1), from_peer="elf:sender")
        assert r1.blocks_imported == 1

        from elfmem.memory.blocks import compute_content_hash
        block_id = compute_content_hash("shared knowledge")
        after_first = await _fetch(system, block_id)
        # Fresh seed: Jeffreys(0.5, 0.5) + remote(10, 2) at trust=1 → (10.5, 2.5).
        assert abs(float(after_first["success_count"]) - 10.5) < 1e-9
        assert abs(float(after_first["failure_count"]) - 2.5) < 1e-9

        # Second import — same content, updated evidence: peer claims another
        # 20 successes vs 5 failures since last time.
        second = _bundle(2, [
            {"id": "ignored", "content": "shared knowledge",
             "category": "knowledge", "tags": [],
             "confidence": 20.0 / 25.0,
             "success_count": 20.0, "failure_count": 5.0,
             "created_at": "2026-05-23T01:00:00Z"},
        ])
        path2 = tmp_path / "second.json"
        path2.write_text(json.dumps(second))
        r2 = await system.import_blocks(str(path2), from_peer="elf:sender")
        assert r2.blocks_imported == 1
        assert r2.blocks_skipped == 0

        after_second = await _fetch(system, block_id)
        # Merge into existing: α = 10.5 + 20 = 30.5; β = 2.5 + 5 = 7.5.
        # No second Jeffreys prior — paid once at first seed.
        assert abs(float(after_second["success_count"]) - 30.5) < 1e-9
        assert abs(float(after_second["failure_count"]) - 7.5) < 1e-9


# ── Pure function ────────────────────────────────────────────────────────────


class TestMergePeerEvidencePure:
    def test_merge_peer_evidence_is_symmetric_pure_function(self):
        """Output for two remote observations is identical whichever order
        they are merged (β arithmetic merge is commutative under fixed trust)."""
        a = merge_peer_evidence(0.5, 0.5, 4.0, 1.0, trust=1.0)
        b = merge_peer_evidence(*a, 3.0, 2.0, trust=1.0)
        # Merging the same two observations in the opposite order
        c = merge_peer_evidence(0.5, 0.5, 3.0, 2.0, trust=1.0)
        d = merge_peer_evidence(*c, 4.0, 1.0, trust=1.0)
        assert abs(b[0] - d[0]) < 1e-9
        assert abs(b[1] - d[1]) < 1e-9

    def test_trust_zero_is_identity(self):
        new_a, new_b = merge_peer_evidence(7.0, 3.0, 100.0, 100.0, trust=0.0)
        assert new_a == 7.0
        assert new_b == 3.0

    def test_trust_one_is_simple_sum(self):
        new_a, new_b = merge_peer_evidence(2.0, 1.0, 5.0, 4.0, trust=1.0)
        assert new_a == 7.0
        assert new_b == 5.0
