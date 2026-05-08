# Deep-sleep rescoring (v0.13.3)

**Status:** approved · **Owner:** elf · **Target release:** v0.13.3

## Intent

elfmem already has three rhythms: heartbeat (`learn`), breathing (`dream`),
sleep (`curate`). This plan adds a fourth rhythm — **deep sleep**: the
periodic re-evaluation of *existing* active blocks against the agent's
*current* identity.

The principle this serves:

> **My identity evolves — it is the living summary of what works.**

A block scored against SELF blocks at promotion time becomes stale as the
agent's identity drifts. Without rescoring, the knowledge graph holds
historical alignments against a current identity. Rescoring keeps memory
honest about the agent that owns it.

The principle this enforces:

> **Memory health is observable and actionable.** Doctor surfaces drift;
> one command (`dream --rescore`) is the action; ordering by
> `last_scored_at ASC` ensures progressive coverage without manual
> targeting.

## Problem

Today `consolidate()` processes inbox blocks once. Their LLM-derived
metadata (alignment score, summary, inferred tags, contradiction
checks) is fixed at promotion time. Three failure modes follow:

1. **LLM-timeout fallback creates a one-way door.** When the LLM call
   times out during consolidate, the block is promoted with neutral
   metadata (`alignment=0.5`, `summary=None`, `tags=[]`). The fallback
   docstring claims it'll be "re-scored on next consolidation if the LLM
   recovers" — but this is false. Consolidate only processes inbox
   blocks; once promoted to active, the block is never re-scored.

2. **No CLI escape from the LLM dependency.** Bulk ingest, disaster
   recovery, offline use, and cost-sensitive batches all want a way to
   skip LLM calls entirely. The Python API exposes `skip_llm=True` on
   `consolidate()`/`dream()` but the CLI doesn't surface it. Operators
   reach past the CLI to the API to make progress when the LLM is slow.

3. **Identity drift is invisible.** As the agent customises its
   constitution and adds peer relationships, the alignment of older
   blocks against the *current* SELF may diverge silently. There's no
   metric for this and no mechanism to act on it.

## Design

### Three CLI flags on `dream`

```
dream                        # unchanged: process inbox with LLM
dream --no-llm               # promote without LLM; mark blocks for later
dream --skip-contradictions  # LLM scoring + summary, skip O(n²) contradictions
dream --rescore [--max N]    # rescore unscored + oldest aged blocks
```

`--rescore` and `--no-llm` are mutually exclusive (rescore needs LLM
by definition). The CLI rejects the combination.

`--max` overrides `consolidation.rescore.max_per_run` from config.

### One column, no redundant tags

Schema migration v2 → v3 adds:
```sql
ALTER TABLE blocks ADD COLUMN last_scored_at TEXT;
```

Backfill on migration:
```sql
UPDATE blocks SET last_scored_at = created_at WHERE last_scored_at IS NULL;
```

Existing blocks treated as "scored at creation" (synthetic but
conservative — oldest blocks become first rescore candidates, which is
the right default).

`--no-llm` consolidations and LLM-timeout fallbacks set
`last_scored_at = NULL` on promotion. NULL is the unambiguous "needs
scoring" signal.

A separate `system/llm-pending` tag is **not** introduced — `NULL` is
sufficient and avoids a second source of truth.

### One eligibility filter, used everywhere

```python
def is_rescore_eligible(block, config) -> bool:
    if block.status != "active":
        return False
    if block.category in config.exclude_categories:
        return False  # message, mind, decision, prediction
    if block.source_peer is not None:
        return False  # peer perspectives stay intact
    if any(tag in config.exclude_tags for tag in block.tags):
        return False  # system/no-rescore
    if block.last_scored_at is not None:
        # NULL always eligible (debt drains first); otherwise apply cooldown
        if hours_since(block.last_scored_at) < config.min_age_hours:
            return False
    return True
```

Single function. Same shape governs both `--rescore` selection and
doctor's drift counting.

### One selection query

```sql
SELECT id FROM blocks
WHERE status = 'active'
  AND category NOT IN (:exclude_categories)
  AND source_peer IS NULL
  AND id NOT IN (SELECT block_id FROM block_tags WHERE tag = 'system/no-rescore')
  AND (last_scored_at IS NULL
       OR last_scored_at < datetime('now', :min_age_clause))
ORDER BY (last_scored_at IS NULL) DESC, last_scored_at ASC
LIMIT :max
```

NULL last_scored_at sorts first (drains debt). Then oldest-by-scored-at
ascending (progressive rotation — every block leaves the front of the
queue once rescored).

### Rescoring is an extension of consolidate, not a new operation

Modify `consolidate()` to accept an optional `rescore_block_ids` list.
Inbox processing happens first (existing path); rescore processing
happens second, using the same `_process_block` machinery. On success:
- `last_scored_at = now`
- New summary/embedding/tags/alignment from LLM
- Contradiction check against current evolving_vecs (not skipped on rescore — drift detection IS the point)

### Doctor: drift surface

Three observable metrics from one query:
- `unscored` = COUNT WHERE last_scored_at IS NULL AND eligible
- `stale` = COUNT WHERE last_scored_at < (now - target_max_age_days) AND eligible
- `total_active` = COUNT WHERE status='active' AND eligible

Drift threshold:
```
drift = unscored + stale
warn_if drift > max(drift_warning_count, total_active * drift_warning_percent / 100)
```

Auto-scaled recommendation:
```
recommended_max = max(20, ceil(drift, 50))   # round up to nearest 50, floor 20
suggestion = f"elfmem dream --rescore --max {recommended_max}"
```

Output format:
```
✓  Scoring drift     0 unscored, 8 stale (>90d, 5.6%)
✗  Scoring drift     47 unscored, 89 stale → elfmem dream --rescore --max 150
```

### Configuration

```yaml
consolidation:
  rescore:
    enabled: true
    max_per_run: 20                 # --rescore default budget
    min_age_hours: 24               # don't churn freshly-scored
    target_max_age_days: 90         # blocks older are considered stale
    drift_warning_count: 25         # absolute floor for warning
    drift_warning_percent: 25       # percentage of total active for warning
    exclude_categories:
      - message
      - mind
      - decision
      - prediction
    exclude_tags:
      - system/no-rescore
```

7 knobs (down from 9 in earlier draft). All have sensible defaults.

## When to use what (USE / DON'T USE)

```
USE WHEN — choose the mode that fits the moment:

  Default (no flags):       Standard consolidation after a learn batch.
                            The right choice for almost every call.

  --no-llm:                 LLM is unavailable, slow, or unaffordable.
                            Bulk ingestion (peer dumps, document chunks,
                            backups). Disaster recovery. Affected blocks
                            are tagged for later catch-up via --rescore.

  --skip-contradictions:    Large structured ingestion where contradictions
                            are unlikely (signed exports, trusted bundles).
                            Keeps LLM scoring; saves O(n²) contradiction loop.

  --rescore:                Catch-up + light deep work. Processes all
                            unscored blocks plus N oldest active blocks.
                            Run after --no-llm; run periodically for hygiene.

  --rescore --max N:        Manual budget. Use a large N (e.g. 1000) for a
                            one-shot full sweep ("deep sleep").

DON'T USE:

  --no-llm by default.      Neutral alignment + raw-content embeddings
                            degrade SELF-frame coherence and recall on
                            long blocks. Reserve for outages, bulk loads,
                            or cost-sensitive batches.

  --no-llm in tight loops without follow-up rescore. Quality debt
  accumulates; the SELF frame becomes uniform.

  --rescore on a hot DB during heavy use. Rescore takes a write lock per
  block; concurrent recall sees momentary staleness. Schedule during
  quiet windows.
```

## Phases (one PR)

| # | Scope | Tests |
|---|-------|-------|
| 1 | Schema v3: `last_scored_at` column + backfill on migration | migration v2→v3 backfills correctly; row-count-validated backup taken |
| 2 | `consolidate()` records `last_scored_at` on success; sets NULL when `skip_llm=True` or LLM timeout | block has `last_scored_at` after consolidate; NULL after `skip_llm=True`; NULL after fallback |
| 3 | `dream --no-llm` + `--skip-contradictions` CLI flags | flags forward correctly; affected blocks have NULL `last_scored_at` |
| 4 | `select_rescore_candidates()` pure function; eligibility filter | each filter branch; NULLs sort first; ordering rotates progressively |
| 5 | `dream --rescore [--max N]` CLI flag; rescore execution path | inbox + rescore both processed; NULL cleared after rescore; min_age_hours respected |
| 6 | Doctor drift check + auto-scaled recommendation | healthy/drift/no-suggestion branches; recommendation math |
| 7 | Docs: `dream()` USE/DON'T USE; README "Deep sleep" subsection; AGENT.md fragment regen | n/a |

## Edge cases

| # | Edge case | Behaviour |
|---|-----------|-----------|
| 1 | `--rescore` + `--no-llm` passed together | CLI rejects: "rescore requires LLM" |
| 2 | Rescore alignment drops below threshold | confidence resets to 0.5; documented in dream output (changes printed) |
| 3 | Rescore re-introduces a contradiction | flagged again; archival is one-way (never un-archive) |
| 4 | Embedding regeneration invalidates edges | edges become slightly stale; defer `--rebuild-edges` to v0.13.4 |
| 5 | Process killed mid-rescore | per-block transactions; oldest-first ordering picks up where left off |
| 6 | `--rescore` with empty queue | no-op cleanly; returns zero counts |
| 7 | `min_age_hours` blocks all candidates | warn user "no candidates within budget; reduce min_age_hours or wait" |
| 8 | Concurrency (two dreams racing) | SQLite serialises; small risk of double-rescore in race; cost is wasted LLM calls, not corruption |
| 9 | Massive backlog (10k unscored after migration from 0.13.x) | recommendation scales: `--max max(20, ceil(drift, 50))`; user sees `--max 10000` and decides |
| 10 | Peer-imported blocks | `source_peer IS NOT NULL` filter excludes; peer perspectives intact |
| 11 | Constitutional blocks | included in rescoring (self-aligned by construction; cheap) |
| 12 | mind/decision/prediction blocks | excluded by category (structured artefacts validated by lifecycle, not LLM) |
| 13 | message blocks | excluded by category (events, not knowledge claims) |

## What's deferred to a later patch

- **`--rebuild-edges` flag**: rescoring updates summary embedding; edges
  built from old embedding become stale. Cost of rebuild is O(N²);
  significant. Defer to v0.13.4 with explicit flag.
- **`--rescore-if-needed` ergonomics**: pre-commit hooks, scheduled
  jobs that no-op when memory is healthy. Useful but not foundational.
- **Slack/email drift notifications**: out of scope; user's monitoring
  infrastructure.

## Code surface estimate

~600 LOC total:
- 80 LOC schema migration
- 100 LOC eligibility/selection logic
- 100 LOC consolidate path mods (rescore_block_ids parameter)
- 100 LOC CLI flag wiring
- 80 LOC doctor checks
- 100 LOC config + tests for new fields
- 50 LOC docs

One PR. Each phase independently testable. Final test count target:
~870 (current 818 + ~50 new).

## Risk assessment

| Risk | Mitigation |
|------|------------|
| Schema migration corrupts existing DB | v0.13.1's row-count-validated backup machinery covers this |
| Rescoring degrades agent identity coherence | edge case 2: confidence resets to 0.5 if alignment drops; documented |
| Users hammer `--rescore` and saturate LLM provider | `--max` budget; `min_age_hours` cooldown; `enabled: false` config kill switch |
| `--no-llm` becomes chronic | doctor surfaces unscored count; suggestion always shown; documentation discourages default use |
| Selection logic bugs cause same blocks to be rescored repeatedly | tests verify rotation; `last_scored_at ASC` ordering is self-correcting |

## The principle (in code)

The render-path docstring established the pattern in v0.13.2:
> *"Authoritative state is read, never inferred. Config is truth;
> defaults are bootstrap only on first install."*

For deep-sleep rescoring the principle is parallel:
> *"Memory health is observable and actionable. The doctor measures;
> the action (`dream --rescore`) heals; ordering by `last_scored_at ASC`
> ensures progressive coverage without manual targeting. Memory tends
> toward consistency under normal use, like physical hygiene tends
> toward homeostasis."*

This goes in the docstring of `select_rescore_candidates()` and the
doctor health-check helper, so the next contributor reads the rule
before touching the code.
