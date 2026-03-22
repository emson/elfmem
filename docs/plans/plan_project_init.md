# Plan: elfmem init — Project-Aware Setup

## Problem

Running `elfmem init` and `elfmem doctor` today is globally-scoped. Both default to
`~/.elfmem/agent.db` and `~/.elfmem/config.yaml`. This means:

- Multiple projects share one database (memory bleeds across unrelated codebases)
- No easy way to tell which project a database belongs to
- `elfmem serve` requires `--db` on every invocation
- Coming back to a project after months: no record of where config/db are
- Agent docs (CLAUDE.md / AGENTS.md) have no elfmem section → Claude doesn't know how to use it

## Solution

**Project-local config** in `.elfmem/config.yaml` (committed to repo, no secrets) +
**database outside repo** in `~/.elfmem/databases/{project-name}.db` +
**auto-discovery** so all commands just work inside a project directory.

---

## Design

### Config discovery chain

Every elfmem command resolves its config through this ordered chain:

```
1. --config PATH flag     (explicit always wins)
2. ELFMEM_CONFIG env var
3. .elfmem/config.yaml    (walk up from cwd to project root)
4. ~/.elfmem/config.yaml  (global fallback)
```

### DB discovery chain

```
1. --db PATH flag          (explicit always wins)
2. ELFMEM_DB env var
3. project.db in config    (read from discovered config)
4. ~/.elfmem/agent.db      (global fallback)
```

With this chain, `cd my-project && elfmem status` just works — no flags needed.

### Project root detection

Walk up from cwd, stop at first directory containing any of:
- `.git/`
- `pyproject.toml`
- `package.json`
- `Cargo.toml`
- `go.mod`
- `.elfmem/`   (already initialised — stop walking)

### Project name inference

Priority order:
1. `pyproject.toml` → `[project].name`
2. `package.json` → `.name`
3. Directory name (fallback)

### Project config file (`.elfmem/config.yaml`)

Contains a `project:` metadata section plus the standard config sections.
The `project:` section is never secrets — safe to commit.

```yaml
# .elfmem/config.yaml  ← committed to repo (no secrets here)
project:
  name: "my-project"
  db: "~/.elfmem/databases/my-project.db"   ← database lives outside repo
  identity: "Software engineering assistant for the my-project codebase"
  created: "2026-03-22"

llm:
  model: "claude-sonnet-4-6"
  ...
```

The `db:` field in the project section is the key that lets `--db` be omitted.

### Database location

Database always lives in `~/.elfmem/databases/{project-name}.db` — outside the repo,
never committed. `.elfmem/config.yaml` points to it via the `project.db` field.

This means:
- Config is version-controlled and portable
- Database is personal and stays on the machine
- No `.gitignore` changes needed for the database

### Agent doc integration

Auto-detect the project's agent doc file:

1. `CLAUDE.md` — Claude Code convention
2. `AGENTS.md` — OpenAI Codex / Copilot convention
3. `claude.md` / `agents.md` — lowercase variants
4. None found → write `CLAUDE.md` (create it)
5. `--docs-file PATH` — explicit path override
6. `--no-docs` — skip entirely

The elfmem section uses HTML comment delimiters so it can be updated without duplication:

```markdown
<!-- elfmem:start -->
## elfmem — Project Memory

- **Project:** my-project
- **Database:** `~/.elfmem/databases/my-project.db`
- **Config:** `.elfmem/config.yaml`
- **Identity:** Software engineering assistant for the my-project codebase

Commands:
- `elfmem doctor` — verify setup
- `elfmem status` — memory health
- `elfmem guide` — all operations

MCP server config for `.claude.json`:
```json
{
  "mcpServers": {
    "elfmem": {
      "command": "elfmem",
      "args": ["serve", "--config", "/absolute/path/.elfmem/config.yaml"]
    }
  }
}
```
<!-- elfmem:end -->
```

Re-running `elfmem init` replaces this section (idempotent). It never duplicates.

### MCP config snippet

`elfmem init` always prints the MCP snippet at the end, with absolute paths.
When the project config has `project.db`, the snippet uses only `--config`
(no `--db` needed — the serve command reads db from config).

### `elfmem serve` without `--db`

`--db` becomes optional. If omitted, falls through the discovery chain.
When invoked from project dir with `.elfmem/config.yaml`, it auto-discovers the db.

```bash
# Before (required --db everywhere)
elfmem serve --db ~/.elfmem/databases/my-project.db --config .elfmem/config.yaml

# After (just works in project directory)
elfmem serve
```

---

## New commands / flags

### `elfmem init` changes

New flags:
- `--global` — force global config in `~/.elfmem/` (ignore project detection)
- `--docs-file PATH` — explicit agent doc file path
- `--no-docs` — skip writing agent doc section

Behavior when run inside a project directory (detected via markers):
1. Create `.elfmem/` in project root
2. Write `.elfmem/config.yaml` with `project:` section
3. Database at `~/.elfmem/databases/{project-name}.db`
4. Seed constitutional blocks (unless `--no-seed`)
5. Store SELF block (if `--self` given)
6. Write elfmem section to CLAUDE.md / AGENTS.md (unless `--no-docs`)
7. Print MCP snippet

Behavior when run outside a project (or with `--global`):
- Same as current behavior: `~/.elfmem/config.yaml` + `~/.elfmem/agent.db`

### `elfmem doctor` changes

New checks:
- **Config source** — which step in the discovery chain resolved the config
- **Project** — project name and identity if project section exists
- **Agent doc** — CLAUDE.md or AGENTS.md has elfmem section
- **MCP config** — .claude.json or .claude/claude-code.yaml mentions elfmem

Enhanced output:

```
✓  Config:     .elfmem/config.yaml (project-local)
✓  Project:    my-project
✓  Database:   ~/.elfmem/databases/my-project.db (45 blocks)
✓  SELF:       8 blocks found
✓  API keys:   ANTHROPIC_API_KEY set
✓  Agent doc:  CLAUDE.md has elfmem section
✗  MCP:        .claude.json not found
   Suggestion: Add elfmem to .claude.json (see elfmem init output)
```

### `elfmem serve` changes

`--db` becomes `Optional[str]`. Falls back to `project.db` in discovered config.

---

## New module: `src/elfmem/project.py`

Pure functions, no globals, no I/O side effects except the explicit write functions.

| Function | Purpose |
|----------|---------|
| `find_project_root(start)` | Walk up cwd to find project root |
| `project_name(root)` | Infer name from pyproject.toml / package.json / dirname |
| `find_local_config(start)` | Find `.elfmem/config.yaml` walking up |
| `detect_agent_doc(root)` | Find CLAUDE.md / AGENTS.md |
| `detect_mcp_config(root)` | Find .claude.json / claude-code.yaml |
| `resolve_config(explicit, cwd)` | Full config discovery chain → (path, source) |
| `resolve_db(explicit, config_path, cwd)` | Full DB discovery chain → (path, source) |
| `write_agent_section(doc_path, ...)` | Write/update elfmem section in agent doc |
| `has_agent_section(doc_path)` | Check if doc has elfmem section |
| `has_mcp_config(mcp_path)` | Check if MCP config mentions elfmem |
| `mcp_json_snippet(config_path)` | Generate MCP JSON block |

## Config changes: `src/elfmem/config.py`

Add `ProjectConfig` model:

```python
class ProjectConfig(BaseModel):
    name: str = ""
    db: str = ""         # path to database (expanded at use time)
    identity: str = ""   # identity description for display
    created: str = ""    # ISO date of init
```

Add to `ElfmemConfig`:
```python
project: ProjectConfig | None = None
```

Update `render_default_config(project=None)` to include `project:` section
when a `ProjectConfig` is provided.

---

## File layout after `elfmem init` in a project

```
my-project/
├── .elfmem/
│   └── config.yaml        ← committed (no secrets)
├── CLAUDE.md              ← updated with elfmem section
├── src/
└── .gitignore             ← database is outside repo, no entry needed

~/.elfmem/
└── databases/
    └── my-project.db      ← personal, never committed
```

---

## Implementation scope

- [x] `src/elfmem/project.py` — new module
- [x] `src/elfmem/config.py` — add `ProjectConfig`, update `render_default_config()`
- [x] `src/elfmem/cli.py` — enhance `init`, `doctor`, `serve`; update resolution chain
