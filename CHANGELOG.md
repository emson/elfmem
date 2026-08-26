# Changelog

All notable changes to elfmem are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
elfmem uses [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

### Added
- **`scripts/hooks/elf_distill.py`** — a `PreCompact`/`SessionEnd` hook,
  increment 2 of automatic memory capture: catches capture-worthy content
  the per-turn regex gate (`elf_context.py`) can't, because it's judgement
  rather than a trigger phrase — a fact stated in passing, a decision that
  emerges across turns. Unlike the other two hooks, this one makes its own
  LLM call (the configured `llm.base_url`/`model`, same as `dream()` uses)
  rather than staying zero-cost: sends the new transcript slice since the
  last distillation plus the SELF frame, asks for durable facts/decisions/
  preferences as a JSON array, writes each via `remember()`. A per-session
  marker (`{session_id}.distilled`) tracks how many transcript lines have
  been processed so repeated firings in one long session never resend the
  same content; the marker only advances after a slice is successfully
  distilled, so a network failure gets retried next time rather than losing
  that slice. Three invocation modes on one script: the hook itself, a
  manual-CLI mode (`--session-id`/`--cwd`/`--transcript-path` flags, same
  LM Studio judgement, triggered by hand), and a host mode (`--host`,
  pre-reasoned candidates supplied on stdin, no LLM call at all) — mirrors
  `dream()`'s existing `host_analyses` pattern, letting a live Claude Code
  session supply its own judgement instead of shelling out to a second one.
  Opt-in, same as the other two hooks; wired to both `PreCompact` and
  `SessionEnd` in this project's `.claude/settings.local.json`.
- **`MemorySystem.record_use(block_ids)`** — records that retrieved blocks
  actually informed an answer, reinforcing them and writing the ledger's
  `use` event. The evidence tier above `frame()`'s automatic assembly record
  and below `outcome()`. Deliberately does *not* touch confidence: use is
  evidence of relevance, never of truth, and folding it into the Beta
  posterior would redefine confidence from "has proven right" to "gets talked
  about" in a term carrying 15-30% of every frame's ranking.
- **`elfmem.memory.attribution`** — pure, LLM-free scoring of which retrieved
  blocks show through in a response, by containment of their distinctive
  terms. `USE_THRESHOLD` is calibrated against a real corpus (148 blocks
  scored against a real 9,746-character answer), not chosen: it admits 4.1%
  of an unrelated corpus, where 0.30 would admit 33%. The error is one-sided
  by design — a paraphrase that reuses no vocabulary is missed and nothing is
  penalised for it, because crediting a block that contributed nothing feeds
  the ranking a signal indistinguishable from real evidence.
- **`scripts/hooks/elf_outcome.py`** — a `Stop` hook closing the loop the
  `record_assembly` docstring names: the voluntary feedback verb has been
  called nine times across three real instances, so reinforcement counted
  retrievals and a block retrieved constantly without being drawn on rose
  exactly like one doing the work. Opt-in alongside the prompt hook. Also
  carries two per-turn gates. Read-side: a prompt that addresses elf by name
  ("as elf, …", "hey elf,") whose answer shows neither prose attribution nor
  an active elfmem call is blocked once with a nudge pointing at the
  already-injected context — never at making more retrieval calls, which the
  use ledger could not tell apart from genuine engagement. Write-side: a
  capture-worthy prompt (correction, stated rule, explicit memory request)
  with no `remember`/`learn` call is blocked once, without requiring a write
  as the only way through — an explicit decision not to store is a valid
  outcome the check can't verify but shouldn't have to. Active-call detection
  now covers both invocation styles this project actually uses interchangeably
  — an MCP tool call or a Bash-invoked CLI command — after a live gap where
  only the MCP name was recognized and a genuine `elfmem recall` run via Bash
  didn't count as engagement.
- **`FrameResult.compose(query)`** — combines the rendered frame and a question
  into one complete prompt. For library callers and agent loops building the
  prompt for a separate model call. Not for MCP tool calls from a chat client:
  the host already holds the question there, so `.text` is what you want.
- **`FrameDefinition.guarantee_excludes`** — tag patterns that disqualify a
  block from a *guaranteed* slot while leaving it free to compete on score.
  `SELF` sets `["peer/%"]`.
- **`scripts/hooks/elf_context.py`** — a `UserPromptSubmit` hook for Claude
  Code that retrieves before the model reads the prompt, so recall stops
  depending on the assistant choosing to call it. ATTENTION on every
  substantive prompt, SELF once per session. Detects when a prompt addresses
  elf by name and, on those turns, adds an engage-or-dismiss line to the
  injected context so attention is primed before the answer is written
  rather than corrected after. Also detects capture-worthy prompts —
  explicit memory requests and clear correction/rule language ("remember
  that", "note that", "that's outdated", "from now on") — and primes the
  same way: store it with a cue if it holds, or decide explicitly that it
  doesn't belong. Opt-in: wire it up in `.claude/settings.local.json`; see
  the module docstring.
- **Substrate migration** as a new `substrate_export` step recognized by the
  existing `elfmem migrate status`/`plan`/`apply` — the same plan-then-apply
  command already used for Claude MCP config drift now also detects when a
  project's database has content not yet exported to the `.elfmem/memory/`
  file substrate, and migrates it: `VACUUM INTO` backup (row-count validated)
  → `export_to_markdown()` → `rebuild_index()` into a fresh `.elfmem/index.db`
  → retrieval-parity check against the original, on the four frame-level
  queries. The live database is only ever read, never written to or deleted —
  every write lands in a new file — so `elfmem migrate apply --undo --id
  <step>` can always safely remove the generated files and reconfirm nothing
  about the original changed. `apply` stops at "exported and verified": it
  does not switch a running agent over to the file substrate (that requires
  further engineering, not yet built — see ADR 0011). `--db`/`--config`
  options added to `migrate status`/`plan`/`apply` for per-project targeting.
  Fixes two real defects in `index_rebuild.py` found via a production-corpus
  dry run and required as prerequisites: rebuilt blocks previously all
  landed under `category="knowledge"` regardless of source file (silently
  breaking `mind_list()`/`mind_show()`/`ls(category=...)` for every
  Theory-of-Mind block after a rebuild); `confidence`/`alpha`/`beta` were
  exported to frontmatter but never read back on rebuild, resetting every
  block's evidence to the neutral default. See
  [ADR 0011](docs/decisions/0011-substrate-migration-as-a-migrate-step.md).
- `MemorySystem.inbox()` / `elfmem inbox` / `elfmem_inbox` — list pending
  blocks not yet consolidated (FIFO, read-only, no LLM calls). Paired with
  a new `host_analyses` parameter on `consolidate()`/`dream()` (CLI:
  `dream --host-analyses FILE.json`; MCP: `elfmem_dream(host_analyses=...)`)
  that lets a host agent session (e.g. this Claude Code session) supply its
  own `{"alignment_score": float, "tags": [...], "summary": str}` per block
  instead of elfmem's configured LLM adapter — no local model or API key
  needed for that path. A covered block is scored exactly as a successful
  adapter call would be (not the neutral `skip_llm` fallback); blocks not
  covered still use the normal path. Input is validated through the same
  schema (`BlockAnalysisModel`) and tag-filtering (`VALID_SELF_TAGS`) a
  real adapter's response already goes through; a malformed entry raises
  the new `HostAnalysisError` (with `.recovery`) rather than degrading
  silently — this is direct structured input, not unreliable external I/O.
  `operations/consolidate.py`'s dedup/promotion pipeline is unchanged and
  fully covered by the existing test suite (verified behavior-preserving);
  `host_analyses` only substitutes where a block's analysis comes from.
- `MemorySystem.metabolism_dry_run()` / `elfmem dream --metabolism-dry-run`
  — edge-metabolism Stage A (`docs/plans/plan_edge_metabolism.md`). For each
  rescore-eligible block, judges a widened top-K embedding shortlist
  (`GOAL_DIRECTED_CANDIDATE_K=30`) against elf's own `self/goal` blocks —
  not cosine similarity — via a new `LLMService.propose_goal_directed_edges()`
  port method (implemented on `AnthropicLLMAdapter`, `OpenAILLMAdapter`,
  `MockLLMService`), and reports up to `GOAL_DIRECTED_MAX_EDGES_PER_BLOCK=3`
  proposed connections with reasoning per block. Goal content is bounded by
  `GOAL_DIRECTED_SELF_GOALS_CHAR_BUDGET=2400` and candidates use each
  block's `summary` (capped by `GOAL_DIRECTED_CANDIDATE_CHAR_CAP=400`) —
  both found necessary, not precautionary: an unbounded first cut blew a
  local model's context window outright on the real self-hosted corpus (see
  the plan doc's "Stage A build findings"). `MetabolismDryRunResult`
  carries the raw `self_goals`/`candidates` alongside `proposals` — always,
  even when no LLM is configured or the call fails (`llm_failures`) — so a
  host agent session can reason over them directly and apply its own
  judgement via the existing `connect()`/`elfmem_connect`, no separate
  "candidates only" mode or new apply path needed. **Read-only: never calls
  `insert_edge`.** No schema migration — reuses the existing, previously
  unused `edges.note`/`.origin`/`.relation_type` columns. This reopens a
  previously-deferred idea (`docs/plans/archive/plan_memory_scoring.md`'s
  "Zettelkasten auto-linking" deferral); applying proposals live (Stage B)
  is a separate, not-yet-approved decision — see the plan doc.
- `elfmem export --to-markdown [--memory-dir DIR]` and `elfmem index
  check|rebuild|parity` (v2 substrate, Wave 1-4): terminal commands for the
  markdown-file substrate work that previously existed only as library code
  with no CLI entry point. `export --to-markdown` writes every DB-native
  block to `.elfmem/memory/**.md` (read-only against the database). `index
  check` parses those files and reports frontmatter errors without opening
  any database. `index rebuild --to PATH` derives a fresh SQLite index from
  the files with zero LLM calls — writes only to `--to`, never a live/
  configured database, and refuses to overwrite a non-empty target without
  `--force`. `index parity [--live-db PATH]` reruns the plan's Phase 4
  retrieval-parity gate as a repeatable, read-only rehearsal: rebuilds a
  throwaway index from the files and compares retrieval against the live
  database, never writing to it. None of these flip the live CLI's
  recall/edit/forget/ls over to the file substrate — that remains a later,
  separate step (see `docs/plans/v2_substrate`).
- `MemorySystem.review_corpus()` / `elfmem review corpus` / `elfmem_review_corpus`
  (v2 step 6a): deterministic staleness detection for ordinary memory — zero
  LLM calls, pure SQL/math over already-active blocks. A block is proposed
  for archival only when three weak signals all agree: long-unused
  (`review.corpus.stale_min_hours_since_reinforced`, default ~30 days),
  rarely reinforced (`stale_max_reinforcement_count`, default ≤2), and never
  confirmed by an outcome. Nothing is applied automatically — proposals go
  through the same interactive accept/reject/skip/quit walkthrough
  constitutional review already uses (`--json`/`--yes` for scripting), and
  `forget()` gains an optional `reason` parameter (default unchanged:
  `ArchiveReason.FORGOTTEN`) so an accepted staleness proposal is applied
  with `reason=ArchiveReason.DECAYED`, keeping the audit trail honest about
  *why* a block was archived. Nested under the existing `elfmem review`
  command group as `review corpus` — the bare `elfmem review` (no
  subcommand) is unchanged and still runs constitutional review;
  duplicate/contradiction detection (the part that needs one whole-corpus
  LLM call) is a later addition to the same command, not built yet.
- `MemorySystem.edit(block_id, content)`, `.forget(block_id)`, `.ls(tag=,
  category=, limit=)` — the direct block mutation API (v2 step 2). Previously
  the only way to change a block's content was an indirect side effect of
  near-duplicate supersession, and there was no delete or list API at all.
  `edit()` re-embeds the new content and clears `summary`/`last_scored_at`
  for the next rescore pass, leaving confidence/reinforcement untouched.
  `forget()` archives with `archive_reason='forgotten'` (the `ArchiveReason.FORGOTTEN`
  enum value existed since the type was defined but had no write path until
  now) and is idempotent — forgetting an already-archived block returns
  `status='already_archived'`, not an error. `ls()` is a deterministic,
  unscored listing (no LLM or embedding calls), distinct from `recall()`/
  `frame()`'s relevance-ranked retrieval. Exposed via CLI (`elfmem edit`,
  `elfmem forget`, `elfmem ls`) and MCP (`elfmem_edit`, `elfmem_forget`,
  `elfmem_ls`).
- `.env` at the project root is now auto-discovered and loaded for every CLI
  command (v2 step 3), via a new `find_env_file()` walk-up matching
  `find_local_config()`'s pattern. Previously `.env` loading was opt-in and
  `serve --env-file`-only (v0.19.3) — `remember`, `recall`, `doctor`, and
  every other command never saw a project's `.env` unless the key happened
  to already be in the real shell environment. Real environment variables
  still always win (unchanged `load_env_file` setdefault semantics).
- `elfmem doctor --resolve`: makes one real LLM call against the configured
  `llm:` section to confirm the key actually works, rather than checking
  only that an API-key string is present in the environment. Opt-in, since
  unlike every other doctor check it costs time and (for hosted models)
  money. `doctor`'s existing "API keys" check now also reports whether the
  key came from `.env` or the real environment.
- `llm.api_key_env` / `embeddings.api_key_env` config fields (v2 step 5):
  name the environment variable your provider's API key actually lives in.
  Previously every OpenAI-compatible adapter always read the literal
  `OPENAI_API_KEY`, regardless of `base_url` — so Together.ai, Groq,
  OpenRouter, or any other provider only worked if you misnamed your key
  `OPENAI_API_KEY`. `AnthropicLLMAdapter` gains the equivalent `api_key`
  constructor param (previously relied entirely on the SDK reading
  `ANTHROPIC_API_KEY`, with no override). Unset (the default) is unchanged
  behaviour: `OPENAI_API_KEY` for OpenAI-compatible adapters,
  `ANTHROPIC_API_KEY` for `claude-*` models. A misconfigured `api_key_env`
  resolves to no key rather than silently falling back to a real-but-wrong
  one — the resolved key is used exactly as passed, never guessed at.
  ```yaml
  llm:
    model: "meta-llama/Llama-3.3-70B-Instruct-Turbo"
    base_url: "https://api.together.xyz/v1"
    api_key_env: "TOGETHER_API_KEY"
  ```

### Changed
- **ADR 0012 — use-aware archival rejected.** Tested against real data before
  building: 148 active blocks scored against 161 real assistant responses. The
  rule "archive high-assembly, low-use blocks" inverts — its top 15 candidates
  are 10 constitutional blocks, including the two most-reinforced blocks in the
  corpus, because SELF-frame guarantees inflate constitutional reinforcement
  5× while dispositional wording is never quoted back. `curate()` stays
  decay-and-graph based, `record_use()` stays reward-only, and the asymmetry is
  now a documented constraint rather than a conservative default.
- **Adapter SDKs import lazily.** `make_llm_adapter`/`make_embedding_adapter`
  import the `anthropic` and `openai` packages inside the branch that uses
  them rather than at module scope. `import elfmem` drops from ~800ms to
  ~200ms, and retrieval-only entry points (a queryless frame, `elfmem ls`, a
  prompt hook) load neither SDK. `elfmem recall --frame self` now returns in
  ~0.8s where it took ~1.5s.
- **Breaking**: `elfmem init`'s `--seed` now defaults to off (v2 step 4).
  Previously a fresh install silently wrote 10 constitutional cognitive-loop
  blocks into memory before you had expressed any preference, costing 10+
  LLM calls to consolidate and requiring `--no-seed` to opt out. A fresh
  `elfmem init` now creates the config and database and writes zero memory
  blocks; text and JSON output both say so explicitly, with the exact
  command to opt in. **Migration**: scripts or automation relying on
  `elfmem init` seeding by default must add `--seed` explicitly.
  `MemorySystem.setup()`'s `seed` parameter and the `elfmem_setup` MCP tool
  default the same way, for consistency across all three entry points —
  callers relying on the old default must now pass `seed=True` explicitly.
  Established instances (config + DB already present) are unaffected either
  way: re-running `elfmem init` without `--seed` is the same idempotent
  no-op refresh it always was.

### Fixed
- **`frame()` no longer zeroes the decay clock of the blocks it returns.**
  `_current_active_hours()` falls back to a 0.0 baseline until
  `begin_session()` reads the real total, and `frame()` reinforces what it
  returns — so calling it without an open session stamped every retrieved
  block as maximally aged, destroying the recency of exactly the blocks just
  judged most relevant. `remember()` has always guarded this with an
  idempotent `begin_session()`; `frame()` and the new `record_use()` now do
  the same. Latent until now because every caller reaching `frame()` through
  the MCP server or `managed()` happened to open a session first.
- **Ledger replay no longer double-counts co-retrieval on `use` events.** A
  `use` event names a subset of an assembly that already formed those pairs,
  so counting it again inflated the association of precisely the pairs
  already strongest. Reinforcement still counts both events. No stored ledger
  contained a `use` event before this release, so no history changes meaning.
- **`frame("self", query=...)` no longer lets the query shape identity.** SELF
  has always been documented as queryless; the code embedded the query anyway,
  let it move 10% of the ranking, then cached the result under a key that
  ignored it — so the first question asked in a session silently fixed elf's
  identity for the next hour, and every later question got that answer back
  regardless of subject. Frames now declare `queryless` and drop the query
  before anything reads it. A query is still accepted and now genuinely ignored.
- **`frame(top_k=N)` is no longer ignored on a cache hit.** `FrameCache` keyed
  on frame name alone, so a `top_k=3` call was served a result cached by an
  earlier `top_k=10` call. The key is now `(frame, top_k)`.
- **Inbound peer letters no longer take elf's identity slots.** The SELF frame
  guaranteed `self/constitutional`, a tag the consolidating LLM assigns freely
  — in a mature instance it had spread to 39 blocks, nine of them peer
  correspondence of up to 1,100 tokens. Correspondence now forfeits the
  guarantee (it can still be retrieved on merit).
- **The SELF template no longer renders peer-authored text as elf's own
  principles.** It now speaks in the imperative — a numbered constitution
  introduced as governing the response — which makes provenance a trust
  boundary rather than formatting. Blocks tagged `peer/inbound` or
  `peer/from:*` render in a separate section, attributed and explicitly marked
  as not instruction.
- `consolidate()`/`dream()` no longer crashes with an unhandled
  `ValidationError` when the configured LLM returns non-JSON text for
  `process_block()` — a real failure mode on local/self-hosted models (seen
  live via LM Studio), not just a timeout. The inbox-processing path only
  caught `TimeoutError`; `rescore_blocks()` already caught both and fell
  back gracefully. `consolidate()` now does the same — the block is
  promoted with neutral fallback scoring and `last_scored_at=NULL`, first
  in line for the next `dream --rescore`, instead of aborting the whole run.
- Near-duplicate consolidation no longer silently supersedes (archives)
  blocks tagged `self/constitutional`. Previously `consolidate()`/`dream()`
  would archive any active block within `near_dup_near_threshold` (0.90)
  cosine of an incoming block with no tier, pin, or tag check — including
  constitutional identity blocks, which lost tags, edges, and evidence in
  the same call (`update_block_status` hard-deletes them on archive). The
  incoming near-duplicate is now promoted alongside the protected block
  instead, and `ConsolidateResult.blocked_supersessions` reports how many
  times this fired so operators can see it rather than lose data silently.
- Ordinary (non-constitutional) supersession now records which block did
  the superseding: `blocks.superseded_by` (schema v6) is set alongside
  `archive_reason='superseded'`, closing the "archived, but by what?" audit
  gap on the path responsible for nearly all archivals in practice.
- README.md and several `docs/` pages brought current with the v2 substrate
  work above — a straight read against `cli.py`/`mcp.py`/`api.py` found the
  README documenting roughly a third of the actual surface (13 of 30 CLI
  commands, 10 of 30 MCP tools, 31 of 55 `MemorySystem` methods) and
  asserting things no longer true post-ADR-0009/0010 ("five retrieval
  frames" — there are four; contradiction detection and automatic
  block archival both listed as core capabilities). Corrected two broken
  example invocations (`elfmem peer init NAME` → `--name NAME`; `elfmem
  export FILE` → `-o FILE`) and a stale `elfmem setup` reference (→ `elfmem
  init`, also fixed in `doctor`'s own peer-inbox error message in
  `cli.py`). Documented the previously-missing CLI surface (`edit`/
  `forget`/`ls`/`inbox`, `templates`, `agent-docs`, `migrate-embeddings`,
  the `review`/`index`/`mind` command groups), all 30 MCP tools, and the
  24 previously-undocumented `MemorySystem` API methods and their return
  types. `guide.py`'s `GUIDES` dict gains the 15 public methods that had
  no `AgentGuide` entry (`from_config`, `from_env`, `managed`, `session`,
  `begin_session`, `end_session`, `close`, `should_dream`,
  `last_learned_block_id`, `last_recall_block_ids`, `session_block_ids`,
  `visualise`, `connect_by_query`, `connects`, `peer_remove`) — a
  standing CLAUDE.md rule this repo wasn't meeting — and fixes the
  existing `setup` entry's `returns`/`example` fields, which still
  described the pre-v2 return shape and the old seed-by-default behaviour.
  Retired `docs/mcp_server_setup.md` (dated March 2026, listed 9 of 30
  commands) outright; rewrote `docs/CLAUDE_CODE_INTEGRATION.md` in place
  rather than deleting it — four other docs point to it as the canonical
  Claude Code integration reference, so the stale/broken setup mechanics
  (invalid `elfmem init` positional-arg syntax, a pre-ADR-0008 MCP config
  path) were stripped while the content unique to it (Agent Discipline,
  Simulation-Based Calibration) was kept and tightened. Refreshed
  `docs/quickstart.md`, `docs/index.md`, `docs/SETUP_AND_CONFIG.md`,
  `docs/elfmem_tool.md`, and `ROADMAP.md` (added the "In Progress" v2
  substrate entry this whole wave was otherwise undocumented under) to
  match; `mkdocs.yml` nav updated for the retired file.

### Removed
- **Breaking**: decay-driven block archival (v2 step 7a, ADR 0009).
  `curate()` no longer archives blocks whose recency falls below
  `prune_threshold` — in months of self-hosted operation this trigger never
  fired once (41 blocks archived `superseded`, 0 `decayed`), while
  `review_corpus()` (step 6a) already covers the same "unused, rarely
  reinforced" signal deterministically, at zero LLM cost, with human review
  before anything is archived. `CurateResult.archived` and
  `MemoryConfig.prune_threshold` are removed. **Migration**: use
  `elfmem review corpus` / `review_corpus()` for staleness detection, and
  `forget(reason=ArchiveReason.DECAYED)` to apply an accepted proposal.
  Decay tier / `decay_lambda` / recency themselves are **not** removed — they
  remain live inputs to retrieval ranking, edge-decay pruning, and curate's
  own top-N reinforcement scoring.
- **Breaking**: pairwise LLM contradiction detection at consolidate-time (v2
  step 7b, ADR 0010). It was the dominant LLM cost of `consolidate()` (up to
  10 contradiction calls per 1 alignment-scoring call per inbox block, ADR
  0007) for a realized yield of 14 lifetime findings, 12 (86%) still
  unresolved — corroborated by MemoryAgentBench's Conflict Resolution
  competency, purpose-built to test this mechanism, scoring 4.8% with it
  fully enabled. Removed: `MemoryConfig.contradiction_threshold` /
  `.contradiction_similarity_prefilter` / `.contradiction_top_k`,
  `LLMConfig.contradiction_model`, `dream()`/`consolidate()`'s
  `skip_contradictions` parameter and the `--skip-contradictions` CLI flag,
  `LLMService.detect_contradiction()` (and all three adapter
  implementations), `ConsolidateResult.contradictions_detected` /
  `.contradictions`, the `ContradictionFinding` type, and
  `ConsolidationHealthMetrics.contradiction_detection_rate` /
  `.prefilter_pass_rate` / `.contradiction_cap_rate`. **Migration**: none
  needed for typical callers (additive fields/flags); custom `LLMService`
  adapters no longer need to implement `detect_contradiction`. **Kept
  unchanged**: the `contradictions` table and contradiction *suppression* at
  recall time (`context/contradiction.py::suppress_contradictions`, live on
  every `frame()`/`recall()` call) — existing findings keep suppressing; new
  content simply isn't auto-checked until a corpus-level LLM review (step
  6b) replaces this write path.
- `memory/dedup.py::find_near_duplicate` / `resolve_near_duplicate` and the
  `EXACT_DUP_THRESHOLD`/`NEAR_DUP_THRESHOLD` constants — dead code with no
  callers; the live near-duplicate/supersede logic has lived in
  `operations/consolidate.py` since an earlier refactor. `cosine_similarity`
  is unaffected and remains in `memory/dedup.py`.

## [0.19.3] — 2026-07-14

### Changed
- `mcp_json_snippet()` (the `elfmem init`-printed / CLAUDE.md-embedded MCP
  entry) now resolves the currently-running `elfmem` executable to an
  absolute path for `command`, instead of a bare `"elfmem"` string that
  depends on the spawning process inheriting the right `PATH` — this broke
  for project-local `uv`-managed venvs where `elfmem` only exists at
  `.venv/bin/elfmem`. Existing entries generated by the old snippet keep
  working unchanged; only `elfmem migrate apply` upgrades them, and only
  when asked.
- `elfmem serve` gains a new `--env-file PATH` option: loads `KEY=VALUE`
  pairs from a dotenv-style file into the environment before starting,
  without overriding variables already set. `mcp_json_snippet()` appends it
  automatically when a `.env` exists at the project root, so a spawned MCP
  subprocess reliably receives `OPENAI_API_KEY`/`ANTHROPIC_API_KEY` instead
  of silently degrading to mock/no-op behaviour.

### Fixed
- `elfmem migrate`'s scanner (`DEFAULT_SCAN_PATHS`) never included
  `~/.claude.json` — Claude Code's actual global, per-project MCP config —
  and couldn't parse its nested `projects[<path>].mcpServers` shape at all
  (it only understood the flat `mcpServers` shape used by `.mcp.json`). Both
  are now scanned and detected.
- New drift check: an MCP entry nested under a project in `~/.claude.json`
  whose `--config` points at a *different* project's config (stale copy,
  hand-edit, or moved project) is now flagged by `elfmem doctor --migrate-mcp`
  / `elfmem migrate status` and fixable via `elfmem migrate apply` — this is
  the exact failure mode found in elfmem's own dev instance, where its MCP
  entry had drifted to an unrelated global config/db with no peer identity
  configured.

Known limitation: `elfmem migrate apply` hashes the whole source file to
detect concurrent edits before writing. `~/.claude.json` is actively
rewritten by Claude Code itself for unrelated per-session bookkeeping, so a
`plan` → `apply` gap spanning another Claude Code session may report
`"stale"` and ask you to re-plan even though the specific entry you're
fixing hasn't changed. Fails safe (never a wrong write) — see ADR 0008's
"Post-implementation review findings" for the accepted-risk rationale.

See [ADR 0008](docs/decisions/0008-mcp-entry-default.md) for the full design
rationale and alternatives considered.

## [0.19.2] — 2026-07-02

### Added
- `consolidation.contradiction_top_k` (default 10): caps contradiction-detection
  LLM calls per inbox block to the K most similar active blocks passing the
  existing cosine prefilter, bounding worst-case per-block cost to O(K)
  regardless of active-set size. New `ConsolidationHealthMetrics.contradiction_cap_rate`
  reports how often the cap actually binds.
- `consolidation.max_inbox_per_run` (default 5): bounds how many inbox blocks
  one `consolidate()`/`dream()` call processes. `ConsolidateResult.inbox_remaining`
  reports what's left; call `dream()` again (or loop on it, like
  `learn_document()` already does) to drain a larger backlog.

### Changed
- `dream --max N` previously only affected `--rescore`'s budget (a no-op
  without `--rescore`). It now *also* bounds inbox processing in the same
  invocation via the new `consolidation.max_inbox_per_run` budget above —
  existing automation using `dream --rescore --max N` expecting N to bound
  only the rescore pass will now also have inbox processing capped to N in
  that same call. Run inbox processing and `--rescore` as separate calls if
  you need independent budgets for each.
- `rescore_blocks()` now commits each block in its own transaction instead of
  sharing one transaction for the whole batch — a crash partway through a
  `--rescore` run now preserves every block already rescored, instead of
  losing the whole batch. `rescore()`'s docstring claim of "brief write locks
  per block" is now accurate (previously aspirational).
- `get_inbox_blocks()` now returns inbox blocks in explicit FIFO order
  (oldest first), so `max_inbox_per_run` truncation can't starve old blocks.

See [ADR 0007](docs/decisions/0007-bound-and-checkpoint-consolidation.md) —
motivated by `elfmem dream` being killed repeatedly against a slow local LLM
adapter with zero forward progress. Per-block durability inside
`consolidate()` itself (the counterpart fix for the inbox-processing path) is
scoped as a follow-up.

---

## [0.19.1] — 2026-06-06

Consolidation observability: `ConsolidationHealthMetrics` on
`ConsolidateResult.health` surfaces five diagnostic ratios per cycle
(edge_creation_rate, contradiction_detection_rate, prefilter_pass_rate,
promotion_rate, deduplication_rate) without any behavioural change.
Closes [#73](https://github.com/emson/elfmem/issues/73) and defers
multi-parameter self-tuning in [ADR 0006](docs/decisions/0006-defer-multi-parameter-self-tuning.md)
with documented reopen triggers. Same shape as v0.18.1 (per-pair
contradiction signals): additive observability on an existing return
type.

Two supporting changes ride along: CI now enforces ROADMAP↔docs/roadmap.md
sync (closing an 8-day drift window), and `AGENTS.md` was added with a
vendor-neutral memory-routing rule earned from a peer message from Mira
(routing facts to identity memory vs. session memory).

### Added

- `ConsolidationHealthMetrics` on `ConsolidateResult.health` — five
  diagnostic ratios per consolidation cycle (`edge_creation_rate`,
  `contradiction_detection_rate`, `prefilter_pass_rate`,
  `promotion_rate`, `deduplication_rate`). Observability only — no
  policy or runtime behaviour reads them. Enables future detection of
  systematically-misbehaving static thresholds without committing to
  adaptive tuning. Default `None` on the empty-inbox path and on
  externally-constructed `ConsolidateResult` instances. Issue #73,
  [ADR 0006](docs/decisions/0006-defer-multi-parameter-self-tuning.md).
- `ConsolidationHealthMetrics` exported from the package root for the
  same agent-friendly import surface as other public types.
- `AGENTS.md` — vendor-neutral guidance file readable by any AI coding
  agent. Currently contains the memory-routing rule (identity memory vs.
  session memory; verb-level shibboleth / survival test / audience test).
  `CLAUDE.md` defers to `AGENTS.md` for the rule and contributes only
  Claude-specific particulars.

### Changed

- CI `lint` job now runs `scripts/sync_roadmap.sh --check` as its first
  step, enforcing the previously-documented-but-unchecked contract that
  `docs/roadmap.md` mirrors `ROADMAP.md`. Caught 8 days of pre-existing
  v0.19.0 header drift.

---

## [0.19.0] — 2026-05-25

Peer-protocol hardening: the four bugs that surfaced while elf tried to reply
to Alv (peer registry empty despite YAML declaration, `outbox/alv/` vs
`inbox/elf-alv/` slug drift, non-atomic envelope writes, silent black-hole
sends to uninitialised recipients) are all fixed. The fixes are surgical —
no envelope-schema break, no message-id change — so v0.18 peers remain wire
compatible.

### Added

- `peers:` top-level list in `config.yaml` is now load-bearing. Each entry is
  a `PeerSpec` with fields `name` (required), `did` (derived from `name` as
  `elf:<slug(name)>` when omitted), `description`, `project_root`, `db_path`,
  `delivery_path` (derived from `project_root` when omitted), and `trust`
  (default 1.0). `MemorySystem.from_config()` syncs declared peers into
  `peer_roster` on engine startup — insert-only, so existing operational
  state (trust adjustments, message counters) is preserved across restarts.
  Resolves the historical bug where `peers:` was silently ignored by the
  pydantic loader.
- `operations.peer.canonical_did(conn, to_peer)` — resolves a recipient
  argument (DID or display name) to its canonical DID. Used by `peer_send`
  so callers passing a display name produce the same outbox folder as
  callers passing the DID. Look-up is name-first against `peer_roster`,
  falling back to `elf:<slug(name)>` for unknown names.
- `operations.peer.sync_peers_from_config(conn, peers)` — idempotent
  upsert of declarative peer state into the roster.
- `operations.peer.migrate_legacy_outbox_slugs(conn, outbox_dir)` —
  one-shot rename of pre-canonical `outbox/<name-slug>/` folders to the
  canonical `outbox/<did-slug>/` form. Refuses to rename when both folders
  exist; preserves audit history.
- Recipient-readiness precondition: `peer_send` to a peer with
  `delivery_path` now verifies `<delivery_path>/../config.yaml` exists
  before writing. Missing marker raises `PeerError` with the exact
  `'elfmem init'` invocation in `.recovery`.
- `config.PeerSpec` accepts the legacy `identity:` field as `description:`
  (one-release deprecation) so v0.18 configs upgrade without edits.

### Changed

- `_write_message_file` writes envelopes atomically via a dotfile-staged
  temp + `os.rename`. Duplicate sends of identical content are now true
  no-ops (idempotent skip when destination exists), aligning the on-disk
  behaviour with the content-addressable `msg_id` design.
- `_resolve_delivery` derives the outbox subdirectory from the canonical
  recipient DID (not the raw `to_peer` argument), so `peer_send("Alv", ...)`
  and `peer_send("elf:alv", ...)` land in the same folder.
- `ElfmemConfig` rejects malformed `peers:` at load: duplicate DIDs,
  self-referential entries (DID equal to `peer.identity`), and shared
  `project_root` values fail fast with a recovery hint identifying the
  offending names.

---

## [0.18.1] — 2026-05-24

Small additive surface release: each contradiction detected by
``consolidate()`` is now returned as a typed ``ContradictionFinding``
on ``ConsolidateResult.contradictions`` (alongside the existing count),
carrying detection-time signals — ``cosine``, ``tag_jaccard``,
``category_match``, ``hours_apart`` — that agents can use to gate
per-deployment suppression rules. Closes the agent-side gap raised in
[issue #50](https://github.com/emson/elfmem/issues/50).

No schema change, no migration, no suppression-semantics change. Pure
additive API surface — every existing reader of ``ConsolidateResult``
or its ``to_dict()`` continues to work unchanged; the new field is an
empty list when no pairs are detected.

### Added

- ``ContradictionFinding`` result type and ``ConsolidateResult.contradictions``
  list — each detected pair is surfaced with its detection-time signals
  (``cosine``, ``tag_jaccard``, ``category_match``, ``hours_apart``)
  alongside the existing ``contradictions_detected`` count. Closes the
  agent-side gap from [issue #50](https://github.com/emson/elfmem/issues/50):
  agents can now apply per-deployment suppression rules (e.g. "high cosine
  with high tag overlap likely indicates same topic, not contradiction")
  directly on ``dream()`` output, without an extra query to recompute
  features from current block state. Signals are not persisted — they
  reflect the moment of detection, by design — and not used for core
  suppression. Exported from ``elfmem``; appears in
  ``ConsolidateResult.to_dict()`` for MCP/CLI consumers.

---

## [0.18.0] — 2026-05-23

First milestone of the constitutional review work. v0.18 ships the
manual constitutional review mechanism deferred by
[ADR 0003](docs/decisions/0003-defer-constitutional-evolution.md): a
read-only ``review_constitutional()`` call surfaces drifted
``self/constitutional`` blocks as LLM-proposed amendments, and an
explicit ``accept_amendment()`` applies them with a full pre/post audit
trail. The design choice is recorded in
[ADR 0004](docs/decisions/0004-manual-constitutional-review.md): every
mechanism rejected by ADR 0003 was AUTOMATIC; this one is structurally
different — manual surface, explicit consent, one-step undo, no
scheduled trigger anywhere in the pipeline.

The earlier longitudinal Monte-Carlo simulation
(``scripts/longitudinal_sim/mc_constitutional_review.py`` in the
research compilation) reported **+9-14pp retrieval quality across the
drifting scenarios with zero stable-case tax** — the property all four
automatic mechanisms in ADR 0003 failed to deliver. The headline
regression test for the end-to-end loop is in
``tests/test_amendment_apply.py::TestIntegration::test_review_accept_then_re_review_skips_cooled_block``:
accept one of two drifted proposals, re-run review immediately, the
amended block is in cooldown and only the un-accepted block is
re-surfaced.

This release is purely additive — every existing operation behaves
exactly as in 0.17.

### Added
- Schema v5: ``block_amendments`` audit table — substrate for
  constitutional review (MANUAL surfacing + explicit accept; see
  [ADR 0003](docs/decisions/0003-defer-constitutional-evolution.md)
  and [ADR 0004](docs/decisions/0004-manual-constitutional-review.md)).
- Result types: ``ProposedAmendment``, ``ConstitutionalReviewResult``,
  ``AmendmentResult``, ``AmendmentRecord`` (exported from ``elfmem``).
- Drift detection module ``elfmem.operations.review`` — pure math
  (``compute_drift``, ``recent_self_centroid``) plus pure-read DB
  helpers (``fetch_recent_reinforced_embeddings``,
  ``fetch_constitutional_blocks``).
- ``MemorySystem.review_constitutional()`` — READ-ONLY: surfaces drifted
  ``self/constitutional`` blocks as LLM-proposed amendments. MANUAL
  cycle: nothing is applied without an explicit ``accept_amendment``
  call. Returns ``ConstitutionalReviewResult`` with the proposals,
  reviewed/skipped/failed counts, and an ``insufficient_history`` flag
  for fresh databases (cold-start safe — no LLM calls when history is
  insufficient).
- ``ReviewConfig`` (nested under ``ElfmemConfig`` as ``review``) with
  9 tunables: ``drift_threshold`` (0.35), ``min_recent_reinforced_blocks``
  (20), ``window_hours`` (30d), ``min_reinforcement`` (2), ``top_n`` (50),
  ``cooldown_hours`` (90d), ``max_proposals`` (5), ``min_block_evidence``
  (2.0 of α+β), ``min_age_days`` (30d).
- ``LLMService.propose_amendment`` protocol method, implemented by
  ``AnthropicLLMAdapter``, ``OpenAILLMAdapter``, and ``MockLLMService``.
- ``AMENDMENT_PROPOSAL_PROMPT`` in ``elfmem.prompts``.
- ``MemorySystem.accept_amendment(block_id, proposed_content, ...)`` —
  MUTATING: applies a proposed amendment to a constitutional block.
  Embedding runs OUTSIDE the DB transaction; the transaction inserts one
  ``block_amendments`` audit row and updates the block (content,
  embedding, ``summary = NULL``, ``last_scored_at = NULL``). The Beta
  sufficient statistics (α, β), ``reinforcement_count``, and
  ``last_reinforced_at`` are deliberately unchanged — content edits are
  not knowledge-confirmation events. Invalidates the ``self`` frame cache.
- ``MemorySystem.revert_amendment(amendment_id)`` — one-step undo:
  restores ``block.content`` to the amendment's ``pre_content`` (not the
  original-from-creation content). Stamps ``reverted_at`` on the audit
  row rather than deleting it. Raises ``AmendmentAlreadyReverted`` on a
  double revert.
- ``MemorySystem.list_amendments(block_id=None, limit=100)`` — newest-first
  audit history, optionally filtered by block. Returns
  ``list[AmendmentRecord]`` (includes reverted amendments — absence
  would corrupt the audit trail).
- Exceptions: ``BlockNotFound``, ``AmendmentNotFound``,
  ``AmendmentAlreadyReverted``. Each carries a ``.recovery`` field per
  the agent-first contract; exported from ``elfmem``.
- CLI: new ``elfmem review`` subcommand group, mirroring the ``peer``
  pattern. Four commands:
  - ``elfmem review`` — interactive review when stdin/stdout is a TTY
    (accept / reject / skip / quit per proposal); JSON-only when
    piped, ``--json``, or ``--yes`` (auto-accept all).
  - ``elfmem review accept <block_id>`` — apply an amendment from
    ``--content-file PATH`` or piped stdin. Acceptor recorded as
    ``"user"``. Confirms before writing unless ``--yes``.
  - ``elfmem review revert <amendment_id>`` — one-step undo. Shows
    the content that will be restored, confirms unless ``--yes``.
  - ``elfmem review list [--block ID] [--limit N]`` — newest-first
    table of amendment history; ``reverted`` rows are clearly marked.
  All commands accept ``--json`` for machine-readable output.
- MCP: four new tools wrapping the v0.18 API. Acceptor is hard-coded
  to ``"agent"`` on the MCP path; ``ElfmemError`` is caught at the tool
  boundary and surfaced as ``{"error": ..., "recovery": ...}`` so
  calling agents can branch on the recovery hint without parsing
  free-form text.
  - ``elfmem_review_constitutional`` — returns the
    ``ConstitutionalReviewResult`` dict (cold-start safe).
  - ``elfmem_accept_amendment`` — applies a proposal; returns
    ``AmendmentResult`` dict.
  - ``elfmem_revert_amendment`` — one-step undo by amendment_id.
  - ``elfmem_list_amendments`` — returns ``{"amendments": [...]}``,
    newest first.

---

## [0.17.0] — 2026-05-23

Third milestone of the memory-scoring architecture work driven by
[issue #50](https://github.com/emson/elfmem/issues/50). Where v0.15.2
removed the confidence cliff and v0.15.3 surfaced cold-start blocks
through a centrality floor, v0.17 rebuilds the substrate underneath
confidence itself: blocks now store the Beta posterior's sufficient
statistics ``(α, β)`` directly, and every mechanism that updates
confidence does so as additive Bayesian evidence. ``confidence`` is the
denormalised view ``α/(α+β)`` — always consistent within a single
transaction, never overwritten in isolation.

The bundle scope and the empirical case for it are documented in
[ADR 0002](docs/decisions/0002-v017-scope.md); the original planning
exercise (now archived) is in
[`docs/plans/archive/plan_memory_scoring.md`](docs/plans/archive/plan_memory_scoring.md).
Related decisions: power-law decay rejected
([ADR 0001](docs/decisions/0001-power-law-decay-rejected.md));
constitutional evolution deferred to v0.18+
([ADR 0003](docs/decisions/0003-defer-constitutional-evolution.md)).

Headline numbers (validated by the regression fixtures, not estimates):

- Rescore damage at α=15, β=2 (mature block, alignment drop 0.882 → 0.55):
  old clobber Δconfidence ≈ 0.332 → new additive Δ ≈ 0.009 — **22×
  smaller** (and up to ~36× at higher α+β).
- Long-horizon simulation: **+5.6pp retrieval quality at 730 simulated
  days** over the v0.15 substrate.
- Peer bundles cross-compatible: **BUNDLE_VERSION 1 ↔ 2** — v0.17
  instances read v1 (confidence-only) bundles from v0.15/0.16 peers and
  v0.15/0.16 instances ignore the new (α, β) fields gracefully.

### Added

- `success_count` and `failure_count` columns on the ``blocks`` table —
  Beta sufficient statistics, defaulted to the Jeffreys prior
  (α=β=0.5). Schema migrated automatically from v3 to v4 on first open;
  existing rows bootstrapped from ``confidence × (1 + outcome_evidence)``
  so the migration preserves both current confidence and cumulative
  event count exactly. ``confidence`` and ``outcome_evidence`` become
  denormalised views maintained on every write.
- `compute_bayesian_update_ab(success_count, failure_count, signal,
  weight) -> (α, β, confidence)` — pure-function sufficient-statistics
  form of the Beta-Binomial update; the canonical entry point from
  v0.17 forward.
- `merge_peer_evidence(local_α, local_β, remote_α, remote_β, trust)
  -> (α, β)` — trust-weighted arithmetic merge of two Beta-Binomial
  observations. Internal helper, surfaced because it is the contract
  between this release's peer code and any future custom importer.
- `memory.rescore_evidence_weight` config (default 0.5) — weight of the
  rescore alignment as a Beta-Binomial evidence event. Validated
  ``ge=0.0``; zero is a meaningful "refresh metadata only, no
  confidence update" mode.
- Exploration bonus in ``compute_score``: ``κ × √(α·β / ((α+β)² ·
  (α+β+1)))`` with **κ = 0.05** hardcoded ([ADR 0002](docs/decisions/0002-v017-scope.md)).
  Self-extinguishes — ~0.018 on a Jeffreys prior, ~0.0025 on a
  100-event mature block — so no frame gating is needed. Applies
  uniformly to recall and curate scoring.
- Bundle format **BUNDLE_VERSION = 2** — exports ship
  ``success_count`` and ``failure_count`` alongside ``confidence``.
  v0.17 importers still accept v1 bundles (older senders); v0.15/0.16
  importers silently ignore the extra v2 fields.

### Changed

- ``rescore()`` is **additive**: the new alignment is folded into the
  block's Beta posterior as one weighted evidence event (weight =
  ``memory.rescore_evidence_weight``), no longer clobbers
  ``confidence``. Mature blocks barely move; cold blocks track the new
  alignment. This is the headline behaviour change of the release —
  the regression test at α=15, β=2 is pinned in
  ``tests/test_additive_rescore.py``.
- ``import_blocks()`` (peer merge) on **re-import of known content** is
  now an arithmetic merge: ``α' = local_α + remote_α × trust``, ``β' =
  local_β + remote_β × trust``. Fresh imports seed at ``α = 0.5 +
  remote_α × trust``, ``β = 0.5 + remote_β × trust`` (Jeffreys prior +
  trust-scaled remote evidence). Replaces the v0.16 early-return that
  silently dropped peer corroboration. ``trust=0.0`` ignores the peer
  entirely; ``trust=1.0`` accepts the full remote evidence.
- ``consolidate()`` promotion now seeds (α=confidence, β=1−confidence)
  — total prior mass 1.0 — so a fresh block satisfies the invariant
  ``confidence == α/(α+β)`` from birth. Earlier behaviour left α=β=0.5
  on newly promoted blocks, which meant the first outcome update had
  to "earn back" the alignment-derived confidence.
- ``update_block_scoring()`` and ``update_block_outcome()`` accept (α,
  β) and derive ``confidence`` and ``outcome_evidence`` in the same
  UPDATE statement. The invariant ``confidence == α/(α+β)`` is
  unbreakable: explicit ``confidence`` arguments are silently
  overridden by the derived value when sufficient statistics are
  supplied.

### Deprecated

- ``compute_bayesian_update(confidence, outcome_evidence, signal,
  weight, prior_strength) -> float`` — retained as a thin wrapper over
  the new sufficient-statistics form. Scheduled for removal in v0.18+.
  Migrate to ``compute_bayesian_update_ab`` (see docstring for the
  one-line conversion).
- ``memory.outcome_prior_strength`` and ``peer.confidence_floor``
  config fields — no longer consulted; retained for one release so
  existing YAML configs keep loading. Removal in v0.18+.
- ``ImportResult.confidence_floor`` — populated but informational only;
  v0.17 peer imports do not gate on a floor (the trust-scaled
  arithmetic merge subsumes the heuristic).

### Removed

- ``prior_strength`` keyword from ``record_outcome`` and
  ``mind_outcome`` — unused after the substrate landed; α and β are
  read directly off the block. Callers passing the old kwarg get a
  ``TypeError`` (correct fail-fast behaviour for an internal API).
- ``_peer_confidence(floor, trust)`` heuristic — the
  ``floor × 1.5 if trust >= 0.7 else floor`` ramp is fully subsumed
  by ``merge_peer_evidence``.

### Migration notes

- **Library callers** of ``record_outcome`` / ``mind_outcome``: drop
  the ``prior_strength=`` kwarg. No other change required.
- **Library callers** of ``compute_bayesian_update``: still works; one
  release of grace before removal. New code should import
  ``compute_bayesian_update_ab`` from ``elfmem.operations.outcome``.
- **Peer operators** running mixed v0.15/0.17 fleets: nothing to do.
  Bundles cross-version cleanly.
- **Config files**: ``outcome_prior_strength`` and
  ``peer.confidence_floor`` keys keep loading; they have no effect
  in v0.17 and will produce a ``ValidationError`` when removed in
  v0.18+.

---

## [0.15.3] — 2026-05-18

Second milestone of the memory-scoring architecture work driven by
[issue #50](https://github.com/emson/elfmem/issues/50). v0.15.2 removed
the confidence cliff (which contributed ±0.03 to the retrieval gap).
This release addresses the *dominant* term in Dmitry's symptom: graph
centrality, which contributed ±0.105 — 3.5× larger than the cliff.

### Fixed

- Fresh blocks no longer lose top-K retrieval to bedrock on graph
  centrality alone. A cold-start centrality floor lifts blocks with
  few edges (raw centrality < 0.10) and high recency (> 0.70) to a
  recency-scaled floor value (peak 0.50 × recency at recency = 1.0).
  The floor self-extinguishes as the block either ages or builds
  graph connections — no permanent protection. Affects retrieval
  scoring only; curation, archival, and visualisation paths see raw
  centrality unchanged. Implemented as a new pure helper
  `effective_centrality()` in `src/elfmem/scoring.py`. The frozen
  `compute_score()` formula is unchanged; the floor adjusts the
  centrality input before scoring. See
  `docs/plans/plan_v0.15.3_centrality_floor.md` for the full design
  rationale, including edge-case analysis and per-tier freshness
  window behaviour.

---

## [0.15.2] — 2026-05-17

First milestone of the confidence architecture work driven by
[issue #50](https://github.com/emson/elfmem/issues/50). The full analysis
and four-milestone plan live in `docs/plans/plan_confidence_architecture.md`
(internal — not yet published).

### Fixed

- `consolidate()` no longer snaps below-threshold alignment_scores to a
  flat 0.50, removing the 0.20 step discontinuity at
  `self_alignment_threshold=0.70`. The new mapping is identity
  (`confidence = analysis.alignment_score`), aligning `consolidate.py`
  with `rescore.py:245` — the two paths previously disagreed. A block
  the LLM rates at α=0.65 now lands at confidence=0.65 instead of being
  flattened to 0.50; this is a real boost for fresh blocks that just miss
  the historical threshold. LLM-timeout / `skip_llm=True` paths still
  land at confidence=0.50 because `_fallback_analysis()` returns
  `alignment_score=0.50`. The `self_alignment_threshold` config field
  is no longer consulted in the cliff but is retained as an accepted
  parameter for backwards compatibility; a future release will deprecate
  it formally once the architecture decision in
  `plan_confidence_architecture.md` is finalised.

### Note on scope

This is the smallest-possible correctness fix and does NOT address
the broader cold-start retrieval symptom Dmitry reported. Simulation
work showed that the cliff contributes only ±0.03 to the ATTENTION
score gap; the dominant term is centrality (±0.105). The retrieval
side of the issue is the subject of v0.16.x work — see the architecture
doc for the four-milestone plan.

---

## [0.15.1] — 2026-05-17

Two correctness fixes from [Dmitry's report (#50)](https://github.com/emson/elfmem/issues/50).
Both bugs caused silent under-reporting — one of data, one of usage.

### Fixed

- `connect(source, target, relation=X, if_exists='reinforce')` no longer
  silently drops the caller's `relation` when it disagrees with the stored
  edge. The default reinforce branch now raises `ConnectError` with a
  `.recovery` hint pointing the agent at `if_exists='update'`. Passing no
  relation (the new default) or a matching relation reinforces silently
  as before — only explicit semantic conflict raises. The `relation`
  parameter default changed from `"similar"` to `None` across
  `MemorySystem.connect`, `connect_by_query`, `ConnectSpec`, and the
  `elfmem_connect` MCP tool. Two consequent behaviour changes for callers
  who relied on the old default:
    - **reinforce** (default `if_exists`): callers who passed an explicit
      relation matching the stored value are unaffected; callers passing
      a conflicting relation now see a `ConnectError` instead of silent
      drop — the recovery hint tells them how to proceed.
    - **update**: `connect(A, B, if_exists='update')` with no relation
      kwarg now preserves the stored relation (PATCH semantics). Previously
      it silently reset to `"similar"`. Callers who relied on that reset
      must now pass `relation="similar"` explicitly.
- LLM and embedding token counters now record the call count even when
  the provider omits `usage` (common on LM Studio, Ollama, and other
  local OpenAI-compatible servers). Previously, missing or `None`
  `usage.prompt_tokens` caused both token count *and* call count to be
  dropped, so `elfmem status` lifetime counters under-reported on
  local-server setups. `TokenCounter.record_llm` and `record_embedding`
  now default their token arguments to `0`; adapters always call them
  on completion.

---

## [0.15.0] — 2026-05-17

The embedding-model lock. Closes the silent-corruption risk Dmitry flagged
after a month of production use: changing `embeddings.model` in
`config.yaml` previously rendered the DB's stored vectors meaningless
without warning, because cosines between different models' vector spaces
are noise. Ships in two phases bundled together — the lock infrastructure
that detects the mismatch, and the migration verb that lets users recover.

No schema change. Existing healthy installs see no behavioural change on
upgrade — the lock backfills transparently from the existing
`blocks.embedding_model` column. Existing installs with already-heterogeneous
data (rare; from a previous undetected model swap) get a loud
`EmbeddingLockError` with a recovery hint pointing at `elfmem migrate-embeddings`.

### Added — `elfmem migrate-embeddings` (Phase 2 of [#50 follow-up](https://github.com/emson/elfmem/issues/50))

The recovery path for the `EmbeddingLockError` introduced in Phase 1. Without
this, a user hitting a mismatch could not re-embed their corpus without
manual SQL surgery.

- **`elfmem migrate-embeddings`** (new top-level command):
  - Default mode: **estimate** (no writes). Reports block count, total
    content character count, rough token estimate, and the target model.
  - `--execute`: actually re-embed. Backs up the DB, re-embeds in batches
    of ~50 within per-batch transactions, drops `origin IN ('similarity',
    'co_retrieval')` edges (preserves `origin = 'user'`), updates the
    embedding lock at the end. Auto-resumes if interrupted — already-
    migrated blocks are skipped via the SQL `WHERE` filter.
  - `--to <model>`: override target (default: `embeddings.model` from config).
  - `--from <model>`: only migrate blocks currently tagged with this model.
    Used to disambiguate heterogeneous-source DBs.
  - `--batch <N>`: blocks per transaction (default 50).

- **Critical design property**: the migration verb **bypasses** the
  `LockedEmbeddingService` wrapper installed by `MemorySystem.from_config()`.
  It constructs a bare `EmbeddingService` via `make_embedding_adapter()` and
  a bare engine directly. If it went through `from_config()` the wrapper
  would see the new model disagree with the OLD lock and self-block —
  the recovery command would be unable to recover. Verified by
  `test_execute_bypasses_locked_wrapper`.

- **SQL NULL trap caught on review**: the resumability filter must be
  `WHERE embedding_model IS NULL OR embedding_model != :target`. A naive
  `!= :target` would silently skip NULL rows because `NULL != X` evaluates
  to `NULL` (falsy in SQL). Verified by `test_estimate_counts_null_*` and
  `test_execute_handles_null_*`.

- **Embeds the same text consolidate.py does**: `summary.strip().lower()`
  when summary is set; else `content.strip().lower()`. Matches what
  `consolidate.py:343-344` writes to the `embedding` column. Verified by
  `test_execute_uses_summary_when_present`.

9 new tests covering estimate accuracy, execute correctness, resumability,
NULL-row handling, wrapper-bypass, and the heterogeneous `--from` path.

### Added — embedding-model lock (Phase 1 of [#50 follow-up](https://github.com/emson/elfmem/issues/50))

Closes the silent-corruption bug Dmitry reported after a month of production use:
changing `embeddings.model` in `config.yaml` previously corrupted the DB without
warning (cosine similarities between vectors from different models are noise).

- **`LockedEmbeddingService`** (`src/elfmem/adapters/locked.py`): wraps the
  configured `EmbeddingService`. Every `embed()` / `embed_batch()` call verifies
  that the adapter's `model_name` and the produced vector's length match the
  locked values stored in `system_config`. First-ever embed sets the lock
  atomically (`INSERT OR IGNORE` + read-back, race-safe). No cache — verify on
  every call avoids the staleness window that a per-session cache would create
  for long-lived MCP-server sessions. Cost is one sub-ms SELECT per embed; the
  surrounding LLM call is 10-500ms, so the overhead is noise.
- **`backfill_embedding_lock_if_needed()`** (`src/elfmem/db/queries.py`): called
  from `MemorySystem.from_config()` after schema migrations. Three outcomes for
  existing installs:
  - All active blocks with `embedding_model` set agree on one value →
    transparent lock-set; legacy `NULL`/`""`/`"unknown"` rows backfilled.
  - Two or more distinct known models → `EmbeddingLockError` with a recovery
    pointing at `elfmem migrate-embeddings --from <model> --to <model> --execute`.
  - All-legacy (every active block has unknown model) → `EmbeddingLockError`
    with recovery pointing at `elfmem migrate-embeddings --execute`. We
    deliberately don't silently assume the current adapter is correct.
- **`EmbeddingLockError`**: new `ElfmemError` subclass; every raise carries a
  `.recovery` field with the exact command the user/agent should run next
  (per the agent-first contract).
- **`elfmem doctor` "Embedding lock" surface**: non-raising; reports `OK`,
  `FRESH` (no lock yet), or `MISMATCH` with both recovery commands. Diagnostic
  must never be blocked by the state it's diagnosing.

### Migration

- **Healthy installs**: transparent. First boot after upgrade backfills the
  lock from existing homogeneous `blocks.embedding_model` data. No user action.
- **Installs with already-heterogeneous data** (rare; previous undetected model
  swap): loud `EmbeddingLockError` on first command after upgrade. The error
  surfaces existing corruption — it doesn't introduce a new failure. Recovery
  via `migrate-embeddings` (Phase 2, shipping immediately after).
- **No schema change**: the lock uses two new `system_config` keys
  (`embedding_model_lock`, `embedding_dimensions_lock`). The per-row
  `blocks.embedding_model` column already exists.

Design rationale: see `docs/plans/plan_embedding_lock.md`. Phase 2 (the
`elfmem migrate-embeddings` recovery command) ships next.

---

## [0.14.0] — 2026-05-16

Three threads land together: a docs sweep that brings every user-facing
surface in line with the live frame registry; MCP/CLI parity for the
v0.13.3 `dream` flags and the Theory of Mind API (closes external issue
[#50](https://github.com/emson/elfmem/issues/50)); and a new opt-in concept,
**named agents** — set `project.agent_name` and the rendered `.elfmem/AGENT.md`
fragment teaches the host LLM what your agent's name means.

No breaking changes. Existing installs without `agent_name` see no behavioural
change; default `elfmem_dream()` is byte-identical to v0.13.3.

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
