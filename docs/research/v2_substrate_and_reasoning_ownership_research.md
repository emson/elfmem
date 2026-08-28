# elfmem v2 — Substrate, Retrieval, and Reasoning-Ownership Research

**Status**: research document — under review, not yet a plan
**Author**: elf (Ben Emson, with Claude collaboration)
**Date**: 2026-08-08
**Driver**: four compounding operational problems reported directly by the maintainer —
(1) memory blocks drift and cannot be directly edited or removed, (2) LLM consolidation
cost feels disproportionate to the value delivered, (3) config/env-var resolution has
caused repeated production incidents, (4) fresh installs seed unwanted content that is
hard to remove. The question behind all four: is SQLite-backed block consolidation the
right substrate at all, or should elfmem look more like plain markdown documents — and if
neither extreme is right, what is?

**Companion document**: [`docs/plans/plan_v2_substrate_reevaluation.md`](../plans/plan_v2_substrate_reevaluation.md)
— an earlier, narrower pass over the same evidence, written before the external research
sweep (§4) and the reasoning-ownership simulation (§5) below. This document supersedes it
as the entry point; the plan doc's block-format and migration detail still stand and are
referenced rather than repeated.

---

## Abstract

We audited the live production instance of elfmem (the maintainer's own dev database,
2026-04-07 to 2026-06-21) and found that its LLM-mediated consolidation pipeline spends a
**52x token amplification** — 1.65M input tokens across 2,903 LLM calls — to maintain a
31.6k-token active corpus, while the mechanism most responsible for removing content
(near-duplicate supersession) silently destroyed 6 of 10 constitutional identity blocks
over four months with no audit trail, no guard, and no undo. The decay/archival system
built to *intentionally* remove stale content accounts for exactly one of the 42 archivals
observed; the other 41 were this same silent-overwrite mechanism.

We ran two sequential optimize-style simulations. The first (§3) tests the substrate
question — SQLite-only vs. plain markdown vs. a hybrid — against a ten-scenario frozen
set and converges on **git-versioned markdown files as the source of truth, with SQLite
demoted to a derived, disposable, zero-LLM-cost index**. The second (§5), prompted by
reviewing a sibling project's already-shipped design (`ctx`, a Claude Code Skill for
markdown-vault context curation), tests where *reasoning* should live and converges on
**splitting elfmem into a deterministic index plus a small set of Claude Code Skills that
borrow the host agent's own model instead of elfmem managing its own LLM credentials**,
with elfmem's own LLM gateway demoted to an optional path for the one case that still
needs it — unattended, headless operation with no host agent present.

An external research sweep (§4) independently corroborates the substrate direction (three
unrelated 2026 systems converge on the same files-are-truth/index-is-derived shape) and
the cost-reduction direction (a published paper measures the same write-time-to-corpus-
level-review trade we propose, and it wins on quality *and* cost, not one for the other).
It also surfaces one genuinely open problem the field has not solved — content identity
that survives file edits, not just moves — which we resolve with a design already half-
present in elfmem's existing code.

We close with a combined target architecture, a deletion list, a non-destructive migration
path, and eleven open decisions that are the maintainer's to make, not ours.

---

## 1. Evidence from the live instance

All figures below are computed directly from `~/.elfmem/databases/elfmem.db`, not
estimated. Full computation detail lives in the companion plan document §2; this section
gives the figures load-bearing for the decisions in §3 and §5.

### 1.1 Cost

| Metric | Value |
|---|---|
| Lifetime LLM calls | 2,903 |
| Lifetime LLM input tokens | 1,646,801 |
| Blocks ever created / active now | 185 / 140 |
| Live corpus size | ≈31,633 tokens |
| LLM calls per surviving block | 20.7 |
| Input tokens per corpus token | **52.1x** |

At the ~14s/call latency the project's own `config.yaml` and ADR 0007 cite for local
adapters, 2,903 calls is roughly 11 hours of compute to curate 32k tokens of text.

### 1.2 Decay is arithmetically inert; supersession does all the work

```
archive_reason | count
---------------+------
superseded     |   41
decayed        |    1
```

The four-tier decay model exists to retire stale content gracefully over time. In four
months of real use it retired one block. The session-aware activity clock reads 3.69 total
active hours across 370 sessions (36s average) — recency at the STANDARD decay tier is
still 0.96 after the instance's entire lifetime. Whatever reshapes this memory, it is not
decay.

### 1.3 The constitution eroded, and the mechanism is identified in code

6 of 10 seeded constitutional role slots (`self/role/<name>`) are gone; the 4 survivors
hold different content than was seeded. The live mechanism is
`operations/consolidate.py`'s `_collect_decisions`/`_apply_decisions` pair — not
`memory/dedup.py::resolve_near_duplicate`, a near-identical function with the same
"inherits nothing" character but zero callers in `src/`, which an earlier pass of this
analysis mis-cited. At `near_dup_near_threshold = 0.90`, any incoming block within 0.90
cosine of an existing one triggers a silent, unlogged, irreversible overwrite — no tier
check, no pin check, no audit row, regardless of `self/constitutional` status or a
Beta-confidence of 1.0. `update_block_status` additionally hard-deletes the archived
block's tags, edges, and contradictions outright on the same call. Confirmation in the
data: active blocks average 4.4 tags each; archived blocks average 0.17 — the blocks that
died were the ones that never accumulated standing.

### 1.4 Expensive subsystems, near-zero realised value

| Subsystem | Lifetime usage |
|---|---|
| Contradiction detection | 14 found, 12 unresolved; ~2/3 of all LLM calls |
| Bayesian α/β outcome evidence (v0.17) | 6 records |
| Constitutional review + amendments (v0.18) | 0 amendments |

### 1.5 Root causes

- **RC1** — no direct mutation API. `MemorySystem` exposes ~40 public methods; none is
  edit, delete, or list. Every change to memory is a side effect of an LLM decision.
- **RC2** — write-time pairwise LLM curation is the wrong shape at 31.6k-token scale. The
  entire corpus fits in one prompt; one call can see everything at once, which 2,903
  pairwise calls structurally cannot.
- **RC3** — destructive operations have no audit trail and no undo, except on the one path
  (constitutional amendments) explicitly designed with review in mind.
- **RC4** — config resolution is multi-layered and partly implicit; API keys come from
  neither `config.yaml` nor a documented chain but from SDK defaults, with `--env-file`
  wired only to `serve`. v0.19.3 shipped specifically because an MCP entry silently drifted
  to a different project's config.
- **RC5** — `init --seed` defaults to on and writes ten prose blocks into memory before the
  user has an opinion, with no way to remove them once consolidated.

---

## 2. Framing the two questions

The maintainer's original question — "should this be plain markdown instead?" — turns out
to bundle two independent design questions that the rest of this document treats
separately, because they have different answers:

1. **Where does the data live, and what's authoritative?** (§3) — files vs. database vs.
   hybrid.
2. **Who does the reasoning that curates it, and does elfmem need to own an LLM
   relationship at all?** (§5) — elfmem's own adapters vs. borrowing a host agent.

Collapsing these into one question ("markdown or not") obscures that the honest answer to
each is different — the first resolves to a genuine hybrid, the second resolves to "it
depends which of four distinct consumption modes is calling."

---

## 3. Simulation I — substrate

**Goal**: maximise control and correctness per unit of LLM cost, subject to being robust,
flexible, and elegant.

**Fitness dimensions** (ranked): Control (can the operator deterministically change or
remove any memory?) · Integrity (can memory be silently destroyed?) · Cost (LLM calls to
maintain steady state) · Simplicity · Scale headroom.

**Frozen scenario set**

| ID | Scenario |
|---|---|
| S1 | Reword one constitutional principle |
| S2 | Delete 3 unwanted seeded blocks |
| S3 | New block lands at 0.91 cosine to a pinned constitutional block |
| S4 | 10 blocks learned in a batch; steady-state maintenance cost |
| S5 | Fresh install, new machine, local model only |
| S6 | API key absent; MCP server starts anyway |
| S7 | Corpus grows 140 → 14,000 blocks |
| S8 | Two agents write concurrently |
| S9 | Audit "what changed in memory last month" |
| S10 | Does the constitution survive 6 months? |

### Iterations (condensed — full step-wise traces in the companion plan doc §6)

| Iter | Design | Outcome | Verdict |
|---|---|---|---|
| 1 | Baseline (elfmem v0.19.3 as shipped) | 8 fail / 1 partial / 1 pass | baseline |
| 2 | Add CRUD (`edit`/`forget`/`ls`) + pin guard + mutation log | S1–S3, S9, S10 fixed; cost (S4) and simplicity untouched — a table, a column, three methods added on top of 23.5k LOC | kept as emergency patch, not destination |
| 3 | Move curation from per-block write-time to corpus-level review (one LLM call over the whole corpus, proposal file, human-approved apply) | S4 drops from up to 110 calls to ~1–11; S7 improves (graceful degradation via chunking vs. unbounded O(n) pairwise); simplicity improves (deletes the contradiction loop and most of ADR 0007's checkpointing, which existed only to survive the path being removed) | kept |
| 4 | Files (`.elfmem/memory/**.md`) become authoritative; SQLite becomes a derived, rebuildable index; `self.md` is read directly, never enters the block table | S1/S2 become file edits; **S3 becomes structurally impossible** — no supersession path can reach a file that was never a row; S5 fixed (nothing to remove because nothing was auto-added); S8 regresses to 🟡 (file conflicts vs. SQLite's clean WAL locking) — mitigated by an append-only log for fast ingestion, human-paced edits for curated notes | kept, one accepted trade |
| 5 | Profile-based LLM gateway (`api_key_env`, `api_key: none` for local endpoints), one printed config-resolution chain, boot-time preflight | S6 fixed — preflight fails loudly instead of degrading silently; the specific v0.19.3 incident (MCP entry pointing at the wrong config) is caught at the root by `doctor --resolve` rather than patched at one call site | kept — all dimensions green except the one accepted S8 trade |
| 6 | Drop SQLite entirely — pure markdown + grep | S7 regresses hard: no semantic retrieval, no context-budget mechanism at 14,000 blocks | **rejected** — confirms the index is not incidental, it is what buys the scale story |

**Winner**: Iteration 5. See §6 for the combined architecture; block format, mutation API,
migration phases, and full scenario traces are detailed in the companion plan document.

---

## 4. External validation — research sweep

A structured web research pass (decision-mode, 22 sources fetched, 15 claims independently
verified by adversarial multi-vote before inclusion) was run against four questions: how
named 2026 agentic-memory systems index file-based knowledge, whether any solve identity-
continuity across file moves, whether query-type routing is an established pattern, and
what's published on cost/quality tradeoffs at personal-corpus scale.

### 4.1 Files-as-truth, DB-as-index is not a compromise position

Three independent, unrelated 2026 sources converge on the same architecture Iteration 4
(§3) arrived at:

- **MOSS** (arXiv 2607.04391, a production case study): *"The database is the map; the
  documents are the territory."* The corpus "is never altered, chunked into oblivion, or
  replaced by derived representations."
- **markdown-vault-mcp** ([GitHub](https://github.com/pvliesdonk/markdown-vault-mcp)): a
  working MCP server — SQLite FTS5/BM25 + vector, fused by Reciprocal Rank Fusion — over an
  untouched markdown vault.
- **Vault-LD** ([vault-ld.org](https://vault-ld.org/)): frontmatter resolved into RDF
  triples, prose body left alone, "no database required" for the source of truth itself.

Confidence: high, three independent 2-0 verified sources.

### 4.2 Stable identity across moves is genuinely unsolved — with one exception already in elfmem

What's actually published: **SHA256 content-hash change detection plus a boot-time
reconciliation pass** (markdown-vault-mcp, test-covered) — this answers "did this file
change since I last indexed it," not "does this chunk's accumulated history (reinforcement
count, decay clock, edges) survive being renamed, moved, or *edited*." No system in this
sweep was shown to key chunk identity on a stable ID for continuity purposes across edits;
the one claim asserting path-based identity semantics was independently refuted (0-2).

This is where elfmem is ahead of the published field, not behind it: `compute_content_hash()`
in `memory/blocks.py` already makes the block ID `sha256(content)[:16]` — already
content-addressable. The gap the research surfaces: once free-form file editing is the
whole point of §3's design, content-hash-as-ID breaks on every edit (new hash → new ID →
lost history), which moves-only reconciliation never had to handle. **Refinement to carry
into §6**: a permanent `id:` in frontmatter, assigned once, independent of content; the
hash becomes a secondary field used only to detect "does this need re-embedding."

### 4.3 Query-type routing is thin evidence — don't build it

Exactly one verified instance of "route structurally different query types differently
against one corpus" exists: MOSS classifies each question as temporal / thematic /
affective / personal / documentary and runs different SQL per type, on the stated
principle that *"relevance is not a static property of stored items; it is a property of
the question being asked."* It is a single unreplicated deployment (one person, ~44M
tokens, ~1 year) with no ablation showing it beats one well-tuned hybrid strategy. Every
other routing claim checked in this sweep — a four-bundle depth router, a "MemScheduler,"
a coarse-to-fine hierarchy — **failed independent verification** against its own primary
source.

Implication for elfmem: the existing two-tier split (constitution = full inclusion, no
ranking needed; everything else = one hybrid strategy) is not a lesser version of a fancier
system — it is the reference architecture found in the literature. There is no published
evidence that per-query-type routing on top of it would earn its complexity.

### 4.4 The measured win is on the write path, not the read path

**SAGE** (arXiv 2605.30711) measured, against underlying tables rather than an abstract,
exactly the trade proposed in §3 Iteration 3: Mem0 and A-Mem invoke an LLM on every
candidate fact regardless of novelty; SAGE gates with a cheap rule-based novelty check
first. Result: best average token-F1 against Mem0 on 7 of 7 open-weight backbones tested,
while cutting add-phase API cost 3.4x and add-phase latency 2.5x on GPT-4o-mini for a 1.3-
point LLM-judge gap. This is independent, external, measured validation that moving
curation off the per-item write-time path can win on quality *and* cost simultaneously,
not trade one for the other.

### 4.5 Worth stealing, flagged as unverified in this sweep

One source (Zep/Graphiti) reportedly never deletes a superseded fact — it marks the old
one invalid with a timestamp and retains it. This is exactly the fix for §1.3's silent
constitutional destruction, but the claim was not among the batch independently checked in
this run (surfaced, not verified — treat as a pattern worth borrowing, not a confirmed
citation). It costs almost nothing to adopt regardless: in a git-backed file world, "keep
the old version with a timestamp" is git history, obtained for free.

### 4.6 Honest gaps in this research pass

Despite asking directly, no claim about Zep/Graphiti, Letta/MemGPT, HippoRAG, or GraphRAG
survived independent verification in this run — a gap in this sweep, not evidence those
systems work some other way. The field is young (every surviving source dated Dec 2025–Aug
2026); several are non-peer-reviewed preprints or vendor blogs (one vendor "state of the
art" claim was explicitly checked and refuted). MOSS, the strongest single source for §4.1,
is one deployment with no external replication.

### 4.7 Addendum (2026-08-08) — file/directory organization, not just storage

§4.1–4.6 answered *where the data lives* (files vs. database). A separate paper — Zhou et
al., ["Filesystem-Based Memory for LLM Agents: Organization, Evolution, and
Sustainability"](https://arxiv.org/html/2607.26637v1) (UIUC, UC San Diego, UC Merced, Adobe
Research, Texas A&M) — studies *how the files should be organized*, at 32k–128k token scale
(comparable to elfmem's own 31.6k-token corpus, §1.1). Five claims below were verified
directly against the primary text via WebFetch, not taken from the secondhand digest that
prompted this addendum — confidence: high for all five.

- **The "Taxonomy Contract"** (P1–P5, Section 2.1): sibling distinction (siblings
  distinguishable by label alone), sibling relatedness (siblings belong together),
  parent-child coverage (a parent covers its children), tree-wide proximity (distance
  mirrors relatedness), structural economy (structure serves the search, not itself). A
  concrete framework for §11 open decision 3 (one file per block vs. many-per-file) — the
  answer isn't a fixed rule, it's "whichever satisfies P1–P5 for this content," decided and
  periodically re-decided by `elf-review`, not fixed once at migration time.
- **Organization risk, not just organization benefit.** The same agent-curated store shape
  scored 86.1% on LoCoMo and 37.5% on PersonaMem 32k (Table 1) — organization choice can be
  a large correctness risk depending on task type, not a safe aesthetic decision. Directly
  strengthens why the companion plan's §8 Phase 4 migration gate (verify retrieval output
  before flipping authority) is load-bearing, not optional ceremony.
- **Curator strength shapes style, not quality; searcher strength pays directly.** On
  PersonaMem 128k, correctness across three curator strengths was 73.8% → 66.7% → 71.4% —
  not even monotone in model strength — while resulting store structure varied from 122 to
  2 to 105 files (Section 4.3). Supports §5's `elf-review`/`elf-recall` split on independent
  grounds: `elf-review` (curator) doesn't need a strong model; an agentic `elf-recall`
  (searcher) does. If step 5's `api_key_env` gateway is ever used to route to a stronger
  model somewhere, this is the evidence for where.
- **Taxonomy adherence erodes with growth except under the strongest curator** (Section
  4.4) — only the strongest management agent tested held organization at 140 accumulated
  tasks; weaker curators visibly drifted. A concrete, named risk for `elf-review`: it should
  check its own proposals against P1–P5 explicitly, not rely on unchecked LLM judgment
  staying good over many cycles.
- **The tool harness is a control knob**, reshaping the resulting store "as strongly as
  swapping the model" (Section 4.5). Whatever mutation tools `elf-review` gets beyond
  `edit`/`forget`/`ls` (§1 — a `move`/`split`/`merge` tool, say) is itself a design decision
  with measured effect on outcome — worth treating deliberately when step 8 specs it, not
  adding tools ad hoc.

Source captured at
`/Users/emson/Dropbox/vaults/elf_vault_proj/elf_vault/sources/papers/filesystem-based-memory-for-llm-agents.md`
(2026-08-08).

---

## 5. Simulation II — reasoning ownership

### 5.1 The `ctx` precedent

`ctx` (`/Users/emson/Dropbox/vaults/skill_forge/skills/ctx`) is a shipped Claude Code
Skill solving an adjacent problem — curating context from a personal markdown vault into a
cited prompt — that made two decisions directly relevant here, one of which elfmem should
adopt and one of which does not transfer:

- **D-001**: it is a Skill, not an application with its own LLM client. Its own research
  found *"most of a standalone app's plumbing — LLM gateway, key management, response
  capture — exists only to connect the tool to an LLM the app doesn't otherwise have. A
  skill runs inside an agent that already has all of it."* No adapters, no API key, no
  gateway — the "judge" reasoning is whichever agent is running the skill.
- **D-002**: no index, ever — live `ripgrep` on every query, backed by a citation that
  agentic grep beats embedding search for literal-span recovery at personal-vault scale.
- **D-003**: the compiler never lets an LLM rewrite curated text — deterministic
  template-fill only, gated by a human checkbox worksheet that defaults checked (Claude may
  *judge and suggest*, never *decide and include without review*).

### 5.2 Why D-002 does not transfer wholesale to elfmem

`ctx` is built for **occasional, human-supervised, unranked** retrieval — a human types a
query a few times a day, reviews a worksheet, and the answer only needs to *match*, not be
*ranked*, because a human is filtering it anyway. elfmem's primary consumption mode
(`frame()`/`recall()` called by an autonomous agent loop, silently, every turn, with no
human present) is **frequent, autonomous, ranked** — it must reflect recency, reinforcement
history, and graph centrality, none of which grep can supply, and it cannot insert a
checkbox review into an agentic loop without breaking the loop.

### 5.3 Frozen scenario set

| ID | Scenario |
|---|---|
| S1 | Human, in a Claude Code session, asks "what do I know about X" — occasional |
| S2 | Autonomous agent loop calls `frame('attention', query)` every turn, no human present |
| S3 | Nightly cron runs `elfmem curate` — no session, no host agent |
| S4 | Peer agent (not Claude Code) queries elf's memory |
| S5 | elf records a new memory mid-task |
| S6 | Human reviews/dedupes memory (the RC1/RC3 fix from §1) |
| S7 | Corpus grows to 10,000+ chunks |
| S8 | A skill's LLM judge proposes a bad dedup — who catches it? |
| S9 | Fresh install — does elfmem need an LLM API key on day one? |
| S10 | A scheduled review is mid-flight while S2's autonomous loop also fires |

### 5.4 Iterations

| Iter | Design | Outcome | Verdict |
|---|---|---|---|
| 1 | Baseline: §3's winner (files + derived index + own LLM gateway for corpus review) | S9 still fails — a solo user needs a key for a capability (review) they may never use headlessly | baseline |
| 2 | Add `elf-review` — a Skill mirroring `ctx`'s Discover→Judge→Worksheet→Apply, reasoning done by the host agent | S6/S8 strengthen (inherits `ctx`'s tested worksheet mechanics); S9 partly fixed (review no longer needs a key; S2–S5 still do) | kept |
| 3 | Go further: replace `frame()`/`recall()` themselves with a `ctx`-style skill, no index at all | **S2 fails twice** — no human present to checkbox-review an autonomous loop's every call, and grep alone cannot rank by recency/reinforcement/centrality; **S3 fails** — no host agent at 3am; **S4 fails** — a non-Claude-Code peer has no `SKILL.md` to invoke | **rejected** — confirms precisely where §5.2's prediction holds |
| 4 | Split by consumption mode: `elf-review` (writes, human-gated) and `elf-recall` (read-only, literally `ctx` pointed at `.elfmem/memory/`, live grep, no index) for the occasional/human case; the index-backed MCP tools stay untouched for the autonomous/no-human case; the gateway shrinks to cover only S3 | S1 now genuinely good (proven mechanics, not a reimplementation); S9 fixed for the common case; new labelled risk **S11** — `elf-recall` (unranked grep) and `frame()` (ranked index) can disagree on the same question, mitigated by labelling `elf-recall`'s output as unranked | kept |
| 5 | Test dropping embeddings from the index too, per §4.1's own citation | S2 regresses silently on vocabulary-mismatch queries (the exact "error handling" / "swallow exceptions silently" case worked through earlier in this investigation) — embeddings solve precisely this, and grep cannot | **rejected as default**; kept as an explicit `index.embeddings: auto\|always\|never` option, since `elf-recall` (Iteration 4) already covers the case where embeddings add nothing |

**Winner**: Iteration 4. **Stopping reason**: goal reached — every fitness dimension green
with one labelled, mitigated risk (S11); Iteration 5 confirmed the boundary rather than
finding a further improvement.

### 5.5 Consequences worth stating plainly

- **Not literally free** — `elf-review`'s reasoning cost moves from elfmem's API bill to
  the host session's context and turns. Real cost, just not a separate credential to
  manage, and not double-paid.
- **Two retrieval paths now exist** and can disagree (S11) — must stay legible, not hidden.
- **More artifacts, not fewer** — a `SKILL.md` + scripts alongside the library/CLI/MCP
  surface. §3 simplified elfmem's internals; §5 simplifies its LLM *ownership*, at the cost
  of a second packaging format to maintain.
- **Peers stay on the protocol path** — Alv/Mira were never going to invoke a `SKILL.md`;
  they call the index directly and reason with their own model. Skills solve the
  Claude-Code-specific slice of this, not the whole peer story.
- **Curator/searcher model strength should be asymmetric** (§4.7) — external evidence that
  `elf-review`'s reasoning strength barely moves correctness (it shapes organizational style
  instead), while an agentic `elf-recall`'s strength pays off directly. Don't spend a
  stronger model on the cheap, frequent operation; spend it on the one that's occasional and
  actually reasoning-heavy.

---

## 6. Combined target architecture

```
L0  SUBSTRATE     .elfmem/memory/**.md        git-versioned, hand-editable, AUTHORITATIVE
                    self.md                   constitution — read directly, never a row, never superseded
                    notes/*.md                curated knowledge; each block carries a permanent
                                               frontmatter id (§4.2), independent of content hash
                    log/YYYY-MM.md            append-only fast path (mitigates S8 file conflicts)

L1  INDEX         .elfmem/index.db            DERIVED, disposable, rebuildable with zero LLM calls
                                               embeddings (default on, §5.4) + FTS5/BM25 + graph + RRF
                                               reinforcement/recency/centrality bookkeeping lives here

L2a SKILLS        elf-review                  Discover(index) → Judge(host LLM) → Worksheet → Apply
                  elf-recall                  ctx, pointed at .elfmem/memory/ — live grep, unranked,
                                               occasional/human-supervised only (labelled as such — S11)

L2b MCP TOOLS     frame() / recall()          index-backed, ranked, no human review — the autonomous,
                                               every-turn path; unchanged in spirit from today

L3  GATEWAY       optional                    only exercised by headless/no-host-agent operation
                  (profile-based, §3 Iter 5)  (S3: scheduled cron review with nobody watching)
```

**What each layer owns**: L0 is truth. L1 is math (no LLM). L2a is reasoning borrowed from
whichever agent invokes the skill, gated by a human checkbox before any write. L2b is
reasoning-free ranked retrieval for the agentic loop. L3 is the fallback for the one
scenario nothing else covers.

Block/frontmatter format, the `edit()`/`forget()`/`ls()` API surface, config-resolution
chain, `doctor --resolve` preflight design, and onboarding template detail are unchanged
from the companion plan document §5 and §5.6–5.7, with the single refinement from §4.2
(permanent `id:` separate from content hash) folded in.

---

## 7. What gets deleted

Unchanged from the companion plan document's assessment (§7 there), with one addition from
§5: most of the `LLMService` protocol's `process_block`/`detect_contradiction`/
`propose_amendment` surface shrinks to a minimal interface used only by the now-optional L3
gateway, since the primary interactive path no longer calls it at all.

Roughly 8–11k of 23,500 source lines are candidates for removal on the evidence in §1 that
they do no observable work on the one deployment audited: pairwise contradiction detection,
decay tiers and `curate()` archival, near-dup supersession, the inbox/active/archived state
machine, `rescue.py` and the embedding-model lock, and the ADR 0007 checkpointing machinery
that exists only to survive the write-time path being removed.

Each ADR governing a removed subsystem (0001, 0003, 0006, 0007) was sound given its
premises; what changed is the evidence, not the reasoning. Each removal should carry its
own ADR citing this document.

---

## 8. Migration

Non-destructive, reversible, verified at each step — full six-phase sequence in the
companion plan document §8 (export to markdown → commit → rebuild index → verify retrieval
parity on 10 fixed queries → hand-restore lost constitutional roles → flip authority).
Phase 4 (retrieval parity) is the gate; do not proceed past it on the assumption that a
diverging ranking is "probably fine."

---

## 9. Sequencing recommendation

| Step | Ship | Fixes | Risk |
|---|---|---|---|
| 1 | Pin guard + supersession log | stops active data loss (§1.3) | trivial |
| 2 | `edit()` / `forget()` / `ls()` | RC1 | low |
| 3 | `doctor --resolve` + `.env` everywhere + preflight | RC4 | low |
| 4 | `init` writes zero blocks | RC5 | low |
| 5 | LLM gateway profiles + `api_key_env` | OpenRouter/local support | low |
| 6 | `elfmem review` — corpus-level, proposal-only | RC2 | medium |
| 7a | Retire decay-driven block archival (ADR 0009) | cost, complexity | low |
| 7b | Retire pairwise contradiction detection (ADR 0010) | cost, complexity | medium |
| 8 | Markdown substrate + derived index | structural (§3) | high |
| 9 | `elf-review` / `elf-recall` skills | reasoning-ownership (§5) | medium — depends on 8 |

Step 7 split after grounding: decay's archival trigger and pairwise
contradiction detection have different replacement-readiness (6a shipped
before 7a; 6b, contradiction's replacement, has not shipped) and different
blast radius (7a's mechanism was evidenced-inert in production; 7b's
retirement accepts a disclosed coverage gap for new content). See ADR 0009
and ADR 0010.

**Steps 1–5 are worth doing regardless of whether steps 8–9 are ever taken** — independently
valuable, low-risk, each closes a reported problem on its own. Step 1 stops live data loss
and should not wait on the rest of this document being decided.

---

## 10. Residual risks and honest caveats

| Risk | Note |
|---|---|
| Every §1 figure is from one instance (solo use, 36s average sessions) | The decay-is-inert finding specifically would weaken under longer sessions. What does *not* depend on instance-specifics: the missing edit API, the "inherits nothing" overwrite semantics, the 52x cost amplification, and `OPENAI_API_KEY`-only key resolution — those are properties of the code. Recommend pulling the same `lifetime_token_usage`/`archive_reason` breakdown from the Alv and Mira instances before committing to §3 Step 8. |
| Concurrent file writes can conflict (S8, §3 Iter 4) | Mitigated, not eliminated, by the append-only log split |
| Whole-corpus review outgrows one context window as the corpus scales | Not binding below ~2,000 blocks; needs a cluster-then-review fallback beyond that |
| `elf-recall` and `frame()` can disagree (S11, §5.4) | Must stay legible — label unranked results as such |
| §4's external research is young and thin in places | Several sources are non-peer-reviewed preprints; the query-routing and stable-ID pillars specifically are single-source or unverified — treat as directional, not settled |
| Markdown parsing is a new bug surface | Strict format, fail-fast, `elfmem index --check` |
| This document's own convergence (files-as-truth confirmed by both simulation and external research) could be confirmation bias rather than independent triangulation | The three external sources (§4.1) were found via a structured adversarial-verification research pass, not selected to confirm §3's conclusion — but the reader should weigh this themselves rather than take the alignment as proof |

---

## 11. Open decisions

1. **Emergency patch now, or wait for the full picture?** Step 1 (§9) is a few lines and
   stops live constitutional data loss today. Recommend shipping it standalone,
   immediately, independent of any decision on the rest of this document.
2. **Is `self.md`-as-file acceptable**, given constitutional blocks currently participate
   in retrieval scoring? Reading it as always-included preamble is Architecture M from
   ADR 0003 — previously deferred, measured there at +33pp under drift, −7pp under
   stability.
3. **One file per block, or many blocks per file?** §4.7's Taxonomy Contract (P1–P5) gives
   this a real decision framework instead of a fixed global rule — decided per content area
   and periodically re-checked by `elf-review`, not chosen once at migration time. This
   document's working assumption (many-per-file with `##` headings, matching `ctx`'s
   convention) is easier to browse but needs the sibling-distinction/relatedness checks
   (P1/P2) to avoid becoming a junk drawer; one-per-file is simpler to parse and diff.
4. **Do peer, mind, and amendment subsystems survive unchanged**, or does this evidence
   base warrant reassessing them too?
5. **Should `elf-recall` literally depend on `ctx`, or fork its pattern?** Sharing code
   creates a cross-project dependency; forking duplicates a proven pipeline. `ctx`'s
   read-only-vault invariant (D-004) does not hold for elfmem, since `elf-review` must
   write — the two skills cannot simply share one script set unmodified.
6. **Should embeddings default on or off** (§5.4 Iteration 5)? Recommended default is on
   for the index (silent degradation on vocabulary mismatch is worse than the cost of
   embedding), off-by-default acceptable for `elf-recall` specifically.
7. **Is the peer/multi-tool sharing case (§5.5, "peers stay on the protocol path") real
   enough day-to-day to justify keeping the index/MCP layer at elfmem's current scale**,
   or would a single-tool, single-user deployment be better served by skipping straight to
   `ctx`-style retrieval with no index at all? (Raised, not resolved, earlier in this
   investigation — the honest answer depends on how load-bearing Alv/Mira sync actually is
   in practice.)

---

## References

- Live production database: `~/.elfmem/databases/elfmem.db` (queried 2026-08-04)
- Companion plan: [`docs/plans/plan_v2_substrate_reevaluation.md`](../plans/plan_v2_substrate_reevaluation.md)
- `ctx` skill: `/Users/emson/Dropbox/vaults/skill_forge/skills/ctx` (`SKILL.md`, `docs/decisions.md` D-001–D-005)
- MOSS: [arXiv 2607.04391](https://arxiv.org/pdf/2607.04391)
- markdown-vault-mcp: [github.com/pvliesdonk/markdown-vault-mcp](https://github.com/pvliesdonk/markdown-vault-mcp)
- Vault-LD: [vault-ld.org](https://vault-ld.org/)
- SAGE: [arXiv 2605.30711](https://arxiv.org/pdf/2605.30711)
- Memanto (architecture cited, benchmark claims refuted on independent check): [arXiv 2604.22085](https://arxiv.org/pdf/2604.22085)
- Zhou et al., "Filesystem-Based Memory for LLM Agents: Organization, Evolution, and
  Sustainability" (Taxonomy Contract, §4.7): [arXiv 2607.26637v1](https://arxiv.org/html/2607.26637v1)
- Existing project ADRs referenced: 0001, 0003, 0006, 0007 (`docs/decisions/`)
