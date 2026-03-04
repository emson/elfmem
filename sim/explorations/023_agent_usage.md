# Title: Agent Usage Patterns

## Status: complete

## Question

The AMGS library provides memory blocks, decay, frames, a graph layer, hybrid
retrieval, and lifecycle operations. But how does an actual LLM application use
this? What does an agent look like from the outside? How does knowledge
accumulate and evolve over real sessions? Does the design hold up under realistic
usage?

Evaluate the agent model end-to-end: from agent creation through multi-session
evolution, across different archetypes.

---

## The Agent's Relationship to AMGS

AMGS is a **memory substrate**. The agent is the reasoning layer on top.

```
┌────────────────────────────────────────────────────────┐
│                   AGENT PROCESS                         │
│                                                         │
│  User message                                           │
│       │                                                 │
│       ▼                                                 │
│  ┌──────────────┐     calls      ┌─────────────────┐   │
│  │ Orchestrator │ ─────────────▶ │  AMGS Library   │   │
│  │  (LLM logic) │ ◀───────────── │  (memory layer) │   │
│  └──────────────┘   FrameResult  └─────────────────┘   │
│       │                                                 │
│       ▼                                                 │
│  Build prompt → LLM → Response                         │
│       │                                                 │
│       ▼                                                 │
│  Optionally: learn(new_knowledge)                       │
└────────────────────────────────────────────────────────┘
```

The agent makes three kinds of AMGS calls:
1. **Read**: `frame()` — assemble context before generating
2. **Write**: `learn()` — store something worth remembering after generating
3. **Maintain**: `consolidate()`, `curate()` — scheduled or triggered housekeeping

The LLM never touches SQLite. It never calls decay functions. It only sees
`FrameResult.text` strings and decides what to `learn()`.

---

## The Per-Turn Pattern

Every agent turn follows this sequence:

### Before generating

```python
async def run_turn(session, user_message):
    # 1. Always assemble identity first (cached — fast, ~0ms)
    self_ctx  = system.frame("self")

    # 2. Retrieve relevant knowledge (hybrid pipeline — ~20-50ms)
    attn_ctx  = system.frame("attention", query=user_message)

    # 3. Retrieve task/goal context (lighter retrieval)
    task_ctx  = system.frame("task", query=user_message)

    # 4. Build prompt
    prompt = build_prompt(
        system=self_ctx.text,
        memory=attn_ctx.text,
        task=task_ctx.text,
        history=session.recent_turns(n=6),
        user=user_message,
    )

    # 5. Generate
    response = await llm.complete(prompt)
    return response
```

### After generating

```python
    # 6. Decide what to remember
    if should_learn(user_message, response):
        system.learn(
            content=extract_knowledge(response),
            tags=infer_tags(user_message),
            category="knowledge",
        )

    # 7. Reinforcement happened automatically inside frame() for blocks used
    # 8. Session turn recorded automatically
    return response
```

`should_learn()` is agent-specific. A minimal implementation: learn anything
the LLM explicitly flags, or anything that corrects a previous belief.
A more sophisticated implementation: ask the LLM to classify its own response.

---

## Worked Example: Python Coding Assistant

**Agent**: A Python coding assistant. Three months of history. 43 active blocks.

### Memory state at session start

**SELF blocks** (5, constitutional + learned):

| ID | Content summary | confidence | self_alignment | hours_since_reinf |
|----|----------------|-----------|---------------|-------------------|
| S1 | Prefer functional patterns over stateful classes | 0.85 | 0.91 | 312 |
| S2 | Always explain the *why* behind code choices | 0.78 | 0.88 | 312 |
| S3 | User (Alex) prefers concise code, dislikes boilerplate | 0.72 | 0.82 | 48 |
| S4 | Expertise: async Python, SQLAlchemy, data pipelines | 0.90 | 0.94 | 312 |
| S5 | When uncertain, flag it explicitly rather than guessing | 0.68 | 0.79 | 312 |

**Selected knowledge blocks** (relevant subset shown):

| ID  | Content summary | confidence | hours_since_reinf | edges_to |
|-----|----------------|-----------|-------------------|----------|
| K7  | Alex's stack: FastAPI + SQLAlchemy + Celery | 0.85 | 8 | K12, K18 |
| K12 | Celery Beat: Alex hit timezone-naive datetime bug | 0.62 | 120 | K7, K15, K18 |
| K15 | celery-once library: prevents duplicate task execution via Redis lock | 0.58 | 340 | K12 |
| K18 | Alex's timezone config: UTC everywhere, aware datetimes | 0.71 | 120 | K12 |
| K23 | Task idempotency patterns: at-least-once vs exactly-once delivery | 0.71 | 55 | K15 |
| K31 | Database row locking: SELECT FOR UPDATE, advisory locks | 0.64 | 200 | K23 |

---

### Turn: "The Celery task is firing twice — I think it's a race condition"

**Stage 1: Pre-filter**

Active blocks with `hours_since_reinforcement < 200`: 36 of 43 pass.
K15 (340h) is filtered out. K31 (200h) is right at boundary — included.

**Stage 2: Vector similarity** (query = "Celery task firing twice race condition")

Top 20 seeds (N_seeds = top_k × 4 = 5 × 4):

| Block | similarity |
|-------|-----------|
| K23   | 0.71 (idempotency, task delivery) |
| K12   | 0.68 (Celery, prior session bug) |
| K7    | 0.65 (Alex's Celery stack) |
| K31   | 0.58 (locking, race conditions) |
| ...   | ... |

K15 was filtered in stage 1. It would have scored similarity ≈ 0.74 (most
directly relevant) but is invisible because it hasn't been touched in 340 hours.
**This is a known failure mode — addressed below.**

**Stage 3: Graph expansion** (1-hop from seeds)

Seeds K12 and K23 have edges to K15 and K18. K15 is in the *graph* even though
it failed the time-window pre-filter. The graph expansion fetches scoring fields
only — no embedding required.

Expansion candidates added with `similarity = 0`:

| Block | via seed | centrality |
|-------|---------|-----------|
| K15   | K12, K23 | 0.72 |
| K18   | K12     | 0.55 |

The graph recovered K15 after the pre-filter discarded it. This is the precise
scenario the hybrid pipeline was designed for.

**Stage 4: Composite scoring** (ATTENTION frame weights)

```
score = 0.35·sim + 0.25·conf + 0.20·decay_factor + 0.20·centrality
```

| Block | sim  | conf | decay(d) | central | score |
|-------|------|------|----------|---------|-------|
| K23   | 0.71 | 0.71 | 0.93     | 0.41    | 0.70  |
| K7    | 0.65 | 0.85 | 0.98     | 0.35    | 0.69  |
| K12   | 0.68 | 0.62 | 0.80     | 0.68    | 0.68  |
| K15   | 0.00 | 0.58 | 0.67     | 0.72    | 0.43  | ← graph-only
| K18   | 0.00 | 0.71 | 0.80     | 0.55    | 0.41  | ← graph-only

Top 5 returned. **K15 and K18 would be absent without graph expansion.**

**Prompt assembled** (~2840 tokens):

```
[SELF — 380 tokens]
You prefer functional patterns. You explain the why behind choices.
The user (Alex) likes concise code. You have expertise in async Python,
SQLAlchemy, and data pipelines. When uncertain, flag it explicitly.

[ATTENTION — 1840 tokens]
## Task Idempotency Patterns
...at-least-once delivery means tasks may execute more than once.
Design tasks to be idempotent: same input → same effect regardless of retries...

## Celery Beat Timezone Bug (Prior Session)
...Alex encountered this before: datetime.utcnow() produces timezone-naive
datetimes; Celery Beat interprets these incorrectly when DST is active.
Fix: use timezone-aware datetimes throughout (datetime.now(tz=UTC))...

## Alex's Project Stack
...FastAPI + SQLAlchemy + Celery. PostgreSQL backend. Redis as broker...

## celery-once: Duplicate Task Prevention
...prevents concurrent duplicate execution via Redis lock. Lock key derived
from task name + arguments. Useful when at-most-once semantics are needed...

## Alex's Timezone Configuration
...UTC everywhere policy. All datetimes timezone-aware. Confirmed working...

[TASK — 620 tokens]
Current session goal: debug Celery double-execution
Recent turns: (none — first turn of session)
```

**LLM response** identifies two candidates: (1) visibility timeout shorter than
task runtime causing re-delivery, (2) missing idempotency guard. Recommends
checking `CELERY_TASK_ACKS_LATE` and `visibility_timeout`. Mentions celery-once
as exact-semantics option if needed.

**After response:**

```python
system.learn(
    content="""Celery double-fire root cause: if visibility_timeout < task
    runtime, broker re-delivers the message while the first worker is still
    executing. The second worker picks it up. Fix: set visibility_timeout
    to max(task_runtime) * 1.5, or use CELERY_TASK_ACKS_LATE=False with
    idempotent task design.""",
    tags=["celery", "race-condition", "distributed-systems", "reliability"],
    category="knowledge",
)
```

Reinforcement: K23, K7, K12, K15, K18 all get `hours_since_reinforcement = 0`.
K15 in particular recovers from 340h → 0h, saving it from eventual pruning.

---

## Session Lifecycle

### Session begin

```python
session = system.begin_session(
    session_id="2026-03-04-alex-coding",
    task_type="coding",          # selects TASK frame template
    task_description="Debug Celery race condition",
)
```

`begin_session()` does three things:
1. Inserts row in `sessions` table with `started_at`
2. Updates SELF frame cache if stale (age > `cache_ttl`, default 3600s)
3. Logs `task_type` for self-alignment scoring later

### Session end

```python
system.end_session(session_id)
```

`end_session()` does:
1. Marks session `ended_at`
2. Computes blocks used this session → bump `reinforcement_count`
3. Queues inbox items from `learn()` calls (already queued turn-by-turn)
4. Triggers `consolidate()` if `inbox_size > consolidate_threshold` (default: 10)
5. Triggers `curate()` if `last_curate` was more than `curate_interval` ago (default: 24h)

### The consolidation cycle

```python
# consolidate() — runs after session ends or when inbox is large
def consolidate():
    inbox_items = load_inbox()
    for item in inbox_items:
        # LLM: is this a near-duplicate of an existing block?
        dup = dedup_check(item)
        if dup:
            merge_or_discard(item, dup)
            continue
        # LLM: does this contradict any existing block?
        contradiction = contradiction_check(item)
        if contradiction:
            flag_contradiction(item, contradiction)
        # Assign self-alignment score
        item.self_alignment = score_self_alignment(item, self_frame)
        # Promote to active
        activate(item)
```

---

## Multi-Session SELF Evolution

The SELF frame is not static. It evolves through three mechanisms:

1. **Self-alignment rescoring**: every `consolidate()` run rescores all SELF
   blocks against the current system prompt (constitutional + task-type blocks)
2. **Decay**: SELF blocks have slow λ (default 0.0001) but non-zero — blocks
   that are never reinforced eventually fade
3. **New self-inference**: after consolidate(), LLM reviews recently promoted
   knowledge blocks and asks: "does this reveal something about my identity
   or values?" → new SELF block candidates sent to inbox

### Three-session evolution trace

**Session 1** (Week 1): First session with Alex. No prior knowledge.

```
SELF blocks: 3 constitutional (from agent creation)
  - "I give accurate, well-reasoned responses"
  - "I flag uncertainty explicitly"
  - "I prefer clean, maintainable code"

After session:
  - 4 knowledge blocks learned (Alex's stack, preferences, one bug fix)
  - consolidate() promotes all 4
  - self_alignment scoring: K3 "Alex likes concise code" scores 0.61 self_alignment
    (below the 0.70 threshold for SELF promotion — remains knowledge block)
```

**Session 12** (Week 4): Twelve sessions of coding work.

```
SELF blocks: 5 (3 original + 2 learned)
  - Original 3 (reinforced, confidence rising)
  - S3: "The user (Alex) prefers concise code, dislikes boilerplate" (self_alignment=0.82)
    — crossed threshold after being reinforced across multiple sessions
  - S4: "Expertise: async Python, SQLAlchemy, data pipelines"
    — inferred from pattern of questions asked; high self_alignment=0.94

Knowledge blocks: 27 active
  - Alex's project patterns, recurring error types, architectural preferences
  - Graph has 34 edges; 3 dense clusters (Celery, DB, API design)
```

**Session 47** (Month 3): Mature agent with rich memory.

```
SELF blocks: 6 (stable — S5 added: "prefer to test assumptions before debugging")
Knowledge blocks: 43 active, 11 pruned (faded, never reinforced)
Graph: 89 edges, clear hub structure
  - Hub: K7 (Alex's stack) — degree 12, centrality 0.91
  - Bridge: K23 (idempotency) — connects Celery cluster to DB cluster
  - Isolate: K39 (obscure WSGI detail, never reinforced) — prune candidate

SELF evolution summary:
  - 2 constitutional blocks have increased confidence (0.60 → 0.85)
    because Alex's interactions have consistently reinforced them
  - 0 constitutional blocks have decayed or been contradicted
  - 3 learned SELF blocks emerged from the knowledge graph
  - Agent's behaviour is now meaningfully adapted to Alex
    in ways that were not pre-programmed
```

---

## Three Agent Archetypes

### Archetype 1: Task-Specialist (coding assistant, research assistant)

**Dominant frames**: ATTENTION + TASK. SELF is stable but secondary.

**Memory pattern**: Knowledge blocks accumulate domain-specific facts. Graph
clusters form around problem areas (e.g. Celery cluster, DB cluster). Decay
works well — old task context fades, recurring patterns survive.

**learn() strategy**: Learn whenever a bug fix, gotcha, or design decision is
established. Conservative: prefer precision over recall.

**Works well because**: The domain is narrow, embeddings are precise, graph
clusters are coherent. Retrieval surfaces the right context with high precision.

---

### Archetype 2: Persistent Companion (long-horizon relationship)

**Dominant frames**: SELF. ATTENTION used for episodic memory retrieval.

**Memory pattern**: SELF blocks are the primary artefact. They encode user
preferences, shared history, communication style. Knowledge blocks encode
episodic events ("Alex got the promotion", "prefers morning meetings").

**learn() strategy**: Learn anything that reveals the user's state, preferences,
or values. Liberal: prefer recall over precision. Consolidation handles noise.

**Works well because**: SELF block decay is slow, so identity is stable. Graph
edges between episodic blocks surface related memories. The system can surface
"you mentioned X three months ago" through centrality alone.

**Risk**: SELF blocks can become stale if the user changes. Explicit
contradiction detection is critical here — "Alex no longer likes X" must
create a contradiction that gets resolved in consolidate().

---

### Archetype 3: Knowledge Accumulator (research / document analysis)

**Dominant frames**: ATTENTION + TASK. SELF minimal (professional, neutral).

**Memory pattern**: Heavy block ingestion via `learn()`. Large corpus. Graph
becomes the primary navigation structure — blocks are summaries, edges are
semantic relationships.

**learn() strategy**: Learn every meaningful claim or finding. Volume is high;
deduplication and contradiction detection are load-bearing.

**Works well because**: Graph centrality identifies the most important ideas
(hubs = foundational concepts). Decay ensures outdated findings fade. The hybrid
pipeline handles large corpora because pre-filter + vector reduces search space.

**Risk**: Inbox overflow. If learn() is called hundreds of times per session,
consolidate() becomes expensive. Mitigation: `max_inbox_size` with aggressive
batching and near-duplicate suppression before LLM consolidation.

---

## Evaluation

### What Works Well

**1. Identity persistence across context resets**

The SELF frame survives indefinitely. Session 47's agent has coherent identity
not because the LLM remembers previous conversations (it doesn't), but because
SELF blocks have been continuously reinforced across sessions. The SELF frame
is the agent's memory of itself.

**2. Decay as implicit prioritisation**

Unused knowledge silently fades. The agent in session 47 has 43 *active* blocks,
but 11 have been pruned. Those 11 were things learned once that were never used
again. Decay did the right thing without any explicit curation instruction.

**3. Graph expansion for related context**

K15 (celery-once) was recovered from a 340h stale state purely through graph
edges. The LLM received exactly the right context without the user having to
re-explain it. This is the most direct demonstration that the graph layer adds
genuine value beyond pure vector retrieval.

**4. Self-alignment scoring as identity filter**

The SELF frame only contains blocks that score above 0.70 self-alignment.
Alex's concise-code preference crossed that threshold organically through
repeated reinforcement. The agent's "personality" is shaped by actual behaviour,
not just pre-programmed instructions.

**5. Token budget enforcement**

The agent never exceeds context window limits from memory. The token budgets
(SELF: 600, ATTENTION: 2000, TASK: 800) ensure that even with 43 active blocks,
the LLM sees only the most relevant subset.

---

### Failure Modes

**F1: The cold start problem**

Session 1 with no prior knowledge: SELF has 3 generic constitutional blocks,
ATTENTION and TASK return nothing. The agent is effectively a vanilla LLM with
extra latency. There is no value-add until enough blocks have accumulated.

**Mitigation**: Seed agents with domain-specific constitutional blocks at
creation. Coding assistant gets 5–10 pre-written SELF blocks covering language
preferences, error handling style, etc. The cold start is shorter.

---

**F2: Pre-filter kills relevant blocks**

K15 (340h since reinforcement) failed the pre-filter in stage 1. If it had
*no* graph edges, it would have been permanently invisible despite being the
most relevant block for the query. The graph saved it this time, but an isolated
block that goes stale is silently lost.

**Mitigation A**: Widen `search_window_hours` for frames that prioritise recall
over precision (e.g. TASK frame: 400h window; ATTENTION: 200h).

**Mitigation B**: `guarantee_tags` on the frame definition. If the TASK frame
has `guarantee_tags=["project-stack"]`, K7 and its neighbours are always
included regardless of staleness.

**Mitigation C**: Graph expansion as the safety net (current design). The
combination of pre-filter + graph expansion means: blocks with graph connections
survive staleness; only truly isolated stale blocks are lost. This is acceptable.

---

**F3: learn() called too aggressively**

A poorly implemented agent calls `learn()` on every turn: "user said hello",
"user asked about X", "I responded with Y". The inbox grows unboundedly.
Consolidate() becomes the bottleneck. The knowledge graph becomes noisy.

**Mitigation A**: `max_inbox_size` config. If inbox exceeds threshold, new
`learn()` calls are dropped until consolidate() runs.

**Mitigation B**: Near-duplicate suppression in consolidate() catches redundant
blocks before they pollute the graph.

**Mitigation C**: LLM should be given explicit criteria for what to learn.
A system prompt instruction: "Only call learn() for facts you could not have
inferred from common knowledge — user-specific patterns, project-specific
decisions, surprising findings."

---

**F4: SELF rigidity — identity can't adapt**

SELF blocks have λ ≈ 0.0001 (very slow decay). If the user's preferences
change ("Alex now prefers verbose, well-documented code"), the old SELF block
("Alex prefers concise code") continues to dominate because it has been highly
reinforced. The new preference has to fight against accumulated history.

**Mitigation**: Contradiction detection in consolidate(). A new block stating
"Alex prefers verbose documentation" should create a contradiction with the old
SELF block. Contradiction resolution explicitly demotes the old block and
promotes the new one, regardless of reinforcement history.

This requires the LLM to learn the *changed* preference, which means the agent
must see the contradiction and produce a block that explicitly contradicts the old
one. This puts responsibility on learn() quality.

---

**F5: Retrieval confidence over-trust**

The LLM sees retrieved blocks as "things I remember" and may over-trust them.
A block with confidence 0.58 and decay factor 0.67 represents a weakly-held,
fading memory. But it appears in the prompt as confident prose.

**Mitigation**: Include metadata in rendered blocks. The ATTENTION frame template
can render: `## Celery Once Library *(confidence: 0.58, last seen: 14 days ago)*`.
The LLM can calibrate trust based on explicitly stated confidence.

This is a rendering decision in the frame template, not a scoring change.

---

**F6: Embedding drift**

Blocks stored in session 1 used the embedding model's representation of that
content. If the embedding model is updated, old blocks have incompatible
embeddings. Vector similarity comparisons become meaningless.

**Mitigation**: `embedding_model` column in the `blocks` table. On model update:
1. New embeds computed lazily (on next recall, update the embedding)
2. Or bulk re-embed at a curate() pass triggered by config version change.
3. Track version in `system_config`. Old and new model versions can coexist
   during migration.

---

### Summary Evaluation

| Concern | Assessment |
|---------|-----------|
| Identity persistence | **Strong** — SELF frame designed exactly for this |
| Knowledge accumulation | **Strong** — decay + reinforcement calibrate naturally |
| Graph recovery of related context | **Strong** — demonstrated in worked example |
| Cold start | **Weak** — mitigated by seeding, but unavoidable |
| Stale isolated blocks | **Moderate** — graph helps; truly isolated blocks lost |
| SELF adaptation to change | **Moderate** — requires contradiction detection to work well |
| Inbox management | **Moderate** — agent discipline + max_inbox_size + dedup |
| Retrieval confidence calibration | **Moderate** — solvable via template rendering |
| Embedding drift | **Low risk** — solvable via model versioning in schema |
| Broad-domain agents | **Weak** — large corpora reduce retrieval precision |

The design is strongest for **narrow-domain, long-horizon agents** where the
domain produces coherent knowledge clusters, the user's patterns are learnable,
and sessions accumulate over months. It is weakest for general-purpose assistants
where the retrieval precision degrades with corpus size and domain variety.

---

## Locked Design Decisions

| Decision | Rationale |
|----------|-----------|
| Agent calls `frame()` before every generation, not once per session | Query changes each turn; context must be retrieved fresh per turn |
| SELF frame is assembled once per session (cached), not per turn | SELF is identity — it should not fluctuate within a session |
| `learn()` goes to inbox, not directly to active | Dedup and contradiction checks required before promotion |
| `consolidate()` triggered by inbox size OR session end, not turn count | Turn count is a proxy; inbox size is the actual load signal |
| `guarantee_tags` on TASK frame for project-critical blocks | Pre-filter can drop critical context; guarantee_tags is the override |
| Confidence rendered in ATTENTION frame template | LLM must be able to calibrate trust on retrieved blocks |
| `embedding_model` column in `blocks` table | Required for managing embedding drift across model updates |
| Agent seeded with domain SELF blocks at creation | Mitigates cold start; sets baseline identity before any sessions |
| `should_learn()` is agent-defined, not library-defined | The library cannot know what is worth storing — only the agent can |

---

## Open Questions

1. **learn() quality gate**: should the LLM classify its own output before
   calling learn()? A "memory classification" prompt after each response
   ("is anything in this response worth storing as a memory block?") could
   replace heuristic `should_learn()`.

2. **Frame composition for complex tasks**: what if one query benefits from
   both episodic memory (past conversations) and factual knowledge? Today's
   ATTENTION frame mixes these. Should there be an EPISODIC frame separate
   from KNOWLEDGE?

3. **Session types and frame selection**: a debugging session has different
   memory needs than a planning session. Should `task_type` in `begin_session()`
   auto-select different frame weight profiles? (Pre-configuring this per
   task_type is a form of custom frames from exploration 016.)

4. **Forgetting by instruction**: can the user say "forget what I told you
   about X"? This requires targeted block pruning, not just decay. A
   `system.forget(query)` operation — retrieve relevant blocks, confirm with
   user, set status='archived'.

5. **Multi-agent scenarios**: two agents sharing a knowledge graph (e.g.
   a planner agent and an executor agent). Shared L1 storage, separate SELF
   frames. What isolation is needed? Are cross-agent edge types useful?
