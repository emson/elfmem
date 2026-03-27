"""MemorySystem public API — thin façade over all layers."""

from __future__ import annotations

import json
import os
import time
from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine

from elfmem.adapters.factory import make_embedding_adapter, make_llm_adapter
from elfmem.config import ElfmemConfig
from elfmem.context.frames import FrameCache, get_frame_definition
from elfmem.db.engine import create_engine
from elfmem.db.models import metadata
from elfmem.db.queries import (
    get_block_counts,
    get_config,
    get_inbox_count,
    get_total_active_hours,
    load_co_retrieval_staging,
    prune_stale_co_retrieval_staging,
    seed_builtin_data,
    set_config,
)
from elfmem.exceptions import ElfmemError, FrameError
from elfmem.guide import get_guide
from elfmem.memory.graph import stage_and_promote_co_retrievals
from elfmem.memory.retrieval import hybrid_retrieve
from elfmem.operations.connect import do_connect, do_disconnect
from elfmem.operations.consolidate import consolidate
from elfmem.operations.curate import curate as _curate
from elfmem.operations.curate import should_curate
from elfmem.operations.learn import learn as _learn
from elfmem.operations.outcome import record_outcome as _record_outcome
from elfmem.operations.recall import recall as _recall
from elfmem.policy import ConsolidationPolicy
from elfmem.ports.services import EmbeddingService, LLMService
from elfmem.session import begin_session as _begin_session
from elfmem.session import end_session as _end_session
from elfmem.token_counter import TokenCounter
from elfmem.types import (
    ConnectByQueryResult,
    ConnectResult,
    ConnectSpec,
    ConnectsResult,
    ConsolidateResult,
    CurateResult,
    DisconnectResult,
    FrameResult,
    LearnResult,
    OperationRecord,
    OutcomeResult,
    ScoredBlock,
    SetupResult,
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
        policy: ConsolidationPolicy | None = None,
        initial_pending: int = 0,
        initial_co_retrieval_staging: dict[tuple[str, str], int] | None = None,
    ) -> None:
        self._engine = engine
        self._llm = llm_service
        self._embedding = embedding_service
        self._config = config or ElfmemConfig()
        self._frame_cache = FrameCache()
        self._session_id: str | None = None
        self._session_started_at: float | None = None  # monotonic seconds
        self._session_base_hours: float = 0.0  # total_active_hours at session start
        self._history: deque[OperationRecord] = deque(maxlen=100)
        self._token_counter = token_counter
        self._policy: ConsolidationPolicy | None = policy
        # In-memory advisory counter: blocks currently awaiting consolidation.
        # Seeded from DB inbox_count in from_config(); incremented by learn()/remember().
        # Reset to 0 by consolidate()/dream(). Advisory only — use status() for DB accuracy.
        self._pending: int = max(0, initial_pending)
        # Session breadcrumbs — in-memory only, reset on genuinely new begin_session().
        self._last_learned_block_id: str | None = None
        self._last_recall_block_ids: list[str] = []
        self._session_block_ids: list[str] = []
        # Hebbian co-retrieval staging — accumulates across sessions, never reset.
        # Maps canonical (from_id, to_id) → co-retrieval count without existing edge.
        # Promotes to permanent co_occurs edge at co_retrieval_edge_threshold.
        # Persisted to DB (co_retrieval_staging table) and restored on startup via
        # from_config(). FK CASCADE on blocks keeps the table self-consistent.
        self._co_retrieval_staging: dict[tuple[str, str], int] = (
            initial_co_retrieval_staging or {}
        )
        # Per-session dedup set — cleared on begin_session() so each pair
        # contributes at most 1 count per session. Makes threshold semantically
        # mean "N distinct sessions" not "N calls in one session."
        self._co_retrieval_session_seen: set[tuple[str, str]] = set()

    # ── Factory methods ──────────────────────────────────────────────────────

    @classmethod
    async def from_config(
        cls,
        db_path: str,
        config: ElfmemConfig | str | dict[str, Any] | None = None,
        *,
        policy: ConsolidationPolicy | None = None,
    ) -> MemorySystem:
        """Create a MemorySystem from configuration.

        USE WHEN: Primary entry point. Handles all wiring: database, LLM
        adapter, embedding adapter.

        DON'T USE WHEN: You need full control over service injection — use
        the constructor directly with custom LLMService/EmbeddingService.

        COST: Fast. Creates the database file and schema if needed.

        RETURNS: Fully configured MemorySystem, ready for session().

        NEXT: Use ``async with system.session():`` to start interacting.
        For always-on agents: call remember() and check should_dream.

        Args:
            db_path: Path to SQLite database file (created if not exists).
            config: Configuration source:
                - None: reads ELFMEM_CONFIG env var for YAML path, else defaults
                - str: path to YAML config file
                - dict: inline configuration values (validated by Pydantic)
                - ElfmemConfig: pre-built config object
            policy: Optional ConsolidationPolicy for adaptive consolidation
                timing. When set, should_dream and dream() defer to the policy
                rather than the fixed inbox_threshold.

        Example::

            system = await MemorySystem.from_config("agent.db")
            system = await MemorySystem.from_config("agent.db", "elfmem.yaml")
            system = await MemorySystem.from_config(
                "agent.db",
                {"llm": {"model": "ollama/llama3.2", "base_url": "http://localhost:11434"}}
            )
            # With adaptive consolidation policy:
            from elfmem import ConsolidationPolicy
            system = await MemorySystem.from_config("agent.db", policy=ConsolidationPolicy())
        """
        cfg = _resolve_config(config)

        engine = await create_engine(db_path)
        async with engine.begin() as conn:
            await conn.run_sync(metadata.create_all)
            await seed_builtin_data(conn)
            # Seed _pending from DB so the advisory is accurate on restart.
            # Blocks that survived a crash or process restart are counted.
            initial_pending = await get_inbox_count(conn)
            # Restore Hebbian staging so multi-session counts survive process restarts.
            # FK CASCADE on blocks.id ensures stale rows are auto-cleaned on archival.
            co_retrieval_staging = await load_co_retrieval_staging(conn)
            # Restore persisted policy threshold so adaptive learning continues
            # across restarts. Clamped to [min, max] by restore_threshold().
            if policy is not None:
                stored = await get_config(conn, "consolidation_policy_threshold")
                if stored is not None:
                    with suppress(ValueError, TypeError):
                        policy.restore_threshold(int(stored))

        # Shared counter: both adapters record to the same object.
        # MemorySystem reads it in status() and manages its lifecycle.
        counter = TokenCounter()

        llm_svc = make_llm_adapter(cfg, counter)
        embedding_svc = make_embedding_adapter(cfg, counter)

        return cls(
            engine=engine,
            llm_service=llm_svc,
            embedding_service=embedding_svc,
            config=cfg,
            token_counter=counter,
            policy=policy,
            initial_pending=initial_pending,
            initial_co_retrieval_staging=co_retrieval_staging,
        )

    @classmethod
    async def from_env(
        cls, db_path: str, *, policy: ConsolidationPolicy | None = None
    ) -> MemorySystem:
        """Create a MemorySystem from ELFMEM_ environment variables.

        USE WHEN: Deploying in environments where YAML config files are
        not practical (containers, CI, serverless).

        COST: Fast. Reads env vars and delegates to from_config().

        RETURNS: Fully configured MemorySystem.
        """
        cfg = ElfmemConfig.from_env()
        return await cls.from_config(db_path, cfg, policy=policy)

    @classmethod
    @asynccontextmanager
    async def managed(
        cls,
        db_path: str,
        config: ElfmemConfig | str | dict[str, Any] | None = None,
        *,
        policy: ConsolidationPolicy | None = None,
    ) -> AsyncIterator[MemorySystem]:
        """Full lifecycle context manager: open → session → yield → dream → close.

        USE WHEN: Scripts, CLI commands, and short-lived agents that need a
        complete open-and-close lifecycle in one block. Starts a session on
        entry so active-hours tracking and frame scoring are always correct.
        Consolidates any pending blocks before closing (safety net).

        DON'T USE WHEN: Long-running processes that reuse the same
        MemorySystem across many requests — call from_config() once, then
        use begin_session()/end_session() or session() as needed.

        COST: from_config() on entry (fast). dream() on exit only if pending.

        NEXT: After the block exits, the engine is disposed and all DB
        connections are closed.

        Example::

            async with MemorySystem.managed("agent.db") as mem:
                result = await mem.remember("User prefers dark mode")
                if mem.should_dream:
                    await mem.dream()
                ctx = await mem.frame("attention", query="preferences")
        """
        mem = await cls.from_config(db_path, config, policy=policy)
        await mem.begin_session()
        try:
            yield mem
        finally:
            # Safety net: consolidate any pending blocks before closing.
            if mem.should_dream:
                await mem.dream()
            await mem.end_session()
            await mem.close()

    # ── Session breadcrumbs ──────────────────────────────────────────────────

    @property
    def last_learned_block_id(self) -> str | None:
        """Block ID from the most recent learn() or remember() call that created a new block."""
        return self._last_learned_block_id

    @property
    def last_recall_block_ids(self) -> list[str]:
        """Block IDs from the most recent recall() or frame() call."""
        return list(self._last_recall_block_ids)

    @property
    def session_block_ids(self) -> list[str]:
        """All block IDs touched (learned or recalled) during the current session."""
        return list(self._session_block_ids)

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
            # Always-on advisory fields: in-memory state, may differ from DB.
            pending_count=self._pending,
            effective_threshold=self._effective_threshold(),
            co_retrieval_staging_count=len(self._co_retrieval_staging),
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

    @property
    def _db_path(self) -> str:
        """File path to the SQLite database. Empty string for in-memory databases."""
        db = str(self._engine.url.database or "")
        return "" if db in ("", ":memory:") else db

    def visualise(
        self,
        path: str | None = None,
        open_browser: bool = True,
        offline: bool = False,
        include_archived: bool = False,
        max_nodes: int = 100,
    ) -> str:
        """Generate an interactive HTML dashboard of the knowledge system.

        USE WHEN: Exploring the knowledge graph, diagnosing retrieval behaviour,
                  or sharing a snapshot of memory state with a team.
        DON'T USE WHEN: In automated pipelines or latency-sensitive agent code.
                         This is a developer/debug tool, not a production operation.
        COST: One synchronous SQLite read pass. No LLM calls. Fast.
        RETURNS: Absolute path to the generated HTML file.
        NEXT: Open the file in a browser. No cleanup required.
              Requires the elfmem[viz] extra: ``uv add elfmem[viz]``.

        Args:
            path: Output file path. A temp file is created if None.
            open_browser: Open the generated file in the default browser.
            offline: Inline vendored JS libraries (no CDN requests). Requires
                     real vis-network and Chart.js in src/elfmem/viz/assets/.
            include_archived: Include archived blocks as dim nodes in the graph.
            max_nodes: Cap on visible graph nodes; top-N selected by centrality.

        Raises:
            ElfmemError: If elfmem[viz] is not installed or if the database is
                         in-memory (no file to read from).
        """
        try:
            from elfmem.viz import DashboardData, render_dashboard
        except ImportError as exc:
            raise ElfmemError(
                "Visualisation requires the viz extra.",
                recovery="uv add elfmem[viz]",
            ) from exc

        db_path = self._db_path
        if not db_path:
            raise ElfmemError(
                "Visualisation requires a file-based database.",
                recovery=(
                    "Use MemorySystem.from_config('path/to/agent.db') "
                    "instead of an in-memory database."
                ),
            )

        data = DashboardData.from_db(
            db_path,
            include_archived=include_archived,
            max_nodes=max_nodes,
        )
        return render_dashboard(
            data,
            path=path,
            open_browser=open_browser,
            offline=offline,
        )

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
            # Sync _pending from DB: ensures accuracy regardless of how blocks
            # were added (learn, remember, or external writes). Separate read
            # connection closes before dream() opens its write connection.
            async with self._engine.connect() as conn:
                self._pending = await get_inbox_count(conn)
            # Respect policy threshold if set; fall back to config threshold.
            if self.should_dream:
                await self.dream()
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
            base_hours = await get_total_active_hours(conn)
            session_id = await _begin_session(conn, task_type=task_type)
        self._session_id = session_id
        self._session_started_at = time.monotonic()
        self._session_base_hours = base_hours
        # Fresh token slate for every new session
        if self._token_counter is not None:
            self._token_counter.reset()
        # Reset breadcrumbs: new session = fresh ID context
        self._last_learned_block_id = None
        self._last_recall_block_ids = []
        self._session_block_ids = []
        # Per-session Hebbian dedup: reset so each new session contributes fresh counts
        self._co_retrieval_session_seen.clear()
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
        # Snapshot (not reset) before the DB transaction. Reset only after
        # the transaction succeeds — prevents token data loss on DB failure.
        session_usage = (
            self._token_counter.snapshot() if self._token_counter is not None else None
        )
        async with self._engine.begin() as conn:
            duration = await _end_session(
                conn,
                self._session_id,
                wall_start=self._session_started_at,
                base_hours=self._session_base_hours,
            )
            if session_usage is not None:
                raw = await get_config(conn, "lifetime_token_usage")
                lifetime = _parse_token_usage(raw)
                updated = lifetime + session_usage
                await set_config(
                    conn, "lifetime_token_usage", json.dumps(updated.to_dict())
                )
        # Reset counter only after successful DB persist
        if self._token_counter is not None:
            self._token_counter.reset()
        self._session_id = None
        self._session_started_at = None
        self._session_base_hours = 0.0
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
        context manager auto-consolidates on exit when should_dream is True.
        Check system.should_dream after calling; call dream() when True.

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
        # Track pending count for should_dream advisory.
        # Both "created" and "near_duplicate_superseded" add a block to inbox.
        if result.status in ("created", "near_duplicate_superseded"):
            self._pending += 1
        # Breadcrumbs: only created blocks get a usable ID for connect()
        if result.status == "created":
            self._last_learned_block_id = result.block_id
            if result.block_id not in self._session_block_ids:
                self._session_block_ids.append(result.block_id)
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
        current_hours = self._current_active_hours()
        mem = self._config.memory

        async with self._engine.begin() as conn:
            result = await consolidate(
                conn,
                llm=self._llm,
                embedding_svc=self._embedding,
                current_active_hours=current_hours,
                self_alignment_threshold=mem.self_alignment_threshold,
                edge_score_threshold=mem.edge_score_threshold,
                edge_degree_cap=mem.edge_degree_cap,
                contradiction_similarity_prefilter=mem.contradiction_similarity_prefilter,
            )
            await set_config(conn, "last_consolidated_at", datetime.now(UTC).isoformat())

        self._pending = 0
        self._frame_cache.clear()

        async with self._engine.connect() as conn:
            run_curate = await should_curate(
                conn,
                current_hours,
                curate_interval_hours=mem.curate_interval_hours,
            )

        # Curate runs in its own transaction so its failure cannot roll back
        # the consolidation committed above.
        if run_curate:
            async with self._engine.begin() as conn:
                await _curate(
                    conn,
                    current_active_hours=current_hours,
                    prune_threshold=mem.prune_threshold,
                    edge_prune_threshold=mem.edge_prune_threshold,
                    reinforce_top_n=mem.curate_reinforce_top_n,
                )
                # Passive checkpoint at a natural maintenance boundary.
                # OperationalError is expected on in-memory databases (no WAL).
                with suppress(OperationalError):
                    await conn.execute(text("PRAGMA wal_checkpoint(PASSIVE)"))

        self._record_op("consolidate", result.summary)
        return result

    async def remember(
        self,
        content: str,
        tags: list[str] | None = None,
        *,
        category: str = "knowledge",
        source: str = "api",
    ) -> LearnResult:
        """Store knowledge and auto-start a session. Agent-friendly variant of learn().

        USE WHEN: Building always-on agents, MCP tools, or any context where
        you don't want to manage session lifecycle explicitly. Prefer this
        over learn() for agent code.

        DIFFERENCE FROM learn():
          - Auto-starts a session if none is active (idempotent — safe to call
            inside ``async with system.session():`` too).
          - Same result type, same cost, same semantics.

        DON'T USE WHEN: You're using the session() context manager and want
        explicit control over when sessions start. Either works; session() is
        cleaner for scripted use.

        COST: Instant. No LLM calls.

        RETURNS: LearnResult. Same as learn(). Check system.should_dream after
        this call — call dream() when True to process pending blocks.

        NEXT: After calling remember(), check should_dream. When True, call
        dream() at the next natural pause point (not in a tight loop).

        Example::

            result = await system.remember("EUR/USD breaks 1.10 resistance")
            if system.should_dream:
                await system.dream()
        """
        # Idempotent session start: no-op if session already active.
        await self.begin_session()
        return await self.learn(content, tags=tags, category=category, source=source)

    async def dream(self) -> ConsolidateResult | None:
        """Consolidate pending blocks at a natural pause point.

        The breathing rhythm: learn fast (remember), process deliberately (dream).

        USE WHEN: system.should_dream is True, or at any natural pause in agent
        execution (end of a reasoning step, waiting for user input, between tasks).
        Safe to call speculatively — returns None instantly if nothing is pending.

        DON'T USE WHEN: In a tight loop. One call processes all pending blocks.

        COST: LLM call per pending block. Returns None immediately (zero cost)
        if inbox is empty.

        RETURNS: ConsolidateResult if blocks were processed; None if inbox was
        empty. None is not an error — it means "nothing needed doing."

        NEXT: After dream(), newly consolidated blocks are searchable via frame()
        and recall(). The frame cache is cleared automatically.

        Note: _pending is an advisory counter. If blocks were added externally
        (e.g., another process), dream() may return None despite inbox having
        items. Call status() for DB-accurate inbox_count.

        Example::

            # Always-on agent loop
            result = await system.remember("new fact")
            if system.should_dream:
                dream_result = await system.dream()
                if dream_result:
                    print(dream_result)  # Consolidated 5: 4 promoted, 8 edges.
        """
        if self._pending == 0:
            return None
        result = await self.consolidate()
        # Feed policy so it can adapt the threshold for the next cycle,
        # then persist the new threshold so it survives process restarts.
        # Persistence is best-effort: a DB failure here is non-fatal —
        # the adapted threshold is still in memory for this session,
        # and the next restart will use whatever was last successfully saved.
        if self._policy is not None:
            self._policy.record_result(result)
            async with self._engine.begin() as conn:
                await set_config(
                    conn,
                    "consolidation_policy_threshold",
                    str(self._policy.effective_threshold),
                )
        return result

    @property
    def should_dream(self) -> bool:
        """True when pending blocks have reached the consolidation threshold.

        Uses ConsolidationPolicy if set; otherwise uses config.memory.inbox_threshold.

        This is a synchronous advisory based on the in-memory _pending counter.
        It is fast (no DB access) but can drift if blocks are added externally.
        For DB-accurate state, check status().inbox_count >= status().inbox_threshold.

        Example::

            result = await system.remember("fact")
            if system.should_dream:
                await system.dream()
        """
        if self._policy is not None:
            return self._policy.should_consolidate(self._pending)
        return self._pending >= self._config.memory.inbox_threshold

    def _effective_threshold(self) -> int:
        """Current consolidation threshold: policy-driven or config default."""
        if self._policy is not None:
            return self._policy.effective_threshold
        return self._config.memory.inbox_threshold

    async def setup(
        self,
        identity: str | None = None,
        values: list[str] | None = None,
        *,
        seed: bool = True,
    ) -> SetupResult:
        """Bootstrap agent identity: seed constitutional blocks and optional identity.

        USE WHEN: First use — before any other operations. Seeds 10 constitutional
        blocks that form the cognitive loop (curiosity, feedback, balance, etc.)
        then adds any identity description and domain values you provide.

        DON'T USE WHEN: Every session — SELF blocks persist across restarts.
        Duplicate content is silently rejected, so re-running is safe but
        unnecessary. Call once, then use remember() for new knowledge.

        COST: Fast per block (pure DB insert, no LLM). Each block queues in
        inbox; one LLM call per block during dream()/consolidate().

        RETURNS: SetupResult with blocks_created (new) and total_attempted.
        blocks_created=0 means all were already present — safe, not an error.

        NEXT: SELF blocks sit in inbox until dream() or consolidate() runs.
        After consolidation, frame('self') returns constitutional blocks as
        guaranteed slots. Call dream() or let the session context manager
        handle consolidation automatically.

        Args:
            identity: Optional identity description stored as a self/context block.
            values:   Optional list of domain-specific principles, each stored
                      as a separate self/value block.
            seed:     Seed the 10 constitutional blocks (default True). Pass
                      False to skip constitutional seeding and only add
                      identity/values — useful for custom bootstrapping.

        Example::

            result = await system.setup(
                identity="I am a trading assistant focused on risk-adjusted returns.",
                values=["cut losing positions early", "size positions to max 2% risk"],
            )
            print(result)  # Setup complete: 12/12 new blocks created.
        """
        results: list[LearnResult] = []

        if seed:
            from elfmem.seed import CONSTITUTIONAL_SEED
            for block in CONSTITUTIONAL_SEED:
                r = await self.remember(
                    block["content"],  # type: ignore[arg-type]
                    tags=block["tags"],  # type: ignore[arg-type]
                )
                results.append(r)

        if identity:
            r = await self.remember(identity, tags=["self/context"])
            results.append(r)

        if values:
            for value in values:
                r = await self.remember(value, tags=["self/value"])
                results.append(r)

        blocks_created = sum(1 for r in results if r.status == "created")
        result = SetupResult(
            blocks_created=blocks_created,
            total_attempted=len(results),
        )
        self._record_op("setup", result.summary)
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
        current_hours = self._current_active_hours()
        mem = self._config.memory

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
            # Hebbian staging — fires on genuine frame() retrievals only.
            # Skipped on cache hits: a cached result carries no new retrieval signal.
            # Paired with reinforce_co_retrieved_edges() in recall.py (runs first,
            # handling existing edges). We stage NEW pairs without existing edges.
            # session_seen enforces per-session dedup: each pair counts once per
            # begin_session() cycle so threshold means "N distinct sessions."
            recalled_ids = [b.id for b in result.blocks]
            promoted_count = 0
            if recalled_ids and not result.cached:
                promoted_count = await stage_and_promote_co_retrievals(
                    conn,
                    recalled_ids,
                    self._co_retrieval_staging,
                    threshold=mem.co_retrieval_edge_threshold,
                    edge_weight=mem.co_retrieval_edge_weight,
                    current_active_hours=current_hours,
                    staging_max=mem.co_retrieval_staging_max,
                    session_seen=self._co_retrieval_session_seen,
                )
            result.edges_promoted = promoted_count
        self._last_recall_block_ids = recalled_ids
        for bid in recalled_ids:
            if bid not in self._session_block_ids:
                self._session_block_ids.append(bid)
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
        current_hours = self._current_active_hours()

        if query is None:
            weights = frame_def.weights.renormalized_without_similarity()
        else:
            weights = frame_def.weights

        tag_filter: str | None = None
        if frame_def.filters.tag_patterns:
            tag_filter = frame_def.filters.tag_patterns[0]

        async with self._engine.connect() as conn:
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

        recalled_ids = [b.id for b in blocks]
        self._last_recall_block_ids = recalled_ids
        for bid in recalled_ids:
            if bid not in self._session_block_ids:
                self._session_block_ids.append(bid)
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
            print(result)  # Outcome recorded: 3 blocks updated (+0.042 avg confidence),
            #               2 edges reinforced.
        """
        current_hours = self._current_active_hours()
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
                edge_reinforce_delta=mem.edge_reinforce_delta,
                penalize_threshold=mem.penalize_threshold,
                penalty_factor=mem.penalty_factor,
                lambda_ceiling=mem.lambda_ceiling,
            )
        self._record_op("outcome", result.summary)
        return result

    async def connect(
        self,
        source: str,
        target: str,
        relation: str = "similar",
        *,
        weight: float | None = None,
        note: str | None = None,
        if_exists: Literal["reinforce", "update", "skip", "error"] = "reinforce",
    ) -> ConnectResult:
        """Create or update a semantic edge between two knowledge blocks.

        USE WHEN: The agent observes a meaningful relationship between two blocks
        and wants to encode it explicitly. Best called immediately after recall(),
        learn(), or outcome() when block IDs are available.

        DON'T USE WHEN: You don't have block IDs — use connect_by_query() instead.
        Don't connect blocks the agent hasn't read; unverified connections add noise.

        COST: Instant. No LLM calls. Pure database write.

        RETURNS: ConnectResult. action values:
          'created'    — new edge stored.
          'reinforced' — existing edge weight boosted; count incremented.
          'updated'    — relation type or note changed on existing edge.
          'skipped'    — edge exists and if_exists='skip'; no change.
        If a lower-priority auto-edge was displaced, displaced_edge is set in result.

        NEXT: To undo, call disconnect(). Block IDs are available via
        system.last_recall_block_ids and system.last_learned_block_id.

        Args:
            source: Block ID. Available from recall(), learn(), and outcome() results.
            target: Block ID. Edges are undirected; source/target order does not matter.
            relation: Semantic type. Core types: 'similar' (default), 'supports',
                'contradicts', 'elaborates', 'co_occurs', 'outcome'. Any other
                string is stored as a custom type.
            weight: Edge strength [0.0, 1.0]. None uses the relation-type default.
            note: Optional description of why this connection exists.
            if_exists: 'reinforce' (default) | 'update' | 'skip' | 'error'.

        Raises:
            SelfLoopError: source == target.
            BlockNotActiveError: block not found or not active.
            DegreeLimitError: degree cap full with only protected edges.
            ConnectError: if_exists='error' and edge already exists.
        """
        async with self._engine.begin() as conn:
            result = await do_connect(
                conn,
                source=source,
                target=target,
                relation=relation,
                weight=weight,
                note=note,
                if_exists=if_exists,
                edge_degree_cap=self._config.memory.edge_degree_cap,
                edge_reinforce_delta=self._config.memory.edge_reinforce_delta,
                current_active_hours=self._current_active_hours(),
            )
        self._record_op("connect", result.summary)
        return result

    async def disconnect(
        self,
        source: str,
        target: str,
        *,
        guard_relation: str | None = None,
        reason: str | None = None,
    ) -> DisconnectResult:
        """Remove the edge between two knowledge blocks.

        USE WHEN: An agent-created edge was incorrect. Also use to override
        automatic edges that cause retrieval noise (textually similar but
        contextually unrelated blocks).

        DON'T USE WHEN: The edge is correct but weak — decay and pruning remove
        it naturally. Only use disconnect() for deliberate correction.

        COST: Instant. No LLM calls.

        RETURNS: DisconnectResult. action values:
          'removed'   — edge deleted.
          'not_found' — no edge exists between the pair; no action taken.
          'guarded'   — edge exists but relation type did not match guard_relation.

        NEXT: No follow-up required. The edge is immediately gone from graph expansion.

        Args:
            source: Block ID.
            target: Block ID.
            guard_relation: Only remove if current relation type matches this value.
                Safety check — prevents accidentally removing agent-typed edges
                when intending to remove auto-created ones.
            reason: Optional reason stored in operation history.
        """
        async with self._engine.begin() as conn:
            result = await do_disconnect(
                conn,
                source=source,
                target=target,
                guard_relation=guard_relation,
            )
        note = f" reason={reason!r}" if reason else ""
        self._record_op("disconnect", result.summary + note)
        return result

    async def connect_by_query(
        self,
        source_query: str,
        target_query: str,
        relation: str = "similar",
        *,
        note: str | None = None,
        min_confidence: float = 0.70,
        if_exists: Literal["reinforce", "update", "skip", "error"] = "reinforce",
        dry_run: bool = False,
    ) -> ConnectByQueryResult:
        """Find two blocks by semantic query and connect them.

        USE WHEN: The agent has a clear conceptual relationship in mind but
        doesn't have block IDs available. Internally runs two recall() calls.

        DON'T USE WHEN: You have block IDs — use connect() for precision.
        Vague queries may match the wrong blocks.

        COST: Two embedding calls (fast). No LLM calls.

        RETURNS: ConnectByQueryResult. ALWAYS verify source_content and
        target_content to confirm the correct blocks were matched. Use
        dry_run=True to preview without writing.

        Args:
            source_query: Natural language description of the source block.
            target_query: Natural language description of the target block.
            relation: Semantic type — same as connect().
            note: Optional description of the relationship.
            min_confidence: Minimum score for a match to be accepted. Default: 0.70.
            if_exists: Same as connect().
            dry_run: Preview matches without writing the edge.
        """
        src_blocks = await self.recall(source_query, top_k=1)
        tgt_blocks = await self.recall(target_query, top_k=1)

        src_block = src_blocks[0] if src_blocks else None
        tgt_block = tgt_blocks[0] if tgt_blocks else None
        src_conf = src_block.score if src_block else 0.0
        tgt_conf = tgt_block.score if tgt_block else 0.0

        if (
            src_block is None or tgt_block is None
            or src_conf < min_confidence or tgt_conf < min_confidence
        ):
            return ConnectByQueryResult(
                source_query=source_query,
                target_query=target_query,
                source_id=src_block.id if src_block else None,
                target_id=tgt_block.id if tgt_block else None,
                source_content=src_block.content if src_block else None,
                target_content=tgt_block.content if tgt_block else None,
                source_confidence=src_conf,
                target_confidence=tgt_conf,
                action="insufficient_confidence",
            )

        if dry_run:
            return ConnectByQueryResult(
                source_query=source_query,
                target_query=target_query,
                source_id=src_block.id,
                target_id=tgt_block.id,
                source_content=src_block.content,
                target_content=tgt_block.content,
                source_confidence=src_conf,
                target_confidence=tgt_conf,
                action="dry_run_preview",
            )

        connect_result = await self.connect(
            src_block.id, tgt_block.id,
            relation=relation, note=note, if_exists=if_exists,
        )
        return ConnectByQueryResult(
            source_query=source_query,
            target_query=target_query,
            source_id=src_block.id,
            target_id=tgt_block.id,
            source_content=src_block.content,
            target_content=tgt_block.content,
            source_confidence=src_conf,
            target_confidence=tgt_conf,
            action="connected",
            connect_result=connect_result,
        )

    async def connects(
        self,
        edges: list[ConnectSpec],
    ) -> ConnectsResult:
        """Create or update multiple edges in a single operation.

        USE WHEN: End-of-session reflection — the agent has identified several
        relationships to encode at once.

        COST: Instant per edge. One DB call per spec. No LLM calls.

        RETURNS: ConnectsResult with per-edge results and aggregate counts.
        Per-edge errors are collected (not raised) so a single failure does not
        abort the batch.

        Args:
            edges: List of ConnectSpec(source, target, relation, weight, note, if_exists).
        """
        results: list[ConnectResult] = []
        counts: dict[str, int] = {
            "created": 0, "reinforced": 0, "updated": 0, "skipped": 0, "deferred": 0,
        }
        errors: list[str] = []

        for spec in edges:
            try:
                r = await self.connect(
                    spec.source,
                    spec.target,
                    spec.relation,
                    weight=spec.weight,
                    note=spec.note,
                    if_exists=spec.if_exists,  # type: ignore[arg-type]
                )
                results.append(r)
                counts[r.action] = counts.get(r.action, 0) + 1
            except Exception as exc:
                errors.append(f"{spec.source[:8]}→{spec.target[:8]}: {exc}")

        batch_result = ConnectsResult(
            results=results,
            created=counts["created"],
            reinforced=counts["reinforced"],
            updated=counts["updated"],
            skipped=counts["skipped"],
            deferred=counts.get("deferred", 0),
            errors=errors,
        )
        self._record_op("connects", batch_result.summary)
        return batch_result

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
        current_hours = self._current_active_hours()
        mem = self._config.memory
        async with self._engine.begin() as conn:
            result = await _curate(
                conn,
                current_active_hours=current_hours,
                prune_threshold=mem.prune_threshold,
                edge_prune_threshold=mem.edge_prune_threshold,
                reinforce_top_n=mem.curate_reinforce_top_n,
            )
        # Sync in-memory staging after archival. Two-step:
        # 1. Prune rows where either block was archived (status != 'active').
        #    FK CASCADE handles physical deletions; this handles the normal
        #    archival case where the block row stays with status='archived'.
        # 2. Reload the now-clean staging into memory.
        if self._co_retrieval_staging:
            async with self._engine.begin() as conn:
                await prune_stale_co_retrieval_staging(conn)
                self._co_retrieval_staging = await load_co_retrieval_staging(conn)
        self._record_op("curate", result.summary)
        return result

    async def close(self) -> None:
        """Dispose the database engine. Call when done with this MemorySystem.

        USE WHEN: Shutting down. Releases the SQLite connection pool.

        COST: Fast.
        """
        await self._engine.dispose()

    # ── Private helpers ──────────────────────────────────────────────────────

    def _current_active_hours(self) -> float:
        """Total active hours including the current in-progress session.

        Thread-safe: reads only instance fields owned by this MemorySystem.
        """
        if self._session_started_at is None:
            return self._session_base_hours
        elapsed = (time.monotonic() - self._session_started_at) / 3600.0
        return self._session_base_hours + elapsed

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


# ── Presentation helpers (used by CLI and MCP) ───────────────────────────────


def format_recall_response(result: FrameResult) -> dict[str, Any]:
    """Format a FrameResult for agent tool responses.

    FrameResult.to_dict() is compact and omits per-block detail intentionally.
    Agents calling outcome() need block IDs — this function surfaces them.
    Used by both the MCP server and CLI --json output.
    """
    return {
        "text": result.text,
        "frame_name": result.frame_name,
        "cached": result.cached,
        "blocks": [_format_block(b) for b in result.blocks],
    }


def _format_block(block: ScoredBlock) -> dict[str, Any]:
    """Extract the agent-relevant fields from a ScoredBlock."""
    return {
        "id": block.id,
        "content": block.content,
        "score": round(block.score, 3),
        "tags": block.tags,
    }


# ── Internal helpers ──────────────────────────────────────────────────────────

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
