# Title: Should Edges Have Types?

## Status: complete

## Question

Exploration 013 dropped edge types to simplify. But should at least one additional
type — specifically an `opposes` or contradiction edge — be added? Or is this overkill?
Reason through which edge types have genuine unique value vs. which are covered
by existing mechanisms.

---

## The Core Question

A standard similarity-based edge (`weight = 0.71`) means: "these two concepts are
related." But what about concepts that are *oppositely* related — that express
conflicting claims about the same topic?

Two blocks:
```
A: "Type annotations make code more maintainable — always annotate function signatures."
B: "Type annotations are ceremony. They add noise without improving runtime behaviour."
```

Both about the same topic (type annotations). High embedding similarity (~0.88).
A standard edge would be created between them with weight 0.88.

But this edge is wrong. It says "these are related" when the real relationship is
"these conflict." The edge misleads the scoring and retrieval system.

This is the case **for** at least one typed edge. The question is: how far should
this go?

---

## What Typed Edges Would Enable

| Edge type | What it enables | Can existing mechanisms cover it? |
|-----------|----------------|----------------------------------|
| `opposes` | Contradiction detection; prevent conflicting blocks co-appearing in frame | **No** — similarity score cannot detect semantic opposition |
| `elaborates` | "Show me all examples of this principle" | Partially — high similarity already surfaces related blocks |
| `supports` | Confidence propagation (well-supported claims gain confidence) | Partially — high co-retrieval reinforcement achieves similar effect |
| `precedes` | Procedural step ordering | Not relevant — blocks are concepts, not steps |
| `summarises` | Navigate from detail to overview | Partially — centrality captures this implicitly |

**The uniqueness test:** only `opposes` captures something that similarity fundamentally
cannot. All other types are approximated — imperfectly, but usably — by the existing
weight-and-centrality model.

The reason `opposes` is unique: **two conceptually opposing blocks have HIGH similarity
scores.** They discuss the same topic, use the same vocabulary. Cosine similarity will
correctly place them close together in embedding space — and therefore incorrectly
create a standard `relates_to` edge between them.

This is the only case where a similarity edge is actively misleading, not merely
incomplete.

---

## Evaluating the Other Types

### `elaborates` (A is a detailed version of B)

**What it would add:** Navigation — "given this principle, show me elaborations."
Preferential surface in certain query contexts.

**Why it's not needed:**
- High similarity already surfaces elaborations in ATTENTION queries
- A detailed block has more tokens and higher embedding similarity to related blocks —
  it naturally ranks well without special treatment
- Who creates the edge? Detecting "this is an elaboration of that" requires LLM
  understanding of hierarchy. Expensive and often ambiguous.

**Verdict: overkill.** The existing model handles this adequately.

---

### `supports` (A provides evidence for B)

**What it would add:** Confidence propagation — if A (evidence) has high confidence
and supports B (claim), B's confidence rises. Epistemic tracking.

**Why it's not needed for Phase 1:**
- At 50 blocks, confidence is set at consolidation and updated by curate().
  Propagation through a support graph adds significant complexity for marginal benefit.
- Co-retrieval already creates a weak version of this: if A and B are frequently
  retrieved together, their reinforcement counts rise together. The edge weight
  strengthens, which indirectly affects scoring.
- Detecting "A supports B" requires semantic reasoning, not similarity. Another
  expensive LLM call at consolidation.

**Verdict: useful Phase 2 feature for multi-hop reasoning, overkill for Phase 1.**

---

### `precedes` / `leads_to` (procedural ordering)

**What it would add:** Sequential retrieval — "after retrieving step 1, automatically
surface step 2."

**Why it's not needed:**
- AMGS blocks are designed as discrete, self-contained concepts (exploration 008/010).
  Procedural sequences ("first do X, then do Y, then do Z") are better captured
  in a single block, not split across three with ordering edges.
- If a process is complex enough to need multiple blocks, the TASK frame handles
  sequential assembly — not the graph.

**Verdict: wrong level of abstraction for this system.**

---

## The `opposes` Case in Depth

### Why the existing model fails for contradictions

When blocks A and B oppose each other, the standard pipeline produces:

```
similarity(A, B) = 0.88   → above 0.60 threshold
→ edge created: weight = 0.88
→ A and B become mutual neighbours in the graph
→ centrality of both increases (they have an edge to each other)
→ in recall(), both score well and may both appear in top-5
→ context frame contains contradictory blocks
→ LLM receives conflicting instructions / knowledge
```

The SELF frame has contradiction resolution (exploration 006) — it detects when
two blocks make conflicting claims at prompt assembly time. But this is late and
expensive: every assembly must scan for contradictions even when none exist.

With an explicit `opposes` edge, contradiction handling is pushed upstream:
- Known at consolidation time
- Blocks connected by `opposes` are automatically excluded from co-appearance
  in context frames
- No scanning needed at assembly — the relationship is already encoded

### How `opposes` edges are created

**Path 1 — Explicit learner declaration at learn()**

The learner signals: "this block challenges an existing belief."

```markdown
---
opposes: a3f9c2b1d84593e1
---

## Type annotations are often unnecessary ceremony

In dynamic languages, annotations add maintenance burden without
improving runtime behaviour. Lean on tests and documentation instead.
```

Clean and intentional. The learner knows what they're updating.

**Path 2 — LLM detection at consolidation**

When a new block has high similarity (> 0.75) to an existing block, the consolidation
pipeline makes a cheap LLM call:

```
"Do these two blocks make conflicting claims about the same topic?
 Block A: [content]
 Block B: [content]
 Answer: yes/no + brief reason"
```

This call is already justified by the self-tag inference pipeline (exploration 012).
For blocks above the 0.75 similarity threshold, an LLM call happens anyway.
Adding an `opposes` check to that call costs almost nothing.

```
if similarity > 0.75:
    LLM call (already planned for self-tag inference)
    ├── Does this block express self-relevance? → candidate_tag
    └── Does this block contradict block X? → opposes edge
```

Two questions, one LLM call, one threshold.

### How `opposes` edges behave differently from standard edges

| Behaviour | Standard edge | `opposes` edge |
|-----------|-------------|----------------|
| Created by | Similarity ≥ 0.60 | Explicit declaration OR LLM detection |
| Weight | Similarity score → usage-evolving | Fixed: strength of contradiction (0.5–1.0) |
| Co-retrieval reinforcement | Yes (weight += 0.05) | No — co-appearance is a failure, not success |
| Decay | Yes (disuse decay) | No — contradictions persist until resolved |
| Counts toward centrality | Yes | **No** — being contradicted ≠ being central |
| Effect at retrieval | Increases likelihood of neighbour appearing | Decreases likelihood of both appearing together |

The critical differences: `opposes` edges don't decay, don't reinforce, and don't
count toward centrality. They're a different kind of relationship — not associative
but adversarial.

### How `opposes` edges affect the system

**At recall():**
If block A is in the top-K and block B is connected to A via `opposes`:
```
if B is also in top-K:
    compare confidence(A) vs confidence(B)
    drop the lower-confidence block from the frame
    log: "suppressed B due to contradiction with A"
```

The higher-confidence block wins. The frame never contains both halves of a contradiction.

**At SELF frame assembly:**
Contradiction resolution (exploration 006) becomes trivial. Instead of scanning all
block pairs for semantic conflict, just query: `SELECT * FROM contradictions WHERE
block_a_id IN (selected_blocks) OR block_b_id IN (selected_blocks)`.

**At curate() — contradiction resolution over time:**
If one block in an `opposes` pair has substantially higher confidence AND higher
reinforcement_count after multiple curate passes, the system can auto-resolve:

```
if confidence(A) > confidence(B) + 0.30 AND reinforcement(A) > 2 × reinforcement(B):
    → flag B for review: "this block may be superseded by a stronger competing belief"
    → optionally: trigger forget(B) + mark contradiction as resolved
```

This is the interference mechanism from exploration 005 made explicit. One belief
displacing another isn't random — it's structured, traceable, and reversible.

---

## Is It Overkill?

### Arguments that it is overkill for Phase 1

- At 50 blocks, genuine contradictions will be rare. The agent is unlikely to have
  enough volume of self-relevant blocks to create meaningful contradictions in early use.
- The SELF frame's existing contradiction detection (exploration 006) already handles
  the most critical case (contradictions in the system prompt).
- Adding `opposes` edges means adding detection logic at consolidation, separate
  storage, and modified retrieval logic. Three places to implement and test.

### Arguments that it isn't

- The detection cost is near-zero if we're already making an LLM call at consolidation
  for the 0.75+ similarity threshold (exploration 012 established this).
- Without `opposes` edges, the system actively creates misleading edges. A weight-0.88
  edge between two contradicting blocks tells the scoring formula "these are highly
  related." Centrality goes up for both. Both appear in frames. The signal is wrong.
- Contradiction resolution in the SELF frame is a known design requirement. Explicit
  edges make it simpler to implement correctly, not harder.
- The schema change is minimal: one boolean field or one separate table of pairs.

### The honest verdict

**`opposes` is not overkill because similarity actively produces the wrong result
for contradictory blocks.** Every other edge type we could add is a "nice to have"
that approximates existing behaviour. `opposes` corrects a genuine failure mode.

The other types — `elaborates`, `supports`, `precedes` — are overkill. They add
complexity without correcting anything that's currently wrong.

---

## Implementation: Minimal `opposes` Model

A separate `contradictions` table, not a field on the main edges table. This is
cleaner because contradictions have a fundamentally different lifecycle
(no decay, no reinforcement, explicit resolution).

```sql
CREATE TABLE contradictions (
  block_a_id   TEXT NOT NULL,
  block_b_id   TEXT NOT NULL,   -- canonical min/max ordering as with edges
  strength     REAL NOT NULL,   -- how strongly do these contradict? (0.5–1.0)
  detected_at  TEXT NOT NULL,
  source       TEXT NOT NULL,   -- 'explicit' | 'llm_inferred'
  resolved     BOOLEAN DEFAULT FALSE,
  resolved_at  TEXT,
  resolved_by  TEXT,            -- block ID that won, or 'forgotten' if both removed
  PRIMARY KEY (block_a_id, block_b_id),
  FOREIGN KEY (block_a_id) REFERENCES blocks(id) ON DELETE CASCADE,
  FOREIGN KEY (block_b_id) REFERENCES blocks(id) ON DELETE CASCADE
);
```

**`strength`** represents how directly the blocks contradict each other:
- 0.9–1.0: direct logical opposition ("always do X" vs "never do X")
- 0.7–0.9: strong disagreement ("prefer X" vs "avoid X")
- 0.5–0.7: tension (same topic, different emphasis — may not be a real conflict)

Strength below 0.5 probably isn't worth recording as a contradiction — it's just
different perspectives on the same topic.

---

## Result: The Final Edge Model

```
Standard edges:   associative relationships
                  weight-based, decay, reinforce, count toward centrality

Contradictions:   opposing relationships (separate table)
                  strength-based, no decay, no reinforcement, excluded from centrality
```

Two relationship types, cleanly separated. Everything else is Phase 2+.

---

## Insight

### The test for any new edge type: does it correct a failure, or add a nicety?

`opposes` corrects a failure: similarity scoring produces actively wrong edges for
contradictory blocks. That justifies the complexity.

`elaborates`, `supports`, etc. are niceties: they add expressive power the system
doesn't currently need and doesn't currently mishandle. Defer them.

### Contradictions and the self-consistency property

A well-maintained SELF frame should not contain contradictions. But beliefs
genuinely do conflict — especially as the agent learns more over time. The `opposes`
mechanism is what keeps identity coherent: not by preventing contradictions from
entering memory, but by ensuring they don't co-appear in any context frame until
one has clearly won.

This is the soft-bias principle from exploration 004 applied to contradictions:
don't hard-gate conflicting information from entering memory. Let both coexist.
But manage how they appear together at retrieval and synthesis time.

---

## Design Decisions from this Exploration

| Decision | Rationale |
|----------|-----------|
| Only one additional edge type: `opposes` | Only type that corrects a genuine failure (similarity scores misrepresent contradictions) |
| `opposes` stored in separate `contradictions` table | Different lifecycle — no decay, no reinforcement, explicit resolution |
| All other edge types deferred to Phase 2+ | `elaborates`, `supports`, etc. approximate existing behaviour without correcting failures |
| `opposes` detected via explicit learner declaration OR LLM call at consolidation | LLM call is already triggered at similarity > 0.75 for self-tag inference |
| `opposes` edges excluded from centrality calculation | Being contradicted ≠ being conceptually central |
| At recall(): lower-confidence block suppressed if contradicting block also in top-K | Context frames must not contain active contradictions |
| Contradiction resolution: higher-confidence + higher-reinforcement block wins over time | Structured interference — traceable and reversible |

---

## Open Questions

- [ ] What is the right `strength` threshold for recording a contradiction?
      (0.5 catches tensions; 0.7 catches real conflicts — start at 0.7?)
- [ ] Should the learner be able to explicitly *remove* a contradiction record?
      (i.e., "these don't actually conflict, I was wrong to flag this")
- [ ] Should contradictions between non-self blocks matter? Or only for self-tagged blocks?
      (A contradiction between two knowledge/technical blocks has lower stakes than one
      between two self/value blocks)
- [ ] At what point does a contradiction auto-resolve vs. require agent review?
      (confidence gap of 0.30 is a starting guess — needs tuning)

---

## Variations

- [ ] What if the agent has a `self/value` block and a `knowledge/technical` block
      that contradict? Does the self/value block automatically win? Should it?
- [ ] Explore `supports` edge for Phase 2: if block A (evidence) strongly supports
      block B (claim), does propagating confidence from A to B improve calibration?
- [ ] What if two contradicting blocks are both high-confidence and high-reinforcement?
      Neither "wins" — the agent has a genuine unresolved tension. How should this surface?
