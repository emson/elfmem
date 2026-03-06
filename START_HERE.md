# START HERE — elfmem

**elfmem** is a Python library for adaptive, self-aware memory for LLM agents. Knowledge that gets used survives; knowledge that doesn't fades away.

Three ways to use it: **MCP server** (for AI agents), **CLI** (shell scripts and automation), or **Python library** (direct integration).

---

## Quick Setup (2 minutes)

```bash
uv add 'elfmem[tools]'
export ANTHROPIC_API_KEY=sk-ant-...
export ELFMEM_DB=agent.db
```

**MCP** — add to your agent's MCP config:
```json
{
  "mcpServers": {
    "elfmem": {
      "command": "elfmem",
      "args": ["serve", "--db", "/absolute/path/to/agent.db"]
    }
  }
}
```

**CLI** — start immediately:
```bash
elfmem remember "User prefers dark mode"
elfmem recall "UI preferences"
elfmem status
elfmem guide
```

**Python**:
```python
from elfmem import MemorySystem
system = await MemorySystem.from_config("agent.db")
async with system.session():
    await system.learn("User prefers dark mode")
    result = await system.frame("attention", query="UI preferences")
    print(result.text)  # inject into your LLM prompt
```

---

## Key Documents

| Document | Purpose | Start Here If... |
|----------|---------|------------------|
| **QUICKSTART.md** | Installation, first 5 minutes, all three interfaces | You're setting up elfmem |
| **README.md** | Full API reference, configuration, architecture | You want the complete picture |
| **docs/elfmem_tool.md** | MCP tool reference + CLI reference | You're integrating with an agent |
| **SIMULATION_OVERVIEW.md** | Philosophy — what elfmem solves and why | You want to understand the design |
| **docs/amgs_architecture.md** | Technical specification, layer model | You're extending or debugging |
| **sim/EXPLORATIONS.md** | 26 design explorations with mathematical proofs | You want design rationale |

---

## Core Operations

```
remember/learn     →  store knowledge (instant, inbox)
consolidate        →  process inbox: embed, score, deduplicate, build graph (LLM calls)
recall/frame       →  retrieve context for prompt injection (fast, 4-stage pipeline)
outcome            →  record signal to update confidence (fast, no LLM)
curate             →  maintenance: archive stale, prune weak edges (fast)
status             →  system health snapshot + suggested next action
guide              →  runtime documentation for any operation
```

Call `elfmem guide` at any time to see this overview. Call `elfmem guide <method>` for detailed help on any operation.

---

## Architecture at a Glance

```
Transport:    cli.py (typer)        mcp.py (fastmcp)
                   └─────────────────────┘
                        smart.py (SmartMemory — auto session + consolidation)
                               ↓
              api.py (MemorySystem — public Python API)
                               ↓
         ┌─────────────────────────────────────────┐
         │  operations/  │  memory/  │  context/   │
         │  learn        │  blocks   │  frames      │
         │  consolidate  │  dedup    │  rendering   │
         │  recall       │  graph    │  contradicts │
         │  curate       │  retrieval│              │
         └─────────────────────────────────────────┘
                               ↓
                         db/ (SQLite via SQLAlchemy)
```

**Four layers with clear boundaries:**

| Layer | Responsibility |
|-------|---------------|
| `db/` | Tables, queries, async engine — all DB writes |
| `memory/` | Blocks, dedup, graph, retrieval — pure functions |
| `context/` | Frames, rendering, contradictions — pure functions |
| `operations/` | Orchestration, lifecycle — all side effects |

---

## Design Decisions (Locked)

These emerged from 26 mathematical explorations before implementation:

1. **Soft bias for identity, not hard gates** — Nothing is blocked from learning; self-aligned blocks just survive longer and surface more.
2. **Reinforcement is mandatory** — Standard knowledge dies in ~12.5 days without use. Retrieval automatically reinforces.
3. **Session-aware decay** — The clock only ticks during active sessions. Memory survives weekends.
4. **Retrieval is pure; reinforcement is a separate operation** — Clean separation of read path from side effects.
5. **SQLite, not a vector database** — Zero infrastructure; embeddings stored as BLOBs; one file, fully portable.
6. **LiteLLM as unified backend** — One adapter for 100+ providers. Switch with a config change.

---

## How to Navigate This Repository

```
elfmem/
├── START_HERE.md            ← YOU ARE HERE
├── QUICKSTART.md            ← Read next (install + first 5 minutes)
├── README.md                ← Full reference (API, config, architecture)
├── SIMULATION_OVERVIEW.md   ← Design philosophy
├── src/elfmem/
│   ├── api.py               ← MemorySystem (public API)
│   ├── smart.py             ← SmartMemory (MCP + CLI facade)
│   ├── mcp.py               ← FastMCP server (6 tools)
│   ├── cli.py               ← Typer CLI (7 commands)
│   └── ...                  ← Core layers
├── tests/                   ← 400+ tests, mock-first (no API keys needed)
├── docs/
│   ├── elfmem_tool.md       ← MCP + CLI reference
│   └── amgs_architecture.md ← Technical deep dive
└── sim/
    ├── EXPLORATIONS.md      ← Index of 26 design explorations
    └── explorations/        ← Mathematical proofs for every design decision
```

---

## Ready?

```bash
cat QUICKSTART.md   # installation + first steps
elfmem guide        # discover all operations
elfmem status       # check memory health at any time
```
