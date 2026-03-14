# Agent Discipline — Prompt Instructions for Self-Calibrating elfmem Agents

## What This Is

**Agent discipline** is the set of behavioral instructions that tells an agent
*when* and *how* to use elfmem so that memory self-calibrates over time. Without
discipline, elfmem is a passive store. With discipline, it becomes a learning
system that gets better at guiding the agent with every cycle.

This document contains copy-pasteable prompt instructions at three tiers of
complexity. Use the tier that fits your agent's needs.

---

## The Core Principle

Every time an agent acts, it creates a learning opportunity:

```
RECALL → EXPECT → ACT → OBSERVE → CALIBRATE → ENCODE
```

**Calibration** is the step most agents skip. It means: tell elfmem which
recalled blocks actually helped, which were noise, and what surprised you.
Without this step, all knowledge decays equally and the memory never improves.

---

## Tier 1: Basic Discipline (2 instructions)

Minimum viable memory usage. Suitable for simple agents, quick tasks, or
getting started.

### Prompt Instructions (copy-paste into system prompt)

```markdown
## Memory Discipline

Before starting work:
  elfmem_recall("<your task description>", frame="attention")
  Read the returned blocks. Use them to inform your approach.

After completing work, if something surprised you:
  elfmem_remember("<what you expected vs what happened>. Pattern: <the lesson>",
                  tags=["domain/<area>", "pattern/<type>"])
```

### What This Achieves

- Agent has context before acting (avoids reinventing known solutions)
- Surprising outcomes get captured (the highest-value learning moments)
- Everything else handled by elfmem's natural decay

### What It Misses

- No outcome signaling (all blocks decay equally)
- No frame selection (always uses ATTENTION)
- No session-level reflection

---

## Tier 2: Standard Discipline (6 instructions)

Adds inline calibration and frame selection. The sweet spot for most agents.
Suitable for team agents, recurring tasks, and projects that span multiple
sessions.

### Prompt Instructions (copy-paste into system prompt)

```markdown
## Memory Discipline

### Before Each Task

1. IDENTIFY task type and select frame:
   - Novel problem / exploration     → frame="attention", top_k=10
   - Executing a known pattern       → frame="task",      top_k=5
   - Values or identity conflict     → frame="self",      top_k=5
   - Understanding context           → frame="attention", top_k=10
   - Quick fact lookup               → frame="attention", top_k=3

2. RECALL relevant knowledge:
   elfmem_recall("<task description>", frame=<selected>, top_k=<selected>)

3. SET EXPECTATION (internal, before acting):
   "Based on what I recalled, I expect <specific prediction about outcome>."

### After Each Task

4. CALIBRATE recalled blocks:
   For each block returned by recall:
   - Block guided my work effectively →
     elfmem_outcome([block_id], signal=0.85, source="used_in_work")
   - Block was recalled but I ignored it →
     elfmem_outcome([block_id], signal=0.45, source="not_used")
   - Block actively misled me →
     elfmem_outcome([block_id], signal=0.15, source="misleading")

5. ENCODE surprises (when |outcome - expectation| is significant):
   elfmem_remember(
     "Expected <X>, observed <Y>. Pattern: <transferable lesson>",
     tags=["domain/<area>", "pattern/<type>"]
   )

6. CONSOLIDATE at natural pauses:
   elfmem_dream()
```

### What This Achieves

- Frame selection matches task type (50%+ retrieval improvement)
- Useful blocks get reinforced, noise decays faster
- Expectations make surprise detection rigorous
- Knowledge consolidates at pause points

### Signal Reference

| Outcome | Signal | When |
|---------|--------|------|
| Block guided successful work | 0.80–0.95 | Used it, outcome was good |
| Block was relevant but not decisive | 0.55–0.70 | Informed thinking, didn't drive action |
| Block recalled but not used | 0.40–0.50 | Retrieved, ignored as irrelevant |
| Block set wrong expectation | 0.10–0.20 | Relied on it, outcome contradicted it |
| Block actively caused failure | 0.00–0.10 | Followed its guidance, things broke |

---

## Tier 3: Full Discipline (12 instructions)

Adds session lifecycle, metrics tracking, session reflection, and
meta-learning. For long-running agents, team agents, and agents that need to
improve measurably over time.

### Prompt Instructions (copy-paste into system prompt)

```markdown
## Memory Discipline

### Session Start (once per work session)

1. CHECK system health:
   elfmem_status()
   If suggestion says to consolidate or curate, do it before starting.

2. RECALL recent context:
   elfmem_recall("my recent work and decisions", frame="attention", top_k=5)

3. GROUND in identity:
   elfmem_recall("my role and principles", frame="self", top_k=3)

4. INITIALIZE session metrics (track internally):
   recalls_made = 0
   blocks_used = 0
   blocks_ignored = 0
   surprises = 0
   gaps = 0

### Before Each Task

5. SELECT frame by task type:
   | Task Type           | Frame     | top_k | Notes                    |
   |---------------------|-----------|-------|--------------------------|
   | Novel / exploration | attention | 10-20 | Broad, expect noise      |
   | Known execution     | task      | 5     | Focused, trust results   |
   | Identity conflict   | self      | 5     | Values-guided            |
   | Context building    | attention | 10    | Moderate breadth         |
   | Quick lookup        | attention | 3     | Fast, specific           |

6. RECALL and SET EXPECTATION:
   result = elfmem_recall("<task description>", frame=<selected>)
   recalls_made += 1

   Expectation: "I expect <specific prediction>."

   If recall returns nothing useful:
     gaps += 1
     Note: "Knowledge gap: <what I needed but didn't find>"

### After Each Task

7. INLINE CALIBRATE every recalled block:
   For each block from the most recent recall:
     if used:
       elfmem_outcome([id], signal=0.85, source="used")
       blocks_used += 1
     elif ignored:
       elfmem_outcome([id], signal=0.45, source="not_used")
       blocks_ignored += 1
     elif misleading:
       elfmem_outcome([id], signal=0.15, source="misleading")
       blocks_ignored += 1

8. ENCODE if surprised (|observation - expectation| is significant):
   elfmem_remember(
     "Expected <X>, observed <Y>. Pattern: <transferable lesson>",
     tags=["calibration/surprise", "domain/<area>"]
   )
   surprises += 1

9. ENCODE knowledge gaps discovered:
   elfmem_remember(
     "Gap: needed knowledge about <X> but nothing was available. "
     "Resolved by <what I did instead>.",
     tags=["calibration/gap", "domain/<area>"]
   )

### At Natural Pauses

10. CONSOLIDATE:
    elfmem_dream()

### Session End (once per work session)

11. COMPUTE session metrics:
    hit_rate = blocks_used / max(1, blocks_used + blocks_ignored)
    surprise_rate = surprises / max(1, recalls_made)

12. REFLECT and record:
    elfmem_remember(
      "Session: <what I worked on>. "
      "Hit rate: <X>%. Surprises: <N>. Gaps: <N>. "
      "Insight: <most important thing I learned>. "
      "Adjustment: <what I'll do differently next time>.",
      tags=["calibration/session", "meta-learning"]
    )
    elfmem_dream()
```

### Diagnostic Thresholds

Use these to detect when calibration itself needs adjustment:

| Metric | Healthy | Attention | Degraded |
|--------|---------|-----------|----------|
| Hit rate (blocks_used / total recalled) | > 60% | 40–60% | < 40% |
| Surprise rate (surprises / recalls) | 10–30% | 30–50% | > 50% |
| Gap rate (gaps / recalls) | < 10% | 10–25% | > 25% |
| Noise ratio (ignored / total recalled) | < 40% | 40–60% | > 60% |

**When degraded:**
- Hit rate low → Try different frames, more specific queries
- Surprise rate high → Knowledge base is stale, curate and re-learn
- Gap rate high → Domain not covered, seed more knowledge
- Noise ratio high → Lower top_k, use more focused frames

---

## Role-Specific Variations

### Lead Developer (Architecture / Planning)

```markdown
Additional discipline:
- Before writing a plan: recall("architecture decisions for <feature>", frame="self")
- After plan is accepted: remember the key decisions with tags=["team/architecture"]
- After plan fails: outcome(guiding_blocks, signal=0.2, source="plan_failed")
  and remember WHY it failed as a pattern
```

### Developer (Implementation)

```markdown
Additional discipline:
- Before implementing: recall("<feature> coding patterns", frame="task")
- After tests pass: outcome(guiding_blocks, signal=0.85, source="tests_pass")
- After tests fail: outcome(guiding_blocks, signal=0.3, source="tests_fail")
  and remember the failure pattern
- After refactoring: remember any new pattern discovered
```

### Tester (Test Writing)

```markdown
Additional discipline:
- Before writing tests: recall("testing patterns for <feature type>", frame="task")
- Before writing tests: recall("what broke last time in <area>", frame="attention")
- After tests catch a real bug: outcome(guiding_blocks, signal=0.95)
  and remember("Tests for <X> caught <Y>", tags=["testing/success"])
- After tests miss a bug: remember the gap as a pattern
```

---

## Anti-Patterns

### 1. Remember Everything
**Symptom:** Hundreds of blocks, recall returns noise.
**Fix:** Only remember patterns, surprises, and transferable lessons. Trust decay.

### 2. Never Calibrate
**Symptom:** All blocks decay equally. Good knowledge fades with bad.
**Fix:** Always outcome() after using recalled blocks. Even a simple used/not-used split helps enormously.

### 3. Always Same Frame
**Symptom:** ATTENTION for everything. Misses identity context, returns broad noise.
**Fix:** Match frame to task type (decision tree above).

### 4. Calibrate Everything as "Good"
**Symptom:** Signal inflation. Every block gets 0.85. Nothing differentiates.
**Fix:** Be honest. Unused blocks get 0.45. Misleading blocks get 0.15. Only genuinely useful blocks get high signals.

### 5. Skip Expectations
**Symptom:** Can't tell if you're surprised because you never predicted.
**Fix:** Before acting, state what you expect. After acting, compare. The gap IS the learning signal.

### 6. Forget to Dream
**Symptom:** Blocks stay in inbox, never consolidated, never retrievable by embedding.
**Fix:** elfmem_dream() at every natural pause. It's safe to call speculatively.

---

## How It All Fits Together

```
SESSION START
  │
  ├─ status() → health check
  ├─ recall(frame="attention") → recent context
  └─ recall(frame="self") → identity grounding
  │
  ▼
TASK LOOP (repeat for each task)
  │
  ├─ Select frame by task type
  ├─ recall() → get relevant blocks
  ├─ Set expectation
  │
  ├─ ═══ DO THE WORK ═══
  │
  ├─ Inline calibrate: outcome() each block (used/ignored/misleading)
  ├─ If surprised: remember(pattern)
  ├─ If gap found: remember(gap)
  └─ If pause: dream()
  │
  ▼
SESSION END
  │
  ├─ Compute metrics (hit rate, surprise rate, gaps)
  ├─ remember(session reflection + metrics)
  ├─ dream() → consolidate everything
  └─ (curate() if long session or retrieval quality degraded)
```

---

## Example: Full Cycle Walkthrough

**Scenario:** Dev-team agent implementing a new `recall()` optimization.

```
SESSION START:
  status() → "Memory healthy. No action required."
  recall("my recent work", frame="attention")
    → "Last session: implemented graph temporal decay. Hit rate 72%."
  recall("my coding principles", frame="self")
    → "Functional Python, ≤50 lines, fail fast, type hints everywhere."

TASK: Implement pre-filter optimization for recall.
  Frame: task (known execution pattern)
  recall("retrieval optimization patterns", frame="task", top_k=5)
    → Returns 5 blocks. Block A: "4-stage pipeline: pre-filter → vector → graph → score"
    → Block B: "Pre-filter reduces LLM calls by 95%"
    → Block C: (unrelated constitutional block about curiosity)
  Expectation: "Pre-filter should be a pure function, ≤50 lines, with type hints."

  ═══ IMPLEMENT ═══

  Outcome: Tests pass. Clean implementation. 42 lines.

  Calibrate:
    outcome([A.id], signal=0.90, source="guided_architecture")
    outcome([B.id], signal=0.85, source="confirmed_optimization_value")
    outcome([C.id], signal=0.40, source="not_used")

  No surprise (expectation matched). No gap. Move on.

TASK: Handle edge case — empty query string.
  recall("error handling edge cases for recall", frame="attention", top_k=5)
    → Returns 3 blocks. None about empty query specifically.
  Gap found!
  Expectation: "Empty query should return empty result, not raise."

  ═══ IMPLEMENT ═══

  Surprise: Discovered that empty query with SELF frame should still return
  constitutional blocks (identity is always relevant).

  Calibrate:
    outcome(block_ids, signal=0.45, source="not_directly_relevant")
  Encode surprise:
    remember("Empty query + SELF frame should return constitutional blocks. "
             "SELF frame is never truly empty — identity persists.",
             tags=["pattern/edge-case", "domain/retrieval"])
  Encode gap:
    remember("Gap: no prior knowledge about empty-query handling. "
             "Resolved by testing each frame with empty string.",
             tags=["calibration/gap", "domain/retrieval"])

SESSION END:
  Metrics: hit_rate=65%, surprises=1, gaps=1
  remember("Session: implemented recall pre-filter + empty query edge case. "
           "Hit rate 65%. 1 surprise (SELF frame + empty query). "
           "1 gap (empty query handling). "
           "Insight: SELF frame has special semantics worth documenting. "
           "Adjustment: check frame-specific edge cases earlier.",
           tags=["calibration/session", "meta-learning"])
  dream()
```

After a few sessions like this, the agent's memory will contain:
- Practical patterns that actually guided implementation (high confidence)
- Edge cases discovered through surprise (medium confidence, rising with reuse)
- Session-level meta-learning about its own calibration quality
- Gaps that were filled (preventing future agents from hitting the same wall)

---

## See Also

- `examples/calibrating_agent.py` — Python implementation of a self-calibrating agent
- `examples/decision_maker.py` — Simpler example focused on multi-frame decision-making
- `docs/agent_usage_patterns_guide.md` — The 20 core agent patterns for elfmem
- `docs/cognitive_loop_operations_guide.md` — Full cognitive loop framework
- `docs/CLAUDE_CODE_INTEGRATION.md` — Using elfmem with Claude Code teams
