# Performance Optimizations for elfmem

**Status:** PROPOSED
**Date:** 2026-04-28

## Context

elfmem currently targets 50–500 active blocks (Phase 1). At this scale most operations
are fast enough, but three operations have unnecessary overhead that compounds:

- **`dream()`** (consolidation): serial embedding calls dominate wall-clock time
- **`curate()`**: N+1 tag queries and redundant full-table scans
- **`recall()`**: BM25 index rebuilt from scratch on every call; no embedding cache

These optimizations keep the architecture unchanged — same rhythms, same pipeline stages,
same result types. Pure internal speedups with no API surface changes.

### Bottleneck Summary

| Operation | Dominant cost | Typical latency |
|-----------|--------------|-----------------|
| `learn()` | 1 DB insert | ~1ms (already heartbeat speed) |
| `recall()` | 1 embedding API + 6–8 DB queries | 200–500ms |
| `dream()` | N embedding + N LLM calls | 5–30s (N = inbox size) |
| `curate()` | 3× full table scans + N+1 tag queries | 100ms–2s |

---

## Plan: 5 Changes, Ordered by Impact / Risk

### Change 1: N+1 Tag Queries in Curate

**Effort:** 30 min | **Risk:** zero

**Problem:** `_archive_decayed_blocks()` and `_reinforce_top_blocks()` both call
`_get_tags_fast(conn, block_id)` inside a loop — one SQL query per active block.
`get_active_blocks()` is also called 3 times independently in the same curate pass.

**Files:**
- `src/elfmem/operations/curate.py` — lines 180–192 and 234–252

**Fix:**
- In `_archive_decayed_blocks()`: call `get_tags_batch(conn, all_ids)` once before the
  loop (after computing `degrees`). Replace `_get_tags_fast()` at line 182 with
  `tags_map.get(block["id"], [])`.
- In `_reinforce_top_blocks()`: same pattern — `get_tags_batch()` once before the loop,
  dict lookup in the loop body.
- Cache `get_active_blocks()`: call once at the top of `curate()`, pass to both helpers
  as a parameter (currently called at lines 120, 160, 223).
- Remove `_get_tags_fast()` helper (lines 211–214) — no longer needed.

**Impact:** For 200 active blocks: ~400 queries → ~2 queries in curate.

---

### Change 2: Batch Summary Embeddings in Consolidation

**Effort:** 1 hr | **Risk:** low

**Problem:** `consolidate.py:330` calls `embedding_svc.embed()` individually for each
inbox block's summary text inside the loop. That's N API round-trips.

**Files:**
- `src/elfmem/operations/consolidate.py` — lines 270–390 in `_collect_decisions()`

**Fix — two-pass approach:**
1. **First pass (lines 270–347):** Keep the loop but instead of calling `embed()` at
   line 330, store `summary_text` in the `_BlockDecision` dataclass (add
   `summary_text: str | None = None` and `summary_vec: np.ndarray | None = None` fields).
2. **After the loop:** Collect all summary texts from non-rejected decisions, call
   `embedding_svc.embed_batch()` once, assign vectors back to decisions.
3. **In `_apply_decisions()`:** Use the pre-computed summary vectors when writing to DB.

**Dependency note:** The `evolving_vecs` dict at line 378 currently uses the summary
vector immediately. After this change, use the *content* vector (already available from
line 277) for `evolving_vecs` during the loop, then store the batch-computed summary
vector for DB persistence. The content vector is what drives dedup and contradiction
detection — the summary vector is only persisted for future retrieval.

**Impact:** For inbox of 20 blocks: 20 API calls → 1 API call (~2s saved).

---

### Change 3: Graph Expansion Batch Query

**Effort:** 45 min | **Risk:** low

**Problem:** `retrieval.py:280–284` calls `queries.get_block(conn, nid)` in a loop for
each expanded neighbour — one query per neighbour.

**Files:**
- `src/elfmem/db/queries.py` — add new `get_blocks_batch()` function
- `src/elfmem/memory/retrieval.py` — lines 270–285 in `_stage_3_graph_expand()`

**Fix:**
1. Add `get_blocks_batch()` to `queries.py`, following the `get_tags_batch()` pattern:
   ```python
   async def get_blocks_batch(
       conn: AsyncConnection,
       block_ids: list[str],
   ) -> dict[str, dict[str, Any]]:
       if not block_ids:
           return {}
       result = await conn.execute(
           select(blocks).where(blocks.c.id.in_(block_ids))
       )
       return {row["id"]: dict(row) for row in result.mappings()}
   ```
2. Replace the loop in `_stage_3_graph_expand()`:
   ```python
   blocks_map = await queries.get_blocks_batch(conn, new_ids)
   return [b for b in blocks_map.values() if b.get("status") == "active"]
   ```

**Impact:** For 10 expanded neighbours: 10 queries → 1 query.

---

### Change 4: Session-Scoped BM25 Index Cache

**Effort:** 2 hr | **Risk:** medium

**Problem:** `_stage_2b_bm25_search()` rebuilds `BM25Okapi` from scratch on every
`recall()` — tokenizing all candidate block contents each time.

**Files:**
- `src/elfmem/memory/retrieval.py` — lines 197–215
- `src/elfmem/api.py` — wire cache lifecycle to session / consolidation

**Fix:**
1. Create `BM25Cache` class in `retrieval.py`:
   ```python
   class BM25Cache:
       def __init__(self) -> None:
           self._index: BM25Okapi | None = None
           self._block_ids_hash: int | None = None

       def get(self, candidates: list[dict[str, Any]]) -> BM25Okapi | None:
           ids_hash = hash(tuple(b["id"] for b in candidates))
           if ids_hash == self._block_ids_hash and self._index is not None:
               return self._index
           return None

       def set(self, candidates: list[dict[str, Any]], index: BM25Okapi) -> None:
           self._block_ids_hash = hash(tuple(b["id"] for b in candidates))
           self._index = index

       def invalidate(self) -> None:
           self._index = None
           self._block_ids_hash = None
   ```
2. Pass `BM25Cache` into `hybrid_retrieve()` and `_stage_2b_bm25_search()`.
3. Invalidate on `consolidate()` and `curate()` completion (block set changed).
4. `MemorySystem` owns the instance; `begin_session()` creates fresh.

**Impact:** Eliminates O(n) tokenization + index build on every recall after first miss.
For 200 blocks: ~50ms saved per recall.

---

### Change 5: Embedding LRU Cache in Adapter

**Effort:** 1 hr | **Risk:** low

**Problem:** `OpenAIEmbeddingAdapter.embed()` has no caching. The same query string
re-embedded across recalls within a session hits the API every time. The "cache hit"
comment at `consolidate.py:277` is misleading — there's no actual cache in the adapter.

**Files:**
- `src/elfmem/adapters/openai.py` — lines 267–279
- `src/elfmem/adapters/mock.py` — add matching cache for test parity

**Fix:**
1. Add `_cache: dict[str, np.ndarray]` to `OpenAIEmbeddingAdapter.__init__()`.
2. In `embed()`: check cache before API call; store result after.
3. In `embed_batch()`: filter out cached texts, call API only for misses, merge results.
4. Add `clear_cache()` method for session reset.
5. Cap cache size (500 entries) with simple oldest-eviction.
6. Wire `clear_cache()` into `MemorySystem.begin_session()`.
7. Add matching `_cache` to `MockEmbeddingService` for test consistency.

**Impact:** Repeated SELF-frame recalls with same query: ~100ms saved per hit.

---

## Estimated Combined Impact

For a typical instance with ~200 active blocks, inbox threshold of 10:

| Optimization | Before | After | Saving |
|---|---|---|---|
| Batch tags in curate (#1) | ~400 individual queries | 2 batch queries | ~400ms per curate |
| Batch summary embeddings (#2) | 10 API calls × 100ms | 1 API call | ~1s per dream |
| Graph expansion batch (#3) | ~10 individual queries | 1 batch query | ~50ms per recall |
| BM25 cache (#4) | 200 tokenizations per recall | 0 (cached) | ~50ms per recall |
| Embedding cache (#5) | redundant API calls | cache hits | ~100ms per repeated recall |

---

## Out of Scope (Future Work)

- **Parallel LLM calls in consolidation:** `evolving_vecs` mutation chain creates
  cross-iteration dependencies. Requires a two-pass refactor (separate dedup from LLM
  scoring). High reward (~8s saved) but high complexity. Recommend as follow-up.
- **sqlite-vec / vectorized cosine:** Not needed at Phase 1 scale. Revisit at 2000+ blocks.
- **Frame-aware SQL push-down:** Moderate complexity, modest gain.

---

## Verification

1. `uv run pytest tests/ -x -q` — full suite, 0 regressions
2. `uv run --env-file .env elfmem curate` — completes, same results
3. `uv run --env-file .env elfmem recall --frame self "test query"` — results unchanged
4. `uv run --env-file .env elfmem dream` — consolidation results unchanged
5. No CHANGELOG entry needed (pure internal speedups, no user-facing change)

---

## Files Modified

| File | Changes |
|------|---------|
| `src/elfmem/operations/curate.py` | Batch tags, cache active blocks, remove `_get_tags_fast` |
| `src/elfmem/operations/consolidate.py` | Two-pass summary embedding |
| `src/elfmem/db/queries.py` | Add `get_blocks_batch()` |
| `src/elfmem/memory/retrieval.py` | `BM25Cache`, use `get_blocks_batch()` in graph expand |
| `src/elfmem/adapters/openai.py` | Embedding LRU cache |
| `src/elfmem/adapters/mock.py` | Matching cache for tests |
| `src/elfmem/api.py` | Wire BM25Cache + embedding cache lifecycle |
