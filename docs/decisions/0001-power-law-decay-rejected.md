# 0001 — Power-law decay rejected

**Status**: Accepted
**Date**: 2026-05-23
**Deciders**: elf (curator), Ben

## Context

`docs/plans/plan_memory_scoring.md` proposed shipping power-law retrievability decay as an opt-in v0.17 feature, behind a `memory.use_power_law_decay: bool = False` flag. The proposal cited FSRS-5 research showing power-law beats exponential for flashcard-style spaced repetition.

The research agent (cited in plan_memory_scoring.md) graded this evidence A for flashcards but D for agent memory — the empirical case had not been made in our domain.

## Alternatives considered

1. **Ship as default**: replace `exp(-λt)` with `(1 + 0.5t/stability)^(-0.5)` for all users
2. **Ship as opt-in flag** (the original plan): expose the formula behind a config flag for users to A/B
3. **Reject entirely**: don't ship the mechanism at all
4. **Defer**: wait for more research

## Decision

**Reject entirely.** Drop from v0.17 plans. Document the empirical refutation here so the decision is searchable.

## Evidence

Simulation (`scripts/longitudinal_sim/mc_scoring_proposed.py`) tested four scenarios across 4 strategies × 2 seeds. Power-law decay (`v017_full`) underperformed across every scenario:

```
scenario          v016 (exponential)   v017_full (power-law)
─────────────────────────────────────────────────────────────
baseline             80.7%                 75.3%   (−5.4pp)
weekly_rescore       81.4%                 76.4%   (−5.0pp)
long_horizon         72.4%                 72.3%   (essentially flat)
uncertain_mix        78.4%                 70.7%   (−7.6pp)
```

Recent reach (plasticity) catastrophically degraded:

```
scenario          v016                  v017_full
─────────────────────────────────────────────────
baseline             78.4%                 25.2%   (−53pp)
weekly_rescore       78.0%                 13.6%   (−64pp)
long_horizon         64.0%                 28.4%   (−36pp)
uncertain_mix        76.8%                 33.6%   (−43pp)
```

Mechanism: at t=1000 hours, exponential gives recency = 4.5×10⁻⁵; power-law gives 0.41. A 10,000-fold difference at the long-time end. Power-law makes one-year-old blocks competitive against one-day-old blocks. Top-K fills with stale content.

## Consequences

- v0.17 ships `exp(-λt)` decay only. No flag, no opt-in.
- Documentation in `plan_memory_scoring.md` should add this ADR's reference under "What we are explicitly NOT adopting."
- Future contributors should consult this ADR before reproposing power-law.

## Trigger to revisit

Only if MemoryAgentBench (or equivalent agent-memory benchmark) shows power-law wins on agent workloads with statistical significance. None reported as of 2026-05.

## References

- Original proposal: [`docs/plans/plan_memory_scoring.md`](../plans/plan_memory_scoring.md) — "v0.17.0 — Exploration bonus + power-law experiment"
- Simulation: [`scripts/longitudinal_sim/mc_scoring_proposed.py`](../../scripts/longitudinal_sim/mc_scoring_proposed.py)
- Findings: [`docs/research/scoring_proposed_evaluation.md`](../research/scoring_proposed_evaluation.md)
