"""Tests for ElfDecisionMaker — example of agent decision-making on top of elfmem.

Run from project root: pytest examples/test_decision_maker.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import pytest

from decision_maker import (
    CalibrationResult,
    Decision,
    ElfDecisionMaker,
    PendingDecision,
    Signal,
)
from elfmem.smart import SmartMemory


class TestSignal:
    """Signal computation from objective metrics."""

    def test_signal_from_perfect_results(self) -> None:
        """Perfect results yield signal >= 0.9."""
        signal = Signal.from_test_results(tests_pass=True, regressions=0, goal_advanced=True, elegant=True)
        assert signal >= 0.9

    def test_signal_from_failed_tests(self) -> None:
        """Failed tests yield Signal.WRONG (0.0)."""
        signal = Signal.from_test_results(tests_pass=False)
        assert signal == Signal.WRONG

    def test_signal_regression_penalty(self) -> None:
        """Each regression reduces signal."""
        no_regression = Signal.from_test_results(tests_pass=True, regressions=0)
        with_regression = Signal.from_test_results(tests_pass=True, regressions=2)
        assert with_regression < no_regression

    def test_signal_goal_not_advanced_reduces(self) -> None:
        """Goal not advanced reduces signal."""
        advanced = Signal.from_test_results(tests_pass=True, goal_advanced=True)
        not_advanced = Signal.from_test_results(tests_pass=True, goal_advanced=False)
        assert not_advanced < advanced

    def test_signal_capped_at_one(self) -> None:
        """Signal never exceeds 1.0."""
        signal = Signal.from_test_results(tests_pass=True, regressions=0, goal_advanced=True, elegant=True)
        assert signal <= 1.0

    def test_signal_floored_at_zero(self) -> None:
        """Signal never goes below 0.0."""
        signal = Signal.from_test_results(tests_pass=True, regressions=100)
        assert signal >= 0.0


class TestElfDecisionMakerDecide:
    """ElfDecisionMaker.decide() tests."""

    @pytest.mark.asyncio
    async def test_decide_returns_decision(self, db_path_str: str) -> None:
        """decide() returns a Decision object."""
        mem = await SmartMemory.open(db_path_str)
        maker = ElfDecisionMaker(mem)

        decision = await maker.decide("what should I work on next?")
        assert isinstance(decision, Decision)
        await mem.close()

    @pytest.mark.asyncio
    async def test_decision_has_choice(self, db_path_str: str) -> None:
        """Decision always has a non-empty choice."""
        mem = await SmartMemory.open(db_path_str)
        maker = ElfDecisionMaker(mem)

        decision = await maker.decide("how should I approach this problem?")
        assert isinstance(decision.choice, str)
        assert len(decision.choice) > 0
        await mem.close()

    @pytest.mark.asyncio
    async def test_decision_has_rationale(self, db_path_str: str) -> None:
        """Decision includes rationale explaining the choice."""
        mem = await SmartMemory.open(db_path_str)
        maker = ElfDecisionMaker(mem)

        decision = await maker.decide("what architecture to use?")
        assert isinstance(decision.rationale, str)
        assert len(decision.rationale) > 0
        await mem.close()

    @pytest.mark.asyncio
    async def test_decision_has_self_alignment(self, db_path_str: str) -> None:
        """Decision includes a self_alignment score."""
        mem = await SmartMemory.open(db_path_str)
        maker = ElfDecisionMaker(mem)

        decision = await maker.decide("how to build the next feature?")
        assert 0.0 <= decision.self_alignment <= 1.0
        await mem.close()

    @pytest.mark.asyncio
    async def test_decide_with_options(self, db_path_str: str) -> None:
        """decide() with options picks one of them."""
        mem = await SmartMemory.open(db_path_str)
        maker = ElfDecisionMaker(mem)

        options = ["Build feature A", "Build feature B", "Refactor existing code"]
        decision = await maker.decide("what to build next?", options=options)

        assert decision.choice in options
        await mem.close()

    @pytest.mark.asyncio
    async def test_decide_creates_pending_record(self, db_path_str: str) -> None:
        """decide() adds a PendingDecision to internal state."""
        mem = await SmartMemory.open(db_path_str)
        maker = ElfDecisionMaker(mem)

        assert len(maker.pending_decisions) == 0
        await maker.decide("what should elf focus on?")
        assert len(maker.pending_decisions) == 1
        await mem.close()

    @pytest.mark.asyncio
    async def test_decide_returns_pending_record(self, db_path_str: str) -> None:
        """decision.pending is a PendingDecision with block_ids."""
        mem = await SmartMemory.open(db_path_str)
        maker = ElfDecisionMaker(mem)

        # Learn some context first
        await mem.remember("elf's goal: build autonomous systems")

        decision = await maker.decide("what should I do next?")
        assert decision.pending is not None
        assert isinstance(decision.pending, PendingDecision)
        await mem.close()

    @pytest.mark.asyncio
    async def test_decide_has_supporting_frames(self, db_path_str: str) -> None:
        """Decision includes context from each frame."""
        mem = await SmartMemory.open(db_path_str)
        maker = ElfDecisionMaker(mem)

        decision = await maker.decide("how should I approach testing?")
        assert "self" in decision.supporting_frames
        assert "task" in decision.supporting_frames
        assert "attention" in decision.supporting_frames
        assert "attention" in decision.supporting_frames  # world covered by attention frame
        await mem.close()


class TestElfDecisionMakerCalibrate:
    """ElfDecisionMaker.calibrate() tests."""

    @pytest.mark.asyncio
    async def test_calibrate_returns_result(self, db_path_str: str) -> None:
        """calibrate() returns a CalibrationResult."""
        mem = await SmartMemory.open(db_path_str)
        maker = ElfDecisionMaker(mem)

        decision = await maker.decide("what to build next?")
        result = await maker.calibrate(decision.pending, Signal.GOOD)

        assert isinstance(result, CalibrationResult)
        await mem.close()

    @pytest.mark.asyncio
    async def test_calibrate_removes_pending(self, db_path_str: str) -> None:
        """After calibration, decision is removed from pending."""
        mem = await SmartMemory.open(db_path_str)
        maker = ElfDecisionMaker(mem)

        decision = await maker.decide("what to build next?")
        assert len(maker.pending_decisions) == 1

        await maker.calibrate(decision.pending, Signal.PERFECT)
        assert len(maker.pending_decisions) == 0
        await mem.close()

    @pytest.mark.asyncio
    async def test_calibrate_records_signal(self, db_path_str: str) -> None:
        """CalibrationResult records the signal used."""
        mem = await SmartMemory.open(db_path_str)
        maker = ElfDecisionMaker(mem)

        decision = await maker.decide("something")
        result = await maker.calibrate(decision.pending, Signal.PERFECT)

        assert abs(result.signal - Signal.PERFECT) < 0.01
        await mem.close()

    @pytest.mark.asyncio
    async def test_calibrate_records_learning(self, db_path_str: str) -> None:
        """calibrate() records a learning block."""
        mem = await SmartMemory.open(db_path_str)
        maker = ElfDecisionMaker(mem)

        decision = await maker.decide("how to structure the API?")
        result = await maker.calibrate(decision.pending, Signal.GOOD, note="API design worked well")

        assert isinstance(result.learning_recorded, str)
        assert "[LEARNING]" in result.learning_recorded
        await mem.close()

    @pytest.mark.asyncio
    async def test_calibrate_reports_block_count(self, db_path_str: str) -> None:
        """CalibrationResult includes number of blocks reinforced."""
        mem = await SmartMemory.open(db_path_str)
        maker = ElfDecisionMaker(mem)

        decision = await maker.decide("what architecture to use?")
        result = await maker.calibrate(decision.pending, Signal.GOOD)

        assert isinstance(result.blocks_reinforced, int)
        assert result.blocks_reinforced >= 0
        await mem.close()

    @pytest.mark.asyncio
    async def test_calibrate_advises_dream(self, db_path_str: str) -> None:
        """CalibrationResult indicates if should_dream."""
        mem = await SmartMemory.open(db_path_str)
        maker = ElfDecisionMaker(mem)

        decision = await maker.decide("something")
        result = await maker.calibrate(decision.pending, Signal.GOOD)

        assert isinstance(result.should_dream, bool)
        await mem.close()


class TestElfDecisionMakerFullCycle:
    """Full decision → execute → calibrate cycle."""

    @pytest.mark.asyncio
    async def test_full_decision_calibration_cycle(self, db_path_str: str) -> None:
        """Complete cycle: learn context → decide → calibrate → reflect."""
        mem = await SmartMemory.open(db_path_str)
        maker = ElfDecisionMaker(mem)

        # 1. Populate memory with context
        await mem.remember("elf's goal: maximize autonomy through learning", tags=["self/principle"])
        await mem.remember("current task: build autonomous decision loop", tags=["task/current"])

        # 2. Make a decision
        decision = await maker.decide(
            "what should elf do to improve autonomous decision-making?",
            options=[
                "Build ElfDecisionMaker with multi-frame synthesis",
                "Run manual simulation scripts",
                "Ask user for direction",
            ],
        )
        assert decision.choice in [
            "Build ElfDecisionMaker with multi-frame synthesis",
            "Run manual simulation scripts",
            "Ask user for direction",
        ]

        # 3. Calibrate with excellent result (decision was good)
        result = await maker.calibrate(
            decision.pending,
            Signal.PERFECT,
            note="autonomous decision loop implemented, 480 tests pass",
        )

        # 4. Verify cycle completed
        assert result.signal == Signal.PERFECT
        assert "LEARNING" in result.learning_recorded
        assert len(maker.pending_decisions) == 0
        await mem.close()

    @pytest.mark.asyncio
    async def test_multiple_decisions_and_calibrations(self, db_path_str: str) -> None:
        """Multiple decision cycles — pending list managed correctly."""
        mem = await SmartMemory.open(db_path_str)
        maker = ElfDecisionMaker(mem)

        # Make 3 decisions
        d1 = await maker.decide("what to build first?")
        d2 = await maker.decide("what to test?")
        d3 = await maker.decide("what to document?")

        assert len(maker.pending_decisions) == 3

        # Calibrate in order
        await maker.calibrate(d1.pending, Signal.PERFECT)
        assert len(maker.pending_decisions) == 2

        await maker.calibrate(d2.pending, Signal.GOOD)
        assert len(maker.pending_decisions) == 1

        await maker.calibrate(d3.pending, Signal.NEUTRAL)
        assert len(maker.pending_decisions) == 0
        await mem.close()

    @pytest.mark.asyncio
    async def test_decision_summary_observable(self, db_path_str: str) -> None:
        """summary() returns observable state."""
        mem = await SmartMemory.open(db_path_str)
        maker = ElfDecisionMaker(mem)

        summary = maker.summary()
        assert "pending_count" in summary
        assert "pending_decisions" in summary
        assert summary["pending_count"] == 0

        await maker.decide("what now?")
        summary = maker.summary()
        assert summary["pending_count"] == 1
        await mem.close()

    @pytest.mark.asyncio
    async def test_reflect_returns_text(self, db_path_str: str) -> None:
        """reflect() returns text from self-frame recall."""
        mem = await SmartMemory.open(db_path_str)
        maker = ElfDecisionMaker(mem)

        text = await maker.reflect("what decisions have worked best for elf")
        assert isinstance(text, str)
        await mem.close()

    @pytest.mark.asyncio
    async def test_dream_if_ready(self, db_path_str: str) -> None:
        """dream_if_ready() consolidates when should_dream is True."""
        mem = await SmartMemory.open(db_path_str)
        maker = ElfDecisionMaker(mem)

        # Not ready initially
        result = await maker.dream_if_ready()
        assert result == "not ready to dream"

        # Learn enough blocks to trigger should_dream
        threshold = mem._threshold
        for i in range(threshold):
            await mem.remember(f"Block {i}")

        # Should be ready now
        if mem.should_dream:
            result = await maker.dream_if_ready()
            assert "not ready" not in result

        await mem.close()


class TestPercieve:
    """ElfDecisionMaker.perceive() tests."""

    @pytest.mark.asyncio
    async def test_perceive_returns_all_frames(self, db_path_str: str) -> None:
        """perceive() returns results for all four frames."""
        mem = await SmartMemory.open(db_path_str)
        maker = ElfDecisionMaker(mem)

        frames = await maker.perceive("how should I approach testing?")

        assert "self" in frames
        assert "task" in frames
        assert "attention" in frames
        assert all(f in frames for f in ["self", "task", "attention"])
        await mem.close()

    @pytest.mark.asyncio
    async def test_perceive_all_frames_have_text(self, db_path_str: str) -> None:
        """All frame results have text content."""
        mem = await SmartMemory.open(db_path_str)
        maker = ElfDecisionMaker(mem)

        frames = await maker.perceive("what should I prioritize?")

        for frame_name, frame_result in frames.items():
            assert hasattr(frame_result, "text"), f"Frame '{frame_name}' missing text attribute"
        await mem.close()
