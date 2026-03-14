# Simulation-Calibrated Agents: Research Synthesis & Design

## Research Landscape (2025-2026)

Four research streams converge on the question of how LLM agents can simulate
outcomes and self-calibrate. This document synthesizes findings from 60+
papers and projects into a concrete architecture for elfmem.

---

## Stream 1: LLM Forecasting — What Actually Works

### The AIA Forecaster (State of the Art)

The most successful system to date achieves results "statistically
indistinguishable from expert superforecasters" (Brier 0.1076 vs 0.1110).
Its architecture has four components:

1. **Agentic Search** — Independent agents conduct adaptive, iterative
   searches, each determining its own query strategy
2. **Ensemble Aggregation** — 10 independent forecasts averaged
3. **Supervisor Agent** — Reconciliation layer that identifies disagreements
   and conducts targeted searches to resolve ambiguities
4. **Platt Scaling** — Sigmoid extremization to correct LLM hedging bias

**Key insight:** Ensemble of 10 independent calls dramatically outperforms
a single call. The supervisor reconciliation outperforms naive averaging.

### Ensemble Forecasting ("Wisdom of the Silicon Crowd")

Published in *Science Advances* (2024). 12 LLMs forecasting 31 questions:
- LLM ensemble Brier: **0.20** vs human crowd: **0.19** (no significant difference)
- Simple median aggregation — deliberately matches human benchmark methodology
- Exposing LLMs to human predictions improved accuracy **17-28%**

### Calibration Techniques Ranked by Effectiveness

| Technique | Impact | Cost | Notes |
|-----------|--------|------|-------|
| Ensemble aggregation (N=10) | +++++ | N LLM calls | Most robust single technique |
| Platt scaling / extremization | +++ | Zero (post-hoc) | Corrects hedging bias toward 0.5 |
| Retrieval augmentation | ++++ | Variable | 24-28% accuracy improvement |
| Supervisor reconciliation | +++ | 1-2 LLM calls | Resolves ensemble disagreements |
| Linguistic verbal uncertainty | ++ | Zero | Ask for verbal confidence, not numeric |
| Direct structured prompting | ++ | Zero | "Range then probability" beats narrative |
| Human-AI hybrid | +++ | Human time | Bidirectional improvement |

### What Doesn't Work

- **Narrative framing** — fictional scripts, debate formats degrade accuracy
- **Longer chain-of-thought** — reasoning *quality* matters more than *length*
- **Single-model confidence** — individual model probability estimates are
  poorly calibrated; ensembles are essential

### ForecastBench Timeline

- Superforecasters: Brier 0.081
- Best LLM (GPT-4.5, 2025): 0.101
- Improvement rate: ~0.016 Brier/year
- Projected parity: **November 2026** (95% CI: Dec 2025 – Jan 2028)

---

## Stream 2: Agent-Based Simulation — Scale and Emergence

### Generative Agent Simulations

- **Stanford 1,000 People** (2024) — Simulated 1,052 real individuals using
  2-hour interviews + LLM. Replicated survey responses **85% as accurately**
  as participants replicate their own answers two weeks later.
- **AgentSociety** (Tsinghua, 2025) — 10K+ agents, 5M interactions.
  Reproduces polarization, inflammatory message spread, UBI effects.
  Three-layer cognition: emotions, needs, cognition.
- **AgentTorch** (MIT, 2025) — **8.4M agents** via "archetype" clustering.
  Cluster similar agents, share LLM calls, add stochastic variation.
  Deployed for New Zealand H5N1 digital twin (5M citizens).

### Military Wargaming

- **GenWar** (Johns Hopkins APL, 2025) — AI wargaming lab for senior
  military commanders using LLM agents as advisers and adversary leaders
- **U.S. Army CGSC** ran AI-enabled wargame exercise (Nov 2025)
- Significant qualitative differences between LLM and human responses remain

### Key Architectural Patterns

**Archetype clustering** is the breakthrough for scale:
```
Instead of: 10,000 agents × LLM call each = 10,000 calls
Do:         10,000 agents → 50 archetypes × LLM call each = 50 calls
            + stochastic variation per individual agent
```

This is directly applicable to scenario simulation — cluster similar
scenarios, evaluate representative ones, interpolate the rest.

---

## Stream 3: Structured Reasoning — MCTS, Debate, Counterfactuals

### Language Agent Tree Search (LATS)

LLM as policy + value function + self-reflection, combined with MCTS:
- **Reflection-after-failure** — when a branch fails, generate an explicit
  reflection that becomes context for future exploration
- 94.4% pass@1 on HumanEval
- Key: learning from simulated failures without weight updates

### Confidence-Gated Deliberation (DOWN)

**"Debate Only When Necessary"** (2025):
- Selectively activates multi-agent debate based on confidence scores
- **6x efficiency improvement** while preserving accuracy
- Implication: most decisions don't need simulation — only uncertain ones do

### Self-Play Critic (SPC)

Co-evolutionary adversarial game:
- **Sneaky generator** creates subtle errors
- **Critic** detects them
- Both evolve through opposing optimization
- Produces increasingly sophisticated scenarios through competition

### Counterfactual Decomposition

**Critical finding:** Break counterfactual reasoning into two phases:
1. **Extract causal structure** (what causes what) — LLMs excel at this
   (92% accuracy, 20-point gain over prior methods)
2. **Reason about interventions** (what if X changes) — LLMs are weaker here

Implication: Simulation should first map the causal structure of the domain,
then reason about specific interventions within that structure.

### Game Theory Findings

- Reasoning *quality* matters more than *length* (longer CoT doesn't help)
- **Persona sensitivity** — how agents are characterized strongly
  influences strategic outcomes
- LLMs exhibit predictable biases: cooperation bias, end-game defection

---

## Stream 4: Novel Calibration — The Frontier

### Missing-Mass Estimation (Conformal Prediction)

**CPQ** (NeurIPS 2025): Uses Good-Turing estimators to quantify "what fraction
of relevant answers has the model never generated?"

```
missing_mass = probability of encountering entirely novel knowledge on topic X
```

Applied to elfmem: treat retrieval results as samples from an unknown
distribution. The missing mass tells you the probability of encountering
knowledge the system has never seen. This is a **mathematically principled
version of gap detection**.

### Active Inference / Free Energy Principle

**DR-FREE** (Nature Communications, Dec 2025): Each action's value decomposes into:

```
G = pragmatic_value (goal achievement) + epistemic_value (uncertainty reduction)
```

**Distributionally robust** variant: minimizes the *maximum* free energy
across an ambiguity set of possible environments. Handles uncertainty about
the environment itself, not just states within it.

Applied to elfmem: Each knowledge block has:
- **Pragmatic value** — how useful is this for current tasks?
- **Epistemic value** — how much would testing/reinforcing this block
  reduce overall system uncertainty?

Curation prioritizes blocks maximizing the combined score.

### Information Gain as Reinforcement (IGPO)

**Core idea:** Reinforce based on *information gain*, not access frequency.

```
R_info = H(Y) - H(Y|action)
```

Actions (retrieval, recall) that maximally reduce uncertainty about
outcomes get the highest reinforcement signal.

Applied to elfmem: blocks that consistently produce large belief updates
(surprise, correction, insight) are high-value. Blocks that produce zero
information gain are candidates for archival.

### Reflective Confidence (DeepConf / Meta 2025)

**Paradigm shift:** Low confidence triggers **deeper thinking**, not
termination or a warning flag.

```
Traditional:  low confidence → flag "uncertain" → proceed cautiously
Reflective:   low confidence → trigger reflection cycle → re-examine →
              synthesize higher-confidence response
```

DeepConf achieves 99.9% on AIME 2025 with up to **84.7% token reduction**
by terminating high-confidence traces early and investing in low-confidence ones.

Applied to elfmem: When recall returns low-confidence blocks, trigger a
reconstruction cycle — re-examine from different frames, check consistency
with related blocks, generate a synthesized view with justified confidence.

### Epistemic vs Aleatoric Uncertainty

Two fundamentally different kinds of not-knowing:

| Type | Meaning | Action |
|------|---------|--------|
| **Epistemic** | I lack data | Seek more information |
| **Aleatoric** | Topic is inherently noisy/contested | Accept uncertainty |

Applied to elfmem: Tag knowledge blocks with uncertainty type. "I don't know
about quantum computing" (epistemic — go learn) is fundamentally different
from "Experts disagree about consciousness" (aleatoric — accept and model
the disagreement).

### Reconstructive Memory (MRAgent)

**"Memory is Reconstructed, Not Retrieved"** (ICLR 2026 Workshop):

Replace static vector-similarity retrieval with **reasoning-guided graph
traversal** that reconstructs answers from knowledge fragments.

```
Traditional: query → embed → cosine similarity → top-k blocks
MRAgent:     query → cue → tag → explore graph with reasoning →
             prune paths based on accumulated evidence → reconstruct
```

The reconstruction process itself produces calibration signals: if it
requires many speculative leaps, confidence is low. If the graph path is
well-supported, confidence is high.

### A-MEM: Zettelkasten-Inspired Self-Organizing Memory

**NeurIPS 2025:** Each memory unit gets LLM-generated keywords, tags,
contextual descriptions, and dynamic links. New memories trigger updates
to existing memories' contextual representations.

This is strikingly similar to elfmem's architecture — but with the added
innovation that *storing* a new block actively reshapes the descriptions
of existing blocks, not just the graph edges.

---

## Brainstorm: Seven Alternative Architectures

### Architecture A: Ensemble Forecaster

**Inspired by:** AIA Forecaster, Wisdom of the Silicon Crowd

```
Situation → N independent LLM calls → each generates scenario + probability
→ Supervisor reconciles disagreements → Platt scaling → calibrated forecast
→ Pre-calibrate elfmem blocks based on which supported correct forecast
```

**Strengths:** Empirically proven. Simple. Mathematically grounded.
**Weaknesses:** Expensive (N LLM calls). Doesn't learn causal structure.
Treats each simulation as independent (no memory across simulations).

### Architecture B: MCTS Scenario Explorer

**Inspired by:** LATS, RAP, ReKG-MCTS

```
Situation → identify branching points → MCTS explores scenario tree
→ LLM evaluates each node (value function) → UCB1 balances exploration
→ Reflection on failed branches → calibrate blocks at leaf nodes
```

**Strengths:** Principled exploration. Reflection enables learning.
Can discover non-obvious scenarios.
**Weaknesses:** Computationally expensive (many rollouts). Branching
factor explodes in open-ended domains. Value function quality critical.

### Architecture C: Free Energy Optimizer

**Inspired by:** Active Inference, DR-FREE, IGPO

```
Situation → compute expected free energy for each possible action
→ G = pragmatic_value + epistemic_value
→ Choose action that minimizes G (balances goal + learning)
→ After action: compute information gain → reinforce high-IG blocks
→ Missing-mass estimation → quantify knowledge gaps
```

**Strengths:** Theoretically principled. Naturally balances exploration
and exploitation. Principled gap detection via missing mass.
**Weaknesses:** Complex to implement. Free energy computation requires
good generative model of outcomes. Relatively untested for LLM agents.

### Architecture D: Confidence-Gated Hybrid

**Inspired by:** DOWN, DeepConf, Reflective Confidence

```
Situation → recall() → compute confidence
→ HIGH confidence: act directly (no simulation needed)
→ MEDIUM confidence: quick ensemble (3 scenarios, 1-2 LLM calls)
→ LOW confidence: full simulation + reflective reconstruction cycle
→ VERY LOW: flag as epistemic gap, seed knowledge acquisition
```

**Strengths:** Efficient (most decisions skip simulation entirely).
Matches cognitive load to genuine uncertainty. Reflective loops improve
quality where it matters most.
**Weaknesses:** Confidence estimation must be accurate for gating to work.
Risk of overconfidence causing premature commitment.

### Architecture E: Co-Evolutionary Adversarial

**Inspired by:** SPC, SPAG, Multi-Agent Evolve

```
Generator agent → produces scenarios from elfmem knowledge
Critic agent → challenges plausibility, finds gaps, contradictions
Both improve through opposition → increasingly sophisticated scenarios
→ Only scenarios that survive criticism get probability assignments
→ Calibrate blocks based on which survived and which were demolished
```

**Strengths:** Self-improving quality over time. Naturally produces
adversarial scenarios. The critic is a built-in quality check.
**Weaknesses:** Two agents = double the cost. Risk of generator-critic
co-adaptation that misses genuine scenarios. Hard to validate improvement.

### Architecture F: Causal-First Simulator

**Inspired by:** Counterfactual decomposition, game theory findings

```
Phase 1: Extract causal graph of the domain
  → "What causes what? Who influences whom? What constraints exist?"
  → Store as edges in elfmem knowledge graph

Phase 2: Identify interventions
  → "What is changing? Which causal paths are affected?"

Phase 3: Trace consequences through causal graph
  → Follow edges, accumulate effect estimates
  → Where causal path is uncertain, branch into scenarios

Phase 4: Calibrate
  → Well-supported causal paths → reinforce supporting blocks
  → Uncertain causal links → flag as epistemic gaps
  → Contradictory paths → trigger investigation
```

**Strengths:** Leverages LLMs' strongest capability (causal extraction at
92% accuracy). Produces interpretable, traceable predictions. Causal graph
persists in elfmem and improves over time.
**Weaknesses:** Causal graph construction requires significant upfront
investment. Quantitative reasoning along causal paths is weak in LLMs.
Fails for truly novel domains with no known causal structure.

### Architecture G: Archetype Monte Carlo

**Inspired by:** AgentTorch archetypes, AgentSociety, ensemble methods

```
For actor/scenario simulation:
1. Cluster similar scenarios into archetypes (3-5 representative types)
2. Simulate each archetype in detail (1 LLM call per archetype)
3. Interpolate individual scenarios from archetype results
4. Add stochastic variation for each individual scenario
5. Aggregate across the distribution

For global politics specifically:
1. Cluster actors by behavioral archetype (hawks/doves/pragmatists)
2. Simulate archetype interactions (combinatorics manageable)
3. Map real actors to nearest archetype + deviation
```

**Strengths:** Dramatically reduces computational cost (N/k LLM calls
instead of N). Scales to complex multi-actor scenarios. Stochastic
variation captures uncertainty naturally.
**Weaknesses:** Archetype clustering loses individual nuance. Quality
depends heavily on cluster quality. May miss scenarios that don't fit
any archetype.

---

## Evaluation: Which Architecture for elfmem?

| Architecture | Empirical Evidence | Complexity | elfmem Fit | Cost |
|---|---|---|---|---|
| A: Ensemble Forecaster | +++++ | ++ | +++ | High |
| B: MCTS Explorer | ++++ | ++++ | ++ | Very High |
| C: Free Energy Optimizer | ++ | +++++ | ++++ | Moderate |
| D: Confidence-Gated Hybrid | +++ | ++ | +++++ | Low-High |
| E: Co-Evolutionary | +++ | +++ | +++ | High |
| F: Causal-First | +++ | ++++ | ++++ | Moderate |
| G: Archetype Monte Carlo | +++ | +++ | +++ | Low |

---

## The Recommended Design: Confidence-Gated Free Energy Simulation

The best architecture for elfmem is a **synthesis of D + C + A + F**:

- **D (Confidence-Gated)** as the outer loop — don't simulate unless needed
- **C (Free Energy)** as the scoring framework — principled balance of
  pragmatic and epistemic value
- **A (Ensemble)** for the simulation engine — empirically proven
- **F (Causal-First)** for domain modeling — leverages LLMs' strongest capability

### Why This Combination

1. **Confidence gating (D)** is essential for efficiency. Research shows
   most decisions don't need simulation. DOWN achieves 6x efficiency by
   only debating when uncertain. elfmem should do the same — only
   trigger simulation when recall confidence is below threshold.

2. **Free energy scoring (C)** provides the principled framework for
   what to reinforce. Instead of ad-hoc signal values (0.85 for "used",
   0.45 for "ignored"), compute actual information gain per block.
   This is the theoretically grounded version of inline calibration.

3. **Ensemble generation (A)** is the empirically strongest technique.
   Generate N independent scenario sets, reconcile via supervisor.
   Platt scaling corrects hedging bias. Simple, proven, effective.

4. **Causal extraction (F)** is what LLMs do best (92% accuracy).
   Building the causal structure FIRST, then simulating interventions
   within that structure, leverages the LLM's strengths and avoids
   its weaknesses (quantitative reasoning).

### The Architecture

```
┌─────────────────────────────────────────────────────────┐
│           CONFIDENCE-GATED FREE ENERGY SIMULATION        │
│                                                          │
│  GATE (every recall):                                    │
│    confidence = recall().composite_score                  │
│    ┌──────────────────────────────────────────┐          │
│    │ HIGH (>0.7): Act directly, inline calibrate│         │
│    │ MEDIUM (0.4-0.7): Quick ensemble (Tier 1)  │         │
│    │ LOW (0.2-0.4): Full simulation (Tier 2)    │         │
│    │ VERY LOW (<0.2): Causal mapping (Tier 3)   │         │
│    └──────────────────────────────────────────┘          │
│                                                          │
│  TIER 1 — Quick Ensemble (2 LLM calls):                  │
│    3 independent scenario generations                     │
│    Median probability aggregation                         │
│    Pre-calibrate: outcome() on consistent/contradicting   │
│                                                          │
│  TIER 2 — Full Simulation (5 LLM calls):                 │
│    Call 1: Recall multi-frame knowledge                   │
│    Call 2: Extract causal structure of the domain          │
│    Call 3: Generate 5+ scenarios along causal paths        │
│    Call 4: Independent probability assignment              │
│    Call 5: Supervisor reconciliation + adversarial check   │
│    Platt scaling on final probabilities                    │
│    Free energy scoring per block                          │
│    Missing-mass gap estimation                            │
│                                                          │
│  TIER 3 — Deep Causal Analysis (8-10 LLM calls):         │
│    Full causal graph extraction                            │
│    Multi-frame recall (SELF + WORLD + ATTENTION + TASK)    │
│    Archetype modeling for multi-actor scenarios             │
│    Ensemble of 5+ scenario sets                            │
│    Supervisor reconciliation                               │
│    Co-evolutionary validation (generator + critic)         │
│    Reflective confidence loops on low-confidence paths     │
│    Epistemic vs aleatoric uncertainty tagging              │
│    Free energy optimization for block curation             │
│                                                          │
│  POST-HOC (when reality arrives):                         │
│    Brier score computation                                │
│    Trace-back to blocks that generated correct/wrong       │
│    Meta-learning about simulation quality by domain        │
│                                                          │
│  Builds on: CalibratingAgent → elfmem primitives          │
└─────────────────────────────────────────────────────────┘
```

### The Free Energy Scoring Detail

For each knowledge block retrieved during simulation:

```
G(block) = α × pragmatic_value + (1-α) × epistemic_value

pragmatic_value:
  = how useful was this block for generating consistent,
    well-supported scenarios?
  = fraction of surviving scenarios that cite this block

epistemic_value:
  = how much did this block reduce uncertainty?
  = H(scenarios_without_block) - H(scenarios_with_block)
  where H = entropy of scenario probability distribution

α = 0.6 by default (slightly favor practical utility)
α → 1.0 in execution mode (exploit)
α → 0.0 in exploration mode (explore)
```

The outcome signal becomes:

```
outcome_signal = sigmoid(G(block) - G_mean)
```

This replaces the ad-hoc signal table (0.85 for "used", etc.) with a
principled score derived from the simulation itself.

### Missing-Mass for Gap Detection

Using Good-Turing estimation from conformal prediction research:

```
Given N knowledge blocks retrieved for a topic:
  n_1 = number of blocks seen only once (hapax legomena)
  missing_mass ≈ n_1 / N

Interpretation:
  missing_mass < 0.1: topic well-covered
  missing_mass 0.1-0.3: moderate gaps
  missing_mass > 0.3: significant gaps — explore before acting
```

This is a mathematically principled version of our earlier heuristic
gap detection.

### Reflective Confidence Loops

When simulation encounters a low-confidence scenario path:

```
Traditional approach:
  low confidence → flag as uncertain → assign wide probability range

Reflective approach:
  low confidence → trigger reconstruction cycle:
    1. Recall from different frame (WORLD instead of TASK)
    2. Check consistency with related blocks via graph traversal
    3. Generate adversarial counter-scenario
    4. If reconstruction converges → higher justified confidence
    5. If reconstruction diverges → genuine epistemic uncertainty
       → tag as epistemic gap → remember for future learning
```

This invests computational resources where they matter most —
at the boundaries of knowledge.

### Platt Scaling Implementation

Correct LLM hedging bias with a simple sigmoid transform:

```
p_calibrated = 1 / (1 + exp(-(a × logit(p_raw) + b)))

where:
  logit(p) = log(p / (1-p))
  a, b = parameters fit on historical prediction/outcome pairs
  a > 1 → extremization (corrects hedging toward 0.5)
```

Initial values (from AIA Forecaster research):
- a = 1.3 (moderate extremization)
- b = 0.0 (no bias shift)

These parameters self-calibrate from accumulated Brier scores.

---

## Edge Cases Informed by Research

### 1. The "Average Persona" Problem (from AgentSociety)

LLMs converge toward an average worldview, suppressing diversity.
Scenario generation may produce bland, centrist scenarios that miss
extreme but plausible outcomes.

**Mitigation:** Explicit persona injection. Generate scenarios
from different analytical perspectives (hawk, dove, pragmatist,
contrarian). Tag each with its perspective. This is the archetype
approach applied to viewpoint diversity rather than agent diversity.

### 2. Temporal Behavioral Drift (from generative agents research)

Over long simulations, LLM agents drift from their initial
characterization. Extended scenario chains become unreliable.

**Mitigation:** Limit scenario chain length. For multi-step futures,
use independent simulations at each step rather than extending
a single narrative. elfmem's decay mechanism naturally handles
staleness — old simulation results fade unless reinforced by outcomes.

### 3. Information Gain Collapse (from IGPO)

If the knowledge base is already comprehensive for a domain,
every recall produces near-zero information gain. The system
can't distinguish "I know this well" from "I'm stuck in an
echo chamber."

**Mitigation:** Periodic adversarial probing. Even in high-confidence
domains, run adversarial scenarios that challenge the strongest
beliefs. If the adversarial scenario is surprisingly well-supported,
that's a signal the echo chamber is real.

### 4. Causal Structure Brittleness (from counterfactual research)

LLMs extract causal structure well (92%) but reason poorly
about interventions within that structure. The causal graph
may be correct but the intervention reasoning wrong.

**Mitigation:** Validate intervention reasoning against historical
data. For each causal link, check: "Have we seen this link activated
in the past? What happened?" If yes, use the historical outcome as
a prior. If no, flag as unvalidated and widen uncertainty.

### 5. Confidence Calibration for Gating (from DeepConf)

The confidence gate only works if the confidence estimate is
itself well-calibrated. A miscalibrated gate either wastes
resources (simulating when unnecessary) or misses important
uncertainty (not simulating when it should).

**Mitigation:** Track gate accuracy. After each decision:
- Was high-confidence correct? (should be >80% of the time)
- Was low-confidence genuinely uncertain? (outcome should be
  less predictable)
If the gate is miscalibrated, adjust thresholds.

---

## Implementation Roadmap

### Phase 1: Foundations (extend CalibratingAgent)

Add confidence gating and quick ensemble:
- Compute recall confidence from composite scores
- Gate: HIGH → inline calibrate only; LOW → trigger simulation
- Quick ensemble: 3 independent scenario generations + median
- Platt scaling with default parameters

### Phase 2: Free Energy Scoring

Replace ad-hoc signal values with information-theoretic scores:
- Pragmatic value from scenario support
- Epistemic value from entropy reduction
- Missing-mass estimation for gap detection
- Epistemic vs aleatoric tagging on blocks

### Phase 3: Causal Structure

Add causal extraction as the first simulation step:
- LLM extracts causal graph from recalled knowledge
- Store causal edges in elfmem knowledge graph
- Simulate interventions along causal paths
- Validate against historical outcomes where available

### Phase 4: Advanced Techniques

- Supervisor reconciliation for ensemble disagreements
- Reflective confidence loops for low-confidence paths
- Co-evolutionary generator/critic for scenario quality
- Archetype clustering for multi-actor simulations
- Brier score tracking and meta-learning

---

## The Central Insight

The research converges on one finding: **the most effective simulation
is not the most complex — it's the most appropriately gated.**

DOWN shows 6x efficiency by only debating when uncertain.
DeepConf achieves 84.7% token reduction by terminating confident traces early.
The AIA Forecaster's supervisor only activates when ensemble members disagree.

For elfmem, this means:

> **Don't simulate everything. Simulate only what you're uncertain about.
> Invest the saved compute in deeper analysis where it matters.**

Most agent actions are routine. The agent recalls knowledge, acts on it,
and the outcome is predictable. Simulation adds no value here.

But when the agent encounters genuine uncertainty — novel domains,
contradictory knowledge, high-stakes decisions — that's where simulation
transforms calibration quality. The free energy framework tells you
exactly where to invest: wherever the epistemic value is highest.

---

## Key Sources

### Forecasting & Calibration
- AIA Forecaster (arxiv 2511.07678) — state-of-art system matching superforecasters
- Wisdom of the Silicon Crowd (Science Advances, 2024) — ensemble LLM forecasting
- ForecastBench (ICLR 2025) — dynamic forecasting benchmark
- OpenForecaster (arxiv 2512.25070) — auto-generated forecasting training data

### Agent Simulation
- AgentSociety (arxiv 2502.08691) — 10K+ agent social simulation
- AgentTorch (AAMAS 2025) — archetype clustering for millions of agents
- GenWar (JHU APL, 2025) — military wargaming with LLM agents
- LATS (ICML 2024) — language agent tree search with reflection

### Structured Reasoning
- DOWN (arxiv 2504.05047) — confidence-gated multi-agent deliberation
- SPC (arxiv 2504.19162) — self-play critic for co-evolutionary improvement
- Counterfactual Causal Inference (NeurIPS 2025) — causal decomposition
- Game-Theoretic LLM (arxiv 2411.05990) — strategic reasoning workflows

### Novel Calibration
- CPQ / Good-Turing (NeurIPS 2025) — missing-mass estimation for coverage gaps
- DR-FREE (Nature Communications, 2025) — distributionally robust free energy
- IGPO (arxiv 2510.14967) — information gain policy optimization
- DeepConf (Meta, 2025) — reflective confidence with token reduction
- A-MEM (NeurIPS 2025) — Zettelkasten-inspired self-organizing memory
- MRAgent (ICLR 2026 Workshop) — reconstructive memory
- Anthropic Introspection (2025) — emergent introspective awareness in LLMs

---

## See Also

- `examples/simulation_calibration.md` — Original simulation design (pre-research)
- `examples/calibrating_agent.py` — Inline calibration implementation
- `examples/agent_discipline.md` — Prompt instructions for agent discipline
- `docs/brainstorm_adaptive_intelligence.md` — Mathematical foundations
