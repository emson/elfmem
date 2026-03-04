# Playground: Retrieval — 4-Stage Hybrid Pipeline

## Status: Draft

## Subsystem Specification

The hybrid retrieval pipeline (explorations 021, 024) is the core of every
`frame()` call for ATTENTION and TASK. It converts a query into ranked blocks
in four stages.

```
Stage 1 — Pre-filter:      SQL WHERE; excludes archived + stale blocks
Stage 2 — Vector search:   cosine similarity → top N_seeds blocks
Stage 3 — Graph expand:    1-hop neighbours of seeds; similarity set to 0
Stage 4 — Composite score: rank all candidates (seeds + expansion) by formula
```

SELF frame bypasses stages 2 and 3 entirely (tag-filter + direct composite scoring).

### Stage Inputs and Outputs

| Stage | Input | Output |
|-------|-------|--------|
| Pre-filter | All active blocks | Blocks where `last_reinforced_at > (total_active_hours - search_window_hours)` |
| Vector search | Pre-filtered blocks + query embedding | Top N_seeds blocks by cosine similarity |
| Graph expand | N_seeds + edges table | N_seeds ∪ {1-hop neighbours}, deduped |
| Composite score | All candidates + their metadata | Top-K blocks ranked by composite score |

### Key Invariant: Expansion Block Similarity

Graph-expanded blocks did **not** surface in the vector search — they are
"related but not directly similar" to the query. Their `similarity` is set
to `0`, not interpolated or penalised. They compete purely on confidence,
recency, centrality, and reinforcement.

### Why This Matters

From exploration 023 (worked Celery example): Block K15 (celery-once library)
was 340 active hours stale — excluded by the pre-filter's time window. However,
it was connected via edges to K12 and K23 which **did** pass the filter. Graph
expansion recovered K15 through its neighbours, giving the LLM the exactly-right
context without the user re-explaining it.

---

## Parameters

```yaml
top_k: 5
N_seeds_multiplier: 4         # N_seeds = top_k × 4 = 20
search_window_hours: 200      # pre-filter: reinforced within this window
contradiction_oversample: 2   # sample top_k × 2 before suppression
max_expansion_candidates: 200 # N_seeds × degree_cap (20 × 10) upper bound
```

---

## Test Suite

### TC-R-001: Pre-Filter Excludes Stale and Archived Blocks

**Purpose:** Stage 1 reduces the candidate set to blocks reinforced within
`search_window_hours`. Archived blocks are always excluded.

**Given:**
```yaml
total_active_hours: 300
search_window_hours: 200   # threshold = 300 - 200 = 100

blocks:
  B_fresh:   status=active,   last_reinforced_at=180   # 180 > 100 → INCLUDED
  B_stale:   status=active,   last_reinforced_at=80    # 80 < 100  → EXCLUDED
  B_old:     status=active,   last_reinforced_at=100   # 100 not > 100 → EXCLUDED
  B_archived: status=archived, last_reinforced_at=290  # archived → EXCLUDED always
```

**When:** Pre-filter applied at `total_active_hours=300`

**Then:**
- B_fresh: **INCLUDED** (180 > 100)
- B_stale: **EXCLUDED** (80 < 100)
- B_old: **EXCLUDED** (100 not strictly greater than 100)
- B_archived: **EXCLUDED** (wrong status)

**Expected:** Only B_fresh passes; 1 candidate block
**Status:** NOT YET RUN

---

### TC-R-002: Vector Search Returns N_seeds Blocks

**Purpose:** Stage 2 returns exactly N_seeds = top_k × N_seeds_multiplier blocks,
sorted by descending cosine similarity.

**Given:**
```yaml
top_k: 5
N_seeds_multiplier: 4    # N_seeds = 20
pre_filtered_blocks: 35  # 35 blocks survived pre-filter
query: "async patterns Python"
```

**When:** Vector search over 35 pre-filtered blocks

**Then:**
- Returns exactly 20 blocks (N_seeds)
- Blocks sorted by cosine similarity (descending)
- Block at rank 1 has highest similarity to query

**Expected:** 20 blocks, sorted by similarity
**Status:** NOT YET RUN

---

### TC-R-003: Vector Search Returns All Blocks When Fewer Than N_seeds

**Purpose:** When fewer pre-filtered blocks exist than N_seeds, all of them
become seeds (no truncation needed).

**Given:**
```yaml
N_seeds: 20
pre_filtered_blocks: 8   # fewer than N_seeds
```

**When:** Vector search over 8 blocks

**Then:** All 8 blocks become seeds (no blocks excluded at this stage)

**Expected:** 8 seeds; no truncation
**Status:** NOT YET RUN

---

### TC-R-004: Graph Expansion Adds 1-Hop Neighbours with similarity=0

**Purpose:** Stage 3 adds blocks connected to seeds via edges, setting their
`similarity=0` since they were not found by vector search.

**Given:**
```yaml
seeds: [B1, B2, B3]  # from vector search

edges:
  B1 — B4 (weight=0.75)  # B4 not in seeds
  B2 — B5 (weight=0.60)  # B5 not in seeds
  B3 — B1 (weight=0.80)  # B1 already a seed — skip
  B4 — B6 (weight=0.70)  # B6 is 2-hop from B1 — skip (1-hop only)
```

**When:** Graph expansion from seeds [B1, B2, B3]

**Then:**
- Candidates = {B1, B2, B3, B4, B5}
- B4: `similarity=0.0`, `was_expanded=True`
- B5: `similarity=0.0`, `was_expanded=True`
- B6: not included (2-hop from seed)

**Expected:** B4 and B5 added with similarity=0; B6 excluded
**Status:** NOT YET RUN

---

### TC-R-005: High-Centrality Expanded Block Can Beat Low-Centrality Seed

**Purpose:** An expanded block with high centrality and good reinforcement
can outrank a seed block with high similarity but poor other signals.

**Given:**
```yaml
# B_seed: came from vector search (high similarity, low other signals)
B_seed:
  similarity:    0.88   # high — found by vector search
  confidence:    0.45
  recency:       0.40
  centrality:    0.20
  reinforcement: 0.15
  was_expanded:  false
  # ATTENTION score = 0.35×0.88 + 0.15×0.45 + 0.25×0.40 + 0.15×0.20 + 0.10×0.15
  #                 = 0.308 + 0.068 + 0.100 + 0.030 + 0.015 = 0.521

# B_exp: came from graph expansion (similarity=0, strong other signals)
B_exp:
  similarity:    0.00   # not found by vector search
  confidence:    0.85
  recency:       0.80
  centrality:    0.90   # hub block — highly connected
  reinforcement: 0.75
  was_expanded:  true
  # ATTENTION score = 0.35×0.00 + 0.15×0.85 + 0.25×0.80 + 0.15×0.90 + 0.10×0.75
  #                 = 0.000 + 0.128 + 0.200 + 0.135 + 0.075 = 0.538
```

**When:** Composite scoring of candidates including B_seed and B_exp

**Then:** `score(B_exp) = 0.538 > score(B_seed) = 0.521`

The expanded block beats the vector-search block because its centrality and
recency signals compensate for zero similarity.

**Expected:** B_exp ranked above B_seed
**Tolerance:** ±0.001
**Status:** NOT YET RUN

---

### TC-R-006: SELF Frame Bypasses Vector Search and Graph Expansion

**Purpose:** SELF frame has no query — stages 2 and 3 are skipped entirely.
No embedding call is made.

**Given:**
```yaml
call: system.frame("self")   # no query
```

**When:** Frame pipeline executes

**Then:**
- `embed()` not called (0 embedding API calls)
- Stage 2 (vector search) skipped
- Stage 3 (graph expansion) skipped
- Stage 4 runs directly on pre-filtered `self/*`-tagged blocks

**Expected:** Zero embedding calls; direct composite scoring on tagged blocks
**Status:** NOT YET RUN

---

### TC-R-007: Contradiction Suppression Removes Lower-Confidence Block

**Purpose:** If two blocks in the top-K candidate set have an active contradiction
record, the lower-confidence block is removed and replaced by the next candidate.

**Given:**
```yaml
top_k: 5
candidates (ranked by score): [B1, B2, B3_old, B4, B3_new, B6, B7]
# B3_old and B3_new have an active contradiction record
# B3_old has confidence=0.60; B3_new has confidence=0.85
# After composite scoring, B3_old ranks at position 3, B3_new at position 5
```

**When:** Contradiction suppression pass

**Then:**
- B3_old (lower confidence) removed from results
- B3_new (higher confidence) retained
- B6 fills position 5 (next candidate after B3_new)
- Final results: [B1, B2, B3_new, B4, B6]

**Expected:** 5 blocks returned; B3_old absent; B3_new present
**Status:** NOT YET RUN

---

### TC-R-008: End-to-End Retrieval Returns Valid Results

**Purpose:** Full pipeline (pre-filter → vector → expand → score → suppress → top-K)
returns well-formed results.

**Given:**
```yaml
total_active_hours: 100
blocks: 15 active blocks (various similarity, confidence, recency values)
query: "async database patterns"
top_k: 5
```

**When:** `await system.frame("attention", query="async database patterns")`

**Then:**
- Returns exactly 5 blocks (or fewer if corpus < 5 active blocks)
- All returned blocks have `status=active`
- No contradicting block pair in results
- `.text` is non-empty string
- Each block in `.blocks` has a valid composite score

**Expected:** 5 valid, non-contradicting active blocks; well-formed FrameResult
**Status:** NOT YET RUN

---

## Parameter Tuning

### PT-1: N_seeds_multiplier (currently 4)

**Question:** Does `N_seeds = top_k × 4` provide enough headroom for graph
expansion to matter?

**Scenario:** With top_k=5, N_seeds=20. Each seed can expand to up to 10 neighbours
(degree cap). Worst case: 20 × 10 = 200 candidates before composite scoring.

**Concern:** If N_seeds is too small, the graph expansion step can't recover
blocks that would have been useful — the seeds don't reach the relevant neighbourhoods.

**Test:** Run 3 agents with N_seeds_multiplier at 2, 4, 6. Measure:
- How often does an expanded block make the final top-K?
- Does increasing to 6 improve recall quality, or is 4 already sufficient?

**Hypothesis:** 4 is sufficient for typical agents. Only high-fan-out knowledge
graphs (many cross-domain connections) benefit from 6+.

---

### PT-2: search_window_hours — No "Would-Have-Won" Block Excluded

**Question:** Does `search_window_hours=200` ever exclude a block that would have
won composite scoring if included?

**Key invariant:** A block excluded by pre-filter at 200h has `recency = e^(-0.010×200) = 0.135`
(standard tier). Could such a block win composite scoring?

**Worst case analysis:**
```
ATTENTION score (max non-recency):
= 0.35×1.0 + 0.15×1.0 + 0.25×0.135 + 0.15×1.0 + 0.10×1.0
= 0.35 + 0.15 + 0.034 + 0.15 + 0.10 = 0.784

A block with all-max non-recency signals and 200h staleness scores 0.784.
A freshly-reinforced block with moderate signals:
= 0.35×0.70 + 0.15×0.65 + 0.25×0.95 + 0.15×0.55 + 0.10×0.50
= 0.245 + 0.098 + 0.238 + 0.083 + 0.050 = 0.714
```

**Conclusion:** A stale-but-excellent block (all-max signals) still outscores a
fresh-moderate block (0.784 vs 0.714). The pre-filter CAN exclude blocks that would win.

**Implication:** `search_window_hours=200` is a performance optimisation, not
correctness-critical at Phase 1 scale. At small corpora (≤100 blocks), consider
**disabling** the pre-filter entirely (setting `search_window_hours` very high).
Only enable aggressive pre-filtering at Phase 2+ scale.

---

## Open Assertions

1. Pre-filter result is a strict subset of all active blocks
2. Expansion blocks are a strict subset of active blocks (archived neighbours ignored)
3. No block appears twice in the final candidate set (seeds ∪ expansion is deduped)
4. Composite scoring is applied to all candidates equally (seeds and expansion use same formula)
5. The contradiction suppression step never adds blocks — it only removes
6. The final top-K count is always `min(top_k, len(candidates))` — never over-reports

---

## Python Test Sketch

```python
# elfmem/tests/test_retrieval.py

import pytest
from elfmem import MemorySystem
from tests.fixtures import MockLLMService, MockEmbeddingService

async def test_pre_filter_excludes_stale(system):
    # Set up: one fresh block, one stale block
    system.set_total_active_hours(300)
    fresh_id = await system.learn("async patterns")   # reinforced recently
    stale_id = "B_stale"  # last_reinforced_at=50 (250 hours ago)
    system._set_last_reinforced_at(stale_id, 50)
    await system.consolidate()

    result = await system.frame("attention", query="async")
    ids = [b.id for b in result.blocks]
    assert fresh_id in ids
    assert stale_id not in ids

async def test_expansion_blocks_have_zero_similarity(system):
    # Set up: B1 has edge to B2; query only similar to B1
    result = await system.frame("attention", query="celery tasks")
    expanded = [b for b in result.blocks if b.was_expanded]
    assert all(b.similarity == 0.0 for b in expanded)

async def test_self_frame_no_embed_call(system, mock_embedding):
    await system.frame("self")
    assert mock_embedding.embed.call_count == 0

async def test_contradiction_suppression(system):
    result = await system.frame("attention", query="caching strategy")
    ids = [b.id for b in result.blocks]
    # B_old and B_new contradict; only one should appear
    assert not ("B_old" in ids and "B_new" in ids)
    assert len(result.blocks) == 5  # top_k still satisfied

async def test_end_to_end_valid_results(system):
    result = await system.frame("attention", query="database async")
    assert 1 <= len(result.blocks) <= 5
    assert all(b.status == "active" for b in result.blocks)
    assert isinstance(result.text, str) and len(result.text) > 0
```
