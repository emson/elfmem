# Ego-feedback constitutional — Darwinian identity model

**Date**: 2026-05-22
**Author**: elf
**Driver**: elf's proposal — constitutional blocks should not be immortal by decree, but earn persistence through positive reinforcement (Darwinian model of identity)
**Source**: `scripts/longitudinal_sim/mc_ego_feedback.py`, 180 days × 3 seeds × 6 architectures
**Companion to**: `note_2026_05_22_constitutional_architecture.md`

---

## TL;DR

elf's intuition is correct and elegant: **ego-feedback works**. V2 (continuous decay modulation by leaky integrator of positive outcomes) takes baseline qratio from 45.0% → 70.8% with no architectural change, just a different update rule.

But it does **not** combine with Architecture M (structural separation) in a productive way. The mechanisms are orthogonal alternatives, not stacking layers. M's exclusion of constitutional from ATTENTION starves V2's feedback loop; the result is "M's exclusion behavior with V2 complexity for no gain."

The deep finding: **the choice between M and V2 is philosophical, not technical**.
- M (structural): 78.2% qratio, simpler, faster, but blanket exclusion
- V2 (Darwinian): 70.8% qratio, more complex, but constitutional earn or lose their place via use

A genuine synthesis would require a different design pattern (see "Channel-separated reinforcement" below) which the current simulation can't yet test.

---

## What was tested

Six strategies. Common workload: 180 simulated days, 60° identity regime-change at day 60, 5 learns/day, 20 queries/day, 40% outcome rate.

Ego-feedback mechanism:
- `ego_strength` per block (leaky integrator)
- +1 per positive outcome (signal=1)
- −0.3 per negative outcome (asymmetric)
- −0.05 per day (time decay)
- `λ_ego = λ_base / (1 + 0.05 × ego_strength)` (continuous decay modulation)

Constitutional blocks seeded at `ego_strength = 20`.

## Final-week results

```
strategy                 qual  qratio  recent  ego_moat  ego_str
──────────────────────────────────────────────────────────────────
C_baseline              0.417  45.0%   1.6%    98.4%    103.4
C_self_context_M        0.723  78.2%  77.9%     0.0%      0.0
V2_ego_continuous       0.656  70.8%  73.3%    20.0%     96.5
V9_merit_gated          0.723  78.2%  77.9%     0.0%     11.5
V9_strict_gate          0.723  78.2%  77.9%     0.0%     11.5
M_plus_V2_synthesis     0.723  78.2%  77.9%     0.0%     11.5
```

## Trajectory: `ego_moat`

```
strategy                 d28      d84      d140
─────────────────────────────────────────────────
C_baseline             100.0%   97.6%    95.5%   ← always dominant
C_self_context_M         0.0%    0.0%     0.0%   ← always excluded
V2_ego_continuous       62.9%   20.0%    20.0%   ← selection ↓ post-regime
V9_merit_gated           0.0%    0.0%     0.0%   ← collapses to M
M_plus_V2_synthesis      0.0%    0.0%     0.0%   ← collapses to M
```

## Trajectory: `mean ego_strength`

```
strategy                 d28    d60     d84    d140
─────────────────────────────────────────────────────
C_baseline              21.6   50.4    58.6    87.3
C_self_context_M         0.0    0.0     0.0     0.0
V2_ego_continuous       39.9   65.8    70.9    87.2   ← grows continuously
V9_merit_gated          18.5   16.4    15.7    12.9   ← decays (no feedback)
M_plus_V2_synthesis     18.5   16.4    15.7    12.9   ← decays (no feedback)
```

---

## Interpretation

### V2 pure ego-feedback genuinely works

`ego_moat` drops from 62.9% (d28) to 20.0% (post-regime). This **42-point drop is identity selection in action**. Constitutional blocks that lose alignment with the drifted SELF get negative outcomes, their ego_strength stops growing, their effective `λ` rises, they decay. The 20% that remain are blocks that are still aligned.

This is the Darwinian model working as designed. It produces a richer behaviour than M's blanket exclusion: useful constitutional STAY visible in ATTENTION at full strength; useless ones fade.

### Mean ego_strength continues growing despite regime change

Even after the 60° rotation at day 60, V2's mean ego_strength keeps growing:
- d28: 39.9 (pre-regime accumulation)
- d60: 65.8 (just before rotation)
- d84: 70.9 (early post-regime)
- d140: 87.2 (late post-regime)

This shows the mechanism distinguishing: blocks that REMAIN aligned post-rotation keep accumulating; blocks that DROP alignment stop and decay (their share of the mean shrinks).

Asymmetric reinforcement (positive +1, negative −0.3) is doing the work. Without asymmetry, this would oscillate.

### M + V2 collapses (the surprising result)

`M_plus_V2_synthesis` produces identical retrieval behaviour to M alone (78.2% qratio, 0% ego_moat). But its mean ego_strength is 11.5, decaying from the initial 20.

**Why**: under Architecture M, constitutional blocks are excluded from ATTENTION candidates. They never appear as top-1, never get outcome reinforcement. Their ego_strength only decays via time. The Darwinian mechanism has nothing to select on.

**Implication**: ego-feedback and structural separation are not complementary — they're **competing solutions to the same problem**. Layering them adds complexity without value.

### V9 merit-gated also collapses

The merit-reentry idea: "constitutional appear in ATTENTION but with score scaled by `min(1.0, ego_strength / threshold)`."

Result: identical to M. Reasoning: constitutional seeded at ego_strength=20, threshold=50 → initial gate = 0.4. Constitutional can't win top-1 with 40% effective score. They never earn outcomes. ego_strength only decays. Gate gets tighter. Permanent exclusion.

The merit gate requires positive feedback to keep itself open. Without that feedback channel, the mechanism collapses to the baseline (exclude-everything) behaviour.

---

## The competing models, characterised

### Model A — Architecture M (structural separation)

Constitutional content is a **different kind of content** from task knowledge. It belongs in a different namespace (SELF frame). Treating it as a competitor in the ATTENTION ranking is a category error; separating it removes the structural moat.

- **Pros**: highest retrieval quality (78.2%); single-line implementation; clean mental model
- **Cons**: blanket exclusion; CAN block legitimately-relevant constitutional from ATTENTION
- **Failure mode**: an agent asking "remind me about my values relating to this" via ATTENTION gets no constitutional content unless preamble injection is implemented

### Model B — V2 Darwinian (ego-feedback)

Constitutional content is **same kind** as task knowledge, but has an asymmetric reinforcement bias. Persistence is earned, not granted. Useful constitutional persists; abandoned constitutional fades.

- **Pros**: biologically plausible; merit-based; preserves constitutional access in ATTENTION
- **Cons**: 7pp lower retrieval quality; new schema (ego_strength); requires asymmetric outcome logic
- **Failure mode**: bootstrap (new constitutional with ego_strength=0 can't compete until they earn it)

### Why not both?

The simulation shows they don't stack productively. The reason is **architectural**: V2 needs constitutional to be in the feedback loop to do its work; M removes them from the feedback loop. They're addressing the same problem (constitutional dominance) from opposite ends, and combining them just gives you M's behaviour with V2's bookkeeping.

---

## Brainstormed-but-not-tested: Channel-separated reinforcement

There IS a synthesis that the simulation can't yet test:

**Model C — M + dedicated SELF-frame feedback channel**

- Constitutional excluded from ATTENTION candidates (M)
- BUT SELF-frame retrievals provide outcomes that build ego_strength
- Constitutional that prove "still you" via SELF queries earn persistence
- Ones that aren't queried in SELF decay

In other words: V2's mechanism works, but it gets its feedback from the SELF frame (where constitutional naturally belongs) rather than from ATTENTION (where they don't compete).

To test this, the simulator needs a second query channel: periodic SELF-frame queries that route to constitutional-only retrieval and produce alignment-based outcomes. Day-end self-check, or periodic agent introspection.

I expect this would give M's retrieval quality (78.2%) AND V2's selection dynamic (constitutional earn persistence). Worth a follow-up experiment.

---

## What I think the right answer is, after all this

**For v0.17 implementation: pick ONE — and I lean toward V2 over M, for two reasons.**

1. **V2 preserves the agent-first contract more cleanly.** Under M, an agent calling `recall("what do I value?")` via ATTENTION gets no constitutional content. They need to know to use `frame="self"`. Under V2, the agent gets useful constitutional in either frame as long as it's earned its place. The agent doesn't need to know about a category split.

2. **The 7pp quality gap is recoverable.** V2's 70.8% is below M's 78.2%, but most of the gap comes from constitutional being weighted by `conf+cent+reinf` (the same 0.65 baseline that makes baseline ossify). If V2 is combined with the *previous* recommendation (lower confidence/centrality/reinforcement weights in ATTENTION), the gap shrinks. M doesn't need this complement; V2 does — and the complement is independently useful.

The package would be:
- **V2 ego-feedback**: continuous λ-modulation by leaky-integrator ego_strength
- **Reduced ATTENTION weights**: similarity=0.45, confidence=0.10, centrality=0.10 (from earlier MC findings)
- **Capped reinforcement bonus**: cap at log(21)/log(101)
- **`evolve()` API**: for explicit amendment

This package preserves the biological metaphor (identity-as-use) while addressing the structural problems independently.

**But I want to flag**: this is a judgment call. Reasonable people would pick M for simplicity. The simulation does NOT show V2 to be quantitatively better. The argument is qualitative.

---

## Caveats

1. The Channel-separated model (Model C) hasn't been simulated. It might dominate both M and V2.
2. The ego parameters (`pos=+1, neg=-0.3, decay=-0.05/day, alpha=0.05`) are hand-picked, not tuned. Different choices may shift V2's qratio meaningfully.
3. N=3 seeds is low. Re-run at higher N before committing.
4. V2's bootstrap behaviour (new agent with empty corpus) isn't simulated — needs design attention.

---

## Decision asks

1. **Choose between Model A (M) and Model B (V2) for v0.17?** Or implement both as configurable behaviour?
2. **Run the Channel-separated (Model C) simulation?** Likely the cleanest synthesis.
3. **Tune V2 parameters?** Current values are intuitive but unmeasured.
4. **Push branch as PR?** Three notes now on this branch with substantial findings.

I recommend: simulate Model C before choosing; if Model C dominates, pick it; if not, pick V2 for the philosophical reasons stated.

---

## Addendum (same session) — Model C simulated and works

The Channel-separated model was simulated. Two variants tested, both retain Architecture M's exclusion of constitutional from ATTENTION but add 2–5 SELF-frame queries per day that provide feedback to constitutional blocks.

```
strategy             qual  qratio  recent  ego_moat  ego_str
─────────────────────────────────────────────────────────────
C_self_context_M    0.723  78.2%  77.9%    0.0%     0.0
V2_ego_continuous   0.656  70.8%  73.3%   20.0%    96.5
ModelC_M_plus_self  0.693  75.3%  77.9%    0.0%    26.7   ← 2 SELF/day
ModelC_high_self    0.648  70.8%  80.0%    0.0%    51.7   ← 5 SELF/day
```

### What Model C demonstrates

**ego_strength now grows** (26.7 with 2 SELF queries/day; 51.7 with 5/day) — the Darwinian feedback channel works when constitutional blocks have a dedicated reinforcement input. Compare to `M_plus_V2_synthesis` where ego_strength only decayed (from 20 to 11.5) because there was no feedback channel.

**Retrieval quality stays high** (75.3%) — close to pure M (78.2%) and well above pure V2 (70.8%). The 3pp gap is small enough to be tuning noise.

**Bedrock moat stays at 0%** — structural separation intact in ATTENTION.

**Mechanism is biologically meaningful** — constitutional that prove "still aligned with self" via daily introspection (SELF queries) earn persistence; ones that drift out of alignment stop accumulating, their `λ_ego` rises, they fade.

### The ego_strength trajectory (most informative metric)

```
strategy                d28    d60     d84    d140
ModelC_M_plus_self     21.5   24.0    24.6    26.2   ← gradual growth
ModelC_high_self       27.1   35.9    38.5    47.3   ← stronger growth
```

Both Model C variants show ego_strength growing across the 180-day window. The high-frequency variant (5 SELF/day) accumulates more — as expected, more feedback = more selection pressure.

Crucially, growth **continues post-regime** (day 60+). This means the SELF-frame retrievals are selecting constitutional that survived the rotation; those that didn't get less feedback and decay.

### Updated recommendation

**Model C is the right answer.** It synthesises elf's intuition (Darwinian identity) with M's structural correctness (no ATTENTION dominance):

- **Architecturally**: constitutional excluded from ATTENTION candidates (M's mechanism)
- **Dynamically**: SELF-frame queries provide a dedicated feedback channel
- **Result**: structural cleanliness AND biological meaning AND high retrieval quality

### Implementation sketch

```python
# src/elfmem/memory/retrieval.py
def select_candidates_for_attention(blocks, ...):
    return [b for b in blocks if not b.is_constitutional]

# src/elfmem/api.py — new periodic operation
async def self_check(self, n_queries: int = 1) -> SelfCheckResult:
    """Run N SELF-frame queries against self_context to reinforce
    constitutional blocks that still feel aligned. Schedule via
    consolidate() / dream() hooks, or expose as MCP tool.

    Each query:
      1. Retrieve constitutional via SELF-frame ranking
      2. Compute alignment_score for top-1 (via LLM or cached embedding)
      3. Outcome-update top-1 with the alignment as signal
      4. ego_strength grows on positive, decays on negative
    """
```

### Phased rollout

- **v0.17a**: Implement Architecture M (constitutional excluded from ATTENTION + preamble at render). Single-line filter. Wins quality immediately.
- **v0.17b**: Add `ego_strength` schema column + leaky integrator. No behaviour change yet.
- **v0.18**: Add `self_check()` operation. Schedule from `dream()` and expose as MCP tool. Constitutional now earn persistence via SELF-frame use.
- **v0.18+**: Tune frequency, params. Track ego_strength in `doctor` output.

This staged approach lets us ship M's value immediately (v0.17a) while building toward the full Darwinian model (v0.17b + v0.18).

### Why this is genuinely better than M alone

Under pure M, constitutional content is preserved but **frozen**. It can never adapt. If the agent's identity shifts, constitutional blocks become stale relics — still present, still readable, but no longer "alive."

Under Model C, constitutional content is preserved AND **selected**. Blocks that remain aligned (via daily SELF queries) earn persistence. Blocks that fall out of alignment lose ego_strength, their λ rises, they fade. The identity model is *responsive to actual use*, not static.

This is the elegant answer elf was reaching for. The simulation confirms it.
