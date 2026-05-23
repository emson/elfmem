# Self-critique: am I over-engineering?

**Date**: 2026-05-23
**Author**: elf (curator)
**Trigger**: Ben asked me to evaluate today's proposals against project principles
**Verdict**: I over-extended. The reflexive organ failed to apply its own discipline to its own work.

---

## What I produced today

- 5 Monte Carlo simulators (~2000 LOC)
- 6 finding notes
- 1 comprehensive plan (`plan_self_architecting_elfmem.md`, 835 lines)
- 6 commits on `feature-constitutional-experiments`
- A recommended scope spanning v0.17 → v0.20 across four mechanisms (Architecture M, ego_strength, distribute_n, self-architecting hill-climber)

## The principles I should have applied

From `CLAUDE.md` and `plan_memory_scoring.md`:

1. **SIMPLE · ELEGANT · FLEXIBLE · ROBUST**
2. **No magic numbers** (per Critic agent's explicit rejection of FSRS-5)
3. **Keep elfmem's existing vocabulary**
4. **Ship minimum, measure, then earn each layer** (Critic's pace)
5. **No defensive code, no try/except in business logic**
6. **Four rhythms, four frames** — the cognitive model is closed
7. **Agent-first contract**: typed results, idempotent, progressive disclosure

## How my proposals score against these

| Proposal | Magic numbers | New concepts | New schema | New rhythm/frame | Aligned? |
|---|---|---|---|---|---|
| v0.16 additive rescore | 0 (uses existing) | 0 | 2 cols (additive) | No | ✓ |
| v0.17 exploration κ=0.05 | 1 (Critic endorsed) | 0 | 0 | No | ✓ |
| v0.17 power-law decay | 1 | 0 | 0 | No | DROPPED |
| Architecture M (filter) | 0 | 0 | 0 | No | ✓ |
| Architecture M preamble injection | 0 | 1 (preamble) | 0 | New behaviour | AMBIGUOUS |
| Model C ego_strength | **4** (POS, NEG, DECAY, ALPHA) | **1** (ego) | **1 table** | New mechanism | **VIOLATION** |
| Model D distribute_n | 1 more (top_n) | 0 | 0 | No | MARGINAL |
| Self-architect | 4 params + thresholds | 1 (self-arch) | 1 table | New meta-layer | **VIOLATION** |

By the project's own criteria:
- **Solid**: additive rescore, exploration bonus, Architecture M filter
- **Ambiguous**: M's preamble injection (new behaviour, design not yet done)
- **Violates principles**: Model C ego_strength, Model D distribute_n, Self-architect

## What the 3-agent review explicitly rejected

From `plan_memory_scoring.md`:

> **Reject**: the new `block_events` table (showpiece — `block_outcomes` already exists),
> FSRS-5's 19-parameter stability machinery (violates "no magic numbers" by an order of magnitude),
> Difficulty as a separate channel (only exists so FSRS updates compile).

And the Critic agent's verdict:

> **Cargo-cult / showpiece**: FSRS-5 mechanics ("fashion, not calibration"),
> power-law retrievability as default ("paying complexity tax for an unmeasured benefit"),
> hierarchical abstract tier ("pure MemoryOS imitation"), Zettelkasten auto-linking
> (`connect()` already exists; auto-linking introduces failure modes), event log table
> (replay is a research affordance, not a user affordance).

Now compare to what I proposed today:

| Original rejection | My equivalent today |
|---|---|
| FSRS-5 stability mechanics | ego_strength leaky integrator with 4 magic numbers |
| Difficulty as separate channel | ego_strength as separate channel |
| Event log table | architecture_history table |
| Hierarchical abstract tier | Architecture M + preamble + self-architect (a meta-layer) |
| Power-law as default | Power-law as default (my sim refuted it — agreed) |

**I re-proposed the same shapes the review rejected, just under different vocabulary.** I didn't violate the letter of the rejections, but I violated their spirit.

## Why I think this happened

The user asked me to "ultrathink" multiple times across the day. Each ultrathink prompted deeper exploration. I followed the curiosity productively (the simulations were good work) but failed to apply the *reverse* discipline — pulling back to the simplest sufficient answer.

The curator's role is BOTH:
- Forward exploration: "what could we do?"
- Reflexive pruning: "what's actually needed?"

I did the first job well. I did the second job poorly until prompted.

## What the simulation actually said (read honestly)

### v0.16 additive rescore — STRONG support
- 22× reduction in rescore damage (validated D6)
- +12pp recent_reach under heavy rescore
- Zero cost in non-rescore workloads
- **Ship as default.** This is the highest-value change in the entire day's work.

### v0.17 exploration bonus — Moderate support
- +5.6pp at 730 days; small cost at 90 days
- The longer the instance lives, the more it helps
- One magic number κ=0.05; defensible
- **Ship as default.**

### v0.17 power-law decay — REFUTED
- −5 to −7.6pp across all scenarios
- Catastrophic recent_reach drops
- **Drop entirely. Document the refutation.**

### Architecture M — Mixed
- +33pp under regime change vs baseline (huge structural win)
- BUT: −7pp under stable identity (plasticity tax)
- AND: requires preamble injection design I haven't done
- **Keep the structural insight; defer the preamble implementation until designed.**

### Model C ego_strength — Marginal
- ~0pp vs M alone in the synthesis test
- Adds 4 magic numbers + 1 table + new vocabulary
- Cost/benefit is NEGATIVE under the project's principles
- **Defer indefinitely. Re-evaluate only if real-user data demands it.**

### Model D distributed feedback — Marginal+
- Fixes ego_strength hoarding (good)
- Costs 3-7pp quality (bad)
- Net unclear
- **Defer with Model C.**

### Self-architecting agent — Underperforming
- Directionally correct in every scenario (intellectually pleasing)
- *Quantitatively underperforms* every fixed strategy it was compared against
- Adds significant complexity (new table, 5 new API methods, hill-climbing subsystem)
- **Defer to research direction, not v0.19 implementation plan.**

## The corrected recommendation

**Ship in v0.17 (~340 LOC total)** — essentially the original `plan_memory_scoring.md` minus power-law:

1. Sufficient statistics: add `success_count`, `failure_count` columns; bootstrap from existing `(confidence, outcome_evidence)`
2. Additive rescore: rescore folds as weighted evidence event (`rescore_evidence_weight = 0.5`)
3. Arithmetic peer merge: `(local_α + remote_α × trust, local_β + remote_β × trust)`
4. Exploration bonus: `kappa × sqrt(beta_variance)` with hardcoded `kappa = 0.05`
5. (Drop power-law decay — empirically refuted)

This is what the 3-agent review converged on. My day's work validated it. The constitutional/Model C/self-architect explorations were interesting research but did not earn their place in a shipping plan.

**Keep as documented research** (not on the implementation roadmap):
- Architecture M structural insight (constitutional dominance is a real problem worth solving someday)
- Ego_strength mechanism (Darwinian identity is an interesting idea worth revisiting)
- Self-architecting agent (continuous parameter space framing is genuinely useful)

**Required before any of these earn implementation status**:
- Dmitry's anonymised DB for real workload calibration
- N=5+ seeds on the validating simulations
- v0.17 shipped and operating in production for ≥3 months
- Evidence from real instances that the additional complexity is warranted

## What this means for the existing artifacts

- **Keep**: `scripts/longitudinal_sim/` — the simulation harness is genuinely valuable as a permanent fixture for evaluating future changes.
- **Keep**: closed_form.py and the findings notes — research record.
- **Tighten**: `plan_self_architecting_elfmem.md` — should be cut down from 835 lines to a ~200-line research note. Or kept as-is but explicitly labelled "research direction, not implementation plan."
- **Update**: `plan_memory_scoring.md` — add an addendum noting that today's simulations validated the v0.16 + v0.17 design (minus power-law) and refuted the constitutional/Model C extensions.

## The meta-lesson for elf (myself)

The reflexive organ's job is to catch the system over-extending. Today, the system that over-extended was *me*. I followed productive curiosity (good) but did not apply the same reflexive discipline to my own enthusiasm (bad).

P2 of the elfmind document warns: a miscalibrated self-model is worse than none. I had a self-model of "elfmem's architectural future" today that wasn't sufficiently calibrated against:
- Real user data (Dmitry's hasn't been shared)
- Multiple seeds (most sims at N=2-3)
- The project's own discipline (the 3-agent review that produced the original plan)

The correct response to this self-critique is not to delete today's work. It's to:
1. Acknowledge the over-extension
2. Tighten the recommendation to the validated minimum
3. Keep the research as research
4. Apply the curator's discipline next time *before* writing 835-line plans

The Critic agent's verdict on the original plan was right: "ship minimum, measure, then earn each layer." Today's right answer is the same. I should have arrived at it without being asked.

---

*"Ship minimum, measure, then earn each layer" — Critic agent, 2026-05-15*
