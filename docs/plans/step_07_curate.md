# Step 7: curate() — Implementation Plan

## Overview

Build the third and final vertical slice: the `curate()` maintenance operation.
`curate()` is the garbage collector — it archives decayed blocks, prunes weak
edges, and reinforces top-scoring active blocks to prevent useful knowledge from
fading between explicit recalls.

`curate()` runs automatically at `begin_session()` when enough active hours have
elapsed since the last curate run.

**Key design decisions (locked):**
- `curate()` is triggered automatically at `begin_session()` (not `end_session()`)
- Trigger condition: `elapsed_active_hours >= curate_interval_hours` (default: 40)
- Three-phase operation: archive decayed → prune edges → reinforce top-N
- Archive reason is always set: `"decayed"` for blocks below prune threshold
- Edge pruning uses weight threshold (0.10); CASCADE on archived blocks handles the rest
- Top-N reinforcement updates `last_reinforced_at` to current `total_active_hours`
- No LLM or embedding calls — pure database operations

---

## Files to Create/Modify

| File | Action | Purpose |
|------|--------|---------|
| `src/elfmem/operations/curate.py` | Create | curate() operation + helpers |
| `src/elfmem/api.py` | Modify | Add curate() method, integrate into begin_session() |

---

## Module Design

### 1. `src/elfmem/operations/curate.py`

**Purpose:** Maintenance operation that prunes decayed blocks and weak edges,
and reinforces top-scoring active blocks. Pure database operations — no LLM
or embedding calls.

**Imports:**
```python
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncConnection

from elfmem.db import queries
from elfmem.scoring import LAMBDA, ScoringWeights, compute_recency, compute_score, log_normalise_reinforcement
from elfmem.types import ArchiveReason, CurateResult, DecayTier
```

**Constants:**

```python
PRUNE_THRESHOLD: float = 0.05           # blocks with recency below this are archived
EDGE_PRUNE_THRESHOLD: float = 0.10      # edges with weight below this are deleted
CURATE_REINFORCE_TOP_N: int = 5         # reinforce top N blocks by composite score
CURATE_INTERVAL_HOURS: float = 40.0     # active hours between auto-curate runs
```

**Main function:**

```python
async def curate(
    conn: AsyncConnection,
    *,
    current_active_hours: float,
    prune_threshold: float = PRUNE_THRESHOLD,
    edge_prune_threshold: float = EDGE_PRUNE_THRESHOLD,
    reinforce_top_n: int = CURATE_REINFORCE_TOP_N,
) -> CurateResult:
    """Run maintenance on the memory store.

    Three phases:
    1. Archive decayed blocks (recency < prune_threshold)
    2. Prune weak edges (weight < edge_prune_threshold)
    3. Reinforce top-N active blocks by composite score

    This operation makes no LLM or embedding calls — it is purely
    database-driven. Designed to be fast and safe to auto-trigger.

    Args:
        conn: Database connection (within a transaction).
        current_active_hours: Current total active hours for recency computation.
        prune_threshold: Recency threshold below which blocks are archived.
        edge_prune_threshold: Edge weight threshold below which edges are deleted.
        reinforce_top_n: Number of top-scoring blocks to reinforce.

    Returns:
        CurateResult with counts of archived, edges_pruned, reinforced.
    """
```

**Decomposed helper functions (each ≤50 lines):**

```python
async def _archive_decayed_blocks(
    conn: AsyncConnection,
    *,
    current_active_hours: float,
    prune_threshold: float,
) -> int:
    """Archive active blocks whose recency has fallen below the prune threshold.

    For each active block:
    1. Determine effective decay tier from tags
    2. Compute recency = exp(-λ × (current_active_hours - last_reinforced_at))
    3. If recency < prune_threshold: archive with reason="decayed"

    Returns count of blocks archived.
    """
```

```python
async def _prune_weak_edges(
    conn: AsyncConnection,
    *,
    edge_prune_threshold: float,
) -> int:
    """Delete edges with weight below the prune threshold.

    Uses a single DELETE query: DELETE FROM edges WHERE weight < threshold.

    Returns count of edges pruned.
    """
```

```python
async def _reinforce_top_blocks(
    conn: AsyncConnection,
    *,
    current_active_hours: float,
    top_n: int,
) -> int:
    """Reinforce the top-N active blocks by composite score.

    Prevents useful-but-unretrieved blocks from decaying between recalls.
    Uses the SELF_WEIGHTS preset for scoring (confidence + recency +
    centrality + reinforcement — no similarity since there's no query).

    Steps:
    1. Fetch all active blocks with their scoring components
    2. Compute composite score for each (using renormalized SELF_WEIGHTS)
    3. Sort by score descending, take top N
    4. Update last_reinforced_at to current_active_hours for those blocks
    5. Increment reinforcement_count for those blocks

    Returns count of blocks reinforced.
    """
```

```python
async def should_curate(
    conn: AsyncConnection,
    *,
    curate_interval_hours: float = CURATE_INTERVAL_HOURS,
) -> bool:
    """Check whether curate() should run based on elapsed active hours.

    Reads 'last_curate_at' from system_config. If not set (first run),
    returns True. Otherwise returns True when:
        total_active_hours - last_curate_at >= curate_interval_hours

    Args:
        conn: Database connection.
        curate_interval_hours: Minimum active hours between curate runs.

    Returns:
        True if curate is due.
    """
```

```python
async def _update_last_curate_at(
    conn: AsyncConnection,
    current_active_hours: float,
) -> None:
    """Record the active hours at which curate() last ran.

    Calls queries.set_config(conn, "last_curate_at", str(current_active_hours)).
    """
```

**Processing flow:**

1. Call `_archive_decayed_blocks(conn, ...)` → count of archived blocks
2. Call `_prune_weak_edges(conn, ...)` → count of edges pruned
3. Call `_reinforce_top_blocks(conn, ...)` → count of blocks reinforced
4. Call `_update_last_curate_at(conn, current_active_hours)`
5. Return `CurateResult(archived=..., edges_pruned=..., reinforced=...)`

**Key implementation notes for `_archive_decayed_blocks`:**
- Fetch all active blocks via `queries.get_active_blocks(conn)`
- For each block, fetch tags via `queries.get_tags(conn, block_id)`
- Determine effective decay tier using `memory.blocks.determine_decay_tier(tags)`
  (imported from Step 5 — lowest λ wins)
- Compute `hours_since = current_active_hours - block.last_reinforced_at`
- Compute `recency = compute_recency(tier, hours_since)`
- If `recency < prune_threshold`: call `queries.update_block_status(conn, block_id, "archived", archive_reason="decayed")`
- Note: CASCADE on the edges table means archiving a block automatically deletes
  its edges. The edge count from `_prune_weak_edges` only covers explicit weak-edge
  pruning, not cascade deletes.

**Key implementation notes for `_reinforce_top_blocks`:**
- Scoring context: there is no query, so use `SELF_WEIGHTS.renormalized_without_similarity()`
  to compute composite scores without the similarity component
- For each active block, compute all 5 scoring components:
  - `similarity = 0.0` (no query)
  - `confidence = block.confidence` (stored in DB)
  - `recency = compute_recency(tier, hours_since)` (computed)
  - `centrality = queries.get_weighted_degree(conn, block_id)` / max_degree (computed)
  - `reinforcement = log_normalise_reinforcement(block.reinforcement_count, max_reinforcement)` (computed)
- Sort by composite score descending, take top N
- Call `queries.reinforce_blocks(conn, [block_ids], current_active_hours)`
- Note: centrality requires finding `max_degree` across all active blocks.
  Compute all degrees first, then normalise.

**Key implementation notes for `_prune_weak_edges`:**
- This is a simple bulk DELETE: `DELETE FROM edges WHERE weight < :threshold`
- Add this query to `db/queries.py` (see Dependencies section)

---

### 2. `src/elfmem/api.py` — Modifications

**Add curate method and integrate into begin_session:**

```python
async def curate(
    self,
    *,
    prune_threshold: float = 0.05,
    edge_prune_threshold: float = 0.10,
    reinforce_top_n: int = 5,
) -> CurateResult:
    """Manually trigger maintenance. Archives decayed blocks, prunes
    weak edges, and reinforces top-scoring blocks.

    Normally runs automatically at begin_session(). Can be called
    explicitly for immediate maintenance.

    Returns:
        CurateResult with counts.
    """
```

**Modify `begin_session()` to auto-trigger curate:**

The existing `begin_session()` (from Step 5) should be modified to check
whether curate is due and run it before the session starts:

```python
async def begin_session(self, task_type: str = "general") -> None:
    """Start a new session. Triggers curate() if overdue.

    Checks elapsed active hours since last curate. If >= curate_interval_hours,
    runs curate() before starting the session.
    """
    async with self._engine.begin() as conn:
        # Auto-curate if due
        if await should_curate(conn):
            current_hours = await queries.get_total_active_hours(conn)
            await curate(conn, current_active_hours=current_hours)

        # Start session (existing code from Step 5)
        self._session_id = await session.begin_session(conn, task_type=task_type)
```

**Add import:**
```python
from elfmem.operations.curate import curate as curate_op, should_curate
```

---

### 3. `src/elfmem/db/queries.py` — New Query

**Add one new query function needed by curate:**

```python
async def prune_weak_edges(
    conn: AsyncConnection,
    threshold: float,
) -> int:
    """Delete all edges with weight below the given threshold.

    Args:
        conn: Database connection.
        threshold: Weight threshold; edges strictly below this are deleted.

    Returns:
        Number of edges deleted.
    """
```

**Implementation:**
```python
result = await conn.execute(
    delete(edges).where(edges.c.weight < threshold)
)
return result.rowcount
```

This is the only new query needed. All other queries used by curate() already
exist from Step 3 (get_active_blocks, get_tags, update_block_status,
reinforce_blocks, get_weighted_degree, get_config, set_config,
get_total_active_hours).

---

## Key Invariants

1. **No LLM or embedding calls** — curate is pure database operations
2. **Archive reason always set** — every archived block has `archive_reason="decayed"`
3. **Idempotent on empty corpus** — curate on zero active blocks returns
   `CurateResult(0, 0, 0)` with no side effects
4. **Edge CASCADE on archive** — archiving a block automatically deletes its edges
   via ON DELETE CASCADE; `edges_pruned` count in CurateResult only counts
   explicit weak-edge pruning, not cascade deletes
5. **`last_curate_at` updated after every run** — prevents re-triggering until
   enough active hours elapse
6. **Top-N reinforcement uses queryless scoring** — similarity=0.0, weights
   renormalized from SELF_WEIGHTS
7. **`curate()` runs before session starts** — auto-trigger in `begin_session()`
   ensures clean state at session start
8. **Block state only moves forward** — inbox → active → archived (curate only
   moves active → archived)
9. **Edge prune threshold is strict less-than** — `weight < 0.10` (edge at
   exactly 0.10 is retained)

## Security Considerations

1. **No SQL injection** — all queries via SQLAlchemy expression language
2. **Bounded operation** — curate processes at most `len(active_blocks)` blocks
   and `len(edges)` edges; no unbounded loops
3. **No external calls** — no network I/O, no API keys needed

## Edge Cases

1. **No active blocks** — all three phases are no-ops; returns `CurateResult(0, 0, 0)`
2. **All blocks below prune threshold** — all archived; `_reinforce_top_blocks`
   finds zero active blocks and reinforces none
3. **No edges exist** — `_prune_weak_edges` returns 0
4. **`last_curate_at` not set** — first run; `should_curate()` returns True
5. **`reinforce_top_n` > active block count** — reinforces all active blocks
   (no error; just reinforce whatever exists)
6. **Block archived by curate was in mid-retrieval** — not a concern because
   curate runs at `begin_session()`, before any retrieval calls
7. **Edge weight exactly at threshold (0.10)** — retained (strict less-than)
8. **Permanent block** — recency never reaches prune threshold in practice
   (would take ~299,600 active hours); always survives curate
9. **curate called manually during session** — valid; uses current active hours
   from `compute_current_active_hours()`

## Dependencies

- `elfmem.db.queries` (Step 3) — get_active_blocks, get_tags, update_block_status,
  reinforce_blocks, get_weighted_degree, get_config, set_config,
  get_total_active_hours + new `prune_weak_edges` function
- `elfmem.memory.blocks` (Step 5) — `determine_decay_tier` for tag → tier mapping
- `elfmem.scoring` (Step 2) — compute_recency, compute_score,
  log_normalise_reinforcement, SELF_WEIGHTS
- `elfmem.types` (Step 1) — ArchiveReason, CurateResult, DecayTier
- `elfmem.session` (Step 5) — compute_current_active_hours (for manual curate)
- `elfmem.operations.curate` imported by `elfmem.api` (Step 5)

## Done Criteria

1. TC-L-007: `curate()` archives blocks with `recency < prune_threshold`
2. TC-L-008: `curate()` reinforces top-N active blocks by composite score
3. TC-L-011: `begin_session()` triggers `curate()` when elapsed active hours >= interval
4. TC-D-001: Standard block survival timeline — recency values at various hours_since
5. TC-D-002: Ephemeral block reaches prune threshold at ~60 active hours
6. TC-D-003: Permanent block near-immortal (never pruned in practice)
7. TC-D-005: Reinforcement resets decay clock (last_reinforced_at updated)
8. TC-D-006: Pre-filter correctly excludes old blocks (search_window_hours boundary)
9. TC-D-007: Durable block survives 300 hours without reinforcement
10. TC-D-010: Archive reason set correctly (`decayed` for recency < threshold)
11. TC-G-006: Weak edges (weight < 0.10) pruned at curate()
12. TC-G-009: Archived block's edges CASCADE deleted
13. `curate()` on empty corpus returns `CurateResult(0, 0, 0)` with no side effects
14. `should_curate()` returns True when no `last_curate_at` exists (first run)
15. `should_curate()` returns False when elapsed hours < interval
16. `mypy --strict` passes on all new files
17. `ruff check` clean
