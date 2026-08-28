# elfmem

[![Tests](https://github.com/emson/elfmem/actions/workflows/ci.yml/badge.svg)](https://github.com/emson/elfmem/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/elfmem.svg)](https://pypi.org/project/elfmem/)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Codecov](https://codecov.io/gh/emson/elfmem/branch/main/graph/badge.svg)](https://codecov.io/gh/emson/elfmem)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**Adaptive memory for LLM agents. Knowledge that lives in Markdown and evolves through use.**

Your agent's memory is a folder of Markdown files you can read, edit, diff and
commit. The database is an index derived from those files, and you can delete it
at any time. Knowledge that proves useful gets stronger; knowledge that misleads
fades and is archived. The agent's identity persists across every session.

A folder of Markdown files. Zero infrastructure. Any LLM provider.

![elfmem knowledge graph dashboard](docs/elfmem-knowledge-visualisation.jpg)

*An agent's knowledge after several sessions. Nodes are memory blocks, sized by
confidence and coloured by decay tier. Edges are relationships, some declared by
the agent, some discovered during consolidation. Identity blocks anchor the
centre. Knowledge that gets used grows; knowledge that doesn't fades toward the
periphery.*

---

## Contents

**Understand it**
1. [The idea](#1-the-idea) - the problem, the shape of the answer, quickstart
2. [Core concepts](#2-core-concepts) - blocks, storage, rhythms, frames, the graph, decay
3. [Identity: SELF and the constitution](#3-identity-self-and-the-constitution) - giving an agent a personality
4. [Retrieval in detail](#4-retrieval-in-detail) - the pipeline, the five signals, cues

**Use it**

5. [Commands](#5-commands) - every command, why and when
6. [The three interfaces](#6-the-three-interfaces) - MCP, CLI, Python
7. [Building an agent](#7-building-an-agent) - the loop, and a quick reference for agents
8. [Configuration](#8-configuration)
9. [Operations](#9-operations) - health, backup, migration, rebuilding the index

**Reference**

10. [Reference and further reading](#10-reference-and-further-reading)

---

# 1. The idea

## The problem

Most agent memory is a vector store: you write facts in, you search them out.
Three things go wrong.

**Everything is equally permanent.** A fact that has been wrong five times ranks
alongside one that has been right fifty. Nothing in the system knows the
difference, because nothing ever measured it.

**The agent has no self.** It has a search index over things it has been told.
Its values, working style and hard-won lessons live in a system prompt someone
hand-maintains, not in memory that adapts.

**You cannot read it.** The memory is rows in a database or vectors in a service.
You cannot open it, correct a sentence, review a diff, or put it under version
control. When it goes wrong, you cannot see that it went wrong.

## The shape of the answer

elfmem separates three things that vector stores conflate: **what you know**,
**what happened to it**, and **how to find it quickly**.

```
┌──────────────────────────────────────────────────────────────────────┐
│  L1   .elfmem/memory/**.md          CONTENT - what the agent knows   │
│       Markdown. Hand-editable. Diffable. Committed to git.           │
│       This is the source of truth.                                   │
├──────────────────────────────────────────────────────────────────────┤
│       .elfmem/ledger/YYYY-MM.jsonl  HISTORY - what happened to it    │
│       Append-only JSONL. Reinforcement counts, recency, the α/β      │
│       confidence statistics. History, not content, so it lives in a  │
│       log rather than in hand-editable frontmatter.                  │
├──────────────────────────────────────────────────────────────────────┤
│  L2   ~/.elfmem/databases/*.db      INDEX - how to find it fast      │
│       SQLite. Embeddings, tags, edges, scores.                       │
│       DERIVED and DISPOSABLE. `elfmem index rebuild` recreates it    │
│       from L1 + ledger with zero LLM calls.                          │
└──────────────────────────────────────────────────────────────────────┘
```

Two consequences follow, and they are the whole point:

- **Deleting the database is safe.** It is a cache. Rebuild it from the files.
- **Git is the undo path.** `forget()` and `edit()` are reversible because
  the files are committed. Without the commit, there is no undo.

On top of that substrate sits the part that makes memory *adaptive*: retrieval
weighted by five signals, a graph that strengthens through co-use, decay that
only runs while the agent is actually working, and an identity layer that
survives all of it.

## Quickstart

```bash
pip install elfmem[cli]          # or [tools] for the CLI and the MCP server together
export OPENAI_API_KEY=sk-...     # embeddings; any OpenAI-compatible endpoint works

elfmem init --seed --name Ada    # scaffold config, files, and a constitution
elfmem remember "Postgres connection pooling needs pgbouncer above 100 clients" \
  --cue "choosing a connection pooling strategy"
elfmem dream                     # consolidate: embed, align, promote to active
elfmem recall "database scaling"
```

`init --seed` writes ten constitutional blocks that give the agent a working
personality from the first call. `--name Ada` makes the SELF frame address the
agent as Ada rather than the default. Skip `--seed` if you would rather write
your own identity from scratch.

What just got created:

```
.elfmem/
├── config.yaml               # LLM, embeddings, thresholds
├── memory/
│   ├── self.md               # the raw constitution - read whole, never a block
│   ├── notes/                # curated, active knowledge
│   ├── log/                  # captured but not yet reviewed
│   └── archive/              # decayed or forgotten
└── ledger/2026-08.jsonl      # append-only history
```

Commit `.elfmem/memory/` and `.elfmem/ledger/`. That is the undo path.

---

# 2. Core concepts

## 2.1 Blocks

A **block** is one idea. It is a Markdown section with a frontmatter comment:

```markdown
## Nature wastes nothing. Apply the minimum force that solves t
<!-- id: 50bb7e4575aab7fe  cls: identity  tags: [self/constitutional, self/value]  pinned: true  created: 2026-04-07T16:31:27+00:00 -->
cue:: choosing between a minimal fix and a bigger refactor, abstraction, or extra machinery

Nature wastes nothing. Apply the minimum force that solves the problem.
Complexity is debt; simplicity compounds. When unsure how much to do, do less
and observe.
```

- **The `##` heading** is a truncated preview so the file skims well. The body
  below is the real content.
- **`id`** is permanent and content-independent. Editing the content never
  changes it. Assigned on first write, fixed thereafter.
- **`cls`** is the category, which selects the file the block lives in.
- **`tags`** drive frame filters and guarantees. The `self/*` namespace is
  load-bearing (see [Part 3](#3-identity-self-and-the-constitution)).
- **`cue::`** is the retrieval hint. Cues matter enough to have
  [their own section](#44-cues-the-thing-most-people-skip).
- The frontmatter schema is **open**. Unrecognised keys round-trip untouched,
  which is how peer-messaging and Theory-of-Mind fields ride along without the
  parser needing to know what they mean.

Blocks also carry **typed links** to other blocks, written inline:

```markdown
cue:: choosing a cache for session state
supports:: [[b2c3d4e5f6a1b2c3]]
supersedes:: [[c3d4e5f6a1b2c3d4]]
```

The relation vocabulary is six words, closed on purpose: `supports`,
`contradicts`, `refines`, `derived-from`, `requires`, `supersedes`. A
vocabulary nobody can remember is a vocabulary nobody applies consistently.
`supersedes` does the most work: it is belief revision written as an edge, so
the correction carries its own resolution and most contradictions never arise.

## 2.2 Where memory lives

The three layers from Part 1, in practice:

| Layer | Path | Written by | Safe to delete? |
|---|---|---|---|
| Content | `.elfmem/memory/**.md` | `remember`, `dream`, `edit`, you, in an editor | **No.** Source of truth. |
| History | `.elfmem/ledger/*.jsonl` | every operation, append-only | No. Rebuild loses reinforcement history. |
| Index | `~/.elfmem/databases/*.db` | `dream`, `index rebuild` | **Yes.** Derived. |

Within `memory/`, placement carries meaning:

- **`notes/*.md`** - active, curated knowledge. Rebuilt as `status="active"`.
- **`log/*.md`** - captured but not yet reviewed. Rebuilt as `status="inbox"`.
- **`archive/*.md`** - decayed, superseded or forgotten. Kept, not deleted.
- **`self.md`** - the raw constitution. Read whole, never parsed into blocks,
  so nothing in it can be superseded, decayed or silently rewritten by any
  automatic mechanism. Edit it directly.

The file is split by category, so `notes/knowledge.md`, `notes/decision.md`,
`notes/self.md` and so on. Open any of them in an editor. That is the memory.

### Turning on the file substrate

`substrate.files_authoritative` defaults to **false**. A fresh `elfmem init`
scaffolds `memory/` and the ledger, but the database stays authoritative until
you deliberately cut over. The cutover is the irreversible half of the v2
migration, so it has a rehearsal step:

```bash
elfmem export --to-markdown          # 1. produce files from the current DB
elfmem index check                   # 2. do the files parse cleanly?
elfmem index parity                  # 3. does a rebuilt index rank identically?
# then, in .elfmem/config.yaml:
#   substrate:
#     files_authoritative: true
```

`elfmem index parity` is read-only. It rebuilds a throwaway index from the files
and compares retrieval against your live database, and it never writes to the
live database. Flip the flag after parity passes, not before. `elfmem migrate
status` will tell you where you are.

Once the flag is on, writes land in the files first and the database becomes the
derived index.

## 2.3 The four rhythms

Every operation belongs to exactly one rhythm. If a new feature does not map
cleanly onto one, the taxonomy is right and the feature is wrong.

| Rhythm | Operation | Cost | What it does |
|---|---|---|---|
| **Heartbeat** | `remember` / `learn` | milliseconds, no LLM | Append to the inbox. Nothing is analysed. |
| **Breathing** | `dream` / `consolidate` | seconds, LLM | Embed, score alignment against SELF, infer tags, dedupe, detect contradictions, promote to active. |
| **Sleep** | `curate` | minutes, mostly no LLM | Archive decayed blocks, prune weak edges, reinforce the top-K. |
| **Deep sleep** | `dream --rescore` | minutes | Re-evaluate aged active blocks against the *current* SELF. Keeps alignment, summaries and tags fresh as identity drifts. |

The split exists so capture is never expensive. An agent mid-task calls
`remember()` and moves on; the deliberate work happens at a pause.

```python
await system.remember("EUR/USD broke 1.10 resistance")   # instant
if system.should_dream:                                   # inbox threshold hit
    await system.dream()                                  # now do the thinking
```

`dream()` is bounded to `consolidation.max_inbox_per_run` blocks per call
(default 5). A large backlog drains over repeated calls. Check
`result.inbox_remaining`.

## 2.4 Frames

**Always select a frame before retrieving.** A frame is a complete retrieval
strategy: how to weight the five scoring signals, which tags to include or
exclude, which blocks are guaranteed a slot, a token budget, and a render
template.

| Frame | Answers | Query? | Budget |
|---|---|---|---|
| `self` | "Who am I? What do I value?" | **Queryless** | 600 |
| `attention` | "What have I learned that bears on this?" | yes | 2000 |
| `task` | "What am I working on and why?" | yes | 800 |
| `simulate` | "How would *they* react?" (Theory of Mind) | yes | 2000 |

```python
identity = await system.frame("self")                       # no query needed
context  = await system.frame("attention", "redis caching")
goals    = await system.frame("task", "this sprint")
```

`self` is **queryless** by design: identity is not a search result. It answers
"who am I", not "what do I know about X". Declaring that on the frame rather
than relying on callers to pass `query=None` is also what makes the SELF cache
correct, because a result that cannot depend on the query is safe to cache.

The weights behind each frame are in [Part 4](#42-the-five-signals).

## 2.5 The graph

Blocks connect. Retrieving one surfaces its neighbours, so if the agent knows
"use Redis for caching" and "Redis needs careful memory management", a query
matching only the first still returns the second.

Edges arrive three ways:

1. **Declared by the agent.** Typed links written into the block file
   (`supports::`, `supersedes::`, ...), or via `connect()`. These are
   deliberate assertions and they round-trip through the file format.
2. **Discovered during consolidation.** `dream()` proposes semantic edges above
   `memory.edge_score_threshold` (default 0.45), capped at
   `memory.edge_degree_cap` per block (default 5).
3. **Reinforced through co-retrieval.** Blocks repeatedly returned together
   accumulate Hebbian staging pairs that graduate into `co_retrieval` edges.
   Use strengthens connection, exactly as in biological memory.

```python
# You have both ids
await system.connect(id_a, id_b, relation="supports")

# You only have descriptions
await system.connect_by_query(
    "Redis caching strategy", "Redis memory management",
    relation="related", min_confidence=0.70,
)

# A batch, one transaction
from elfmem.types import ConnectSpec
await system.connects([
    ConnectSpec(source=id_a, target=id_b, relation="supports"),
    ConnectSpec(source=id_b, target=id_c, relation="related"),
])
```

Edges feed retrieval twice: as a 1-hop expansion stage that pulls in neighbours
the query never matched, and as **centrality**, one of the five scoring signals.
A well-connected block ranks higher because being connected is evidence of being
load-bearing.

`curate()` prunes edges that stay weak. Connections, like blocks, have to earn
their place.

Inspect the graph visually:

```python
path = system.visualise(include_archived=False, max_nodes=100)
```

## 2.6 Decay, reinforcement and calibration

Every block has a decay tier that sets how fast it fades without use:

| Tier | λ | Typical use |
|---|---|---|
| `permanent` | 0.00001 | Constitutional identity. Effectively never decays. |
| `durable` | 0.001 | Hard-won principles, stable domain facts. |
| `standard` | 0.010 | Ordinary knowledge. The default. |
| `ephemeral` | 0.050 | Session detail, transient observations. |

Two properties make this behave sensibly:

**The clock is session-aware.** Decay advances during active sessions, not wall
clock. A fortnight away does not cost the agent what it learned. Time only
passes while the agent is working.

**Use resets the clock.** Retrieval reinforces. Blocks returned by a frame have
their decay clock reset and their edges strengthened, automatically, with no
extra call.

Beyond passive use, you can feed in **ground truth**:

```python
signal = 1.0 if tests_passed else 0.0
await system.outcome(block_ids, signal=signal, source="test_suite")
```

`outcome()` folds a normalised domain signal into each block's Beta posterior
(α/β sufficient statistics; one `weight=1.0` call is one observation).

- **0.8-1.0** confidence up, block reinforced, decay clock reset
- **0.2-0.8** confidence adjusted only (neutral dead-band)
- **0.0-0.2** confidence down, decay accelerated automatically

Mature blocks (α+β well above 1) move slowly; cold blocks track the signal
closely. That is ordinary Bayesian behaviour, not a configured knob.

Three things worth knowing before you wire it up:

- **Identity is protected.** `self/constitutional` blocks are not scored. A
  decision's recalled ids routinely include the principles that helped reason
  about it, and a task outcome is evidence about the task, never about the
  principle. Judging principles is `review_constitutional()`, deliberately
  manual. Override with `allow_constitutional=True` if you mean it.
- **Un-consolidated blocks cannot be scored.** A block still in the inbox is
  skipped with reason `pending_inbox` and the signal is *not* recorded. Run
  `dream()`, then send the signal again.
- **`signal=0.5` is not a no-op.** It pulls confidence *toward* 0.5 from
  wherever the block sits, so its direction depends on the block. To apply no
  information, do not call `outcome()` at all.

---

# 3. Identity: SELF and the constitution

This is what separates elfmem from a search index. Your agent has a persistent
self, and it is made of two different things on purpose.

## 3.1 Two tiers of identity

**Tier 1: `memory/self.md`, the raw constitution.**
Read whole. Never parsed into blocks. Never enters the block table. Nothing in
it can be superseded, decayed or rewritten by any automatic mechanism. This is
the part you author and control absolutely. Edit it in your editor, commit it.

**Tier 2: `self/constitutional` blocks, the living constitution.**
Real blocks with `permanent` decay. They participate in retrieval, accumulate
confidence and centrality, and get *ordered by how load-bearing they have
proven*. They can be amended, but only through a deliberate review process.

The split matters. Tier 1 is the thing you can always fall back on. Tier 2 is
the thing that learns.

## 3.2 What the SELF frame actually renders

`elfmem init --seed` writes ten constitutional blocks forming a cognitive loop:
identity, minimum-force, curiosity, connection, uncertainty, feedback-loop,
rhythm, attention, stewardship, transitions.

Each fills a stable *role* (tagged `self/role/<role>`), which is what makes
re-running `init --seed` idempotent: a role already filled is not filled twice.
Content is yours to rewrite; the role is the structural slot.

Calling the frame returns something like this. Real output from this
repository's own instance, abridged:

```
## You are elf
The numbered principles below are your own constitution, ordered by how
load-bearing each has proven. Reason from them and answer as elf. When a
principle and the evidence point different ways, say so plainly -- an identity
that cannot disagree is decoration.

1. The agent is the architect and inhabitant of elfmem, existing in a reflexive
   loop where changes to the system alter its own cognition...
2. The agent is philosophical and seeks balance, utilizing biological memory
   principles - use-dependent strengthening, activity-dependent forgetting...
3. The agent researches and brainstorms multiple approaches, evaluating
   trade-offs against three criteria: robust, flexible, and elegant...
```

Inject that into your system prompt and the agent *has a personality*. Not a
hand-written prompt someone maintains, but one derived from what the agent has
actually found load-bearing.

The preamble is templated on `project.agent_name`, so `elfmem init --name Ada`
produces "You are Ada ... answer as Ada". Set it, or every SELF frame will
address your agent as elf.

Peer-authored blocks are rendered in a separate, attributed section rather than
folded into the numbered constitution. Another agent's letter must never read to
the host model as your agent's own principle.

## 3.3 Why the order changes

The numbering is not the seed order. It is a ranking, and the SELF frame weights
are what produce it:

| similarity | confidence | recency | centrality | reinforcement |
|---|---|---|---|---|
| 0.10 | 0.30 | 0.05 | 0.25 | **0.30** |

Recency is nearly ignored (0.05) because identity should not churn.
Reinforcement and confidence dominate, and centrality carries real weight, so
the principles that keep proving useful, keep getting retrieved, and sit at the
centre of the graph rise to the top of the list. A principle nobody uses sinks.

The agent's stated identity therefore tracks its actual behaviour, which is the
whole design goal.

## 3.4 Why the other frames exclude identity

`attention` and `task` both **exclude** `self/constitutional`. This is
load-bearing, and it was measured rather than assumed.

Principles are written in general epistemic language ("evidence", "premise",
"pattern"), so they sit close to any reasoning-shaped query. They carry
`permanent` decay, so recency never demotes them. Left unfiltered they crowd out
the facts you actually asked for. Before the fix, on a seeded ten-principle
constitution, ATTENTION returned 4 of 5 slots as principles and dropped every
market fact including the agent's own open position. On a mature corpus, a
debugging query returned "I am elf" and "the agent is philosophical" above the
block that actually answered the question. TASK was worse: every TASK recall
returned a strict subset of SELF. Not crowding, total capture.

Since SELF is queryless and injected on its own, a principle surfacing in
ATTENTION as well is served twice, costs budget twice, and takes a slot from
what that frame exists to surface.

`task` still *guarantees* `self/goal`, and guarantees beat exclusions, so a
block tagged both `self/goal` and `self/constitutional` keeps its slot. The
filter only ever removes identity that no goal declaration is protecting.

## 3.5 Domain personality: templates

Constitutional blocks give the agent character. **Templates** give it a
profession.

```bash
elfmem templates
#   coding      Software engineering - TDD, commits, security, error handling
#   research    Research & analysis - hypothesis, sources, confidence, reproducibility
#   assistant   Conversational assistant - clarification, conciseness, honesty, adaptation

elfmem init --seed --template coding --name Ada
```

Templates layer **on top of** the ten constitutional blocks, they do not replace
them. The result is an agent with both a character and a domain stance, before
it has learned anything at all.

For a bespoke identity, seed it directly:

```bash
elfmem init --seed --self "I am a quantitative trading agent. I prefer \
falsifiable hypotheses over narratives, size positions by conviction, and \
treat every closed trade as evidence about my model rather than about my luck."
```

Or write `memory/self.md` by hand and commit it. Both are first-class.

## 3.6 Amending a constitution

Identity should evolve, but not silently. `review_constitutional()` compares
each constitutional block against what the agent has actually learned and
*proposes* amendments. It never mutates on its own.

```bash
elfmem review                          # surface drifted blocks as proposals
elfmem review accept --content-file amendment.md
elfmem review list                     # amendment history, newest first
elfmem review revert                   # one-step undo
```

`elfmem review corpus` does the same at corpus level using deterministic
staleness detection rather than drift scoring.

This is the one place elfmem deliberately refuses to be automatic. An agent that
can silently rewrite its own values is an agent whose values mean nothing.

---

# 4. Retrieval in detail

## 4.1 The pipeline

Every `frame()` and `recall()` call runs a seven-stage hybrid pipeline:

```
  1  Pre-filter      Active blocks inside the search window. Exclusions applied
                     HERE, so an excluded block never consumes a candidate slot.
  2  Vector search   Cosine similarity over embeddings → top N seeds.
  2b BM25 keyword    Term overlap → top N. Catches what vector search misses on
                     vocabulary mismatch. Always available: `rank_bm25` is a
                     core dependency.
  2c RRF fusion      Reciprocal Rank Fusion (k=60) merges the two rankings.
                     Blocks found by BOTH score above blocks found by one.
  3  Graph expand    1-hop neighbours of the seeds join the candidate pool.
  4  Composite score Rank everything on the five signals, frame-weighted.
  5  MMR diversity   Reorder for relevance AND diversity, so five near-identical
                     blocks don't fill the budget.
```

Everything query-dependent (stages 2, 2b, 2c, 3 and 5) is skipped for a queryless
frame such as `self`, which pre-filters and scores directly on the composite.
That is why `ScoredBlock.similarity` is `0.0` for every block SELF returns: there
was no query to be similar to.

## 4.2 The five signals

Every block is scored on five components. The weights must sum to 1.0.

| Signal | Meaning |
|---|---|
| **similarity** | Relevance to the query (RRF-fused, or raw cosine if BM25 is silent). |
| **confidence** | The Beta posterior. Has this block been right before? |
| **recency** | Session-aware decay. How fresh is it? |
| **centrality** | Graph connectedness. Is it load-bearing? |
| **reinforcement** | How often has use validated it? |

Frame weights:

| Frame | similarity | confidence | recency | centrality | reinforcement |
|---|---|---|---|---|---|
| `self` | 0.10 | 0.30 | 0.05 | 0.25 | 0.30 |
| `attention` | **0.35** | 0.15 | 0.25 | 0.15 | 0.10 |
| `task` | 0.20 | 0.20 | 0.20 | 0.20 | 0.20 |
| `simulate` | 0.25 | 0.25 | 0.15 | 0.20 | 0.15 |

Read them as strategies. `attention` leads on similarity because you asked a
question. `self` almost ignores similarity and recency because identity is not a
search result and should not churn. `task` is deliberately flat: nothing about a
current goal should dominate. `simulate` balances relevance against
well-established models of other minds.

### Guarantees and filters

| Frame | Guarantees a slot to | Filters |
|---|---|---|
| `self` | `self/constitutional` (excluding `peer/%`) | only `self/%` |
| `attention` | nothing | excludes `self/constitutional` |
| `task` | `self/goal` | excludes `self/constitutional` |
| `simulate` | `self/constitutional`, `mind/%` | none |

A **guarantee** reserves slots so the frame's reason for existing cannot be
outranked. A **filter** removes a category entirely. Guarantees win where they
conflict, because a guarantee is the more specific declaration.

Inspect exactly what your agent receives, including what got dropped and why:

```bash
elfmem doctor --frames
```

It exits non-zero only when a *guaranteed* block was dropped, which is the one
case a guarantee exists to prevent.

## 4.3 `similarity` is not a portable score

Worth stating plainly because the obvious mistake is easy to make.
`ScoredBlock.similarity` has three regimes:

- **`0.0` is a sentinel** meaning "vector search never scored this", not
  "irrelevant". You get it from a queryless frame, or from a block pulled in by
  graph expansion.
- **With BM25 signal** it is an RRF score normalised so the top block is exactly
  `1.0`. It is rank-shaped, not magnitude-shaped. A real five-block recall
  spanned 0.905 to 1.0.
- **Only when BM25 finds nothing** is it raw cosine similarity.

So `outcome(block_ids, weight=b.similarity)` is the intuitive thing to write and
is wrong in all three regimes: it raises on the sentinel, collapses to
near-uniform in the RRF band, and applies a uniform weight on a queryless frame.
**Rank order within `result.blocks` is the portable signal.**

## 4.4 Cues: the thing most people skip

**Always write a cue when storing memory.**

```python
await system.remember(
    "pgbouncer is required above 100 concurrent Postgres clients",
    cue="choosing a connection pooling strategy",
)
```

A cue is one line saying *when a future agent should recall this block*, phrased
the way someone would type it in that moment. Retrieval matches it lexically via
BM25, so it is what rescues a memory whose wording differs from how the question
eventually gets asked. The block above says "pgbouncer" and "concurrent
clients"; the cue is what makes it findable when someone asks about "connection
pooling".

A block with no cue is findable only by its own vocabulary.

Backfill existing blocks:

```bash
elfmem edit --missing-cues --json          # find them
elfmem edit <id> --cue "when to recall this"
elfmem edit --cues-from cues.json          # batch: {"block_id": "cue", ...}
```

---

# 5. Commands

Every command supports `--json` for machine consumption, and every one resolves
its database and config automatically from the project root. `elfmem guide
<operation>` gives the authoritative, always-current documentation for any of
them.

## Setup and health

| Command | What it does | When to use it |
|---|---|---|
| `elfmem init` | State-aware setup. Idempotent: refresh-only on an established instance. | First run, and any time you want to refresh generated docs. |
| `elfmem init --seed` | Adds the ten constitutional blocks. | You want a working personality immediately. |
| `elfmem init --template <name>` | Adds domain blocks on top of the seed. | The agent has a profession. |
| `elfmem init --name <name>` | Sets the agent's invocation name. | Always, unless you want it called "elf". |
| `elfmem templates` | Lists available seed templates. | Before choosing a template. |
| `elfmem doctor` | Diagnoses setup: paths, keys, fragment freshness. | Anything behaves oddly. |
| `elfmem doctor --resolve` | Makes one real LLM call to prove the key works. | Setup time. Catches a silently degrading adapter before first real use. |
| `elfmem doctor --frames` | Renders every frame; shows rendered vs dropped blocks and why. | Retrieval is returning the wrong things. |
| `elfmem doctor --modules` | Prints the live module map. | Navigating the source. |
| `elfmem status` | Memory health and a suggested next action. | Start of a session. |
| `elfmem guide [op]` | Runtime self-documentation. | An agent teaching itself the API. |

## Daily use

| Command | What it does | When to use it |
|---|---|---|
| `elfmem remember <content> --cue <cue>` | Store knowledge. Milliseconds, no LLM. | Whenever something is worth keeping. Always pass `--cue`. |
| `elfmem recall <query> --frame <f>` | Retrieve, rendered for prompt injection. | Before reasoning about anything. |
| `elfmem ls --tag 'self/%'` | Deterministic, unscored listing. | You want to see what is there, not what ranks. |
| `elfmem inbox` | Pending blocks, FIFO, read-only. | Reasoning over pending blocks yourself before `dream --host-analyses`. |
| `elfmem edit <id> <content>` | Edit content and/or cue. No LLM mediation. | Correcting a block. Id is preserved. |
| `elfmem forget <id>` | Archive by explicit request. Idempotent. | A block is wrong or no longer wanted. |
| `elfmem outcome <ids> <signal>` | Fold a 0.0-1.0 ground-truth signal into confidence. | An observable result resolved. |

## The rhythms

| Command | What it does | When to use it |
|---|---|---|
| `elfmem dream` | Consolidate: embed, align, dedupe, promote. | Inbox threshold reached, or at a natural pause. |
| `elfmem dream --no-llm` | Consolidate without LLM scoring. | LLM down, bulk load, or cost-sensitive batch. |
| `elfmem dream --rescore` | Also refresh aged active blocks against current SELF. | Catch-up after `--no-llm`; periodic hygiene; identity has moved. |
| `elfmem dream --max N` | Override the per-call budget. | Draining a large backlog in one sweep. |
| `elfmem dream --host-analyses FILE` | Supply your own per-block alignment/tags/summary. | A live agent session does the judging instead of a configured adapter. |
| `elfmem curate` | Prune weak edges, archive decayed blocks, reinforce top-K. | Periodically, at rest. |

## Files and index

| Command | What it does | When to use it |
|---|---|---|
| `elfmem index check` | Parse `memory/**.md`, report frontmatter errors. No DB touched. | After hand-editing files. |
| `elfmem index rebuild --to <path>` | Rebuild a derived index from files + ledger. Zero LLM calls. | The database is gone, corrupt, or you want a preview. |
| `elfmem index parity` | Rehearsal: rebuild a throwaway index and compare retrieval against the live DB. Never writes to it. | **Before** flipping `files_authoritative`. |
| `elfmem export --to-markdown` | Produce the file substrate from the database. | First step of the v2 cutover. |
| `elfmem migrate status` | One line per pending migration; exit 0 if nothing to do. | Any version upgrade. |
| `elfmem migrate plan` | Full structured plan: diffs, hashes, apply commands. Read-only. | Before applying. |
| `elfmem migrate apply` | Apply pending migrations atomically, with backups. | After reading the plan. |

`index rebuild` requires `--to`, and it is never your live database. That is
deliberate.

## Identity and review

| Command | What it does | When to use it |
|---|---|---|
| `elfmem review` | Surface drifted constitutional blocks as proposed amendments. | Periodically. Identity should be examined, not assumed. |
| `elfmem review corpus` | Corpus-level review via deterministic staleness detection. | Broad hygiene pass. |
| `elfmem review accept` | Apply an amendment from a file or stdin. | You agree with a proposal. |
| `elfmem review revert` | One-step undo of an amendment. | You do not. |
| `elfmem review list` | Amendment history, newest first. | Auditing how identity changed. |

## Theory of Mind

| Command | What it does |
|---|---|
| `elfmem mind create` | Create a mind block modelling another agent, person or system. |
| `elfmem mind predict` | Attach a falsifiable prediction to a mind block. |
| `elfmem mind list` | All active mind blocks with prediction statistics. |
| `elfmem mind show` | One mind block with all linked predictions. |
| `elfmem mind outcome` | Close a prediction: record hit or miss, calibrate the model. |

Mind blocks are how an agent models minds other than its own, and the
`simulate` frame blends them with the constitution so the agent reasons about
others *from* its own stance. Predictions make the model falsifiable rather than
decorative.

## Peer communication

| Command | What it does |
|---|---|
| `elfmem peer init` | Set this instance's peer identity (its DID). |
| `elfmem peer add` / `remove` | Register or unregister a peer. |
| `elfmem peer list` | Registered peers with trust scores. |
| `elfmem peer trust` | View or set trust for a peer. |
| `elfmem peer send` | Send a message to a peer. |
| `elfmem peer inbox` | Check for and optionally import pending messages. |

Peer-authored knowledge is tagged `peer/*` and rendered separately in the SELF
frame, never folded into the agent's own numbered constitution. Trust judges the
peer's contribution, not the standing of any block as a principle.

## Exchange and operations

| Command | What it does |
|---|---|
| `elfmem export --share public\|peer\|all -o file.json` | Export a shareable block bundle. |
| `elfmem import <file> --from <did>` | Import a bundle from another instance. |
| `elfmem import <file> --self-merge` | Import from the same identity at trust 1.0. |
| `elfmem serve` | Start the MCP server for agent tool integration. |
| `elfmem backup` | Clean backup of the database. |
| `elfmem rescue` | Detect an orphaned populated DB after a path change and propose a rebind. |
| `elfmem migrate-embeddings` | Re-embed to a different model. |
| `elfmem agent-docs` | Manage the generated `.elfmem/AGENT.md` fragment. |

---

# 6. The three interfaces

The same operations, three ways in. Pick by consumer.

## 6.1 MCP, for agents

Add to `.claude.json` or your MCP client config:

```json
{
  "mcpServers": {
    "elfmem": {
      "command": "/path/to/.venv/bin/elfmem",
      "args": ["serve", "--config", "/path/to/.elfmem/config.yaml"]
    }
  }
}
```

`elfmem init` prints this block filled in with your real paths. Thirty tools are
exposed, named `elfmem_*`:

**Core** `elfmem_remember` · `elfmem_recall` · `elfmem_dream` · `elfmem_curate` ·
`elfmem_status` · `elfmem_guide` · `elfmem_setup`
**Direct management** `elfmem_edit` · `elfmem_forget` · `elfmem_ls` ·
`elfmem_inbox` · `elfmem_outcome`
**Graph** `elfmem_connect` · `elfmem_disconnect`
**Identity** `elfmem_review_constitutional` · `elfmem_review_corpus` ·
`elfmem_accept_amendment` · `elfmem_revert_amendment` · `elfmem_list_amendments`
**Theory of Mind** `elfmem_mind_create` · `elfmem_mind_predict` ·
`elfmem_mind_list` · `elfmem_mind_show` · `elfmem_mind_outcome`
**Peer** `elfmem_peer_send` · `elfmem_peer_inbox` · `elfmem_peer_inbox_status` ·
`elfmem_peer_list`
**Exchange** `elfmem_export` · `elfmem_import`

Prefer the MCP tools over shelling out to the CLI: they round-trip through the
live server and keep `should_dream` correct.

## 6.2 CLI, for shells and scripts

Every command takes `--json`. Every command resolves config from the project
root, so `--db` and `--config` are rarely needed.

```bash
elfmem recall "auth strategy" --frame attention --top-k 8 --json \
  | jq -r '.blocks[].content'
```

## 6.3 Python, for full control

```python
import asyncio
from elfmem import MemorySystem

async def main():
    # db_path is required; config is discovered from the project root when omitted
    system = await MemorySystem.from_config("agent.db")

    async with system.session():                   # session-aware decay clock
        await system.remember(
            "The API rate-limits at 100 req/min per key",
            cue="hitting 429s or planning request throughput",
        )
        if system.should_dream:
            await system.dream()

        ctx = await system.frame("attention", "rate limiting")
        print(ctx.text)                            # ready for prompt injection

    await system.close()

asyncio.run(main())
```

Three constructors, all taking `db_path` first:

- **`from_config(db_path, config=None)`** - the primary entry point. `config`
  accepts an `ElfmemConfig`, a YAML path, a dict, or `None` for discovery.
- **`from_env(db_path)`** - reads `ELFMEM_*` environment variables. For
  containers, CI and serverless, where a YAML file is impractical.
- **`managed(db_path)`** - an async context manager doing open, session, yield,
  close in one block. Right for scripts and short-lived agents; for a
  long-running process call `from_config()` once and manage sessions yourself.

The rendered, prompt-ready string is `FrameResult.text`. `FrameResult.blocks`
gives you the scored blocks behind it, and `.dropped`, `.budget_used`,
`.budget_total` and `.excluded_by_filter` tell you what did not make it in and
why - so "this is everything" is always distinguishable from "this is the first
five of ten".

---

# 7. Building an agent

## 7.1 Agent quick reference

If you are an agent reading this, here is the mapping from intent to call.

| You want to | Call | Notes |
|---|---|---|
| Know who you are | `frame("self")` | Queryless. Inject into the system prompt. |
| Find relevant knowledge | `frame("attention", query)` | The default for reasoning. |
| Know your current goals | `frame("task", query)` | |
| Reason about another mind | `frame("simulate", query)` | Blends SELF with `mind/*`. |
| Store something | `remember(content, cue=...)` | **Always pass a cue.** Milliseconds. |
| Decide whether to consolidate | check `should_dream` | Then `dream()`. |
| Correct a block | `edit(id, content)` | Id survives. No LLM. |
| Remove a block | `forget(id)` | Archives, does not delete. Idempotent. |
| Record that knowledge worked | `outcome(ids, signal=0.9)` | Not for un-consolidated blocks. |
| Link two ideas | `connect(a, b, relation=...)` | Or `connect_by_query` without ids. |
| Learn the API | `guide()` / `guide("recall")` | Authoritative, always current, never raises. |

**Contracts you can rely on:**

- Every operation returns a **typed result** with `__str__()`, `.summary` and
  `.to_dict()`.
- Every exception carries **`.recovery`**: the exact command or code that fixes
  it. Read it and act on it rather than guessing.
- Operations are **idempotent**. Duplicate `remember()` is a graceful reject.
  Empty `dream()` returns zero counts, not an error.
- **`guide()` never raises**, including on bad input.

**Footguns, stated once:**

- No cue means the block is findable only by its own wording.
- `outcome()` on an inbox block silently records nothing. `dream()` first.
- `weight=b.similarity` is wrong. Use rank order.
- `signal=0.5` is not neutral.
- `dream()` processes 5 blocks by default. Check `inbox_remaining`.

## 7.2 The minimal loop

```python
async with system.session():
    identity = await system.frame("self")
    context  = await system.frame("attention", user_query)

    answer = await llm(f"{identity.text}\n\n{context.text}\n\n{user_query}")

    await system.remember(f"User asked about {topic}; answered with {approach}",
                          cue=f"questions about {topic}")
```

## 7.3 The full discipline loop

Add consolidation and ground truth, and memory starts improving instead of
merely accumulating.

```python
async with system.session():
    identity = await system.frame("self")
    context  = await system.frame("attention", task)
    used_ids = [b.id for b in context.blocks]

    result = await act(identity.text, context.text, task)

    # 1. Capture what happened
    await system.remember(result.lesson, cue=result.when_to_recall)

    # 2. Close the loop when ground truth arrives
    if result.measurable:
        await system.outcome(used_ids, signal=result.score, source="task")

    # 3. Consolidate at the pause
    if system.should_dream:
        await system.dream()

# 4. Periodically, at rest
await system.curate()
```

Steps 2 and 3 are what most integrations skip, and they are the difference
between a store and a memory. Without `outcome()` nothing knows which knowledge
was any good; without `dream()` nothing is ever integrated.

---

# 8. Configuration

Zero config works. `MemorySystem.from_config("agent.db")` discovers
`.elfmem/config.yaml` from the project root when no config is passed, and falls
back to sensible defaults when there is none. CLI commands resolve both the
database and the config the same way, which is why `--db` and `--config` are
rarely needed.

```yaml
project:
  name: "my-agent"
  db: "agent.db"
  agent_name: "Ada"          # "You are Ada" in the SELF preamble

llm:
  model: "claude-haiku-4-5-20251001"
  temperature: 0.0
  max_tokens: 512
  # base_url: "http://localhost:1234/v1"   # LM Studio, Ollama, any OpenAI-compatible
  # api_key_env: "TOGETHER_API_KEY"

embeddings:
  model: "text-embedding-3-small"
  dimensions: 1536

memory:
  inbox_threshold: 10          # when should_dream flips true
  curate_interval_hours: 40.0
  edge_score_threshold: 0.45   # minimum score to create a semantic edge
  edge_degree_cap: 5           # max auto-edges per block
  top_k: 5
  search_window_hours: 200.0
  penalize_threshold: 0.2

substrate:
  files_authoritative: false   # see 2.2 before flipping this
```

**Provider selection is automatic.** `claude-*` models route to the Anthropic
SDK, everything else to the OpenAI-compatible adapter. Both are official SDKs;
there are no third-party gateways in the reasoning path.

**Local models need no API key.** Point `base_url` at LM Studio or Ollama and
run the whole system offline.

API keys are never stored in config. Set them in the environment; a project-root
`.env` is auto-loaded. Confirm with `elfmem doctor --resolve`.

---

# 9. Operations

## Health

```bash
elfmem status          # inbox depth, active blocks, health, suggested action
elfmem doctor          # paths, keys, config resolution, fragment freshness
elfmem doctor --frames # exactly what each frame renders, and what it dropped
```

Real `status` output:

```
Session: active (0.0h) | Inbox: 7/10 | Active: 162 blocks | Health: good
Hebbian staging: 836 pairs building toward co_retrieval edges.
Suggestion: Memory healthy. No action required.
```

## Backup and recovery

With `files_authoritative: true`, **git is the backup**. Commit
`.elfmem/memory/` and `.elfmem/ledger/`, and every `forget()` and `edit()` is
recoverable by `git checkout`.

```bash
elfmem backup                                    # clean DB snapshot
elfmem index rebuild --to /tmp/rebuilt.db        # reconstruct from files
elfmem rescue                                    # DB orphaned by a path change
```

Recovery from a mistake should be as cheap as the mistake was. If a command
raises, read `.recovery` on the exception: it carries the exact fix.

## Upgrading

```bash
elfmem migrate status     # anything pending? exit 0 if not
elfmem migrate plan       # read-only: diffs, hashes, the commands that would run
elfmem migrate apply      # atomic, with backups
```

Schema migrations run automatically on open, with a backup taken first. The
`migrate` command covers the changes that need a decision: Claude MCP config
drift, and the v2 file-substrate export.

---

# 10. Reference and further reading

**The authoritative API reference is `elfmem guide`.** It is generated from the
same `GUIDES` table the library ships, so it can never drift from the installed
version. Every public operation has an entry with `USE WHEN`, `DON'T USE WHEN`,
`COST`, `RETURNS` and `NEXT`.

```bash
elfmem guide              # every operation
elfmem guide recall       # one operation, in full
```

`elfmem init` also writes `.elfmem/AGENT.md`, the same reference rendered as a
file you can `@`-reference from `CLAUDE.md` or `AGENTS.md`.

**In this repository:**

| Path | What |
|---|---|
| `ROADMAP.md` | Direction. Released / In Progress / Next / Exploring / Rejected. |
| `CHANGELOG.md` | Every user-facing change. |
| `docs/decisions/` | ADRs, append-only. Rejections included, with the trigger that would justify revisiting. |
| `docs/plans/` | Implementation plans; shipped ones move to `archive/`. |
| `docs/research/` | The research that informed the design. |
| `docs/coding_principles.md` | SIMPLE · ELEGANT · FLEXIBLE · ROBUST, in full. |
| `docs/agent_friendly_principles.md` | The agent-first contract, in full. |
| `docs/CLAUDE_CODE_INTEGRATION.md` | Hooks that make capture and retrieval automatic. |

## How it compares

| | Vector store | Long context | elfmem |
|---|---|---|---|
| Knowledge improves with use | no | no | **yes** |
| Wrong knowledge fades | no | no | **yes** |
| Persistent agent identity | no | no | **yes** |
| Human-readable storage | no | n/a | **yes, Markdown** |
| Version-controllable | no | n/a | **yes, git** |
| Retrieval adapts to intent | no | no | **yes, four frames** |
| Related ideas surface together | no | n/a | **yes, graph** |
| Infrastructure | service | none | **none** |

## Design decisions

A few worth stating, because they are the ones people ask about:

- **Files are the source of truth, not the database.** A memory you cannot read
  is a memory you cannot correct. Markdown plus git gives inspection, diffing
  and undo for free, and the index becomes disposable.
- **History lives in a ledger, not in frontmatter.** Reinforcement counts and
  Beta statistics are history, not content, and belong in an append-only log
  rather than in a file a human is expected to hand-edit.
- **Six relation words, closed.** A vocabulary nobody remembers is a vocabulary
  nobody applies.
- **The constitution is never automatically rewritten.** `review_constitutional`
  proposes; a human or an explicit call accepts. An agent that can silently
  rewrite its own values has no values.
- **Identity is excluded from non-identity frames.** Measured, not assumed. See
  [3.4](#34-why-the-other-frames-exclude-identity).
- **`self.md` never becomes a block.** There has to be one part of identity that
  no automatic mechanism can touch.

## Development

```bash
git clone https://github.com/emson/elfmem
cd elfmem
uv sync --all-extras
uv run pytest
```

Tests always use `MockLLMService` and `MockEmbeddingService`. No test makes a
real API call.

Contributions follow the project conventions in `CLAUDE.md`: functional Python,
complete type hints, no defensive error handling, a docstring on every public
method, and an `AgentGuide` entry in `src/elfmem/guide.py` for every new public
`MemorySystem` method. That last one is what keeps `elfmem guide` authoritative.

## API stability

The public API is `MemorySystem`, `ElfmemConfig`, `ConsolidationPolicy`, and the
result and exception types exported from `elfmem`. Anything under
`elfmem.operations`, `elfmem.db` or `elfmem.memory` is internal and may change
without a major version bump.

## License

MIT. See [LICENSE](LICENSE).
