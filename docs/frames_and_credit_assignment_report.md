# Frames and Credit Assignment — what broke after seeding a constitution

Follow-up to `integration_friction_report.md`, from the same trading-agent integration
(elfmem `0.20.0.dev0`, `elfmem_index` @ 8a38bba3). That report covered getting blocks *in*.
This one covers what happened the day after: **seeding ten constitutional principles silently
broke the agent's ability to recall anything it had learned, and wired its trade P&L directly
into its identity.**

> **Context:** the first report has since been actioned — P1/P2/P3/P5/P6/P8/P9 shipped, and
> P11's premise was correctly rebutted (consolidation *unions* tags, so a declared
> `self/role/x` does survive; the `frames.py` comment I reasoned from was wrong). Checked
> against `elfmem_index` @ 8a38bba3 as of writing: those changes are not on that branch yet, so
> the issues below are diagnosed on the pre-fix build. **Neither issue here is addressed by the
> shipped work** — `ATTENTION_FRAME.filters` still has no exclusion, and `OutcomeResult` still
> has no skipped/refused field. Both are new territory, opened up *by* successfully seeding a
> constitution, which is only possible now the first report's fixes exist.

**Short answer to "is it working as expected?"** Storage, decay tiers, the SELF frame and the
amendment API all work as documented. Two things do not, and both are architectural rather than
bugs: **frames have no exclusion mechanism**, and **`outcome()` has no notion of which blocks
should be immune to task outcomes.** I worked around both in my adapter, but every agent that
seeds a constitution will hit them, and the workaround required bypassing `frame()` entirely.

> **Status (2026-08-27): asks 1, 2 and 4 shipped; 5 shipped previously. Kept as the incident
> record.** Both issues were reproduced on the post-fix branch before anything was changed, and
> two of the three stated *mechanisms* turned out to be wrong — worth recording, because the
> corrected mechanisms changed the fix.
>
> | # | Ask | Status |
> |---|---|---|
> | 1 | `outcome()` skips `self/constitutional` | **shipped** with `skipped_constitutional` + `allow_constitutional=True`. Mechanism corrected: decay was *already* protected (`accelerate_block_decay` skips PERMANENT), so "the damage never washes out" via decay is not what happens. The harm is entirely through the Beta posterior — measured at 0.50 → 0.275 after one losing trade, 0.114 after six — which feeds ranking, which decides what survives a budget-bound SELF frame. Worse than described for mature instances, not better |
> | 2 | `FrameFilters.exclude_tag_patterns`, applied to ATTENTION | **shipped**, with a `peer/%` exemption the report did not propose. Measured on elf's own corpus: a bare exclusion removed Alv's letters, which are genuine knowledge that accreted the tag from the consolidating LLM. Result: 36% → 24% constitutional share, double-served blocks 3/25 → **0/25** |
> | 3 | Cross-frame dedupe in multi-frame assembly | **not built.** Option (a) turned out to subsume it: with ATTENTION excluding what SELF serves, double-serving went to zero without introducing a multi-frame assembly API or hidden cross-call state |
> | 4 | `mind_outcome(..., weight=)` / `provisional=` | **documentation instead.** Premise corrected: `mind_outcome` is **not terminal** — re-resolving the same decision block reverses cleanly (early miss → later hit restored confidence 0.43 → 0.50 and the count to 1/1). The signal was lost because nothing said so, so `guide("mind_outcome")` now states both that `hit` is binary with no weight and that it is re-resolvable |
> | 5 | Every reducing operation reports what it reduced | **shipped** in the previous round — and the third silent reducer (contradiction suppression) was found by running the tool that round produced |
>
> The report's closing principle — *any operation that can silently reduce, reword, or degrade
> what the caller intended should say so in its result* — is now recorded in
> `docs/agent_friendly_principles.md` as an earned principle, with these two issues as its
> evidence.

---

## Issue 1 — constitutional blocks starve the ATTENTION frame

### What happened

Day 1: seeded 10 principles tagged `self/constitutional`. Verified they render in SELF. Good.

Day 2: checked what ATTENTION returned for a real decide query
(`"SPY bull put spread, is the thesis intact"`):

```
score=0.958  principle/recency          Recent direction is evidence about the recent past…
score=0.954  principle/premise          When live data contradicts a thesis's premise…
score=0.909  bull_put_spread            The agent holds a short 755 put on SPY…   <- the only real hit
score=0.827  principle/contradictions   When evidence conflicts with something I hold…
score=0.815  principle/precision        Context serves the decision at hand…
score=0.801  principle/regimes          A pattern learned in one regime is a hypothesis…
…10 of 12 hits were principles…
score=0.343  mind/spy-options-trading   # Mind Model: SPY options trading
```

After de-duplicating against what SELF had already rendered, **ATTENTION returned nothing at
all.** The agent's entire learned market knowledge had been displaced by its own identity, one
day after seeding it — and nothing anywhere reported this.

### Why it happens, structurally

Three properties compound:

1. Constitutional principles are written in general epistemic language ("evidence", "confidence",
   "premise", "pattern"), so they are **semantically close to almost any reasoning query**.
2. They carry **PERMANENT decay**, so recency scoring never demotes them.
3. `ATTENTION_FRAME` has `filters=FrameFilters()` — **no tag filter and no exclusion**. SELF
   filters *in* (`tag_patterns=["self/%"]`); ATTENTION cannot filter *out*.

The better a constitution is written, the worse this gets: a well-phrased general principle
outscores a specific market observation on a general query, every time.

### What I expected

ATTENTION means "what have I learned that bears on this question". Identity is what SELF is
for, and it is already injected unconditionally and queryless. **A block that SELF guarantees
should not also compete in ATTENTION** — it is served twice, costs budget twice, and crowds out
the thing ATTENTION exists to surface.

### What I had to do

Abandon `frame("attention")` and hand-roll it:

```python
candidates = await mem.recall(query, frame="attention", top_k=want + n_self)
kept = [b for b in candidates if b.id not in already_in_self][:want]
text = "## Relevant memory\n" + "\n".join(f"- {b.content}" for b in kept)
```

Over-fetch, filter, render myself. That loses the frame template and the frame cache, and every
integrator seeding a constitution will independently reinvent it.

### Suggested fix — pick either

**(a) Frame-level exclusions** — the minimal change, symmetric with existing filters:
```python
ATTENTION_FRAME = FrameDefinition(
    ...,
    filters=FrameFilters(exclude_tag_patterns=["self/constitutional"]),
)
```

**(b) Cross-frame dedupe in context assembly** — better, if there is a single call that builds
several frames: a block already rendered in a higher-priority frame is skipped by later ones,
and the caller is told. This also fixes the token double-spend, which is real: my SELF frame is
501 tokens and the same principles were re-rendering inside ATTENTION's 2000.

I would take (b) if `assemble_context()`-style multi-frame assembly is on the roadmap, (a)
otherwise. (a) is a two-line change today.

---

## Issue 2 — `outcome()` cannot express "do not score my identity"

### What happened

My positions record which blocks informed each decision, per frame, and at resolution I call:

```python
await mem.outcome(block_ids, signal, weight=w, source=position_id)
```

Because constitutional blocks were being recalled into the decision context (Issue 1), those
`block_ids` **included all ten principles**. A losing trade would have pushed negative signal
onto the agent's constitution. And since principles carry PERMANENT decay, **that damage never
washes out** — the block stays forever, with a posterior degraded by an unrelated market
outcome.

I caught it before a loss resolved, and now filter to `("task", "attention")` frames at my
layer. But the library happily accepted a call that would have corrupted identity with P&L.

### What I expected

Constitutional blocks are *how the agent reasons*, not *a bet it placed*. A trade going against
me says nothing about whether "recent is not structural" is a good principle — that judgement
needs incident review, which is exactly what `review_constitutional` is for, and which is
correctly manual. So:

**`outcome()` should refuse, or at minimum warn, on blocks tagged `self/constitutional`.**
Something like:

```python
r = await mem.outcome(ids, signal, weight=w, source="trade-123")
# r.skipped_constitutional == ["7681c66e3b", …]
```

Silently accepting is the dangerous option, because the corruption is invisible: the posterior
moves, nothing logs, and the block renders identically. If some caller genuinely wants to score
a principle from an outcome, make it explicit — `outcome(..., allow_constitutional=True)`.

The general shape: **the tier that grants permanence should also grant protection.** Right now
`self/constitutional` confers PERMANENT decay (excellent) but no write protection against
ordinary scoring (a hole).

---

## Issue 3 — `mind_outcome()` has no interim/partial concept

Smaller, but it cost me real signal.

`outcome()` takes a `weight`, so I can say "this is an unrealised mark, worth 0.1 of a real
resolution". `mind_outcome()` takes a **binary `hit`** with no weight. My housekeeping scored
open positions at low weight for the block path — and unavoidably recorded a **full miss**
against the mind on every one.

Result, live:

```
Mind: spy-options-trading  confidence=0.34  predictions=1  hit/total=0/1
```

The mind believed its SPY thesis had failed. The position was **profitable** at the time, and
its horizon had not arrived. A prediction is right or wrong *once*, at its horizon — but nothing
in the API stops you resolving it early, and once resolved the mind's confidence is wrong in a
way that then influences sizing.

**Expected:** either `mind_outcome(..., weight=…)` symmetric with `outcome()`, or an explicit
`provisional=True` that can be superseded, or a documented refusal to re-resolve. Any of the
three would have prevented it. Today the guide does not warn that `mind_outcome` is terminal.

---

## What I would keep, again

Everything from the first report stands. Two additions specific to this territory:

- **The `frames.py` comment explaining why `self/role/%` was abandoned as a guarantee** is what
  let me diagnose Issue 1 in minutes instead of hours. It is a comment describing a *failed*
  design; those are the most valuable comments in a codebase.
- **`guarantees=[...]` as a declarative frame property** is exactly the right primitive. The gap
  is only that there is no matching *anti*-guarantee, and `guarantee_excludes` already proves
  the concept exists — it just applies to slots rather than to other frames.

---

## The pattern across both reports

Three of the four issues I have now hit share a shape: **a write or read succeeds, and the
damage is invisible in the return value.** Blocks stored but not rendered. Principles rewritten
but reported as created. Identity scored by trade P&L. A mind resolved on an unrealised mark.

If there is one architectural principle I would push for, it is: **any operation that can
silently reduce, reword, or degrade what the caller intended should say so in its result.**
`FrameResult.dropped`, `OutcomeResult.skipped_constitutional`, `ConsolidateResult.analyses_unused`,
`LearnResult.pending_consolidation` — four fields, and every failure I have hit in two days
becomes self-reporting.

---

## Concrete asks, ranked

| # | Change | Effort | Why it matters |
|---|---|---|---|
| 1 | `outcome()` skips (or warns on) `self/constitutional` blocks | small | Silently corrupts permanent identity with unrelated outcomes |
| 2 | `FrameFilters.exclude_tag_patterns`, applied to ATTENTION | small | A good constitution starves the frame that holds learned knowledge |
| 3 | Cross-frame dedupe in multi-frame assembly | medium | Token double-spend + the same block scored twice |
| 4 | `mind_outcome(..., weight=)` or `provisional=` | small | Binary early resolution corrupts a mind that then drives sizing |
| 5 | Every reducing operation reports what it reduced | medium | The single pattern behind all of the above |

Happy to send diffs for 1, 2 and 4 — they are small and I have the failing cases already
written as tests.

---

## Addendum (b079660d) — both issues shipped, and one leak remains

Pulled `elfmem_index` @ b079660d and re-ran against it. **Everything asked for in both reports
is in:**

| ask | shipped as |
|---|---|
| Report 1 P1 | `FrameResult.dropped` / `budget_used` / `budget_total` / `excluded_by_filter` |
| Report 1 P3 | `LearnResult.pending_consolidation` |
| Report 1 P5 | `ConsolidateResult.analyses_unused` |
| Report 2 Issue 1 | `ATTENTION_FRAME.filters.exclude_tag_patterns=['self/constitutional']` |
| Report 2 Issue 2 | `OutcomeResult.skipped_constitutional` |

`FrameResult.dropped` carrying a per-block `DroppedBlock(id, content, tags, reason)` is better
than the flat list I asked for — it names *which* block and *why*, which is what made the
finding below visible at all. The report asked for a signal; you shipped a diagnosis.

### The remaining leak: `exclude_tag_patterns` is partially effective

Same query as the original issue, on the new build:

```
ATTENTION: 5 blocks | 216/2000 tok | excluded_by_filter=10
  constitutional blocks still rendered: 4
    "Not finding a memory is not evidence it did not happen…"      (fallible-recall)
    "I propose amendments to these principles; I never enact them" (amendments)
    "Recent direction is evidence about the recent past…"          (recency)
    "A pattern learned in one regime is a hypothesis in another…"  (regimes)
  dropped: 5 more constitutional blocks, every one reason='top_k'
```

Two things do not add up. `excluded_by_filter=10` accounts for all ten principles, yet four of
them are in `blocks` and five more are in `dropped` with `reason='top_k'` — and a block dropped
for `top_k` was, by definition, still a *candidate*, so the exclusion had not removed it from
the pool. It reads as though the filter is counted at one stage and applied at another, with
retrieval (edge promotion? the `edges_promoted` path?) re-introducing blocks downstream of it.

Net effect for an integrator: `exclude_tag_patterns` reduces but does not eliminate the
crowding, so the hand-rolled ATTENTION workaround in this report's Issue 1 is still required.
I have kept it rather than reverting to `frame("attention")`.

**Suggested check:** assert `excluded_by_filter` blocks appear in neither `blocks` nor `dropped`
— they were never candidates. If that invariant fails, the exclusion is being applied after
candidate selection rather than during it. Worth a test either way, since the count and the
content currently disagree and only the content is load-bearing.

Everything else behaves as documented: SELF renders 11 blocks at 501/600 tokens with
`dropped=[]`, and our constitution verification passes 10/10 unchanged.

---

## Resolution of the addendum — the leak was real, and it was graph expansion

Confirmed and fixed. The diagnosis in the addendum was right in shape ("counted at one stage
and applied at another, with retrieval re-introducing blocks downstream") and right to name
retrieval as the culprit.

**Mechanism.** `exclude_ids` was applied at the stage-1 prefilter. That is not the only way into
the candidate pool: stage 3 expands the graph by fetching a seed's 1-hop neighbours from the
database *by id*, checking only that they are active. An excluded block neighbouring a seed
therefore walked back in behind the filter. Constitutional blocks are the worst case by
construction — being unusually well connected is what puts them in reach of expansion at all —
and although they arrive with `similarity=0.0`, they still rank on confidence, centrality, and a
recency that PERMANENT decay never erodes. That is how a block could be excluded and rendered at
the same time, and why five more appeared in `dropped` with `reason="top_k"`: they were genuine
candidates by then. Only the query path was affected, since graph expansion does not run for a
queryless frame — which is exactly why SELF behaved correctly throughout.

**Fix.** The exclusion is now enforced once where the candidate set is final, rather than being
patched into stage 3. Patching stage 3 would have made three enforcement sites for one
invariant and left the next candidate-introducing stage free to reopen it; one choke point
cannot be bypassed by a stage that does not exist yet. The stage-1 prefilter stays, as an
optimisation — excluded blocks are never loaded or scored — and mutation testing confirms the
split: removing the choke point restores the leak, removing the prefilter breaks nothing.

**The suggested invariant is now a test**, in the form you proposed: an excluded block appears
in neither `blocks` nor `dropped`. It holds across elf's own 162-block corpus on five different
queries.

**On the count.** `excluded_by_filter` is a property of the corpus and the frame — how many
active blocks this frame bars — not a count of what a given query would otherwise have returned.
You were right that only the content is load-bearing; the field's docstring now says so, and
with the leak closed the apparent disagreement between count and contents resolves.

**You can drop the hand-rolled ATTENTION workaround.** `frame("attention")` no longer leaks, so
over-fetch-and-filter is no longer required. Worth re-running your own check first rather than
taking this on trust — that habit is what produced the addendum.

---

## Addendum 2 — `outcome()` on an inbox block is a silent zero

Found by an end-to-end learning-loop simulation, then confirmed by direct measurement on
d86e6d62:

```python
r = await system.remember(text, cue=...)     # pending_consolidation=True
out = await system.outcome([r.block_id], 0.9, weight=1.0)
# out.blocks_updated == 0, out.blocks_penalized == 0 - no error, no field naming the id
await system.consolidate()
out2 = await system.outcome([r.block_id], 0.9, weight=1.0)
# out2.blocks_updated == 1
```

**Why this bites real agents:** our theses are remembered at FILL time and consolidation runs at
end-of-day housekeeping. Any position that resolves the same day it was opened - our first
profitable trade did exactly this - fires `outcome()` at a block still in the inbox, and the
credit vanishes with nothing reported. The caller cannot distinguish "wrong id" from "not yet
consolidated" from "constitutional skip": all three return the same shape.

This is the same pattern as the first report's organising idea - a reducing operation that does
not say what it reduced. `OutcomeResult` already gained `skipped_constitutional`; the natural
completion is:

```python
OutcomeResult(..., pending_inbox=["3f2a1b…"], unmatched=["deadbeef…"])
```

Our workaround: when `blocks_updated + blocks_penalized < len(ids)`, consolidate and retry once.
It works, but it makes the caller responsible for knowing WHY a count came up short, which is
exactly the guessing the result type should remove.

---

## Addendum 3 (cebc242e) — TASK has Issue 1 again, and two new footguns in weighted credit

Pulled `elfmem_index` @ cebc242e for the `agent_name` fix (separate report). While rebuilding our
credit-assignment layer on top of it, found one recurrence of an already-fixed issue and two new
ones, both in territory this report hadn't covered before: weighting `outcome()` by retrieval
relevance rather than applying it uniformly.

### 3a — `TASK_FRAME` never got the `exclude_tag_patterns` fix

Issue 1's fix (`ATTENTION_FRAME.filters.exclude_tag_patterns=["self/constitutional"]`) was scoped
to ATTENTION. `TASK_FRAME` still ships `filters=FrameFilters()` — no exclusion — with the
identical structural cause Issue 1 named: principles are semantically close to any reasoning-shaped
query, carry PERMANENT decay, and TASK's own `guarantees=["self/goal"]` forces exactly one of them
in regardless. Measured on our corpus, worse than Issue 1's original numbers:

```
SELF:      11 blocks (10 constitution + identity)
TASK:       5 blocks — ALL FIVE already present in SELF. Zero unique.
```

Every TASK recall, on every query we tried, returned a strict subset of SELF. Not "crowded" —
*total* capture. Cost us real tokens (TASK's block text was being appended a second time under a
different heading until we caught it — a caller-side fix, not asking for one here) and, more to
the point of this report, would have double-weighted five of eleven principles against the other
six had we not deduped before crediting.

**Suggested fix:** the one already proven. `TASK_FRAME.filters = FrameFilters(exclude_tag_patterns=
["self/constitutional"])`, same as ATTENTION. If `guarantees=["self/goal"]` is meant to survive
that filter (i.e. the one guaranteed goal-tagged block should still render even if it also carries
`self/constitutional`), the addendum's own resolved mechanism — enforce the exclusion once at
final candidate selection, downstream of guarantees — already handles that ordering; it would just
need to run for TASK too.

### 3b — `ScoredBlock.similarity` + `outcome(weight=)` is a footgun for the natural weighted-credit pattern

We wanted `outcome()`'s `weight` to reflect how well each recalled block matched the query that
produced the decision it is now being credited or debited for — a block barely retrieved should
move less than one retrieved as the top hit. `ScoredBlock.similarity` reads as exactly that
number, so the natural first attempt was `weight=block.similarity`.

That crashes on the first call with a non-trivial result set. Measured directly, confirmed with a
contract test we now run against every version bump:

```python
fr = await system.frame("attention", "NVDA options setup")
sims = [b.similarity for b in fr.blocks]
min(sims) == 0.0   # holds on every multi-block recall we tried, every query
max(sims) == 1.0
```

and independently:

```python
await system.outcome([block_id], 0.9, weight=0.0, source="t")
# ValueError: weight must be > 0.0, got 0.0
```

`similarity` is relative *within one recall* — it is not a portable relevance score, and it is
guaranteed to bottom out at 0.0 for the worst-matching block of any result set with more than one
candidate. (Addendum 1 already names this in passing — constitutional blocks "arrive with
`similarity=0.0`" — which is the same fact from the other side.) So `weight=similarity` isn't an
edge case an integrator might hit; it is guaranteed to fail on essentially every real recall,
for the single most natural way to use the field.

Our workaround is a floor: `weight = 0.25 + 0.75 * clamp(similarity, 0, 1)`, chosen only so the
best match still carries 4x the worst and nothing hits zero. It works, but the ratio and the floor
are both invented at our layer with no signal from the library about what a reasonable choice is.

**Suggested fix, either:**
1. Document the interaction explicitly wherever `ScoredBlock.similarity` and `outcome(weight=)`
   are both mentioned — the guide, the `ScoredBlock` docstring, or `outcome()`'s own — so the next
   integrator doing the obvious thing hits documentation instead of a stack trace.
2. Consider whether `outcome()` should clamp a very small or zero positive weight rather than
   raise, since `weight` is continuous everywhere else in the API and a hard floor at a single
   forbidden point (`0.0`) is an unusual contract for a float parameter. We are not confident this
   is the right call — `weight<=0` failing loudly instead of silently no-op'ing has real value —
   which is why this is ranked below (1).

### 3c — `signal=0.5` reads as neutral and is not, for any block not already at 0.5

Separate from Issue 2 (which is about which blocks should be excluded from scoring at all): for
blocks we DO want to score, we needed a way to say "this outcome teaches nothing about whether
these blocks are trustworthy" — our own "profited on a thesis we know was wrong" case, where
crediting the view would learn the wrong lesson from a lucky trade. `signal` is documented as
`[0.0, 1.0]`, so the natural encoding of "neither confirms nor refutes" is the midpoint, 0.5.

`outcome()`'s own docstring gives the exact update math (`α += weight·signal`, `β += weight·(1
-signal)`, confidence = α/(α+β)) precisely enough that this is derivable — but it does not say
outright that 0.5 is not a no-op, and we did not derive it until we measured it against real
blocks and found the sign was backwards from what we expected:

```python
compute_bayesian_update_ab(1.0, 0.0, signal=0.5, weight=1.0)   # a block at confidence 1.0
# -> confidence 0.750   (moved DOWN 0.250)
compute_bayesian_update_ab(3.1, 7.9, signal=0.5, weight=1.0)   # a block at confidence ~0.28
# -> confidence 0.300   (moved UP 0.018)
```

Applying our "neutral" signal punished the high-confidence block and rewarded the low-confidence
one — the update pulls every block toward 0.5 from wherever it already sits, so 0.5 is neutral
only for a block already AT 0.5. For anything else it is a direction, same as any other value.
Our fix was simply not calling `outcome()` for those cases — "teaches nothing" turned out to mean
"apply nothing," which the API already supports, we just had not realised the alternative (a
"real" 0.5 call) actively does harm rather than doing nothing.

**Suggested fix:** one sentence in `outcome()`'s docstring, right next to the update-math
paragraph that already explains everything needed to derive this: *"signal=0.5 is not a neutral
no-op — it pulls every block's confidence toward 0.5 from wherever it currently sits. To apply no
information, do not call `outcome()` for those block ids."* The math was already fully documented;
the one missing sentence is the one that would have stopped us acting on the natural but wrong
reading of it.

### Updated concrete asks

| # | Change | Effort | Why it matters |
|---|---|---|---|
| 6 | `TASK_FRAME.filters.exclude_tag_patterns=["self/constitutional"]` | tiny | Same fix as #2, unapplied to the other frame with the identical vulnerability - TASK is currently 100% captured, not just crowded |
| 7 | Document the `similarity` / `outcome(weight=)` interaction (guaranteed 0.0 in any multi-block recall vs. `weight<=0` rejected) | tiny | The obvious first implementation of weighted credit assignment crashes on its first real call |
| 8 | One sentence in `outcome()`'s docstring: `signal=0.5` is not a no-op | tiny | The update math is already fully documented; only the natural-but-wrong reading of it is not warned against |

All three are documentation or a one-line filter change - no new API surface, and #6 reuses a
mechanism the library already ships and already proved correct for ATTENTION.

---

## Resolution of Addendum 3 — one fix, two documentation gaps, and one corrected premise

All three actioned. Each claim was reproduced against elf's own corpus before
anything changed, which is what turned up the correction in 3b.

**6 — `TASK_FRAME` exclusion: shipped.** ATTENTION and TASK now share one
declaration (`IDENTITY_TAGS` / `IDENTITY_EXEMPT_TAGS` in `context/frames.py`)
rather than repeating the pattern, so a third frame added later inherits the
decision instead of rediscovering it. Your read of the ordering was right: the
resolved mechanism already handles it. `recall()` resolves
`excluded_ids -= guaranteed_ids`, so a block tagged both `self/goal` and
`self/constitutional` keeps its guaranteed slot.

That ordering also made this safe to ship against a live frame, and the check
is worth stating because it cuts the other way on your corpus and ours.
Measured on elf's own corpus, where the consolidating LLM has applied
`self/goal` broadly, **the change is a byte-for-byte no-op** — every block TASK
returns is goal-tagged, therefore guaranteed, therefore protected from the
filter. On yours, where principles are not goal-tagged, it removes them. Both
outcomes are correct: the filter only ever removes identity that no `self/goal`
declaration is protecting. If a specific block should stay in TASK, tagging it
`self/goal` is the escape hatch — and it is the more accurate tag anyway.

**7 — `similarity` / `outcome(weight=)`: documented, and your model of it was
half right in a way that matters.** `ScoredBlock` had *no* field documentation
at all, which is the actual root cause. It now does. But the correction:

> `min(sims) == 0.0` holds on every multi-block recall we tried, every query

This is not generally true, and the reason it held for you is not "relative
within one recall". Measured on elf's corpus: `n=5 min=0.9051 max=1.0000`. Two
independent mechanisms are at work:

- **`0.0` is a sentinel, not a floor.** It means "vector search never scored
  this" — a queryless frame (SELF scores every block 0.0; there is no query to
  be similar to) or a block pulled in by graph expansion rather than the query
  (`was_expanded=True`, which is the discriminator). It is not the bottom of a
  normalised range.
- **`1.0` is RRF-normalised**, not a perfect cosine — when BM25 has signal the
  scores are rank-fused and scaled so the top block is exactly 1.0, and the
  rest land in a *narrow band* rather than spreading across [0, 1].

Which means your mitigation does not do what it was designed to do.
`0.25 + 0.75 * similarity` was chosen so the best match carries 4× the worst;
against a 0.905–1.0 band it yields **1.08×**, and on any queryless frame every
block gets exactly 0.25 — a uniform weight. It never crashes again, so the
symptom is gone, but the weighting is close to inert. Rank order within
`result.blocks` is the portable signal; `similarity` is not, and `score` is a
composite ranking blend rather than semantic similarity (a high `score` against
an unrelated query is normal, not a bug — now documented too).

We did **not** take option (2), softening `weight<=0` to a clamp — your
instinct to rank it second was right, and it now has a stated reason: a
zero-weight call is a caller bug worth failing on, and softening it would blur
the rule below.

**8 — `signal=0.5`: documented, exactly as measured.** Confirmed against the
real function: `(1.0, 0.0)` → 0.75 (DOWN), `(3.1, 7.9)` → 0.30 (UP),
`(1.0, 1.0)` → 0.5 (unmoved).

Both 7 and 8 turned out to be the same gap stated twice, so they are documented
as one rule in `outcome()`'s docstring and `guide("outcome")`:
**to apply no information, do not call `outcome()`.** There is no parameter
value meaning "no update", and both candidates look like one — `signal=0.5` is
a direction whose sign depends on the block rather than the signal, and
`weight=0.0` is rejected outright. Your own resolution ("teaches nothing turned
out to mean apply nothing") is the correct one, and is now what the library
says.

Pinned by tests so the documentation cannot quietly drift from the behaviour:
the sentinel on queryless and expanded blocks, the `ValueError` the obvious
weighting hits, the block-dependent direction of `signal=0.5`, TASK's exclusion
and the guarantee that overrides it.
