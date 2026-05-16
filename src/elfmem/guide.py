"""Agent-friendly documentation for elfmem operations.

This module provides structured, runtime-accessible documentation that helps
LLM agents understand when and how to use each elfmem operation — without
needing to consult external docs.

Usage::

    system.guide()           # overview of all operations
    system.guide("learn")    # detailed guide for learn()
    system.guide("unknown")  # returns list of valid method names
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AgentGuide:
    """Structured documentation for a single elfmem operation.

    All fields are plain strings optimised for LLM context consumption.
    ``str(guide)`` renders a compact multi-line reference card.
    """

    name: str
    what: str       # One sentence: what does this do?
    when: str       # Decision criteria: when should the agent call this?
    when_not: str   # Anti-patterns: when is this the wrong choice?
    cost: str       # Latency/cost signal: "Instant" | "Fast" | "LLM call"
    returns: str    # What comes back and what the values mean
    next: str       # Typical follow-up action
    example: str    # Minimal working code snippet

    def __str__(self) -> str:
        example_indented = "\n".join(f"    {line}" for line in self.example.splitlines())
        return (
            f"elfmem.{self.name}()\n"
            f"  What:       {self.what}\n"
            f"  Use when:   {self.when}\n"
            f"  Don't use:  {self.when_not}\n"
            f"  Cost:       {self.cost}\n"
            f"  Returns:    {self.returns}\n"
            f"  Next:       {self.next}\n"
            f"  Example:\n"
            f"{example_indented}"
        )


# ── Static guide data ─────────────────────────────────────────────────────────

GUIDES: dict[str, AgentGuide] = {
    "remember": AgentGuide(
        name="remember",
        what="Store knowledge and auto-start a session. Agent-friendly variant of learn().",
        when=(
            "Building always-on agents, MCP tools, or any context where you don't want "
            "to manage session lifecycle explicitly. Prefer this over learn() for agent code."
        ),
        when_not=(
            "You're using the session() context manager — either works, but session() is "
            "cleaner for scripted use. Never call in a tight loop; one call per new observation."
        ),
        cost="Instant. No LLM calls. Auto-starts session if none active (idempotent).",
        returns=(
            "LearnResult. Same status values as learn(): "
            "'created' — new block stored; "
            "'duplicate_rejected' — exact content already exists; "
            "'near_duplicate_superseded' — similar block replaced. "
            "Check system.should_dream after this call."
        ),
        next=(
            "After calling remember(), check system.should_dream. "
            "When True, call dream() at the next natural pause (not in a tight loop)."
        ),
        example=(
            "result = await system.remember('EUR/USD breaks 1.10 resistance')\n"
            "if system.should_dream:\n"
            "    dream_result = await system.dream()\n"
            "    if dream_result:\n"
            "        print(dream_result)  # Consolidated 5: 4 promoted, 8 edges."
        ),
    ),
    "dream": AgentGuide(
        name="dream",
        what="Consolidate pending inbox blocks at a natural pause point.",
        when=(
            "system.should_dream is True — or at any natural pause in agent execution: "
            "end of a reasoning step, waiting for user input, between tasks. "
            "Safe to call speculatively — returns None instantly if nothing is pending."
        ),
        when_not=(
            "In a tight loop. One call processes ALL pending blocks. "
            "Don't call before remember() — blocks need to queue first."
        ),
        cost=(
            "LLM call per pending block (alignment scoring + tag inference). "
            "Returns None immediately (zero cost) if inbox is empty."
        ),
        returns=(
            "ConsolidateResult if blocks were processed — includes processed, promoted, "
            "deduplicated, edges_created counts. "
            "None if inbox was empty. None is not an error."
        ),
        next=(
            "After dream(), newly consolidated blocks are searchable via frame() and recall(). "
            "Frame cache is cleared automatically. "
            "If policy is set, adaptive threshold adjusts based on promotion rate. "
            "Tip: blocks with shared tags form graph edges at lower cosine similarity — "
            "richer tags mean a better-connected knowledge graph."
        ),
        example=(
            "# Always-on agent pattern\n"
            "result = await system.remember('new observation')\n"
            "if system.should_dream:\n"
            "    dream_result = await system.dream()\n"
            "    if dream_result:\n"
            "        print(dream_result)  # Consolidated 10: 9 promoted, 16 edges."
        ),
    ),
    "learn": AgentGuide(
        name="learn",
        what="Store a knowledge block for future retrieval.",
        when=(
            "The agent discovers a fact, preference, decision, or observation "
            "worth remembering across sessions."
        ),
        when_not=(
            "Transient context that only matters in the current turn, or "
            "information already present in the active prompt."
        ),
        cost="Instant. No LLM calls.",
        returns=(
            "LearnResult. status values: "
            "'created' — new block stored in inbox; "
            "'duplicate_rejected' — exact content already exists, no action taken; "
            "'near_duplicate_superseded' — similar existing block replaced."
        ),
        next=(
            "Blocks queue in inbox until consolidate() runs. "
            "The session() context manager auto-consolidates on exit when inbox >= threshold."
        ),
        example=(
            "result = await system.learn('User prefers dark mode')\n"
            "print(result)  # Stored block a1b2c3d4. Status: created."
        ),
    ),
    "learn_document": AgentGuide(
        name="learn_document",
        what="Ingest a document: chunk, learn each chunk, auto-consolidate.",
        when=(
            "The agent needs to ingest a document, article, or long-form text. "
            "Handles chunking, learning, and consolidation in one call."
        ),
        when_not=(
            "Single facts or short observations — use learn() instead. "
            "Already-chunked data — use learn() in a loop."
        ),
        cost=(
            "O(chunks) learn() calls + dream() at inbox_threshold intervals. "
            "With skip_llm=True, dream() uses the fast embedding-only path."
        ),
        returns=(
            "LearnDocumentResult with chunks_total, chunks_created, "
            "chunks_duplicate, consolidations, blocks_promoted."
        ),
        next=(
            "recall() or frame() to query the ingested knowledge. "
            "Consolidation happened automatically during ingestion."
        ),
        example=(
            "result = await system.learn_document(article_text, chunk_size=200)\n"
            "print(result)  # Ingested 12 chunks: 12 created, 2 consolidations."
        ),
    ),
    "consolidate": AgentGuide(
        name="consolidate",
        what="Process inbox blocks: score, embed, deduplicate, and promote to active memory.",
        when=(
            "After a batch of learn() calls, or explicitly before recall/frame "
            "when you know new blocks are in inbox. "
            "The session() context manager handles this automatically on exit."
        ),
        when_not=(
            "Inbox is empty — safe to call but a no-op. "
            "Avoid calling in a tight loop; one call processes all pending blocks."
        ),
        cost=(
            "LLM call per block (alignment scoring + tag inference). "
            "Slow for large inboxes; fast for small ones."
        ),
        returns=(
            "ConsolidateResult with counts: processed (total inbox blocks), "
            "promoted (moved to active), deduplicated (near-duplicates found), "
            "edges_created (knowledge graph edges built)."
        ),
        next=(
            "Promoted blocks are now searchable via frame() and recall(). "
            "Call status() to verify memory state."
        ),
        example=(
            "result = await system.consolidate()\n"
            "print(result)  # Consolidated 5: 4 promoted, 1 deduped, 8 edges."
        ),
    ),
    "frame": AgentGuide(
        name="frame",
        what="Retrieve and render context for a named frame, ready for prompt injection.",
        when=(
            "Assembling context for an LLM prompt. "
            "Use 'self' for identity context, 'attention' for query-relevant knowledge, "
            "'task' for goal/task context."
        ),
        when_not=(
            "You only need raw block data without rendering — use recall() instead. "
            "Avoid calling frame() inside tight generation loops; results are cached."
        ),
        cost="Fast. Embedding call if query provided; no LLM calls.",
        returns=(
            "FrameResult. Use result.text for direct prompt injection. "
            "result.blocks contains the scored ScoredBlock candidates. "
            "result.cached indicates whether this was served from the TTL cache."
        ),
        next=(
            "Inject result.text into your LLM prompt. "
            "Reinforce is a side effect of retrieval — no separate call needed."
        ),
        example=(
            "ctx = await system.frame('attention', query='error handling')\n"
            "prompt = f'{ctx.text}\\nUser: how do I handle errors?'"
        ),
    ),
    "recall": AgentGuide(
        name="recall",
        what="Raw retrieval returning scored blocks without rendering or side effects.",
        when=(
            "Inspecting what is in memory, debugging retrieval quality, "
            "or building custom rendering from scored block data."
        ),
        when_not=(
            "You need context ready for prompt injection — use frame() instead. "
            "frame() renders, respects token budgets, and handles caching."
        ),
        cost="Fast. Embedding call if query provided; no LLM calls.",
        returns=(
            "list[ScoredBlock] sorted by composite score descending. "
            "Empty list if nothing found — never raises for empty results."
        ),
        next="No side effects. Safe to call multiple times with the same query.",
        example=(
            "blocks = await system.recall('error handling', top_k=3)\n"
            "for b in blocks:\n"
            "    print(b)  # [0.87] User prefers explicit error handling..."
        ),
    ),
    "curate": AgentGuide(
        name="curate",
        what="Maintenance: archive decayed blocks, prune weak edges, reinforce top knowledge.",
        when=(
            "Explicit maintenance after heavy use, or when retrieval quality degrades. "
            "Also runs automatically when curate_interval_hours elapses after consolidate()."
        ),
        when_not=(
            "Immediately after consolidate() — auto-curate already triggers if interval elapsed. "
            "Don't call in response to every session; it's a periodic operation."
        ),
        cost="Fast. Database operations only; no LLM calls.",
        returns=(
            "CurateResult with counts: archived (decayed blocks removed from active), "
            "edges_pruned (weak graph edges removed), "
            "reinforced (top-N blocks had reinforcement boosted)."
        ),
        next=(
            "Memory is now cleaner. "
            "Retrieval quality may improve as stale blocks are gone."
        ),
        example=(
            "result = await system.curate()\n"
            "print(result)  # Curated: 2 archived, 1 edges pruned, 5 reinforced."
        ),
    ),
    "status": AgentGuide(
        name="status",
        what="Return a snapshot of system state with a suggested next action.",
        when=(
            "Deciding whether to consolidate, curate, or start a session. "
            "Checking memory health before a long agent run. "
            "Verifying state after operations."
        ),
        when_not="(Always safe to call. No side effects.)",
        cost="Fast. One database read; no LLM calls.",
        returns=(
            "SystemStatus with: session_active, inbox_count/inbox_threshold, "
            "active_count, archived_count, health ('good'|'attention'), suggestion, "
            "session_tokens (TokenUsage — LLM + embedding calls this session), "
            "lifetime_tokens (TokenUsage — all-time total, persisted across restarts). "
            "Use result.suggestion for the recommended next action. "
            "Use str(result.session_tokens) for a compact token cost line."
        ),
        next="Follow result.suggestion for the recommended action.",
        example=(
            "s = await system.status()\n"
            "print(s)  # Session: active (0.5h) | Inbox: 8/10 | Active: 42 | Health: good\n"
            "          # Tokens this session: LLM: 4,820 tokens (9 calls)"
            " | Embed: 1,230 tokens (14 calls)\n"
            "          # Suggestion: Inbox nearly full. Consolidation approaching.\n"
            "if s.health == 'attention':\n"
            "    await system.consolidate()"
        ),
    ),
    "history": AgentGuide(
        name="history",
        what="Return recent operations performed by this MemorySystem in the current process.",
        when=(
            "Debugging unexpected results — e.g., recall returns nothing and you "
            "want to verify consolidate() actually ran."
        ),
        when_not=(
            "Persistent audit logging is needed — history is in-memory only "
            "and resets when the process restarts."
        ),
        cost="Instant. In-memory only; no database access.",
        returns=(
            "list[OperationRecord] with fields: operation (method name), "
            "summary (str(result) at call time), timestamp (ISO UTC). "
            "Most recent last. Empty list if no operations have run."
        ),
        next="(Informational only. No action required.)",
        example=(
            "for record in system.history(last_n=5):\n"
            "    print(record)  # learn()  →  Stored block a1b2.  [14:32:01]"
        ),
    ),
    "outcome": AgentGuide(
        name="outcome",
        what="Update block confidence using a normalised domain signal via Bayesian update.",
        when=(
            "After an observable result can be scored: a forecast resolves, tests pass/fail, "
            "content engagement is measured, or a CSAT score arrives. "
            "Works without an active session — outcomes may arrive weeks after retrieval."
        ),
        when_not=(
            "To reinforce recently-used blocks — that happens automatically via frame(). "
            "Don't call outcome() for transient observations; only for measurable results "
            "that reflect whether retrieved knowledge was actually correct or useful."
        ),
        cost="Fast. Database operations only; no LLM calls.",
        returns=(
            "OutcomeResult with: blocks_updated (active blocks whose confidence changed), "
            "mean_confidence_delta (average confidence shift, positive or negative), "
            "edges_reinforced (graph edges strengthened for positive signals), "
            "blocks_penalized (blocks whose decay was accelerated for low signals). "
            "blocks_updated=0 means all block_ids were non-active (silently skipped)."
        ),
        next=(
            "Signal spectrum (default thresholds): "
            "0.8–1.0 → confidence UP + reinforce (decay resets). "
            "0.2–0.8 → confidence adjusted only (neutral dead-band). "
            "0.0–0.2 → confidence DOWN + decay accelerated automatically"
            " (no separate call needed). "
            "Over ~10 outcomes, evidence dominates the LLM alignment prior. "
            "DURABLE and PERMANENT blocks are never penalized."
        ),
        example=(
            "# Trading: Brier score resolved after 30 days\n"
            "signal = 1.0 - brier_score  # 0.85 = good forecast\n"
            "result = await system.outcome(block_ids, signal=signal, source='brier')\n"
            "print(result)  # Outcome recorded: 3 blocks updated"
            " (+0.042 avg confidence), 2 edges reinforced.\n"
            "\n"
            "# Coding: test suite pass/fail\n"
            "signal = 1.0 if all_tests_passed else 0.0\n"
            "result = await system.outcome(block_ids, signal=signal, source='test_suite')\n"
            "\n"
            "# Writing: engagement rate vs baseline\n"
            "signal = min(engagement_rate / baseline, 1.0)\n"
            "result = await system.outcome(block_ids, signal=signal, source='engagement')\n"
            "\n"
            "# Support: CSAT score 1–5\n"
            "signal = (csat_score - 1.0) / 4.0\n"
            "result = await system.outcome(block_ids, signal=signal, source='csat')"
        ),
    ),
    "setup": AgentGuide(
        name="setup",
        what=(
            "Bootstrap the cognitive loop: seeds 10 constitutional blocks, then adds optional "
            "identity description and domain values to the SELF frame."
        ),
        when=(
            "First use — before any other operations. Also when the agent's role, values, or "
            "constraints change significantly. Constitutional blocks ship with every instance."
        ),
        when_not=(
            "Every session — SELF blocks persist across restarts. Duplicates are rejected "
            "automatically so re-running is safe but unnecessary. Don't call on every turn."
        ),
        cost=(
            "Fast per block. Each block queues in inbox; one LLM call per block during "
            "consolidate() (auto on session close)."
        ),
        returns=(
            "dict with status='setup_complete', blocks_created (int), and blocks (list of "
            "LearnResult dicts). blocks_created=0 means all were exact duplicates — safe. "
            "Constitutional blocks are tagged self/constitutional"
            " (PERMANENT decay, ~34yr half-life)."
        ),
        next=(
            "SELF blocks sit in inbox until consolidate() runs (auto on session close). "
            "After consolidation, recall(frame='self') always includes constitutional blocks "
            "(guaranteed slots) plus any domain values you added. "
            "Check status with elfmem_status() or 'elfmem doctor' CLI. "
            "Three tiers: constitutional (PERMANENT) → values (DURABLE, ~29d)"
            " → context (STANDARD, ~3d)."
        ),
        example=(
            "# Minimal: seeds 10 constitutional blocks only\n"
            "elfmem_setup()\n"
            "\n"
            "# With identity: constitutional + custom identity block\n"
            "elfmem_setup(\n"
            "    identity='I am a trading assistant focused on risk-adjusted returns.',\n"
            "    values=['cut losing positions early', 'size positions to max 2% risk']\n"
            ")\n"
            "\n"
            "# Skip constitutional seeding (advanced: manual control)\n"
            "elfmem_setup(seed=False, identity='Custom agent without default seed')"
        ),
    ),
    "connect": AgentGuide(
        name="connect",
        what="Create or strengthen a semantic edge between two knowledge blocks.",
        when=(
            "The agent observes a relationship between two recalled blocks that the system "
            "has not captured, or has captured with the wrong semantic type. "
            "Best called immediately after recall() or learn() when block IDs are available."
        ),
        when_not=(
            "You don't have block IDs — use connect_by_query() instead. "
            "Don't connect blocks the agent hasn't read; unverified connections add noise. "
            "Don't call for blocks that will decay soon — weak connections fade on their own."
        ),
        cost="Instant. No LLM calls. Pure database write.",
        returns=(
            "ConnectResult. action: 'created' (new edge), 'reinforced' (existing edge boosted), "
            "'updated' (relation/note changed), 'skipped' (edge exists, if_exists=skip). "
            "If a lower-priority auto-edge was displaced, displaced_edge is set in result."
        ),
        next=(
            "No follow-up required. To undo, call disconnect(). "
            "Block IDs are in system.last_recall_block_ids and system.last_learned_block_id."
        ),
        example=(
            "# After recall — agent notices an unlabelled relationship\n"
            "results = await system.recall('frame selection heuristics')\n"
            "await system.connect(\n"
            "    source=results[0].id,\n"
            "    target=results[1].id,\n"
            "    relation='supports',\n"
            "    note='B gives the mechanism behind A'\n"
            ")\n"
            "# Using breadcrumb shortcut\n"
            "await system.learn('New insight about X')\n"
            "await system.recall('related concept Y')\n"
            "await system.connect(\n"
            "    source=system.last_learned_block_id,\n"
            "    target=system.last_recall_block_ids[0],\n"
            "    relation='elaborates'\n"
            ")"
        ),
    ),
    "disconnect": AgentGuide(
        name="disconnect",
        what="Remove the edge between two knowledge blocks.",
        when=(
            "An agent-created edge was incorrect and should not persist. "
            "Also use to override automatic edges that cause retrieval noise "
            "(e.g., two blocks that are textually similar but contextually unrelated)."
        ),
        when_not=(
            "The edge is correct but weak — decay and pruning remove it naturally over time. "
            "Only use disconnect() for deliberate correction of wrong connections."
        ),
        cost="Instant. No LLM calls.",
        returns=(
            "DisconnectResult. action: 'removed' (edge deleted), "
            "'not_found' (no edge exists between the pair), "
            "'guarded' (edge exists but relation did not match guard_relation)."
        ),
        next="No follow-up required. Edge is immediately gone from graph expansion.",
        example=(
            "# Remove a wrong connection\n"
            "result = await system.disconnect(source_id, target_id)\n"
            "print(result)  # Removed similar edge: abc12345…→def67890… (was weight=0.63).\n"
            "\n"
            "# Safe removal with guard (only remove if it's a 'similar' auto-edge)\n"
            "result = await system.disconnect(\n"
            "    source_id, target_id,\n"
            "    guard_relation='similar'\n"
            ")\n"
            "# → 'guarded' if the edge is actually 'supports' (won't remove)"
        ),
    ),
    "mind_create": AgentGuide(
        name="mind_create",
        what="Create a Theory of Mind block modelling another agent's goals, beliefs, fears.",
        when=(
            "You need to make predictions about what another agent or person will do. "
            "Start by modelling their mind — goals, beliefs, fears, motivations."
        ),
        when_not=(
            "Storing general facts about someone — use learn(). "
            "Mind blocks are structured models for falsifiable predictions."
        ),
        cost="Instant. No LLM calls. Block queued in inbox.",
        returns=(
            "LearnResult with block_id. Category is 'mind', decay tier is DURABLE "
            "(~6 month half-life). Tagged mind/<subject-slug>."
        ),
        next=(
            "Add predictions with mind_predict(). Retrieve with frame('simulate') "
            "to inhabit the perspective and reason about the modelled mind."
        ),
        example=(
            "result = await system.mind_create(\n"
            "    'customer-archetype',\n"
            "    goals=['Ship fast without learning infra'],\n"
            "    beliefs=['Agent-ready code is a moat'],\n"
            "    fears=['Complex setup causes abandonment'],\n"
            ")"
        ),
    ),
    "mind_predict": AgentGuide(
        name="mind_predict",
        what="Add a falsifiable prediction linked to a mind block.",
        when=(
            "You have a specific, testable hypothesis about what the modelled mind will do. "
            "Predictions must have a verify_at date. No prior consolidation needed."
        ),
        when_not=(
            "The claim is unfalsifiable or has no verification date. "
            "Casual observations go in learn()."
        ),
        cost="Instant. Promotes mind block if needed, creates decision block + predicts edge.",
        returns="MindPredictResult with decision_block_id and edge action.",
        next="When the prediction resolves, call mind_outcome() with the decision_block_id.",
        example=(
            "result = await system.mind_predict(\n"
            "    mind_block_id,\n"
            "    'Will pay 49/mo for hosted version',\n"
            "    verify_at='2026-06-30',\n"
            "    reasoning='Prefers predictable cost over setup friction',\n"
            ")"
        ),
    ),
    "mind_outcome": AgentGuide(
        name="mind_outcome",
        what="Close a prediction: record hit/miss, calibrate the mind model.",
        when=(
            "A prediction has resolved — the verify_at date passed and you have evidence. "
            "No prior consolidation needed."
        ),
        when_not="The prediction hasn't resolved yet. Wait for observable evidence.",
        cost="Fast. Promotes decision block if needed, then updates confidence via Bayesian model.",
        returns=(
            "MindOutcomeResult with confidence deltas for both mind and decision blocks. "
            "Hit: confidence up + reinforce. Miss: confidence down + decay."
        ),
        next=(
            "The mind model's confidence is now calibrated. Future simulate frame "
            "retrievals reflect the updated model accuracy."
        ),
        example=(
            "# Prediction hit\n"
            "result = await system.mind_outcome(\n"
            "    decision_block_id,\n"
            "    hit=True,\n"
            "    reason='Signed up week 1 at tier price',\n"
            ")\n"
            "# Prediction miss\n"
            "result = await system.mind_outcome(\n"
            "    decision_block_id,\n"
            "    hit=False,\n"
            "    reason='Requested full bespoke integration',\n"
            ")"
        ),
    ),
    "guide": AgentGuide(
        name="guide",
        what="Return agent-friendly documentation for a specific method or all methods.",
        when=(
            "Discovering what operations are available, or understanding the correct "
            "usage of a specific method before calling it."
        ),
        when_not="(Always safe to call. No side effects.)",
        cost="Instant. No database access.",
        returns=(
            "str. With no argument: compact overview table. "
            "With a method name: full AgentGuide for that method. "
            "With unknown name: list of valid method names."
        ),
        next="(Informational only.)",
        example=(
            "print(system.guide())           # full overview\n"
            "print(system.guide('learn'))    # detailed guide for learn()\n"
            "print(system.guide('unknown'))  # lists valid method names"
        ),
    ),
    # ── Peer communication ────────────────────────────────────────────────────
    "peer_init": AgentGuide(
        name="peer_init",
        what="Set this instance's identity (DID) in the database. Call once before any peer ops.",
        when="First time you want to use peer communication. Safe to re-run — idempotent.",
        when_not="Identity is already set — check with peer_list() or elfmem peer list.",
        cost="Instant. Database write only.",
        returns="str — the assigned identity DID (e.g. 'elf:research-elf').",
        next="Call peer_add() to register peers to communicate with.",
        example=(
            "did = await system.peer_init('research-elf')\n"
            "print(did)  # elf:research-elf"
        ),
    ),
    "peer_add": AgentGuide(
        name="peer_add",
        what="Register a peer for message exchange. Optionally set a direct delivery path.",
        when=(
            "Adding a known peer before sending messages or importing their bundles. "
            "With delivery_path: the peer's inbox directory is on a shared/local filesystem "
            "(Dropbox, NFS, same machine) — messages are written directly without transport."
        ),
        when_not=(
            "You don't know the peer's DID yet — get it from them first. "
            "Don't add a peer just to import a one-off bundle; from_peer is optional on import."
        ),
        cost="Instant. Database write only.",
        returns="PeerInfo with did, name, trust, is_self, delivery_path, message counts.",
        next="Send a message with peer_send(), or exchange bundles with export_blocks().",
        example=(
            "# Standard peer (outbox-mediated)\n"
            "peer = await system.peer_add('elf:trader', 'Trading Elf')\n"
            "\n"
            "# Direct delivery (shared filesystem)\n"
            "peer = await system.peer_add(\n"
            "    'elf:vault', 'Vault Elf',\n"
            "    delivery_path='/shared/vault/.elfmem/inbox',\n"
            ")\n"
            "\n"
            "# Self-federation (same identity on another machine)\n"
            "peer = await system.peer_add('elf:laptop', 'Laptop', is_self=True)"
        ),
    ),
    "peer_send": AgentGuide(
        name="peer_send",
        what=(
            "Send a message to a registered peer. Heartbeat speed — no LLM calls. "
            "Writes a JSON file to the peer's inbox (direct) or your outbox (mediated)."
        ),
        when=(
            "Sending a question, observation, or reply to another elfmem instance. "
            "The peer must exist in the roster (peer_add first)."
        ),
        when_not=(
            "Sharing bulk knowledge — use export_blocks() instead. "
            "Broadcasting to many peers — send individually; each peer has its own trust."
        ),
        cost="Instant. No LLM calls. Pure file write.",
        returns=(
            "PeerSendResult with msg_id, to_peer, delivery_path. "
            "delivery_path shows where the message file was written."
        ),
        next=(
            "The peer picks it up with peer_inbox(). "
            "Message blocks are stored locally in your memory too (category='message')."
        ),
        example=(
            "result = await system.peer_send('elf:trader', 'What is your gilt view?')\n"
            "print(result)  # Sent m_a1b2c3d4 to elf:trader\n"
            "\n"
            "# Reply to an existing message\n"
            "result = await system.peer_send(\n"
            "    'elf:trader', 'I agree with your analysis',\n"
            "    in_reply_to='m_e5f6g7h8',\n"
            ")"
        ),
    ),
    "peer_inbox": AgentGuide(
        name="peer_inbox",
        what="Scan the inbox directory for pending messages from peers.",
        when=(
            "Checking for incoming messages from registered peers. "
            "With import_all=True, messages are imported into your memory immediately."
        ),
        when_not=(
            "You want to import bulk knowledge blocks — use import_blocks() instead. "
            "Messages are events (not claims) so they skip dedup/contradiction detection."
        ),
        cost="Instant. File scan + optional database writes. No LLM calls.",
        returns=(
            "PeerInboxResult with messages_found, messages_imported, "
            "messages_skipped, peers (list of sender DIDs)."
        ),
        next=(
            "Imported message blocks enter your active memory. "
            "Use frame('attention') or recall() to query them."
        ),
        example=(
            "# Check what's waiting\n"
            "inbox = await system.peer_inbox()\n"
            "print(inbox)  # Found 3 messages from 2 peer(s). Imported 0, skipped 0.\n"
            "\n"
            "# Import all pending messages\n"
            "inbox = await system.peer_inbox(import_all=True)\n"
            "# Filter by sender\n"
            "inbox = await system.peer_inbox(from_peer='elf:trader', import_all=True)"
        ),
    ),
    "peer_inbox_status": AgentGuide(
        name="peer_inbox_status",
        what="Check whether peer messages are waiting without importing them.",
        when=(
            "Deciding whether to trigger a peer message processing session. "
            "Use in polling loops or RemoteTrigger prompts to gate on inbox state."
        ),
        when_not=(
            "You need message content — use peer_inbox() or frame(frame='task') instead. "
            "Don't use this to import messages — pass import_all=True to peer_inbox()."
        ),
        cost="Instant. Pure filesystem scan. No LLM calls. No database access.",
        returns=(
            "PeerInboxStatus with pending (int), from_peers (list of sender DIDs), "
            "oldest_at / newest_at (ISO timestamps), inbox_dir (path scanned)."
        ),
        next=(
            "If pending > 0: call peer_inbox(import_all=True) to ingest, then dream(). "
            "Or fire a SELF-grounded processing prompt."
        ),
        example=(
            "status = system.peer_inbox_status()\n"
            "if status.pending > 0:\n"
            "    inbox = await system.peer_inbox(import_all=True)\n"
            "    if system.should_dream:\n"
            "        await system.dream()"
        ),
    ),
    "export_blocks": AgentGuide(
        name="export_blocks",
        what="Export shareable knowledge blocks to a JSON bundle file.",
        when=(
            "Sharing knowledge with another elfmem instance. "
            "Only exports blocks tagged share='public' or share='shared'."
        ),
        when_not=(
            "Sending a real-time message — use peer_send() instead. "
            "Self/constitutional blocks (self/ tags) are never exported regardless of share."
        ),
        cost="Fast. Database read + file write. No LLM calls.",
        returns=(
            "ExportResult with blocks_exported, edges_exported, output_path. "
            "The bundle file is a self-contained JSON the receiving instance can import."
        ),
        next="Transfer the bundle file and have the peer call import_blocks().",
        example=(
            "# Mark blocks as shareable first (learn with share='public')\n"
            "result = await system.export_blocks(\n"
            "    share_level='public',\n"
            "    output_path='knowledge.json',\n"
            "    min_confidence=0.5,\n"
            ")\n"
            "print(result)  # Exported 18 blocks, 12 edges → knowledge.json"
        ),
    ),
    "import_blocks": AgentGuide(
        name="import_blocks",
        what="Import a knowledge bundle from another elfmem instance into your inbox.",
        when=(
            "Receiving a bundle exported by a peer. Blocks enter inbox and go through "
            "your normal consolidation pipeline. Trust modulates confidence on import."
        ),
        when_not=(
            "You want to receive messages — use peer_inbox() instead. "
            "Don't import your own exports — use is_self_merge=True for self-federation."
        ),
        cost="Fast. File read + database writes. No LLM calls at import time.",
        returns=(
            "ImportResult with blocks_imported, blocks_skipped (below confidence floor), "
            "edges_imported, from_peer."
        ),
        next=(
            "Imported blocks sit in inbox. Call dream() to consolidate them — "
            "this is where dedup and contradiction detection run against your existing knowledge."
        ),
        example=(
            "# From a known peer (uses their trust for confidence scaling)\n"
            "result = await system.import_blocks('knowledge.json', from_peer='elf:trader')\n"
            "print(result)  # Imported 15 blocks (3 skipped), 9 edges from elf:trader\n"
            "\n"
            "# Self-federation (same identity, different machine — trust=1.0)\n"
            "result = await system.import_blocks('laptop_sync.json', is_self_merge=True)\n"
            "\n"
            "# After importing, consolidate to integrate with existing knowledge\n"
            "await system.dream()"
        ),
    ),
    "peer_list": AgentGuide(
        name="peer_list",
        what="List all registered peers with trust scores and message counts.",
        when="Checking who you've registered, their trust levels, and activity.",
        when_not="(Always safe. No side effects.)",
        cost="Instant. Single database read.",
        returns=(
            "list[PeerInfo]. Each has: did, name, trust (0.0–1.0), is_self, "
            "delivery_path, messages_in, messages_out, blocks_imported, blocks_exported."
        ),
        next="peer_trust() to update trust, peer_send() to send a message.",
        example=(
            "peers = await system.peer_list()\n"
            "for p in peers:\n"
            "    print(p)  # Trading Elf (elf:trader) [trust=0.72] — 3↓ 5↑"
        ),
    ),
    "peer_trust": AgentGuide(
        name="peer_trust",
        what="View or manually set trust for a peer (0.0–1.0).",
        when=(
            "Bootstrapping trust for a new peer before outcomes have accumulated, "
            "or correcting trust after an incident. Trust normally evolves via outcome()."
        ),
        when_not=(
            "Trust will calibrate naturally — let outcome() handle it unless you need "
            "to bootstrap or override. Don't set trust=1.0 on external peers; reserve "
            "that for is_self peers."
        ),
        cost="Instant. Database write only.",
        returns="PeerInfo reflecting the updated trust value.",
        next=(
            "Trust affects confidence scaling on import_blocks(). "
            "Use outcome() on peer-sourced blocks to let trust self-calibrate over time."
        ),
        example=(
            "# View current trust\n"
            "peer = await system.peer_trust('elf:trader')\n"
            "print(peer)  # Trading Elf (elf:trader) [trust=0.50]\n"
            "\n"
            "# Set trust manually\n"
            "peer = await system.peer_trust('elf:trader', set_value=0.8)"
        ),
    ),
}

# ── Overview ──────────────────────────────────────────────────────────────────

OVERVIEW: str = "\n".join([
    "elfmem — adaptive memory for LLM agents",
    "Call system.guide('name') for detailed help on any operation.",
    "",
    "  Operation              Cost         Description",
    "  ─────────────────────────────────────────────────────────────────────",
    "  setup(identity, ...)   Fast         Seed SELF frame with agent identity (first use)",
    "  remember(content, ...) Instant      Store knowledge + auto-start session (agent-friendly)",
    "  learn(content, ...)    Instant      Store knowledge for later retrieval (explicit sessions)",
    "  dream()                LLM call     Consolidate pending blocks at a natural pause",
    "  recall(query, ...)     Fast         Raw retrieval — list of scored blocks",
    "  frame(name, ...)       Fast         Retrieve + render a named context frame",
    "  consolidate()          LLM call     Process inbox: score, embed, promote (explicit)",
    "  outcome(ids, signal)   Fast         Bayesian confidence update from domain result",
    "  connect(src, tgt, ...) Instant      Assert a semantic edge between two blocks",
    "  disconnect(src, tgt)   Instant      Remove a wrong or unwanted edge",
    "  curate()               Fast         Archive stale blocks, prune weak edges",
    "  rescore(max_count?)    LLM call     Deep-sleep: re-evaluate aged blocks vs SELF",
    "  mind_create(subj, ...) Instant      Create a Theory of Mind block for a subject",
    "  mind_predict(id, ...)  Instant      Add a falsifiable prediction to a mind block",
    "  mind_outcome(id, ...)  Fast         Close a prediction: hit/miss + calibrate",
    "  mind_list()            Fast         List all mind blocks with prediction stats",
    "  mind_show(id)          Fast         Show a mind block with linked predictions",
    "  status()               Fast         System health snapshot + suggested action",
    "  history(last_n=10)     Instant      Recent operations in this process session",
    "  guide(method?)         Instant      This help",
    "",
    "  ── Peer communication ───────────────────────────────────────────────",
    "  peer_init(name)        Instant      Set this instance's identity DID (once)",
    "  peer_add(did, name)    Instant      Register a peer (+ optional delivery_path)",
    "  peer_send(did, msg)    Instant      Send a message to a peer (file write, no LLM)",
    "  peer_inbox(...)        Instant      Scan inbox for pending messages",
    "  peer_inbox_status()    Instant      Check for unprocessed messages (no import, no DB)",
    "  peer_list()            Instant      List all registered peers with trust scores",
    "  peer_trust(did, ...)   Instant      View or manually set trust for a peer",
    "  peer_remove(did)       Instant      Unregister a peer",
    "  export_blocks(...)     Fast         Export shareable blocks as a JSON bundle",
    "  import_blocks(path)    Fast         Import a bundle from another instance",
    "",
    "Four rhythms:   remember [heartbeat] → dream [breathing] → curate [sleep] → rescore [deep]",
    "Always-on:      remember() → check should_dream → dream() when True",
    "Session-based:  async with system.session(): learn() → frame() → outcome()",
    "Peer exchange:  peer_init() → peer_add() → peer_send() / export_blocks()",
    "Quick start:    elfmem_setup(identity='...') | system.status() | system.guide('remember')",
])


def get_guide(method_name: str | None = None) -> str:
    """Return documentation string for the named method, or the full overview.

    Args:
        method_name: Method to look up, or None for the full overview.

    Returns:
        Formatted string ready for agent consumption.
    """
    if method_name is None:
        return OVERVIEW
    guide_entry = GUIDES.get(method_name)
    if guide_entry is not None:
        return str(guide_entry)
    valid = ", ".join(f"'{m}'" for m in sorted(GUIDES))
    return f"Unknown method '{method_name}'. Valid methods: {valid}."
