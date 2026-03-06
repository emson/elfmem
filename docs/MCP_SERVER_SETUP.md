# elfmem MCP Server Setup & Configuration Guide

**Version:** 0.1.0
**Date:** March 2026
**Target:** Claude Code, local development, and production deployment

---

## Common Error: "MCP server requires the 'mcp' extra"

**If you see this error when running `elfmem serve`:**
```
MCP server requires the 'mcp' extra:
  pip install 'elfmem[mcp]'  or  uv add 'elfmem[mcp]'
```

**Root cause:** The optional dependencies weren't installed (you ran `uv sync` without extras).

**Quick fix (do this now):**
```bash
cd ~/Dropbox/devel/projects/ai/elf0_mem_sim
uv sync --extra mcp --extra cli  # <-- This is the critical step!
```

**Why this works:**
- `uv sync` reads `pyproject.toml` and installs the local package
- `--extra mcp --extra cli` includes optional dependencies (fastmcp, typer)
- This is NOT the same as `uv add elfmem[mcp]` (which would try to add a third-party package and fail)

**Verify it worked:**
```bash
uv run python -c "import fastmcp; print('✓ fastmcp installed')"
uv run elfmem serve --help  # Should show help, not an error
```

---

## Quick Start (5 minutes)

If you're already set up, start the server with:

```bash
# With environment variables
export ELFMEM_DB=~/.elfmem/agent.db
export ELFMEM_CONFIG=~/.elfmem/config.yaml
elfmem serve

# Or with explicit flags (preferred for scripts)
elfmem serve --db ~/.elfmem/agent.db --config ~/.elfmem/config.yaml
```

Check that `~/.elfmem/` exists with `agent.db` and `config.yaml`. If not, follow **Full Setup** below.

---

## Full Setup Guide (Step-by-Step)

### Step 1: Install elfmem with MCP and CLI support

```bash
cd ~/Dropbox/devel/projects/ai/elf0_mem_sim

# CRITICAL: Install with BOTH MCP and CLI extras
# Use --extra (singular) repeated for each extra you want
uv sync --extra mcp --extra cli

# Verify installation
uv run elfmem --help
```

⚠️ **Important:** You **must** include `--extra mcp --extra cli` in the sync command. Running just `uv sync` will install base dependencies only and cause "MCP server requires the 'mcp' extra" errors later. See **Troubleshooting** section if you get that error.

Also: Never run `uv add elfmem[mcp]` — it will fail with a self-dependency error. Always use `uv sync --extra mcp --extra cli` instead.

**Expected output:**
```
Usage: elfmem [OPTIONS] COMMAND [ARGS]...

Adaptive memory for AI agents.

Options:
  --help  Show this message and exit.

Commands:
  curate    Archive decayed blocks, prune weak edges, reinforce top knowledge.
  guide     Show documentation for a specific operation, or the full overview.
  outcome   Record domain outcome signal [0.0-1.0] to update block confidence.
  recall    Retrieve relevant knowledge, rendered for prompt injection.
  remember  Store knowledge for future retrieval.
  serve     Start the elfmem MCP server for agent tool integration.
  status    System health and suggested next action.
```

### Step 2: Create the ~/.elfmem directory

```bash
mkdir -p ~/.elfmem
```

### Step 3: Initialize the database

The database (`agent.db`) will be created automatically on first run. However, you can initialize it explicitly:

```bash
# The database is auto-created; just verify with status
uv run elfmem status --db ~/.elfmem/agent.db --config ~/.elfmem/config.yaml
```

**Expected output (first time):**
```
System health snapshot:
├─ Sessions: 1 active
├─ Blocks: 0 active, 0 archived
├─ Memory health: empty (ready to learn)
├─ Session tokens: usage(prompt=0, completion=0, total=0)
├─ Lifetime tokens: usage(prompt=..., completion=..., total=...)
└─ Next action: Start learning with elfmem_remember()
```

### Step 4: Set up configuration

Copy the default config (already at `~/.elfmem/config.yaml`), or create a custom one:

**Option A: Use existing config**
```bash
# It's already there with sensible defaults
cat ~/.elfmem/config.yaml
```

**Option B: Create a custom config**

Create `~/.elfmem/config.yaml`:

```yaml
# elfmem configuration for your agent
# See docs/amgs_architecture.md for parameter meanings

llm:
  model: "claude-haiku-4-5-20251001"  # Fast, cheap LLM for analysis
  temperature: 0.0                    # Deterministic reasoning
  max_tokens: 512
  timeout: 30
  max_retries: 3

embeddings:
  model: "text-embedding-3-small"     # OpenAI embeddings
  dimensions: 1536
  timeout: 30

memory:
  # Consolidation thresholds
  inbox_threshold: 10                 # Promote after 10 blocks learned
  curate_interval_hours: 40.0         # Archive schedule (~2 days)

  # Quality gates
  self_alignment_threshold: 0.70      # Min confidence to keep blocks
  contradiction_threshold: 0.80       # Flag conflicting beliefs
  near_dup_exact_threshold: 0.95      # Reject near-exact copies
  near_dup_near_threshold: 0.90       # Merge similar blocks

  # Graph tuning
  similarity_edge_threshold: 0.60     # Create edges for related blocks
  edge_degree_cap: 10                 # Prevent over-connection

  # Retrieval
  top_k: 5                            # Return top-5 blocks per query
  search_window_hours: 200.0          # Look back 200 hours (~8 days)

  # Outcome feedback
  outcome_prior_strength: 2.0         # LLM alignment prior weight
  outcome_reinforce_threshold: 0.5    # Threshold to reinforce
  penalize_threshold: 0.20            # Threshold to accelerate decay

# Optional: Custom prompts (uncomment to override)
# prompts:
#   process_block_file: "~/.elfmem/prompts/process_block.txt"
#   contradiction_file: "~/.elfmem/prompts/contradiction.txt"
```

### Step 5: Set up environment variables (recommended)

Add to `~/.zshrc` or `~/.bashrc`:

```bash
# elfmem configuration
export ELFMEM_DB=~/.elfmem/agent.db
export ELFMEM_CONFIG=~/.elfmem/config.yaml
```

Then reload:
```bash
source ~/.zshrc  # or ~/.bashrc
```

### Step 6: Configure for Claude Code (MCP integration)

Update `.claude/claude-code.yaml` in your project root:

```yaml
mcpServers:
  elfmem:
    command: elfmem
    args:
      - serve
      - --db
      - ~/.elfmem/agent.db
      - --config
      - ~/.elfmem/config.yaml
```

**What this does:**
- Registers `elfmem` as an MCP server in Claude Code
- Claude Code will start the server automatically when you open the project
- All tools (`elfmem_remember`, `elfmem_recall`, etc.) become available to Claude

### Step 7: Verify the setup

```bash
# Test each CLI command
uv run elfmem remember "Test fact" --db ~/.elfmem/agent.db

uv run elfmem recall "test" --db ~/.elfmem/agent.db

uv run elfmem status --db ~/.elfmem/agent.db

# Test the MCP server (starts in background)
uv run elfmem serve --db ~/.elfmem/agent.db --config ~/.elfmem/config.yaml &
sleep 2

# Check logs
ps aux | grep elfmem
```

---

## Configuration Best Practices

### LLM Provider Selection

**For Claude Code (recommended):**
```yaml
llm:
  model: "claude-haiku-4-5-20251001"  # Fast, accurate, cheap
  temperature: 0.0                    # Deterministic block analysis
```

**For other providers via LiteLLM:**
```yaml
# OpenAI (explicit)
llm:
  model: "gpt-4-turbo"
  temperature: 0.0

# Anthropic (via litellm prefix)
llm:
  model: "claude-opus-4-6"
  temperature: 0.0

# Local Ollama
llm:
  model: "ollama/mistral"
  temperature: 0.0

# Groq
llm:
  model: "groq/mixtral-8x7b-32768"
  temperature: 0.0
```

**Credentials:** Set API keys in environment:
```bash
export OPENAI_API_KEY="sk-..."
export ANTHROPIC_API_KEY="sk-ant-..."
export GROQ_API_KEY="..."
```

### Embedding Model Selection

**Default (recommended):**
```yaml
embeddings:
  model: "text-embedding-3-small"     # Fast, 1536 dims, cheap
  dimensions: 1536
```

**Alternatives via LiteLLM:**
```yaml
# OpenAI larger model
embeddings:
  model: "text-embedding-3-large"
  dimensions: 3072

# Local (Ollama)
embeddings:
  model: "ollama/nomic-embed-text"
  dimensions: 768

# Cohere
embeddings:
  model: "cohere.embed-english-v3.0"
  dimensions: 1024
```

### Memory Tuning

**For 50–100 blocks (MVP):**
```yaml
memory:
  inbox_threshold: 5                  # Promote sooner
  curate_interval_hours: 72.0         # Curate weekly
  self_alignment_threshold: 0.65      # Lenient
  top_k: 3                            # Small result set
```

**For 100–500 blocks (production):**
```yaml
memory:
  inbox_threshold: 10                 # Standard
  curate_interval_hours: 40.0         # Twice weekly
  self_alignment_threshold: 0.70      # Balanced
  top_k: 5                            # Richer context
```

**For cost-sensitive deployment:**
```yaml
llm:
  max_tokens: 256                     # Reduce output tokens
  timeout: 15                         # Faster timeout
memory:
  curate_interval_hours: 100.0        # Curate less often
  edge_degree_cap: 5                  # Fewer graph connections
```

### Custom Prompts

To override default prompts, create prompt files:

**~/.elfmem/prompts/process_block.txt:**
```
You are a knowledge classifier for an adaptive memory system.
Classify each fact according to [YOUR CRITERIA].
Output JSON with: alignment_score, tag_list, reasoning.
```

**~/.elfmem/prompts/contradiction.txt:**
```
You are a fact-checker. Identify contradictions between:
[BLOCK A] and [BLOCK B].
Output JSON with: contradiction_score, reasoning.
```

Then reference in config:
```yaml
prompts:
  process_block_file: "~/.elfmem/prompts/process_block.txt"
  contradiction_file: "~/.elfmem/prompts/contradiction.txt"
```

---

## Directory Structure

After setup, your `~/.elfmem/` should look like:

```
~/.elfmem/
├── agent.db                    # SQLite database (created auto)
├── config.yaml                 # Configuration (provided)
└── prompts/ (optional)
    ├── process_block.txt
    └── contradiction.txt
```

Your project:

```
elf0_mem_sim/
├── .claude/
│   └── claude-code.yaml        # MCP server registration
├── src/elfmem/
│   ├── mcp.py                  # MCP server (FastMCP)
│   └── cli.py                  # CLI commands
├── docs/
│   ├── MCP_SERVER_SETUP.md     # This file
│   └── amgs_architecture.md    # Full specification
└── sim/
    ├── explorations/           # 26 design docs
    └── playgrounds/            # Interactive specs
```

---

## Troubleshooting

### Issue: "MCP server requires the 'mcp' extra"

**Error:**
```
MCP server requires the 'mcp' extra:
  pip install 'elfmem[mcp]'  or  uv add 'elfmem[mcp]'
```

**Root cause:** Optional dependencies not installed. You probably ran `uv sync` without the `--extra` flags.

**Solution:**
```bash
cd ~/Dropbox/devel/projects/ai/elf0_mem_sim

# CRITICAL: Use this exact command to install optional extras
# Note: it's --extra (singular), repeated for each extra
uv sync --extra mcp --extra cli

# NOT this (it fails with self-dependency error):
# uv add elfmem[mcp]  ✗ WRONG - this tries to add a third-party package

# Verify it worked
uv run python -c "import fastmcp; print('✓ OK')"
uv run elfmem serve --db ~/.elfmem/agent.db --config ~/.elfmem/config.yaml
```

**Why `uv add elfmem[mcp]` fails:**
- The project is named `elfmem` in `pyproject.toml`
- When you run `uv add elfmem[mcp]`, uv tries to add a third-party package named `elfmem` to the local `elfmem` package
- This creates a self-dependency, which uv forbids
- **Always use `uv sync --extra mcp --extra cli` instead** — this installs the local package with its optional extras

**uv syntax note:** The correct flag is `--extra` (singular), not `--extras`. Use it once per extra you want.

### Issue: `ModuleNotFoundError: No module named 'elfmem'`

**Solution:**
```bash
cd ~/Dropbox/devel/projects/ai/elf0_mem_sim
uv sync --extras "mcp,cli"
uv run elfmem serve --db ~/.elfmem/agent.db
```

### Issue: `Error: --db is required (or set ELFMEM_DB env var)`

**Solution:** Set up environment variables (Step 5 above):
```bash
export ELFMEM_DB=~/.elfmem/agent.db
export ELFMEM_CONFIG=~/.elfmem/config.yaml
```

### Issue: MCP server starts but Claude can't see tools

**Solution:**
1. Check `.claude/claude-code.yaml` is correctly formatted (no YAML syntax errors)
2. Restart Claude Code (`Cmd+Q` then reopen)
3. Verify server is running: `ps aux | grep elfmem`
4. Check server logs: the server writes to stderr, so look for errors in Claude's MCP logs

### Issue: `OPENAI_API_KEY not set` or similar

**Solution:** Set credentials in environment before running:
```bash
export OPENAI_API_KEY="sk-..."
export ANTHROPIC_API_KEY="sk-ant-..."
uv run elfmem serve --db ~/.elfmem/agent.db --config ~/.elfmem/config.yaml
```

### Issue: Database locked / concurrent access

**Solution:**
- SQLite can handle one writer at a time. If you run multiple elfmem instances, they'll queue.
- For concurrent agents, switch to PostgreSQL (future enhancement).
- For now: one MCP server per project, or one CLI session at a time.

### Issue: Embedding dimension mismatch

**Error:** `ValueError: embedding dimension mismatch`

**Solution:** Verify `config.yaml` dimensions match your embedding model:
```yaml
embeddings:
  model: "text-embedding-3-small"     # MUST be 1536
  dimensions: 1536                    # MUST match model
```

If you change the embedding model, **delete `agent.db`** and restart (can't re-embed existing blocks with different dimensionality).

---

## Running in Production

### Local Development
```bash
# Terminal 1: Start the MCP server
export ELFMEM_DB=~/.elfmem/agent.db
export ELFMEM_CONFIG=~/.elfmem/config.yaml
uv run elfmem serve

# Terminal 2: Use Claude Code (auto-connects via .claude/claude-code.yaml)
```

### Long-Running Process
```bash
# Start server in background with nohup
nohup uv run elfmem serve \
  --db ~/.elfmem/agent.db \
  --config ~/.elfmem/config.yaml \
  > ~/.elfmem/server.log 2>&1 &

# Check logs
tail -f ~/.elfmem/server.log

# Stop
pkill -f "elfmem serve"
```

### Docker (Future)
```dockerfile
FROM python:3.12-slim
RUN pip install elfmem[mcp,cli]
ENTRYPOINT ["elfmem", "serve", "--db", "/app/agent.db", "--config", "/app/config.yaml"]
```

### Environment Variables Reference

| Variable | Default | Example |
|----------|---------|---------|
| `ELFMEM_DB` | (required) | `~/.elfmem/agent.db` |
| `ELFMEM_CONFIG` | (optional) | `~/.elfmem/config.yaml` |
| `OPENAI_API_KEY` | (required for OpenAI) | `sk-...` |
| `ANTHROPIC_API_KEY` | (required for Anthropic) | `sk-ant-...` |
| `GROQ_API_KEY` | (required for Groq) | `gsk_...` |

---

## Next Steps

1. **Read the architecture:** `docs/amgs_architecture.md` (full system spec)
2. **Explore examples:** `sim/explorations/` (26 design decision docs)
3. **Start remembering:** Use `elfmem remember` to add facts
4. **Query:** Use `elfmem recall` or Claude tools to retrieve knowledge
5. **Feedback:** Use `elfmem outcome` to teach the system from results
6. **Monitor:** Run `elfmem status` to check memory health

---

## Advanced: Custom Configuration Per Agent

If you're managing multiple agents, create separate configs:

```bash
mkdir -p ~/.elfmem/{agent-alice,agent-bob}

# Agent Alice
cp ~/.elfmem/config.yaml ~/.elfmem/agent-alice/config.yaml
elfmem serve \
  --db ~/.elfmem/agent-alice/alice.db \
  --config ~/.elfmem/agent-alice/config.yaml

# Agent Bob
cp ~/.elfmem/config.yaml ~/.elfmem/agent-bob/config.yaml
elfmem serve \
  --db ~/.elfmem/agent-bob/bob.db \
  --config ~/.elfmem/agent-bob/config.yaml
```

---

## Reference: MCP Tools

Once the server is running, these tools are available:

| Tool | Purpose |
|------|---------|
| `elfmem_remember(content, tags?)` | Store knowledge |
| `elfmem_recall(query, top_k?, frame?)` | Retrieve knowledge |
| `elfmem_status()` | Check memory health |
| `elfmem_outcome(block_ids, signal, weight?, source?)` | Record feedback |
| `elfmem_curate()` | Archive & reinforce |
| `elfmem_guide(method?)` | Get documentation |

See `src/elfmem/mcp.py` for full signatures.

---

## Support

- **Questions about elfmem:** See `sim/explorations/` and `docs/amgs_architecture.md`
- **Issues with MCP:** Check `.claude/claude-code.yaml` syntax and server logs
- **Configuration help:** See the examples above and `docs/amgs_architecture.md`

