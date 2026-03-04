# Title: Block Anatomy — Front Matter and ID Design

## Status: complete

## Questions

1. **Front matter:** Should blocks contain YAML front matter, and if so, which fields
   belong there vs. in the database?

2. **Block ID:** Should the ID be a content hash, and should it encode additional
   information like category, timestamp, or source?

---

## Background

A block is the atomic unit of the system. Everything else — decay, scoring, retrieval,
self, edges — operates on blocks. Getting the block format right is foundational.

The current description: a markdown block consisting of an H2 title and a short paragraph
around one concept. Clean and minimal. The question is what surrounds that content:
does it have metadata attached, and how is it identified?

---

## The Two Layers of Block Data

Before reasoning about format, distinguish the two fundamentally different kinds of
data a block carries:

**Layer 1 — Immutable data:** Set once at creation. Never changes for the life of the block.
```
id, created_at, source, category, is_self, is_constitutional
```

**Layer 2 — Operational data:** Changes continuously as the system runs.
```
confidence, reinforcement_count, decay_lambda, hours_since_reinforcement,
self_alignment, edges, status (active/superseded/pruned)
```

This distinction drives everything that follows.

---

## Question 1: Should Blocks Have YAML Front Matter?

### The case for front matter

Front matter makes a block **self-describing**. If you pick up a block file in isolation —
from a backup, an export, a shared repository, a debug session — you know what it is
without querying a database.

```markdown
---
id: a3f9c2b1d84593e1
created: 2026-03-04T10:30:00Z
source: api
category: knowledge/technical
is_self: false
is_constitutional: false
---

## Python asyncio patterns

Asyncio uses an event loop to schedule coroutines. Use `async def` to define
coroutines and `await` to yield control. Blocking calls must be wrapped with
`asyncio.run_in_executor` to avoid stalling the loop.
```

This is portable. The block is its own record. It can be:
- Stored in a git repository and diffed
- Sent to another system and re-ingested
- Read by a human without tooling
- Validated without a database connection

It also follows the convention of nearly every markdown-based system
(Jekyll, Hugo, Obsidian, Foam, Logseq). Front matter is the established
idiom for markdown metadata.

### The case against front matter — the mutability trap

The danger is **putting the wrong fields in front matter**. If operational data
(confidence, reinforcement_count, decay_weight) goes in front matter, every
curate() pass and every recall() call must rewrite the file. The block is
no longer immutable on disk. This creates several problems:

- **Concurrency:** two processes reading/writing the same block simultaneously
- **Sync errors:** database and front matter get out of sync; which is the truth?
- **Audit failure:** you can no longer hash the file to verify it hasn't changed
- **Performance:** disk I/O on every reinforcement event (every recall())

**Conclusion:** Front matter is correct for **immutable data only.** Operational
data lives exclusively in the database. The on-disk block file never changes
after consolidation writes it.

### What belongs in front matter

| Field | Immutable? | In front matter? | Reason |
|-------|-----------|-----------------|--------|
| `id` | Yes | Yes | Core identity |
| `created` | Yes | Yes | Provenance timestamp |
| `source` | Yes | Yes | Who submitted it (api/cli/sdk/llm/user) |
| `category` | Mostly | Yes | Set at consolidation; see note below |
| `is_self` | Mostly | Yes | Self-tagged at creation; signals identity intent |
| `is_constitutional` | Yes | Yes | Set once; never unset |
| `confidence` | No | **No** — database only | Changes with curate() |
| `reinforcement_count` | No | **No** — database only | Changes with recall() |
| `decay_lambda` | No | **No** — database only | Could change on recategorisation |
| `self_alignment` | No | **No** — database only | Recomputed periodically |
| `edges` | No | **No** — database only | Change constantly |
| `status` | No | **No** — database only | active/superseded/pruned |

**Note on `category`:** Category is assigned at consolidation and is "mostly immutable"
— it rarely changes but could be corrected. If category changes, the front matter
and database must both be updated. Acceptable because recategorisation is an
intentional, infrequent administrative action, not a routine operational event.

**Note on `is_self`:** A block can be submitted with `is_self: true` as a hint.
It can also be promoted to self-status by high reinforcement (exploration 003).
If promoted post-creation, the front matter would need updating. This is an
acceptable cost — promotion to self-identity is significant and deliberate.

### The two-phase front matter model

Blocks have front matter in two states:

**Phase A — INBOX (raw, submitted by learner):**
The learner writes minimal markdown. Front matter is optional and contains only
intent hints. The system accepts either format:

```markdown
## Python asyncio patterns

Asyncio uses an event loop...
```

Or with hints:
```markdown
---
category: knowledge/technical
is_self: true
---

## My core debugging philosophy

When debugging, I always start with the simplest possible hypothesis...
```

The learner should not need to write `id` or `created` — the system generates those.

**Phase B — MEMORY (stored, post-consolidation):**
The system writes the complete front matter block. The file is now sealed.

```markdown
---
id: a3f9c2b1d84593e1
created: 2026-03-04T10:30:00Z
source: api
category: knowledge/technical
is_self: false
is_constitutional: false
---

## Python asyncio patterns

Asyncio uses an event loop to schedule coroutines...
```

This file is written once and never modified. All operational data lives in the database.

---

## Question 2: What Should the Block ID Be?

### What we need from an ID

1. **Unique** — no two blocks have the same ID
2. **Stable** — the ID never changes for a given block
3. **Useful for dedup** — ideally, ID generation reveals whether content already exists
4. **Compact and readable** — short enough to appear in logs without noise
5. **Opaque enough** — does not leak sensitive content

### Evaluating the options

#### Option A: Content hash

```
id = sha256(normalize(title + "\n" + body))[0:16]
```

Where `normalize` = strip whitespace, lowercase, collapse internal whitespace.

```
Example:
  title = "Python asyncio patterns"
  body  = "Asyncio uses an event loop..."
  hash  = sha256("python asyncio patterns\nasyncio uses an event loop...")
  id    = "a3f9c2b1d84593e1"
```

**Pros:**
- **Content-addressable:** same concept, same phrasing → same ID → instant exact dedup
  without embedding comparison. If `learn("## X\n\nY")` is called twice, the second
  call produces the same hash and is immediately rejected without embedding work.
- Stable: content never changes, so hash never changes.
- Compact: 16 hex chars (64 bits of address space — collision risk negligible for
  any realistic corpus; even at 1M blocks, collision probability is ~2.7×10⁻⁹).

**Cons:**
- Slightly different phrasing → completely different hash. "asyncio uses" and "asyncio
  utilises" produce different IDs even though the concepts are nearly identical.
  But this is fine — semantic similarity is handled by embeddings; exact dedup
  is handled by the hash.

**Verdict: this is the right foundation.**

---

#### Option B: Encoding category in the ID

```
id = {category_prefix}_{sha256(content)[0:12]}
e.g. "knt_a3f9c2b1d845"  (knt = knowledge/technical)
     "slf_b7e1a20938cd"  (slf = self)
     "cst_c2d4f8109a3b"  (cst = constitutional)
```

**Appealing because:** IDs are instantly readable in logs. `knt_a3f9` tells you at a
glance what kind of block you're looking at. No query needed.

**Problem 1 — Category is assigned at consolidation, not at content-hash time.**
The learner submits content. The system assigns category during consolidation.
If we include category in the hash, we can't compute the ID until after categorisation.
The hash is no longer purely a function of content.

**Problem 2 — Category can change.**
If a block is recategorised (e.g., moved from `knowledge/technical` to `knowledge/principle`),
its ID changes. Every database record pointing to the old ID breaks. Every edge reference
breaks. Every cached system prompt that referenced this block by ID becomes stale.
This is the reference integrity nightmare — a category change cascades into a
full ID migration.

**Problem 3 — Prefix adds noise without real utility.**
If you need to filter by category, use a database query: `WHERE category = 'knowledge/technical'`.
The prefix in the ID is redundant and only helps in raw log scanning — a marginal benefit
that doesn't justify the brittleness.

**Verdict: do not encode category in the ID.**

---

#### Option C: Encoding timestamp in the ID

```
id = {created_ts}_{sha256(content)[0:8]}
e.g. "20260304T103045_a3f9c2b1"
```

**Appealing because:** IDs sort chronologically. You can tell when a block was created
from its ID. Useful for debugging ("which blocks were added in the last session?").

**Problem 1 — Breaks content-addressability.**
Two calls to `learn()` with identical content at different times produce different IDs.
Exact dedup via hash is now impossible — you'd need embedding comparison for every
consolidation even for perfectly identical content.

**Problem 2 — Timestamp is already in front matter.**
`created: 2026-03-04T10:30:00Z` is in the front matter. Time-based filtering is done
via the database: `WHERE created > '2026-03-04'`. The timestamp in the ID is
redundant — it duplicates data already stored elsewhere.

**Problem 3 — Long IDs.**
`20260304T103045_a3f9c2b1` is 25 chars. Log lines become noisy. The timestamp portion
is 15 of those chars and adds no information that the `created` field doesn't already provide.

**Problem 4 — Microsecond collisions.**
If two blocks are learned in the same second (a batch submission), the timestamp
portion is identical. The short hash `a3f9c2b1` (8 chars = 32 bits) now has a
non-trivial collision risk if batch sizes are large. You'd need to use more hash
chars, making the ID even longer.

**Verdict: do not encode timestamp in the ID.**

---

#### Option D: ULID (Universally Unique Lexicographically Sortable Identifier)

```
id = ulid()
e.g. "01HQXK4Z9A8MWVJK3N5EBCXY4"
```

A ULID is 26 chars: first 10 chars encode timestamp (millisecond precision),
last 16 chars are random. IDs sort chronologically. No collision risk.

**Pros:**
- Time-sortable without encoding timestamp visibly
- Globally unique without coordination
- Standard format, libraries in every language

**Cons:**
- Loses content-addressability entirely — same content submitted twice gets two
  different ULIDs. No fast exact dedup.
- 26 chars is long for a log ID.
- Adds a dependency (ULID library).

**Verdict: useful if you need global uniqueness without collision risk, but
loses content-addressability. Not ideal for this system.**

---

### The recommended design

**ID = content hash, with `created` in front matter for temporal needs.**

```
id = sha256(normalize(title + "\n" + body))[:16]
```

The ID answers: **"What is this block?"** (its content fingerprint)
The `created` field answers: **"When was this block learned?"**
The `category` field answers: **"What kind of block is this?"**

These are three separate questions with three separate answers. They should not
be crammed into a single ID field.

**The fast exact-dedup benefit:**

During consolidation, before any embedding work:
```
incoming_id = sha256(normalize(B.title + "\n" + B.body))[:16]
if incoming_id in memory.block_ids:
    → exact duplicate → reject immediately
    → no embedding API call needed
    → O(1) lookup
```

Only blocks that pass the hash check need embedding. This matters at scale —
embedding API calls cost latency and money; hash comparison costs nothing.

**Near-duplicate detection still requires embeddings:**

```
incoming_id = sha256(...)[:16]
if incoming_id in memory.block_ids: reject (exact duplicate, free)
else:
  embedding = embed(B.content)         # only here do we call the embedding API
  for block in memory.blocks:
    if similarity(embedding, block.embedding) > 0.90: near-duplicate handling
```

---

## Result: The Canonical Block Format

### INBOX block (submitted by learner — minimal)

```markdown
## Python asyncio patterns

Asyncio uses an event loop to schedule coroutines. Use `async def` to define
coroutines and `await` to yield control. Blocking calls must be wrapped with
`asyncio.run_in_executor` to avoid stalling the loop.
```

Or with optional hints:
```markdown
---
category: knowledge/technical
is_self: false
---

## Python asyncio patterns

Asyncio uses an event loop to schedule coroutines...
```

### MEMORY block (written by consolidation — complete)

```markdown
---
id: a3f9c2b1d84593e1
created: 2026-03-04T10:30:00Z
source: api
category: knowledge/technical
is_self: false
is_constitutional: false
---

## Python asyncio patterns

Asyncio uses an event loop to schedule coroutines. Use `async def` to define
coroutines and `await` to yield control. Blocking calls must be wrapped with
`asyncio.run_in_executor` to avoid stalling the loop.
```

### Database record (operational data — never in the file)

```yaml
id: a3f9c2b1d84593e1
confidence: 0.53
reinforcement_count: 0
decay_lambda: 0.01
hours_since_reinforcement: 0
self_alignment: 0.62
status: active
edges:
  - to: b7e1a20938cd4f22
    type: relates_to
    weight: 0.71
```

---

## Insight

### The ID is a content fingerprint, not a label

Encoding category or timestamp into the ID conflates *identity* with *metadata*.
The ID answers one question: "Is this block the same content as some other block?"
Everything else — when it was created, what kind it is, how it relates to others —
is metadata, stored separately, queryable independently.

### Front matter creates a two-tier truth

The block file is the **immutable truth** about content and provenance.
The database is the **mutable truth** about operational state.
When they diverge (e.g., if the database is rebuilt), the block files win.
They are the primary record; the database is a derived index.

This has a useful property: **the entire system can be reconstructed from the block files.**
Re-consolidation reads all MEMORY block files, re-generates embeddings and edges,
rebuilds the database. No data is lost because no data lived only in the database.
(Except reinforcement_count and confidence — these are genuinely ephemeral and would
reset on a full rebuild. That may be acceptable as a known trade-off.)

### The learner experience is simple

The learner writes markdown. One H2, one paragraph. Optionally hints category or is_self.
The system handles everything else. This keeps the interface clean:

```bash
amgs learn "## Python asyncio patterns\n\nAsyncio uses an event loop..."
```

No need to know about IDs, hashing, or front matter format. The system is a black box
from the learner's perspective. The front matter is infrastructure, not user interface.

---

## Design Decisions from this Exploration

| Decision | Rationale |
|----------|-----------|
| Front matter contains only immutable fields | Mutable fields in front matter require rewriting files; breaks immutability |
| Immutable fields in front matter: id, created, source, category, is_self, is_constitutional | These describe identity and provenance; never change after consolidation |
| Operational fields are database-only | confidence, reinforcement_count, decay, edges, status change continuously |
| Learner input: minimal markdown, optional hints | id and created are system-generated; learner writes content only |
| ID = sha256(normalized content)[:16] | Content-addressable; enables O(1) exact dedup before embedding |
| Category NOT encoded in ID | Category can change; ID changes would break all references |
| Timestamp NOT encoded in ID | Breaks content-addressability; redundant with created field |
| Block files are the primary record; database is derived | Full rebuild possible from block files alone |

---

## Open Questions

- [ ] Should `is_self` be settable by the learner at submit time, or only assigned by the system
      at consolidation (based on content and self-alignment score)?
- [ ] If category is corrected post-consolidation, should the block be re-consolidated
      (new embedding, new edge computation) or just relabelled?
- [ ] What is the normalisation function for hashing? (case, punctuation, whitespace treatment)
      This needs to be deterministic and documented.
- [ ] Should the full sha256 be stored alongside the short ID for collision verification?
- [ ] On a full database rebuild from block files, should reinforcement_count and confidence
      be reset (lost) or estimated from some other signal?

---

## Variations

- [ ] What if we store blocks in a git repository? The git commit hash becomes part of
      the provenance chain — each block's history is auditable via git log.
- [ ] What if blocks are stored as a single SQLite blob rather than flat files?
      What does the portability trade-off look like?
- [ ] What if the learner-submitted hints (category, is_self) conflict with what
      the system infers at consolidation? Which wins?
