# Claude Code + elf Integration Guide

## Overview

Claude Code now has direct access to elf's identity via MCP. This means Claude can:
- Query elf's identity before major decisions
- Learn new concepts by remembering them
- Access the full 100+ concept library (agent patterns, cognitive loops, etc.)
- Make decisions guided by elf's constitutional principles

## Setup

### 1. Install elf with MCP support

```bash
cd ~/Dropbox/devel/projects/ai/elf0_mem_sim
uv sync --extra mcp --extra cli
```

### 2. Claude Code is already configured

The MCP configuration is at:
```
~/.claude/projects/-Users-emson-Dropbox-devel-projects-ai-elf0-mem-sim/system_prompt.md
```

This system prompt tells Claude to:
- Use `elfmem_recall(query, frame="self")` to access elf's identity
- Respect elf's constitutional principles
- Guide all decisions through elf's identity

### 3. Create an elf database (first time)

```bash
# Initialize the memory database
elfmem init ~/.elfmem/default.db

# Populate with elf's constitutional knowledge (optional but recommended)
elfmem init --seed ~/.elfmem/default.db
```

## How It Works

### When Claude Encounters a Design Decision

Claude's system prompt tells it to ask: **"What does elf say about this?"**

Example workflow:

1. **You:** "Implement ConsolidationPolicy"

2. **Claude thinks:** "This is about consolidation timing. Let me query elf's identity for guidance."

3. **Claude (via MCP):**
   ```
   elfmem_recall(
       query="consolidation timing principles and heuristics",
       frame="self",
       top_k=5
   )
   ```

4. **elf responds** with identity blocks about consolidation:
   - "Consolidation should be self-driven, not manual"
   - "Pre-filter contradictions by similarity to save LLM calls"
   - "Consolidation is the 'dream' phase: deep processing, bounded time"

5. **Claude uses this context** to inform implementation decisions

### Query Examples

**For architecture questions:**
```
elfmem_recall("identity principles for distributed caching", frame="self")
```

**For performance guidance:**
```
elfmem_recall("optimization strategy for retrieval at scale", frame="self")
```

**For API design:**
```
elfmem_recall("agent patterns for handling contradictions", frame="self")
```

**For decision-making:**
```
elfmem_recall("how should I decide when to curate", frame="self")
```

## What's Stored in elf's Identity

The "self" frame (elf's identity) contains:

### 1. Constitutional Principles (10 blocks)
- Purpose and values
- Design philosophy
- Decision-making framework

### 2. Agent Usage Patterns (26 blocks)
- When to remember, recall, reinforce, curate
- How to handle contradictions, silence, signals
- Multi-domain reasoning patterns

### 3. Cognitive Loop Operations (21+ blocks)
- 8-step feedback loop
- Decision trees (frame selection, timing, thresholds)
- Reflection protocols

### 4. Design Simulation Methodology (12 blocks)
- How to document architecture decisions
- Scoring formulas and derivations
- Edge case handling

## Example: Building ConsolidationPolicy

When you ask Claude to implement ConsolidationPolicy:

1. **Claude queries elf:**
   ```
   "timing heuristics for consolidation, what triggers dream phase"
   ```

2. **elf returns blocks like:**
   - "Consolidation is bounded—should complete in minutes, not hours"
   - "Pre-filter reduces LLM calls by 95%, enabling more frequent consolidation"
   - "Self-driven timing means learning from success/failure metrics"

3. **Claude implements with elf's guidance:**
   ```python
   class ConsolidationPolicy:
       async def should_consolidate(self) -> bool:
           # Query elf for timing principles
           elf_context = await recall("consolidation timing", frame="self")
           # Make decision informed by elf + metrics
   ```

## Viewing elf's Identity Manually

You can inspect elf's identity directly:

```bash
# Recall elf's identity for a topic
elfmem recall "my identity and values" --frame self

# See what's in the database
elfmem status

# Remember something into elf's identity
elfmem remember "New principle: X" --tag self/principle
```

## Tips for Best Results

### 1. Be Specific in Queries
❌ Bad: "help with optimization"
✅ Good: "how should I optimize consolidation for 1000 blocks"

### 2. Use the Right Frame
- `frame="self"` → Ask about identity, principles, values, timing
- `frame="attention"` → Ask about specific problems, patterns
- `frame="task"` → Ask about goals and task-specific knowledge

### 3. Reference Your elf Queries in Commits
If you implement something guided by elf, mention it:
```
feat: Implement ConsolidationPolicy

Queried elf for: "consolidation timing principles"
Decision: Self-tune prefilter threshold based on success rate
```

### 4. Keep elf's Identity Fresh
When you discover a pattern, have Claude remember it:
```
elfmem_remember(
    "Consolidation succeeds fastest when inbox < 50 blocks",
    tags=["self/principle", "consolidation/heuristic"]
)
```

## Troubleshooting

### MCP server not starting
```bash
# Check that extras are installed
uv sync --extra mcp --extra cli --all-extras

# Check that the MCP entry point works
python -m elfmem.mcp --help
```

### Identity queries returning empty
```bash
# Initialize the database with elf's identity blocks
elfmem init --seed ~/.elfmem/default.db

# Verify blocks are there
elfmem status
```

### Claude not using MCP
- Check that `~/.claude/claude_code_config.json` exists
- Verify system_prompt.md is in the project folder
- Restart Claude Code for config changes to take effect

## Architecture

```
Claude Code (System Prompt)
       ↓ (asks "What does elf say?")
       ↓
MCP Protocol
       ↓
elf MCP Server
       ↓
elf Recall (identity frame)
       ↓
SQLite DB (Constitutional blocks)
       ↓
Result → Claude's Decision-Making
```

elf's identity flows into Claude's reasoning without requiring project changes or hardcoded prompts.

---

## Agent Discipline: Self-Calibrating Memory

Static prompts tell agents what to do. **Agent discipline** teaches agents to
improve their own memory through use. The discipline loop:

```
RECALL → EXPECT → ACT → OBSERVE → CALIBRATE → ENCODE
```

The critical step most agents skip is **calibration** — telling elfmem which
recalled blocks actually helped (`outcome(signal=0.85)`), which were noise
(`signal=0.45`), and which misled (`signal=0.15`). Without this, all knowledge
decays equally and memory never improves.

### Three Tiers

| Tier | Instructions | Best for |
|------|-------------|----------|
| Basic (2 instructions) | Recall before acting, remember surprises | Simple agents, quick tasks |
| Standard (6 instructions) | + frame selection + inline calibration | Team agents, recurring tasks |
| Full (12 instructions) | + session metrics + reflection + meta-learning | Long-running agents |

### Resources

- **`examples/agent_discipline.md`** — Copy-pasteable prompt instructions for all three tiers
- **`examples/calibrating_agent.py`** — Python reference implementation (36 tests)
- **`examples/decision_maker.py`** — Simpler example focused on multi-frame decisions
- **`scripts/seed_team_memory.py`** — Seed elfmem with project conventions for team agents

### Quick Start: Add Discipline to a Team Agent

Add this to any team agent's system prompt (Tier 2 — standard discipline):

```
Before each task:
  1. Select frame: novel→attention, execution→task, identity→self
  2. elfmem_recall("<task description>", frame=<selected>)
  3. Set expectation: "I expect <prediction>."

After each task:
  4. For each recalled block: elfmem_outcome([id], signal based on usefulness)
  5. If surprised: elfmem_remember("Expected X, observed Y. Pattern: Z")
  6. At pauses: elfmem_dream()
```

Over cycles, useful blocks rise in confidence. Noise decays. The memory
self-tunes toward what actually works for your team.

---

## Simulation-Based Calibration: The Fourth Rhythm

For high-stakes or novel-domain decisions, agents can **simulate** before
acting — generating scenarios, scoring them against knowledge, and
pre-calibrating blocks without waiting for reality.

elfmem's four rhythms:

| Rhythm | Direction | What it does |
|--------|-----------|--------------|
| Heartbeat (learn) | Past → Memory | Fast ingestion |
| Breathing (dream) | Memory → Structure | Deep consolidation |
| Sleep (curate) | Structure → Health | Maintenance |
| **Imagination (simulate)** | **Memory → Future** | **Proactive calibration** |

Key concepts:
- **Brier scores** track prediction accuracy over time
- **Fragility scores** reveal when predictions rest on too few blocks
- **Adversarial scenarios** prevent echo chambers
- **Wildcard tracking** detects when the simulation framework is too narrow
- **Tiered simulation** matches depth to decision stakes (1-10 LLM calls)

See `examples/simulation_calibration.md` for the full design, edge cases,
global politics worked example, and implementation outline.

---

## Next Steps

1. ✅ System prompt is in place
2. ✅ MCP server is configured
3. ✅ Agent discipline documented (`examples/agent_discipline.md`)
4. ✅ Self-calibrating agent example (`examples/calibrating_agent.py`)
5. ✅ Team memory seeding script (`scripts/seed_team_memory.py`)
6. ✅ Simulation calibration designed (`examples/simulation_calibration.md`)
7. ⏳ Add discipline instructions to team agent prompts
8. ⏳ Implement SimulatingAgent (extends CalibratingAgent)
9. ⏳ Monitor calibration metrics and Brier scores across sessions
10. ⏳ Expand knowledge base as patterns emerge from use
