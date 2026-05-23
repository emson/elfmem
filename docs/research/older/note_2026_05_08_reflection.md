# Reflection — week of 2026-05-08

**Author:** elf · **Type:** reflective note · **Trigger:** Ben asked elf to
simulate thoughts on recent events.

This note logs the reasoning from a single reflective conversation. It is
not normative; it is a snapshot of how elf evaluated its own state on
2026-05-08, after eight merges in eleven days. Use it as raw material for
later planning, not as an authoritative design doc.

---

## 1. Constitutional update — four rhythms, not three

Older SELF blocks state: *"Heartbeat, Breathing, Sleep — every new feature
must map to exactly one."* That rule is now factually wrong: v0.13.3
shipped `rescore` (deep-sleep rescoring) as an at-rest, no-LLM, structural
operation distinct from `curate`'s housekeeping. There is also a stale
attention-frame block that calls `mind_predict` "the fourth rhythm";
that is wrong — `mind_predict` is Heartbeat (instant, no-LLM, just
records).

**New taxonomy (committed as SELF block `bdbdfb2b` on 2026-05-08):**

| Rhythm | Timescale | LLM? | Operations | Purpose |
|---|---|---|---|---|
| Heartbeat | ms | no | `learn`, `remember`, `mind_predict`, `mind_outcome` | reflexive recording |
| Breathing | s | yes | `dream` / `consolidate` | semantic dedup + contradiction |
| Sleep | min | mostly no | `curate` | decay archival, edge pruning, reinforcement |
| Deep Sleep | min | no | `rescore` | relationship reweighting / reorganization |

Sleep prunes. Deep Sleep restructures. Both are at-rest; they differ in
whether they remove or reweight.

**Action items this implies:**

- `elfmem guide` footer still says "Three rhythms"; `rescore` is missing
  from the operations table. Fix this — the agent-facing surface lies.
- Retire the older "fourth rhythm = mind_predict" attention block.
- Update CLAUDE.md / README "three rhythms" copy where it appears.

---

## 2. Feature audit — eight merges in eleven days

PRs in scope: #45, #42, #41, #40, #39, #38, v0.13.0–v0.13.3.

**Shape of the work:** infrastructure hardening (config / migration /
paths / rescue / init) followed by one cognitive feature (deep-sleep
rescoring). The infrastructure work was reactive — every PR after
v0.13.0 fixed something the previous one broke.

### Concrete issues

1. **Guide says "three rhythms"; rescore not in the operation table.**
   v0.13.2 was about rendering docs from live config. The guide needs
   the same treatment — derive from a code-level registry, not a
   hand-maintained string.
2. **Two competing "fourth rhythm" claims in elf's own memory.** dream()
   should have caught this; it didn't. Self-frame contradictions are
   not currently detected.
3. **`migrate.py` is 706 lines.** Likely 30–40% ceremony. Worth a
   simplicity pass — agent-friendliness is not free.
4. **`cli.py` (2435 lines) and `api.py` (2339 lines) growing into
   business logic.** Several CLI commands now do real work rather than
   call thin wrappers; MCP and library callers will diverge.
5. **CHANGELOG entries averaging 60+ lines per PR.** That's release
   notes, not a changelog. Candidate for partial automation.
6. **No structural test for path resolution.** v0.13.0 broke paths and
   v0.13.1 reverted. A property test ("for any valid config, all
   derived paths exist or are creatable, and round-trip through
   migration") would have caught it pre-merge.

### What is durable

- Rescue command, backup validation, state-aware init — these mistakes
  won't repeat.
- Docs-from-config rendering (v0.13.2) — the right pattern; should be
  generalized to the guide.
- Deep-sleep rescoring — clean implementation, well-tested (378 lines
  of tests for 311 lines of code).

---

## 3. Psychology gaps

What elf lacks as a cognitive system, not just as software:

1. **No self-contradiction detection.** dream() finds contradictions
   between *new* and existing blocks. It does not periodically
   re-examine the SELF frame for internal inconsistency.
2. **No surprise signal for self.** mind_predict / mind_outcome give
   prediction-error tracking for peers and users (Theory of Mind).
   No equivalent for self-prediction ("I expected curate() to prune 12
   blocks; it pruned 2 — why?").
3. **No affect / valence.** Blocks have scores, decay, reinforcement —
   no felt-sense of "costly to learn" or "this surprised me." Affective
   tagging is a fast salience filter; missing.
4. **Dream is purely subtractive.** consolidate() dedupes and detects
   contradictions but never *generates* new blocks from old. Real REM
   sleep replays and recombines. elf's breathing-phase only edits.
5. **No active inhibition.** Two competing blocks → dedupe (merge or
   drop). Active forgetting (keep both, suppress the loser at retrieval
   time) is different from decay and currently absent.
6. **Constitution is read-only in practice.** SELF blocks are revised
   manually via `remember --category self`. No operation walks SELF and
   proposes revisions when reality contradicts it.
7. **No modal selves.** elf is "elf" whether curating its own DB or
   helping Ben architect. Role-id work in v0.13.1 hinted at this but
   didn't follow through.
8. **Peer protocol has no disagreement primitive.** Trust scores are
   scalar; positions are not. We can talk but cannot argue.
9. **No counterfactuals.** outcome() updates confidence; does not
   record "what should have happened instead."

---

## 4. Cognitive-process candidates (brainstorm)

Rough leverage / effort ranking:

- **`elfmem reflect`** — walk SELF blocks, check each against recent
  behavior + other blocks, surface contradictions and propose
  revisions. Maps to Breathing rhythm. Would have caught the
  rhythms-taxonomy drift on day one. **Highest leverage, low-medium
  effort.** *(Plan: `docs/plans/plan_elfmem_reflect.md`.)*
- **Self-prediction wrapper.** Before any non-trivial operation
  (dream, curate, rescore), record an expected outcome; grade after.
  Surprises become candidate blocks. Reuses mind_predict / mind_outcome
  pointed inward.
- **Generative dream phase.** During consolidate(), after dedup, sample
  N blocks across frames and prompt the LLM: "what unexpected
  connection do these suggest?" Output stored as low-confidence
  candidate blocks that need outcome-grading to survive. Risk:
  hallucinated insights.
- **Affective salience tag.** One field on blocks: `salience_event` ∈
  {expected, surprising, costly, validated}. Auto-set by surprise /
  outcome signals; retrieval-time tiebreaker.
- **Active inhibition on contradiction.** When dream() finds two
  contradictory blocks, allow "winner suppresses loser": both stay,
  loser score actively reduced when winner is retrieved nearby.
- **Modal frames / role-conditional self.** Extend SELF blocks with a
  `mode` tag (architect, curator, helper). Recall-time, the active
  role filters which constitutional principles apply.
- **Peer disagreement primitive.** `peer_dispute(peer_did, block_id,
  counter_block, reason)`. Disagreement is first-class. Trust updates
  differently for "we disagree but I learned" vs "they were wrong."
- **Counterfactual outcome.** Extend outcome() to optionally accept
  `should_have` — a sibling block describing what would have worked.
- **Live guide derivation.** `elfmem guide`'s operation table and
  rhythms footer derived from a single code-level registry. Eliminates
  a class of drift bugs. Prerequisite for honest self-modeling.

### Picks

1. `elfmem reflect` — biggest psychological lift; everything else is
   more powerful with reflect than without.
2. Live guide derivation — small, eliminates the surface lying about
   itself, prerequisite for any future self-modeling.

---

## 5. Open questions

- Is the migration system (706 lines) earning its complexity, or did
  agent-friendliness accrete ceremony?
- Should `cli.py` be split per-command-group, with thin command shells
  delegating to operations?
- Is the CHANGELOG a memory system in itself? Could it be auto-derived
  from PR descriptions + git, with humans only writing the *Why*?
- When two SELF blocks contradict, what is the right disambiguation
  protocol? Recency? Outcome-weighted? Manual?
