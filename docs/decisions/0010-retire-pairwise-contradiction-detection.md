# 0010 — Retire pairwise LLM contradiction detection at consolidate-time

**Status**: Accepted
**Date**: 2026-08-08
**Deciders**: elf (curator), Ben

## Context

`consolidate()` ran an LLM-based pairwise contradiction-detection loop for
every inbox block: candidates clearing a cosine-similarity prefilter were
capped to the `contradiction_top_k` most similar (ADR 0007) and each sent to
`LLMService.detect_contradiction()`; scores above `contradiction_threshold`
were written to the `contradictions` table. `docs/plans/plan_v2_substrate_reevaluation.md`
bundled retiring this together with decay-driven archival (see
[ADR 0009](0009-retire-decay-driven-archival.md)) under "step 7," but
grounding showed the two have different structure and different
replacement-readiness, so they were split.

**Cost.** By ADR 0007's own accounting, contradiction checking is up to 10
LLM calls per inbox block against 1 `process_block` call — the dominant
worst-case LLM cost of `consolidate()` by construction.

**Realized value.** The self-hosted elfmem database
(`~/.elfmem/databases/elfmem.db`) had 14 contradictions ever recorded, 12
(86%) still unresolved. The human review loop for these findings is barely
used.

**External corroboration.** `benchmarks/memoryagentbench/`'s Conflict
Resolution competency was purpose-built to test this exact mechanism — its
own code comments called it "elfmem's primary moat," and ran full pairwise
detection (`skip_llm=False`) specifically for CR examples. The most recent
recorded run (`benchmarks/memoryagentbench/results/20260410T151306Z_mabench_elfmem.json`)
scored Conflict_Resolution at **4.8%** with detection fully enabled — this
corroborates rather than contradicts the production-inertness finding.

**The read side is not inert.** Grounding `operations/recall.py` and
`context/contradiction.py::suppress_contradictions()` established that
contradiction records ARE actively consulted: step 6 of the 10-step recall
pipeline, run on **every** `frame()`/`recall()` call, silently dropping the
lower-confidence half of any contradicting pair present in the candidate
set. This is a live, load-bearing filter, not a passive audit log — it must
not be retired alongside the write side.

## Alternatives considered

1. **Retire detection and suppression together.** Rejected: suppression is
   free (no LLM call), still consults existing records correctly, and
   nothing in the evidence suggests it's a problem. Retiring it would be an
   unrelated, unjustified regression.
2. **Wait for step 6b** (corpus-level LLM contradiction/duplicate review,
   human-gated, not yet designed) **before retiring the pairwise loop**, to
   avoid any coverage gap for new content. Rejected: the pairwise loop's
   cost is being paid today for near-zero realized value today; deferring
   the fix until an unscheduled future step ships has no offsetting benefit.
   The coverage gap this decision creates is disclosed below, not silent.
3. **Retire detection only; leave suppression, the DB layer, and config
   surface for a future write path untouched.** Chosen.

## Decision

Remove from `operations/consolidate.py`: the pairwise `detect_contradiction`
LLM loop, `_ContradictionDecision`, `_contradiction_features()`,
`CONTRADICTION_THRESHOLD` / `CONTRADICTION_SIMILARITY_PREFILTER` /
`CONTRADICTION_TOP_K` module constants, and the `contradiction_threshold` /
`contradiction_similarity_prefilter` / `contradiction_top_k` /
`skip_contradictions` parameters from `_collect_decisions()` and
`consolidate()`.

Remove the now-fully-dead downstream surface: `MemoryConfig.contradiction_threshold`
/ `.contradiction_similarity_prefilter` / `.contradiction_top_k`,
`LLMConfig.contradiction_model`, `PromptsConfig.contradiction` /
`.contradiction_file` / `.resolve_contradiction()`, `prompts.CONTRADICTION_PROMPT`,
the `LLMService.detect_contradiction()` port method and all three adapter
implementations (Anthropic, OpenAI, Mock), `adapters.models.ContradictionScore`,
`dream()`'s `skip_contradictions` parameter, the CLI `--skip-contradictions`
flag, and the MCP `elfmem_dream` tool's `skip_contradictions` parameter.

Remove the now-permanently-empty result surface: `ConsolidateResult.contradictions_detected`
/ `.contradictions`, the `ContradictionFinding` type, and
`ConsolidationHealthMetrics.contradiction_detection_rate` /
`.prefilter_pass_rate` / `.contradiction_cap_rate`.

**Keep, fully untouched**: the `contradictions` DB table; `insert_contradiction`
/ `get_contradictions_for_blocks` / `resolve_contradiction` query functions;
`context/contradiction.py::suppress_contradictions()` and its call site in
`operations/recall.py`. These are exactly what step 6b will write to.

## Consequences

- Breaking API changes: `ConsolidateResult.contradictions_detected` /
  `.contradictions`, `ConsolidationHealthMetrics.contradiction_detection_rate`
  / `.prefilter_pass_rate` / `.contradiction_cap_rate`, the `ContradictionFinding`
  type (removed entirely), `dream()`/`consolidate()`'s `skip_contradictions`
  keyword, the `--skip-contradictions` CLI flag, and the
  `LLMService.detect_contradiction()` port method. Migration: none needed for
  typical callers — these were additive fields/flags; custom `LLMService`
  adapters no longer need to implement `detect_contradiction`.
- **New content stops getting automatic contradiction detection** until step
  6b ships. Existing contradiction records (14, as of this writing) continue
  to suppress at recall time; they are removed only when their blocks are
  archived (the existing cascading-delete behavior in `update_block_status`
  is unchanged).
- ADR 0007's Change 1 (`contradiction_top_k` cap) is now moot — there is no
  contradiction loop left to bound. ADR 0007's Changes 2 and 3 (per-block
  commit durability, `max_inbox_per_run` budget) are unaffected and remain
  in force; `tests/test_consolidation_checkpointing.py` was trimmed to drop
  only the Change-1 test class.
- Two benchmark harnesses (`benchmarks/memoryagentbench/`, `benchmarks/locomo/`)
  had their own `contradiction_similarity_prefilter` config knobs forwarding
  into `ElfmemConfig`; both updated to stop referencing the removed field.
  MemoryAgentBench's Conflict Resolution competency still runs full LLM
  scoring (`skip_llm=False`) for alignment/tag/summary retrieval quality, but
  no longer exercises automatic contradiction detection specifically —
  documented inline in `benchmarks/memoryagentbench/adapter.py`.
- Closed a pre-existing test gap discovered while making this change:
  `suppress_contradictions()` — the mechanism this ADR's whole "keep the read
  side" argument rests on — had zero direct unit tests; it was only
  exercised indirectly through two assertion-free placeholder tests that
  depended on the now-removed detection loop to populate fixture data.
  Replaced both with `tests/test_contradiction_suppression.py`, which tests
  `suppress_contradictions()` directly against the DB layer.

## Trigger to revisit

Step 6b (corpus-level LLM contradiction/duplicate review, human-gated)
landing. At that point this ADR's "coverage gap for new content" consequence
is closed by a cheaper mechanism — batch review instead of per-block O(n)
LLM calls during every `consolidate()`.

## References

- [ADR 0007](0007-bound-and-checkpoint-consolidation.md) — introduced
  `contradiction_top_k`, whose Change 1 is now moot
- [ADR 0009](0009-retire-decay-driven-archival.md) — companion decision,
  split from the same "step 7" plan-doc entry
- `docs/plans/plan_v2_substrate_reevaluation.md`
- `benchmarks/memoryagentbench/results/20260410T151306Z_mabench_elfmem.json`
  — Conflict_Resolution scored 4.8% with detection fully enabled
- `src/elfmem/context/contradiction.py::suppress_contradictions` — the
  untouched read-side mechanism
- `src/elfmem/operations/corpus_review.py` — step 6a, a sibling mechanism
  (staleness, not contradiction — a different concept)
