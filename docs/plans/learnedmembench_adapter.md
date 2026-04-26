# LearnedMemBench Adapter for elfmem

## Overview

The LMB adapter lives at `benchmarks/learnedmembench/adapter.py`, following the
same pattern as the existing LoCoMo and MABench adapters. It implements the
`LearnedMemoryAdapter` protocol defined by the external `learnedmembench`
package, wiring each protocol method to elfmem's public API.

The adapter is thin — elfmem already has direct API equivalents for every
protocol method. The main work is mapping between LMB's protocol types and
elfmem's types, plus exposing state introspection (which elfmem supports
internally but doesn't yet expose publicly).

## File Structure

```
benchmarks/learnedmembench/
├── adapter.py       # LearnedMemoryAdapter implementation
├── config.py        # LMBenchConfig (embedding model, LLM model, etc)
└── __init__.py
```

## Protocol → elfmem API Mapping

Every protocol method maps directly to an existing elfmem operation:

```
Protocol method          elfmem API               Notes
─────────────────────────────────────────────────────────────────
learn(content, tags)     system.learn(content,     Direct. Returns block ID
                           tags=tags)                from LearnResult.block_id

recall(query, top_k)     system.recall(query=,     Direct. Map ScoredBlock →
                           top_k=)                   protocol RetrievedBlock

consolidate()            system.consolidate()      Direct. No return needed

curate()                 system.curate()           Direct. No return needed

begin_session()          system.begin_session()    Direct

end_session()            system.end_session()      Direct

outcome(ids, signal)     system.outcome(ids,       Direct
                           signal=signal)

setup_identity(values)   system.setup(values=      Direct
                           values)

capabilities()           Return fixed set           See §Capabilities below

get_block_state(id)      queries.get_block(conn,   Needs new public API
                           id)                       — see §State Introspection

get_contradictions()     queries.get_contra-       Needs new public API
                           dictions_for_blocks()

get_edges()              queries.get_edges_for_    Needs new public API
                           block() / get_all_edges
```

## Capabilities Declaration

elfmem supports all LMB capabilities:

```python
def capabilities(self) -> set[str]:
    return {
        "consolidation",
        "decay",
        "contradiction",
        "graph",
        "sessions",
        "outcome",
        "identity",
        "curate",
        "state_introspection",
    }
```

## Implementation

### adapter.py

```python
"""elfmem adapter for LearnedMemBench."""

from __future__ import annotations

import tempfile
from pathlib import Path

from elfmem import ElfmemConfig, MemorySystem

from benchmarks.learnedmembench.config import LMBenchConfig

# Import the protocol types from the external learnedmembench package.
# These will be defined by learnedmembench — shown here as reference.
#
# from learnedmembench.protocol import (
#     LearnedMemoryAdapter,
#     RetrievedBlock,
#     BlockState,
#     ContradictionRecord,
#     EdgeRecord,
# )


class ElfmemLMBAdapter:
    """LearnedMemoryAdapter implementation for elfmem.

    Lifecycle: create via `ElfmemLMBAdapter.create()`, use, then `close()`.
    Or use as async context manager.
    """

    def __init__(self, system: MemorySystem, db_path: str) -> None:
        self._system = system
        self._db_path = db_path

    @classmethod
    async def create(cls, config: LMBenchConfig) -> ElfmemLMBAdapter:
        elfmem_cfg = build_elfmem_config(config)
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            db_path = tmp.name
        system = await MemorySystem.from_config(db_path, config=elfmem_cfg)
        return cls(system, db_path)

    async def close(self) -> None:
        await self._system.close()
        for suffix in ["", "-wal", "-shm"]:
            p = Path(self._db_path + suffix)
            if p.exists():
                p.unlink()

    # ── Required operations ────────────────────────────────────

    async def learn(self, content: str, tags: list[str]) -> str:
        result = await self._system.learn(content=content, tags=tags)
        return result.block_id

    async def recall(self, query: str, top_k: int) -> list[dict]:
        blocks = await self._system.recall(query=query, top_k=top_k)
        return [
            {
                "id": b.id,
                "content": b.content,
                "score": b.score,
                "similarity": b.similarity,
                "confidence": b.confidence,
                "recency": b.recency,
                "tags": b.tags,
            }
            for b in blocks
        ]

    # ── Optional lifecycle operations ──────────────────────────

    async def consolidate(self) -> None:
        await self._system.consolidate()

    async def curate(self) -> None:
        await self._system.curate()

    async def begin_session(self) -> None:
        await self._system.begin_session()

    async def end_session(self) -> None:
        await self._system.end_session()

    async def outcome(self, block_ids: list[str], signal: float) -> None:
        await self._system.outcome(block_ids, signal=signal)

    async def setup_identity(self, values: list[str]) -> None:
        await self._system.setup(values=values)

    # ── Capability declaration ─────────────────────────────────

    def capabilities(self) -> set[str]:
        return {
            "consolidation", "decay", "contradiction", "graph",
            "sessions", "outcome", "identity", "curate",
            "state_introspection",
        }

    # ── State introspection ────────────────────────────────────
    # These methods require new public API on MemorySystem.
    # See §What elfmem Needs below.

    async def get_block_state(self, block_id: str) -> dict | None:
        return await self._system.get_block(block_id)

    async def get_contradictions(self) -> list[dict]:
        return await self._system.get_contradictions()

    async def get_edges(self) -> list[dict]:
        return await self._system.get_edges()


def build_elfmem_config(config: LMBenchConfig) -> ElfmemConfig:
    return ElfmemConfig.model_validate({
        "llm": {
            "model": config.llm_model,
            "base_url": config.base_url,
        },
        "embeddings": {
            "model": config.embedding_model,
            "base_url": config.base_url,
            "dimensions": config.embedding_dimensions,
        },
        "memory": {
            "inbox_threshold": config.inbox_threshold,
            "top_k": config.top_k,
            "search_window_hours": 100000.0,
            "curate_interval_hours": 100000.0,
        },
    })
```

### config.py

```python
"""Configuration for LearnedMemBench adapter."""

from dataclasses import dataclass


@dataclass
class LMBenchConfig:
    base_url: str = "http://localhost:1234/v1"
    llm_model: str = "google/gemma-4-26b-a4b"
    embedding_model: str = "text-embedding-nomic-embed-text-v1.5"
    embedding_dimensions: int = 768
    top_k: int = 10
    inbox_threshold: int = 50
```

## What elfmem Needs

The adapter is almost entirely passthrough. Three state introspection methods
need new public API on `MemorySystem`. The internal `queries.py` functions
already exist — they just need thin public wrappers.

### 1. `get_block(block_id) -> dict | None`

**Internal function**: `queries.get_block(conn, block_id)` at
`src/elfmem/db/queries.py:85`

**What to add to `api.py`**:

```python
async def get_block(self, block_id: str) -> dict[str, Any] | None:
    """Return raw block state for introspection.

    USE WHEN: Benchmarks or debugging need internal block state.
    DON'T USE WHEN: You want retrieval results — use recall().
    COST: Single DB query.
    RETURNS: Block dict with id, content, status, confidence,
             decay_lambda, reinforcement_count, last_reinforced_at,
             embedding. None if not found.
    NEXT: Interpret state for verification.
    """
    async with self._engine.connect() as conn:
        return await queries.get_block(conn, block_id)
```

### 2. `get_contradictions() -> list[dict]`

**Internal function**: `queries.get_contradictions_for_blocks(conn, block_ids)`
at `src/elfmem/db/queries.py:742`. Currently requires block IDs — need a
variant that returns all contradictions.

**What to add**:

First, add `get_all_contradictions()` to `queries.py`:

```python
async def get_all_contradictions(conn: AsyncConnection) -> list[dict[str, Any]]:
    """Return all contradiction records."""
    result = await conn.execute(
        text("SELECT * FROM contradictions")
    )
    return [dict(row._mapping) for row in result]
```

Then expose on `api.py`:

```python
async def get_contradictions(self) -> list[dict[str, Any]]:
    """Return all detected contradiction records.

    USE WHEN: Benchmarks need to verify contradiction detection.
    DON'T USE WHEN: You want retrieval — contradictions are already
                    suppressed in recall().
    COST: Single DB query.
    RETURNS: List of dicts with block_a_id, block_b_id, score, resolved.
    NEXT: Compare against expected contradictions.
    """
    async with self._engine.connect() as conn:
        return await queries.get_all_contradictions(conn)
```

### 3. `get_edges() -> list[dict]`

**Internal function**: `queries.get_edges_for_block(conn, block_id)` at
`src/elfmem/db/queries.py:468`. Currently per-block — need a variant that
returns all edges.

**What to add**:

First, add `get_all_edges()` to `queries.py`:

```python
async def get_all_edges(conn: AsyncConnection) -> list[dict[str, Any]]:
    """Return all edges in the knowledge graph."""
    result = await conn.execute(
        text("SELECT * FROM edges")
    )
    return [dict(row._mapping) for row in result]
```

Then expose on `api.py`:

```python
async def get_edges(self) -> list[dict[str, Any]]:
    """Return all edges in the knowledge graph.

    USE WHEN: Benchmarks need to verify graph formation.
    DON'T USE WHEN: You want retrieval — graph expansion is already
                    part of recall().
    COST: Single DB query. O(edges) result size.
    RETURNS: List of dicts with from_id, to_id, weight, relation_type,
             origin, reinforcement_count, last_active_hours.
    NEXT: Analyse graph topology or compare against expected edges.
    """
    async with self._engine.connect() as conn:
        return await queries.get_all_edges(conn)
```

## Implementation Order

1. **Add 3 public API methods** to `MemorySystem` (`get_block`, `get_contradictions`,
   `get_edges`) — wrapping existing internal queries
2. **Add 2 query functions** to `queries.py` (`get_all_contradictions`,
   `get_all_edges`)
3. **Create** `benchmarks/learnedmembench/config.py`
4. **Create** `benchmarks/learnedmembench/adapter.py`
5. **Tests**: Test the 3 new public API methods through the public API
   (e.g., learn facts, consolidate, verify `get_block` returns expected state)

Steps 1-2 are the only changes to elfmem's core. Steps 3-4 are benchmark
adapter code. Step 5 follows testing_principles.md — test through the public
API only.

## What NOT to Do

- **Don't add LMB as a dependency of elfmem.** The adapter imports from
  `learnedmembench` (the external package) but elfmem itself has no knowledge
  of LMB. Same pattern as the LoCoMo adapter importing `sentence_transformers`.
- **Don't change existing API semantics.** The 3 new methods are additive.
  `recall()`, `consolidate()`, etc remain unchanged.
- **Don't expose internal state beyond what the protocol needs.** `get_block`
  returns the raw dict — the adapter maps it to the protocol's `BlockState`.
  elfmem doesn't need to know about LMB's types.
- **Don't duplicate logic.** The adapter is pure delegation. No scoring, no
  filtering, no BM25 — elfmem's pipeline handles all of that via `recall()`.
