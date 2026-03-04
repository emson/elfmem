"""MemorySystem public API — thin façade over all layers."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine

from elfmem.adapters.litellm import LiteLLMAdapter, LiteLLMEmbeddingAdapter
from elfmem.config import ElfmemConfig
from elfmem.context.frames import FrameCache, get_frame_definition
from elfmem.db.engine import create_engine
from elfmem.db.models import metadata
from elfmem.db.queries import seed_builtin_data
from elfmem.memory.retrieval import hybrid_retrieve
from elfmem.operations.consolidate import consolidate
from elfmem.operations.curate import curate as _curate
from elfmem.operations.curate import should_curate
from elfmem.operations.learn import learn as _learn
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
    ScoredBlock,
)


class MemorySystem:
    """Adaptive memory system for LLM agents.

    Typical usage::

        system = await MemorySystem.from_config("agent.db")
        async with system.session():
            await system.learn("I prefer explicit error handling.")
            result = await system.frame("attention", query="error handling")
            print(result.text)

    All methods require an active session (started via :meth:`session` or
    :meth:`begin_session`).
    """

    def __init__(
        self,
        *,
        engine: AsyncEngine,
        llm_service: LLMService,
        embedding_service: EmbeddingService,
        config: ElfmemConfig | None = None,
    ) -> None:
        self._engine = engine
        self._llm = llm_service
        self._embedding = embedding_service
        self._config = config or ElfmemConfig()
        self._frame_cache = FrameCache()
        self._session_id: str | None = None

    # ── Factory methods ──────────────────────────────────────────────────────

    @classmethod
    async def from_config(
        cls,
        db_path: str,
        config: ElfmemConfig | str | dict[str, Any] | None = None,
    ) -> MemorySystem:
        """Create a MemorySystem from configuration.

        This is the primary entry point for users. Handles all wiring:
        database engine, LLM adapter, embedding adapter.

        Args:
            db_path: Path to SQLite database file (created if not exists).
            config: Configuration source:
                - None: reads ELFMEM_CONFIG env var for YAML path, or uses defaults
                - str: path to YAML config file
                - dict: configuration values (validated by Pydantic)
                - ElfmemConfig: pre-built config object

        Returns:
            Fully configured MemorySystem, ready for session().

        Example::

            system = await MemorySystem.from_config("agent.db")
            system = await MemorySystem.from_config("agent.db", "elfmem.yaml")
            system = await MemorySystem.from_config(
                "agent.db",
                {"llm": {"model": "ollama/llama3.2", "base_url": "http://localhost:11434"}}
            )
        """
        cfg = _resolve_config(config)

        # Create engine and initialise schema
        engine = await create_engine(db_path)
        async with engine.begin() as conn:
            await conn.run_sync(metadata.create_all)
            await seed_builtin_data(conn)

        llm_svc = LiteLLMAdapter(
            model=cfg.llm.model,
            temperature=cfg.llm.temperature,
            max_tokens=cfg.llm.max_tokens,
            timeout=cfg.llm.timeout,
            max_retries=cfg.llm.max_retries,
            base_url=cfg.llm.base_url,
            alignment_model=cfg.llm.alignment_model,
            tags_model=cfg.llm.tags_model,
            contradiction_model=cfg.llm.contradiction_model,
            alignment_prompt=cfg.prompts.resolve_self_alignment(),
            tag_prompt=cfg.prompts.resolve_self_tags(),
            contradiction_prompt=cfg.prompts.resolve_contradiction(),
            valid_self_tags=cfg.prompts.resolve_valid_tags(),
        )

        embedding_svc = LiteLLMEmbeddingAdapter(
            model=cfg.embeddings.model,
            dimensions=cfg.embeddings.dimensions,
            timeout=cfg.embeddings.timeout,
            base_url=cfg.embeddings.base_url,
        )

        return cls(
            engine=engine,
            llm_service=llm_svc,
            embedding_service=embedding_svc,
            config=cfg,
        )

    @classmethod
    async def from_env(cls, db_path: str) -> MemorySystem:
        """Create a MemorySystem from ELFMEM_ environment variables.

        Convenience wrapper around from_config with env-based config.
        """
        cfg = ElfmemConfig.from_env()
        return await cls.from_config(db_path, cfg)

    # ── Session management ───────────────────────────────────────────────────

    @asynccontextmanager
    async def session(
        self,
        task_type: str = "general",
    ) -> AsyncIterator[MemorySystem]:
        """Async context manager that wraps a single interaction session.

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
            inbox_threshold = self._config.memory.inbox_threshold
            async with self._engine.begin() as conn:
                from elfmem.db.queries import get_inbox_count
                count = await get_inbox_count(conn)
                if count >= inbox_threshold:
                    await self.consolidate()
            await self.end_session()
            self._frame_cache.clear()

    async def begin_session(self, task_type: str = "general") -> str:
        """Start a session. Returns session_id."""
        async with self._engine.begin() as conn:
            session_id = await _begin_session(conn, task_type=task_type)
        self._session_id = session_id
        return session_id

    async def end_session(self) -> float:
        """End the current session. Returns session duration in active hours."""
        if self._session_id is None:
            return 0.0
        async with self._engine.begin() as conn:
            duration = await _end_session(conn, self._session_id)
        self._session_id = None
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
        """Ingest a new piece of knowledge into the inbox.

        Args:
            content: Text content to store.
            tags: Optional initial tags (self/* tags assigned during consolidate).
            category: Block category ("knowledge", "observation", etc.).
            source: Source label (e.g., "api", "tool_result", "user").

        Returns:
            LearnResult with block_id and status.
        """
        async with self._engine.begin() as conn:
            return await _learn(conn, content=content, tags=tags, category=category, source=source)

    async def consolidate(self) -> ConsolidateResult:
        """Run inbox processing: score, embed, promote, dedup, build edges.

        Processes all inbox blocks through the full consolidation pipeline.
        Also runs curate() if the curate interval has elapsed.
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
            )

            # Auto-curate if interval elapsed
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

        return result

    async def frame(
        self,
        name: str,
        query: str | None = None,
        *,
        top_k: int | None = None,
    ) -> FrameResult:
        """Retrieve and render context for the named frame.

        Args:
            name: Frame name ("self", "attention", "task").
            query: Query text (required for ATTENTION, optional for TASK).
            top_k: Number of blocks to return. Defaults to config.memory.top_k.

        Returns:
            FrameResult with .text (rendered string) and .blocks (scored blocks).
        """
        k = top_k if top_k is not None else self._config.memory.top_k
        frame_def = get_frame_definition(name)
        current_hours = compute_current_active_hours()

        async with self._engine.begin() as conn:
            return await _recall(
                conn,
                embedding_svc=self._embedding,
                frame_def=frame_def,
                query=query,
                current_active_hours=current_hours,
                top_k=k,
                cache=self._frame_cache,
            )

    async def recall(
        self,
        query: str | None = None,
        *,
        top_k: int | None = None,
        frame: str = "attention",
    ) -> list[ScoredBlock]:
        """Raw retrieval without rendering. No reinforcement side effects.

        Power-user method for inspection.

        Args:
            query: Query text (None for queryless retrieval).
            top_k: Number of blocks to return.
            frame: Frame name for weights/filters. Default "attention".

        Returns:
            List of ScoredBlock objects, sorted by score descending.
        """
        k = top_k if top_k is not None else self._config.memory.top_k
        frame_def = get_frame_definition(frame)
        current_hours = compute_current_active_hours()

        if query is None:
            weights = frame_def.weights.renormalized_without_similarity()
        else:
            weights = frame_def.weights

        tag_filter: str | None = None
        if frame_def.filters.tag_patterns:
            tag_filter = frame_def.filters.tag_patterns[0]

        async with self._engine.begin() as conn:
            return await hybrid_retrieve(
                conn,
                embedding_svc=self._embedding,
                query=query,
                weights=weights,
                current_active_hours=current_hours,
                top_k=k,
                tag_filter=tag_filter,
                search_window_hours=frame_def.filters.search_window_hours,
            )

    async def curate(self) -> CurateResult:
        """Run manual maintenance: archive decayed blocks, prune edges, reinforce top-N."""
        current_hours = compute_current_active_hours()
        mem = self._config.memory
        async with self._engine.begin() as conn:
            return await _curate(
                conn,
                current_active_hours=current_hours,
                prune_threshold=mem.prune_threshold,
                edge_prune_threshold=mem.edge_prune_threshold,
                reinforce_top_n=mem.curate_reinforce_top_n,
            )

    async def close(self) -> None:
        """Dispose the database engine. Call when done with this MemorySystem."""
        await self._engine.dispose()


# ── Helpers ───────────────────────────────────────────────────────────────────

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
