"""Example: Self-calibrating agent built on elfmem.

A reference implementation showing how an agent uses elfmem with full
discipline — recall before acting, calibrate after acting, reflect at
session boundaries. The memory self-improves through every cycle.

This code lives in examples/ — it is NOT part of the elfmem library.
elfmem provides memory primitives; the agent provides the discipline.

The discipline loop:
    SESSION START  → status + recall context + ground identity
    BEFORE TASK    → select frame + recall + set expectation
    AFTER TASK     → inline calibrate (outcome per block) + encode surprises
    SESSION END    → compute metrics + reflect + dream

See examples/agent_discipline.md for the full prompt instructions and rationale.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from elfmem import MemorySystem
from elfmem.types import FrameResult, OutcomeResult


# ── Types ─────────────────────────────────────────────────────────────────────


class TaskType(Enum):
    """Task classification determines frame selection."""

    NOVEL = "novel"
    EXECUTION = "execution"
    IDENTITY = "identity"
    CONTEXT = "context"
    QUICK = "quick"


class BlockVerdict(Enum):
    """How a recalled block was actually used during the task."""

    USED = "used"
    IGNORED = "ignored"
    MISLEADING = "misleading"


# Signal values for each verdict
VERDICT_SIGNALS: dict[BlockVerdict, float] = {
    BlockVerdict.USED: 0.85,
    BlockVerdict.IGNORED: 0.45,
    BlockVerdict.MISLEADING: 0.15,
}

# Frame selection table: task_type → (frame_name, top_k)
FRAME_TABLE: dict[TaskType, tuple[str, int]] = {
    TaskType.NOVEL: ("attention", 10),
    TaskType.EXECUTION: ("task", 5),
    TaskType.IDENTITY: ("self", 5),
    TaskType.CONTEXT: ("attention", 10),
    TaskType.QUICK: ("attention", 3),
}


@dataclass
class SessionMetrics:
    """Tracks calibration quality across a session."""

    recalls_made: int = 0
    blocks_used: int = 0
    blocks_ignored: int = 0
    blocks_misleading: int = 0
    surprises: int = 0
    gaps: int = 0

    @property
    def total_blocks(self) -> int:
        return self.blocks_used + self.blocks_ignored + self.blocks_misleading

    @property
    def hit_rate(self) -> float:
        """Fraction of recalled blocks that were actually used."""
        if self.total_blocks == 0:
            return 0.0
        return self.blocks_used / self.total_blocks

    @property
    def surprise_rate(self) -> float:
        """Fraction of recalls that produced a surprise."""
        if self.recalls_made == 0:
            return 0.0
        return self.surprises / self.recalls_made

    @property
    def gap_rate(self) -> float:
        """Fraction of recalls that found a knowledge gap."""
        if self.recalls_made == 0:
            return 0.0
        return self.gaps / self.recalls_made

    @property
    def noise_ratio(self) -> float:
        """Fraction of recalled blocks that were ignored or misleading."""
        if self.total_blocks == 0:
            return 0.0
        return (self.blocks_ignored + self.blocks_misleading) / self.total_blocks

    @property
    def health(self) -> str:
        """Overall calibration health assessment."""
        if self.recalls_made < 3:
            return "insufficient_data"
        issues = 0
        if self.hit_rate < 0.4:
            issues += 1
        if self.surprise_rate > 0.5:
            issues += 1
        if self.noise_ratio > 0.6:
            issues += 1
        if issues == 0:
            return "healthy"
        if issues == 1:
            return "attention"
        return "degraded"

    def summary(self) -> str:
        """One-line summary for session reflection."""
        return (
            f"hit_rate={self.hit_rate:.0%}, "
            f"surprise_rate={self.surprise_rate:.0%}, "
            f"gaps={self.gaps}, "
            f"noise={self.noise_ratio:.0%}, "
            f"health={self.health}"
        )


@dataclass
class TaskResult:
    """Result of a single task cycle through the discipline loop."""

    task_description: str
    expectation: str
    frame_used: str
    blocks_recalled: list[str]
    verdicts: dict[str, BlockVerdict]
    surprise: str | None
    gap: str | None
    outcomes_sent: int


@dataclass
class SessionReflection:
    """Reflection produced at session end."""

    work_summary: str
    metrics: SessionMetrics
    insight: str
    adjustment: str
    reflection_block_id: str | None


# ── Agent ─────────────────────────────────────────────────────────────────────


class CalibratingAgent:
    """An agent that self-calibrates its elfmem usage through disciplined feedback.

    Usage:
        system = await MemorySystem.from_config("agent.db")
        agent = CalibratingAgent(system)

        # Start session (grounds in identity + recent context)
        await agent.start_session()

        # Before each task: recall with frame selection
        recall = await agent.before_task("implement pre-filter", TaskType.EXECUTION)

        # ... do the work ...

        # After each task: calibrate every recalled block
        result = await agent.after_task(
            expectation="Pure function, ≤50 lines",
            verdicts={block_id: BlockVerdict.USED for block_id in recall_ids},
            surprise="Empty query + SELF frame returns constitutional blocks",
        )

        # End session: reflect + consolidate
        reflection = await agent.end_session(
            work_summary="Implemented recall pre-filter",
            insight="SELF frame has special empty-query semantics",
            adjustment="Check frame-specific edge cases earlier",
        )

    Design:
        - start_session() grounds the agent in identity and recent context
        - before_task() selects frame by task type, recalls, returns context
        - after_task() calibrates each block, encodes surprises and gaps
        - end_session() computes metrics, reflects, consolidates
        - Metrics track calibration quality across the session
        - The agent's memory literally improves with every cycle
    """

    def __init__(self, system: MemorySystem) -> None:
        self._system = system
        self._metrics = SessionMetrics()
        self._last_recall: FrameResult | None = None
        self._last_task: str = ""
        self._session_started = False

    @property
    def metrics(self) -> SessionMetrics:
        """Current session metrics (read-only snapshot)."""
        return self._metrics

    # ── Session lifecycle ─────────────────────────────────────────────────

    async def start_session(self) -> tuple[str, str]:
        """Start a session: check health, recall recent context, ground in identity.

        Returns (recent_context, identity_context) for the agent to read.
        """
        await self._system.begin_session()
        self._metrics = SessionMetrics()
        self._session_started = True

        # Check system health
        status = await self._system.status()
        if "consolidat" in (status.suggestion or "").lower():
            await self._system.dream()

        # Recall recent context and identity
        recent = await self._system.frame(
            "attention", query="my recent work and decisions", top_k=5,
        )
        identity = await self._system.frame(
            "self", query="my role and principles", top_k=3,
        )

        return recent.text, identity.text

    async def end_session(
        self,
        work_summary: str,
        insight: str,
        adjustment: str,
    ) -> SessionReflection:
        """End session: compute metrics, record reflection, consolidate.

        Args:
            work_summary: What was accomplished this session.
            insight: Most important thing learned.
            adjustment: What to do differently next time.
        """
        metrics = self._metrics
        reflection_content = (
            f"Session: {work_summary}. "
            f"{metrics.summary()}. "
            f"Insight: {insight}. "
            f"Adjustment: {adjustment}."
        )
        result = await self._system.remember(
            reflection_content,
            tags=["calibration/session", "meta-learning"],
        )

        # Consolidate everything
        await self._system.dream()
        self._session_started = False

        return SessionReflection(
            work_summary=work_summary,
            metrics=metrics,
            insight=insight,
            adjustment=adjustment,
            reflection_block_id=result.block_id,
        )

    # ── Task lifecycle ────────────────────────────────────────────────────

    async def before_task(
        self,
        task_description: str,
        task_type: TaskType = TaskType.EXECUTION,
    ) -> FrameResult:
        """Recall relevant knowledge for a task using frame selection.

        Returns the FrameResult so the agent can read the blocks and use them.
        Block IDs from result.blocks are needed for after_task() verdicts.
        """
        frame_name, top_k = FRAME_TABLE[task_type]

        result = await self._system.frame(
            frame_name, query=task_description, top_k=top_k,
        )

        self._last_recall = result
        self._last_task = task_description
        self._metrics.recalls_made += 1

        # Detect knowledge gap: no blocks or all blocks score very low
        if not result.blocks or all(b.score < 0.3 for b in result.blocks):
            self._metrics.gaps += 1

        return result

    async def after_task(
        self,
        expectation: str,
        verdicts: dict[str, BlockVerdict],
        surprise: str | None = None,
        gap: str | None = None,
    ) -> TaskResult:
        """Calibrate recalled blocks and encode surprises / gaps.

        Args:
            expectation: What you expected before acting (for the record).
            verdicts: Map of block_id → BlockVerdict for each recalled block.
            surprise: If outcome differed from expectation, describe the pattern.
            gap: If knowledge was missing, describe what was needed.
        """
        outcomes_sent = 0

        # Signal each block based on its verdict
        for block_id, verdict in verdicts.items():
            signal = VERDICT_SIGNALS[verdict]
            await self._system.outcome(
                [block_id],
                signal=signal,
                source=verdict.value,
            )
            outcomes_sent += 1

            # Update metrics
            if verdict == BlockVerdict.USED:
                self._metrics.blocks_used += 1
            elif verdict == BlockVerdict.IGNORED:
                self._metrics.blocks_ignored += 1
            elif verdict == BlockVerdict.MISLEADING:
                self._metrics.blocks_misleading += 1

        # Encode surprise as a new pattern
        if surprise:
            self._metrics.surprises += 1
            await self._system.remember(
                f"Expected: {expectation}. Observed: {surprise}. "
                f"Task: {self._last_task}.",
                tags=["calibration/surprise", "pattern/discovered"],
            )

        # Encode knowledge gap
        if gap:
            if not any(
                b_id in verdicts for b_id in
                [b.id for b in (self._last_recall.blocks if self._last_recall else [])]
            ):
                self._metrics.gaps += 1
            await self._system.remember(
                f"Gap: {gap}. Task: {self._last_task}.",
                tags=["calibration/gap"],
            )

        # Dream if ready
        if self._system.should_dream:
            await self._system.dream()

        recalled_ids = (
            [b.id for b in self._last_recall.blocks] if self._last_recall else []
        )

        return TaskResult(
            task_description=self._last_task,
            expectation=expectation,
            frame_used=self._last_recall.frame_name if self._last_recall else "",
            blocks_recalled=recalled_ids,
            verdicts=verdicts,
            surprise=surprise,
            gap=gap,
            outcomes_sent=outcomes_sent,
        )

    # ── Convenience ───────────────────────────────────────────────────────

    async def quick_calibrate(
        self,
        used_ids: list[str],
        ignored_ids: list[str] | None = None,
        misleading_ids: list[str] | None = None,
    ) -> int:
        """Shorthand: calibrate blocks by passing ID lists instead of a verdict dict.

        Returns the number of outcome signals sent.
        """
        verdicts: dict[str, BlockVerdict] = {}
        for bid in used_ids:
            verdicts[bid] = BlockVerdict.USED
        for bid in (ignored_ids or []):
            verdicts[bid] = BlockVerdict.IGNORED
        for bid in (misleading_ids or []):
            verdicts[bid] = BlockVerdict.MISLEADING

        sent = 0
        for block_id, verdict in verdicts.items():
            await self._system.outcome(
                [block_id],
                signal=VERDICT_SIGNALS[verdict],
                source=verdict.value,
            )
            sent += 1

            if verdict == BlockVerdict.USED:
                self._metrics.blocks_used += 1
            elif verdict == BlockVerdict.IGNORED:
                self._metrics.blocks_ignored += 1
            else:
                self._metrics.blocks_misleading += 1

        return sent

    async def diagnose(self) -> str:
        """Return a diagnostic string based on current session metrics.

        Helpful for agents that want to self-adjust mid-session.
        """
        m = self._metrics
        issues: list[str] = []

        if m.recalls_made < 3:
            return "Insufficient data — complete at least 3 task cycles."

        if m.hit_rate < 0.4:
            issues.append(
                f"Hit rate low ({m.hit_rate:.0%}): "
                "try more specific queries or different frames."
            )
        if m.surprise_rate > 0.5:
            issues.append(
                f"Surprise rate high ({m.surprise_rate:.0%}): "
                "knowledge may be stale — curate and re-learn."
            )
        if m.noise_ratio > 0.6:
            issues.append(
                f"Noise ratio high ({m.noise_ratio:.0%}): "
                "lower top_k or use more focused frames."
            )
        if m.gap_rate > 0.25:
            issues.append(
                f"Gap rate high ({m.gap_rate:.0%}): "
                "domain not well covered — seed more knowledge."
            )

        if not issues:
            return f"Calibration healthy. {m.summary()}"

        return "Calibration issues:\n" + "\n".join(f"  - {i}" for i in issues)
