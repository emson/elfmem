# elfmem — Adaptive Memory for AI Agents

**Status:** Agent-friendly interfaces (MCP + CLI) — stable and ready

## What Is elfmem?

elfmem is an **adaptive memory system for AI agents**. It learns what you care about, forgets what you ignore, and adjusts its confidence based on real outcomes.

Think of it as a **semantic memory partner** that:
- **Remembers** facts, preferences, decisions, observations across sessions
- **Retrieves** only the most relevant knowledge when you ask
- **Learns** which knowledge is correct via feedback signals
- **Forgets** gracefully — knowledge decays when unused, but comes back if you need it again

Perfect for:
- AI coding assistants building long-term context
- Chatbots that need consistent personality across conversations
- Autonomous agents that learn from their own outcomes
- Applications that need zero-infrastructure persistence

---

## Quick Start

### Option 1: MCP (Recommended for AI Agents)

If your agent environment supports MCP (Claude Desktop, Claude Code, Cursor, VS Code + Cline, etc.):

```bash
# Start the elfmem MCP server
elfmem serve --db agent_memory.db

# In your agent config, add the MCP transport:
{
  "mcpServers": {
    "elfmem": {
      "command": "elfmem",
      "args": ["serve", "--db", "/absolute/path/to/agent_memory.db"]
    }
  }
}
```

The agent discovers three tools:
- `elfmem_remember` — learn and store knowledge
- `elfmem_recall` — retrieve relevant context
- `elfmem_status` — check memory health

**That's it.** Sessions, consolidation, and decay are managed automatically.

### Option 2: CLI (Fallback, Universal)

If your agent has bash access but no MCP:

```bash
# Store knowledge
elfmem remember "User prefers dark mode" --tags ui,preferences

# Retrieve context
elfmem recall "What UI settings does the user prefer?" --json

# Check status
elfmem status --json
```

### Option 3: Python Library (For Developers)

If you're building an application:

```python
from elfmem import MemorySystem

system = await MemorySystem.from_config("agent.db")
async with system.session():
    await system.learn("User prefers dark mode", tags=["ui", "preferences"])
    result = await system.frame("attention", query="UI preferences")
    print(result.text)  # inject into prompt
```

See [QUICKSTART.md](quickstart.md) for the full guide.

---

## Core Concepts

### Three Core Operations

#### 1. **Remember** — Store knowledge

```
MCP:  elfmem_remember(content, tags?)
CLI:  elfmem remember "content" --tags tag1,tag2
Py:   await system.learn(content, tags=tags)
```

Store a fact, preference, decision, or observation.

**When to use:**
- Agent discovers something worth keeping (user preference, code pattern, design decision)
- After testing completes (test results, code quality metrics)
- When you want to log observations for later analysis

**What happens:**
- Blocks queue in inbox until consolidation
- Session context manager auto-consolidates on exit (if inbox is full)
- No LLM calls yet — learning is instant

**Example:**
```json
{
  "content": "User prefers explicit error handling with try/except blocks",
  "tags": ["code_style", "error_handling", "preference"]
}
```

#### 2. **Recall** — Retrieve relevant knowledge

```
MCP:  elfmem_recall(query, top_k=5)
CLI:  elfmem recall "query" --top-k 5 --json
Py:   await system.frame("attention", query=query, top_k=5)
```

Get rendered context ready to inject into your LLM prompt.

**When to use:**
- Before generating code (to check style preferences)
- Before answering questions (to inject user context)
- Before making decisions (to check relevant history)

**What it returns:**
- Rendered markdown text ready for prompt injection
- Scored blocks (if you need raw data)
- Meta info (which blocks were used, cache status)

**Side effects:**
- Retrieved blocks are automatically reinforced (decay resets)
- No additional calls needed — just use the returned text

**Example:**
```json
{
  "query": "What are the user's code style preferences?",
  "top_k": 5
}
```

Returns:
```
## Code Style Preferences

User prefers explicit error handling with try/except blocks
User uses type hints in all function signatures
User prefers async/await over callbacks
```

#### 3. **Status** — Check memory health

```
MCP:  elfmem_status()
CLI:  elfmem status --json
Py:   await system.status()
```

Get a snapshot of memory state and a suggested action.

**When to use:**
- At the start of a session (to verify memory is healthy)
- If recall returns nothing (to check if memory is empty)
- For monitoring/logging (to track memory usage over time)

**What it tells you:**
- Is a session active? How long?
- How many blocks in inbox, active, archived?
- What's the health status ('good' or 'attention')?
- Suggested next action
- Token usage this session and lifetime

**Example:**
```json
{
  "session_active": true,
  "session_hours": 0.5,
  "inbox_count": 8,
  "inbox_threshold": 10,
  "active_count": 42,
  "health": "good",
  "suggestion": "Inbox nearly full. Consolidation approaching."
}
```

---

## Common Workflows

### Workflow 1: Single-Turn Agent (Stateless)

Agent: I need to generate code. Let me check user preferences first.

```
1. recall("code style preferences")  → get context
2. [generate code using context]
3. Done — memory is managed automatically
```

**No session management needed.** The MCP server manages lifecycle automatically.

### Workflow 2: Multi-Turn Conversation (Stateful)

Agent: I'm starting a long conversation. I'll remember things along the way.

```
1. remember("User said they prefer async/await")
2. [respond to user]
3. remember("User tested the code, all tests passed")
4. [generate next response]
5. Session ends → auto-consolidate if inbox full
```

**Memory accumulates** and can be retrieved in the same session or future sessions.

### Workflow 3: Feedback Loop (Learning)

Agent: The user is testing my work. I should record the outcome.

```
1. recall("my recent code suggestions")  → get block IDs
2. [run tests]
3. if tests_passed:
     outcome(block_ids, signal=1.0)  # "that advice was great"
   else:
     outcome(block_ids, signal=0.0)  # "that advice was wrong"
4. Memory confidence updates automatically
```

**After ~10 outcomes, evidence dominates over the LLM prior.** Your memory learns what actually works.

---

## Advanced Features

### Outcome Feedback (Learning from Results)

Update block confidence based on real-world signals.

```json
{
  "block_ids": ["block_a1b2c3", "block_d4e5f6"],
  "signal": 0.85,
  "source": "test_suite"
}
```

**Signal spectrum (default thresholds):**
- **0.8–1.0**: Confidence UP + reinforce (decay resets). Used for: correct predictions, passing tests, positive feedback
- **0.2–0.8**: Confidence adjusted, no reinforcement (neutral dead-band)
- **0.0–0.2**: Confidence DOWN + decay accelerated (explicit penalization). Used for: failed tests, incorrect predictions, negative feedback

**Example normalisations:**
```python
# Trading forecast that resolved
signal = 1.0 - brier_score  # 0–1 scale

# Coding: tests pass/fail
signal = 1.0 if all_tests_passed else 0.0

# Writing: engagement vs baseline
signal = min(engagement_rate / baseline, 1.0)

# Support: CSAT 1–5 scale
signal = (csat_score - 1.0) / 4.0
```

### Multiple Frames (Different Context Views)

Retrieve different views of your memory:

```
MCP:  elfmem_recall(query, frame="self" | "attention" | "task")
CLI:  elfmem recall "query" --frame attention
Py:   await system.frame("attention", query=query)
```

**Three frames:**
- **`self`** — Identity context (queryless). Agent's personality, values, long-term goals
- **`attention`** — Query-driven. Most relevant knowledge for current task (default)
- **`task`** — Goal/objective context. Current task, constraints, success criteria

**Why frames?** Different retrieval modes for different purposes. Identity is stable; attention is dynamic.

### Curate (Explicit Maintenance)

Manually trigger memory maintenance:

```json
{
  "operation": "curate"
}
```

Runs automatically on a schedule, but you can trigger it explicitly:
- Archive decayed blocks (knowledge that hasn't been used in ~12 days)
- Prune weak graph edges
- Reinforce top-N blocks (most important knowledge)

**When to manually curate:**
- After a very long session with heavy memory use
- When you notice retrieval quality degrading
- For explicit maintenance/cleanup

### Knowledge Graph Expansion

When you recall knowledge, elfmem also surfaces **related-but-not-similar** blocks via graph traversal. This recovers context you might have forgotten about.

Example: You ask about "error handling" and get back not just direct matches, but also related patterns (logging, exceptions, recovery strategies) because they're connected in the graph.

---

## MCP Tool Reference

### `elfmem_remember`

Learn and store knowledge. **Auto-manages session and consolidation.**

**Parameters:**
```json
{
  "content": "string (required) — the knowledge to store",
  "tags": "string[] (optional) — labels for categorization"
}
```

**Returns:**
```json
{
  "block_id": "string — unique ID of stored block",
  "status": "created | duplicate_rejected | near_duplicate_superseded"
}
```

**Cost:** Instant (no LLM calls)

**Typical usage:**
```python
# After discovering a user preference
result = await agent_tools.elfmem_remember(
    content="User prefers async/await over callbacks",
    tags=["code_style", "javascript", "preference"]
)

# After test results
result = await agent_tools.elfmem_remember(
    content="test_my_function passes with current implementation",
    tags=["testing", "code_quality"]
)
```

---

### `elfmem_recall`

Retrieve relevant knowledge, rendered for prompt injection.

**Parameters:**
```json
{
  "query": "string (required) — what to search for",
  "top_k": "integer (optional, default 5) — how many blocks",
  "frame": "string (optional, default 'attention') — 'self' | 'attention' | 'task'"
}
```

**Returns:**
```json
{
  "text": "string — rendered markdown ready for prompt injection",
  "blocks": [
    {
      "id": "string",
      "content": "string",
      "tags": ["string"],
      "score": "number (0–1)"
    }
  ],
  "frame_name": "string",
  "cached": "boolean"
}
```

**Cost:** Fast (embedding call only, no LLM)

**Typical usage:**
```python
# Before generating code
context = await agent_tools.elfmem_recall(
    query="code style preferences"
)
prompt = f"{context['text']}\n\nUser: Write a function to..."

# Check identity context
self_context = await agent_tools.elfmem_recall(
    query=None,
    frame="self"
)
# Returns agent's personality, values, goals
```

---

### `elfmem_status`

Check memory health and get suggested action.

**Parameters:**
```json
{}  // No parameters
```

**Returns:**
```json
{
  "session_active": "boolean",
  "session_hours": "number | null",
  "inbox_count": "integer",
  "inbox_threshold": "integer",
  "active_count": "integer",
  "archived_count": "integer",
  "health": "good | attention",
  "suggestion": "string — recommended next action",
  "session_tokens": {
    "llm_calls": "integer",
    "llm_total_tokens": "integer",
    "embedding_calls": "integer",
    "embedding_tokens": "integer"
  },
  "lifetime_tokens": "{ same structure }"
}
```

**Cost:** Fast (one database read, no LLM)

**Typical usage:**
```python
# At session start
status = await agent_tools.elfmem_status()
if status["health"] == "attention":
    print(f"Warning: {status['suggestion']}")

# Monitor token usage
print(f"This session: {status['session_tokens']}")
print(f"All-time: {status['lifetime_tokens']}")
```

---

### `elfmem_outcome` (Advanced)

Record a domain outcome signal to update block confidence.

**Parameters:**
```json
{
  "block_ids": "string[] (required) — which blocks contributed",
  "signal": "number (required, 0.0–1.0) — outcome quality",
  "weight": "number (optional, default 1.0) — observation weight",
  "source": "string (optional) — audit label"
}
```

**Returns:**
```json
{
  "blocks_updated": "integer",
  "mean_confidence_delta": "number",
  "edges_reinforced": "integer",
  "blocks_penalized": "integer"
}
```

**Cost:** Fast (no LLM calls)

**Typical usage:**
```python
# After tests pass
blocks = await agent_tools.elfmem_recall(
    query="my recent code suggestions"
)
block_ids = [b["id"] for b in blocks["blocks"]]

result = await agent_tools.elfmem_outcome(
    block_ids=block_ids,
    signal=1.0,  # all tests passed
    source="test_suite"
)
print(f"Updated {result['blocks_updated']} blocks")

# After tests fail
result = await agent_tools.elfmem_outcome(
    block_ids=block_ids,
    signal=0.0,  # tests failed
    source="test_suite"
)
print(f"Penalized {result['blocks_penalized']} blocks")
```

---

### `elfmem_curate` (Advanced)

Manually trigger memory maintenance.

**Parameters:**
```json
{}  // No parameters
```

**Returns:**
```json
{
  "archived": "integer — decayed blocks removed",
  "edges_pruned": "integer — weak graph edges removed",
  "reinforced": "integer — top-N blocks boosted"
}
```

**Cost:** Fast (database operations only)

**When to use:**
- After very long sessions with heavy memory use
- When you notice retrieval degrading
- For explicit cleanup

---

### `elfmem_guide` (Self-Help)

Get detailed documentation for a specific operation.

**Parameters:**
```json
{
  "method": "string (optional) — method name or None for overview"
}
```

**Returns:**

A plain string containing formatted documentation. Inject directly into your prompt or print to the user.

**Cost:** Instant

**Typical usage:**
```python
# Get overview
guide = await agent_tools.elfmem_guide()

# Deep dive into specific method
guide = await agent_tools.elfmem_guide(method="outcome")
```

---

## CLI Reference

### `elfmem remember`

```bash
elfmem remember "content" [--tags tag1,tag2] [--category knowledge]
```

**Options:**
- `--tags TAG1,TAG2` — comma-separated labels
- `--category CATEGORY` — block category (default: knowledge)
- `--json` — output JSON instead of text

**Examples:**
```bash
elfmem remember "User prefers dark mode"
elfmem remember "test_my_function passes" --tags testing --json
elfmem remember "Always use explicit error handling" --category preference --tags code_style
```

---

### `elfmem recall`

```bash
elfmem recall "query" [--top-k 5] [--frame attention] [--json]
```

**Options:**
- `--top-k N` — number of blocks (default: 5)
- `--frame FRAME` — self | attention | task (default: attention)
- `--json` — output JSON instead of text

**Examples:**
```bash
elfmem recall "code style preferences"
elfmem recall "error handling patterns" --top-k 10 --json
elfmem recall "" --frame self  # queryless recall — uses identity weights
```

---

### `elfmem status`

```bash
elfmem status [--json]
```

**Options:**
- `--json` — output JSON

**Examples:**
```bash
elfmem status
elfmem status --json | jq '.session_tokens'
```

---

### `elfmem outcome`

```bash
elfmem outcome BLOCK_ID1,BLOCK_ID2 SIGNAL [--weight 1.0] [--source test]
```

**Arguments:**
- `BLOCK_IDS` — comma-separated block IDs
- `SIGNAL` — outcome quality (0.0–1.0)

**Options:**
- `--weight N` — observation weight (default: 1.0)
- `--source LABEL` — audit label
- `--json` — output JSON

**Examples:**
```bash
elfmem outcome abc123,def456 1.0 --source test_suite
elfmem outcome abc123 0.0 --source "wrong_prediction" --weight 2.0 --json
```

---

### `elfmem curate`

```bash
elfmem curate [--json]
```

**Options:**
- `--json` — output JSON

**Examples:**
```bash
elfmem curate
elfmem curate --json
```

---

### `elfmem serve`

Start the elfmem MCP server for agent tool integration.

```bash
elfmem serve --db PATH [--config PATH]
```

**Options:**
- `--db PATH` — path to SQLite database (required, or set `ELFMEM_DB`)
- `--config PATH` — path to YAML config (optional, or set `ELFMEM_CONFIG`)

**Examples:**
```bash
# Basic
elfmem serve --db ~/.memory/agent.db

# With custom config
elfmem serve --db agent.db --config elfmem.yaml

# Using environment variables
export ELFMEM_DB=agent.db
elfmem serve
```

---

## Architecture & Concepts

### How Memory Works

1. **Learn** — Blocks queue in inbox (instant, no LLM)
2. **Consolidate** — Process inbox: score alignment, embed, deduplicate (LLM calls)
3. **Active** — Blocks enter the knowledge graph
4. **Retrieve** — 4-stage hybrid pipeline: pre-filter → vector → graph → composite score
5. **Reinforce** — Retrieved blocks auto-strengthen (decay resets)
6. **Decay** — Unused blocks gradually fade (session-aware, not wall-clock)
7. **Archive** — Stale blocks are removed periodically

### Block Lifecycle

```
inbox → consolidate → active (graph) → retrieve → reinforce ↻
                         ↓
                      decay
                         ↓
                      archive
```

### Decay Tiers

Blocks have different decay rates:

| Tier | Decay Rate | Use Case |
|---|---|---|
| **permanent** | 0.00001 | Core values, mission, non-negotiables |
| **durable** | 0.001 | Important patterns, critical learnings |
| **standard** | 0.010 | Regular knowledge (default) |
| **ephemeral** | 0.050 | Temporary context, current session only |

Decay is **session-aware**: blocks survive holidays (wall-clock) but fade quickly if not used during active sessions.

### Retrieval Pipeline

When you recall, elfmem scores blocks through 4 stages:

1. **Pre-filter** — Quick checks (recency, tags, status)
2. **Vector search** — Semantic similarity to query
3. **Graph expansion** — Related-but-not-similar blocks via knowledge graph
4. **Composite score** — Weighted combination of similarity, confidence, recency, centrality, reinforcement

This recovers context you might not remember to ask for.

---

## Configuration

### Default Config

elfmem works out-of-the-box with sensible defaults:
- LLM: `claude-sonnet-4-6` (via LiteLLM)
- Embeddings: `text-embedding-3-small` (OpenAI)
- Database: SQLite (zero infrastructure)
- Inbox threshold: 10 blocks
- Token budget: unlimited

### Custom Config (YAML)

```yaml
llm:
  model: "claude-sonnet-4-6"
  temperature: 0.7
  timeout: 30
  max_retries: 2

embeddings:
  model: "text-embedding-3-small"
  dimensions: 1536

memory:
  inbox_threshold: 10
  top_k: 5
  decay_period_hours: 24

  # Frame definitions
  attention:
    search_window_hours: 168  # 1 week

  self:
    search_window_hours: null  # all time
```

### Environment Variables

```bash
export ELFMEM_DB="agent_memory.db"
export ELFMEM_CONFIG="elfmem.yaml"
export OPENAI_API_KEY="sk-..."
export ANTHROPIC_API_KEY="sk-ant-..."
```

---

## Troubleshooting

### Recall Returns No Results

**Problem:** `recall()` returns empty blocks.

**Possible causes:**
1. Memory is empty — call `remember()` first
2. Query is too specific — try broader terms
3. Blocks are in inbox (not consolidated yet) — call `consolidate()` or wait for session exit
4. Blocks were archived — check `status()` for archived count

**Solution:**
```bash
# Check what's in memory
elfmem status --json

# Consolidation runs automatically when inbox fills or on session exit
# Check status to see if inbox has pending blocks
elfmem status --json

# Try recall again with broader query
elfmem recall "general knowledge"
```

### Memory Using Too Much Space

**Problem:** Database file is growing quickly.

**Possible causes:**
1. Storing too much raw data (logs, full code files) — store summaries instead
2. Inbox is too large — reduce inbox_threshold
3. Archive is accumulating — curation is not running

**Solution:**
```bash
# Check current status
elfmem status

# Manually curate
elfmem curate

# Check inbox size
elfmem status --json | jq '.inbox_count'

# If large, reduce threshold in config
# inbox_threshold: 5  # was 10
```

### Session Stays Active Too Long

**Problem:** Session doesn't end, preventing new sessions.

**Cause:** MCP server process hung or disconnected abnormally.

**Solution:**
```bash
# Kill the hung process
pkill -f "elfmem serve"

# Restart the MCP server
elfmem serve --db agent_memory.db
```

### Token Usage Unexpectedly High

**Problem:** Consolidation uses more tokens than expected.

**Causes:**
1. Large inbox (one LLM call per block)
2. Multiple models (alignment, tags, contradiction detection)
3. Tall knowledge graph (expansion checks)

**Optimization tips:**
1. Consolidate more frequently (smaller batches) — set `inbox_threshold: 5`
2. Use a cheaper model for alignment scoring — set `alignment_model: "gpt-4o-mini"`
3. Batch multiple `remember()` calls before consolidation

---

## Best Practices for AI Agents

### 1. Remember Decisions, Not Data

❌ Bad:
```python
await remember("""
{
  "filename": "api.ts",
  "content": "[500 lines of code]",
  "error": "Cannot find module 'express'"
}
""")
```

✅ Good:
```python
await remember(
    "User is using TypeScript. Project has missing Express dependency in api.ts",
    tags=["typescript", "dependencies", "error"]
)
```

**Why:** Summaries compress context, are more retrievable, and don't waste space.

### 2. Tag Consistently

❌ Bad:
```python
await remember("User likes dark mode", tags=["dark"])
await remember("User prefers dark UI", tags=["ui-theme"])
await remember("Prefers dark background", tags=["appearance"])
```

✅ Good:
```python
await remember("User prefers dark mode", tags=["ui_theme", "preference"])
await remember("User prefers dark background on all screens", tags=["ui_theme", "preference"])
```

**Why:** Consistent tags improve recall precision.

### 3. Batch Memory Operations

❌ Bad:
```python
for fact in facts:
    await remember(fact)  # Consolidates after each one
```

✅ Good:
```python
for fact in facts:
    await remember(fact)  # Queue all
# Session context manager auto-consolidates on exit
```

**Why:** One consolidation pass is cheaper than N.

### 4. Use Outcome Feedback

❌ Bad:
```python
await remember("User's code patterns")
# [generate code]
# [ignore test results]
```

✅ Good:
```python
blocks = await recall("code generation guidelines")
block_ids = [b["id"] for b in blocks["blocks"]]
# [generate code]
# [run tests]
if tests_passed:
    await outcome(block_ids, signal=1.0, source="test_suite")
else:
    await outcome(block_ids, signal=0.0, source="test_suite")
```

**Why:** Feedback teaches your memory what actually works. After ~10 signals, evidence dominates the LLM prior.

### 5. Respect Context Budgets

❌ Bad:
```python
context = await recall("everything", top_k=100)
prompt = f"{context['text']}\n{user_input}"
```

✅ Good:
```python
context = await recall("user preferences", top_k=5)
prompt = f"{context['text']}\n\n{user_input}"
```

**Why:** Injecting too much context dilutes the signal. Focus on what's relevant.

---

## Performance Characteristics

| Operation | Cost | Speed | Notes |
|---|---|---|---|
| `remember()` | Instant | <10ms | No LLM calls |
| `consolidate()` | LLM calls | 100-500ms per block | Parallelized |
| `recall()` | Embed call | 50-100ms | Cached (TTL) |
| `outcome()` | Fast | <50ms | DB only |
| `curate()` | Fast | <100ms | DB only |
| `status()` | Fast | <10ms | One DB query |

**Database:** SQLite in-process. No network overhead. ~1MB per 100 blocks.

---

## Examples

### Example 1: Code Generation Assistant

```python
# Agent: I'll generate code for the user, remembering their style

async with system.session():
    # Check user preferences
    context = await recall("code style preferences")

    # Generate code
    code = generate_code_with_context(context["text"])

    # Remember the code pattern
    await remember(
        f"Generated {language} code using {pattern} pattern",
        tags=[language, pattern, "generated"]
    )

    # Run tests
    tests_passed = run_tests(code)

    # Record outcome
    block_ids = [b["id"] for b in context["blocks"]]
    signal = 1.0 if tests_passed else 0.0
    await outcome(block_ids, signal=signal, source="test_suite")
```

### Example 2: Conversational Agent

```python
# Agent: I'm having a multi-turn conversation. I remember what I learn.

async with system.session():
    while True:
        user_input = get_user_input()

        # Inject user context
        self_context = await recall(frame="self")  # identity
        attention = await recall(user_input, frame="attention")  # relevant knowledge

        # Generate response
        response = generate_response(
            user_input,
            self_context=self_context["text"],
            knowledge=attention["text"]
        )

        # If user reveals a preference, remember it
        if preference := extract_preference(user_input):
            await remember(preference, tags=["user_preference"])

        send_response(response)
    # Session auto-consolidates on exit
```

### Example 3: Autonomous Agent with Learning Loop

```python
# Agent: I'm autonomous. I learn from outcomes over time.

async with system.session():
    # Get current task
    task = get_task()

    # Check relevant knowledge
    context = await recall(task.description)

    # Execute task
    result = execute_task(task, knowledge=context["text"])

    # Observe outcome (maybe days later via webhook)
    if result.has_outcome():
        signal = normalize_outcome(result.metric)
        block_ids = [b["id"] for b in context["blocks"]]
        await outcome(block_ids, signal=signal, source=result.source)

        # Memory now remembers which knowledge was correct
        # Future recalls will rank correct knowledge higher
```

---

## See Also

- **[QUICKSTART.md](quickstart.md)** — 5-minute walkthrough
- **[SIMULATION_OVERVIEW.md](simulation_overview.md)** — Philosophy and design principles
- **[docs/amgs_architecture.md](./amgs_architecture.md)** — Technical deep dive
- **[sim/EXPLORATIONS.md](https://github.com/emson/elfmem/blob/main/sim/EXPLORATIONS.md)** — Index of 26 design decisions

---

## Support

- **Questions?** Check `elfmem guide` or the examples above
- **Found a bug?** Open an issue on GitHub
- **Want to integrate?** See the Python library documentation in [QUICKSTART.md](quickstart.md)

Happy remembering! 🧠
