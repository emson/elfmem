# Graph Edge Improvement Research

**Status:** Research complete; C1–C2 implementation COMPLETE with refinements ✅
**Date:** 2026-03-09
**Implementation:** See `docs/plans/plan_graph_hebbian.md` (C1 plan + addendum) and `docs/plans/hebbian_agent_simulation.md` (simulation analysis + 4 critical fixes)
**Purpose:** Evaluate current edge limitations, survey external systems, and recommend prioritised improvements to elfmem's knowledge graph edge creation and retrieval.

---

## 1. The Problem

Elfmem's current edges answer one question: **"Are these two blocks textually similar?"**

The question the graph *should* answer is: **"Should recalling block A cause me to also consider block B?"**

These are fundamentally different. Two blocks can be highly similar but unrelated in use. Two blocks can be dissimilar but deeply connected in practice. The gap between geometric similarity and retrieval utility is where edge quality lives.

### Current Edge Mechanisms

| Mechanism | Trigger | Weight Formula | LLM? |
|-----------|---------|----------------|------|
| Similarity edges | `consolidate()` after block promotion | `cosine_similarity` (min 0.60) | No |
| Outcome edges | `outcome()` with signal > threshold | `signal × 0.5` | No |
| Co-retrieval reinforcement | `recall()` after returning blocks | `reinforcement_count++` only | No |
| Pruning | `curate()` every 40 active hours | weight < 0.10 AND count == 0 | No |

### Core Weaknesses

1. **Single signal.** Edges created exclusively from cosine similarity. Textual closeness ≠ relationship utility.
2. **Frozen weights.** Edge weight set at creation and never updated. Outcome signals don't change edge strength — only `reinforcement_count` increments.
3. **Outcome edges underweighted.** Edges confirmed by actual agent feedback start at `signal × 0.5` — structurally weaker than geometric edges (0.60–1.0). This is backwards.
4. **No new edges from co-retrieval.** `reinforce_co_retrieved_edges()` only reinforces existing edges, never creates new ones. The strongest Hebbian signal (used together repeatedly) creates nothing.
5. **No edge types.** Every edge is undifferentiated. Retrieval cannot distinguish a supporting relationship from an opposing one.
6. **No temporal decay.** Stale edges from months-old usage persist at full weight, polluting graph expansion.
7. **`edge_reinforce_delta` is dead code.** Defined in config (0.10), referenced nowhere.

---

## 2. External System Survey

### 2.1 Kairos — Validation-Gated Hebbian Learning (NeurIPS 2025)
*Most philosophically aligned with elfmem's biological metaphor.*

**Key innovations:**
- **Long-Term Potentiation (LTP) analog:** Asymptotic strengthening — `w_new = w + α × (w_max − w)`. Diminishing returns prevent any single edge dominating.
- **Long-Term Depression (LTD) analog:** Unused edges decay over time.
- **Validation-gating:** Consolidation only occurs when reasoning passes multi-dimensional quality assessment (logical consistency, grounding, novelty, alignment). This prevents reinforcing incorrect associations — analogous to dopaminergic reward signals gating synaptic plasticity.
- **Emergent connections:** New edges form between previously unconnected nodes that are repeatedly co-activated in reasoning chains.

**Elfmem mapping:** Validation-gating maps directly to elfmem's `outcome()` → `dream()` cycle. Outcome signal = dopamine. Edge strengthening only when outcome confirms the retrieval was useful.

### 2.2 BambooKG — Frequency-Weighted Neurobiological Graph (arXiv 2025)
*Simplest approach; captures usage patterns without LLM calls.*

**Key innovations:**
- Edge weight = f(activation frequency). Each co-access event increments the weight.
- Grounded in Spike-Timing Dependent Plasticity (STDP): the timing of activation determines whether synapses strengthen or weaken.
- Retrieval works entirely WITHOUT LLMs or embeddings after initial construction.
- Frequency distribution naturally encodes recency without explicit decay.

**Elfmem mapping:** Direct implementation of Hebbian co-retrieval edge creation. "Blocks retrieved together N times get a permanent edge" is the core rule.

### 2.3 Zep / Graphiti — Temporal Knowledge Graph (arXiv 2025)
*Most sophisticated edge versioning; prevents stale knowledge.*

**Key innovations:**
- Every edge is a `(subject, predicate, object)` triplet.
- **Bitemporal timestamps:** `t_valid`/`t_invalid` (real-world truth window) + `t_created`/`t_expired` (ingestion window).
- Contradictions trigger temporal invalidation: old edge's `t_invalid` set to new edge's `t_valid`. Old facts aren't deleted — they're versioned.
- LLM compares new edges against semantically similar existing ones for conflict detection.

**Elfmem mapping:** Temporal invalidation approach is overkill for Phase 1, but the bitemporal concept suggests adding `last_active_hours` to edges to enable decay.

### 2.4 A-Mem — Agent-Driven Zettelkasten (NeurIPS 2025)
*The agent itself participates in edge creation.*

**Key innovations:**
- Each memory is a structured "note" with contextual description, keywords, and tags (Zettelkasten method).
- **Memory Linking:** Analyzes historical memories to find relevant connections, establishing links where meaningful similarities exist.
- **Memory Evolution:** New memories trigger updates to contextual representations of existing memories.
- The agent drives memory organisation rather than a fixed automated pipeline.

**Elfmem mapping:** The `connect()` API concept (see Section 4.1). The agent — as the consumer of the knowledge — has the highest-quality signal about which blocks are meaningfully related.

### 2.5 Mem0 — LLM Entity-Relationship Pipeline
*Most production-proven; highest edge quality but highest cost.*

**Key innovations:**
- Two-pass LLM pipeline: entity extraction → relationship generation → conflict detection.
- Graph + vector search run in parallel (ThreadPoolExecutor).
- 26% higher accuracy vs OpenAI memory; 90% lower token usage.
- BM25 reranking on top of embedding + graph results.

**Elfmem mapping:** The two-pass LLM approach is too expensive per-edge for elfmem's design. But the **batch classification** variant (one LLM call for all edges from a single dream cycle) is feasible and maps to the breathing rhythm.

### 2.6 GraphRAG — Community Hierarchies (Microsoft Research)
*Best for holistic/global queries; wrong fit for elfmem's single-agent model.*

**Key innovations:**
- Leiden algorithm creates hierarchical community structure.
- Pre-computed community summaries enable global query answering.
- Local search: entity neighbourhood traversal. Global search: community summary aggregation.

**Elfmem verdict:** GraphRAG is designed for document corpora (millions of chunks), not single-agent memory (50–500 blocks). Community summaries require full reindexing on change. Not appropriate for Phase 1 or 2. Defer indefinitely.

### 2.7 LightRAG — Dual-Level Retrieval (EMNLP 2025)
*Good retrieval pattern; edge description storage is the key idea.*

**Key innovations:**
- Edges have their own LLM-generated textual profiles describing the relationship.
- Dual-level retrieval: low-level (specific nodes/edges) vs high-level (aggregated topics via keyword matching).

**Elfmem mapping:** Edge `note` field in `connect()` API — agent can store a human-readable description of why this connection exists. Cheap to implement; high interpretability value.

### 2.8 RAPTOR — Recursive Tree Retrieval (arXiv 2024)
*Hierarchical summarisation; different paradigm from elfmem.*

**Verdict:** RAPTOR builds a literal tree of summaries. Elfmem's flat graph with frame-based retrieval serves the same purpose more flexibly. Not applicable.

---

## 3. Biological Principles That Apply

| Principle | Biological Basis | Elfmem Application |
|-----------|-----------------|-------------------|
| **Hebbian learning** | "Neurons that fire together wire together" | Co-retrieval creates edges |
| **Validation-gated plasticity** | Dopamine gates synaptic change | Outcome signal gates edge strengthening |
| **Long-Term Potentiation** | Asymptotic strengthening with diminishing returns | `w_new = w + α × (w_max − w)` |
| **Long-Term Depression** | Unused synapses weaken | Edge temporal decay at curate() |
| **Temporal contiguity** | Items experienced close in time associate | Same-session blocks get edge bonus |
| **Conscious association** | Deliberate rehearsal forms strong connections | Agent `connect()` API |
| **Consolidation** | Fast hippocampal encoding, slow neocortical integration | Heartbeat/breathing/sleep already implemented |

---

## 4. Recommended Improvements

### 4.1 `connect()` — Agent-Suggested Edges  ⭐ Highest Priority

**The core insight:** The agent IS the consumer of the knowledge. It knows relationships no algorithm can infer. When it recalls two blocks and decides they are related, that's the highest-quality signal in the system.

**API:**
```python
result = await system.connect(
    source="block_id_a",
    target="block_id_b",
    relation="supports",          # supports | contradicts | elaborates | co_occurs | outcome | similar | <custom>
    weight=0.8,                   # optional: default derived from relation type
    note="Both describe frame selection heuristics"  # optional: human-readable reason
)
```

**Properties:**
- Heartbeat speed — pure DB insert, zero LLM calls.
- Idempotent — connecting same pair twice updates relation/weight, not error.
- Typed result — `ConnectResult` with `__str__`, `summary`, `to_dict()`.
- `.recovery` on all error cases.
- Degree cap respected — weakest edge displaced if block at cap.
- Self-loops rejected gracefully.
- Unknown relation types accepted as custom; scored as `SIMILAR`.

**Biological analog:** Conscious association — the brain doesn't only wire passively through repetition. Deliberate rehearsal and explicit connection-making are how expertise forms.

**Edge cases:**

| Case | Mitigation |
|------|-----------|
| Bad edge suggested | Decay kills unused edges. Self-correcting. |
| Existing similarity edge | Upgrade: add relation type, optionally boost weight. |
| Self-loop | Reject. Return `ConnectError` with `.recovery`. |
| Invalid block ID | `ConnectError` with `.recovery = "system.recall(...) to find valid IDs"`. |
| Agent spams edges | Degree cap enforced. Weakest edge displaced if at cap. |
| Unknown relation type | Accept as custom. Scored as `SIMILAR` in retrieval. |
| Conflicting relation type | If edge exists with different type: update to new type, log conflict. |

---

### 4.2 Fix Outcome Edge Weighting + Wire `edge_reinforce_delta`  ⭐ Critical Fix

**Problem:** Outcome edges start at `signal × 0.5`, making edges confirmed by actual agent feedback weaker than geometric similarity edges (0.60–1.0). This is structurally backwards.

**Fix 1 — Weight scale:**
```python
# Before:
OUTCOME_EDGE_WEIGHT_SCALE = 0.5

# After:
OUTCOME_EDGE_WEIGHT_SCALE = 0.8   # outcome signal is the strongest signal
```

**Fix 2 — Wire `edge_reinforce_delta`:**
```python
# outcome.py — after signal > threshold:
for edge connecting outcome blocks:
    delta = signal * config.edge_reinforce_delta   # 0.10 by default
    edge.weight = min(edge.weight + delta, 1.0)
    # UPDATE edges SET weight = new_weight WHERE ...
```

This implements Kairos's Long-Term Potentiation — edges that participate in validated reasoning get stronger. The config parameter was defined for exactly this purpose and is currently unused.

**Impact:** Closes the feedback loop. Outcome-confirmed edges become the strongest in the graph, earning retrieval priority through proven utility.

---

### 4.3 Add `relation_type` + `origin` + `last_active_hours` to Edge Schema

**Schema change:**
```sql
ALTER TABLE edges ADD COLUMN relation_type TEXT NOT NULL DEFAULT 'similar';
ALTER TABLE edges ADD COLUMN origin TEXT NOT NULL DEFAULT 'similarity';
ALTER TABLE edges ADD COLUMN last_active_hours REAL;
ALTER TABLE edges ADD COLUMN note TEXT;
```

| Column | Values | Purpose |
|--------|--------|---------|
| `relation_type` | `similar`, `supports`, `contradicts`, `elaborates`, `co_occurs`, `outcome`, `<custom>` | Semantic label for retrieval filtering |
| `origin` | `similarity`, `outcome`, `co_retrieval`, `agent`, `llm_inferred` | Provenance — how was this edge created? |
| `last_active_hours` | Float (session-aware hours) | Enables temporal decay at curate() |
| `note` | Optional text | Agent or LLM description of why this connection exists |

**Retrieval impact by relation type:**

| Type | Expansion Behaviour |
|------|---------------------|
| `supports` | Prioritised in TASK frame expansion |
| `contradicts` | Surface as counterpoint; suppress lower-confidence block |
| `elaborates` | Prioritised in ATTENTION/WORLD frame expansion |
| `co_occurs` | Standard expansion weight |
| `outcome` | Highest expansion weight — proven useful |
| `similar` | Standard expansion weight (current behaviour) |
| `<custom>` | Scored as `similar` |

**Note:** This schema change is the foundation for improvements 4.4, 4.6, and 4.7.

---

### 4.4 Hebbian Co-Retrieval Edge Creation

**Problem:** `reinforce_co_retrieved_edges()` only reinforces existing edges. The most important Hebbian signal — "these blocks were retrieved together repeatedly" — creates no new edges.

**Proposal:** Track co-retrieval pairs in a lightweight staging counter. Promote to edge after threshold.

```python
CO_RETRIEVAL_EDGE_THRESHOLD = 3   # "once is coincidence, twice is pattern, three times is signal"

# On recall(), for each pair WITHOUT existing edge:
staging_counter[(block_a, block_b)] += 1
if staging_counter[(a, b)] >= CO_RETRIEVAL_EDGE_THRESHOLD:
    create_edge(a, b, relation="co_occurs", origin="co_retrieval", weight=0.55)
    del staging_counter[(a, b)]
```

**Storage options:**
- **In-memory dict** (simplest): staging resets on restart. Only persistent patterns across a session matter. Sufficient for Phase 1.
- **Lightweight DB table** (durable): survives restart. Use if agent has long-running sessions with meaningful pattern formation between restarts.

Recommended: start with in-memory. Add DB persistence in Phase 2 if needed.

**Edge cases:**

| Case | Mitigation |
|------|-----------|
| Burst: 3 co-retrievals in one session, never again | Edge created but decays without further co-retrieval. Self-correcting. |
| Many blocks in one recall result | O(n²) pairs, but top_k is bounded (≤20). At most 190 pairs to check. Acceptable. |
| Staging grows large | Bounded by top_k × session queries. Cap staging at 1000 entries; evict LRU. |
| Restart loses staging | Acceptable in Phase 1. Persistent patterns reform quickly. |

---

### 4.5 Composite Edge Score at Consolidation

**Problem:** Pure cosine similarity misses meaningful signals already present in block metadata.

**Proposal:** Replace single-signal edge score with multi-signal composite:

```python
def edge_score(
    block_a: Block,
    block_b: Block,
    vec_a: list[float],
    vec_b: list[float],
) -> float:
    sim = cosine_similarity(vec_a, vec_b)
    tag_overlap = jaccard_similarity(block_a.tags, block_b.tags)
    frame_match = 1.0 if block_a.frame == block_b.frame else 0.3
    temporal = temporal_proximity(block_a.created_hours, block_b.created_hours)

    return (
        sim * 0.55
        + tag_overlap * 0.20
        + frame_match * 0.15
        + temporal * 0.10
    )
```

Where `temporal_proximity` decays with session-hour distance:
```python
def temporal_proximity(h_a: float, h_b: float, sigma: float = 8.0) -> float:
    return exp(-(h_a - h_b) ** 2 / (2 * sigma ** 2))
```

**Signals used:**

| Signal | What It Captures | Source |
|--------|-----------------|--------|
| Cosine similarity | Textual closeness | Embeddings (already computed) |
| Tag Jaccard | Categorical kinship | `blocks.tags` (already stored) |
| Frame match | Contextual domain | `blocks.frame` (already stored) |
| Temporal proximity | Same-session learning | `blocks.created_hours` (already stored) |

**Zero additional LLM calls.** All signals exist in the DB.

**Benefit:** Two blocks tagged `frame-selection` in the ATTENTION frame, even if worded differently, will form stronger edges. Two blocks learned in the same troubleshooting session get a temporal boost.

---

### 4.6 Edge Temporal Decay at Curate()

**Already specified in exploration 013; not implemented.**

**Schema prerequisite:** `last_active_hours` column (4.3).

**Update on reinforcement:**
```python
# In reinforce_edges() and reinforce_co_retrieved_edges():
UPDATE edges SET
    reinforcement_count = reinforcement_count + 1,
    last_active_hours = :current_active_hours
WHERE (from_id, to_id) IN (...)
```

**Decay at curate():**
```python
λ_edge = min(λ_block_a, λ_block_b) * 0.5
if edge.reinforcement_count >= 10:
    λ_edge *= 0.5   # established relationship bonus

hours_since = current_active_hours - edge.last_active_hours
effective_weight = edge.weight * exp(-λ_edge * hours_since)

if effective_weight < edge_prune_threshold:
    DELETE edge
```

**Biological analog:** Long-Term Depression — unused synaptic connections weaken. Knowledge that no longer co-occurs in agent reasoning fades from the graph.

**Edge cases:**

| Case | Mitigation |
|------|-----------|
| Agent on "holiday" (no sessions) | `last_active_hours` uses session-aware clock (not wall clock). Holidays don't kill edges. Consistent with block decay model. |
| High-reinforcement edges decay too fast | Established relationship bonus (reinforcement_count ≥ 10 → λ halved). |
| Newly created edges decay before first use | Edges have 0 active hours delta at creation. No decay until first curate() cycle after creation. |

---

### 4.7 LLM Batch Edge Classification at dream()

**When:** During `dream()`, after similarity edges are created for a batch of promoted blocks.

**Approach:** One LLM call per dream cycle classifies all new edges from that batch:

```
Prompt (one call, batched):
"Classify the relationship between each pair:

Pair 1:
  A: "Use SELF frame when values are in conflict"
  B: "Constitutional blocks define identity constraints"
  → supports / contradicts / elaborates / unrelated

Pair 2:
  A: "High cosine similarity blocks are near-duplicates"
  B: "Deduplicate before promoting to active memory"
  → supports / contradicts / elaborates / unrelated

..."
```

**Cost:** 1 LLM call per dream cycle, regardless of how many edges were created. At top_k=10 edges per block and N newly promoted blocks, this classifies up to `N × 10` edges in one call.

**Output:** Updates `relation_type` on created edges. Unrelated pairs flagged for deletion or downgraded weight.

**Benefit:** Automatic semantic typing without per-edge cost. Maps perfectly to the "breathing" rhythm — LLM involvement is appropriate at consolidation.

**Edge cases:**

| Case | Mitigation |
|------|-----------|
| LLM misclassifies | Classification is soft guidance, not hard truth. Decay and co-retrieval will correct over time. |
| No new edges in a dream cycle | Skip classification call entirely. |
| LLM returns unknown category | Fall back to `similar`. Log for review. |
| Prompt too long (many pairs) | Batch in sub-groups of 20 pairs. Run as sequential calls within dream(). |

---

## 5. The Complete Edge Architecture

Mapping all improvements to elfmem's three biological rhythms:

### Heartbeat (milliseconds, zero LLM)

| Operation | Edge Behaviour |
|-----------|---------------|
| `learn()` | No edges. Fast ingestion only. |
| **`connect()`** *(NEW)* | Agent-suggested edge. Pure DB insert. Typed relation, optional note. |

### Breathing (seconds, LLM-powered)

| Operation | Edge Behaviour |
|-----------|---------------|
| `dream()` / `consolidate()` | Similarity edges created (composite score: sim + tags + frame + temporal) |
| | *(NEW)* LLM batch classification of new edges → updates `relation_type` |
| `outcome()` | Outcome edges created/strengthened (weight = `signal × 0.8`) |
| | *(FIX)* Wire `edge_reinforce_delta` → actually update edge weights |
| `recall()` | Co-retrieval reinforcement (updates `reinforcement_count` + `last_active_hours`) |
| | *(NEW)* Increment staging counter for pairs without existing edges |

### Sleep (minutes, maintenance)

| Operation | Edge Behaviour |
|-----------|---------------|
| `curate()` | *(NEW)* Apply temporal decay to effective weight |
| | *(ENHANCED)* Prune edges where decayed weight < threshold |
| | *(NEW)* Promote staging pairs at threshold → create `co_occurs` edges |
| | *(NEW)* Graph health metrics in curate result (edge count, type distribution, isolated nodes) |

---

## 6. Implementation Priority Order

These are ordered by impact-to-effort ratio and dependency. Each is a discrete, independently plannable deliverable.

### Phase A — Foundation (implement together, low risk)

**A1: Fix outcome edge weighting**
- Change `OUTCOME_EDGE_WEIGHT_SCALE` from 0.5 to 0.8
- Wire `edge_reinforce_delta` to actually update edge weights in `outcome.py`
- Remove dead config note
- Files: `outcome.py`, `queries.py`, `config.py`
- Tests: update weight assertions in `test_lifecycle.py`

**A2: Schema extension**
- Add `relation_type`, `origin`, `last_active_hours`, `note` to edges table
- Migration: add columns with defaults (backward compatible)
- Update `insert_edge()`, `upsert_outcome_edge()`, `get_edges_for_block()` to include new fields
- Files: `models.py`, `queries.py`, `db/migrations.py` (or schema init)
- Tests: update schema assertions in `test_storage.py`

### Phase B — New Capabilities (implement after Phase A)

**B1: `connect()` public API**
- New `ConnectResult` type in `types.py`
- New `connect()` method on `MemorySystem`
- New MCP tool `elfmem_connect`
- Files: `types.py`, `api.py`, `mcp/server.py`
- Tests: new `test_connect.py`
- Guide entry: add to `GUIDES` dict

**B2: Composite edge score at consolidation**
- Replace `cosine_similarity` with multi-signal `edge_score()` in `consolidate.py`
- Add `temporal_proximity()` pure function
- Add `jaccard_similarity()` pure function (tags are lists)
- Files: `consolidate.py`, `operations/scoring.py` (new or existing utility)
- Tests: update edge creation assertions in `test_lifecycle.py`

### Phase C — Hebbian + Decay (implement after Phase B)

**C1: Co-retrieval edge creation (Hebbian)**
- Add in-memory staging counter to `MemorySystem`
- Update `recall.py` to check staging after reinforcement
- Promote pairs at `CO_RETRIEVAL_EDGE_THRESHOLD = 3`
- Files: `api.py`, `recall.py`, `graph.py`, `queries.py`
- Tests: add staging and promotion cases to `test_lifecycle.py`

**C2: Edge temporal decay at curate()**
- Update `reinforce_edges()` to also set `last_active_hours`
- Add decay computation to `curate.py`
- Update `prune_weak_edges()` to use decayed effective weight
- Files: `curate.py`, `queries.py`, `graph.py`
- Tests: add decay assertions to `test_curate.py`

### Phase D — LLM Classification (implement after Phase C, optional)

**D1: LLM batch edge classification at dream()**
- After edge creation in `consolidate.py`, collect new edge pairs
- One LLM call per dream cycle to classify relationship types
- Update `relation_type` field on created edges
- Files: `consolidate.py`, `prompts/` (new prompt template)
- Tests: mock LLM response in `test_lifecycle.py`

**D2: Relation-type-aware graph expansion**
- Update `expand_1hop()` to filter or weight by `relation_type`
- `CONTRADICTS` edges surface counterpoint blocks (flagged in result)
- `SUPPORTS`/`ELABORATES` edges weighted higher in TASK/ATTENTION frames
- Files: `retrieval.py`, `graph.py`, `queries.py`
- Tests: add expansion behaviour tests per relation type

---

## 7. What NOT to Build (and Why)

| Approach | Reason to Defer |
|----------|----------------|
| **GraphRAG community hierarchies** | Designed for corpora (millions of chunks). Reindexing cost too high. Wrong abstraction for single-agent memory at 50–500 blocks. |
| **RAPTOR recursive summarisation** | Tree structure conflicts with elfmem's frame-based flat graph. Elfmem's curate() already handles summarisation via block archival. |
| **Full triplet extraction (Mem0/LightRAG style)** | O(n) LLM calls per block at ingest. Too expensive for Phase 1. May reconsider if corpus grows to 5,000+ blocks. |
| **Neo4j / dedicated graph DB** | SQLite handles Phase 1 (50–500 blocks) with no library dependency. Revisit at Phase 3 if graph algorithms become bottleneck. |
| **PageRank materialisation** | Specified for Phase 2 in architecture doc. Weighted degree (Phase 1) is sufficient. |
| **Bitemporal edge versioning (Zep style)** | Adds significant complexity. Elfmem's session-aware decay achieves temporal relevance more simply. |

---

## 8. Key Metrics to Track After Implementation

To evaluate whether improvements are working, measure:

| Metric | Measurement | Target |
|--------|-------------|--------|
| Edge type distribution | % of edges per type after 100 sessions | `co_occurs` + `outcome` > 30% (usage-earned edges) |
| Outcome edge weight trajectory | Average weight of outcome edges over time | Should trend upward as `edge_reinforce_delta` compounds |
| Co-retrieval promotion rate | Edges promoted from staging per curate cycle | Non-zero. Indicates Hebbian patterns forming. |
| Graph expansion recall lift | % of useful blocks recovered via expansion vs seeds only | Improvement vs baseline (track with A/B on `expand=True/False`) |
| Agent `connect()` usage | Edges created via agent vs automatic | Qualitative indicator of agent engagement with graph |
| Stale edge pruning | Edges pruned per curate cycle after decay | Should stabilise; runaway pruning indicates λ too high |

---

## 9. Summary

The current graph is a useful but incomplete first step. Similarity-based edges are the floor, not the ceiling. The improvements recommended here layer three types of signals — agent intent, usage patterns, and outcome confirmation — on top of the existing geometric foundation. Together they close the gap between "textually similar" and "meaningfully connected."

**The most important single change:** Add `connect()` and fix outcome edge weighting. These are trivially cheap to implement, zero LLM cost, and immediately close the feedback loop that elfmem's biological metaphor requires.

**The most architecturally significant change:** The edge schema extension (relation_type + origin + last_active_hours). Everything else builds on this foundation.

**The most novel idea:** Agent-suggested edges via `connect()`. No automated system can match the agent's contextual knowledge of why two pieces of information are related. Making the agent an active participant in graph construction — not just a consumer of retrieval results — is the defining capability that differentiates elfmem from systems that treat the graph as a pure infrastructure concern.

---

*References:*
- *Kairos: Validation-Gated Hebbian Learning for Adaptive Agent Memory (NeurIPS 2025 Workshop)*
- *BambooKG: A Neurobiologically-Inspired Frequency-Weight Knowledge Graph (arXiv 2025)*
- *Zep: A Temporal Knowledge Graph Architecture for Agent Memory (arXiv 2025)*
- *A-Mem: Agentic Memory for LLM Agents (NeurIPS 2025)*
- *Mem0: Memory-Augmented Generation (arXiv 2025)*
- *GraphRAG: Unlocking LLM Discovery on Narrative Private Data (Microsoft Research)*
- *LightRAG: Simple and Fast Retrieval-Augmented Generation (EMNLP 2025)*
- *RAPTOR: Recursive Abstractive Processing for Tree-Organized Retrieval (arXiv 2024)*
- *Graph-based Agent Memory: Taxonomy, Techniques, and Applications (arXiv 2026)*
- *elfmem architecture specification: docs/amgs_architecture.md*
- *elfmem exploration 013: Edge lifecycle design*
- *elfmem exploration 014: Contradiction edge design*
- *elfmem exploration 020: Centrality scoring*
- *elfmem exploration 021: Four-stage hybrid retrieval pipeline*
