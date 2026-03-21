"""Tests for DashboardData — synchronous SQLite extraction layer.

Uses a temp-file SQLite database (not in-memory) because DashboardData.from_db()
uses plain sqlite3. Schema is created via SQLAlchemy sync engine to avoid
duplicating DDL.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine as sync_create_engine

from elfmem.db.models import metadata
from elfmem.viz.data import DashboardData, _tier_from_lambda

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def db_path(tmp_path: pytest.fixture) -> str:  # type: ignore[assignment]
    """Temp-file SQLite database with the elfmem schema pre-created."""
    path = str(tmp_path / "test_viz.db")
    engine = sync_create_engine(f"sqlite:///{path}")
    with engine.begin() as conn:
        metadata.create_all(conn)
    engine.dispose()
    # Seed system_config with defaults
    with sqlite3.connect(path) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO system_config (key, value) VALUES ('total_active_hours', '0.0')"
        )
        conn.execute(
            "INSERT OR IGNORE INTO system_config (key, value) VALUES ('lifetime_token_usage', '{}')"
        )
        weights = (
            '{"similarity": 0.35, "confidence": 0.15,'
            ' "recency": 0.25, "centrality": 0.15, "reinforcement": 0.10}'
        )
        conn.execute(
            "INSERT OR IGNORE INTO frames"
            " (name, weights_json, filters_json, guarantees_json,"
            "  template, token_budget, source, created_at)"
            " VALUES (?, ?, '[]', '[]', '', 1000, 'builtin', ?)",
            ("attention", weights, datetime.now(UTC).isoformat()),
        )
        conn.commit()
    return path


def _insert_block(
    path: str,
    *,
    status: str = "active",
    decay_lambda: float = 0.010,
    last_reinforced_at: float = 0.0,
    reinforcement_count: int = 0,
    confidence: float = 0.5,
    content: str | None = None,
    category: str = "general",
) -> str:
    block_id = uuid.uuid4().hex[:16]
    content = content or f"block content {block_id}"
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            INSERT INTO blocks
              (id, content, category, source, created_at, status,
               confidence, reinforcement_count, decay_lambda,
               last_reinforced_at, outcome_evidence)
            VALUES (?, ?, ?, 'api', ?, ?, ?, ?, ?, ?, 0.0)
            """,
            (
                block_id,
                content,
                category,
                datetime.now(UTC).isoformat(),
                status,
                confidence,
                reinforcement_count,
                decay_lambda,
                last_reinforced_at,
            ),
        )
        conn.commit()
    return block_id


def _insert_edge(path: str, from_id: str, to_id: str, weight: float = 0.5) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO edges
              (from_id, to_id, weight, reinforcement_count, created_at, relation_type, origin)
            VALUES (?, ?, ?, 0, ?, 'similar', 'similarity')
            """,
            (from_id, to_id, weight, datetime.now(UTC).isoformat()),
        )
        conn.commit()


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestHealthData:
    def test_health_counts_match_db(self, db_path: str) -> None:
        for _ in range(3):
            _insert_block(db_path, status="inbox")
        for _ in range(5):
            _insert_block(db_path, status="active")
        for _ in range(2):
            _insert_block(db_path, status="archived")

        data = DashboardData.from_db(db_path)

        assert data.health.inbox_count == 3
        assert data.health.active_count == 5
        assert data.health.archived_count == 2

    def test_empty_db_all_zero_counts(self, db_path: str) -> None:
        data = DashboardData.from_db(db_path)

        assert data.health.inbox_count == 0
        assert data.health.active_count == 0
        assert data.health.archived_count == 0

    def test_health_is_good_when_inbox_empty(self, db_path: str) -> None:
        _insert_block(db_path, status="active")
        data = DashboardData.from_db(db_path)
        assert data.health.health == "good"

    def test_health_is_degraded_when_inbox_overflows(self, db_path: str) -> None:
        for _ in range(25):
            _insert_block(db_path, status="inbox")
        data = DashboardData.from_db(db_path)
        assert data.health.health == "degraded"

    def test_generated_at_is_iso(self, db_path: str) -> None:
        data = DashboardData.from_db(db_path)
        # Should parse without exception
        datetime.fromisoformat(data.health.generated_at)


class TestGraphData:
    def test_graph_nodes_are_active_only(self, db_path: str) -> None:
        for _ in range(5):
            _insert_block(db_path, status="active")
        for _ in range(2):
            _insert_block(db_path, status="archived")

        data = DashboardData.from_db(db_path)

        assert len(data.graph.nodes) == 5
        assert all(n["status"] == "active" for n in data.graph.nodes)

    def test_graph_includes_archived_when_flag_set(self, db_path: str) -> None:
        _insert_block(db_path, status="active")
        _insert_block(db_path, status="archived")

        data = DashboardData.from_db(db_path, include_archived=True)

        assert len(data.graph.nodes) == 2

    def test_graph_not_truncated_under_limit(self, db_path: str) -> None:
        for _ in range(5):
            _insert_block(db_path, status="active")

        data = DashboardData.from_db(db_path, max_nodes=100)

        assert data.graph.truncated is False
        assert data.graph.total_blocks == 5

    def test_graph_truncated_by_centrality(self, db_path: str) -> None:
        # Insert 15 blocks so we exceed max_nodes=10
        block_ids = [_insert_block(db_path, status="active") for _ in range(15)]
        # Give some blocks edges so they have higher centrality
        for i in range(5):
            _insert_edge(db_path, block_ids[i], block_ids[i + 1])

        data = DashboardData.from_db(db_path, max_nodes=10)

        assert data.graph.truncated is True
        assert len(data.graph.nodes) == 10
        assert data.graph.total_blocks == 15

    def test_graph_edges_reference_included_nodes_only(self, db_path: str) -> None:
        block_ids = [_insert_block(db_path, status="active") for _ in range(10)]
        for i in range(9):
            _insert_edge(db_path, block_ids[i], block_ids[i + 1])

        data = DashboardData.from_db(db_path, max_nodes=5)

        included = {n["id"] for n in data.graph.nodes}
        for edge in data.graph.edges:
            assert edge["from_id"] in included
            assert edge["to_id"] in included

    def test_no_nodes_for_empty_db(self, db_path: str) -> None:
        data = DashboardData.from_db(db_path)
        assert data.graph.nodes == []

    def test_node_decay_tier_derived_from_lambda(self, db_path: str) -> None:
        _insert_block(db_path, status="active", decay_lambda=0.00001)
        data = DashboardData.from_db(db_path)
        assert data.graph.nodes[0]["decay_tier"] == "permanent"

    def test_node_ephemeral_for_high_lambda(self, db_path: str) -> None:
        _insert_block(db_path, status="active", decay_lambda=0.050)
        data = DashboardData.from_db(db_path)
        assert data.graph.nodes[0]["decay_tier"] == "ephemeral"


class TestLifecycleData:
    def test_lifecycle_tier_counts_sum_to_active(self, db_path: str) -> None:
        _insert_block(db_path, status="active", decay_lambda=0.00001)
        _insert_block(db_path, status="active", decay_lambda=0.001)
        _insert_block(db_path, status="active", decay_lambda=0.010)
        _insert_block(db_path, status="active", decay_lambda=0.050)

        data = DashboardData.from_db(db_path)

        total = sum(data.lifecycle.tier_counts.values())
        assert total == data.lifecycle.active == 4


class TestDecayData:
    def test_decay_curves_have_expected_points_per_tier(self, db_path: str) -> None:
        data = DashboardData.from_db(db_path)
        for tier in ("permanent", "durable", "standard", "ephemeral"):
            assert len(data.decay.tier_curves[tier]) == 25

    def test_decay_prune_threshold_is_005(self, db_path: str) -> None:
        data = DashboardData.from_db(db_path)
        assert data.decay.prune_threshold == 0.05

    def test_decay_at_risk_count_correct(self, db_path: str) -> None:
        # Insert a block with very high hours_since so recency < 0.10
        # standard lambda=0.010; exp(-0.010 * 300) = exp(-3) ≈ 0.05 < 0.10
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO system_config (key, value)"
                " VALUES ('total_active_hours', '300.0')"
            )
            conn.execute(
                "UPDATE system_config SET value='300.0' WHERE key='total_active_hours'"
            )
            conn.commit()
        # Insert block reinforced at hour 0 (so hours_since = 300)
        _insert_block(db_path, status="active", decay_lambda=0.010, last_reinforced_at=0.0)
        # Insert a healthy block (reinforced at hour 299)
        _insert_block(db_path, status="active", decay_lambda=0.010, last_reinforced_at=299.0)

        data = DashboardData.from_db(db_path)

        assert data.decay.at_risk_count == 1


class TestScoringData:
    def test_scoring_frames_present(self, db_path: str) -> None:
        data = DashboardData.from_db(db_path)
        assert len(data.scoring.frames) >= 1

    def test_scoring_frame_has_weights(self, db_path: str) -> None:
        data = DashboardData.from_db(db_path)
        frame = data.scoring.frames[0]
        assert "similarity" in frame["weights"]

    def test_last_retrieval_is_empty_list(self, db_path: str) -> None:
        data = DashboardData.from_db(db_path)
        assert data.scoring.last_retrieval == []


class TestSerialization:
    def test_to_json_is_valid_json(self, db_path: str) -> None:
        _insert_block(db_path, status="active")
        data = DashboardData.from_db(db_path)
        parsed = json.loads(data.to_json())
        assert "health" in parsed
        assert "graph" in parsed

    def test_to_json_no_raw_script_tag(self, db_path: str) -> None:
        # to_json() escapes < and > as \u003c / \u003e for safe HTML embedding
        _insert_block(db_path, status="active", content="</script>alert('xss')<script>")
        data = DashboardData.from_db(db_path)
        json_str = data.to_json()
        assert "</script>" not in json_str
        assert r"\u003c" in json_str


class TestTierFromLambda:
    @pytest.mark.parametrize(
        "lam, expected",
        [
            (0.00001, "permanent"),
            (0.001,   "durable"),
            (0.010,   "standard"),
            (0.050,   "ephemeral"),
        ],
    )
    def test_exact_values(self, lam: float, expected: str) -> None:
        assert _tier_from_lambda(lam) == expected

    def test_approximate_value(self) -> None:
        # 0.009 is closest to 0.010 (standard)
        assert _tier_from_lambda(0.009) == "standard"


class TestFileNotFound:
    def test_raises_on_missing_db(self, tmp_path: pytest.fixture) -> None:  # type: ignore[assignment]
        with pytest.raises(FileNotFoundError):
            DashboardData.from_db(str(tmp_path / "nonexistent.db"))
