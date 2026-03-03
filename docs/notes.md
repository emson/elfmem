# AMGS Simulation: Outcomes & Thoughts

## Purpose
This is an append-only log of observations, decisions, and insights from running the AMGS simulation. Used to capture the evolution of design thinking and identify patterns.

---

## Session 1: Initial Launch (2026-03-03)

### Simulation Run
- **Time:** Initial system with 48 blocks ingested
- **Duration:** 7 simulated days (2026-03-01 to 2026-03-08)
- **Results:**
  - 44 blocks consolidated (91.7%)
  - 4 blocks pruned during time advancement (short decay profiles)
  - 0 edges created (similarity threshold of 0.5 too high for synthetic embeddings)
  - Category entropy: 98.11% (nearly perfect diversity)
  - SELF frame: All 8 personal_identity blocks selected
  - Top block score: 0.479 (confidence-dominated due to is_self_component flag)

### Key Findings

#### 1. Isolation is Realistic
The graph has **zero edges** between 48 blocks. Initial reaction: "Bug?" Actual insight: This is correct behavior for a knowledge base where blocks are semantically distinct. Highlights that:
- Similarity threshold (0.5) is appropriate for preventing spurious links
- Synthetic embeddings with topic clustering create well-separated blocks
- Real-world knowledge (Python dev + AI research + identity) naturally partitions

#### 2. Decay is Working as Designed
- 4 blocks pruned after 7 days (~8%)
- Blocks with `short` (λ=0.03, half-life ~23h) and `ephemeral` (λ=0.1, half-life ~7h) profiles fell below 5% threshold
- Core/permanent blocks (λ=0.0001) maintained >80% decay weight
- This matches intuition: daily observations fade, identity beliefs persist

#### 3. Frame Scoring is Dominated by Single Component
SELF frame top block scored 0.479 with breakdown:
- Recency: 1.0 (weight 0.05) → 0.0500
- Centrality: 0.5 (weight 0.25) → 0.1250
- **Confidence: 0.85 (weight 0.30) → 0.2550** ← Dominates
- Similarity: 0.5 (weight 0.10) → 0.0500
- Reinforcement: 0.5372 (weight 0.30) → 0.1612

**Insight:** With isolated graph (0 edges), centrality defaults to 0.5 (neutral). Scoring becomes: confidence (30%) + reinforcement (30%) + minor contributions. Graph connectivity will dramatically reshape these scores.

#### 4. No Edges = Lost Signal
The zero-edge graph means:
- No "what else do I know about this topic?" discovery
- No graph centrality differentiation (all nodes equally central)
- Frame assembly becomes recency + confidence + reinforcement only
- WORLD frame (which weights centrality 0.30) will show no differentiation

**Next iteration:** Lower similarity threshold (0.3-0.4) to create edges. This will unlock:
- Meaningful centrality scores
- Graph-based discovery
- Topic clustering visible in frame results

---

## Simulation Design Decisions

### 1. ID Design: Pure Content Hash
**Decision:** Keep SHA-256 content hash as block ID
- Maintains immutability across lifecycle
- Deduplication works perfectly
- No metadata in ID (correct principle)

Reference: `memory/ID_DESIGN_ANALYSIS.md` (future production considerations)

### 2. In-Memory Storage (Pure Simulation)
**Decision:** Keep sim.py pure in-memory
- All state in Python dicts (blocks, edges)
- NetworkX graph live in memory
- Focus on exploring ideas, not persistence
- Fast iteration and parameter tweaking

Reference: `memory/STORAGE_ARCHITECTURE.md` (what production would look like)

### 3. Frame Weights: Defaults Seem Reasonable
**Observation:** SELF frame weights align with observed behavior
- Low recency (0.05): Identity shouldn't change daily
- High confidence (0.30): Trust self-tagged blocks
- High reinforcement (0.30): Strengthen through use
- Low similarity (0.10): Don't query-chase identity

**But:** This only visible when graph has edges. Hypothesis: once centrality works, we'll see more differentiation and need to validate weights empirically.

---

## Session 2: Incremental Addition Strategy (2026-03-03)

### Implementation & Testing

Added `assemble_frame_incremental()` method to MemorySystem:
- Scores all candidates (same as top-K)
- Sorts by score
- Adds blocks one at a time until score falls below `quality_threshold`
- Stops early if budget reached

**Key Finding: Threshold-Based Quality Cutoff Works**

Test with ATTENTION frame, query="Python performance optimization":
- **Top-K (10 blocks):** All 10 personal_identity blocks returned, lowest score=0.6064
- **Incremental (threshold=0.61):** Only 9 personal_identity blocks, stops when score drops to 0.6064 < 0.61
- **Result:** Eliminated 1 low-quality padding block

**Implication:**
- Top-K always returns exactly N blocks, including marginal ones
- Incremental only returns high-quality blocks
- Prevents dilution of context with low-relevance results
- Quality threshold can be tuned per-frame based on domain

---

## Hypotheses to Test

### H1: Graph Connectivity Drives Centrality
**Current state:** 0 edges, all nodes have centrality ~0.5 (neutral)
**Test:** Lower similarity threshold → create edges → re-assemble WORLD frame
**Expected:** Top nodes will have higher centrality, WORLD frame will show differentiation

### H2: Reinforcement Creates Emergent Clustering
**Hypothesis:** If we assemble SELF repeatedly and reinforce same blocks, centrality will increase for identity-connected blocks
**Test:** Assemble SELF → reinforce → advance time → assemble again → check if neighbors also rise
**Expected:** Identity blocks become more central through repeated access

### H3: Decay Profiles Match Intuition
**Current:** short/ephemeral fade in 7 days; core/permanent persist months
**Test:** Run 30-day, 90-day scenarios; chart decay curves
**Expected:** Can categorize blocks into "working memory" vs "identity" vs "background knowledge" based on profile behavior

### H4: ATTENTION Frame Should Show Query Relevance
**Current:** ATTENTION frame shows personal_identity blocks (why?)
**Test:** Assemble ATTENTION with different queries: "Python asyncio", "machine learning", "code style"
**Expected:** Should show relevant blocks from ai_research and python_dev categories, not personal_identity

---

## Context Frames Deep Dive

**Key Discovery:** Why is ATTENTION returning personal_identity blocks?
- ATTENTION weights similarity at 0.35 (highest)
- Query: "Python concurrency"
- But personal_identity blocks are still scoring highest
- Hypothesis: Query embedding bias isn't working, or all embeddings are equally distant
- **This is worth investigating**

See: `memory/CONTEXT_FRAMES_EXPLAINED.md` for complete breakdown of all 6 frame types + 3 composite frames

---

## Simulation Experiments & Next Steps

### Critical Finding: Query Embeddings Work, Weights Don't

**SOLVED:** Why ATTENTION returns personal_identity for Python queries

The embeddings ARE working correctly:
- Query "Python performance optimization" → python_dev has +0.0338 mean similarity advantage
- Query "machine learning algorithms" → ai_research has +0.0542 mean similarity advantage

**But personal_identity blocks still win because of CONFIDENCE, not similarity:**

For "Python performance optimization" query:
```
Python_dev block score:     0.5780
  - Similarity contribution: 0.1780 (best query match!)
  - Confidence contribution: 0.0750 (regular block)
  - Total non-query:        0.4000

Personal_identity block:    0.6130
  - Similarity contribution: 0.1605 (weaker query match)
  - Confidence contribution: 0.1275 (self-tagged advantage!)
  - Total non-query:        0.4000

Advantage: Identity blocks get +0.0525 from confidence boost
           Python blocks get +0.0175 from query similarity
           IDENTITY WINS: 0.0525 > 0.0175
```

**Design Insight:**
- ATTENTION frame was designed assuming self-tagged blocks are sparse
- In our test corpus (10/40 blocks are self-tagged), confidence dominates similarity
- In production with larger corpus (10/1000), identity wouldn't dominate, query would

**Options to Fix:**
1. Lower `is_self_component` confidence from 0.85 → 0.65 (less boost)
2. Raise ATTENTION similarity weight from 0.35 → 0.50 (more query-driven)
3. Accept this as correct: Identity should influence all reasoning, even queries
4. Separate ATTENTION into: ATTENTION (query-dominant) and INTROSPECTION (identity-dominant)

- [ ] **SHORT_TERM bug:** Returns 0 blocks because time_window_hours=48 but sim advanced 7 days
  - Fix: Either reset sim or adjust time window for testing

### Short-term (Next Session)
- [ ] Investigate SHORT_TERM and query embedding issues (above)
- [ ] Lower similarity threshold from 0.5 → 0.3 to create edges
- [ ] Re-run simulation, verify graph has edges
- [ ] Assemble WORLD frame, inspect centrality differentiation (should show much higher variance now)
- [ ] Test ATTENTION with multiple queries to see if results improve
- [ ] Run REASONING composite frame (union of WORLD + TASK + ATTENTION)

### Medium-term (Exploration)
- [ ] Test hypothesis: Graph connectivity drives centrality scoring
- [ ] Test hypothesis: Reinforcement creates emergent clustering
- [ ] Run 30-day, 90-day, 180-day scenarios; chart decay curves
- [ ] Compare frame weights: What balance feels right for SELF vs ATTENTION?
- [ ] What happens if we change reinforcement scoring to log vs linear?
- [ ] Test what happens with lower confidence blocks (confidence=0.1)

### Long-term (Reference & Future)
- [ ] Document future production architecture insights
- [ ] Plan DuckDB schema based on simulation learnings
- [ ] Design GraphManager for production
- [ ] Plan scale path to Neo4j

---

## Questions Open

1. **Threshold:** What similarity threshold optimizes for useful edges without noise?
   - 0.5 = 0 edges (too high)
   - 0.3 = ? edges (need to test)
   - 0.1 = probably too low (noisy)

2. **Weights tuning:** Are current frame weights optimal?
   - Could run grid search over ATTENTION weights
   - Could A/B test different configurations
   - Hypothesis: query similarity should be higher (>0.35?)

3. **Graph scale:** How many blocks before NetworkX becomes slow?
   - Current: 44 blocks, instant
   - Test: 1000 blocks, 10000 blocks
   - Should measure PageRank computation time

4. **Identity emergence:** Can SELF frame be assembled without explicit is_self_component flag?
   - Alternative: blocks with high reinforcement + high centrality
   - Alternative: blocks within N hops of a "self" root node
   - Hybrid: explicit flag + structural signals

---

## Future Architecture (Reference)

These documents capture architectural patterns to explore in the real system (NOT in this simulation):
- `memory/STORAGE_ARCHITECTURE.md` - DuckDB schema, storage layer design
- `memory/GRAPH_STRATEGY.md` - Graph materialization, caching strategies
- `memory/ID_DESIGN_ANALYSIS.md` - Hash strategy evolution for production

---

## Current Simulation Structure

```
src/
├── sim.py              # MemorySystem (pure in-memory simulation)
├── models.py           # Dataclasses: MemoryBlock, Edge, BlockStatus, etc.
└── embeddings.py       # EmbeddingSimulator (synthetic, topic-clustered)

docs/
├── amgs_architecture.md     # Full spec (what we're simulating)
├── amgs_instructions.md     # Usage guide for sim
└── notes.md                 # This file (append-only log of observations)

memory/
├── ID_DESIGN_ANALYSIS.md           # Reference: future ID strategies
├── STORAGE_ARCHITECTURE.md         # Reference: future persistence layer
└── GRAPH_STRATEGY.md               # Reference: future graph backend
```

---

## References
- **Spec:** `docs/amgs_architecture.md`
- **Instructions:** `docs/amgs_instructions.md`
- **Simulation Code:** `src/sim.py`
