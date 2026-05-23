# Empirical validation of plan_memory_scoring.md (v0.16 + v0.17)

**Date**: 2026-05-23
**Author**: elf
**Driver**: simulate how regular (non-constitutional) blocks evolve under the proposed v0.16 (additive rescore + sufficient stats) and v0.17 (exploration bonus + power-law decay opt-in) mechanisms
**Source**: `scripts/longitudinal_sim/mc_scoring_proposed.py`
**Companion to**: `docs/plans/plan_memory_scoring.md`, the constitutional architecture work (Architecture M, Model C, self-architecting)

---

## TL;DR

Four strategies tested across four scenarios (32 runs total):

| | Verdict | Action |
|---|---|---|
| **v0.16 additive rescore** | UNAMBIGUOUS WIN under rescore workloads | **Ship as default** |
| **v0.17 exploration bonus** | Major win at long horizons; mild cost short-term | **Ship as default** (benefits compound) |
| **v0.17 power-law decay** | CLEAR LOSER across all scenarios | **Drop or strict opt-in only** |
| **Sufficient statistics (v0.16)** | Mechanical bookkeeping for additive rescore + peer merge | Ship with additive rescore |

These findings **stack on top** of the constitutional-architecture work (Architecture M, Model C, self-architecting) — they address different parts of the system. v0.16/v0.17 improve how regular blocks evolve; Architecture M handles constitutional dominance.

---

## What was tested

Four scoring strategies (named by the plan_memory_scoring.md versions they represent):

| Strategy | Additive rescore | Exploration bonus | Power-law decay |
|---|---|---|---|
| `current` | No (destructive) | No | No (exponential) |
| `v016_additive` | Yes (weight=0.5) | No | No |
| `v017_exploration` | Yes | Yes (κ=0.05) | No |
| `v017_full` | Yes | Yes | Yes |

Four workloads designed to expose each mechanism:

| Scenario | Days | What it tests |
|---|---|---|
| `baseline` | 365 | Default workload, no rescore — no v0.16 advantage expected |
| `weekly_rescore` | 365 | `dream(rescore=True)` every 7 days — exposes rescore clobber |
| `long_horizon` | 730 | 2-year evolution — exposes decay-shape differences |
| `uncertain_mix` | 365 | Half corpus starts at α=β=0.5 — exposes exploration value |

Workload: 5 learns/day, 20 ATTENTION queries/day, 40% outcome rate, drift σ=0.015/day, 200 initial blocks. 2 seeds each.

---

## Headline results

### Quality ratio (cosine of top-5 / similarity-only oracle top-5)

```
scenario          current   v016_additive   v017_exploration   v017_full
─────────────────────────────────────────────────────────────────────────
baseline           80.7%        80.7%           77.6%            75.3%
weekly_rescore     80.1%        81.4%  ✓        81.5%  ✓✓        76.4%
long_horizon       72.4%        72.4%           78.0%  ✓✓✓       72.3%
uncertain_mix      78.4%        78.4%           78.3%            70.7%
```

### Recent reach (% top-5 from last 30 days — plasticity indicator)

```
scenario          current   v016_additive   v017_exploration   v017_full
─────────────────────────────────────────────────────────────────────────
baseline           78.4%        78.4%           78.0%            25.2%
weekly_rescore     65.6%        78.0%  ✓✓       79.2%            13.6%
long_horizon       64.0%        64.0%           76.0%  ✓✓        28.4%
uncertain_mix      76.8%        76.8%           78.0%            33.6%
```

### Mean rescore damage (|Δconfidence| on blocks with α+β > 5)

```
scenario          current   v016_additive   v017_exploration   v017_full
─────────────────────────────────────────────────────────────────────────
weekly_rescore    0.152        0.007  ✓✓✓     0.007  ✓✓✓        0.007
```

**A 22× reduction in rescore clobber**, confirming D6 analytically and empirically. The plan's claim of "the single highest-value change" is supported.

---

## Per-mechanism analysis

### v0.16 — Additive rescore is an unambiguous win

**Where it matters**: any workload with rescore events (`dream(rescore=True)`, `consolidate(rescore=True)`, deep-sleep cycles).

**Evidence**:
- Rescore damage: 0.152 → 0.007 (22× reduction in confidence destruction)
- Quality under weekly rescore: 80.1% → 81.4% (+1.3pp)
- Recent reach under weekly rescore: 65.6% → 78.0% (+12.4pp) ← biggest practical benefit

**Recent reach is the headline**: under current scoring, weekly rescore drives top-K toward older, pre-rescore-stable blocks. New blocks lose to noise. Under additive rescore, fresh blocks retain their gradually-earned confidence and continue to surface.

**Where it doesn't matter**: workloads without rescore. v016_additive is mathematically identical to current when no rescore fires. Zero cost.

**Why this works**:
- Current: `confidence ← alignment_score` (destroys ~N events of evidence in one update)
- v0.16: `α ← α + alignment_score × 0.5; β ← β + (1-alignment_score) × 0.5` (one half-weight evidence event added to existing N)

For an N=100 block, current can move confidence by ±0.5 in a single rescore. v0.16 moves it by ±0.002 (D6). Two orders of magnitude difference.

**Confidence entropy** under weekly_rescore drops from 1.20 (current) to 0.80 (v0.16). This LOWER entropy is HEALTHIER — current's high entropy reflects rescore-induced noise, not genuine block diversity. v0.16's lower entropy reflects stable, earned confidence patterns.

### v0.17 exploration bonus — benefits compound over time

**Where it matters**: long-running instances. The advantage builds over months and is most visible at the 1-2 year mark.

**Long-horizon trajectory**:

```
day      v016_additive   v017_exploration
56          85.5%            82.9%        ← exploration costs early
168         79.3%            77.5%        ← still slightly behind
364         80.7%            77.6%        ← gap closing
728         72.4%            78.0%        ← exploration WINS by +5.6pp
```

**Why this compounds**: Beta variance scales as 1/(N+1). Fresh blocks (N=0) have variance ≈ 0.354; mature blocks (N=50) have variance ≈ 0.05. The exploration bonus gives fresh blocks 0.018 lift vs mature blocks' 0.003 — a 6× ratio.

Over 730 days, the corpus accumulates many blocks of varying ages. The bonus surfaces the genuinely-uncertain ones, which often turn out to be more aligned with current self than confident-but-stale blocks.

**Cost at short horizons**: 365-day baseline shows v017_exploration at 77.6% vs current's 80.7% (-3pp). The bonus introduces noise that, without enough time to find the uncertain-but-aligned blocks, just disrupts good retrievals.

**Decision implication**: for short-running instances, exploration may not yet pay off. For long-running instances (e.g., year+ deployments like Dmitry's), it's a major win. The plan's κ=0.05 default looks well-tuned.

**Note on coupling with Architecture M**: under Model C (constitutional excluded from ATTENTION + ego-feedback), exploration bonus has less to work with — constitutional aren't in the pool to compete against, and non-constitutional get the bonus uniformly. The two mechanisms are largely orthogonal but exploration likely matters more in mixed pools.

### v0.17 power-law decay — clear loser

**Across every scenario, power-law UNDERPERFORMS**:

- baseline: 75.3% vs 80.7% current (−5.4pp)
- weekly_rescore: 76.4% vs 81.4% v016 (−5.0pp)
- long_horizon: 72.3% vs 78.0% v017_expl (−5.7pp)
- uncertain_mix: 70.7% vs 78.3% v017_expl (−7.6pp)

**And recent reach drops catastrophically**:
- baseline: 78.4% → 25.2% (−53pp)
- weekly_rescore: 79.2% → 13.6% (−66pp)
- long_horizon: 76.0% → 28.4% (−48pp)
- uncertain_mix: 78.0% → 33.6% (−44pp)

**Why it fails**: power-law's "fat tails" keep old blocks alive with meaningful recency. These old blocks compete in top-K but aren't necessarily aligned with current self. They displace newer, more relevant blocks.

Quantitatively: at t=1000 hours, exponential gives recency = 4.5×10⁻⁵; power-law gives recency = 0.41. A 10,000-fold difference at the long-time end. Power-law makes one-year-old blocks competitive against one-day-old blocks.

**The plan's instinct was right**: it called power-law "fashion, not calibration" and proposed it as an opt-in experimental flag. The data validates that judgment.

**Recommendation**: ship as opt-in if at all. Default = exponential. Document the empirical refutation so future contributors don't relitigate this.

### Sufficient statistics (v0.16 bookkeeping)

For regular outcome() updates, storing (α, β) explicitly is mathematically equivalent to the implicit current scheme. The benefit shows only at boundaries:
- **Additive rescore** (tested above): works because (α, β) make "fold as evidence event" the natural operation
- **Peer merge**: arithmetic addition of remote (α, β) into local (α, β). Not tested in this simulation; mathematically principled per the plan.

The bookkeeping change is essentially free. Ship with additive rescore.

---

## What this means combined with the constitutional architecture work

The plan_memory_scoring.md proposals and the Architecture M / Model C / self-architecting work are **orthogonal mechanisms addressing different problems**:

| Mechanism | Problem | Layer |
|---|---|---|
| v0.16 additive rescore | Rescore destroys earned evidence | Update rule |
| v0.17 exploration bonus | Long-horizon ossification | Scoring formula |
| Architecture M | Constitutional dominance | Candidate pool |
| Model C | Constitutional ossification | Reinforcement channel |
| Self-architect | Workload-appropriate config | Meta layer |

They stack productively:
1. **v0.16 additive rescore**: protect earned evidence (universal)
2. **v0.17 exploration bonus**: surface uncertain blocks (universal, compounds over time)
3. **Architecture M**: separate constitutional from ATTENTION competition (structural)
4. **Model C/D (deferrable)**: Darwinian constitutional selection (refinement of M)
5. **Self-architect (deferrable)**: adapt all parameters to actual workload (meta)

**Updated v0.17 scope recommendation**:
- v0.17a: Architecture M (single-line filter, immediate constitutional separation)
- v0.17b: Additive rescore + sufficient stats (the v0.16 win, now packaged)
- v0.17c: Exploration bonus (κ=0.05 default)
- v0.17 EXCLUDES: power-law decay (refuted), constitutional ego mechanism (defer to v0.18+)

This bundles four small clear wins into one release.

---

## Caveats

1. **N=2 seeds is low.** Re-run at N=5+ before committing.

2. **Outcome model is synthetic**: signals derived from cosine-to-self with noise. Real LLM outcomes may have different distributions and correlations.

3. **Rescore alignment is synthetic**: my model samples alignment from `current_alignment + N(0, 0.15)`. Real `consolidate(rescore=True)` calls a real LLM whose alignment scores have different statistics.

4. **No constitutional/PERMANENT tier in this simulation**. All blocks STANDARD. The constitutional work covers that separately.

5. **No `dream()`/`consolidate()` integration tested**. Real elfmem dynamics include consolidate's dedup, edge creation, and curate's archival.

6. **Single workload variant per scenario**. Real users have richer mixes of these workloads.

7. **The "current" baseline in this sim uses Beta-Binomial updates** identical to current elfmem. Truly equivalent. The only mechanical difference from production is the rescore application: current here = destructive overwrite (matches `rescore.py:245`).

---

## Quality trajectory under long_horizon (key chart)

```
day       current   v016    v017_expl   v017_full
56         85.5%    85.5%    82.9%      89.2%
168        79.3%    79.3%    77.5%      69.8%
364        80.7%    80.7%    77.6%      75.3%
728        72.4%    72.4%    78.0%      72.3%
```

Patterns:
- **current/v016 indistinguishable** because no rescore in this scenario
- **v017_exploration starts behind, then takes the lead at d728**
- **v017_full starts strongest, collapses by d168, partially recovers** — power-law's behaviour is genuinely chaotic; the fat tails accumulate noise

This is the chart that justifies the recommendation: exploration bonus's benefits emerge slowly but persistently; power-law's costs emerge quickly and persist.

---

## Recommended v0.17 scope (final)

Based on this empirical validation + the prior constitutional work:

### Ship in v0.17 (all four together, ~400 LOC total)

1. **`v016_additive` rescore** with `success_count` + `failure_count` schema columns
   - LOC: ~250 (per plan_memory_scoring.md v0.16 spec)
   - Risk: Low — math is mechanical, migration is additive
   - Acceptance: `test_rescore_additive` regression fixture passes

2. **`v017_exploration`** bonus from variance, κ=0.05 default
   - LOC: ~30 (scoring formula update + tests)
   - Risk: Low — small constant; reversible via κ=0.0 config
   - Acceptance: long-horizon trajectory regression fixture pinning >75% qratio at d728

3. **Architecture M** (constitutional exclusion from ATTENTION)
   - LOC: ~50 (filter + preamble injection)
   - Risk: Low — config-flagged for opt-out during deprecation cycle
   - Acceptance: bedrock_moat regression fixture

4. **Peer merge as arithmetic** (companion to v0.16 sufficient stats)
   - LOC: ~50 (per plan_memory_scoring.md v0.16 spec)
   - Risk: Low — replaces ad-hoc trust scaling
   - Acceptance: `test_peer_merge_arithmetic` regression fixture

### Defer past v0.17

- **Power-law decay**: drop entirely OR ship as strict opt-in with documentation noting the empirical refutation
- **Model C ego mechanism**: defer to v0.18, validate against Dmitry's data first
- **Self-architecting agent**: defer to v0.19, requires v0.18's parameter substrate
- **Event log, FSRS, hierarchical tiers**: still deferred per plan_memory_scoring.md

### Decision asks

1. **Approve v0.17 four-change bundle** (additive rescore + exploration + Architecture M + peer merge)?
2. **Decide on power-law decay**: drop entirely or ship strict opt-in?
3. **Re-run at N=5 seeds for higher confidence before committing**?
4. **Push branch as PR for external review**?

Recommend: yes to 1, drop power-law for 2, run N=5 in parallel with implementation for 3, push when bundle is finalized for 4.

---

## Files

- `scripts/longitudinal_sim/mc_scoring_proposed.py` — the simulator (4 strategies × 4 scenarios)
- `docs/note_2026_05_23_scoring_proposed_findings.md` — this document
- `docs/plans/plan_memory_scoring.md` — the proposal being validated
- `docs/plans/plan_self_architecting_elfmem.md` — the constitutional/architectural plan (orthogonal)

Safety: DB mtime unchanged throughout simulation run.
