# Step 6: recall() + frame() — Implementation Plan

## Overview

Build the second vertical slice: the 4-stage hybrid retrieval pipeline and
frame assembly system. This is the read path — converting a query (or no query)
into ranked, rendered context text for LLM injection.

The pipeline: pre-filter → vector search → graph expand → composite score →
contradiction suppression → guarantee enforcement → token budget → render.

**Key design decisions (locked):**
- Retrieval is pure (no side effects) — reinforcement is a separate step at L4
- SELF frame bypasses vector search and graph expansion (no query)
- Graph-expanded blocks get `similarity=0.0` and `was_expanded=True`
- Queryless frames use renormalized weights (similarity weight redistributed)
- Guaranteed blocks always included, surviving token budget cuts
- Contradiction suppression removes lower-confidence block from pairs
- Over-sample `top_k × 2` candidates before suppression

---

## Files to Create/Modify

| File | Action | Purpose |
|------|--------|---------|
| `src/elfmem/memory/graph.py` | Create | Centrality computation, 1-hop expansion, edge reinforcement |
| `src/elfmem/memory/retrieval.py` | Create | 4-stage hybrid retrieval pipeline |
| `src/elfmem/context/__init__.py` | Create | Package init |
| `src/elfmem/context/frames.py` | Create | Frame registry, built-in definitions, cache |
| `src/elfmem/context/rendering.py` | Create | Blocks → rendered text with templates |
| `src/elfmem/context/contradiction.py` | Create | Contradiction suppression logic |
| `src/elfmem/operations/recall.py` | Create | Orchestrates retrieval + reinforcement |
| `src/elfmem/api.py` | Modify | Add frame() and recall() methods |

---

## Module Design

### 1. `src/elfmem/memory/graph.py`

**Purpose:** Graph operations — centrality computation, 1-hop expansion, and
co-retrieval edge reinforcement.

**Imports:**
```python
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncConnection

from elfmem.db import queries
from elfmem.types import Edge
```

**Constants:**
```python
EDGE_REINFORCE_DELTA = 0.10    # weight increase per co-retrieval
EDGE_WEIGHT_CAP = 1.0          # maximum edge weight
```

**Functions:**

```python
async def compute_centrality(
    conn: AsyncConnection,
    block_ids: list[str],
) -> dict[str, float]:
    """Compute normalised weighted-degree centrality for a set of blocks.

    centrality(block) = weighted_degree(block) / max_weighted_degree

    Blocks with no edges have centrality 0.0.
    If all blocks have no edges, all centralities are 0.0.

    Args:
        conn: Database connection.
        block_ids: Block IDs to compute centrality for.

    Returns:
        Dict mapping block_id → centrality in [0.0, 1.0].
    """


async def expand_1hop(
    conn: AsyncConnection,
    seed_ids: list[str],
) -> list[str]:
    """Get 1-hop neighbour block IDs from the graph.

    Returns block IDs connected to any seed via an edge,
    excluding the seeds themselves. Only returns active blocks.

    Args:
        conn: Database connection.
        seed_ids: Seed block IDs from vector search.

    Returns:
        List of neighbour block IDs (deduplicated, active only).
    """


async def reinforce_co_retrieved_edges(
    conn: AsyncConnection,
    block_ids: list[str],
) -> int:
    """Reinforce edges between co-retrieved blocks.

    For each pair of block_ids that share an edge, increment the
    edge weight by EDGE_REINFORCE_DELTA (capped at EDGE_WEIGHT_CAP).

    Args:
        conn: Database connection.
        block_ids: Block IDs that were returned in the same frame() call.

    Returns:
        Count of edges reinforced.
    """
```

**Key implementation notes:**
- `compute_centrality` calls `queries.get_weighted_degree(conn, block_ids)`,
  finds `max_degree`, divides each by max. If `max_degree == 0`, all
  centralities are 0.0.
- `expand_1hop` calls `queries.get_neighbours(conn, seed_ids)`, then
  filters to only active blocks (check status via a query or accept that
  CASCADE deletion means only active neighbours exist if edges are cleaned up).
- `reinforce_co_retrieved_edges` computes all pairs from `block_ids`, checks
  which pairs have existing edges, increments weight. Uses canonical order.
  Cap weight at 1.0.

---

### 2. `src/elfmem/memory/retrieval.py`

**Purpose:** The 4-stage hybrid retrieval pipeline. All stages are pure
(no side effects). Returns scored candidates.

**Imports:**
```python
from __future__ import annotations

import numpy as np
from sqlalchemy.ext.asyncio import AsyncConnection

from elfmem.db import queries
from elfmem.memory.dedup import cosine_similarity
from elfmem.memory.graph import compute_centrality, expand_1hop
from elfmem.ports.services import EmbeddingService
from elfmem.scoring import (
    ScoringWeights,
    compute_recency,
    compute_score,
    log_normalise_reinforcement,
)
from elfmem.types import DecayTier, ScoredBlock
```

**Constants:**
```python
N_SEEDS_MULTIPLIER = 4           # N_seeds = top_k × this
CONTRADICTION_OVERSAMPLE = 2     # sample top_k × this before suppression
DEFAULT_SEARCH_WINDOW_HOURS = 200.0
```

**Function:**

```python
async def hybrid_retrieve(
    conn: AsyncConnection,
    *,
    embedding_svc: EmbeddingService,
    query: str | None,
    weights: ScoringWeights,
    current_active_hours: float,
    top_k: int = 5,
    tag_filter: str | None = None,
    search_window_hours: float = DEFAULT_SEARCH_WINDOW_HOURS,
) -> list[ScoredBlock]:
    """Execute the 4-stage hybrid retrieval pipeline.

    Stage 1 — Pre-filter: SQL WHERE on status=active and search window.
    Stage 2 — Vector search: cosine similarity → top N_seeds. (Skipped if no query.)
    Stage 3 — Graph expand: 1-hop neighbours of seeds. (Skipped if no query.)
    Stage 4 — Composite score: rank all candidates by weighted formula.

    Args:
        conn: Database connection.
        embedding_svc: For embedding the query.
        query: Query text (None for queryless retrieval like SELF frame).
        weights: ScoringWeights to use (may be renormalized if no query).
        current_active_hours: For computing recency.
        top_k: Number of blocks to return.
        tag_filter: Optional SQL LIKE pattern for tag filtering (e.g. "self/%").
        search_window_hours: Pre-filter time window.

    Returns:
        List of top_k ScoredBlock objects, sorted by score descending.
        Over-sampled to top_k × CONTRADICTION_OVERSAMPLE for suppression headroom.
    """
```

**The pipeline should be decomposed into stage functions:**

```python
async def _stage_1_prefilter(
    conn: AsyncConnection,
    *,
    current_active_hours: float,
    search_window_hours: float,
    tag_filter: str | None,
) -> list[dict]:
    """Stage 1: Pre-filter active blocks within search window.

    If tag_filter provided, only include blocks with matching tags.
    Returns block dicts with scoring fields.
    """


async def _stage_2_vector_search(
    embedding_svc: EmbeddingService,
    query: str,
    candidates: list[dict],
    n_seeds: int,
) -> list[tuple[dict, float]]:
    """Stage 2: Embed query, compute cosine similarity, return top N_seeds.

    Returns list of (block_dict, similarity) sorted by similarity descending.
    """


async def _stage_3_graph_expand(
    conn: AsyncConnection,
    seed_ids: list[str],
    existing_candidate_ids: set[str],
) -> list[dict]:
    """Stage 3: 1-hop graph expansion from seeds.

    Returns block dicts for expansion blocks (not already in seeds).
    """


def _stage_4_composite_score(
    candidates: list[tuple[dict, float, bool]],
    *,
    weights: ScoringWeights,
    current_active_hours: float,
    centralities: dict[str, float],
    max_reinforcement_count: int,
    top_k: int,
) -> list[ScoredBlock]:
    """Stage 4: Compute composite score for all candidates.

    Each candidate is (block_dict, similarity, was_expanded).
    Returns top (top_k × CONTRADICTION_OVERSAMPLE) ScoredBlock objects.
    """
```

**Key implementation notes:**

- **Pre-filter (Stage 1):**
  - Uses `queries.get_active_blocks(conn, min_last_reinforced_at=cutoff)` where
    `cutoff = current_active_hours - search_window_hours`
  - If `tag_filter` provided (e.g., "self/%"), additionally filter by
    `queries.get_blocks_by_tag_pattern(conn, tag_filter)` — intersection
  - For SELF frame: no embeddings needed, so use `get_active_blocks` not
    `get_active_blocks_with_embeddings`
  - For ATTENTION/TASK with query: use `get_active_blocks_with_embeddings`

- **Vector search (Stage 2):**
  - `query_vec = await embedding_svc.embed(query)`
  - Compute cosine similarity for each candidate: `cosine_similarity(query_vec, block_embedding)`
  - Sort by similarity descending, take top `n_seeds = top_k * N_SEEDS_MULTIPLIER`
  - If fewer candidates than n_seeds, return all

- **Graph expansion (Stage 3):**
  - `neighbour_ids = await expand_1hop(conn, seed_ids)`
  - Filter out any that are already in the seed set
  - Fetch scoring fields for these blocks (no embeddings needed)
  - These blocks get `similarity=0.0` and `was_expanded=True`

- **Composite scoring (Stage 4):**
  - Compute `hours_since = current_active_hours - block["last_reinforced_at"]`
  - `recency = compute_recency(DecayTier(block["decay_tier_value"]), hours_since)`
    - Note: need to map `decay_lambda` → `DecayTier` or just use the lambda
      directly with `math.exp(-lam * hours_since)`
  - `reinforcement = log_normalise_reinforcement(count, max_count)`
  - `centrality = centralities.get(block_id, 0.0)`
  - `score = compute_score(similarity=sim, confidence=conf, recency=rec, ...)`
  - Sort by score descending
  - Return top `top_k * CONTRADICTION_OVERSAMPLE`

- **For queryless retrieval (SELF frame, queryless ATTENTION):**
  - Skip stages 2 and 3 entirely
  - All candidates get `similarity=0.0`
  - Weights should be renormalized via `weights.renormalized_without_similarity()`
    (caller is responsible for passing the right weights)

---

### 3. `src/elfmem/context/frames.py`

**Purpose:** Frame registry — built-in frame definitions and custom frame
management. Frame definitions are loaded from the database `frames` table.

**Imports:**
```python
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncConnection

from elfmem.db import queries
from elfmem.scoring import (
    ATTENTION_WEIGHTS,
    SELF_WEIGHTS,
    TASK_WEIGHTS,
    ScoringWeights,
)
```

**Types:**

```python
@dataclass(frozen=True)
class CachePolicy:
    ttl_seconds: int = 3600
    invalidate_on: list[str] = field(
        default_factory=lambda: ["self_block_change"]
    )


@dataclass(frozen=True)
class FrameFilters:
    tag_patterns: list[str] | None = None    # SQL LIKE patterns, OR-combined
    categories: list[str] | None = None
    search_window_hours: float = 200.0


@dataclass(frozen=True)
class FrameDefinition:
    name: str
    weights: ScoringWeights
    filters: FrameFilters
    guarantees: list[str]           # tag patterns for guaranteed blocks
    template: str                   # template name
    token_budget: int
    cache: CachePolicy | None       # None = no caching
    source: Literal["builtin", "user"] = "user"
```

**Built-in frame definitions (from exploration 024 §3.2):**

```python
SELF_FRAME = FrameDefinition(
    name="self",
    weights=SELF_WEIGHTS,
    filters=FrameFilters(tag_patterns=["self/%"]),
    guarantees=["self/constitutional"],
    template="self",
    token_budget=600,
    cache=CachePolicy(ttl_seconds=3600, invalidate_on=["self_block_change", "curate_complete"]),
    source="builtin",
)

ATTENTION_FRAME = FrameDefinition(
    name="attention",
    weights=ATTENTION_WEIGHTS,
    filters=FrameFilters(),
    guarantees=[],
    template="attention",
    token_budget=2000,
    cache=None,
    source="builtin",
)

TASK_FRAME = FrameDefinition(
    name="task",
    weights=TASK_WEIGHTS,
    filters=FrameFilters(),
    guarantees=["self/goal"],
    template="task",
    token_budget=800,
    cache=None,
    source="builtin",
)

BUILTIN_FRAMES: dict[str, FrameDefinition] = {
    "self": SELF_FRAME,
    "attention": ATTENTION_FRAME,
    "task": TASK_FRAME,
}
```

**Functions:**

```python
def get_frame_definition(name: str) -> FrameDefinition:
    """Get a frame definition by name.

    Checks built-in frames first, then would check database for custom frames.
    Raises ValueError if frame not found.

    Phase 1: only built-in frames supported. Custom frames deferred.
    """


def frame_definition_from_db_row(row: dict) -> FrameDefinition:
    """Convert a database row (from frames table) to a FrameDefinition.

    Deserialises JSON fields (weights_json, filters_json, etc.)
    """
```

**Frame cache:**

```python
class FrameCache:
    """Simple TTL cache for frame results. Scoped per session.

    Only SELF frame uses caching (cache is not None).
    """

    def __init__(self) -> None:
        self._cache: dict[str, tuple[float, FrameResult]] = {}
        # key → (expires_at_timestamp, result)

    def get(self, frame_name: str) -> FrameResult | None:
        """Get cached result, or None if expired/missing."""

    def set(self, frame_name: str, result: FrameResult, ttl_seconds: int) -> None:
        """Cache a result with TTL."""

    def invalidate(self, frame_name: str) -> None:
        """Invalidate a specific frame's cache."""

    def clear(self) -> None:
        """Clear all cached frames (e.g., on session end)."""
```

---

### 4. `src/elfmem/context/rendering.py`

**Purpose:** Render scored blocks into text for LLM injection. Simple template
system — Phase 1 uses basic string formatting.

**Functions:**

```python
def render_blocks(
    blocks: list[ScoredBlock],
    template: str,
    token_budget: int,
) -> str:
    """Render scored blocks into text using the specified template.

    Templates:
    - "self": instruction-style rendering for identity context
    - "attention": knowledge-style rendering for relevant context
    - "task": goal-style rendering for task context

    Enforces token budget by removing lowest-score blocks until
    the rendered text fits.

    Args:
        blocks: Scored blocks, sorted by score descending.
        template: Template name ("self", "attention", "task").
        token_budget: Approximate character budget.

    Returns:
        Rendered text string.
    """


def _render_self_template(blocks: list[ScoredBlock]) -> str:
    """Render blocks in identity/instruction style.

    Example output:
    ## Identity
    - I always explain my reasoning before giving recommendations.
    - I prefer async patterns in Python when possible.
    """


def _render_attention_template(blocks: list[ScoredBlock]) -> str:
    """Render blocks in knowledge/context style.

    Example output:
    ## Relevant Knowledge
    [1] Use Celery with Redis for background tasks in Django.
    [2] Redis supports pub/sub for real-time communication.
    """


def _render_task_template(blocks: list[ScoredBlock]) -> str:
    """Render blocks in goal/task style.

    Example output:
    ## Active Goals
    - Complete the API documentation
    ## Context
    [1] Use pytest for testing async code.
    """


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: len(text) // 4.

    Simple heuristic — sufficient for Phase 1. Replace with tiktoken
    if precision matters.
    """
```

**Key implementation notes:**
- Token budget enforcement works by greedy inclusion from highest score:
  add blocks one at a time, check cumulative token count, stop when budget
  exceeded. Guaranteed blocks always included first.
- `_estimate_tokens` uses `len(text) // 4` as a rough heuristic. Good enough
  for Phase 1 where budgets are approximate.

---

### 5. `src/elfmem/context/contradiction.py`

**Purpose:** Contradiction suppression — remove lower-confidence member of
contradicting pairs from candidate sets.

**Functions:**

```python
async def suppress_contradictions(
    conn: AsyncConnection,
    candidates: list[ScoredBlock],
) -> list[ScoredBlock]:
    """Remove blocks involved in contradictions, keeping higher-confidence one.

    For each pair of candidates with an active contradiction record:
    - Keep the block with higher confidence
    - Remove the block with lower confidence
    - If confidence is equal, keep the more recently reinforced one

    Args:
        conn: Database connection for fetching contradiction records.
        candidates: List of scored candidate blocks (oversampled).

    Returns:
        Filtered list with contradicting pairs resolved.
    """
```

**Key implementation notes:**
- Extract all candidate block IDs
- Call `queries.get_contradictions_for_blocks(conn, candidate_ids)`
- For each contradiction pair found in candidates, mark the lower-confidence
  one for removal
- Return filtered list

---

### 6. `src/elfmem/operations/recall.py`

**Purpose:** Orchestrates the full retrieval flow: pure retrieval + side effects.
This is the L4 operation that `frame()` calls.

**Imports:**
```python
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncConnection

from elfmem.context.contradiction import suppress_contradictions
from elfmem.context.frames import FrameCache, FrameDefinition, get_frame_definition
from elfmem.context.rendering import render_blocks
from elfmem.db import queries
from elfmem.memory.graph import reinforce_co_retrieved_edges
from elfmem.memory.retrieval import hybrid_retrieve
from elfmem.ports.services import EmbeddingService
from elfmem.types import FrameResult, ScoredBlock
```

**Functions:**

```python
async def recall(
    conn: AsyncConnection,
    *,
    embedding_svc: EmbeddingService,
    frame_def: FrameDefinition,
    query: str | None,
    current_active_hours: float,
    top_k: int = 5,
    cache: FrameCache | None = None,
) -> FrameResult:
    """Execute full retrieval with reinforcement side effects.

    Pipeline:
    1. Check cache (if frame has caching enabled)
    2. Determine effective weights (renormalize if no query)
    3. Run hybrid retrieval pipeline (pure)
    4. Apply guarantee enforcement
    5. Suppress contradictions
    6. Enforce token budget via rendering
    7. Reinforce returned blocks (side effect)
    8. Reinforce co-retrieved edges (side effect)
    9. Cache result (if applicable)
    10. Return FrameResult

    Args:
        conn: Database connection.
        embedding_svc: For embedding the query.
        frame_def: Frame definition (weights, filters, guarantees, etc.)
        query: Query text (None for queryless).
        current_active_hours: For recency computation and reinforcement.
        top_k: Blocks to return.
        cache: Optional frame cache.

    Returns:
        FrameResult with rendered text and scored blocks.
    """
```

**Guarantee enforcement:**

```python
async def _enforce_guarantees(
    conn: AsyncConnection,
    candidates: list[ScoredBlock],
    guarantee_tag_patterns: list[str],
    top_k: int,
) -> list[ScoredBlock]:
    """Ensure guaranteed blocks are always in the result.

    1. Find blocks matching guarantee tag patterns (e.g., "self/constitutional")
    2. Pre-allocate slots for guaranteed blocks
    3. Fill remaining slots with highest-scoring non-guaranteed candidates
    4. Return combined list (guaranteed first, then scored)

    Args:
        conn: Database connection.
        candidates: Scored candidates from retrieval.
        guarantee_tag_patterns: Tag patterns for guaranteed inclusion.
        top_k: Total slots available.

    Returns:
        List of at most top_k ScoredBlock objects with guarantees enforced.
    """
```

**Key implementation notes:**
- If `cache` is set and has a hit for this frame, return cached result
- If no query, use `frame_def.weights.renormalized_without_similarity()`
- Tag filter from `frame_def.filters.tag_patterns` — first pattern used
  (Phase 1 only supports single pattern)
- After hybrid retrieval: apply guarantees, suppress contradictions, trim to top_k
- Render via `render_blocks(blocks, frame_def.template, frame_def.token_budget)`
- Side effects: `queries.reinforce_blocks(conn, block_ids, current_active_hours)`
  and `reinforce_co_retrieved_edges(conn, block_ids)`
- Cache result if `frame_def.cache` is not None

---

### 7. `src/elfmem/api.py` — Modifications

Add `frame()` and `recall()` methods:

```python
async def frame(
    self,
    name: str,
    query: str | None = None,
    *,
    top_k: int = 5,
    token_budget: int | None = None,
) -> FrameResult:
    """Retrieve and render context for the named frame.

    This is the primary retrieval interface. Returns rendered text
    suitable for LLM system prompt injection.

    Args:
        name: Frame name ("self", "attention", "task", or custom).
        query: Query text (required for ATTENTION, optional for TASK).
        top_k: Number of blocks to return.
        token_budget: Override frame's default token budget.

    Returns:
        FrameResult with .text (rendered string) and .blocks (scored blocks).
    """


async def recall(
    self,
    name: str,
    query: str | None = None,
    *,
    top_k: int = 5,
) -> list[ScoredBlock]:
    """Raw retrieval without rendering or reinforcement.

    Power-user method for inspection. No side effects.

    Returns:
        List of ScoredBlock objects.
    """
```

**Key implementation notes:**
- `frame()` calls `recall_op.recall(conn, ...)` which does retrieval +
  reinforcement + rendering
- `recall()` calls `hybrid_retrieve(conn, ...)` directly — pure retrieval,
  no reinforcement
- Both resolve the frame definition via `get_frame_definition(name)`
- `frame()` checks cache first

---

## Key Invariants

1. **Retrieval is pure** — no side effects in Stages 1-4; reinforcement only at L4
2. **Expanded blocks get similarity=0.0** — they compete on other signals only
3. **Pre-filter is STRICT greater-than** — `last_reinforced_at > cutoff` (not >=)
4. **Weights always sum to 1.0** — renormalized weights also sum to 1.0
5. **Guaranteed blocks survive budget cuts** — they are pre-allocated before scoring
6. **Contradiction suppression never adds blocks** — only removes
7. **SELF frame makes zero embedding calls** — no query, no embed()
8. **Co-retrieval reinforcement only for edges that already exist** — never creates edges
9. **Centrality normalised to [0.0, 1.0]** — max-degree block gets 1.0

## Security Considerations

1. **No embedding data corruption** — `cosine_similarity` validates both vectors
   are normalised (or handles un-normalised gracefully via `dot / (norm*norm)`)
2. **Tag filter patterns** — use SQL LIKE with `%` suffix only; no user-controlled
   wildcards that could cause performance issues

## Edge Cases

1. **Zero active blocks** — all stages return empty; FrameResult has empty text
   and empty blocks list
2. **Fewer candidates than top_k** — return all available (no error)
3. **No edges exist** — all centralities are 0.0; graph expansion returns nothing
4. **All candidates contradict each other** — suppression removes all but one;
   result may have fewer than top_k blocks
5. **Multiple guaranteed blocks** — all included; remaining slots filled by scoring
6. **Guaranteed block not in active blocks** — not included (only active blocks
   can be guaranteed — guarantee is checked after pre-filter)
7. **Query embedding produces no similar blocks** — Stage 2 returns nothing;
   Stage 3 can't expand; Stage 4 still works if pre-filter has blocks

## Dependencies

- `elfmem.db.queries` (Step 3)
- `elfmem.adapters.mock` (Step 4) — for testing
- `elfmem.scoring` (Step 2) — compute_score, compute_recency, etc.
- `elfmem.operations.learn` + `consolidate` (Step 5) — data must exist to retrieve
- `elfmem.types` (Step 1)

## Done Criteria

1. TC-R-001: Pre-filter excludes stale and archived blocks
2. TC-R-002: Vector search returns N_seeds blocks
3. TC-R-003: Vector search returns all when fewer than N_seeds
4. TC-R-004: Graph expansion adds 1-hop neighbours with similarity=0
5. TC-R-005: High-centrality expanded block can beat low-centrality seed
6. TC-R-006: SELF frame bypasses vector search and graph expansion
7. TC-R-007: Contradiction suppression removes lower-confidence block
8. TC-R-008: End-to-end retrieval returns valid results
9. TC-F-001: SELF frame always includes constitutional blocks
10. TC-F-002: SELF frame cached
11. TC-F-004: ATTENTION frame ranks query-relevant block correctly
12. TC-F-005: TASK frame guarantees self/goal blocks
13. TC-F-006: Token budget enforced
14. TC-F-007: Guarantee tags pre-allocated before token budget cuts
15. TC-F-008: Contradiction suppression in frame results
16. TC-F-009: Queryless ATTENTION returns most salient blocks
17. TC-G-004: Co-retrieved blocks get edge reinforcement
18. TC-G-005: Edge not reinforced when only one block returned
19. TC-G-007: Centrality computed correctly from edge weights
20. TC-L-009: recall() via frame() reinforces returned blocks
21. TC-L-010: frame() does not reinforce blocks not returned
22. `mypy --strict` passes on all new files
23. `ruff check` clean
