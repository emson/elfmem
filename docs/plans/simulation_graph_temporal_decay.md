# Agent Simulation: C2 — Edge Temporal Decay

**Purpose:** Evaluate the C2 plan from an agent-first perspective.
Identify gaps, failure modes, and improvements before implementation.

**Method:** Walk through 16 scenarios across different agent archetypes.
For each: state the setup, trace what happens, evaluate the outcome,
and note any API improvement needed.

---

## Agent Archetypes Used

| Archetype | Pattern | Phase 1 Scale |
|-----------|---------|---------------|
| **Solo Dev** | Long single session, one domain | 50–100 blocks |
| **Knowledge Builder** | Burst learning, light recall | 100–200 blocks |
| **Domain Shifter** | Multi-project, pivots between topics | 150–400 blocks |
| **Signal Agent** | Heavy outcome() feedback loop | 50–100 blocks |
| **Idle Agent** | Learns, then goes quiet | 50 blocks |
| **Meta Agent** | Reads CurateResult and acts on it | any |

---

## Scenario 1 — Fresh Start: First Curate After Consolidation

**Archetype:** Solo Dev
**Setup:** Agent learns 20 blocks at h=5. Consolidate creates 8 similarity edges
(weight 0.45–0.78), all with `last_active_hours=5.0`. Curate runs immediately at h=5.

**Trace:**
```
all edges: last_active_hours=5.0, current=5.0
→ hours_since = max(0.0, 5.0 − 5.0) = 0.0
→ effective_weight = weight × exp(0) = weight (unchanged)
→ no edges pruned by temporal decay
```

**Outcome:** ✅ Correct. A brand-new graph is not harmed by an immediate curate.

**Agent impact:** None. The agent can safely call `should_curate()` / `curate()`
immediately after `consolidate()` without losing freshly created edges.

---

## Scenario 2 — Solo Dev Steady State: 300 Active Hours

**Archetype:** Solo Dev
**Setup:** Agent runs continuously. Edges formed at h=0–50, co-retrieved regularly.
At h=200, last meaningful co-retrieval (last_active_hours updated to 200).
Curate runs at h=280.

**Trace (standard edge, weight=0.60, count=3, last_active=200):**
```
hours_since = 280 − 200 = 80
λ_edge = min(0.010, 0.010) × 0.5 = 0.005
effective = 0.60 × exp(−0.005 × 80) = 0.60 × 0.670 = 0.402
threshold = 0.10 → survives
```

**Curate runs at h=440 (no further co-retrieval):**
```
hours_since = 440 − 200 = 240
effective = 0.60 × exp(−1.20) = 0.60 × 0.301 = 0.181 → survives
```

**Curate runs at h=640:**
```
hours_since = 440, effective = 0.60 × exp(−2.2) = 0.60 × 0.111 = 0.067 → PRUNED
```

**Outcome:** ✅ An edge unused for ~440 active hours (equivalent to months of full-time
work) is pruned. Correct biological behaviour. The agent has moved on.

**Agent impact:** None visible. The edge was genuinely stale. If the agent needs it,
next consolidate() recreates it; next recall() over the same topic retrieves the blocks.

---

## Scenario 3 — Domain Shift: The Critical Scenario

**Archetype:** Domain Shifter
**Setup:**
- Phase A (Python, h=0–100): 50 Python blocks, many edges, heavily co-retrieved.
  Last co-retrieval of Python edges at h=95 (last_active_hours=95).
- Phase B (Data Science, h=100–600): 80 new blocks, zero Python recalls.
- Agent returns to Python at h=600.

**Curate cycles during Phase B (every 40 hours):**
```
h=200: hours_since=105, effective=0.60×exp(−0.525)=0.60×0.592=0.355 → survives
h=280: hours_since=185, effective=0.60×exp(−0.925)=0.60×0.396=0.238 → survives
h=360: hours_since=265, effective=0.60×exp(−1.325)=0.60×0.266=0.160 → survives
h=440: hours_since=345, effective=0.60×exp(−1.725)=0.60×0.179=0.107 → survives (barely)
h=480: hours_since=385, effective=0.60×exp(−1.925)=0.60×0.146=0.088 → PRUNED
```

Python edges are silently pruned at h=480. Agent returns at h=600 to a
disconnected Python subgraph. Blocks still active (top-K reinforcement kept them alive),
but their inter-connections are gone.

**🚨 FLAW 1 — Silent Domain Erosion**

The agent returns to Python, calls `recall("async patterns")`, and gets blocks back
from vector search — but without graph expansion. The Python cluster is now fragmented.
The agent has NO signal that its Python connections dissolved while it was away.

**CurateResult it saw at h=480:** `"Curated: 5 edges decayed."` — but the agent was
deep in data science work, probably not reading curate results carefully. By the time
it returns to Python, the signal is gone.

**Mitigation A (counters the silent nature):** Add `edges_decayed` to a persistent
tally in `system_config` — something the agent can query later.

**Mitigation B (immediate):** Make `CurateResult.summary` include a next-step tip
when significant edges decay:
```
"Curated: 5 edges decayed. If working across topics, consider running consolidate()
to rebuild connections for recently active blocks."
```

**Mitigation C (proactive):** When `recall()` returns blocks with `was_expanded=False`
and the blocks have high `confidence` (should have neighbours), surface a hint:
`"Tip: these blocks may have lost graph connections. Run consolidate() to rebuild."` —
but this is scope creep for C2.

**Accepted mitigation for C2:** B only. The summary tip when edges_decayed > 0.

---

## Scenario 4 — Signal Agent: Outcome Edges Under Decay

**Archetype:** Signal Agent
**Setup:** Agent uses outcome() heavily. A pair of blocks was confirmed 8 times
(high signal=0.9 each time). Outcome edge created at h=10 with weight=0.72,
reinforcement_count=8 (from upsert_outcome_edge updating count on each call).
`last_active_hours` updated to h=10 on creation. Each subsequent outcome() call
doesn't update last_active_hours (only upsert_outcome_edge on NEW edges does;
existing edges just increment reinforcement_count).

**Wait — critical inspection:** `upsert_outcome_edge()` on an EXISTING edge does:
```python
await conn.execute(
    update(edges).values(reinforcement_count=edges.c.reinforcement_count + 1)
)
```
It does NOT update `last_active_hours`. Only `reinforce_edges()` (called from
`reinforce_co_retrieved_edges()` during recall) updates `last_active_hours`.

**So:** An outcome edge confirmed 8 times but never co-retrieved has:
- `last_active_hours = 10.0` (from creation — after C2 fix)
- `reinforcement_count = 8`

At h=500: hours_since=490, count=8 (below ESTABLISHED_EDGE_THRESHOLD=10):
```
λ_edge = 0.005
effective = 0.72 × exp(−2.45) = 0.72 × 0.086 = 0.062 → PRUNED
```

**🚨 FLAW 2 — Outcome Confirmation Doesn't Refresh Edge Anchor**

An edge confirmed via `outcome()` 8 times is pruned at h=500 because
`upsert_outcome_edge()` only updates `reinforcement_count`, not `last_active_hours`.
The agent's explicit feedback signal is not treated as "active use."

This contradicts the biological model: outcome confirmation IS the strongest signal of
usefulness. A synapse strengthened through repeated dopaminergic gating should NOT
decay as if it hasn't been used.

**Fix:** `upsert_outcome_edge()` on an EXISTING edge should also update
`last_active_hours = current_active_hours`:

```python
# When edge already exists (rowcount == 0):
await conn.execute(
    update(edges)
    .where(and_(edges.c.from_id == from_id, edges.c.to_id == to_id))
    .values(
        reinforcement_count=edges.c.reinforcement_count + 1,
        last_active_hours=last_active_hours,  # refresh anchor on re-confirmation
    )
)
```

This is a required fix to the plan — not optional. Without it, the most
validated edges in the system still decay on the co-retrieval clock rather than
the outcome clock.

**Updated plan entry:** `upsert_outcome_edge()` must update `last_active_hours`
on BOTH insert (new edge) and update (existing edge reinforcement).

---

## Scenario 5 — The Agent That Reads CurateResult

**Archetype:** Meta Agent
**Setup:** Agent runs curate() and receives:
```python
result = await system.curate()
print(result)
# → "Curated: 3 archived, 12 edges decayed, 5 reinforced."
```

The agent sees 12 edges decayed. It wants to reason about this:
- Were these important connections?
- Should it use connect() to rebuild any?
- Is the graph still healthy?

**What the agent has:** `result.edges_decayed = 12`. Nothing else.

**What the agent needs:**
1. **Denominator.** "12 out of 47" is actionable. "12" in isolation is not.
   Is this 25% of the graph? 1%? The agent can't gauge severity.
2. **Identity.** WHICH edges decayed? The agent might know from its own reasoning
   that "the async-patterns ↔ database-optimization connection is important."
   Without knowing it decayed, it can't reconnect it.
3. **Guidance.** What should it do now?

**🚨 FLAW 3 — Observability Gap: Count Without Context**

A count without a denominator is a half-measure. An agent optimising for graph health
cannot act on `edges_decayed=12` without knowing the total graph size.

**Fix A (required for C2):** Add `total_edges_after: int` to `CurateResult`.
This is a single `SELECT COUNT(*) FROM edges` added after pruning. Trivial cost.

```python
@dataclass(frozen=True)
class CurateResult:
    ...
    edges_decayed: int = 0
    total_edges_after: int = 0  # NEW — denominator for observability
```

Updated summary:
```
"Curated: 3 archived, 12 edges decayed (35 remain), 5 reinforced."
```

**Fix B (future / C3):** `CurateResult` includes a `decayed_sample: list[str]` —
3-5 one-line summaries of what was lost: `"async patterns ↔ database-optimization (was 0.45)"`.
Scope-creep for C2; note for future.

---

## Scenario 6 — The Idle Agent

**Archetype:** Idle Agent
**Setup:** Agent learns 30 blocks, consolidates (edges with `last_active_hours=10`),
then is idle for a simulated long period. BUT: `current_active_hours` ONLY increments
during actual sessions. The idle period is wall-clock time, not active hours.

**Trace:**
The agent was at h=10 when it last ran. It returns after 3 months offline. The
DB still has `total_active_hours=10`. The agent starts a new session. Active hours
resume at 10 + new session time.

Edges still have `last_active_hours=10`. At h=15 (5 hours of new usage):
```
hours_since = 15 − 10 = 5
effective = 0.60 × exp(−0.025) = 0.60 × 0.975 = 0.585 → survives
```

**Outcome:** ✅ Perfect. The session-aware clock is the core design axiom.
Holidays, weekends, and months of inactivity don't kill knowledge. This is one of
elfmem's primary differentiators from wall-clock systems.

**Agent impact:** Zero. The agent returns to a fully intact graph. This scenario
validates the session-aware hours approach completely.

---

## Scenario 7 — Over-Calling curate()

**Archetype:** Any
**Setup:** Agent calls curate() every 2 active hours (aggressive), ignoring
`should_curate()`. Current active hours = 50. All edges have `last_active_hours`
set close to current.

**Trace (edge last_active=48, current=50):**
```
hours_since = 2
λ_edge = 0.005
effective = 0.60 × exp(−0.01) = 0.60 × 0.990 = 0.594 → survives
```

**Outcome:** ✅ Idempotent under over-calling. The formula degrades gently;
short elapsed times produce near-zero decay. Aggressive curate callers don't
lose edges prematurely.

**Agent impact:** Safe. `should_curate()` guard is advisory, not mandatory.
Agents that ignore it won't corrupt the graph.

---

## Scenario 8 — Never Calling curate()

**Archetype:** Any
**Setup:** Agent never calls curate() — only learn() + consolidate() + recall().

**What happens:** Temporal decay never runs. Edges accumulate indefinitely.
`prune_weak_edges()` also never runs. Graph grows monotonically.

**Impact:** Stale edges persist. Graph expansion retrieves increasingly irrelevant
neighbours. Recall quality degrades silently. No error, no warning.

**🚨 FLAW 4 — No Signal When Curate Has Been Skipped Too Long**

The agent using only learn/consolidate/recall has no indication that its graph
is growing stale. The `status()` call returns inbox/active counts but no
"time since last curate" or "graph staleness" metric.

**Fix:** `MemorySystem.status()` should include `hours_since_curate: float | None`.
This already requires reading `last_curate_at` from `system_config`. The field could
be None if curate has never run. An agent can then act:
```python
status = await system.status()
if status.hours_since_curate and status.hours_since_curate > 80:
    await system.curate()
```

This is consistent with the existing `should_curate()` function — surface it in status.

**Note:** This fix touches `SystemStatus` in `types.py` and `MemorySystem.status()`.
Relatively contained. Include in C2 or note as a follow-on improvement.

---

## Scenario 9 — INSERT OR IGNORE: The Reconsolidation Question

**Archetype:** Knowledge Builder
**Setup:** Agent learns blocks A and B at h=5. Consolidate creates edge A↔B,
`last_active_hours=5`. Agent learns many new blocks. At h=100, blocks A and B
are in the active set. Agent runs consolidate() again.

**What happens:** `insert_edge()` uses `INSERT OR IGNORE`. The duplicate insert is
silently ignored. Edge A↔B keeps `last_active_hours=5` even though the current
time is h=100.

**Is this a flaw?** Let's think carefully.

The `last_active_hours` represents "when was this connection last actively used."
Re-consolidation at h=100 does NOT represent "active use" of the A↔B connection —
it just re-recognises that A and B are still geometrically similar. Only co-retrieval
(the agent actually using both blocks together in reasoning) represents active use.

**Verdict:** ✅ Not a flaw. The behaviour is semantically correct.

`INSERT OR IGNORE` means: "the connection was established at h=5; subsequent
recognition of the same similarity is not evidence that the connection was used."
The edge ages from its creation anchor; only co-retrieval refreshes it.

**BUT:** An agent that runs consolidate() frequently on a stable corpus may believe
it's "refreshing" its graph when in fact edge timestamps are not advancing.
This expectation mismatch should be documented in the docstring and guide.

**Documentation fix:** `insert_edge()` docstring should note that `INSERT OR IGNORE`
does not update `last_active_hours` on duplicate — edge timestamps advance only via
`reinforce_edges()` (co-retrieval) or `outcome()` (confirmation).

---

## Scenario 10 — The Pre-C2 Edge Problem (Migration)

**Archetype:** Any agent upgrading
**Setup:** Agent has an existing DB with edges created before C2 (all with
`last_active_hours=NULL`). Upgrades to C2-enabled elfmem.

**What happens:**
- `_prune_decayed_edges()` skips all NULL edges (guard: "skip if no session anchor")
- Static pruning (`prune_weak_edges`) still applies: weight < 0.10 AND count=0
- Well-weighted NULL edges (weight=0.60) persist indefinitely — temporal decay never fires

**Is this a flaw?** For the transition period: acceptable. NULL edges from old code
face only static pruning, which is what they faced before C2. No regression.

Over time, edges that get co-retrieved have `last_active_hours` set by `reinforce_edges()`.
These then participate in temporal decay normally. Only edges that are NEVER co-retrieved
remain NULL and outside temporal decay — which is the correct conservative treatment.

**Verdict:** ✅ Acceptable degraded behavior during transition. Cleans up naturally as
edges are co-retrieved or re-created.

**Documentation fix:** Note in `_prune_decayed_edges` that NULL-anchor edges are
intentionally excluded — they're either newly created or pre-C2 legacy edges.

---

## Scenario 11 — Two Thresholds: Static vs Temporal

**Archetype:** Any
**Setup:** Edge A↔B: weight=0.35 (moderate composite score), never co-retrieved,
`last_active_hours=10.0`, current=500.

**Static prune** (prune_weak_edges): weight=0.35 ≥ 0.10 → survives (count doesn't matter).
**Temporal decay** at h=500, hours_since=490:
```
λ = 0.005, effective = 0.35 × exp(−2.45) = 0.35 × 0.086 = 0.030 → PRUNED
```

This is correct. The edge had moderate initial score but was never used. After ~490
active hours of non-use, it's dead weight.

**Now consider:** What if `edge_prune_threshold=0.10` is too HIGH for temporal decay?

A weight=0.60 edge that was meaningfully created (both blocks genuinely related,
composite score high) but the agent simply never happened to recall both in the same
query. At h=640: effective=0.067 → PRUNED.

The agent lost a VALID connection just because it never triggered BOTH blocks in the
same recall. This is the "false positive decay" problem — temporally pruning a
semantically valid edge because co-retrieval patterns didn't happen to fire.

**🚨 FLAW 5 — Single Threshold Conflates "Never Useful" and "Stale but Valid"**

Static pruning at 0.10 catches genuinely useless edges.
Temporal decay at 0.10 may catch edges that are semantically valid but never
co-retrieved. A higher-weight edge (0.60) formed from a solid composite score
should require a longer evidence window before temporal pruning.

**Option A:** Separate thresholds — `edge_prune_threshold=0.10` for static,
`edge_temporal_threshold=0.05` for temporal (more conservative).

**Option B:** Time-weighted threshold — `temporal_threshold = edge_prune_threshold × 0.5`.
Default 0.05. Only prune temporally when effective weight drops to HALF the static threshold.

**Option C:** Status quo — accept that high-weight stale edges decay correctly.
If the agent never uses two blocks together, the connection is genuinely unvalidated.
The composite edge score (which already includes temporal proximity and tags) provides
the right initial weight. If the connection were truly important, the agent's recall
patterns would have reinforced it.

**Verdict for C2:** Accept option C with documentation. The temporal decay
threshold matching the static threshold is intentional — it means edges decay
to "junk level" before being pruned. An edge that starts at 0.60 needs to drop
to 0.10 effective weight before deletion. That requires 479+ active hours of
non-co-retrieval (standard blocks). This is a very long grace period.

The calibration table in the plan already shows this is conservative enough.

---

## Scenario 12 — Established Threshold Boundary (count=9 vs count=10)

**Archetype:** Signal Agent
**Setup:** Two similar blocks co-retrieved 9 times vs 10 times. Both with
weight=0.50, last_active_hours=0.0, current=500.

**count=9 (not established):**
```
λ = 0.005, hours_since=500
effective = 0.50 × exp(−2.5) = 0.50 × 0.082 = 0.041 → PRUNED
```

**count=10 (established):**
```
λ = 0.0025, hours_since=500
effective = 0.50 × exp(−1.25) = 0.50 × 0.287 = 0.143 → survives
```

There is a hard discontinuity at count=10. An agent that co-retrieves a pair 9 times
loses the edge; 10 times keeps it. The same staleness, radically different outcome.

**Is this a flaw?** It's an inherent property of any threshold-based system.
The cliff is slightly jarring but the magnitude of the difference (0.041 vs 0.143)
is substantial enough to be considered a feature — established edges genuinely should
survive much longer.

**Mitigation:** The threshold is already conservative at 10 co-retrievals. An agent
that uses two blocks together 9 times but then shifts domain has arguably moved on.
At count=9, the edge survives 400+ active hours without reinforcement (threshold
only hit at h=500 in this example). Acceptable.

**Alternative:** Continuous bonus instead of hard cutoff — `λ_factor = 1.0 / (1 + count/10)`.
More biologically accurate but harder to reason about. Defer to a future scoring revision.

---

## Scenario 13 — The Connect-Then-Decay Lifecycle

**Archetype:** Meta Agent
**Setup:** Agent uses `connect()` to manually link block A and block B with
`relation_type="supports"`, `weight=0.85`, `note="B elaborates A's core theorem"`.

Edge created by `insert_agent_edge()`: `origin="agent"`, `last_active_hours=h_current`.

After 800 active hours of non-use, curate() runs. Does this edge decay?

**Trace:**
```python
if edge.get("origin") == "agent":
    continue  # PROTECTED — temporal decay skipped
```

**Outcome:** ✅ Agent-asserted connections never temporally decay. Correct.

The agent EXPLICITLY said these blocks are related. That deliberate assertion is
not overridable by the passage of time. If the relationship becomes stale,
the agent revokes it explicitly via `connect()` with lower weight or a future
`disconnect()` API.

**Follow-on thought:** What if the agent made a mistake with `connect()` and wants
the system to self-correct? Currently: can't happen. The agent must explicitly
correct its own connections. This is the right tradeoff for agent-first design
— the agent's explicit will is sovereign.

---

## Scenario 14 — curate() in a Long-Running Always-On Agent

**Archetype:** Daemon / tool agent running 24/7
**Setup:** Agent accumulates 10 active hours/day. After 30 days: h=300.
Curate runs every 40 hours (7-8 curate cycles over 30 days).

Edges from day 1 (h≈0–10) with no co-retrieval:
```
After curate at h=280: hours_since≈270, effective=0.60×exp(−1.35)=0.60×0.259=0.155 → survives
After curate at h=320: hours_since≈310, effective=0.60×exp(−1.55)=0.60×0.212=0.127 → survives
After curate at h=360: hours_since≈350, effective=0.60×exp(−1.75)=0.60×0.174=0.104 → survives (barely)
After curate at h=400: hours_since≈390, effective=0.60×exp(−1.95)=0.60×0.142=0.085 → PRUNED
```

After ~40 days (400 active hours), edges that never proved useful are pruned.
For a 10 hr/day agent, that's 40 days. For a 24 hr/day agent (hypothetical), 17 days.

**Verdict:** ✅ Appropriate for always-on agents. Connections that are never used in
40+ days of active operation are genuinely stale.

**Agent impact:** The always-on agent should call `should_curate()` before each
`consolidate()` cycle. The existing `should_curate()` function handles this correctly.

---

## Scenario 15 — CurateResult as Agent Decision Input

**Archetype:** Meta Agent
**Setup:** A sophisticated agent reads curate results and acts on them:

```python
result = await system.curate()

# What can the agent currently infer?
# result.archived      → blocks lost (might need to re-learn)
# result.edges_decayed → connections lost (might need to reconnect)
# result.reinforced    → top blocks reinforced (healthy)
```

**Current `result.summary`:** `"Curated: 3 archived, 12 edges decayed, 5 reinforced."`

**What the agent cannot infer:**
- How many total edges remain? Is 12 decayed out of 15 catastrophic?
  Is 12 out of 500 irrelevant?
- Are the decayed edges from important topic clusters or noise?
- Should it run `consolidate()` now to rebuild?

**Ideal agent interaction after C2 with improvements:**
```
"Curated: 3 archived, 12 edges decayed (35 remain), 5 reinforced.
Graph: 35 edges across 47 active blocks.
Tip: If working across topics, run consolidate() to rebuild lost connections."
```

**Required improvements from this scenario:**
1. `total_edges_after: int` in `CurateResult` (denominator)
2. Contextual tip in `summary` when `edges_decayed / total_edges_after > 0.15`
   (i.e., >15% of the graph decayed in one cycle — that's significant)

---

## Scenario 16 — The Pathological: All Edges Decay at Once

**Archetype:** Burst learner → long absence → return
**Setup:** Agent learns 100 blocks in session 1 (h=0–20). All similarity edges
created with `last_active_hours=10.0`. Agent does nothing for 1000 active hours.

Curate at h=1000:
```
hours_since = 990 for all edges
standard edges: effective = weight × exp(−0.005 × 990) = weight × 0.0074
ALL edges below 0.10 threshold → ALL pruned
```

Agent returns to a graph with 0 edges. Blocks all still active (top-K reinforcement
kept them alive). But the knowledge graph is shattered.

**Is this a flaw?** It's extreme input — 1000 active hours of zero recalls is the
equivalent of using the system for 10 hours/day for 100 days without ever using it
for recall. Extreme, but possible for a tool agent that learned a corpus and then
primarily used it for generation without recall.

**🚨 FLAW 6 — Catastrophic Graph Loss Without Recovery Path**

The agent that allowed this has no signal that its entire graph was destroyed.
`edges_decayed: int` would be large, but if the agent doesn't read it carefully,
it's silent. After this event, `expand_1hop()` returns nothing. Recall quality
drops to vector-only search. The agent might not notice.

**Mitigation:**

1. **Cap decay per curate cycle.** Add `max_decay_fraction: float = 0.30` to curate():
   only prune the worst 30% of decayed edges per cycle. This prevents single-cycle
   catastrophic loss and gives the agent time to notice and react. Remaining edges
   get another 40 active hours before re-evaluation.

2. **Warn the agent via summary:** If `edges_decayed / total_edges_before > 0.25`,
   escalate the message: `"WARNING: 82% of graph connections decayed. Run consolidate()
   to rebuild."` This makes the event visible.

3. **Bridge protection for edges** (mirrors block bridge protection): if removing an
   edge would disconnect a graph cluster (leave a block isolated), preserve it.
   But this requires graph connectivity analysis — expensive for curate(). Defer.

**Recommended for C2:** Mitigations 1 and 2. They are cheap and directly address
the risk without adding complexity.

The `max_decay_fraction` defaults to 1.0 (no cap, current behavior) so existing
behavior is preserved; agents can opt into protection by setting it in config.

---

## Summary: Flaws and Required Improvements

### Required (must fix before implementation)

| # | Flaw | Fix | Location |
|---|------|-----|----------|
| F2 | `upsert_outcome_edge()` doesn't refresh `last_active_hours` on re-confirmation | Update UPDATE statement to include `last_active_hours` | `queries.py` |
| F3 | No denominator in CurateResult | Add `total_edges_after: int` field | `types.py`, `curate.py` |

### Important (fix in C2, not expensive)

| # | Flaw | Fix | Location |
|---|------|-----|----------|
| F1 | Silent domain erosion — decayed edges provide no recovery guidance | Add contextual tip to `summary` when `edges_decayed > 0` | `types.py` |
| F5 | Same threshold for static/temporal pruning (acceptable but confusing) | Document why single threshold is correct; add calibration note in docstring | `curate.py` |
| F6 | Catastrophic mass-decay without warning | Add `edges_decayed > 0.25 × total` escalation message | `types.py` |

### Deferred (note for C3 or later)

| # | Flaw | Fix | Location |
|---|------|-----|----------|
| F4 | No `hours_since_curate` in `status()` | Add to `SystemStatus` | `types.py`, `api.py` |
| — | `decayed_sample` for identity of lost edges | Add to `CurateResult` | `types.py`, `curate.py` |
| — | Continuous established bonus vs hard threshold | Replace `count >= 10` with `1/(1+count/10)` factor | `scoring.py` |
| — | Bridge protection for edges during mass-decay | Graph connectivity check before pruning | `curate.py` |

---

## Updated `CurateResult` Design

Taking all scenarios into account, the final `CurateResult` should be:

```python
@dataclass(frozen=True)
class CurateResult:
    archived: int
    edges_pruned: int                # static: weight < threshold AND count==0
    reinforced: int
    constitutional_reinforced: int = 0
    edges_decayed: int = 0           # temporal: effective_weight < threshold
    total_edges_after: int = 0       # denominator — graph size after pruning

    @property
    def summary(self) -> str:
        if not any([self.archived, self.edges_pruned, self.edges_decayed,
                    self.reinforced, self.constitutional_reinforced]):
            return "Curated: nothing required."
        parts: list[str] = []
        if self.archived:
            parts.append(f"{self.archived} archived")
        if self.edges_pruned:
            parts.append(f"{self.edges_pruned} edges pruned")
        if self.edges_decayed:
            edge_info = f"{self.edges_decayed} edges decayed"
            if self.total_edges_after > 0:
                edge_info += f" ({self.total_edges_after} remain)"
            parts.append(edge_info)
        if self.reinforced:
            parts.append(f"{self.reinforced} reinforced")
        if self.constitutional_reinforced:
            parts.append(f"{self.constitutional_reinforced} constitutional reinforced")

        summary = f"Curated: {', '.join(parts)}."

        # Escalation: significant graph decay
        if self.total_edges_after > 0:
            total_before = self.total_edges_after + self.edges_decayed + self.edges_pruned
            decay_fraction = (self.edges_decayed + self.edges_pruned) / max(total_before, 1)
            if decay_fraction > 0.25:
                summary += " Graph connections reduced significantly — consider running consolidate() to rebuild."
            elif self.edges_decayed > 0:
                summary += " Tip: run consolidate() to rebuild connections for recently active blocks."

        return summary
```

---

## Revised Implementation Checklist for Plan

Based on the simulation, the plan needs these additions:

1. **`upsert_outcome_edge()` update clause** — add `last_active_hours` to the UPDATE
   that fires when an existing edge is reinforced via outcome(). This is
   the most important semantic fix.

2. **`total_edges_after`** — count edges after all pruning in `curate()`;
   populate `CurateResult.total_edges_after`. One `SELECT COUNT(*)` after deletion.

3. **`CurateResult.summary` escalation** — contextual tip when `edges_decayed > 0`;
   escalated warning when `decay_fraction > 0.25`.

4. **`_prune_decayed_edges()` docstring** — explicitly document that NULL
   `last_active_hours` is intentionally excluded and why.

5. **`insert_edge()` docstring** — note that `INSERT OR IGNORE` does not refresh
   `last_active_hours` on duplicate; only co-retrieval does.

None of these change the core formula or structural decisions. They improve
the agent-facing surface without adding complexity to the implementation.
