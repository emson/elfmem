# Design Simulation Quick Reference

## Exploration Template (Copy & Paste)

```markdown
# Title: [Question about your domain]

## Status: draft

## Question
[One clear question]

## Setup
```yaml
entities:
  A: {key1: val, key2: val}
  B: {key1: val, key2: val}
relationships:
  A→B: {type: relation, strength: 0.8}
```

## Computation
[Step-by-step math with intermediate values shown]

Example format:
```
Step 1: Compute X
  X = formula(inputs) = value

Step 2: Apply Y to X
  Y = formula(X) = value

Result = Y = final_value
```

## Result
[Final state after computation]

## Insight
[What we learned; how it affects design]

## Variations
[What if we changed X? Seeds next exploration]
```

---

## Key Formulas (Templates)

### Decay Model
```
decay_weight(t) = e^(-λ × t)
half_life = ln(2) / λ ≈ 0.693 / λ
```

### Composite Scoring
```
score = w₁×C₁ + w₂×C₂ + ... + wₙ×Cₙ
where Σ(wᵢ) = 1.0 (weights must sum to 1)
```

### Reinforcement (Normalized)
```
reinforcement_score = log(1 + count) / log(1 + max_count)
if max_count = 0: score = 0.5 (neutral)
```

### Centrality (Graph)
```
centrality = log(1 + degree) / log(1 + max_degree)
```

### Recency
```
recency = decay_weight(hours_since_event)
```

---

## Conventions Checklist

- [ ] **Time:** Always in hours; include human labels (e.g., `168 = 7 days`)
- [ ] **Scores:** [0, 1] range, 2-3 decimal precision
- [ ] **Entity shorthand:** A, B, C for readability
- [ ] **Relations:** `A→B` for directed; `A↔B` for undirected
- [ ] **YAML:** Compact, semantic keys
- [ ] **Score breakdown:** Always show `w_i × component = contribution`
- [ ] **Assumptions:** State all symbolic values explicitly
- [ ] **Status:** draft → running → complete → superseded
- [ ] **Naming:** `NNN_short_name.md` (sequential, descriptive)

---

## Complete Score Breakdown Template

```
Entity X (context):
  component_1:   value × weight = contribution
  component_2:   value × weight = contribution
  component_3:   value × weight = contribution
  TOTAL:                          sum
```

Example:
```
Block A (ATTENTION frame):
  recency:       0.95 × 0.25 = 0.2375
  centrality:    0.72 × 0.15 = 0.1080
  confidence:    0.85 × 0.15 = 0.1275
  similarity:    0.92 × 0.35 = 0.3220
  reinforcement: 0.68 × 0.10 = 0.0680
  TOTAL:                        0.8630
```

---

## Workable vs Approximate vs Code

| **Works Well in Documents** | **Needs Approximation** | **Needs Code** |
|---|---|---|
| Decay (exact math) | Semantic similarity | Real embeddings |
| Composite scoring | Graph metrics at scale | Performance profiling |
| State transitions | Token counts | Large retrieval queries |
| Boolean logic | Floating-point precision | Hardware I/O |
| Frame selection | Learning curves | Concurrent algorithms |

**Use this to decide:** document-driven test or prototype?

---

## Exploration Sequence Pattern

```
Exploration 001: Basic mechanism
  ↓ (Variation: what if X?)
Exploration 002: Parameter sensitivity
  ↓ (Variation: what if Y?)
Exploration 003: Edge case (boundary)
  ↓ (Variation: what if we combine X and Y?)
Exploration 004: Interaction effects
  ↓ (Variation: scale to N=100)
Exploration 005: Large-scale behavior
```

**Each variation seeds the next.** This creates a connected learning path.

---

## Common Symbolic Values

| **Concept** | **Symbolic Range** | **Example** |
|---|---|---|
| Similarity (semantic) | [0, 1] | 0.82 = high relevance |
| Centrality (small graph) | [0, 1] | 0.65 = moderately central |
| Confidence (belief) | [0, 1] | 0.75 = reasonably sure |
| Weight (relative importance) | [0, 1] | 0.40 = moderate importance |
| Decay lambda | [0, 1] | 0.01 = ~2.9 day half-life |

**Always note:** `0.82 (high relevance to query "Python concurrency")`

---

## File Organization

```
project/
├── explorations/
│   ├── 001_basic_mechanism.md       [complete]
│   ├── 002_parameter_X_sensitivity.md [complete]
│   ├── 003_edge_case_empty_state.md [complete]
│   ├── 004_interaction_effects.md   [running]
│   └── 005_large_scale_behavior.md  [draft]
├── DESIGN_SIMULATION_GUIDE.md
└── DESIGN_SIMULATION_QUICK_REF.md
```

---

## Checklist: Before You Implement

- [ ] Have you written at least 3 explorations?
- [ ] Do your formulas handle edge cases (empty, zero, infinity)?
- [ ] Can you reproduce each result by hand?
- [ ] Does every component in a composite have clear meaning?
- [ ] Have you tested parameter sensitivity (what if weight = 0.9 instead of 0.5)?
- [ ] Are all assumptions documented?
- [ ] Can someone else understand and extend your explorations?

---

## Quick Start: Your First Exploration

1. **Pick a subsystem** to design
2. **Write the Setup** with minimal state
3. **Compute step-by-step**, showing every intermediate value
4. **State the Result** clearly
5. **Note Insight** — what surprised you?
6. **Propose Variation** — seed exploration 2
7. **Mark Status: complete** when done

**Time to first exploration:** ~30 minutes
**Time to design clarity:** ~5 explorations (2-3 hours)

---

## Why This Works

✓ **Explicit:** Formulas and values are clear, not hidden in code
✓ **Auditable:** Every computation is step-by-step, reviewable
✓ **Iterative:** Change a formula, recompute, observe immediately
✓ **Teachable:** Other people can follow and extend your reasoning
✓ **Verifiable:** Implementation checks itself against the spec

---

## Resources

- **Full guide:** `DESIGN_SIMULATION_GUIDE.md`
- **Template:** `sim/explorations/_template.md`
- **Architecture reference:** `docs/amgs_architecture.md`
- **Learned concepts:** Query elfmem for `methodology/` or `design-pattern` tags
