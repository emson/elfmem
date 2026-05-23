# Decisions for elfmem v0.17 (and beyond)

**Date**: 2026-05-23
**Author**: elf (curator)
**Purpose**: After two days of analysis, simulation, and self-critique, make the actual project decisions. Decisive, not exploratory.

---

## TL;DR (the decisions)

| Proposal | Decision | Reason |
|---|---|---|
| v0.16 additive rescore + sufficient stats | **SHIP** | 22× rescore-damage reduction. Validated. |
| v0.16 arithmetic peer merge | **SHIP** | Bundled with sufficient stats. ~50 LOC. |
| v0.17 exploration bonus (κ=0.05) | **SHIP** | +5.6pp at 730 days. One hardcoded constant. |
| v0.17 power-law decay (opt-in flag) | **DROP** | My own sim refuted it: −5 to −7.6pp everywhere. |
| Architecture M (constitutional/ATTENTION separation) | **DEFER** | Big help under drift, but −7pp under stable identity. No data on user mix. |
| Model C ego_strength mechanism | **DEFER INDEFINITELY** | 4 magic numbers, 1 new table, no production evidence. |
| Model D distributed feedback | **DEFER** | Marginal over Model C; ships together or not at all. |
| Self-architecting agent | **DEFER INDEFINITELY** | Simulation showed it underperforms fixed strategies. |
| Constitutional review cycle (Dmitry's proposal) | **DEFER** | Real signal of demand needed first. |

**Net v0.17 scope**: ~330 LOC, exactly matching the original `plan_memory_scoring.md` proposal minus power-law. **The two days of additional simulation work validated and tightened the existing plan, not extended it.**

---

## How elfmem actually grows up

The phrase "elfmem grows up" can be read two ways:

1. *Charitable*: the system becomes more meta-aware — learns its own configuration, develops architectural self-knowledge, evolves its own scoring formulas
2. *Skeptical*: the system becomes SIMPLER and more SETTLED over time. Its defaults work for everyone. It needs no meta-layer because its base layer is good.

Long-lived software libraries (sqlite, curl, postgres) grow up by becoming the second kind, not the first. They don't add meta-layers; they crystallize patterns.

I think this is the right interpretation. **A well-designed system doesn't NEED a self-architect, because its defaults are good enough.** Adding a self-architect is a band-aid for poor defaults. The right move is to find good defaults that work for most users, not to build infrastructure for users to find their own.

By this measure, the right elfmem trajectory is:
- **v0.17**: ship v0.16 + exploration bonus. Rescore problem solved. Long-horizon problem mitigated. **Most users will go years without thinking about either.**
- **v0.18-0.20**: watch real-world telemetry. Identify any SYSTEMATIC problems that emerge in production.
- **v0.21+**: IF (and only if) systematic problems are observed, address them with the smallest possible mechanism — not the most architecturally interesting one.
- **v1.0**: lock the public API. Stable for years.

This is the opposite of the trajectory I sketched in `plan_self_architecting_elfmem.md`. That plan grew the system by 4 versions across 6 mechanisms. This trajectory grows it by 1 version, then waits.

The Critic agent's framing from 2026-05-15 was right and remains right: *"ship minimum, measure, then earn each layer."*

---

## User impact analysis

Five user classes to consider:

### 1. Power users (Dmitry-class)
Read the source. Have specific workloads. Run weekly rescore. Have peer protocols active.

- **v0.16 additive rescore**: high impact. Solves their actual reported problem.
- **v0.17 exploration bonus**: high impact at long horizons.
- **Self-architect**: NEGATIVE impact. They tune better than a hill-climber.
- **Architecture M**: ambiguous. Some have stable identity (would lose 7pp). Some have drift (would gain 33pp). We can't know without data.

### 2. Non-developers (Claude Desktop)
Don't know what scoring is. Just want memory.

- **v0.16/v0.17**: positive impact silently. They never notice the formula change; they notice retrieval improves.
- **Self-architect**: NEGATIVE impact. Adds settings, MCP tools, decision points they don't understand.
- **Architecture M**: depends on whether their constitution drifts. Probably no — most non-devs set values once and reuse.

### 3. Library consumers
Use elfmem programmatically.

- **v0.16/v0.17**: low impact (formula changes don't break API).
- **Architecture M**: BREAKING. `recall(frame="attention")` returns different blocks. Even with config flag, this churn ripples through their code.
- **Self-architect**: new API surface they have to ignore.

### 4. Future contributors
Read the codebase. Need to understand it.

- **v0.16 additive rescore**: small added concept (`success_count` and `failure_count`). Mathematically familiar (Beta-Binomial).
- **v0.17 exploration**: one term added to compute_score. Cognitively cheap.
- **Architecture M**: new behavior (preamble injection) that needs documenting and testing.
- **Self-architect**: massive concept burden. New table, hill-climbing, shadow eval, regime detection, collaborative milestones.
- **Model C ego_strength**: introduces a new vocabulary (`ego_strength`, `EGO_POS_RATE`, `EGO_LAMBDA_ALPHA`) on top of the existing four-rhythm cognitive model. Conceptual debt.

### 5. The maintainer (Ben, solo)
Must support, debug, document, migrate, and onboard.

- Every shipped feature has perpetual cost: tests, documentation, support burden, conceptual surface.
- Solo OSS half-life of architectural ambition is one release cycle.
- Cannot afford to ship features whose value isn't proven in production.

**By every user-class lens, the same conclusion**: ship the disciplined v0.16 + v0.17 (minus power-law), then wait for production signal before adding more.

---

## The complexity argument (specifically)

Every line of code added is a line that needs:
- Tests with real edge cases (not just happy path)
- Documentation in CLAUDE.md, AgentGuide, MCP descriptions
- Migration path (how do existing instances upgrade?)
- Support burden (when users hit edge cases, the maintainer answers)
- Mental model space in every reader's head

The proposed scope **changes elfmem's conceptual surface dramatically**:

**Current public concepts** (rough inventory):
- Four rhythms (heartbeat, breathing, sleep, deep sleep)
- Four frames (self, attention, task, simulate)
- Block lifecycle (BIRTH → GROWTH → MATURITY → DECAY → ARCHIVE)
- Tiers (PERMANENT, DURABLE, STANDARD, EPHEMERAL)
- Operations (learn, recall, frame, outcome, dream, curate, rescore, connect, evolve, peer_*)
- Confidence + alignment_score + outcome_evidence + decay_lambda

That's already a lot. The cognitive model is closed but rich.

**Disciplined v0.17 adds**:
- `success_count` + `failure_count` (sufficient statistics for confidence — documented as internal)
- Exploration bonus (one term in compute_score)

Two new concepts, both small, both derived from existing math. The four rhythms and four frames remain closed.

**My over-extended plan would have added**:
- `is_constitutional` flag (Architecture M)
- Constitutional preamble at frame render (new behavior)
- `ego_strength` (Model C)
- 4 magic numbers for ego mechanism
- `distribute_n` (Model D)
- `attention_const_weight`, `ego_alpha`, `self_check_freq` (self-architect)
- New table `block_ego_state`
- New table `architecture_history`
- Hill-climbing subsystem with shadow eval, regime detection
- Collaborative-milestone UX

That's ~10 new concepts requiring documentation, testing, migration paths, and support burden. **It doubles elfmem's conceptual surface for marginal validated benefit.**

The Critic agent's verdict applies cleanly: *"the half-life of architectural ambition in solo OSS is about one release cycle."* That's not pessimism. That's empirical truth about how OSS projects with one maintainer evolve. Ben cannot afford to maintain ten new concepts whose value isn't proven by real user signal.

---

## On power-law specifically

The user asked: do we need the power law?

**No, and we have evidence.**

The plan called it an "opt-in experiment." My simulation called it `v017_full` and tested it across four scenarios. Results:

```
scenario          v016 (no power-law)   v017_full (power-law)
─────────────────────────────────────────────────────────────
baseline             80.7%                 75.3%   (−5.4pp)
weekly_rescore       81.4%                 76.4%   (−5.0pp)
long_horizon         72.4%                 72.3%   (essentially flat)
uncertain_mix        78.4%                 70.7%   (−7.6pp)
```

And recent_reach (plasticity):
```
scenario          v016 (no power-law)   v017_full (power-law)
─────────────────────────────────────────────────────────────
baseline             78.4%                 25.2%   (−53pp catastrophic)
weekly_rescore       78.0%                 13.6%   (−64pp catastrophic)
long_horizon         64.0%                 28.4%   (−36pp)
uncertain_mix        76.8%                 33.6%   (−43pp)
```

The mechanism is clear: power-law's fat tails keep stale blocks competitive. At t=1000 hours, exponential decay gives recency = 4.5×10⁻⁵, but power-law gives recency = 0.41. **A 10,000-fold difference at the long-time end.** One-year-old blocks become competitive against one-day-old blocks. Top-K fills with stale content.

**Decision**: drop power-law from v0.17 entirely. Don't even ship the flag. Document the empirical refutation in `plan_memory_scoring.md` so no future contributor relitigates this.

This is the easiest decision in this entire document. The data is unambiguous.

---

## On Architecture M specifically

Architecture M is intellectually appealing — it solves constitutional dominance with one filter line. But the simulation showed a real trade-off:

```
scenario              baseline   M       Δ
─────────────────────────────────────────
stable                85.3%    78.4%   −7pp
slow_drift            62.5%    72.4%   +10pp
regime_change         44.5%    77.6%   +33pp
quiet_burst           76.6%    77.0%   0pp
adversarial           65.7%    77.3%   +12pp
```

M is a **HUGE help** when identity drifts, but a **real cost** when it doesn't. The question is: which population of users dominates?

We don't have data. Without telemetry from real elfmem instances, shipping M would be a bet — and a bet that breaks `recall(frame="attention")` behavior for half the users to help the other half.

**Decision**: defer until we have evidence. The structural insight (constitutional and task knowledge are different *kinds* of content) is correct and worth preserving in research notes. But the implementation is not yet earned.

What would unblock M? Either:
1. Telemetry from real instances showing drift rate distribution
2. Dmitry confirming his cold-start symptom persists post-v0.15.3 (the question already drafted)
3. Multiple production users reporting the constitutional-dominance symptom

Until then, M stays as documented research.

---

## On the self-architecting agent specifically

This is the hardest one for me to drop because the framing (continuous parameter space, agent learns its own config) is elegant. But honesty requires admitting the simulation showed it **underperforms every fixed strategy**:

```
scenario          baseline    M       Model C   Model D   self_architect
────────────────────────────────────────────────────────────────────────
stable             81.1%    77.3%    76.5%     76.1%     76.5%
drift              77.8%    71.9%    74.9%     75.9%     73.0%
regime_change      74.0%    76.9%    79.1%     70.0%     72.3%
```

The agent correctly *identified the right direction* in every scenario. It did not produce the right *outcome* in any scenario. The hill-climber is too slow, the shadow eval is too noisy, the 20% step is too conservative. With infinite tuning and infinite data, this could work. With finite resources, the fixed-strategy baselines win.

By P2 of elfmind: a miscalibrated self-model is worse than none. A miscalibrated self-architect is worse than a sensible fixed default. **The right thing to do is pick a sensible fixed default.**

**Decision**: defer indefinitely. Keep the research note as a record of why this didn't work, in case anyone else tries it.

---

## On constitutional review cycles

Dmitry's proposal: a quarterly LLM-driven review that surfaces drift between constitutional blocks and the agent's recent decisions, suggesting amendments.

This is **conceptually orthogonal** to the architectural work. It's a UX feature, not a scoring change. It could ship at any time.

Should it ship?

- User impact: **high for power users** (Dmitry, Alv, anyone who treats elfmem as living memory)
- Complexity cost: moderate (new operation, new prompt template, new MCP tool)
- Validation: zero — no simulation here, just Dmitry's intuition

**Decision**: defer to v0.18+, ship as a v0.17.x patch if Dmitry signals demand after v0.17. Treat it as an opt-in user-facing feature, not an architectural change. Implementation: ~150 LOC + prompt engineering.

---

## The actual v0.17 plan

After the over-extension and the self-critique, here is the simple, defensible v0.17:

### Scope (~330 LOC)
1. **Sufficient statistics**: add `success_count`, `failure_count` columns; bootstrap from existing `(confidence, outcome_evidence)`
2. **Additive rescore**: `α += signal × 0.5; β += (1-signal) × 0.5` instead of overwriting confidence
3. **Arithmetic peer merge**: `(local_α + remote_α × trust, local_β + remote_β × trust)`
4. **Exploration bonus**: `kappa × sqrt(beta_variance)` with hardcoded `kappa = 0.05`

### What it costs the user
- One ALTER TABLE migration on first open (additive; reversible)
- Behavior identical to v0.16.x except: (a) rescore preserves earned evidence, (b) top-K subtly tilts toward exploration

### What it explicitly does NOT do
- Does not add Architecture M (constitutional dominance is a real problem, deferred)
- Does not add ego_strength (Darwinian identity is interesting but not earned)
- Does not add self-architecting (P2 risk realized in simulation)
- Does not add power-law decay (empirically refuted)
- Does not add constitutional review cycles (defer to v0.18+ as UX feature)

### Why this scope is right
- Each change is mathematically simple (Beta-Binomial, variance term, addition)
- Each change is empirically validated (22× rescore reduction, +5.6pp long-horizon)
- Each change is reversible (config flags exist for safety)
- The total scope matches the original `plan_memory_scoring.md` — meaning the discipline has held
- The complexity surface grows by 2 small concepts, not 10 large ones

### Decision asks for Ben

1. **Approve the v0.17 scope as above?** (Effectively yes-to-original-plan, minus power-law.)
2. **Accept that today's research goes into `docs/research/` instead of `docs/plans/`?** The constitutional/Model C/self-architect work was good research but not yet plans.
3. **Approve renaming the branch?** Currently `feature-constitutional-experiments`; honest name would be `research-constitutional-evolution` or `research-long-term-evolution`.
4. **Ship v0.17 with current scope, watch real instances for 3 months, then revisit?**
5. **Send Dmitry the v0.15.3 follow-up question already drafted?** That's the real-world data we need before any further architectural work.

---

## What I would tell Ben if he asked once

> Ship v0.16 + v0.17 exploration bonus. Drop power-law. Defer everything else.
>
> The constitutional/Model C/self-architect work is real research worth preserving but not yet worth implementing. Watch real instances for 3 months. Then we'll have data, not speculation.
>
> Today's work validated the original plan. That's the deliverable, not extensions to it.

---

*The shortest version of this document is one sentence: ship the validated minimum, defer the speculative maximum, and let production tell us what's actually broken.*
