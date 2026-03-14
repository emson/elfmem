# Running Multiple MCP Servers for Different Projects

## Overview

This guide explains how to set up and run multiple MCP (Model Context Protocol) servers simultaneously for different Claude Code projects. It covers the architecture, step-by-step setup, and answers the critical question: **Do you need multiple FastMCP server instances?**

**Quick Answer:** No. One elfmem MCP implementation can serve multiple projects via different database paths. Claude Code manages server startup/shutdown automatically.

---

## Part 1: Understanding MCP Architecture

### What is an MCP Server?

An MCP server is a subprocess that Claude Code launches to provide tools and resources. In our case:
- **Command:** `uv run python -m elfmem.mcp`
- **Role:** Exposes elfmem operations (recall, remember, dream, curate, outcome, status, guide)
- **Lifetime:** Claude Code starts it when needed, stops it when switching projects

### Key Insight: MCP Server vs. Database

```
One MCP Server Entry = One Database Instance

┌─────────────────────────────────────────────────────┐
│ ~/.claude/claude_code_config.json                   │
├─────────────────────────────────────────────────────┤
│ "mcpServers": {                                     │
│   "elfmem": {                                       │
│     "command": "uv run python -m elfmem.mcp",       │
│     "env": { "ELFMEM_DB_PATH": "default.db" }       │
│   },                                                │
│   "movemyth_elfmem": {                              │
│     "command": "uv run python -m elfmem.mcp",       │
│     "env": { "ELFMEM_DB_PATH": "movemyth.db" }      │
│   }                                                 │
│ }                                                   │
└─────────────────────────────────────────────────────┘

Both entries use the SAME command
Each has a DIFFERENT database path
```

### Do You Need Multiple FastMCP Server Processes?

**No.** Here's why:

| Aspect | Answer | Why |
|--------|--------|-----|
| **One server instance per project?** | No | Each MCP config entry is independent |
| **Multiple server processes?** | No | Claude Code manages process lifecycle |
| **Port conflicts?** | N/A | MCP uses stdio, not ports |
| **Concurrent access?** | Yes | SQLite handles concurrent reads/writes |
| **Isolation?** | Yes | Each entry has its own database |

**What Actually Happens:**

1. You open **Project A** in Claude Code
   - Claude Code reads system_prompt.md
   - System prompt says: use MCP server "elfmem"
   - Claude Code **starts** the "elfmem" server instance

2. You switch to **Project B**
   - Claude Code reads system_prompt.md
   - System prompt says: use MCP server "movemyth_elfmem"
   - Claude Code **stops** the previous server
   - Claude Code **starts** the "movemyth_elfmem" server instance

3. You switch back to **Project A**
   - Claude Code **stops** the current server
   - Claude Code **restarts** the "elfmem" server instance

This is all automatic. You don't manually start/stop servers.

---

## Part 2: Step-by-Step Setup for Multiple Projects

### Prerequisite: Know Your Project Paths

Before starting, identify your projects:

```
Project A: /Users/emson/Dropbox/devel/projects/ai/elf0_mem_sim
Project B: /Users/emson/Dropbox/devel/projects/move_myth_proj
Project C: (future project)
```

### Phase 1: Create and Register Databases

**Step 1.1:** Create a database file for each project

```bash
# Create directory if it doesn't exist
mkdir -p ~/.elfmem

# Copy base database (if you want to inherit existing knowledge)
cp ~/.elfmem/default.db ~/.elfmem/movemyth.db

# Or initialize fresh (for completely separate knowledge)
# elfmem init ~/.elfmem/movemyth.db  # (command varies by implementation)

# Verify both exist
ls -lh ~/.elfmem/*.db
```

**Output:**
```
-rw-r--r--  3932096 Mar 14 10:22 /Users/emson/.elfmem/default.db
-rw-r--r--  3932096 Mar 14 10:23 /Users/emson/.elfmem/movemyth.db
```

### Phase 2: Register MCP Servers in Global Configuration

**Step 2.1:** Update global MCP config

Edit `~/.claude/claude_code_config.json`:

```json
{
  "mcpServers": {
    "elfmem": {
      "command": "uv",
      "args": ["run", "python", "-m", "elfmem.mcp"],
      "env": {
        "ELFMEM_DB_PATH": "~/.elfmem/default.db",
        "ELFMEM_CONFIG_PATH": "~/.elfmem/config.yaml"
      },
      "alwaysAllow": ["elfmem_recall", "elfmem_remember"]
    },
    "movemyth_elfmem": {
      "command": "uv",
      "args": ["run", "python", "-m", "elfmem.mcp"],
      "env": {
        "ELFMEM_DB_PATH": "~/.elfmem/movemyth.db",
        "ELFMEM_CONFIG_PATH": "~/.elfmem/movemyth_config.yaml"
      },
      "alwaysAllow": ["elfmem_recall", "elfmem_remember"]
    }
  }
}
```

**Key Points:**
- Each MCP server entry has a unique name: `"elfmem"`, `"movemyth_elfmem"`
- Both use the same **command**: `uv run python -m elfmem.mcp`
- Each has a different **environment variable**: `ELFMEM_DB_PATH`
- Only these three MCP operations are auto-allowed; others require user approval

**Step 2.2:** Verify the configuration

```bash
cat ~/.claude/claude_code_config.json | jq '.mcpServers | keys'
```

Expected output:
```json
[
  "elfmem",
  "movemyth_elfmem"
]
```

### Phase 3: Create Project-Specific System Prompts

**Critical:** Claude Code uses the project's system prompt to determine which MCP server to use.

**Step 3.1:** Understand path encoding

Claude Code encodes project paths by replacing `/` with `-`. For example:

```
Physical path:     /Users/emson/Dropbox/devel/projects/ai/elf0_mem_sim
Encoded as:        -Users-emson-Dropbox-devel-projects-ai-elf0-mem-sim
Claude Code dir:   ~/.claude/projects/-Users-emson-Dropbox-devel-projects-ai-elf0-mem-sim

Physical path:     /Users/emson/Dropbox/devel/projects/move_myth_proj
Encoded as:        -Users-emson-Dropbox-devel-projects-move_myth_proj
Claude Code dir:   ~/.claude/projects/-Users-emson-Dropbox-devel-projects-move_myth_proj
```

**Step 3.2:** Create system prompts for existing projects

**For elf0_mem_sim (already exists):**

```bash
ls ~/.claude/projects/-Users-emson-Dropbox-devel-projects-ai-elf0-mem-sim/system_prompt.md
# Should exist; no action needed
```

**For move_myth_proj (new):**

```bash
mkdir -p ~/.claude/projects/-Users-emson-Dropbox-devel-projects-move_myth_proj
```

**Step 3.3:** Create the system prompt for MoveMyth

Create file: `~/.claude/projects/-Users-emson-Dropbox-devel-projects-move_myth_proj/system_prompt.md`

```markdown
# MoveMyth.com Project — System Prompt

## Using the movemyth_elfmem MCP Server

This project uses a **dedicated elfmem instance** isolated from other projects.

### MCP Endpoint: movemyth_elfmem

All elfmem operations (recall, remember, dream, curate, etc.) connect to:
- **Database:** ~/.elfmem/movemyth.db
- **Server:** movemyth_elfmem (registered in ~/.claude/claude_code_config.json)

### Available Operations

**recall(query, frame="attention", top_k=5)**
- Retrieve knowledge from MoveMyth's memory
- Frames: "self" (identity), "attention" (broad), "task" (goal),
  "world" (context), "short_term" (recent)
- Example: `elfmem_recall("forecasting accuracy patterns", frame="world")`

**remember(content, tags=[])**
- Store patterns, insights, and learnings
- Use semantic hierarchical tags
- Example: `elfmem_remember("Superforecaster pattern: base rate + model",
  tags=["forecasting/accuracy", "agent-pattern/recall"])`

**outcome(block_ids, signal, source="")**
- Provide feedback on retrieved blocks (signal: 0.0-1.0)
- 1.0 = block was very useful, 0.0 = block was harmful
- Example: `elfmem_outcome([block_123], signal=0.85, source="successful_forecast")`

**dream()**
- Trigger consolidation (embedding, contradiction detection, graph building)
- Call at natural pauses (end of session, after major decision)

**curate()**
- Trigger maintenance (decay archival, top-K reinforcement, edge pruning)
- Call weekly or when memory exceeds 100 blocks

**status()**
- View memory health: total blocks, session tokens, lifetime tokens
- Example: `elfmem_status()`

**guide(topic="")**
- Get runtime documentation
- Example: `elfmem_guide("recall")` → explains recall operation

### Workflow Example

```
Before forecasting decision:
  1. elfmem_recall("what has worked in similar forecasts", frame="world")
  2. elfmem_recall("what do we value in accuracy", frame="self")
  3. Set expectation: "I expect Brier Index of 75%"

After forecasting:
  4. Compare actual vs. expectation
  5. elfmem_outcome([block_ids], signal=<0-1>, source="forecast_attempt")
  6. If surprised: elfmem_remember("Pattern discovered...")
  7. At pause: elfmem_dream()
```

### Important Notes

- **No interference:** MoveMyth's memory is completely isolated from elf0_mem_sim
- **Autonomous growth:** Knowledge accumulates independently per project
- **Same system, different minds:** Both projects use elfmem, but have separate identities
```

### Phase 4: Template for Future Projects

When you add **Project C** (e.g., `/Users/emson/Dropbox/devel/projects/project_c`):

**Step 4.1:** Follow the same pattern

```bash
# 1. Create database
cp ~/.elfmem/default.db ~/.elfmem/project_c.db

# 2. Register in global config
# Edit ~/.claude/claude_code_config.json, add:
{
  "project_c_elfmem": {
    "command": "uv",
    "args": ["run", "python", "-m", "elfmem.mcp"],
    "env": {
      "ELFMEM_DB_PATH": "~/.elfmem/project_c.db",
      "ELFMEM_CONFIG_PATH": "~/.elfmem/project_c_config.yaml"
    },
    "alwaysAllow": ["elfmem_recall", "elfmem_remember"]
  }
}

# 3. Create project directory
mkdir -p ~/.claude/projects/-Users-emson-Dropbox-devel-projects-project_c

# 4. Create system prompt
# (Copy the template above, adjust project name and database references)
```

**Step 4.2:** Restart Claude Code

After updating the global config, close and reopen Claude Code to load the new MCP servers.

---

## Part 3: Verification and Testing

### Test 1: Verify Global Configuration

```bash
# Check that both servers are registered
cat ~/.claude/claude_code_config.json | jq '.mcpServers'

# Expected:
# {
#   "elfmem": { ... },
#   "movemyth_elfmem": { ... }
# }
```

### Test 2: Verify Project Directories

```bash
# Check that system prompts exist
ls ~/.claude/projects/*/system_prompt.md

# Expected:
# /Users/emson/.claude/projects/-Users-emson-Dropbox-devel-projects-ai-elf0-mem-sim/system_prompt.md
# /Users/emson/.claude/projects/-Users-emson-Dropbox-devel-projects-move_myth_proj/system_prompt.md
```

### Test 3: Test MCP in Claude Code

**For elf0_mem_sim project:**

1. Open the elf0_mem_sim project in Claude Code
2. In chat, invoke:
   ```
   elfmem_recall("test query", frame="self")
   ```
3. Should connect to `~/.elfmem/default.db`

**For MoveMyth project:**

1. Open the move_myth_proj project in Claude Code
2. In chat, invoke:
   ```
   elfmem_recall("test query", frame="self")
   ```
3. Should connect to `~/.elfmem/movemyth.db`

### Test 4: Verify Data Isolation

```bash
# From elf0_mem_sim project, remember something
elfmem_remember("Test: elf0_mem_sim only", tags=["test"])

# Switch to move_myth_proj project
# Try to recall it
elfmem_recall("elf0_mem_sim only", frame="attention")

# Should return: NO RESULTS (data is isolated)
```

---

## Part 4: How Claude Code Routes MCP Calls

Understanding the routing helps explain why this works:

```
┌─────────────────────────────────────────────────────────┐
│ Claude Code IDE                                          │
│                                                          │
│ User opens: /Users/emson/Dropbox/devel/projects/        │
│             move_myth_proj                              │
└─────────────────────────────────────────────────────────┘
                           ↓
┌─────────────────────────────────────────────────────────┐
│ Claude Code reads system prompt                          │
│ Location: ~/.claude/projects/                           │
│           -Users-emson-Dropbox-devel-projects-          │
│           move_myth_proj/system_prompt.md               │
│                                                          │
│ Content: "This project uses movemyth_elfmem MCP server" │
└─────────────────────────────────────────────────────────┘
                           ↓
┌─────────────────────────────────────────────────────────┐
│ Claude Code looks up "movemyth_elfmem" in global config │
│ File: ~/.claude/claude_code_config.json                 │
│                                                          │
│ Finds:                                                   │
│ {                                                        │
│   "command": "uv run python -m elfmem.mcp",             │
│   "env": { "ELFMEM_DB_PATH": "~/.elfmem/movemyth.db" }  │
│ }                                                        │
└─────────────────────────────────────────────────────────┘
                           ↓
┌─────────────────────────────────────────────────────────┐
│ Claude Code starts MCP server process                    │
│ Command: uv run python -m elfmem.mcp                    │
│ Environment: ELFMEM_DB_PATH=~/.elfmem/movemyth.db       │
│                                                          │
│ Server now listens on stdio for MCP calls               │
└─────────────────────────────────────────────────────────┘
                           ↓
┌─────────────────────────────────────────────────────────┐
│ Claude (LLM) calls: elfmem_recall("...", frame="...")   │
│                                                          │
│ Claude Code routes call to active MCP server            │
│ Server (running with movemyth.db) executes call         │
│ Returns: results from ~/. elfmem/movemyth.db            │
└─────────────────────────────────────────────────────────┘
```

---

## Part 5: Configuration Reference

### Complete Global Config Example

```json
{
  "mcpServers": {
    "elfmem": {
      "command": "uv",
      "args": ["run", "python", "-m", "elfmem.mcp"],
      "env": {
        "ELFMEM_DB_PATH": "~/.elfmem/default.db",
        "ELFMEM_CONFIG_PATH": "~/.elfmem/config.yaml"
      },
      "alwaysAllow": ["elfmem_recall", "elfmem_remember"]
    },
    "movemyth_elfmem": {
      "command": "uv",
      "args": ["run", "python", "-m", "elfmem.mcp"],
      "env": {
        "ELFMEM_DB_PATH": "~/.elfmem/movemyth.db",
        "ELFMEM_CONFIG_PATH": "~/.elfmem/movemyth_config.yaml"
      },
      "alwaysAllow": ["elfmem_recall", "elfmem_remember"]
    }
  }
}
```

### Directory Structure After Setup

```
~/.elfmem/
├── default.db                    ← elf0_mem_sim knowledge base
├── movemyth.db                   ← MoveMyth.com knowledge base
├── config.yaml                   ← elf0_mem_sim config
└── movemyth_config.yaml          ← MoveMyth.com config

~/.claude/
├── claude_code_config.json       ← Global MCP registration
└── projects/
    ├── -Users-emson-Dropbox-devel-projects-ai-elf0-mem-sim/
    │   └── system_prompt.md      ← Uses "elfmem" MCP server
    └── -Users-emson-Dropbox-devel-projects-move_myth_proj/
        └── system_prompt.md      ← Uses "movemyth_elfmem" MCP server
```

---

## Part 6: Troubleshooting

### Issue 1: "MCP Server Not Found"

**Error Message:**
```
Error: MCP server "movemyth_elfmem" not found
```

**Solution:**
1. Verify entry exists in `~/.claude/claude_code_config.json`
2. Check spelling matches exactly (case-sensitive)
3. Restart Claude Code after editing config
4. Reload the project

### Issue 2: "Wrong Database Being Used"

**Symptom:** Recalling from movemyth_elfmem returns elf0_mem_sim results

**Solution:**
1. Check system_prompt.md for correct MCP server name
2. Verify environment variable in config: `ELFMEM_DB_PATH=~/.elfmem/movemyth.db`
3. Verify project directory is correctly encoded in path

### Issue 3: "Database Locked"

**Error Message:**
```
sqlite3.OperationalError: database is locked
```

**Causes & Solutions:**
- Ensure both projects are not running simultaneously (Claude Code manages this)
- SQLite should handle concurrent readers, but only one writer
- If persistent, restart Claude Code

### Issue 4: "System Prompt Not Being Used"

**Solution:**
1. Verify system_prompt.md exists in correct directory
2. Check directory name encoding:
   ```bash
   # For /Users/emson/Dropbox/devel/projects/move_myth_proj
   # Directory should be: -Users-emson-Dropbox-devel-projects-move_myth_proj

   # Count the hyphens:
   # -Users-emson-Dropbox-devel-projects-move_myth_proj
   # 1     2     3       4     5        6               7
   ```
3. Reload project in Claude Code

### Issue 5: "Config Changes Not Taking Effect"

**Solution:**
1. Close Claude Code completely
2. Wait 2 seconds
3. Reopen Claude Code
4. Reload the project

---

## Part 7: Best Practices

### DO ✓

- **Use descriptive MCP names:** `project_name_elfmem` makes it clear which project it serves
- **Keep databases separate:** Never share databases between projects
- **Version control system prompts:** Keep system_prompt.md in project repos
- **Document integrations:** In project README, explain which MCP server it uses
- **Test after setup:** Verify data isolation before starting development

### DON'T ✗

- **Don't manually edit database paths** in environment variables during runtime
- **Don't share one database** between projects (will cause confusion and data mixing)
- **Don't commit Claude Code config** to version control (it's user-specific)
- **Don't create MCP entries** without corresponding databases
- **Don't forget to restart** Claude Code after config changes

---

## Part 8: Adding a Third Project (Template)

When you're ready to add **Project C**, use this template:

### 8.1 Create Database

```bash
cp ~/.elfmem/default.db ~/.elfmem/project_c.db
```

### 8.2 Update Global Config

Edit `~/.claude/claude_code_config.json`:

```json
"project_c_elfmem": {
  "command": "uv",
  "args": ["run", "python", "-m", "elfmem.mcp"],
  "env": {
    "ELFMEM_DB_PATH": "~/.elfmem/project_c.db",
    "ELFMEM_CONFIG_PATH": "~/.elfmem/project_c_config.yaml"
  },
  "alwaysAllow": ["elfmem_recall", "elfmem_remember"]
}
```

### 8.3 Create Project Directory

```bash
mkdir -p ~/.claude/projects/-Users-emson-Dropbox-devel-projects-project_c
```

### 8.4 Create System Prompt

Create `~/.claude/projects/-Users-emson-Dropbox-devel-projects-project_c/system_prompt.md`

```markdown
# Project C — System Prompt

This project uses the **project_c_elfmem** MCP server.

## Database
- **Path:** ~/.elfmem/project_c.db
- **Server:** project_c_elfmem

## Available Operations
[Same as MoveMyth template above, with project name changed]
```

### 8.5 Restart and Test

```bash
# Restart Claude Code
# Open Project C
# Test: elfmem_recall("test", frame="self")
```

---

## Summary

| What | Where | Purpose |
|-----|-------|---------|
| **Global Config** | `~/.claude/claude_code_config.json` | Register all MCP servers |
| **Project Prompt** | `~/.claude/projects/<PROJECT_ID>/system_prompt.md` | Tell Claude which MCP server to use |
| **Database** | `~/.elfmem/<project>.db` | Store project's knowledge |
| **MCP Entry Name** | `<project>_elfmem` | Links project prompt → global config → database |

**Key Realization:**
- Claude Code manages server startup/shutdown automatically
- You never manually start or stop MCP servers
- One command (`uv run python -m elfmem.mcp`) serves all projects
- Each project gets its own database via environment variables
- Routing happens through system prompts and global config

You can now add unlimited projects following this pattern.
