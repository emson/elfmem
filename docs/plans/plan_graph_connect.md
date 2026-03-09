# Plan: Graph Connect — Agent-Suggested Edges

**Status:** Ready for implementation
**Date:** 2026-03-09
**Scope:** Phase A (schema foundation + outcome fix) + Phase B1 (connect API)
**Research docs:** `docs/graph_improvement_research.md`, `docs/graph_connect_design.md`
**Out of scope:** Composite edge score (B2), Hebbian co-retrieval creation (C1), edge temporal decay (C2), LLM batch classification (D1). These are follow-on plans.

---

## 1. Goal

Give agents the ability to explicitly assert semantic relationships between knowledge blocks. The agent, as the consumer of the knowledge, has the highest-quality signal about why two pieces of information are related — no algorithm can infer this. Making the agent an active participant in graph construction is the defining improvement over pure similarity-based edges.

**Deliverables:**
1. Edge schema extended with `relation_type`, `origin`, `last_active_hours`, `note`
2. Outcome edge weighting fixed (`0.5` → `0.8`), `edge_reinforce_delta` wired up
3. `connect()` — create or update a semantic edge between two blocks
4. `disconnect()` — remove an edge (correct mistakes)
5. `connect_by_query()` — find blocks by content then connect (ergonomic alternative)
6. `connects()` — batch variant for end-of-session reflection
7. Session breadcrumbs on `MemorySystem` — eliminate the ID-friction problem
8. `ConnectResult`, `DisconnectResult`, `ConnectByQueryResult`, `ConnectsResult` result types
9. `ConnectError` exception hierarchy
10. `elfmem_connect` and `elfmem_disconnect` MCP tools
11. Guide entries for `connect` and `disconnect`
12. `OVERVIEW` table updated

---

## 2. Current State — What Exists

### Edge Schema (models.py)

```python
edges = Table(
    "edges",
    Column("from_id", Text, ForeignKey(..., CASCADE)),
    Column("to_id",   Text, ForeignKey(..., CASCADE)),
    Column("weight",  Float, nullable=False),
    Column("reinforcement_count", Integer, default=0),
    Column("created_at", Text, nullable=False),
    UniqueConstraint("from_id", "to_id"),
)
```

Missing: `relation_type`, `origin`, `last_active_hours`, `note`.

### Edge Creation — Three Existing Paths

**Path 1 — Consolidation (consolidate.py Phase 3):**
```
for each promoted block:
    cosine_sim(block_vec, all_active_vecs)
    filter sim >= 0.60, top 10
    insert_edge(from_id, to_id, weight=sim)   ← single signal, weight frozen at sim
```

**Path 2 — Outcome (outcome.py):**
```
if signal > threshold and len(blocks) > 1:
    outcome_weight = signal * 0.5             ← BACKWARDS: weaker than similarity edges
    upsert_outcome_edge(from_id, to_id, weight)
    # edge_reinforce_delta never applied       ← DEAD CODE
```

**Path 3 — Co-retrieval (graph.py):**
```
reinforce_co_retrieved_edges(block_ids):
    find pairs with EXISTING edges only
    increment reinforcement_count             ← never creates new edges
    weight never changes                      ← delta is dead code
```

### DB Query Functions (queries.py)

| Function | Signature | Gap |
|---|---|---|
| `insert_edge` | `(conn, *, from_id, to_id, weight)` | No relation_type, origin, note |
| `upsert_outcome_edge` | `(conn, *, from_id, to_id, weight) → bool` | No relation_type, origin, note |
| `reinforce_edges` | `(conn, edge_pairs)` | Only increments count; weight unchanged |
| `get_edges_for_block` | `(conn, block_id) → list[dict]` | Returns all cols; new cols would appear automatically |
| `get_neighbours` | `(conn, block_ids) → list[str]` | No change needed |
| `prune_weak_edges` | `(conn, threshold) → int` | No change needed now |
| `get_weighted_degree` | `(conn, block_ids) → dict` | No change needed now |

Missing: `get_edge()`, `upsert_agent_edge()`, `delete_edge()`, `update_edge_weight()`.

### Types (types.py)

```python
@dataclass
class Edge:
    from_id: str
    to_id: str
    weight: float

    @staticmethod
    def canonical(a: str, b: str) -> tuple[str, str]: ...
```

Missing: `relation_type`, `origin`, `note`. No `ConnectResult`, `DisconnectResult`, etc.

### Exceptions (exceptions.py)

Existing: `ElfmemError`, `SessionError`, `ConfigError`, `StorageError`, `FrameError`.
Missing: `ConnectError` and its subtypes.

### API (api.py)

No `connect()`, `disconnect()`, or `connect_by_query()` methods.
No `last_learned_block_id`, `last_recall_block_ids`, or `session_block_ids` breadcrumbs.

### Config (config.py — MemoryConfig)

```python
edge_reinforce_delta: float = 0.10   # defined but NEVER used anywhere
```

### Guide (guide.py)

`connect` and `disconnect` are not in `GUIDES` or the `OVERVIEW` table.

### MCP (mcp.py)

No `elfmem_connect` or `elfmem_disconnect` tools.

---

## 3. Control Flow — Before vs After

### Before: Edge Creation

```
Agent                   elfmem
  │                       │
  │── learn("X") ────────>│── inbox insert
  │                       │
  │── dream() ───────────>│── consolidate()
  │                       │     Phase 3:
  │                       │       cosine_sim(new_block, all_active)
  │                       │       filter >= 0.60, top 10
  │                       │       insert_edge(a, b, weight=sim)
  │                       │                 ↑ ONLY mechanism
  │                       │
  │── outcome(ids, 0.9) ──>│── upsert_outcome_edge(a, b, weight=0.45)
  │                       │     ↑ signal×0.5 — weaker than similarity edges
  │                       │     ↑ edge_reinforce_delta never applied
  │                       │
  │── recall("X") ────────>│── hybrid_retrieve()
  │<── [blocks] ──────────│     reinforce_co_retrieved_edges()
  │                       │       only count++ on EXISTING edges
  │                       │       never creates new edges
  │                       │
  │   (agent notices       │
  │    a relationship)     │
  │   [no mechanism to     │
  │    encode it]    ✗     │
```

### After: Edge Creation

```
Agent                   elfmem
  │                       │
  │── learn("X") ────────>│── inbox insert
  │<── LearnResult ───────│     .block_id
  │   system.last_learned_block_id = block_id   [NEW breadcrumb]
  │                       │
  │── dream() ───────────>│── consolidate()
  │                       │     Phase 3 (SAME — composite score in separate plan):
  │                       │       insert_edge(a, b, weight=sim,
  │                       │           relation_type='similar',    [NEW]
  │                       │           origin='similarity')        [NEW]
  │                       │
  │── outcome(ids, 0.9) ──>│── upsert_outcome_edge(a, b,
  │                       │       weight=0.72,           ← was 0.45 [FIXED]
  │                       │       relation_type='outcome',        [NEW]
  │                       │       origin='outcome')               [NEW]
  │                       │     apply edge_reinforce_delta        [FIXED]
  │                       │
  │── recall("X") ────────>│── hybrid_retrieve()
  │<── FrameResult ───────│     reinforce_co_retrieved_edges()
  │   system.last_recall_block_ids = [ids]      [NEW breadcrumb]
  │                       │
  │   (agent notices       │
  │    a relationship)     │
  │                       │
  │── connect(a, b,  ─────>│── validate (not same id, both active)
  │     relation=          │   check degree cap
  │     "supports")        │   displace weakest auto-edge if at cap
  │<── ConnectResult ─────│   upsert_agent_edge(a, b, 'supports',
  │     .action="created"  │       origin='agent', weight=0.75)
  │     .weight=0.75       │
  │                       │
  │── disconnect(a, b) ───>│── validate edge exists
  │<── DisconnectResult ──│   delete_edge(a, b)
  │     .action="removed"  │
  │                       │
  │── connect_by_query( ──>│── recall(source_query, top_k=1)
  │     "frame heuristics",│   recall(target_query, top_k=1)
  │     "constitutional",  │   if both above min_confidence:
  │     "supports")        │       connect(source_id, target_id, ...)
  │<── ConnectByQueryResult│   return with block content for verification
```

### Control Flow: `connect()` Internal

```
connect(source, target, relation, *, weight, note, if_exists)
│
├── 1. Validate source != target  →  raise SelfLoopError if same
│
├── 2. Fetch source block  →  raise BlockNotActiveError if not found/not active
├── 3. Fetch target block  →  raise BlockNotActiveError if not found/not active
│
├── 4. Canonical ordering: (min(src, tgt), max(src, tgt))
│
├── 5. Resolve weight: explicit > relation_type default
│
├── 6. Check existing edge
│   ├── if_exists = "error"   → raise ConnectError if edge exists
│   ├── if_exists = "skip"    → return existing edge state, action="skipped"
│   ├── if_exists = "update"  → update relation/note; keep/update weight
│   └── if_exists = "reinforce" (default)
│       ├── if edge exists: increment count + apply edge_reinforce_delta → action="reinforced"
│       └── if not: proceed to creation
│
├── 7. Check degree cap (only on creation path)
│   ├── count edges for source block
│   ├── count edges for target block
│   └── if either at cap:
│       ├── find lowest-priority displaceable edge
│       │   (priority: similar < co_occurs < [protected: elaborates, supports, contradicts, outcome])
│       ├── if displaceable found:
│       │   delete it, store as displaced_edge for result notification
│       └── if all protected:
│           store as pending → action="deferred", return early
│
└── 8. insert_agent_edge(from_id, to_id, weight, relation_type, origin='agent', note)
    return ConnectResult(action="created", ...)
```

### Control Flow: `disconnect()` Internal

```
disconnect(source, target, *, guard_relation, reason)
│
├── 1. Canonical ordering
├── 2. Fetch edge → if not found: return DisconnectResult(action="not_found")
├── 3. If guard_relation provided: check matches current relation_type
│   └── if mismatch: return DisconnectResult(action="guarded")
├── 4. delete_edge(from_id, to_id)
└── 5. _record_op("disconnect", result)
    return DisconnectResult(action="removed", removed_relation, removed_weight)
```

---

## 4. Files Changed

| File | Type | Nature of Change |
|------|------|-----------------|
| `src/elfmem/db/models.py` | Modify | Add 4 columns to edges table |
| `src/elfmem/db/queries.py` | Modify | Update edge functions + 4 new functions |
| `src/elfmem/memory/graph.py` | Modify | Add displacement logic |
| `src/elfmem/operations/outcome.py` | Modify | Fix weight scale + wire delta |
| `src/elfmem/operations/consolidate.py` | Modify | Pass relation_type + origin to insert_edge |
| `src/elfmem/operations/recall.py` | Modify | Pass last_active_hours to reinforce_edges |
| `src/elfmem/operations/connect.py` | **New** | Pure connect/disconnect/query functions |
| `src/elfmem/types.py` | Modify | New result types; update Edge; add SuggestedConnection |
| `src/elfmem/exceptions.py` | Modify | Add ConnectError hierarchy |
| `src/elfmem/api.py` | Modify | connect(), disconnect(), connect_by_query(), connects(); breadcrumbs |
| `src/elfmem/guide.py` | Modify | connect + disconnect guide entries; OVERVIEW update |
| `src/elfmem/mcp.py` | Modify | elfmem_connect + elfmem_disconnect tools |
| `src/elfmem/__init__.py` | Modify | Export new result types |
| `tests/test_connect.py` | **New** | Comprehensive connect test suite |
| `tests/test_storage.py` | Modify | Update edge schema assertions |
| `tests/test_lifecycle.py` | Modify | Update outcome edge weight assertions |
| `tests/test_mcp.py` | Modify | Add connect/disconnect tool tests |

---

## 5. Implementation Steps

Steps are ordered by dependency. Complete each step fully before proceeding.

---

### Step 1 — Schema Extension (`src/elfmem/db/models.py`)

Add four columns to the `edges` table. All backward-compatible with SQLite defaults.

**Exact change — add after `created_at` column:**
```python
edges = Table(
    "edges",
    metadata,
    Column("from_id", Text, ForeignKey("blocks.id", ondelete="CASCADE"), nullable=False),
    Column("to_id", Text, ForeignKey("blocks.id", ondelete="CASCADE"), nullable=False),
    Column("weight", Float, nullable=False),
    Column("reinforcement_count", Integer, nullable=False, default=0),
    Column("created_at", Text, nullable=False),
    # ── New columns ────────────────────────────────────────────────────────────
    Column("relation_type", Text, nullable=False, server_default="similar"),
    Column("origin", Text, nullable=False, server_default="similarity"),
    Column("last_active_hours", Float),          # None until first reinforcement
    Column("note", Text),                        # optional agent/LLM description
    UniqueConstraint("from_id", "to_id", name="uq_edge"),
)
```

**Important:** SQLite `server_default` means the default applies at the DB level for existing rows when columns are added via `ALTER TABLE`. For new schemas (tests), `default=` is the SQLAlchemy construct; for production DBs, migration runs `ALTER TABLE edges ADD COLUMN ...`.

**Migration note:** If a DB file already exists, the schema init will NOT re-run automatically. Document that running `elfmem migrate` (or re-creating the DB) is needed for existing installations. For tests, `StaticPool` creates fresh DBs each time — no migration needed.

**Tests to update:** `tests/test_storage.py` — any assertions on the `edges` table column count or column names.

---

### Step 2 — Update DB Query Functions (`src/elfmem/db/queries.py`)

Four existing functions need new parameters. Four new functions needed.

#### 2a. Update `insert_edge()` — add new columns

```python
async def insert_edge(
    conn: AsyncConnection,
    *,
    from_id: str,
    to_id: str,
    weight: float,
    relation_type: str = "similar",
    origin: str = "similarity",
    note: str | None = None,
) -> None:
    """Insert a similarity edge idempotently. from_id < to_id enforced by caller."""
    await conn.execute(
        insert(edges).prefix_with("OR IGNORE").values(
            from_id=from_id,
            to_id=to_id,
            weight=weight,
            reinforcement_count=0,
            created_at=_now_iso(),
            relation_type=relation_type,
            origin=origin,
            last_active_hours=None,
            note=note,
        )
    )
```

All callers use keyword args (`from_id=`, `to_id=`, `weight=`) so the new params are additive. No call sites break.

#### 2b. Update `upsert_outcome_edge()` — add new columns + return edge dict

```python
async def upsert_outcome_edge(
    conn: AsyncConnection,
    *,
    from_id: str,
    to_id: str,
    weight: float,
    note: str | None = None,
) -> bool:
    """Create an outcome edge or reinforce existing. Returns True if created."""
    if from_id == to_id:
        return False
    result = await conn.execute(
        insert(edges).prefix_with("OR IGNORE").values(
            from_id=from_id,
            to_id=to_id,
            weight=weight,
            reinforcement_count=0,
            created_at=_now_iso(),
            relation_type="outcome",
            origin="outcome",
            last_active_hours=None,
            note=note,
        )
    )
    if result.rowcount == 1:
        return True
    await conn.execute(
        update(edges)
        .where(and_(edges.c.from_id == from_id, edges.c.to_id == to_id))
        .values(reinforcement_count=edges.c.reinforcement_count + 1)
    )
    return False
```

#### 2c. Update `reinforce_edges()` — also update `last_active_hours`

```python
async def reinforce_edges(
    conn: AsyncConnection,
    edge_pairs: list[tuple[str, str]],
    current_active_hours: float | None = None,
) -> None:
    """Reinforce co-retrieval edges: increment reinforcement_count and update last_active_hours."""
    for from_id, to_id in edge_pairs:
        values: dict[str, Any] = {"reinforcement_count": edges.c.reinforcement_count + 1}
        if current_active_hours is not None:
            values["last_active_hours"] = current_active_hours
        await conn.execute(
            update(edges)
            .where(and_(edges.c.from_id == from_id, edges.c.to_id == to_id))
            .values(**values)
        )
```

#### 2d. Add `get_edge()` — fetch single edge by pair

```python
async def get_edge(
    conn: AsyncConnection,
    from_id: str,
    to_id: str,
) -> dict[str, Any] | None:
    """Fetch a single edge by canonical pair. Returns None if not found."""
    result = await conn.execute(
        select(edges).where(
            and_(edges.c.from_id == from_id, edges.c.to_id == to_id)
        )
    )
    row = result.mappings().first()
    return dict(row) if row else None
```

#### 2e. Add `insert_agent_edge()` — agent-asserted edge with full fields

```python
async def insert_agent_edge(
    conn: AsyncConnection,
    *,
    from_id: str,
    to_id: str,
    weight: float,
    relation_type: str,
    note: str | None,
    current_active_hours: float | None,
) -> None:
    """Insert an agent-asserted edge. from_id < to_id enforced by caller.

    Always inserts — caller must check existence and handle if_exists logic first.
    """
    await conn.execute(
        insert(edges).values(
            from_id=from_id,
            to_id=to_id,
            weight=weight,
            reinforcement_count=0,
            created_at=_now_iso(),
            relation_type=relation_type,
            origin="agent",
            last_active_hours=current_active_hours,
            note=note,
        )
    )
```

#### 2f. Add `update_edge()` — update relation/note/weight on existing edge

```python
async def update_edge(
    conn: AsyncConnection,
    *,
    from_id: str,
    to_id: str,
    relation_type: str | None = None,
    weight: float | None = None,
    note: str | None = None,
    reinforce_delta: float | None = None,
    current_active_hours: float | None = None,
) -> None:
    """Update fields on an existing edge. Only non-None fields are changed."""
    values: dict[str, Any] = {}
    if relation_type is not None:
        values["relation_type"] = relation_type
    if note is not None:
        values["note"] = note
    if weight is not None:
        values["weight"] = weight
    if reinforce_delta is not None:
        # Asymptotic cap: never exceed 1.0
        values["weight"] = func.min(edges.c.weight + reinforce_delta, 1.0)
    if current_active_hours is not None:
        values["last_active_hours"] = current_active_hours
    if not values:
        return
    await conn.execute(
        update(edges)
        .where(and_(edges.c.from_id == from_id, edges.c.to_id == to_id))
        .values(**values)
    )
```

**Note on `func.min`:** SQLAlchemy's `func.min` in an UPDATE context works as `MIN(weight + delta, 1.0)` in SQLite. Alternatively, use Python-side clamping after fetching the current value.

#### 2g. Add `delete_edge()` — remove an edge by pair

```python
async def delete_edge(
    conn: AsyncConnection,
    from_id: str,
    to_id: str,
) -> bool:
    """Delete an edge by canonical pair. Returns True if deleted, False if not found."""
    result = await conn.execute(
        delete(edges).where(
            and_(edges.c.from_id == from_id, edges.c.to_id == to_id)
        )
    )
    return (result.rowcount or 0) > 0
```

---

### Step 3 — Fix Outcome Edge Logic (`src/elfmem/operations/outcome.py`)

Two changes, both in the same block of outcome.py.

**Change 1 — Weight scale constant (line ~21):**
```python
# Before:
OUTCOME_EDGE_WEIGHT_SCALE: float = 0.5

# After:
OUTCOME_EDGE_WEIGHT_SCALE: float = 0.8
```

**Change 2 — Wire `edge_reinforce_delta` to update existing edge weights.**

After the `upsert_outcome_edge` loop, add weight-update pass for pre-existing edges:

```python
# After the upsert loop (approx lines 139-148):
if len(updated_ids) > 1 and signal > reinforce_threshold:
    await reinforce_blocks(conn, updated_ids, current_active_hours)
    outcome_weight = signal * OUTCOME_EDGE_WEIGHT_SCALE
    for from_id, to_id in _canonical_pairs(updated_ids):
        created = await upsert_outcome_edge(
            conn, from_id=from_id, to_id=to_id, weight=outcome_weight
        )
        if created:
            outcome_edges_created += 1
        else:
            edges_reinforced += 1
            # FIX: wire the edge_reinforce_delta that was previously dead code
            await update_edge(
                conn,
                from_id=from_id,
                to_id=to_id,
                reinforce_delta=signal * edge_reinforce_delta,
                current_active_hours=current_active_hours,
            )
```

**Import required:** `from elfmem.db.queries import update_edge` (new function from Step 2f).
**Parameter required:** `edge_reinforce_delta: float` must be threaded through from config. Check how `outcome()` receives config currently — if via `MemoryConfig`, access `config.memory.edge_reinforce_delta`.

**Tests to update:** `tests/test_lifecycle.py` and `tests/test_outcome.py` — any assertions that check outcome edge weight will need to update from `signal × 0.5` to `signal × 0.8`.

---

### Step 4 — Pass `last_active_hours` to `reinforce_edges` (`src/elfmem/operations/recall.py`)

The reinforcement side-effect (step 9 in recall.py) calls `reinforce_co_retrieved_edges()`. That function calls `reinforce_edges()`. Now that `reinforce_edges()` accepts `current_active_hours`, thread it through.

**In `recall.py` step 9 (lines 99-103):**
```python
# Before:
await reinforce_co_retrieved_edges(conn, returned_ids)

# After:
await reinforce_co_retrieved_edges(conn, returned_ids, current_active_hours)
```

**In `graph.py` `reinforce_co_retrieved_edges()`:**
```python
async def reinforce_co_retrieved_edges(
    conn: AsyncConnection,
    block_ids: list[str],
    current_active_hours: float | None = None,   # NEW: optional for backward compat
) -> int:
    ...
    if to_reinforce:
        await queries.reinforce_edges(conn, to_reinforce, current_active_hours)
    ...
```

**Pass-through is backward-compatible** — `current_active_hours` defaults to `None`, matching the existing `reinforce_edges` default.

Also update `consolidate.py` Phase 3: when `insert_edge` is called, pass `relation_type="similar"` and `origin="similarity"` (additive keyword args, no breaking change):

```python
# Before:
await insert_edge(conn, from_id=from_id, to_id=to_id, weight=sim)

# After:
await insert_edge(
    conn,
    from_id=from_id,
    to_id=to_id,
    weight=sim,
    relation_type="similar",
    origin="similarity",
)
```

---

### Step 5 — Add Exception Types (`src/elfmem/exceptions.py`)

Append to the existing hierarchy. All inherit from `ElfmemError` which already provides `.recovery`.

```python
class ConnectError(ElfmemError):
    """Raised when a connect() or disconnect() operation cannot complete."""


class SelfLoopError(ConnectError):
    """source and target are the same block ID."""

    def __init__(self, block_id: str) -> None:
        super().__init__(
            f"Cannot connect block '{block_id[:8]}' to itself.",
            recovery="source and target must be different block IDs.",
        )


class BlockNotActiveError(ConnectError):
    """A block ID was not found in active memory."""

    def __init__(self, block_id: str) -> None:
        super().__init__(
            f"Block '{block_id[:8]}…' not found in active memory.",
            recovery=(
                "Use system.recall() to find active block IDs. "
                "If the block was archived, re-learn its content to reactivate."
            ),
        )


class DegreeLimitError(ConnectError):
    """All existing edges for a block are protected; new edge cannot be placed."""

    def __init__(self, block_id: str, cap: int) -> None:
        super().__init__(
            f"Block '{block_id[:8]}…' has {cap} protected edges; no auto-edges to displace.",
            recovery=(
                "Run system.curate() to prune stale edges, or "
                "increase edge_degree_cap in config, or "
                "call system.disconnect() to manually remove an edge."
            ),
        )
```

---

### Step 6 — Add Result Types (`src/elfmem/types.py`)

Append after the existing result types. Follow the established pattern: `@dataclass`, `summary` property, `__str__` returns `self.summary`, `to_dict()` returns plain dict.

#### 6a. Update `Edge` dataclass — add new fields

```python
@dataclass
class Edge:
    from_id: str
    to_id: str
    weight: float
    relation_type: str = "similar"
    origin: str = "similarity"
    note: str | None = None

    @staticmethod
    def canonical(a: str, b: str) -> tuple[str, str]:
        return (min(a, b), max(a, b))
```

**Backward-compatible:** new fields have defaults.

#### 6b. Add `DisplacedEdge` (used inside ConnectResult)

```python
@dataclass
class DisplacedEdge:
    from_id: str
    to_id: str
    relation_type: str
    weight: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "from_id": self.from_id,
            "to_id": self.to_id,
            "relation_type": self.relation_type,
            "weight": self.weight,
        }
```

#### 6c. Add `ConnectResult`

```python
@dataclass
class ConnectResult:
    source_id: str
    target_id: str
    relation: str
    weight: float
    action: str              # "created" | "reinforced" | "updated" | "skipped" | "deferred"
    note: str | None = None
    displaced_edge: DisplacedEdge | None = None

    @property
    def summary(self) -> str:
        short_src = self.source_id[:8]
        short_tgt = self.target_id[:8]
        base = (
            f"{self.action.title()} {self.relation} edge: "
            f"{short_src}…→{short_tgt}… (weight={self.weight:.2f})."
        )
        if self.displaced_edge:
            base += (
                f" Displaced auto-{self.displaced_edge.relation_type} edge "
                f"(weight={self.displaced_edge.weight:.2f}) to fit degree cap."
            )
        return base

    def __str__(self) -> str:
        return self.summary

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "target_id": self.target_id,
            "relation": self.relation,
            "weight": self.weight,
            "action": self.action,
            "note": self.note,
            "displaced_edge": self.displaced_edge.to_dict() if self.displaced_edge else None,
        }
```

#### 6d. Add `DisconnectResult`

```python
@dataclass
class DisconnectResult:
    source_id: str
    target_id: str
    action: str              # "removed" | "not_found" | "guarded"
    removed_relation: str | None = None
    removed_weight: float | None = None

    @property
    def summary(self) -> str:
        short_src = self.source_id[:8]
        short_tgt = self.target_id[:8]
        if self.action == "removed":
            return (
                f"Removed {self.removed_relation} edge: "
                f"{short_src}…→{short_tgt}… (was weight={self.removed_weight:.2f})."
            )
        if self.action == "not_found":
            return f"No edge found between {short_src}… and {short_tgt}…"
        if self.action == "guarded":
            return f"Edge not removed — relation type did not match guard_relation."
        return f"Disconnect {self.action}."

    def __str__(self) -> str:
        return self.summary

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "target_id": self.target_id,
            "action": self.action,
            "removed_relation": self.removed_relation,
            "removed_weight": self.removed_weight,
        }
```

#### 6e. Add `ConnectByQueryResult`

```python
@dataclass
class ConnectByQueryResult:
    source_query: str
    target_query: str
    source_id: str | None
    target_id: str | None
    source_content: str | None    # full content of matched block — agent must verify
    target_content: str | None
    source_confidence: float
    target_confidence: float
    action: str                   # "connected" | "insufficient_confidence" | "dry_run_preview"
    connect_result: ConnectResult | None = None

    @property
    def summary(self) -> str:
        if self.action == "insufficient_confidence":
            return (
                f"connect_by_query: confidence too low "
                f"(source={self.source_confidence:.2f}, target={self.target_confidence:.2f}). "
                f"Use connect() with explicit IDs."
            )
        if self.action == "dry_run_preview":
            s = self.source_content[:60] + "…" if self.source_content else "?"
            t = self.target_content[:60] + "…" if self.target_content else "?"
            return f"dry_run: source='{s}' | target='{t}'. Call again without dry_run=True to connect."
        if self.connect_result:
            return self.connect_result.summary
        return f"connect_by_query: {self.action}."

    def __str__(self) -> str:
        return self.summary

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_query": self.source_query,
            "target_query": self.target_query,
            "source_id": self.source_id,
            "target_id": self.target_id,
            "source_content": self.source_content,
            "target_content": self.target_content,
            "source_confidence": self.source_confidence,
            "target_confidence": self.target_confidence,
            "action": self.action,
            "connect_result": self.connect_result.to_dict() if self.connect_result else None,
        }
```

#### 6f. Add `ConnectSpec` and `ConnectsResult` (for batch)

```python
@dataclass
class ConnectSpec:
    source: str
    target: str
    relation: str = "similar"
    weight: float | None = None
    note: str | None = None
    if_exists: str = "reinforce"


@dataclass
class ConnectsResult:
    results: list[ConnectResult]
    created: int
    reinforced: int
    updated: int
    skipped: int
    deferred: int
    errors: list[str]       # non-fatal per-edge error messages

    @property
    def summary(self) -> str:
        parts = [f"{self.created} created"]
        if self.reinforced:
            parts.append(f"{self.reinforced} reinforced")
        if self.updated:
            parts.append(f"{self.updated} updated")
        if self.deferred:
            parts.append(f"{self.deferred} deferred")
        if self.errors:
            parts.append(f"{len(self.errors)} errors")
        return f"Edges: {', '.join(parts)}."

    def __str__(self) -> str:
        return self.summary

    def to_dict(self) -> dict[str, Any]:
        return {
            "results": [r.to_dict() for r in self.results],
            "created": self.created,
            "reinforced": self.reinforced,
            "updated": self.updated,
            "skipped": self.skipped,
            "deferred": self.deferred,
            "errors": self.errors,
        }
```

---

### Step 7 — Degree Cap Displacement Logic (`src/elfmem/memory/graph.py`)

Add a helper that enforces the displacement priority order when a block is at cap.

**Displacement priority (lowest to highest protection):**
```
Evict first:   "similar"    (auto-created from geometry)
Evict second:  "co_occurs"  (Hebbian, statistical)
Never evict:   "elaborates", "supports", "contradicts", "outcome", "agent" origin
```

```python
# Constants at top of graph.py
_EVICTION_ORDER: list[str] = ["similar", "co_occurs"]
_PROTECTED_RELATIONS: frozenset[str] = frozenset(
    {"elaborates", "supports", "contradicts", "outcome"}
)


async def find_displaceable_edge(
    conn: AsyncConnection,
    block_id: str,
) -> dict[str, Any] | None:
    """Find the lowest-priority edge connected to block_id that can be displaced.

    Returns the edge row dict to displace, or None if all edges are protected.
    Priority: similar edges first, co_occurs second, then by weight ascending.
    Never displaces: elaborates, supports, contradicts, outcome, agent-origin edges.
    """
    block_edges = await queries.get_edges_for_block(conn, block_id)
    if not block_edges:
        return None

    # Filter to displaceable only: must be in _EVICTION_ORDER relation types
    # AND must NOT have origin='agent'
    candidates = [
        e for e in block_edges
        if e.get("relation_type", "similar") in _EVICTION_ORDER
        and e.get("origin", "similarity") != "agent"
    ]
    if not candidates:
        return None

    # Sort: first by eviction order index, then by weight ascending (weakest first)
    def eviction_key(e: dict[str, Any]) -> tuple[int, float]:
        relation = e.get("relation_type", "similar")
        order_idx = _EVICTION_ORDER.index(relation) if relation in _EVICTION_ORDER else 99
        return (order_idx, e["weight"])

    candidates.sort(key=eviction_key)
    return candidates[0]
```

---

### Step 8 — New Operations File (`src/elfmem/operations/connect.py`)

New file containing pure async functions for connect, disconnect, and connect-by-query. No side effects beyond DB writes. No LLM calls.

```python
"""connect() / disconnect() — agent-asserted edge operations.

These functions are the pure operation layer: they validate, enforce degree caps,
and write to the database. The api.py layer wraps them with session management,
breadcrumbs, and operation recording.
"""

from __future__ import annotations

from math import exp
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncConnection

from elfmem.db import queries
from elfmem.exceptions import BlockNotActiveError, ConnectError, DegreeLimitError, SelfLoopError
from elfmem.memory.graph import find_displaceable_edge
from elfmem.types import (
    ConnectByQueryResult,
    ConnectResult,
    ConnectsResult,
    ConnectSpec,
    DisconnectResult,
    DisplacedEdge,
    Edge,
)

# Default weights by relation type — semantic hierarchy
_RELATION_DEFAULT_WEIGHTS: dict[str, float] = {
    "similar":     0.65,
    "co_occurs":   0.55,
    "elaborates":  0.70,
    "supports":    0.75,
    "contradicts": 0.60,
    "outcome":     0.80,
}
_DEFAULT_WEIGHT_FALLBACK = 0.65   # for unknown custom types


async def do_connect(
    conn: AsyncConnection,
    *,
    source: str,
    target: str,
    relation: str,
    weight: float | None,
    note: str | None,
    if_exists: Literal["reinforce", "update", "skip", "error"],
    edge_degree_cap: int,
    edge_reinforce_delta: float,
    current_active_hours: float | None,
) -> ConnectResult:
    """Core connect logic. Called by api.connect()."""

    # 1. Self-loop guard
    if source == target:
        raise SelfLoopError(source)

    # 2. Validate both blocks exist and are active
    src_block = await queries.get_block(conn, source)
    if src_block is None or src_block.get("status") != "active":
        raise BlockNotActiveError(source)
    tgt_block = await queries.get_block(conn, target)
    if tgt_block is None or tgt_block.get("status") != "active":
        raise BlockNotActiveError(target)

    # 3. Canonical ordering
    from_id, to_id = Edge.canonical(source, target)

    # 4. Resolve weight
    resolved_weight = weight if weight is not None else _RELATION_DEFAULT_WEIGHTS.get(
        relation, _DEFAULT_WEIGHT_FALLBACK
    )
    resolved_weight = max(0.0, min(1.0, resolved_weight))  # clamp [0, 1]

    # 5. Check existing edge
    existing = await queries.get_edge(conn, from_id, to_id)

    if existing is not None:
        if if_exists == "error":
            raise ConnectError(
                f"Edge already exists between {from_id[:8]}… and {to_id[:8]}…",
                recovery="Use if_exists='reinforce' or 'update' to modify existing edges.",
            )
        if if_exists == "skip":
            return ConnectResult(
                source_id=source,
                target_id=target,
                relation=existing.get("relation_type", "similar"),
                weight=existing["weight"],
                action="skipped",
            )
        if if_exists == "update":
            await queries.update_edge(
                conn,
                from_id=from_id,
                to_id=to_id,
                relation_type=relation,
                weight=resolved_weight if weight is not None else None,
                note=note,
                current_active_hours=current_active_hours,
            )
            return ConnectResult(
                source_id=source,
                target_id=target,
                relation=relation,
                weight=resolved_weight,
                action="updated",
                note=note,
            )
        # if_exists == "reinforce" (default)
        await queries.update_edge(
            conn,
            from_id=from_id,
            to_id=to_id,
            reinforce_delta=edge_reinforce_delta,
            current_active_hours=current_active_hours,
        )
        return ConnectResult(
            source_id=source,
            target_id=target,
            relation=existing.get("relation_type", "similar"),
            weight=min(existing["weight"] + edge_reinforce_delta, 1.0),
            action="reinforced",
        )

    # 6. Degree cap check — only on new edge creation
    displaced_edge: DisplacedEdge | None = None
    for check_id in [from_id, to_id]:
        block_edges = await queries.get_edges_for_block(conn, check_id)
        if len(block_edges) >= edge_degree_cap:
            displaceable = await find_displaceable_edge(conn, check_id)
            if displaceable is None:
                raise DegreeLimitError(check_id, edge_degree_cap)
            # Displace it
            displaced_edge = DisplacedEdge(
                from_id=displaceable["from_id"],
                to_id=displaceable["to_id"],
                relation_type=displaceable.get("relation_type", "similar"),
                weight=displaceable["weight"],
            )
            await queries.delete_edge(conn, displaceable["from_id"], displaceable["to_id"])
            break  # only displace once even if both endpoints at cap

    # 7. Create edge
    await queries.insert_agent_edge(
        conn,
        from_id=from_id,
        to_id=to_id,
        weight=resolved_weight,
        relation_type=relation,
        note=note,
        current_active_hours=current_active_hours,
    )

    return ConnectResult(
        source_id=source,
        target_id=target,
        relation=relation,
        weight=resolved_weight,
        action="created",
        note=note,
        displaced_edge=displaced_edge,
    )


async def do_disconnect(
    conn: AsyncConnection,
    *,
    source: str,
    target: str,
    guard_relation: str | None,
) -> DisconnectResult:
    """Core disconnect logic. Called by api.disconnect()."""
    from_id, to_id = Edge.canonical(source, target)
    existing = await queries.get_edge(conn, from_id, to_id)

    if existing is None:
        return DisconnectResult(
            source_id=source,
            target_id=target,
            action="not_found",
        )

    if guard_relation is not None and existing.get("relation_type") != guard_relation:
        return DisconnectResult(
            source_id=source,
            target_id=target,
            action="guarded",
        )

    await queries.delete_edge(conn, from_id, to_id)
    return DisconnectResult(
        source_id=source,
        target_id=target,
        action="removed",
        removed_relation=existing.get("relation_type"),
        removed_weight=existing["weight"],
    )
```

---

### Step 9 — Session Breadcrumbs on `MemorySystem` (`src/elfmem/api.py`)

Add three tracking attributes and their public properties. Set them as side effects of `learn()`, `recall()`, and `frame()`.

**In `__init__` (add after existing instance vars):**
```python
# Session breadcrumbs — in-memory only, reset on begin_session()
self._last_learned_block_id: str | None = None
self._last_recall_block_ids: list[str] = []
self._session_block_ids: list[str] = []
```

**New properties:**
```python
@property
def last_learned_block_id(self) -> str | None:
    """Block ID from the most recent learn() or remember() call. None if not called."""
    return self._last_learned_block_id

@property
def last_recall_block_ids(self) -> list[str]:
    """Block IDs from the most recent recall() or frame() call. Empty list if not called."""
    return list(self._last_recall_block_ids)

@property
def session_block_ids(self) -> list[str]:
    """All block IDs touched (learned, recalled) during the current session."""
    return list(self._session_block_ids)
```

**Update `begin_session()`:** Reset breadcrumbs on new session.
```python
# In begin_session(), after existing reset logic:
self._last_learned_block_id = None
self._last_recall_block_ids = []
self._session_block_ids = []
```

**Update `learn()` result handling (after existing logic):**
```python
# After building LearnResult:
if result.status == "created":
    self._last_learned_block_id = result.block_id
    if result.block_id not in self._session_block_ids:
        self._session_block_ids.append(result.block_id)
return result
```

**Update `recall()` / `frame()` result handling:**
```python
# After building FrameResult (or list[ScoredBlock]):
recalled_ids = [b.id for b in result.blocks]
self._last_recall_block_ids = recalled_ids
for bid in recalled_ids:
    if bid not in self._session_block_ids:
        self._session_block_ids.append(bid)
return result
```

---

### Step 10 — Add `connect()`, `disconnect()`, `connect_by_query()`, `connects()` to `MemorySystem` (`src/elfmem/api.py`)

Import new operations and result types, then add four public methods.

**Imports to add:**
```python
from elfmem.operations.connect import do_connect, do_disconnect
from elfmem.types import (
    ConnectByQueryResult,
    ConnectResult,
    ConnectsResult,
    ConnectSpec,
    DisconnectResult,
)
```

**`connect()` method:**
```python
async def connect(
    self,
    source: str,
    target: str,
    relation: str = "similar",
    *,
    weight: float | None = None,
    note: str | None = None,
    if_exists: Literal["reinforce", "update", "skip", "error"] = "reinforce",
) -> ConnectResult:
    """Create or update a semantic edge between two knowledge blocks.

    USE WHEN: The agent observes a meaningful relationship between two blocks
    and wants to encode it explicitly. Best called immediately after recall(),
    learn(), or outcome() when block IDs are available in the result.

    DON'T USE WHEN: You don't have block IDs — use connect_by_query() instead.
    Don't connect blocks the agent hasn't read; unverified connections add noise.

    COST: Instant. No LLM calls. Pure database write.

    RETURNS: ConnectResult. action values:
      'created'    — new edge stored.
      'reinforced' — existing edge weight boosted; count incremented.
      'updated'    — relation type or note changed on existing edge.
      'skipped'    — edge exists and if_exists='skip'; no change.
    If a lower-priority auto-edge was displaced, displaced_edge is set in result.

    NEXT: No follow-up required. To undo, call disconnect(). Block IDs are
    available via system.last_recall_block_ids and system.last_learned_block_id.

    Args:
        source: Block ID. Available from recall(), learn(), and outcome() results.
        target: Block ID. Edges are undirected; source/target order does not matter.
        relation: Semantic type. Core types with scoring effects:
          'similar' (default), 'supports', 'contradicts', 'elaborates',
          'co_occurs', 'outcome'. Any other string stored as custom type.
        weight: Edge strength [0.0, 1.0]. None uses the relation-type default.
        note: Optional description of why this connection exists.
        if_exists: 'reinforce' (default) | 'update' | 'skip' | 'error'.

    Raises:
        SelfLoopError: source == target.
        BlockNotActiveError: block not found or not active.
        DegreeLimitError: degree cap full with only protected edges.
        ConnectError: if_exists='error' and edge already exists.
    """
    async with self._engine.begin() as conn:
        result = await do_connect(
            conn,
            source=source,
            target=target,
            relation=relation,
            weight=weight,
            note=note,
            if_exists=if_exists,
            edge_degree_cap=self._config.memory.edge_degree_cap,
            edge_reinforce_delta=self._config.memory.edge_reinforce_delta,
            current_active_hours=self._current_active_hours(),
        )
    self._record_op("connect", result)
    return result
```

**`disconnect()` method:**
```python
async def disconnect(
    self,
    source: str,
    target: str,
    *,
    guard_relation: str | None = None,
    reason: str | None = None,
) -> DisconnectResult:
    """Remove the edge between two knowledge blocks.

    USE WHEN: An agent-created edge was incorrect. Also use to override automatic
    edges that cause retrieval noise (textually similar but contextually unrelated).

    DON'T USE WHEN: The edge is correct but weak — decay and pruning remove it
    naturally. Only use disconnect() for deliberate correction.

    COST: Instant. No LLM calls.

    RETURNS: DisconnectResult. action values:
      'removed'   — edge deleted.
      'not_found' — no edge exists between the pair; no action taken.
      'guarded'   — edge exists but relation type did not match guard_relation.

    NEXT: No follow-up required. The edge is immediately gone from graph expansion.

    Args:
        source: Block ID.
        target: Block ID.
        guard_relation: Only remove if current relation type matches this value.
                        Safety check — prevents accidentally removing agent-typed
                        edges when intending to remove auto-created ones.
        reason: Optional reason stored in operation history.
    """
    async with self._engine.begin() as conn:
        result = await do_disconnect(
            conn,
            source=source,
            target=target,
            guard_relation=guard_relation,
        )
    self._record_op("disconnect", result)
    return result
```

**`connect_by_query()` method:**
```python
async def connect_by_query(
    self,
    source_query: str,
    target_query: str,
    relation: str = "similar",
    *,
    note: str | None = None,
    min_confidence: float = 0.70,
    if_exists: Literal["reinforce", "update", "skip", "error"] = "reinforce",
    dry_run: bool = False,
) -> ConnectByQueryResult:
    """Find two blocks by semantic query and connect them.

    USE WHEN: The agent has a clear conceptual relationship in mind but doesn't
    have block IDs available. Internally runs two recall(top_k=1) calls.

    DON'T USE WHEN: You have block IDs — use connect() for precision.
    Vague queries may match the wrong blocks.

    COST: Two embedding calls (fast). No LLM calls.

    RETURNS: ConnectByQueryResult. ALWAYS verify source_content and target_content
    to confirm correct blocks were matched. Use dry_run=True to preview without
    writing.

    Args:
        source_query: Natural language description of the source block.
        target_query: Natural language description of the target block.
        relation: Semantic type — same as connect().
        note: Optional description of the relationship.
        min_confidence: Minimum score for a match to be accepted. Default: 0.70.
        if_exists: Same as connect().
        dry_run: Preview matches without writing the edge.
    """
    # Recall top-1 for each query
    src_results = await self.recall(source_query, top_k=1)
    tgt_results = await self.recall(target_query, top_k=1)

    src_block = src_results.blocks[0] if src_results.blocks else None
    tgt_block = tgt_results.blocks[0] if tgt_results.blocks else None
    src_conf = src_block.score if src_block else 0.0
    tgt_conf = tgt_block.score if tgt_block else 0.0

    if src_block is None or tgt_block is None or src_conf < min_confidence or tgt_conf < min_confidence:
        return ConnectByQueryResult(
            source_query=source_query,
            target_query=target_query,
            source_id=src_block.id if src_block else None,
            target_id=tgt_block.id if tgt_block else None,
            source_content=src_block.content if src_block else None,
            target_content=tgt_block.content if tgt_block else None,
            source_confidence=src_conf,
            target_confidence=tgt_conf,
            action="insufficient_confidence",
        )

    if dry_run:
        return ConnectByQueryResult(
            source_query=source_query,
            target_query=target_query,
            source_id=src_block.id,
            target_id=tgt_block.id,
            source_content=src_block.content,
            target_content=tgt_block.content,
            source_confidence=src_conf,
            target_confidence=tgt_conf,
            action="dry_run_preview",
        )

    connect_result = await self.connect(
        src_block.id, tgt_block.id,
        relation=relation, note=note, if_exists=if_exists,
    )
    return ConnectByQueryResult(
        source_query=source_query,
        target_query=target_query,
        source_id=src_block.id,
        target_id=tgt_block.id,
        source_content=src_block.content,
        target_content=tgt_block.content,
        source_confidence=src_conf,
        target_confidence=tgt_conf,
        action="connected",
        connect_result=connect_result,
    )
```

**`connects()` batch method:**
```python
async def connects(
    self,
    edges: list[ConnectSpec],
) -> ConnectsResult:
    """Create or update multiple edges in a single operation.

    USE WHEN: End-of-session reflection — the agent has identified several
    relationships to encode at once.

    COST: Instant per edge. One DB call per spec. No LLM calls.

    RETURNS: ConnectsResult with per-edge results and aggregate counts.
    Per-edge errors are collected (not raised) so a single failure does not
    abort the batch.

    Args:
        edges: List of ConnectSpec(source, target, relation, weight, note, if_exists).
    """
    results: list[ConnectResult] = []
    counts = {"created": 0, "reinforced": 0, "updated": 0, "skipped": 0, "deferred": 0}
    errors: list[str] = []

    for spec in edges:
        try:
            r = await self.connect(
                spec.source,
                spec.target,
                spec.relation,
                weight=spec.weight,
                note=spec.note,
                if_exists=spec.if_exists,
            )
            results.append(r)
            counts[r.action] = counts.get(r.action, 0) + 1
        except Exception as exc:
            errors.append(f"{spec.source[:8]}→{spec.target[:8]}: {exc}")

    return ConnectsResult(
        results=results,
        created=counts["created"],
        reinforced=counts["reinforced"],
        updated=counts["updated"],
        skipped=counts["skipped"],
        deferred=counts.get("deferred", 0),
        errors=errors,
    )
```

---

### Step 11 — Guide Entries (`src/elfmem/guide.py`)

Add two new entries to `GUIDES` and update `OVERVIEW`.

**Add to GUIDES dict:**
```python
"connect": AgentGuide(
    name="connect",
    what="Create or strengthen a semantic edge between two knowledge blocks.",
    when=(
        "The agent observes a relationship between two recalled blocks that the system "
        "has not captured, or has captured with the wrong semantic type. "
        "Best called immediately after recall() or learn() when block IDs are available."
    ),
    when_not=(
        "You don't have block IDs — use connect_by_query() instead. "
        "Don't connect blocks the agent hasn't read; unverified connections add noise. "
        "Don't call for blocks that will decay soon — weak connections fade on their own."
    ),
    cost="Instant. No LLM calls. Pure database write.",
    returns=(
        "ConnectResult. action: 'created' (new edge), 'reinforced' (existing edge boosted), "
        "'updated' (relation/note changed), 'skipped' (edge exists, if_exists=skip). "
        "If a lower-priority auto-edge was displaced, displaced_edge is set in result."
    ),
    next=(
        "No follow-up required. To undo, call disconnect(). "
        "Block IDs are in system.last_recall_block_ids and system.last_learned_block_id."
    ),
    example=(
        "# After recall — agent notices an unlabelled relationship\n"
        "results = await system.recall('frame selection heuristics')\n"
        "await system.connect(\n"
        "    source=results.blocks[0].id,\n"
        "    target=results.blocks[1].id,\n"
        "    relation='supports',\n"
        "    note='B gives the mechanism behind A'\n"
        ")\n"
        "# Using breadcrumb shortcut\n"
        "await system.learn('New insight about X')\n"
        "await system.recall('related concept Y')\n"
        "await system.connect(\n"
        "    source=system.last_learned_block_id,\n"
        "    target=system.last_recall_block_ids[0],\n"
        "    relation='elaborates'\n"
        ")"
    ),
),
"disconnect": AgentGuide(
    name="disconnect",
    what="Remove the edge between two knowledge blocks.",
    when=(
        "An agent-created edge was incorrect and should not persist. "
        "Also use to override automatic edges that cause retrieval noise "
        "(e.g., two blocks that are textually similar but contextually unrelated)."
    ),
    when_not=(
        "The edge is correct but weak — decay and pruning remove it naturally over time. "
        "Only use disconnect() for deliberate correction of wrong connections."
    ),
    cost="Instant. No LLM calls.",
    returns=(
        "DisconnectResult. action: 'removed' (edge deleted), "
        "'not_found' (no edge exists between the pair), "
        "'guarded' (edge exists but relation did not match guard_relation)."
    ),
    next="No follow-up required. Edge is immediately gone from graph expansion.",
    example=(
        "# Remove a wrong connection\n"
        "result = await system.disconnect(source_id, target_id)\n"
        "print(result)  # Removed similar edge: abc12345…→def67890… (was weight=0.63).\n"
        "\n"
        "# Safe removal with guard (only remove if it's a 'similar' auto-edge)\n"
        "result = await system.disconnect(\n"
        "    source_id, target_id,\n"
        "    guard_relation='similar'\n"
        ")\n"
        "# → 'guarded' if the edge is actually 'supports' (won't remove)"
    ),
),
```

**Update OVERVIEW table:** Add `connect` and `disconnect` rows:
```python
"  connect(src, tgt, ...)  Instant      Assert a semantic edge between two blocks",
"  disconnect(src, tgt)    Instant      Remove a wrong or unwanted edge",
```

---

### Step 12 — MCP Tools (`src/elfmem/mcp.py`)

```python
@mcp.tool()
async def elfmem_connect(
    source: str,
    target: str,
    relation: str = "similar",
    note: str | None = None,
    if_exists: str = "reinforce",
) -> dict[str, Any]:
    """Create or strengthen a semantic edge between two knowledge blocks.

    Use block IDs from elfmem_recall or elfmem_remember responses.
    relation: 'similar' | 'supports' | 'contradicts' | 'elaborates' | 'co_occurs' | 'outcome' | <custom>
    if_exists: 'reinforce' (default) | 'update' | 'skip' | 'error'

    Block IDs are available from previous elfmem_recall/elfmem_remember calls.
    Also accessible via system.last_recall_block_ids and system.last_learned_block_id.
    """
    result = await _mem().connect(
        source, target, relation=relation, note=note, if_exists=if_exists
    )
    return result.to_dict()


@mcp.tool()
async def elfmem_disconnect(
    source: str,
    target: str,
    guard_relation: str | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    """Remove the edge between two blocks. Use to correct wrong connections.

    guard_relation: Only remove if current relation type matches this value (safety check).
    Returns action: 'removed' | 'not_found' | 'guarded'.
    """
    result = await _mem().disconnect(
        source, target, guard_relation=guard_relation, reason=reason
    )
    return result.to_dict()
```

---

### Step 13 — Export New Types (`src/elfmem/__init__.py`)

Add to the existing public exports:

```python
from elfmem.types import (
    # ... existing exports ...
    ConnectResult,
    ConnectByQueryResult,
    ConnectsResult,
    ConnectSpec,
    DisconnectResult,
    DisplacedEdge,
)
from elfmem.exceptions import (
    # ... existing exports ...
    ConnectError,
    SelfLoopError,
    BlockNotActiveError,
    DegreeLimitError,
)
```

---

## 6. Test Strategy

### New file: `tests/test_connect.py`

Use the existing `conftest.py` fixture pattern. All tests use `MockLLMService` and `MockEmbeddingService` — no real API calls.

**Test groups:**

#### Group 1 — Schema (can also go in test_storage.py)
```
test_edges_table_has_relation_type_column
test_edges_table_has_origin_column
test_edges_table_has_last_active_hours_column
test_edges_table_has_note_column
test_insert_edge_defaults_to_similar_origin_similarity
test_upsert_outcome_edge_sets_relation_outcome_origin_outcome
```

#### Group 2 — Outcome edge fix
```
test_outcome_edge_weight_is_signal_times_0_8
test_outcome_edge_reinforce_delta_applied_on_second_outcome
test_outcome_edge_weight_does_not_exceed_1_0
```

#### Group 3 — `connect()` happy paths
```
test_connect_creates_edge_between_two_active_blocks
test_connect_returns_connect_result_with_action_created
test_connect_default_relation_is_similar
test_connect_supports_relation_uses_weight_0_75
test_connect_custom_relation_uses_fallback_weight
test_connect_explicit_weight_overrides_default
test_connect_note_stored_on_edge
test_connect_sets_origin_to_agent
```

#### Group 4 — `connect()` if_exists behaviour
```
test_connect_reinforce_increments_count_on_existing_edge
test_connect_reinforce_boosts_weight_by_delta
test_connect_update_changes_relation_type
test_connect_update_changes_note
test_connect_skip_returns_existing_edge_unchanged
test_connect_error_raises_on_existing_edge
```

#### Group 5 — `connect()` validation and errors
```
test_connect_self_loop_raises_self_loop_error
test_connect_self_loop_error_has_recovery
test_connect_unknown_source_raises_block_not_active_error
test_connect_archived_block_raises_block_not_active_error
test_connect_block_not_active_error_has_recovery
test_connect_weight_clamped_to_0_1_range
```

#### Group 6 — Degree cap displacement
```
test_connect_displaces_weakest_similar_edge_when_at_cap
test_connect_displaced_edge_present_in_result
test_connect_never_displaces_supports_edge
test_connect_never_displaces_outcome_edge
test_connect_all_protected_raises_degree_limit_error
test_connect_degree_limit_error_has_recovery
```

#### Group 7 — `disconnect()` behaviour
```
test_disconnect_removes_existing_edge
test_disconnect_returns_removed_action
test_disconnect_returns_not_found_for_missing_edge
test_disconnect_guard_relation_prevents_wrong_type_removal
test_disconnect_guard_relation_removes_matching_type
test_disconnect_removed_edge_not_in_graph_expansion
```

#### Group 8 — `connect_by_query()`
```
test_connect_by_query_connects_matching_blocks
test_connect_by_query_returns_block_content_for_verification
test_connect_by_query_insufficient_confidence_returns_no_edge
test_connect_by_query_dry_run_preview_no_edge_written
test_connect_by_query_dry_run_returns_matched_content
```

#### Group 9 — `connects()` batch
```
test_connects_creates_multiple_edges
test_connects_collects_errors_without_raising
test_connects_returns_aggregate_counts
test_connects_partial_failure_does_not_abort_batch
```

#### Group 10 — Session breadcrumbs
```
test_last_learned_block_id_set_after_learn
test_last_recall_block_ids_set_after_recall
test_session_block_ids_accumulates_across_operations
test_breadcrumbs_reset_on_begin_session
```

#### Group 11 — MCP tools
```
test_elfmem_connect_tool_returns_dict
test_elfmem_disconnect_tool_returns_dict
test_elfmem_connect_tool_propagates_connect_error_as_exception
```

### Updates to existing tests

**`tests/test_storage.py`:**
- Update any assertion on edges table column list or `edges.c.*` attribute access
- Verify all four new columns exist with correct defaults

**`tests/test_lifecycle.py`:**
- Update assertions that check outcome edge weight: `signal * 0.5` → `signal * 0.8`
- Add assertion: outcome edge weight increases on second outcome call (delta applied)

**`tests/test_outcome.py`:**
- Update weight expectations from `signal × 0.5` to `signal × 0.8`
- Add test: `edge_reinforce_delta` increases weight on repeated positive outcomes

**`tests/test_mcp.py`:**
- Add tests for `elfmem_connect` and `elfmem_disconnect` tool registration and basic invocation

---

## 7. Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|-----------|
| Existing DB files lack new columns | Medium | High | Document migration path. Tests use fresh StaticPool DBs — unaffected. Production: ALTER TABLE or delete and re-create. |
| `insert_edge()` callers break due to new params | Low | High | All new params have defaults. All callers use keyword args. Zero breaking change at call sites. |
| `reinforce_edges()` signature change breaks graph.py | Low | Medium | New `current_active_hours` param defaults to None. Backward-compatible. |
| Outcome edge weight change breaks test assertions | Medium | Low | Known impact — update `test_lifecycle.py` and `test_outcome.py` as part of this plan. |
| `do_connect()` imports from `graph.py` creating circular dependency | Low | Medium | Check import graph. `graph.py` imports from `queries.py` only. `operations/connect.py` imports from `graph.py` and `queries.py`. No cycles. |
| `func.min()` in SQLAlchemy UPDATE not supported in SQLite | Low | Medium | Use Python-side clamp: fetch current weight, compute new weight, update. Slightly more verbose but safe. |
| `connect_by_query()` matches wrong block, agent doesn't verify | Medium | Medium | Always return block content in result. Document verification requirement prominently in guide. |
| `connects()` batch leaves partial state on failure | Low | Low | Per-edge errors collected, not raised. Each edge is atomic. Partial success is acceptable and reported. |
| `DegreeLimitError` confuses agents — they don't know which blocks to disconnect | Low | Low | Error message includes block ID prefix and lists recovery options (curate, increase cap, disconnect). |
| `update_edge()` with `func.min()` — SQLAlchemy server-side expression | Low | Medium | Use `case()` expression or Python-side fetch-and-update pattern to avoid SQLAlchemy expression complexity. |

---

## 8. Implementation Order Summary

```
Step 1  →  db/models.py          (schema — foundation for all)
Step 2  →  db/queries.py         (DB functions — foundation for operations)
Step 3  →  operations/outcome.py (fix — safe, isolated)
Step 4  →  operations/recall.py  (pass-through — minimal change)
           operations/consolidate.py (pass relation_type/origin)
Step 5  →  exceptions.py         (new types — no dependencies)
Step 6  →  types.py              (new types — depends on nothing new)
Step 7  →  memory/graph.py       (displacement — depends on queries)
Step 8  →  operations/connect.py (new file — depends on queries, graph, types, exceptions)
Step 9  →  api.py (breadcrumbs)  (depends on types)
Step 10 →  api.py (new methods)  (depends on operations/connect, types, exceptions)
Step 11 →  guide.py              (documentation — no code dependencies)
Step 12 →  mcp.py                (tools — depends on api)
Step 13 →  __init__.py           (exports — last, depends on all)

Tests   →  run after Step 13; iterate on failures
```

**Estimated test count:** ~55 new tests in `test_connect.py` + updates to ~10 tests across existing files.

---

## 9. Definition of Done

- [ ] All four new edge columns present in schema with correct SQLite defaults
- [ ] `insert_edge()` and `upsert_outcome_edge()` write `relation_type` and `origin`
- [ ] Outcome edges created at `signal × 0.8` (verified by test)
- [ ] Repeated `outcome()` on same blocks increases edge weight via `edge_reinforce_delta`
- [ ] `connect()` creates an edge with `origin="agent"` and correct `relation_type`
- [ ] `connect()` `if_exists` all four modes work correctly
- [ ] `connect()` never displaces `supports`, `elaborates`, `contradicts`, `outcome` edges
- [ ] `connect()` `SelfLoopError`, `BlockNotActiveError`, `DegreeLimitError` all have `.recovery`
- [ ] `disconnect()` removes edge; `"not_found"` and `"guarded"` behave correctly
- [ ] `connect_by_query()` returns block content; `dry_run=True` writes nothing
- [ ] `connects()` batch collects errors without aborting
- [ ] `last_learned_block_id`, `last_recall_block_ids`, `session_block_ids` set correctly
- [ ] Breadcrumbs reset on `begin_session()`
- [ ] `elfmem_connect` and `elfmem_disconnect` MCP tools registered and functional
- [ ] `connect` and `disconnect` entries in `GUIDES` dict with all required fields
- [ ] OVERVIEW table includes connect and disconnect rows
- [ ] All new types exported from `elfmem.__init__`
- [ ] All existing tests still pass (no regressions)
- [ ] `tests/test_connect.py` fully green with all test groups covered
