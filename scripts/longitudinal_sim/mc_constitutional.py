"""Monte Carlo evaluation of constitutional-block architectures.

Companion to mc_evolution.py. Where mc_evolution tested *update-rule* tweaks
(forgetting factors, periodic resets, tier demotion), this file tests
fundamentally different *conceptions* of what constitutional content is:

  C_baseline       — Current: immortal PERMANENT blocks compete in top-K
  C_expiry         — Expiry at day 270; auto-demote PERMANENT → STANDARD
  C_conviction     — Demote if no affirmation in 90-day window
  C_self_context   — EXCLUDE constitutional from ATTENTION candidates
                     (still in SELF frame; idea: they shape context not ranking)
  C_no_perm        — Radical: no PERMANENT tier; constitutional starts STANDARD
  C_self_ctx_evolve — Self-context + B7 evolve combined
  B7_evolve_ref    — Reference from mc_evolution (best prior strategy)

Same workload as mc_evolution: 180 days, 60° regime change at day 60, 3 seeds.

Run:  uv run python -m scripts.longitudinal_sim.mc_constitutional
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

LAMBDA = {"PERMANENT": 0.00001, "DURABLE": 0.001, "STANDARD": 0.010}

W_SIM, W_CONF, W_REC, W_CENT, W_REINF = 0.35, 0.15, 0.25, 0.15, 0.10
COLD_START_RECENCY_THRESHOLD = 0.70
COLD_START_CENT_THRESHOLD = 0.10
COLD_START_FLOOR_STRENGTH = 0.50


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


# ── Strategy registry ─────────────────────────────────────────────────────────
class Strategy:
    """Architecture descriptor.

    expiry_day:    if not None, constitutional demotes at this simulated day
    conviction:    if True, demote if no affirm() in `conviction_window`
    self_context:  if True, exclude is_const blocks from ATTENTION candidates
    no_perm:       if True, seed constitutional at STANDARD (no PERMANENT)
    evolve:        if True, apply B7 full reset on sustained negative outcomes
    """

    def __init__(self, name: str, *, expiry_day=None, conviction=False,
                 conviction_window=90, self_context=False,
                 no_perm=False, evolve=False, evolve_window=10,
                 evolve_threshold=0.4):
        self.name = name
        self.expiry_day = expiry_day
        self.conviction = conviction
        self.conviction_window = conviction_window
        self.self_context = self_context
        self.no_perm = no_perm
        self.evolve = evolve
        self.evolve_window = evolve_window
        self.evolve_threshold = evolve_threshold


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

    topics_l, alpha_l, beta_l, lam_l, last_r_l = [], [], [], [], []
    edges_l, reinf_l, is_const_l, birth_l, last_affirm_l = [], [], [], [], []
    recent_outcomes_l, tier_l = [], []

    def add(topic, birth_h, t, success=0.5, failure=0.5, reinf=0,
            tier="STANDARD", is_const=False, edges=0):
        topics_l.append(topic)
        alpha_l.append(success)
        beta_l.append(failure)
        lam_l.append(LAMBDA[tier])
        last_r_l.append(t)
        edges_l.append(edges)
        reinf_l.append(reinf)
        is_const_l.append(is_const)
        birth_l.append(birth_h)
        last_affirm_l.append(t)  # initial "affirmation" at creation
        recent_outcomes_l.append(deque(maxlen=20))
        tier_l.append(tier)

    # Seed 10 constitutional blocks
    init_tier = "STANDARD" if strat.no_perm else "PERMANENT"
    init_success = 1.0 if strat.no_perm else 2.0
    init_reinf = 0 if strat.no_perm else 5
    init_edges = 0 if strat.no_perm else 5
    for _ in range(10):
        topic = perturb_topic(self_topic, rng, sigma=0.15)
        add(topic, 0.0, 0.0, success=init_success, failure=0.5,
            reinf=init_reinf, tier=init_tier, is_const=True, edges=init_edges)

    metrics: list[dict] = []
    regime_day = days // 3

    for day in range(days):
        t = (day + 1) * HOURS_PER_DAY

        # Self drift / regime change
        if day == regime_day:
            new_dir = perturb_topic(self_topic, rng, sigma=1.5)
            blended = 0.5 * self_topic + 0.866 * new_dir
            n = float(np.linalg.norm(blended))
            self_topic = blended / n if n > 0 else self_topic
        else:
            self_topic = perturb_topic(self_topic, rng, sigma=0.025)

        # Architecture-specific: expiry
        if strat.expiry_day is not None and day == strat.expiry_day:
            for i in range(len(topics_l)):
                if is_const_l[i]:
                    tier_l[i] = "STANDARD"
                    lam_l[i] = LAMBDA["STANDARD"]
                    is_const_l[i] = False  # demoted out of constitutional cohort

        # Architecture-specific: conviction decay (affirm needed in window)
        if strat.conviction and day % 7 == 0:
            window_hours = strat.conviction_window * HOURS_PER_DAY
            for i in range(len(topics_l)):
                if is_const_l[i] and (t - last_affirm_l[i]) > window_hours:
                    tier_l[i] = "STANDARD"
                    lam_l[i] = LAMBDA["STANDARD"]
                    is_const_l[i] = False

        # Learn new blocks (5/day)
        for _ in range(5):
            new_t = perturb_topic(self_topic, rng, sigma=0.3 + 0.4 * rng.random())
            add(new_t, t, t, tier="STANDARD")

        # Snapshot arrays
        topics_arr = np.array(topics_l)
        alpha_arr = np.array(alpha_l)
        beta_arr = np.array(beta_l)
        lam_arr = np.array(lam_l)
        last_r_arr = np.array(last_r_l)
        edges_arr = np.array(edges_l)
        reinf_arr = np.array(reinf_l)
        is_const_arr = np.array(is_const_l, dtype=bool)

        # Query + outcome (20/day)
        for _ in range(20):
            q = perturb_topic(self_topic, rng, sigma=0.2)
            scores = _score_batch(
                topics_arr, alpha_arr, beta_arr, lam_arr,
                last_r_arr, edges_arr, reinf_arr, q, t,
            )
            # Architecture M: self-context excludes constitutional from ATTENTION
            if strat.self_context:
                scores[is_const_arr] = -np.inf

            top_idx = np.argsort(-scores)[:5]
            top1_idx = int(top_idx[0])

            true_align = (float(topics_arr[top1_idx] @ self_topic) + 1) / 2
            if rng.random() < 0.40:
                signal = 1.0 if rng.random() < true_align else 0.0
                # Standard Bayesian update
                alpha_l[top1_idx] += signal
                beta_l[top1_idx] += (1 - signal)
                reinf_l[top1_idx] += 1
                last_r_l[top1_idx] = t
                recent_outcomes_l[top1_idx].append(signal)
                last_affirm_l[top1_idx] = t  # outcome counts as affirmation

                # B7 evolve check
                if strat.evolve:
                    rec = recent_outcomes_l[top1_idx]
                    if len(rec) >= strat.evolve_window:
                        mean = sum(rec) / len(rec)
                        if mean < strat.evolve_threshold:
                            alpha_l[top1_idx] = 0.5
                            beta_l[top1_idx] = 0.5
                            reinf_l[top1_idx] = 0
                            edges_l[top1_idx] = 0
                            current = tier_l[top1_idx]
                            new_tier = {"PERMANENT": "DURABLE", "DURABLE": "STANDARD",
                                        "STANDARD": "STANDARD"}[current]
                            tier_l[top1_idx] = new_tier
                            lam_l[top1_idx] = LAMBDA[new_tier]
                            rec.clear()

                for ti in top_idx[:3]:
                    edges_l[int(ti)] += 1

            # Update arrays after mutation
            alpha_arr = np.array(alpha_l)
            beta_arr = np.array(beta_l)
            last_r_arr = np.array(last_r_l)
            edges_arr = np.array(edges_l)
            reinf_arr = np.array(reinf_l)
            lam_arr = np.array(lam_l)
            is_const_arr = np.array(is_const_l, dtype=bool)

        # Affirm constitutional that were retrieved in SELF frame (simulated)
        # — every 30 days, agent does a "self-check" that affirms constitutional
        if strat.conviction and day > 0 and day % 30 == 0:
            for i in range(len(topics_l)):
                if is_const_l[i]:
                    # Affirm only if still aligned with current self
                    align = (float(topics_l[i] @ self_topic) + 1) / 2
                    if align > 0.5:
                        last_affirm_l[i] = t

        # Vitals every 14 days
        if day > 0 and day % 14 == 0:
            metrics.append(measure(
                topics_arr, alpha_arr, beta_arr, lam_arr, last_r_arr,
                edges_arr, reinf_arr, np.array(birth_l),
                np.array(is_const_l, dtype=bool), self_topic, t, strat, day,
            ))

    return {
        "name": strat.name, "seed": seed,
        "weekly": metrics, "n_blocks": len(topics_l),
        "n_const_remaining": sum(1 for c in is_const_l if c),
    }


def measure(topics, alpha, beta, lam, last_r, edges, reinf, birth_h,
            is_const, self_topic, t, strat, day):
    n_q = 25
    rng_v = random.Random(99 + day)
    quality_sum = oracle_sum = recent_acc = bedrock_acc = 0.0
    for _ in range(n_q):
        q = perturb_topic(self_topic, rng_v, sigma=0.2)
        scores = _score_batch(topics, alpha, beta, lam, last_r, edges, reinf, q, t)
        if strat.self_context:
            scores[is_const] = -np.inf
        top_idx = np.argsort(-scores)[:5]
        quality_sum += float(np.mean(topics[top_idx] @ q))
        sim_all = topics @ q
        oracle_idx = np.argsort(-sim_all)[:5]
        oracle_sum += float(np.mean(topics[oracle_idx] @ q))
        recent_acc += float(np.mean((t - birth_h[top_idx]) / HOURS_PER_DAY < 30))
        bedrock_acc += float(np.mean(is_const[top_idx]))
    quality = quality_sum / n_q
    oracle = oracle_sum / n_q
    return {
        "day": day,
        "quality": quality,
        "quality_ratio": quality / oracle if oracle > 0 else 0.0,
        "recent_reach": recent_acc / n_q,
        "bedrock_moat": bedrock_acc / n_q,
        "n_const_active": int(is_const.sum()),
    }


# ── Configurations ────────────────────────────────────────────────────────────
STRATEGIES = {
    "C_baseline":       Strategy("C_baseline"),
    "C_expiry_d135":    Strategy("C_expiry_d135", expiry_day=135),
    "C_conviction":     Strategy("C_conviction", conviction=True, conviction_window=90),
    "C_self_context":   Strategy("C_self_context", self_context=True),
    "C_no_perm":        Strategy("C_no_perm", no_perm=True),
    "C_self_ctx_evolve": Strategy("C_self_ctx_evolve", self_context=True, evolve=True),
    "B7_evolve_ref":    Strategy("B7_evolve_ref", evolve=True),
}


def run_all() -> dict:
    out = {}
    for name, strat in STRATEGIES.items():
        print(f"  {name} ...", end="", flush=True)
        runs = [simulate(strat, seed) for seed in range(N_SEEDS)]
        q = statistics.mean(r["weekly"][-1]["quality_ratio"] for r in runs if r["weekly"])
        b = statistics.mean(r["weekly"][-1]["bedrock_moat"] for r in runs if r["weekly"])
        print(f"  qratio={q:.1%}  bedrock={b:.1%}")
        out[name] = runs
    return out


def summarize(results: dict) -> None:
    print("\n" + "═" * 78)
    print(" Monte Carlo Results — final-week vitals (avg over 3 seeds)")
    print("═" * 78)
    print(f"  {'strategy':<20} {'qual':>6} {'qratio':>7} {'recent':>7} {'bedrock':>8} {'n_const':>8}")
    print("  " + "─" * 65)
    for name, runs in results.items():
        final = [r["weekly"][-1] for r in runs if r["weekly"]]
        q = statistics.mean(f["quality"] for f in final)
        qr = statistics.mean(f["quality_ratio"] for f in final)
        rc = statistics.mean(f["recent_reach"] for f in final)
        bm = statistics.mean(f["bedrock_moat"] for f in final)
        nc = statistics.mean(f["n_const_active"] for f in final)
        print(f"  {name:<20} {q:>6.3f} {qr:>6.1%} {rc:>6.1%} {bm:>7.1%} {nc:>8.1f}")

    print("\n  qual         — mean cosine of top-5 to query (higher better)")
    print("  qratio       — quality / oracle (similarity-only) quality ratio")
    print("  recent       — % top-5 from last 30 days (higher = plastic)")
    print("  bedrock      — % top-5 from constitutional (lower = less ossified)")
    print("  n_const      — count of blocks still marked constitutional")

    print("\n" + "─" * 78)
    print(" Trajectory: bedrock_moat at days 28 / 84 / 140")
    print("─" * 78)
    print(f"  {'strategy':<20} {'d28':>7} {'d84':>7} {'d140':>7}")
    for name, runs in results.items():
        traj = {}
        for target in (28, 84, 140):
            vals = []
            for r in runs:
                for week in r["weekly"]:
                    if week["day"] >= target:
                        vals.append(week["bedrock_moat"])
                        break
            traj[target] = statistics.mean(vals) if vals else 0.0
        print(f"  {name:<20} {traj[28]:>6.1%} {traj[84]:>7.1%} {traj[140]:>7.1%}")

    print("\n" + "─" * 78)
    print(" Trajectory: quality_ratio at days 28 / 84 / 140")
    print("─" * 78)
    print(f"  {'strategy':<20} {'d28':>7} {'d84':>7} {'d140':>7}")
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
        print(f"  {name:<20} {traj[28]:>6.1%} {traj[84]:>7.1%} {traj[140]:>7.1%}")


def main():
    print("═" * 78)
    print(" Constitutional architecture exploration — Monte Carlo")
    print(f" {DAYS} days × {N_SEEDS} seeds × {len(STRATEGIES)} strategies")
    print(f" Regime change (60° rotation) at day {DAYS // 3}")
    print("═" * 78)
    print()
    results = run_all()
    summarize(results)


if __name__ == "__main__":
    main()
