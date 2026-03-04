# Title: How Are New Context Frames Made?

## Status: complete

## Question

Exploration 015 established three named frames: `self`, `attention`, `task`.
These cover the core use cases. But what if an application needs a frame tailored
to a specific domain — "code review context", "meeting prep context", "security audit"?

How would a caller define and register a new frame? What properties are configurable?
Where are custom frame definitions stored? What can be customised vs. what is fixed?

What's the difference between an ad-hoc inline call and a registered named frame?

---

## What Makes a Frame (Recap)

From exploration 015, a frame has four properties:

```
scoring_weights   — how the five components are weighted for this frame
filter            — which blocks are eligible candidates
template          — how selected blocks are rendered to a string
token_budget      — hard upper bound on rendered output
```

Everything else follows from these four. Creating a new frame means providing values
for all four.

---

## Three Approaches to Creating a New Frame

### Approach A: Ad-hoc inline weights on `recall()`

Pass scoring weights directly to `recall()`. No name, no persistence, no reuse.

```python
blocks = memory.recall(
    query="function signature review",
    weights={
        "similarity":    0.50,
        "confidence":    0.25,
        "recency":       0.05,
        "centrality":    0.10,
        "reinforcement": 0.10
    },
    filter_tags=["knowledge/technical"]
)
text = memory.render(blocks, template="attention")
```

**What it enables:** One-off retrieval with custom scoring. No need to register anything.

**What it doesn't enable:** Reuse. The next call with the same intent must repeat
the same weights. No caching. No guarantee-blocks. No custom template.

**Verdict:** Useful as a power-user escape hatch. Cheap to implement (just add
`weights` parameter to `recall()`). Not a replacement for registered frames.

---

### Approach B: Named registration (persistent, reusable)

Register a frame definition once. Reference it by name in all future calls.

```python
memory.register_frame(
    name="code_review",
    weights={
        "similarity":    0.50,
        "confidence":    0.20,
        "recency":       0.05,
        "centrality":    0.10,
        "reinforcement": 0.15
    },
    filter_tags=["knowledge/technical", "self/style"],
    template="attention",   # reuse the attention rendering template
    token_budget=1500
)

# Later, anywhere in the application:
result = memory.frame("code_review", query="how should I name this parameter?")
```

**What it enables:** Reusable named frames. Any part of the application (or agent)
can call `memory.frame("code_review")` without repeating the definition. Stored
in the database, survives restarts.

**Verdict:** The primary mechanism for custom frames. Named registration is the
right default for any frame that will be used more than once.

---

### Approach C: Inheritance (extend a named frame)

New frames extend an existing one. Only override what changes.

```python
memory.register_frame(
    name="code_review",
    extends="attention",        # inherit all of attention's defaults
    overrides={
        "weights": {"similarity": 0.50, "recency": 0.05},
        "filter_tags": ["knowledge/technical"],
        "token_budget": 1500
    }
)
```

Inheritance resolves at registration time, not at call time. The stored frame
definition contains the fully-resolved configuration — there is no runtime
inheritance chain. If the parent frame changes, child frames are NOT automatically
updated.

**Why resolve at registration, not at call time:** Runtime inheritance chains create
spooky action at a distance. If `attention` frame weights change, every frame that
inherits from it would silently change behaviour. Registering the resolved definition
makes the frame explicit and stable.

**Verdict:** Ergonomic for the common case where a custom frame is "attention with a
narrower filter." Worth including in the API as syntactic sugar over Approach B.

---

## The FrameDefinition Schema

A minimal but complete frame definition:

```python
@dataclass
class FrameDefinition:
    name:             str                     # unique identifier, used in frame() calls
    weights:          ScoringWeights          # must sum to 1.0 (validated; auto-normalised if not)
    filter_tags:      List[str] | None        # None = all blocks; glob patterns supported ("self/*")
    filter_category:  str | None              # None = all categories; e.g. "knowledge/*"
    template:         str                     # "self" | "attention" | "task" | inline template string
    token_budget:     int                     # hard cap on rendered output
    guarantee_tags:   List[str]               # blocks with these tags always included (like constitutional)
    cache_ttl:        int | None              # None = no caching; seconds if cached
    source:           str                     # "builtin" | "user" | "agent"
```

### `weights` validation

The five components (recency, centrality, confidence, similarity, reinforcement)
must sum to 1.0. If the caller provides relative values that don't sum to 1.0,
auto-normalisation applies:

```
provided:  {"similarity": 5, "recency": 2, "confidence": 1}
missing:   {"centrality": 0, "reinforcement": 0}  ← default to 0

raw sum = 5 + 2 + 1 + 0 + 0 = 8
normalised:
  similarity    = 5/8 = 0.625
  recency       = 2/8 = 0.250
  confidence    = 1/8 = 0.125
  centrality    = 0
  reinforcement = 0
```

Auto-normalisation makes the API forgiving. Callers don't need to think in
proportions — they can express emphasis ("similarity is 5× more important than
recency") and the system handles the constraint.

### `filter_tags` glob patterns

Filter tags support simple glob patterns so callers don't need to enumerate every
tag in a namespace:

```
"self/*"          → matches self/constitutional, self/value, self/style, etc.
"knowledge/*"     → matches knowledge/technical, knowledge/principle, etc.
"self/value"      → exact match only
```

Multiple filter tags are OR-combined: `["knowledge/technical", "self/style"]` means
"blocks tagged knowledge/technical OR self/style."

### `guarantee_tags`

Blocks matching `guarantee_tags` are always included in the frame, regardless of score,
subject to the token budget. The same pre-allocation principle used for constitutional
blocks in the SELF frame (exploration 007) — guaranteed blocks are reserved first,
then the remaining budget is filled by scored candidates.

Example: a `security_review` frame that always includes the block about security
principles regardless of current query similarity:

```python
memory.register_frame(
    name="security_review",
    extends="attention",
    filter_tags=["knowledge/technical"],
    guarantee_tags=["self/constraint"],   # my security constraints always appear
    token_budget=1500
)
```

### `cache_ttl`

Custom frames can opt into caching, same mechanism as the SELF frame cache.
A frame that aggregates stable knowledge (rarely-changing knowledge/principle blocks)
can cache its results. A frame driven by recency or query similarity should not cache.

Default for custom frames: `cache_ttl=None` (no caching).

---

## Storage: The `frames` Table

Custom frame definitions are stored in the database alongside blocks and edges.
Built-in frames (self, attention, task) are seeded at initialisation.

```sql
CREATE TABLE frames (
    name           TEXT PRIMARY KEY,
    weights_json   TEXT NOT NULL,        -- {"recency": 0.25, "similarity": 0.35, ...}
    filter_tags    TEXT,                 -- JSON array | NULL (all blocks)
    filter_category TEXT,               -- glob string | NULL (all categories)
    template       TEXT NOT NULL,        -- "self" | "attention" | "task" | inline string
    token_budget   INTEGER NOT NULL,
    guarantee_tags TEXT,                 -- JSON array | NULL
    cache_ttl      INTEGER,              -- NULL = no cache
    source         TEXT NOT NULL,        -- 'builtin' | 'user' | 'agent'
    created_at     TEXT NOT NULL
);
```

**Built-in frames are `source='builtin'`** and cannot be modified or deleted via the
public API. They are the hardcoded named frames from exploration 015.

**User-defined frames are `source='user'`** — registered via `register_frame()`,
modifiable, deletable.

**Agent-defined frames are `source='agent'`** — registered by the agent itself during
operation (Phase 2 feature). The agent can define frames based on learned retrieval
patterns.

---

## What Custom Frames Cannot Do

Certain properties of the three built-in frames are hardcoded and not configurable
in custom frames:

| Property | Built-in frames only | Reason |
|----------|---------------------|--------|
| Constitutional block guarantee | `self` frame only | Constitutional logic is identity-layer specific |
| Task-type scoring modifiers (±0.20) | `self` frame only | Task modifiers are identity-specific (exploration 007) |
| Session prompt cache with event invalidation | `self` frame only | SELF cache has specific invalidation triggers tied to self-tag events |
| Internal library use (consolidate, curate) | Named frames only | Library internals reference named frames explicitly |
| `_recall()` access | Named frames only | Internal calls use named frame identifiers, not custom names |

Custom frames are retrieval and presentation configurations. They do not participate
in the library's internal mechanics.

---

## Updating and Deleting Custom Frames

```python
# Update a registered frame
memory.update_frame(
    name="code_review",
    overrides={"token_budget": 2000}   # only the specified fields change
)

# Delete a registered frame
memory.delete_frame("code_review")    # removes from DB; existing .frame() calls with this name raise FrameNotFoundError
```

Built-in frames (`source='builtin'`) cannot be updated or deleted. Any attempt raises
`BuiltinFrameError`.

---

## Worked Example: Registering and Using a Custom Frame

### Scenario: an application for reviewing Python code commits

The application wants a frame that surfaces:
- Technical knowledge blocks most similar to the diff being reviewed
- The agent's style preferences (how the agent wants code to look)
- Recent technical knowledge (what the agent has been learning lately)

```python
memory.register_frame(
    name="code_review",
    extends="attention",           # starts with attention's weights
    overrides={
        "weights": {
            "similarity":    0.45,   # diff similarity is primary
            "recency":       0.20,   # recent learning matters
            "confidence":    0.15,
            "centrality":    0.10,
            "reinforcement": 0.10
        },
        "filter_tags":    ["knowledge/technical", "self/style"],
        "guarantee_tags": ["self/style"],   # always include style preferences
        "token_budget":   1500
    }
)
```

**Stored frame definition (resolved, no inheritance chain):**
```json
{
  "name": "code_review",
  "weights": {"similarity": 0.45, "recency": 0.20, "confidence": 0.15,
               "centrality": 0.10, "reinforcement": 0.10},
  "filter_tags": ["knowledge/technical", "self/style"],
  "template": "attention",
  "token_budget": 1500,
  "guarantee_tags": ["self/style"],
  "cache_ttl": null,
  "source": "user"
}
```

**Using it at review time:**
```python
diff = """
-def process(data):
+def process_user_data(data: dict) -> Result:
"""

review_context = memory.frame("code_review", query=diff)

# review_context.text:
#
#   ## Python type annotations
#   Type hints make function signatures self-documenting. Prefer annotating
#   all function parameters and return types.
#
#   ## Naming: describe behaviour, not type
#   Variable names should reveal intent. process_user_data is better than
#   process because it clarifies the subject.
#
#   [style preference block — guaranteed inclusion]
#   ## Prefer explicit over implicit
#   Clarity over cleverness. A slightly longer, obvious name is always better
#   than a short, ambiguous one.
```

The `self/style` block appeared because of `guarantee_tags` — it would have scored
lower on similarity to the diff alone but the frame guarantees it's included.

---

## Ad-hoc vs. Registered: When to Use Each

| Situation | Use |
|-----------|-----|
| One-off retrieval, experimenting with weights | Ad-hoc `recall(weights=...)` |
| Repeated use, same context type, same weights | `register_frame()` + `frame("name")` |
| Frame is a small variation of an existing frame | `register_frame(extends=...)` |
| Frame should persist across sessions and restarts | `register_frame()` (stored in DB) |
| Frame is being built dynamically by the agent | `register_frame(source='agent')` (Phase 2) |

---

## Phase 1 vs Phase 2

**Phase 1 — include:**
- `register_frame()`, `update_frame()`, `delete_frame()` API
- Inheritance via `extends` (resolved at registration)
- `filter_tags` with glob patterns
- `guarantee_tags`
- Auto-normalisation of weights
- `frames` table in the database
- Three template styles inherited from built-in frames

**Phase 2 — defer:**
- Agent-created frames (`source='agent'`) based on observed retrieval patterns
- Custom template strings with placeholder syntax
- LLM-synthesised rendering (pass blocks to an LLM, get prose output instead of markdown list)
- Frame versioning (keeping history of changes to a frame definition)
- Frame sharing between agents or loading from external configuration files

---

## Design Decisions from this Exploration

| Decision | Rationale |
|----------|-----------|
| Three mechanisms: ad-hoc weights, named registration, inheritance | Different use cases; ad-hoc for experiments, registered for reuse, extends for variation |
| Inheritance resolved at registration time (not call time) | Prevents silent behaviour changes when parent frame is modified |
| Auto-normalise weights to sum to 1.0 | Callers can express relative emphasis without counting to 1.0 |
| Filter tags support glob patterns ("self/*") | Avoids enumerating every tag in a namespace |
| Multiple filter tags are OR-combined | A block matching ANY tag in the list is eligible |
| `guarantee_tags` pre-allocated like constitutional blocks | Domain-specific "always surface this" without hardcoding |
| Custom frames stored in `frames` table with `source` field | Persists across restarts; source distinguishes builtin vs. user vs. agent |
| Builtin frames cannot be modified or deleted | System correctness depends on self, attention, task being stable |
| Custom frames cannot participate in library-internal mechanics | Internal calls use named frames explicitly; custom frames are retrieval config only |
| Default `cache_ttl=null` for custom frames | Query-driven custom frames should be fresh; caller opts into caching |

---

## Open Questions

- [ ] Should built-in frames be inspectable via `memory.get_frame("self")` to expose
      their weights to the caller? (Useful for debugging; probably yes)
- [ ] Should `register_frame()` validate that the provided filter actually matches any
      existing blocks, or is that an unnecessary check? (Probably unnecessary — blocks
      come and go; the frame definition is independent of current block state)
- [ ] What happens when a custom frame's `guarantee_tags` match more blocks than the
      token budget allows? (Same as constitutional overflow in exploration 007 — include
      only highest-confidence guaranteed blocks until budget is exhausted)
- [ ] Should frame names be namespaced to avoid collisions between users/plugins?
      (e.g., `user.code_review` vs. `plugin.code_review`)
- [ ] Should the agent be able to auto-suggest frame definitions based on observed
      call patterns? ("You call attention with these weights 80% of the time; should
      I register this as a frame?") — Phase 2

---

## Variations

- [ ] What if a custom frame's filter produces zero eligible blocks? Trace through
      how `frame()` behaves and whether it falls back gracefully.
- [ ] What if two `guarantee_tags` blocks are in a contradicting pair? Which wins?
      (Apply the same confidence-weighted suppression from exploration 014/015)
- [ ] Can a custom frame use the SELF template style? Work through what "instruction-
      style" output looks like for non-self-tagged blocks.
