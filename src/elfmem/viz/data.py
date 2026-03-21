"""DashboardData — synchronous SQLite read pass that collects all dashboard data.

Deliberately uses plain sqlite3 (not the async SQLAlchemy stack). Visualisation
is a read-only developer tool; async overhead adds complexity without benefit.
"""

from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

# Mirrors LAMBDA constants in scoring.py — copied to avoid importing core at
# visualisation time (viz is an optional extra; core may not be importable in
# all environments where DashboardData is constructed).
_LAMBDA_TO_TIER: dict[float, str] = {
    0.00001: "permanent",
    0.001: "durable",
    0.010: "standard",
    0.050: "ephemeral",
}
_DEFAULT_LAMBDA: float = 0.010
_PRUNE_THRESHOLD: float = 0.05
_AT_RISK_THRESHOLD: float = 0.10
_CURVE_POINTS: int = 25


def _tier_from_lambda(lam: float) -> str:
    """Map a decay_lambda value to its tier name by finding the closest constant."""
    closest = min(_LAMBDA_TO_TIER.keys(), key=lambda x: abs(x - lam))
    return _LAMBDA_TO_TIER[closest]


def _compute_tier_curves() -> dict[str, list[dict[str, float]]]:
    """Pre-compute _CURVE_POINTS decay points for each tier.

    Horizon for each tier: hours where recency drops to 0.01.
    JS receives plain (x, y) arrays — no maths in the browser.
    """
    curves: dict[str, list[dict[str, float]]] = {}
    for lam, tier in _LAMBDA_TO_TIER.items():
        # horizon: exp(-lam * h) = 0.01  →  h = -ln(0.01) / lam
        horizon = -math.log(0.01) / lam
        points = []
        for i in range(_CURVE_POINTS):
            h = horizon * i / (_CURVE_POINTS - 1)
            y = math.exp(-lam * h)
            points.append({"x": round(h, 2), "y": round(y, 4)})
        curves[tier] = points
    return curves


# ── Sub-dataclasses ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class HealthData:
    inbox_count: int
    active_count: int
    archived_count: int
    total_active_hours: float
    last_consolidated: str  # ISO timestamp or "never"
    health: str  # "good" | "attention" | "degraded"
    suggestion: str
    lifetime_tokens: dict  # type: ignore[type-arg]
    generated_at: str  # ISO timestamp


@dataclass(frozen=True)
class GraphData:
    nodes: list  # type: ignore[type-arg]  # list[dict] — id, label, content, decay_tier, ...
    edges: list  # type: ignore[type-arg]  # list[dict] — from_id, to_id, weight, relation_type, ...
    total_blocks: int  # total active blocks before max_nodes cap
    truncated: bool  # True when max_nodes cap was applied


@dataclass(frozen=True)
class LifecycleData:
    inbox: int
    active: int
    archived: int
    tier_counts: dict  # type: ignore[type-arg]  # {"permanent": N, "durable": N, ...}
    origin_counts: dict  # type: ignore[type-arg]  # block source → count
    edge_origin_counts: dict  # type: ignore[type-arg]  # edge origin → count


@dataclass(frozen=True)
class DecayData:
    blocks: list  # type: ignore[type-arg]  # per-block recency snapshot for scatter chart
    tier_curves: dict  # type: ignore[type-arg]  # pre-computed (x, y) curves per tier
    prune_threshold: float  # 0.05 — archive cliff
    at_risk_count: int  # blocks with recency_score < 0.10
    current_active_hours: float


@dataclass(frozen=True)
class ScoringData:
    frames: list  # type: ignore[type-arg]  # frame name → weight profile for radar chart
    last_retrieval: list  # type: ignore[type-arg]  # most recent frame() blocks; empty if unavailable
    last_retrieval_note: str  # human note when last_retrieval is empty


# ── Top-level dataclass ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DashboardData:
    """Complete dashboard payload built from one synchronous SQLite read pass.

    USE WHEN: Generating the elfmem visualisation dashboard.
    DON'T USE WHEN: In agent production loops — this is a developer/debug tool.
    COST: One synchronous SQLite read pass (~6 queries). No LLM calls.
    RETURNS: Frozen DashboardData with five typed sub-objects.
    NEXT: Pass to render_dashboard() or call to_json() for custom consumers.
    """

    health: HealthData
    graph: GraphData
    lifecycle: LifecycleData
    decay: DecayData
    scoring: ScoringData

    @classmethod
    def from_db(
        cls,
        db_path: str,
        *,
        include_archived: bool = False,
        max_nodes: int = 100,
    ) -> DashboardData:
        """Query SQLite and build all dashboard data.

        USE WHEN: Generating a visualisation. Read-only. No session side effects.
        COST: One synchronous SQLite read pass (~6 queries). Fast.

        Args:
            db_path: Absolute path to the SQLite database file.
            include_archived: Include archived blocks as dim nodes in the graph.
            max_nodes: Cap on graph nodes; top-N selected by centrality.

        Raises:
            FileNotFoundError: If db_path does not exist.
        """
        if not Path(db_path).exists():
            raise FileNotFoundError(f"Database not found: {db_path}")

        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            health = _build_health(conn)
            graph = _build_graph(conn, include_archived=include_archived, max_nodes=max_nodes)
            lifecycle = _build_lifecycle(conn)
            decay = _build_decay(conn)
            scoring = _build_scoring(conn)

        return cls(
            health=health,
            graph=graph,
            lifecycle=lifecycle,
            decay=decay,
            scoring=scoring,
        )

    def to_json(self) -> str:
        """Serialise all data to a JSON string for safe embedding in HTML or custom consumers.

        USE WHEN: Embedding into the dashboard template or piping to external tools.
        RETURNS: JSON string. `<`, `>`, `&` are unicode-escaped so this string is safe
                 to inject into a `<script>` block via Jinja2's `| safe` filter without
                 risk of `</script>` injection breaking the page.
        """
        raw = json.dumps(asdict(self), default=str)
        # Escape HTML special characters to prevent injection when embedding JSON
        # inside a <script> tag. Python's json.dumps does not do this by default.
        # This mirrors Django's json_script approach.
        return raw.replace("&", r"\u0026").replace("<", r"\u003c").replace(">", r"\u003e")


# ── Builder functions ──────────────────────────────────────────────────────────


def _build_health(conn: sqlite3.Connection) -> HealthData:
    status_rows = conn.execute(
        "SELECT status, COUNT(*) as cnt FROM blocks GROUP BY status"
    ).fetchall()
    counts = {row["status"]: row["cnt"] for row in status_rows}
    inbox_count = counts.get("inbox", 0)
    active_count = counts.get("active", 0)
    archived_count = counts.get("archived", 0)

    config_rows = conn.execute("SELECT key, value FROM system_config").fetchall()
    config = {row["key"]: row["value"] for row in config_rows}

    total_active_hours = float(config.get("total_active_hours") or 0.0)
    last_consolidated = config.get("last_consolidated") or "never"

    try:
        lifetime_tokens = json.loads(config.get("lifetime_token_usage") or "{}")
    except (json.JSONDecodeError, TypeError):
        lifetime_tokens = {}

    if active_count == 0 and inbox_count == 0:
        health = "attention"
        suggestion = "Memory is empty. Call learn() to start building knowledge."
    elif inbox_count > 20 or (active_count > 0 and inbox_count > active_count):
        health = "degraded"
        suggestion = (
            f"{inbox_count} unprocessed inbox blocks. Call dream() to consolidate."
        )
    elif inbox_count > 5:
        health = "attention"
        suggestion = f"{inbox_count} blocks pending consolidation — consider dream() soon."
    else:
        health = "good"
        suggestion = (
            "Memory healthy. Call learn() to add knowledge or frame() to retrieve context."
        )

    return HealthData(
        inbox_count=inbox_count,
        active_count=active_count,
        archived_count=archived_count,
        total_active_hours=total_active_hours,
        last_consolidated=last_consolidated,
        health=health,
        suggestion=suggestion,
        lifetime_tokens=lifetime_tokens,
        generated_at=datetime.now(UTC).isoformat(),
    )


def _build_graph(
    conn: sqlite3.Connection,
    *,
    include_archived: bool,
    max_nodes: int,
) -> GraphData:
    # Check which columns exist in blocks table for backward compatibility
    blocks_cols = {row[1] for row in conn.execute("PRAGMA table_info(blocks)").fetchall()}
    edges_cols = {row[1] for row in conn.execute("PRAGMA table_info(edges)").fetchall()}

    status_clause = "status IN ('active', 'archived')" if include_archived else "status = 'active'"
    block_select = "id, content, category, confidence, reinforcement_count, decay_lambda, status"
    if "self_alignment" in blocks_cols:
        block_select += ", self_alignment"

    block_rows = conn.execute(
        f"""
        SELECT {block_select}
        FROM blocks WHERE {status_clause}
        """
    ).fetchall()

    tag_rows = conn.execute("SELECT block_id, tag FROM block_tags").fetchall()
    tags_map: dict[str, list[str]] = {}
    for row in tag_rows:
        tags_map.setdefault(row["block_id"], []).append(row["tag"])

    edge_select = "e.from_id, e.to_id, e.weight, e.reinforcement_count"
    if "relation_type" in edges_cols:
        edge_select += ", e.relation_type"
    if "origin" in edges_cols:
        edge_select += ", e.origin"

    edge_rows = conn.execute(
        f"""
        SELECT {edge_select}
        FROM edges e
        JOIN blocks ba ON e.from_id = ba.id AND ba.status = 'active'
        JOIN blocks bb ON e.to_id   = bb.id AND bb.status = 'active'
        """
    ).fetchall()

    # Weighted degree centrality
    degree: dict[str, float] = {}
    for e in edge_rows:
        w = e["weight"] or 0.0
        degree[e["from_id"]] = degree.get(e["from_id"], 0.0) + w
        degree[e["to_id"]] = degree.get(e["to_id"], 0.0) + w
    max_deg = max(degree.values(), default=1.0)
    centrality = {bid: deg / max_deg for bid, deg in degree.items()}

    total = len(block_rows)
    if total > max_nodes:
        block_rows = sorted(
            block_rows,
            key=lambda b: centrality.get(b["id"], 0.0),
            reverse=True,
        )[:max_nodes]
    included_ids = {b["id"] for b in block_rows}

    edges_filtered = [
        e for e in edge_rows
        if e["from_id"] in included_ids and e["to_id"] in included_ids
    ]

    nodes = [
        {
            "id": b["id"],
            "label": (b["content"] or "")[:60],
            "content": (b["content"] or "")[:500],
            "decay_tier": _tier_from_lambda(b["decay_lambda"] or _DEFAULT_LAMBDA),
            "status": b["status"],
            "confidence": b["confidence"] or 0.0,
            "reinforcement_count": b["reinforcement_count"] or 0,
            "centrality": round(centrality.get(b["id"], 0.0), 4),
            "self_alignment": (
                (b["self_alignment"] or 0.0) if "self_alignment" in blocks_cols else 0.0
            ),
            "tags": tags_map.get(b["id"], []),
            "category": b["category"] or "",
        }
        for b in block_rows
    ]

    edge_dicts = [
        {
            "from_id": e["from_id"],
            "to_id": e["to_id"],
            "weight": e["weight"] or 0.0,
            "relation_type": (
                (e["relation_type"] or "similar") if "relation_type" in edges_cols else "similar"
            ),
            "origin": (e["origin"] or "similarity") if "origin" in edges_cols else "similarity",
            "reinforcement_count": e["reinforcement_count"] or 0,
        }
        for e in edges_filtered
    ]

    return GraphData(
        nodes=nodes,
        edges=edge_dicts,
        total_blocks=total,
        truncated=total > max_nodes,
    )


def _build_lifecycle(conn: sqlite3.Connection) -> LifecycleData:
    status_rows = conn.execute(
        "SELECT status, COUNT(*) as cnt FROM blocks GROUP BY status"
    ).fetchall()
    status_counts = {row["status"]: row["cnt"] for row in status_rows}

    tier_rows = conn.execute(
        "SELECT decay_lambda, COUNT(*) as cnt FROM blocks"
        " WHERE status='active' GROUP BY decay_lambda"
    ).fetchall()
    tier_counts: dict[str, int] = {
        "permanent": 0, "durable": 0, "standard": 0, "ephemeral": 0
    }
    for row in tier_rows:
        tier = _tier_from_lambda(row["decay_lambda"] or _DEFAULT_LAMBDA)
        tier_counts[tier] = tier_counts.get(tier, 0) + row["cnt"]

    origin_rows = conn.execute(
        "SELECT source, COUNT(*) as cnt FROM blocks GROUP BY source"
    ).fetchall()
    origin_counts = {row["source"]: row["cnt"] for row in origin_rows}

    edge_origin_rows = conn.execute(
        "SELECT origin, COUNT(*) as cnt FROM edges GROUP BY origin"
    ).fetchall()
    edge_origin_counts = {row["origin"]: row["cnt"] for row in edge_origin_rows}

    return LifecycleData(
        inbox=status_counts.get("inbox", 0),
        active=status_counts.get("active", 0),
        archived=status_counts.get("archived", 0),
        tier_counts=tier_counts,
        origin_counts=origin_counts,
        edge_origin_counts=edge_origin_counts,
    )


def _build_decay(conn: sqlite3.Connection) -> DecayData:
    config_row = conn.execute(
        "SELECT value FROM system_config WHERE key='total_active_hours'"
    ).fetchone()
    current_active_hours = float(config_row["value"]) if config_row else 0.0

    block_rows = conn.execute(
        """
        SELECT id, content, decay_lambda, last_reinforced_at, reinforcement_count, confidence
        FROM blocks WHERE status = 'active'
        """
    ).fetchall()

    blocks_data = []
    at_risk_count = 0
    for row in block_rows:
        lam = row["decay_lambda"] or _DEFAULT_LAMBDA
        hours_since = max(0.0, current_active_hours - (row["last_reinforced_at"] or 0.0))
        recency = math.exp(-lam * hours_since)
        if recency < _AT_RISK_THRESHOLD:
            at_risk_count += 1
        blocks_data.append(
            {
                "id": row["id"],
                "recency_score": round(recency, 4),
                "decay_tier": _tier_from_lambda(lam),
                "hours_since_reinforced": round(hours_since, 2),
                "reinforcement_count": row["reinforcement_count"] or 0,
                "confidence": row["confidence"] or 0.0,
                "content_preview": (row["content"] or "")[:60],
            }
        )

    return DecayData(
        blocks=blocks_data,
        tier_curves=_compute_tier_curves(),
        prune_threshold=_PRUNE_THRESHOLD,
        at_risk_count=at_risk_count,
        current_active_hours=current_active_hours,
    )


def _build_scoring(conn: sqlite3.Connection) -> ScoringData:
    frame_rows = conn.execute(
        "SELECT name, weights_json, token_budget, cache_json FROM frames"
    ).fetchall()

    frames = []
    for row in frame_rows:
        try:
            weights = json.loads(row["weights_json"] or "{}")
        except (json.JSONDecodeError, TypeError):
            weights = {}
        try:
            cache = json.loads(row["cache_json"] or "{}")
        except (json.JSONDecodeError, TypeError):
            cache = {}
        frames.append(
            {
                "name": row["name"],
                "weights": weights,
                "token_budget": row["token_budget"],
                "cache_ttl": cache.get("ttl_seconds", 0),
            }
        )

    # block_outcomes stores outcome signals, not retrieval score breakdowns.
    # last_retrieval is intentionally empty in the current schema.
    return ScoringData(
        frames=frames,
        last_retrieval=[],
        last_retrieval_note=(
            "Score breakdown per retrieval is not stored in the current schema. "
            "Use frame() and inspect the returned ScoredBlock objects directly."
        ),
    )
