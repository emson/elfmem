# Plan: Graph Composite Edge Scoring (Phase B2)

**Status:** Ready for implementation
**Date:** 2026-03-09
**Scope:** Phase B2 — replace single-signal cosine edge scoring with multi-signal composite in `consolidate()`
**Research source:** `docs/graph_improvement_research.md` §4.5 and §6 (Phase B2)
**Depends on:** Phase A (schema + outcome fix) — COMPLETE
**Out of scope:** Hebbian co-retrieval (C1), edge temporal decay (C2), LLM classification (D1)

---

## 1. Problem

`consolidate()` Phase 3 creates edges using only cosine similarity:

```python
sim = cosine_similarity(vec, a_vec)
if sim >= similarity_edge_threshold:        # 0.60
    candidates.append((a_block["id"], sim))
...
await insert_edge(..., weight=sim)
```

This misses three signals that are already in the DB, require zero extra LLM calls, and capture meaningfully different relationship types:

| Signal | Captures | Available in DB? |
|--------|---------|-----------------|
| Tag Jaccard | Categorical kinship | ✅ `block_tags` table |
| Category match | Domain alignment (observation vs preference vs fact) | ✅ `blocks.category` |
| Temporal proximity | Same-session learning → experiential connection | ✅ `blocks.last_reinforced_at` |

**Failure cases:**
- Two blocks tagged `frame-selection/heuristic`, phrased differently → cosine 0.45 → no edge. They are deeply related.
- Two blocks cosine 0.62 from unrelated domains → weak edge that will never be used.
- Two blocks learned in the same troubleshooting session, tagged `debugging` → cosine 0.50 → no edge.

---

## 2. Solution

```
edge_score = cosine × 0.55
           + tag_jaccard × 0.20
           + category_match × 0.15
           + temporal_proximity × 0.10
```

**Properties:**
- Score ∈ [0.0, 1.0] — same range as cosine, threshold is directly comparable
- Symmetric: `score(a, b) == score(b, a)`
- Pure: deterministic, no LLM calls, no side effects
- Zero schema changes — all signals already exist

**Threshold calibration** (`edge_score_threshold = 0.40`, `MINIMUM_COSINE_FOR_EDGE = 0.30`):

| Scenario | Score | Edge? |
|----------|-------|-------|
| cosine=0.60, no context signals | ~0.38 | No — raised bar for pure cosine |
| cosine=0.70, no context signals | ~0.43 | Yes |
| cosine=0.50, shared tags + same category + same session | ~0.62 | Yes — new capability |
| cosine=0.45, 5 shared tags, same category, same session | ~0.55 | Yes — new capability |
| cosine=0.60, same category only | ~0.53 | Yes |
| cosine=0.28, same category + same session (no tags) | — | **No** — cosine guard rejects at 0.28 < 0.30 |
| cosine=0.30, same category + same session (no tags) | ~0.415 | Yes — minimum acceptable edge |

Minimum cosine for a pure-cosine pair to qualify: `(0.40 − 0.045) / 0.55 ≈ 0.65`

**Why the cosine guard exists:** Same-session + same-category context gives a fixed non-cosine floor of 0.25 (when tags=[]). Without a guard, blocks with cosine≈0.27 would form edges purely from shared session context. At that cosine level, blocks are semantically close to random. Graph expansion during recall follows these spurious edges and injects off-topic blocks into the agent's context — degrading answer quality silently.

---

## 3. Architecture Decision — Where Each Function Lives

### `scoring.py` — pure Python helper functions only

`scoring.py` is explicitly a zero-numpy, zero-DB module (`import math` only). Two new pure functions belong here because they are generic, reusable, and could serve future edge scoring paths (co-retrieval, curate decay):

```python
jaccard_similarity(tags_a: list[str], tags_b: list[str]) -> float
temporal_proximity(hours_a: float, hours_b: float, *, sigma: float) -> float
```

Constants added alongside:
```python
TEMPORAL_SIGMA_HOURS: float = 8.0      # Gaussian half-width in active hours
CROSS_CATEGORY_SCORE: float = 0.30     # frame_match when categories differ
```

### `consolidate.py` — composite orchestrator as private function

`consolidate.py` already imports `numpy` and `cosine_similarity`. The composite function is a private implementation detail of Phase 3 — it has exactly one caller and is not a reusable utility:

```python
def _composite_edge_score(
    vec_a: np.ndarray,
    vec_b: np.ndarray,
    tags_a: list[str],
    tags_b: list[str],
    hours_a: float,
    hours_b: float,
    category_a: str,
    category_b: str,
) -> float
```

This keeps `scoring.py` numpy-free and `consolidate.py` self-contained.

---

## 4. File Changes

| File | Change |
|------|--------|
| `src/elfmem/scoring.py` | Add `jaccard_similarity()`, `temporal_proximity()`, 2 constants |
| `src/elfmem/operations/consolidate.py` | Add `_composite_edge_score()`, rewrite Phase 3, rename constant+param |
| `src/elfmem/db/queries.py` | Add `get_tags_batch()` |
| `src/elfmem/config.py` | Rename `similarity_edge_threshold` → `edge_score_threshold`, default 0.60 → 0.40 |
| `src/elfmem/api.py` | Update param name in `consolidate()` call |
| `src/elfmem/guide.py` | Add one sentence to `dream` entry: tags increase graph connectivity |
| `tests/test_lifecycle.py` | Update 2 existing tests that pass `similarity_edge_threshold=0.60` explicitly |
| `tests/test_edge_scoring.py` | **New** — pure function unit tests |
| `tests/test_storage.py` | Add `get_tags_batch()` test |

No schema changes. No new result types. No MCP changes.

**Guide change:** `src/elfmem/guide.py` — add one sentence to the `dream` entry noting that tags increase graph connectivity.

---

## 5. `scoring.py` — New Functions

Add after the existing `compute_lambda_edge()` function.

### Constants

```python
# Temporal proximity — Gaussian kernel half-width in active hours.
# At TEMPORAL_SIGMA_HOURS apart: proximity = exp(-0.5) ≈ 0.61.
# At 3× sigma apart: proximity < 0.01 (effectively different sessions).
TEMPORAL_SIGMA_HOURS: float = 8.0

# Category match score when blocks belong to different categories.
# 0.30 means "different domains still have baseline relationship potential"
# rather than zero, which would make category dominate the score unfairly.
CROSS_CATEGORY_SCORE: float = 0.30

# Hard minimum cosine before any edge is considered.
# Without this guard, same-session + same-category blocks can form edges
# at cosine ≈ 0.27 (non-cosine floor = 0.25 when temporal=cat=1.0, tags=[]).
# This causes recall poisoning: graph expansion follows spurious edges to
# off-topic blocks, injecting unrelated content into the agent's prompt.
# 0.30 is the semantic floor for "might be related" in embedding space.
MINIMUM_COSINE_FOR_EDGE: float = 0.30
```

### `jaccard_similarity()`

```python
def jaccard_similarity(tags_a: list[str], tags_b: list[str]) -> float:
    """Jaccard similarity between two tag lists: |A ∩ B| / |A ∪ B|.

    Returns 0.0 when both lists are empty (no categorical signal).
    Returns 0.0 when one list is empty (no shared categories).
    Returns 1.0 for identical non-empty lists.
    """
    set_a = set(tags_a)
    set_b = set(tags_b)
    union = set_a | set_b
    if not union:
        return 0.0
    return len(set_a & set_b) / len(union)
```

### `temporal_proximity()`

```python
def temporal_proximity(
    hours_a: float,
    hours_b: float,
    *,
    sigma: float = TEMPORAL_SIGMA_HOURS,
) -> float:
    """Gaussian proximity between two active-hour timestamps.

    Returns exp(−Δh² / 2σ²) ∈ (0.0, 1.0].
    Same hours → 1.0. sigma hours apart → ≈ 0.61. 3×sigma → < 0.01.

    Uses active hours (session-aware clock), not wall-clock time.
    Symmetric: temporal_proximity(a, b) == temporal_proximity(b, a).
    """
    delta = hours_a - hours_b
    return math.exp(-(delta * delta) / (2.0 * sigma * sigma))
```

---

## 6. `consolidate.py` — Changes

### New private function `_composite_edge_score()`

Add above the `consolidate()` function. Uses `cosine_similarity` (already imported from `dedup`), `np` (already imported), and the two new `scoring.py` helpers.

```python
def _composite_edge_score(
    vec_a: np.ndarray,
    vec_b: np.ndarray,
    tags_a: list[str],
    tags_b: list[str],
    hours_a: float,
    hours_b: float,
    category_a: str,
    category_b: str,
) -> float:
    """Multi-signal edge quality score for similarity-origin edges.

    Formula: cosine×0.55 + tag_jaccard×0.20 + category_match×0.15 + temporal×0.10

    Cosine is clamped to [0.0, 1.0] — negative cosine (anti-correlated vectors)
    contributes 0 rather than penalising contextually related blocks.

    Hard guard: returns 0.0 if cosine < MINIMUM_COSINE_FOR_EDGE.
    Without this, same-session + same-category context signals (non-cosine
    floor = 0.25) would allow cosine ≈ 0.27 to form edges — below the semantic
    floor for meaningful relatedness. This causes recall poisoning where graph
    expansion follows spurious edges and injects off-topic blocks into context.
    """
    w_cos, w_tag, w_cat, w_temp = 0.55, 0.20, 0.15, 0.10
    cos = max(0.0, cosine_similarity(vec_a, vec_b))
    if cos < MINIMUM_COSINE_FOR_EDGE:
        return 0.0
    tag  = jaccard_similarity(tags_a, tags_b)
    cat  = 1.0 if category_a == category_b else CROSS_CATEGORY_SCORE
    temp = temporal_proximity(hours_a, hours_b)
    return w_cos * cos + w_tag * tag + w_cat * cat + w_temp * temp
```

**Import addition:**
```python
from elfmem.scoring import (
    ...          # existing imports
    CROSS_CATEGORY_SCORE,
    MINIMUM_COSINE_FOR_EDGE,
    jaccard_similarity,
    temporal_proximity,
)
```

### Constant rename

```python
# Before:
SIMILARITY_EDGE_THRESHOLD = 0.60

# After:
EDGE_SCORE_THRESHOLD = 0.40
```

### Parameter rename in `consolidate()` signature

```python
# Before:
similarity_edge_threshold: float = SIMILARITY_EDGE_THRESHOLD,

# After:
edge_score_threshold: float = EDGE_SCORE_THRESHOLD,
```

### Phase 3 rewrite

**Before:**
```python
# ── Phase 3: edge creation ────────────────────────────────────────────────
edges_created = 0
all_active_items = list(active_vecs.values())

for block, vec in newly_promoted:
    block_id = block["id"]
    candidates = []
    for a_block, a_vec in all_active_items:
        if a_block["id"] == block_id:
            continue
        sim = cosine_similarity(vec, a_vec)
        if sim >= similarity_edge_threshold:
            candidates.append((a_block["id"], sim))

    candidates.sort(key=lambda x: x[1], reverse=True)
    for other_id, sim in candidates[:edge_degree_cap]:
        from_id, to_id = Edge.canonical(block_id, other_id)
        await insert_edge(
            conn,
            from_id=from_id,
            to_id=to_id,
            weight=sim,
            relation_type="similar",
            origin="similarity",
        )
        edges_created += 1
```

**After:**
```python
# ── Phase 3: edge creation ────────────────────────────────────────────────
# Composite score: cosine×0.55 + tag_jaccard×0.20 + category×0.15 + temporal×0.10
# One batch tag query for all active blocks — avoids N per-block queries.
edges_created = 0
all_active_items = list(active_vecs.values())
all_block_ids = [b["id"] for b, _ in all_active_items]
tags_cache: dict[str, list[str]] = await get_tags_batch(conn, all_block_ids)

for block, vec in newly_promoted:
    block_id = block["id"]
    # Use current_active_hours — the block dict is from Phase 0 (stale last_reinforced_at=0.0).
    # reinforce_blocks() updated the DB in Phase 2, but the in-memory dict was not refreshed.
    block_hours = current_active_hours
    block_category = block["category"]
    tags = tags_cache.get(block_id, [])
    candidates = []

    for a_block, a_vec in all_active_items:
        if a_block["id"] == block_id:
            continue
        score = _composite_edge_score(
            vec, a_vec,
            tags, tags_cache.get(a_block["id"], []),
            block_hours, float(a_block.get("last_reinforced_at") or 0.0),
            block_category, a_block["category"],
        )
        if score >= edge_score_threshold:
            candidates.append((a_block["id"], score))

    candidates.sort(key=lambda x: x[1], reverse=True)
    for other_id, score in candidates[:edge_degree_cap]:
        from_id, to_id = Edge.canonical(block_id, other_id)
        await insert_edge(
            conn,
            from_id=from_id,
            to_id=to_id,
            weight=score,          # composite score, not raw cosine
            relation_type="similar",
            origin="similarity",
        )
        edges_created += 1
```

**Import addition:**
```python
from elfmem.db.queries import (
    ...          # existing imports
    get_tags_batch,
)
```

### Temporal hours: why `current_active_hours` not `block["last_reinforced_at"]`

The block dict in `newly_promoted` comes from `get_inbox_blocks()` at Phase 0. At that point `last_reinforced_at = 0.0` (the initial value from `insert_block()`). Phase 2 calls `reinforce_blocks(conn, [block_id], current_active_hours)` which updates the DB row, but does NOT update the Python dict. The dict is stale.

For **existing active blocks** (`a_block` from `active_vecs`): `last_reinforced_at` is accurate — these blocks were fetched in Phase 0 and Phase 2 does not reinforce them.

Result: a newly promoted block gets `hours = current_active_hours`, existing active blocks get `hours = last_reinforced_at`. This correctly models "newly learned blocks share the current session time."

---

## 7. `queries.py` — `get_tags_batch()`

```python
async def get_tags_batch(
    conn: AsyncConnection,
    block_ids: list[str],
) -> dict[str, list[str]]:
    """Fetch tags for multiple blocks in a single query.

    Returns {block_id: [tags_sorted_alphabetically]}.
    Block IDs with no tags return []. Missing IDs also return [].
    """
    if not block_ids:
        return {}
    result = await conn.execute(
        select(block_tags.c.block_id, block_tags.c.tag)
        .where(block_tags.c.block_id.in_(block_ids))
        .order_by(block_tags.c.block_id, block_tags.c.tag)
    )
    tags_map: dict[str, list[str]] = {bid: [] for bid in block_ids}
    for row in result.mappings():
        tags_map[row["block_id"]].append(row["tag"])
    return tags_map
```

One query replaces up to N queries (one per block). Safe with empty input. Alphabetically sorted for deterministic Jaccard (set operations are order-independent but consistent output is cleaner for debugging).

---

## 8. `config.py` — Rename and Recalibrate

```python
# MemoryConfig — before:
similarity_edge_threshold: float = 0.60

# After:
edge_score_threshold: float = 0.40
```

`similarity_edge_threshold` implied cosine only. `edge_score_threshold` is implementation-agnostic and describes what it actually is: the minimum quality score for an edge to be created.

**Also update `render_default_config()`:**
```python
# Before:
  similarity_edge_threshold: {d.memory.similarity_edge_threshold}

# After:
  edge_score_threshold: {d.memory.edge_score_threshold}
```

---

## 9. `api.py` — Param Name Update

```python
# In MemorySystem._consolidate() / dream() — before:
similarity_edge_threshold=self._config.memory.similarity_edge_threshold,

# After:
edge_score_threshold=self._config.memory.edge_score_threshold,
```

---

## 10. Edge Case Analysis

| Scenario | Behaviour | Why it's correct |
|----------|-----------|-----------------|
| Both blocks have no tags | `jaccard = 0.0`, tag component = 0 | No shared category signal → no categorical boost. Score relies on cosine + category + temporal. |
| One block has tags, other has none | `jaccard = 0.0` | Asymmetric tagging means no shared signal. |
| Blocks both have `last_reinforced_at = 0.0` | `temporal = 1.0` | Both are brand-new blocks; 0.0 is the initial inbox value. In the Phase 3 loop, newly promoted blocks get `block_hours = current_active_hours` (not 0.0). The 0.0 case only arises if somehow an existing active block has never been reinforced — in which case temporal=1.0 vs current_active_hours will produce a low proximity anyway. |
| Newly promoted block vs old active block | `block_hours = current_active_hours`, `a_block_hours = old_hours` → large delta → `temporal ≈ 0` | Correct. Old blocks are from a different session. Cosine + tags + category still determine the edge. |
| Two newly promoted blocks in the same batch | Both get `block_hours = current_active_hours` → `temporal = 1.0` | They were learned in the same session. Temporal bonus is justified. |
| Negative cosine similarity | Clamped to 0.0 before applying weight | Anti-correlated vectors shouldn't penalise blocks that share tags and category. Floor is 0, not negative. |
| Empty category string `""` | `"" == ""` → `category_match = 1.0` | Category is a non-null column. Empty string means data quality issue, not a scoring concern. The consolidate pipeline always sets category. |
| All blocks in batch share same category | All get `category_match = 1.0` | Correct. A batch of SELF-frame blocks should have stronger edges between them. |
| All blocks share same session AND same category (e.g. "knowledge" throughout) | Non-cosine floor = 0.25 (no tags) or 0.45 (full tag match). Without the guard, cosine≈0.27 would qualify. | `MINIMUM_COSINE_FOR_EDGE = 0.30` hard rejects before scoring. Blocks with cosine=0.30 still get contextual boost; blocks below are structurally unrelated. |
| Large batch (50 blocks, same session) | 50×49/2 = 1,225 candidate pairs. Without guard, same-session floor lets spurious edges form at scale. | Guard prevents recall poisoning even in high-volume ingestion. Degree cap (5) then limits graph density. |
| Recall quality impact | Spurious edges cause graph expansion to inject off-topic blocks into prompt context. | Guard is a silent quality guarantee — agent never sees the failure mode. |
| Degree cap | Unchanged. `candidates[:edge_degree_cap]` takes the top-N by composite score. | Composite score ordering is strictly better than cosine ordering. Best-quality edges survive. |
| Tags for newly promoted blocks | Added in Phase 2 (`add_tags()`). Batch query runs at start of Phase 3, within the same transaction. All tags are visible. | Transaction isolation: writes in Phase 2 visible to Phase 3 reads on the same connection. ✓ |
| Tags for blocks added in this batch | Newly promoted blocks ARE added to `active_vecs` at end of Phase 2 loop. Their IDs are in `all_block_ids`. `tags_cache` includes them. | ✓ |
| Backward compatibility — existing DB edges | Existing edges have weights = raw cosine (0.60–0.85). New edges after upgrade have composite weights (0.40–0.95). Mixed graph until blocks cycle through. | `prune_weak_edges(threshold=0.10)` treats all weights the same — no issue at that threshold. Graph expansion orders by weight; old high-cosine edges appear slightly more relevant than equivalent new composite edges. Self-heals as active blocks accumulate new composite edges over time. No migration needed. |

---

## 11. Test Plan

### New file: `tests/test_edge_scoring.py`

Pure function tests — no DB, no async, no fixtures needed.

```python
from elfmem.scoring import (
    CROSS_CATEGORY_SCORE,
    TEMPORAL_SIGMA_HOURS,
    jaccard_similarity,
    temporal_proximity,
)
from elfmem.operations.consolidate import _composite_edge_score

TOL = 0.001

class TestJaccardSimilarity:
    def test_both_empty_returns_zero():             # [], [] → 0.0
    def test_identical_lists_return_one():           # ["a","b"], ["a","b"] → 1.0
    def test_partial_overlap():                      # {a,b} ∩ {b,c} / {a,b,c} → 1/3
    def test_no_overlap_returns_zero():              # {a} ∩ {b} → 0.0
    def test_one_empty_returns_zero():               # [], ["x","y"] → 0.0
    def test_symmetric():                            # jaccard(a,b) == jaccard(b,a)

class TestTemporalProximity:
    def test_same_hours_returns_one():               # hours_a==hours_b → 1.0
    def test_sigma_apart():                          # |Δh|==TEMPORAL_SIGMA_HOURS → exp(-0.5)
    def test_far_apart_near_zero():                  # |Δh|==3×sigma → < 0.012
    def test_symmetric():                            # proximity(a,b) == proximity(b,a)

class TestCompositeEdgeScore:
    def test_perfect_match_returns_one():
    # Identical vecs (cosine=1.0), identical tags (jaccard=1.0),
    # same category, same hours → score = 1.0

    def test_zero_cosine_no_tags_different_cat_returns_min():
    # cosine=0.0, jaccard=0.0, different cat → 0.3×0.15 + 1.0×0.10 = 0.145
    # (temporal=1.0 when both hours=0.0)

    def test_tag_overlap_boosts_above_cosine_threshold():
    # cosine=0.50 → cosine component only = 0.275
    # + full tag overlap: 0.275 + 0.20 = 0.475 (above 0.40 threshold)

    def test_same_category_boosts_vs_different():
    # delta = (1.0 - 0.30) × 0.15 = 0.105 difference

    def test_score_bounded_zero_to_one()
    def test_symmetric()

    def test_negative_cosine_clamped():
    # Anti-correlated vecs: cosine < 0 → clamped to 0.0
    # Score = tag + category + temporal components only

    def test_minimum_cosine_guard_returns_zero():
    # cosine=0.28, same category, same session, shared tags
    # Without guard: 0.28×0.55 + 0.20 + 0.15 + 0.10 = 0.604 (would qualify)
    # With guard: cosine < MINIMUM_COSINE_FOR_EDGE → return 0.0
    # Assert _composite_edge_score(...) == 0.0

    def test_at_minimum_cosine_returns_nonzero():
    # cosine=0.30 (exactly at guard), same cat, same session, no tags
    # Expected: 0.30×0.55 + 0 + 0.15 + 0.10 = 0.415 (not zero)
    # Assert abs(score - 0.415) < TOL
```

### `test_storage.py` — add `get_tags_batch` test

```python
class TestGetTagsBatch:
    async def test_returns_tags_for_multiple_blocks(db_conn):
    # Insert two blocks with tags, call get_tags_batch([id1, id2])
    # Assert tags_map[id1] == expected_tags, tags_map[id2] == expected_tags

    async def test_empty_input_returns_empty_dict(db_conn):
    # get_tags_batch(conn, []) → {}

    async def test_missing_block_id_returns_empty_list(db_conn):
    # Block exists but has no tags → []
```

### `test_lifecycle.py` — update existing + add integration tests

**New integration test: minimum cosine guard in practice:**

```python
async def test_same_session_low_cosine_no_edge(system):
    # cosine=0.28 between two "knowledge" blocks learned in same session
    # Even with same category and same session (temporal=1.0, cat=1.0),
    # the guard prevents the edge. Assert edges_created == 0.
    embedding_svc = MockEmbeddingService(
        similarity_overrides={
            frozenset({"python async", "sql optimization"}): 0.28
        }
    )
    # ... learn both, consolidate, assert no edge
```

**Existing tests that break (must update):**

- `TC-G-001` line 355: `similarity_edge_threshold=0.60` → `edge_score_threshold=0.65`
  - Test currently relies on cosine=0.78 with no context signals. At threshold=0.65 a cosine=0.78 pair still qualifies (composite ≈ 0.48). Updated threshold for the call; assertion unchanged.
- `TC-G-002` line 394: `similarity_edge_threshold=0.60` → `edge_score_threshold=0.40` (or just remove it to use the default)

**New integration tests (`TestCompositeEdgeCreation`):**

```python
async def test_shared_tags_form_edge_at_low_cosine():
    # Setup: MockEmbeddingService with similarity_overrides controlling cosine to ~0.50
    # MockLLMService with tag_overrides so both blocks get ["frame-selection"]
    # Call consolidate(). Assert edges_created >= 1.
    # Cosine alone (0.50 < default 0.65 minimum) would not qualify; tags push it above 0.40.
    embedding_svc = MockEmbeddingService(
        similarity_overrides={
            frozenset({"frame heuristic alpha", "frame heuristic beta"}): 0.50
        }
    )
    llm = MockLLMService(tag_overrides={"frame heuristic": ["frame-selection/heuristic"]})
    # ... learn both blocks, consolidate, assert edge exists

async def test_pure_cosine_0_62_does_not_form_edge():
    # cosine=0.62, no tag overlap, different categories
    # composite ≈ 0.62×0.55 + 0 + 0.3×0.15 + temporal ≈ 0.38 < 0.40
    # Assert edges_created == 0

async def test_edge_weight_is_composite_not_cosine():
    # cosine=0.78, same tags (jaccard=1.0), same category, same session
    # expected_weight = 0.78×0.55 + 1.0×0.20 + 1.0×0.15 + 1.0×0.10 = 0.879
    # Fetch edge from DB, assert abs(edge["weight"] - 0.879) < TOL
    # Verify weight != 0.78 (not raw cosine)
```

**Test setup pattern** (consistent with existing tests in `test_lifecycle.py`):
```python
embedding_svc = MockEmbeddingService(
    similarity_overrides={
        frozenset({"content a".strip().lower(), "content b".strip().lower()}): 0.50
    }
)
```
Note: `consolidate.py` normalises content as `content.strip().lower()` before embedding. Keys must match.

---

## 12. Implementation Order

Green test suite at every step.

### Step 1 — `scoring.py`: pure functions
Add `TEMPORAL_SIGMA_HOURS`, `CROSS_CATEGORY_SCORE`, `jaccard_similarity()`, `temporal_proximity()`.
**Run:** `tests/test_edge_scoring.py` (pure functions, no DB) — all pass immediately.

### Step 2 — `queries.py`: batch tag query
Add `get_tags_batch()`.
**Run:** new test in `test_storage.py` — passes.

### Step 3 — `config.py`: rename threshold
`similarity_edge_threshold` → `edge_score_threshold`, default 0.60 → 0.40.
Update `render_default_config()`.
**Run:** `python -m pytest tests/ -q` — catch any test that references the old name.

### Step 4 — `consolidate.py`: replace Phase 3
Add `_composite_edge_score()`. Rewrite Phase 3. Rename constant and parameter.
Add imports: `get_tags_batch`, `CROSS_CATEGORY_SCORE`, `jaccard_similarity`, `temporal_proximity`.
**Run:** `tests/test_lifecycle.py` — update the two tests that pass `similarity_edge_threshold` explicitly.

### Step 5 — `api.py`: param rename
Update `consolidate()` call to pass `edge_score_threshold`.
**Run:** `python -m pytest tests/ -q` — all 353+ tests pass.

### Step 6 — Integration tests
Add `TestCompositeEdgeCreation` to `test_lifecycle.py` (including minimum cosine guard test).
**Run:** `python -m pytest tests/ -q` — all tests pass.

### Step 7 — Guide update
In `guide.py`, add to the `dream` entry: "Tags assigned during consolidation directly affect graph connectivity — blocks with shared tags form edges at lower cosine similarity."
**Run:** `python -m pytest tests/ -q` — no regressions.

---

## 13. Success Criteria

- [ ] `jaccard_similarity()`, `temporal_proximity()`, `MINIMUM_COSINE_FOR_EDGE`, `CROSS_CATEGORY_SCORE`, `TEMPORAL_SIGMA_HOURS` are in `scoring.py` with zero numpy
- [ ] `_composite_edge_score()` is private to `consolidate.py`
- [ ] Score is in [0.0, 1.0] for all valid inputs; symmetric
- [ ] `_composite_edge_score()` returns 0.0 for any cosine < `MINIMUM_COSINE_FOR_EDGE` regardless of other signals
- [ ] Shared-tag blocks form edges at cosine below the old threshold (≥ 0.30)
- [ ] Pure-cosine blocks require cosine ≥ 0.65 to form an edge
- [ ] Same-session + same-category blocks at cosine=0.28 do NOT form edges (recall poisoning prevented)
- [ ] Edge weights stored in DB are composite scores, not raw cosine values
- [ ] One `get_tags_batch()` call per `consolidate()` invocation (not N)
- [ ] Zero new LLM calls
- [ ] `edge_score_threshold` in `MemoryConfig`, default 0.40; old name gone
- [ ] `dream()` guide mentions tags increase graph connectivity
- [ ] All 353+ existing tests pass

---

## 14. Agent Simulation Findings

Scenarios evaluated to stress-test the design from the agent's perspective.

### Scenario 1: Single-session batch ingestion (common)

Agent learns 10 blocks in one session, all `category="knowledge"`, no tags. Temporal=1.0, cat=1.0 for all pairs. Non-cosine floor = 0.25.

**Without guard:** cosine=0.27 qualifies → spurious edges. Agent asks about Python; graph expansion follows an edge to SQL block (cosine=0.28) and injects SQL content into Python context. Silent quality degradation.

**With guard:** cosine must be ≥ 0.30. Blocks below are structurally unrelated — no edge. ✓

### Scenario 2: Tags-as-primary-signal (recommended pattern)

Agent tags blocks with domain namespaces: `["async", "python"]` and `["async", "database"]`. cosine≈0.45 (different domains). Jaccard=1/3≈0.33. Score = 0.45×0.55 + 0.33×0.20 + 0.15 + 0.10 = 0.577. Edge forms ✓ — correct, these ARE experientially related for the async-programming domain.

**Key insight:** Tags are the agent's primary tool for augmenting graph connectivity beyond pure semantics. The `dream()` guide should make this explicit.

### Scenario 3: Zero-tag agent (graceful degradation)

Agent never assigns tags. `jaccard=0.0` always. System degrades gracefully:
- Old blocks vs new (temporal≈0): needs cosine≥0.45 (same cat) or cosine≥0.65 (diff cat)
- Same session (temporal=1.0): cosine≥0.30 (guard floor) + cat boost possible
- This is strictly better than old cosine-only threshold=0.60: more connections in-session, fewer spurious cross-domain connections at rest.

### Scenario 4: Cross-category semantic pair

"User prefers dark mode" (preference) + "Use dark color scheme in UI" (knowledge). cosine=0.60, different categories. Score = 0.33 + 0 + 0.30×0.15 + temporal. With temporal=1.0: 0.33 + 0.045 + 0.10 = 0.475 ≥ 0.40. Edge forms ✓.

**Cross-category pairs need cosine ≥ ~0.47 (same session) or ~0.64 (different sessions).** This is correct — cross-category edges require stronger semantic overlap when temporal context is absent.

### Scenario 5: Long-running agent (temporal decay)

Agent has 100 active hours. Old block (last_reinforced_at=10.0) vs new block (current=100.0). Δh=90, sigma=8 → temporal=exp(-90²/128)≈0. Score = cosine×0.55 + tag×0.20 + cat×0.15. Old and new blocks need cosine≥0.45 (same cat, no tags) or tags to compensate. Old knowledge is structurally harder to connect to new knowledge — intentional.

### Scenario 6: Large batch (50 blocks, same session)

1,225 candidate pairs. Without guard: same-session floor allows cosine=0.27 × 1,225 pairs = large spurious graph. Degree cap (5) limits per-block edges but doesn't prevent junk. With guard: only semantically adjacent pairs (cosine≥0.30) enter scoring. Degree cap then selects the top 5 by composite quality. Graph remains navigable.

### Scenario 7: Backward compatibility (existing DB)

Existing edges in DB have `weight = raw_cosine` (0.60–0.85). New edges after upgrade have `weight = composite` (0.40–0.95). Mixed graph. `prune_weak_edges(threshold=0.10)` unaffected — all existing edges are far above 0.10. Graph expansion orders by weight; old high-cosine edges (e.g., 0.72) appear slightly more relevant than equivalent new composite edges (e.g., 0.58). Self-heals as active blocks gain new composite edges over time. No migration needed.

### What the agent sees (API perspective)

The agent experiences zero API changes. `remember()` returns the same result. `dream()` returns the same `DreamResult`. The only observable changes:
1. More edges created for semantically adjacent blocks that share tags or session context
2. Fewer spurious edges between genuinely unrelated blocks from the same session
3. Edge weights in raw DB are composite scores (not visible via public API)
4. `guide("dream")` now mentions tags affect connectivity

The improvement is entirely silent and beneficial — no breaking changes, no new parameters to learn.

---

## 16. Out of Scope

| Feature | Reason |
|---------|--------|
| Co-retrieval edge creation (C1) | Separate plan. Staging counter in `MemorySystem`. |
| Edge temporal decay at `curate()` (C2) | Separate plan. `last_active_hours` schema ready. |
| LLM batch classification (D1) | Separate plan. One LLM call per `dream()` cycle. |
| `prune_weak_edges()` threshold | Unchanged. `edge_prune_threshold = 0.10` applies to stored composite weight. |
| `find_displaceable_edge()` | Unchanged. Eviction order by relation type is unaffected. |
