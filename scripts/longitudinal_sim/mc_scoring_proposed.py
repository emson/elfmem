"""Monte Carlo evaluation of the proposed v0.16 + v0.17 scoring mechanisms.

Compares current elfmem scoring against the proposal in
docs/plans/plan_memory_scoring.md — across multiple scenarios for REGULAR
(non-constitutional) blocks.

The four mechanisms being tested:
  - Additive rescore (v0.16): rescore folds as weighted evidence (w=0.5),
    instead of overwriting confidence with alignment_score
  - Exploration bonus (v0.17): kappa × sqrt(Beta-variance) ranking lift
    for uncertain (low α+β) blocks
  - Power-law decay (v0.17 opt-in): (1+0.5t/stability)^(-0.5) replacing
    exp(-λt). Slower late decay; fatter tails.
  - Sufficient statistics (v0.16): bookkeeping change; equivalent math
    for normal outcomes. Tested via rescore behaviour.

Strategies:
  current          — destructive rescore, no exploration, exponential decay
  v016_additive    — additive rescore only
  v017_exploration — additive rescore + exploration bonus
  v017_full        — additive rescore + exploration + power-law decay

Scenarios (workloads for regular blocks):
  baseline         — standard workload, no rescore
  weekly_rescore   — dream(rescore=True) every 7 days (tests v0.16 clobber)
  long_horizon     — 730 days (tests v0.17 decay differences)
  uncertain_mix    — half corpus is low-N at start (tests exploration bonus)

Run:  uv run python -m scripts.longitudinal_sim.mc_scoring_proposed
"""
from __future__ import annotations

import math
import random
import statistics
from collections import deque
from dataclasses import dataclass, field

import numpy as np

DIM = 8
HOURS_PER_DAY = 4.0
N_SEEDS = 2

LAMBDA_BASE = 0.010  # STANDARD tier
W_SIM, W_CONF, W_REC, W_CENT, W_REINF = 0.35, 0.15, 0.25, 0.15, 0.10

# v0.17 parameters
KAPPA = 0.05
POWER_LAW_STABILITY_HOURS = 100.0  # = 1 / LAMBDA_BASE
RESCORE_WEIGHT = 0.5


def random_topic(rng):
    v = np.array([rng.gauss(0, 1) for _ in range(DIM)])
    n = float(np.linalg.norm(v))
    return v / n if n > 0 else v


def perturb_topic(t, rng, sigma):
    noise = np.array([rng.gauss(0, sigma) for _ in range(DIM)])
    out = t + noise
    n = float(np.linalg.norm(out))
    return out / n if n > 0 else out


@dataclass
class Strategy:
    name: str
    additive_rescore: bool = False
    exploration_bonus: bool = False
    power_law_decay: bool = False


STRATEGIES = {
    "current":          Strategy("current"),
    "v016_additive":    Strategy("v016_additive", additive_rescore=True),
    "v017_exploration": Strategy("v017_exploration", additive_rescore=True,
                                  exploration_bonus=True),
    "v017_full":        Strategy("v017_full", additive_rescore=True,
                                  exploration_bonus=True, power_law_decay=True),
}


@dataclass
class Scenario:
    name: str
    days: int = 365
    rescore_every_days: int = 0  # 0 = never
    uncertain_seed_fraction: float = 0.0  # fraction of seed corpus that's low-N
    drift_sigma: float = 0.015


SCENARIOS = {
    "baseline":       Scenario("baseline", days=365),
    "weekly_rescore": Scenario("weekly_rescore", days=365, rescore_every_days=7),
    "long_horizon":   Scenario("long_horizon", days=730),
    "uncertain_mix":  Scenario("uncertain_mix", days=365, uncertain_seed_fraction=0.5),
}


# ── Scoring ───────────────────────────────────────────────────────────────────
def compute_recency_exp(elapsed_hours, lambda_decay=LAMBDA_BASE):
    return math.exp(-lambda_decay * elapsed_hours)


def compute_recency_powerlaw(elapsed_hours, stability=POWER_LAW_STABILITY_HOURS):
    return (1.0 + 0.5 * elapsed_hours / stability) ** -0.5


def score_batch(topics, alpha, beta, last_r, edges, reinf, q, t, strategy: Strategy):
    sim = (topics @ q + 1) / 2
    conf = alpha / (alpha + beta)
    elapsed = t - last_r
    if strategy.power_law_decay:
        rec = (1.0 + 0.5 * elapsed / POWER_LAW_STABILITY_HOURS) ** -0.5
    else:
        rec = np.exp(-LAMBDA_BASE * elapsed)
    raw_cent = np.minimum(1.0, edges / 10.0)
    reinf_n = np.minimum(1.0, np.log1p(reinf) / np.log(101))

    base = (W_SIM * sim + W_CONF * conf + W_REC * rec
            + W_CENT * raw_cent + W_REINF * reinf_n)
    if strategy.exploration_bonus:
        # Beta(α,β) variance = αβ / [(α+β)²(α+β+1)]
        total = alpha + beta
        variance = (alpha * beta) / (total * total * (total + 1))
        exploration = KAPPA * np.sqrt(variance)
        base = base + exploration
    return base


def apply_rescore(alpha, beta, new_alignment, strategy: Strategy):
    """Returns new (alpha, beta) after rescore.

    current:     confidence overwrites — equivalent to (alignment, 1-alignment)
                 with the same total mass, losing history
    additive:    fold as weighted evidence event with weight=0.5
    """
    if strategy.additive_rescore:
        new_alpha = alpha + new_alignment * RESCORE_WEIGHT
        new_beta = beta + (1 - new_alignment) * RESCORE_WEIGHT
    else:
        # Current behaviour: overwrite confidence. Preserve total mass for
        # comparability, but the ratio is reset.
        total = alpha + beta
        new_alpha = new_alignment * total
        new_beta = (1 - new_alignment) * total
    return new_alpha, new_beta


# ── Simulation ────────────────────────────────────────────────────────────────
def simulate(strategy: Strategy, scenario: Scenario, seed: int) -> dict:
    rng = random.Random(seed)
    np.random.seed(seed)

    self_topic = random_topic(rng)

    topics_l, alpha_l, beta_l, last_r_l = [], [], [], []
    edges_l, reinf_l, birth_l = [], [], []

    def add(topic, birth_h, t, success, failure, reinf, edges):
        topics_l.append(topic)
        alpha_l.append(success)
        beta_l.append(failure)
        last_r_l.append(t)
        edges_l.append(edges)
        reinf_l.append(reinf)
        birth_l.append(birth_h)

    # Seed initial corpus (200 blocks)
    n_seed = 200
    for i in range(n_seed):
        topic = perturb_topic(self_topic, rng, sigma=0.4)
        # Some blocks are well-established (high N), some are uncertain (low N)
        is_uncertain = i < (n_seed * scenario.uncertain_seed_fraction)
        if is_uncertain:
            add(topic, 0.0, 0.0, success=0.5, failure=0.5, reinf=0, edges=0)
        else:
            # Established block: N=10 outcomes already
            add(topic, 0.0, 0.0, success=7.0, failure=3.0, reinf=10, edges=3)

    metrics = []
    rescore_damage_samples = []  # per-rescore-event: (block_id, pre_conf, post_conf)

    for day in range(scenario.days):
        t = (day + 1) * HOURS_PER_DAY
        self_topic = perturb_topic(self_topic, rng, sigma=scenario.drift_sigma)

        # Learn 5/day
        for _ in range(5):
            new_t = perturb_topic(self_topic, rng, sigma=0.3 + 0.4 * rng.random())
            add(new_t, t, t, success=0.5, failure=0.5, reinf=0, edges=0)

        # ATTENTION queries
        topics_arr = np.array(topics_l)
        alpha_arr = np.array(alpha_l)
        beta_arr = np.array(beta_l)
        last_r_arr = np.array(last_r_l)
        edges_arr = np.array(edges_l)
        reinf_arr = np.array(reinf_l)

        for _ in range(20):
            q = perturb_topic(self_topic, rng, sigma=0.2)
            scores = score_batch(topics_arr, alpha_arr, beta_arr,
                                  last_r_arr, edges_arr, reinf_arr, q, t, strategy)
            top_idx = np.argsort(-scores)[:5]
            top1_idx = int(top_idx[0])
            true_align = (float(topics_arr[top1_idx] @ self_topic) + 1) / 2
            if rng.random() < 0.40:
                signal = 1.0 if rng.random() < true_align else 0.0
                alpha_l[top1_idx] += signal
                beta_l[top1_idx] += (1 - signal)
                reinf_l[top1_idx] += 1
                last_r_l[top1_idx] = t
                for ti in top_idx[:3]:
                    edges_l[int(ti)] += 1
            alpha_arr = np.array(alpha_l)
            beta_arr = np.array(beta_l)
            last_r_arr = np.array(last_r_l)
            edges_arr = np.array(edges_l)
            reinf_arr = np.array(reinf_l)

        # Periodic rescore
        if scenario.rescore_every_days > 0 and day > 0 and day % scenario.rescore_every_days == 0:
            # Rescore a random sample of 20 active blocks
            n_blocks = len(topics_l)
            sample_idx = rng.sample(range(n_blocks), min(20, n_blocks))
            for idx in sample_idx:
                # Generate new alignment score — drawn from current alignment
                # to self_topic plus noise (simulates LLM re-evaluation)
                true_curr_align = (float(topics_arr[idx] @ self_topic) + 1) / 2
                new_align = max(0.0, min(1.0, true_curr_align + rng.gauss(0, 0.15)))
                # Record damage on well-established blocks
                pre_conf = alpha_l[idx] / (alpha_l[idx] + beta_l[idx])
                pre_total = alpha_l[idx] + beta_l[idx]
                new_a, new_b = apply_rescore(alpha_l[idx], beta_l[idx], new_align, strategy)
                post_conf = new_a / (new_a + new_b)
                if pre_total > 5.0:  # only count well-established blocks
                    rescore_damage_samples.append((idx, pre_conf, post_conf, pre_total))
                alpha_l[idx] = new_a
                beta_l[idx] = new_b

        # Vitals every 28 days
        if day > 0 and day % 28 == 0:
            metrics.append(measure(
                topics_arr, alpha_arr, beta_arr, last_r_arr, edges_arr, reinf_arr,
                np.array(birth_l), self_topic, t, strategy, day,
            ))

    return {
        "strategy": strategy.name,
        "scenario": scenario.name,
        "seed": seed,
        "weekly": metrics,
        "rescore_damage_samples": rescore_damage_samples,
        "n_blocks": len(topics_l),
        "final_alpha_arr": np.array(alpha_l),
        "final_beta_arr": np.array(beta_l),
    }


def measure(topics, alpha, beta, last_r, edges, reinf, birth_h,
            self_topic, t, strategy: Strategy, day):
    n_q = 25
    rng_v = random.Random(99 + day)
    q_sum = o_sum = recent_acc = 0.0
    top_block_ids_set = set()  # for entropy

    for _ in range(n_q):
        q = perturb_topic(self_topic, rng_v, sigma=0.2)
        scores = score_batch(topics, alpha, beta, last_r, edges, reinf, q, t, strategy)
        top_idx = np.argsort(-scores)[:5]
        q_sum += float(np.mean(topics[top_idx] @ q))
        o_sum += float(np.mean(topics[np.argsort(-(topics @ q))[:5]] @ q))
        recent_acc += float(np.mean((t - birth_h[top_idx]) / HOURS_PER_DAY < 30))
        for bi in top_idx:
            top_block_ids_set.add(int(bi))

    # Confidence distribution entropy (high = diverse, low = ossified)
    conf = alpha / (alpha + beta)
    # Histogram of confidence in 10 bins
    hist, _ = np.histogram(conf, bins=10, range=(0, 1))
    probs = hist / hist.sum() if hist.sum() > 0 else np.zeros(10)
    nonzero = probs[probs > 0]
    conf_entropy = -float(np.sum(nonzero * np.log(nonzero))) if len(nonzero) else 0.0

    # N+α+β distribution (corpus maturity)
    total_evidence = alpha + beta
    mean_evidence = float(np.mean(total_evidence))
    p95_evidence = float(np.percentile(total_evidence, 95))

    # Diversity: how many UNIQUE blocks appeared in top-5 across the 25 queries
    n_unique_top = len(top_block_ids_set)
    diversity = n_unique_top / (5 * n_q)  # max 1.0 if every slot was unique

    return {
        "day": day,
        "quality_ratio": q_sum / max(0.001, o_sum),
        "recent_reach": recent_acc / n_q,
        "conf_entropy": conf_entropy,
        "mean_evidence": mean_evidence,
        "p95_evidence": p95_evidence,
        "top_k_diversity": diversity,
        "n_blocks": len(topics),
    }


# ── Runner ────────────────────────────────────────────────────────────────────
def run_all():
    results = {}
    for sc_name, sc in SCENARIOS.items():
        results[sc_name] = {}
        for st_name, st in STRATEGIES.items():
            print(f"  {sc_name}/{st_name} ...", end="", flush=True)
            runs = [simulate(st, sc, seed) for seed in range(N_SEEDS)]
            final = [r["weekly"][-1] for r in runs if r["weekly"]]
            qr = statistics.mean(f["quality_ratio"] for f in final)
            ent = statistics.mean(f["conf_entropy"] for f in final)
            div = statistics.mean(f["top_k_diversity"] for f in final)
            damage_samples = [s for r in runs for s in r["rescore_damage_samples"]]
            if damage_samples:
                damages = [abs(pre - post) for (_, pre, post, _) in damage_samples]
                mean_damage = statistics.mean(damages)
            else:
                mean_damage = 0.0
            print(f"  qr={qr:.1%}  entropy={ent:.2f}  div={div:.1%}  damage={mean_damage:.3f}")
            results[sc_name][st_name] = runs
    return results


def summarise(results):
    print("\n" + "═" * 80)
    print(" Quality ratio (cosine of top-5 / oracle top-5)")
    print("═" * 80)
    print(f"  {'scenario':<18}", end="")
    for st in STRATEGIES:
        print(f"  {st:>17}", end="")
    print()
    for sc_name in SCENARIOS:
        print(f"  {sc_name:<18}", end="")
        for st_name in STRATEGIES:
            runs = results[sc_name][st_name]
            final = [r["weekly"][-1] for r in runs if r["weekly"]]
            qr = statistics.mean(f["quality_ratio"] for f in final)
            print(f"  {qr:>16.1%}", end="")
        print()

    print("\n" + "═" * 80)
    print(" Recent reach (% top-5 from last 30 days — higher = more plastic)")
    print("═" * 80)
    print(f"  {'scenario':<18}", end="")
    for st in STRATEGIES:
        print(f"  {st:>17}", end="")
    print()
    for sc_name in SCENARIOS:
        print(f"  {sc_name:<18}", end="")
        for st_name in STRATEGIES:
            runs = results[sc_name][st_name]
            final = [r["weekly"][-1] for r in runs if r["weekly"]]
            rr = statistics.mean(f["recent_reach"] for f in final)
            print(f"  {rr:>16.1%}", end="")
        print()

    print("\n" + "═" * 80)
    print(" Confidence entropy (corpus diversity — higher = healthier)")
    print("═" * 80)
    print(f"  {'scenario':<18}", end="")
    for st in STRATEGIES:
        print(f"  {st:>17}", end="")
    print()
    for sc_name in SCENARIOS:
        print(f"  {sc_name:<18}", end="")
        for st_name in STRATEGIES:
            runs = results[sc_name][st_name]
            final = [r["weekly"][-1] for r in runs if r["weekly"]]
            ent = statistics.mean(f["conf_entropy"] for f in final)
            print(f"  {ent:>16.2f}", end="")
        print()

    print("\n" + "═" * 80)
    print(" Top-K diversity (unique blocks in top-5 / total slots — higher = exploration)")
    print("═" * 80)
    print(f"  {'scenario':<18}", end="")
    for st in STRATEGIES:
        print(f"  {st:>17}", end="")
    print()
    for sc_name in SCENARIOS:
        print(f"  {sc_name:<18}", end="")
        for st_name in STRATEGIES:
            runs = results[sc_name][st_name]
            final = [r["weekly"][-1] for r in runs if r["weekly"]]
            div = statistics.mean(f["top_k_diversity"] for f in final)
            print(f"  {div:>16.1%}", end="")
        print()

    print("\n" + "═" * 80)
    print(" Mean rescore damage (|Δconfidence| on blocks with α+β > 5)")
    print(" Lower = additive rescore preserves earned evidence")
    print("═" * 80)
    print(f"  {'scenario':<18}", end="")
    for st in STRATEGIES:
        print(f"  {st:>17}", end="")
    print()
    for sc_name in SCENARIOS:
        print(f"  {sc_name:<18}", end="")
        for st_name in STRATEGIES:
            runs = results[sc_name][st_name]
            damage_samples = [s for r in runs for s in r["rescore_damage_samples"]]
            if damage_samples:
                damages = [abs(pre - post) for (_, pre, post, _) in damage_samples]
                md = statistics.mean(damages)
                print(f"  {md:>16.3f}", end="")
            else:
                print(f"  {'n/a':>17}", end="")
        print()

    # Quality trajectory at d56, d168, d364
    print("\n" + "═" * 80)
    print(" Quality trajectory under weekly_rescore (test of additive rescore)")
    print("═" * 80)
    print(f"  {'strategy':<20} {'d56':>8} {'d168':>8} {'d364':>8}")
    for st_name in STRATEGIES:
        runs = results["weekly_rescore"][st_name]
        traj = {}
        for target in (56, 168, 364):
            vals = []
            for r in runs:
                for week in r["weekly"]:
                    if week["day"] >= target:
                        vals.append(week["quality_ratio"])
                        break
            traj[target] = statistics.mean(vals) if vals else 0.0
        print(f"  {st_name:<20} {traj[56]:>7.1%} {traj[168]:>7.1%} {traj[364]:>7.1%}")

    print("\n" + "═" * 80)
    print(" Quality trajectory under long_horizon (test of power-law decay)")
    print("═" * 80)
    print(f"  {'strategy':<20} {'d56':>8} {'d168':>8} {'d364':>8} {'d728':>8}")
    for st_name in STRATEGIES:
        runs = results["long_horizon"][st_name]
        traj = {}
        for target in (56, 168, 364, 728):
            vals = []
            for r in runs:
                for week in r["weekly"]:
                    if week["day"] >= target:
                        vals.append(week["quality_ratio"])
                        break
            traj[target] = statistics.mean(vals) if vals else 0.0
        print(f"  {st_name:<20} {traj[56]:>7.1%} {traj[168]:>7.1%} {traj[364]:>7.1%} {traj[728]:>7.1%}")


def main():
    print("═" * 80)
    print(" Proposed scoring (v0.16 + v0.17) evaluation")
    print(f" Strategies: {list(STRATEGIES.keys())}")
    print(f" Scenarios:  {list(SCENARIOS.keys())}")
    print(f" {N_SEEDS} seeds each")
    print("═" * 80)
    print()
    results = run_all()
    summarise(results)


if __name__ == "__main__":
    main()
