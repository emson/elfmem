#!/usr/bin/env python3
"""Seed elfmem with project DNA for team prompt guidance.

Extracts transferable patterns from docs into elfmem blocks so that
team agents (lead-dev, dev, testing) can recall project conventions,
coding principles, and architectural decisions before acting.

Usage:
    uv run python scripts/seed_team_memory.py

Each block is tagged hierarchically so team agents can recall by role:
    elfmem_recall("coding patterns for async operations", frame="self")
    elfmem_recall("testing approach for consolidation", frame="task")
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Add src to path so we can import elfmem
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from elfmem import ElfmemConfig, MemorySystem


# ---------------------------------------------------------------------------
# Block definitions: each is (content, tags)
# ---------------------------------------------------------------------------

TEAM_IDENTITY = [
    (
        "We are a professional Python open source team building elfmem, "
        "an adaptive memory library for LLM agents. Our philosophy: "
        "SIMPLE, ELEGANT, FLEXIBLE, ROBUST. We write functional-style Python "
        "that is easy for both humans and LLMs to understand, modify, and debug.",
        ["team/identity", "team/philosophy"],
    ),
    (
        "Three team roles work together: "
        "lead-dev-team (Opus) writes specs and plans in docs/plans/, "
        "guides architecture decisions, finds edge cases. "
        "dev-team (Sonnet) implements production-grade code, refactors for DRY elegance. "
        "testing-team (Haiku) writes focused tests before implementation, "
        "follows testing_principles.md, errs on fewer but better tests.",
        ["team/structure", "team/roles"],
    ),
]

CODING_PRINCIPLES = [
    (
        "Functional Python: pure functions, input → output, no side effects, "
        "no mutation. Compose pipelines from small functions (≤50 lines each). "
        "If a function exceeds 50 lines, extract subfunctions.",
        ["team/coding", "pattern/functional", "pattern/composition"],
    ),
    (
        "Fail fast: let exceptions bubble up. Business logic has NO try/catch. "
        "Catch only at CLI/MCP system boundaries. "
        "No defensive code — no broad 'except', no try/except in business logic.",
        ["team/coding", "pattern/error-handling"],
    ),
    (
        "Early returns with guard clauses, never nested if/else. "
        "Transform data — return new objects, don't mutate in place.",
        ["team/coding", "pattern/control-flow"],
    ),
    (
        "Complete type hints on every function, public and private. "
        "Constants for magic numbers. Descriptive names that reveal intent — "
        "never cryptic abbreviations.",
        ["team/coding", "pattern/readability"],
    ),
    (
        "Comments explain WHY, not WHAT. "
        "Consistent patterns: same problem = same solution across the codebase. "
        "If an agent can predict how the next function will look, the code is right.",
        ["team/coding", "pattern/consistency"],
    ),
]

TESTING_PRINCIPLES = [
    (
        "Test high-level behavior, not implementation details. "
        "Test public APIs and interfaces. Never test private methods "
        "or third-party library internals.",
        ["team/testing", "pattern/test-scope"],
    ),
    (
        "Follow Arrange-Act-Assert pattern. One assertion per test. "
        "Clear test names that describe the scenario. "
        "Each test is self-contained with no dependencies between tests.",
        ["team/testing", "pattern/test-structure"],
    ),
    (
        "Fewer, better tests. Focus on critical paths and real use cases. "
        "Avoid testing theoretical or unlikely scenarios. "
        "Integration tests over many unit tests.",
        ["team/testing", "pattern/test-quality"],
    ),
    (
        "Tests always use MockLLMService and MockEmbeddingService — "
        "never real API calls. In-memory SQLite with StaticPool for test isolation. "
        "Async/await with pytest-asyncio fixtures.",
        ["team/testing", "pattern/test-infrastructure"],
    ),
    (
        "Float comparison tolerance: 0.001. "
        "Use shared fixtures from tests/conftest.py. "
        "Don't make tests brittle — avoid exact string matches, test error types instead.",
        ["team/testing", "pattern/test-robustness"],
    ),
]

AGENT_FRIENDLY_CONTRACT = [
    (
        "Agent-first contract: all operations return typed result objects with "
        "__str__ (agent summary), summary property, and to_dict() (JSON). "
        "Never return raw dicts, lists, or None.",
        ["team/api-contract", "pattern/agent-friendly"],
    ),
    (
        "All exceptions carry a .recovery field — the exact code/command to fix "
        "the problem. Never raise generic Exception or RuntimeError. "
        "Never blame the agent — focus on what to do.",
        ["team/api-contract", "pattern/error-recovery"],
    ),
    (
        "Idempotent operations: duplicate learn() → graceful reject. "
        "Empty consolidate() → zero counts, not error. "
        "Redundant close() → no-op. Order independence where possible.",
        ["team/api-contract", "pattern/idempotency"],
    ),
    (
        "Progressive disclosure: Tier 1 (zero config, zero ceremony) must always work. "
        "Tier 2 adds explicit lifecycle. Tier 3 gives full control. "
        "Never require Tier 3 knowledge for Tier 1 workflows.",
        ["team/api-contract", "pattern/progressive-disclosure"],
    ),
    (
        "Docstrings follow the 5-field template: "
        "USE WHEN, DON'T USE WHEN, COST, RETURNS, NEXT. "
        "These map to the agent's decision loop: "
        "should I call it? am I misusing it? is it slow? what do I do with the result?",
        ["team/api-contract", "pattern/docstrings"],
    ),
    (
        "String-first returns: __str__ leads with what happened (past-tense verb), "
        "includes actionable context (counts, thresholds, state), "
        "suggests next action when relevant. Fits on one or two lines. "
        "Use | to separate independent status facts.",
        ["team/api-contract", "pattern/string-returns"],
    ),
]

ARCHITECTURE_DECISIONS = [
    (
        "Three rhythms drive all design decisions: "
        "Heartbeat (learn: milliseconds, no LLM, pure inbox insert), "
        "Breathing (dream/consolidate: seconds, LLM-powered dedup + contradiction detection), "
        "Sleep (curate: minutes, decay archival + graph pruning + top-K reinforcement).",
        ["team/architecture", "pattern/rhythms"],
    ),
    (
        "Five frames for retrieval — always select before retrieving: "
        "SELF (identity), ATTENTION (query-driven), TASK (goals), "
        "WORLD (context), SHORT_TERM (recent). "
        "Frame selection dominates retrieval quality (~50% improvement).",
        ["team/architecture", "pattern/frames"],
    ),
    (
        "Knowledge lifecycle: BIRTH → GROWTH → MATURITY → DECAY → ARCHIVE. "
        "Decay is session-aware (holidays don't kill knowledge). "
        "Reinforcement resets the decay clock. "
        "Constitutional blocks have permanent decay (~7.9 year half-life).",
        ["team/architecture", "pattern/lifecycle"],
    ),
    (
        "4-stage hybrid retrieval pipeline: "
        "pre-filter → vector search → graph expansion → composite scoring. "
        "Graph expansion recovers related-but-not-similar context. "
        "Retrieval is pure; reinforcement is a separate L4 operation.",
        ["team/architecture", "pattern/retrieval"],
    ),
    (
        "LLM infrastructure: LiteLLMAdapter + LiteLLMEmbeddingAdapter for production, "
        "wired by MemorySystem.from_config(). "
        "ElfmemConfig via YAML / env vars / dict / None (sensible defaults). "
        "Fully configurable prompts and per-call-type model overrides.",
        ["team/architecture", "pattern/llm-infra"],
    ),
    (
        "Key paths: src/elfmem/api.py (MemorySystem — all public operations), "
        "src/elfmem/types.py (result types, exceptions), "
        "src/elfmem/operations/ (learn, consolidate, curate, outcome, recall), "
        "src/elfmem/adapters/mock.py (MockLLMService, MockEmbeddingService), "
        "tests/conftest.py (shared test fixtures).",
        ["team/architecture", "reference/paths"],
    ),
]

WORKFLOW_PATTERNS = [
    (
        "Lead-dev workflow: read requirements → study docs → write plan in docs/plans/ → "
        "specify edge cases and mitigations → define API surface → "
        "hand off to testing-team for test creation, then dev-team for implementation.",
        ["team/workflow", "role/lead-dev"],
    ),
    (
        "Dev-team workflow: read plan from docs/plans/ → ask questions → "
        "wait for tests from testing-team → implement to pass tests → "
        "refactor for DRY elegance → ensure all tests pass → submit for review.",
        ["team/workflow", "role/dev"],
    ),
    (
        "Testing-team workflow: read plan from docs/plans/ → write focused tests "
        "following testing_principles.md → fewer but better tests → "
        "cover critical paths and edge cases → inform dev-team when tests are ready.",
        ["team/workflow", "role/testing"],
    ),
    (
        "Plan file convention: docs/plans/plan_<feature_name>.md — "
        "contains requirements, API surface, edge cases, mitigations, "
        "implementation steps. Plans are the single source of truth for a feature.",
        ["team/workflow", "pattern/planning"],
    ),
]

FEEDBACK_LOOP = [
    (
        "Team feedback loop: plan → implement → test → measure → learn → improve. "
        "After each feature: what patterns worked? what broke? what surprised us? "
        "Remember patterns, not events. Reinforce what works, let bad patterns decay.",
        ["team/learning", "pattern/feedback-loop"],
    ),
    (
        "Remember only: surprising outcomes, generalizable patterns, "
        "unexpected failures and successes, learnings from resolved conflicts. "
        "NOT every observation or event.",
        ["team/learning", "pattern/what-to-remember"],
    ),
    (
        "Signal quality matters: tight feedback (tests pass/fail) = weight 1.0. "
        "Loose feedback (user reports days later) = weight 0.5. "
        "Batch related outcomes before signaling. "
        "Penalize confident errors more harshly.",
        ["team/learning", "pattern/signal-quality"],
    ),
]


ALL_BLOCKS = (
    TEAM_IDENTITY
    + CODING_PRINCIPLES
    + TESTING_PRINCIPLES
    + AGENT_FRIENDLY_CONTRACT
    + WORKFLOW_PATTERNS
    + ARCHITECTURE_DECISIONS
    + FEEDBACK_LOOP
)


async def seed() -> None:
    """Seed elfmem with all team knowledge blocks."""
    system = await MemorySystem.from_config(
        db_path=str(Path.home() / ".elfmem" / "default.db"),
    )

    async with system.session():
        # First: setup constitutional identity
        print("Setting up constitutional identity...")
        setup_result = await system.setup(
            identity=(
                "I am elf — an adaptive memory system for LLM agents. "
                "I help teams build with consistent patterns, learn from outcomes, "
                "and self-improve through biological-style memory: "
                "fast ingestion, deep consolidation, decay-based archival."
            ),
            values=[
                "Simple, elegant, flexible, robust",
                "Functional Python — pure functions, composable pipelines",
                "Agent-first — every API serves the agent's one-shot loop",
                "Fail fast — exceptions are visible, recovery is actionable",
                "Test behavior, not implementation",
            ],
        )
        print(f"  {setup_result}")

        # Then: seed all knowledge blocks
        print(f"\nSeeding {len(ALL_BLOCKS)} team knowledge blocks...")
        created = 0
        duplicates = 0

        for content, tags in ALL_BLOCKS:
            result = await system.learn(content, tags=tags)
            status = str(result)
            if "duplicate" in status.lower():
                duplicates += 1
            else:
                created += 1
            print(f"  [{created + duplicates:02d}] {status}")

        print(f"\nDone: {created} created, {duplicates} duplicates skipped.")

        # Consolidate to embed and build graph
        print("\nConsolidating (embedding + graph building)...")
        consolidate_result = await system.consolidate()
        print(f"  {consolidate_result}")

    print("\nTeam memory seeded. Agents can now recall project conventions:")
    print('  elfmem_recall("coding patterns", frame="self")')
    print('  elfmem_recall("testing approach for recall", frame="task")')
    print('  elfmem_recall("architecture decisions", frame="attention")')


if __name__ == "__main__":
    asyncio.run(seed())
