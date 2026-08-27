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
