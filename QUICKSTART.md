# Quick Start — elfmem

Get adaptive memory into your agent in under 5 minutes.

---

## Install

```bash
uv add 'elfmem[tools]'      # recommended: CLI + MCP server + library
uv add 'elfmem[cli]'        # CLI + library only
uv add elfmem               # Python library only
```

Set your API key:

```bash
export ANTHROPIC_API_KEY=sk-ant-...   # for Claude (default model: claude-sonnet-4-6)
# or
export OPENAI_API_KEY=sk-...          # for OpenAI models
```

---

## Pick Your Interface

### Option A: MCP Server (Recommended — agents with MCP support)

Works with Claude Desktop, Claude Code, Cursor, VS Code + Cline, any MCP host.

```bash
elfmem serve --db agent.db
```

Add to your MCP host config (e.g. Claude Desktop `claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "elfmem": {
      "command": "elfmem",
      "args": ["serve", "--db", "/absolute/path/to/agent.db"],
      "env": {
        "ANTHROPIC_API_KEY": "sk-ant-..."
      }
    }
  }
}
```

Six tools become available: `elfmem_remember`, `elfmem_recall`, `elfmem_status`, `elfmem_outcome`, `elfmem_curate`, `elfmem_guide`. Sessions and consolidation are handled automatically.

### Option B: CLI (Shell access, no MCP needed)

```bash
# Set the database path once
export ELFMEM_DB=agent.db

# Store knowledge (instant — no LLM calls)
elfmem remember "User prefers explicit error handling"
elfmem remember "User uses Python over JavaScript" --tags language,preference

# Retrieve context (ready for prompt injection)
elfmem recall "code style preferences"

# Check memory health
elfmem status

# Get help
elfmem guide
elfmem guide recall
```

### Option C: Python Library (Full control)

```python
import asyncio
from elfmem import MemorySystem

async def main():
    system = await MemorySystem.from_config("agent.db", {
        "llm": {"model": "claude-sonnet-4-6"},
        "embeddings": {"model": "text-embedding-3-small"},
    })

    async with system.session():
        # Store knowledge
        await system.learn("User prefers explicit error handling", tags=["code_style"])

        # Retrieve context — text is ready for prompt injection
        result = await system.frame("attention", query="error handling preferences")
        prompt = f"{result.text}\n\nUser: How should I handle errors here?"

asyncio.run(main())
```

---

## Your First 5 Minutes (CLI walkthrough)

### 1. Store some knowledge

```bash
export ELFMEM_DB=./my_memory.db

elfmem remember "User prefers explicit error handling with try/except"
elfmem remember "User prefers Python over JavaScript"
elfmem remember "User works in a large Django monorepo"
```

Each call is instant — no LLM calls yet. Blocks queue in the inbox.

### 2. Retrieve context

```bash
elfmem recall "coding preferences"
```

Returns rendered markdown ready for prompt injection:

```
## Relevant Context

User prefers explicit error handling with try/except
User prefers Python over JavaScript
User works in a large Django monorepo
```

### 3. Check status

```bash
elfmem status
```

```
Session: inactive | Inbox: 3/10 | Active: 0 blocks | Health: good
Tokens this session: no token usage recorded
Suggestion: Memory is empty. Call learn() to add knowledge.
```

The inbox has 3 blocks pending consolidation. Blocks become fully searchable after consolidation runs (automatic when inbox reaches threshold, or when an MCP session ends).

### 4. Learn more and let consolidation run

```bash
# Add more blocks to trigger consolidation (default threshold: 10)
for i in $(seq 1 7); do
  elfmem remember "Additional fact number $i"
done

# Or trigger explicitly via the Python API
# await system.consolidate()
```

### 5. Get help from within elfmem

```bash
elfmem guide              # overview of all operations
elfmem guide recall       # detailed guide for recall
elfmem guide outcome      # how to record outcome feedback
```

---

## Configuration

### YAML Config File

Create `elfmem.yaml` for custom settings:

```yaml
llm:
  model: "claude-sonnet-4-6"
  # Per-call overrides (None = use model above)
  alignment_model: null
  tags_model: null
  contradiction_model: null

embeddings:
  model: "text-embedding-3-small"
  dimensions: 1536

memory:
  inbox_threshold: 10       # consolidate when inbox hits this
  curate_interval_hours: 40 # auto-curate after this many active hours
  top_k: 5                  # default blocks returned
  self_alignment_threshold: 0.70
```

Use the config file:

```bash
elfmem serve --db agent.db --config elfmem.yaml
# or set env var
export ELFMEM_CONFIG=elfmem.yaml
elfmem status
```

### Local Models (No API Key)

```yaml
# elfmem-local.yaml
llm:
  model: "ollama/llama3.2"
  base_url: "http://localhost:11434"

embeddings:
  model: "ollama/nomic-embed-text"
  dimensions: 768
  base_url: "http://localhost:11434"
```

---

## Key Concepts

### Knowledge Lifecycle

```
learn()  →  inbox  →  consolidate()  →  active (graph)  →  recall()
                                              ↓
                                           decay
                                              ↓
                                           archive
```

- **learn/remember**: instant, no LLM — blocks queue in inbox
- **consolidate**: LLM calls per block — scores alignment, embeds, deduplicates, builds graph
- **recall/frame**: fast — 4-stage hybrid retrieval (pre-filter + vector + graph + composite score)
- **curate**: fast, DB only — archives stale blocks, prunes weak edges

### Session-Aware Decay

Blocks decay during active sessions only. Memory survives weekends and restarts. Knowledge fades from *lack of use*, not from wall-clock time.

| Decay Tier | Rate (λ) | Half-life | Use Case |
|-----------|---------|---------|----------|
| permanent | 0.00001 | ~80,000h | Core identity, constitutional constraints |
| durable   | 0.001   | ~693h   | Stable preferences, learned values |
| standard  | 0.010   | ~69h    | General knowledge (default) |
| ephemeral | 0.050   | ~14h    | Session observations, temporary facts |

### Three Frames

| Frame | Purpose | When to Use |
|-------|---------|-------------|
| `attention` | Query-driven retrieval (default) | RAG, current task |
| `self` | Agent identity and values | System prompt injection |
| `task` | Goal-oriented context | Task planning, constraints |

### Outcome Feedback

Record real-world signals to improve memory quality:

```python
# After tests pass: reinforce knowledge
signal = 1.0 if tests_passed else 0.0
await system.outcome(block_ids, signal=signal, source="test_suite")

# Signal spectrum:
# 0.8–1.0 → confidence UP + decay resets
# 0.2–0.8 → neutral (confidence adjusts, no other effect)
# 0.0–0.2 → confidence DOWN + decay accelerated
```

After ~10 outcomes, evidence dominates the LLM alignment prior. Your memory learns what actually works.

---

## MCP Tool Quick Reference

| Tool | Purpose | Cost |
|------|---------|------|
| `elfmem_remember` | Store knowledge | Instant |
| `elfmem_recall` | Retrieve context for prompt injection | Fast (embed) |
| `elfmem_status` | Memory health + suggestion | Fast (DB) |
| `elfmem_outcome` | Record outcome signal to update confidence | Fast (DB) |
| `elfmem_curate` | Archive stale blocks, prune weak edges | Fast (DB) |
| `elfmem_guide` | Runtime documentation for any operation | Instant |

---

## CLI Quick Reference

```bash
elfmem remember CONTENT [--tags t1,t2] [--category C] [--db PATH] [--json]
elfmem recall QUERY [--top-k N] [--frame attention|self|task] [--db PATH] [--json]
elfmem status [--db PATH] [--json]
elfmem outcome BLOCK_IDS SIGNAL [--weight N] [--source LABEL] [--db PATH] [--json]
elfmem curate [--db PATH] [--json]
elfmem guide [METHOD]
elfmem serve --db PATH [--config PATH]
```

Database: `--db PATH` or `ELFMEM_DB` env var.
Config: `--config PATH` or `ELFMEM_CONFIG` env var.

---

## Next Steps

- **Full reference**: `docs/elfmem_tool.md` — comprehensive MCP + CLI documentation
- **Design philosophy**: `SIMULATION_OVERVIEW.md` — what elfmem solves and why
- **Architecture**: `docs/amgs_architecture.md` — technical deep dive
- **Python API**: `system.guide()` inside Python, or `src/elfmem/api.py`
