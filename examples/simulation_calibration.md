# Simulation-Based Calibration — Design Document

## The Problem

Current elfmem calibration is **retrospective**:

```
ACT → wait for reality → OBSERVE → COMPARE → SIGNAL
```

This is slow. In some domains (geopolitics, strategy, long-running projects),
feedback arrives weeks or months later — or never arrives at all. The agent
can't calibrate what it can't observe.

## The Insight: Simulate First, Act Better

**Proactive calibration** runs scenarios *before* acting:

```
RECALL → SIMULATE (many scenarios) → SCORE predictions → CALIBRATE → ACT with better priors
     ↓                                                                        ↓
     └──── when reality arrives: RESOLVE → Brier score → deep calibration ────┘
```

An LLM agent's "simulation" is **narrative generation** — "if X happens, then
plausibly Y follows." This is how humans simulate: scenario planning, war-gaming,
pre-mortems. The LLM's ability to generate plausible narratives IS the simulation
engine.

The mathematical payoff: you can run 10 simulations in seconds and learn from
the *distribution* of outcomes before committing to action.

---

## The Fourth Rhythm: Imagination

elfmem has three rhythms. Simulation adds a fourth:

| Rhythm | Operation | Timescale | Direction |
|--------|-----------|-----------|-----------|
| Heartbeat | learn/remember | milliseconds | Past → Memory |
| Breathing | dream/consolidate | seconds | Memory → Structure |
| Sleep | curate | minutes | Structure → Health |
| **Imagination** | **simulate** | **seconds** | **Memory → Future** |

Imagination is the only rhythm that looks *forward*. It generates possible
futures from current knowledge and uses the distribution of those futures to
calibrate memory *before* reality provides feedback.

---

## How Simulation Works

### Step 1: Recall Knowledge

Standard elfmem retrieval, multi-frame:

```python
world_knowledge = recall("EU-China trade dynamics", frame="world", top_k=10)
self_knowledge  = recall("my analytical framework", frame="self", top_k=3)
task_context    = recall("current question: carbon tariff impact", frame="task", top_k=5)
```

### Step 2: Identify Key Uncertainties

From the recalled knowledge, extract the decision points where the future could
branch. These are the simulation's degrees of freedom.

```
Uncertainties for "EU carbon tariffs on China":
  1. China's response strategy (retaliate / adapt / negotiate)
  2. EU internal cohesion (unified / fractured)
  3. Third-party positioning (US / ASEAN alignment)
  4. Timeline (immediate / gradual / delayed)
```

### Step 3: Generate Scenarios

For each combination of key uncertainties, generate a plausible narrative.
Not exhaustive (5 uncertainties × 3 options = 243 scenarios) — curated
to 5-10 distinct, representative scenarios plus mandatory extras.

**Mandatory scenario types:**

| Type | Purpose | Minimum probability |
|------|---------|---------------------|
| Base case | Most likely outcome | — |
| Optimistic | Best plausible outcome | — |
| Pessimistic | Worst plausible outcome | — |
| Adversarial | Deliberately contradicts the strongest belief | — |
| Wildcard | Unforeseen factor outside current knowledge | ≥ 0.10 |

The adversarial scenario is critical. Without it, simulation becomes a
self-reinforcing echo chamber — the agent generates scenarios that confirm
what it already believes, scores them against that same knowledge, and
reinforces the original belief. The adversarial scenario forces genuine
epistemic challenge.

### Step 4: Assign Probabilities

**Critical: separate from scenario generation** to avoid anchoring bias.

Generate all scenarios first, then assign probabilities in a distinct step.
This prevents the first-generated scenario from anchoring at high probability
with remaining scenarios splitting the remainder.

Elicitation protocol (borrowed from forecast tournaments):
1. Score each scenario 0-100 independently
2. Normalize so mutually exclusive scenarios sum to ~1.0
3. Reserve ≥ 0.10 for the wildcard (mandatory)

### Step 5: Score Against Knowledge Base

For each scenario, trace back to which recalled blocks support or contradict it:

```
Scenario: "China retaliates with rare earth restrictions"
  Supporting blocks:
    - "China used rare earth leverage against Japan in 2010" (B1, score=0.82)
    - "Rare earth dominance gives China asymmetric trade power" (B2, score=0.71)
  Contradicting blocks:
    - "China diversified rare earth customers since 2015" (B3, score=0.65)
  Knowledge gap:
    - No blocks about EU rare earth alternatives or stockpiles
```

### Step 6: Compute Calibration Signals

Three scores that reveal calibration quality WITHOUT waiting for reality:

**1. Internal Consistency Score**

For mutually exclusive scenarios with probabilities p₁...pₙ:

```
ConsistencyScore = 1.0 - |Σpᵢ - 1.0|
```

- Score = 1.0: Probabilities sum perfectly — internally consistent
- Score < 1.0: Over/underconfident — agent believes too many or too few things

**2. Knowledge Fragility Score**

For scenario Sᵢ supported by m independent blocks:

```
Fragility(Sᵢ) = 1 / m
```

- Fragility = 1.0: Prediction rests on a single block — dangerously fragile
- Fragility = 0.2: Five independent supporting blocks — robust

Overall fragility = weighted average across scenarios:
```
OverallFragility = Σ pᵢ × Fragility(Sᵢ)
```

**3. Coverage Score**

What fraction of the scenario space has knowledge support:

```
Coverage = (scenarios with ≥2 supporting blocks) / total scenarios
```

- Coverage = 1.0: Knowledge covers all plausible outcomes
- Coverage = 0.3: Knowledge gaps dominate — explore before acting

### Step 7: Pre-Calibrate

Before the agent acts, adjust block confidence based on simulation:

```python
# Blocks supporting well-evidenced, internally consistent scenarios
for block_id in consistent_supporting_ids:
    outcome([block_id], signal=0.75, source="simulation_consistent")

# Blocks that led to contradictions between scenarios
for block_id in contradictory_ids:
    outcome([block_id], signal=0.50, source="simulation_contradicted")

# Knowledge gaps discovered
for gap in gaps_found:
    remember(f"Simulation gap: {gap.description}",
             tags=["calibration/simulation-gap", f"domain/{gap.domain}"])
```

Note: pre-calibration signals are WEAKER than real-outcome signals (0.75 vs 0.85).
This is deliberate — simulation is inference, not evidence. Real outcomes get
stronger signals when they arrive.

### Step 8: Resolve (When Reality Arrives)

When the actual outcome is known:

```python
# Match actual outcome to predicted scenarios
matched_scenario = match_to_closest(actual_outcome, scenarios)

# Compute Brier score
brier = (1/N) * Σ(pᵢ - oᵢ)²  # oᵢ=1 if scenario i matched, else 0

# Deep calibration
for scenario in scenarios:
    if scenario == matched:
        outcome(scenario.supporting_ids, signal=0.90, source="simulation_correct")
    else:
        # Only penalize blocks that ONLY supported wrong scenarios
        exclusively_wrong = scenario.supporting_ids - matched.supporting_ids
        outcome(exclusively_wrong, signal=0.30, source="simulation_wrong")

# Meta-learning: remember simulation quality
remember(
    f"Simulation '{situation}': Brier={brier:.2f}. "
    f"Matched scenario at p={matched.probability:.2f}. "
    f"Fragility={fragility:.2f}. Coverage={coverage:.2f}. "
    f"Pattern: {'well-calibrated' if brier < 0.15 else 'needs work'}.",
    tags=["calibration/brier", "meta-learning/simulation"]
)
```

**Brier score interpretation:**

| Brier | Meaning | Action |
|-------|---------|--------|
| 0.00–0.10 | Excellent calibration | Reinforce simulation methodology |
| 0.10–0.20 | Good calibration | Minor adjustments |
| 0.20–0.30 | Mediocre — like informed guessing | Review knowledge gaps |
| 0.30+ | Poor — worse than uniform random | Investigate what went wrong |

---

## Applied Example: Global Politics Simulation

Global politics is the hardest domain for simulation because:
- **No clean feedback** — outcomes take months/years; causation is disputed
- **Reflexive** — predictions change behavior (self-fulfilling / self-defeating)
- **Multi-agent** — actors respond to each other strategically (game theory)
- **Fat-tailed** — black swans dominate (COVID, invasions, revolutions)
- **Narrative-driven** — the "story" matters as much as the "facts"

This makes it the ideal stress-test for the simulation framework.

### Scenario: EU Carbon Border Adjustment Mechanism (CBAM) Impact on China

**Step 1: Build actor models in elfmem**

Each geopolitical actor becomes a cluster of knowledge blocks:

```python
# EU model
remember("EU CBAM: carbon tariff on imports based on embedded emissions. "
         "Targets steel, cement, aluminium, fertilizer. Effective 2026.",
         tags=["actor/eu/policy", "domain/trade"])

remember("EU internal tension: industrialists want exemptions, "
         "greens want strict enforcement. France leads, Germany hesitates.",
         tags=["actor/eu/constraints", "domain/politics"])

# China model
remember("China produces 55% of global steel. EU is 2nd largest export market. "
         "CBAM directly threatens ~$15B/year in trade.",
         tags=["actor/china/exposure", "domain/trade"])

remember("China's historical response to trade pressure: initial retaliation, "
         "then pragmatic negotiation. 2010 rare earth embargo lasted 3 months "
         "before WTO pressure forced reversal.",
         tags=["actor/china/behavior-pattern", "domain/trade-history"])

remember("China's 2060 carbon neutrality pledge creates internal alignment "
         "pressure. CBAM compliance may align with domestic policy goals.",
         tags=["actor/china/internal-dynamics", "domain/climate"])

# Relationship model
remember("EU-China trade: $850B bilateral, deeply interdependent. "
         "Neither side benefits from prolonged disruption. "
         "But asymmetric: EU more exposed in critical minerals, "
         "China more exposed in market access.",
         tags=["relationship/eu-china", "domain/trade"])
```

**Step 2: Generate scenarios**

```
TRIGGER: EU CBAM enters full enforcement, January 2027.

Scenario 1 — "Pragmatic Adaptation" (p=0.35)
  China accelerates decarbonisation of export-facing heavy industry.
  CBAM becomes a catalyst for existing policy. Minor trade disruption.
  EU-China climate cooperation deepens.
  Supporting blocks: China 2060 pledge, internal alignment pressure
  Contradicting blocks: None strong

Scenario 2 — "Retaliation Spiral" (p=0.20)
  China imposes counter-tariffs on EU agricultural exports and
  restricts critical mineral exports. EU splinters as Germany
  breaks ranks. WTO dispute filed. Resolution in 18-24 months.
  Supporting blocks: Historical retaliation pattern, rare earth precedent
  Contradicting blocks: Neither side benefits from prolonged disruption

Scenario 3 — "Supply Chain Rerouting" (p=0.20)
  China reroutes exports through lower-carbon intermediaries
  (Vietnam, Indonesia) to avoid CBAM. EU struggles to enforce
  origin rules. CBAM effectiveness undermined.
  Supporting blocks: Historical trade rerouting patterns
  Contradicting blocks: None (knowledge gap — no blocks on enforcement mechanisms)

Scenario 4 — "Grand Bargain" (p=0.10)
  CBAM triggers a broader EU-China climate deal including carbon
  market linkage, technology transfer, and joint standards.
  Geopolitical relations improve as climate creates common ground.
  Supporting blocks: EU-China climate cooperation history
  Contradicting blocks: Current geopolitical tensions (semiconductor restrictions)

Scenario 5 — ADVERSARIAL: "CBAM Collapse" (p=0.05)
  Internal EU politics kill CBAM enforcement. French election shifts
  priorities. CBAM becomes toothless. China ignores it.
  Supporting blocks: EU internal tensions, industrialist pressure
  Contradicting blocks: Strong institutional momentum, regulatory lock-in

Scenario 6 — WILDCARD (p=0.10)
  Unforeseen factor: e.g., a major climate event accelerates
  global carbon pricing consensus, making CBAM the template
  for a global framework. Or: a technology breakthrough
  (green steel at cost parity) makes CBAM irrelevant.
  Supporting blocks: None (by definition)
  Contradicting blocks: None
```

**Step 3: Score and calibrate**

```
Consistency check:
  Σpᵢ = 0.35 + 0.20 + 0.20 + 0.10 + 0.05 + 0.10 = 1.00 ✓

Fragility analysis:
  Scenario 1: 2 supporting blocks → fragility = 0.50
  Scenario 2: 2 supporting, 1 contradicting → fragility = 0.50
  Scenario 3: 1 supporting block → fragility = 1.00 ← FRAGILE!
  Scenario 4: 1 supporting, 1 contradicting → fragility = 1.00 ← FRAGILE!
  Scenario 5: 2 supporting, 1 contradicting → fragility = 0.50
  Overall fragility: 0.35×0.5 + 0.20×0.5 + 0.20×1.0 + 0.10×1.0 + 0.05×0.5 + 0.10×∞ = 0.60

  VERDICT: Moderately fragile. Scenarios 3 and 4 each rest on a single block.
  ACTION: Seek more knowledge about supply chain rerouting and EU-China climate deals.

Coverage analysis:
  4/6 scenarios have ≥1 supporting block. 2/6 have ≥2.
  VERDICT: Moderate coverage. Several thin spots.

Knowledge gaps discovered:
  - EU CBAM enforcement mechanisms (no blocks)
  - Historical supply chain rerouting patterns (1 weak block)
  - EU-China climate cooperation track record (1 block)
  - Technology trajectories for green steel/cement (0 blocks)

Pre-calibration actions:
  outcome([china_2060_pledge_id, internal_alignment_id],
          signal=0.75, source="simulation_supports_base_case")
  outcome([retaliation_history_id],
          signal=0.65, source="simulation_consistent_but_contradicted")
  remember("Simulation gap: no knowledge of CBAM enforcement mechanisms. "
           "Critical for Scenario 3 (supply chain rerouting).",
           tags=["calibration/simulation-gap", "domain/trade-policy"])
  remember("Simulation gap: green steel technology trajectory unknown. "
           "Could invalidate all scenarios if cost parity arrives.",
           tags=["calibration/simulation-gap", "domain/technology"])
```

**Step 4: What the agent now knows that it didn't before**

Before simulation, the agent had scattered knowledge about EU-China trade.
After simulation:
- It knows its base case is moderately supported but not robust
- It knows supply chain rerouting is a real possibility but poorly evidenced
- It knows there are two critical knowledge gaps to fill
- It has specific blocks tagged as supporting the most likely outcome
- It knows its overall fragility score (0.60 — needs work)

All of this happened WITHOUT any real-world outcome. The agent is better
calibrated before it acts.

---

## The Simulation-Calibration Feedback Loop

Over multiple simulations and resolutions:

```
Simulation 1: predict CBAM impact → Brier = 0.18 (ok)
Simulation 2: predict chip export controls → Brier = 0.35 (poor)
Simulation 3: predict climate summit outcome → Brier = 0.12 (good)

Meta-pattern: Better at trade policy, worse at technology policy.
  → Seed more knowledge in technology domain.
  → Use wider uncertainty bands for technology scenarios.
  → Adjust frame selection: use WORLD frame (broader) for tech topics,
    not TASK frame (narrower).
```

The agent learns not just about the domain but about its own simulation
quality. Brier scores become a calibration signal for the simulation
methodology itself.

```python
# Track simulation quality over time
remember(
    "Simulation meta-pattern: trade policy Brier avg=0.15, "
    "technology policy Brier avg=0.32. "
    "Adjustment: broader frames and more uncertainty for tech scenarios.",
    tags=["meta-learning/simulation-quality", "calibration/meta"]
)
```

---

## Edge Cases and Mitigations

### 1. Echo Chamber (Self-Reinforcing Loop)

**Problem:** Agent generates scenarios from its own knowledge, scores them
against that same knowledge. Beliefs reinforce themselves without external
challenge. Over cycles, the agent becomes confidently wrong.

**Mitigations:**
- **Mandatory adversarial scenario** — forces the agent to articulate a
  plausible world where its strongest belief is wrong. Score it fairly.
- **Pre-calibration signal cap** — simulation-derived signals max out at
  0.75 (not 0.85+). Only real outcomes can drive strong reinforcement.
- **Diversity requirement** — if all scenarios draw from the same ≤3 blocks,
  flag as "intellectually narrow" and expand recall.
- **Periodic adversarial audit** — every N simulations, run one where the
  agent must argue AGAINST its highest-confidence blocks.

### 2. Probability Anchoring

**Problem:** First-generated scenario gets anchored at high probability.
Remaining scenarios split the leftover. This is a well-documented cognitive
bias, and LLMs exhibit it too.

**Mitigations:**
- **Two-step elicitation** — generate all scenarios first, then assign
  probabilities in a separate LLM call with all scenarios visible.
- **Independent scoring** — score each scenario 0-100 independently, then
  normalize. This avoids the "remainder" framing.
- **Shuffle order** — present scenarios in random order during probability
  assignment to break position bias.

### 3. Black Swans (Unknown Unknowns)

**Problem:** The most impactful outcome is one the agent never simulated.
All Brier scores are meaningless when reality falls outside the scenario set.

**Mitigations:**
- **Mandatory wildcard** with minimum 10% probability. This is the agent's
  explicit acknowledgment of its own ignorance.
- **Wildcard hit rate tracking** — if wildcards match reality more than 20%
  of the time, the simulation framework is too narrow. Widen the scenario
  space, add more actors, extend time horizons.
- **Post-hoc wildcard analysis** — when a wildcard hits, decompose it:
  "What kind of event was this? Could I have anticipated it with broader
  knowledge?" Turn the wildcard into a named scenario type for future
  simulations.

### 4. Temporal Horizon Mismatch

**Problem:** Scenarios predict different time horizons. "China retaliates
within weeks" vs "Grand bargain over 3 years" are incomparable. Brier scoring
is meaningless without aligned horizons.

**Mitigations:**
- **Fix the horizon before generating** — "What happens within 6 months?"
  not "What happens eventually?"
- **Multi-horizon simulations** — run separate simulations for 3-month,
  1-year, and 3-year horizons. Each produces its own Brier score.
- **Horizon-tagged blocks** — tag temporal predictions with their horizon
  so calibration correctly matches prediction to outcome.

### 5. Cascading Confidence (Rich-Get-Richer)

**Problem:** Simulation reinforces Block A → A scores higher → A dominates
future simulations → A gets reinforced more → echo chamber at block level.

**Mitigations:**
- **Diminishing returns** — reinforcement above confidence 0.90 has
  progressively less effect. A block at 0.95 gains almost nothing from
  another positive signal.
- **Source diversity requirement** — a scenario supported by only one block
  is flagged as fragile regardless of that block's confidence.
- **Anti-monopoly rule** — if a single block appears in >50% of simulation
  outcomes, flag it for adversarial challenge. No single piece of knowledge
  should dominate the agent's worldview.

### 6. Domain Transfer Failure

**Problem:** Patterns from domain A applied to domain B via simulation.
"Trade wars always end in negotiation" (US-EU historical pattern) applied to
China-EU (different power dynamics, cultural context, institutional constraints).

**Mitigations:**
- **Domain specificity tags** — blocks tagged with their domain of origin.
  When used in a different domain, apply a transfer discount (weight × 0.5).
- **Cross-domain prediction tracking** — maintain separate Brier scores for
  within-domain vs cross-domain predictions. If cross-domain accuracy is
  consistently poor, reduce cross-domain transfer weight.
- **Explicit analogy flagging** — when simulation draws an analogy
  ("X is like Y because..."), tag it. Check whether the analogy's
  structural features actually hold in the new domain.

### 7. Reflexive Predictions

**Problem:** In politics, predictions change behavior. If everyone predicts
war, actors may de-escalate (self-defeating prophecy) or stockpile weapons
(self-fulfilling prophecy). Brier scores for reflexive predictions measure
something different from predictive accuracy.

**Mitigations:**
- **Reflexivity flag** — for each scenario, ask: "If this prediction were
  known to all actors, would it change the outcome?" Tag reflexive
  predictions separately.
- **Discounted Brier scores** — reflexive predictions get lower weight in
  calibration. Their Brier scores measure how actors responded to the
  prediction, not the agent's forecasting ability.
- **Second-order simulation** — for high-stakes reflexive scenarios,
  simulate "what happens if my prediction becomes known?" This is expensive
  (doubles LLM calls) but valuable for critical decisions.

### 8. Computational Cost

**Problem:** N scenarios × M scoring queries × K LLM calls per scenario.
Full Monte Carlo simulation is expensive.

**Mitigations:**
- **Tiered simulation** (see below) — match simulation depth to decision stakes.
- **Cached embeddings** — scenario scoring against knowledge uses existing
  block embeddings (no new embedding calls).
- **Batch LLM calls** — generate all scenarios in one prompt, not N separate
  calls. Score all probabilities in one call.
- **Simulation budget** — cap total LLM calls per simulation. Tier 1: 1 call.
  Tier 2: 3 calls. Tier 3: 10 calls max.

### 9. Scenario Combinatorial Explosion

**Problem:** 5 actors × 3 responses each = 243 scenarios. Can't evaluate all.

**Mitigations:**
- **Decision-tree pruning** — only branch at the most uncertain nodes.
  If one actor's response is 90% predictable, collapse it to one outcome.
- **Representative sampling** — pick 5-10 scenarios that span the outcome
  space, not exhaustive enumeration.
- **Influence analysis** — identify which actor's decision has the largest
  impact on overall outcome. Simulate THAT actor's branching in detail,
  collapse others to their most likely response.

### 10. Simulation Confidence vs. Real Confidence

**Problem:** Agent confuses "I simulated this well" with "I know this."
High consistency score ≠ high accuracy. An internally consistent simulation
built on wrong premises is confidently wrong.

**Mitigations:**
- **Separate signal sources** — simulation-derived confidence capped at 0.75.
  Real-outcome-derived confidence can reach 0.95. The system structurally
  prevents simulation from generating "I'm certain" signals.
- **Source tagging** — all simulation-derived calibrations tagged with
  `source="simulation"`. Can be audited separately from `source="outcome"`.
- **Reality anchor** — track the ratio of simulation-calibrated blocks to
  outcome-calibrated blocks. If >80% of confidence comes from simulation
  with no real outcomes, flag the domain as "untested."

---

## Tiered Simulation

Like agent discipline, match simulation depth to the decision:

### Tier 1: Quick Check (1 LLM call, <5 seconds)

```
Prompt: "Given this situation, what are 3 plausible outcomes?
         Rate your confidence in the most likely one."

Use when: routine decisions, low stakes, time-constrained
LLM cost: 1 call
Calibration: remember if confidence < 50% (uncertainty signal)
```

### Tier 2: Scored Simulation (2-3 LLM calls, <30 seconds)

```
Call 1: Generate 5 scenarios + adversarial + wildcard
Call 2: Assign probabilities with all scenarios visible
Call 3: (optional) Score each scenario against knowledge base

Use when: important decisions, novel domains, team planning
LLM cost: 2-3 calls
Calibration: pre-calibrate blocks, flag gaps, remember fragility
```

### Tier 3: Full Analysis (5-10 LLM calls, <2 minutes)

```
Call 1: Recall multi-frame knowledge
Call 2: Identify key uncertainties
Call 3: Generate 10+ scenarios with actor modeling
Call 4: Adversarial scenario generation
Call 5: Probability assignment (independent scoring)
Call 6: Cross-validation between scenarios
Call 7-10: (if needed) Resolve contradictions, fill gaps, second-order effects

Use when: high-stakes strategy, long-term planning, novel domain analysis
LLM cost: 5-10 calls
Calibration: full pre/post calibration, Brier tracking, meta-learning
```

---

## Architecture: Where Simulation Sits

Simulation is an **agent-level capability**, not a core elfmem operation.
It uses elfmem's primitives (recall, outcome, remember, dream) in a
structured loop — the same way `CalibratingAgent` uses them for inline
calibration.

```
┌──────────────────────────────────────────────────┐
│              SimulatingAgent                      │
│                                                   │
│  simulate()  → generate scenarios from knowledge  │
│  score()     → evaluate consistency + fragility   │
│  calibrate() → outcome() + remember()             │
│  resolve()   → Brier score when reality arrives   │
│  diagnose()  → meta-learning about sim quality    │
│                                                   │
│  Builds on: CalibratingAgent (inline calibration) │
│  Uses: elfmem recall / outcome / remember / dream │
└───────────────────┬──────────────────────────────┘
                    │ calls
┌───────────────────▼──────────────────────────────┐
│                  elfmem                           │
│  recall()   — knowledge retrieval (unchanged)     │
│  outcome()  — block reinforcement (unchanged)     │
│  remember() — pattern storage (unchanged)         │
│  dream()    — consolidation (unchanged)           │
│  curate()   — maintenance (unchanged)             │
└──────────────────────────────────────────────────┘
```

This is the elegant insight: **simulation is just structured recall +
structured comparison + structured remember.** No new elfmem operations
needed. The simulation engine is a pattern ON TOP of elfmem.

---

## Implementation Outline

### Data Types

```python
@dataclass
class Scenario:
    description: str
    probability: float
    supporting_block_ids: list[str]
    contradicting_block_ids: list[str]
    scenario_type: str  # "base" | "optimistic" | "pessimistic" |
                        # "adversarial" | "wildcard"

@dataclass
class SimulationResult:
    situation: str
    scenarios: list[Scenario]
    consistency_score: float      # 1.0 - |Σpᵢ - 1.0|
    fragility_score: float        # weighted avg fragility
    coverage_score: float         # fraction with ≥2 supporting blocks
    gaps: list[str]               # knowledge gaps discovered
    calibration_actions: int      # number of outcome() calls made

@dataclass
class ResolutionResult:
    brier_score: float
    matched_scenario: Scenario | None
    blocks_reinforced: int
    blocks_penalized: int
    meta_learning: str            # summary for remember()
```

### Core Methods

```python
class SimulatingAgent(CalibratingAgent):

    async def simulate(
        self,
        situation: str,
        n_scenarios: int = 5,
        tier: int = 2,
    ) -> SimulationResult:
        """Generate scenarios and pre-calibrate knowledge.

        Tier 1: 3 quick scenarios, 1 LLM call.
        Tier 2: 5 scenarios + adversarial + wildcard, 2-3 LLM calls.
        Tier 3: 10+ scenarios with actor modeling, 5-10 LLM calls.
        """

    async def resolve(
        self,
        simulation: SimulationResult,
        actual_outcome: str,
    ) -> ResolutionResult:
        """Compare reality to prediction. Compute Brier score. Deep calibrate."""

    async def simulation_health(self) -> str:
        """Report simulation calibration quality from historical Brier scores."""
```

### LLM Prompt Structure (Tier 2)

**Call 1 — Scenario Generation:**
```
Given this situation: {situation}

And this relevant knowledge:
{recalled_blocks_text}

Generate exactly {n} plausible scenarios for what happens next.
For each scenario:
  - A 2-3 sentence description
  - Which pieces of the provided knowledge support this scenario
  - Which pieces contradict it
  - Whether this is a base case, optimistic, pessimistic, or novel outcome

Additionally, generate:
  - One ADVERSARIAL scenario that contradicts the most likely outcome
  - One WILDCARD scenario involving a factor not in the provided knowledge

Format as structured JSON.
```

**Call 2 — Probability Assignment:**
```
Here are {n+2} scenarios for the situation: {situation}

{scenarios_list}

Assign a probability (0.0-1.0) to each scenario such that:
  - Mutually exclusive scenarios sum to approximately 1.0
  - The wildcard scenario has probability >= 0.10
  - Each probability reflects how likely you believe this outcome is,
    given the knowledge provided

Score each scenario independently, then normalize.
Format as JSON: [{"scenario_index": 0, "probability": 0.35}, ...]
```

### Integration with CalibratingAgent

`SimulatingAgent` extends `CalibratingAgent` — it inherits inline
calibration, session metrics, and session reflection. Simulation adds a
pre-action phase:

```
Session Start → [CalibratingAgent.start_session()]
  │
Before Task → [CalibratingAgent.before_task()]
  │
  ├─── IF high stakes OR novel domain:
  │    └── simulate() → pre-calibrate
  │
During Task → act
  │
After Task → [CalibratingAgent.after_task()]  ← inline calibrate
  │
  ├─── IF simulation was run AND outcome is known:
  │    └── resolve() → Brier score → deep calibrate
  │
Session End → [CalibratingAgent.end_session()]
```

---

## What Makes This Approach Robust, Flexible, and Elegant

### Robust

- **Edge cases handled** — 10 identified with concrete mitigations
- **Signal caps** — simulation can't overpower real outcomes
- **Mandatory adversarial** — prevents echo chambers structurally
- **Wildcard tracking** — detects when simulation framework is too narrow
- **Source tagging** — simulation vs outcome calibration always distinguishable

### Flexible

- **Tiered** — Tier 1 costs one LLM call, Tier 3 costs ten. Match to stakes.
- **Domain-agnostic** — works for geopolitics, code architecture, product strategy
- **Frame-compatible** — uses elfmem's existing frame system for knowledge retrieval
- **Composable** — builds on CalibratingAgent, which builds on elfmem primitives
- **Progressive** — start with Tier 1, add complexity as needed

### Elegant

- **No new elfmem operations** — simulation is structured recall + outcome + remember
- **Single feedback currency** — everything reduces to outcome signals (0.0-1.0)
- **Brier scores** — well-understood, mathematically principled calibration metric
- **Fourth rhythm** — fits naturally into elfmem's biological metaphor
  (heartbeat, breathing, sleep, imagination)
- **The simulation IS the calibration** — generating scenarios reveals knowledge
  quality without waiting for reality

---

## The Central Insight

The most powerful thing about simulation-based calibration is not that it
predicts the future. It often won't.

**The power is that generating scenarios reveals the structure of your own
knowledge**: where it's robust, where it's fragile, where it's missing,
and where it contradicts itself. This self-knowledge is the calibration.

A well-calibrated agent doesn't need to be right about the future. It needs
to know how much it knows, and act accordingly.

---

## See Also

- `examples/calibrating_agent.py` — Inline calibration (the foundation this builds on)
- `examples/agent_discipline.md` — Prompt instructions for all discipline tiers
- `docs/brainstorm_adaptive_intelligence.md` — Mathematical foundations (Monte Carlo,
  Bayesian updating, Thompson Sampling)
- `docs/cognitive_loop_operations_guide.md` — The cognitive loop this extends
- `docs/agent_usage_patterns_guide.md` — The 20 core patterns
