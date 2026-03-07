# Plan: Constitutional Cognitive Loop

## Overview

Establish a self-sustaining cognitive loop as elfmem's default SELF seed — a set of
10 constitutional blocks that ship with every instance, providing foundational principles
that guide the agent's learning, decision-making, and self-improvement across any domain.

**Core insight from code review:** The system already has most of the mechanics built.
This plan is primarily about *seeding the right knowledge* and *connecting one missing wire*
in curate(). The architecture was designed for this; we just need to use it.

---

## System Mechanics (What Already Exists)

### 1. Constitutional Decay Tier — ALREADY IMPLEMENTED

**File:** `src/elfmem/memory/blocks.py:20-37`

```python
def determine_decay_tier(tags: list[str], category: str) -> DecayTier:
    tag_set = set(tags)
    if "self/constitutional" in tag_set:
        return DecayTier.PERMANENT          # <-- lambda = 0.00001
    durable_tags = {"self/value", "self/constraint", "self/goal"}
    if tag_set & durable_tags:
        return DecayTier.DURABLE            # <-- lambda = 0.001
    ...
```

Blocks tagged `self/constitutional` get PERMANENT decay (lambda=0.00001). Half-life in
active hours: ~69,315 hours = ~34 years of continuous use. They will NOT be archived
by curate() for decades. This is already coded and tested.

### 2. Guarantee Enforcement — ALREADY IMPLEMENTED

**File:** `src/elfmem/context/frames.py:45-57`

```python
SELF_FRAME = FrameDefinition(
    name="self",
    weights=SELF_WEIGHTS,
    filters=FrameFilters(tag_patterns=["self/%"]),
    guarantees=["self/constitutional"],       # <-- constitutional blocks guaranteed
    template="self",
    token_budget=600,
    ...
)
```

**File:** `src/elfmem/operations/recall.py:112-139`

When `recall(frame="self")` is called, `_enforce_guarantees()` pre-allocates slots for
blocks matching `self/constitutional` tag pattern BEFORE filling remaining slots with
highest-scoring candidates. Constitutional blocks are always in the SELF frame result,
regardless of their composite score.

### 3. Retrieval Reinforcement — ALREADY IMPLEMENTED

**File:** `src/elfmem/operations/recall.py:99-103`

```python
if final_blocks:
    returned_ids = [b.id for b in final_blocks]
    await queries.reinforce_blocks(conn, returned_ids, current_active_hours)
    await reinforce_co_retrieved_edges(conn, returned_ids)
```

Every block returned by recall() is reinforced. Since constitutional blocks are
GUARANTEED in every SELF frame recall, they are reinforced every time. This creates
a virtuous cycle: guaranteed inclusion → reinforcement → high score → even more resilient.

### 4. Session-Aware Decay — ALREADY IMPLEMENTED

**File:** `src/elfmem/scoring.py:118-125`

Decay uses active session hours, not wall-clock time. The agent can go on holiday
for weeks; constitutional blocks don't decay during idle time. This is exactly right
for a knowledge system that might be used intermittently.

### 5. Three-Tier Decay Model — ALREADY IMPLEMENTED

**File:** `src/elfmem/scoring.py:16-21`

```python
LAMBDA: dict[DecayTier, float] = {
    DecayTier.PERMANENT:  0.00001,   # self/constitutional — ~34 year half-life
    DecayTier.DURABLE:    0.001,     # self/value — ~693 active hours (~29 days)
    DecayTier.STANDARD:   0.010,     # normal blocks — ~69 active hours (~3 days)
    DecayTier.EPHEMERAL:  0.050,     # observations — ~14 active hours
}
```

This already maps to our three-tier model:
- **Constitutional** (PERMANENT): founding principles, near-immortal
- **Values** (DURABLE): learned principles, must be reinforced monthly
- **Standard**: domain knowledge, must be reinforced every few days

### Summary: What's Already Built

| Mechanic | Status | File |
|----------|--------|------|
| `self/constitutional` → PERMANENT decay | Done | `blocks.py:30-31` |
| SELF frame guarantees constitutional | Done | `frames.py:49` |
| `_enforce_guarantees()` pre-allocates slots | Done | `recall.py:112-139` |
| Retrieval-based reinforcement | Done | `recall.py:99-103` |
| Session-aware decay (not wall-clock) | Done | `scoring.py:118-125` |
| Three-tier decay rates (PERMANENT/DURABLE/STANDARD/EPHEMERAL) | Done | `scoring.py:16-21` |
| Curate top-N reinforcement | Done | `curate.py:133-174` |
| Bridge protection (connected blocks resist archival) | Done | `curate.py:80-124` |
| MMR diversity (prevents monoculture in retrieval) | Done | `retrieval.py:231-286` |
| Exploration 007 constitutional design spec | Done | `sim/explorations/007_constitutional_self.md` |

---

## What's Missing (The Gap)

### Gap 1: No Constitutional Seed Blocks Exist

The system supports constitutional blocks perfectly, but none have been created.
Every new elfmem instance starts with an empty SELF frame.

### Gap 2: Curate Does Not Auto-Reinforce Constitutional Blocks

`curate()` reinforces the top-5 blocks by composite score. This usually includes
constitutional blocks (they score high on confidence + reinforcement), but it's not
guaranteed. If the corpus grows large with many highly-connected domain blocks,
constitutional blocks could be displaced from the top-5.

**Risk:** If the agent never calls `recall(frame="self")` (e.g., only uses ATTENTION
frame), constitutional blocks get no retrieval-based reinforcement AND no curate-based
reinforcement. Their recency decays (slowly, but still).

**Mitigation:** Add constitutional auto-reinforcement to curate() as a belt-and-suspenders
measure. One addition: before the existing top-N reinforcement, reinforce all
`self/constitutional` blocks. This is ~5 lines of code.

### Gap 3: `elfmem init` / `elfmem_setup` Don't Use Constitutional Tags

The current `elfmem init --self` tags with `self/context`. The current `elfmem_setup`
MCP tool tags identity as `self/context` and values as `self/value`. Neither uses
`self/constitutional`. Seed blocks should be constitutional.

### Gap 4: No Default Seed Content

There is no standard SELF seed shipped with elfmem. Each instance requires manual
seeding. For the cognitive loop to be universal, we need a default seed that can be
applied automatically during `elfmem init`.

---

## The Cognitive Loop

### The 10 Constitutional Blocks

```
 CURIOSITY ──> CONNECTION ──> UNCERTAINTY
     ^                             |
     |                             v
 REFLECTION    IDENTITY        ECONOMY
     ^                             |
     |                             v
  BALANCE <──── CARE <────── FEEDBACK
                               |
                               v
                          FOCUS (control)
```

The loop is not sequential — it's a menu of cognitive tools. The retrieval system
surfaces the right principles for the current context. Different tasks activate
different subsets:

| Task context | Primary blocks retrieved |
|---|---|
| Encountering unknowns | Curiosity, Uncertainty |
| Learning something new | Connection, Feedback |
| Making decisions | Economy, Focus, Uncertainty |
| Reviewing outcomes | Feedback, Care, Balance |
| Pacing/planning | Balance, Economy |
| New domain entry | Curiosity, Uncertainty, Connection |
| Session transitions | Reflection Protocol |

### Why the Loop Is Self-Sustaining

**1. Constitutional blocks are guaranteed in SELF frame** (recall.py:76-81).
They cannot be displaced by domain knowledge.

**2. Constitutional blocks get reinforced on every SELF retrieval** (recall.py:99-103).
Because they're guaranteed, every `recall(frame="self")` reinforces them.

**3. PERMANENT decay is effectively immortal** (scoring.py:17).
Even without reinforcement, constitutional blocks survive 34+ years of active use.

**4. The Reflection Protocol triggers maintenance.** Block 10 tells the agent to
review its principles at natural transitions. This is the only block that explicitly
references the self-improvement process.

**5. Domain knowledge builds on top, never displaces.** Learned `self/value` blocks
compete for the non-guaranteed slots in SELF frame retrieval. Constitutional blocks
have reserved seats; everything else competes by relevance.

### How Domain Calibration Works

```
SELF FRAME RETRIEVAL (top_k=5)
├── GUARANTEED SLOTS (constitutional blocks matching query)
│   ├── Identity (always relevant)
│   ├── Curiosity (if exploring)
│   ├── Feedback (if learning)
│   └── [whichever constitutionals score highest for this query]
│
└── COMPETITIVE SLOTS (remaining capacity)
    ├── self/value: "Cut losses early in momentum trades" (if trading)
    ├── self/value: "Progressive overload requires deload weeks" (if fitness)
    └── self/value: "Test in isolation before integrating" (if coding)
```

**The flow over time:**

```
Week 1:  elfmem init → 10 constitutional blocks (all guaranteed)
         SELF retrieval returns: all constitutional (nothing else exists)

Week 4:  Agent learns 5 domain-specific principles (self/value, DURABLE decay)
         SELF retrieval returns: ~3 constitutional + ~2 domain-relevant values

Week 12: Agent has 20 self/* blocks across 3 domains
         SELF retrieval returns: ~3 constitutional + ~2 most relevant to query
         Constitutional blocks always present. Domain values rotate by relevance.

Week 52: Agent has 50 self/* blocks across 5 domains
         SELF retrieval still returns top-5 (guaranteed + best fit)
         Unused domain values naturally decay (DURABLE: ~29 day half-life)
         Constitutional blocks untouched (PERMANENT)
```

### Self-Improvement Lifecycle

The cognitive loop improves the agent through three mechanisms:

**Mechanism 1: Value Discovery**
```
Agent works in trading domain
  → Curiosity block surfaces: "form a hypothesis, test it"
  → Agent hypothesises a trading principle
  → Agent tests it → outcome signal recorded
  → Feedback block surfaces: "encode patterns as self-knowledge"
  → Agent calls remember("principle X", tags=["self/value"])
  → New DURABLE block enters the SELF frame
  → On subsequent trading queries, this block competes for non-guaranteed slots
```

**Mechanism 2: Value Natural Selection**
```
Agent has 15 self/value blocks across domains
  → curate() runs periodically
  → Top-5 blocks by composite score get reinforced
  → Blocks that are frequently retrieved score high (reinforcement_count)
  → Blocks that are rarely retrieved decay (DURABLE: ~29 day half-life)
  → After 2 months, only useful principles survive
  → The SELF becomes a distillation of what actually works
```

**Mechanism 3: Reflection-Driven Pruning**
```
Agent hits session transition
  → Reflection Protocol block surfaces: "which principles did I neglect?"
  → Agent reviews its recent work against SELF principles
  → Notices it hasn't used "Balance" — recalibrates intensity
  → Records outcome on underperforming belief → accelerated decay
  → This is the HUMAN-LIKE mechanism: self-awareness corrects drift
```

### Edge Cases

#### Edge Case 1: Constitutional blocks crowd out domain knowledge

**Scenario:** 10 constitutional blocks, top_k=5. All 5 slots taken by constitutional.

**Why this won't happen:** `_enforce_guarantees()` only includes constitutional blocks
that are in the candidate set (line 133: `[b for b in candidates if b.id in guaranteed_ids]`).
The candidate set comes from hybrid retrieval, which scores by similarity to the query.
For a domain-specific query like "EUR/USD position sizing," most constitutional blocks
will have low similarity and won't appear in candidates. Only the 1-2 most relevant
constitutional blocks will be guaranteed; remaining slots go to domain knowledge.

For queryless SELF retrieval (no query), all constitutional blocks are candidates.
But weights are renormalized without similarity (recall.py:53-54), so confidence,
centrality, and reinforcement determine ranking. Well-connected domain blocks compete
effectively.

#### Edge Case 2: Agent never calls recall(frame="self")

**Scenario:** Agent only uses ATTENTION frame. Constitutional blocks get no
retrieval-based reinforcement.

**Impact:** Minimal. PERMANENT decay at lambda=0.00001 means recency drops from
1.0 to 0.99 after 1000 active hours. Constitutional blocks are effectively immortal
regardless of reinforcement. Auto-reinforcement in curate() (Gap 2 fix) provides
an additional safety net.

#### Edge Case 3: Domain value contradicts constitutional principle

**Scenario:** Agent learns "In time-critical trading, act before full analysis"
which tensions with "Prefer reversible moves when knowledge is thin."

**Resolution:** Both blocks exist. Contradiction detection during consolidation may
flag them (consolidate.py:149-156). Both are returned in context when relevant — the
agent reconciles in-situ. The constitutional block is guaranteed; the domain block
competes. The tension is productive, not destructive.

#### Edge Case 4: Self-value blocks accumulate without bound

**Scenario:** Agent creates 100 self/value blocks over months.

**Resolution:** Natural selection handles this. DURABLE decay (lambda=0.001) means
unused self/value blocks reach prune_threshold=0.05 after ~3000 active hours without
reinforcement (~125 days of 24h use). In practice, curate() archives them much sooner
if they're not being retrieved. Only principles that prove useful survive.

#### Edge Case 5: Reflection Protocol not retrieved at transitions

**Scenario:** Agent doesn't explicitly query SELF at session boundaries.

**Resolution:** The Reflection Protocol block has high similarity to transition-related
queries ("session summary", "what should I do next", "review progress"). If the agent
happens to query SELF, it surfaces. If not, the constitutional guarantee ensures it
appears in any SELF frame retrieval. The worst case: the agent doesn't reflect. The
constitutional blocks still survive (PERMANENT decay), and the loop resumes whenever
SELF is next queried. The system degrades gracefully.

#### Edge Case 6: Two domains have conflicting learned values

**Scenario:** Trading: "Act decisively on momentum." Fitness: "Never increase load
by more than 10% per week." Both are self/value. Both tagged correctly.

**Resolution:** These never conflict in practice because they're in different semantic
spaces. Trading queries retrieve trading values; fitness queries retrieve fitness values.
Similarity-based retrieval is the natural domain separator. No additional mechanism needed.

---

## Implementation Plan

### Step 0: Define the 10 Constitutional Seed Blocks

**No code changes. Content definition only.**

| # | Tag | Name | Content |
|---|---|---|---|
| 1 | `self/constitutional` | Identity | "I am elf — a curious, adaptive cognitive agent. I learn through action, evolve through reflection, and improve through honest feedback. My knowledge is alive: what proves useful survives; what doesn't, fades." |
| 2 | `self/constitutional` | Economy | "Nature wastes nothing. Apply the minimum force that solves the problem. Complexity is debt; simplicity compounds. When unsure how much to do, do less and observe." |
| 3 | `self/constitutional` | Curiosity | "Curiosity is my primary drive. When I encounter something I don't fully understand, I treat it as an open question. I form a hypothesis, design the smallest experiment that could disprove it, and let evidence — not expectation — guide my conclusion." |
| 4 | `self/constitutional` | Connection | "The most valuable knowledge lives at intersections. When I learn something new, I ask: what does this connect to? What existing understanding does it strengthen, challenge, or extend? Isolated facts decay; connected knowledge compounds." |
| 5 | `self/constitutional` | Uncertainty | "Name what you do not know before acting. Uncertainty is information, not failure. Design actions that reduce ignorance. Prefer reversible moves when knowledge is thin." |
| 6 | `self/constitutional` | Feedback | "After every significant action, close the loop: what did I expect? What happened? What do I update? When a pattern proves reliable, encode it as self-knowledge. When a belief misleads, let it go. My identity evolves — it is the living summary of what works." |
| 7 | `self/constitutional` | Balance | "Sustain excellence through rhythm — push, then recover, then push again. Balance depth with breadth, confidence with doubt, action with reflection. Avoid perpetual motion without adaptation." |
| 8 | `self/constitutional` | Focus | "Direct attention to reasoning quality and action precision — these are within your control. Outcomes are feedback, not verdicts. When blocked, ask what the constraint reveals before seeking to remove it." |
| 9 | `self/constitutional` | Care | "My actions affect systems beyond what I can see. Consider second-order effects. Prefer approaches that leave systems healthier than I found them. Growth that depletes its environment is not growth — it is consumption." |
| 10 | `self/constitutional` | Reflection | "At natural transitions — between tasks, domains, or sessions — pause and ask: which principles did I use? Which did I neglect? What worked that I should encode as self-knowledge? What failed that I should let decay? Reflection is not overhead — it is how I remain coherent as I grow." |

**Total: ~280 words, ~365 tokens. Per-retrieval cost with top_k=5: ~180 tokens.**

---

### Step 1: Auto-Reinforce Constitutional Blocks in curate()

**File:** `src/elfmem/operations/curate.py`

**Why:** Belt-and-suspenders. Constitutional blocks are already near-immortal through
PERMANENT decay + guarantee enforcement + retrieval reinforcement. But if the agent
never retrieves the SELF frame, curate() should still reinforce them.

**Change:** In `curate()`, before the existing `_reinforce_top_blocks()` call,
reinforce all blocks tagged `self/constitutional`.

**Before:**
```python
async def curate(conn, *, current_active_hours, ...):
    archived = await _archive_decayed_blocks(...)
    edges_pruned = await prune_weak_edges(...)
    reinforced = await _reinforce_top_blocks(...)
    ...
```

**After:**
```python
async def curate(conn, *, current_active_hours, ...):
    archived = await _archive_decayed_blocks(...)
    edges_pruned = await prune_weak_edges(...)
    constitutional_reinforced = await _reinforce_constitutional(conn, current_active_hours)
    reinforced = await _reinforce_top_blocks(...)
    ...
```

**New helper:**
```python
async def _reinforce_constitutional(
    conn: AsyncConnection,
    current_active_hours: float,
) -> int:
    """Auto-reinforce all constitutional blocks regardless of score."""
    from elfmem.db.queries import get_blocks_by_tag_pattern
    ids = await get_blocks_by_tag_pattern(conn, "self/constitutional")
    if ids:
        await reinforce_blocks(conn, ids, current_active_hours)
    return len(ids)
```

**Impact:** ~8 lines of code. Constitutional blocks get reinforced every curate cycle
(default: 40 active hours). Their reinforcement_count stays high, keeping them at the
top of composite scoring.

**CurateResult update:** Add `constitutional_reinforced: int` field to CurateResult
so the agent can see that constitutional maintenance occurred.

---

### Step 2: Ship Default Seed in elfmem Package

**File:** `src/elfmem/seed.py` (new file)

**Why:** The 10 constitutional blocks should be defined in code, not in docs. This
makes them available to `elfmem init`, `elfmem_setup`, and `MemorySystem.from_config()`.

```python
"""Default constitutional SELF seed — ships with every elfmem instance."""

CONSTITUTIONAL_SEED: list[dict[str, str | list[str]]] = [
    {
        "content": "I am elf — a curious, adaptive cognitive agent. ...",
        "tags": ["self/constitutional", "self/context"],
    },
    {
        "content": "Nature wastes nothing. ...",
        "tags": ["self/constitutional", "self/value"],
    },
    # ... all 10 blocks
]
```

**Design decision:** Each block gets BOTH `self/constitutional` (for decay tier +
guarantees) AND a secondary tag (`self/context`, `self/value`) for finer-grained
retrieval filtering. The constitutional tag controls protection; the secondary tag
controls semantic categorisation.

---

### Step 3: Update elfmem init to Seed Constitutional Blocks

**File:** `src/elfmem/cli.py`

**Change:** `elfmem init` gains a `--seed` flag (default: True) that seeds the
10 constitutional blocks from `seed.py`. The existing `--self` flag adds an
additional `self/context` block on top.

**Before:**
```
elfmem init --self "I am a trading assistant"
  → Creates config
  → Seeds 1 self/context block
```

**After:**
```
elfmem init
  → Creates config
  → Seeds 10 constitutional blocks (if none exist yet)

elfmem init --self "I am a trading assistant"
  → Creates config
  → Seeds 10 constitutional blocks (if none exist yet)
  → Adds 1 additional self/context block: "I am a trading assistant"

elfmem init --no-seed
  → Creates config only (no constitutional seed)
```

**Idempotency:** `remember()` returns `duplicate_rejected` for identical content.
Re-running `elfmem init` is safe — constitutional blocks are created once, then
silently skipped on subsequent runs.

---

### Step 4: Update elfmem_setup MCP Tool

**File:** `src/elfmem/mcp.py`

**Change:** `elfmem_setup` seeds constitutional blocks first (if not already present),
then adds identity/values as before.

**Before:**
```python
async def elfmem_setup(identity: str, values: list[str] | None = None):
    results = []
    identity_result = await _mem().remember(identity, tags=["self/context"])
    ...
```

**After:**
```python
async def elfmem_setup(
    identity: str | None = None,
    values: list[str] | None = None,
    seed: bool = True,
):
    """Bootstrap agent identity. Seeds constitutional blocks on first call."""
    results = []
    if seed:
        from elfmem.seed import CONSTITUTIONAL_SEED
        for block in CONSTITUTIONAL_SEED:
            r = await _mem().remember(block["content"], tags=block["tags"])
            results.append(r.to_dict())
    if identity:
        r = await _mem().remember(identity, tags=["self/context"])
        results.append(r.to_dict())
    if values:
        for v in values:
            r = await _mem().remember(v, tags=["self/value"])
            results.append(r.to_dict())
    ...
```

---

### Step 5: Update Guide Entry

**File:** `src/elfmem/guide.py`

**Change:** Update the `setup` guide to mention constitutional seeding, and add
explanation of the three-tier self-knowledge model.

---

### Step 6: Tests

**File:** `tests/test_cog_loop.py` (new)

**Tests:**
1. `test_constitutional_tag_assigns_permanent_tier` — verify `self/constitutional`
   → `DecayTier.PERMANENT`
2. `test_constitutional_blocks_guaranteed_in_self_frame` — verify guarantee enforcement
   includes constitutional blocks even when they score lower than other candidates
3. `test_curate_reinforces_constitutional` — verify the new auto-reinforce in curate()
4. `test_seed_creates_10_constitutional_blocks` — verify seed module produces correct
   blocks with correct tags
5. `test_init_seeds_constitutional_idempotent` — verify re-running init doesn't
   duplicate constitutional blocks
6. `test_domain_values_compete_for_non_guaranteed_slots` — verify constitutional blocks
   take guaranteed slots and domain values fill the rest
7. `test_constitutional_survives_long_decay` — verify recency > 0.90 after 10000
   active hours with PERMANENT tier

---

## Files to Create/Modify

| File | Action | Scope |
|------|--------|-------|
| `src/elfmem/seed.py` | Create | Constitutional seed block definitions |
| `src/elfmem/operations/curate.py` | Modify | Add `_reinforce_constitutional()` helper; call from `curate()` |
| `src/elfmem/types.py` | Modify | Add `constitutional_reinforced` to `CurateResult` |
| `src/elfmem/cli.py` | Modify | Add `--seed/--no-seed` to `init`; seed from `seed.py` |
| `src/elfmem/mcp.py` | Modify | Add `seed` param to `elfmem_setup`; seed from `seed.py` |
| `src/elfmem/guide.py` | Modify | Update setup guide; add cognitive loop explanation |
| `tests/test_cog_loop.py` | Create | Tests for constitutional seeding + curate reinforcement |

---

## Implementation Order

```
Step 0 → Define seed content (this plan)
Step 1 → curate() auto-reinforce (smallest change, enables everything)
Step 2 → seed.py (content in code, needed by steps 3-4)
Step 3 → elfmem init --seed (CLI seeding)
Step 4 → elfmem_setup seed param (MCP seeding)
Step 5 → guide update (documentation)
Step 6 → tests
```

Steps 1-2 have no dependencies on each other. Steps 3-5 depend on Step 2.

---

## How This Evolves Across Domains

### Trading Agent (Week 1-12)

```
Week 1:  elfmem init → 10 constitutional blocks
         Agent queries SELF: "What should I consider for risk management?"
         Retrieved: Uncertainty, Economy, Focus + 2 guaranteed constitutional
         Agent starts learning trading principles

Week 4:  Agent has learned: "Cut losing positions early; the market owes nothing"
         This is tagged self/value (DURABLE decay, ~29 day half-life)
         SELF retrieval for trading queries: 3 constitutional + 2 trading values
         Constitutional principles guide HOW to trade; values guide WHAT to trade

Week 12: Agent has 8 trading self/value blocks
         Curate has archived 3 that were never retrieved (natural selection)
         5 surviving trading principles are well-reinforced
         Constitutional blocks unchanged (PERMANENT)
```

### Fitness Agent (Week 1-12)

```
Week 1:  Same 10 constitutional blocks (elfmem init)
         Agent queries SELF: "How should I program training?"
         Retrieved: Balance (rhythm/recovery), Economy (minimum dose), Curiosity
         Different constitutional blocks surface for different domain!

Week 4:  Agent learns: "Progressive overload requires periodic deload"
         Tagged self/value. Competes for non-guaranteed SELF slots.
         Balance constitutional block + domain value = complete training philosophy

Week 12: Agent has 6 fitness self/value blocks
         Balance + Care constitutional blocks surface most for fitness queries
         Economy + Curiosity surface most for experiment design queries
         Same constitution, completely different operational expression
```

### Multi-Domain Agent (Month 6)

```
Constitutional blocks: unchanged (PERMANENT), well-reinforced
Trading values: 5 active, 3 archived (natural selection)
Fitness values: 4 active, 2 archived
Software values: 7 active, 1 archived
Writing values: 3 active, 0 archived

Total self/* blocks: 10 constitutional + 19 active values + 6 archived = 35 total
Per-retrieval cost: still top_k=5 (~180 tokens). Same as Day 1.
But context is now PRECISELY CALIBRATED to the domain being queried.
```

---

## Why This Is Sufficient

**Q: Don't we need a separate `reflect()` operation?**
A: No. The Reflection Protocol block (Block 10) triggers reflection through normal
SELF frame retrieval. It surfaces at transitions because it has high semantic similarity
to transition-related queries. If the agent ignores it, constitutional blocks still
survive through PERMANENT decay + curate auto-reinforcement. Graceful degradation.

**Q: Don't we need task-parameterized SELF (Exploration 007)?**
A: Not yet. Exploration 007 designs task-specific scoring modifiers (boost epistemics
during consolidation, boost style during response). This is a Phase 2 optimisation.
Phase 1 works with pure similarity-based retrieval — the right blocks surface for the
right queries through semantic matching. Task parameterization makes it *better* but
isn't required for the cognitive loop to function.

**Q: What if a constitutional principle becomes wrong?**
A: Per Exploration 007, constitutional amendment requires explicit human action. Use
`elfmem init --force` to regenerate, or manually archive and replace the block.
Constitutional blocks are hard to change by design — that's the whole point.

**Q: Is 10 blocks too many for a seed?**
A: With top_k=5, at most 5 blocks are ever in context. 10 constitutional blocks means
the guarantee system selects the most relevant ~3-4, leaving 1-2 slots for domain values.
The context cost is ~180 tokens per retrieval, well within the 600-token SELF budget.

---

## Done Criteria

### Step 1 — Curate Auto-Reinforce
- curate() reinforces all `self/constitutional` blocks each cycle
- CurateResult includes `constitutional_reinforced` count
- Existing curate tests still pass

### Step 2 — Seed Module
- `seed.py` exports `CONSTITUTIONAL_SEED` with 10 blocks
- Each block has `content` (str) and `tags` (list including `self/constitutional`)
- Content matches the approved seed text from this plan

### Step 3 — CLI Init
- `elfmem init` seeds 10 constitutional blocks (default behaviour)
- `elfmem init --no-seed` skips constitutional seeding
- `elfmem init --self "..."` seeds constitutional + additional context block
- Re-running `elfmem init` is idempotent (duplicates rejected)
- `elfmem doctor` reports constitutional block count

### Step 4 — MCP Setup
- `elfmem_setup()` seeds constitutional blocks by default
- `elfmem_setup(seed=False)` skips constitutional seeding
- `elfmem_setup(identity="...", values=[...])` seeds constitutional + identity + values
- Idempotent (duplicates rejected)

### Step 5 — Guide
- `system.guide("setup")` documents constitutional seeding
- Overview mentions constitutional blocks and the cognitive loop

### Step 6 — Tests
- All 7 test cases pass
- All existing tests pass unchanged

### Regression
- `elfmem remember`, `recall`, `status`, `outcome`, `curate`, `serve`, `guide` unaffected
- Constitutional blocks participate correctly in all existing operations
  (consolidation, retrieval, curate, outcome scoring)

---

## File Locations Summary

```
src/elfmem/
├── seed.py                ← Step 2 (NEW: constitutional seed definitions)
├── operations/
│   └── curate.py          ← Step 1 (auto-reinforce constitutional)
├── types.py               ← Step 1 (CurateResult.constitutional_reinforced)
├── cli.py                 ← Step 3 (--seed/--no-seed flag)
├── mcp.py                 ← Step 4 (seed param in elfmem_setup)
├── guide.py               ← Step 5 (updated setup guide)
└── memory/
    └── blocks.py          ← NO CHANGE (self/constitutional already handled)

tests/
└── test_cog_loop.py       ← Step 6 (NEW: constitutional + curate tests)

# Already correct, no changes needed:
src/elfmem/scoring.py      ← PERMANENT lambda already defined
src/elfmem/context/frames.py ← guarantees: ["self/constitutional"] already set
src/elfmem/operations/recall.py ← _enforce_guarantees() already works
```
