# Behavioural Simulation Research — Analysis & Integration with elfmem

**Date**: 2026-03-11
**Status**: Research / Evaluation
**Context**: Analysis of an agent-based behavioural simulation plan from a prediction market system (KLS), evaluated against elfmem's existing simulation calibration architecture

---

## 1. What the Behavioural Simulation Plan Does

The plan adds a **Behavioural Simulation Layer** to a prediction market trading system. Its core contribution: modelling *how specific actors behave* (Trump, Iran, SCOTUS) to generate causally-grounded scenario paths, rather than relying on statistical base rates alone.

The system has four layers of increasing cost and depth:

| Layer | When | What | Cost |
|-------|------|------|------|
| Base | Always | Independent P estimate + store recall + 4-gate decision | Free |
| Standard | Most theses >$100 | Pre-mortem + historical analogs + Monte Carlo | ~$0.02 |
| Deep | Human-actor theses >$200, confidence 0.55-0.75 | Character Council + SELF check + red/blue debate | ~$0.10-0.30 |
| Meta | Periodic | Post-resolution audit + strategy optimisation + Brier scoring | Variable |

The **Character Council** is the core simulation engine: load entity profiles → LLM generates N scenario paths constrained by those profiles → weight by plausibility → probability distribution. The output feeds the existing Monte Carlo system.

The **SELF simulation** treats the forecaster (elf0) as a first-class entity with measured biases, known failure modes, and quantified correction factors. Three modes: pre-decision check, post-resolution audit, strategy optimisation.

---

## 2. Structural Analysis

### 2.1 The Entity Profile Architecture

Each entity is a 7-section markdown file:

```
Identity and Scope → who/what, domains, confidence, half-life
Decision Style → how they think, speed, risk tolerance
Incentive Structure → what they optimise for now
Historical Patterns → what they've consistently done
Current Constraints → limits on action space RIGHT NOW
Trigger Conditions → what causes action vs. waiting
Failure Modes → characteristic blind spots
```

**Quality criterion**: profiles must be *specific and falsifiable*.

Bad: "Trump is unpredictable and makes impulsive decisions."
Good: "Trump's public threats resolve to kinetic action ~15% of the time. Signal-to-noise ratio on military threats is ~1:6."

Each entity type has a half-life governing staleness:

| Entity Type | Half-Life | Rationale |
|-------------|-----------|-----------|
| Individual political actor | 60-90 days | Core style stable; policy focus shifts |
| Institutional body | 180-365 days | Institutional logic changes slowly |
| Nation-state leadership | 90-180 days | Strategic posture evolves; tactics faster |
| SELF (elf0) | 30 days | Calibration data updates frequently |

### 2.2 The Character Council Prompt

The simulation engine uses a structured LLM prompt:

1. Thesis statement + resolution criteria
2. Entity profiles (verbatim — profiles ARE the constraint)
3. Current context from store recall
4. Generate N distinct scenario paths (default 30-50)
5. Each path: actor decisions, causal chain, outcome, plausibility rating, conditions for more/less likely

Paths are required to include: base case, optimistic/pessimistic outliers, and at least one path where an actor behaves against their typical pattern.

### 2.3 The SELF Entity — Three Modes

**Mode 1: Pre-Decision Check** (per-thesis)
- Load SELF entity profile
- Identify which failure modes apply to THIS thesis
- Apply correction factors to confidence estimate
- Output: corrected_confidence, failure_modes_triggered

**Mode 2: Post-Resolution Audit** (per resolved simulation)
- Compare actual vs. optimal decision
- Gap analysis → specific SELF profile update suggestions
- Primary mechanism for updating SELF with measured data

**Mode 3: Strategy Optimisation** (periodic)
- Test elf0 decision rules across multiple configurations against resolved theses
- Find optimal operating parameters (min confidence, simulation requirements)
- Recommend configuration for next operating period

### 2.4 The Simulation Output Format

Dual-audience markdown file with 7 sections:

| Section | Audience | Content |
|---------|----------|---------|
| 1. Summary | Both | Quantitative: P(YES), confidence interval, entities, Brier |
| 2. Reasoning | Human | 2-4 sentence synthesis |
| 3. Key Paths | Both | Top 5-8 paths with actor decisions |
| 4. Monitoring | Human | Observable signals for more/less likely |
| 5. SELF Check | Both | Failure modes triggered, corrections applied |
| 6. Resolution | Both | Post-resolution: Brier score, calibration notes |
| 7. Raw Data | Machine | Full JSON appendix for scoring/audit |

---

## 3. What's Excellent

### 3.1 SELF as First-Class Entity

The plan's most powerful insight. The asymmetry:

> Fix Trump estimation accuracy by 20% → improve geopolitical theses.
> Fix elf0's decision process by 20% → improve *every* thesis, in *every* domain, permanently.

External simulation improves input quality for individual decisions. SELF simulation improves the decision *process* itself — this compounds across all future decisions. The SELF profile is grounded in *measured calibration data* (Brier scores per domain, quantified bias offsets), not just aspirational principles.

**elfmem connection**: elfmem's SELF frame contains constitutional blocks (identity, values, principles). What's missing is measured calibration data — quantified biases, domain-specific accuracy, known failure modes with evidence. The KLS plan shows how to extend the SELF frame from "who I am" to "where I systematically get it wrong and by how much."

### 3.2 Text-First Phase 0

Zero code delivers maximum immediate value. Entity profiles are markdown files — they're knowledge blocks. Store recall already works on them. No engineering required. The system delivers value before any code exists.

This is the same philosophy as elfmem's learn() → dream() progression: fast ingestion first, structure later. The KLS plan applies this principle to the simulation system itself.

### 3.3 Layered Application with Explicit Triggers

Not every decision needs simulation. The trigger criteria are concrete and actionable:

- 2+ interacting human decision-makers
- Event driven by discretionary judgment (not pure statistics)
- Exposure above threshold
- Confidence in the 0.55-0.75 band (highest marginal value)

The confidence band criterion is particularly smart. Simulation has the most value in the uncertain middle — at extremes, you're already confident enough to act or decline. This maps directly to elfmem's confidence gating (DOWN pattern) but with domain-specific thresholds rather than abstract confidence scores.

### 3.4 "Hallucination as Feature"

A precise reframing: the LLM's generative capacity is the simulation engine. Entity profiles constrain the generation space. Without profiles: fantasy. With profiles: structured extrapolation within behavioural bounds.

This is the same principle as Shell's scenario planning and CIA RED CELL reports — use generative reasoning to expand the hypothesis space, then filter by plausibility. The insight: LLMs aren't predicting the future, they're enumerating *plausible* futures consistent with behavioural constraints.

### 3.5 Compounding Post-Resolution Loop

```
Simulation → Brier score → Entity profile updates → Better simulation
                        → SELF profile updates → Better decision process
```

Three timescales of learning:
- Per-thesis: pre-decision SELF check
- Per-resolution: post-resolution audit + entity updates
- Per-wave: strategy optimisation across configurations

Each layer improves a different aspect: entity profiles improve input quality, SELF profile improves process quality, strategy optimisation improves operating parameters.

### 3.6 Dual-Audience Output Format

Single file serves humans (narrative sections) and machines (JSON appendix). No translation layer, no sync problems, no format drift. The human reads the reasoning summary and monitoring signals. The LLM reads the JSON appendix for scoring and audit.

---

## 4. What's Weak

### 4.1 "Monte Carlo" Isn't Monte Carlo

The plan generates N paths via LLM, then claims to perform Monte Carlo simulation:

```python
total_weight = sum(path.plausibility for path in paths)
yes_weight = sum(p.plausibility for p in paths if p.outcome == "yes")
p_yes = yes_weight / total_weight
```

This is a weighted average over a fixed set of LLM-generated paths. The "Monte Carlo" step adds noise to plausibility ratings and resamples 1000 times — but this amplifies the LLM's initial generation bias, not true distributional uncertainty. The 10th/90th percentile bounds look rigorous but rest on synthetic noise applied to self-assessed ratings.

**The statistical claim exceeds the statistical reality.** This is weighted scenario aggregation, not Monte Carlo sampling from a probability distribution.

**Better approach**: Call it what it is (weighted scenario aggregation), or use genuine ensemble diversity — N independent LLM calls generating paths independently, then aggregate via median. elfmem's research synthesis recommends exactly this: ensemble of 3-10 independent calls with supervisor reconciliation.

### 4.2 Self-Assessed Plausibility Is Circular

The same LLM that generates a scenario also rates its plausibility (0.0-1.0). This is asking the generator to judge its own quality. Known problems:

- LLMs cluster self-assessed confidence around 0.3-0.7, compressing the tails
- The generator anchors on its own narrative quality, not external evidence
- First-generated scenarios tend to receive higher plausibility (primacy effect)

**Better approach**: Separate generation from evaluation. Generate paths in one call, evaluate plausibility in a separate call (or use a critic model). The research synthesis's ensemble approach naturally avoids this — each independent call generates its own paths without seeing others' assessments.

Alternatively, use Platt scaling to correct systematic calibration bias in the ratings: `p_calibrated = 1/(1 + exp(-(a × logit(p_raw) + b)))`.

### 4.3 SELF Corrections as Additive Offsets Are Fragile

```
Corrected estimate: 0.68 - 0.12 - 0.08 = 0.48
```

Five problems with this:

1. **No probability bound enforcement** — corrections can push below 0 or above 1
2. **Assumes constant bias** — a +0.12 bias at p=0.68 is probably not +0.12 at p=0.20 (biases scale nonlinearly with confidence)
3. **No interaction effects** — recency bias and geopolitical overconfidence aren't independent; applying both additively double-counts shared causes
4. **False precision** — applying a 0.12 correction implies the bias is known to ±0.01, which requires many resolved outcomes to estimate
5. **Not self-correcting** — the correction doesn't update based on whether it was itself correct

**Better approach**: Platt scaling. The sigmoid transform `p_calibrated = 1/(1 + exp(-(a × logit(p_raw) + b)))` respects probability bounds, handles nonlinear bias, and the parameters a,b can be fit from resolved outcomes. This is the standard approach in calibration literature (used by AIA Forecaster).

### 4.4 Entity Profile Quality Has No Validation Mechanism

The plan claims "a Trump profile alone would have prevented the Iran SHORT error." This is post-hoc reasoning. The profile was written *after* the error, with knowledge of what went wrong. Any profile looks good in retrospect.

The quality criteria ("specific and falsifiable") are necessary but not sufficient. There's no mechanism to:
- Validate a profile's predictive power before relying on it
- Detect when a profile is too generic to be useful
- Measure which profile sections actually influenced simulation quality

**Better approach**: Profile validation through backtesting. When a new profile is written, retroactively check: would this profile have changed past decisions? If it doesn't change anything, it's too generic. Track which profile sections are cited in simulation paths that resolve correctly vs. incorrectly.

### 4.5 40 Scenarios Is Expensive and Redundant

The plan defaults to 30-50 scenario paths. With capable models (Opus-class), each path requires substantial reasoning about multiple actors, causal chains, and conditions. Real costs are likely $1-5 per simulation, not the estimated $0.10-0.30.

More importantly, 40 paths from a single LLM call don't provide 40x the information of 8 paths. After ~8-10 diverse paths, additional paths tend to be minor variations of existing ones. The information gain per path diminishes rapidly.

**Better approach**: Generate 5-8 archetypal paths covering the full outcome space (base case, optimistic, pessimistic, adversarial, wildcard). Use ensemble diversity (multiple independent calls) rather than path quantity for robustness. elfmem's tiered simulation design (Tier 1: 1-2 calls, Tier 2: 5 calls, Tier 3: 8-10 calls) is more cost-efficient.

### 4.6 No Explicit Interaction Modeling

The plan states: "Focus on each entity's behaviour independently; let the scenario generator capture interactions." But interactions ARE the prediction problem. Trump's behaviour depends on Iran's response, which depends on Trump's signal, etc.

The Character Council prompt handles this implicitly (entities appear together in the prompt), but there's no explicit game-theoretic or causal structure for interactions. The prompt says "generate paths" and hopes the LLM captures the strategic interplay.

This isn't necessarily wrong — explicit interaction modeling is expensive and often over-engineered. But acknowledging it as a known limitation (rather than a design choice) would be more honest. For high-stakes multi-actor scenarios, the causal-first approach (Architecture F in elfmem's research synthesis) handles this better.

---

## 5. Mapping to elfmem's Architecture

### 5.1 Conceptual Correspondence

| KLS Plan | elfmem Equivalent | Status in elfmem |
|----------|-------------------|-----------------|
| Entity profiles (markdown) | Blocks in WORLD/SELF frames | ✅ Exists (blocks + frames) |
| 7-section profile structure | Structured block content + tags | ⚡ Could adopt as pattern |
| Character Council (scenario gen) | Simulation calibration | ✅ Designed, not implemented |
| Self-assessed plausibility | — | ❌ Avoided — elfmem uses ensemble |
| SELF correction (additive) | Outcome signals + Platt scaling | ✅ Better approach designed |
| Brier scoring | outcome() with signal calibration | ✅ Designed |
| Half-life / freshness | Temporal decay (session-aware) | ✅ Implemented |
| Layered triggers | Confidence gating (DOWN pattern) | ✅ Designed |
| Post-resolution audit | Calibration loop | ✅ Designed |
| 40 scenario paths | 5-8 archetypal paths | ✅ Better approach designed |
| Dual-audience output | — | ⚡ Could adopt |
| Missing-mass gap detection | Good-Turing estimators | ✅ Designed |
| Epistemic vs aleatoric | Uncertainty tagging | ✅ Designed |
| Strategy optimisation | — | ⚡ New capability |

### 5.2 Where elfmem Is Ahead

**Ensemble generation**: Instead of one LLM generating 40 paths and rating its own plausibility, elfmem's design uses N independent calls with median aggregation. More robust against single-generation biases.

**Platt scaling**: Instead of additive SELF corrections, elfmem uses sigmoid-based calibration that respects probability bounds. Parameters self-calibrate from accumulated Brier scores.

**Missing-mass estimation**: Good-Turing estimators for mathematically principled gap detection, rather than heuristic "empty recall = knowledge gap."

**Free energy scoring**: `G = α × pragmatic_value + (1-α) × epistemic_value` provides principled block-level scoring, replacing ad-hoc signal values.

**Confidence gating**: The DOWN pattern (debate only when uncertain) with measured gate accuracy tracking is more principled than heuristic trigger criteria.

### 5.3 Where the KLS Plan Is Ahead

**SELF with measured calibration data**: elfmem's SELF frame has constitutional blocks (principles, values). The KLS plan extends this with *quantified* failure modes — measured biases per domain, correction factors with evidence, a historical patterns section that grows with each resolved outcome. This is the gap: elfmem knows *who it is* but doesn't yet track *where it's systematically wrong*.

**Three SELF modes at different timescales**: Pre-decision (per-action), post-resolution (per-outcome), strategy optimisation (periodic). elfmem's outcome() handles the first two implicitly but lacks the explicit strategy optimisation mode that tests operating configurations against historical data.

**Entity profile structure**: The 7-section format (identity, decision style, incentives, historical patterns, constraints, triggers, failure modes) is a reusable pattern for modeling any decision-making entity. This could be adopted as an elfmem pattern for WORLD frame blocks.

**Dual-audience output**: Narrative sections for human review + JSON appendix for machine processing in a single file. elfmem's result types have `__str__`, `summary`, and `to_dict()` but don't have a standardised multi-audience document format for complex outputs like simulation results.

**Text-first deployment**: Phase 0 delivers value before any code exists. Entity profiles are just markdown files that work with existing recall. elfmem could adopt the same approach — define entity profiles as a block pattern, document the structure, and let agents use them immediately via recall.

---

## 6. What elfmem Should Adopt

### 6.1 SELF Profile with Measured Calibration Data (High Priority)

Extend the SELF frame beyond constitutional blocks to include measured calibration:

```
SELF frame (current):
  Constitutional blocks → identity, values, principles

SELF frame (extended):
  Constitutional blocks → identity, values, principles (permanent decay)
  Calibration blocks → measured biases, domain accuracy, failure modes (30-day half-life)
  Decision rules → operating parameters, thresholds (updated per-wave)
```

The calibration blocks would contain:
- **Measured failure modes**: "Geopolitical overconfidence: +0.12 (from N=8 resolved predictions)"
- **Domain accuracy**: "Crypto timing: Brier 0.18 (N=12). Macro rates: Brier 0.24 (N=6)."
- **Known triggers**: "High-salience news → recency bias; short horizon + binary event → overestimation"

These are fundamentally different from constitutional blocks — they're empirical, they update frequently, and they have short half-lives. But they live in the SELF frame because they're about the agent's own decision process.

### 6.2 Entity Profile Pattern for WORLD Frame (Medium Priority)

Adopt the 7-section entity profile as a standardised pattern for modeling decision-making entities:

```markdown
# Entity: [Name]

## Identity and Scope
Entity type, domains, confidence, half-life, tags

## Decision Style
How this entity thinks and decides

## Incentive Structure
What they're optimising for now

## Historical Patterns
What they've consistently done, with case studies

## Current Constraints
Limits on action space RIGHT NOW

## Trigger Conditions
What causes action vs. waiting

## Failure Modes
Characteristic blind spots and when they behave unpredictably
```

This pattern is useful beyond prediction markets — any domain where understanding actor behaviour matters (team dynamics, user modeling, competitive analysis).

### 6.3 Strategy Optimisation Mode (Medium Priority)

elfmem's outcome() and dream() handle per-action and per-session calibration. What's missing is periodic meta-calibration:

```
After N resolved outcomes, test the agent's decision rules:
  Config A: confidence threshold 0.5, no simulation
  Config B: confidence threshold 0.6, simulation for uncertain
  Config C: confidence threshold 0.7, simulation always

Measure: Brier score, decision quality, computational cost per config
Adopt the optimal configuration for next period.
```

This is the "strategy optimisation" mode — testing the agent's operating parameters against historical data to find what actually works. It's a higher-order calibration loop that improves the calibration process itself.

### 6.4 Dual-Audience Output Pattern (Low Priority)

For complex simulation outputs, adopt the pattern of narrative sections (human-readable) + structured data appendix (machine-processable) in a single document. elfmem's result types already have `__str__` and `to_dict()` — extending this to multi-section documents for simulation results would improve both human review and machine audit.

---

## 7. What elfmem Should NOT Adopt

### 7.1 Self-Assessed Plausibility

elfmem's ensemble approach (N independent calls + median) is strictly better than single-call generation with self-assessed plausibility ratings. Don't adopt the circular self-assessment pattern.

### 7.2 Additive SELF Corrections

Platt scaling is mathematically superior to additive bias offsets. elfmem's existing design uses sigmoid calibration. Don't regress to additive corrections.

### 7.3 40 Scenario Paths by Default

5-8 archetypal paths with ensemble diversity provides better signal at lower cost than 40 paths from a single call. The information gain per path diminishes rapidly after ~8.

### 7.4 Entity Profile Management CLI

The KLS plan includes `kls entity list/show/flag/audit/update` commands. elfmem doesn't need a separate entity management subsystem — entities are blocks. Use existing learn(), recall(), and outcome() operations. The entity profile pattern (Section 6.2) should be a documentation convention, not a code feature.

---

## 8. Synthesis: The Combined Architecture

Merging the KLS plan's strengths with elfmem's existing design:

```
┌─────────────────────────────────────────────────────────────────┐
│              BEHAVIOURAL SIMULATION IN ELFMEM                   │
│                                                                 │
│  SELF FRAME (extended):                                         │
│    Constitutional blocks → identity, values (permanent decay)   │
│    Calibration blocks → measured biases, accuracy (30d decay)   │
│    Decision rules → operating parameters (updated per-wave)     │
│                                                                 │
│  WORLD FRAME (entity profiles):                                 │
│    7-section entity profiles for decision-making actors         │
│    Standard pattern: identity, style, incentives, history,      │
│    constraints, triggers, failure modes                         │
│    Half-life by entity type (30-365 days)                       │
│                                                                 │
│  CONFIDENCE GATE (every recall):                                │
│    HIGH (>0.7): Act directly, inline calibrate                  │
│    MEDIUM (0.4-0.7): Quick ensemble (Tier 1, 2-3 LLM calls)    │
│    LOW (0.2-0.4): Full simulation (Tier 2, 5 LLM calls)        │
│    VERY LOW (<0.2): Deep analysis (Tier 3, 8-10 LLM calls)     │
│                                                                 │
│  SIMULATION ENGINE (when gated):                                │
│    1. Recall entity profiles from WORLD frame                   │
│    2. Extract causal structure (LLMs excel: 92% accuracy)       │
│    3. Generate scenarios constrained by entity profiles          │
│    4. Ensemble: N independent calls + median aggregation        │
│    5. Platt scaling on probabilities (not additive correction)  │
│    6. Free energy scoring per block (pragmatic + epistemic)     │
│    7. Missing-mass estimation for gap detection                 │
│                                                                 │
│  PRE-DECISION SELF CHECK (from KLS):                            │
│    Load SELF calibration blocks                                 │
│    Identify triggered failure modes for THIS situation          │
│    Apply Platt-scaled correction (not additive offset)          │
│    Output: corrected confidence + reliability assessment        │
│                                                                 │
│  POST-RESOLUTION LOOP:                                          │
│    Brier score computation                                      │
│    outcome() signals on blocks that supported correct/wrong     │
│    SELF calibration block updates (measured bias data)           │
│    Entity profile freshness check + targeted updates            │
│                                                                 │
│  STRATEGY OPTIMISATION (periodic, from KLS):                    │
│    Test operating configurations against resolved outcomes      │
│    Find optimal: confidence thresholds, simulation triggers     │
│    Adopt best configuration for next period                     │
│                                                                 │
│  OUTPUT FORMAT (dual-audience, from KLS):                       │
│    Sections 1-5: Human-readable narrative                       │
│    Section 6: Resolution record (filled post-hoc)               │
│    Section 7: JSON appendix for machine processing              │
│                                                                 │
│  BUILDS ON:                                                     │
│    elfmem primitives (learn, recall, outcome, dream, curate)    │
│    CalibratingAgent (session metrics, inline calibration)       │
│    Research synthesis (ensemble, Platt, free energy, gating)    │
│    KLS plan (SELF calibration, entity profiles, strategy opt)   │
└─────────────────────────────────────────────────────────────────┘
```

### Key Design Decisions in the Combined Architecture

**1. Entity profiles are blocks, not a separate subsystem.**
No entity management CLI. Learn entity profiles as blocks with structured content and hierarchical tags (`actor/trump/decision-style`, `actor/iran/trigger-conditions`). Recall by tag or semantic query. Update via learn() + consolidate(). This keeps the architecture simple and consistent with elfmem's axioms.

**2. SELF calibration blocks are distinct from constitutional blocks.**
Both live in the SELF frame, but with fundamentally different decay profiles. Constitutional blocks have permanent decay (~7.9 year half-life). Calibration blocks have 30-day half-life — they must be refreshed by resolved outcomes or they fade. This ensures calibration data doesn't go stale.

**3. Platt scaling replaces additive corrections everywhere.**
Whenever the system corrects a probability estimate — whether from SELF bias, ensemble disagreement, or historical calibration — use sigmoid calibration. Parameters a,b fit from resolved Brier data. Self-correcting, respects probability bounds, handles nonlinearity.

**4. Ensemble diversity over path quantity.**
3-5 independent LLM calls each generating 3-5 paths beats 1 call generating 40 paths. The diversity comes from independent generation, not volume. Median aggregation is robust to outlier calls.

**5. Causal extraction before scenario generation.**
LLMs extract causal structure at 92% accuracy. Build the causal graph FIRST ("what causes what, who influences whom"), then generate scenarios along causal paths. This leverages the LLM's strongest capability and produces traceable, interpretable predictions.

**6. Strategy optimisation is a new curate() variant.**
Run periodically (after N resolved outcomes). Test operating configurations. Adopt the best. This is meta-curation — curating the decision process, not just the knowledge.

---

## 9. Implementation Considerations

### Phase 0: Text-Only (No Code)

Adopt the text-first approach. Define entity profile and SELF calibration block patterns in documentation. Agents can use them immediately via learn() and recall():

```python
# Learn an entity profile
await system.learn(
    "Trump's public threats resolve to kinetic action ~15% of the time. "
    "Signal-to-noise ratio on military threats is ~1:6. "
    "Threshold: direct attack on US assets OR congressional authorization.",
    tags=["actor/trump/trigger-conditions", "domain/geopolitics"]
)

# Learn a SELF calibration block
await system.learn(
    "Geopolitical overconfidence: +0.12 estimated bias. "
    "Evidence: 8 resolved predictions, 6 overestimated probability. "
    "Trigger: high-salience news cluster → recency overweighting.",
    tags=["self/calibration/failure-mode", "domain/geopolitics"]
)

# Recall entity profile for simulation
entity_blocks = await system.frame(
    "trump decision style and trigger conditions",
    frame_type="world",
    top_k=5
)
```

### Phase 1: Pre-Decision SELF Check

Add SELF calibration recall before major decisions:

```python
# Recall SELF calibration blocks relevant to this decision
self_calibration = await system.frame(
    "failure modes for geopolitical predictions with short time horizons",
    frame_type="self",
    top_k=5
)
# Agent applies calibration context to decision
```

### Phase 2: Full Simulation Engine

Implement within elfmem's existing architecture:

```python
# Simulation as structured recall + scenario generation + outcome
entity_context = await system.frame("trump iran decision patterns", frame_type="world")
self_check = await system.frame("my calibration for geopolitical events", frame_type="self")

# Generate scenarios (via LLM adapter, constrained by entity profiles)
scenarios = await generate_ensemble_scenarios(
    thesis="US strikes Iran by Feb 28",
    entity_blocks=entity_context.blocks,
    self_blocks=self_check.blocks,
    n_calls=3,  # Ensemble diversity
    paths_per_call=5
)

# Score and calibrate
for block in entity_context.blocks:
    signal = compute_free_energy_signal(block, scenarios)
    await system.outcome([block.id], signal=signal)
```

### Phase 3: Strategy Optimisation

After sufficient resolved outcomes:

```python
# Test configurations against historical data
configs = [
    {"confidence_threshold": 0.5, "simulation": "never"},
    {"confidence_threshold": 0.6, "simulation": "uncertain_only"},
    {"confidence_threshold": 0.7, "simulation": "always"},
]
results = await test_configurations(configs, resolved_outcomes)
optimal = min(results, key=lambda r: r.brier_score)
# Adopt optimal config
```

---

## 10. Risk Assessment

### Risks of Adopting This Approach

| Risk | Severity | Mitigation |
|------|----------|------------|
| Entity profiles go stale silently | Medium | Half-life decay ensures stale profiles fade from recall; freshness tags enable audit |
| SELF calibration data is sparse early | High | Start with estimated biases, wide uncertainty; narrow as data accumulates |
| Over-reliance on simulation for routine decisions | Medium | Confidence gating ensures simulation only fires when genuinely uncertain |
| Entity profiles become echo chambers | Medium | Mandatory adversarial scenarios; periodic profile challenges |
| Platt scaling parameters are poorly fit | Low | Start with literature defaults (a=1.3, b=0.0); self-correct with data |
| Strategy optimisation overfits to small sample | Medium | Require minimum N=15 resolved outcomes; cross-validate configurations |

### Risks of NOT Adopting This Approach

| Risk | Severity | Notes |
|------|----------|-------|
| SELF frame lacks calibration data | High | Agent knows principles but not measured accuracy |
| No mechanism for self-correction | High | Same biases repeat without quantified awareness |
| Simulation operates on abstract knowledge only | Medium | Without entity profiles, scenario generation lacks behavioural grounding |
| No meta-calibration (strategy optimisation) | Medium | Operating parameters remain assumed-optimal, not measured-optimal |

---

## 11. Conclusions

The KLS behavioural simulation plan is a well-designed, pragmatic system that delivers value from Phase 0 (text-only entity profiles) through Phase 5 (full SELF calibration loop). Its core insight — treating the forecaster's own biases as a first-class simulation target — is the highest-leverage idea in the plan.

elfmem's existing simulation design (Confidence-Gated Free Energy Simulation from the research synthesis) is architecturally stronger in several areas: ensemble generation over self-assessed plausibility, Platt scaling over additive corrections, free energy scoring over ad-hoc signals, and missing-mass estimation for gap detection.

The combined architecture takes the best from both:

- **From KLS**: SELF calibration with measured data, entity profile structure, strategy optimisation mode, dual-audience output, text-first deployment
- **From elfmem**: ensemble aggregation, Platt scaling, free energy scoring, confidence gating, missing-mass estimation, causal extraction

The result is a behavioural simulation system that:
1. Only simulates when genuinely uncertain (confidence gating)
2. Grounds scenarios in specific actor behaviour (entity profiles)
3. Uses the strongest calibration techniques (ensemble + Platt + free energy)
4. Tracks and corrects its own biases (SELF calibration blocks)
5. Improves its own operating parameters (strategy optimisation)
6. Delivers value before any code is written (text-first Phase 0)

---

## Related Documents

- `examples/simulation_calibration.md` — Original simulation design with worked example
- `examples/simulation_research_synthesis.md` — 60+ paper synthesis, 7 architecture evaluations
- `examples/calibrating_agent.py` — Self-calibrating agent implementation
- `examples/agent_discipline.md` — Prompt instructions for agent discipline
- `docs/agent_usage_patterns_guide.md` — 20 core agent patterns
- `docs/cognitive_loop_operations_guide.md` — Cognitive loop framework
- `docs/CLAUDE_CODE_INTEGRATION.md` — MCP integration and agent discipline
- `docs/amgs_architecture.md` — Full technical specification
