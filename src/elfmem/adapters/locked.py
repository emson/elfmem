"""Lock-enforcing wrapper around an EmbeddingService.

The wrapper is the single enforcement point for embedding-model integrity.
On every ``embed()`` / ``embed_batch()`` call it verifies that the adapter's
``model_name`` and the produced vector's length match the locked values
stored in ``system_config``. On the first call against a fresh DB, the
lock is set atomically.

The wrapper is applied inside ``MemorySystem.from_config()`` and is
deliberately bypassed by the admin ``elfmem migrate-embeddings`` command,
which uses a bare ``EmbeddingService`` to re-embed under a new model
without self-blocking against its own safety system.

See ``docs/plans/plan_embedding_lock.md`` for the full design rationale.
"""

from __future__ import annotations

import numpy as np
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from elfmem.exceptions import EmbeddingLockError
from elfmem.ports.services import EmbeddingService


class LockedEmbeddingService:
    """Wraps an ``EmbeddingService`` and refuses to embed if the adapter's
    model disagrees with the DB lock.

    No internal cache: every embed call runs one ``SELECT`` against the
    two-row ``system_config`` lookup (sub-millisecond) before returning
    the inner adapter's result. This eliminates a staleness bug that a
    per-session cache would otherwise create for long-lived MCP-server
    sessions, where ``elfmem migrate-embeddings`` running in another
    process could change the lock without the cache ever refreshing.
    """

    def __init__(self, inner: EmbeddingService, engine: AsyncEngine) -> None:
        self._inner = inner
        self._engine = engine

    @property
    def model_name(self) -> str:
        return self._inner.model_name

    async def embed(self, text: str) -> np.ndarray:  # noqa: A002 — matches protocol
        vec = await self._inner.embed(text)
        await self._verify(vec)
        return vec

    async def embed_batch(self, texts: list[str]) -> list[np.ndarray]:
        vecs = await self._inner.embed_batch(texts)
        if vecs:
            await self._verify(vecs[0])
        return vecs

    async def _verify(self, vec: np.ndarray) -> None:
        """Verify the adapter's (model_name, len(vec)) matches the lock,
        or set the lock if absent. Raises ``EmbeddingLockError`` on
        mismatch with a recovery hint.
        """
        select_lock = text(
            "SELECT key, value FROM system_config "
            "WHERE key IN ('embedding_model_lock', 'embedding_dimensions_lock')"
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(select_lock)).all()
        stored: dict[str, str] = {str(r[0]): str(r[1]) for r in rows}
        stored_model = stored.get("embedding_model_lock", "")

        # First-ever embed: set the lock atomically (race-safe).
        if not stored_model:
            async with self._engine.begin() as conn:
                await conn.execute(
                    text(
                        "INSERT OR IGNORE INTO system_config (key, value) VALUES "
                        "('embedding_model_lock', :m), "
                        "('embedding_dimensions_lock', :d)"
                    ),
                    {"m": self._inner.model_name, "d": str(len(vec))},
                )
                rows = (await conn.execute(select_lock)).all()
            stored = {str(r[0]): str(r[1]) for r in rows}
            stored_model = stored.get("embedding_model_lock", "")

        stored_dims = int(stored.get("embedding_dimensions_lock") or "0")
        if stored_model != self._inner.model_name or stored_dims != len(vec):
            raise EmbeddingLockError(
                f"Embedding model mismatch: DB locked to "
                f"({stored_model!r}, {stored_dims}-dim) but adapter is configured "
                f"for ({self._inner.model_name!r}, {len(vec)}-dim). Cosines "
                "between vectors from different models are noise.",
                recovery=(
                    f"Either edit embeddings.model in config.yaml to "
                    f"{stored_model!r} and restart (adopt DB), OR run "
                    f"`elfmem migrate-embeddings --execute` to re-embed "
                    f"all blocks under {self._inner.model_name!r} (destructive — "
                    "see the estimate first by running it with no flags)."
                ),
            )
