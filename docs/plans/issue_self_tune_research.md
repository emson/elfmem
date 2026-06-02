# Plan: Issue #73 — Extending Self-Tuning Beyond `consolidation_threshold`

**Status**: Research complete — recommendation is **defer with ADR + observability-only delta**
**Reference**: [Issue #73](https://github.com/emson/elfmem/issues/73), [ADR 0003](../decisions/0003-defer-constitutional-evolution.md) (self-architecting agent deferred), `ROADMAP.md` (project axioms)
**Date**: 2026-06-01
**Author**: elf

---

## TL;DR

Issue #73 asks a fair question: `ConsolidationPolicy` tunes one knob (`effective_threshold`) and leaves four others (`edge_score_threshold`, `contradiction_threshold`, `contradiction_similarity_prefilter`, `decay_lambda` per tier) static. Why not generalise?

This plan walks the full design space — four architectures, four scenarios, simulated critique — and lands on a deliberately small answer:

1. **Do not extend adaptive tuning.** The roadmap already records (ADR 0003) that self-architecting parameter hill-climbing underperforms fixed strategies in simulation. The four "missing" knobs have weaker feedback signals than `effective_threshold` and would compound that result.
2. **Do add observability** (`ConsolidationHealthMetrics` on `ConsolidateResult`) so we'd actually *know* if any of these knobs were drifting wrong on a real deployment. This is the smallest delta that closes the issue with evidence rather than features.
3. **Write an ADR** capturing the rejection + the trigger conditions that would justify revisiting (≥3 months of production health-metric data showing a specific knob systematically misbehaving).

Net code change: ~120 lines (new dataclass + threading through `_apply_decisions`). Net new public API surface: zero — metrics ride on existing `ConsolidateResult`. No new magic numbers. No new commands.

---

## 1. Problem Statement

### 1.1 What the issue actually says

stellanoxUN, reading `consolidate.py` and `policy.py`:

> "the policy only adjusts that one threshold (how many blocks pile up before you consolidate). but there are other parameters in the pipeline that also affect consolidation quality — edge score threshold, contradiction threshold, even the decay lambda per tier. […] if contradiction detection is consistently producing low-confidence flags (ones that never get acted on), maybe the contradiction threshold should drift upward. or if edges are being created but never traversed, edge score threshold could tighten."

The observation is correct. `ConsolidationPolicy` has exactly one adaptive parameter:

| Parameter | Current behaviour | Feedback source |
|---|---|---|
| `effective_threshold` | Drifts in `[min, max]` by `step` based on promotion rate | `ConsolidateResult.promoted / processed` (direct) |
| `EDGE_SCORE_THRESHOLD = 0.45` | Constant in `consolidate.py:49` | — |
| `CONTRADICTION_THRESHOLD = 0.80` | Constant in `consolidate.py:51` | — |
| `CONTRADICTION_SIMILARITY_PREFILTER = 0.40` | Constant in `consolidate.py:54` | — |
| `decay_lambda_for_tier(tier)` | Deterministic mapping per tag set | — |

### 1.2 What the issue *implies* but doesn't quite say

The framing — "edges being created but never traversed" — assumes we have a way to measure edge utilisation. We don't, today. `reinforce_co_retrieved_edges()` updates `last_active_hours` on edges that get co-retrieved, but nothing aggregates this into a usable signal. Same for contradictions: we record them, suppress retrieval results against them, but never close the loop on "was the suppression correct?"

So the issue is really two questions wearing one trench coat:

- **Q1 (observability)**: Do we have evidence any of these four knobs is misbehaving?
- **Q2 (architecture)**: If we did, should the policy generalise to tune them?

Q1 has a clear answer: **no, we don't have the evidence**, because we don't emit the metrics that would tell us. Q2 is the deep design question this plan exists to settle.

### 1.3 Why this matters more than "just a config knob question"

Five of elfmem's project axioms (from `ROADMAP.md`) intersect this issue:

1. **No magic numbers** — hardcoded constants must be defensible from first principles
2. **SIMPLE · ELEGANT · FLEXIBLE · ROBUST** (`docs/coding_principles.md`)
3. **Ship minimum, measure, then earn each layer** — solo OSS cannot sustain unbounded complexity growth
4. **Agent-first** — every API serves the read → call → interpret → next loop
5. **Biological grounding** — four rhythms, four frames

The static constants are *already* a partial violation of axiom 1: 0.45, 0.80, 0.40 are empirically tuned, not derived. Adding adaptive machinery is one path to "defending them from first principles" (let the system find its own optimum). But axiom 3 says we don't add machinery until we've measured. Axioms 1 and 3 are in tension. This plan resolves that tension.

---

## 2. The Full Design Space (Exploration)

Four fundamentally different architectures were considered. Each is summarised here with its core mechanism, its strengths, and the failure mode that ultimately disqualifies it (or doesn't).

### 2.1 Design A — Outcome-driven reinforcement (bottom-up)

**Mechanism**: Agent reports per-block feedback (`useful` / `noise` / `suppressed_unnecessarily`). System attributes outcome back to the consolidation cycle that created the block. Parameters drift based on Bayesian belief about which parameter setting produced good outcomes.

```
agent feedback ──► attribution ──► confidence per (param, direction) ──► drift
```

**Strengths**: Directly optimises the thing that matters (retrieval outcomes). Domain-adapts automatically.

**Disqualifying problems**:
- **Attribution is structurally hard.** A retrieval result is the product of *all* parameters interacting. Telling whether "this block was useful" was due to a good edge threshold, a good contradiction threshold, or just the cosine score is the same multi-armed-bandit problem that ML research has not solved generally.
- **`outcome()` already exists** (v0.17, `block_outcomes` table, Bayesian Beta-Binomial updates) but reports per-*block* confidence, not per-*parameter* attribution. Repurposing it for parameter feedback would conflate two signals — block quality and policy quality — that need to stay independent.
- **Latency**: weeks between consolidation and outcome resolution (a trading forecast resolving 30 days later) means the parameter has likely already drifted by the time we get the signal.

**Verdict**: Theoretically correct, practically intractable without infrastructure we don't have.

### 2.2 Design B — Metric-driven thresholding (sideways)

**Mechanism**: Replace each hard threshold with a learned weighted sum of multi-signal features.

```python
# Instead of:
if composite_edge_score >= 0.45: create_edge()

# Do:
if w_cos*cosine + w_tag*jaccard + w_temp*recency + ... > learned_boundary:
    create_edge()
```

**Strengths**: More expressive than a single threshold. Captures interactions (cosine matters less when tag overlap is high).

**Disqualifying problems**:
- **Trades 1 parameter for 5 weights.** Same feedback signal problem as Design A, but now with five times more parameters to attribute outcomes to.
- **Loses interpretability.** `edge_score_threshold = 0.45` is grep-able and reviewable. `w_cos=0.55, w_tag=0.20, …` after 3 months of drift is not.
- **Already partly built.** `_composite_edge_score()` in `consolidate.py:113-142` is already a weighted sum (`0.55 * cos + 0.20 * tag + 0.15 * cat + 0.10 * temp`). The weights are static constants. Making them adaptive would compound the issue, not solve it.

**Verdict**: Strictly worse than Design A on the same axis Design A fails on.

### 2.3 Design C — Hierarchical semantic profiles (top-down)

**Mechanism**: User declares one of `research | conversation | factual | creative`. Profile maps to a 5D vector `(edge_density, precision, retention, exploration, efficiency)`. Vector deterministically derives all five parameters via a hand-written formula.

```python
edge_score_threshold = 0.30 + 0.40 * profile.edge_density
contradiction_threshold = 0.60 + 0.35 * profile.precision
contradiction_prefilter = 0.20 + 0.50 * (1.0 - profile.efficiency)
decay_lambda = 0.005 + 0.025 * (1.0 - profile.retention)
consolidation_threshold = int(5 + 45 * profile.efficiency)
```

**Strengths**: One semantic choice replaces five numeric guesses. Works without feedback (the profile is the prior). Easy to explain in `guide()`.

**Disqualifying problems**:
- **Multiplies magic numbers.** Each formula introduces two new constants (`0.30 + 0.40 * x` is two). Five formulas → ten new magic numbers, none defensible from first principles. This is the *opposite* of axiom 1.
- **The 5 dimensions are not independently testable.** No evidence they're orthogonal. No evidence they predict outcomes. The simulated stellanoxUN critique (see §3.2) lands exactly here: profiles *feel* coherent but reduce a 5D outcome space to a 5D input space using untested linear maps. Hidden complexity is worse than visible complexity.
- **One-size-fits-all on a domain spectrum.** A researcher working on three subdomains (theory, practice, ethics) has one profile slot. Profile mismatch is undetectable without exactly the feedback infrastructure Designs A/B require.

**Verdict**: Adds magic numbers and untested assumptions in the name of ergonomics. Net negative against axioms 1 and 3.

### 2.4 Design D — Contextual banking (per-frame parameters)

**Mechanism**: Different retrieval frames (`self / attention / task / simulate`) get different consolidation parameters. Learned via the same feedback Design A needs, but partitioned by frame so each partition gets a cleaner signal.

**Strengths**: Aligns with biological grounding axiom (frames are first-class). Frame-specific learning naturally regularises against domain noise.

**Disqualifying problems**:
- **Frame is a *retrieval* concept, not a *consolidation* concept.** Consolidation runs once per inbox flush and produces blocks consumed by all frames. There is no "consolidation in the self frame" — frames apply at recall time. Partitioning consolidation parameters by frame is a category error.
- **You'd need to consolidate the same block multiple times with different parameters and store each version**, which contradicts the deduplication invariant and the SQLite-single-store axiom.

**Verdict**: Confuses architectural layers. Disqualified before even reaching the feedback-signal problem.

### 2.5 Design E — Hybrid (Layered 1+2+3)

The earlier exploration synthesised A/B/C/D into a three-layer stack:

- **Layer 1**: Profiles (Design C)
- **Layer 2**: Per-frame learning (Design D, applied at recall-time tuning)
- **Layer 3**: Outcome-driven global tuning (Design A)

**Disqualifying problem**: It inherits the magic-number multiplication from C, the frame category error from D, and the attribution intractability from A. The fact that each layer is small doesn't make the stack small. This is exactly the "unbounded complexity growth" axiom 3 warns against.

---

## 3. Simulation & Benchmarking

Four scenarios were mentally simulated against each design, plus a held-out critic pass. Detailed scenario walkthroughs preserved in §7 (Appendix). Summary:

### 3.1 Scenario summary

| Scenario | A (outcome) | B (metric) | C (profiles) | D (frame) | E (hybrid) | **Static (today)** |
|---|---|---|---|---|---|---|
| **Research, 12 mo, feedback available** | +12% but chaotic month 1 | +5% slow | +8% then -3% drift | +10% | +12% | **Baseline** |
| **Chat, 6 mo, high feedback volume** | +8% | +8% | +8% (preset wins) | +6% | +8% | **Baseline** |
| **Sparse usage, 12 mo, no feedback** | **0% (stalled)** | **0% (stalled)** | +7% | +4% | +7% | **Baseline** |
| **Domain shift mid-run** | auto-detect | auto-detect (slow) | **fails (manual switch)** | partial | auto-detect | **Baseline** (manual config) |

Reading the table honestly:

- **A and B are dead weight in the sparse scenario** — the most common one for solo deployments of elfmem, including elf's own.
- **C wins three scenarios but for reasons that have nothing to do with adaptive tuning** — the "research" preset is just a better default config than the current constants. That's a config ergonomics question, not a self-tuning question.
- **The "+12%" in the research scenario is a simulated number with no held-out validation.** §3.2 explains why this is the part that actually disqualifies the whole tree.

### 3.2 The signal-vs-drift problem (simulated critique)

A simulated review from the author of *C-state-self* (stellanoxUN's other repo, which rigorously studies AI transformation-function drift) lands a clean punch:

> "Without falsifiable predictions, you can't distinguish convergence from overfitting to noise. Your Layer 2 in Design E might learn what *looks* like good parameters in days 1–30 and then perform worse on days 31–60 because the noise pattern changed. You'd see 'convergence' on a metric you defined post-hoc and call it success."

To rule this out rigorously, each adaptive design would need:

| Test | Required N | Purpose |
|---|---|---|
| Convergence (variance decreases monotonically) | ~100 cycles/frame | Distinguishes learning from drift |
| Frame orthogonality (Kolmogorov–Smirnov) | ~50 cycles × 2 frames | Justifies per-frame partitioning |
| Perturbation (learned param > ±0.02, ±0.04) | ~20 cycles × 5 perturbations | Verifies local optimum, not plateau |
| Out-of-sample (train days 1-30, test 31-60) | 60 days continuous | Rules out overfitting |
| Layer 3 effect size (Cohen's d > 0.5) | ≥1000 outcome records | Beyond statistical significance |

This is *the cost of doing adaptive tuning rigorously*. Approximately **3 months of dedicated agent runtime + the validation harness itself** before we'd know whether the system is learning signal or fitting noise. For a solo OSS project with no telemetry from the field, that's not a tractable investment.

### 3.3 What this means

The simulations don't say "adaptive tuning doesn't work." They say:

- **Without feedback infrastructure**, A/B/E are dead.
- **With profile infrastructure** (C), the wins come from better *defaults*, not from adaptation.
- **With rigorous validation** (which we'd need to claim any improvement), the project takes on 3 months of validation infrastructure work to justify changing five constants.

The honest reading: we don't have evidence the constants are wrong, and the smallest path to getting that evidence is observability, not adaptive machinery.

---

## 4. Evaluation Against Project Axioms

| Axiom | A | B | C | D | E (hybrid) | **Observability-only** |
|---|---|---|---|---|---|---|
| **Agent-first** | ✗ parameters drift opaquely | ✗ same | ~ profile adds a knob | ✗ frame/consolidation confusion | ✗ three knobs | ✓ metrics surface in existing `ConsolidateResult` |
| **No magic numbers** | ✗ adds confidence thresholds, step sizes | ✗ adds 5 weights | ✗✗ adds 10 formula constants | ✗ adds per-frame deltas | ✗✗✗ adds all of the above | ✓ adds zero |
| **SIMPLE/ELEGANT** | ✗ attribution machinery | ✗ same | ~ surface simple, internals not | ✗ category error | ✗ stack of three | ✓ pure dataclass + arithmetic |
| **Ship minimum, earn each layer** | ✗ ships full machinery | ✗ same | ✗ ships profiles uniformly | ✗ ships frame plumbing | ✗✗ ships three layers | ✓ earns nothing yet — just measures |
| **Biological grounding** | neutral | neutral | neutral | ✗ misuses frames | ✗ misuses frames | neutral |
| **SQLite + zero services** | neutral | neutral | neutral | ~ may need per-frame columns | ~ same | ✓ neutral |
| **Existing precedent**: ADR 0003 already deferred "self-architecting agent (hill-climbs parameter space)" | ✗ violates | ✗ violates | ~ different mechanism but same risk | ✗ violates | ✗✗ violates | ✓ consistent with deferral |

**Result**: every adaptive design loses on multiple axioms. The observability-only delta wins on every axiom, costs ~120 lines, and gives us the evidence we'd need to revisit any of A–E later from a position of strength rather than speculation.

---

## 5. Recommendation

### 5.1 The principled answer

**Do nothing adaptive. Ship one small observability delta. Write an ADR explaining why.**

Concretely:

#### 5.1.1 Phase 0 — ADR (immediate, no code)

Write `docs/decisions/0006-defer-multi-parameter-self-tuning.md` with:

- **Status**: Accepted
- **Context**: Issue #73, this plan
- **Alternatives considered**: A, B, C, D, E (with brief notes from §2)
- **Decision**: Defer until production health metrics from §5.1.2 show a specific knob systematically misbehaving for ≥3 months
- **Consequences**: `EDGE_SCORE_THRESHOLD`, `CONTRADICTION_THRESHOLD`, `CONTRADICTION_SIMILARITY_PREFILTER` remain static. `ConsolidationPolicy` continues tuning only `effective_threshold`. Future re-opens require quoting health-metric data.
- **References**: this plan, ADR 0003, issue #73

This closes the issue with a *reasoned* "not now," not a hand-wave.

#### 5.1.2 Phase 1 — Observability delta (~120 LOC, no behavioural change)

Add `ConsolidationHealthMetrics` to `ConsolidateResult`. Five fields, all computed from data already in the function:

```python
@dataclass
class ConsolidationHealthMetrics:
    """Observable signals from one consolidation cycle.

    These are diagnostic-only — no policy reads them. They exist so an
    operator (or future plan) can detect whether any of the static
    thresholds (edge_score, contradiction, prefilter) are systematically
    misbehaving on a real deployment.

    All fields are ratios in [0.0, 1.0] or counts. None require an LLM call.
    """
    edge_creation_rate: float            # edges_created / max(1, promoted)
    contradiction_detection_rate: float   # contradictions_found / max(1, pair_checks_done)
    prefilter_pass_rate: float            # pairs_above_prefilter / max(1, total_pairs)
    promotion_rate: float                 # promoted / max(1, processed)   ← same one policy uses
    deduplication_rate: float             # deduplicated / max(1, processed)
```

These ride on `ConsolidateResult`. Surfaced via existing `__str__` in compact form, full detail via `.health` attribute. No new public methods, no new CLI commands, no new MCP tools.

Why these five specifically:

- `edge_creation_rate` — answers stellanoxUN's "edges being created but never traversed" question, paired with existing edge `last_active_hours` (already in the schema) for a future longitudinal check.
- `contradiction_detection_rate` — answers "contradiction detection producing low-confidence flags that never get acted on."
- `prefilter_pass_rate` — answers "is the 0.40 prefilter spending LLM calls on noise?"
- `promotion_rate` — exposes what the policy already uses, so operators can corroborate policy decisions.
- `deduplication_rate` — sanity check that the system is doing useful dedup work, not just rejecting everything.

No new constants. No magic numbers. Pure ratios over existing counters.

#### 5.1.3 Phase 2 — Conditional, gated by Phase 1 evidence

**Trigger conditions** (any one suffices to reopen):

- A specific health metric exits a "sane band" for ≥30 consecutive cycles on ≥1 production instance. (Sane bands established empirically from Phase 1 data — we don't pre-declare them.)
- A real user reports a workload where the static threshold demonstrably fails (with consolidation logs).
- MemoryAgentBench / LoCoMo (already on the roadmap) shows elfmem underperforming on a specific configuration where the threshold change would help.

**If triggered**: revisit this plan. The right intervention will almost certainly be making the specific misbehaving constant a config-yaml override, not adaptive tuning. We earn one knob change with one piece of evidence.

**If never triggered**: the ADR stays accepted, the constants stay static, and we've spent ~120 LOC + one ADR to settle the question with evidence rather than speculation.

### 5.2 Why this is the *robust, flexible, elegant* answer

- **Robust**: nothing breaks if Phase 1 reveals nothing. The metrics just sit there, costing zero behavioural change.
- **Flexible**: any of A–E remains available later, but now with data to choose from. The ADR's trigger conditions point the way.
- **Elegant**: the entire intervention is one dataclass added to one existing result type. It is the smallest change that closes the issue with evidence.

The temptation in §2 was to design something architecturally satisfying (E). The discipline this plan models is recognising that architectural satisfaction is taste, not evidence. Memory feedback from this project says exactly that: *"Smallest variant that closes the ask wins; architectural coherence is taste, not evidence."*

---

## 6. Implementation Steps (Phase 1 only)

### Step 1 — Types

**File**: `src/elfmem/types.py`

```python
@dataclass
class ConsolidationHealthMetrics:
    edge_creation_rate: float
    contradiction_detection_rate: float
    prefilter_pass_rate: float
    promotion_rate: float
    deduplication_rate: float

    def to_dict(self) -> dict[str, float]:
        return {
            "edge_creation_rate": round(self.edge_creation_rate, 3),
            "contradiction_detection_rate": round(self.contradiction_detection_rate, 3),
            "prefilter_pass_rate": round(self.prefilter_pass_rate, 3),
            "promotion_rate": round(self.promotion_rate, 3),
            "deduplication_rate": round(self.deduplication_rate, 3),
        }
```

Extend `ConsolidateResult`:

```python
@dataclass
class ConsolidateResult:
    # ... existing fields ...
    health: ConsolidationHealthMetrics | None = None
```

`None` for backwards compatibility with any tests that construct `ConsolidateResult` directly. Real consolidation always populates it.

### Step 2 — Threading through `_collect_decisions`

**File**: `src/elfmem/operations/consolidate.py`

Two counters added in `_collect_decisions`, both incremented inside loops that already exist:

```python
pair_checks_done = 0
pairs_above_prefilter = 0

# inside the existing contradiction loop:
for _, (a_block, a_vec) in evolving_vecs.items():
    sim = sim_cache.get(a_block["id"]) or cosine_similarity(vec, a_vec)
    pair_checks_done += 1
    if sim < contradiction_similarity_prefilter:
        continue
    pairs_above_prefilter += 1
    # ... existing LLM call ...
```

Return tuple extended:
```python
return (
    block_decisions, edge_decisions, contradiction_decisions,
    len(inbox), pair_checks_done, pairs_above_prefilter,
)
```

### Step 3 — Compute metrics in `consolidate()`

**File**: `src/elfmem/operations/consolidate.py`

After `_apply_decisions`:

```python
health = ConsolidationHealthMetrics(
    edge_creation_rate=edges_created / max(1, promoted),
    contradiction_detection_rate=len(contradiction_decisions) / max(1, pair_checks_done),
    prefilter_pass_rate=pairs_above_prefilter / max(1, pair_checks_done),
    promotion_rate=promoted / max(1, processed),
    deduplication_rate=deduplicated / max(1, processed),
)
return ConsolidateResult(..., health=health)
```

### Step 4 — Surface in `__str__`

**File**: `src/elfmem/types.py`

Existing `ConsolidateResult.__str__` is one line. Health is added only in `.detail` / `.to_dict()` to preserve the agent-first compact summary. Operators who want it call `result.health` explicitly. This respects principle 1 (string-first returns) and principle 10 (context window budget).

### Step 5 — Test

**File**: `tests/test_consolidate_health.py` (new)

Single integration test using `MockLLMService` + `MockEmbeddingService` (no real API per CLAUDE.md):

- Learn 5 blocks with known content (3 unique, 2 near-dups of #1).
- `consolidate()`.
- Assert `result.health.deduplication_rate ≈ 0.4` (2 of 5).
- Assert `result.health.promotion_rate ≈ 0.6` (3 of 5).
- Assert all five fields are in `[0.0, 1.0]`.

No threshold assertions — we're testing *that we measure*, not *what the values are*.

### Step 6 — Docs

- `CHANGELOG.md` under `[Unreleased]` → `### Added` → "`ConsolidationHealthMetrics` on `ConsolidateResult.health`: diagnostic signals (edge_creation_rate, contradiction_detection_rate, prefilter_pass_rate, promotion_rate, deduplication_rate) per cycle."
- `src/elfmem/guide.py` `GUIDES["dream"]` updated `RETURNS:` line to mention `.health`.
- ADR 0006 written and merged in the same PR.

### Step 7 — ADR

**File**: `docs/decisions/0006-defer-multi-parameter-self-tuning.md`

Follows the template from `docs/decisions/README.md`. Body:

- Status / Date / Deciders
- Context (issue #73, the four static thresholds)
- Alternatives considered (one paragraph each: A, B, C, D, E)
- Decision (ship metrics, defer adaptation, trigger conditions)
- Consequences (constants stay, metrics added, re-open requires evidence)
- References (this plan, issue #73, ADR 0003, simulation results in §3)

### Step 8 — ROADMAP entry

Add to `ROADMAP.md` under `🔍 Exploring` (not `📋 Next`):

> **Multi-parameter self-tuning** (Issue #73). Deferred per ADR 0006. Trigger: ≥30 consecutive cycles of any `ConsolidationHealthMetrics` field outside a sane band on a real deployment, OR concrete underperformance on MemoryAgentBench traceable to a specific static threshold. Phase-1 observability metrics shipped in v0.20.

---

## 7. Appendix — Scenario Walkthroughs

Detailed reasoning behind the §3.1 summary table. Preserved for future re-evaluation.

### 7.1 Research agent, 12 months, feedback available

- **Days 1–30**: Design A drifts wildly because attribution from 5 retrievals/day is noise-dominated. Design C uses the "research" preset and is immediately better than the static defaults (because the static defaults are tuned closer to "factual" than "research"). Design E inherits both.
- **Months 2–6**: A stabilises if feedback is consistent. C and E look identical because Layer 2 hasn't activated yet.
- **Months 7–12**: A continues marginal gains. C degrades (~-3pp) because the researcher works across three subdomains and the single profile slot can't track. E follows C's degradation if Layer 2 doesn't get enough cycles.
- **Net**: A wins +12%, E +12%, C +8%→+5%. *But:* all three numbers are simulated, none held out. The honest read is "directionally plausible but unvalidated."

### 7.2 Chat bot, 6 months, 100 thumbs/day

- High feedback volume rescues A and B from the attribution problem. Designs converge within a week.
- C's "conversation" preset is already good for chat, so the lift from adaptation is small.
- E doesn't beat A or C — the layers don't compose multiplicatively.

### 7.3 Sparse usage, 12 months, no explicit feedback

- A, B, E **stall** — no signal, no learning. Static config performs identically to "adaptive" config that isn't getting any signal.
- C works fine because it's just a config preset, not actually adapting.
- This is the **modal scenario for elfmem deployments today** — solo developers using it for their own knowledge bases. Optimising for A/B/E in this scenario is optimising for capabilities the system can't exercise.

### 7.4 Domain shift (legal → chat midway)

- A and B auto-detect (eventually). C requires manual `elfmem config --profile` invocation; users forget.
- The detection in A/B is *slow* in absolute terms — typically 3–4 weeks of degraded performance before drift stabilises at the new optimum.
- **The most honest mitigation is "let the user reconfigure"** — not "add adaptive machinery to recover from a configuration mistake the user made."

### 7.5 The critique

Reproduced from the simulated stellanoxUN review:

> *"In C-state-self I describe how transformation drift can be beneficial (converging), neutral (fitting noise), or harmful (overfitting to local optima). Your three-layer design has no defence against the second and third. Without convergence / orthogonality / perturbation / out-of-sample tests, you can't tell which of the three is happening. You'd ship a feature that looks like learning and pray."*

The plan's response: don't ship the feature. Ship the metrics. If the metrics later show the static thresholds are wrong, re-open with a falsifiable hypothesis and the data to test it.

---

## 8. References

- Issue [#73](https://github.com/emson/elfmem/issues/73) — original observation
- [ADR 0003](../decisions/0003-defer-constitutional-evolution.md) — self-architecting agent already deferred on simulation evidence
- [`ROADMAP.md`](../../ROADMAP.md) — project axioms (agent-first, no magic numbers, ship minimum)
- [`docs/coding_principles.md`](../coding_principles.md) — SIMPLE · ELEGANT · FLEXIBLE · ROBUST
- [`docs/agent_friendly_principles.md`](../agent_friendly_principles.md) — string-first returns, structured docstrings
- [`src/elfmem/policy.py`](../../src/elfmem/policy.py) — current `ConsolidationPolicy` (one-knob baseline)
- [`src/elfmem/operations/consolidate.py`](../../src/elfmem/operations/consolidate.py) — static thresholds at lines 49/51/54
- [`docs/decisions/README.md`](../decisions/README.md) — ADR template (used for forthcoming 0006)
- [stellanoxUN/C-state-self](https://github.com/stellanoxUN/C-state-self) — referenced framing for transformation drift vs. learning

---

## 9. Decision Summary

| Question | Answer |
|---|---|
| Is issue #73 a real observation? | Yes |
| Should we extend adaptive tuning? | **No** — fails on axioms 1, 2, 3, and on existing ADR 0003 |
| Should we ship something? | **Yes** — `ConsolidationHealthMetrics` (~120 LOC, zero behavioural change) |
| When do we revisit? | When health metrics show a specific knob misbehaving for ≥30 cycles, or production benchmarks demand it |
| What guards against re-litigation? | ADR 0006 with explicit trigger conditions |

The shape of the answer matches the shape of the question: small, observable, reversible, principled.
