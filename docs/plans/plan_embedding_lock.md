# Plan: embedding-model lock + safe migration

**Status**: design — ready to implement after v0.14.0
**Driver**: [issue #50 follow-up](https://github.com/emson/elfmem/issues/50)
**Target**: v0.15.0

---

## The bug

Today, changing `embeddings.model` in `config.yaml` silently corrupts the
DB. Same-dim swaps produce noise cosines; different-dim swaps crash on
first recall. Stored blocks already carry an `embedding_model` column —
nothing reads it for safety.

---

## Design

### Source of truth
- **Existing column** `blocks.embedding_model`. Already populated. No schema
  change needed; we start *trusting* it.

### Cached write-default
- `system_config["embedding_model_lock"]` (str) — the model new embeddings
  get tagged with.
- `system_config["embedding_dimensions_lock"]` (int) — set from
  `len(first_real_vec)`, never from adapter config (which may truncate/pad).

Two keys. No canary, no JSON blobs.

### Enforcement: single wrapper, no cache

```python
class LockedEmbeddingService:
    """Wraps EmbeddingService. Verifies the lock on every embed call —
    no cache. ~1 SELECT per embed (sub-ms vs LLM's 10-500ms — negligible
    overhead). Catches external lock changes (e.g. concurrent migration)
    without any cache-invalidation mechanism.

    BOTH `embed()` and `embed_batch()` are intercepted — consolidate.py
    uses embed_batch for the contradiction prefilter.
    """

    def __init__(self, inner, engine):
        self._inner = inner
        self._engine = engine

    @property
    def model_name(self) -> str:
        return self._inner.model_name

    @property
    def dimensions(self) -> int:
        return self._inner.dimensions

    async def embed(self, text: str) -> np.ndarray:
        vec = await self._inner.embed(text)
        await self._verify(vec)
        return vec

    async def embed_batch(self, texts: list[str]) -> list[np.ndarray]:
        vecs = await self._inner.embed_batch(texts)
        if vecs:
            await self._verify(vecs[0])
        return vecs

    async def _verify(self, vec) -> None:
        # Read both keys in one query
        async with self._engine.connect() as conn:
            rows = (await conn.execute(text(
                "SELECT key, value FROM system_config "
                "WHERE key IN ('embedding_model_lock', 'embedding_dimensions_lock')"
            ))).all()
        stored = dict(rows)
        stored_model = stored.get("embedding_model_lock", "")

        if not stored_model:
            # Lock missing — set atomically (race-safe)
            async with self._engine.begin() as conn:
                await conn.execute(text(
                    "INSERT OR IGNORE INTO system_config (key, value) VALUES "
                    "('embedding_model_lock', :m), ('embedding_dimensions_lock', :d)"
                ), {"m": self._inner.model_name, "d": str(len(vec))})
                rows = (await conn.execute(text(
                    "SELECT key, value FROM system_config WHERE key IN (...)"
                ))).all()
            stored = dict(rows)
            stored_model = stored.get("embedding_model_lock", "")

        stored_dims = int(stored.get("embedding_dimensions_lock") or "0")
        if stored_model != self._inner.model_name or stored_dims != len(vec):
            raise EmbeddingLockError(..., recovery=...)
```

**Why no cache**: a previous draft cached `_session_verified=True` after first
call. But MCP-server sessions live for hours. If `migrate-embeddings` runs in
another process and changes the lock, a cached MCP server keeps embedding with
the stale adapter — silent corruption, the exact bug the lock is meant to
prevent. Verify-on-every-call eliminates this without needing a generation
counter or TTL invalidation. Cost: ~22 SELECTs per dream batch of 10 blocks
(~22ms total) vs dream's own 5-60 second cost. Noise.

**One enforcement point.** Covers `consolidate`, `rescore`, any future embed
caller. Race-free (SQLite atomic INSERT-OR-IGNORE + SELECT-back).
No special-casing of tests — mock embedder's `model_name="mock"` locks
naturally to "mock" in in-memory test DBs.

**Critical design property: the migration command bypasses this wrapper.**
`elfmem migrate-embeddings` constructs its own bare `EmbeddingService` via
`make_embedding_adapter()` and its own `create_engine()`; it does NOT go
through `MemorySystem.from_config()`. If it did, the wrapper would see the
new model disagree with the OLD lock and refuse to embed — the migration
would self-block. Clean separation: admin operations operate below the
agent-facing safety layer.

### One backfill path (one-shot per install)

On `MemorySystem.from_config()`, after `ensure_schema_current`, if lock is
unset AND active blocks exist, three outcomes:

| Block state | Behaviour |
|---|---|
| **Homogeneous known** (all active blocks with `embedding_model` set agree on a single value) | Set lock from that value via `INSERT OR IGNORE` (race-safe). Backfill any `NULL` / empty / `"unknown"` rows in the active set to the detected model. Transparent. |
| **Heterogeneous known** (two or more distinct known models) | Raise `EmbeddingLockError` with recovery: `elfmem migrate-embeddings --from <model> --to <model> --execute` |
| **All-legacy** (all active blocks have `NULL` / `""` / `"unknown"`) | Raise `EmbeddingLockError` with recovery: `elfmem migrate-embeddings --execute`. *Don't* silently assume the current adapter is correct — if it isn't, backfill would be the source of corruption. |
| **No active blocks** (fresh DB) | Skip backfill; the wrapper's first embed will set the lock. |

Dimensions for the lock are derived from `len(bytes_to_embedding(...))` on a
representative stored vector — never from the adapter's `dimensions` config
(which may truncate or pad).

### Doctor surface

```
Embedding lock      OK (text-embedding-3-small, 1536-dim)
                    OR
                    FRESH (no lock yet — will set on first dream)
                    OR
                    MISMATCH — DB locked to text-embedding-3-small;
                    config says nomic-embed-text. Recover:
                      • Edit embeddings.model in config to text-embedding-3-small
                      • Or: elfmem migrate-embeddings --execute (re-embeds all)
```

Non-raising. Always usable to diagnose.

### Migration verb

```
elfmem migrate-embeddings              # default: estimate, no writes
elfmem migrate-embeddings --execute    # run (auto-resume on partial state)
elfmem migrate-embeddings --to <X>     # rare: override target
elfmem migrate-embeddings --from <X> --to <Y>
                                       # heterogeneous-source disambiguation
```

**`--execute` flow:**

1. Construct a bare `EmbeddingService` via `make_embedding_adapter()` and
   `create_engine(db_path)` directly. **Do NOT use
   `MemorySystem.from_config()`** — it would apply the lock wrapper and
   self-block the migration.
2. Backup DB via `create_backup(db_path, suffix="pre-migrate-embeddings")`.
3. For each active block, in batches (default ~50) within a transaction:
   - If `embedding_model` already matches target → skip (resumability).
   - Else: embed content with the bare service; update `embedding` bytes
     and `embedding_model` field. Track `len(vec)` of the most recent
     embedding (used for the lock-dim update at the end — no separate
     "probe" call needed).
4. After all blocks committed:
   - Drop edges where `origin IN ('similarity', 'co_retrieval')` —
     similarity-derived. Preserve `origin = 'user'` (and any custom
     user-asserted relations).
   - The Hebbian *staging table* is preserved (block IDs unchanged) —
     only materialised edges drop.
5. Update lock keys in a single statement using `INSERT ... ON CONFLICT
   DO UPDATE`.
6. Print recommendation: `elfmem dream` to rebuild similarity edges from
   the new vectors.

Resumability is automatic from per-batch atomicity. Mid-run crashes leave
blocks in mixed state; lock unchanged until step 5; `--execute` again
skips already-migrated blocks.

**SQL NULL-trap (caught on review)**: the resumability filter must be:

```sql
WHERE embedding_model IS NULL OR embedding_model != :target_model
```

A naive `WHERE embedding_model != :target_model` would silently skip rows
where `embedding_model IS NULL` because `NULL != X` evaluates to `NULL`
(falsy in SQL). Those rows would remain unmigrated and fail the lock
check later. Verified by `test_migrate_handles_null_embedding_model_rows`.

**Estimate mode** (default; no writes):
- Reports block count to re-embed
- Total content character count
- Rough token estimate (`chars // 4` — close enough for back-of-envelope)
- Cost depends on provider; defer real pricing tables.

---

## Edge-case matrix

| # | Scenario | Behaviour |
|---|---|---|
| 1 | Race: two MCP boots, same DB | `INSERT OR IGNORE → SELECT-back` is atomic. Winner sets lock; loser raises if adapters disagree. |
| 2 | Race: two `from_config` calls against fresh DB | Backfill uses `INSERT OR IGNORE` (matching wrapper). Loser's insert no-ops; verify catches downstream mismatch. |
| 3 | Mock embedding in tests | model_name="mock" locks to "mock"; in-memory DB throws away with engine. No bypass needed. |
| 4 | Heterogeneous legacy install | Backfill detects, raises with `--from/--to` recovery hint. |
| 5 | NULL, empty string `""`, or `"unknown"` rows in legacy data | All folded into a single legacy bucket and backfilled to dominant model. |
| 6 | OpenAI returns truncated/padded vectors | `len(first_real_vec)` is the source of truth for dims; adapter config never trusted. |
| 7 | Migrate while MCP server running | SQLite WAL serialises writes; next MCP session-start re-verifies, raises with restart hint. |
| 8 | Migration crash at block 7000 of 10000 | Per-batch atomicity; re-running `--execute` resumes; lock unchanged until completion. |
| 9 | Migration crash *between* last block commit and lock update | Self-healing: re-run `--execute` skips all already-migrated blocks (no-op block loop) and just runs the lock update. Recoverable transient state, not corruption. |
| 10 | Empty active table | `migrate-embeddings --execute` is a no-op; lock stays unset until first real dream. |
| 11 | Archived blocks | Not in active set; migration ignores them. Documented as expected. |
| 12 | Backup restored on different machine with different config | Doctor surfaces both recovery commands; user picks based on intent (no separate `config sync` verb needed). |
| 13 | Long-lived MemorySystem after external migrate | `reset_for_new_session()` on next `begin_session()` clears `_session_verified`; re-verification raises if adapter still configured for old model. |
| 14 | `consolidate` calls `embed_batch` for contradiction prefilter | Wrapper intercepts `embed_batch` AND `embed` — first call from either path triggers verification. |
| 15 | Migration verb constructs through `from_config` (mistake) | Would deadlock: wrapper applies, sees new model vs old lock, refuses. **Migration must use bare `make_embedding_adapter()` + `create_engine()` directly.** This is the most subtle pitfall — surface explicitly in tests. |
| 16 | Hebbian staging across migration | Block IDs unchanged → staging table stays valid. Only materialised edges drop. |
| 17 | Co-retrieval-origin edges across migration | Treated as similarity-derived (dropped with `origin = 'similarity'`). User-asserted edges (`origin = 'user'`) preserved. |
| 18 | Peer-imported foreign-model blocks | Enter inbox without embeddings; dreamed locally under current model. Lock applies normally — no special handling needed. |

---

## What is deliberately deferred to a follow-up PR

| Item | Why deferred |
|---|---|
| **Canary fingerprint** (catches Ollama silent weight updates) | Real risk but no user has reported hitting it. Ship the obvious fix first; add canary in v0.16.0 if reports surface. Cost saved: 1 embed per boot, 1 config key, ~30 lines, an epsilon-tuning decision. |
| **Multi-model retrieval** (hybrid dense+sparse) | Per-row column already supports it. Build when a user asks. |
| **Cost-budget enforcement on migrate** | Preview only, no hard cap. Auto-stopping mid-migration creates partial state we'd then have to recover from. Manual judgement. |
| **`elfmem config sync-from-db` verb** | Doctor's recovery text "Edit `embeddings.model` in your config to `<db-lock-model>`" is enough. No new command needed. |
| **Provider-deprecation runway mode** (old reads + new writes during transition) | Hypothetical; complex; covered by manual migration when deprecation hits. |
| **Auto-fallback on migration failure** | Manual recovery preferred; don't paper over failures. |

---

## Implementation phases

**Phase 1 — Lock infrastructure** (~120 lines + tests)
- `LockedEmbeddingService` wrapper (`src/elfmem/adapters/locked.py`)
- `verify_or_set_embedding_lock` query function (`src/elfmem/db/queries.py`)
- `backfill_embedding_lock_if_needed` helper called from
  `MemorySystem.from_config()` after `ensure_schema_current`
- Doctor lock-status surface (`src/elfmem/cli.py`)

**Phase 2 — Migration tool** (~200 lines + tests)
- `elfmem migrate-embeddings --execute / --to / --from`
  (default is estimate; no `--estimate` flag)
- Per-batch transactions; auto-resume; backup; edge-drop;
  lock update at end
- Migration verb uses bare `make_embedding_adapter` +
  `create_engine`, NOT `MemorySystem.from_config()`
- Doctor surfaces the two recovery paths (config edit OR migrate)

Phase 1 alone improves safety. Phase 2 makes the recovery path
ergonomic.

## Test obligations

Each test obligation maps to a code-discipline rule stored in elf's memory:

**Agent-perspective tests** (every public surface; assert via observable state):
- `test_dream_sets_lock_on_first_embed` — fresh DB, run dream, assert
  `system_config` rows exist with correct values via `get_config()`
  (not internal `_session_verified` state).
- `test_dream_raises_on_model_mismatch` — pre-set lock to `"foo"`,
  construct system with `MockEmbeddingService(model_name="bar")`,
  assert `EmbeddingLockError` raised on first dream with `.recovery`
  containing `"migrate-embeddings"`.
- `test_doctor_reports_lock_status` — three subtests (fresh / ok /
  mismatch); each constructs a doctor invocation and asserts the
  reported text + exit code.

**Backwards-compat tests** (written FIRST for any hashed/serialized state change):
- `test_legacy_install_homogeneous_blocks_backfills_silently` — pre-seed
  DB with active blocks all tagged `embedding_model="text-embedding-3-small"`,
  no lock; assert `from_config()` succeeds + lock set correctly.
- `test_legacy_install_with_null_and_unknown_rows_backfilled` — mix of
  NULL, `""`, `"unknown"`, and `"text-embedding-3-small"`; assert all
  rows updated to the dominant model + lock set.
- `test_legacy_install_heterogeneous_blocks_refuses_with_recovery` —
  blocks with two different model values; assert `EmbeddingLockError`
  with `.recovery` mentioning `--from` and `--to`.

**Race-safety tests**:
- `test_backfill_insert_or_ignore_idempotent` — simulate two concurrent
  backfill writes (call the helper twice in quick succession against
  the same DB); both succeed, lock has consistent values.
- `test_wrapper_first_call_atomic_set_and_select` — simulate two
  wrapper instances embedding simultaneously against same fresh DB
  with different adapter `model_name`s; one succeeds, one raises.

**The pitfall test** (specific to this feature's subtle deadlock):
- `test_migrate_embeddings_does_not_self_block` — run migration end-to-end
  on a DB that already has a lock; assert it completes without raising
  `EmbeddingLockError` (proves the migration command bypasses the
  wrapper, not via mock/fake but actual code path).

**Coverage of both call paths**:
- `test_wrapper_intercepts_embed_batch_too` — call only `embed_batch`,
  never `embed`, on a fresh wrapper; assert lock is set after.

Total: ~10 focused tests for Phase 1, ~6 for Phase 2 (estimate-mode,
execute end-to-end, resumability after mid-run interruption, edge drop,
lock update at end, idempotent re-run).

---

## Migration impact (0.14.x → 0.15.0)

| User state | Behaviour |
|---|---|
| Healthy install, no model swap | Transparent. Backfill sets lock from existing homogeneous `embedding_model` data on first session. No user action. |
| Healthy install with NULL/"unknown" legacy rows | Same — backfill folds NULLs into the dominant model. Logged. |
| Already-heterogeneous install (rare; legacy unnoticed swap) | Loud `EmbeddingLockError` on first session with `.recovery`. This is the failure we're surfacing — corruption the user already has. |
| Fresh install on 0.15.0 | Lock set on first dream. No backfill needed. |

CHANGELOG note: under `### Changed`, flag the heterogeneous-install behaviour
change with the migration recipe.

---

## Long-term effects

- **`system_config` pattern stays small.** Four invariants total
  (`peer_identity`, `total_active_hours`, `embedding_model_lock`,
  `embedding_dimensions_lock`). Watch for growth past a handful.
- **Multi-model future is unlocked, not closed.** Per-row column already
  supports hybrid retrieval; the lock is just the write-default.
- **The wrapping pattern** ("wrap services that touch versioned state")
  becomes a template. Reusable for future similar concerns.

---

## Principles check

| Principle | Status |
|---|---|
| SIMPLE | ~320 lines total; one enforcement point; two config keys. |
| ELEGANT | Per-row column is the truth; wrapper is single-purpose; session-scoped verification matches existing lifecycle. |
| FLEXIBLE | Per-row column supports any future multi-model design without refactor. |
| ROBUST | Race-free (atomic), session-scoped (catches external changes), backfill handles legacy. |
| Functional Python | Wrapper has one mutable field; rest is pure functions in `db/queries.py`. |
| Fail fast | `EmbeddingLockError` at boundary; no defensive code in business logic. |
| Agent-first contract | `.recovery` on exception; idempotent across sessions; doctor non-raising. |
| Progressive disclosure | Zero-config installs unaffected; existing installs auto-backfill; explicit action only for heterogeneous or intentional model change. |

---

## Origin

This plan was synthesised from a structured debate between two reviewing
agents (logic-checker and consequences-reasoner) plus elf making calls,
then simplified through a principles re-audit. Source of the original
bug: Dmitry's month-of-production-use follow-up on
[issue #50](https://github.com/emson/elfmem/issues/50).
