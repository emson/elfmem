# Changelog

All notable changes to elfmem are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
elfmem uses [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

### Added
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
