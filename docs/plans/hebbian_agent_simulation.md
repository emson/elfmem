# Hebbian Co-Retrieval — Agent Simulation & Improvement Analysis

**Date:** 2026-03-09
**Scope:** C1 implementation stress-tested against 10 agent scenarios
**Result:** 4 issues found (1 critical bug, 2 semantic flaws, 1 observability gap) → 4 targeted fixes

---

## Framing: What Does an Agent Actually Experience?

An elf agent operates in a tight loop:

```
loop:
  ctx = await system.frame("attention", query=current_task)   ← C1 STAGES HERE
  response = llm(ctx.text + user_message)
  await system.learn(insights_from_response)
  if should_signal: await system.outcome(block_ids, signal)
  if system.should_dream: await system.dream()
```

C1 Hebbian staging fires on every `frame()` call. The agent never calls it directly — it just happens. This "invisible learning" is a strength (zero ceremony) but also where subtle failures hide.

---

## Scenario 1: The Agentic Assistant (Steady-State)

**Setup:** Agent processes user queries all day. Each loop: `frame("attention", query=user_msg)`.

**Trace:**
```
Query 1: "python list comprehensions" → [A, B, C]  staging: {(A,B):1, (A,C):1, (B,C):1}
Query 2: "python performance tips"   → [A, B, D]  staging: {(A,B):2, ...}
Query 3: "optimising python code"    → [A, B, E]  staging: {(A,B):3} → PROMOTED to co_occurs edge
Query 4+: A and B now connected → graph expansion returns both together even when only A matches
```

**Assessment: Works correctly.** Blocks that consistently co-appear in semantically related queries form edges. The graph learns the agent's domain topology.

**Minor concern:** Queries 1-3 all asking variations of "python performance" may or may not represent genuinely independent signals. The agent called the same mental concept three ways in one session — is that 3 independent confirmations? See Issue 2.

---

## Scenario 2: The Identity Loop — CRITICAL BUG FOUND

**Setup:** Agent wraps every LLM call with SELF frame for identity grounding:

```python
for message in conversation:
    self_ctx = await system.frame("self")         # identity context
    attn_ctx = await system.frame("attention", query=message)
    response = llm(self_ctx.text + attn_ctx.text + message)
```

**SELF frame has a 1-hour TTL cache** (confirmed in `context/frames.py: CachePolicy(ttl_seconds=3600)`).

**What actually happens:**
```
Call 1 (t=0):   frame("self") → DB query → [A, B, C, D, E]  cached=False → STAGES all pairs
Call 2 (t=1s):  frame("self") → CACHE HIT → [A, B, C, D, E] cached=True  → STAGES AGAIN (bug!)
Call 3 (t=2s):  frame("self") → CACHE HIT → [A, B, C, D, E] cached=True  → (A,B) count=3 → PROMOTE!
```

**Result:** With threshold=3, the first 3 consecutive calls to `frame("self")` — happening in literal seconds — promote ALL C(N,2) constitutional block pairs to co_occurs edges via pure cache hits. No genuine independent retrieval occurred. This fires in 3 seconds rather than 3 distinct usage sessions.

**This is a critical bug.** Cached results carry no new signal — they are the same DB query result served from memory. Staging on a cache hit means a loop calling `frame("self")` every iteration creates the entire constitutional graph within seconds of the first real retrieval.

**Fix:** Guard staging with `if not result.cached`.

---

## Scenario 3: The Burst Session vs. Cross-Session Signal

**Setup:** Two patterns produce identical staging counts:

```
Pattern A (burst): Same session, 3 calls in 3 seconds
  frame("attention", query="risk") → [A, B] → staging (A,B)=1
  frame("attention", query="risk") → [A, B] → staging (A,B)=2
  frame("attention", query="risk") → [A, B] → PROMOTED

Pattern B (cross-session): 3 separate day-long sessions
  Session 1: frame("attention", query="risk") → [A, B] → staging (A,B)=1
  Session 2: frame("attention", query="risk") → [A, B] → staging (A,B)=2
  Session 3: frame("attention", query="risk") → [A, B] → PROMOTED
```

**Pattern B is strongly meaningful.** A and B co-appear across 3 separate agent runs = genuine functional relationship.

**Pattern A is noise.** Three calls in one session = probably the same task iteration, possibly even the same query. The threshold=3 rationale ("once is coincidence, twice is pattern, three times is signal") assumes independence, which burst patterns violate.

**The plan explicitly says:** "Cross-session patterns are the most reliable Hebbian signal." But nothing in the current implementation enforces this. A threshold=3 burst session produces the same outcome as threshold=3 cross-session usage.

**Fix:** Per-session deduplication. Each pair can contribute at most 1 count per `begin_session()` cycle. This makes the threshold semantically mean "N distinct sessions" not "N calls."

---

## Scenario 4: The Focused Domain Agent

**Setup:** A trading assistant always retrieves the same 8 core knowledge blocks for every query:

```
{risk_management, position_sizing, correlation, volatility, kelly_criterion,
 drawdown, sharpe_ratio, mean_reversion}
```

Every `frame("attention")` call returns these 8 blocks (tight domain, specific vocabulary).

**Without per-session dedup:**
```
Call 1 (Session 1): C(8,2) = 28 pairs staged, all count=1
Call 2 (Session 1): same 28 pairs staged again, all count=2
Call 3 (Session 1): same 28 pairs → ALL 28 pairs promoted to edges
```

**Result:** 28 edges created in one session after 3 calls. The agent's graph becomes a complete subgraph for these 8 nodes. No degree cap enforcement in Phase 1, so each node gets 7 co_retrieval edges immediately.

**With per-session dedup (Fix 2):** Each pair counts once per session. Three separate sessions needed. The graph grows more slowly but more meaningfully.

**Assessment:** The fix is essential for domain-focused agents. Without it, highly correlated retrieval creates graph noise rather than signal.

---

## Scenario 5: The Long-Running Agent with Concept Drift

**Setup:** Agent processes Topic A for 100 sessions, then shifts to Topic B. Topic A blocks are rarely retrieved again.

**State after 100 Topic A sessions:**
```
staging = {
  (block_a1, block_a2): 2,   # 2/3 toward promotion — one more session needed
  (block_a3, block_a4): 1,   # 1/3 toward promotion
  ... (dozens more)
}
```

**After shift to Topic B:**
- Topic A blocks decay → eventually archived by curate()
- Their pairs stay in staging dict forever: `{(archived_id, archived_id): count}`
- These pairs never promote (archived blocks can't be retrieved)
- Eviction only fires when `len(staging) > 1000` — at Phase 1 scale, this may never happen

**Zombie entry accumulation:** With 500 blocks, 40 pairs per recall, 100 sessions → staging could hold 200+ zombie entries for archived blocks, consuming 20% of the cap while providing no value.

**Fix:** Curate-time cleanup. When `curate()` archives blocks, scan staging dict and remove pairs where either block is no longer active. The active block set is already fetched inside `curate()` — free O(n) intersection.

---

## Scenario 6: The Multi-Frame Loop — Cross-Frame Blind Spot

**Setup:** Agent calls all three frames per loop:

```python
identity = await system.frame("self")          # returns constitutional blocks [C1..C10]
goals    = await system.frame("task")          # returns goal blocks [G1, G2, G3]
context  = await system.frame("attention", query=task)  # returns knowledge [K1..K5]
```

**What stages:**
- `frame("self")`: pairs among {C1..C10}
- `frame("task")`: pairs among {G1..G3}
- `frame("attention")`: pairs among {K1..K5}

**What does NOT stage:** C1 and K1 — even if they're always both relevant to the agent's reasoning in the same loop iteration, they appear in different `frame()` calls and are never co-staged.

**Assessment:** This is a design limitation, not a bug. Cross-frame co-use is a real signal (the agent uses SELF and ATTENTION context together for every prompt) but the current design can't capture it. The `_session_block_ids` breadcrumb already tracks all blocks seen in a session — a future "session-level Hebbian" enhancement could stage cross-frame pairs.

**Current mitigation:** Frame templates render separate contexts; the LLM combines them. Cross-frame semantic relationships are still discoverable via consolidation similarity or outcome signals. C1 enriches the graph, not replaces it.

**Phase 2 opportunity:** After session ends, run staging across `session_block_ids` pairs. Low priority — consolidation already handles semantic similarity; C1 targets usage-proved functional relationship.

---

## Scenario 7: The Agent That Mostly Uses recall()

**Setup:** Agent uses `recall()` for quick lookups and only calls `frame()` for final context injection before LLM calls.

```python
# Exploration phase (no staging)
candidates = await system.recall(query="position sizing concepts")
specific   = await system.recall(query="Kelly criterion formula")

# Context injection (staging fires here)
ctx = await system.frame("attention", query="current trade decision")
```

**Result:** C1 correctly doesn't stage on `recall()` calls. The API contract is clean. Only `frame()` — the "I am about to use this context for real reasoning" call — triggers staging.

**Assessment: Works as designed.** The distinction between `recall()` (exploration, no side effects) and `frame()` (context injection, learning side effects) is meaningful and correctly implemented.

---

## Scenario 8: The Fresh Start — Staging Lost on Restart

**Setup:** Agent runs for 2 sessions. Pair (A,B) reaches count=2 (threshold=3). Process restarts.

```
Session 1: (A,B) staged, count=1
Session 2: (A,B) staged, count=2
[RESTART]
Session 3: staging = {} — count=0 again
Session 4: count=1
Session 5: count=2
Session 6: count=3 → PROMOTED — but it took 6 sessions instead of 3
```

**Assessment:** Acceptable for Phase 1. The plan explicitly acknowledges this. The longer it takes, the more confident the eventual edge. "Patterns that survive restarts reform quickly in active usage" — true for genuinely important relationships.

**When this matters:** Short-lived agents (serverless, per-request spawning). For these agents, staging never accumulates and C1 never fires. The design implicitly assumes medium-to-long-lived processes. This should be documented.

---

## Scenario 9: The SELF Frame — Constitutional Noise

**Setup:** Agent calls `frame("self")` once per conversation turn for identity grounding.

**The SELF frame guarantees** blocks tagged `self/constitutional` always appear (via `_enforce_guarantees()`). If 8 constitutional blocks exist, they appear in EVERY `frame("self")` result, regardless of query.

**Combined with Scenario 2 fix (no staging on cache hit):**
The fix makes this scenario safer. Without the fix, the 8 constitutional blocks would form a complete subgraph (28 edges) after 3 `frame("self")` calls in seconds. With the fix, they form it after 3 distinct sessions.

**Is this desirable?** The constitutional blocks ARE always co-used — they collectively define identity. So these edges are semantically correct. They just shouldn't form immediately.

**Remaining concern:** With per-session dedup, all 28 constitutional pairs reach threshold in exactly N_threshold sessions. Every session forms 28 new staging counts at once. This front-loads the constitutional graph creation.

**Mitigation:** The edges form correctly, just faster than average pairs (because constitutional blocks appear in 100% of SELF retrievals). They'll also be reinforced by `reinforce_co_retrieved_edges()` after creation, growing their `reinforcement_count` quickly — giving them C2 LTD protection (λ halved). Constitutional blocks forming a dense, durable subgraph is correct behavior.

---

## Scenario 10: Observability — The Silent Promotion

**Setup:** Agent wants to know when its graph changes due to its own behavior.

**Current state:**
```python
result = await system.frame("attention", query="risk management")
# Did this just promote an edge? Unknown. Agent can't tell.
status = await system.status()
# staging_count changed from 3 to 0? Agent might notice if polling.
# But no per-call signal.
```

**The gap:** Graph promotions are silent. The agent can infer a promotion happened by watching `status().co_retrieval_staging_count` drop, but this requires polling and comparison, which is awkward in an event-driven loop.

**Why it matters:** An agent that knows edge promotion just happened can:
- Log it for introspection
- Trigger a recall() with `expand=True` to immediately benefit from the new edge
- Record the graph evolution as a learning event

**Fix:** Add `edges_promoted: int = 0` to `FrameResult`. Zero most of the time; non-zero when staging crosses threshold. No polling needed.

---

## Issues Summary

| # | Severity | Issue | Root Cause |
|---|----------|-------|-----------|
| 1 | **Critical bug** | Cached SELF frame triggers staging | `result.cached` not checked before staging |
| 2 | **Semantic flaw** | Burst session = cross-session (threshold doesn't enforce independence) | No per-session dedup in staging |
| 3 | **Operational** | Zombie staging entries from archived blocks | Staging dict never cleaned up without cap eviction |
| 4 | **Observability** | Agent can't see per-call promotion signal | FrameResult has no `edges_promoted` field |

---

## Fixes Implemented

### Fix 1: Cache-Aware Staging (Bug Fix)

In `api.py.frame()`:
```python
# Before (bug):
if recalled_ids:
    await stage_and_promote_co_retrievals(...)

# After:
if recalled_ids and not result.cached:  # cache hits carry no new signal
    await stage_and_promote_co_retrievals(...)
```

### Fix 2: Per-Session Deduplication

Add `self._co_retrieval_session_seen: set[tuple[str, str]] = set()` to `MemorySystem.__init__()`.
Clear in `begin_session()`.
Pass to `stage_and_promote_co_retrievals()` as `session_seen` parameter.

Each pair contributes at most 1 count per session. Threshold becomes "N distinct sessions" semantically.

**Behavioral change:** Tests using threshold=2 now require 2 `begin_session()` cycles. This is the correct semantics.

### Fix 3: Zombie Cleanup in curate()

In `api.py.curate()`, after `_curate()` returns: scan staging dict, remove pairs where either block_id is not in the active set.

```python
# After _curate():
if self._co_retrieval_staging:
    async with self._engine.connect() as conn:
        active = await get_active_blocks(conn)
    active_ids = {b["id"] for b in active}
    self._co_retrieval_staging = {
        pair: count
        for pair, count in self._co_retrieval_staging.items()
        if pair[0] in active_ids and pair[1] in active_ids
    }
```

### Fix 4: FrameResult.edges_promoted

Add `edges_promoted: int = 0` to `FrameResult` dataclass.
Capture promotion count from `stage_and_promote_co_retrievals()` return value.
Set on result before returning from `frame()`.

```python
promoted_count = 0
if recalled_ids and not result.cached:
    promoted_count = await stage_and_promote_co_retrievals(...)
result.edges_promoted = promoted_count
```

---

## Design Insights

### What C1 does well
- Zero agent ceremony — Hebbian learning is invisible infrastructure
- API contract is clean: `frame()` = learning side effects, `recall()` = pure
- The `co_retrieval_staging_count` in `status()` gives non-zero signal when learning is active
- `co_occurs` edges in `_EVICTION_ORDER` — displaced by semantic agent edges, correct priority
- C2 temporal decay self-corrects burst-promoted edges (they weaken without continued use)

### What C1 cannot do (by design, not bugs)
- Cross-frame staging (SELF + ATTENTION co-used blocks)
- Persistent staging across process restarts (Phase 1 limitation)
- Weighted staging (pair co-retrieved 5 times in one session counts same as 5 separate sessions, post-fix)

### Design tension: Immediate vs. Deferred Promotion
The plan chose immediate promotion (at recall time, not at curate time) for real-time graph availability. This is correct for agent-first design — the edge is available on the very next `frame()` call.

The cost: promotion is transactional overhead on every `frame()`. At top_k=20 and 190 pairs, this is bounded and acceptable at Phase 1 scale.
