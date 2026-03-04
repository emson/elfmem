# elfmem Explorations Index

A complete catalog of 26 explorations for **elfmem** (ELF Memory), an adaptive, self-aware memory system for LLM agents.

Each exploration answers one design question through worked mathematical examples, design decisions, and implementation insights.

---

## The Twenty-Six Explorations (So Far)

### 001 — Basic Decay Showdown

**Question:** How long do memory blocks with different decay profiles actually survive?

**Setup:** 3 blocks (identity, knowledge, observation) with λ values ranging from 0.0001 to 0.1.
No activity, no reinforcement. How many days until each is pruned?

**Key Result:** Standard knowledge (λ=0.01) dies in 12.5 days. This means retrieval MUST
reinforce blocks, or learned knowledge disappears.

**Design Impact:** Reinforcement is non-optional. The system must use memory during
retrieval to keep knowledge alive.

**Limitations:** Assumes pure wall-clock time. Doesn't account for activity (holidays).
See **005** for sophisticated decay.

---

### 002 — The Confidence Trap

**Question:** Does the ATTENTION frame really return query-relevant blocks, or does
self-tagging cause identity blocks to dominate?

**Setup:** 5 blocks (2 identity, 3 knowledge), a specific query ("Python concurrency"),
and the ATTENTION scoring formula (0.35 weight on similarity).

**Key Result:** ATTENTION works correctly. Query-relevant blocks (PY2, PY1) rank highest
due to high similarity weight. Identity blocks rank 3rd-4th but don't dominate.

**Design Impact:** The weights are tuned correctly. The Python sim's "bug" was corpus
composition (too many identity blocks), not the formula.

**Design Question:** Would incremental assembly (threshold-based) instead of top-K be better?

---

### 003 — Full SELF Frame Scoring Walkthrough

**Question:** With a small graph (5 blocks, 4 edges), how does the SELF frame assembly
work end-to-end? Which blocks make it, and why?

**Setup:** Realistic block metadata (reinforcement counts, confidence, decay), a small
graph with edges between identity blocks, and the SELF scoring formula.

**Key Result:** A non-self-tagged block (K1: "list comprehensions") infiltrates the SELF
frame because its reinforcement_count (10) overpowers another self-tagged block (S2: "collaboration")
with fewer reinforcements (4).

**Design Impact:** Reinforcement is incredibly powerful. Frequent use signals identity.
This is a feature, not a bug — but it raises the question: should is_self_component
get a direct scoring bonus?

**Design Question:** Is high usage a signal of identity, or should identity be protected
from usage-driven changes?

---

### 004 — Self as Filter vs. Self as Context

**Question:** Should self act as a hard **gatekeeper** (blocks non-aligned content), or
as a soft **context layer** (influences decay and retrieval)?

**Setup:** Abstract comparison of five models:
- Hard gate (block if self-similarity < threshold)
- Soft confidence boost (self-aligned starts with higher confidence)
- Decay modifier (self-aligned uses slower decay)
- Retrieval boost (self-aligned surfaces more in frames)
- Novelty × Relevance (interest = fit AND novelty)

**Key Result:** Hard gates create echo chambers and are irreversible. Soft bias enables
growth while preserving self-coherence.

**Design Decision:** Use three-layer interest model:
1. Novelty dedup at ingestion (remove true duplicates only)
2. Self-alignment scoring at consolidation (confidence boost + decay profile)
3. SelfAlignment component at retrieval (optional, frame-type dependent)

**Design Impact:** Self grows naturally. Nothing is blocked. System can learn anything
but emphasizes self-relevant knowledge. This is reversible.

---

### 005 — Beyond Wall-Clock Decay

**Question:** Pure time-based decay (001) kills knowledge during holidays. What actually
causes memory loss, and how do we model it separately?

**Setup:** The holiday problem. You learn Python asyncio over 14 days, take a 2-week holiday
(no system use), return. Should your knowledge still be there?

**Current model:** Standard blocks decay after ~12.5 days idle. Knowledge is lost. ✗

**Key Result:** Three decay mechanisms exist:
1. **Staleness** (time-dependent) — meeting notes, dated content
2. **Interference** (event-driven) — new content displaces old similar content
3. **Disuse** (usage-driven) — blocks scoring low in queries, eventually pruned

Different block types need different mechanisms. Learned knowledge should NOT decay
from wall-clock time alone.

**Design Decision:** Phase approach:
- Phase 1 (MVP): Session-aware decay (only count active hours)
- Phase 2 (v1.1): Add displacement tracking (interference model)
- Phase 3 (v1.2): Full multi-factor decay (all three mechanisms)

**Design Impact:** Knowledge persists across breaks. Only truly stale content (meeting notes)
and truly unused content (low scores) are pruned.

---

---

### 006 — Self as System Prompt

**Question:** How does SELF frame assembly produce a system prompt for LLM calls
while using the same mechanisms as all other frames and staying small (like a CLAUDE.md)?

**Setup:** 6 self-tagged blocks across 4 category subcategories (constraint, value, style,
context), a 600-token budget, and the complete 5-layer pipeline.

**Key Result:** The selection mechanism is identical to all other frames. Only the output
processing differs: budget enforcement → contradiction check → template formatting → cache.
System prompt produced: 378 tokens, 6 blocks, 4 sections.

**Design Decision:** Three separate layers after selection:
1. Token budget (greedy selection within 600 token hard cap)
2. Contradiction resolution (higher confidence wins, lower excluded but not deleted)
3. Template formatting (blocks → markdown sections by category subcategory)
4. Cache (TTL 1 hour + 5 event-based invalidation triggers)

**Edge Cases Resolved:** Empty self (seed self fallback), hard contradiction (confidence-weighted),
budget overflow (natural score-based culling), circular reinforcement (Brier calibration + entropy
monitoring), recursive self-modification (provenance tracking + async update), stale context blocks
(durable decay profile auto-expires over months).

---

### 007 — Constitutional Self

**Question:** Can SELF adapt to different task types (response, consolidation, pruning) while keeping
a stable constitutional core? How does the architecture change to support task-parameterized identity?

**Setup:** 9 self-tagged blocks across 3 categories: constitutional (C1–C3: epistemic humility,
direct communication, context-first ethics), values (V1–V2), reasoning (R1–R2), epistemics (E1–E3).
Two full scoring passes: RESPONSE task type and CONSOLIDATION task type.

**Key Result:** Same 9 blocks produce different system prompts per task type. RESPONSE surfaces V1,
R1, V2, R2 (+0.20 modifiers on values/reasoning). CONSOLIDATION surfaces E1, E3, E2, P1 (+0.20 on
epistemics). Constitutional blocks C1–C3 appear in both — pre-allocated, never scored out.

**Design Decisions:**
1. Constitutional blocks use `is_constitutional: true` flag and bypass scoring (guaranteed inclusion)
2. Constitutional budget is pre-allocated (100 of 600 tokens), reserved before variable selection
3. Task-parameterized modifiers (±0.20) applied per `category_subcategory`, not per block
4. Separate caches per task type: constitutional cache (amendment-invalidated only) + task cache (standard triggers)
5. Constitutional Amendment Process (5 steps: flag → draft → 48h review → atomic activate → audit)

**Edge Cases Resolved:** Constitutional overflow (only highest-confidence Cs included if >100 tokens),
unknown task type (falls back to "general" — no modifiers), constitutional-variable conflict (C wins
unconditionally), task modifier amplifying bad blocks (modifiers narrow/broaden focus, don't create new content),
multiple concurrent tasks (serialize; each call gets task_type parameter independently).

---

### 008 — The Four Lifecycle Operations

**Question:** How do learn, consolidate, curate, and retrieve work as discrete system
actions? What triggers each? What does each do step by step to blocks?

**Setup:** 5 INBOX blocks (Python asyncio and related topics) being processed through
the complete lifecycle: learn() × 5 → consolidate() → curate() at t+300h → retrieve()
with query "how do I handle async operations".

**Key Result:** Four distinct operations with separate responsibilities:
- `learn()` — instant, hot-path safe, no embeddings, just push to INBOX
- `consolidate()` — heavyweight async: embed, semantic dedup, edge creation, self-alignment, write to MEMORY
- `curate()` — maintenance: apply decay, prune below threshold, reinforce top blocks, bump confidence
- `retrieve()` — synchronous: score against query, return top-K frame, MUST reinforce returned blocks

**Design Decisions:**
1. learn() is instant (no embeddings) — callable in hot path without latency
2. consolidate() triggered by count (N blocks) OR time interval (whichever comes first)
3. retrieve() reinforcement is mandatory — the act of retrieval IS the reinforcement event
4. curate() reinforces top-scoring blocks — prevents good-but-unretrieved blocks from dying
5. Prune is strictly-below threshold — at-threshold blocks get one more chance

**Confirmed from previous explorations:** Holiday problem is real: M1 ("Python list
comprehensions") — most central block — pruned at t+300h because no retrieval reinforced it.
Session-aware decay would have preserved it.

---

### 009 — Near-Duplicate Handling and Curate Scheduling

**Question 1:** When a new block is nearly identical to an existing one, should we
merge them — or does that violate our data immutability principle?

**Question 2:** Weekly curate() has the same holiday problem as wall-clock decay.
How should curate() be triggered?

**Key Result 1 (near-duplicates):** No merging — ever. Blocks are immutable once
created. Instead: forget(A) + create(B) + inherit(reinforcement, edges, confidence).
The new block's content is exactly as submitted; the old block's usage history
transfers to the new block via metadata. Three thresholds: >0.95 exact (reject B),
0.90–0.95 near (forget A, create B), ≤0.90 distinct (both kept, edge created).

**Key Result 2 (curate scheduling):** No wall-clock scheduling anywhere. Three triggers:
(1) active_hours_since_last_curate >= 40 checked at session start;
(2) block_count > soft_cap (500) checked after consolidation;
(3) explicit call. All session-aware, all event-driven. Consistent with exploration 005.

**Design Decisions:**
1. Blocks are immutable — merging is permanently off the table
2. Superseded blocks (A) get decay_lambda=0.5 → pruned at next curate(), not immediately
3. Inheritance: reinforcement_count + edges + max(confidence) carry forward; content/alignment/decay recomputed
4. Curate runs on active hours, not calendar time
5. No mini-curate inside consolidate() — single responsibility, full memory view required

---

### 010 — Block Anatomy: Front Matter and ID Design

**Question 1:** Should blocks contain YAML front matter, and if so, which fields
belong in the file vs. the database?

**Question 2:** Should the block ID encode category, timestamp, or other information?

**Key Result 1 (front matter):** Yes, but immutable fields only. Mutable operational
data (confidence, decay, reinforcement, edges) lives in the database only. The block
file is written once at consolidation and never modified again.

Front matter fields: `id`, `created`, `source`, `category`, `is_self`, `is_constitutional`.
Learner input is minimal markdown — the system generates id and created automatically.

**Key Result 2 (ID):** Content hash only — `sha256(normalized_content)[:16]`. This gives
content-addressability: same content = same ID = O(1) exact dedup before any embedding
call. Category and timestamp must NOT be encoded in the ID — category can change
(breaking all references), timestamp breaks content-addressability.

**Key architectural insight:** Block files are the primary record. The database is a
derived index. A full rebuild of the database from block files is always possible (except
reinforcement_count and confidence, which are genuinely operational and may reset).

---

### 011 — Identifying Self Blocks: Beyond the Boolean Flag

**Question:** `is_self: boolean` is restrictive — a block might be relevant to
multiple things. How should self-relevance be expressed?

**Setup:** Eight approaches evaluated: scalar weight, tags array, computed score only,
category array, frame affinity, graph-emergent, two-signal (intent + score), tags
with self namespace.

**Key Result:** Replace `is_self` and `is_constitutional` with a `tags` array using
a reserved `self/` namespace. A block can carry multiple tags: `[self/value, knowledge/principle]`
makes it eligible for both SELF and ATTENTION frames without compromise.

**Self tag taxonomy:**
- `self/constitutional` — invariants, guaranteed inclusion, λ=0.00001
- `self/constraint` — strong rules, λ=0.001
- `self/value` — beliefs/principles, λ=0.001
- `self/style` — communication preferences, λ=0.01
- `self/context` — situational identity, λ=0.03
- `self/goal` — directional, λ=0.01

**Design Decisions:**
1. `is_self` and `is_constitutional` removed — both replaced by tags
2. Tags + `self_alignment` are complementary: tags = explicit intent (stable); self_alignment = system inference (dynamic)
3. `category` remains as single primary structural field; `tags` is supplementary multi-label
4. Self tags drive decay profile (lowest λ among applicable tags wins)
5. Tags do not gate frames — blocks with self tags compete in all frames via scoring

---

### 012 — How Self Tags Get Assigned

**Question:** Which moment assigns self tags — learn(), consolidate(), or curate()?
Who decides a block is self-relevant, and how certain are they?

**Key Result:** All three moments play distinct roles in a trust hierarchy:
- **learn()** — explicit declaration by the learner/agent (highest trust; confirmed immediately)
- **consolidate()** — inferred via self_alignment + first-person heuristic + LLM call (medium trust; stored as candidate)
- **curate()** — usage-based promotion for blocks that became self-relevant through repeated use (emergent; promoted to confirmed)

**`self/constitutional` exception:** Cannot be declared at learn(), inferred by the system,
or usage-promoted. Requires the formal amendment process (exploration 007).

**The inference pipeline at consolidate():**
1. `self_alignment < 0.60` → skip
2. first-person language OR `self_alignment >= 0.75` → LLM classification call
3. LLM high confidence → CANDIDATE tag; low/null → no tag

**curate() promotion criteria:** sustained `self_alignment >= 0.75` for 3+ passes,
`recall_in_self_context >= 3`, `reinforcement_count >= 5` → LLM classifies type → CONFIRMED

**The primary path:** Agent calls learn() and includes explicit tags — the agent, using
its current SELF context, is the best classifier. Inference is a fallback, not the default.

---

### 013 — Edges: Storage, Lifecycle, and Simplification

**Questions:** Where are edges stored? How do they degrade or strengthen over time?
How can the edge model be simplified?

**Key Result (storage):** Database only, undirected with canonical ordering
(`from_id = min(A,B)`, `to_id = max(A,B)`). `ON DELETE CASCADE` handles block
pruning automatically. Six fields total — no edge type, no stored λ.

**Key Result (lifecycle):**
- Created at consolidation: weight = similarity score (≥0.60)
- Reinforced at recall(): co-retrieved pairs get `weight += 0.05`, hours reset to 0
- Decayed at curate(): `weight × e^(-λ_edge × hours_since_co_retrieval)` where λ_edge is derived (not stored)
- Pruned at curate() if weight < 0.10, or CASCADE when either block is deleted
- Promoted: reinforcement_count ≥ 10 → λ_edge halved (established relationship)

**Key Result (simplification):**
- Drop edge type field — all edges are implicitly `relates_to`; weight carries the meaningful information
- Weight = single evolving value (starts at similarity, updated by use and decay)
- λ_edge derived from block endpoints: `min(λ_from, λ_to) × 0.5`
- Degree cap: max 10 edges per block — prevents hub dominance, keeps centrality discriminating

---

### 014 — Should Edges Have Types?

**Question:** Should we add typed edges — specifically an `opposes`/contradiction
type? Or is this overkill?

**Key Result:** One additional type only — `opposes` — and it's justified, not overkill.
All other types (`elaborates`, `supports`, `precedes`) are deferred: they approximate
existing behaviour without correcting anything currently wrong.

**Why `opposes` is necessary:** Similarity scoring produces the *wrong* result for
contradictory blocks. Two blocks making opposing claims about the same topic have
HIGH similarity (~0.88) and would receive a strong standard edge — telling the scoring
formula they are closely related. This is actively misleading: centrality rises for
both, both appear in context frames together, the LLM receives conflicting instructions.

**Why other types are overkill:** `elaborates`, `supports`, `precedes` add expressiveness
the system doesn't currently mishandle. The weight-and-centrality model approximates
them adequately for Phase 1 scale.

**Implementation:** Separate `contradictions` table (not a field on edges). Different
lifecycle — no decay, no reinforcement, excluded from centrality. At recall(): lower-
confidence block suppressed if contradicting block also in top-K. Detection: explicit
learner declaration OR LLM call already triggered at similarity > 0.75.

---

### 015 — Context Frames: API Design and Call Semantics

**Question:** Context frames are built from specific collections of blocks. Should
calling a frame return a rendered system prompt string, raw blocks, or both?
How does the library handle internal vs. external use of frames?

**Key Result:** Two-layer API with a convenience function on top:
- `recall(frame, query, top_k)` → raw `List[ScoredBlock]`, always reinforces
- `render(blocks, template)` → str, no retrieval, no side effects
- `frame(name, query)` → `FrameResult` (.text + .blocks), the primary public interface

**Three named frames:** `self` (identity, instruction-style, cached), `attention`
(query-driven knowledge, markdown blocks), `task` (goals + relevant knowledge, structured).

**Internal vs. external:** Public `recall()` / `frame()` always reinforce returned blocks.
Internal library calls (consolidate(), curate()) use private `_recall()` which does not
reinforce — internal inspection is not a retrieval event.

**Contradiction suppression** happens inside `recall()` — callers always receive a
contradiction-free list. Extra candidates (top-K×2) sampled before suppression to
ensure the frame is never left short.

**Frame composition is the caller's responsibility** — the library returns independent
pieces; the application decides how to assemble them into a full system prompt.

**Design Decisions:**
1. `frame()` returns `FrameResult` with `.text` (for LLM injection) and `.blocks` (for inspection)
2. Query is optional for all frames; when absent, similarity component = 0 (renormalized)
3. TASK frame guarantees `self/goal` blocks like SELF guarantees constitutional blocks
4. Token budget enforced greedily inside `frame()` — most important blocks always included
5. SELF frame cached (session-stable); ATTENTION and TASK frames not cached (query-specific)

---

### 016 — Custom Context Frames

**Question:** The three named frames cover core use cases. How would a caller
create a domain-specific frame — e.g., "code review context" or "meeting prep"?
What properties are configurable? Where are definitions stored?

**Key Result:** Three mechanisms, cleanest to use case:
- **Ad-hoc** (`recall(weights=...)`) — one-off experiments, no persistence
- **Named registration** (`register_frame()`) — stored in DB, reusable by name
- **Inheritance** (`register_frame(extends=...)`) — resolved at registration time, not call time

**FrameDefinition schema:** `weights` (auto-normalised to 1.0), `filter_tags` (glob
patterns, OR-combined), `template` (reuse a built-in style), `token_budget`,
`guarantee_tags` (always included, like constitutional for SELF), `cache_ttl`,
`source` ('builtin' | 'user' | 'agent').

**Custom frames cannot** participate in library-internal mechanics, use task-type
scoring modifiers, or trigger the SELF cache's event-invalidation logic. They are
retrieval and presentation configurations — not identity-layer features.

**Built-in frames** ('builtin' source) are read-only. Any modification attempt raises
`BuiltinFrameError`.

**Design Decisions:**
1. Inheritance resolved at registration (parent changes don't silently affect children)
2. Auto-normalise weights (callers express relative emphasis; system handles sum = 1.0)
3. `filter_tags` are OR-combined glob patterns
4. `guarantee_tags` pre-allocated before scored candidates fill remaining budget
5. Default `cache_ttl=null` — custom frames are fresh per call unless caller opts in

---

## Navigation Guide

### By Concern

**Decay & Lifecycle:**
- **001** — Basic decay profiles and survival timelines
- **005** — Sophisticated decay (staleness, interference, disuse)

**Scoring & Retrieval:**
- **002** — ATTENTION frame and the confidence trap
- **003** — Complete SELF frame scoring with graph

**Identity & Self:**
- **003** — Can reinforcement override self-tagging?
- **004** — Self as filter vs. context (architectural decision)
- **006** — Self as system prompt (the production mechanism)
- **007** — Constitutional blocks + task-parameterized SELF (the full model)

**System Operations & Lifecycle:**
- **008** — learn / consolidate / curate / recall as discrete operations
- **009** — near-duplicate handling and curate scheduling

**Context Frames & API:**
- **015** — frame API design (frame / recall / render; internal vs. external calls)
- **016** — custom frame registration (ad-hoc, named, inheritance; FrameDefinition schema)

**Storage Layer:**
- **017** — complete storage design (SQLite schema, indexes, embeddings, file layout, scaling)
- **018** — DuckDB vs SQLite evaluation (confirmed SQLite for Phase 1; DuckDB hybrid noted for Phase 3)
- **019** — Database tooling: SQLAlchemy Core + Alembic (schema, migrations, query layer, testing)
- **020** — Graph layer: centrality algorithms, emergent structures, co-retrieval loop, networkx tooling
- **021** — Hybrid retrieval flow: pre-filter → vector search → graph expansion → composite score

**Integrated System Behavior:**
- **003** — Shows everything working together (graph, scoring, self)
- **004** — Shows how self affects the entire lifecycle
- **006** — Shows self assembly → formatting → cache → LLM system prompt
- **007** — Shows constitutional guarantee + task-specific adaptation side-by-side
- **008** — Shows complete block lifecycle from raw input to retrieved context frame

### By Depth

**Start here (foundations):**
1. **001** — Understand decay and why reinforcement matters
2. **002** — Understand scoring and why ATTENTION works

**Then (intermediate):**
3. **003** — See how it all fits together
4. **004** — Understand the self-interest design decision

**Then (advanced):**
5. **005** — Understand the sophisticated decay model needed long-term
6. **006** — Understand how SELF becomes an LLM system prompt
7. **007** — Understand constitutional invariants and task adaptation
8. **008** — See the complete operational lifecycle end-to-end

### By Phase

**Phase 1 (MVP):**
- 001, 002, 003 — Implement the basic system
- 004 — Make the self-interest decision (use soft bias)
- 006, 007 — Implement SELF as system prompt with constitutional layer
- 008 — Implement the four lifecycle operations (learn/consolidate/curate/recall)
- 015 — Implement the context frame API (frame / recall / render)

**Phase 2 (v1.1):**
- 005 (displacement) — Add interference tracking to decay

**Phase 3+ (future):**
- 005 (usage) — Full multi-factor decay

---

### 017 — Storage Layer: Blocks, Frames, and Edges

**Question:** How should blocks, frame definitions, and edges be stored to enable
fast recall(), scale from 50 to 50,000 blocks, and run without infrastructure?

**Key result:** SQLite only, WAL mode, 8-table schema. No separate vector
database at any realistic agent memory scale.

**Schema tables:** `blocks`, `block_tags`, `inbox`, `edges`, `contradictions`,
`frames`, `sessions`, `system_config`. Each table has a specific reason for
existing; nothing is speculative.

**Fast recall() design:** All 5 scoring fields (confidence, reinforcement,
decay, alignment, centrality) are readable in one `blocks` query + per-block
edge lookup. Contradiction check uses a partial index on `resolved = 0`.
Embeddings batch-loaded once per recall() call for brute-force cosine similarity
in Python (numpy). Total DB query count per recall(): 8 queries for 5 returned blocks.

**Scaling path — additive only, no rearchitecting:**
- Phase 1 (≤50): brute-force similarity, flat directory, embeddings in `blocks`
- Phase 2 (≤5000): split to `block_embeddings` table, materialise `centrality_cached`
- Phase 3 (≤50,000): sqlite-vec extension for ANN search, same file, no new infra

**Design Decisions:**
1. Tags in `block_tags` table (not JSON on `blocks`) — indexed glob filtering, ON DELETE CASCADE
2. `embedding` BLOB in `blocks` table for Phase 1 — split at Phase 2 threshold
3. Centrality computed per-query from `edges` in Phase 1 — materialised at Phase 2
4. `hours_since_reinforcement` stored on `blocks`, updated in bulk at session start
5. curate() and recall() reinforcement each run in a single transaction

---

### 018 — DuckDB vs SQLite: OLAP vs OLTP Tradeoff

**Question:** Should the storage layer use DuckDB instead of SQLite? DuckDB has
better analytical query performance and native vector support. Is it a better fit?

**Key result:** SQLite wins decisively. The reinforcement write pattern (small,
frequent, per-PK updates on every `recall()` call) is antithetical to DuckDB's
columnar model. DuckDB's MVCC and column-store are optimised for bulk analytical
workloads — the opposite of what AMGS does.

**Decisive factor:** Reinforcement is OLTP (write to one row by primary key).
DuckDB imposes a columnar tax on small frequent writes. SQLite's row-store is
exactly the right fit.

**DuckDB hybrid path:** Noted as Phase 2+ option — `ATTACH 'amgs.db'` (SQLite)
for operational data, DuckDB for analytical queries over snapshots. But measure
the bottleneck first; premature optimisation at Phase 1.

**Design Decisions:**
1. SQLite confirmed for all phases; DuckDB hybrid is Phase 2+ only if curate() analytics are bottleneck
2. The write pattern (reinforcement on every recall()) is the deciding factor, not read performance

---

### 019 — Database Tooling: SQLAlchemy and Alembic Best Practices

**Question:** What are the best practices for managing the SQLite database?
Specifically: SQLAlchemy vs raw sqlite3, and Alembic migration setup.

**Key result:** SQLAlchemy **Core** (not ORM). ORM's N+1 query pattern is
problematic for centrality queries (degree computed per-block). Core gives
explicit SQL control while providing connection pooling, parameter binding,
and type handling.

**Critical SQLite-specific settings:**
- `NullPool` for the engine (no connection pool; SQLite is file-local)
- `render_as_batch=True` globally in Alembic `env.py` (SQLite cannot ALTER TABLE — batch mode rewrites via temp table)
- PRAGMA setup via `@event.listens_for(engine, "connect")`: WAL mode, foreign keys ON, synchronous=NORMAL, cache_size=-32000
- `StaticPool` for in-memory test databases

**Design Decisions:**
1. SQLAlchemy Core (not ORM) — avoids N+1 centrality queries, cleaner BLOB handling, single-statement bulk UPDATEs
2. `render_as_batch=True` globally — all migrations use `op.batch_alter_table()` defensively
3. `models.py` is schema source of truth — imported by Alembic, app, and tests
4. Embedding conversion (`tobytes`/`frombuffer`) in one dedicated module — single conversion boundary
5. Seed data (built-in frames, `system_config` defaults) in initial migration
6. Named query functions in `queries/` layer — no scattered SQL strings in application code
7. Integer (0/1) for boolean columns — SQLite has no native BOOLEAN type

---

### 020 — Graph Layer: Algorithms, Structures, and Evolution

**Question:** How does the graph layer actually work at runtime? What algorithms,
what emergent structures, and what tooling?

**Key result:** Two-phase graph implementation. Phase 1: weighted degree
centrality computed per-query via SQL (no materialised graph). Phase 2:
PageRank materialised at `curate()` using networkx, stored in `centrality_cached`.

**Emergent structures over time:** clusters (semantically related blocks),
hubs (foundational concepts with high degree), bridges (cross-domain connectors),
isolates (unused blocks — prune candidates).

**Co-retrieval reinforcement:** blocks recalled together get edge weight +0.1.
This positive feedback loop causes frequently co-recalled blocks to become
more tightly connected, which causes them to be co-recalled even more.
Clusters form organically.

**networkx role:** Analysis and curate() tooling only. All runtime operations
(expansion, centrality lookup) use pure SQL. networkx is not on the critical path.

**Design Decisions:**
1. 1-hop graph expansion at Phase 1 (bounded: N_seeds × degree_cap ≤ 200)
2. Multi-hop expansion (2+ hops) deferred to Phase 2+
3. networkx for analysis only — SQL for all runtime operations
4. Co-retrieval edge reinforcement (+0.1) creates organic cluster formation
5. PageRank materialised at curate() in Phase 2; weighted degree per-query in Phase 1

---

### 021 — Hybrid Retrieval Flow: Four-Stage Pipeline

**Question:** A four-stage retrieval architecture was proposed: pre-filter →
vector search → graph expansion → composite scoring. How does this work precisely?
What does each stage do, and how do expansion blocks enter scoring?

**Key result:** The four-stage pipeline replaces exploration 017's "score all
active blocks" approach. Pre-filter dramatically reduces the candidate set.
Vector search finds semantic seeds. Graph expansion recovers related blocks
the vector search missed. Composite scoring ranks everything.

**Critical detail:** Expansion blocks (from graph) enter with `similarity = 0`.
They are scored on confidence + decay + centrality alone. This is correct — the
graph is recovering *related but not directly similar* context.

**SELF frame exception:** SELF frame (no query) skips stages 2 and 3 entirely.
It scores all active `self/*`-tagged blocks directly.

**80–95% pre-filter reduction:** This claim holds at Phase 2+ with large corpora.
At Phase 1 (≤50 blocks), the pre-filter reduces from ~50 to ~35–40. The value
is architectural consistency, not raw performance at small scale.

**Design Decisions:**
1. N_seeds = top_k × 4 (default: top_k=5 → 20 seeds) — enough context for 1-hop expansion
2. `search_window_hours = 200` as default pre-filter time window
3. Expansion block similarity set to 0 (not interpolated, not penalised)
4. SELF frame bypasses stages 2 and 3 — no query, tag-filter only
5. Pre-filter is the correct place for time-window and status checks (not scoring)

---

### 022 — Layer Model: Four-Layer Architecture

**Question:** Twenty-one explorations have produced design decisions across
storage, graph, retrieval, scoring, identity, lifecycle operations, frames, and
tooling. What is the right architectural layer model?

**Key result:** Four layers (L1→L4) plus external dependencies as injected
protocols. Lifecycle operations are vertical slices in L4, not their own layer.

```
L4 — Interface:   MemorySystem API, session lifecycle, lifecycle operations
L3 — Context:     Frame registry, scoring with frame weights, rendering, SELF
L2 — Memory:      Block lifecycle, graph, hybrid retrieval, tagging, dedup
L1 — Storage:     SQLite (SQLAlchemy Core), block files, query functions, migrations
External Deps:    EmbeddingService, LLMService (injected protocols)
```

**Layer boundary contracts:**
- L1 → L2: plain dicts (no SQLAlchemy objects cross the boundary)
- L2 → L3: `ScoredBlock` typed objects
- L3 → L4: `FrameResult` with `.text` and `.blocks`
- External deps injected as `Protocol` classes

**Module layout:** `amgs/db/`, `amgs/memory/`, `amgs/context/`, `amgs/operations/`,
`amgs/ports/` (protocols), `amgs/adapters/` (implementations), `amgs/api.py`

**Design Decisions:**
1. L2 (Memory) and L3 (Context) are separate layers — retrieval concerns must not mix with presentation
2. Lifecycle operations (learn/consolidate/curate/recall) are vertical slices at L4, not a separate layer
3. L1 returns plain dicts — no SQLAlchemy model objects cross the L1/L2 boundary
4. External dependencies (EmbeddingService, LLMService) are injected protocols, not a layer
5. `models.py` in L1 is the schema source of truth imported by Alembic, queries, and tests

---

### 023 — Agent Usage Patterns

**Question:** How does an actual LLM application use the AMGS library? What does
the per-turn interaction pattern look like? How does knowledge accumulate across
sessions? Does the design hold up under realistic agent usage?

**Key result:** AMGS is a memory substrate; the agent is the thin reasoning layer
on top. The per-turn pattern is `frame('self')` + `frame('attention', query)` +
`frame('task', query)` → build prompt → generate → `learn()`. The library handles
the rest automatically (reinforcement, decay, graph maintenance).

**Worked example (Python coding assistant):** Graph expansion demonstrated its
value concretely — K15 (celery-once library, 340h stale, failed pre-filter) was
recovered via graph edges from K12 and K23. The LLM received exactly the right
context without the user re-explaining it. Without graph expansion, this block
would have been invisible.

**SELF evolution across sessions:** SELF blocks emerge organically from knowledge
blocks crossing the `self_alignment > 0.70` threshold through repeated
reinforcement. By session 47, the agent has 3 constitutional + 3 learned SELF
blocks shaped by actual interaction history.

**Three archetypes:**
- Task-Specialist: narrow domain, ATTENTION-heavy, graph clusters are coherent → strongest fit
- Persistent Companion: SELF-heavy, episodic memory, contradiction detection critical
- Knowledge Accumulator: high learn() volume, graph navigation is primary, inbox management critical

**Evaluation summary:**
- Identity persistence: **Strong** — SELF frame is exactly right for this
- Knowledge accumulation: **Strong** — decay + reinforcement calibrate naturally
- Graph recovery of related context: **Strong** — demonstrated in worked example
- Cold start: **Weak** — mitigated by seeding, unavoidable for new agents
- Stale isolated blocks (no edges): **Moderate** — guarantee_tags as override
- SELF adaptation to change: **Moderate** — requires contradiction detection
- Broad-domain agents: **Weak** — large corpora reduce retrieval precision

**Design Decisions:**
1. `frame()` called before every generation (not once per session) — query changes each turn
2. SELF frame cached once per session — identity must not fluctuate within a session
3. `learn()` → inbox only; never direct to active — dedup and contradiction checks required
4. `consolidate()` triggered by inbox size OR session end — inbox size is the actual load signal
5. `guarantee_tags` on TASK frame for project-critical blocks — pre-filter override mechanism
6. Confidence and staleness metadata rendered in ATTENTION frame template — LLM can calibrate trust
7. `embedding_model` column in `blocks` table — required for managing embedding drift on model updates
8. `should_learn()` is agent-defined, not library-defined — the library cannot know what is worth storing
9. Agents seeded with domain SELF blocks at creation — reduces cold start window

---

### 024 — System Refinement: Audit, Unify, Simplify

**Question:** Twenty-three explorations built AMGS from first principles. Before
implementation: audit the entire design for inconsistencies, redundancies, and
inelegance. Refactor into the most robust, flexible, and elegant form.

**Audit findings (10 issues identified):**
- A1: Scoring formula uses 4 components in 023, 5 everywhere else → fixed: always 5
- A2: Two scoring modules in two layers → fixed: one shared `scoring.py`
- A3: Constitutional blocks are a special case → fixed: `guarantee_tags` handles it
- A4: SELF caching is hardcoded → fixed: `CachePolicy` on any frame
- A5: `_recall(reinforce=False)` is leaky → fixed: retrieval is pure; reinforcement in L4
- A6: `hours_since_reinforcement` stored and bulk-updated → fixed: computed from `last_reinforced_at`
- A7: Block states scattered → fixed: 3 states (inbox, active, archived) with archive_reason
- A8: Six decay rates → fixed: 4 tiers (permanent, durable, standard, ephemeral)
- A9: Dedup thresholds diverge between 008/009 → fixed: 009's three-band model canonical
- A10: Content hash enables instant dedup at learn() → applied

**Five design principles:**
1. No special cases — general mechanisms handle specific needs
2. Computation over storage — derived values computed at query time
3. Side effects at the boundary — L2 and L3 are pure; mutations in L4 only
4. One concept, one place — scoring is one function, frames are one schema
5. Configuration over code — frame differences are data, not code branches

**Key refinements:**
- Scoring: one function in shared `amgs/scoring.py`, queryless renormalization
- Frames: `FrameDefinition` with `guarantees`, `CachePolicy`, `FrameFilters` — all generic
- Decay: `last_reinforced_at` + `total_active_hours` counter; computed at query time
- State: `inbox → active → archived` with `ArchiveReason` enum
- Retrieval: pure (no side effects); reinforcement is separate L4 step
- Module layout: 22 files (was 25); `context/scoring.py`, `context/self_frame.py`, `memory/decay.py` removed

**Design Decisions:** 17 locked decisions resolving all audit findings.

---

### 025 — LLM Gateway: Configuration, Providers, and Structured Outputs

**Question:** AMGS makes three LLM calls (score_self_alignment, infer_self_tags,
detect_contradiction) and one embedding call. How should the gateway be designed
for easy provider switching, reliable structured outputs, and clean configuration?

**Key result:** LiteLLM as unified backend + instructor for structured outputs.
Provider switching is a one-line config change (model name only). API keys come
from env vars. Config in YAML. Factory method `MemorySystem.from_config()`.

**LiteLLM model naming convention encodes the provider:**
- `"gpt-4o-mini"` → OpenAI, reads `OPENAI_API_KEY`
- `"anthropic/claude-haiku-4-5-20251001"` → Anthropic, reads `ANTHROPIC_API_KEY`
- `"ollama/llama3.2"` + `base_url` → local Ollama, no API key
- `"groq/llama-3.1-8b-instant"` → Groq, reads `GROQ_API_KEY`

**Structured outputs via `instructor`:** Wraps LiteLLM, forces Pydantic-validated
responses, auto-retries on malformed output. Three Pydantic response models:
`AlignmentScore` (float), `SelfTagInference` (validated list[str]), `ContradictionScore` (float).

**Config hierarchy:** `MemorySystem.from_config()` accepts: None (reads `AMGS_CONFIG`
env var, falls back to defaults), YAML path, dict, or `AMGSConfig` object.
Fully programmatic constructor still available for custom adapter injection.

**6 production dependencies:** sqlalchemy, alembic, pydantic + pydantic-settings,
numpy, litellm, instructor, pyyaml.

**Design Decisions:** 10 locked decisions covering provider selection, output
validation, config loading, secrets handling, and test mocking strategy.

---

### 026 — Prompt Override Mechanism

**Question:** AMGS ships three default prompts for alignment scoring, tag
inference, and contradiction detection. How can library users override these
prompts for domain-specific agents — without modifying library code?

**Key result:** Three levels of override. Level 1 (inline in YAML): paste the
custom prompt string under `prompts.self_alignment`. Level 2 (file reference):
point `prompts.contradiction_file` at a `.txt` file. Level 3 (subclass):
extend `LiteLLMAdapter` and override any method.

**The gap fixed:** `MemorySystem.from_config()` was not passing any prompts to
`LiteLLMAdapter`. A new `PromptsConfig` section in `AMGSConfig` closes this.
One line change in the factory: `LiteLLMAdapter(cfg.llm, cfg.prompts)`.

**SelfTagInference change:** The `@field_validator` on `SelfTagInference.tags`
that used the module-level `VALID_SELF_TAGS` constant is removed. Tag filtering
moves to the adapter, which has access to the configured vocabulary. This allows
`valid_self_tags` in `PromptsConfig` to replace the default tag set.

**Per-call-type model overrides (from 025 open question #3):** Resolved by adding
optional `alignment_model`, `tags_model`, `contradiction_model` fields to
`LLMConfig`. Each defaults to the base `model`. Contradiction detection, where
false positives are costly, can target a higher-precision model while alignment
scoring uses a cheap model. No code changes — config only.

**Template variable reference:**
- `self_alignment` and `self_tags` require: `{self_context}`, `{block}`
- `contradiction` requires: `{block_a}`, `{block_b}`

**Optional validation:** `cfg.prompts.validate_templates()` at startup detects
missing variables before any LLM calls. Failure is loud (`ValueError`) not silent.

**Design Decisions:** 9 locked decisions covering priority (inline > file > default),
tag vocabulary semantics (replace not augment), model override orthogonality to prompts.

---

## Phase 2: Playgrounds

Test specification documents organized by subsystem. Each playground contains:
formal invariants, explicit PASS/FAIL test cases, parameter tuning scenarios,
and a Python test sketch directly translatable to `pytest`.

| Playground | Test Cases | Status |
|-----------|------------|--------|
| `sim/playgrounds/scoring/` | 12 TC + 3 tuning | Draft |
| `sim/playgrounds/decay/` | 10 TC + 4 tuning | Draft |
| `sim/playgrounds/lifecycle/` | 14 TC + 4 tuning | Draft |
| `sim/playgrounds/frames/` | 10 TC + 3 tuning | Draft |
| `sim/playgrounds/retrieval/` | 8 TC + 2 tuning | Draft |
| `sim/playgrounds/graph/` | 10 TC + 2 tuning | Draft |

See `sim/playgrounds/README.md` for format guide.

---

## How to Extend

Each exploration ends with **Variations** — ideas for follow-up explorations:

```markdown
## Variations

- [ ] What if [parameter changed]? Does [outcome] improve?
- [ ] What if [mechanism added]? How does [behavior] change?
```

To run a variation:

1. Copy the exploration file: `cp 001_*.md 001_variation_*.md`
2. Change the parameter in Setup
3. Recompute the Computation section
4. Compare results
5. Capture new insights

Or ask Claude: "Run variation 3 from exploration 001"

---

## Design Decisions Made

From these twenty-five explorations, the following decisions are locked in:

| Decision | Exploration | Rationale |
|----------|-------------|-----------|
| Reinforcement is mandatory | 001 | Knowledge dies without reinforcement |
| ATTENTION weights are correct | 002 | Query-similarity dominates self-tag |
| High-usage blocks can become identity | 003 | Reinforcement is powerful, can override tags |
| Self uses soft bias, not hard gates | 004 | Hard gates create echo chambers, lose serendipity |
| Decay is multi-factor, not pure time | 005 | Knowledge shouldn't die during idle periods |
| MVP uses session-aware decay | 005 | Simple, solves holiday problem |
| Self prompt uses same selection mechanism | 006 | Consistent; only formatting layer differs |
| Token budget (not block count) enforces smallness | 006 | Hard 600-token cap maps to real constraint |
| System prompt is cached with TTL + event invalidation | 006 | Session stability; event-driven refresh on change |
| Category subcategory routes blocks to sections | 006 | Constraints always in Constraints regardless of score |
| Seed self is required for empty-state startup | 006 | Minimal default identity before anything is learned |
| Constitutional blocks bypass scoring (guaranteed) | 007 | Behavioral invariants must not be scored out by usage |
| Constitutional budget is pre-allocated (100 tokens) | 007 | Reserved before variable selection begins |
| Task type drives scoring modifiers (±0.20) | 007 | RESPONSE boosts values/reasoning; CONSOLIDATION boosts epistemics |
| Separate caches: constitutional + per-task-type | 007 | Constitutional changes only on amendment; task cache uses standard triggers |
| Constitutional Amendment Process (5-step) | 007 | Prevents accidental modification; requires explicit intent + review |
| learn() is instant, no embeddings | 008 | Hot-path safe; embeddings deferred to consolidate() |
| consolidate() triggered by count OR time | 008 | Prevents INBOX growing unbounded |
| retrieve() reinforcement is mandatory | 008 | Retrieval event IS the reinforcement; non-negotiable |
| curate() reinforces top-scoring blocks | 008 | Prevents good-but-unretrieved blocks from dying between uses |
| Prune is strictly-below threshold | 008 | At-threshold blocks get one more curate pass before removal |
| Blocks are immutable — no merging ever | 009 | Provenance and auditability require single-source blocks |
| Near-duplicate (0.90–0.95): forget(A) + create(B) + inherit | 009 | Correction without mutation; immutability preserved |
| Exact duplicate (>0.95): reject new block silently | 009 | No new information; dedup is correct |
| Superseded blocks get fast decay (λ=0.5) | 009 | Avoids immediate deletion of in-flight references |
| Curate triggers: active-hours OR block-count OR explicit | 009 | No wall-clock scheduling; consistent with session-aware decay |
| Curate primary threshold: 40 active hours | 009 | Proportional to use, immune to holidays |
| No mini-curate inside consolidate() | 009 | Single responsibility; full memory view needed |
| Front matter contains immutable fields only | 010 | Mutable fields require rewriting files, breaking immutability |
| Front matter fields: id, created, source, category, is_self, is_constitutional | 010 | Set once at consolidation; never change |
| Operational data is database-only | 010 | confidence, decay, reinforcement, edges change continuously |
| ID = sha256(normalized content)[:16] | 010 | Content-addressable; O(1) exact dedup before embedding |
| Category and timestamp NOT encoded in ID | 010 | Category can change breaking references; timestamp redundant with created field |
| Block files are primary record; database is derived index | 010 | Full rebuild possible from block files alone |
| `is_self` and `is_constitutional` replaced by `tags` array | 011 | Tags allow multi-context membership; booleans don't |
| Self tags use reserved `self/` namespace | 011 | Controlled taxonomy with semantic weight |
| Self tag taxonomy: constitutional, constraint, value, style, context, goal | 011 | Each tag maps to a decay profile |
| Tags + self_alignment are complementary (intent vs. inference) | 011 | Tags are stable; self_alignment is dynamic |
| Self tags drive decay profile (lowest λ wins for multiple tags) | 011 | Most durable applicable profile for the block |
| Tags do not gate frame eligibility | 011 | All blocks compete in all frames via scoring weights |
| Explicit tags at learn() are the primary self-tag path | 012 | Agent judgement using current SELF context is best classifier |
| `self/constitutional` cannot be declared at learn() | 012 | Too powerful; requires formal amendment process |
| Inferred tags at consolidation are CANDIDATE, not confirmed | 012 | Inference is uncertain; confirmed status requires validation |
| Candidate → confirmed via agent review OR curate() usage criteria | 012 | Two paths: intentional (review) and emergent (usage) |
| curate() promotion: sustained self_alignment + 3+ SELF recalls + 5+ reinforcements | 012 | Sustained patterns are identity; one-off signals are noise |
| Seed self blocks bootstrap self_alignment for cold start | 012 | Without reference context, alignment scoring is meaningless |
| Edges stored in database only, undirected canonical order | 013 | Operational data; `relates_to` is symmetric; cleaner storage |
| ON DELETE CASCADE for block deletion | 013 | Automatic edge cleanup; no orphaned edges or cleanup queries |
| Edge type field dropped (Phase 1) | 013 | Weight carries the meaningful relationship information |
| Edge weight = single evolving value (similarity → usage-weighted) | 013 | Simpler than separate weight + decay fields |
| λ_edge derived from endpoint blocks (not stored) | 013 | Edges inherit durability from their blocks |
| Edge prune threshold: weight < 0.10 | 013 | Lower than block threshold; edges cheaper to recreate |
| Degree cap: max 10 edges per block | 013 | Prevents hub dominance; keeps centrality discriminating |
| reinforcement_count ≥ 10 → λ_edge halved | 013 | Established relationships earn resilience through use |
| Only one additional edge type: `opposes` | 014 | Only type that corrects a genuine model failure |
| `opposes` stored in separate `contradictions` table | 014 | Different lifecycle — no decay, no reinforcement, explicit resolution |
| `elaborates`, `supports`, `precedes` deferred to Phase 2+ | 014 | Approximate existing behaviour; don't correct failures |
| `opposes` edges excluded from centrality | 014 | Being contradicted ≠ being conceptually central |
| At recall(): lower-confidence block suppressed when contradicting block in top-K | 014 | Context frames must never contain active contradictions |
| `frame()` is primary public interface → returns `FrameResult` (.text + .blocks) | 015 | Single call gives rendered string for injection and raw blocks for inspection |
| `recall()` is secondary public interface → returns raw `List[ScoredBlock]` | 015 | Power users and custom prompt assembly pipelines |
| `render()` is stateless utility → takes blocks, returns string | 015 | Composable with any block source; no retrieval side effects |
| Three named frames: `self`, `attention`, `task` | 015 | Each has distinct scoring weights, filter, template, and token budget |
| Query optional for all frames; when absent, similarity = 0 (renormalized) | 015 | Queryless SELF = "who am I?"; queryless ATTENTION = "most salient now" |
| Contradiction suppression inside `recall()` — before blocks returned | 015 | Callers always receive contradiction-free list; no caller-side scanning |
| Extra candidates (top-K×2) sampled before contradiction suppression | 015 | Frame never left short when blocks are suppressed |
| `_recall(reinforce=False)` for internal library calls | 015 | consolidate()/curate() internal inspections are not retrieval events |
| SELF frame cached; ATTENTION and TASK not cached | 015 | Identity is session-stable; query-specific results must be fresh |
| Frame composition is caller's responsibility | 015 | Library provides pieces; application assembles full system prompt |
| Token budget enforced greedily inside `frame()` | 015 | Most important blocks always included; least important cut at budget |
| TASK frame guarantees self/goal blocks (like SELF guarantees constitutional) | 015 | Goals always present regardless of score when task frame is requested |
| Three custom frame mechanisms: ad-hoc weights, named registration, inheritance | 016 | Different use cases; ad-hoc for experiments, registered for reuse, extends for variation |
| Inheritance resolved at registration time (not call time) | 016 | Prevents silent behaviour changes when parent frame is modified |
| Weights auto-normalised to sum to 1.0 | 016 | Callers express relative emphasis without needing to count to 1.0 |
| Filter tags support glob patterns, OR-combined | 016 | "self/*" matches all self-tagged blocks without enumerating each type |
| `guarantee_tags` pre-allocated before scored candidates | 016 | Domain-specific "always surface this" without hardcoding |
| Built-in frames are read-only (`BuiltinFrameError` on mutation) | 016 | Library correctness depends on self/attention/task being stable |
| Custom frames stored in `frames` table with `source` field | 016 | Persists across restarts; source distinguishes builtin/user/agent |
| Custom frames cannot participate in library-internal mechanics | 016 | Internal calls use named frames explicitly; custom frames are retrieval config only |
| Default `cache_ttl=null` for custom frames | 016 | Query-driven frames should be fresh; caller opts into caching explicitly |
| SQLite only — no separate vector DB or other database | 017 | Sufficient for all realistic agent memory scales; zero infrastructure |
| WAL mode (`PRAGMA journal_mode = WAL`) | 017 | Concurrent recall() reads during async consolidate() writes |
| Tags in `block_tags` table, not JSON column on `blocks` | 017 | Indexed glob filtering; ON DELETE CASCADE; clean status promotion |
| `embedding` BLOB co-located in `blocks` (Phase 1) | 017 | Single bulk load for brute-force similarity; split to `block_embeddings` at Phase 2 |
| Centrality computed per-query from `edges` (Phase 1) | 017 | No premature materialisation; add `centrality_cached` column at Phase 2 |
| `hours_since_reinforcement` stored on `blocks`, updated at session start | 017 | Session-aware decay without real-time computation |
| `sessions` table tracks active hours per session | 017 | Enables session-aware bulk decay updates without wall-clock time |
| Contradiction partial index on `resolved = 0` | 017 | Active contradictions stay small; historical resolutions don't slow the check |
| Phase 1 flat file directory; Phase 2 two-level sharding by ID prefix | 017 | Migration is file-move + UPDATE; ID-deterministic so no ID changes |
| Phase 1–3 schema is additive only (columns added, nothing renamed) | 017 | Safe migrations; existing queries keep working |
| sqlite-vec extension for Phase 3 ANN search | 017 | Same SQLite file; no new infrastructure; loaded as a Python extension |
| Phase 1 centrality = weighted degree, computed at query time | 020 | One indexed SQL query per block; no iterative algorithm needed |
| Phase 2 centrality = PageRank, materialised by curate() | 020 | Captures connection quality (connected-to-important-nodes) not just count |
| `centrality_cached` nullable — falls back to weighted degree if NULL | 020 | Smooth Phase 1→2 migration; no breaking change to scoring formula |
| No graph traversal in Phase 1 runtime — all graph ops are SQL | 020 | Graph too small for traversal to add value; SQL is sufficient and faster |
| Multi-hop retrieval deferred to Phase 2 | 020 | Centrality already captures graph signal; too noisy at 50-block scale |
| networkx for analysis/curate() PageRank; SQL for all runtime operations | 020 | No library in the hot path; analysis tools available as optional extra |
| networkx preferred over igraph for Phase 1–2 | 020 | Pure Python install; adequate at < 100,000 edges; better documentation |
| Co-retrieval reinforcement creates intentional positive feedback loop | 020 | Frequently co-used concepts become durably linked — encodes learned usage patterns |
| Bridge edges dissolving when unused is correct behaviour | 020 | Unused cross-topic connections represent irrelevant relationships in practice |
| Persistent isolates signal block quality issues | 020 | Either genuinely unique concept, or poorly-expressed block that didn't embed well |
| Graph health metrics (density, isolates, components) computed at curate() | 020 | Informs system health without adding runtime cost |
| recall() redesigned as four-stage hybrid pipeline | 021 | Pre-filter + vector + graph expand + composite score; each stage corrects the previous |
| Time window pre-filter (`search_window_hours = 200`) excludes old inactive blocks | 021 | Primary reduction mechanism; blocks at 200h have decay_weight ~0.135 — unlikely to win anyway |
| N_seeds = top_k × 4 (vector search returns more than final top-K) | 021 | Composite scoring reorders seeds; need headroom for graph expansion to compete |
| Expansion blocks enter composite scoring with similarity = 0 | 021 | Not found by vector search; their other components determine if they win |
| 1-hop graph expansion in Phase 1 (bounded: N_seeds × degree_cap ≤ 200) | 021 | Multi-hop exploration 020 "deferred" referred to 2+ hops; 1-hop is practical now |
| Expansion blocks do NOT load embeddings | 021 | No similarity needed; saves significant I/O at Phase 2+ scale |
| Pre-filter and graph expansion are complementary corrections | 021 | Pre-filter may exclude old relevant blocks; graph expansion recovers them via edges |
| SELF frame (no query) skips stages 2 and 3 of hybrid pipeline | 021 | No query to embed; no seeds for graph expansion; direct composite scoring |
| `search_window_hours` stored in system_config (tunable) | 021 | Aggressive for large corpora, permissive for small; no code change to adjust |
| SQLite confirmed over DuckDB for Phase 1 | 018 | Reinforcement writes on every recall(); multi-process WAL; zero-install |
| DuckDB's columnar tax on small frequent writes is decisive | 018 | AMGS writes transactionally on every read — antithetical to columnar OLAP design |
| Phase 3 storage path: measure bottleneck first, then choose | 018 | curate() analytics → DuckDB hybrid; similarity → sqlite-vec; writes → stay SQLite |
| DuckDB hybrid (ATTACH SQLite as read-only for analytics) is a valid Phase 2+ option | 018 | DuckDB can query SQLite files directly; no data duplication or sync complexity |
| SQLAlchemy Core (not ORM) for all database access | 019 | No N+1 centrality queries; explicit column selection; bulk UPDATE in one statement |
| `NullPool` for SQLite engine | 019 | Avoids "database is locked" from pooled connections; WAL handles concurrency |
| `render_as_batch=True` globally in Alembic `env.py` | 019 | SQLite ALTER TABLE is restricted; batch mode is always safe |
| PRAGMA setup via `event.listens_for(engine, "connect")` | 019 | Every connection gets WAL/foreign-key settings, including test connections |
| `models.py` is the schema source of truth (Alembic + app + tests all import it) | 019 | No schema drift between migration files and application code |
| Seed data (built-in frames, system_config) in initial migration | 019 | Fresh database is usable without running application code |
| Named query functions in `queries/` layer — no scattered SQL strings | 019 | Testable in isolation; refactorable without searching the codebase |
| Embedding conversion (`tobytes`/`frombuffer`) in one dedicated module | 019 | Single conversion boundary; never leaks into query or scoring logic |
| `StaticPool` for in-memory test databases | 019 | All test connections share one database instance |
| Integer (0/1) for boolean columns in SQLite | 019 | SQLite has no BOOLEAN; explicit integers avoid coercion surprises |
| `op.batch_alter_table` in all migrations, even simple `add_column` | 019 | Defensive consistency; future NOT NULL columns need it anyway |
| SQLite confirmed for all phases; DuckDB hybrid is Phase 2+ only | 018 | Reinforcement write pattern (OLTP) is antithetical to DuckDB's columnar model |
| 1-hop graph expansion at Phase 1 (N_seeds × degree_cap ≤ 200) | 020 | Bounded, practical; multi-hop deferred to Phase 2+ |
| networkx for analysis and curate() tooling only — SQL for runtime | 020 | networkx not on the critical path of any agent turn |
| Co-retrieval edge reinforcement (+0.1 per co-recall event) | 020 | Organic cluster formation; frequently co-recalled blocks become tightly connected |
| N_seeds = top_k × 4; expansion blocks enter with similarity=0 | 021 | Enough seeds for 1-hop expansion; graph recovers related-not-similar context |
| SELF frame bypasses vector search and graph expansion | 021 | SELF has no query; tag-filter + scoring is sufficient |
| `search_window_hours = 200` as default pre-filter time window | 021 | Balances staleness rejection vs. coverage for typical agents |
| L1 returns plain dicts — no SQLAlchemy model objects cross L1/L2 | 022 | Layer boundary contract; L2 never imports from `amgs.db.models` |
| Lifecycle operations are vertical slices at L4, not a separate layer | 022 | Operations orchestrate across all layers; they are not a peer layer |
| External deps (EmbeddingService, LLMService) injected as Protocol | 022 | Swap implementations without touching memory or context layers |
| `frame()` called before every generation, not once per session | 023 | Query changes each turn; attention context must be fresh |
| SELF frame cached once per session | 023 | Identity must not fluctuate within a session |
| `should_learn()` is agent-defined, not library-defined | 023 | The library cannot know what is worth storing |
| Agents seeded with domain SELF blocks at creation | 023 | Reduces cold start; sets baseline identity before sessions begin |
| Confidence and staleness metadata rendered in ATTENTION frame template | 023 | LLM must calibrate trust on retrieved blocks with explicit signals |
| `embedding_model` column required for embedding drift management | 023 | Model updates require re-embedding; version tracking is non-optional |
| Five scoring components always (similarity, confidence, recency, centrality, reinforcement) | 024 | Resolves 4-vs-5 inconsistency; each signal is meaningfully distinct |
| One scoring function in shared `amgs/scoring.py` | 024 | Eliminates duplicate scoring modules across L2 and L3 |
| Queryless retrieval renormalizes weights (drops similarity, scales rest to 1.0) | 024 | No conditional in scoring function; pipeline decides weights before scoring |
| `guarantee_tags` replaces constitutional special-case logic | 024 | General mechanism; constitutionals = `guarantees=["self/constitutional"]` on SELF |
| `CachePolicy` on FrameDefinition replaces hardcoded SELF-only caching | 024 | Any frame can cache; SELF is just the default cached frame |
| Retrieval is side-effect-free; reinforcement is a separate L4 step | 024 | Eliminates `_recall(reinforce=False)`; makes L2 and L3 pure |
| `last_reinforced_at` (cumulative active hours) replaces `hours_since_reinforcement` | 024 | Computed at query time; eliminates bulk UPDATE and stale data |
| `total_active_hours` global counter tracks all session activity | 024 | Decay clock pauses between sessions; one value, always current |
| Three block states: inbox, active, archived (with archive_reason) | 024 | Clean state machine replacing scattered status terms |
| Four decay tiers: permanent (0.00001), durable (0.001), standard (0.01), ephemeral (0.05) | 024 | Simplifies 6+ decay rates; tag taxonomy still controls filtering |
| `learn()` computes content hash for instant exact-dedup | 024 | O(1) rejection at hot path; no embedding needed |
| SELF frame is a configured `FrameDefinition`, not a special module | 024 | `context/self_frame.py` merged into `context/frames.py` |
| `memory/decay.py` removed; decay computed in `scoring.py` | 024 | Decay weight is a function, not stored state |
| `consolidate()` auto-triggered at `end_session()` | 024 | Developer doesn't need to remember; inbox always processed |
| `curate()` auto-triggered at `begin_session()` | 024 | Maintenance runs before session starts |
| LiteLLM as unified LLM + embedding backend | 025 | One dependency, 100+ providers; provider switch = model name change only |
| `instructor` for all structured LLM outputs | 025 | Pydantic-validated; auto-retry on malformed output; provider-agnostic |
| API keys from env vars only, never in config files | 025 | LiteLLM reads standard env vars automatically; prevents credential leaks |
| `AMGSConfig` (Pydantic) as single config source of truth | 025 | Validated at load time; LLM, embedding, and memory tuning all in one place |
| YAML config for structure; env vars for secrets | 025 | Clean separation: app behaviour (YAML) vs credentials (env) |
| `AMGS_CONFIG` env var as implicit config file pointer | 025 | Container/CI override without code change |
| Prompts in `amgs/prompts.py` as named string constants | 025 | Visible, reviewable, overridable at construction time |
| `LLMConfig.base_url` enables local Ollama support | 025 | Zero-cost local development and testing without API keys |
| `MemorySystem.from_config()` factory accepts path / dict / object / None | 025 | All config styles supported; fully programmatic constructor still available |
| Mock services use text-hash seeding for deterministic embeddings | 025 | Same text → same vector → deterministic similarity tests |
| `PromptsConfig` in `AMGSConfig` — prompts are config-level, not code-level | 026 | Prompt changes are deployment concerns; no code changes needed |
| Inline prompt override takes priority over file override | 026 | Inline is explicit; file is referenced; explicit beats implicit |
| Prompt files resolved at adapter construction time, not at YAML parse time | 026 | One file read per adapter instance; I/O not triggered at config import |
| `valid_self_tags` list replaces (not augments) default tags | 026 | Prevents silent tag accumulation; explicit opt-in for all tags in vocabulary |
| Tag filtering moves from `SelfTagInference` validator to adapter | 026 | Adapter has configured vocabulary; Pydantic model captures raw LLM output only |
| Per-call model overrides on `LLMConfig` (alignment_model, tags_model, contradiction_model) | 026 | Model selection is an LLM concern; prompts and models are orthogonal |
| Subclassing `LiteLLMAdapter` is the Level 3 escape hatch | 026 | Full method override; no framework-level hooks needed |
| `validate_templates()` is opt-in, not enforced at load time | 026 | Startup check is optional; config loading stays synchronous and side-effect-free |
| No hot-reload of prompts — resolved once at startup | 026 | Process restart required for prompt changes; simplest mental model |

---

## Open Design Questions

Some questions are unanswered and worth exploring:

| Question | Relevant Explorations | Status |
|----------|----------------------|--------|
| Should is_self_component get direct scoring bonus? | 003, 004 | Unanswered |
| Should ATTENTION frame exclude is_self blocks? | 002, 004 | Unanswered |
| What's the right idle_factor for dual-rate decay? | 005 | Needs tuning |
| Can we measure self-alignment empirically? | 004 | Unanswered |
| What happens when self's values are challenged? | 004 | Unanswered |
| Does incremental assembly work better than top-K? | 002 | Unanswered |
| Should identity/constraint blocks bypass score ranking? | 006 | Unanswered |
| What's the right token budget? (300 vs. 600 vs. 1000) | 006 | Needs tuning |
| Should LLM synthesis replace template formatting? At what scale? | 006 | Unanswered |
| Should the seed self be stored as permanent blocks or hardcoded? | 006 | Unanswered |
| What's the right TTL for the system prompt cache? | 006 | Needs tuning |
| Should constitutional blocks appear in ALL frame types (ATTENTION, WORLD)? | 007 | Unanswered |
| How many task types do we need? (response/consolidation/pruning/enhancement/identity) | 007 | Needs validation |
| Should task type be explicit or auto-detected from query? | 007 | Unanswered |
| What's the right constitutional budget allocation? (100 vs. 150 vs. dynamic) | 007 | Needs tuning |
| Can the same block be constitutional in one context, variable in another? | 007 | Unanswered |
| Should consolidate() dedup within INBOX before comparing to MEMORY? | 008 | Unanswered |
| What is the right inbox_consolidate_threshold? (5 vs. 10 vs. 20) | 008 | Needs tuning |
| Should curate() persist a prune log for audit/recovery? | 008 | Unanswered |
| Should retrieve() reinforcement be proportional to rank? | 008 | Unanswered |
| Should there be a forget() operation for explicit deletion? | 008 | Unanswered |
| Should consolidation merge near-duplicate blocks (>0.90 similarity) or just dedup? | 008 | **Resolved in 009** — forget + create + inherit |
| What is the right active-hours threshold for curate()? (40h vs 20h vs 80h) | 009 | Needs tuning |
| What is the right block soft_cap? (starting at 50; tune upward) | 009 | Set to 50 for MVP |
| Should the superseded block log be user-accessible for review? | 009 | Unanswered |
| What if user intends B as a new concept, not a replacement (similarity=0.93)? | 009 | Unanswered |
| Should `frame("self", task_type=...)` pass task-type modifiers from exploration 007? | 015 | Likely yes — natural extension of exploration 007 |
| What is the right behaviour when ATTENTION frame has no blocks above a score threshold? | 015 | Return top-K regardless; caller checks scores |
| Should `render()` support custom template strings for power users? | 015 | Phase 2 |
| Should `FrameResult` include a `suppressed_blocks` list for debugging? | 015 | Unanswered |
| Should there be a `world` frame for general knowledge blocks? | 015 | Phase 2 |
| Should builtin frames be inspectable via `get_frame("self")`? | 016 | Probably yes; useful for debugging |
| What if `guarantee_tags` blocks exceed the token budget? | 016 | Include highest-confidence guaranteed blocks until budget exhausted |
| Should frame names be namespaced to prevent collisions? | 016 | Unanswered |
| Agent auto-suggesting frame definitions from observed call patterns? | 016 | Phase 2 |
| Split `block_embeddings` table from `blocks` now vs. at 1000-block threshold? | 017 | Unanswered |
| Should `centrality_cached` be added in Phase 1 anyway? | 017 | Unanswered — 50 blocks makes per-query trivial |
| Should `block_prune_log` table exist for audit/recovery? | 017 | Unanswered |
| Global `~/.amgs/amgs.db` vs. relative memory directory (multiple instances)? | 017 | Unanswered |
| When should DuckDB ATTACH hybrid be evaluated vs. staying pure SQLite? | 018 | Measure curate() bottleneck first |
| What is the right typed `Config` dataclass for `system_config` access? | 019 | Unanswered |
| Should PageRank replace weighted degree in Phase 1 (not Phase 2)? | 020 | 50 blocks makes it trivial — possibly yes |
| Should graph expansion widen `search_window_hours` for expanded nodes? | 021 | Unanswered — currently expansion uses same window as seeds |
| Which modules are MVP for a minimal Phase 1 build? | 022 | Unanswered — critical path not yet traced |
| Should a `memory classification` prompt after each response replace heuristic `should_learn()`? | 023 | Unanswered — promising approach |
| Should there be an EPISODIC frame separate from KNOWLEDGE in ATTENTION? | 023 | Unanswered |
| Should `task_type` auto-select different frame weight profiles? | 023 | Likely yes — natural extension of exploration 007 |
| Should `system.forget(query)` be a first-class lifecycle operation? | 023 | Unanswered |
| What does a multi-agent scenario look like — shared L1, separate SELF frames? | 023 | Unanswered |
| Should user/agent be able to set initial confidence at learn() time? | 024 | Unanswered |
| Template system: string interpolation (Phase 1) vs Jinja2-style? | 024 | Phase 1: `str.format()` |
| Active hours crash recovery: periodic flush vs session-end-only? | 024 | Unanswered |
| Does frame `extends=` inheritance survive the refactoring? | 024 | Yes — compatible with refined FrameDefinition |
| `forget()` confirmation flow: immediate vs return-candidates-first? | 024 | Unanswered |
| Should the adapter support batched LLM calls for consolidate()? | 025 | Phase 2 — serial fine for small inbox |
| Should AMGS log LLM token costs to a `llm_costs` table? | 025 | Unanswered |
| Per-method model overrides (large model for contradiction, small for alignment)? | 025 | **Resolved in 026** — `alignment_model`, `tags_model`, `contradiction_model` in `LLMConfig` |
| Should `frame()` be async (required since `embed()` is async)? | 025 | Yes — consistently async API is cleaner |
| Should custom prompt templates be stored in `system_config` table for DB-level versioning? | 026 | Unanswered — config files sufficient for Phase 1 |
| Should a prompt hash be stored alongside alignment scores for drift detection? | 026 | Unanswered — adds schema complexity; Phase 2 consideration |
| Should `begin_session()` accept a prompt override for per-session customisation? | 026 | Unanswered — Phase 2 extension |
| Should the template engine upgrade from `str.format()` to Jinja2 for complex prompts? | 026 | Phase 2 if prompts need conditionals or partials; `str.format()` sufficient for Phase 1 |

These can drive Phase 2 explorations.

---

## Reference

- **START_HERE.md:** Project entry point
- **QUICKSTART.md:** Quick overview of explorations
- **sim/README.md:** How to write and run explorations
- **docs/amgs_architecture.md:** The full elfmem specification
- **docs/notes.md:** Previous Python simulator observations

---

**Project:** [elfmem](https://github.com/yourusername/elfmem) — Self-aware memory for LLM agents
