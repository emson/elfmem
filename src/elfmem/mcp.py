"""elfmem MCP server — adaptive memory as agent tools.

Start:  elfmem serve --db agent.db
        elfmem serve --db agent.db --config elfmem.yaml
        elfmem serve --db agent.db --adaptive-policy

Tool implementation pattern
---------------------------
Each public tool (elfmem_*) is a thin @mcp.tool() wrapper that delegates to a
private _tool_* coroutine. This keeps the business logic directly callable and
independently testable without going through FastMCP's FunctionTool machinery.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastmcp import FastMCP

from elfmem.api import MemorySystem, format_recall_response
from elfmem.policy import ConsolidationPolicy

_memory: MemorySystem | None = None
_db_path: str = ""
_config_path: str | None = None
_use_adaptive_policy: bool = False


@asynccontextmanager
async def _lifespan(server: FastMCP) -> AsyncIterator[None]:
    global _memory, _config_path, _db_path
    policy = ConsolidationPolicy() if _use_adaptive_policy else None
    from elfmem.project import find_local_config, resolve_db

    # Step 1: Prefer project-local config over the global startup value.
    # The project config carries the right project.db, LLM settings, and identity.
    config_source = "explicit (--config)"
    if not _config_path:
        discovered = find_local_config()
        if discovered is not None:
            _config_path = str(discovered)
            config_source = "auto-discovered (.elfmem/config.yaml)"
        else:
            config_source = "none"

    # Step 2: Re-resolve DB now that we have the best available config.
    # _db_path is non-empty only when --db was passed (or set at startup) — in
    # that case honour it; otherwise consult ELFMEM_DB env, project.db in
    # config, then the global fallback.
    db_source = "explicit (--db)"
    if not _db_path:
        _db_path, db_source = resolve_db(None, _config_path)

    # Step 3: Identity banner. One stderr line so failed-config and identity
    # mix-ups are visible in Claude Code's MCP logs without enabling debug.
    _print_boot_banner(_db_path, db_source, _config_path, config_source)

    _memory = await MemorySystem.from_config(_db_path, config=_config_path, policy=policy)
    try:
        yield
    finally:
        await _memory.end_session()
        await _memory.close()
        _memory = None


def _print_boot_banner(
    db_path: str, db_source: str, config_path: str | None, config_source: str
) -> None:
    """Print a one-line identity banner to stderr at MCP startup.

    Shows the resolved DB and config paths with their resolution sources, so
    silent fallbacks to ~/.elfmem/agent.db are visible in Claude Code's MCP
    server logs without needing debug mode.
    """
    import sys
    cfg = config_path or "<none>"
    print(
        f"[elfmem] mcp boot: db={db_path} ({db_source}) "
        f"config={cfg} ({config_source})",
        file=sys.stderr,
    )


mcp = FastMCP("elfmem", lifespan=_lifespan)


def _mem() -> MemorySystem:
    """Return active MemorySystem. Fails fast if server not initialised."""
    if _memory is None:
        raise RuntimeError("elfmem MCP server not initialised.")
    return _memory


# ── Tool implementations (directly callable, used by tests) ──────────────────


async def _tool_remember(
    content: str,
    tags: list[str] | None = None,
    cue: str | None = None,
) -> dict[str, Any]:
    mem = _mem()
    result = await mem.remember(content, tags=tags, cue=cue)
    response = result.to_dict()
    response["should_dream"] = mem.should_dream
    return response


async def _tool_recall(
    query: str,
    top_k: int = 5,
    frame: str = "attention",
) -> dict[str, Any]:
    mem = _mem()
    await mem.begin_session()  # idempotent — no-op if session already active
    result = await mem.frame(frame, query=query or None, top_k=top_k)
    return format_recall_response(result)


async def _tool_edit(
    block_id: str, content: str | None = None, cue: str | None = None,
) -> dict[str, Any]:
    mem = _mem()
    result = await mem.edit(block_id, content, cue=cue)
    return result.to_dict()


async def _tool_forget(block_id: str) -> dict[str, Any]:
    mem = _mem()
    result = await mem.forget(block_id)
    return result.to_dict()


async def _tool_ls(
    tag: str | None = None,
    category: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    mem = _mem()
    results = await mem.ls(tag, category, limit=limit)
    return [r.to_dict() for r in results]


async def _tool_inbox(max_count: int | None = None) -> list[dict[str, Any]]:
    results = await _mem().inbox(max_count)
    return [r.to_dict() for r in results]


async def _tool_status(peer_inbox: bool = False) -> dict[str, Any]:
    mem = _mem()
    result = (await mem.status()).to_dict()
    if peer_inbox:
        result["peer_inbox"] = mem.peer_inbox_status().to_dict()
    return result


async def _tool_outcome(
    block_ids: list[str],
    signal: float,
    weight: float = 1.0,
    source: str = "",
) -> dict[str, Any]:
    result = await _mem().outcome(block_ids, signal, weight=weight, source=source)
    return result.to_dict()


async def _tool_curate() -> dict[str, Any]:
    result = await _mem().curate()
    return result.to_dict()


async def _tool_dream(
    rescore: bool = False,
    rescore_max: int | None = None,
    no_llm: bool = False,
    host_analyses: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    result = await _mem().dream(
        skip_llm=no_llm,
        rescore=rescore,
        rescore_max=rescore_max,
        host_analyses=host_analyses,
    )
    if result is None:
        return {"message": "No pending blocks to consolidate", "status": "idle"}
    return result.to_dict()


async def _tool_setup(
    identity: str | None = None,
    values: list[str] | None = None,
    seed: bool = False,
) -> dict[str, Any]:
    result = await _mem().setup(identity=identity, values=values, seed=seed)
    return result.to_dict()


async def _tool_connect(
    source: str,
    target: str,
    relation: str | None = None,
    note: str | None = None,
    if_exists: str = "reinforce",
) -> dict[str, Any]:
    result = await _mem().connect(
        source, target, relation=relation, note=note, if_exists=if_exists  # type: ignore[arg-type]
    )
    return result.to_dict()


async def _tool_disconnect(
    source: str,
    target: str,
    guard_relation: str | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    result = await _mem().disconnect(
        source, target, guard_relation=guard_relation, reason=reason
    )
    return result.to_dict()


async def _tool_guide(method: str | None = None) -> str:
    return _mem().guide(method)


# ── Theory of Mind tools (mind_*) ────────────────────────────────────────────


async def _tool_mind_create(
    subject: str,
    goals: list[str] | None = None,
    beliefs: list[str] | None = None,
    fears: list[str] | None = None,
    motivations: list[str] | None = None,
) -> dict[str, Any]:
    result = await _mem().mind_create(
        subject,
        goals=goals,
        beliefs=beliefs,
        fears=fears,
        motivations=motivations,
    )
    return result.to_dict()


async def _tool_mind_predict(
    mind_block_id: str,
    prediction: str,
    verify_at: str,
    reasoning: str | None = None,
) -> dict[str, Any]:
    result = await _mem().mind_predict(
        mind_block_id,
        prediction,
        verify_at=verify_at,
        reasoning=reasoning,
    )
    return result.to_dict()


async def _tool_mind_list() -> list[dict[str, Any]]:
    results = await _mem().mind_list()
    return [r.to_dict() for r in results]


async def _tool_mind_show(mind_block_id: str) -> dict[str, Any]:
    result = await _mem().mind_show(mind_block_id)
    return result.to_dict()


async def _tool_mind_outcome(
    decision_block_id: str,
    hit: bool,
    reason: str,
) -> dict[str, Any]:
    result = await _mem().mind_outcome(
        decision_block_id, hit=hit, reason=reason
    )
    return result.to_dict()


async def _tool_peer_send(
    to_peer: str,
    content: str,
    in_reply_to: str | None = None,
) -> dict[str, Any]:
    result = await _mem().peer_send(to_peer, content, in_reply_to=in_reply_to)
    return result.to_dict()


async def _tool_peer_inbox(
    from_peer: str | None = None,
    import_all: bool = False,
) -> dict[str, Any]:
    result = await _mem().peer_inbox(from_peer=from_peer, import_all=import_all)
    return result.to_dict()


def _tool_peer_inbox_status() -> dict[str, Any]:
    return _mem().peer_inbox_status().to_dict()


async def _tool_peer_list() -> list[dict[str, Any]]:
    results = await _mem().peer_list()
    return [r.to_dict() for r in results]


async def _tool_export(
    share_level: str = "public",
    min_confidence: float = 0.0,
    output_path: str = "export.json",
) -> dict[str, Any]:
    result = await _mem().export_blocks(
        share_level=share_level,
        min_confidence=min_confidence,
        output_path=output_path,
    )
    return result.to_dict()


async def _tool_import(
    path: str,
    from_peer: str | None = None,
    self_merge: bool = False,
) -> dict[str, Any]:
    result = await _mem().import_blocks(
        path, from_peer=from_peer, self_merge=self_merge,
    )
    return result.to_dict()


# ── Constitutional review tools (v0.18) ──────────────────────────────────────


def _error_envelope(exc: Exception) -> dict[str, Any]:
    """Project the agent-first ElfmemError contract into an MCP response.

    Every ElfmemError carries a ``.recovery`` — that's the actionable
    instruction the calling agent needs. Surfacing it in the response body
    (rather than letting the exception cross the FastMCP boundary as an
    unstructured string) means the agent can branch on the recovery hint
    without parsing free-form text.
    """
    return {
        "error": str(exc.args[0]) if exc.args else exc.__class__.__name__,
        "recovery": getattr(exc, "recovery", None),
    }


async def _tool_review_constitutional(
    drift_threshold: float | None = None,
    max_proposals: int | None = None,
    min_block_evidence: float | None = None,
    min_age_days: float | None = None,
) -> dict[str, Any]:
    from elfmem.exceptions import ElfmemError
    try:
        result = await _mem().review_constitutional(
            drift_threshold=drift_threshold,
            max_proposals=max_proposals,
            min_block_evidence=min_block_evidence,
            min_age_days=min_age_days,
        )
        return result.to_dict()
    except ElfmemError as e:
        return _error_envelope(e)


async def _tool_review_corpus() -> dict[str, Any]:
    from elfmem.exceptions import ElfmemError
    try:
        result = await _mem().review_corpus()
        return result.to_dict()
    except ElfmemError as e:
        return _error_envelope(e)


async def _tool_accept_amendment(
    block_id: str,
    proposed_content: str,
    rationale: str | None = None,
    drift_score: float | None = None,
) -> dict[str, Any]:
    from elfmem.exceptions import ElfmemError
    try:
        result = await _mem().accept_amendment(
            block_id, proposed_content, rationale,
            drift_score=drift_score,
            acceptor="agent",
        )
        return result.to_dict()
    except ElfmemError as e:
        return _error_envelope(e)


async def _tool_revert_amendment(amendment_id: int) -> dict[str, Any]:
    from elfmem.exceptions import ElfmemError
    try:
        result = await _mem().revert_amendment(amendment_id)
        return result.to_dict()
    except ElfmemError as e:
        return _error_envelope(e)


async def _tool_list_amendments(
    block_id: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    from elfmem.exceptions import ElfmemError
    try:
        records = await _mem().list_amendments(block_id=block_id, limit=limit)
        return {"amendments": [r.to_dict() for r in records]}
    except ElfmemError as e:
        return _error_envelope(e)


# ── MCP tool registrations (thin wrappers, one line each) ─────────────────────


@mcp.tool()
async def elfmem_remember(
    content: str,
    tags: list[str] | None = None,
    cue: str | None = None,
) -> dict[str, Any]:
    """Store knowledge for future retrieval.

    Call when the agent discovers a fact, preference, decision, or observation
    worth keeping across sessions. Pure learn, no blocking.

    ALWAYS pass a `cue`. It states *when a future agent should recall this
    block* — the situation, not a summary. Retrieval matches it lexically
    against the query, so it is what rescues a memory whose wording differs
    from how the question gets asked. Write it the way someone would type it
    in that moment:

        cue="deciding whether to add a new command or extend an existing one"
        cue="when a dream run is killed partway through consolidation"

    Not "when relevant", and not a restatement of the block's conclusion.

    Returns: block_id, status, and should_dream advisory.
    If should_dream is True, consolidation (embedding, alignment, contradictions)
    will benefit from running soon via elfmem_dream.
    """
    return await _tool_remember(content, tags=tags)


@mcp.tool()
async def elfmem_recall(
    query: str,
    top_k: int = 5,
    frame: str = "attention",
) -> dict[str, Any]:
    """Retrieve relevant knowledge, rendered for prompt injection.

    Use result.text directly in your LLM prompt. Block IDs in result.blocks
    can be passed to elfmem_outcome to record outcome feedback later.
    frame: "attention" (query-driven, default) | "self" (identity) | "task" (goals)
        | "simulate" (Theory-of-Mind retrieval — self constitution + mind/* blocks).
    """
    return await _tool_recall(query, top_k=top_k, frame=frame)


@mcp.tool()
async def elfmem_edit(
    block_id: str, content: str | None = None, cue: str | None = None,
) -> dict[str, Any]:
    """Edit an active block's content and/or its cue line. No LLM mediation.

    Use when a stored memory is wrong, stale, or needs rewording and you
    know its block_id (e.g. from elfmem_ls). Pass `content` to rewrite it,
    `cue` to change when it should be recalled, or both.

    Setting only a cue is cheap: no re-embedding, and confidence,
    reinforcement and the rescore queue are all untouched — a cue says when
    to recall a block, not what it claims.
    """
    return await _tool_edit(block_id, content, cue)


@mcp.tool()
async def elfmem_forget(block_id: str) -> dict[str, Any]:
    """Archive a block by explicit request — the direct delete path.

    Use when a memory should no longer be active. Idempotent: forgetting
    an already-archived block returns status='already_archived', not an error.
    """
    return await _tool_forget(block_id)


@mcp.tool()
async def elfmem_ls(
    tag: str | None = None,
    category: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """List active blocks — a deterministic, unscored view of memory.

    Use to find a block_id for elfmem_edit/elfmem_forget, or to audit
    memory contents directly. Not relevance-ranked — use elfmem_recall
    for that. tag is a SQL LIKE pattern (e.g. 'self/%').
    """
    return await _tool_ls(tag, category, limit)


@mcp.tool()
async def elfmem_inbox(max_count: int | None = None) -> list[dict[str, Any]]:
    """List pending blocks not yet consolidated — FIFO order (oldest first).

    Read-only, no LLM calls. Use this to reason about pending blocks
    yourself (alignment_score 0.0-1.0, tags from the self/* vocabulary,
    a factual 1-2 sentence summary) before calling elfmem_dream with
    host_analyses — i.e. supplying your own analysis instead of relying on
    elfmem's configured LLM adapter.
    """
    return await _tool_inbox(max_count)


@mcp.tool()
async def elfmem_status(peer_inbox: bool = False) -> dict[str, Any]:
    """Memory health snapshot. Check the suggestion field for recommended action.

    With peer_inbox=True, includes a peer_inbox key with pending message count
    and sender list — use this to decide whether to trigger message processing.
    """
    return await _tool_status(peer_inbox=peer_inbox)


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
    return await _tool_outcome(block_ids, signal, weight=weight, source=source)


@mcp.tool()
async def elfmem_curate() -> dict[str, Any]:
    """Prune weak/decayed edges, reinforce top knowledge.

    Runs automatically on schedule after consolidation.
    Call manually only if retrieval quality visibly degrades.
    """
    return await _tool_curate()


@mcp.tool()
async def elfmem_dream(
    rescore: bool = False,
    rescore_max: int | None = None,
    no_llm: bool = False,
    host_analyses: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Deep consolidation: embed, align, promote to active memory, build graph.

    Call when elfmem_remember indicates should_dream=True, or when you want
    to consolidate pending knowledge. Safe at natural pause points.

    Embedding & LLM calls per pending block. Slow if many pending.
    Returns: blocks processed, promoted, dedup'd, edges created — plus
    rescored / rescore_failed when rescore=True.

    rescore: After processing the inbox, refresh aged or unscored active
             blocks against the current SELF (deep-sleep mode, v0.13.3).
             Selection: NULL last_scored_at first, then oldest by
             last_scored_at. Mutually exclusive with no_llm=True.
    rescore_max: Cap the number of blocks rescored in this call. None = use
             the configured RescoreConfig.max_per_run.
    no_llm: Bypass all LLM calls — embed + promote only. Use for outages,
             bulk loads, cost-sensitive batches. Affected blocks have
             last_scored_at=NULL and will be picked up first by a future
             rescore=True call.
    host_analyses: supply your own analysis for some or all pending blocks
             instead of elfmem's configured LLM adapter — e.g. this session
             reasoning over elfmem_inbox's output. Shape:
             {"block_id": {"alignment_score": 0.0-1.0, "tags": [...],
             "summary": "..."}, ...}. tags must come from the self/*
             vocabulary (self/constitutional, self/constraint, self/value,
             self/style, self/goal, self/context) — others are silently
             dropped. Blocks not covered here still use the normal path
             (configured adapter, or the no_llm fallback).
    """
    return await _tool_dream(
        rescore=rescore,
        rescore_max=rescore_max,
        no_llm=no_llm,
        host_analyses=host_analyses,
    )


@mcp.tool()
async def elfmem_setup(
    identity: str | None = None,
    values: list[str] | None = None,
    seed: bool = False,
) -> dict[str, Any]:
    """Bootstrap agent identity in the SELF frame.

    Call this on first use to establish who you are — adds any identity
    description and values you provide. Pass seed=True to also seed 10
    constitutional blocks that form a cognitive loop (curiosity, feedback,
    balance, etc.) — an opinionated starting personality, not a requirement.

    Safe to call multiple times — exact duplicate content is silently rejected,
    so re-running is harmless. Constitutional blocks (if seeded) are created
    once, then skipped on subsequent calls.

    seed:     Seed the 10 constitutional blocks (default False — this no
              longer happens silently; opt in explicitly).
    identity: Optional natural language description of agent role and constraints.
    values:   Optional list of domain-specific principles (each stored separately).

    Returns blocks_created and total_attempted counts.
    """
    return await _tool_setup(identity=identity, values=values, seed=seed)


@mcp.tool()
async def elfmem_connect(
    source: str,
    target: str,
    relation: str | None = None,
    note: str | None = None,
    if_exists: str = "reinforce",
) -> dict[str, Any]:
    """Create or strengthen a semantic edge between two knowledge blocks.

    Use block IDs from elfmem_recall or elfmem_remember responses.
    Block IDs are also available via system.last_recall_block_ids and
    system.last_learned_block_id after calling those tools.

    relation: 'similar' (used for new edges if omitted) | 'supports' |
              'contradicts' | 'elaborates' | 'co_occurs' | 'outcome' | <custom>.
              Omit to reinforce/update an existing edge without asserting a
              relation — passing a value that conflicts with the stored relation
              under if_exists='reinforce' raises (with .recovery pointing to
              if_exists='update').
    if_exists: 'reinforce' (default) | 'update' | 'skip' | 'error'
    """
    return await _tool_connect(source, target, relation=relation, note=note, if_exists=if_exists)


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
    return await _tool_disconnect(source, target, guard_relation=guard_relation, reason=reason)


@mcp.tool()
async def elfmem_mind_create(
    subject: str,
    goals: list[str] | None = None,
    beliefs: list[str] | None = None,
    fears: list[str] | None = None,
    motivations: list[str] | None = None,
) -> dict[str, Any]:
    """Create a Theory of Mind block for a subject (another agent, user, stakeholder).

    Stores a structured mental model — goals / beliefs / fears / motivations —
    as a `mind` category block with DURABLE decay (~6 month half-life). The
    model is retrievable via the `simulate` frame, and predictions about the
    subject can be attached with elfmem_mind_predict.

    USE WHEN: modelling a specific actor's likely behaviour for downstream
    reasoning. DON'T USE WHEN: storing general facts about someone — use
    elfmem_remember instead.

    Returns: LearnResult with the mind block ID. Cost: instant, no LLM.
    """
    return await _tool_mind_create(
        subject=subject,
        goals=goals,
        beliefs=beliefs,
        fears=fears,
        motivations=motivations,
    )


@mcp.tool()
async def elfmem_mind_predict(
    mind_block_id: str,
    prediction: str,
    verify_at: str,
    reasoning: str | None = None,
) -> dict[str, Any]:
    """Add a falsifiable prediction linked to a mind block.

    Creates a `decision` category block and a `predicts` edge to the mind
    block. Predictions must have a verify_at date (ISO format) — the moment
    when the prediction will become observable. Close the loop with
    elfmem_mind_outcome when the date arrives.

    USE WHEN: you have a specific, testable hypothesis about what a modelled
    mind will do. DON'T USE WHEN: the claim is unfalsifiable or has no
    verification date.

    Returns: MindPredictResult with decision_block_id and edge status.
    """
    return await _tool_mind_predict(
        mind_block_id=mind_block_id,
        prediction=prediction,
        verify_at=verify_at,
        reasoning=reasoning,
    )


@mcp.tool()
async def elfmem_mind_list() -> list[dict[str, Any]]:
    """List all active mind blocks with prediction statistics.

    USE WHEN: discovering which minds are modelled and their calibration.
    Returns: list of mind summaries — subject, confidence, prediction counts,
    hit/miss ratios. Cost: fast (DB reads only).
    """
    return await _tool_mind_list()


@mcp.tool()
async def elfmem_mind_show(mind_block_id: str) -> dict[str, Any]:
    """Show a mind block with all linked predictions and outcomes.

    USE WHEN: inspecting a specific mind model before reasoning about it,
    or before running a simulate-frame retrieval grounded in this subject.
    Returns: full content + predictions + closed outcomes. Cost: fast.
    """
    return await _tool_mind_show(mind_block_id)


@mcp.tool()
async def elfmem_mind_outcome(
    decision_block_id: str,
    hit: bool,
    reason: str,
) -> dict[str, Any]:
    """Close a prediction: record hit/miss and calibrate the mind model.

    USE WHEN: a prediction has resolved — the verify_at date has passed and
    you can observe whether it was correct. Updates confidence on both the
    decision block and the linked mind block via Bayesian calibration.
    DON'T USE WHEN: the prediction hasn't resolved yet — wait for evidence.

    Returns: MindOutcomeResult with confidence deltas.
    """
    return await _tool_mind_outcome(
        decision_block_id=decision_block_id, hit=hit, reason=reason
    )


@mcp.tool()
async def elfmem_peer_send(
    to_peer: str,
    content: str,
    in_reply_to: str | None = None,
) -> dict[str, Any]:
    """Send a message to a peer elfmem instance.

    Creates a message block in your memory and writes a JSON file
    to the outbox directory. Transport is handled externally.
    """
    return await _tool_peer_send(to_peer, content, in_reply_to=in_reply_to)


@mcp.tool()
async def elfmem_peer_inbox(
    from_peer: str | None = None,
    import_all: bool = False,
) -> dict[str, Any]:
    """Check for and import pending messages from peers.

    Scans the inbox directory. With import_all=True, imports messages
    into your memory as inbox blocks.
    """
    return await _tool_peer_inbox(from_peer=from_peer, import_all=import_all)


@mcp.tool()
async def elfmem_peer_inbox_status() -> dict[str, Any]:
    """Check for unprocessed peer messages without importing them.

    Returns pending count, sender DIDs, and timestamps. Use to decide
    whether to trigger message processing or call elfmem_peer_inbox.
    """
    return _tool_peer_inbox_status()


@mcp.tool()
async def elfmem_peer_list() -> list[dict[str, Any]]:
    """List all registered peers with trust scores and statistics."""
    return await _tool_peer_list()


@mcp.tool()
async def elfmem_export(
    share_level: str = "public",
    min_confidence: float = 0.0,
    output_path: str = "export.json",
) -> dict[str, Any]:
    """Export shareable blocks as a JSON bundle for another instance.

    share_level: 'public' | 'peer' | 'all'. Blocks tagged self/* are never exported.
    """
    return await _tool_export(
        share_level=share_level,
        min_confidence=min_confidence,
        output_path=output_path,
    )


@mcp.tool()
async def elfmem_import(
    path: str,
    from_peer: str | None = None,
    self_merge: bool = False,
) -> dict[str, Any]:
    """Import a block bundle from another elfmem instance.

    Blocks enter the inbox with provenance tracking.
    self_merge=True for same-identity federation (preserves original confidence).
    """
    return await _tool_import(path, from_peer=from_peer, self_merge=self_merge)


@mcp.tool()
async def elfmem_guide(method: str | None = None) -> str:
    """Detailed documentation for a specific operation, or the full overview.

    method: e.g. "remember", "recall", "outcome". None returns overview table.
    """
    return await _tool_guide(method)


@mcp.tool()
async def elfmem_review_constitutional(
    drift_threshold: float | None = None,
    max_proposals: int | None = None,
    min_block_evidence: float | None = None,
    min_age_days: float | None = None,
) -> dict[str, Any]:
    """Surface drifted self/constitutional blocks as proposed amendments.

    USE WHEN: Periodic self-examination — check whether the agent's tagged
    self/constitutional blocks still match its empirical operational
    identity (the centroid of recently-reinforced ordinary blocks). MANUAL
    cycle: this only PROPOSES; nothing is applied until elfmem_accept_amendment
    is called for a specific proposal. Safe to run any time.

    Cold-start safe: with insufficient operational history the response
    carries insufficient_history=True and no LLM calls are made.

    drift_threshold: Override the configured threshold (default 0.35; half-
        rescaled cosine, so 0.35 ≈ ~52 degrees off the operational centroid).
    max_proposals: Cap on returned proposals; defaults to ReviewConfig.max_proposals.
    min_block_evidence: Beta posterior α+β floor below which a block is too
        cold to be amended (default 2.0).
    min_age_days: Minimum wall-clock age before a block can be amended
        (default 30; protects fresh constitutional seeding).

    RETURNS: dict with keys ``proposals``, ``reviewed_count``,
        ``skipped_count``, ``insufficient_history``, ``failed_proposal_count``.
    NEXT: For each proposal worth applying, call elfmem_accept_amendment with
        block_id + proposed_content (+ optional rationale + drift_score).
    """
    return await _tool_review_constitutional(
        drift_threshold=drift_threshold,
        max_proposals=max_proposals,
        min_block_evidence=min_block_evidence,
        min_age_days=min_age_days,
    )


@mcp.tool()
async def elfmem_review_corpus() -> dict[str, Any]:
    """Run a corpus-level review cycle: deterministic staleness detection.

    USE WHEN: Periodically, to find ordinary blocks that have quietly
    stopped earning their place — long-unused, rarely reinforced, never
    confirmed by an outcome. Zero LLM calls. This only PROPOSES; nothing is
    applied until elfmem_forget is called per accepted proposal. Distinct
    from elfmem_review_constitutional, which checks self/constitutional
    blocks for drift, not ordinary memory for staleness.

    RETURNS: dict with keys ``reviewed_count`` and ``proposals`` (each with
        block_id, kind='stale', reason, content_preview).
    NEXT: For each proposal worth applying, call elfmem_forget(block_id).
    """
    return await _tool_review_corpus()


@mcp.tool()
async def elfmem_accept_amendment(
    block_id: str,
    proposed_content: str,
    rationale: str | None = None,
    drift_score: float | None = None,
) -> dict[str, Any]:
    """Apply a proposed amendment to a constitutional block.

    USE WHEN: A ProposedAmendment from elfmem_review_constitutional has
    been reviewed and the agent has decided to commit it. Writes the
    new content, captures a pre/post audit row, queues the block for
    rescore (summary and last_scored_at are cleared so the next dream
    pass regenerates them). The Beta sufficient statistics (α, β),
    reinforcement_count, and last_reinforced_at are intentionally
    preserved — content edits are not knowledge-confirmation events.

    Acceptor is recorded as "agent" (MCP path). For human-in-the-loop
    accepts use the elfmem CLI instead (acceptor="user").

    drift_score: If supplied (usually from a recent review proposal) it
        is stored verbatim. Pass None to recompute against the current
        operational centroid — one extra read query, no extra LLM.

    RETURNS: dict with ``amendment_id``, ``block_id``, ``pre_content``,
        ``post_content``, ``timestamp``, ``acceptor``. On error: an
        envelope ``{"error": ..., "recovery": ...}`` derived from the
        underlying ElfmemError.
    NEXT: Optionally call elfmem_dream(rescore=True) to refresh
        alignment/tags against the new content immediately.
    """
    return await _tool_accept_amendment(
        block_id=block_id,
        proposed_content=proposed_content,
        rationale=rationale,
        drift_score=drift_score,
    )


@mcp.tool()
async def elfmem_revert_amendment(amendment_id: int) -> dict[str, Any]:
    """Undo one amendment: restore block to its immediate prior content.

    USE WHEN: An amendment was a mistake and the agent wants the block
    back at its immediately-prior content. Revert is one-step: walks
    back exactly one amendment. To revert further, call repeatedly on
    older amendment ids.

    The audit row gains a non-null reverted_at rather than being deleted —
    history is preserved.

    RETURNS: dict with ``amendment_id``, ``block_id``, ``pre_content``,
        ``post_content`` (the restored content), ``timestamp``,
        ``acceptor`` ("system" for revert events). On error: an envelope
        ``{"error": ..., "recovery": ...}`` — most commonly when the
        amendment was already reverted (recovery: pick a different id).
    NEXT: The block is queued for rescore (last_scored_at cleared);
        dream(rescore=True) refreshes it.
    """
    return await _tool_revert_amendment(amendment_id)


@mcp.tool()
async def elfmem_list_amendments(
    block_id: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """List amendment history, newest first. Optionally filter by block.

    USE WHEN: Inspecting the audit trail before deciding to revert, or
    surfacing the constitutional change log to the agent's working
    context. Always available — does not raise for an unknown block_id
    (absence is information: returns an empty list).

    block_id: If given, only that block's history is returned.
    limit: Maximum rows (default 100).

    RETURNS: dict ``{"amendments": [record_dict, ...]}`` in timestamp
        DESC order. Each record carries ``id``, ``block_id``,
        ``timestamp``, ``pre_content``, ``post_content``,
        ``pre_summary``, ``post_summary``, ``drift_score``,
        ``rationale``, ``acceptor``, and ``reverted_at`` (null when the
        amendment is still active).
    NEXT: Pick an id and call elfmem_revert_amendment if needed.
    """
    return await _tool_list_amendments(block_id=block_id, limit=limit)


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

    from elfmem.project import _CONFIG_ENV_NAMES, _DB_ENV_NAMES, _read_env

    # Pass startup hints to main(); _lifespan does the real resolution.
    # _read_env handles deprecated aliases (ELFMEM_CONFIG_PATH, ELFMEM_DB_PATH)
    # with a one-time stderr warning, and refuses if both forms are set with
    # conflicting values.
    raw_config, _ = _read_env(_CONFIG_ENV_NAMES)
    config_path = os.path.expanduser(raw_config) if raw_config else None

    raw_db, _ = _read_env(_DB_ENV_NAMES)
    db_path = os.path.expanduser(raw_db) if raw_db else ""
    adaptive = os.getenv("ELFMEM_ADAPTIVE_POLICY", "").lower() in ("1", "true", "yes")

    try:
        main(db_path, config_path, use_adaptive_policy=adaptive)
    except KeyboardInterrupt:
        sys.exit(0)
