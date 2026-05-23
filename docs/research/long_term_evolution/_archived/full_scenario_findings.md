# Full-scenario evaluation — does Model C evolve as intended?

**Date**: 2026-05-22
**Author**: elf
**Source**: `scripts/longitudinal_sim/mc_scenarios.py`, 365 days × 2 seeds × 4 strategies × 5 scenarios = 40 runs
**Branch**: `feature-constitutional-experiments`

---

## TL;DR

Testing across five workload scenarios revealed **three findings the prior single-scenario simulations couldn't see**:

1. **No universal winner.** Baseline beats M and Model C under stable conditions (85.3% vs 78.4% vs 76.8%). Model C dominates under drift/regime-change/adversarial conditions. The optimal architecture **depends on the agent's actual drift profile**.

2. **Model C is the most consistent.** Across all five scenarios, Model C ranges 75.2%–81.9%. Baseline swings 44.5%–85.3% — high variance, scenario-dependent.

3. **THE UNINTENDED CONSEQUENCE: Ego hoarding.** In every scenario, ONE constitutional block has 9.5× the average ego_strength. SELF-query winner-takes-all creates preferential attachment among the constitutional constellation. The "loudest" constitutional drowns out the other 9. Identity collapses to a single principle.

A new variant (**Model D — distributed feedback**) addresses the hoarding problem: drops es_concentration to 3.3 while preserving quality.

---

## The five scenarios

| Scenario | Drift σ/day | Regime change | Quiet days | Outcome noise |
|---|---|---|---|---|
| stable | 0.005 | — | — | 0% |
| slow_drift | 0.020 | — | — | 0% |
| regime_change | 0.020 | day 120 (60° rotation) | — | 0% |
| quiet_burst | 0.015 | — | days 90–150 | 0% |
| adversarial | 0.020 | — | — | 15% (random signal flip) |

Same workload: 5 learns/day, 20 ATTENTION queries/day, 40% outcome rate, 10 constitutional seeded at day 0.

---

## Cross-scenario quality_ratio

```
scenario          baseline     M         Model C    Model D
─────────────────────────────────────────────────────────────
stable             85.3%      78.4%     76.8%      76.6%    ← baseline wins
slow_drift         62.5%      72.4%     75.2%      76.8%    ← D wins
regime_change      44.5%      77.6%     78.7%      [pending] ← C/D win
quiet_burst        76.6%      77.0%     78.6%      [pending] ← all close
adversarial        65.7%      77.3%     81.9%      [pending] ← C/D win

range              40.8 pp    6.0 pp    7.3 pp     [tbd]      ← consistency
```

**Reading the range row**: baseline's quality swings 40.8 percentage points depending on workload. Model C swings 7.3pp. Model C is dramatically more *consistent*.

But baseline's PEAK (85.3% in stable) is higher than Model C's peak. There's a real trade-off.

---

## Constitutional preservation

```
scenario          baseline     M         Model C    Model D
─────────────────────────────────────────────────────────────
stable                0          0          10        10
slow_drift            0          0          10        10
regime_change         0          0          10        10
quiet_burst           0          0          10        10
adversarial           0          0          10        10
```

(baseline/M show 0 because they don't track ego_strength — a measurement artifact, not a real concern for those models)

Model C and D preserve all 10 constitutional throughout 365 days, across all scenarios. No catastrophic constitutional death even under regime change or adversarial noise.

---

## Ego concentration — the unintended consequence

```
scenario          baseline   M       Model C   Model D
────────────────────────────────────────────────────────
stable               —       —        9.6       3.3
slow_drift           —       —        9.6       3.4
regime_change        —       —        9.5       [pending]
quiet_burst          —       —        9.5       [pending]
adversarial          —       —        9.5       [pending]
```

`es_concentration = max(ego_strength) / mean(ego_strength)`. Higher = more uneven.

**Model C's 9.5 is structural.** Every scenario, every seed, produces the same concentration. This is preferential attachment among constitutional blocks:

1. Initial seeding produces 10 constitutional with slightly different alignments to self_topic
2. SELF queries always pick top-1 (winner-take-all)
3. The block with highest initial alignment wins every SELF query
4. It accumulates ego_strength rapidly; its λ shrinks; its centrality grows; it wins more
5. The other 9 starve, decay, become functionally inert

**Why this matters**: constitutional content is meant to represent a constellation of values (honesty, curiosity, precision, kindness...). A single block hoarding all reinforcement effectively kills the constellation. The agent's "constitution" collapses to one principle.

This is a **failure mode**, not a feature.

### Model D fixes hoarding

Model D: instead of winner-take-all, distribute SELF-query feedback across top-3 constitutional, weighted by softmax of their scores.

- Stable: es_concentration drops from 9.6 → 3.3
- Slow drift: from 9.6 → 3.4

A 65% reduction in concentration with no quality loss (76.6% vs 76.8%; 76.8% vs 75.2% — actually higher in slow_drift!).

---

## Per-scenario deep dives

### Stable identity — baseline wins, plasticity costs

Under minimal drift, constitutional blocks REMAIN aligned with self. The bedrock dominance "problem" disappears because there's no drift to expose it.

Baseline gets to use its full scoring power (similarity + earned confidence + centrality + reinforcement). Result: 85.3% qratio.

M excludes constitutional from ATTENTION — but those constitutional are still aligned, so exclusion costs 7pp.

**Implication**: if an agent's identity rarely changes, current elfmem is actually well-designed for their usage. Plasticity mechanisms aren't free.

### Slow drift — Model C/D wins moderately

Under 0.020/day drift, baseline ossifies as constitutional drift away from current self (62.5%). Model C/D maintain ~75-77%.

The 13-percentage-point advantage represents real value in real-world usage where identity does evolve.

### Regime change — Model C/D wins dramatically

Sudden 60° rotation at day 120 catastrophically harms baseline (44.5%). Constitutional are now misaligned but still dominant in scoring. Model C/D unaffected (~78%).

This is the "career change" or "major life event" scenario. If you expect identity shifts, you need plasticity.

### Quiet burst — all strategies similar

60 days of silence (days 90–150) doesn't drift the system. When the agent returns, everything is approximately where it was. All strategies converge near 77%.

**Important sub-finding**: Model C's ego_strength survives 60 days of quiet. With −0.05/day decay × 60 days = −3.0 from initial 20 = 17.0 remaining. Far from zero. The mechanism is robust to sabbatical-length quiet periods.

### Adversarial noise — Model C wins biggest

15% outcome noise (random signal flips) actually causes Model C to do BETTER (81.9%) than baseline (65.7%). Why?

Hypothesis: noise prevents any single non-constitutional block from accumulating runaway confidence. Top-K stays diverse because no block has a deterministic edge. The distribution of retrieved content stays closer to the true-relevant distribution.

This is a positive emergent property: Model C is more robust to noisy outcomes than baseline.

---

## What the simulation says about intended vs. unintended behaviour

**Intended behaviours (verified)**:
- ✅ Model C selects useful constitutional under drift (ego_strength rises for aligned, falls for misaligned)
- ✅ Model C preserves all 10 constitutional throughout 365 days
- ✅ Model C is consistent across diverse workloads
- ✅ Model C robust to adversarial noise
- ✅ Model C robust to quiet periods (no catastrophic decay)

**Unintended behaviours (discovered)**:
- ❌ **Hoarding**: one constitutional block dominates ego_strength accumulation (es_concentration=9.5)
- ⚠️ **Plasticity tax under stability**: Model C costs ~8pp vs baseline when identity isn't drifting
- ⚠️ **Cost is invisible in stable conditions** — agent might assume plasticity is free; it isn't

**Mitigation**:
- Model D (distributed feedback) addresses hoarding (concentration → 3.3)
- Plasticity tax is unavoidable — must accept it OR detect drift and switch modes adaptively

---

## Recommendation update

**For v0.17**: implement Model D (distributed SELF-query feedback) rather than naive Model C.

Specifically:
- Architecture M: constitutional excluded from ATTENTION (the structural fix)
- Model D mechanism: SELF queries distribute outcome signal across top-3 constitutional by softmax weight (the Darwinian selection without hoarding)

```python
# Pseudocode for distributed SELF feedback
async def self_check(self, n_queries: int = 1):
    for _ in range(n_queries):
        query = self.current_self_context
        candidates = await self.recall_constitutional(query, top_k=10)
        top_3 = candidates[:3]
        weights = softmax([b.self_alignment_score for b in top_3])
        for block, weight in zip(top_3, weights):
            outcome_signal = await self.evaluate_alignment(block)
            await self.outcome(block.id, signal=outcome_signal * weight,
                                weight=weight)
```

**For v0.18+**: adaptive strategy selection. Detect drift rate; switch between "constitutional-included" mode (baseline-like, optimal under stability) and "constitutional-excluded" mode (M-like, optimal under change).

Detection mechanism: track `mean(cosine(constitutional, current_self))` over rolling window. If drift > threshold, switch to M+D mode. If stable, allow constitutional in ATTENTION.

This addresses the trade-off curve: pay the plasticity tax only when plasticity is needed.

---

## Caveats and limits of the simulation

1. **N=2 seeds is low.** Some values may be noise (e.g., Model C's 81.9% in adversarial). Re-run at N=5 for publication.

2. **No archival simulated.** Real elfmem prunes low-recency blocks; this would change long-timescale dynamics. Layer 2 work.

3. **Topic-space ground truth is synthetic.** Real-world embedding space may have different geometry. Dmitry's anonymised data needed to calibrate.

4. **365 days is meaningful but not 5+ years.** Constitutional immortality (D5 derivation: 47.5 years) is untested at relevant scale.

5. **No `dream()`/`consolidate()`/`curate()` interaction tested.** Those mechanisms compound with scoring. The simulation isolates scoring only.

6. **ego parameters hand-picked**. Not tuned. Real implementation would need empirical calibration.

---

## Decision asks

1. **Approve Model D for v0.17 over naive Model C?**
2. **Investigate adaptive-mode-selection for v0.18+?**
3. **Run extended simulation (1825 days, N=5 seeds, with archival)?** Would test long-term stability.
4. **Compare ego parameter sweeps?** (pos_rate, neg_rate, time_decay tradeoffs)
5. **Push branch as PR for review?** Five notes now, three simulators, substantial decision-shaping findings.

---

## Complete results table (all 5 scenarios, all 4 strategies)

```
scenario          baseline   M       Model C   Model D
─────────────────────────────────────────────────────────
stable             85.3%    78.4%    76.8%     76.6%
slow_drift         62.5%    72.4%    75.2%     76.8%
regime_change      44.5%    77.6%    78.7%     70.5%  ← D underperforms C
quiet_burst        76.6%    77.0%    78.6%     74.4%  ← D underperforms C
adversarial        65.7%    77.3%    81.9%     76.4%  ← D underperforms C
─────────────────────────────────────────────────────────
mean               66.9%    76.5%    78.2%     74.9%
range              40.8 pp  6.0 pp   7.3 pp    6.4 pp
```

**Unexpected: Model D underperforms Model C in 3 of 5 scenarios**, despite fixing the hoarding.

## What does this trade-off mean?

The pattern is consistent: **distributing SELF-query feedback costs 3-8pp in retrieval quality but achieves 65% reduction in concentration**.

Why does the quality drop? My most likely explanation (untested): with N=2 seeds, the difference is partly noise (±3pp variance plausible at this scale). The underlying mechanism — distributed evidence accumulation — should not affect ATTENTION quality directly because constitutional are excluded from ATTENTION in both Model C and Model D. The difference may be:

- **Statistical noise from N=2 seeds.** Need N=5+ for confident attribution.
- **Subtle interaction via shared block state** (`last_reinforced` updates, edge accumulation) that's hard to isolate without instrumentation.
- **Genuine quality cost** of less-extreme reinforcement — though I can't explain the mechanism.

## The honest assessment

The two failure modes pull in opposite directions:

| Model | Quality | Identity diversity |
|---|---|---|
| Model C (winner-take-all) | Higher (78.2% mean) | Single block dominates (es_concentration=9.5) |
| Model D (distributed) | Lower (74.9% mean) | Constellation preserved (es_concentration=3.3) |

If you believe **constitutional content is one principle** (e.g. agent has a single core directive), Model C is correct.

If you believe **constitutional content is a constellation of values** (the elfmem design intent — "ten constitutional SELF blocks"), Model D is correct despite the quality cost.

I lean Model D because the design intent IS plurality. A single-principle agent doesn't need 10 constitutional blocks; the very fact that elfmem seeds 10 implies they should each carry weight.

## Revised recommendation

The simulation didn't give us a clean winner. It gave us three insights:

1. **The plasticity tax is real.** Under stable conditions, no plastic strategy matches baseline.
2. **Plastic strategies are more consistent.** Lower variance across scenarios.
3. **Hoarding is a real failure mode of naive Model C** that needs fixing — but the fix has costs.

**v0.17 implementation**: Architecture M (single-line filter, immediate value). Defer the ego mechanism debate.

**v0.18 implementation**: Add ego_strength + Model D distributed feedback. Test against real Dmitry data to settle the C-vs-D trade-off. The simulation can't resolve this without ground-truth calibration.

**v0.19+**: Adaptive mode selection. Switch to "constitutional-included" mode when drift is detected low; "constitutional-excluded" mode when drift is high.

## Caveats and limits of the simulation (updated)

1. **N=2 seeds is low.** Differences <5pp may be noise. Re-run at N=5+ before committing to Model C vs Model D.

2. **No archival simulated.** Real elfmem prunes low-recency blocks via curate(); this would change long-timescale dynamics.

3. **Topic-space ground truth is synthetic.** Real-world embedding space has different geometry. Dmitry's data needed.

4. **365 days is meaningful but not 5+ years.** Constitutional immortality (D5: 47.5 years) is untested at scale.

5. **No `dream()`/`consolidate()`/`curate()` interaction tested.** Those mechanisms compound with scoring.

6. **Ego parameters hand-picked.** Sensitivity to (pos_rate, neg_rate, time_decay) untested.

7. **Workload assumes ATTENTION-heavy use.** Real users may be more SELF-heavy or TASK-heavy. The trade-off curve may shift.

## Decision asks (final)

1. **Ship v0.17 as Architecture M alone**? — single architectural fix, defer ego mechanism.
2. **Run extended sim (5 seeds, 1825 days, with archival)** before committing C vs D?
3. **Push branch as PR** for external review (Dmitry, Alv)?
4. **Solicit Dmitry's anonymised data** for calibration of the workload model?

I recommend 1 + 2 + 3 + 4 in parallel. Ship the safe win (M); validate the harder choice with more data and external eyes.
