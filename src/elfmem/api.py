"""MemorySystem public API — thin façade over all layers."""

from __future__ import annotations

import json
import os
import time
from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine

from elfmem.adapters.litellm import LiteLLMAdapter, LiteLLMEmbeddingAdapter
from elfmem.config import ElfmemConfig
from elfmem.context.frames import FrameCache, get_frame_definition
from elfmem.db.engine import create_engine
from elfmem.db.models import metadata
from elfmem.db.queries import (
    get_block_counts,
    get_config,
    get_inbox_count,
    get_total_active_hours,
    seed_builtin_data,
    set_config,
)
from elfmem.exceptions import FrameError
from elfmem.guide import get_guide
from elfmem.memory.retrieval import hybrid_retrieve
from elfmem.token_counter import TokenCounter
from elfmem.operations.consolidate import consolidate
from elfmem.operations.curate import curate as _curate
from elfmem.operations.curate import should_curate
from elfmem.operations.learn import learn as _learn
from elfmem.operations.outcome import record_outcome as _record_outcome
from elfmem.operations.recall import recall as _recall
from elfmem.ports.services import EmbeddingService, LLMService
from elfmem.session import (
    begin_session as _begin_session,
)
from elfmem.session import (
    compute_current_active_hours,
)
from elfmem.session import (
    end_session as _end_session,
)
from elfmem.types import (
    ConsolidateResult,
    CurateResult,
    FrameResult,
    LearnResult,
    OperationRecord,
    OutcomeResult,
    ScoredBlock,
    SystemStatus,
    TokenUsage,
)


class MemorySystem:
    """Adaptive memory system for LLM agents.

    Typical usage::

        system = await MemorySystem.from_config("agent.db")
        async with system.session():
            await system.learn("I prefer explicit error handling.")
            result = await system.frame("attention", query="error handling")
            print(result.text)  # inject into your LLM prompt

    Quick reference::

        system.guide()           # overview of all operations
        system.guide("learn")    # detailed guide for a specific method
        await system.status()    # system health + suggested next action
        system.history()         # recent operations in this session
    """

    def __init__(
        self,
        *,
        engine: AsyncEngine,
        llm_service: LLMService,
        embedding_service: EmbeddingService,
        config: ElfmemConfig | None = None,
        token_counter: TokenCounter | None = None,
    ) -> None:
        self._engine = engine
        self._llm = llm_service
        self._embedding = embedding_service
        self._config = config or ElfmemConfig()
        self._frame_cache = FrameCache()
        self._session_id: str | None = None
        self._session_started_at: float | None = None  # monotonic seconds
        self._history: deque[OperationRecord] = deque(maxlen=100)
        self._token_counter = token_counter

    # ── Factory methods ──────────────────────────────────────────────────────

    @classmethod
    async def from_config(
        cls,
        db_path: str,
        config: ElfmemConfig | str | dict[str, Any] | None = None,
    ) -> MemorySystem:
        """Create a MemorySystem from configuration.

        USE WHEN: Primary entry point. Handles all wiring: database, LLM
        adapter, embedding adapter.

        DON'T USE WHEN: You need full control over service injection — use
        the constructor directly with custom LLMService/EmbeddingService.

        COST: Fast. Creates the database file and schema if needed.

        RETURNS: Fully configured MemorySystem, ready for session().

        NEXT: Use ``async with system.session():`` to start interacting.

        Args:
            db_path: Path to SQLite database file (created if not exists).
            config: Configuration source:
                - None: reads ELFMEM_CONFIG env var for YAML path, else defaults
                - str: path to YAML config file
                - dict: inline configuration values (validated by Pydantic)
                - ElfmemConfig: pre-built config object

        Example::

            system = await MemorySystem.from_config("agent.db")
            system = await MemorySystem.from_config("agent.db", "elfmem.yaml")
            system = await MemorySystem.from_config(
                "agent.db",
                {"llm": {"model": "ollama/llama3.2", "base_url": "http://localhost:11434"}}
            )
        """
        cfg = _resolve_config(config)

        engine = await create_engine(db_path)
        async with engine.begin() as conn:
            await conn.run_sync(metadata.create_all)
            await seed_builtin_data(conn)

        # Shared counter: both adapters record to the same object.
        # MemorySystem reads it in status() and manages its lifecycle.
        counter = TokenCounter()

        llm_svc = LiteLLMAdapter(
            model=cfg.llm.model,
            temperature=cfg.llm.temperature,
            max_tokens=cfg.llm.max_tokens,
            timeout=cfg.llm.timeout,
            max_retries=cfg.llm.max_retries,
            base_url=cfg.llm.base_url,
            process_block_model=cfg.llm.process_block_model,
            contradiction_model=cfg.llm.contradiction_model,
            process_block_prompt=cfg.prompts.resolve_process_block(),
            contradiction_prompt=cfg.prompts.resolve_contradiction(),
            valid_self_tags=cfg.prompts.resolve_valid_tags(),
            token_counter=counter,
        )

        embedding_svc = LiteLLMEmbeddingAdapter(
            model=cfg.embeddings.model,
            dimensions=cfg.embeddings.dimensions,
            timeout=cfg.embeddings.timeout,
            base_url=cfg.embeddings.base_url,
            token_counter=counter,
        )

        return cls(
            engine=engine,
            llm_service=llm_svc,
            embedding_service=embedding_svc,
            config=cfg,
            token_counter=counter,
        )

    @classmethod
    async def from_env(cls, db_path: str) -> MemorySystem:
        """Create a MemorySystem from ELFMEM_ environment variables.

        USE WHEN: Deploying in environments where YAML config files are
        not practical (containers, CI, serverless).

        COST: Fast. Reads env vars and delegates to from_config().

        RETURNS: Fully configured MemorySystem.
        """
        cfg = ElfmemConfig.from_env()
        return await cls.from_config(db_path, cfg)

    # ── Agent-friendly introspection ─────────────────────────────────────────

    def guide(self, method_name: str | None = None) -> str:
        """Return agent-friendly documentation for this library or a specific method.

        USE WHEN: Discovering what operations are available, or understanding
        how a specific method should be used before calling it.

        DON'T USE WHEN: (Always safe to call. No side effects.)

        COST: Instant. No database access. Can be called before a session starts.

        RETURNS: str. With no argument: compact overview of all operations.
        With a method name: full guide (what/when/cost/returns/next/example).
        With an unknown name: the list of valid method names.

        NEXT: (Informational only. No action required.)

        Example::

            print(system.guide())           # full overview table
            print(system.guide("learn"))    # detailed guide for learn()
            print(system.guide("unknown"))  # lists valid method names
        """
        return get_guide(method_name)

    async def status(self) -> SystemStatus:
        """Return a snapshot of current system state with a suggested next action.

        USE WHEN: Deciding whether to consolidate, curate, or start a session.
        Checking memory health before a long agent run. Verifying state after
        operations complete.

        DON'T USE WHEN: (Always safe to call. No side effects.)

        COST: Fast. One database read; no LLM calls.

        RETURNS: SystemStatus with inbox_count, inbox_threshold, active_count,
        archived_count, session_active, health ('good'|'attention'), suggestion,
        session_tokens (TokenUsage for this session), and lifetime_tokens
        (TokenUsage all-time). Use result.suggestion for the recommended action.

        NEXT: Follow result.suggestion. If health == 'attention', call
        consolidate() to process a full inbox.

        Example::

            s = await system.status()
            print(s)  # Session: active (0.5h) | Inbox: 8/10 | Active: 42 | Health: good
            if s.health == "attention":
                await system.consolidate()
        """
        async with self._engine.connect() as conn:
            counts = await get_block_counts(conn)
            last_consolidated = await get_config(conn, "last_consolidated_at") or "never"
            total_active_hours = await get_total_active_hours(conn)
            lifetime_tokens = _parse_token_usage(
                await get_config(conn, "lifetime_token_usage")
            )

        session_tokens = (
            self._token_counter.snapshot()
            if self._token_counter is not None
            else TokenUsage()
        )

        session_active = self._session_id is not None
        session_hours: float | None = None
        if session_active and self._session_started_at is not None:
            session_hours = (time.monotonic() - self._session_started_at) / 3600.0

        health, suggestion = _derive_health(
            inbox_count=counts["inbox"],
            inbox_threshold=self._config.memory.inbox_threshold,
            active_count=counts["active"],
        )

        return SystemStatus(
            session_active=session_active,
            session_hours=session_hours,
            inbox_count=counts["inbox"],
            inbox_threshold=self._config.memory.inbox_threshold,
            active_count=counts["active"],
            archived_count=counts["archived"],
            total_active_hours=total_active_hours,
            last_consolidated=last_consolidated,
            health=health,
            suggestion=suggestion,
            session_tokens=session_tokens,
            lifetime_tokens=lifetime_tokens,
        )

    def history(self, last_n: int = 10) -> list[OperationRecord]:
        """Return the most recent operations performed by this MemorySystem.

        USE WHEN: Debugging unexpected results — e.g., recall() returns nothing
        and you want to verify that consolidate() actually ran.

        DON'T USE WHEN: Persistent audit logging is needed — history is
        in-memory only and resets when the process restarts.

        COST: Instant. In-memory only; no database access.

        RETURNS: list[OperationRecord] (operation, summary, timestamp), most
        recent last. Empty list if no operations have run yet.

        NEXT: (Informational only. No action required.)

        Example::

            for record in system.history(last_n=5):
                print(record)  # learn()  →  Stored block a1b2.  [14:32:01]
        """
        records = list(self._history)
        return records[-last_n:] if last_n < len(records) else records

    # ── Session management ───────────────────────────────────────────────────

    @asynccontextmanager
    async def session(
        self,
        task_type: str = "general",
    ) -> AsyncIterator[MemorySystem]:
        """Async context manager that wraps a single interaction session.

        USE WHEN: Starting an agent interaction loop. Handles session lifecycle
        and auto-consolidation automatically.

        COST: Fast on entry. May trigger consolidate() on exit if inbox is full.

        RETURNS: Yields self. Use as ``async with system.session() as mem:``.

        NEXT: Call learn(), frame(), recall() inside the context.

        On entry: begins session (starts active-hours clock).
        On exit: runs consolidate() if inbox threshold reached, then ends session.

        Usage::

            async with system.session():
                await system.learn("...")
                result = await system.frame("attention", query="...")
        """
        await self.begin_session(task_type=task_type)
        try:
            yield self
        finally:
            async with self._engine.begin() as conn:
                count = await get_inbox_count(conn)
                if count >= self._config.memory.inbox_threshold:
                    await self.consolidate()
            await self.end_session()
            self._frame_cache.clear()

    async def begin_session(self, task_type: str = "general") -> str:
        """Start a session explicitly. Returns the session_id.

        USE WHEN: You need manual session control outside a context manager.

        DON'T USE WHEN: Using ``async with system.session():`` — that handles
        this automatically.

        COST: Fast.

        RETURNS: str session_id. If a session is already active, returns the
        existing session_id without starting a new one (idempotent).
        """
        if self._session_id is not None:
            # Already active — idempotent; do NOT reset the token counter
            return self._session_id

        async with self._engine.begin() as conn:
            session_id = await _begin_session(conn, task_type=task_type)
        self._session_id = session_id
        self._session_started_at = time.monotonic()
        # Fresh token slate for every new session
        if self._token_counter is not None:
            self._token_counter.reset()
        self._record_op("begin_session", f"Session {session_id[:8]} started.")
        return session_id

    async def end_session(self) -> float:
        """End the current session. Returns session duration in active hours.

        USE WHEN: Paired with begin_session() for manual session control.

        DON'T USE WHEN: Using ``async with system.session():`` — handled
        automatically.

        COST: Fast.

        RETURNS: float — session duration in active hours. Returns 0.0 if no
        session was active (safe to call redundantly).
        """
        if self._session_id is None:
            return 0.0
        # Capture and zero the counter before the DB transaction — prevents
        # any tokens from being double-counted if begin_session follows soon.
        session_usage = (
            self._token_counter.reset() if self._token_counter is not None else None
        )
        async with self._engine.begin() as conn:
            duration = await _end_session(conn, self._session_id)
            if session_usage is not None:
                raw = await get_config(conn, "lifetime_token_usage")
                lifetime = _parse_token_usage(raw)
                updated = lifetime + session_usage
                await set_config(
                    conn, "lifetime_token_usage", json.dumps(updated.to_dict())
                )
        self._session_id = None
        self._session_started_at = None
        self._record_op("end_session", f"Session ended ({duration:.2f}h active).")
        return duration

    # ── Public operations ────────────────────────────────────────────────────

    async def learn(
        self,
        content: str,
        tags: list[str] | None = None,
        *,
        category: str = "knowledge",
        source: str = "api",
    ) -> LearnResult:
        """Store a knowledge block for future retrieval.

        USE WHEN: The agent discovers a fact, preference, decision, or
        observation worth remembering across sessions.

        DON'T USE WHEN: Information is transient (current turn only) or
        already present in the active prompt context.

        COST: Instant. No LLM calls.

        RETURNS: LearnResult. status values:
          'created'                   — new block stored in inbox
          'duplicate_rejected'        — exact content already exists
          'near_duplicate_superseded' — similar block replaced

        NEXT: Blocks queue in inbox until consolidate() runs. The session()
        context manager auto-consolidates on exit when inbox >= threshold.

        Args:
            content: Text content to store. Be specific and self-contained —
                     this text is what gets retrieved later.
            tags: Optional initial tags. Self-aligned tags are inferred
                  during consolidate().
            category: Block category (e.g. "knowledge", "preference",
                      "decision", "observation").
            source: Source label for provenance (e.g. "api", "tool_result").
        """
        async with self._engine.begin() as conn:
            result = await _learn(
                conn, content=content, tags=tags, category=category, source=source
            )
        self._record_op("learn", result.summary)
        return result

    async def consolidate(self) -> ConsolidateResult:
        """Process inbox blocks: score, embed, deduplicate, and promote to active memory.

        USE WHEN: After a batch of learn() calls, or explicitly before
        recall/frame when you know new blocks are in the inbox. The session()
        context manager handles this automatically.

        DON'T USE WHEN: Inbox is empty — safe to call but returns zero counts.
        Avoid calling in a tight loop; one call processes all pending blocks.

        COST: LLM call per block (alignment scoring + tag inference). Slow for
        large inboxes; fast when inbox is small.

        RETURNS: ConsolidateResult with counts: processed, promoted,
        deduplicated, edges_created. processed=0 means inbox was empty.

        NEXT: Promoted blocks are now searchable via frame() and recall().
        Also triggers curate() automatically if curate_interval has elapsed.
        """
        current_hours = compute_current_active_hours()
        mem = self._config.memory

        async with self._engine.begin() as conn:
            result = await consolidate(
                conn,
                llm=self._llm,
                embedding_svc=self._embedding,
                current_active_hours=current_hours,
                self_alignment_threshold=mem.self_alignment_threshold,
                similarity_edge_threshold=mem.similarity_edge_threshold,
                edge_degree_cap=mem.edge_degree_cap,
                contradiction_similarity_prefilter=mem.contradiction_similarity_prefilter,
            )

            if await should_curate(
                conn,
                current_hours,
                curate_interval_hours=mem.curate_interval_hours,
            ):
                await _curate(
                    conn,
                    current_active_hours=current_hours,
                    prune_threshold=mem.prune_threshold,
                    edge_prune_threshold=mem.edge_prune_threshold,
                    reinforce_top_n=mem.curate_reinforce_top_n,
                )

            # Record consolidation timestamp for status() reporting
            await set_config(conn, "last_consolidated_at", datetime.now(UTC).isoformat())

        self._record_op("consolidate", result.summary)
        return result

    async def frame(
        self,
        name: str,
        query: str | None = None,
        *,
        top_k: int | None = None,
    ) -> FrameResult:
        """Retrieve and render context for the named frame, ready for prompt injection.

        USE WHEN: Assembling context for an LLM prompt. Use 'self' for identity
        context, 'attention' for query-relevant knowledge, 'task' for goals.

        DON'T USE WHEN: You only need raw block data without rendering — use
        recall() instead.

        COST: Fast. Embedding call if query provided; no LLM calls.

        RETURNS: FrameResult. Use result.text for direct prompt injection.
        result.blocks are the scored candidates. result.cached indicates a TTL
        cache hit (no retrieval occurred).

        NEXT: Inject result.text into your LLM prompt.

        Args:
            name: Frame name — 'self', 'attention', or 'task'.
            query: Query text. Required for ATTENTION; optional for TASK;
                   not used for SELF (identity context is queryless).
            top_k: Number of blocks to return. Defaults to config.memory.top_k.

        Raises:
            FrameError: If name is not a valid frame. Recovery hint included.
        """
        try:
            frame_def = get_frame_definition(name)
        except ValueError as exc:
            raise FrameError(
                str(exc),
                recovery="Valid frames: 'self', 'attention', 'task'.",
            ) from exc

        k = top_k if top_k is not None else self._config.memory.top_k
        current_hours = compute_current_active_hours()

        async with self._engine.begin() as conn:
            result = await _recall(
                conn,
                embedding_svc=self._embedding,
                frame_def=frame_def,
                query=query,
                current_active_hours=current_hours,
                top_k=k,
                cache=self._frame_cache,
            )
        self._record_op("frame", result.summary)
        return result

    async def recall(
        self,
        query: str | None = None,
        *,
        top_k: int | None = None,
        frame: str = "attention",
    ) -> list[ScoredBlock]:
        """Raw retrieval without rendering. No reinforcement side effects.

        USE WHEN: Inspecting what is in memory, debugging retrieval quality,
        or building custom rendering from scored block data.

        DON'T USE WHEN: You need context ready for prompt injection — use
        frame() instead. frame() renders, respects token budgets, and caches.

        COST: Fast. Embedding call if query provided; no LLM calls.

        RETURNS: list[ScoredBlock] sorted by composite score descending.
        Empty list if nothing found — never raises for empty results.

        NEXT: No side effects. Safe to call multiple times.

        Args:
            query: Query text (None for queryless retrieval).
            top_k: Number of blocks to return.
            frame: Frame name for weights/filters. Default "attention".

        Raises:
            FrameError: If frame is not a valid frame name.
        """
        try:
            frame_def = get_frame_definition(frame)
        except ValueError as exc:
            raise FrameError(
                str(exc),
                recovery="Valid frames: 'self', 'attention', 'task'.",
            ) from exc

        k = top_k if top_k is not None else self._config.memory.top_k
        current_hours = compute_current_active_hours()

        if query is None:
            weights = frame_def.weights.renormalized_without_similarity()
        else:
            weights = frame_def.weights

        tag_filter: str | None = None
        if frame_def.filters.tag_patterns:
            tag_filter = frame_def.filters.tag_patterns[0]

        async with self._engine.begin() as conn:
            blocks = await hybrid_retrieve(
                conn,
                embedding_svc=self._embedding,
                query=query,
                weights=weights,
                current_active_hours=current_hours,
                top_k=k,
                tag_filter=tag_filter,
                search_window_hours=frame_def.filters.search_window_hours,
            )

        count = len(blocks)
        noun = "block" if count == 1 else "blocks"
        self._record_op("recall", f"recall({frame!r}) → {count} {noun} returned.")
        return blocks

    async def outcome(
        self,
        block_ids: list[str],
        signal: float,
        *,
        weight: float = 1.0,
        source: str = "",
    ) -> OutcomeResult:
        """Update block confidence from a normalised domain outcome signal.

        USE WHEN: A measurable result is available — a forecast resolved,
        tests passed or failed, content engagement was measured, a CSAT
        score arrived. Converts domain metrics to elfmem confidence updates.

        DON'T USE WHEN: Reinforcing recently-used blocks — frame() handles
        that automatically. Don't call for transient observations; only for
        outcomes that reflect whether retrieved knowledge was correct/useful.

        COST: Fast. Database operations only; no LLM calls. Does not require
        an active session — outcomes can arrive weeks after retrieval.

        RETURNS: OutcomeResult with: blocks_updated (active blocks changed),
        mean_confidence_delta (average shift, positive or negative),
        edges_reinforced (graph edges strengthened for positive signals),
        blocks_penalized (blocks whose decay was accelerated for low signals).
        blocks_updated=0 means all block_ids were non-active (silently skipped).

        NEXT: Positive signal → confidence grows + blocks resist decay.
        Negative signal → confidence falls. Below penalize_threshold (default
        0.20), blocks also have decay_lambda accelerated automatically.
        After ~10 outcomes, evidence dominates the LLM alignment prior.

        Signal spectrum::

            0.8–1.0  → confidence UP + reinforce (decay resets)
            0.2–0.8  → confidence adjusted, no reinforcement or penalization
            0.0–0.2  → confidence DOWN + decay accelerated automatically

        Domain signal normalisation (one-liners in agent code)::

            signal = 1.0 - brier_score           # trading: 0=worst, 1=perfect
            signal = 1.0 if tests_passed else 0.0  # coding
            signal = min(engagement / baseline, 1.0)  # writing
            signal = (csat_score - 1.0) / 4.0    # support (1–5 scale)

        Args:
            block_ids: Block IDs that contributed to the outcome (from recall/frame).
            signal: Normalised quality signal in [0.0, 1.0]. Raises ValueError if outside.
            weight: Observation weight (> 0.0). Higher = faster convergence.
                    Use weight > 1.0 for high-stakes outcomes.
            source: Audit label (e.g. "brier", "test_suite", "csat", "engagement").

        Raises:
            ValueError: If signal is outside [0.0, 1.0] or weight <= 0.0.

        Example::

            # Trading: Brier score resolves after 30 days
            block_ids = [b.id for b in await system.recall("EUR/USD forecast")]
            signal = 1.0 - brier_score
            result = await system.outcome(block_ids, signal=signal, source="brier")
            print(result)  # Outcome recorded: 3 blocks updated (+0.042 avg confidence), 2 edges reinforced.
        """
        current_hours = compute_current_active_hours()
        mem = self._config.memory
        async with self._engine.begin() as conn:
            result = await _record_outcome(
                conn,
                block_ids=block_ids,
                signal=signal,
                weight=weight,
                source=source,
                current_active_hours=current_hours,
                prior_strength=mem.outcome_prior_strength,
                reinforce_threshold=mem.outcome_reinforce_threshold,
                penalize_threshold=mem.penalize_threshold,
                penalty_factor=mem.penalty_factor,
                lambda_ceiling=mem.lambda_ceiling,
            )
        self._record_op("outcome", result.summary)
        return result

    async def curate(self) -> CurateResult:
        """Archive decayed blocks, prune weak edges, reinforce top-N knowledge.

        USE WHEN: Explicit maintenance after heavy use, or when retrieval
        quality degrades. Also runs automatically after consolidate() when
        curate_interval_hours has elapsed.

        DON'T USE WHEN: Immediately after consolidate() — auto-curate already
        triggers if the interval elapsed. Don't call after every session.

        COST: Fast. Database operations only; no LLM calls.

        RETURNS: CurateResult with counts: archived (decayed blocks removed),
        edges_pruned (weak edges removed), reinforced (top-N blocks boosted).
        All zeros means memory was already clean.

        NEXT: Memory is cleaner. Retrieval quality may improve.
        """
        current_hours = compute_current_active_hours()
        mem = self._config.memory
        async with self._engine.begin() as conn:
            result = await _curate(
                conn,
                current_active_hours=current_hours,
                prune_threshold=mem.prune_threshold,
                edge_prune_threshold=mem.edge_prune_threshold,
                reinforce_top_n=mem.curate_reinforce_top_n,
            )
        self._record_op("curate", result.summary)
        return result

    async def close(self) -> None:
        """Dispose the database engine. Call when done with this MemorySystem.

        USE WHEN: Shutting down. Releases the SQLite connection pool.

        COST: Fast.
        """
        await self._engine.dispose()

    # ── Private helpers ──────────────────────────────────────────────────────

    def _record_op(self, operation: str, summary: str) -> None:
        """Append an operation record to the in-memory history deque."""
        self._history.append(
            OperationRecord(
                operation=operation,
                summary=summary,
                timestamp=datetime.now(UTC).isoformat(),
            )
        )


# ── Module-level helpers ───────────────────────────────────────────────────────

def _derive_health(
    inbox_count: int,
    inbox_threshold: int,
    active_count: int,
) -> tuple[str, str]:
    """Derive health level and suggestion from current system state.

    Returns:
        Tuple of (health: str, suggestion: str).
        health is 'good' or 'attention'.
    """
    fill_ratio = inbox_count / max(inbox_threshold, 1)
    if fill_ratio >= 1.0:
        return "attention", "Inbox full. Call consolidate() to process pending blocks."
    if fill_ratio >= 0.8:
        return (
            "good",
            f"Inbox nearly full ({inbox_count}/{inbox_threshold}). Consolidation approaching.",
        )
    if active_count == 0 and inbox_count == 0:
        return "good", "Memory is empty. Seed your identity: elfmem init --self '...'"
    return "good", "Memory healthy. No action required."


def _resolve_config(
    config: ElfmemConfig | str | dict[str, Any] | None,
) -> ElfmemConfig:
    """Resolve config argument to an ElfmemConfig instance."""
    if config is None:
        config_path = os.getenv("ELFMEM_CONFIG")
        return ElfmemConfig.from_yaml(config_path) if config_path else ElfmemConfig()
    if isinstance(config, str):
        return ElfmemConfig.from_yaml(config)
    if isinstance(config, dict):
        return ElfmemConfig.model_validate(config)
    return config


_TOKEN_USAGE_FIELDS = frozenset(
    {"llm_input_tokens", "llm_output_tokens", "embedding_tokens", "llm_calls", "embedding_calls"}
)


def _parse_token_usage(raw: str | None) -> TokenUsage:
    """Safely deserialise a TokenUsage from a JSON string stored in system_config.

    Returns ``TokenUsage()`` (all zeros) on any error — missing key, corrupted
    JSON, or unexpected schema all degrade gracefully rather than raising.
    """
    if not raw:
        return TokenUsage()
    try:
        data = json.loads(raw)
        return TokenUsage(**{k: int(data.get(k, 0)) for k in _TOKEN_USAGE_FIELDS})
    except (json.JSONDecodeError, TypeError, ValueError, KeyError):
        return TokenUsage()
