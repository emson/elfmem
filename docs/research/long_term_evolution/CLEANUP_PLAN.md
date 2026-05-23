# Cleanup plan — moving research out of docs/plans/

**Date**: 2026-05-23
**Status**: proposed (not yet executed)

This is the proposed file-move plan to clean up the doc tree after two days
of intense research. Each move is justified. Executing this plan is destructive
(git-tracked, but changes nav structure), so Ben should review before moving.

---

## Principle

| Location | What goes there |
|---|---|
| `ROADMAP.md` (root) | Single source of truth for direction |
| `docs/decisions/` | ADRs — load-bearing decisions, append-only |
| `docs/plans/` | **Active** implementation plans only (currently being executed) |
| `docs/research/` | Compiled research that's still consulted |
| `docs/plans/archive/` | Shipped plans (per existing convention in `plans/README.md`) |

The existing `docs/plans/README.md` already establishes that shipped plans are "frozen artifacts." We extend that: research notes go to `docs/research/`, decisions go to `docs/decisions/`.

---

## File-by-file disposition

### Today's exploratory notes (currently in `docs/`)

These are dated and exploratory. Each should move to `docs/research/long_term_evolution/` (compiled, not raw dated notes).

| Current path | Action | Target |
|---|---|---|
| `docs/note_2026_05_08_reflection.md` | Move | `docs/research/older/note_2026_05_08_reflection.md` (older exploration) |
| `docs/note_2026_05_09_refactor.md` | Move | `docs/research/older/note_2026_05_09_refactor.md` |
| `docs/note_2026_05_21_elf_reply_to_alv.md` | Move | `docs/research/long_term_evolution/elf_reply_to_alv.md` (this is curator-voice synthesis, worth keeping) |
| `docs/note_2026_05_22_mc_evolution_findings.md` | Compile then move | `docs/research/long_term_evolution/_archived/mc_evolution_findings.md` |
| `docs/note_2026_05_22_constitutional_architecture.md` | Compile then move | `docs/research/long_term_evolution/_archived/constitutional_architecture.md` |
| `docs/note_2026_05_22_ego_feedback_findings.md` | Compile then move | `docs/research/long_term_evolution/_archived/ego_feedback_findings.md` |
| `docs/note_2026_05_22_full_scenario_findings.md` | Compile then move | `docs/research/long_term_evolution/_archived/full_scenario_findings.md` |
| `docs/note_2026_05_22_self_architect.md` | Promote (rewrite as compiled) | `docs/research/long_term_evolution/self_architecting_agent.md` |
| `docs/note_2026_05_23_scoring_proposed_findings.md` | Promote (rewrite as compiled) | `docs/research/scoring_proposed_evaluation.md` |
| `docs/note_2026_05_23_self_critique.md` | Promote | `docs/research/long_term_evolution/self_critique.md` |
| `docs/note_2026_05_23_decisions.md` | Promote | `docs/research/long_term_evolution/decisions.md` |

**Rationale**: dated notes are exploration, not curated knowledge. The compiled artifacts in `docs/research/long_term_evolution/` are what future readers should consult. The raw dated notes are archived for git-history purposes but not exposed in mkdocs nav.

### Plans that aren't active plans

These are in `docs/plans/` but aren't actually plans for current implementation.

| Current path | Status | Action | Target |
|---|---|---|---|
| `docs/plans/plan_self_architecting_elfmem.md` | Over-extended; superseded by ADRs 0002/0003 | Move | `docs/research/long_term_evolution/_archived/over_extended_plan.md` |
| `docs/plans/plan_evolving_memory.md` | Brainstorm doc; superseded by ADR 0003 | Move | `docs/research/long_term_evolution/_archived/brainstorm.md` |
| `docs/plans/plan_longitudinal_evaluation.md` | Methodology doc, not implementation plan | Move | `docs/research/methodology/longitudinal_evaluation.md` |
| `docs/plans/plan_confidence_architecture.md` | Superseded by plan_memory_scoring.md | Move | `docs/plans/archive/plan_confidence_architecture.md` (per existing convention) |
| `docs/plans/plan_v0.15.3_centrality_floor.md` | Shipped in v0.15.3 | Move | `docs/plans/archive/plan_v0.15.3_centrality_floor.md` |
| `docs/plans/plan_elfmem_reflect.md` | Older planning, status unclear | Review before deciding | (TBD) |

**Plans that should stay in `docs/plans/`** (currently active):
- `plan_memory_scoring.md` — the active v0.16/v0.17 plan
- `README.md` — index

All the other shipped plans (already listed in `docs/plans/README.md` as frozen artifacts) can stay where they are — they're historically labeled correctly.

### Other files

| File | Action | Reason |
|---|---|---|
| `scripts/verify_v0_15_3_scoring.py` | Keep as-is | One-off but useful reference for verification approach |
| `scripts/longitudinal_sim/` | Keep as-is | Permanent simulation fixture |

---

## Mkdocs nav update

After moving files, `mkdocs.yml` nav needs updating:

```yaml
nav:
  - Home: index.md
  - Quick Start: quickstart.md
  - Roadmap: ../ROADMAP.md   # NEW — point at root file (or copy into docs/)
  - Getting Started:
    # ... existing ...
  - Building Agents:
    # ... existing ...
  - Architecture:
    # ... existing ...
  - MCP Reference:
    # ... existing ...
  - Research:
    - Long-term evolution: research/long_term_evolution/README.md   # NEW
    - Scoring evaluation: research/scoring_proposed_evaluation.md   # NEW
    # ... existing research entries ...
  - Decisions:                                                       # NEW SECTION
    - Index: decisions/README.md
  - Contributing:
    # ... existing ...
  - Design History:
    - Implementation Plans: plans/README.md
```

**Note**: ROADMAP.md is conventionally in the repo root for GitHub display. We can either copy it into `docs/` for mkdocs OR use a markdown include / symlink.

---

## Branch and commit hygiene

| Item | Action |
|---|---|
| Branch `feature-constitutional-experiments` | Rename to `research/long-term-evolution` (honest label — this is research not a feature) |
| 2 commits on `main` (longitudinal harness + mc_evolution) | Cherry-pick the simulation harness onto research branch; revert main if needed |
| 7 commits on the research branch | Squash-ready summary commit for PR; or keep as detailed log |

---

## Execution order

If approved:

1. **Compile and promote** (write the artifacts in `docs/research/`):
   - `docs/research/long_term_evolution/closed_form_analysis.md` (compiled from `closed_form.py` + D1–D6 summaries)
   - `docs/research/long_term_evolution/constitutional_evolution.md` (compiled from the 4 constitutional notes)
   - `docs/research/scoring_proposed_evaluation.md` (compiled from `note_2026_05_23_scoring_proposed_findings.md`)
2. **Archive raw notes** (git mv to `docs/research/long_term_evolution/_archived/`)
3. **Move stale plans** (git mv per table above)
4. **Update mkdocs.yml** (add Decisions section, add Research entries)
5. **Update CLAUDE.md** if any new conventions need documenting (ADR location, ROADMAP location)
6. **Rename branch and PR**

Each step is a separate commit for reviewability.

---

## What we explicitly are NOT cleaning up

- `scripts/longitudinal_sim/` — permanent fixture
- `docs/plans/plan_memory_scoring.md` — active v0.16/v0.17 plan (will become primary v0.17 plan after this cleanup)
- Existing shipped plans listed in `docs/plans/README.md` — already correctly labeled as frozen artifacts
- `tests/test_longitudinal_safety.py`, `tests/test_scoring.py::TestColdStartGapRegression` — they exercise the simulation safety and v0.15.3 regression fixtures, valuable for ongoing maintenance

---

## Open question for Ben

1. Approve this cleanup plan as-is?
2. Modify any disposition (e.g., delete some notes rather than archive)?
3. Should `ROADMAP.md` live in root only (GitHub convention) or also be exposed in mkdocs nav?
4. Should we file GitHub issues for each "In Progress" and "Next" roadmap item before merging?
