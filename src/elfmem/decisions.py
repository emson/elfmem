"""ElfDecisionMaker — autonomous decision-making and calibration using elfmem.

elf uses its own memory system to:
1. Make decisions using multi-frame recall (self + task + attention + world)
2. Execute actions (delegating to Claude Code tools)
3. Calibrate performance by signalling outcome back to the blocks that informed decisions
4. Learn by reinforcing good patterns and letting bad ones decay

The loop:
    PERCEIVE (multi-frame recall)
    → DECIDE (synthesize across frames, SELF veto)
    → EXECUTE (Claude Code tools or MCP)
    → OBSERVE (tests, regressions, goal advancement)
    → CALIBRATE (outcome() with block_ids)
    → CONSOLIDATE (dream() when pending)
    → REFLECT (periodic curate + SELF update)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from elfmem.smart import SmartMemory
from elfmem.types import FrameResult


# ── Signals ───────────────────────────────────────────────────────────────────

class Signal:
    """Decision outcome signals for calibration."""
    PERFECT   = 1.0   # Tests pass, no regressions, goal fully advanced, elegant
    GOOD      = 0.75  # Works well, minor issues
    NEUTRAL   = 0.5   # Uncertain or mixed result
    POOR      = 0.3   # Partial failure or off-target
    WRONG     = 0.0   # Tests broken, regressions, or goal not advanced

    @staticmethod
    def from_test_results(
        tests_pass: bool,
        regressions: int = 0,
        goal_advanced: bool = True,
        elegant: bool = True,
    ) -> float:
        """Compute signal from objective test and goal metrics."""
        if not tests_pass:
            return Signal.WRONG
        base = 0.70
        if elegant:
            base += 0.15
        if goal_advanced:
            base += 0.15
        regression_penalty = min(0.3, regressions * 0.10)
        return max(0.0, base - regression_penalty)


# ── Decision record ────────────────────────────────────────────────────────────

@dataclass
class PendingDecision:
    """A decision waiting for outcome calibration."""

    situation: str
    """Natural language description of the situation that prompted the decision."""

    choice: str
    """What was decided."""

    block_ids: list[str]
    """IDs of memory blocks that informed this decision (used for outcome signalling)."""

    decision_block_id: str | None = None
    """ID of the block that records this decision itself."""

    tags: list[str] = field(default_factory=list)
    """Tags associated with this decision for future retrieval."""


@dataclass
class Decision:
    """A completed decision ready for execution."""

    choice: str
    """What was decided — a clear, actionable description."""

    rationale: str
    """Why this was chosen (from frame synthesis)."""

    self_alignment: float
    """How well this aligns with elf's constitutional principles (0–1)."""

    supporting_frames: dict[str, str]
    """What each frame contributed: {"self": "...", "task": "...", "attention": "..."}"""

    pending: PendingDecision | None = None
    """The pending record for later calibration."""


# ── Calibration record ─────────────────────────────────────────────────────────

@dataclass
class CalibrationResult:
    """Result of calibrating a decision's outcome."""

    situation: str
    signal: float
    blocks_reinforced: int
    learning_recorded: str
    should_dream: bool


# ── Decision maker ─────────────────────────────────────────────────────────────

class ElfDecisionMaker:
    """elf makes autonomous decisions using multi-frame memory synthesis.

    Usage:
        maker = ElfDecisionMaker(memory)

        # Make a decision
        decision = await maker.decide("what should elf focus on next?")
        print(decision.choice)
        print(decision.rationale)

        # Execute the decision...

        # Calibrate — signal how well it worked
        result = await maker.calibrate(decision.pending, Signal.PERFECT)

    Design:
        - SELF frame provides constitutional veto (identity stays stable)
        - TASK frame provides goal alignment
        - ATTENTION frame provides situational context
        - WORLD frame provides applicable patterns
        - outcome() reinforces blocks that led to good decisions
        - dream() consolidates decision patterns
        - curate() prunes weak patterns over time
    """

    def __init__(self, memory: SmartMemory) -> None:
        self._memory = memory
        self._pending: list[PendingDecision] = []

    async def perceive(self, situation: str, top_k: int = 5) -> dict[str, FrameResult]:
        """Retrieve context from all frames for a situation.

        Three frames serve distinct cognitive roles:
          self      → Constitutional veto: values, identity, principles (who am I?)
          task      → Goal alignment: objectives, next steps (what am I trying to do?)
          attention → Context + patterns: current state + what has worked (what's happening?)

        Returns a mapping of frame_name → FrameResult.
        """
        self_ctx = await self._memory.recall(situation, frame="self", top_k=3)
        task_ctx = await self._memory.recall(situation, frame="task", top_k=top_k)
        attention_ctx = await self._memory.recall(situation, frame="attention", top_k=top_k)

        return {
            "self": self_ctx,
            "task": task_ctx,
            "attention": attention_ctx,
        }

    async def decide(
        self,
        situation: str,
        options: list[str] | None = None,
        top_k: int = 5,
    ) -> Decision:
        """Make a decision using multi-frame synthesis.

        Args:
            situation: Natural language description of what needs deciding.
            options: Optional list of specific options to evaluate.
                     If None, frames provide context for open-ended decision.
            top_k: Number of blocks to retrieve per frame.

        Returns:
            Decision with chosen action, rationale, and pending calibration record.
        """
        frames = await self.perceive(situation, top_k=top_k)

        # Collect all block_ids that informed this decision
        all_block_ids: list[str] = []
        for frame_result in frames.values():
            all_block_ids.extend(b.id for b in frame_result.blocks)

        # Remove duplicates while preserving order
        seen: set[str] = set()
        block_ids = [b for b in all_block_ids if not (b in seen or seen.add(b))]  # type: ignore[func-returns-value]

        # Build synthesis context from frames
        self_text = frames["self"].text
        task_text = frames["task"].text
        attention_text = frames["attention"].text

        # Evaluate options against SELF if provided
        choice, rationale, self_alignment = self._synthesize(
            situation=situation,
            options=options or [],
            self_text=self_text,
            task_text=task_text,
            attention_text=attention_text,
        )

        # Record the decision itself as a memory block
        tags = ["decision/pending", "cognitive-loop", "autonomous"]
        learn_result = await self._memory.remember(
            f"[DECISION] {choice} | for: {situation} | alignment: {self_alignment:.2f}",
            tags=tags,
        )
        decision_block_id = learn_result.block_id if hasattr(learn_result, "block_id") else None

        # Create pending record for later calibration
        pending = PendingDecision(
            situation=situation,
            choice=choice,
            block_ids=block_ids,
            decision_block_id=decision_block_id,
            tags=tags,
        )
        self._pending.append(pending)

        return Decision(
            choice=choice,
            rationale=rationale,
            self_alignment=self_alignment,
            supporting_frames={
                "self": self_text[:200],
                "task": task_text[:200],
                "attention": attention_text[:200],
            },
            pending=pending,
        )

    async def calibrate(
        self,
        pending: PendingDecision,
        signal: float,
        note: str = "",
    ) -> CalibrationResult:
        """Calibrate decision quality by signalling outcome to memory system.

        This is the learning mechanism: blocks that led to good decisions are
        reinforced (higher confidence, slower decay); blocks that led to bad
        decisions decay faster.

        Args:
            pending: The PendingDecision to calibrate (from decision.pending).
            signal: Outcome quality [0.0–1.0]. Use Signal.PERFECT, Signal.GOOD, etc.
            note: Optional note to record alongside the calibration.

        Returns:
            CalibrationResult with learning summary.
        """
        # Signal outcome to all blocks that informed the decision
        if pending.block_ids:
            await self._memory.outcome(
                pending.block_ids,
                signal=signal,
                source="decision_loop",
                weight=1.0,
            )

        # Record the learning explicitly
        quality = self._signal_to_quality(signal)
        learning = (
            f"[LEARNING] Decision '{pending.choice}' → {quality} (signal={signal:.2f})"
            + (f". {note}" if note else "")
        )
        await self._memory.remember(
            learning,
            tags=["learning", "calibration", f"outcome/{quality}", "cognitive-loop"],
        )

        # Remove from pending
        if pending in self._pending:
            self._pending.remove(pending)

        # Consolidate if ready
        should_dream = self._memory.should_dream

        return CalibrationResult(
            situation=pending.situation,
            signal=signal,
            blocks_reinforced=len(pending.block_ids),
            learning_recorded=learning,
            should_dream=should_dream,
        )

    async def reflect(self, topic: str = "what decisions have worked best") -> str:
        """Reflect on past decisions to extract durable wisdom.

        Queries the self frame for calibrated decision patterns and
        consolidates them into elf's identity.
        """
        # Query calibration learnings
        learnings = await self._memory.recall(
            f"what have I learned about {topic}",
            frame="self",
            top_k=10,
        )

        # Consolidate if pending
        if self._memory.should_dream:
            await self._memory.dream()

        return learnings.text

    async def dream_if_ready(self) -> str:
        """Consolidate pending blocks if should_dream is True.

        Returns a summary of consolidation or 'not ready' message.
        """
        if not self._memory.should_dream:
            return "not ready to dream"
        result = await self._memory.dream()
        if result is None:
            return "nothing to consolidate"
        return f"consolidated {result.processed} blocks: {result.promoted} promoted, {result.deduplicated} deduped"

    @property
    def pending_decisions(self) -> list[PendingDecision]:
        """Decisions awaiting calibration."""
        return list(self._pending)

    def summary(self) -> dict[str, Any]:
        """Observable state for debugging and logging."""
        return {
            "pending_count": len(self._pending),
            "pending_decisions": [p.choice for p in self._pending],
        }

    # ── Private ────────────────────────────────────────────────────────────────

    def _synthesize(
        self,
        situation: str,
        options: list[str],
        self_text: str,
        task_text: str,
        attention_text: str,
    ) -> tuple[str, str, float]:
        """Synthesize a decision from frame contexts.

        Returns (choice, rationale, self_alignment_score).

        When options are provided, scores each against SELF + TASK and picks the best.
        When no options, selects the highest-priority task goal from context.
        """
        if not options:
            choice = self._extract_top_priority(task_text, self_text)
            rationale = (
                f"Selected from task frame: '{task_text[:100]}'. "
                f"Aligns with elf's identity: '{self_text[:100]}'."
            )
            self_alignment = self._score_alignment(choice, self_text)
            return choice, rationale, self_alignment

        # SELF (constitutional) has highest weight, then task goals, then attention context
        scored: list[tuple[float, str]] = []
        for option in options:
            self_alignment = self._score_alignment(option, self_text)
            task_relevance = self._score_alignment(option, task_text)
            context_relevance = self._score_alignment(option, attention_text)
            combined = 0.5 * self_alignment + 0.3 * task_relevance + 0.2 * context_relevance
            scored.append((combined, option))

        scored.sort(reverse=True)
        best_score, best_option = scored[0]

        rationale = (
            f"Chose '{best_option}' (alignment={best_score:.2f}) over "
            f"{len(options)-1} alternatives. SELF: '{self_text[:80]}'. Task: '{task_text[:80]}'."
        )

        return best_option, rationale, best_score

    def _score_alignment(self, option: str, context: str) -> float:
        """Simple keyword-overlap alignment score between option and context.

        A lightweight heuristic: real implementation would use embedding similarity
        via elfmem_recall with frame="self" scoring.
        """
        if not context.strip():
            return 0.5  # Unknown → neutral

        option_words = set(option.lower().split())
        context_words = set(context.lower().split())
        overlap = len(option_words & context_words)
        if not option_words:
            return 0.5
        return min(1.0, 0.5 + overlap / len(option_words) * 0.5)

    def _extract_top_priority(self, task_text: str, self_text: str) -> str:
        """Extract the top-priority task from the task frame text."""
        # Take first sentence of task context as the priority
        sentences = task_text.strip().split(".")
        for sentence in sentences:
            s = sentence.strip()
            if len(s) > 10:
                return s
        return task_text[:100].strip() or "continue advancing elf's capabilities"

    def _signal_to_quality(self, signal: float) -> str:
        if signal >= Signal.PERFECT * 0.9:
            return "excellent"
        elif signal >= Signal.GOOD * 0.9:
            return "good"
        elif signal >= Signal.NEUTRAL:
            return "neutral"
        elif signal >= Signal.POOR:
            return "poor"
        return "failed"
