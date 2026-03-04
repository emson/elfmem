# Exploration 003: Full SELF Frame Scoring Walkthrough

## Status: complete

## Question

Walk through a complete SELF frame assembly with 5 candidate blocks,
a small graph with edges, and realistic metadata. Which blocks make it
into the SELF frame and why? How does each scoring component contribute?

## Setup

```yaml
blocks:
  S1:
    content: "I believe in writing clear, maintainable code above all else"
    category: identity
    confidence: 0.90
    decay_lambda: 0.0001   # core
    is_self: true
    reinforcement_count: 8
    hours_since_reinforcement: 12

  S2:
    content: "I prefer collaboration over solo work, ideas improve through discussion"
    category: identity
    confidence: 0.80
    decay_lambda: 0.0001   # core
    is_self: true
    reinforcement_count: 4
    hours_since_reinforcement: 72

  S3:
    content: "I approach debugging systematically: reproduce, isolate, fix, verify"
    category: identity
    confidence: 0.85
    decay_lambda: 0.0001   # core
    is_self: true
    reinforcement_count: 6
    hours_since_reinforcement: 24

  K1:
    content: "Python list comprehensions are more readable than map/filter chains"
    category: python
    confidence: 0.70
    decay_lambda: 0.01     # standard
    is_self: false
    reinforcement_count: 10
    hours_since_reinforcement: 48

  K2:
    content: "The CAP theorem states you can only have two of consistency, availability, partition tolerance"
    category: systems
    confidence: 0.75
    decay_lambda: 0.001    # durable
    is_self: false
    reinforcement_count: 2
    hours_since_reinforcement: 168

edges:
  S1→S3: { relation: supports, weight: 0.8, confidence: 0.9 }
    # "clear code" supports "systematic debugging"
  S2→S1: { relation: elaborates, weight: 0.6, confidence: 0.7 }
    # "collaboration" elaborates on why "clear code" matters
  S3→K1: { relation: exemplifies, weight: 0.4, confidence: 0.5 }
    # "systematic approach" connects to Python style preference
  K1→K2: { relation: unrelated, weight: 0.1, confidence: 0.3 }
    # weak accidental link

# Graph structure:
#   S2 --0.6--> S1 --0.8--> S3 --0.4--> K1 --0.1--> K2
#
# S1 has 2 connections (in-degree 1, out-degree 1)
# S3 has 2 connections (in-degree 1, out-degree 1)
# S2 has 1 connection  (out-degree 1)
# K1 has 2 connections (in-degree 1, out-degree 1)
# K2 has 1 connection  (in-degree 1)

frame: SELF
top_k: 3

# SELF weights: recency=0.05, centrality=0.25, confidence=0.30, similarity=0.10, reinforcement=0.30
weights: { recency: 0.05, centrality: 0.25, confidence: 0.30, similarity: 0.10, reinforcement: 0.30 }

# SELF frame has no query, so similarity = 0.5 (neutral) for all blocks
query: null
similarities: { S1: 0.5, S2: 0.5, S3: 0.5, K1: 0.5, K2: 0.5 }

max_reinforcement_count: 10  # K1 has the highest
```

## Computation

### Step 1: Candidate Pool

SELF frame selection rule: all blocks where `is_self = true`, plus blocks
exceeding a centrality threshold. For this exploration, candidates are:

- S1, S2, S3: explicitly self-tagged
- K1: not self-tagged, but has graph connections to self blocks
- K2: not self-tagged, weakly connected

For SELF, we'll include all 5 as candidates and let scoring decide.

### Step 2: Component Scores

**Recency** (using each block's own λ):
```
S1: e^(-0.0001 × 12)  = 0.9988
S2: e^(-0.0001 × 72)  = 0.9928
S3: e^(-0.0001 × 24)  = 0.9976
K1: e^(-0.01   × 48)  = 0.6188
K2: e^(-0.001  × 168) = 0.8464
```

**Centrality** (degree centrality = connections / max_possible):
```
With 5 nodes, max possible connections = 4 (to every other node).
Degree centrality = (in_degree + out_degree) / (2 × (n-1)) for directed graph.

S1: (1 in + 1 out) / 8 = 0.250
S2: (0 in + 1 out) / 8 = 0.125
S3: (1 in + 1 out) / 8 = 0.250
K1: (1 in + 1 out) / 8 = 0.250
K2: (1 in + 0 out) / 8 = 0.125
```

Note: These are raw degree centrality values. In practice we might use
PageRank or normalize differently. For this walkthrough, using raw degree.

**Confidence** (raw values):
```
S1: 0.90, S2: 0.80, S3: 0.85, K1: 0.70, K2: 0.75
```

**Similarity** (no query for SELF, neutral 0.5):
```
All: 0.50
```

**Reinforcement** (log-normalized, max=10):
```
S1: log(1+8)/log(1+10)  = log(9)/log(11)  = 2.197/2.398 = 0.916
S2: log(1+4)/log(1+10)  = log(5)/log(11)  = 1.609/2.398 = 0.671
S3: log(1+6)/log(1+10)  = log(7)/log(11)  = 1.946/2.398 = 0.811
K1: log(1+10)/log(1+10) = log(11)/log(11) = 2.398/2.398 = 1.000
K2: log(1+2)/log(1+10)  = log(3)/log(11)  = 1.099/2.398 = 0.458
```

### Step 3: Weighted Scoring

SELF weights: rec=0.05, cen=0.25, conf=0.30, sim=0.10, reinf=0.30

```
S1:  0.05×0.999 + 0.25×0.250 + 0.30×0.90 + 0.10×0.50 + 0.30×0.916
   = 0.0500 + 0.0625 + 0.2700 + 0.0500 + 0.2748
   = 0.7073

S2:  0.05×0.993 + 0.25×0.125 + 0.30×0.80 + 0.10×0.50 + 0.30×0.671
   = 0.0496 + 0.0313 + 0.2400 + 0.0500 + 0.2013
   = 0.5722

S3:  0.05×0.998 + 0.25×0.250 + 0.30×0.85 + 0.10×0.50 + 0.30×0.811
   = 0.0499 + 0.0625 + 0.2550 + 0.0500 + 0.2433
   = 0.6607

K1:  0.05×0.619 + 0.25×0.250 + 0.30×0.70 + 0.10×0.50 + 0.30×1.000
   = 0.0309 + 0.0625 + 0.2100 + 0.0500 + 0.3000
   = 0.6534

K2:  0.05×0.846 + 0.25×0.125 + 0.30×0.75 + 0.10×0.50 + 0.30×0.458
   = 0.0423 + 0.0313 + 0.2250 + 0.0500 + 0.1374
   = 0.4860
```

### Step 4: Selection (top_k = 3)

```
Rank 1: S1  0.7073  "clear, maintainable code"       ← SELECTED
Rank 2: S3  0.6607  "systematic debugging"            ← SELECTED
Rank 3: K1  0.6534  "list comprehensions readable"    ← SELECTED (!)
Rank 4: S2  0.5722  "collaboration over solo"
Rank 5: K2  0.4860  "CAP theorem"
```

## Result

```yaml
self_frame:
  selected:
    - { block: S1, score: 0.7073, reason: "highest confidence + high reinforcement" }
    - { block: S3, score: 0.6607, reason: "strong confidence + good reinforcement" }
    - { block: K1, score: 0.6534, reason: "perfect reinforcement score (1.0) compensates for lower confidence" }
  excluded:
    - { block: S2, score: 0.5722, reason: "low centrality (0.125) + lower reinforcement" }
    - { block: K2, score: 0.4860, reason: "low reinforcement + low centrality" }

surprise: "K1 (Python list comprehensions) makes it into the SELF frame
  despite not being self-tagged, because its reinforcement score (1.0)
  compensates for lower confidence (0.70). Meanwhile, S2 (collaboration
  value) is excluded because it has fewer reinforcements and low centrality."
```

## Insight

1. **Reinforcement can override self-tagging.** K1 has reinforcement_count=10
   (score=1.0 × 0.30 = 0.300), while S2 has count=4 (score=0.671 × 0.30 = 0.201).
   That 0.099 gap is larger than S2's confidence advantage (0.80-0.70) × 0.30 = 0.030.
   Heavily-used knowledge blocks can infiltrate the SELF frame.

2. **Is this a bug or a feature?** Arguments for feature: if you look up "list
   comprehensions" 10 times, maybe it IS part of your identity as a Python developer.
   Frequent use signals identity. Arguments for bug: the SELF frame should reflect
   who you ARE, not what you DO. These are different things.

3. **The is_self_component flag is underweighted.** Currently, it only affects the
   initial confidence score (0.85 for self-tagged vs 0.60-0.70 for others). This is
   a +0.045 to +0.075 advantage via the confidence component. But reinforcement can
   easily create a larger gap. Consider: should `is_self = true` provide a direct
   scoring bonus, not just a confidence boost?

4. **Centrality is low-resolution with degree centrality.** S1, S3, and K1 all have
   centrality=0.250. PageRank would differentiate: S1 has an incoming edge from S2
   AND connects to S3, making it structurally more important. Degree centrality
   misses this. PageRank or eigenvector centrality would give S1 a higher score.

5. **S2's low score reveals a connectivity problem.** S2 ("collaboration") only has
   one outgoing edge. It's not well-connected in the identity subgraph. Either:
   - The consolidation pipeline should create more edges for identity blocks
   - Or S2 genuinely is a less important identity belief (used less, less connected)

6. **The scoring formula works, but the component scores matter.** Raw confidence
   and raw reinforcement are the dominant signals (0.30 weight each = 60% combined).
   Centrality (0.25) only matters when there's meaningful graph structure. Recency
   (0.05) is correctly minimal for identity. Similarity (0.10) is a no-op for SELF
   since there's no query.

## Variations

- [ ] Use PageRank instead of degree centrality — does S1 pull further ahead?
- [ ] Add a direct `is_self` bonus of +0.1 to the score — does S2 beat K1?
- [ ] What if K1 had reinforcement_count=3 instead of 10? Where does it rank?
- [ ] What if we set top_k=4? S2 enters — is that the right cutoff?
- [ ] Test incremental assembly with threshold=0.60: would K1 be included or excluded?
