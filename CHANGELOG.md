# Changelog

All notable changes to elfmem are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
elfmem uses [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

### Fixed
- **`contradictions_detected` now surfaced on `ConsolidateResult`** (closes
  [#50](https://github.com/emson/elfmem/issues/50) item 1). When contradiction
  detection ran, the LLM-detected pairs were inserted into the contradictions
  table — but the count never reached the result object. `to_dict()` returned
  only `processed/promoted/deduplicated/edges_created`, so MCP clients and CLI
  consumers couldn't tell whether the flagship contradiction-detection feature
  had fired. New field `ConsolidateResult.contradictions_detected: int = 0`
  carries the per-call LLM verdict (above-threshold pairs detected this batch,
  not a cumulative DB row count); `to_dict()` includes it; `summary` surfaces
  it when non-zero. `AgentGuide` entries for `dream` / `consolidate`, the MCP
  `elfmem_dream` docstring, and `docs/dreaming_architecture.md` JSON example
  updated to list the new field.

  **Follow-up flagged**: contradiction detection only fires in a narrow
  similarity band (`0.40 ≤ sim < 0.90`); above 0.90, the second block is
  treated as a near-duplicate and supersedes the first, bypassing detection
  entirely. This means high-similarity contradicting wording (e.g. two
  birthday dates that share four of five tokens) may still report
  `contradictions_detected: 0` even after this fix, because detection never
  ran. See [`docs/plans/plan_contradiction_detection_band.md`](docs/plans/plan_contradiction_detection_band.md)
  for the design discussion of a follow-up that runs contradiction detection
  on near-dup candidates before superseding.
- **`elfmem recall --frame` help** now lists `simulate` alongside `attention|self|task`.
  The `simulate` frame (Theory-of-Mind) shipped with the `mind` feature but was missing
  from the CLI help string, the MCP `elfmem_recall` docstring, and `docs/quickstart.md`.
- **`MemorySystem.frame()` `FrameError.recovery`** now lists all four valid frames
  (`'self', 'attention', 'task', 'simulate'`). Previously the recovery hint advertised
  only three, so an agent that correctly called `frame("simulate")` and mistyped would
  be told `simulate` doesn't exist.
- **`elfmem guide` OVERVIEW** now describes **four rhythms** (Heartbeat → Breathing →
  Sleep → Deep Sleep) and lists `rescore(max_count?)` in the operations table. The
  agent-runtime surface (`guide.py`) was the last place still saying "three rhythms"
  after v0.13.3 added the fourth.
- **Stale-concept sweep across docs**: `README.md`, `CLAUDE.md`, `docs/index.md`,
  `docs/quickstart.md`, `docs/elfmem_tool.md`, `docs/MULTIPLE_MCP_QUICK_REFERENCE.md`,
  `docs/multiple_mcp_steps.md`, `docs/dreaming_architecture.md`, and
  `docs/CLAUDE_CODE_INTEGRATION.md` updated to match the live registry: four frames
  (self/attention/task/simulate) and four rhythms (the fourth being Deep Sleep /
  `dream --rescore`, per CHANGELOG v0.13.3 — not the `simulate` frame, which is a
  retrieval mode, not a temporal rhythm).
- **Agent-pattern guides** (`docs/agent_usage_patterns_guide.md`,
  `docs/cognitive_loop_operations_guide.md`, `docs/operationalize_cognitive_loop.md`,
  `docs/research_agent_patterns.md`): added a top-of-file frame-consolidation note;
  rewrote `frame="world"` / `frame="short_term"` code examples to use `frame="attention"`
  so the snippets are runnable. Historical narrative `WORLD` / `SHORT_TERM` references
  remain as context, mapped by the note.
- **`docs/amgs_architecture.md`** flagged at the top as an **original design spec**:
  describes frames (`WORLD`, `SHORT_TERM`) and frame-composition presets (`SESSION`,
  `REASONING`, `BRIEFING`, `DEEP_RECALL`) that were not carried into the shipped
  implementation. Readers are pointed at `docs/quickstart.md` / `elfmem guide` for the
  current surface.
- **Memory-seeding scripts**: `scripts/seed_team_memory.py` and
  `scripts/learn_agent_patterns.py` were ingesting **"three rhythms" / "five frames"**
  text into agent memory, propagating the stale model into any seeded project. Updated
  to four rhythms and four frames.
- **`examples/simulation_calibration.md`** and **`docs/CLAUDE_CODE_INTEGRATION.md`** no
  longer call the `simulate` frame "the fourth rhythm". `simulate` is a frame; the
  fourth rhythm is Deep Sleep / rescoring.
- **`docs/SETUP_AND_CONFIG.md`** troubleshooting: documents the `OLLAMA_FLASH_ATTENTION=false`
  fix for the "json: unsupported value: NaN" error users hit when running embedding
  models (e.g. `bge-m3`) through Ollama.

Closes the docs portion of [#50](https://github.com/emson/elfmem/issues/50) (item 4:
non-existent `world` / `short_term` frames in CHANGELOG and guides; runtime FrameError
now lists all four valid frames). Other items in #50 are tracked separately.

### Added — named-agent identity

- **`project.agent_name`** field in `.elfmem/config.yaml` and **`elfmem init --name`**
  flag. When set, the rendered `.elfmem/AGENT.md` fragment includes an "Agent Identity"
  section binding the name to the SELF-recall protocol — so the host LLM knows that
  hearing the agent's name means "ground the response in the SELF frame." Empty/unset
  → fragment renders as before, no behaviour change. Drift detection participates: a
  rename surfaces in `elfmem agent-docs check` / `elfmem doctor` and is fixed by
  `elfmem agent-docs install`. Eats its own dog food: this repo's hand-written
  "Agent Identity: elf" section in CLAUDE.md is gone; the fragment supplies it.
- **`project.set_agent_name_in_config(path, name)`** helper: surgical one-line update
  of `project.agent_name` in a `config.yaml`, preserving comments and all other lines
  byte-for-byte. Action returned is `"replaced"`, `"inserted"`, or `"unchanged"`. Raises
  `ConfigError` (with `.recovery`) when the config doesn't exist or lacks both the
  field and an `identity:` anchor — refuses to invent project-section structure.

### Added — MCP/CLI parity (closes [#50](https://github.com/emson/elfmem/issues/50) items 2 + 3)

- **`elfmem_dream` MCP tool now accepts `rescore`, `rescore_max`, `no_llm`,
  `skip_contradictions`** — bringing it to parity with the v0.13.3 CLI flags
  (`elfmem dream --rescore [--max N] --no-llm --skip-contradictions`). MCP
  clients can now trigger deep-sleep rescoring, bypass the LLM during outages
  or bulk loads, and skip the O(n²) contradiction loop for trusted ingestion.
  Default invocation (`elfmem_dream()` with no args) is byte-identical to
  pre-feature behaviour. Threading verified by tests.
- **Five new MCP tools surfacing the Theory of Mind API**:
  - `elfmem_mind_create(subject, goals?, beliefs?, fears?, motivations?)` →
    creates a `mind`-category block, DURABLE decay, retrievable via the
    `simulate` frame.
  - `elfmem_mind_predict(mind_block_id, prediction, verify_at, reasoning?)` →
    attaches a falsifiable prediction (decision block + `predicts` edge).
  - `elfmem_mind_list()` → enumerates all mind blocks with prediction
    statistics (count, hit/miss ratio, calibration).
  - `elfmem_mind_show(mind_block_id)` → full view of one mind block with
    every linked prediction and its outcome.
  - `elfmem_mind_outcome(decision_block_id, hit, reason)` → closes a
    prediction; Bayesian-calibrates both the decision and mind blocks.

  Theory of Mind was Python-API-only since v0.7.0 — unreachable from any MCP
  client (Claude Desktop, Cursor, etc.). The workaround was `remember(...,
  tags=["mind/<subject>"])`, which bypassed all lifecycle protections. These
  wrappers close that gap with the same docstring + agent-first contract
  shape as the rest of the MCP surface.

### Added — `AgentGuide` entries for previously-undocumented public methods

Closes a pre-existing contract gap. Per CLAUDE.md: "every new public
`MemorySystem` method must have a corresponding `AgentGuide` entry in
`src/elfmem/guide.py`." Three methods shipped without one:

- **`mind_list`** (since v0.7.0) — discovery for mind blocks.
- **`mind_show`** (since v0.7.0) — detailed view of a single mind + predictions.
- **`rescore`** (since v0.13.3) — standalone deep-sleep operation, the public
  surface behind `dream(rescore=True)`.

Each entry follows the `USE WHEN / DON'T USE WHEN / COST / RETURNS / NEXT`
template and a runnable example. `elfmem guide rescore` / `elfmem guide
mind_list` / `elfmem guide mind_show` now return proper guidance instead of
a "valid method names" fallback.

### Migration

- **`elfmem init --name X` is now state-aware on established instances.** Previously
  `init` was refresh-only on established installs and silently ignored `--name`. Now,
  when the flag is passed and differs from the current config value, only the
  `agent_name:` line is surgically updated (via `set_agent_name_in_config`); the rest
  of the config — comments, blank lines, custom values — is preserved. Fresh installs
  continue to receive the field as part of the initial config write. No `--force`
  needed for the common rename path.
- **Hash backwards-compatibility for the AGENT.md fragment (named agents).** The
  agent-docs content hash mixes in `|agent_name=X` only when a name is set.
  Empty/unset `agent_name` produces a hash byte-identical to pre-feature renders, so
  existing installs upgrading to this version don't get a "edited" drift false-positive
  from `elfmem agent-docs check` / `elfmem doctor`. Subsequent renames still surface
  as drift, as intended.
- **AGENT.md fragment hash changes for all existing installs (GUIDES changes).**
  The `_guides_to_markdown(GUIDES)` content hash depends on the GUIDES dict. This
  release adds three new entries (`mind_list`, `mind_show`, `rescore`) and edits
  the `returns` text on `dream` / `consolidate` to mention `contradictions_detected`.
  Either change moves the hash. Existing installs will see `elfmem agent-docs check`
  report drift (`stale_version` if the lib version also bumped; otherwise `edited`).
  **Recovery is the existing one:** `elfmem agent-docs install` regenerates and
  re-locks. No data migration.
- **No behavioural change** for any pre-existing operation. The new MCP wrappers,
  `AgentGuide` entries, and `agent_name` field are pure additions; default
  `elfmem_dream()` and unnamed installs are byte-identical to the previous version.

---

## [0.13.3] — 2026-05-08

Deep-sleep rescoring. Adds elfmem's fourth rhythm: periodic re-evaluation
of *existing* active blocks against the *current* identity. The principle:
**memory health is observable and actionable** — doctor measures, one
command (`dream --rescore`) heals, ordering by `last_scored_at ASC` ensures
progressive coverage without manual targeting.

Closes a silent defect that had been in elfmem since the LLM-fallback was
added: blocks promoted via `skip_llm=True` or LLM timeout were stuck at
neutral metadata forever — `consolidate()` only processes inbox blocks,
never re-touching active ones. The fallback docstring claimed "re-scored
on next consolidation if the LLM recovers" but this was false. Now true.

### Added
- **`elfmem dream --no-llm`**: surface the existing `skip_llm=True` API
  capability at the CLI. Promotes inbox blocks without LLM scoring; affected
  blocks have `last_scored_at = NULL` and are picked up first by `--rescore`.
  Use for outages, bulk loads, cost-sensitive batches.
- **`elfmem dream --skip-contradictions`**: surface the existing
  `skip_contradictions=True` API capability at the CLI. Keeps LLM scoring
  but skips the O(n²) contradiction detection loop. Use for trusted
  structured ingestion.
- **`elfmem dream --rescore [--max N]`**: deep-sleep mode. After processing
  inbox, refreshes aged or unscored active blocks against the current SELF.
  Selection: NULL `last_scored_at` first (debt drains), then oldest by
  `last_scored_at` ascending (progressive rotation — every block leaves
  the front of the queue once rescored). Mutually exclusive with `--no-llm`.
- **`MemorySystem.rescore(max_count=None)`**: public API for programmatic
  rescore. Returns `{"rescored": N, "failed": M, "attempted": N+M}`.
- **`elfmem doctor` Scoring drift check**: surfaces unscored count, stale
  count (older than `target_max_age_days`), and percent of active. Drift
  warning fires when EITHER absolute count OR percentage threshold is
  exceeded; the recommendation is auto-scaled to the observed debt
  (rounded to nearest 50, floored at 20). Healthy state shows
  `0 unscored, N stale (>90d, X%)`.
- **`elfmem.operations.rescore` module**: pure-function selection + drift
  surface. Public exports: `RescoreFilter`, `DriftStats`,
  `select_rescore_candidates`, `compute_drift_stats`, `rescore_blocks`,
  and module-level `DEFAULT_*` constants.
- **`RescoreConfig`** in `elfmem.config`: `enabled`, `max_per_run`,
  `min_age_hours`, `target_max_age_days`, `drift_warning_count`,
  `drift_warning_percent`, `exclude_categories`, `exclude_tags`. All
  configurable via YAML; sensible defaults for typical agent memory sizes.

### Changed
- **Schema migration v2 → v3 (additive)**: adds nullable `last_scored_at`
  TEXT column to the `blocks` table. Backfill on migration sets it to
  `created_at` for existing blocks (synthetic but conservative — oldest
  blocks become first rescore candidates). Migration is row-count-validated
  by 0.13.1's backup machinery; safe by construction.
- **`consolidate()`** now records `last_scored_at` on success (current
  ISO timestamp) and clears it (NULL) when the LLM was bypassed via
  `skip_llm=True` or timeout fallback. Closes the prior one-way-door
  defect — blocks no longer get stuck at neutral metadata indefinitely.
- **`ConsolidateResult`** gains `rescored` and `rescore_failed` fields
  populated when `dream(rescore=True)` is called. Surfaces in `to_dict()`
  and `summary` so callers see both phases of dream's work.

### Eligibility filter (single source of truth)
A block is rescore-eligible iff:
- `status == "active"`
- `category` not in `["message", "mind", "decision", "prediction"]`
  (events / structured artefacts excluded by design)
- `source_peer IS NULL` (peer perspectives stay intact)
- no tag in `exclude_tags` (`system/no-rescore` is the explicit opt-out)
- `last_scored_at IS NULL` (debt — drains first regardless of cooldown), OR
  `last_scored_at < now - min_age_hours` (cooldown — don't churn fresh)

### Migration
None required. Schema migration is automatic, additive, and backed up on
first run of any post-0.13.3 elfmem command. Existing healthy installs
see `0 unscored, 0 stale` immediately. Affected installs (those using
`skip_llm=True` via the Python API or hitting LLM timeouts) see their
debt surface in doctor and can drain it with `dream --rescore`.

### Plan reference
[docs/plans/plan_deep_sleep_rescoring.md](docs/plans/plan_deep_sleep_rescoring.md)

---

## [0.13.2] — 2026-05-08

State-aware ``elfmem init``. Closes the anti-recovery loop where doctor
flagged stale agent docs, recommended ``elfmem init``, and init then
re-introduced the very drift it was supposed to remove by rendering from
inferred defaults instead of live config. One verb, three behaviours
selected by lifecycle state — no new commands, smaller surface, safer
re-runs.

### Fixed
- **Agent doc section rendered from inferred defaults instead of live
  config (Bug A).** ``init`` previously passed directory basename as
  ``Project`` and ``~/.elfmem/databases/{dir}.db`` as ``Database`` to the
  doc renderer, even when ``.elfmem/config.yaml`` already specified
  different values. Result: re-running ``init`` (which doctor recommended
  for stale docs) clobbered correct paths with wrong ones — the same
  shape of failure as the 0.13.0 path regression. Renderer now reads
  ``project.name`` and ``project.db`` from the config file and uses them
  faithfully. Empty/missing fields are omitted; never fabricated.
- **No "established instance" semantics on init.** v0.13.1 made block-level
  seeding idempotent, but the outer init shell still asserted the
  fresh-install template every run. Doctor steered operators here; init
  re-introduced drift. v0.13.2 makes ``init`` state-aware: detection at
  entry classifies the instance and selects the right behaviour. No new
  command added — same ``init`` verb, smarter implementation.

### Added
- **``elfmem.lifecycle.is_established_instance(config_path, db_path)``.**
  Pure-read state detector returning ``EstablishmentState`` with kind in
  ``{"fresh", "established", "orphan", "unreadable"}``. Reused by ``init``
  and (in future) doctor. ``to_dict()`` for agent invocation.
- **State-aware ``elfmem init``.** Three behaviours, one command:
  - **Fresh** (no config / empty DB): full setup as before.
  - **Established** (config + content rows): refresh-only — does NOT
    rewrite config, re-renders agent doc from live config, runs the
    idempotent constitutional seed, prints
    ``[established — refreshing only]`` mode banner.
  - **Orphan** (configured DB empty + populated neighbour): refuses with a
    pointer to ``elfmem rescue``. ``--force-new`` bypasses (rarely needed).
  - **Unreadable**: refuses; never silently overwrites a corrupt DB.
- **``read_render_values_from_config(config_path)``** in ``elfmem.project``.
  Public helper returning ``(name, db)`` tuple from config; never raises.
- **Render-time visibility.** The auto-managed CLAUDE.md/AGENTS.md elfmem
  block now includes ``_auto-generated from .elfmem/config.yaml — edit
  OUTSIDE these markers_`` so operators stop wasting effort hand-fixing
  text that will be re-rendered next run. Quick-commands list now includes
  ``elfmem init`` (idempotent) and ``elfmem rescue``.
- **``init --json`` includes lifecycle state.** Adds ``lifecycle`` and
  ``mode_banner`` fields so agent callers see exactly which branch ran.

### Principle (now in code)
*Authoritative state is read, never inferred. When live state exists,
config is truth; defaults are bootstrap only on first install.* Quoted
in the docstrings of ``_build_section`` and ``is_established_instance``
so the next contributor reads the rule before touching the render or
detection paths.

### Migration
No user action required for existing healthy installs. Affected users
(stale docs from prior 0.13.x init runs) just re-run ``elfmem init`` —
on an established instance it now rewrites the docs from live config and
leaves everything else alone. The mode banner (``[established —
refreshing only]``) makes the implicit branch explicit.

---

## [0.13.1] — 2026-05-07

Critical safety patch. v0.13.0 introduced two bugs that combined to silently
relocate user databases and create false-positive "fresh install" states.
This release reverts the destabilising change, hardens every safety net it
exposed, and adds a structured recovery surface.

### Fixed
- **Path-resolution regression (catastrophic).** v0.13.0 changed bare-relative
  ``project.db`` to resolve against the config file's directory instead of
  the caller's cwd. Existing users with relative configs found their DB
  silently "missing"; ``elfmem doctor`` then suggested ``elfmem init``,
  which created a fresh empty DB at the new path while the real data sat
  orphaned. Reverted to 0.12.x semantics: bare-relative paths are kept
  verbatim and resolved by ``Path()`` at the call site (cwd-relative).
  Affected users recover via ``elfmem rescue`` (see below).
- **Backup safety net was technically correct, operationally useless.** The
  ``.before-vN.bak`` mechanism dutifully backed up whatever was at the path,
  even an already-empty DB created by the path regression. ``create_backup``
  now validates row counts in canonical content tables (``blocks``,
  ``peer_roster``, ``block_tags``, ``edges``) post-write; mismatch → stub
  is deleted, ``BackupValidationError`` raised, migration aborted. A
  populated DB whose backup ends up empty cannot pass through unnoticed.
- **Constitutional re-seed created ghost duplicates.** ``setup()`` keyed
  idempotency on content hash and only caught inbox-stage duplicates.
  Active/archived collisions silently produced fresh UUIDs, multiplying
  stock content on every re-run and diluting the SELF frame. Now
  identifies each constitutional block by stable role tag
  (``self/role/<role>``); seeds whose role is filled in any active or
  inbox state are skipped, preserving any user customisation of that slot.
  Archived blocks count as "unfilled" so explicit retirements can be
  re-seeded.

### Added
- **``elfmem rescue`` command.** Detects orphaned populated DBs and proposes
  a rebind plan. Walks neighbour locations (config-dir-relative, parent-of-
  config-dir, ``~/.elfmem/databases/``), inspects row counts read-only, and
  reports an action: ``none`` | ``rebind`` | ``ambiguous`` | ``first_install``.
  ``--apply --yes`` rewrites ``project.db`` in the config to an absolute
  path (with a timestamped config backup taken first). ``--json`` for agent
  invocation.
- **``elfmem init`` neighbour-DB pre-flight.** Before creating a fresh DB,
  init now scans for populated neighbour DBs. If exactly one is found,
  init refuses with an ``elfmem rescue`` recovery hint. If multiple are
  found, init refuses and lists them for human/agent review. ``--force-new``
  bypasses the check (rarely needed).
- **``elfmem doctor`` DB drift check.** New observability surface — when
  the configured DB is missing or empty AND a populated neighbour exists,
  doctor's recovery suggestion is ``elfmem rescue``, NOT ``elfmem init``.
  Doctor never recommends a destructive path when a non-destructive one
  fits the symptom.
- **``self/role/<role>`` tags on every constitutional seed.** Stable
  identifier per cognitive slot. ``CONSTITUTIONAL_ROLES`` exported from
  ``elfmem.seed`` for programmatic access.
- **``elfmem.rescue`` module.** Public surface: ``DbCandidate``,
  ``RescuePlan``, ``inspect``, ``find_neighbour_dbs``, ``build_rescue_plan``.
  Pure-read; ``to_dict()`` on every result type for agent consumption.
- **``BackupValidationError``** in ``elfmem.db.migrate`` — typed exception
  with ``.recovery`` field, raised when a backup fails post-write integrity
  validation.

### Migration (recovering from 0.13.0)
If you upgraded to 0.13.0 and your DB looks empty:

```
$ elfmem doctor
✗ Database  /path/to/.elfmem/x.db (project.db in config)
   Suggestion: elfmem rescue
✗ DB drift  populated DB at /path/to/x.db (247 blocks) is not the
            configured target — likely 0.13.0 path regression
   Suggestion: elfmem rescue --apply --yes

$ elfmem rescue
Configured DB is empty; populated DB found at /path/to/x.db
(247 blocks). Suggested: rewrite project.db to '/path/to/x.db' (absolute).

$ elfmem rescue --apply --yes
✓ rebind applied. Config backup: <config>.elfmem-bak-rescue-<ts>
```

The rescue command never deletes the orphan or the empty-fresh DB — that
decision is left to the user. Inspect both, decide, remove the unwanted
one manually.

---

## [0.13.0] — 2026-05-07

### Added
- **Unified env vars: `ELFMEM_CONFIG` and `ELFMEM_DB`.** The MCP server and CLI now read the same canonical names. The legacy MCP-only names (`ELFMEM_CONFIG_PATH`, `ELFMEM_DB_PATH`) still work but emit a one-time stderr deprecation warning per process and will be removed in v0.14. If both canonical and deprecated forms are set with conflicting values, startup fails with a clear `ConfigError` — silent precedence would hide misconfigurations. See "Migration" below.
- **MCP startup banner.** `elfmem serve` (and `python -m elfmem.mcp`) now prints one stderr line at boot showing the resolved DB and config paths with their resolution sources, e.g. `[elfmem] mcp boot: db=/x/.elfmem/databases/elfmem.db (project.db in config) config=/x/.elfmem/config.yaml (auto-discovered)`. Makes silent fallbacks visible without enabling debug logs.
- **`elfmem migrate` command group: structured, agent-friendly migration system.**
  - `elfmem migrate status` — one-line summary per pending migration; exit 0 if clean.
  - `elfmem migrate plan [--json]` — full per-step plan with file hashes, before/after diffs, and ready-to-run `apply_command` strings. The JSON form is the contract for agent invocation.
  - `elfmem migrate apply [--id ID] [--dry-run] [--yes] [--json]` — atomically rewrites stale config entries with a tmp-file rename, after writing a `<file>.elfmem-bak-<step_id>-<timestamp>` backup. Hash-gated: refuses if the source file drifted since the plan was built. Idempotent: re-running returns `skipped` on already-applied steps. Per-step granularity lets agents apply one migration at a time and recover from per-step failures.
- **`elfmem doctor --migrate-mcp`.** Quick read-only health-check shortcut — scans `~/.claude/claude_code_config.json` and the cwd's `.claude.json` for elfmem MCP entries with deprecated env vars or legacy launch patterns, and prints a diff per finding. For applying changes, use `elfmem migrate apply` instead.
- **`src/elfmem/migrate.py` module.** Public surface: scan layer (`scan`, `scan_file`, `scan_with_warnings`, `is_elfmem_entry`, `MigrationFinding`, `ParseWarning`) and plan/apply layer (`build_plan`, `apply_step`, `apply_plan`, `MigrationPlan`, `MigrationStep`, `StepApplyResult`, `ApplyResult`). All result types include `to_dict()` for agent consumption.
- **Robustness in `migrate apply`:**
  - **File-grouped writes.** Steps targeting the same file (e.g. multiple elfmem MCP servers in one Claude config) now apply in a single backup-and-write cycle. Previously the first step succeeded and the rest returned `stale` because the file hash changed between writes. Per-step result granularity is preserved; agents still see one outcome per step.
  - **Symlink preservation.** When the target is a symlink (e.g. dotfile-managed configs via stow / chezmoi / yadm), the link is preserved and the real target is rewritten in place. Backups also live next to the resolved target so the source-tree-managed link directory isn't polluted.
  - **OSError surfacing.** Permission and disk-space failures during apply now return a `failed` result with a recovery hint instead of propagating as a stack trace.
  - **Plan integrity guard.** If two steps targeting the same file disagree on the source hash (artificial corruption), the whole file group fails fast with a clear message rather than producing inconsistent partial state.
  - **Nanosecond backup timestamps.** `<file>.elfmem-bak-<step_id>-<unix_ns>` filenames eliminate collisions on rapid retries.
- **Parse-warning surface in `migrate status` / `migrate plan`.** Files that look like elfmem-relevant Claude configs but contain JSON5 features (comments, trailing commas) are no longer silently skipped. They appear under `warnings` in the plan with the parser's error message and a hint to convert to plain JSON.
- **Test-mode safety guard for `resolve_db()`.** When `PYTEST_CURRENT_TEST` is set and resolution would fall through to the global `~/.elfmem/agent.db`, raises `ConfigError` instead. Prevents tests from silently writing into the developer's real memory. Set `ELFMEM_ALLOW_GLOBAL_FALLBACK=1` to opt out for tests that legitimately need the fallback.

### Changed
- **Relative `project.db` paths now resolve against the config file's directory.** Previously a relative `project.db: db/x.db` in `.elfmem/config.yaml` would resolve against the caller's cwd, making configs non-portable. Absolute paths and tilde expansions are unchanged. In practice, every config generated by `elfmem init` uses an absolute path, so this only affects hand-edited configs.

### Migration (env var rename and launch pattern)
For users who registered the MCP server with `ELFMEM_DB_PATH` / `ELFMEM_CONFIG_PATH` or `python -m elfmem.mcp`, the canonical pattern is now:

```json
{
  "mcpServers": {
    "elfmem": {
      "command": "elfmem",
      "args": ["serve", "--config", "/absolute/path/to/.elfmem/config.yaml"]
    }
  }
}
```

**Step-by-step (humans):**

1. `elfmem migrate status` — confirm what's pending.
2. `elfmem migrate plan` — review the diff per server entry.
3. `elfmem migrate apply --dry-run` — see exactly what would happen, no writes.
4. `elfmem migrate apply` — interactive; prompts before writing. Each step writes a timestamped backup before modifying the original.
5. Restart Claude Code so MCP servers reload with the new entries.
6. `elfmem doctor` — verify the setup is clean.

If something goes wrong, every modified file has a `<file>.elfmem-bak-<step_id>-<timestamp>` companion. Restore with `mv <backup> <file>`.

**Step-by-step (agents):**

1. Call `elfmem migrate plan --json`. Parse the result.
2. For each step in `steps`, decide whether the change is acceptable (in most cases it will be — these are mechanical renames). Steps include `apply_command` strings ready to invoke.
3. Execute `elfmem migrate apply --yes --json` (apply all) or `elfmem migrate apply --id <step_id> --yes --json` (one at a time). Parse `applied` / `skipped` / `failed`.
4. If any step returns `status: "stale"`, the source file changed between plan and apply. Re-run `plan` and try again — this is the safe path.
5. Confirm with `elfmem doctor --json`.

The legacy env-var names continue to work in v0.13.x with a one-time stderr deprecation warning; they are removed in v0.14.

### Fixed
- **`find_project_root()` no longer returns `~` as a project root.** Home directory is now excluded *before* checking project markers, preventing `~/.elfmem` from satisfying the `.elfmem` marker and causing all peer paths to resolve to `~/.elfmem/inbox`. Home is a data/config boundary, not a project root.
- **`_discover_project_root()` guards against global `~/.elfmem/config.yaml`.** The shortcut that maps `<root>/.elfmem/config.yaml → <root>` now explicitly rejects the home directory as the derived root. This means `ELFMEM_CONFIG_PATH=~/.elfmem/config.yaml` (the global MCP registration pattern) no longer silently causes peer paths to resolve to the old global inbox.
- **`_resolve_peer_dir()` adds Tier 3 late discovery with caching.** When a `MemorySystem` was constructed from a global config (no project root at build time), peer operations now call `find_project_root()` at the point of use. The discovered root is cached back into `_project_root` so the inbox path is stable for the lifetime of the instance — cwd changes after the first peer call cannot silently shift which project's messages are visible. This lets the global `elfmem` MCP server — launched with `ELFMEM_CONFIG_PATH=~/.elfmem/config.yaml` — correctly find project-local peer messages when Claude Code is running inside a project. No new configuration required.

- **`PeerInboxStatus.warning` field.** `scan_peer_inbox()` now distinguishes between an uninitialised project (`.elfmem/` directory absent — project root found via `.git` but `elfmem setup` never run) and a normal empty inbox (`.elfmem/` present, `inbox/` just hasn't received any messages yet). The former sets `warning` to a message directing the user to run `elfmem setup`; the latter leaves it empty. `warning` is included in `to_dict()` only when non-empty, and `summary` / `__str__` surface it. Previously both cases returned silent `pending: 0`, masking misconfiguration.

### Migration (upgrading from 0.12.0)
If you had peer messages stuck in `~/.elfmem/inbox/<peer>/` (unread because the MCP server was resolving the wrong path), run `elfmem doctor` — it will detect the legacy messages and print an `mv` command to move them to the project-local inbox. Re-run `elfmem peer init --name <name>` afterward to update the stored inbox path in the database.

---

## [0.12.0] — 2026-05-07

### Changed
- **Peer inbox/outbox are now project-local by default.** `MemorySystem` derives them from the project root (the directory containing `.elfmem/config.yaml`) as `<project>/.elfmem/inbox` and `<project>/.elfmem/outbox`. Previously they defaulted to the global `~/.elfmem/inbox` / `outbox`, which silently diverged from project-local paths peers were writing to — meaning the MCP server could miss messages that landed in the right place. `PeerConfig.inbox_dir` and `outbox_dir` are now optional overrides (default `None`); leave them unset and elfmem picks the project-local path. Set them explicitly only for tests or unusual deployments.
- **`elfmem serve` (MCP) auto-discovers `.elfmem/config.yaml`.** When launched without `--config` and without `ELFMEM_CONFIG_PATH`, the server walks up from cwd to locate a project config. This is what lets Claude Code launch the server with no flags and still see project-local peer messages.
- **`elfmem doctor` peer-inbox check** now reports the resolved project-local path (rather than the raw config value) and warns when a legacy `~/.elfmem/inbox` directory still contains pending messages, with a `mv` command in the recovery hint.

### Removed
- **Global `~/.elfmem/inbox` and `~/.elfmem/outbox` defaults.** Peer messaging is project-scoped; running peer ops outside a project (and without an explicit override) now raises `ProjectNotFound` with a recovery hint pointing to `elfmem setup`. Migration: move any existing messages from `~/.elfmem/inbox/<sender>/` into `<project>/.elfmem/inbox/<sender>/`. `elfmem doctor` flags this automatically.

### Added
- **`ProjectNotFound` exception.** Raised when a peer operation needs a project root but none is found and no explicit override is configured. Carries a `.recovery` hint pointing at `elfmem setup`.
- **Agent-docs system (`src/elfmem/agent_docs.py`).** Auto-generates library API reference from `guide.GUIDES`, stored as project-local `.elfmem/AGENT.md`. Drift detection via `.agent-docs.lock` tracks version and content hash. Three CLI commands: `elfmem agent-docs install | check | diff`. Installed at `elfmem init`, validated by `elfmem doctor`. Single source of truth for agent invocation patterns.

---

## [0.11.0] — 2026-05-03

### Added
- **`MemorySystem.peer_inbox_status()`:** Lightweight filesystem scan reporting unprocessed peer messages. Returns `PeerInboxStatus` with pending count, sender DIDs, oldest/newest timestamps, and inbox path. Zero LLM calls, no database access. Designed for polling triggers.
- **`elfmem status --peer-inbox` CLI flag:** Focused inbox status view for scripting and RemoteTrigger prompts. Supports `--json` output.
- **`elfmem_status` MCP tool `peer_inbox` param:** When `True`, includes `peer_inbox` key in response with `PeerInboxStatus` data.
- **`AgentGuide` entry for `peer_inbox_status`:** Runtime self-documentation for the new method.
- **`scan_peer_inbox()` in `operations/peer.py`:** Pure function (Path → PeerInboxStatus) reusing existing `_scan_inbox()` and `_parse_message()` helpers.
- **`elfmem doctor` peer checks:** Doctor now validates peer communication setup — identity, inbox/outbox path accessibility, per-peer delivery path reachability, and inbox drift detection (warns when `inbox_dir` has changed since `peer init`).
- **`peer inbox` warnings:** When no messages are found but peers have been active in the last 30 days, `PeerInboxResult` now includes a warning suggesting inbox path verification. Catches silent wrong-path misconfigurations.
- **`elfmem doctor --modules`:** Prints the key module map (from `project.py KEY_MODULES`) without running health checks. Always current — adding a new module means adding one line to the dict, not editing CLAUDE.md.
- **`KEY_MODULES` dict in `project.py`:** Single source of truth for the project's module layout. Maintained alongside the code; displayed on demand via `elfmem doctor --modules`.
- **Version-stamped agent doc sections:** `elfmem init` now embeds the installed version in the section comment (`<!-- elfmem:start v0.9.1 -->`). `elfmem doctor` detects legacy or mismatched versions and suggests a refresh.
- **`extract_section_version(doc_path)`:** New public function in `project.py` — parses the elfmem version from the section start comment for programmatic version checking.
- **`format_key_modules()`:** New public function in `project.py` — returns the KEY_MODULES table as formatted text for CLI and agent consumption.
- **`AgentGuide` entries for all peer operations:** `peer_init`, `peer_add`, `peer_send`, `peer_inbox`, `peer_list`, `peer_trust`, `export_blocks`, `import_blocks` — all now in `guide.py GUIDES`. `elfmem guide` is authoritative for all operations including v0.9.x peer features.
- **Updated `elfmem guide` OVERVIEW:** Peer communication operations now appear in the compact overview table, grouped under a "Peer communication" section.
- **Peer communication:** elfmem instances can exchange knowledge and messages. Pull-based, file-mediated, zero infrastructure. Three schema additions (`source_peer`, `share`, `envelope_json` on blocks) and one new table (`peer_roster`).
- **`elfmem peer` CLI command group:** `peer init`, `peer add`, `peer remove`, `peer list`, `peer trust`, `peer send`, `peer inbox` subcommands for managing peer identity, roster, messaging, and trust.
- **`elfmem export` / `elfmem import` CLI commands:** Export shareable blocks as signed JSON bundles; import with provenance tracking and trust-gated confidence. Self-federation via `--self-merge`.
- **New API methods:** `peer_init()`, `peer_add()`, `peer_remove()`, `peer_list()`, `peer_trust()`, `peer_send()`, `peer_inbox()`, `export_blocks()`, `import_blocks()`.
- **New MCP tools:** `elfmem_peer_send`, `elfmem_peer_inbox`, `elfmem_peer_list`, `elfmem_export`, `elfmem_import`.
- **New result types:** `PeerInfo`, `ExportResult`, `ImportResult`, `PeerSendResult`, `PeerInboxResult` — all with agent-friendly `__str__`, `summary`, and `to_dict()` surfaces.
- **Trust loop:** Outcome closure on peer-originated blocks automatically updates peer trust scores. Trust decays for inactive peers during `curate()`.
- **Message blocks skip dedup:** Blocks with `category=message` bypass near-duplicate rejection and contradiction detection during `consolidate()` — messages are events, not knowledge claims.
- **`delivery_path` on `peer_add()`:** Optional filesystem path to a peer's inbox directory. When set, `peer_send()` writes directly there (subdirectory named by sender), enabling instant delivery with no transport layer. CLI: `elfmem peer add <did> --name <n> --delivery-path <path>`.
- **`PeerConfig`:** New configuration section for peer identity, outbox/inbox directories, confidence floor, and trust thresholds.
- **`PeerError` exception:** New exception type for peer operations, with `.recovery` field.
- **Automatic schema migration:** `db/migrate.py` applies pending migrations on startup via `MemorySystem.from_config()`. Version-tracked, idempotent, zero ceremony. Existing databases are upgraded transparently — no manual migration commands needed. Pre-migration backup is created automatically.
- **`elfmem backup` CLI command:** Creates a clean, self-contained database backup using `VACUUM INTO`. Records backup metadata in `system_config` for `elfmem doctor` to report.
- **Backup advisory in `elfmem doctor`:** Reports backup count, total size, and latest backup name. Suggests `elfmem backup` when no backups exist. Suggests cleanup when more than 3 backups accumulate.

### Changed
- **`visualise()` now includes archived blocks by default:** `include_archived` defaults to `True`. Archived nodes load as diamond-shaped, hidden by default with a toggle button. The full knowledge lifecycle is always one click away.

### Fixed
- **`mind_predict()` no longer requires `consolidate()` after `mind_create()`:** Mind blocks are now promoted to active inline when a prediction is made against them, with correct DURABLE decay tier (λ=0.001) assigned. Structured blocks are validated by their lifecycle events, not by LLM processing.
- **`mind_outcome()` no longer requires `consolidate()` before closing a prediction:** Decision blocks are now promoted to active inline when their outcome is recorded. Outcome closure is the consolidation event for predictions.
- **Dashboard decay chart scale fixed:** Decay curves now use a logarithmic X-axis so all four tiers (spanning 5 orders of magnitude) are visible. Previously, the permanent tier stretched the axis to 460,000 hours, making standard and ephemeral curves invisible.
- **Dashboard scoring tab now shows all 4 frames including `simulate`:** Builtin frame profiles are mirrored in the viz module so frames missing from the DB (e.g. `simulate`) always appear. Score boosts (`mind: 6×`, `decision: 5×`, `tag:self/: 10×`) are now visualised as a grouped bar chart.
- **Dashboard graph no longer spins after loading:** Physics simulation is disabled after stabilisation completes, preventing node drift and orbital motion. Zoom speed reduced for smoother navigation.
- **Dashboard graph supports Theory of Mind:** New edge colours for `predicts`, `validates`, `elaborates`, `supports` relations. Category-based node colouring toggle (tier vs category) and category filter pills reveal mind/decision block structure.

## [0.8.0] — 2026-04-28

### Added
- **`elfmem --version` / `-V` CLI flag:** Prints installed version and exits. Version is read from package metadata (`importlib.metadata`), single source of truth in `pyproject.toml`.
- **`elfmem.__version__`:** Exported from the package root for programmatic access.

## [0.7.0] — 2026-04-28

### Added
- **Theory of Mind (ToM) blocks:** New `mind` block category for modelling other agents' goals, beliefs, fears, motivations, and falsifiable predictions. Mind blocks use DURABLE decay tier (~6 month half-life). New API methods: `mind_create()`, `mind_predict()`, `mind_list()`, `mind_show()`, `mind_outcome()`.
- **`simulate` frame:** New built-in retrieval frame for inhabiting perspectives and reasoning about modelled minds. Uses `score_boosts` to prioritise SELF blocks (10×), mind blocks (6×), and decision blocks (5×) via category/tag-prefix multipliers applied during composite scoring.
- **`score_boosts` on `FrameDefinition`:** Frames can now specify per-category and per-tag-prefix score multipliers. Plain keys match block categories (e.g. `"mind": 6.0`); keys prefixed with `"tag:"` match tag prefixes (e.g. `"tag:self/": 10.0`). Applied in retrieval stage 4 before top-k selection.
- **`predicts` and `validates` edge relation types:** Default weights 0.70 and 0.75 respectively. `predicts` links mind blocks to decision blocks (predictions). `validates` is created on outcome closure.
- **`elfmem mind` CLI command group:** `mind create`, `mind predict`, `mind list`, `mind show`, `mind outcome` subcommands for managing ToM blocks from the command line.
- **New result types:** `MindSummary`, `MindPredictResult`, `MindShowResult`, `MindOutcomeResult`, `PredictionDetail` — all with agent-friendly `__str__`, `summary`, and `to_dict()` surfaces.
- **`SIMULATE_WEIGHTS` scoring preset:** Balanced weights (similarity=0.25, confidence=0.25, recency=0.15, centrality=0.20, reinforcement=0.15) for the simulate frame.
- **`_render_simulate_template`:** Groups blocks by role (Identity, Minds, Decisions, Context) for simulate frame rendering.
- **DB queries:** `get_active_blocks_by_category()`, `get_edges_by_relation_type()` for mind block operations.

### Fixed
- **CLI commands no longer hang due to implicit consolidation:** `MemorySystem.managed()` gains `auto_dream` parameter (default `True` for backward compatibility). All CLI commands now pass `auto_dream=False`, preventing surprise `dream()` calls on context exit that blocked for minutes with local LLM backends. Unconsolidated blocks remain safely in the inbox — run `elfmem dream` explicitly when ready. `elfmem remember` now prints an advisory when inbox hits threshold.

### Changed
- **`MemorySystem.managed(auto_dream=...)` parameter:** New keyword-only parameter controls whether pending blocks are consolidated on exit. Default is `True` (preserves existing behaviour for scripts). Pass `False` for CLI tools and contexts where implicit consolidation would cause unexpected delays.

## [0.6.0] — 2026-04-26

### Fixed
- **`EmbeddingService` protocol gains `model_name` property:** `consolidate()` was storing `embedding_model="mock"` (hardcoded string, TODO since inception). `OpenAIEmbeddingAdapter` exposes `model_name → self._model`; `MockEmbeddingService` exposes `model_name → "mock"`. `_BlockDecision` carries the model name and `_apply_decisions` writes it via `d.embedding_model`. All stored block embeddings now record their actual source model.
- **MemoryAgentBench context always built from blocks, not frame-rendered text:** `context_text = frame_result.text` was bounded by the attention frame's hardcoded 2000-token `token_budget`, while the BM25 path rebuilt context from `block.content` (bounded only by `_context_budget_words`). Fixed: always build `"\n\n".join(b.content for b in blocks)` so both paths are bounded identically by `config.context_window_tokens`.
- **`consolidate()` with `skip_llm=True` — O(n²) active-block re-embedding eliminated:** `_collect_decisions` was fetching all active blocks and re-calling `embed_batch` on their content at every consolidation batch, even though each promoted block already has its embedding stored by `update_block_scoring`. With `skip_llm=True` (non-CR benchmark paths), the stored embedding equals `embed(content)` since summary falls back to content — so stored vectors are directly reusable at zero API cost. `get_active_blocks_with_embeddings` + `bytes_to_embedding` replaces the `embed_batch` call. With `skip_llm=False`, `embed_batch` is preserved because the stored embedding is `embed(summary) ≠ embed(content)` and near-dup/contradiction detection requires content vectors. Impact: Accurate Retrieval (800+ chunks/example) drops from ~365M → ~0 re-embedding tokens; CR (18-188 chunks) unchanged.
- **MemoryAgentBench BM25 index aligned with elfmem retrieval content:** BM25 was built on raw chunks during ingestion, but elfmem's vector retrieval returns `block.get("summary") or content`. The mismatch caused RRF merge to fall back to content-prefix heuristic matching, often failing and polluting the context with raw chunks alongside summaries. Fixed: BM25 is now built post-consolidation from active block content via `frame("attention", query=None)` — summaries when available (CR with full LLM), raw content otherwise. RRF merge now uses exact block-ID matching (no supplementary fallback needed). `_BM25Index.add(block_id, content)` and `search()` returns `(block_id, content, score)` triples.
- **MemoryAgentBench answerer uses context, not parametric knowledge:** SYSTEM_PROMPT and QA prompt now explicitly forbid using training knowledge ("ONLY from the provided context — never use your own knowledge"). Previous prompts allowed Gemma to answer from priors, producing predictions like "United Kingdom" regardless of retrieved context. Also handles conflicting facts by preferring the most recently stated version.
- **MemoryAgentBench `top_k` raised to 20:** With 18 total blocks and `top_k=10`, only 10 post-suppression blocks reached the context; the remaining ~3 (which may contain multi-hop chain links) were dropped. At 20, all post-suppression blocks fit within the 2643-word context budget (summaries are ~40 words each).
- **MemoryAgentBench `contradiction_similarity_prefilter` raised 0.50→0.75:** With 18 highly similar factconsolidation chunks, the 0.50 threshold caused 153 pairwise LLM calls (28 min ingestion). True contradictions (same entity, different claims) have cosine similarity >0.80 and are unaffected. Expected ingestion: ~3 min.
- **MemoryAgentBench Conflict Resolution — contradiction detection now active:** `is_conflict_resolution` was computed but never wired to the `skip_llm` flag, so elfmem's contradiction detection never ran during CR evaluation. Fixed: CR examples now use `skip_llm=False` (full consolidation); other competencies use `skip_llm=True` for speed. Verified: CR F1 improved from 1.3% → 4.8% (3.7×) on `factconsolidation_mh_6k` with Gemma 4 26B A4B.
- **MemoryAgentBench context budget derived from `context_window_tokens`:** Replaced the hardcoded `max_context_words=2000` band-aid (which still overflows 2048-context models) with `_context_budget_words(config)` — a pure function that subtracts prompt overhead from `MABenchConfig.context_window_tokens` and converts to words at 1.4 tokens/word.
- **MemoryAgentBench runner logging silenced by datasets library:** `datasets` sets up root-logger handlers on import, making `logging.basicConfig()` a no-op and swallowing all INFO/ERROR output including caught exceptions. Fixed: `force=True` on `basicConfig` in `runner.main()`.


### Added
- **`MemorySystem.learn_document(text, chunk_size, chunker, skip_llm)`:** Ingest a document in one call — chunks text, learns each chunk, auto-consolidates via `dream()` at `inbox_threshold` intervals. Accepts an optional `chunker` callback (e.g. `nltk.sent_tokenize`); default splits at sentence boundaries. Returns `LearnDocumentResult` with chunk and consolidation counts.
- **`LearnDocumentResult` type:** New result type with `chunks_total`, `chunks_created`, `chunks_duplicate`, `consolidations`, `blocks_promoted`. Exported from `elfmem`.
- **BM25 keyword search in retrieval pipeline (stage 2b):** `hybrid_retrieve()` now runs BM25 in parallel with vector search, discovering blocks with strong keyword overlap that embedding similarity misses. Soft dependency on `rank_bm25` — when not installed, the stage is silently skipped (zero regression). Install via `pip install elfmem[bm25]`.
- **Reciprocal Rank Fusion (stage 2c):** When both vector and BM25 produce results, `hybrid_retrieve()` merges their ranked lists via RRF (k=60, Cormack et al. 2009). Blocks found by both rankers score higher; BM25-only blocks receive proportional relevance scores instead of the previous `similarity=0.0`. Falls back to raw cosine when BM25 is absent.
- **`dream(skip_llm, skip_contradictions)` parameters:** `dream()` now forwards `skip_llm` and `skip_contradictions` to `consolidate()`, enabling fast-path consolidation without bypassing policy tracking or threshold persistence.
- **`MABenchConfig.context_window_tokens`:** New config field (default 4096) representing the LM Studio model's context window. All answer-context truncation derives from this value; set to 2048 for smaller models.

### Fixed
- **Config wiring: `contradiction_threshold`, `near_dup_exact_threshold`, `near_dup_near_threshold`:** These three `MemoryConfig` fields existed but were not passed from `MemorySystem.consolidate()` to the consolidation operation. Custom config values were silently ignored (defaults matched, so no observable bug at default settings). Now wired through.

### Added
- **LoCoMo benchmark harness:** Complete benchmark suite for evaluating elfmem against LoCoMo (ACL 2024) — 10 conversations, 1,986 QA pairs, 5 categories. Includes metrics (Porter-stemmed F1), typed data loading, BM25 hybrid retrieval, observation transform, and CLI runner with `--test`, `--baselines`, `--resume`, `--top-k`, `--category` flags. Results conform to `benchmark_report_spec.md`.
- **`consolidate(skip_llm=True)`:** Bypass all LLM calls during consolidation (embed + promote only). Reduces ingestion from hours to seconds for bulk import and benchmarks.
- **`consolidate(skip_contradictions=True)`:** Keep LLM summaries and alignment scoring but skip O(n²) contradiction detection. Best for large batches where contradiction checking is unnecessary.
- **`_extract_json()` in OpenAI adapter:** Strips markdown code fences from LLM responses. Fixes compatibility with local models (Gemma, Ollama) that wrap JSON in ` ```json ``` ` fences.
- **Tags in ScoredBlock during retrieval:** Fixed retrieval pipeline to load block tags from database into ScoredBlock objects (was hardcoded to empty list).
- **Benchmark guides and strategy:** `benchmark_report_spec.md` (standard output format), `benchmark_strategy.md` (MemoryAgentBench → LoCoMo → LongMemEval priority), `locomo_benchmark_guide.md`, `memoryagentbench_guide.md`, `longmemeval_benchmark_guide.md`.
- **Git workflow documentation:** Protected main branch policy. All work happens on feature branches with PR-based review. Release tags created on main after merge, never before. Documented in CLAUDE.md.

---

## [0.5.0] — 2026-03-28

### Added
- **Logging infrastructure (Phase 1):** Structured, minimal-by-default logging with JSON/text/compact formatters. Disabled by default (CRITICAL level); enable via `ELFMEM_LOG_LEVEL=INFO` or config. Includes `LoggingConfig`, context variables for operation/session IDs, and `configure_logging()` factory. Zero overhead when disabled.

### Changed
- **BREAKING** `MINIMUM_COSINE_FOR_EDGE` raised from 0.30 to 0.50. Blocks must now
  share genuine semantic similarity before contextual signals (category, temporal
  proximity) can push a pair past the edge threshold. Previously, same-category,
  same-session blocks with cosine as low as 0.30 formed edges, polluting the graph
  with spurious connections. Migration: no code changes needed; existing edges are
  unaffected, but fewer new similarity edges will be created on consolidation.
- **BREAKING** `EDGE_SCORE_THRESHOLD` raised from 0.40 to 0.45. Combined with the
  higher cosine guard, this tightens the quality bar for new similarity edges.
  Migration: callers passing an explicit `edge_score_threshold` should review their
  value against the new default.
- **BREAKING** `EDGE_DEGREE_CAP` reduced from 10 to 5. Each newly promoted block
  creates at most 5 edges during consolidation (previously 10). Migration: callers
  passing an explicit `edge_degree_cap` should review their value.
- `consolidate()` restructured into read-then-compute-then-write phases. LLM and
  embedding calls now happen before the first database write, so they run under
  a shared WAL read lock instead of the exclusive write lock. Write lock window
  reduced from O(n × LLM_latency) to milliseconds. Behaviour and public signature
  unchanged; all existing callers unaffected.
- `curate()` auto-trigger inside `consolidate()` now runs in its own separate
  transaction after consolidation commits. A `curate()` failure no longer rolls back
  a successful consolidation. Migration: no change required.
- `total_active_hours` is now incremented via an atomic SQL `UPDATE ... SET value =
  CAST(value AS REAL) + delta`, eliminating a lost-update race when two sessions
  end concurrently in a multi-process deployment.

### Added
- `PRAGMA busy_timeout=10000`: write contention now surfaces as a clear
  `OperationalError` after 10 s instead of hanging indefinitely.
- `PRAGMA wal_autocheckpoint=500`: WAL file is checkpointed every 500 pages
  (down from 1000) to prevent unbounded disk growth under sustained write load.
- `PRAGMA wal_checkpoint(PASSIVE)` runs inside each triggered `curate()` to
  reclaim WAL disk space at a natural maintenance boundary.
- `asyncio.timeout()` on every LLM call inside `consolidate()` (30 s per block
  analysis, 15 s per contradiction check). Timed-out blocks are promoted with
  neutral defaults (confidence 0.50, no tags) and will be re-scored on the next
  consolidation cycle.
- `increment_total_active_hours(conn, delta)` query function for atomic
  active-hours accumulation.
- `co_retrieval_staging` table persists Hebbian co-retrieval counts across
  process restarts. Counts are now durable: an MCP server restart no longer
  resets Hebbian staging to zero. FK CASCADE on `blocks.id` automatically
  removes stale rows when a block is archived, replacing the previous manual
  zombie-cleanup pass in `curate()`.
- `upsert_co_retrieval_count`, `load_co_retrieval_staging`,
  `delete_co_retrieval_pair` query functions for co-retrieval staging
  persistence.
- `MemorySystem.__init__` accepts `initial_co_retrieval_staging` to seed
  in-memory staging from a DB snapshot on startup. `from_config()` populates
  this automatically.

---

## [0.3.0] — 2026-03-26

> Package and documentation hardening for public release.

### Added
- GitHub Pages documentation deployment workflow with MkDocs Material theme
- CI/CD workflows: tests on Python 3.11-3.13, PyPI publishing via OIDC trusted publishing
- Status badges in README (Tests, PyPI, Python version, Codecov, License)
- `.nojekyll` to prevent Jekyll interference with static site deployment
- Enhanced PyPI package metadata: maintainer info, security contact, expanded classifiers

### Changed
- Improved project metadata: author and maintainer email addresses
- Extended classifier coverage for better PyPI discoverability
- Strengthened GitHub Pages configuration to avoid upstream project conflicts

---

## [0.2.0] — 2026-03-26

> First public release. Version 0.1.0 was pre-publication only.


### Added
- Interactive knowledge graph visualization dashboard (`elfmem[viz]`)
  - Force-directed graph with zoom-dependent labels
  - Decay curves, lifecycle flow, and scoring breakdown panels
  - Node type filter pills (decay tier, status, tags)
  - Archived nodes hidden by default; togglable via filter pill
- `elfmem_connect` and `elfmem_disconnect` MCP tools for manual graph editing
- `elfmem_setup` MCP tool for bootstrapping agent identity
- `elfmem_guide` MCP tool for runtime documentation
- Token usage tracking (`TokenUsage`, `session_tokens`, `lifetime_tokens` on `SystemStatus`)
- Hebbian co-retrieval edge creation (C1): blocks co-appearing in `frame()` calls across N sessions promote to `co_occurs` edges
- Edge temporal decay / long-term depression (C2): edges decay exponentially based on inactivity; established edges get LTD protection
- `ConsolidationPolicy`: self-tuning consolidation threshold based on promotion rate feedback
- `FrameResult.edges_promoted`: surfaces co-retrieval promotions per call
- Batch embedding support (`embed_batch`) for ~5x API call reduction during consolidation
- `examples/calibrating_agent.py`: self-calibrating agent with session metrics and per-block verdict tracking
- `examples/decision_maker.py`: multi-frame decision maker with outcome calibration
- `examples/agent_discipline.md`: copy-pasteable system prompt instructions at three tiers

### Changed
- `MemorySystem` now owns the full three-rhythms API directly: `remember()`, `dream()`, `should_dream`, `setup()`
- `SmartMemory` is deprecated in favour of `MemorySystem` directly
- `process_block()` combines `score_self_alignment()` and `infer_self_tags()` into a single LLM call
- All result types implement `__str__`, `.summary()`, and `.to_dict()`
- All exceptions carry a `.recovery` field with the exact command or code to fix the problem
- `begin_session()` is idempotent — safe to call multiple times; counter resets only on new sessions
- `curate()` now purges staging entries for archived blocks (prevents zombie accumulation)
- `scripts/visualise.py` replaces `demo_visualise.py`

### Removed
- **LiteLLM and instructor dependencies removed** (security concerns, large transitive tree).
  Replaced by two official SDK adapters: `AnthropicLLMAdapter` (Anthropic SDK) and
  `OpenAILLMAdapter` + `OpenAIEmbeddingAdapter` (OpenAI SDK). Provider is auto-detected
  from the model name: `claude-*` → Anthropic, all others → OpenAI-compatible.
  OpenAI-compatible APIs (Ollama, Groq, Together, Mistral) work via `base_url`.
- **`SmartMemory` removed.** `MemorySystem` owns the full API directly.
  `MemorySystem.managed()` replaces `SmartMemory.managed()`.

### Fixed
- Empty query string crash in `frame()` when called with `query=""`
- Schema backward compatibility: visualization works with databases created before schema migrations
- `LearnResult.to_dict()` return type corrected to `dict[str, Any]`
- `EmbeddingService` protocol now includes `embed_batch` method
- Ruff E501, SIM105, B904, B905, B007, F841, E402 violations resolved
- `OpenAILLMAdapter` and `OpenAIEmbeddingAdapter` create their SDK clients lazily so that
  operations like `status()` succeed even when `OPENAI_API_KEY` is not set

---

## [0.1.0] — 2026-01-01

### Added
- Initial release
- `MemorySystem` with `learn()`, `frame()`, `recall()`, `outcome()`, `consolidate()`, `curate()`
- Five frames: `self`, `attention`, `task`, `world`, `short_term`
- Four decay tiers: permanent, durable, standard, ephemeral
- 4-stage hybrid retrieval: pre-filter → vector search → graph expansion → composite scoring
- Knowledge graph with centrality, 1-hop expansion, and co-retrieval reinforcement
- Contradiction detection and near-duplicate resolution
- LiteLLM + instructor adapters for 100+ LLM providers
- Mock adapters for deterministic testing without API keys
- FastMCP server with six initial tools
- Typer CLI with seven commands
- SQLite backend via SQLAlchemy Core + aiosqlite
- `ElfmemConfig` via YAML, dict, env vars, or `None` (sensible defaults)
- Session-aware decay: clock ticks only during active use
- `AgentGuide` runtime documentation system
- `ElfmemError` exception hierarchy with `.recovery` field
- 386 tests, all passing with deterministic mocks

[0.2.0]: https://github.com/emson/elfmem/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/emson/elfmem/releases/tag/v0.1.0
