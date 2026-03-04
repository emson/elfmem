# Playground: Decay — Session-Aware Decay Clock

## Status: Draft

## Subsystem Specification

Decay determines how much a block's "freshness" contributes to its score.
It is **computed at query time**, not stored, using:

```
recency = e^(-λ × hours_since_reinforcement)
```

Where:
```
hours_since_reinforcement = total_active_hours - last_reinforced_at
```

- `total_active_hours` — global counter incremented during active sessions only
- `last_reinforced_at` — cumulative active hours at the moment of last reinforcement
- `λ` (lambda) — decay rate determined by the block's decay tier

This is **session-aware decay**: the clock only runs during active use. Knowledge
does not decay over weekends, holidays, or idle periods.

---

## The Four Decay Tiers

From exploration 024 (refined from explorations 001 and 005):

| Tier | λ Value | Half-Life | Use Case |
|------|---------|-----------|----------|
| permanent | 0.00001 | ~80,000 active hours | Constitutional/core identity blocks |
| durable | 0.001 | ~693 active hours | Stable knowledge, self/* blocks |
| standard | 0.010 | ~69 active hours | General knowledge |
| ephemeral | 0.050 | ~14 active hours | Observations, session-specific facts |

Half-life formula: `h½ = ln(2) / λ = 0.693 / λ`

### Prune Threshold

A block with `recency < 0.05` is a candidate for archival at `curate()`.

---

## Parameters

```yaml
decay_tiers:
  permanent:  0.00001
  durable:    0.001
  standard:   0.010
  ephemeral:  0.050

prune_threshold: 0.05     # blocks below this are archived at curate()
search_window_hours: 200  # pre-filter: only blocks reinforced within this window
```

---

## Test Suite

### TC-D-001: Standard Block Survival Timeline

**Purpose:** Verify the half-life formula and confirm standard blocks (~69 hours) require
regular reinforcement to survive.

**Given:**
```yaml
block:
  decay_tier: standard   # λ = 0.010
  last_reinforced_at: 0  # just reinforced
current:
  total_active_hours: [0, 10, 25, 50, 69, 100, 138, 200, 300]
```

**When:** Compute `recency = e^(-0.010 × hours_since)` for each point

**Then:**
```
hours=0:   recency = e^(0)      = 1.000
hours=10:  recency = e^(-0.1)   = 0.905
hours=25:  recency = e^(-0.25)  = 0.779
hours=50:  recency = e^(-0.5)   = 0.607
hours=69:  recency = e^(-0.69)  = 0.501   ← half-life
hours=100: recency = e^(-1.0)   = 0.368
hours=138: recency = e^(-1.38)  = 0.252
hours=200: recency = e^(-2.0)   = 0.135
hours=300: recency = e^(-3.0)   = 0.050   ← prune threshold
```

**Expected:** Values within ±0.001; prune threshold reached at ~300 active hours
**Status:** NOT YET RUN

---

### TC-D-002: Ephemeral Block Survival Timeline

**Purpose:** Ephemeral blocks (λ=0.05) should reach the prune threshold ~6× faster
than standard blocks.

**Given:**
```yaml
block:
  decay_tier: ephemeral   # λ = 0.050
  last_reinforced_at: 0
```

**When:** Compute prune threshold crossing point

**Then:**
```
prune at: -ln(0.05) / 0.050 = 2.996 / 0.050 ≈ 60 active hours
```

At 60 active hours, recency ≈ 0.050 — prune threshold reached.

**Expected:** Ephemeral blocks prunable after ~60 active hours without reinforcement
**Tolerance:** ±1 hour
**Status:** NOT YET RUN

---

### TC-D-003: Permanent Block Near-Immortality

**Purpose:** Constitutional/permanent blocks (λ=0.00001) should not reach prune
threshold within any realistic usage span.

**Given:**
```yaml
block:
  decay_tier: permanent   # λ = 0.00001
  last_reinforced_at: 0
```

**When:** Compute hours until prune threshold

**Then:**
```
prune at: -ln(0.05) / 0.00001 = 2.996 / 0.00001 = 299,600 active hours
```

At 8 active hours/day, 5 days/week: `299,600 / (8×5×52) ≈ 144 years`

**Expected:** Permanent blocks effectively never decay in practice
**Status:** NOT YET RUN

---

### TC-D-004: Session-Aware Clock Pauses Between Sessions

**Purpose:** Verify that decay only accumulates during active sessions, not during
idle time between sessions.

**Given:**
```yaml
block:
  decay_tier: standard  # λ = 0.010
  last_reinforced_at: 50   # reinforced at 50 cumulative active hours

session_history:
  - session_1: duration=10h  (total_active_hours goes 0→10)
  - gap: 72 real-world hours (no session; total_active_hours stays at 10)
  - session_2: duration=5h   (total_active_hours goes 10→15)
  - gap: 168 real-world hours (weekend; total_active_hours stays at 15)
  - session_3: start         (total_active_hours = 15)
```

**When:** Block was last reinforced at hour 50 and we check at total_active_hours=15

Wait — this block hasn't been reinforced yet. Let me revise.

**Given:**
```yaml
block:
  decay_tier: standard
  last_reinforced_at: 5    # reinforced during session_1 at hour 5

current total_active_hours: 15   # after session_2

hours_since_reinforcement = 15 - 5 = 10
recency = e^(-0.010 × 10) = e^(-0.1) = 0.905
```

**Expected:** `recency = 0.905`, NOT `e^(-0.010 × 250)` (which is what wall-clock decay would compute, incorrectly treating the 240-hour gap as active time)

**Key assertion:** Two weeks of calendar time is 10 active hours, not 240 hours.
Session-aware decay gives 0.905; wall-clock decay would give 0.082.

**Status:** NOT YET RUN

---

### TC-D-005: Reinforcement Resets Decay Clock

**Purpose:** Reinforcing a block updates `last_reinforced_at` to the current
`total_active_hours`, resetting the decay clock to zero.

**Given:**
```yaml
block:
  decay_tier: standard    # λ = 0.010
  last_reinforced_at: 20  # reinforced 30 active hours ago
total_active_hours: 50

# Before reinforcement:
hours_since = 50 - 20 = 30
recency_before = e^(-0.010 × 30) = e^(-0.3) = 0.741
```

**When:** Block is recalled (reinforced); `last_reinforced_at` updated to 50

**Then:**
```
# After reinforcement:
hours_since = 50 - 50 = 0
recency_after = e^(0) = 1.000
```

**Expected:** `recency_after = 1.000` — fresh as if just created
**Status:** NOT YET RUN

---

### TC-D-006: Pre-Filter Correctly Excludes Old Blocks

**Purpose:** The pre-filter `WHERE last_reinforced_at > (total_active_hours - search_window_hours)`
should exclude blocks that haven't been reinforced within the search window.

**Given:**
```yaml
total_active_hours: 300
search_window_hours: 200   # default

# Pre-filter threshold: 300 - 200 = 100

blocks:
  A: last_reinforced_at: 150   # 150 active hours ago → included (150 > 100)
  B: last_reinforced_at: 80    # 220 active hours ago → excluded (80 < 100)
  C: last_reinforced_at: 100   # exactly at threshold → excluded (100 NOT > 100)
  D: last_reinforced_at: 101   # just within window → included (101 > 100)
```

**When:** Apply pre-filter at `total_active_hours=300`

**Then:**
- Block A: **INCLUDED**
- Block B: **EXCLUDED**
- Block C: **EXCLUDED** (strictly greater than, not >=)
- Block D: **INCLUDED**

**Expected:** A and D pass; B and C excluded
**Status:** NOT YET RUN

---

### TC-D-007: Durable Block Survives Moderate Absence

**Purpose:** Durable blocks (λ=0.001, half-life ~693 hours) should remain well
above prune threshold even after 300 active hours without reinforcement.

**Given:**
```yaml
block:
  decay_tier: durable  # λ = 0.001
  last_reinforced_at: 0
current total_active_hours: 300
```

**When:** `recency = e^(-0.001 × 300) = e^(-0.3) = 0.741`

**Then:** `recency = 0.741` — well above prune threshold (0.05)

Durable blocks survive ~3,000 active hours (equivalent to years at normal usage)
without reinforcement.

**Expected:** `recency = 0.741`; block not near prune threshold
**Tolerance:** ±0.001
**Status:** NOT YET RUN

---

### TC-D-008: Decay Tier Determined by Self-Tags (Lowest λ Wins)

**Purpose:** A block with multiple self tags should use the minimum λ (slowest decay)
from among all applicable tiers.

**Given:**
```yaml
block:
  tags: ["self/value", "self/constitutional"]
  # self/value → durable (λ=0.001)
  # self/constitutional → permanent (λ=0.00001)
```

**When:** Determine effective decay tier for the block

**Then:** Block uses `λ = 0.00001` (permanent — lowest λ wins)

Constitutional tag always wins, regardless of other tags present.

**Expected:** Effective λ = 0.00001
**Status:** NOT YET RUN

---

### TC-D-009: Standard Block Tag-Free Defaults to Standard Tier

**Purpose:** Blocks with no self/* tags default to standard decay.

**Given:**
```yaml
block:
  tags: ["python", "async", "best-practice"]  # no self/* tags
```

**When:** Determine effective decay tier

**Then:** Block uses `λ = 0.010` (standard)

**Expected:** Effective λ = 0.010
**Status:** NOT YET RUN

---

### TC-D-010: Archive Reason Set Correctly at Curate

**Purpose:** When `curate()` archives a block, the `archive_reason` reflects the cause.

**Given:**
```yaml
block_A:
  recency: 0.03   # below prune_threshold=0.05
  status: active

block_B:
  recency: 0.95   # healthy
  status: active
  # but a near-duplicate of a newer block with higher confidence
  near_duplicate_of: block_C
  is_superseded: true
```

**When:** `curate()` runs

**Then:**
- Block A archived with `archive_reason = "decayed"` (recency < threshold)
- Block B archived with `archive_reason = "superseded"` (replaced by block_C)

**Expected:** Correct archive_reason values on each block
**Status:** NOT YET RUN

---

## Parameter Tuning

### PT-1: Prune Threshold (currently 0.05)

**Question:** Is 0.05 the right cutoff, or should it be 0.03 (more permissive) or
0.08 (more aggressive pruning)?

**Scenario:**
```
λ=0.010 (standard):
  prune at 0.05 → ~300 active hours
  prune at 0.03 → ~350 active hours (17% longer survival)
  prune at 0.08 → ~253 active hours (16% shorter survival)
```

**Recommendation:** Start at 0.05. If agents accumulate too many stale blocks
(visible in curate() pruning stats), lower to 0.03. If memory stays too large,
raise to 0.08.

---

### PT-2: Search Window Hours (currently 200)

**Question:** Does `search_window_hours=200` correctly balance excluding stale blocks
vs. accidentally filtering out recently-used-but-old blocks?

**Scenario:** At 200h, a standard block has recency = 0.135. It could still win
composite scoring if it has high centrality and reinforcement. But at 200h, it's
unlikely to be among the top-K results anyway. Pre-filter is a performance
optimisation, not a quality gate — it's safe when it only excludes blocks that
wouldn't win scoring.

**Key invariant to verify:** No block excluded by pre-filter would have made the
final top-K with actual composite scoring. If any such block exists in a test
corpus, `search_window_hours` is too aggressive.

---

### PT-3: Ephemeral vs. Standard Tier Boundary

**Question:** Should there be a tier between ephemeral (14h half-life) and standard
(69h half-life)? Is the gap too large?

**Scenario:** An "observation" block that describes a meeting outcome. It's useful
for 2–3 sessions (a few days of use) but not long-term. Ephemeral at 14h might be
too short; standard at 69h might be too long.

**Hypothesis:** The four tiers are sufficient if users pick correctly. A fifth tier
(`transient`, λ=0.025, half-life ~28h) would address the gap if needed.

---

### PT-4: Curate Interval (currently 40 active hours)

**Question:** Should `curate()` run every 40 active hours, more frequently, or less?

**Tradeoff:**
- Too frequent: wasted computation, premature pruning of potentially-reinforced blocks
- Too infrequent: inbox grows stale; decayed blocks clutter retrieval

At 4 active hours/session, 40h = ~10 sessions. That seems right — weekly maintenance
for a moderate-use agent.

**Scenario:** Run 3 test agents with curate at 20h, 40h, 80h. Compare inbox size,
prune rates, and retrieval precision after 200 active hours.

---

## Open Assertions

1. `recency` is always in `[0.0, 1.0]`
2. `hours_since_reinforcement` is always `≥ 0`
3. Reinforcing a block never decreases `recency`
4. A block reinforced at `total_active_hours = T` always has `recency = 1.0` when queried at `T`
5. The four decay tiers are mutually exclusive (each block has exactly one effective λ)
6. Archive operation is irreversible within a session (no un-archive)

---

## Python Test Sketch

```python
# elfmem/tests/test_decay.py

import math
import pytest
from elfmem.scoring import compute_recency, DecayTier

def test_standard_half_life():
    # Standard block (λ=0.010) half-life = ln(2)/0.010 ≈ 69.3 hours
    recency = compute_recency(tier=DecayTier.STANDARD, hours_since=69.3)
    assert abs(recency - 0.500) < 0.005

def test_prune_threshold_standard():
    # Standard block reaches prune threshold at ~300 active hours
    recency = compute_recency(tier=DecayTier.STANDARD, hours_since=300)
    assert recency <= 0.05 + 0.005  # allow small tolerance

def test_permanent_block_near_immortal():
    # After 10,000 active hours, permanent block still well above prune threshold
    recency = compute_recency(tier=DecayTier.PERMANENT, hours_since=10_000)
    assert recency > 0.90

def test_reinforcement_resets_clock(db):
    block = db.get_block("B1")
    db.reinforce(block.id)
    recency = compute_recency(
        tier=block.decay_tier,
        hours_since=0  # just reinforced
    )
    assert recency == 1.0

def test_session_aware_not_wall_clock():
    # 10 active hours since reinforcement, regardless of calendar time
    recency = compute_recency(tier=DecayTier.STANDARD, hours_since=10)
    assert abs(recency - 0.905) < 0.001
    # NOT e^(-0.010 * 250) even if 250 calendar hours have passed

def test_pre_filter_excludes_old_blocks(db):
    db.set_total_active_hours(300)
    old_block = db.get_block("old")  # last_reinforced_at=80
    new_block = db.get_block("new")  # last_reinforced_at=150

    results = db.pre_filter(search_window_hours=200)
    assert old_block.id not in results
    assert new_block.id in results
```
