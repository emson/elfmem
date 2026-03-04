# Title: Edges — Storage, Lifecycle, and Simplification

## Status: complete

## Questions

1. **Storage:** Where do edges live? Block files? Database? A separate graph file?
2. **Lifecycle:** How do edges degrade (become stale) or strengthen (become more relevant)?
3. **Simplification:** The current edge model has types, weights, and directionality.
   What can be removed without losing meaningful behaviour?

---

## Background

Edges are the graph structure of the AMGS. They represent relationships between
memory blocks and drive centrality scoring — well-connected blocks rank higher in
every frame. Edges are created at consolidation when two blocks have embedding
similarity above 0.60.

Current state from earlier explorations:
```yaml
edges:
  - from: M4
    to: M1
    type: relates_to
    weight: 0.71
```

Three things to examine: where these are stored, how they change over time,
and whether the model can be made simpler without meaningful loss.

---

## Question 1: Where Are Edges Stored?

### The constraint from exploration 010

Edges are operational data — they change constantly (new edges created at consolidation,
edges reinforced at recall, edges pruned at curate). Operational data belongs in the
database, not block files. This is already settled.

The question is: what is the database structure, and is there any value in
a supplementary representation?

### Option A: Database table only

```sql
CREATE TABLE edges (
  from_id TEXT NOT NULL,
  to_id   TEXT NOT NULL,
  weight  REAL NOT NULL,
  created_at TEXT NOT NULL,
  reinforcement_count INTEGER DEFAULT 0,
  hours_since_co_retrieval REAL DEFAULT 0,
  PRIMARY KEY (from_id, to_id)
);
```

Query for all edges of block X:
```sql
SELECT * FROM edges WHERE from_id = X OR to_id = X;
```

**Pros:** Operationally clean. Single source of truth. Easy to query, update, delete.
When a block is pruned, a single `DELETE FROM edges WHERE from_id = X OR to_id = X`
removes all its edges atomically.

**Cons:** Edges are invisible from block files. You can't pick up a block and know
what it connects to. Portability is reduced — exporting a block doesn't include
its relationships.

### Option B: Separate graph file

A single `graph.yaml` or `graph.json` containing all edges.

```yaml
# .amgs/graph.yaml
edges:
  - from: a3f9c2b1
    to:   b7e1a209
    weight: 0.71
  - from: a3f9c2b1
    to:   c2d4f810
    weight: 0.68
```

**Pros:** Portable. Human-readable. Can be version-controlled separately.
Useful for debugging ("show me the whole graph at this point in time").

**Cons:** At 50 blocks × 5 edges each = 250 edges in one file. At 200 blocks it's
unwieldy. Read/write contention: every consolidation and every curate() rewrites
the same file. Not scalable.

### Option C: Per-block edge file

Each block has a companion edge file: `{id}.edges.yaml`.

```yaml
# a3f9c2b1.edges.yaml
edges:
  - to: b7e1a209
    weight: 0.71
  - to: c2d4f810
    weight: 0.68
```

**Pros:** Portable with the block. Block + edges = full node representation.
**Cons:** Edges are bidirectional. If A connects to B, which file "owns" the edge?
Both? Then deleting block A requires updating B's edge file — reference integrity
problem. Not clean.

### Option D: Database primary + lightweight edge summary in block front matter

Block front matter includes only the edge count (immutable at consolidation —
initial edge count doesn't change unless new consolidation adds edges):

```yaml
---
id: a3f9c2b1d84593e1
created: 2026-03-04T10:30:00Z
source: api
category: knowledge/technical
tags: []
initial_edge_count: 2   # set at consolidation, never updated
---
```

Full edge details in database. The front matter field is a hint, not a source of truth.
**Verdict:** Adds noise to front matter without meaningful benefit. Not worth it.

### Decision: Database only, with canonical undirected storage

**Edges are stored in the database exclusively.** No edge data in block files.

The key design choice: **undirected, canonical order.**

For any edge between blocks A and B, always store:
```
from_id = min(A.id, B.id)   # lexicographic minimum
to_id   = max(A.id, B.id)   # lexicographic maximum
```

This ensures each edge appears exactly once regardless of query direction.
No duplicate rows. Queries work naturally:

```sql
-- All edges for block X
SELECT * FROM edges WHERE from_id = X OR to_id = X;

-- Edge between specific pair (order-independent)
SELECT * FROM edges WHERE from_id = min(A, B) AND to_id = max(A, B);
```

**Why undirected?**

The current primary edge type (`relates_to`) is symmetric: if "asyncio patterns"
relates to "list comprehensions," then "list comprehensions" relates to "asyncio patterns."
Storing direction adds complexity without adding meaning. If directed edge types
(elaborates, contradicts) are needed in future, directionality can be added then.

**Final schema:**

```sql
CREATE TABLE edges (
  from_id              TEXT NOT NULL,   -- min(A, B) lexicographic
  to_id                TEXT NOT NULL,   -- max(A, B) lexicographic
  weight               REAL NOT NULL,   -- current strength [0.0, 1.0]
  created_at           TEXT NOT NULL,   -- ISO timestamp
  reinforcement_count  INTEGER DEFAULT 0,
  hours_since_co_retrieval REAL DEFAULT 0,
  PRIMARY KEY (from_id, to_id),
  FOREIGN KEY (from_id) REFERENCES blocks(id) ON DELETE CASCADE,
  FOREIGN KEY (to_id)   REFERENCES blocks(id) ON DELETE CASCADE
);
```

`ON DELETE CASCADE` handles block pruning automatically — delete a block,
all its edges are removed. No manual cleanup needed.

---

## Question 2: Edge Lifecycle — Degradation and Promotion

### Edges are different from blocks

Block decay models *information* becoming outdated. But edges represent *relationships*
between blocks. Blocks are immutable — their content doesn't change. So the relationship
between two blocks is either relevant or not based on how the system uses them together.

The relevant decay mechanism for edges is **disuse** (from exploration 005):
an edge that is never activated (the two blocks never retrieved together) becomes
progressively less meaningful — not because the relationship is wrong, but because
it is not useful to this agent's actual thinking.

This distinguishes two types of edge:
- **Active edges:** A and B are frequently retrieved together. Their relationship is
  central to how the agent thinks. The edge should be strong.
- **Dormant edges:** A and B were similar at consolidation time but have never
  been co-retrieved. The relationship exists in embedding space but not in practice.
  These edges should fade.

### Edge reinforcement: co-retrieval

When recall() returns blocks [A, B, C, D, E], every pair of returned blocks
that has an edge between them is reinforced:

```
returned: [M4, M2, M6, M5, M7]
edges present: M4-M5, M4-M6, M8-M2 (M8 not returned)

M4-M5 co-retrieved → reinforce
M4-M6 co-retrieved → reinforce
M8-M2: M8 not in result → no reinforcement
```

Reinforcement updates:
```
edge.reinforcement_count += 1
edge.hours_since_co_retrieval = 0
edge.weight = min(1.0, edge.weight + 0.05)   # small boost, capped at 1.0
```

The weight boost (+0.05) is intentionally small. A single co-retrieval doesn't
dramatically strengthen an edge. Consistent co-retrieval over many sessions
creates a meaningfully stronger edge.

**Example progression:**

```
Initial weight (similarity): 0.71
After 1 co-retrieval:  0.76
After 3 co-retrievals: 0.86
After 5 co-retrievals: 0.96  (approaching 1.0 — extremely strong relationship)
After 10 co-retrievals: 1.0  (capped)
```

An edge at 0.96 between two blocks means: every time the agent thinks about one,
it very likely thinks about the other. That's genuine conceptual proximity, earned
through use.

### Edge decay: disuse at curate()

At curate() time, edges with no recent co-retrieval decay:

```
edge_decay_weight = e^(-λ_edge × hours_since_co_retrieval)
```

What is `λ_edge`? Edge decay should be slower than standard block decay (λ=0.01).
Relationships persist longer than individual facts. A reasonable starting point:

```
λ_edge = 0.005   # half the standard block decay rate
```

At λ=0.005:
- Edge survives 10 active hours at full strength
- At 200 active hours (5 weeks): decay_weight = e^(-1.0) = 0.37
- At 460 active hours (11.5 weeks): decay_weight = 0.10 → approaching prune threshold

**Edge prune threshold:** 0.10 (lower than block threshold of 0.05 — edges are
cheaper to recreate than blocks, so prune more aggressively).

If `edge_decay_weight < 0.10` at curate() → edge is pruned.

At the next consolidation, if the two blocks still have similar embeddings and
are above the 0.60 similarity threshold, the edge will simply be recreated.
An edge pruned by disuse can return if the blocks become relevant again.

### Edge promotion: from weak to strong

A newly created edge starts at its similarity weight (e.g., 0.71). It can grow
to 1.0 through co-retrieval. But there's a question: should an edge ever be
*structurally* promoted — from a normal edge to something more durable?

Two mechanisms for structural promotion:

**1. High reinforcement count threshold**

If an edge accumulates `reinforcement_count >= 10`, it becomes a "strong edge"
with a slower decay rate:

```
reinforcement_count < 10:  λ_edge = 0.005 (standard edge decay)
reinforcement_count >= 10: λ_edge = 0.001 (slow decay — relationship is established)
```

This means: an edge formed through genuine repeated use becomes resilient.
Even if A and B aren't retrieved together for a while, the established relationship
persists. This mirrors how human memory works — repeatedly rehearsed associations
are more durable.

**2. New block as transitive bridge**

When a new block C is consolidated with edges to both A and B, it acts as
a transitive bridge. If `edge(C-A) >= 0.60` and `edge(C-B) >= 0.60`:

```
edge(A-B).weight = max(edge(A-B).weight, edge(C-A) × edge(C-B))
```

For example:
- New block C: "asyncio event loop internals"
- edge(C, M4 "asyncio patterns") = 0.89
- edge(C, M6 "asyncio task cancellation") = 0.82
- Transitive bridge weight: 0.89 × 0.82 = 0.73
- edge(M4, M6) was 0.81 — not changed (already stronger)
- But if edge(M4, M6) was 0.55 (below creation threshold, no direct edge):
  - Transitive bridge creates it at 0.73
  - This is an edge that *should* have existed but didn't quite meet the similarity threshold

This is a useful mechanism for discovering indirect relationships.

### Lifecycle summary

```
Created at consolidation:
  weight = similarity (0.60 – 1.0)
  reinforcement_count = 0
  hours_since_co_retrieval = 0

Reinforced at recall() (co-retrieval):
  weight += 0.05 (capped at 1.0)
  reinforcement_count += 1
  hours_since_co_retrieval = 0

Decayed at curate():
  edge_decay_weight = e^(-λ_edge × hours_since_co_retrieval)
  if edge_decay_weight < 0.10: PRUNE

Promoted at curate() (high reinforcement):
  if reinforcement_count >= 10: λ_edge = 0.001 (established relationship)

Pruned automatically:
  ON DELETE CASCADE when either block is deleted
```

---

## Question 3: Simplification

### What the current model has

From earlier explorations, edges have:
1. **Type** (`relates_to`, potentially `contradicts`, `supports`, `elaborates`)
2. **Weight** (similarity score, updated by co-retrieval)
3. **Directionality** (from → to)
4. **Decay** (with reinforcement history)
5. **Degree cap** (implied — not yet formalised)

What can be removed or simplified?

### Simplification 1: Drop edge types (for now)

Current: `type: relates_to`

Edge types (contradicts, supports, elaborates) require either:
- The learner to manually specify relationships — unrealistic burden
- An LLM call per edge at consolidation time — expensive

In practice, at Phase 1 scale, all edges mean roughly the same thing: "these two
concepts are related." The weight captures *how* related. Type adds almost no
decision-relevant information that weight doesn't already carry.

**Remove `type` from the schema.** All edges are implicitly `relates_to`.
Add typed edges in Phase 2 if a genuine use case emerges.

```sql
-- Before
weight  REAL,
type    TEXT,   -- ← remove

-- After
weight  REAL    -- sufficient
```

### Simplification 2: Single weight field (not weight + decay_weight)

Currently thinking about edge storage as having `weight` (similarity) and
`edge_decay_weight` (computed from λ and hours). This is two values doing
overlapping jobs.

Simpler: **weight IS the current strength**, updated by both reinforcement
and decay. No separate decay_weight field.

```
At consolidation: weight = similarity_score (e.g., 0.71)
At recall() co-retrieval: weight = min(1.0, weight + 0.05)
At curate():
  new_weight = weight × e^(-λ_edge × hours_since_co_retrieval)
  if new_weight < 0.10: prune
  else: weight = new_weight
```

Weight starts as a similarity score and evolves into a usage-weighted relationship
strength. One number, full history.

### Simplification 3: Degree cap — max 10 edges per block

Without a cap, a hub block in a large corpus could accumulate hundreds of edges.
High-centrality blocks would dominate scoring to an extreme degree, and
edge maintenance at curate() would scale poorly.

**Hard cap: each block holds at most 10 edges.**

At consolidation, when a new edge would push a block over 10:
```
Find the weakest existing edge on that block.
If new_edge.weight > weakest_edge.weight:
    Remove weakest. Add new.
Else:
    Discard new edge — not strong enough to displace existing edges.
```

This is a natural form of edge competition. Each block's 10 edges are its
strongest, most relevant connections. Weaker connections don't make the cut.

**Why 10?**

At K=5 (blocks in a context frame) and max 10 edges per block, centrality
scores remain meaningful and varied across a 50-block corpus without any
block becoming overwhelmingly central. Tune upward as corpus grows.

### Simplification 4: No edge-specific decay lambda — derive from block properties

Rather than storing `λ_edge` per edge, derive it:

```
λ_edge = min(λ_from_block, λ_to_block) × 0.5
```

An edge inherits half the slowest decay rate of its two endpoints. Constitutional
blocks (λ=0.00001) produce near-permanent edges. Standard blocks (λ=0.01) produce
edges with λ=0.005. The edge is no more durable than its weakest block — but the
0.5 multiplier means edges outlast their blocks slightly, creating a grace period
where the relationship persists even as the blocks age.

No additional fields needed. Edge decay is derived at curate() time from current
block metadata.

**Exception:** Once an edge crosses the `reinforcement_count >= 10` threshold,
its effective λ is halved again (the "established relationship" bonus):

```
base_λ = min(λ_from, λ_to) × 0.5
established_bonus = 0.5 if reinforcement_count >= 10 else 1.0
λ_edge = base_λ × established_bonus
```

### The simplified schema

```sql
CREATE TABLE edges (
  from_id              TEXT NOT NULL,
  to_id                TEXT NOT NULL,
  weight               REAL NOT NULL,         -- current strength; starts at similarity
  created_at           TEXT NOT NULL,
  reinforcement_count  INTEGER DEFAULT 0,     -- co-retrieval count
  hours_since_co_retrieval REAL DEFAULT 0,    -- for decay at curate()
  PRIMARY KEY (from_id, to_id),
  FOREIGN KEY (from_id) REFERENCES blocks(id) ON DELETE CASCADE,
  FOREIGN KEY (to_id)   REFERENCES blocks(id) ON DELETE CASCADE
);
```

Six fields. No `type`. No `λ_edge` stored (derived). Weight serves double duty
as similarity + usage history. Clean.

---

## Worked Example: Edge Lifecycle at Scale

**Starting state after first consolidation (from exploration 008):**

```
Edges (weight = initial similarity):
  M4-M1: 0.71
  M5-M1: 0.68
  M5-M4: 0.74
  M6-M4: 0.81
  M7-M1: 0.63
  M8-M2: 0.73
```

**After recall() with query "how do I handle async operations":**

Returned blocks: [M4, M2, M6, M5, M7]

Co-retrieved pairs with existing edges:
```
M4-M5: edge exists (0.74) → reinforce → 0.74 + 0.05 = 0.79
M4-M6: edge exists (0.81) → reinforce → 0.81 + 0.05 = 0.86
M4-M7: no edge (M4-M7 never created, similarity was below 0.60)
M5-M6: no edge
M2-M5: no edge
M2-M6: no edge
```

```
M4-M1: M1 pruned — CASCADE deleted this edge already
M5-M1: M1 pruned — CASCADE deleted
M7-M1: M1 pruned — CASCADE deleted
```

Edges after recall():
```
M4-M5: 0.79  (reinforced)
M4-M6: 0.86  (reinforced)
M5-M4: same as M4-M5 (undirected — same row)
M8-M2: 0.73  (not co-retrieved — unchanged)
M7: isolated (no edges, M1 was its only connection)
```

**After curate() at t+300h active hours:**

Compute derived λ_edge for each edge:
```
M4 (λ=0.01) - M5 (λ=0.01): λ_edge = min(0.01, 0.01) × 0.5 = 0.005
M4 (λ=0.01) - M6 (λ=0.01): λ_edge = 0.005
M8 (λ=0.001) - M2 (λ=0.001): λ_edge = min(0.001, 0.001) × 0.5 = 0.0005
```

M4-M5 hours_since_co_retrieval = 0 (just reinforced). No decay.
M4-M6 hours_since_co_retrieval = 0. No decay.
M8-M2 hours_since_co_retrieval = 300h (never co-retrieved):

```
new_weight = 0.73 × e^(-0.0005 × 300)
           = 0.73 × e^(-0.15)
           = 0.73 × 0.861
           = 0.629
```

Still well above 0.10. M8-M2 survives.

Final edge state:
```
M4-M5: 0.79, reinforcement_count=1
M4-M6: 0.86, reinforcement_count=1
M8-M2: 0.629, reinforcement_count=0, hours=300
```

---

## Result: The Simplified Edge Model

```yaml
# What an edge is:
edge:
  from_id: a3f9c2b1           # canonical min(A, B)
  to_id:   b7e1a209           # canonical max(A, B)
  weight:  0.71               # current strength; starts at similarity
  created_at: 2026-03-04T10:30:00Z
  reinforcement_count: 0
  hours_since_co_retrieval: 0

# Creation: consolidation, if similarity >= 0.60 and degree cap not exceeded
# Reinforcement: co-retrieval in recall() → weight += 0.05, count++, hours = 0
# Decay: curate() → weight × e^(-λ_edge × hours_since_co_retrieval)
# Pruning: weight < 0.10 at curate(), OR CASCADE on block deletion
# Promotion: reinforcement_count >= 10 → λ_edge halved (established relationship)
# Degree cap: max 10 edges per block; weakest displaced by stronger new edge

# λ_edge (derived, not stored):
# = min(λ_from_block, λ_to_block) × 0.5
# × 0.5 again if reinforcement_count >= 10
```

---

## Insight

### Edges should mirror block behaviour, not duplicate it

Edges have their own decay (by disuse, not by time), their own reinforcement
(by co-retrieval, not individual use), and their own promotion (by sustained
co-retrieval). This mirrors the block lifecycle closely — same mechanisms,
different signals.

The key difference: **edges decay by co-retrieval disuse; blocks decay by
individual retrieval disuse.** A block can be retrieved frequently without
ever being retrieved *with a specific other block*. Those two are independent.

### CASCADE deletion is the right pruning strategy for edges

When a block is pruned, its edges should die with it. No orphaned edges.
No cleanup queries. The `ON DELETE CASCADE` constraint handles this atomically.
This keeps the graph consistent without requiring curate() to hunt for dangling edges.

### The degree cap prevents the hub problem

Without a cap, frequently-retrieved hub blocks accumulate edges indefinitely.
Centrality scores become dominated by a few over-connected nodes, and the
graph loses discriminating power. The degree cap of 10 keeps centrality
meaningful at the scale we're targeting (50 blocks initially).

### Dropping edge types simplifies without meaningful loss

At Phase 1 scale, all useful information about a relationship is in the weight.
A weight of 0.96 tells you more than `type: relates_to` ever could. Types
would be useful for contradiction detection and hierarchical reasoning —
genuine Phase 2 features.

---

## Design Decisions from this Exploration

| Decision | Rationale |
|----------|-----------|
| Edges stored in database only | Operational data; changes too frequently for files |
| Undirected with canonical ordering (min/max) | `relates_to` is symmetric; simpler storage and queries |
| ON DELETE CASCADE for block deletion | Automatic edge cleanup; no orphaned edges |
| No edge type field (Phase 1) | All edges are `relates_to`; type adds no decision-relevant information |
| weight = single evolving value | Combines similarity and usage history into one number |
| Co-retrieval reinforcement: weight += 0.05 | Small per-event boost; consistent use required for strong edges |
| Edge decay derived from block λ (not stored) | Edges inherit durability from their endpoint blocks |
| Prune threshold: weight < 0.10 at curate() | Lower than block threshold (0.05); edges cheaper to recreate |
| Degree cap: max 10 edges per block | Prevents hub dominance; keeps centrality discriminating |
| reinforcement_count >= 10 → λ_edge halved | Established relationships become resilient |

---

## Open Questions

- [ ] Should co-retrieval reinforcement weight the boost by rank proximity?
      (Rank 1 + Rank 2 co-retrieved together → bigger boost than Rank 1 + Rank 5?)
- [ ] What is the right degree cap? (10 for 50-block corpus — scales with corpus size?)
- [ ] Should there be a minimum edge weight below which an edge cannot be reinforced
      back to strength? (Prevent zombie edges: pruned + recreated repeatedly)
- [ ] Should transitive bridge edge creation be implemented? (Phase 2)
- [ ] Should edges between self-tagged blocks decay slower than non-self edges?

---

## Variations

- [ ] What happens to centrality if the degree cap is 5 instead of 10?
      Does one block become disproportionately central in a 50-block corpus?
- [ ] What if co-retrieval reinforcement is proportional to both blocks' scores
      in that recall() call? (Higher-ranked co-retrieval = bigger edge boost)
- [ ] Explore: can edge weight be used to predict which block to retrieve next
      given a starting block? (Graph-walk retrieval as an alternative to scoring)
