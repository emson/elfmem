# Playground: Context Frames — SELF, ATTENTION, TASK Assembly

## Status: Draft

## Subsystem Specification

Context frames are the primary public interface for retrieving memory as
structured, renderable text for LLM injection. Each frame has a distinct
scoring profile, filter, cache policy, and template.

Three built-in frames (explorations 015, 016, 024):

| Frame | Weights | Cached | Query | Guarantees |
|-------|---------|--------|-------|------------|
| SELF | conf=0.30, reinf=0.30, cent=0.25, rec=0.05, sim=0.10 | Yes — per session | No | `self/constitutional` always included |
| ATTENTION | sim=0.35, rec=0.25, conf=0.15, cent=0.15, reinf=0.10 | No | Yes | None |
| TASK | all=0.20 | No | Yes | `self/goal` always included |

### Frame API

```python
result: FrameResult = await system.frame("self")
result: FrameResult = await system.frame("attention", query="...")
result: FrameResult = await system.frame("task", query="...")

result.text    # str  — rendered for LLM injection
result.blocks  # List[ScoredBlock] — for inspection
```

### Pipeline for ATTENTION and TASK

```
embed(query) → pre-filter → vector search (N_seeds) → graph expand
             → composite score → guarantee_tags pre-allocated
             → contradiction suppression → top-K → render
```

### Pipeline for SELF (no query)

```
tag-filter (self/*) → composite score (similarity=0, renormalized weights)
→ guarantee_tags (self/constitutional) pre-allocated
→ render (instruction-style template)
```

### FrameResult Invariants

1. `.text` is always a non-empty string
2. `.blocks` has at most `top_k` items
3. All `.blocks` have `status=active`
4. No contradicting block pair appears together in `.blocks`
5. `guarantee_tags` blocks always appear, regardless of score (within token budget)
6. Token budget is enforced: lowest-score blocks cut when total exceeds budget

---

## Parameters

```yaml
top_k: 5                # blocks per frame (default)
token_budget: 600       # approximate character budget for rendered text
N_seeds_multiplier: 4   # N_seeds = top_k × 4 for vector search
contradiction_oversample: 2   # sample top_k × 2 before suppression

SELF_cache_ttl_sessions: 1    # cached for 1 session; invalidated on SELF change
ATTENTION_cache_ttl: null     # never cached — query-specific
TASK_cache_ttl: null          # never cached — query-specific
```

---

## Test Suite

### TC-F-001: SELF Frame Always Includes Constitutional Blocks

**Purpose:** `guarantee_tags=["self/constitutional"]` on the SELF frame means
constitutional blocks appear regardless of score. Even a constitutional block
with low score must be in the results.

**Given:**
```yaml
blocks:
  B_const:
    tags: ["self/constitutional"]
    confidence: 0.60
    reinforcement: 0.20
    centrality: 0.10
    recency: 0.95
    # composite SELF score ≈ 0.30 × 0.60 + 0.30 × 0.20 + 0.25 × 0.10 + 0.05 × 0.95
    # ≈ 0.180 + 0.060 + 0.025 + 0.048 = 0.313   ← low

  B_high:
    tags: ["self/value"]
    confidence: 0.95
    reinforcement: 0.90
    centrality: 0.80
    recency: 0.90
    # composite SELF score ≈ 0.30 × 0.95 + 0.30 × 0.90 + 0.25 × 0.80 + 0.05 × 0.90
    # ≈ 0.285 + 0.270 + 0.200 + 0.045 = 0.800   ← high

top_k: 2
```

**When:** `await system.frame("self")`

**Then:**
- Both B_const and B_high appear in `.blocks` (top_k=2)
- B_const included via guarantee, even though its score (0.313) < B_high (0.800)
- If top_k were 1, B_const still appears (guarantee pre-allocated before scored candidates)

**Expected:** B_const always in results regardless of score
**Status:** NOT YET RUN

---

### TC-F-002: SELF Frame Cached — Second Call Returns Same Blocks

**Purpose:** SELF frame result is cached for the current session. A second call
without any SELF-relevant changes returns identical blocks without re-querying.

**Given:**
```yaml
active_self_blocks: [B1, B2, B3, B4, B5]
```

**When:**
```python
result1 = await system.frame("self")
result2 = await system.frame("self")
```

**Then:**
- `result1.blocks == result2.blocks` (same objects, same order)
- Database not re-queried on second call (query count unchanged)

**Expected:** Identical results; no re-query on cache hit
**Status:** NOT YET RUN

---

### TC-F-003: SELF Cache Invalidated When Constitutional Block Amended

**Purpose:** The SELF cache must be invalidated when constitutional blocks change —
the agent's core identity has shifted.

**Given:** SELF frame cached from previous call

**When:** A constitutional block is amended (updated) via the formal amendment process

**Then:**
- Cache invalidated
- Next call to `frame("self")` re-queries and returns updated blocks

**Expected:** Fresh results after constitutional amendment
**Status:** NOT YET RUN

---

### TC-F-004: ATTENTION Frame Ranks Query-Relevant Block Correctly

**Purpose:** ATTENTION frame's `similarity=0.35` weight must make a query-relevant
block rank above a high-confidence, low-similarity identity block.

**Given:**
```yaml
query: "celery background tasks"

block_A:   # identity block, not query-relevant
  similarity:    0.12   # low relevance to query
  confidence:    0.95
  recency:       0.90
  centrality:    0.75
  reinforcement: 0.80
  # ATTENTION score = 0.35×0.12 + 0.15×0.95 + 0.25×0.90 + 0.15×0.75 + 0.10×0.80
  #                = 0.042 + 0.143 + 0.225 + 0.113 + 0.080 = 0.603

block_B:   # knowledge block, highly query-relevant
  similarity:    0.88   # high relevance to query
  confidence:    0.65
  recency:       0.70
  centrality:    0.35
  reinforcement: 0.30
  # ATTENTION score = 0.35×0.88 + 0.15×0.65 + 0.25×0.70 + 0.15×0.35 + 0.10×0.30
  #                = 0.308 + 0.098 + 0.175 + 0.053 + 0.030 = 0.664
```

**When:** `await system.frame("attention", query="celery background tasks")`

**Then:** `score(B) = 0.664 > score(A) = 0.603` — query-relevant block B ranks above A

**Expected:** B ranked above A; query-relevance wins
**Tolerance:** ±0.001
**Status:** NOT YET RUN

---

### TC-F-005: TASK Frame Guarantees self/goal Blocks

**Purpose:** TASK frame `guarantee_tags=["self/goal"]` ensures active goal blocks
always appear, regardless of score relative to other blocks.

**Given:**
```yaml
blocks:
  B_goal:
    tags: ["self/goal"]
    # low TASK score (recently added, low centrality, medium confidence)
    # composite TASK score ≈ 0.38

  B_knowledge:
    # no self/goal tag; high composite score
    # composite TASK score ≈ 0.78

top_k: 3
```

**When:** `await system.frame("task", query="write unit tests")`

**Then:** B_goal appears in results (guaranteed), even with score 0.38 < B_knowledge 0.78

**Expected:** B_goal always in TASK frame results
**Status:** NOT YET RUN

---

### TC-F-006: Token Budget Enforced — Lowest-Score Block Cut When Over Budget

**Purpose:** When the rendered text of top-K blocks exceeds `token_budget`,
the lowest-scoring block is cut until the budget is satisfied.

**Given:**
```yaml
top_k: 5
token_budget: 300   # tight budget

blocks: [B1(score=0.80, tokens=80), B2(score=0.75, tokens=80),
         B3(score=0.70, tokens=80), B4(score=0.60, tokens=80),
         B5(score=0.50, tokens=80)]

# Total: 5 × 80 = 400 tokens → exceeds budget of 300
# Must cut B5 (lowest score), then B4 → 3 blocks × 80 = 240 ≤ 300 ✓
```

**When:** Token budget enforcement in `frame()`

**Then:**
- 3 blocks returned (B1, B2, B3)
- B4 and B5 cut (lowest scores)
- `.text` length ≤ 300 tokens (approximately)

**Expected:** 3 blocks; highest-score blocks always retained
**Status:** NOT YET RUN

---

### TC-F-007: Guarantee Tags Pre-Allocated Before Token Budget Cuts

**Purpose:** Guaranteed blocks (constitutional, goal) are allocated their token
budget first. Token budget cuts never remove guaranteed blocks.

**Given:**
```yaml
token_budget: 200

B_const: tags=["self/constitutional"], tokens=80   # guaranteed
B_high:  score=0.90, tokens=80                     # high scorer
B_mid:   score=0.70, tokens=80                     # medium scorer

# Total = 240 tokens > 200 budget
# B_mid should be cut (lowest score among non-guaranteed)
# B_const must NOT be cut (guaranteed)
```

**When:** Token budget enforcement with guaranteed block present

**Then:**
- B_const retained (guaranteed)
- B_high retained (highest score)
- B_mid cut (lowest score among non-guaranteed)

**Expected:** Guaranteed blocks always survive budget cuts
**Status:** NOT YET RUN

---

### TC-F-008: Contradiction Suppression — One of Contradicting Pair Removed

**Purpose:** Two blocks with an active `contradictions` record should not both
appear in the same frame. The lower-confidence block is suppressed.

**Given:**
```yaml
B_old:
  confidence: 0.60
  score: 0.75
  contradicts: B_new

B_new:
  confidence: 0.85   # higher confidence
  score: 0.70        # slightly lower score

# Without suppression: both would be in top-5
# With suppression: B_old removed (lower confidence), B_new retained
```

**When:** `frame("attention", query="...")` — both B_old and B_new in top-5 candidates

**Then:**
- B_new returned (higher confidence)
- B_old suppressed
- A replacement candidate (rank 6) fills the gap

**Expected:** B_old absent; B_new present; top-K still has 5 blocks
**Status:** NOT YET RUN

---

### TC-F-009: Queryless ATTENTION Returns Most Salient Blocks

**Purpose:** `frame("attention")` without a query drops similarity, renormalises
weights, and returns most salient blocks by recency + confidence + centrality + reinforcement.

**Given:**
```yaml
# No query — similarity component dropped and weights renormalized:
# Original: sim=0.35, conf=0.15, rec=0.25, cent=0.15, reinf=0.10
# Remaining sum = 0.65; renormalized:
# conf=0.231, rec=0.385, cent=0.231, reinf=0.154

block_X:
  recency: 0.90   # recently reinforced
  confidence: 0.80
  centrality: 0.60
  reinforcement: 0.70

block_Y:
  recency: 0.20   # stale
  confidence: 0.75
  centrality: 0.55
  reinforcement: 0.65
```

**When:** `await system.frame("attention")` (no query)

**Then:**
```
score_X = 0.231×0.80 + 0.385×0.90 + 0.231×0.60 + 0.154×0.70
        = 0.185 + 0.347 + 0.139 + 0.108 = 0.779

score_Y = 0.231×0.75 + 0.385×0.20 + 0.231×0.55 + 0.154×0.65
        = 0.173 + 0.077 + 0.127 + 0.100 = 0.477
```

`score_X (0.779) > score_Y (0.477)` — recently-reinforced block wins

**Expected:** X ranked above Y; no embedding call made (no query)
**Tolerance:** ±0.001
**Status:** NOT YET RUN

---

### TC-F-010: Custom Registered Frame Uses Its Own Weights

**Purpose:** A user-registered frame with custom weights produces rankings that
reflect those weights, not any built-in frame's weights.

**Given:**
```python
system.register_frame("code_review", FrameDefinition(
    weights=ScoringWeights(
        similarity=0.40, confidence=0.40,
        recency=0.10, centrality=0.05, reinforcement=0.05
    )
))
```

```yaml
block_A: similarity=0.80, confidence=0.80, recency=0.20, centrality=0.20, reinf=0.20
block_B: similarity=0.40, confidence=0.40, recency=0.90, centrality=0.85, reinf=0.80
```

**When:** `await system.frame("code_review", query="...")`

**Then:**
```
score_A = 0.40×0.80 + 0.40×0.80 + 0.10×0.20 + 0.05×0.20 + 0.05×0.20
        = 0.320 + 0.320 + 0.020 + 0.010 + 0.010 = 0.680

score_B = 0.40×0.40 + 0.40×0.40 + 0.10×0.90 + 0.05×0.85 + 0.05×0.80
        = 0.160 + 0.160 + 0.090 + 0.043 + 0.040 = 0.493
```

`score_A (0.680) > score_B (0.493)` — custom frame correctly prioritises sim + conf

**Expected:** A ranked above B with custom weights
**Tolerance:** ±0.001
**Status:** NOT YET RUN

---

## Parameter Tuning

### PT-1: top_k (currently 5)

**Question:** Is 5 blocks per frame the right default? Too few → thin context.
Too many → prompt bloat; LLM attention diluted.

**Scenario:** Measure prompt quality at top_k = 3, 5, 8 for:
- SELF frame (identity blocks): 3 is probably enough for most agents
- ATTENTION frame (knowledge): 5 is a reasonable default
- TASK frame (goal + context): 3–5 is sufficient

**Recommendation:** Keep 5 as default. Allow per-frame override:
`system.frame("self", top_k=3)`.

---

### PT-2: token_budget (currently 600)

**Question:** Is 600 characters/tokens the right budget per frame?

**Context:** A typical LLM system prompt has 1,000–2,000 tokens available.
Three frames (SELF + ATTENTION + TASK) compete for that space:
- SELF: ~100–200 tokens (identity blocks are short)
- ATTENTION: ~200–400 tokens (knowledge blocks may be longer)
- TASK: ~100–200 tokens (goals are typically short)

**Recommendation:** 600 total per frame is generous. Start at 600; reduce to 400
if prompt length becomes a problem in practice.

---

### PT-3: Contradiction Oversample Factor (currently top_k × 2)

**Question:** Is `top_k × 2` enough candidate headroom so that after suppression,
the final top-K is still `top_k` blocks?

**Worst case:** All top-K candidates are part of contradicting pairs. Need at
least `top_k + n_contradictions` in the oversample to fill the gap.

At `top_k=5`, sampling 10 candidates should cover up to 5 contradictions.
This is an extreme edge case; in practice, 1–2 contradictions are typical.

**Recommendation:** Keep `top_k × 2`. Only revisit if agents with many contradictions
(high knowledge update rate) consistently receive fewer than top_k results.

---

## Open Assertions

1. `frame()` never returns `status=inbox` blocks
2. `frame()` never returns `status=archived` blocks
3. SELF cache key is scoped per session, not per process
4. Multiple calls to `frame("attention", query=same_query)` within a session
   may return different results if new knowledge was consolidated between calls
5. `FrameResult.text` is deterministic for the same `.blocks` and template
6. `frame("self")` with no active self/* blocks still returns a valid (possibly minimal) FrameResult

---

## Python Test Sketch

```python
# elfmem/tests/test_frames.py

import pytest
from elfmem import MemorySystem
from tests.fixtures import MockLLMService, MockEmbeddingService

@pytest.fixture
async def system():
    s = MemorySystem(
        db_path=":memory:",
        llm_service=MockLLMService(),
        embedding_service=MockEmbeddingService(),
    )
    async with s.session():
        yield s

async def test_self_frame_includes_constitutional(system):
    result = await system.frame("self")
    tag_sets = [set(b.tags) for b in result.blocks]
    assert any("self/constitutional" in tags for tags in tag_sets)

async def test_self_frame_cached(system):
    result1 = await system.frame("self")
    result2 = await system.frame("self")
    assert [b.id for b in result1.blocks] == [b.id for b in result2.blocks]

async def test_attention_ranks_by_query_similarity(system):
    # query-relevant block should rank above high-confidence off-topic block
    result = await system.frame("attention", query="celery background tasks")
    # MockEmbeddingService gives deterministic embeddings; set up blocks accordingly
    assert result.blocks[0].similarity >= result.blocks[-1].similarity

async def test_task_guarantees_goal_blocks(system):
    result = await system.frame("task", query="write unit tests")
    goal_blocks = [b for b in result.blocks if "self/goal" in b.tags]
    assert len(goal_blocks) >= 1

async def test_contradiction_suppression(system):
    # After contradiction detection, contradicting pair should not co-appear
    result = await system.frame("attention", query="caching strategy")
    block_ids = [b.id for b in result.blocks]
    # B_old and B_new contradict each other — at most one should appear
    assert not ("B_old" in block_ids and "B_new" in block_ids)

async def test_frame_result_has_text_and_blocks(system):
    result = await system.frame("self")
    assert isinstance(result.text, str)
    assert len(result.text) > 0
    assert isinstance(result.blocks, list)
    assert all(b.status == "active" for b in result.blocks)

async def test_queryless_frame_no_embedding_call(system, mock_embedding):
    await system.frame("attention")   # no query
    assert mock_embedding.embed.call_count == 0
```
