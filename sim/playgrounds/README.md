# elfmem Playgrounds

Phase 2 of the elfmem simulation system. Playgrounds transition from
"how does this work?" (explorations) to "does this satisfy requirements?" (test specifications).

Each playground covers one subsystem with a formal spec, explicit test assertions, and
parameter tuning scenarios. The output is a **test suite blueprint** — directly
translatable to Python `pytest` code.

---

## Playground vs. Exploration

| Aspect | Exploration | Playground |
|--------|-------------|------------|
| **Question** | How does this work? | Does this satisfy requirements? |
| **Format** | Worked example | Formal test cases |
| **Output** | Design decision | Pass/Fail assertions |
| **Tuning** | N/A | Parameter sensitivity |
| **Coverage** | One scenario | Full test surface |
| **Next step** | Insight → decision | Assertion → Python test |

---

## The Six Playgrounds

| Playground | Subsystem | Key Invariants |
|-----------|-----------|----------------|
| **scoring/** | Unified 5-component scoring | Weights sum to 1.0; queryless renormalises |
| **decay/** | Decay tiers + session-aware clock | Four λ values; computed from `last_reinforced_at` |
| **lifecycle/** | learn → consolidate → curate | Dedup, state transitions, trigger conditions |
| **frames/** | SELF, ATTENTION, TASK assembly | Frame weights; guarantee_tags; token budget |
| **retrieval/** | 4-stage hybrid pipeline | Pre-filter → vector → graph → composite |
| **graph/** | Edges, centrality, expansion | Degree cap; λ_edge; 1-hop bounded expansion |

---

## Playground File Format

Each playground file follows this structure:

```markdown
# Playground: [Subsystem Name]

## Subsystem Specification
[Formal description: inputs, outputs, invariants]

## Parameters
[All configurable values with current defaults]

## Test Suite

### TC-001: [Test Name]
**Purpose:** [What invariant this verifies]
**Given:** [Setup state — YAML or code-style description]
**When:** [Operation performed]
**Then:** [Expected result]
**Tolerance:** [±0.001 for floats, exact for strings/ints]
**Status:** PASS | FAIL | NOT YET RUN

---
[Repeat for each test case]

## Parameter Tuning
[Scenarios where default values may need adjustment]

## Open Assertions
[Invariants we know exist but haven't written test cases for yet]

## Python Test Sketch
[Pseudocode for how these translate to pytest]
```

---

## Status

| Playground | Test Cases | Tuning Scenarios | Status |
|-----------|------------|-----------------|--------|
| scoring/ | 12 | 3 | Draft |
| decay/ | 10 | 4 | Draft |
| lifecycle/ | 14 | 4 | Draft |
| frames/ | 10 | 3 | Draft |
| retrieval/ | 8 | 2 | Draft |
| graph/ | 10 | 2 | Draft |

---

## From Playground to Code

When a playground is complete, its test cases map directly to Python:

```python
# From TC-001 in scoring playground:
# "Given weights [0.1, 0.2, 0.35, 0.25, 0.1], sim=0.8, conf=0.7..."

def test_attention_frame_scores_query_relevant_block():
    weights = ScoringWeights(
        recency=0.25, centrality=0.15, confidence=0.15,
        similarity=0.35, reinforcement=0.10
    )
    score = compute_score(
        similarity=0.8, confidence=0.7, recency=0.6,
        centrality=0.4, reinforcement=0.3,
        weights=weights,
    )
    assert abs(score - 0.600) < 0.001
```

The playground **is** the test specification. Code generation is the final step.
