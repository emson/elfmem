# Brainstorm: Domain-Agnostic Outcome Feedback for elfmem

**Date:** March 2026
**Context:** KLS trading system integration; generalisation to any agent domain
**Reference:** `docs/brainstorm_adaptive_intelligence.md`, KLS learning analytics doc

---

## 1. The Problem

elfmem stores knowledge, retrieves it, and maintains it. But it cannot learn from
**what happened after** the knowledge was used. The system has no mechanism for:

- "I used these blocks to make a forecast — the forecast was accurate"
- "I used these blocks to write code — the tests passed"
- "I used these blocks to draft a post — engagement was high"
- "I used these blocks for customer support — the user was satisfied"

The consequence: **confidence is static forever.** A block that consistently appears in
good outcomes looks identical to one that consistently appears in bad outcomes. The system
has memory but no judgement.

### What KLS revealed

KLS has Brier scores, PnL, calibration metrics — rich outcome data that could drive
block confidence updates. But KLS's gap is universal: **any agent that uses knowledge
and observes outcomes has the same gap.** The solution must not be specific to trading.

### The design constraint

elfmem must remain domain-agnostic. It cannot know about Brier scores, engagement rates,
test results, or CSAT scores. The domain-specific conversion must be the agent's
responsibility. elfmem should accept a **standardised signal** and handle the rest.

---

## 2. Design Dimensions

Five orthogonal choices define the solution space:

### Dimension A: Signal Type
What does the agent give elfmem?

| Option | Description | Example |
|--------|-------------|---------|
| A1. Binary | good/bad | `quality=True` |
| A2. Continuous [0,1] | normalised quality | `signal=0.85` |
| A3. Unbounded float | raw metric, elfmem normalises | `metric=0.06` |
| A4. Structured type | domain-specific dataclass | `BrierOutcome(score=0.06)` |

### Dimension B: Block Association
How does the agent specify which blocks the outcome applies to?

| Option | Description | Complexity |
|--------|-------------|------------|
| B1. Explicit block_ids | Agent tracks IDs from recall/frame | Low |
| B2. Retrieval ID | Auto-tracked, agent references later | Medium |
| B3. Both | Agent chooses | Medium |

### Dimension C: Confidence Update Mechanism
How does the signal change block confidence?

| Option | Description | Properties |
|--------|-------------|------------|
| C1. Direct set | Agent sets new confidence | No memory of history |
| C2. EMA | Exponential moving average | Simple, configurable decay |
| C3. Beta-Binomial | Bayesian posterior update | Principled, self-regularising |
| C4. Custom function | Agent provides update function | Maximum flexibility |

### Dimension D: API Surface
How many new methods does this require?

| Option | Description |
|--------|-------------|
| D1. Single method | `outcome(block_ids, signal)` |
| D2. Split methods | `positive_outcome()` + `negative_outcome()` |
| D3. Extended reinforce | `reinforce(block_ids, quality=0.85)` |
| D4. Plugin system | Register domain-specific handler |

### Dimension E: Persistence
How are outcomes stored?

| Option | Description | Auditability |
|--------|-------------|-------------|
| E1. In-place only | Update confidence, no history | None |
| E2. Audit log only | Separate table, reconstruct on read | Full |
| E3. Both | Update + log | Full + fast reads |

---

## 3. Alternatives

### Alternative 1: Domain Plugin System

Each domain implements a `DomainPlugin` that converts domain outcomes to block updates.

```python
class TradingPlugin(OutcomePlugin):
    def convert(self, outcome: Any) -> list[BlockSignal]:
        return [BlockSignal(block_id=b, signal=1.0 - outcome.brier_score)
                for b in outcome.block_ids]

system = MemorySystem.from_config("db", outcome_plugin=TradingPlugin())
```

**Evaluation:**
- Simplicity: ❌ Requires class per domain
- Generalisability: ❌ Plugin must exist before use
- Elegance: ❌ Heavy OOP pattern for a simple concept
- Auditability: ⚠️ Depends on plugin
- Composability: ❌ One plugin per system
- Decoupling: ❌ elfmem imports domain types

**Verdict:** Over-engineered. Violates elfmem's "zero infrastructure" philosophy.

---

### Alternative 2: Raw Signal (Minimal)

Extend `reinforce_blocks()` with a quality parameter.

```python
await system.reinforce(["abc", "def"], quality=0.85)
await system.reinforce(["ghi"], quality=0.15)  # negative signal
```

No new method, no new type, no new table.

**Evaluation:**
- Simplicity: ✅ Zero new concepts
- Generalisability: ✅ Domain-agnostic
- Elegance: ❌ Conflates reinforcement with feedback (different semantics)
- Auditability: ❌ No history of outcomes
- Composability: ✅ Call many times
- Decoupling: ✅ elfmem knows nothing about domain

**Verdict:** Too minimal. Loses the ability to distinguish "block was accessed"
(reinforcement) from "block contributed to a good outcome" (feedback). These are
different signals and should remain distinct.

---

### Alternative 3: Structured Outcome Records

Define a flexible `Outcome` type with metadata.

```python
@dataclass
class Outcome:
    block_ids: list[str]
    signal: float                   # 0.0–1.0
    metadata: dict[str, Any]        # domain context
    outcome_type: str               # "forecast", "code", "content"

await system.record_outcome(Outcome(
    block_ids=["abc"], signal=0.85,
    metadata={"brier_score": 0.06, "pnl": 1250.0},
    outcome_type="forecast",
))
```

**Evaluation:**
- Simplicity: ⚠️ New type, but straightforward
- Generalisability: ✅ metadata carries anything
- Elegance: ⚠️ `outcome_type` is stringly-typed and not used internally
- Auditability: ✅ Full metadata preserved
- Composability: ✅ Multiple outcomes per block
- Decoupling: ✅ metadata is opaque to elfmem

**Verdict:** Good but noisy. The metadata and outcome_type are architecturally dead —
elfmem stores them but never uses them. They exist only for external audit. This is
a smell: if elfmem doesn't need it, it shouldn't require it.

---

### Alternative 4: The "Observe" Pattern (RL-inspired)

From reinforcement learning: the agent observes a reward after taking an action.

```python
await system.observe(
    block_ids=["abc", "def"],
    reward=0.85,
    context="Forecast resolved correctly, Brier=0.06",
)
```

**Evaluation:**
- Simplicity: ✅ Three arguments
- Generalisability: ✅ Reward is universal
- Elegance: ⚠️ "reward" implies RL framing that may confuse non-ML users
- Auditability: ⚠️ context is unstructured string
- Composability: ✅ Call per outcome
- Decoupling: ✅ elfmem knows nothing about domain

**Verdict:** Clean but the RL terminology ("reward", "observe") doesn't match
elfmem's vocabulary (learn, recall, frame, consolidate, curate). An agent-first
library should use natural language, not ML jargon.

---

### Alternative 5: The "Judge" Pattern (Callback)

elfmem provides a hook that the domain fills in.

```python
class ForecastJudge(OutcomeJudge):
    async def evaluate(self, blocks: list[ScoredBlock], raw_outcome: Any) -> float:
        return 1.0 - raw_outcome.brier_score

system = MemorySystem.from_config("db", judge=ForecastJudge())
await system.outcome(block_ids=["abc"], raw_outcome=forecast_result)
```

**Evaluation:**
- Simplicity: ❌ Requires class implementation
- Generalisability: ✅ Arbitrary conversion logic
- Elegance: ❌ Callback indirection hides the logic
- Auditability: ⚠️ Depends on judge
- Composability: ❌ One judge per system
- Decoupling: ❌ elfmem calls domain-specific code

**Verdict:** Wrong direction of dependency. elfmem should receive a signal, not call
out to domain code. The agent normalises; elfmem consumes.

---

### Alternative 6: The "outcome()" Pattern (Recommended)

elfmem exposes a single `outcome()` method that accepts a normalised signal. The
agent is responsible for converting domain metrics to [0.0, 1.0]. elfmem handles
Bayesian updating, reinforcement, edge learning, and audit logging internally.

```python
# Trading agent:
signal = 1.0 - brier_score  # 0.06 Brier → 0.94 signal
await system.outcome(block_ids, signal=0.94, source="brier")

# Coding agent:
signal = 1.0 if all_tests_passed else 0.0
await system.outcome(block_ids, signal=signal, source="tests")

# Writing agent:
signal = min(engagement_rate / baseline_engagement, 1.0)
await system.outcome(block_ids, signal=signal, source="engagement")

# Customer support agent:
signal = (csat_score - 1.0) / 4.0  # normalise 1-5 to 0-1
await system.outcome(block_ids, signal=signal, source="csat")
```

**Evaluation:**
- Simplicity: ✅ Two required args (block_ids, signal)
- Generalisability: ✅ Any domain can normalise to [0, 1]
- Elegance: ✅ Fits existing vocabulary perfectly
- Auditability: ✅ source label + per-outcome log table
- Composability: ✅ Multiple calls, different sources, accumulate
- Decoupling: ✅ elfmem never imports domain code

**Verdict: Winner.** Minimal API surface, maximum generalisability. The domain
conversion is a one-liner that lives in the agent's code, not in elfmem.

---

## 4. Evaluation Matrix

| Criterion | Plugin | Minimal | Structured | Observe | Judge | **outcome()** |
|-----------|--------|---------|-----------|---------|-------|-----------|
| Simplicity | ❌ | ✅ | ⚠️ | ✅ | ❌ | **✅** |
| Generalisability | ❌ | ✅ | ✅ | ✅ | ✅ | **✅** |
| Elegance | ❌ | ❌ | ⚠️ | ⚠️ | ❌ | **✅** |
| Auditability | ⚠️ | ❌ | ✅ | ⚠️ | ⚠️ | **✅** |
| Composability | ❌ | ✅ | ✅ | ✅ | ❌ | **✅** |
| Decoupling | ❌ | ✅ | ✅ | ✅ | ❌ | **✅** |

---

## 5. Recommended API Design

### Method Signature

```python
async def outcome(
    self,
    block_ids: list[str],
    signal: float,
    *,
    weight: float = 1.0,
    source: str = "",
) -> OutcomeResult:
```

### Naming Rationale

The method name `outcome()` completes the knowledge lifecycle:

```
learn()       → tell the system new knowledge
recall()      → ask for knowledge
frame()       → ask for rendered context
outcome()     → tell the system how the knowledge performed
consolidate() → process and promote knowledge
curate()      → maintain knowledge health
```

The cycle: **learn → recall → use → outcome → (knowledge improves) → recall again**

Other names considered and rejected:
- `feedback()` — too generic, overloaded in Python (print feedback, user feedback, etc.)
- `observe()` — RL jargon, doesn't match elfmem's natural-language vocabulary
- `appraise()` — implies subjective valuation rather than empirical evidence
- `calibrate()` — too domain-specific (implies statistical calibration)
- `signal()` — too abstract, could be confused with OS signals
- `evaluate()` — ambiguous (evaluate the blocks? evaluate the outcome?)

### Parameters

**`block_ids: list[str]`** (required)
Which blocks were used in the action whose outcome you are reporting. Extracted from
`frame().blocks` or `recall()` results. The agent tracks these.

**`signal: float`** (required)
Quality of the outcome, normalised to [0.0, 1.0]:
- `0.0` = terrible outcome (blocks were harmful or wrong)
- `0.5` = neutral (no evidence either way)
- `1.0` = excellent outcome (blocks were very helpful)

The agent is responsible for normalising domain metrics to this range.

**`weight: float = 1.0`** (optional)
Evidence strength. Controls how much this single outcome moves the Bayesian prior.
- `< 1.0` — noisy or uncertain signal (anecdotal evidence, small sample)
- `= 1.0` — standard observation (default)
- `> 1.0` — highly reliable signal (large sample, validated data)

**`source: str = ""`** (optional)
Label for audit trail. Examples: `"brier"`, `"tests"`, `"engagement"`, `"csat"`.
elfmem does not interpret this string — it is stored for external analytics only.

### Return Type

```python
@dataclass(frozen=True)
class OutcomeResult:
    """Result of reporting an outcome for retrieved blocks."""
    blocks_updated: int
    mean_confidence_delta: float  # average change (can be negative)
    edges_reinforced: int

    @property
    def summary(self) -> str:
        direction = "+" if self.mean_confidence_delta >= 0 else ""
        parts = [
            f"{self.blocks_updated} blocks updated "
            f"({direction}{self.mean_confidence_delta:.3f} avg confidence)"
        ]
        if self.edges_reinforced > 0:
            parts.append(f"{self.edges_reinforced} edges reinforced")
        return "Outcome recorded: " + ", ".join(parts) + "."

    def __str__(self) -> str:
        return self.summary

    def to_dict(self) -> dict[str, Any]:
        return {
            "blocks_updated": self.blocks_updated,
            "mean_confidence_delta": self.mean_confidence_delta,
            "edges_reinforced": self.edges_reinforced,
        }
```

Example output: `"Outcome recorded: 3 blocks updated (+0.042 avg confidence), 2 edges reinforced."`

### Guide Entry

```python
AgentGuide(
    name="outcome",
    what="Report how well retrieved blocks performed in practice.",
    when="You have evidence about whether recalled knowledge was useful — a forecast "
         "resolved, tests passed, content performed well, a decision worked out.",
    when_not="Adding new knowledge (use learn()). Boosting blocks without measured "
             "evidence (use frame(), which auto-reinforces on retrieval).",
    cost="Fast. Database writes only; no LLM calls.",
    returns="OutcomeResult — blocks_updated, mean_confidence_delta, edges_reinforced. "
            "Future recall() and frame() will rank updated blocks differently.",
    next="No immediate action needed. Evidence accumulates across multiple outcomes. "
         "After 10+ outcomes per block, confidence converges toward true quality.",
    example='result = await system.outcome(["abc123", "def456"], signal=0.85, source="brier")',
)
```

### Docstring (Agent-Facing)

```python
async def outcome(
    self,
    block_ids: list[str],
    signal: float,
    *,
    weight: float = 1.0,
    source: str = "",
) -> OutcomeResult:
    """Report the outcome of using retrieved blocks.

    USE WHEN: You have evidence about whether recalled knowledge was useful.
    A forecast resolved, tests passed, content performed well, a user was
    satisfied. The signal drives Bayesian confidence updates — blocks that
    consistently appear in good outcomes gain confidence; blocks in bad
    outcomes lose it.

    DON'T USE WHEN: You want to add new knowledge (use learn()). You want
    to boost a block without measured evidence (use frame() — it auto-
    reinforces on retrieval).

    COST: Fast. Database writes only; no LLM calls.

    RETURNS: OutcomeResult with blocks_updated, mean_confidence_delta,
    and edges_reinforced. Future recall() and frame() will rank these
    blocks differently based on accumulated evidence.

    NEXT: Evidence accumulates. After 10+ outcomes per block, confidence
    converges toward true quality. No immediate follow-up needed.

    Args:
        block_ids: Block IDs that were used in the action whose outcome
            you are reporting. Get these from frame().blocks or recall().
        signal: Quality of outcome, normalised to [0.0, 1.0]:
            0.0 = terrible outcome (blocks were harmful/wrong)
            0.5 = neutral (no evidence either way)
            1.0 = excellent outcome (blocks were very helpful)
        weight: Evidence strength (default 1.0). Use < 1.0 for noisy
            signals, > 1.0 for highly reliable outcomes.
        source: Label for audit trail (e.g. "brier", "tests", "engagement").

    Domain conversion examples::

        # Trading (Brier score: 0=perfect, 1=worst):
        signal = 1.0 - brier_score

        # Coding (tests: binary pass/fail):
        signal = 1.0 if all_tests_passed else 0.0

        # Social media (engagement rate vs baseline):
        signal = min(engagement_rate / baseline, 1.0)

        # Customer support (CSAT 1-5 scale):
        signal = (csat_score - 1.0) / 4.0
    """
```

---

## 6. Internal Mechanics: Bayesian Confidence Update

### Why Beta-Binomial?

The block's true quality can be modelled as a Bernoulli process: each time the block
is used, it either contributes to a good outcome (success) or a bad one (failure).
The Beta distribution is the conjugate prior for this process, making updates trivial:

```
confidence ~ Beta(α, β)
E[confidence] = α / (α + β)
```

### The Prior

The initial confidence comes from the LLM alignment score at consolidation time.
We encode this as a weak Beta prior:

```python
OUTCOME_PRIOR_STRENGTH = 2.0  # prior "pseudo-observations"

α₀ = initial_confidence × PRIOR_STRENGTH   # e.g. 0.65 × 2.0 = 1.30
β₀ = (1 − initial_confidence) × PRIOR_STRENGTH  # 0.35 × 2.0 = 0.70
```

With `PRIOR_STRENGTH = 2.0`, the initial LLM alignment has the weight of two
observations. By the 10th outcome, evidence overwhelms the prior.

### The Update

Each outcome provides a continuous signal in [0.0, 1.0], weighted by `weight`:

```python
α_new = α + signal × weight
β_new = β + (1 − signal) × weight
new_confidence = α_new / (α_new + β_new)
```

### Convergence Examples

Starting from `confidence = 0.65` (`PRIOR_STRENGTH = 2.0`):

| After N outcomes | All signal=1.0 | All signal=0.0 | Mixed (0.7 avg) |
|-----------------|----------------|----------------|-----------------|
| 0 | 0.650 | 0.650 | 0.650 |
| 1 | 0.767 | 0.433 | 0.700 |
| 5 | 0.900 | 0.186 | 0.750 |
| 10 | 0.942 | 0.108 | 0.775 |
| 25 | 0.974 | 0.048 | 0.796 |
| 50 | 0.987 | 0.025 | 0.808 |

Properties:
- **Self-regularising:** Few observations → prior dominates; many → evidence dominates
- **Monotonically convergent:** Consistent signals drive confidence toward 0.0 or 1.0
- **Symmetric:** Good and bad evidence are treated equally
- **Smooth:** No discontinuous jumps from a single observation

### State Tracking

To compute the update, elfmem needs to derive current α and β from stored state.
Two columns suffice:

```python
# Stored on each block:
confidence: float       # current E[Beta(α, β)]
outcome_count: int = 0  # number of outcomes recorded

# Derived at update time:
total_evidence = PRIOR_STRENGTH + outcome_count
α = confidence × total_evidence
β = (1 − confidence) × total_evidence

# After update:
α += signal × weight
β += (1 − signal) × weight
new_confidence = α / (α + β)
new_outcome_count = outcome_count + 1
```

### Side Effects

Beyond the confidence update, `outcome()` triggers:

1. **Reinforcement** (signal > 0.5): Blocks with positive outcomes are reinforced —
   `last_reinforced_at` and `reinforcement_count` updated. This protects them from
   decay. Blocks with negative signals are NOT anti-reinforced; they simply miss
   the reinforcement and decay naturally.

2. **Hebbian edge learning** (signal > 0.5): Co-used blocks with good outcomes have
   their edges reinforced via `reinforce_co_retrieved_edges()`. Blocks that succeed
   together wire together.

3. **Audit logging**: Each outcome is recorded in a `block_outcomes` table with
   block_id, signal, weight, source, and timestamp.

---

## 7. Domain Conversion Examples

The normalisation from domain metrics to signal ∈ [0.0, 1.0] is always the agent's
responsibility. This one-liner lives in the agent's code, not in elfmem.

### Trading (Brier Score)

```python
# Brier: 0.0 = perfect, 1.0 = worst
signal = 1.0 - brier_score

# Examples:
# Brier 0.02 (excellent) → signal 0.98
# Brier 0.10 (good)      → signal 0.90
# Brier 0.25 (random)    → signal 0.75
# Brier 0.81 (terrible)  → signal 0.19
```

### Trading (PnL-based)

```python
# Normalise PnL relative to position size
signal = sigmoid(pnl / position_size)  # maps (-∞, +∞) to (0, 1)

# Or simpler: binary win/loss
signal = 1.0 if pnl > 0 else 0.0
```

### Coding (Test Results)

```python
# Binary: all pass or not
signal = 1.0 if all_tests_passed else 0.0

# Graded: fraction of tests passing
signal = tests_passed / total_tests

# With severity: weighted by test importance
signal = sum(w * passed for w, passed in weighted_tests) / sum(w for w, _ in weighted_tests)
```

### Social Media (Engagement)

```python
# Relative to historical baseline
signal = min(engagement_rate / baseline_engagement, 1.0)

# Or log-scaled for viral content
signal = min(log(1 + engagement) / log(1 + baseline_engagement), 1.0)
```

### Customer Support (CSAT)

```python
# Normalise 1-5 scale to 0-1
signal = (csat_score - 1.0) / 4.0

# Or binary: satisfied (4-5) vs not (1-3)
signal = 1.0 if csat_score >= 4 else 0.0
```

### Research (Citation / Peer Review)

```python
# Binary: accepted or rejected
signal = 1.0 if paper_accepted else 0.0

# Graded: review score normalised
signal = (review_score - min_score) / (max_score - min_score)
```

### General Agent (LLM Self-Assessment)

```python
# Ask the LLM: "Were these recalled facts useful for your response?"
# Parse structured output → score ∈ [0, 1]
signal = llm_usefulness_score
```

---

## 8. Schema Changes

### Blocks table — two new columns

```sql
ALTER TABLE blocks ADD COLUMN outcome_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE blocks ADD COLUMN initial_confidence FLOAT;
-- initial_confidence is set once at consolidation, never updated.
-- Used to reconstruct the prior for Bayesian updates.
```

Actually, `initial_confidence` is not needed if we track `outcome_count` — we can
derive α and β from `confidence` + `outcome_count` + `PRIOR_STRENGTH` at update time.

**Minimal change: one new column.**

```sql
ALTER TABLE blocks ADD COLUMN outcome_count INTEGER NOT NULL DEFAULT 0;
```

### New table: block_outcomes (audit log)

```sql
CREATE TABLE block_outcomes (
    id TEXT PRIMARY KEY,
    block_id TEXT NOT NULL REFERENCES blocks(id),
    signal REAL NOT NULL,
    weight REAL NOT NULL DEFAULT 1.0,
    source TEXT NOT NULL DEFAULT '',
    confidence_before REAL NOT NULL,
    confidence_after REAL NOT NULL,
    created_at TEXT NOT NULL
);
```

This is an append-only log. elfmem writes to it but never reads from it during
normal operations. It exists for external analytics (e.g., "show me the confidence
trajectory of block X over time").

---

## 9. Configuration

### New config keys in MemoryConfig

```python
class MemoryConfig(BaseModel):
    # ... existing fields ...

    # Outcome feedback
    outcome_prior_strength: float = 2.0
    # Weight of the initial LLM alignment prior. Higher = more outcomes needed
    # to override the alignment score. Lower = outcomes dominate quickly.
    # 2.0 means the initial alignment has the weight of 2 observations.

    outcome_reinforce_threshold: float = 0.5
    # Minimum signal to trigger block reinforcement (recency protection).
    # Blocks with signal below this are not reinforced (decay naturally).
```

### Why configurable?

Different agents have different trust levels:
- **High-trust alignment** (agent has excellent self-model): `prior_strength = 5.0`
  → More outcomes needed to override the alignment score
- **Low-trust alignment** (generic agent, weak self-model): `prior_strength = 1.0`
  → Outcomes dominate quickly
- **Conservative reinforcement** (don't protect questionable blocks): `threshold = 0.7`
- **Aggressive reinforcement** (protect any non-terrible block): `threshold = 0.3`

---

## 10. Integration with Existing Operations

### How outcome() affects retrieval

The `confidence` component in the composite score is weighted differently per frame:

```
SELF_WEIGHTS.confidence      = 0.30  (30% of self-frame score)
ATTENTION_WEIGHTS.confidence = 0.15  (15% of attention-frame score)
TASK_WEIGHTS.confidence      = 0.20  (20% of task-frame score)
```

A block whose confidence moves from 0.65 → 0.90 (after 10 good outcomes) gains:
- Self frame: +0.075 composite score (0.25 × 0.30)
- Attention frame: +0.0375 composite score (0.25 × 0.15)
- Task frame: +0.050 composite score (0.25 × 0.20)

This is significant. In a dense corpus where many blocks score similarly, a 0.05
composite score difference can move a block from position #8 to position #3.

### How outcome() affects curate()

Blocks with low confidence (after negative outcomes) are more likely to be archived:
- Lower confidence → lower composite score in `_reinforce_top_blocks()`
- Not reinforced by outcomes → faster natural decay
- Bridge protection still applies (high-degree blocks survive regardless)

### How outcome() interacts with consolidate()

No interaction. `consolidate()` sets the **initial** confidence from LLM alignment.
`outcome()` updates confidence from **observed evidence**. They are sequential:

```
learn() → consolidate() [sets initial confidence = alignment score]
    ↓
frame()/recall() → agent uses blocks → outcome observed
    ↓
outcome() [updates confidence from evidence, respecting the alignment prior]
```

### Session awareness

`outcome()` does NOT require an active session. Outcomes may arrive hours or weeks
after the blocks were retrieved (e.g., a trading forecast resolving after 30 days).
The method writes directly to the database like `learn()`.

If a session IS active, `outcome()` uses `current_active_hours` for reinforcement
timestamps. If no session is active, it reads `total_active_hours` from the database.

---

## 11. The Complete Knowledge Lifecycle

With `outcome()`, elfmem's lifecycle becomes a closed loop:

```
┌─────────────────────────────────────────────────────────────┐
│                                                             │
│   learn("Bitcoin ETF approval likely by Q2")                │
│       ↓                                                     │
│   consolidate()                                             │
│       → confidence = 0.65 (from LLM alignment)              │
│       → embedded, tagged, edges created                     │
│       ↓                                                     │
│   frame("attention", query="crypto forecast")               │
│       → block returned in position #4                       │
│       → agent records block_ids                             │
│       ↓                                                     │
│   [agent makes forecast, outcome resolves weeks later]      │
│       ↓                                                     │
│   outcome(block_ids, signal=0.94, source="brier")           │
│       → confidence: 0.65 → 0.77 (Bayesian update)          │
│       → block reinforced (protected from decay)             │
│       → edges to co-used blocks strengthened                │
│       ↓                                                     │
│   frame("attention", query="crypto forecast")               │
│       → same block now returned in position #2              │
│       → better context → better forecast                    │
│       ↓                                                     │
│   [cycle repeats — knowledge improves with each outcome]    │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

---

## 12. What This Does NOT Do

1. **Does not determine what a "good outcome" is.** The agent normalises domain
   metrics to [0, 1]. elfmem has no opinion on what constitutes success.

2. **Does not track which blocks *caused* good outcomes.** Correlation is not
   causation. A block retrieved alongside 4 others gets the same signal as the
   block that actually drove the decision. This is a known limitation. Possible
   future extension: per-block attribution weights.

3. **Does not integrate Monte Carlo simulation.** MC is for position sizing (risk
   management), not knowledge quality. It operates at a different layer and should
   remain separate.

4. **Does not replace domain-specific analytics.** KLS should still compute Brier
   scores, calibration curves, and adaptive thresholds. elfmem receives the
   normalised signal from those computations.

5. **Does not automatically re-calibrate.** The agent decides when to call
   `outcome()`. elfmem does not poll for outcomes or trigger re-calibration.

---

## 13. Future Extensions (Not Phase 1)

### Per-block attribution weights

```python
await system.outcome(
    block_ids=["a", "b", "c"],
    signal=0.85,
    attribution={"a": 0.6, "b": 0.3, "c": 0.1},  # block A was most influential
)
```

The signal would be scaled per block: block A gets `0.85 × 0.6 = 0.51` effective
signal, block C gets `0.85 × 0.1 = 0.085`. This requires the agent to assess which
blocks were actually useful, which is a harder problem.

### Multi-dimensional traits

```python
system.define_trait("accuracy", description="Forecast accuracy")
system.define_trait("actionability", description="Led to concrete action")

await system.outcome(block_ids, traits={"accuracy": 0.9, "actionability": 0.3})
```

Per-trait confidence allows different scoring for different contexts. A block might be
accurate but not actionable. This requires schema changes (per-trait confidence columns
or a normalised trait_scores table) and is complex.

### Counterfactual analysis

"What would have happened if this block had NOT been in the context?" Requires running
the same query with the block excluded and comparing outcomes. Expensive (LLM cost)
but would provide true causal attribution.

### Temporal pattern detection

Track whether certain blocks are consistently useful at specific times (e.g., "macro
blocks are useful at month-end when reports drop"). Could use the outcome log to detect
periodic patterns and boost relevant blocks preemptively.

---

## 14. Implementation Path

### Phase 1: Foundation
1. Add `outcome_count` column to blocks table
2. Add `block_outcomes` audit table
3. Add `OutcomeResult` to `types.py`
4. Add `OUTCOME_PRIOR_STRENGTH` to config
5. Implement `outcome()` on `MemorySystem`
6. Add guide entry
7. Write tests

### Phase 2: Refinements
8. Add `outcome_reinforce_threshold` to config
9. Dashboard/analytics for block confidence trajectories
10. Batch `outcomes()` method for bulk processing

### Phase 3: Advanced
11. Per-block attribution weights
12. Multi-dimensional traits
13. Counterfactual analysis hooks

---

## 15. Summary

**The recommended solution is a single `outcome()` method** that accepts a normalised
signal in [0.0, 1.0] and updates block confidence via Bayesian Beta-Binomial updating.

The agent converts domain-specific metrics (Brier scores, test results, engagement
rates, CSAT scores) to the normalised signal. elfmem handles the rest: Bayesian
updating, reinforcement, edge learning, and audit logging.

This design is:
- **Simple:** Two required parameters (block_ids, signal)
- **General:** Works for trading, coding, writing, support, research, or any domain
- **Principled:** Bayesian updating with configurable prior strength
- **Auditable:** Full outcome log for external analytics
- **Composable:** Multiple sources, multiple calls, evidence accumulates naturally
- **Elegant:** Completes the learn → recall → use → outcome lifecycle
