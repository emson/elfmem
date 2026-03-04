# Exploration 001: The Decay Showdown

## Status: complete

## Question

Three memory blocks with different decay profiles are created at the same time.
After 7 days, 30 days, and 90 days — what survives? What does this tell us
about how decay profiles map to memory types?

**IMPORTANT NOTE:** This exploration assumes pure wall-clock time-based decay.
See **Exploration 005** for discussion of more sophisticated decay models that account
for activity, staleness vs. displacement, and the three-layer interest model.

## Setup

```yaml
blocks:
  A:
    content: "I believe in writing clear, readable code above all"
    category: identity
    confidence: 0.85
    decay_lambda: 0.0001    # core profile
    is_self: true
    reinforcement_count: 0
    hours_since_reinforcement: 0

  B:
    content: "Python's asyncio uses an event loop for concurrent I/O"
    category: python
    confidence: 0.70
    decay_lambda: 0.01      # standard profile
    is_self: false
    reinforcement_count: 0
    hours_since_reinforcement: 0

  C:
    content: "Had a good debugging session today, found race condition"
    category: observation
    confidence: 0.50
    decay_lambda: 0.1       # ephemeral profile
    is_self: false
    reinforcement_count: 0
    hours_since_reinforcement: 0

edges: []
prune_threshold: 0.05  # blocks below this decay_weight are pruned
```

## Computation

Formula: `decay_weight = e^(-λ × t)` where t is hours.

### After 7 days (168 hours)

```
Block A (core):      e^(-0.0001 × 168) = e^(-0.0168) = 0.9834
Block B (standard):  e^(-0.01   × 168) = e^(-1.68)   = 0.1864
Block C (ephemeral): e^(-0.1    × 168) = e^(-16.8)    = 0.0000005 ≈ 0.0000
```

| Block | Profile   | 7-day decay_weight | Status  |
|-------|-----------|-------------------|---------|
| A     | core      | 0.9834            | alive   |
| B     | standard  | 0.1864            | alive   |
| C     | ephemeral | 0.0000            | PRUNED  |

Block C is pruned after just ~3 days (falls below 0.05 at ~30 hours).

### After 30 days (720 hours)

```
Block A (core):      e^(-0.0001 × 720) = e^(-0.072)  = 0.9306
Block B (standard):  e^(-0.01   × 720) = e^(-7.2)    = 0.00075
Block C: already pruned
```

| Block | Profile   | 30-day decay_weight | Status  |
|-------|-----------|---------------------|---------|
| A     | core      | 0.9306              | alive   |
| B     | standard  | 0.0007              | PRUNED  |
| C     | ephemeral | —                   | pruned  |

Block B is pruned around day 8-9 (falls below 0.05 at ~200 hours ≈ 8.3 days).

### After 90 days (2160 hours)

```
Block A (core):      e^(-0.0001 × 2160) = e^(-0.216) = 0.8058
Block B: already pruned
Block C: already pruned
```

| Block | Profile   | 90-day decay_weight | Status  |
|-------|-----------|---------------------|---------|
| A     | core      | 0.8058              | alive   |
| B     | standard  | —                   | pruned  |
| C     | ephemeral | —                   | pruned  |

### When does each block cross the prune threshold (0.05)?

Solve: `e^(-λ × t) = 0.05` → `t = -ln(0.05) / λ` → `t = 2.996 / λ`

```
Block A (core):      2.996 / 0.0001 = 29,957 hours ≈ 1,248 days ≈ 3.4 years
Block B (standard):  2.996 / 0.01   = 299.6 hours  ≈ 12.5 days
Block C (ephemeral): 2.996 / 0.1    = 29.96 hours  ≈ 1.25 days
```

## Result

```yaml
prune_timeline:
  C_ephemeral: { hours: 30,    days: 1.25,   human: "gone by tomorrow" }
  B_standard:  { hours: 300,   days: 12.5,   human: "gone in two weeks" }
  A_core:      { hours: 29957, days: 1248,   human: "lasts 3.4 years" }

survival_at:
  7_days:  [A, B]       # C pruned
  30_days: [A]          # B pruned
  90_days: [A]          # only identity survives
  1_year:  [A]          # still at 0.58 decay_weight

natural_interpretation:
  ephemeral: "working memory — today's observations, transient thoughts"
  standard:  "knowledge — needs reinforcement within ~2 weeks or dies"
  core:      "identity — persists for years without any reinforcement"
```

## Insight

1. **Standard profile is the critical one.** It's the workhorse for actual knowledge,
   but it dies in 12.5 days without reinforcement. This means the consolidation
   pipeline MUST create edges and the retrieval system MUST reinforce blocks during
   use. Otherwise, all learned knowledge silently disappears.

2. **Ephemeral is correctly ephemeral.** A daily observation lasting ~30 hours feels
   right. If you don't act on it by tomorrow, it fades. This matches human working
   memory behavior.

3. **Core is extremely sticky.** 80% retention at 90 days, 58% at 1 year. Identity
   beliefs should not need active reinforcement to persist. This feels correct.

4. **The "durable" profile (λ=0.001) fills the gap.** Half-life of 28.9 days, prune
   at ~125 days. This is the right profile for "things I've learned and use
   occasionally" — important knowledge that doesn't need weekly reinforcement.

5. **Reinforcement is the survival mechanism for standard blocks.** Every time a
   standard block is accessed during frame assembly or search, its
   `hours_since_reinforcement` resets to 0. A block accessed once per week
   never decays past ~50%. This creates natural selection: useful knowledge
   survives, unused knowledge fades.

## Limitations & Future Explorations

This exploration assumes **pure wall-clock time-based decay**. Real systems need more sophistication:

1. **Activity-Aware Decay (Exploration 005):** System idle during holiday shouldn't kill knowledge.
   What if we only count active session hours? Or use dual decay rates (fast when active, slow when idle)?

2. **Staleness vs. Displacement (Exploration 005):** Not all decay is time-based.
   - Ephemeral blocks (meeting notes) ARE time-sensitive — should decay by staleness
   - Standard blocks (knowledge) are NOT time-sensitive — should decay by displacement (new info) + usage (missed retrievals)
   - Core blocks (identity) should have minimal decay regardless of time

3. **Self-Interest Model (Exploration 004):** Self-aligned blocks may deserve different decay treatment.
   - Self-aligned blocks could use durable profile automatically
   - Non-aligned blocks could decay faster unless reinforced
   - This creates natural selection without hard filtering

## Variations

- [ ] What if Block B (standard) is reinforced once at day 5? Does it survive to day 30?
- [ ] What happens with the "durable" profile (λ=0.001)?
- [ ] What if the prune threshold is 0.10 instead of 0.05? How much earlier do blocks die?
- [ ] What if we add a "short" profile (λ=0.03) — where does it fall in the timeline?
- [ ] What if we only count active session hours, not wall-clock time? (See Exploration 005)
