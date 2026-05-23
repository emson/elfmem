"""Verify v0.15.3 cold-start centrality floor against the REAL scoring path.

Re-runs the cold-start scenarios from `/tmp/confidence_sim.py` using the actual
shipped formulas (`compute_score`, `effective_centrality`, `ATTENTION_WEIGHTS`)
rather than the simulation's local copies — which used different weights.

Sim used ATTENTION = (0.60, 0.15, 0.10, 0.15, 0.00).
Real ATTENTION_WEIGHTS = (0.35, 0.15, 0.25, 0.15, 0.10).

The contribution of each channel to the bedrock-vs-new gap differs by up to 3×
between the two. This script tells us what the floor actually accomplishes in
production scoring.

Run:  uv run python scripts/verify_v0_15_3_scoring.py
"""
from __future__ import annotations

from elfmem.scoring import ATTENTION_WEIGHTS, compute_score, effective_centrality


def gap(label: str, new_kwargs: dict, bedrock_kwargs: dict) -> None:
    raw_new_centrality = new_kwargs["centrality"]
    recency = new_kwargs["recency"]
    floored = effective_centrality(raw_centrality=raw_new_centrality, recency=recency)

    pre = compute_score(**new_kwargs, weights=ATTENTION_WEIGHTS)
    post = compute_score(
        **{**new_kwargs, "centrality": floored}, weights=ATTENTION_WEIGHTS,
    )
    bedrock = compute_score(**bedrock_kwargs, weights=ATTENTION_WEIGHTS)

    pre_gap = pre - bedrock
    post_gap = post - bedrock
    closed = post_gap - pre_gap
    verdict = "✓ new wins" if post_gap > 0 else "✗ bedrock still wins"

    print(f"\n── {label} ──")
    print(f"  raw centrality:   {raw_new_centrality:.2f}")
    print(f"  floored centrality: {floored:.3f}")
    print(f"  bedrock score:    {bedrock:.4f}")
    print(f"  new (pre-floor):  {pre:.4f}   gap = {pre_gap:+.4f}")
    print(f"  new (post-floor): {post:.4f}   gap = {post_gap:+.4f}")
    print(f"  closed by floor:  {closed:+.4f}  →  {verdict}")


def channel_decomposition(new_kwargs: dict, bedrock_kwargs: dict) -> None:
    """Per-channel contribution to (new − bedrock), at real ATTENTION weights."""
    w = ATTENTION_WEIGHTS
    print("\n── Channel decomposition (new − bedrock, weighted) ──")
    for ch in ("similarity", "confidence", "recency", "centrality", "reinforcement"):
        contrib = getattr(w, ch) * (new_kwargs[ch] - bedrock_kwargs[ch])
        print(f"  {ch:14s} weight={getattr(w, ch):.2f}  Δraw={new_kwargs[ch]-bedrock_kwargs[ch]:+.3f}  contrib={contrib:+.4f}")


def main() -> None:
    print("=" * 72)
    print("v0.15.3 cold-start floor — verification against REAL scoring path")
    print("=" * 72)
    print(f"ATTENTION_WEIGHTS = {ATTENTION_WEIGHTS.model_dump()}")

    # S1 — wide-margin (sim's scenario_cold_start)
    new_s1 = dict(similarity=0.92, confidence=0.65, recency=1.0,
                  centrality=0.0, reinforcement=0.0)
    bedrock_wide = dict(similarity=0.55, confidence=0.95, recency=0.70,
                        centrality=0.80, reinforcement=1.0)
    gap("S1 — wide similarity gap (0.92 vs 0.55)", new_s1, bedrock_wide)
    channel_decomposition(new_s1, bedrock_wide)

    # S1b — tight-margin (sim's scenario_cold_start_tight, the realistic one)
    new_s1b = dict(similarity=0.74, confidence=0.65, recency=1.0,
                   centrality=0.0, reinforcement=0.0)
    bedrock_tight = dict(similarity=0.62, confidence=0.95, recency=0.70,
                         centrality=0.80, reinforcement=1.0)
    gap("S1b — tight similarity gap (0.74 vs 0.62)", new_s1b, bedrock_tight)
    channel_decomposition(new_s1b, bedrock_tight)

    # S1b without bedrock reinforcement — the integration test's setup
    bedrock_no_reinf = dict(similarity=0.62, confidence=0.95, recency=0.70,
                            centrality=0.80, reinforcement=0.0)
    gap("S1b' — tight gap, bedrock has NO reinforcement", new_s1b, bedrock_no_reinf)
    channel_decomposition(new_s1b, bedrock_no_reinf)

    # Recency-only ablation: how does the floor behave as the new block ages?
    print("\n── Floor decay as fresh block ages ──")
    print(f"  bedrock (tight, no-reinf):  {compute_score(**bedrock_no_reinf, weights=ATTENTION_WEIGHTS):.4f}")
    for rec in (1.0, 0.85, 0.71, 0.70, 0.50, 0.10):
        floored = effective_centrality(raw_centrality=0.0, recency=rec)
        new = compute_score(
            similarity=0.74, confidence=0.65, recency=rec,
            centrality=floored, reinforcement=0.0,
            weights=ATTENTION_WEIGHTS,
        )
        bedrock = compute_score(**bedrock_no_reinf, weights=ATTENTION_WEIGHTS)
        print(f"  recency={rec:.2f}  floored_cent={floored:.3f}  "
              f"new_score={new:.4f}  margin={new-bedrock:+.4f}")

    print("\n" + "=" * 72)
    print("Interpretation:")
    print(" • Real ATTENTION puts 0.25 weight on recency vs sim's 0.10 — recency")
    print("   already helps fresh blocks more in production than the sim suggested.")
    print(" • Real ATTENTION puts 0.10 weight on reinforcement vs sim's 0.00 —")
    print("   bedrock with reinforcement=1.0 has a 0.10 ranking moat the sim ignored.")
    print(" • The floor closes 0.075 of weighted gap (0.15 × 0.50).")
    print(" • Whether that's enough depends on the bedrock blocks' reinforcement.")
    print("=" * 72)


if __name__ == "__main__":
    main()
