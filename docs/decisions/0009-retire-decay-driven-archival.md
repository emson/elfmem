# 0009 — Retire decay-driven block archival trigger

**Status**: Accepted
**Date**: 2026-08-08
**Deciders**: elf (curator), Ben

## Context

`curate()` has always run `_archive_decayed_blocks()`: it computes each active
block's `recency = exp(-decay_lambda * hours_since_reinforced)` from its
`DecayTier`, and archives (reason `"decayed"`) any block whose recency falls
below `PRUNE_THRESHOLD` (0.05), except high-weighted-degree "bridge" nodes.
`docs/plans/plan_v2_substrate_reevaluation.md` bundled this together with
pairwise contradiction detection under "step 7: retire pairwise contradiction
+ decay" as expensive, low-value machinery.

Grounding against the real self-hosted elfmem database
(`~/.elfmem/databases/elfmem.db`, months of active use) found:

```
archive_reason   count
──────────────────────
superseded          41
decayed               0
```

The archival trigger has never fired in production. Meanwhile step 6a
(`review_corpus()` / `elfmem review corpus`, shipped ahead of this ADR)
already covers the same underlying need — surfacing blocks that are
long-unused, rarely reinforced, and never confirmed by an outcome — as a
deterministic, zero-LLM-cost, human-gated proposal instead of a silent
automatic archive.

Further grounding found `decay_lambda` / `DecayTier` / `compute_recency` are
**not** solely used for archival. They are also live inputs to:

- `memory/retrieval.py::_stage_4_composite_score` — `recency` is a scoring
  term on **every** `frame()`/`recall()` call.
- `curate.py::_prune_decayed_edges` — a separate edge-decay mechanism via
  `compute_lambda_edge`, unrelated to block archival.
- `curate.py::_reinforce_top_blocks` — the same recency term feeds curate's
  own top-N reinforcement composite score.
- `operations/mind.py` — sets `decay_lambda` on Theory-of-Mind blocks.

ADR 0001 established the project's precedent for scoring-mechanism changes:
don't touch what you haven't measured. The archival trigger has direct
evidence of inertness; the tier/lambda system as a *retrieval-ranking*
signal does not — deleting the whole system would violate that same
precedent for the parts nobody measured.

## Alternatives considered

1. **Retire the whole `DecayTier`/`decay_lambda` system**, matching the plan
   doc's original "retire decay" framing. Rejected: would silently degrade
   live retrieval ranking with zero evidence gathered, and would also break
   edge-decay pruning and curate's own reinforcement scoring — neither of
   which showed any sign of being inert.
2. **Keep `_archive_decayed_blocks()` but tune thresholds** (raise
   `prune_threshold`, widen bridge protection). Rejected: doesn't address the
   actual finding — the mechanism doesn't archive too aggressively, it
   doesn't archive at all — and 6a already ships a demonstrably-usable,
   human-gated replacement for the same underlying need.
3. **Retire only the archival trigger, leave decay-tier/lambda machinery
   fully intact everywhere else.** Chosen.

## Decision

Remove `_archive_decayed_blocks()`, `PRUNE_THRESHOLD`, and
`BRIDGE_PROTECTION_QUANTILE` from `operations/curate.py`. Remove
`MemoryConfig.prune_threshold`. `curate()` is now two phases (edge
pruning/decay, top-N reinforcement) instead of three.

`DecayTier`, `decay_lambda_for_tier()`, and `compute_recency()` are
**untouched** — they remain load-bearing for live retrieval scoring, edge
decay pruning, and curate's own reinforcement scoring.

## Consequences

- `CurateResult.archived` field removed (a permanently-0 field is worse than
  no field). Breaking change for any caller reading `.archived` /
  `to_dict()["archived"]`. Migration: use `review_corpus()` /
  `elfmem review corpus` for staleness detection, and
  `forget(reason=ArchiveReason.DECAYED)` to act on accepted proposals.
- `curate()`'s docstring / CLI help / MCP tool description updated to "prune
  weak/decayed edges, reinforce top knowledge" — archival dropped from the
  description.
- `MemoryConfig.prune_threshold` removed. A deployment YAML that still sets
  `memory.prune_threshold` has the key silently ignored (Pydantic's default
  `extra="ignore"`), not an error.
- `src/elfmem/viz/data.py` still renders a `prune_threshold` / "archive
  cliff" line on its decay-curve dashboard chart via its own independent
  local constant (`_PRUNE_THRESHOLD`) — now describing a threshold `curate()`
  no longer enforces. Left untouched; out of scope for this step, flagged as
  a residual follow-up for whoever next touches `viz/`.

## Trigger to revisit

If a future need for automatic (non-human-gated) archival re-emerges with
real evidence that `review_corpus()` + human review is insufficient.

## References

- Original framing: [`docs/plans/plan_v2_substrate_reevaluation.md`](../plans/plan_v2_substrate_reevaluation.md)
- [ADR 0001](0001-power-law-decay-rejected.md) — precedent for evidence-gated
  changes to scoring/decay mechanisms
- `src/elfmem/operations/corpus_review.py` — step 6a, the shipped replacement
- `src/elfmem/memory/retrieval.py::_stage_4_composite_score` — the live
  retrieval-scoring consumer of `decay_lambda` that was **not** touched
- Companion decision: [ADR 0010](0010-retire-pairwise-contradiction-detection.md)
