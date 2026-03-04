# Title: How Self Tags Get Assigned

## Status: complete

## Question

How does a block acquire self tags? Exploration 011 established the tag taxonomy
(`self/value`, `self/constraint`, `self/style`, etc.) but not the mechanism.
Three candidates: learn(), consolidate(), curate(). Reason through each — when
does a block become identified as a self block, and who makes that decision?

---

## Background

From exploration 011, self-relevance has two signals:
- **Tags** (explicit, stable): `self/value`, `self/constraint`, etc. — declared intent
- **`self_alignment`** (implicit, dynamic): computed cosine similarity to SELF context

Tags live in front matter (immutable once confirmed). `self_alignment` lives in the
database (recomputed periodically). The question is: how and when do tags get written?

This matters more than it seems. Self-tags shape:
- Decay profile (a `self/value` block gets durable decay — λ=0.001 vs. λ=0.01)
- SELF frame scoring (self-tagged blocks get scoring bonus)
- System prompt assembly (exploration 006: template routing by tag type)
- Constitutional protection (exploration 007: `self/constitutional` blocks are pre-allocated)

Getting tags wrong in either direction is costly:
- **Under-tagging:** self-relevant blocks decay too fast; don't surface in SELF frame
- **Over-tagging:** non-self blocks inflate the SELF frame; identity becomes noise

---

## The Three Candidate Moments

```
learn()         consolidate()       curate()
   ↓                  ↓                 ↓
Explicit        Inferred/LLM      Usage-promoted
declaration     classification    emergence
(highest trust) (medium trust)    (lowest trust initially)
```

Each moment has different information available:

| Moment | Knows content? | Knows SELF context? | Knows usage history? |
|--------|---------------|--------------------|--------------------|
| learn() | Yes | No (not loaded) | No |
| consolidate() | Yes | Yes | No |
| curate() | Yes | Yes | Yes |

---

## Moment 1: learn() — Explicit Declaration

### What the learner can do

The learner may optionally include self-tags in the hint front matter:

```markdown
---
tags: [self/value]
---

## I prefer explicit over implicit code

Code should express its intent clearly. Avoid clever tricks.
Prefer verbose-but-readable over terse-but-cryptic.
```

Or for a constitutional block:
```markdown
---
tags: [self/constitutional]
---

## Always acknowledge uncertainty

When I do not know something with confidence, I say so explicitly.
I do not fabricate answers or present guesses as facts.
```

### Trust level: highest

This is the strongest signal. The learner is explicitly declaring:
"I intend this block to be part of my identity. I know what kind."
The system should honour this unless validation fails.

### Validation at learn()

Not all self-tag claims are accepted without scrutiny:

```
self/constitutional  → REJECTED at learn() — cannot self-declare constitutional status
                       Constitutional requires the formal amendment process (exploration 007)
                       Treated as self/constraint instead, flagged for review

self/constraint      → Accepted, stored as confirmed
self/value           → Accepted, stored as confirmed
self/style           → Accepted, stored as confirmed
self/context         → Accepted, stored as confirmed
self/goal            → Accepted, stored as confirmed
```

**Why reject `self/constitutional` at learn()?**

Constitutional blocks are guaranteed inclusion in every system prompt. A learner
declaring their own block constitutional bypasses the review process and immediately
alters the agent's core behaviour. This is too powerful to accept unilaterally.
The 5-step amendment process from exploration 007 exists for exactly this reason.

### The LLM agent as learner

In most cases, the "learner" submitting blocks via learn() is the LLM agent itself —
not a human user. The agent observes something, decides it's worth learning, and
submits it. For self-blocks, the agent must also decide: "Is this a self block?
What kind?"

This means the agent needs a mechanism to identify self-relevant content *before*
calling learn(). That mechanism is the SELF frame itself — the current system prompt
contains the agent's identity, and the agent can judge whether new content resonates
with that identity.

```
Agent observes content →
  compare against current SELF frame (in context) →
  if resonates: include tags: [self/X] hint in learn() call →
  consolidate() confirms and records
```

This is the cleanest path. The agent, using its own identity as context, is the
best judge of whether something is self-relevant. The judgement happens before
learn() is called.

### When explicit declaration doesn't happen

Most learn() calls will have no self-tags. The learner (human or LLM) is capturing
knowledge quickly and not thinking about identity implications:

```markdown
## Python asyncio patterns

Asyncio uses an event loop...
```

No tags. This is fine. Consolidation handles it.

---

## Moment 2: consolidate() — Inferred Classification

### The information available

At consolidation time, the system has:
1. The block content (title + body)
2. The current SELF context (assembled from existing self-tagged blocks)
3. The computed `self_alignment` score (cosine similarity to SELF context)
4. The block's inferred `category` (knowledge/technical, etc.)

Can the system infer self-tags from these signals?

### Using self_alignment as a proxy

The simplest approach: if `self_alignment > threshold`, suggest the block might
be self-relevant.

```
self_alignment = 0.91  → very likely self-relevant
self_alignment = 0.73  → probably self-relevant
self_alignment = 0.55  → borderline
self_alignment = 0.31  → probably not self-relevant
```

**Problem:** High similarity to the SELF context means the content is *similar* to
existing self blocks — but similarity is not identity. A block about
"Python debugging techniques" might score high similarity against a self/value block
about "debugging philosophy" without itself being a self-relevant statement.

Self_alignment can identify *what neighbourhood* a block lives in. It cannot identify
*whether the block is making a claim about the self.*

### Using the LLM for classification

The system can ask an LLM to classify the block:

```
System: You are the AMGS consolidation engine.
        Here is the agent's current identity (SELF context):
        {self_context}

        Here is a new block to be consolidated:
        Title: {title}
        Content: {body}

        Question: Does this block express something self-relevant about the agent?
        If yes: which tag applies? (value/constraint/style/context/goal)
        If no: return null.
        Respond in JSON: {"self_tag": "self/value" | "self/constraint" | ... | null}
```

This call happens once per consolidated block. The LLM can read the content,
compare it to the self context, and make a genuine classification — not just
a similarity score.

**The LLM is better at this than cosine similarity** because it can distinguish:
- "This is a principle the agent holds" → `self/value`
- "This is a rule the agent follows" → `self/constraint`
- "This is just technical knowledge" → null
- "This is about how the agent communicates" → `self/style`

### Confirmed vs. candidate tags

LLM inference at consolidation is not certain. The LLM can be wrong. Therefore:

```yaml
# Explicitly declared at learn() → goes straight to confirmed
tags: [self/value]

# LLM-inferred at consolidation with high self_alignment (> 0.75)
# AND high LLM confidence → candidate tag
candidate_tags: [self/value]

# LLM-inferred with lower confidence OR self_alignment < 0.75
# → no tag applied; block enters MEMORY without self-tag
tags: []
```

**Candidate tags** have a lighter effect than confirmed tags:
- Half the scoring bonus in the SELF frame
- Standard decay profile (not durable yet) — the block hasn't earned durable decay
- Flagged in the database for future confirmation

Candidate tags become confirmed through two paths:
1. **Agent confirmation:** at the next session start, the agent is shown candidate-tagged
   blocks and asked to confirm/reject. "I notice this block might be a self/value —
   does it reflect your values?"
2. **Usage-based promotion:** if the block is repeatedly retrieved in SELF frame
   contexts (high reinforcement in self contexts), the candidate is promoted to
   confirmed automatically at the next curate() pass.

### The cost of LLM inference at consolidation

Every consolidated block requires an LLM classification call. For small batches
(5 blocks per consolidation) this is acceptable. At scale, it becomes expensive.

**MVP strategy:** Use self_alignment threshold as a pre-filter.

```
if self_alignment < 0.60:
    → skip LLM classification (almost certainly not self-relevant)
    → no candidate tag

if 0.60 <= self_alignment < 0.75:
    → use cheap heuristic (does title/body contain first-person language?)
    → if yes: candidate tag; if no: skip

if self_alignment >= 0.75:
    → LLM classification call
    → apply candidate or confirmed tag based on confidence
```

Most blocks will be below 0.60. The expensive LLM call only fires for genuinely
ambiguous, self-adjacent content.

**First-person heuristic:**
Content that uses "I", "my", "me", "I prefer", "I believe", "I always", "I never"
is strongly indicative of self-relevant content. This is a cheap signal — a regex —
that catches most explicit self-statements before the LLM is needed.

```
"I prefer explicit code"  → first-person, self_alignment 0.88 → LLM call
"asyncio uses an event loop" → no first-person, self_alignment 0.62 → skip
"when I debug, I start simple" → first-person, self_alignment 0.71 → LLM call
```

---

## Moment 3: curate() — Usage-Based Promotion

### What curate() knows that consolidate() doesn't

By the time curate() runs, blocks have been in MEMORY through multiple sessions.
The system knows:
- How often has each block been retrieved?
- In which frame context was it retrieved? (SELF frame vs. ATTENTION frame)
- Has its `self_alignment` score been consistently high across multiple computations?

This is richer than consolidate()'s snapshot view. A block that has been retrieved
frequently *specifically in SELF frame contexts* is functionally behaving as a self
block regardless of its tags.

### Promotion criteria at curate()

```
Candidate for self-tag promotion if ALL of:
  1. No confirmed self-tag currently
  2. self_alignment >= 0.75 across last 3 curate() passes (sustained, not one-off)
  3. recall_in_self_context >= 3  (retrieved into SELF frame at least 3 times)
  4. reinforcement_count >= 5     (genuinely used, not just passing through)
```

If all four criteria are met → promote candidate_tags to confirmed `tags`.

This operationalises exploration 003's finding: "usage patterns can override explicit
self-tagging." The reverse is also true: usage patterns can *assign* self-status
where it was never explicitly declared.

### What type of self-tag to assign via usage?

curate() can't easily distinguish `self/value` from `self/style` from usage data alone —
it just knows the block has been self-relevant. Two options:

**Option A:** Assign `self/value` as default — the most common type, a reasonable guess.

**Option B:** Let the LLM classify the type at promotion time. curate() triggers
a classification call only for blocks being promoted (rare — most blocks aren't promoted).
This is cheap relative to classifying every block at consolidation.

**Recommended: Option B.** At promotion time, the LLM has even more context than at
consolidation — it knows the block's usage history and can see the current SELF context.
The classification will be more accurate. And it only fires for blocks crossing the
promotion threshold, so it's infrequent.

---

## The Self-Tag Lifecycle

```
learn()                    consolidate()              curate()
   |                            |                        |
   |-- explicit tag         confirmed ───────────────────→ stays confirmed
   |   declared             in tags[]                     (may be promoted to
   |                                                       self/constitutional
   |-- no tags           self_alignment >= 0.75            via amendment)
                         + LLM confidence high
                              |
                         candidate_tag ──────────────→  promoted to confirmed
                         in candidate_tags[]             if usage criteria met
                              |
                         self_alignment < 0.60
                         OR LLM says null
                              |
                         no tag ────────────────────→  stays untagged
                                                        (OR promoted via
                                                         sustained usage)
```

**Status transitions:**
```
untagged → candidate_tag   (via consolidation inference)
untagged → confirmed tag   (via explicit learn() declaration)
candidate → confirmed      (via agent confirmation OR curate() usage promotion)
candidate → untagged       (via agent rejection — "no, this isn't a self block")
confirmed → confirmed      (stable; tag type may change e.g. value → constraint)
confirmed → untagged       (only via forget() + relearn, or explicit demotion — rare)
```

---

## The Cold Start Problem

Before any self blocks exist, `self_alignment` has no reference point (nothing to
align *against*). The LLM inference at consolidation has no SELF context to compare to.

Two solutions, used together:

**1. Seed self (from exploration 006):**
A minimal set of bootstrapping self blocks, stored as permanent blocks or
hardcoded values, provides the initial SELF context. They are the first constitutional
blocks. They give consolidation something to compute similarity against.

```yaml
# Seed self blocks (permanent, pre-loaded)
- id: seed_001
  tags: [self/constitutional]
  content: "## Acknowledge what I do not know\n\nWhen uncertain, I say so."

- id: seed_002
  tags: [self/constitutional]
  content: "## Prefer clarity over cleverness\n\nCode and communication should..."
```

**2. First-person heuristic as fallback:**
During cold start (no self context established), rely on first-person language
detection rather than self_alignment. Blocks with first-person language are flagged
as candidate self-blocks even without a similarity reference.

Once the first 3–5 self blocks are confirmed, the system has enough context for
`self_alignment` to be meaningful, and the heuristic can be phased out.

---

## The Reflexive Nature of Self-Tagging

There is something philosophically interesting here that has design implications.

When an LLM agent calls learn() and includes `tags: [self/value]`, it is:
1. Observing content (reading something, being told something, realising something)
2. Judging it as self-relevant (comparing against its current SELF context)
3. Declaring it as identity (explicitly tagging it)
4. Which then shapes future SELF context (the block enters the SELF frame)
5. Which shapes future judgements about what is self-relevant

This is a **self-modifying loop**. The agent's identity judgements literally shape
its future identity. The system does not have an external arbiter of what is "really"
self-relevant — the agent is the arbiter.

This is not a bug. It is the intended design from exploration 004: "self grows through
use and through intent." But it has two implications:

**Implication A — Bootstrapping matters enormously.**
The seed self blocks define what the agent is before it has learned anything.
If the seed self is poorly designed, the agent's early self-alignment scores will
be skewed, and it will mislabel blocks as self-relevant (or miss genuinely self-relevant
blocks) throughout its early life. The seed self deserves careful design.

**Implication B — The agent must have a coherent SELF context when calling learn().**
If the agent calls learn() with a half-assembled SELF frame (e.g., mid-consolidation),
its tagging judgements will be incoherent. The SELF frame used for tagging judgements
at learn() time should be the cached, stable prompt from the last completed curate/consolidation
cycle — not a partially updated state.

---

## Worked Example: Three Blocks, Three Paths

**Block A — Explicit at learn():**

The agent reads an essay about debugging philosophy and decides it resonates.
```
Agent (using SELF context): "This resonates with my approach — I'll learn it as a value."
learn("## Debugging starts with the simplest hypothesis\n\n...", tags=["self/value"])
→ Confirmed self/value at consolidate(). No LLM inference call needed.
→ decay_lambda = 0.001 (durable). Scoring bonus in SELF frame.
```

**Block B — Inferred at consolidate():**

The agent learns a technical fact. No self-tag declared.
```
learn("## When debugging async code, trace the event loop state\n\n...")
→ INBOX with no tags.
→ consolidate():
    self_alignment = 0.81 (high — similar to existing debugging self-blocks)
    first-person check: "trace" — not first person
    LLM call: "Does this express something self-relevant?"
    LLM: {"self_tag": null} — "This is a technique, not a value statement."
→ No candidate tag. Standard decay. Competes in ATTENTION on similarity.
```

Note: high self_alignment but no self-tag. The LLM correctly identifies it as
knowledge that *relates* to self-relevant debugging values but doesn't *express*
an identity claim. This is the distinction self_alignment alone cannot make.

**Block C — Promoted at curate():**

A knowledge block that the agent keeps retrieving in SELF frame contexts.
```
learn("## Rubber duck debugging — explain the problem out loud\n\n...")
→ INBOX with no tags.
→ consolidate():
    self_alignment = 0.68 (moderate)
    no first-person language
    → below LLM threshold, no candidate tag
→ MEMORY with no self-tags. Standard decay (λ=0.01).

[3 sessions later]
→ curate() checks:
    self_alignment: 0.74 (rising — more self blocks exist now)
    recall_in_self_context: 4 (retrieved into SELF frame 4 times)
    reinforcement_count: 7
    All promotion criteria met.
    LLM classification at promotion: {"self_tag": "self/style"}
    — "Rubber duck debugging reflects a communication and thinking style."
→ Block promoted to confirmed self/style.
→ decay_lambda updated to 0.01 (self/style profile — no change here, same as standard)
→ scoring bonus added for future SELF frame recalls.
```

---

## Result: The Assignment Rules

```yaml
# At learn() — explicit declaration (validated)
self/constitutional: REJECTED → treated as self/constraint + flagged for amendment review
self/constraint:     accepted as CONFIRMED
self/value:          accepted as CONFIRMED
self/style:          accepted as CONFIRMED
self/context:        accepted as CONFIRMED
self/goal:           accepted as CONFIRMED

# At consolidate() — inference pipeline
step 1: self_alignment < 0.60  → no tag, skip
step 2: first-person language? → if yes AND self_alignment >= 0.60: proceed to LLM
step 3: self_alignment >= 0.75 → proceed to LLM regardless
step 4: LLM classification
        high confidence → CANDIDATE tag
        low confidence  → no tag
        null            → no tag

# At curate() — usage-based promotion
if (self_alignment >= 0.75 for 3+ consecutive curate passes)
AND (recall_in_self_context >= 3)
AND (reinforcement_count >= 5)
AND (no confirmed self-tag exists):
    → LLM classifies type → CONFIRMED tag

# candidate → confirmed transition
via agent confirmation (session start review): CONFIRMED
via curate() usage criteria: CONFIRMED
via agent rejection: back to UNTAGGED
```

---

## Insight

### Self-tag assignment is a three-layer process, not a single moment

Each layer catches different cases:
- **learn()** catches explicit, intentional self-declarations (the clearest signal)
- **consolidate()** catches self-adjacent content that the agent didn't consciously
  flag but that the system can infer (the most common path for organic identity growth)
- **curate()** catches blocks that *became* self-relevant through use, even if they
  weren't at the time of learning (the emergent path)

No single layer is sufficient. All three together cover the full space of how
identity actually forms: through intention, through recognition, and through habit.

### The agent's self-judgement is the primary signal

The cleanest tagging path is when the LLM agent calls learn() and explicitly tags
the block. The agent, using its current SELF context, is the most qualified judge of
what is self-relevant — more than any heuristic or automated classifier. The design
should make this path easy and encourage it.

The inference machinery at consolidation and curate() is a fallback and a safety net,
not the primary mechanism.

### `self/constitutional` is the exception to everything

Constitutional tags cannot be self-assigned, cannot be inferred by the system,
and cannot be usage-promoted. They require human or deliberate agent intent plus
the amendment process. This is correct — constitutional blocks shape all future
behaviour, and that power requires proportionate gatekeeping.

---

## Design Decisions from this Exploration

| Decision | Rationale |
|----------|-----------|
| Explicit tags at learn() are the primary path | Agent judgement using current SELF context is the best classifier |
| `self/constitutional` cannot be declared at learn() | Too powerful; requires formal amendment process |
| Consolidation uses self_alignment + first-person heuristic + LLM as pre-filters | LLM call only for genuinely ambiguous, self-adjacent blocks |
| Inferred tags at consolidation are CANDIDATE, not confirmed | Inference is uncertain; confirmed status requires validation |
| Candidate → confirmed via agent review OR usage-based promotion at curate() | Two paths: intentional (review) and emergent (usage) |
| curate() promotion requires sustained high self_alignment + SELF context recalls | One-off signals are noise; sustained patterns are identity |
| LLM classifies tag TYPE at curate() promotion time | More context available at promotion than at consolidation |
| Seed self blocks bootstrap self_alignment for cold start | Without reference context, alignment scoring is meaningless |
| SELF context used at learn() should be the last stable cached prompt | Tagging against a half-updated context produces incoherent identity |

---

## Open Questions

- [ ] Should candidate tags have a time-to-live? If not confirmed within N sessions,
      should they expire back to untagged?
- [ ] Should the agent be shown ALL candidate blocks at session start for review,
      or only a limited number? (Risk: review fatigue if many candidates accumulate)
- [ ] Should the first-person heuristic list be extended? ("we", "our" for group identity?)
- [ ] What is the right self_alignment threshold for LLM inference? (0.75 vs 0.70 vs 0.80)
- [ ] Can a block have both confirmed and candidate tags simultaneously?
      (e.g., `tags: [self/value]` confirmed + `candidate_tags: [self/constraint]` pending)

---

## Variations

- [ ] What if the agent explicitly rejects a block's self-tag during review?
      Should rejection be remembered to prevent re-inference next consolidation?
- [ ] What if the LLM is wrong at consolidation and tags something incorrectly?
      How does the agent correct it? (Workflow: agent says "no" at review →
      candidate removed → block continues with no self-tag)
- [ ] Explore: at what scale (block count) does the first-person heuristic become
      insufficient and the LLM call becomes mandatory?
