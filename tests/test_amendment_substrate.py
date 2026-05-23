"""Substrate tests for v0.18 constitutional review.

Covers the v4→v5 schema migration (block_amendments audit table) and the new
result types (ProposedAmendment, ConstitutionalReviewResult, AmendmentResult,
AmendmentRecord). No business logic yet — that lands in later commits.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import StaticPool

from elfmem import (
    AmendmentRecord,
    AmendmentResult,
    ConstitutionalReviewResult,
    ProposedAmendment,
)
from elfmem.db.migrate import CURRENT_SCHEMA_VERSION, ensure_schema_current


async def _create_v4_engine() -> AsyncEngine:
    """Create an in-memory database with v4 schema (pre-block_amendments)."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        # Enable FK enforcement (SQLite default-off; mirrors the production engine).
        await conn.execute(text("PRAGMA foreign_keys = ON"))
        await conn.execute(text("""
            CREATE TABLE blocks (
                id TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                category TEXT NOT NULL,
                source TEXT NOT NULL,
                created_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'inbox',
                confidence REAL NOT NULL DEFAULT 0.50,
                reinforcement_count INTEGER NOT NULL DEFAULT 0,
                decay_lambda REAL NOT NULL DEFAULT 0.01,
                last_reinforced_at REAL NOT NULL DEFAULT 0.0,
                outcome_evidence REAL NOT NULL DEFAULT 0.0
            )
        """))
        await conn.execute(text("""
            CREATE TABLE system_config (
                key TEXT PRIMARY KEY, value TEXT NOT NULL
            )
        """))
        await conn.execute(text(
            "INSERT INTO system_config (key, value) VALUES ('schema_version', '4')"
        ))
        await conn.execute(text(
            "INSERT INTO blocks (id, content, category, source, created_at, status) "
            "VALUES ('b1', 'constitutional content', 'self', 'api', "
            "'2026-01-01T00:00:00', 'active')"
        ))
    return engine


# ── Migration tests ──────────────────────────────────────────────────────────


class TestSchemaV5Migration:
    async def test_schema_v5_creates_block_amendments_table(self):
        engine = await _create_v4_engine()
        async with engine.begin() as conn:
            version = await ensure_schema_current(conn)
        assert version == CURRENT_SCHEMA_VERSION
        assert version >= 5

        async with engine.connect() as conn:
            result = await conn.execute(text("PRAGMA table_info(block_amendments)"))
            cols = {row[1]: row for row in result}
        expected = {
            "id", "block_id", "timestamp", "pre_content", "post_content",
            "pre_summary", "post_summary", "drift_score", "rationale",
            "acceptor", "reverted_at",
        }
        assert expected.issubset(cols.keys())
        # block_id and pre/post content and drift_score and acceptor must be NOT NULL.
        # PRAGMA table_info column index 3 is notnull (0/1).
        assert cols["block_id"][3] == 1
        assert cols["pre_content"][3] == 1
        assert cols["post_content"][3] == 1
        assert cols["drift_score"][3] == 1
        assert cols["acceptor"][3] == 1
        # Nullable: rationale, pre_summary, post_summary, reverted_at
        assert cols["rationale"][3] == 0
        assert cols["pre_summary"][3] == 0
        assert cols["post_summary"][3] == 0
        assert cols["reverted_at"][3] == 0

        await engine.dispose()

    async def test_schema_v5_creates_indexes(self):
        engine = await _create_v4_engine()
        async with engine.begin() as conn:
            await ensure_schema_current(conn)

        async with engine.connect() as conn:
            result = await conn.execute(text(
                "SELECT name FROM sqlite_master "
                "WHERE type='index' AND tbl_name='block_amendments'"
            ))
            names = {row[0] for row in result}
        assert "idx_block_amendments_block_id" in names
        assert "idx_block_amendments_timestamp" in names

        await engine.dispose()

    async def test_block_amendments_fk_cascade_on_block_delete(self):
        engine = await _create_v4_engine()
        async with engine.begin() as conn:
            await ensure_schema_current(conn)

        async with engine.begin() as conn:
            await conn.execute(text("PRAGMA foreign_keys = ON"))
            await conn.execute(text(
                "INSERT INTO block_amendments "
                "(block_id, pre_content, post_content, drift_score, acceptor) "
                "VALUES ('b1', 'old', 'new', 0.4, 'agent')"
            ))
            await conn.execute(text("DELETE FROM blocks WHERE id = 'b1'"))
            result = await conn.execute(text(
                "SELECT COUNT(*) FROM block_amendments WHERE block_id = 'b1'"
            ))
            assert result.scalar() == 0

        await engine.dispose()

    async def test_block_amendments_acceptor_check_constraint(self):
        engine = await _create_v4_engine()
        async with engine.begin() as conn:
            await ensure_schema_current(conn)

        async with engine.begin() as conn:
            with pytest.raises(IntegrityError):
                await conn.execute(text(
                    "INSERT INTO block_amendments "
                    "(block_id, pre_content, post_content, drift_score, acceptor) "
                    "VALUES ('b1', 'old', 'new', 0.4, 'robot')"
                ))

        await engine.dispose()

    async def test_block_amendments_reverted_at_nullable(self):
        engine = await _create_v4_engine()
        async with engine.begin() as conn:
            await ensure_schema_current(conn)

        async with engine.begin() as conn:
            await conn.execute(text(
                "INSERT INTO block_amendments "
                "(block_id, pre_content, post_content, drift_score, acceptor) "
                "VALUES ('b1', 'old', 'new', 0.4, 'agent')"
            ))
            row = (await conn.execute(text(
                "SELECT reverted_at FROM block_amendments WHERE block_id = 'b1'"
            ))).first()
            assert row[0] is None

            await conn.execute(text(
                "UPDATE block_amendments SET reverted_at = CURRENT_TIMESTAMP "
                "WHERE block_id = 'b1'"
            ))
            row = (await conn.execute(text(
                "SELECT reverted_at FROM block_amendments WHERE block_id = 'b1'"
            ))).first()
            assert row[0] is not None

        await engine.dispose()

    async def test_migration_v4_to_v5_idempotent(self):
        engine = await _create_v4_engine()
        async with engine.begin() as conn:
            v_first = await ensure_schema_current(conn)
        async with engine.begin() as conn:
            v_second = await ensure_schema_current(conn)
        assert v_first == v_second == CURRENT_SCHEMA_VERSION
        await engine.dispose()


# ── Type tests ───────────────────────────────────────────────────────────────


class TestProposedAmendment:
    def test_to_dict_roundtrip(self):
        p = ProposedAmendment(
            block_id="blk-1234567890",
            original_content="Old principle.",
            proposed_content="New principle.",
            rationale="Recent evidence contradicts the old wording.",
            drift_score=0.42,
        )
        d = p.to_dict()
        assert d["block_id"] == "blk-1234567890"
        assert d["original_content"] == "Old principle."
        assert d["proposed_content"] == "New principle."
        assert d["rationale"].startswith("Recent")
        assert d["drift_score"] == 0.42

    def test_summary_renders(self):
        p = ProposedAmendment(
            block_id="blk-1234567890",
            original_content="a" * 100,
            proposed_content="b",
            rationale="r",
            drift_score=0.5,
        )
        s = p.summary
        assert "blk-1234" in s
        assert "0.50" in s

    def test_str_equals_summary(self):
        p = ProposedAmendment(
            block_id="blk-1", original_content="o", proposed_content="n",
            rationale="r", drift_score=0.1,
        )
        assert str(p) == p.summary


class TestConstitutionalReviewResult:
    def test_summary_insufficient_history(self):
        r = ConstitutionalReviewResult(
            proposals=[], reviewed_count=0, skipped_count=0,
            insufficient_history=True,
        )
        assert "insufficient" in r.summary

    def test_summary_no_proposals(self):
        r = ConstitutionalReviewResult(
            proposals=[], reviewed_count=5, skipped_count=2,
            insufficient_history=False,
        )
        s = r.summary
        assert "5" in s and "2" in s and "no amendments" in s

    def test_summary_with_proposals(self):
        prop = ProposedAmendment(
            block_id="b", original_content="o", proposed_content="n",
            rationale="r", drift_score=0.3,
        )
        r = ConstitutionalReviewResult(
            proposals=[prop, prop], reviewed_count=10, skipped_count=1,
            insufficient_history=False,
        )
        s = r.summary
        assert "2 amendments" in s

    def test_to_dict(self):
        prop = ProposedAmendment(
            block_id="b", original_content="o", proposed_content="n",
            rationale="r", drift_score=0.3,
        )
        r = ConstitutionalReviewResult(
            proposals=[prop], reviewed_count=1, skipped_count=0,
            insufficient_history=False,
        )
        d = r.to_dict()
        assert d["reviewed_count"] == 1
        assert d["insufficient_history"] is False
        assert len(d["proposals"]) == 1
        assert d["proposals"][0]["block_id"] == "b"


class TestAmendmentResult:
    def test_summary_renders(self):
        ts = datetime(2026, 5, 23, 12, 0, 0)
        r = AmendmentResult(
            amendment_id=7,
            block_id="blk-deadbeef",
            pre_content="before",
            post_content="after",
            timestamp=ts,
            acceptor="agent",
        )
        s = r.summary
        assert "7" in s and "blk-dead" in s and "agent" in s
        assert "2026-05-23" in s

    def test_to_dict(self):
        ts = datetime(2026, 5, 23, 12, 0, 0)
        r = AmendmentResult(
            amendment_id=7, block_id="b", pre_content="a",
            post_content="b", timestamp=ts, acceptor="user",
        )
        d = r.to_dict()
        assert d["amendment_id"] == 7
        assert d["acceptor"] == "user"
        assert d["timestamp"] == ts.isoformat()


class TestAmendmentRecord:
    def test_summary_active(self):
        ts = datetime(2026, 5, 23, 9, 0, 0)
        rec = AmendmentRecord(
            id=1, block_id="blk-feedface", timestamp=ts,
            pre_content="a", post_content="b",
            pre_summary=None, post_summary=None,
            drift_score=0.6, rationale="why",
            acceptor="system", reverted_at=None,
        )
        s = rec.summary
        assert "active" in s
        assert "0.60" in s
        assert "system" in s

    def test_summary_reverted(self):
        ts = datetime(2026, 5, 23, 9, 0, 0)
        rec = AmendmentRecord(
            id=1, block_id="b", timestamp=ts,
            pre_content="a", post_content="b",
            pre_summary=None, post_summary=None,
            drift_score=0.6, rationale=None,
            acceptor="agent", reverted_at=ts,
        )
        assert "reverted" in rec.summary

    def test_to_dict_nullable_fields(self):
        ts = datetime(2026, 5, 23, 9, 0, 0)
        rec = AmendmentRecord(
            id=1, block_id="b", timestamp=ts,
            pre_content="a", post_content="b",
            pre_summary=None, post_summary=None,
            drift_score=0.6, rationale=None,
            acceptor="agent", reverted_at=None,
        )
        d = rec.to_dict()
        assert d["pre_summary"] is None
        assert d["post_summary"] is None
        assert d["rationale"] is None
        assert d["reverted_at"] is None
        assert d["timestamp"] == ts.isoformat()
