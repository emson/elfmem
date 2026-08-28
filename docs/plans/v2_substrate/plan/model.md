# Concept model — elfmem v2, Steps 8–9

*The shared interpretation of the spec. Durable: changes only when the architecture
changes. Every builder receives the core concepts verbatim via the constitution, so
wording here is load-bearing.*

**Spec:**
`docs/research/v2_substrate_and_reasoning_ownership_research.md` (entry point, §1–§11)
+ `docs/plans/plan_v2_substrate_reevaluation.md` (companion — block format, migration
phases, config chain detail referenced rather than repeated) · **Interpreted:** 2026-08-09
· **Covers:** Steps 8–9 only (full · Steps 1–7b are already shipped and out of scope
for this model)

**Spec hashes** (sha256, first 16 hex): `473ef56f4ae49c8e` (research doc),
`cf645763314e5dfc` (companion plan). **Base commit:** `752cd9f` on `elfmem_index`.

**Decision-gate context:** the user was asked whether to gather cross-instance
(Alv/Mira) usage data before committing to Step 8, per the plan doc's own stated
precondition (§10). **Decision: commit now on single-instance evidence.** The
instance-dependent finding (decay is inert) is explicitly the weakest one; the
architecture-level findings (missing edit API, unguarded constitutional overwrite,
52x cost amplification, key-resolution fragility) do not depend on session length
and are the load-bearing evidence for this model.

---

## Invariants

*Statements that hold everywhere, regardless of which unit is being built.*

1. **The index is the map; the files are the territory.** `.elfmem/index.db` never
   holds information that cannot be reconstructed from `.elfmem/memory/**.md` by
   `elfmem index` with zero LLM calls. Anything that can't be rebuilt this way is a
   bug, not a feature. (research doc §4.1, quoting MOSS: *"the corpus is never
   altered, chunked into oblivion, or replaced by derived representations."*)
2. **`self.md` is never a block-table row.** It is read directly into the `self`
   frame; it is never superseded, never decayed, never subject to `consolidate()`.
   No supersession path can reach it because it was never a row to begin with —
   this is a structural guarantee, not a guard (plan doc Iteration 4, S3).
3. **A block's `id:` is permanent and content-independent**, assigned once at
   creation. `sha256(content)` becomes a secondary field, used only to decide
   whether re-embedding is needed. Reinforcement count, decay clock, and edges key
   off `id:`, not content hash — otherwise every edit orphans the block's history
   (research doc §4.2).
4. **No LLM call mutates memory directly.** Every change to `.elfmem/memory/**.md`
   is either (a) a deterministic operation — `learn` (append), `edit`, `forget`,
   `promote` — or (b) a human-approved `--apply` of a proposal file an LLM only
   *drafted*. Reasoning proposes; a human or deterministic code applies (plan doc
   §5.4, "proposal, not mutation").
5. **A pinned block (`pinned: true` in frontmatter, superseding today's
   `self/constitutional` tag-based guard) is never proposed for removal and is
   always included in its frame.**

---

## Architecture

> Markdown files under `.elfmem/memory/` are the sole source of truth; SQLite
> (`.elfmem/index.db`) is a zero-LLM-cost, disposable, rebuildable derived index;
> all LLM-mediated curation moves from per-block write-time mutation to
> corpus-level, proposal-only, human-approved review, with the review's reasoning
> supplied either by elfmem's own optional gateway (headless/no-host-agent) or by
> whichever host agent invokes the `elf-review` skill (interactive).

This is Iteration 5 of the plan doc's Simulation I (substrate) composed with
Iteration 4 of the research doc's Simulation II (reasoning ownership) — both
already run, against ten and eleven frozen scenarios respectively, converging
cleanly with no further improvement found (Iteration 6 in each case regressed).
This model does not re-run Choose; it inherits the decision.

**What becomes free.**

| Property | Free because |
|---|---|
| Constitutional block can never be silently destroyed by supersession (S3) | `self.md` was never a row — no supersession path exists, not merely guarded |
| "What changed in memory last month" audit (S9) | `git log -p .elfmem/memory/` — no mutation-log table to build or maintain |
| Constitution survives 6+ months unattended (S10) | Survives by construction as a version-controlled file |
| Fresh install needs no LLM API key for the common case (S5, S9 of Sim II) | Nothing is auto-added, so nothing needs curating; `elf-review`'s reasoning is borrowed from the host session |
| Undo any memory change | `git revert` — no bespoke undo mechanism |
| "Edit a block" is a text edit with a visible diff | Files are the source of truth; no round-trip through an opaque mutation API required |

**Rejected, and why.**

| Candidate | Failed on | Or converged into |
|---|---|---|
| Iteration 2: CRUD + pin guard, SQLite unchanged (= today's shipped Steps 1–2) | Cost (S4) and simplicity untouched — a table, a column, three methods on top of 23.5k LOC | Kept as the emergency patch that bought time, not the destination |
| Iteration 6: drop SQLite entirely, pure markdown + grep | S7 (scale to 14,000 blocks) fails hard — no semantic retrieval, no budget mechanism | Confirms the index is not incidental; it buys the entire scale story |
| Sim II Iteration 3: replace `frame()`/`recall()` themselves with a `ctx`-style skill, no index at all | S2 fails twice (no human to checkbox-review an autonomous loop's every call; grep can't rank by recency/reinforcement/centrality), S3 fails (no host agent at 3am for cron), S4 fails (non-Claude-Code peer has no `SKILL.md`) | Confirms exactly where the occasional/autonomous split holds |
| Sim II Iteration 5: drop embeddings from the index too | Silent regression on vocabulary-mismatch queries — grep cannot solve this, embeddings can | Kept as an explicit `index.embeddings: auto\|always\|never` opt-out, not the default |

---

## Generative core

*A concept earns a row only if removing it makes something in the spec
inexpressible.*

| Concept | What it is | Inexpressible without it |
|---|---|---|
| **Stable block identity** (`id:` frontmatter field, content-hash-independent) | A permanent identifier assigned once at block creation, carried in frontmatter | Editing a block without losing its reinforcement/decay/edge history; the entire "files are hand-editable" promise breaks on the first edit without this |
| **L1/L2 authority split** | Files are truth; the index is derived, disposable, rebuildable with zero LLM calls | "The index can be deleted without loss" (S7, S9, S10 all route through this) — without the split there is no derived/authoritative distinction to reason about at all |
| **Markdown block format with frontmatter** (`id`, `tags`, `pinned`, `created`) | The concrete syntax carrying block metadata in a human-editable file | Metadata (pin state, tags, id) existing outside the DB — without a format, "edit the file" can't also mean "edit the metadata" |
| **Corpus-level, proposal-only review engine** (Discover → Judge → Worksheet → Apply, one LLM call over the whole corpus, reasoning-source-agnostic) | The mechanism that replaces pairwise write-time curation | Cutting LLM cost from 20.7 calls/block to ~1 call per review cycle (S4); also the single mechanism addressing RC1 (control), RC2 (cost) and RC3 (audit) at once, per the plan doc's own claim |
| **Reasoning-ownership seam** (review's LLM call is bound to either the host agent invoking `elf-review`, or elfmem's own optional L3 gateway) | Where the review engine gets its LLM | A solo user never needing an API key for the common case (Sim II S9); also what keeps `elf-review`'s cost off elfmem's bill and onto the host session's turns |
| **`elfmem index` rebuild** | The zero-LLM operation that derives L2 from L1 | The L1/L2 split being anything more than a claim — without a rebuild path, "derived" is just an assertion |

**Note on collapse found during interpretation:** the plan doc's `elfmem review`
(§5.4, CLI command using elfmem's own gateway) and the research doc's `elf-review`
skill (§5, host-agent reasoning) are described in two places as if they were two
mechanisms. They are not — same Discover → Judge → Worksheet → Apply pipeline,
same proposal-file format, same `--apply` semantics. The only difference is *which
LLM answers the Judge step*. Modelled here as **one review engine with a pluggable
reasoning source**, not two review implementations. This is a real reduction, not
just a naming exercise — building two would mean two proposal-file parsers, two
worksheet UIs, two sets of tests for the same Apply logic. See "Amendments applied
to the spec" below.

---

## Derived from the core

| Named in the spec | Derived from | How | Rent the collapse pays |
|---|---|---|---|
| `elfmem export --to-markdown` (migration Phase 1) | Block format + stable identity | One-time tool: read every active/archived block row, assign `id:` (reuse existing `sha256(content)[:16]` as the seed the first time, since no block has a permanent id yet), write frontmatter + content to `.elfmem/memory/**.md` | A single well-tested exporter, not export logic duplicated per migration phase |
| `elfmem promote <id> --to notes/x.md` | Mutation API (file move) + block format | Move a `## heading` block between `log/YYYY-MM.md` and `notes/*.md`, frontmatter unchanged | No new persistence concept — it's `edit` + `forget` composed |
| `elfmem index --check` | `elfmem index` rebuild + parser | Same parse path as rebuild, dry-run, report malformed frontmatter | No second parser to maintain in sync |
| Migration Phase 4 verification (10 fixed queries, parity check) | `frame()`/`recall()` (unchanged) + `elfmem index` | Run existing retrieval before and after migration, diff the top-5 sets | Not a new retrieval mechanism — reuses L3 as-is against two index states |
| `elf-recall` skill | `ctx`'s Discover mechanics, forked (not shared as a dependency — see spec defect below) | Live `ripgrep` over `.elfmem/memory/`, unranked, no index | Proven pipeline reused rather than reimplemented from scratch |
| Retrieval budget-driven selection (`corpus <= budget → return all`, `> budget → hybrid`) | Existing 7-stage `hybrid_retrieve()` in `memory/retrieval.py` (prefilter → vector → BM25 → RRF → graph expand → composite score → MMR) | Unchanged mechanism, re-pointed at the rebuilt index | Zero rewrite of the retrieval stack — this is explicitly **not** part of Steps 8–9's core |
| Config/gitignore/onboarding template changes | Existing `ElfmemConfig`/`ReviewConfig` (already has a `corpus: CorpusReviewConfig` sub-tree from step 6a) | Extend `ReviewConfig`, not a new config tree | One config surface for all three review flavors (constitutional / staleness / corpus-LLM) instead of three |
| Gateway profiles, `api_key_env`, `doctor --resolve` | Already shipped (Step 5) | Reused as-is by the now-optional L3 gateway | Nothing to build — flagged only so it isn't mistaken for in-scope work |
| **Peer bundle import** (resolved via `/scout` + `/simulate` — see "Spec defects" below) | The append-only log convention already chosen for `learn()` + stable `msg_id` provenance + existing `merge_peer_evidence()` math | Peer messages append to the log tagged `source_peer:`/`msg_id:`; `elfmem index` rebuild groups entries by `msg_id` before calling `merge_peer_evidence()` once per distinct message | No new persistence mechanism — the only new logic is "group by `msg_id` before merge," which has to exist somewhere regardless of substrate. Also fixes a **currently-shipped, confirmed double-count bug**: `_import_single_block` matches by `content_id = sha256(content)`, so a verbatim resend and a distinct same-content message are indistinguishable today and both re-run the merge arithmetic |

---

## Domains

### Core

*Differentiators. Built first, most rigour. This is what becomes the constitution.*

| Concept | Definition | Defined in | Depended on by |
|---|---|---|---|
| Stable block identity | Permanent `id:`, content-hash as secondary field | Block format (new) | Everything that touches a block after this migration |
| L1/L2 authority split | Files=truth, index=derived/disposable | Architecture decision (this doc) | All Steps 8 units |
| Corpus-level proposal-only review engine | One LLM call, proposal file, human `--apply` | New `operations/` module | `elfmem review` (Step 8) and `elf-review` skill (Step 9) both bind to it |
| Reasoning-ownership seam | Pluggable Judge-step LLM source | Review engine's design | `elf-review` skill vs. L3 gateway path |

### Supporting

*Necessary and specific to this product, not differentiating.*

| Concept | Definition | Defined in |
|---|---|---|
| Markdown block/frontmatter parser | Reads/writes `## heading` + HTML-comment frontmatter | New `memory/` module — **single owner, see Seams** |
| `elfmem index` rebuild | Zero-LLM derivation of L2 from L1 | New, replaces DB-native write paths in `consolidate.py`/`memory/blocks.py` |
| Mutation API → file ops | `edit`/`forget`/`ls`/`promote` operate on files, not rows | `api.py` (existing methods re-pointed) |
| Migration tooling | Export, phase-gate verification, hand-restore | New, one-shot |

### Generic

*Solved elsewhere. Never the interesting part.*

| Concept | Provided by |
|---|---|
| Versioning, diff, undo | git — "obtained for free" (research doc §4.5) |
| Live grep retrieval for `elf-recall` | `ctx`'s Discover mechanics (forked pattern, not a shared dependency — open decision 5) |
| Hybrid ranked retrieval (FTS5 + vector + RRF) | Already exists, `memory/retrieval.py`, unchanged |
| LLM gateway / profile resolution | Already shipped, Step 5 |

---

## Pace layers

| Layer | Rate | Contains |
|---|---|---|
| Slow (build first) | slow | Block identity scheme (`id:` format), L1/L2 authority split, frontmatter format, migration Phase 0–4 (export + parity gate) |
| Fast (build after, revisit often) | fast | Review worksheet UX, `elf-recall` skill polish, `promote` command, file/directory organisation (Taxonomy Contract P1–P5 — explicitly meant to be periodically re-decided by `elf-review`, never fixed once at migration time, per research doc §4.7) |

---

## Seams

*Path is the concrete file/path this seam controls. Owner unit must be a real unit id
from `build-plan.md` — `validate_plan.py` cross-checks the two documents agree.*

| Seam | Path | Owner unit | Protocol |
|---|---|---|---|
| Markdown frontmatter parser | `src/elfmem/memory/blockfile.py` | U-001 | `sole owner` — index rebuild, mutation API, `elf-recall`, and migration export must all call this, none may re-parse independently |
| Index write path | `.elfmem/index.db` | U-002 | `sole owner` — no other unit writes to index.db directly; `frame()`/`recall()` only read it |
| Review config surface | `src/elfmem/config.py` | U-008 | `growable by injection` — add a sibling field/sub-config to `ReviewConfig` for the new corpus-LLM review (already has `corpus: CorpusReviewConfig` from step 6a), not a new top-level config tree |
| Peer bundle import (landing) | `src/elfmem/operations/peer.py` | U-012 | `append-only` — writes to the log only, never mutates |
| Peer bundle reconciliation | `.elfmem/index.db` | U-002 | `sole owner` — reconciliation (via the log-folding extension point) happens exactly once, at rebuild/review time, never at import time; U-012 satisfies this as an injected dedup function, not an edit to U-002's file |
| Peer bundle export | `src/elfmem/operations/peer.py` | U-012 | `sole owner` |

CLI command registration (`src/elfmem/cli.py`, Typer `@app.command()`) is
deliberately **not** listed as a seam requiring an owner: Typer's decorator
registry is already `growable by injection` as shipped infrastructure — U-003,
U-004, and U-012 each add commands without editing a shared dispatch table or
one another's code, so there is no unowned risk here to track.

---

## Concept graph

```
stable block identity (id:)
        │
        ▼
markdown frontmatter format ──────► elfmem index (rebuild, L1→L2)
        │                                   │
        ▼                                   ▼
mutation API (edit/forget/ls/promote)   frame()/recall() (unchanged, re-pointed)
        │
        ▼
corpus-level review engine (Discover→Judge→Worksheet→Apply)
        │
        ├──► elfmem review (Step 8, L3 gateway reasoning — headless/cron)
        └──► elf-review skill (Step 9, host-agent reasoning — interactive)

elf-recall skill (Step 9) ──► forked from `ctx` ──► reads markdown frontmatter
   (independent of the index; can disagree with frame()/recall() — labelled risk S11)

migration tooling (export, parity gate) ──► depends on: frontmatter format,
                                             elfmem index, frame()/recall()

peer bundle import ──► append-only log (same convention as learn())
                              │
                              ▼
                       elfmem index rebuild ──► group by msg_id ──► merge_peer_evidence()
                              │                  (fixes live double-count bug;
                              │                   resolves ADR 0005 Phase 5)
                              ▼
                       corpus-level review engine (promotion into notes/, same as any other content)

peer bundle export ──► reads elf's own notes/log (independent of the import path)
```

---

## Spec defects found during interpretation

| Defect | Severity | Destination |
|---|---|---|
| ~~**Peer bundle export/import has no owner in the new architecture.**~~ **RESOLVED** (2026-08-09, via `/scout` decision-mode research + `/simulate` optimize loop, 3 candidates × 7 scenarios). Winner: peer messages append to the same append-only log convention already chosen for `learn()`, tagged `source_peer:`/`msg_id:`; `elfmem index` rebuild groups by `msg_id` before running the existing `merge_peer_evidence()` once per distinct message. Rejected: DB-native-with-exemption (violates the L1/L2 invariant outright, and reintroduces the original unguarded-supersession bug for peer content specifically) and file-native-with-write-time-mutation (races under concurrent same-peer sends; doesn't solve resend/collision without the same `msg_id` ledger this design already has). External research found no verified precedent for markdown-as-truth peer sync specifically, but did verify (Graphiti/SEDM, 2025-2026): idempotent merge via scoped identity reuse not exact-hash matching, explicit non-double-counting reconciliation on merge, and non-destructive soft-invalidation — this design satisfies all three. **Bonus finding**: simulation surfaced that `_import_single_block` (shipped code) matches by `content_id = sha256(content)`, so it **already cannot distinguish a resend from a distinct same-content message** — a live double-count bug, not a hypothetical one — and this design fixes it as a side effect. It also resolves [ADR 0005](../../../decisions/0005-peer-protocol-hardening.md)'s deferred Phase 5 (`msg_id` collision on repeat-content sends), whose own stated reopen trigger — "wire-format evolution" — this migration is. **One residual risk, not a failure**: deferred reconciliation uses `peer_trust` *at rebuild time* rather than *at receipt time*; document this as an intentional behavior change (arguably more correct — applies the operator's latest trust judgment) when the unit is built. Recorded as amendment D-002 below. | Resolved — High severity retired | Fed into Seams and "Derived from the core" above; owner assigned |
| **`elfmem review` (Step 8, CLI/gateway) and `elf-review` (Step 9, skill) are describable as the same engine with a pluggable reasoning source**, but the two source documents present them as separate deliverables in separate steps. | Medium | Resolved here as a modelling collapse (see Generative core note above) — not a spec-behaviour change, just a build-organisation simplification. Recorded as an amendment below. |
| **One-file-per-block vs. many-per-file is explicitly left as "working assumption, needs periodic re-check"** (§4.7, open decision 3) rather than resolved. Building the parser to assume one shape and discovering the other is needed mid-migration is a real rework risk. | Medium | Needs elicitation before Stage 3 unit contracts are written for the parser — either the user picks a starting convention now, or the parser is built format-agnostic from the start (higher initial cost, no rework risk) |
| **`self.md`-as-file vs. constitutional-blocks-in-retrieval-scoring — resolved by consequence, not a fresh choice.** Reading `self.md` as always-included preamble is Architecture M from ADR 0003, previously deferred and measured at +33pp under drift / −7pp under stability. **Re-examined**: Invariant 2 ("self.md is never a block-table row") is not new here — it was already load-bearing in the architecture the user approved when choosing "commit now on single-instance evidence" (Iteration 4/5, the winning design in both source documents). Once self.md is never indexed/embedded, there is no ranking-based alternative to "always included as preamble" — nothing else *could* selectively surface "parts of self.md," since nothing about it participates in retrieval scoring at all. This is a logical entailment of an already-made decision, not an independent one. | Resolved by entailment | Proceed on this basis. Still needs its own ADR when Step 8 ships (citing this document and superseding ADR 0003's deferral, since production evidence — not new reasoning — is what changed), same pattern as ADR 0009/0010 |

---

## Amendments applied to the spec

| Amendment | Behaviour preserved? | Decision id |
|---|---|---|
| Collapsed `elfmem review` (Step 8) and `elf-review` (Step 9) into one review engine with a pluggable reasoning source, instead of two separate implementations | yes — same Discover→Judge→Worksheet→Apply behaviour from both source documents, only the build organisation changes | D-001 |
| Peer bundle import moves from DB-native, write-time arithmetic merge to append-only file log + rebuild-time, `msg_id`-deduplicated merge. **Not purely behaviour-preserving** — it fixes a live double-count bug (see resolved defect above) as a deliberate side effect, and changes which `peer_trust` value a merge uses (rebuild-time vs. receipt-time) | no — two behaviour changes, both intentional and net-positive, both need to survive in an ADR when Step 8 ships (candidate: cites this document + [ADR 0005](../../../decisions/0005-peer-protocol-hardening.md)) | D-002 |

---

## Model drift log

*Three entries after building U-001, U-002, and U-012 — all real. Two are
about U-002 (different concepts); the third (U-012) is the same *pattern* as
both — a unit's actual build surfacing a real interaction the plan hadn't
spelled out — appearing for a third time. Per the guide, repeated entries are
the signal to revise the interpretation, not work around it again: **the
pattern itself is now the finding.** The generative core (model.md's
"Generative core" table) treats the block format (U-001) as settled
infrastructure that later units simply use. Three times now, a later unit
has instead discovered a real constraint the format didn't anticipate
(edges have no representation; `self.md` wiring has no owner; per-file
duplicate-`id:` collides with legitimate same-content peer messages). The
format is not wrong, but it is less finished than "generative core" implied
— treat U-001 as still-settling through Wave 3, not closed after Wave 1.*

| Date | Unit | Concept needed | Resolution |
|---|---|---|---|
| 2026-08-09 | U-002 | Graph-edge reconstruction from files — the model's "Derived from the core" and build-plan.md's Done-when both assumed `elfmem index` rebuild reproduces edge rows, but the block format (U-001) has no way to encode an edge in frontmatter | Scoped out of U-002: edges are DB-only and lost on a full rebuild, same as the already-disclosed α/β trade-off in model.md's residual risks — extended to cover edges too, not a new risk class. If edges need to survive rebuild, that's a frontmatter-format decision for U-001's format (not yet made) — flagged, not resolved |
| 2026-08-09 | U-002 | Wiring `self.md` into `frame('self', ...)` — model.md's Invariant 2 and the resolved `self.md`-as-preamble defect both describe this as settled, but no unit in build-plan.md actually owns `context/frames.py` or `operations/recall.py`, which is where the wiring would happen | **Real coverage gap, not yet assigned to any unit** — U-002 exposes `self.md` content via `RebuildResult.self_content` (proving it's available, not lost) but does not wire it into `frame()`. Added to build-plan.md's Coverage table as unassigned |
| 2026-08-10 | U-012 | U-001's per-file duplicate-`id:` invariant (correct and desirable for ordinary notes/log content) collides with landing two distinct peer messages that share identical content in one file — the exact scenario U-012 exists to fix, blocked by the format it's built on | Fixed at the landing layer, not by weakening U-001's invariant: each peer message lands in its own file (`log/peer/<peer>-<msg_id>.md`) rather than a shared monthly file. Turned out to be a strict improvement (better concurrency story too), but the *need* to redesign came from the format, not from the reconciliation logic `/simulate` had already stress-tested |
| 2026-08-10 | U-005 (via a real-data dry run, not a unit build) | **U-001's own parser had a real bug**, not a coverage gap this time: every `##` line was treated as a block boundary, but real content (Theory-of-Mind blocks especially) legitimately contains its own `##` sub-headings. Confirmed on the real 185-block production corpus — 15 blocks mis-split into 158 on rebuild, invisible to every hand-written fixture because none happened to contain a nested heading | **Fixed in U-001 directly** (`docs/plans/v2_substrate/plan/dry_run_2026-08-10.md`), not deferred — a boundary now requires either no prior block, a bare (frontmatter-less) current block, or the candidate line itself being followed by frontmatter. Two new regression tests. Re-run confirmed block count now matches exactly (140=140) on the real corpus. This is the fourth "unit's real build/use surfaces a gap U-001 didn't anticipate" entry — treat the format as validated against real data now, not just its own fixtures, but still revisit this pattern if a fifth instance appears |
