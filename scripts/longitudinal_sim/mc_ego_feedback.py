"""Monte Carlo evaluation of ego-feedback constitutional architectures.

elf's hypothesis (2026-05-22): constitutional blocks should not be immortal
by decree. They should earn persistence through positive reinforcement. A
constitutional block that keeps proving useful becomes more persistent than
a normal block; one that's abandoned or contradicted decays normally.

This is the Darwinian model of identity: persistence is an outcome of use,
not a property of declaration.

Variants tested:
  C_baseline           — current: immortal PERMANENT constitutional
  C_self_context_M     — Architecture M from previous round (winner)
  V2_ego_continuous    — λ_ego = λ_base / (1 + α × ego_strength)
                         where ego_strength is leaky integrator of +outcomes
  V3_ego_tier          — discrete tier promotion based on ego_strength
  M_plus_V2_synthesis  — Architecture M + V2 ego-feedback
                         (excludes from ATTENTION, but evolves in SELF)
  V2_no_const_seed     — V2 mechanism applied to *all* blocks (no seeded
                         constitutional; identity emerges from use)

Same workload as mc_constitutional.py: 180 days, 60° regime change at
day 60, 3 seeds.

Run:  uv run python -m scripts.longitudinal_sim.mc_ego_feedback
"""
from __future__ import annotations

import math
import random
import statistics
from collections import deque

import numpy as np

DIM = 8
HOURS_PER_DAY = 4.0
DAYS = 180
N_SEEDS = 3

LAMBDA_BASE = {"PERMANENT": 0.00001, "DURABLE": 0.001, "STANDARD": 0.010}

W_SIM, W_CONF, W_REC, W_CENT, W_REINF = 0.35, 0.15, 0.25, 0.15, 0.10
COLD_START_RECENCY_THRESHOLD = 0.70
COLD_START_CENT_THRESHOLD = 0.10
COLD_START_FLOOR_STRENGTH = 0.50

# Ego-feedback parameters
EGO_POS_RATE = 1.0      # +1 per positive outcome
EGO_NEG_RATE = 0.3      # -0.3 per negative outcome (asymmetric)
EGO_TIME_DECAY = 0.05   # -0.05 per active hour (leaky)
EGO_LAMBDA_ALPHA = 0.05 # λ_ego = λ_base / (1 + α × ego_strength)

# V3 tier promotion thresholds (ego_strength)
V3_PROMOTE_DURABLE = 50
V3_PROMOTE_PERMANENT = 200
V3_DEMOTE_DURABLE = 25
V3_DEMOTE_STANDARD = 100


def random_topic(rng):
    v = np.array([rng.gauss(0, 1) for _ in range(DIM)])
    n = float(np.linalg.norm(v))
    return v / n if n > 0 else v


def perturb_topic(t, rng, sigma):
    noise = np.array([rng.gauss(0, sigma) for _ in range(DIM)])
    out = t + noise
    n = float(np.linalg.norm(out))
    return out / n if n > 0 else out


class Strategy:
    def __init__(self, name: str, *, self_context=False, ego_continuous=False,
                 ego_tier=False, no_perm_seed=True, ego_for_all=False,
                 seeded_constitutional=True, merit_reentry_threshold=None,
                 self_frame_reinforcement=False, self_query_rate=1):
        self.name = name
        self.self_context = self_context
        self.ego_continuous = ego_continuous
        self.ego_tier = ego_tier
        self.no_perm_seed = no_perm_seed
        self.ego_for_all = ego_for_all
        self.seeded_constitutional = seeded_constitutional
        self.merit_reentry_threshold = merit_reentry_threshold
        # Model C: SELF-frame queries provide outcomes to build ego_strength
        # for constitutional blocks. Decouples reinforcement channel from
        # ATTENTION (which uses M's exclusion).
        self.self_frame_reinforcement = self_frame_reinforcement
        self.self_query_rate = self_query_rate  # SELF queries per day


def _score_batch(topics, alpha, beta, lam, last_r, edges, reinf, q, t):
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


def simulate(strat: Strategy, seed: int, days: int = DAYS) -> dict:
    rng = random.Random(seed)
    np.random.seed(seed)

    self_topic = random_topic(rng)

    # Arrays (lists for mutation, converted to np.array per scoring pass)
    topics_l, alpha_l, beta_l, lam_l, last_r_l = [], [], [], [], []
    edges_l, reinf_l, is_ego_l, birth_l = [], [], [], []
    ego_strength_l, last_eval_h_l = [], []
    tier_l = []
    recent_outcomes_l = []

    def add(topic, birth_h, t, *, success=0.5, failure=0.5, reinf=0,
            tier="STANDARD", is_ego=False, edges=0, ego_strength=0.0):
        topics_l.append(topic)
        alpha_l.append(success)
        beta_l.append(failure)
        # If ego_continuous, lambda derives from ego_strength
        if strat.ego_continuous and (is_ego or strat.ego_for_all):
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
        recent_outcomes_l.append(deque(maxlen=20))

    # Seed constitutional
    if strat.seeded_constitutional:
        # If no_perm_seed=True (the ego-feedback hypothesis), seed at STANDARD
        # with elevated initial ego_strength (representing intentional setup).
        seed_tier = "STANDARD" if strat.no_perm_seed else "PERMANENT"
        seed_strength = 20.0 if strat.no_perm_seed else 0.0
        seed_success = 1.0 if strat.no_perm_seed else 2.0
        seed_reinf = 5 if not strat.no_perm_seed else 2
        seed_edges = 5 if not strat.no_perm_seed else 2
        for _ in range(10):
            topic = perturb_topic(self_topic, rng, sigma=0.15)
            add(topic, 0.0, 0.0, success=seed_success, failure=0.5,
                reinf=seed_reinf, tier=seed_tier, is_ego=True,
                edges=seed_edges, ego_strength=seed_strength)

    metrics = []
    regime_day = days // 3

    def update_ego_strength_v2(idx, delta, t):
        ego_strength_l[idx] = max(0.0, ego_strength_l[idx] + delta)
        # Recompute lambda from new ego_strength
        if strat.ego_continuous and (is_ego_l[idx] or strat.ego_for_all):
            lam_l[idx] = LAMBDA_BASE["STANDARD"] / (
                1 + EGO_LAMBDA_ALPHA * ego_strength_l[idx]
            )
        # V3 tier promotion/demotion
        if strat.ego_tier and is_ego_l[idx]:
            es = ego_strength_l[idx]
            current = tier_l[idx]
            new_tier = current
            if es >= V3_PROMOTE_PERMANENT and current != "PERMANENT":
                new_tier = "PERMANENT"
            elif es >= V3_PROMOTE_DURABLE and current == "STANDARD":
                new_tier = "DURABLE"
            elif es < V3_DEMOTE_DURABLE and current == "DURABLE":
                new_tier = "STANDARD"
            elif es < V3_DEMOTE_STANDARD and current == "PERMANENT":
                new_tier = "DURABLE"
            if new_tier != current:
                tier_l[idx] = new_tier
                lam_l[idx] = LAMBDA_BASE[new_tier]

    def daily_time_decay(t):
        """Apply leaky time-decay to all ego blocks each day."""
        if not (strat.ego_continuous or strat.ego_tier):
            return
        for i in range(len(topics_l)):
            if is_ego_l[i] or strat.ego_for_all:
                elapsed_h = t - last_eval_h_l[i]
                if elapsed_h > 0:
                    decay = EGO_TIME_DECAY * (elapsed_h / HOURS_PER_DAY)
                    update_ego_strength_v2(i, -decay, t)
                    last_eval_h_l[i] = t

    for day in range(days):
        t = (day + 1) * HOURS_PER_DAY

        # Drift / regime change
        if day == regime_day:
            new_dir = perturb_topic(self_topic, rng, sigma=1.5)
            blended = 0.5 * self_topic + 0.866 * new_dir
            n = float(np.linalg.norm(blended))
            self_topic = blended / n if n > 0 else self_topic
        else:
            self_topic = perturb_topic(self_topic, rng, sigma=0.025)

        # Apply ego time-decay each day
        daily_time_decay(t)

        # Learn 5 new blocks
        for _ in range(5):
            new_t = perturb_topic(self_topic, rng, sigma=0.3 + 0.4 * rng.random())
            # ego_for_all: new blocks also get ego mechanism
            add(new_t, t, t, tier="STANDARD",
                is_ego=strat.ego_for_all, ego_strength=0.0)

        # Snapshot
        topics_arr = np.array(topics_l)
        alpha_arr = np.array(alpha_l)
        beta_arr = np.array(beta_l)
        lam_arr = np.array(lam_l)
        last_r_arr = np.array(last_r_l)
        edges_arr = np.array(edges_l)
        reinf_arr = np.array(reinf_l)
        is_ego_arr = np.array(is_ego_l, dtype=bool)

        # Query + outcome
        for _ in range(20):
            q = perturb_topic(self_topic, rng, sigma=0.2)
            scores = _score_batch(
                topics_arr, alpha_arr, beta_arr, lam_arr,
                last_r_arr, edges_arr, reinf_arr, q, t,
            )
            if strat.self_context:
                scores[is_ego_arr] = -np.inf
            elif strat.merit_reentry_threshold is not None:
                # V9: constitutional in ATTENTION but score scaled by ego_strength.
                # min(1.0, ego_strength / threshold) — earns full visibility as
                # they prove themselves; discounted while building/decaying.
                es_arr = np.array(ego_strength_l)
                ego_gate = np.minimum(
                    1.0, es_arr / strat.merit_reentry_threshold
                )
                # Apply gate only to ego blocks
                gate = np.where(is_ego_arr, ego_gate, 1.0)
                scores = scores * gate

            top_idx = np.argsort(-scores)[:5]
            top1_idx = int(top_idx[0])

            true_align = (float(topics_arr[top1_idx] @ self_topic) + 1) / 2
            if rng.random() < 0.40:
                signal = 1.0 if rng.random() < true_align else 0.0
                alpha_l[top1_idx] += signal
                beta_l[top1_idx] += (1 - signal)
                reinf_l[top1_idx] += 1
                last_r_l[top1_idx] = t
                recent_outcomes_l[top1_idx].append(signal)

                # Ego feedback: asymmetric leaky integrator
                if is_ego_l[top1_idx] or strat.ego_for_all:
                    delta = EGO_POS_RATE if signal > 0.5 else -EGO_NEG_RATE
                    update_ego_strength_v2(top1_idx, delta, t)

                for ti in top_idx[:3]:
                    edges_l[int(ti)] += 1

            # Refresh arrays
            alpha_arr = np.array(alpha_l)
            beta_arr = np.array(beta_l)
            lam_arr = np.array(lam_l)
            last_r_arr = np.array(last_r_l)
            edges_arr = np.array(edges_l)
            reinf_arr = np.array(reinf_l)

        # Model C: SELF-frame queries provide reinforcement channel for
        # constitutional blocks. Run self_query_rate queries/day; each
        # retrieves from constitutional pool only and produces outcomes.
        if strat.self_frame_reinforcement:
            for _ in range(strat.self_query_rate):
                # SELF query: query is just current self (no perturbation —
                # "what represents me right now?")
                q = self_topic
                # Score among constitutional blocks only
                const_idx = np.where(is_ego_arr)[0]
                if len(const_idx) == 0:
                    continue
                # SELF frame: queryless-like — weight confidence + centrality high
                const_scores = (
                    0.10 * (topics_arr[const_idx] @ q + 1) / 2
                    + 0.30 * (alpha_arr[const_idx] / (alpha_arr[const_idx] + beta_arr[const_idx]))
                    + 0.05 * np.exp(-lam_arr[const_idx] * (t - last_r_arr[const_idx]))
                    + 0.25 * np.minimum(1.0, edges_arr[const_idx] / 10.0)
                    + 0.30 * np.minimum(1.0, np.log1p(reinf_arr[const_idx]) / np.log(101))
                )
                top_in_const = int(const_idx[np.argmax(const_scores)])
                # Outcome reflects current alignment of THIS block to self
                true_align = (float(topics_arr[top_in_const] @ self_topic) + 1) / 2
                if rng.random() < 0.6:  # higher outcome rate for SELF queries
                    signal = 1.0 if rng.random() < true_align else 0.0
                    alpha_l[top_in_const] += signal
                    beta_l[top_in_const] += (1 - signal)
                    reinf_l[top_in_const] += 1
                    last_r_l[top_in_const] = t
                    delta = EGO_POS_RATE if signal > 0.5 else -EGO_NEG_RATE
                    update_ego_strength_v2(top_in_const, delta, t)
            # Refresh arrays
            alpha_arr = np.array(alpha_l)
            beta_arr = np.array(beta_l)
            lam_arr = np.array(lam_l)
            last_r_arr = np.array(last_r_l)
            reinf_arr = np.array(reinf_l)

        # Vitals every 14 days
        if day > 0 and day % 14 == 0:
            metrics.append(measure(
                topics_arr, alpha_arr, beta_arr, lam_arr, last_r_arr,
                edges_arr, reinf_arr, np.array(birth_l),
                is_ego_arr, np.array(ego_strength_l), self_topic, t, strat, day,
            ))

    # Final tier distribution
    tier_counts = {"PERMANENT": 0, "DURABLE": 0, "STANDARD": 0}
    for i in range(len(tier_l)):
        if is_ego_l[i]:
            tier_counts[tier_l[i]] += 1

    return {
        "name": strat.name, "seed": seed,
        "weekly": metrics, "n_blocks": len(topics_l),
        "n_ego": sum(1 for c in is_ego_l if c),
        "tier_counts": tier_counts,
        "mean_ego_strength": float(np.mean([
            ego_strength_l[i] for i in range(len(topics_l)) if is_ego_l[i]
        ])) if any(is_ego_l) else 0.0,
    }


def measure(topics, alpha, beta, lam, last_r, edges, reinf, birth_h,
            is_ego, ego_strength, self_topic, t, strat, day):
    n_q = 25
    rng_v = random.Random(99 + day)
    quality_sum = oracle_sum = recent_acc = ego_acc = 0.0
    for _ in range(n_q):
        q = perturb_topic(self_topic, rng_v, sigma=0.2)
        scores = _score_batch(topics, alpha, beta, lam, last_r, edges, reinf, q, t)
        if strat.self_context:
            scores[is_ego] = -np.inf
        elif strat.merit_reentry_threshold is not None:
            ego_gate = np.minimum(1.0, ego_strength / strat.merit_reentry_threshold)
            gate = np.where(is_ego, ego_gate, 1.0)
            scores = scores * gate
        top_idx = np.argsort(-scores)[:5]
        quality_sum += float(np.mean(topics[top_idx] @ q))
        sim_all = topics @ q
        oracle_idx = np.argsort(-sim_all)[:5]
        oracle_sum += float(np.mean(topics[oracle_idx] @ q))
        recent_acc += float(np.mean((t - birth_h[top_idx]) / HOURS_PER_DAY < 30))
        ego_acc += float(np.mean(is_ego[top_idx]))
    quality = quality_sum / n_q
    oracle = oracle_sum / n_q
    return {
        "day": day,
        "quality": quality,
        "quality_ratio": quality / oracle if oracle > 0 else 0.0,
        "recent_reach": recent_acc / n_q,
        "ego_moat": ego_acc / n_q,
        "mean_ego_strength": float(np.mean(ego_strength[is_ego])) if is_ego.any() else 0.0,
        "n_ego_active": int(is_ego.sum()),
    }


# ── Configurations ────────────────────────────────────────────────────────────
STRATEGIES = {
    "C_baseline":           Strategy("C_baseline", no_perm_seed=False),
    "C_self_context_M":     Strategy("C_self_context_M", self_context=True, no_perm_seed=False),
    "V2_ego_continuous":    Strategy("V2_ego_continuous", ego_continuous=True, no_perm_seed=True),
    "ModelC_M_plus_self":   Strategy("ModelC_M_plus_self", self_context=True,
                                      ego_continuous=True, no_perm_seed=True,
                                      self_frame_reinforcement=True, self_query_rate=2),
    "ModelC_high_self":     Strategy("ModelC_high_self", self_context=True,
                                      ego_continuous=True, no_perm_seed=True,
                                      self_frame_reinforcement=True, self_query_rate=5),
}


def run_all() -> dict:
    out = {}
    for name, strat in STRATEGIES.items():
        print(f"  {name} ...", end="", flush=True)
        runs = [simulate(strat, seed) for seed in range(N_SEEDS)]
        q = statistics.mean(r["weekly"][-1]["quality_ratio"] for r in runs if r["weekly"])
        e = statistics.mean(r["weekly"][-1]["ego_moat"] for r in runs if r["weekly"])
        mes = statistics.mean(r["mean_ego_strength"] for r in runs)
        print(f"  qratio={q:.1%}  ego_moat={e:.1%}  mean_ego_strength={mes:.1f}")
        out[name] = runs
    return out


def summarize(results: dict) -> None:
    print("\n" + "═" * 78)
    print(" Ego-feedback architecture — final-week vitals (avg over 3 seeds)")
    print("═" * 78)
    print(f"  {'strategy':<22} {'qual':>6} {'qratio':>7} {'recent':>7} {'ego_moat':>9} {'ego_str':>8}")
    print("  " + "─" * 70)
    for name, runs in results.items():
        final = [r["weekly"][-1] for r in runs if r["weekly"]]
        q = statistics.mean(f["quality"] for f in final)
        qr = statistics.mean(f["quality_ratio"] for f in final)
        rc = statistics.mean(f["recent_reach"] for f in final)
        em = statistics.mean(f["ego_moat"] for f in final)
        es = statistics.mean(f["mean_ego_strength"] for f in final)
        print(f"  {name:<22} {q:>6.3f} {qr:>6.1%} {rc:>6.1%} {em:>8.1%} {es:>8.1f}")

    print("\n  qual         — mean cosine of top-5 to query")
    print("  qratio       — quality / oracle quality ratio")
    print("  recent       — % top-5 from last 30 days")
    print("  ego_moat     — % top-5 from ego blocks")
    print("  ego_str      — mean ego_strength across ego blocks (V2/V3 only)")

    # Trajectory: ego_moat over time
    print("\n" + "─" * 78)
    print(" Trajectory: ego_moat at days 28 / 84 / 140")
    print("─" * 78)
    print(f"  {'strategy':<22} {'d28':>7} {'d84':>7} {'d140':>7}")
    for name, runs in results.items():
        traj = {}
        for target in (28, 84, 140):
            vals = []
            for r in runs:
                for week in r["weekly"]:
                    if week["day"] >= target:
                        vals.append(week["ego_moat"])
                        break
            traj[target] = statistics.mean(vals) if vals else 0.0
        print(f"  {name:<22} {traj[28]:>6.1%} {traj[84]:>7.1%} {traj[140]:>7.1%}")

    print("\n" + "─" * 78)
    print(" Trajectory: quality_ratio at days 28 / 84 / 140")
    print("─" * 78)
    print(f"  {'strategy':<22} {'d28':>7} {'d84':>7} {'d140':>7}")
    for name, runs in results.items():
        traj = {}
        for target in (28, 84, 140):
            vals = []
            for r in runs:
                for week in r["weekly"]:
                    if week["day"] >= target:
                        vals.append(week["quality_ratio"])
                        break
            traj[target] = statistics.mean(vals) if vals else 0.0
        print(f"  {name:<22} {traj[28]:>6.1%} {traj[84]:>7.1%} {traj[140]:>7.1%}")

    # Ego strength trajectory — shows the leaky integrator in action
    print("\n" + "─" * 78)
    print(" Trajectory: mean ego_strength at days 28 / 60 (pre-regime) / 84 / 140")
    print(" (V2/V3 only — shows asymmetric reinforcement under SELF drift)")
    print("─" * 78)
    print(f"  {'strategy':<22} {'d28':>7} {'d60':>7} {'d84':>7} {'d140':>7}")
    for name, runs in results.items():
        traj = {}
        for target in (28, 60, 84, 140):
            vals = []
            for r in runs:
                for week in r["weekly"]:
                    if week["day"] >= target:
                        vals.append(week["mean_ego_strength"])
                        break
            traj[target] = statistics.mean(vals) if vals else 0.0
        print(f"  {name:<22} {traj[28]:>6.1f} {traj[60]:>7.1f} {traj[84]:>7.1f} {traj[140]:>7.1f}")


def main():
    print("═" * 78)
    print(" Ego-feedback constitutional architectures — Monte Carlo")
    print(f" {DAYS} days × {N_SEEDS} seeds × {len(STRATEGIES)} strategies")
    print(f" Regime change at day {DAYS // 3} (60° rotation)")
    print(f" Ego params: pos=+{EGO_POS_RATE}, neg=-{EGO_NEG_RATE}, decay=-{EGO_TIME_DECAY}/day")
    print(f"             λ_ego = λ_base / (1 + {EGO_LAMBDA_ALPHA} × ego_strength)")
    print("═" * 78)
    print()
    results = run_all()
    summarize(results)


if __name__ == "__main__":
    main()
