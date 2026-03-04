# Title: The Graph Layer — Structure, Algorithms, and Evolution

## Status: complete

## Question

Explorations 013 and 014 established what edges are and how they're stored.
Exploration 017 showed how to query them efficiently. But the graph aspects
haven't been reasoned through as a whole:

- What graph algorithms does the system actually need?
- What centrality measure is correct, and should it change at Phase 2?
- Should recall() traverse edges (multi-hop), or is top-K scoring enough?
- What graph structures emerge from learning — clusters, hubs, bridges?
- When does a graph library become useful, and which one?
- How does the graph shape change over time as blocks are added and used?

---

## What the Graph Is

The AMGS memory graph is an **undirected weighted graph**:

```
Nodes:  memory blocks (one node per active block)
Edges:  semantic similarity relationships (weight = similarity score, evolving)
        contradictions are NOT edges in this graph (separate table, separate logic)
```

The graph is not designed — it **emerges**. Consolidation creates edges when
embedding similarity exceeds 0.60. Recall() reinforces co-retrieved edges. curate()
prunes weak edges. No human or agent explicitly draws the graph; it grows from
learning patterns.

At 50 blocks with a degree cap of 10, the graph has at most 250 edges. In practice,
most blocks will have 3–7 edges. The graph is sparse and low-diameter.

---

## What Graph Operations the System Needs

### By lifecycle stage:

| Stage | Graph operation | What it does |
|-------|----------------|--------------|
| `consolidate()` | Edge creation | For each new block, find existing blocks with similarity ≥ 0.60; create edges; enforce degree cap |
| `consolidate()` | Degree cap enforcement | If new block has > 10 candidate edges, keep the 10 strongest; if an existing block is at cap, displace its weakest edge |
| `recall()` | Centrality lookup | Per candidate block: sum edge weights (weighted degree) |
| `recall()` | Contradiction check | For candidate block set: query `contradictions` table — this is NOT graph traversal |
| `recall()` | Co-retrieval reinforcement | For each pair of returned blocks: if an edge exists, increment `reinforcement_count`, reset `hours_since_co_retrieval` |
| `curate()` | Edge decay | Apply `weight × e^(-λ_edge × hours_since_co_retrieval)` to all edges |
| `curate()` | Edge pruning | Delete edges where `weight < 0.10` |
| `curate()` | Centrality update | (Phase 2+) Recompute PageRank; store as `centrality_cached` on blocks |

The key observation: **Phase 1 graph operations require no graph traversal**. Every
operation is expressible as a direct SQL query — joins, aggregations, updates — all
index-backed. There is no BFS, no path-finding, no graph walk in the Phase 1 runtime.

---

## Centrality: The Right Algorithm for Each Phase

Centrality answers: **how conceptually central is this block in memory?**

The centrality score feeds directly into the scoring formula (0.25 weight in SELF,
0.15 in ATTENTION). A block with higher centrality ranks higher in every frame.

### Phase 1: Weighted Degree

```
centrality(i) = Σ weight(i, j)   for all edges j adjacent to i
```

Computed at query time via SQL:
```sql
SELECT COALESCE(SUM(weight), 0.0) AS centrality
FROM edges
WHERE from_id = ? OR to_id = ?
```

**Why weighted degree is correct for Phase 1:**
- Simple and interpretable: more connections to more similar blocks = more central
- No iterative computation needed: one indexed query per block
- At 50 blocks: maximum centrality query count = 50, all fast

**What it misses:** A block connected to three highly-central blocks is treated the
same as a block connected to three peripheral blocks. Both have equal weighted degree
if the edge weights match. The *quality* of connections is ignored.

### Phase 2: PageRank

```
PR(i) = (1 - d) + d × Σ ( PR(j) / degree(j) )   for each neighbour j
        where d = 0.85 (damping factor)
```

PageRank is recursive: a block is central if central blocks link to it. This captures
connection quality, not just connection count.

**Why PageRank fits memory:**
- A "foundational concept" block linked to by many topic-specific blocks ranks high
- A fringe block linked only to other fringe blocks ranks low, even with many edges
- It reflects how knowledge is actually structured: some ideas are load-bearing

**The cost:** PageRank requires iterative computation (50–100 iterations to converge).
It cannot be computed at query time for each recall() call. It must be pre-computed
and stored.

**Phase 2 implementation:**
1. `curate()` computes PageRank across the full graph using networkx
2. Stores result as `centrality_cached REAL` on the `blocks` table
3. `recall()` reads `centrality_cached` directly — no edge queries for centrality
4. Between curate() passes, `centrality_cached` is slightly stale — acceptable

```python
# In curate() — Phase 2
import networkx as nx

def recompute_centrality(conn):
    G = build_memory_graph(conn)
    pr = nx.pagerank(G, weight="weight", alpha=0.85)
    max_pr = max(pr.values())  # normalise to 0.0–1.0
    for block_id, score in pr.items():
        conn.execute(
            update(blocks)
            .where(blocks.c.id == block_id)
            .values(centrality_cached=score / max_pr)
        )
```

**Phase 1 → Phase 2 migration:** Add `centrality_cached REAL` column (nullable).
`recall()` falls back to weighted-degree SQL query when `centrality_cached IS NULL`.
curate() populates it. No breaking change.

---

## Degree Cap Enforcement at Consolidation

When a new block B is consolidated:

```
1. Embed B → embedding_B
2. Compute similarity(B, all existing active blocks)
3. Filter: similarity > 0.60  →  candidate_edges = [(block_id, similarity), ...]
4. Sort candidate_edges by similarity descending
5. For B: cap at 10 candidates → B's edges = top 10 by similarity
6. For each candidate block X in B's edges:
   - Count existing edges of X
   - If degree(X) < 10: add edge (B, X, weight=similarity)
   - If degree(X) == 10: compare similarity(B, X) vs weakest existing edge of X
     - If similarity(B, X) > weakest: displace weakest, add new edge
     - If not: skip (B is not strong enough to displace)
```

This is all SQL:
```sql
-- Count degree of block X
SELECT COUNT(*) FROM edges WHERE from_id = 'X' OR to_id = 'X';

-- Find weakest edge of block X
SELECT from_id, to_id, weight FROM edges
WHERE from_id = 'X' OR to_id = 'X'
ORDER BY weight ASC LIMIT 1;
```

The displacement is an intentional design: the graph always contains the strongest
relationships. Weak connections are displaced by stronger ones as new knowledge
arrives. The graph is self-pruning at consolidation, independent of curate().

---

## Emergent Graph Structures

With semantic similarity as the edge criterion, specific structures emerge:

### Clusters

Blocks about the same topic form clusters — many strong internal edges, few
weak cross-topic edges. A cluster of Python asyncio blocks will have high mutual
similarity (0.75–0.90) and strong edges. A cluster of database concepts will
have high mutual similarity. Cross-cluster edges (Python asyncio ↔ database) will
be weaker (0.60–0.70 — they're related but less similar).

```
[asyncio patterns] ──0.85── [event loop]
        │                        │
       0.81                     0.74
        │                        │
[async/await] ──0.78── [await vs sync] ──0.63── [connection pools]
                                                       │
                                                      0.71
                                                       │
                                              [db transactions]
```

### Hubs

Blocks that discuss broad or foundational concepts become hubs — they connect
to many clusters because they're similar to blocks in multiple domains.

Example: a block about "separation of concerns as a design principle" is similar to
blocks in testing, database design, API design, and code structure. It bridges all
of them. Its weighted degree (and PageRank in Phase 2) is high.

**Hubs are high-value for retrieval.** Their high centrality means they appear in
recall() even for queries where their direct similarity is modest. They act as
"attractors" that broaden the context frame.

### Bridges

A single edge connecting two otherwise disconnected clusters. These are valuable
but vulnerable: if the bridge edge decays (not co-retrieved for a long time), the
two clusters become disconnected in the graph.

This is intentional. Unused bridges represent relationships that proved irrelevant
in practice. If a cross-topic insight was never actually useful in retrieval, the
bridge edge dissolving is correct.

### Isolates

Blocks with no edges. Either:
1. Truly unique — no other block has similarity > 0.60 (a lone concept)
2. Poorly expressed — the block's content didn't embed well relative to related blocks

Isolates have centrality = 0. They compete in recall() only on recency, confidence,
and reinforcement. If they're never retrieved (low reinforcement), they decay and
are eventually pruned.

This is a useful signal: persistent isolates after many sessions indicate either
a highly unique concept worth preserving, or a block that should be rewritten.

---

## The Co-Retrieval Reinforcement Loop

When two blocks A and B are both in the top-K returned by recall():

```python
# In recall() — after top-K selected
for block_a, block_b in combinations(returned_blocks, 2):
    if edge_exists(block_a.id, block_b.id):
        reinforce_edge(block_a.id, block_b.id)
        # weight += 0.05 (from exploration 013)
        # hours_since_co_retrieval = 0
        # reinforcement_count += 1
```

This creates a **positive feedback loop**:
- A and B are similar → strong edge created at consolidation
- A and B are frequently co-retrieved (same queries surface both) → edge reinforced
- Higher edge weight → higher centrality for both A and B
- Higher centrality → A and B rank higher → co-retrieved even more often
- reinforcement_count ≥ 10 → λ_edge halved → edge becomes highly durable

This is intentional. Concepts that are genuinely useful together become permanently
linked in memory. The graph encodes learned co-usage patterns, not just initial
semantic similarity.

**The risk:** A pair of blocks might be co-retrieved for the wrong reason (query
phrasing, not genuine relevance), reinforcing a spurious connection. At 50 blocks,
this is unlikely to cause problems. At Phase 2 scale, curate()'s decay for unused
edges provides a correction: if the spurious edge stops being co-retrieved, it decays
out of existence over time.

---

## Multi-Hop Retrieval: Deferred to Phase 2

**The Phase 1 question:** Should recall() follow edges to find blocks not in the
top-K but connected to blocks that are?

Example: block A scores 4th by composite score. Block C scores 6th. A and C have
a strong edge (weight 0.88). C is narrowly excluded from top-5. Multi-hop retrieval
would include C because of its connection to A.

**Why this is deferred:**
1. **The centrality component already captures graph signal.** A block with strong
   edges scores higher via its centrality component. The graph is already contributing
   to the scoring formula — C's exclusion happened because its overall score was lower,
   not because the graph was ignored.

2. **Multi-hop increases frame noise at Phase 1 scale.** With 50 blocks, including
   neighbours of top-K risks polluting the frame with related-but-not-query-relevant
   content. The frame is only 5 blocks — every slot matters.

3. **Phase 1 graphs are too small for multi-hop to add much.** At 50 blocks with
   max 10 edges per block, a 1-hop expansion from 5 seed nodes might reach 30 unique
   blocks — more than half the corpus. This isn't useful targeting, it's noise.

**The Phase 2 case for multi-hop:** At 5,000 blocks with rich cluster structure,
1-hop expansion from 5 seed nodes reaches perhaps 50 blocks — a meaningful subgraph.
Scoring those 50 by the full formula and selecting the best 10 is genuine graph-aided
retrieval. This is worth exploring at Phase 2 scale.

---

## Graph Library: networkx for Tooling, SQL for Runtime

Two distinct use cases for graph operations:

### Runtime (hot path — recall, consolidate, curate)

All graph operations in SQL. No library dependency. No graph object constructed in
memory. Index-backed queries that run in microseconds.

```python
# Runtime centrality — SQL only
def get_weighted_degree(conn, block_id: str) -> float:
    result = conn.execute(
        select(func.coalesce(func.sum(edges.c.weight), 0.0))
        .where(or_(edges.c.from_id == block_id, edges.c.to_id == block_id))
    ).scalar()
    return result
```

### Analysis / tooling (cold path — debug, visualise, curate PageRank)

`networkx` loaded on demand. Not in the dependency requirements for the core
library — an optional `amgs[analysis]` extra.

```python
# In amgs/analysis/graph.py  (optional module)
import networkx as nx

def build_memory_graph(conn) -> nx.Graph:
    """Load the full memory graph as a networkx Graph for analysis."""
    G = nx.Graph()

    block_rows = conn.execute(
        select(blocks.c.id, blocks.c.confidence,
               blocks.c.reinforcement_count, blocks.c.status)
        .where(blocks.c.status == "active")
    ).mappings().all()

    for row in block_rows:
        G.add_node(row["id"], **dict(row))

    edge_rows = conn.execute(select(edges)).mappings().all()
    for row in edge_rows:
        G.add_edge(row["from_id"], row["to_id"],
                   weight=row["weight"],
                   reinforcement_count=row["reinforcement_count"])
    return G


def graph_summary(G: nx.Graph) -> dict:
    return {
        "nodes":               G.number_of_nodes(),
        "edges":               G.number_of_edges(),
        "density":             nx.density(G),
        "connected_components": nx.number_connected_components(G),
        "isolates":            list(nx.isolates(G)),
        "top_hubs":            sorted(nx.degree(G, weight="weight"),
                                      key=lambda x: x[1], reverse=True)[:5],
        "avg_clustering":      nx.average_clustering(G, weight="weight"),
    }


def compute_pagerank(G: nx.Graph) -> dict[str, float]:
    pr = nx.pagerank(G, weight="weight", alpha=0.85)
    max_pr = max(pr.values())
    return {k: v / max_pr for k, v in pr.items()}  # normalised 0.0–1.0
```

**Why networkx and not igraph?**
- networkx is pure Python — no C compilation, easy install
- At 5,000 nodes / 25,000 edges, networkx handles PageRank in < 100ms
- igraph is 10–50× faster but only necessary above ~100,000 edges
- networkx has better documentation and Python-idiomatic API

At Phase 3 scale (50,000 blocks), igraph or a graph database becomes worth considering.
For Phase 1–2, networkx is correct.

---

## Worked Example: Graph Evolution Over Three curate() Passes

### Initial state (after consolidating 8 blocks)

```
Blocks: B1(asyncio basics), B2(async/await), B3(event loop),
        B4(connection pools), B5(db transactions), B6(testing patterns),
        B7(clean code), B8(naming conventions)

Edges after consolidation (similarity > 0.60):
  B1 ──0.88── B2  (asyncio ↔ async/await — very similar)
  B1 ──0.76── B3  (asyncio ↔ event loop — related)
  B2 ──0.81── B3  (async/await ↔ event loop — related)
  B4 ──0.73── B5  (connection pools ↔ transactions — database cluster)
  B6 ──0.64── B7  (testing ↔ clean code — methodology cluster)
  B7 ──0.71── B8  (clean code ↔ naming — style cluster)
  B1 ──0.62── B4  (asyncio ↔ connection pools — async db bridge)

Graph: two strong clusters (asyncio, database) + one weak cluster (methodology)
       one bridge: B1──B4 (asyncio to database)
       two isolates from methodology cluster (B6, B7, B8 form a chain, not a cluster)
```

### After Session 1: heavy Python asyncio use

Queries about async code surface B1, B2, B3 repeatedly. Zero queries about
database or testing.

```
Reinforcement effects:
  B1, B2, B3: reinforcement_count += 8 each (heavily used)
  B4, B5, B6, B7, B8: reinforcement_count += 1 (incidentally surfaced)

Edge reinforcement:
  B1──B2: reinforcement_count = 6 (co-retrieved most often)
  B1──B3, B2──B3: reinforcement_count = 4 each
  B4──B5: reinforcement_count = 0 (never co-retrieved)
  B1──B4: reinforcement_count = 0 (bridge never used)

After curate() pass 1:
  hours_since_co_retrieval for B4──B5, B1──B4: +40 active hours
  B4──B5 weight: 0.73 × e^(-0.007 × 40) ≈ 0.73 × 0.757 ≈ 0.553
  B1──B4 weight: 0.62 × e^(-0.007 × 40) ≈ 0.62 × 0.757 ≈ 0.469
  B1──B2 weight: reinforced 6×, minimal decay → still ~0.90
```

### After Session 2: database focus

Queries about database concurrency surface B4, B5, and — via the bridge B1──B4 —
also B1 (asyncio patterns, relevant to async db writes).

```
Reinforcement effects:
  B4, B5: reinforcement_count += 5 each
  B1──B4: co-retrieved! reinforcement_count = 1, weight += 0.05 → 0.519
  B4──B5: reinforcement_count = 4, weight += 0.20 → 0.753

After curate() pass 2:
  B1──B2 still strong (0.88+), barely decayed (used in session 1)
  B4──B5 recovered (0.753 — reinforced in session 2)
  B1──B4: bridge is now established; reinforcement_count = 1; decay halted
  B6──B7, B7──B8: methodology cluster has decayed (0.64→0.48, 0.71→0.54)
                   not yet below prune threshold (0.10) but weakening
```

### After Session 3: first testing query

One query about testing surfaces B6. B7 scores well (centrality from B6 edge).
B8 appears third.

```
Reinforcement effects:
  B6, B7: co-retrieved → B6──B7 reinforcement_count = 1, weight += 0.05 → 0.53
  B8: retrieved but B7──B8 edge too weak to show co-retrieval boost

curate() pass 3 summary:
  Asyncio cluster (B1─B2─B3): strong, stable, mutual λ_edge halved (reinforcement_count≥10)
  Database cluster (B4─B5): stable, reinforced
  Bridge B1──B4: alive, slowly strengthening
  Methodology chain (B6─B7─B8): B6──B7 slightly recovering; B7──B8 approaching prune threshold
```

**Observation:** The graph has learned from usage. The asyncio cluster has become
a tight, durable subgraph. The database cluster recovered when used. The bridge
B1──B4 is becoming established through co-retrieval. The methodology chain is
fragmenting — B7──B8 may soon be pruned if B8 remains unretrieved.

This is the intended behaviour: **the graph reflects what's been useful, not just
what's similar.**

---

## Graph Health Metrics (for curate() and tooling)

Computed at curate() time using the `build_memory_graph()` function. Stored in
`system_config` for inspection:

```python
summary = graph_summary(G)
# Useful signals:
# - isolates: blocks with no edges → candidate for rewriting or pruning
# - connected_components > 1: graph is fragmented → may indicate learning gaps
# - density: edges / max_edges → should stay in 0.05–0.30 range
#   too dense (> 0.30): everything is related — similarity threshold too low
#   too sparse (< 0.05): graph has no useful structure — consider lowering threshold
# - top_hubs: the 5 most central blocks — these are the load-bearing concepts
```

At Phase 1 (50 blocks), these metrics are informational. At Phase 2, they could
drive adaptive behaviour (e.g., automatically suggest lowering the similarity
threshold if density is too sparse).

---

## Design Decisions from this Exploration

| Decision | Rationale |
|----------|-----------|
| Phase 1 centrality = weighted degree, computed at query time | No iterative algorithm needed; one indexed SQL query per block |
| Phase 2 centrality = PageRank, materialised by curate() | Captures connection quality, not just count; too expensive for query-time computation |
| `centrality_cached` nullable — falls back to weighted degree query if NULL | Smooth Phase 1→2 migration; no breaking change |
| Phase 1 graph traversal = none (all graph ops are SQL) | Graph is too small for traversal to add value; SQL is sufficient |
| Multi-hop retrieval deferred to Phase 2 | Centrality already captures graph signal; too noisy at 50-block scale |
| networkx for analysis and curate() PageRank; SQL for runtime | No library in the hot path; analysis tools available on demand |
| networkx preferred over igraph for Phase 1–2 | Pure Python install; adequate performance; better documentation |
| Co-retrieval reinforcement creates positive feedback loop (intentional) | Frequently co-used concepts become permanently linked — reflects learned co-usage |
| Bridge edges dissolving when unused is correct | Unused cross-topic connections represent relationships that proved irrelevant in practice |
| Isolates signal block quality issues | A persistent isolate either is genuinely unique or was poorly expressed |
| Graph health metrics computed at curate() | Density, component count, isolates inform system health without adding runtime cost |

---

## Open Questions

- [ ] Should networkx graph construction happen at every curate() pass, or only
      when PageRank is implemented (Phase 2)? (Cheap at Phase 1 scale; useful for
      health metrics even before PageRank)
- [ ] What's the right damping factor for PageRank in a memory graph? (Standard
      0.85 is a starting point; a higher damping may suit memory graphs where
      hub-and-spoke structure is expected)
- [ ] Should isolated blocks (no edges after N curate() passes) trigger a
      learn() suggestion: "this block has no connections — consider rewriting it
      or adding related blocks"?
- [ ] At Phase 2, should multi-hop expansion be additive (top-K + neighbours)
      or substitutive (neighbours replace low-scoring top-K members)?

---

## Variations

- [ ] Simulate the graph at 500 blocks — what does the degree distribution look
      like? How many connected components? What is the average path length?
      (Likely: scale-free-ish distribution, 1–2 large components, short paths)
- [ ] Compare weighted degree vs PageRank centrality for 20 blocks — are the
      rankings meaningfully different? When do they diverge?
- [ ] What happens to graph structure when the similarity threshold is lowered
      from 0.60 to 0.50? How does density change, and does retrieval quality improve?
