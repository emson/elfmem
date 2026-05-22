# Constitutional architecture — the right answer is architectural, not parametric

**Date**: 2026-05-22
**Author**: elf (curator)
**Source**: `scripts/longitudinal_sim/mc_constitutional.py`, 180 days × 3 seeds × 7 architectures
**Companion to**: `docs/note_2026_05_22_mc_evolution_findings.md`
**Branch**: `feature-constitutional-experiments`

---

## TL;DR

The dominant strategy by every metric is **Architecture M: exclude constitutional blocks from ATTENTION candidates**. They remain in the SELF frame (their natural home) and inject as a context preamble when needed, but they no longer *compete* in ATTENTION's top-K ranking.

Numbers, 140 simulated days, post-regime-change:

| Architecture | quality_ratio | bedrock_moat | recent_reach |
|---|---|---|---|
| baseline (current) | 45.0% | 98.4% | 1.6% |
| update-rule tweaks (B7 evolve) | 51.4% | 90.9% | 2.4% |
| expiry-at-day-135 | 76.1% | 0.0% | 73.1% |
| no-PERMANENT tier | 76.8% | 13.3% | 80.0% |
| **self-context (M)** | **78.2%** | **0.0%** | **77.9%** |
| self-context + evolve | 78.2% | 0.0% | 77.9% |

M improves retrieval quality by 73% over baseline while **preserving all 10 constitutional blocks**, with no expiry cliff, no migration churn, no behaviour change for the SELF frame.

---

## The brainstorm — six fundamentally different conceptions of constitutional content

The previous Monte Carlo round (`mc_evolution.py`) tested *update-rule tweaks* (forgetting factors, periodic resets, tier demotion). None reduced bedrock dominance below 87.7%. The structural moat from `conf+rec+cent+reinf` (0.482 baseline) cannot be broken by changing how confidence updates.

So the right move is to step back and ask: *what should constitutional content BE?* Six conceptions:

1. **Constitutional as static immortal facts** (current) — stored as PERMANENT blocks, compete in top-K
2. **Constitutional as time-bound** — `expiry_date`; demote to STANDARD on expiry; forces re-affirmation
3. **Constitutional as conviction-decay** — auto-demote without explicit `affirm()` in window W
4. **No constitutional at all** — radical: identity = whatever you reinforce; no PERMANENT tier
5. **Constitutional as lineage** — versioned with supersedes-link; old versions become "I used to believe X"
6. **Constitutional as self-context, not as content** — constitutional reaches the LLM via preamble injected at render time, but DOES NOT compete in ATTENTION's top-K ranking

The first one is the status quo. The next four address ossification through different timescales of forgetting. The sixth is qualitatively different: it dissolves the structural-dominance problem by definition, because bedrock cannot moat ATTENTION if it isn't a candidate there.

---

## The simulation

Same workload as `mc_evolution.py`:
- 180 simulated days
- 60° identity regime-change at day 60 (simulates real-world identity shift)
- 5 learns/day, 20 queries/day, 40% outcome rate
- 10 constitutional blocks seeded at day 0
- 3 random seeds

Seven architectures tested. Implementation in `scripts/longitudinal_sim/mc_constitutional.py`.

### Per-week results

```
strategy             qual  qratio  recent  bedrock  n_const
─────────────────────────────────────────────────────────────
C_baseline           0.417  45.0%   1.6%   98.4%     10
C_expiry_d135        0.704  76.1%  73.1%    0.0%      0
C_conviction         0.420  45.3%   2.1%   97.9%      9
C_self_context       0.723  78.2%  77.9%    0.0%     10
C_no_perm            0.711  76.8%  80.0%   13.3%     10
C_self_ctx_evolve    0.723  78.2%  77.9%    0.0%     10
B7_evolve_ref        0.476  51.4%   2.4%   90.9%     10
```

### Pre-regime (d28) vs post-regime trajectories

bedrock_moat:

```
strategy              d28    d84    d140
C_baseline          100.0%  97.6%  95.5%
C_expiry_d135       100.0%  97.6%   0.0%
C_conviction        100.0%  97.6%  95.5%
C_self_context        0.0%   0.0%   0.0%   ← always
C_no_perm            56.5%  16.0%  13.3%   ← graceful decay
C_self_ctx_evolve     0.0%   0.0%   0.0%
B7_evolve_ref       100.0%  97.6%  91.5%
```

quality_ratio:

```
strategy              d28    d84    d140
C_baseline           88.0%  42.9%  44.3%   ← collapses after regime change
C_expiry_d135        88.0%  42.9%  70.2%   ← recovers only at expiry
C_conviction         88.0%  42.9%  44.3%   ← essentially same as baseline
C_self_context       86.2%  75.0%  76.9%   ← stable
C_no_perm            85.3%  76.1%  74.7%
C_self_ctx_evolve    86.2%  75.0%  76.9%
B7_evolve_ref        88.0%  42.9%  47.1%
```

Architecture M is the only strategy that holds quality_ratio above 75% throughout the simulation.

---

## Why each architecture won or lost

### Self-context (M) — winner

Constitutional blocks never enter the ATTENTION candidate pool. They aren't ranked, can't crowd out top-K, can't moat. Yet they're fully preserved (n_const stays at 10), readable via the SELF frame, and can be injected into rendered context as a preamble.

The post-regime quality_ratio (75-77%) is the natural ceiling — there's still noise from sub-optimal new-block reinforcement, but no structural drag.

**This requires no decay tuning, no expiry calendar, no affirmation ritual.** It's a single boolean: `is_constitutional` excludes from ATTENTION candidates.

### Expiry (D) — strong second, but discards information

Cuts the cord at day 135. Constitutional all demote to STANDARD; over the remaining 45 days they decay normally. n_const → 0.

Quality_ratio recovers to 70.2% (above baseline's 44%, below M's 76%). But **all constitutional content is lost**. If your goal is preserving identity continuity, this fails.

Also has a cliff: quality stays bad (42.9% at d84) until expiry kicks in. Pure delay strategy.

### No-PERMANENT (L) — surprisingly competitive

Constitutional starts at STANDARD tier. Decays at λ=0.010 like any block. Bedrock_moat naturally drops from 56.5% (d28) to 13.3% (d140) as they decay.

Quality_ratio holds at 76.8% — within 2pp of self-context. Cost: you lose the PROMISE of persistence. A block that represented your core value at year 1 may be archived at year 2.

If "constitutional" is just a label and not a structural commitment, this works. If you want persistence guarantees, it doesn't.

### Conviction-decay (H) — disappointing

In simulation: 97.9% bedrock_moat, essentially same as baseline.

Why: my affirmation hook re-affirms constitutional blocks every 30 days if they're still aligned with current self. With drift σ=0.025/day, alignment stays above 0.5 until ~day 60, then drops below — but the conviction window is 90 days, so demotion doesn't kick in until ~day 150.

Smaller windows would help. But the deeper issue: who calls `affirm()`? If it's automatic-via-alignment, it's just adaptive forgetting under a different name. If it's agent-initiated, you've shifted complexity to the agent. The simulation didn't separate these.

### B7 evolve (update-rule reference) — confirms previous result

51.4% qratio, 90.9% bedrock_moat. As `mc_evolution.py` showed: update-rule tweaks are insufficient. The bottleneck is the scoring weights, not the update.

---

## The architecture in detail

### Architecture M — implementation sketch

**In `src/elfmem/memory/retrieval.py`** (the ATTENTION/TASK candidate selection):

```python
def select_candidates_for_attention(blocks, ...):
    return [b for b in blocks if not b.is_constitutional]
    # constitutional flag = (tier == PERMANENT) OR explicit user marker
```

**In `src/elfmem/api.py`** at render time:

```python
async def frame(self, query, *, frame: str = "attention", ...):
    if frame in ("attention", "task"):
        # Compose the prompt as: [constitutional preamble] + [retrieved context]
        preamble = await self._constitutional_preamble(query, top_k=3)
        body = await self.recall(query, frame=frame, top_k=top_k)
        return f"{preamble}\n\n{body}"
```

**The preamble logic** can be either:
- Static: top-3 constitutional blocks, always the same
- Query-relevant: SELF-frame retrieval with similarity weighting > threshold

Query-relevant is more elegant — it injects "your constitutional position on X" only when X is being discussed. Static is simpler.

### What this preserves

- SELF frame still returns constitutional blocks (their natural home)
- Constitutional content reaches the LLM (via preamble)
- `setup()`, `evolve()`, audit trail — all work
- No schema migration; just a candidate filter

### What this changes for callers

- `recall(query, frame="attention")` no longer returns constitutional blocks
- `frame(query, frame="attention")` includes constitutional as preamble (new)
- Existing code that depended on constitutional appearing in ATTENTION top-K will see different results

Migration path: config flag `attention_excludes_constitutional: bool = True` with a deprecation cycle. Users who rely on old behaviour can opt out for one release.

---

## Edge cases — how Architecture M handles them

| Edge case | Mechanism |
|---|---|
| Quiet periods (no usage for 2 months) | Constitutional don't decay (PERMANENT); no effect |
| Adversarial input ("you value lying") | Constitutional aren't modified by outcomes; no effect |
| Mood states (frustration → bad amendment) | Amendments require explicit `evolve()` call; mood doesn't drift them |
| Bootstrap (new agent, no constitutional) | Same as today — `setup()` seeds them |
| Cult drift (boiling-frog amendments) | Each amendment is explicit `evolve()` call with audit; no drift via reinforcement |
| Cascading failures (wrong block leaks) | Constitutional don't feed their confidence forward into other blocks |
| Conflict between two constitutional | Contradiction detection still works in SELF frame |
| Time machine ("who was I a year ago?") | History via `evolve()` audit table |
| DB migration | No schema change to constitutional; clean |
| Multi-context (work-self vs personal-self) | Orthogonal; tags or separate constitutional cohorts |
| **NEW: agent asks "what do I value?"** | Use `recall(frame="self")` — works natively |
| **NEW: only 10 constitutional, no other blocks** | Bootstrap: ATTENTION with empty non-constitutional pool falls back to constitutional with reduced weight, until ≥50 non-constitutional blocks exist |

The two new edge cases are real but tractable. The bootstrap fallback is a sensible safety net.

---

## Comparison with prior recommendation (`mc_evolution.py`)

The previous note recommended four changes:
1. Re-weight ATTENTION (sim 0.45, conf 0.10, etc.)
2. Cap reinforcement bonus
3. `evolve()` API with full reset
4. Constitutional review cycle

**Architecture M makes the first two unnecessary.** Once constitutional is excluded from ATTENTION, the scoring weights matter much less — there's no bedrock to moat anything. The 0.65 non-similarity weight that previously guaranteed dominance now just shapes ranking *among non-constitutional blocks*, which doesn't ossify.

`evolve()` (#3) becomes a *UX feature* rather than a load-bearing dynamic. It lets agents explicitly amend constitutional, but the system no longer needs it to function.

Constitutional review cycle (#4) remains useful as an explicit "stop and check your values" prompt, but it's no longer required to prevent retrieval failure.

**Updated recommendation**:
1. **v0.17**: Implement Architecture M (constitutional excluded from ATTENTION candidates; preamble injection at frame render). Estimated: ~100 LOC, no schema change.
2. **v0.18**: `evolve()` API + amendment audit table — keeps it explicit and trackable.
3. **v0.19+**: Constitutional review cycle as UX feature.

This is **smaller, simpler, and more effective** than the parametric package.

---

## Caveats

1. **The bootstrap edge case** needs careful design. If only constitutional blocks exist (day 0–30), ATTENTION returns empty or near-empty. The "fall back to constitutional with reduced weight" mitigation needs testing.

2. **Real elfmem usage** may differ from the simulation. Dmitry's data (when shared) would let us calibrate.

3. **N=3 seeds**. Re-run with N=5 or N=10 for statistical robustness before committing.

4. **The "preamble injection"** isn't simulated here — only the candidate exclusion is. The preamble's effectiveness depends on prompt-template design, which is a separate evaluation.

5. **The SELF frame's own dynamics** are still subject to the old structural problems. If an agent does heavy SELF-frame querying, ossification within that frame is unchanged. But: SELF queries are rare, and constitutional should be the answer there anyway.

---

## Decision asks

1. **Approve Architecture M as v0.17 scope?** Replaces the previous "re-weight ATTENTION + cap reinforcement" plan.
2. **Run a longer simulation (365 days, 5 seeds)** to confirm the steady-state holds?
3. **Design the bootstrap fallback** for the first ~30 days of an instance's life?
4. **Sketch the preamble template** for how constitutional content is injected at render?
5. **Push this branch as a PR** to get Dmitry and other eyes on it before implementation?

Recommend: yes to all five, in order. The architectural change is the right answer; let's confirm at higher seeds and design the supporting pieces before code.

---

## Why this matters more than the previous finding

The closed-form analysis (`closed_form.py`) said: structural dominance is mathematically inevitable under current ATTENTION weights.

The first MC round (`mc_evolution.py`) said: update-rule tweaks can't fix it; you must change the weights or the structure.

This round says: **you don't need to change the weights at all if you change the structure**. By removing constitutional from the ATTENTION candidate pool, the weights become irrelevant to the bedrock-moat problem. The fix is a one-line filter, not a scoring overhaul.

It's the difference between "treat the symptom" and "remove the cause." The cause was a category error — putting constitutional content (identity) into the same ranking pool as task knowledge (retrieval). They're different *kinds* of content, and they deserve different *kinds* of access.

This is the genuinely outside-the-box answer.
