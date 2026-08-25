"""MemorySystem public API — thin façade over all layers."""

from __future__ import annotations

import json
import logging
import os
import re
import time
from collections import deque
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path
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
    get_block,
    get_block_counts,
    get_config,
    get_inbox_blocks,
    get_inbox_count,
    get_tags_batch,
    get_total_active_hours,
    list_active_blocks,
    load_co_retrieval_staging,
    prune_stale_co_retrieval_staging,
    seed_builtin_data,
    set_config,
    update_block_content,
    update_block_cue,
    update_block_status,
)
from elfmem.exceptions import (
    BlockNotFound,
    ConfigError,
    ElfmemError,
    FrameError,
    ProjectNotFound,
    SubstrateWriteError,
)
from elfmem.guide import get_guide
from elfmem.logging_config import configure_logging
from elfmem.memory import file_mutation as _file_mutation
from elfmem.memory import ledger as _ledger
from elfmem.memory.blockfile import Block as _BlockFile
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
    AmendmentRecord,
    AmendmentResult,
    ArchiveReason,
    BlockAnalysis,
    BlockSummary,
    ConnectByQueryResult,
    ConnectResult,
    ConnectSpec,
    ConnectsResult,
    ConsolidateResult,
    ConstitutionalReviewResult,
    CorpusReviewResult,
    CurateResult,
    DisconnectResult,
    EditResult,
    ExportResult,
    ForgetResult,
    FrameResult,
    ImportResult,
    InboxBlockSummary,
    LearnDocumentResult,
    LearnResult,
    MetabolismDryRunResult,
    MindOutcomeResult,
    MindPredictResult,
    MindShowResult,
    MindSummary,
    OperationRecord,
    OutcomeResult,
    PeerInboxResult,
    PeerInboxStatus,
    PeerInfo,
    PeerSendResult,
    ScoredBlock,
    SetupResult,
    SystemStatus,
    TokenUsage,
)

logger = logging.getLogger(__name__)

# ── Document chunking helpers ────────────────────────────────────────────────

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def _default_chunker(text: str) -> list[str]:
    """Split text into sentences at [.!?] followed by whitespace."""
    return [s.strip() for s in _SENTENCE_SPLIT_RE.split(text) if s.strip()]


def _assemble_chunks(sentences: list[str], chunk_size: int) -> list[str]:
    """Combine sentences into chunks of ~chunk_size words."""
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for sentence in sentences:
        sent_len = len(sentence.split())
        if current_len + sent_len > chunk_size and current:
            chunks.append(" ".join(current))
            current = []
            current_len = 0
        current.append(sentence)
        current_len += sent_len
    if current:
        chunks.append(" ".join(current))
    return chunks


def _parse_host_analyses(
    raw: dict[str, dict[str, Any]] | None,
) -> dict[str, BlockAnalysis] | None:
    """Validate and convert host-supplied ``consolidate(host_analyses=...)``
    input into ``BlockAnalysis``, reusing the exact schema
    (``BlockAnalysisModel``) and tag-filtering (``VALID_SELF_TAGS``) a real
    LLM adapter's response already goes through — host-supplied reasoning
    gets the same guarantees, not a looser bespoke check. Raises on a
    malformed entry rather than silently degrading: this is direct
    structured input from the caller, not unreliable external I/O, so
    fail-fast is correct here (contrast the broad except around the real
    adapter call in operations/consolidate.py).
    """
    if not raw:
        return None
    from pydantic import ValidationError

    from elfmem.adapters.models import BlockAnalysisModel
    from elfmem.exceptions import HostAnalysisError
    from elfmem.prompts import VALID_SELF_TAGS

    result: dict[str, BlockAnalysis] = {}
    for block_id, fields in raw.items():
        try:
            parsed = BlockAnalysisModel.model_validate(fields)
        except ValidationError as e:
            raise HostAnalysisError(
                f"Invalid host_analyses entry for block {block_id!r}: {e}",
                recovery=(
                    "Each entry must be {\"alignment_score\": <float 0.0-1.0>, "
                    "\"tags\": [<str>, ...], \"summary\": \"<str>\"}. "
                    f"Fix block {block_id!r}'s entry and retry."
                ),
            ) from e
        filtered_tags = [t for t in parsed.tags if t in VALID_SELF_TAGS]
        result[block_id] = BlockAnalysis(
            alignment_score=parsed.alignment_score,
            tags=filtered_tags,
            summary=parsed.summary,
        )
    return result


def _db_matches_project(cfg: ElfmemConfig, db_path: str) -> bool:
    """Is *db_path* the database this project's config declares?

    Used to decide whether ledger events belong to the project's history.
    A config with no project section declares nothing, so nothing conflicts.
    """
    declared = getattr(getattr(cfg, "project", None), "db", None)
    if not declared:
        return True
    return (
        Path(declared).expanduser().resolve()
        == Path(db_path).expanduser().resolve()
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
        project_root: Path | None = None,
    ) -> None:
        self._engine = engine
        self._llm = llm_service
        self._embedding = embedding_service
        self._config = config or ElfmemConfig()
        self._project_root = project_root
        # ^project root used to derive default peer inbox/outbox paths. None when
        # no project was discovered; peer ops then fail fast with ProjectNotFound
        # unless the config explicitly overrides inbox_dir/outbox_dir.
        self._frame_cache = FrameCache()
        # Append-only event ledger. Holds the history the markdown substrate
        # structurally cannot: reinforcement, recency, and the α/β posterior.
        # None when no project was discovered — the ledger is a project-local
        # file, and a global/ephemeral instance simply has nowhere to put it,
        # which degrades to exactly the pre-ledger behaviour.
        self._ledger_dir: Path | None = (
            Path(project_root) / ".elfmem" / _ledger.LEDGER_DIRNAME
            if project_root is not None
            else None
        )
        # File substrate. `memory_dir` is where blocks live when files are
        # authoritative; None when no project was discovered, in which case
        # file authority is impossible and the flag is ignored.
        self._memory_dir: Path | None = (
            Path(project_root) / ".elfmem" / "memory"
            if project_root is not None
            else None
        )
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

    @property
    def _files_authoritative(self) -> bool:
        """Is the file substrate the source of truth for writes?

        Requires both the opt-in and a discoverable project: a global instance
        has nowhere to put `.elfmem/memory/`, so it stays database-primary
        regardless of the flag rather than failing at the first write.
        """
        return (
            self._config.substrate.files_authoritative
            and self._memory_dir is not None
        )

    def _log(self, kind: str, **payload: object) -> None:
        """Append one ledger event. Silently a no-op with no project root.

        Never raises: a memory operation that succeeded must not be reported
        as failed because its history line could not be written. A lost line
        costs some replayable history; a raised exception costs the write.
        """
        if self._ledger_dir is None:
            return
        try:
            _ledger.append(
                self._ledger_dir, kind,
                active_hours=self._current_active_hours(), **payload,
            )
        except OSError as e:
            logger.warning("Ledger append failed (%s): %s", kind, e)

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

        # Configure logging (zero overhead if disabled)
        configure_logging(
            level=cfg.logging.level,
            format_type=cfg.logging.format,
            file_path=cfg.logging.file,
            module_overrides=cfg.logging.modules,
        )

        engine = await create_engine(db_path)
        async with engine.begin() as conn:
            await conn.run_sync(metadata.create_all)
            await seed_builtin_data(conn)
            # Apply any pending schema migrations (idempotent, fast skip if current).
            # Backup is created automatically before the first migration.
            from elfmem.db.migrate import ensure_schema_current
            await ensure_schema_current(conn, db_path=db_path)
            # One-shot embedding-lock backfill for existing installs.
            # Sets the lock from existing data if homogeneous; raises with a
            # clear recovery hint on heterogeneous or all-legacy state. No-op
            # on fresh DBs (the wrapper will set the lock on first embed).
            from elfmem.db.queries import backfill_embedding_lock_if_needed
            await backfill_embedding_lock_if_needed(conn)
            # Sync config-declared peers into peer_roster (insert-only).
            # Idempotent: existing peers' trust/stats are preserved; only
            # missing entries are added. Resolves the historical bug where
            # peers: in YAML was silently ignored by the config loader.
            if cfg.peers:
                from elfmem.operations.peer import sync_peers_from_config
                await sync_peers_from_config(conn, cfg.peers)
            # Migrate legacy outbox/<name-slug>/ to the canonical
            # outbox/<did-slug>/ form. Safe to run unconditionally — does
            # nothing on instances that never had the pre-canonical layout.
            project_root_for_migration = _discover_project_root(config)
            if project_root_for_migration:
                from elfmem.operations.peer import (
                    migrate_legacy_outbox_slugs,
                )
                outbox_for_migration = (
                    project_root_for_migration / ".elfmem" / "outbox"
                )
                await migrate_legacy_outbox_slugs(conn, outbox_for_migration)
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
        # Wrap the embedding service so every embed call verifies the lock.
        # The wrapper is the single enforcement point for embedding-model
        # integrity; the migration verb deliberately bypasses it by using
        # the bare adapter via its own factory call. See
        # docs/plans/plan_embedding_lock.md.
        from elfmem.adapters.locked import LockedEmbeddingService
        embedding_svc = LockedEmbeddingService(embedding_svc, engine)

        project_root = _discover_project_root(config)

        # The ledger describes the memory that pairs with the project's own
        # database. When `--db` points somewhere else -- a copy, a scratch
        # index, a migration rehearsal -- events from that database must not
        # land in the project's real ledger, so the ledger is switched off
        # rather than pointed at a history it does not describe.
        if project_root is not None and not _db_matches_project(cfg, db_path):
            logger.debug(
                "Ledger disabled: db_path %s is not this project's configured "
                "database.", db_path,
            )
            project_root = None

        return cls(
            engine=engine,
            llm_service=llm_svc,
            embedding_service=embedding_svc,
            config=cfg,
            token_counter=counter,
            policy=policy,
            initial_pending=initial_pending,
            initial_co_retrieval_staging=co_retrieval_staging,
            project_root=project_root,
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
        auto_dream: bool = True,
    ) -> AsyncIterator[MemorySystem]:
        """Full lifecycle context manager: open → session → yield → close.

        USE WHEN: Scripts, CLI commands, and short-lived agents that need a
        complete open-and-close lifecycle in one block. Starts a session on
        entry so active-hours tracking and frame scoring are always correct.

        DON'T USE WHEN: Long-running processes that reuse the same
        MemorySystem across many requests — call from_config() once, then
        use begin_session()/end_session() or session() as needed.

        COST: from_config() on entry (fast). dream() on exit only when
        auto_dream=True and blocks are pending.

        NEXT: After the block exits, the engine is disposed and all DB
        connections are closed. Check ``should_dream`` before exiting if
        you passed ``auto_dream=False``.

        Args:
            auto_dream: When True (default), consolidates pending blocks on
                exit as a safety net. Set to False for CLI commands and
                other contexts where implicit consolidation would cause
                unexpected delays. Unconsolidated blocks remain safely in
                the inbox for the next explicit ``dream()`` call.

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
            if auto_dream and mem.should_dream:
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
            cue: str | None = None,
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
                conn, content=content, tags=tags, category=category,
                source=source, cue=cue,
            )
        # Track pending count for should_dream advisory.
        # Both "created" and "near_duplicate_superseded" add a block to inbox.
        if result.status in ("created", "near_duplicate_superseded"):
            self._pending += 1
        # Breadcrumbs: only created blocks get a usable ID for connect()
        if result.status == "created":
            if self._files_authoritative:
                # The file is the commit. The index insert above is a derived
                # consequence -- if this raises, that row is orphaned, so it
                # is rolled back rather than left claiming a block the
                # substrate never accepted.
                try:
                    _file_mutation.append_block(
                        self._memory_dir,
                        _BlockFile(
                            title=content.strip().splitlines()[0][:60] or "Untitled",
                            content=content,
                            id=result.block_id,
                            tags=list(tags or []),
                            cue=cue,
                        ),
                        subdir="log",
                        category=category,
                    )
                except Exception as exc:
                    async with self._engine.begin() as conn:
                        await update_block_status(
                            conn, result.block_id, "archived",
                            archive_reason=ArchiveReason.FORGOTTEN.value,
                        )
                    raise SubstrateWriteError(result.block_id, cause=exc) from exc
            self._last_learned_block_id = result.block_id
            self._log(_ledger.KIND_BIRTH, id=result.block_id)
            if result.block_id not in self._session_block_ids:
                self._session_block_ids.append(result.block_id)
        self._record_op("learn", result.summary)
        return result

    async def inbox(self, max_count: int | None = None) -> list[InboxBlockSummary]:
        """List pending inbox blocks — not yet consolidated. FIFO order
        (oldest first), matching the order `consolidate()` would process
        them under `max_inbox_per_run`.

        USE WHEN: reasoning about pending blocks yourself before calling
        `consolidate(host_analyses=...)` — e.g. a host agent session (this
        Claude Code session) supplying its own alignment_score/tags/summary
        instead of a configured LLM adapter.

        DON'T USE WHEN: you only need a count — `status().inbox_count` is
        cheaper (no content fetched).

        COST: pure read. No LLM calls, no embedding calls.

        RETURNS: list[InboxBlockSummary] — id, content, category, tags,
        created_at. Empty list if the inbox is empty.

        NEXT: for each block, decide alignment_score (0.0-1.0), tags (from
        the self/* vocabulary — self/constitutional, self/constraint,
        self/value, self/style, self/goal, self/context), and a factual
        1-2 sentence summary. Then call `consolidate(host_analyses={
        block_id: {"alignment_score": ..., "tags": [...], "summary": ...}
        })` — or `dream(host_analyses=...)` to also run rescore/curate in
        the same call.
        """
        async with self._engine.connect() as conn:
            rows = await get_inbox_blocks(conn)
            if max_count is not None:
                rows = rows[:max_count]
            tags_map = await get_tags_batch(conn, [r["id"] for r in rows])
        return [
            InboxBlockSummary(
                id=r["id"],
                content=r["content"],
                category=r["category"],
                tags=tags_map.get(r["id"], []),
                created_at=r["created_at"],
            )
            for r in rows
        ]

    async def consolidate(
        self, *, skip_llm: bool = False,
        max_inbox_per_run: int | None = None,
        host_analyses: dict[str, dict[str, Any]] | None = None,
    ) -> ConsolidateResult:
        """Process inbox blocks: score, embed, deduplicate, and promote to active memory.

        USE WHEN: After a batch of learn() calls, or explicitly before
        recall/frame when you know new blocks are in the inbox. The session()
        context manager handles this automatically.

        DON'T USE WHEN: Inbox is empty — safe to call but returns zero counts.

        COST: LLM call per block (alignment scoring + tag inference). Slow for
        large inboxes; fast when inbox is small.
        - skip_llm=True: bypasses ALL LLM calls (embed + promote only).
          Fastest but blocks get neutral scoring and no summaries.
        - host_analyses: supply the analysis yourself instead of paying for
          (or depending on) a configured LLM adapter — see below.

        One call processes at most ``max_inbox_per_run`` blocks (default from
        ``consolidation.max_inbox_per_run``, currently 5 — ADR 0007). A large
        inbox is drained across repeated calls, not in one unbounded run;
        check ``inbox_remaining`` on the result and call again if nonzero.
        Pass a large ``max_inbox_per_run`` (e.g. 100000) for a one-shot
        full-drain sweep on a fast adapter, matching the ``rescore()``
        convention for its own ``max_count``.

        Args:
            host_analyses: ``{block_id: {"alignment_score": float,
                "tags": [str, ...], "summary": str}, ...}``. A block id
                present here is treated as genuinely analysed — the
                configured LLM adapter is never called for it, and the
                block is scored exactly as a successful adapter call would
                score it (not the neutral ``skip_llm`` fallback). Lets a
                host agent session (e.g. this Claude Code session) supply
                its own reasoning instead of a configured LLM adapter —
                see ``inbox()`` for the read half of this loop. Blocks in
                the batch not covered here still go through the normal
                path (configured adapter, or ``skip_llm`` fallback).
                Validated and tag-filtered the same way a real adapter's
                response is (``VALID_SELF_TAGS``) — a malformed entry
                raises rather than silently degrading, since this is
                direct structured input, not unreliable external I/O.

        RETURNS: ConsolidateResult with counts: processed, promoted,
        deduplicated, edges_created, inbox_remaining. processed=0 means
        inbox was empty.

        NEXT: Promoted blocks are now searchable via frame() and recall().
        Also triggers curate() automatically if curate_interval has elapsed.
        If inbox_remaining > 0, call consolidate()/dream() again to continue.
        """
        current_hours = self._current_active_hours()
        mem = self._config.memory
        budget = max_inbox_per_run if max_inbox_per_run is not None else mem.max_inbox_per_run
        parsed_host_analyses = _parse_host_analyses(host_analyses)

        async with self._engine.begin() as conn:
            result = await consolidate(
                conn,
                llm=self._llm,
                embedding_svc=self._embedding,
                current_active_hours=current_hours,
                self_alignment_threshold=mem.self_alignment_threshold,
                near_dup_exact_threshold=mem.near_dup_exact_threshold,
                near_dup_near_threshold=mem.near_dup_near_threshold,
                edge_score_threshold=mem.edge_score_threshold,
                edge_degree_cap=mem.edge_degree_cap,
                skip_llm=skip_llm,
                max_inbox_per_run=budget,
                host_analyses=parsed_host_analyses,
                ledger_dir=self._ledger_dir,
            )
            await set_config(conn, "last_consolidated_at", datetime.now(UTC).isoformat())

        # ADR 0007: a budget-limited run may leave blocks in the inbox, so
        # _pending tracks what's actually left rather than assuming a full
        # drain. should_dream/status() stay accurate across repeated calls.
        self._pending = result.inbox_remaining
        if self._files_authoritative:
            # Promotion happened in the index; the substrate has to follow, or
            # the block stays in log/ and every rebuild returns it to inbox.
            async with self._engine.connect() as conn:
                rows = await list_active_blocks(conn)
            _file_mutation.reconcile_status(
                self._memory_dir,
                active_categories={r["id"]: r["category"] for r in rows},
            )
            # Tags are declared state, so LLM-inferred ones have to reach the
            # file. Left in the index alone, a rebuild would drop every tag
            # the agent derived, taking frame('self')'s tag filter and the
            # decay tier with it.
            async with self._engine.connect() as conn:
                tags_by_id = await get_tags_batch(conn, [r["id"] for r in rows])
            _file_mutation.sync_tags(self._memory_dir, tags_by_id)
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
            cue: str | None = None,
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
        return await self.learn(content, tags=tags, category=category, source=source, cue=cue)

    async def learn_document(
        self,
        text: str,
        *,
        chunk_size: int = 256,
        chunker: Callable[[str], list[str]] | None = None,
        tags: list[str] | None = None,
        category: str = "knowledge",
        source: str = "document",
        skip_llm: bool = False,
    ) -> LearnDocumentResult:
        """Ingest a document: chunk, learn each chunk, auto-consolidate.

        USE WHEN: Ingesting a document, article, or long-form text. Handles
        chunking, learning, and consolidation in one call.

        DON'T USE WHEN: Ingesting a single fact or short observation — use
        learn() instead.

        COST: O(chunks) learn() calls + dream() at inbox_threshold intervals.
        With skip_llm=True, dream() runs the fast embedding-only path.

        RETURNS: LearnDocumentResult with chunk and consolidation counts.

        NEXT: recall() or frame() to query the ingested knowledge.

        Args:
            text: The full document text to ingest.
            chunk_size: Target words per chunk (default 256).
            chunker: Sentence splitter function. Receives the full text,
                returns a list of sentences. Default splits at [.!?]
                followed by whitespace. Pass ``nltk.sent_tokenize`` for
                better accuracy with abbreviations and edge cases.
            tags: Tags applied to every chunk (in addition to ``chunk:N``).
            category: Block category (default "knowledge").
            source: Block source identifier (default "document").
            skip_llm: Forward to dream() — bypass LLM calls during
                consolidation (embed + promote only).

        Example::

            result = await system.learn_document(article_text, chunk_size=200)
            print(result)  # Ingested 12 chunks: 12 created, 2 consolidations, 10 promoted.
        """
        split_fn = chunker or _default_chunker
        sentences = split_fn(text)
        chunks = _assemble_chunks(sentences, chunk_size)

        created = 0
        duplicates = 0
        consolidations = 0
        promoted = 0

        for i, chunk in enumerate(chunks):
            result = await self.learn(
                content=chunk,
                tags=[f"chunk:{i}", *(tags or [])],
                category=category,
                source=source,
            )
            if result.status == "created":
                created += 1
            elif result.status == "duplicate_rejected":
                duplicates += 1

            if self.should_dream:
                dr = await self.dream(skip_llm=skip_llm)
                if dr is not None:
                    consolidations += 1
                    promoted += dr.promoted

        doc_result = LearnDocumentResult(
            chunks_total=len(chunks),
            chunks_created=created,
            chunks_duplicate=duplicates,
            consolidations=consolidations,
            blocks_promoted=promoted,
        )
        self._record_op("learn_document", doc_result.summary)
        return doc_result

    async def edit(
        self, block_id: str, content: str | None = None, *,
        cue: str | None = None,
    ) -> EditResult:
        """Replace an active block's content directly — no LLM mediation.

        USE WHEN: A stored memory is wrong, stale, or needs rewording, and
        you know which block. This is the direct write path RC1 identified
        as missing — previously the only way to change a block's content was
        an indirect side effect of near-duplicate supersession.

        DON'T USE WHEN: The block is in inbox (not yet consolidated) or
        already archived — edit() only targets active blocks. For an inbox
        block, just learn() the corrected version instead.

        COST: One embedding call (the new content must be re-embedded). No
        LLM call — alignment/tags/summary are not re-scored here.

        RETURNS: EditResult with the block's id.

        NEXT: The block's summary and last_scored_at are cleared, so it is
        first in line for the next dream(--rescore) or consolidate() cycle.
        Confidence, reinforcement_count, and decay_lambda are untouched —
        editing content is not a knowledge-confirmation event.

        Raises:
            BlockNotFound: block_id doesn't exist or isn't active.

        Example::

            result = await system.edit(block_id, "Corrected: prefers tabs, not spaces.")
        """
        if content is None and cue is None:
            raise ConfigError(
                "edit() needs content, cue, or both — nothing to change.",
                recovery="Pass content='...' to rewrite the block, or "
                        "cue='...' to set when it should be recalled.",
            )
        async with self._engine.begin() as conn:
            block = await get_block(conn, block_id)
            if block is None or block["status"] != "active":
                raise BlockNotFound(block_id)
            if content is not None:
                if self._files_authoritative:
                    _file_mutation.edit_block(
                        self._memory_dir, block_id, content
                    )
                embedding = await self._embedding.embed(content.strip().lower())
                await update_block_content(
                    conn,
                    block_id=block_id,
                    content=content,
                    embedding=embedding,
                    embedding_model=self._embedding.model_name,
                )
            if cue is not None:
                # No re-embed: a cue says when to recall the block, not what
                # it claims, so it neither invalidates the embedding nor
                # belongs in the rescore queue.
                await update_block_cue(conn, block_id=block_id, cue=cue)
        self._frame_cache.clear()
        self._log(_ledger.KIND_EDIT, id=block_id)
        result = EditResult(block_id=block_id)
        self._record_op("edit", result.summary)
        return result

    async def forget(
        self, block_id: str, *, reason: ArchiveReason = ArchiveReason.FORGOTTEN,
    ) -> ForgetResult:
        """Archive a block by explicit request — the direct delete path.

        USE WHEN: A memory should no longer be active — it's wrong, no
        longer relevant, or was seeded content you never wanted. This is
        the direct delete path RC1 identified as missing; previously blocks
        could only leave active memory via near-duplicate supersession or
        decay, neither of which is a deliberate human choice.

        DON'T USE WHEN: You want a block to stop showing up in retrieval
        temporarily without losing it forever — forget() is a real archive
        (tags, edges, and contradictions involving this block are removed).

        COST: Instant. No LLM or embedding calls.

        RETURNS: ForgetResult. status='forgotten' on a real archive;
        status='already_archived' if the block was archived already — this
        is a safe no-op, not an error, so forget() is idempotent to call.

        NEXT: The block no longer appears in frame(), recall(), or ls().

        Args:
            block_id: The block to archive.
            reason: Audit trail for *why* — defaults to FORGOTTEN (a direct
                    human/agent decision). `review_corpus()` (v2 step 6a)
                    passes DECAYED when applying an accepted staleness
                    proposal, so `archive_reason` distinguishes "I decided
                    to remove this" from "corpus review found it stale and
                    I accepted that finding" in the same audit column.

        Raises:
            BlockNotFound: block_id doesn't exist at all (any status).

        Example::

            result = await system.forget(block_id)
            print(result)  # Forgot block a1b2c3d4.
        """
        async with self._engine.begin() as conn:
            block = await get_block(conn, block_id)
            if block is None:
                raise BlockNotFound(block_id)
            if block["status"] == "archived":
                result = ForgetResult(block_id=block_id, status="already_archived")
                self._record_op("forget", result.summary)
                return result
            if self._files_authoritative:
                # Removing it from the file is the real forget; the index row
                # is only marked archived so retrieval stops returning it.
                # Git history is the undo, which is why .elfmem/memory must
                # be committed.
                _file_mutation.forget_block(self._memory_dir, block_id)
            await update_block_status(
                conn, block_id, "archived",
                archive_reason=reason.value,
            )
        self._frame_cache.clear()
        self._log(_ledger.KIND_REMOVE, id=block_id, why=reason.value)
        result = ForgetResult(block_id=block_id, status="forgotten")
        self._record_op("forget", result.summary)
        return result

    async def review_corpus(self) -> CorpusReviewResult:
        """Run a corpus-level review cycle: deterministic staleness detection.

        USE WHEN: Periodically, to find blocks that have quietly stopped
        earning their place — long-unused, rarely reinforced, never
        confirmed by an outcome. The corpus-level counterpart to
        review_constitutional(): proposes, never mutates on its own.

        DON'T USE WHEN: You want automatic archival — nothing is applied
        until you call forget(proposal.block_id, reason=ArchiveReason.DECAYED)
        per proposal you accept. See `elfmem review corpus` for the
        interactive accept/reject/skip walkthrough.

        COST: Zero LLM calls — pure SQL/math over already-active blocks.
        (Duplicate/contradiction detection, which does need one whole-corpus
        LLM call, is a later addition to this method's output, not yet
        built.)

        RETURNS: CorpusReviewResult with reviewed_count and a list of
        CorpusProposal (kind='stale' today).

        NEXT: Iterate `.proposals`; for each you accept, call
        `forget(proposal.block_id, reason=ArchiveReason.DECAYED)`.

        Example::

            result = await system.review_corpus()
            for p in result.proposals:
                print(p)  # [stale] a1b2c3d4…: not reinforced in 812h (reinforced 0x, ...)
        """
        from elfmem.operations.corpus_review import review_corpus as _review_corpus

        current_hours = self._current_active_hours()
        cfg = self._config.review.corpus
        async with self._engine.connect() as conn:
            result = await _review_corpus(
                conn,
                current_active_hours=current_hours,
                min_hours_since_reinforced=cfg.stale_min_hours_since_reinforced,
                max_reinforcement_count=cfg.stale_max_reinforcement_count,
                max_proposals=cfg.max_proposals,
            )
        self._record_op("review_corpus", result.summary)
        return result

    async def ls(
        self,
        tag: str | None = None,
        category: str | None = None,
        *,
        limit: int = 50,
    ) -> list[BlockSummary]:
        """List active blocks — a deterministic, unscored view of memory.

        USE WHEN: You need to see what's in memory to find a block_id for
        edit()/forget(), or to audit memory contents directly. This is the
        direct read path RC1 identified as missing.

        DON'T USE WHEN: You want relevance-ranked results for a query — use
        recall() or frame() instead. ls() does not rank by similarity,
        centrality, or reinforcement; it is a flat listing, most-recently-
        active first.

        COST: Instant. No LLM or embedding calls.

        RETURNS: List of BlockSummary, most-recently-reinforced first.
        Empty list if nothing matches (not an error).

        NEXT: Pass a returned .id to edit() or forget().

        Args:
            tag: Optional SQL LIKE pattern (e.g. 'self/%' for a prefix
                 match), same semantics as frame()'s tag_filter.
            category: Optional exact category match.
            limit: Maximum rows returned (default 50).

        Example::

            for b in await system.ls(tag="self/%"):
                print(b)  # a1b2c3d4 Apply minimum force... [self/value]
        """
        async with self._engine.connect() as conn:
            rows = await list_active_blocks(
                conn, tag=tag, category=category, limit=limit,
            )
            ids = [row["id"] for row in rows]
            tags_map = await get_tags_batch(conn, ids)
        return [
            BlockSummary(
                id=row["id"],
                content=row["content"],
                category=row["category"],
                tags=tags_map.get(row["id"], []),
                created_at=row["created_at"],
                reinforcement_count=row["reinforcement_count"],
            )
            for row in rows
        ]

    async def rescore(
        self, *, max_count: int | None = None,
    ) -> dict[str, int]:
        """Deep-sleep rescoring: refresh aged or unscored active blocks.

        USE WHEN: After ``--no-llm`` ingest (catch up debt); periodically
        to keep the knowledge graph aligned with the agent's evolving
        identity (every block scored against current SELF, not the SELF
        at promotion time).

        DON'T USE WHEN: Hot-path retrieval is in progress on the same DB
        — rescore takes brief write locks per block.

        COST: One LLM call per rescored block. Bounded by ``max_count``
        (default from ``consolidation.rescore.max_per_run``, typically 20).

        RETURNS: ``{"rescored": N, "failed": M, "attempted": N+M}``.

        Update math (v0.17): additive. The new alignment is folded into the
        block's Beta posterior as one weighted evidence event (weight =
        ``memory.rescore_evidence_weight``, default 0.5); ``confidence`` is
        the derived view ``α/(α+β)``. Mature blocks (α+β ≫ 1) barely move;
        cold blocks track the new alignment. Eliminates the v0.15.2 cliff
        where rescore could undo months of outcome evidence in one pass.

        Selection priority:
        - First, all blocks with ``last_scored_at IS NULL`` (debt — drained first).
        - Then, oldest by ``last_scored_at`` (progressive rotation — every
          block leaves the front of the queue once rescored).

        Eligibility: status=active, category not in
        ``rescore.exclude_categories`` (message/mind/decision/prediction),
        ``source_peer IS NULL`` (peer perspectives stay intact), no
        ``rescore.exclude_tags`` (default ``system/no-rescore``), and
        ``last_scored_at IS NULL OR < now - min_age_hours`` (cooldown).
        """
        from elfmem.operations.rescore import (
            RescoreFilter,
            rescore_blocks,
            select_rescore_candidates,
        )
        cfg = self._config.rescore
        if not cfg.enabled:
            return {"rescored": 0, "failed": 0, "attempted": 0}
        budget = max_count if max_count is not None else cfg.max_per_run
        filt = RescoreFilter(
            exclude_categories=tuple(cfg.exclude_categories),
            exclude_tags=tuple(cfg.exclude_tags),
            min_age_hours=cfg.min_age_hours,
            target_max_age_days=cfg.target_max_age_days,
        )
        # Candidate selection is read-only (its own short connection); each
        # candidate is then rescored in its own committed transaction inside
        # rescore_blocks (ADR 0007) — a kill mid-run keeps every block
        # rescored so far instead of losing the whole batch.
        async with self._engine.connect() as conn:
            candidates = await select_rescore_candidates(
                conn, filt=filt, max_count=budget,
            )
        return await rescore_blocks(
            self._engine, block_ids=candidates,
            llm=self._llm, embedding_svc=self._embedding,
            evidence_weight=self._config.memory.rescore_evidence_weight,
            ledger_dir=self._ledger_dir,
        )

    async def metabolism_dry_run(
        self, *, max_count: int | None = None,
    ) -> MetabolismDryRunResult:
        """Propose goal-directed connections without writing anything.

        Edge-metabolism Stage A (docs/plans/plan_edge_metabolism.md): for
        each rescore-eligible block, judges a widened candidate shortlist
        against elf's own ``self/goal`` blocks — not just similarity — and
        reports what it would connect. **Never calls insert_edge.** Stage B
        (applying proposals live, no gate) is a separate, not-yet-approved
        decision; this method exists to gather the evidence that decision
        needs.

        USE WHEN: sanity-checking edge metabolism against real content
            before deciding whether Stage B is worth building.
        DON'T USE WHEN: you want edges actually created — nothing here
            writes to the graph.
        COST: one LLM call per block considered (same eligibility rule and
            budget as ``rescore()`` — default 20/run).
        RETURNS: ``MetabolismDryRunResult`` — proposed connections with
            one-line reasoning each, plus candidate-pool sizes (evidence for
            "is there anything here worth connecting" even before reading
            individual proposals).
        NEXT: read the proposals by hand. This is the validation step the
            Zettelkasten-auto-linking deferral (docs/plans/archive/plan_memory_scoring.md)
            asked for before any live graph-mutation mechanism ships.
        """
        from elfmem.operations.rescore import RescoreFilter, select_rescore_candidates
        from elfmem.operations.rescore import metabolism_dry_run as _metabolism_dry_run

        cfg = self._config.rescore
        budget = max_count if max_count is not None else cfg.max_per_run
        filt = RescoreFilter(
            exclude_categories=tuple(cfg.exclude_categories),
            exclude_tags=tuple(cfg.exclude_tags),
            min_age_hours=cfg.min_age_hours,
            target_max_age_days=cfg.target_max_age_days,
        )
        async with self._engine.connect() as conn:
            candidates = await select_rescore_candidates(conn, filt=filt, max_count=budget)
            return await _metabolism_dry_run(conn, block_ids=candidates, llm=self._llm)

    async def dream(
        self,
        *,
        skip_llm: bool = False,
        rescore: bool = False,
        rescore_max: int | None = None,
        inbox_max: int | None = None,
        host_analyses: dict[str, dict[str, Any]] | None = None,
    ) -> ConsolidateResult | None:
        """Consolidate pending blocks at a natural pause point.

        The breathing rhythm: learn fast (remember), process deliberately (dream).

        USE WHEN: system.should_dream is True, or at any natural pause in agent
        execution (end of a reasoning step, waiting for user input, between tasks).
        Safe to call speculatively — returns None instantly if nothing is pending.

        DON'T USE WHEN: In a tight loop.

        COST: LLM call per pending block (unless skip_llm=True). Returns None
        immediately (zero cost) if inbox is empty. One call processes at most
        ``inbox_max`` blocks (default from ``consolidation.max_inbox_per_run``,
        currently 5 — ADR 0007); a larger backlog drains across repeated
        calls. Check the result's ``inbox_remaining`` and call again if
        nonzero — ``learn_document()`` already loops this way.

        RETURNS: ConsolidateResult if blocks were processed; None if inbox was
        empty. None is not an error — it means "nothing needed doing."

        NEXT: After dream(), newly consolidated blocks are searchable via frame()
        and recall(). The frame cache is cleared automatically. If the result's
        inbox_remaining > 0, call dream() again to continue draining.

        Note: _pending is an advisory counter. If blocks were added externally
        (e.g., another process), dream() may return None despite inbox having
        items. Call status() for DB-accurate inbox_count.

        Args:
            skip_llm: Bypass all LLM calls (embed + promote only). Fastest path
                for bulk ingestion where alignment scoring isn't needed.
                Affected blocks have ``last_scored_at = NULL`` and are picked
                up first by ``rescore=True`` on a future call.
            rescore: After processing inbox, also refresh aged or unscored
                active blocks against the current SELF. Mutually exclusive
                with ``skip_llm`` (rescore needs the LLM by definition).
            rescore_max: Override the per-call rescore budget (default from
                ``consolidation.rescore.max_per_run``).
            inbox_max: Override the per-call inbox-processing budget (default
                from ``consolidation.max_inbox_per_run``). The CLI's ``--max``
                flag maps to both this and ``rescore_max`` in the same
                invocation (ADR 0007) — pass them separately here for
                independent control.
            host_analyses: supply your own alignment_score/tags/summary for
                some or all pending blocks instead of the configured LLM
                adapter — see ``consolidate()``'s ``host_analyses`` and
                ``inbox()`` for the read half of this loop.

        Example::

            # Always-on agent loop
            result = await system.remember("new fact")
            if system.should_dream:
                dream_result = await system.dream()
                if dream_result:
                    print(dream_result)  # Consolidated 5: 4 promoted, 8 edges.
        """
        if rescore and skip_llm:
            from elfmem.exceptions import ConfigError
            raise ConfigError(
                "dream(rescore=True) requires the LLM; cannot combine with skip_llm.",
                recovery="Drop --no-llm; --rescore needs LLM to do its work.",
            )
        if self._pending == 0 and not rescore:
            return None
        result: ConsolidateResult | None = None
        if self._pending > 0:
            result = await self.consolidate(
                skip_llm=skip_llm,
                max_inbox_per_run=inbox_max,
                host_analyses=host_analyses,
            )
        if rescore:
            rs = await self.rescore(max_count=rescore_max)
            if result is None:
                result = ConsolidateResult(
                    processed=0,
                    promoted=0, deduplicated=0, edges_created=0,
                )
            result.rescored = rs["rescored"]
            result.rescore_failed = rs["failed"]
        # Feed policy so it can adapt the threshold for the next cycle,
        # then persist the new threshold so it survives process restarts.
        # Persistence is best-effort: a DB failure here is non-fatal —
        # the adapted threshold is still in memory for this session,
        # and the next restart will use whatever was last successfully saved.
        if self._policy is not None and result is not None:
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
        seed: bool = False,
    ) -> SetupResult:
        """Bootstrap agent identity: optional identity/values, optional constitutional seed.

        USE WHEN: First use — before any other operations. Adds any identity
        description and domain values you provide. Pass seed=True to also
        seed 10 constitutional blocks that form a cognitive loop (curiosity,
        feedback, balance, etc.) — an opinionated starting personality, not
        a requirement.

        DON'T USE WHEN: Every session — SELF blocks persist across restarts.
        Re-running is idempotent: each constitutional block fills a stable
        role (self/role/<name>); seeds whose role is already filled are
        skipped, preserving any user customisation of that slot.

        COST: Fast per block (pure DB insert, no LLM). Each block queues in
        inbox; one LLM call per block during dream()/consolidate().

        RETURNS: SetupResult with blocks_created (new) and total_attempted.
        blocks_created=0 means all were already present — safe, not an error.
        With no identity, no values, and seed=False (the default), this is a
        no-op: total_attempted=0.

        NEXT: SELF blocks sit in inbox until dream() or consolidate() runs.
        After consolidation, frame('self') returns constitutional blocks (if
        seeded) as guaranteed slots, plus any domain values you added. Call
        dream() or let the session context manager handle consolidation
        automatically.

        Args:
            identity: Optional identity description stored as a self/context block.
            values:   Optional list of domain-specific principles, each stored
                      as a separate self/value block.
            seed:     Seed the 10 constitutional blocks (default False — v2
                      step 4: onboarding no longer writes opinionated content
                      before you've expressed a preference). Pass True to
                      seed the constitutional cognitive loop.

        Example::

            result = await system.setup(
                identity="I am a trading assistant focused on risk-adjusted returns.",
                values=["cut losing positions early", "size positions to max 2% risk"],
            )
            print(result)  # Setup complete: 3/3 new blocks created.

            # Opt into the constitutional cognitive loop too:
            result = await system.setup(seed=True)
            print(result)  # Setup complete: 10/10 new blocks created.
        """
        results: list[LearnResult] = []

        if seed:
            from elfmem.seed import CONSTITUTIONAL_SEED
            # Role-based idempotency: a constitutional block fills a specific
            # cognitive slot (its role). If a block tagged self/role/<role>
            # already exists in any non-archived state, the slot is filled —
            # do not re-seed, regardless of whether the user has customised
            # the content. Archived blocks are explicit user retirements and
            # do count as "unfilled" so the agent can re-bootstrap.
            existing_roles = await self._existing_constitutional_roles()
            for block in CONSTITUTIONAL_SEED:
                role = block.get("role", "")
                if role and role in existing_roles:
                    continue
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

    async def _existing_constitutional_roles(self) -> set[str]:
        """Return the set of constitutional roles already filled in this DB.

        A role is "filled" if any block tagged ``self/role/<role>`` exists in
        an active or inbox state (NOT archived — archived means the user
        explicitly retired the block and may want re-seeding). Pure read.
        """
        from sqlalchemy import text
        async with self._engine.connect() as conn:
            rows = await conn.execute(text(
                "SELECT DISTINCT bt.tag "
                "FROM block_tags bt JOIN blocks b ON b.id = bt.block_id "
                "WHERE bt.tag LIKE 'self/role/%' "
                "  AND b.status IN ('active', 'inbox')"
            ))
            tags = [r[0] for r in rows.fetchall()]
        prefix = "self/role/"
        return {t[len(prefix):] for t in tags if t.startswith(prefix)}

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
                recovery="Valid frames: 'self', 'attention', 'task', 'simulate'.",
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
        # The free label tier: recorded by the system, needing nothing from
        # the calling agent. Across three real instances the *voluntary*
        # feedback verb has been called nine times in total, so a history
        # that depends on agent discipline is a history that stays empty.
        if recalled_ids and not result.cached:
            _ledger.record_assembly(
                self._ledger_dir, recalled_ids,
                active_hours=self._current_active_hours(),
                frame=name, session_id=self._session_id,
            )
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
                recovery="Valid frames: 'self', 'attention', 'task', 'simulate'.",
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

        Update math (v0.17): sufficient statistics ``α = success_count`` and
        ``β = failure_count`` are stored on every block. One outcome with
        ``weight=w`` adds ``w·signal`` to α and ``w·(1-signal)`` to β. The
        confidence column is the denormalised view ``α/(α+β)``, always
        consistent within a single transaction. Mature blocks (α+β ≫ 1)
        move slowly; the cold-block prior (α+β = 1.0) tracks the signal
        closely. No "prior strength" knob — the prior mass is implicit in
        the seed at promotion.

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
                reinforce_threshold=mem.outcome_reinforce_threshold,
                edge_reinforce_delta=mem.edge_reinforce_delta,
                penalize_threshold=mem.penalize_threshold,
                penalty_factor=mem.penalty_factor,
                lambda_ceiling=mem.lambda_ceiling,
            )
        for bid in block_ids:
            self._log(_ledger.KIND_OUTCOME, id=bid, sig=signal, w=weight, src=source)
        self._record_op("outcome", result.summary)
        return result

    async def connect(
        self,
        source: str,
        target: str,
        relation: str | None = None,
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
            relation: Semantic type. Core types: 'similar' (used when omitted on
                new edges), 'supports', 'contradicts', 'elaborates', 'co_occurs',
                'outcome'. Any other string is stored as a custom type. Pass None
                (or omit) to reinforce/update without asserting a relation —
                this distinguishes "I don't care" from "I assert similar".
            weight: Edge strength [0.0, 1.0]. None uses the relation-type default.
            note: Optional description of why this connection exists.
            if_exists: 'reinforce' (default) | 'update' | 'skip' | 'error'.

        Raises:
            SelfLoopError: source == target.
            BlockNotActiveError: block not found or not active.
            DegreeLimitError: degree cap full with only protected edges.
            ConnectError: if_exists='error' and edge already exists, OR
                if_exists='reinforce' and an explicit relation conflicts with
                the stored relation (.recovery points to if_exists='update').
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
        relation: str | None = None,
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
        """Prune weak/decayed edges, reinforce top-N knowledge.

        USE WHEN: Explicit maintenance after heavy use, or when retrieval
        quality degrades. Also runs automatically after consolidate() when
        curate_interval_hours has elapsed.

        DON'T USE WHEN: Immediately after consolidate() — auto-curate already
        triggers if the interval elapsed. Don't call after every session.

        COST: Fast. Database operations only; no LLM calls.

        RETURNS: CurateResult with counts: edges_pruned (weak edges removed),
        edges_decayed (temporally-decayed edges removed), reinforced (top-N
        blocks boosted). All zeros means memory was already clean.

        NEXT: Memory is cleaner. Retrieval quality may improve.
        """
        current_hours = self._current_active_hours()
        mem = self._config.memory
        async with self._engine.begin() as conn:
            result = await _curate(
                conn,
                current_active_hours=current_hours,
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

    async def review_constitutional(
        self,
        *,
        drift_threshold: float | None = None,
        max_proposals: int | None = None,
        min_recent_reinforced_blocks: int | None = None,
        window_hours: float | None = None,
        cooldown_hours: float | None = None,
        min_block_evidence: float | None = None,
        min_age_days: float | None = None,
    ) -> ConstitutionalReviewResult:
        """Surface drifted constitutional blocks as LLM-proposed amendments.

        USE WHEN: Periodic (or on-demand) self-examination — check whether
        the agent's tagged ``self/constitutional`` blocks still match its
        empirical operational identity (the centroid of recently-reinforced
        ordinary blocks). MANUAL surface: this only PROPOSES; nothing is
        applied without an explicit ``accept_amendment`` call (commit 4).
        See ADR 0003 for why the cycle is manual rather than automatic.

        DON'T USE WHEN: On a fresh database with little operational history —
        the call returns ``insufficient_history=True`` with no LLM calls,
        which is fine but uninformative.

        COST: O(eligible constitutional blocks) LLM calls, bounded by
        ``max_proposals`` (default 5). Two read-only DB queries up front.
        Returns immediately (no LLM calls) when history is insufficient.

        RETURNS: ConstitutionalReviewResult with ``.proposals`` (sorted by
        drift_score DESC), counts of reviewed/skipped/failed blocks, and
        the ``insufficient_history`` flag. Idempotent under MockLLMService
        for tests; production LLMs may produce slight variation between runs.

        NEXT: For each proposal the agent or user wishes to apply, call
        ``accept_amendment(block_id, proposed_content, ...)`` (commit 4).

        Args:
            drift_threshold: Per-call override of ``review.drift_threshold``.
            max_proposals: Per-call cap on proposals returned.
            min_recent_reinforced_blocks: Override the centroid evidence floor.
            window_hours: Recent-activity window for centroid construction.
            cooldown_hours: Blocks amended within this window are skipped.
            min_block_evidence: Beta posterior α+β floor below which a
                constitutional block is too cold to be amended.
            min_age_days: Minimum wall-clock age before a block can be amended.
        """
        from elfmem.config import ReviewConfig
        from elfmem.operations.review import (
            review_constitutional as _review_constitutional,
        )
        base = self._config.review
        cfg = ReviewConfig(
            drift_threshold=(
                drift_threshold if drift_threshold is not None
                else base.drift_threshold
            ),
            min_recent_reinforced_blocks=(
                min_recent_reinforced_blocks
                if min_recent_reinforced_blocks is not None
                else base.min_recent_reinforced_blocks
            ),
            window_hours=(
                window_hours if window_hours is not None else base.window_hours
            ),
            min_reinforcement=base.min_reinforcement,
            top_n=base.top_n,
            cooldown_hours=(
                cooldown_hours if cooldown_hours is not None
                else base.cooldown_hours
            ),
            max_proposals=(
                max_proposals if max_proposals is not None else base.max_proposals
            ),
            min_block_evidence=(
                min_block_evidence if min_block_evidence is not None
                else base.min_block_evidence
            ),
            min_age_days=(
                min_age_days if min_age_days is not None else base.min_age_days
            ),
        )
        current_hours = self._current_active_hours()
        async with self._engine.begin() as conn:
            result = await _review_constitutional(
                conn, self._llm, cfg,
                current_active_hours=current_hours,
            )
        self._record_op("review_constitutional", result.summary)
        return result

    async def accept_amendment(
        self,
        block_id: str,
        proposed_content: str,
        rationale: str | None = None,
        *,
        drift_score: float | None = None,
        acceptor: str = "agent",
    ) -> AmendmentResult:
        """Apply a proposed amendment to a constitutional block.

        USE WHEN: A ``ProposedAmendment`` from ``review_constitutional()`` has
        been reviewed and the agent or user wants to commit it. Writes the
        new content, captures a pre/post audit row in ``block_amendments``,
        and clears stale ``summary`` / ``last_scored_at`` so the next rescore
        regenerates them against the new content.

        DON'T USE WHEN: You only want to inspect history (``list_amendments``)
        or undo a previous edit (``revert_amendment``).

        COST: One embedding call + one short DB transaction (INSERT amendment
        + UPDATE block). The embedding runs BEFORE the transaction so a slow
        network round-trip never holds a write lock.

        RETURNS: ``AmendmentResult`` with the new ``amendment_id`` and a
        snapshot of pre/post content.

        NEXT: The block is queued for rescore (``last_scored_at = NULL``).
        Optionally call ``dream(rescore=True)`` to refresh alignment/tags
        immediately; otherwise the periodic rescore pass picks it up.

        Args:
            block_id: The constitutional block being amended.
            proposed_content: Replacement content (typically from
                ``ProposedAmendment.proposed_content``).
            rationale: Optional audit rationale (typically from
                ``ProposedAmendment.rationale``).
            drift_score: If supplied, stored in the audit row verbatim.
                When ``None`` the operation recomputes drift against the
                current operational centroid — accurate but adds one
                read query.
            acceptor: ``"agent"`` (default), ``"user"``, or ``"system"``.

        Raises:
            BlockNotFound: ``block_id`` does not exist.
        """
        from elfmem.operations.review import (
            _compute_current_drift,
        )
        from elfmem.operations.review import (
            accept_amendment as _accept,
        )
        if drift_score is None:
            current_hours = self._current_active_hours()
            async with self._engine.connect() as conn:
                drift_score = await _compute_current_drift(
                    conn,
                    block_id=block_id,
                    review_cfg=self._config.review,
                    current_active_hours=current_hours,
                )
        result = await _accept(
            self._engine,
            self._embedding,
            self._frame_cache,
            block_id=block_id,
            proposed_content=proposed_content,
            rationale=rationale,
            drift_score=drift_score,
            acceptor=acceptor,
        )
        self._record_op("accept_amendment", result.summary)
        return result

    async def revert_amendment(self, amendment_id: int) -> AmendmentResult:
        """Undo one amendment: restore the block to its immediate prior state.

        USE WHEN: An amendment was a mistake. Revert is one-step: the block
        returns to whatever ``pre_content`` was when this amendment was
        applied — NOT to the original-from-creation content. To walk
        further back, call ``revert_amendment`` again on the next-older
        amendment.

        DON'T USE WHEN: You want to inspect history first — use
        ``list_amendments``.

        COST: One embedding call (BEFORE the transaction) + a short
        transaction with two UPDATEs.

        RETURNS: ``AmendmentResult`` describing the restoration. The audit
        row gains a non-null ``reverted_at`` rather than being deleted —
        history is preserved.

        NEXT: The block is queued for rescore (``last_scored_at = NULL``).

        Raises:
            AmendmentNotFound: invalid ``amendment_id``.
            AmendmentAlreadyReverted: the row already carries
                ``reverted_at``; the block is already at its pre-amendment
                state.
        """
        from elfmem.operations.review import revert_amendment as _revert
        result = await _revert(
            self._engine,
            self._embedding,
            self._frame_cache,
            amendment_id=amendment_id,
        )
        self._record_op("revert_amendment", result.summary)
        return result

    async def list_amendments(
        self,
        *,
        block_id: str | None = None,
        limit: int = 100,
    ) -> list[AmendmentRecord]:
        """List amendments, newest first; optionally filter by block_id.

        USE WHEN: Inspecting audit history before deciding to revert, or
        surfacing the constitutional change log to the agent.
        DON'T USE WHEN: You only need the most recent ``AmendmentResult`` —
        ``accept_amendment`` already returned it.
        COST: One indexed SELECT bounded by ``limit``.
        RETURNS: list of ``AmendmentRecord`` in ``timestamp DESC`` order;
            empty list when there is no history.
        NEXT: Pick an ``id`` and call ``revert_amendment`` if needed.
        """
        from elfmem.operations.review import (
            list_amendments as _list_amendments,
        )
        return await _list_amendments(
            self._engine, block_id=block_id, limit=limit,
        )

    # ── Peer communication operations ───────────────────────────────────────

    def _resolve_peer_dir(self, kind: Literal["inbox", "outbox"]) -> Path:
        """Resolve the project-local peer inbox/outbox directory.

        Order of resolution:
            1. Explicit override in ``config.peer.<kind>_dir`` (test fixtures,
               unusual deployments). Tilde-expanded.
            2. ``<project_root>/.elfmem/<kind>``, derived once at construction.
            3. Late discovery via ``find_project_root()`` — handles global MCP
               servers initialised without a project context. Result is cached
               back into ``_project_root`` so all subsequent peer calls in this
               session resolve consistently, regardless of cwd changes.

        Raises:
            ProjectNotFound: No override and no project root could be located.
                The recovery hint instructs the caller to run ``elfmem setup``.
        """
        override = (
            self._config.peer.inbox_dir if kind == "inbox"
            else self._config.peer.outbox_dir
        )
        if override:
            return Path(override).expanduser()
        if self._project_root is not None:
            return self._project_root / ".elfmem" / kind
        # Tier 3: late discovery. Result is cached so the inbox path is stable
        # for the lifetime of this instance — cwd drift between calls cannot
        # silently change which project's messages are visible.
        from elfmem.project import find_project_root
        root = find_project_root()
        if root is not None:
            self._project_root = root  # cache: all future peer calls use same root
            return root / ".elfmem" / kind
        raise ProjectNotFound(f"peer {kind}")

    async def peer_init(self, name: str) -> str:
        """Set this instance's peer identity.

        USE WHEN: First-time setup for peer communication.
        COST: Instant. Database write only.
        RETURNS: The DID string (e.g. "elf:my-agent").
        NEXT: Register peers with peer_add().
        """
        from elfmem.db.queries import set_config
        from elfmem.operations.peer import set_identity

        inbox_dir = self._resolve_peer_dir("inbox")
        async with self._engine.begin() as conn:
            did = await set_identity(conn, name)
            await set_config(conn, "peer_inbox_dir", str(inbox_dir))
        self._record_op("peer_init", f"Identity set: {did}")
        return did

    async def peer_add(
        self,
        did: str,
        name: str,
        *,
        is_self: bool = False,
        delivery_path: str | None = None,
    ) -> PeerInfo:
        """Register a peer for communication.

        USE WHEN: You want to exchange knowledge with another elfmem instance.
        DON'T USE WHEN: The peer is already registered (idempotent — returns existing).
        COST: Instant.
        RETURNS: PeerInfo with trust score and statistics.

        Args:
            delivery_path: Filesystem path to the peer's inbox directory.
                When set, peer_send() writes directly there (instant delivery).
                When None, messages go to the local outbox for manual transport.
        """
        from elfmem.db.queries import get_peer, insert_peer

        async with self._engine.begin() as conn:
            existing = await get_peer(conn, did)
            if existing is None:
                await insert_peer(
                    conn, did=did, name=name, is_self=is_self,
                    delivery_path=delivery_path,
                )
                existing = await get_peer(conn, did)
        result = _peer_row_to_info(existing)  # type: ignore[arg-type]
        self._record_op("peer_add", result.summary)
        return result

    async def peer_remove(self, did: str) -> bool:
        """Unregister a peer. Returns True if the peer existed.

        USE WHEN: Removing a peer you no longer communicate with.
        COST: Instant.
        """
        from elfmem.db.queries import delete_peer

        async with self._engine.begin() as conn:
            removed = await delete_peer(conn, did)
        self._record_op("peer_remove", f"{'Removed' if removed else 'Not found'}: {did}")
        return removed

    async def peer_list(self) -> list[PeerInfo]:
        """List all registered peers with trust scores and statistics.

        USE WHEN: Discovering which peers are available for communication.
        COST: Fast. Database reads only.
        RETURNS: list[PeerInfo].
        """
        from elfmem.db.queries import get_all_peers

        async with self._engine.connect() as conn:
            rows = await get_all_peers(conn)
        result = [_peer_row_to_info(r) for r in rows]
        self._record_op("peer_list", f"{len(result)} peer(s) registered.")
        return result

    async def peer_trust(
        self, did: str, *, set_value: float | None = None,
    ) -> PeerInfo:
        """Get or set trust for a peer.

        USE WHEN: Inspecting or manually adjusting trust for a peer.
        COST: Instant.
        RETURNS: PeerInfo with current trust score.
        """
        from elfmem.db.queries import get_peer, update_peer_trust

        async with self._engine.begin() as conn:
            if set_value is not None:
                await update_peer_trust(conn, did, set_value)
            peer = await get_peer(conn, did)
        if peer is None:
            from elfmem.exceptions import PeerError
            raise PeerError(
                f"Peer not found: {did}",
                recovery=f"Register first: elfmem peer add {did} --name <name>",
            )
        result = _peer_row_to_info(peer)
        self._record_op("peer_trust", result.summary)
        return result

    async def peer_send(
        self,
        to_peer: str,
        content: str,
        *,
        in_reply_to: str | None = None,
    ) -> PeerSendResult:
        """Send a message to a peer. Heartbeat speed.

        USE WHEN: Communicating with another elfmem instance.
        DON'T USE WHEN: Sharing bulk knowledge — use export_blocks() instead.
        COST: Instant. No LLM calls. Creates a message block + outbox file.
        RETURNS: PeerSendResult with msg_id and outbox path.
        NEXT: Transport the outbox file to the peer's inbox directory.
        """
        from elfmem.operations.peer import get_identity, send_message

        outbox_dir = self._resolve_peer_dir("outbox")
        async with self._engine.begin() as conn:
            identity = await get_identity(conn)
            result = await send_message(
                conn,
                to_peer=to_peer,
                content=content,
                in_reply_to=in_reply_to,
                identity=identity,
                outbox_dir=outbox_dir,
            )
        self._pending += 1
        self._last_learned_block_id = result.block_id
        self._record_op("peer_send", result.summary)
        return result

    async def peer_inbox(
        self,
        *,
        from_peer: str | None = None,
        import_all: bool = False,
    ) -> PeerInboxResult:
        """Check and optionally import pending messages from peers.

        USE WHEN: Starting a session or periodically checking for messages.
        COST: Fast. Filesystem scan + optional database writes.
        RETURNS: PeerInboxResult with message counts and peer list.
        NEXT: Run dream() to consolidate imported messages into active memory.
        """
        from elfmem.operations.peer import check_inbox, get_identity

        inbox_dir = self._resolve_peer_dir("inbox")
        async with self._engine.begin() as conn:
            identity = await get_identity(conn)
            result = await check_inbox(
                conn,
                inbox_dir=inbox_dir,
                from_peer=from_peer,
                import_messages=import_all,
                identity=identity,
            )
        if result.messages_imported > 0:
            self._pending += result.messages_imported
        self._record_op("peer_inbox", result.summary)
        return result

    def peer_inbox_status(self) -> PeerInboxStatus:
        """Check whether peer messages are waiting without importing them.

        USE WHEN: Deciding whether to trigger a peer message processing session.
        DON'T USE WHEN: You need message content — use peer_inbox() or
            frame(frame="task") instead.
        COST: Zero LLM calls. Pure filesystem scan. No database access.
        RETURNS: PeerInboxStatus with pending count, sender DIDs, and timing.
        NEXT: If pending > 0, call peer_inbox(import_all=True) or fire
            the processing prompt.
        """
        from elfmem.operations.peer import scan_peer_inbox

        inbox_dir = self._resolve_peer_dir("inbox")
        result = scan_peer_inbox(inbox_dir)
        self._record_op("peer_inbox_status", result.summary)
        return result

    async def export_blocks(
        self,
        *,
        share_level: str = "public",
        min_confidence: float = 0.0,
        output_path: str,
    ) -> ExportResult:
        """Export shareable blocks as a JSON bundle.

        USE WHEN: Sharing knowledge with another elfmem instance.
        DON'T USE WHEN: Sending a single message — use peer_send() instead.
        COST: Fast. Database reads + file write.
        RETURNS: ExportResult with counts and output path.
        NEXT: Transfer the bundle file to the receiving instance.
        """
        from elfmem.operations.peer import export_blocks as _export
        from elfmem.operations.peer import get_identity

        async with self._engine.connect() as conn:
            identity = await get_identity(conn)
            result = await _export(
                conn,
                share_level=share_level,
                min_confidence=min_confidence,
                identity=identity,
                output_path=output_path,
            )
        self._record_op("export_blocks", result.summary)
        return result

    async def import_blocks(
        self,
        path: str,
        *,
        from_peer: str | None = None,
        self_merge: bool = False,
    ) -> ImportResult:
        """Import a block bundle with provenance tracking.

        USE WHEN: Receiving knowledge from another elfmem instance.
        DON'T USE WHEN: The bundle is from an untrusted, unregistered peer.
        COST: Fast. Database writes. Imported blocks enter inbox.
        RETURNS: ImportResult with counts.
        NEXT: Run dream() to consolidate imported blocks.
        """
        import json as _json
        from pathlib import Path

        from elfmem.operations.peer import import_bundle

        bundle_path = Path(path).expanduser()
        bundle_data = _json.loads(bundle_path.read_text(encoding="utf-8"))
        peer_did = from_peer or bundle_data.get("from_did", "unknown")

        async with self._engine.begin() as conn:
            result = await import_bundle(
                conn,
                bundle_data=bundle_data,
                from_peer=peer_did,
                is_self_merge=self_merge,
                confidence_floor=self._config.peer.confidence_floor,
            )
        if result.blocks_imported > 0:
            self._pending += result.blocks_imported
        self._record_op("import_blocks", result.summary)
        return result

    # ── Mind (Theory of Mind) operations ───────────────────────────────────

    async def mind_create(
        self,
        subject: str,
        *,
        goals: list[str] | None = None,
        beliefs: list[str] | None = None,
        fears: list[str] | None = None,
        motivations: list[str] | None = None,
    ) -> LearnResult:
        """Create a Theory of Mind block for a subject.

        USE WHEN: You need to model another agent's or person's mind —
        their goals, beliefs, fears, and motivations — as an explicit,
        falsifiable representation.

        DON'T USE WHEN: Storing general knowledge about someone. Use
        learn() for facts; mind_create() is for structured mental models
        that will generate predictions.

        COST: Instant. No LLM calls. Block goes to inbox.

        RETURNS: LearnResult with the mind block ID. Category is "mind",
        decay tier is DURABLE (~6 month half-life).

        NEXT: Add predictions with mind_predict(). Retrieve with
        frame("simulate") to reason about the modelled mind.
        """
        from elfmem.operations.mind import create_mind

        async with self._engine.begin() as conn:
            result = await create_mind(
                conn,
                subject=subject,
                goals=goals,
                beliefs=beliefs,
                fears=fears,
                motivations=motivations,
            )
        if result.status in ("created", "near_duplicate_superseded"):
            self._pending += 1
        if result.status == "created":
            self._last_learned_block_id = result.block_id
        self._record_op("mind_create", result.summary)
        return result

    async def mind_predict(
        self,
        mind_block_id: str,
        prediction: str,
        *,
        verify_at: str,
        reasoning: str | None = None,
    ) -> MindPredictResult:
        """Add a falsifiable prediction linked to a mind block.

        USE WHEN: You have a specific, testable hypothesis about what a
        modelled mind will do. Predictions require a verify_at date.

        DON'T USE WHEN: The claim is unfalsifiable or has no verification
        date. Casual observations go in learn().

        COST: Instant. Creates a decision block + predicts edge.

        RETURNS: MindPredictResult with decision_block_id and edge status.

        NEXT: When the prediction resolves, call mind_outcome() with the
        decision_block_id and hit=True/False.
        """
        from elfmem.operations.mind import predict

        async with self._engine.begin() as conn:
            result = await predict(
                conn,
                mind_block_id=mind_block_id,
                prediction=prediction,
                verify_at=verify_at,
                reasoning=reasoning,
                edge_degree_cap=self._config.memory.edge_degree_cap,
                edge_reinforce_delta=self._config.memory.edge_reinforce_delta,
                current_active_hours=self._current_active_hours(),
            )
        self._pending += 1  # decision block goes to inbox
        self._last_learned_block_id = result.decision_block_id
        self._record_op("mind_predict", result.summary)
        return result

    async def mind_list(self) -> list[MindSummary]:
        """List all active mind blocks with prediction statistics.

        USE WHEN: Discovering which minds are modelled and their calibration.

        COST: Fast. Database reads only.

        RETURNS: list[MindSummary] with subject, confidence, prediction
        counts, and hit/miss ratios.
        """
        from elfmem.operations.mind import list_minds

        async with self._engine.connect() as conn:
            result = await list_minds(conn)
        self._record_op("mind_list", f"{len(result)} mind(s) found.")
        return result

    async def mind_show(self, mind_block_id: str) -> MindShowResult:
        """Show a mind block with all linked predictions.

        USE WHEN: Inspecting a specific mind model before reasoning about
        it or before running simulate frame retrieval.

        COST: Fast. Database reads only.

        RETURNS: MindShowResult with content, predictions, and outcomes.
        """
        from elfmem.operations.mind import show_mind

        async with self._engine.connect() as conn:
            result = await show_mind(conn, mind_block_id)
        self._record_op("mind_show", result.summary)
        return result

    async def mind_outcome(
        self,
        decision_block_id: str,
        *,
        hit: bool,
        reason: str,
    ) -> MindOutcomeResult:
        """Close a prediction: record hit/miss, calibrate the mind model.

        USE WHEN: A prediction has resolved — the verify_at date has
        passed and you can observe whether the prediction was correct.

        DON'T USE WHEN: The prediction hasn't resolved yet. Don't
        speculate — wait for observable evidence.

        COST: Fast. Database writes only. Updates confidence on both
        the decision block and the linked mind block.

        RETURNS: MindOutcomeResult with confidence deltas and edge status.

        NEXT: The mind model's confidence is now calibrated. Future
        simulate frame retrievals reflect the updated model accuracy.
        """
        from elfmem.operations.mind import mind_outcome as _mind_outcome

        async with self._engine.begin() as conn:
            result = await _mind_outcome(
                conn,
                decision_block_id=decision_block_id,
                hit=hit,
                reason=reason,
                current_active_hours=self._current_active_hours(),
                reinforce_threshold=self._config.memory.outcome_reinforce_threshold,
                edge_reinforce_delta=self._config.memory.edge_reinforce_delta,
                edge_degree_cap=self._config.memory.edge_degree_cap,
            )
        self._record_op("mind_outcome", result.summary)
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


def _peer_row_to_info(row: dict[str, Any]) -> PeerInfo:
    """Convert a peer_roster row dict to a PeerInfo result type."""
    return PeerInfo(
        did=row["did"],
        name=row["name"],
        trust=row["trust"],
        is_self=bool(row["is_self"]),
        first_contact=row["first_contact"],
        last_active=row["last_active"],
        blocks_imported=row["blocks_imported"],
        blocks_exported=row["blocks_exported"],
        messages_in=row["messages_in"],
        messages_out=row["messages_out"],
        delivery_path=row.get("delivery_path"),
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


def _discover_project_root(
    config: ElfmemConfig | str | dict[str, Any] | None,
) -> Path | None:
    """Find the project root for peer-path derivation.

    Priority: explicit config-file path → ELFMEM_CONFIG env → walk up from cwd.
    A config file at ``<root>/.elfmem/config.yaml`` makes ``<root>`` the project
    root. Returns ``None`` when no project marker is found anywhere; peer
    operations then fail fast with ``ProjectNotFound``.
    """
    from elfmem.project import find_project_root

    explicit_path: str | None = None
    if isinstance(config, str):
        explicit_path = config
    elif config is None:
        explicit_path = os.getenv("ELFMEM_CONFIG")

    if explicit_path:
        cfg_path = Path(explicit_path).expanduser().resolve()
        # <root>/.elfmem/config.yaml → <root>
        if cfg_path.parent.name == ".elfmem":
            candidate = cfg_path.parent.parent
            # Guard: ~/.elfmem/config.yaml is the global data dir, not a project.
            # Returning ~ here would make all peer paths resolve to ~/.elfmem/inbox,
            # silently hiding project-local messages.
            if candidate.resolve() != Path.home().resolve():
                return candidate
            # Fall through to find_project_root — global config is not a project.
        # Fall through: explicit config not under .elfmem/, treat its directory
        # as project root only if it carries a project marker.
        return find_project_root(cfg_path.parent)

    return find_project_root()


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
