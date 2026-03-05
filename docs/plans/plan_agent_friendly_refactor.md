# Plan: Agent-Friendly API Refactor

**Reference:** `docs/agent_friendly_principles.md`
**Principle:** Minimal code changes. Additive-only. No breaking changes to existing signatures, tests, or return type field names.

---

## Before/After Analysis

### Current State (Before)

The library is internally well-structured but presents a developer-facing surface. Specifically:

**`src/elfmem/types.py`**
- Five result dataclasses (`LearnResult`, `ConsolidateResult`, `FrameResult`, `CurateResult`, `ScoredBlock`) with Python default `__repr__`. No `__str__`, no `to_dict()`, no `summary`.
- An agent printing `str(learn_result)` gets `"LearnResult(block_id='a1b2', status='created')"` — syntactically a repr, not an agent-readable sentence.

**`src/elfmem/api.py`**
- Eight public methods with `Args/Returns` docstrings. No `USE WHEN`, no `COST`, no `NEXT`.
- No `guide()` — an agent has no runtime self-documentation access.
- No `status()` — an agent cannot query inbox depth, session state, or block counts.
- No `history()` — an agent cannot inspect what operations recently ran.
- No operation logging.

**`src/elfmem/__init__.py`**
- Exports only `MemorySystem`. All result types require internal import paths.

**No custom exception hierarchy**
- Errors propagate as raw Python exceptions (`ValueError`, `RuntimeError`) with no `recovery` field.

**`src/elfmem/db/queries.py`**
- Has `get_inbox_count()` but no consolidated block-count query across all statuses.
- `consolidate()` does not record its completion timestamp in `system_config`.

### Target State (After)

| Layer | Change | Risk |
|-------|--------|------|
| `types.py` | Add `__str__`, `to_dict()`, `summary` to all result types; add `SystemStatus` and `OperationRecord` dataclasses | Zero — pure additions |
| `exceptions.py` (new) | `ElfmemError` base + 4 subclasses with `recovery` field | Zero — new file |
| `guide.py` (new) | `AgentGuide` dataclass + static guide data for all public methods | Zero — new file, no deps |
| `api.py` | Add `guide()`, `status()`, `history()`; add `_record_op()` and `_history` deque; update docstrings to agent contract template; record `last_consolidated_at` in system_config after consolidate | Low — additive; docstring changes are non-functional |
| `__init__.py` | Export all public result types and exceptions | Zero — additive |
| `db/queries.py` | Add `get_block_counts()` returning `{inbox, active, archived}` in one query | Zero — pure addition |
| `tests/test_result_types.py` (new) | Tests for `__str__`, `to_dict()`, `summary` | New file |
| `tests/test_agent_api.py` (new) | Tests for `guide()`, `status()`, `history()` | New file |

**Nothing changes in:** `operations/`, `memory/`, `context/`, `db/models.py`, `scoring.py`, `session.py`, `config.py`, `prompts.py`, `ports/`, `adapters/`

---

## Logic Flow Analysis

### `learn()` — Before
```
api.learn(content, tags, category, source)
  └─ engine.begin() → conn
  └─ operations.learn(conn, ...) → LearnResult(block_id, status)
  └─ return LearnResult
```

### `learn()` — After
```
api.learn(content, tags, category, source)
  └─ engine.begin() → conn
  └─ operations.learn(conn, ...) → LearnResult(block_id, status)  [+ __str__, to_dict()]
  └─ self._record_op("learn", result.summary)          ← 1 new line
  └─ return LearnResult
```

### `consolidate()` — Before
```
api.consolidate()
  └─ compute_current_active_hours()
  └─ engine.begin() → conn
  └─ operations.consolidate(conn, ...) → ConsolidateResult
  └─ should_curate? → operations.curate(conn, ...)
  └─ return ConsolidateResult
```

### `consolidate()` — After
```
api.consolidate()
  └─ compute_current_active_hours()
  └─ engine.begin() → conn
  └─ operations.consolidate(conn, ...) → ConsolidateResult  [+ __str__, to_dict()]
  └─ should_curate? → operations.curate(conn, ...)
  └─ set_config(conn, "last_consolidated_at", _now_iso())   ← 1 new line (façade layer)
  └─ self._record_op("consolidate", result.summary)         ← 1 new line
  └─ return ConsolidateResult
```

### `status()` — New flow
```
api.status()                                           ← new method
  └─ engine.connect() → conn  (read-only, no transaction)
  └─ db.queries.get_block_counts(conn)
       → {inbox: N, active: N, archived: N}            ← 1 new query
  └─ db.queries.get_config(conn, "last_consolidated_at")  (existing query)
  └─ session_active = self._session_id is not None
  └─ session_hours = session._elapsed_hours() if active
  └─ suggestion = _derive_suggestion(counts, session_active, threshold)
  └─ return SystemStatus(...)                           ← new dataclass
```

### `guide()` — New flow
```
api.guide(method_name=None)                            ← new method
  └─ if None: return overview string (static)
  └─ elif method_name in GUIDES: return AgentGuide     ← static dict lookup
  └─ else: return "Unknown method. Available: [list]"
```
No DB access. No async required. Synchronous. Zero cost.

### `history()` — New flow
```
api.history(last_n=10)                                 ← new method
  └─ records = list(self._history)[-last_n:]           ← in-memory deque
  └─ return OperationHistory(records)                  ← new dataclass w/ __str__
```
No DB access. In-memory only. Zero cost.

---

## Implementation Phases

### Phase 1 — Foundation: Types + Exceptions + Exports
*Scope: Pure additions, zero risk, no logic changes*

**`src/elfmem/exceptions.py`** — New file
```
ElfmemError(Exception)          base; holds .recovery: str
  SessionError(ElfmemError)     no active session
  ConfigError(ElfmemError)      bad configuration
  StorageError(ElfmemError)     database-level failure
  FrameError(ElfmemError)       unknown frame name requested
```
Each exception stores `message` + `recovery`. `__str__` returns `"{message} — Recovery: {recovery}"`.

**`src/elfmem/types.py`** — Add to existing dataclasses
- `LearnResult.__str__` → `"Stored block {id[:8]}. Status: {status}."`
  (status='duplicate_rejected' → `"Duplicate rejected — block {id[:8]} already exists."`)
- `LearnResult.summary` → same one-liner (property backed by `__str__`)
- `LearnResult.to_dict()` → `{"block_id": ..., "status": ...}`
- `ConsolidateResult.__str__` → `"Consolidated {processed}: {promoted} promoted, {deduplicated} deduped, {edges_created} edges."`
  (if processed=0 → `"Nothing to consolidate. Inbox was empty."`)
- `ConsolidateResult.to_dict()` → `{"processed": ..., "promoted": ..., ...}`
- `FrameResult.__str__` → `"{frame_name} frame: {len(blocks)} blocks{' (cached)' if cached else ''}."`
- `FrameResult.to_dict()` → `{"frame_name": ..., "block_count": ..., "cached": ..., "text": ...}`
- `CurateResult.__str__` → `"Curated: {archived} archived, {edges_pruned} edges pruned, {reinforced} reinforced."`
- `CurateResult.to_dict()` → `{"archived": ..., "edges_pruned": ..., "reinforced": ...}`
- Add `SystemStatus` dataclass (used by `status()` in Phase 2):
  ```python
  @dataclass
  class SystemStatus:
      session_active: bool
      session_hours: float | None
      inbox_count: int
      inbox_threshold: int
      active_count: int
      archived_count: int
      total_active_hours: float
      last_consolidated: str        # ISO string or "never"
      health: str                   # "good" | "attention" | "degraded"
      suggestion: str               # one actionable sentence

      def __str__(self) -> str: ...
      def to_dict(self) -> dict: ...
  ```
- Add `OperationRecord` dataclass (used by `history()` in Phase 3):
  ```python
  @dataclass
  class OperationRecord:
      operation: str        # "learn", "consolidate", etc.
      summary: str          # str(result) at call time
      timestamp: str        # ISO string

      def __str__(self) -> str: ...
  ```

**`src/elfmem/__init__.py`** — Expand exports
```python
from elfmem.api import MemorySystem
from elfmem.types import (
    LearnResult, ConsolidateResult, FrameResult, CurateResult,
    ScoredBlock, SystemStatus, OperationRecord,
)
from elfmem.exceptions import (
    ElfmemError, SessionError, ConfigError, StorageError, FrameError,
)
from elfmem.config import ElfmemConfig

__all__ = [
    "MemorySystem", "ElfmemConfig",
    "LearnResult", "ConsolidateResult", "FrameResult", "CurateResult",
    "ScoredBlock", "SystemStatus", "OperationRecord",
    "ElfmemError", "SessionError", "ConfigError", "StorageError", "FrameError",
]
```

**Tests: `tests/test_result_types.py`** — New file
- `test_learn_result_str_created`
- `test_learn_result_str_duplicate_rejected`
- `test_learn_result_str_near_duplicate_superseded`
- `test_learn_result_to_dict_keys`
- `test_consolidate_result_str_nonzero`
- `test_consolidate_result_str_zero_processed`
- `test_consolidate_result_to_dict_keys`
- `test_frame_result_str_uncached`
- `test_frame_result_str_cached`
- `test_curate_result_str_nonzero`
- `test_curate_result_str_all_zero`
- `test_elfmem_error_recovery_in_str`
- `test_session_error_is_elfmem_error`

---

### Phase 2 — Self-Description: `guide()` and `status()`
*Scope: New query, new module, new methods on MemorySystem*

**`src/elfmem/db/queries.py`** — Add one function
```python
async def get_block_counts(conn: AsyncConnection) -> dict[str, int]:
    """Return {inbox, active, archived} block counts in a single query."""
    result = await conn.execute(
        select(blocks.c.status, func.count().label("n"))
        .group_by(blocks.c.status)
    )
    counts = {"inbox": 0, "active": 0, "archived": 0}
    for row in result.mappings():
        if row["status"] in counts:
            counts[row["status"]] = row["n"]
    return counts
```

**`src/elfmem/guide.py`** — New file
```python
@dataclass(frozen=True)
class AgentGuide:
    name: str
    what: str        # one sentence
    when: str        # decision criteria
    when_not: str    # anti-patterns
    cost: str        # "Instant" | "Fast" | "LLM call" | "Slow (batch)"
    returns: str     # what comes back + possible values
    next: str        # typical follow-up
    example: str     # minimal working code snippet

# Static dict: method name → AgentGuide
GUIDES: dict[str, AgentGuide] = {
    "learn": AgentGuide(
        name="learn",
        what="Store a knowledge block for future retrieval.",
        when="The agent discovers a fact, preference, decision, or observation "
             "worth remembering across sessions.",
        when_not="Transient context that only matters in the current turn, or "
                 "information already in the active prompt.",
        cost="Instant. No LLM calls.",
        returns="LearnResult. status values: 'created' (new block stored in inbox), "
                "'duplicate_rejected' (exact content already exists), "
                "'near_duplicate_superseded' (similar block replaced).",
        next="Blocks queue in inbox until consolidate() runs. Session context "
             "manager auto-consolidates on exit when inbox >= threshold.",
        example="result = await system.learn('User prefers dark mode')\nprint(result)",
    ),
    "consolidate": AgentGuide(...),
    "frame": AgentGuide(...),
    "recall": AgentGuide(...),
    "curate": AgentGuide(...),
    "status": AgentGuide(...),
    "history": AgentGuide(...),
    "guide": AgentGuide(...),
}

OVERVIEW: str  # compact table: name | cost | one-line description
```

**`src/elfmem/api.py`** — Add `guide()` and `status()` methods; update docstrings

`guide()`:
```python
def guide(self, method_name: str | None = None) -> str:
    """Return agent-friendly documentation for this library or a specific method.

    USE WHEN: An agent needs to understand what methods are available or how
    a specific method should be used.

    COST: Instant. No database access.

    RETURNS: String. With no argument: compact overview of all operations.
    With a method name: full AgentGuide for that method.
    Unknown method name: list of valid names.
    """
```

`status()`:
```python
async def status(self) -> SystemStatus:
    """Return a snapshot of current system state with a suggested next action.

    USE WHEN: An agent needs to decide whether to consolidate, curate, or
    start a session, or wants to understand memory health.

    COST: Fast. One database read (no LLM calls).

    RETURNS: SystemStatus with inbox_count, active_count, session_active,
    health ('good'|'attention'|'degraded'), and a suggestion string.
    """
```

Health derivation logic (pure, in `guide.py` or `api.py`):
```python
def _derive_health_and_suggestion(
    inbox_count: int, inbox_threshold: int,
    active_count: int, session_active: bool,
) -> tuple[str, str]:
    fill_ratio = inbox_count / max(inbox_threshold, 1)
    if fill_ratio >= 1.0:
        return "attention", "Inbox full. Call consolidate() to process pending blocks."
    if fill_ratio >= 0.8:
        return "good", f"Inbox {inbox_count}/{inbox_threshold}. Consolidation approaching."
    if active_count == 0 and inbox_count == 0:
        return "good", "Memory empty. Call learn() to add knowledge."
    return "good", "Memory healthy. No action required."
```

Also update `consolidate()` to record `last_consolidated_at` in system_config:
```python
# At the end of api.consolidate(), inside the same transaction:
await set_config(conn, "last_consolidated_at", _now_iso())
```

Update all public method docstrings to follow the agent contract template:
`USE WHEN / DON'T USE WHEN / COST / RETURNS / NEXT`

**Tests: `tests/test_agent_api.py`** — New file (partial list)
- `test_guide_overview_returns_string`
- `test_guide_known_method_returns_all_fields`
- `test_guide_unknown_method_returns_valid_names_list`
- `test_status_empty_db_health_good`
- `test_status_inbox_count_reflects_learns`
- `test_status_full_inbox_health_attention`
- `test_status_session_active_flag`
- `test_status_last_consolidated_after_consolidate`

---

### Phase 3 — Operation History: `history()`
*Scope: In-memory deque on MemorySystem instance, one `_record_op()` call per method*

**`src/elfmem/api.py`** — Modify `__init__` and add `_record_op` + `history()`

In `__init__`:
```python
from collections import deque
self._history: deque[OperationRecord] = deque(maxlen=100)
```

Add private helper:
```python
def _record_op(self, operation: str, summary: str) -> None:
    from datetime import UTC, datetime
    self._history.append(OperationRecord(
        operation=operation,
        summary=summary,
        timestamp=datetime.now(UTC).isoformat(),
    ))
```

Add `_record_op(...)` as the final line in: `learn()`, `consolidate()`, `curate()`, `frame()`, `recall()`, `begin_session()`, `end_session()`.

Add `history()` public method:
```python
def history(self, last_n: int = 10) -> list[OperationRecord]:
    """Return the most recent operations performed by this MemorySystem.

    USE WHEN: An agent gets unexpected results and needs to understand
    what operations have run in the current process session.

    COST: Instant. In-memory only. Does not persist across restarts.

    RETURNS: List of OperationRecord (operation, summary, timestamp),
    most recent last. Empty list if no operations have run.
    """
    records = list(self._history)
    return records[-last_n:] if last_n < len(records) else records
```

`OperationRecord.__str__` renders as:
```
learn()        → Stored block a1b2. Status: created.          (2 min ago)
consolidate()  → Consolidated 8: 7 promoted, 1 deduped, 12 edges.  (1 min ago)
```

**Tests: `tests/test_agent_api.py`** — Add to existing file
- `test_history_empty_initially`
- `test_history_records_learn_operation`
- `test_history_records_consolidate_operation`
- `test_history_last_n_limit`
- `test_history_max_100_records`

---

### Phase 4 — Error Hardening
*Scope: Replace bare exceptions in `api.py` with `ElfmemError` subclasses*

**`src/elfmem/api.py`** — Update error sites (these are the only sites where agents receive errors from the facade layer):

| Current | After |
|---------|-------|
| `if self._session_id is None: raise ...` | `raise SessionError("...", recovery="Use 'async with system.session():'...")` |
| Frame name not found propagates as `KeyError` from `get_frame_definition` | Wrap in `FrameError` with `recovery="Valid frames: 'self', 'attention', 'task'."` |
| Config loading errors | Wrap in `ConfigError` with `recovery` pointing to config docs |

No changes to `operations/` layer — only the public façade raises agent-friendly errors. Internal errors still propagate naturally (they indicate bugs, not agent misuse).

**Tests:** Update any existing tests that assert on raw exception types.

---

### Phase 5 — MCP Server (Separate Deliverable)
*Scope: New optional module. Requires `mcp` optional dependency.*

**`src/elfmem/mcp.py`** — New file

Exposes elfmem as an MCP server with auto-session management:

```
Tools:
  elfmem_learn(content, tags?, category?)     → str (str(LearnResult))
  elfmem_recall(query, top_k?)               → str (rendered blocks)
  elfmem_frame(name, query?, top_k?)         → str (FrameResult.text)
  elfmem_status()                             → str (str(SystemStatus))
  elfmem_guide(method_name?)                 → str (guide text)
  elfmem_consolidate()                        → str (str(ConsolidateResult))
  elfmem_curate()                             → str (str(CurateResult))
```

Auto-session strategy: The MCP server holds a singleton `MemorySystem`. On first tool call, it starts a session. An inactivity timer (configurable, default 30 min) ends the session and starts a fresh one on next call.

All tool return values are **strings** — MCP tool results go directly into the agent's context window. No JSON objects.

Tool description format (each):
```json
{
  "name": "elfmem_learn",
  "description": "Store something worth remembering. Use when the agent discovers information that should persist across sessions.",
  "inputSchema": { ... minimal, well-described parameters ... }
}
```

Entry point:
```python
# Start MCP server
python -m elfmem.mcp --db agent.db --config elfmem.yaml
```

Optional dependency in `pyproject.toml`:
```toml
[project.optional-dependencies]
mcp = ["mcp>=1.0"]
```

Install: `uv add elfmem[mcp]`

**New files for Phase 5:**
- `src/elfmem/mcp.py`
- `tests/test_mcp.py`

---

## File Locations Summary

```
src/elfmem/
├── __init__.py          MODIFIED  — expand exports (Phase 1)
├── api.py               MODIFIED  — add guide(), status(), history(), _record_op(),
│                                    update docstrings, record last_consolidated_at (Ph 2,3,4)
├── types.py             MODIFIED  — add __str__, to_dict(), summary; add SystemStatus,
│                                    OperationRecord dataclasses (Phase 1)
├── exceptions.py        NEW       — ElfmemError hierarchy (Phase 1)
├── guide.py             NEW       — AgentGuide dataclass + GUIDES dict + OVERVIEW (Phase 2)
├── mcp.py               NEW       — MCP server (Phase 5)
└── db/
    └── queries.py       MODIFIED  — add get_block_counts() (Phase 2)

tests/
├── test_result_types.py NEW       — __str__, to_dict(), exception tests (Phase 1)
├── test_agent_api.py    NEW       — guide(), status(), history() tests (Phases 2,3)
└── [existing tests]     UNCHANGED
```

---

## Constraints and Decisions

**What does NOT change:**
- Existing method signatures on `MemorySystem` — zero breaking changes
- Existing return type field names — `block_id`, `status`, `processed`, etc. unchanged
- All internal layers: `operations/`, `memory/`, `context/`, `scoring.py`, `session.py`, `prompts.py`, `ports/`, `adapters/`
- Existing test assertions — existing tests continue to pass unmodified

**Key decisions:**

1. **`__str__` vs `.for_agent()`** — Use `__str__`. It requires no method call knowledge; agents printing any result get the right output automatically.

2. **`LearnResult` inbox count** — `__str__` only shows what's in the dataclass (no inbox count). The `status()` method provides system-level context. This avoids changing `learn()` to accept inbox counts as context — which would require changes throughout the operations layer.

3. **`history()` is in-memory only** — Does not persist across process restarts. This is the right scope: history is for within-session debugging, not long-term audit. DB-backed history would be over-engineering for this use case.

4. **`guide()` is synchronous** — No `async`. No DB access. Callable before a session exists, immediately after construction. Agents can call it to understand the library without any setup.

5. **`status()` uses `engine.connect()` (read-only)**, not `engine.begin()` (write transaction). No writes, so no transaction needed. This is slightly faster and semantically correct.

6. **`last_consolidated_at` written in `api.consolidate()` facade**, not in `operations/consolidate.py`. The operations layer stays pure; timestamp recording is an observability concern owned by the façade.

7. **MCP in Phase 5 as optional dep** — MCP has a package dependency; users who don't need MCP shouldn't pull it in. Optional extras (`elfmem[mcp]`) is the correct pattern.

8. **Exception hardening scoped to `api.py` only** — Internal layers raise Python built-ins; that's appropriate for bugs/programming errors. Only the public façade raises `ElfmemError` subclasses, which are for agent-recoverable misuse patterns.

---

## Execution Order

| Phase | Files Changed | Risk | Effort |
|-------|--------------|------|--------|
| 1 — Foundation | `types.py`, `exceptions.py` (new), `__init__.py` | Zero | Low |
| 2 — Guide + Status | `guide.py` (new), `api.py`, `db/queries.py` | Low | Medium |
| 3 — History | `api.py` (deque + recording) | Zero | Low |
| 4 — Error hardening | `api.py` (exception sites only) | Low | Low |
| 5 — MCP server | `mcp.py` (new), `pyproject.toml` | Medium | High |

Phases 1–4 are a single cohesive refactor. Phase 5 is a separate deliverable that builds on the others.
