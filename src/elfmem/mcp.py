"""elfmem MCP server — adaptive memory as agent tools.

Start:  elfmem serve --db agent.db
        elfmem serve --db agent.db --config elfmem.yaml
        elfmem serve --db agent.db --adaptive-policy
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastmcp import FastMCP

from elfmem.api import MemorySystem
from elfmem.policy import ConsolidationPolicy
from elfmem.smart import format_recall_response

_memory: MemorySystem | None = None
_db_path: str = ""
_config_path: str | None = None
_use_adaptive_policy: bool = False


@asynccontextmanager
async def _lifespan(server: FastMCP) -> AsyncIterator[None]:  # type: ignore[type-arg]
    global _memory
    policy = ConsolidationPolicy() if _use_adaptive_policy else None
    _memory = await MemorySystem.from_config(_db_path, config=_config_path, policy=policy)
    try:
        yield
    finally:
        await _memory.end_session()
        await _memory.close()
        _memory = None


mcp = FastMCP("elfmem", lifespan=_lifespan)


def _mem() -> MemorySystem:
    """Return active MemorySystem. Fails fast if server not initialised."""
    if _memory is None:
        raise RuntimeError("elfmem MCP server not initialised.")
    return _memory


@mcp.tool()
async def elfmem_remember(
    content: str,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """Store knowledge for future retrieval.

    Call when the agent discovers a fact, preference, decision, or observation
    worth keeping across sessions. Pure learn, no blocking.

    Returns: block_id, status, and should_dream advisory.
    If should_dream is True, consolidation (embedding, alignment, contradictions)
    will benefit from running soon via elfmem_dream.
    """
    mem = _mem()
    result = await mem.remember(content, tags=tags)
    response = result.to_dict()
    response["should_dream"] = mem.should_dream
    return response


@mcp.tool()
async def elfmem_recall(
    query: str,
    top_k: int = 5,
    frame: str = "attention",
) -> dict[str, Any]:
    """Retrieve relevant knowledge, rendered for prompt injection.

    Use result.text directly in your LLM prompt. Block IDs in result.blocks
    can be passed to elfmem_outcome to record outcome feedback later.
    frame: "attention" (query-driven, default) | "self" (identity) | "task" (goals).
    """
    mem = _mem()
    await mem.begin_session()  # idempotent — no-op if session already active
    result = await mem.frame(frame, query=query or None, top_k=top_k)
    return format_recall_response(result)


@mcp.tool()
async def elfmem_status() -> dict[str, Any]:
    """Memory health snapshot. Check the suggestion field for recommended action."""
    result = await _mem().status()
    return result.to_dict()


@mcp.tool()
async def elfmem_outcome(
    block_ids: list[str],
    signal: float,
    weight: float = 1.0,
    source: str = "",
) -> dict[str, Any]:
    """Update block confidence from a domain outcome signal.

    signal: 0.0-1.0. Use block IDs from a previous elfmem_recall response.
    0.8-1.0 reinforces (decay resets). 0.2-0.8 neutral. 0.0-0.2 penalises.
    """
    result = await _mem().outcome(block_ids, signal, weight=weight, source=source)
    return result.to_dict()


@mcp.tool()
async def elfmem_curate() -> dict[str, Any]:
    """Archive decayed blocks, prune weak edges, reinforce top knowledge.

    Runs automatically on schedule after consolidation.
    Call manually only if retrieval quality visibly degrades.
    """
    result = await _mem().curate()
    return result.to_dict()


@mcp.tool()
async def elfmem_dream() -> dict[str, Any]:
    """Deep consolidation: embed, align, detect contradictions, build graph.

    Call when elfmem_remember indicates should_dream=True, or when you want
    to consolidate pending knowledge. Safe at natural pause points.

    Embedding & LLM calls per pending block. Slow if many pending.
    Returns: blocks processed, promoted, dedup'd, edges created.
    """
    result = await _mem().dream()
    if result is None:
        return {"message": "No pending blocks to consolidate", "status": "idle"}
    return result.to_dict()


@mcp.tool()
async def elfmem_setup(
    identity: str | None = None,
    values: list[str] | None = None,
    seed: bool = True,
) -> dict[str, Any]:
    """Bootstrap agent identity in the SELF frame.

    Call this on first use to establish who you are. Seeds 10 constitutional
    blocks that form the cognitive loop (curiosity, feedback, balance, etc.)
    then adds any identity description and values you provide.

    Safe to call multiple times — exact duplicate content is silently rejected,
    so re-running is harmless. Constitutional blocks are created once, then
    skipped on subsequent calls.

    seed:     Seed the 10 constitutional blocks (default True). Pass False to
              skip constitutional seeding and only add identity/values.
    identity: Optional natural language description of agent role and constraints.
    values:   Optional list of domain-specific principles (each stored separately).

    Returns blocks_created and total_attempted counts.
    """
    result = await _mem().setup(identity=identity, values=values, seed=seed)
    return result.to_dict()


@mcp.tool()
async def elfmem_connect(
    source: str,
    target: str,
    relation: str = "similar",
    note: str | None = None,
    if_exists: str = "reinforce",
) -> dict[str, Any]:
    """Create or strengthen a semantic edge between two knowledge blocks.

    Use block IDs from elfmem_recall or elfmem_remember responses.
    Block IDs are also available via system.last_recall_block_ids and
    system.last_learned_block_id after calling those tools.

    relation: 'similar' | 'supports' | 'contradicts' | 'elaborates' | 'co_occurs' | 'outcome' | <custom>
    if_exists: 'reinforce' (default) | 'update' | 'skip' | 'error'
    """
    result = await _mem().connect(
        source, target, relation=relation, note=note, if_exists=if_exists  # type: ignore[arg-type]
    )
    return result.to_dict()


@mcp.tool()
async def elfmem_disconnect(
    source: str,
    target: str,
    guard_relation: str | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    """Remove the edge between two blocks. Use to correct wrong connections.

    guard_relation: Only remove if current relation type matches this value (safety check).
    Returns action: 'removed' | 'not_found' | 'guarded'.
    """
    result = await _mem().disconnect(
        source, target, guard_relation=guard_relation, reason=reason
    )
    return result.to_dict()


@mcp.tool()
async def elfmem_guide(method: str | None = None) -> str:
    """Detailed documentation for a specific operation, or the full overview.

    method: e.g. "remember", "recall", "outcome". None returns overview table.
    """
    return _mem().guide(method)


# ── Entry point ───────────────────────────────────────────────────────────────


def main(
    db_path: str,
    config_path: str | None = None,
    *,
    use_adaptive_policy: bool = False,
) -> None:
    """Start the MCP server. Called by ``elfmem serve``.

    Args:
        db_path: Path to SQLite database file.
        config_path: Optional path to elfmem.yaml config file.
        use_adaptive_policy: When True, enables ConsolidationPolicy so the
            server self-tunes its consolidation threshold based on promotion
            rate feedback. The learned threshold persists across restarts.
    """
    global _db_path, _config_path, _use_adaptive_policy
    _db_path = db_path
    _config_path = config_path
    _use_adaptive_policy = use_adaptive_policy
    mcp.run()


if __name__ == "__main__":
    import os
    import sys

    db_path = os.path.expanduser(os.getenv("ELFMEM_DB_PATH", "~/.elfmem/default.db"))
    config_path = os.getenv("ELFMEM_CONFIG_PATH")
    if config_path:
        config_path = os.path.expanduser(config_path)
    adaptive = os.getenv("ELFMEM_ADAPTIVE_POLICY", "").lower() in ("1", "true", "yes")

    try:
        main(db_path, config_path, use_adaptive_policy=adaptive)
    except KeyboardInterrupt:
        sys.exit(0)
