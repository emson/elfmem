# Step 5: learn() + consolidate() — Implementation Plan

## Overview

Build the first vertical slice through all layers: the `learn()` and
`consolidate()` operations. `learn()` is the fast-path ingestion that adds
content to the inbox. `consolidate()` is the batch promotion that computes
embeddings, detects duplicates, scores self-alignment, infers tags, builds
graph edges, and promotes blocks to active status.

This step also creates the `MemorySystem` API class and session management.

**Key design decisions (locked):**
- `learn()` never calls LLM or embedding service — it's synchronous I/O only
- Content-hash dedup at `learn()` — O(1) duplicate rejection
- `consolidate()` processes all inbox blocks; makes at most N×3 LLM calls
- Near-duplicate detection uses cosine similarity thresholds (0.90-0.95)
- Decay tier determined by self-tags (lowest λ wins)
- Edge creation at consolidation — similarity ≥ 0.60 threshold
- Degree cap of 10 edges per block

---

## Files to Create/Modify

| File | Action | Purpose |
|------|--------|---------|
| `src/elfmem/operations/__init__.py` | Create | Package init |
| `src/elfmem/operations/learn.py` | Create | learn() operation |
| `src/elfmem/operations/consolidate.py` | Create | consolidate() operation |
| `src/elfmem/memory/__init__.py` | Create | Package init |
| `src/elfmem/memory/blocks.py` | Create | Block state transitions, content hashing, decay tier logic |
| `src/elfmem/memory/dedup.py` | Create | Near-duplicate detection and resolution |
| `src/elfmem/session.py` | Create | Session lifecycle, active hours tracking |
| `src/elfmem/api.py` | Create | MemorySystem class — public API |

---

## Module Design

### 1. `src/elfmem/memory/blocks.py`

**Purpose:** Block-level operations — content hashing, decay tier assignment,
state transitions. Pure functions where possible.

**Imports:**
```python
from __future__ import annotations

import hashlib

from elfmem.types import BlockStatus, ArchiveReason, DecayTier
```

**Functions:**

```python
def compute_content_hash(content: str) -> str:
    """Compute content-addressable block ID: sha256(normalised_content)[:16].

    Normalisation: strip leading/trailing whitespace, lowercase.

    Args:
        content: Raw block content (markdown text).

    Returns:
        16-character hex string.
    """


def determine_decay_tier(tags: list[str], category: str) -> DecayTier:
    """Determine the decay tier for a block based on its tags and category.

    Priority (lowest λ wins):
    1. "self/constitutional" → PERMANENT (λ=0.00001)
    2. "self/value", "self/constraint", "self/goal" → DURABLE (λ=0.001)
    3. category == "observation" → EPHEMERAL (λ=0.050)
    4. Everything else (knowledge, self/style, self/context) → STANDARD (λ=0.010)

    Args:
        tags: List of tag strings for the block.
        category: Block category ("knowledge", "observation", etc.)

    Returns:
        The appropriate DecayTier.
    """


def decay_lambda_for_tier(tier: DecayTier) -> float:
    """Return the λ value for a given decay tier.

    Uses the canonical LAMBDA dict from scoring.py.
    """
```

**Key implementation notes:**
- `compute_content_hash` must match `content_hash` in `db/queries.py` —
  both use the same normalisation logic. Import or share the implementation.
- `determine_decay_tier` implements the tag-priority logic from exploration
  024 §A8. The tag check order matters: constitutional first, then
  durable-class tags, then category, then default.

---

### 2. `src/elfmem/memory/dedup.py`

**Purpose:** Near-duplicate detection and resolution during consolidation.

**Imports:**
```python
from __future__ import annotations

import numpy as np
from sqlalchemy.ext.asyncio import AsyncConnection

from elfmem.db import queries
```

**Constants:**
```python
EXACT_DUP_THRESHOLD = 0.95    # cosine similarity above this → reject
NEAR_DUP_THRESHOLD = 0.90     # cosine similarity in [0.90, 0.95) → supersede
```

**Functions:**

```python
def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Compute cosine similarity between two normalised vectors.

    Both vectors must be L2-normalised (unit vectors).
    Returns a float in [-1.0, 1.0].
    """


async def find_near_duplicate(
    conn: AsyncConnection,
    embedding: np.ndarray,
    active_embeddings: list[tuple[str, np.ndarray]],
) -> tuple[str, float] | None:
    """Find the most similar active block to the given embedding.

    Args:
        conn: Database connection (unused in pure computation, but needed
            for potential future optimisations).
        embedding: The new block's embedding vector.
        active_embeddings: List of (block_id, embedding) tuples for all
            active blocks with embeddings.

    Returns:
        (block_id, similarity) of the most similar block if similarity ≥
        NEAR_DUP_THRESHOLD, else None.
    """


async def resolve_near_duplicate(
    conn: AsyncConnection,
    *,
    existing_block_id: str,
    new_block_id: str,
    similarity: float,
) -> str:
    """Handle near-duplicate resolution.

    If similarity ≥ EXACT_DUP_THRESHOLD (0.95): reject new block silently.
    If similarity in [NEAR_DUP_THRESHOLD, EXACT_DUP_THRESHOLD): supersede
    old block — archive it with reason "superseded", inherit its
    reinforcement_count and confidence to the new block.

    Args:
        conn: Database connection for state mutations.
        existing_block_id: ID of the existing active block.
        new_block_id: ID of the new inbox block being processed.
        similarity: Cosine similarity between the two blocks.

    Returns:
        "rejected" if exact duplicate, "superseded" if near-duplicate resolved.
    """
```

**Key implementation notes:**
- `cosine_similarity` for normalised vectors is just `np.dot(a, b)` — simple
  because MockEmbeddingService and real embeddings are both L2-normalised
- `find_near_duplicate` does a brute-force scan over active embeddings.
  At Phase 1 scale (≤500 blocks), this is <1ms. No vectorDB needed.
- `resolve_near_duplicate` inherits metadata from old → new block:
  `reinforcement_count`, `confidence`. Archives old block with
  `archive_reason=SUPERSEDED`.

---

### 3. `src/elfmem/operations/learn.py`

**Purpose:** The `learn()` operation — fast-path ingestion into inbox.

**Imports:**
```python
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncConnection

from elfmem.db import queries
from elfmem.memory.blocks import compute_content_hash
from elfmem.types import LearnResult
```

**Function:**

```python
async def learn(
    conn: AsyncConnection,
    *,
    content: str,
    tags: list[str] | None = None,
    category: str = "knowledge",
    source: str = "api",
) -> LearnResult:
    """Add a block to the inbox for later consolidation.

    This is the fast path — no LLM calls, no embedding, no scoring.
    Only computes a content hash and inserts into the inbox.

    Exact duplicates (same content hash as existing inbox or active block)
    are rejected silently.

    Args:
        conn: Database connection (within a transaction).
        content: Block content (markdown text).
        tags: Optional initial tags for the block.
        category: Block category ("knowledge", "observation", etc.)
        source: Ingestion source ("api", "cli", "sdk").

    Returns:
        LearnResult with block_id and status ("created" or "duplicate_rejected").
    """
```

**Key implementation notes:**
- Computes `block_id = compute_content_hash(content)`
- Checks `block_exists(conn, block_id)` — if True, return
  `LearnResult(block_id, "duplicate_rejected")`
- Otherwise: `insert_block(conn, block_id=..., content=..., status="inbox", ...)`
- If `tags` provided: `add_tags(conn, block_id, tags)`
- No LLM calls. No embedding calls. No async I/O beyond the database.
- Returns immediately — the block is in the inbox, not yet active

---

### 4. `src/elfmem/operations/consolidate.py`

**Purpose:** The `consolidate()` operation — batch promotion from inbox to active.
This is the most complex operation in the system.

**Imports:**
```python
from __future__ import annotations

import numpy as np
from sqlalchemy.ext.asyncio import AsyncConnection

from elfmem.db import queries
from elfmem.memory.blocks import determine_decay_tier, decay_lambda_for_tier
from elfmem.memory.dedup import (
    EXACT_DUP_THRESHOLD,
    NEAR_DUP_THRESHOLD,
    cosine_similarity,
    find_near_duplicate,
    resolve_near_duplicate,
)
from elfmem.ports.services import EmbeddingService, LLMService
from elfmem.types import ConsolidateResult, Edge
```

**Constants:**
```python
SELF_ALIGNMENT_THRESHOLD = 0.70    # minimum to infer self-tags
SIMILARITY_EDGE_THRESHOLD = 0.60   # minimum similarity to create edge
EDGE_DEGREE_CAP = 10               # max edges per block
```

**Function:**

```python
async def consolidate(
    conn: AsyncConnection,
    *,
    llm: LLMService,
    embedding_svc: EmbeddingService,
    current_active_hours: float,
    self_alignment_threshold: float = SELF_ALIGNMENT_THRESHOLD,
    similarity_edge_threshold: float = SIMILARITY_EDGE_THRESHOLD,
    edge_degree_cap: int = EDGE_DEGREE_CAP,
) -> ConsolidateResult:
    """Process all inbox blocks: embed, dedup, score, tag, build edges, promote.

    Pipeline per inbox block:
    1. Compute embedding
    2. Near-duplicate check against active blocks
       - ≥0.95: reject silently
       - 0.90-0.95: supersede old block, inherit metadata
       - <0.90: proceed
    3. Score self-alignment via LLM
    4. If alignment ≥ threshold: infer self-tags via LLM
    5. Determine decay tier from tags
    6. Detect contradictions with active blocks (via LLM)
    7. Build edges with similar active blocks (≥0.60 similarity)
    8. Promote to active

    Args:
        conn: Database connection (within a transaction).
        llm: LLM service for alignment scoring, tag inference, contradiction.
        embedding_svc: Embedding service for vector computation.
        current_active_hours: Current total active hours (for last_reinforced_at).
        self_alignment_threshold: Minimum alignment to trigger tag inference.
        similarity_edge_threshold: Minimum similarity to create graph edge.
        edge_degree_cap: Maximum edges per block.

    Returns:
        ConsolidateResult with counts of processed, promoted, deduplicated,
        edges_created.
    """
```

**Key implementation notes:**

The function should be decomposed into smaller helper functions (≤50 lines each):

```python
async def _embed_block(
    embedding_svc: EmbeddingService,
    content: str,
) -> np.ndarray:
    """Compute embedding for a single block."""


async def _check_near_duplicates(
    conn: AsyncConnection,
    block_id: str,
    embedding: np.ndarray,
    active_embeddings: list[tuple[str, np.ndarray]],
) -> str | None:
    """Check for near-duplicates. Returns "rejected" or "superseded" or None."""


async def _score_and_tag(
    conn: AsyncConnection,
    llm: LLMService,
    *,
    block_id: str,
    content: str,
    self_context: str,
    self_alignment_threshold: float,
) -> tuple[float, list[str]]:
    """Score self-alignment and optionally infer tags.

    Returns (alignment_score, inferred_tags).
    """


async def _detect_contradictions(
    conn: AsyncConnection,
    llm: LLMService,
    *,
    block_id: str,
    content: str,
    active_blocks: list[dict],
    contradiction_threshold: float = 0.80,
) -> int:
    """Detect contradictions between new block and active blocks.

    Returns count of contradictions detected.
    """


async def _build_edges(
    conn: AsyncConnection,
    *,
    block_id: str,
    embedding: np.ndarray,
    active_embeddings: list[tuple[str, np.ndarray]],
    similarity_threshold: float,
    degree_cap: int,
) -> int:
    """Create edges between new block and similar active blocks.

    Only creates edges where similarity ≥ threshold and neither
    endpoint exceeds the degree cap.

    Returns count of edges created.
    """


async def _get_self_context(
    conn: AsyncConnection,
) -> str:
    """Build the self-context string for LLM calls.

    Fetches active blocks with self/* tags and concatenates their content.
    Used as context for alignment scoring and tag inference.
    """
```

**Processing flow:**
1. Fetch inbox blocks via `get_inbox_blocks(conn)`
2. If empty, return `ConsolidateResult(0, 0, 0, 0)` immediately
3. Fetch all active blocks with embeddings (for dedup and edge building)
4. Build self-context string from self/* tagged blocks
5. For each inbox block:
   a. Embed the block content
   b. Check near-duplicates
   c. If rejected/superseded, skip to next block (increment dedup counter)
   d. Score self-alignment
   e. If alignment ≥ threshold, infer self-tags
   f. Determine decay tier from tags + category
   g. Detect contradictions with active blocks
   h. Update block: set embedding, alignment, decay_lambda, token_count
   i. Add inferred tags
   j. Promote to active status
   k. Build edges with similar active blocks
   l. Update active embeddings list (include newly promoted block)
6. Return ConsolidateResult with counts

---

### 5. `src/elfmem/session.py`

**Purpose:** Session lifecycle management and active hours tracking.

**Imports:**
```python
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncConnection

from elfmem.db import queries
```

**Functions:**

```python
async def begin_session(
    conn: AsyncConnection,
    *,
    task_type: str = "general",
) -> str:
    """Start a new session.

    Records the session start and snapshots total_active_hours.
    Returns the session ID.

    Args:
        conn: Database connection.
        task_type: Type of task for this session.

    Returns:
        Session ID (UUID hex string).
    """


async def end_session(
    conn: AsyncConnection,
    session_id: str,
) -> float:
    """End the current session.

    Computes session duration, updates total_active_hours.
    Returns the session duration in hours.

    Args:
        conn: Database connection.
        session_id: ID of the session to end.

    Returns:
        Duration of the session in hours.
    """


async def compute_current_active_hours(
    conn: AsyncConnection,
    session_id: str,
) -> float:
    """Compute the current total active hours including elapsed session time.

    Returns:
        total_active_hours + elapsed_this_session.
    """
```

**Key implementation notes:**
- Session ID is `uuid.uuid4().hex[:16]`
- `begin_session` snapshots `total_active_hours` into `sessions.start_active_hours`
- `end_session` computes `duration = (now - started_at)` in hours, then
  `set_total_active_hours(conn, old_total + duration)`
- `compute_current_active_hours` reads `session.started_at` and computes
  elapsed time from wall-clock, adds to `total_active_hours` — this gives
  the "live" active hours for use during a session

---

### 6. `src/elfmem/api.py`

**Purpose:** The `MemorySystem` class — the public API surface. Orchestrates
all operations. This is the L4 boundary where side effects happen.

**Imports:**
```python
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncConnection

from elfmem.db import queries
from elfmem.db.engine import create_engine, create_test_engine
from elfmem.operations.learn import learn
from elfmem.operations.consolidate import consolidate
from elfmem.ports.services import EmbeddingService, LLMService
from elfmem.session import begin_session, end_session, compute_current_active_hours
from elfmem.types import ConsolidateResult, LearnResult
```

**Class:**

```python
class MemorySystem:
    """The public interface to elfmem — an adaptive memory system for LLM agents.

    Usage:
        system = MemorySystem(engine, llm_service, embedding_service)
        async with system.session():
            await system.learn("Some knowledge.")
            result = await system.frame("attention", query="related query")

    All methods are async. Session management is required — learn() and frame()
    must be called within a session context.
    """

    def __init__(
        self,
        engine: AsyncEngine,
        llm_service: LLMService,
        embedding_service: EmbeddingService,
    ) -> None:
        """Initialise MemorySystem with database engine and service adapters.

        Args:
            engine: SQLAlchemy async engine (from create_engine or create_test_engine).
            llm_service: LLM service implementation (real or mock).
            embedding_service: Embedding service implementation (real or mock).
        """

    async def begin_session(self, task_type: str = "general") -> None:
        """Start a new session. Triggers curate() if overdue.

        Must be called before learn() or frame(). Alternatively use
        the async context manager: `async with system.session()`.
        """

    async def end_session(self) -> None:
        """End the current session. Always runs consolidate() on remaining inbox."""

    async def learn(
        self,
        content: str,
        *,
        tags: list[str] | None = None,
        category: str = "knowledge",
        source: str = "api",
    ) -> LearnResult:
        """Add a block to the inbox. Fast path — no LLM calls.

        Args:
            content: Block content (markdown text).
            tags: Optional initial tags.
            category: Block category.
            source: Ingestion source.

        Returns:
            LearnResult with block_id and status.
        """

    async def consolidate(self) -> ConsolidateResult:
        """Manually trigger consolidation of all inbox blocks.

        Normally called automatically at end_session(). Can be called
        explicitly for immediate processing.

        Returns:
            ConsolidateResult with processing counts.
        """
```

**Key implementation notes:**
- Store `self._engine`, `self._llm`, `self._embedding`, `self._session_id`
- `begin_session` calls `session.begin_session(conn, task_type=...)`
- `end_session` calls `consolidate()` then `session.end_session(conn, ...)`
- `learn()` opens a connection, calls `learn(conn, content=..., ...)`, returns result
- `consolidate()` opens a connection, computes current_active_hours, calls
  `consolidate(conn, llm=..., embedding_svc=..., current_active_hours=...)`
- Transaction pattern: `async with self._engine.begin() as conn:` for writes
- Raise `RuntimeError` if `learn()` or `frame()` called outside a session
- Add `session()` as an async context manager for convenience:
  ```python
  from contextlib import asynccontextmanager

  @asynccontextmanager
  async def session(self, task_type: str = "general"):
      await self.begin_session(task_type)
      try:
          yield self
      finally:
          await self.end_session()
  ```

---

## Key Invariants

1. **`learn()` never blocks on LLM/embedding** — only database I/O
2. **Content hash = block ID** — `sha256(content.strip().lower())[:16]`
3. **Exact duplicates rejected at learn()** — O(1) hash check
4. **Near-duplicate resolution inherits metadata** — reinforcement_count,
   confidence from old → new block
5. **Consolidate is idempotent on empty inbox** — no LLM calls, no side effects
6. **Decay tier determined by most protective tag** — constitutional (permanent)
   wins over value (durable) wins over default (standard)
7. **Edge degree cap enforced** — max 10 edges per block
8. **Canonical edge order** — `from_id < to_id`
9. **Self-context for LLM calls** — built from active self/* tagged blocks
10. **Session required** — learn() and frame() must be called within a session

## Security Considerations

1. **Content hash uses sha256** — collision-resistant; 16-hex truncation gives
   64 bits (birthday bound: ~2^32 ≈ 4 billion blocks before expected collision)
2. **LLM output validation** — alignment scores bounded [0.0, 1.0] by Protocol
   contract; mock enforces this; real adapter uses Pydantic validation
3. **Self-tag injection** — inferred tags are filtered against the valid taxonomy
   (VALID_SELF_TAGS set) in the real adapter; mock returns controlled values
4. **No SQL injection** — all queries via SQLAlchemy expression language

## Edge Cases

1. **Empty inbox** — `consolidate()` returns immediately with zeros
2. **Single block in inbox** — no contradiction detection needed (no pairs);
   self-alignment still runs
3. **All inbox blocks are duplicates** — all rejected; ConsolidateResult shows
   `processed=N, promoted=0, deduplicated=N`
4. **No self/* blocks exist** — self-context is empty string; LLM still runs
   but alignment scores will be low
5. **Block with no tags** — `determine_decay_tier([], "knowledge")` returns
   `STANDARD` (default)
6. **learn() called outside session** — should raise `RuntimeError`

## Dependencies

- `elfmem.db.engine` + `elfmem.db.queries` (Step 3)
- `elfmem.adapters.mock` (Step 4)
- `elfmem.scoring` (Step 2, already complete)
- `elfmem.types` (Step 1, already complete)
- `elfmem.ports.services` (Step 1, already complete)

## Done Criteria

1. TC-L-001: `learn()` adds block to inbox, not active
2. TC-L-002: Exact duplicate rejected at `learn()`
3. TC-L-003: `consolidate()` promotes inbox blocks to active
4. TC-L-004: `consolidate()` processes self-alignment and tags
5. TC-L-005: Near-duplicate resolution (forget + create + inherit)
6. TC-L-006: Very high similarity block rejected silently
7. TC-L-012: `end_session()` consolidates inbox regardless of size
8. TC-L-013: `learn()` returns block ID
9. TC-L-014: `consolidate()` on empty inbox is no-op
10. TC-G-001: Edge created at consolidation for similar blocks
11. TC-G-002: No edge created below threshold
12. TC-G-003: Degree cap enforced
13. TC-G-008: Contradiction stored in contradictions table, not edges
14. TC-D-008: Decay tier determined by self-tags (lowest λ wins)
15. TC-D-009: Tag-free block defaults to standard tier
16. `mypy --strict` passes on all new files
17. `ruff check` clean
