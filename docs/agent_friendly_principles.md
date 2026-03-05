# Agent-Friendly Library Design Principles

A library designed for LLM agents must serve a fundamentally different interaction model than one designed for human developers. This document captures the principles and patterns for building "agent-first" libraries — ones that are easy for agents to discover, use correctly, and recover from errors in.

These principles are general and apply to any library that agents will consume, whether via tool-use, MCP, code generation, or direct programmatic integration.

---

## The Core Distinction

A human developer reads tutorials, browses Stack Overflow, experiments in a REPL, and builds mental models over days. An LLM agent has one interaction loop:

1. Read tool descriptions or documentation (one shot)
2. Decide whether and how to call this API
3. Pass parameters
4. Interpret the result
5. Decide what to do next

Every design decision in an agent-first library must serve this loop. The enemy is ambiguity, surprise, and friction at any step.

---

## Principle 1: String-First Returns

**Why it matters:** Agents ultimately consume text. Result objects get serialised into the agent's context window. The library's most important output isn't the Python object — it's the string the agent reads when that object is serialised.

**Rule:** Every result type must have an agent-optimised `__str__` that:
- Leads with what happened (past-tense verb)
- Includes the most actionable context (counts, thresholds, state)
- Suggests a next action when relevant
- Fits on one or two lines

**Pattern:**
```python
@dataclass
class OperationResult:
    # ... fields ...

    def __str__(self) -> str:
        # "Stored block a1b2. Inbox: 8/10. Auto-consolidates at 10."
        # "Consolidated 10 blocks: 9 promoted, 1 deduplicated, 14 edges created."
        # "No relevant knowledge found."
```

**Format guidelines:**
- Use `|` to separate independent status facts: `"Session: active (1.2h) | Inbox: 8/10 | Active: 42 blocks"`
- Use `:` for label–value pairs: `"Stored block a1b2. Inbox: 8/10."`
- End with a suggestion only when the agent has a clear next action
- Never pad with filler words ("I found 2 blocks" → "2 blocks found")

**What NOT to do:**
```python
# Bad: no context, agent can't act on this
"LearnResult(block_id='a1b2c3d4', status='created')"

# Bad: verbose preamble wastes context tokens
"I successfully stored your knowledge block with ID a1b2c3d4. The operation completed
without errors. The block is now in the inbox queue and will be processed..."
```

**Corollary:** Provide a `.summary` property for a single-sentence summary and a `.detail` property or `.to_dict()` for full structured data. Let callers choose verbosity.

---

## Principle 2: Self-Describing API (`guide()` / `help()`)

**Why it matters:** External documentation isn't accessible at runtime. An agent generating code or deciding which tool to call relies on whatever it can introspect from the library itself. Docstrings are visible in IDE-like contexts but not in most tool-use scenarios.

**Rule:** Provide a built-in `guide()` or `help()` method that returns an agent-optimised reference at runtime.

**Pattern:**
```python
# Top-level overview
system.guide()
# Returns: list of all operations with one-line descriptions

# Method-specific deep dive
system.guide("learn")
# Returns structured AgentGuide with: what, when, when_not, cost, returns, next
```

**The `AgentGuide` contract:**
```python
@dataclass
class AgentGuide:
    name: str           # Method name
    what: str           # One sentence: what does this do?
    when: str           # When should an agent call this?
    when_not: str       # Anti-patterns: when is this the wrong choice?
    cost: str           # "Instant" | "LLM call" | "Slow (batch)"
    returns: str        # What comes back, and what the values mean
    next: str           # Typical follow-up actions
    example: str        # Minimal working example
```

**Edge case:** If `guide("nonexistent")` is called, return a list of valid names with one-liners. Never raise — agents recover better from helpful responses than from exceptions.

**Generalisation:** This applies to any non-trivial API. The `guide()` method is the library's runtime contract with its agent consumers. It costs little to implement and dramatically reduces agent confusion.

---

## Principle 3: Structured Method Docstrings

**Why it matters:** When agents read code or receive autocomplete context, docstrings are the primary source of truth. Generic docstrings ("Gets the frame") are useless. Agent-optimised docstrings are decision aids.

**Rule:** Every public method follows a strict five-field template.

**Template:**
```python
async def method_name(self, param: type, ...) -> ResultType:
    """One-sentence description of what this does.

    USE WHEN: [Decision criteria — what situation calls for this method?]

    DON'T USE WHEN: [Anti-patterns — common misuses to avoid.]

    COST: [Instant | Fast | LLM call (~Xs) | Slow (batch processing)]

    RETURNS: [ResultType description — enumerate possible status values and
    what each means.]

    NEXT: [What the caller should typically do after this. Mention
    auto-triggers if relevant.]
    """
```

**Example:**
```python
async def learn(self, content: str, tags: list[str] | None = None) -> LearnResult:
    """Store a knowledge block for future retrieval.

    USE WHEN: The agent discovers a fact, preference, decision, or observation
    worth remembering across sessions.

    DON'T USE WHEN: Information is transient (current turn only) or already
    present in the agent's prompt context.

    COST: Instant. No LLM calls.

    RETURNS: LearnResult — block_id and status. Status values:
      'created'                   — new block stored in inbox
      'duplicate_rejected'        — exact match already exists
      'near_duplicate_superseded' — similar block replaced

    NEXT: Blocks queue in inbox until consolidate() runs. Session context
    manager auto-consolidates on exit when inbox reaches threshold.
    """
```

**Why five fields?** They map directly to the agent's decision loop: (1) what is this? (2) should I call it now? (3) am I misusing it? (4) will it be slow? (5) what do I do with the result?

---

## Principle 4: System Status as Decision Context

**Why it matters:** An agent making decisions like "should I consolidate now?" or "is the system healthy?" needs observable state. Without it, agents either call methods speculatively (wasteful) or miss important triggers.

**Rule:** Provide a `status()` method that returns a structured system snapshot with a decision suggestion.

**Pattern:**
```python
@dataclass
class SystemStatus:
    # State
    session_active: bool
    session_hours: float | None
    inbox_count: int
    inbox_threshold: int
    active_count: int
    archived_count: int
    last_operation: str          # e.g., "consolidate (2h ago)"

    # Derived
    needs_consolidation: bool    # inbox_count >= inbox_threshold * 0.9
    health: str                  # "good" | "attention" | "degraded"
    suggestion: str              # One actionable sentence

    def __str__(self) -> str:
        return (
            f"Session: {'active' if self.session_active else 'inactive'} | "
            f"Inbox: {self.inbox_count}/{self.inbox_threshold} | "
            f"Active: {self.active_count} blocks | "
            f"Health: {self.health}"
        )
```

**Generalisation:** Any stateful system exposed to agents needs a `status()` or `health()` endpoint. It should be:
- Synchronous or fast (never blocks)
- Never raises (returns degraded status instead)
- Always includes a `suggestion` for what the agent should do next

---

## Principle 5: Instructive Errors

**Why it matters:** When an agent hits an error, it needs to know how to fix it — not just what went wrong. Stack traces and terse exception messages are designed for human debuggers, not agent recovery loops.

**Rule:** Every exception must include a `recovery` field: a complete, actionable instruction for the agent.

**Pattern:**
```python
class LibraryError(Exception):
    """Base exception. All library errors include a recovery hint."""
    def __init__(self, message: str, recovery: str):
        super().__init__(message)
        self.recovery = recovery

    def __str__(self) -> str:
        return f"{super().__str__()} — Recovery: {self.recovery}"


class SessionError(LibraryError):
    pass

# Usage:
raise SessionError(
    "No active session.",
    recovery="Use 'async with system.session():' to start one, "
             "or call system.begin_session() for manual control."
)
```

**Recovery message guidelines:**
- Provide the exact code or command needed to fix the problem
- Be specific: "Call `system.begin_session()` before `recall()`" not "Start a session"
- Include both the quick fix and the canonical pattern
- Never blame the agent — focus on what to do, not what was wrong

**When to raise vs. return gracefully:**

| Scenario | Action |
|----------|--------|
| Fundamental API misuse (no session, bad config) | Raise with recovery |
| "Nothing to do" (empty inbox, no blocks match) | Return empty result |
| Retry-able transient failure (network, timeout) | Raise with retry hint |
| Bad parameter value | Raise with valid value examples |

**Key insight:** Never raise on "nothing to do." An empty `consolidate()` should return `ConsolidateResult(processed=0, ...)` with `str()` → `"Nothing to consolidate. Inbox is empty."`, not an exception.

---

## Principle 6: Idempotency and Graceful Degradation

**Why it matters:** Agents retry operations. Agents call things in the wrong order. Agents lose state. The library must be robust to these patterns without punishing the agent.

**Rules:**
1. Duplicate operations must be safe. Calling `learn()` twice with the same content returns a graceful `duplicate_rejected` — never an error.
2. Empty operations must be safe. `consolidate()` on an empty inbox returns zero counts, not an exception.
3. Redundant calls must be safe. Calling `close()` twice is a no-op. Starting a session when one is already active returns the existing session ID.
4. Order independence where possible. If an operation requires prior state (e.g., session), either create it automatically or raise with a clear recovery message — don't silently fail.

**Pattern for idempotent operations:**
```python
async def learn(self, content: str, ...) -> LearnResult:
    # Content-hash deduplication — identical content always succeeds
    content_hash = hashlib.sha256(content.encode()).hexdigest()[:16]
    existing = await self._db.find_by_hash(content_hash)
    if existing:
        return LearnResult(block_id=existing.id, status="duplicate_rejected")
    # ... proceed
```

**Pattern for safe empty operations:**
```python
async def consolidate(self) -> ConsolidateResult:
    inbox = await self._db.get_inbox_blocks()
    if not inbox:
        return ConsolidateResult(processed=0, promoted=0, deduplicated=0, edges_created=0)
    # ... proceed
```

---

## Principle 7: Progressive Disclosure (Complexity Tiers)

**Why it matters:** A simple agent should succeed with minimal API knowledge. An advanced agent should have full control. Requiring complex setup for basic operations drives agents to abandon the library.

**Rule:** Design three usage tiers. Tier 1 must work with zero configuration and zero ceremony.

**Tier 1 — Minimal (2–3 methods, zero ceremony):**
```python
system = await Library.from_config("store.db")
await system.learn("User prefers dark mode")
results = await system.recall("What does the user prefer?")
```
No sessions, no lifecycle management, no configuration. The library handles everything internally with sensible defaults.

**Tier 2 — Standard (5–6 methods, explicit lifecycle):**
```python
async with system.session():
    await system.learn("User prefers dark mode")
    context = await system.frame("attention", query="preferences")
    # Use context.text in prompt
```
Explicit session management, multiple retrieval modes, auto-consolidation on exit.

**Tier 3 — Advanced (all methods + config):**
Full control: manual lifecycle, custom scoring, prompt overrides, per-call model selection, maintenance operations.

**Implementation implications:**
- Tier 1 requires smart defaults: auto-session, auto-consolidation, safe defaults for all config
- Methods should have sensible defaults for all optional parameters
- Tier 1 workflows should never require knowledge of Tier 3 concepts
- The library should work "out of the box" with just one constructor call

**Generalisation:** This tiered approach applies to any non-trivial library. The key discipline is ensuring Tier 1 is genuinely useful — not a toy version that forces users to Tier 2 for real work.

---

## Principle 8: Minimal Import Surface

**Why it matters:** Agents struggle with complex import trees. `from library.context.frames import FrameDefinition` requires deep knowledge of the module structure. One top-level import should give access to everything the agent needs.

**Rule:** All public types must be importable from the package root.

**Pattern:**
```python
# __init__.py — everything the agent needs
from library.core import LibrarySystem
from library.types import (
    LearnResult,
    RecallResult,
    FrameResult,
    SystemStatus,
    LibraryError,
    SessionError,
)
from library.config import LibraryConfig

__all__ = [
    "LibrarySystem",
    "LearnResult",
    "RecallResult",
    "FrameResult",
    "SystemStatus",
    "LibraryError",
    "SessionError",
    "LibraryConfig",
]
```

**Usage:** `from library import LibrarySystem, LibraryConfig` — nothing else needed.

**What to exclude from the top level:** Internal implementation classes, intermediate base classes, test utilities, anything an agent wouldn't directly instantiate or reference in type hints.

---

## Principle 9: Consistent Return Shape

**Why it matters:** If an agent knows the pattern of one return type, it should be able to predict the pattern of all return types. Inconsistency forces re-learning.

**Rules:**
1. All operations return typed result objects — never raw dicts, lists, or `None`
2. All result objects are dataclasses (or Pydantic models if validation is needed)
3. All result objects have `__str__` (agent summary), `to_dict()` (JSON-serialisable), and optionally `detail` (verbose form)
4. Success/failure is signalled by `status` fields or exceptions — not by `None` returns or magic sentinel values
5. Counts are always present on batch operations — even if zero

**Consistent field naming across all result types:**

| Field | Meaning |
|-------|---------|
| `status` | Outcome descriptor (string enum) |
| `count` / `processed` / `created` | Numeric outcomes |
| `summary` | One-line agent-readable summary |
| `id` / `block_id` | Identifier of created/modified entity |
| `cached` | Whether the result was served from cache |

**Anti-pattern:** Different methods returning different shapes for similar data:
```python
# Inconsistent — agents have to re-learn
learn()       → returns block_id directly as str
consolidate() → returns ConsolidateResult object
curate()      → returns (int, int, int) tuple
```

```python
# Consistent — agents learn the pattern once
learn()       → LearnResult(block_id, status, ...)
consolidate() → ConsolidateResult(processed, promoted, ...)
curate()      → CurateResult(archived, edges_pruned, reinforced, ...)
```

---

## Principle 10: Context Window Budget Control

**Why it matters:** An agent's context window is finite and expensive. Returning the maximum data by default wastes tokens on low-priority information. Agents should control how much context they receive.

**Rule:** All retrieval methods accept optional `max_tokens` or `top_k` parameters, and default to conservative budgets.

**Pattern:**
```python
async def recall(
    self,
    query: str,
    top_k: int = 5,              # Number of results
    max_tokens: int | None = None  # Hard token cap on rendered output
) -> RecallResult:
    ...
```

**For string representations:**
- Default `__str__` should be compact (one line)
- Provide `.detail` or `.verbose()` for expanded output
- Rendered context (e.g., frame text) should respect token budgets with greedy truncation, never silent truncation — indicate when content was cut

**Token budget pattern for rendered output:**
```python
def render_blocks(blocks: list[Block], max_tokens: int) -> tuple[str, bool]:
    """Returns (rendered_text, was_truncated)."""
    lines = []
    tokens_used = 0
    for block in blocks:
        block_tokens = estimate_tokens(block.content)
        if tokens_used + block_tokens > max_tokens:
            return "\n".join(lines) + "\n[...truncated]", True
        lines.append(f"[{len(lines)+1}] {block.content}")
        tokens_used += block_tokens
    return "\n".join(lines), False
```

---

## Principle 11: Operation History / Introspection

**Why it matters:** When an agent gets unexpected results, it needs to understand what happened. Traditional logging (stderr) isn't accessible in most tool-use scenarios. The history must be accessible through the API itself.

**Rule:** Provide an operation history accessible via the API, not just via logging.

**Pattern:**
```python
history = await system.history(last_n=10)
str(history)
# "Recent operations:
#  1. learn() → created block a1b2 (2 min ago)
#  2. learn() → duplicate_rejected (2 min ago)
#  3. consolidate() → processed 8, promoted 7 (1 min ago)
#  4. recall(query='preferences') → 5 blocks returned (30s ago)"
```

**What to record:**
- Operation name
- Outcome (status, counts)
- Timestamp (relative "2 min ago" is more agent-readable than epoch)
- Key identifiers (block IDs for learn, query for recall)

**What NOT to record:** Internal debug info, full block content, stack traces. The history is for operational visibility, not debugging.

**Generalisation:** Any stateful system needs a lightweight audit trail accessible through the API. This is especially important for systems where actions have delayed effects (e.g., "why didn't my recall return the block I learned 5 minutes ago?" → history shows consolidate never ran).

---

## Principle 12: MCP Server as First-Class Interface

**Why it matters:** The most direct way for an agent to use a library is as a tool, not as a library it writes code against. MCP (Model Context Protocol) is the emerging standard for agent-tool communication. Shipping an MCP server makes the library usable by any MCP-compatible agent without code generation.

**Rule:** For any library intended for agent use, provide an MCP server wrapper as a first-class deliverable.

**MCP tool design principles:**

1. **Tool names use `library_operation` format:** `elfmem_learn`, `elfmem_recall`, `elfmem_status`
2. **Descriptions are decision aids, not feature lists:**
   - Bad: "Stores a knowledge block in the SQLite database with deduplication"
   - Good: "Store something worth remembering. Use when the agent discovers information that should persist across sessions."
3. **Parameters are minimal:** Agents struggle with large parameter surfaces. Every optional parameter must earn its place.
4. **Returns are strings:** MCP tool results go directly into the agent's context. Return clean, formatted text — not JSON objects or raw data.
5. **Session management is automatic:** Expose explicit session tools but default to auto-session management. Agents shouldn't need to manage lifecycle manually.

**MCP tool description template:**
```json
{
  "name": "library_operation",
  "description": "One sentence: what it does. One sentence: when to use it.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "param_name": {
        "type": "string",
        "description": "What this parameter controls, with examples if helpful."
      }
    },
    "required": ["required_param"]
  }
}
```

**Minimum MCP tool surface for any memory/knowledge library:**
- `store` / `learn` — Add knowledge
- `retrieve` / `recall` — Query knowledge
- `status` — System health and state
- `guide` / `help` — Self-documentation

---

## Principle 13: Naming for Intent, Not Implementation

**Why it matters:** Method names are the first thing an agent reads. Names that reflect implementation details (`processInbox`, `computeEmbeddings`, `runGC`) tell the agent what the library does internally but not when to use it. Names that reflect intent (`consolidate`, `curate`, `learn`) tell the agent what it accomplishes.

**Rules:**
1. Use verbs for operations: `learn()`, `recall()`, `consolidate()`, `curate()`
2. Use nouns for state queries: `status()`, `history()`, `guide()`
3. Prefer domain vocabulary over technical vocabulary
4. Consistency across the API matters more than perfect individual naming

**Evaluation framework for a method name — ask:**
- Can an agent guess what this does from the name alone?
- Does the name suggest when to use it?
- Is it consistent with other method names in the library?
- Does it avoid implementation details (database operations, algorithm names)?

**When domain terms are obscure:** Keep the domain term (it carries precise meaning) but make it crystal clear in docstrings and `guide()`. Don't rename to a generic term that loses the library's conceptual integrity.

---

## Principle 14: Semantic Enum Values

**Why it matters:** Agents read string values and make branching decisions based on them. Opaque or abbreviated values force the agent to memorise a lookup table. Semantic values are self-documenting.

**Rules:**
1. Enum values should be readable as plain English
2. Status values should describe the outcome, not the internal state
3. Enumerate all possible values in the docstring and `guide()`

**Pattern:**
```python
# Less readable — what does "inbox" mean for an agent?
class BlockStatus(StrEnum):
    INBOX = "inbox"
    ACTIVE = "active"

# More readable
class BlockStatus(StrEnum):
    PENDING = "pending_consolidation"
    ACTIVE = "active"
    ARCHIVED = "archived"
```

**For return status fields — use outcome-descriptive values:**
```python
# Created → describes what happened
# duplicate_rejected → describes why nothing happened
# near_duplicate_superseded → describes what changed
status: str  # "created" | "duplicate_rejected" | "near_duplicate_superseded"
```

**Trade-off:** Verbose enum values increase `__str__` length. Use compact values in `__str__` representations, full values in structured data:
```python
str(result)     # "Stored a1b2. Inbox: 8/10."  (compact)
result.status   # "created"                     (semantic)
result.to_dict()# {"status": "created", ...}    (full)
```

---

## Principle 15: Configuration Ergonomics

**Why it matters:** An agent setting up a library may be generating configuration from a template or environment context. Configuration that requires deep library knowledge to use correctly creates a high barrier to entry.

**Rules:**
1. Zero configuration must work. A library called with no config should use sensible defaults and succeed.
2. Support multiple config formats in a single constructor: path, dict, object, or `None`.
3. Config keys should be readable without documentation: `llm.model` not `llm.mdl`; `memory.inbox_threshold` not `memory.ith`
4. Provide a `config_example()` or `config_template()` method that generates valid starter config

**Factory method pattern:**
```python
@classmethod
async def from_config(
    cls,
    store_path: str,
    config: str | dict | LibraryConfig | None = None
) -> "LibrarySystem":
    """
    Accepts:
      None        — use all defaults
      str         — path to YAML config file
      dict        — inline configuration
      LibraryConfig — pre-built config object
    """
```

**Environment variable support:** Support `LIBRARY_*` environment variables as a configuration layer. Document them in `guide("config")`. Agents running in environments where config files aren't practical can configure via env vars.

---

## Principle 16: Error Recovery over Error Reporting

**Why it matters:** An agent that hits an error goes into a recovery loop. The faster it can identify the fix, the less context it wastes. Errors that explain recovery are loops of 1–2 turns. Errors that just report problems can spin for many turns.

**The recovery hierarchy (in order of preference):**

1. **Auto-recover:** The library fixes the problem and continues. Best for common misuses (e.g., no session → start one automatically).
2. **Return gracefully:** Return an empty or partial result with a clear `__str__` explaining what happened and what to do.
3. **Raise with recovery:** Raise a typed exception with a `recovery` field containing the exact fix.
4. **Raise with diagnosis:** If the root cause is unclear, include diagnostic info alongside the recovery hint.

**Never do:**
- Raise a generic `Exception` or `RuntimeError` with no context
- Return `None` where a typed result is expected
- Silently swallow errors that the agent needs to know about
- Log errors to stderr only (agents can't read it)

**Auto-recovery pattern:**
```python
async def recall(self, query: str, ...) -> RecallResult:
    # If no session, start a transient one rather than raising
    if not self._session_active:
        await self._start_transient_session()
    # ... proceed
```

---

## Summary: The Agent-First Checklist

Use this checklist when designing or reviewing a library intended for agent consumption.

### Discovery
- [ ] All public types importable from package root
- [ ] `guide()` method returns runtime documentation
- [ ] Method docstrings follow USE WHEN / DON'T USE WHEN / COST / RETURNS / NEXT template
- [ ] MCP server (or tool schema) available for direct tool-use integration

### Return Values
- [ ] All operations return typed result objects (no raw dicts, lists, or None)
- [ ] All result types have agent-optimised `__str__` (action-leading, concise)
- [ ] All result types support `to_dict()` for structured access
- [ ] Empty/zero-result operations return empty results, not errors
- [ ] Batch operations include counts even when zero

### Status and Observability
- [ ] `status()` method returns system state with decision suggestion
- [ ] `history()` or equivalent accessible via API (not just logs)
- [ ] Retrieval methods return count of results, not just results

### Errors
- [ ] Custom exception hierarchy with `recovery` field
- [ ] Recovery messages contain the exact code or command to fix the problem
- [ ] No silent failures (all errors surface through exceptions or status fields)
- [ ] Transient failures are retriable; permanent failures explain why

### Ergonomics
- [ ] Tier 1 usage works with zero ceremony (no config, no session management)
- [ ] Idempotent operations: duplicate calls are safe
- [ ] All optional parameters have sensible defaults
- [ ] `max_tokens` or `top_k` controls available on all retrieval methods
- [ ] Consistent naming: verbs for operations, nouns for state queries

### Configuration
- [ ] Works with zero configuration
- [ ] `from_config()` accepts None, path, dict, and config object
- [ ] Environment variable support (`LIBRARY_*`)
- [ ] `config_example()` or inline example in `guide("config")`

---

## Applied Example: elfmem

The following shows how these principles apply to the elfmem library specifically.

| Principle | elfmem Implementation |
|-----------|----------------------|
| String-first returns | `__str__` on `LearnResult`, `ConsolidateResult`, `FrameResult`, `CurateResult` |
| Self-describing | `system.guide()` and `system.guide("learn")` |
| Structured docstrings | USE WHEN / COST / RETURNS / NEXT template on all public methods |
| System status | `system.status()` → `SystemStatus` with inbox count, session state, suggestion |
| Instructive errors | `SessionError`, `ConfigError` etc. with `recovery` field |
| Idempotency | Content-hash dedup on `learn()`, empty-safe `consolidate()` and `curate()` |
| Progressive disclosure | Tier 1: `learn()` + `recall()`; Tier 2: `session()` + `frame()`; Tier 3: full control |
| Minimal imports | `from elfmem import MemorySystem, LearnResult, ...` — all from root |
| Consistent returns | `LearnResult`, `ConsolidateResult`, `CurateResult`, `FrameResult` all follow same shape |
| Budget control | `max_tokens` on `frame()`, `top_k` on `recall()` |
| Operation history | `system.history(last_n=10)` → structured operation log |
| MCP server | `elfmem_learn`, `elfmem_recall`, `elfmem_frame`, `elfmem_status`, `elfmem_guide` |
| Intent-based naming | `learn`, `recall`, `consolidate`, `curate`, `frame`, `status`, `guide` |
| Semantic enums | `status: "created" | "duplicate_rejected" | "near_duplicate_superseded"` |
| Config ergonomics | `MemorySystem.from_config(db_path, config=None)` accepts None/path/dict/object |
| Error recovery | Auto-session option; empty inbox returns zero counts not exception |
