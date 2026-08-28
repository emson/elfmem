# Plan — goal-directed edge metabolism

**Status**: Stage A approved for build (this plan). Stage B (live autonomous
mutation) **not yet approved** — see "Decision needed" below.
**Owner**: elf, Ben
**Related**: [ADR 0003](decisions/0003-defer-constitutional-evolution.md),
[ADR 0006](decisions/0006-defer-multi-parameter-self-tuning.md),
[ADR 0009](decisions/0009-retire-decay-driven-archival.md),
[ADR 0010](decisions/0010-retire-pairwise-contradiction-detection.md),
[`docs/plans/archive/plan_memory_scoring.md`](plans/archive/plan_memory_scoring.md)
(Zettelkasten auto-linking deferral)

## Intent

Today, `consolidate()` creates `origin='similarity'` edges deterministically
(cosine + tag + category + temporal composite, thresholded, capped at 5/block
— `operations/consolidate.py::_composite_edge_score`). It cannot express *why*
two blocks matter to each other beyond numeric proximity, and it cannot
connect two blocks that are conceptually related but lexically/embedding-
distant (e.g. a technical decision and a business goal that share no
vocabulary).

The ask: let the **self frame** — elf's own `self/constitutional` and
`self/goal` blocks — decide which additional connections serve elf's own
goals, triggered by a periodic "metabolism" pass, applied **without a human
approval gate**. Correction happens two ways instead of a gate: live
conversation (existing tools: `forget`/`connect`/`outcome`), and shaping the
self frame itself (the existing constitutional amendment mechanism).

This document is the output of a `/simulate` optimize loop (full journey
preserved in this session's transcript, condensed here) plus the diligence
pass that followed it — which surfaced that **this project has already
considered and explicitly deferred almost exactly this feature once**, on
grounds that are still live. That finding reshapes the plan below.

## The design (Stage B target — what it will do, once approved)

**Two-tier edges.** Tier 1 (unchanged): today's deterministic similarity
scorer, always-on, zero LLM cost. Tier 2 (new): goal-directed edges, ungated
by the cosine floor, created only during a periodic "metabolism" pass,
capped hard per pass, always provenance-tagged, subject to the exact same
decay/reinforcement dynamics `curate()` already applies to every edge — an
unused goal-directed edge withers on its own, same as any edge today. No
gate is needed because the correction pressure is *usage*, not approval.

**Metabolism is Deep Sleep, not a fifth rhythm.** `rescore()` already exists
specifically to "keep the knowledge graph aligned with the agent's evolving
identity" (its own docstring) — bounded budget (`max_per_run`, default 20),
staleness horizon (`target_max_age_days`, default 90 days), progressive
rotation, and additive/evidence-weighted updates (the v0.15.2 fix that
stops one bad pass from erasing months of accumulated evidence). Its own
docstring already flags the gap this plan fills: *"Does not touch
contradictions or graph edges. Edge regeneration is deferred to a future
patch (cost is O(N²))."* Extend `rescore_blocks()` to also propose
goal-directed edges for the block it's already re-touching — one trigger,
one budget, one pass, both jobs. No new schema: `edges.origin`,
`.relation_type`, and `.note` ("optional agent/LLM description") already
exist and are unused beyond `'similar'` — this needs zero migration.

**Bounded candidates, not a corpus scan.** The LLM never sees the whole
corpus (that failure mode — hallucination on long concatenated context,
O(corpus) cost per pass — was traced and rejected in the simulate loop).
Candidates are a widened top-K nearest-neighbour shortlist by embedding
(pure math, same cost class as today's prefilter, just a higher K and no
threshold cutoff), giving the LLM room to find non-obvious connections
without unbounded scope.

**Provenance over gating.** Every tier-2 edge stores which self/goal block
justified it and the model's one-line reasoning, in the existing `note`
column. This is how you correct a bad decision without approving each one:
ask why, then either `forget()` the edge or amend the goal block that
produced it — fixing the cause, not just the symptom.

**Constitutional tie-break.** When self/goal blocks disagree, reuse the
precedence `curate()` already gives constitutional blocks (`_reinforce_constitutional`
— "regardless of score") rather than inventing new conflict resolution.

**Trust-gating.** Reuse existing peer-trust infrastructure to discount or
exclude peer-sourced blocks as tier-2 edge *targets* — `rescore()` already
excludes peer-sourced blocks as the *subject* of self-alignment scoring
(`source_peer IS NULL`); extend the same caution to what a metabolism pass
is allowed to connect *to*, mitigating prompt-injection-flavoured framing
from untrusted content.

Full scenario-by-scenario trace (11 frozen scenarios: scale, malformed LLM
output, hallucination, determinism/rebuild-invariant, concurrency,
contradictory goals, adversarial content, cost) is preserved in this
session's transcript; nothing here overrides an invariant already load-
bearing elsewhere — `elfmem index rebuild`'s zero-LLM determinism guarantee
is untouched because tier-2 edges are a *live-consolidation* enrichment, not
something rebuild reproduces or depends on.

## Related decisions — read before building Stage B

This is not a green field. Four decisions in this codebase bear directly on
"should an LLM autonomously decide graph structure":

1. **[ADR 0003](decisions/0003-defer-constitutional-evolution.md)** (2026-05-23)
   rejected a "self-architecting agent" that adapts its own configuration —
   simulation showed it **underperforms fixed strategies in every scenario
   tested**. Not the same mechanism (that was parameter tuning; this is
   content decisions), but the same family of claim ("let the agent decide
   autonomously") failed empirically once already in this codebase.
2. **[ADR 0006](decisions/0006-defer-multi-parameter-self-tuning.md)** (2026-06-02)
   generalises the axiom this plan must respect: *"no magic numbers —
   hardcoded constants must be defensible from first principles."* Every new
   constant this plan introduces (candidate K, tier-2 cap) must trace to an
   existing, already-justified constant (`EDGE_DEGREE_CAP=5`,
   `rescore.max_per_run=20`) — not a fresh guess.
3. **[`plan_memory_scoring.md`](plans/archive/plan_memory_scoring.md)
   explicitly deferred "Zettelkasten auto-linking"** — automatic LLM-judged
   edge creation — with a named trigger: *"when we have evidence that manual
   `connect()` is undertilised by agents ... AND we have a way to validate
   LLM-judged links don't introduce phantom edges."* **This is, almost
   word for word, this plan.** ADR 0003's own phrasing about a sibling
   deferral applies here without softening: *"re-proposing them under
   different vocabulary doesn't change the underlying judgment."*
4. **[ADR 0010](decisions/0010-retire-pairwise-contradiction-detection.md)**
   (2026-08-08 — three days before this plan) retired a *different* pairwise
   per-block LLM judgment mechanism (contradiction detection) specifically
   because measured **realized value in production was near zero**: 14
   contradictions ever recorded on the real self-hosted DB, 86% still
   unresolved; a benchmark built to showcase the mechanism scored 4.8% with
   it fully enabled. Its replacement design was **corpus-level and
   human-gated** — the opposite of "no human in the loop." This is the most
   recent and most directly cautionary precedent: elfmem has, within the
   past week, measured a mechanism in this exact shape and found it wasn't
   worth its cost.

**What I checked against real data before writing this plan** (read-only,
`~/.elfmem/databases/elfmem.db`):

```
origin        relation_type   count
similarity    similar         27
agent         replies_to       9
co_retrieval  co_occurs        9
agent         predicts         8
```

53 edges total. 17 are `origin='agent'` — but tracing both `relation_type`
values to source (`operations/mind.py:202`, `operations/peer.py:814`) shows
**both are mechanical side effects of other features** (`mind_predict()` and
peer reply-threading), not spontaneous `connect()`/`connect_by_query()`
calls issued because the agent noticed two blocks were related. **Genuine
spontaneous manual linking, on this real corpus, is effectively zero.** The
first half of the Zettelkasten trigger — "`connect()` is undertilised" —
appears satisfied by real evidence, on this instance, today. The second half
— "a way to validate LLM-judged links don't introduce phantom edges" — is
exactly what Stage A below is built to produce.

## Decision needed (write as ADR 0011 once resolved, not before)

Given ADR 0010 landed three days before this plan, on a mechanism in the
same shape, with a negative result — **Stage B (live, autonomous, ungated
edge mutation) should not ship on design confidence alone.** The
responsible next step, consistent with this project's own repeated pattern
(ADR 0006, ADR 0009, ADR 0010 — measure against the real self-hosted DB,
then decide) is to build the measurement instrument the Zettelkasten
deferral already asked for, look at what it produces on real content, and
*then* decide Stage B with evidence instead of simulation alone.

This plan builds **Stage A only**. Stage B needs an explicit go-ahead after
Stage A's output has been reviewed — not because a human gate belongs in
the live mechanism (it doesn't, per the brief), but because *shipping the
mechanism at all* is the kind of load-bearing, previously-rejected-once
decision this project's own conventions route through an ADR with evidence,
not through silent re-implementation under new vocabulary.

## Stage A — build now (safe, zero mutation, zero schema change)

A dry-run instrument: for a bounded sample of blocks already eligible for
rescoring, compute the widened candidate shortlist and (behind an explicit
opt-in flag) ask the LLM to propose tier-2 connections with reasoning —
**report only, never write**. This is the literal "way to validate
LLM-judged links don't introduce phantom edges" the deferral asked for,
buildable without deciding Stage B's autonomy question at all.

| | |
|---|---|
| **Owns** | `src/elfmem/operations/rescore.py` (extended), `tests/test_rescore_metabolism.py` |
| **Touches** | `src/elfmem/cli.py` (`dream` command — new `--metabolism-dry-run` flag), `src/elfmem/api.py` (`dream()` — new opt-in parameter) |
| **Needs** | Nothing new — `cosine_similarity` (`memory/dedup.py`), `insert_edge`'s existing `note`/`origin`/`relation_type` params (unused, no migration), self/goal block retrieval (tag pattern `self/%`, mirrors `SELF_FRAME`) |
| **Done when** | Given a corpus with self/goal blocks and a rescore-eligible block, the dry run reports 0-K proposed connections with reasoning strings and a candidate-pool size, and **writes nothing to `edges`** — verified by asserting `edges` table row count is unchanged before/after |
| **Verified by** | `uv run ruff check src/elfmem/operations/rescore.py src/elfmem/cli.py && uv run mypy src/elfmem/operations/rescore.py src/elfmem/cli.py && uv run pytest tests/test_rescore_metabolism.py` |
| **Out of scope** | Writing edges (Stage B); a `should_metabolize` advisory property; extending the tier-2 mechanism to `mind`/`message`/`decision`/`prediction` categories (excluded by `rescore()`'s existing eligibility, inherited as-is) |

### Constants, justified (not guessed — ADR 0006's bar)

| Constant | Value | Justification |
|---|---|---|
| Candidate shortlist K | 30 | 6× `EDGE_DEGREE_CAP` (5) — wide enough to surface non-obvious neighbours, still O(1) per block, not O(corpus) |
| Tier-2 proposals per block | 3 | Below `EDGE_DEGREE_CAP` (5) — tier 2 is the higher-risk, lower-confidence layer; it should never out-connect tier 1 |
| Sample size per dry run | `rescore.max_per_run` (20, existing) | Reuse, not a new number |

## Cross-project dependency and fallback

elfmem is consumed by other projects via `.elfmem/config.yaml` +
`.elfmem/AGENT.md` (see this project's own `CLAUDE.md` "elfmem — Project
Memory" section, rendered the same way for every consumer). Tier-2
metabolism is **strictly additive and version-gated**:

- Detect availability via `elfmem doctor --modules` (`KEY_MODULES` in
  `project.py` — add one line when Stage B ships) or `elfmem --version`.
- **If missing** (older elfmem, or Stage A/B not yet shipped): nothing
  degrades. Tier-1 similarity edges are unaffected in every elfmem version;
  a consuming project simply doesn't get tier-2 connections until it
  upgrades. No code in a consuming project should ever *require* tier-2
  edges to exist — treat their absence as the default case, their presence
  as an enrichment, matching elfmem's own "Tier 1 must always work"
  contract (`docs/agent_friendly_principles.md`).
- Suggested fallback text for a consuming project's own docs: *"If
  `elfmem doctor --modules` doesn't list edge metabolism, either run
  `uv sync` / upgrade elfmem, or continue relying on `connect()` /
  `connect_by_query()` for explicit linking — nothing else changes."*

## What happens after Stage A ships

1. Run `elfmem dream --metabolism-dry-run` against the real corpus (same
   read-only-safe posture as the earlier migration dry run — Stage A
   writes nothing; the flag ignores `--rescore`/`--no-llm`).
2. Review the proposed edges and reasoning by hand — this *is* the human
   review the brief said shouldn't gate the live mechanism, but nothing
   forbids using it once, up front, to sanity-check the mechanism itself
   before trusting it to run unattended.
3. Bring the result back for the Stage B go/no-go — at that point this
   section's content, plus whatever Stage A found, becomes ADR 0011.

## Stage A build findings (2026-08-11, first real-corpus dry run)

Ran `elfmem dream --db <read-only copy> --metabolism-dry-run --json`
against a safe copy of the real 185-block self-hosted DB (never touched the
original — same MD5-verified posture as the earlier migration dry run).
Two real bugs surfaced immediately, neither visible from design or unit
tests against small fixtures — exactly the value this stage exists to
provide:

1. **`self/%` is not `self/goal`.** The first cut fetched every `self/%`
   tagged block (129 on the real corpus — constitutional/value/style/
   context/constraint, not just goals) as "the agent's goals." Blew a local
   model's 4096-token context window outright (`n_keep: 27252 >= n_ctx:
   4096`) — 20/20 blocks failed. Fixed: exact tag `self/goal` (28 blocks on
   this corpus), plus `GOAL_DIRECTED_SELF_GOALS_CHAR_BUDGET=2400` (~600
   tokens, matching `SELF_FRAME.token_budget`) so growth over time can't
   silently reopen the same failure.
2. **Full content for 30 candidates is also too much.** Real active-block
   content averages 904 chars (max 5935); 30 of them blew the same context
   window even after fix 1. Fixed: candidates now use the block's `summary`
   (avg 291 chars — already computed at consolidate time, exactly what
   summaries are for) with a `GOAL_DIRECTED_CANDIDATE_CHAR_CAP=400`
   fallback cap for blocks with no summary yet.

Both fixed, re-verified against the same real corpus (results below).
Neither the real production DB nor its copy were mutated by any of this —
`metabolism_dry_run` never calls `insert_edge`.

**Result after both fixes**: `blocks_considered=20`, 2 self/goal blocks
used (within the char budget, most-recently-reinforced first), **0
llm_failures**, 48 proposals across all 20 blocks. Full proposal content
isn't reproduced here (real memory content); see the actual run output for
the reasoning strings and candidate pairs before deciding Stage B. On
inspection the reasoning reads as genuinely grounded (specific principles
tied to specific stated goals), not generic filler — a real, if informal,
first pass at validating "does this avoid phantom edges."

## Host-agent reasoning mode (no local model / no API key required)

A live design question during Stage A build: should metabolism support
running via a host agent session's own reasoning (e.g. a Claude Code
session with elfmem's MCP tools) instead of elfmem's own configured LLM
adapter — the same "reasoning-ownership seam" `model.md` already designs
for the corpus-review engine (elf-review: host-agent reasoning *or*
elfmem's own gateway, same pipeline either way)?

**Resolved without adding any CLI surface.** `metabolism_dry_run()` already
computes the full candidate shortlist and self/goal content before it ever
calls the LLM. `MetabolismDryRunResult` now carries that raw data
(`self_goals`, `candidates`) alongside `proposals` — always, regardless of
whether the LLM call succeeded. A host session with no configured adapter
gets `llm_failures` equal to `blocks_considered` (visible, not silently
masked — this matters, given the two bugs above were exactly this kind of
failure) and reads `self_goals`/`candidates` instead of `proposals`,
reasoning over them directly and applying its own judgement via the
already-shipped `connect()` / `elfmem_connect` MCP tool (already supports
richer relation types than similarity — `elaborates`, `supports`,
`contradicts`, `validates` — plus a `note` field for the reasoning, and
already writes `origin='agent'`, automatically distinguishable from
`origin='metabolism'` — the pipeline path). No `--candidates-only` flag, no
new apply endpoint: the same single result already carries everything, and
the write path already existed. This also directly answers "cross-project
dependency and fallback" above — a project with no LLM adapter configured
at all still gets goal-directed connections, for free, whenever a host
agent session is doing the reasoning.
