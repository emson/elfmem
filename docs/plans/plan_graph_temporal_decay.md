# Plan: C2 — Edge Temporal Decay at curate()

**Status:** Ready to implement
**Phase:** C2 (depends on A2 schema extension — ✅ complete)
**Date:** 2026-03-09
**Biological analogy:** Long-Term Depression — unused synaptic connections weaken over time

---

## 1. Problem Statement

Edges in the knowledge graph are created once and persist indefinitely at full weight.
A similarity edge formed between two blocks 300 active hours ago — blocks never
co-retrieved since — carries the same retrieval authority as an edge reinforced
yesterday. This is wrong and actively harmful:

- **Recall poisoning:** Stale edges pull irrelevant blocks into graph expansion, diluting
  context quality.
- **Frozen graph:** The graph cannot evolve as the agent's knowledge domain shifts.
  Regions the agent no longer visits remain connected at full weight forever.
- **Inconsistency with block model:** Blocks decay via `compute_recency()`. Edges between
  them should decay too. A decayed block that barely survives archival should not carry
  strong edges — the graph and the blocks tell contradictory stories.

The `last_active_hours` column was added in A2 precisely to enable this. It is populated
by `reinforce_edges()` and by `insert_agent_edge()` — but currently never read.

---

## 2. What C2 Adds

Three new behaviors:

1. **Session anchor at creation.** `insert_edge()` and `upsert_outcome_edge()` gain an
   optional `last_active_hours` parameter. Callers (`consolidate.py`, `outcome.py`)
   pass `current_active_hours` so every new edge records when it was born in session
   time, not just wall-clock time.

2. **Temporal decay at curate().** A new `_prune_decayed_edges()` helper runs after
   the existing `prune_weak_edges()`. It fetches all edges, computes an effective
   weight accounting for elapsed active hours, and deletes edges whose effective weight
   has fallen below the prune threshold.

3. **Observability.** `CurateResult` gains `edges_decayed: int` to distinguish
   temporally-pruned edges (stale relationships) from statically-pruned ones (never
   proved useful). Agents can observe "12 edges decayed this cycle" and understand
   their knowledge graph is evolving.

---

## 3. Decay Formula (from research doc §4.6)

### 3.1 Edge decay rate

```
λ_edge = min(λ_a, λ_b) × 0.5
```

Where `λ_a`, `λ_b` are the decay rates of the two endpoint blocks (from their
`DecayTier`). The edge inherits durability from the **more stable** block. Halving
gives edges more longevity than their weakest block — relationships outlast individual
memories.

**Established edge bonus** (Long-Term Depression protection):
```
if reinforcement_count >= ESTABLISHED_EDGE_THRESHOLD (10):
    λ_edge *= 0.5
```

Edges proven through ≥10 co-retrievals decay at one-quarter the base rate. These are
genuine, tested relationships — Long-Term Depression should not erase them casually.

### 3.2 Effective weight at curate time

```
hours_since = max(0.0, current_active_hours − edge.last_active_hours)
effective_weight = edge.weight × exp(−λ_edge × hours_since)

if effective_weight < edge_prune_threshold:
    DELETE edge
```

**`hours_since` uses active hours** (session-aware clock), not wall time. An agent
that takes a two-week holiday accumulates zero active hours — edges do not decay
during the absence. Consistent with block decay model.

### 3.3 Worked examples

| Scenario | λ_edge | hours_since | weight | effective | outcome |
|----------|--------|-------------|--------|-----------|---------|
| Standard blocks, stale | 0.005 | 500 | 0.50 | 0.041 | PRUNED |
| Standard blocks, recent | 0.005 | 50 | 0.50 | 0.390 | survives |
| Established (count=12) | 0.0025 | 400 | 0.50 | 0.184 | survives |
| Non-established (count=5) | 0.005 | 400 | 0.50 | 0.068 | PRUNED |
| Permanent+Standard blocks | 0.000005 | 500 | 0.50 | 0.499 | survives |
| Ephemeral blocks | 0.025 | 100 | 0.50 | 0.041 | PRUNED |

Constitutional edges between PERMANENT blocks (λ_edge ≈ 5e-6) are effectively
immortal, consistent with their blocks.

---

## 4. Design Decisions

### 4.1 `NULL last_active_hours` → skip temporal decay

When `last_active_hours IS NULL`, the edge has no session anchor. This happens for:
- Edges created by old code before this change (rare, in-memory SQLite means fresh each test run)
- Theoretically possible future edge creation paths that forget the parameter

**Decision:** Skip temporal decay entirely for NULL-anchored edges. Static pruning
(`prune_weak_edges`) still applies to them.

**Rationale:** We cannot safely assign a decay age to an edge with no timestamp.
Deleting a potentially-valid edge because we don't know when it was created would be
a false positive. The static threshold (weight < 0.10 AND reinforcement_count=0) is
the correct safety net for these.

**Consequence:** Once `insert_edge()` and `upsert_outcome_edge()` are updated to always
pass `last_active_hours`, NULL edges become rare (only legacy or uncorrected callers).

### 4.2 Agent-origin edges protected

Edges with `origin == "agent"` skip temporal decay. The agent explicitly asserted these
connections. If they're wrong, the agent can revoke them explicitly (via future
`disconnect()` API or by letting the static threshold eventually remove very-low-weight ones).

This is consistent with `find_displaceable_edge()` which already protects agent-origin
edges from degree-cap displacement.

### 4.3 Interaction with `prune_weak_edges()`

The two pruning mechanisms are **complementary, not redundant**:

| Mechanism | Catches | Guards |
|-----------|---------|--------|
| `prune_weak_edges()` | Edges that were never useful (weight < threshold AND count=0) | Fast; no per-row computation needed |
| `_prune_decayed_edges()` | Edges that WERE useful but have gone stale | Applies to all edges including well-reinforced ones |

**Run order:** `prune_weak_edges()` first (fast DB DELETE), then `_prune_decayed_edges()`
(slower, requires per-edge computation). Archiving blocks first (phase 1 of curate) means
orphaned-endpoint edges are already cascade-deleted before decay runs.

A reinforced edge (count > 0) that has gone stale **will** be pruned by temporal decay
even though `prune_weak_edges()` spares it. This is the correct behavior: being
reinforced 2 years ago is not the same as being actively used.

### 4.4 Batch tag fetch for efficiency

Computing tier requires tags for both endpoints. Rather than N individual `get_tags()`
calls, use the existing `get_tags_batch()` (added in B2) to fetch all endpoint tags in
one query. At Phase 1 scale (≤500 blocks, degree cap 10, ≤2500 edges), this is fast.

### 4.5 `compute_lambda_edge()` extension is backward compatible

Adding `reinforcement_count: int = 0` as a keyword-only parameter with default 0
preserves all existing callers. When called with the default, the output is identical
to the current formula. No existing tests break.

---

## 5. Files Changed

| File | Change |
|------|--------|
| `src/elfmem/scoring.py` | Add `ESTABLISHED_EDGE_THRESHOLD = 10`; extend `compute_lambda_edge()` |
| `src/elfmem/db/queries.py` | Add `get_all_edges()`, `delete_edges_bulk()`, `count_edges()`; add `last_active_hours` param to `insert_edge()`, `upsert_outcome_edge()`; update UPDATE clause in `upsert_outcome_edge()` to refresh `last_active_hours` |
| `src/elfmem/operations/consolidate.py` | Pass `last_active_hours=current_active_hours` to `insert_edge()` |
| `src/elfmem/operations/outcome.py` | Pass `last_active_hours=current_active_hours` to `upsert_outcome_edge()` |
| `src/elfmem/operations/curate.py` | Add `_prune_decayed_edges()`; call it in `curate()`; add `count_edges()` after pruning |
| `src/elfmem/types.py` | Add `edges_decayed: int = 0` and `total_edges_after: int = 0` to `CurateResult`; update `summary` with contextual tips, `to_dict()` |
| `tests/test_curate.py` | Add `TestEdgeTemporalDecay` class (5 tests) |

No schema changes — `last_active_hours` column already exists (A2).
No config changes — uses existing `edge_prune_threshold`.
No MCP changes — `CurateResult.__str__` updates automatically.

---

## 6. Detailed Implementation

### Step 1: `scoring.py` — extend `compute_lambda_edge()`

Add constant and extend function. Placed immediately above `compute_lambda_edge()`.

```python
# Reinforcement count above which edge λ is halved (established relationship bonus).
# Mirrors Long-Term Depression protection: well-used edges decay at 1/4 the base rate.
ESTABLISHED_EDGE_THRESHOLD: int = 10


def compute_lambda_edge(
    tier_a: DecayTier,
    tier_b: DecayTier,
    *,
    reinforcement_count: int = 0,
) -> float:
    """Compute edge decay rate from endpoint block tiers and reinforcement history.

    λ_edge = min(λ_a, λ_b) × 0.5

    The edge inherits durability from the more stable block. Halving gives edges
    more longevity than their weakest-endpoint block.

    Established edges (reinforcement_count ≥ ESTABLISHED_EDGE_THRESHOLD) have
    λ halved again — they decay at one-quarter the base rate (LTD protection).

    Backward compatible: default reinforcement_count=0 preserves existing output.
    """
    lam = min(LAMBDA[tier_a], LAMBDA[tier_b]) * 0.5
    if reinforcement_count >= ESTABLISHED_EDGE_THRESHOLD:
        lam *= 0.5
    return lam
```

### Step 2: `queries.py` — two new functions + two updated signatures

**`get_all_edges()`** — placed near `get_edges_for_block()`:

```python
async def get_all_edges(conn: AsyncConnection) -> list[dict[str, Any]]:
    """Fetch all edge rows. Used by curate() for temporal decay computation."""
    result = await conn.execute(select(edges))
    return [dict(row) for row in result.mappings()]
```

**`count_edges()`** — lightweight count for `CurateResult.total_edges_after`:

```python
async def count_edges(conn: AsyncConnection) -> int:
    """Return total number of edges in the graph."""
    result = await conn.execute(select(func.count()).select_from(edges))
    return result.scalar() or 0
```

**`delete_edges_bulk()`** — placed near `delete_edge()`:

```python
async def delete_edges_bulk(
    conn: AsyncConnection,
    pairs: list[tuple[str, str]],
) -> int:
    """Delete multiple edges by canonical (from_id, to_id) pairs.

    Returns count of edges actually deleted. Silently skips missing pairs.
    """
    if not pairs:
        return 0
    deleted = 0
    for from_id, to_id in pairs:
        result = await conn.execute(
            delete(edges).where(
                and_(edges.c.from_id == from_id, edges.c.to_id == to_id)
            )
        )
        deleted += result.rowcount or 0
    return deleted
```

**`insert_edge()` signature update** (backward compatible):

```python
async def insert_edge(
    conn: AsyncConnection,
    *,
    from_id: str,
    to_id: str,
    weight: float,
    relation_type: str = "similar",
    origin: str = "similarity",
    note: str | None = None,
    last_active_hours: float | None = None,   # NEW — session anchor at creation
) -> None:
    """Insert a similarity edge idempotently. from_id < to_id enforced by caller."""
    await conn.execute(
        insert(edges).prefix_with("OR IGNORE").values(
            from_id=from_id,
            to_id=to_id,
            weight=weight,
            reinforcement_count=0,
            created_at=_now_iso(),
            relation_type=relation_type,
            origin=origin,
            last_active_hours=last_active_hours,   # was hardcoded None
            note=note,
        )
    )
```

**`upsert_outcome_edge()` signature update** (backward compatible):

```python
async def upsert_outcome_edge(
    conn: AsyncConnection,
    *,
    from_id: str,
    to_id: str,
    weight: float,
    note: str | None = None,
    last_active_hours: float | None = None,   # NEW — session anchor at creation and re-confirmation
) -> bool:
    ...
    result = await conn.execute(
        insert(edges).prefix_with("OR IGNORE").values(
            ...
            last_active_hours=last_active_hours,   # was hardcoded None
            ...
        )
    )
    if result.rowcount == 1:
        return True  # new edge

    # Edge already exists — reinforce it AND refresh its temporal anchor.
    # Outcome confirmation is active use: the agent is saying "this connection
    # was relevant to a real-world result." Reset the decay clock.
    await conn.execute(
        update(edges)
        .where(and_(edges.c.from_id == from_id, edges.c.to_id == to_id))
        .values(
            reinforcement_count=edges.c.reinforcement_count + 1,
            last_active_hours=last_active_hours,  # NEW — was omitted; key fix from simulation
        )
    )
    return False
```

**Why this matters (from agent simulation):** Without this fix, an edge confirmed 8 times
via `outcome()` still decays from its creation timestamp. Outcome confirmation is the
strongest signal of usefulness in the system — it must refresh the decay clock.
See simulation scenario 4 for the full failure trace.

### Step 3: `consolidate.py` — pass `current_active_hours` to `insert_edge()`

Single-line change at the call site:

```python
await insert_edge(
    conn,
    from_id=from_id,
    to_id=to_id,
    weight=score,
    relation_type="similar",
    origin="similarity",
    last_active_hours=current_active_hours,   # NEW
)
```

### Step 4: `outcome.py` — pass `current_active_hours` to `upsert_outcome_edge()`

```python
created = await upsert_outcome_edge(
    conn,
    from_id=from_id,
    to_id=to_id,
    weight=outcome_weight,
    last_active_hours=current_active_hours,   # NEW
)
```

### Step 5: `types.py` — extend `CurateResult`

Add `edges_decayed` and `total_edges_after` fields; update `summary` and `to_dict()`.
`total_edges_after` gives agents a denominator — "12 decayed (35 remain)" is actionable;
"12 decayed" in isolation is not (from agent simulation scenario 5).

The contextual tip and escalation in `summary` close the observability gap: an agent
that shifted domains for 500 hours needs to know it should run `consolidate()` to rebuild
its graph (from agent simulation scenario 3).

```python
@dataclass(frozen=True)
class CurateResult:
    archived: int
    edges_pruned: int
    reinforced: int
    constitutional_reinforced: int = 0
    edges_decayed: int = 0       # NEW: temporally-decayed edges removed at curate()
    total_edges_after: int = 0   # NEW: denominator — graph size after all pruning

    @property
    def summary(self) -> str:
        if not any([
            self.archived, self.edges_pruned, self.edges_decayed,
            self.reinforced, self.constitutional_reinforced,
        ]):
            return "Curated: nothing required."
        parts: list[str] = []
        if self.archived:
            parts.append(f"{self.archived} archived")
        if self.edges_pruned:
            parts.append(f"{self.edges_pruned} edges pruned")
        if self.edges_decayed:
            edge_info = f"{self.edges_decayed} edges decayed"
            if self.total_edges_after > 0:
                edge_info += f" ({self.total_edges_after} remain)"
            parts.append(edge_info)
        if self.reinforced:
            parts.append(f"{self.reinforced} reinforced")
        if self.constitutional_reinforced:
            parts.append(f"{self.constitutional_reinforced} constitutional reinforced")

        result = f"Curated: {', '.join(parts)}."

        # Contextual tip: tell the agent what to do next.
        # Escalate when >25% of the graph decayed in one cycle (catastrophic signal).
        if self.edges_decayed > 0:
            total_before = self.total_edges_after + self.edges_decayed + self.edges_pruned
            decay_fraction = (self.edges_decayed + self.edges_pruned) / max(total_before, 1)
            if decay_fraction > 0.25:
                result += " Graph connections reduced significantly — run consolidate() to rebuild."
            else:
                result += " Tip: run consolidate() to rebuild connections for recently active blocks."

        return result

    def to_dict(self) -> dict[str, int]:
        return {
            "archived": self.archived,
            "edges_pruned": self.edges_pruned,
            "edges_decayed": self.edges_decayed,
            "total_edges_after": self.total_edges_after,
            "reinforced": self.reinforced,
            "constitutional_reinforced": self.constitutional_reinforced,
        }
```

### Step 6: `curate.py` — new helper + updated orchestration

**New imports:**
```python
import math

from elfmem.db.queries import (
    delete_edges_bulk,     # NEW
    get_active_blocks,
    get_all_edges,         # NEW
    get_blocks_by_tag_pattern,
    get_config,
    get_tags_batch,        # NEW
    get_weighted_degree,
    prune_weak_edges,
    reinforce_blocks,
    set_config,
    update_block_status,
)
from elfmem.scoring import (
    ESTABLISHED_EDGE_THRESHOLD,   # NEW
    SELF_WEIGHTS,
    compute_lambda_edge,
    compute_recency,
    log_normalise_reinforcement,
)
```

**Updated `curate()` signature and body:**
```python
async def curate(
    conn: AsyncConnection,
    *,
    current_active_hours: float,
    prune_threshold: float = PRUNE_THRESHOLD,
    edge_prune_threshold: float = EDGE_PRUNE_THRESHOLD,
    reinforce_top_n: int = CURATE_REINFORCE_TOP_N,
) -> CurateResult:
    """Run maintenance on the memory corpus.

    Four phases:
    1. Archive blocks whose recency has dropped below prune_threshold
    2. Delete edges whose weight is below edge_prune_threshold (static, fast)
    3. Delete edges whose effective weight has decayed below threshold (temporal)
    4. Reinforce constitutional blocks and top-N by composite score

    Updates last_curate_at in system_config after completion.
    """
    archived = await _archive_decayed_blocks(conn, current_active_hours, prune_threshold)
    edges_pruned = await prune_weak_edges(conn, edge_prune_threshold)
    edges_decayed = await _prune_decayed_edges(conn, current_active_hours, edge_prune_threshold)
    total_edges_after = await count_edges(conn)   # denominator for agent observability
    constitutional_reinforced = await _reinforce_constitutional(conn, current_active_hours)
    reinforced = await _reinforce_top_blocks(conn, current_active_hours, reinforce_top_n)

    await set_config(conn, "last_curate_at", str(current_active_hours))

    return CurateResult(
        archived=archived,
        edges_pruned=edges_pruned,
        edges_decayed=edges_decayed,
        total_edges_after=total_edges_after,
        reinforced=reinforced,
        constitutional_reinforced=constitutional_reinforced,
    )
```

**New `_prune_decayed_edges()` helper:**
```python
async def _prune_decayed_edges(
    conn: AsyncConnection,
    current_active_hours: float,
    edge_prune_threshold: float,
) -> int:
    """Delete edges whose effective weight has decayed below the prune threshold.

    Effective weight = weight × exp(−λ_edge × hours_since_last_active).
    λ_edge inherits from the more stable endpoint block (via compute_lambda_edge).
    Established edges (reinforcement_count ≥ ESTABLISHED_EDGE_THRESHOLD) decay
    at half rate — long-proven relationships get LTD protection.

    Skips:
    - Edges with NULL last_active_hours (no session anchor; static prune handles them).
    - Agent-origin edges (deliberate conscious associations; agent revokes explicitly).
    """
    all_edges = await get_all_edges(conn)
    if not all_edges:
        return 0

    # Batch-fetch tags for all endpoint blocks in one query
    endpoint_ids = list({e["from_id"] for e in all_edges} | {e["to_id"] for e in all_edges})
    tags_map = await get_tags_batch(conn, endpoint_ids)

    # Build category map from active blocks (archived blocks already have cascade-deleted edges)
    active = await get_active_blocks(conn)
    category_map: dict[str, str] = {b["id"]: b["category"] for b in active}

    def _tier(block_id: str) -> DecayTier:
        tags = tags_map.get(block_id, [])
        category = category_map.get(block_id, "knowledge")
        return determine_decay_tier(tags, category)

    to_delete: list[tuple[str, str]] = []
    for edge in all_edges:
        # Guard 1: agent-origin edges are protected from temporal decay
        if edge.get("origin") == "agent":
            continue
        # Guard 2: no session anchor — cannot safely compute age
        if edge["last_active_hours"] is None:
            continue

        hours_since = max(0.0, current_active_hours - float(edge["last_active_hours"]))
        lam = compute_lambda_edge(
            _tier(edge["from_id"]),
            _tier(edge["to_id"]),
            reinforcement_count=int(edge["reinforcement_count"]),
        )
        effective_weight = float(edge["weight"]) * math.exp(-lam * hours_since)

        if effective_weight < edge_prune_threshold:
            to_delete.append((edge["from_id"], edge["to_id"]))

    return await delete_edges_bulk(conn, to_delete)
```

---

## 7. Test Plan

All tests live in `tests/test_curate.py`, new class `TestEdgeTemporalDecay`.
Each test uses the existing `system_setup` fixture and `_make_active_pair()` helper.
`insert_edge()` is called with explicit `last_active_hours` to control decay age.

### Test matrix

| Test | Setup | Assert |
|------|-------|--------|
| `test_stale_edge_pruned_by_decay` | weight=0.50, last_active_hours=0.0, current=500 | edges_decayed ≥ 1, edge gone |
| `test_recent_edge_survives_decay` | weight=0.50, last_active_hours=490.0, current=500 | edges_decayed == 0, edge present |
| `test_established_edge_decays_slower` | count=10, weight=0.50, last_active=0.0, moderate hours | edge survives where non-established would not |
| `test_agent_edge_not_decayed` | origin="agent", weight=0.50, last_active=0.0, current=500 | edge present after curate |
| `test_null_last_active_hours_not_decayed` | last_active_hours=None (NULL), weight=0.05 | not in edges_decayed (may be in edges_pruned) |

### Test 1: stale edge pruned by decay

```python
async def test_stale_edge_pruned_by_decay(self, system_setup) -> None:
    """Edge with large hours_since decays to effective_weight < threshold and is deleted."""
    # Standard blocks: λ_edge = 0.005. At hours_since=500, effective = 0.50 × e^-2.5 ≈ 0.041.
    # 0.041 < edge_prune_threshold=0.10 → PRUNED.
```

### Test 2: recent edge survives decay

```python
async def test_recent_edge_survives_decay(self, system_setup) -> None:
    """Edge last active 10 hours ago is not pruned by temporal decay."""
    # Standard blocks: λ_edge = 0.005. At hours_since=10, effective = 0.50 × e^-0.05 ≈ 0.475.
    # 0.475 > 0.10 → survives.
```

### Test 3: established edge decays slower

```python
async def test_established_edge_decays_slower(self, system_setup) -> None:
    """Edge with reinforcement_count=10 has λ halved; survives hours where count=0 would not."""
    # count=10: λ_edge = 0.0025. At hours_since=300, effective = 0.50 × e^-0.75 ≈ 0.236.
    # count=0 at same hours: λ_edge = 0.005, effective = 0.50 × e^-1.5 ≈ 0.111.
    # Both survive 0.10 threshold at 300hrs. Use hours=500: count=10 → 0.184 (survives),
    # count=0 → 0.041 (pruned). Two edges, verify only the non-established is pruned.
```

### Test 4: agent edge not decayed

```python
async def test_agent_edge_not_decayed(self, system_setup) -> None:
    """Agent-origin edge survives even with large hours_since (protected from LTD)."""
    # Insert with origin="agent", last_active_hours=0.0, current=500.
    # Must use insert_agent_edge() or insert_edge(origin="agent", last_active_hours=0.0).
    # After curate: edge still present, edges_decayed == 0.
```

### Test 5: NULL last_active_hours not decayed

```python
async def test_null_last_active_hours_skips_temporal_decay(self, system_setup) -> None:
    """Edge with NULL last_active_hours is excluded from temporal decay computation."""
    # Insert edge without last_active_hours (defaults to None/NULL), weight=0.50.
    # After curate at current=500: NOT in edges_decayed (static prune may apply if weight < 0.10,
    # but with weight=0.50, edge survives entirely).
```

---

## 8. Edge Cases and Mitigations

| Case | Mitigation |
|------|-----------|
| Agent on holiday (no sessions) | `last_active_hours` uses session-aware clock. Zero active hours accumulate during the absence. Edges do not decay. ✅ (simulation scenario 6) |
| `current_active_hours < last_active_hours` | `max(0.0, ...)` clamp. `hours_since` is never negative. Weight never amplified above original. ✅ |
| Very large edge set (>2500) | `get_tags_batch()` handles batch tag fetch. `delete_edges_bulk()` iterates pairs. At Phase 1 scale (degree cap=10, ≤500 blocks), ≤2500 edges maximum. Acceptable. ✅ |
| Both endpoints archived in same cycle | `_archive_decayed_blocks()` runs first and cascade-deletes edges. `_prune_decayed_edges()` finds no rows for those endpoints. ✅ |
| Endpoint block archived between cycles | FK CASCADE on `update_block_status()` (explicit delete) removes edges at archival. ✅ |
| NULL last_active_hours (pre-C2 or legacy callers) | Skip temporal decay entirely. Static pruning still applies. Gradual transition: edges get timestamps when first co-retrieved. ✅ (simulation scenario 10) |
| PERMANENT-PERMANENT edge (constitutional pair) | λ_edge ≈ 5×10⁻⁶. At 200,000 active hours, effective ≈ 0.368 × weight. Effectively immortal. ✅ |
| Established threshold hard cutoff (count=9 vs 10) | Hard threshold is intentional — 10 co-retrievals is a strong signal. Edge at count=9 still survives 400+ hours before threshold is crossed. Acceptable. (simulation scenario 12) |
| Agent edge with very low weight | Weight < static threshold (0.10) AND reinforcement_count=0 → `prune_weak_edges()` deletes it first. Temporal decay never sees it. ✅ |
| Outcome confirmation doesn't refresh anchor | Fixed: `upsert_outcome_edge()` UPDATE clause now includes `last_active_hours`. Critical correctness fix from simulation scenario 4. ✅ |
| Silent domain erosion — agent returns to stale graph | Mitigated by `CurateResult.summary` contextual tip and escalation message. Agent always gets actionable guidance when edges decay. (simulation scenario 3) |
| Catastrophic mass-decay (1000+ hours of zero co-retrieval) | `CurateResult.summary` escalates when >25% of graph decayed in one cycle: "run consolidate() to rebuild." Agent is not silent about catastrophic events. (simulation scenario 16) |
| Reconsolidation doesn't refresh edge timestamp | INSERT OR IGNORE is semantically correct — edge age represents last ACTIVE USE, not last similarity check. Documented in `insert_edge()` docstring. (simulation scenario 9) |
| Over-calling curate() | Idempotent under over-calling. Short elapsed times → near-zero decay. ✅ (simulation scenario 7) |

---

## 9. Calibration Reference

`EDGE_PRUNE_THRESHOLD = 0.10` (existing `EDGE_PRUNE_THRESHOLD` in `curate.py`)

### Survival horizon: standard edge (λ_edge = 0.005)

| hours_since | weight=0.40 | weight=0.60 | weight=0.80 |
|-------------|-------------|-------------|-------------|
| 100 | 0.242 ✅ | 0.364 ✅ | 0.485 ✅ |
| 200 | 0.147 ✅ | 0.220 ✅ | 0.293 ✅ |
| 300 | 0.089 ❌ | 0.134 ✅ | 0.178 ✅ |
| 400 | 0.054 ❌ | 0.081 ❌ | 0.108 ✅ |
| 500 | 0.033 ❌ | 0.049 ❌ | 0.066 ❌ |

### Established edge bonus (λ_edge = 0.0025)

| hours_since | weight=0.40 | weight=0.60 | weight=0.80 |
|-------------|-------------|-------------|-------------|
| 300 | 0.187 ✅ | 0.280 ✅ | 0.374 ✅ |
| 500 | 0.116 ✅ | 0.174 ✅ | 0.233 ✅ |
| 800 | 0.067 ❌ | 0.100 ✅ | 0.134 ✅ |
| 1000 | 0.049 ❌ | 0.074 ❌ | 0.098 ❌ |

**Takeaway:** Standard edges survive ~200 active hours; established edges ~700 active hours
before mid-weight edges start falling below threshold. Consistent with biological memory:
rarely-used associations fade; frequently-reinforced ones persist for months of active use.

---

## 10. Consistency Checks

- **Block decay model parity.** Both use `exp(−λ × hours_since)`. Both use session-aware
  active hours. Both use `determine_decay_tier()` for tier lookup. ✅
- **Agent first.** `CurateResult.edges_decayed` is observable via `to_dict()` and
  `summary`. The agent can act on the signal ("the graph is aging; consider re-connecting
  key concepts"). ✅
- **Fail fast.** No defensive `try/except`. Division by zero impossible (`λ > 0` always).
  `math.exp()` never raises for finite inputs. ✅
- **No new LLM calls.** Pure computation over existing DB data. ✅
- **Idempotent.** Running `curate()` twice at the same `current_active_hours` is safe —
  already-decayed edges are gone, so re-running is a no-op. ✅
- **Backward compatible.** All changes are additive: new parameters with defaults,
  new field with default=0, new helper function. Existing tests don't change behavior. ✅

---

## 11. Implementation Order

1. `scoring.py` — constant + `compute_lambda_edge()` extension (pure, testable immediately)
2. `types.py` — `CurateResult.edges_decayed` field
3. `queries.py` — `get_all_edges()`, `delete_edges_bulk()`, signature updates
4. `consolidate.py`, `outcome.py` — pass `last_active_hours` at call sites
5. `curate.py` — `_prune_decayed_edges()` + updated `curate()` call
6. `tests/test_curate.py` — `TestEdgeTemporalDecay` (5 tests)

Run `pytest` after each step. Steps 1–4 are individually green. Step 5 adds new
behavior. Step 6 verifies the new behavior.

---

## 12. Success Criteria

- [ ] `compute_lambda_edge(STANDARD, STANDARD, reinforcement_count=10)` returns half
      the value of `compute_lambda_edge(STANDARD, STANDARD)`.
- [ ] `insert_edge()` called with `last_active_hours=42.0` stores 42.0 in DB.
- [ ] `upsert_outcome_edge()` on an EXISTING edge updates `last_active_hours` to the
      passed value (critical fix from simulation scenario 4).
- [ ] Stale similarity edge (weight=0.50, last_active=0.0, current=500) is in
      `result.edges_decayed` after `curate()`.
- [ ] Fresh similarity edge (last_active=490.0, current=500) is NOT pruned.
- [ ] Agent-origin edge survives curate at any staleness level.
- [ ] `CurateResult.total_edges_after` is populated and non-negative after curate().
- [ ] `CurateResult.__str__` includes "N edges decayed (M remain)" when N > 0.
- [ ] `CurateResult.__str__` escalates when decay_fraction > 0.25.
- [ ] All 372 existing tests continue to pass.
- [ ] 5 new tests in `TestEdgeTemporalDecay` pass.

---

## 13. Agent Simulation Reference

Full scenario analysis: `docs/plans/simulation_graph_temporal_decay.md`

Key findings that changed the plan:
- **F2 (critical):** `upsert_outcome_edge()` must refresh `last_active_hours` on re-confirmation — without this, outcome-validated edges decay from creation time, ignoring signal strength.
- **F3 (important):** `total_edges_after` gives agents a denominator for health assessment.
- **F1/F6 (UX):** Contextual tip and escalation in `CurateResult.summary` close the observability gap for domain-shifting agents and catastrophic decay events.
- **Scenario 9 (validation):** INSERT OR IGNORE behavior for reconsolidation is semantically correct — documented, not fixed.
- **Scenario 6 (validation):** Session-aware clock confirmed to handle idle agents perfectly.
