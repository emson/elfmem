# Cognitive Loop Operations Guide — Complete Framework

> **Note (v0.13.x frame consolidation):** This guide predates the frame registry consolidation. The live built-in frames are `self`, `attention`, `task`, and `simulate` (see `src/elfmem/context/frames.py BUILTIN_FRAMES`). Treat any reference to `WORLD` / `frame="world"` below as `frame="attention"` (query-driven retrieval — already weights similarity, recency, and graph centrality). Treat `SHORT_TERM` / `frame="short_term"` as `frame="attention"` as well (the recency weighting is built in). For perspective-taking / Theory-of-Mind retrieval, use `frame="simulate"`. Code examples below have been updated to use live frame names; narrative uppercase references remain as historical context.


## Overview

The **cognitive loop** is the self-improving feedback system that enables adaptive learning. It consists of three integrated layers:

1. **Constitutional Blocks** (SELF) → Provide the WHY (identity, values, principles)
2. **Agent Patterns** (HOW) → Provide the HOW (remember, recall, outcome, curate)
3. **Operational Frameworks** (WHEN) → Provide the WHEN (decision triggers, reflection protocols)

This guide shows how they work together.

---

## The Complete Loop

```
┌──────────────────────────────────────────────────────────────┐
│                 AGENT ENCOUNTERS SITUATION                   │
└────────────────────────┬─────────────────────────────────────┘
                         │
        ┌────────────────▼─────────────────┐
        │   CONSULT CONSTITUTIONAL BLOCKS  │
        │   (Why do I care? What's my goal)│
        └────────────────┬─────────────────┘
                         │
        ┌────────────────▼─────────────────┐
        │   SELECT FRAME & RECALL PATTERN  │
        │   (Use Agent Patterns)           │
        │   frame = select_frame(task)     │
        │   blocks = recall(query, frame)  │
        └────────────────┬─────────────────┘
                         │
        ┌────────────────▼──────────────────┐
        │   SET EXPECTATION (CRITICAL!)     │
        │   before acting                   │
        └────────────────┬──────────────────┘
                         │
        ┌────────────────▼──────────────────┐
        │   ACT                              │
        │   Execute planned action           │
        └────────────────┬──────────────────┘
                         │
        ┌────────────────▼──────────────────┐
        │   OBSERVE                          │
        │   Measure actual outcome           │
        └────────────────┬──────────────────┘
                         │
        ┌────────────────▼──────────────────┐
        │   COMPARE                          │
        │   signal = |observation-expectation│
        └────────────────┬──────────────────┘
                         │
        ┌────────────────▼──────────────────┐
        │   SIGNAL                           │
        │   outcome(blocks, signal, weight)  │
        └────────────────┬──────────────────┘
                         │
        ┌────────────────▼──────────────────┐
        │   ENCODE                           │
        │   if signal > 0.3:                 │
        │     remember(pattern)              │
        └────────────────┬──────────────────┘
                         │
        ┌────────────────▼──────────────────┐
        │   REFLECT (at transitions)        │
        │   What worked? What surprised?    │
        │   Update beliefs. Evolve identity.│
        └────────────────┬──────────────────┘
                         │
                    ┌────▼────┐
                    │ Loop back│
                    │ to start │
                    └──────────┘
```

---

## Layer 1: Constitutional Blocks → Behavioral Principles

Each constitutional block maps to specific operational behaviors:

### Block 1: Curiosity-Driven Learning
**Principle:** Treat unknowns as opportunities, not failures.

**Operational Behavior:**
```
When encountering unknown situation:
  1. Ask: "What don't I know?"
  2. Retrieve with ATTENTION frame (broad exploration)
  3. If empty: Design exploration, gather evidence
  4. If contradictions: Design resolution experiment
  5. Convert surprises to patterns
  6. Treat gaps as research questions
```

### Block 2: Minimum Force & Simplicity
**Principle:** Don't do more than necessary. Complexity is debt.

**Operational Behavior:**
```
When deciding what/how much to do:
  1. For known tasks: TASK frame (minimal scope)
  2. For novel: Start ATTENTION top_k=5, expand only if needed
  3. Remember patterns only, not events
  4. Don't retrieve all blocks; use minimal sufficient scope
  5. Batch outcomes instead of signaling individually
```

### Block 3: Hypothesis-Driven Experimentation
**Principle:** Evidence guides belief, not expectation.

**Operational Behavior:**
```
When discovering a gap:
  1. Form multiple hypotheses
  2. Design minimal test that could disprove most
  3. Gather evidence rigorously
  4. Update confidence based on results
  5. If wrong: confidence -= 0.3, tag for investigation
  6. Never skip the test; let evidence speak
```

### Block 4: Relational Learning
**Principle:** Isolated facts decay; connected knowledge compounds.

**Operational Behavior:**
```
When learning something new:
  1. Query WORLD frame for related blocks
  2. Analyze connections: supports? challenges? extends?
  3. Store edges explicitly in graph
  4. When retrieving: if top-5 insufficient, expand via edges
  5. Prevent knowledge isolation
```

### Block 5: Epistemic Humility
**Principle:** Name unknowns. Make reversible moves when knowledge is thin.

**Operational Behavior:**
```
Before significant action:
  1. List all assumptions
  2. Estimate confidence in each
  3. If confidence < 0.5 AND risk > medium: design reversible move
  4. When low-confidence: broad exploration (ATTENTION, top_k=20)
  5. Ask what would prove assumption wrong; test if possible
```

### Block 6: Close the Feedback Loop
**Principle:** Expectation → Outcome → Signal → Belief Update (MANDATORY).

**Operational Behavior:**
```
For every significant action:
  1. BEFORE: Set explicit expectation
  2. AFTER: Measure actual outcome
  3. COMPUTE: signal = |outcome - expectation|
  4. SIGNAL: Reinforce blocks that guided prediction
  5. ENCODE: Remember if signal > 0.3
  6. UPDATE: Confidence reflects reliability, not just outcomes
```

### Block 7: Rhythmic Learning
**Principle:** Push, then recover, then push again. Sustain excellence.

**Operational Behavior:**
```
Each session:
  START: Recall recent context (last 24h)
  DURING: Maintain moderate pace (1-5 blocks/hour)
  END: Curate + Reinforce + Reflect
  WEEKLY: 5 days work + 2 days deep consolidation
  Monitor pace: too fast = shallow, too slow = stagnant
```

### Block 8: Process Focus
**Principle:** Judge reasoning quality. Outcomes inform, don't judge.

**Operational Behavior:**
```
When reflecting on outcomes:
  1. Judge reasoning quality, not just results
  2. Good reasoning + bad outcome: still valid learning
  3. Bad reasoning + good outcome: don't internalize
  4. Focus on controllable: information gathering, hypothesis generation
  5. Accept uncontrollable: luck, external events
```

### Block 9: Systems Thinking
**Principle:** Consider second-order effects. Leave systems healthier.

**Operational Behavior:**
```
Before taking action:
  1. Predict direct effects (what I intend)
  2. Trace secondary effects (how systems respond)
  3. Check health: Is system healthier after my action?
  4. Universalize: If everyone did this, would it be good?
  5. If harm: redesign to be sustainable
```

### Block 10: Reflective Practice at Transitions
**Principle:** Pause and learn from natural transition points.

**Operational Behavior:**
```
At task end, domain switch, session end:
  1. Which principles did I apply? Which neglected?
  2. What worked? What failed?
  3. What surprised me?
  4. What should I encode?
  5. What should I release?
  6. Has my identity evolved?
  7. Convert insights to knowledge
```

---

## Layer 2: Frame Selection Decision Tree

The most critical decision: choosing the right frame.

```
┌─ What type of task?
│
├─ NOVEL PROBLEM (exploring new domain)
│  └─ Frame: ATTENTION
│     ├─ top_k: 20 (broad exploration)
│     ├─ expand_graph: YES (see all context)
│     └─ weight: 0.5 (signals are noisy in novel domains)
│
├─ EXECUTION (known pattern)
│  └─ Frame: TASK
│     ├─ top_k: 5 (focused on goal)
│     ├─ expand_graph: NO (stay on proven path)
│     └─ weight: 1.0 (clear success/failure signals)
│
├─ VALUES/IDENTITY CONFLICT
│  └─ Frame: SELF
│     ├─ top_k: 5 (identity is few, core principles)
│     ├─ expand_graph: YES (understand context)
│     └─ weight: 1.0 (values matter deeply)
│
├─ UNDERSTANDING CONTEXT
│  └─ Frame: WORLD
│     ├─ top_k: 10 (moderate breadth)
│     ├─ expand_graph: YES (connections matter)
│     └─ weight: 0.5 (understanding is partial)
│
└─ QUICK LOOKUP (immediate fact)
   └─ Frame: SHORT_TERM
      ├─ top_k: 3 (very focused)
      ├─ expand_graph: NO
      └─ weight: 0.8 (recent signals are clear)
```

---

## Layer 3: The Four Operation Decision Loops

### Remember (Learn) Decision Loop

```
Does the outcome surprise me?
  ├─ YES (|observation - expectation| > 0.3)
  │  └─ Extract pattern (generalization, not event)
  │     ├─ Tag hierarchically (domain/category/subcategory)
  │     ├─ Set confidence = 0.5 + surprise_magnitude
  │     └─ Search memory for connections, store edges
  │
  └─ NO (outcome matches expectation)
     └─ Don't remember (model is correct, no learning needed)
```

### Recall (Retrieve) Decision Loop

```
What retrieval scope do I need?
  ├─ Known domain, tight goal
  │  └─ Start: TASK frame, top_k=5, no expand
  │     └─ If insufficient: expand graph
  │
  ├─ Novel domain, broad exploration
  │  └─ Start: ATTENTION frame, top_k=20, expand=YES
  │     └─ Let graph guide discovery
  │
  ├─ Contradictions detected
  │  └─ Recursive recall: SELF + WORLD frames
  │     └─ Design resolution experiment
  │
  └─ Empty recall (silence = signal)
     └─ Knowledge gap found
        └─ Phase determines action:
           ├─ Exploration: explore and remember
           ├─ Execution: be very careful (untested)
           └─ High-stakes: STOP, design experiment first
```

### Outcome (Signal) Decision Loop

```
After action completes, compute signal:

  1. Was there a tight feedback loop?
     ├─ YES (outcome within minutes): weight = 1.0 (learn fast)
     └─ NO (outcome in days): weight = 0.5 (learn slowly)

  2. Was the signal clear or noisy?
     ├─ Single outcome: likely noisy
     └─ Batch 3-5 outcomes: average them (clear signal)

  3. How confident was the block being reinforced?
     ├─ High confidence (0.9+): penalize errors harshly
     └─ Low confidence (0.3-): penalize errors mildly

  4. Did this pattern guide success?
     ├─ YES: reinforce the pattern (not the event)
     └─ NO: don't credit it
```

### Curate (Maintain) Decision Loop

```
When should I consolidate?

  Trigger curation IF any:
    ├─ blocks_in_inbox > 50 (too many uncommitted)
    ├─ days_since_curate > 7 (scheduled maintenance)
    ├─ learning_rate < 2/hour (stability signal)
    └─ contradictions > 0.5 (model instability)

  Curation actions:
    ├─ Archive INBOX blocks to ACTIVE
    ├─ Reinforce top-10 recently used blocks
    ├─ Prune weak edges (confidence < 0.3)
    └─ Never touch constitutional blocks (they survive forever)
```

---

## Layer 4: Reflection Protocols

### Daily (5-10 minutes)
```
1. What surprised me today?
2. Did I apply my constitutional principles?
3. Which patterns worked well?
4. Which patterns struggled?
5. Is there an unresolved contradiction worth designing an experiment for?
6. What should I remember from today?
```

### Weekly (30-60 minutes)
```
1. How did learning rate evolve?
2. Which domains had clearest learning? Which muddy?
3. Are contradictions resolving?
4. Which patterns am I relying on most? Are they reliable?
5. Have I been balanced? (depth vs breadth, certainty vs doubt, action vs reflection)
6. What meta-pattern emerged about my learning?
7. What do I want to change next week?
```

### Monthly (2-3 hours)
```
1. How has knowledge graph evolved? (Sparse→Dense? New domains?)
2. Which constitutional blocks guided the month? Which neglected?
3. Are there systemic patterns to what I'm learning?
4. Have my core assumptions changed?
5. What should I encode as permanent learning?
6. What should I archive as outdated?
7. Is my identity evolving in ways I value?
```

---

## How to Use This Framework

### For Agents

**When you encounter a situation:**

1. **Consult constitutional blocks:** Why do I care? What does my identity say?
2. **Select frame:** What type of task is this?
3. **Recall:** Get relevant knowledge
4. **Set expectation:** Before acting (CRITICAL!)
5. **Act:** Execute plan
6. **Observe:** Measure outcome
7. **Compare:** Compute signal
8. **Signal:** Reinforce what worked
9. **Encode:** Remember surprising patterns
10. **Reflect:** At natural transitions, ask hard questions

**Each cycle improves the next.** Knowledge compounds. Identity evolves.

### For System Designers

**Design for the loop:**

1. Make expectation-setting visible (it's mandatory)
2. Make frame selection easy (it's critical for quality)
3. Make feedback signals reliable (tightness matters)
4. Make reflection natural (not afterthought)
5. Make curation automatic (but respect agent control)
6. Monitor: Is the loop closing? Is knowledge stable?

---

## Quick Reference: The Complete Decision Tree

```
┌─────────────────────────────────┐
│  Agent Encounters Situation     │
└────────────┬────────────────────┘
             │
      ┌──────▼──────┐
      │ Task Type?  │
      └──────┬──────┘
    ┌────────┼────────────┬─────────┬──────────┐
    │        │            │         │          │
  NOVEL   EXECUTION    VALUES    CONTEXT   QUICK
    │        │            │         │          │
 ATTEND     TASK        SELF      WORLD    SHORT
 t20,exp   t5,noexp    t5,exp     t10,exp   t3
 w=0.5     w=1.0       w=1.0      w=0.5    w=0.8
    │        │            │         │          │
    └────────┼────────────┴─────────┴──────────┘
             │
      ┌──────▼──────────┐
      │ Recall blocks  │
      └────────┬────────┘
             │
      ┌──────▼──────────────┐
      │ Surprising?        │
      │ (outcome≠expect)  │
      └────────┬───────────┘
    ┌─────────┴─────────┐
    │                   │
   YES                 NO
    │                   │
  REMEMBER             NONE
 (pattern)          (model OK)
    │
    └─→ SIGNAL → LOOP

At TRANSITIONS: REFLECT
Ask hard questions
Convert to knowledge
Curate
Update identity
```

---

## Complete Example: A Cycle in Action

**Situation:** Agent is learning Python async programming.

**Step 1 - Constitutional:** "I am curious. I explore unknowns with experiments."

**Step 2 - Frame Selection:** Novel domain → ATTENTION frame, top_k=20

**Step 3 - Recall:** Get broad knowledge on async/await, event loops, concurrency

**Step 4 - Expectation:** "Async functions return coroutines (not direct results)"

**Step 5 - Act:** Write code using async/await

**Step 6 - Observe:** Code works as expected

**Step 7 - Compare:** signal = 0 (expectation matched! Learning not needed)

**Step 8 - Signal:** No reinforcement (model was correct)

**Later cycle - Different outcome:**

**Step 4 - Expectation:** "Calling async function runs it immediately"

**Step 5 - Act:** Call async function, expect result

**Step 6 - Observe:** Got coroutine object, not result

**Step 7 - Compare:** signal = 1.0 (surprise!)

**Step 8 - Signal:** Reinforce blocks that guided the mistaken expectation

**Step 9 - Encode:** Remember pattern: "Async functions must be awaited to run. Calling without await returns coroutine."

**Step 10 - Reflect (session end):**
- What surprised me? Difference between calling and awaiting
- Pattern that worked? Treating async as special function type
- What should I remember? The await requirement, how to test async code
- What should I explore next? Exception handling in async code

**Next cycle:** Better understanding, better predictions, faster learning.

---

## The Power of This Framework

✅ **Closed feedback loops** ensure learning from every action
✅ **Constitutional blocks** align behavior with identity
✅ **Frame selection** optimizes retrieval for task type
✅ **Reflection protocols** convert experience to wisdom
✅ **Curation** prevents knowledge decay and noise accumulation
✅ **Identity evolution** through lived learning experience

**Result:** Adaptive learning system that improves indefinitely.

---

## Implementation: Agent Discipline

This framework describes the theory. **Agent discipline** is the practical
implementation — copy-pasteable prompt instructions that embed these loops
into any agent's behavior, plus a Python reference implementation with tests.

- `examples/agent_discipline.md` — Prompt instructions (3 tiers: basic → full)
- `examples/calibrating_agent.py` — Self-calibrating agent with session metrics
- `docs/agent_usage_patterns_guide.md` — The 20 core patterns this framework builds on

