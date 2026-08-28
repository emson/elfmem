# Block-Ledger Synthesis - evaluating the "text is the store, everything else is a derived view" proposal against elfmem as built

**Status**: research document - under review, not yet a plan
**Author**: elf (with Ben Emson)
**Date**: 2026-08-24
**Branch audited**: `elfmem_index` @ `4de47cd` (+ uncommitted substrate-migrate work)
**Driver**: two external research notes proposing a block-level, ledger-derived, frame-pluggable
memory architecture. The question put: does this improve elfmem, or should elfmem become this?

**Companion documents** (read these first if you have not):
- [`docs/research/v2_substrate_and_reasoning_ownership_research.md`](v2_substrate_and_reasoning_ownership_research.md) - the 2026-08-08 substrate + reasoning-ownership research this document extends
- [`docs/plans/plan_v2_substrate_reevaluation.md`](../plans/plan_v2_substrate_reevaluation.md) - block format, mutation API, six-phase migration
- [`docs/plans/v2_substrate/plan/`](../plans/v2_substrate/plan/) - the build plan, model, and dry-run results for Waves 1-4

---

## Verdict, up front

**Neither pivot nor reject. The proposal is best read as the missing back half of elfmem's own
v2 plan, and three of its mechanisms should be adopted now because they close gaps elfmem has
already named, assigned to nobody, and become blocked on.**

Three findings drive this:

1. **There is no substrate disagreement to resolve.** The proposal's load-bearing decision -
   markdown blocks are truth, the database is a derived, rebuildable view - is the decision
   elfmem already made on 2026-08-08, corroborated by three independent 2026 systems, and has
   already built four of six migration waves for. Pivoting would discard ~6,600 lines of
   tested migration machinery in order to re-derive the same conclusion.

2. **elfmem's migration is stuck, and the proposal's central mechanism is exactly what
   unsticks it.** The Phase 4 retrieval-parity gate reports `GATE PASSED: False` with 5/5
   queries diverging, and it is *structurally unpassable*: rebuild zeroes `reinforcement_count`,
   `last_reinforced_at`, `decay_lambda`, `created_at`, and the entire `edges` table, and three
   of those are live terms in the ranking formula. An **append-only event ledger makes every
   one of them replayable**. This is the highest-value idea in the notes for elfmem
   specifically, and neither note frames it that way.

3. **Three of the proposal's most-loved mechanisms walk directly into measured rejections
   already recorded in this repo.** ACT-R base-level activation is power-law decay, which
   ADR 0001 rejected on simulation showing -53pp plasticity. Learned score calibration
   requires "used" labels, of which the live instance has produced **six in four months**.
   Representation-degrading collapse is decay-driven archival wearing a better hat, and
   ADR 0009 retired that on the finding that it **never fired in production**.

The synthesis below adopts five mechanisms, measures three, defers three behind explicit
triggers, and rejects two. It also proposes one mechanism that appears in neither note - the
**cue-collision test** - which turns the silent-supersession path that motivated the whole v2
programme from a guarded exception into a structural impossibility. Section 9 proposes the
retrodictive experiment that would confirm or kill it before it ships.

---

## 1. The proposal, restated as fourteen separable mechanisms

The two notes are one design. Separating it into mechanisms is the only way to evaluate it,
because they have wildly different costs and wildly different evidence behind them.

| # | Mechanism | One-line summary |
|---|---|---|
| P1 | Files are truth, index is derived | Markdown blocks are the source of truth; every score is a materialised view |
| P2 | Dual-hash identity | Permanent birth-hash ID + per-version content hash |
| P3 | Simhash fingerprint | 64-bit locality-sensitive hash for dedup, move/orphan reconciliation, clustering |
| P4 | Append-only event ledger | One line per write/read/link/use/contradiction; all scores derived from it |
| P5 | ACT-R salience | `A ~= ln(n/(1-d)) - d*ln(L)`, O(1) from access count `n` and age `L` |
| P6 | Confidence by volatility class | Separate exponential half-life per class (identity / project / status) |
| P7 | Degradation as representation change | vivid -> fading -> collapsed -> archived; LLM rewrites to gist, git holds the original |
| P8 | Surprise-gated writes | Write only if the memory would have changed the last response |
| P9 | Mandatory cue line | Every block states when a future agent should recall it |
| P10 | Typed links, small vocabulary | supports / contradicts / refines / derived-from / requires / supersedes |
| P11 | Spreading activation / personalised PageRank | Query-seeded graph walk instead of static centrality |
| P12 | Frames as text-defined plugins | Frame = a file declaring trigger / filter / rank / budget |
| P13 | Calibration to P(used) | Per-frame logistic regression makes frame scores comparable |
| P14 | Knapsack + MMR assembly | Maximise calibrated value under a token budget, with diversity and `requires` closure |

---

## 2. Where elfmem actually is (measured, this branch, today)

Everything in this section is read from code or queried from the live database, not estimated.

### 2.1 Corpus scale

```
active blocks     145        (knowledge 126, message 33, self 11, decision 8,
archived           42         attention 5, mind 3, task 1)
corpus tokens  ~33,574
edges              93
outcome records     6        <- lifetime, four months
amendments          0        <- lifetime
```

**The whole corpus fits in a single context window with room to spare.** This is the single
most important number in this document, and almost every sophisticated mechanism in the
proposal is designed for a corpus two to three orders of magnitude larger.

### 2.2 The substrate migration: built, verified, and stopped one step short

Waves 1-4 shipped. `export --to-markdown`, the block-file parser, `index check|rebuild|parity`,
file-native mutation primitives, and an `elfmem migrate` substrate step that runs backup ->
export -> rebuild -> parity -> marker file. All of it tested. **None of it is wired into the
live system.** Every runtime path - `learn()`, `edit()`, `forget()`, `ls()`, `recall()` - is
still DB-native. `src/elfmem/memory/file_mutation.py:9-12` says so outright: *"the live system
is still DB-primary until migration completes."*

The stop is at Phase 4, the retrieval-parity gate. From the real-data dry run
(`docs/plans/v2_substrate/plan/dry_run_2026-08-10.md:41-48`):

```
Baseline: active=140 inbox=3 archived=42 (185 total)
Exported: 185 blocks -> 11 files
Rebuilt:  143 blocks written
Block count: before=140 after=140  MATCH
Query parity: 5/5 DIVERGE
GATE PASSED: False
```

### 2.3 Why the gate cannot pass as currently constructed

Retrieval ranks on a five-term composite (`src/elfmem/scoring.py:113-152`):

```
score = w_sim*similarity + w_conf*confidence + w_rec*recency
      + w_cent*centrality + w_reinf*reinforcement + kappa*sqrt(variance)
```

Rebuild from markdown reconstructs `similarity` (re-embedded) and `confidence` (from
frontmatter, since ADR 0011). It **structurally zeroes the other three**:

| Term | Source | State after rebuild |
|---|---|---|
| `recency` = `exp(-decay_lambda * (now_ah - last_reinforced_at))` | `blocks.last_reinforced_at` | forced to `0.0` (`db/queries.py:91`) |
| | `blocks.decay_lambda` | forced to `0.01` STANDARD (`determine_decay_tier` never called on the rebuild path) |
| `reinforcement` = `log(1+n)/log(1+n_max)` | `blocks.reinforcement_count` | forced to `0` (`db/queries.py:89`) |
| `centrality` = weighted degree / max degree | `edges` table | **entire table lost** - no frontmatter encoding for an edge exists |

Plus one silent loss nobody has written down: `created_at` is written to the file as `created:`,
parsed into `Block.created`, and then **dropped on the floor** - `_write_block` never passes it
to `insert_block`, which hardcodes `_now_iso()`. Every rebuilt block is dated at rebuild time.

The gate demands top-5 identity on frames whose ranking depends on three terms the rebuild
destroys. It is not a tuning problem. **It cannot pass until those terms round-trip.**

### 2.4 The four other things worth knowing before evaluating the proposal

**(a) Retrieval already does more than the notes assume.** RRF fusion of BM25 and cosine
already exists (`retrieval.py:219-268`). MMR already exists at stage 5 with `lambda = 0.7`
(`retrieval.py:392-447`). One-hop graph expansion already exists. The proposal's "fuse three
cheap signals" is largely shipped.

**(b) The weak link is assembly, not retrieval.** `_render_with_budget`
(`context/rendering.py:41-58`) is a greedy prefix scan that **`break`s on first overflow**:

```python
for block in blocks:
    candidate = selected + [block]
    if _estimate_tokens(fn(candidate)) <= token_budget:
        selected = candidate
    else:
        break          # <- one oversized block truncates everything after it
```

One long block discards every smaller block behind it, however valuable. Token estimation is
`len(text) // 4`. This is where P14's knapsack framing lands squarely on a real defect.

**(c) Retrieval and usage are the same event, by fiat.** `recall()` unconditionally calls
`reinforce_blocks()` on everything it returns (`operations/recall.py:101-104`). A block that
was retrieved and ignored is indistinguishable from one that was retrieved and acted on. This
is precisely the conflation the proposal attacks - and elfmem's own data shows why the
proposal's fix (explicit `use` events) will not work unaided: the one explicit feedback verb
that exists, `outcome()`, has been called **six times in four months**.

**(d) There is no ledger, no audit trail, and no undo.** `MemorySystem._history` is an
in-memory `deque(maxlen=100)`, lost on exit. `reinforce_blocks` is destructive aggregation -
it increments a counter and overwrites a timestamp; no row records *when* or *why*. The v2
plan's answer is "git is the audit trail" - but `.gitignore:17` is a bare `.elfmem`, and
`elfmem init` creates neither `.elfmem/memory/`, nor `self.md`, nor `.elfmem/.gitignore`.
**Today, `forget()` destroys text with no recovery path whatsoever.**

---

## 3. Collisions: where the proposal meets measured rejections already recorded here

These are not objections of taste. Each is a controlled result already recorded in this repo.

### 3.1 P5 (ACT-R salience) collides with ADR 0001

ACT-R base-level activation is power-law decay. [ADR 0001](../decisions/0001-power-law-decay-rejected.md)
tested power-law against exponential across four scenarios and two seeds:

```
scenario          exponential   power-law
baseline             80.7%        75.3%   (-5.4pp)
uncertain_mix        78.4%        70.7%   (-7.6pp)

recent reach (plasticity):
baseline             78.4%        25.2%   (-53pp)
weekly_rescore       78.0%        13.6%   (-64pp)
```

Mechanism: at t=1000 active hours, exponential gives recency `4.5e-5`; power-law gives `0.41`.
A 10,000-fold difference at the long end. Year-old blocks become competitive with day-old
blocks and top-K fills with stale content.

**The sharper point**: elfmem *already stores ACT-R's sufficient statistics*.
`blocks.reinforcement_count` is `n`. `blocks.last_reinforced_at` is `L` (in session-aware
active hours, which is strictly better than wall time for this purpose). The proposal's
"one line of state, constant time" is already how elfmem works. The only delta P5 offers is
the functional form - and that form lost a controlled test in this codebase.

**Disposition: reject the formula, keep the state model (already present).** ADR 0001's
revisit trigger stands unchanged: a benchmark result showing power-law wins on agent workloads.

### 3.2 P13 (calibration) collides with the evidence supply, not with an ADR

The proposal's own recommended first experiment is *"the calibration loop on a single task
frame versus raw scores, because if that shows a measurable lift in used-block rate, the whole
frame architecture is justified."*

That experiment cannot be run. Fitting a logistic regression from frame score to P(used)
requires "used" labels. The live instance has produced **6 in four months**, all from
voluntary `outcome()` calls. [ADR 0006](../decisions/0006-defer-multi-parameter-self-tuning.md)
disqualified the closest architectural sibling (option B, "replace each threshold with a
learned weighted sum") on the grounds that it *"trades 1 parameter for 5 weights; same
attribution problem; loses grep-ability and reviewability of constants."*

But the honest reading is that P13 is **blocked on a prerequisite, not wrong**. The
prerequisite is P4, the ledger. Section 6.5 proposes a three-tier label scheme that produces
labels *without agent cooperation*, which is the actual fix.

**Disposition: defer behind an explicit accrual trigger. Build the prerequisite now.**

### 3.3 P7 (gist collapse) collides with ADR 0009 and with the corpus size

[ADR 0009](../decisions/0009-retire-decay-driven-archival.md) retired decay-driven archival on
this evidence: `superseded = 41, decayed = 0` (the earlier research doc's query counted 1).
Either way, the mechanism built to gracefully retire stale content retired at most one block in
four months, because the session-aware clock reads 3.69 total
active hours across 370 sessions and recency at STANDARD is still 0.96 after the instance's
entire lifetime.

P7 is the same trigger with a better payload: instead of archiving on low activation, rewrite
to gist on low activation. The trigger is the part that was measured inert. Layered on top:

- The corpus is 33.6k tokens. There is nothing to compress *for*.
- Each collapse is an LLM call producing an irreversible-in-the-index rewrite, on a system
  whose entire v2 programme exists because irreversible LLM rewrites destroyed the
  constitution.
- The stated recovery path ("git holds the original") does not exist yet; `.elfmem` is
  gitignored.

**Disposition: defer the mechanism, reserve the format slot now** (see 6.3). Format changes are
free before authority flips and expensive after; mechanisms are expensive whenever built.

### 3.4 The hardest objection: event log tables were already rejected, by name

This is the objection that most deserves an answer, and it is the one neither note anticipates.

[ADR 0003](../decisions/0003-defer-constitutional-evolution.md) records that the reviewing
Critic *"rejected ... FSRS-5 mechanics, **event log tables**, hierarchical tiers, and
Zettelkasten auto-linking - all on the same grounds: 'complexity without measured benefit.'"*
and then adds the sentence that should be read as aimed directly at documents like this one:

> **Re-proposing them under different vocabulary doesn't change the underlying judgment.**

So: is P4 an event log table under different vocabulary? Partly yes, and the burden is on this
document to show what changed.

**What changed is the premise, not the reasoning.** When the event log was rejected, the
database was authoritative. In that world a log is pure redundancy: `reinforcement_count` and
`last_reinforced_at` already live durably in `blocks`, the log would only re-derive what SQLite
already guarantees, and "complexity without measured benefit" is exactly right.

Under files-as-truth, that state has **nowhere else to live**. It is not redundant with the
files, because the files cannot express it; it is not redundant with the index, because the
index is by design disposable. And the cost of it having nowhere to live is not hypothetical -
it is `GATE PASSED: False`, 5/5 queries diverging, measured against a copy of the real
production corpus on 2026-08-10.

That is the same move the v2 research document already made for four other ADRs:

> Each ADR governing a removed subsystem (0001, 0003, 0006, 0007) was sound given its premises;
> what changed is the evidence, not the reasoning.

Two further disciplines borrowed from the same ADRs, so this does not become the thin end of a
wedge:

- **The ledger buys a measured benefit or it does not ship.** The benefit is falsifiable and
  the measurement already exists: re-run the Phase 4 dry run. If parity does not materially
  improve, the ledger has not earned its place and this document is wrong.
- **The ledger is evidence-gathering machinery, not adaptation machinery** - ADR 0006's own
  distinction. It records what happened. It does not tune anything. Everything in the proposal
  that *would* tune something (P13) stays deferred behind an accrual trigger.

---

---

## 4. Three gaps the proposal closes that elfmem has named and left unassigned

From `docs/plans/v2_substrate/plan/build-plan.md:344-351` ("Coverage table - genuine unassigned
gaps") and `model.md:261-283` ("Model drift log"):

| elfmem's own words | Proposal mechanism that answers it |
|---|---|
| *"Graph-edge reconstruction on rebuild - descoped; needs a U-001 frontmatter-format decision not yet made"* | **P10** - typed inline links are the frontmatter encoding for an edge |
| *"the block format (U-001) has no way to encode an edge in frontmatter"* (`model.md:279`) | **P10** |
| Reinforcement count and recency *"do NOT round-trip"* (`index_rebuild.py:20-25`) | **P4** - the ledger replays both exactly |
| *"stable identity across moves is genuinely unsolved"* (research doc §4.2), and content-hash-as-ID breaks on every edit | **P2 + P3** - permanent ID (built) plus simhash tombstone matching for orphan recovery (not built) |
| §5.4 Iteration 5: dropping embeddings *"regresses silently on vocabulary-mismatch queries"* | **P9** - a cue line is a lexical index of *retrieval situations*, which is what vocabulary mismatch actually breaks |
| ADR 0010's disclosed coverage gap: new content is not contradiction-checked at all until step 6b ships, and 6b is not built | **P10's `supersedes`** - the write carries the resolution, so most contradictions never arise |

The model drift log already flagged the pattern:

> The generative core treats the block format (U-001) as settled infrastructure that later
> units simply use. Three times now, a later unit has instead discovered a real constraint the
> format didn't anticipate. The format is not wrong, but it is **less finished than "generative
> core" implied**.

This document is the fourth instance, and it argues the format is not finished because it is
missing exactly the fields P4, P9, and P10 supply.

---

## 5. Disposition of all fourteen mechanisms

| # | Mechanism | Verdict | Rationale |
|---|---|---|---|
| P1 | Files are truth | **Already decided** | Same conclusion, 2026-08-08; Waves 1-4 built. Confirmation, not news. |
| P2 | Permanent ID + version hash | **Half built, finish it** | Permanent `id` shipped (`blockfile.py` invariant 3). Version hash specified in `model.md:42-44`, never built - rebuild re-embeds every block, every time. |
| P3 | Simhash fingerprint | **ADOPT** | Embedding-free near-dup; the only mechanism that recovers a block cut-and-pasted without its ID. Cheap. |
| P4 | Append-only ledger | **ADOPT - highest value** | Unblocks the parity gate; delivers the audit trail RC3 needs; makes `why` free; degrades gracefully with zero agent cooperation. |
| P5 | ACT-R salience | **REJECT (formula)** | ADR 0001, measured. State model already present. |
| P6 | Confidence half-life by class | **MEASURE** | elfmem has volatility classes (`DecayTier`) but applies them to *use*-decay only. Separating *truth*-decay is a real distinction, cheaply tested. |
| P7 | Gist collapse | **DEFER (trigger)** | ADR 0009 trigger was inert; corpus is 33.6k tokens; recovery path unbuilt. Reserve the format slot. |
| P8 | Surprise gate | **MEASURE (cheap form)** | SAGE evidence already in elfmem's research doc §4.4 supports it. The LLM-judged form costs a call per write, defeating the purpose. Use the cue-collision test (6.4) instead. |
| P9 | Mandatory cue line | **ADOPT - best cost/benefit** | Zero marginal LLM cost (written in the same breath as the block). Attacks vocabulary mismatch, the one failure grep cannot cover. Enables 6.4. |
| P10 | Typed links | **ADOPT** | Closes the named unassigned gap. Six-word vocabulary; elfmem already has four of the six as edge relation types. |
| P11 | PPR / spreading activation | **MEASURE** | Static centrality ranks hubs high regardless of query; PPR is query-conditional. ~20 lines at 145 blocks / 93 edges. Ship only if it wins on the harness. |
| P12 | Pluggable text-defined frames | **DEFER (trigger)** | Zero demand for a fifth frame. Note the `frames` DB table exists, is seeded, is stale, and **is read by nothing** - dead schema. Restructure frames declaratively; do not ship a plugin loader. |
| P13 | Calibration to P(used) | **DEFER (accrual trigger)** | 6 labels in 4 months. Blocked on P4 + 6.5, not wrong. |
| P14 | Knapsack + MMR assembly | **ADOPT (assembly half)** | MMR already exists at retrieval. The assembly half lands on a live defect: `break`-on-first-overflow. Greedy value/token, ~30 lines, deterministic, no learning. |

Five adopt, three measure, three defer, one reject, two already-done.

---

## 6. The synthesis design

### 6.1 One invariant that resolves most of the ambiguity

> **Declared state lives in files. Derived state lives in the ledger and the index.
> Nothing derived is ever hand-edited, and nothing declared is ever computed.**

This is not currently true. `export --to-markdown` writes `confidence`, `alpha`, and `beta`
into every block's frontmatter (`migration/export.py:57-61`) because that was the only way to
make them survive rebuild (the ADR 0011 fix). Under a ledger they survive by replay instead,
which is strictly better - you get the full audit, not just the aggregate.

**Consequence: block format v2 has fewer fields than v1, not more.** Three derived fields
leave; `cue`, `cls`, and typed links arrive. This is worth stating plainly because "adopt a
richer proposal" usually means "adopt a heavier format", and here it does not.

| | Declared (in the file) | Derived (ledger + index) |
|---|---|---|
| | `id`, `cls`, `tags`, `pinned`, `cue::`, typed links, body | `confidence`, alpha, beta, `reinforcement_count`, `last_reinforced_at`, `decay_lambda`, centrality, embedding, `created_at`, simhash, edge weights |

Note that simhash is **derived**, and therefore does *not* go in frontmatter - correcting the
proposal, which puts a fingerprint in the block. A fingerprint stored beside hand-editable text
goes stale the moment someone edits the text. Recompute it at index time (it is cheap), and
store the fingerprint of *removed* blocks in the ledger's tombstone events, which is the only
place it is genuinely needed (orphan recovery).

### 6.2 The ledger

```
.elfmem/ledger/2026-08.jsonl          append-only, git-committed, one JSON object per line
.elfmem/ledger/.checkpoint.json       regenerable aggregate snapshot, gitignored
```

Event kinds, all short:

```jsonc
{"t":"2026-08-24T09:14:22.031Z","s":7,"ah":3.71,"k":"birth","id":"8f3a2b1c","f":"notes/self.md","fp":"3a7e91c4","cls":"identity"}
{"t":"...","s":8,"ah":3.71,"k":"edit","id":"8f3a2b1c","vh":"9c11ab40"}
{"t":"...","s":9,"ah":3.72,"k":"remove","id":"8f3a2b1c","fp":"3a7e91c4","why":"superseded","by":"b7e2c4d1"}
{"t":"...","s":10,"ah":3.80,"k":"asm","frame":"attention","q":"a91f","ids":["8f3a2b1c","b7e2c4d1"],"sid":"s-042"}
{"t":"...","s":11,"ah":3.81,"k":"use","ids":["b7e2c4d1"],"sid":"s-042","src":"host"}
{"t":"...","s":12,"ah":3.82,"k":"out","id":"b7e2c4d1","sig":1.0,"w":1.0,"src":"agent"}
{"t":"...","s":13,"ah":3.90,"k":"link","from":"8f3a2b1c","to":"b7e2c4d1","rel":"refines","o":"declared"}
```

Four design decisions that make this robust rather than merely appealing:

1. **`ah` (cumulative active hours) is recorded on every event.** Without it, replay cannot
   reconstruct `last_reinforced_at`, which is measured in session-aware active hours, not wall
   time (`db/models.py:40`). Wall-clock timestamps alone would silently break the decay clock
   on replay. This is the non-obvious requirement and it is easy to miss.

2. **Every line stays under 4096 bytes.** POSIX guarantees atomic `O_APPEND` writes below
   `PIPE_BUF`, which makes concurrent multi-process appends safe with no locking. Enforce it
   by never inlining content - IDs only - and by chunking any `ids` array over ~100 entries
   across multiple lines. This is what makes S18 (two agents writing at once) a non-event, and
   it is the same property that makes the ledger merge cleanly in git.

3. **One `asm` line per frame call, not per block.** An autonomous loop calling `frame()` every
   turn would otherwise write one row per block per turn. Frame-granularity keeps the ledger
   proportional to *calls*, not to *calls x top_k*.

4. **Log plus snapshot, not log alone.** The proposal says compute every score from the ledger
   and never store it. That is `O(history)` per query. The synthesis keeps the ledger as truth,
   the index as the materialised view, and adds a checkpoint so `index rebuild` is
   `O(checkpoint + tail)` rather than `O(all history)`. The checkpoint is regenerable and
   therefore gitignored.

**Replay derives**: `created_at` (from `birth.t`), `reinforcement_count`, `last_reinforced_at`,
alpha/beta, learned edge weights (co-occurrence over `asm` events), and the full provenance
chain behind `why`. **Declared edges come from files**, learned weights from the ledger - a
clean split that keeps hand-authored structure hand-authored.

**Failure handling**: a malformed line is skipped, counted, and reported by `index check`. It
never aborts a rebuild. Fail-soft is correct here specifically because the ledger is an input
to derived state, not truth about content - the opposite of the fail-fast rule that governs
block parsing.

### 6.3 Block format v2

```markdown
## Minimum force
<!-- id: 8f3a2b1c9d0e4f17  cls: identity  tags: [self/value, self/constitutional]  pinned: true -->
cue:: deciding whether to add a new command or extend an existing one
refines:: [[coding-principles#^b7e2c4d1]]

Apply the minimum force that solves the problem. Complexity is debt.
Before adding a new top-level command, apply the test: does this extend an
existing verb? If yes, extend it.
```

- `cls` is the volatility class driving *truth*-decay (P6): `identity` / `project` / `status`.
  Distinct from `DecayTier`, which drives *use*-decay. A block can be highly salient and
  low-confidence at once - that is the "important, verify first" case, and it should be
  surfaced with a flag rather than silently down-ranked.
- `cue::` is mandatory for new blocks, lint-checked, and indexed in its own FTS column with its
  own weight. Cap its length so BM25 length normalisation cannot be gamed by verbosity.
- Typed links use the existing Dataview inline convention so Obsidian renders them without
  plugins. Closed vocabulary of six; `index check` rejects unknown types (fail-fast). Dangling
  targets are a warning, not an error - link-before-write is normal in a vault.
- **Reserved but unimplemented**: `collapsed_from::` and a `state:: collapsed` value, so P7's
  format exists before P7's mechanism is earned.

Two format bugs must be fixed at the same time, because they undercut the "just edit the file"
promise:

- **The `##` boundary heuristic silently swallows hand-appended blocks.** A `##` line only
  starts a new block if the preceding block has no frontmatter, or the candidate is itself
  followed by frontmatter (`blockfile.py:159-242`). Hand-append `## New note` after an exported
  block and it disappears into the previous block's body. Fix: treat a `##` at column 0
  following a blank line as a boundary and mint an ID for it, rather than absorbing it.
- **`pinned:` is inert.** No DB column, no reader anywhere in `src/`. Invariant 5 ("a pinned
  block is never proposed for removal and is always included in its frame") is unimplemented.
  Fix: real column, enforced at the assembly tier (6.6) and in every removal path.

### 6.4 The cue-collision test - this document's own contribution

Neither note proposes this. It falls out of P9 and it is the piece that turns a nice-to-have
field into a mechanism.

The proposal frames the write gate as **surprise**: "would this memory have changed the last
response?" That question needs an LLM, which costs a call per write and defeats the purpose of
gating writes to save calls.

Reframe it. The novelty that matters for a *memory* system is not "is this content new" but
**"is this retrieval situation new"** - and the cue line states the retrieval situation
explicitly, in lexical form, for free. So compare cues, not content:

| cue similar? | content similar? | Action | Cost |
|---|---|---|---|
| no | no | **WRITE** - genuinely novel | 0 LLM |
| no | yes | **WRITE + `derived-from::`** - same fact, new situation; legitimately additive | 0 LLM |
| yes | yes | **EDIT the existing block** - true duplicate | 0 LLM |
| yes | no | **COLLISION** - same situation, different answer. Propose `supersedes::` or `contradicts::`. Never auto-write. | 0 LLM to detect |

Two consequences worth being explicit about:

**(a) It converts a guard into a structure.** v2 step 1 shipped the constitutional pin guard
(`operations/consolidate.py:318-329`, `blocked_supersessions`), so `self/constitutional` blocks
are no longer silently overwritten - the specific 2026 data loss is stopped. But the guard is a
tag-scoped exception around a mechanism that is still, for every *other* block, a silent
irreversible overwrite at 0.90 cosine with no audit row. Under the cue-collision test, high
*content* similarity with *dissimilar cues* means "keep both" (row 2), and the only
destructive-looking case is the cue collision, which is routed to a proposal and never applied
automatically. The destructive default stops needing a guard, because it stops being the
default. Any block can then be pinned or not without that being what stands between it and
deletion.

**(b) It partially closes ADR 0010's disclosed coverage gap at zero LLM cost.** ADR 0010
retired pairwise LLM contradiction detection (14 findings, 12 unresolved, two-thirds of all LLM
calls) and accepted that new content would not be contradiction-checked until corpus-level
review (step 6b) ships. Row 4 is a contradiction detector that costs two lexical comparisons.
It will not find every contradiction - only the ones that share a retrieval situation - but
those are exactly the ones that matter, because they are the ones that will be retrieved
together.

**Test it retrodictively before shipping it.** See E3 in §9.

### 6.5 Usage labels without agent cooperation

The design constraint elfmem's data proves and the proposal misses: **the architecture must
degrade gracefully to zero usage signal.** Six voluntary outcome calls in four months is the
realistic case, not the pathological one.

Three tiers of label, in descending volume and ascending quality:

| Tier | Event | Who supplies it | Expected volume |
|---|---|---|---|
| **assembled** | `asm` | the system, automatically | every frame call - free |
| **inferred** | `use` with `src:"overlap"` | lexical overlap between the host's output and the block text | frequent, noisy, opt-in |
| **declared** | `use` / `out` with `src:"agent"` | explicit `outcome()` or a `use(ids)` verb | rare, clean |

The inferred tier follows a precedent that already exists in this codebase:
`dream(host_analyses=...)` lets a host agent session supply its own analysis instead of a
configured LLM adapter. The same seam supplies usage.

Critically: **reinforcement should move off `asm` and onto `use`-if-present-else-`asm`**. Today
`recall()` reinforces everything it returns, which is why `reinforcement_count` measures how
often a block *won a ranking* rather than how often it *helped*. That is a feedback loop that
rewards its own past decisions. With a ledger, the fix is a replay rule, not a schema change.

If the inferred and declared tiers never materialise, everything still works - the system
behaves exactly as it does today, on `asm` alone. That is the graceful-degradation property
P13 needs and does not state.

### 6.6 Assembly

Replace `_render_with_budget`'s break-on-first-overflow with a three-tier greedy fill:

1. **Pinned floor** - constitutional and `pinned: true` blocks are allocated first, up to a
   declared floor. Never subject to diversity pruning or budget eviction.
2. **Hot** - top blocks by salience regardless of query (this is where P5's *intent* is
   honoured, using elfmem's existing exponential recency rather than ACT-R's power law).
3. **Retrieved** - query-ranked candidates, filled greedily by **value per token** rather than
   by rank order. This is the fractional-knapsack relaxation, ~30 lines, deterministic, and it
   needs no learned weights - `value` is the existing composite score.

Then a **closure pass**: any selected block with a `requires::` edge pulls its dependency in,
charges its tokens, and re-runs the fill. Bound to two iterations, then hard-truncate and
report the truncation in the result object rather than silently.

Two guards on determinism, because the parity gate depends on it: break greedy ties by block
ID, and replace `len(text) // 4` with the real tokenizer already available via `TokenCounter`.

MMR stays where it is (retrieval stage 5, `lambda = 0.7` - which is close enough to the
canonical Carbonell and Goldstein default to be defensible from literature rather than from a
fit).

### 6.7 Where reasoning lives - unchanged

The 2026-08-08 research doc's §5 conclusion stands and this proposal does not disturb it:
`elf-review` and `elf-recall` borrow the host agent's model; the index-backed `frame()`/
`recall()` path stays reasoning-free for the autonomous loop; the LLM gateway shrinks to
headless operation only.

Note the pleasant consequence: **every mechanism adopted in §5 is zero-LLM.** The ledger,
cue-collision test, typed links, simhash, and assembly are all arithmetic and string
comparison. The only LLM cost in the whole synthesis is a one-time backfill of cue lines over
145 existing blocks, and the host agent can do that in a single session without an API key.

---

## 7. Simulation

Method follows this repo's own convention (`docs/design_simulation_guide.md`): a frozen
scenario set, fitness dimensions ranked in advance, iterate until the last iterations only
confirm boundaries rather than finding improvements.

### 7.1 Fitness dimensions

Inherited from the 2026-08-08 research doc, plus one earned by this evidence base:

1. **Control** - can the operator deterministically change or remove any memory?
2. **Integrity** - can memory be silently destroyed?
3. **Cost** - LLM calls to maintain steady state
4. **Simplicity**
5. **Scale headroom**
6. **Signal independence** *(new)* - does the design degrade gracefully when usage labels
   never arrive? Earned by: 6 outcome records and 0 amendments in four months.

Dimension 6 is the one that disqualifies most of the proposal's cleverness and it is not
optional. A design that only works when the agent volunteers feedback is a design that does
not work here.

### 7.2 Frozen scenario set

S1-S11 are carried forward unchanged from the 2026-08-08 document so results stay comparable.
S12-S18 are new and specific to this proposal.

| ID | Scenario |
|---|---|
| S1 | Reword one constitutional principle |
| S2 | Delete 3 unwanted seeded blocks |
| S3 | New block lands at 0.91 cosine to a pinned constitutional block |
| S4 | 10 blocks learned in a batch; steady-state maintenance cost |
| S5 | Fresh install, new machine, local model only |
| S6 | API key absent; MCP server starts anyway |
| S7 | Corpus grows 145 -> 14,000 blocks |
| S8 | Two agents write concurrently |
| S9 | Audit "what changed in memory last month" |
| S10 | Does the constitution survive 6 months? |
| S11 | `elf-recall` (unranked grep) and `frame()` (ranked index) disagree |
| **S12** | **Rebuild the index from files and pass the Phase 4 parity gate** |
| **S13** | Vocabulary-mismatch query ("swallow exceptions silently" vs a block phrased "error handling") |
| **S14** | Cold start: a folder of hand-written markdown, no ledger, no IDs |
| **S15** | The agent never reports usage - only `asm` events ever accrue |
| **S16** | A non-constitutional block lands at 0.91 cosine to an existing one |
| **S17** | A ledger line is truncated by a crash mid-append |
| **S18** | Two processes append to the ledger in the same millisecond |

### 7.3 Iterations

| Iter | Design under test | Outcome | Verdict |
|---|---|---|---|
| **1** | Baseline: `elfmem_index` @ `4de47cd` as built | S12 fails structurally (three ranking terms zeroed). S9 fails (`.gitignore:17` is a bare `.elfmem`; `init` creates no memory dir; `forget()` has no recovery path). S13 depends entirely on embeddings. S16 fails (silent 0.90-cosine supersession, no audit row). S14 fails (`created_at` silently reset; `##`-boundary heuristic swallows hand-appended blocks). | baseline |
| **2** | + Phase 0 hygiene: round-trip `created_at`; derive `decay_lambda` on rebuild; make `pinned` a real column with real enforcement; align `index check`/`rebuild` on `archive/`; un-gitignore `.elfmem/memory` + ledger; `init` writes the memory dir, `self.md`, and `.elfmem/.gitignore` | S9 -> partial (git history now exists). S14 -> partial. S12 unchanged - **recency, reinforcement, and centrality are still zeroed**. | kept, insufficient |
| **3** | + **P4 ledger with checkpoint** (6.2) | S12 -> the three zeroed terms replay exactly, including the session-aware `ah` clock. S9 -> full (`git log -p` for text, `ledger/2026-07.jsonl` for behaviour). S15 -> holds: `asm` events are recorded by the system with no agent cooperation, so the system behaves exactly as today in the worst case. S17/S18 -> handled by design (skip-and-count on malformed lines; sub-`PIPE_BUF` atomic appends). Cost: one small append per write and per frame call. Simplicity: +1 file format, -3 frontmatter fields. | **kept - the load-bearing iteration** |
| **4** | + **Format v2**: `cue::`, typed links, `cls`, derived fields removed (6.3) | S13 -> fixed without embeddings; the cue states the retrieval situation lexically. S16 -> fixed structurally by the cue-collision test (6.4). S3 -> now over-determined (pin guard *and* cue-collision *and* ledger audit). S7 -> improves; simhash clustering makes corpus review chunkable. New cost: a lint surface, and a one-time cue backfill over 145 blocks. | kept |
| **5** | + **Assembly**: three-tier greedy value/token fill replacing break-on-first-overflow, plus `requires::` closure and a real tokenizer (6.6) | Fixes a live defect: today one oversized block discards every smaller block behind it. S7 improves. Determinism preserved by ID tie-breaking, which S12 depends on. | kept |
| **6** | Test **P5 (ACT-R salience)** in place of exponential recency | Plasticity regresses per ADR 0001 (-53pp recent reach on the baseline scenario). S15 unaffected. No dimension improves. | **rejected - confirms boundary** |
| **7** | Test **P13 (calibration)** now | Untrainable: 6 labels in 4 months. Under S15 the regression has no data at all and silently degenerates to a constant, which is worse than the current explicit weights because it *looks* adaptive. Fails dimension 6. | **rejected as premature; trigger-gated** |
| **8** | Test **P7 (gist collapse)** now | At 33.6k corpus tokens the trigger never fires (ADR 0009's measured inertness). Adds LLM cost and an irreversible rewrite to a system whose v2 programme exists because of irreversible rewrites. Fails dimensions 2 and 3 for zero gain on 5. | **rejected now; format slot reserved** |
| **9** | Test **P12 (frame plugin loader)** now | No demand. Note the `frames` DB table already exists, is seeded, is stale relative to the Python registry, and is **read by nothing** - a prior attempt at exactly this, now dead schema. Shipping a loader would add a second dead surface. | **rejected; delete the dead table instead** |

**Winner: Iteration 5.** **Stopping reason**: iterations 6-9 each confirmed a boundary rather
than finding an improvement, which is this repo's own convergence criterion.

### 7.4 Two scenarios worth walking step by step

**S16 - a non-constitutional block lands at 0.91 cosine (the common case of the failure that
motivated v2):**

- *Today*: `_collect_decisions` sees `best_sim >= 0.90`, the pin guard does not fire because the
  target is not `self/constitutional`, and the existing block is archived with
  `archive_reason='superseded'`. No audit row records what the old content said. 41 blocks have
  taken this path; 6 of them were constitutional before the guard shipped.
- *Iteration 5*: the cue-collision test asks first whether the two blocks answer the same
  retrieval situation. If the cues differ, both are kept and a `derived-from::` edge is written -
  the same fact usefully indexed under two situations. If the cues collide, the write is
  suspended and a `supersedes::` proposal is surfaced. Either way a `birth`/`link`/`remove`
  event lands in the ledger, and the old text is one `git show` away.

**S15 - the agent never reports usage (the realistic case):**

- Every `frame()` call writes one `asm` line. `reinforcement_count` and `last_reinforced_at`
  replay from those, exactly as they are maintained today.
- The `use` and `out` tiers stay empty. P13 stays deferred. Nothing else notices.
- The system's behaviour is **identical to today's**, with the addition of a replayable history.
  That is the property that makes the ledger safe to adopt before the labels exist: it is not a
  bet on the labels arriving.

---

## 8. Edge cases and mitigations

Each row is a failure the synthesis must survive, not a hypothetical.

| # | Edge case | Mitigation |
|---|---|---|
| E1 | Ledger grows unboundedly under an autonomous every-turn loop | One `asm` line per frame call, not per block; monthly file rotation; checkpoint compaction so rebuild is `O(checkpoint + tail)` |
| E2 | Two processes append simultaneously | Every line under 4096 bytes so `O_APPEND` is atomic under POSIX; enforced by never inlining content and chunking long `ids` arrays |
| E3 | A crash truncates a line mid-write | One JSON object per line; malformed lines are skipped, counted, and reported by `index check`; a rebuild never aborts on ledger damage. Fail-soft here, unlike block parsing, because the ledger feeds derived state rather than asserting content |
| E4 | Replay is non-deterministic, so the parity gate flickers | Sort by `(t, s, id)` with a per-process sequence counter `s`; break greedy-assembly ties by block ID |
| E5 | Ledger references a block that no longer exists in files | Dropped at rebuild, counted, surfaced. Normal after a hand-delete |
| E6 | Wall-clock timestamps cannot reconstruct the session-aware decay clock | Record cumulative active hours `ah` on **every** event. This is the non-obvious requirement; omitting it silently breaks recency on replay |
| E7 | Clock skew when merging peer histories | Do not merge ledgers across peers. Peer merge stays on the existing arithmetic alpha/beta path (ADR 0002) |
| E8 | Agents write lazy cues ("cue:: when relevant") | `index check` lints: minimum length, and reject a cue that is a substring of the block's first line. `elf-review` re-checks cue quality as part of its taxonomy pass |
| E9 | Verbose cues game BM25 length normalisation | Index cues in a separate FTS column with its own weight and a hard length cap |
| E10 | 145 existing blocks have no cue | One-time backfill by the host agent in a single session. No API key needed, no adapter involved |
| E11 | Simhash is unreliable on short text | Only trust it above a minimum token count; below it, fall back to exact content hash. Block median is ~230 tokens, so this bites only on log entries |
| E12 | The simhash Hamming threshold is a new magic number (violates axiom 1) | **Use simhash only as a recall-oriented prefilter, never as the decider** - cosine or exact-hash confirms. A prefilter threshold then trades compute, not correctness, so it is not load-bearing |
| E13 | Agents invent link types outside the vocabulary | Closed vocabulary of six; `index check` fails fast on unknown types |
| E14 | Dangling link targets | Warning, not error. Link-before-write is normal in a vault |
| E15 | `supersedes` chains break because `archive/` is never re-read on rebuild | Make `rebuild` read `archive/` as `status='archived'`. This also removes the existing `check`/`rebuild` scope asymmetry |
| E16 | MMR or budget eviction drops a block that a `requires::` chain needs | Closure pass after selection; bounded to two iterations; then hard-truncate and **report** the truncation in the result object rather than silently |
| E17 | Pinned blocks evicted by budget pressure | Pinned floor allocated first, exempt from diversity pruning. Requires making `pinned` real - it is currently inert |
| E18 | `similarity` changes meaning depending on whether `rank_bm25` is installed | Pre-existing defect (`retrieval.py:219-268` overwrites cosine with normalised RRF for all seeds when BM25 fires). Make BM25 a hard dependency or a declared config choice, not a silent soft import - especially once cue lines make lexical signal load-bearing |
| E19 | Format v2 invalidates the markdown already exported | Files are not authoritative yet, so this is a re-export, not a migration. **Format changes are free before the authority flip and expensive after** - which is the argument for doing this now rather than after U-006 |
| E20 | Ledger writes add I/O to the recall hot path | One short line-buffered append per call. At 36-second average sessions this is not measurable. Do not batch: buffering trades a real durability property for an unmeasurable saving |

---

## 9. Evaluation plan

The proposal's stated first experiment - calibration on one frame - cannot be run, because it
needs labels that do not exist. Replace it with a ladder in dependency order. Each rung is a
gate: if it fails, the rungs above it do not get built.

The harness already exists: `scripts/longitudinal_sim/` (in-memory only, safety-asserted
against touching a real DB), plus LoCoMo and MemoryAgentBench adapters under `benchmarks/`.

| ID | Experiment | Metric | Decision it settles |
|---|---|---|---|
| **E0** | Re-run the substrate dry run after Phase 0 hygiene + the ledger, against a read-only copy of the production DB | Rank-Biased Overlap (see 9.1) vs the current `5/5 DIVERGE` | **Does the ledger earn its place?** If parity does not materially improve, this document is wrong and the ledger should not ship |
| **E1** | Cue ablation: backfill cues on 145 blocks, then run a vocabulary-mismatch query set with embeddings **off** | recall@5 with cue vs without | Whether cue lines close the gap embeddings currently cover - which settles open decision 6 ("should embeddings default on?") with data instead of judgement |
| **E2** | Assembly ablation: break-on-overflow vs three-tier greedy value/token | tokens used / budget; count of blocks dropped that would have fitted | Whether 6.6 is worth 30 lines |
| **E3** | **Retrodiction**: replay the 41 historical supersessions through the cue-collision test (needs E1's cues first) | How many of the 41 would have been kept-both; how many of the 6 constitutional losses would have been prevented | Whether 6.4 is a real mechanism or a plausible story. **This is the strongest available test because it runs against the actual damage** |
| **E4** | Personalised PageRank vs static centrality | LoCoMo / MemoryAgentBench, plus the longitudinal harness | Whether P11 ships |
| **E5** | Confidence half-life by volatility class, separated from use-decay | Longitudinal harness, `uncertain_mix` and `long_horizon` scenarios | Whether P6 ships |
| **E6** | *Gated*: frame calibration to P(used) | used-block rate vs raw score, per frame | Whether P13 ships. **Cannot start** until the accrual trigger in §10 is met |

### 9.1 The parity gate needs redefining, not just unblocking

The current gate is exact top-5 ID-set equality across four queryless frame calls. Two problems:
it is stricter than "retrieval is equivalent", and four queryless calls is a thin sample.

Proposed replacement, three parts:

- **Gate A (hard, unchanged in spirit)**: block count matches; every pinned and constitutional
  block is present in both; zero parse errors.
- **Gate B (ranking)**: **Rank-Biased Overlap** (Webber, Moffat and Zobel, 2010) at `p = 0.9`,
  averaged `>= 0.90` over the query set, **and** top-5 set overlap `>= 4/5` on every individual
  query. RBO is the published metric for comparing top-weighted indefinite rankings; `p = 0.9`
  is the paper's own conventional setting, so this is defensible from literature rather than
  fitted - which matters under axiom 1.
- **Gate C (regression)**: no statistically significant drop on the existing benchmark adapters.

Expand the query set from 4 queryless frame calls to roughly 20, including real queries drawn
from the ledger's own `asm` history once it exists - which is a pleasant second-order benefit:
**the ledger supplies the gate's own test set.**

---

## 10. Sequencing

Ordered by dependency, and by the principle that each phase must be independently valuable so
the programme can stop at any phase boundary without leaving a half-built thing.

| Phase | Ship | Closes | Risk | Gate to proceed |
|---|---|---|---|---|
| **0** | Hygiene: round-trip `created_at`; derive `decay_lambda` on rebuild; `pinned` becomes a real column with real enforcement; `check`/`rebuild` agree on `archive/`; fix the `##`-boundary swallow; un-gitignore `.elfmem/memory` + ledger; `init` writes memory dir + `self.md` + `.elfmem/.gitignore` | Five undocumented losses; the "git is the audit trail" claim that currently has no implementation | trivial | none - do this regardless of everything else in this document |
| **1** | **Ledger + checkpoint** (6.2); `index rebuild` replays it; `why(block_id)` verb | S9, S12; ADR 0003's "measured benefit" burden | low | **E0.** If parity does not materially improve, stop here |
| **2** | **Format v2** (6.3): `cue::`, typed links, `cls`; derived fields removed from frontmatter; `index check` lints; one-time cue backfill | S13, the named unassigned edge-representation gap, ADR 0010's coverage gap | medium | **E1** |
| **3** | **Cue-collision test** (6.4) at the write gate | S16; the destructive default | medium | **E3** - retrodict against the 41 historical supersessions before enabling |
| **4** | Redefined parity gate (9.1); re-run; if it passes, **U-006 flip authority** | the actual v2 cutover | high | Gates A + B + C |
| **5** | **Assembly** (6.6): three-tier greedy value/token, `requires::` closure, real tokenizer | the break-on-first-overflow defect | low | **E2** |
| **6** | Measure tier: P11 (PPR), P6 (confidence half-life) | - | low | **E4**, **E5**; ship only what wins |
| **7** | Deferred, trigger-gated: P7 gist collapse, P13 calibration, P12 frame plugins | - | - | see triggers below |

**Phases 0 and 1 are worth doing whether or not anything else in this document is accepted.**
Phase 0 fixes five real losses. Phase 1 is the only thing that makes the migration completable.

### 10.1 Pre-committed reopen triggers

Following the discipline of ADRs 0006, 0007, and 0009 - every deferral states what evidence
would reopen it, and what the smallest correct fix would then be.

| Deferred | Trigger to revisit | Smallest correct fix when triggered |
|---|---|---|
| **P5 ACT-R salience** | Unchanged from ADR 0001: a benchmark result showing power-law wins on agent workloads with statistical significance | Nothing smaller than a new ADR; ADR 0001 rejected even the opt-in flag |
| **P13 calibration** | `>= 200` non-`asm` usage labels accrued in the ledger over `>= 3` consecutive months on a real instance | Calibrate **one** frame (the proposal's own experiment), measure used-block rate, and stop there |
| **P7 gist collapse** | Corpus exceeds ~150k tokens, **or** corpus-level review no longer fits one context window. Plus ADR 0009's standing trigger: real evidence `review_corpus()` + human review is insufficient | Collapse the single largest low-activation cluster, by hand, once, and measure whether anything breaks before automating |
| **P12 frame plugins** | A second concrete consumer needs a frame that the four built-ins cannot express | Add the fifth frame to the Python registry. A loader only when there are two such consumers |
| **P8 LLM surprise gate** | The cue-collision test (6.4) demonstrably under-blocks: measured write volume of blocks that are never subsequently assembled | Tighten the cue-similarity threshold before adding an LLM call |

---

## 11. Risks and honest caveats

| Risk | Assessment |
|---|---|
| **This document over-fits one instance** | Inherited from the 2026-08-08 research doc and not resolved. Every number is from the maintainer's own dev DB: 145 blocks, 370 sessions averaging 36 seconds, 3.69 total active hours. The "labels never arrive" finding is the most instance-dependent claim here and it is load-bearing for rejecting P13. Its precondition - pulling the same breakdown from the Alv and Mira instances - was consciously waived once already (`model.md:17-23`). It is cheaper to satisfy now than it was then, and it should be satisfied before Phase 3 |
| **The ledger is a new failure surface** | Mitigated by E2/E3/E4 above, but it is genuinely new code on the write path of every operation. The mitigation for the mitigation: Phase 1 gates on E0, so it ships only if it demonstrably buys the parity result |
| **The cue-collision test is unvalidated** | It is this document's own invention. It has no external citation and no measurement. E3 exists precisely because it should not ship on elegance. If E3 shows it would not have prevented the historical losses, drop 6.4 and keep the cue line for retrieval only - the cue line stands on E1 alone |
| **ADR 0003's judgement may simply still apply** | §3.4 argues the premise changed. A reader who thinks the premise did not change should reject Phase 1, and the honest test is E0, not argument |
| **Convergence between the notes and elfmem's own conclusion could be confirmation bias** | The 2026-08-08 document flagged this about its own external sources. It applies again, more sharply: two independent research passes agreeing on files-as-truth is weaker evidence than it feels, because both were reasoning from similar priors about markdown and git |
| **Format v2 is a breaking change to an unreleased format** | Low cost now, high cost after U-006. This asymmetry is the main argument for sequencing Phase 2 before Phase 4, and it will not be available again |
| **The proposal's scale assumptions may become right** | Most of what is deferred here is deferred because the corpus is 33.6k tokens. If elfmem grows two orders of magnitude, P7, P11, P12, and P13 all become live again. The triggers in 10.1 are written so that growth reopens them automatically rather than requiring someone to notice |

---

## 12. Open decisions - Ben's, not this document's

1. **Does Phase 0 ship immediately, independent of everything else?** Recommendation: yes. It is
   five small fixes to losses nobody has written down, and one of them (`forget()` having no
   recovery path because `.elfmem` is gitignored) is live data-loss exposure of exactly the kind
   step 1 was shipped to stop.
2. **Is the ledger accepted as a premise change rather than a re-proposal?** (§3.4.) This is the
   single decision the rest of the document hangs from. If no, Phases 1-4 do not happen and the
   substrate migration needs a different answer to the parity problem - or an explicit decision
   to abandon the flip and keep the DB authoritative.
3. **Does the parity gate get redefined (RBO) or stay exact-match?** Redefining a gate you are
   failing deserves scrutiny. The counter-argument: the current gate demands bit-identical
   ranking from a rebuild, which is a stronger claim than "retrieval is equivalent", and no
   system in the cited literature holds itself to it.
4. **Do we satisfy the Alv/Mira precondition before Phase 3**, having waived it once already?
   Recommendation: yes, and it is cheap - one query per instance.
5. **Cue backfill: host agent, or leave old blocks cue-less?** Backfilling 145 blocks is one
   session. Leaving them cue-less means E1 measures a mixed corpus and E3 cannot run at all.
6. **Delete the dead `frames` table?** It exists, is seeded, is stale relative to the Python
   registry, and is read by nothing. It is a prior attempt at P12. Recommendation: delete it, so
   that if P12 is ever earned it starts from a clean decision rather than from stale rows.
7. **Does this document's disposition of P5 close ADR 0001 for good, or does ADR 0002's
   four-part gate change anything?** Worth noting: that gate required "v0.17 in production for
   >= 3 months", which ROADMAP.md dates as "not before ~2026-08-24" - i.e. it matures now. The
   gate's other three parts (Dmitry's DB, N>=5 seeds, real-instance evidence) are not met, so
   nothing here reopens, but somebody should say so deliberately rather than by omission.

---

## 13. What this means for the two research notes

Stated plainly, because the notes deserve a direct answer:

- **The substrate half is right and elfmem already agrees.** Files as truth, index as a derived
  view, block-level units with permanent IDs. Independently re-derived, externally corroborated,
  four of six waves built. No action needed beyond finishing.
- **The ledger is the best idea in either note**, and neither note identifies why: it is the
  thing that makes a derived index actually derivable. elfmem discovered that the hard way, in a
  dry run, on real data.
- **The cue line is the best cost/benefit idea**, and it does more than the notes claim - it
  reframes the write gate from a semantic question (needs an LLM) to a lexical one (free).
- **The scoring half is the weak half.** ACT-R, calibration, and gist collapse are each
  well-argued and each collide with a controlled result already recorded here. The common root
  is that all three assume a corpus and a usage signal that this deployment does not have and
  may never have.
- **The frame-plugin half is unearned.** Not wrong, just answering a question nobody has asked
  yet, with a dead table in this codebase already marking the last time it was asked.

---

## References

**Internal**
- [`docs/research/v2_substrate_and_reasoning_ownership_research.md`](v2_substrate_and_reasoning_ownership_research.md) - substrate + reasoning-ownership research, 2026-08-08
- [`docs/plans/plan_v2_substrate_reevaluation.md`](../plans/plan_v2_substrate_reevaluation.md) - block format, mutation API, six-phase migration
- [`docs/plans/v2_substrate/plan/build-plan.md`](../plans/v2_substrate/plan/build-plan.md) - Waves 1-6, coverage table of unassigned gaps
- [`docs/plans/v2_substrate/plan/model.md`](../plans/v2_substrate/plan/model.md) - generative core, model drift log
- [`docs/plans/v2_substrate/plan/dry_run_2026-08-10.md`](../plans/v2_substrate/plan/dry_run_2026-08-10.md) - the `GATE PASSED: False` result
- ADRs [0001](../decisions/0001-power-law-decay-rejected.md) (power-law rejected), [0002](../decisions/0002-v017-scope.md) (four-part reopen gate), [0003](../decisions/0003-defer-constitutional-evolution.md) (event log tables rejected by name), [0006](../decisions/0006-defer-multi-parameter-self-tuning.md) (learned weights disqualified), [0007](../decisions/0007-bound-and-checkpoint-consolidation.md), [0009](../decisions/0009-retire-decay-driven-archival.md) (decay archival retired), [0010](../decisions/0010-retire-pairwise-contradiction-detection.md) (contradiction write-side retired), [0011](../decisions/0011-substrate-migration-as-a-migrate-step.md) (cutover deferred)
- Live production database: `~/.elfmem/databases/elfmem.db`, queried 2026-08-24

**Code cited**
`src/elfmem/scoring.py:113-152` (composite score) · `src/elfmem/memory/retrieval.py:219-268` (RRF), `:392-447` (MMR) · `src/elfmem/context/rendering.py:41-58` (break-on-overflow budgeter) · `src/elfmem/operations/recall.py:101-104` (unconditional reinforcement) · `src/elfmem/operations/consolidate.py:318-329` (constitutional pin guard) · `src/elfmem/memory/blockfile.py:159-242` (`##` boundary heuristic), `:16-19` (permanent-ID invariant) · `src/elfmem/memory/index_rebuild.py:20-25` (round-trip trade-offs) · `src/elfmem/migration/export.py:55-79` (frontmatter fields) · `src/elfmem/db/queries.py:89-92` (the forced-zero rebuild fields) · `src/elfmem/memory/file_mutation.py:9-12` (DB-primary until migration completes) · `.gitignore:17`

**External**
- Webber, Moffat and Zobel, "A Similarity Measure for Indefinite Rankings", ACM TOIS 2010 - Rank-Biased Overlap, proposed in 9.1
- Carbonell and Goldstein, "The Use of MMR, Diversity-Based Reranking", SIGIR 1998 - the source of the `lambda = 0.7` default already in `retrieval.py`
- SAGE ([arXiv 2605.30711](https://arxiv.org/pdf/2605.30711)) - cheap novelty gate before LLM curation wins on quality *and* cost; already cited in the 2026-08-08 doc §4.4 and the strongest external support for 6.4
- Zhou et al., ["Filesystem-Based Memory for LLM Agents"](https://arxiv.org/html/2607.26637v1) - Taxonomy Contract P1-P5; relevant to `cls` and to file organisation
- MOSS ([arXiv 2607.04391](https://arxiv.org/pdf/2607.04391)), markdown-vault-mcp, Vault-LD - the three independent files-as-truth corroborations from the 2026-08-08 sweep
