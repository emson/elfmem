# Quick Start: AMGS Simulation

A document-driven simulation where Claude reasons through the system's behavior using
structured markdown files with worked mathematical examples.

---

## What Is This?

Instead of running Python code, we use markdown files as the simulation medium. Every
computation is explicit and editable. This is the **whiteboard** for exploring architectural
ideas before writing code.

**Goal:** Rapidly test design concepts and build confidence that the architecture works.

---

## The Three Phases

### Phase 1: Explorations (Now)

Micro-scenarios in `sim/explorations/`. Each file is self-contained:
- Small setup (3-5 blocks, minimal state)
- Worked computation (step-by-step math)
- Result and insight
- Variations for next questions

**Status:** 5 explorations complete, covering decay, scoring, self, and interest.

### Phase 2: Playgrounds (Later)

Once patterns emerge, organize explorations into playgrounds by subsystem:
- `playgrounds/decay/` — decay mechanics
- `playgrounds/scoring/` — frame assembly and scoring
- `playgrounds/frames/` — context frame composition
- `playgrounds/graph/` — graph structure and centrality
- `playgrounds/lifecycle/` — ingestion to pruning

### Phase 3: Executable Specs (When Stable)

Turn solidified playgrounds into code-generation source:
- `specs/01_data_model.md` → `models.py` + DDL + tests
- `specs/02_scoring.md` → `scoring.py` + tests
- etc.

---

## How to Use It

### To Read an Exploration

```
Open: sim/explorations/001_basic_decay.md
Follow: Setup → Computation → Result → Insight
```

Learn what it teaches. Check the Variations section for follow-up questions.

### To Run a Variation

Variations are small tweaks to existing explorations. Run them by:

```
"Run variation 2 from exploration 001: what if prune threshold is 0.10?"
```

I'll:
1. Create `001_variation_prune_threshold.md` (or inline)
2. Update the Setup
3. Recompute all math
4. Compare results to baseline
5. Record insights

### To Create a New Exploration

Ask a question:

```
"Create an exploration: what happens if we add reinforcement to Block B from 001?"
```

I'll:
1. Create `00X_your_question.md` using the template
2. Set up the blocks and state
3. Compute the math
4. Analyze results
5. Suggest variations

### To Inspect an Exploration

Ask for details about any file:

```
"Walk me through the scoring computation in 003. Why does K1 beat S2?"
```

I'll trace through the math and explain each component.

---

## The Five Explorations

| # | Title | Key Question | Main Insight |
|---|-------|--------------|--------------|
| 001 | Basic Decay | How long do blocks survive? | Standard blocks die in 12.5 days without reinforcement |
| 002 | Confidence Trap | Does ATTENTION frame work? | Yes, query-similarity dominates; the bug was corpus composition |
| 003 | SELF Scoring | How does a full frame assembly work? | Reinforcement is so powerful it can override self-tagging |
| 004 | Self Interest Model | Should self gate learning or influence it? | Soft bias, not hard gates; self grows naturally without blocking |
| 005 | Decay Sophistication | Why does wall-clock decay kill knowledge on holidays? | Separate staleness (time), interference (events), disuse (usage) |

**Start with 001, read in order. They build on each other.**

---

## Key Formulas (You'll See These Repeatedly)

### Decay
```
decay_weight = e^(-λ × t)
half_life = 0.693 / λ
```

### Composite Score
```
Score = w1×Recency + w2×Centrality + w3×Confidence + w4×Similarity + w5×Reinforcement
```

### Reinforcement (normalized)
```
reinforcement_score = log(1 + count) / log(1 + max_count)
```

### SELF Frame Weights
```
Recency=0.05, Centrality=0.25, Confidence=0.30, Similarity=0.10, Reinforcement=0.30
```

### ATTENTION Frame Weights
```
Recency=0.25, Centrality=0.15, Confidence=0.15, Similarity=0.35, Reinforcement=0.10
```

---

## Design Decisions Made

These are locked in based on the explorations:

1. **Reinforcement is mandatory.** Knowledge dies without it.
2. **Weights are correctly tuned.** ATTENTION correctly surfaces query-relevant blocks.
3. **Self uses soft bias.** No hard filtering. Everything is learned, but self-aligned knowledge survives longer.
4. **Decay is multi-factor.** Session-aware in Phase 1, then split staleness/interference/disuse in later phases.
5. **Nothing is gatekept.** Interest influences decay and retrieval, not ingestion.

---

## Open Questions (For Phase 2)

These are worth exploring as variations or new explorations:

- Should `is_self_component` get a direct scoring bonus?
- Should ATTENTION frame exclude self-tagged blocks?
- What's the right idle_factor for dual-rate decay?
- Does incremental assembly (quality threshold) work better than top-K?
- Can we measure self-alignment empirically?

---

## File Structure

```
sim/
├── README.md                    # Detailed guide, formulas, conventions
├── EXPLORATIONS.md              # Index and navigation
├── explorations/
│   ├── _template.md             # Blank template
│   ├── 001_basic_decay.md
│   ├── 002_confidence_trap.md
│   ├── 003_scoring_walkthrough.md
│   ├── 004_self_interest_model.md
│   └── 005_decay_sophistication.md
├── playgrounds/                 # Phase 2 (not yet started)
└── specs/                       # Phase 3 (not yet started)
```

---

## Next Steps

**Option A: Deep Dive**
Read all 5 explorations in order (30-60 min), understand the design decisions.

**Option B: Quick Tour**
Read just 001 and 004. Understand decay and self-interest. Skip the detailed math.

**Option C: Explore Variations**
Pick one exploration, run 2-3 variations, see how changing parameters affects results.

**Option D: Design Discussion**
Pick a design question from the explorations, brainstorm new approaches, create a new exploration.

---

## What Next?

After exploring, the workflow is:

1. **Solidify:** Extract patterns from Phase 1 into Phase 2 playgrounds
2. **Formalize:** Write assertion-based test cases for each subsystem
3. **Codify:** Turn playgrounds into executable specs
4. **Implement:** Generate Python code directly from specs

But we're not there yet. Phase 1 is about building confidence in the design.

---

## Questions?

Ask me anything about an exploration, and I'll explain the math, compute a variation,
or help you create a new one.

**Examples:**
- "Why does Block B survive but not Block C in 001?"
- "What if we added a graph edge between blocks in 003?"
- "Can you run variation 1 from 005?"
- "Create an exploration: what happens if..."

Go explore.
