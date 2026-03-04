# Playground: Graph — Edges, Centrality, and Expansion

## Status: Draft

## Subsystem Specification

The graph layer (explorations 013, 014, 020) tracks semantic relationships
between memory blocks. Edges are created at consolidation, strengthened by
co-retrieval, and pruned by curate().

### Edge Model

```python
Edge:
  from_id: str    # canonical: min(A,B)
  to_id:   str    # canonical: max(A,B)
  weight:  float  # starts at similarity score; evolves with use
```

- **Undirected**: canonical order prevents duplicates
- **λ_edge** derived: `min(λ_from, λ_to) × 0.5` — inherits durability from blocks
- **Co-retrieval reinforcement**: `weight += 0.1` when both ends recalled together
- **Degree cap**: max 10 edges per block — prevents hub dominance
- **Prune threshold**: `weight < 0.10` at curate()

### Centrality (Phase 1)

```
degree(block) = sum of all edge weights connected to this block
centrality(block) = degree(block) / max_degree_in_corpus
```

Normalised weighted degree, computed per-query from `edges` table (no materialisation).

### Contradiction Edges

Stored in a separate `contradictions` table — **not** in `edges`.
Different lifecycle: no decay, no reinforcement, explicit resolution.
Contradiction edges are **excluded** from centrality computation.

---

## Parameters

```yaml
similarity_edge_threshold: 0.60   # minimum similarity to create edge at consolidation
edge_degree_cap: 10               # max edges per block
edge_prune_threshold: 0.10        # weight below this pruned at curate()
edge_reinforce_delta: 0.10        # co-retrieval adds this weight
```

---

## Test Suite

### TC-G-001: Edge Created at Consolidation for Similar Blocks

**Purpose:** Two blocks with similarity ≥ `similarity_edge_threshold` get an edge
at consolidation time. Initial edge weight = similarity score.

**Given:**
```yaml
B1: "async patterns in Python"
B2: "await keyword and coroutines in Python"
cosine_similarity(B1, B2) = 0.78  # ≥ 0.60 threshold
```

**When:** `consolidate()` processes B1 and B2

**Then:**
- Edge created: `(from=min(B1,B2), to=max(B1,B2), weight=0.78)`
- `B1.edges` contains B2
- `B2.edges` contains B1

**Expected:** Edge exists with weight=0.78
**Tolerance:** ±0.001
**Status:** NOT YET RUN

---

### TC-G-002: No Edge Created Below Threshold

**Purpose:** Block pairs with similarity < 0.60 do not get edges at consolidation.

**Given:**
```yaml
B1: "async Python"
B2: "SQL database indexing strategies"
cosine_similarity(B1, B2) = 0.22  # < 0.60 threshold
```

**When:** `consolidate()` processes B1 and B2

**Then:**
- No edge created between B1 and B2
- Neither block's edge list includes the other

**Expected:** No edge exists between B1 and B2
**Status:** NOT YET RUN

---

### TC-G-003: Degree Cap Enforced — 11th Edge Candidate Rejected

**Purpose:** A block already at `edge_degree_cap=10` does not gain additional
edges at consolidation, even if new similar blocks arrive.

**Given:**
```yaml
B_hub:
  current_edge_count: 10   # at cap
  # edges to: B1, B2, B3, B4, B5, B6, B7, B8, B9, B10

B_new:
  similarity_to_B_hub: 0.72   # would normally create an edge
```

**When:** `consolidate()` processes B_new alongside B_hub

**Then:**
- No new edge created between B_hub and B_new (B_hub is at cap)
- B_hub's edge count stays at 10
- B_new still gets consolidated normally (may get edges with other blocks)

**Expected:** B_hub's degree stays at 10; no edge to B_new
**Status:** NOT YET RUN

---

### TC-G-004: Co-Retrieved Blocks Get Edge Reinforcement

**Purpose:** When two blocks are both returned in the same `frame()` call,
their shared edge weight increases by `edge_reinforce_delta=0.10`.

**Given:**
```yaml
edge B1—B2: weight=0.65
B1 and B2 both returned in frame("attention", query="async tasks")
```

**When:** `frame()` returns both B1 and B2 in the same call

**Then:**
```
new_weight = 0.65 + 0.10 = 0.75
```

**Expected:** Edge weight = 0.75 after co-retrieval
**Tolerance:** ±0.001
**Status:** NOT YET RUN

---

### TC-G-005: Edge Not Reinforced When Only One Block Returned

**Purpose:** Edge reinforcement requires both endpoints to be in the same
retrieval result. Single-block returns do not reinforce shared edges.

**Given:**
```yaml
edge B1—B2: weight=0.65
# frame() returns B1 but not B2 (B2 scored too low for top-K)
```

**When:** `frame()` returns B1 but not B2

**Then:** Edge weight unchanged (still 0.65)

**Expected:** Weight = 0.65 (unchanged)
**Status:** NOT YET RUN

---

### TC-G-006: Edge Pruned at curate() When Weight Falls Below Threshold

**Purpose:** Edges with `weight < edge_prune_threshold=0.10` are deleted at
curate(), preventing weak relationships from cluttering graph expansion.

**Given:**
```yaml
edge B1—B2: weight=0.08   # below 0.10 threshold
edge B3—B4: weight=0.25   # above threshold — keep
```

**When:** `curate()` runs

**Then:**
- Edge B1—B2: **DELETED**
- Edge B3—B4: **RETAINED** (weight 0.25 ≥ 0.10)

**Expected:** B1—B2 deleted; B3—B4 unchanged
**Status:** NOT YET RUN

---

### TC-G-007: Centrality Computed Correctly from Edge Weights

**Purpose:** Centrality = normalised weighted degree. A hub block with many
strong edges scores higher than a peripheral block with few weak edges.

**Given:**
```yaml
blocks: [B_hub, B_mid, B_peripheral]

edges:
  B_hub — B1: weight=0.90
  B_hub — B2: weight=0.85
  B_hub — B3: weight=0.80
  B_hub — B4: weight=0.70   # degree(B_hub) = 3.25

  B_mid — B5: weight=0.60
  B_mid — B6: weight=0.55   # degree(B_mid) = 1.15

  B_peripheral — B7: weight=0.62  # degree(B_peripheral) = 0.62

max_degree = 3.25 (B_hub)
```

**When:** Centrality computed for all blocks

**Then:**
```
centrality(B_hub)        = 3.25 / 3.25 = 1.000
centrality(B_mid)        = 1.15 / 3.25 = 0.354
centrality(B_peripheral) = 0.62 / 3.25 = 0.191
```

**Expected:** Centralités as computed; B_hub scores 1.0
**Tolerance:** ±0.001
**Status:** NOT YET RUN

---

### TC-G-008: Contradiction Stored in contradictions Table, Not edges

**Purpose:** A detected contradiction creates a record in `contradictions`,
not in `edges`. Contradiction edges are excluded from centrality computation.

**Given:**
```yaml
B_old: "Use synchronous database calls in Django views."
B_new: "Never use synchronous database calls in Django — always use async."
# LLM detects contradiction score = 0.92 ≥ contradiction_threshold=0.80
```

**When:** `consolidate()` detects contradiction between B_old and B_new

**Then:**
- Record created in `contradictions` table: `(B_old_id, B_new_id, score=0.92, resolved=0)`
- NO record created in `edges` table between B_old and B_new
- Neither block's centrality computation includes this relationship

**Expected:** Contradiction in `contradictions` table; not in `edges`
**Status:** NOT YET RUN

---

### TC-G-009: Archived Block's Edges CASCADE Deleted

**Purpose:** When a block is archived, all its edges are automatically deleted
via ON DELETE CASCADE. No orphaned edges remain.

**Given:**
```yaml
B_archived:
  status: active
  edges: [B1, B2, B3]  # 3 edges
```

**When:** B_archived is archived (e.g., decayed at curate())

**Then:**
- B_archived status → archived
- Edge B_archived—B1: **DELETED** (CASCADE)
- Edge B_archived—B2: **DELETED** (CASCADE)
- Edge B_archived—B3: **DELETED** (CASCADE)
- B1, B2, B3: their degrees decrease by the weight of the deleted edges

**Expected:** All 3 edges deleted; no orphaned records
**Status:** NOT YET RUN

---

### TC-G-010: λ_edge Derived from Endpoint Blocks

**Purpose:** Edge decay rate (`λ_edge`) is computed from the endpoint blocks'
decay tiers, not stored in the edge record.

**Given:**
```yaml
B1: decay_tier=durable    # λ=0.001
B2: decay_tier=standard   # λ=0.010

# λ_edge = min(0.001, 0.010) × 0.5 = 0.001 × 0.5 = 0.0005
```

**When:** λ_edge computed for edge B1—B2

**Then:**
```
λ_edge = 0.0005
half_life_edge = ln(2) / 0.0005 ≈ 1386 active hours
```

The edge inherits durability from the more stable block (durable wins over standard).
This means edges connecting long-lived identity blocks are themselves durable.

**Expected:** λ_edge = 0.0005
**Tolerance:** ±0.00001
**Status:** NOT YET RUN

---

## Parameter Tuning

### PT-1: similarity_edge_threshold (currently 0.60)

**Question:** Is 0.60 the right threshold for creating edges at consolidation?

**Tradeoff:**
- Too low (0.50): Noisy graph; weakly-related blocks connected; centrality becomes meaningless
- Too high (0.70): Sparse graph; graph expansion rarely recovers useful blocks

**Scenario:** Sample 100 block pairs in a real corpus. Measure:
- At 0.60: How many "genuinely related" pairs get edges vs. "spuriously connected" pairs?
- At 0.65: Does the graph lose important connections?

**Recommendation:** 0.60 as default. If expansion is recovering too many
irrelevant blocks, raise to 0.65.

---

### PT-2: edge_degree_cap (currently 10)

**Question:** Does a cap of 10 prevent hub dominance without losing useful connections?

**Scenario:** In a 50-block corpus, the most connected block (a foundational concept
like "async programming") might naturally have 15–20 neighbours. Capping at 10 forces
only the 10 highest-weight edges to survive.

**Key invariant:** The 10 retained edges should be the highest-weight ones (enforced
at consolidation time — lower-weight candidate rejected when cap is reached).

**Concern:** A new, highly-similar block added late might have high similarity to
a hub but get rejected because the hub is at cap. The oldest/lowest-weight edge
should be replaced if the new edge would be stronger.

**Open question:** Should adding a new strong edge to a capped block displace the
weakest existing edge?

---

## Open Assertions

1. Edges are stored exactly once per pair (canonical ordering prevents duplicates)
2. `ON DELETE CASCADE` is enforced — no orphaned edges ever exist in the table
3. Edge weight is always in `(0.0, 1.0]` — never negative, never zero after creation
4. λ_edge is always less than both endpoint block λ values
5. Centrality is always in `[0.0, 1.0]` (normalised)
6. A block with no edges has centrality = 0.0
7. Contradiction records have no weight field — they are binary (active/resolved)
8. Co-retrieval reinforcement is bounded (weight ≤ 1.0 — capped if necessary)

---

## Python Test Sketch

```python
# elfmem/tests/test_graph.py

import pytest
from elfmem import MemorySystem
from tests.fixtures import MockLLMService, MockEmbeddingService

async def test_edge_created_above_threshold(system):
    b1 = await system.learn("async patterns in Python")
    b2 = await system.learn("await and coroutines in Python")
    await system.consolidate()
    # MockEmbeddingService gives deterministic embeddings;
    # configure similar text to produce high similarity
    assert system.edge_exists(b1, b2)

async def test_no_edge_below_threshold(system):
    b1 = await system.learn("async Python")
    b2 = await system.learn("SQL database indexes")
    await system.consolidate()
    assert not system.edge_exists(b1, b2)

async def test_degree_cap_enforced(system):
    hub_id = await system.learn("Python programming fundamentals")
    await system.consolidate()
    # Add 12 similar blocks — hub should cap at 10 edges
    for i in range(12):
        await system.learn(f"Python tip {i}: related concept")
    await system.consolidate()
    assert system.edge_count(hub_id) <= 10

async def test_co_retrieval_reinforces_edge(system):
    b1 = await system.learn("celery task queues")
    b2 = await system.learn("redis message broker for celery")
    await system.consolidate()
    weight_before = system.get_edge_weight(b1, b2)
    await system.frame("attention", query="background tasks celery")
    weight_after = system.get_edge_weight(b1, b2)
    assert abs(weight_after - (weight_before + 0.10)) < 0.001

async def test_cascade_delete_on_archive(system):
    b1 = await system.learn("stale knowledge")
    b2 = await system.learn("related concept")
    await system.consolidate()
    assert system.edge_exists(b1, b2)
    system.archive_block(b1, reason="decayed")
    assert not system.edge_exists(b1, b2)

async def test_centrality_normalised(system):
    # After consolidation, max centrality should be 1.0
    # (at least one block has the highest degree)
    centralities = system.get_all_centralities()
    assert max(centralities.values()) == pytest.approx(1.0, abs=0.001)
    assert all(0.0 <= c <= 1.0 for c in centralities.values())

async def test_contradiction_not_in_edges_table(system):
    b1 = await system.learn("use synchronous DB calls")
    b2 = await system.learn("never use synchronous DB calls — always async")
    await system.consolidate()  # MockLLM detects contradiction
    assert not system.edge_exists(b1, b2)
    assert system.contradiction_exists(b1, b2)
```
