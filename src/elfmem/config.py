"""ElfmemConfig — configuration models for the elfmem memory system."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator


class LLMConfig(BaseModel):
    """Configuration for the LLM service.

    API keys are NOT stored here — they come from environment variables:
      Anthropic  → ANTHROPIC_API_KEY (default; override via api_key_env)
      OpenAI     → OPENAI_API_KEY (default; override via api_key_env)
      Together / Groq / OpenRouter / any OpenAI-compatible provider →
          set api_key_env to that provider's own env var name, e.g.
          "TOGETHER_API_KEY". Without it, the OpenAI-compatible adapter
          reads OPENAI_API_KEY regardless of base_url — which only works
          if you name your provider's key OPENAI_API_KEY, which is the
          RC4 "OpenRouter is broken by construction" gap (v2 step 5).
    """

    model: str = "claude-haiku-4-5-20251001"
    temperature: float = 0.0
    max_tokens: int = 512
    timeout: int = 30
    max_retries: int = 3
    base_url: str | None = None
    # Explicit env var to read the API key from (v2 step 5). None = today's
    # default behaviour, unchanged: ANTHROPIC_API_KEY for claude-* models,
    # OPENAI_API_KEY for every OpenAI-compatible adapter regardless of
    # base_url. Set this to use Together.ai, Groq, OpenRouter, or any other
    # provider whose key isn't literally named OPENAI_API_KEY.
    api_key_env: str | None = None

    # Per-call model override — None = use model above
    process_block_model: str | None = None


class EmbeddingConfig(BaseModel):
    """Configuration for the embedding service.

    WARNING: dimensions must match what is stored in the database.
    Changing the embedding model requires re-embedding all blocks.
    """

    model: str = "text-embedding-3-small"
    dimensions: int = 1536
    timeout: int = 30
    base_url: str | None = None
    # Same purpose as LLMConfig.api_key_env (v2 step 5) — None reads
    # OPENAI_API_KEY, unchanged default behaviour.
    api_key_env: str | None = None


class MemoryConfig(BaseModel):
    """Configuration for memory system thresholds and tunables."""

    # Lifecycle thresholds
    inbox_threshold: int = 10
    curate_interval_hours: float = 40.0
    search_window_hours: float = 200.0
    vector_n_seeds_multiplier: int = 4

    max_inbox_per_run: int | None = 5
    # Bounds how many inbox blocks one consolidate()/dream() call processes.
    # None = unbounded (pre-ADR-0007 behaviour). Default 5 is a stopgap until
    # per-block commit durability lands (ADR 0007 Change 2): at 5 blocks ×
    # 1 process_block call each, one dream() call is bounded well within the
    # ~14s/call local-adapter latency that motivated this cap (originally
    # also bounding contradiction_top_k contradiction calls, retired in v2
    # step 7b — ADR 0010). Half of inbox_threshold, so a normal
    # auto-triggered batch still drains in one call. Override with --max /
    # this field once ADR 0007 Change 2 ships and a kill is no longer
    # catastrophic.

    # Quality thresholds
    self_alignment_threshold: float = 0.70
    near_dup_exact_threshold: float = 0.95
    near_dup_near_threshold: float = 0.90

    # Graph
    edge_score_threshold: float = 0.45
    edge_degree_cap: int = 5
    edge_prune_threshold: float = 0.10
    edge_reinforce_delta: float = 0.10

    # Scoring
    top_k: int = 5

    # Curate
    curate_reinforce_top_n: int = 5

    # Outcome scoring
    outcome_prior_strength: float = 2.0
    # DEPRECATED (v0.17, removal v0.18+): no longer read by ``record_outcome``.
    # Sufficient statistics (α, β) are now stored directly on each block, so
    # the alignment "prior strength" is encoded once at promotion (α + β = 1.0)
    # rather than reconstituted on every outcome. Retained for one release so
    # existing YAML configs keep loading without errors.

    outcome_reinforce_threshold: float = 0.5
    # Minimum signal to trigger block reinforcement and Hebbian edge learning.
    # Blocks below this threshold receive no reinforcement (decay naturally).

    penalize_threshold: float = 0.20
    # Signal below this triggers decay acceleration (decay_lambda *= penalty_factor).
    # Dead-band: signals in [penalize_threshold, reinforce_threshold] adjust confidence only.

    penalty_factor: float = 2.0
    # Multiplier applied to decay_lambda on penalization. Capped at lambda_ceiling.

    lambda_ceiling: float = 0.050
    # Maximum decay_lambda after penalization. Equals EPHEMERAL tier (0.050).

    # Hebbian co-retrieval edge creation
    co_retrieval_edge_threshold: int = 3
    # Minimum co-retrievals for a pair to be promoted to a co_occurs edge.
    # "Once is coincidence, twice is pattern, three times is signal."

    co_retrieval_edge_weight: float = 0.55
    # Weight for Hebbian-promoted co_retrieval edges.
    # Above similarity floor (0.40), below outcome-confirmed (0.80).

    # Deep-sleep rescore — additive Bayesian update (v0.17)
    rescore_evidence_weight: float = Field(default=0.5, ge=0.0)
    # Weight of the rescore alignment as a Beta-Binomial evidence event.
    # 0.0 disables the confidence update (alignment refresh only).
    # 0.5 is the calibrated default: blocks with mature evidence (α+β ≫ 1)
    # barely move on rescore; cold blocks track the new alignment.
    # See ADR 0002 and docs/plans/plan_memory_scoring.md for the empirical
    # validation (22× reduction in rescore damage at α=15, β=2).

    co_retrieval_staging_max: int = 1000
    # Maximum staging dict entries. Evicts lowest-count pairs when exceeded.
    # Defensive cap — Phase 1 usage (50–500 blocks, top_k≤20) stays well below.


class PromptsConfig(BaseModel):
    """Configuration for LLM prompt templates.

    Three levels of override:
    1. Inline string (process_block)
    2. File path (process_block_file) — resolved relative to cwd
    3. Custom LLMService implementation passed directly to MemorySystem.__init__()

    Inline takes priority over file. If neither is set, library defaults
    from elfmem.prompts are used.
    """

    # Level 1: Inline overrides — None = use library default
    process_block: str | None = None

    # Level 2: File path overrides — None = not used
    process_block_file: str | None = None

    # Tag vocabulary override — None = use VALID_SELF_TAGS from prompts.py
    valid_self_tags: list[str] | None = None

    def resolve_process_block(self) -> str:
        """Resolve the block analysis prompt: inline > file > default."""
        return self._resolve(self.process_block, self.process_block_file, "process_block")

    def resolve_valid_tags(self) -> frozenset[str]:
        """Resolve the valid tag vocabulary."""
        if self.valid_self_tags is not None:
            return frozenset(self.valid_self_tags)
        from elfmem.prompts import VALID_SELF_TAGS
        return VALID_SELF_TAGS

    def validate_templates(self) -> None:
        """Raise ValueError if any resolved prompt is missing required variables."""
        _check_vars(self.resolve_process_block(), ["self_context", "block"], "process_block")

    @staticmethod
    def _resolve(inline: str | None, filepath: str | None, prompt_name: str) -> str:
        if inline is not None:
            return inline
        if filepath is not None:
            return Path(filepath).read_text(encoding="utf-8")
        from elfmem import prompts
        defaults = {
            "process_block": prompts.BLOCK_ANALYSIS_PROMPT,
        }
        return defaults[prompt_name]


def _check_vars(template: str, required: list[str], name: str) -> None:
    missing = [v for v in required if f"{{{v}}}" not in template]
    if missing:
        raise ValueError(
            f"Prompt '{name}' is missing required variables: {missing}"
        )


class LoggingConfig(BaseModel):
    """Configuration for structured logging.

    Logging is disabled by default (CRITICAL level). Enable via:
    - Environment: ELFMEM_LOG_LEVEL=INFO
    - Config file: logging.level: INFO
    - Code: configure_logging(level="DEBUG")
    """

    level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "CRITICAL"
    format: Literal["text", "json", "compact"] = "text"
    file: str | None = None  # Log file path; None = stderr
    modules: dict[str, str] | None = None  # Per-module overrides

    # Advanced tuning
    slow_query_threshold_ms: int = 100
    lock_contention_threshold_ms: int = 50
    sample_rate: float = 1.0  # For high-volume systems (1.0 = all, 0.1 = 10%)


class ProjectConfig(BaseModel):
    """Project metadata written by ``elfmem init``, read by ``elfmem doctor``.

    Stored in the ``project:`` section of ``.elfmem/config.yaml``.
    Contains no secrets — safe to commit to the repository.
    The ``db`` field lets all CLI commands auto-discover the database path
    without requiring ``--db`` on every invocation.
    """

    name: str = ""
    db: str = ""        # path to the database file (may contain ~)
    identity: str = ""  # identity description for display and seeding
    # Invocation token, two jobs: (1) when the user addresses the host by
    # this name, AGENT.md's protocol section tells it to recall SELF; (2)
    # since v0.20, interpolated into the SELF preamble itself ("You are
    # {agent_name}") — previously hardcoded to "elf" regardless of this
    # field, a gap this project's own field-testers hit and reported
    # (docs/self_preamble_naming_report.md) after naming their agent "Theo"
    # via `elfmem init --name` and getting "answer as elf" back anyway.
    agent_name: str = ""
    created: str = ""   # ISO date of initialisation


class RescoreConfig(BaseModel):
    """Configuration for deep-sleep rescoring (v0.13.3).

    Rescoring re-evaluates active blocks against the current SELF using the
    LLM, keeping alignment / summary / tags fresh as the agent's identity
    drifts. Driven by ``dream --rescore``; surfaced as a drift health check
    in ``elfmem doctor``.

    The defaults are calibrated for typical agent memory sizes:
    - ``max_per_run=20`` keeps each ``--rescore`` invocation under ~60s on
      common providers; tune up for larger DBs.
    - ``min_age_hours=24`` prevents recently-scored blocks from churning.
    - ``target_max_age_days=90`` is the staleness horizon: blocks scored
      longer ago than this count toward drift.
    - ``drift_warning_count=25`` is the absolute drift floor for the doctor
      warning (protects small-DB users from invisible drift).
    - ``drift_warning_percent=25`` is the proportional drift cap (protects
      large-DB users from doctor noise). Either being exceeded triggers
      the warning.
    """

    enabled: bool = True
    max_per_run: int = 20
    min_age_hours: int = 24
    target_max_age_days: int = 90
    drift_warning_count: int = 25
    drift_warning_percent: int = 25
    exclude_categories: list[str] = Field(
        default_factory=lambda: ["message", "mind", "decision", "prediction"]
    )
    exclude_tags: list[str] = Field(default_factory=lambda: ["system/no-rescore"])


class CorpusReviewConfig(BaseModel):
    """Configuration for `elfmem review corpus` — deterministic staleness
    detection (v2 step 6a).

    Independent of the decay-tier system (`decay_lambda`, still live in
    retrieval scoring and edge pruning): no lambda, no interaction with
    curate(). This is the replacement for curate()'s decay-driven block
    archival, which step 7a retired as evidenced-inert (see ADR 0009). A
    block is a staleness candidate only when three weak, cheap-to-compute
    signals all agree — long-unused, rarely reinforced, and never confirmed
    by an outcome — rather than any single strong signal. Pure SQL/math; no
    LLM call, unlike the duplicate/contradiction detection step 6b adds later.
    """

    stale_min_hours_since_reinforced: float = Field(default=720.0, gt=0.0)
    # ~30 days. A block untouched for less than this is not a candidate
    # regardless of its other signals.

    stale_max_reinforcement_count: int = Field(default=2, ge=0)
    # A block reinforced more than this many times has earned enough
    # standing that staleness needs stronger evidence than "unused a while".

    max_proposals: int = Field(default=20, ge=1)
    # Per-cycle ceiling, same rationale as ReviewConfig.max_proposals below.


class ReviewConfig(BaseModel):
    """Configuration for constitutional review (v0.18).

    Constitutional review surfaces drift between the agent's tagged
    ``self/constitutional`` blocks and the empirical operational identity
    expressed by recently-reinforced ordinary blocks. The cycle is MANUAL
    — review proposes amendments; nothing is applied without explicit
    ``accept_amendment``. See ADR 0003.

    Defaults are tuned for typical agent memory at 6+ months operation:
    - ``drift_threshold=0.35`` — half-rescaled cosine; >0.35 ≈ ≤30° apart.
    - ``min_recent_reinforced_blocks=20`` — below this, ``review_constitutional``
      returns ``insufficient_history=True`` with no LLM calls.
    - ``window_hours=720`` (~30 days) — recent activity window for the
      operational centroid.
    - ``cooldown_hours=2160`` (~90 days) — a block recently amended is
      excluded from the next review cycle so we don't churn principles.
    - ``max_proposals=5`` — per cycle ceiling; agents tend to over-edit
      themselves without this brake.
    - ``min_block_evidence=2.0`` — Beta posterior α+β floor; below this
      the block has not earned enough lifetime evidence to be amended.
    - ``min_age_days=30`` — a block must be at least this old before any
      amendment can be proposed. Protects fresh constitutional seeding.
    """

    drift_threshold: float = Field(default=0.35, ge=0.0, le=1.0)
    min_recent_reinforced_blocks: int = Field(default=20, ge=1)
    window_hours: float = Field(default=30.0 * 24.0, gt=0.0)
    min_reinforcement: int = Field(default=2, ge=0)
    top_n: int = Field(default=50, ge=1)
    cooldown_hours: float = Field(default=90.0 * 24.0, ge=0.0)
    max_proposals: int = Field(default=5, ge=1)
    min_block_evidence: float = Field(default=2.0, ge=0.0)
    min_age_days: float = Field(default=30.0, ge=0.0)
    corpus: CorpusReviewConfig = Field(default_factory=lambda: CorpusReviewConfig())


def _slug(text: str) -> str:
    """Filesystem-safe slug. Lowercase, non-alphanumerics → dashes, trimmed.

    Mirrors ``elfmem.operations.peer._slugify`` — kept here to avoid an import
    cycle (config.py is leaf-level). The two must stay in lock-step; covered
    by ``tests/test_peer_config_sync.py::test_slug_matches_operations``.
    """
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _derive_did(name: str) -> str:
    """Derive a canonical ``elf:`` DID from a display name."""
    return f"elf:{_slug(name)}"


_DID_RE = re.compile(r"^[a-z][a-z0-9]*:[a-z0-9][a-z0-9-]*$")


class PeerSpec(BaseModel):
    """Declarative peer registration (read from ``config.yaml peers:``).

    Loaded into the SQLite ``peer_roster`` at engine startup via
    ``sync_peers_from_config`` — insert-only, so operational state (trust
    adjustments, message counters) on existing peers is preserved.

    Required: ``name``. Everything else is derived or optional:
    - ``did`` defaults to ``f"elf:{slug(name)}"`` (canonical identifier).
    - ``delivery_path`` defaults to ``<project_root>/.elfmem/inbox`` when
      ``project_root`` is set; otherwise None (sender writes to its own
      outbox and transport happens externally).
    - Legacy ``identity:`` (prose) is accepted and remapped to
      ``description`` with a one-release deprecation.
    """

    model_config = {"populate_by_name": True, "extra": "allow"}

    name: str
    did: str | None = None
    description: str | None = None
    project_root: str | None = None
    db_path: str | None = None
    delivery_path: str | None = None
    trust: float = 1.0

    @model_validator(mode="before")
    @classmethod
    def _accept_legacy_identity(cls, data: object) -> object:
        # v0.18 and earlier configs used ``identity:`` for what is now
        # ``description:``. Remap silently when no description is set.
        if not isinstance(data, dict):
            return data
        if "identity" in data and "description" not in data:
            data = {**data, "description": data.pop("identity")}
        return data

    @model_validator(mode="after")
    def _fill_derived(self) -> PeerSpec:
        # DID: provided value must be canonical; else derive from name.
        if self.did is None:
            object.__setattr__(self, "did", _derive_did(self.name))
        elif not _DID_RE.match(self.did):
            raise ValueError(
                f"Peer '{self.name}' has malformed did='{self.did}'. "
                f"Expected '<scheme>:<name>' (lowercase, [a-z0-9-]). "
                f"Suggested: '{_derive_did(self.name)}'."
            )
        # delivery_path: derive from project_root when not explicit.
        if self.delivery_path is None and self.project_root:
            self.delivery_path = str(
                Path(self.project_root).expanduser() / ".elfmem" / "inbox"
            )
        return self


class PeerConfig(BaseModel):
    """Configuration for peer communication.

    The inbox and outbox directories are project-local by default — derived
    from the project root (``<project>/.elfmem/inbox`` / ``outbox``) at runtime
    by ``MemorySystem``. Explicit values here override that derivation; this is
    primarily an escape hatch for tests and unusual deployments. Most users
    should leave them unset and let ``elfmem setup`` create the project.
    """

    identity: str | None = None
    outbox_dir: str | None = None
    inbox_dir: str | None = None
    confidence_floor: float = 0.3
    # DEPRECATED (v0.17, removal v0.18+): peer imports now use the trust-scaled
    # arithmetic merge in ``merge_peer_evidence``; there is no per-import floor
    # to clamp against. Retained for one release for config-file compatibility.
    auto_ingest_trust_threshold: float = 0.7
    trust_decay_days: int = 90
    trust_decay_factor: float = 0.95


class SubstrateConfig(BaseModel):
    """Which layer is the source of truth for memory content.

    Default off. With it off the database is authoritative and the file
    substrate is produced by migration tooling only, which is where every
    instance sits until someone deliberately cuts over. With it on, writes
    land in ``.elfmem/memory/**.md`` first and the database becomes a derived
    index that `elfmem index rebuild` can reproduce from files plus ledger.

    Flipping this is the irreversible half of the v2 migration and should
    follow a passing `elfmem index parity`, not precede it.
    """

    files_authoritative: bool = False


class ElfmemConfig(BaseModel):
    """Top-level configuration for the elfmem memory system.

    Supports construction from:
    - Python code: ElfmemConfig(llm=LLMConfig(...), ...)
    - YAML file: ElfmemConfig.from_yaml("config.yaml")
    - Dict: ElfmemConfig.model_validate({...})
    - Environment: ElfmemConfig.from_env()
    - Defaults: ElfmemConfig()
    """

    project: ProjectConfig | None = None
    llm: LLMConfig = Field(default_factory=LLMConfig)
    embeddings: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    substrate: SubstrateConfig = Field(default_factory=SubstrateConfig)
    prompts: PromptsConfig = Field(default_factory=PromptsConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    peer: PeerConfig = Field(default_factory=PeerConfig)
    peers: list[PeerSpec] = Field(default_factory=list)
    rescore: RescoreConfig = Field(default_factory=RescoreConfig)
    review: ReviewConfig = Field(default_factory=ReviewConfig)

    @model_validator(mode="after")
    def _validate_peers(self) -> ElfmemConfig:
        # Duplicate DIDs in config are a load-time error — fail fast with the
        # offending value so the operator can resolve in one edit.
        seen_dids: dict[str, str] = {}
        seen_roots: dict[str, str] = {}
        self_did = self.peer.identity
        for peer in self.peers:
            did = peer.did or ""
            if did in seen_dids:
                raise ValueError(
                    f"Duplicate peer did='{did}' "
                    f"(names: '{seen_dids[did]}', '{peer.name}'). "
                    f"Remove one entry from peers: in .elfmem/config.yaml."
                )
            seen_dids[did] = peer.name
            if self_did and did == self_did:
                raise ValueError(
                    f"Peer '{peer.name}' has did='{did}' equal to "
                    f"peer.identity. A peer cannot be self-referential — "
                    f"change peer.identity or remove this peer entry."
                )
            if peer.project_root:
                resolved = str(Path(peer.project_root).expanduser().resolve())
                if resolved in seen_roots:
                    raise ValueError(
                        f"Peers '{seen_roots[resolved]}' and '{peer.name}' "
                        f"share project_root='{resolved}'. project_root must "
                        f"be unique across peers."
                    )
                seen_roots[resolved] = peer.name
        return self

    @classmethod
    def from_yaml(cls, path: str) -> ElfmemConfig:
        """Load config from a YAML file. All sections are optional.

        Raises:
            ValueError: If any custom prompt template is missing required variables.
            FileNotFoundError: If a prompt file path in the config does not exist.
        """
        with open(Path(path).expanduser()) as f:
            data = yaml.safe_load(f)
        # Remove None values so empty sections (e.g., "prompts:" with only comments)
        # trigger default_factory instead of validation error.
        data = {k: v for k, v in (data or {}).items() if v is not None}
        cfg = cls.model_validate(data)
        # Validate prompt templates early — fail fast before any LLM calls.
        cfg.prompts.validate_templates()
        return cfg

    @classmethod
    def from_env(cls) -> ElfmemConfig:
        """Load config from ELFMEM_ prefixed environment variables."""
        data: dict[str, dict[str, object]] = {"llm": {}, "embeddings": {}}
        if model := os.getenv("ELFMEM_LLM_MODEL"):
            data["llm"]["model"] = model
        if base_url := os.getenv("ELFMEM_LLM_BASE_URL"):
            data["llm"]["base_url"] = base_url
        if emb_model := os.getenv("ELFMEM_EMBEDDING_MODEL"):
            data["embeddings"]["model"] = emb_model
        if emb_dim := os.getenv("ELFMEM_EMBEDDING_DIMENSIONS"):
            data["embeddings"]["dimensions"] = int(emb_dim)
        if emb_url := os.getenv("ELFMEM_EMBEDDING_BASE_URL"):
            data["embeddings"]["base_url"] = emb_url
        return cls.model_validate(data)


def render_default_config(project: ProjectConfig | None = None) -> str:
    """Render a commented config.yaml string from ElfmemConfig() defaults.

    When *project* is provided, a ``project:`` section is prepended with
    project metadata (name, db path, identity). This section is what lets
    all elfmem CLI commands auto-discover the right database without ``--db``.

    Values are sourced from ElfmemConfig() so the output always reflects
    current code defaults. Used by ``elfmem init`` to generate config files.
    """
    import textwrap
    d = ElfmemConfig()

    project_section = ""
    if project is not None:
        project_section = textwrap.dedent(f"""\
            # Project metadata — auto-discovered by elfmem CLI commands.
            # The db path lets "elfmem serve" (and all other commands) find the
            # database without requiring --db on every invocation.
            project:
              name: "{project.name}"
              db: "{project.db}"
              identity: "{project.identity}"
              agent_name: "{project.agent_name}"
              created: "{project.created}"

            """)

    settings = textwrap.dedent(f"""\
        # elfmem configuration
        # Generated by: elfmem init
        # Edit as needed. All sections are optional — missing keys use code defaults.
        # API keys are NOT stored here. Set them as environment variables (a
        # project-root .env file is auto-loaded — see elfmem doctor --resolve):
        #   Anthropic (claude-* models): ANTHROPIC_API_KEY
        #   OpenAI-compatible (everything else): OPENAI_API_KEY by default —
        #     set llm.api_key_env below to use a differently-named key
        #     (Together.ai, Groq, OpenRouter, etc.)

        llm:
          model: "{d.llm.model}"
          temperature: {d.llm.temperature}
          max_tokens: {d.llm.max_tokens}
          timeout: {d.llm.timeout}
          max_retries: {d.llm.max_retries}
          # base_url: "https://api.together.xyz/v1"
          # api_key_env: "TOGETHER_API_KEY"   # only needed if not OPENAI_API_KEY/ANTHROPIC_API_KEY

        embeddings:
          model: "{d.embeddings.model}"
          dimensions: {d.embeddings.dimensions}
          timeout: {d.embeddings.timeout}
          # base_url: "https://api.together.xyz/v1"
          # api_key_env: "TOGETHER_API_KEY"

        memory:
          inbox_threshold: {d.memory.inbox_threshold}
          curate_interval_hours: {d.memory.curate_interval_hours}
          self_alignment_threshold: {d.memory.self_alignment_threshold}
          near_dup_exact_threshold: {d.memory.near_dup_exact_threshold}
          near_dup_near_threshold: {d.memory.near_dup_near_threshold}
          edge_score_threshold: {d.memory.edge_score_threshold}
          edge_degree_cap: {d.memory.edge_degree_cap}
          top_k: {d.memory.top_k}
          search_window_hours: {d.memory.search_window_hours}
          outcome_prior_strength: {d.memory.outcome_prior_strength}
          outcome_reinforce_threshold: {d.memory.outcome_reinforce_threshold}
          penalize_threshold: {d.memory.penalize_threshold}

        # Logging configuration (optional — uncomment to enable)
        # logging:
        #   level: INFO
        #   format: json
        #   file: ~/.elfmem/elfmem.log
        #   modules:
        #     elfmem.operations.consolidate: DEBUG

        # Custom prompts (optional — uncomment to override library defaults):
        # prompts:
        #   process_block_file: "~/.elfmem/prompts/process_block.txt"
        """)

    return project_section + settings
