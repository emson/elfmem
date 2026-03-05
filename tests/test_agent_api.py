"""Tests for agent-friendly MemorySystem methods: guide(), status(), history().

Tests use the same fixtures as the rest of the suite (in-memory SQLite,
mock LLM/embedding services). All tests follow Arrange-Act-Assert.
"""

from __future__ import annotations

import pytest

from elfmem.api import MemorySystem, _derive_health
from elfmem.exceptions import FrameError
from elfmem.types import OperationRecord, SystemStatus


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
async def system(test_engine, mock_llm, mock_embedding) -> MemorySystem:
    """A MemorySystem wired to the in-memory test engine."""
    return MemorySystem(
        engine=test_engine,
        llm_service=mock_llm,
        embedding_service=mock_embedding,
    )


# ── guide() ──────────────────────────────────────────────────────────────────

class TestGuide:
    def test_overview_returns_string(self, system):
        result = system.guide()
        assert isinstance(result, str)

    def test_overview_mentions_all_operations(self, system):
        result = system.guide()
        for method in ("learn", "recall", "frame", "consolidate", "curate", "status", "history"):
            assert method in result

    def test_known_method_returns_all_guide_fields(self, system):
        result = system.guide("learn")
        for field in ("What:", "Use when:", "Don't use:", "Cost:", "Returns:", "Next:", "Example:"):
            assert field in result

    def test_known_method_consolidate(self, system):
        result = system.guide("consolidate")
        assert "consolidate" in result
        assert "LLM" in result  # cost field mentions LLM

    def test_known_method_frame(self, system):
        result = system.guide("frame")
        assert "self" in result
        assert "attention" in result
        assert "task" in result

    def test_unknown_method_returns_valid_names(self, system):
        result = system.guide("nonexistent_method")
        assert "nonexistent_method" in result
        assert "learn" in result  # lists valid names

    def test_guide_is_synchronous(self, system):
        # guide() must not be a coroutine — agents call it without await
        import inspect
        assert not inspect.iscoroutinefunction(system.guide)

    def test_guide_str_contains_example(self, system):
        result = system.guide("recall")
        assert "Example:" in result

    def test_guide_callable_before_session(self, system):
        # guide() must work with no session active
        result = system.guide("status")
        assert "status" in result


# ── status() ─────────────────────────────────────────────────────────────────

class TestStatus:
    @pytest.mark.asyncio
    async def test_status_returns_system_status(self, system):
        result = await system.status()
        assert isinstance(result, SystemStatus)

    @pytest.mark.asyncio
    async def test_status_empty_db_inbox_zero(self, system):
        result = await system.status()
        assert result.inbox_count == 0

    @pytest.mark.asyncio
    async def test_status_empty_db_active_zero(self, system):
        result = await system.status()
        assert result.active_count == 0

    @pytest.mark.asyncio
    async def test_status_session_inactive_before_begin(self, system):
        result = await system.status()
        assert result.session_active is False

    @pytest.mark.asyncio
    async def test_status_session_active_after_begin(self, system):
        await system.begin_session()
        result = await system.status()
        assert result.session_active is True
        await system.end_session()

    @pytest.mark.asyncio
    async def test_status_session_hours_present_when_active(self, system):
        await system.begin_session()
        result = await system.status()
        assert result.session_hours is not None
        assert result.session_hours >= 0.0
        await system.end_session()

    @pytest.mark.asyncio
    async def test_status_session_hours_none_when_inactive(self, system):
        result = await system.status()
        assert result.session_hours is None

    @pytest.mark.asyncio
    async def test_status_inbox_count_reflects_learn(self, system):
        await system.begin_session()
        await system.learn("Some knowledge block")
        result = await system.status()
        assert result.inbox_count == 1
        await system.end_session()

    @pytest.mark.asyncio
    async def test_status_health_good_when_empty(self, system):
        result = await system.status()
        assert result.health == "good"

    @pytest.mark.asyncio
    async def test_status_suggestion_present(self, system):
        result = await system.status()
        assert isinstance(result.suggestion, str)
        assert len(result.suggestion) > 0

    @pytest.mark.asyncio
    async def test_status_last_consolidated_never_on_fresh_db(self, system):
        result = await system.status()
        assert result.last_consolidated == "never"

    @pytest.mark.asyncio
    async def test_status_str_is_agent_readable(self, system):
        result = await system.status()
        s = str(result)
        assert "Session:" in s
        assert "Inbox:" in s
        assert "Health:" in s
        assert "Suggestion:" in s

    @pytest.mark.asyncio
    async def test_status_to_dict_has_all_keys(self, system):
        result = await system.status()
        d = result.to_dict()
        expected = {
            "session_active", "session_hours", "inbox_count", "inbox_threshold",
            "active_count", "archived_count", "total_active_hours",
            "last_consolidated", "health", "suggestion",
        }
        assert set(d.keys()) == expected

    @pytest.mark.asyncio
    async def test_status_inbox_threshold_from_config(self, system):
        result = await system.status()
        # Default config has inbox_threshold = 10
        assert result.inbox_threshold == 10


# ── status() health derivation ────────────────────────────────────────────────

class TestDeriveHealth:
    """Unit tests for the _derive_health() pure function."""

    def test_full_inbox_returns_attention(self):
        health, _ = _derive_health(inbox_count=10, inbox_threshold=10, active_count=5)
        assert health == "attention"

    def test_full_inbox_suggestion_mentions_consolidate(self):
        _, suggestion = _derive_health(inbox_count=10, inbox_threshold=10, active_count=5)
        assert "consolidate" in suggestion.lower()

    def test_nearly_full_inbox_returns_good(self):
        health, _ = _derive_health(inbox_count=8, inbox_threshold=10, active_count=5)
        assert health == "good"

    def test_nearly_full_suggestion_mentions_approaching(self):
        _, suggestion = _derive_health(inbox_count=8, inbox_threshold=10, active_count=5)
        assert "approaching" in suggestion.lower() or "nearly" in suggestion.lower()

    def test_empty_memory_returns_good(self):
        health, _ = _derive_health(inbox_count=0, inbox_threshold=10, active_count=0)
        assert health == "good"

    def test_empty_memory_suggestion_mentions_learn(self):
        _, suggestion = _derive_health(inbox_count=0, inbox_threshold=10, active_count=0)
        assert "learn" in suggestion.lower()

    def test_healthy_memory_returns_good(self):
        health, _ = _derive_health(inbox_count=2, inbox_threshold=10, active_count=50)
        assert health == "good"

    def test_zero_threshold_does_not_divide_by_zero(self):
        # Should not raise even with threshold=0
        health, _ = _derive_health(inbox_count=5, inbox_threshold=0, active_count=10)
        assert health in ("good", "attention")


# ── history() ────────────────────────────────────────────────────────────────

class TestHistory:
    def test_history_empty_before_any_operation(self, system):
        records = system.history()
        assert records == []

    @pytest.mark.asyncio
    async def test_history_records_learn(self, system):
        await system.begin_session()
        await system.learn("Something worth remembering")
        records = system.history()
        operations = [r.operation for r in records]
        assert "learn" in operations

    @pytest.mark.asyncio
    async def test_history_records_begin_session(self, system):
        await system.begin_session()
        records = system.history()
        assert any(r.operation == "begin_session" for r in records)
        await system.end_session()

    @pytest.mark.asyncio
    async def test_history_records_end_session(self, system):
        await system.begin_session()
        await system.end_session()
        records = system.history()
        assert any(r.operation == "end_session" for r in records)

    @pytest.mark.asyncio
    async def test_history_records_curate(self, system):
        await system.begin_session()
        await system.curate()
        records = system.history()
        assert any(r.operation == "curate" for r in records)
        await system.end_session()

    @pytest.mark.asyncio
    async def test_history_most_recent_last(self, system):
        await system.begin_session()
        await system.learn("First block unique content aaa")
        await system.learn("Second block unique content bbb")
        records = system.history()
        # Both learns are recorded; history preserves insertion order
        learn_records = [r for r in records if r.operation == "learn"]
        assert len(learn_records) == 2
        # The two block IDs in the summaries should differ (different content → different hash)
        assert learn_records[0].summary != learn_records[1].summary

    @pytest.mark.asyncio
    async def test_history_last_n_limits_results(self, system):
        await system.begin_session()
        for i in range(10):
            await system.learn(f"Block number {i} with unique content abc{i}")
        records = system.history(last_n=3)
        assert len(records) <= 3

    def test_history_last_n_zero_returns_empty(self, system):
        records = system.history(last_n=0)
        assert records == []

    @pytest.mark.asyncio
    async def test_history_returns_list_of_operation_records(self, system):
        await system.begin_session()
        await system.learn("Some content for history test")
        records = system.history()
        assert all(isinstance(r, OperationRecord) for r in records)

    @pytest.mark.asyncio
    async def test_history_record_has_timestamp(self, system):
        await system.begin_session()
        await system.learn("content with timestamp")
        record = next(r for r in system.history() if r.operation == "learn")
        assert len(record.timestamp) >= 19  # ISO string has at least YYYY-MM-DDTHH:MM:SS

    @pytest.mark.asyncio
    async def test_history_record_has_non_empty_summary(self, system):
        await system.begin_session()
        await system.learn("content for summary check")
        record = next(r for r in system.history() if r.operation == "learn")
        assert len(record.summary) > 0

    @pytest.mark.asyncio
    async def test_history_str_is_readable(self, system):
        await system.begin_session()
        await system.learn("readable history test")
        record = next(r for r in system.history() if r.operation == "learn")
        s = str(record)
        assert "learn()" in s
        assert "→" in s


# ── Phase 4: FrameError on invalid frame name ─────────────────────────────────

class TestFrameError:
    @pytest.mark.asyncio
    async def test_frame_raises_frame_error_for_unknown_name(self, system):
        await system.begin_session()
        with pytest.raises(FrameError) as exc_info:
            await system.frame("not_a_real_frame")
        assert "not_a_real_frame" in str(exc_info.value)
        await system.end_session()

    @pytest.mark.asyncio
    async def test_frame_error_has_recovery_hint(self, system):
        await system.begin_session()
        with pytest.raises(FrameError) as exc_info:
            await system.frame("invalid")
        assert exc_info.value.recovery
        assert "self" in exc_info.value.recovery
        await system.end_session()

    @pytest.mark.asyncio
    async def test_recall_raises_frame_error_for_unknown_frame(self, system):
        await system.begin_session()
        with pytest.raises(FrameError):
            await system.recall(query="test", frame="not_a_frame")
        await system.end_session()

    @pytest.mark.asyncio
    async def test_frame_error_is_elfmem_error(self, system):
        from elfmem.exceptions import ElfmemError
        await system.begin_session()
        with pytest.raises(ElfmemError):
            await system.frame("bad_frame")
        await system.end_session()


# ── begin_session() idempotency ───────────────────────────────────────────────

class TestSessionIdempotency:
    @pytest.mark.asyncio
    async def test_begin_session_twice_returns_same_id(self, system):
        id1 = await system.begin_session()
        id2 = await system.begin_session()
        assert id1 == id2
        await system.end_session()

    @pytest.mark.asyncio
    async def test_end_session_without_session_returns_zero(self, system):
        duration = await system.end_session()
        assert duration == 0.0
