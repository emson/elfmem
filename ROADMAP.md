# elfmem Roadmap

> **What this is**: the public-facing roadmap for elfmem. One source of truth.
> What's shipped, what's next, what we've considered and explicitly rejected.
>
> **What this is not**: a calendar. elfmem is solo OSS. Dates are illustrative;
> sequence is what matters.
>
> **Last reviewed**: 2026-05-25 (post-v0.19.0 release; original v0.19
> production-signal slot shifted to v0.20). Reviewed quarterly. Open
> issues at [github.com/emson/elfmem/issues](https://github.com/emson/elfmem/issues).

---

## Project axioms

These don't change between releases. They constrain what we ship.

- **Agent-first**: every public API serves the agent's read → call → interpret → next loop
- **Biological grounding**: four rhythms (heartbeat / breathing / sleep / deep sleep), four frames (self / attention / task / simulate)
- **No magic numbers** — hardcoded constants must be defensible from first principles, not from flashcard fits
- **SIMPLE · ELEGANT · FLEXIBLE · ROBUST** — `docs/coding_principles.md`
- **Ship minimum, measure, then earn each layer** — solo OSS cannot sustain unbounded complexity growth
- **SQLite + zero external services** — backwards-compatible, file-portable, no cloud lock-in

---

## Status legend

| Symbol | Meaning |
|---|---|
| ✅ | Released |
| 🚧 | In Progress |
| 📋 | Next (committed, not yet started) |
| 🔍 | Exploring (research, no commitment) |
| ❌ | Rejected (with reason) |

---

## Recently Released

| Version | Highlights | Date |
|---|---|---|
| ✅ **v0.19.3** | MCP entry default, drift detection, and migration — `mcp_json_snippet()` now resolves the running `elfmem` executable to an absolute path instead of a bare `"elfmem"` string (broke on project-local `uv` venvs); `elfmem serve --env-file` reliably delivers API keys to the spawned MCP subprocess instead of silently degrading to mock/no-op behaviour; `elfmem migrate`/`doctor --migrate-mcp` now scan the real `~/.claude.json` (previously omitted) and its nested `projects[path].mcpServers` shape, with a new drift check catching an MCP entry wired to a *different* project's config. Unplanned, bug-driven — found via elfmem's own dev instance drifting to an unrelated config/db with peer messaging silently broken ([#81](https://github.com/emson/elfmem/pull/81), [ADR 0008](docs/decisions/0008-mcp-entry-default.md)) | 2026-07-14 |
| ✅ **v0.19.2** | Bound and checkpoint consolidation for slow LLM adapters — `consolidation.contradiction_top_k` (default 10) caps contradiction-detection LLM calls per inbox block to the K most similar candidates, bounding worst-case cost to O(K) regardless of active-set size; `consolidation.max_inbox_per_run` (default 5) self-terminates `dream()`/`consolidate()` runs, surfaced as `--max` and `ConsolidateResult.inbox_remaining`; `rescore_blocks()` now commits per-block instead of one all-or-nothing transaction. Unplanned, bug-driven (same pattern as v0.19.0) — mitigates but does not fully close the `elfmem dream` kill-and-lose-progress failure mode; per-block commit durability inside `consolidate()` itself is a follow-up ([#78](https://github.com/emson/elfmem/pull/78), [ADR 0007](docs/decisions/0007-bound-and-checkpoint-consolidation.md)) | 2026-07-02 |
| ✅ **v0.19.1** | `ConsolidationHealthMetrics` on `ConsolidateResult.health` — five diagnostic ratios per cycle (`edge_creation_rate`, `contradiction_detection_rate`, `prefilter_pass_rate`, `promotion_rate`, `deduplication_rate`). Observability only; same additive shape as v0.18.1. Defers multi-parameter self-tuning ([ADR 0006](docs/decisions/0006-defer-multi-parameter-self-tuning.md)) with explicit reopen triggers. Also: CI now enforces ROADMAP↔docs/roadmap.md sync; `AGENTS.md` added with the memory-routing rule earned from Mira's peer message ([#74](https://github.com/emson/elfmem/pull/74), closes [#73](https://github.com/emson/elfmem/issues/73)) | 2026-06-06 |
| ✅ **v0.19.0** | Peer-protocol hardening — `peers:` in `config.yaml` now load-bearing; canonical-DID routing eliminates `outbox/alv/` vs `inbox/elf-alv/` slug drift; atomic + idempotent envelope writes (dotfile temp + `os.rename`); recipient-readiness precondition replaces silent black-hole sends; one-shot legacy folder migration. Wire-compatible with v0.18 peers ([#71](https://github.com/emson/elfmem/pull/71), [ADR 0005](docs/decisions/0005-peer-protocol-hardening.md)) | 2026-05-25 |
| ✅ **v0.18.1** | `ContradictionFinding` surfaces per-pair detection-time signals (`cosine`, `tag_jaccard`, `category_match`, `hours_apart`) on `ConsolidateResult.contradictions` — agents can gate suppression rules without recomputing from current block state ([#69](https://github.com/emson/elfmem/pull/69)) | 2026-05-24 |
| ✅ **v0.18.0** | Manual constitutional review — `review_constitutional()` surfaces drifted constitutional blocks; `accept_amendment()` applies with audit + (α, β) preservation; `revert_amendment()` one-step undo; CLI + MCP surfaces ([#67](https://github.com/emson/elfmem/pull/67)) | 2026-05-23 |
| ✅ **v0.17.0** | Bayesian sufficient statistics (α, β); additive rescore (22× damage reduction); arithmetic peer merge (BUNDLE_VERSION 2); exploration bonus (κ=0.05) ([#65](https://github.com/emson/elfmem/pull/65)) | 2026-05-23 |
| ✅ **v0.15.3** | Cold-start centrality floor for fresh blocks ([#61](https://github.com/emson/elfmem/issues/61)) | 2026-05-17 |
| ✅ **v0.15.2** | Removed confidence cliff at alignment_score=0.70 ([#60](https://github.com/emson/elfmem/issues/60)) | 2026-05-16 |
| ✅ **v0.15.1** | Surface `connect()` relation conflicts; fix token under-counting ([#59](https://github.com/emson/elfmem/issues/59)) | 2026-05-14 |
| ✅ **v0.15.0** | Embedding-model lock — closes silent-corruption risk from changing `embeddings.model` ([#56](https://github.com/emson/elfmem/pull/56), [#57](https://github.com/emson/elfmem/pull/57)) | 2026-05-17 |
| ✅ **v0.14.x** | Theory of Mind tools in MCP; dream flags exposed | 2026-05-10 |

See [CHANGELOG.md](CHANGELOG.md) for full history.

---

## Next

### 📋 v0.20 — Production signal response

> Originally slated as v0.19. Pre-empted by v0.19.0 peer-protocol hardening
> (an unplanned signal from elf's own peer-messaging usage; see "Recently
> Released" above and [ADR 0005](docs/decisions/0005-peer-protocol-hardening.md)).
> v0.19.2 (consolidation checkpointing) and v0.19.3 (MCP entry default +
> drift migration) also slotted in ahead of this without renumbering it —
> the telemetry gate is unchanged — date-bound, not version-bound.

v0.17 (sufficient stats + scoring bundle) shipped 2026-05-23. v0.18 (manual constitutional review) shipped 2026-05-23. Telemetry window for both now open. Concrete v0.20 scope depends on:
- Dmitry's follow-up answer (postponed until we have something substantive — draft preserved in [archived plan](docs/plans/archive/plan_memory_scoring.md#appendix---draft-follow-up-question-for-dmitry-issue-50))
- ≥3 months of v0.17 + v0.18 telemetry from real instances (i.e., not before ~2026-08-24)
- Any newly-observed systematic failure modes — especially around the new amendment loop

Possible v0.20 candidates (each requires its own ADR before committing):
- **Amendment loop tuning** if v0.18 defaults are off (drift_threshold, cooldown_hours, max_proposals)
- **Stronger rescore tuning** if v0.17 defaults need adjustment
- **Scheduled review triggers** — `dream(review=True)` integration so review is part of the deep-sleep rhythm rather than a manual ritual (only if production data shows manual cadence is too sparse)

---

## Exploring

These are research directions, not commitments. Each requires a Decision Record before becoming a roadmap item.

### 🔍 Constitutional / identity evolution — automatic mechanisms

The manual review cycle (shipped in v0.18) addresses the MANUAL side of constitutional evolution. The **automatic** mechanisms remain deferred per [ADR 0003](docs/decisions/0003-defer-constitutional-evolution.md):

- **Architecture M**: exclude constitutional from ATTENTION candidate pool; inject as preamble at frame render. Big help under drift (+33pp), real cost under stability (−7pp).
- **Model C (ego_strength)**: Darwinian — constitutional earn persistence via positive outcomes. Adds 4 magic numbers + 1 table.
- **Model D**: distributed feedback across top-N constitutional (fixes Model C hoarding, adds cost).
- **Self-architecting agent**: hill-climb in parameter space; agent picks its own configuration. Simulation showed it underperforms fixed strategies.

**Status**: deferred until production signal from v0.18 manual review demands an automatic complement. The simulations explored the design space but did not produce a decisively-better-than-baseline result for any user class.

### 🔍 MemoryAgentBench / LoCoMo participation

Empirical comparison against MemMachine, A-MEM, Mem0. Would calibrate the simulation harness and validate weight choices against real workloads. Requires effort to integrate; not on the current critical path.

### 🔍 Multi-context (work-self vs personal-self)

Per-tag parameter sets or per-frame overrides. Real demand: unconfirmed. Filed for tracking only.

### 🔍 Multi-parameter self-tuning ([issue #73](https://github.com/emson/elfmem/issues/73))

`ConsolidationPolicy` adapts only `effective_threshold`; the four other consolidation knobs (edge score, contradiction, prefilter, decay-λ) remain static constants. The full design space (5 architectures, 4 scenarios) was explored in [`docs/plans/issue_self_tune_research.md`](docs/plans/issue_self_tune_research.md); every adaptive variant fails on axioms 1 ("no magic numbers") or 3 ("ship minimum, earn each layer"), consistent with [ADR 0003](docs/decisions/0003-defer-constitutional-evolution.md)'s prior deferral of self-architecting parameter search. Decision recorded in [ADR 0006](docs/decisions/0006-defer-multi-parameter-self-tuning.md).

Observability-only delta shipped: `ConsolidationHealthMetrics` on `ConsolidateResult.health` (five diagnostic ratios). **Triggers to reopen**: ≥30 consecutive cycles of any health-metric field outside a sane band on a real deployment, OR concrete underperformance on MemoryAgentBench / LoCoMo traceable to a specific static threshold. The likely fix when triggered is making one constant a config-yaml override — not adaptive tuning.

### 🔍 Peer-protocol architectural cleanup (phases 5 & 6 of v0.19)

[ADR 0005](docs/decisions/0005-peer-protocol-hardening.md) deferred two phases from v0.19 because no current bug justified the blast radius. They unblock on trigger, not on a date:

- **Phase 5 — Envelope `schema_version` + time-bucketed `msg_id`**. Current
  `msg_id = m_<hash(content)[:8]>` collapses two legitimate repeat-content sends
  from the same sender into one message. **Trigger to reopen**: production logs
  show `msg_id` collisions in real traffic, or wire-format evolution forces a
  versioned envelope. (Requires observability — see "Issues" in the change
  notes; no collision counter exists today.)
- **Phase 6 — Quarantine routing for unknown senders and corrupt envelopes**.
  Unknown senders currently land in their named subdirectory; malformed JSON
  fails silently in `_parse_message`. **Trigger to reopen**: peer roster grows
  beyond ~10 entries (federation noise becomes material), or federation to a
  product-elf at scale (per the cloud-architecture sketch in `note-to-alv`).

---

## Rejected

Things we considered and decided **not** to do. Documented so they aren't relitigated.

### ❌ Power-law retrievability decay (FSRS-style)

**Considered**: replacing `exp(-λt)` with `(1 + 0.5t/stability)^(-0.5)`.
**Why rejected**: simulation across 4 scenarios showed −5 to −7.6pp quality and **−44 to −66pp recent_reach** (catastrophic). Fat tails keep stale blocks competitive against fresh ones. Power-law works for flashcards; refuted for agent memory.
**Decision Record**: [`docs/decisions/0001-power-law-decay-rejected.md`](docs/decisions/0001-power-law-decay-rejected.md)
**Trigger to revisit**: only if MemoryAgentBench shows power-law wins on agent workloads (none reported as of 2026-05).

### ❌ FSRS-5 19-parameter stability machinery

**Considered**: importing the FSRS-5 difficulty/stability mechanics wholesale.
**Why rejected**: 19 magic numbers fitted to flashcard data; no fitting infrastructure for agent traces. Violates "no magic numbers" by an order of magnitude.
**Trigger to revisit**: ≥10,000 outcome events in production from real elfmem deployments AND a fitting pipeline.

### ❌ `block_events` event log table

**Considered**: a full per-event log for replay and audit.
**Why rejected**: `block_outcomes` already captures the only event type with non-trivial signal. Replay is a research affordance, not a user affordance.
**Trigger to revisit**: real user demand for compliance / replay features.

### ❌ Hierarchical abstract tier (raw / summary / abstract)

**Considered**: MemoryOS-style three-tier hierarchy.
**Why rejected**: existing summary-block mechanism is sufficient. Adding a third tier is imitation without measured benefit.
**Trigger to revisit**: measurement of context-bloat retrieval defects on real queries.

### ❌ Zettelkasten auto-linking on consolidate

**Considered**: LLM-driven automatic `connect()` calls during dream().
**Why rejected**: `connect()` already exists for explicit use; auto-linking introduces LLM failure modes without measured gain.
**Trigger to revisit**: evidence that explicit `connect()` is undertilised by real agents AND a validation strategy for LLM-judged links.

### ❌ Renaming `confidence → utility`

**Considered**: aligning vocabulary with cognitive-science papers (Park's "importance").
**Why rejected**: `confidence` is elfmem's brand-term used across `outcome()`, `consolidate()`, MCP, AgentGuide, CLI. Rename has high churn cost for zero behavioural value.
**Trigger to revisit**: never. Terminology decision is settled.

---

## Long-term north star

Not committed, but where this is heading.

- **v0.19**: peer-protocol hardening (shipped/shipping — unplanned, signal-driven)
- **v0.20+**: production signal response (originally v0.19; gated on ≥3 months of v0.17/v0.18 telemetry)
- **v0.21+**: benchmarking against MemoryAgentBench / LoCoMo if calibration is needed
- **v0.22+**: earned architectural features (only the deferred items that empirical evidence supports)
- **v1.0**: public API freeze. Stable for years. Backwards-compatible changes only.

**Discipline**: every subsequent layer must be earned with evidence — not designed in advance.

---

## How this roadmap is maintained

- **Quarterly review**: at start of each quarter, re-evaluate "In Progress" and "Next" against actual user signal
- **Per-release update**: at each minor release, move items from "In Progress" to "Recently Released"; revisit "Exploring"
- **Decision Records**: every "Rejected" item or major architectural choice gets an ADR in `docs/decisions/`
- **Issue links**: each roadmap item should link to a GitHub issue once filed; discussions happen there

If something here is wrong, file an issue. Pull requests against this file are welcome.
