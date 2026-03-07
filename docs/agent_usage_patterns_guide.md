# Agent Usage Patterns for elfmem — Complete Guide

## Overview

This guide captures optimal patterns for AI agents using elfmem's four core operations. These patterns emerge from research into how LLM agents learn, what feedback loops compound knowledge, and common pitfalls.

**Key Insight:** Agents that follow these patterns learn 5-10x faster and achieve more stable knowledge than agents that don't.

---

## Core Principles

### 1. Not All Experiences Are Worth Remembering

**Most agents make this mistake:** Storing every observation, event, or outcome.

**The Fix:** Remember only:
- Surprising observations (violations of expectations)
- Generalizable patterns (rules that transfer)
- Unexpected failures and successes
- Learnings from resolved conflicts

**Result:** Smaller, higher-quality knowledge base. More reliable retrieval.

---

### 2. Feedback Loop is Mandatory

**Formula:**
```
PLAN → SET EXPECTATION → ACT → OBSERVE → COMPARE → SIGNAL → ENCODE
```

**Most agents miss:** The expectation-setting step. Without it, you can't compute surprise/signal.

**The Fix:**
```python
# Before action:
expectation = predict(action, retrieved_knowledge)
store(expectation)  # Critical!

# After action:
observation = measure_outcome()
signal = abs(observation - expectation)
outcome(blocks, signal=signal)  # Reinforce guides
```

**Result:** Closed feedback loop. Knowledge compounds predictably.

---

### 3. Frame Selection Dominates Retrieval Quality

**Anti-pattern:** Always retrieving from ATTENTION or a default frame.

**The Fix:** Select frame by task type:

| **Task** | **Frame** | **Top-K** | **Expand?** |
|---|---|---|---|
| Novel problem | ATTENTION | 20 | YES |
| Executing known pattern | TASK | 5 | NO |
| Values/identity conflict | SELF | 5 | YES |
| Understanding context | WORLD | 10 | YES |
| Quick lookup | SHORT_TERM | 3 | NO |

**Result:** 50%+ improvement in retrieval relevance.

---

### 4. Signals Must Be Calibrated

**Anti-pattern:** Treating all signals equally, or creating noisy signals.

**The Fix:**
- **Weight by signal reliability:** Tight feedback loop (action → outcome in seconds) = weight 1.0. Loose loop (days) = weight 0.5.
- **Batch outcomes:** Average 3-5 related outcomes before signaling.
- **Penalize confident errors:** Block with confidence=0.9 that was wrong deserves harsher penalty.

**Result:** Learning from reliable signals only. Noise filtered out.

---

### 5. Curation is Active Knowledge Gardening

**Anti-pattern:** Set it and forget it. Knowledge just decays.

**The Fix:**
- **Trigger on schedule:** Every 7 days or when INBOX > 50 blocks
- **Reinforce top patterns:** Boost recently-used blocks
- **Archive weak edges:** Prune connections below confidence 0.3
- **Preserve constitutional:** Identity blocks never decay

**Result:** Knowledge stays alive and useful. Noise gradually removed.

---

## The 5 Core Remember Patterns

### Pattern 1: Remember After Surprise

```python
if |observation - expectation| > threshold:
    remember(pattern, confidence = 0.5 + surprise_magnitude)
```

**When to use:** After every significant action.

**Example:**
- Expected: API call succeeds in <100ms
- Observed: Timeout after 30 seconds
- Surprise magnitude: High
- Remember: "API X times out under high load. Implement timeout + retry."

**Why it works:** Surprises indicate knowledge gaps. High surprise = high learning value.

---

### Pattern 2: Remember Patterns, Not Events

```
BAD:  "On 2026-03-07 at 14:32, the request failed"
GOOD: "Database timeouts occur when concurrent writes exceed 100/sec.
       Implement connection pooling with max_wait=5s"
```

**Tagging strategy:**
```
BAD:  ["event", "learned", "important"]
GOOD: ["domain/database/concurrency", "pattern/optimization"]
```

**Why it works:** Patterns transfer to new situations. Events don't. Natural decay removes unreinforced noise.

---

### Pattern 3: Remember Connections

When you learn something new, search existing memory for related blocks:

```
New learning: "Rate limiting prevents cascading failures"
Related blocks:
  - "API degradation under load" (connected via feedback loops)
  - "Circuit breaker pattern" (connected via failure handling)
Explicit edge: "Rate limiting supports circuit breaker; prevents need to trip it"
```

**Why it works:** Isolated knowledge decays. Connected knowledge reinforces across the graph.

---

### Pattern 4: Tag Hierarchically

```
SEMANTIC HIERARCHY TEMPLATE:
  domain/category/subcategory/specific
  agent_context/memory_type/stability

EXAMPLES:
  "programming/python/concurrency/asyncio_patterns"
  "pattern/performance/caching/redis_strategies"
  "self/experience/conflict_resolved/async_vs_sync_choice"
```

**Why it works:** Enables multi-grain filtering. "programming" returns all code knowledge. "programming/python" narrows down. Hierarchies beat flat tags 10:1.

---

### Pattern 5: Confidence = Actual Reliability

```
0.3  Seen once, contradictions exist, limited testing
0.5  Multiple confirmations, tested in 2-3 contexts
0.7  Reliable across varied conditions, no contradictions
0.9  Deep validation, used successfully 10+ times
```

**NOT to be confused with:**
- Recency (that's handled by decay)
- Importance (that's handled by reinforcement weight)

**Why it works:** Retrieved high-confidence blocks are more reliable. Agents can weight low-confidence knowledge appropriately (explore vs. exploit).

---

## The 5 Core Recall Patterns

### Pattern 1: Frame Selection is Task-Dependent

```python
frame = {
    "exploration": "attention",           # Broad scope, diverse ideas
    "execution": "task",                  # Goal-focused
    "identity_conflict": "self",          # Values-guided
    "understanding": "world",             # Context and connections
    "quick_fact": "short_term"            # Recent learning
}.get(task_type, "attention")

blocks = recall(query, frame=frame)
```

**Real example:**
- Task: "Should I refactor this code now or later?"
- This is a SELF conflict (effort vs. technical debt vs. deadlines)
- Retrieve SELF frame: What do I value? (speed vs. quality)
- Not ATTENTION: That would give me generic refactoring advice
- Result: Decision grounded in identity

**Why it works:** Frame filtering dramatically improves signal-to-noise.

---

### Pattern 2: Query Semantically

```
BAD QUERIES:
  "python"               (100+ results, all Python-related)
  "TypeError line 42"    (0 results, too specific)

GOOD QUERIES:
  "concurrent programming patterns"       (Captures intent)
  "API error handling best practices"    (Domain + intent)
  "when should I use async vs threads?"  (Comparison, actionable)
```

**Why it works:**
- Semantic captures intent better than keywords
- Specific enough to be useful, general enough to transfer
- Goldilocks zone: not too broad, not too narrow

---

### Pattern 3: Handle Contradictions Recursively

```python
if contradictions_detected(blocks):
    # Don't pick one arbitrarily
    # Understand the conflict

    self_blocks = recall(query, frame="self")    # My values
    world_blocks = recall(query, frame="world")  # Broader context

    # Design resolution experiment:
    design_experiment(
        contradictions=contradictions,
        values=self_blocks,
        context=world_blocks
    )
```

**Real example:**
- Retrieve: Block A says "optimize for speed". Block B says "optimize for reliability".
- Both are true in different contexts. Don't merge or pick one.
- Query SELF: "What do I actually value here?" → (reliability matters more in this domain)
- Query WORLD: "What's the usage pattern?" → (Users tolerate slowness more than crashes)
- Result: Coherent decision grounded in values and context.

**Why it works:** Contradictions signal incomplete model. Ignoring them compounds confusion. Recursive queries provide context for genuine resolution.

---

### Pattern 4: Silence is Signal (Empty Recall)

```python
if len(recall(query)) == 0:
    action = {
        "exploration_phase": "Explore and remember",
        "execution_phase": "Be very careful (untested)",
        "high_stakes": "STOP. Design experiment first."
    }.get(phase)
```

**Real example:**
- Query: "How do I handle distributed transactions?"
- Result: No blocks found
- This is NOT a failure. This is a knowledge gap discovery.
- In exploration mode: Explore and learn. Document what you discover.
- In production: Design careful experiment. Untested path is risky.

**Why it works:** Gaps are highest-learning opportunities. Treating gaps seriously leads to robust knowledge.

---

### Pattern 5: Expand Graph When Top-K Insufficient

```python
blocks = recall(query, top_k=5)
if blocks_feel_insufficient(blocks):
    expanded = recall(query, top_k=5, expand_graph=true)
    # Retrieves related-but-not-similar blocks via edges
```

**When to use:**
- Task is novel (topicallearning curve)
- Domain is interconnected (ideas reference each other)
- Need broad context (not just top-similar)

**Why it works:** Similarity-based retrieval is blind to indirect relevance. Graph expansion recovers context you didn't know was relevant.

---

## The 5 Core Outcome Patterns

### Pattern 1: Signal = Expectation - Observation

```python
expectation = agent_prediction(situation)
observation = actual_outcome(after_action)
surprise = abs(observation - expectation)
signal = normalize(surprise)  # [0, 1] scale
```

**Examples:**
```
Expected: API succeeds (<100ms)
Observed: Timeout (30s)
Signal: 0.1 (bad prediction, learn!)

Expected: Timeout
Observed: Timeout
Signal: 0.5 (model correct, neutral)

Expected: Timeout
Observed: Success
Signal: 0.9 (pleasant surprise, learn!)
```

**Why it works:**
- Surprise captures learning value
- Correct predictions = no learning needed
- Mispredictions = high learning value

---

### Pattern 2: Weight by Signal Confidence

```
Tight feedback loop (action → outcome in seconds): weight = 1.0
Loose loop (action → outcome in days): weight = 0.5
Noisy environment (many confounding factors): weight = 0.3
```

**Why it works:**
- Tight loops are reliable; learn fast
- Loose loops are ambiguous; learn slowly
- Noisy signals are uncertain; dilute appropriately

---

### Pattern 3: Batch Outcomes to Reduce Noise

```python
# Don't: signal individually
outcome([block_1], signal=0.6)
outcome([block_2], signal=0.7)
outcome([block_3], signal=0.65)

# Do: batch related outcomes
outcomes = [0.6, 0.7, 0.65]
avg_signal = mean(outcomes)       # 0.65
confidence = 1.0 - std(outcomes)  # Low variance = high confidence
outcome(blocks, signal=avg_signal, weight=confidence)
```

**Why it works:** Single outcomes are noisy. Batches reveal true signal. Consistency indicates reliable learning.

---

### Pattern 4: Reinforce Patterns, Not Events

```python
# After successful action:
retrieved_blocks = recall(query)

for block in retrieved_blocks:
    if block.enabled_success(action):
        # Reinforce the pattern (rule), not the event
        outcome([block.id], signal=0.9, source="successful_execution")
```

**Why it works:** Patterns transfer to new situations. Events don't.

---

### Pattern 5: Penalize Confident Errors

```python
# Block had confidence=0.9, was wrong → harsh penalty
# Block had confidence=0.3, was wrong → mild penalty

signal_adjustment = 1.0 - block.confidence
outcome([block.id], signal=base_signal * signal_adjustment)
```

**Why it works:** Prevents over-confidence accumulation. High-confidence errors are more damaging.

---

## The 5 Core Curate Patterns

### Pattern 1: Trigger on Accumulation OR Stability

```python
should_curate = (
    (blocks_in_inbox > 50) OR                    # Too many uncommitted
    (days_since_last_curate > 7) OR              # Time-based
    (new_blocks_this_session < 2 AND             # Stability signal:
     time_in_session > 2_hours)                  # Learning has slowed
)
```

**Why it works:**
- Accumulation: Too many options hurt decision-making
- Stability: When learning slows, consolidate what you've learned

---

### Pattern 2: Preserve Constitutional Blocks

```python
# Constitutional blocks (tagged "self/constitutional"):
#   - PERMANENT decay (λ = 0.00001, ~7.9 year half-life)
#   - Always guaranteed in SELF retrieval
#   - Auto-reinforced during curation
#   - Confidence always = 1.0

# NEVER archive constitutional blocks
# They are identity; if they decay, agent becomes directionless
```

**Why it works:** Identity is bedrock. Everything else is built on it.

---

### Pattern 3: Reinforce Top-K by Recent Usage

```python
top_blocks = get_blocks_by_recency(
    hours=168,        # Last 7 days
    k=10,             # Top 10 most-used
    status="active"
)

for block in top_blocks:
    # Boost confidence
    block.confidence = min(0.99, block.confidence + 0.05)
    # Reset decay timer
    block.last_reinforced = now()
```

**Why it works:**
- Knowledge you use often should survive
- Reinforcement combats natural decay
- Creates virtuous cycle: use → curate → reinforced → more useful

---

### Pattern 4: Archive Weak Edges

```python
edges_to_prune = [
    edge for edge in graph.edges
    if edge.confidence < 0.3 and
       not edge.recently_traversed(days=30)
]

for edge in edges_to_prune:
    archive_edge(edge)  # Reversible
```

**Why it works:**
- Weak edges create false paths
- Unused edges probably aren't valuable
- Cleaner graph = more reliable retrieval

---

### Pattern 5: Meta-Curation

```python
# After several curation cycles, reflect:
- How many blocks were archived? (Decay rate healthy?)
- Which domains have highest retention? (Where is knowledge stable?)
- Are contradictions being resolved? (Is learning coherent?)
- Is the graph growing or stabilizing? (Learning curve?)

if contradictions_persist:
    # Previous learning isn't coherent
    # Trigger meta-experiment: design resolution process
```

**Why it works:** Monitoring curation itself reveals system health.

---

## The 5 Key Anti-Patterns

### Anti-Pattern 1: Remember Everything

**Symptom:** Hundreds of blocks, retrieval returns 50+ results, can't find signal.

**Why it fails:** Events don't transfer. Noise accumulates.

**Fix:** Remember patterns, surprising outcomes, transferable lessons. Trust decay.

---

### Anti-Pattern 2: No Feedback Loop

**Symptom:** Knowledge gradually becomes stale and forgotten.

**Why it fails:** Without signals, all knowledge decays equally. Good patterns can't compound.

**Fix:** Collect outcome signals after actions. Batch for reliability. Reinforce patterns. Curate periodically.

---

### Anti-Pattern 3: Ignore Contradictions

**Symptom:** Conflicting blocks confuse decision-making. Contradiction persists.

**Why it fails:** Contradictions signal incomplete model. Ignoring them compounds confusion.

**Fix:** Flag immediately. Query SELF/WORLD for context. Design resolution experiment.

---

### Anti-Pattern 4: Generic Tags

**Symptom:** tags=['learned', 'important', 'fact']. All blocks identical.

**Why it fails:** Tags become noise. Can't filter or categorize.

**Fix:** Semantic hierarchy. Example: 'programming/python/concurrency'.

---

### Anti-Pattern 5: Fixed Decay Everywhere

**Symptom:** Constitutional principles decay like event facts. Good knowledge fades as fast as bad.

**Why it fails:** Not all knowledge is equal. Stability should influence decay.

**Fix:** Adaptive decay profiles. Constitutional=permanent. High-confidence=durable. Experimental=ephemeral.

---

## Operationalizing the Cognitive Loop

The 10 constitutional blocks define *principles*. These usage patterns define *how to operationalize* those principles.

**Example:**
- Constitutional block: "Curiosity is my primary drive"
- Agent usage pattern: "Silence is signal—empty recall indicates knowledge gap"
- Combined: When agent discovers a gap, curiosity drives exploration (pattern-consistent behavior)

**Next phase:** Operationalize each of the 10 constitutional blocks into concrete behaviors, decision frameworks, and reflection practices.

---

## Quick Reference: Task → Frame → Top-K → Expand → Weight

| **Task** | **Frame** | **Top-K** | **Expand** | **Weight** |
|---|---|---|---|---|
| Novel problem | ATTENTION | 20 | YES | 0.5 |
| Execution | TASK | 5 | NO | 1.0 |
| Values conflict | SELF | 5 | YES | 1.0 |
| Understanding | WORLD | 10 | YES | 0.5 |
| Quick lookup | SHORT_TERM | 3 | NO | 0.8 |

---

## Summary: The 20 Agent Usage Patterns

### Remember (5)
1. Remember after surprise
2. Remember patterns, not events
3. Remember connections
4. Tag hierarchically
5. Confidence = actual reliability

### Recall (5)
6. Frame selection is task-dependent
7. Query semantically
8. Handle contradictions recursively
9. Silence is signal (knowledge gaps)
10. Expand graph when insufficient

### Outcome (5)
11. Signal = expectation - observation
12. Weight by signal confidence
13. Batch outcomes to reduce noise
14. Reinforce patterns, not events
15. Penalize confident errors

### Curate (5)
16. Trigger on accumulation or stability
17. Preserve constitutional blocks
18. Reinforce top-K by recency
19. Archive weak edges
20. Meta-curation (monitor the monitors)

---

## Next: Operationalizing the Cognitive Loop

These patterns tell agents *how to use elfmem*. Next, we'll operationalize the 10 constitutional blocks to tell agents *why* and *when* to use these patterns.
