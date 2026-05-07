"""Tests for role-based idempotency of MemorySystem.setup().

The 0.13.0 constitutional re-seed bug: every setup() call inserted 10 fresh
copies of the seed blocks because dedup was content-hash keyed and only
caught inbox-stage duplicates. Active/archived collisions silently produced
ghost copies that diluted the SELF frame.

The fix: each constitutional block has a stable role tag (self/role/<name>).
setup() queries existing role tags and skips seeds whose role is filled.
"""

from __future__ import annotations

from elfmem.api import MemorySystem
from elfmem.config import ElfmemConfig, MemoryConfig
from elfmem.seed import CONSTITUTIONAL_ROLES, CONSTITUTIONAL_SEED


class TestRoleTagsPresent:
    def test_seed_has_role_field(self):
        for block in CONSTITUTIONAL_SEED:
            assert "role" in block, f"seed block missing role: {block.get('content')[:50]}"
            assert block["role"], "role must be non-empty"

    def test_seed_has_role_tag(self):
        for block in CONSTITUTIONAL_SEED:
            role = block["role"]
            tags = block["tags"]
            assert f"self/role/{role}" in tags

    def test_roles_are_unique(self):
        roles = [b["role"] for b in CONSTITUTIONAL_SEED]
        assert len(roles) == len(set(roles)), "role names must be unique"

    def test_roles_constant_matches_seed(self):
        assert tuple(b["role"] for b in CONSTITUTIONAL_SEED) == CONSTITUTIONAL_ROLES


class TestSetupIdempotency:
    async def test_first_setup_creates_all_roles(
        self, test_engine, mock_llm, mock_embedding,
    ):
        cfg = ElfmemConfig(memory=MemoryConfig(inbox_threshold=3))
        system = MemorySystem(
            engine=test_engine, llm_service=mock_llm,
            embedding_service=mock_embedding, config=cfg,
        )
        result = await system.setup(seed=True)
        assert result.blocks_created == len(CONSTITUTIONAL_SEED)

    async def test_re_setup_creates_no_new_blocks(
        self, test_engine, mock_llm, mock_embedding,
    ):
        cfg = ElfmemConfig(memory=MemoryConfig(inbox_threshold=3))
        system = MemorySystem(
            engine=test_engine, llm_service=mock_llm,
            embedding_service=mock_embedding, config=cfg,
        )
        await system.setup(seed=True)
        # Second call: every role is filled (inbox status counts) → 0 new.
        result = await system.setup(seed=True)
        assert result.blocks_created == 0
        assert result.total_attempted == 0

    async def test_existing_role_blocks_query(
        self, test_engine, mock_llm, mock_embedding,
    ):
        cfg = ElfmemConfig(memory=MemoryConfig(inbox_threshold=3))
        system = MemorySystem(
            engine=test_engine, llm_service=mock_llm,
            embedding_service=mock_embedding, config=cfg,
        )
        # Empty DB: no roles filled.
        roles = await system._existing_constitutional_roles()
        assert roles == set()

        await system.setup(seed=True)
        roles = await system._existing_constitutional_roles()
        # All 10 roles present.
        assert roles == set(CONSTITUTIONAL_ROLES)

    async def test_partial_seed_fills_missing_roles_only(
        self, test_engine, mock_llm, mock_embedding,
    ):
        cfg = ElfmemConfig(memory=MemoryConfig(inbox_threshold=3))
        system = MemorySystem(
            engine=test_engine, llm_service=mock_llm,
            embedding_service=mock_embedding, config=cfg,
        )
        # Manually seed one role.
        await system.remember(
            "user-customised content for the curiosity slot",
            tags=["self/constitutional", "self/role/curiosity"],
        )
        # Now setup: should skip 'curiosity' and create the other 9.
        result = await system.setup(seed=True)
        assert result.blocks_created == len(CONSTITUTIONAL_SEED) - 1
        # The user's customised content survives.
        roles = await system._existing_constitutional_roles()
        assert "curiosity" in roles
        # And it's the user's text, not stock.
        from sqlalchemy import text
        async with test_engine.connect() as conn:
            rows = await conn.execute(text(
                "SELECT b.content FROM blocks b "
                "JOIN block_tags t ON b.id = t.block_id "
                "WHERE t.tag = 'self/role/curiosity'"
            ))
            contents = [r[0] for r in rows.fetchall()]
        assert any("user-customised" in c for c in contents)
        assert not any("Curiosity is my primary drive" in c for c in contents)

    async def test_setup_with_identity_still_runs_after_seed_skip(
        self, test_engine, mock_llm, mock_embedding,
    ):
        cfg = ElfmemConfig(memory=MemoryConfig(inbox_threshold=3))
        system = MemorySystem(
            engine=test_engine, llm_service=mock_llm,
            embedding_service=mock_embedding, config=cfg,
        )
        await system.setup(seed=True)
        # Re-running with an identity addition should still add the identity
        # block, even though all role seeds are skipped.
        result = await system.setup(
            seed=True, identity="I am a domain-specific assistant",
        )
        # Identity is added (1 new block).
        assert result.blocks_created == 1
