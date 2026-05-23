"""Tests for v0.17 Bayesian sufficient statistics (α, β) on blocks.

Verifies:
- Schema has the new ``success_count``/``failure_count`` columns
- Defaults to the Jeffreys prior α=β=0.5 for new rows
- Every outcome write maintains the invariant ``confidence == α / (α + β)``
- Existing v0.15-era rows (only confidence + outcome_evidence) are bootstrapped
  to consistent (α, β) by the v3 → v4 migration
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import StaticPool

from elfmem.adapters.mock import MockEmbeddingService, MockLLMService
from elfmem.db.engine import create_test_engine
from elfmem.db.migrate import CURRENT_SCHEMA_VERSION, ensure_schema_current
from elfmem.db.queries import get_block, seed_builtin_data
from elfmem.operations.consolidate import consolidate
from elfmem.operations.learn import learn
from elfmem.operations.outcome import record_outcome


@pytest.fixture
async def setup():
    engine = await create_test_engine()
    async with engine.begin() as conn:
        await seed_builtin_data(conn)
    mock_llm = MockLLMService(default_alignment=0.65)
    mock_embedding = MockEmbeddingService(dimensions=64)
    yield engine, mock_llm, mock_embedding
    await engine.dispose()


async def _make_active_block(conn, mock_llm, mock_embedding, content="ss block"):
    result = await learn(conn, content=content, category="knowledge", source="api")
    await consolidate(conn, llm=mock_llm, embedding_svc=mock_embedding, current_active_hours=1.0)
    return result.block_id


class TestSchema:
    async def test_blocks_table_has_success_failure_columns(self, test_engine):
        async with test_engine.connect() as conn:
            result = await conn.execute(text("PRAGMA table_info(blocks)"))
            cols = {row[1] for row in result}
        assert "success_count" in cols
        assert "failure_count" in cols

    async def test_new_block_defaults_to_05_05_priors(self, test_engine):
        """Inserting a row through the ORM (no α, β provided) yields the
        Jeffreys prior set by the column ``default``."""
        from elfmem.db.models import blocks
        async with test_engine.begin() as conn:
            await conn.execute(blocks.insert().values(
                id="raw1",
                content="x",
                category="knowledge",
                source="api",
                created_at="2026-05-23T00:00:00",
                status="inbox",
            ))
            row = await get_block(conn, "raw1")
        assert row["success_count"] == 0.5
        assert row["failure_count"] == 0.5


class TestBayesianUpdate:
    async def test_confidence_is_derived_from_alpha_beta(self, setup):
        engine, mock_llm, mock_embedding = setup
        async with engine.begin() as conn:
            block_id = await _make_active_block(conn, mock_llm, mock_embedding)
            await record_outcome(
                conn, block_ids=[block_id], signal=0.8, weight=1.0, source="test",
                current_active_hours=2.0, prior_strength=2.0, reinforce_threshold=0.5,
            )
            block = await get_block(conn, block_id)
        alpha = float(block["success_count"])
        beta = float(block["failure_count"])
        confidence = float(block["confidence"])
        assert abs(confidence - alpha / (alpha + beta)) < 1e-9

    async def test_outcome_updates_sufficient_statistics(self, setup):
        """signal=1.0, weight=1.0 on a fresh α=0.5, β=0.5 row → α=1.5, β=0.5."""
        engine, mock_llm, mock_embedding = setup
        from elfmem.db.models import blocks
        async with engine.begin() as conn:
            # Insert a raw block at Jeffreys defaults — bypass consolidate so
            # the priors are exactly (0.5, 0.5).
            await conn.execute(blocks.insert().values(
                id="fresh",
                content="x",
                category="knowledge",
                source="api",
                created_at="2026-05-23T00:00:00",
                status="active",
            ))
            await record_outcome(
                conn, block_ids=["fresh"], signal=1.0, weight=1.0, source="test",
                current_active_hours=2.0, prior_strength=2.0, reinforce_threshold=0.5,
            )
            block = await get_block(conn, "fresh")
        assert abs(float(block["success_count"]) - 1.5) < 1e-9
        assert abs(float(block["failure_count"]) - 0.5) < 1e-9
        assert abs(float(block["confidence"]) - 0.75) < 1e-9


class TestV015Bootstrap:
    """Verify the v3 → v4 migration bootstraps α, β from legacy columns."""

    async def test_existing_v015_db_bootstraps_correctly(self):
        # Build an in-memory DB at v3 (no success/failure columns) and seed
        # a row with the legacy (confidence, outcome_evidence) state we want
        # to bootstrap from.
        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        async with engine.begin() as conn:
            await conn.execute(text("""
                CREATE TABLE blocks (
                    id TEXT PRIMARY KEY,
                    content TEXT NOT NULL,
                    category TEXT NOT NULL,
                    source TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'inbox',
                    archive_reason TEXT,
                    confidence REAL NOT NULL DEFAULT 0.50,
                    reinforcement_count INTEGER NOT NULL DEFAULT 0,
                    decay_lambda REAL NOT NULL DEFAULT 0.01,
                    last_reinforced_at REAL NOT NULL DEFAULT 0.0,
                    self_alignment REAL,
                    embedding BLOB,
                    embedding_model TEXT,
                    token_count INTEGER,
                    summary TEXT,
                    last_session_id TEXT,
                    outcome_evidence REAL NOT NULL DEFAULT 0.0,
                    source_peer TEXT,
                    share TEXT DEFAULT 'private',
                    envelope_json TEXT,
                    last_scored_at TEXT
                )
            """))
            await conn.execute(text(
                "CREATE TABLE system_config (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            ))
            await conn.execute(text(
                "INSERT INTO system_config (key, value) VALUES ('schema_version', '3')"
            ))
            # A row with established outcomes:
            #   confidence=0.8, outcome_evidence=4.0
            # → expected α = 0.8 * 5.0 = 4.0, β = 0.2 * 5.0 = 1.0
            await conn.execute(text(
                "INSERT INTO blocks (id, content, category, source, created_at, "
                "status, confidence, outcome_evidence) "
                "VALUES ('legacy', 'x', 'knowledge', 'api', "
                "'2026-01-01T00:00:00', 'active', 0.8, 4.0)"
            ))

        async with engine.begin() as conn:
            version = await ensure_schema_current(conn)
        assert version == CURRENT_SCHEMA_VERSION

        async with engine.connect() as conn:
            result = await conn.execute(text(
                "SELECT success_count, failure_count, confidence, outcome_evidence "
                "FROM blocks WHERE id = 'legacy'"
            ))
            alpha, beta, confidence, evidence = result.first()

        assert abs(alpha - 4.0) < 1e-9
        assert abs(beta - 1.0) < 1e-9
        # Confidence is preserved exactly: α / (α + β) == original confidence
        assert abs(confidence - 0.8) < 1e-9
        # outcome_evidence is unchanged by the migration (only α, β are filled).
        assert abs(evidence - 4.0) < 1e-9

        await engine.dispose()
