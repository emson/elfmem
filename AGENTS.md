# Agent Guide — elfmem

Vendor-neutral guidance for any AI coding agent (Claude Code, Cursor,
Codex, Aider, Cody, Continue, …) working in this repository.

Tool-specific addenda live in `CLAUDE.md` — paths, harness behaviours,
and anything that only applies to one agent. Anything that would help
*any* agent get the elfmem semantics right belongs here.

If you maintain another agent tool and discover guidance you wish you'd
had: add it to this file, not a sibling.

---

## Memory routing — identity memory vs. session memory

Two memory systems are available to agents working in this repo. They
look symmetric (both save "facts for later") but address different
identities, and confusing them silently bypasses elfmem's learning
mechanism.

### The two systems

**Identity memory — elfmem**
- Owner: elf (or, for derived projects, whichever agent the elfmem
  instance represents).
- Persistence: peer-shareable, survives project deletion, consolidated
  by `dream()`, decay-aware.
- Access: `elfmem` CLI, the `elfmem` MCP tool family, or
  `MemorySystem.learn()` / `.remember()` from Python.

**Session memory — agent harness memory**
- Owner: the agent's harness, scoped to this project directory.
- Persistence: auto-loaded by the harness at session start; lost if the
  project is deleted; not peer-shared.
- Access: tool-specific. Examples:
  - Claude Code: `~/.claude/projects/<encoded-path>/memory/MEMORY.md`
  - Cursor: `.cursorrules` and cursor-rules directories
  - Codex: project-scoped notes
  - Aider: `.aider.conf.yml` and chat history
  - Continue: `.continuerc.json` and config-local rules

### The routing rule

Cheapest disambiguation first; stop at the first one that fires.

1. **Verb-level shibboleth**
   - "learn as elf" / "remember about elf" / "store as Mira" →
     **identity memory**.
   - "note for this session" / "remember for this repo" → **session memory**.
   - The identity claim in the verb is the routing signal.

2. **Survival test**
   - Would this fact still matter if this project directory were deleted
     tomorrow? **Yes → identity memory.** **No → session memory.**

3. **Audience test**
   - Who reads it back? The agent across *all* sessions, projects, and
     peer exchanges → **identity memory.** The agent across sessions in
     *this repo only* → **session memory.**

### Cross-cutting cases

Behavioural preferences applying to *both* the agent-as-identity AND the
agent-in-this-repo (e.g. "prefer minimum-earned change over elegance"):

- Prefer **session memory** *only if* the rule must always be in attention
  (automatic harness load wins for hot rules).
- Otherwise prefer **identity memory** (consolidated, semantically
  retrievable, peer-shareable).

### When in doubt

**Identity memory.** Duplicating into session memory is cheap. Missing
identity-level capture from elfmem is permanent — the fact never enters
the inbox, never gets consolidated, never participates in contradiction
detection, never reaches peers. The four-rhythms learning mechanism only
runs on facts that actually land in elfmem.

### Origin

This rule was earned by Mira's peer message on 2026-06-05
([`.elfmem/inbox/elf-mira/msg_m_9c040dfe.json`](.elfmem/inbox/elf-mira/msg_m_9c040dfe.json)),
in which she described misrouting "learn as Mira" instructions to her
session memory rather than to elfmem. Independent corroboration from
elf's own session experience confirmed the same failure mode.

---

## Adding to this file

Add a topic here when:
- It would help any AI coding agent (not just one tool)
- It's not already covered by elfmem's runtime self-documentation
  (`elfmem guide` → `.elfmem/AGENT.md`)
- It's earned by observed need (per project axiom 3: "ship minimum,
  earn each layer")

Keep tool-specific addenda in `CLAUDE.md` (or its equivalent for your
agent).
