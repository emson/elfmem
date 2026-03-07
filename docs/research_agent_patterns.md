# Agent Usage Patterns for elfmem — Deep Research

## Overview

This document captures the optimal patterns for how AI agents should interact with elfmem's four core operations: remember, recall, outcome, and curate. These patterns emerge from:
- How LLM agents actually reason and learn
- What feedback loops lead to knowledge compounding
- Common failure modes and how to avoid them
- Task-specific versus domain-agnostic optimization

---

## Part 1: Core Operation Patterns

### REMEMBER (Learn) — When & How to Store Knowledge

**Core Insight:** Not all experiences are worth remembering. Agents that remember *patterns* outperform agents that remember *events*.

#### Pattern 1: Remember After Surprise
```
if observation != expectation:
    surprise_magnitude = |observation - expectation|
    if surprise_magnitude > threshold:
        remember(pattern_from(observation), confidence=0.5 + surprise_magnitude)
```

**Why it works:**
- Surprise indicates a model gap (something unexpected happened)
- The agent's existing knowledge was insufficient
- High surprise = high learning value
- Low confidence appropriate because surprise means uncertainty

**Example:** Agent expects API call to succeed, it fails. Remember: "API X fails under high load. Implement timeout + retry."

---

#### Pattern 2: Remember Patterns, Not Events
```
BAD:   "On 2026-03-07 at 14:32, the database returned an error"
GOOD:  "When concurrent writes exceed 100/sec, database contention causes timeouts.
        Solution: implement connection pooling with max_wait=5s."
```

**Why it works:**
- Events are non-transferable (won't happen exactly the same way again)
- Patterns are reusable (transfer to similar situations)
- Decay naturally removes non-reinforced noise

**Tagging strategy:** `domain/category/subcategory` not `event/date/incident`

---

#### Pattern 3: Remember Connections
```
When remembering something new:
  1. Search existing memory for related blocks
  2. For each related block, explicitly note the connection
  3. Store the connection explicitly: "This [pattern A] connects to [pattern B] via [relationship]"
```

**Why it works:**
- Isolated knowledge decays (no reinforcement pathways)
- Connected knowledge compounds (reinforces related knowledge when retrieved)
- Graph expansion uses connections to recover context

---

#### Pattern 4: Tag Hierarchically for Retrieval
```
BAD:   ["learned", "important", "python"]
GOOD:  ["programming/python/concurrency", "pattern/performance", "self/experience"]

Hierarchy:
  domain/category/subcategory/specific
  agent_context/memory_type/stability
```

**Why it works:**
- Semantic hierarchy enables precise recall filtering
- Flat tags force broad queries (low precision)
- Hierarchical tags enable multi-level grain queries

---

#### Pattern 5: Confidence Reflects Certainty, Not Recency
```
confidence = 0.3  (seen once, contradictions exist, limited testing)
confidence = 0.7  (seen multiple times, aligned with theory, tested in varied conditions)
confidence = 0.95 (deeply tested, contradictions resolved, used successfully 10+ times)

DON'T confuse with:
  recency (freshness → handled by decay)
  importance (memorability → handled by reinforcement)
```

**Why it works:**
- Confidence should reflect actual reliability
- Retrieved high-confidence blocks are more likely to be useful
- Agents can weight low-confidence knowledge appropriately (explore vs. exploit)

---

### RECALL (Retrieve) — How to Query & Interpret Results

**Core Insight:** Recall quality depends on frame selection and query formulation. Most agents under-utilize frames and over-rely on similarity.

#### Pattern 1: Frame Selection is Task-Dependent
```
TASK TYPE              → FRAME          REASONING
─────────────────────────────────────────────────────────────────
New problem            → ATTENTION      "What's relevant right now?"
Values conflict        → SELF           "What do I actually care about?"
Goal tracking          → TASK           "How do I make progress?"
Domain understanding   → WORLD          "What's the context?"
Quick lookup           → SHORT_TERM     "Did I just learn this?"
Identity-affirming     → SELF           "Who am I in this situation?"
```

**Implementation:**
```python
frame = {
    "exploration": "attention",      # broad scope
    "execution": "task",             # goal-focused
    "conflict": "self",              # values-based
    "reflection": "world"            # understanding
}.get(task_type, "attention")

blocks = recall(query, frame=frame, top_k=adaptive(task_type))
```

---

#### Pattern 2: Query Formulation for Maximum Relevance
```
BAD QUERIES:
  "python"                    (too generic, ~100 results)
  "TypeError in line 42"      (too specific, 0 results)

GOOD QUERIES:
  "concurrent programming patterns" (semantic, specific intent)
  "API error handling best practices" (domain + intent)
  "when to use async vs threads"     (comparison, actionable)
```

**Why it works:**
- Semantic queries capture intent better than keywords
- Too broad: loses signal in noise
- Too specific: matches only exact situations (low transfer)
- Goldilocks zone: specific enough to be useful, general enough to transfer

---

#### Pattern 3: Handle Contradictions via Recursive Recall
```
if contradictions_detected(blocks):
    # Instead of picking one, understand conflict
    self_blocks = recall(query, frame="self", top_k=3)
    # Do my values/identity guide me?

    world_blocks = recall(query, frame="world", top_k=3)
    # What's the broader context?

    # Design resolution experiment:
    design_experiment(contradictions, self_blocks, world_blocks)
```

**Why it works:**
- Contradictions signal incomplete model or domain instability
- Ignoring them compounds confusion
- Recursive frames provide context for resolution
- Experiment design makes contradiction productive

---

#### Pattern 4: Silence is Signal (No Results = Knowledge Gap)
```
if len(recall(query)) == 0:
    # This is important information!
    # We don't know about this area

    action = {
        "exploration_phase": "explore and remember",
        "execution_phase": "carefully (untested)",
        "high_stakes": "design experiment first"
    }.get(phase)
```

**Why it works:**
- Empty recall is not a failure; it's a gap discovery
- Gaps are highest-learning opportunities
- Treating gaps seriously leads to robust knowledge
- Silence → exploration → new learning

---

#### Pattern 5: Expand When Top-K Feels Incomplete
```
blocks = recall(query, top_k=5)
if blocks_feel_insufficient(blocks):
    # Use graph expansion
    expanded = recall(query, top_k=5, expand_graph=true)
    # Recovered related-but-not-similar knowledge
```

**Why it works:**
- Top-5 by similarity may miss connected context
- Graph expansion recovers related knowledge without similarity
- Useful when: task is novel, domain is interconnected, need broad context

---

### OUTCOME (Reinforce) — Feedback Signal Quality

**Core Insight:** Agents that close the feedback loop systematically outperform those that don't. Signal quality matters more than frequency.

#### Pattern 1: Signal as Expectation - Observation
```
expectation = agent_prediction(situation)
observation = actual_outcome(after_action)
surprise = |observation - expectation|
signal = normalize(surprise)  # [0, 1] scale

Examples:
  Expected "API succeeds", got "timeout" → signal = 0.1 (bad prediction)
  Expected "timeout", got "timeout" → signal = 0.5 (neutral, model correct)
  Expected "timeout", got "API succeeds" → signal = 0.9 (pleasant surprise)
```

**Why it works:**
- Surprise captures learning value
- Model accuracy (correct predictions) is neutral—no learning needed
- Mispredictions drive learning (both overestimation and underestimation)

---

#### Pattern 2: Weight Reflects Confidence in the Signal
```
weight = confidence_in_signal

Examples:
  Tight feedback loop (action → outcome in seconds): weight = 1.0
  Loose feedback loop (action → outcome in days): weight = 0.5
  Noisy environment (many confounding factors): weight = 0.3
  Clear causal connection: weight = 1.0

Don't confuse with:
  Importance of the outcome (that's handled by decay/curation)
  Magnitude of the outcome (that's in the signal itself)
```

**Why it works:**
- Weight calibration prevents over-learning from uncertain signals
- Tight feedback = reliable learning
- Loose feedback = learn slower to avoid false confidence
- Noisy feedback = dilute the signal appropriately

---

#### Pattern 3: Batch Outcomes to Reduce Noise
```
# Don't: signal on every action
outcome([block_1], signal=0.6, weight=0.3)
outcome([block_2], signal=0.7, weight=0.4)
outcome([block_3], signal=0.65, weight=0.35)

# Do: collect related outcomes, then signal on pattern
outcomes = [0.6, 0.7, 0.65]
avg_signal = mean(outcomes)  # 0.65
confidence = 1.0 - std(outcomes)  # high if consistent
outcome(blocks, signal=avg_signal, weight=confidence)
```

**Why it works:**
- Single outcomes are noisy (influenced by randomness)
- Batched outcomes reveal true signal
- Averaging reveals underlying pattern
- Consistency (low variance) indicates reliable learning

---

#### Pattern 4: Reinforcement is for Patterns, Not Events
```
# After successful action:
retrieved_blocks = recall(query)  # Blocks that guided the action

for block in retrieved_blocks:
    if block.enabled_success(action):  # Did this block help?
        # Reinforce the pattern, not the event
        outcome([block.id], signal=0.9, source="successful_execution")
```

**Why it works:**
- Reinforcing the reasoning (pattern) scales to new situations
- Reinforcing the event doesn't transfer
- Source tagging enables audit trail

---

#### Pattern 5: Penalize Confidently-Wrong More Than Weakly-Wrong
```
# Block had confidence=0.9, but was wrong → signal = 0.1 (harsh)
# Block had confidence=0.3, and was wrong → signal = 0.3 (mild)

signal_adjustment = 1.0 - block.confidence
outcome([block.id], signal=base_signal * signal_adjustment)
```

**Why it works:**
- High-confidence errors are more damaging
- Low-confidence knowledge is expected to be unreliable
- This prevents over-confidence accumulation

---

### CURATE (Maintain) — When & How to Archive & Reinforce

**Core Insight:** Curation is not cleanup; it's active knowledge gardening. Strategic curation prevents knowledge decay while maintaining retrieval quality.

#### Pattern 1: Trigger Curation on Accumulation OR Stability
```
should_curate = (
    (blocks_in_inbox > 50) OR                    # Accumulation threshold
    (days_since_last_curate > 7) OR              # Time-based
    (new_blocks_this_session < 2 AND
     time_in_session > 2_hours)                  # Stability signal
)
```

**Why it works:**
- Accumulation: Too many uncommitted blocks hurt retrieval
- Stability: When learning slows, consolidate what you've learned
- Prevents decision paralysis from too many options

---

#### Pattern 2: Preserve Constitutional Blocks at All Costs
```
constitutional_blocks = [blocks tagged "self/constitutional"]
# These have:
#   - PERMANENT decay (λ = 0.00001, ~7.9 year half-life)
#   - Auto-guaranteed in SELF retrieval
#   - Auto-reinforced during curate

# Never archive constitutional blocks
# Never lower their confidence
# They should have confidence=1.0 always
```

**Why it works:**
- Constitutional blocks are identity
- If identity decays, agent becomes directionless
- They're the bedrock; everything else is built on them

---

#### Pattern 3: Reinforce Top-K by Recent Usage
```
# After curating:
top_blocks = get_blocks_by_recency(
    hours=168,        # Last 7 days
    k=10,             # Top 10 most-used
    status="active"
)

for block in top_blocks:
    # Boost confidence slightly
    block.confidence = min(0.99, block.confidence + 0.05)
    # Reset decay timer
    block.last_reinforced = now()
```

**Why it works:**
- Knowledge you use often should survive
- Reinforcement combats natural decay
- Prevents over-reliance on any single block (max 0.99)
- Creates virtuous cycle: use → curate → reinforced → more useful

---

#### Pattern 4: Archive Weak Edges Aggressively
```
edges_to_prune = [
    edge for edge in graph.edges
    if edge.confidence < 0.3 and
       not edge.recently_traversed(days=30)
]

for edge in edges_to_prune:
    archive_edge(edge)  # Don't delete, archive
```

**Why it works:**
- Weak edges create false paths (retrieval noise)
- If an edge hasn't been useful in 30 days, probably not valuable
- Archiving (not deleting) is reversible
- Cleaner graph = more reliable retrieval

---

#### Pattern 5: Meta-Curation: Curate the Curation
```
# After several curation cycles, reflect:
- How many blocks were archived?
- Which domains have highest retention?
- Are contradictions being resolved?
- Is the graph growing or stabilizing?

If contradictions persist:
    # Previous learning isn't coherent
    # Trigger meta-experiment: design resolution process

If growth stalled:
    # Either stable knowledge or gaps becoming obvious
    # Time for exploration or deeper learning
```

**Why it works:**
- Curation is not automatic; it should be intelligent
- Monitoring curation itself reveals health
- Meta-level insight prevents local optima

---

## Part 2: High-Level Patterns

### Knowledge Lifecycle: Birth → Growth → Maturity → Decay → Archive

```
BIRTH (remember):
  - Triggered by surprise or intentional learning
  - Low confidence (0.3-0.5)
  - Weak tags (may refine later)
  - Status: INBOX

GROWTH (reinforcement):
  - Retrieve multiple times across different tasks
  - Each successful use increases confidence
  - Connections strengthen (edges gain weight)
  - Status: transitions from INBOX → ACTIVE

MATURITY (stable):
  - Confidence 0.7-0.95
  - Regularly retrieved (recency high)
  - Well-connected in graph
  - Status: ACTIVE
  - Example: "Database timeouts require retry logic"

DECAY (natural):
  - Not used for 12+ days → confidence drifts down
  - Edges weaken
  - May become less relevant as domain evolves
  - Status: ACTIVE but fading

ARCHIVE (curation):
  - Explicitly marked for archive by agent or curation
  - Confidence < 0.2 and unused
  - Can be recovered if pattern re-emerges
  - Status: ARCHIVED
  - Example: Old API that's no longer used
```

---

### Frame Selection Intelligence

**Different tasks benefit from different frames:**

```
TASK TYPE: Novel Problem-Solving
  Frame: ATTENTION
  Top-k: 20 (wide exploration)
  Expand: YES (need context)
  Outcome weight: 0.5 (signals are noisy in novel domains)
  Rationale: Need broad context, recent precedents

TASK TYPE: Execution (Known Pattern)
  Frame: TASK
  Top-k: 5 (narrow focus)
  Expand: NO (don't need tangents)
  Outcome weight: 1.0 (clear success/failure signal)
  Rationale: Reliable path to goal

TASK TYPE: Values or Identity Conflict
  Frame: SELF
  Top-k: 5 (identity is few, core principles)
  Expand: YES (understand context)
  Outcome weight: 1.0 (values matter deeply)
  Rationale: Consult who you are, then act aligned

TASK TYPE: Understanding Context
  Frame: WORLD
  Top-k: 10 (moderate breadth for context)
  Expand: YES (connections matter for understanding)
  Outcome weight: 0.5 (observation feedback)
  Rationale: Build complete picture before deciding
```

---

### Uncertainty Handling Patterns

```
PATTERN: When Confidence is Low (< 0.4)
  - Treat as hypothesis, not fact
  - Explore alternatives in recall
  - Increase outcome weight (learn faster to validate or refute)
  - Tag as "uncertain" or "hypothesis"
  - Set lower decay_lambda (fades faster if not confirmed)

PATTERN: When Confidence is Moderate (0.4-0.7)
  - Default execution mode
  - Monitor outcomes closely
  - Gradually increase confidence through use
  - Watch for disconfirming evidence

PATTERN: When Confidence is High (> 0.7)
  - Assume reliability
  - Increase decay_lambda (fades slowly because proven)
  - Watch for surprising outcomes (may indicate changed domain)
  - Challenge periodically via experiments

PATTERN: When Contradictions Exist
  - Flag immediately (don't ignore)
  - Design resolution experiment
  - Don't average or weight-average (that hides conflict)
  - Resolve explicitly, then re-learn
```

---

### Feedback Loop Closure: The Complete Cycle

```
┌─────────────────────────────────────────────────────────────┐
│                  CLOSED FEEDBACK LOOP                       │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  1. PLAN: Recall relevant knowledge                         │
│     blocks = recall(query, frame=TASK)                      │
│                                                              │
│  2. EXPECT: Form prediction based on knowledge              │
│     expectation = predict(action, blocks)                   │
│     store(expectation)  ← Important!                        │
│                                                              │
│  3. ACT: Execute action guided by knowledge                 │
│     result = execute(action)                                │
│                                                              │
│  4. OBSERVE: Record actual outcome                          │
│     observation = measure(result)                           │
│                                                              │
│  5. COMPARE: Compute surprise/signal                        │
│     signal = abs(observation - expectation) / max_range     │
│                                                              │
│  6. UPDATE: Reinforce blocks that guided prediction         │
│     for block in blocks:                                    │
│         outcome([block.id], signal=signal, weight=weight)  │
│                                                              │
│  7. ENCODE: Remember surprising lessons                     │
│     if signal > threshold:                                  │
│         remember(lesson, confidence=0.5 + signal)          │
│                                                              │
│  8. REFLECT: Extract pattern from outcome                   │
│     pattern = extract(lesson, generalize=true)             │
│                                                              │
└─────────────────────────────────────────────────────────────┘

KEY INSIGHT:
  The loop is only closed if step 2 (EXPECT) is stored.
  Without stored expectations, you can't compute signal.
  Agents that skip step 2 learn slowly or not at all.
```

---

## Part 3: Task-Specific Patterns

### Exploratory Tasks (Novel Domains)

```
Characteristics:
  - Domain is new or unstable
  - Outcome signals are noisy or delayed
  - Goal is understanding, not optimization
  - Surprises are valuable

Recommended Pattern:
  1. Recall with ATTENTION frame, top_k=20 (broad)
  2. Expand graph aggressively (recover distant context)
  3. Remember many things (let decay filter later)
  4. Keep outcome weights low (0.3-0.5, domain is unstable)
  5. Tag liberally with "hypothesis" (mark uncertainty)
  6. Curate less frequently (let learning accumulate first)
  7. Periodically reflect: "What surprised me? Why?"
  8. Convert surprises to design experiments
```

---

### Execution Tasks (Known Pattern)

```
Characteristics:
  - Domain is stable and well-understood
  - Outcome signals are clear (success/failure)
  - Goal is reliable, repeatable results
  - Surprises indicate bugs, not learning

Recommended Pattern:
  1. Recall with TASK frame, top_k=5 (focused)
  2. Don't expand graph (stay on proven path)
  3. Remember only significant deviations
  4. Keep outcome weights high (0.8-1.0, signals are clear)
  5. Tag conservatively (only what transfers)
  6. Curate frequently (keep knowledge sharp)
  7. Monitor for pattern drift (domain changing?)
  8. Escalate surprises as anomalies
```

---

### Reasoning Tasks (Complex Decisions)

```
Characteristics:
  - Outcome depends on quality of reasoning, not luck
  - Signal is about reasoning process (not just result)
  - Goal is intellectual coherence
  - Need to understand trade-offs

Recommended Pattern:
  1. Recall with TASK + ATTENTION frames (goal + context)
  2. Expand moderately (connected ideas matter)
  3. Remember the reasoning path, not just conclusion
  4. Keep outcome weight moderate (0.6-0.7)
  5. Tag reasoning patterns: "tradeoff_analysis", "constraint_reasoning"
  6. Curate to surface connected reasoning patterns
  7. Identify reasoning heuristics that worked (meta-learning)
  8. Refactor poor reasoning paths explicitly
```

---

### Values/Identity Conflicts

```
Characteristics:
  - Multiple values in tension
  - No obvious "right" answer
  - Outcome is about alignment with identity, not objective success
  - Needs SELF frame as guide

Recommended Pattern:
  1. Recall SELF frame (who are you?)
  2. Recall ATTENTION frame (what's the situation?)
  3. Identify which constitutional blocks apply
  4. Design synthesis (how to honor multiple values?)
  5. Remember the resolution pattern for future conflicts
  6. Tag with "self/resolved_conflict"
  7. Reinforce SELF blocks that guided resolution
  8. Reflect: Does my identity feel coherent?
```

---

## Part 4: Anti-Patterns (What NOT to Do)

### Anti-Pattern 1: Remember Everything
```
SYMPTOM:
  - Hundreds of blocks with low confidence
  - Retrieve returns 50+ irrelevant results
  - Can't distinguish signal from noise

WHY IT FAILS:
  - Events don't transfer (won't happen exactly again)
  - Retrieval becomes useless (too much noise)
  - Agent can't learn because contradictions drown out patterns
  - Natural decay keeps knowledge that's unreinforced; noise accumulates faster

FIX:
  - Remember only patterns, not events
  - Remember only surprising outcomes
  - Remember only generalizable lessons
  - Trust decay to remove non-reinforced noise
```

---

### Anti-Pattern 2: No Reinforcement (Set It and Forget It)
```
SYMPTOM:
  - Knowledge gradually becomes stale
  - Agent forgets what worked
  - Confidence decays to zero after 12 days of non-use

WHY IT FAILS:
  - Without signals, all knowledge decays equally
  - Agent can't distinguish good patterns from bad patterns
  - Useful knowledge dies alongside noise
  - Learning doesn't compound

FIX:
  - After every significant action, collect outcome signal
  - Batch signals for reliability
  - Reinforce blocks that guided successful actions
  - Curate periodically to boost top patterns
```

---

### Anti-Pattern 3: Recall Only From One Frame
```
SYMPTOM:
  - Agent gets stuck in local optimum
  - Values drift because SELF frame never consulted
  - Tasks fail because broader context (WORLD) was ignored
  - Same solutions applied everywhere

WHY IT FAILS:
  - ATTENTION is task-focused; misses identity implications
  - SELF provides values; ignored in pure task execution
  - WORLD provides context; ignored in narrow focus
  - Single-frame agents are brittle

FIX:
  - Select frame based on task type (see Frame Selection Intelligence)
  - For conflicts, query multiple frames
  - For important decisions, consult SELF explicitly
  - For understanding, query WORLD context
```

---

### Anti-Pattern 4: Generic Tags
```
SYMPTOM:
  tags = ["learned", "important", "fact"]

WHY IT FAILS:
  - Tags become noise (every block has same tags)
  - Can't filter or categorize
  - Recall queries return everything
  - No semantic structure

FIX:
  - Semantic hierarchy: domain/category/subcategory
  - Examples: "programming/python/concurrency", "pattern/optimization", "self/value"
  - Specific enough to enable filtering
  - General enough to transfer
```

---

### Anti-Pattern 5: Ignore Contradictions
```
SYMPTOM:
  - Recall returns Block A and Block B that contradict
  - Agent picks one arbitrarily
  - Contradiction festers, affecting future decisions

WHY IT FAILS:
  - Contradictions indicate incomplete model
  - Ignoring them compounds confusion
  - Agent makes decisions based on unstable foundation
  - Error accumulates over time

FIX:
  - Flag contradictions immediately
  - Design resolution experiment
  - Understand context via SELF and WORLD frames
  - Resolve explicitly, then re-learn
  - Mark resolved contradiction as closed (prevents re-opening)
```

---

### Anti-Pattern 6: Fixed Decay Everywhere
```
SYMPTOM:
  - All blocks use λ=0.01 (2.9 day half-life)
  - Constitutional principles decay like event facts
  - Stable domain knowledge decays as fast as exploratory hypothesis

WHY IT FAILS:
  - Some knowledge is core (constitution) and should be permanent
  - Some knowledge is proved (high confidence) and should be durable
  - Some knowledge is exploratory (low confidence) and should be ephemeral
  - Fixed decay treats all knowledge equally

FIX:
  - Adaptive decay profiles based on stability
  - Constitutional blocks: λ = 0.00001 (permanent)
  - High-confidence blocks: λ = 0.001 (durable, ~28 days)
  - Medium-confidence: λ = 0.01 (standard, ~3 days)
  - Experimental: λ = 0.05 (ephemeral, ~7 hours)
  - Confidence should influence decay rate
```

---

## Part 5: System-Level Patterns

### Knowledge Graph Lifecycle

```
PHASE 1: SPARSE (First 50 blocks)
  - Wide exploration is appropriate
  - Don't specialize yet
  - Top-k=20 for retrieval (need diversity)
  - Low curation frequency (let accumulate)
  - Goal: Build foundational patterns

PHASE 2: GROWTH (50-200 blocks)
  - Patterns emerging
  - Connections forming
  - Can start specializing
  - Top-k=10-15 (still broad but focused)
  - Regular curation (clean up noise)
  - Goal: Densify graph, resolve contradictions

PHASE 3: MATURE (200+ blocks)
  - Stable patterns evident
  - Graph is well-connected
  - Can specialize deeply
  - Top-k=5-10 (narrow, high-quality)
  - Frequent curation (maintain quality)
  - Goal: Optimize for reliability, not discovery

TRANSITION SIGNALS:
  Phase 1 → 2: Graph density > 0.3 (many edges forming)
  Phase 2 → 3: New blocks per session < 5 (learning slowing)
              Confidence of top blocks > 0.7 (patterns are reliable)
```

---

### Session Management Pattern

```
SESSION START (first recall):
  1. Recall recent context
     recent = recall(query="what did I learn last session?",
                     frame="short_term",
                     hours=24)
  2. Review blocks created in last 24h
  3. Check for unresolved contradictions
  4. Check for unresolved experiments

DURING SESSION:
  1. Normal operation (remember, recall, outcome cycles)
  2. Batch outcomes when possible
  3. Note surprising observations

SESSION END:
  1. Curate accumulated blocks (INBOX → ACTIVE)
  2. Reinforce top patterns (recently used blocks)
  3. Reflect: "What worked? What surprised me?"
  4. Convert insights to new blocks
  5. Design next session's experiments

REFLECTION TEMPLATE:
  - How many new blocks? (learning rate healthy?)
  - Any unresolved contradictions? (model coherence?)
  - Which patterns did I rely on? (knowledge quality?)
  - What surprised me? (learning opportunities?)
  - What should I curate or archive?
```

---

### Multi-Domain Agents

```
CHALLENGE:
  Agent operates in multiple domains (e.g., software + operations + writing)
  Different domains have different stability, signal clarity, transferability

PATTERN 1: Domain-Specific Frames (Early)
  - Separate ATTENTION frames by domain
  - Keep SELF frame unified (values apply everywhere)
  - Curate per-domain
  - Rationale: Domains are still separate; consolidate later

PATTERN 2: Cross-Domain Connectors (Growth)
  - When patterns transfer across domains, link explicitly
  - Example: "Python concurrency → patterns transfer to → async systems design"
  - Tag transfers: "cross_domain/software_to_systems"
  - Rationale: Find unexpected connections

PATTERN 3: Meta-Knowledge (Mature)
  - Create blocks about patterns that apply everywhere
  - Example: "When facing novel problem, structure it as:
            known boundaries, variables, constraints, then enumerate states"
  - Tag as "meta/problem_solving"
  - Rationale: Solidify transferable reasoning

PATTERN 4: Selective Integration (Optimization)
  - Merge ATTENTION frames when domains interleave
  - Keep separate frames for truly independent domains
  - Monitor: merged frames should have cleaner graph
  - Rationale: Humans often find surprising connections
```

---

## Part 6: Implementation Heuristics

### "Remember When Surprised" Heuristic

```python
def should_remember(observation, expectation, domain_volatility):
    surprise = compute_surprise(observation, expectation)

    threshold = {
        "stable_domain": 0.1,      # High bar; must be surprising
        "dynamic_domain": 0.3,     # Lower bar; expect surprises
        "exploratory": 0.0         # Remember everything surprising
    }[domain_volatility]

    return surprise > threshold[domain_volatility]
```

---

### "Start Broad, Then Narrow" Retrieval Heuristic

```python
def adaptive_recall(query, task_type, domain_understanding):
    if domain_understanding == "new":
        top_k = 20
        expand_graph = True
        # Wide net to build understanding
    elif domain_understanding == "moderate":
        top_k = 10
        expand_graph = True if task_type == "reasoning" else False
        # Balanced approach
    else:  # "deep"
        top_k = 5
        expand_graph = False
        # Focused, rely on known patterns

    return recall(query, top_k=top_k, expand_graph=expand_graph)
```

---

### "Silence is Signal" Observation Heuristic

```python
def interpret_empty_recall(query, task_type):
    if len(recall(query)) == 0:
        action = {
            "exploration": "great! explore this gap",
            "execution": "risky—be careful, untested path",
            "high_stakes": "STOP. Design experiment first."
        }[task_type]

        return action
```

---

### "Batch Outcomes for Signal Clarity" Heuristic

```python
def should_batch_outcomes(outcomes):
    if len(outcomes) < 3:
        return False  # Too few to average meaningfully

    variance = std(outcomes)
    if variance > 0.2:
        return False  # Inconsistent; don't average

    return True  # Consistent signal; batch it
```

---

### "Confidence != Recency" Calibration

```python
def calibrate_confidence(observation_count, consistency, contradiction_count):
    """
    Confidence reflects actual reliability, not how fresh the knowledge is.
    """
    base_confidence = min(0.95, observation_count * 0.15)  # Caps at 3 observations

    consistency_boost = (1.0 - contradiction_count * 0.2)  # Contradictions lower confidence

    final_confidence = base_confidence * consistency_boost

    return max(0.1, min(0.95, final_confidence))  # Clamp to [0.1, 0.95]
```

---

## Summary: The 12 Key Agent Usage Patterns

1. **Remember after surprise** — surprises indicate learning opportunities
2. **Remember patterns, not events** — patterns transfer; events don't
3. **Remember connections** — isolated knowledge decays; connections compound
4. **Tag hierarchically** — enables semantic recall and filtering
5. **Frame selection is task-dependent** — different tasks need different frames
6. **Query semantically** — intent-based queries beat keyword matching
7. **Handle contradictions recursively** — treat as model gaps, not failures
8. **Silence is signal** — empty recall is a knowledge gap, not a failure
9. **Expand graph when insufficient** — recover related-but-not-similar context
10. **Signal = expectation - observation** — surprise drives learning
11. **Weight by signal confidence** — tight loops weight more than loose loops
12. **Batch outcomes to reduce noise** — averaging reveals true signal

---

## Next: Operationalizing the Cognitive Loop

These patterns are the "how" of using elfmem effectively. Next, we'll explore the "why" and "when"—the 10 constitutional blocks and how to operationalize them into concrete behaviors, decision-making frameworks, and reflection practices.

