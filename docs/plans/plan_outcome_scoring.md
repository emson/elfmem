# Plan: Domain-Agnostic Outcome Scoring (`outcome()`)

**Reference:** `docs/brainstorm_outcome_feedback.md`, `docs/coding_principles.md`

---

## Problem Statement

elfmem's block confidence is set once at consolidation from an LLM alignment score and
never updated. Blocks that consistently appear in good outcomes look identical to blocks
that consistently appear in bad ones. The system has memory of facts but no memory of
its own performance.

The KLS trading system demonstrated the gap: Brier scores from resolved forecasts could
improve block confidence, but there is no mechanism to feed that back. The solution must
be domain-agnostic: trading, coding, writing, customer support, or any other domain must
be able to report outcomes using the same API.

---

## Design Decisions

### 1. Normalised signal in [0.0, 1.0] — agent normalises, elfmem consumes

elfmem cannot know about Brier scores, test pass rates, or CSAT scores. The agent
converts domain metrics to a normalised quality signal. This is always a one-liner:

```python
signal = 1.0 - brier_score           # trading
signal = 1.0 if tests_passed else 0.0  # coding
signal = min(engagement / baseline, 1.0)  # writing
signal = (csat_score - 1.0) / 4.0    # support
```

### 2. Bayesian Beta-Binomial update — not EMA, not direct set

Each block's confidence is a Beta posterior combining the LLM alignment prior with
observed evidence. This is self-regularising: the prior dominates early; evidence
dominates later. Formula:

```
total  = PRIOR_STRENGTH + outcome_evidence
α      = confidence × total + signal × weight
β      = (1 - confidence) × total + (1 - signal) × weight
new_confidence = α / (α + β)
new_outcome_evidence = outcome_evidence + weight
```

With `PRIOR_STRENGTH = 2.0` (configurable), the alignment score has the weight of two
observations. By the 10th outcome, evidence dominates.

### 3. `outcome()` does not require an active session

Outcomes may arrive weeks after retrieval (e.g., a trading forecast resolving after
30 days). The method writes directly to the database and uses `compute_current_active_hours()`
for reinforcement timestamps, exactly like `learn()`.

### 4. Non-active blocks are silently skipped

If a block was archived between retrieval and outcome reporting, skip it without error.
The agent cannot be expected to know whether a block is still active weeks later.

### 5. Reinforce blocks with positive signal

If `signal > outcome_reinforce_threshold` (default 0.5), reinforce the blocks
(update `last_reinforced_at`, increment `reinforcement_count`) and reinforce
co-retrieved edges (Hebbian learning). This makes positive-outcome blocks more
resistant to decay. Blocks with negative signals are not anti-reinforced — they
naturally decay faster because they receive no reinforcement.

### 6. Full audit trail in `block_outcomes` table

An append-only table records every outcome signal with `confidence_before`,
`confidence_after`, `signal`, `weight`, and `source`. elfmem writes to this but does
not read from it during normal operation. It exists for external analytics.

---

## Implementation Steps

### Step 1 — Schema: `models.py`

**File:** `src/elfmem/db/models.py`

Changes:
1. Add `outcome_evidence` column (Float, default 0.0) to `blocks` table.
   Tracks the total weighted evidence received. Used to reconstruct the Beta prior.
2. Add `block_outcomes` table for the audit log.
3. Add index on `block_outcomes.block_id`.

```python
# In blocks table:
Column("outcome_evidence", Float, nullable=False, default=0.0)

# New table:
block_outcomes = Table(
    "block_outcomes",
    metadata,
    Column("id", Text, primary_key=True),
    Column("block_id", Text, ForeignKey("blocks.id", ondelete="CASCADE"), nullable=False),
    Column("signal", Float, nullable=False),
    Column("weight", Float, nullable=False),
    Column("source", Text, nullable=False, default=""),
    Column("confidence_before", Float, nullable=False),
    Column("confidence_after", Float, nullable=False),
    Column("created_at", Text, nullable=False),
)
Index("idx_block_outcomes_block_id", block_outcomes.c.block_id)
```

### Step 2 — Queries: `queries.py`

**File:** `src/elfmem/db/queries.py`

Add three new query functions:
1. `update_block_outcome(conn, *, block_id, new_confidence, new_outcome_evidence)` —
   updates confidence and outcome_evidence on a block.
2. `insert_block_outcome(conn, *, block_id, signal, weight, source, confidence_before,
   confidence_after)` — appends an audit record.

These follow the exact pattern of existing queries: pure async functions, no try/catch,
type hints throughout.

### Step 3 — Types: `types.py`

**File:** `src/elfmem/types.py`

Add `OutcomeResult` frozen dataclass following the existing result pattern:

```python
@dataclass(frozen=True)
class OutcomeResult:
    blocks_updated: int
    mean_confidence_delta: float
    edges_reinforced: int

    @property
    def summary(self) -> str: ...
    def __str__(self) -> str: return self.summary
    def to_dict(self) -> dict[str, Any]: ...
```

`__str__` format:
- `"Outcome recorded: nothing to update."` (no active blocks found)
- `"Outcome recorded: 3 blocks updated (+0.042 avg confidence), 2 edges reinforced."`
- `"Outcome recorded: 2 blocks updated (-0.031 avg confidence)."` (negative signal)

### Step 4 — Config: `config.py`

**File:** `src/elfmem/config.py`

Add two fields to `MemoryConfig`:

```python
outcome_prior_strength: float = 2.0
# Weight of LLM alignment prior. Higher = more outcomes needed to override alignment.
# 2.0 means alignment has the weight of 2 observations.

outcome_reinforce_threshold: float = 0.5
# Minimum signal to trigger block reinforcement and edge learning.
# Blocks below this threshold receive no reinforcement (decay naturally).
```

### Step 5 — Operation: `operations/outcome.py`

**File:** `src/elfmem/operations/outcome.py` (new file)

Two functions following the coding principles (pure, composable, ≤50 lines each):

1. `compute_bayesian_update(*, confidence, outcome_evidence, signal, weight, prior_strength) -> float`
   — pure function, no DB access, fully testable.

2. `record_outcome(conn, *, block_ids, signal, weight, source, current_active_hours,
   prior_strength, reinforce_threshold) -> OutcomeResult`
   — main operation, orchestrates the pipeline:
   ```
   validate signal + weight
   → for each block_id: fetch, skip if not active, compute update, persist
   → write audit records
   → if signal > threshold: reinforce blocks + edges
   → return OutcomeResult
   ```

Validation follows coding principles — fail fast, errors bubble up:
```python
def _validate_signal(signal: float) -> None:
    if not (0.0 <= signal <= 1.0):
        raise ValueError(f"signal must be in [0.0, 1.0], got {signal!r}")

def _validate_weight(weight: float) -> None:
    if weight <= 0.0:
        raise ValueError(f"weight must be > 0.0, got {weight!r}")
```

### Step 6 — Guide: `guide.py`

**File:** `src/elfmem/guide.py`

1. Add `"outcome"` entry to `GUIDES` dict with all 8 fields.
2. Update `OVERVIEW` string to include the `outcome()` row.
3. Update the lifecycle line to show: `... → frame() / recall() → outcome()`.

### Step 7 — API: `api.py`

**File:** `src/elfmem/api.py`

Add `outcome()` method to `MemorySystem`. Consistent with other methods:
- No session requirement
- Delegates to `record_outcome()` operation
- Records to `self._history` via `_record_op()`
- Returns `OutcomeResult`

Method docstring follows the agent-friendly USE WHEN / DON'T USE WHEN / COST /
RETURNS / NEXT / EXAMPLE pattern with domain conversion examples.

### Step 8 — Exports: `__init__.py`

**File:** `src/elfmem/__init__.py`

Add `OutcomeResult` to imports from `elfmem.types` and to `__all__`.

### Step 9 — Tests: `tests/test_outcome.py`

**File:** `tests/test_outcome.py` (new file)

Three test classes following Arrange-Act-Assert:

1. `TestComputeBayesianUpdate` — pure function unit tests (no DB):
   - Prior dominates when outcome_evidence=0
   - Good outcome increases confidence
   - Bad outcome decreases confidence
   - Neutral outcome barely changes confidence
   - Converges toward 1.0 after many good outcomes
   - Converges toward 0.0 after many bad outcomes
   - High weight moves confidence faster than low weight
   - Output always in [0.0, 1.0]

2. `TestRecordOutcome` — integration tests via operation directly:
   - Empty block_ids returns zero-counts OutcomeResult
   - Active block confidence updated correctly
   - Archived block silently skipped
   - Outcome evidence accumulates across calls
   - audit record written to block_outcomes
   - Positive signal triggers reinforcement
   - Negative signal skips reinforcement
   - Edges reinforced for multi-block positive outcome
   - ValueError for signal < 0
   - ValueError for signal > 1
   - ValueError for weight <= 0

3. `TestOutcomeAPI` — integration tests via MemorySystem.outcome():
   - Returns OutcomeResult
   - str(result) is agent-readable
   - to_dict() has correct keys
   - Works without active session
   - Works within active session
   - History records the operation
   - guide("outcome") returns full guide text

---

## Acceptance Criteria

1. All existing 318 tests continue to pass (no regressions)
2. All new tests pass
3. `system.guide("outcome")` returns agent-readable documentation
4. Signal outside [0.0, 1.0] raises `ValueError`
5. Outcome on archived block does not raise
6. After 10 positive outcomes, block confidence measurably higher than initial
7. After 10 negative outcomes, block confidence measurably lower than initial
8. `OutcomeResult` exported from `elfmem` package

---

## What Is Explicitly Out of Scope

- Per-block attribution weights (blocks contribute equally to the signal)
- Multi-dimensional traits (single confidence scalar only)
- Batch `outcomes()` method (multiple individual calls suffice)
- Automatic re-calibration triggers
- Migration path for existing databases (fresh DB always works)
