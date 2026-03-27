# Plan: Database Locking Hardening

**Status:** All 6 steps complete. Plan complete.
**Date:** 2026-03-27
**Scope:** Engine config, transaction boundary refactor, consolidate() read-compute-write pattern, curate isolation, WAL health
**Analysis doc:** See previous locking analysis in conversation history
**Out of scope:** Multi-process coordination (advisory file lock), MCP per-request session isolation — follow-on plans

---

## 1. Goal

Eliminate the root cause of all locking hazards in one structural change — **LLM I/O inside
a write transaction** — and harden the remaining edge cases with surgical, low-risk fixes.

The result is a system where:
- The WAL write lock is held only during pure database operations (milliseconds, never seconds)
- Contention surfaces immediately as a clear error, not a silent hang
- A curate() failure can never roll back a successful consolidation
- Total active hours is correct under concurrent sessions
- Periodic WAL cleanup prevents unbounded disk growth

**Deliverables:**

1. `PRAGMA busy_timeout=10000` and `PRAGMA wal_autocheckpoint=500` added to production engine
2. `consolidate()` refactored to **read-compute-write** pattern (LLM calls outside the transaction)
3. `curate()` moved to its own transaction, decoupled from `consolidate()`
4. `total_active_hours` update changed to a SQL-level atomic increment
5. WAL checkpoint triggered from `curate()` after its transaction commits
6. `api.py:recall()` switched from `engine.begin()` to `engine.connect()`
7. `_co_retrieval_staging` persisted to a new `co_retrieval_staging` DB table

---

## 2. Problem Statement

### The root cause

Every locking issue in elfmem traces to one architectural pattern:

```python
# api.py: consolidate()
async with self._engine.begin() as conn:          # WAL_WRITE_LOCK acquired on first UPDATE
    for block in inbox:                            # ← can be 100+ blocks
        analysis = await llm.process_block(...)   # ← NETWORK I/O UNDER WRITE LOCK
        await update_block_scoring(conn, ...)      # write escalates lock early
        score = await llm.detect_contradiction(...)# more NETWORK I/O UNDER WRITE LOCK
```

After the first `UPDATE` in the loop, the WAL write lock is held for every subsequent LLM and
embedding call. For 100 blocks at 100ms/LLM this is 10+ seconds. For a hung LLM provider
with no timeout this is forever.

### Why this matters beyond a single agent

In the MCP server, multiple tool calls run concurrently. A single `elfmem_dream` call holds
the write lock while every concurrent `elfmem_remember` call either hangs (no `busy_timeout`)
or raises an unhandled `OperationalError`. The agent sees failures where there are none.

### Why the current design emerged this way

The single-transaction wrapping of consolidate() is well-intentioned: it ensures atomicity
of the inbox-to-active promotion. If something fails mid-consolidation, all blocks stay in
inbox and can be retried. This is correct. The problem is conflating atomicity of DB work
with the duration of the write lock. These are separable concerns.

---

## 3. Design Decisions

### Decision 1: Read-compute-write pattern for consolidate() — not just LLM timeouts

The naive fix is to wrap each LLM call in `asyncio.timeout(30)`. That bounds the hang to
30 seconds per block — still 50 minutes for 100 blocks.

The right fix separates *what the lock is for* from *what takes time*:

```
Phase A — Read txn (milliseconds):
  SELECT inbox blocks, SELECT active blocks + embeddings
  COMMIT READ ← lock released

Phase B — Pure Python (seconds to minutes, no DB):
  for each block:
    embed(content)
    llm.process_block(content)
    compute near-dup, score, tier, tags
    detect contradictions
  → produce ConsolidationPlan: list[BlockDecision]

Phase C — Write txn (milliseconds):
  for each decision in plan:
    UPDATE block status/scoring
    INSERT tags, edges, contradictions
  UPDATE system_config
  COMMIT WRITE ← lock released after milliseconds
```

**Write lock duration collapses from minutes to milliseconds.**

This is the standard "read-compute-write" pattern used in any system where reads feed
expensive computation that feeds writes. It is the correct architectural boundary, not a
workaround.

**Atomicity is preserved**: Phase C either fully commits or fully rolls back. If it fails,
all blocks remain in inbox and will be retried next consolidation. This is identical
to the current rollback behavior, but the window of failure is now milliseconds not minutes.

**Inbox drift is handled**: New `learn()` calls during Phase B (between read and write)
add to the inbox. Phase C's write uses the block IDs from Phase A's read — it only
processes the snapshot it loaded. New arrivals are left for the next consolidation.
This is the same behavior as today (consolidate reads inbox at start, new learns go to
next cycle).

**LLM timeouts still apply**: `asyncio.timeout(30)` on each LLM call in Phase B is still
good practice, but the DB is not at risk during Phase B.

### Decision 2: curate() gets its own transaction, separated from consolidate()

Current:
```python
async with self._engine.begin() as conn:   # single transaction
    result = await consolidate(conn, ...)  # 100 blocks promoted ✓
    if await should_curate(conn, ...):
        await _curate(conn, ...)           # bug here → ALL 100 blocks rolled back ✗
    await set_config(conn, ...)
```

After separation:
```python
# Transaction 1: consolidation
async with self._engine.begin() as conn:
    result = await consolidate(conn, ...)
    await set_config(conn, "last_consolidated_at", ...)
# COMMIT: 100 blocks are active regardless of what follows

# Transaction 2: optional maintenance
if await _should_curate_after(conn_for_check):
    async with self._engine.begin() as conn:
        await _curate(conn, ...)
        await set_config(conn, "last_curate_at", ...)
        await conn.execute(text("PRAGMA wal_checkpoint(PASSIVE)"))
    # COMMIT: curation changes are atomic to themselves
```

A curate() failure now produces a `CurateResult` error or raises independently. It cannot
silently erase consolidation work. The two operations have independent atomicity boundaries
that match user expectations.

### Decision 3: PRAGMA busy_timeout as the safety net, not the primary protection

`busy_timeout=10000` (10 seconds) is added to production PRAGMAs. This means any write
that collides with an active writer waits up to 10 seconds before raising `OperationalError`.

This is a safety net, not primary protection. Primary protection is Decision 1 (write lock
held for milliseconds). After that change, 10 seconds is more than enough for any remaining
contention (which will be brief: the occasional simultaneous `connect()` + `learn()` pair).

**Why 10 seconds, not 30?** 30 seconds is long enough that a user would notice UI freezes.
10 seconds surfaces real problems while tolerating brief contention. Short operations
(learn, connect) should never wait more than a few milliseconds under normal load.

### Decision 4: SQL-level atomic increment for total_active_hours

Current: read-modify-write (broken under concurrent sessions):
```python
total = await get_total_active_hours(conn)  # READ
new_total = total + duration_hours          # COMPUTE in Python
await set_total_active_hours(conn, new_total)  # WRITE: last writer wins
```

Replacement: atomic SQL update:
```sql
UPDATE system_config
   SET value = CAST(value AS REAL) + :delta
 WHERE key = 'total_active_hours'
```

This eliminates the lost-update race when two sessions end concurrently. SQLite serializes
the UPDATE internally; no application-level locking needed.

### Decision 5: WAL checkpoint from curate() — passive, non-blocking

`PRAGMA wal_checkpoint(PASSIVE)` runs after the curate() transaction commits:

- **PASSIVE**: checkpoints what it can without blocking writers. If a writer is active, it
  checkpoints up to the last committed frame before the writer started. Never blocks.
- **Triggered by curate()**: curate() already signals "system is at a maintenance boundary."
  Checkpoint fits naturally here.
- **Does not truncate WAL** by default (PASSIVE). For a periodic full truncation, expose a
  separate `elfmem vacuum` CLI command using `CHECKPOINT(TRUNCATE)`.

`PRAGMA wal_autocheckpoint=500` (reduced from default 1000) also added to engine config.
This halves the max WAL size between checkpoints and gives the background auto-checkpoint
more frequent windows to run.

### Decision 6: engine.connect() for recall() — intent clarity, not just optimization

`api.py:recall()` performs no writes. It currently uses `engine.begin()`:

```python
async with self._engine.begin() as conn:   # announces "I might write"
    blocks = await hybrid_retrieve(conn, ...)  # only reads + embed call
```

Switching to `engine.connect()` is correct because:
1. It accurately signals "read-only" — SQLite may use a snapshot-isolated read
2. It doesn't acquire the WAL_WRITE_LOCK even briefly
3. It makes the code easier to audit: `engine.begin()` always means "writes will happen"

This is a correctness and clarity fix, not a performance optimization.

### Decision 7: Persist co_retrieval_staging to a DB table

The in-memory `_co_retrieval_staging: dict[tuple[str,str], int]` is cleared on process
restart, which means Hebbian learning effectively resets every time the MCP server
restarts or the agent process is restarted.

A new `co_retrieval_staging` table persists counts across restarts:

```sql
co_retrieval_staging (
    from_id  TEXT NOT NULL,
    to_id    TEXT NOT NULL,
    count    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (from_id, to_id),
    FOREIGN KEY (from_id) REFERENCES blocks(id) ON DELETE CASCADE,
    FOREIGN KEY (to_id)   REFERENCES blocks(id) ON DELETE CASCADE
)
```

FK CASCADE handles archived block cleanup automatically — no zombie staging entries.
The in-memory dict is still used as a write-buffer during a session (fast path);
it is flushed to the table when promoted (threshold reached) or cleaned up by curate().

The load path in `from_config()` reads the table and pre-populates `_co_retrieval_staging`.
The write path in `stage_and_promote_co_retrievals()` upserts to the table in the same
transaction as the edge insert (when count reaches threshold) or in a separate lightweight
transaction on batch flush.

---

## 4. What We Are NOT Doing (and Why)

| Idea | Why Not |
|------|---------|
| Application-level mutex (asyncio.Lock) for consolidate() | asyncio is single-threaded; within one process no mutex is needed. Across processes, a file lock is the right primitive — but this is Phase 2. |
| Connection pool instead of NullPool | NullPool is correct for WAL mode. Pooled connections hold WAL_READ_LOCK slots open between operations, preventing checkpoint. The current design is right. |
| Separate read and write SQLAlchemy engines | Adds complexity without benefit for Phase 1. WAL handles concurrent reads naturally. Re-evaluate if write throughput becomes a bottleneck at thousands of blocks/second. |
| asyncio.timeout() only (without read-compute-write) | Bounds the hang but not the lock duration. With timeout(30) and 100 blocks, worst-case write lock = 50 minutes. Not acceptable. |
| Moving embeddings outside the transaction only | Embeddings happen in Phase 1 (before first write) so they're already outside the write lock today. The problem is LLM calls in Phase 2 (scoring loop, contradiction detection). |

---

## 5. Implementation Steps

### Step 1: Engine hardening (low risk, do first)

**File**: `src/elfmem/db/engine.py`

Add two pragmas to `_PRODUCTION_PRAGMAS`:

```python
_PRODUCTION_PRAGMAS = [
    "PRAGMA journal_mode=WAL",
    "PRAGMA foreign_keys=ON",
    "PRAGMA synchronous=NORMAL",
    "PRAGMA cache_size=-32000",
    "PRAGMA temp_store=MEMORY",
    "PRAGMA busy_timeout=10000",      # ← new: fail fast after 10s on lock contention
    "PRAGMA wal_autocheckpoint=500",  # ← new: checkpoint more frequently, smaller WAL
]
```

No logic changes, no schema changes. Verified by starting a MemorySystem and running
`SELECT * FROM pragma_compile_options` or inspecting the DB with sqlite3 CLI.

---

### Step 2: Atomic total_active_hours increment

**File**: `src/elfmem/db/queries.py`

Replace `set_total_active_hours(conn, new_total)` with a SQL-level increment:

```python
async def increment_total_active_hours(
    conn: AsyncConnection,
    delta_hours: float,
) -> None:
    """Atomically increment total_active_hours by delta_hours.

    USE WHEN: Ending a session. Atomic SQL UPDATE prevents lost-update race
    when two sessions end concurrently (multi-process scenario).
    """
    await conn.execute(
        update(system_config)
        .where(system_config.c.key == "total_active_hours")
        .values(value=func.cast(system_config.c.value, Float) + delta_hours)
    )
```

**File**: `src/elfmem/session.py`

Replace the `base_hours + duration_hours` pattern with a call to `increment_total_active_hours`.
Remove the read (`get_total_active_hours`) that was needed to compute the new total.

```python
# Before:
new_total = base_hours + duration_hours
await set_total_active_hours(conn, new_total)

# After:
await increment_total_active_hours(conn, duration_hours)
```

The `base_hours` field on `MemorySystem` is still needed to compute `_current_active_hours()`
(the running estimate during a session). It is still read from DB at session start via
`get_total_active_hours`. Only the write path changes.

---

### Step 3: Separate curate() from consolidate() — transaction boundary fix

**File**: `src/elfmem/api.py`, `consolidate()` method

Split the single transaction into two sequential transactions:

```python
async def consolidate(self) -> ConsolidateResult:
    current_hours = self._current_active_hours()
    mem = self._config.memory

    # Transaction 1: consolidation only
    async with self._engine.begin() as conn:
        result = await consolidate(
            conn,
            llm=self._llm,
            embedding_svc=self._embedding,
            current_active_hours=current_hours,
            ...
        )
        await set_config(conn, "last_consolidated_at", datetime.now(UTC).isoformat())
    # COMMIT — inbox blocks promoted; this is safe regardless of what follows

    self._pending = 0
    self._frame_cache.clear()

    # Transaction 2: optional maintenance (separate atomicity scope)
    async with self._engine.connect() as check_conn:
        run_curate = await should_curate(
            check_conn,
            current_hours,
            curate_interval_hours=mem.curate_interval_hours,
        )

    if run_curate:
        async with self._engine.begin() as conn:
            await _curate(
                conn,
                current_active_hours=current_hours,
                prune_threshold=mem.prune_threshold,
                edge_prune_threshold=mem.edge_prune_threshold,
                reinforce_top_n=mem.curate_reinforce_top_n,
            )
            await set_config(conn, "last_curate_at", str(current_hours))
            await conn.execute(text("PRAGMA wal_checkpoint(PASSIVE)"))
        # COMMIT — curation is its own atomic operation

    self._record_op("consolidate", result.summary)
    return result
```

The `should_curate` check uses `engine.connect()` (read-only) rather than reopening a write
transaction just to read a config value.

---

### Step 4: Read-compute-write refactor of consolidate() — the core change

**File**: `src/elfmem/operations/consolidate.py`

Restructure `consolidate()` into three clearly named internal functions:

```python
async def consolidate(
    conn: AsyncConnection,
    *,
    llm: LLMService,
    embedding_svc: EmbeddingService,
    current_active_hours: float,
    ...
) -> ConsolidateResult:
    """Promote all inbox blocks through the full consolidation pipeline.

    Three-phase: read inputs → compute with LLM/embed → write results.
    The database connection is only used in the read and write phases.
    LLM and embedding calls happen between them with no DB lock held.
    """
    # Phase A: read all inputs in the current connection (caller's transaction)
    plan = await _build_consolidation_plan(
        conn,
        embedding_svc=embedding_svc,
        current_active_hours=current_active_hours,
        ...
    )
    if plan.is_empty:
        return ConsolidateResult(processed=0, promoted=0, deduplicated=0, edges_created=0)

    # Phase B: LLM scoring — all external I/O, no DB access
    # Caller must release the transaction before calling this.
    # This function takes plain data (no AsyncConnection).
    decisions = await _score_with_llm(plan, llm=llm, ...)

    # Phase C: write results in a new transaction (caller opens it)
    return await _write_decisions(conn, decisions=decisions, current_active_hours=current_active_hours)
```

Because `consolidate()` is called from `api.py` inside `engine.begin()`, the refactor
changes the call site in `api.py` to match the three-phase structure:

```python
# api.py: consolidate() method

# Phase A: read (short transaction)
async with self._engine.begin() as conn:
    plan = await _read_consolidation_inputs(conn, ...)
# COMMIT

if plan.is_empty:
    self._pending = 0
    return ConsolidateResult(...)

# Phase B: LLM/embed (no DB lock)
decisions = await _score_with_llm(plan, llm=self._llm, embedding_svc=self._embedding)

# Phase C: write (short transaction)
async with self._engine.begin() as conn:
    result = await _write_consolidation_results(conn, decisions=decisions, ...)
    await set_config(conn, "last_consolidated_at", ...)
# COMMIT
```

#### Internal data structures for the plan

```python
@dataclass
class BlockDecision:
    """The computed outcome for one inbox block after LLM scoring."""
    block_id: str
    content: str
    action: Literal["promote", "archive_exact_dup", "archive_near_dup", "supersede"]
    supersedes_id: str | None            # for near-dup supersede path
    alignment_score: float
    tags: list[str]
    summary: str | None
    embedding: np.ndarray | None         # summary embedding
    decay_lambda: float
    confidence: float
    token_count: int

@dataclass
class EdgeDecision:
    """An edge to create between two promoted blocks."""
    from_id: str
    to_id: str
    weight: float
    relation_type: str
    origin: str

@dataclass
class ConsolidationPlan:
    """Complete read-phase snapshot. Everything LLM scoring needs."""
    inbox: list[dict]                    # raw inbox block rows
    active_vecs: dict[str, tuple[dict, np.ndarray]]  # content_key → (block, vec)
    current_active_hours: float

    @property
    def is_empty(self) -> bool:
        return len(self.inbox) == 0

@dataclass
class ConsolidationDecisions:
    """Complete LLM-phase output. Everything the write phase needs."""
    block_decisions: list[BlockDecision]
    edge_decisions: list[EdgeDecision]
    contradiction_pairs: list[tuple[str, str, float]]
```

This makes the data flow explicit: `ConsolidationPlan` goes in, `ConsolidationDecisions`
comes out, and the write phase only needs the decisions — no LLM, no embed, no read.

#### LLM timeout in Phase B

Each LLM call in `_score_with_llm()` is wrapped:

```python
try:
    analysis = await asyncio.wait_for(
        llm.process_block(content, ""),
        timeout=30.0,
    )
except asyncio.TimeoutError:
    # Promote with safe defaults — do not block the batch on one slow block
    analysis = _default_analysis()
```

A timed-out block gets `alignment_score=0.5` (neutral), no tags, no summary. It is
promoted to active and will be re-processed on the next consolidation if the LLM recovers.
The key invariant is: **a single slow LLM call does not hold up the entire batch or the DB**.

---

### Step 5: api.py:recall() — switch to engine.connect()

**File**: `src/elfmem/api.py`, `recall()` method

```python
# Before:
async with self._engine.begin() as conn:
    blocks = await hybrid_retrieve(conn, ...)

# After:
async with self._engine.connect() as conn:
    blocks = await hybrid_retrieve(conn, ...)
```

One-line change. `hybrid_retrieve` contains no writes so this is behaviorally identical,
but signals "read-only" intent clearly and avoids escalating to a write transaction.

---

### Step 6: Persist co_retrieval_staging table

**File**: `src/elfmem/db/models.py`

Add the new table:

```python
co_retrieval_staging = Table(
    "co_retrieval_staging",
    metadata,
    Column("from_id", Text, ForeignKey("blocks.id", ondelete="CASCADE"), nullable=False),
    Column("to_id",   Text, ForeignKey("blocks.id", ondelete="CASCADE"), nullable=False),
    Column("count",   Integer, nullable=False, default=0),
    UniqueConstraint("from_id", "to_id", name="uq_co_retrieval_staging"),
)
```

FK CASCADE means when either block is archived, its staging rows are deleted automatically.
No separate zombie cleanup pass needed (current `curate()` staging cleanup becomes redundant).

**File**: `src/elfmem/db/queries.py`

Add:
- `upsert_co_retrieval_count(conn, from_id, to_id, increment=1) → int` — returns new count
- `load_co_retrieval_staging(conn) → dict[tuple[str,str], int]` — reads all rows
- `delete_co_retrieval_pair(conn, from_id, to_id) → None` — called on promotion

**File**: `src/elfmem/api.py`, `from_config()`

Load staging table into `_co_retrieval_staging` on startup:

```python
async with engine.begin() as conn:
    await conn.run_sync(metadata.create_all)
    await seed_builtin_data(conn)
    initial_pending = await get_inbox_count(conn)
    # Restore Hebbian staging across process restarts
    initial_staging = await load_co_retrieval_staging(conn)
    ...
return cls(..., initial_staging=initial_staging, ...)
```

**File**: `src/elfmem/memory/graph.py`, `stage_and_promote_co_retrievals()`

When count reaches threshold: call `delete_co_retrieval_pair(conn, ...)` inside the same
transaction as `insert_edge(...)`. On count increment (below threshold): call
`upsert_co_retrieval_count(conn, ...)`. The in-memory dict remains as a fast-path buffer
for the current session; DB is the source of truth.

---

## 6. Testing

Each step has targeted tests. No new test infrastructure needed — all tests use the existing
`test_engine` fixture (in-memory SQLite, `StaticPool`, no LLM cost).

| Step | Test approach |
|------|--------------|
| 1: Pragmas | `PRAGMA` query after engine creation; check busy_timeout value |
| 2: Atomic hours | Two concurrent `end_session()` calls; verify total = sum of both durations |
| 3: curate isolation | Mock curate to raise; verify consolidate result is still visible in DB |
| 4: Read-compute-write | Insert 5 inbox blocks; verify all promoted in Phase C; verify no DB writes during Phase B by checking connection not in begin() state |
| 5: recall() read-only | Verify no rows changed after recall(); verify no write transaction opened |
| 6: Staging persistence | Load staging, add count, simulate process restart (clear in-memory dict, re-load from DB), verify counts restored |

---

## 7. Migration

**Schema change**: Step 6 adds `co_retrieval_staging` table. `metadata.create_all` handles
this automatically on first run (`IF NOT EXISTS` semantics). No data migration needed — existing
databases simply start with an empty staging table. In-flight staging counts (if any) are lost,
which is the existing behavior on process restart. No regression.

All other steps are pure Python/query changes — no schema migration.

---

## 8. Changelog Entry

```markdown
## [Unreleased]

### Changed
- `consolidate()` refactored to read-compute-write pattern: LLM and embedding calls now
  happen outside the database write transaction. Write lock duration reduced from O(n × LLM_latency)
  to O(n × row_size). No behavior change; inbox snapshot semantics preserved.
- `curate()` auto-trigger runs in its own transaction after `consolidate()` commits.
  A curate() failure no longer rolls back a successful consolidation.
- `api.py:recall()` now uses a read-only database connection (`engine.connect()`) instead
  of a write transaction. No behavior change.
- `total_active_hours` is now incremented via an atomic SQL UPDATE. Fixes a lost-update
  race when two sessions end concurrently in a multi-process configuration.

### Added
- `PRAGMA busy_timeout=10000`: write contention now surfaces as a clear OperationalError
  after 10 seconds instead of hanging indefinitely.
- `PRAGMA wal_autocheckpoint=500`: WAL file is checkpointed more frequently, preventing
  unbounded disk growth under sustained write load.
- `PRAGMA wal_checkpoint(PASSIVE)` runs after each `curate()` to reclaim WAL disk space.
- `co_retrieval_staging` table: Hebbian co-retrieval counts are now persisted across process
  restarts. Staging counts survive MCP server restarts and agent process cycling.
```
