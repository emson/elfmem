# elfmem Design Simulation

A comprehensive document-driven specification system for **elfmem** (ELF Memory),
an adaptive, self-aware memory system for LLM agents.

Instead of writing code immediately, we reason through the system's behavior
using structured markdown files with explicit mathematical computation and design reasoning.

## How It Works

The "execution engine" is Claude reasoning over documents. Every computation
is explicit, auditable, and editable. This is the **whiteboard** to a
Python sim's calculator.

### The Feedback Loop

```
1. Write a micro-scenario (question + setup + inline YAML)
2. Claude "runs" it (applies formulas, reasons through transitions)
3. Results are written into the file
4. Insights captured, design decisions recorded
5. Repeat with variations
```

### What Can Be Simulated in Documents

| Works Well                        | Needs Approximation              | Needs Code            |
|-----------------------------------|----------------------------------|-----------------------|
| Decay over time (exact math)      | Semantic similarity (use 0-1)    | Real embeddings       |
| Scoring formula (exact math)      | Graph centrality (small graphs)  | PageRank at scale     |
| State transitions (lifecycle)     | Token budget estimation          | Actual tokenization   |
| Frame assembly logic              | Composite frame merging          | Large-scale retrieval |
| Edge case reasoning               | Entropy calculations             | Performance profiling |

Convention: when we can't compute exactly, we use **symbolic values** —
explicit numbers (0.8, 0.4, 0.1) with stated assumptions.

---

## Phases

### Phase 1: Explorations (current)

Micro-scenarios in `explorations/`. Each file is a self-contained thought
experiment: small setup, one question, worked computation, insight.

**Goal:** Rapidly test individual concepts. Build intuition. Find design gaps.

### Phase 2: Playgrounds (after ~15-20 explorations)

Organized by subsystem in `playgrounds/`. Each playground has a spec with
test cases. Patterns from Phase 1 are extracted and formalized.

**Goal:** Solidify the design. Write assertion-based test cases.

### Phase 3: Executable Specs (when design stabilizes)

Documents in `specs/` that map 1:1 to Python modules. Each spec contains
the type definitions, algorithms, worked examples, test cases, and edge
cases needed to generate the real code.

**Goal:** Generate production code and tests directly from specs.

---

## How to Write an Exploration

### File Naming

```
sim/explorations/NNN_short_name.md
```

Number sequentially. Name describes the question being explored.

### Structure

Every exploration follows the same pattern:

```markdown
# Title: The Question Being Asked

## Status: [draft | running | complete | superseded]

## Question
One clear question this exploration answers.

## Setup
Inline YAML defining the minimal state needed.
Use the SMALLEST possible system (3-5 blocks).

## Computation
Step-by-step worked math. Show every intermediate value.
Use the formulas from the architecture spec.

## Result
The answer. What state does the system end up in?

## Insight
What did we learn? How does this affect the design?

## Variations
What if we changed X? (Seeds the next exploration.)
```

### Key Formulas

These are the formulas you'll use repeatedly:

**Decay:**
```
decay_weight(t) = e^(-λ × t)
half_life = ln(2) / λ ≈ 0.693 / λ
```

**Composite Score:**
```
Score = w1×Recency + w2×Centrality + w3×Confidence + w4×Similarity + w5×Reinforcement
```

**Default Scoring Weights:**

| Frame     | w1 Rec | w2 Cen | w3 Conf | w4 Sim | w5 Reinf |
|-----------|--------|--------|---------|--------|----------|
| SELF      | 0.05   | 0.25   | 0.30    | 0.10   | 0.30     |
| ATTENTION | 0.25   | 0.15   | 0.15    | 0.35   | 0.10     |
| SHORT_TERM| 0.50   | 0.05   | 0.10    | 0.20   | 0.15     |
| WORLD     | 0.10   | 0.30   | 0.25    | 0.25   | 0.10     |
| TASK      | 0.15   | 0.10   | 0.15    | 0.45   | 0.15     |
| INBOX     | 0.60   | 0.00   | 0.10    | 0.20   | 0.10     |

**Decay Profiles:**

| Profile    | λ        | Half-life     |
|------------|----------|---------------|
| ephemeral  | 0.1      | ~6.9 hours    |
| short      | 0.03     | ~23 hours     |
| standard   | 0.01     | ~2.9 days     |
| durable    | 0.001    | ~28.9 days    |
| core       | 0.0001   | ~289 days     |
| permanent  | 0.00001  | ~7.9 years    |

**Reinforcement Score (normalized):**
```
reinforcement_score = log(1 + count) / log(1 + max_count)
If max_count = 0, score = 0.5 (neutral)
```

**Recency Score:**
```
recency_score = decay_weight(hours_since_reinforcement)
Uses the block's own λ for consistency.
```

---

## How to "Run" an Exploration

### Solo (you + the document)

1. Write the Setup section with your YAML state
2. Work through the Computation by hand using the formulas above
3. Write the Result
4. Capture the Insight

### With Claude

1. Write the Setup and Question sections
2. Ask Claude: "Run exploration NNN" or paste the file
3. Claude computes results, fills in Computation and Result
4. You review, discuss, and capture Insight together
5. Claude writes the completed exploration back to disk

### Typical Session

```
You:    "Create an exploration: what happens to ATTENTION frame
         when 60% of blocks are self-tagged?"
Claude: [creates 004_attention_self_saturation.md with setup]
Claude: [computes scores, shows ATTENTION is identity-dominated]
You:    "Try it with similarity weight at 0.50 instead of 0.35"
Claude: [adds Variation section, recomputes, shows improvement]
You:    "Good. That's a design change. Note it as a decision."
Claude: [updates Insight section with proposed weight change]
```

---

## Conventions

### Block Shorthand

In explorations, blocks are defined inline:

```yaml
blocks:
  A: # Short ID for readability
    content: "I value clear communication"
    category: identity
    confidence: 0.85
    decay_lambda: 0.0001   # core profile
    is_self: true
    reinforcement_count: 3
    hours_since_reinforcement: 48
```

### Edge Shorthand

```yaml
edges:
  A→B: {relation: supports, weight: 0.7, confidence: 0.8}
  B→C: {relation: elaborates, weight: 0.5, confidence: 0.6}
```

### Score Reporting

Always show the full breakdown:

```
Block A (SELF frame):
  recency:      0.995 × 0.05 = 0.0498
  centrality:   0.720 × 0.25 = 0.1800
  confidence:   0.850 × 0.30 = 0.2550
  similarity:   0.400 × 0.10 = 0.0400
  reinforcement: 0.683 × 0.30 = 0.2049
  TOTAL:                        0.7297
```

### Symbolic Similarity

Since we can't compute real embeddings, state similarity explicitly:

```yaml
similarities:  # to query "Python concurrency"
  A: 0.15   # identity block, low relevance to query
  B: 0.82   # Python GIL block, high relevance
  C: 0.45   # general programming, moderate
```

### Time

All time is in **hours** for computation. Use human-readable labels:

```yaml
time: 168  # 7 days
time: 720  # 30 days
time: 2160 # 90 days
```

---

## Directory Structure

```
sim/
├── README.md                          # This file
├── explorations/                      # Phase 1: micro-scenarios
│   ├── _template.md                   # Blank template
│   ├── 001_basic_decay.md
│   ├── 002_confidence_trap.md
│   ├── 003_scoring_walkthrough.md
│   └── ...
├── playgrounds/                       # Phase 2: subsystem specs (later)
│   ├── scoring/
│   ├── decay/
│   ├── frames/
│   ├── graph/
│   └── lifecycle/
└── specs/                             # Phase 3: code-gen source (later)
```

---

## Reference

- Architecture spec: `docs/amgs_architecture.md`
- Previous Python sim notes: `docs/notes.md`
- Previous sim instructions: `docs/amgs_instructions.md`
