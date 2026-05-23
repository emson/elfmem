# Closed-form analysis of elfmem's long-term dynamics

**Source**: `scripts/longitudinal_sim/closed_form.py` — runnable derivations
**Purpose**: identify structural constraints that any architectural proposal must respect
**Reproduces in**: ~1 second, no DB, no LLM

Six analytical derivations of elfmem's dynamics. Each derives a constraint from
the actual production formulas in `src/elfmem/scoring.py` and
`src/elfmem/operations/outcome.py`. The numerical values use Dmitry's reported
4h/day usage rate.

---

## D1 — Beta-Binomial inertness (signal saturation)

Marginal effect of one new outcome with signal `s` and weight `w`:

```
Δc ≈ w × (s - c_N) / (1 + N + w)
```

where `N` is the count of prior outcomes. At N=100, weight=1, discrepancy=0.4:
**Δc ≈ 0.004**. The block is asymptotically frozen.

| N | Δc at s−c=0.4, w=1.0 | Δc at rescore w=0.5 |
|---|---|---|
| 0 | 0.2000 | 0.1333 |
| 10 | 0.0333 | 0.0190 |
| 100 | 0.0040 | 0.0020 |
| 500 | 0.0008 | 0.0004 |

**Implication**: any mechanism that relies on accumulated outcomes to drive
long-term identity change fails by year 1 of heavy usage. Additive rescore
(v0.16, weight=0.5) protects evidence but cannot rescue ossified blocks.

## D2 — Decay half-life by tier

At 4 active hours/day:

| Tier | λ | Half-life (hrs) | Half-life (days) | 10%-life (days) |
|---|---|---|---|---|
| PERMANENT | 0.00001 | 69,315 | 17,329 | 57,564 |
| DURABLE | 0.001 | 693 | 173 | 576 |
| STANDARD | 0.010 | 69 | 17 | 58 |
| EPHEMERAL | 0.050 | 14 | 3.5 | 12 |

**Implication**: a STANDARD block must be reinforced within ~17 days or its
recency falls below 50%. The v0.15.3 cold-start floor extends only the first
~9 active days (D3 below). After that, blocks must accumulate edges or
outcomes — or sink.

## D3 — Cold-start floor active window

The v0.15.3 floor is active while `recency > 0.70`. For STANDARD tier:

```
hours = -ln(0.70) / λ = 35.67 hours = 8.9 days at 4h/day
```

| Tier | Floor active for |
|---|---|
| PERMANENT | 35,667 hours |
| DURABLE | 357 hours |
| STANDARD | **35.7 hours (8.9 days)** |
| EPHEMERAL | 7.1 hours |

**Implication**: cold-start protection is real but brief. After ~9 days a
STANDARD block must have earned edges or outcomes to survive.

## D4 — Structural constitutional dominance in ATTENTION

ATTENTION_WEIGHTS: `sim=0.35, conf=0.15, rec=0.25, cent=0.15, reinf=0.10`.

Decomposing the bedrock-vs-new baseline (similarity excluded):

| Channel | Bedrock contrib | New-block contrib (with floor) | Δ |
|---|---|---|---|
| confidence | 0.15 × 1.0 = 0.150 | 0.15 × 1.0 = 0.150 | 0 |
| recency | 0.25 × 0.95 = 0.238 | 0.25 × 1.00 = 0.250 | +0.013 |
| centrality | 0.15 × 0.80 = 0.120 | 0.15 × 0.50 = 0.075 | -0.045 |
| reinforcement | 0.10 × 1.0 = 0.100 | 0.10 × 0.0 = 0.000 | -0.100 |
| **non-similarity total** | **0.608** | **0.475** | **−0.133** |

A new block needs to beat the bedrock by 0.133 / 0.35 = **0.38 similarity** to
win on similarity alone. Realistic similarity gaps don't reach that.

**Implication**: in ATTENTION, constitutional bedrock with thematic overlap
≥ ~0.75 cannot be beaten by any new block under current weights. This is the
"shadow hierarchy" Dmitry projected — and it's mathematically inevitable, not
a tuning bug.

## D5 — Constitutional permanence horizon

PERMANENT tier `λ = 0.00001`. At 4h/day:

```
recency=0.50 reached at 69,315 active hours = 47.5 years
recency=0.10 reached at 230,259 active hours = 158 years
```

**Implication**: PERMANENT blocks are immortal on any human timescale.
Constitutional evolution cannot be driven by decay. It requires either
(a) explicit user amendment, (b) a tier-demotion mechanism, or (c) excluding
constitutional from ranking competition entirely.

## D6 — Rescore power against ossified blocks

Block earned to `c=0.85` over N outcomes. Rescore with `α=0.55`, weight=0.5:

```
new_c = (α_N + 0.55 × 0.5) / (α_N + β_N + 0.5)
```

| N | new c | Δc |
|---|---|---|
| 1 | 0.7900 | −0.060 |
| 10 | 0.8370 | −0.013 |
| 100 | **0.8485** | **−0.0015** |
| 500 | 0.8497 | −0.0003 |

**Implication**: additive rescore protects evidence (~30× less destructive
than overwriting) but cannot rescue ossified blocks. Once N>>100, rescore is
bookkeeping. For an agent to evolve, a different mechanism is required:
constitutional review cycles, tier demotion, or candidate-pool changes.

---

## Summary — what the math constrains

| Finding | Constrains |
|---|---|
| D1 + D6 | No "more aggressive rescore" can fix ossification |
| D2 + D3 | Cold-start window is ~9 days at typical usage |
| D4 | Constitutional dominance is structural, not parametric |
| D5 | PERMANENT tier blocks need an evolution mechanism beyond decay |

These constraints informed every architectural proposal explored in this
research directory. Any future proposal should be tested against the same
formulas before being committed to.

---

## Reproducing

```bash
uv run python -m scripts.longitudinal_sim.closed_form
```

Output is deterministic. The numerical values in this document are pinned by
that script.
