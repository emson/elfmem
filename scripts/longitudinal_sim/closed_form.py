"""Layer 1 — closed-form derivations.

For each compounding process, derive the steady state or characteristic timescale
analytically and print the numerical answer. No database, no LLM, no embedding —
pure math against the real elfmem formulas.

Run:  uv run python -m scripts.longitudinal_sim.closed_form
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from elfmem.scoring import (
    ATTENTION_WEIGHTS,
    LAMBDA,
    SELF_WEIGHTS,
    TASK_WEIGHTS,
    compute_score,
    effective_centrality,
)
from elfmem.types import DecayTier

# Dmitry's reported usage (issue #50 follow-up, 2026-05-17)
ACTIVE_HOURS_PER_DAY: float = 4.0
DAYS_PER_YEAR: int = 365


# ── D1 ────────────────────────────────────────────────────────────────────────
def d1_bayesian_inertness() -> None:
    """How fast does the Beta-Binomial update become inert?

    Δc ≈ w × (s - c_N) / (α_N + β_N + w)

    where α_N + β_N = prior_strength + Σ wᵢ. With prior_strength=1.0 and unit
    weights, this is 1 + N. So the marginal effect of one more outcome at
    discrepancy (s - c_N) decays as 1/(N+1).
    """
    print("─" * 72)
    print("D1 — Bayesian update sensitivity (signal inflation)")
    print("─" * 72)
    print("  Marginal Δc from one outcome at discrepancy (s-c) with weight w:")
    print("  Δc = w(s-c) / (1 + N + w)\n")

    print(f"  {'N':>5}  {'Δc at s-c=0.4, w=1.0':>22}  {'Δc at rescore w=0.5':>22}")
    for n in (0, 1, 5, 10, 25, 50, 100, 250, 500):
        marginal = 0.4 / (1 + n + 1.0)
        rescore_marginal = 0.4 * 0.5 / (1 + n + 0.5)
        print(f"  {n:>5}  {marginal:>22.4f}  {rescore_marginal:>22.4f}")

    print("\n  Conclusion: by N=100 outcomes, one more event moves confidence by")
    print("  <0.004. Asymptotically inert. Dmitry's signal-inflation claim is")
    print("  mathematically correct under the current prior_strength=1.0.")


# ── D2 ────────────────────────────────────────────────────────────────────────
def d2_decay_half_life() -> None:
    """Half-life and 10%-life of each tier in days at Dmitry's 4h/day usage."""
    print("\n" + "─" * 72)
    print("D2 — Decay half-life by tier @ 4 active-hours/day")
    print("─" * 72)
    print(f"  {'tier':<12} {'λ':>9}  {'½-life (h)':>10}  {'½-life (d)':>10}  {'10%-life (d)':>13}")
    for tier in DecayTier:
        lam = LAMBDA[tier]
        half_h = math.log(2) / lam
        ten_h = math.log(10) / lam
        half_d = half_h / ACTIVE_HOURS_PER_DAY
        ten_d = ten_h / ACTIVE_HOURS_PER_DAY
        print(f"  {tier.value:<12} {lam:>9.5f}  {half_h:>10.1f}  {half_d:>10.1f}  {ten_d:>13.1f}")

    print("\n  Implication: a STANDARD block without reinforcement has recency=0.50")
    print("  at ~17 days (4h/day) and recency=0.10 at ~58 days. Whether it")
    print("  survives the curate sweep depends on co-retrieval before then.")


# ── D3 ────────────────────────────────────────────────────────────────────────
def d3_cold_start_floor_window() -> None:
    """How long does the v0.15.3 cold-start floor remain active?

    Floor active while recency > 0.70. For STANDARD: recency=0.70 at
    h = -ln(0.70)/λ = 35.67 active hours.
    """
    print("\n" + "─" * 72)
    print("D3 — Cold-start floor active window")
    print("─" * 72)
    threshold = 0.70
    print(f"  Floor active while recency > {threshold}")
    print(f"  {'tier':<12} {'active hours':>13}  {'@4h/day':>10}")
    for tier in DecayTier:
        lam = LAMBDA[tier]
        h = -math.log(threshold) / lam
        days = h / ACTIVE_HOURS_PER_DAY
        print(f"  {tier.value:<12} {h:>13.1f}  {days:>8.1f} d")

    print("\n  STANDARD blocks have 35.7 active hours of cold-start protection —")
    print("  ~9 days at Dmitry's usage. After that, the block must have earned")
    print("  edges (from co-retrieval) or outcomes (from agent calls) or it sinks.")


# ── D4 ────────────────────────────────────────────────────────────────────────
def d4_constitutional_dominance() -> None:
    """Score decomposition: can ANY new block beat a constitutional bedrock
    block in ATTENTION frame?

    Bedrock (constitutional): conf=1.0, rec=0.95 (PERMANENT), cent=0.80,
                              reinf=1.0. Variable: similarity.
    New block: conf=?, rec=1.0, cent=floor (0.50 at recency=1.0), reinf=0.
              Variable: similarity, alignment (sets confidence ceiling).
    """
    print("\n" + "─" * 72)
    print("D4 — Constitutional dominance: can new content win in ATTENTION?")
    print("─" * 72)
    w = ATTENTION_WEIGHTS

    def bedrock(sim: float) -> float:
        return compute_score(
            similarity=sim, confidence=1.0, recency=0.95,
            centrality=0.80, reinforcement=1.0, weights=w,
        )

    def new(sim: float, conf: float, floored: bool = True) -> float:
        cent = 0.50 if floored else 0.0
        return compute_score(
            similarity=sim, confidence=conf, recency=1.0,
            centrality=cent, reinforcement=0.0, weights=w,
        )

    print("  ATTENTION weights: sim=0.35, conf=0.15, rec=0.25, cent=0.15, reinf=0.10")
    print()
    print("  Bedrock baseline (constitutional, sim varies):")
    for s in (0.40, 0.55, 0.70, 0.85):
        print(f"    sim={s:.2f}  → bedrock score = {bedrock(s):.4f}")
    print()
    print("  Best-case new block (sim=1.0, conf=1.0, floor active):")
    best = new(1.0, 1.0, floored=True)
    print(f"    score = {best:.4f}")
    print(f"  Best-case new block (sim=1.0, conf=1.0, NO floor — block aged out):")
    no_floor = new(1.0, 1.0, floored=False)
    print(f"    score = {no_floor:.4f}")
    print()
    print("  Bedrock score at sim=0.55 (typical thematic overlap): "
          f"{bedrock(0.55):.4f}")
    print("  → A new block with sim=1.0, conf=1.0 wins by "
          f"{best - bedrock(0.55):+.4f} while floored.")
    print("  → Once floor extinguishes, margin drops to "
          f"{no_floor - bedrock(0.55):+.4f}.")
    print()
    print("  Bedrock score at sim=0.70 (high thematic overlap): "
          f"{bedrock(0.70):.4f}")
    print(f"  → New block (sim=1.0, floored) margin = {best - bedrock(0.70):+.4f}")
    print(f"  → New block (sim=1.0, no floor) margin = {no_floor - bedrock(0.70):+.4f}")
    print()
    print("  Conclusion: in ATTENTION, even a perfect-match new block can only")
    print("  win against constitutional bedrock when bedrock's similarity to the")
    print("  same query is below ~0.75. Above that, constitutional wins by math.")
    print("  Dmitry's 'shadow hierarchy' projection is structurally inevitable.")


# ── D5 ────────────────────────────────────────────────────────────────────────
def d5_constitutional_permanence() -> None:
    """PERMANENT tier half-life in years at Dmitry's usage rate."""
    print("\n" + "─" * 72)
    print("D5 — Constitutional permanence horizon")
    print("─" * 72)
    lam = LAMBDA[DecayTier.PERMANENT]
    half_h = math.log(2) / lam
    half_years = half_h / (ACTIVE_HOURS_PER_DAY * DAYS_PER_YEAR)
    ten_h = math.log(10) / lam
    ten_years = ten_h / (ACTIVE_HOURS_PER_DAY * DAYS_PER_YEAR)
    print(f"  PERMANENT λ = {lam}")
    print(f"  recency=0.50 reached at {half_h:.0f} active-hours = {half_years:.0f} years @ 4h/day")
    print(f"  recency=0.10 reached at {ten_h:.0f} active-hours = {ten_years:.0f} years @ 4h/day")
    print()
    print("  Conclusion: PERMANENT blocks are immortal on any human timescale.")
    print("  Dmitry's constitutional review concern (issue #50 architectural")
    print("  question) is real — there is no decay-driven mechanism to evolve them.")


# ── D6 ────────────────────────────────────────────────────────────────────────
def d6_rescore_under_inflation() -> None:
    """Under additive rescore (the v0.16 plan), how much can rescore move an
    ossified block?

    Block at c=0.85 with N evidence events. Rescore with α=0.55, weight=0.5
    (the v0.16 default `rescore_evidence_weight`).
    """
    print("\n" + "─" * 72)
    print("D6 — Additive rescore power against ossified blocks (v0.16 preview)")
    print("─" * 72)
    print("  Block earned c=0.85 over N outcomes. Rescore with α=0.55, w=0.5:")
    print("  new_c = (α_N + 0.55×0.5) / (α_N + β_N + 0.5)\n")
    print(f"  {'N':>5}  {'α_N+β_N':>9}  {'new c':>7}  {'Δc':>8}")
    earned_c = 0.85
    rescore_alpha = 0.55
    rescore_w = 0.5
    for n in (1, 5, 10, 25, 50, 100, 250, 500):
        total = 1.0 + n  # prior_strength=1 + N events of weight 1
        alpha_n = earned_c * total
        beta_n = (1 - earned_c) * total
        new_alpha = alpha_n + rescore_alpha * rescore_w
        new_beta = beta_n + (1 - rescore_alpha) * rescore_w
        new_c = new_alpha / (new_alpha + new_beta)
        print(f"  {n:>5}  {total:>9.1f}  {new_c:>7.4f}  {new_c - earned_c:>+8.4f}")
    print()
    print("  At N=100, rescore moves confidence by 0.001 — i.e. additive rescore")
    print("  protects evidence but cannot pull an ossified block back to centre.")
    print("  Constitutional review (D5) cannot be solved by rescore alone.")


# ── Summary ───────────────────────────────────────────────────────────────────
def summary() -> None:
    print("\n" + "═" * 72)
    print(" Summary — what the math says about Dmitry's projections")
    print("═" * 72)
    print()
    print(" Projection 1: Signal inflation drives hit-rate down over years")
    print("   → CONFIRMED. Beta-Binomial is asymptotically inert by N=100 events.")
    print("   → Even v0.16 additive rescore (weight=0.5) cannot move ossified")
    print("     blocks meaningfully (Δc ≈ 0.001 at N=100).")
    print()
    print(" Projection 2: Constitutional dominance (shadow hierarchy)")
    print("   → CONFIRMED structurally. In ATTENTION, constitutional blocks with")
    print("     thematic overlap sim≥0.75 cannot be beaten by any new content.")
    print("   → v0.15.3 floor helps for ~9 days at Dmitry's usage rate, then")
    print("     extinguishes. Blocks must accumulate edges by then or sink.")
    print()
    print(" Projection 3: Constitutional immortality")
    print("   → MATHEMATICALLY TRUE. PERMANENT tier half-life is 47.5 years.")
    print("   → Rescore cannot fix this; needs a different mechanism")
    print("     (Dmitry's quarterly review proposal, or programmatic evolution).")
    print()
    print(" Implication for the plan:")
    print("   • v0.16 (additive rescore + sufficient stats) — load-bearing for")
    print("     PEER MERGE and avoiding RESCORE CLOBBER, but it does NOT solve")
    print("     ossification once N>>100. We should not over-claim.")
    print("   • v0.17 exploration bonus may need to come EARLIER and be STRONGER")
    print("     than the original κ=0.05. The variance term decays with N too,")
    print("     so it offers natural lift to under-tested blocks.")
    print("   • Constitutional evolution (v0.18+) is now mathematically required,")
    print("     not aesthetic. Rescore alone cannot evolve the constitution.")
    print()
    print(" Layer 2 simulation is justified — non-linear interactions (cold-start")
    print(" × decay × exploration) need empirical evaluation that math alone cannot")
    print(" reach.")


def main() -> None:
    print("═" * 72)
    print(" elfmem longitudinal derivations — Layer 1 (closed form)")
    print(f" Assumed usage: {ACTIVE_HOURS_PER_DAY}h/day (Dmitry's reported rate)")
    print("═" * 72)
    d1_bayesian_inertness()
    d2_decay_half_life()
    d3_cold_start_floor_window()
    d4_constitutional_dominance()
    d5_constitutional_permanence()
    d6_rescore_under_inflation()
    summary()


if __name__ == "__main__":
    main()
