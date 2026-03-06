# elfmem Setup and Configuration Guide

Complete guide to setting up environment variables and configuration for elfmem MCP server and CLI tools.

## Table of Contents
- [Quick Start](#quick-start)
- [Initialisation Commands](#initialisation-commands)
- [Environment Variables](#environment-variables)
- [YAML Configuration](#yaml-configuration)
- [MCP Server Setup](#mcp-server-setup)
- [Configuration Methods](#configuration-methods)
- [Examples](#examples)

---

## Quick Start

### First-Time Setup

Run `elfmem init` once before anything else. It creates `~/.elfmem/`, generates a default `config.yaml`, and optionally seeds your SELF frame (agent identity):

```bash
# Basic init (config + database only)
elfmem init

# Recommended: seed agent identity at the same time
elfmem init --self "I am an AI assistant focused on [your purpose]."

# Verify everything is ready
elfmem doctor
```

`elfmem init` is idempotent — safe to re-run. Config is skipped if it already exists (pass `--force` to overwrite). Duplicate SELF content is silently rejected.

### Minimal MCP Server (All Defaults)
```bash
# After init, start the server
export ELFMEM_DB=~/.elfmem/agent.db
export ELFMEM_CONFIG=~/.elfmem/config.yaml
elfmem serve
```

### With Custom LLM Provider
```bash
# Use Ollama (local)
export ELFMEM_DB=~/.elfmem/agent.db
export ELFMEM_LLM_MODEL="ollama/llama3.2"
export ELFMEM_LLM_BASE_URL="http://localhost:11434"

elfmem serve --db $ELFMEM_DB
```

### With Config File
```bash
export ELFMEM_DB=~/.elfmem/agent.db
export ELFMEM_CONFIG=~/.elfmem/config.yaml

elfmem serve
```

---

## Initialisation Commands

### `elfmem init`

Creates the elfmem data directory, generates a default config, and optionally seeds the SELF frame.

```bash
elfmem init [OPTIONS]

Options:
  --self TEXT      Natural language description of agent identity (seeds SELF frame)
  --db TEXT        Database path (default: ~/.elfmem/agent.db, env: ELFMEM_DB)
  --config TEXT    Config YAML path (default: ~/.elfmem/config.yaml, env: ELFMEM_CONFIG)
  --force          Overwrite existing config (default: skip if exists)
  --json           Output JSON instead of human-readable text
```

**When to use:**
- First run — before `elfmem serve` or any CLI/MCP tool use
- When you want to add or update agent identity (re-running with `--self` is safe; duplicates are rejected)
- After changing config defaults and wanting to regenerate `config.yaml` (use `--force`)

**When NOT to use:**
- Every session — SELF blocks persist; don't re-seed on every agent start
- In production pipelines — use `elfmem_setup` MCP tool for programmatic identity seeding

### `elfmem doctor`

Read-only diagnostic command. Checks config, database, SELF blocks, and API keys. No side effects.

```bash
elfmem doctor [OPTIONS]

Options:
  --db TEXT        Database path (default: ~/.elfmem/agent.db, env: ELFMEM_DB)
  --config TEXT    Config YAML path (default: ~/.elfmem/config.yaml, env: ELFMEM_CONFIG)
  --json           Output JSON
```

**Output example:**
```
Config dir:  ~/.elfmem/ (exists)
Config:      ~/.elfmem/config.yaml (exists)
Database:    ~/.elfmem/agent.db (exists)
SELF:        3 SELF blocks found
API keys:    ANTHROPIC_API_KEY set
```

Exits `0` if everything is healthy, `1` if any check fails (useful for CI/CD).

### `elfmem_setup` (MCP tool)

Agent-native equivalent of `elfmem init --self`. Call it from Claude or any MCP client to seed the SELF frame programmatically:

```python
elfmem_setup(
    identity="I am Claude Code, an AI-powered software engineering assistant.",
    values=["write minimal clean code", "confirm before destructive operations"]
)
# Returns: {"status": "setup_complete", "blocks_created": 2, "blocks": [...]}
```

Safe to call multiple times — exact duplicates are silently rejected.

---

## Environment Variables

All env vars are **optional**. LiteLLM reads provider API keys from standard env vars automatically.

### Database & Config Paths

| Variable | Purpose | Example |
|----------|---------|---------|
| `ELFMEM_DB` | SQLite database file path | `/data/agent.db` |
| `ELFMEM_CONFIG` | YAML config file path | `./config/elfmem.yaml` |

### LLM Configuration

| Variable | Default | Example | Notes |
|----------|---------|---------|-------|
| `ELFMEM_LLM_MODEL` | `claude-sonnet-4-6` | `gpt-4o-mini`, `ollama/llama3.2` | LiteLLM model string |
| `ELFMEM_LLM_BASE_URL` | `None` | `http://localhost:11434` | Optional: proxy/local endpoint |

### Embedding Configuration

| Variable | Default | Example | Notes |
|----------|---------|---------|-------|
| `ELFMEM_EMBEDDING_MODEL` | `text-embedding-3-small` | `nomic-embed-text` | OpenAI/Ollama embeddings |
| `ELFMEM_EMBEDDING_DIMENSIONS` | `1536` | `768` | Must match model output |
| `ELFMEM_EMBEDDING_BASE_URL` | `None` | `http://localhost:11434` | Ollama or proxy endpoint |

### Provider API Keys (LiteLLM Format)

| Provider | Env Var | Example |
|----------|---------|---------|
| OpenAI | `OPENAI_API_KEY` | `sk-...` |
| Anthropic | `ANTHROPIC_API_KEY` | `sk-ant-...` |
| Groq | `GROQ_API_KEY` | `gsk_...` |

LiteLLM automatically reads these standard env vars. No additional setup needed.

---

## YAML Configuration

Three-tier configuration override system:
1. **Inline strings** (highest priority) — in YAML
2. **File paths** — referenced from YAML
3. **Code defaults** — library defaults

### Full Configuration File Example

```yaml
# elfmem.yaml
llm:
  model: "claude-sonnet-4-6"
  temperature: 0.0
  max_tokens: 512
  timeout: 30
  max_retries: 3
  base_url: null
  # Per-call model overrides (optional)
  process_block_model: null        # Use model above if null
  contradiction_model: null

embeddings:
  model: "text-embedding-3-small"
  dimensions: 1536
  timeout: 30
  base_url: null

memory:
  # Lifecycle
  inbox_threshold: 10
  curate_interval_hours: 40.0
  prune_threshold: 0.05
  search_window_hours: 200.0
  vector_n_seeds_multiplier: 4

  # Quality thresholds
  self_alignment_threshold: 0.70
  contradiction_threshold: 0.80
  near_dup_exact_threshold: 0.95
  near_dup_near_threshold: 0.90

  # Graph
  similarity_edge_threshold: 0.60
  edge_degree_cap: 10
  edge_prune_threshold: 0.10
  edge_reinforce_delta: 0.10

  # Scoring
  top_k: 5
  curate_reinforce_top_n: 5

  # Outcome scoring
  outcome_prior_strength: 2.0
  outcome_reinforce_threshold: 0.5
  penalize_threshold: 0.20
  penalty_factor: 2.0
  lambda_ceiling: 0.050

prompts:
  # Inline overrides (None = use library defaults)
  process_block: null              # Combined block analysis prompt
  contradiction: null

  # File-based overrides
  process_block_file: null         # Path to custom process_block prompt
  contradiction_file: null

  # Custom tag vocabulary
  valid_self_tags: null            # null = use library defaults
```

### Minimal Configuration File

```yaml
# config-minimal.yaml
llm:
  model: "claude-sonnet-4-6"

embeddings:
  model: "text-embedding-3-small"
```

### Local Ollama Setup

```yaml
# config-ollama.yaml
llm:
  model: "ollama/llama3.2"
  base_url: "http://localhost:11434"
  temperature: 0.0
  max_tokens: 512

embeddings:
  model: "nomic-embed-text"
  dimensions: 768
  base_url: "http://localhost:11434"
```

### Custom Prompts (Inline)

```yaml
# config-custom-prompts.yaml
llm:
  model: "gpt-4o-mini"

prompts:
  process_block: |
    You are analyzing a memory block for a research assistant.

    Agent Identity:
    {self_context}

    Block:
    {block}

    Return JSON with: alignment_score [0-1], tags [list], summary [string]

  contradiction: |
    Are these two research findings contradictory?

    Finding A: {block_a}
    Finding B: {block_b}

    Return JSON: {"score": <0.0-1.0>}
```

### Custom Prompts (File-Based)

```yaml
# config-file-prompts.yaml
llm:
  model: "claude-sonnet-4-6"

prompts:
  process_block_file: ./prompts/my_analysis.txt
  contradiction_file: ./prompts/my_contradiction.txt

  # Custom tag vocabulary
  valid_self_tags:
    - "self/identity"
    - "self/value"
    - "self/preference"
    - "self/goal"
    - "self/constitutional"
    - "self/skill"
```

### Per-Call Model Overrides

```yaml
# Use GPT-4 for block analysis (expensive), but gpt-4o-mini for contradictions
llm:
  model: "gpt-4o-mini"
  process_block_model: "gpt-4"     # Override for block analysis
  contradiction_model: null         # Use model above
```

---

## MCP Server Setup

### Starting the Server

#### Using CLI
```bash
# With env vars
export ELFMEM_DB=/data/agent.db
export ELFMEM_CONFIG=./config/elfmem.yaml
elfmem serve

# With arguments
elfmem serve --db /data/agent.db --config ./config/elfmem.yaml
```

#### Programmatically
```python
from elfmem.mcp import main

# Start with defaults and env var config
main(db_path="/data/agent.db")

# Start with explicit config path
main(db_path="/data/agent.db", config_path="./elfmem.yaml")
```

### Integration with Claude Desktop

Create `.claude/claude.json`:

```json
{
  "mcpServers": {
    "elfmem": {
      "command": "elfmem",
      "args": ["serve", "--db", "$HOME/agent.db", "--config", "$HOME/elfmem.yaml"],
      "env": {
        "OPENAI_API_KEY": "${OPENAI_API_KEY}",
        "ANTHROPIC_API_KEY": "${ANTHROPIC_API_KEY}",
        "GROQ_API_KEY": "${GROQ_API_KEY}"
      }
    }
  }
}
```

### Integration with Other MCP Clients

```bash
# Generic MCP launcher
mcp-run elfmem serve --db ./agent.db --config ./elfmem.yaml
```

---

## Configuration Methods

### Priority Order (Highest → Lowest)

1. **Command-line arguments**
   ```bash
   elfmem serve --db /path/to/db --config /path/to/config.yaml
   ```

2. **Environment variables**
   ```bash
   export ELFMEM_DB=/path/to/db
   export ELFMEM_CONFIG=/path/to/config.yaml
   export ELFMEM_LLM_MODEL=gpt-4o-mini
   ```

3. **Config file values** (YAML)
   ```yaml
   llm:
     model: "claude-sonnet-4-6"
   ```

4. **Code defaults** (library built-ins)
   - `model: "claude-sonnet-4-6"`
   - `embeddings.model: "text-embedding-3-small"`
   - `embeddings.dimensions: 1536`

### Resolution Flow

```
Command-line arg
  ↓ (if not provided)
Env var (ELFMEM_*)
  ↓ (if not set)
YAML config value
  ↓ (if not specified)
Library default
```

### MemorySystem.from_config()

The Python API offers flexible configuration:

```python
from elfmem.api import MemorySystem

# 1. From YAML file
system = await MemorySystem.from_config(
    "agent.db",
    config="./elfmem.yaml"
)

# 2. From env vars (ELFMEM_*)
system = await MemorySystem.from_config("agent.db")

# 3. From inline dict
system = await MemorySystem.from_config(
    "agent.db",
    config={
        "llm": {"model": "gpt-4o-mini"},
        "embeddings": {"model": "text-embedding-3-small"}
    }
)

# 4. From pre-built ElfmemConfig object
from elfmem.config import ElfmemConfig
cfg = ElfmemConfig.from_yaml("./elfmem.yaml")
system = await MemorySystem.from_config("agent.db", config=cfg)

# 5. From environment variables only
system = await MemorySystem.from_env("agent.db")
```

---

## Examples

### Example 1: OpenAI (Quickest)

**Setup:**
```bash
# 1. Install with CLI extra
pip install 'elfmem[cli]'  # or: uv add 'elfmem[cli]'

# 2. Set API key (already installed, OpenAI reads OPENAI_API_KEY)
export OPENAI_API_KEY=sk-...

# 3. Set database
export ELFMEM_DB=~/.elfmem/agent.db

# 4. Start
elfmem serve
```

All defaults work: Claude Sonnet embeddings via OpenAI.

### Example 2: Anthropic with Custom Config

**Setup:**
```bash
# 1. Install
pip install 'elfmem[cli]'

# 2. Set API key
export ANTHROPIC_API_KEY=sk-ant-...

# 3. Create config
cat > elfmem.yaml <<EOF
llm:
  model: "anthropic/claude-opus-4-6"
  temperature: 0.0
  max_tokens: 1024

embeddings:
  model: "text-embedding-3-small"
EOF

# 4. Start
export ELFMEM_DB=./agent.db
export ELFMEM_CONFIG=./elfmem.yaml
elfmem serve
```

### Example 3: Ollama (Self-Hosted)

**Setup:**
```bash
# 1. Install Ollama, pull models
ollama pull llama3.2
ollama pull nomic-embed-text

# 2. Start Ollama server (default: http://localhost:11434)
ollama serve

# 3. Create config (in another terminal)
cat > ollama-config.yaml <<EOF
llm:
  model: "ollama/llama3.2"
  base_url: "http://localhost:11434"

embeddings:
  model: "nomic-embed-text"
  dimensions: 768
  base_url: "http://localhost:11434"

memory:
  top_k: 5
EOF

# 4. Start server
export ELFMEM_DB=./agent.db
elfmem serve --db $ELFMEM_DB --config ./ollama-config.yaml
```

### Example 4: Docker Deployment

**Dockerfile:**
```dockerfile
FROM python:3.12-slim

WORKDIR /app

RUN pip install 'elfmem[cli,mcp]'

# Create data directory
RUN mkdir -p /data

ENTRYPOINT ["elfmem", "serve"]
CMD ["--db", "/data/agent.db"]
```

**docker-compose.yml:**
```yaml
version: "3.8"

services:
  elfmem:
    build: .
    environment:
      ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY}
      ELFMEM_CONFIG: /etc/elfmem/config.yaml
      OPENAI_API_KEY: ${OPENAI_API_KEY}
    volumes:
      - ./data:/data
      - ./elfmem.yaml:/etc/elfmem/config.yaml:ro
    ports:
      - "5173:5173"  # MCP port

  ollama:  # Optional: self-hosted LLM/embeddings
    image: ollama/ollama
    ports:
      - "11434:11434"
    volumes:
      - ./ollama-data:/root/.ollama
```

**Run:**
```bash
docker-compose up -d
```

### Example 5: Development Setup

**Project structure:**
```
my-agent/
├── .env                    # Private secrets (git-ignored)
├── .env.example           # Template (committed)
├── config/
│   ├── elfmem-dev.yaml    # Development config
│   └── elfmem-prod.yaml   # Production config
├── prompts/
│   ├── process_block.txt
│   └── contradiction.txt
└── data/
    └── agent.db           # SQLite database
```

**.env**
```bash
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
ELFMEM_DB=./data/agent.db
ELFMEM_CONFIG=./config/elfmem-dev.yaml
```

**.env.example**
```bash
ANTHROPIC_API_KEY=
OPENAI_API_KEY=
ELFMEM_DB=./data/agent.db
ELFMEM_CONFIG=./config/elfmem-dev.yaml
```

**config/elfmem-dev.yaml**
```yaml
llm:
  model: "claude-haiku-4-5-20251001"  # Fast & cheap for dev
  max_tokens: 512

embeddings:
  model: "text-embedding-3-small"

memory:
  inbox_threshold: 5  # Consolidate faster in dev
  curate_interval_hours: 1.0
```

**config/elfmem-prod.yaml**
```yaml
llm:
  model: "claude-opus-4-6"  # Best quality for production
  max_tokens: 1024

embeddings:
  model: "text-embedding-3-large"

memory:
  inbox_threshold: 50
  curate_interval_hours: 24.0
```

**Start development:**
```bash
source .env
elfmem serve
```

---

## Verification

### Check Configuration is Applied

```bash
# List current status (includes config snapshot)
elfmem status --db $ELFMEM_DB

# JSON output for scripting
elfmem status --db $ELFMEM_DB --json | jq .
```

### Verify LLM/Embedding Connection

```bash
# Test a simple remember-recall cycle
elfmem remember "Test memory" --db $ELFMEM_DB
elfmem recall "test" --db $ELFMEM_DB --top-k 1

# Check tokens used (embedded in status if token tracking enabled)
elfmem status --db $ELFMEM_DB
```

### Debug Environment

```bash
# Check what ELFMEM_* env vars are set
env | grep ELFMEM

# Check if API keys are available to LiteLLM
export OPENAI_API_KEY=sk-test
python -c "import litellm; litellm.embedding_cost = True; print('LiteLLM OK')"
```

---

## Common Scenarios

### "Model not found" error
**Check:**
```bash
# 1. Verify env var or config
echo $ELFMEM_LLM_MODEL

# 2. Verify API key
echo $ANTHROPIC_API_KEY | head -c 10

# 3. Verify LiteLLM supports it
python -c "import litellm; print(litellm.model_list)"
```

### "Embedding dimensions mismatch" error
**Fix:**
```yaml
# Verify dimensions match the model:
# - text-embedding-3-small: 1536
# - text-embedding-3-large: 3072
# - nomic-embed-text (Ollama): 768

embeddings:
  model: "nomic-embed-text"
  dimensions: 768  # Must match!
```

### "Connection refused to Ollama"
**Check:**
```bash
# 1. Is Ollama running?
curl http://localhost:11434/api/tags

# 2. Verify base_url in config
# 3. Check firewall/networking
```

### High token usage
**Tune in config:**
```yaml
memory:
  top_k: 3  # Retrieve fewer blocks per query

llm:
  max_tokens: 256  # Smaller responses
```

---

## Summary

**Quickest start:**
```bash
export ELFMEM_DB=agent.db
elfmem serve
```

**Production setup:**
```bash
export ELFMEM_DB=/data/agent.db
export ELFMEM_CONFIG=/etc/elfmem/config.yaml
export ANTHROPIC_API_KEY=sk-ant-...
elfmem serve
```

**Local development (Ollama):**
```bash
export ELFMEM_DB=./agent.db
elfmem serve --config ./ollama-config.yaml
```

All three approaches work with the same codebase. Choose based on your deployment model.
