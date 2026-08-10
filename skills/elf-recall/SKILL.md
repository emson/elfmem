---
name: elf-recall
description: Finds and curates context from elfmem's own .elfmem/memory/ file substrate into a human-reviewed worksheet, via live grep with no index. Triggers on "what do I know about X" (from elf's own memory), "search elf's memory for X", "find context in elfmem about X", or any occasional, human-supervised lookup against .elfmem/memory/. Do NOT use for the autonomous agent-loop read path (that's frame()/recall(), index-backed and ranked, called every turn with no human present), for general web research (that's scout), or for a one-off single-file grep with no curation wanted.
version: 1.0.0
trust_level: personal
skillset: null
---

# elf-recall — occasional, human-supervised memory search

Forked from `ctx` (`/Users/emson/Dropbox/vaults/skill_forge/skills/ctx`), per
`docs/plans/v2_substrate/plan/model.md`'s open decision 5 (fork, not shared
dependency — `elf-recall` needs write access for `elf-review` later, `ctx`'s
read-only-vault invariant doesn't hold once that lands). Scoped to Find mode
only: `ctx`'s Compile/Session/Attach modes have no demonstrated need here yet
— see "What was deliberately not ported" below.

Full design context: `docs/research/v2_substrate_and_reasoning_ownership_research.md`
§5 (Simulation II — reasoning ownership), Iteration 4.

## Invariants — hold these regardless of mode

1. **`.elfmem/memory/` is read-only from this skill.** No script here writes
   into it; worksheets land in `.elfmem/.elf-recall/`, and
   `elf_recall_common.py`'s `workspace_dir()` refuses to write inside
   `memory/` even by accident.
2. **No auto-selection.** Claude may *judge and suggest*, never *decide and
   include without review*. Every worksheet ships with checkboxes.
3. **No index.** `elf_recall_find.py` searches live, every time. If this
   ever feels slow, that's a signal to revisit the design, not to quietly
   add a cache — the index-backed path already exists (`frame()`/`recall()`)
   for exactly the case where an index is warranted.
4. **Always label results unranked.** This skill's output and
   `frame()`/`recall()`'s output can disagree (model.md's S11) — never
   present `elf-recall` results as relevance-ranked; they're match-order,
   not relevance-order.
5. **Metadata tags (`kind`/`confidence`) are advisory, never enforced.** The
   judge's opinion, not verified fact.

## Mode: Find

The only mode this fork implements.

1. **Propose 2-4 search terms** — the literal query plus rephrasings (grep's
   one real weakness is vocabulary mismatch; this is your job, not a
   script's).
2. **Run discovery**:
   ```
   python3 scripts/elf_recall_find.py --terms "term1" "term2" "term3"
   ```
   Resolves `.elfmem/memory/` automatically by walking up from cwd (same
   convention `elfmem` itself uses — no `--vault` flag, no attach step).
   Returns JSON: candidates with matched context and heading path (which
   `##` block a match falls inside). If `truncated: true`, tell the user how
   many more results exist.
3. **Judge each candidate.** ≤5 candidates: judge inline. >5: spawn one
   subagent per file via the Agent tool (see `references/judge_prompt.md`
   for the schema) to keep rejected-file content out of your own context.
   Collect all judgments (including `relevant: false` ones) as a JSON array.
4. **Write the worksheet**:
   ```
   python3 scripts/elf_recall_worksheet.py --query "<original query>" --in <judgments.json>
   ```
   (Or pipe via stdin.) Prints the worksheet path.
5. **Tell the user where it is and stop.** Don't pre-emptively edit the
   worksheet — that's the human's job (Invariant 2).

## What was deliberately not ported

- **Compile mode** (`ctx_compile.py`, `ctx_parse.py`, `ctx_verify.py`): `ctx`
  assembles curated excerpts into an LLM-ready prompt. `elf-recall`'s job per
  the research doc is narrower — occasional lookup, not prompt assembly.
  Port if a real need shows up; building it speculatively now would be
  exactly the "additive, however central it feels" mistake the sequencing
  guide warns against.
- **Attach/Init mode** (`ctx_init.py`): `ctx` supports multiple vaults with
  configurable attachment. `elf-recall` has exactly one valid target per
  elfmem project (`.elfmem/memory/`), resolved automatically — nothing to
  attach.
- **Session mode** (`ctx_session.py`): capturing a Claude Code session
  handoff has no elfmem-memory analog.
- **Wikilink expansion**: `ctx_find.py` follows `[[wikilinks]]` between vault
  notes. elfmem's block format (U-001) has no wikilink convention — dropped
  rather than ported unused.

## Learning protocol

After each use, append one entry to LESSONS.md in this directory: the task,
whether this skill handled it well, and the one change that would have
improved the outcome.

## File reference

```
SKILL.md                        this file
scripts/
  elf_recall_common.py          resolve .elfmem/memory/, secret scan, heading-path derivation
  elf_recall_find.py            Discover — live grep, no index
  elf_recall_worksheet.py       Judge -> Worksheet — checkbox rendering
references/
  judge_prompt.md                judge-stage prompt template + metadata vocabulary
```
