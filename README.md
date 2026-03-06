# elfmem

**Adaptive, self-aware memory for LLM agents.**

elfmem gives your LLM agent a memory that grows, evolves, and forgets — just like a human's. Knowledge that gets used survives; knowledge that doesn't fades away. Identity persists across sessions. Context is always relevant.

```python
import asyncio
from elfmem import MemorySystem

async def main():
    system = await MemorySystem.from_config("agent.db", {
        "llm": {"model": "claude-sonnet-4-6"},
        "embeddings": {"model": "text-embedding-3-small", "dimensions": 1536},
    })

    async with system.session():
        # Teach the agent something
        await system.learn("Use Celery with Redis for background tasks in Django.")
        await system.learn("I always explain my reasoning before giving recommendations.")

        # Retrieve relevant context for a prompt
        identity = await system.frame("self")         # Who am I?
        context  = await system.frame("attention",    # What do I know about this?
                                      query="background job processing")

        print(identity.text)   # Agent identity, values, style
        print(context.text)    # Relevant knowledge, ranked by importance

asyncio.run(main())
```

## Features

- **Adaptive decay** — Knowledge survives when reinforced through use, fades when ignored. Session-aware clock means your agent's memory doesn't decay over weekends.
- **SELF frame** — Persistent agent identity. Values, style, and constraints survive across sessions with near-permanent decay rates.
- **Hybrid retrieval** — 4-stage pipeline: pre-filter, vector search, graph expansion, composite scoring. Finds knowledge that's relevant *and* important.
- **Knowledge graph** — Semantic edges between memory blocks. Co-retrieved knowledge strengthens connections. Graph expansion recovers related-but-not-similar context.
- **Contradiction detection** — LLM-powered detection of conflicting knowledge. Newer, higher-confidence blocks win.
- **Near-duplicate resolution** — Detects when new knowledge updates existing knowledge. Old block archived, new block inherits history.
- **Zero infrastructure** — SQLite backend. No Redis, no Postgres, no vector database. One file, fully portable.
- **Any LLM provider** — LiteLLM backend supports 100+ providers. Switch from OpenAI to Anthropic to local Ollama with a config change.

## For AI Agents

elfmem exposes three interfaces. Pick the one that fits your environment.

### MCP (Recommended — agents with MCP support)

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
      "env": {"ANTHROPIC_API_KEY": "sk-ant-..."}
    }
  }
}
```

Six tools are available: `elfmem_remember`, `elfmem_recall`, `elfmem_status`, `elfmem_outcome`, `elfmem_curate`, `elfmem_guide`. The agent calls them directly — sessions and consolidation are automatic.

### CLI (Shell access only)

```bash
export ELFMEM_DB=agent.db
elfmem remember "User prefers dark mode" --tags ui,preference
elfmem recall "code style preferences" --json
elfmem status
elfmem guide recall
```

### Python Library (Full control)

See the code example at the top of this file.

## Installation

```bash
uv add elfmem                # Python library only
uv add 'elfmem[cli]'        # + CLI commands
uv add 'elfmem[tools]'      # + CLI + MCP server (recommended)
```

Or with pip:

```bash
pip install elfmem
pip install 'elfmem[tools]'
```

Requires Python 3.11+. Set your API key:

```bash
export ANTHROPIC_API_KEY=sk-ant-...   # for Claude (default)
export OPENAI_API_KEY=sk-...          # for OpenAI models
```

## How It Works

### The Lifecycle

Every piece of knowledge follows the same path:

```
learn()        →  Instant ingestion. Content-hash dedup. No API calls.
consolidate()  →  Batch processing. Embeddings, self-alignment scoring,
                  tag inference, near-duplicate detection, graph edges.
recall()       →  4-stage hybrid retrieval. Reinforces returned blocks.
curate()       →  Maintenance. Archives decayed blocks, prunes weak edges,
                  reinforces top-scoring knowledge.
```

### Three Frames

Frames are pre-configured retrieval pipelines optimized for different contexts:

| Frame | Purpose | Scoring Priority | Use Case |
|-------|---------|-----------------|----------|
| **SELF** | Agent identity | Confidence, reinforcement, centrality | System prompt injection |
| **ATTENTION** | Query-relevant knowledge | Similarity, recency | RAG-style retrieval |
| **TASK** | Goal-oriented context | Balanced across all signals | Task planning |

```python
# Identity context — cached, no embedding needed
self_ctx = await system.frame("self")

# Knowledge retrieval — hybrid pipeline with graph expansion
attn_ctx = await system.frame("attention", query="async error handling")

# Task context — balanced scoring, goal blocks guaranteed
task_ctx = await system.frame("task", query="refactor the API layer")
```

### Decay Tiers

Knowledge decays at different rates based on its nature:

| Tier | Half-life | Use Case |
|------|-----------|----------|
| Permanent | ~80,000 hours | Constitutional beliefs, core identity |
| Durable | ~693 hours | Stable preferences, learned values |
| Standard | ~69 hours | General knowledge |
| Ephemeral | ~14 hours | Session observations, temporary facts |

Decay is **session-aware**: the clock only ticks during active use. Your agent's memory doesn't degrade over holidays or downtime.

### Composite Scoring

Every block is scored across five dimensions:

```
Score = w_similarity    * cosine_similarity(query, block)
      + w_confidence    * block.confidence
      + w_recency       * exp(-lambda * hours_since_reinforced)
      + w_centrality    * normalized_weighted_degree(block)
      + w_reinforcement * log(1 + count) / log(1 + max_count)
```

Each frame uses different weights. SELF emphasizes confidence and reinforcement. ATTENTION emphasizes similarity and recency.

## Configuration

### Minimal (defaults)

```python
system = await MemorySystem.from_config("agent.db")
# Uses claude-sonnet-4-6 for LLM, text-embedding-3-small for embeddings
# Requires ANTHROPIC_API_KEY environment variable
```

### YAML config file

```yaml
# elfmem.yaml
llm:
  model: "claude-sonnet-4-6"
  contradiction_model: "claude-opus-4-6"  # higher precision for contradictions

embeddings:
  model: "text-embedding-3-small"
  dimensions: 1536

memory:
  inbox_threshold: 10
  curate_interval_hours: 40
  self_alignment_threshold: 0.70
  prune_threshold: 0.05
```

```python
system = await MemorySystem.from_config("agent.db", "elfmem.yaml")
```

### Local models (no API key)

```yaml
llm:
  model: "ollama/llama3.2"
  base_url: "http://localhost:11434"

embeddings:
  model: "ollama/nomic-embed-text"
  dimensions: 768
  base_url: "http://localhost:11434"
```

### Environment variables

```bash
export ANTHROPIC_API_KEY=sk-ant-...
# or
export OPENAI_API_KEY=sk-...
# or any provider LiteLLM supports
```

API keys are read by LiteLLM from standard environment variables. They never appear in config files.

## Agent Integration Pattern

```python
async def run_turn(system, user_message):
    # 1. Assemble context
    self_ctx = await system.frame("self")
    attn_ctx = await system.frame("attention", query=user_message)

    # 2. Build prompt with memory context
    prompt = f"""
    {self_ctx.text}

    {attn_ctx.text}

    User: {user_message}
    """

    # 3. Generate response
    response = await llm.complete(prompt)

    # 4. Learn from the interaction
    if worth_remembering(response):
        await system.learn(extract_knowledge(response))

    return response
```

## API Reference

### MemorySystem

```python
# Factory
system = await MemorySystem.from_config(db_path, config=None)

# Session management (required)
async with system.session():
    ...

# Write
result = await system.learn(content, tags=None, category="knowledge")

# Read
frame_result = await system.frame(name, query=None, top_k=5)
blocks = await system.recall(query=None, top_k=5, frame="attention")  # raw, no side effects

# Outcome feedback
result = await system.outcome(block_ids, signal, weight=1.0, source="")

# Maintenance (usually automatic)
await system.consolidate()  # process inbox → active
await system.curate()       # archive decayed, prune edges, reinforce top-N
```

### Return Types

```python
LearnResult(block_id, status)           # "created" | "duplicate_rejected" | "near_duplicate_superseded"
FrameResult(text, blocks, frame_name)   # rendered text + scored blocks
ConsolidateResult(processed, promoted, deduplicated, edges_created)
CurateResult(archived, edges_pruned, reinforced)
OutcomeResult(blocks_updated, mean_confidence_delta, edges_reinforced, blocks_penalized)
SystemStatus(session_active, inbox_count, active_count, health, suggestion, session_tokens, ...)
```

### Custom Prompts

Override the LLM prompts for domain-specific agents:

```yaml
prompts:
  self_alignment: |
    You are evaluating a memory block for a medical AI assistant...
    {self_context}
    {block}
    Respond: {"score": <float>}

  valid_self_tags:
    - "self/constitutional"
    - "self/domain/oncology"
    - "self/regulatory/hipaa"
```

### Custom Adapters

For full control, implement the port protocols directly:

```python
from elfmem.ports.services import LLMService, EmbeddingService

class MyLLMService:
    async def score_self_alignment(self, block: str, self_context: str) -> float: ...
    async def infer_self_tags(self, block: str, self_context: str) -> list[str]: ...
    async def detect_contradiction(self, block_a: str, block_b: str) -> float: ...

system = MemorySystem(engine, llm_service=MyLLMService(), embedding_service=MyEmbedder())
```

## Architecture

```
src/elfmem/
├── api.py                  # MemorySystem — public API
├── config.py               # ElfmemConfig — Pydantic configuration
├── smart.py                # SmartMemory — auto-managed facade (MCP + CLI)
├── mcp.py                  # FastMCP server — 6 agent tools
├── cli.py                  # Typer CLI — 7 commands
├── scoring.py              # Composite scoring formula (frozen)
├── types.py                # Domain types — shared vocabulary
├── guide.py                # AgentGuide — runtime documentation
├── exceptions.py           # ElfmemError hierarchy with recovery hints
├── prompts.py              # LLM prompt templates
├── session.py              # Session lifecycle, active hours tracking
├── token_counter.py        # Token usage accumulator
├── ports/
│   └── services.py         # LLMService + EmbeddingService protocols
├── adapters/
│   ├── mock.py             # Deterministic mocks for testing
│   ├── litellm.py          # Real adapters (LiteLLM + instructor)
│   └── models.py           # Pydantic response models
├── db/
│   ├── models.py           # SQLAlchemy Core table definitions
│   ├── engine.py           # Async engine factory
│   └── queries.py          # All database operations
├── memory/
│   ├── blocks.py           # Block state, content hashing, decay tiers
│   ├── dedup.py            # Near-duplicate detection and resolution
│   ├── graph.py            # Centrality, expansion, edge reinforcement
│   └── retrieval.py        # 4-stage hybrid retrieval pipeline
├── context/
│   ├── frames.py           # Frame definitions, registry, cache
│   ├── rendering.py        # Blocks → rendered text
│   └── contradiction.py    # Contradiction suppression
└── operations/
    ├── learn.py            # learn() — fast-path ingestion
    ├── consolidate.py      # consolidate() — batch promotion
    ├── recall.py           # recall() — retrieval + reinforcement
    └── curate.py           # curate() — maintenance
```

**Four layers, clear boundaries:**

| Layer | Responsibility | Side Effects |
|-------|---------------|-------------|
| **Storage** (db/) | Tables, queries, engine | Database writes |
| **Memory** (memory/) | Blocks, dedup, graph, retrieval | None (pure) |
| **Context** (context/) | Frames, rendering, contradictions | None (pure) |
| **Operations** (operations/) | Orchestration, lifecycle | All side effects |

## Development

```bash
# Clone
git clone https://github.com/emson/elfmem.git
cd elfmem

# Install with dev dependencies
uv sync --extra dev

# Run tests (no API key needed — uses deterministic mocks)
uv run pytest

# Type checking
uv run mypy --ignore-missing-imports src/elfmem/

# Lint
uv run ruff check src/ tests/
```

### Testing Philosophy

All tests run against deterministic mock services. No API keys, no network calls, fully reproducible. The mock embedding service produces hash-seeded vectors — same input always gives the same embedding. The mock LLM service returns configurable scores and tags via substring matching.

```python
from elfmem.adapters.mock import make_mock_llm, make_mock_embedding

# Control exactly what the LLM returns
llm = make_mock_llm(
    alignment_overrides={"identity": 0.95},
    tag_overrides={"identity": ["self/value"]},
)

# Control similarity between specific texts
embedding = make_mock_embedding(
    similarity_overrides={
        frozenset({"cats are great", "dogs are great"}): 0.85,
    },
)
```

## Design Decisions

| Decision | Rationale |
|----------|-----------|
| SQLAlchemy Core, not ORM | Bulk updates, embedding BLOBs, N+1 centrality queries |
| Session-aware decay, not wall-clock | Knowledge survives holidays and downtime |
| Soft bias for identity, not hard gates | Everything is learned; self-aligned knowledge just survives longer |
| Retrieval is pure; reinforcement is separate | Clean separation of read path and side effects |
| LiteLLM as unified backend | One adapter for 100+ providers; switch with config |
| Mock-first testing | All logic verified without API keys; adapters are thin wrappers |

## License

MIT

## Acknowledgements

elfmem was designed through 26 structured explorations and 6 subsystem playgrounds, building mathematical confidence in every architectural decision before writing code. The complete design documentation is in `sim/explorations/`.
