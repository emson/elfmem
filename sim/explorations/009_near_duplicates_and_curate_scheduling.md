# Title: Near-Duplicate Handling and Curate Scheduling

## Status: complete

## Questions

1. **Near-duplicates:** When a new block is nearly identical to an existing one,
   should we merge them — or does that violate our data immutability principle?
   If not merge, then what?

2. **Curate scheduling:** Weekly curate has the same holiday problem as wall-clock decay.
   How should curate() be triggered so it doesn't run wastefully during idle periods
   and doesn't fail to run during active use?

---

## Question 1: Near-Duplicate Handling

### The situation

During consolidate(), a new block B arrives with similarity 0.92 to existing block A.
Both express essentially the same concept, but B has slightly better phrasing or
updated information (it's newer).

Options:
1. **Reject B** — A already exists, B is redundant
2. **Accept B alongside A** — both exist, retrieval surface duplicates
3. **Merge A+B into a new block C** — create a synthesis
4. **Forget A, create B** — replace with new, transfer useful metadata

### The principle at stake

From the design: **blocks are immutable once created.** Their content is never edited.
This principle exists for important reasons:

- **Provenance:** every block has a known origin (who submitted it, when, from what source)
- **Auditability:** the system's knowledge can be traced back to specific learn() calls
- **Trust:** a block that changes silently cannot be trusted; its confidence score becomes meaningless
- **Simplicity:** immutable blocks are easy to cache, hash, and version

Merging violates this. If we merge A and B into C:
- C's content is not from any single learn() call — it was synthesised by the storage layer
- C's confidence score is arbitrary (average? max? unknown)
- C's provenance is muddied — who "said" this? A's source? B's source? The system?
- If C is wrong, we cannot trace it back to a specific input

This is not a theoretical concern. If the system learns contradictory things, we need to know
WHICH inputs created the contradiction. Merged blocks destroy that signal.

**Conclusion: merging is off the table.** It violates immutability and breaks provenance.

---

### Evaluating the remaining options

#### Option 1: Reject B (keep A)

```
A: "Python asyncio uses an event loop to schedule coroutines..." [conf=0.72, reinforcement=8]
B: "Python asyncio schedules coroutines via an event loop..." [new, better phrasing]
→ B rejected. A survives.
```

**Problem:** The system cannot be corrected. If A has a subtle error or outdated
information, submitting B (the correction) does nothing. The system is frozen at
the moment of first learning. This is an echo chamber of a different kind — not
identity-based but content-based. The learner loses trust: "I submitted a correction
and nothing changed."

#### Option 2: Accept B alongside A

```
A: conf=0.72, reinforcement=8
B: conf=0.53, reinforcement=0
Both in MEMORY.
```

**Problem:** Every recall() for related queries surfaces both A and B, consuming
two slots in the context frame for one concept. The LLM receives slightly different
phrasings of the same thing — redundant context, wasted tokens. Over time, near-duplicates
accumulate and the context frame becomes polluted. This is the "noisy memory" failure mode.

#### Option 3: Forget A, create B (with inheritance)

```
forget(A)  →  A is flagged deleted, removed at next maintenance pass
create(B)  →  B enters MEMORY as a new, immutable block
B.inherits_from = A  →  B inherits A's reinforcement_count and edges
```

**This preserves immutability:** B is a wholly new, untouched block. Its content is
exactly what was submitted via learn(). A is deleted, not mutated.

**This preserves provenance:** B's source is the learn() call that submitted it.
A's deletion is logged (deleted_by: consolidation/near-duplicate, superseded_by: B.id).

**This allows correction:** submitting B (an update to A) results in B replacing A.
The learner's correction takes effect.

**This avoids duplication:** only one block per concept exists in MEMORY.

**The inheritance mechanism** — what carries forward from A to B:

```yaml
B.reinforcement_count = A.reinforcement_count
# Rationale: B represents the same concept A did. The usage history
# of that concept belongs to B now. Starting B at 0 would falsely
# make it look brand new and risk early pruning.

B.edges = A.edges (transferred to B, pointing to same neighbours)
# Rationale: A's graph position reflects how this concept relates to
# others. B occupies the same conceptual position.

B.confidence = max(A.confidence, 0.53)
# Rationale: if A was well-calibrated (high confidence), that
# calibration carries partial weight. But B starts fresh otherwise.

B.decay_lambda = newly computed (based on B's category and self_alignment)
# Rationale: B may have different self_alignment than A, so the
# decay profile is recomputed from scratch.

B.hours_since_reinforcement = 0
# Rationale: B is new. The clock resets.

B.self_alignment = newly computed
# Rationale: SELF may have evolved since A was consolidated.
# B gets a fresh alignment score against the current SELF context.
```

**What about A's deletion?**

A is not immediately removed from MEMORY. Instead:
```yaml
A.status: superseded
A.superseded_by: B.id
A.decay_lambda: 0.5  # very fast decay — will be pruned at next curate()
```

A survives briefly in case:
- An in-flight recall() is using A as part of an active context frame
- An audit needs to see what was replaced

At next curate(), A's decay_weight will be effectively 0 and it is pruned cleanly.

---

### Threshold design

Two distinct similarity thresholds:

```
similarity > 0.95: exact duplicate → reject B outright (no new information)
  Same concept, same phrasing. Learning the same thing twice is noise.
  A is untouched. B is silently discarded.

0.90 < similarity <= 0.95: near-duplicate → forget(A) + create B + inherit
  Similar concept, different phrasing or updated information.
  The learner probably intends to update or refine the existing block.
  A is superseded. B takes its place.

similarity <= 0.90: distinct enough → B added to MEMORY normally
  The concepts differ enough to coexist. Both are kept.
  An edge may be created between them (similarity > 0.60 threshold from 008).
```

**Why 0.95 for exact vs. 0.90 for near?**

At 0.95+, two blocks are so similar that no meaningful new information can
be extracted from B. The phrasing difference is noise. Accepting B would
just add a near-identical block with no benefit.

At 0.90–0.95, the blocks overlap heavily but differ enough that B might
be a correction, a nuance, or a restatement. The presumption is that the
learner is submitting B intentionally — they want this version to be known.

**Worked example:**

```
A: "Python asyncio uses an event loop to schedule coroutines. Use async def..."
   confidence=0.72, reinforcement=8, edges=[M1, M6]

B: "Python asyncio schedules coroutines using an event loop. Use async def and
    await to yield control. Tasks can be cancelled with task.cancel()."
   (new, more complete)

similarity(A, B) = 0.93  → near-duplicate range

Action:
  1. Mark A as superseded (A.status=superseded, decay_lambda=0.5)
  2. Create B with full metadata
  3. B.reinforcement_count = 8   (inherited from A)
  4. B.edges = [M1, M6]          (inherited from A)
  5. B.confidence = max(0.72, 0.53) = 0.72
  6. B.self_alignment = [recomputed]
  7. B.decay_lambda = 0.01       (recomputed: knowledge/technical, moderate alignment)
  8. B.hours_since_reinforcement = 0

Next curate(): A.decay_weight ≈ 0.0 → pruned. B survives with A's history.
```

---

### INBOX dedup extension

This same logic applies within the INBOX itself, before blocks reach MEMORY.
During consolidate(), before comparing against MEMORY:

```
Step 0 (new): Dedup within INBOX

For each pair (I_i, I_j) in INBOX:
  if similarity(I_i, I_j) > 0.95: discard I_j (exact duplicate within batch)
  if 0.90 < similarity(I_i, I_j) <= 0.95:
    keep I_j (the later submission wins — newer version)
    discard I_i
```

This prevents two near-identical blocks submitted in the same batch from both
entering MEMORY. The later submission is presumed to be the intended version
(same reasoning as above — learner submitted an update).

---

### Summary: near-duplicate handling

```
Exact duplicate (> 0.95):   reject new block entirely
Near-duplicate (0.90–0.95): forget(old) + create(new) + inherit metadata
Distinct (≤ 0.90):          add normally, create edge if > 0.60
```

Blocks are never mutated. Only created and deleted. Immutability and provenance
are preserved throughout.

---

## Question 2: Curate Scheduling

### The problem

In exploration 008, curate() was described as running "weekly." But weekly is a
wall-clock interval. The holiday problem from exploration 005 applies directly:

> If the system is idle for two weeks, a weekly curate() fires twice during
> the holiday, finds no new blocks to process, and wastes work.
> Worse: if using wall-clock decay, two curate() passes during idle might
> prune blocks that "decayed" during a period of genuine non-use.

More precisely, there are two sub-problems:

**Sub-problem A: Curate runs during idle (wasteful)**
Wall-clock scheduled curate() fires on schedule regardless of system activity.
It processes memory, finds mostly unchanged blocks, does little work, and logs
the event. Harmless but wasteful.

**Sub-problem B: Curate never runs during active use (dangerous)**
If curate() only runs on schedule and the schedule misaligns with use patterns,
blocks may accumulate past the soft cap, or high-value blocks may go unreinforced
by curate() for too long.

**Sub-problem C: Pruning avalanche on return**
If wall-clock decay is used (not session-aware) AND curate() is session-aware,
the return from holiday triggers a single curate() pass that sees N weeks of
accumulated decay. Many blocks fall below threshold simultaneously. The system
prunes aggressively on the first session back.

This is the mirror of the holiday problem: knowledge survived the holiday intact
(you didn't forget Python during two weeks on a beach) but the system deletes it
the moment you return.

---

### The key dependency

Curate scheduling cannot be designed independently of the decay model.

From exploration 005, Phase 1 uses **session-aware decay**:
- The clock only advances during active sessions
- Idle periods (holidays, gaps between uses) do not advance `hours_since_reinforcement`
- Blocks do not age when the system is not in use

With session-aware decay, Sub-problem C disappears. Blocks haven't decayed during
the holiday (the clock was paused), so curate() on return does not trigger
an avalanche. It simply sees blocks that aged only during active hours.

This means the right scheduling unit for curate() is **active hours**, not calendar time.

---

### Evaluating trigger strategies

#### Strategy 1: Active-hours threshold

```
Trigger: active_hours_since_last_curate >= threshold
Default threshold: 40 active hours
```

40 active hours is roughly:
- Light user (2h/day): 20 calendar days between curates
- Moderate user (4h/day): 10 calendar days
- Heavy user (8h/day): 5 calendar days

**Pro:** Consistent with session-aware decay. Curate runs in proportion to use.
A system used infrequently curates infrequently (correct — less memory churn).
A system used heavily curates more often (correct — more blocks to maintain).

**Con:** A system used 2h/day might go 20 calendar days without curating. During
that time, blocks accumulate and memory grows. If a block reaches the prune
threshold via active-hours decay but curate() hasn't run, the block lingers.

**Mitigation:** The block lingers but doesn't harm recall() — it scores poorly
and isn't returned. It's just taking up space. Acceptable in Phase 1.

---

#### Strategy 2: Block-count pressure trigger

```
Trigger: block_count > soft_cap
Default soft_cap: 50 blocks
```

**Pro:** Keeps memory lean regardless of time. If you learn rapidly, curate runs
more often to make room. If you learn slowly, curate runs rarely (correct — less
to maintain).

**Con:** If you never exceed the soft cap, curate never runs. Blocks accumulate
decay debt and high-value blocks never get curate's reinforcement boost. Memory
stagnates rather than self-organises.

**Con:** A sudden burst of learn() calls could push block_count to 501, triggering
curate() mid-consolidation. Operationally awkward.

---

#### Strategy 3: Session-start trigger

```
Trigger: at the start of every session, if active_hours_since_last_curate >= threshold
```

This is Strategy 1 but with a natural execution point: the beginning of a user session.

**Pro:** Runs at a moment when the system is already active. No surprise mid-session
delays. The user starts a session, curate runs in the background, memory is clean
for the session.

**Pro:** If the system has a long gap (holiday), the first session back triggers
curate if the active-hours threshold is met. Because of session-aware decay, the
blocks haven't aged during the gap — curate finds a healthy memory, not an avalanche.

**Con:** If sessions are very short (5 minutes), the threshold may never be met
within a session cycle that happens to check at start. Mitigated by the threshold
being active-hours-based — a 5-minute session adds 0.083h, so threshold is met
across many sessions.

---

#### Strategy 4: Combined (recommended)

```
Trigger curate() when ANY of:

  A. active_hours_since_last_curate >= 40    (primary: usage-proportional)
     → checked at session start

  B. block_count > soft_cap                  (secondary: space pressure)
     → checked after every consolidate()

  C. explicit call                           (tertiary: on demand)
     → amgs curate / POST /memory/curate
```

This combines the strengths of all strategies:

- **A** ensures curate runs regularly in proportion to use, not calendar time
- **B** ensures memory doesn't grow unbounded even if A's threshold isn't met
- **C** gives operators and tests explicit control

**Execution model:**
```
Curate is always async and non-blocking.
It runs in the background after the session-start check completes.
The session proceeds normally while curate works.
```

---

### Worked scenario: the holiday problem for curate()

**Setup:** System last curated at t=0. User goes on holiday t=20h to t=340h
(320 calendar hours = ~13 days). Session-aware: active hours only count when system is open.

```
t=0h:    Last curate(). active_hours_since_last_curate = 0.
t=20h:   Session ends. Accumulated 20 active hours.
t=340h:  User returns. No active hours accumulated during holiday.
         active_hours_since_last_curate = 20  (not 320)
t=340h:  New session starts.
         Check: 20 active hours < 40 threshold → DO NOT curate.
t=360h:  Session ends. 40 active hours accumulated.
t=360h:  Next session starts.
         Check: 40 active hours >= 40 threshold → CURATE.
```

Curate fires at t=360h (after two sessions back, not during the holiday, not the moment
of return). Blocks are examined. Because of session-aware decay, blocks have only 40
hours of decay applied (not 340). No pruning avalanche. Business as usual.

**Compare with wall-clock scheduled curate (the bad version):**

```
t=0h:    Last curate().
t=168h:  Scheduled curate fires. User is on holiday. No active sessions.
         Wall-clock decay: blocks have aged 168h since last reinforcement.
         Standard blocks (λ=0.01) at t=168h: decay_weight = e^(-0.01×168) = e^(-1.68) = 0.186
         Still above threshold. No pruning. Wasted run but harmless this time.
t=336h:  Scheduled curate fires again. User still on holiday.
         Standard blocks: decay_weight = e^(-0.01×336) = e^(-3.36) = 0.035 < 0.05 → PRUNED
         Knowledge deleted during holiday.
```

Session-aware scheduling avoids this entirely.

---

### The mini-curate question

Should consolidate() run a lightweight curate pass after each consolidation?

**Arguments for:**
- Keeps memory clean incrementally rather than in large periodic batches
- Prevents space pressure building up between scheduled curates
- Natural integration point: consolidation already scores blocks

**Arguments against:**
- Consolidation is already the heavyweight step; adding curate work slows it further
- Curate's reinforcement of top-scoring blocks needs a full memory view to be meaningful
  (reinforcing top-K from a 5-block consolidation is not the same as top-K from 500 blocks)
- Confuses the single-responsibility principle: consolidation moves blocks into memory,
  curate maintains memory

**Decision: no mini-curate.** Consolidation and curate remain separate. The block-count
pressure trigger (Strategy B) handles space pressure between scheduled curate passes.
If block_count exceeds soft_cap, a full curate runs — not a partial one.

---

## Result

### Near-duplicate handling (locked)

```yaml
duplicate_thresholds:
  exact:     0.95    # reject B silently — same content, no new information
  near:      0.90    # forget(A) + create(B) + inherit(reinforcement, edges, confidence)
  distinct:  0.60    # create edge between A and B (from 008)

inheritance_on_near_duplicate:
  reinforcement_count: copied from A to B
  edges: transferred from A to B
  confidence: max(A.confidence, initial_confidence)
  self_alignment: recomputed fresh
  decay_lambda: recomputed fresh
  hours_since_reinforcement: 0

deletion_on_supersede:
  A.status: superseded
  A.superseded_by: B.id
  A.decay_lambda: 0.5   # very fast — effectively pruned at next curate()
  A.deleted_at: [timestamp of consolidation pass]
```

### Curate scheduling (locked)

```yaml
curate_triggers:
  primary:
    type: active_hours_threshold
    threshold: 40               # active hours (not calendar hours)
    check_point: session_start
    execution: async background

  secondary:
    type: block_count_pressure
    soft_cap: 50                # configurable; start small, tune upward
    check_point: post_consolidation

  tertiary:
    type: explicit
    interfaces: [cli, api, sdk]

curate_does_NOT_run:
  - During idle periods / holidays
  - Mid-session (checked at start, runs in background)
  - After every consolidation (no mini-curate)
  - On a wall-clock schedule
```

---

## Insight

### Immutability is non-negotiable

Merging blocks was the intuitive solution but it breaks too many things: provenance,
auditability, confidence calibration, trust. The "forget + create + inherit" approach
achieves the same practical outcome (one block per concept) without violating the
data model. The key insight: **inheritance transfers the history, not the content.**
The new block's content is exactly what was submitted. The old block's usage history
lives on in the new block's metadata.

### Curate scheduling mirrors decay design

The right scheduling unit for curate() is active hours, for the same reason that
session-aware decay uses active hours: idle periods are not periods of forgetting.
Curate should run in proportion to system use, not in proportion to calendar time.
This keeps the system consistent: both decay and maintenance operate on the same
time model.

### The three triggers cover all failure modes

- **Active-hours threshold** covers the normal case: regular use, proportional maintenance
- **Block-count pressure** covers the burst case: rapid learning fills memory faster than scheduled curate
- **Explicit call** covers operational control: tests, migrations, manual intervention

No wall-clock scheduling anywhere in the system. Everything is session-aware or event-driven.

---

## Design Decisions from this Exploration

| Decision | Rationale |
|----------|-----------|
| No merging — blocks are immutable once created | Provenance and auditability require traceable, single-source blocks |
| Near-duplicate: forget(A) + create(B) + inherit | Allows correction without mutation; immutability preserved |
| Exact duplicate (>0.95): silently reject new block | No new information; dedup is correct behavior |
| Inheritance transfers reinforcement, edges, confidence | Usage history of a concept belongs to whatever block currently holds it |
| A is marked superseded, fast-decayed, pruned at next curate | Avoids immediate deletion of in-flight references |
| Curate triggered by active-hours, block-count, or explicit call | No wall-clock scheduling anywhere; consistent with session-aware decay |
| Primary threshold: 40 active hours | Proportional to use; immune to holidays |
| Secondary threshold: 50 blocks (start small) | Space pressure failsafe; independent of time |
| No mini-curate inside consolidate() | Single responsibility; full memory view needed for meaningful top-K reinforcement |

---

## Open Questions

- [ ] What is the right active-hours threshold? (40h vs. 20h vs. 80h) — needs tuning with real use data
- [ ] What is the right soft_cap? (starting at 50; tune upward with real use data)
- [ ] When A is superseded by B, should B explicitly notify the graph?
      (Neighbours of A have edges to A.id — do they need updating to point to B.id?)
- [ ] Should the prune log (recording superseded blocks) be user-accessible for review?
- [ ] What happens if the user submits B but intends it as a separate concept, not a replacement?
      (similarity=0.93 but genuinely distinct ideas) — can the learner signal "this is new"?

---

## Variations

- [ ] What if a block has been reinforced 50 times and a near-duplicate arrives?
      Should high reinforcement override the forget(A) + create(B) decision?
- [ ] What if three INBOX blocks are all near-duplicates of each other?
      Which one wins? (Last submitted? Longest? Highest self-alignment?)
- [ ] What if curate() runs and block_count = 501, pruning 2 blocks to 499?
      Next consolidation adds 5 blocks → 504. Does curate run again immediately?
      (Need a cooldown or hysteresis band)
