# Build plan — elfmem v2, Steps 8–9

*Disposable. Changes constantly. Contract depth: Waves 1–2 `full` (the tracer, built
first), Wave 3 `sketched` (next up), Waves 4–8 `named` (beyond — coverage assigned,
detail deferred to the pass that plans that wave).*

**Spec:** `docs/research/v2_substrate_and_reasoning_ownership_research.md` +
`docs/plans/plan_v2_substrate_reevaluation.md` · **Model:** `model.md` ·
**Constitution:** `C1` · **Scale:** 12 units

**Validated by:** `python3 docs/plans/v2_substrate/plan/validate_plan.py docs/plans/v2_substrate/plan --spec docs/research/v2_substrate_and_reasoning_ownership_research.md --write-lock`

## Architecture selection

*Not run fresh here — inherited. Both source documents already ran Choose
(the plan doc's Simulation I, §6; the research doc's Simulation II, §5) before this
plan existed. Recorded for completeness, not re-derived.*

**Fitness function**, ranked (plan doc §6, research doc §5.3):

1. Control — can the operator deterministically change/remove any memory?
2. Integrity — can memory be silently destroyed?
3. Cost — LLM calls to maintain steady state
4. Simplicity — source lines and concepts a maintainer must hold
5. Scale headroom — behaviour as the corpus grows 100x

**Frozen scenarios** (plan doc §6 S1–S11, research doc §5.3 S1–S10 — see `model.md`
"Rejected, and why" for the full trace; not reproduced here to avoid the
never-excerpt-long-normative-text rule).

**Candidates traced:** DB-native + CRUD patch · corpus-level review replacing pairwise
· files-as-truth/index-derived · pure-markdown-no-index · skill-borrowed reasoning
vs. elfmem-owned gateway.

**Winner:** Iteration 5 (substrate) × Iteration 4 (reasoning ownership) — see
`model.md` Architecture section for the one-sentence statement and the full
rejected-candidates table.

## The first domino

**Chosen:** U-001, U-002 (Wave 1–2)

**Why this one:** exercises Invariant 1 (index is derived, files are truth,
zero-LLM rebuild) and Invariant 3 (stable id survives edits) — the two invariants
everything else in Steps 8–9 is built on top of. Deliberately excludes the
mutation API, migration of the real production corpus, and the review engine —
those are additive once the substrate mechanism is proven.

**Scoped by irreversibility.**

| Mechanism | Why it cannot wait |
|---|---|
| Stable `id:` independent of content hash | Retrofitting it after blocks have been edited without it means every existing block's history is already lost — this is the irreversibility class named in the sequencing guide by example |
| Index-is-fully-derived (nothing DB-only) | Building anything DB-native-with-an-exception first (as Iteration 1/2 of the substrate simulation did) means auditing every write path later to remove the exception — exactly what happened to the peer-bundle gap this plan already had to resolve once |

### Mechanisms in the tracer

*Test names are pytest node ids (this project's actual test convention — see
`map.md`), not a spec-declared id scheme: the source documents have no
formal acceptance-criteria ids (Stage 0 readiness gap), so these are minted
here, at the point they become machine-checkable, rather than invented
upstream in the spec.*

| Mechanism | Success path | Rejection path |
|---|---|---|
| Frontmatter parser | `tests/test_blockfile.py::test_parses_wellformed_block_file` | `tests/test_blockfile.py::test_malformed_frontmatter_reported_not_skipped` — `elfmem index --check` reports it, does not silently skip |
| Stable `id:` | `tests/test_blockfile.py::test_edit_preserves_id_and_history` — `id:`, reinforcement count, decay clock survive rebuild unchanged | `tests/test_blockfile.py::test_duplicate_id_in_same_file_errors` — does not let one block silently shadow the other |
| `elfmem index` rebuild | `tests/test_index_rebuild.py::test_rebuild_matches_reference_fixture` — zero LLM calls (asserted via `MockLLMService` call count) | `tests/test_index_rebuild.py::test_missing_memory_dir_fails_loudly` — fails with `.recovery`, does not silently produce an empty index |
| `self.md` never a row (Invariant 2) | `tests/test_index_rebuild.py::test_self_md_appears_in_self_frame` | `tests/test_index_rebuild.py::test_self_md_absent_from_ls_listing` — proves it never entered the block table, not merely that it's excluded from a filter |

**Explicitly excluded from the tracer** (additive, however central): mutation API
(U-003), migration of the real corpus (U-004–U-007), the review engine (U-008),
both skills (U-010, U-011), peer bundle import (U-012).

## Increment ladder

| # | Increment | Type | Usable because | Gate question |
|---|---|---|---|---|
| 0 | Inherited gates | n/a | `uv run ruff check` / `mypy src` / `pytest` already exist and pass (1,164 tests) — no scaffold work needed | none |
| 1 | Waves 1–2: format + rebuild | **tracer** | A block can be hand-written as a file and appear in the index; editing it doesn't lose history | none (developer-facing) |
| 2 | Waves 3–6: mutation API + full migration + peer-import fix | **dogfoodable** | The actual differentiating behaviour the redesign exists for: "edit a block" is a text edit with a `git diff`, on the real 140-block production corpus, with peer sync intact | "Did editing your own memory feel like editing a file, or did it still feel like fighting an API?" |
| 3 | Wave 7: review engine | | LLM cost drops from ~20.7 calls/surviving block toward ~1/review cycle — the other half of RC2 | "Did a review cycle surface real duplicates/staleness, or was it noise?" |
| 4 | Wave 8: `elf-review` skill | | Review's reasoning is borrowed from the host session, no elfmem-owned API key needed for the common case | "Did running review from inside a Claude Code session feel natural, or bolted-on?" |

## Waves

| Wave | Execution | Units | Notes |
|---|---|---|---|
| 1 | sequential, narrow | U-001 | The spine — nothing else can start without the format |
| 2 | sequential | U-002 | Needs U-001; almost everything downstream needs this too, so it stays sequential rather than forced-parallel |
| 3 | parallel (4 units — at the sequencing guide's stated upper bound for incident-free batches) | U-003, U-004, U-010, U-012 | Leaf-shaped: `api.py` (U-003), new export tool (U-004), new skill directory (U-010), `operations/peer.py` (U-012) — no shared owned files. U-012 satisfies U-002's log-folding extension point (`growable by injection`) rather than editing U-002's file |
| 4 | sequential, integration | U-005 | The gate — migration Phase 4, retrieval parity on 10 fixed queries. Needs U-004's export and U-002's rebuild |
| 5 | sequential | U-006 | Hand-restore constitutional roles + flip authority. Needs U-005 passed |
| 6 | sequential, integration | U-007 | Decommission old DB-native write paths. Needs U-006 **and** U-012 (see ordering constraint below) |
| 7 | sequential, narrow | U-008 | The review engine — core, high-risk, built once the substrate is stable under it |
| 8 | sequential | U-011 | `elf-review` skill wrapper around U-008. (U-010, `elf-recall`, already shipped in Wave 3 — it only needed the format, not the review engine) |

## Ordering constraints that are not negotiable

*Retrofit-cost and irreversibility constraints dependency analysis cannot derive.*

- **U-012 (peer-import file landing) must ship no later than U-007
  (decommission old DB-native write paths),** because U-007 removes
  `import_bundle`'s direct-DB-row write path — decommissioning it before its
  replacement exists would break peer sync outright. Not a pure dependency (U-012
  only technically needs U-001/U-002), but a hard ordering constraint discovered
  during `/simulate` — declared here per the sequencing guide rather than left to
  a topological sort that would happily schedule it after and break peer sync.
- **U-001's stable `id:` scheme exists from Wave 1,** because retrofitting it
  after any block has been edited under content-hash identity means that block's
  reinforcement/decay/edge history is already unrecoverable — the irreversibility
  class named in the sequencing guide by example, not a hypothetical here.

## Units

*Closure, per unit: can this unit reach green using only paths it owns? `Owns`
covers its own test files at the location `map.md` states; `Verified by`'s filter
selects those paths.*

---

## U-001: Block format + frontmatter parser
**Phase:** v2 Step 8
**Wave:** 1
**Depth:** full
**Execution:** sequential
**Owns:** `src/elfmem/memory/blockfile.py`, `tests/test_blockfile.py`
**Touches:** (none)
**Needs:** (none)
**Implements:** research doc §4.2 (permanent `id:`), plan doc §5.2 (block format),
research doc §4.7 (Taxonomy Contract — working assumption: many-per-file, `##`
heading per block, matching `ctx`'s convention per `map.md`'s open-issues note);
D-001 (model.md)
**Risk:** high — foundational; every other unit inherits whatever this gets wrong;
the "elfmem is ahead of the published field, not behind it" claim (research doc
§4.2) rests entirely on this working correctly
**Done when:** `test_parses_wellformed_block_file`,
`test_malformed_frontmatter_reported_not_skipped`,
`test_edit_preserves_id_and_history`, `test_duplicate_id_in_same_file_errors`
(all in `tests/test_blockfile.py`) green; round-trip (parse → write → parse) is
byte-stable on fixtures covering: no frontmatter, malformed frontmatter,
duplicate `id:`, `pinned: true`, a constitution-mode file with no block headings
**Verified by:** `uv run ruff check src/elfmem/memory/blockfile.py && uv run mypy src/elfmem/memory/blockfile.py && uv run pytest tests/test_blockfile.py`
**Out of scope:** index rebuild (U-002); any mutation logic beyond parse/write
primitives; peer-specific frontmatter fields (`source_peer`/`msg_id` — defined
here as an extensible frontmatter schema, but peer semantics belong to U-012)

---

## U-002: `elfmem index` rebuild (L1 → L2 derivation)
**Phase:** v2 Step 8
**Wave:** 2
**Depth:** full
**Execution:** sequential
**Owns:** `src/elfmem/memory/index_rebuild.py`, `.elfmem/index.db`, `tests/test_index_rebuild.py`
**Touches:** (none)
**Needs:** U-001
**Implements:** plan doc §5.1 (L2 INDEX layer), model.md Invariant 1
**Risk:** high — this is the load-bearing proof that "derived" is more than an
assertion (model.md, Generative core row 6)
**Done when:** `test_rebuild_matches_reference_fixture`,
`test_missing_memory_dir_fails_loudly`, `test_self_md_appears_in_self_frame`,
`test_self_md_absent_from_ls_listing` (all in `tests/test_index_rebuild.py`)
green; rebuilding from `.elfmem/memory/` reproduces identical
block/embedding/FTS/graph-edge rows to a reference fixture with zero LLM calls
(asserted via `MockLLMService` call-count == 0); ships a documented extension
point for additional append-only log sources beyond `log/YYYY-MM.md` (the
`growable by injection` seam U-012 satisfies in Wave 3 without editing this file)
**Verified by:** `uv run ruff check src/elfmem/memory/index_rebuild.py && uv run mypy src/elfmem/memory/index_rebuild.py && uv run pytest tests/test_index_rebuild.py`
**Out of scope:** mutation API (U-003); migration of the real corpus (U-004);
peer-specific dedup logic (U-012 supplies the `msg_id`-keyed dedup function as a
plugin to the extension point this unit ships, not as an edit to it)

---

## U-003: File-native mutation primitives
**Phase:** v2 Step 8
**Wave:** 3
**Depth:** full (revised from `sketched` while building — see below)
**Owns:** `src/elfmem/memory/file_mutation.py` (revised from `src/elfmem/api.py`
— see note), `tests/test_file_mutation.py` (revised from
`tests/test_block_mutation.py`)
**Touches:** (none)
**Needs:** U-001, U-002
**Implements:** plan doc §5.3 (the mutation API table)
**Risk:** medium
**Done when:** `test_edit_preserves_id_updates_content`,
`test_forget_removes_block_idempotent`, `test_list_blocks_filters_by_tag`,
`test_promote_moves_between_log_and_notes` (all in
`tests/test_file_mutation.py`) green
**Verified by:** `uv run ruff check src/elfmem/memory/file_mutation.py && uv run mypy src/elfmem/memory/file_mutation.py && uv run pytest tests/test_file_mutation.py`
**Out of scope:** wiring these into `MemorySystem.edit()`/`.forget()`/`.ls()`
— those still call the DB-native path until U-006 flips authority. This unit
is the file-native primitive layer only.

**Revision note (found while building, not at packet-render time):** the
original contract named `src/elfmem/api.py` directly — re-pointing
`MemorySystem`'s public methods at files. That can't correctly happen yet:
the live system is still DB-primary until migration (U-004–U-006) completes,
so re-pointing the public API now would silently split "what the API returns"
from "what's actually authoritative" mid-build. Rescoped to a new, independent
module operating purely on `.elfmem/memory/`, with the actual `api.py`
re-pointing deferred to U-006 (flip authority) — a new coverage item, not
covered by any existing unit; see Coverage table below.

---

## U-004: Migration export (`elfmem export --to-markdown`)
**Phase:** v2 Step 8
**Wave:** 3
**Depth:** sketched
**Owns:** `src/elfmem/migration/export.py`, `tests/test_migration_export.py`
**Needs:** U-001
**Implements:** plan doc §8 Phases 0–2

---

## U-005: Migration Phase 4 — retrieval parity gate
**Phase:** v2 Step 8
**Wave:** 4
**Depth:** full (promoted while building)
**Owns:** `src/elfmem/migration/parity.py`, `tests/test_migration_parity.py`
**Touches:** (none)
**Needs:** U-002, U-004
**Implements:** plan doc §8 Phase 4 (the gate — "do not proceed on the assumption
a diverging ranking is probably fine")
**Risk:** medium — a false PASS here is worse than a false FAIL, since it's
the last check before Phase 5/6 touch the real corpus
**Done when:** `test_identical_states_pass_the_gate`,
`test_block_count_mismatch_fails_the_gate`,
`test_diverging_query_results_fail_the_gate`,
`test_frame_with_no_query_still_compared` (all in
`tests/test_migration_parity.py`) green
**Verified by:** `uv run ruff check src/elfmem/migration/parity.py && uv run mypy src/elfmem/migration/parity.py && uv run pytest tests/test_migration_parity.py`
**Out of scope:** running the gate against the real production corpus — this
unit builds and verifies the mechanism against synthetic fixtures only. Point
it at `~/.elfmem/databases/elfmem.db` (or its Phase 0 backup) and a rebuilt
`index.db` is a separate, explicit action for whoever runs the actual
migration, not something this unit does on its own.

**Design note (found while building):** compares `hybrid_retrieve()`, not
`recall()` — `recall()` reinforces returned blocks and edges as a side
effect, which would corrupt a read-only comparison (the second call would see
state the first call changed). Not a deviation from the contract, just a
detail the "named" depth left for this pass to discover.

---

## U-006: Migration Phase 5–6 — hand-restore roles + flip authority
**Phase:** v2 Step 8
**Wave:** 5
**Depth:** named
**Needs:** U-005
**Implements:** plan doc §8 Phases 5–6

---

## U-007: Decommission DB-native write paths
**Phase:** v2 Step 8
**Wave:** 6
**Depth:** named
**Needs:** U-006, U-012
**Implements:** plan doc §7 (deletion list: inbox/active/archived state machine,
`rescue.py`, remaining `consolidate.py` write paths). U-012 dependency is an
ordering constraint, not a pure technical one — see "Ordering constraints" above

---

## U-008: Corpus-level review engine (Discover → Judge → Worksheet → Apply)
**Phase:** v2 Step 8/9 boundary
**Wave:** 7
**Depth:** named
**Owns:** `src/elfmem/config.py`
*(the `ReviewConfig` extension only — named early because the Seams table needs
a real owner; the review-engine module's own path is undetermined until this
wave is planned at full depth)*
**Needs:** U-001, U-002
**Implements:** plan doc §5.4; model.md's collapsed-concept note (this unit *is*
both "`elfmem review`" and the engine `elf-review` wraps — one build, two bindings)

---

## U-010: `elf-recall` skill
**Phase:** v2 Step 9
**Wave:** 3
**Depth:** named
**Needs:** U-001
**Implements:** research doc §5.4 Iteration 4; open decision 5 (fork vs. depend on
`ctx` — unresolved, deferred to the pass that plans this wave at full depth)

---

## U-011: `elf-review` skill wrapper
**Phase:** v2 Step 9
**Wave:** 8
**Depth:** named
**Needs:** U-008
**Implements:** research doc §5 (D-001/D-003 from the `ctx` precedent)

---

## U-012: Peer bundle import — append-only log + rebuild-time reconciliation
**Phase:** v2 Step 8
**Wave:** 3
**Depth:** full (revised from `sketched` while building — see note)
**Owns:** `src/elfmem/operations/peer.py` (additive functions only — see
note), `tests/test_peer_file_import.py` (revised from `tests/test_peer_import.py`)
**Touches:** (none)
**Needs:** U-001, U-002
**Implements:** model.md's resolved peer-bundle defect (D-002); fixes the
confirmed live double-count bug in `_import_single_block`; resolves ADR 0005
Phase 5's deferred trigger
**Risk:** high — same evidence-arithmetic correctness bar as the code it
replaces (ADR 0002's Bayesian merge), plus new dedup logic
**Done when:** `test_peer_log_entry_appended_not_merged`,
`test_resent_msg_id_deduplicated_before_merge`,
`test_distinct_messages_same_content_both_counted`,
`test_fold_produces_correct_alpha_beta` (all in `tests/test_peer_file_import.py`) green
**Verified by:** `uv run ruff check src/elfmem/operations/peer.py && uv run mypy src/elfmem/operations/peer.py && uv run pytest tests/test_peer_file_import.py`
**Out of scope:** re-pointing `MemorySystem`'s live peer import/export at
this new path (belongs to U-007, same reasoning as U-003 — see next); export
re-pointing to files (deferred, not built this pass — flagged below)

**Revision note (found while building, consistent with U-003's precedent):**
the original contract implied editing `import_bundle`/`_build_bundle` in
place. Doing that now would change live peer-sync behaviour before migration
completes — the exact trap U-003 already hit and was rescoped around. Built
as new, additive functions in the same file instead
(`land_peer_log_entry`, `fold_peer_log`) — the existing DB-native
`import_bundle`/`_build_bundle` are untouched and still live until U-007
switches callers over. **Export re-pointing (reading from files instead of
DB rows) was not built this pass** — `fold_peer_log` (the import side) was
the part with a live bug to fix and a deferred ADR-0005 trigger to resolve;
export has neither pressure and is added to Coverage as a remaining item for
whoever promotes this unit's Wave-6 integration.

---

## Coverage

*Every spec claim assigned to at least one unit. Full traceability generated into
`plan.lock.yaml` on validator run — this table lists gaps until then.*

| Unassigned spec anchor | Why not yet assigned |
|---|---|
| Research doc §4.7 file/directory organisation re-check cadence ("periodically re-decided by `elf-review`") | Depends on U-008/U-011 existing first — not a Steps 8–9 build unit, it's an operating procedure for after they ship |
| Peer/mind/amendment subsystem reassessment (open decision 4, research doc §11) | Explicitly out of scope for this plan — mind/amendment subsystems were not touched by `/simulate`'s peer-bundle resolution; a separate model pass if the user wants them reassessed |
| **Wiring `self.md` into `frame('self', ...)`** — model.md Invariant 2 describes this as settled architecture, but no unit owns `context/frames.py`/`operations/recall.py`, where the wiring happens. Found during U-002 (see model.md's Model drift log, 2026-08-09) | Genuine gap, not yet assigned. U-002 exposes the content (`RebuildResult.self_content`); the wiring itself needs a small unit — likely folded into U-003's wave (same file territory) or its own — decide when U-003 is promoted to full depth |
| **Graph-edge reconstruction on `elfmem index` rebuild** — Done-when for U-002 originally assumed edge rows reproduce on rebuild; the block format (U-001) has no way to encode an edge. Found during U-002 (model.md's Model drift log, 2026-08-09) | Descoped from U-002: edges are lost on a full rebuild, consistent with the already-disclosed α/β trade-off. If this needs to change, it's a U-001 frontmatter-format decision, not yet made |
| **Re-pointing `MemorySystem.edit()`/`.forget()`/`.ls()` at the file-native primitives U-003 builds.** Originally assumed to be U-003 itself; found while building U-003 that this can't happen before migration completes (the live system is still DB-primary) | Belongs to U-006 (flip authority) — added as an explicit sub-task there, not a new unit |
| **Peer bundle export re-pointing** (reading from `.elfmem/memory/` files instead of DB rows) and **re-pointing live `import_bundle`/`_build_bundle` callers** at U-012's new file-native functions. Originally assumed to be part of U-012; the import-side fix (the live double-count bug, ADR 0005) had the actual pressure, export didn't | Belongs to U-006/U-007 alongside the other DB-native re-pointing work — same reasoning as the `api.py` gap above |

## Spec amendments

| Amendment | Kind | Behaviour changed? | Status | Decision id |
|---|---|---|---|---|
| Collapsed `elfmem review` and `elf-review` into one engine (U-008) with a pluggable reasoning source | naming / de-duplication | no | applied | D-001 |
| Peer bundle import: DB-native write-time merge → append-only log + rebuild-time, `msg_id`-deduplicated merge | contradiction resolution (fixes a live bug) + behaviour change (trust-value timing) | yes — two intentional, net-positive changes | applied | D-002 |

## Deferred with triggers

| Decision | Revisit when |
|---|---|
| One-file-per-block vs. many-per-file (working assumption: many-per-file adopted for U-001) | `elf-review` (U-011) exists and can periodically re-check against the Taxonomy Contract (P1–P5), per research doc §4.7 — not before |
| `elf-recall` fork vs. depend on `ctx` (open decision 5) | When U-010 is promoted to full depth (Wave 3 is next after this plan's initial pass) |
| Trust-value timing in deferred peer merge (rebuild-time vs. receipt-time `peer_trust`) | Document as intentional in U-012's contract when promoted to full depth; revisit only if an operator reports surprising behaviour after a trust change |
