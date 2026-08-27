# 0013 — Substrate cutover as a second migration step

**Status**: Accepted
**Date**: 2026-08-27
**Deciders**: elf (curator), Ben
**Supersedes**: the "explicitly not built" scope boundary in
[ADR 0011](0011-substrate-migration-as-a-migrate-step.md)

## Context

ADR 0011 built Phases 0–4 of the substrate migration (backup → export +
ledger seed → rebuild → parity gate) and deliberately stopped there, because
Phase 6 ("flip authority") "needs real re-wiring of `learn()`/`edit()`/
`forget()` at the API level, not a migration script."

That re-wiring has since landed. `MemorySystem._files_authoritative` now
gates file-native `append_block` / `edit_block` / `forget_block` /
`reconcile_status` / `sync_tags`, and this project has been running
`files_authoritative: true` against its own corpus since 2026-08-25. The
reason for the deferral no longer holds, and its absence had become the
gap users actually hit: `apply` left them with verified files, no documented
next step, and a config flag documented nowhere.

Two things were measured before deciding, rather than assumed:

- **Round-trip fidelity.** A corpus exercising learn / consolidate / outcome
  / connect / mind_create / mind_predict / mind_outcome / edit / forget /
  curate, exported and rebuilt from files + ledger: **zero field drift**
  (confidence, α/β, decay_lambda, reinforcement_count, cue, status,
  category), **zero tag drift**, edges preserved. The only block not rebuilt
  is the archived one — deliberate, and it is still present in
  `archive/<category>.md`.
- **Rollback cost.** Under file authority the database row is still written
  on every operation (`learn()` inserts first, then appends to the file, and
  archives the row if the file write fails). The database therefore never
  falls behind the files, so flipping back leaves a complete, current
  database.

## Decision

A second step kind, `"substrate_cutover"`, in the same
`MigrationStep`/`StepApplyResult` model — discovered by `migrate status`,
executed by `migrate apply`, reversed by `apply --undo --id`.

The two substrate steps are **mutually exclusive by construction**:
`scan_substrate` offers the export while one is outstanding and stops once a
current one is recorded; `scan_cutover` only offers itself after that. It is
therefore not possible to be offered a flip onto a stale snapshot.

`cutover_preflight()` gates the flip on: export applied, parity passed,
corpus fingerprint unchanged since export, project-local config present, not
already flipped, and — the ones that matter most — `memory/` and `ledger/`
both tracked by git *and* committed.

The git checks are not ceremony. Under file authority, git history is the
only undo for `forget()` and `edit()`. They also catch a silent failure the
inner `.elfmem/.gitignore` cannot: a repository-root `.gitignore` with a
blanket `.elfmem/` rule silences that file's negations entirely, because git
does not descend into an excluded directory to read them.

Preflight **reports every failure at once** rather than short-circuiting.
The first integration friction report's headline complaint was "each fix
revealed the next gate"; a preflight that stops at the first failure
reproduces exactly that, one round trip per problem.

The config edit is a surgical line replacement, not a yaml load/dump
round-trip: the generated config is mostly comments explaining why each
value is what it is, and pyyaml would discard all of them.

## Alternatives considered

1. **`migrate apply --cutover` flag.** Rejected: `migrate status` would
   never mention it, so the step that completes the migration would be the
   one users cannot discover — the same invisibility the friction reports
   were about.
2. **Fold cutover into the export step.** Rejected: flipping authority is a
   decision, and bundling it would mean an export could never be run as a
   rehearsal. The export's value is that it is safe to run before deciding.
3. **Have cutover re-export first, so it always has fresh files.** Rejected:
   it would hide a real signal. A drifted fingerprint means the agent kept
   working after the export, and the user should see that and re-run the
   export deliberately.

## Consequences

- The migration is now complete end to end through commands users already
  know, and `migrate status` narrates the whole arc: export pending →
  cutover pending → nothing pending.
- `substrate.files_authoritative` is documented in `SETUP_AND_CONFIG.md`
  with an explicit warning against setting it by hand on a populated
  database, since doing so moves no data and only changes which copy is
  believed.
- Rollback is a config flip with no data loss, which makes cutover a
  genuinely reversible decision rather than a one-way door.
- Fixed along the way: `elfmem migrate` resolved the database path from
  `ProjectInfo.db` — which *infers* `~/.elfmem/databases/<project>.db` from
  the project name rather than reading `project.db` from the config. Any
  project whose config pointed elsewhere was scanned at a path that did not
  exist, so migration reported "No migrations pending" while the real corpus
  sat untouched. It now uses the same resolution chain as `doctor`. This is
  the authoritative-state-vs-inferred-default failure class again (v0.13.3
  path resolution); read the configured value, never re-derive it.
