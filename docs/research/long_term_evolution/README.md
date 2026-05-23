# Research: long-term evolution of elfmem

Compiled research from May 2026 exploration of how elfmem's memory dynamics
compound over years of usage. Drove decisions in ADRs 0001, 0002, 0003.

This research is preserved but is **not** the active implementation roadmap.
For what's actually being shipped, see [`ROADMAP.md`](../../../ROADMAP.md) at
the project root.

## Contents

| File | What it covers |
|---|---|
| [`closed_form_analysis.md`](closed_form_analysis.md) | Six analytical derivations (D1–D6) of structural problems |
| [`scoring_proposed_evaluation.md`](../scoring_proposed_evaluation.md) | Empirical validation of plan_memory_scoring.md proposals (lives one level up, applies broadly) |
| [`constitutional_evolution.md`](constitutional_evolution.md) | Architecture M, Model C, Model D explorations |
| [`self_architecting_agent.md`](self_architecting_agent.md) | Continuous-parameter framing; hill-climbing simulation |
| [`self_critique.md`](self_critique.md) | Honest evaluation of the above against project principles |
| [`decisions.md`](decisions.md) | Decisive synthesis: what to ship, what to defer |

## Simulation harness

The simulation infrastructure that produced these findings lives at
[`scripts/longitudinal_sim/`](../../../scripts/longitudinal_sim/). It is a
**permanent fixture** for evaluating future scoring changes — not throwaway
exploratory code.

## Reading order

If you're reading this fresh:

1. Start with `closed_form_analysis.md` — establishes the problem space mathematically
2. Then `scoring_proposed_evaluation.md` (parent dir) — what got validated
3. Then `constitutional_evolution.md` — what didn't earn its place
4. Then `self_critique.md` — honest re-evaluation
5. Then `decisions.md` — the verdict

## Why this research is preserved

The mechanisms explored here (Architecture M, ego_strength, distribute_n,
self-architecting hill-climber) didn't make it into the v0.17 roadmap because
the simulations didn't produce decisively better results than the disciplined
v0.17 baseline. But the research is preserved because:

- The structural insights (constitutional vs task knowledge as different
  *kinds*; signal inflation; asymptotic Bayesian inertness) are correct and
  may inform future decisions
- The simulation harness is reusable for any future scoring experiment
- The negative results save future contributors from rediscovering them

If you're proposing a new mechanism in this space, start by reading these
notes and confirming you have an argument the simulations didn't already
explore.
