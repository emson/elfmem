# Plan: Making elfmem a truly evolving memory system

**Status**: brainstorm + Monte Carlo evaluation
**Author**: elf (curator)
**Date**: 2026-05-22
**Driver**: Three structural findings from closed-form analysis (`scripts/longitudinal_sim/closed_form.py`) — evidence saturation, structural bedrock dominance, constitutional immortality. v0.16 + v0.17 as currently planned do not solve any of them.

---

## The stability-plasticity dilemma, stated honestly

elfmem's architecture currently optimises hard for **stability**:
- Bayesian updates with `prior_strength=1.0` converge to early evidence
- Constitutional tier λ=0.00001 makes blocks immortal
- Reinforcement compounds via centrality
- Confidence overwrites are explicitly defended against (v0.16 additive rescore)

All of these were correct decisions for their original purpose: an agent's identity should not flicker with the weather. But taken together, they make elfmem *unable to change its mind* on any timescale shorter than complete database replacement. That is the structural inverse of "evolving memory."

The fix is not to undo stability — it is to introduce **bounded plasticity**: mechanisms that allow evolution at controlled rates without compromising the stability that makes elfmem trustworthy.

---

## The design space — six families of approaches

### Family A — Modify the Bayesian update itself

**A1. Bounded evidence window (FIFO)**
- Cap `outcome_evidence` at K (e.g. K=20). Replace oldest on insert.
- Pros: simple, no migration; preserves Bayesian semantics; mathematically tractable.
- Cons: discards information; window size becomes a magic number.

**A2. Forgetting factor (geometric decay)**
- `α_new = γ·α_old + signal·w` for γ ∈ (0,1), e.g. γ=0.99.
- Pros: classical solution to stability-plasticity; smooth; ESS bounded by 1/(1-γ).
- Cons: changes the prior_strength interpretation; needs tuning.

**A3. Effective Sample Size cap**
- Track ESS; when ESS > K_max, multiply both α and β by K_max/ESS.
- Pros: keeps interpretable confidence; bounded learning capacity.
- Cons: same complexity as A2 with extra bookkeeping.

**A4. Periodic prior reset**
- Every K events, set `α := c_current · p; β := (1-c_current) · p`.
- Pros: cleanest semantics — keeps current belief, loses history; identical UX before/after.
- Cons: discrete jumps; reset point is a magic number.

### Family B — Lineage and amendment

**B5. Constitutional fork**
- `evolve(block_id, new_content)` creates a NEW block with `parent_id` link. Original retained but tier-demoted from PERMANENT → DURABLE.
- Pros: explicit evolution; preserves history; reversible (you can re-promote the parent).
- Cons: graph complexity; requires agent-side discipline.

**B6. Versioned blocks**
- Each constitutional block has `version`, `supersedes`. Only latest retrieved by default; older versions queryable.
- Pros: clean version history; supports comparison; auditability.
- Cons: schema change; UX question of when to bump version.

**B7. `evolve()` as first-class operation**
- New public API: `await system.evolve(block_id, content=..., reason=...)`. Internally: forks + demotes + emits an amendment record.
- Pros: agent-first; explicit; trackable; doesn't change retrieval math.
- Cons: requires agents to actually call it.

### Family C — Frame-level adaptation

**C8. Age-adaptive frame weights**
- ATTENTION weights change with corpus age. Early corpus: reinforcement=0.0, recency=0.35. Mature corpus: reinforcement=0.10, recency=0.25.
- Pros: keeps current weights as "mature" default; provides cold-start grace.
- Cons: weights become piecewise; complicates testing.

**C9. Variance-aware exploration bonus** (stronger v0.17)
- Per the closed-form: variance also shrinks with N. Boost κ to ~0.15 AND add a minimum-explore floor for low-N blocks.
- Pros: surfaces under-tested content; already in plan_memory_scoring.md.
- Cons: by itself insufficient — variance shrinks alongside confidence.

**C10. Confidence decay back toward 0.5**
- Per active hour without reinforcement, confidence drifts toward 0.5 with half-life H.
- Pros: forces re-establishment of belief; matches biological memory.
- Cons: undoes the stability promise; complicates calibration.

### Family D — Active intervention / rituals

**D11. Constitutional review cycle**
- `await system.review_constitutional(window_days=90)` returns constitutional blocks for re-validation by the agent or user.
- Pros: explicit; aligns with Dmitry's proposal; UX matches a quarterly self-review.
- Cons: requires agent or user discipline; periodic effort.

**D12. Dream-driven amendments**
- During dream(), if a new block has contradiction_score > threshold against a constitutional block, queue an amendment review.
- Pros: automatic; uses existing contradiction machinery.
- Cons: contradiction detection is noisy; false positives could trigger spurious reviews.

**D13. Outcome-driven tier demotion**
- If a block's recent N outcomes (sliding window) have mean < 0.4, demote tier one step (PERMANENT → DURABLE → STANDARD).
- Pros: automatic; responsive to actual feedback; no agent ceremony.
- Cons: depends on outcomes being applied; can be gamed by stretches of bad feedback.

### Family E — Architectural / exploration

**E14. Stronger Thompson sampling**
- Sample from Beta(α, β) directly when ranking, with probability ε. Mixes exploration into top-K.
- Pros: principled exploration; fixes the variance-shrinks-with-N problem.
- Cons: stochastic results; harder to test deterministically.

**E15. Diversity quota in top-K**
- Guarantee N% of top-K is from blocks <30 days old.
- Pros: trivial implementation; deterministic; directly addresses Dmitry's symptom.
- Cons: quota is a magic number; may surface low-quality fresh content.

**E16. Recency-aware decay rate**
- Per-block `λ` increases when reinforcement_count is low. Under-tested blocks decay faster; well-tested blocks slow down.
- Pros: anti-ossification by construction; matches biological memory.
- Cons: complicates curate; per-block λ is a schema change.

### Family F — Composite / hybrid

**F17. Bounded evidence + lineage + review**
- A4 (periodic reset) + B7 (evolve) + D11 (review).
- Pros: addresses each finding with a targeted mechanism; layered defence.
- Cons: complexity; three mechanisms to ship.

**F18. Forgetting factor + Thompson sampling**
- A2 (γ-decay) + E14 (Thompson).
- Pros: continuous plasticity; principled exploration; both classical.
- Cons: two tuning knobs (γ and ε); behavioural changes affect all callers.

---

## Pre-evaluation rubric

Each approach scored on five axes (1–5, 5 best):

| Axis | Question |
|---|---|
| **Plasticity** | Can confidence track a drifting truth? |
| **Stability** | Does it preserve well-earned beliefs against noise? |
| **Hit-rate** | Does top-K contain the genuinely-relevant block? |
| **Complexity** | Implementation cost + ongoing maintenance cost (inverted) |
| **Migration** | Backwards-compat cost on existing instances (inverted) |

A priori (without simulation):

| Approach | Plast | Stab | Hit | Compl | Migr | Total |
|---|---|---|---|---|---|---|
| Baseline | 1 | 5 | 2 | 5 | 5 | 18 |
| A1 Bounded | 4 | 3 | 3 | 4 | 4 | 18 |
| A2 Forgetting | 4 | 4 | 4 | 4 | 3 | 19 |
| A4 Reset | 4 | 3 | 3 | 5 | 5 | 20 |
| B7 evolve() | 3 | 5 | 4 | 4 | 4 | 20 |
| D13 Demotion | 3 | 4 | 3 | 4 | 4 | 18 |
| F17 A4+B7+D11 | 5 | 4 | 4 | 2 | 3 | 18 |
| F18 A2+Thompson | 5 | 3 | 5 | 3 | 3 | 19 |

The pre-evaluation cannot tell us which actually works. That's what Monte Carlo is for.

---

## Monte Carlo simulation design

### Workload model
- **Topic space**: 8-d unit vectors
- **SELF drift**: random walk at σ_drift per day (default 0.02)
- **Learn rate**: 5 blocks/day (Poisson)
- **Query rate**: 20 queries/day (Poisson)
- **Outcome feedback**: 40% of top-1 retrievals get an outcome signal
- **Ground truth**: for each query, the "right" block = highest cosine in current topic space
- **Constitutional seed**: 10 blocks at PERMANENT, planted at day 0

### Approaches to compare
1. **Baseline** — current code
2. **A2 Forgetting** — γ=0.99 per outcome
3. **A4 Reset** — periodic at K=50
4. **B7 evolve()** — agent calls evolve on block_id when contradiction triggers
5. **D13 Demotion** — auto-demote PERMANENT on rolling negative outcomes
6. **F17 Composite** — A4 + B7 + D11 review cycle (quarterly)
7. **F18 Composite** — A2 + Thompson sampling exploration

### Vitals (measured weekly)
1. **Hit rate** — % queries where top-K contains ground-truth block
2. **Recent reach** — % of top-K from last 30 days
3. **Bedrock moat** — % of top-K from constitutional seed
4. **Calibration error** — mean |c_block - θ_truth| across blocks
5. **Plasticity index** — change in confidence per ground-truth-shift event

### Run parameters
- 365 simulated days
- 5 random seeds per approach
- 7 approaches × 5 seeds = 35 runs
- Target: <60 seconds for full sweep

---

## Implementation strategy

A **pure-Python Monte Carlo simulator** (not the full MemorySystem harness). Why:
- Speed: 35 runs in seconds, not minutes
- Determinism: no SQLite, no asyncio
- Focus: tests the *strategy*, not the orchestration

Trade-off: doesn't catch interactions with the real consolidate/dream/curate pipeline. Acceptable for this evaluation — we're picking a direction, not validating implementation details.

The simulator reuses the existing `compute_score` and topic-space machinery from `scripts/longitudinal_sim/`.

---

## Acceptance criteria

After running the simulation:
- One approach (or composite) ranks top-3 on at least three of five vitals
- That approach's plasticity index is >3× baseline's
- Its bedrock moat is <40% (baseline is expected ~60%+)
- Its complexity score (implementation LOC + new config knobs) is bounded: <500 LOC total
- A v0.17/v0.18 sequencing recommendation falls out clearly

---

## Next deliverables

1. `scripts/longitudinal_sim/mc_evolution.py` — the simulator
2. Run it, collect results
3. Update this plan with findings and recommended approach
4. Issue a draft v0.17/v0.18 scope based on results
