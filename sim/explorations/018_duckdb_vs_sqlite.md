# Title: DuckDB vs SQLite — Storage Engine Evaluation

## Status: complete

## Question

Exploration 017 chose SQLite without evaluating DuckDB. DuckDB is a legitimate
contender: it's in-process, single-file, embeddable, and has native support for
array types and vector similarity search. Is SQLite still the right choice, or
should the storage engine be DuckDB?

---

## What DuckDB Is

DuckDB is an in-process OLAP (analytical) database. Like SQLite it has no server,
runs as a library inside the application process, and stores data in a single file.
Unlike SQLite it is column-oriented internally and uses a vectorized execution engine
designed for analytical workloads — aggregations, full-table scans, per-row math
across many columns.

It is not the same kind of database as SQLite. The difference is not just performance —
it is the fundamental storage and execution model:

```
SQLite:  row-oriented,    OLTP — fast point lookups, small frequent writes
DuckDB:  column-oriented, OLAP — fast full-table scans, aggregations, bulk math
```

Both run in-process. Both are ACID. Both are SQL. But they are optimised for
opposite workloads.

---

## The AMGS Workload Profile

To evaluate the choice, first classify the actual operations:

| Operation | Type | Pattern |
|-----------|------|---------|
| `recall()` — load scoring fields + embeddings | **OLAP read** | Full scan of `blocks` WHERE status='active' |
| `recall()` — centrality per block | **OLTP read** | Point lookup × 5 (`edges` by block id) |
| `recall()` — contradiction check | **OLTP read** | Set membership check (5–10 ids) |
| `recall()` — reinforcement write | **OLTP write** | 5 row UPDATEs by PK |
| `consolidate()` — INSERT block + tags + edges | **OLTP write** | 1–3 row INSERTs per block |
| `consolidate()` — dedup check | **OLTP read** | Point lookup by content hash |
| `curate()` — decay all blocks | **OLAP** | Full scan, per-row math, bulk UPDATE, bulk DELETE |
| `curate()` — max reinforcement for normalisation | **OLAP** | `SELECT MAX(reinforcement_count)` |
| `curate()` — edge decay | **OLAP** | Full `edges` scan, bulk math |

**The workload is mixed OLTP + OLAP.** Two operations (recall() reinforcement and
consolidate() writes) are clearly OLTP. One operation (curate()) is clearly OLAP.
The scoring load in recall() is borderline — it reads many columns from all active
blocks, which looks OLAP, but then does point-lookup edge queries, which looks OLTP.

Neither database is a perfect fit. The question is which mismatch hurts more.

---

## Head-to-Head Comparison

### 1. Zero-install

```
SQLite:  import sqlite3   — Python standard library, no pip install
DuckDB:  pip install duckdb
```

Minor, but real for distribution. A library that wraps AMGS can be installed without
pulling in a non-trivial C++ binary. At ~30MB for the DuckDB binary vs. effectively
zero for sqlite3, this matters for CLI tools and lightweight deployments.

**Winner: SQLite**

---

### 2. Concurrent read/write (recall() during consolidate())

Exploration 017 established that WAL mode lets recall() read while consolidate()
writes asynchronously. How does DuckDB compare?

DuckDB uses MVCC (Multi-Version Concurrency Control). Within a single process,
readers and writers don't block each other — DuckDB gives each transaction a
snapshot of the data. This is fine for in-process async use.

However, DuckDB does not support multiple **processes** accessing the same database
file simultaneously. If AMGS is used as a library from two Python processes (e.g.,
a background consolidation daemon + a web server calling recall()), DuckDB will
raise an error on the second connection attempt.

SQLite WAL mode supports this. Multiple processes can read; one can write.

**Winner: SQLite** for multi-process deployments; DuckDB acceptable for single-process.

---

### 3. Row-level update performance (reinforcement writes)

Every recall() call reinforces 5 blocks and 2–5 edges:

```sql
UPDATE blocks SET reinforcement_count = reinforcement_count + 1,
                  hours_since_reinforcement = 0
WHERE id IN ('a1', 'a2', 'a3', 'a4', 'a5');
```

This is a small transaction against row-level data identified by primary key —
the canonical OLTP workload. SQLite's row-oriented storage handles this with
a direct B-tree lookup and in-place update.

DuckDB's columnar format requires reading a column chunk, modifying 5 values,
and writing the chunk back. For 5 rows in a table of 50–5,000 blocks, DuckDB
does more work per transaction than SQLite.

This happens on **every recall() call** — the most frequent write in the system.

**Winner: SQLite** (clearly, for small frequent transactions)

---

### 4. Full-table scan analytical performance (curate(), scoring)

curate() applies decay to all blocks:

```sql
SELECT id, decay_lambda, hours_since_reinforcement
FROM blocks WHERE status = 'active';
-- then bulk UPDATE, bulk DELETE based on computed values
```

Also: scoring during recall() reads confidence, reinforcement_count,
decay_lambda, hours_since_reinforcement for all active blocks simultaneously.

This is a columnar read — a few columns, all rows. DuckDB's vectorized engine
excels here. It reads each column as a contiguous block, applies the math
(e.g., `exp(-lambda * hours)`) across the whole column in SIMD, and produces
results faster than SQLite's row-by-row processing.

**At Phase 1 (50 blocks):** irrelevant — both are sub-millisecond.
**At Phase 2 (5,000 blocks):** DuckDB's advantage becomes measurable, perhaps
2–5× faster for the curate() sweep.
**At Phase 3 (50,000 blocks):** DuckDB's analytical advantage is significant.

**Winner: DuckDB** at Phase 2+ scale; SQLite fine at Phase 1.

---

### 5. Embedding / vector similarity search

Both databases require an extension for ANN (Approximate Nearest Neighbor) search.

```
SQLite:  sqlite-vec extension  — FLOAT32 array type, ANN via HNSW index
DuckDB:  vss extension         — FLOAT32 array type, ANN via HNSW index
DuckDB:  array_distance()      — built-in exact cosine similarity (no extension)
```

DuckDB has `array_distance()` as a **built-in function** — no extension needed for
brute-force similarity search:

```sql
-- DuckDB: no extension needed
SELECT id, array_distance(embedding, $1::FLOAT[1536]) as sim
FROM blocks
WHERE status = 'active'
ORDER BY sim LIMIT 10;
```

SQLite requires either the sqlite-vec extension or pulling all embeddings into
Python for numpy cosine similarity.

DuckDB also integrates natively with numpy arrays via Arrow — the Python binding
can pass a numpy array as a parameter without serialisation overhead.

**Winner: DuckDB** — cleaner embedding support, no extension needed for Phase 1–2.

---

### 6. Ecosystem maturity and tooling

```
SQLite:  Released 2000. 24 years of production use. Universal DB browser support.
         Every operating system includes it. Known failure modes are well-documented.

DuckDB:  Released 2018. v1.0 reached 2024. Rapidly evolving. Excellent but
         historically had breaking changes between minor versions.
         Fewer GUI tools (though DBeaver, TablePlus support it).
```

For an exploratory agent memory system under active development, DuckDB's faster
evolution is a mixed bag — improvements arrive quickly but upgrade paths can break.

**Winner: SQLite** on stability; DuckDB improving rapidly.

---

### 7. Python integration

```python
# SQLite
import sqlite3
conn = sqlite3.connect("amgs.db")

# DuckDB
import duckdb
conn = duckdb.connect("amgs.db")
conn.execute("CREATE TABLE ...")
conn.execute("SELECT ...", [numpy_array])  # ← native numpy parameter passing
df = conn.execute("SELECT ...").df()       # ← returns pandas DataFrame directly
```

DuckDB's Python API is noticeably more ergonomic for data-heavy work. Direct
numpy/pandas/Arrow integration without serialisation is a genuine advantage
for the embedding operations.

**Winner: DuckDB** on ergonomics; SQLite on universality.

---

## The Decisive Factor: Write Pattern Frequency

The AMGS has an unusual write pattern: **small reinforcement writes happen on
every single recall() call**. This is not a typical OLAP workload.

In a pure analytics system, you ingest data in batches and read it analytically.
AMGS is different: it reads analytically (scores across all blocks) but writes
transactionally on every read (reinforcement). The writes are not occasional batch
operations — they happen synchronously as part of the read path.

DuckDB is designed for workloads where writes are infrequent, large, and batch-like.
AMGS's reinforcement writes are exactly the opposite: frequent, small, per-PK.

This is the strongest single argument against DuckDB for this use case.

---

## The Honest Verdict

| Criterion | SQLite | DuckDB | Winner |
|-----------|--------|--------|--------|
| Zero-install | ✅ builtin | ❌ pip install | SQLite |
| Multi-process concurrency | ✅ WAL mode | ❌ single-process | SQLite |
| Small frequent writes (reinforcement) | ✅ OLTP native | ❌ columnar overhead | SQLite |
| Full-table scan analytics (curate) | Adequate | ✅ vectorized SIMD | DuckDB |
| Embedding / vector support | Extension needed | ✅ built-in + extension | DuckDB |
| numpy/pandas integration | Manual | ✅ native Arrow | DuckDB |
| Ecosystem maturity | ✅ 24 years | Newer | SQLite |
| Phase 1 fit (50 blocks) | ✅ | Fine, no advantage | SQLite |
| Phase 2 fit (5,000 blocks) | Fine | ✅ competitive | DuckDB |
| Phase 3 fit (50,000 blocks) | Fine | ✅ analytical advantage | DuckDB |

**SQLite is the correct choice for Phase 1.** The deciding factors:
- Reinforcement writes happen on every recall() — DuckDB pays a columnar tax on every call
- Multi-process access: WAL mode works; DuckDB's single-file lock doesn't
- Zero install: meaningful for distribution
- Phase 1 scale (50 blocks): DuckDB's analytical advantages are completely invisible

**DuckDB becomes competitive at Phase 2+ IF** the usage profile shifts toward
heavier curate() analytics and the host application is single-process.

---

## A Note on the Hybrid Approach

Some systems use SQLite for transactional data and DuckDB for analytics. For AMGS:

```
SQLite:  blocks, tags, inbox, edges, contradictions, frames, sessions — all writes
DuckDB:  read-only analytical queries during curate() and scoring
```

This is genuinely appealing: each database does what it's good at. But it adds
synchronisation complexity — writes go to SQLite, and DuckDB must either attach
the SQLite file (DuckDB can directly query SQLite files via a scanner) or get a
copy of the data.

DuckDB can actually query SQLite files directly:
```sql
-- DuckDB can attach a SQLite file as a read-only data source:
ATTACH 'amgs.db' AS sqlite_db (TYPE sqlite, READ_ONLY);
SELECT exp(-decay_lambda * hours_since_reinforcement) as decay
FROM sqlite_db.blocks WHERE status = 'active';
```

This hybrid is elegant: SQLite does all writes; DuckDB reads the SQLite file
directly for analytical passes. No data duplication, no synchronisation problem.

**But this is Phase 2+.** For Phase 1 at 50 blocks, any performance gain from
DuckDB's vectorized execution is immeasurable. Introducing the hybrid adds
complexity for zero observable benefit.

---

## Updated Decision for Exploration 017

The storage decision from exploration 017 stands for Phase 1:

> **SQLite, WAL mode, single file.**

The Phase 3 extension path should be updated:

```
Previous plan: sqlite-vec extension for ANN at Phase 3
Updated plan:  Option A — sqlite-vec (stay in SQLite)
               Option B — DuckDB hybrid (SQLite writes + DuckDB analytics)
               Decision at Phase 2 → 3 boundary based on actual bottleneck
```

The bottleneck to measure at Phase 2 boundary:
- If **curate() analytics** is the bottleneck → consider DuckDB hybrid
- If **similarity search** is the bottleneck → sqlite-vec or DuckDB vss (roughly equivalent)
- If **reinforcement write latency** is the bottleneck → stay in SQLite regardless

---

## Design Decisions from this Exploration

| Decision | Rationale |
|----------|-----------|
| SQLite confirmed as Phase 1 storage engine | Reinforcement writes on every recall(); multi-process WAL; zero-install |
| DuckDB deferred to Phase 2+ evaluation | Analytical advantages invisible at 50 blocks; columnar tax on frequent small writes |
| Phase 3 path updated: measure bottleneck first | curate() analytics → DuckDB hybrid; similarity → sqlite-vec; writes → stay SQLite |
| DuckDB hybrid (attach SQLite for analytics) noted as Phase 2+ option | DuckDB can query SQLite files directly; no data duplication |
| Reinforcement-write frequency is the decisive factor against DuckDB | AMGS writes transactionally on every read — antithetical to columnar OLAP design |

---

## Open Questions

- [ ] At what block count does curate() analytical time become noticeable?
      (Measure at Phase 2: if curate() > 100ms at 5,000 blocks, DuckDB hybrid
      is worth evaluating)
- [ ] Does DuckDB's direct SQLite scanner (`ATTACH TYPE sqlite`) work reliably
      with SQLite WAL mode? (Needs testing at Phase 2)
- [ ] Is the DuckDB hybrid genuinely zero-synchronisation, or does the SQLite
      WAL checkpoint need to complete before DuckDB can see the latest writes?

---

## Variations

- [ ] Benchmark recall() reinforcement write latency: SQLite vs. DuckDB for
      5-row UPDATE by PK at 50 / 5,000 / 50,000 block scale.
- [ ] Benchmark curate() full-table scan: SQLite row scan vs. DuckDB vectorized
      at the same three scales.
- [ ] Prototype the DuckDB hybrid: write via SQLite, query via DuckDB ATTACH —
      verify WAL visibility and measure the overhead.
