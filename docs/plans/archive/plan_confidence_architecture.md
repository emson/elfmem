# Plan: confidence architecture — Path L vs Path G

**Status**: design — under review
**Driver**: [issue #50 cold-start report](https://github.com/emson/elfmem/issues/50) + structural inconsistencies found during analysis
**Target**: cliff fix in v0.15.2; architecture choice for v0.16+

---

## Background — what we're really solving

Dmitry's report ("I just told it something important and it ignored that next turn") surfaces a complaint about cold-start retrieval. Investigation found that the report actually conflates **three distinct bugs hiding inside one symptom**:

1. **The cliff** — `consolidate.py:338-342` snaps alignment_score below 0.70 to a flat 0.50, creating a 0.20 discontinuity. Pure correctness defect. ±0.03 score impact in ATTENTION.
2. **The rescore clobber** — `rescore.py:245` overwrites confidence with raw `alignment_score`, destroying outcome history accumulated over months. v0.13.3 latent bug. ±0.30 damage per simulation S3.
3. **The cold-start retrieval gap** — fresh blocks lose top-K on **centrality** (0.105 score gap), not confidence (0.015 gap). Retrieval-side defect — the *real* proximate cause of Dmitry's symptom.

Simulation results (`/tmp/confidence_sim.py`, 5 mappings × 5 scenarios) confirm:
- No confidence mapping alone fixes Dmitry's symptom (S1b, S4b)
- Making rescore additive reduces damage 30× regardless of mapping (S3b)
- Bayesian sufficient statistics are the only mapping where peer transfer can be principled (S5)
- One positive outcome lifts confidence past 0.70 under all mappings (S2) — outcome rescue is fast once it starts

The deepest structural insight: **a single scalar named "confidence" is currently being asked to represent three orthogonal quantities** — belief in truth, retrieval salience, and identity-alignment. That conflation is what generates every visible defect.

---

## Path L — Conservative incremental

### Principle
Keep confidence-as-concept. Make the three operations (init, outcome, rescore) numerically consistent and additive. No event log, no per-block multi-channel state. Each milestone is independently shippable and reversible.

### Milestones

#### v0.15.2 — Cliff deletion (1 day)

**Scope**: align `consolidate.py` with `rescore.py` so both use the same formula.

**Change**: `src/elfmem/operations/consolidate.py:338-342` becomes:
```python
confidence = analysis.alignment_score
```

**Why this works**: `_fallback_analysis()` already returns `alignment_score=0.5` (line 141), so LLM-timeout blocks naturally land at 0.50 — no threshold needed. The `self_alignment_threshold` config field becomes unused; deprecate but keep accepting it for backwards compat.

**Migration**: none. Existing DBs unaffected. New writes use identity. Rescore-on-aged-blocks re-applies the new mapping naturally.

**Risk**: Low. The most aggressive shift is for blocks the LLM rated α<0.5 — they now get confidence=α instead of 0.50. This is arguably correct (the LLM said not-aligned; honour that). Outcome() rescues real-but-mis-rated blocks within 1-2 calls.

**Tests**: grep `tests/` for assertions of `confidence == 0.50` on sub-threshold blocks; flip to `confidence == alignment_score`. ~5 tests likely.

**Does not fix**: Dmitry's actual top-K symptom (that's a retrieval-side problem, not a storage one).

---

#### v0.16.0 — Sufficient-stats storage + additive rescore (~1 week)

**Scope**: store the Beta-Binomial sufficient statistics (`success_count`, `failure_count`) as first-class columns. Make rescore additive instead of destructive. Make peer-merge arithmetic.

**Schema** (one migration):
```sql
ALTER TABLE blocks ADD COLUMN success_count REAL NOT NULL DEFAULT 0.5;
ALTER TABLE blocks ADD COLUMN failure_count REAL NOT NULL DEFAULT 0.5;
-- bootstrap from existing confidence:
UPDATE blocks SET
    success_count = confidence * 1.0,
    failure_count = (1.0 - confidence) * 1.0
WHERE success_count = 0.5 AND failure_count = 0.5;
```

**Updates**:
- `outcome.py:compute_bayesian_update` becomes a write to `(success_count, failure_count)`:
  ```python
  success_count += signal * weight
  failure_count += (1 - signal) * weight
  confidence = success_count / (success_count + failure_count)  # derived view
  ```
- `rescore.py:245` becomes additive — folds new α as one weighted evidence event:
  ```python
  weight = config.memory.rescore_evidence_weight  # default 0.5 (half-strength)
  success_count += analysis.alignment_score * weight
  failure_count += (1 - analysis.alignment_score) * weight
  ```
- `peer.py:_peer_confidence` becomes a sum: `(s_a + s_b·trust, f_a + f_b·trust)`.
- `consolidate.py` initial write seeds `(α·w_llm, (1-α)·w_llm)` instead of writing confidence directly.

**Confidence column stays** as a denormalised cache, updated on every (α, β) write. Existing readers (`scoring.py`, `mind.py`'s 0.5 classifier) keep working unchanged.

**Mind.py 0.5 threshold**: surface as `MindConfig.outcome_hit_threshold: float = 0.5`. No behaviour change; just no longer hidden.

**New config**:
- `memory.outcome_evidence_weight: float = 1.0` (already implicit)
- `memory.llm_initial_weight: float = 1.0` (how strongly LLM rating bootstraps the prior)
- `memory.rescore_evidence_weight: float = 0.5` (how strongly rescore folds new ratings)

**Migration**: in-place bootstrap from existing confidence on first open under v0.16. One-time per DB. No data loss.

**Risk**: Medium. Schema migration must be reversible. New code path for additive evidence needs thorough testing. Backward compat preserved by keeping `confidence` as derived view.

**Tests**: extend `test_outcome.py` with sufficient-stats assertions. New `test_rescore_additive.py` proving 30× damage reduction (per S3b). New `test_peer_merge_arithmetic.py`.

**Does not fix**: cold-start retrieval gap (the centrality problem).

---

#### v0.16.1 — Cold-start retrieval protection (~3 days)

**Scope**: address the real Dmitry symptom — fresh blocks losing top-K despite better semantic match.

**Four candidate mechanisms** (pick one after design discussion):

| Option | Mechanism | Code impact | Side effects |
|---|---|---|---|
| **a) Freshness centrality floor** | Blocks with `edges < 2` AND `recency > 0.8` use `centrality = max(actual, 0.5)` in scoring | ~3 lines in `scoring.py` | Minimal; only affects blocks under both conditions |
| **b) ATTENTION reweighting** | Drop `centrality` weight 0.15→0.05; boost `recency` 0.10→0.20 | 2 numbers in `config.py` | Affects all blocks, all queries in ATTENTION |
| **c) Reserved slot** | Top-K guarantees ≥1 slot for blocks with age < N hours | ~10 lines in `retrieval.py` | Cleanest semantically; new code path |
| **d) Exploration bonus** | Score gains `κ · 1/sqrt(edges+1)` term — Thompson-style | ~5 lines in `scoring.py`; new weight | Generalises to "uncertain things explored more" |

**Recommendation**: option (a) for v0.16.1 — smallest, most targeted. Option (d) is the "right" answer long-term but only makes sense once we have variance (Path G).

**Risk**: Low–medium. Retrieval changes need validation against existing behaviour. Add `recall()` regression tests with known fixtures.

---

#### v0.17.0 — Confidence-as-derived (~2 weeks)

**Scope**: remove the `confidence` column entirely. All readers compute `c = α / (α + β)` on demand. Cleanup pass.

**Why this step exists separately**: it's safe only after every reader is audited (the call-trace agent found 14 sites). One-PR removal would be too much churn.

**Schema**: `ALTER TABLE blocks DROP COLUMN confidence`. Backwards-incompat — bump minor version.

**Risk**: Low after audit. Each reader becomes a property access on a small helper. SQL queries gain a computed column expression. Performance neutral (one float division per row).

**Outcome**: clean state model. Confidence is no longer a state variable; it's a view over `(α, β)`. Path L is now complete and the substrate matches what Path G would have built.

---

### Path L total
- **3 weeks calendar time** across 4 minor versions
- **~600 LOC delta** total
- **One schema migration** (additive, reversible)
- Each step ships value independently; each is reversible
- Ends with substrate that Path G can build on

---

## Path G — Multi-channel activation framework

### Principle
Replace `confidence` with three orthogonal channels computed (not stored) from an event log. Adopt cognitive-science-derived activation theory (ACT-R) + spaced-repetition stability model (FSRS) + the Bayesian sufficient stats from Path L. This is a fundamental rearchitecture of how memory ranks itself.

### Architectural inspirations

| Source | Adopted concept |
|---|---|
| **ACT-R activation theory** (Anderson, 1996) | Activation = log-sum of decayed past use events. Computed from event log, not stored. |
| **FSRS spaced-repetition** (Wozniak; Anki) | Per-block `stability S`. Retrievability = `exp(-Δt / S)`. S grows on success, shrinks on failure. |
| **Beta-Binomial sufficient stats** (online learning) | `(α, β)` updated by every evidence event. Posterior is `α/(α+β)`; variance gives uncertainty. |
| **Thompson sampling** (multi-armed bandits) | Score includes a `κ · √variance` term so high-uncertainty blocks get exploration lift. |

### Schema

```sql
-- Replace blocks.confidence with three channels:
ALTER TABLE blocks ADD COLUMN success_count REAL NOT NULL DEFAULT 0.5;
ALTER TABLE blocks ADD COLUMN failure_count REAL NOT NULL DEFAULT 0.5;
ALTER TABLE blocks ADD COLUMN stability REAL NOT NULL DEFAULT 24.0;  -- hours
-- alignment kept as-is (drifts as SELF evolves):
-- (blocks.self_alignment already exists)

-- New event log for activation computation:
CREATE TABLE block_events (
    block_id TEXT NOT NULL REFERENCES blocks(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,  -- 'learn', 'consolidate', 'outcome', 'recall_hit',
                                -- 'rescore', 'peer_import', 'connect'
    signal REAL,                -- positive/negative weight, NULL for neutral
    weight REAL NOT NULL DEFAULT 1.0,
    created_at_hours REAL NOT NULL  -- relative to system start, same as decay
);
CREATE INDEX idx_block_events_block_id ON block_events(block_id);
CREATE INDEX idx_block_events_recency ON block_events(created_at_hours);
```

### Retrieval scoring formula

```python
def score(block, query, frame, now_hours):
    similarity = cosine(query_embed, block.embed)
    
    # Channel 1: belief — Bayesian posterior
    belief_mean = block.success / (block.success + block.failure)
    belief_var  = (block.success * block.failure) / (
        (block.success + block.failure)**2 *
        (block.success + block.failure + 1)
    )
    
    # Channel 2: activation — ACT-R style log-sum of decayed events
    events = recent_events(block.id, max_age_hours=2160)  # 90 days
    activation = math.log(sum(
        math.exp(-(now_hours - e.t) / block.stability)
        for e in events
    ) + 1e-9)  # +ε avoids log(0)
    
    # Channel 3: alignment — current LLM rating against current SELF
    alignment = block.self_alignment  # set by consolidate/rescore
    
    # Exploration bonus — Thompson-style, scales with uncertainty
    exploration = math.sqrt(belief_var)
    
    return (
        frame.w_sim         * similarity
      + frame.w_belief      * belief_mean
      + frame.w_activation  * activation
      + frame.w_alignment   * alignment
      + frame.kappa         * exploration
    )
```

### Event mechanics

| Event | What it does |
|---|---|
| `learn` | Insert block; `(α, β) = (0.5·w_llm, 0.5·w_llm)`; `S = S_init`; log event |
| `consolidate` | Add LLM rating as evidence: `α += LLM_α · w_llm`, `β += (1-LLM_α) · w_llm`; log |
| `outcome(signal, weight)` | `α += signal · weight`; `β += (1-signal) · weight`; update S (FSRS update on success/fail); log |
| `recall_hit` | Block surfaced and was used (heuristic: agent's next operation referenced it) → small S boost; log |
| `rescore` | Re-rate against current SELF; fold as one weighted evidence event (never overwrite); update `self_alignment`; log |
| `peer_import` | Merge incoming `(α, β, S)` weighted by peer trust |

### FSRS-style stability update

On a positive outcome (`signal ≥ 0.5`):
```python
retrievability = math.exp(-(now - last_event_t) / S)
S_new = S * (1 + math.exp(a) * (11 - difficulty) * S**b * 
            (math.exp(c * (1 - retrievability)) - 1))
S_new = clamp(S_new, S_MIN, S_MAX)
```
On a negative outcome: `S_new = S * d_factor` where `d_factor < 1`.

Difficulty `D = 1 - alignment` — well-aligned blocks are "easier" (longer intervals); poorly-aligned blocks are "harder" (shorter intervals, more frequent review).

(Real FSRS uses ~17 parameters fit to user data; we adopt the structure with reasonable defaults and make the constants configurable.)

### Migration from Path L state

Path G assumes Path L's `(α, β)` storage already exists.

```sql
-- Add stability with FSRS-reasonable default:
ALTER TABLE blocks ADD COLUMN stability REAL NOT NULL DEFAULT 24.0;

-- Synthesise event log from existing block_outcomes table:
INSERT INTO block_events (block_id, event_type, signal, weight, created_at_hours)
SELECT block_id, 'outcome', signal, weight, created_at_hours
FROM block_outcomes;

-- Add a single 'learn' event per block as the origin:
INSERT INTO block_events (block_id, event_type, signal, weight, created_at_hours)
SELECT id, 'learn', NULL, 1.0, created_at_hours
FROM blocks;
```

### Path G roadmap

| Version | Scope | Risk |
|---|---|---|
| v0.16.0 | (same as Path L v0.16.0 — sufficient stats; no event log yet) | Medium |
| v0.17.0 | Introduce event log; activation computation; opt-in via `ElfmemConfig.experimental.activation_scoring=True`; A/B against current scorer | Medium-high |
| v0.18.0 | Activation default. Legacy confidence column derived-only or dropped. Mind.py updates to use belief explicitly | High (default behaviour change) |
| v0.19.0 | FSRS-style stability fully active; spaced-review rhythm in dream | Medium |
| v1.0.0 | Stability sufficiently empirically validated; freeze the interface | — |

### Path G dissolves the problems

| Problem | Resolution |
|---|---|
| Cliff at α=0.70 | Doesn't exist — alignment is one channel, smooth by construction |
| Rescore clobbers outcome history | Doesn't exist — rescore appends event, never overwrites |
| Cold-start fresh blocks lose top-K | Exploration bonus `κ·√var` lifts high-uncertainty new blocks naturally |
| Constitutional dominance over months | Stability decays without reinforcement; bedrock must continuously earn rank |
| Peer convergence non-arithmetic | Sufficient stats merge by addition: `(α₁+α₂, β₁+β₂, max(S₁,S₂))` |
| mind.py hard-codes 0.5 threshold | Use `belief_mean ≥ threshold` explicitly; threshold becomes config |
| SELF drift demoting old proven blocks | Alignment is a separate channel; belief is preserved through outcomes |
| "I told it something important and it ignored that" | Recent event in log + exploration bonus lift new blocks |

### Path G total
- **~8 weeks calendar time** across 4-5 minor versions
- **~2000 LOC delta** (event log + activation + FSRS + scoring rewrite + migration + tests)
- **Two schema migrations** (sufficient stats, event log)
- **One major behaviour change** at v0.18.0 (activation default)
- Aligns elfmem's "biological memory" framing with the actual cognitive-science literature
- Reversible per-milestone via feature flag through v0.17.x; v0.18+ is the commit point

---

## Side-by-side tradeoff

| Axis | Path L | Path G |
|---|---|---|
| **Time to first Dmitry-bug fix** | 1 day (v0.15.2 cliff deletion) | 1 day (same v0.15.2) |
| **Time to architectural cleanup** | ~3 weeks (v0.17.0) | ~8 weeks (v0.19.0) |
| **Risk per release** | Low — each step isolated | Medium-high at v0.18.0 boundary |
| **Reversibility** | Every milestone reversible | Reversible until v0.18.0; then commit |
| **LOC delta** | ~600 | ~2000 |
| **Schema migrations** | 1 (additive) | 2 (additive + event log) |
| **New concepts** | Sufficient stats (clean Bayesian) | Activation theory + FSRS + Bayesian (3 frameworks unified) |
| **Long-term coherence** | Good — clean Bayesian | Excellent — matches biological framing |
| **Cold-start retrieval fix** | Manual mechanism in v0.16.1 (centrality floor etc.) | Falls out automatically from exploration bonus |
| **Constitutional dominance** | Not addressed | Naturally addressed via stability decay |
| **Peer convergence** | Arithmetic merge of (α,β) | Arithmetic merge of (α,β,S) — strict superset |
| **Performance at scale (100k blocks)** | Same as today | Activation computation is O(events_per_block) per query — needs caching/decay-window |
| **Compatibility burden** | Minimal; mostly additive | One-time event-log bootstrap; opt-in period to A/B |

---

## When each path is correct

**Path L is the right choice if**:
- We prefer incremental delivery over architectural ambition
- Dmitry-class bugs are the main concern, not multi-agent peer scenarios
- We're not yet sure activation theory is the right framework (need more empirical data)
- We want every step independently reviewable and reversible

**Path G is the right choice if**:
- We're committed to elfmem's "biological memory" identity claim
- Multi-agent peer scenarios matter (peer convergence is a Path G strength)
- We're willing to invest 8 weeks for a long-term durable architecture
- We want elfmem to be defensibly grounded in cognitive science literature

**They are not mutually exclusive**: Path L's milestones v0.15.2 + v0.16.0 are also Path G's first milestones. The fork happens at v0.17.0. Doing Path L first and then re-evaluating after v0.16 ships is the lowest-risk way to keep options open.

---

## Open questions

1. **Does the activation log scale?** At 100k blocks × N events each, activation computation per query is O(N·events). Need to design retention/cache strategy. Options: cap events per block (lossy); decay-window retention (last 90 days verbatim); precompute activation hourly via dream rhythm.

2. **Are FSRS defaults appropriate for memory blocks (vs. flashcards)?** FSRS is empirically fit to flashcard reviews. We'd need to verify the parameters generalise, or run our own fitting against synthetic agent traces.

3. **How does `recall_hit` get detected?** We need a signal that "a recalled block was actually used" — either an agent-facing `confirm()` call, a heuristic (next operation references the block's content), or telemetry. Without this, activation loses its self-reinforcing property.

4. **Should we deprecate `confidence` as a public API name?** Or keep "confidence" as the user-facing term backed by belief mean? The former is cleaner; the latter is less disruptive to docs and AgentGuide.

5. **What's the LLM-rating-as-evidence weight?** Path L's `llm_initial_weight=1.0` treats the LLM as one outcome event. Is that calibrated? If the LLM is overconfident, this propagates. Empirical calibration study needed.

6. **Mind.py's 0.5 hit threshold** — should it become `belief_mean ≥ threshold` or `belief_mean - κ·√var ≥ threshold` (lower confidence bound)? The latter is more honest for prediction outcomes.

7. **Does the exploration bonus open an adversarial vector?** Could a peer flood low-evidence blocks that surface anyway due to exploration weight? Need to bound `κ` and consider per-source rate limits.

---

## Recommendation

**Ship Path L through v0.16.0** (cliff fix + sufficient stats + additive rescore + peer merge). This delivers the biggest single value (30× rescore-damage reduction per simulation S3b) with low risk.

**Pause and re-evaluate before v0.17.0.** By then we'll have lived with sufficient-stats storage for a release cycle, validated the migration in production, and have empirical data on whether activation theory adds enough value to justify the v0.17-v0.19 work.

**Publish this doc** so contributors (Dmitry, others) can challenge the framing before we commit. Cognitive-science-grounded architecture is the kind of decision that benefits from external review.

---

## References

- Anderson, J.R. (1996) "ACT: A simple theory of complex cognition." *American Psychologist*. The activation theory model.
- Wozniak, P. & Open Spaced Repetition. "FSRS: Free Spaced Repetition Scheduler." Active OSS project; powers Anki's FSRS mode.
- Hu, Koren, Volinsky (2008) "Collaborative filtering for implicit feedback datasets" — for the sufficient-stats + decay update pattern.
- Russo et al. (2018) "A Tutorial on Thompson Sampling." For the exploration bonus rationale.
- Bjork, R.A. (1994) "Memory and metamemory considerations in the training of human beings." For the "desirable difficulty" principle underlying spaced retrieval.
- elfmem `/tmp/confidence_sim.py` — empirical simulation results referenced throughout.
