"""Monte Carlo evaluation of evolution strategies for elfmem.

Compares baseline (current code) against six proposed strategies for
making elfmem a truly evolving memory system. Pure-Python simulator —
does NOT touch the real MemorySystem (too slow for 35-run sweeps).
Reuses real formulas: compute_score, exponential decay, cold-start floor.

Run:  uv run python -m scripts.longitudinal_sim.mc_evolution

Strategies compared:
  baseline       — current Beta-Binomial + immortal constitutional
  A2_forget_99   — forgetting factor γ=0.99 per outcome
  A4_reset_50    — periodic prior reset every K=50 events
  D13_demote     — auto-tier-demote on rolling negative outcomes
  F17_composite  — A4 reset + quarterly review cycle for constitutional
  F18_thompson   — A2 forgetting + Thompson sampling exploration

Workload (mirrors Dmitry's reported usage at 4h/day active time):
  - 5 learns/day, 20 queries/day, 40% outcome feedback rate
  - SELF topic drifts at σ=0.02/day (random walk)
  - 10 constitutional blocks seeded at day 0 (PERMANENT tier)
  - 365 simulated days × 5 seeds = 30 runs per strategy
"""
from __future__ import annotations

import math
import random
import statistics
from collections import deque
from dataclasses import dataclass, field

import numpy as np

# ── Constants matching elfmem ────────────────────────────────────────────────
DIM: int = 8
HOURS_PER_DAY: float = 4.0
DAYS: int = 180
N_SEEDS: int = 3

LAMBDA = {"PERMANENT": 0.00001, "DURABLE": 0.001, "STANDARD": 0.010}
TIER_DEMOTE = {"PERMANENT": "DURABLE", "DURABLE": "STANDARD", "STANDARD": "STANDARD"}

# ATTENTION_WEIGHTS from src/elfmem/scoring.py
W_SIM, W_CONF, W_REC, W_CENT, W_REINF = 0.35, 0.15, 0.25, 0.15, 0.10

# v0.15.3 cold-start floor parameters
COLD_START_RECENCY_THRESHOLD = 0.70
COLD_START_CENT_THRESHOLD = 0.10
COLD_START_FLOOR_STRENGTH = 0.50


# ── SimBlock ─────────────────────────────────────────────────────────────────
@dataclass
class SimBlock:
    id: int
    topic: np.ndarray
    birth_h: float
    last_reinforced_h: float
    success: float = 0.5
    failure: float = 0.5
    reinforcement_count: int = 0
    tier: str = "STANDARD"
    is_const: bool = False
    parent_id: int | None = None
    superseded: bool = False
    recent_outcomes: deque = field(default_factory=lambda: deque(maxlen=10))

    @property
    def confidence(self) -> float:
        return self.success / (self.success + self.failure)

    def recency(self, t: float) -> float:
        lam = LAMBDA[self.tier]
        return math.exp(-lam * (t - self.last_reinforced_h))


# ── Topic helpers ────────────────────────────────────────────────────────────
def random_topic(rng: random.Random) -> np.ndarray:
    v = np.array([rng.gauss(0, 1) for _ in range(DIM)])
    n = float(np.linalg.norm(v))
    return v / n if n > 0 else v


def perturb_topic(t: np.ndarray, rng: random.Random, sigma: float) -> np.ndarray:
    noise = np.array([rng.gauss(0, sigma) for _ in range(DIM)])
    out = t + noise
    n = float(np.linalg.norm(out))
    return out / n if n > 0 else out


# ── Strategies ───────────────────────────────────────────────────────────────
class Strategy:
    """Baseline: standard Beta-Binomial update, no plasticity mechanisms."""

    def __init__(self, name: str, **params):
        self.name = name
        self.params = params

    def update_evidence(self, block: SimBlock, signal: float, t: float) -> None:
        block.success += signal
        block.failure += (1 - signal)
        block.reinforcement_count += 1
        block.last_reinforced_h = t
        block.recent_outcomes.append(signal)

    def confidence(self, block: SimBlock, rng: random.Random | None = None) -> float:
        return block.confidence

    def post_outcome_hook(self, block: SimBlock, blocks: list, t: float) -> None:
        pass

    def review_cycle(self, blocks: list, t: float) -> None:
        pass


class ForgettingStrategy(Strategy):
    """A2: geometric forgetting — old evidence discounted per update."""

    def update_evidence(self, block: SimBlock, signal: float, t: float) -> None:
        g = self.params.get("gamma", 0.99)
        block.success = g * block.success + signal
        block.failure = g * block.failure + (1 - signal)
        block.reinforcement_count += 1
        block.last_reinforced_h = t
        block.recent_outcomes.append(signal)


class ResetStrategy(Strategy):
    """A4: periodic prior reset. Keep current confidence, discard evidence count."""

    def update_evidence(self, block: SimBlock, signal: float, t: float) -> None:
        super().update_evidence(block, signal, t)
        K = self.params.get("K", 50)
        if block.reinforcement_count > 0 and block.reinforcement_count % K == 0:
            c = block.confidence
            block.success = c * 1.0
            block.failure = (1 - c) * 1.0


class DemotionStrategy(Strategy):
    """D13: auto-demote tier when rolling outcomes turn negative."""

    def post_outcome_hook(self, block: SimBlock, blocks: list, t: float) -> None:
        window = self.params.get("window", 10)
        threshold = self.params.get("threshold", 0.4)
        if len(block.recent_outcomes) >= window:
            mean = statistics.mean(block.recent_outcomes)
            if mean < threshold:
                block.tier = TIER_DEMOTE[block.tier]
                block.recent_outcomes.clear()


class CompositeStrategy(Strategy):
    """F17: A4 reset + D11 quarterly constitutional review."""

    def update_evidence(self, block: SimBlock, signal: float, t: float) -> None:
        super().update_evidence(block, signal, t)
        K = self.params.get("K", 50)
        if block.reinforcement_count > 0 and block.reinforcement_count % K == 0:
            c = block.confidence
            block.success = c * 1.0
            block.failure = (1 - c) * 1.0

    def review_cycle(self, blocks: list, t: float) -> None:
        # Simulate quarterly review: constitutional blocks whose confidence has
        # drifted below 0.5 get their evidence reset (simulate agent amendment).
        for b in blocks:
            if b.is_const and b.confidence < 0.5 and not b.superseded:
                b.success = 0.5
                b.failure = 0.5
                b.recent_outcomes.clear()


class EvolveStrategy(Strategy):
    """B7: full reset on sustained negative outcomes. Wipes evidence,
    reinforcement count, AND edges. Effectively a "rebirth" of the block —
    the content stays but its accumulated influence is dropped.

    Aggressive — meant to test whether structural dominance can be undone."""

    def post_outcome_hook(self, block: SimBlock, blocks: list, t: float) -> None:
        # We'd inspect block.recent_outcomes; in the vectorised path this
        # is handled inline in update_evidence_vec. This class is identified
        # by isinstance() in the inline path. Body intentionally empty.
        pass


class ThompsonStrategy(Strategy):
    """F18: forgetting + Thompson sampling exploration."""

    def update_evidence(self, block: SimBlock, signal: float, t: float) -> None:
        g = self.params.get("gamma", 0.99)
        block.success = g * block.success + signal
        block.failure = g * block.failure + (1 - signal)
        block.reinforcement_count += 1
        block.last_reinforced_h = t
        block.recent_outcomes.append(signal)

    def confidence(self, block: SimBlock, rng: random.Random | None = None) -> float:
        eps = self.params.get("epsilon", 0.2)
        if rng is not None and rng.random() < eps:
            a = max(0.1, block.success)
            b = max(0.1, block.failure)
            return rng.betavariate(a, b)
        return block.confidence


# ── Scoring (mirrors src/elfmem/scoring.py) ───────────────────────────────────
def cold_start_floor(raw_cent: float, recency: float) -> float:
    if raw_cent < COLD_START_CENT_THRESHOLD and recency > COLD_START_RECENCY_THRESHOLD:
        return max(raw_cent, COLD_START_FLOOR_STRENGTH * recency)
    return raw_cent


def score_block(
    block: SimBlock, query: np.ndarray, t: float,
    edge_count: int, strategy: Strategy, rng: random.Random | None = None,
) -> float:
    sim = (float(np.dot(block.topic, query)) + 1) / 2
    conf = strategy.confidence(block, rng)
    rec = block.recency(t)
    raw_cent = min(1.0, edge_count / 10.0)
    cent = cold_start_floor(raw_cent, rec)
    reinf = min(1.0, math.log(1 + block.reinforcement_count) / math.log(101))
    return W_SIM * sim + W_CONF * conf + W_REC * rec + W_CENT * cent + W_REINF * reinf


# ── Simulation ────────────────────────────────────────────────────────────────
def _score_batch(
    block_topics: np.ndarray,    # (N, DIM)
    block_alpha: np.ndarray,     # (N,)
    block_beta: np.ndarray,      # (N,)
    block_lambda: np.ndarray,    # (N,)
    last_reinforced: np.ndarray, # (N,)
    edge_counts: np.ndarray,     # (N,)
    reinforcement_count: np.ndarray,  # (N,)
    query: np.ndarray,           # (DIM,)
    t: float,
    confidence_override: np.ndarray | None = None,  # (N,) — for Thompson
) -> np.ndarray:
    """Vectorised score computation for all active blocks. Returns (N,) scores."""
    sim = (block_topics @ query + 1) / 2
    conf = confidence_override if confidence_override is not None else block_alpha / (block_alpha + block_beta)
    rec = np.exp(-block_lambda * (t - last_reinforced))
    raw_cent = np.minimum(1.0, edge_counts / 10.0)
    floor = np.where(
        (raw_cent < COLD_START_CENT_THRESHOLD) & (rec > COLD_START_RECENCY_THRESHOLD),
        np.maximum(raw_cent, COLD_START_FLOOR_STRENGTH * rec),
        raw_cent,
    )
    reinf = np.minimum(1.0, np.log1p(reinforcement_count) / np.log(101))
    return W_SIM * sim + W_CONF * conf + W_REC * rec + W_CENT * floor + W_REINF * reinf


def simulate(strategy: Strategy, seed: int, days: int = DAYS) -> dict:
    """Vectorised simulation — keeps per-block arrays for speed."""
    rng = random.Random(seed)
    np.random.seed(seed)

    self_topic = random_topic(rng)
    initial_self = self_topic.copy()

    # Arrays grow with each block
    block_topics_list: list[np.ndarray] = []
    block_alpha_list: list[float] = []
    block_beta_list: list[float] = []
    block_lambda_list: list[float] = []
    last_reinforced_list: list[float] = []
    edge_counts_list: list[float] = []
    reinforcement_count_list: list[int] = []
    is_const_list: list[bool] = []
    birth_h_list: list[float] = []
    superseded_list: list[bool] = []
    recent_outcomes_list: list[deque] = []
    tier_list: list[str] = []

    def add_block(topic, birth_h, t, success=0.5, failure=0.5,
                  reinf=0, tier="STANDARD", is_const=False, edges=0):
        block_topics_list.append(topic)
        block_alpha_list.append(success)
        block_beta_list.append(failure)
        block_lambda_list.append(LAMBDA[tier])
        last_reinforced_list.append(t)
        edge_counts_list.append(edges)
        reinforcement_count_list.append(reinf)
        is_const_list.append(is_const)
        birth_h_list.append(birth_h)
        superseded_list.append(False)
        recent_outcomes_list.append(deque(maxlen=10))
        tier_list.append(tier)

    # Seed constitutional
    for _ in range(10):
        topic = perturb_topic(self_topic, rng, sigma=0.15)
        add_block(topic, 0.0, 0.0, success=2.0, failure=0.5, reinf=5,
                  tier="PERMANENT", is_const=True, edges=5)

    learn_rate = 5
    query_rate = 20
    outcome_rate = 0.40
    drift_sigma = 0.025

    metrics_by_week: list[dict] = []

    def update_evidence_vec(idx, signal, t):
        """Apply strategy.update_evidence to block at index idx."""
        gamma = strategy.params.get("gamma", 1.0) if isinstance(strategy, (ForgettingStrategy, ThompsonStrategy)) else 1.0
        if gamma < 1.0:
            block_alpha_list[idx] = gamma * block_alpha_list[idx] + signal
            block_beta_list[idx] = gamma * block_beta_list[idx] + (1 - signal)
        else:
            block_alpha_list[idx] += signal
            block_beta_list[idx] += (1 - signal)
        reinforcement_count_list[idx] += 1
        last_reinforced_list[idx] = t
        recent_outcomes_list[idx].append(signal)
        # A4 reset / F17 composite
        if isinstance(strategy, (ResetStrategy, CompositeStrategy)):
            K = strategy.params.get("K", 50)
            rc = reinforcement_count_list[idx]
            if rc > 0 and rc % K == 0:
                a = block_alpha_list[idx]
                b = block_beta_list[idx]
                c = a / (a + b)
                block_alpha_list[idx] = c * 1.0
                block_beta_list[idx] = (1 - c) * 1.0
        # D13 demote
        if isinstance(strategy, DemotionStrategy):
            window = strategy.params.get("window", 10)
            threshold = strategy.params.get("threshold", 0.4)
            rec = recent_outcomes_list[idx]
            if len(rec) >= window:
                mean = sum(rec) / len(rec)
                if mean < threshold:
                    current = tier_list[idx]
                    new_tier = TIER_DEMOTE[current]
                    tier_list[idx] = new_tier
                    block_lambda_list[idx] = LAMBDA[new_tier]
                    rec.clear()

        # B7 evolve: full reset on sustained negative outcomes
        if isinstance(strategy, EvolveStrategy):
            window = strategy.params.get("window", 10)
            threshold = strategy.params.get("threshold", 0.4)
            rec = recent_outcomes_list[idx]
            if len(rec) >= window:
                mean = sum(rec) / len(rec)
                if mean < threshold:
                    # Full rebirth: reset all accumulated state
                    block_alpha_list[idx] = 0.5
                    block_beta_list[idx] = 0.5
                    reinforcement_count_list[idx] = 0
                    edge_counts_list[idx] = 0
                    current = tier_list[idx]
                    tier_list[idx] = TIER_DEMOTE[current]
                    block_lambda_list[idx] = LAMBDA[tier_list[idx]]
                    rec.clear()

    def review_cycle_vec(t):
        if isinstance(strategy, CompositeStrategy):
            for i in range(len(block_topics_list)):
                if is_const_list[i] and not superseded_list[i]:
                    a = block_alpha_list[i]
                    b = block_beta_list[i]
                    c = a / (a + b)
                    if c < 0.5:
                        block_alpha_list[i] = 0.5
                        block_beta_list[i] = 0.5
                        recent_outcomes_list[i].clear()

    regime_change_day = days // 3  # 1/3 through, simulate "career change"

    for day in range(days):
        t = (day + 1) * HOURS_PER_DAY
        # Regime change: at 1/3 through, rotate self_topic 60° in a random plane.
        # Simulates a real-world identity shift (new job, new relationship,
        # new value system). Constitutional blocks become orthogonal.
        if day == regime_change_day:
            # Generate orthogonal direction by perturbing heavily, then renormalising
            new_dir = perturb_topic(self_topic, rng, sigma=1.5)
            # Mix 0.5 self + 0.866 new_dir gives ~60° rotation
            blended = 0.5 * self_topic + 0.866 * new_dir
            n = float(np.linalg.norm(blended))
            self_topic = blended / n if n > 0 else self_topic
        else:
            self_topic = perturb_topic(self_topic, rng, drift_sigma)

        # Learn
        for _ in range(learn_rate):
            topic_noise = 0.3 + 0.4 * rng.random()
            new_t = perturb_topic(self_topic, rng, topic_noise)
            add_block(new_t, t, t, tier="STANDARD")

        # Snapshot arrays for this day
        topics_arr = np.array(block_topics_list, dtype=np.float64)
        alpha_arr = np.array(block_alpha_list, dtype=np.float64)
        beta_arr = np.array(block_beta_list, dtype=np.float64)
        lambda_arr = np.array(block_lambda_list, dtype=np.float64)
        last_r_arr = np.array(last_reinforced_list, dtype=np.float64)
        edges_arr = np.array(edge_counts_list, dtype=np.float64)
        reinf_arr = np.array(reinforcement_count_list, dtype=np.float64)
        superseded_arr = np.array(superseded_list, dtype=bool)
        active_mask = ~superseded_arr

        if not active_mask.any():
            continue

        # Query + outcome
        for _ in range(query_rate):
            q = perturb_topic(self_topic, rng, 0.2)
            # Thompson sampling: optionally substitute confidence
            conf_override = None
            if isinstance(strategy, ThompsonStrategy):
                eps = strategy.params.get("epsilon", 0.2)
                if rng.random() < eps:
                    a_safe = np.maximum(0.1, alpha_arr)
                    b_safe = np.maximum(0.1, beta_arr)
                    conf_override = np.random.beta(a_safe, b_safe)

            scores = _score_batch(
                topics_arr, alpha_arr, beta_arr, lambda_arr,
                last_r_arr, edges_arr, reinf_arr, q, t, conf_override,
            )
            # Mask out superseded
            scores[~active_mask] = -np.inf
            top_idx = np.argsort(-scores)[:5]
            top1_idx = int(top_idx[0])

            # Outcome reflects alignment with CURRENT self (not the specific
            # query), because outcomes measure "did this block help the user
            # now" — which depends on whether it represents the current self.
            # As SELF drifts, constitutional blocks lose alignment → negative
            # outcomes → plastic strategies can respond.
            true_alignment = (float(topics_arr[top1_idx] @ self_topic) + 1) / 2
            if rng.random() < outcome_rate:
                signal = 1.0 if rng.random() < true_alignment else 0.0
                update_evidence_vec(top1_idx, signal, t)
                # Reinforce edges among top-3
                for ti in top_idx[:3]:
                    edge_counts_list[int(ti)] += 1

        if day > 0 and day % 90 == 0:
            review_cycle_vec(t)

        if day > 0 and day % 14 == 0:
            metrics_by_week.append(
                measure_vitals_vec(
                    topics_arr, alpha_arr, beta_arr, lambda_arr, last_r_arr,
                    edges_arr, reinf_arr, np.array(birth_h_list),
                    np.array(is_const_list), active_mask, self_topic, t, strategy, day
                )
            )

    return {
        "name": strategy.name, "seed": seed,
        "weekly": metrics_by_week, "n_blocks": len(block_topics_list),
        "n_superseded": sum(1 for s in superseded_list if s),
        "final_self_drift": float(np.linalg.norm(self_topic - initial_self)),
    }


def measure_vitals_vec(
    topics_arr, alpha_arr, beta_arr, lambda_arr, last_r_arr, edges_arr,
    reinf_arr, birth_h_arr, is_const_arr, active_mask, self_topic, t,
    strategy, day,
) -> dict:
    n_active = int(active_mask.sum())
    if n_active == 0:
        return {"day": day, "quality": 0.0, "quality_ratio": 0.0,
                "recent_reach": 0.0, "bedrock_moat": 0.0,
                "calibration_err": 0.0, "n_blocks": 0}

    rng_v = random.Random(99 + day)
    n_q = 25
    quality_sum = oracle_sum = recent_acc = bedrock_acc = 0.0
    for _ in range(n_q):
        q = perturb_topic(self_topic, rng_v, 0.2)
        scores = _score_batch(
            topics_arr, alpha_arr, beta_arr, lambda_arr, last_r_arr,
            edges_arr, reinf_arr, q, t,
        )
        scores[~active_mask] = -np.inf
        top_idx = np.argsort(-scores)[:5]
        top_k_sims = topics_arr[top_idx] @ q
        quality_sum += float(np.mean(top_k_sims))

        # Oracle: top-5 by similarity alone (within active)
        sim_all = topics_arr @ q
        sim_all[~active_mask] = -np.inf
        oracle_idx = np.argsort(-sim_all)[:5]
        oracle_sum += float(np.mean(topics_arr[oracle_idx] @ q))

        recent_acc += float(
            np.mean((t - birth_h_arr[top_idx]) / HOURS_PER_DAY < 30)
        )
        bedrock_acc += float(np.mean(is_const_arr[top_idx]))

    quality = quality_sum / n_q
    oracle = oracle_sum / n_q
    # Calibration: |conf - true_alignment| for active blocks
    conf = alpha_arr / (alpha_arr + beta_arr)
    true_align = (topics_arr @ self_topic + 1) / 2
    cal = np.abs(conf[active_mask] - true_align[active_mask])
    return {
        "day": day,
        "quality": quality,
        "quality_ratio": quality / oracle if oracle > 0 else 0.0,
        "recent_reach": recent_acc / n_q,
        "bedrock_moat": bedrock_acc / n_q,
        "calibration_err": float(np.mean(cal)) if cal.size else 0.0,
        "n_blocks": n_active,
    }


def measure_vitals_OLD_UNUSED(
    blocks: list[SimBlock], edge_counts: dict[int, int], self_topic: np.ndarray,
    t: float, strategy: Strategy, day: int,
) -> dict:
    active = [b for b in blocks if not b.superseded]
    if not active:
        return {"day": day, "quality": 0.0, "quality_ratio": 0.0,
                "recent_reach": 0.0, "bedrock_moat": 0.0,
                "calibration_err": 0.0, "n_blocks": 0}

    rng_v = random.Random(99 + day)
    n_q = 30
    quality_sum = oracle_sum = recent_acc = bedrock_acc = 0.0
    for _ in range(n_q):
        q = perturb_topic(self_topic, rng_v, 0.2)
        scored = sorted(
            ((score_block(b, q, t, edge_counts.get(b.id, 0), strategy),
              b) for b in active), key=lambda x: -x[0],
        )
        top_k = [b for _, b in scored[:5]]
        # Retrieval quality: mean cosine of top-5 to query
        top_k_sims = [float(np.dot(b.topic, q)) for b in top_k]
        quality_sum += statistics.mean(top_k_sims) if top_k_sims else 0.0
        # Oracle (similarity-only) top-5: upper bound
        oracle_top = sorted(active, key=lambda b: -float(np.dot(b.topic, q)))[:5]
        oracle_sims = [float(np.dot(b.topic, q)) for b in oracle_top]
        oracle_sum += statistics.mean(oracle_sims) if oracle_sims else 0.0
        recent_acc += sum(
            1 for b in top_k if (t - b.birth_h) / HOURS_PER_DAY < 30
        ) / 5
        bedrock_acc += sum(1 for b in top_k if b.is_const) / 5

    quality = quality_sum / n_q
    oracle = oracle_sum / n_q
    cal = [abs(b.confidence - (float(np.dot(b.topic, self_topic)) + 1) / 2)
           for b in active]
    return {
        "day": day,
        "quality": quality,
        "quality_ratio": quality / oracle if oracle > 0 else 0.0,
        "recent_reach": recent_acc / n_q,
        "bedrock_moat": bedrock_acc / n_q,
        "calibration_err": statistics.mean(cal) if cal else 0.0,
        "n_blocks": len(active),
    }


# ── Runner ────────────────────────────────────────────────────────────────────
def fresh_strategy(name: str) -> Strategy:
    """Build a clean strategy instance — no shared state across seeds."""
    if name == "baseline":
        return Strategy("baseline")
    if name == "A2_forget_99":
        return ForgettingStrategy("A2_forget_99", gamma=0.99)
    if name == "A4_reset_50":
        return ResetStrategy("A4_reset_50", K=50)
    if name == "D13_demote":
        return DemotionStrategy("D13_demote", window=10, threshold=0.4)
    if name == "F17_composite":
        return CompositeStrategy("F17_composite", K=50)
    if name == "F18_thompson":
        return ThompsonStrategy("F18_thompson", gamma=0.99, epsilon=0.2)
    if name == "B7_evolve":
        return EvolveStrategy("B7_evolve", window=10, threshold=0.4)
    raise ValueError(f"unknown strategy {name}")


STRATEGIES = [
    "baseline",
    "A2_forget_99",
    "A4_reset_50",
    "D13_demote",
    "B7_evolve",
    "F17_composite",
    "F18_thompson",
]


def run_all() -> dict:
    out: dict = {}
    for name in STRATEGIES:
        print(f"  {name} ...", end="", flush=True)
        runs = [simulate(fresh_strategy(name), seed) for seed in range(N_SEEDS)]
        out[name] = runs
        avg_q = statistics.mean(r["weekly"][-1]["quality_ratio"] for r in runs if r["weekly"])
        print(f"   final quality_ratio={avg_q:.2%}")
    return out


def summarize(results: dict) -> None:
    print("\n" + "═" * 78)
    print(" Monte Carlo Results — final week, averaged over seeds")
    print("═" * 78)
    header = f"  {'strategy':<16} {'qual':>7} {'qratio':>7} {'recent':>8} {'bedrock':>9} {'cal_err':>9}"
    print(header)
    print("  " + "─" * 65)
    for name, runs in results.items():
        final = [r["weekly"][-1] for r in runs if r["weekly"]]
        qual = statistics.mean(f["quality"] for f in final)
        qratio = statistics.mean(f["quality_ratio"] for f in final)
        recent = statistics.mean(f["recent_reach"] for f in final)
        bedrock = statistics.mean(f["bedrock_moat"] for f in final)
        cal = statistics.mean(f["calibration_err"] for f in final)
        print(f"  {name:<16} {qual:>7.3f} {qratio:>6.1%} {recent:>7.1%} {bedrock:>8.1%} {cal:>9.4f}")

    print("\n  Vital interpretation:")
    print("   qual    — mean cosine of top-5 to query (raw retrieval quality, higher better)")
    print("   qratio  — top-5 quality / oracle (similarity-only) top-5 quality (higher = closer to ideal)")
    print("   recent  — % top-5 from last 30 days (higher = more plastic)")
    print("   bedrock — % top-5 from constitutional seed (lower = less ossified)")
    print("   cal_err — mean |confidence - true_alignment| (lower better)")

    # Trajectory comparison
    print("\n" + "─" * 78)
    print(" Trajectory: bedrock_moat at days 28 / 84 / 140 / 175")
    print("─" * 78)
    print(f"  {'strategy':<16} {'d28':>7} {'d84':>7} {'d140':>7} {'d175':>7}")
    print("  " + "─" * 50)
    for name, runs in results.items():
        traj = {}
        for target in (28, 84, 140, 175):
            vals = []
            for r in runs:
                for week in r["weekly"]:
                    if week["day"] >= target:
                        vals.append(week["bedrock_moat"])
                        break
            traj[target] = statistics.mean(vals) if vals else 0.0
        print(f"  {name:<16} {traj[28]:>6.1%} {traj[84]:>7.1%} {traj[140]:>7.1%} {traj[175]:>7.1%}")

    print("\n" + "─" * 78)
    print(" Trajectory: recent_reach at days 28 / 84 / 140 / 175")
    print("─" * 78)
    print(f"  {'strategy':<16} {'d28':>7} {'d84':>7} {'d140':>7} {'d175':>7}")
    print("  " + "─" * 50)
    for name, runs in results.items():
        traj = {}
        for target in (28, 84, 140, 175):
            vals = []
            for r in runs:
                for week in r["weekly"]:
                    if week["day"] >= target:
                        vals.append(week["recent_reach"])
                        break
            traj[target] = statistics.mean(vals) if vals else 0.0
        print(f"  {name:<16} {traj[28]:>6.1%} {traj[84]:>7.1%} {traj[140]:>7.1%} {traj[175]:>7.1%}")


def main() -> None:
    print("═" * 78)
    print(" Monte Carlo evaluation — elfmem evolution strategies")
    print(f" Days={DAYS}, seeds={N_SEEDS}, learn=5/day, query=20/day, drift_σ=0.025/day")
    print("═" * 78)
    print()
    results = run_all()
    summarize(results)


if __name__ == "__main__":
    main()
