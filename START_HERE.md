# START HERE

You now have a complete markdown-based simulation system ready to use.

---

## What Was Built

A document-driven simulation of the AMGS architecture with **5 complete explorations**
covering the major design areas:

1. **001:** Decay — how long knowledge survives
2. **002:** Scoring — ATTENTION frame correctness  
3. **003:** Frames — complete SELF assembly with graph
4. **004:** Self — hard gates vs. soft bias (MAJOR DECISION)
5. **005:** Decay 2.0 — staleness, interference, disuse

**~1,900 lines** of worked mathematical examples, design insights, and decision records.

---

## How to Start

### Option A: Quick Overview (15 minutes)
```
1. Read this file (2 min)
2. Read QUICKSTART.md (5 min)
3. Skim 004_self_interest_model.md (8 min)
```

You'll understand the design philosophy and the major self-interest decision.

### Option B: Deep Dive (1 hour)
```
1. Read QUICKSTART.md (5 min)
2. Read explorations 001-005 in order (45 min)
3. Review EXPLORATIONS.md (10 min)
```

You'll understand all major design decisions and see the mathematical evidence behind them.

### Option C: Exploration Mode (ongoing)
```
1. Pick an exploration that interests you
2. Ask me to run a variation
3. Compare results
4. Create a new exploration for unanswered questions
```

---

## Key Documents

| Document | Purpose | Length | Read When |
|----------|---------|--------|-----------|
| **QUICKSTART.md** | Overview, file structure, how to use | 250 lines | First |
| **SIMULATION_OVERVIEW.md** | Philosophy, phases, insights | 380 lines | After quickstart |
| **sim/README.md** | Detailed guide, formulas, conventions | 276 lines | When writing explorations |
| **sim/EXPLORATIONS.md** | Index, navigation, decisions made | 226 lines | To find topics |
| **sim/explorations/001-005** | The actual explorations | 1,355 lines | As questions arise |

---

## Major Decisions Made

From the 5 explorations, these design questions are **DECIDED**:

### 1. Self Uses Soft Bias, Not Hard Gates (Exploration 004)

**Decision:** Self influences decay and retrieval, but never blocks ingestion.

**Why:** Hard gates create echo chambers. Soft bias enables growth while preserving coherence.

**What it means:**
- Nothing is blocked from learning
- Self-aligned blocks decay slower and surface more
- Self grows naturally through reinforcement
- System can adapt to new domains

### 2. Reinforcement is Non-Optional (Exploration 001)

**Decision:** Knowledge dies in ~12.5 days without reinforcement.

**Why:** Standard blocks (λ=0.01) need weekly reinforcement or they're pruned.

**What it means:**
- Every retrieval MUST reinforce blocks
- Unused knowledge fades naturally (good)
- Graph edges and good consolidation are critical
- The system self-selects for useful knowledge

### 3. Frame Weights Are Correct (Exploration 002)

**Decision:** The spec weights work. Query similarity dominates (0.35).

**Why:** With diverse corpus, ATTENTION correctly surfaces query-relevant blocks.

**What it means:**
- Can proceed with implementation confidence
- ATTENTION frame will work as designed
- The "bug" from Python sim was corpus composition, not formula

### 4. Usage Can Override Self-Tags (Exploration 003)

**Decision:** Blocks accessed frequently can enter the SELF frame.

**Why:** Reinforcement is more powerful than explicit self-tagging.

**What it means:**
- Identity is dynamic, not static
- What you use shapes who you become
- Self grows through usage patterns
- This is the self-organising property at work

### 5. Decay is Multi-Factor (Exploration 005)

**Decision:** Use session-aware decay in Phase 1, split into staleness/interference/disuse later.

**Why:** Pure time-based decay kills knowledge on holidays (wrong).

**What it means:**
- Phase 1: Only count active session hours
- Phase 2: Add interference tracking
- Phase 3: Full staleness/interference/disuse separation
- Knowledge survives across breaks

---

## Open Questions (Not Yet Decided)

These are worth exploring but not blockers:

- Should `is_self_component` get a direct scoring bonus?
- Should ATTENTION frame exclude self-tagged blocks?
- What's the right idle_factor for dual-rate decay?
- Does incremental assembly work better than top-K?
- Can we measure self-alignment empirically?

---

## How to Use Going Forward

### To Learn About a Topic

Find it in EXPLORATIONS.md. Example:
```
"What's the decay situation?" → 001 + 005
"How does scoring work?" → 002 + 003
"What about self?" → 004
```

### To Test a Variation

Ask for a specific change:
```
"Run variation: what if decay_lambda was 0.05 instead of 0.01?"
"What if we raise ATTENTION similarity weight to 0.50?"
"Can you test the incremental assembly idea from variation 2 of 002?"
```

### To Create a New Exploration

Ask a question:
```
"Create an exploration: what happens if self-aligned blocks use durable decay automatically?"
"Explore: can we measure how much reinforcement is enough to prevent pruning?"
"Test the idea from open question 1: should is_self_component get a scoring bonus?"
```

---

## The Three Phases

### Phase 1: Explorations ✓ DONE
- [x] 5 complete explorations
- [x] Design decisions locked
- [x] Open questions identified
- [x] Mathematical validation

### Phase 2: Playgrounds (NEXT)
- [ ] Organize by subsystem (decay, scoring, frames, graph, lifecycle)
- [ ] Add test case assertions
- [ ] Run empirical variations
- [ ] Fine-tune weights
- [ ] **Output:** Test suite specifications

### Phase 3: Executable Specs (THEN)
- [ ] Write code generation source (one spec per module)
- [ ] Include type definitions, algorithms, test cases
- [ ] **Output:** Code + tests generated directly from specs

---

## Next Steps This Week

**Option A: Understand the Design**
1. Read QUICKSTART.md
2. Read explorations 001 and 004
3. Skim EXPLORATIONS.md
**Time:** 30 minutes

**Option B: Deep Understanding**
1. Read all of QUICKSTART.md, SIMULATION_OVERVIEW.md
2. Read all 5 explorations
3. Review open questions in EXPLORATIONS.md
**Time:** 1 hour

**Option C: Start Exploring**
1. Read QUICKSTART.md
2. Pick one exploration
3. Run 2-3 variations
4. Ask about design implications
**Time:** 1-2 hours

**Recommended:** Option B (1 hour investment for complete confidence in the design)

---

## File Locations

```
/Users/emson/Dropbox/devel/projects/ai/elf0_mem_sim/
├── START_HERE.md ← YOU ARE HERE
├── QUICKSTART.md ← READ THIS NEXT
├── SIMULATION_OVERVIEW.md
├── sim/
│   ├── README.md
│   ├── EXPLORATIONS.md
│   └── explorations/
│       ├── _template.md
│       ├── 001_basic_decay.md
│       ├── 002_confidence_trap.md
│       ├── 003_scoring_walkthrough.md
│       ├── 004_self_interest_model.md
│       └── 005_decay_sophistication.md
```

---

## Ready?

Pick an option above and ask me any questions as they come up. The simulation is ready to explore.
