# Changelog

All notable changes to elfmem are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
elfmem uses [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

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
