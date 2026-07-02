# 0007 — Bound and checkpoint consolidation for slow LLM adapters

**Status**: Accepted
**Date**: 2026-07-01
**Deciders**: Ben Emson, elf

## Context

elfmem's default configuration routes LLM scoring through a local
OpenAI-compatible server (LM Studio, `google/gemma-4-26b-a4b`) rather than a
cloud API. In practice, `elfmem dream` was killed three times in a row
against a 211-active-block corpus, producing zero output each time, before
the operator worked around it with `dream --no-llm` (embed-only) and
deferred LLM scoring to `--rescore`.

Two structural properties of the pipeline explain this, independent of any
specific external kill limit:

1. **Unbounded per-block contradiction cost.** `consolidate()` issues one
   sequential `process_block` LLM call per inbox block, then for each inbox
   block loops over every active block whose cached cosine similarity
   clears the `contradiction_similarity_prefilter` (default 0.40,
   `consolidate.py:442`) and issues a sequential `detect_contradiction`
   call. The prefilter cuts ~95% of pairs by *precision*, but there is no
   cap on *cardinality* — a block with many similar neighbours still costs
   O(n_active) LLM calls. At ~14s/call locally, 211 actives, this alone can
   run into the tens of minutes; at thousands of actives it is unbounded.

2. **One all-or-nothing transaction.** `consolidate()` (`api.py:791`) and
   `rescore()` (`api.py:1011`) each wrap every read, LLM call, and write for
   the entire batch in a single `async with self._engine.begin()`. Nothing
   is durably committed until the whole call returns. A kill at any point —
   background task-runner limit, OOM, Ctrl-C — loses every block processed
   so far, not just the one in flight. (Verified directly in
   `rescore_blocks()`, `rescore.py:232-273`: per-block LLM failures are
   skipped gracefully, but all successful per-block writes still share the
   caller's one outer transaction — there is no per-block *durability*,
   only per-block *error isolation*.)

3. **No self-imposed run budget.** `rescore()` already has
   `consolidation.rescore.max_per_run` (default 20) bounding how much work
   one call takes on. `consolidate()`/`dream()` has no equivalent — one call
   always processes the entire inbox, however large, with no way to stop
   itself before an external limit does.

Both per-call timeout sites (`consolidate.py:391-393`, `445-451`) already
catch `TimeoutError` gracefully and fall back rather than raise, so the
failure mode is confirmed to be **cumulative duration**, not an unhandled
exception propagating out of the transaction.

## Alternatives considered

**A — Concurrent LLM calls** (`asyncio.gather` + semaphore across blocks or
contradiction checks). *Disqualified as the primary fix*: the default
adapter is a single local model server serving one GPU; concurrent client
requests mostly queue server-side rather than execute in parallel, so
wall-clock barely improves against the actual reported failure, while adding
concurrency risk (SQLite write-lock contention) for no compensating gain.
Left as a possible future opt-in for adapters known to support real
server-side concurrency (cloud APIs) — not the fix for this issue.

**B — Background daemon / trickle consolidation** (`elfmem serve`
consolidates continuously in small increments instead of one-shot CLI
invocations). Would sidestep the CLI-vs-external-kill-limit problem
entirely. *Disqualified for now*: materially larger scope (process
lifecycle, idle cost, coordination with concurrent foreground CLI use) than
the problem warrants. Recorded as a future ROADMAP "Exploring" item, not
built here.

**C — Raise the cosine prefilter threshold** (e.g. 0.40 → 0.60) instead of
adding a hard cap. *Disqualified as the sole fix*: a threshold bounds match
*precision*, not *cardinality* — a sufficiently large or dense active set
can still pass hundreds of candidates above any fixed threshold. Only a hard
per-block cap bounds worst-case cost independent of corpus size. (Threshold
and cap are complementary, not substitutes — the decision keeps both.)

**D — Document "use `--no-llm` + `--rescore` for local models"; no code
change.** *Disqualified*: `rescore()` does not do contradiction detection
(only `process_block`), so deferring via `--rescore` means those blocks
never get contradiction-checked, silently trading a performance problem for
a quality gap. The workaround also does not resolve `--rescore` itself
running into the same unbounded-duration transaction risk (point 2 above)
once deferred debt is large.

## Decision

1. **Cap contradiction checks per inbox block to the top-K most similar
   active blocks** that clear the existing cosine prefilter
   (`memory.contradiction_top_k`), replacing "all blocks above threshold"
   with "at most K blocks above threshold." Bounds worst-case per-block LLM
   cost to O(K), independent of active-set size. **Default: 10** —
   provisional, chosen the same way ADR 0006 chose its static thresholds
   (round-number default, not fitted to data we don't have), with the same
   kind of reopen trigger: revisit via the new
   `ConsolidationHealthMetrics.contradiction_cap_rate` if it's regularly
   nonzero on a real deployment, at which point the correct move is raising
   the config value, not removing the cap. Tracked in
   [issue #79](https://github.com/emson/elfmem/issues/79) — ADR 0006's
   pattern of tying "provisional" defaults to a tracked issue + observable
   trigger, not a comment that never gets revisited.

2. **Commit consolidation decisions incrementally, per inbox block**,
   instead of one all-or-nothing transaction — apply and commit each
   block's decisions (promotion, dedup, contradictions, edges) before
   moving to the next block. Apply the identical pattern to
   `rescore_blocks()`. A kill at any point preserves every fully-processed
   block; only the one block in flight is lost and is naturally retried by
   the next `dream()`/`--rescore` call (both already have idempotent,
   priority-ordered selection).

3. **Add a self-terminating per-run budget to `dream()`/`consolidate()`**
   (count-based, new `consolidation.max_inbox_per_run` config field, default
   **5**). A run stops itself and returns a typed result reporting blocks
   processed vs. remaining, rather than depending on an external
   process-lifetime limit to end it. No such field exists today — verified
   against `MemoryConfig`/`RescoreConfig` in `config.py`.

   Unlike `contradiction_top_k`, this default is **not** unbounded-by-default
   despite the general preference for zero-behaviour-change defaults
   (see "minimum-earned change" precedent): the per-block *durability* fix
   (item 2) is the one that makes an interrupted run harmless, and it is the
   riskier of the two changes in this ADR — see the companion plan's staged
   rollout (durability lands on a follow-up branch after the lower-risk cap
   and budget). Until durability ships, a default of `None` would leave
   `elfmem dream`'s plain, zero-flag invocation exactly as exposed to the
   original failure as before, which fails this project's own "Tier 1
   (zero config, zero ceremony) must always work" contract. Default 5 is
   grounded in the numbers from the reported incident, not guessed: at 5
   inbox blocks/run × (1 process_block call + up to `contradiction_top_k`=10
   contradiction calls) = at most 55 LLM calls/run; at the ~14s/call
   observed against the local adapter that triggered this issue, that is
   ≤~13 minutes worst case, and typically much less. It is also half of
   `inbox_threshold` (10, the existing auto-trigger point for `should_dream`),
   so a normal-sized batch still drains in one call under typical operation.
   Revisit this default once (2) ships, at which point unbounded may become
   the better default since a kill would no longer be catastrophic.

   **`--max` reuses the existing `dream --max` flag** (previously only
   meaningful with `--rescore`) rather than introducing a second flag name.
   When both a pending inbox and `--rescore` are processed in the same
   invocation, `--max` applies the same numeric cap to both stages
   independently (inbox processing and rescore catch-up each stop at that
   count). This keeps the CLI surface unchanged at the cost of not letting
   one invocation set two different budgets — acceptable because the two
   stages are already separately invocable (`elfmem dream --max N` vs.
   `elfmem dream --rescore --max N` vs. running both flags in sequence with
   different values across two calls).

4. **No change to per-call LLM timeouts or adapter selection logic.**
   `LLMConfig.timeout` is already overridable per deployment; document the
   recommendation to raise it for slow local adapters rather than adding
   local/cloud branching in code.

Explicitly **not** doing, recorded with trigger conditions to revisit:
concurrent LLM calls (A) — revisit if a cloud-only deployment is
demonstrably bottlenecked on sequential LLM round-trips with health metrics
to prove it; background daemon mode (B) — revisit if one-shot CLI
consolidation recurs as a pain point after (1)-(3) ship.

## Consequences

- `consolidate()`/`rescore()` commit incrementally; a partial run always
  leaves the DB in a consistent, resumable state — consistent with the
  existing idempotent-operation contract (`should_dream`, duplicate
  `learn()` reject).
- `ConsolidateResult` gains an additive field reporting blocks remaining in
  the inbox after a budget-limited run (non-breaking; existing consumers
  unaffected).
- Two new config fields: `consolidation.max_inbox_per_run`,
  `consolidation.contradiction_top_k`, both with defaults chosen not to
  change behaviour for small/typical corpora.
- A dense corpus with many blocks above the similarity prefilter for a given
  new block may now skip contradiction-checking some pairs beyond the top-K
  cap. Mitigated by keeping the cap generous relative to observed
  `prefilter_pass_rate`; if this proves to matter in practice, the smallest
  correct fix is raising K via config, not re-introducing the unbounded
  scan.
- Precludes treating "the whole inbox in one call" as a correctness
  invariant anywhere downstream — any code relying on `consolidate()`
  fully draining the inbox in a single call must instead check the returned
  remaining count and loop if it needs full drainage (e.g. `learn_document`
  at `api.py:944-948` already loops on `should_dream`, so this is naturally
  compatible; verify at implementation time).

## References

- `src/elfmem/operations/consolidate.py:338-478` — per-block scoring and
  contradiction loop
- `src/elfmem/operations/rescore.py:232-273` — per-block rescore loop,
  shares the caller's outer transaction
- `src/elfmem/api.py:759-836` (`consolidate()`), `:960-1019` (`rescore()`),
  `:1021-` (`dream()`)
- `src/elfmem/config.py` — `MemoryConfig` (contradiction thresholds),
  `RescoreConfig` (existing `max_per_run` precedent)
- [ADR 0006](0006-defer-multi-parameter-self-tuning.md) — related but
  orthogonal: that ADR is about *scoring parameter* tuning
  (thresholds/decay), this one is about *runtime budgeting and durability*
  of the consolidation pipeline itself
- `docs/plans/plan_elfmem_optimise.md` — prior performance work (embedding
  batching); does not touch contradiction-loop scaling or transaction
  granularity
- `docs/plans/plan_dreaming_architecture.md`,
  `docs/plans/plan_contradiction_detection_band.md` — introduced the
  existing cosine prefilter and near-dup/contradiction ordering; neither
  proposes a hard cap, per-block commit, or a consolidate-level run budget
