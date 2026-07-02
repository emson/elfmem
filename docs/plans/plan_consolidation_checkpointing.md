# Bound and Checkpoint Consolidation for Slow LLM Adapters

**Status:** IN PROGRESS — Changes 1, 3, 4 implemented on branch
`consolidation-checkpointing` (2026-07-01); Change 2 (per-block commit inside
`consolidate()` itself) remains as a follow-up per the risk ordering below.
**Date:** 2026-07-01
**Driver:** `elfmem dream` killed three times against a local LM Studio adapter
(~14s/call), zero output each time. See [ADR 0007](../decisions/0007-bound-and-checkpoint-consolidation.md)
for the full context and rejected alternatives.

## Executive summary

`consolidate()`/`dream()` has two structural properties that make it scale
badly against a slow LLM adapter or a large active-block corpus, independent
of any external process-kill limit:

1. Per-block contradiction cost is O(n_active) in the worst case — the
   existing cosine prefilter (0.40) cuts ~95% of pairs by precision but has
   no cap on cardinality.
2. The entire batch (every block's reads, LLM calls, and writes) is one
   `async with self._engine.begin()` transaction — nothing is durable until
   the whole call returns, so a kill anywhere loses everything processed so
   far, in both `consolidate()` and `rescore()`.

This plan makes three changes: a hard per-block cap on contradiction checks
(top-K), per-block incremental commits in both `consolidate()` and
`rescore_blocks()`, and a self-terminating per-run budget on
`consolidate()`/`dream()` mirroring the existing `--rescore --max`. No
concurrency changes, no daemon/background service, no adapter
local-vs-cloud branching — see ADR 0007 for why those were rejected.

## Scope

Ships as a single minor version bump (next available, e.g. v0.20.0 — confirm
against `pyproject.toml` at release time). All three changes are additive:
new config fields with defaults that preserve current behaviour for
small/typical corpora, and an additive `ConsolidateResult` field.

## Changes, ordered by risk

### Change 1: Top-K cap on contradiction checks per block — SHIPPED

**Effort:** ~1 hr | **Risk:** low (bounded, config-gated)

**Problem:** `src/elfmem/operations/consolidate.py:439-451` loops over every
active block in `evolving_vecs`, checking the cached cosine similarity
against `contradiction_similarity_prefilter` (default 0.40) before issuing a
`detect_contradiction` LLM call. Every block that clears the threshold gets
checked — no limit on how many that can be.

**Fix:**
- Add `contradiction_top_k: int = 10` to `MemoryConfig` in `config.py` (near
  `contradiction_similarity_prefilter`, line ~61). (Default resolved — see
  "Decisions" above; no further data pull needed to land this change.)
- In `_collect_decisions()`, before the contradiction loop at line 439:
  compute all `(a_block, sim)` pairs clearing the prefilter first (reusing
  `sim_cache`), sort by `sim` descending, take the top
  `contradiction_top_k`, and only issue LLM calls for those. Skip the rest
  without incrementing `pairs_above_prefilter` beyond what's actually
  checked — `ConsolidationHealthMetrics.prefilter_pass_rate` should reflect
  *checked* pairs, not just *eligible* pairs, or add a new counter
  (`pairs_capped`) so the existing health metric doesn't silently change
  meaning. Decide which during implementation and note it in the docstring.
- Default value: pick from real `prefilter_pass_rate` data if available
  (`elfmem status` / health metrics on this project's own DB) rather than
  guessing; fall back to 10 if no data exists, documented as "generous for
  typical corpora, revisit via health metrics if it bites."

**Test:** unit test with a synthetic active set where >K blocks clear the
prefilter — assert exactly K `detect_contradiction` calls are made (via
`MockLLMService` call count), not `len(passing)`.

### Change 2: Per-block incremental commit in `consolidate()` — FOLLOW-UP (not in this branch)

**Effort:** ~2-3 hrs | **Risk:** medium (touches the core write path)

**Problem:** `_collect_decisions()` (read + LLM, no writes) and
`_apply_decisions()` (all writes) are two phases of one call, both inside
the single transaction opened by `api.py:791`. A kill during
`_collect_decisions()` loses every already-scored block; a kill during
`_apply_decisions()` loses the whole write batch.

**Fix:**
- Restructure `consolidate()`'s inbox loop so each block's decision
  (produced by the per-block body currently inside `_collect_decisions()`,
  `consolidate.py:338-478`) is applied and committed before moving to the
  next block, rather than accumulating `block_decisions`/
  `contradiction_decisions`/`edge_decisions` for the whole inbox and writing
  them all at the end (`_apply_decisions()`, `consolidate.py:663-669`).
- Edge decisions currently depend on the full `newly_promoted` +
  `evolving_vecs` state computed after the loop (`_compute_edge_decisions()`,
  called at line 480 with the *final* evolving set) — confirm during
  implementation whether edge scoring can be computed per-block against the
  evolving set *so far* (consistent with "later inbox blocks see earlier
  decisions" behaviour already documented at `consolidate.py:332`) without
  changing edge-creation semantics. If not, edges may need a second,
  smaller finalization pass — keep this pass reasonably bounded (it's O(new
  blocks), not O(active blocks), so it's not the source of the original
  problem).
- Each per-block commit uses its own `async with self._engine.begin()` (or
  equivalent), replacing the single outer transaction in `api.py:791`.
- `set_config(conn, "last_consolidated_at", ...)` (`api.py:807`) should move
  to after the loop completes (or after each block — either is fine, this
  is a cheap idempotent write).

**Test:** simulate a kill mid-batch (process N blocks, raise/interrupt
before block N+1, e.g. via a `MockLLMService` that raises after N calls in a
test double) and assert the first N blocks are active in the DB and the
inbox correctly still contains the rest, ready for the next `dream()` call.

### Change 3: Per-block incremental commit in `rescore_blocks()` — SHIPPED

**Effort:** ~1 hr | **Risk:** low (simpler loop, no edge/contradiction
interaction)

**Problem:** Same class of bug as Change 2, but simpler: `rescore()`
(`api.py:1011-1019`) wraps `select_rescore_candidates()` +
`rescore_blocks()` in one transaction; `rescore_blocks()`
(`rescore.py:232-273`) already skips failed blocks gracefully but all
successful `update_block_scoring()` writes share the outer transaction.

**Fix:** Move the per-block write (`update_block_scoring()`,
`rescore.py:262-269`) into its own committed transaction per iteration,
independent of the candidate-selection transaction. `select_rescore_candidates()`
can remain a single read-only call.

**Test:** same shape as Change 2's kill-mid-batch test, scaled down —
assert blocks rescored before an injected failure have `last_scored_at`
updated and are excluded from the next `select_rescore_candidates()` call.

### Change 4: Self-terminating budget on `dream()`/`consolidate()` — SHIPPED

**Effort:** ~1-2 hrs | **Risk:** low (additive, opt-in via config/flag)

**Problem:** `consolidate()` always processes the entire inbox in one call;
no way to bound how much work a single invocation takes on, unlike
`rescore()`'s existing `max_per_run`/`--max`.

**Fix:**
- Add `max_inbox_per_run: int | None = 5` to `MemoryConfig` in `config.py`
  (near `inbox_threshold`). `None` still means unbounded (e.g. for
  programmatic callers that explicitly opt back out), but the shipped
  default is 5 — see ADR 0007 for the arithmetic grounding this default in
  the reported incident's own numbers.
- In `consolidate()`/`_collect_decisions()`, after reading the inbox
  (`consolidate.py:284`), slice to at most `max_inbox_per_run` blocks
  (oldest first, matching existing inbox FIFO semantics) when the config
  value is not `None`.
- `ConsolidateResult` gains an additive field, `inbox_remaining: int`,
  populated from `len(inbox) - processed` before slicing. `ConsolidateResult.__str__`/
  `summary` mentions remaining count when nonzero, e.g. "... 4 remaining —
  run dream() again to continue."
- CLI: `dream --max N` (already exists at `cli.py:1068-1078` for the rescore
  budget) now also bounds inbox processing when inbox work happens in the
  same invocation. One flag, applied independently to each stage that
  actually runs in that call — see ADR 0007's resolution. Update the flag's
  help text accordingly; no new flag name.

**Test:** inbox of 10 blocks, `max_inbox_per_run=3` — assert exactly 3
processed, `inbox_remaining == 7`, and a second `dream()` call drains the
next 3.

## Non-goals (per ADR 0007)

- No concurrent/parallel LLM calls.
- No background daemon / trickle-consolidation service.
- No adapter local-vs-cloud detection or branching logic.
- No change to per-call timeouts (`LLMConfig.timeout`) — document raising it
  for slow local adapters instead.

## Migration

All changes are additive (new config fields default to today's behaviour;
new result field defaults to 0/absent). No schema migration. Existing
callers of `consolidate()`/`dream()`/`rescore()` continue to work unchanged.
`learn_document()` (`api.py:932-948`) already loops on `should_dream`, so a
budget-limited `dream()` call that doesn't fully drain the inbox is
naturally compatible — verify this explicitly in a test once Change 4 lands.

## Risks

- Change 2 is the riskiest: it touches the core read/LLM/write structure of
  `consolidate()` and needs care around edge-decision computation ordering
  (see note above). Land Changes 1, 3, and 4 first (independent, lower
  risk) and Change 2 last, once the others are validated.
- Picking `contradiction_top_k`'s default without real health-metric data
  risks either a no-op default (too high) or unexpectedly skipped
  contradiction checks on dense corpora (too low). Pull `prefilter_pass_rate`
  from this project's own `elfmem status`/health metrics before finalizing
  the default.

## Decisions (resolved 2026-07-01)

1. **`--max` naming**: reuse the existing flag (no `--inbox-max`). When both
   inbox processing and `--rescore` run in the same invocation, `--max`
   applies the same numeric cap to each stage independently. See ADR 0007.
2. **Defaults**: `contradiction_top_k=10` (provisional, ADR-0006-style
   reopen trigger via health metrics); `max_inbox_per_run=5` (grounded in
   the reported incident's own numbers — see ADR 0007 — *not* unbounded,
   because Change 2/3's durability fix for `consolidate()` itself is
   deferred to a follow-up branch, so the budget is today's only default-on
   protection against the original failure).
3. **Implementation order approved**: Changes 1, 3, 4 ship together on
   branch `consolidation-checkpointing`. Change 2 (per-block commit inside
   `consolidate()` itself — the highest-risk change, touching edge-decision
   ordering) ships on a follow-up branch once (1)/(3)/(4) are validated.

Remaining open item, to confirm once Change 2 is actually implemented:
whether `_compute_edge_decisions()` can be called incrementally per block
(re-querying the committed active set so far) without changing the final
edge graph versus today's end-of-batch computation, or needs a small
finalization pass. Analysis in ADR 0007 suggests per-block recomputation
against the live active set produces an equivalent graph as long as edge
creation is symmetric — needs verification against `edge_degree_cap`
ordering effects when actually implemented.
