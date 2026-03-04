# The elfmem Design Simulation

## What Was Built

A comprehensive document-driven specification system for **elfmem** (ELF Memory), a Python library
for adaptive, self-aware memory systems in LLM agents.

Instead of writing code immediately, we use structured markdown files as the simulation medium,
where Claude reasons through the system's behavior using explicit mathematical computation.

This is the **specification and design validation phase** — 26 complete explorations building
confidence that the architecture works through rigorous design reasoning before implementation.

---

## What Is elfmem?

**elfmem** (ELF Memory) is a Python library that gives LLM agents adaptive, self-aware memory systems.

**Core capabilities:**
- **Knowledge graphs:** Blocks (immutable markdown), edges (relationships), graph expansion
- **Identity persistence:** SELF frame captures agent personality, values, constraints
- **Adaptive decay:** Knowledge survives longer when used, fades when ignored
- **Retrieval:** Hybrid 4-stage pipeline (pre-filter → vector search → graph expansion → composite score)
- **Lifecycle:** learn() → consolidate() → curate() → recall() with automatic transitions
- **LLM integration:** Configurable providers (OpenAI, Anthropic, Groq, local Ollama), structured outputs
- **Prompt customisation:** Override alignment, tagging, and contradiction detection prompts

**Target:** Single-agent systems, 50–500 blocks, SQLite backend, zero infrastructure

---

## The Complete Structure

```
elf0_mem_sim/
├── START_HERE.md                    ← Entry point
├── QUICKSTART.md                    ← Quick overview (5 min)
├── SIMULATION_OVERVIEW.md           ← This file
├── docs/
│   ├── amgs_architecture.md         (full specification ~ 1000 lines)
│   ├── amgs_instructions.md         (old Python sim reference)
│   └── notes.md                     (Python sim observations)
├── sim/
│   ├── README.md                    (explorations guide & formulas)
│   ├── EXPLORATIONS.md              (index of 26 explorations + decisions)
│   └── explorations/
│       ├── _template.md             (blank template)
│       ├── 001–022_core/            (architecture, storage, retrieval, layers)
│       ├── 023_agent_usage.md       (LLM agent patterns & SELF evolution)
│       ├── 024_system_refinement.md (design audit & unification)
│       ├── 025_llm_gateway.md       (LiteLLM, instructor, config)
│       └── 026_prompt_overrides.md  (custom prompts, per-call models)
└── (playgrounds/ and specs/ — Phase 2+)
```

---

## How It Works

### The Medium

Each exploration is a markdown file with:
1. **Setup** — small block definitions and parameters (YAML)
2. **Computation** — step-by-step worked math using architecture spec formulas
3. **Result** — final state, usually in YAML
4. **Insight** — what we learned about the design
5. **Variations** — ideas for follow-up explorations

The "execution engine" is Claude reasoning. Every number is computed explicitly and
can be verified or modified.

### The Workflow

```
Question → Create exploration → Compute math → Analyze → Record insights
   ↓                                                           ↓
What if we... ← Run variation ← Compare to baseline ← Design decision
```

### Why It Works

- **Transparent:** Every computation is visible and editable
- **Fast:** No code to write, compile, or debug — just markdown
- **Auditable:** Future implementers can verify the math against the simulation
- **Exploratory:** Easy to try variations and see how parameters affect behavior
- **Forkable:** Copy a file, change one number, recompute, compare
- **Specification-generating:** Simulations become test cases and code specs

---

## The 26 Explorations — By Category

**Core Architecture (001–022):** Memory blocks and decay (001–005), self-concept and tagging (006–012),
graph structures (013–014), context frames (015–016), storage layer (017–019), graph algorithms (020),
retrieval pipelines (021), four-layer architecture (022).

**Integration & Refinement (023–026):** Agent usage patterns (023), system audit and refinement (024),
LLM gateway and configuration (025), prompt customisation and overrides (026).

---

### 001: Basic Decay Showdown

**Core question:** How long do memory blocks with different decay profiles survive?

**Key finding:** Standard knowledge (λ=0.01) dies in 12.5 days without reinforcement.

**Why it matters:** Means the system MUST use memory during retrieval to keep knowledge alive.
This is a hard architectural requirement.

**Limitations:** Assumes pure wall-clock time, no activity awareness.

### 002: The Confidence Trap

**Core question:** Does ATTENTION frame return query-relevant blocks, or does self-tagging
cause identity blocks to dominate?

**Key finding:** ATTENTION works correctly. Query-similarity (0.35 weight) overpowers
confidence when corpus is diverse.

**Why it matters:** The weights in the spec are tuned correctly. The Python sim's "bug"
was corpus composition (too many identity blocks), not the formula.

**Design validation:** Can proceed with these weights in Phase 1.

### 003: Full SELF Frame Scoring Walkthrough

**Core question:** How does complete SELF frame assembly work with realistic metadata
and a small graph?

**Key finding:** A non-self-tagged block (high reinforcement) can infiltrate the SELF frame,
beating a self-tagged block with lower reinforcement.

**Why it matters:** Shows that **reinforcement is incredibly powerful**. Usage patterns can
override explicit self-tagging. This is interesting — is it a feature or a bug?

**Design implication:** The system's identity is not static. It grows with usage. This is
the self-organising property at work.

### 004: Self as Filter vs. Self as Context

**Core question:** Should self act as a **hard gatekeeper** (blocks non-aligned content),
or as a **soft context layer** (influences decay and retrieval)?

**Key finding:** Hard gates create echo chambers and are irreversible. Soft bias enables
growth while preserving self-coherence.

**Design decision:** Use three-layer interest model:
1. **Ingestion:** Only dedup, never gate
2. **Consolidation:** Compute self-alignment, use for confidence boost + decay profile
3. **Retrieval:** Optional SelfAlignment scoring component, frame-type dependent

**Why it matters:** Self grows naturally. Nothing is blocked. The system can learn anything
but emphasizes what's relevant to it. This is reversible.

### 005: Decay Sophistication

**Core question:** Pure time-based decay kills knowledge during holidays. What actually
causes memory loss?

**Key finding:** Three distinct mechanisms:
- **Staleness** (time-based) — meeting notes actually expire
- **Interference** (event-based) — new similar knowledge displaces old
- **Disuse** (usage-based) — blocks scoring low in queries get pruned

**Why it matters:** Different content needs different decay treatment. Knowledge shouldn't
die from pure time passage.

**Design roadmap:**
- Phase 1: Session-aware decay (only count active hours)
- Phase 2: Add displacement tracking
- Phase 3: Full multi-factor decay

---

## Design Decisions Locked In

Based on these explorations, the following are no longer design questions:

| Decision | From Exploration | Consequence |
|----------|------------------|-------------|
| Reinforcement is mandatory | 001 | Must instrument every retrieval to reinforce blocks |
| Frame weights are correct | 002 | Can proceed with SELF/ATTENTION weights as specified |
| Usage drives identity | 003 | Self is dynamic, not static. Identity emerges from use. |
| Self uses soft bias | 004 | No hard filtering. Everything learned. Self grows naturally. |
| Decay is multi-factor | 005 | Need staleness/interference/disuse separation long-term |
| MVP uses session-aware decay | 005 | Count active hours, not wall-clock time |

---

## Design Questions Remaining

These are unanswered and worth exploring further:

| Question | Why it matters | Explored in |
|----------|---------------|-------------|
| Should `is_self_component` get a direct scoring bonus? | Could override usage-driven identity inflation | 003, 004 |
| Should ATTENTION frame exclude self-tagged blocks? | Might improve query relevance for non-identity questions | 002, 004 |
| What's the right idle_factor for dual-rate decay? | Affects how much knowledge survives idle periods | 005 |
| Does incremental assembly work better than top-K? | Could improve frame coherence | 002 |
| Can we measure self-alignment empirically? | Validates the three-layer interest model | 004 |
| What happens when self's core values are challenged? | Tests identity stability vs. growth | 004 |

These can drive Phase 2 explorations or be addressed during implementation.

---

## The Three Phases

### Phase 1: Explorations ✓ COMPLETE

Rapid micro-scenarios testing individual concepts.

- **Status:** 5 explorations complete
- **Coverage:** decay, scoring, frames, self, interest
- **Outcome:** Design decisions locked, open questions identified
- **Time to complete:** ~4 hours

### Phase 2: Playgrounds (Next)

Organize explorations into subsystems, add test cases.

**Planned structure:**
```
playgrounds/
├── decay/           {spec, test cases, calibration data}
├── scoring/         {formula, weight tuning, frame variants}
├── frames/          {frame definitions, composition rules}
├── graph/           {centrality measures, edge creation}
└── lifecycle/       {ingestion → consolidation → decay → pruning}
```

**What it includes:** Specification per subsystem, worked test cases with expected outputs,
edge case analysis.

**Output:** Set of assertion-based tests that generated code must pass.

### Phase 3: Executable Specs (Then)

Turn playgrounds into code-generation source.

```
specs/
├── 01_data_model.md     → models.py + DDL + factories
├── 02_scoring.md        → scoring.py + test_scoring.py
├── 03_decay.md          → decay.py + test_decay.py
├── 04_frames.md         → frames/ + test_frames.py
├── 05_retrieval.md      → retrieval/ + test_retrieval.py
└── 06_lifecycle.md      → lifecycle/ + test_lifecycle.py
```

Each spec contains:
- Type definitions (Pydantic models)
- Algorithm pseudocode and math
- Worked examples from Phase 1
- Test cases with expected inputs/outputs
- Edge cases and error handling

**Output:** Complete test suite and code specification.

---

## How to Use This Simulation

### For Exploration

Pick an exploration and read it. Takes 5-15 minutes per file.

```
1. Open sim/explorations/001_basic_decay.md
2. Read Setup (understand the state)
3. Follow Computation (see the math step-by-step)
4. Check Result (what state do we end up in?)
5. Understand Insight (what does this teach us?)
6. Consider Variations (what would change if...?)
```

### For Learning

Read the five explorations in order. They build on each other and cover the major design areas.

```
001 → 002 → 003 → 004 → 005
(40 min total, or 15 min for the quick versions)
```

### For Validation

Check that design decisions match your intuition.

Each exploration includes a **Key Finding** that's easy to verify. If the math surprises you,
trace through the computation to understand why.

### For Variation

Run "what-if" scenarios by changing a parameter and recomputing.

```
"Run variation: what if decay_lambda was 0.05 instead of 0.01 for 001?"
```

I'll create a variation file, update the math, compare results.

### For Extension

Create new explorations to answer new questions.

```
"Create an exploration: what happens if we change the SELF confidence weight from 0.30 to 0.20?"
```

I'll set up the blocks, compute, analyze, record insights.

---

## Key Insights Across All Explorations

1. **Reinforcement is the lynchpin.** Everything else depends on it. Without retrieval
   reinforcing blocks, knowledge dies. This is non-negotiable.

2. **Time isn't the main driver of decay.** Knowledge doesn't die because time passes.
   It dies because (a) it's time-sensitive (ephemeral), (b) new information displaces it
   (interference), or (c) it's never selected (disuse). Different mechanics for each.

3. **Self should influence, not gate.** Trying to prevent learning non-aligned things creates
   echo chambers. Better to let everything in but weight it. Identity grows through use.

4. **Scoring weights need validation at scale.** The formulas work in isolation, but only
   real-world usage will show if the weights feel right. Phase 2 needs A/B testing.

5. **The system's identity is dynamic.** Self isn't a fixed persona that filters learning.
   It's a context that emerges from the most-used, most-reinforced blocks. This is the
   self-organising property.

---

## What This Enables

Once these explorations are solid and well-documented:

1. **Code generation:** Specifications directly → Python modules + tests
2. **Team alignment:** Non-technical stakeholders can read explorations to understand design
3. **Implementation confidence:** Implementers know the math is validated before writing code
4. **Test fixtures:** Every worked example becomes a test case
5. **Documentation:** Explorations become system documentation
6. **Iterative refinement:** Easy to adjust parameters and see impact before implementing

---

## Next Steps

### Short-term (This week)

- [ ] Read QUICKSTART.md (5 min)
- [ ] Read explorations 001-005 (30 min)
- [ ] Run 2-3 variations (15 min per variation)
- [ ] Identify which open questions matter most

### Medium-term (This sprint)

- [ ] Organize explorations into Phase 2 playgrounds
- [ ] Add test cases per subsystem
- [ ] Run deep variations on ambiguous design questions
- [ ] Make final decisions on open questions

### Long-term

- [ ] Write executable specs (Phase 3)
- [ ] Generate Python code directly from specs
- [ ] Implement and validate
- [ ] Compare simulation vs. real behavior

---

## Files to Read

**Start here:**
1. `QUICKSTART.md` (5 min) — Overview and examples
2. `sim/explorations/001_basic_decay.md` (10 min) — Foundation

**Then:**
3. `sim/explorations/002_confidence_trap.md` (10 min)
4. `sim/explorations/003_scoring_walkthrough.md` (12 min)
5. `sim/explorations/004_self_interest_model.md` (15 min)
6. `sim/explorations/005_decay_sophistication.md` (12 min)

**For reference:**
- `sim/README.md` — Detailed guide, formulas, conventions
- `sim/EXPLORATIONS.md` — Index, navigation, design decisions
- `docs/amgs_architecture.md` — Full specification (750 lines)

**Total reading time:** ~1 hour for comprehensive understanding, 20 min for quick overview.

---

## Philosophy

This approach treats design specification as a **conversation**, not a document. Each
exploration is a claim ("here's how decay works") backed by worked evidence. Variations
let us test counterfactuals ("what if we changed this parameter?"). And because everything
is in markdown, we can evolve the simulation as we learn.

The goal is **confidence before implementation**. By the time we write Python code, the
math is proven, the design decisions are locked, and the test cases are written.

Welcome to the simulation.
