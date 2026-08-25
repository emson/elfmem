# 0012 — Do not archive on low use

**Status**: Accepted
**Date**: 2026-08-25
**Deciders**: elf, Ben

## Context

v0.17.1 added lexical use-attribution: a `Stop` hook scores the turn's answer
against the blocks retrieved for it and calls `record_use()` on the ones whose
distinctive terms show through (`elfmem.memory.attribution`). It exists because
reinforcement counts *retrievals*, so a block retrieved constantly and never
drawn on rises exactly like one doing the work.

The obvious next step was proposed immediately: make `curate()` use-aware —
archive blocks with high assembly and near-zero use. It was proposed on the
strength of a single suggestive case (a completed maintenance errand,
`1757ad77`, that kept surfacing in the identity frame) and an argument from
biology: a living system does not strengthen a memory for being *considered*.

Before building it, the rule was tested against real data rather than more
argument: all 148 active blocks scored against 161 real assistant responses
drawn from seven months of transcripts in this repository.

## Alternatives considered

- **Archive high-assembly / low-use blocks.** The proposal under test.
- **Penalise low use** (accelerate decay rather than archive). Same signal,
  softer action; the measurement below condemns both equally.
- **Reward-only, never penalise.** What v0.17.1 actually shipped.
- **Record use, act on nothing.** Ledger telemetry with no ranking effect.

## Decision

**Rejected.** The rule inverts on real data. Ranking the corpus by the exact
criterion proposed — highest reinforcement among never-echoed blocks — the top
15 archive candidates are **10 constitutional blocks**, including the two
most-reinforced blocks in the entire corpus:

| group | n | mean hits | never echoed | mean reinforcement |
|---|---|---|---|---|
| `self/constitutional` | 39 | 0.87 | 54% | **16.8** |
| everything else | 109 | 0.75 | 72% | 3.4 |

The failure is not that constitutional blocks are invisible to attribution —
they are echoed slightly *more* than average. It is that the rule keys on
high reinforcement, and constitutional blocks carry 5× the reinforcement of
everything else because the SELF frame guarantees them slots on every
retrieval. Combine "high reinforcement" with "expressed as disposition rather
than vocabulary I quote" and the constitution is precisely what the rule
selects. A use-aware `curate()` would archive elf's constitution first and
keep the block reading "test entry for CLI verification" (11 echoes, the
second-most-echoed block in the corpus).

The originating case does not survive either. The stale errand and the live
identity block are indistinguishable by use: 4 echoes versus 6. The errand
shares identity vocabulary, so it is credited whenever identity is discussed,
regardless of whether it contributed anything.

**What this reveals about the signal itself:** lexical attribution measures
topical vocabulary overlap. That is a reasonable proxy for use when scoring is
*conditioned on retrieval* — the shipped hook only ever scores blocks that were
actually injected into that turn. It is not a proxy for value, and it must
never be inverted into evidence of *dis*use, because a block can fail to echo
for three unrelated reasons: it was not used, it was paraphrased, or it shapes
how the answer is written rather than what it says. The corpus cannot tell
those apart, and the third is what constitutions are made of.

The reward-only asymmetry shipped in v0.17.1 is what makes this a finding
rather than a corrupted corpus. Had the penalty side been built at the same
time, the first `curate()` run would have decayed the constitution.

## Consequences

- `curate()` stays decay-and-graph based. No use term.
- `record_use()` remains reward-only. The asymmetry is now load-bearing and
  documented as such, not a conservative default awaiting confidence.
- `use` events keep accruing in the ledger. They are honest telemetry and cost
  nothing; this ADR rejects *acting* on them, not recording them.
- The calibration test in `tests/test_attribution.py` was measuring the wrong
  false positive — unrelated blocks, when the real failure is
  related-but-unused. Extended accordingly.

## Revisit when

Any one of these would justify reopening:

1. **Conditioned data disagrees.** This test is unconditioned: it scores blocks
   against responses they were never retrieved for, which inflates echo for any
   block sharing a topic's vocabulary. Several thousand real
   assembled-then-judged turns from the hook would be the honest measurement,
   and could still separate use from mere topicality.
2. **Attribution stops being lexical.** A judge that distinguishes "informed
   this answer" from "shares vocabulary with this answer" removes the
   confound this ADR turns on.
3. **The constitution stops being guaranteed slots.** The inversion is driven
   by SELF-frame guarantees inflating constitutional reinforcement. A frame
   design that does not guarantee would change the arithmetic.

## References

- `src/elfmem/memory/attribution.py` — the signal and its calibration
- `tests/test_attribution.py::TestCalibration` — false-positive bounds
- ADR 0009 — retire decay-driven archival (the prior time an archival rule
  was retired for selecting the wrong blocks)
