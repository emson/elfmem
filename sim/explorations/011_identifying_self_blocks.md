# Title: Identifying Self Blocks — Beyond the Boolean Flag

## Status: complete

## Question

`is_self: boolean` is restrictive. A block can be relevant to multiple things — it
might be both a self-relevant value AND a useful knowledge block. How should self-relevance
be expressed on a block? Brainstorm alternatives, evaluate trade-offs, decide.

---

## Background

The current design has `is_self: true/false` in the front matter. This was derived
naturally from the architecture spec's concept of self-tagged blocks. But it has a
fundamental limitation: a block is either self or it isn't.

Real knowledge doesn't work that way. Consider:

```markdown
## I prefer explicit over implicit code

Code should express its intent clearly. Avoid clever tricks.
Prefer verbose-but-readable over terse-but-cryptic.
```

This is clearly a self block — it expresses a value and shapes behaviour.

```markdown
## Python asyncio patterns

Asyncio uses an event loop to schedule coroutines...
```

This is clearly a knowledge block — technical, not identity-related.

But:

```markdown
## Debugging starts with the simplest hypothesis

When something breaks, resist the urge to jump to complex explanations.
Start with the obvious: wrong input, off-by-one, stale cache.
Build up from there only if the simple explanation fails.
```

Is this self or knowledge? It's a debugging principle AND a reflection of how
the author thinks. It belongs in both. A binary flag forces a choice that shouldn't
need to be made.

---

## What the Boolean Actually Does

Before evaluating alternatives, trace exactly what `is_self` does in the current design:

1. **Scoring bonus in SELF frame** (exploration 003): self-tagged blocks get a scoring
   advantage when the system assembles the SELF context frame.

2. **Decay profile influence** (exploration 004): self-aligned blocks get durable decay
   (λ=0.001), surviving much longer than standard knowledge (λ=0.01).

3. **Self-alignment computation** (exploration 004): `self_alignment` is computed as
   cosine similarity to the current SELF context — this is a *separate* continuous
   score and doesn't depend on `is_self`.

4. **Retrieval surfacing**: blocks with `is_self=true` are more likely to appear in
   SELF frame assembly, though not exclusively — reinforcement can override this
   (exploration 003 showed a non-self-tagged block beating a self-tagged one).

So `is_self` is not a hard gate. It's a scoring signal and a decay modifier. The
question is: can we express that signal more richly?

---

## Brainstorm: Eight Alternative Approaches

---

### Approach 1: Scalar self_weight (0.0 to 1.0)

Replace the boolean with a continuous value:

```yaml
self_weight: 0.90   # strongly self-relevant
self_weight: 0.45   # moderately self-relevant
self_weight: 0.00   # not self-relevant (omit or default)
```

**Scoring:** `self_weight` replaces the binary flag in scoring formulas.
The SELF frame uses it as a multiplier rather than a bump.

**Pros:**
- Captures degrees of self-relevance
- Can be set by learner or inferred by system
- Same field, richer signal

**Cons:**
- Who determines the value? If the learner sets it, it's subjective and inconsistent.
  ("Is this 0.7 or 0.8?"). If the system computes it, it's redundant with `self_alignment`.
- Introduces another tunable threshold: "include in SELF frame if self_weight > 0.50?"
- Doesn't answer *what kind* of self-relevance this is

**Verdict:** Better than boolean but still one-dimensional. Doesn't solve the
"multiple things" problem — you're still describing self-relevance on a single axis.

---

### Approach 2: Explicit Tags Array

A block can carry a set of string labels. Self-related labels coexist with other labels:

```yaml
tags: [self/value, knowledge/technical]
tags: [self/constraint, self/value]
tags: [knowledge/principle, self/style]
tags: []   # no tags — pure knowledge block
```

The SELF frame selects blocks with any `self/*` tag. The scoring bonus applies
proportionally to how many and which self-tags are present.

**Reserved self tag taxonomy:**
```
self/value         — expresses something the author believes
self/constraint    — a hard rule the author follows (softer than constitutional)
self/style         — communication or formatting preferences
self/context       — situational identity (current role, project mode)
self/goal          — aspirational or directional (where I'm heading)
```

**Pros:**
- A block can be both `self/value` AND `knowledge/technical` — the "multiple things" problem solved
- Extensible: new tag types require no schema change
- Human-readable: `self/constraint` is clearer than `is_self: true`
- Allows the system to distinguish *what kind* of self-relevance, enabling different
  scoring behaviour per tag type
- Compatible with the existing `self_alignment` score — tags express intent,
  score expresses computed relevance

**Cons:**
- Requires a defined tag taxonomy (open-ended tags become noise)
- Learner must know the taxonomy to tag correctly
- Tag inconsistency: "self/value" vs "self/values" vs "self-value" — needs normalisation

**Verdict:** Strong. Solves the multiple-things problem. Tags coexist naturally.
Requires a controlled vocabulary but that's a feature, not a bug.

---

### Approach 3: Self-Alignment Score Replaces the Flag Entirely

We already compute `self_alignment` (0.0–1.0) during consolidation. Why have an
explicit flag at all? Instead, any block with `self_alignment > threshold` is treated
as a self block for scoring and decay purposes.

```yaml
# No is_self field
# No tags for self
# Just:
self_alignment: 0.85   # computed — block is self-relevant
self_alignment: 0.31   # computed — not particularly self-relevant
```

**SELF frame logic:** rank blocks by composite SELF score (includes self_alignment
as a component). High self_alignment blocks naturally surface.

**Pros:**
- No explicit declaration needed — the system figures it out
- Consistent with the soft-bias principle from exploration 004: "self influences,
  not gates"
- One fewer concept for the learner to think about

**Cons:**
- Loses **learner intent**. There's a meaningful difference between:
  - "This block scored 0.85 self_alignment by coincidence"
  - "I explicitly wrote this block to express my values"
  - The score can't distinguish these cases
- Fragile during cold start: before SELF context is established, self_alignment
  scores are unreliable. Explicit tags bootstrap the system.
- Self-alignment is recomputed periodically. A block might drift in and out of
  "self relevance" as the SELF context evolves. Intentional self-declarations
  should be more stable than that.
- Makes it impossible for the learner to say "include this in my self context"
  without relying on the system computing it correctly.

**Verdict:** Tempting, but loses something important: learner intent and system
stability. Better as a complement to explicit tags, not a replacement.

---

### Approach 4: Multiple Categories (Category as Array)

The current `category` is a single value (`knowledge/technical`). What if it's an array?

```yaml
category:
  - knowledge/principle
  - self/value
```

Self-relevance becomes a category, not a flag. A block can have multiple categories
and belong to multiple frames naturally.

**Pros:**
- Uses the existing category structure — no new concepts
- Category already drives decay profile and frame routing in the current design
- `self/value` as a category is intuitive and consistent with the taxonomy

**Cons:**
- Category has structural meaning (decay profile selection, template routing in system
  prompt from exploration 006). Multiple categories means multiple structural behaviours
  collide: which decay profile wins? standard (λ=0.01) or durable (λ=0.001)?
- Category was designed as a single primary classification. Turning it into an array
  adds complexity to every system that reads category.

**Verdict:** Partially correct — self-relevance as a category-like signal is right.
But conflating it with the primary category field creates structural ambiguity.
Better to keep `category` as the primary classification and add a separate `tags`
array for supplementary labels (including self-signals).

---

### Approach 5: Frame Affinity Declaration

The block explicitly declares which frames it wants to appear in:

```yaml
frame_affinity: [SELF, ATTENTION, TASK]
```

**Pros:**
- Direct expression of multi-context relevance
- Precise: the block says exactly where it belongs

**Cons:**
- This reverses the correct information flow. Frames should select blocks based
  on query and scoring — blocks shouldn't push themselves into frames.
- Creates a hard override that bypasses scoring. A block with `frame_affinity: [SELF]`
  would always appear in SELF regardless of its actual relevance to the current context.
- Fragile: blocks would need updating every time a new frame type is added.
- Puts frame-selection logic in the learner's hands, which they don't understand
  and shouldn't need to.

**Verdict:** Backwards. Frames select blocks; blocks don't select frames.

---

### Approach 6: Graph-Emergent Self-Relevance

Don't declare self-relevance at all. Instead, let it emerge from graph structure.
Blocks connected to known constitutional/self blocks inherit self-relevance
proportional to edge weight and distance.

```
C1 (constitutional) → edge(0.85) → M4 (asyncio)
M4 inherits self-relevance: 0.85 × decay_by_distance
```

Over time, blocks that are densely connected to the self cluster become "self-adjacent"
without explicit tagging.

**Pros:**
- Fully emergent — no learner burden
- Self-relevance grows organically through the graph
- Philosophically aligned with "self grows through use" (exploration 003)

**Cons:**
- Hard to debug: "Why is my asyncio block appearing in my SELF frame?"
  → Because it has 3 hops to a constitutional block via shared Python blocks.
  Opaque and surprising.
- Doesn't honour learner intent. A learner who explicitly writes a self-block
  gets no preferential treatment unless it happens to connect to existing self blocks.
- Cold start problem: before the graph is dense, self-emergence doesn't work.
- Could create unexpected self-inflation: a popular technical block that connects
  to everything becomes self-adjacent even if it's purely technical.

**Verdict:** An interesting Phase 2+ mechanism for *discovering* implicit self-relevance,
but not reliable enough as the *primary* identification mechanism. Keep as a variation.

---

### Approach 7: Self-Intent + Computed Score (Two Signals)

Separate the two concerns explicitly:

```yaml
self_intent: value    # explicit learner declaration: the TYPE of self-relevance
self_alignment: 0.85  # system-computed: the DEGREE of self-relevance
```

`self_intent` replaces `is_self` and expresses category of self-relevance:
- `value` — a belief or principle
- `constraint` — a rule or limit on behaviour
- `style` — how I communicate
- `context` — situational identity
- null / absent — not explicitly self-declared

The SELF frame uses both:
- `self_intent` present → scoring bonus (explicit intent weight)
- `self_alignment` → continuous relevance component (already in formula)

**Pros:**
- Two-signal model captures more information than either alone
- `self_intent` is stable (set at creation); `self_alignment` is dynamic (recomputed)
- Allows the SELF frame to treat "I explicitly said this is a value block" differently
  from "the system computed this is 0.85 aligned with self"
- The intent type enables routing (constitutional blocks always in Constraints section,
  value blocks in Values section — from exploration 006)

**Cons:**
- Two fields instead of one — more to explain
- `self_intent` is still a single value — can't express "both value and constraint"

---

### Approach 8: Tags with Reserved Self Namespace (Recommended)

Combining insights from approaches 2 and 7:

A block carries a `tags` array. Self-related tags use a reserved `self/` prefix.
Non-self-related tags use other namespaces.

```yaml
# Pure knowledge block
tags: []

# Explicit self block — a value
tags: [self/value]

# Self block with type specificity
tags: [self/constraint]

# Constitutional block (from 007 — this is the clearest case for explicit tagging)
tags: [self/constitutional]

# Multi-context: a principle that's both self-relevant and general knowledge
tags: [self/value, knowledge/principle]

# Multiple self dimensions
tags: [self/value, self/style]
```

**The self tag taxonomy (controlled vocabulary):**

| Tag | Meaning | Decay profile |
|-----|---------|---------------|
| `self/constitutional` | Behavioural invariants — never override | permanent (λ=0.00001) |
| `self/constraint` | Strong behavioural rules — rarely change | durable (λ=0.001) |
| `self/value` | Beliefs and principles — stable but can evolve | durable (λ=0.001) |
| `self/style` | Communication and output preferences | standard (λ=0.01) |
| `self/context` | Situational identity — role, project mode | short (λ=0.03) |
| `self/goal` | Directional — where I'm heading | standard (λ=0.01) |

Note: `self/constitutional` replaces `is_constitutional: true`. Tags subsume both fields.

**SELF frame scoring with tags:**

```
self_tag_bonus = 0.0
if "self/constitutional" in tags: self_tag_bonus = 0.50  (pre-allocated, see 007)
elif "self/constraint"   in tags: self_tag_bonus = 0.20
elif "self/value"        in tags: self_tag_bonus = 0.15
elif "self/style"        in tags: self_tag_bonus = 0.10
elif "self/context"      in tags: self_tag_bonus = 0.10
elif "self/goal"         in tags: self_tag_bonus = 0.10

# Multiple tags accumulate (capped at 0.30 for non-constitutional)
self_tag_bonus = min(sum(per_tag_bonuses), 0.30)
```

**ATTENTION frame with multi-tagged blocks:**

A block with `tags: [self/value, knowledge/technical]` is eligible for both frames.
In ATTENTION, it competes on query similarity. Its self-tags don't help or hurt it
in ATTENTION — they're irrelevant to that frame's scoring. This is correct: the block
is surfaced when relevant to the query, not because it's self-tagged.

**Decay profile selection from tags (replaces the is_self + is_constitutional logic):**

```
if "self/constitutional" in tags: λ = 0.00001
elif "self/constraint" in tags:   λ = 0.001
elif "self/value" in tags:        λ = 0.001
elif "self/style" in tags:        λ = 0.01
elif "self/context" in tags:      λ = 0.03
else:                              λ = from category (standard rule)
```

If multiple self tags → use the most durable profile (lowest λ wins).

---

## Comparison Matrix

| Approach | Multi-context? | Learner intent? | Degree? | Type? | Complexity |
|----------|---------------|----------------|---------|-------|------------|
| 1. Scalar self_weight | No | Yes (manually) | Yes | No | Low |
| 2. Tags array | **Yes** | **Yes** | No | **Yes** | Medium |
| 3. self_alignment only | No | **No** | **Yes** | No | Low |
| 4. Category as array | Yes | Yes | No | Yes | Medium |
| 5. Frame affinity | Yes | Yes | No | No | High (wrong) |
| 6. Graph-emergent | **Yes** | No | Yes | No | High |
| 7. self_intent + score | No | **Yes** | **Yes** | **Yes** | Medium |
| 8. Tags (recommended) | **Yes** | **Yes** | No | **Yes** | Medium |

Approach 8 (tags) wins on the things that matter most:
- Multi-context: one block can be both self and knowledge
- Learner intent: explicit declaration is honoured
- Type: which kind of self-relevance it is

The missing dimension is "degree" — tags don't express *how much* self-relevant a block is.
But degree is already covered by `self_alignment` (computed score). Tags + self_alignment
together cover all four dimensions.

---

## Result: The Revised Block Format

```markdown
---
id: a3f9c2b1d84593e1
created: 2026-03-04T10:30:00Z
source: api
category: knowledge/principle
tags: [self/value, knowledge/principle]
---

## Debugging starts with the simplest hypothesis

When something breaks, resist the urge to jump to complex explanations.
Start with the obvious: wrong input, off-by-one, stale cache.
Build up from there only if the simple explanation fails.
```

```markdown
---
id: c2d4f8109a3b7e21
created: 2026-03-04T09:00:00Z
source: cli
category: self/constitutional
tags: [self/constitutional]
---

## Always acknowledge uncertainty

When I do not know something with confidence, I say so clearly.
I do not fabricate answers or present guesses as facts.
```

```markdown
---
id: b7e1a20938cd4f22
created: 2026-03-03T15:45:00Z
source: sdk
category: knowledge/technical
tags: []
---

## Python asyncio patterns

Asyncio uses an event loop to schedule coroutines. Use `async def`...
```

**Changes from the previous block format (exploration 010):**

| Field | Old | New |
|-------|-----|-----|
| `is_self` | `is_self: true/false` | Removed — replaced by tags |
| `is_constitutional` | `is_constitutional: true/false` | Removed — replaced by `self/constitutional` tag |
| `tags` | Did not exist | Added — array of namespaced labels |

`category` remains as the primary structural classification.
`tags` is the supplementary multi-label field.

---

## Insight

### The flag was answering the wrong question

`is_self: true` asks "is this block a self block?" The better question is:
"In what ways is this block relevant to self — and to what else?"

A boolean collapses a rich, multi-dimensional relationship into a single bit.
Tags preserve the richness while remaining simple to write and read.

### Tags and self_alignment are complementary, not redundant

`tags` = explicit, stable, declared by the learner at creation time.
`self_alignment` = implicit, dynamic, computed by the system from the current SELF context.

A block might have `tags: [self/value]` but low `self_alignment` early on
(before the SELF context matures enough to recognise it). Over time, as more
self-blocks are added and the SELF context stabilises, the same block's
`self_alignment` rises. The tag held its place in the SELF frame while the
score caught up.

Conversely, a block might have no self-tags but high `self_alignment` because
it was repeatedly used in self-relevant contexts. That's the emergent signal
from exploration 003 — usage patterns creating identity. No tag needed.

### Constitutional blocks are just the clearest case

`self/constitutional` is the tag where explicit intent is most important.
You would never want a constitutional block to lose its constitutional status
because `self_alignment` drifted. The tag is the hard anchor; the score is
the soft signal. Constitutional blocks have both — and they have the tag in
front matter as an immutable, unforgeable record of their status.

---

## Design Decisions from this Exploration

| Decision | Rationale |
|----------|-----------|
| Replace `is_self` and `is_constitutional` with `tags` array | Tags allow multi-context membership; boolean flags don't |
| Self tags use reserved `self/` namespace | Distinguishes self-intent from other labels; enables controlled taxonomy |
| Self tag taxonomy: constitutional, constraint, value, style, context, goal | Different self-types get different decay profiles and scoring bonuses |
| Tags drive decay profile selection (lowest λ wins for multiple tags) | Most durable applicable profile for the block's identity |
| Tags + self_alignment are complementary signals | Tags = learner intent (stable); self_alignment = system inference (dynamic) |
| `category` remains as single primary structural classification | Structural role (decay, routing) needs one authoritative value |
| Blocks with self tags remain eligible for all frames | Tags don't gate frames; scoring weights determine frame inclusion |

---

## Open Questions

- [ ] Should the `self/` tag namespace be open (any `self/X` is valid) or strictly
      controlled (only the six defined tags are accepted)? Open is flexible but messy.
- [ ] Can a learner add arbitrary tags outside the `self/` namespace? If so, what do
      they mean to the system? (Currently: nothing — only `self/*` tags have semantic weight)
- [ ] Should tag-based decay override category-based decay, or should they combine?
      (Current recommendation: self tags win, lowest λ applies)
- [ ] Should the SELF frame give different scoring bonuses per tag type, or a flat
      bonus for any `self/*` tag?
- [ ] At what point, if ever, can a block's self-tags be changed? (Promotion to
      `self/value` seems reasonable; demotion feels like a delete-and-relearn operation)

---

## Variations

- [ ] What if a block has `self/constitutional` AND `self/value` tags?
      Does the constitutional tag dominate entirely, or do both bonuses apply?
- [ ] What if the system auto-suggests tags at consolidation time based on
      content analysis? ("This block looks like a self/value — confirm?")
- [ ] Explore graph-emergent self-relevance (approach 6) as a Phase 2 mechanism
      for *discovering* blocks that have become implicitly self-relevant through
      repeated use, even without explicit self-tags.
