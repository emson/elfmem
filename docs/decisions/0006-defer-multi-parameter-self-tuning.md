# 0006 — Defer multi-parameter self-tuning

**Status**: Accepted
**Date**: 2026-06-02
**Deciders**: Ben Emson, elf

## Context

[Issue #73](https://github.com/emson/elfmem/issues/73) observed that
`ConsolidationPolicy` adapts only `effective_threshold` (the inbox-size
trigger), while four other parameters in the consolidation pipeline remain
static constants in `src/elfmem/operations/consolidate.py`:

- `EDGE_SCORE_THRESHOLD = 0.45` (line 49)
- `CONTRADICTION_THRESHOLD = 0.80` (line 51)
- `CONTRADICTION_SIMILARITY_PREFILTER = 0.40` (line 54)
- `decay_lambda_for_tier(tier)` (deterministic per-tag mapping)

The proposal: generalise the policy to self-tune these too.

The full exploration is preserved in
[`docs/plans/issue_self_tune_research.md`](../plans/issue_self_tune_research.md).
This ADR records the decision in the shorter form needed for future
re-litigation.

## Alternatives considered

**A — Outcome-driven (reuse `outcome()`)**. Attribute per-block feedback back
to the consolidation cycle's parameters; drift parameters based on Bayesian
belief about which setting produced good retrievals. *Disqualified*:
attribution from per-block outcomes to per-parameter credit is the
multi-armed-bandit problem in disguise; we have no infrastructure for it.
`outcome()` already updates block confidence — repurposing it for policy
feedback conflates two signals that must stay independent.

**B — Metric-driven weighted thresholds**. Replace each threshold with a
learned weighted sum of multi-signal features (cosine, tag overlap,
recency, …). *Disqualified*: trades 1 parameter for 5 weights; same
attribution problem; loses grep-ability and reviewability of constants.
`_composite_edge_score()` is already a static weighted sum — making the
weights adaptive would compound the issue, not solve it.

**C — Hierarchical semantic profiles**. User declares one of
`research | conversation | factual | creative`; a hand-written formula
derives all five parameters. *Disqualified*: each formula introduces two
new constants (`0.30 + 0.40 * x`), so five formulas mint ten new magic
numbers — the opposite of axiom 1 ("no magic numbers"). The 5 profile
dimensions are not independently validated to be orthogonal or predictive.

**D — Per-frame banking**. Different retrieval frames
(`self / attention / task / simulate`) get different consolidation
parameters. *Disqualified*: category error. Frames apply at recall time;
consolidation runs once and produces blocks consumed by all frames.
Partitioning consolidation parameters by frame would require multiple
consolidations of the same block, contradicting the dedup invariant.

**E — Three-layer hybrid (A + C + D)**. *Disqualified*: inherits the
problems of all three. "Each layer is small" does not make the stack small.

[ADR 0003](0003-defer-constitutional-evolution.md) already deferred a
related proposal ("self-architecting agent hill-climbs parameter space")
on simulation evidence that it underperforms fixed strategies. The four
"missing" knobs have weaker feedback signals than `effective_threshold`
and would compound that result.

## Decision

**Do not extend adaptive tuning.** Ship one observability delta:
`ConsolidationHealthMetrics` on `ConsolidateResult.health` — five
diagnostic ratios per cycle, computed from counters that already exist in
`_collect_decisions`. No new public API surface, no new constants, no
behavioural change.

This closes the issue with *evidence-gathering machinery* rather than
*adaptation machinery*. If any of the four static thresholds is actually
misbehaving, the metrics will reveal it. If none of them is, we have
spent ~130 LOC to settle the question with data instead of speculation.

## Consequences

- `EDGE_SCORE_THRESHOLD`, `CONTRADICTION_THRESHOLD`,
  `CONTRADICTION_SIMILARITY_PREFILTER`, and `decay_lambda_for_tier` remain
  static.
- `ConsolidationPolicy` continues tuning only `effective_threshold`.
- `ConsolidateResult.to_dict()` gains one additive `health` key; any
  downstream JSON consumer sees the key but no existing key changed.
- All existing `ConsolidateResult(...)` constructor sites continue to work
  unchanged (`health` defaults to `None`).
- Reopening this question requires producing:
  - ≥30 consecutive cycles of a specific `health` field outside a sane
    band on a real deployment, OR
  - Concrete underperformance on MemoryAgentBench / LoCoMo traceable to
    one of the static thresholds.
- The smallest correct fix when triggered will almost certainly be
  promoting the misbehaving constant to a `config.yaml` override — not
  adaptive tuning. One knob change with one piece of evidence.

## References

- [Issue #73](https://github.com/emson/elfmem/issues/73)
- [`docs/plans/issue_self_tune_research.md`](../plans/issue_self_tune_research.md) — full design exploration (5 architectures, 4 scenarios)
- [ADR 0003](0003-defer-constitutional-evolution.md) — related deferral with the same underlying reasoning
- `src/elfmem/policy.py` — current one-knob baseline (`ConsolidationPolicy`)
- `src/elfmem/operations/consolidate.py:49-54` — the four static thresholds in question
