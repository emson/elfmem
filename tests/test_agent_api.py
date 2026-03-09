"""Tests for agent-friendly MemorySystem methods: guide(), status(), history().

All tests use the shared fixtures (in-memory SQLite, mock services) and follow
Arrange-Act-Assert. The private _derive_health() function is exercised
indirectly through status() tests rather than tested directly.
"""

from __future__ import annotations

import pytest

from elfmem.api import MemorySystem
from elfmem.exceptions import ElfmemError, FrameError
from elfmem.types import OperationRecord, SystemStatus


# ── Fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture
async def system(test_engine, mock_llm, mock_embedding) -> MemorySystem:
    return MemorySystem(
        engine=test_engine,
        llm_service=mock_llm,
        embedding_service=mock_embedding,
    )


# ── guide() ────────────────────────────────────────────────────────────────────


class TestGuide:
    def test_overview_returns_string(self, system):
        assert isinstance(system.guide(), str)

    def test_overview_mentions_all_operations(self, system):
        result = system.guide()
        for method in ("learn", "recall", "frame", "consolidate", "curate", "status", "history"):
            assert method in result

    def test_known_method_returns_all_guide_fields(self, system):
        result = system.guide("learn")
        for field in ("What:", "Use when:", "Don't use:", "Cost:", "Returns:", "Next:", "Example:"):
            assert field in result

    def test_unknown_method_returns_valid_names(self, system):
        result = system.guide("nonexistent_method")
        assert "nonexistent_method" in result
        assert "learn" in result  # lists valid alternatives

    def test_guide_is_synchronous(self, system):
        import inspect
        assert not inspect.iscoroutinefunction(system.guide)

    def test_guide_callable_before_session(self, system):
        # guide() must work with no session active
        result = system.guide("status")
        assert "status" in result


# ── status() ───────────────────────────────────────────────────────────────────


class TestStatus:
    @pytest.mark.asyncio
    async def test_status_returns_system_status(self, system):
        assert isinstance(await system.status(), SystemStatus)

    @pytest.mark.asyncio
    async def test_status_empty_db_inbox_zero(self, system):
        assert (await system.status()).inbox_count == 0

    @pytest.mark.asyncio
    async def test_status_empty_db_active_zero(self, system):
        assert (await system.status()).active_count == 0

    @pytest.mark.asyncio
    async def test_status_session_inactive_before_begin(self, system):
        assert (await system.status()).session_active is False

    @pytest.mark.asyncio
    async def test_status_session_active_after_begin(self, system):
        await system.begin_session()
        assert (await system.status()).session_active is True
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
        assert (await system.status()).session_hours is None

    @pytest.mark.asyncio
    async def test_status_inbox_count_reflects_learn(self, system):
        await system.begin_session()
        await system.learn("Some knowledge block")
        assert (await system.status()).inbox_count == 1
        await system.end_session()

    @pytest.mark.asyncio
    async def test_status_health_good_when_empty(self, system):
        assert (await system.status()).health == "good"

    @pytest.mark.asyncio
    async def test_status_suggestion_present(self, system):
        result = await system.status()
        assert isinstance(result.suggestion, str) and len(result.suggestion) > 0

    @pytest.mark.asyncio
    async def test_status_last_consolidated_never_on_fresh_db(self, system):
        assert (await system.status()).last_consolidated == "never"


# ── history() ──────────────────────────────────────────────────────────────────


class TestHistory:
    def test_history_empty_before_any_operation(self, system):
        assert system.history() == []

    @pytest.mark.asyncio
    async def test_history_records_learn(self, system):
        await system.begin_session()
        await system.learn("Something worth remembering")
        assert "learn" in [r.operation for r in system.history()]

    @pytest.mark.asyncio
    async def test_history_records_end_session(self, system):
        await system.begin_session()
        await system.end_session()
        assert any(r.operation == "end_session" for r in system.history())

    @pytest.mark.asyncio
    async def test_history_last_n_limits_results(self, system):
        await system.begin_session()
        for i in range(10):
            await system.learn(f"Block number {i} with unique content abc{i}")
        assert len(system.history(last_n=3)) <= 3

    def test_history_returns_list_of_operation_records(self, system):
        assert all(isinstance(r, OperationRecord) for r in system.history())


# ── Error handling ─────────────────────────────────────────────────────────────


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
        assert exc_info.value.recovery and "self" in exc_info.value.recovery
        await system.end_session()

    @pytest.mark.asyncio
    async def test_recall_raises_frame_error_for_unknown_frame(self, system):
        await system.begin_session()
        with pytest.raises(FrameError):
            await system.recall(query="test", frame="not_a_frame")
        await system.end_session()

    @pytest.mark.asyncio
    async def test_frame_error_is_elfmem_error(self, system):
        await system.begin_session()
        with pytest.raises(ElfmemError):
            await system.frame("bad_frame")
        await system.end_session()


# ── Session idempotency ────────────────────────────────────────────────────────


class TestSessionIdempotency:
    @pytest.mark.asyncio
    async def test_begin_session_twice_returns_same_id(self, system):
        id1 = await system.begin_session()
        id2 = await system.begin_session()
        assert id1 == id2
        await system.end_session()

    @pytest.mark.asyncio
    async def test_end_session_without_session_returns_zero(self, system):
        assert await system.end_session() == 0.0
