# Title: The Layer Model

## Status: complete

## Question

Twenty-one explorations have produced design decisions across storage, graph,
retrieval, scoring, identity, lifecycle operations, frames, and tooling. These
need to be organized into a coherent architectural model before implementation.

What are the layers? How do they relate? Where do the lifecycle operations sit?
Where do external dependencies (embedding service, LLM) live? What are the
contracts between layers?

---

## What Needs to Be Organized

From the explorations, the system has these distinct concerns:

| Concern | Explorations |
|---------|-------------|
| Storage: SQLite, block files, queries, migrations | 010, 017, 018, 019 |
| Graph: edges, centrality, expansion | 013, 014, 020 |
| Retrieval: hybrid pipeline, scoring formula | 002, 003, 021 |
| Frames: definitions, rendering, composition | 015, 016 |
| Identity: SELF, constitutional, task-type, cache | 006, 007, 011, 012 |
| Lifecycle: learn, consolidate, curate, recall | 008, 009 |
| API: public interface, session management | 015, 016 |

The question is how to group and order these into layers with clean dependencies.

---

## Candidate Approaches

### Approach A: Classic three-tier

```
Presentation → Logic → Data
```

Standard and familiar. But "logic" here contains two meaningfully distinct things:
the memory manipulation logic (decay, edges, scoring) and the context assembly logic
(frame definitions, rendering, SELF identity). Collapsing them into one tier hides
important structure. **Too coarse.**

---

### Approach B: Microservices / service decomposition

Separate processes for embedding, storage, retrieval, identity. REST or gRPC
boundaries between them.

This is a local library used by one process. Network boundaries add latency without
any benefit. No horizontal scaling is needed. No team boundary requires the services
to be independently deployable. **Wrong tool for this problem.**

---

### Approach C: Capability-based (no fixed layers)

Organise by capability: Ingestion, Consolidation, Retrieval, Identity, Maintenance.
Each capability is a module; modules use shared utilities.

Problem: capabilities don't have clear dependency directions. Ingestion touches
both storage and memory logic. Retrieval touches graph, scoring, frames, and storage.
Without a dependency rule (lower layers don't import upper layers), coupling spreads
in all directions. **Good for grouping operations; bad as an architecture.**

---

### Approach D: Four horizontal layers + vertical operation slices

This is the recommended approach. It combines strict horizontal layering (for
clear dependency direction) with vertical operation slices (for organizing the
lifecycle operations that coordinate across layers).

---

## The Four Layers

```
┌──────────────────────────────────────────────────────────────────┐
│  L4  INTERFACE                                                    │
│  Public API: learn(), consolidate(), curate(), frame(), recall()  │
│  Session lifecycle, orchestration, error handling                 │
├──────────────────────────────────────────────────────────────────┤
│  L3  CONTEXT                                                      │
│  Frame definitions and registry (builtin + custom)                │
│  Composite scoring with frame-specific weights                    │
│  Frame rendering (blocks → string)                               │
│  SELF assembly: constitutional guarantee, task-type, cache        │
│  Contradiction suppression                                        │
├──────────────────────────────────────────────────────────────────┤
│  L2  MEMORY                                                       │
│  Block lifecycle (states, decay, pruning)                        │
│  Graph: edge creation, centrality, 1-hop expansion               │
│  Hybrid retrieval pipeline (pre-filter → vector → expand)        │
│  Self-tag assignment (tagging logic)                             │
│  Near-duplicate detection and inheritance                         │
├──────────────────────────────────────────────────────────────────┤
│  L1  STORAGE                                                      │
│  SQLite via SQLAlchemy Core                                       │
│  Block files (.md read/write)                                     │
│  Named query functions                                            │
│  Alembic migrations, embedding bytes↔numpy conversion            │
└──────────────────────────────────────────────────────────────────┘

EXTERNAL DEPENDENCIES (injected as protocols — not a layer)
  EmbeddingService    LLMService
```

**The dependency rule:** each layer imports only from layers below it. L3 never
imports from L4. L2 never imports from L3. L1 imports nothing from the system.
External dependencies are injected into whichever layer needs them.

---

## Each Layer in Detail

### L1 — Storage

The data access layer. Knows nothing about scoring, frames, identity, or lifecycle.
Can be replaced (e.g., SQLite → Postgres) without touching L2, L3, or L4.

**Responsibilities:**
- SQLAlchemy engine setup (exploration 019: WAL, pragmas, NullPool)
- SQLAlchemy Table definitions in `models.py` — schema source of truth
- Named query functions (no SQL strings outside this layer)
- Alembic migrations
- Block file read/write (markdown files — exploration 010)
- Embedding bytes ↔ numpy conversion (the conversion only, not the API call)

**What L1 does NOT know:**
- What a scoring weight is
- What a frame is
- What "self" means
- What lifecycle operations are

**Boundary contract:** L1 functions accept and return plain Python dicts, primitives,
and numpy arrays. No SQLAlchemy objects cross the L1 boundary. No SQL strings appear
outside L1.

```python
# L1 contract examples — plain return types
def get_active_blocks_for_scoring(conn) -> list[dict]: ...
def get_self_tagged_blocks(conn) -> list[dict]: ...
def insert_block(conn, block_data: dict) -> None: ...
def get_block_edges(conn, block_id: str) -> list[dict]: ...
```

---

### L2 — Memory

The memory domain logic layer. Knows about blocks, edges, decay, and the hybrid
retrieval pipeline. Does not know about frame definitions or rendering — it returns
scored candidate lists, not frames.

**Responsibilities:**
- Block state transitions (active → superseded → forgotten)
- Session-aware decay computation and bulk application
- Near-duplicate detection: thresholds, forget+create+inherit logic (exploration 009)
- Graph management: edge creation, degree cap enforcement, centrality (exploration 013, 020)
- Hybrid retrieval pipeline: pre-filter → embed → vector similarity → graph expansion → produce candidate list (exploration 021)
- Scoring formula as a pure function: `score(block, weights) → float`
- Self-tag assignment logic: inference pipeline, candidate/confirmed states (exploration 012)

**What L2 does NOT know:**
- How to render blocks into a string
- What a frame definition looks like
- What the SELF cache is
- Which operation called it (consolidate, curate, or recall)

**Boundary contract:** L2 returns `ScoredBlock` objects — blocks with their computed
scores and score components attached. Frame-specific weights are passed in from L3;
L2 doesn't know which frame is being served.

```python
# L2 contract examples
def compute_candidates(
    storage: StoragePort,
    embedding_service: EmbeddingService,
    query: str | None,
    filters: BlockFilter,
    weights: ScoringWeights,
    top_n: int,
) -> list[ScoredBlock]: ...

def apply_decay(storage: StoragePort, active_hours: float) -> None: ...

def create_edges_for_block(
    storage: StoragePort,
    new_block: Block,
    candidates: list[ScoredBlock],
) -> None: ...
```

The scoring weights come FROM L3 (frame definitions). L2 applies them but doesn't
know their names or origins.

---

### L3 — Context

The context assembly layer. Takes scored candidates from L2 and assembles them into
named context frames with rendered text. Knows what frames are; does not know about
database tables or embedding computation.

**Responsibilities:**
- Frame registry: builtin frames + custom frame registration (exploration 015, 016)
- Contradiction suppression: query contradictions table, suppress lower-confidence block from pairs (exploration 014, 021)
- SELF frame assembly: constitutional guarantee, task-type scoring modifiers, caching (exploration 006, 007)
- Composite scoring with frame-specific weights: calls L2's scoring formula with the frame's weights
- Frame rendering: `List[ScoredBlock] → str` using frame templates
- Token budget enforcement: greedy selection within budget (exploration 006, 015)

**What L3 does NOT know:**
- How blocks are stored
- How embeddings are computed
- What SQL queries run
- The graph structure

**Boundary contract:** L3 accepts `List[ScoredBlock]` from L2 and returns
`FrameResult(text, blocks, token_count)`. L3 is also where the SELF cache lives —
it caches `FrameResult`, not raw blocks.

```python
# L3 contract examples
def assemble_frame(
    frame_def: FrameDefinition,
    candidates: list[ScoredBlock],
    contradictions: list[ContradictionPair],
    top_k: int,
) -> FrameResult: ...

def get_self_frame(
    candidates: list[ScoredBlock],
    contradictions: list[ContradictionPair],
    task_type: str,
    token_budget: int,
) -> FrameResult: ...

def render(blocks: list[ScoredBlock], template: str, budget: int) -> str: ...
```

---

### L4 — Interface

The orchestration layer. Coordinates L2, L3, and external dependencies to implement
the four lifecycle operations and the public API. This is the only layer that
"knows the whole story."

**Responsibilities:**
- `MemorySystem` class: the single entry point for all callers
- Public API: `learn()`, `consolidate()`, `curate()`, `frame()`, `recall()`, `register_frame()`
- Session lifecycle: open session, record active hours, close session
- Orchestration of lifecycle operations (see Vertical Slices below)
- Input validation and error handling

**What L4 does NOT know:**
- Database table schemas
- Scoring formula details
- Frame rendering templates

**L4 is thin.** Its job is to orchestrate, not compute. If L4 is doing scoring,
decay computation, or rendering, something has leaked out of L2 or L3.

---

## External Dependencies: Injected as Protocols

The system calls two external services:
- **Embedding service** — `embed(text) → np.ndarray` — called at consolidation and recall()
- **LLM service** — `infer_self_tags(block, self_context) → list[str]` and
  `detect_contradiction(block_a, block_b) → float` — called at consolidation

These are NOT a layer. They are injected into the `MemorySystem` constructor
as protocol implementations. L2 uses `EmbeddingService`; L3 uses neither directly;
L4 wires everything together.

```python
class EmbeddingService(Protocol):
    async def embed(self, text: str) -> np.ndarray: ...

class LLMService(Protocol):
    async def score_self_alignment(self, block: str, self_context: str) -> float: ...
    async def infer_self_tags(self, block: str, self_context: str) -> list[str]: ...
    async def detect_contradiction(self, block_a: str, block_b: str) -> float: ...

class MemorySystem:
    def __init__(
        self,
        db_path: str,
        embedding_service: EmbeddingService,
        llm_service: LLMService,
    ): ...
```

Injecting as protocols means:
- Tests can inject mock services (no real API calls in tests)
- The embedding provider can be swapped (OpenAI → local model) without changing any layer
- L2 and L4 depend on the abstraction, not the implementation

---

## Vertical Operation Slices

The four lifecycle operations (learn, consolidate, curate, recall) are vertical
slices that coordinate across layers. They live in L4 as orchestrator functions.

### learn()

```
L4: validate markdown format
L1: content-hash the block
L1: INSERT into inbox (fail silently on duplicate hash)
L4: check inbox count → trigger consolidate() if count ≥ threshold
```

Touches: L1 only (fast path, no L2 or L3).

### consolidate()

```
L4: begin consolidation session
L1: SELECT all inbox blocks
L2: for each inbox block:
    L2: embed block content (via EmbeddingService)
    L2: compute similarity to all active MEMORY blocks
    L2: near-dup check → forget+inherit if 0.90–0.95 sim (L1 + L2)
    L2: create edges for similarity ≥ 0.60 (L1 + L2)
    L2: score self-alignment (via LLMService)
    L2: infer candidate self-tags (via LLMService, if self_alignment ≥ 0.75)
    L2: determine decay_lambda from tags
L1: INSERT block + tags into MEMORY
L1: DELETE from inbox
L4: end
```

Touches: L1, L2, external dependencies. L3 not involved (no frames assembled).

### curate()

```
L4: check trigger conditions (active_hours ≥ 40 OR block_count > 50)
L1: SELECT all active blocks + edge data
L2: apply decay to all blocks (bulk update via L1)
L2: delete blocks where decay_weight < 0.05 (L1 CASCADE handles edges)
L2: apply decay to edges; delete edges where weight < 0.10
L3: promote candidate self-tags meeting criteria (via LLMService)
L2: reinforce top-scoring blocks (curate() bonus)
L2: (Phase 2) recompute PageRank via networkx; write centrality_cached
L1: UPDATE sessions record (end of active period)
```

Touches: L1, L2, L3. The tag promotion step (L3) is the only L3 involvement.

### recall()

```
L4: get frame definition from L3
L4: get contradiction pairs from L1 (for the SELF frame bypass)
L2: run hybrid retrieval pipeline
    L1: pre-filter query (status + time_window + frame filters)
    external: embed query (EmbeddingService) — if query provided
    L2: cosine similarity → N_seeds
    L1: graph expansion — neighbours of seeds
    L2: composite scoring of seeds + expansion
L3: assemble_frame(frame_def, candidates, contradictions, top_k)
    L3: contradiction suppression
    L3: token budget enforcement
    L3: rendering (if called via frame(), not recall())
L1: reinforce returned blocks + co-retrieved edges (one transaction)
L4: return FrameResult or List[ScoredBlock]
```

Touches: L1, L2, L3, external embedding service. The most complex slice.

---

## Module Layout

```
amgs/
├── __init__.py
├── api.py                       # L4 — MemorySystem public interface
├── session.py                   # L4 — session lifecycle
│
├── ports/                       # External dependency abstractions (not a layer)
│   ├── embedding.py             # EmbeddingService protocol
│   └── llm.py                   # LLMService protocol
│
├── adapters/                    # Concrete implementations of ports
│   ├── openai_embedding.py
│   └── anthropic_llm.py
│
├── operations/                  # L4 — vertical operation slices
│   ├── learn.py
│   ├── consolidate.py
│   ├── curate.py
│   └── recall.py
│
├── context/                     # L3
│   ├── frames.py                # Frame registry, FrameDefinition, custom frame CRUD
│   ├── scoring.py               # Applies frame weights to ScoredBlock candidates
│   ├── rendering.py             # ScoredBlock list → str (templates)
│   ├── self_frame.py            # SELF assembly: constitutional, task-type, cache
│   └── contradiction.py         # Contradiction suppression
│
├── memory/                      # L2
│   ├── blocks.py                # Block lifecycle, state transitions
│   ├── decay.py                 # Decay formula, session-aware bulk update
│   ├── graph.py                 # Edge creation, centrality, 1-hop expansion
│   ├── pipeline.py              # Hybrid retrieval pipeline (pre-filter → vector → expand → score)
│   ├── scoring.py               # score(block, weights) → float (pure function)
│   ├── tagging.py               # Self-tag inference and promotion logic
│   └── dedup.py                 # Near-duplicate detection, forget+create+inherit
│
├── db/                          # L1
│   ├── engine.py                # SQLAlchemy engine, pragma listener
│   ├── models.py                # Table definitions (schema source of truth)
│   ├── queries/
│   │   ├── blocks.py
│   │   ├── edges.py
│   │   ├── frames.py
│   │   ├── inbox.py
│   │   └── recall.py
│   └── migrations/              # Alembic versions
│       ├── env.py
│       └── versions/
│           └── 001_initial_schema.py
│
└── files.py                     # L1 — block .md file read/write
```

---

## Layer Boundary Contracts in Code

The boundaries are enforced by the types that cross them:

```
L1  →  L2:  dict (row as mapping), np.ndarray (embedding)
L2  →  L3:  ScoredBlock (block data + computed scores + score components)
L3  →  L4:  FrameResult (text, blocks, token_count, frame_name)
L4  →  caller: FrameResult or List[ScoredBlock]
```

**Nothing from SQLAlchemy crosses the L1 boundary.** Query functions return
`list[dict]`, not `Row` or `MappedResult` objects. The caller never sees
SQLAlchemy internals.

**Nothing about frame definitions crosses into L2.** L2's `compute_candidates()`
accepts `ScoringWeights` (a plain dataclass with five floats). It doesn't know
the weights came from a frame called "attention."

**Scoring weights flow down, scored results flow up:**

```python
# L3 reads frame definition:
frame_def = frame_registry.get("attention")   # L3

# L3 passes weights down to L2:
candidates = pipeline.compute_candidates(
    ...,
    weights=frame_def.weights,  # L3 → L2: just a dict of floats
    ...
)

# L2 returns scored candidates to L3:
# candidates: List[ScoredBlock]

# L3 assembles frame:
result = assemble_frame(frame_def, candidates, contradictions, top_k)
# result: FrameResult(text, blocks, token_count)

# L4 returns to caller:
return result
```

---

## Why Four Layers, Not Three

The critical separation is between L2 (Memory) and L3 (Context).

Without this separation, the scoring formula, frame definitions, rendering templates,
SELF cache logic, constitutional block guarantees, and task-type modifiers all pile
into one "business logic" layer. That layer would be:
- Too large to reason about
- Mixing retrieval concerns (vectors, edges) with presentation concerns (templates, caching)
- Impossible to test scoring independently of rendering

With the separation:
- L2 can be tested with no knowledge of frames: "given these weights, does this candidate list come out right?"
- L3 can be tested with mock candidates: "given these ScoredBlocks, does the frame render correctly with the right budget enforcement?"
- The SELF cache lives in L3 where it belongs (caching rendered identity text), not in L2 where it would be contaminating retrieval logic

---

## Testing Strategy Per Layer

| Layer | Test approach | What to inject |
|-------|--------------|---------------|
| L1 | In-memory SQLite (StaticPool), test query functions directly | Nothing — L1 has no dependencies on other layers |
| L2 | Mock L1 (return fake block dicts), mock EmbeddingService | `StoragePort` stub, `EmbeddingService` returning fixed vectors |
| L3 | Provide fake `List[ScoredBlock]`, check rendered output | No mocking needed — L3 is pure logic on data structures |
| L4 | Integration test with in-memory SQLite + mock external services | Mock `EmbeddingService`, mock `LLMService` |

L3 is notable: it has no external dependencies and takes data structures as input.
It is the most unit-testable layer — pure functions on typed inputs.

---

## Design Decisions from this Exploration

| Decision | Rationale |
|----------|-----------|
| Four horizontal layers: Interface, Context, Memory, Storage | Three tiers too coarse; microservices wrong scope; four layers match natural concern boundaries |
| Dependency direction: L4 → L3 → L2 → L1 only (never upward) | Prevents coupling; lower layers remain independent and reusable |
| L1 returns plain dicts — no SQLAlchemy objects cross the boundary | L2 and above are completely independent of the ORM/query framework |
| L2 returns `ScoredBlock` — no frame knowledge in Memory layer | Retrieval and context assembly are cleanly separated |
| L3 returns `FrameResult` — rendering is a Context layer responsibility | L4 and callers never perform text assembly |
| Lifecycle operations are vertical slices in L4, not a layer | They orchestrate across layers; a "lifecycle layer" would create circular dependencies |
| External dependencies injected as protocols into `MemorySystem.__init__()` | Swappable providers; mockable in tests; no coupling to specific API vendors |
| `memory/scoring.py` is a pure function in L2 | Weights come from L3 frames; computation is L2's job; pure function = trivially testable |
| SELF frame assembly lives in L3 (`context/self_frame.py`) | SELF IS a frame (specialized); its cache, constitutional logic, and task-type modifiers are presentation concerns |
| `ports/` and `adapters/` sit outside the four layers | External dependency abstractions are cross-cutting infrastructure, not a domain layer |

---

## Open Questions

- [ ] Should L4 be async throughout (using `async def`)? consolidate() and the LLM
      calls within it are naturally async. If L4 is async, does L2 need to be async
      too? (Likely yes — `EmbeddingService.embed()` is async; L2 pipeline calls it)
- [ ] Should `StoragePort` be an explicit protocol that L2 depends on (for testability)
      or should L2 import L1 query functions directly? (Protocol is cleaner but adds
      a layer of indirection; for Phase 1, direct import may be simpler)
- [ ] Should `MemorySystem` be a singleton or instanciated per session? (Per-session
      makes test isolation easier; singleton matches typical library usage)
- [ ] Where does the `analysis/` optional module live? (Likely outside the four layers —
      a separate top-level package that imports L1 and L2 but is not a runtime dependency)

---

## Variations

- [ ] Draw the dependency graph for the recall() operation: which modules in which
      layers are imported, in what order, with what data flowing between them.
- [ ] What does a minimal Phase 1 implementation look like — which modules can be
      left as stubs initially and which are critical path?
- [ ] Trace the consolidate() operation through the layer model as a sequence
      diagram: which layer is active at each step, what data is passed between layers.
