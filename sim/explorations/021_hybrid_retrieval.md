# Title: Hybrid Retrieval Flow

## Status: complete

## Question

The retrieval flow described so far (explorations 015, 017) scored all active blocks
and returned the top-K. A richer approach layers the operation into four stages:

1. **Pre-filter** — reduce the candidate pool by category and time window
2. **Vector similarity** — run against the filtered set only
3. **Graph expansion** — pull in structurally connected nodes the vector search missed
4. **Composite scoring** — rank all candidates using the frame's formula

This means embeddings are never compared against the full corpus — the pre-filter
reduces the search space before any similarity computation occurs.

How does this change the recall() design? When does the pre-filter claim hold?
How does graph expansion interact with pre-filtering? What are the failure modes?

---

## The Previous Model and Its Limitation

Exploration 017's recall() query plan loaded all active blocks and their embeddings,
ran brute-force cosine similarity in Python, then scored everything. This is
correct and fast at Phase 1 (50 blocks, trivial). But it has a structural property
worth naming: **every query touches every block.**

At Phase 2 (5,000 blocks), loading all embeddings is 30MB per recall() call.
At Phase 3 (50,000 blocks), it's 300MB. More importantly, a query about Python
asyncio has no reason to compare against blocks about cooking, project management,
or medical terminology — but the old model scores all of them anyway.

The hybrid approach is the principled fix: constrain the similarity search to
plausibly relevant blocks before computing a single cosine similarity.

---

## The Four Stages

### Stage 1: Pre-filter

Reduce the active block pool using fast, index-backed SQL filters.

**Filter A — Status (always applied)**
```sql
WHERE status = 'active'
```
Already assumed everywhere. Not new.

**Filter B — Tag/category (from frame definition)**
```sql
AND (tag LIKE 'knowledge/%' OR tag LIKE 'self/style')
```
Applies only when the frame has `filter_tags` or `filter_category` set
(from exploration 016). Default ATTENTION frame has no tag filter — all active
blocks remain candidates. Custom frames with domain-specific filters benefit most.

**Filter C — Time window (new)**
```sql
AND hours_since_reinforcement < :search_window_hours
```
Excludes blocks that haven't been active recently. This is the most impactful
filter for reducing search space at scale. A configurable `search_window_hours`
in `system_config` controls the threshold.

Default: `search_window_hours = 200` active hours.

Choosing 200: at `λ_standard = 0.01`, a block at 200 hours has
`decay_weight = e^(-0.01 × 200) = 0.135` — still above the prune threshold
(0.05) but decayed enough that it's unlikely to win in composite scoring anyway.
Excluding it from vector search costs almost nothing in recall quality while
saving significant embedding load at scale.

**The candidate pool after pre-filtering:**
```python
candidates = conn.execute(
    select(blocks.c.id, blocks.c.embedding, blocks.c.confidence, ...)
    .join(block_tags, ...)   # if filter_tags set
    .where(
        and_(
            blocks.c.status == "active",
            blocks.c.hours_since_reinforcement < search_window_hours,
            # tag/category filter if applicable
        )
    )
).mappings().all()
```

One SQL query. Index-backed on `status` and `hours_since_reinforcement`.
Returns only the blocks (and their embeddings) that the similarity search
needs to examine.

---

### Stage 2: Vector Similarity

Run cosine similarity against the pre-filtered candidate set only.

```python
query_vec = embed(query)
candidate_vecs = np.vstack([blob_to_embedding(c["embedding"]) for c in candidates])
similarities = cosine_similarity(query_vec, candidate_vecs)

# Take top-N for graph expansion seeding (N >> k, typically k×4)
# Not yet applying composite score — similarity only at this stage
seed_indices = np.argsort(similarities)[::-1][:N_seeds]
seed_blocks = [candidates[i] for i in seed_indices]
```

`N_seeds` is larger than the final `top_k`. It needs to be large enough that
the composite scoring stage (which considers all five components, not just
similarity) can reorder the seeds and still produce the right final top-K.
A reasonable default: `N_seeds = top_k × 4` (20 seeds for top-5 final).

---

### Stage 3: Graph Expansion

Retrieve the 1-hop graph neighbours of the seed blocks that were NOT already
in the candidate pool (either because they failed the pre-filter or had lower
similarity scores than the N_seeds cutoff).

```python
seed_ids = {b["id"] for b in seed_blocks}
all_candidate_ids = {c["id"] for c in candidates}

# Get direct neighbours of seeds
neighbour_rows = conn.execute(
    select(edges.c.from_id, edges.c.to_id, edges.c.weight)
    .where(
        or_(
            edges.c.from_id.in_(seed_ids),
            edges.c.to_id.in_(seed_ids),
        )
    )
).mappings().all()

# Collect neighbour IDs not already in the candidate pool
expansion_ids = set()
for row in neighbour_rows:
    neighbour_id = row["to_id"] if row["from_id"] in seed_ids else row["from_id"]
    if neighbour_id not in all_candidate_ids:
        expansion_ids.add(neighbour_id)

# Load scoring fields for expansion blocks (no embedding needed — only composite score)
expansion_blocks = conn.execute(
    select(blocks.c.id, blocks.c.confidence, blocks.c.reinforcement_count, ...)
    .where(blocks.c.id.in_(expansion_ids))
).mappings().all()
```

**Why expansion blocks don't need their embedding loaded:**
Expansion blocks were not found by vector similarity — their similarity to the
query is unknown and not needed. They enter the composite scoring stage with
`similarity = 0` for their scoring component. Their centrality, confidence, and
reinforcement scores determine if they make the final top-K.

This is deliberate: graph expansion adds blocks that are *structurally* related
(connected to semantically relevant blocks) even if not semantically relevant
themselves. Their rank is determined by everything except similarity.

---

### Stage 4: Composite Scoring

Score ALL candidates: similarity_seeds + expansion_blocks.

```python
all_candidates = seed_blocks + expansion_blocks

for block in all_candidates:
    centrality = get_weighted_degree(conn, block["id"])

    # similarity is known for seed_blocks, 0.0 for expansion_blocks
    sim = similarities[seed_indices.index(block["id"])] if block in seed_blocks else 0.0

    decay_weight = exp(-block["decay_lambda"] * block["hours_since_reinforcement"])
    recency_score = decay_weight
    reinforcement_score = log(1 + block["reinforcement_count"]) / log(1 + max_count)

    score = (
        frame.weights["recency"]       * recency_score +
        frame.weights["centrality"]    * centralityNorm +
        frame.weights["confidence"]    * block["confidence"] +
        frame.weights["similarity"]    * sim +
        frame.weights["reinforcement"] * reinforcement_score
    )

# Sort all_candidates by score descending
# Check contradictions
# Return top_k
```

An expansion block with high confidence (0.90), high reinforcement (20 recalls),
and decent centrality can outrank a seed block with high similarity (0.80) but
low confidence (0.40) and zero reinforcement. The composite formula determines
the winner — not which pipeline stage found the block.

---

## The 80–95% Reduction Claim: When Does It Hold?

The claim states the pre-filter reduces the search space by 80–95% before
any embedding comparison. This depends on what filters are active.

### Phase 1 (50 blocks)

At 50 blocks with a 200-hour time window and most blocks recently active:
- Status filter: removes 0–5 forgotten/superseded blocks → ~50 remain
- Time window: most blocks were learned recently → ~45–50 remain
- Tag/category: only if the frame has a filter set → depends on frame

**Actual reduction: 0–20%.** The claim does not hold at Phase 1 scale. This is
expected and acceptable — at 50 blocks, brute-force over all blocks is instant.
The hybrid architecture is not wrong at Phase 1; it simply provides no performance
benefit.

### Phase 2 (5,000 blocks, diverse domain)

An agent with 5,000 blocks across many domains. Query about Python asyncio.
- Status filter: ~4,800 active blocks remain
- Time window (200 hours): ~500 blocks were used in the last 200 active hours
- Tag filter (knowledge/technical): ~2,000 of 4,800 are technical

Combined: time window narrows to ~500 → **~90% reduction before vector search.**

But: the ATTENTION frame has no tag filter by default. With only the time window:
500 of 4,800 = 90% reduction. The claim holds for the time window alone.

### Phase 3 (50,000 blocks)

At scale with a long history, the time window effect is even stronger:
- Only blocks used in the last 200 active hours (a small fraction of total history)
- 2,000–5,000 of 50,000 active blocks → 90–96% reduction

**The claim is realistic for Phase 2+ with the time window filter.** It does not
hold at Phase 1 scale (not needed there). Category/tag filters amplify the reduction
when the frame has them set.

---

## How Pre-filter and Graph Expansion Complement Each Other

These two stages are designed to correct each other's failures:

**Pre-filter failure mode:** Excludes an old but genuinely relevant block that
hasn't been reinforced recently. Example: a block about database locking written
100 active hours ago, slightly outside the 200-hour window but very relevant to
the current query.

**Graph expansion correction:** If that excluded block has a strong edge to a
block that IS in the candidate pool (a newer block about database connections,
for example), graph expansion pulls it in as a neighbour. The old block competes
on composite score with `similarity = 0` but potentially high confidence and
centrality.

**Graph expansion failure mode:** An expansion block enters with `similarity = 0`.
If the frame has a high similarity weight (like ATTENTION: 0.35), this strongly
disadvantages expansion blocks. They'll only win if their other components
(confidence, reinforcement, centrality) are strong enough to overcome the 0.35
similarity handicap.

**Pre-filter assistance:** The time window already weeds out truly dormant blocks
before graph expansion needs to act. Expansion blocks are only added when a seed
block has a direct edge to them — suggesting they were at some point learned as
related concepts.

The two stages are not independent: **the aggressiveness of the pre-filter
determines how much the graph expansion has to correct.**

---

## Special Cases

### SELF frame (no query)

The SELF frame doesn't use vector similarity. The hybrid pipeline reduces to:

```
1. Pre-filter:  blocks WHERE tag LIKE 'self/%' AND status = 'active'
2. Vector:      SKIPPED (no query)
3. Graph expand: SKIPPED (no seeds)
4. Score:       composite scoring on all self-tagged blocks (similarity = 0)
5. Return top-K
```

No change from the existing SELF frame behaviour. The hybrid pipeline is a
no-op for queryless frames.

### Very small candidate pool after pre-filtering

If the pre-filter leaves fewer than `N_seeds` blocks (e.g., only 3 blocks match
the filters but N_seeds = 20):
- Run vector similarity on all 3
- Graph expansion adds their neighbours regardless of pre-filter
- Score whatever is available
- Return however many blocks meet minimum quality (up to top-K)

The system degrades gracefully. A pre-filter that's too aggressive will result
in a thin candidate pool and heavy reliance on graph expansion.

### Frame with `guarantee_tags` (from exploration 016)

Guaranteed blocks are added after Stage 4 scoring, not before Stage 1 pre-filtering.
They bypass the entire pipeline and are always included up to their token budget.
The pipeline fills the remaining budget from the composite-scored candidates.

---

## Updated recall() Query Plan

Replacing the query plan from exploration 017:

```
Input: frame="attention", query="concurrent database writes", top_k=5

─── Stage 1: Pre-filter ─────────────────────────────────────────────────────
SELECT b.id, b.embedding, b.confidence, b.reinforcement_count,
       b.decay_lambda, b.hours_since_reinforcement, b.self_alignment
FROM blocks b
WHERE b.status = 'active'
AND   b.hours_since_reinforcement < 200        -- time window
-- (no tag filter for default attention frame)
→ Phase 1: ~50 rows   Phase 2: ~500 rows   Phase 3: ~2,000 rows

─── Stage 2: Vector similarity ──────────────────────────────────────────────
In Python:
  query_vec = embed("concurrent database writes")   -- 1 external call
  candidate_vecs = stack(candidates.embeddings)
  similarities = cosine_similarity(query_vec, candidate_vecs)
  seed_indices = top_20_by_similarity                -- N_seeds = top_k × 4
→ O(N_candidates) cosine computation — much smaller N than full corpus

─── Stage 3: Graph expansion ────────────────────────────────────────────────
SELECT from_id, to_id, weight
FROM edges
WHERE from_id IN (seed_ids) OR to_id IN (seed_ids)
→ typically 20 seeds × avg 5 edges = ~100 rows

-- collect unique neighbour IDs not already in candidate pool
-- load scoring fields for expansion blocks (no embedding)
SELECT id, confidence, reinforcement_count, decay_lambda, hours_since_reinforcement
FROM blocks WHERE id IN (expansion_ids)
→ typically 20–60 additional blocks

─── Stage 4: Composite scoring ──────────────────────────────────────────────
In Python:
  score all seed_blocks (similarity known) + expansion_blocks (similarity=0)
  per-block centrality: SELECT SUM(weight) FROM edges WHERE from_id=? OR to_id=?
  → typically 5 centrality queries (just for top candidates)

contradiction check:
SELECT block_a_id, block_b_id FROM contradictions
WHERE (block_a_id IN all_scored_ids OR block_b_id IN all_scored_ids)
AND resolved = 0

sort, take top-5

─── Stage 5: Reinforce ──────────────────────────────────────────────────────
BEGIN TRANSACTION;
UPDATE blocks SET reinforcement_count = reinforcement_count + 1,
                  hours_since_reinforcement = 0 WHERE id IN (top_5_ids);
UPDATE edges SET reinforcement_count = reinforcement_count + 1,
                  hours_since_co_retrieval = 0 WHERE (co-retrieved pairs);
COMMIT;

─── Summary ─────────────────────────────────────────────────────────────────
DB queries:  3 SELECT + 1 UPDATE transaction
             (vs 8 in the exploration 017 query plan, but more targeted)
Python ops:  cosine similarity (smaller N) + scoring + sorting
Key change:  embedding load is proportional to recent activity, not total corpus
```

---

## What Changes From Previous Explorations

| Exploration | Previous | Updated |
|-------------|----------|---------|
| **015** recall() | Scores all active blocks | Scores pre-filtered candidates + graph expansion only |
| **017** recall() query plan | Load all active embeddings | Load only time-windowed embeddings |
| **020** multi-hop | Deferred to Phase 2 | 1-hop graph expansion now part of Phase 1 pipeline (bounded and practical) |

**The graph expansion in this exploration is 1-hop only.** It is bounded: at most
`N_seeds × degree_cap = 20 × 10 = 200` additional candidates. This is the version
of "multi-hop" that is practical at Phase 1 scale. Exploration 020's deferral
referred to 2+ hop traversal for multi-step reasoning paths — that remains Phase 2+.

---

## Configuration Parameters

New `system_config` entries needed:

| Key | Default | Description |
|-----|---------|-------------|
| `search_window_hours` | 200 | Time window for pre-filter; blocks older than this excluded from vector search |
| `vector_search_n_seeds` | `top_k × 4` | Number of top-similarity results to use as graph expansion seeds |
| `graph_expansion_enabled` | `true` | Toggle graph expansion step (disable for debugging) |

---

## Design Decisions from this Exploration

| Decision | Rationale |
|----------|-----------|
| Four-stage pipeline: pre-filter → vector → graph expand → composite score | Each stage corrects the previous stage's failures; layered approach improves efficiency and coverage |
| Pre-filter uses time window as primary reduction mechanism | Category/tag filters only apply when frame has them set; time window always applies |
| Time window default: 200 active hours | Aligns with decay profile; blocks at 200h have decay_weight ~0.135 — unlikely to win composite scoring anyway |
| N_seeds = top_k × 4 for expansion seeding | Composite scoring can reorder seeds; need enough seeds that the right blocks survive reordering |
| Expansion blocks enter with similarity = 0 | They were not found by vector search; their other scoring components determine if they win |
| 1-hop expansion only (Phase 1) | Bounded at N_seeds × degree_cap; practical at Phase 1 scale without explosion |
| Expansion blocks do NOT need their embedding loaded | No similarity computation needed for expansion blocks; saves significant I/O at Phase 2+ scale |
| Guaranteed blocks bypass the pipeline entirely | Guaranteed blocks are always included; they don't compete via retrieval |
| SELF frame (no query) skips stages 2 and 3 | No vector similarity possible; no seeds for graph expansion; direct composite scoring |
| Pre-filter and graph expansion are complementary corrections | Pre-filter may exclude old relevant blocks; graph expansion recovers them via edge connections |
| `search_window_hours` is configurable in system_config | Tunable without code change; aggressive for large corpora, permissive for small |

---

## Open Questions

- [ ] Should expansion blocks get a small similarity bonus for being graph-adjacent
      to high-similarity seeds, rather than a hard zero? (e.g., `similarity_bonus =
      avg(seed_similarities) × edge_weight_to_seed × 0.5`)
- [ ] What is the right `search_window_hours` for Phase 1 (50 blocks, all recent)?
      At 50 blocks, setting 200 hours may not meaningfully filter anything. Should
      Phase 1 skip the time window filter entirely and activate it at Phase 2?
- [ ] Should the graph expansion use edge weight as a gating condition?
      (e.g., only expand via edges with weight > 0.50 to avoid pulling in weakly
      connected and probably irrelevant blocks)
- [ ] How does graph expansion interact with `guarantee_tags`? Can a guaranteed block
      also appear as an expansion candidate, or does its guarantee supersede?
- [ ] Should the number of expansion blocks be capped independently of N_seeds × degree_cap?
      (e.g., hard cap at 50 expansion blocks regardless of graph size)

---

## Variations

- [ ] Trace the hybrid pipeline for a SELF frame with a query (targeted self-reflection)
      — does graph expansion make sense when the filter is already self/* tags only?
- [ ] Compare hybrid retrieval vs. score-all for 50 blocks — show a case where graph
      expansion recovers a block that pre-filtering excluded, and a case where it doesn't.
- [ ] What is the right `N_seeds` multiplier? With `top_k=5` and `N_seeds=20`, how often
      does composite scoring reorder the top-5 vs. similarity ordering? If rarely, N_seeds
      can be reduced to top_k × 2.
