# Brainstorm: Adaptive Intelligence for elfmem

**Date:** March 2026
**Context:** Code review of `retrieval.py`, `consolidate.py`, `curate.py`, `graph.py`, `scoring.py`
**Reference:** `docs/amgs_architecture.md`

---

## The Core Diagnosis: A Passive System

The current system is architecturally sound but **epistemically static**. Every retrieval session
begins from the same fixed scores:

| Component | Set When | Updated When |
|-----------|----------|--------------|
| `confidence` | consolidation (= LLM alignment score) | **Never** |
| `decay_lambda` (λ) | consolidation (from tag/category tier) | **Never** |
| edge `weight` | consolidation (cosine similarity) | Partially (edge reinforcement exists but is **not called** from retrieval) |
| centrality | each retrieval (weighted-degree) | Continuous — this one works well |
| reinforcement_count | on reinforce_blocks() | Continuous — this one works well |

The consequence: the system cannot learn that "block X is consistently useful for this class of
query" or "block Y is retrieved often but never acted upon." Usage intelligence is invisible to
future retrievals. The system has **memory of facts but not memory of its own performance.**

---

## The Structural Gap: What Would Make it Genuinely Self-Improving

```
learn() → consolidate() → frame()/recall() → reinforce()

Current: reinforce() updates timestamp + count. That's it.
Missing: reinforce() should feed back into confidence, λ, edges, and topology.
```

A truly adaptive system needs **feedback loops** — mechanism that observe retrieval outcomes and
update the parameters that drive future retrieval. Below are the mathematical concepts that create
those loops, organized by expected impact.

---

## Tier 1 — High Impact, Low Complexity: Implement These First

### 1. Maximal Marginal Relevance (MMR) for Frame Diversity

**The problem:** `_stage_4_composite_score()` is pure score maximisation. If the top-3 blocks all
say essentially the same thing (similar content, similar embeddings, all scored high because they
share the same reinforcement history), the context frame wastes tokens on redundancy.

For a 50–500 block system with a finite context window, **every redundant block costs context that
could carry genuinely different information.**

**The mathematics (Carbonell & Goldstein, 1998):**

```
MMR(b, Selected) = λ × score(b, query) − (1−λ) × max_{s ∈ Selected} sim(b, s)
```

Iterative selection: at each step, pick the block that maximises `MMR(b, Selected_so_far)`.
- λ close to 1.0 → mostly relevance (current behaviour)
- λ = 0.5 → balanced relevance + diversity
- λ close to 0.0 → mostly diversity (an exploration mode)

**Why it fits perfectly here:** We already have all the infrastructure:
- `cosine_similarity()` is already in `memory/dedup.py`
- Block embeddings are already loaded during retrieval
- It's a pure post-processing step after stage 4 — no new data required

**Practical effect:**
```
Without MMR:  "User prefers dark mode | User prefers dark backgrounds | User finds dark themes easier"
With MMR:     "User prefers dark mode | User learns visually | User is timezone Asia/Tokyo"
```

**Estimated improvement:** Highest qualitative improvement per line of code. Zero LLM cost.

---

### 2. Hebbian Edge Learning — Already Built, Not Connected

**Critical finding from code review:** `reinforce_co_retrieved_edges()` in `graph.py:52` already
implements Hebbian edge reinforcement — it updates edge weights when blocks are co-retrieved.
**It is never called from the retrieval or frame pipeline.**

Hebbian principle: *"Neurons that fire together, wire together."*

```
edge_weight(A, B) += δ  if A and B were retrieved in the same frame/recall
edge_weight(A, B) -= ε  if A is retrieved frequently without B (implicit negative signal)
```

**Why this matters:**
- Edges are currently created once at consolidation via cosine similarity (threshold 0.60)
- Two blocks might have LOW similarity but ALWAYS appear together in relevant contexts
  (e.g., "error handling" and "async programming" aren't semantically similar but often retrieved together)
- Those co-retrieval relationships are never captured in the graph

**The fix:** Call `reinforce_co_retrieved_edges(conn, block_ids)` inside `recall()` and `frame()`
operations whenever blocks are returned. This is a one-line wire-up.

**Longer-term extension:** Add a **Hebbian anti-Hebb decay** in `curate()`:
- Edges where two blocks are retrieved independently (not together) over N sessions → weight decreases
- Edges where two blocks are always co-retrieved → weight accumulates toward 1.0
- The edge structure will slowly converge toward the actual semantic topology revealed by use

**Estimated improvement:** Significant improvement in graph expansion (stage 3). The graph learns
what concepts cluster in actual agent usage rather than what looks similar at consolidation time.

---

### 3. Betweenness Centrality for Archival Protection

**The problem:** `_archive_decayed_blocks()` in `curate.py` archives blocks based purely on recency
dropping below 0.05. A block with low recency (rarely accessed) might be a critical **bridge node**
connecting two otherwise disconnected knowledge clusters. Archiving it would silently disconnect the
graph.

**The mathematics:** Betweenness centrality measures what fraction of shortest paths between
all pairs of nodes pass through a given node.

```
BC(v) = Σ_{s≠v≠t} [σ(s,t|v) / σ(s,t)]
```

Where σ(s,t) = total shortest paths from s to t, σ(s,t|v) = those passing through v.

**Practical application:**
- At each `curate()` call, compute betweenness for blocks near the archive threshold
- If `betweenness(block) > threshold` → mark as "protected" regardless of recency
- Protective logic: *"This block is structurally important even if no one has accessed it recently"*

**For a 50–500 block system** this is computationally trivial. NetworkX computes betweenness
for 500 nodes in milliseconds.

**Simple heuristic alternative** (no full NetworkX needed): A block is structurally important
if it has edges to blocks in **multiple distinct semantic clusters**. Detect this by checking
whether its neighbours' embedding vectors span more than one region of embedding space.

---

### 4. Novelty / Surprise Detection at learn() Time

**The problem:** `learn()` writes every block to the inbox identically. The system doesn't know
whether a new block is genuinely adding new information or just adding more of what it already knows.

**Information-theoretic surprise:**
```
surprise(block) = 1 − max_{a ∈ active} cosine_similarity(embed(block), embed(a))
```

- `surprise ≈ 0.0` → this block is very similar to something already known
- `surprise ≈ 1.0` → this block covers territory not yet in the knowledge base

**Three uses of the surprise signal:**

1. **At learn() time:** Embed the block cheaply and compare to a fast approximate index.
   High surprise → flag for priority consolidation. Low surprise → flag as likely near-duplicate
   before even running consolidation.

2. **Initial tier assignment:** High-surprise blocks start as `EPHEMERAL` (new information hasn't
   proven its durability yet). Low-surprise blocks that survive dedup can start as `DURABLE`
   (they're reinforcing established knowledge).

3. **Coverage reporting in status():** Track the "information density" of the knowledge base.
   `status()` could report: *"You have extensive coverage of Python/async (12 blocks) but no
   coverage of deployment (0 blocks adjacent in embedding space)."*

**Cost:** One embedding call at learn() time (already done at consolidation, so no extra LLM cost).

---

## Tier 2 — High Impact, Moderate Complexity

### 5. Bayesian Confidence Updating (Beta-Binomial Model)

**The problem:** `confidence` is set once at consolidation:
```python
confidence = alignment if alignment >= self_alignment_threshold else 0.50
```
It never changes. A block with `confidence=0.50` that has been reinforced 200 times and a block
with `confidence=0.50` that has never been touched look identical to the scoring function.

**The mathematics:** Beta-Binomial conjugate model.

Every block's relevance can be modelled as a Bernoulli trial: retrieved and reinforced = success;
retrieved and not reinforced = failure.

```
confidence ~ Beta(α, β)
α = initial_alignment_successes + reinforcement_count
β = initial_alignment_failures + retrieved_but_not_reinforced_count

E[confidence] = α / (α + β)
```

The initial values encode the LLM alignment prior:
```
α₀ = confidence_init × PRIOR_STRENGTH   (e.g., 2.0)
β₀ = (1 − confidence_init) × PRIOR_STRENGTH
```

**Requires:** A `retrieved_count` column (total times returned from retrieval, reinforced or not).
Then `retrieved_but_not_reinforced = retrieved_count − reinforcement_count`.

**Effect over time:**
- Block used often and frequently reinforced → `confidence → 1.0`
- Block retrieved often but rarely acted upon → `confidence → 0.0` → eventually archived
- Blocks with few retrievals maintain their prior (LLM alignment score) until evidence accumulates

This is the **single most principled improvement** to the confidence scoring, because it
creates a ground-truth calibration from actual agent behaviour.

---

### 6. Adaptive Decay Rate (λ Evolution)

**The problem:** `decay_lambda` is determined by tag/category tier at consolidation. A
`STANDARD` block (λ=0.010) stays STANDARD forever. If that block has been reinforced 100 times,
its effective half-life should have adapted to reflect its proven durability.

**The mathematics:** Empirically update λ based on observed usage:

```
reinforcement_interval = time between consecutive reinforcement events

empirical_half_life = median(reinforcement_intervals)

λ_new = ln(2) / empirical_half_life
λ_new = clip(λ_new, λ_PERMANENT, λ_EPHEMERAL)
```

**Simpler version** (at each `curate()` call):
```
if reinforcement_rate_last_N_sessions > HIGH_THRESHOLD:
    λ *= DURABILITY_INCREASE_FACTOR   (e.g., 0.90 → slower decay)
else if reinforcement_rate_last_N_sessions < LOW_THRESHOLD:
    λ *= DECAY_INCREASE_FACTOR        (e.g., 1.10 → faster decay)
```

**The effect:** A STANDARD block that proves itself through consistent use gradually migrates
toward DURABLE behaviour. A DURABLE block that is never retrieved gradually migrates toward
STANDARD. The tiers become **starting points, not permanent classifications.**

---

### 7. Thompson Sampling for Exploration vs. Exploitation

**The problem:** Current retrieval is pure exploitation — always returns the highest-scoring blocks.
This creates a **rich-get-richer dynamic**: high-scoring blocks get reinforced, increasing their score,
making them retrieved more often, accumulating even more reinforcement. Low-scoring but potentially
valuable blocks are never given a chance to prove their relevance.

**The mathematics:** Thompson Sampling (Bayesian multi-armed bandit).

Model each block's relevance as `Beta(α, β)`:
- At retrieval time: **sample** from each block's distribution rather than using the mean
- High-confidence blocks: narrow distribution, usually samples near its mean
- Uncertain blocks: wide distribution, occasionally samples high → gets selected → receives signal

```
for block in candidates:
    sampled_value = Beta(α=block.reinforcement_count + 1,
                         β=block.retrieved_but_not_reinforced + 1).sample()

selected = top_k_by(sampled_value)
```

**Practical result:** Roughly 90–95% of retrievals return the same high-quality blocks as pure
exploitation. But 5–10% of the time, an underexplored block gets selected, its relevance is tested,
and the feedback loop either confirms it (reinforcement) or doesn't (increasing β). Over time,
the true quality distribution of the knowledge base becomes accurately known.

---

## Tier 3 — Strategic Long-Term Value

### 8. DBSCAN Semantic Clustering for Topology Discovery

**What it reveals:** Run DBSCAN (density-based clustering) on the embedding vectors of all
active blocks. This discovers the "natural topology" of the knowledge base without requiring
a predefined number of clusters or manual categorisation.

```python
from sklearn.cluster import DBSCAN
labels = DBSCAN(eps=0.30, min_samples=2, metric='cosine').fit_predict(embeddings)
# -1 = isolated/noise block, 0,1,2... = cluster ID
```

**Four applications:**

1. **Tag propagation:** Blocks in the same cluster likely share tags. If cluster C contains
   10 blocks tagged `[python, async]` and one untagged block, it probably belongs to that topic.

2. **Deduplication acceleration:** Scale near-dup detection from O(N²) → O(N×k) by only
   comparing blocks within the same cluster. For 500 blocks in 20 clusters of 25 each, this
   is a 20× speedup.

3. **Bridge block detection:** A block connecting two otherwise separate clusters is a
   "conceptual bridge" — it should be promoted to DURABLE and protected from archival.

4. **Knowledge gap reporting:** Empty regions in embedding space adjacent to existing clusters
   reveal topics the agent doesn't know about. Report in `status()`.

---

### 9. Spreading Activation — Dynamic Relevance Propagation

**Concept from cognitive science:** When a concept is accessed, activation "spreads" to its
neighbours, decaying with graph distance. The total activation reaching a block determines
its "readiness" — how primed it is for retrieval.

```
activation(v, t+1) = decay × activation(v, t) + Σ_{u→v} edge_weight(u,v) × activation(u, t)
```

**Applied to elfmem:**
- When block A is retrieved, activation spreads to its graph neighbours
- Blocks that receive activation from multiple recently-retrieved blocks accumulate "readiness"
- `readiness(block)` becomes an input to the composite score, complementing centrality

**Why this is powerful:** Spreading activation captures **contextual priming** — the idea that
some blocks become relevant only when related concepts have recently been in play. This is more
expressive than static centrality because it's dynamic per query context.

**Implementation note:** This is a small change to the composite score function — add a
`spreading_activation` component alongside the existing five.

---

### 10. Information Bottleneck Theory — Principled Compression

**Tishby et al.'s Information Bottleneck:** Find the minimum representation that maximises
the mutual information between the compressed knowledge and future queries.

```
Compress knowledge K to find K̃ that:
maximises I(K̃; future_queries) while
minimising I(K̃; raw_input)
```

**Applied to curation decisions:** Instead of archiving blocks whose recency drops below 0.05,
ask: "If we remove this block, how much do we reduce our ability to answer future queries?"

This requires modelling `P(future_queries | knowledge_base)`. A tractable approximation:
- Build a histogram of past query embeddings
- For each candidate-for-archive block, measure how much it covers the query distribution
- If coverage contribution > threshold → protect from archival

This is the **principled theoretical foundation** for all archival decisions. The simpler heuristics
(betweenness centrality, recency) are approximations of this deeper principle.

---

### 11. Monte Carlo Impact Simulation for Consolidation Decisions

**The problem:** Before performing deduplication/contradiction resolution/tier assignment,
we're making irrevocable decisions based on static scoring. We could simulate the downstream
impact first.

**For near-duplicate resolution:** Instead of always superseding the old block, simulate:
- *"If we keep the OLD block: how does retrieval quality change for the past N queries?"*
- *"If we keep the NEW block: same simulation."*
- *"If we MERGE them: same simulation."*

For a 50–500 block system with at most 10 near-dup decisions per consolidation, this is
computationally trivial (run 30 simulations, each < 1ms). The expected value of each decision
is directly observable.

**Scope for MC simulation:**
```
Decisions amenable to Monte Carlo:
- Which block to keep in near-duplicate pair
- Whether to create an edge (could create false connections)
- Tier assignment (STANDARD vs DURABLE for borderline blocks)
- Whether to archive a block near the decay threshold
```

---

## The Synthesis: An Integrated Adaptive Learning Architecture

These concepts don't operate independently — they form a **coherent adaptive system** when combined.

```
┌─────────────────────────────────────────────────────────────────────────┐
│  INGEST LAYER                                                           │
│  learn(block) → surprise(block) → fast-path dedup signal              │
│                                    ↓ novelty score                      │
│                               priority_queue                            │
└────────────────────────────────────┬────────────────────────────────────┘
                                     ↓
┌─────────────────────────────────────────────────────────────────────────┐
│  CONSOLIDATION LAYER                                                    │
│  + DBSCAN cluster membership assigned                                   │
│  + initial λ = EPHEMERAL if high-surprise else STANDARD                 │
│  + Bayesian prior: α₀, β₀ from alignment score                         │
└────────────────────────────────────┬────────────────────────────────────┘
                                     ↓
┌─────────────────────────────────────────────────────────────────────────┐
│  RETRIEVAL LAYER                                                        │
│  Stage 1-3: unchanged (pre-filter → vector → graph expand)             │
│  Stage 4: composite score + Thompson Sampling exploration               │
│  Stage 5 (NEW): MMR diversity pass → final top-k                       │
│  Edge side-effect: reinforce_co_retrieved_edges() ← WIRE THIS UP NOW  │
│  Block side-effect: increment retrieved_count                           │
└────────────────────────────────────┬────────────────────────────────────┘
                                     ↓ (signals flow up)
┌─────────────────────────────────────────────────────────────────────────┐
│  FEEDBACK LAYER (at curate() time)                                      │
│  Bayesian confidence update: α,β ← reinforcement and retrieved_count   │
│  Adaptive λ update: λ ← empirical reinforcement interval               │
│  Hebbian edge evolution: weights ← co-retrieval frequency              │
│  Topology audit: betweenness → archival protection list                 │
│  Cluster recompute: DBSCAN → tag propagation                           │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Evaluation Matrix: Impact vs. Complexity

| Concept | Impact | Complexity | Infrastructure Required | Start? |
|---------|--------|------------|------------------------|--------|
| MMR diversity (stage 5) | ★★★★★ | ★ | Already have cosine_similarity | **Yes, now** |
| Hebbian edges (wire-up) | ★★★★★ | ★ | Already built, just needs calling | **Yes, now** |
| Betweenness protection | ★★★★ | ★★ | Add NetworkX / or heuristic | **Yes** |
| Novelty at learn() | ★★★★ | ★★ | One extra embedding call | **Yes** |
| Bayesian confidence | ★★★★★ | ★★★ | New `retrieved_count` column | Next phase |
| Adaptive λ | ★★★★ | ★★★ | Track reinforcement timestamps | Next phase |
| Thompson Sampling | ★★★ | ★★★ | Needs `retrieved_count` column | Next phase |
| DBSCAN clustering | ★★★★ | ★★★ | Add sklearn dependency | Next phase |
| Spreading Activation | ★★★ | ★★★ | New scoring component | Later |
| Monte Carlo simulation | ★★★ | ★★★★ | Framework for impact simulation | Later |
| Information Bottleneck | ★★★★ | ★★★★★ | Complex probabilistic model | Research |

---

## Three Outside-The-Box Ideas Worth Considering

### A — Contrastive Memory: Blocks That Define Each Other

When two similar blocks (high cosine similarity) both survive deduplication, they have a special
relationship: they're similar enough to be in the same semantic neighbourhood, but distinct enough
to both deserve existence. This distinction is meaningful.

**Proposal:** Create a **contrastive edge** type between such pairs:
```
edge_type = "contrasts_with"  (not "similar_to")
```
The *direction* of the difference `embed(A) - embed(B)` in embedding space encodes what A has
that B lacks. Tag A with "A has more of [direction]" and B with "B has more of [direction]."

During frame assembly: if block A is selected, the contrastive edge surfaces B as a candidate
not because they're similar, but because B provides the complementary perspective.

This is how expert human memory works — not just knowing facts, but knowing what distinguishes
related facts from each other.

### B — Temporal Pattern Mining: Blocks with Rhythmic Relevance

Some knowledge is periodically relevant rather than continuously relevant. "Deployment checklist"
might be retrieved once a month during release cycles, but never in between. Standard decay will
archive it as rarely-accessed, when it should be protected as "periodically critical."

**Proposal:** Track the **time-series of access events** per block.
Apply FFT or autocorrelation to detect periodic patterns:
```
access_times = [t1, t3, t7, t14, t28, t60, ...]  # 30-day period detected
```
Blocks with strong periodicity signals get tagged `periodicity/monthly` and their decay clock
resets based on the detected period rather than real-time elapsed.

This is the equivalent of "seasonal memory" in cognitive science.

### C — Eigenvector Decomposition for Self-Insight

The full weight matrix of the knowledge graph is a symmetric matrix W ∈ ℝ^{N×N}.
Its eigendecomposition reveals the fundamental "modes" of the knowledge structure:
- The dominant eigenvector = the "direction of maximum coherence" (what the agent knows most about)
- Small eigenvalues = weakly-connected peripheral knowledge

A summary in `status()` like: *"The dominant theme of your knowledge base is [summarise eigenvector
direction]. Peripheral knowledge includes [low-eigenvalue cluster topics]."* — generated without
any LLM call, purely from linear algebra — would be a genuinely powerful self-insight tool.

---

## Recommended First Steps

**Immediate (no new data, no schema changes):**
1. Wire `reinforce_co_retrieved_edges()` into `frame()` and `recall()` — one line
2. Add MMR as a stage-5 post-processing step in `hybrid_retrieve()` — 10 lines
3. Add betweenness heuristic to archival protection in `curate()` — 5 lines

**Next phase (schema changes, one new column):**
4. Add `retrieved_count` column to blocks table
5. Implement Bayesian confidence updating in `curate()`
6. Implement adaptive λ evolution in `curate()`

**Later (new capabilities):**
7. DBSCAN clustering for tag propagation and dedup acceleration
8. Thompson Sampling exploration mode
9. Novelty scoring at learn() time
10. Spreading Activation as a scoring component

---

## The Central Insight

The current system is a high-quality passive store. The gap between it and a genuinely adaptive
cognitive system is not large in terms of code — **it is large in terms of feedback loops.**

The most important sentence in the architecture document is:

> *"All memory decays unless reinforced through use."*

The missing corollary: **all parameters should adapt through use.** Confidence, decay rate, edge
weights, tier classification — all of these should slowly converge toward values that reflect how
the knowledge base actually behaves under real agent usage, not how it looked on the day it was
ingested.

The mathematical framework for this convergence already has well-proven tools:
Bayesian updating, exponential moving averages, Hebbian learning, MMR selection.
None require machine learning infrastructure or model training.
All are incremental, reversible, and inspectable.
