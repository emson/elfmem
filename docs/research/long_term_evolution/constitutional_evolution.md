# Constitutional evolution — research compilation

**Status**: deferred (see [ADR 0003](../../decisions/0003-defer-constitutional-evolution.md))
**Sources**: four exploratory notes from 2026-05-22, archived in `_archived/`
**Purpose**: distill what was explored, what was found, and why the mechanisms didn't ship

---

## The problem

Closed-form derivations D4 and D5 (see [`closed_form_analysis.md`](closed_form_analysis.md))
established two structural problems:

- **D4**: in ATTENTION frame, constitutional bedrock with thematic overlap ≥ 0.75 cannot be beaten by any new block under current weights — mathematically inevitable
- **D5**: PERMANENT tier half-life is 47.5 years; constitutional cannot evolve via decay

These compound into a long-term failure mode: as the agent's identity drifts
over years, constitutional blocks ossify and dominate retrieval inappropriately.

## Four mechanism families explored

### Architecture M — structural separation

**Idea**: constitutional content is a different *kind* of content from task
knowledge. Don't force them into one ranking pool. Filter constitutional out
of ATTENTION candidates; inject as a preamble at frame render time.

**Implementation cost**: one-line filter, plus design for preamble injection
(template, token budget, query-relevance filtering).

**Simulation result** (4 strategies × 5 scenarios × 365 days):

```
scenario          baseline   M
─────────────────────────────────
stable             85.3%    78.4%   ← −7pp tax
slow_drift         62.5%    72.4%   ← +10pp
regime_change      44.5%    77.6%   ← +33pp
quiet_burst        76.6%    77.0%   ← 0pp
adversarial        65.7%    77.3%   ← +12pp
```

**Verdict**: big help under drift, real cost under stability. Without
telemetry on real-user drift distribution, shipping M is a bet that helps
some users while harming others.

### Model C — Darwinian identity (ego_strength mechanism)

**Idea**: constitutional should earn persistence through positive outcomes,
not be immortal by decree. Add a per-block `ego_strength` that modulates
effective decay rate. SELF-frame queries provide a dedicated reinforcement
channel.

**Implementation cost**: 4 magic numbers (`EGO_POS_RATE=1.0`, `EGO_NEG_RATE=0.3`,
`EGO_TIME_DECAY=0.05`, `EGO_LAMBDA_ALPHA=0.05`), 1 new schema table
(`block_ego_state`), new vocabulary.

**Simulation result**:

```
strategy            qratio  ego_moat  hoarding (es_concentration)
─────────────────────────────────────────────────────────────────
baseline             45.0%   98.4%     n/a
Architecture M       78.2%    0.0%     n/a
Model C              75.3%    0.0%    HOARDING (9.5×)
```

**Verdict**: marginal over M alone (75.3% vs 78.2%). Adds 4 magic numbers and
exhibits a clear failure mode: ONE constitutional block dominates
ego_strength accumulation (concentration=9.5× across every scenario). The
Critic agent's charge against FSRS-5 — "fashion, not calibration" — applies
cleanly to ego_strength.

### Model D — distributed feedback

**Idea**: Model C's hoarding is winner-take-all. Distribute SELF-query
feedback across top-3 constitutional with softmax weights.

**Implementation cost**: one more parameter (`distribute_n`), modest logic.

**Simulation result** (vs Model C):

- Hoarding fixed: concentration 9.5× → 3.3×
- Quality cost: 3-7pp drop in 3 of 5 scenarios

**Verdict**: fixes one failure mode by introducing a quality cost. Net unclear.

### Self-architecting agent — meta-layer

**Idea**: the four named architectures (baseline, M, Model C, Model D) are
not discrete; they're settings in a 4D parameter space (`attention_const_weight`,
`ego_alpha`, `distribute_n`, `self_check_freq`). Hill-climb to learn the right
configuration per workload.

**Implementation cost**: new table (`architecture_history`), 5+ API methods,
hill-climbing subsystem with shadow eval, regime detection, collaborative
milestones — significant.

**Simulation result** (hill-climber starts M-like, adapts every 28 days):

```
scenario          baseline   M       Model C   Model D   self_architect
────────────────────────────────────────────────────────────────────────
stable             81.1%    77.3%    76.5%     76.1%     76.5%
drift              77.8%    71.9%    74.9%     75.9%     73.0%
regime_change      74.0%    76.9%    79.1%     70.0%     72.3%
```

The agent correctly identified the right *direction* for each scenario
(stable → raise atw toward 1.0; drift → keep atw=0, raise distribute_n;
regime change → raise ego_alpha). But it **underperforms every fixed strategy
in quality**.

**Verdict**: directionally correct, quantitatively underperforming. By P2 of
elfmind: a miscalibrated self-model is worse than no self-model. The
hill-climber is the miscalibrated self-model.

## Why all four are deferred

Common pattern: each mechanism is intellectually interesting but doesn't
produce a decisively-better-than-baseline result across the simulated user
mix. Shipping any of them is a bet on a workload distribution we don't yet
have data for.

Specific failures of project discipline:
- Model C introduces 4 magic numbers + new vocabulary — the exact shape the
  `plan_memory_scoring.md` 3-agent review rejected for FSRS-5
- Model D adds yet more parameters
- Self-architecting adds an entire meta-layer that the simulation refutes
- Architecture M is conceptually clean but trades one failure mode (drift)
  for another (stability tax)

See [ADR 0003](../../decisions/0003-defer-constitutional-evolution.md) for
the formal decision.

## What we keep from this research

- The structural insight that constitutional and task content are different
  *kinds* of content — this remains true even if Architecture M doesn't ship
- The simulation harness in `scripts/longitudinal_sim/` is a permanent fixture
- The empirical refutation of these mechanisms saves future contributors from
  rediscovering them

## Trigger to revisit

Any of:
1. Production reports from multiple users that constitutional dominance is
   causing retrieval failures (currently: only Dmitry's report, addressed by
   v0.15.3)
2. Telemetry on real-user drift rate distribution
3. Benchmark evidence (MemoryAgentBench / LoCoMo) showing one of these
   mechanisms wins on agent workloads
4. Higher-confidence simulation (N≥10 seeds across more scenarios) confirming
   the marginal results

## Archived raw notes

For full reasoning history (not required reading; the above is the distilled
synthesis):

- `_archived/mc_evolution_findings.md` — update-rule tweaks don't work
- `_archived/constitutional_architecture.md` — Architecture M discovery
- `_archived/ego_feedback_findings.md` — Model C synthesis
- `_archived/full_scenario_findings.md` — cross-scenario evaluation
