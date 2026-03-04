# Title: The Four Lifecycle Operations

## Status: complete

## Question

How do the four system operations — learn, consolidate, curate, recall — work as
discrete actions? What triggers each one, what does each do step by step, and how
do blocks flow through the system from raw input to pruned memory to recalled output?

---

## Background

The AMGS operates as a pipeline of four discrete actions. An entity (LLM, user, CLI,
SDK) drives the system by calling these operations. Nothing happens automatically —
each action is explicit and traceable.

```
learn()  →  INBOX
consolidate()  →  INBOX → MEMORY (with embeddings, edges, scoring)
curate()  →  MEMORY → MEMORY (decay, prune, promote)
recall()   →  MEMORY → Context Frame (for LLM call)
```

Each operation is independent. Each can be called separately or in sequence.
Each has a defined trigger condition and a defined effect on block state.

---

## The Atomic Unit: The Markdown Block

Before exploring operations, define the input format. Every block the system learns
is a single discrete concept expressed as a short markdown snippet:

```markdown
## Python asyncio patterns

Asyncio uses an event loop to schedule coroutines. Use `async def` to define
coroutines and `await` to yield control. Blocking calls must be wrapped with
`asyncio.run_in_executor` to avoid stalling the loop.
```

**Constraints:**
- H2 title: 4–10 words, plain English, no jargon padding
- Body: 1–5 sentences, ~50–200 tokens, one concept only
- One block = one idea. If you need two ideas, call learn() twice.

This constraint is intentional. Atomic blocks:
- Enable precise decay (each concept ages independently)
- Enable precise retrieval (blocks don't carry irrelevant cargo)
- Enable precise graph edges (similarity is between clean concepts, not mixed blobs)
- Enable precise self-alignment (one concept, one alignment score)

---

## Setup

```yaml
# Starting state of the system before any operations

inbox: []  # INBOX context frame — staging area for unprocessed blocks

memory:
  blocks:
    M1:
      title: "Python list comprehensions"
      content: "List comprehensions provide a concise way to create lists..."
      embedding: [0.82, 0.14, 0.61, ...]  # symbolic: vector in semantic space
      category: knowledge
      category_subcategory: knowledge/technical
      confidence: 0.72
      decay_lambda: 0.01   # standard decay profile
      is_self: false
      is_constitutional: false
      reinforcement_count: 8
      hours_since_reinforcement: 120  # 5 days ago
      self_alignment: 0.55
      created_at: t-720h

    M2:
      title: "Prefer explicit over implicit code"
      content: "Code should express its intent clearly. Implicit magic..."
      embedding: [0.21, 0.88, 0.33, ...]
      category: knowledge
      category_subcategory: knowledge/principle
      confidence: 0.85
      decay_lambda: 0.001  # durable — core principle
      is_self: true
      is_constitutional: false
      reinforcement_count: 24
      hours_since_reinforcement: 48
      self_alignment: 0.91
      created_at: t-2160h  # 90 days ago

    M3:
      title: "Database connection pooling"
      content: "Connection pools reuse database connections instead of..."
      embedding: [0.45, 0.22, 0.78, ...]
      category: knowledge
      category_subcategory: knowledge/technical
      confidence: 0.40
      decay_lambda: 0.01
      is_self: false
      is_constitutional: false
      reinforcement_count: 1
      hours_since_reinforcement: 600  # 25 days ago
      self_alignment: 0.30
      created_at: t-600h

  edges:
    - from: M1
      to: M2
      type: relates_to
      weight: 0.41  # low-moderate similarity

system_config:
  prune_threshold: 0.05          # decay_weight below this → prune
  inbox_consolidate_threshold: 5  # consolidate when inbox reaches N blocks
  consolidate_interval_hours: 4   # OR consolidate if any blocks and 4h elapsed
  curate_interval_hours: 168      # curate weekly (active hours)
  similarity_edge_threshold: 0.60 # create edge if embedding similarity >= 0.60
  top_k: 5                        # max blocks in a context frame
```

---

## Operation 1: learn()

### What it is

A fast, non-blocking write. The caller provides a markdown block. The system
validates and pushes it to INBOX. No embeddings. No scoring. No self consultation.
The learn action must be instant — it can be called in a hot path (mid-conversation,
mid-session) without delay.

### Interface

```bash
# CLI
amgs learn "## Python asyncio patterns\n\nAsyncio uses an event loop..."

# API
POST /memory/learn
Content-Type: application/json
{ "content": "## Python asyncio patterns\n\nAsyncio uses an event loop..." }

# SDK
ms.learn("## Python asyncio patterns\n\nAsyncio uses an event loop...")
```

### What learn() does step by step

**Input:** raw markdown string

**Step 1 — Parse**
Extract the H2 title and body. Validate structure.

```
title: "Python asyncio patterns"
body: "Asyncio uses an event loop to schedule coroutines..."
token_count: ~65 tokens  (estimated without real tokenizer)
```

If no H2 title found → reject with error: "Block must begin with an H2 title (## Title)"
If body is empty → reject with error: "Block body is required"
If token_count > 300 → reject with warning: "Block too large. Split into multiple learn() calls."

**Step 2 — Shallow dedup**
Check INBOX for exact title match (string equality only — not semantic).
This prevents the trivially obvious case of double-submitting the same block.

```
INBOX titles currently: []
Match found? No → proceed
```

No semantic dedup at this stage. Semantic dedup happens at consolidation time,
where we have embeddings for all existing memory blocks to compare against.

**Step 3 — Create INBOX block**
Create an unprocessed block record with minimal metadata.

```yaml
I1:
  title: "Python asyncio patterns"
  content: "## Python asyncio patterns\n\nAsyncio uses an event loop..."
  status: pending             # not yet processed by consolidation
  received_at: t+0h
  source: api                 # or: cli, sdk, llm, user
```

**Step 4 — Push to INBOX**
```
INBOX: [I1]
```

**Step 5 — Check consolidation trigger**
After pushing:
- inbox_count = 1
- inbox_consolidate_threshold = 5
- 1 < 5 → do NOT trigger consolidation automatically

If called via CLI with `--consolidate-now` flag → trigger immediately.
Otherwise, consolidation will be triggered on schedule or at threshold.

**Result after learn():**
```
INBOX: [I1]  (1 block pending)
MEMORY: unchanged
```

**What learn() does NOT do:**
- Does not generate embeddings (too slow for hot path)
- Does not consult self (not yet)
- Does not create edges (no embeddings yet)
- Does not score the block
- Does not modify any existing block

---

### Calling learn() four more times

To show the consolidation trigger, we submit four more blocks:

```
learn("## Generator functions\n\nGenerators use yield to produce values...")
learn("## Asyncio task cancellation\n\nTasks can be cancelled with task.cancel()...")
learn("## Python context managers\n\nContext managers use __enter__ and __exit__...")
learn("## Prefer composition over inheritance\n\nFavouring composition keeps classes small...")
```

After five calls:
```
INBOX: [I1, I2, I3, I4, I5]  (5 blocks pending)
inbox_count = 5 = inbox_consolidate_threshold
→ TRIGGER consolidation automatically
```

---

## Operation 2: consolidate()

### What it is

The heavy processing step. Moves INBOX blocks into MEMORY. This is where embeddings
are generated, semantic dedup runs, graph edges are created, self-alignment is scored,
and decay profiles are assigned. Consolidation is the system "digesting" what it learned.

### Trigger conditions (either)
1. `inbox_count >= inbox_consolidate_threshold` (5 blocks) — triggered after learn()
2. `time_since_last_consolidation >= consolidate_interval_hours` (4h) AND `inbox_count > 0` — scheduled

### What consolidate() does step by step

**For each INBOX block, in order:**

---

#### Processing I1: "Python asyncio patterns"

**Step 1 — Generate embedding**
Run the block content through the embedding model.

```
embedding(I1) = [0.78, 0.19, 0.82, ...]  # symbolic vector
```

(In production: call to embedding API. In simulation: assign symbolic vector
with stated similarity assumptions.)

**Step 2 — Semantic dedup against MEMORY**
Compare I1's embedding to all blocks in MEMORY.

```
similarity(I1, M1: "Python list comprehensions") = 0.71
similarity(I1, M2: "Prefer explicit over implicit") = 0.18
similarity(I1, M3: "Database connection pooling") = 0.29
```

Dedup threshold: similarity > 0.92 → reject as near-duplicate.
Max similarity = 0.71 (M1). Well below 0.92. → PROCEED.

**Step 3 — Assign category**
Either extracted from block metadata (if caller supplied it) or inferred from content.

```
category: knowledge
category_subcategory: knowledge/technical
```

**Step 4 — Compute self-alignment**
How much does this block relate to the current SELF context?

SELF frame assembles the current self context (top self-tagged blocks).
Current dominant self themes (from M2 "Prefer explicit over implicit", reinforcement=24):
- "explicit code", "clear intent", "compositional thinking"

Similarity of I1 to self themes:
```
self_alignment(I1) = cosine(embedding(I1), embedding(SELF_context))
                   = 0.62  # moderate — asyncio is relevant to technical practice
```

This is above the soft alignment threshold (0.50). → assign standard decay,
slight confidence boost.

**Step 5 — Assign decay profile**
Rules (from exploration 005):
- is_constitutional: permanent (λ=0.00001)
- is_self AND self_alignment > 0.80: durable (λ=0.001)
- self_alignment > 0.50: standard (λ=0.01)
- self_alignment <= 0.50: standard (λ=0.01) — no penalty
- category == observation/ephemeral: short (λ=0.03) or ephemeral (λ=0.1)

I1: self_alignment=0.62 → standard (λ=0.01)

**Step 6 — Assign initial confidence**
Base confidence for new blocks: 0.50 (neutral, unvalidated)
Self-alignment boost: +0.05 × self_alignment = +0.05 × 0.62 = +0.031

```
confidence(I1) = 0.50 + 0.031 = 0.531  → round to 0.53
```

**Step 7 — Create edges to similar MEMORY blocks**
Edge threshold: similarity >= 0.60

```
similarity(I1, M1) = 0.71 >= 0.60 → CREATE EDGE
similarity(I1, M2) = 0.18 < 0.60  → no edge
similarity(I1, M3) = 0.29 < 0.60  → no edge
```

New edge:
```yaml
- from: I1 (will become M4)
  to: M1
  type: relates_to
  weight: 0.71
```

**Step 8 — Write to MEMORY**
I1 becomes M4 with full metadata:

```yaml
M4:
  title: "Python asyncio patterns"
  content: "## Python asyncio patterns\n\nAsyncio uses an event loop..."
  embedding: [0.78, 0.19, 0.82, ...]
  category: knowledge
  category_subcategory: knowledge/technical
  confidence: 0.53
  decay_lambda: 0.01
  is_self: false
  is_constitutional: false
  reinforcement_count: 0
  hours_since_reinforcement: 0   # just created
  self_alignment: 0.62
  created_at: t+0h
```

---

#### Processing I2–I5 (summary)

Applying the same pipeline to each remaining INBOX block:

| Block | Title | self_alignment | λ | confidence | Edge to |
|-------|-------|---------------|---|------------|---------|
| I2→M5 | Generator functions | 0.58 | 0.01 | 0.53 | M1(0.68), M4(0.74) |
| I3→M6 | Asyncio task cancellation | 0.61 | 0.01 | 0.53 | M4(0.81) |
| I4→M7 | Python context managers | 0.52 | 0.01 | 0.52 | M1(0.63) |
| I5→M8 | Prefer composition over inheritance | 0.84 | 0.001 | 0.57 | M2(0.73) |

Note on M8: "Prefer composition over inheritance" has self_alignment=0.84 (above 0.80
AND is being treated as a self-relevant principle). Gets durable decay (λ=0.001) and
a higher confidence boost.

M8 also connects to M2 ("Prefer explicit over implicit") — two principle-type blocks
forming a small cluster of coding philosophy.

---

**Step 9 — Clear INBOX**
After all 5 blocks are processed:
```
INBOX: []
```

**Step 10 — Update graph centrality (lightweight)**
Recompute degree centrality (edge count / total possible edges) for affected blocks.

```
Edges added this consolidation:
  M4-M1 (weight 0.71)
  M5-M1 (weight 0.68)
  M5-M4 (weight 0.74)
  M6-M4 (weight 0.81)
  M7-M1 (weight 0.63)
  M8-M2 (weight 0.73)

Updated degree centrality (edges / (n-1) where n=8 blocks):
  M1: 4 edges → 4/7 = 0.571  (was 1/3 = 0.333 — now a hub)
  M4: 3 edges → 3/7 = 0.429  (new asyncio hub)
  M2: 2 edges → 2/7 = 0.286
  M5: 2 edges → 2/7 = 0.286
  M6: 1 edge  → 1/7 = 0.143
  M7: 1 edge  → 1/7 = 0.143
  M8: 1 edge  → 1/7 = 0.143
  M3: 0 edges → 0/7 = 0.000  (isolated — no connections)
```

M1 ("Python list comprehensions") has become the most central block. It connects to
old memory (M1 existed before) and all new asyncio/Python blocks. Centrality will
boost its scores at retrieval time.

**Result after consolidate():**

```yaml
INBOX: []  (cleared)
MEMORY:
  blocks: [M1, M2, M3, M4, M5, M6, M7, M8]
  edges: 7 total
```

---

## Operation 3: curate()

### What it is

The maintenance cycle. Curate applies decay, computes scores, prunes blocks
below threshold, and reinforces high-value blocks. It does not move blocks —
it updates their state in place.

Think of curate as the system "sleeping on it." Over time, unused knowledge
fades. Frequently reinforced, high-self-alignment blocks solidify.

### Trigger conditions
- Scheduled: every 168 active hours (weekly) — default
- On demand: `amgs curate` or `POST /memory/curate`
- Can be triggered by block count: if memory exceeds soft cap, curate prunes

### Simulating curate() at t+300h (12.5 days after consolidation)

Three hundred active hours have passed. No retrieval occurred in this period
(simulating a period of low system use — partial holiday scenario).

**Step 1 — Apply decay to all blocks**

```
decay_weight = e^(-λ × hours_since_reinforcement)
```

| Block | λ | hours_idle | decay_weight | Above 0.05? |
|-------|---|------------|--------------|-------------|
| M1 | 0.01 | 420 (120+300) | e^(-0.01×420) = e^(-4.2) = 0.015 | NO → prune |
| M2 | 0.001 | 348 (48+300) | e^(-0.001×348) = e^(-0.348) = 0.706 | yes |
| M3 | 0.01 | 900 (600+300) | e^(-0.01×900) = e^(-9.0) = 0.0001 | NO → prune |
| M4 | 0.01 | 300 | e^(-0.01×300) = e^(-3.0) = 0.050 | BORDERLINE |
| M5 | 0.01 | 300 | e^(-0.01×300) = 0.050 | BORDERLINE |
| M6 | 0.01 | 300 | e^(-0.01×300) = 0.050 | BORDERLINE |
| M7 | 0.01 | 300 | e^(-0.01×300) = 0.050 | BORDERLINE |
| M8 | 0.001 | 300 | e^(-0.001×300) = e^(-0.3) = 0.741 | yes |

**Note on borderline blocks (decay_weight = 0.050, prune_threshold = 0.05):**
Exactly at threshold. Design decision: at-threshold blocks survive (prune = strictly below).
This is the "holiday" problem from exploration 005: standard blocks barely survive
300 hours idle. At 310 hours, they would drop below threshold.

This confirms exploration 005's finding: for a system used weekly, standard decay (λ=0.01)
is too aggressive. Session-aware decay would have paused the clock during idle periods,
keeping these blocks alive.

**Step 2 — Score all surviving blocks**
Use SELF scoring formula (used during curate to determine which blocks to reinforce):

```
Score = 0.05×Recency + 0.25×Centrality + 0.30×Confidence + 0.10×Similarity + 0.30×Reinforcement
```

During curate, Similarity is 0 (no query). Recency uses decay_weight as proxy.

| Block | Recency | Centrality | Confidence | Similarity | Reinforcement | Score |
|-------|---------|------------|------------|------------|---------------|-------|
| M2 | 0.706 | 0.286 | 0.85 | 0.00 | log(25)/log(26)=0.976 | 0.05×0.706 + 0.25×0.286 + 0.30×0.85 + 0.00 + 0.30×0.976 |
| M4 | 0.050 | 0.429 | 0.53 | 0.00 | log(1)/log(2)=0.000 | |
| M5 | 0.050 | 0.286 | 0.53 | 0.00 | 0.000 | |
| M6 | 0.050 | 0.143 | 0.53 | 0.00 | 0.000 | |
| M7 | 0.050 | 0.143 | 0.52 | 0.00 | 0.000 | |
| M8 | 0.741 | 0.143 | 0.57 | 0.00 | 0.000 | |

Computing M2:
```
Score(M2) = 0.05×0.706 + 0.25×0.286 + 0.30×0.85 + 0 + 0.30×0.976
          = 0.035 + 0.072 + 0.255 + 0 + 0.293
          = 0.655
```

Computing M4 (new, unreinforced):
```
Score(M4) = 0.05×0.050 + 0.25×0.429 + 0.30×0.53 + 0 + 0.30×0.000
          = 0.003 + 0.107 + 0.159 + 0 + 0
          = 0.269
```

Computing M8 (new, high alignment, durable):
```
Score(M8) = 0.05×0.741 + 0.25×0.143 + 0.30×0.57 + 0 + 0.30×0.000
          = 0.037 + 0.036 + 0.171 + 0 + 0
          = 0.244
```

| Block | Score | Rank |
|-------|-------|------|
| M2 | 0.655 | 1st |
| M4 | 0.269 | 2nd (centrality saves it) |
| M5 | 0.222 | 3rd |
| M8 | 0.244 | 2nd/3rd |
| M6 | 0.184 | 4th |
| M7 | 0.183 | 5th |

**Step 3 — Prune below threshold**

M1: decay_weight=0.015 < 0.05 → **PRUNE**
M3: decay_weight=0.0001 < 0.05 → **PRUNE**

Before pruning M1: update edges. M1 had edges to M4, M5, M7.
Remove those edges. Recompute centrality:

```
After pruning M1:
  M4: loses 1 edge → 2 edges → 2/5 = 0.400
  M5: loses 1 edge → 1 edge  → 1/5 = 0.200
  M7: loses 1 edge → 0 edges → 0/5 = 0.000  (now isolated)
```

M3 had 0 edges — no edge cleanup needed.

**Step 4 — Reinforce top-scoring blocks**
Blocks in top quartile by score get a curate-pass reinforcement bump.
This simulates "the system valued this block" — a weak form of usage reinforcement.

Top quartile of 6 surviving blocks = top 2:
- M2 (score 0.655): reinforcement_count 24→25, hours_since_reinforcement reset to 0
- M4 (score 0.269): reinforcement_count 0→1, hours_since_reinforcement reset to 0

**Step 5 — Update confidence based on persistence**
Blocks that survive a curate pass without being pruned get a small confidence bump
(+0.01) as a signal that they've "stood the test of time."

```
M2: 0.85 → 0.86 (capped at 1.0)
M4: 0.53 → 0.54
M5: 0.53 → 0.54
M6: 0.53 → 0.54
M7: 0.52 → 0.53
M8: 0.57 → 0.58
```

**Result after curate():**

```yaml
MEMORY:
  blocks:
    M2: confidence=0.86, reinforcement_count=25, hours_since_reinforcement=0
    M4: confidence=0.54, reinforcement_count=1, hours_since_reinforcement=0
    M5: confidence=0.54, hours_since_reinforcement=300
    M6: confidence=0.54, hours_since_reinforcement=300
    M7: confidence=0.53, hours_since_reinforcement=300, edges=0 (isolated)
    M8: confidence=0.58, hours_since_reinforcement=300
  pruned: [M1, M3]
  edges: 3 remaining (M4-M5, M4-M6, M8-M2)
```

M1 ("Python list comprehensions") was pruned despite being a useful block.
This is the holiday problem again. M7 ("Python context managers") is now isolated.
This is a known risk of session-unaware decay and motivates the session-aware
approach from exploration 005.

---

## Operation 4: recall()

### What it is

A synchronous, query-driven assembly of a context frame. recall() scores all
MEMORY blocks against a query using frame-specific weights, returns top-K, and
reinforce every returned block (mandatory — exploration 001).

### Interface

```bash
# CLI
amgs recall --query "how do I handle async operations" --frame attention --top-k 5

# API
GET /memory/recall?query=how+do+I+handle+async+operations&frame=attention&top_k=5

# SDK
results = ms.recall("how do I handle async operations", frame="attention", top_k=5)
```

### What recall() does step by step

**Query:** "how do I handle async operations"
**Frame:** ATTENTION
**Top-K:** 5

**Step 1 — Embed the query**
```
embedding(query) = [0.77, 0.22, 0.85, ...]  # similar to asyncio-related blocks
```

**Step 2 — Compute similarity of query to each MEMORY block**

```
similarity(query, M2: "Prefer explicit") = 0.21
similarity(query, M4: "Asyncio patterns") = 0.88
similarity(query, M5: "Generator functions") = 0.72
similarity(query, M6: "Asyncio task cancellation") = 0.91
similarity(query, M7: "Context managers") = 0.41
similarity(query, M8: "Prefer composition") = 0.19
```

**Step 3 — Compute ATTENTION frame scores**

ATTENTION weights: rec=0.25, cen=0.15, conf=0.15, sim=0.35, reinf=0.10

At t+300h post-curate, hours_since_reinforcement for non-reinforced blocks is 300h:
- M4 and M2 were reinforced during curate → hours=0 → Recency = e^(-0.01×0) = 1.0 (M4) and e^(-0.001×0) = 1.0 (M2)
- Others: 300h → e^(-0.01×300) = 0.050

Updated centrality (post-prune): M4=0.400, M5=0.200, M6=0.200, M2=0.200, M7=0.000, M8=0.143

```
Score = 0.25×Recency + 0.15×Centrality + 0.15×Confidence + 0.35×Similarity + 0.10×Reinforcement
```

| Block | Rec | Cen | Conf | Sim | Reinf | Score |
|-------|-----|-----|------|-----|-------|-------|
| M4 | 1.000 | 0.400 | 0.54 | 0.88 | log(2)/log(3)=0.631 | 0.25×1.0+0.15×0.4+0.15×0.54+0.35×0.88+0.10×0.631 |
| M5 | 0.050 | 0.200 | 0.54 | 0.72 | 0.000 | |
| M6 | 0.050 | 0.200 | 0.54 | 0.91 | 0.000 | |
| M7 | 0.050 | 0.000 | 0.53 | 0.41 | 0.000 | |
| M8 | 0.050 | 0.143 | 0.58 | 0.19 | 0.000 | |
| M2 | 1.000 | 0.200 | 0.86 | 0.21 | 0.976 | |

Computing M4:
```
Score(M4) = 0.25×1.0 + 0.15×0.40 + 0.15×0.54 + 0.35×0.88 + 0.10×0.631
          = 0.250 + 0.060 + 0.081 + 0.308 + 0.063
          = 0.762
```

Computing M6 ("Asyncio task cancellation" — highest similarity):
```
Score(M6) = 0.25×0.050 + 0.15×0.200 + 0.15×0.54 + 0.35×0.91 + 0.10×0.000
          = 0.013 + 0.030 + 0.081 + 0.319 + 0
          = 0.443
```

Computing M5 ("Generator functions"):
```
Score(M5) = 0.25×0.050 + 0.15×0.200 + 0.15×0.54 + 0.35×0.72 + 0
          = 0.013 + 0.030 + 0.081 + 0.252
          = 0.376
```

Computing M2 ("Prefer explicit"):
```
Score(M2) = 0.25×1.0 + 0.15×0.200 + 0.15×0.86 + 0.35×0.21 + 0.10×0.976
          = 0.250 + 0.030 + 0.129 + 0.074 + 0.098
          = 0.581
```

Computing M7 ("Context managers"):
```
Score(M7) = 0.25×0.050 + 0 + 0.15×0.53 + 0.35×0.41 + 0
          = 0.013 + 0 + 0.080 + 0.144
          = 0.237
```

Computing M8 ("Prefer composition"):
```
Score(M8) = 0.25×0.050 + 0.15×0.143 + 0.15×0.58 + 0.35×0.19 + 0
          = 0.013 + 0.021 + 0.087 + 0.067
          = 0.188
```

**Rankings:**

| Rank | Block | Score | Why |
|------|-------|-------|-----|
| 1st | M4 — Asyncio patterns | 0.762 | High sim + recent curate reinforcement + central hub |
| 2nd | M2 — Prefer explicit | 0.581 | Very high reinforcement + recent + general relevance |
| 3rd | M6 — Asyncio cancellation | 0.443 | Highest raw similarity, but zero reinforcement and low recency |
| 4th | M5 — Generator functions | 0.376 | Moderate similarity, adjacent to asyncio |
| 5th | M7 — Context managers | 0.237 | Low similarity, isolated (no edges), weak |

**Step 4 — Reinforce returned blocks (MANDATORY)**

Every block returned in the context frame must be reinforced:

```
For each block in [M4, M2, M6, M5, M7]:
  block.reinforcement_count += 1
  block.hours_since_reinforcement = 0
```

This is non-negotiable (exploration 001). The act of retrieval is the reinforcement.
Without this, blocks die during active use. M6, M5, M7 — which had decay_weight = 0.050
and were near the prune threshold — are now reset: hours_since_reinforcement=0,
decay_weight will be 1.0 at next check. They have been saved by use.

**Step 5 — Return context frame**

```yaml
context_frame:
  type: ATTENTION
  query: "how do I handle async operations"
  blocks:
    - M4: "Python asyncio patterns"
    - M2: "Prefer explicit over implicit code"
    - M6: "Asyncio task cancellation"
    - M5: "Generator functions"
    - M7: "Python context managers"
  assembled_at: t+300h
```

This frame is passed as context to the LLM for its response.

---

## Result: Complete System State

```yaml
# After: learn(×5) → consolidate() → curate() at t+300h → recall()

MEMORY:
  blocks:
    M2:
      title: "Prefer explicit over implicit code"
      confidence: 0.86
      decay_lambda: 0.001
      reinforcement_count: 26  # 24 original + 1 curate + 1 retrieve
      hours_since_reinforcement: 0
      is_self: true

    M4:
      title: "Python asyncio patterns"
      confidence: 0.54
      decay_lambda: 0.01
      reinforcement_count: 2   # 1 curate + 1 retrieve
      hours_since_reinforcement: 0

    M5:
      title: "Generator functions"
      confidence: 0.54
      decay_lambda: 0.01
      reinforcement_count: 1   # 1 retrieve
      hours_since_reinforcement: 0

    M6:
      title: "Asyncio task cancellation"
      confidence: 0.54
      decay_lambda: 0.01
      reinforcement_count: 1   # 1 retrieve
      hours_since_reinforcement: 0

    M7:
      title: "Python context managers"
      confidence: 0.53
      decay_lambda: 0.01
      reinforcement_count: 1   # 1 retrieve
      hours_since_reinforcement: 0

    M8:
      title: "Prefer composition over inheritance"
      confidence: 0.58
      decay_lambda: 0.001
      reinforcement_count: 0
      hours_since_reinforcement: 300   # not retrieved — still aging

  pruned: [M1, M3]

  edges: [M4-M5, M4-M6, M8-M2]

INBOX: []
```

---

## Insight

### The pipeline is clean and separation is correct

Four discrete operations, each with a single responsibility:

| Operation | Responsibility | Speed | When |
|-----------|---------------|-------|------|
| learn() | Accept input, validate, push to INBOX | Instant | Hot path |
| consolidate() | Embed, dedup, edge, align, move to MEMORY | Slow | Scheduled/threshold |
| curate() | Decay, score, prune, reinforce top blocks | Medium | Weekly schedule |
| recall() | Score against query, return frame, reinforce | Fast | Every LLM call |

This separation matters. learn() can be called mid-conversation with no lag.
consolidate() can run in the background. curate() can run overnight.
recall() is always synchronous but is read-only except for reinforcement.

### Reinforcement during recall() is crucial

M6 ("Asyncio task cancellation") had decay_weight=0.050 — exactly at the prune
threshold. Without the recall() reinforcement, the next curate() would have
pruned it. With reinforcement, it resets to decay_weight=1.0 and survives.

This confirms exploration 001: retrieval reinforcement is not optional.
The system MUST instrument every recall() call to reinforce returned blocks.

### Curate() saves the most-used blocks, not the most recent

M2 ("Prefer explicit") was written 90 days ago but scores highest during curate()
because reinforcement_count=24 makes its Reinforcement component = 0.976.
Age doesn't kill well-used blocks. Only idle neglect kills them.

### Centrality shapes retrieval meaningfully

M4 ("Asyncio patterns") ranked 1st despite M6 having higher raw similarity (0.88 vs 0.91).
The difference: M4 is a hub (centrality=0.400), M6 is a leaf (centrality=0.200).
Centrality acts as a signal of structural importance. Hub blocks bring useful context
even when they're not the closest match.

### The holiday problem is real

M1 ("Python list comprehensions") — a useful, well-connected block (centrality=0.571
before pruning) — was pruned at t+300h because no retrieval reinforced it.
If the system was idle for 12.5 days (300 active hours), the knowledge was lost.
Session-aware decay (exploration 005) would have preserved it by pausing
the clock during idle periods.

### M8 is at risk

"Prefer composition over inheritance" was not retrieved in this session. Its
hours_since_reinforcement=300 and decay_weight=0.741 (safe now due to durable λ=0.001),
but without retrieval, it will age. Its durable decay profile buys it roughly 29 days
before it hits the prune threshold. This is correct behaviour — it's a principle
block and should persist longer than technical knowledge.

---

## Design Decisions from this Exploration

| Decision | Rationale |
|----------|-----------|
| learn() is instant, no embeddings | Can be called in hot path without latency |
| consolidate() is the heavyweight step | Embeddings, edges, scoring all in one async batch |
| curate() reinforces top-scoring blocks | Prevents good-but-unretrieved blocks from dying |
| recall() MUST reinforce returned blocks | Non-negotiable: retrieval is the reinforcement event |
| Consolidation triggered by count OR time | Prevents INBOX from growing unbounded |
| Prune is strictly-below threshold | At-threshold blocks survive (one more curate might reinforce them) |

---

## Confirmed Decisions (from post-exploration review)

- [x] consolidate() DOES check INBOX for semantic duplicates before comparing to MEMORY.
      Two near-duplicate INBOX blocks are resolved before either reaches MEMORY.
- [x] `forget()` operation EXISTS — explicit deletion by block ID or title.
      Curate() handles natural decay; forget() handles deliberate removal.
- [x] Operation name: `recall()` not `retrieve()` — simpler, more intuitive.

See **exploration 009** for reasoning on:
- Near-duplicate handling (merge vs. forget + new block)
- curate() scheduling (the holiday problem applied to maintenance)

## Open Questions

- [ ] What is the right inbox_consolidate_threshold? (5 vs. 10 vs. 20)
- [ ] Should curate() persist a log of pruned blocks for audit/recovery?
- [ ] Should recall() reinforcement be proportional to rank?
      (Rank-1 block gets +2, rank-5 gets +1 — rather than flat +1 for all)

---

## Variations

- [ ] What if consolidation discovers two INBOX blocks are near-duplicates of each other
      (before comparing to MEMORY)? Which wins?
- [ ] What if recall() is called with frame=SELF instead of ATTENTION?
      How do scores shift?
- [ ] What if inbox_consolidate_threshold=1 (immediate consolidation)?
      What does that cost? What does it gain?
- [ ] What if curate() runs daily instead of weekly?
      Which blocks survive longer? Which die faster?
