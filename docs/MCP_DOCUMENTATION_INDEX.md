# MCP Documentation Index

## Quick Navigation

**New to multiple MCPs?**
→ Start with `SETUP_COMPLETE.md` (5-min overview)

**Need copy-paste template?**
→ Go to `MULTIPLE_MCP_QUICK_REFERENCE.md`

**Want deep understanding?**
→ Read `multiple_mcp_steps.md` (Parts 1-4)

**Troubleshooting?**
→ Jump to `multiple_mcp_steps.md` Part 6 or reference card

---

## Documentation Files

### 1. SETUP_COMPLETE.md
**What:** Setup completion and verification guide
**When to read:** After initial setup, before testing
**Length:** 5 minutes
**Contains:**
- What's been configured
- Immediate next steps (4 verification tests)
- Key insight: FastMCP servers answer
- Template for adding third project
- File locations reference
- Success criteria

**Best for:** Getting started immediately

---

### 2. multiple_mcp_steps.md
**What:** Comprehensive technical guide (2500+ lines)
**When to read:** For complete understanding
**Length:** 20-30 minutes (can be read in parts)
**Contains:**
- **Part 1:** Understanding MCP Architecture
  - What is an MCP server
  - MCP Server vs. Database distinction
  - Why you don't need multiple FastMCP servers (full explanation)

- **Part 2:** Step-by-step setup (8 phases)
  - For elf0_mem_sim
  - For move_myth_proj
  - For future projects

- **Part 3:** Verification and testing (4 tests)
  - Test global config
  - Test project directories
  - Test MCP in Claude Code
  - Test data isolation

- **Part 4:** How Claude Code routes MCP calls
  - Visual routing diagram
  - Step-by-step flow

- **Part 5:** Configuration reference
  - Complete example JSON
  - Directory structure

- **Part 6:** Troubleshooting (5 issues)
  - MCP server not found
  - Wrong database being used
  - Database locked
  - System prompt not being used
  - Config changes not taking effect

- **Part 7:** Best practices
  - DO (5 practices)
  - DON'T (5 anti-patterns)

- **Part 8:** Adding a third project
  - Complete template
  - Copy-paste instructions

**Best for:** Complete understanding

---

### 3. MULTIPLE_MCP_QUICK_REFERENCE.md
**What:** One-page quick reference card
**When to read:** When you need quick answers
**Length:** 3-5 minutes
**Contains:**
- Current setup summary table
- Step-by-step template for new projects (copy-paste ready)
- Step 1-6 checklist
- Key facts (7 important points)
- FastMCP server answer
- Troubleshooting checklist
- Link to full documentation

**Best for:** Adding future projects quickly

---

## Concept Map: How to Use These Docs

```
START HERE
     ↓
Is this your first time?
     ├─ YES → Read SETUP_COMPLETE.md
     │        ↓
     │        Do immediate next steps
     │        ↓
     │        Tests pass?
     │        ├─ YES → You're done! ✓
     │        └─ NO → Go to multiple_mcp_steps.md Part 6
     │
     └─ NO (Adding new project) → MULTIPLE_MCP_QUICK_REFERENCE.md
                                  ↓
                                  Follow copy-paste template
                                  ↓
                                  Restart Claude Code
                                  ↓
                                  Test
                                  ↓
                                  Done ✓

Having trouble?
     ↓
Go to multiple_mcp_steps.md Part 6
or
MULTIPLE_MCP_QUICK_REFERENCE.md Troubleshooting Checklist
```

---

## Common Scenarios

### "I just set this up, what do I do first?"
1. Read: `SETUP_COMPLETE.md` (5 min)
2. Follow: "Immediate Next Steps" section
3. Restart Claude Code
4. Run the 4 tests

### "I want to understand how this works"
1. Read: `MULTIPLE_MCP_QUICK_REFERENCE.md` (key facts section)
2. Read: `multiple_mcp_steps.md` Parts 1-4 (15 min)
3. You'll understand the complete architecture

### "How do I add a third project?"
1. Open: `MULTIPLE_MCP_QUICK_REFERENCE.md`
2. Go to: "Adding a New Project" section
3. Copy-paste the template
4. Follow steps 1-6 (2 min)
5. Restart Claude Code
6. Test

### "Something's not working"
1. Open: `multiple_mcp_steps.md` Part 6
2. Find your issue
3. Follow the solution
4. Verify with the checklist

### "Which MCP server am I using?"
- Answer: Check your project's system_prompt.md
- Location: `~/.claude/projects/<PROJECT_ID>/system_prompt.md`
- Look for: "This project uses the X_elfmem MCP server"

### "Where is my database?"
- Answer: Configured in global config
- Location: `~/.claude/claude_code_config.json`
- Look for: `"ELFMEM_DB_PATH"` in your MCP server entry

---

## The Three Key Files

### File 1: Global Configuration
**Location:** `~/.claude/claude_code_config.json`
**Purpose:** Register all MCP servers globally
**Contents:** 2 MCP server entries (elfmem + movemyth_elfmem)
**When edited:** When adding a new project (add new entry)

### File 2: Project System Prompt
**Location:** `~/.claude/projects/<PROJECT_ID>/system_prompt.md`
**Purpose:** Tell Claude which MCP server to use
**Contents:** MCP server name + available operations
**When edited:** When setting up a new project

### File 3: Database File
**Location:** `~/.elfmem/<project_name>.db`
**Purpose:** Store project's knowledge base
**Contents:** Isolated blocks for that project
**When created:** When setting up a new project

---

## FAQ

**Q: Do I need multiple FastMCP servers?**
A: No. One implementation serves all projects. See `SETUP_COMPLETE.md` for explanation.

**Q: Can both projects run simultaneously?**
A: In Claude Code: No (one project active at a time). In separate windows: Yes.

**Q: What happens when I switch projects?**
A: Claude Code automatically stops the previous MCP server and starts the new one.

**Q: Can I manually control the MCP servers?**
A: No—Claude Code manages them automatically. Never try to manually start/stop.

**Q: How isolated is the data?**
A: Completely isolated. Different databases, different MCP servers, zero cross-contamination.

**Q: What if the config changes don't take effect?**
A: Restart Claude Code. Changes require a full restart to load.

**Q: How long does setup take?**
A: Initial: 10-15 minutes. New projects: 2 minutes each.

**Q: Can I use this for 10+ projects?**
A: Yes. Same pattern for each. Infinitely scalable.

---

## Document Progression

### Level 1: Quick Start (5 min)
→ `SETUP_COMPLETE.md`

### Level 2: Reference (3-5 min)
→ `MULTIPLE_MCP_QUICK_REFERENCE.md`

### Level 3: Complete Understanding (20-30 min)
→ `multiple_mcp_steps.md`

### Level 4: Implementation (varies)
→ Choose based on your scenario above

---

## Key Insights

**Insight 1: No Multiple Servers Needed**
One MCP implementation (elfmem.mcp) serves all projects. Environment variables route to the right database. See `SETUP_COMPLETE.md`.

**Insight 2: System Prompts Control Routing**
Claude doesn't need to know which MCP server to use—the system prompt tells it. Different projects use different prompts.

**Insight 3: Complete Isolation**
Each project has its own database. Knowledge doesn't leak between projects unless you explicitly move data.

**Insight 4: Automatic Lifecycle Management**
Claude Code starts/stops servers automatically. You never manually manage processes.

**Insight 5: Infinitely Scalable Pattern**
Adding projects follows the same pattern every time. Template-based, 2-minute setup.

---

## File Size Reference

| Document | Size | Read Time |
|----------|------|-----------|
| SETUP_COMPLETE.md | 4-5 KB | 5 min |
| MULTIPLE_MCP_QUICK_REFERENCE.md | 3-4 KB | 3-5 min |
| multiple_mcp_steps.md | 25-30 KB | 20-30 min |
| **TOTAL** | ~35 KB | **~30 min** |

You don't need to read everything—pick what you need.

---

## Related Documentation

For context on elfmem integration with Claude Code:
→ `CLAUDE_CODE_INTEGRATION.md`

For agent discipline and self-calibration:
→ `agent_usage_patterns_guide.md`

For cognitive loop operations:
→ `cognitive_loop_operations_guide.md`

---

## Document Maintenance

Last updated: 2026-03-14

These documents cover:
- ✓ elf0_mem_sim + move_myth_proj setup
- ✓ Adding unlimited future projects
- ✓ Troubleshooting all common issues
- ✓ Best practices and patterns
- ✓ Complete architecture explanation

If you find gaps or unclear sections, consider:
1. Checking the specific guide for your use case
2. Reading the troubleshooting section
3. Following the step-by-step templates
4. Testing with the verification procedures

---

## Contact/Support

For issues with the documentation:
- Check the troubleshooting guides first
- See if your scenario is covered in the FAQs
- Read through `multiple_mcp_steps.md` Part 6

For issues with the actual MCP setup:
- Verify all 3 key files exist and are correct
- Follow the verification checklist in `SETUP_COMPLETE.md`
- Check the troubleshooting section

---
