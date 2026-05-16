# Multiple MCP Servers — Quick Reference Card

## Current Setup ✓

### Registered MCP Servers
```json
{
  "elfmem": "~/.elfmem/default.db" → elf0_mem_sim
  "movemyth_elfmem": "~/.elfmem/movemyth.db" → move_myth_proj
}
```

### Project Mappings
| Project | Path | MCP Server | Database | System Prompt |
|---------|------|-----------|----------|---------------|
| elf0_mem_sim | `/Users/emson/Dropbox/devel/projects/ai/elf0_mem_sim` | `elfmem` | `default.db` | ✓ Exists |
| MoveMyth | `/Users/emson/Dropbox/devel/projects/move_myth_proj` | `movemyth_elfmem` | `movemyth.db` | ✓ Exists |

---

## Adding a New Project (Copy-Paste Template)

### Step 1: Create Database
```bash
cp ~/.elfmem/default.db ~/.elfmem/<PROJECT_NAME>.db
```

### Step 2: Update Global Config
Edit: `~/.claude/claude_code_config.json`

```json
"<PROJECT_NAME>_elfmem": {
  "command": "uv",
  "args": ["run", "python", "-m", "elfmem.mcp"],
  "env": {
    "ELFMEM_DB_PATH": "~/.elfmem/<PROJECT_NAME>.db",
    "ELFMEM_CONFIG_PATH": "~/.elfmem/<PROJECT_NAME>_config.yaml"
  },
  "alwaysAllow": ["elfmem_recall", "elfmem_remember", "elfmem_outcome", "elfmem_dream", "elfmem_curate", "elfmem_status", "elfmem_guide"]
}
```

### Step 3: Create Project Directory
```bash
# Replace /ACTUAL/PROJECT/PATH with real path
# Encode by replacing / with -

mkdir -p ~/.claude/projects/-Users-emson-Dropbox-devel-projects-<PROJECT_NAME>
```

### Step 4: Create System Prompt
File: `~/.claude/projects/-Users-emson-Dropbox-devel-projects-<PROJECT_NAME>/system_prompt.md`

```markdown
# <PROJECT_NAME> — System Prompt

This project uses the **<PROJECT_NAME>_elfmem** MCP server.

## Database
- Path: ~/.elfmem/<PROJECT_NAME>.db
- Server: <PROJECT_NAME>_elfmem

## Available Operations

**elfmem_recall(query, frame="attention", top_k=5)**
- Retrieve knowledge (frames: self, attention, task, simulate)

**elfmem_remember(content, tags=[])**
- Store knowledge with semantic tags

**elfmem_outcome(block_ids, signal, source="")**
- Provide feedback (0.0-1.0) on retrieved blocks

**elfmem_dream(rescore=False, rescore_max=None, no_llm=False, skip_contradictions=False)**
- Trigger consolidation. Default = process inbox normally.
- `rescore=True`: deep-sleep mode — re-evaluate aged active blocks vs current SELF
- `no_llm=True`: bypass LLM scoring (embed + promote only — for outages / bulk loads)
- `skip_contradictions=True`: skip the O(n²) contradiction loop (trusted ingestion)

**elfmem_curate()**
- Trigger maintenance

**elfmem_status()**
- View memory health

**elfmem_guide(topic="")**
- Get documentation

**Theory of Mind (model other agents / users / stakeholders):**

**elfmem_mind_create(subject, goals=[], beliefs=[], fears=[], motivations=[])**
- Create a structured mental model. Returns mind block ID.

**elfmem_mind_predict(mind_block_id, prediction, verify_at, reasoning=None)**
- Attach a falsifiable prediction with a verify_at date.

**elfmem_mind_list()** / **elfmem_mind_show(mind_block_id)**
- List all minds with calibration stats / inspect one with linked predictions.

**elfmem_mind_outcome(decision_block_id, hit, reason)**
- Close a prediction; Bayesian-calibrates the mind model.
```

### Step 5: Restart Claude Code
- Close Claude Code
- Wait 2 seconds
- Reopen Claude Code

### Step 6: Test
```
Open the new project
Invoke: elfmem_recall("test", frame="self")
Should connect to ~/.elfmem/<PROJECT_NAME>.db
```

---

## Key Facts

### Do You Need Multiple FastMCP Servers?
**No.** One implementation serves all projects via different databases.

### How Does Routing Work?
```
Project A opens
  → System prompt says: use "elfmem"
  → Claude Code starts MCP server with ELFMEM_DB_PATH=default.db
  → All calls use default.db

User switches to Project B
  → System prompt says: use "movemyth_elfmem"
  → Claude Code stops previous server
  → Claude Code starts MCP server with ELFMEM_DB_PATH=movemyth.db
  → All calls use movemyth.db
```

### What If I Forget to Restart?
Changes to `~/.claude/claude_code_config.json` won't take effect until Claude Code restarts.

### Data Isolation?
Yes. Each project has its own database. Zero cross-contamination.

### Can Projects Run Simultaneously?
In Claude Code IDE: No (only one project active at a time).
In separate Claude Code windows: Yes (each manages its own MCP server).

---

## Troubleshooting Checklist

- [ ] Database file exists: `ls ~/.elfmem/<PROJECT_NAME>.db`
- [ ] MCP entry in config: `cat ~/.claude/claude_code_config.json | jq '.mcpServers.<PROJECT_NAME>_elfmem'`
- [ ] System prompt exists: `ls ~/.claude/projects/-Users-emson-Dropbox-devel-projects-<PROJECT_NAME>/system_prompt.md`
- [ ] Project path encoded correctly (/ → -)
- [ ] Claude Code restarted after config changes
- [ ] Project reloaded in Claude Code

---

## Full Documentation

See: `/docs/multiple_mcp_steps.md` for comprehensive guide with examples, architecture explanation, and detailed troubleshooting.
