# Design Simulation Framework — Reusable Concepts

A comprehensive methodology for reasoning about complex systems before implementation, extracted from elfmem's document-driven specification approach.

## Core Philosophy

**Documents as Executable Specifications**

Instead of code-first or whiteboard-only approaches, use structured markdown files where:
- Every computation is **explicit and mathematical**
- Every state transition is **reasoned through, not guessed**
- Every design decision is **auditable and editable**
- The "execution engine" is **human/AI reasoning over formulas**

This creates a **whiteboard with memory** — you can edit, re-run, and iterate without losing previous thinking.

---

## The 12 Core Concepts

### 1. **Document-Driven Specification**
Use structured markdown files with explicit mathematical computation as the "whiteboard" for system design.

**When to use:**
- Designing domain logic before implementation
- Making scoring/ranking systems transparent
- Reasoning through state machines
- Creating auditable specifications

**Key insight:** Markdown + math = living specification that's reviewable and revisable.

---

### 2. **Micro-Scenario Pattern**
Test individual concepts through self-contained thought experiments with minimal setup, one clear question, worked computation, result, and insight.

**Structure:**
```markdown
# Title: The Question

## Question
One clear question.

## Setup
Minimal state (3-5 entities).

## Computation
Step-by-step math.

## Result
Final answer.

## Insight
What we learned.

## Variations
What if X changed?
```

**When to use:**
- Exploring parameter sensitivity
- Testing edge cases
- Building intuition about behavior
- Seeding the next exploration

---

### 3. **Symbolic Approximation**
When exact computation is impossible, use explicit symbolic values with stated assumptions.

**Examples:**
- Semantic similarity: `0.82` (with note: "high relevance to query")
- Graph centrality: `0.65` (with note: "computed on 5-block subgraph")
- Token estimation: `1500` (with note: "rough estimate; actual tokenization varies")

**Rule:** Always state assumptions. Symbolic ≠ made-up.

---

### 4. **Phase-Based Development**
Structured progression from exploration to implementation:

1. **Phase 1: Explorations** (micro-scenarios, rapid testing)
   - File naming: `NNN_short_name.md`
   - Status: draft → running → complete → superseded
   - Goal: Test concepts, find gaps

2. **Phase 2: Playgrounds** (subsystem specs, formalized patterns)
   - Organized by subsystem
   - Patterns from Phase 1 extracted
   - Test cases with assertions

3. **Phase 3: Executable Specs** (code-generation source)
   - 1:1 mapping to Python modules
   - Worked examples, edge cases
   - Ready for code generation

---

### 5. **Complete Score Breakdown Pattern**
When reporting composite scores, always show full breakdown:

```
Block A (ATTENTION frame):
  recency:       0.95 × 0.25 = 0.2375
  centrality:    0.72 × 0.15 = 0.1080
  confidence:    0.85 × 0.15 = 0.1275
  similarity:    0.92 × 0.35 = 0.3220
  reinforcement: 0.68 × 0.10 = 0.0680
  TOTAL:                        0.8630
```

**Why:** Composites are opaque. Full breakdowns are debuggable.

---

### 6. **Inline YAML for State Representation**
Define system state compactly in exploration setup:

```yaml
blocks:
  A:
    content: "I value clarity"
    confidence: 0.85
    decay_lambda: 0.001
    reinforcement_count: 5
    hours_since_use: 48

edges:
  A→B: {relation: supports, weight: 0.7}
```

**Advantages:**
- State is explicit and reproducible
- Easy to reason about variations
- Can be copy-pasted into code tests

---

### 7. **Exact Math Formulas as Design Currency**
Encode domain logic in precise mathematical formulas that become the spec:

```
decay_weight(t) = e^(-λ × t)
score = Σ(w_i × component_i)
centrality = log(1 + in_degree) / log(1 + max_degree)
```

**Why:**
- Formulas are unambiguous
- Implementation can be verified against formula
- Edge cases become obvious

---

### 8. **Workable Approximations Table**
Distinguish what works well in documents vs what needs approximation vs what needs code:

| **Works Well in Docs** | **Needs Approximation** | **Needs Code** |
|---|---|---|
| Decay (exact math) | Semantic similarity | Real embeddings |
| Scoring formula | Graph centrality | PageRank at scale |
| State transitions | Token estimation | Performance profiling |
| Frame logic | Entropy calc | Large-scale retrieval |

**Use this to scope what to document vs what to prototype.**

---

### 9. **Variation-Seeded Exploration**
After computing a result, ask "what if we changed X?" to seed the next exploration:

```
Exploration 003: Basic decay over 7 days
→ Insight: Decay curve shows flat tail after day 3
→ Variation: What if we used different λ?
→ Seeds Exploration 004: Decay sensitivity analysis
```

**Creates:** Connected sequence of experiments, not isolated scenarios.

---

### 10. **Convention-First Design**
Establish consistent naming, notation, and reporting conventions early:

**Example conventions:**
- Time always in **hours** (with human-readable labels like `168 = 7 days`)
- Scores in **[0, 1]** range with 2 decimal precision
- Block shorthand: **A, B, C** for readability
- Relations: **A→B** for directed edges

**Why:** Consistency reduces cognitive load and makes explorations shareable.

---

### 11. **Edge Case Reasoning in Documents**
Exploit the slow-thinking advantage of documents to reason through edge cases before implementation:

**Example questions:**
- What happens when the graph is empty?
- What if similarity is exactly 0.5?
- What happens at t=0 vs t=∞?
- Can weights sum to > 1.0?

**Document these as explicit test cases** that implementation must satisfy.

---

### 12. **File-Driven Exploration Progression**
Organize explorations with sequential numbering and explicit Status markers:

```
sim/explorations/
├── 001_basic_decay.md              [complete]
├── 002_confidence_trap.md          [complete]
├── 003_scoring_walkthrough.md      [complete]
├── 004_attention_self_saturation.md [running]
└── 005_graph_expansion.md          [draft]
```

**Why:**
- Auditable and referable
- Status tracking prevents duplicate work
- Sequence becomes the spec

---

## How to Apply These Concepts

### To a New Project

1. **Choose a subsystem** to design (e.g., "scoring", "decay", "prioritization")
2. **Write one micro-scenario** with setup, question, and formula
3. **Compute results** step-by-step, showing intermediate values
4. **Write Insight** — what's surprising? What's obvious now?
5. **Write Variation** — seed the next exploration
6. **Repeat** — each exploration builds on the previous

### Common Pitfalls to Avoid

- ❌ **Vague symbolic values** → Always state assumptions
- ❌ **Incomplete score breakdowns** → Always show w_i × component_i
- ❌ **Missing edge cases** → List them explicitly
- ❌ **Unrelated explorations** → Variations seed the next, creating sequence
- ❌ **Messy conventions** → Establish early, use consistently

---

## Template: Starting Your First Exploration

```markdown
# Title: [Clear question about your domain]

## Status: draft

## Question
One sentence. What are we exploring?

## Setup
```yaml
# Minimal state — 3-5 entities
entities:
  A: {key: value}
  B: {key: value}
```

## Computation
Step-by-step math using your domain's formulas.

## Result
Final answer. What state do we end up in?

## Insight
What surprised us? How does this affect design?

## Variations
What if we changed X? (Seeds next exploration)
```

---

## Benefits of This Approach

✓ **Reduces implementation risk** — catch design gaps before coding
✓ **Makes reasoning auditable** — formulas are verifiable
✓ **Enables rapid iteration** — edit formulas, re-run, observe
✓ **Serves as living spec** — implementation verifies against document
✓ **Facilitates collaboration** — other people can review and extend
✓ **Builds intuition** — variations show parameter sensitivity

---

## Reference

- **Source:** elfmem Design Simulation system (sim/README.md)
- **Architecture examples:** docs/amgs_architecture.md
- **Learned concepts in elfmem:** Tagged with `methodology/` and `design-pattern`
