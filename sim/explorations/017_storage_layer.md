# Title: Storage Layer — Blocks, Frames, and Edges

## Status: complete

## Question

The system has three kinds of stored artefacts:
- **Blocks** — markdown files (primary record) + operational data (database)
- **Edges and contradictions** — operational graph data (database)
- **Frame definitions** — named retrieval configurations (database)

How should all of this be stored to (a) enable fast recall(), (b) scale from
50 to 50,000 blocks without rearchitecting, and (c) remain simple enough to
run without infrastructure?

---

## Start With the Query Patterns

Storage design follows access patterns. Before choosing schemas and indexes,
trace exactly what recall() needs to read at each call.

### SELF frame (no query)

```
1. Filter:  all blocks where tag LIKE 'self/%' AND status = 'active'
2. Score:   per block — confidence, reinforcement_count, hours_since_reinforcement,
            decay_lambda, plus centrality (derived from edges)
3. Dedupe:  check contradictions table for any pairs in candidate set
4. Return:  top-K by composite score
5. Write:   UPDATE reinforcement_count, hours_since_reinforcement for returned blocks
```

No embeddings needed. Centrality requires an edge lookup per block.

### ATTENTION frame (with query)

```
1. Embed:   query → query_vector (1 external LLM call, not a DB concern)
2. Load:    all active block embeddings from DB
3. Rank:    cosine_similarity(query_vector, each block_embedding) in memory
4. Score:   combine similarity with other 4 scoring components
5. Dedupe:  contradiction check
6. Return:  top-K
7. Write:   reinforce returned blocks + co-retrieved edges
```

The expensive step is step 2-3 — loading and comparing embeddings. This is the
only operation that gets slower as block count grows.

### TASK frame (with or without query)

```
1. Guarantee: all blocks tagged self/goal → always included
2. Filter:    all blocks for scored portion
3. Score:     if query provided, include similarity; else similarity = 0
4. Merge:     guaranteed blocks + top scored blocks, token budget enforced
5. Dedupe:    contradiction check
6. Return + Write: same as above
```

### curate() pass

```
1. Read:    all active blocks + their operational data
2. Compute: decay_weight per block (e^(-λ × hours_since_reinforcement))
3. Prune:   DELETE blocks where decay_weight < 0.05
4. Decay edges: UPDATE edge weights, DELETE edges where weight < 0.10
5. Promote: top-scoring blocks get reinforcement boost
6. Promote: candidate self-tags meeting promotion criteria → confirmed
```

curate() is a full-table scan. At 50 blocks it's trivial. At 5000 it takes
perhaps 50ms. At 50,000 it needs batching. Not a concern for Phase 1.

### What the query patterns demand

| Requirement | Implication |
|-------------|-------------|
| Fast tag filtering | Index on `block_tags.tag` |
| Fast status filtering | Index on `blocks.status` |
| Fast centrality lookup | Index on `edges.from_id` and `edges.to_id` |
| Fast contradiction check for a set of IDs | Index on `contradictions.block_a_id` and `.block_b_id` |
| Fast similarity search | Embeddings co-located with blocks; in-memory cosine at Phase 1 scale |
| Atomic reinforcement writes | Transaction grouping |
| Non-blocking recall() during consolidate() writes | WAL mode |

---

## The Database: SQLite

**Single file, zero infrastructure.** SQLite handles this workload at any
realistic agent memory scale:

- 50 blocks: fits entirely in memory, sub-millisecond queries
- 5,000 blocks: 30 MB embedding storage, ~10ms similarity scan
- 50,000 blocks: needs batched operations but schema unchanged

No Postgres, no Redis, no separate vector database. The only database is
`amgs.db` — an SQLite file in the memory directory. SQLite is single-writer;
WAL mode enables concurrent reads during writes.

```sql
PRAGMA journal_mode = WAL;     -- concurrent reads during consolidate() writes
PRAGMA synchronous = NORMAL;   -- safe with WAL; faster than FULL
PRAGMA foreign_keys = ON;      -- enforce ON DELETE CASCADE on edges/tags
PRAGMA cache_size = -32000;    -- 32MB page cache (negative = KB)
```

---

## Complete Schema

### `blocks` — operational state of each memory block

```sql
CREATE TABLE blocks (
    id                        TEXT    PRIMARY KEY,
    file_path                 TEXT    NOT NULL UNIQUE,  -- relative path to .md file
    category                  TEXT    NOT NULL,
    source                    TEXT    NOT NULL,         -- 'api' | 'cli' | 'sdk'
    created_at                TEXT    NOT NULL,         -- ISO 8601
    status                    TEXT    NOT NULL DEFAULT 'active',
                                      -- 'active' | 'superseded' | 'forgotten'
    confidence                REAL    NOT NULL DEFAULT 0.5,
    reinforcement_count       INTEGER NOT NULL DEFAULT 0,
    decay_lambda              REAL    NOT NULL DEFAULT 0.01,
    hours_since_reinforcement REAL    NOT NULL DEFAULT 0.0,
    self_alignment            REAL,                     -- NULL until computed
    embedding                 BLOB,                     -- NULL until consolidate()
    embedding_model           TEXT,                     -- model id (for invalidation)
    token_count               INTEGER,
    last_reinforced_session   TEXT                      -- session id of last reinforce
);
```

**Why `hours_since_reinforcement` as a stored value, not a computed one:**
Session-aware decay (exploration 005) means this is updated in bulk at session
start, not computed from wall-clock time. The stored value is always valid at
query time — the update happens once when a new session opens.

**Why `embedding` lives in `blocks`:**
At Phase 1 scale (50 blocks), loading all embeddings for similarity search means
loading 50 rows — trivially fast. At Phase 2 scale (>1000 blocks), split into a
separate `block_embeddings` table to keep the main `blocks` table slim for
non-similarity queries.

### `block_tags` — normalised tag storage

```sql
CREATE TABLE block_tags (
    block_id    TEXT NOT NULL,
    tag         TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'confirmed',  -- 'confirmed' | 'candidate'
    assigned_by TEXT NOT NULL,                      -- 'explicit' | 'inferred' | 'promoted'
    assigned_at TEXT NOT NULL,
    PRIMARY KEY (block_id, tag),
    FOREIGN KEY (block_id) REFERENCES blocks(id) ON DELETE CASCADE
);
```

Tags are NOT stored as a JSON array in the `blocks` row. A separate table allows:
- Indexed filtering: `WHERE tag LIKE 'self/%'` — fast and index-backed
- Status filtering: only confirmed tags included in SELF frame scoring
- Cascade delete: tags vanish automatically when their block is pruned
- Clean promotion: `UPDATE block_tags SET status = 'confirmed'` — atomic

### `inbox` — blocks received but not yet consolidated

```sql
CREATE TABLE inbox (
    id          TEXT    PRIMARY KEY,  -- content hash, same formula as block IDs
    content     TEXT    NOT NULL,     -- raw markdown (title + body)
    received_at TEXT    NOT NULL,
    source      TEXT    NOT NULL
);
```

Inbox is deliberately minimal — no embeddings, no scoring data. Content exists
here in its rawest form. At consolidation, the block is moved to MEMORY and the
inbox row is deleted.

The content hash as inbox ID enables O(1) dedup within the inbox: if the same
content is submitted twice before consolidation, the second INSERT fails
silently on PRIMARY KEY conflict.

### `edges` — associative relationships between blocks

```sql
CREATE TABLE edges (
    from_id                  TEXT    NOT NULL,
    to_id                    TEXT    NOT NULL,
    weight                   REAL    NOT NULL,
    created_at               TEXT    NOT NULL,
    reinforcement_count      INTEGER NOT NULL DEFAULT 0,
    hours_since_co_retrieval REAL    NOT NULL DEFAULT 0.0,
    PRIMARY KEY (from_id, to_id),
    FOREIGN KEY (from_id) REFERENCES blocks(id) ON DELETE CASCADE,
    FOREIGN KEY (to_id)   REFERENCES blocks(id) ON DELETE CASCADE
);
```

Canonical ordering (`from_id = min(id_a, id_b)`) keeps every undirected edge
stored exactly once. `ON DELETE CASCADE` means no cleanup queries are needed
when a block is pruned — the edge simply disappears.

### `contradictions` — opposing relationships (separate lifecycle)

```sql
CREATE TABLE contradictions (
    block_a_id  TEXT    NOT NULL,
    block_b_id  TEXT    NOT NULL,
    strength    REAL    NOT NULL,
    detected_at TEXT    NOT NULL,
    source      TEXT    NOT NULL,    -- 'explicit' | 'llm_inferred'
    resolved    INTEGER NOT NULL DEFAULT 0,  -- 0 = active, 1 = resolved
    resolved_at TEXT,
    resolved_by TEXT,
    PRIMARY KEY (block_a_id, block_b_id),
    FOREIGN KEY (block_a_id) REFERENCES blocks(id) ON DELETE CASCADE,
    FOREIGN KEY (block_b_id) REFERENCES blocks(id) ON DELETE CASCADE
);
```

Separate from `edges` because contradictions have a different lifecycle:
no decay, no reinforcement, explicit resolution. The recall() contradiction
check only queries where `resolved = 0`.

### `frames` — named frame definitions

```sql
CREATE TABLE frames (
    name            TEXT    PRIMARY KEY,
    weights_json    TEXT    NOT NULL,  -- {"recency":0.25,"similarity":0.35,...}
    filter_tags     TEXT,              -- JSON array | NULL (all blocks)
    filter_category TEXT,              -- glob | NULL
    template        TEXT    NOT NULL,  -- 'self' | 'attention' | 'task'
    token_budget    INTEGER NOT NULL,
    guarantee_tags  TEXT,              -- JSON array | NULL
    cache_ttl       INTEGER,           -- NULL = no caching
    source          TEXT    NOT NULL DEFAULT 'user',  -- 'builtin'|'user'|'agent'
    created_at      TEXT    NOT NULL
);
```

Seeded at init with the three built-in frames (`source='builtin'`).
Custom frames registered by callers are stored here alongside them.

### `sessions` — session lifecycle for decay accounting

```sql
CREATE TABLE sessions (
    id         TEXT    PRIMARY KEY,
    started_at TEXT    NOT NULL,
    ended_at   TEXT,               -- NULL while session is active
    active     INTEGER NOT NULL DEFAULT 1
);
```

Used to compute active hours for session-aware decay. At session start:

```sql
SELECT SUM(
    (julianday(ended_at) - julianday(started_at)) * 24
) as active_hours
FROM sessions
WHERE ended_at IS NOT NULL
AND id != (SELECT id FROM sessions WHERE active = 1);
```

Then: bulk-update `hours_since_reinforcement` for blocks not reinforced in
this period. One query; all blocks updated in a single transaction.

### `system_config` — tunable parameters

```sql
CREATE TABLE system_config (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

Stores: `prune_threshold`, `similarity_edge_threshold`, `top_k`, `soft_cap`,
`inbox_consolidate_threshold`, `curate_active_hours_threshold`, `embedding_model`.

---

## Indexes

Every index exists for a specific query pattern:

```sql
-- Tag filtering: the most common filter in recall()
CREATE INDEX idx_block_tags_tag ON block_tags(tag);

-- Active-only block scanning (ATTENTION frame loads all active blocks)
CREATE INDEX idx_blocks_status ON blocks(status);

-- Centrality: edge lookup per block (two directions, one index each)
CREATE INDEX idx_edges_from ON edges(from_id);
CREATE INDEX idx_edges_to   ON edges(to_id);

-- Contradiction check: only unresolved contradictions matter
CREATE INDEX idx_contradictions_a ON contradictions(block_a_id)
    WHERE resolved = 0;
CREATE INDEX idx_contradictions_b ON contradictions(block_b_id)
    WHERE resolved = 0;

-- INBOX count check (consolidate trigger)
CREATE INDEX idx_inbox_received ON inbox(received_at);

-- curate() decay sweep: all active blocks by decay lambda
CREATE INDEX idx_blocks_decay ON blocks(status, decay_lambda)
    WHERE status = 'active';
```

**What is NOT indexed:**
- `embedding` — BLOBs are never index-searched; brute-force similarity is in Python
- `confidence`, `reinforcement_count` — read as part of full row fetch, not filtered
- `edges.weight` — scanned per block (max 10 edges per block, degree cap from 013)

---

## Centrality: Computed at Query Time in Phase 1

Centrality = sum of weights of all edges touching a block.

```sql
SELECT COALESCE(SUM(weight), 0) as centrality
FROM edges
WHERE from_id = ? OR to_id = ?
```

With a degree cap of 10 edges per block (exploration 013), this returns ≤10 rows.
With two indexes (`idx_edges_from`, `idx_edges_to`), each lookup is O(log N) where
N is total edges, not total blocks. Fast at all realistic Phase 1–2 scales.

**Phase 2 optimisation:** add `centrality_cached REAL` to `blocks`, updated by
curate(). The scoring formula reads it directly. curate() recomputes it from edges.
This eliminates per-block edge queries during recall() at the cost of one extra
column and a curate() step.

For Phase 1: compute at query time. No premature optimisation.

---

## Embedding Storage Strategy

An embedding is a vector of 1536 float32 values (for OpenAI ada-002 / text-embedding-3-small).
Size per block: 1536 × 4 bytes = **6,144 bytes ≈ 6 KB**.

| Block count | Embedding storage | Similarity search time |
|-------------|------------------|----------------------|
| 50 | 300 KB | < 1ms (trivial) |
| 1,000 | 6 MB | ~2ms (numpy, in-memory) |
| 10,000 | 60 MB | ~15ms (numpy, in-memory) |
| 100,000 | 600 MB | ~150ms (needs ANN index) |

**Phase 1:** Embeddings stored as BLOB in `blocks.embedding`. Loaded all at once
for brute-force cosine similarity in Python/numpy. SQLite BLOB columns are stored
inline for values ≤ 2 KB (spills to overflow pages above that). 6 KB embeddings
will use overflow pages but this is transparent — SQLite handles it.

**Phase 2 (>1,000 blocks):** Split to a separate `block_embeddings` table:
```sql
CREATE TABLE block_embeddings (
    block_id        TEXT PRIMARY KEY,
    embedding       BLOB NOT NULL,
    embedding_model TEXT NOT NULL,
    FOREIGN KEY (block_id) REFERENCES blocks(id) ON DELETE CASCADE
);
```

This keeps the `blocks` table narrow (fast full-table scans for SELF frame
scoring, which doesn't need embeddings at all) while keeping embeddings
grouped for bulk loading during ATTENTION frame scoring.

**Phase 3 (>10,000 blocks):** Add the `sqlite-vec` extension for approximate
nearest-neighbor search inside the same SQLite file. No new database, no new
infrastructure — just an extension loaded at connection time.

```python
conn.enable_load_extension(True)
conn.load_extension("vec0")  # sqlite-vec
```

Then: `CREATE VIRTUAL TABLE block_vss USING vec0(embedding float[1536])`.
Queries via `embedding <-> query_vec LIMIT 20` return ANN results inside SQL.

---

## File System Layout

Block files are the primary record (exploration 010). Database is a derived index.

```
~/.amgs/
├── blocks/
│   ├── a3f9c2b1d84593e1.md
│   ├── b7e4a9d2c815f3a8.md
│   └── ...
├── inbox/
│   └── (cleared after each consolidate())
└── amgs.db
```

**Phase 1: flat `blocks/` directory.** At 50 blocks this is trivial.

**Phase 2 (>1,000 blocks): 2-level sharding by ID prefix:**
```
blocks/
├── a3/
│   └── a3f9c2b1d84593e1.md
├── b7/
│   └── b7e4a9d2c815f3a8.md
```

256 directories (00–ff), each holding on average N/256 files. At 10,000 blocks:
~39 files per directory. Prevents inode exhaustion on some filesystems.

Migration from flat to sharded: move files + `UPDATE blocks SET file_path = ...`.
Content-addressed IDs make this safe — paths are deterministic from IDs.

---

## Transaction Groupings

| Operation | Transaction scope | Reason |
|-----------|------------------|--------|
| `learn()` | 1 transaction per call | Single INSERT to inbox |
| `consolidate()` one block | 1 transaction per block | Partial progress preserved; one failure doesn't abort others |
| `consolidate()` edge creation | Part of the block's transaction | Block + edges + tags are atomic |
| `recall()` reinforcement | 1 transaction for all returned blocks | Partial reinforcement would skew scoring |
| `curate()` maintenance pass | 1 transaction for the entire pass | All-or-nothing maintenance; no half-pruned state |
| Session open/close | 1 transaction | Sessions table + bulk hours_since_reinforcement update |

---

## The recall() Query Plan in Full

Tracing a complete ATTENTION frame call to show the actual SQL sequence:

```
Input: query = "concurrent database writes"

─── Step 1: Load scoring data + embeddings ──────────────────────────────────
SELECT b.id, b.confidence, b.reinforcement_count, b.hours_since_reinforcement,
       b.decay_lambda, b.self_alignment, b.embedding
FROM blocks b
WHERE b.status = 'active'
AND b.embedding IS NOT NULL;
→ returns 50 rows, ~300KB of embedding data

─── Step 2: In Python (not SQL) ─────────────────────────────────────────────
query_vec = embed("concurrent database writes")           # external LLM call
sims = cosine_similarity(query_vec, all_block_embeddings) # numpy, < 1ms

─── Step 3: Per-block centrality (parallelisable) ───────────────────────────
SELECT COALESCE(SUM(weight), 0)
FROM edges
WHERE from_id = ? OR to_id = ?;
→ one query per candidate block; ≤10 rows per query

─── Step 4: In Python ───────────────────────────────────────────────────────
score = (0.25 × recency_score)
      + (0.15 × centralityScore / max_centrality)
      + (0.15 × confidence)
      + (0.35 × similarity[i])
      + (0.10 × log(1 + reinforcement_count) / log(1 + max_count))

sort by score descending, take top-10 as candidates

─── Step 5: Contradiction check ─────────────────────────────────────────────
SELECT block_a_id, block_b_id, strength
FROM contradictions
WHERE (block_a_id IN (id1..id10) OR block_b_id IN (id1..id10))
AND resolved = 0;
→ typically 0 rows; partial-index makes this near-instant

─── Step 6: In Python — suppression ─────────────────────────────────────────
for each contradicting pair: keep higher-confidence block, drop other
take final top-5

─── Step 7: Reinforce ───────────────────────────────────────────────────────
BEGIN TRANSACTION;

UPDATE blocks
SET reinforcement_count = reinforcement_count + 1,
    hours_since_reinforcement = 0,
    last_reinforced_session = 'sess_abc'
WHERE id IN ('id1', 'id2', 'id3', 'id4', 'id5');

UPDATE edges
SET reinforcement_count = reinforcement_count + 1,
    hours_since_co_retrieval = 0
WHERE (from_id IN (...) AND to_id IN (...));   -- only co-retrieved pairs

COMMIT;

─── Total: ──────────────────────────────────────────────────────────────────
  DB queries:   1 (load) + 5 (centrality) + 1 (contradictions) + 1 (reinforce)
  Python ops:   cosine similarity + scoring + sorting
  Wall time:    < 5ms at 50 blocks (excluding external embedding call)
```

---

## Phase Scaling Summary

| Phase | Block count | Embedding search | Centrality | DB size |
|-------|------------|-----------------|------------|---------|
| Phase 1 | ≤50 | In-memory brute force (trivial) | Per-query edge lookup | < 5 MB |
| Phase 2 | ≤5,000 | In-memory numpy (~10ms) | Materialised in `blocks` table | ~50 MB |
| Phase 3 | ≤50,000 | sqlite-vec ANN index | Materialised, updated by curate() | ~350 MB |
| Phase 4 | >50,000 | Dedicated vector index | Pre-computed | Consider Postgres |

Each transition requires:
- Phase 1 → 2: split `embedding` to `block_embeddings` table, add `centrality_cached` column
- Phase 2 → 3: add sqlite-vec virtual table, migrate embeddings to vec0 format
- Phase 3 → 4: replace SQLite with Postgres + pgvector; file paths unchanged

**The file layout and schema remain stable across Phase 1–3.** No rearchitecting —
only additive changes. The block IDs, edge schema, and contradiction schema are
unchanged across all phases.

---

## What Makes This Fast for recall()

1. **All scoring fields on one row** — `blocks` read returns everything needed for
   scoring (confidence, reinforcement, decay, alignment) in a single query. No joins
   needed to score a block.

2. **Tags in a separate indexed table** — filtering `WHERE tag LIKE 'self/%'` uses
   the `idx_block_tags_tag` index. This is faster than JSON parsing a tags array
   in the `blocks` row.

3. **Contradiction check uses partial indexes** — `WHERE resolved = 0` is indexed
   separately. As contradictions are resolved over time, the active index stays small
   regardless of historical contradiction count.

4. **Embeddings batch-loaded once per recall()** — a single `SELECT ... WHERE status='active'`
   loads all embeddings. Cosine similarity runs in Python in a single numpy operation.
   No per-block embedding queries.

5. **WAL mode** — consolidate() writes don't block recall() reads. In a long-running
   application where consolidation runs async, recall() always sees the last committed
   state without waiting.

---

## Design Decisions from this Exploration

| Decision | Rationale |
|----------|-----------|
| SQLite only — no separate databases | Sufficient for all realistic agent memory scales; zero infrastructure |
| WAL mode enabled | Concurrent recall() reads during async consolidate() writes |
| Tags in `block_tags` table, not JSON in `blocks` | Indexed filtering; ON DELETE CASCADE; clean status promotion |
| `embedding` BLOB in `blocks` table (Phase 1) | Co-located for simple bulk load; split to `block_embeddings` at Phase 2 |
| Centrality computed at query time from `edges` (Phase 1) | No premature materialisation; add `centrality_cached` at Phase 2 |
| `hours_since_reinforcement` stored directly on `blocks` | Updated in bulk at session start; always valid at query time without computation |
| `sessions` table tracks active hours | Enables session-aware decay without wall-clock time |
| Contradiction check uses partial index on `resolved = 0` | Active contradictions stay small as old ones resolve; index remains fast |
| Phase 1: flat `blocks/` directory | Simple; no inode issues at 50 blocks |
| Phase 2: 2-level sharding by ID prefix | Deterministic from ID; migration is file-move + UPDATE |
| Phase 1 → 3 schema is additive only | Columns added; no existing columns renamed or removed; migrations are safe |
| sqlite-vec extension for Phase 3 ANN search | Same SQLite file; no new infrastructure; loaded as an extension |
| curate() runs in one transaction | All-or-nothing maintenance; no half-pruned memory state |
| recall() reinforcement is one transaction | Partial reinforcement would corrupt scoring; atomicity required |

---

## Open Questions

- [ ] Should `block_embeddings` be split from `blocks` immediately (simpler Phase 2
      migration) or at 1000-block threshold (simpler Phase 1)?
- [ ] Should `centrality_cached` be added in Phase 1 to avoid per-block edge queries,
      even though 50 blocks makes this trivial?
- [ ] Is there value in a `block_prune_log` table for audit — recording what was pruned
      and why? (Raised in exploration 008; useful for debugging curate() behaviour)
- [ ] Should the database live at `~/.amgs/amgs.db` (global per user) or relative to
      the memory directory (allows multiple independent AMGS instances)?
- [ ] Should `system_config` values be overridable per session, or are they always global?

---

## Variations

- [ ] Trace through consolidate() in the same query-plan style as the recall() trace
      above — what does one block's full consolidation pipeline look like as SQL?
- [ ] What does the curate() maintenance pass look like as a query sequence?
      In particular, how are decayed edge weights computed and applied in bulk?
- [ ] Model the Phase 1 → Phase 2 migration as a concrete ALTER TABLE + index creation
      sequence. What's the minimum downtime for 5,000 blocks?
