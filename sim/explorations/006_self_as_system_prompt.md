# Exploration 006: Self as System Prompt

## Status: complete

## Question

The SELF frame is used to assemble a system prompt for LLM calls — the persistent identity
layer that grounds all responses. How does this work while:
- Using the **same mechanisms** as all other frame types
- Keeping the prompt **small** (like a CLAUDE.md file)
- Maintaining **stability** across queries in the same session
- Handling **edge cases** without special-casing

---

## The Core Tension

SELF frame assembly (Exploration 003) produces a **ranked list of memory blocks**.
A system prompt is a **single coherent document** with a hard token budget.
These are different things.

The resolution: keep the **selection mechanism identical** to all other frames.
Add a **formatting layer** on top that converts the ranked list into prose.
That layer is new. The selection machinery is not.

```
┌─────────────────────────────────────────────────────────────────┐
│  SELECTION LAYER  (identical to all other frames)               │
│                                                                 │
│  Self-tagged blocks → SELF scoring → Top-K within budget       │
│                                                                 │
│  Same: weights, decay, reinforcement, graph, confidence         │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│  FORMATTING LAYER  (self-specific, runs once per refresh)       │
│                                                                 │
│  Ranked blocks → Contradiction check → Template → Prose doc    │
│                                                                 │
│  New: token budget, contradiction resolution, markdown template │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│  CACHE LAYER  (stability mechanism)                             │
│                                                                 │
│  Formatted document cached with TTL + event-based invalidation │
│                                                                 │
│  New: cache, invalidation triggers, session binding            │
└─────────────────────────────────────────────────────────────────┘
                              ↓
                      System Prompt Text
```

---

## Setup: The Five Axioms Applied

From the architecture spec, the system's axioms must hold:

| Axiom | How it applies to Self-as-System-Prompt |
|-------|----------------------------------------|
| 1. Atomicity | Self blocks are immutable content, mutable metadata — unchanged |
| 2. Relational Meaning | Self blocks connected by edges in the same graph — unchanged |
| 3. Dynamic Relevance | Self blocks decay and are reinforced on access — unchanged |
| 4. Selective Attention | Only a subset of self blocks fit in the prompt (budget) — unchanged mechanism |
| 5. Constructed Context | System prompt is assembled on demand from memory — unchanged |
| 6. Calibrated Confidence | Self block confidence affects selection — unchanged |
| 7. Emergent Identity | System prompt emerges from most-reinforced, highest-confidence blocks — unchanged |

**All seven axioms hold.** The formatting layer adds no new axioms. It is a pure output transformation.

---

## Setup: Block Definitions

```yaml
# Self-tagged blocks with category subcategories for section routing
blocks:
  C1:
    content: "I always respond in the language the user is writing in"
    category: identity/constraint      # → Constraints section of prompt
    confidence: 0.95
    decay_lambda: 0.0001              # core — constraint should persist
    is_self: true
    reinforcement_count: 12
    hours_since_reinforcement: 6

  C2:
    content: "I never claim certainty when I don't have it"
    category: identity/constraint
    confidence: 0.90
    decay_lambda: 0.0001
    is_self: true
    reinforcement_count: 8
    hours_since_reinforcement: 24

  V1:
    content: "I believe in clear, honest communication above persuasion"
    category: identity/value
    confidence: 0.85
    decay_lambda: 0.0001
    is_self: true
    reinforcement_count: 6
    hours_since_reinforcement: 48

  V2:
    content: "I value conciseness — say more with less"
    category: identity/value
    confidence: 0.80
    decay_lambda: 0.0001
    is_self: true
    reinforcement_count: 10
    hours_since_reinforcement: 12

  S1:
    content: "I think step-by-step through problems before answering"
    category: identity/style
    confidence: 0.75
    decay_lambda: 0.0001
    is_self: true
    reinforcement_count: 4
    hours_since_reinforcement: 72

  X1:
    content: "I am a Python developer working on AI memory systems"
    category: identity/context
    confidence: 0.80
    decay_lambda: 0.001              # durable — context changes more often
    is_self: true
    reinforcement_count: 3
    hours_since_reinforcement: 48

  # Non-self block (should NOT appear in system prompt)
  K1:
    content: "Python asyncio uses cooperative multitasking"
    category: python
    confidence: 0.70
    decay_lambda: 0.01
    is_self: false
    reinforcement_count: 5
    hours_since_reinforcement: 24

edges:
  C1→C2: { relation: supports, weight: 0.7 }   # honesty constraints reinforce each other
  V1→C2: { relation: exemplifies, weight: 0.6 } # honesty value exemplifies uncertainty constraint
  V2→S1: { relation: shapes, weight: 0.5 }      # brevity shapes thinking style

# System parameters
token_budget: 600          # hard cap for system prompt
prune_threshold: 0.05
max_reinforcement_count: 12
```

---

## Computation: Layer 1 — SELF Frame Assembly

### Step 1: Candidate Pool

Candidates: all blocks where `is_self = true` OR high centrality in identity subgraph.

```yaml
candidates: [C1, C2, V1, V2, S1, X1]
excluded: [K1]  # not self-tagged
```

### Step 2: Component Scores

**Recency** (λ × hours_since_reinforcement):
```
C1: e^(-0.0001 × 6)   = 0.9994
C2: e^(-0.0001 × 24)  = 0.9976
V1: e^(-0.0001 × 48)  = 0.9952
V2: e^(-0.0001 × 12)  = 0.9988
S1: e^(-0.0001 × 72)  = 0.9928
X1: e^(-0.001  × 48)  = 0.9531
```

**Centrality** (degree / max_possible with 6 nodes):
```
C1: (0 in + 1 out) / 10 = 0.10
C2: (2 in + 0 out) / 10 = 0.20   ← highest — two blocks point to it
V1: (0 in + 1 out) / 10 = 0.10
V2: (0 in + 1 out) / 10 = 0.10
S1: (1 in + 0 out) / 10 = 0.10
X1: (0 in + 0 out) / 10 = 0.00   ← isolated — no connections
```

**Confidence:**
```
C1: 0.95, C2: 0.90, V1: 0.85, V2: 0.80, S1: 0.75, X1: 0.80
```

**Similarity** (no query for SELF → neutral 0.50 for all):
```
All: 0.50
```

**Reinforcement** (log-normalized, max=12):
```
C1: log(1+12)/log(1+12) = 1.000
C2: log(1+8)/log(1+12)  = 0.847
V1: log(1+6)/log(1+12)  = 0.748
V2: log(1+10)/log(1+12) = 0.937
S1: log(1+4)/log(1+12)  = 0.618
X1: log(1+3)/log(1+12)  = 0.531
```

### Step 3: Weighted Scores

SELF weights: rec=0.05, cen=0.25, conf=0.30, sim=0.10, reinf=0.30

```
C1: 0.05×0.999 + 0.25×0.10 + 0.30×0.95 + 0.10×0.50 + 0.30×1.000
  = 0.050 + 0.025 + 0.285 + 0.050 + 0.300
  = 0.710

C2: 0.05×0.998 + 0.25×0.20 + 0.30×0.90 + 0.10×0.50 + 0.30×0.847
  = 0.050 + 0.050 + 0.270 + 0.050 + 0.254
  = 0.674

V2: 0.05×0.999 + 0.25×0.10 + 0.30×0.80 + 0.10×0.50 + 0.30×0.937
  = 0.050 + 0.025 + 0.240 + 0.050 + 0.281
  = 0.646

V1: 0.05×0.995 + 0.25×0.10 + 0.30×0.85 + 0.10×0.50 + 0.30×0.748
  = 0.050 + 0.025 + 0.255 + 0.050 + 0.224
  = 0.604

X1: 0.05×0.953 + 0.25×0.00 + 0.30×0.80 + 0.10×0.50 + 0.30×0.531
  = 0.048 + 0.000 + 0.240 + 0.050 + 0.159
  = 0.497

S1: 0.05×0.993 + 0.25×0.10 + 0.30×0.75 + 0.10×0.50 + 0.30×0.618
  = 0.050 + 0.025 + 0.225 + 0.050 + 0.185
  = 0.535
```

**Ranked:**
```
1. C1  0.710  "respond in user's language"         constraint
2. C2  0.674  "never claim certainty I don't have" constraint
3. V2  0.646  "value conciseness"                  value
4. V1  0.604  "clear honest communication"         value
5. S1  0.535  "think step-by-step"                 style
6. X1  0.497  "Python developer, AI memory"        context
```

---

## Computation: Layer 2 — Token Budget Enforcement

Estimated token counts per block (content + formatting overhead):

```yaml
token_estimates:
  C1: 45 tokens  # "I always respond in the language the user writes in"
  C2: 52 tokens  # "I never claim certainty when I don't have it"
  V2: 38 tokens  # "I value conciseness — say more with less"
  V1: 55 tokens  # "I believe in clear, honest communication above persuasion"
  S1: 60 tokens  # "I think step-by-step through problems before answering"
  X1: 62 tokens  # "I am a Python developer working on AI memory systems"

overhead:  # Headers, bullets, whitespace in template
  section_headers: 4 × 12 = 48 tokens
  intro_line: 18 tokens
  total_overhead: 66 tokens
```

**Budget allocation (600 token hard cap):**
```
Available for content: 600 - 66 = 534 tokens

Running total:
  + C1  (45): 45   / 534  ✓ include
  + C2  (52): 97   / 534  ✓ include
  + V2  (38): 135  / 534  ✓ include
  + V1  (55): 190  / 534  ✓ include
  + S1  (60): 250  / 534  ✓ include
  + X1  (62): 312  / 534  ✓ include

All 6 blocks fit within budget.
Total system prompt: 312 + 66 = 378 tokens
```

All 6 blocks fit. The budget acts as a safety valve for when there are many self blocks.
With 40 self-tagged blocks, only the top-scoring ones would be included.

---

## Computation: Layer 3 — Contradiction Detection

Check each pair of blocks for semantic conflict.

```yaml
contradiction_check:

  C1 × C2: complementary (both honesty-related) → OK
  V2 × V1: potential tension
    V2: "conciseness"     confidence: 0.80
    V1: "clear honest communication"  confidence: 0.85
    # "Clear" might mean "thorough" — conflicts with "concise"?
    # Similarity: 0.62 — moderate overlap
    # Not a hard contradiction: clarity and conciseness can coexist
    → ACCEPT BOTH, ordering resolves: V1 (higher conf) frames V2 (brevity serves clarity)

  V2 × S1: potential tension
    V2: "conciseness"
    S1: "think step-by-step"
    # Step-by-step is a process, not a length. Not contradictory.
    → ACCEPT BOTH

  No hard contradictions found.
  Soft tensions noted:
    V1↔V2: tension (clarity vs brevity) — ordering in template matters
```

**Contradiction resolution algorithm:**
```
If similarity(A, B) > 0.75 AND semantic_polarity(A, B) = opposing:
    Keep: higher confidence block
    Discard: lower confidence block
    Log: (A, B, reason, timestamp) → calibration_log

Otherwise:
    Keep both, order by score
```

---

## Computation: Layer 4 — Template Formatting

Map each block to its section by `category` subcategory:

```yaml
section_mapping:
  identity/constraint → "## Constraints"
  identity/value      → "## What I Value"
  identity/style      → "## How I Work"
  identity/context    → "## Current Focus"
  identity/core       → "## Who I Am"  (if present)
```

**Applying template:**

```markdown
---
# Self — Identity Context
assembled_at: 2026-03-04T13:30:00Z
token_count: 378
block_count: 6
cache_ttl: 3600s
---

## Who I Am
*(no identity/core blocks — section omitted)*

## What I Value
- I believe in clear, honest communication above persuasion [V1]
- I value conciseness — say more with less [V2]

## How I Work
- I think step-by-step through problems before answering [S1]

## Constraints
- I always respond in the language the user is writing in [C1]
- I never claim certainty when I don't have it [C2]

## Current Focus
- I am a Python developer working on AI memory systems [X1]
```

The block IDs in brackets are metadata — stripped before the prompt is passed to the LLM.

**Final system prompt text (LLM receives):**

```
## What I Value
- I believe in clear, honest communication above persuasion
- I value conciseness — say more with less

## How I Work
- I think step-by-step through problems before answering

## Constraints
- I always respond in the language the user is writing in
- I never claim certainty when I don't have it

## Current Focus
- I am a Python developer working on AI memory systems
```

**Token count: 378 / 600 budget (63% utilisation)**

---

## Computation: Layer 5 — Cache and Invalidation

```yaml
cache:
  key: "self_system_prompt"
  value: <formatted text above>
  assembled_at: 2026-03-04T13:30:00Z
  ttl: 3600  # 1 hour
  block_ids_included: [C1, C2, V1, V2, S1, X1]
  top_scores: { C1: 0.710, C2: 0.674 }

invalidation_triggers:
  1_new_self_block:
    trigger: new block consolidated with is_self = true
    action: invalidate immediately
    reason: self composition may have changed

  2_significant_score_shift:
    trigger: any included block's score changes by > 0.10
    action: invalidate and reassemble
    reason: identity priorities may have shifted

  3_included_block_pruned:
    trigger: any block in block_ids_included is pruned
    action: invalidate immediately
    reason: prompt references non-existent block

  4_ttl_expiry:
    trigger: age > ttl (1 hour)
    action: invalidate, reassemble on next request
    reason: regular refresh catches any drift

  5_explicit_request:
    trigger: lifecycle.refresh_self() called
    action: invalidate immediately
    reason: human-initiated update
```

**During a session:** Cache hit → use cached prompt. No reassembly.
**Between sessions:** TTL has likely expired → reassemble fresh.
**On new insight:** Event-based invalidation → next LLM call gets updated self.

---

## Edge Cases and Mitigations

### Edge Case 1: Empty Self at Startup

```yaml
scenario: System starts with no self-tagged blocks

problem: No candidates → frame assembly returns empty list → empty system prompt
         → LLM has no identity grounding → unpredictable behavior

mitigation:
  seed_self:
    description: "A hardcoded minimal system prompt used before any blocks are learned"
    source: "A default 'CLAUDE.md' equivalent, set at deployment"
    content: |
      ## Constraints
      - I am an AI assistant. I respond helpfully and honestly.
      - I never claim certainty I don't have.
    token_count: 48

  logic:
    if assembled_blocks.empty():
        return seed_self  # fall back to hardcoded minimum
    else:
        return formatted_prompt  # assembled from memory

  note: "seed_self is intentionally minimal. It doesn't assert expertise or
        personality — those emerge through learning."
```

### Edge Case 2: Hard Contradiction

```yaml
scenario:
  V2: "I value conciseness — say more with less"      confidence: 0.80
  V_new: "I provide comprehensive explanations"        confidence: 0.40

  # These directly contradict each other.
  # Semantic polarity: opposing
  # Similarity: 0.82 (both about response length/style)

resolution:
  winner: V2  (higher confidence 0.80 > 0.40)
  action: exclude V_new from system prompt
  log_entry:
    type: contradiction_detected
    winner: V2
    loser: V_new
    reason: "opposing semantic polarity, lower confidence"
    timestamp: 2026-03-04T14:00:00Z

  # V_new still exists in memory — it just doesn't make the prompt.
  # If user repeatedly reinforces comprehensive explanations, V_new.confidence rises.
  # Eventually it may exceed V2.confidence and displace it.
  # Identity evolution is gradual, not sudden.
```

### Edge Case 3: Budget Overflow

```yaml
scenario: 40 self-tagged blocks, 600 token budget

selection_math:
  # Budget for content: 600 - 66 (overhead) = 534 tokens
  # Average block: ~55 tokens
  # Blocks that fit: 534 / 55 ≈ 9 blocks

  # Only top 9 by SELF score make it in
  # Remaining 31 blocks exist in memory but not in the prompt

consequence:
  - System prompt reflects the 9 most identity-essential blocks
  - Lower-ranked blocks may influence WORLD/ATTENTION frames
  - If a lower-ranked block gets reinforced heavily, it may enter top-9 next refresh
  - This is correct behavior: the prompt is a curated summary, not a dump
```

### Edge Case 4: Circular Reinforcement

```yaml
scenario:
  V2 in prompt: "I value conciseness"
  → LLM gives concise answers
  → User is satisfied (reinforcement signal)
  → V2.reinforcement_count increases
  → V2.score increases
  → V2 stays in prompt at higher rank
  → LLM gives MORE concise answers (loop tightens)
  → Eventually: answers become pathologically terse

mitigations:
  1_brier_calibration:
    mechanism: Track if concise answers receive positive vs. negative feedback
    action: If user repeatedly asks "can you elaborate?", V2.confidence decreases
    result: Self-correcting loop — calibration breaks the cycle

  2_section_limits:
    mechanism: Each template section has a max bullet count (default: 3)
    action: Even if 8 value blocks exist, only 3 appear in the Values section
    result: Budget + section limits prevent any single value from dominating

  3_entropy_monitoring:
    mechanism: Track category diversity of self blocks
    action: Alert if any category accounts for > 50% of self composition
    result: Flags monocultural self early, before it becomes pathological
```

### Edge Case 5: Self-Updating from Its Own Output

```yaml
scenario:
  1. Self prompt is assembled → used for LLM call
  2. LLM generates response
  3. Agent ingests the response as new knowledge
  4. New block has high self-similarity → tagged is_self = true
  5. New self block consolidates → invalidates cache
  6. Next call reassembles self including the new block
  7. New self influences next LLM response
  8. Which may generate more self-similar content
  → Recursive self-modification loop

mitigations:
  1_temporal_separation:
    mechanism: Self-tagged blocks from the agent's own output require
              higher confidence threshold (0.80 vs 0.70 for external)
    result: Agent must "believe" its own outputs before they shape self

  2_provenance_tracking:
    mechanism: meta.source = "self_generated" tracked in block metadata
    result: Human can review and curate self-generated self blocks

  3_async_self_update:
    mechanism: Self cache updates asynchronously, not immediately
    action: Cache invalidation happens at session boundary, not mid-session
    result: Within a session, self is stable even if new self blocks consolidate
```

### Edge Case 6: Stale Context Block

```yaml
scenario:
  X1: "I am a Python developer working on AI memory systems"
       created: 6 months ago, confidence: 0.80, decay_lambda: 0.001

  New block: "I'm transitioning to Rust for performance work"
             confidence: 0.65, decay_lambda: 0.001

  # After 6 months, X1.decay_weight = e^(-0.001 × 4380h) = 0.012 → near pruning
  # New Rust block is recent, confidence growing
  # Eventually: Rust block outscores X1 → enters context section → X1 exits

  # This is correct behavior. Context should update as circumstances change.
  # The decay + scoring mechanism handles this automatically.
  # No special case needed.

note: "Durable profile (λ=0.001) was correct for identity/context blocks.
      They should change over months, not days."
```

---

## Result: The Complete System

```yaml
self_system_prompt_pipeline:

  layer_1_selection:
    mechanism: SELF frame assembly (identical to all other frames)
    inputs: [self_tagged_blocks, graph, scores]
    weights: { recency: 0.05, centrality: 0.25, confidence: 0.30,
               similarity: 0.10, reinforcement: 0.30 }
    output: ranked_block_list

  layer_2_budget:
    mechanism: Greedy token budget enforcement
    inputs: [ranked_block_list, token_budget=600]
    output: budget_constrained_blocks

  layer_3_contradiction:
    mechanism: Pairwise similarity + confidence comparison
    inputs: [budget_constrained_blocks]
    rule: if similarity > 0.75 and opposing_polarity → keep higher confidence
    output: contradiction_free_blocks

  layer_4_formatting:
    mechanism: Template-based section mapping by category subcategory
    inputs: [contradiction_free_blocks]
    sections: [Who I Am, What I Value, How I Work, Constraints, Current Focus]
    output: formatted_markdown_document

  layer_5_cache:
    mechanism: Key-value cache with TTL + event invalidation
    ttl: 3600  # 1 hour
    invalidation_triggers:
      - new self block consolidated
      - included block score changes > 0.10
      - included block pruned
      - explicit refresh
    output: cached_system_prompt

properties:
  average_token_count: 300-500
  max_token_count: 600
  stability: high (cached, session-stable)
  consistency: total (same mechanisms as all frames)
  evolvability: gradual (blocks decay, confidence calibrates)
  edge_cases_handled: 6 identified mitigations in place
```

---

## Insight

### 1. The Mechanism Is Identical; the Output Format Is Not

The critical insight for axiom consistency: **selection and scoring are unchanged**.
The SELF frame is assembled exactly like the WORLD or ATTENTION frame.
What differs is the OUTPUT PROCESSING:
- Other frames → list of blocks injected as context
- Self frame → list → format → cache → system prompt

This is the minimum necessary difference. No new storage mechanism, no new scoring path.

### 2. The Token Budget Does What Block Count Can't

A block count limit (e.g., "max 8 self blocks") is arbitrary. Some blocks are dense
(100 tokens), some are sparse (20 tokens). A token budget directly enforces the real
constraint: how much of the context window self is allowed to consume.

The budget also naturally keeps self focused. With a 600 token cap, you have room for
8-12 bullet points across 4-5 sections. This forces prioritization — which is healthy.

### 3. Stability Is a Feature, Not a Bug

Consistent behavior within a session requires a stable self. If self changes on every
query (because scoring changes slightly), users get inconsistent responses.
Caching with event-driven invalidation gives both:
- Stability within a session (cache hit)
- Evolution across sessions (TTL + trigger invalidation)

### 4. Identity Sections Are Not Optional

The template has sections (`Constraints`, `Values`, `Style`, `Context`). The category
subcategory on each block routes it to the right section. This means:
- Behavioral constraints ALWAYS appear in a `Constraints` section
- Values ALWAYS appear in a `Values` section
- Even if scoring puts a constraint block at rank 6, it still goes under Constraints

This is important: **category subcategory overrides pure score ranking for section placement**.
Score determines which blocks are included; category determines where they appear.

### 5. The Seed Self Is Not an Afterthought

A minimal hardcoded seed self must exist before anything is learned. This is the
equivalent of the `CLAUDE.md` default. Without it, a new deployment has no identity
grounding at all. The seed self is intentionally minimal — it doesn't assert expertise
or personality. Those emerge through learning and reinforcement.

### 6. Circular Reinforcement Is Managed, Not Prevented

Blocking the feedback loop (self → behavior → reinforcement → self) would also block
learning. The loop is healthy when calibrated. Brier scoring, section limits, and
entropy monitoring are the guardrails, not a hard block.

---

## What This Changes in the Architecture

```python
# Current architecture (implicit)
class SelfFrame:
    def assemble(self) -> List[MemoryBlock]:
        # standard frame assembly
        ...

# New architecture
class SelfFrame:
    def assemble(self) -> List[MemoryBlock]:
        # standard frame assembly — UNCHANGED
        ...

    def format_as_system_prompt(self) -> str:
        # NEW: wraps assemble() with token budget + contradiction check + template
        blocks = self.assemble()
        blocks = enforce_token_budget(blocks, budget=600)
        blocks = resolve_contradictions(blocks)
        return apply_template(blocks)

    def get_cached_prompt(self) -> str:
        # NEW: cache layer
        if cache.valid():
            return cache.get()
        prompt = self.format_as_system_prompt()
        cache.set(prompt, ttl=3600)
        return prompt
```

The scoring and selection machinery doesn't change. Two new methods added.
The rest of the SELF frame implementation is unchanged.

---

## Variations

- [ ] What if identity/constraint blocks are always forced into the prompt regardless of score?
- [ ] What if section limits (max 3 bullets each) are enforced? Which blocks get cut?
- [ ] What if the formatting step uses an LLM synthesis call instead of template? When is that worth the cost?
- [ ] Test: what happens if the token budget is 300 tokens instead of 600? Which blocks survive?
- [ ] Can we use entropy monitoring to detect when self is becoming monocultural?
- [ ] What's the right TTL? Test: 15 min vs. 1 hour vs. session-scoped
- [ ] Should the seed self be stored as special is_self blocks (with permanent λ) or hardcoded?
