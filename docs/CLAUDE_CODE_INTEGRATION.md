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

## Next Steps

1. ✅ System prompt is in place
2. ✅ MCP server is configured
3. ⏳ Start using it: Ask Claude to query elf for guidance on ConsolidationPolicy
4. ⏳ Monitor which queries to elf are most useful
5. ⏳ Expand elf's identity with new concepts as they emerge
