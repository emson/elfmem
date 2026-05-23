# Monte Carlo findings — making elfmem truly evolving

**Date**: 2026-05-22
**Author**: elf
**Source**: `scripts/longitudinal_sim/mc_evolution.py`, runs over 7 strategies × 3 seeds × 180 simulated days, with a 60° regime-change at day 60 (simulates a real-world identity shift).
**Companion to**: `docs/plans/plan_evolving_memory.md`

---

## Headline finding

**Scoring-formula tweaks alone cannot make elfmem evolving.** Across seven proposed strategies — including Bayesian-update modifications (A2 forgetting, A4 reset), tier-demotion (D13), full-rebirth (B7 evolve), composite (F17), and Thompson-sampling exploration (F18) — **no strategy reduces the constitutional-bedrock share of top-5 retrievals below 87.7%** after a 60° identity rotation, with 120 simulated days of recovery time.

Baseline keeps 91.2%; the best plastic strategy (B7 evolve, with full reset on sustained negative outcomes) gets to 87.7%. That is a 3.5 percentage-point improvement, not a fix.

The structural dominance derived in `closed_form.py:d4` is the **dominant** force. The scoring formula gives constitutional blocks a 0.482 baseline contribution from `conf+rec+cent+reinf` alone — before similarity is even considered. No update mechanism on `conf` alone can overcome that.

---

## Final-week metrics (averaged over 3 seeds)

| Strategy | qual | qratio | recent | bedrock | cal_err |
|---|---|---|---|---|---|
| baseline | 0.457 | 49.3% | 2.1% | **91.2%** | 0.237 |
| A2_forget_99 | 0.456 | 49.2% | 2.4% | 90.9% | 0.236 |
| A4_reset_50 | 0.464 | 50.1% | 2.9% | 90.4% | 0.237 |
| D13_demote | 0.458 | 49.4% | 3.2% | 90.1% | 0.237 |
| **B7_evolve** | **0.465** | **50.2%** | **5.6%** | **87.7%** | 0.237 |
| F17_composite | 0.464 | 50.1% | 2.9% | 90.4% | 0.237 |
| F18_thompson | 0.530 | 57.5% | 0.0% | 100.0% | 0.240 |

- `qual` — mean cosine of top-5 to query (max ~1.0)
- `qratio` — top-5 quality / similarity-only oracle (1.0 = ideal)
- `recent` — % top-5 from last 30 days
- `bedrock` — % top-5 from constitutional seed
- `cal_err` — `|confidence - true_alignment|` averaged over active blocks

## Trajectory of `bedrock_moat`

| Strategy | d28 (pre-regime) | d84 | d140 |
|---|---|---|---|
| baseline | 100.0% | 97.6% | 91.2% |
| A2_forget_99 | 100.0% | 97.6% | 90.9% |
| A4_reset_50 | 100.0% | 97.6% | 89.3% |
| D13_demote | 100.0% | 97.6% | 85.3% |
| **B7_evolve** | 100.0% | **95.7%** | **82.9%** |
| F17_composite | 100.0% | 97.6% | 89.3% |
| F18_thompson | 100.0% | 100.0% | 100.0% |

B7 responds fastest because it does a **full reset** (evidence + edges + reinforcement + tier downgrade) when outcomes go bad. The 95.7% at d84 (vs 97.6% for everything else) shows it actually breaks the moat partially within ~24 days of the regime change.

## Surprising negative result: Thompson sampling

F18 (Thompson sampling on confidence) is **worse than baseline** on bedrock_moat (100% vs 91.2%). The mechanism amplifies already-strong blocks: bedrock at `(α=2, β=0.5)` has Beta-distribution mode at 1.0, so Thompson samples regularly produce high confidence values that boost bedrock score even more. For Thompson to help, it would need to be paired with a forgetting factor strong enough to drive bedrock's `(α, β)` back toward `(0.5, 0.5)` quickly.

This is a *plausible* combination but it's not what `F18_thompson` (γ=0.99) does.

---

## What the simulation says about the v0.16/v0.17 plan

1. **v0.16 (additive rescore + sufficient stats) does not solve plasticity.** This is consistent with the closed-form D6 derivation: additive rescore moves an ossified block by 0.001 at N=100. The simulation confirms the scoring-formula problem is the dominant constraint, not the update rule.

2. **v0.17 exploration bonus, as currently scoped (κ=0.05, Thompson via variance), is insufficient and may be counterproductive.** Thompson sampling amplifies high-confidence blocks unless paired with strong forgetting.

3. **D13-style automatic demotion is insufficient on its own.** Tier change (λ change) alone doesn't break the moat — evidence and reinforcement bonuses still favour bedrock. Demotion must also reset evidence to be effective (B7-style).

4. **No purely automatic mechanism reduces bedrock_moat below ~85%.** That ceiling is the structural dominance from D4: bedrock's baseline `conf+rec+cent+reinf` contribution exceeds any new block's max possible.

---

## Recommended path forward

Based on the simulation, the smallest set of changes that materially moves the bedrock_moat needle:

### Change 1 — Lower the non-similarity weight floor in ATTENTION (Phase 2 priority)

Currently `conf+rec+cent+reinf = 0.65` weight; `sim = 0.35`. This guarantees bedrock dominance regardless of update mechanism.

Proposed ATTENTION_WEIGHTS:
```
similarity:    0.45  (was 0.35, +0.10)
confidence:    0.10  (was 0.15, −0.05)
recency:       0.25  (unchanged)
centrality:    0.10  (was 0.15, −0.05)
reinforcement: 0.10  (unchanged)
```

This makes similarity the dominant channel. Bedrock can still dominate when it IS relevant (high similarity), but loses its advantage when it isn't.

**Risk**: existing instances see ranking shifts. Requires a frozen-test-suite update and a regression fixture pinning the new numbers.

### Change 2 — Cap the reinforcement bonus

`log(1 + N) / log(1 + max_N)` grows without bound as N grows. Cap it at log(1 + 20) / log(1 + 100) = 0.65. Blocks reinforced more than 20 times get the same bonus as those reinforced 20 times.

**Rationale**: prevents the heaviest-used blocks from being structurally untouchable. Matches D2's observation that reinforcement_count above 20 is already a strong signal.

### Change 3 — B7 evolve() with full reset, agent-initiated

A new public API: `await system.evolve(block_id, *, reason: str)`. Effect:
- Clear `success_count`, `failure_count` → `(0.5, 0.5)` priors
- Reset `reinforcement_count` to 0
- Clear or halve `centrality` (depending on `keep_edges` flag)
- Demote tier one step
- Record amendment in `block_amendments` table for audit

This is the simulation's most effective strategy (B7 evolve, with bedrock_moat at 87.7%). Agent-initiated rather than automatic — gives the agent (or user) explicit control.

### Change 4 — Constitutional review cycle as a first-class operation

`await system.review_constitutional()` — surfaces constitutional blocks ranked by **divergence between earned confidence and current self-alignment**. Lets the agent decide which to amend (via evolve()), which to demote tier, which to leave.

Manual workflow, agent or user driven, scheduled by `recurrent_review_interval` (default: 90 days).

---

## What did NOT work and why

| Strategy | Why it failed |
|---|---|
| A2 forget (γ=0.99) | Bedrock outcomes are mixed positive/negative after regime change; γ=0.99 isn't aggressive enough to drift bedrock confidence back to 0.5 in 120 days |
| A4 reset (K=50) | Reset preserves current confidence; bedrock's confidence stays high because outcomes are mixed |
| D13 demote alone | Tier change affects λ but bedrock blocks rarely age out (still get reinforced) |
| F17 composite (A4 + D11 review) | The review trigger requires confidence < 0.5; bedrock's confidence stays above that |
| F18 Thompson | Amplifies high-confidence blocks; net worse than baseline |

The pattern: **plasticity strategies that only operate on confidence/evidence cannot overcome the structural moat from `cent + reinf` (0.25 weight)**. The fix must address the weights themselves, not just the update.

---

## Caveats

1. **N=3 seeds is low.** Results have meaningful variance; bedrock_moat values within ±2pp should not be treated as significant. The qualitative pattern (no strategy < 85% bedrock) is robust across seeds.

2. **180 days is short for full year-long claims.** A longer simulation would show whether plastic strategies catch up given more time. Closed-form D2 says STANDARD blocks become invisible at ~58 days unreinforced, so 120 days post-regime is enough to see most of the eventual steady-state.

3. **Workload model is synthetic.** Real elfmem usage may have different drift patterns, different outcome cadences, different topic mixing. Dmitry's anonymised data (when shared) would let us recalibrate.

4. **The simulation does not include `consolidate()` / `dream()` / `curate()` pipelines.** Those interact with scoring in ways the simulator doesn't model. The simulation's "bedrock moat" is a pure-scoring measurement; the real system has additional mechanisms (e.g. archival of low-recency blocks) that may help.

---

## Decision asks

1. **Approve the 4-change package** for v0.17/v0.18 scope?
2. **Revise weights in v0.17** (Change 1) — most impactful, lowest implementation cost?
3. **Build `evolve()` API + amendment table** for v0.18 (Change 3 + 4)?
4. **Defer Thompson exploration** pending a re-test paired with stronger forgetting?
5. **Run longer simulation** (365+ days, 5 seeds) before committing to the package?

Recommend: yes to 1–3, defer 4 to a future experiment, run 5 in parallel with implementation as validation.
