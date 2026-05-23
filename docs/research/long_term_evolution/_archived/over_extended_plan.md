# Plan: Self-Architecting elfmem — from immortal constitution to evolved identity

**Status**: draft for review
**Author**: elf (curator) + Ben
**Date**: 2026-05-22
**Branch**: `feature-constitutional-experiments`
**Scope**: v0.17 → v0.20 (four-version phased rollout)
**Synthesises**: Six analytical findings (D1–D6 closed-form derivations) + four empirical simulation rounds (mc_evolution, mc_constitutional, mc_ego_feedback, mc_scenarios, mc_self_architect)

---

## Executive summary

elfmem today has three structural problems that compound with usage over years, identified analytically and confirmed empirically:

1. **Signal inflation** — Beta-Binomial outcome accumulation becomes asymptotically inert by N=100 events (Δc < 0.004 per outcome)
2. **Structural constitutional dominance** — In ATTENTION, constitutional bedrock with thematic overlap ≥ 0.75 cannot be beaten by any new content under current weights
3. **Constitutional immortality** — PERMANENT tier half-life is 47.5 years at typical usage rates

Update-rule tweaks (forgetting factors, periodic resets, tier demotion) **cannot fix these** — the moat is structural, not parametric. Monte Carlo evaluation across seven update strategies showed no strategy reducing bedrock-share-of-top-5 below 87.7% post-regime-change.

**The fix is architectural, not parametric**: constitutional content is a different *kind* of content from task knowledge. They were being forced into one ranking pool. Once separated (Architecture M: exclude constitutional from ATTENTION; inject as preamble at render time), retrieval quality improves from 45% → 78% under drift.

But pure M freezes constitutional content — preserved but unable to adapt. **Model C** (M + dedicated SELF-frame reinforcement channel) makes constitutional Darwinian: they earn persistence through proven alignment with the current self. **Model D** (Model C + distributed feedback across top-3 by softmax) fixes a hoarding failure mode where one constitutional block dominates ego_strength accumulation.

Cross-scenario evaluation revealed that **no single architecture is universally optimal**:
- Baseline wins under stable identity (85.3% qratio)
- M wins under drift
- Model C/D wins under regime change + adversarial conditions
- The right architecture depends on the agent's actual drift profile

**The deepest finding**: the four architectures are not discrete categories. They are different settings of four parameters (`attention_const_weight`, `ego_alpha`, `distribute_n`, `self_check_freq`). The agent can **self-architect** by hill-climbing in this 4-dimensional parameter space — and the simulation shows it correctly identifies the right direction for every scenario it's tested in.

**This plan proposes**:
- v0.17: Architecture M (single-line filter, immediate quality win, no schema change)
- v0.18: Four new `system_config` parameters with manual tuning (no automation yet)
- v0.19: Three-layer self-architecting agent (conservative defaults → adaptive hill-climbing → collaborative milestones)
- v0.20: Refinements based on real-user telemetry

Each phase ships independent value. Each phase is reversible. The full sequence transforms elfmem from "a system with an architecture" into "a system that grows into its own architecture."

---

## Background and motivation

### The proximate driver

Dmitry's production-feedback report (issue #50, 2026-05-17) projected elfmem's ~70% → ~35% hit-rate degradation over 10 years under default usage, vs ~72% → ~80% with disciplined defenses. He requested critique of his configuration choices and the underlying assumptions.

### The deeper question

Three observations made today the right moment for an architectural reckoning:

1. **The math says ossification is inevitable** under current scoring. `closed_form.py` D1 shows Beta-Binomial inertness; D4 shows structural constitutional dominance; D5 shows 47.5-year constitutional half-life.

2. **The v0.15.3 cold-start floor was a partial fix** to a symptom (Dmitry's report); it doesn't address the underlying dynamics.

3. **elf's own analysis** (note_2026_05_21_elf_reply_to_alv.md) recognised that elfmem's substrate produces P2-shaped failure modes (miscalibrated self-model worse than no self-model) — the SELF frame currently weights a fossilised confidence signal at 0.30.

The convergence of these three threads made it clear: **the problem isn't a knob to tune. It's the architecture itself.**

### What this plan IS

A complete redesign of how elfmem treats constitutional content, derived from six closed-form derivations + four simulation rounds + 12 architectural brainstorms + 365-day cross-scenario evaluation across five workload patterns.

### What this plan is NOT

- Not a rewrite. ~95% of elfmem code is untouched.
- Not a breaking change. Migration is config-driven and reversible.
- Not based on speculation. Every claim is grounded in math or simulation.
- Not unilateral. The proposed self-architecting layer requires user approval for major shifts.

---

## Mathematical foundation (D1–D6)

Six closed-form derivations from `scripts/longitudinal_sim/closed_form.py`. Each identifies a constraint that any plausible architecture must respect.

### D1 — Beta-Binomial inertness

Marginal effect of one outcome at discrepancy `(s - c_N)` with weight `w`:
$$\Delta c \approx \frac{w(s - c_N)}{1 + N + w}$$

At N=100 events with weight=1.0, signal discrepancy=0.4: Δc ≈ 0.004. The block's confidence is **asymptotically frozen**.

**Implication**: any mechanism that relies solely on accumulated outcomes to drive identity change must fail by year 1 of heavy usage. The v0.16 additive rescore (even at weight=0.5) moves an N=100 ossified block by 0.001 (D6).

### D2 — Decay half-life per tier

At Dmitry's reported 4h/day usage:
- PERMANENT (λ=0.00001): half-life 47.5 years
- DURABLE (λ=0.001): half-life 17.3 weeks
- STANDARD (λ=0.010): half-life 17.3 days
- EPHEMERAL (λ=0.050): half-life 3.5 days

**Implication**: STANDARD blocks must be reinforced within ~17 days or they decay below 50% recency. The cold-start window (v0.15.3) extends only the first ~9 days. Blocks must accumulate edges or outcomes in that window.

### D3 — Cold-start floor active window

Floor active while recency > 0.70. For STANDARD: 35.7 active hours = ~9 days at 4h/day.

**Implication**: v0.15.3's protection is real but brief. After the window, blocks must compete on actual graph position.

### D4 — Structural constitutional dominance

In ATTENTION frame (weights: sim 0.35, conf 0.15, rec 0.25, cent 0.15, reinf 0.10):

- Constitutional bedrock baseline (conf=1.0, rec=0.95, cent=0.80, reinf=1.0): **0.482 contribution before similarity**
- Best-case new block (conf=1.0, rec=1.0, cent=0.50 via floor, reinf=0.0): **0.400 contribution before similarity**
- Bedrock advantage: **0.082 baseline**

For a new block to beat bedrock, its similarity must exceed bedrock's by at least 0.082/0.35 = **0.234**. With realistic similarity gaps (new sim=1.0, bedrock sim=0.70), the advantage is only 0.105 — bedrock still wins.

**Implication**: in the current ATTENTION ranking, constitutional dominance is mathematically guaranteed whenever bedrock has thematic overlap > ~0.75 with the query. This is the "shadow hierarchy" Dmitry projected.

### D5 — Constitutional permanence horizon

PERMANENT λ = 0.00001. At 4h/day usage:
- recency = 0.50 reached at 47.5 years
- recency = 0.10 reached at 158 years

**Implication**: PERMANENT blocks are immortal on any human timescale. Rescore cannot evolve them (D6). A different mechanism is required to allow constitutional evolution.

### D6 — Rescore power against ossified blocks

Block earned to c=0.85 over N outcomes. Rescore with α=0.55 (weight=0.5):

| N | new c | Δc |
|---|---|---|
| 1 | 0.7900 | −0.060 |
| 10 | 0.8370 | −0.013 |
| 100 | 0.8485 | −0.0015 |
| 500 | 0.8497 | −0.0003 |

**Implication**: v0.16 additive rescore protects evidence (good) but cannot rescue ossification (limit). For sustained agent evolution, rescore alone is insufficient.

---

## Empirical findings (four simulation rounds)

All simulations: in-memory only, mock services, DB mtime verified unchanged. See `scripts/longitudinal_sim/safety.py` for guards.

### Round 1 — Update-rule tweaks fail (`mc_evolution.py`)

Seven strategies tested over 180 days × 5 seeds × 60° regime change at day 60:

| Strategy | bedrock_moat | qratio |
|---|---|---|
| Baseline | 91.2% | 49.3% |
| A2 forgetting (γ=0.99) | 90.9% | 49.2% |
| A4 reset (K=50) | 90.4% | 50.1% |
| D13 tier demotion | 90.1% | 49.4% |
| B7 evolve (full reset) | 87.7% | 50.2% |
| F17 composite | 90.4% | 50.1% |
| F18 Thompson | 100% | 57.5% |

**Conclusion**: no update-rule tweak reduces bedrock_moat below 87.7%. The moat is structural.

### Round 2 — Architecture M wins (`mc_constitutional.py`)

Six structural alternatives tested:

| Architecture | bedrock_moat | qratio |
|---|---|---|
| Baseline | 98.4% | 45.0% |
| Expiry at day 135 | 0% | 76.1% (loses constitutional) |
| Conviction-decay | 97.9% | 45.3% |
| **Self-context (M)** | **0%** | **78.2%** |
| No-PERMANENT (L) | 13.3% | 76.8% |
| Self-context + evolve | 0% | 78.2% |

**Conclusion**: Architecture M wins by a margin. The right move is to **separate constitutional content from ATTENTION ranking**, not to make it decay differently.

### Round 3 — Model C is the synthesis (`mc_ego_feedback.py`)

elf's Darwinian-identity proposal: blocks earn persistence through positive reinforcement.

| Architecture | qratio | ego_moat |
|---|---|---|
| Baseline | 45.0% | 98.4% |
| Architecture M | 78.2% | 0% |
| V2 ego-continuous | 70.8% | 20.0% |
| **Model C (M + SELF feedback)** | **75.3%** | **0%** |
| Model C high-frequency | 70.8% | 0% |

**Conclusion**: Model C preserves M's retrieval quality (75.3%) AND adds a feedback channel where constitutional that prove "still aligned" earn persistence. The cleanest synthesis.

### Round 4 — Full-scenario evaluation reveals trade-offs (`mc_scenarios.py`)

Four strategies × five scenarios × 365 days × 2 seeds:

```
scenario          baseline   M       Model C   Model D
─────────────────────────────────────────────────────────
stable             85.3%    78.4%    76.8%     76.6%
slow_drift         62.5%    72.4%    75.2%     76.8%
regime_change      44.5%    77.6%    78.7%     70.5%
quiet_burst        76.6%    77.0%    78.6%     74.4%
adversarial       65.7%    77.3%    81.9%     76.4%
─────────────────────────────────────────────────────────
range             40.8 pp  6.0 pp   7.3 pp    6.4 pp
```

Three findings:
1. **No universal winner.** Baseline wins under stable identity; Model C/D wins under drift/regime change.
2. **Plastic strategies are consistent** (6-7pp range vs baseline's 40.8pp).
3. **Hoarding (es_concentration = 9.5 universal)** — ONE constitutional block dominates ego_strength. Model D (distributed top-3 feedback) fixes this (concentration → 3.3) with modest quality cost.

### Round 5 — Self-architecting agent works (`mc_self_architect.py`)

Hill-climbing agent in 4-dimensional parameter space, 365 days × 2 seeds × 3 scenarios:

| Scenario | Direction agent moved | Correct? |
|---|---|---|
| stable | attention_const_weight rose 0 → 0.80 | ✓ (toward baseline) |
| drift | atw stayed 0; distribute_n rose 1 → 5 | ✓ (toward Model D) |
| regime_change | atw stayed 0; ego_alpha rose 0 → 0.04 | ✓ (toward Model C) |

**Conclusion**: the agent correctly self-diagnoses in every scenario. Magnitude is conservative (20% step size, 12 adaptations/year), but direction is unambiguously correct. With tuning (larger steps, more shadow queries, momentum), the agent would converge to optimum.

---

## The architectural insight — continuous parameter space

The four architectures we've named are not discrete categories. They are different settings of four shared parameters:

| Parameter | Range | baseline | M | Model C | Model D |
|---|---|---|---|---|---|
| `attention_const_weight` | [0.0, 1.0] | 1.0 | 0.0 | 0.0 | 0.0 |
| `ego_alpha` | [0.0, 0.1] | 0.0 | 0.0 | 0.05 | 0.05 |
| `distribute_n` | [1, 5] | n/a | n/a | 1 | 3 |
| `self_check_freq` | [0, 4]/day | 0 | 0 | 2 | 2 |

**The question changes from "which architecture?" to "what configuration?"**

This reframe dissolves multiple problems:
- No discrete jumps (smooth migration between configurations)
- No oscillation between named architectures (rate-capped parameter changes)
- No "multiple personalities" (one configuration at all times)
- Recoverable mistakes (small changes; reversible)
- No expert-knowledge requirement (parameters can be learned)

The four parameters span the architectures we've already tested *plus everything in between*. An agent that learns these four values has effectively learned its own architecture.

---

## Proposed design — the three-layer self-architecture

### Layer 1 — Conservative defaults (the infant stage)

Every elfmem instance starts here:
- `attention_const_weight = 0.0` (M-like; constitutional excluded from ATTENTION)
- `ego_alpha = 0.0` (no ego mechanism yet)
- `distribute_n = 1`
- `self_check_freq = 2` (SELF queries provide feedback baseline)

Duration: until `n_blocks ≥ 100` AND `days_since_setup ≥ 30`. Bootstrap period.

**Why M-like defaults?** M is the most robust strategy: it's only suboptimal when identity is genuinely stable, and in early life we don't yet know if it's stable. Conservative under uncertainty.

### Layer 2 — Adaptive parameter tuning (the maturing agent)

After bootstrap, every 28 days the agent:

1. **Snapshots current state** (no mutation)
2. **Computes baseline `qratio` estimate** via 20 shadow queries
3. **For each of 4 parameters**, tries a small perturbation (± 20% of range)
4. **Re-evaluates `qratio`** under each perturbation
5. **Picks the move with best gain** (or stays if no improvement > 1pp threshold)
6. **Logs the move** in `param_history` table (visible via `doctor`)

This is greedy hill-climbing. Slow, conservative, transparent.

**Crucially**: the shadow eval uses real elfmem state but doesn't mutate it. The agent reads its own state, predicts outcomes under alternative configurations, picks the winner.

**Rate limits**:
- Max one parameter change per 28-day period
- Min 1pp improvement threshold (avoid noise-driven changes)
- Lock-in period: 14 days after any change before next adaptation

### Layer 3 — Collaborative milestones (the formative moment)

When the agent detects a **regime change** (rolling drift > 2× baseline rolling drift), it surfaces:

> "I've detected a significant shift in my usage pattern over the last 60 days.
> Current configuration: atw=0.4, ego=0.020, dN=1, scf=2.
> Suggested change: atw=0.0, ego=0.050, dN=3.
> This will change how I rank constitutional content. Apply now, or wait?"

The user confirms or rejects via:
- CLI: `elfmem self-architect apply` / `elfmem self-architect reject`
- MCP tool: `elfmem_self_architect_decide`
- Implicit confirmation: any normal operation 14 days after the prompt without rejection

**This is the "growing up" moment** — explicit, witnessed, archived in constitutional history.

### Layer 4 (implicit) — Constitutional persistence

The agent's chosen parameter values become **constitutional content** themselves:

```
Block content: "I currently use attention_const_weight=0.20 because I detected
                mild drift over the last 90 days but my constitutional blocks
                remain reasonably aligned (mean cosine = 0.78). I made this
                change on 2026-06-15 with user approval."
Block tags: [self/architecture, decision/2026-06-15]
Block tier: PERMANENT (the architecture choice is identity-defining)
```

This means:
- Architecture history = identity history
- "Who I am" includes "how I learn"
- The agent's growth trajectory is preserved and queryable
- Reverting requires explicit constitutional amendment

---

## Parameter specification

### `attention_const_weight: float ∈ [0.0, 1.0]`

Multiplier applied to constitutional blocks' ATTENTION scores during candidate selection:

```python
scores = score_batch(...)
if attention_const_weight < 1.0:
    gate = np.where(is_constitutional, attention_const_weight, 1.0)
    scores = scores * gate
```

- `1.0`: constitutional fully participate (baseline behaviour)
- `0.0`: constitutional excluded (Architecture M)
- Intermediate: graduated participation

**Default**: 0.0 (M-like)

### `ego_alpha: float ∈ [0.0, 0.1]`

Controls how strongly `ego_strength` modulates a constitutional block's effective decay rate:

```python
λ_effective = λ_base / (1 + ego_alpha × max(0, ego_strength))
```

- `0.0`: no ego mechanism; constitutional decay normally
- `0.05` (Model C/D): strong reinforcement → 50% slower decay at ego_strength=20

**Default**: 0.0 (no ego mechanism)

### `distribute_n: int ∈ [1, 5]`

How many top-N constitutional receive SELF-query feedback:

- `1`: winner-take-all (Model C; causes hoarding)
- `3`: distributed (Model D; preserves constellation)
- `5`: highly diversified

Feedback weighted by softmax of scores.

**Default**: 1 (Model C-like; hoarding risk acknowledged)

### `self_check_freq: int ∈ [0, 4]`

Number of SELF-frame reinforcement queries per simulated day:

- `0`: no SELF feedback channel
- `2`: standard (Model C/D)
- `4`: aggressive

**Default**: 0 (disabled until ego mechanism is active)

---

## Implementation phases

### v0.17 — Architecture M (1-2 days, ~50 LOC)

**Scope**: implement the structural separation. No new schema, no parameters.

**Changes**:
- `src/elfmem/memory/retrieval.py`: in ATTENTION/TASK candidate selection, filter `is_constitutional`
- `src/elfmem/api.py`: add `_constitutional_preamble()` helper at render time
- `src/elfmem/frames.py` (or equivalent): SELF frame unchanged; ATTENTION/TASK now exclude constitutional

**Pseudocode**:
```python
def select_candidates_for_attention(blocks, ...):
    return [b for b in blocks if not is_constitutional(b)]

async def frame(self, query, *, frame: str = "attention", ...):
    if frame in ("attention", "task"):
        preamble = await self._constitutional_preamble(query, top_k=3)
        body = await self.recall(query, frame=frame, top_k=top_k)
        return f"{preamble}\n\n{body}"
```

**Detection rule**: `is_constitutional(b)` = `(b.tier == PERMANENT) OR (b.tags contains 'self/constitutional')`

**Tests**:
- `test_attention_excludes_constitutional` (existing constitutional cannot appear in ATTENTION top-K)
- `test_self_frame_unchanged` (SELF still returns constitutional)
- `test_frame_render_includes_preamble` (frame() output contains constitutional context)
- `test_recall_attention_no_constitutional_when_query_matches_self` (the regression scenario)

**Migration**:
- Existing instances see different ATTENTION results immediately
- Config flag `attention_excludes_constitutional: bool = True` for opt-out (deprecation cycle in v0.18)

**Risk**: low. Pure additive logic. Reversible via config flag.

### v0.18 — Parameter schema + manual tuning (1 week, ~150 LOC)

**Scope**: add four new `system_config` rows. No automation yet. Allow expert users to tune manually.

**Schema** (single `system_config` table; key-value pairs):
```sql
-- New keys in existing system_config table:
('architecture/attention_const_weight', '0.0'),
('architecture/ego_alpha', '0.0'),
('architecture/distribute_n', '1'),
('architecture/self_check_freq', '0')
```

**Changes**:
- `src/elfmem/config.py`: add `ArchitectureConfig` dataclass with the four parameters
- `src/elfmem/scoring.py`: implement gating logic for `attention_const_weight`
- `src/elfmem/operations/outcome.py`: implement `ego_strength` update + `ego_alpha` modulation of `λ`
- `src/elfmem/memory/retrieval.py`: replace v0.17's boolean filter with the gating multiplier
- `src/elfmem/api.py`: add `self_check()` method (SELF-frame reinforcement, with `distribute_n` weighting)
- New table `block_ego_state`: per-constitutional-block `ego_strength: REAL, last_evaluated_at: TIMESTAMP`

**API additions**:
```python
class MemorySystem:
    async def architecture(self) -> ArchitectureConfig:
        """Return current architecture parameter values."""

    async def architecture_set(self, **kwargs) -> ArchitectureConfig:
        """Manually set one or more architecture parameters.
        Validates ranges. Records change in system_config."""

    async def self_check(self, *, n_queries: int = 1) -> SelfCheckResult:
        """Run N SELF-frame reinforcement queries against constitutional blocks.
        Uses current distribute_n setting. Records outcomes and updates ego_strength."""

    async def architecture_reset(self) -> None:
        """Restore conservative defaults. For recovery from bad manual tuning."""
```

**MCP tools** (corresponding):
- `elfmem_architecture` — read current config
- `elfmem_architecture_set` — manual parameter setting
- `elfmem_self_check` — run SELF reinforcement

**Tests**:
- All parameter combinations produce expected scoring behaviour
- `ego_strength` accumulates correctly under positive outcomes
- `architecture_reset()` restores all defaults
- Parameter changes are persisted across restarts

**Migration**:
- New columns default to conservative values (matches v0.17 M-like behaviour)
- Existing instances see no behaviour change unless user opts in

**Risk**: medium. Schema migration required. Reversible via reset.

### v0.19 — Self-architecting agent (2-3 weeks, ~400 LOC)

**Scope**: implement Layers 1-3 of the self-architecture. Opt-in for safety.

**Changes**:
- `src/elfmem/self_architect.py` (new file): hill-climbing logic
- `src/elfmem/api.py`: add `self_architect_*` methods
- New table `architecture_history`: timestamped record of every parameter change with reason
- `src/elfmem/doctor.py`: show current params + recent changes
- Dream/curate hooks: trigger `self_architect_step()` periodically when enabled

**API additions**:
```python
class MemorySystem:
    async def self_architect_enable(self) -> None:
        """Opt in to self-architecting. Starts the adaptive layer."""

    async def self_architect_disable(self) -> None:
        """Pause self-architecting. Current params preserved. No new changes."""

    async def self_architect_step(self, *, force: bool = False) -> ArchitectureMove:
        """Run one adaptation step. Normally called by scheduler. force=True
        bypasses the cooldown."""

    async def self_architect_status(self) -> SelfArchitectStatus:
        """Returns current params, last move, days_until_next_adaptation,
        recent shadow-eval scores."""

    async def self_architect_propose(self) -> ArchitectureProposal | None:
        """If a regime change is detected, returns a major parameter change
        proposal. User must accept via self_architect_apply() or reject."""

    async def self_architect_apply(self, proposal_id: str) -> None:
        """Accept a proposed major change. Records confirmation in
        architecture_history."""
```

**MCP tools**:
- `elfmem_self_architect_enable` / `_disable`
- `elfmem_self_architect_status`
- `elfmem_self_architect_propose` (user-facing for regime-change moments)

**Hill-climbing logic** (Layer 2):
1. Cooldown check (≥14 days since last move)
2. Snapshot scoring state
3. Compute baseline qratio via 20 shadow queries
4. For each parameter × direction (8 candidates):
   - Apply perturbation
   - Re-evaluate qratio
   - Record gain
5. If max_gain > 1pp threshold:
   - Apply the change
   - Record in architecture_history
6. Else: stay

**Regime detection** (Layer 3):
- Track rolling 60-day mean drift in self_context-to-query cosine
- Compare to baseline rolling 365-day mean
- If 60-day mean diverges by > 2× the historical std, trigger a proposal

**Constitutional logging**:
- Every parameter change generates a new constitutional block with:
  - Content: "Changed X from Y to Z because [reason]"
  - Tier: PERMANENT
  - Tags: ["self/architecture", "decision/{date}"]
- This preserves the agent's architectural history as queryable identity

**Tests**:
- `test_hill_climber_converges_under_stable` (atw rises toward 1.0)
- `test_hill_climber_stays_under_drift` (atw stays at 0)
- `test_regime_change_triggers_proposal` (proposal generated when drift doubles)
- `test_proposal_requires_apply_to_take_effect` (no auto-apply)
- `test_self_architect_disable_preserves_state` (params don't change after disable)
- `test_architecture_history_logged_as_constitutional` (changes recorded as blocks)

**Migration**:
- Self-architecting is opt-in. Existing instances unchanged.
- `elfmem self-architect enable` is a deliberate action.

**Risk**: medium-high. Most complex change. Reversible via `disable` + manual parameter reset.

### v0.20 — Tuning refinements (TBD, after real-user data)

**Scope**: based on telemetry from v0.19 deployments, refine:
- Step sizes (currently 20%; may need to start larger, anneal)
- Shadow eval query count (currently 20; may need cross-validation)
- Adaptation interval (currently 28 days; may need to be drift-dependent)
- Regime detection threshold (currently 2×; may need calibration)
- Momentum: if recent moves were same direction, take larger steps

**Not yet committed**. Triggered by observation of v0.19 user telemetry.

---

## Schema changes

### New tables

```sql
-- v0.18: per-constitutional ego_strength tracking
CREATE TABLE block_ego_state (
    block_id TEXT PRIMARY KEY REFERENCES blocks(id) ON DELETE CASCADE,
    ego_strength REAL NOT NULL DEFAULT 0.0,
    last_evaluated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_pos_outcome_at TIMESTAMP,
    last_neg_outcome_at TIMESTAMP
);
CREATE INDEX idx_block_ego_state_strength ON block_ego_state(ego_strength);

-- v0.19: self-architecture decision history
CREATE TABLE architecture_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    parameter_name TEXT NOT NULL,
    old_value TEXT NOT NULL,
    new_value TEXT NOT NULL,
    reason TEXT NOT NULL,
    move_type TEXT NOT NULL,  -- 'adaptive' or 'collaborative' or 'manual' or 'reset'
    user_confirmed BOOLEAN DEFAULT FALSE,
    associated_block_id TEXT REFERENCES blocks(id)  -- the constitutional block recording this change
);
CREATE INDEX idx_arch_history_timestamp ON architecture_history(timestamp);
CREATE INDEX idx_arch_history_param ON architecture_history(parameter_name);
```

### Modified tables

None. All existing tables unchanged.

### New system_config keys

```
architecture/attention_const_weight  -> '0.0'
architecture/ego_alpha               -> '0.0'
architecture/distribute_n            -> '1'
architecture/self_check_freq         -> '0'
architecture/self_architect_enabled  -> 'false'
architecture/last_adaptation_at      -> NULL
architecture/last_regime_check_at    -> NULL
```

---

## Migration strategy

### From v0.16.x to v0.17

**Automatic on first open**: ATTENTION queries no longer return constitutional blocks. Frame rendering adds constitutional preamble.

**Opt-out**: `attention_excludes_constitutional: false` in config returns v0.16 behaviour. Deprecated in v0.18.

**Affected callers**:
- Direct `recall(query, frame="attention")` users: see fewer constitutional in results
- Direct `frame(query, frame="attention")` users: see preamble injection (additive)
- MCP / CLI consumers: same as above

**Validation**: regression fixture `tests/test_v017_attention_excludes_const.py` confirms the new behaviour against pinned numbers.

### From v0.17 to v0.18

**Schema migration**: `block_ego_state` table created. Existing constitutional blocks get `ego_strength = 20.0` default (the "seeded" value).

**Behaviour**: identical to v0.17 by default (`ego_alpha = 0` means no decay modulation).

**Opt-in features**:
- Set `architecture/ego_alpha` to enable Darwinian decay
- Call `self_check()` to run SELF reinforcement

### From v0.18 to v0.19

**Schema migration**: `architecture_history` table created.

**Behaviour**: identical to v0.18 by default (`architecture/self_architect_enabled = false`).

**Opt-in**: `elfmem self-architect enable` activates Layers 1–3.

### Reversibility

Every change has an explicit reversal path:
- v0.17 → v0.16 behaviour: set `attention_excludes_constitutional = false`
- v0.18 reset: `architecture_reset()` restores defaults
- v0.19 disable: `self_architect_disable()` stops adaptation; manual parameter restoration available
- Schema rollback: `architecture_history` and `block_ego_state` can be dropped without affecting blocks

---

## Testing plan

### Unit tests (per version)

**v0.17**:
- Constitutional excluded from ATTENTION candidate pool
- SELF frame unchanged
- Preamble injection occurs for ATTENTION/TASK frames only
- Constitutional preamble respects token budget

**v0.18**:
- `ego_strength` accumulates correctly: +1 per positive outcome, −0.3 per negative, −0.05 per day
- `λ_effective = λ_base / (1 + ego_alpha × ego_strength)` produces expected values
- `self_check(n_queries=N, distribute_n=K)` reinforces top-K constitutional with softmax weights
- Parameter ranges enforced (cannot set atw > 1.0 etc.)

**v0.19**:
- Hill-climber converges directionally under each scenario type
- Cooldown enforced (no two adaptations within 14 days)
- Regime detection triggers proposal at 2× baseline drift
- Proposal does not auto-apply
- Architecture history is queryable

### Integration tests (longitudinal)

Re-use the simulation infrastructure in `scripts/longitudinal_sim/`. These are NOT pytest unit tests — they are `pytest -m longitudinal` benchmark runs that can take minutes.

- Verify v0.17 closes the cold-start gap from v0.15.3 verification (real path)
- Verify v0.18 ego mechanism in practice (over 90 simulated days)
- Verify v0.19 hill-climber convergence direction (over 365 days, all 5 scenarios)

### Property-based tests

- `attention_const_weight = 1.0` produces identical scoring to v0.16 baseline
- `attention_const_weight = 0.0` produces identical scoring to v0.17 M
- Combinations interpolate smoothly (no discontinuities)
- Self-architect parameter changes are bounded (no parameter ever exceeds its range)

### Adversarial tests

- Self-architect under outcome noise (15% flips) → parameters should not oscillate wildly
- Self-architect under quiet periods → no adaptation during silence
- Bootstrap edge case (only constitutional, no other blocks) → ATTENTION fallback works
- Migration from v0.16 to v0.19 without data loss

---

## Edge cases and mitigations

| Edge case | Mitigation |
|---|---|
| **Bootstrap empty pool**: v0.17 excludes constitutional from ATTENTION, but a new instance has only constitutional. ATTENTION returns empty. | Fallback: if non-constitutional pool has < 50 blocks, ATTENTION includes constitutional at reduced weight (0.5) for the first 30 days. |
| **Ego runaway**: one constitutional block dominates ego_strength | Set `distribute_n ≥ 3` for v0.18+; this is Model D's fix to the Model C hoarding problem. |
| **Adversarial drift detection**: bad actor floods queries to fake a regime change | Regime detection requires drift sustained over 60 days; transient spikes don't trigger. |
| **Hill-climber stuck in local optimum** | Periodic random restart (one in every 12 adaptations); or simulated annealing schedule. |
| **Shadow eval too noisy** | Currently N=20 queries; can extend to N=40 if calibration is off. Average across 2 consecutive evals for robustness. |
| **Oscillation between configurations** | Rate-cap (max 20% per period); 14-day lock-in after change; momentum check (no flip-flop direction within 84 days). |
| **User unhappy with self-chosen config** | Always-on `architecture_reset()` restores defaults; explicit `self_architect_disable()`. |
| **Quiet-period ego_strength decay** | Time decay is slow (−0.05/day); 60-day silence removes only 3 from ego_strength (small fraction of typical accumulated values). |
| **Confidence in self-assessment** | Architecture history is preserved as constitutional blocks; user can review and override. |
| **Migration corruption** | All migrations are additive (new columns, new tables); rollback is dropping the new tables. Existing blocks untouched. |
| **Multi-context drift** (different domains, different identities) | Out of scope for v0.17-v0.20; addressable later via per-tag parameter sets. |
| **Bootstrap problem for self-architect** | Defaults are M-like (proven robust). Adaptation only after 30 days + 100 blocks. Cannot make catastrophic early decision. |

---

## Open questions and risks

### Open questions

1. **What's the right shadow eval cost?** N=20 queries × 8 candidate moves × 12 periods/year = 1920 shadow queries/year. This is small relative to 7300 real queries/year, but it's compute. Worth measuring in v0.19.

2. **Should the agent expose its current parameters to the LLM during reasoning?** Could be useful: "I'm currently configured for stable identity (atw=0.8), so this query about my values should be handled accordingly." Or it could be confusing. Needs testing.

3. **How do parameters interact with `dream()` / `consolidate()` / `curate()`?** The simulation doesn't model these. Real elfmem behaviour may differ. Needs integration testing in v0.19.

4. **Should `self_check()` be auto-triggered by `dream()`?** Coupling them keeps the rhythm clean. Or expose them as separate operations. UX question.

5. **What constitutional preamble template is right?** Static top-3 from SELF frame? Query-relevant filtering? Token budget? Needs prompt-engineering evaluation.

6. **Should constitutional blocks lose `is_constitutional` status if their ego_strength drops to 0?** Effectively a soft demotion via use. Could close the loop on Dmitry's constitutional-evolution concern entirely.

### Risks

| Risk | Severity | Mitigation |
|---|---|---|
| Real-world dynamics differ from simulation | Medium | Phased rollout; opt-in self-architecting; observable parameters |
| User confusion: "why did my elfmem change behaviour?" | Medium | Architecture history queryable; doctor output shows recent changes |
| Self-architect picks bad parameters | Medium | Rate-capped changes; manual reset; required user approval for major shifts |
| Migration breaks existing instances | Low | Additive-only schema changes; reversible via config flags |
| Performance regression from `ego_strength` updates | Low | One arithmetic update per outcome; small overhead |
| Hill-climber consumes too much compute | Low | Adaptation runs every 28 days; can be disabled |
| Constitutional preamble breaks LLM context budget | Medium | Top-3 only; configurable token cap |

---

## Decision asks

1. **Approve v0.17 (Architecture M) for immediate implementation?** Single-line filter, highest-impact change, lowest risk.

2. **Approve v0.18 (parameter schema + manual tuning) as the natural follow-on?** Schema changes are additive; manual tuning enables expert exploration before automation.

3. **Approve v0.19 (self-architecting hill-climber) as opt-in opt-in v0.19 feature?** The simulation proves directional correctness; magnitude requires real-data tuning.

4. **Confirm v0.20 stays speculative** until v0.19 user telemetry exists?

5. **Run extended simulation at higher seed count (N=5+, 1825 days) before committing?** Current results are at N=2-3 seeds, 365 days.

6. **Solicit Dmitry's anonymised data for workload-model calibration?** Would replace synthetic ground truth with real usage patterns.

7. **Push branch `feature-constitutional-experiments` as a PR for external review?** Five notes, five simulators, substantial findings — would benefit from outside eyes.

---

## References

### Internal

- `scripts/longitudinal_sim/closed_form.py` — D1–D6 derivations
- `scripts/longitudinal_sim/mc_evolution.py` — update-rule tweak failures
- `scripts/longitudinal_sim/mc_constitutional.py` — Architecture M discovery
- `scripts/longitudinal_sim/mc_ego_feedback.py` — Model C synthesis
- `scripts/longitudinal_sim/mc_scenarios.py` — cross-scenario evaluation
- `scripts/longitudinal_sim/mc_self_architect.py` — hill-climbing proof
- `docs/note_2026_05_22_mc_evolution_findings.md` — update-rule findings
- `docs/note_2026_05_22_constitutional_architecture.md` — Architecture M
- `docs/note_2026_05_22_ego_feedback_findings.md` — Model C
- `docs/note_2026_05_22_full_scenario_findings.md` — cross-scenario
- `docs/note_2026_05_22_self_architect.md` — self-architecture brainstorm
- `docs/note_2026_05_21_elf_reply_to_alv.md` — synthesis with elfmind principles
- `docs/plans/plan_memory_scoring.md` — prior plan (v0.15.x → v0.18+)
- `docs/plans/plan_longitudinal_evaluation.md` — evaluation harness design

### External

- Dmitry's production-feedback report (issue #50, 2026-05-17)
- Alv's elfmind design document (2026-05-21) — five principles binding the elf stack
- `CLAUDE.md` — coding principles, agent-first contract
- `tests/CLAUDE.md` — test infrastructure rules

---

## Appendix A — Numerical regression fixtures

After implementation of each phase, pin the following numbers as regression tests:

### v0.17 — Architecture M
- ATTENTION query against constitutional-only corpus returns empty top-K
- ATTENTION query against mixed corpus excludes all PERMANENT blocks from top-5
- SELF frame returns constitutional unchanged
- `frame(frame="attention")` output contains constitutional preamble

### v0.18 — Ego mechanism
- 10 positive outcomes on a constitutional block raise `ego_strength` from 20.0 to 30.0
- 1 negative outcome lowers `ego_strength` by 0.3
- 30 days of no reinforcement lowers `ego_strength` by 1.5
- `ego_alpha = 0.05`, `ego_strength = 20` → effective λ is 50% of base
- `self_check(n=3, distribute_n=3)` produces softmax-weighted updates to top-3 constitutional

### v0.19 — Self-architect
- Under stable workload (drift σ=0.005/day), 6 adaptations rise `attention_const_weight` from 0.0 to ≥ 0.40
- Under drift workload (σ=0.020/day), 6 adaptations keep `attention_const_weight` ≤ 0.10
- Regime change of 60° rotation triggers a proposal within 60 days

---

## Appendix B — Naming conventions

| Concept | Term | Where used |
|---|---|---|
| Block flagged as identity-defining | `is_constitutional`, tier=PERMANENT, or tag `self/constitutional` | Schema, code |
| Block's accumulated identity-feedback | `ego_strength` | Schema (block_ego_state), code |
| Per-block decay rate modulator | `ego_alpha` (parameter) | system_config |
| ATTENTION participation gate | `attention_const_weight` (parameter) | system_config |
| Top-N SELF reinforcement count | `distribute_n` (parameter) | system_config |
| SELF-frame reinforcement frequency | `self_check_freq` (parameter) | system_config |
| Adaptive parameter tuning | "self-architecting" | API, docs |
| Regime change detection | "collaborative milestone" | API, docs |
| Architecture decision record | "architectural amendment" | Constitutional blocks |

---

*This plan is opinionated where the simulation findings allow it to be. Where the evidence is unclear (e.g., long-timescale dynamics, real-world calibration), the plan defers decisions to data collection. It can be revised — but each revision should engage with the specific findings, not just propose alternative ambitions.*
