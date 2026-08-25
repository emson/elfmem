"""SQLAlchemy Core table definitions — schema source of truth for elfmem."""

from __future__ import annotations

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    Table,
    Text,
    UniqueConstraint,
    func,
)

metadata = MetaData()

blocks = Table(
    "blocks",
    metadata,
    Column("id", Text, primary_key=True),
    Column("content", Text, nullable=False),
    Column("category", Text, nullable=False),
    Column("source", Text, nullable=False),
    Column("created_at", Text, nullable=False),
    Column("status", Text, nullable=False, default="inbox"),
    Column("archive_reason", Text),
    # Pin (v2 Phase 0, schema v7) — a pinned block is never proposed for
    # removal by an automatic mechanism. Previously `pinned:` existed only
    # in the markdown frontmatter with no DB column and no reader anywhere,
    # so Invariant 5 was declared but unimplemented. Backfilled from the
    # self/constitutional tag, which is what the pin guard checked before.
    Column("pinned", Integer, nullable=False, default=0, server_default="0"),
    # Supersession audit trail (v2 step 1) — id of the block that replaced
    # this one when archive_reason='superseded'. NULL for decay/forgotten
    # archivals and for any block that isn't archived.
    Column("superseded_by", Text),
    Column("confidence", Float, nullable=False, default=0.50),
    Column("reinforcement_count", Integer, nullable=False, default=0),
    Column("decay_lambda", Float, nullable=False, default=0.01),
    Column("last_reinforced_at", Float, nullable=False, default=0.0),
    Column("self_alignment", Float),
    Column("embedding", LargeBinary),
    Column("embedding_model", Text),
    Column("token_count", Integer),
    Column("summary", Text),
    Column("last_session_id", Text),
    Column("outcome_evidence", Float, nullable=False, default=0.0),
    # Bayesian sufficient statistics (v0.17) — α, β of the Beta posterior.
    # confidence and outcome_evidence are denormalised views maintained on every write:
    #   confidence       = α / (α + β)
    #   outcome_evidence = (α + β) - 1.0     (post-prior event count)
    # Defaults α=β=0.5 encode the Jeffreys prior (uniform-on-log-odds), so a
    # brand-new block with no outcomes reads confidence=0.5 and evidence≈0.
    Column("success_count", Float, nullable=False, default=0.5, server_default="0.5"),
    Column("failure_count", Float, nullable=False, default=0.5, server_default="0.5"),
    # Peer communication (v0.9.0)
    Column("source_peer", Text),           # DID of originating peer (None = local)
    Column("share", Text, default="private"),  # private | public | peer
    Column("envelope_json", Text),         # JSON envelope for message blocks
    # Deep-sleep rescoring (v0.13.3)
    Column("last_scored_at", Text),        # ISO ts of last LLM pass; NULL = unscored
    # Block format v2 (schema v8) — both DECLARED, written by whoever authored
    # the block, never computed. `cue` states when a future agent should recall
    # this block: the highest-leverage thing a writer can add, because it is a
    # lexical index of retrieval *situations*, which is precisely what
    # vocabulary-mismatch queries fail on. `volatility_class` records how fast
    # the claim stops being true (distinct from decay_lambda, which is how fast
    # it stops being used).
    Column("cue", Text),
    Column("volatility_class", Text),
)

block_tags = Table(
    "block_tags",
    metadata,
    Column("block_id", Text, ForeignKey("blocks.id", ondelete="CASCADE"), nullable=False),
    Column("tag", Text, nullable=False),
    UniqueConstraint("block_id", "tag", name="uq_block_tag"),
)

edges = Table(
    "edges",
    metadata,
    Column("from_id", Text, ForeignKey("blocks.id", ondelete="CASCADE"), nullable=False),
    Column("to_id", Text, ForeignKey("blocks.id", ondelete="CASCADE"), nullable=False),
    Column("weight", Float, nullable=False),
    Column("reinforcement_count", Integer, nullable=False, default=0),
    Column("created_at", Text, nullable=False),
    Column("relation_type", Text, nullable=False, server_default="similar"),
    Column("origin", Text, nullable=False, server_default="similarity"),
    Column("last_active_hours", Float),          # None until first reinforcement
    Column("note", Text),                        # optional agent/LLM description
    # Which block declared this edge (schema v8). Endpoints are canonicalised
    # to (min, max), so this is the only place a typed link's arrow survives a
    # round trip through the index and back out to the file substrate.
    Column("declared_by", Text),
    UniqueConstraint("from_id", "to_id", name="uq_edge"),
)

contradictions = Table(
    "contradictions",
    metadata,
    Column("block_a_id", Text, ForeignKey("blocks.id", ondelete="CASCADE"), nullable=False),
    Column("block_b_id", Text, ForeignKey("blocks.id", ondelete="CASCADE"), nullable=False),
    Column("score", Float, nullable=False),
    Column("resolved", Integer, nullable=False, default=0),
    Column("created_at", Text, nullable=False),
    # What kind of pair this is (schema v9). 'contradiction' = two claims that
    # cannot both be true. 'near_duplicate' = two blocks whose content matched
    # closely enough that one used to be silently destroyed; both are now kept
    # and the pair is recorded here instead.
    Column("kind", Text, nullable=False, server_default="contradiction"),
    # Lexical overlap of the two blocks' cue lines, when both have one.
    # Recorded, never acted on: it is the evidence a future auto-merge rule
    # would need, gathered before any rule is written rather than after.
    Column("cue_similarity", Float),
    UniqueConstraint("block_a_id", "block_b_id", name="uq_contradiction"),
)

frames = Table(
    "frames",
    metadata,
    Column("name", Text, primary_key=True),
    Column("weights_json", Text, nullable=False),
    Column("filters_json", Text, nullable=False),
    Column("guarantees_json", Text, nullable=False, default="[]"),
    Column("template", Text, nullable=False),
    Column("token_budget", Integer, nullable=False),
    Column("cache_json", Text),
    Column("source", Text, nullable=False, default="user"),
    Column("created_at", Text, nullable=False),
)

sessions = Table(
    "sessions",
    metadata,
    Column("id", Text, primary_key=True),
    Column("task_type", Text, nullable=False, default="general"),
    Column("started_at", Text, nullable=False),
    Column("ended_at", Text),
    Column("start_active_hours", Float, nullable=False),
)

system_config = Table(
    "system_config",
    metadata,
    Column("key", Text, primary_key=True),
    Column("value", Text, nullable=False),
)

block_outcomes = Table(
    "block_outcomes",
    metadata,
    Column("id", Text, primary_key=True),
    Column("block_id", Text, ForeignKey("blocks.id", ondelete="CASCADE"), nullable=False),
    Column("signal", Float, nullable=False),
    Column("weight", Float, nullable=False),
    Column("source", Text, nullable=False, default=""),
    Column("confidence_before", Float, nullable=False),
    Column("confidence_after", Float, nullable=False),
    Column("created_at", Text, nullable=False),
)

co_retrieval_staging = Table(
    "co_retrieval_staging",
    metadata,
    # Canonical order: from_id < to_id — enforced at the application layer.
    Column("from_id", Text, ForeignKey("blocks.id", ondelete="CASCADE"), nullable=False),
    Column("to_id", Text, ForeignKey("blocks.id", ondelete="CASCADE"), nullable=False),
    Column("count", Integer, nullable=False, default=1),
    UniqueConstraint("from_id", "to_id", name="uq_co_retrieval_staging"),
)

peer_roster = Table(
    "peer_roster",
    metadata,
    Column("did", Text, primary_key=True),
    Column("name", Text, nullable=False),
    Column("public_key", Text),                                  # v2 (signing)
    Column("trust", Float, nullable=False, default=0.0),
    Column("is_self", Integer, nullable=False, default=0),       # 1 = same identity
    Column("first_contact", Text, nullable=False),
    Column("last_active", Text, nullable=False),
    Column("blocks_imported", Integer, nullable=False, default=0),
    Column("blocks_exported", Integer, nullable=False, default=0),
    Column("messages_in", Integer, nullable=False, default=0),
    Column("messages_out", Integer, nullable=False, default=0),
    Column("notes", Text),
    Column("delivery_path", Text),  # filesystem path to peer's inbox dir
)

block_amendments = Table(
    "block_amendments",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column(
        "block_id", Text,
        ForeignKey("blocks.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column(
        "timestamp", DateTime,
        nullable=False, server_default=func.current_timestamp(),
    ),
    Column("pre_content", Text, nullable=False),
    Column("post_content", Text, nullable=False),
    Column("pre_summary", Text),
    Column("post_summary", Text),
    Column("drift_score", Float, nullable=False),
    Column("rationale", Text),
    Column(
        "acceptor", Text, nullable=False,
    ),
    Column("reverted_at", DateTime),
    CheckConstraint(
        "acceptor IN ('agent', 'user', 'system')",
        name="ck_block_amendments_acceptor",
    ),
)

Index("idx_blocks_status", blocks.c.status)
Index("idx_block_amendments_block_id", block_amendments.c.block_id)
Index("idx_block_amendments_timestamp", block_amendments.c.timestamp)
Index("idx_blocks_last_reinforced", blocks.c.last_reinforced_at)
Index("idx_block_tags_tag", block_tags.c.tag)
Index("idx_block_tags_block_id", block_tags.c.block_id)
Index("idx_edges_from", edges.c.from_id)
Index("idx_edges_to", edges.c.to_id)
Index("idx_block_outcomes_block_id", block_outcomes.c.block_id)
Index("idx_blocks_source_peer", blocks.c.source_peer)
Index(
    "idx_contradictions_unresolved",
    contradictions.c.block_a_id,
    contradictions.c.block_b_id,
    sqlite_where=(contradictions.c.resolved == 0),
)
