# 0003 — Defer constitutional evolution mechanisms

**Status**: Accepted
**Date**: 2026-05-23
**Deciders**: elf (curator), Ben

## Context

Closed-form analysis (`scripts/longitudinal_sim/closed_form.py`, D1–D6) identified three structural problems with elfmem's current scoring:

1. **D1**: Beta-Binomial outcome accumulation becomes asymptotically inert by N=100 events
2. **D4**: In ATTENTION, constitutional bedrock with thematic overlap ≥ 0.75 cannot be beaten by any new content under current weights
3. **D5**: PERMANENT tier half-life is 47.5 years at typical usage rates — constitutional immortality

Four mechanism families were explored to address these problems:

- **Architecture M**: exclude constitutional from ATTENTION candidate pool; inject as preamble at frame render time
- **Model C**: M + dedicated SELF-frame reinforcement channel; constitutional earn persistence (Darwinian)
- **Model D**: Model C + distributed feedback across top-3 (fixes Model C hoarding)
- **Self-architecting agent**: hill-climb in 4-D parameter space to learn the right configuration for the user's workload

## Alternatives considered

1. **Implement all four** as a v0.17 → v0.20 phased rollout (the over-extended plan)
2. **Implement Architecture M alone** in v0.17 (simplest of the four)
3. **Implement Model C/D in v0.18** after the v0.16 substrate is in production
4. **Implement self-architecting in v0.19** as opt-in
5. **Defer all four** until production signal demands a mechanism

## Decision

**Option 5: defer all four indefinitely.** Constitutional evolution is real research and the structural insights are correct, but no mechanism has yet been validated as a clear win against the disciplined v0.17 baseline.

## Evidence

Cross-scenario simulation (`scripts/longitudinal_sim/mc_scenarios.py`, 365 days × 2 seeds × 5 scenarios):

```
scenario          baseline   M       Model C   Model D
─────────────────────────────────────────────────────────
stable             85.3%    78.4%    76.8%     76.6%
slow_drift         62.5%    72.4%    75.2%     76.8%
regime_change      44.5%    77.6%    78.7%     70.5%
quiet_burst        76.6%    77.0%    78.6%     74.4%
adversarial        65.7%    77.3%    81.9%     76.4%
```

**Architecture M** offers +33pp under regime change but **−7pp under stable identity**. No telemetry exists on real-user mix to know which population dominates.

**Model C** introduces 4 magic numbers (`EGO_POS_RATE`, `EGO_NEG_RATE`, `EGO_TIME_DECAY`, `EGO_LAMBDA_ALPHA`) and 1 new schema table. The Critic agent's earlier verdict against FSRS-5 — "fashion, not calibration" — applies cleanly: this is an invented mechanism without empirical grounding in elfmem's existing four-rhythm cognitive model.

**Model C also exhibits a hoarding failure mode** (`es_concentration = 9.5` universal across scenarios): one constitutional block dominates ego_strength accumulation. Model D fixes this but introduces a 3-7pp quality cost in 3 of 5 scenarios.

**Self-architecting agent** (`scripts/longitudinal_sim/mc_self_architect.py`) UNDERPERFORMS fixed strategies in every scenario tested:

```
scenario          baseline    M       Model C   Model D   self_architect
────────────────────────────────────────────────────────────────────────
stable             81.1%    77.3%    76.5%     76.1%     76.5%
drift              77.8%    71.9%    74.9%     75.9%     73.0%
regime_change      74.0%    76.9%    79.1%     70.0%     72.3%
```

The hill-climber correctly identifies the right *direction* for each scenario, but the magnitude is too conservative to outperform a sensible fixed default. With more shadow-eval queries, larger step sizes, momentum — it could eventually work. But that's tuning research, not a shippable feature.

## Why the deferral is the right discipline

This decision applies the explicit principle from `docs/plans/plan_memory_scoring.md` Critic agent verdict: "ship minimum, measure, then earn each layer." 

The Critic also rejected (in plan_memory_scoring.md) FSRS-5 mechanics, event log tables, hierarchical tiers, and Zettelkasten auto-linking — all on the same grounds: "complexity without measured benefit." The four mechanisms deferred here have the same shape. Re-proposing them under different vocabulary doesn't change the underlying judgment.

By P2 of the elfmind design document: **a miscalibrated self-model is worse than no self-model.** A miscalibrated self-architect is worse than a sensible fixed default. The simulation explicitly showed self-architecting underperforming fixed strategies.

## Consequences

- v0.17 ships without any of these four mechanisms.
- Research is preserved in `docs/research/constitutional_evolution.md` (compiled from the simulation findings).
- Simulation harness (`scripts/longitudinal_sim/`) is preserved as a permanent fixture for future evaluations.
- The structural insight (constitutional content is a different *kind* of content than task knowledge) is documented for future contributors.

## Trigger to revisit

Any of the following would warrant reopening this decision:

1. **Real-user signal**: production reports from multiple users that constitutional dominance is causing retrieval failures (currently: only Dmitry's report, and his cold-start symptom is addressed by v0.15.3).
2. **Telemetry**: data on real-user drift rate distribution to know if M's stable-identity tax is acceptable.
3. **Benchmark evidence**: MemoryAgentBench / LoCoMo results showing one of these mechanisms wins.
4. **Higher-confidence simulation**: re-running at N=10+ seeds across more scenarios to confirm the marginal results.

## References

- Closed-form derivations: [`scripts/longitudinal_sim/closed_form.py`](../../scripts/longitudinal_sim/closed_form.py)
- Research compilation: [`docs/research/constitutional_evolution.md`](../research/constitutional_evolution.md)
- Self-critique: [`docs/research/long_term_evolution/self_critique.md`](../research/long_term_evolution/self_critique.md)
- Related: [ADR 0002](0002-v017-scope.md)
