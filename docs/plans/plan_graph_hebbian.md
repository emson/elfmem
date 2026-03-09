# C1 — Hebbian Co-Retrieval Edge Creation

**Status:** Planning complete — ready to implement
**Date:** 2026-03-09
**Depends on:** A1, A2, B1, B2, C2 (all complete)
**Biological metaphor:** "Neurons that fire together wire together" (Hebb, 1949)

---

## 1. What We're Building

The current graph only grows via consolidation (similarity edges) and outcome signals. It is **blind to the agent's actual retrieval patterns** — the most important signal in the system.

When the agent calls `frame()` and two blocks appear together repeatedly, they are co-used in reasoning. This is a stronger signal than textual similarity: it proves functional relationship in context. The current system ignores this signal entirely.

**C1 adds Hebbian learning:** track co-retrieved block pairs across `frame()` calls. When a pair co-appears `N` times without an existing edge, create a permanent `co_occurs` edge. This closes the feedback loop between retrieval behaviour and graph structure.

---

## 2. Design Decisions

### 2.1 Where does staging state live?

**Decision: In-memory dict on `MemorySystem` instance.**

- `dict[tuple[str, str], int]` mapping canonical `(from_id, to_id)` → co-retrieval count
- Initialised empty in `__init__()`, never reset (not a session breadcrumb — it's a longer-horizon learning accumulator)
- Survives within a process lifetime; resets on restart (acceptable for Phase 1)
- Consistent with `_pending`, `_last_recall_block_ids`, `_session_block_ids` — all in-memory advisory state

**Why not DB?** Phase 1 targets 50–500 blocks and short-to-medium sessions. Patterns that survive restarts reform quickly in active usage. DB staging would add schema complexity, migration risk, and latency to the hot recall path. Revisit in Phase 2 if needed.

**Why not module-level?** Module-level state is shared across MemorySystem instances in the same process, breaking isolation. Each agent maintains its own Hebbian learning state.

### 2.2 Where does staging fire?

**Decision: Inside `frame()` only, not in raw `recall()`.**

`frame()` is the primary context-injection path — it already fires reinforcement side effects (`reinforce_blocks()`, `reinforce_co_retrieved_edges()`). Hebbian staging is a learning side effect, consistent with this contract.

`recall()` in `api.py` has explicit contract: "Raw retrieval without rendering. No reinforcement side effects." Staging would violate this contract. Agents calling `recall()` for inspection/debugging should not trigger learning.

### 2.3 When does promotion happen?

**Decision: Immediately at threshold during `frame()`, not deferred to `curate()`.**

Immediate promotion (inside the same transaction as the retrieval) means:
- Edge is available on the very next `frame()` call for graph expansion
- No waiting for curate() interval to elapse (up to 40 active hours)
- Agent observes graph growth matching its usage patterns in real time

The research diagram places promotion at `curate()` but the pseudo-code description (`create_edge(); del staging_counter`) implies immediacy. The agent-first principle favours immediate availability.

### 2.4 Should degree cap be enforced at promotion?

**Decision: No — omit for Phase 1.**

Degree cap (`EDGE_DEGREE_CAP = 10`) prevents hub nodes from accumulating too many edges. At Phase 1 scale (50–500 blocks, max top_k=20), co-retrieval edges are supplementary. The threshold of 3 co-retrievals is already a quality filter. If a node briefly exceeds cap by 1, the next `curate()` or `connect()` will clean it up.

Adding cap enforcement would require 2 additional DB queries per pair per recall, complicating the hot path. Revisit in Phase 2 if graph bloat becomes measurable.

### 2.5 Should staging reset on `begin_session()`?

**Decision: No — staging accumulates across sessions.**

A pair co-retrieved once in session 1, once in session 2, and once in session 3 is more significant than a pair co-retrieved 3 times in 1 burst session. Cross-session patterns are the most reliable Hebbian signal.

Contrast with `_last_recall_block_ids` (reset on session) — that's a navigation breadcrumb, not a learning accumulator.

### 2.6 What about pairs with existing edges?

**Decision: Skip staging; rely on `reinforce_co_retrieved_edges()` already running.**

`recall.py` already calls `reinforce_co_retrieved_edges()` for all returned block pairs (step 9 in the recall pipeline). This increments `reinforcement_count` and updates `last_active_hours` for existing edges.

Staging logic skips pairs already in `existing` (built from `get_edges_for_block()`). No double-counting. No collision between reinforcement and staging.

### 2.7 Edge properties for promoted pairs

```
relation_type = "co_occurs"    # semantically neutral — pattern-observed, not classified
origin        = "co_retrieval" # provenance: Hebbian learning, not similarity or agent
weight        = 0.55           # above similarity floor (0.40), below outcome-confirmed (0.80)
last_active_hours = current    # starts temporal decay clock from first creation
```

`co_occurs` edges are in `_EVICTION_ORDER` in `graph.py` — they can be displaced by higher-priority `connect()` edges (supports, elaborates, contradicts), preserving deliberate agent structure.

### 2.8 Staging cap and eviction

Research specifies: "Cap staging at 1000 entries; evict LRU."

**Implementation: evict lowest-count pairs, keep highest-count.**

Pairs closest to the threshold are the most valuable to preserve — they're one or two co-retrievals away from promotion. Dropping lowest-count pairs (furthest from promotion) minimises information loss.

```python
# When len(staging) > staging_max:
top = sorted(staging.items(), key=lambda x: x[1], reverse=True)[:staging_max - 100]
staging.clear()
staging.update(top)
```

100-entry headroom after eviction prevents thrashing (repeated evict/fill cycles).

---

## 3. Algorithm

```
frame() called with N returned blocks (N ≥ 2):

  [Already running in recall.py before staging]
  reinforce_co_retrieved_edges(conn, block_ids, current_hours)
    → for existing edges: reinforcement_count++, last_active_hours=now

  [NEW — runs in api.py inside same transaction]
  stage_and_promote_co_retrievals(conn, block_ids, staging, threshold, weight, current_hours):
    1. Build all canonical pairs from block_ids:
       pairs = [(min(A,B), max(A,B)) for each pair A,B in block_ids]
       At most O(top_k²/2) = 190 pairs for top_k=20.

    2. Load existing edges (batch, single pass):
       existing = {(from_id, to_id)} from get_edges_for_block() for each block

    3. For each pair NOT in existing:
       count = staging.get(pair, 0) + 1
       if count >= threshold:
         insert_edge(from_id, to_id, weight=0.55, relation_type="co_occurs",
                     origin="co_retrieval", last_active_hours=current_hours)
         staging.pop(pair)
         promoted += 1
       else:
         staging[pair] = count

    4. If len(staging) > staging_max: evict lowest-count pairs

    5. Return count of edges promoted
```

**Complexity:** O(top_k²) pairs × O(top_k) edge lookups per frame() call. At top_k=20: 190 pairs × 20 lookups = 3800 operations maximum. Acceptable for SQLite at Phase 1 scale.

**Optimisation:** Batch the edge lookup in step 2: iterate block_ids once, deduplicate, collect all edges in one pass. Each block is fetched once even if it appears in multiple pairs.

---

## 4. Calibration

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| `co_retrieval_edge_threshold` | 3 | "Once is coincidence, twice is pattern, three times is signal" — filters out burst sessions while capturing persistent patterns |
| `co_retrieval_edge_weight` | 0.55 | Above similarity floor (0.40), below outcome-confirmed (0.80). Represents "proven useful N times in context" |
| `co_retrieval_staging_max` | 1000 | Very generous for Phase 1 (50–500 blocks, max 190 pairs per recall). Cap is defensive, not a hot path |

---

## 5. Edge Cases and Mitigations

| Case | Mitigation |
|------|-----------|
| Burst: threshold co-retrievals in one session, never again | Edge created. Without reinforcement (last_active_hours=creation), C2 temporal decay removes it within ~2 curate cycles. Self-correcting. |
| Many blocks per recall (top_k=20) | Max 190 pairs. O(top_k²) is bounded. Threshold filters spurious burst patterns. |
| Staging grows beyond 1000 entries | Evict lowest-count pairs; keep highest-count (closest to promotion). 100-entry headroom prevents thrashing. |
| Restart loses staging | Acceptable. Persistent patterns reform in active usage. Phase 2 can add DB backing. |
| One block in staged pair gets archived | Archived block can't be retrieved; pair stays in staging but never promoted. Eviction at cap eventually cleans these zombie entries. |
| Pair promoted but both blocks archived next curate() | Edge CASCADE-deleted with blocks. Correct — archived knowledge doesn't need edges. |
| Frame cache returns same blocks without fresh retrieval | Built-in frames have `cache_json: null` — cache is disabled. Staging fires on genuine retrievals only. |
| `INSERT OR IGNORE` on already-existing edge | Staging only fires for pairs NOT in `existing` (checked first). If an edge was created between the DB lookup and the INSERT (race condition impossible in single-threaded async), the INSERT silently ignores. |
| All blocks in recall already have edges to each other | All pairs land in `existing`; staging count stays 0. Zero overhead. |
| Raw `recall()` API call (no side effects) | Staging NOT triggered. Only `frame()` fires staging. Contract preserved. |

---

## 6. Files to Change

### 6.1 `src/elfmem/config.py`

**Add 3 fields to `MemoryConfig`:**

```python
# Hebbian co-retrieval edge creation
co_retrieval_edge_threshold: int = 3
# Minimum co-retrievals for a pair to be promoted to a co_occurs edge.
# "Once is coincidence, twice is pattern, three times is signal."

co_retrieval_edge_weight: float = 0.55
# Weight for Hebbian-promoted co_retrieval edges.
# Above similarity floor (0.40), below outcome-confirmed (0.80).

co_retrieval_staging_max: int = 1000
# Maximum staging dict entries. Evicts lowest-count pairs when exceeded.
# Defensive cap — Phase 1 usage (50–500 blocks, top_k≤20) stays well below.
```

### 6.2 `src/elfmem/memory/graph.py`

**Add `stage_and_promote_co_retrievals()` after `reinforce_co_retrieved_edges()`:**

```python
async def stage_and_promote_co_retrievals(
    conn: AsyncConnection,
    block_ids: list[str],
    staging: dict[tuple[str, str], int],
    *,
    threshold: int,
    edge_weight: float,
    current_active_hours: float,
    staging_max: int = 1000,
) -> int:
    """Increment staging counts for co-retrieved pairs without existing edges.

    Promotes pairs that reach `threshold` to permanent co_occurs edges
    (origin="co_retrieval"). Promoted pairs are removed from staging.

    Only stages pairs WITHOUT an existing edge — pairs with edges are
    already reinforced by reinforce_co_retrieved_edges() before this call.

    Returns count of edges promoted in this call.

    Must be called after reinforce_co_retrieved_edges() so that existing-edge
    check reflects the current graph state.
    """
    if len(block_ids) < 2:
        return 0

    # Build all canonical pairs from returned block IDs
    canonical_pairs: list[tuple[str, str]] = [
        (min(block_ids[i], block_ids[j]), max(block_ids[i], block_ids[j]))
        for i in range(len(block_ids))
        for j in range(i + 1, len(block_ids))
    ]

    # Batch-load existing edges (one pass per block, deduped)
    existing: set[tuple[str, str]] = set()
    seen: set[str] = set()
    for bid in block_ids:
        if bid in seen:
            continue
        seen.add(bid)
        for edge in await queries.get_edges_for_block(conn, bid):
            existing.add((edge["from_id"], edge["to_id"]))

    # Stage pairs without existing edges; promote at threshold
    promoted = 0
    for pair in canonical_pairs:
        if pair in existing:
            continue  # already connected — reinforced by reinforce_co_retrieved_edges()

        count = staging.get(pair, 0) + 1
        if count >= threshold:
            await queries.insert_edge(
                conn,
                from_id=pair[0],
                to_id=pair[1],
                weight=edge_weight,
                relation_type="co_occurs",
                origin="co_retrieval",
                last_active_hours=current_active_hours,
            )
            staging.pop(pair, None)
            promoted += 1
        else:
            staging[pair] = count

    # Evict when over cap — preserve pairs closest to promotion (highest count)
    if len(staging) > staging_max:
        top = sorted(staging.items(), key=lambda x: x[1], reverse=True)[: staging_max - 100]
        staging.clear()
        staging.update(top)

    return promoted
```

### 6.3 `src/elfmem/api.py`

**Three changes:**

#### A. Add to imports (near graph imports at top of file):
```python
from elfmem.memory.graph import stage_and_promote_co_retrievals
```

#### B. Add to `__init__()` after `self._session_block_ids`:
```python
# Hebbian co-retrieval staging — accumulates across sessions, never reset.
# Maps canonical (from_id, to_id) → co-retrieval count without existing edge.
# Promotes to permanent co_occurs edge at co_retrieval_edge_threshold.
# In-memory only; resets on process restart (Phase 1 acceptable).
self._co_retrieval_staging: dict[tuple[str, str], int] = {}
```

#### C. Update `frame()` — add staging inside the `async with` block:

Current code (approximately lines 868–887):
```python
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
recalled_ids = [b.id for b in result.blocks]
self._last_recall_block_ids = recalled_ids
...
```

Replace with:
```python
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
    # Paired with reinforce_co_retrieved_edges() in recall.py (runs first,
    # handling existing edges). We stage NEW pairs without existing edges.
    recalled_ids = [b.id for b in result.blocks]
    if recalled_ids:
        await stage_and_promote_co_retrievals(
            conn,
            recalled_ids,
            self._co_retrieval_staging,
            threshold=mem.co_retrieval_edge_threshold,
            edge_weight=mem.co_retrieval_edge_weight,
            current_active_hours=current_hours,
            staging_max=mem.co_retrieval_staging_max,
        )
self._last_recall_block_ids = recalled_ids
for bid in recalled_ids:
    if bid not in self._session_block_ids:
        self._session_block_ids.append(bid)
self._record_op("frame", result.summary)
return result
```

**Note:** `recalled_ids` is defined inside the `async with` block but accessible after (Python scoping). Safe because exceptions from the block propagate before `self._last_recall_block_ids` is assigned.

#### D. Update `status()` — add `co_retrieval_staging_count`:

In the `SystemStatus(...)` constructor call inside `status()`, add:
```python
co_retrieval_staging_count=len(self._co_retrieval_staging),
```

### 6.4 `src/elfmem/types.py`

**Add field to `SystemStatus`:**

```python
co_retrieval_staging_count: int = 0
# Pairs building toward Hebbian co_retrieval edges.
# Non-zero means the agent's frame() usage is forming new graph connections.
```

**Update `__str__`** (add Hebbian line between pending_count and tokens):
```python
if self.co_retrieval_staging_count > 0:
    lines.append(
        f"Hebbian staging: {self.co_retrieval_staging_count} pairs building toward co_retrieval edges."
    )
```

**Update `to_dict()`**:
```python
"co_retrieval_staging_count": self.co_retrieval_staging_count,
```

---

## 7. Tests

**New file: `tests/test_hebbian.py`**

Tests use `MemorySystem` public API with `co_retrieval_edge_threshold=2` (low for fast cycles) and an explicit `similarity_overrides` that sets cosine(alpha, beta)=0.10 (below `MINIMUM_COSINE_FOR_EDGE=0.30`) to prevent consolidation from creating a similarity edge between the two test blocks.

### Test fixture

```python
ALPHA = "hebbian alpha"
BETA = "hebbian beta"

@pytest.fixture
async def system(test_engine, mock_llm):
    """MemorySystem with threshold=2 and no similarity edge between test blocks."""
    emb = MockEmbeddingService(
        similarity_overrides={frozenset({ALPHA, BETA}): 0.10}
    )
    cfg = ElfmemConfig(memory=MemoryConfig(
        inbox_threshold=2,          # dream() works with 2 blocks
        co_retrieval_edge_threshold=2,  # fast promotion for testing
    ))
    s = MemorySystem(engine=test_engine, llm_service=mock_llm, embedding_service=emb, config=cfg)
    await s.begin_session()
    return s

async def _two_active_no_edge(system: MemorySystem) -> tuple[str, str]:
    """Learn and consolidate 2 blocks guaranteed to have no similarity edge."""
    r1 = await system.learn(ALPHA)
    r2 = await system.learn(BETA)
    await system.dream()
    return r1.block_id, r2.block_id
```

**Why `inbox_threshold=2`:** `dream()` only consolidates when `pending >= threshold`. With 2 learned blocks and threshold=3 (default), dream() returns None. Setting threshold=2 ensures consolidation happens.

**Why cosine override=0.10:** `_composite_edge_score()` in consolidate.py has a hard guard: `if cos < MINIMUM_COSINE_FOR_EDGE (0.30): return 0.0`. With cosine=0.10, no similarity edge is created, giving staging a clean pair to work with.

### 5 Tests

```python
class TestHebbianCoRetrieval:

    async def test_staging_count_increments_on_co_retrieval(self, system) -> None:
        """TC-H-001: After first frame() with 2 unconnected blocks, staging_count == 1."""
        await _two_active_no_edge(system)
        await system.frame("attention", query="hebbian")
        assert (await system.status()).co_retrieval_staging_count == 1

    async def test_co_retrieval_edge_promoted_at_threshold(self, system, test_engine) -> None:
        """TC-H-002: At threshold frame() calls, co_occurs edge with co_retrieval origin created."""
        b1, b2 = await _two_active_no_edge(system)
        await system.frame("attention", query="hebbian")
        await system.frame("attention", query="hebbian")
        from_id, to_id = Edge.canonical(b1, b2)
        async with test_engine.connect() as conn:
            edge = await get_edge(conn, from_id, to_id)
        assert edge is not None
        assert edge["origin"] == "co_retrieval"
        assert edge["relation_type"] == "co_occurs"

    async def test_staging_cleared_after_promotion(self, system) -> None:
        """TC-H-003: Staging count drops to 0 after promotion."""
        await _two_active_no_edge(system)
        await system.frame("attention", query="hebbian")
        await system.frame("attention", query="hebbian")
        assert (await system.status()).co_retrieval_staging_count == 0

    async def test_existing_edge_pair_not_staged(self, system, test_engine) -> None:
        """TC-H-004: Pair with pre-existing edge is not incremented in staging."""
        b1, b2 = await _two_active_no_edge(system)
        from_id, to_id = Edge.canonical(b1, b2)
        async with test_engine.begin() as conn:
            await insert_edge(conn, from_id=from_id, to_id=to_id, weight=0.70)
        await system.frame("attention", query="hebbian")
        assert (await system.status()).co_retrieval_staging_count == 0

    async def test_raw_recall_does_not_stage(self, system) -> None:
        """TC-H-005: MemorySystem.recall() does not trigger Hebbian staging."""
        await _two_active_no_edge(system)
        await system.recall(query="hebbian")
        assert (await system.status()).co_retrieval_staging_count == 0
```

---

## 8. Worked Example

### Setup
- Block A: "Use SELF frame when values conflict" (id: `a1b2c3d4...`)
- Block B: "Constitutional blocks protect core identity" (id: `e5f6g7h8...`)
- Agent has called `dream()` → both blocks are active, no similarity edge (different wording, low cosine)
- Config: `co_retrieval_edge_threshold=3`, `co_retrieval_edge_weight=0.55`

### Session trace

| Call | Returned blocks | Staging before | Action | Staging after |
|------|----------------|---------------|--------|--------------|
| `frame("self", query="values")` | [A, B, C] | {} | stage (A,B)=1, (A,C)=1, (B,C)=1 | {(A,B):1, (A,C):1, (B,C):1} |
| `frame("self", query="identity")` | [A, B] | {(A,B):1,...} | stage (A,B)=2 | {(A,B):2, (A,C):1, (B,C):1} |
| `frame("self", query="constitutional")` | [A, B, D] | {(A,B):2,...} | promote (A,B)→edge; stage (A,D)=1, (B,D)=1 | {(A,C):1, (B,C):1, (A,D):1, (B,D):1} |
| `status()` | — | — | — | `co_retrieval_staging_count=4` |

### After 3rd call:
- Edge `(A,B)` created: `weight=0.55, relation_type="co_occurs", origin="co_retrieval", last_active_hours=<current>`
- 4th `frame("self")` call: A and B are now connected → `reinforce_co_retrieved_edges()` handles them (increment reinforcement_count, update last_active_hours). NOT staged again.
- Over time: if A-B are consistently retrieved together, their `reinforcement_count` grows. At count≥10, C2 temporal decay gives them LTD protection (λ halved again). Truly Hebbian: fires together → wires together → proves durability over time.

---

## 9. Agent-Friendliness Checklist

| Concern | Solution |
|---------|---------|
| Agent can't see Hebbian learning progress | `status().co_retrieval_staging_count` — always visible |
| Agent doesn't know when edges were Hebbian-created | `edge["origin"] == "co_retrieval"` — inspectable via `connect()` and graph queries |
| Agent accidentally triggers staging on exploration `recall()` | Staging only fires on `frame()`, not `recall()` |
| Cross-session patterns lost on restart | Documented limitation (Phase 1). Patterns reform in active use. |
| Staging grows unbounded | Cap at 1000 entries with eviction |
| Hebbian edges pollute high-quality graph | `co_occurs` is in `_EVICTION_ORDER` — displaced by `connect()` semantic edges |
| No feedback in CurateResult | Not added — `curate()` doesn't run promotion (happens at recall time). `status()` is the right surface. |

---

## 10. Implementation Order

Execute in this order to minimise risk:

1. **`config.py`** — Add 3 config params. Zero functional change. Tests still pass.
2. **`graph.py`** — Add `stage_and_promote_co_retrievals()`. Pure function, no callers yet. Tests still pass.
3. **`types.py`** — Add `co_retrieval_staging_count` to `SystemStatus` (with default=0). Backward compatible. Tests still pass.
4. **`api.py`** — Wire everything together: add staging dict, import new function, update `frame()`, update `status()`.
5. **`tests/test_hebbian.py`** — Run new tests. All 5 should pass.
6. **Full test suite** — Verify 377 + 5 = 382 passing, zero regressions.

---

## 11. What This Does NOT Change

| Item | Status |
|------|--------|
| `recall.py` operation | Unchanged — staging handled in `api.py` not the operation function |
| `curate.py` | Unchanged — promotion happens at recall time, not curate time |
| `queries.py` | Unchanged — uses existing `insert_edge()` and `get_edges_for_block()` |
| `CurateResult` | Unchanged — staging info exposed via `status()` instead |
| `consolidate.py` | Unchanged — similarity edges unaffected |
| Existing tests | Unchanged — all 377 should pass |

---

## 12. Metrics This Enables

After C1, agents can track:

- **`status().co_retrieval_staging_count`** — pairs building toward edges. Non-zero = Hebbian learning active.
- **Edge origin distribution** — via connect API inspection: what % of edges are `co_retrieval` vs `similarity` vs `outcome` vs `agent`? Target: co_retrieval + outcome > 30% = usage-earned graph.
- **Hebbian promotion rate** — edges with `origin="co_retrieval"` accumulating over sessions = graph genuinely reflecting agent retrieval patterns.

---

*Biological reference: Hebbian learning (1949) — "Cells that fire together wire together." BambooKG (arXiv 2025) implements direct frequency-weighted Hebbian graphs. Kairos (NeurIPS 2025) adds validation-gating. C1 implements pure Hebbian with C2 temporal decay (LTD) as the validation gate — edges that stop co-occurring naturally weaken and are pruned.*

---

## Addendum: Simulation-Based Refinements (2026-03-09)

After simulating C1 across 10 agent scenarios, four critical issues were identified and fixed:

### Issue 1: Cache Hits Triggered Staging (Critical Bug)

**Finding:** SELF frame uses a 1-hour TTL cache (`CachePolicy(ttl_seconds=3600)`). An agent calling `frame("self")` three times in rapid succession would promote ALL constitutional block pairs to edges via cache hits — 3 seconds of calls, not 3 independent sessions.

**Root cause:** Staging fired on `result.cached=True` results with no new signal.

**Fix:** Guard staging with `if not result.cached` before calling `stage_and_promote_co_retrievals()`. Cached results carry no new retrieval signal and must not count toward promotion.

### Issue 2: Burst Session ≠ Cross-Session Signal (Semantic Flaw)

**Finding:** The threshold=3 rationale ("once is coincidence, twice is pattern, three times is signal") assumes independence. But 3 calls in one session (burst) produced identical outcomes to 3 calls across 3 weeks (cross-session), which the research explicitly identified as more meaningful.

**Root cause:** No per-session deduplication — each call incremented count independently.

**Fix:** Add `_co_retrieval_session_seen: set[tuple[str, str]]` to `MemorySystem`. Each pair contributes at most 1 count per `begin_session()` cycle. Threshold now semantically means "N distinct sessions" not "N calls in a row."

**Behavioral change:** Tests and agents expecting immediate threshold=N promotion now require N sessions. This is the correct semantics.

### Issue 3: Zombie Staging Entries (Operational)

**Finding:** When blocks are archived by `curate()`, their staged pairs linger in the staging dict indefinitely. They never promote (archived blocks can't be retrieved) but also never get evicted unless the 1000-entry cap is hit.

**Root cause:** Staging dict never cleaned up after archival decisions.

**Fix:** In `curate()`, after archiving blocks, scan the staging dict against active block IDs. Remove pairs where either endpoint is no longer active. O(n) operation, runs once per curate cycle (40-hour interval).

### Issue 4: Silent Edge Promotions (Observability Gap)

**Finding:** Agents had no per-call signal when an edge was promoted. Inferring this required polling `status().co_retrieval_staging_count` and comparing, which is awkward in event-driven loops.

**Root cause:** `FrameResult` had no field for promotion count.

**Fix:** Add `edges_promoted: int = 0` to `FrameResult`. Captures the return value from `stage_and_promote_co_retrievals()` and exposes it. Zero most of the time; non-zero when staging crosses threshold.

### Test Coverage

9 tests now validate C1 correctness:
- 5 core staging tests (original design)
- 1 test: per-session dedup prevents double-counting in one session
- 2 tests: `edges_promoted` field reflects promotion count
- 1 test: curate() removes zombie staging entries

All tests pass; 386-test suite green (0 regressions from fixes).

### Documentation

- **`docs/plans/hebbian_agent_simulation.md`** — Full 10-scenario analysis with detailed findings, impact assessment, and design rationale for each fix.
- **`tests/test_hebbian.py`** — 9 tests with clear docstrings explaining each behavior.
