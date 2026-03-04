# Exploration 005: Beyond Wall-Clock Decay

## Status: complete

## Question

Pure wall-clock decay (Exploration 001) kills knowledge after 12.5 days without reinforcement,
even during idle periods (holidays). But knowledge doesn't actually become less useful just
because time passes. What factors actually drive memory loss, and how can we model them separately?

---

## The Holiday Problem

**Scenario:** You're a Python developer. After 14 days of work learning asyncio, you take a 2-week
holiday. On day 14 of the holiday (28 days elapsed), you return. Your asyncio knowledge should
still be there.

**Current model result:**
```
standard block (λ=0.01):  e^(-0.01 × 672h) = 0.0000000000001  → PRUNED
```

**Problem:** Asyncio is still valid. You didn't forget it. Nothing in the world changed.
The pure time-based model is fundamentally wrong.

---

## Setup: What Actually Causes Memory Loss?

Looking at cognitive science and information theory, there are at least four distinct mechanisms:

| Mechanism | What drives it | Time-dependent? | Example |
|-----------|----------------|-----------------|---------|
| **Staleness** | Real-world time | YES | "Meeting at 3pm today" expires by tomorrow |
| **Interference** | New similar information | NO | "Python has GIL" is displaced by "Python 3.13 removes GIL" |
| **Disuse** | Selective attention patterns | NO | You search "concurrency" but this block never surfaces → unused |
| **Obsolescence** | External world changes | NO | Library version deprecates, feature removed |

**Key insight:** Only **staleness** is truly time-dependent. The others are driven by **events**
(new information, new queries, world changes).

This suggests:
- **Ephemeral blocks** (meeting notes, daily observations) NEED staleness decay
- **Standard blocks** (learned knowledge) need interference + disuse decay, NOT staleness
- **Core blocks** (identity) need minimal decay, only from obsolescence

---

## Model A: Session-Aware Decay

Count only **active session time**, not wall-clock time.

```python
# Simpler approach: freeze decay during idle periods
decay_weight = e^(-λ × active_session_hours)

# Only increments when system is in active use
# Holiday = time freeze for decay clock
```

### Example: Two-Week Holiday

```yaml
scenario:
  day_0_to_14:   active work, using system constantly
  day_15_to_28:  holiday, system idle, no activity
  day_29+:       return from holiday

# Current model (wall-clock)
after_28_days_wall_clock:
  standard_block: e^(-0.01 × 672h) = 0.000000001 → PRUNED

# Session-aware model
active_session_hours: 14d × 8h/day = 112h (assume 8 hours active per day)
after_28_days_session_aware:
  standard_block: e^(-0.01 × 112h) = 0.322 → SURVIVES
```

### Pros & Cons

✓ Simple to implement (just count active minutes)
✓ Solves the holiday problem
✗ Ephemeral blocks still need to die during idle time
  (Meeting note from day 14 should be gone by day 28, even if system is idle)
✗ Time-sensitive content (dated knowledge) doesn't decay correctly

---

## Model B: Dual-Rate Decay

Two decay rates per block: fast when active, slow when idle.

```python
if system_is_active:
    λ_effective = λ_active         # normal rate
else:
    λ_effective = λ_idle           # reduced rate, e.g., λ × 0.1
```

### Example Configuration

```yaml
decay_profiles:
  ephemeral:
    lambda_active: 0.1      # half-life ~7h during active use
    lambda_idle: 0.01       # half-life ~69h during idle
    # Still dies in ~3 days even idle (time-sensitive)

  standard:
    lambda_active: 0.01     # half-life ~2.9 days during active use
    lambda_idle: 0.001      # half-life ~29 days during idle
    # Survives 2 weeks idle without decay

  core:
    lambda_active: 0.0001
    lambda_idle: 0.00001    # barely decays even during idle
```

### Example: Two-Week Holiday (Dual-Rate)

```yaml
# Ephemeral block from day 14 work session
ephemeral_block:
  day_0_to_14:   e^(-0.1 × 112h) = 0.0000001  → PRUNED immediately (correct!)
  day_14_to_28:  already pruned

# Standard block from day 14 work session
standard_block:
  day_0_to_14:   active 112h  → e^(-0.01 × 112) = 0.322
  day_14_to_28:  idle 336h    → e^(-0.001 × 336) = 0.715
  combined:      0.322 × 0.715 = 0.230  → SURVIVES (correct!)

# Core block (always present)
core_block:
  day_0_to_14:   e^(-0.0001 × 112) = 0.989
  day_14_to_28:  e^(-0.00001 × 336) = 0.997
  combined:      0.989 × 0.997 = 0.986  → SURVIVES with high confidence
```

### Pros & Cons

✓ Solves the holiday problem for standard blocks
✓ Ephemeral blocks still decay quickly (even idle)
✓ Core blocks are nearly immune to idle time
✗ Still assumes all knowledge decays with time
✗ Doesn't account for interference (new knowledge displacing old)

---

## Model C: Multi-Factor Decay (The Sophisticated Model)

Separate decay into three independent factors:

```
effective_relevance = staleness × displacement × usage_signal

staleness(t) = e^(-λ_staleness × t)           # time-based
displacement(B) = 1 - (interference_score × sensitivity)  # event-based
usage_signal(B) = e^(-λ_usage × missed_retrievals)        # usage-based
```

### Staleness Factor

Only for inherently time-sensitive content:

```yaml
staleness_profiles:
  ephemeral:    λ_staleness = 0.1   # meeting notes, daily observations
  short:        λ_staleness = 0.03
  standard:     λ_staleness = 0.0   # knowledge isn't time-sensitive!
  durable:      λ_staleness = 0.0
  core:         λ_staleness = 0.0   # identity isn't time-sensitive
```

**Key:** Most blocks don't decay from staleness — they don't care about wall-clock time.

### Displacement Factor

Driven by new, similar content arriving:

```python
# When new block B' arrives, existing related blocks decay faster
for existing_block in graph:
    similarity = cosine(B'.embedding, existing_block.embedding)
    if similarity > 0.70:
        # High similarity = B' interferes with existing
        displacement = 1 - (similarity × displacement_sensitivity)
        # High similarity + high sensitivity → strong interference
        # displacement = 1 - (0.85 × 0.5) = 0.575  → 57.5% relevance
```

**Example:** "Python removes GIL" arrives → "Python has GIL" block decays faster (interference).

### Usage Signal Factor

Driven by retrieval patterns in queries:

```python
# Every time a relevant query is issued, we track:
missed_retrievals = 0
retrieved = False

for query in query_stream:
    candidates = retrieve_candidates(query)
    if block in candidates and block.score < threshold:
        missed_retrievals += 1   # relevant but not selected
    elif block in result_set:
        missed_retrievals = 0    # reinforced, reset counter

usage_decay = e^(-λ_usage × missed_retrievals)
# λ_usage = 0.05 per missed retrieval
# After 5 missed retrievals: e^(-0.25) = 0.778 → 78% relevance
# After 20 missed retrievals: e^(-1.0) = 0.368 → pruned
```

**Example:** You search "concurrency" regularly but this block's similarity is too low,
so it's never included. After 20 misses, it's pruned.

### Combined Model

```python
# For a given block at time t:
effective_decay_weight = staleness(t) × displacement × usage_signal

# Example: standard block with no new interference, 1 missed retrieval
standard_no_interference:
  staleness = e^(-0 × t) = 1.0           # not time-sensitive
  displacement = 1.0                      # no new content
  usage_signal = e^(-0.05 × 1) = 0.951
  → 1.0 × 1.0 × 0.951 = 0.951 (95% alive)

# Example: standard block with high interference, 5 missed retrievals
standard_with_interference:
  staleness = 1.0
  displacement = 1 - (0.85 × 0.5) = 0.575
  usage_signal = e^(-0.05 × 5) = 0.778
  → 1.0 × 0.575 × 0.778 = 0.448 (45% alive)
```

---

## Comparison: The Two-Week Holiday

```yaml
scenario: standard block (asyncio knowledge)
  day_0_to_14: active, no interfering blocks, reinforced 3 times
  day_14_to_28: idle, no new content, no reinforcement

# Model A: Session-Aware
active_hours = 112
decay_weight = e^(-0.01 × 112) = 0.322  → SURVIVES ✓

# Model B: Dual-Rate
active_decay = e^(-0.01 × 112) = 0.322
idle_decay = e^(-0.001 × 336) = 0.715
combined = 0.322 × 0.715 = 0.230  → SURVIVES ✓

# Model C: Multi-Factor
staleness = 1.0           (not time-sensitive)
displacement = 1.0        (no new content)
usage_signal = 0.95       (rarely searched but not actively missed)
effective = 1.0 × 1.0 × 0.95 = 0.95  → STRONGLY SURVIVES ✓✓
```

All three solve the holiday problem. But Model C reveals the **why**: knowledge survives
because:
- It's not time-sensitive (staleness = 1.0)
- Nothing new displaced it (displacement = 1.0)
- It wasn't actively rejected (usage = high)

---

## The Deep Insight

**Different content types need different decay mechanisms:**

| Block Type | Staleness | Displacement | Usage | Formula |
|------------|-----------|--------------|-------|---------|
| Meeting note | YES | NO | NO | `staleness(t)` |
| Learned knowledge | NO | YES | YES | `displacement × usage` |
| Durable concept | WEAK | WEAK | WEAK | `weak_displacement × weak_usage` |
| Core identity | NO | NO | NO | stable, barely decays |

Current λ-based model conflates all of these. The right model separates them.

---

## Design Decision Recommendation

**Phase 1 (MVP):** Keep session-aware decay (Model A)
- Simplest to implement
- Solves the holiday problem
- Ephemeral blocks still decay quickly enough
- Acceptable approximation

**Phase 2 (v1.1):** Add displacement tracking (Model C, partial)
- When new blocks consolidate, compute similarity to existing
- Increase decay of highly-similar blocks
- Unlocks the "interference" mechanism
- Doesn't require activity tracking

**Phase 3 (v1.2):** Full multi-factor decay (Model C)
- Track missed retrievals
- Implement usage-based decay
- Full separation of staleness/displacement/usage
- Requires retrieval log instrumentation

---

## Variations

- [ ] Implement Model A (session-aware) in Phase 1: how do metrics change?
- [ ] What's the right idle_factor for dual-rate decay? (0.01, 0.05, 0.1?)
- [ ] How should displacement_sensitivity vary by category?
- [ ] Can we detect interference automatically (high similarity to recent blocks)?
- [ ] What happens if a missed retrieval counter resets on ANY block reinforcement?
- [ ] Should ephemeral blocks use dual-rate at all, or always pure staleness?

---

## Related Explorations

- **004:** Self-Interest Model — how self alignment affects decay profile selection
- **001:** Basic Decay — the wall-clock model this exploration improves upon
- **006:** (future) Activity-based consolidation — how session activity affects when consolidation triggers
