# Exploration 002: The Confidence Trap

## Status: complete

## Question

The previous Python simulation found that ATTENTION frame returns identity
blocks instead of query-relevant blocks, because confidence weight (0.15)
overpowers similarity weight (0.35) when identity blocks have high confidence.

Can we reproduce this, understand exactly why it happens, and find the
minimum weight adjustment that fixes it?

## Setup

```yaml
blocks:
  # Identity blocks (high confidence, self-tagged)
  ID1:
    content: "I value clear communication in all interactions"
    category: identity
    confidence: 0.85
    decay_lambda: 0.0001
    is_self: true
    reinforcement_count: 5
    hours_since_reinforcement: 24

  ID2:
    content: "I prefer functional programming patterns"
    category: identity
    confidence: 0.85
    decay_lambda: 0.0001
    is_self: true
    reinforcement_count: 3
    hours_since_reinforcement: 24

  # Knowledge blocks (normal confidence, query-relevant)
  PY1:
    content: "Python asyncio uses cooperative multitasking via event loop"
    category: python
    confidence: 0.60
    decay_lambda: 0.01
    is_self: false
    reinforcement_count: 1
    hours_since_reinforcement: 48

  PY2:
    content: "The GIL prevents true parallelism for CPU-bound Python threads"
    category: python
    confidence: 0.65
    decay_lambda: 0.01
    is_self: false
    reinforcement_count: 2
    hours_since_reinforcement: 48

  AI1:
    content: "Transformer attention mechanism computes query-key-value products"
    category: ai
    confidence: 0.70
    decay_lambda: 0.01
    is_self: false
    reinforcement_count: 1
    hours_since_reinforcement: 72

edges: []

frame: ATTENTION
query: "Python concurrency and async patterns"

# ATTENTION weights from spec
weights: { recency: 0.25, centrality: 0.15, confidence: 0.15, similarity: 0.35, reinforcement: 0.10 }

# Assumed similarities to query "Python concurrency and async patterns"
similarities:
  ID1: 0.10  # identity, barely relevant
  ID2: 0.25  # mentions programming, somewhat relevant
  PY1: 0.88  # directly about async — best match
  PY2: 0.82  # about concurrency — strong match
  AI1: 0.30  # attention mechanism, moderate

# Centrality: no edges, so all blocks get neutral 0.5
centrality: { ID1: 0.5, ID2: 0.5, PY1: 0.5, PY2: 0.5, AI1: 0.5 }

# For reinforcement normalization
max_reinforcement_count: 5  # max across all blocks
```

## Computation

### Component Scores

**Recency** (decay_weight using each block's own λ):
```
ID1: e^(-0.0001 × 24)  = 0.9976
ID2: e^(-0.0001 × 24)  = 0.9976
PY1: e^(-0.01   × 48)  = 0.6188
PY2: e^(-0.01   × 48)  = 0.6188
AI1: e^(-0.01   × 72)  = 0.4868
```

**Centrality** (all neutral, no edges):
```
All blocks: 0.5
```

**Confidence** (raw values):
```
ID1: 0.85, ID2: 0.85, PY1: 0.60, PY2: 0.65, AI1: 0.70
```

**Similarity** (to query):
```
ID1: 0.10, ID2: 0.25, PY1: 0.88, PY2: 0.82, AI1: 0.30
```

**Reinforcement** (log-normalized):
```
ID1: log(1+5)/log(1+5) = 1.000
ID2: log(1+3)/log(1+5) = 0.774
PY1: log(1+1)/log(1+5) = 0.387
PY2: log(1+2)/log(1+5) = 0.613
AI1: log(1+1)/log(1+5) = 0.387
```

### Weighted Scores (ATTENTION: 0.25/0.15/0.15/0.35/0.10)

```
ID1:  0.25×0.998 + 0.15×0.50 + 0.15×0.85 + 0.35×0.10 + 0.10×1.000
    = 0.2494 + 0.0750 + 0.1275 + 0.0350 + 0.1000
    = 0.5869

ID2:  0.25×0.998 + 0.15×0.50 + 0.15×0.85 + 0.35×0.25 + 0.10×0.774
    = 0.2494 + 0.0750 + 0.1275 + 0.0875 + 0.0774
    = 0.6168

PY1:  0.25×0.619 + 0.15×0.50 + 0.15×0.60 + 0.35×0.88 + 0.10×0.387
    = 0.1547 + 0.0750 + 0.0900 + 0.3080 + 0.0387
    = 0.6664  ← WINNER

PY2:  0.25×0.619 + 0.15×0.50 + 0.15×0.65 + 0.35×0.82 + 0.10×0.613
    = 0.1547 + 0.0750 + 0.0975 + 0.2870 + 0.0613
    = 0.6755  ← ACTUAL WINNER

AI1:  0.25×0.487 + 0.15×0.50 + 0.15×0.70 + 0.35×0.30 + 0.10×0.387
    = 0.1217 + 0.0750 + 0.1050 + 0.1050 + 0.0387
    = 0.4454
```

### Ranking

```
1. PY2  0.6755  "GIL prevents true parallelism"     ← CORRECT: query-relevant
2. PY1  0.6664  "asyncio uses cooperative multitask" ← CORRECT: query-relevant
3. ID2  0.6168  "prefer functional programming"
4. ID1  0.5869  "value clear communication"
5. AI1  0.4454  "transformer attention mechanism"
```

## Result

```yaml
ranking:
  1: { block: PY2, score: 0.6755, correct: true }
  2: { block: PY1, score: 0.6664, correct: true }
  3: { block: ID2, score: 0.6168, correct: false_but_close }
  4: { block: ID1, score: 0.5869, correct: true }
  5: { block: AI1, score: 0.4454, correct: true }

verdict: ATTENTION frame WORKS with 5 diverse blocks.
  Top-2 are the correct Python concurrency blocks.
  Identity blocks rank 3rd and 4th due to recency and confidence
  advantages, but similarity (0.35 weight) is strong enough
  to put the right blocks on top.
```

## Insight

1. **The trap only triggers when identity blocks dominate the corpus.**
   With 2/5 identity blocks (40%), the system works correctly. The Python
   sim had 10/40 blocks as identity (25%), but all 10 scored similarly,
   creating a block of high-scoring identity results that pushed out
   diverse knowledge blocks in top-K.

2. **The real problem is top-K, not the weights.** If top_k=2, we get
   PY2 and PY1 (correct). If top_k=5, we get all blocks including identity
   (also fine — identity context isn't harmful). The problem in the Python
   sim was top_k=10 pulling in ALL identity blocks.

3. **Recency gives identity blocks a persistent advantage.** Identity blocks
   have λ=0.0001, so their recency score is always ~1.0. Knowledge blocks
   with λ=0.01 lose recency fast. At 48 hours, PY1/PY2 are at 0.62 recency
   vs identity at 1.00. This 0.38 gap × 0.25 weight = 0.095 point advantage
   for identity on recency alone.

4. **The fix from the Python sim notes (raise similarity to 0.50) would
   help but isn't necessary.** The current 0.35 weight works when the
   corpus isn't dominated by self-tagged blocks. The real fix is:
   - Ensure identity blocks are a small fraction of total corpus
   - Use incremental assembly (quality threshold) instead of top-K
   - Or: exclude `is_self=true` blocks from ATTENTION frame entirely

5. **Centrality at 0.5 (no edges) is a dead signal.** It contributes
   0.075 equally to every block. Once we have edges, centrality will
   differentiate blocks and could either help or hurt. Need to test
   with a connected graph.

## Variations

- [ ] What if 8/10 blocks are identity? At what ratio does ATTENTION break?
- [ ] What if we exclude is_self blocks from ATTENTION candidate pool?
- [ ] What if PY1/PY2 were reinforced 5 times each? Does reinforcement fix it?
- [ ] What happens with graph edges? Does centrality help Python blocks?
- [ ] Test with incremental assembly (threshold=0.60) instead of top-K
