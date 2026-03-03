# AMGS Simulation — Claude Code Instructions

## Setup

```bash
pip install numpy networkx
```

Drop `amgs_sim.py` into your project directory.

## Quick Start

```python
from amgs_sim import MemorySystem

sys = MemorySystem(seed=42)
sys.seed_corpus("python_dev")
sys.seed_corpus("ai_research")
sys.seed_corpus("personal_identity")
sys.consolidate()
sys.status()
```

## Available Corpora

| Corpus | Blocks | Description |
|---|---|---|
| `python_dev` | 15 | Python programming knowledge |
| `ai_research` | 15 | AI/ML concepts and techniques |
| `personal_identity` | 10 | Identity and values (self-tagged) |
| `project_notes` | 10 | AMGS project documentation |
| `world_knowledge` | 10 | General world knowledge |

## Core Operations

### Ingest → Consolidate → Advance → Assemble

```python
# Add knowledge to inbox
sys.ingest("Some new knowledge", category="python_dev", decay_profile="standard")

# Process inbox: embed, link, score, set status to consolidated
sys.consolidate()

# Advance simulated time (applies decay, triggers pruning)
sys.advance(days=7)

# Assemble a context frame
self_frame = sys.assemble_frame("SELF")
attn = sys.assemble_frame("ATTENTION", query="machine learning", query_topic="ai_research")
session = sys.assemble_frame("SESSION", query="Python async")
```

### Decay Profiles

| Profile | λ | Half-life |
|---|---|---|
| `ephemeral` | 0.10 | ~7 hours |
| `short` | 0.03 | ~23 hours |
| `standard` | 0.01 | ~2.9 days |
| `durable` | 0.001 | ~28.9 days |
| `core` | 0.0001 | ~289 days |
| `permanent` | 0.00001 | ~7.9 years |

### Frame Types

| Frame | Purpose | Key Weight |
|---|---|---|
| `SELF` | Identity, core beliefs | confidence + reinforcement |
| `ATTENTION` | Current query reasoning | query similarity |
| `SHORT_TERM` | Recent events | recency |
| `WORLD` | Domain knowledge | centrality + confidence |
| `TASK` | Problem solving | query similarity (heavy) |
| `INBOX` | Raw unprocessed blocks | recency only |

### Composite Frames

| Frame | Children | Strategy |
|---|---|---|
| `SESSION` | SELF + ATTENTION + SHORT_TERM | priority_chain |
| `BRIEFING` | SELF + SHORT_TERM + INBOX | priority_chain |
| `REASONING` | WORLD + TASK + ATTENTION | union |

## Inspection Methods

```python
# System overview
sys.status()

# Deep inspection of a single block (supports 8-char prefix)
sys.inspect_block("abc123de")

# Frame inspection — shows every block with score breakdown
frame = sys.assemble_frame("SELF")
sys.inspect_frame(frame)

# WHY did this block score this way?
sys.score_breakdown("abc123de", "SELF")
sys.score_breakdown("abc123de", "ATTENTION", query="Python typing")

# How will this block's relevance decay over 90 days?
sys.decay_forecast("abc123de", days=90)

# What would happen if we removed this block from SELF?
sys.what_if_remove("abc123de", "SELF")

# What would happen if we added an edge between these blocks?
sys.what_if_add_edge("abc123de", "def456ab", weight=0.9)

# Category diversity and distribution
sys.entropy_report()

# Graph-level statistics: nodes, edges, density, top PageRank
sys.graph_stats()

# List blocks with filtering and sorting
sys.list_blocks(category="python_dev", sort_by="decay_weight", limit=10)
sys.list_blocks(self_only=True)
sys.list_blocks(status=BlockStatus.PRUNED)

# Compare system state across time
sys.advance(days=7)   # snapshot taken automatically
sys.advance(days=7)   # another snapshot
sys.compare_snapshots()
```

## Custom Frames

```python
# Define a frame with custom scoring weights
sys.define_frame(
    "HIGH_CENTRALITY",
    weights={
        "recency": 0.05,
        "centrality": 0.60,
        "confidence": 0.15,
        "similarity": 0.10,
        "reinforcement": 0.10,
    },
    top_k=8,
)

# Define a composite frame from existing frames
sys.define_composite(
    "DEEP_TECHNICAL",
    children=["SELF", "HIGH_CENTRALITY"],
    strategy="priority_chain",
    budget={"SELF": 4, "HIGH_CENTRALITY": 12},
    total_budget=16,
)

# Assemble and inspect
frame = sys.assemble_frame("DEEP_TECHNICAL", query="gradient descent")
sys.inspect_frame(frame)
```

## Scenario Runner

```python
snapshots = sys.run_scenario([
    {"action": "seed", "corpus": "python_dev"},
    {"action": "seed", "corpus": "personal_identity"},
    {"action": "consolidate"},
    {"action": "snapshot"},
    {"action": "advance", "days": 7},
    {"action": "snapshot"},
    {"action": "advance", "days": 30},
    {"action": "snapshot"},
    {"action": "advance", "days": 60},
    {"action": "snapshot"},
    {"action": "assemble", "frame": "SELF"},
])
sys.compare_snapshots(0, -1)  # Compare first and last
```

### Scenario Actions

| Action | Parameters | Description |
|---|---|---|
| `seed` | `corpus`, `n` (optional) | Seed a corpus |
| `ingest` | `content`, `category`, `decay_profile`, `is_self` | Ingest a single block |
| `consolidate` | `threshold` (optional) | Run consolidation |
| `advance` | `hours` and/or `days` | Advance simulated time |
| `reinforce` | `block_id` | Reinforce a block |
| `snapshot` | — | Capture system state |
| `assemble` | `frame`, `query` (optional) | Assemble a frame |

## Ready-to-Run Examples

```python
from amgs_sim import *

example_basic_walkthrough()     # Start here
example_decay_exploration()     # Watch memories fade
example_frame_composition()     # Composite frames
example_90_day_simulation()     # Full lifecycle
example_what_if_analysis()      # Counterfactual reasoning
```

## Interrogation Patterns for Claude Code

These are questions you can ask Claude Code once the simulation is loaded:

**Understanding attention:**
- "Show me the SELF frame and explain why each block is there"
- "Score breakdown for the top block in ATTENTION — what's driving its score?"
- "Why is this Python block in the SELF frame? It shouldn't be identity."

**Tuning parameters:**
- "The SELF frame is too narrow. Reduce confidence weight to 0.15 and increase centrality to 0.40"
- "What happens if I make decay faster for python_dev blocks?"
- "Create a frame that only cares about graph centrality and test it"

**Temporal reasoning:**
- "Advance 30 days. Which blocks survived? Which got pruned? Why?"
- "Forecast the decay of the top 3 SELF blocks over 90 days"
- "Run a 90-day scenario and show me how the category entropy changes"

**Counterfactual analysis:**
- "What if I remove the top-ranked SELF block? What replaces it?"
- "What would happen if I added an edge between these two unconnected blocks?"
- "If I reinforce this block 10 times, how does its score change?"

**System health:**
- "Is the graph too sparse? Show me density and clustering"
- "Are any categories dominating? Show me the entropy report"
- "Compare the system state from week 1 to week 12"