# elfmem Trading Integration — Hello World Example

**A complete walkthrough of how elfmem integrates with a trading analytics system.**

Last updated: 2026-03-05

---

## Overview

This note describes how elfmem acts as the **knowledge layer** inside a trading bot that uses Brier scoring, Monte Carlo simulation, and adaptive edge thresholds. It works through a concrete "hello world" example — a single Bitcoin price forecast — to show exactly how every elfmem operation maps to a step in the trading lifecycle.

The key design principle: **elfmem knows nothing about trading**. It stores and retrieves knowledge. An external adapter translates trading outcomes (Brier scores) into elfmem's native language (signals, reinforcement, decay). This separation keeps elfmem domain-agnostic.

---

## System Layers

```
┌─────────────────────────────────────────────────────────┐
│                   TRADING BOT                           │
│                                                         │
│  ┌───────────────────┐   ┌────────────────────┐         │
│  │  ANALYTICS LAYER  │   │  SIMULATION LAYER  │         │
│  │                   │   │                    │         │
│  │  ForecastRecord   │   │  GBM Monte Carlo   │         │
│  │  Brier score      │   │  Kelly fraction    │         │
│  │  Calibration      │   │  VaR / CVaR        │         │
│  │  Empirical Bayes  │   │  prob_positive     │         │
│  │  Edge thresholds  │   │                    │         │
│  └────────┬──────────┘   └────────┬───────────┘         │
│           │ outcome signals       │ position sizing     │
│           └──────────┬────────────┘                     │
│                      ▼                                  │
│  ┌───────────────────────────────────────┐              │
│  │         ElfmemTradingAdapter          │              │
│  │                                       │              │
│  │  brier_score → outcome signal [0,1]   │              │
│  │  calibration → SELF frame update      │              │
│  │  resolution  → reinforce or penalize  │              │
│  └───────────────────┬───────────────────┘              │
│                      ▼                                  │
│  ┌───────────────────────────────────────┐              │
│  │              elfmem                   │              │
│  │                                       │              │
│  │  learn()       — store observations   │              │
│  │  consolidate() — merge inbox blocks   │              │
│  │  frame()       — get forecast context │              │
│  │  outcome()     — Bayesian update      │              │
│  │  penalize()    — accelerate decay     │  ← gap today │
│  │  SELF frame    — identity + bias      │              │
│  └───────────────────────────────────────┘              │
└─────────────────────────────────────────────────────────┘
```

---

## The 7-Step Hello World Loop

### Setup

```python
import asyncio
from elfmem import MemorySystem

async def main():
    memory = await MemorySystem.from_config()   # SQLite, zero infrastructure
    store = ForecastStore("forecasts.jsonl")    # external, not elfmem
    adapter = ElfmemTradingAdapter(memory, store)

    await memory.begin_session()
    # ... see steps below ...
    await memory.end_session()
```

---

### Step 1 — LEARN: store a market observation

The agent sees Bitcoin breaking $70k on high volume. This is a raw observation.

```python
block_id = await adapter.learn_observation(
    "Bitcoin broke above $72k on above-average volume. "
    "Spot ETF inflows hit $400M in a single day. "
    "Open interest on CME futures increased 18%.",
    domain="crypto",
    confidence=0.85,     # high: this is a factual observation
)
```

**What elfmem does:**
- Calls `memory.learn()` with tags `["crypto", "observation"]`
- Assigns `DecayTier.EPHEMERAL` (because category="observation")
- Default `decay_lambda = 0.050` — observations decay fast unless reinforced
- Block lands in `status="inbox"`, waits for consolidation

**Why confidence=0.85:** The agent is confident in the *fact* (ETF data is public), not in any forecast.

---

### Step 2 — CONSOLIDATE: merge inbox into active knowledge

```python
await memory.consolidate()
```

**What elfmem does:**
- LLM merges related inbox blocks (deduplication, contradiction detection)
- Surviving blocks promoted to `status="active"`
- Embeddings computed for vector retrieval
- SELF alignment scored

Run this once per session or when inbox exceeds threshold. Not every step.

---

### Step 3 — RECALL: build forecast context

```python
question = "Will Bitcoin exceed $80,000 within 30 days?"

frame_result = await memory.frame("attention", query=question)
# frame_result.text contains the most relevant active blocks,
# formatted and token-budgeted, ready to inject into an LLM prompt
```

**What elfmem does internally (4-stage pipeline):**

```
Stage 1 — Pre-filter: active blocks only, tag filter if specified
Stage 2 — Vector: cosine similarity against query embedding
Stage 3 — Graph: 1-hop neighbours of top vector hits
Stage 4 — Composite: weighted score (similarity, confidence,
          recency, centrality, reinforcement)
```

The `"attention"` frame weights similarity (0.35) and recency (0.25) heavily — good for "what's relevant right now?" questions.

To track which blocks were used for feedback later:

```python
# Lower-level call that returns block IDs
recall_result = await memory.recall(question, tags=["crypto"])
block_ids = [b.id for b in recall_result.blocks]
context_text = "\n\n".join(b.content for b in recall_result.blocks)
```

---

### Step 4 — FORECAST: call your LLM

This step is entirely outside elfmem. The adapter injects elfmem context into the prompt.

```python
# Adversarial 3-stage forecast (your own LLM call)
probability = await adversarial_forecast(
    question=question,
    context=context_text,    # elfmem-retrieved blocks
    self_model=await memory.frame("self").text,  # identity/bias context
)
# e.g. probability = 0.68

# Determine edge threshold from calibration
thresholds = compute_domain_thresholds(store.resolved())
threshold = thresholds.get("crypto", DEFAULT_THRESHOLD)  # 0.08 default
market_price = 0.55    # current Polymarket price
edge = probability - market_price   # 0.13

if edge < threshold:
    print("Edge below threshold — skip trade")
    return
```

**Record the forecast for future calibration:**

```python
record = ForecastRecord(
    id=f"forecast-{uuid.uuid4().hex[:8]}",
    question=question,
    domain="crypto",
    forecast_probability=probability,
    block_ids=block_ids,      # ← critical: links forecast to elfmem blocks
)
store.append(record)
```

`block_ids` is the bridge. When this forecast resolves, we use these IDs to feed the outcome back to the exact blocks that contributed.

---

### Step 5 — POSITION: Monte Carlo sizing

Also outside elfmem. Uses the simulation layer independently.

```python
sim = simulate(historical_closes, paths=1000, horizon=30)
# sim.prob_positive = 0.61, sim.kelly_fraction = 0.08

if sim.prob_positive <= 0.50:
    print("GBM says no edge — skip trade")
    return

position_size = sim.kelly_fraction * capital   # $800 on $10,000
print(f"Position: ${position_size:.0f} (Kelly {sim.kelly_fraction:.1%})")
```

Monte Carlo never touches elfmem. It uses historical price data, not stored knowledge.

---

### Step 6 — RESOLVE: feed outcome back to elfmem

When the forecast resolves (Bitcoin hit $80k by the deadline):

```python
# Analytics layer computes Brier score
resolved = store.resolve(record.id, outcome=1.0)   # YES it happened
# resolved.brier_score = (0.68 - 1.0)² = 0.1024

# Adapter translates to elfmem signal
await adapter.on_resolution(
    brier_score=resolved.brier_score,
    block_ids=resolved.block_ids,
    outcome=resolved.outcome,
)
```

**Inside `on_resolution()` — the translation layer:**

```python
async def on_resolution(self, brier_score, block_ids, outcome):
    # Convert Brier score to a quality signal in [0.0, 1.0]
    # Brier 0.0 = perfect → signal 1.0
    # Brier 0.25 = random → signal 0.0
    signal = max(0.0, 1.0 - (brier_score / 0.25))

    await self.memory.outcome(
        block_ids=block_ids,
        signal=signal,
        weight=1.0,      # one forecast = one unit of evidence
        source="brier",
    )
```

**What elfmem does with `outcome(signal=0.59, source="brier")`:**

For each contributing block:
1. Bayesian Beta-Binomial update: confidence shifts toward the signal
2. Audit record written to `block_outcomes` table
3. If `signal > reinforce_threshold` (0.5 default): `reinforce_blocks()` called
   - `reinforcement_count += 1`
   - `last_reinforced_at = current_active_hours`
   - Co-retrieval edges reinforced

A Brier score of 0.1024 maps to signal 0.59 — positive signal, blocks reinforced.
A Brier score of 0.2500 maps to signal 0.00 — blocks not reinforced, decay continues.
A Brier score > 0.25 (worse than random) maps to signal < 0.00 → clamped to 0.00.

**What's missing today (the `penalize()` gap):**

When a forecast is confidently wrong (Brier > 0.15, signal near 0), the blocks that contributed are not reinforced — correct. But they also aren't actively penalised. Their `decay_lambda` stays at the same rate. A block that caused three bad forecasts decays at the same speed as a fresh block.

The `penalize()` method (described in `plan_penalize.md`) would multiply `decay_lambda` upward, making bad knowledge fade faster than natural decay alone.

---

### Step 7 — CALIBRATE + REFLECT: update the SELF frame

After enough forecasts resolve (10+ recommended):

```python
await adapter.update_self_frame()
```

**Inside `update_self_frame()`:**

```python
async def update_self_frame(self):
    resolved = self.store.resolved()
    if len(resolved) < 10:
        return

    # Compute calibration metrics (external)
    thresholds = compute_domain_thresholds(resolved)
    global_brier = mean(r["brier_score"] for r in resolved)

    # Format as knowledge and store in elfmem
    calibration_text = f"""## Forecast Calibration ({len(resolved)} resolved)
Global Brier: {global_brier:.3f} ({quality_label(global_brier)})
Domains: {format_domain_table(thresholds, resolved)}
Systematic bias: {compute_overconfidence(resolved)}"""

    await self.memory.learn(
        calibration_text,
        tags=["self/calibration", "self/performance"],
        confidence=1.0,   # ground truth
    )
```

**What this achieves:**

The SELF frame (`tags=["self/%"]`, `token_budget=500`) automatically surfaces calibration knowledge in every future `frame("self")` call. Future forecasts are made with the agent's *actual measured accuracy* in context — not its self-estimated accuracy.

The flywheel: more forecasts → better calibration → more accurate SELF frame → better-informed future forecasts → better Brier scores → tighter edge thresholds → higher-quality trade selection.

---

## Complete Data Flow Diagram

```
Market Observation
      │
      ▼
memory.learn()          → inbox block (EPHEMERAL tier, lambda=0.05)
      │
      ▼
memory.consolidate()    → active block (embedding, confidence scored)
      │
      ▼
memory.frame("attention", query=question)
      │                 → composite scored, token-budgeted context text
      │
      ▼                 + block_ids recorded
LLM forecast(context)   → probability = 0.68
      │
      ▼
store.append(ForecastRecord)   [external]
      │
      ▼
trade execution (if edge ≥ threshold)
      │
   (time passes — outcome becomes known)
      │
      ▼
store.resolve(id, outcome=1.0)
      │
      ▼
brier_score = (0.68 - 1.0)² = 0.1024
      │
      ▼
signal = 1.0 - (brier/0.25) = 0.59
      │
      ▼
memory.outcome(block_ids, signal=0.59, source="brier")
      │
      ├── confidence updated (Bayesian Beta-Binomial)
      ├── block reinforced (signal > threshold)
      └── co-retrieval edges reinforced
      │
   (10+ resolved)
      │
      ▼
adapter.update_self_frame()
      │
      ▼
memory.learn(calibration_text, tags=["self/calibration"])
      │
      ▼
future frame("self") includes real Brier accuracy
```

---

## What elfmem Already Has vs. What Needs Building

| Capability | Status | How it works |
|---|---|---|
| Store market observations | ✅ `memory.learn()` | Tags, confidence, decay tier |
| Retrieve forecast context | ✅ `memory.frame()` | 4-stage hybrid pipeline |
| Positive outcome feedback | ✅ `memory.outcome(signal > 0.5)` | Bayesian + reinforcement |
| Neutral/negative feedback | ✅ `memory.outcome(signal ≈ 0.0)` | Confidence update, no reinforce |
| SELF frame calibration | ✅ `memory.learn(tags=["self/..."])` | Automatic in SELF frame |
| **Decay acceleration (penalize)** | **❌ Not implemented** | `decay_lambda` exists but not settable externally |
| Forecast record storage | ❌ Not elfmem's job | External `ForecastStore` |
| Brier computation | ❌ Not elfmem's job | External `analytics/brier.py` |
| Monte Carlo simulation | ❌ Not elfmem's job | External `analytics/montecarlo.py` |
| Adaptive thresholds | ❌ Not elfmem's job | External `analytics/adaptive.py` |

The only genuine gap inside elfmem for this use case is `penalize()` — the ability to accelerate decay on blocks that consistently contributed to bad forecasts. Everything else is either already present or correctly belongs to the external layer.

---

## The Brier → Signal Conversion

This conversion is the adapter's core job. It maps the Brier score's scale (lower is better) to elfmem's signal scale (higher is better):

```
signal = max(0.0, 1.0 - (brier_score / 0.25))

Brier 0.00 → signal 1.00  (perfect forecast, strong positive update)
Brier 0.05 → signal 0.80  (excellent, good update)
Brier 0.10 → signal 0.60  (good, modest positive update)
Brier 0.15 → signal 0.40  (moderate, slight negative update)
Brier 0.20 → signal 0.20  (weak, meaningful negative update)
Brier 0.25 → signal 0.00  (random/no skill, zero signal)
Brier >0.25→ signal 0.00  (clamped — don't use negative signals in outcome())
```

For Brier > 0.25 (worse than random chance), the outcome() signal is clamped to 0.0 — elfmem's Bayesian update does not accept negative signals. This is where `penalize()` fills the gap: it handles the "actively wrong" case by accelerating decay rather than trying to drive confidence below zero.

---

## File Structure for a Hello World Bot

```
my_trading_bot/
├── bot.py                    # main loop (see below)
├── elfmem_adapter.py         # ElfmemTradingAdapter — the bridge
├── forecaster.py             # adversarial LLM forecast (your code)
├── analytics/
│   ├── brier.py              # ForecastRecord, ForecastStore
│   ├── calibration.py        # CalibrationMetrics, per-domain stats
│   ├── adaptive.py           # Empirical Bayes shrinkage, thresholds
│   └── montecarlo.py         # GBM, Kelly sizing
├── forecasts.jsonl           # persisted forecast records (external to elfmem)
└── memory.db                 # elfmem's SQLite — managed automatically
```

`memory.db` and `forecasts.jsonl` are separate. elfmem manages its own database. The forecast record store is yours. The adapter bridges them at resolution time via `block_ids`.

---

## Hello World: Complete Minimal Script

```python
# bot.py — simplest possible end-to-end example
import asyncio
import uuid
from elfmem import MemorySystem
from analytics.brier import ForecastStore, ForecastRecord, compute_brier
from analytics.adaptive import compute_domain_thresholds, DEFAULT_THRESHOLD
from elfmem_adapter import ElfmemTradingAdapter


async def main():
    # ── Init ──────────────────────────────────────────────────────────────
    memory = await MemorySystem.from_config()
    store = ForecastStore("forecasts.jsonl")
    adapter = ElfmemTradingAdapter(memory, store)
    await memory.begin_session()

    # ── 1. Learn ──────────────────────────────────────────────────────────
    await adapter.learn_observation(
        "Bitcoin broke above $72k on ETF inflow day. Volume 3x average.",
        domain="crypto",
        confidence=0.85,
    )
    await memory.consolidate()   # inbox → active

    # ── 2. Forecast ───────────────────────────────────────────────────────
    question = "Will Bitcoin exceed $80,000 within 30 days?"
    recall = await memory.recall(question, tags=["crypto"])
    block_ids = [b.id for b in recall.blocks]
    context = "\n\n".join(b.content for b in recall.blocks)

    probability = 0.68   # replace: await adversarial_forecast(question, context)

    # ── 3. Check edge threshold ───────────────────────────────────────────
    resolved = store.resolved()
    threshold = compute_domain_thresholds(resolved).get("crypto", DEFAULT_THRESHOLD)
    market_price = 0.55
    edge = probability - market_price
    print(f"Edge: {edge:.0%}  Threshold: {threshold:.0%}")

    if edge < threshold:
        print("Skipping — edge below calibrated threshold")
    else:
        # ── 4. Record and (hypothetically) trade ─────────────────────────
        record = ForecastRecord(
            id=f"forecast-{uuid.uuid4().hex[:8]}",
            question=question,
            domain="crypto",
            forecast_probability=probability,
            block_ids=block_ids,
        )
        store.append(record)
        print(f"Forecast recorded: {record.id}")

        # ── 5. Later: resolve and feed back to elfmem ────────────────────
        # When outcome is known (manual or automated):
        # resolved_record = store.resolve(record.id, outcome=1.0)
        # await adapter.on_resolution(
        #     brier_score=resolved_record.brier_score,
        #     block_ids=block_ids,
        #     outcome=1.0,
        # )

        # ── 6. Calibrate SELF frame (after 10+ resolved) ─────────────────
        # await adapter.update_self_frame()

    status = await memory.status()
    print(status)
    await memory.end_session()


asyncio.run(main())
```

---

## Key Insight: Why `block_ids` Is the Critical Link

The entire feedback loop depends on recording which elfmem block IDs contributed to each forecast. Without this link, you can compute Brier scores but cannot route the outcome signal back to the specific knowledge that informed the decision.

This means:
1. Always use `memory.recall()` (not `memory.frame()`) when you need `block_ids`
2. Store `block_ids` in your `ForecastRecord` at forecast time
3. At resolution time, pass those same IDs to `adapter.on_resolution()`

The blocks that helped make a good forecast survive. The blocks that contributed to bad forecasts decay faster (once `penalize()` is implemented). This is how elfmem's knowledge base self-selects toward accuracy over time.
