"""elfmem MCP server — adaptive memory as agent tools.

Start:  elfmem serve --db agent.db
        elfmem serve --db agent.db --config elfmem.yaml
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastmcp import FastMCP

from elfmem.smart import SmartMemory, format_recall_response

_memory: SmartMemory | None = None
_db_path: str = ""
_config_path: str | None = None


@asynccontextmanager
async def _lifespan(server: FastMCP) -> AsyncIterator[None]:  # type: ignore[type-arg]
    global _memory
    _memory = await SmartMemory.open(_db_path, config=_config_path)
    try:
        yield
    finally:
        await _memory.close()
        _memory = None


mcp = FastMCP("elfmem", lifespan=_lifespan)


def _mem() -> SmartMemory:
    """Return active SmartMemory. Fails fast if server not initialised."""
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
    result = await _mem().remember(content, tags=tags)
    response = result.to_dict()
    response["should_dream"] = _mem().should_dream
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
    result = await _mem().recall(query, top_k=top_k, frame=frame)
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

    Returns blocks_created count and per-block status dicts.
    """
    results = []

    if seed:
        from elfmem.seed import CONSTITUTIONAL_SEED
        for block in CONSTITUTIONAL_SEED:
            r = await _mem().remember(
                block["content"],  # type: ignore[arg-type]
                tags=block["tags"],  # type: ignore[arg-type]
            )
            results.append(r.to_dict())

    if identity:
        r = await _mem().remember(identity, tags=["self/context"])
        results.append(r.to_dict())

    if values:
        for value in values:
            r = await _mem().remember(value, tags=["self/value"])
            results.append(r.to_dict())

    created = sum(1 for r in results if r["status"] == "created")
    return {
        "status": "setup_complete",
        "blocks_created": created,
        "blocks": results,
    }


@mcp.tool()
async def elfmem_guide(method: str | None = None) -> str:
    """Detailed documentation for a specific operation, or the full overview.

    method: e.g. "remember", "recall", "outcome". None returns overview table.
    """
    return _mem().guide(method)


# ── Entry point ───────────────────────────────────────────────────────────────


def main(db_path: str, config_path: str | None = None) -> None:
    """Start the MCP server. Called by `elfmem serve`."""
    global _db_path, _config_path
    _db_path = db_path
    _config_path = config_path
    mcp.run()


if __name__ == "__main__":
    import os
    import sys

    db_path = os.path.expanduser(os.getenv("ELFMEM_DB_PATH", "~/.elfmem/default.db"))
    config_path = os.getenv("ELFMEM_CONFIG_PATH")
    if config_path:
        config_path = os.path.expanduser(config_path)

    try:
        main(db_path, config_path)
    except KeyboardInterrupt:
        sys.exit(0)
