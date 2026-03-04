# Playground: Unified Scoring Function

## Status: Draft

## Subsystem Specification

The unified scoring function is the single computation that ranks memory blocks
for any context frame. It lives in `elfmem/scoring.py` and is the only scoring
implementation in the library.

### Signature

```python
def compute_score(
    similarity: float,      # cosine similarity to query [0.0, 1.0]
    confidence: float,      # block confidence [0.0, 1.0]
    recency: float,         # decay_weight = e^(-λ × hours_since_reinforcement) [0.0, 1.0]
    centrality: float,      # normalised degree centrality [0.0, 1.0]
    reinforcement: float,   # log-normalised retrieval count [0.0, 1.0]
    weights: ScoringWeights,
) -> float:
    ...
```

### Formula

```
score = w_sim × similarity
      + w_conf × confidence
      + w_rec × recency
      + w_cent × centrality
      + w_reinf × reinforcement
```

### Invariants

1. All weight values: `w ∈ [0.0, 1.0]`
2. Weights must sum to 1.0: `sum(weights) == 1.0`
3. All input components: `v ∈ [0.0, 1.0]`
4. Output score: `score ∈ [0.0, 1.0]`
5. Queryless renormalisation: when `similarity` is dropped (no query),
   remaining four weights rescale to sum to 1.0

---

## Parameters

```yaml
# Built-in frame weight defaults (from explorations 002, 003, 024)
SELF_weights:
  similarity:    0.10   # minor — SELF often queryless
  confidence:    0.30   # major — identity blocks must be reliable
  recency:       0.05   # minor — constitutional blocks don't decay
  centrality:    0.25   # major — hub identity blocks preferred
  reinforcement: 0.30   # major — frequently recalled = core identity

ATTENTION_weights:
  similarity:    0.35   # dominant — query relevance is the goal
  confidence:    0.15
  recency:       0.25   # important — don't surface stale knowledge
  centrality:    0.15
  reinforcement: 0.10   # minor — freshness beats frequency here

TASK_weights:
  similarity:    0.20
  confidence:    0.20
  recency:       0.20
  centrality:    0.20
  reinforcement: 0.20   # equal weights — no dominant signal for task context
```

---

## Test Suite

### TC-S-001: Basic ATTENTION Frame Score

**Purpose:** Verify the scoring formula produces correct output for ATTENTION weights.

**Given:**
```yaml
weights: ATTENTION  # sim=0.35, conf=0.15, rec=0.25, cent=0.15, reinf=0.10
components:
  similarity:    0.80
  confidence:    0.70
  recency:       0.60
  centrality:    0.40
  reinforcement: 0.30
```

**When:** `compute_score(0.80, 0.70, 0.60, 0.40, 0.30, ATTENTION_weights)`

**Then:**
```
score = 0.35×0.80 + 0.15×0.70 + 0.25×0.60 + 0.15×0.40 + 0.10×0.30
      = 0.280 + 0.105 + 0.150 + 0.060 + 0.030
      = 0.625
```

**Expected:** `0.625`
**Tolerance:** ±0.001
**Status:** NOT YET RUN

---

### TC-S-002: SELF Frame Score

**Purpose:** Verify SELF frame correctly weights confidence and reinforcement.

**Given:**
```yaml
weights: SELF  # sim=0.10, conf=0.30, rec=0.05, cent=0.25, reinf=0.30
components:
  similarity:    0.20   # low — SELF often has no query
  confidence:    0.90   # high — well-established identity block
  recency:       0.95   # high — constitutional block, never decays
  centrality:    0.70   # high — hub identity block
  reinforcement: 0.85   # high — recalled in every session
```

**When:** `compute_score(0.20, 0.90, 0.95, 0.70, 0.85, SELF_weights)`

**Then:**
```
score = 0.10×0.20 + 0.30×0.90 + 0.05×0.95 + 0.25×0.70 + 0.30×0.85
      = 0.020 + 0.270 + 0.048 + 0.175 + 0.255
      = 0.768
```

**Expected:** `0.768`
**Tolerance:** ±0.001
**Status:** NOT YET RUN

---

### TC-S-003: Weights Sum to 1.0 — Invariant Check

**Purpose:** ScoringWeights must reject any configuration where weights do not sum to 1.0.

**Given:**
```python
weights = ScoringWeights(
    similarity=0.35, confidence=0.15, recency=0.25,
    centrality=0.15, reinforcement=0.05  # sums to 0.95, not 1.0
)
```

**When:** Construction of `ScoringWeights`

**Then:** `ValidationError` (Pydantic) raised at construction time.

**Expected:** Raises `ValidationError` with message indicating weights sum ≠ 1.0
**Status:** NOT YET RUN

---

### TC-S-004: Queryless Renormalisation — SELF Frame

**Purpose:** When query is absent (similarity dropped), remaining weights must rescale to sum to 1.0.

**Given:**
```yaml
original_SELF_weights:
  similarity:    0.10
  confidence:    0.30
  recency:       0.05
  centrality:    0.25
  reinforcement: 0.30

# Drop similarity (no query)
remaining_sum = 0.30 + 0.05 + 0.25 + 0.30 = 0.90
scale_factor  = 1.0 / 0.90 = 1.111...
```

**When:** `weights.renormalized_without_similarity()`

**Then:**
```
confidence:    0.30 × 1.111 = 0.333
recency:       0.05 × 1.111 = 0.056
centrality:    0.25 × 1.111 = 0.278
reinforcement: 0.30 × 1.111 = 0.333
sum = 1.000 ✓
```

**Expected:** Renormalized weights sum exactly to 1.0; individual values as computed above
**Tolerance:** ±0.001
**Status:** NOT YET RUN

---

### TC-S-005: Queryless Score Produces Same Ranking Order as Full Score

**Purpose:** Removing similarity from queryless SELF should preserve the relative ranking of blocks
with similar identity profiles (confidence and reinforcement still discriminate).

**Given:** Two blocks, both with SELF weights (no query):
```yaml
block_A:
  confidence:    0.90
  recency:       0.95
  centrality:    0.70
  reinforcement: 0.85

block_B:
  confidence:    0.60
  recency:       0.80
  centrality:    0.40
  reinforcement: 0.50
```

**When:** Compute scores with renormalized weights (similarity dropped)

**Then:** `score(A) > score(B)` — Block A ranks higher in the queryless SELF frame

**Expected:** A ranked above B
**Status:** NOT YET RUN

---

### TC-S-006: Minimum Score — All Zeros

**Purpose:** A block with all zero components scores 0.0 regardless of weights.

**Given:** `components = (0.0, 0.0, 0.0, 0.0, 0.0)`, any valid weights

**When:** `compute_score(0.0, 0.0, 0.0, 0.0, 0.0, any_weights)`

**Then:** `score == 0.0`

**Expected:** `0.0`
**Status:** NOT YET RUN

---

### TC-S-007: Maximum Score — All Ones

**Purpose:** A block with all maximum components scores 1.0 regardless of weights.

**Given:** `components = (1.0, 1.0, 1.0, 1.0, 1.0)`, any valid weights

**When:** `compute_score(1.0, 1.0, 1.0, 1.0, 1.0, any_weights)`

**Then:** `score == 1.0` (since `sum(weights) == 1.0` and each term equals its weight)

**Expected:** `1.0`
**Status:** NOT YET RUN

---

### TC-S-008: High Similarity Dominates in ATTENTION Frame

**Purpose:** Verify ATTENTION frame correctly surfaces query-relevant blocks over high-confidence
identity blocks (validates exploration 002's key finding).

**Given:**
```yaml
# A: high-confidence identity block, low similarity to query
block_A:
  similarity:    0.10   # not query-relevant
  confidence:    0.95   # high
  recency:       0.90
  centrality:    0.80
  reinforcement: 0.75

# B: query-relevant knowledge block, moderate other components
block_B:
  similarity:    0.85   # highly query-relevant
  confidence:    0.65
  recency:       0.60
  centrality:    0.30
  reinforcement: 0.20
```

**When:** `compute_score(each, ATTENTION_weights)`

**Then:**
```
score_A = 0.35×0.10 + 0.15×0.95 + 0.25×0.90 + 0.15×0.80 + 0.10×0.75
        = 0.035 + 0.143 + 0.225 + 0.120 + 0.075 = 0.598

score_B = 0.35×0.85 + 0.15×0.65 + 0.25×0.60 + 0.15×0.30 + 0.10×0.20
        = 0.298 + 0.098 + 0.150 + 0.045 + 0.020 = 0.611
```

**Expected:** `score_B (0.611) > score_A (0.598)` — query-relevant block wins
**Tolerance:** ±0.001
**Status:** NOT YET RUN

---

### TC-S-009: TASK Frame Equal Weights

**Purpose:** TASK frame with equal weights treats all signals as equally important; no single
component can dominate.

**Given:**
```yaml
weights: TASK  # all weights = 0.20
components:
  similarity:    0.60
  confidence:    0.60
  recency:       0.60
  centrality:    0.60
  reinforcement: 0.60
```

**When:** `compute_score(0.60, 0.60, 0.60, 0.60, 0.60, TASK_weights)`

**Then:** `score = 0.60` (uniform components × uniform weights = average)

**Expected:** `0.60`
**Tolerance:** ±0.001
**Status:** NOT YET RUN

---

### TC-S-010: Log-Normalised Reinforcement Score

**Purpose:** Verify `reinforcement_score = log(1 + count) / log(1 + max_count)` produces
correct values and is bounded [0.0, 1.0].

**Given:** `max_count = 100`

**When:** Compute reinforcement scores for counts [0, 1, 5, 10, 50, 100]

**Then:**
```
count=0:    log(1) / log(101)     = 0.0   / 4.615 = 0.000
count=1:    log(2) / log(101)     = 0.693 / 4.615 = 0.150
count=5:    log(6) / log(101)     = 1.792 / 4.615 = 0.388
count=10:   log(11) / log(101)    = 2.398 / 4.615 = 0.520
count=50:   log(51) / log(101)    = 3.932 / 4.615 = 0.852
count=100:  log(101) / log(101)   = 4.615 / 4.615 = 1.000
```

**Expected:** Values as computed; all in [0.0, 1.0]; count=0 → 0.0; count=max_count → 1.0
**Tolerance:** ±0.001
**Status:** NOT YET RUN

---

### TC-S-011: Custom Frame Weights Respected

**Purpose:** A user-registered custom frame with bespoke weights produces scores that
reflect those weights, not the defaults.

**Given:**
```yaml
# "code_review" frame: similarity and confidence matter most
custom_weights:
  similarity:    0.40
  confidence:    0.40
  recency:       0.10
  centrality:    0.05
  reinforcement: 0.05
components:
  similarity:    0.80
  confidence:    0.80
  recency:       0.20
  centrality:    0.20
  reinforcement: 0.20
```

**When:** `compute_score(0.80, 0.80, 0.20, 0.20, 0.20, custom_weights)`

**Then:**
```
score = 0.40×0.80 + 0.40×0.80 + 0.10×0.20 + 0.05×0.20 + 0.05×0.20
      = 0.320 + 0.320 + 0.020 + 0.010 + 0.010
      = 0.680
```

**Expected:** `0.680`
**Tolerance:** ±0.001
**Status:** NOT YET RUN

---

### TC-S-012: Score Stability Across Frame Types for Same Block

**Purpose:** The same block evaluated under different frame weights produces different scores,
and the direction of change is consistent with the frame's intended purpose.

**Given:**
```yaml
# A query-relevant, low-confidence, recently-reinforced block
block:
  similarity:    0.85
  confidence:    0.35   # low
  recency:       0.70
  centrality:    0.30
  reinforcement: 0.40
```

**When:** Score under SELF, ATTENTION, TASK

**Then:**
```
SELF score      = 0.10×0.85 + 0.30×0.35 + 0.05×0.70 + 0.25×0.30 + 0.30×0.40
               = 0.085 + 0.105 + 0.035 + 0.075 + 0.120 = 0.420

ATTENTION score = 0.35×0.85 + 0.15×0.35 + 0.25×0.70 + 0.15×0.30 + 0.10×0.40
               = 0.298 + 0.053 + 0.175 + 0.045 + 0.040 = 0.611

TASK score      = 0.20×0.85 + 0.20×0.35 + 0.20×0.70 + 0.20×0.30 + 0.20×0.40
               = 0.170 + 0.070 + 0.140 + 0.060 + 0.080 = 0.520
```

**Expected:** `ATTENTION (0.611) > TASK (0.520) > SELF (0.420)`

This block ranks highest in ATTENTION (its high similarity wins) and lowest in SELF
(low confidence penalises it heavily in identity-focused retrieval).

**Status:** NOT YET RUN

---

## Parameter Tuning

### PT-1: ATTENTION Similarity Weight (currently 0.35)

**Question:** Does raising similarity weight to 0.45 cause identity blocks to be
suppressed too aggressively in ATTENTION frame?

**Scenario:** Run TC-S-008 with similarity weight at 0.25, 0.35, 0.45, 0.50.
Track the gap between query-relevant (B) and high-confidence identity (A) blocks.

**Current result (0.35):** B wins by 0.013 margin — acceptable.
**Concern:** At 0.25, identity blocks may dominate again.

**Recommendation:** Validate that at `similarity=0.25`, Block A beats Block B.
If confirmed, 0.35 is the minimum safe weight. Keep at 0.35.

---

### PT-2: SELF Confidence vs. Reinforcement Balance (currently 0.30 each)

**Question:** Should confidence outweigh reinforcement in SELF frame, or should
they remain equal?

**Rationale:** Confidence is set at learn/consolidate time (agent-verified).
Reinforcement is emergent (grows with usage). Both signal identity relevance but differently.

**Scenario:** Compute SELF scores with weights (conf=0.40, reinf=0.20) and compare
ranking to baseline (conf=0.30, reinf=0.30) for a corpus of 5 identity blocks.

**Hypothesis:** Equal weighting is correct — usage-verified identity is as strong as
explicitly-tagged identity.

---

### PT-3: TASK Frame Equal Weights Assumption

**Question:** Are equal weights (0.20 each) actually the right default for task context,
or should similarity be slightly higher to ensure query relevance?

**Scenario:** Compare TASK frame block selection with:
- Equal: `[0.20, 0.20, 0.20, 0.20, 0.20]`
- Slight sim bias: `[0.25, 0.20, 0.20, 0.20, 0.15]`

Run on 5 blocks with varying similarity profiles. Does the equal-weight TASK ever
surface a block with low similarity that the slight-bias version would exclude?

---

## Open Assertions

1. `compute_score` is a pure function — identical inputs always produce identical outputs
2. Scores are deterministic (no randomness in the formula)
3. Adding a small positive delta to any component increases the score
4. The formula is commutative — component order does not matter
5. `renormalized_without_similarity()` always produces weights that sum to exactly 1.0

---

## Python Test Sketch

```python
# elfmem/tests/test_scoring.py

import math
import pytest
from elfmem.scoring import compute_score, ScoringWeights

ATTENTION = ScoringWeights(
    similarity=0.35, confidence=0.15, recency=0.25,
    centrality=0.15, reinforcement=0.10
)
SELF = ScoringWeights(
    similarity=0.10, confidence=0.30, recency=0.05,
    centrality=0.25, reinforcement=0.30
)
TASK = ScoringWeights(
    similarity=0.20, confidence=0.20, recency=0.20,
    centrality=0.20, reinforcement=0.20
)

def test_attention_frame_basic(weights=ATTENTION):
    score = compute_score(0.80, 0.70, 0.60, 0.40, 0.30, ATTENTION)
    assert abs(score - 0.625) < 0.001

def test_all_zeros_scores_zero():
    score = compute_score(0.0, 0.0, 0.0, 0.0, 0.0, ATTENTION)
    assert score == 0.0

def test_all_ones_scores_one():
    score = compute_score(1.0, 1.0, 1.0, 1.0, 1.0, ATTENTION)
    assert abs(score - 1.0) < 0.001

def test_weights_must_sum_to_one():
    with pytest.raises(ValidationError):
        ScoringWeights(
            similarity=0.35, confidence=0.15, recency=0.25,
            centrality=0.15, reinforcement=0.05  # sums to 0.95
        )

def test_renormalization_sums_to_one():
    renorm = SELF.renormalized_without_similarity()
    assert abs(sum(renorm.values()) - 1.0) < 1e-9

def test_reinforcement_log_normalisation():
    def reinf(count, max_count=100):
        return math.log(1 + count) / math.log(1 + max_count)
    assert abs(reinf(0)   - 0.000) < 0.001
    assert abs(reinf(1)   - 0.150) < 0.001
    assert abs(reinf(100) - 1.000) < 0.001
```
