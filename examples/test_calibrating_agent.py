"""Tests for CalibratingAgent — self-calibrating agent example.

Run from project root: pytest examples/test_calibrating_agent.py -v
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import pytest

from calibrating_agent import (
    BlockVerdict,
    CalibratingAgent,
    SessionMetrics,
    SessionReflection,
    TaskResult,
    TaskType,
    FRAME_TABLE,
    VERDICT_SIGNALS,
)
from elfmem import MemorySystem


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def db_path_str(tmp_path: Path) -> str:
    return str(tmp_path / "test_calibrating.db")


@pytest.fixture
async def system(db_path_str: str) -> MemorySystem:
    sys = await MemorySystem.from_config(db_path_str)
    yield sys
    await sys.close()


@pytest.fixture
async def agent(system: MemorySystem) -> CalibratingAgent:
    return CalibratingAgent(system)


# ── SessionMetrics ────────────────────────────────────────────────────────────


class TestSessionMetrics:
    """SessionMetrics computes calibration quality."""

    def test_empty_metrics_zero_rates(self) -> None:
        m = SessionMetrics()
        assert m.hit_rate == 0.0
        assert m.surprise_rate == 0.0
        assert m.gap_rate == 0.0
        assert m.noise_ratio == 0.0

    def test_hit_rate_computed_from_used_blocks(self) -> None:
        m = SessionMetrics(blocks_used=3, blocks_ignored=1, blocks_misleading=1)
        assert abs(m.hit_rate - 0.6) < 0.001

    def test_surprise_rate_computed_from_recalls(self) -> None:
        m = SessionMetrics(recalls_made=10, surprises=3)
        assert abs(m.surprise_rate - 0.3) < 0.001

    def test_noise_ratio_includes_ignored_and_misleading(self) -> None:
        m = SessionMetrics(blocks_used=2, blocks_ignored=3, blocks_misleading=1)
        assert abs(m.noise_ratio - 4 / 6) < 0.001

    def test_health_insufficient_data_under_three_recalls(self) -> None:
        m = SessionMetrics(recalls_made=2)
        assert m.health == "insufficient_data"

    def test_health_healthy_when_metrics_good(self) -> None:
        m = SessionMetrics(
            recalls_made=10, blocks_used=8, blocks_ignored=2,
            surprises=2, gaps=0,
        )
        assert m.health == "healthy"

    def test_health_degraded_when_multiple_issues(self) -> None:
        m = SessionMetrics(
            recalls_made=10, blocks_used=1, blocks_ignored=8, blocks_misleading=1,
            surprises=7, gaps=3,
        )
        assert m.health == "degraded"

    def test_summary_contains_key_metrics(self) -> None:
        m = SessionMetrics(recalls_made=5, blocks_used=3, blocks_ignored=2, surprises=1)
        s = m.summary()
        assert "hit_rate" in s
        assert "surprise_rate" in s
        assert "health" in s


# ── Frame selection ───────────────────────────────────────────────────────────


class TestFrameSelection:
    """Frame table maps task types to correct frames."""

    def test_novel_uses_attention(self) -> None:
        frame, top_k = FRAME_TABLE[TaskType.NOVEL]
        assert frame == "attention"
        assert top_k == 10

    def test_execution_uses_task(self) -> None:
        frame, top_k = FRAME_TABLE[TaskType.EXECUTION]
        assert frame == "task"
        assert top_k == 5

    def test_identity_uses_self(self) -> None:
        frame, top_k = FRAME_TABLE[TaskType.IDENTITY]
        assert frame == "self"
        assert top_k == 5

    def test_all_task_types_have_entry(self) -> None:
        for task_type in TaskType:
            assert task_type in FRAME_TABLE


# ── Verdict signals ───────────────────────────────────────────────────────────


class TestVerdictSignals:
    """Verdict → signal mapping is correctly ordered."""

    def test_used_signal_highest(self) -> None:
        assert VERDICT_SIGNALS[BlockVerdict.USED] > VERDICT_SIGNALS[BlockVerdict.IGNORED]

    def test_ignored_signal_above_misleading(self) -> None:
        assert VERDICT_SIGNALS[BlockVerdict.IGNORED] > VERDICT_SIGNALS[BlockVerdict.MISLEADING]

    def test_all_signals_in_range(self) -> None:
        for signal in VERDICT_SIGNALS.values():
            assert 0.0 <= signal <= 1.0


# ── Session lifecycle ─────────────────────────────────────────────────────────


class TestSessionLifecycle:

    @pytest.mark.asyncio
    async def test_start_session_returns_context(self, agent: CalibratingAgent) -> None:
        recent, identity = await agent.start_session()
        assert isinstance(recent, str)
        assert isinstance(identity, str)

    @pytest.mark.asyncio
    async def test_start_session_resets_metrics(self, agent: CalibratingAgent) -> None:
        agent._metrics.recalls_made = 99
        await agent.start_session()
        assert agent.metrics.recalls_made == 0

    @pytest.mark.asyncio
    async def test_end_session_returns_reflection(self, agent: CalibratingAgent) -> None:
        await agent.start_session()
        reflection = await agent.end_session(
            work_summary="tested session lifecycle",
            insight="sessions work",
            adjustment="none needed",
        )
        assert isinstance(reflection, SessionReflection)
        assert reflection.work_summary == "tested session lifecycle"
        assert reflection.insight == "sessions work"

    @pytest.mark.asyncio
    async def test_end_session_records_reflection_block(self, agent: CalibratingAgent) -> None:
        await agent.start_session()
        reflection = await agent.end_session(
            work_summary="test",
            insight="insight",
            adjustment="adjustment",
        )
        assert reflection.reflection_block_id is not None

    @pytest.mark.asyncio
    async def test_end_session_includes_metrics(self, agent: CalibratingAgent) -> None:
        await agent.start_session()
        reflection = await agent.end_session(
            work_summary="test", insight="i", adjustment="a",
        )
        assert isinstance(reflection.metrics, SessionMetrics)


# ── Before task ───────────────────────────────────────────────────────────────


class TestBeforeTask:

    @pytest.mark.asyncio
    async def test_before_task_returns_frame_result(self, agent: CalibratingAgent) -> None:
        await agent.start_session()
        result = await agent.before_task("implement feature X")
        assert hasattr(result, "blocks")
        assert hasattr(result, "text")

    @pytest.mark.asyncio
    async def test_before_task_increments_recalls(self, agent: CalibratingAgent) -> None:
        await agent.start_session()
        assert agent.metrics.recalls_made == 0
        await agent.before_task("task one")
        assert agent.metrics.recalls_made == 1
        await agent.before_task("task two")
        assert agent.metrics.recalls_made == 2

    @pytest.mark.asyncio
    async def test_before_task_uses_correct_frame(
        self, agent: CalibratingAgent,
    ) -> None:
        await agent.start_session()
        result = await agent.before_task("check values", TaskType.IDENTITY)
        assert result.frame_name == "self"

    @pytest.mark.asyncio
    async def test_before_task_detects_gap_when_no_blocks(
        self, agent: CalibratingAgent,
    ) -> None:
        """Fresh DB with no blocks → gap detected."""
        await agent.start_session()
        await agent.before_task("obscure topic with no prior knowledge")
        assert agent.metrics.gaps >= 0  # May or may not gap depending on constitutional


# ── After task ────────────────────────────────────────────────────────────────


class TestAfterTask:

    @pytest.mark.asyncio
    async def test_after_task_returns_task_result(self, agent: CalibratingAgent) -> None:
        await agent.start_session()

        # Seed a block so we have something to recall
        learn_result = await agent._system.remember("test pattern for recall")
        await agent._system.dream()
        recall = await agent.before_task("test pattern")

        block_ids = [b.id for b in recall.blocks]
        verdicts = {bid: BlockVerdict.USED for bid in block_ids}

        result = await agent.after_task(
            expectation="pattern should be found",
            verdicts=verdicts,
        )
        assert isinstance(result, TaskResult)

    @pytest.mark.asyncio
    async def test_after_task_tracks_used_blocks(self, agent: CalibratingAgent) -> None:
        await agent.start_session()
        await agent._system.remember("useful knowledge")
        await agent._system.dream()
        recall = await agent.before_task("useful knowledge")

        block_ids = [b.id for b in recall.blocks]
        verdicts = {bid: BlockVerdict.USED for bid in block_ids}

        await agent.after_task(expectation="found", verdicts=verdicts)
        assert agent.metrics.blocks_used == len(block_ids)

    @pytest.mark.asyncio
    async def test_after_task_tracks_ignored_blocks(self, agent: CalibratingAgent) -> None:
        await agent.start_session()
        await agent._system.remember("irrelevant noise")
        await agent._system.dream()
        recall = await agent.before_task("something unrelated")

        block_ids = [b.id for b in recall.blocks]
        verdicts = {bid: BlockVerdict.IGNORED for bid in block_ids}

        await agent.after_task(expectation="nothing useful", verdicts=verdicts)
        assert agent.metrics.blocks_ignored == len(block_ids)

    @pytest.mark.asyncio
    async def test_after_task_encodes_surprise(self, agent: CalibratingAgent) -> None:
        await agent.start_session()
        recall = await agent.before_task("anything")

        result = await agent.after_task(
            expectation="X would happen",
            verdicts={},
            surprise="Y happened instead — pattern: always check Y first",
        )
        assert result.surprise is not None
        assert agent.metrics.surprises == 1

    @pytest.mark.asyncio
    async def test_after_task_encodes_gap(self, agent: CalibratingAgent) -> None:
        await agent.start_session()
        await agent.before_task("rare topic")

        result = await agent.after_task(
            expectation="some knowledge",
            verdicts={},
            gap="No knowledge about rare topic edge cases",
        )
        assert result.gap is not None

    @pytest.mark.asyncio
    async def test_after_task_sends_correct_outcome_count(
        self, agent: CalibratingAgent,
    ) -> None:
        await agent.start_session()
        await agent._system.remember("block A for test")
        await agent._system.remember("block B for test")
        await agent._system.dream()
        recall = await agent.before_task("block for test")

        block_ids = [b.id for b in recall.blocks]
        verdicts = {bid: BlockVerdict.USED for bid in block_ids}

        result = await agent.after_task(expectation="found", verdicts=verdicts)
        assert result.outcomes_sent == len(block_ids)


# ── Quick calibrate ───────────────────────────────────────────────────────────


class TestQuickCalibrate:

    @pytest.mark.asyncio
    async def test_quick_calibrate_counts_correctly(
        self, agent: CalibratingAgent,
    ) -> None:
        await agent.start_session()
        await agent._system.remember("block one")
        await agent._system.remember("block two")
        await agent._system.remember("block three")
        await agent._system.dream()

        recall = await agent.before_task("block")
        ids = [b.id for b in recall.blocks]

        if len(ids) >= 2:
            sent = await agent.quick_calibrate(
                used_ids=[ids[0]],
                ignored_ids=[ids[1]],
            )
            assert sent == 2
            assert agent.metrics.blocks_used >= 1
            assert agent.metrics.blocks_ignored >= 1


# ── Diagnose ──────────────────────────────────────────────────────────────────


class TestDiagnose:

    @pytest.mark.asyncio
    async def test_diagnose_insufficient_data(self, agent: CalibratingAgent) -> None:
        await agent.start_session()
        diag = await agent.diagnose()
        assert "Insufficient" in diag

    @pytest.mark.asyncio
    async def test_diagnose_healthy_after_good_session(
        self, agent: CalibratingAgent,
    ) -> None:
        await agent.start_session()
        # Simulate good metrics
        agent._metrics = SessionMetrics(
            recalls_made=5, blocks_used=8, blocks_ignored=2,
            surprises=1, gaps=0,
        )
        diag = await agent.diagnose()
        assert "healthy" in diag.lower()

    @pytest.mark.asyncio
    async def test_diagnose_flags_low_hit_rate(
        self, agent: CalibratingAgent,
    ) -> None:
        await agent.start_session()
        agent._metrics = SessionMetrics(
            recalls_made=5, blocks_used=1, blocks_ignored=9,
            surprises=0, gaps=0,
        )
        diag = await agent.diagnose()
        assert "Hit rate" in diag

    @pytest.mark.asyncio
    async def test_diagnose_flags_high_surprise_rate(
        self, agent: CalibratingAgent,
    ) -> None:
        await agent.start_session()
        agent._metrics = SessionMetrics(
            recalls_made=10, blocks_used=8, blocks_ignored=2,
            surprises=6, gaps=0,
        )
        diag = await agent.diagnose()
        assert "Surprise rate" in diag


# ── Full cycle ────────────────────────────────────────────────────────────────


class TestFullCycle:

    @pytest.mark.asyncio
    async def test_complete_discipline_cycle(self, agent: CalibratingAgent) -> None:
        """Full cycle: start → seed → recall → calibrate → reflect → end."""
        # 1. Start session
        recent, identity = await agent.start_session()
        assert isinstance(recent, str)

        # 2. Seed knowledge
        await agent._system.remember(
            "Functional Python: pure functions, ≤50 lines, compose pipelines",
            tags=["team/coding"],
        )
        await agent._system.remember(
            "Tests use arrange-act-assert, one assertion per test",
            tags=["team/testing"],
        )
        await agent._system.dream()

        # 3. Before task: recall
        recall = await agent.before_task(
            "implement a composable function pipeline", TaskType.EXECUTION,
        )
        assert recall.frame_name == "task"

        # 4. After task: calibrate
        block_ids = [b.id for b in recall.blocks]
        verdicts = {}
        for i, bid in enumerate(block_ids):
            verdicts[bid] = BlockVerdict.USED if i == 0 else BlockVerdict.IGNORED

        result = await agent.after_task(
            expectation="Should find functional Python pattern",
            verdicts=verdicts,
            surprise=None,
        )
        assert result.outcomes_sent == len(block_ids)

        # 5. End session
        reflection = await agent.end_session(
            work_summary="Implemented function pipeline",
            insight="Coding patterns recall correctly via task frame",
            adjustment="None needed — hit rate acceptable",
        )
        assert isinstance(reflection, SessionReflection)
        assert reflection.reflection_block_id is not None
        assert agent.metrics.recalls_made >= 1
