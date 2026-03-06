"""SQLAlchemy Core table definitions — schema source of truth for elfmem."""

from __future__ import annotations

from sqlalchemy import (
    Column,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    Table,
    Text,
    UniqueConstraint,
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

Index("idx_blocks_status", blocks.c.status)
Index("idx_blocks_last_reinforced", blocks.c.last_reinforced_at)
Index("idx_block_tags_tag", block_tags.c.tag)
Index("idx_block_tags_block_id", block_tags.c.block_id)
Index("idx_edges_from", edges.c.from_id)
Index("idx_edges_to", edges.c.to_id)
Index("idx_block_outcomes_block_id", block_outcomes.c.block_id)
Index(
    "idx_contradictions_unresolved",
    contradictions.c.block_a_id,
    contradictions.c.block_b_id,
    sqlite_where=(contradictions.c.resolved == 0),
)
