# Plan: Memory Scoring Architecture — v0.15.x through v0.18.x

**Status**: plan — ready for review and approval
**Driver**: [issue #50](https://github.com/emson/elfmem/issues/50) + the architectural inconsistencies surfaced during analysis
**Synthesises**: `docs/research/memory_scoring_survey.md` (research paper, 7500 words) + reports from a 3-agent review team (Consistency · Research · Critic)
**Author**: elf, after orchestrating the team

---

## Executive summary

The research paper proposes a sweeping architectural overhaul (3 new schema columns, 1 new table, 5-channel composite scoring, FSRS-5 stability, Zettelkasten auto-linking, hierarchical compression — ~500 LOC across 4 minor versions). A 3-agent review (Consistency, Research, Critic) converged on a much sharper conclusion:

**Most of the proposal is correct in direction but premature in detail.** Only one architectural change is genuinely load-bearing today: making rescore additive over Bayesian sufficient statistics. Everything else — event log, FSRS mechanics, power-law retrievability, hierarchical tiers, Zettelkasten auto-linking — needs empirical evidence elfmem doesn't yet have.

Crucially, **Dmitry's actual symptom is a retrieval-side problem** (centrality cold-start gap), not a storage-side one. The minimum fix for his report is three lines in `scoring.py` — not a multi-version architectural overhaul.

This plan therefore proposes:

| Version | Scope | Effort | Risk |
|---|---|---|---|
| **v0.15.3** | Centrality cold-start floor — Dmitry's actual fix | 1 day, ~10 LOC | very low |
| **v0.16.0** | Sufficient statistics `(α, β)` + additive rescore + peer merge | 1 week, ~250 LOC | low |
| **v0.17.0** | Exploration bonus from variance; power-law decay as opt-in experiment | 3 days, ~80 LOC | medium (experimental flag) |
| **v0.18+** | DEFERRED — event log, FSRS mechanics, hierarchical tiers, Zettelkasten auto-link | — | speculative until empirical case |

Total committed scope: **~3 weeks across 3 minor versions, ~340 LOC** — versus the paper's 8 weeks and ~2000 LOC. Most of the saved scope is genuine work-not-done, not work-rushed.

---

## What the agent team found

The full agent reports are summarised here; they should be read in full when reviewing this plan.

### Consistency agent — alignment with elfmem principles

- **Keep**: additive rescore, sufficient-statistics storage, three-channel conceptual decomposition, power-law for retrievability *eventually*.
- **Reject**: the new `block_events` table (showpiece — `block_outcomes` already exists for the only event type that matters), FSRS-5's 19-parameter stability machinery (violates "no magic numbers" by an order of magnitude), the rename `confidence → utility` (elfmem's brand-term used across `outcome()`, `consolidate()`, MCP, AgentGuide, CLI — keep our terminology), Difficulty as a separate channel (only exists so FSRS updates compile).
- **Single highest-leverage change**: materialise `(success_count, failure_count)` and make rescore additive. ~200 LOC. Dissolves 3 named bugs.

### Research agent — evidence grading

- **A-grade (billion-scale validation)**: power-law decay for human memory; multi-signal retrieval generally.
- **B-grade (strong recent cross-system support)**: Zettelkasten linking (A-MEM), per-item adaptive forgetting, hierarchical compression, three-signal scoring.
- **C-grade (theoretical + few adopters)**: Beta-Binomial sufficient stats for agent memory (extrapolation from BKT — strong analogy but new domain), event log for memory scoring (ESAA shows low overhead but no scoring system has shipped it yet).
- **D-grade (speculative)**: Thompson-style exploration bonus κ for agent retrieval (untuned), `w_llm = 1.0` calibration (LLM judges shown systematically biased), FSRS-5 parameters for non-flashcard data.
- **Missed from paper**: KAR³L + LECTOR (content-aware FSRS for embeddings — more relevant to elfmem than vanilla FSRS-5), ByteRover 2.0 (92.2 LoCoMo), Mem0 token-efficient algorithm, MemoryAgentBench (better benchmark than LoCoMo for update/conflict-resolution), outcome-based exploration paper (more apt than Thompson sampling for LLMs).
- **Recommendation**: adopt only A/B-graded items; ship C-graded items behind feature flags with empirical validation; defer D-graded items until tuning data exists.

### Critic agent — cut to the core

- **Dmitry's literal fix**: 3-line centrality cold-start floor in `scoring.py`. Direct attack on the term that empirically dominates the gap.
- **Load-bearing**: only additive rescore via sufficient stats. Everything else is elective.
- **Cargo-cult / showpiece**: FSRS-5 mechanics ("fashion, not calibration"), power-law retrievability as default ("paying complexity tax for an unmeasured benefit"), hierarchical abstract tier ("pure MemoryOS imitation"), Zettelkasten auto-linking (`connect()` already exists; auto-linking introduces failure modes), event log table (replay is a research affordance, not a user affordance).
- **Irreducible architecture**: two columns (`success_count`, `failure_count`), one scoring tweak (centrality floor), one additive update rule (rescore). ~150 LOC. Ships in v0.16.0. Fixes Dmitry. Fixes rescore clobber. Enables principled peer merge.
- **Minority view**: if elfmem's "biological memory" claim is the product moat, then shipping the cognitive-science-grounded architecture IS the product — and trimming it makes elfmem indistinguishable from "vector store with confidence column." But: one maintainer, 8 weeks, four minor versions — the half-life of architectural ambition in solo OSS is about one release cycle. Ship minimum, measure, then earn each layer.

### My synthesis as elf

The three agents agree more than they disagree. Where they diverge, the disagreements are about pace and ambition, not direction. **All three accept the same destination; they differ on how quickly to get there.**

The right answer is the Critic's pace with the Research agent's evidence-grading discipline and the Consistency agent's vocabulary preservation. That gives us:

- **Ship the actual fix for Dmitry's symptom this week.** Centrality floor. 3 lines.
- **Ship the load-bearing architectural change next.** Sufficient statistics + additive rescore. ~250 LOC.
- **Ship the high-value low-risk follow-ups** once the substrate exists. Exploration bonus. Power-law as experiment.
- **Defer everything speculative.** Event log, FSRS mechanics, Zettelkasten auto-linking, hierarchical abstract tier. Each waits for an empirical case.

Crucially: **keep elfmem's existing vocabulary** (`confidence`, `alignment_score`, `outcome_evidence`, `decay_lambda`). The semantic clarification is in what these mean and how they update, not in renaming them. Park's "importance" terminology is not non-negotiable; ours works.

---

## Post-ship verification of v0.15.3 (2026-05-18)

After v0.15.3 shipped (commit `99a5779`), we re-ran the cold-start scenarios against the **real** scoring path (`compute_score` + `effective_centrality` + `ATTENTION_WEIGHTS`) rather than the simulation's local copy. See `scripts/verify_v0_15_3_scoring.py` and the regression fixture `tests/test_scoring.py::TestColdStartGapRegression`.

**Finding: the simulation used the wrong ATTENTION weights.** Production weights are `(sim=0.35, conf=0.15, rec=0.25, cent=0.15, reinf=0.10)`. The simulation used `(0.60, 0.15, 0.10, 0.15, 0.00)`. Two channels were materially mis-weighted:

| Channel | Sim weight | Real weight | Effect |
|---|---|---|---|
| recency | 0.10 | 0.25 | Fresh blocks already get 2.5× more lift in production than the sim modelled |
| reinforcement | 0.00 | 0.10 | Bedrock with reinforcement=1.0 has a 0.10 ranking moat the sim ignored |

**Consequence: v0.15.3 only fully closes the cold-start gap when competing bedrock is unreinforced.**

Concrete numbers from the regression fixture (tight similarity gap, new sim=0.74 vs bedrock sim=0.62):

| Bedrock state | Margin pre-floor | Margin post-floor | Outcome |
|---|---|---|---|
| reinforcement=0 (e.g. integration-test setup) | −0.048 | **+0.027** | new block surfaces ✓ |
| reinforcement=1 (typical for constitutional/aged content) | −0.148 | **−0.073** | bedrock still wins ✗ |

The floor always closes exactly 0.075 of weighted gap (`centrality_weight × floor_strength = 0.15 × 0.50`). Whether that's enough depends entirely on whether competing bedrock has accumulated reinforcement.

**Implication for v0.16 / v0.17 sequencing:**
- The architectural case for v0.16 (sufficient stats + additive rescore + peer merge) is **unchanged** — those changes solve rescore-clobber and peer merge, which are independent of the cold-start retrieval question.
- The exploration bonus from v0.17 may need to move earlier if Dmitry's affected blocks are competing against reinforced bedrock — it is the natural counterweight to a reinforcement moat, and the v0.16 sufficient-stats substrate is what makes its variance term computable.
- The pre-v0.16 integration test (`test_cold_start_centrality.py`) passes because it uses unreinforced bedrock. It is not a sufficient witness on its own; the new regression fixture pins both regimes.

**Open question for Dmitry (issue #50 follow-up):** does his cold-start symptom persist on v0.15.3 specifically when the competing bedrock blocks have accumulated reinforcement? See draft question at the foot of this document.

---

## v0.15.3 — Centrality cold-start floor (this week)

### Scope
A freshness-aware centrality floor in retrieval scoring. Three to ten lines in `src/elfmem/scoring.py`. No schema change. No new config. No new tests beyond one regression.

### Change
```python
# In compute_centrality() or wherever the centrality term is constructed:
def effective_centrality(block, recency: float, raw_centrality: float) -> float:
    """Fresh blocks get a centrality floor while their graph position is establishing.
    
    Cold-start blocks (few edges, high recency) would otherwise lose top-K to bedrock
    centrality even with much better semantic match. The floor decays as recency does.
    """
    edges = block.get("edge_count", 0)
    if edges < 2 and recency > 0.7:
        return max(raw_centrality, 0.5 * recency)  # decays naturally with recency
    return raw_centrality
```

### Why this works
Per the simulation `/tmp/confidence_sim.py`, the bedrock-vs-new score gap in ATTENTION decomposes as:
- similarity: +0.07 advantage for new block
- confidence: -0.015 (cliff was the cause; v0.15.2 fixed it)
- recency: +0.030 advantage for new block
- centrality: **−0.105** the killer term
- reinforcement: −0.015

Centrality is 3.5× confidence's contribution. The cliff fix (v0.15.2) addressed the second-smallest term. This addresses the largest one.

The floor automatically decays as the block ages (recency falls), so cold-start protection is temporary — once a block is no longer "fresh", it earns centrality through actual graph position.

### Tests
- New: `test_cold_start_block_surfaces_top_k` — fresh block with `sim=0.74` beats bedrock with `sim=0.62, conf=0.95, centrality=0.80`.
- Existing tests should pass unchanged (no behaviour change for non-cold-start blocks).

### Migration
None. Pure scoring change.

### Risk
Very low. Pure scoring math. Reversible.

### Ships
Within 1 day of approval. Patch release v0.15.3.

---

## v0.16.0 — Sufficient statistics + additive rescore (1-2 weeks)

### Scope
The one load-bearing architectural change. Materialise the Beta-Binomial sufficient statistics already implicit in `outcome_evidence` + `confidence` as first-class columns. Make rescore additive. Make peer merge arithmetic. Keep `confidence` as a denormalised cache for backwards compat.

### Schema
```sql
ALTER TABLE blocks ADD COLUMN success_count REAL NOT NULL DEFAULT 0.5;
ALTER TABLE blocks ADD COLUMN failure_count REAL NOT NULL DEFAULT 0.5;

-- Bootstrap from existing values (one-time on first open under v0.16):
UPDATE blocks SET
    success_count = confidence * (1.0 + outcome_evidence),
    failure_count = (1.0 - confidence) * (1.0 + outcome_evidence)
WHERE success_count = 0.5 AND failure_count = 0.5;
```

`confidence` column **stays** as a denormalised view, updated on every (α, β) write:
```python
confidence = success_count / (success_count + failure_count)
```

This preserves every existing reader (`scoring.py`, `mind.py`'s `confidence ≥ 0.5` classifier, MCP tools, CLI) without modification. The semantics is unchanged for callers; the storage is now honest about what it represents.

### Code changes

**`src/elfmem/operations/outcome.py`** — `compute_bayesian_update` becomes a write to `(α, β)` and recomputes `confidence`:
```python
def compute_bayesian_update(
    success_count: float, failure_count: float,
    signal: float, weight: float = 1.0,
) -> tuple[float, float, float]:
    """Returns (new_success, new_failure, new_confidence)."""
    new_s = success_count + signal * weight
    new_f = failure_count + (1 - signal) * weight
    new_c = new_s / (new_s + new_f)
    return (new_s, new_f, new_c)
```

**`src/elfmem/operations/rescore.py:245`** — additive instead of destructive:
```python
# Old: confidence = analysis.alignment_score  (clobbered outcome history)
# New: fold as one weighted evidence event
new_s, new_f, new_c = compute_bayesian_update(
    block["success_count"], block["failure_count"],
    signal=analysis.alignment_score,
    weight=config.memory.rescore_evidence_weight,  # default 0.5
)
```

This is the single highest-value change in the entire plan. Per simulation S3b, additive rescore reduces clobber damage by ~30× (Δ=0.30 → Δ=0.014).

**`src/elfmem/operations/peer.py:_peer_confidence`** — arithmetic merge:
```python
def merge_peer_evidence(local_s, local_f, remote_s, remote_f, trust: float):
    return (
        local_s + remote_s * trust,
        local_f + remote_f * trust,
    )
```

This is the only mathematically principled way to merge two peers' views of the same block. It enables the peer protocol's epistemic coherence without ad-hoc trust scaling.

### Config
- `memory.outcome_evidence_weight: float = 1.0` — already implicit; make explicit
- `memory.llm_initial_weight: float = 1.0` — how strongly LLM rating bootstraps the prior on `consolidate`
- `memory.rescore_evidence_weight: float = 0.5` — how strongly rescore folds new ratings (half-weight by default since rescore is a re-assessment, not a fresh observation)

Three knobs, all defensible defaults, all configurable for users who want to tune.

### Renaming and terminology
- **Keep `confidence`** as the public-facing column name and term. It is the elfmem brand-term.
- **Keep `alignment_score`** for the LLM-rated identity dimension. Already exists, already plays Park's "importance" role.
- The new internal columns are `success_count` and `failure_count` — descriptive Bayesian-sufficient-statistic names, no jargon imported.

### Tests
- `test_rescore_additive` — block at confidence=0.85 with 20 outcome events, rescore with α=0.55, new confidence within Δ=0.05 of 0.85 (not clobbered to 0.55).
- `test_peer_merge_arithmetic` — two peers with `(α=10, β=2)` and `(α=2, β=10)` merge to `(α=12, β=12) → confidence=0.5`.
- `test_outcome_updates_sufficient_stats` — `outcome(signal=1.0, weight=1.0)` increments `success_count` by 1.0, leaves `failure_count` unchanged.
- `test_confidence_column_is_derived_view` — `confidence == success_count / (success_count + failure_count)` always.
- All existing `outcome()`, `rescore()`, `peer_send/inbox` tests pass unchanged.

### AgentGuide updates
- `outcome()` — updated docstring noting `success_count`/`failure_count` are now first-class
- `rescore()` — updated docstring noting rescore is now additive
- `peer_send()`/`peer_inbox()` — updated docstring noting merge is arithmetic

### Migration
One-time additive ALTER TABLE on first open under v0.16. Bootstrap from existing `(confidence, outcome_evidence)`. No data loss. Reversible — keep `confidence` column.

### Risk
Low. Existing readers see equivalent values. New code path is pure-function and well-tested (the math is 30 years old). Migration is additive.

### Ships
1–2 weeks after v0.15.3 approval. Minor release v0.16.0.

---

## v0.17.0 — Exploration bonus + power-law experiment (3-5 days)

### Scope
Two small additions that the v0.16 substrate enables:

1. **Exploration bonus from variance**: blocks with high utility uncertainty get a small ranking lift. This is the principled answer to "fresh blocks need to be discoverable" once the cold-start centrality floor (v0.15.3) has stopped them being invisible.

2. **Power-law retrievability as opt-in**: A/B against the existing exponential `decay_lambda` model. Behind a feature flag. Allow real-world data to vote.

### Code

**`src/elfmem/scoring.py`** — add exploration term:
```python
variance = (
    block["success_count"] * block["failure_count"]
    / ((block["success_count"] + block["failure_count"]) ** 2
       * (block["success_count"] + block["failure_count"] + 1))
)
exploration = kappa * sqrt(variance)
score = weights.similarity * sim + weights.confidence * confidence \
      + weights.recency * recency + weights.centrality * centrality \
      + weights.reinforcement * reinforcement + exploration
```

`kappa` is a hardcoded constant (Critic agent: "the κ hyperparameter is exactly the kind of knob we should hardcode at a defensible value"). Default `kappa = 0.05` — small enough not to dominate, large enough to surface high-uncertainty blocks above bedrock when similarity is comparable.

**`src/elfmem/scoring.py`** — power-law retrievability as flag:
```python
if config.memory.use_power_law_decay:
    retrievability = (1 + 0.5 * elapsed_hours / stability) ** -0.5
else:
    retrievability = exp(-decay_lambda * elapsed_hours)  # current default
```

Where `stability` is bootstrapped from `1 / decay_lambda` for backwards compat.

### Why opt-in
The Research agent grades power-law as A for flashcards, D for agent memory. The Critic agent calls it "fashion, not calibration." We adopt the *form* behind a flag so users can A/B; we don't make it the default until we have evidence on actual elfmem traces.

### Config
- `memory.kappa: float = 0.05` (hardcoded by default; configurable for tuning)
- `memory.use_power_law_decay: bool = False` (experimental flag)

### Tests
- `test_exploration_bonus_lifts_high_variance_blocks` — block with `(α=0.5, β=0.5)` (max variance) ranks higher than block with `(α=10, β=10)` (same mean, low variance) when other signals match.
- `test_power_law_decay_flag_swaps_formula` — with flag on/off, retrievability uses different formula but is monotonic in elapsed time.

### Risk
Medium. Exploration bonus is small (κ=0.05) but changes top-K composition. Power-law is opt-in so risk-isolated. Both changes are reversible.

### Ships
3–5 days after v0.16.0 approval. Minor release v0.17.0.

---

## v0.18+ — Deferred items, with explicit triggers

Each of the following items in the research paper is deferred until empirical evidence justifies the work. Each is paired with a concrete trigger condition.

### Event log table (`block_events`)
**Trigger**: when we want to either (a) replay scoring history with a different formula, or (b) provide audit-trail features to users for compliance/debugging. Until then, `block_outcomes` already captures the only event type with non-trivial signal.

### FSRS-5 stability mechanics
**Trigger**: when we have ≥10,000 outcome events in production from real elfmem deployments and can fit the 19 parameters from actual data. Until then, our `decay_lambda` per-tier is defensible and we have no fitting infrastructure.

### Hierarchical abstract tier
**Trigger**: when we measure a context-bloat retrieval defect on real elfmem queries. Until then, the existing summary-block mechanism is sufficient.

### Zettelkasten auto-linking on consolidate
**Trigger**: when we have evidence that manual `connect()` is undertilised by agents (e.g., few agent-issued connect() calls per dream cycle) AND we have a way to validate LLM-judged links don't introduce phantom edges. Until then, encouraging explicit `connect()` in AgentGuide is sufficient.

### Per-frame weight retuning
**Trigger**: when we have head-to-head benchmark data (MemoryAgentBench, LoCoMo) showing current weights are suboptimal. Until then, current weights are reasonable.

### LLM-as-evidence calibration (`elfmem doctor --recalibrate`)
**Trigger**: when we observe systematic miscalibration on a held-out test set. Until then, `w_llm = 1.0` is a defensible default and instrumentation is premature.

---

## What we are explicitly NOT adopting from the research paper

To make this concrete, the items rejected (with reasoning):

| Item | Why rejected |
|---|---|
| Rename `confidence → utility` | elfmem brand-term; rename has high churn cost for zero behavioural value |
| Rename `alignment_score → importance` | Park's terminology has flashcard baggage; ours fits elfmem's identity framing |
| Full event log table | `block_outcomes` already captures the only event type with non-trivial signal; replay is research luxury, not user value |
| FSRS-5 19-parameter stability update | No fitting infrastructure; cargo-cult on flashcard-fit numbers |
| Difficulty `D` as separate channel | Only exists so FSRS updates compile; no independent semantic justification |
| Three-tier hierarchy (raw/summary/abstract) | Summaries are sufficient; abstract tier is MemoryOS imitation without measured benefit |
| Zettelkasten auto-linking | `connect()` exists; auto-linking introduces LLM failure modes without measured gain |
| Per-frame weight retuning in v0.16 | 16 magic numbers shipped as defaults with no tuning data is parameter explosion |
| `elfmem privacy --forget` / `--recalibrate` CLI | Scope creep; not memory-scoring work |

These can each be revisited when their respective triggers are met. None is permanently rejected; all are deferred until evidence justifies them.

---

## Long-term north star — where this is heading

The three-version plan (v0.15.3 → v0.16.0 → v0.17.0) takes elfmem to a state where:

- Confidence is a derived view over honest Bayesian sufficient statistics
- Rescore preserves earned outcome evidence rather than clobbering it
- Peer merge is mathematically principled
- Fresh blocks have cold-start protection that decays gracefully
- High-uncertainty blocks get a small exploration lift
- Power-law decay is available for users who want to experiment

This is **80% of the research paper's value at 17% of its proposed scope.**

Beyond v0.17, the natural progression — once empirical evidence accumulates — is:

- **v0.18.x — Benchmarking**: contribute elfmem as an entrant to MemoryAgentBench. Measure head-to-head against MemMachine, A-MEM, Mem0. Use real data to retune weights, validate power-law on agent traces.
- **v0.19.x — Earned architectural features**: based on benchmark results, ship the deferred items that empirical evidence supports. Likely candidates: power-law as default if A/B wins; weights retuning; possibly Zettelkasten if `connect()` is shown undertilised.
- **v0.20.x — Event log if needed**: only if replay or audit features are user-requested or if a v0.19 scoring change requires the substrate.
- **v1.0 — Interface freeze**: lock the public API once scoring architecture is empirically grounded.

The key discipline: **earn each subsequent layer with evidence**. The research paper synthesises five decades of work — but elfmem is a specific system with specific users and specific traces. Each adoption decision should be validated against elfmem's own data, not just against the paper's synthesis.

---

## Decision asks

1. **Approve v0.15.3** (centrality floor, 1 day) — ships Dmitry's actual fix this week?
2. **Approve v0.16.0** (sufficient stats + additive rescore + peer merge, 1-2 weeks) — the load-bearing architectural change?
3. **Approve v0.17.0** (exploration bonus + power-law flag, 3-5 days) — the polish on top of the substrate?
4. **Approve the deferred list** — explicit confirmation that event log, FSRS mechanics, hierarchical tiers, Zettelkasten auto-link, and renames are not v0.16-v0.17 scope?
5. **Publish the research paper** (`docs/research/memory_scoring_survey.md`) so external readers (Dmitry, others) can validate the analysis? Currently untracked.

Recommend: yes to all five. Start with v0.15.3 today.

---

## References

- `docs/research/memory_scoring_survey.md` — full 7500-word research paper
- `docs/plans/plan_confidence_architecture.md` — preliminary Path L vs Path G plan (now superseded by this document)
- `/tmp/confidence_sim.py` — 5-mapping × 5-scenario simulation (numerical basis)
- Agent reports in this session transcript: Consistency agent, Research agent (`accb8f19a332428ff`), Critic agent (`a4e49c97d3fef0bb8`)
- `docs/coding_principles.md` — SIMPLE · ELEGANT · FLEXIBLE · ROBUST
- `docs/agent_friendly_principles.md` — agent-first contract
- elfmem CLAUDE.md — four rhythms, four frames, project axioms

---

*This plan is opinionated where the research paper was descriptive. It commits to scope, sequence, and explicit rejections. It can still be revised — but each revision should engage with the agent reports' specific arguments, not just propose alternative ambitions.*

---

## Appendix — draft follow-up question for Dmitry (issue #50)

> Hi Dmitry — v0.15.3 shipped the cold-start centrality floor we discussed; the change is at `src/elfmem/scoring.py:effective_centrality()`. Before we lock in the next architectural step (v0.16, sufficient statistics + additive rescore), we did a numerical re-check against the real scoring path and found one regime where the floor alone may not be enough:
>
> The floor closes ~0.075 of weighted score gap. That's sufficient when the competing "bedrock" blocks have **reinforcement = 0** (e.g. fresh constitutional content, blocks never co-retrieved). It's **not** sufficient when bedrock has reinforcement ≈ 1.0 — typical for blocks that have been co-retrieved many times or are constitutional SELF content that's been around for a while.
>
> Could you confirm one of:
>
> 1. After upgrading to v0.15.3, does your originally-reported symptom (fresh relevant blocks not surfacing in top-K) appear resolved on real queries?
> 2. If it persists, are the blocks that *do* surface ahead of the missed fresh block ones with high reinforcement counts (`elfmem inspect <id>` shows reinforcement) — or are they unreinforced bedrock?
>
> A `(query, expected_id, actual_top3_with_reinforcement_counts)` triple would let us tell exactly which regime you're in. If it's the reinforced-bedrock regime, we'll likely bring the v0.17 exploration bonus forward rather than waiting on the v0.16 substrate.
>
> Numerical detail and the regression fixture pinning both regimes:
> - `scripts/verify_v0_15_3_scoring.py`
> - `tests/test_scoring.py::TestColdStartGapRegression`
> - `docs/plans/plan_memory_scoring.md` — "Post-ship verification of v0.15.3" section
