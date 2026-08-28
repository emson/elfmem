# 0011 — Substrate migration folded into `elfmem migrate`, cutover deferred

**Status**: Accepted
**Date**: 2026-08-12
**Deciders**: elf (curator), Ben

## Context

`docs/plans/plan_v2_substrate_reevaluation.md` §8 designed a 6-phase migration
from the DB-native corpus to the markdown file substrate (Phases 0-2: backup
and export; Phase 3: rebuild; Phase 4: retrieval-parity gate; Phase 5:
hand-restore constitutional roles; Phase 6: flip authority). Phases 0-4
already existed as separate CLI primitives (`elfmem export --to-markdown`,
`elfmem index rebuild`, `elfmem index parity`) before this decision, each
independently safe (read-only against the live database), but running them
in sequence for a real migration was a manual, undocumented procedure — the
only worked example was a hand-run dry-rehearsal script kept in session
scratch (`docs/plans/v2_substrate/plan/dry_run_2026-08-10.md`), not a
committed, repeatable command.

That dry run, against a locked-down copy of this project's own production
corpus (185 blocks), also surfaced two real defects in `index_rebuild.py`
not previously disclosed by the plan's own residual-risks list:
`_write_block()` hardcoded every rebuilt block's `category` to `"knowledge"`
regardless of source file, which would silently break `mind_list()` /
`mind_show()` / `ls(category=...)` for every Theory-of-Mind and message
block after a rebuild; and `confidence`/`alpha`/`beta` were written into the
exported frontmatter by `export.py` but never read back by the rebuilder,
so every rebuilt block reset to the neutral default regardless of how much
evidence it had actually accumulated.

The user asked for "a simple command that elfmem knows how to run and will
migrate itself" — explicitly asking to reuse whatever the project's existing
migration UX already was, not to design a new one.

## Alternatives considered

1. **A new `elfmem migrate substrate {status,plan,prepare,verify,rollback}`
   subcommand group**, mirroring the phase structure directly. Rejected:
   the project already has `elfmem migrate {status,plan,apply}` for Claude
   MCP config drift, with the exact properties this migration also needs
   (plan-then-apply, automatic backup, atomic writes, `--dry-run`, `--yes`,
   idempotent re-runs). A parallel command surface would ask users to learn
   a second vocabulary for the same shape of problem.
2. **Extend `elfmem migrate` with a new `substrate_export` step kind**,
   discovered by `elfmem migrate status`/`plan` alongside config-drift
   findings and executed by `elfmem migrate apply` through kind-based
   dispatch. **Chosen.**
3. **Also build "cutover"** (Phases 5-6: hand-restore constitutional roles,
   re-point `MemorySystem.edit()`/`.forget()`/`.ls()`/etc. at file-native
   operations) as part of the same step. Rejected: this needs real
   engineering at the API layer (`docs/plans/v2_substrate/plan/build-plan.md`
   units U-006/U-007), not a migration script — U-003's own build notes
   record the same conclusion when it hit the identical trap ("re-pointing
   the live API now would silently split what the API returns from what's
   actually authoritative"). Doing it here would produce a migration that
   claims more than it delivers.

## Decision

One new step `kind`, `"substrate_export"`, added to the existing
`MigrationStep`/`MigrationPlan`/`StepApplyResult` model in `src/elfmem/migrate.py`:

- **`scan_substrate()`** (read-only): computes a content-aware fingerprint
  of the live corpus — `sha256` over each block's `(id, status, category,
  sorted tags, sha256(content))`, not a raw file-byte hash, since SQLite's
  own WAL/page-cache churn would false-positive a byte hash the way a JSON
  config file never does. Pending when the database has content and either
  no marker (`.elfmem/.substrate-migration.json`) exists yet, or the current
  fingerprint no longer matches the one recorded at the last apply.
- **`apply_substrate_step()`**: `VACUUM INTO` backup (validated by a
  post-backup row-count comparison) → `export_to_markdown()` → `rebuild_index()`
  to a *new* `.elfmem/index.db` → `check_retrieval_parity()` against the
  original, on the four frame-level queries (`self`/`attention`/`task`/
  `simulate`, no embedding calls needed since `query=None`). Never opens
  the live database for anything but reads. `--dry-run` runs the identical
  pipeline against scratch temp paths and discards the result.
- **`undo_substrate_step()`**: removes the generated `.elfmem/memory/` and
  `index.db` and the marker. Refuses (unless `--force`) if a
  files-content fingerprint recorded at apply time no longer matches what's
  on disk — protects hand-edits made to the exported files since migration.
  Safe by construction: nothing about undo touches the live database, so
  there is nothing destructive to have caused.
- `index_rebuild.py`'s two defects fixed as a prerequisite: category is now
  derived from the source filename (`notes/<category>.md` → that category),
  and `confidence`/`success_count`/`failure_count` are read back from each
  block's frontmatter `extra` fields, falling back to the existing neutral
  default only when absent or malformed.
- `elfmem migrate status`/`plan`/`apply` gain optional `--db`/`--config`
  (previously config-drift scanning didn't need per-project targeting);
  `apply` gains `--undo`/`--force` for the rollback path. No other user-facing
  vocabulary changes — the same three verbs now cover both migration kinds.

**Explicitly not built**: Phases 5-6 (cutover). `apply` stops at "exported
and verified," and says so in its own output — the live agent keeps reading
the original database regardless of the parity result. A failed parity gate
is reported, not hidden, but is informational rather than a rollback
trigger, since there is nothing to roll back from (nothing was cut over).

## Consequences

- Users get the migration through a command they already know
  (`elfmem migrate status/plan/apply`), with no new top-level surface.
- Rollback is safe by construction rather than by careful bookkeeping:
  every write in the real-run path lands in a new file (the backup,
  `.elfmem/memory/`, `index.db`); the live database is only ever read.
  `undo_substrate_step()` exists for convenience and hand-edit protection,
  not because anything destructive needed reversing.
- The `index_rebuild.py` fixes are load-bearing for *any* future use of the
  rebuild path, not just this migration — `elfmem index rebuild` and
  `elfmem index parity` both inherit the corrected category/evidence
  round-trip immediately.
- `apply_plan()`'s existing sync code path (Claude MCP config JSON
  patching) is untouched; the new step kind dispatches separately in
  `cli.py` rather than being folded into `_apply_file_group()`, which has
  no notion of "backup a whole database and run an async pipeline." A
  latent bug in that dispatch (`only=()` — an empty tuple — is falsy in
  Python, so `apply_plan`'s `only else None` fallback silently meant
  "apply everything" when there were zero config steps to filter to) was
  found via a real CLI smoke test, not unit tests, and fixed by guarding
  the call rather than changing `apply_plan`'s existing, already-tested
  semantics.
- Full architectural cutover (Phases 5-6, U-006/U-007) remains a distinct,
  larger piece of future engineering. Nothing here blocks it; the marker
  file and fingerprint scheme this ADR introduces are designed to keep
  working once cutover exists (a completed `substrate_export` apply is
  the natural precondition for it).

## Trigger to revisit

When U-006/U-007 (flip authority) are actually built: cutover will need its
own ADR, but should reuse this one's marker file / fingerprint mechanism
as its precondition check rather than inventing a second one.

## References

- `docs/plans/plan_v2_substrate_reevaluation.md` §8 — the original 6-phase
  migration design
- `docs/plans/v2_substrate/plan/build-plan.md` — units U-002/U-004/U-005
  (built, reused here), U-006/U-007 (not built, explicitly deferred)
- `docs/plans/v2_substrate/plan/dry_run_2026-08-10.md` — the rehearsal that
  found both `index_rebuild.py` defects and validated the parser fix from
  the same session
- `src/elfmem/migrate.py` — `scan_substrate`, `apply_substrate_step`,
  `undo_substrate_step`, `build_full_plan`
- `src/elfmem/memory/index_rebuild.py` — category and confidence/α/β fixes
- `tests/test_substrate_migrate.py`
