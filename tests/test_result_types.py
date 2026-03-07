"""Tests for agent-friendly __str__, summary, and to_dict() on all result types.

These tests exercise the public-facing string representations that agents
consume directly. No database or async machinery is needed.
"""

from __future__ import annotations

import pytest

from elfmem.exceptions import ConfigError, ElfmemError, FrameError, SessionError, StorageError
from elfmem.types import (
    ConsolidateResult,
    CurateResult,
    FrameResult,
    LearnResult,
    OperationRecord,
    ScoredBlock,
    SystemStatus,
)


# ── LearnResult ───────────────────────────────────────────────────────────────

class TestLearnResult:
    def test_str_created(self):
        r = LearnResult(block_id="a1b2c3d4e5f6g7h8", status="created")
        assert str(r) == "Stored block a1b2c3d4. Status: created."

    def test_str_duplicate_rejected(self):
        r = LearnResult(block_id="a1b2c3d4e5f6g7h8", status="duplicate_rejected")
        assert "Duplicate" in str(r)
        assert "a1b2c3d4" in str(r)

    def test_str_near_duplicate_superseded(self):
        r = LearnResult(block_id="a1b2c3d4e5f6g7h8", status="near_duplicate_superseded")
        assert "superseded" in str(r)
        assert "a1b2c3d4" in str(r)

    def test_str_unknown_status_includes_status(self):
        # Fallback for future status values — must not raise
        r = LearnResult(block_id="a1b2c3d4e5f6g7h8", status="future_status")
        assert "future_status" in str(r)

    def test_summary_equals_str(self):
        r = LearnResult(block_id="a1b2c3d4e5f6g7h8", status="created")
        assert r.summary == str(r)

    def test_str_truncates_id_to_8_chars(self):
        r = LearnResult(block_id="abcdef1234567890", status="created")
        assert "abcdef12" in str(r)
        assert "34567890" not in str(r)

    def test_to_dict_keys(self):
        r = LearnResult(block_id="a1b2c3d4", status="created")
        d = r.to_dict()
        assert set(d.keys()) == {"block_id", "status"}

    def test_to_dict_values(self):
        r = LearnResult(block_id="a1b2c3d4", status="created")
        d = r.to_dict()
        assert d["block_id"] == "a1b2c3d4"
        assert d["status"] == "created"


# ── ConsolidateResult ─────────────────────────────────────────────────────────

class TestConsolidateResult:
    def test_str_zero_processed(self):
        r = ConsolidateResult(processed=0, promoted=0, deduplicated=0, edges_created=0)
        assert str(r) == "Nothing to consolidate. Inbox was empty."

    def test_str_nonzero_includes_processed(self):
        r = ConsolidateResult(processed=10, promoted=9, deduplicated=1, edges_created=14)
        s = str(r)
        assert "10" in s
        assert "9 promoted" in s
        assert "1 deduped" in s
        assert "14 edges" in s

    def test_str_zero_deduped_omits_deduped(self):
        r = ConsolidateResult(processed=5, promoted=5, deduplicated=0, edges_created=8)
        assert "deduped" not in str(r)

    def test_summary_equals_str(self):
        r = ConsolidateResult(processed=5, promoted=5, deduplicated=0, edges_created=8)
        assert r.summary == str(r)

    def test_to_dict_keys(self):
        r = ConsolidateResult(processed=5, promoted=4, deduplicated=1, edges_created=6)
        assert set(r.to_dict().keys()) == {"processed", "promoted", "deduplicated", "edges_created"}

    def test_to_dict_values_match_fields(self):
        r = ConsolidateResult(processed=5, promoted=4, deduplicated=1, edges_created=6)
        d = r.to_dict()
        assert d["processed"] == 5
        assert d["promoted"] == 4
        assert d["deduplicated"] == 1
        assert d["edges_created"] == 6


# ── FrameResult ───────────────────────────────────────────────────────────────

def _make_block(score: float = 0.8) -> ScoredBlock:
    return ScoredBlock(
        id="abc", content="test content", tags=["tag1"],
        similarity=score, confidence=0.8, recency=0.9,
        centrality=0.7, reinforcement=0.6, score=score,
    )


class TestFrameResult:
    def test_str_with_blocks_uncached(self):
        blocks = [_make_block(), _make_block()]
        r = FrameResult(text="## Context\nsome text", blocks=blocks, frame_name="attention")
        assert "attention frame" in str(r)
        assert "2 blocks" in str(r)
        assert "cached" not in str(r)

    def test_str_with_blocks_cached(self):
        blocks = [_make_block()]
        r = FrameResult(text="text", blocks=blocks, frame_name="self", cached=True)
        assert "cached" in str(r)

    def test_str_no_blocks(self):
        r = FrameResult(text="", blocks=[], frame_name="attention")
        assert "no blocks found" in str(r)

    def test_str_singular_block(self):
        r = FrameResult(text="text", blocks=[_make_block()], frame_name="task")
        assert "1 block " in str(r)  # singular, not "1 blocks"

    def test_summary_equals_str(self):
        r = FrameResult(text="text", blocks=[_make_block()], frame_name="self")
        assert r.summary == str(r)

    def test_to_dict_does_not_include_blocks_detail(self):
        r = FrameResult(text="rendered text", blocks=[_make_block()], frame_name="attention")
        d = r.to_dict()
        assert "block_count" in d
        assert d["block_count"] == 1
        assert d["frame_name"] == "attention"
        assert d["text"] == "rendered text"
        assert d["cached"] is False


# ── CurateResult ─────────────────────────────────────────────────────────────

class TestCurateResult:
    def test_str_all_zero(self):
        r = CurateResult(archived=0, edges_pruned=0, reinforced=0)
        assert str(r) == "Curated: nothing required."

    def test_str_archived_only(self):
        r = CurateResult(archived=3, edges_pruned=0, reinforced=0)
        assert "3 archived" in str(r)
        assert "edges" not in str(r)

    def test_str_all_nonzero(self):
        r = CurateResult(archived=2, edges_pruned=5, reinforced=10)
        s = str(r)
        assert "2 archived" in s
        assert "5 edges pruned" in s
        assert "10 reinforced" in s

    def test_summary_equals_str(self):
        r = CurateResult(archived=1, edges_pruned=2, reinforced=3)
        assert r.summary == str(r)

    def test_to_dict_keys(self):
        r = CurateResult(archived=1, edges_pruned=2, reinforced=3)
        assert set(r.to_dict().keys()) == {"archived", "edges_pruned", "reinforced", "constitutional_reinforced"}


# ── ScoredBlock ───────────────────────────────────────────────────────────────

class TestScoredBlock:
    def test_str_includes_score(self):
        b = _make_block(score=0.87)
        assert "0.87" in str(b)

    def test_str_truncates_long_content(self):
        long_content = "x" * 200
        b = ScoredBlock(
            id="a", content=long_content, tags=[],
            similarity=0.5, confidence=0.5, recency=0.5,
            centrality=0.5, reinforcement=0.5, score=0.5,
        )
        assert len(str(b)) < 120  # well under full content length

    def test_str_includes_tags(self):
        b = ScoredBlock(
            id="a", content="content", tags=["preference", "ui"],
            similarity=0.5, confidence=0.5, recency=0.5,
            centrality=0.5, reinforcement=0.5, score=0.5,
        )
        assert "preference" in str(b)

    def test_str_limits_tags_to_three(self):
        b = ScoredBlock(
            id="a", content="content", tags=["t1", "t2", "t3", "t4", "t5"],
            similarity=0.5, confidence=0.5, recency=0.5,
            centrality=0.5, reinforcement=0.5, score=0.5,
        )
        s = str(b)
        assert "t4" not in s
        assert "t5" not in s

    def test_to_dict_has_all_scoring_fields(self):
        b = _make_block()
        d = b.to_dict()
        expected = {"id", "content", "tags", "score", "similarity", "confidence",
                    "recency", "centrality", "reinforcement", "was_expanded", "status"}
        assert set(d.keys()) == expected


# ── SystemStatus ──────────────────────────────────────────────────────────────

class TestSystemStatus:
    def _make_status(self, **overrides) -> SystemStatus:
        defaults = dict(
            session_active=True,
            session_hours=1.5,
            inbox_count=3,
            inbox_threshold=10,
            active_count=42,
            archived_count=5,
            total_active_hours=12.3,
            last_consolidated="2025-01-01T12:00:00+00:00",
            health="good",
            suggestion="Memory healthy. No action required.",
        )
        return SystemStatus(**{**defaults, **overrides})

    def test_str_contains_session_active(self):
        s = self._make_status(session_active=True, session_hours=1.5)
        assert "active" in str(s)
        assert "1.5h" in str(s)

    def test_str_contains_inbox_ratio(self):
        s = self._make_status(inbox_count=4, inbox_threshold=10)
        assert "4/10" in str(s)

    def test_str_contains_active_count(self):
        s = self._make_status(active_count=42)
        assert "42" in str(s)

    def test_str_contains_suggestion(self):
        s = self._make_status(suggestion="Inbox full. Call consolidate().")
        assert "Inbox full" in str(s)

    def test_str_session_inactive(self):
        s = self._make_status(session_active=False, session_hours=None)
        assert "inactive" in str(s)

    def test_health_attention_in_str(self):
        s = self._make_status(health="attention")
        assert "attention" in str(s)

    def test_to_dict_has_all_fields(self):
        s = self._make_status()
        d = s.to_dict()
        expected = {
            "session_active", "session_hours", "inbox_count", "inbox_threshold",
            "active_count", "archived_count", "total_active_hours",
            "last_consolidated", "health", "suggestion",
            "session_tokens", "lifetime_tokens",
        }
        assert set(d.keys()) == expected


# ── OperationRecord ───────────────────────────────────────────────────────────

class TestOperationRecord:
    def test_str_includes_operation_name(self):
        r = OperationRecord(
            operation="learn",
            summary="Stored block a1b2. Status: created.",
            timestamp="2025-01-01T14:32:01.123456+00:00",
        )
        assert "learn()" in str(r)

    def test_str_includes_summary(self):
        r = OperationRecord(
            operation="consolidate",
            summary="Consolidated 5: 4 promoted, 0 deduped, 8 edges.",
            timestamp="2025-01-01T14:32:01.123456+00:00",
        )
        assert "Consolidated 5" in str(r)

    def test_str_shows_time_component(self):
        r = OperationRecord(
            operation="learn",
            summary="Stored block a1b2.",
            timestamp="2025-01-01T14:32:01.123456+00:00",
        )
        assert "14:32:01" in str(r)

    def test_to_dict_keys(self):
        r = OperationRecord(operation="learn", summary="ok", timestamp="2025-01-01T00:00:00")
        assert set(r.to_dict().keys()) == {"operation", "summary", "timestamp"}


# ── Exceptions ────────────────────────────────────────────────────────────────

class TestExceptions:
    def test_elfmem_error_has_recovery(self):
        e = ElfmemError("Something failed.", recovery="Try this instead.")
        assert e.recovery == "Try this instead."

    def test_elfmem_error_str_includes_recovery(self):
        e = ElfmemError("Failed.", recovery="Fix it.")
        assert "Fix it." in str(e)
        assert "Recovery:" in str(e)

    def test_session_error_is_elfmem_error(self):
        e = SessionError("No session.", recovery="Call begin_session().")
        assert isinstance(e, ElfmemError)

    def test_frame_error_is_elfmem_error(self):
        e = FrameError("Unknown frame.", recovery="Valid frames: 'self', 'attention', 'task'.")
        assert isinstance(e, ElfmemError)

    def test_config_error_is_elfmem_error(self):
        e = ConfigError("Bad config.", recovery="Check your YAML file.")
        assert isinstance(e, ElfmemError)

    def test_storage_error_is_elfmem_error(self):
        e = StorageError("DB failed.", recovery="Check disk space.")
        assert isinstance(e, ElfmemError)

    def test_exception_message_preserved(self):
        e = ElfmemError("The original message.", recovery="Some recovery.")
        assert "The original message." in str(e)

    def test_recovery_keyword_only(self):
        # recovery must be passed as keyword argument
        with pytest.raises(TypeError):
            ElfmemError("msg", "recovery_as_positional")  # type: ignore[misc]
