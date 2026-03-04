# Exploration 004: Self as Filter vs. Self as Context

## Status: complete

## Question

Should "self" (identity and values) act as a **hard gatekeeper** that filters what gets learned,
or as a **soft context layer** that influences what survives and what surfaces? What are the
design consequences of each choice?

---

## The Core Problem

Current architecture treats self like any other memory block. But there's intuitive appeal to
the idea that self should **curate** incoming knowledge — acting as a lens that makes some
information more salient and lets other information pass through.

The question is: at what point does self apply this curation, and what are the consequences?

---

## Setup: Four Intervention Points

Self-as-filter could apply at four different stages:

```
Ingestion → [filter?] → Consolidation → [filter?] → Graph → [filter?] → Decay → [filter?] → Retrieval
     ↑                        ↑                               ↑                        ↑
  GATE            CONFIDENCE        DECAY RATE            BOOST
```

| Point | Mechanism | Example | Reversible? | Risk |
|-------|-----------|---------|------------|------|
| **Gate at Ingestion** | Block never enters system | "That's not interesting, ignore it" | NO | Irreversible loss |
| **Confidence at Consolidation** | Block enters weak | "That's okay, but not crucial" | YES | Can be reinforced later |
| **Decay Modifier** | Block fades faster/slower | "That aligns with me, lasts longer" | YES — until pruned | Medium |
| **Boost at Retrieval** | Block exists, surfaces more | "Show me what's relevant to me" | YES | No loss |

The difference between hard filtering and soft biasing is fundamental.

---

## Computation: The Echo Chamber Scenario

Let's trace what happens with a hard gate vs. soft influence.

### Setup

An agent (jazz enthusiast, Python developer) with self-aligned blocks:

```yaml
self_frame:
  S1: "I love jazz music and improvisation"
  S2: "I'm fascinated by distributed systems"
  S3: "I value clear, readable code"

# New blocks arrive over time:
new_blocks:
  B1: "Rock music history and composition"
  B2: "Bayesian inference for machine learning"
  B3: "Game design and player psychology"
```

Self-similarities (cosine to self frame embedding):
```yaml
B1_rock:        0.15  # very different from jazz
B2_bayesian:    0.72  # overlaps with systems thinking
B3_gamedesign:  0.25  # different from agent's interests
```

### Scenario A: Hard Gate (Block if self-similarity < 0.50)

**Initial state:** Self has 3 blocks. System is "jazz + distributed systems + Python"

**Day 1:** B1 (rock music) arrives
- self_similarity = 0.15 < 0.50 → BLOCKED
- System's knowledge: still "jazz + distributed systems + Python"

**Day 2:** B2 (Bayesian ML) arrives
- self_similarity = 0.72 > 0.50 → ACCEPTED
- System learns Bayesian inference
- Self grows: now "jazz + distributed systems + Python + ML"

**Day 3:** B3 (game design) arrives
- self_similarity = 0.25 < 0.50 → BLOCKED
- System's knowledge: "jazz + distributed systems + Python + ML"

**Day 30:** Agent needs to build a game and needs game design knowledge
- System has none (was blocked)
- Agent must manually provide the knowledge
- Or self-consistency breaks down if agent learns game design directly

**Analysis:**

Over 30 days with a hard gate, the system only learns within its initial domain. It has become:
- ✗ Self-consistent (true to values)
- ✗ Adaptable (can't learn new domains)
- ✗ Useful for directed learning
- ✗ Growing through serendipity (rock → fusion → broader music knowledge)

The feedback loop is one-way: Self → Learning → Self (stronger). The system has **crystallised**.

### Scenario B: Soft Bias (Influence decay and confidence, not gating)

**Initial state:** Same self (3 blocks)

**Day 1:** B1 (rock music) arrives
- self_similarity = 0.15
- Enters consolidation
- Initial confidence = 0.50 + (0.15 × 0.35) = 0.605  [soft boost from alignment]
- Decay lambda = 0.01 (standard, not boosted)
- System has all knowledge: jazz, distributed systems, Python, rock

**Day 2:** B2 (Bayesian ML) arrives
- self_similarity = 0.72
- Initial confidence = 0.50 + (0.72 × 0.35) = 0.752  [stronger boost]
- Decay lambda = 0.001 (durable profile, self-aligned)
- Likely to be retrieved more often (higher confidence in frames)

**Day 3:** B3 (game design) arrives
- self_similarity = 0.25
- Initial confidence = 0.50 + (0.25 × 0.35) = 0.588
- Decay lambda = 0.01 (standard)
- Exists in graph; will be retrieved only if relevant to a query

**Day 30:** Agent needs game design knowledge
- System HAS it (B3 was accepted)
- Retrieved if query mentions "game" or related concepts
- Agent can build the game
- Self can now grow: learns game design through directed use
- Self may update if game design becomes relevant: "I enjoy game design challenges"

**Analysis:**

Over 30 days with soft bias, the system learns everything but emphasises self-relevant knowledge:
- ✓ Adaptive (can learn any domain)
- ✓ Coherent (self-aligned knowledge is more prominent)
- ✓ Useful (can support directed learning)
- ✓ Growing through novelty (rock, game design, ML can shift self over time)
- ✓ Self-correcting (if a non-aligned block gets reinforced frequently, it becomes self-relevant)

The feedback loop is dynamic: Self ← Learning → Self (growing).

---

## The Five Problems with Hard Gates

### 1. Echo Chamber / Crystallisation

```
Self (values A) → Gate (blocks ¬A) → Never learns ¬A → Self reinforced (stronger A)
```

Feedback loop with no exit. The system can only deepen existing orientations, never pivot.

### 2. Bootstrap Problem

What's the initial self? If self filters what gets learned, and self is made of learned blocks,
then at t=0 with no self yet, nothing can be learned. You need a hardcoded seed self —
now the system isn't self-organising, it's curated.

### 3. Kills Serendipity

Some of the most valuable learning comes from unexpected domains:
- Jazz musician learns probability via music theory → understands statistics intuitively
- Systems engineer reads about mycorrhizal networks → sees distributed systems everywhere
- A blocked block can never create these cross-domain insights

### 4. Breaks Directed Learning

If you need to learn React (not self-aligned), you have to manually override the gate constantly.
The system becomes unusable for professional development outside your comfort zone.

### 5. Irreversible Loss

A blocked block is gone forever. You can't change your mind later. Even if self evolves, the
lost knowledge is lost. With soft bias, nothing is ever lost — low-interest blocks just surface
less often unless reinforced.

---

## The Architectural Insight

**From Axiom 4:** *"Attention is a selection process, not a storage mechanism."*

Interest is an attention phenomenon. It should influence what surfaces, not what exists.

The distinction is crucial:
- **Storage layer:** Store everything (bounded by pruning only for computational efficiency)
- **Attention layer:** Emphasise what's interesting, surface what's relevant

This maps cleanly to the AMGS design: consolidation is storage (nothing blocked), frame assembly
is attention (interest influences scoring).

---

## The Three-Layer Interest Model

Rather than a single intervention point, use three distinct mechanisms:

### Layer 1: Novelty Deduplication (Ingestion)

Compute similarity to existing blocks. If similarity > 0.95, it's a near-duplicate.
Skip it. This is deduplication, not interest filtering — two blocks with identical
content provide no new information.

```yaml
# At ingestion
if max_similarity_to_existing > 0.95:
    return DEDUPLICATE  # Not learned — true duplicate
else:
    continue → consolidation
```

### Layer 2: Self-Alignment Bias (Consolidation)

Compute cosine similarity to current SELF frame. Use it to:

```yaml
# At consolidation
self_alignment = cosine_similarity(block_embedding, self_frame_embedding)

# Soft confidence boost
initial_confidence = base_confidence + (self_alignment × confidence_boost)
  where base_confidence = source_quality
        confidence_boost = 0.20 (tunable)

# Decay profile selection
decay_lambda = select_decay_profile(self_alignment):
    if self_alignment > 0.70:   λ = 0.001  (durable)
    elif self_alignment > 0.50: λ = 0.01   (standard)
    else:                        λ = 0.01   (standard)

# Store for later use
metadata:
  self_alignment: 0.73
  novelty: 0.82  # vs existing blocks in category
  resonance: 3   # connected to 3 self blocks
```

Effect: Self-aligned knowledge enters the system at higher confidence and with slower decay.
Nothing is blocked. Everything is treatable.

### Layer 3: Self-Resonance Scoring (Retrieval)

Optionally add SelfAlignment as a scoring component in frame assembly:

```yaml
# In frame scoring
Score = w1×Recency + w2×Centrality + w3×Confidence + w4×Similarity
      + w5×Reinforcement + w6×SelfAlignment

# Weights vary by frame type
SELF:       {w6: 0.40}  # identity is self-aligned by definition
ATTENTION:  {w6: 0.00}  # query relevance dominates, not self-alignment
WORLD:      {w6: 0.15}  # domain knowledge reflects who you are
SHORT_TERM: {w6: 0.10}  # recent events, weak self influence
TASK:       {w6: 0.05}  # problem-solving, minimal self influence
INBOX:      {w6: 0.00}  # unprocessed, don't filter
```

Effect: Self-aligned blocks surface more often in frames where it matters, but never override
query relevance or task requirements.

---

## Worked Example: The Jazz Musician Learns Bayesian ML

Setup:
```yaml
self_frame:
  S1: "I love improvisation and jazz"
  S2: "I think systems are complex and interconnected"

new_block:
  B: "Bayesian inference uses prior knowledge to update beliefs probabilistically"

self_alignment(B): 0.75  # resonates with "complex systems" thinking
novelty(B): 0.90        # new to agent
resonance(B): 2         # connects to S2 ("complex systems")
```

### Layer 1: Novelty Check
```
max_similarity_to_existing = 0.42  # not a duplicate
→ PASS to consolidation
```

### Layer 2: Consolidation
```
base_confidence = 0.65  # moderate source quality
self_alignment = 0.75
initial_confidence = 0.65 + (0.75 × 0.20) = 0.80

# Select decay profile
self_alignment (0.75) > 0.70 → λ = 0.001 (durable)

# Store metadata
block:
  id: sha256_bayes
  content: "Bayesian inference..."
  confidence: 0.80
  decay_lambda: 0.001
  self_alignment: 0.75
  resonance: 2
  status: consolidated
```

### Layer 3: Retrieval (ATTENTION frame, query "machine learning")

```yaml
# In ATTENTION frame scoring
query_similarity(B): 0.88  # ML query matches Bayesian block

Score = 0.25×Recency + 0.15×Centrality + 0.15×Confidence
      + 0.35×Similarity + 0.10×Reinforcement + 0.00×SelfAlign
      = 0.25×0.95 + 0.15×0.50 + 0.15×0.80 + 0.35×0.88 + 0.10×0 + 0×0.75
      = 0.2375 + 0.075 + 0.12 + 0.308 + 0 + 0
      = 0.7405
```

Later, agent reinforces B because it's useful. Over time:
```
reinforcement_count → 5
usage_in_queries: "ML", "probability", "inference"

# Self may eventually grow
S3_candidate: "I find probabilistic thinking illuminates complex systems"
```

---

## The Consequences of Each Model

### Hard Gate Model
```
Properties:
  - Self prevents noise (high confidence, focused)
  - Self prevents growth (can't learn outside domains)
  - Self prevents serendipity (can't cross-pollinate ideas)
  - Irreversible (what's blocked stays blocked)

Appropriate for:
  - Filtering spam (hard unwanted content)
  - NOT appropriate for knowledge learning
```

### Soft Bias Model
```
Properties:
  - Self influences emphasis (aligned knowledge is more prominent)
  - Self enables growth (can learn anything, biased by self)
  - Self enables serendipity (novel blocks exist, may be useful)
  - Reversible (can reinforce non-aligned blocks into importance)
  - Self-correcting (self grows with usage)

Appropriate for:
  - Learning systems (everything in AMGS)
  - Adaptive agents
  - Systems that need to pivot
```

---

## Insight & Design Decision

**Decision:** Use the three-layer interest model with soft bias.

**Rationale:**
1. Solves the echo chamber problem — system can learn anything
2. Preserves self-coherence — self-aligned knowledge survives longer
3. Enables serendipity — non-aligned blocks can become valuable
4. Reversible — nothing is lost, only weighted
5. Consistent with Axiom 4 (attention as selection, not storage)
6. Self grows naturally — as agent uses non-aligned knowledge, self expands

**Implementation consequences:**
- At ingestion: only dedup, never gate
- At consolidation: compute self_alignment, use for confidence + decay profile
- At retrieval: optionally add SelfAlignment scoring, frame-type dependent
- Self updates naturally: as blocks get reinforced, self frame updates, which influences new blocks

**Self is not a gatekeeper. Self is a context.** It shapes the landscape but doesn't block
the roads.

---

## Variations

- [ ] What if we implement Layer 2 but skip Layer 3? (soft bias without retrieval boost)
- [ ] What if hard gates only apply to truly adversarial content (offensive material)?
- [ ] What if resonance is computed differently? (connection strength × count vs. binary threshold)
- [ ] Can we measure empirically when self-aligned blocks get higher engagement (reinforcement)?
- [ ] What happens if self evolves so much that old self-blocks become misaligned? Do they repurpose?
