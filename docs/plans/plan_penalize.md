# Plan: Penalize Integration — Decay Acceleration via outcome()

**Decay rate acceleration for blocks that contribute to bad outcomes, triggered automatically inside outcome().**

Last updated: 2026-03-05
Status: Implemented

---

## Decision: penalize is internal, not a public API method

The original plan described `penalize()` as a separate public API method. This was superseded before implementation.

**Reason:** Having both `outcome()` and `penalize()` as public methods creates an asymmetry agents must manage manually:

- Good outcome: agent calls `outcome(signal=0.9)` — reinforcement happens automatically.
- Bad outcome: agent would need to call `outcome(signal=0.05)` AND `penalize()` separately.

The correct fix mirrors the positive pattern: extend `outcome()` to automatically trigger decay acceleration when `signal < penalize_threshold`. The agent calls one method for all outcomes. The system does the right thing across the full spectrum.

```
signal 0.8–1.0  → confidence UP + reinforce (decay resets)
signal 0.2–0.8  → confidence adjusted only (neutral dead-band)
signal 0.0–0.2  → confidence DOWN + decay accelerated automatically
```

No public `penalize()` method exists. No `PenalizeResult` type. No separate guide entry.

---

## Design Principles

**1. Penalize accelerates decay; it does not delete.**
Increasing `decay_lambda` is reversible — a penalized block can still be reinforced by future correct outcomes, which resets `last_reinforced_at` and slows its effective decay again.

**2. `decay_lambda` has a ceiling.**
Capped at `EPHEMERAL` tier (0.050). No block decays faster than ephemeral regardless of accumulated penalties.

**3. DURABLE and PERMANENT blocks are protected.**
Blocks with `decay_lambda <= 0.001` are silently skipped — these tiers are floors, not targets.

**4. Three-zone signal spectrum with configurable dead-band.**
`penalize_threshold` (default 0.20) and `outcome_reinforce_threshold` (default 0.50) define three zones. The gap between them is a neutral dead-band where confidence adjusts but decay is unchanged.

---

## The penalty formula

```
new_lambda = min(current_lambda * penalty_factor, lambda_ceiling)
```

Default `penalty_factor = 2.0`, `lambda_ceiling = 0.050`:

```
STANDARD block:  0.010 → 0.020 → 0.040 → 0.050 (capped, after 3 penalties)
EPHEMERAL block: 0.050 → 0.050 (already at ceiling, no-op)
DURABLE block:   skipped — protected
PERMANENT block: skipped — protected
```

---

## What Changed

### `ElfmemConfig.memory` — three new fields

```python
penalize_threshold: float = 0.20   # signal below this triggers decay acceleration
penalty_factor: float = 2.0        # decay_lambda multiplier per bad outcome
lambda_ceiling: float = 0.050      # cap = EPHEMERAL tier
```

### `db/queries.py` — new query `accelerate_block_decay()`

```python
async def accelerate_block_decay(
    conn: AsyncConnection,
    block_ids: list[str],
    penalty_factor: float,
    lambda_ceiling: float,
) -> list[tuple[str, float, float]]:
    """Multiply decay_lambda by penalty_factor for STANDARD/EPHEMERAL blocks.
    Skips non-active and DURABLE/PERMANENT blocks (decay_lambda <= 0.001).
    Returns list of (block_id, lambda_before, lambda_after).
    """
```

### `operations/outcome.py` — penalize branch after reinforce branch

```python
blocks_penalized = 0
if updated_ids and signal < penalize_threshold:
    penalized = await accelerate_block_decay(
        conn,
        block_ids=updated_ids,
        penalty_factor=penalty_factor,
        lambda_ceiling=lambda_ceiling,
    )
    blocks_penalized = len(penalized)
```

### `types.py` — `OutcomeResult` gains `blocks_penalized`

```python
@dataclass(frozen=True)
class OutcomeResult:
    blocks_updated: int
    mean_confidence_delta: float
    edges_reinforced: int
    blocks_penalized: int = 0   # new field
```

Summary string: `"3 blocks updated (+0.042 avg confidence), 2 edges reinforced, 1 block penalized."`

### `api.py` — passes new config fields to `record_outcome()`

```python
result = await _record_outcome(
    conn,
    ...
    reinforce_threshold=mem.outcome_reinforce_threshold,
    penalize_threshold=mem.penalize_threshold,
    penalty_factor=mem.penalty_factor,
    lambda_ceiling=mem.lambda_ceiling,
)
```

### `guide.py` — `outcome` guide updated

Returns now includes `blocks_penalized`. Next section explains the three-zone spectrum.

---

## Files Changed

| File | Change |
|---|---|
| `docs/plans/plan_penalize.md` | Rewritten — penalize is internal |
| `src/elfmem/db/queries.py` | Added `accelerate_block_decay()` |
| `src/elfmem/operations/outcome.py` | Added penalize branch; new params |
| `src/elfmem/types.py` | `OutcomeResult.blocks_penalized`; updated summary/to_dict |
| `src/elfmem/config.py` | `penalize_threshold`, `penalty_factor`, `lambda_ceiling` |
| `src/elfmem/api.py` | Passes new config fields; updated docstring |
| `src/elfmem/guide.py` | Updated `outcome` guide returns + next |

## Files NOT Changed

- `src/elfmem/__init__.py` — no new export needed
- `tests/test_outcome.py` — extended with 7 new penalize test cases

---

## Test additions (`tests/test_outcome.py`, class `TestOutcomePenalize`)

- `test_outcome_penalizes_blocks_when_signal_below_threshold`
- `test_outcome_does_not_penalize_when_signal_above_threshold`
- `test_outcome_penalize_respects_lambda_ceiling`
- `test_outcome_penalize_skips_durable_blocks`
- `test_outcome_penalize_skips_permanent_blocks`
- `test_outcome_result_includes_blocks_penalized_count`
- `test_outcome_penalize_threshold_boundary_at_exactly_threshold`

---

## Adapter usage (trading example)

With integration, the adapter is simpler — one call handles all outcomes:

```python
# outcome() handles both confidence update AND decay acceleration automatically
signal = 1.0 - brier_score
result = await memory.outcome(block_ids, signal=signal, source="brier")
# signal < 0.20 → blocks_penalized > 0 in result
# signal > 0.50 → edges reinforced, decay reset
```

No `if brier_score > THRESHOLD: await memory.penalize(...)` needed.
