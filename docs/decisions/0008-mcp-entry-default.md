# 0008 — MCP entry generation default, drift detection, and migration

**Status**: Accepted
**Date**: 2026-07-05
**Deciders**: Ben Emson, elf

## Context

`mcp_json_snippet()` (`src/elfmem/project.py`) is the function `elfmem init`
prints and embeds into a project's `CLAUDE.md`/`AGENTS.md` managed section —
the JSON block a user pastes into a Claude Code MCP config to give an agent
persistent memory. Until this ADR it generated:

```json
{"mcpServers": {"elfmem": {"command": "elfmem", "args": ["serve", "--config", "<path>"]}}}
```

Two independent, real incidents traced to this shape:

1. **Wrong-project drift, this repo's own dev instance.** elfmem's own
   `~/.claude.json` entry for this project had drifted to point at an
   unrelated global config/db (`~/.elfmem/config.yaml`, `~/.elfmem/agent.db`)
   with no `peer:` section — silently breaking peer messaging. Root cause:
   `elfmem` only exists at `.venv/bin/elfmem` in this project's own
   `uv`-managed dev venv, not on any PATH the spawning process reliably
   inherits, so a bare `"elfmem"` command was never the actual problem here —
   the entry had been hand-edited at some point to a stale absolute path and
   nothing detected the drift.
2. **Silent missing credentials, a sibling elf instance in another project.**
   A peer report (`elf:alv`, 2026-07-05) found that project's MCP server ran
   in mock mode indefinitely because `.mcp.json` launched it without any
   mechanism to deliver `ANTHROPIC_API_KEY` to the spawned subprocess — no
   error, just silent degradation.

Both share a root cause: **the generated snippet assumes an execution
context (PATH resolution, inherited shell environment) that an MCP-spawned
subprocess does not reliably have**, and nothing in elfmem validates that
assumption after the fact.

Existing, reusable infrastructure was found already in place
(`src/elfmem/migrate.py`, `tests/test_mcp_migrate.py`, ~30 tests): a
`MigrationFinding`/`MigrationStep`/`MigrationPlan` model with atomic
tmp+`os.replace()` writes, automatic timestamped backups, a sha256
staleness gate, idempotent skip, and dry-run — wired into
`elfmem doctor --migrate-mcp` (read-only preview) and
`elfmem migrate {status|plan|apply}` (writes, behind confirm/`--yes`).
Its detection logic (`_suggest_entry()`) only handled two things: renaming
two deprecated env var names, and rewriting a legacy `python -m elfmem.mcp`
invocation. It had no notion of "is this entry correct for *this* project" —
and its `DEFAULT_SCAN_PATHS` didn't even include the real global
`~/.claude.json` (Claude Code's actual per-project MCP config file), nor
could its scanner parse that file's nested
`{"projects": {<path>: {"mcpServers": {...}}}}` shape — it only understood a
flat top-level `mcpServers` key (the `.mcp.json` shape). So the existing
migration tool could not have caught either incident above, even though its
surrounding pipeline is sound.

A design-space simulation (optimize intent, 12 adversarial scenarios: varied
install contexts, missing `.env`, deliberate cross-project config sharing,
Windows paths, concurrent writes, CI non-interactivity — see session
transcript) was run before this decision to avoid picking a design that
solves the two known incidents but reintroduces a new failure mode.

## Alternatives considered

**A — Branch on detected install context** (project venv → `uv run elfmem`,
else bare `elfmem`). *Rejected*: requires a heuristic ("is `sys.prefix`
under the project root?") that itself needs edge-case handling for atypical
venv layouts, adding a magic-number-shaped branch to maintain for something
a simpler rule solves outright. Violates "no magic numbers" / SIMPLE.

**B — Always prefix with `uv run --env-file .env`.** *Rejected*: assumes
every downstream project uses `uv` and keeps a project-root `.env`, neither
of which elfmem can assume for consumers who `pip install elfmem` without
`uv`, or who export credentials at the shell/process-supervisor level
instead.

**C — Embed literal secret values directly in the generated `"env"`
object.** *Rejected outright*, not just deprioritized: `.mcp.json` is
explicitly a git-shared, team-visible convention (per Alv's report on the
sibling incident) — baking API keys into it is a credential-leak vector.
Even for `~/.claude.json` (not git-shared), a uniform "never embed secrets,
regardless of which file this ends up in" rule is simpler and safer than a
per-file-location exception.

**D — Detect "wrong project" by deep-comparing config *content*.**
*Rejected*: comparing YAML content (name, db, identity) is fragile against
legitimate per-deployment config variation and doesn't distinguish "stale
pointer" from "user intentionally customized this project's own config."
The simpler, structurally-guaranteed signal is available for free: the real
`~/.claude.json` already tells you which project an entry belongs to via
its `projects[<path>]` dict key, so "does `--config`'s resolved path equal
`get_project_info(Path(path)).config`'s resolved path" is a path-identity
check, not a content diff — no new function, reuses `get_project_info()`.

**E — Auto-apply the fix silently during `elfmem init --refresh` or MCP
server startup.** *Rejected*: violates the existing, deliberately
conservative `migrate.py` UX (preview → explicit `--yes` confirm → backup
always taken). A user who has a rare, intentional reason to point one
project's entry at another's config would have it silently rewritten with
no chance to decline. Detection stays permissive-but-visible; only
`elfmem migrate apply` (already gated) writes.

## Decision

1. **New default command shape**: `mcp_json_snippet()` resolves the
   absolute path of the currently-running `elfmem` executable
   (`shutil.which("elfmem") or sys.argv[0]`, then `Path(...).resolve()`) and
   uses that as `command`, instead of a bare `"elfmem"` string. This is a
   single, uniform rule — no install-context branching — because an
   absolute path is spawn-correct regardless of whether `elfmem` came from
   a project-local `uv` venv, a global `uv tool`/`pipx` install, or an
   activated `pip` venv: subprocess spawning with an absolute path never
   consults `PATH`.
2. **Env var delivery**: `elfmem serve` gains a new `--env-file PATH`
   option (`src/elfmem/cli.py`), backed by a new pure function
   `parse_env_file()`/`load_env_file()` in `project.py` — a minimal
   dotenv-style `KEY=VALUE` parser (comments, blank lines, quoted values),
   applied via `os.environ.setdefault()` so real environment variables
   always take precedence over the file. `mcp_json_snippet()` appends
   `--env-file <abs path>` only when a `.env` file actually exists at the
   given `project_root` — never fabricated, and never embedding literal
   key values in the JSON output. This makes `--env-file` a real,
   documented `elfmem serve` flag rather than relying on `uv run`'s
   own built-in flag of the same name, which only exists when the spawned
   `command` is `uv` itself — true in this repo's dev convention, not
   guaranteed for consumers.
3. **Drift detection**: extend `migrate.py`'s per-entry issue list with a
   new check, scoped to entries found under a `projects[<path>]` key: does
   the entry's `--config` argument resolve to the same file as
   `get_project_info(Path(path)).config`? If not, add an issue and a
   suggested fix (regenerate via the same logic as `mcp_json_snippet()`).
   Composes with the existing env-var-rename and legacy-invocation checks
   on the same `MigrationFinding` — no new pipeline.
4. **Scan-path/shape fix**: add `~/.claude.json` to `DEFAULT_SCAN_PATHS`;
   extend the scanner to also walk the nested
   `data["projects"][path]["mcpServers"]` shape in addition to the existing
   flat `data["mcpServers"]` shape (used by `.mcp.json`), producing the same
   `MigrationFinding`/`MigrationStep` model either way — only the
   `json_pointer` differs (`/mcpServers/<name>` vs.
   `/projects/<path>/mcpServers/<name>`), which the existing `json_pointer`
   field on `MigrationStep` already accommodates.
5. **Rollout**: additive only. `elfmem doctor --migrate-mcp` picks up the
   new checks in its existing read-only preview; `elfmem migrate apply`
   remains the only write path, unchanged confirm/`--yes`/`--dry-run`
   gating. No change to default `doctor` output for users with no drift.

## Consequences

- Existing entries generated by the old snippet keep working exactly as
  before — nothing here changes runtime behavior for a correctly-wired
  entry. Only entries that are actually wrong (bare command unresolvable,
  or `--config` pointing at a different project) get flagged, and only
  `migrate apply` (explicit, confirmed, backed up) changes them.
- A user with no `.env` at their project root gets a snippet with no
  `--env-file` flag — this is a visible gap (no flag present, greppable),
  not a silent one, but it does not by itself prove the *served* process
  has working credentials if they're relying on shell-inherited env vars
  that happen to be absent. Closing that residual gap (per Alv's own
  suggestion: a doctor check that verifies the *served* process actually
  has reachable credentials, not just that a config file exists) is
  explicitly **not** done here — recorded as the direct follow-up trigger.
- The rare case of a user deliberately pointing one project's entry at
  another project's config will surface as a `migrate status` finding.
  Cost is a one-time "review this diff" prompt, not a silent rewrite —
  acceptable per the existing conservative migration UX.
- Windows path resolution for the executable-discovery step
  (`shutil.which`/`sys.argv[0]`) has more platform variance than
  `Path.resolve()` alone; flagged as a residual risk from the design
  simulation, addressed with an explicit test rather than an assumption.

## Post-implementation review findings

An adversarial edge-case pass across both new code paths, before merge,
found and fixed two real bugs the simulation's scenario set didn't surface,
and identified one accepted residual risk:

1. **`_resolve_elfmem_command()`'s fallback resolved the wrong directory.**
   The first implementation called `Path(sys.executable).resolve().parent`
   before checking for a sibling `elfmem` console script. A venv's `python`
   is itself a symlink to a shared interpreter (confirmed against this
   repo's own uv-managed venv: `.venv/bin/python3` → uv's centrally-managed
   toolchain under `~/.local/share/uv/python/...`). Resolving it *before*
   taking the parent lands in that shared toolchain directory, which has no
   `elfmem` console script — the exact bug this fallback exists to avoid,
   just relocated. Fixed by taking `sys.executable`'s parent unresolved
   (the venv's own `bin/`, where `elfmem` actually lives), only resolving
   the final sibling path once found. Caught by a test that failed on first
   run (`test_falls_back_to_executable_sibling_when_which_fails`) — direct
   confirmation the review methodology worked, not a theoretical concern.
2. **Relative `--config` values resolved against the wrong base.** The
   drift check's `Path(cfg_arg).resolve()` resolved a relative `--config`
   value against the *scanning* process's cwd (wherever `elfmem
   migrate`/`doctor` happens to be invoked from), not the project root the
   entry is actually spawned in by Claude Code. Fixed to join a relative
   `cfg_arg` onto the known `project_root` before resolving — matching how
   `elfmem serve --config <relative>` would actually behave when spawned.
3. **Accepted residual risk, not fixed here: whole-file sha256 hash gate on
   `~/.claude.json`.** `_apply_file_group`'s staleness check hashes the
   *entire* source file; `~/.claude.json` is a shared, actively-written file
   (Claude Code itself updates per-session bookkeeping fields — `numStartups`,
   `lastCost`, `lastAPIDuration`, etc. — for every project, not just the one
   being migrated). A `migrate plan` → `migrate apply` gap that spans another
   Claude Code session boundary risks the whole batch (potentially many
   unrelated projects' fixes) coming back `"stale"`, even when the specific
   entry being fixed didn't change. This fails *safe* (a stale rejection
   just asks the user to re-plan; it never corrupts data or applies a wrong
   fix), so it's a UX friction risk, not a correctness risk. Not fixed in
   this change: doing so properly means scoping the hash check to the
   JSON-pointer-targeted subtree instead of the whole file, which touches
   the core safety mechanism shared by every migration kind, not just this
   one — a larger, separate change. **Trigger to revisit**: a real user
   reports `migrate apply` against `~/.claude.json` failing as stale more
   than rarely.

## References

- `src/elfmem/project.py` — `mcp_json_snippet()`, `_resolve_elfmem_command()`,
  `parse_env_file()`/`load_env_file()`
- `src/elfmem/cli.py` — `serve --env-file`, `init` call sites
- `src/elfmem/migrate.py` — `_suggest_entry()`, `scan_file_with_warnings()`,
  `DEFAULT_SCAN_PATHS`
- `tests/test_mcp_migrate.py`, `tests/test_project.py`
- Peer message from `elf:alv`, 2026-07-05 — cross-project MCP env-wiring
  report that named the same failure category
- CHANGELOG.md `[Unreleased]` — user-facing summary
