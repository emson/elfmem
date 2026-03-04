# Step 3: Schema + Storage — Implementation Plan

## Overview

Build the database foundation: SQLAlchemy Core table definitions (`models.py`),
async engine creation (`engine.py`), and named query functions (`queries.py`).
This is the L1 storage layer that every higher layer depends on.

**Key design decisions (locked):**
- SQLAlchemy Core — NOT ORM (exploration 019)
- SQLite with WAL mode (exploration 018)
- Async throughout — `create_async_engine` + `aiosqlite`
- `last_reinforced_at` stores cumulative active hours, NOT wall-clock (exploration 024)
- No separate `inbox` table — `blocks.status = 'inbox'` (exploration 024)
- Embeddings stored as BLOB in `blocks` table (Phase 1 scale)
- `render_as_batch=True` for all Alembic migrations (exploration 019)

---

## Files to Create/Modify

| File | Action | Purpose |
|------|--------|---------|
| `src/elfmem/db/__init__.py` | Create | Package init — export `metadata`, engine functions |
| `src/elfmem/db/models.py` | Create | SQLAlchemy Core table definitions (schema source of truth) |
| `src/elfmem/db/engine.py` | Create | Async engine creation with SQLite pragmas |
| `src/elfmem/db/queries.py` | Create | Named async query functions for all tables |
| `alembic/env.py` | Modify | Remove try/except guard (models.py now exists) |

---

## Module Design

### 1. `src/elfmem/db/__init__.py`

```python
"""elfmem database layer — SQLAlchemy Core on SQLite."""

from elfmem.db.models import metadata
from elfmem.db.engine import create_engine, create_test_engine

__all__ = ["metadata", "create_engine", "create_test_engine"]
```

---

### 2. `src/elfmem/db/models.py`

**Purpose:** Single source of truth for the database schema. Alembic imports
`metadata` from here. Application code imports table objects from here.

**Imports:**
```python
from sqlalchemy import (
    Column,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    Real,
    Table,
    Text,
    UniqueConstraint,
    ForeignKey,
)
```

**Schema — `metadata` object:**
```python
metadata = MetaData()
```

**Table: `blocks`**

Uses the refined schema from exploration 024 §3.8. Key changes from exploration
017: `last_reinforced_at` replaces `hours_since_reinforcement`; `status` includes
`'inbox'` (no separate inbox table); `archive_reason` added.

```python
blocks = Table(
    "blocks",
    metadata,
    Column("id", Text, primary_key=True),                    # sha256(normalized_content)[:16]
    Column("content", Text, nullable=False),                  # raw markdown
    Column("category", Text, nullable=False),                 # "knowledge" | "observation" | etc
    Column("source", Text, nullable=False),                   # "api" | "cli" | "sdk"
    Column("created_at", Text, nullable=False),               # ISO 8601

    # State
    Column("status", Text, nullable=False, default="inbox"),  # inbox | active | archived
    Column("archive_reason", Text),                           # decayed | superseded | forgotten

    # Scoring signals (stored)
    Column("confidence", Real, nullable=False, default=0.50),
    Column("reinforcement_count", Integer, nullable=False, default=0),
    Column("decay_lambda", Real, nullable=False, default=0.01),
    Column("last_reinforced_at", Real, nullable=False, default=0.0),  # cumulative active hours
    Column("self_alignment", Real),                           # NULL until computed

    # Embedding
    Column("embedding", LargeBinary),                         # NULL until consolidate()
    Column("embedding_model", Text),                          # model id for invalidation

    # Metadata
    Column("token_count", Integer),
    Column("last_session_id", Text),
)
```

**Key implementation notes for `blocks`:**
- `id` is `sha256(normalized_content)[:16]` — content-hash for O(1) dedup
- `content` column is the raw markdown — no separate `file_path`. Phase 1
  stores content in the database directly, not as filesystem .md files
  (simplifies testing and deployment; file-based storage can be added later)
- `last_reinforced_at` is a float representing cumulative active hours (NOT
  a datetime). At query time, `hours_since = current_active_hours - last_reinforced_at`
- `status` defaults to `'inbox'` because `learn()` creates blocks in inbox state
- `archive_reason` is NULL for non-archived blocks
- `embedding` is stored as `np.ndarray.tobytes()` and loaded via
  `np.frombuffer(blob, dtype=np.float32)` — conversion functions in queries.py
- `self_alignment` is NULL until computed during `consolidate()`

**Table: `block_tags`**

Normalised tag storage with ON DELETE CASCADE.

```python
block_tags = Table(
    "block_tags",
    metadata,
    Column("block_id", Text, ForeignKey("blocks.id", ondelete="CASCADE"), nullable=False),
    Column("tag", Text, nullable=False),
    UniqueConstraint("block_id", "tag", name="uq_block_tag"),
)
```

**Key implementation notes for `block_tags`:**
- Simplified from exploration 017's schema: removed `status`, `assigned_by`,
  `assigned_at` columns. Tags are either present or not — promotion logic is
  handled in application code, not tag metadata columns
- The unique constraint prevents duplicate (block_id, tag) pairs
- CASCADE ensures tags are deleted when their block is archived/deleted

**Table: `edges`**

Associative relationships between blocks.

```python
edges = Table(
    "edges",
    metadata,
    Column("from_id", Text, ForeignKey("blocks.id", ondelete="CASCADE"), nullable=False),
    Column("to_id", Text, ForeignKey("blocks.id", ondelete="CASCADE"), nullable=False),
    Column("weight", Real, nullable=False),
    Column("reinforcement_count", Integer, nullable=False, default=0),
    Column("created_at", Text, nullable=False),               # ISO 8601
    UniqueConstraint("from_id", "to_id", name="uq_edge"),
)
```

**Key implementation notes for `edges`:**
- `from_id` < `to_id` enforced at application level via `Edge.canonical()` —
  canonical ordering prevents duplicate edges (A→B and B→A)
- Removed `hours_since_co_retrieval` from exploration 017 — edge decay uses
  the same active-hours approach as blocks (simpler; edge reinforcement resets
  the edge's weight directly rather than tracking hours)
- CASCADE: when either endpoint block is archived, the edge is deleted

**Table: `contradictions`**

```python
contradictions = Table(
    "contradictions",
    metadata,
    Column("block_a_id", Text, ForeignKey("blocks.id", ondelete="CASCADE"), nullable=False),
    Column("block_b_id", Text, ForeignKey("blocks.id", ondelete="CASCADE"), nullable=False),
    Column("score", Real, nullable=False),                    # 0.0-1.0
    Column("resolved", Integer, nullable=False, default=0),   # 0/1 — SQLite has no BOOLEAN
    Column("created_at", Text, nullable=False),               # ISO 8601
    UniqueConstraint("block_a_id", "block_b_id", name="uq_contradiction"),
)
```

**Table: `frames`**

Frame definitions stored as data (exploration 024 §3.2).

```python
frames = Table(
    "frames",
    metadata,
    Column("name", Text, primary_key=True),
    Column("weights_json", Text, nullable=False),             # JSON: ScoringWeights fields
    Column("filters_json", Text, nullable=False),             # JSON: tag_patterns, categories, search_window
    Column("guarantees_json", Text, nullable=False, default="[]"),  # JSON: list of guarantee tag patterns
    Column("template", Text, nullable=False),
    Column("token_budget", Integer, nullable=False),
    Column("cache_json", Text),                               # NULL = no caching; JSON: CachePolicy
    Column("source", Text, nullable=False, default="user"),   # "builtin" | "user"
    Column("created_at", Text, nullable=False),               # ISO 8601
)
```

**Table: `sessions`**

```python
sessions = Table(
    "sessions",
    metadata,
    Column("id", Text, primary_key=True),
    Column("task_type", Text, nullable=False, default="general"),
    Column("started_at", Text, nullable=False),               # ISO 8601
    Column("ended_at", Text),                                 # NULL while active
    Column("start_active_hours", Real, nullable=False),       # snapshot of total_active_hours at start
)
```

**Table: `system_config`**

Key-value store for global settings.

```python
system_config = Table(
    "system_config",
    metadata,
    Column("key", Text, primary_key=True),
    Column("value", Text, nullable=False),
)
```

**Indexes:**

```python
Index("idx_blocks_status", blocks.c.status)
Index("idx_blocks_last_reinforced", blocks.c.last_reinforced_at)
Index("idx_block_tags_tag", block_tags.c.tag)
Index("idx_block_tags_block_id", block_tags.c.block_id)
Index("idx_edges_from", edges.c.from_id)
Index("idx_edges_to", edges.c.to_id)
Index(
    "idx_contradictions_unresolved",
    contradictions.c.block_a_id,
    contradictions.c.block_b_id,
    sqlite_where=(contradictions.c.resolved == 0),
)
```

---

### 3. `src/elfmem/db/engine.py`

**Purpose:** Async engine creation with SQLite pragmas. Two factory functions:
one for file-based databases, one for in-memory test databases.

**Imports:**
```python
from sqlalchemy.ext.asyncio import create_async_engine, AsyncEngine
from sqlalchemy.pool import NullPool, StaticPool
from sqlalchemy import event, text
```

**Function: `create_engine`**

```python
async def create_engine(
    db_path: str,
    *,
    echo: bool = False,
) -> AsyncEngine:
    """Create an async SQLAlchemy engine for elfmem with correct SQLite settings.

    Args:
        db_path: Path to the SQLite database file.
        echo: If True, log all SQL statements.

    Returns:
        Configured AsyncEngine with WAL mode, foreign keys, and page cache.
    """
```

**Key implementation notes:**
- URL format: `f"sqlite+aiosqlite:///{db_path}"`
- `poolclass=NullPool` — each context manager gets a fresh connection; no
  connection pooling for file-based SQLite
- `connect_args={"check_same_thread": False}` — required for async usage
- Register `@event.listens_for(engine.sync_engine, "connect")` listener that
  executes SQLite pragmas on every new connection:
  ```sql
  PRAGMA journal_mode=WAL;
  PRAGMA foreign_keys=ON;
  PRAGMA synchronous=NORMAL;
  PRAGMA cache_size=-32000;    -- 32 MB page cache
  PRAGMA temp_store=MEMORY;
  ```
- The listener receives `(dbapi_conn, connection_record)` — execute pragmas
  via `dbapi_conn.execute()` (raw DBAPI, not SQLAlchemy)
- Return the engine (caller is responsible for `engine.dispose()`)

**Function: `create_test_engine`**

```python
async def create_test_engine() -> AsyncEngine:
    """Create an in-memory async engine for tests.

    Uses StaticPool to ensure all connections share the same in-memory database.
    Creates all tables from metadata immediately.

    Returns:
        Configured AsyncEngine with tables created.
    """
```

**Key implementation notes:**
- URL: `"sqlite+aiosqlite:///:memory:"`
- `poolclass=StaticPool` — ensures one connection = one in-memory DB
- `connect_args={"check_same_thread": False}`
- After engine creation, run `metadata.create_all()` using `async with engine.begin() as conn: await conn.run_sync(metadata.create_all)`
- Also set pragmas via the same event listener (foreign keys especially)

---

### 4. `src/elfmem/db/queries.py`

**Purpose:** Named async query functions for all CRUD operations. Every database
interaction goes through a function here — no raw SQL in business logic.

**Imports:**
```python
from __future__ import annotations

import json
from datetime import datetime, timezone

import numpy as np
from sqlalchemy import delete, insert, select, update, func, and_
from sqlalchemy.ext.asyncio import AsyncConnection

from elfmem.db.models import (
    blocks, block_tags, edges, contradictions,
    frames, sessions, system_config,
)
```

**Embedding conversion helpers:**

```python
def embedding_to_bytes(vec: np.ndarray) -> bytes:
    """Convert a numpy float32 vector to bytes for BLOB storage."""
    return vec.astype(np.float32).tobytes()


def bytes_to_embedding(data: bytes) -> np.ndarray:
    """Convert BLOB bytes back to a numpy float32 vector."""
    return np.frombuffer(data, dtype=np.float32)
```

**Content hashing:**

```python
import hashlib

def content_hash(content: str) -> str:
    """Compute the content-addressable block ID.

    Returns sha256(normalised_content)[:16].
    Normalisation: strip whitespace, lowercase.
    """
    normalised = content.strip().lower()
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()[:16]
```

**Block queries — all accept `conn: AsyncConnection` as first argument:**

```python
async def insert_block(
    conn: AsyncConnection,
    *,
    block_id: str,
    content: str,
    category: str,
    source: str,
    status: str = "inbox",
    confidence: float = 0.50,
    decay_lambda: float = 0.01,
    last_reinforced_at: float = 0.0,
) -> None:
    """Insert a new block. Raises IntegrityError if id already exists."""


async def get_block(conn: AsyncConnection, block_id: str) -> dict | None:
    """Fetch a single block by id. Returns None if not found.
    Returns a dict with all column values."""


async def block_exists(conn: AsyncConnection, block_id: str) -> bool:
    """Check if a block id exists (any status). O(1) via primary key."""


async def get_active_blocks(
    conn: AsyncConnection,
    *,
    min_last_reinforced_at: float | None = None,
) -> list[dict]:
    """Fetch all active blocks, optionally filtered by recency cutoff.

    Used by the pre-filter stage of retrieval.
    If min_last_reinforced_at is provided, only returns blocks where
    last_reinforced_at > min_last_reinforced_at (search window filter).
    """


async def get_active_blocks_with_embeddings(
    conn: AsyncConnection,
    *,
    min_last_reinforced_at: float | None = None,
) -> list[dict]:
    """Fetch active blocks that have non-NULL embeddings.

    Same as get_active_blocks but includes the embedding BLOB.
    Used by the ATTENTION frame retrieval (needs cosine similarity).
    """


async def get_inbox_blocks(conn: AsyncConnection) -> list[dict]:
    """Fetch all blocks with status='inbox'. Used by consolidate()."""


async def get_inbox_count(conn: AsyncConnection) -> int:
    """Count of inbox blocks. Used to decide if consolidate() should run."""


async def update_block_status(
    conn: AsyncConnection,
    block_id: str,
    *,
    status: str,
    archive_reason: str | None = None,
) -> None:
    """Transition a block to a new status."""


async def update_block_scoring(
    conn: AsyncConnection,
    block_id: str,
    *,
    confidence: float | None = None,
    self_alignment: float | None = None,
    decay_lambda: float | None = None,
    embedding: np.ndarray | None = None,
    embedding_model: str | None = None,
    token_count: int | None = None,
) -> None:
    """Update scoring-related fields after consolidation.

    Only updates fields that are not None (partial update).
    Converts embedding to bytes before storage.
    """


async def reinforce_blocks(
    conn: AsyncConnection,
    block_ids: list[str],
    current_active_hours: float,
) -> None:
    """Reinforce a set of blocks: increment reinforcement_count and update
    last_reinforced_at to current_active_hours.

    Single UPDATE statement — no per-block queries.
    """
```

**Tag queries:**

```python
async def add_tags(
    conn: AsyncConnection,
    block_id: str,
    tags: list[str],
) -> None:
    """Add tags to a block. Silently ignores duplicates (INSERT OR IGNORE)."""


async def get_tags(conn: AsyncConnection, block_id: str) -> list[str]:
    """Get all tags for a block."""


async def get_blocks_by_tag_pattern(
    conn: AsyncConnection,
    pattern: str,
) -> list[str]:
    """Get block IDs matching a tag pattern (SQL LIKE).

    Example: pattern="self/%" returns all blocks with self/* tags.
    """


async def remove_tags(
    conn: AsyncConnection,
    block_id: str,
    tags: list[str],
) -> None:
    """Remove specific tags from a block."""
```

**Edge queries:**

```python
async def insert_edge(
    conn: AsyncConnection,
    *,
    from_id: str,
    to_id: str,
    weight: float,
) -> None:
    """Insert an edge. from_id < to_id must be enforced by caller (canonical order).

    Raises IntegrityError if edge already exists.
    """


async def get_edges_for_block(conn: AsyncConnection, block_id: str) -> list[dict]:
    """Get all edges where block_id is either from_id or to_id.

    Returns dicts with from_id, to_id, weight, reinforcement_count.
    """


async def get_neighbours(conn: AsyncConnection, block_ids: list[str]) -> list[str]:
    """Get 1-hop neighbour block IDs for a set of seed blocks.

    Used by graph expansion in retrieval Stage 3.
    Returns block IDs that are connected to any of the seed blocks.
    Excludes the seed blocks themselves.
    """


async def reinforce_edges(
    conn: AsyncConnection,
    edge_pairs: list[tuple[str, str]],
) -> None:
    """Reinforce co-retrieval edges: increment reinforcement_count.

    edge_pairs must be in canonical order (from_id < to_id).
    """


async def get_weighted_degree(
    conn: AsyncConnection,
    block_ids: list[str],
) -> dict[str, float]:
    """Compute weighted degree (sum of edge weights) for a set of blocks.

    Returns {block_id: total_weight}. Blocks with no edges return 0.0.
    Used for centrality normalisation.
    """
```

**Contradiction queries:**

```python
async def insert_contradiction(
    conn: AsyncConnection,
    *,
    block_a_id: str,
    block_b_id: str,
    score: float,
) -> None:
    """Insert a contradiction record. Canonical order: block_a_id < block_b_id."""


async def get_contradictions_for_blocks(
    conn: AsyncConnection,
    block_ids: list[str],
) -> list[dict]:
    """Get unresolved contradictions involving any of the given block IDs.

    Only returns contradictions where resolved=0.
    """


async def resolve_contradiction(
    conn: AsyncConnection,
    block_a_id: str,
    block_b_id: str,
) -> None:
    """Mark a contradiction as resolved."""
```

**Frame queries:**

```python
async def get_frame(conn: AsyncConnection, name: str) -> dict | None:
    """Fetch a frame definition by name. Returns None if not found.

    JSON fields (weights_json, filters_json, guarantees_json, cache_json)
    are returned as raw strings — caller deserialises.
    """


async def upsert_frame(
    conn: AsyncConnection,
    *,
    name: str,
    weights_json: str,
    filters_json: str,
    guarantees_json: str,
    template: str,
    token_budget: int,
    cache_json: str | None,
    source: str,
) -> None:
    """Insert or update a frame definition."""


async def list_frames(conn: AsyncConnection) -> list[dict]:
    """List all frame definitions."""
```

**Session queries:**

```python
async def start_session(
    conn: AsyncConnection,
    *,
    session_id: str,
    task_type: str,
    start_active_hours: float,
) -> None:
    """Record a new session start."""


async def end_session(
    conn: AsyncConnection,
    session_id: str,
) -> None:
    """Record session end time."""


async def get_active_session(conn: AsyncConnection) -> dict | None:
    """Get the currently active session (ended_at IS NULL)."""
```

**System config queries:**

```python
async def get_config(conn: AsyncConnection, key: str) -> str | None:
    """Get a system config value by key. Returns None if not found."""


async def set_config(conn: AsyncConnection, key: str, value: str) -> None:
    """Set a system config value (upsert)."""


async def get_total_active_hours(conn: AsyncConnection) -> float:
    """Get the total_active_hours counter. Returns 0.0 if not set."""


async def set_total_active_hours(conn: AsyncConnection, hours: float) -> None:
    """Update the total_active_hours counter."""
```

**Seed data function:**

```python
async def seed_builtin_data(conn: AsyncConnection) -> None:
    """Insert built-in frames and default system_config values.

    Called once when a fresh database is created.
    Idempotent — uses INSERT OR IGNORE.

    Built-in frames:
    - self:      sim=0.10, conf=0.30, rec=0.05, cent=0.25, reinf=0.30
    - attention: sim=0.35, conf=0.15, rec=0.25, cent=0.15, reinf=0.10
    - task:      sim=0.20, conf=0.20, rec=0.20, cent=0.20, reinf=0.20

    Default system_config:
    - total_active_hours: "0.0"
    - prune_threshold: "0.05"
    - top_k: "5"
    """
```

**Key implementation notes:**
- All query functions use `await conn.execute(stmt)` — caller manages the
  connection lifecycle via `async with engine.begin() as conn:`
- INSERT operations use `conn.execute(insert(table).values(...))` — Core
  expression language, not raw SQL strings
- UPDATE operations use `conn.execute(update(table).where(...).values(...))`
- SELECT operations use `conn.execute(select(table).where(...))` and return
  `result.mappings().all()` for dict results
- Partial updates (like `update_block_scoring`) build the `values` dict
  dynamically, only including non-None parameters
- Bulk operations (like `reinforce_blocks`) use `.where(table.c.id.in_(ids))`
  for single-statement efficiency

---

### 5. `alembic/env.py` — Modification

Remove the try/except guard around the models import since `models.py` now exists:

```python
# Before:
try:
    from elfmem.db.models import metadata as target_metadata
except ImportError:
    target_metadata = None

# After:
from elfmem.db.models import metadata as target_metadata
```

---

## Key Invariants

1. **`last_reinforced_at` is cumulative active hours** — never a datetime,
   never wall-clock time. Computed via `total_active_hours + elapsed_this_session`
2. **Content hash = block ID** — `sha256(content.strip().lower())[:16]`
3. **Canonical edge order** — `from_id < to_id` enforced by application code
4. **Foreign keys with CASCADE** — tags and edges auto-delete when blocks are
   archived/deleted
5. **No ORM objects** — all results are dicts or scalars; no lazy loading
6. **All mutations via named functions** — no raw SQL in business logic
7. **Caller manages transactions** — query functions accept `AsyncConnection`,
   caller wraps in `async with engine.begin() as conn:`

## Security Considerations

1. **No SQL injection** — all queries use SQLAlchemy expression language with
   bound parameters; never string-format SQL
2. **No API keys in database** — system_config stores thresholds only; API keys
   come from environment variables via LiteLLM
3. **Content hashing uses sha256** — collision-resistant; 16-hex-char prefix
   gives 64 bits (sufficient for <50K blocks)
4. **WAL mode** — prevents corruption from concurrent reads during writes

## Edge Cases

1. **Empty database** — `seed_builtin_data()` must be called after table creation
   to populate frames and system_config defaults
2. **Duplicate block insert** — `insert_block` raises `IntegrityError` on
   duplicate id; caller handles (learn() uses this for dedup)
3. **NULL embeddings** — blocks start without embeddings (inbox state);
   `get_active_blocks_with_embeddings` filters these out
4. **Zero reinforcement_count** — `log_normalise_reinforcement(0, max)` returns
   0.0 (handled by scoring.py)
5. **Concurrent sessions** — only one session should be active at a time;
   `get_active_session` returns the latest if multiple exist (defensive)

## Dependencies

- `sqlalchemy>=2.0` (already in pyproject.toml)
- `aiosqlite` (already in pyproject.toml)
- `numpy` (already in pyproject.toml) — for embedding conversion
- `elfmem.types` — for enum values (BlockStatus, ArchiveReason, DecayTier)

## Done Criteria

1. `from elfmem.db.models import metadata, blocks, block_tags, edges, contradictions, frames, sessions, system_config` — all importable
2. `from elfmem.db.engine import create_engine, create_test_engine` — both create working async engines
3. `from elfmem.db.queries import insert_block, get_block, ...` — all query functions importable
4. `create_test_engine()` creates in-memory DB with all tables
5. All query functions work against the test engine
6. `seed_builtin_data()` populates 3 built-in frames and default system_config
7. `mypy --strict` passes on all three files
8. `alembic/env.py` imports metadata without try/except
