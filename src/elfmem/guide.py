"""Agent-friendly documentation for elfmem operations.

This module provides structured, runtime-accessible documentation that helps
LLM agents understand when and how to use each elfmem operation — without
needing to consult external docs.

Usage::

    system.guide()           # overview of all operations
    system.guide("learn")    # detailed guide for learn()
    system.guide("unknown")  # returns list of valid method names
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AgentGuide:
    """Structured documentation for a single elfmem operation.

    All fields are plain strings optimised for LLM context consumption.
    ``str(guide)`` renders a compact multi-line reference card.
    """

    name: str
    what: str       # One sentence: what does this do?
    when: str       # Decision criteria: when should the agent call this?
    when_not: str   # Anti-patterns: when is this the wrong choice?
    cost: str       # Latency/cost signal: "Instant" | "Fast" | "LLM call"
    returns: str    # What comes back and what the values mean
    next: str       # Typical follow-up action
    example: str    # Minimal working code snippet

    def __str__(self) -> str:
        example_indented = "\n".join(f"    {line}" for line in self.example.splitlines())
        return (
            f"elfmem.{self.name}()\n"
            f"  What:       {self.what}\n"
            f"  Use when:   {self.when}\n"
            f"  Don't use:  {self.when_not}\n"
            f"  Cost:       {self.cost}\n"
            f"  Returns:    {self.returns}\n"
            f"  Next:       {self.next}\n"
            f"  Example:\n"
            f"{example_indented}"
        )


# ── Static guide data ─────────────────────────────────────────────────────────

GUIDES: dict[str, AgentGuide] = {
    "learn": AgentGuide(
        name="learn",
        what="Store a knowledge block for future retrieval.",
        when=(
            "The agent discovers a fact, preference, decision, or observation "
            "worth remembering across sessions."
        ),
        when_not=(
            "Transient context that only matters in the current turn, or "
            "information already present in the active prompt."
        ),
        cost="Instant. No LLM calls.",
        returns=(
            "LearnResult. status values: "
            "'created' — new block stored in inbox; "
            "'duplicate_rejected' — exact content already exists, no action taken; "
            "'near_duplicate_superseded' — similar existing block replaced."
        ),
        next=(
            "Blocks queue in inbox until consolidate() runs. "
            "The session() context manager auto-consolidates on exit when inbox >= threshold."
        ),
        example=(
            "result = await system.learn('User prefers dark mode')\n"
            "print(result)  # Stored block a1b2c3d4. Status: created."
        ),
    ),
    "consolidate": AgentGuide(
        name="consolidate",
        what="Process inbox blocks: score, embed, deduplicate, and promote to active memory.",
        when=(
            "After a batch of learn() calls, or explicitly before recall/frame "
            "when you know new blocks are in inbox. "
            "The session() context manager handles this automatically on exit."
        ),
        when_not=(
            "Inbox is empty — safe to call but a no-op. "
            "Avoid calling in a tight loop; one call processes all pending blocks."
        ),
        cost=(
            "LLM call per block (alignment scoring + tag inference). "
            "Slow for large inboxes; fast for small ones."
        ),
        returns=(
            "ConsolidateResult with counts: processed (total inbox blocks), "
            "promoted (moved to active), deduplicated (near-duplicates found), "
            "edges_created (knowledge graph edges built)."
        ),
        next=(
            "Promoted blocks are now searchable via frame() and recall(). "
            "Call status() to verify memory state."
        ),
        example=(
            "result = await system.consolidate()\n"
            "print(result)  # Consolidated 5: 4 promoted, 1 deduped, 8 edges."
        ),
    ),
    "frame": AgentGuide(
        name="frame",
        what="Retrieve and render context for a named frame, ready for prompt injection.",
        when=(
            "Assembling context for an LLM prompt. "
            "Use 'self' for identity context, 'attention' for query-relevant knowledge, "
            "'task' for goal/task context."
        ),
        when_not=(
            "You only need raw block data without rendering — use recall() instead. "
            "Avoid calling frame() inside tight generation loops; results are cached."
        ),
        cost="Fast. Embedding call if query provided; no LLM calls.",
        returns=(
            "FrameResult. Use result.text for direct prompt injection. "
            "result.blocks contains the scored ScoredBlock candidates. "
            "result.cached indicates whether this was served from the TTL cache."
        ),
        next=(
            "Inject result.text into your LLM prompt. "
            "Reinforce is a side effect of retrieval — no separate call needed."
        ),
        example=(
            "ctx = await system.frame('attention', query='error handling')\n"
            "prompt = f'{ctx.text}\\nUser: how do I handle errors?'"
        ),
    ),
    "recall": AgentGuide(
        name="recall",
        what="Raw retrieval returning scored blocks without rendering or side effects.",
        when=(
            "Inspecting what is in memory, debugging retrieval quality, "
            "or building custom rendering from scored block data."
        ),
        when_not=(
            "You need context ready for prompt injection — use frame() instead. "
            "frame() renders, respects token budgets, and handles caching."
        ),
        cost="Fast. Embedding call if query provided; no LLM calls.",
        returns=(
            "list[ScoredBlock] sorted by composite score descending. "
            "Empty list if nothing found — never raises for empty results."
        ),
        next="No side effects. Safe to call multiple times with the same query.",
        example=(
            "blocks = await system.recall('error handling', top_k=3)\n"
            "for b in blocks:\n"
            "    print(b)  # [0.87] User prefers explicit error handling..."
        ),
    ),
    "curate": AgentGuide(
        name="curate",
        what="Maintenance: archive decayed blocks, prune weak edges, reinforce top knowledge.",
        when=(
            "Explicit maintenance after heavy use, or when retrieval quality degrades. "
            "Also runs automatically when curate_interval_hours elapses after consolidate()."
        ),
        when_not=(
            "Immediately after consolidate() — auto-curate already triggers if interval elapsed. "
            "Don't call in response to every session; it's a periodic operation."
        ),
        cost="Fast. Database operations only; no LLM calls.",
        returns=(
            "CurateResult with counts: archived (decayed blocks removed from active), "
            "edges_pruned (weak graph edges removed), "
            "reinforced (top-N blocks had reinforcement boosted)."
        ),
        next=(
            "Memory is now cleaner. "
            "Retrieval quality may improve as stale blocks are gone."
        ),
        example=(
            "result = await system.curate()\n"
            "print(result)  # Curated: 2 archived, 1 edges pruned, 5 reinforced."
        ),
    ),
    "status": AgentGuide(
        name="status",
        what="Return a snapshot of system state with a suggested next action.",
        when=(
            "Deciding whether to consolidate, curate, or start a session. "
            "Checking memory health before a long agent run. "
            "Verifying state after operations."
        ),
        when_not="(Always safe to call. No side effects.)",
        cost="Fast. One database read; no LLM calls.",
        returns=(
            "SystemStatus with: session_active, inbox_count/inbox_threshold, "
            "active_count, archived_count, health ('good'|'attention'), suggestion, "
            "session_tokens (TokenUsage — LLM + embedding calls this session), "
            "lifetime_tokens (TokenUsage — all-time total, persisted across restarts). "
            "Use result.suggestion for the recommended next action. "
            "Use str(result.session_tokens) for a compact token cost line."
        ),
        next="Follow result.suggestion for the recommended action.",
        example=(
            "s = await system.status()\n"
            "print(s)  # Session: active (0.5h) | Inbox: 8/10 | Active: 42 | Health: good\n"
            "          # Tokens this session: LLM: 4,820 tokens (9 calls) | Embed: 1,230 tokens (14 calls)\n"
            "          # Suggestion: Inbox nearly full. Consolidation approaching.\n"
            "if s.health == 'attention':\n"
            "    await system.consolidate()"
        ),
    ),
    "history": AgentGuide(
        name="history",
        what="Return recent operations performed by this MemorySystem in the current process.",
        when=(
            "Debugging unexpected results — e.g., recall returns nothing and you "
            "want to verify consolidate() actually ran."
        ),
        when_not=(
            "Persistent audit logging is needed — history is in-memory only "
            "and resets when the process restarts."
        ),
        cost="Instant. In-memory only; no database access.",
        returns=(
            "list[OperationRecord] with fields: operation (method name), "
            "summary (str(result) at call time), timestamp (ISO UTC). "
            "Most recent last. Empty list if no operations have run."
        ),
        next="(Informational only. No action required.)",
        example=(
            "for record in system.history(last_n=5):\n"
            "    print(record)  # learn()  →  Stored block a1b2.  [14:32:01]"
        ),
    ),
    "outcome": AgentGuide(
        name="outcome",
        what="Update block confidence using a normalised domain signal via Bayesian update.",
        when=(
            "After an observable result can be scored: a forecast resolves, tests pass/fail, "
            "content engagement is measured, or a CSAT score arrives. "
            "Works without an active session — outcomes may arrive weeks after retrieval."
        ),
        when_not=(
            "To reinforce recently-used blocks — that happens automatically via frame(). "
            "Don't call outcome() for transient observations; only for measurable results "
            "that reflect whether retrieved knowledge was actually correct or useful."
        ),
        cost="Fast. Database operations only; no LLM calls.",
        returns=(
            "OutcomeResult with: blocks_updated (active blocks whose confidence changed), "
            "mean_confidence_delta (average confidence shift, positive or negative), "
            "edges_reinforced (graph edges strengthened for positive signals), "
            "blocks_penalized (blocks whose decay was accelerated for low signals). "
            "blocks_updated=0 means all block_ids were non-active (silently skipped)."
        ),
        next=(
            "Signal spectrum (default thresholds): "
            "0.8–1.0 → confidence UP + reinforce (decay resets). "
            "0.2–0.8 → confidence adjusted only (neutral dead-band). "
            "0.0–0.2 → confidence DOWN + decay accelerated automatically (no separate call needed). "
            "Over ~10 outcomes, evidence dominates the LLM alignment prior. "
            "DURABLE and PERMANENT blocks are never penalized."
        ),
        example=(
            "# Trading: Brier score resolved after 30 days\n"
            "signal = 1.0 - brier_score  # 0.85 = good forecast\n"
            "result = await system.outcome(block_ids, signal=signal, source='brier')\n"
            "print(result)  # Outcome recorded: 3 blocks updated (+0.042 avg confidence), 2 edges reinforced.\n"
            "\n"
            "# Coding: test suite pass/fail\n"
            "signal = 1.0 if all_tests_passed else 0.0\n"
            "result = await system.outcome(block_ids, signal=signal, source='test_suite')\n"
            "\n"
            "# Writing: engagement rate vs baseline\n"
            "signal = min(engagement_rate / baseline, 1.0)\n"
            "result = await system.outcome(block_ids, signal=signal, source='engagement')\n"
            "\n"
            "# Support: CSAT score 1–5\n"
            "signal = (csat_score - 1.0) / 4.0\n"
            "result = await system.outcome(block_ids, signal=signal, source='csat')"
        ),
    ),
    "guide": AgentGuide(
        name="guide",
        what="Return agent-friendly documentation for a specific method or all methods.",
        when=(
            "Discovering what operations are available, or understanding the correct "
            "usage of a specific method before calling it."
        ),
        when_not="(Always safe to call. No side effects.)",
        cost="Instant. No database access.",
        returns=(
            "str. With no argument: compact overview table. "
            "With a method name: full AgentGuide for that method. "
            "With unknown name: list of valid method names."
        ),
        next="(Informational only.)",
        example=(
            "print(system.guide())           # full overview\n"
            "print(system.guide('learn'))    # detailed guide for learn()\n"
            "print(system.guide('unknown'))  # lists valid method names"
        ),
    ),
}

# ── Overview ──────────────────────────────────────────────────────────────────

OVERVIEW: str = "\n".join([
    "elfmem — adaptive memory for LLM agents",
    "Call system.guide('name') for detailed help on any operation.",
    "",
    "  Operation              Cost         Description",
    "  ─────────────────────────────────────────────────────────────────────",
    "  learn(content, ...)    Instant      Store knowledge for later retrieval",
    "  recall(query, ...)     Fast         Raw retrieval — list of scored blocks",
    "  frame(name, ...)       Fast         Retrieve + render a named context frame",
    "  consolidate()          LLM call     Process inbox: score, embed, promote",
    "  outcome(ids, signal)   Fast         Bayesian confidence update from domain result",
    "  curate()               Fast         Archive stale blocks, prune weak edges",
    "  status()               Fast         System health snapshot + suggested action",
    "  history(last_n=10)     Instant      Recent operations in this process session",
    "  guide(method?)         Instant      This help",
    "",
    "Lifecycle:  session() → learn() → [consolidate()] → frame() / recall() → outcome()",
    "Quick start: system.status() | system.guide('learn') | system.guide('frame')",
])


def get_guide(method_name: str | None = None) -> str:
    """Return documentation string for the named method, or the full overview.

    Args:
        method_name: Method to look up, or None for the full overview.

    Returns:
        Formatted string ready for agent consumption.
    """
    if method_name is None:
        return OVERVIEW
    guide_entry = GUIDES.get(method_name)
    if guide_entry is not None:
        return str(guide_entry)
    valid = ", ".join(f"'{m}'" for m in sorted(GUIDES))
    return f"Unknown method '{method_name}'. Valid methods: {valid}."
