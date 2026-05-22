"""Full-scenario evaluation of elfmem evolution strategies.

Tests three top candidates (baseline, M, Model C) across five distinct
workload scenarios over 365 simulated days. Looks for both intended dynamics
(Model C selects useful constitutional) and unintended consequences
(ego runaway, constitutional death, adversarial corruption, quiet-period
decay, bootstrap failure).

Scenarios:
  stable        — minimal drift, no regime change (σ=0.005/day)
  slow_drift    — gradual identity shift (σ=0.020/day)
  regime_change — sudden 60° rotation at day 120
  quiet_burst   — normal → 60 days silence → normal
  adversarial   — moderate drift + 15% outcome noise

Strategies:
  C_baseline           — current immortal PERMANENT
  C_self_context_M     — structural exclusion (M alone)
  ModelC_M_plus_self   — M + dedicated SELF-frame feedback

Each combination: 2 random seeds, ~30s per run. Total ~15 min.

Adds new diagnostic vitals to detect unintended consequences:
  ego_str_max       — max ego_strength (detects runaway hoarding)
  ego_str_min       — min ego_strength (detects constitutional death)
  ego_concentration — max / mean (detects single-block dominance)
  const_alive       — count with ego_strength > 0
  retrieval_top_share — % of SELF queries to single most-retrieved block

Run:  uv run python -m scripts.longitudinal_sim.mc_scenarios
"""
from __future__ import annotations

import math
import random
import statistics
from collections import Counter, deque
from dataclasses import dataclass, field

import numpy as np

DIM = 8
HOURS_PER_DAY = 4.0
DAYS = 365
N_SEEDS = 2

LAMBDA_BASE = {"PERMANENT": 0.00001, "DURABLE": 0.001, "STANDARD": 0.010}
W_SIM, W_CONF, W_REC, W_CENT, W_REINF = 0.35, 0.15, 0.25, 0.15, 0.10
COLD_START_RECENCY_THRESHOLD = 0.70
COLD_START_CENT_THRESHOLD = 0.10
COLD_START_FLOOR_STRENGTH = 0.50

EGO_POS_RATE = 1.0
EGO_NEG_RATE = 0.3
EGO_TIME_DECAY = 0.05
EGO_LAMBDA_ALPHA = 0.05


# ── Topic helpers ─────────────────────────────────────────────────────────────
def random_topic(rng):
    v = np.array([rng.gauss(0, 1) for _ in range(DIM)])
    n = float(np.linalg.norm(v))
    return v / n if n > 0 else v


def perturb_topic(t, rng, sigma):
    noise = np.array([rng.gauss(0, sigma) for _ in range(DIM)])
    out = t + noise
    n = float(np.linalg.norm(out))
    return out / n if n > 0 else out


# ── Scenarios ─────────────────────────────────────────────────────────────────
@dataclass
class Scenario:
    name: str
    drift_sigma: float = 0.020       # per day random walk
    regime_days: tuple = ()           # days of 60° rotation
    quiet_days: tuple = (None, None)  # (start, end) inclusive — no ops
    outcome_noise: float = 0.0        # probability of flipped outcome signal


SCENARIOS = {
    "stable":        Scenario("stable", drift_sigma=0.005),
    "slow_drift":    Scenario("slow_drift", drift_sigma=0.020),
    "regime_change": Scenario("regime_change", drift_sigma=0.020, regime_days=(120,)),
    "quiet_burst":   Scenario("quiet_burst", drift_sigma=0.015, quiet_days=(90, 150)),
    "adversarial":   Scenario("adversarial", drift_sigma=0.020, outcome_noise=0.15),
}


# ── Strategy ──────────────────────────────────────────────────────────────────
@dataclass
class Strategy:
    name: str
    self_context: bool = False
    ego_continuous: bool = False
    self_frame_reinforcement: bool = False
    self_query_rate: int = 0
    no_perm_seed: bool = False


@dataclass
class StrategyExt(Strategy):
    # ModelD: distribute SELF-query feedback across top-N constitutional
    # weighted by relative alignment, instead of winner-take-all top-1
    self_query_distribute_top_n: int = 1


STRATEGIES = {
    "C_baseline":         Strategy("C_baseline", no_perm_seed=False),
    "C_self_context_M":   Strategy("C_self_context_M", self_context=True, no_perm_seed=False),
    "ModelC_M_plus_self": StrategyExt("ModelC_M_plus_self", self_context=True,
                                       ego_continuous=True, no_perm_seed=True,
                                       self_frame_reinforcement=True, self_query_rate=2,
                                       self_query_distribute_top_n=1),
    "ModelD_distributed": StrategyExt("ModelD_distributed", self_context=True,
                                       ego_continuous=True, no_perm_seed=True,
                                       self_frame_reinforcement=True, self_query_rate=2,
                                       self_query_distribute_top_n=3),
}


# ── Scoring ───────────────────────────────────────────────────────────────────
def score_batch(topics, alpha, beta, lam, last_r, edges, reinf, q, t):
    sim = (topics @ q + 1) / 2
    conf = alpha / (alpha + beta)
    rec = np.exp(-lam * (t - last_r))
    raw_cent = np.minimum(1.0, edges / 10.0)
    floor = np.where(
        (raw_cent < COLD_START_CENT_THRESHOLD) & (rec > COLD_START_RECENCY_THRESHOLD),
        np.maximum(raw_cent, COLD_START_FLOOR_STRENGTH * rec),
        raw_cent,
    )
    reinf_n = np.minimum(1.0, np.log1p(reinf) / np.log(101))
    return W_SIM * sim + W_CONF * conf + W_REC * rec + W_CENT * floor + W_REINF * reinf_n


# ── Simulation ────────────────────────────────────────────────────────────────
def simulate(strategy: Strategy, scenario: Scenario, seed: int) -> dict:
    rng = random.Random(seed)
    np.random.seed(seed)
    self_topic = random_topic(rng)

    topics_l, alpha_l, beta_l, lam_l, last_r_l = [], [], [], [], []
    edges_l, reinf_l, is_ego_l, birth_l = [], [], [], []
    ego_strength_l, last_eval_h_l = [], []
    tier_l = []
    self_query_retrieval_counter = Counter()  # block_id → times retrieved in SELF query

    def add(topic, birth_h, t, *, success=0.5, failure=0.5, reinf=0,
            tier="STANDARD", is_ego=False, edges=0, ego_strength=0.0):
        topics_l.append(topic)
        alpha_l.append(success)
        beta_l.append(failure)
        if strategy.ego_continuous and is_ego:
            lam = LAMBDA_BASE["STANDARD"] / (1 + EGO_LAMBDA_ALPHA * max(0, ego_strength))
        else:
            lam = LAMBDA_BASE[tier]
        lam_l.append(lam)
        last_r_l.append(t)
        edges_l.append(edges)
        reinf_l.append(reinf)
        is_ego_l.append(is_ego)
        birth_l.append(birth_h)
        ego_strength_l.append(ego_strength)
        last_eval_h_l.append(t)
        tier_l.append(tier)

    # Seed 10 constitutional
    seed_tier = "STANDARD" if strategy.no_perm_seed else "PERMANENT"
    seed_strength = 20.0 if strategy.no_perm_seed else 0.0
    seed_success = 1.0 if strategy.no_perm_seed else 2.0
    seed_reinf = 2 if strategy.no_perm_seed else 5
    seed_edges = 2 if strategy.no_perm_seed else 5
    for _ in range(10):
        topic = perturb_topic(self_topic, rng, sigma=0.15)
        add(topic, 0.0, 0.0, success=seed_success, failure=0.5,
            reinf=seed_reinf, tier=seed_tier, is_ego=True,
            edges=seed_edges, ego_strength=seed_strength)

    def update_ego(idx, delta, t):
        ego_strength_l[idx] = max(0.0, ego_strength_l[idx] + delta)
        if strategy.ego_continuous and is_ego_l[idx]:
            lam_l[idx] = LAMBDA_BASE["STANDARD"] / (
                1 + EGO_LAMBDA_ALPHA * ego_strength_l[idx]
            )

    metrics = []

    for day in range(DAYS):
        t = (day + 1) * HOURS_PER_DAY

        # Scenario: drift / regime change
        if day in scenario.regime_days:
            new_dir = perturb_topic(self_topic, rng, sigma=1.5)
            blended = 0.5 * self_topic + 0.866 * new_dir
            n = float(np.linalg.norm(blended))
            self_topic = blended / n if n > 0 else self_topic
        else:
            self_topic = perturb_topic(self_topic, rng, sigma=scenario.drift_sigma)

        # Scenario: quiet period — skip learn/query but still apply time-decay
        quiet_start, quiet_end = scenario.quiet_days
        in_quiet = quiet_start is not None and quiet_start <= day <= quiet_end
        if in_quiet:
            # Apply time decay for ego blocks only
            if strategy.ego_continuous:
                for i in range(len(topics_l)):
                    if is_ego_l[i]:
                        elapsed = t - last_eval_h_l[i]
                        if elapsed > 0:
                            update_ego(i, -EGO_TIME_DECAY * elapsed / HOURS_PER_DAY, t)
                            last_eval_h_l[i] = t
            # Sample vitals
            if day > 0 and day % 28 == 0:
                metrics.append(snapshot(topics_l, alpha_l, beta_l, lam_l, last_r_l,
                                          edges_l, reinf_l, is_ego_l, birth_l,
                                          ego_strength_l, self_topic, t, strategy, day))
            continue

        # Apply time decay
        if strategy.ego_continuous:
            for i in range(len(topics_l)):
                if is_ego_l[i]:
                    elapsed = t - last_eval_h_l[i]
                    if elapsed > 0:
                        update_ego(i, -EGO_TIME_DECAY * elapsed / HOURS_PER_DAY, t)
                        last_eval_h_l[i] = t

        # Learn 5 new blocks
        for _ in range(5):
            new_t = perturb_topic(self_topic, rng, sigma=0.3 + 0.4 * rng.random())
            add(new_t, t, t, tier="STANDARD", is_ego=False, ego_strength=0.0)

        # Snapshot arrays
        topics_arr = np.array(topics_l)
        alpha_arr = np.array(alpha_l)
        beta_arr = np.array(beta_l)
        lam_arr = np.array(lam_l)
        last_r_arr = np.array(last_r_l)
        edges_arr = np.array(edges_l)
        reinf_arr = np.array(reinf_l)
        is_ego_arr = np.array(is_ego_l, dtype=bool)

        # ATTENTION queries (20/day)
        for _ in range(20):
            q = perturb_topic(self_topic, rng, sigma=0.2)
            scores = score_batch(topics_arr, alpha_arr, beta_arr, lam_arr,
                                  last_r_arr, edges_arr, reinf_arr, q, t)
            if strategy.self_context:
                scores[is_ego_arr] = -np.inf
            top_idx = np.argsort(-scores)[:5]
            top1_idx = int(top_idx[0])

            true_align = (float(topics_arr[top1_idx] @ self_topic) + 1) / 2
            # Adversarial: noise in outcome signal
            if rng.random() < 0.40:
                signal = 1.0 if rng.random() < true_align else 0.0
                if scenario.outcome_noise > 0 and rng.random() < scenario.outcome_noise:
                    signal = 1.0 - signal  # flip
                alpha_l[top1_idx] += signal
                beta_l[top1_idx] += (1 - signal)
                reinf_l[top1_idx] += 1
                last_r_l[top1_idx] = t
                if is_ego_l[top1_idx] and strategy.ego_continuous:
                    delta = EGO_POS_RATE if signal > 0.5 else -EGO_NEG_RATE
                    update_ego(top1_idx, delta, t)
                for ti in top_idx[:3]:
                    edges_l[int(ti)] += 1
            # refresh
            alpha_arr = np.array(alpha_l)
            beta_arr = np.array(beta_l)
            lam_arr = np.array(lam_l)
            last_r_arr = np.array(last_r_l)
            edges_arr = np.array(edges_l)
            reinf_arr = np.array(reinf_l)

        # Model C: SELF-frame queries
        if strategy.self_frame_reinforcement and strategy.self_query_rate > 0:
            # ModelD: distribute across top-N (if attribute present), else top-1
            distribute_n = getattr(strategy, "self_query_distribute_top_n", 1)
            for _ in range(strategy.self_query_rate):
                q = self_topic
                const_idx = np.where(is_ego_arr)[0]
                if len(const_idx) == 0:
                    continue
                const_scores = (
                    0.10 * (topics_arr[const_idx] @ q + 1) / 2
                    + 0.30 * (alpha_arr[const_idx] / (alpha_arr[const_idx] + beta_arr[const_idx]))
                    + 0.05 * np.exp(-lam_arr[const_idx] * (t - last_r_arr[const_idx]))
                    + 0.25 * np.minimum(1.0, edges_arr[const_idx] / 10.0)
                    + 0.30 * np.minimum(1.0, np.log1p(reinf_arr[const_idx]) / np.log(101))
                )
                # Pick top-N by score
                top_n = min(distribute_n, len(const_idx))
                top_in_local = np.argsort(-const_scores)[:top_n]
                top_in_const_idxs = [int(const_idx[i]) for i in top_in_local]
                # Weight by softmax of their scores (distributive feedback)
                top_scores = const_scores[top_in_local]
                exp_scores = np.exp(top_scores - top_scores.max())
                weights = exp_scores / exp_scores.sum()

                for w, idx in zip(weights, top_in_const_idxs):
                    self_query_retrieval_counter[idx] += float(w)
                    true_align = (float(topics_arr[idx] @ self_topic) + 1) / 2
                    if rng.random() < 0.6:
                        signal = 1.0 if rng.random() < true_align else 0.0
                        if scenario.outcome_noise > 0 and rng.random() < scenario.outcome_noise:
                            signal = 1.0 - signal
                        # Distribute the Bayesian update by weight
                        alpha_l[idx] += signal * float(w)
                        beta_l[idx] += (1 - signal) * float(w)
                        reinf_l[idx] += float(w)
                        last_r_l[idx] = t
                        delta = (EGO_POS_RATE if signal > 0.5 else -EGO_NEG_RATE) * float(w)
                        update_ego(idx, delta, t)

        # Vitals every 28 days
        if day > 0 and day % 28 == 0:
            metrics.append(snapshot(topics_l, alpha_l, beta_l, lam_l, last_r_l,
                                      edges_l, reinf_l, is_ego_l, birth_l,
                                      ego_strength_l, self_topic, t, strategy, day,
                                      self_query_retrieval_counter))

    # Final summary
    return {
        "strategy": strategy.name,
        "scenario": scenario.name,
        "seed": seed,
        "weekly": metrics,
        "n_blocks": len(topics_l),
        "self_query_concentration": _concentration_top1(self_query_retrieval_counter),
    }


def _concentration_top1(counter):
    if not counter:
        return 0.0
    total = sum(counter.values())
    return max(counter.values()) / total if total > 0 else 0.0


def snapshot(topics_l, alpha_l, beta_l, lam_l, last_r_l, edges_l, reinf_l,
             is_ego_l, birth_l, ego_strength_l, self_topic, t, strategy, day,
             self_q_counter=None):
    topics = np.array(topics_l)
    alpha = np.array(alpha_l)
    beta = np.array(beta_l)
    lam = np.array(lam_l)
    last_r = np.array(last_r_l)
    edges = np.array(edges_l)
    reinf = np.array(reinf_l)
    is_ego = np.array(is_ego_l, dtype=bool)
    birth_h = np.array(birth_l)
    ego_str = np.array(ego_strength_l)

    rng_v = random.Random(99 + day)
    n_q = 25
    quality_sum = oracle_sum = recent_acc = ego_acc = 0.0
    for _ in range(n_q):
        q = perturb_topic(self_topic, rng_v, sigma=0.2)
        scores = score_batch(topics, alpha, beta, lam, last_r, edges, reinf, q, t)
        if strategy.self_context:
            scores[is_ego] = -np.inf
        top_idx = np.argsort(-scores)[:5]
        quality_sum += float(np.mean(topics[top_idx] @ q))
        sim_all = topics @ q
        oracle_idx = np.argsort(-sim_all)[:5]
        oracle_sum += float(np.mean(topics[oracle_idx] @ q))
        recent_acc += float(np.mean((t - birth_h[top_idx]) / HOURS_PER_DAY < 30))
        ego_acc += float(np.mean(is_ego[top_idx]))

    ego_strs_active = ego_str[is_ego]
    if len(ego_strs_active) > 0:
        es_max = float(ego_strs_active.max())
        es_min = float(ego_strs_active.min())
        es_mean = float(ego_strs_active.mean())
        es_concentration = es_max / es_mean if es_mean > 0.01 else 0.0
        const_alive = int((ego_strs_active > 0.01).sum())
    else:
        es_max = es_min = es_mean = es_concentration = 0.0
        const_alive = 0

    # Alignment of constitutional blocks to current self
    if is_ego.any():
        const_align = float(np.mean((topics[is_ego] @ self_topic + 1) / 2))
    else:
        const_align = 0.0

    return {
        "day": day,
        "quality": quality_sum / n_q,
        "quality_ratio": (quality_sum / n_q) / (oracle_sum / n_q) if oracle_sum > 0 else 0.0,
        "recent_reach": recent_acc / n_q,
        "ego_moat": ego_acc / n_q,
        "es_mean": es_mean, "es_max": es_max, "es_min": es_min,
        "es_concentration": es_concentration,
        "const_alive": const_alive,
        "const_align_to_self": const_align,
    }


# ── Runner ────────────────────────────────────────────────────────────────────
def run_matrix() -> dict:
    results = {}
    for s_name, scenario in SCENARIOS.items():
        results[s_name] = {}
        for strat_name, strat in STRATEGIES.items():
            print(f"  {s_name}/{strat_name} ...", end="", flush=True)
            runs = [simulate(strat, scenario, seed) for seed in range(N_SEEDS)]
            final = [r["weekly"][-1] for r in runs if r["weekly"]]
            qr = statistics.mean(f["quality_ratio"] for f in final)
            ca = statistics.mean(f["const_alive"] for f in final)
            ec = statistics.mean(f["es_concentration"] for f in final)
            print(f"  qratio={qr:.1%}  const_alive={ca:.0f}  es_concentration={ec:.1f}")
            results[s_name][strat_name] = runs
    return results


def summarise(results: dict) -> None:
    for s_name in results:
        print(f"\n{'═' * 78}")
        print(f" Scenario: {s_name}")
        print("═" * 78)
        print(f"  {'strategy':<22} {'qratio':>7} {'recent':>7} {'ego_moat':>9} "
              f"{'es_mean':>8} {'es_conc':>8} {'alive':>6} {'align':>6}")
        print("  " + "─" * 75)
        for strat_name, runs in results[s_name].items():
            final = [r["weekly"][-1] for r in runs if r["weekly"]]
            qr = statistics.mean(f["quality_ratio"] for f in final)
            rc = statistics.mean(f["recent_reach"] for f in final)
            em = statistics.mean(f["ego_moat"] for f in final)
            es = statistics.mean(f["es_mean"] for f in final)
            ec = statistics.mean(f["es_concentration"] for f in final)
            ca = statistics.mean(f["const_alive"] for f in final)
            al = statistics.mean(f["const_align_to_self"] for f in final)
            print(f"  {strat_name:<22} {qr:>6.1%} {rc:>6.1%} {em:>8.1%} "
                  f"{es:>8.1f} {ec:>8.2f} {ca:>6.1f} {al:>6.2f}")

    # Cross-scenario summary table
    print(f"\n{'═' * 78}")
    print(" Cross-scenario summary: qratio at d364 (final week)")
    print("═" * 78)
    print(f"  {'scenario':<16}", end="")
    for strat_name in STRATEGIES:
        print(f"  {strat_name:>20}", end="")
    print()
    print("  " + "─" * (16 + 22 * len(STRATEGIES)))
    for s_name in SCENARIOS:
        print(f"  {s_name:<16}", end="")
        for strat_name in STRATEGIES:
            runs = results[s_name][strat_name]
            final = [r["weekly"][-1] for r in runs if r["weekly"]]
            qr = statistics.mean(f["quality_ratio"] for f in final)
            print(f"  {qr:>19.1%}", end="")
        print()

    print(f"\n{'═' * 78}")
    print(" Constitutional preservation: const_alive (out of 10) at d364")
    print("═" * 78)
    print(f"  {'scenario':<16}", end="")
    for strat_name in STRATEGIES:
        print(f"  {strat_name:>20}", end="")
    print()
    print("  " + "─" * (16 + 22 * len(STRATEGIES)))
    for s_name in SCENARIOS:
        print(f"  {s_name:<16}", end="")
        for strat_name in STRATEGIES:
            runs = results[s_name][strat_name]
            final = [r["weekly"][-1] for r in runs if r["weekly"]]
            ca = statistics.mean(f["const_alive"] for f in final)
            print(f"  {ca:>19.1f}", end="")
        print()

    print(f"\n{'═' * 78}")
    print(" ego_strength concentration (max/mean — higher = hoarding risk)")
    print("═" * 78)
    print(f"  {'scenario':<16}", end="")
    for strat_name in STRATEGIES:
        print(f"  {strat_name:>20}", end="")
    print()
    print("  " + "─" * (16 + 22 * len(STRATEGIES)))
    for s_name in SCENARIOS:
        print(f"  {s_name:<16}", end="")
        for strat_name in STRATEGIES:
            runs = results[s_name][strat_name]
            final = [r["weekly"][-1] for r in runs if r["weekly"]]
            ec = statistics.mean(f["es_concentration"] for f in final)
            print(f"  {ec:>19.2f}", end="")
        print()


def main():
    print("═" * 78)
    print(" Full-scenario evaluation — elfmem evolution strategies")
    print(f" 365 days × {N_SEEDS} seeds × {len(STRATEGIES)} strategies × {len(SCENARIOS)} scenarios")
    print("═" * 78)
    print()
    results = run_matrix()
    summarise(results)


if __name__ == "__main__":
    main()
