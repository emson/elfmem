# Plan: elfmem Init, Doctor & Setup

## Overview

Introduce a first-run setup experience across the CLI, MCP, and library layers:

- **`elfmem init`** — create `~/.elfmem/`, generate `config.yaml` from code defaults, optionally seed SELF
- **`elfmem doctor`** — diagnose setup gaps: directory, config, DB, SELF blocks, API keys
- **`elfmem_setup`** MCP tool — agent-native SELF seeding, callable by Claude Code mid-conversation
- **Bug fix** — `~` path expansion missing in `config.py:from_yaml()`, causing `FileNotFoundError`

No new dependencies. No new files (except tests). Minimal changes to existing modules.

---

## Problem Statement

**Current onboarding flow (broken):**
```
1. User installs elfmem
2. Creates ~/.elfmem/ manually
3. Creates config.yaml manually (copy from docs)
4. Runs: elfmem serve --db ~/.elfmem/agent.db --config ~/.elfmem/config.yaml
   → If ~ not expanded: FileNotFoundError ← BUG
5. SELF frame is empty; no discovery path exists
6. status() says "Memory is empty. Call learn()." — no SELF hint
```

**Target onboarding flow (fixed):**
```
1. User installs elfmem
2. Runs: elfmem init --self "I am Claude Code, an AI engineering assistant"
   → Creates ~/.elfmem/
   → Generates config.yaml from ElfmemConfig() code defaults
   → Seeds SELF block via remember(content, tags=["self"])
3. Runs: elfmem serve (ELFMEM_DB/ELFMEM_CONFIG set)  ← works
4. SELF frame has identity blocks  ← works
5. status() says "No SELF blocks. Run: elfmem init --self '...'"  ← discoverable
```

---

## Before/After Analysis

### Before: Control Flow for Config Loading

```
elfmem serve --db ~/.elfmem/agent.db --config ~/.elfmem/config.yaml
  → cli.serve()
  → mcp.main(db_path="~/.elfmem/agent.db", config_path="~/.elfmem/config.yaml")
  → SmartMemory.open(db_path, config="~/.elfmem/config.yaml")
  → MemorySystem.from_config(db_path, "~/.elfmem/config.yaml")
  → _resolve_config("~/.elfmem/config.yaml")
  → ElfmemConfig.from_yaml("~/.elfmem/config.yaml")
  → open("~/.elfmem/config.yaml")           ← FileNotFoundError: ~ not expanded
```

### After: Control Flow for Config Loading

```
elfmem serve --db ~/.elfmem/agent.db --config ~/.elfmem/config.yaml
  → cli.serve()
  → mcp.main(db_path="~/.elfmem/agent.db", config_path="~/.elfmem/config.yaml")
  → SmartMemory.open(db_path, config="~/.elfmem/config.yaml")
  → MemorySystem.from_config(db_path, "~/.elfmem/config.yaml")
  → _resolve_config("~/.elfmem/config.yaml")
  → ElfmemConfig.from_yaml("~/.elfmem/config.yaml")
  → open(Path("~/.elfmem/config.yaml").expanduser())   ← works
```

### Before: Control Flow for elfmem init (does not exist)

```
elfmem init --self "I am Claude Code"
  → error: No such command 'init'.
```

### After: Control Flow for elfmem init

```
elfmem init --self "I am Claude Code"
  → cli.init(self_description="I am Claude Code", config_dir="~/.elfmem")
  → _ensure_config_dir("~/.elfmem")            # mkdir -p
  → _write_default_config("~/.elfmem/config.yaml")  # generate from ElfmemConfig()
  → SmartMemory.managed(db_path, config)       # open DB (created if absent)
  → mem.remember("I am Claude Code", tags=["self"])  # seed SELF
  → echo: "✓ Created ~/.elfmem/config.yaml"
          "✓ Created ~/.elfmem/agent.db"
          "✓ SELF block stored (block_id: a1b2c3d4)"
```

### Before: Control Flow for elfmem_setup (does not exist)

```
Claude: [calls elfmem_setup("I am Claude Code")]
  → Tool not found
```

### After: Control Flow for elfmem_setup

```
Claude: [calls elfmem_setup("I am Claude Code", values=["clean code"])]
  → mcp.elfmem_setup(identity="I am Claude Code", values=["clean code"])
  → _mem().remember("I am Claude Code", tags=["self"])
  → _mem().remember("clean code", tags=["self", "value"])
  → {"status": "setup_complete", "blocks_created": 2, "blocks": [...]}
```

### Before: _derive_health() suggestion (empty state)

```python
if active_count == 0 and inbox_count == 0:
    return "good", "Memory is empty. Call learn() to add knowledge."
```

### After: _derive_health() suggestion (empty state)

```python
if active_count == 0 and inbox_count == 0:
    return "good", "Memory is empty. Seed your identity: elfmem init --self '...'"
```

---

## Files to Create/Modify

| File | Action | Scope |
|------|--------|-------|
| `src/elfmem/config.py` | Modify | 1-line bug fix: add `.expanduser()` in `from_yaml()` |
| `src/elfmem/db/queries.py` | Modify | Add `count_self_blocks(conn) -> int` helper |
| `src/elfmem/api.py` | Modify | Update empty-state suggestion in `_derive_health()` |
| `src/elfmem/cli.py` | Modify | Add `init` and `doctor` commands |
| `src/elfmem/mcp.py` | Modify | Add `elfmem_setup` tool |
| `src/elfmem/guide.py` | Modify | Add `setup` guide entry; update OVERVIEW |
| `tests/test_init.py` | Create | Tests for init, doctor, and elfmem_setup |

---

## Implementation Steps

### Step 0 — Bug Fix: `~` Path Expansion in `config.py`

**File:** `src/elfmem/config.py`

**Problem:** `from_yaml()` uses `open(path)` which does not expand `~`. Any path like
`~/.elfmem/config.yaml` raises `FileNotFoundError`.

**Before** (`config.py:179`):
```python
@classmethod
def from_yaml(cls, path: str) -> ElfmemConfig:
    with open(path) as f:
        data = yaml.safe_load(f)
    data = {k: v for k, v in (data or {}).items() if v is not None}
    return cls.model_validate(data)
```

**After** (one line change):
```python
@classmethod
def from_yaml(cls, path: str) -> ElfmemConfig:
    with open(Path(path).expanduser()) as f:   # ← .expanduser() added
        data = yaml.safe_load(f)
    data = {k: v for k, v in (data or {}).items() if v is not None}
    return cls.model_validate(data)
```

**Note:** `Path` is already imported at the top of `config.py`. This is a true one-line fix.

---

### Step 1 — DB Helper: `count_self_blocks` in `db/queries.py`

**File:** `src/elfmem/db/queries.py`

**Purpose:** The `doctor` command needs to check whether any SELF blocks exist without
creating a full SmartMemory session. A direct SQL COUNT is the minimal approach.

**Add after existing query functions:**
```python
async def count_self_blocks(conn: AsyncConnection) -> int:
    """Count active blocks that carry the 'self' tag or any 'self/*' tag.

    Uses SQLite json_each() to inspect the JSON tags array.
    Returns 0 if no SELF blocks exist (memory has not been seeded).
    """
    result = await conn.execute(
        text(
            "SELECT COUNT(*) FROM blocks "
            "WHERE status = 'active' "
            "AND EXISTS ("
            "  SELECT 1 FROM json_each(tags) "
            "  WHERE value = 'self' OR value LIKE 'self/%'"
            ")"
        )
    )
    row = result.fetchone()
    return int(row[0]) if row else 0
```

**Imports needed:** `text` from `sqlalchemy` (already imported in queries.py).

---

### Step 2 — Update Empty-State Suggestion in `api.py`

**File:** `src/elfmem/api.py`

**Purpose:** When memory is empty, the current suggestion mentions `learn()`. It should
point users toward SELF setup first — that is the correct starting action.

**Before** (`api.py:785–786`):
```python
if active_count == 0 and inbox_count == 0:
    return "good", "Memory is empty. Call learn() to add knowledge."
```

**After** (suggestion updated):
```python
if active_count == 0 and inbox_count == 0:
    return "good", "Memory is empty. Seed your identity: elfmem init --self '...'"
```

This is the only change to `api.py`. The function signature of `_derive_health()` does
not change; no callers need updating.

---

### Step 3 — Add Config Generator to `config.py`

**File:** `src/elfmem/config.py`

**Purpose:** `elfmem init` generates a commented `config.yaml`. The content must be
derived from `ElfmemConfig()` defaults so the generated file always matches the code.

**Add module-level function** (after `ElfmemConfig` class):
```python
def render_default_config() -> str:
    """Render a commented default config.yaml string from ElfmemConfig() defaults.

    Used by `elfmem init` to generate ~/.elfmem/config.yaml.
    Values are sourced from ElfmemConfig() so they always match code defaults.
    """
    import textwrap
    d = ElfmemConfig()
    return textwrap.dedent(f"""\
        # elfmem configuration
        # Generated by: elfmem init
        # Edit as needed. All sections are optional — missing keys use code defaults.
        # API keys are NOT stored here — set them as environment variables:
        #   ANTHROPIC_API_KEY, OPENAI_API_KEY, GROQ_API_KEY, etc.

        llm:
          model: "{d.llm.model}"
          temperature: {d.llm.temperature}
          max_tokens: {d.llm.max_tokens}
          timeout: {d.llm.timeout}
          max_retries: {d.llm.max_retries}

        embeddings:
          model: "{d.embeddings.model}"
          dimensions: {d.embeddings.dimensions}
          timeout: {d.embeddings.timeout}

        memory:
          inbox_threshold: {d.memory.inbox_threshold}
          curate_interval_hours: {d.memory.curate_interval_hours}
          self_alignment_threshold: {d.memory.self_alignment_threshold}
          contradiction_threshold: {d.memory.contradiction_threshold}
          near_dup_exact_threshold: {d.memory.near_dup_exact_threshold}
          near_dup_near_threshold: {d.memory.near_dup_near_threshold}
          similarity_edge_threshold: {d.memory.similarity_edge_threshold}
          edge_degree_cap: {d.memory.edge_degree_cap}
          top_k: {d.memory.top_k}
          search_window_hours: {d.memory.search_window_hours}
          outcome_prior_strength: {d.memory.outcome_prior_strength}
          outcome_reinforce_threshold: {d.memory.outcome_reinforce_threshold}
          penalize_threshold: {d.memory.penalize_threshold}

        # Custom prompts (optional — uncomment to override library defaults):
        # prompts:
        #   process_block_file: "~/.elfmem/prompts/process_block.txt"
        #   contradiction_file: "~/.elfmem/prompts/contradiction.txt"
    """)
```

**Why this approach:**
- Values come from `ElfmemConfig()` — if defaults change in code, generated config reflects them
- `textwrap.dedent` with f-string is clean and readable
- Import of `textwrap` is local to keep module-level imports clean

---

### Step 4 — CLI Commands: `init` and `doctor` in `cli.py`

**File:** `src/elfmem/cli.py`

#### 4a. `elfmem init` command

**Signature:**
```python
@app.command()
def init(
    self_description: Annotated[
        str | None, typer.Option("--self", help="Seed SELF frame with identity description")
    ] = None,
    db: Annotated[
        str, typer.Option("--db", envvar="ELFMEM_DB", help="Database path")
    ] = "~/.elfmem/agent.db",
    config_path: Annotated[
        str, typer.Option("--config", envvar="ELFMEM_CONFIG", help="Config YAML path")
    ] = "~/.elfmem/config.yaml",
    force: Annotated[
        bool, typer.Option("--force", help="Overwrite existing config")
    ] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Initialise elfmem: create config directory, generate config, and optionally seed SELF."""
```

**Logic flow:**
```
1. Expand ~ in db and config_path with os.path.expanduser()
2. Create parent directory of config_path with Path.mkdir(parents=True, exist_ok=True)
3. If config file does not exist (or --force):
   → Write render_default_config() to config_path
   → Record: "created config"
   else:
   → Record: "config already exists, skipped"
4. If --self provided:
   → _run(_init_self(db_path, config_path, self_description))
   → Record: LearnResult summary
5. Print summary (or JSON)
```

**Async helper** (added at bottom of cli.py with other async helpers):
```python
async def _init_self(db_path: str, config: str, content: str) -> LearnResult:
    async with SmartMemory.managed(db_path, config=config) as mem:
        return await mem.remember(content, tags=["self"])
```

**Output (text mode):**
```
elfmem init
✓  Config:   ~/.elfmem/config.yaml (created)
✓  Database: ~/.elfmem/agent.db (ready)

elfmem init --self "I am Claude Code, a software engineering assistant"
✓  Config:   ~/.elfmem/config.yaml (created)
✓  Database: ~/.elfmem/agent.db (ready)
✓  SELF:     Stored block a1b2c3d4. Status: created.
   Next: elfmem serve --db ~/.elfmem/agent.db --config ~/.elfmem/config.yaml
```

**Output (JSON mode):**
```json
{
  "config_path": "/Users/emson/.elfmem/config.yaml",
  "config_action": "created",
  "db_path": "/Users/emson/.elfmem/agent.db",
  "self_block": {"block_id": "a1b2c3d4", "status": "created"}
}
```

**Idempotency rules:**
- Config: skip if exists (unless `--force`)
- DB: `MemorySystem.from_config()` already handles create-if-absent
- SELF block: `remember()` returns `"duplicate_rejected"` for exact duplicates — safe to re-run

---

#### 4b. `elfmem doctor` command

**Signature:**
```python
@app.command()
def doctor(
    db: Annotated[
        str | None, typer.Option("--db", envvar="ELFMEM_DB", help="Database path")
    ] = None,
    config: Annotated[
        str | None, typer.Option("--config", envvar="ELFMEM_CONFIG", help="Config YAML path")
    ] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Diagnose your elfmem setup. Reports what is configured and what is missing."""
```

**Logic flow:**
```
1. Resolve default paths: db → "~/.elfmem/agent.db", config → "~/.elfmem/config.yaml"
2. Expand ~ with os.path.expanduser() on both paths
3. Check config_dir exists (parent of config path)
4. Check config file exists
5. Check db file exists
6. Check API keys: ANTHROPIC_API_KEY, OPENAI_API_KEY (warn if both absent)
7. If db exists: open engine, run count_self_blocks(conn), close
8. Print results with ✓/✗/⚠ prefixes
```

**DB check** (async helper):
```python
async def _doctor_self_count(db_path: str) -> int:
    """Count active SELF blocks. Returns -1 if DB is not accessible."""
    from elfmem.db.engine import create_engine
    from elfmem.db.queries import count_self_blocks
    try:
        engine = await create_engine(db_path)
        async with engine.connect() as conn:
            count = await count_self_blocks(conn)
        await engine.dispose()
        return count
    except Exception:
        return -1
```

**Note:** This does NOT open a SmartMemory session — just a raw engine connect to run
the COUNT query. No LLM calls, no session tracking, no side effects.

**Output (text mode):**
```
elfmem doctor
✓  Config dir:  ~/.elfmem/ (exists)
✓  Config:      ~/.elfmem/config.yaml (exists)
✓  Database:    ~/.elfmem/agent.db (exists)
✓  SELF:        3 SELF blocks found
✓  API keys:    ANTHROPIC_API_KEY set

elfmem doctor   (empty state)
✓  Config dir:  ~/.elfmem/ (exists)
✓  Config:      ~/.elfmem/config.yaml (exists)
✓  Database:    ~/.elfmem/agent.db (exists)
✗  SELF:        No SELF blocks found
   Suggestion:  elfmem init --self "Describe your agent identity here"
⚠  API keys:    Neither ANTHROPIC_API_KEY nor OPENAI_API_KEY is set
   Suggestion:  export ANTHROPIC_API_KEY="sk-ant-..."
```

**Exit code:**
- `0` if all checks pass
- `1` if any `✗` checks fail (useful for CI/CD pipelines)

---

### Step 5 — MCP Tool: `elfmem_setup` in `mcp.py`

**File:** `src/elfmem/mcp.py`

**Add after `elfmem_guide`:**
```python
@mcp.tool()
async def elfmem_setup(
    identity: str,
    values: list[str] | None = None,
) -> dict[str, Any]:
    """Bootstrap agent identity in the SELF frame.

    Call this on first use to establish who you are. Creates SELF-tagged blocks
    from a natural language description. Safe to call multiple times — exact
    duplicates are silently rejected.

    identity: Natural language description of agent role, personality, constraints.
    values:   Optional list of core values or principles (each stored as a block).

    Returns: blocks_created count and per-block status.

    Example:
        elfmem_setup(
            identity="I am Claude Code, an AI-powered software engineering assistant.",
            values=["clean minimal code", "always confirm destructive operations"]
        )
    """
    results = []
    identity_result = await _mem().remember(identity, tags=["self"])
    results.append(identity_result.to_dict())

    if values:
        for value in values:
            r = await _mem().remember(value, tags=["self", "value"])
            results.append(r.to_dict())

    created = sum(1 for r in results if r["status"] == "created")
    return {
        "status": "setup_complete",
        "blocks_created": created,
        "blocks": results,
    }
```

**Design notes:**
- Uses `_mem().remember()` directly — same path as `elfmem_remember`
- `tags=["self"]` is what places blocks in the SELF frame
- `values` are tagged `["self", "value"]` for finer-grained retrieval later
- No new infrastructure — calls existing `SmartMemory.remember()`
- Idempotent: `remember()` returns `"duplicate_rejected"` for identical content

---

### Step 6 — Guide Entry: `setup` in `guide.py`

**File:** `src/elfmem/guide.py`

**Add to `GUIDES` dict:**
```python
"setup": AgentGuide(
    name="setup",
    what="Bootstrap agent identity by seeding the SELF frame with core identity blocks.",
    when=(
        "First use — before any other operations. "
        "Also when the agent's role, values, or constraints change significantly."
    ),
    when_not=(
        "Every session — once seeded, SELF blocks persist and decay slowly. "
        "Don't re-seed unchanged identity on every run."
    ),
    cost="Fast per block. One LLM call per block during consolidate().",
    returns=(
        "dict with blocks_created (int) and blocks (list of LearnResult dicts). "
        "status='setup_complete' always. blocks_created=0 means all were duplicates."
    ),
    next=(
        "SELF blocks are in inbox until consolidate() runs (auto on session close). "
        "After consolidation, elfmem_recall(frame='self') returns your identity context."
    ),
    example=(
        "elfmem_setup(\n"
        "    identity='I am Claude Code, an AI-powered software engineering assistant.',\n"
        "    values=['clean minimal code', 'confirm before destructive operations']\n"
        ")"
    ),
),
```

**Update `OVERVIEW` string** — add `setup` to the operations table:
```
  elfmem_setup(identity)     Fast         Seed SELF frame with agent identity
```

---

## Key Invariants

1. **No new dependencies** — `textwrap` is stdlib; all other imports are already present
2. **Idempotent init** — re-running `elfmem init` is safe: config skipped if exists, SELF block
   returns `duplicate_rejected` for unchanged content
3. **Bug fix is non-breaking** — `Path(path).expanduser()` is a drop-in; absolute paths
   and relative paths are unaffected (expanduser is a no-op when `~` is absent)
4. **doctor has no write side effects** — read-only: filesystem checks + SQL COUNT only;
   does not start sessions, create blocks, or modify the database
5. **`elfmem_setup` is a thin wrapper** — calls `SmartMemory.remember()` directly;
   no special SELF machinery; tags are the only mechanism
6. **SELF blocks are ordinary blocks** — no schema changes, no new DB tables; `tags=["self"]`
   is sufficient for the SELF frame filter
7. **Config generated from defaults** — `render_default_config()` calls `ElfmemConfig()`;
   if defaults change in code, regenerated configs will reflect them
8. **`doctor` exit code** — exits `1` on any `✗` failure; `0` on clean; CI/CD safe

---

## Done Criteria

### Step 0 — Bug Fix
- `elfmem serve --config ~/.elfmem/config.yaml` does not raise `FileNotFoundError`
- `ElfmemConfig.from_yaml("~/.elfmem/config.yaml")` works correctly
- Absolute paths (`/Users/emson/.elfmem/config.yaml`) still work

### Step 1 — DB Helper
- `count_self_blocks(conn)` returns `0` on empty DB
- `count_self_blocks(conn)` returns correct count after inserting SELF-tagged blocks
- `count_self_blocks(conn)` counts `tags=["self"]` and `tags=["self/identity"]` but not `tags=["other"]`

### Step 2 — API suggestion
- `status()` on empty DB returns suggestion containing `elfmem init --self`
- `status()` on non-empty DB returns normal suggestions (unchanged)

### Step 3 — Config generator
- `render_default_config()` returns valid YAML (parseable by `yaml.safe_load`)
- Values in rendered YAML match `ElfmemConfig()` defaults
- `ElfmemConfig.from_yaml(rendered_file)` produces `ElfmemConfig()` (round-trip)

### Step 4 — CLI init / doctor
- `elfmem init` creates `~/.elfmem/config.yaml` and prints confirmation
- `elfmem init` on existing config prints "already exists, skipped" (no `--force`)
- `elfmem init --force` overwrites existing config
- `elfmem init --self "..."` stores a SELF-tagged block and reports block_id
- `elfmem init --self "..."` re-run produces `duplicate_rejected`, not an error
- `elfmem init --json` outputs valid JSON
- `elfmem doctor` prints `✓` for every check when fully configured
- `elfmem doctor` prints `✗ SELF: No SELF blocks` on empty DB
- `elfmem doctor` prints `⚠` when API keys absent
- `elfmem doctor` exits `1` when any check fails

### Step 5 — MCP elfmem_setup
- `elfmem_setup(identity="...")` returns `{"status": "setup_complete", "blocks_created": 1, ...}`
- `elfmem_setup(identity="...", values=["v1", "v2"])` returns `blocks_created: 3`
- Re-calling with same identity returns `blocks_created: 0` (all duplicates)
- SELF blocks appear when `elfmem_recall(frame="self")` is called after consolidation

### Step 6 — Guide
- `system.guide("setup")` returns a non-empty guide string
- `system.guide()` overview table includes `setup`
- `elfmem guide setup` (CLI) prints the setup guide

### Regression
- All existing tests pass unchanged
- `elfmem remember`, `recall`, `status`, `outcome`, `curate`, `serve`, `guide` unaffected

---

## File Locations Summary

```
src/elfmem/
├── config.py          ← Step 0 (bug fix: expanduser) + Step 3 (render_default_config)
├── db/
│   └── queries.py     ← Step 1 (count_self_blocks helper)
├── api.py             ← Step 2 (empty-state suggestion text only)
├── cli.py             ← Step 4 (init + doctor commands + async helpers)
├── mcp.py             ← Step 5 (elfmem_setup tool)
└── guide.py           ← Step 6 (setup guide entry + OVERVIEW update)

tests/
└── test_init.py       ← New: tests for steps 0–6
```

---

## Implementation Order

Implement in this order to maintain a working system at each step:

```
Step 0  → fixes the existing ~ bug (unblocks everything)
Step 1  → adds count_self_blocks (needed by doctor)
Step 2  → improves status() suggestion (standalone, no deps)
Step 3  → adds render_default_config (needed by init)
Step 4a → adds elfmem init command
Step 4b → adds elfmem doctor command
Step 5  → adds elfmem_setup MCP tool
Step 6  → adds guide entry (polish, last)
```

Each step is independently testable. Steps 2, 3, 5, 6 have no dependencies on each other.
