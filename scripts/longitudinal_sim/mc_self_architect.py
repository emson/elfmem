"""Self-architecting elfmem — agent learns its own architecture parameters.

Tests whether an agent can identify the best architectural parameters for
its own usage pattern by continuously measuring vitals and adjusting four
knobs:

  attention_const_weight ∈ [0, 1]  (1=baseline, 0=Architecture M)
  ego_alpha ∈ [0, 0.1]             (0=no ego, 0.05=Model C-like)
  distribute_n ∈ [1, 5]            (1=winner-take-all, 5=spread)
  self_check_freq ∈ [0, 4]         (SELF queries per day)

Mechanism:
  - Bootstrap: start at (0, 0, 1, 2) — M-like with single-target SELF
  - Every 28 days: try small perturbations to each parameter
  - Estimate qratio gain via short shadow eval (5 queries × no-state-mutation)
  - Pick parameter and direction with best estimated gain
  - Move by max(20%, fixed step) toward improvement
  - Repeat

Compared against the three fixed architectures (M, Model C, Model D)
across stable / drift / regime_change scenarios.

Run: uv run python -m scripts.longitudinal_sim.mc_self_architect
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

# Self-architect tuning
ADAPT_INTERVAL_DAYS = 28
BOOTSTRAP_DAYS = 30  # don't adapt before this
STEP_FRAC = 0.20  # max change per period as fraction of range


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
class Params:
    attention_const_weight: float = 0.0  # 0 = M (exclude); 1 = baseline (include)
    ego_alpha: float = 0.0               # 0 = no ego; 0.05 = Model C
    distribute_n: int = 1                # 1 = winner-take-all; 3 = distributed
    self_check_freq: int = 2             # SELF queries per day


PARAM_RANGES = {
    "attention_const_weight": (0.0, 1.0),
    "ego_alpha": (0.0, 0.1),
    "distribute_n": (1, 5),
    "self_check_freq": (0, 4),
}


@dataclass
class Scenario:
    name: str
    drift_sigma: float = 0.020
    regime_days: tuple = ()


SCENARIOS = {
    "stable":        Scenario("stable", drift_sigma=0.005),
    "drift":         Scenario("drift", drift_sigma=0.020),
    "regime_change": Scenario("regime_change", drift_sigma=0.020, regime_days=(120,)),
}


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


def simulate_step(
    topics_arr, alpha_arr, beta_arr, lam_arr, last_r_arr, edges_arr, reinf_arr,
    is_ego_arr, query, t, params: Params,
):
    """Run one ATTENTION query at given params. Returns top-5 indices, scores."""
    scores = score_batch(topics_arr, alpha_arr, beta_arr, lam_arr, last_r_arr,
                          edges_arr, reinf_arr, query, t)
    # Soft attention exclusion: constitutional get score * w (w=0 -> excluded;
    # w=1 -> full participation; intermediate -> graduated participation)
    if params.attention_const_weight < 1.0:
        gate = np.where(is_ego_arr, params.attention_const_weight, 1.0)
        scores = scores * gate
    top_idx = np.argsort(-scores)[:5]
    return top_idx, scores


def shadow_eval(topics_arr, alpha_arr, beta_arr, lam_arr, last_r_arr,
                 edges_arr, reinf_arr, is_ego_arr, self_topic, t, params, rng):
    """Estimate qratio under given params using 8 shadow queries (no state mutation)."""
    n_q = 8
    rng_v = random.Random(rng.randint(0, 1 << 30))
    q_total = o_total = 0.0
    for _ in range(n_q):
        q = perturb_topic(self_topic, rng_v, sigma=0.2)
        top_idx, _ = simulate_step(
            topics_arr, alpha_arr, beta_arr, lam_arr, last_r_arr,
            edges_arr, reinf_arr, is_ego_arr, q, t, params,
        )
        q_total += float(np.mean(topics_arr[top_idx] @ q))
        oracle_idx = np.argsort(-(topics_arr @ q))[:5]
        o_total += float(np.mean(topics_arr[oracle_idx] @ q))
    return q_total / max(0.001, o_total)


def adapt_params(current: Params, vitals_fn) -> tuple[Params, str]:
    """Try perturbing each parameter; pick the best move."""
    base_quality = vitals_fn(current)
    best_move = ("baseline", current, base_quality)

    for param_name, (lo, hi) in PARAM_RANGES.items():
        cur_val = getattr(current, param_name)
        if isinstance(cur_val, int):
            steps = [-1, 1]
        else:
            step_size = (hi - lo) * STEP_FRAC
            steps = [-step_size, step_size]
        for d in steps:
            new_val = max(lo, min(hi, cur_val + d))
            if isinstance(cur_val, int):
                new_val = int(round(new_val))
            if new_val == cur_val:
                continue
            candidate = Params(
                attention_const_weight=current.attention_const_weight,
                ego_alpha=current.ego_alpha,
                distribute_n=current.distribute_n,
                self_check_freq=current.self_check_freq,
            )
            setattr(candidate, param_name, new_val)
            q = vitals_fn(candidate)
            if q > best_move[2]:
                best_move = (f"{param_name}{'+' if d>0 else '-'}", candidate, q)

    return best_move[1], best_move[0]


def simulate(initial_params: Params, scenario: Scenario, seed: int,
              self_architect: bool = False) -> dict:
    """Run sim; if self_architect=True, agent adapts params every 28 days."""
    rng = random.Random(seed)
    np.random.seed(seed)
    self_topic = random_topic(rng)

    params = Params(
        attention_const_weight=initial_params.attention_const_weight,
        ego_alpha=initial_params.ego_alpha,
        distribute_n=initial_params.distribute_n,
        self_check_freq=initial_params.self_check_freq,
    )

    topics_l, alpha_l, beta_l, lam_l, last_r_l = [], [], [], [], []
    edges_l, reinf_l, is_ego_l, birth_l = [], [], [], []
    ego_strength_l, last_eval_h_l = [], []

    def add(topic, birth_h, t, *, success=0.5, failure=0.5, reinf=0, tier="STANDARD",
            is_ego=False, edges=0, ego_strength=0.0):
        topics_l.append(topic)
        alpha_l.append(success)
        beta_l.append(failure)
        if params.ego_alpha > 0 and is_ego:
            lam = LAMBDA_BASE["STANDARD"] / (1 + params.ego_alpha * max(0, ego_strength))
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

    # Seed constitutional — start as STANDARD since we want adaptive
    for _ in range(10):
        topic = perturb_topic(self_topic, rng, sigma=0.15)
        add(topic, 0.0, 0.0, success=1.0, failure=0.5, reinf=2,
            tier="STANDARD", is_ego=True, edges=2, ego_strength=20.0)

    def update_ego(idx, delta, t):
        ego_strength_l[idx] = max(0.0, ego_strength_l[idx] + delta)
        if params.ego_alpha > 0 and is_ego_l[idx]:
            lam_l[idx] = LAMBDA_BASE["STANDARD"] / (1 + params.ego_alpha * ego_strength_l[idx])

    weekly_metrics = []
    param_history = []

    for day in range(DAYS):
        t = (day + 1) * HOURS_PER_DAY

        # Drift / regime
        if day in scenario.regime_days:
            new_dir = perturb_topic(self_topic, rng, sigma=1.5)
            blended = 0.5 * self_topic + 0.866 * new_dir
            n = float(np.linalg.norm(blended))
            self_topic = blended / n if n > 0 else self_topic
        else:
            self_topic = perturb_topic(self_topic, rng, sigma=scenario.drift_sigma)

        # Time decay for ego blocks
        if params.ego_alpha > 0:
            for i in range(len(topics_l)):
                if is_ego_l[i]:
                    elapsed = t - last_eval_h_l[i]
                    if elapsed > 0:
                        update_ego(i, -EGO_TIME_DECAY * elapsed / HOURS_PER_DAY, t)
                        last_eval_h_l[i] = t

        # Learn 5/day
        for _ in range(5):
            new_t = perturb_topic(self_topic, rng, sigma=0.3 + 0.4 * rng.random())
            add(new_t, t, t, tier="STANDARD", is_ego=False)

        topics_arr = np.array(topics_l)
        alpha_arr = np.array(alpha_l)
        beta_arr = np.array(beta_l)
        lam_arr = np.array(lam_l)
        last_r_arr = np.array(last_r_l)
        edges_arr = np.array(edges_l)
        reinf_arr = np.array(reinf_l)
        is_ego_arr = np.array(is_ego_l, dtype=bool)

        # 20 ATTENTION queries
        for _ in range(20):
            q = perturb_topic(self_topic, rng, sigma=0.2)
            top_idx, _ = simulate_step(
                topics_arr, alpha_arr, beta_arr, lam_arr, last_r_arr,
                edges_arr, reinf_arr, is_ego_arr, q, t, params,
            )
            top1_idx = int(top_idx[0])
            true_align = (float(topics_arr[top1_idx] @ self_topic) + 1) / 2
            if rng.random() < 0.40:
                signal = 1.0 if rng.random() < true_align else 0.0
                alpha_l[top1_idx] += signal
                beta_l[top1_idx] += (1 - signal)
                reinf_l[top1_idx] += 1
                last_r_l[top1_idx] = t
                if is_ego_l[top1_idx] and params.ego_alpha > 0:
                    delta = EGO_POS_RATE if signal > 0.5 else -EGO_NEG_RATE
                    update_ego(top1_idx, delta, t)
                for ti in top_idx[:3]:
                    edges_l[int(ti)] += 1
            alpha_arr = np.array(alpha_l)
            beta_arr = np.array(beta_l)
            lam_arr = np.array(lam_l)
            last_r_arr = np.array(last_r_l)
            edges_arr = np.array(edges_l)
            reinf_arr = np.array(reinf_l)

        # SELF feedback (distributed if distribute_n > 1)
        for _ in range(params.self_check_freq):
            const_idx = np.where(is_ego_arr)[0]
            if len(const_idx) == 0:
                continue
            const_scores = (
                0.10 * (topics_arr[const_idx] @ self_topic + 1) / 2
                + 0.30 * (alpha_arr[const_idx] / (alpha_arr[const_idx] + beta_arr[const_idx]))
                + 0.05 * np.exp(-lam_arr[const_idx] * (t - last_r_arr[const_idx]))
                + 0.25 * np.minimum(1.0, edges_arr[const_idx] / 10.0)
                + 0.30 * np.minimum(1.0, np.log1p(reinf_arr[const_idx]) / np.log(101))
            )
            top_n = min(params.distribute_n, len(const_idx))
            top_local = np.argsort(-const_scores)[:top_n]
            top_global = [int(const_idx[i]) for i in top_local]
            top_s = const_scores[top_local]
            ex = np.exp(top_s - top_s.max())
            weights = ex / ex.sum()
            for w, idx in zip(weights, top_global):
                true_align = (float(topics_arr[idx] @ self_topic) + 1) / 2
                if rng.random() < 0.6:
                    signal = 1.0 if rng.random() < true_align else 0.0
                    alpha_l[idx] += signal * float(w)
                    beta_l[idx] += (1 - signal) * float(w)
                    reinf_l[idx] += float(w)
                    last_r_l[idx] = t
                    if params.ego_alpha > 0:
                        delta = (EGO_POS_RATE if signal > 0.5 else -EGO_NEG_RATE) * float(w)
                        update_ego(idx, delta, t)

        # Vitals every 14 days
        if day > 0 and day % 14 == 0:
            weekly_metrics.append(measure(
                np.array(topics_l), np.array(alpha_l), np.array(beta_l),
                np.array(lam_l), np.array(last_r_l), np.array(edges_l),
                np.array(reinf_l), np.array(birth_l),
                np.array(is_ego_l, dtype=bool), np.array(ego_strength_l),
                self_topic, t, params, day,
            ))

        # Self-architect: adapt params every ADAPT_INTERVAL_DAYS days (after bootstrap)
        if self_architect and day >= BOOTSTRAP_DAYS and day % ADAPT_INTERVAL_DAYS == 0:
            topics_snapshot = np.array(topics_l)
            alpha_snapshot = np.array(alpha_l)
            beta_snapshot = np.array(beta_l)
            lam_snapshot = np.array(lam_l)
            last_r_snapshot = np.array(last_r_l)
            edges_snapshot = np.array(edges_l)
            reinf_snapshot = np.array(reinf_l)
            is_ego_snapshot = np.array(is_ego_l, dtype=bool)

            def evaluate(p: Params, _topics=topics_snapshot, _alpha=alpha_snapshot,
                          _beta=beta_snapshot, _lam=lam_snapshot, _lastr=last_r_snapshot,
                          _edges=edges_snapshot, _reinf=reinf_snapshot,
                          _isego=is_ego_snapshot, _self=self_topic, _t=t,
                          _rng=rng) -> float:
                return shadow_eval(_topics, _alpha, _beta, _lam, _lastr, _edges,
                                    _reinf, _isego, _self, _t, p, _rng)

            new_params, move_taken = adapt_params(params, evaluate)
            params = new_params
            param_history.append((day, move_taken, {
                "attention_const_weight": params.attention_const_weight,
                "ego_alpha": params.ego_alpha,
                "distribute_n": params.distribute_n,
                "self_check_freq": params.self_check_freq,
            }))

    return {
        "scenario": scenario.name, "seed": seed,
        "weekly": weekly_metrics,
        "final_params": {
            "attention_const_weight": params.attention_const_weight,
            "ego_alpha": params.ego_alpha,
            "distribute_n": params.distribute_n,
            "self_check_freq": params.self_check_freq,
        },
        "param_history": param_history,
    }


def measure(topics, alpha, beta, lam, last_r, edges, reinf, birth_h,
             is_ego, ego_strength, self_topic, t, params, day):
    n_q = 20
    rng_v = random.Random(99 + day)
    q_sum = o_sum = bedrock_acc = 0.0
    for _ in range(n_q):
        q = perturb_topic(self_topic, rng_v, sigma=0.2)
        scores = score_batch(topics, alpha, beta, lam, last_r, edges, reinf, q, t)
        if params.attention_const_weight < 1.0:
            gate = np.where(is_ego, params.attention_const_weight, 1.0)
            scores = scores * gate
        top_idx = np.argsort(-scores)[:5]
        q_sum += float(np.mean(topics[top_idx] @ q))
        o_sum += float(np.mean(topics[np.argsort(-(topics @ q))[:5]] @ q))
        bedrock_acc += float(np.mean(is_ego[top_idx]))
    return {
        "day": day,
        "quality_ratio": q_sum / max(0.001, o_sum),
        "bedrock_moat": bedrock_acc / n_q,
    }


# ── Comparison runs ───────────────────────────────────────────────────────────
def main():
    print("═" * 78)
    print(" Self-architecting elfmem — does the agent learn its own architecture?")
    print(f" {DAYS} days × {N_SEEDS} seeds × 3 scenarios")
    print(f" Bootstrap days: {BOOTSTRAP_DAYS}; Adapt every {ADAPT_INTERVAL_DAYS} days")
    print("═" * 78)
    print()

    # Fixed baselines for comparison
    fixed_archs = {
        "baseline":  Params(attention_const_weight=1.0, ego_alpha=0.0, distribute_n=1, self_check_freq=0),
        "M":         Params(attention_const_weight=0.0, ego_alpha=0.0, distribute_n=1, self_check_freq=0),
        "Model_C":   Params(attention_const_weight=0.0, ego_alpha=0.05, distribute_n=1, self_check_freq=2),
        "Model_D":   Params(attention_const_weight=0.0, ego_alpha=0.05, distribute_n=3, self_check_freq=2),
    }

    # Self-architect starts at M-like defaults
    sa_init = Params(attention_const_weight=0.0, ego_alpha=0.0, distribute_n=1, self_check_freq=2)

    results = {}
    for sc_name, sc in SCENARIOS.items():
        print(f"  Scenario: {sc_name}")
        results[sc_name] = {}
        for arch_name, arch_params in fixed_archs.items():
            runs = [simulate(arch_params, sc, seed, self_architect=False) for seed in range(N_SEEDS)]
            final = [r["weekly"][-1] for r in runs if r["weekly"]]
            qr = statistics.mean(f["quality_ratio"] for f in final)
            print(f"    {arch_name:<20} qratio={qr:.1%}")
            results[sc_name][arch_name] = (runs, qr)

        # Self-architect
        runs = [simulate(sa_init, sc, seed, self_architect=True) for seed in range(N_SEEDS)]
        final = [r["weekly"][-1] for r in runs if r["weekly"]]
        qr = statistics.mean(f["quality_ratio"] for f in final)
        final_p_avg = {
            k: statistics.mean(r["final_params"][k] for r in runs)
            for k in ("attention_const_weight", "ego_alpha", "distribute_n", "self_check_freq")
        }
        n_moves_avg = statistics.mean(len(r["param_history"]) for r in runs)
        print(f"    {'self_architect':<20} qratio={qr:.1%}  moves={n_moves_avg:.1f}  "
              f"final=(atw={final_p_avg['attention_const_weight']:.2f}, "
              f"ego={final_p_avg['ego_alpha']:.3f}, dN={final_p_avg['distribute_n']:.1f}, "
              f"scf={final_p_avg['self_check_freq']:.1f})")
        results[sc_name]["self_architect"] = (runs, qr)
        print()

    # Cross-scenario summary
    print("═" * 78)
    print(" Cross-scenario qratio")
    print("═" * 78)
    print(f"  {'scenario':<16}", end="")
    for arch in ("baseline", "M", "Model_C", "Model_D", "self_architect"):
        print(f"  {arch:>16}", end="")
    print()
    print("  " + "─" * 100)
    for sc_name in SCENARIOS:
        print(f"  {sc_name:<16}", end="")
        for arch in ("baseline", "M", "Model_C", "Model_D", "self_architect"):
            _, qr = results[sc_name][arch]
            print(f"  {qr:>15.1%}", end="")
        print()

    # Parameter trajectories
    print("\n" + "═" * 78)
    print(" Self-architect — parameter trajectories per scenario")
    print("═" * 78)
    for sc_name in SCENARIOS:
        print(f"\n  ── {sc_name} ──")
        runs, _ = results[sc_name]["self_architect"]
        # Show first seed's history
        history = runs[0]["param_history"]
        # Print sparse — every 4th adaptation
        for i, (day, move, params) in enumerate(history):
            if i % 3 == 0 or i == len(history) - 1:
                print(f"    d{day:>3}  {move:<28} "
                      f"atw={params['attention_const_weight']:.2f}  "
                      f"ego={params['ego_alpha']:.3f}  "
                      f"dN={params['distribute_n']}  "
                      f"scf={params['self_check_freq']}")


if __name__ == "__main__":
    main()
