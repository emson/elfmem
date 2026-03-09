# `connect()` — Agent Edge API Design Evaluation

**Status:** Design complete — ready for implementation planning
**Date:** 2026-03-09
**Purpose:** Simulate how agents will actually call `connect()`, identify failure modes, edge cases, and friction points, then design the best possible API for this use case.

---

## The Core Tension

Agents think in **concepts**. elfmem operates on **block IDs**.

`connect()` requires block IDs. Getting block IDs requires a prior `recall()`. This mismatch is the single biggest friction point in the API, and every design decision either resolves or compounds it.

Everything in this document flows from that observation.

---

## Scenario Simulations

### Scenario 1 — Post-Recall Connection (the happy path)

Agent just recalled two blocks and notices a relationship the similarity algorithm didn't capture.

```python
# Agent is answering: "Which frame should I use for a values conflict?"
results = await system.recall("which frame for values conflicts", frame="self")

# Gets back:
#   Block A (id="796845bc"): "Use SELF frame when constitutional principles conflict with task goals"
#   Block B (id="42f4ba02"): "Epistemic humility: list assumptions before significant actions"
# Agent notices: B explains WHY the SELF frame is appropriate for values conflicts

result = await system.connect(
    source="796845bc...",
    target="42f4ba02...",
    relation="elaborates",
    note="B explains the epistemic mechanism that SELF frame operationalises"
)
# → "Created elaborates edge: 796845bc…→42f4ba02… (weight=0.70)"
```

**This works well.** Agent has IDs from the recall result. Natural pause point. Clear relationship.

**Friction found:** Block IDs in the recall result must be easily accessible. If `results.blocks[0].id` requires digging into the object, agents will approximate with wrong IDs or skip the call entirely.

**Required:** `recall()` result exposes `.block_ids` as a direct convenience property, and the `__str__` output of RecallResult includes short IDs that agents can read and reference.

---

### Scenario 2 — Post-Learn Connection (new knowledge + existing knowledge)

Agent just learned something new and immediately recognises it relates to an existing block.

```python
# Agent has been thinking about retrieval during a reasoning task
recent = await system.recall("session-aware decay calculation", top_k=1)

# Then learns a new insight
new = await system.learn(
    "Session-aware decay solves the 'holiday problem': time stops during agent downtime, not wall-clock",
    tags=["decay", "session-model"]
)

# Agent wants to link the new insight to the retrieved block
await system.connect(
    source=new.block_id,           # directly from learn result
    target=recent.blocks[0].id,   # from recall result
    relation="elaborates",
    note="New block gives the intuitive explanation; retrieved block has the formula"
)
```

**This works well.** `learn()` returns `block_id` directly. `recall()` returns block objects with IDs. Both IDs are naturally available at the call site.

**Friction found:** The agent needs to hold two results in context simultaneously. If the LLM context is long, the IDs from the earlier `recall()` may have scrolled out of the agent's immediate view. The agent might misremember or hallucinate an ID.

**Mitigation A:** `MemorySystem` exposes `system.last_recall_block_ids` and `system.last_learned_block_id` as properties — a session-scoped breadcrumb. Agent can reference these without needing to save results explicitly.

**Mitigation B:** Invalid IDs produce a `ConnectError` with `.recovery = "Valid block IDs appear in recall() and learn() results. Run system.recall() to find active blocks."` — the agent self-corrects on the next turn.

---

### Scenario 3 — The ID Problem (agent knows what it wants but not the IDs)

Mid-task, the agent has an insight: two concepts it has learned are related. It doesn't have their IDs. It only has the concepts.

```python
# Agent (mid-reasoning): "I just realised that 'frame selection' and 'constitutional blocks'
#                         are deeply linked — the SELF frame IS the constitutional block system"
# But the agent doesn't have IDs at hand.

# BAD approach — agent guesses or fabricates:
await system.connect(source="abc123", target="def456", ...)
# → ConnectError: block not found. Recovery: use recall() first.

# GOOD approach — agent resolves IDs first:
result_a = await system.recall("frame selection heuristics", top_k=1)
result_b = await system.recall("constitutional blocks", top_k=1)

await system.connect(
    source=result_a.blocks[0].id,
    target=result_b.blocks[0].id,
    relation="supports",
    note="SELF frame is the runtime expression of constitutional block constraints"
)
```

**Three-call pattern** is the correct flow: recall → recall → connect. But this is friction — the agent must interrupt its reasoning with two retrieval calls before it can record its insight.

**Alternative — `connect_by_query()`:**

```python
await system.connect_by_query(
    source_query="frame selection heuristics",
    target_query="constitutional blocks identity",
    relation="supports",
    note="SELF frame is the runtime expression of constitutional block constraints",
    min_confidence=0.75  # only connect if recall score is confident enough
)
# Returns ConnectByQueryResult with actual block content for agent verification
# → "Connected supports edge between 'Use SELF frame when values...' and
#    'Constitutional blocks define...'. Verify these are the intended blocks."
```

This collapses three calls to one, but adds a new failure mode: the agent's queries might match the wrong blocks. The result must return full block content so the agent can verify.

**Design decision:** Both approaches should exist. `connect_by_query()` is the ergonomic version; `connect()` with explicit IDs is the precise version. Guide agents toward `connect()` when they have IDs, `connect_by_query()` when they don't.

---

### Scenario 4 — Contradiction Discovery

Agent retrieves two blocks that seem to oppose each other. It needs to mark this explicitly so retrieval doesn't treat them as mutual reinforcement.

```python
results = await system.recall("optimal top_k value for retrieval")
# Gets:
#   Block A: "Use top_k=5 for precise task execution" (TASK frame)
#   Block B: "Use top_k=20 for exploratory attention queries" (ATTENTION frame)
# Both retrieved because they're semantically close — same topic.
# Cosine similarity creates a 'similar' edge between them, which is technically wrong:
# retrieval of A should not automatically surface B, because they apply to different contexts.

await system.connect(
    source=results.blocks[0].id,
    target=results.blocks[1].id,
    relation="contradicts",
    note="A applies to TASK frame (precision), B to ATTENTION frame (exploration). "
         "These are not contradictions but context-partitioned — separate use cases."
)
```

**Problem discovered:** Marking as `contradicts` is semantically wrong here — these blocks don't actually contradict, they partition by context. But the agent can't express "these seem similar but have different domains" through the current type system.

**Missing relation type:** `PARTITIONS` or `CONTEXT_DEPENDENT` — "similar topic, different applicable context."

**Resolution options:**
1. Add `partitions` as a relation type — specific to "same topic, different valid contexts"
2. Use `note` field to explain the partitioning, keep type as `elaborates`
3. Use custom relation types — agent provides `relation="context_partitioned"`, system stores it, but treats it as `similar` for scoring purposes

**Recommendation:** Option 3 — open relation type system. Core types affect retrieval scoring. Custom types (any string) are stored and queryable but scored neutrally. This preserves flexibility without requiring an ever-growing enum.

**True contradiction flow:**

```python
results = await system.recall("should I cache embeddings")
# Gets:
#   Block A: "Always cache embeddings — saves API calls"
#   Block B: "Don't cache embeddings — stale vectors after content changes"

await system.connect(
    source=results.blocks[0].id,
    target=results.blocks[1].id,
    relation="contradicts",
    note="A assumes static content. B assumes evolving content. Contradiction is real but conditional."
)
# Effect: During retrieval, if A is in results, B surfaces as a flagged counterpoint,
# not as a supporting block.
```

---

### Scenario 5 — End-of-Session Reflection (bulk connection)

The agent is finishing a session. It reflects on everything it learned and used, and encodes the relationships it noticed.

```python
async with system.session():
    # ... entire session work ...
    pass

# On session end, agent reflects:
session_blocks = await system.recall("today's learnings", frame="short_term", top_k=10)

# Agent identifies three connections from its working memory:
connections = [
    ("block_id_1", "block_id_2", "supports", "Both describe why frame selection matters"),
    ("block_id_3", "block_id_4", "elaborates", "B gives concrete examples of A's principle"),
    ("block_id_2", "block_id_5", "contradicts", "These give opposing recommendations"),
]

for source, target, relation, note in connections:
    result = await system.connect(source, target, relation=relation, note=note)
    # Each call is atomic and idempotent — safe to run in sequence
```

**This works well.** Post-session reflection is a natural, low-friction time to call `connect()`. The agent has processed information and formed judgments. This is the "conscious association during deliberate review" biological pattern.

**Problem:** The agent must remember block IDs across an entire session. IDs are opaque 16-character hex strings — agents can't naturally recall them from working memory.

**Mitigation:** Expose `system.session_block_ids` — a running list of all block IDs touched (learned, recalled, or received in outcomes) during the current session. Agent can iterate over this list at reflection time without having to reconstruct IDs from memory.

```python
# Agent at session end:
print(system.session_block_ids)
# → ["796845bc...", "42f4ba02...", "85515efd...", ...]
# Agent can inspect and connect these knowingly
```

---

### Scenario 6 — Stale or Archived Block

Agent tries to connect to a block that has been archived by `curate()`.

```python
# Agent (returning after a long absence) tries to connect a pattern it remembers:
result = await system.connect(
    source="new_active_block_id",
    target="archived_block_id",  # curate() archived this 3 weeks ago
    relation="supports"
)
# → ConnectError: target block not found in active memory.
#   .recovery = "Block may be archived. Use recall() to check if knowledge
#                is still active. If important, re-learn the content to reactivate it."
```

**Design question:** Should connecting to an archived block auto-reactivate it?

**No.** Auto-reactivation is a significant action — it changes decay timers, consumes resources, and may not be what the agent intended. The agent should explicitly decide to reactivate.

**Better:** The error message explains the situation and gives the exact recovery path. The agent can then:
1. `recall()` to find a current equivalent block
2. `learn()` the content again if it's still relevant (near-duplicate detection will link to archived)
3. Accept that the archived block's knowledge has faded naturally

**Recommendation:** `ConnectError.status = "block_not_active"` vs `"block_not_found"` — distinct statuses allow the agent to differentiate between "ID wrong" (typo) and "block archived" (expected lifecycle event).

---

### Scenario 7 — Degree Cap Hit

A central hub block already has 10 edges. The agent tries to add an 11th.

```python
# "SELF frame for values" block is highly connected — already at degree cap of 10.
result = await system.connect(
    source="hub_block_id",
    target="new_insight_id",
    relation="supports"
)
```

**Design question:** What happens?

**Option A — Silent displacement:** Automatically remove the weakest auto-created edge, add the new one. Agent never knows.

**Option B — Error:** Raise `DegreeCapError`. Agent must decide.

**Option C — Displacement with notification:** Remove weakest auto-created edge, add new one, include `displaced_edge` in result so agent can review.

**Option C is correct for agent-first design.** The agent should know that displacement occurred. A silent side-effect on existing memory is the worst outcome — the agent may later wonder why a previously working connection is gone.

**Displacement priority order** (weakest first, most protected last):

| Priority (evict first) | Relation Type | Rationale |
|------------------------|---------------|-----------|
| 1st to evict | `similar` | Auto-created from geometry; lowest semantic value |
| 2nd to evict | `co_occurs` | Hebbian signal; statistical, not intentional |
| Never evict | `elaborates`, `supports` | Agent-asserted semantic type |
| Never evict | `outcome` | Confirmed by feedback loop |
| Never evict | `contradicts` | Critical for retrieval correctness |

**If all 10 existing edges are agent-asserted or outcome-confirmed:**

```python
# ConnectResult when no auto-edges to displace:
# action = "deferred"
# result.deferred_note = "Degree cap reached with high-priority edges.
#                         Connection stored as pending. Run curate() or
#                         increase edge_degree_cap to resolve."
```

The agent can then decide: increase the cap, manually disconnect a low-priority edge, or accept that the connection is deferred.

---

### Scenario 8 — Agent Corrects a Wrong Connection

Agent made a mistake last session — incorrectly connected two unrelated blocks. Now it wants to undo this.

```python
# Agent (reviewing graph): "I mistakenly connected 'cosine similarity' to 'constitutional blocks'
#                           because both use the word 'identity'. That connection is wrong."

result = await system.disconnect(
    source="cosine_block_id",
    target="constitutional_block_id",
    reason="False positive — connected on keyword 'identity', not semantic relationship"
)
# → "Removed similar edge: cosine_block_id…→constitutional_block_id…"
```

**`disconnect()` is mandatory.** Without it, the agent can't correct mistakes. Wrong edges survive indefinitely if co-retrieval keeps reinforcing them (both blocks may be commonly recalled for different reasons, so reinforcement_count grows even for a wrong edge).

**Should `disconnect()` also accept `relation` as a filter?** Consider:

```python
# Two blocks have BOTH a 'similar' (auto) and an 'elaborates' (agent-created) edge.
# Agent only wants to remove the 'similar' auto-edge, not the 'elaborates' one.
# But edges are (from_id, to_id) unique — there can only be ONE edge between two blocks.
```

**Schema implication:** The current schema has `UniqueConstraint("from_id", "to_id")` — one edge per pair. This means when `connect()` upgrades an auto-created `similar` edge to an `elaborates` edge, the `relation_type` field is updated on the single existing row.

**`disconnect()` therefore always removes the single edge between the pair.** The `relation` parameter can be used as a safety guard only: "only disconnect if the current relation type is X."

```python
await system.disconnect(
    source="block_a",
    target="block_b",
    guard_relation="similar"  # only remove if it's a 'similar' edge; fail safely otherwise
)
```

---

### Scenario 9 — System Suggests, Agent Confirms

The ideal workflow is not purely agent-proactive. The system should surface connection opportunities from observed patterns.

```python
# After recall():
results = await system.recall("how to handle agent uncertainty")

# Suggested connections appear in results:
for suggestion in results.suggested_connections:
    print(suggestion)
# → "Consider connecting 'epistemic humility protocol' and 'uncertainty handling':
#    co-retrieved 2 times without an edge. Suggested relation: co_occurs. Confidence: 0.78"

# Agent evaluates and accepts:
if agent_agrees(suggestion):
    await system.connect(
        source=suggestion.source_id,
        target=suggestion.target_id,
        relation="supports",        # agent upgrades the suggested type based on its judgment
        note="Epistemic humility IS the uncertainty handling protocol"
    )
```

**Suggested connections make the system proactive without being presumptuous.** The system observes patterns; the agent decides what they mean.

**When should suggestions be generated?**

1. **Co-retrieval approaching threshold** — pair co-retrieved N-1 times (one away from auto-creation). "You're about to get an auto edge — want to classify it now?"
2. **High-similarity blocks with no edge** — two blocks with cosine > 0.80 but no edge (perhaps one arrived after the other was created).
3. **Cross-frame connections** — blocks in different frames that are frequently co-retrieved (e.g., TASK frame and WORLD frame blocks about the same topic).

**Anti-pattern to avoid:** Flooding the agent with suggestions. Cap at 3 per recall result. If more exist, surface the highest-confidence ones.

---

### Scenario 10 — Agent Using `connect()` in a Reasoning Loop

The agent is processing a batch of documents and encoding relationships as it goes. This is the "stress test" scenario.

```python
for document in documents:
    learned = await system.learn(document.content, tags=document.tags)

    # Find related existing knowledge
    related = await system.recall(document.content, top_k=3)

    # Connect to most relevant existing block
    if related.blocks and related.blocks[0].score > 0.6:
        await system.connect(
            source=learned.block_id,
            target=related.blocks[0].id,
            relation="elaborates" if related.blocks[0].score > 0.8 else "similar"
        )
```

**Failure modes in batch operation:**

| Failure | Cause | Mitigation |
|---------|-------|-----------|
| One block becomes a hub with 100+ connections | All documents relate to a popular central concept | Degree cap (10) prevents this; displacement policy keeps highest-quality edges |
| Identical edges created (same pair, different iterations) | Document processed twice | `if_exists="reinforce"` (default) is idempotent — safe |
| Wrong relation type in bulk | Agent uses wrong heuristic | Per-call — each `connect()` is atomic, errors don't cascade |
| Rate: N documents × 1 recall × 1 connect = 2N calls | Linear growth | All calls are fast (no LLM); acceptable for N < 1000 |
| Recall finds wrong block for connect | Query matches a different block than intended | `min_confidence` threshold in `connect_by_query()` mitigates; for explicit IDs, agent has what it saw |

---

### Scenario 11 — Agent Upgrade: Connecting After Outcome

The agent just recorded a positive outcome on two blocks. The system auto-created an outcome edge. The agent wants to add semantic meaning to it.

```python
# Auto outcome edge created:
outcome_result = await system.outcome(
    block_ids=["block_a", "block_b"],
    signal=0.9
)
# System auto-created an outcome edge between block_a and block_b (weight=0.72)

# Agent upgrades the edge with semantic context:
await system.connect(
    source="block_a",
    target="block_b",
    relation="supports",          # upgrade from 'outcome' to 'supports'
    note="Block B's pattern is what made Block A's recommendation work in practice",
    if_exists="update"            # update relation type, preserve/boost weight
)
```

**`if_exists` behavior is critical here.** The agent should be able to:
- `"reinforce"` — boost count and weight, keep existing relation
- `"update"` — change relation type and/or note, keep weight (or boost if weight provided)
- `"skip"` — do nothing if edge exists (safe idempotent check)
- `"error"` — fail if edge exists (strict mode for validation)

Default should be `"reinforce"` — the most forgiving, agent-friendly behavior.

---

## Failure Mode Taxonomy

From the 11 scenarios, failures fall into four categories:

### Category 1 — ID Resolution Failures
**Problem:** Agent doesn't have block IDs or has wrong IDs.

| Failure | Frequency | Mitigation |
|---------|-----------|-----------|
| Agent fabricates IDs | Medium | `ConnectError` with recovery immediately corrects |
| Agent uses stale IDs (block archived) | Low | `block_not_active` vs `block_not_found` distinction |
| Agent confuses IDs from multiple calls | Medium | `system.last_recall_block_ids`, `system.last_learned_block_id` |
| Agent uses content strings instead of IDs | High | `connect_by_query()` as ergonomic alternative |

### Category 2 — Semantic Mismatch
**Problem:** Agent uses wrong relation type, or types don't capture the actual relationship.

| Failure | Frequency | Mitigation |
|---------|-----------|-----------|
| Wrong relation type (e.g., marks `supports` when it's actually `elaborates`) | Medium | Custom types accepted; type can be updated via `if_exists="update"` |
| No type for "context-partitioned" relationship | Medium | Open type system — custom strings accepted and stored |
| Agent marks everything as `similar` (low effort) | Medium | Default weights by type create incentive to be specific |
| Agent marks everything as `supports` (over-confidence) | Low | Decay corrects if edge is never co-retrieved |

### Category 3 — Graph Structure Failures
**Problem:** Graph becomes poorly structured due to agent behavior.

| Failure | Frequency | Mitigation |
|---------|-----------|-----------|
| Hub block at degree cap | Low-Medium | Displacement priority order; notification in result |
| All 10 edges are agent-created, no room for new semantic edge | Low | `action="deferred"` with pending queue |
| Agent connects distantly related blocks, polluting expansion | Medium | Decay removes unused edges; `disconnect()` for explicit correction |
| Circular connections (A→B, B→C, C→A) | Low | Valid in knowledge graphs — cycles are fine |
| Agent creates duplicate semantic edges via different paths | Low | One edge per pair (UniqueConstraint); `if_exists` handles |

### Category 4 — Operational Failures
**Problem:** The call fails due to system state or misconfiguration.

| Failure | Frequency | Mitigation |
|---------|-----------|-----------|
| No active session when connecting | Low | `connect()` works without session (like `learn()`) |
| Block in different DB / wrong system | Very Low | Block ID lookup fails → `ConnectError` |
| Source == target (self-loop) | Low | Detected before DB; `SelfLoopError` with clear recovery |
| Weight out of range [0.0, 1.0] | Low | Clamp or error; prefer error to silent corruption |

---

## API Design — Final Specification

### `connect()` — Primary Method

```python
async def connect(
    self,
    source: str,
    target: str,
    relation: str = "similar",
    *,
    weight: float | None = None,
    note: str | None = None,
    if_exists: Literal["reinforce", "update", "skip", "error"] = "reinforce",
) -> ConnectResult:
    """Create or update a directed semantic edge between two knowledge blocks.

    USE WHEN: The agent observes a meaningful relationship between two blocks
    that the automatic system (similarity, co-retrieval) has not captured, or
    has captured with the wrong semantic type. Best called immediately after
    recall(), learn(), or outcome() when the relevant block IDs are available.

    DON'T USE WHEN: You don't have block IDs — use connect_by_query() instead.
    Don't use to link blocks the agent hasn't read; connections without semantic
    justification add noise rather than signal.

    COST: Instant. No LLM calls. Pure database write.

    RETURNS: ConnectResult with action ('created' | 'reinforced' | 'updated' |
    'skipped' | 'deferred'), final edge weight, relation type, and — if a
    lower-priority edge was displaced to make room — displaced_edge details.

    NEXT: No immediate follow-up required. If action='deferred', run curate()
    or increase edge_degree_cap in config. To undo, call disconnect().

    Args:
        source: Block ID of the source block. Available from recall(), learn(),
                and outcome() results.
        target: Block ID of the target block. Edges are undirected; source/target
                order does not affect storage.
        relation: Semantic type of the relationship. Core types with scoring
                  effects: 'similar', 'supports', 'contradicts', 'elaborates',
                  'co_occurs', 'outcome'. Any other string is stored as a custom
                  type and scored as 'similar'. Default: 'similar'.
        weight: Edge strength [0.0, 1.0]. None uses the relation-type default.
                Provide explicit weight only when you have a calibrated signal.
        note: Human-readable description of why this connection exists.
              Stored on the edge; surfaced in status() and future guides.
        if_exists: Behaviour when an edge already exists between source and target.
                   'reinforce' (default) — increment count, boost weight by
                     edge_reinforce_delta, keep existing relation.
                   'update' — replace relation and/or note; keep or update weight.
                   'skip' — return existing edge state without modification.
                   'error' — raise ConnectError if edge exists.

    Raises:
        ConnectError: source == target (SelfLoopError); block not found or not
                      active (BlockNotActiveError); weight outside [0.0, 1.0];
                      if_exists='error' and edge already exists.
                      All errors carry a .recovery field.
    """
```

### `disconnect()` — Correction Method

```python
async def disconnect(
    self,
    source: str,
    target: str,
    *,
    guard_relation: str | None = None,
    reason: str | None = None,
) -> DisconnectResult:
    """Remove the edge between two knowledge blocks.

    USE WHEN: An agent-created edge was incorrect and should not persist.
    Also use to override automatic edges that cause retrieval noise
    (e.g., two blocks that are textually similar but contextually unrelated).

    DON'T USE WHEN: The edge is correct but weak — decay and pruning will
    remove it naturally. Only use disconnect() for deliberate correction.

    COST: Instant. No LLM calls.

    RETURNS: DisconnectResult with action ('removed' | 'not_found' | 'guarded'),
    the removed edge's details, and reason stored for audit.

    NEXT: No follow-up required. The edge is immediately removed from graph
    expansion. If the blocks are still meaningful in context, consider
    whether a different relation type better describes them.

    Args:
        source: Block ID.
        target: Block ID.
        guard_relation: If provided, only remove the edge if its current
                        relation type matches this value. Returns action='guarded'
                        if the relation doesn't match, without modifying anything.
                        Use to avoid accidentally removing agent-created edges
                        when intending to remove auto-created ones.
        reason: Optional reason stored in the operation audit log.
    """
```

### `connect_by_query()` — Ergonomic Alternative

```python
async def connect_by_query(
    self,
    source_query: str,
    target_query: str,
    relation: str = "similar",
    *,
    note: str | None = None,
    min_confidence: float = 0.70,
    if_exists: Literal["reinforce", "update", "skip", "error"] = "reinforce",
    dry_run: bool = False,
) -> ConnectByQueryResult:
    """Find two blocks by semantic query and connect them.

    USE WHEN: The agent has a clear conceptual relationship in mind but doesn't
    have the block IDs available. Internally runs two recall(top_k=1) calls
    and connects the top results if confidence is sufficient.

    DON'T USE WHEN: You have block IDs — use connect() directly for precision.
    Don't use with vague queries; low specificity leads to connecting wrong blocks.

    COST: Two embedding calls (fast). No LLM calls.

    RETURNS: ConnectByQueryResult including the actual block content matched
    for each query — ALWAYS verify these are the blocks you intended before
    treating the connection as authoritative. Set dry_run=True to preview
    matches without writing an edge.

    NEXT: Review source_content and target_content in the result to confirm
    the correct blocks were matched. If wrong, run connect() with explicit IDs.

    Args:
        source_query: Natural language description of the source block.
        target_query: Natural language description of the target block.
        relation: Semantic type. Same values as connect().
        note: Optional description of the relationship.
        min_confidence: Minimum recall score for a match to be accepted.
                        If either query scores below this, returns
                        action='insufficient_confidence' without writing.
                        Default: 0.70.
        dry_run: If True, find and return matching blocks but do not write
                 the edge. Use to verify matches before committing.
    """
```

---

## Result Types

### `ConnectResult`

```python
@dataclass
class ConnectResult:
    source_id: str
    target_id: str
    relation: str
    weight: float
    action: str          # "created" | "reinforced" | "updated" | "skipped" | "deferred"
    note: str | None
    displaced_edge: DisplacedEdge | None   # set if a lower-priority edge was removed

    @property
    def summary(self) -> str:
        short_src = self.source_id[:8]
        short_tgt = self.target_id[:8]
        base = f"{self.action.title()} {self.relation} edge: {short_src}…→{short_tgt}… (weight={self.weight:.2f})"
        if self.displaced_edge:
            base += f". Displaced auto-{self.displaced_edge.relation} edge to fit degree cap."
        return base

    def __str__(self) -> str:
        return self.summary

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "target_id": self.target_id,
            "relation": self.relation,
            "weight": self.weight,
            "action": self.action,
            "note": self.note,
            "displaced_edge": self.displaced_edge.to_dict() if self.displaced_edge else None,
        }

@dataclass
class DisplacedEdge:
    from_id: str
    to_id: str
    relation: str
    weight: float

    def to_dict(self) -> dict[str, Any]: ...
```

### `DisconnectResult`

```python
@dataclass
class DisconnectResult:
    source_id: str
    target_id: str
    action: str         # "removed" | "not_found" | "guarded"
    removed_relation: str | None   # the type of the removed edge, if removed
    removed_weight: float | None

    @property
    def summary(self) -> str:
        if self.action == "removed":
            return f"Removed {self.removed_relation} edge: {self.source_id[:8]}…→{self.target_id[:8]}…"
        if self.action == "not_found":
            return f"No edge found between {self.source_id[:8]}… and {self.target_id[:8]}…"
        if self.action == "guarded":
            return f"Edge not removed — relation type did not match guard_relation."
        return f"Disconnect {self.action}."
```

### `ConnectByQueryResult`

```python
@dataclass
class ConnectByQueryResult:
    source_query: str
    target_query: str
    source_id: str | None
    target_id: str | None
    source_content: str | None    # full content of matched block — verify!
    target_content: str | None
    source_confidence: float
    target_confidence: float
    connect_result: ConnectResult | None  # None if dry_run or insufficient_confidence
    action: str   # "connected" | "insufficient_confidence" | "dry_run_preview"

    @property
    def summary(self) -> str: ...
```

### Suggested connections in `RecallResult`

```python
@dataclass
class SuggestedConnection:
    source_id: str
    target_id: str
    source_content: str   # readable snippet for agent evaluation
    target_content: str
    reason: str           # why the system is suggesting this
    suggested_relation: str
    confidence: float

# RecallResult gains:
@dataclass
class RecallResult:
    # ... existing fields ...
    block_ids: list[str]                            # NEW: convenience shortcut
    suggested_connections: list[SuggestedConnection] # NEW: capped at 3
```

---

## Default Weights by Relation Type

Agents should not need to specify weights. Default weights encode the semantic hierarchy:

| Relation | Default Weight | Rationale |
|----------|---------------|-----------|
| `similar` | 0.65 | Geometric baseline — just above creation threshold |
| `co_occurs` | 0.55 | Statistical signal — usage pattern, not assertion |
| `elaborates` | 0.70 | Agent-asserted — specific, directional relationship |
| `supports` | 0.75 | Agent-asserted — evidence relationship |
| `contradicts` | 0.60 | Present but not reinforcing — lower expansion weight |
| `outcome` | 0.80 | Proven by feedback — highest confidence |
| `<custom>` | 0.65 | Unknown type — treat as similar baseline |

Agent overrides with explicit `weight=` are accepted but should be rare. The type hierarchy captures most semantic variation.

---

## Session-Scoped Breadcrumbs

Properties on `MemorySystem` that eliminate the ID-friction problem:

```python
system.last_learned_block_id    # str | None — block ID from most recent learn()
system.last_recall_block_ids    # list[str]  — block IDs from most recent recall()
system.session_block_ids        # list[str]  — all block IDs touched this session
```

These are in-memory, session-scoped only. They reset on `begin_session()` and are never persisted. They exist purely to reduce friction at the call site.

**Usage:**

```python
await system.learn("New insight about frame selection")
related = await system.recall("frame selection")

# Connect without saving intermediate results:
await system.connect(
    source=system.last_learned_block_id,
    target=system.last_recall_block_ids[0],
    relation="elaborates"
)
```

---

## Degree Cap Displacement Policy

When a block is at the degree cap (default 10) and a new `connect()` is called:

**Step 1 — Check if new edge qualifies for displacement:**
The new edge's relation type must be higher-priority than the weakest existing auto-generated edge. If not, return `action="deferred"`.

**Step 2 — Displacement priority (evict from lowest to highest):**
```
Evict first:  similar   (auto-created from cosine similarity)
Evict second: co_occurs (Hebbian, statistical)
Never evict:  elaborates, supports, contradicts (semantic, agent-asserted or LLM-inferred)
Never evict:  outcome   (proven by feedback loop)
```

**Step 3 — Within a priority tier, evict by lowest effective weight.**

**Step 4 — If all existing edges are protected (no auto-created edges to displace):**
Return `action="deferred"`. The pending connection is logged in a `pending_connections` structure on `MemorySystem`. At the next `curate()` call, pending connections are reviewed and admitted if space is available.

**Rationale:** Silent displacement is a side effect the agent can't reason about. Notified displacement (via `displaced_edge` in result) keeps the agent informed. Deferral for fully-protected caps gives the agent a choice rather than arbitrarily overriding its previous decisions.

---

## The `connects` Plural Method (Batch)

For end-of-session reflection, a batch method reduces call overhead:

```python
async def connects(
    self,
    edges: list[ConnectSpec],
) -> ConnectsResult:
    """Create or update multiple edges in a single operation.

    USE WHEN: End-of-session reflection — the agent has identified several
    relationships to encode at once. More efficient than sequential connect() calls.

    COST: Instant. One DB transaction for all edges. No LLM calls.

    RETURNS: ConnectsResult with per-edge ConnectResult list and aggregate counts.
    """

@dataclass
class ConnectSpec:
    source: str
    target: str
    relation: str = "similar"
    weight: float | None = None
    note: str | None = None
    if_exists: str = "reinforce"

@dataclass
class ConnectsResult:
    results: list[ConnectResult]
    created: int
    reinforced: int
    updated: int
    skipped: int
    deferred: int
    errors: list[ConnectError]   # non-fatal per-edge errors collected, not raised

    @property
    def summary(self) -> str:
        parts = [f"{self.created} created"]
        if self.reinforced: parts.append(f"{self.reinforced} reinforced")
        if self.deferred: parts.append(f"{self.deferred} deferred")
        if self.errors: parts.append(f"{len(self.errors)} errors")
        return f"Edges: {', '.join(parts)}."
```

**Note:** `connects()` uses non-raising error collection — individual edge failures don't abort the batch. Errors are returned in `result.errors` for the agent to review.

---

## MCP Tool Registration

```python
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
    relation: 'similar' | 'supports' | 'contradicts' | 'elaborates' | 'co_occurs' | 'outcome' | <custom>
    if_exists: 'reinforce' (default) | 'update' | 'skip' | 'error'

    Returns action taken ('created', 'reinforced', 'updated', 'skipped', 'deferred')
    and — if a lower-priority auto-edge was displaced — displaced_edge details.
    """
    result = await _mem().connect(source, target, relation=relation, note=note, if_exists=if_exists)
    return result.to_dict()


@mcp.tool()
async def elfmem_disconnect(
    source: str,
    target: str,
    guard_relation: str | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    """Remove the edge between two blocks. Use to correct wrong connections.

    guard_relation: Only remove if current relation matches this value (safety check).
    """
    result = await _mem().disconnect(source, target, guard_relation=guard_relation, reason=reason)
    return result.to_dict()
```

---

## Guide Entry

```python
GUIDES["connect"] = """
connect(source, target, relation, *, note, if_exists) → ConnectResult

USE WHEN: You observe a meaningful relationship between two blocks and want to
encode it explicitly. Most effective immediately after recall(), learn(), or
outcome() when block IDs are available.

RELATION TYPES:
  'similar'     — textually or semantically close (default; auto-created by system)
  'supports'    — block A provides evidence or reasoning for block B
  'contradicts' — block A opposes or challenges block B
  'elaborates'  — block A provides detail, example, or expansion of block B
  'co_occurs'   — these concepts appear together (Hebbian; also auto-created)
  'outcome'     — relationship confirmed by agent feedback loop
  <custom>      — any string; stored verbatim, scored as 'similar'

IF_EXISTS OPTIONS:
  'reinforce' (default) — boost weight; keep existing relation
  'update'              — change relation/note; keep weight
  'skip'                — no change if edge exists
  'error'               — raise if edge exists

FINDING BLOCK IDs:
  system.last_learned_block_id     — from most recent learn()
  system.last_recall_block_ids     — from most recent recall()
  system.session_block_ids         — all blocks touched this session
  results.block_ids                — convenience shortcut on RecallResult

IF YOU DON'T HAVE IDs:
  Use connect_by_query(source_query, target_query, relation) — searches by content.
  Always verify source_content and target_content in the result.

CORRECTING MISTAKES:
  Use disconnect(source, target) to remove wrong connections.
  Alternatively, wrong connections decay naturally if never co-retrieved.

NEXT: No follow-up needed. If action='deferred', run curate() or increase
edge_degree_cap. Suggested connections appear in recall() results.
"""
```

---

## Implementation Risk Register

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|-----------|
| Agents use `connect()` to link everything, degrading graph quality | Medium | High | Degree cap + decay removes unused edges naturally |
| Agents ignore `connect()` entirely (too much friction) | Medium | Medium | Suggested connections in recall() lower the barrier |
| `connect_by_query()` matches wrong blocks silently | Medium | Medium | Always return matched content; `dry_run=True` for preview |
| Degree cap hit with all protected edges; pending queue grows large | Low | Low | `curate()` reviews pending queue; alert in `status()` if queue > N |
| Agent connects archived block (ID confusion) | Low | Low | `block_not_active` error with clear recovery |
| Schema migration breaks existing edge data | Low | High | Add columns with defaults; non-breaking migration |
| Weight parameter misuse (all edges set to 1.0) | Low | Medium | Warn in result if weight inconsistent with relation type |
| Custom relation type strings pollute graph query filters | Low | Low | Normalise to lowercase on storage; no filtering by custom types |

---

## Open Design Questions

These require a decision before implementation but are not blockers for Phase A:

1. **Should `connect()` work without an active session?** (Like `learn()` does.) Recommendation: **Yes** — connection is an instant, atomic operation with no state dependency.

2. **Should `connects()` (batch) run in a single transaction or per-edge?** Recommendation: **Single transaction** — all-or-nothing for the whole batch. Per-edge errors collected but don't abort.

3. **Should `suggested_connections` in recall() be generated on every call or only when the agent requests them?** Recommendation: **Only when `suggest=True` is passed to recall()** — avoids adding computation to every recall() call. Opt-in.

4. **Should `connect_by_query()` use the frame parameter?** (Constrain search to a specific frame.) Recommendation: **Yes, optional** — improves precision when the agent knows which frame the target block lives in.

5. **Should `note` be stored as a separate column or embedded in a JSON field alongside other edge metadata?** Recommendation: **Separate column** — simpler to query, index, and display. One column per concern.

6. **What is the maximum length for `note`?** Recommendation: **500 characters** — long enough for semantic description, short enough to prevent agents writing essays.

7. **Should `disconnect()` log the removal in the operation history (like `_record_op`)?** Recommendation: **Yes** — removals are significant events; agents should see them in `system.history()`.

---

## Summary: The Best Possible `connect()` API

The design above achieves:

**Agent-first:** Block IDs are readily available via convenience properties. Errors are instructive. No silent side effects. `connect_by_query()` for the "I know what I mean but not the ID" case.

**Robust:** Idempotent by default (`if_exists="reinforce"`). Self-correcting (decay removes unused edges). Correctable (`disconnect()`). Non-cascading batch failures.

**Flexible:** Open relation type system — core types with scoring effects, custom types accepted. `if_exists` parameter covers all valid states of existing edges. `dry_run` for safe preview.

**Elegant:** Three methods cover 95% of cases: `connect()`, `disconnect()`, `connect_by_query()`. Batch via `connects()`. Suggested connections meet the agent halfway. Session breadcrumbs eliminate ID-hunting.

**Biologically grounded:** Agent-asserted edges > outcome-confirmed edges > co-retrieval edges > similarity edges. Displacement respects this hierarchy. Decay removes noise. Reinforcement preserves what works.
