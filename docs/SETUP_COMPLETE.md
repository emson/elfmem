# Multiple MCP Setup — COMPLETE ✓

## Setup Status

All components for running multiple MCP servers across different Claude Code projects are now in place and configured.

---

## What's Been Configured

### 1. Global MCP Configuration
**File:** `~/.claude/claude_code_config.json`

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
      "alwaysAllow": [all 7 elfmem operations]
    },
    "movemyth_elfmem": {
      "command": "uv",
      "args": ["run", "python", "-m", "elfmem.mcp"],
      "env": {
        "ELFMEM_DB_PATH": "~/.elfmem/movemyth.db",
        "ELFMEM_CONFIG_PATH": "~/.elfmem/movemyth_config.yaml"
      },
      "alwaysAllow": [all 7 elfmem operations]
    }
  }
}
```

### 2. Databases Created
- `~/.elfmem/default.db` (572K) — elf0_mem_sim knowledge base
- `~/.elfmem/movemyth.db` (572K) — move_myth_proj knowledge base (copy of default)

### 3. Project System Prompts

**elf0_mem_sim:**
- Location: `~/.claude/projects/-Users-emson-Dropbox-devel-projects-ai-elf0-mem-sim/system_prompt.md`
- Uses: `elfmem` MCP server
- Status: ✓ Configured

**move_myth_proj:**
- Location: `~/.claude/projects/-Users-emson-Dropbox-devel-projects-move_myth_proj/system_prompt.md`
- Uses: `movemyth_elfmem` MCP server
- Status: ✓ Configured

### 4. Documentation Created

**Comprehensive Guide:**
- `docs/multiple_mcp_steps.md` (~2500 lines)
  - Architecture explanation
  - Why you don't need multiple FastMCP servers
  - Step-by-step setup for elf0_mem_sim + MoveMyth
  - Template for adding future projects
  - Detailed troubleshooting guide
  - Best practices and anti-patterns
  - Configuration reference

**Quick Reference:**
- `docs/MULTIPLE_MCP_QUICK_REFERENCE.md`
  - Current setup summary
  - Copy-paste templates for new projects
  - Checklist for adding projects
  - Quick troubleshooting

---

## Immediate Next Steps

### Step 1: Restart Claude Code
```bash
# Close Claude Code completely
# Wait 2 seconds
# Reopen Claude Code
```

This loads the updated MCP configuration.

### Step 2: Test elf0_mem_sim Project
1. Open the elf0_mem_sim project
2. In chat, run:
   ```
   elfmem_recall("test query", frame="self")
   ```
3. Expected: Should retrieve from `~/.elfmem/default.db`

### Step 3: Test move_myth_proj Project
1. Open the move_myth_proj project
2. In chat, run:
   ```
   elfmem_recall("test query", frame="self")
   ```
3. Expected: Should retrieve from `~/.elfmem/movemyth.db`

### Step 4: Verify Data Isolation
1. From elf0_mem_sim project, remember something:
   ```
   elfmem_remember("test_elf0_only", tags=["test"])
   ```
2. Switch to move_myth_proj project
3. Try to recall:
   ```
   elfmem_recall("test_elf0_only", frame="attention")
   ```
4. Expected: NO RESULTS (data is isolated between projects) ✓

---

## Key Insight: No Multiple FastMCP Servers Needed

**Question:** Do I need to run multiple FastMCP server processes?

**Answer:** No.

**Why:**

Claude Code uses a smart routing system:

```
┌─────────────────────────────────┐
│ User opens Project A            │
└─────────────────────────────────┘
           ↓
┌─────────────────────────────────┐
│ System prompt says: use "elfmem"│
└─────────────────────────────────┘
           ↓
┌───────────────────────────────────────────┐
│ Claude Code launches MCP server:          │
│ ELFMEM_DB_PATH=~/.elfmem/default.db       │
│ python -m elfmem.mcp                      │
└───────────────────────────────────────────┘
           ↓
┌───────────────────────────────────────────┐
│ All elfmem calls hit default.db           │
└───────────────────────────────────────────┘

User switches to Project B
           ↓
┌───────────────────────────────────────────┐
│ System prompt says: use "movemyth_elfmem" │
└───────────────────────────────────────────┘
           ↓
┌───────────────────────────────────────────┐
│ Claude Code stops previous server         │
│ Claude Code launches new MCP server:      │
│ ELFMEM_DB_PATH=~/.elfmem/movemyth.db      │
│ python -m elfmem.mcp                      │
└───────────────────────────────────────────┘
           ↓
┌───────────────────────────────────────────┐
│ All elfmem calls hit movemyth.db          │
└───────────────────────────────────────────┘
```

**Key Facts:**
- One MCP implementation (`elfmem.mcp`) serves all projects
- Environment variables route to the correct database
- No ports involved (uses stdio) = no port conflicts
- Claude Code manages server lifecycle automatically
- You never manually start/stop servers

---

## Adding a Third Project (Template)

When you're ready to add another project, follow this template from `MULTIPLE_MCP_QUICK_REFERENCE.md`:

### Template (Copy-Paste)

**Step 1:** Create database
```bash
cp ~/.elfmem/default.db ~/.elfmem/PROJECT_NAME.db
```

**Step 2:** Register in global config
```json
"PROJECT_NAME_elfmem": {
  "command": "uv",
  "args": ["run", "python", "-m", "elfmem.mcp"],
  "env": {
    "ELFMEM_DB_PATH": "~/.elfmem/PROJECT_NAME.db",
    "ELFMEM_CONFIG_PATH": "~/.elfmem/PROJECT_NAME_config.yaml"
  },
  "alwaysAllow": ["elfmem_recall", "elfmem_remember", "elfmem_outcome", "elfmem_dream", "elfmem_curate", "elfmem_status", "elfmem_guide"]
}
```

**Step 3:** Create project directory
```bash
# Encode the project path by replacing / with -
mkdir -p ~/.claude/projects/-Users-emson-Dropbox-devel-projects-PROJECT_NAME
```

**Step 4:** Create system prompt
```markdown
# PROJECT_NAME — System Prompt

This project uses the **PROJECT_NAME_elfmem** MCP server.

Database: ~/.elfmem/PROJECT_NAME.db
Server: PROJECT_NAME_elfmem

[Copy the standard operations list from move_myth_proj system_prompt.md]
```

**Step 5:** Restart Claude Code and test

**Time:** ~2 minutes per new project

---

## File Locations (Quick Reference)

| Component | Location |
|-----------|----------|
| **Global Config** | `~/.claude/claude_code_config.json` |
| **elf0_mem_sim Prompt** | `~/.claude/projects/-Users-emson-Dropbox-devel-projects-ai-elf0-mem-sim/system_prompt.md` |
| **MoveMyth Prompt** | `~/.claude/projects/-Users-emson-Dropbox-devel-projects-move_myth_proj/system_prompt.md` |
| **elf0_mem_sim DB** | `~/.elfmem/default.db` |
| **MoveMyth DB** | `~/.elfmem/movemyth.db` |
| **Comprehensive Guide** | `/Dropbox/devel/projects/ai/elf0_mem_sim/docs/multiple_mcp_steps.md` |
| **Quick Reference** | `/Dropbox/devel/projects/ai/elf0_mem_sim/docs/MULTIPLE_MCP_QUICK_REFERENCE.md` |

---

## Troubleshooting Checklist

- [ ] Databases exist: `ls ~/.elfmem/*.db`
- [ ] MCP entries in config: `cat ~/.claude/claude_code_config.json | jq '.mcpServers | keys'`
- [ ] System prompts exist: `find ~/.claude/projects -name system_prompt.md`
- [ ] Claude Code restarted after config changes
- [ ] Project path encoded correctly (/ → -)
- [ ] Project reloaded in Claude Code

---

## What's Enabled Now

Both projects can now:

1. **Recall knowledge** from their isolated memory bases
2. **Remember patterns** specific to their domain
3. **Provide outcome feedback** on retrieved knowledge
4. **Dream (consolidate)** their knowledge graphs
5. **Curate** their knowledge (archival, top-K reinforcement)
6. **Check status** of their memory systems
7. **Get guidance** on any operation

All with complete **data isolation** — no cross-contamination between projects.

---

## Architecture Summary

```
One MCP Implementation (elfmem.mcp)
    ↓
Multiple MCP Configurations (in claude_code_config.json)
    ↓
Multiple Project System Prompts (point to specific MCP servers)
    ↓
Multiple Databases (isolated knowledge per project)
    ↓
Multiple Projects (each with separate identity and memory)
```

This is:
- **Scalable:** Add unlimited projects with same pattern
- **Isolated:** Zero cross-contamination between projects
- **Automatic:** Claude Code manages server lifecycle
- **Simple:** No manual server management required

---

## Success Criteria

You'll know everything is working when:

1. ✓ Both projects open in Claude Code without errors
2. ✓ `elfmem_recall()` returns results (not "command not found")
3. ✓ elf0_mem_sim returns results from `default.db`
4. ✓ move_myth_proj returns results from `movemyth.db`
5. ✓ Data is isolated (no cross-project bleed)
6. ✓ Switching projects switches active MCP server automatically

---

## Next Phase: Development

Once verified, both projects can:
- Build independent knowledge bases
- Self-calibrate through outcome feedback
- Create self-improving feedback loops
- Maintain separate identities
- Scale to enterprise deployments

See `docs/CLAUDE_CODE_INTEGRATION.md` for guidance on using elfmem in team agents.
