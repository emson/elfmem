# elfmem v2 - Substrate Re-evaluation

> **Status**: Proposed - evaluation and redesign, not yet committed
> **Date**: 2026-08-04
> **Scope**: block mutation control, consolidation cost, LLM gateway, config/env, onboarding
> **Evidence base**: the live `elfmem` dev instance (`~/.elfmem/databases/elfmem.db`), 2026-04-07 to 2026-06-21

---

## 1. Verdict up front

elfmem's **storage model is sound**. Its **curation model is not** - and the
curation model is where all four reported problems come from.

The system spends **1.65M LLM input tokens across 2,903 calls** to maintain a
live corpus of **31.6k tokens**. That is a **52x amplification ratio**. In
exchange for that spend, the consolidation pipeline has:

- archived **41 blocks by supersession and exactly 1 by decay** - the entire
  biological decay apparatus (four tiers, session-aware clock, λ constants,
  power-law rejection ADR) has retired **one** block in four months
- silently destroyed constitutional blocks it was explicitly designed to
  protect, including one at `λ=1e-05` with `confidence=1.0`
- produced **14 contradiction findings, 12 still unresolved**, at a cost of
  roughly two thirds of all LLM calls made

Meanwhile the operation the user actually wants - *"change this block"* - has
**no API at all**.

The recommendation is not "replace elfmem with markdown". It is:

> **Make markdown files the source of truth and SQLite a derived, rebuildable
> index. Move all LLM curation from per-block write-time to corpus-level,
> proposal-only, human-approved review.**

This keeps everything elfmem is good at (retrieval, scoring, graph, peers),
deletes the parts that are provably destructive, cuts LLM cost by roughly three
orders of magnitude, and makes "edit a block" a text edit with a git diff.

---

## 2. Evidence - what the live instance actually shows

All figures computed directly from the production DB, not estimated.

### 2.1 Cost

| Metric | Value |
|---|---|
| Lifetime LLM calls | **2,903** |
| Lifetime LLM input tokens | **1,646,801** |
| Lifetime LLM output tokens | 60,186 |
| Embedding calls | 49 |
| Blocks ever created | 185 |
| Blocks active now | 140 |
| Live corpus size | 126,532 chars ≈ **31,633 tokens** |
| Live summaries size | 34,908 chars ≈ 8,727 tokens |
| **LLM calls per surviving block** | **20.7** |
| **Input tokens per corpus token** | **52.1x** |
| **Input tokens per summary token** | **188.7x** |

At local-adapter latency (~14s/call, the figure cited in `config.yaml` and
ADR 0007), 2,903 calls is roughly **11 hours of GPU time** to curate 31.6k
tokens of text. On a hosted model the dollar cost is trivial; the *latency*
cost is not, and it is why `dream()` became a kill-and-lose-progress hazard
serious enough to need two releases of mitigation (v0.19.2, ADR 0007).

### 2.2 Where the archival actually comes from

```
archive_reason | count
---------------+------
superseded     |   41
(decayed)      |    1
```

**41 of 42 archivals were near-duplicate supersession.** One was decay.

The decay machinery is not merely underused, it is arithmetically inert. The
session-aware clock reads **3.69 total active hours across 370 sessions**
(average session: 36 seconds). Recency after the entire lifetime of the
instance:

| Tier | λ | recency after 3.69h |
|---|---|---|
| PERMANENT | 1e-05 | 1.0000 |
| DURABLE | 0.001 | 0.9963 |
| STANDARD | 0.01 | 0.9638 |
| EPHEMERAL | 0.05 | 0.8317 |

Nothing decays because the clock barely advances. The four-tier decay model,
the ADR rejecting power-law decay, `curate()`, `prune_threshold`, and the
DECAY→ARCHIVE lifecycle stage are, on this deployment, **dead weight with a
maintenance cost**.

### 2.3 The constitution eroded - and this is the drift being reported

`setup()` seeds 10 constitutional blocks, each tagged `self/role/<name>`, each
documented as PERMANENT decay, "guaranteed in every SELF frame retrieval",
"auto-reinforced by `curate()`".

Live state: **4 of 10 role slots remain**, and all four hold *different content
than was seeded*.

```
self/role/uncertainty  -> "Authoritative state is read, never inferred..."   (created 2026-05-08)
self/role/minimum-force-> "Minimum-force on commands. Before adding a new..."
self/role/stewardship  -> "The recovery surface is the apology in code..."
self/role/feedback-loop-> "Failure-shape recognition: config-vs-reality..."
```

The original seed text for `uncertainty` ("Name what you do not know before
acting...") is **archived, reason: superseded**. So is the identity block
recording that the agent chose the name *elf*. So is a block at `λ=1e-05,
confidence=1.0` describing the four rhythms - a maximally-protected block,
destroyed anyway.

The mechanism is in `operations/consolidate.py` (`_collect_decisions` decides,
`_apply_decisions` executes) - not, as an earlier pass of this analysis
mis-cited, the near-identical but dead `resolve_near_duplicate` in
`memory/dedup.py`, which has zero callers in `src/`:

```python
# _collect_decisions - the decision, unguarded:
if not is_message and best_active is not None and best_sim >= near_dup_near_threshold:
    supersedes_id = best_active["id"]      # no tier, pin, or tag check

# _apply_decisions - the execution:
if d.action == "supersede" and d.supersedes_id:
    await update_block_status(conn, d.supersedes_id, "archived", archive_reason="superseded")
```

At `near_dup_near_threshold = 0.90`, any incoming block within 0.90 cosine of
an existing one triggers a **wholesale, unlogged, irreversible overwrite**.
`update_block_status` additionally hard-deletes the archived block's
`block_tags`, `edges`, and `contradictions` rows outright (FK CASCADE only
fires on a physical `DELETE`, not a status `UPDATE`, so this is an explicit
delete in the same function) - discarding tags, the edges α/β evidence relied
on, and role in one step. Supersession does not consult decay tier,
`confidence`, `self/constitutional`, or pinning, because **no such guard
exists**.

Confirmation in the data: active blocks carry 616 tags across 140 blocks (4.4
each); archived blocks carry **7 tags across 42 blocks** (0.17 each). The
blocks that died were the ones that never accumulated standing.

This is the single highest-severity defect in the system. It is not a tuning
problem. A threshold change moves *which* blocks get silently overwritten, not
*whether* silent overwriting is the mechanism.

### 2.4 The expensive subsystems have near-zero realised value

| Subsystem | Release | Lifetime usage on live instance |
|---|---|---|
| Contradiction detection | core | 14 found, **12 unresolved**; ~2/3 of all LLM calls |
| Bayesian α/β outcome evidence | v0.17 | **6 outcome records** |
| Constitutional review + amendments | v0.18 | **0 amendments** |
| Knowledge graph | core | 53 edges total (27 similar, 9 co_occurs, 9 replies_to, 8 predicts) |
| Theory-of-Mind `mind/*` | v0.14 | 3 blocks |

Each of these is well-engineered, well-tested, documented with an ADR, and
carries permanent maintenance cost. Together they represent a large fraction of
the 23,499 source lines. Their combined observable effect on the live instance
is close to zero.

### 2.5 Reinforcement is a rich-get-richer loop with no counterweight

Top reinforcement counts: **92, 74, 67, 64, 36, 25, 20, 20, 19, 19...**

`curate()` reinforces the top-5 every cycle. The only external corrective
signal is `outcome()`, which has fired 6 times. So the ranking is
overwhelmingly determined by its own history. Blocks are important because they
were retrieved, and they are retrieved because they are important.

### 2.6 Operational reality

`last_consolidated_at` is **2026-06-21**; today is 2026-08-04. Three blocks
have sat in the inbox for six weeks. The heaviest, slowest operation in the
system is the one a human has to remember to run, and stopped running.

---

## 3. Diagnosis - five root causes

**RC1 - Mutation is implicit and LLM-mediated; there is no direct write path.**
`MemorySystem` exposes ~40 public methods. Not one of them is "change the
content of block X", "delete block X", or even "list blocks". `update_block_content`
exists in `db/queries.py` but is reachable only through the constitutional
amendment flow. Every change to memory is a *side effect* of an LLM decision.
This is the reported drift, exactly: the agent cannot steer its own memory, so
memory drifts.

**RC2 - Write-time pairwise LLM curation is the wrong shape at this scale.**
Cost is O(inbox x active) LLM calls for contradiction plus O(inbox) for
analysis. But the entire corpus is 31.6k tokens - it fits in one context
window with room to spare. Pairwise reasoning at 2,903 calls is solving, very
expensively, a problem that one call could address better, because one call can
see the whole corpus at once and pairwise calls never can.

**RC3 - Destructive operations have no audit trail and no undo.**
Supersession writes `archive_reason='superseded'` and nothing else: no record
of *which* block superseded it, no diff, no revert. `revert_amendment()` exists
for the one path that was designed with review in mind - which proves the team
already knows the right pattern, it just was not applied to the path that does
99% of the damage.

**RC4 - Configuration resolution is multi-layered, partly implicit, and
unverifiable.** Config comes from CLI flag, `ELFMEM_CONFIG`, project-local
YAML, global YAML, `ELFMEM_*` env vars, and `from_env()`; API keys come from
neither - they are delegated entirely to the OpenAI/Anthropic SDK defaults
(`OPENAI_API_KEY` / `ANTHROPIC_API_KEY`), with `--env-file` wired only to
`serve`. Consequences visible in the repo's own history: v0.19.3 shipped
because the MCP entry drifted to a *different project's* config and degraded
silently. The adapter is deliberately constructed with `api_key=None` so
non-LLM commands do not fail - which converts a missing key from a startup
error into a silent runtime degradation.

**RC5 - Onboarding writes opinionated content into memory before the user has
any opinion.** `init --seed` defaults to ON and injects 10 prose blocks
averaging ~250 characters of aspirational voice ("Curiosity is my primary
drive...") straight into the inbox. They then cost 10+ LLM calls to consolidate,
become subject to supersession, and **cannot be removed** because there is no
delete. `init --no-seed` exists but is not the default and does not help anyone
who already ran `init`.

---

## 4. The central question: markdown documents, or something more powerful?

The brief asks whether plain markdown memory documents would be better. Taken
literally the answer is "for your current scale, yes, and it is not close" -
but that framing leaves value on the table. The productive answer is that
**markdown and the database are not competitors; they are different layers that
have been collapsed into one.**

### What markdown wins on, decisively

| Property | Markdown | Current elfmem |
|---|---|---|
| Change a block | edit the line | **no API exists** |
| Delete a block | delete the line | **no API exists** |
| See what changed | `git diff` | not recorded |
| Undo a bad change | `git revert` | only for amendments |
| Review before applying | PR / diff | supersession is silent |
| Cost to maintain | zero | 1.65M tokens |
| Human-inspectable | yes | requires SQL |

Every single reported problem lands in this table.

### What the database wins on

Retrieval above context budget, embeddings, FTS, derived edges, usage
statistics, concurrent access. All real - **but none of them require the
database to be authoritative.**

### The synthesis

> **Files are truth. The index is derived and disposable.**

This is the Obsidian/Datasette pattern, and it dissolves the conflict:

- Editing memory is editing a file. Direct, total control. (RC1)
- Drift becomes a **diff**. Git is the audit trail you were going to have to
  build anyway. (RC3)
- `rm index.db && elfmem index` loses **nothing**. That invariant is what makes
  every other simplification safe to make.
- Indexing needs embeddings only - **zero LLM calls**. (RC2)
- Retrieval, scoring, and the graph survive unchanged; they just read from a
  cache instead of from the master copy.

And it unlocks the "something more powerful" the brief is reaching for:
**corpus-level reasoning**. At 31.6k tokens the whole memory fits in one
prompt. One LLM call that sees everything can find duplicate *clusters*,
transitive contradictions, and thematic drift - things 2,903 pairwise calls
structurally cannot see. Cheaper and strictly more capable.

---

## 5. Proposed architecture

### 5.1 Layers

```
L1  SUBSTRATE   .elfmem/memory/**.md          git-versioned, hand-editable, AUTHORITATIVE
                  self.md                     constitution - never a block, never consolidated
                  notes/*.md                  curated knowledge, one ## heading per block
                  log/YYYY-MM.md              append-only fast path for learn()

L2  INDEX       .elfmem/index.db              DERIVED. embeddings + FTS5 + edges + usage stats
                                              rebuilt by `elfmem index`. zero LLM calls.
                                              deletable without loss.

L3  RETRIEVAL   frame() / recall()            budget-driven selection
                                              corpus <= budget -> return all, ordered
                                              corpus >  budget -> FTS5 + vector + RRF

L4  REVIEW      elfmem review                 corpus-level, scheduled, ONE LLM call
                                              emits a PROPOSAL FILE, mutates nothing
```

### 5.2 Block format

```markdown
## Minimum force on commands
<!-- id: 8f3a2b1c  tags: [self/value, cli]  pinned: true  created: 2026-05-08 -->

Before adding a new top-level command, apply the test: does this extend an
existing verb? If yes, extend it.
```

`id` is stable and content-independent so usage statistics survive edits.
`pinned: true` is the guard that supersession never had - a pinned block is
never proposed for removal and is always included in its frame.

### 5.3 The mutation API that was missing

| Operation | Command | LLM cost |
|---|---|---|
| Add | `elfmem learn "..."` (appends to log) | none |
| **Edit** | edit the file, or `elfmem edit <id>` | none |
| **Delete** | delete the lines, or `elfmem forget <id>` | none |
| **List** | `elfmem ls [--tag ...]` | none |
| **Diff** | `git diff .elfmem/memory/` | none |
| **Undo** | `git revert` | none |
| Promote log -> note | `elfmem promote <id> --to notes/x.md` | none |

Seven operations, none of which exist today, all of them free.

### 5.4 Review: proposal, not mutation

```
$ elfmem review
Analysing 140 blocks (31.6k tokens) in 1 call...
Wrote .elfmem/review-2026-08-04.md

  6 duplicate clusters      (14 blocks -> 6)
  3 contradictions          (2 flagged high-confidence)
  9 stale candidates        (no retrieval in 90d, no outcome)
  2 constitutional drifts

$ elfmem review --apply .elfmem/review-2026-08-04.md
```

The proposal file is markdown with checkboxes. You tick what you want. `--apply`
edits the files, so the result is a **git diff you can read and revert**.

This single change addresses RC1, RC2 and RC3 simultaneously: it restores
control, cuts cost by ~1000x, and makes every mutation auditable.

### 5.5 LLM gateway

```yaml
llm:
  default: local
  profiles:
    local:  { base_url: http://localhost:1234/v1, model: google/gemma-3-27b, api_key: none }
    cheap:  { base_url: https://openrouter.ai/api/v1, model: qwen/qwen-2.5-72b,
              api_key_env: OPENROUTER_API_KEY }
    deep:   { provider: anthropic, model: claude-haiku-4-5-20251001,
              api_key_env: ANTHROPIC_API_KEY }
  tasks:
    summarise: local     # cheap, high volume, low stakes
    review:    deep      # rare, whole-corpus, high stakes
```

Three fixes over `make_llm_adapter`'s `model.startswith("claude")` branch:

1. **`api_key_env` is explicit.** OpenRouter, Groq, Together and Fireworks all
   work today without code changes. Currently only `OPENAI_API_KEY` is ever
   read for OpenAI-compatible endpoints, so OpenRouter is broken by
   construction.
2. **`api_key: none` is a valid, explicit value** for local endpoints. It
   injects a dummy so the OpenAI SDK is satisfied. No more "why does LM Studio
   need `OPENAI_API_KEY`".
3. **Task-to-profile routing.** Bulk summarisation on a local model, rare
   high-stakes review on a strong hosted one. This is where the cost/quality
   trade actually lives.

### 5.6 Config and secrets - one chain, printed

Resolution order, applied identically by **every** entry point including
`serve`:

```
1. CLI flag
2. Process environment
3. .env at project root        <- auto-loaded everywhere, not just `serve --env-file`
4. .elfmem/config.yaml
5. Code defaults
```

Secrets come **only** from layers 2 and 3, so `config.yaml` is committable by
construction. `.env` is auto-discovered by walking up from cwd, the same way
the config already is.

And the piece that would have prevented v0.19.3:

```
$ elfmem doctor --resolve
config      /Users/.../elf0_mem_sim/.elfmem/config.yaml   [project-local]
db          /Users/.../databases/elfmem.db                [project.db in config]
memory dir  /Users/.../elf0_mem_sim/.elfmem/memory        [derived from config]
llm.local   http://localhost:1234/v1                      [config.yaml]
  api_key   (none - local endpoint)                       [explicit]
  preflight OK (1 call, 240ms)
llm.deep    claude-haiku-4-5-20251001                     [config.yaml]
  api_key   ANTHROPIC_API_KEY                             [.env]
  preflight OK (1 call, 890ms)

WARN: MCP entry in ~/.claude.json points at a DIFFERENT config
      expected .../elf0_mem_sim/.elfmem/config.yaml
      found    .../other_project/.elfmem/config.yaml
```

**Preflight makes one real call per profile and fails loudly.** The current
design's deliberate `api_key=None` tolerance is what turns a missing key into
silent mock behaviour - the exact failure v0.19.3 was written to fix, fixed at
the root instead of at one call site.

### 5.7 Onboarding - init writes zero blocks

```
$ elfmem init
Created .elfmem/config.yaml
Created .elfmem/memory/self.md      (template - edit it, nothing is active yet)
Created .env.example
Created .elfmem/.gitignore          (index.db, .env)

Nothing is in memory yet. Edit .elfmem/memory/self.md, then: elfmem index
```

`self.md` ships as **commented-out** suggestions:

```markdown
# Identity

<!-- Uncomment what applies. Delete what does not. This file IS your
     constitution - it is read directly into the self frame, never
     consolidated, never superseded, never decayed. -->

<!-- ## Minimum force
     Apply the minimum force that solves the problem. Complexity is debt. -->
```

`--template coding` inserts more commented-out blocks. Nothing enters memory
until the user uncomments and runs `index`. Removing something you did not want
is deleting a line. **RC5 stops being possible.**

---

## 6. Simulation

Optimize intent. Goal: **maximise control and correctness per unit of LLM
cost**, subject to robust / flexible / elegant.

### Fitness dimensions (ranked)

1. **Control** - can the operator deterministically change/remove any memory?
2. **Integrity** - can memory be silently destroyed?
3. **Cost** - LLM calls to maintain steady state
4. **Simplicity** - source lines and concepts a maintainer must hold
5. **Scale headroom** - behaviour as the corpus grows 100x

Scale: 🔴 poor / 🟡 medium / 🟢 good.

### Frozen scenario set

| ID | Scenario | Nature |
|---|---|---|
| S1 | Operator wants to reword one constitutional principle | control |
| S2 | Operator wants to delete 3 unwanted seeded blocks | control |
| S3 | New block lands at 0.91 cosine to a pinned constitutional block | adversarial |
| S4 | 10 blocks learned in a batch; steady-state maintenance cost | cost |
| S5 | Fresh install by a new user on a new machine, local model only | onboarding |
| S6 | API key absent; MCP server starts anyway | adversarial |
| S7 | Corpus grows 140 -> 14,000 blocks | scale |
| S8 | Two agents write concurrently | adversarial |
| S9 | Operator must audit "what changed in memory last month" | integrity |
| S10 | Long-running instance: does the constitution survive 6 months? | integrity |

---

### Iteration 1 - Baseline (elfmem v0.19.3 as shipped)

**World model**: blocks in SQLite; `learn()` -> inbox; `consolidate()` -> LLM
analysis + near-dup supersession + pairwise contradiction; `curate()` -> decay
archival; retrieval = 7-stage hybrid.

**Step-wise run:**

- **S1** - No edit API. Operator must `learn()` a reworded near-duplicate and
  *hope* it lands in [0.90, 0.95) so it supersedes rather than being rejected
  as exact-dup or promoted as a second copy. Outcome depends on a cosine value
  the operator cannot see. **FAIL.**
- **S2** - No delete API. Blocks can only leave via supersession or decay;
  decay is inert (3.69 active hours). **FAIL - and this is the reported bug.**
- **S3** - `resolve_near_duplicate` archives the constitutional block. No tier
  check, no pin check, no log. Observed 3x in production. **FAIL, severity
  critical.**
- **S4** - 10 blocks x (1 analysis + up to 10 contradiction) = up to 110 calls.
  Measured lifetime rate: 20.7 calls/surviving block. **FAIL.**
- **S5** - Needs `OPENAI_API_KEY` set even for a purely local LM Studio setup;
  `init --seed` injects 10 blocks the user did not choose. **FAIL.**
- **S6** - Adapter constructed with `api_key=None` by design; server boots and
  degrades silently. Documented in v0.19.3 changelog. **FAIL.**
- **S7** - Retrieval is fine (prefilter + vector + RRF). Consolidation is not:
  contradiction is O(inbox x active), capped at top_k=10 by ADR 0007 -
  mitigated, not solved. **PARTIAL.**
- **S8** - SQLite WAL, read/compute/write split. Genuinely good. **PASS.**
- **S9** - No mutation log. `archive_reason` records *that* something was
  superseded, never *by what*. **FAIL.**
- **S10** - Measured: 6 of 10 constitutional roles lost in ~2.5 months; the
  4 survivors hold different content than seeded. **FAIL.**

**Fitness**: Control 🔴 · Integrity 🔴 · Cost 🔴 · Simplicity 🔴 (23.5k LOC) ·
Scale 🟡

**Verdict**: BASELINE. 8 fails, 1 partial, 1 pass.

---

### Iteration 2 - Minimal patch: add CRUD + guard supersession

**Changes tried**: add `edit()`, `forget()`, `ls()`; add a `pinned` column;
refuse supersession of pinned or PERMANENT blocks; log supersessions to a new
`block_mutations` table.

**Step-wise run against frozen set:**

- S1 🟢 `edit()` exists. S2 🟢 `forget()` exists. S3 🟢 pin guard holds.
  S9 🟢 mutation log. S10 🟢 constitution protected by pin.
- S4 🔴 **unchanged** - 20.7 calls/block. Nothing touched the cost model.
- S5 🔴 unchanged. S6 🔴 unchanged. S7 🟡 unchanged. S8 🟢 unchanged.
- Simplicity 🔴 **worse** - one more table, one more column, three more API
  methods, three more MCP tools, three more guide entries on top of 23.5k LOC.

**Fitness**: Control 🟢 · Integrity 🟢 · Cost 🔴 · Simplicity 🔴 (regressed) ·
Scale 🟡

**Verdict**: **KEPT as incumbent.** It fixes the two critical integrity
failures and is by far the cheapest path to doing so. But it fails the brief on
two of four stated goals - cost is untouched and complexity increased. This is
the correct *emergency patch*, not the destination.

---

### Iteration 3 - Move curation from write-time to corpus-level review

**Changes tried**: on top of Iteration 2 - delete pairwise contradiction
detection from `consolidate()`; add `elfmem review` sending the whole corpus in
one call; output a proposal file; `--apply` performs the mutations through the
Iteration-2 CRUD path.

**Step-wise run against frozen set:**

- S1, S2, S3, S9, S10 🟢 - **regression clean**, all inherited from Iteration 2
  and unaffected.
- S4 🟢 **10 blocks now cost 10 summarisation calls at write time (skippable)
  plus 1 scheduled review call.** From up to 110 down to ~1-11.
- S5 🔴 unchanged. S6 🔴 unchanged.
- S7 🟢 **improved** - at 14,000 blocks the corpus exceeds one window, so review
  degrades to changed-slice + sample. That is graceful; pairwise O(n) per block
  was not.
- S8 🟢 unchanged.
- Simplicity 🟡 **improved** - deletes the contradiction loop, the prefilter,
  `contradiction_top_k`, `contradiction_cap_rate`, and most of ADR 0007's
  checkpointing machinery, which existed only to survive the slow path being
  removed.
- **New failure surface discovered**: whole-corpus review at 14k blocks needs
  chunking, and chunk boundaries can hide cross-chunk duplicates. Added as
  **S11** to the frozen set. Mitigation: cluster by embedding first, review
  cluster-wise. Deferred - not binding below ~2,000 blocks.

**Fitness**: Control 🟢 · Integrity 🟢 · Cost 🟢 · Simplicity 🟡 · Scale 🟢

**Verdict**: **KEPT.** Strict improvement on the frozen set, no regressions.

---

### Iteration 4 - Files as truth, index derived

**Changes tried**: on top of Iteration 3 - markdown under `.elfmem/memory/`
becomes authoritative; SQLite becomes a derived index rebuilt by
`elfmem index` with zero LLM calls; `self.md` is read directly into the self
frame and never enters the block table.

**Step-wise run against frozen set:**

- **S1** 🟢 edit the file. Better than Iteration 3's `edit()`: reviewable diff,
  no API needed.
- **S2** 🟢 delete the lines.
- **S3** 🟢 **structurally impossible** - `self.md` is not in the block table,
  so no supersession path can reach it. Iteration 2 needed a guard; here the
  failure mode does not exist.
- **S4** 🟢 unchanged from Iteration 3.
- **S5** 🟢 **fixed** - `init` writes a commented-out template file and zero
  blocks. Nothing to remove because nothing was added.
- **S6** 🟡 partially - a bad key no longer corrupts memory (indexing needs no
  LLM), but silent degradation is still possible. Needs Iteration 5.
- **S7** 🟢 unchanged.
- **S8** 🟡 **REGRESSION** - files plus two concurrent writers means merge
  conflicts, where SQLite WAL was clean. Mitigation: `learn()` appends to
  `log/YYYY-MM.md` (append-only, conflict-free); only curated `notes/` are
  hand-edited, and those are human-paced. Accepted as 🟡 with mitigation, not
  🟢.
- **S9** 🟢 `git log -p .elfmem/memory/` - strictly better than a mutation
  table.
- **S10** 🟢 constitution is a file; it survives by construction.
- **S11** 🟢 unchanged from Iteration 3.
- Simplicity 🟢 - deletes decay tiers, `curate()`, supersession, the
  inbox/active/archived state machine, `rescue`, embedding-model lock (reindex
  instead), and the Iteration-2 mutation log. Retrieval, scoring and peers
  survive.

**Fitness**: Control 🟢 · Integrity 🟢 · Cost 🟢 · Simplicity 🟢 · Scale 🟢

**Verdict**: **KEPT.** One accepted regression (S8) with a concrete mitigation.

---

### Iteration 5 - Gateway, resolution chain, preflight

**Changes tried**: profile-based LLM gateway with explicit `api_key_env` and
`api_key: none`; `.env` auto-loaded by every entry point; `doctor --resolve`
prints every resolved value with its winning layer; preflight makes one real
call per profile at startup.

**Step-wise run against frozen set:**

- S1-S5, S7, S9-S11 🟢 - **regression clean**, unaffected.
- **S6** 🟢 **fixed** - preflight fails loudly at boot. `doctor --resolve`
  surfaces MCP-entry drift, which is the v0.19.3 incident caught at the root.
- **S8** 🟡 unchanged (mitigated).
- Simplicity 🟢 - replaces the `startswith("claude")` branch and the scattered
  `_read_env` / `resolve_config` / `resolve_db` / `--env-file` special cases
  with one chain applied uniformly.
- **New**: OpenRouter and Groq work with no code change, which the brief
  explicitly asked for.

**Fitness**: Control 🟢 · Integrity 🟢 · Cost 🟢 · Simplicity 🟢 · Scale 🟢

**Verdict**: **KEPT.** All dimensions green; only S8 remains amber by accepted
trade.

---

### Iteration 6 - Attempted: drop SQLite entirely, pure markdown + grep

**Changes tried**: remove the index; retrieval by ripgrep and whole-file
inclusion.

**Step-wise run against frozen set:**

- S1, S2, S5, S9, S10 🟢 unchanged.
- **S7** 🔴 **REGRESSION** - at 14,000 blocks (~3M tokens) there is no semantic
  retrieval and no budget mechanism. Fails hard.
- S3 🟢, S4 🟢, S6 🟢, S8 🟢 (no DB to conflict), S11 n/a.
- Simplicity 🟢 - simplest possible.

**Fitness**: Control 🟢 · Integrity 🟢 · Cost 🟢 · Simplicity 🟢 · **Scale 🔴**

**Verdict**: **REJECTED.** Wins on simplicity, loses the primary
differentiator. Incumbent remains Iteration 5. The index costs little (it is
derived and rebuildable) and buys the entire scale story; deleting it trades a
permanent capability for a one-time simplification.

---

### Journey table

| Iter | Change | vs incumbent on frozen set | Kept? |
|---|---|---|---|
| 1 | Baseline v0.19.3 | 8 fail / 1 partial / 1 pass | baseline |
| 2 | CRUD + pin guard + mutation log | +5 scenarios fixed; simplicity regressed | ✅ kept |
| 3 | Corpus-level review replaces pairwise | +1 fixed, +1 improved, 0 regressions; simplicity improved | ✅ kept |
| 4 | Files authoritative, index derived | +2 fixed, S3 made impossible; **S8 regressed to 🟡** | ✅ kept |
| 5 | Gateway + resolution chain + preflight | +1 fixed, 0 regressions | ✅ **incumbent** |
| 6 | Drop SQLite entirely | **S7 regressed 🟢→🔴** | ❌ rejected |

**Stopping reason**: goal reached at Iteration 5 (all five fitness dimensions
green, with one accepted amber trade at S8), and Iteration 6 confirmed the
plateau by demonstrating that further simplification costs a primary capability.

### Winner

**Iteration 5**: files as truth, SQLite as a derived index, corpus-level
proposal-only review, profile-based LLM gateway with a single printed
resolution chain and boot-time preflight.

It wins because it fixes all four reported problems at their root rather than
at their symptom, and because three of its wins are *structural* rather than
defensive - S3, S5 and S10 are not guarded against, they become impossible.

---

## 7. What gets deleted

Roughly 8-11k of 23.5k source lines, on the evidence that they do no observable
work on a real deployment:

| Subsystem | Why | Evidence |
|---|---|---|
| Pairwise contradiction detection | replaced by corpus review | ~2/3 of all LLM calls, 12/14 findings unresolved |
| Decay tiers, λ, `curate()` archival | inert | 1 decayed block in 4 months; recency 0.96 at STANDARD |
| Near-dup supersession | destructive | 41 archivals, 3 constitutional casualties |
| inbox/active/archived state machine | no longer needed | files are the state |
| `rescue.py`, embedding-model lock | reindex is free | 307 + lock machinery |
| ADR 0007 checkpointing | existed to survive the slow path | `max_inbox_per_run`, `contradiction_top_k` |
| `migrate.py` MCP-drift scanning | replaced by `doctor --resolve` | 836 lines |

**Kept**: retrieval and scoring, embeddings and FTS, the graph (derived), α/β
outcomes (in the index), peers, minds, MCP surface, `guide()`.

**Honest note**: several of these carry ADRs recording deliberate decisions
(0001 power-law decay, 0003 constitutional evolution, 0006 self-tuning, 0007
checkpointing). Removing them is not overturning bad reasoning - each ADR is
sound given its premises. What changed is the *evidence*: those decisions were
made without the lifetime-usage data now available, and that data says the
subsystems they govern are not load-bearing. Each removal warrants its own ADR
citing section 2 of this document.

---

## 8. Migration

Non-destructive, reversible, and verifiable at each step.

```
Phase 0  git tag pre-v2; cp elfmem.db elfmem.pre-v2.bak
Phase 1  elfmem export --to-markdown .elfmem/memory/
         -> one file per category, frontmatter preserves id/tags/confidence/alpha/beta
         -> archived blocks go to .elfmem/memory/archive/ (recoverable, not deleted)
Phase 2  git add .elfmem/memory/ && git commit     # the corpus is now versioned
Phase 3  elfmem index                              # rebuild derived index, no LLM
Phase 4  verify: block count, frame() output, and recall() top-5 for 10 fixed
         queries must match pre-migration output
Phase 5  hand-restore the 6 lost constitutional roles into self.md
Phase 6  flip authority: DB becomes derived; delete-and-reindex is now safe
```

Phase 4 is the gate. If retrieval output diverges, stop and diagnose - do not
proceed on the assumption that the new ranking is "probably fine".

Phase 5 is worth doing deliberately: the four surviving role slots hold *earned*
content (recovery surfaces, minimum-force on commands) that is arguably better
than the seeded prose. The right move is to keep the earned content and
re-add the six missing *roles* alongside it, rather than restoring the original
text wholesale.

---

## 9. Sequencing recommendation

The full v2 is a large change. It decomposes into independently shippable
pieces, ordered by value per unit of risk:

| Step | Ship | Fixes | Risk |
|---|---|---|---|
| **1** | Pin guard on supersession + supersession log | stops active data loss | trivial |
| **2** | `edit()` / `forget()` / `ls()` | RC1, the headline complaint | low |
| **3** | `doctor --resolve` + `.env` everywhere + preflight | RC4 | low |
| **4** | `init` writes zero blocks | RC5 | low |
| **5** | LLM gateway profiles + `api_key_env` | OpenRouter/local | low |
| **6** | `elfmem review` corpus-level, proposal-only | RC2 | medium |
| **7** | Retire pairwise contradiction + decay | cost, complexity | medium |
| **8** | Markdown substrate + derived index | structural | high |

**Steps 1-5 are worth doing regardless of whether step 8 is ever taken.** They
are small, independently valuable, and each closes a reported problem. Step 1
should ship this week - the system is currently losing constitutional data in
production.

---

## 10. Residual risks

| Risk | Likelihood | Impact | Note |
|---|---|---|---|
| Concurrent file writes conflict (S8) | medium | medium | append-only `log/` for `learn()`; curated `notes/` are human-paced |
| Whole-corpus review outgrows the window (S11) | low now, high at 10x | medium | cluster-then-review; not binding below ~2,000 blocks |
| Losing α/β and reinforcement history on reindex | medium | low | keyed by stable `id`; only 6 outcomes exist to lose |
| Markdown parsing is a new bug surface | medium | low | strict format, fail-fast, `elfmem index --check` |
| Peer protocol needs rework for file substrate | high | low | files export more naturally than block bundles |
| This document over-fits one instance | medium | **high** | see below |

**The last risk is the important one.** Every number here comes from a single
deployment - the dev instance, used by one person, with unusually short sessions
(36s average) that make the session-aware clock behave pathologically. A
deployment with hour-long sessions would see decay actually function, and the
"decay is inert" finding would weaken considerably.

What does **not** depend on that instance: the absence of an edit/delete API,
the `"inherits nothing"` supersession semantics, the unguarded overwrite of
PERMANENT blocks, the 52x cost amplification, and the `OPENAI_API_KEY`-only key
resolution. Those are properties of the code, and they hold for every
deployment.

Before committing to step 8, it is worth gathering the same `lifetime_token_usage`
and `archive_reason` breakdown from the Alv and Mira instances. If they show the
same 40:1 supersession-to-decay ratio, the case is closed. If they show real
decay activity, steps 1-7 still stand and step 8 needs re-argument.

---

## 11. Open decisions

1. **Emergency patch, or wait for v2?** Step 1 (pin guard) is a few lines and
   stops live data loss. Recommend shipping it standalone, immediately.
2. **Is `self.md`-as-file acceptable**, given that constitutional blocks
   currently participate in retrieval scoring? It would become always-included
   preamble instead - which is *Architecture M* from ADR 0003, previously
   deferred, and measured there at +33pp under drift.
3. **One file per block, or many blocks per file?** This document assumes
   many-per-file with `##` headings. One-per-file is simpler to parse and
   diff, worse to browse.
4. **Do the peer, mind, and amendment subsystems survive v2 unchanged**, or
   is this the moment to reassess them against the same usage evidence?
