# Claude Code Agent Teams (Swarms) — Definitive Prompt & Orchestration Guide

> **Version:** February 2026  
> **Sources:** Anthropic official docs, Anthropic engineering blog (C compiler case study), community practitioners (Addy Osmani, alexop.dev, kieranklaassen), real-world production case studies.

---

## Table of Contents

1. [What Agent Teams Are (And Aren't)](#1-what-agent-teams-are-and-arent)
2. [When to Use Teams vs Subagents vs Solo](#2-when-to-use-teams-vs-subagents-vs-solo)
3. [Setup & Configuration](#3-setup--configuration)
4. [Core Architecture & Primitives](#4-core-architecture--primitives)
5. [Prompt Engineering for Agent Teams](#5-prompt-engineering-for-agent-teams)
6. [The Six Orchestration Patterns](#6-the-six-orchestration-patterns)
7. [CLAUDE.md Configuration for Teams](#7-claudemd-configuration-for-teams)
8. [Cost Optimisation Strategies](#8-cost-optimisation-strategies)
9. [Lessons from Anthropic's C Compiler (16-Agent Case Study)](#9-lessons-from-anthropics-c-compiler-16-agent-case-study)
10. [Production Best Practices](#10-production-best-practices)
11. [Anti-Patterns to Avoid](#11-anti-patterns-to-avoid)
12. [Quick Reference Cheat Sheet](#12-quick-reference-cheat-sheet)

---

## 1. What Agent Teams Are (And Aren't)

Agent teams coordinate **multiple Claude Code instances** working in parallel, each with its own context window. One session acts as the **team lead** (orchestrator), spawning **teammates** (workers) that can:

- Work independently on separate tasks
- Communicate directly with each other via messaging (not just back to the lead)
- Share a task list with dependency tracking
- Self-organise by claiming available work

**The core insight:** LLMs perform worse as context expands. Instead of one agent accumulating a bloated context window, each teammate gets a fresh, focused context. A task that takes one agent 2 hours might take a team 30 minutes — not because agents are faster, but because they work simultaneously on different parts.

**What they are NOT:**

- A magic productivity multiplier for simple tasks (coordination overhead is real)
- A replacement for clear specifications (garbage in, garbage out — amplified by N agents)
- Free — each teammate is a full Claude session consuming tokens independently

---

## 2. When to Use Teams vs Subagents vs Solo

### Decision Framework

```
Is the task small/focused?
  → YES → Solo session

Can work be parallelised but workers don't need to talk?
  → YES → Subagents (Task tool without team_name)

Do workers need to share findings, challenge each other, or coordinate?
  → YES → Agent Teams
```

### Comparison Table

| Aspect | Solo Session | Subagents | Agent Teams |
|---|---|---|---|
| **Context** | Single window | Own window, results return to parent | Own window, fully independent |
| **Communication** | N/A | Report to parent only | Message each other directly |
| **Coordination** | You manage | Parent manages | Shared task list, self-claim |
| **Token cost** | ~200k | ~440k (3 agents) | ~800k+ (3 agents) |
| **Best for** | Focused work | Research, exploration, quick checks | Complex multi-track work |

### Strongest Use Cases for Agent Teams

1. **Research & Review:** Multiple investigators examine different aspects in parallel, then challenge each other's findings
2. **New Features / Modules:** Teammates each own a separate piece (API, UI, tests) without stepping on each other
3. **Debugging with Competing Hypotheses:** Teammates test different theories in parallel and converge on the answer via debate
4. **Cross-Layer Coordination:** Changes spanning frontend, backend, and tests — each owned by a different teammate
5. **QA Swarms:** Multiple agents testing different quality dimensions simultaneously

---

## 3. Setup & Configuration

### Enable Agent Teams

Add to your `settings.json` (or shell environment):

```json
// ~/.claude/settings.json
{
  "env": {
    "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1"
  }
}
```

Or via shell:

```bash
export CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1
```

### Display Backends

| Backend | How It Works | Best For |
|---|---|---|
| **in-process** (default) | All teammates in same Node.js process, invisible | Fast startup, CI/CD |
| **tmux** | Separate panes per teammate, visible | Debugging, monitoring |
| **iterm2** | Split panes in iTerm2 (macOS only) | Visual debugging |

Force a specific backend:

```bash
export CLAUDE_CODE_SPAWN_BACKEND=tmux   # or in-process, iterm2
```

### Permission Settings

Teammates inherit the lead's permission settings. For autonomous operation, pre-approve common operations to avoid permission prompt floods:

```bash
# For maximum autonomy (use with caution):
claude --dangerously-skip-permissions
```

---

## 4. Core Architecture & Primitives

### The Seven Primitives

| Primitive | Purpose | Disk Location |
|---|---|---|
| **Team** | Named group of agents | `~/.claude/teams/{name}/config.json` |
| **Leader** | Creates team, coordinates, synthesises | First member in config |
| **Teammate** | Executes tasks, reports findings | Listed in team config |
| **Task** | Work item with status, owner, dependencies | `~/.claude/tasks/{team}/N.json` |
| **Inbox** | Per-agent message queue | `~/.claude/teams/{name}/inboxes/{agent}.json` |
| **Message** | JSON object between agents | Stored in inbox files |
| **Backend** | How teammates run (in-process/tmux/iterm2) | Auto-detected |

### Lifecycle

```
1. Create Team  →  2. Create Tasks  →  3. Spawn Teammates
      ↓                                        ↓
4. Work (claim, execute, complete)  ←→  5. Coordinate (message, sync)
      ↓
6. Shutdown (request → approve)  →  7. Cleanup
```

### Task States

```
PENDING  →  IN_PROGRESS  →  COMPLETED
                ↑
          (teammate claims
           or lead assigns)
```

Tasks with `blockedBy` dependencies auto-unblock when their blockers complete.

### Communication Channels

Teammates share information via two mechanisms only:
1. **Task files on disk** — the shared task list
2. **SendMessage / Inbox** — direct peer-to-peer messaging

There is **no shared memory**. Teammates load project context (CLAUDE.md, MCP servers, skills) but do NOT inherit the lead's conversation history.

---

## 5. Prompt Engineering for Agent Teams

This is the most critical section. The quality of your prompts determines whether your team produces brilliant work or expensive chaos.

### 5.1 The Golden Rule: Plan First, Parallelise Second

The single most effective pattern across all practitioners:

```
Step 1: Use plan mode to create a detailed implementation plan (~10k tokens)
Step 2: Review and approve the plan
Step 3: Hand the plan to a team for parallel execution
```

**Why this matters:** A team that goes in the wrong direction costs 500k+ tokens. A plan costs ~10k. The plan gives you a checkpoint before committing heavy resources.

#### Example Two-Step Prompt

**Step 1 — Planning prompt:**

```
Plan the refactor of our authentication module. I want to split the
monolithic auth.ts into separate files for JWT handling, session
management, and middleware. Show me the plan before doing anything.
```

**Step 2 — Execution prompt (after approving plan):**

```
Now execute this plan using an agent team. Parallelise where possible:
- Steps 1-3 can run in parallel (independent extractions)
- Step 4-5 depends on 1-3
- Step 6 depends on everything
Use Sonnet for the teammates.
```

### 5.2 How to Prompt the Lead (Your Entry Point)

When you talk to Claude Code with agent teams enabled, you're talking to the **team lead**. Your prompt should communicate:

1. **The goal** — What you're trying to achieve
2. **The team structure** — What roles/specialisations you want
3. **File ownership** — Who owns what (critical for avoiding conflicts)
4. **Coordination expectations** — How teammates should interact
5. **Output expectations** — What the final deliverable looks like

#### Template: Natural Language Team Prompt

```
[GOAL]
Build/Review/Refactor [X]. 

[TEAM STRUCTURE]
Create an agent team with:
- Teammate "name-1" focusing on [scope/files]
- Teammate "name-2" focusing on [scope/files]
- Teammate "name-3" focusing on [scope/files]

[COORDINATION]
They should coordinate on [shared interface/API contract/data schema].
[name-1] should message [name-2] when [condition].

[CONSTRAINTS]
Use Sonnet for teammates. Require plan approval before changes.
Each teammate should report findings to the lead when done.

[OUTPUT]
Synthesise all findings into [report/PR/summary].
```

#### Real Examples That Work Well

**Research & Debate:**

```
Users report the app exits after one message instead of staying connected.
Spawn 5 agent teammates to investigate different hypotheses. Have them
talk to each other to try to disprove each other's theories, like a
scientific debate. Update the findings doc with whatever consensus emerges.
```

**Feature Build:**

```
Create an agent team with two teammates. Teammate "backend" should
implement the new /api/users endpoint in src/routes/. Teammate "frontend"
should build the user profile component in src/components/. They should
coordinate on the API contract.
```

**QA Swarm:**

```
Use a team of agents that will do QA against my app running at
http://localhost:3000/. Have one focus on security, one on performance,
one on accessibility, one on broken links, and one on SEO.
```

**Code Review (Parallel Specialists):**

```
Review this PR from multiple perspectives. Create an agent team with:
- "security" reviewing for vulnerabilities (SQL injection, XSS, auth bypass)
- "performance" reviewing for N+1 queries, memory leaks, inefficiencies
- "architecture" reviewing for SOLID principles and design patterns
Synthesise all findings into a prioritised review.
```

### 5.3 How to Write Teammate Spawn Prompts

When the lead spawns teammates, the **prompt** field is their entire world. They get no conversation history from the lead. This prompt IS their context.

#### The Five Elements of an Effective Teammate Prompt

```
1. IDENTITY — Who you are and your role
2. CONTEXT — What the project is, what matters
3. SCOPE — Exactly what files/areas you own
4. TASK — Step-by-step what to do
5. REPORTING — How to communicate results
```

#### Template: Teammate Spawn Prompt

```
You are [ROLE NAME] on team [TEAM_NAME].

CONTEXT:
- This is a [project type] located at [path]
- Key conventions: [list relevant conventions]
- Important files: [list key files for this role]

YOUR SCOPE:
- You own: [specific files/directories]
- Do NOT modify: [files owned by other teammates]

TASKS:
1. [Specific action with expected outcome]
2. [Specific action with expected outcome]
3. [Specific action with expected outcome]

WHEN DONE:
- Mark your tasks as completed via TaskUpdate
- Send a structured summary to team-lead via Teammate write
- Include: what you found, what you changed, any issues for other teammates

COORDINATION:
- If you need [X], message teammate "[name]"
- If you discover issues in [other area], message the lead, don't fix it yourself
```

#### Bad vs Good Teammate Prompts

**BAD — Too vague:**

```
prompt: "Review the code"
```

**BAD — No reporting instructions:**

```
prompt: "Check the authentication module for security issues"
```

**GOOD — Complete context:**

```
prompt: `
  You are security-reviewer on team pr-review.
  
  Review all files in app/services/auth/ for security vulnerabilities.
  
  Focus on:
  - SQL injection in query construction
  - XSS in any rendered output
  - Authentication bypass in middleware
  - Sensitive data exposure in logs or responses
  
  For each issue found, note:
  - File and line number
  - Severity (critical/high/medium/low)
  - Suggested fix
  
  Send your findings to team-lead via:
  Teammate({ operation: "write", target_agent_id: "team-lead", 
    value: "Your structured findings here" })
`
```

### 5.4 Designing Task Descriptions

Task descriptions are prompts for the agent that claims them. Pack in detail.

**BAD:**

```json
{
  "subject": "Fix auth",
  "description": "Fix the authentication"
}
```

**GOOD:**

```json
{
  "subject": "Refactor User model: extract auth concern",
  "description": "Extract authentication methods from app/models/user.rb into a new AuthenticatableUser concern at app/models/concerns/authenticatable_user.rb. Methods to extract: #authenticate, #generate_token, #refresh_token, #revoke_all_tokens. Ensure User model includes the concern. Run existing auth specs to verify nothing breaks. Mark task complete and notify team-lead with a summary of extracted methods.",
  "activeForm": "Extracting auth concern from User model..."
}
```

### 5.5 Advanced Prompt Techniques

#### Technique 1: The Debate Pattern

Force teammates to challenge each other's findings for higher quality results:

```
Spawn 5 agent teammates to investigate [problem]. Have them talk to
each other to try to disprove each other's theories, like a scientific
debate. The theory that survives active challenge is more likely correct.
```

This works because sequential investigation suffers from **anchoring bias** — once one theory is explored, subsequent investigation is biased toward it. Parallel independent investigators with active disproval produce better results.

#### Technique 2: Swarm Worker Self-Organisation Prompt

For pools of independent tasks, use this self-organising worker prompt:

```
You are a swarm worker. Your job:
1. Call TaskList to see available tasks
2. Find a task with status 'pending' and no owner
3. Claim it with TaskUpdate (set owner to your name)
4. Start it with TaskUpdate (set status to in_progress)
5. Do the work
6. Mark it completed with TaskUpdate
7. Send findings to team-lead via Teammate write
8. Repeat until no tasks remain
9. If no tasks available, send idle notification and exit

Your name is $CLAUDE_CODE_AGENT_NAME — use it when claiming tasks.
```

#### Technique 3: Model Selection in Prompts

Use expensive models for coordination, cheap models for execution:

```
Create an agent team. Use Opus for the team lead (coordination matters).
Use Sonnet for all teammates (they're doing focused execution work).
```

In spawn calls:

```
Task({
  team_name: "my-team",
  name: "worker-1",
  model: "sonnet",    // Cheaper model for focused work
  ...
})
```

#### Technique 4: Dependency Wave Planning

Structure prompts to exploit dependency-based parallelism:

```
Create these tasks with dependencies:
Wave 1 (parallel): Research, Design, Audit current code
Wave 2 (after wave 1): Implementation plan (needs research + design)
Wave 3 (after wave 2): Backend impl, Frontend impl, Test impl (parallel)
Wave 4 (after wave 3): Integration testing, Final review
```

#### Technique 5: Context Window Hygiene in Agent Prompts

Based on Anthropic's C compiler learnings, design for LLM limitations:

```
IMPORTANT CONSTRAINTS FOR YOUR WORK:
- Keep logs concise. Print at most a few lines of output per operation.
- Log errors with ERROR prefix on the same line as the reason (for grep).
- Pre-compute summary statistics rather than dumping raw data.
- Use --fast flags for test runs (sample 10%) during development.
- Save detailed output to files rather than printing to terminal.
- Update the progress doc after each significant milestone.
```

---

## 6. The Six Orchestration Patterns

### Pattern 1: Parallel Specialists (Leader Pattern)

Multiple specialists work simultaneously on different aspects of the same codebase.

```
YOU: "Review this PR from security, performance, and architecture angles"

LEAD spawns:
  security-reviewer  → focuses on vulnerabilities
  perf-reviewer      → focuses on N+1, memory, algorithms
  arch-reviewer      → focuses on SOLID, patterns, testability

All work in parallel → findings to lead → synthesised report
```

**Best for:** Code reviews, audits, multi-dimensional analysis.

### Pattern 2: Pipeline (Sequential Dependencies)

Each stage depends on the previous, work flows through a pipeline.

```
Research → Plan → Implement → Test → Review

TaskUpdate({ taskId: "2", addBlockedBy: ["1"] })
TaskUpdate({ taskId: "3", addBlockedBy: ["2"] })
TaskUpdate({ taskId: "4", addBlockedBy: ["3"] })
TaskUpdate({ taskId: "5", addBlockedBy: ["4"] })
```

**Best for:** Feature development with clear phases, migration workflows.

### Pattern 3: Swarm (Self-Organising Workers)

A pool of identical workers racing to claim independent tasks.

```
Tasks: Review file A, Review file B, Review file C, ... (no dependencies)
Workers: worker-1, worker-2, worker-3 (same prompt, self-organising)

Workers poll TaskList → claim pending tasks → complete → claim next
Natural load balancing: faster workers handle more tasks
```

**Best for:** Bulk file processing, independent reviews, codebase-wide changes.

### Pattern 4: Research + Implementation

Synchronous research phase feeds into parallel implementation.

```
Step 1: Subagent researches best practices (synchronous, returns result)
Step 2: Research feeds into team prompt for parallel implementation
```

**Best for:** Greenfield features, technology evaluations, informed decisions.

### Pattern 5: Plan Approval Workflow

Requires explicit plan approval before any implementation begins.

```
Architect teammate creates plan → Lead reviews → Approve/Reject with feedback
Only after approval does implementation proceed
```

**Best for:** High-stakes changes, regulated environments, architectural decisions.

### Pattern 6: Competing Hypotheses (Debate)

Multiple agents investigate independently and challenge each other.

```
5 agents investigate a bug → each proposes a theory
→ agents try to disprove each other's theories
→ surviving theory is likely the root cause
```

**Best for:** Complex debugging, root cause analysis, design decisions.

---

## 7. CLAUDE.md Configuration for Teams

Your project's `CLAUDE.md` is automatically loaded by ALL teammates. Use it to provide persistent team-wide guidance.

### Recommended CLAUDE.md Structure for Teams

```markdown
# Project: [Name]

## Architecture
[Brief description of project structure and key directories]

## Agent Team Configuration

When working on this project with multiple agents, use these role definitions:

- **Backend Agent**: Focuses on /src/server/. Follows Express middleware patterns.
  Uses TypeORM for database operations.
- **Frontend Agent**: Focuses on /src/client/. Uses component library in
  /src/client/components/shared/. Follows Tailwind conventions.
- **Test Agent**: Writes tests in /tests/. Uses Jest with custom utilities
  in /tests/helpers/.
- **Review Agent**: Reviews all output for security vulnerabilities, type safety,
  and adherence to ESLint configuration.

## File Ownership Boundaries
- /src/server/ → backend agents only
- /src/client/ → frontend agents only
- /tests/ → test agents only
- /src/shared/ → coordinate via lead before modifying

## Coding Conventions
- Prefer small diffs; no unrequested refactors
- Add/modify tests for all changed logic
- Conventional commits; one issue per PR
- Keep solutions minimal and focused

## DX Commands
- `npm run dev` — start dev server
- `npm test` — run test suite
- `npm run lint` — check linting
- `npm run build` — production build

## Definition of Done
- Tests pass; coverage not lower
- Lint passes
- Types check
- PR description includes summary, rationale, and test notes

## Agent Instructions
- Before coding: propose a plan in bullets; wait for approval
- Avoid over-engineering
- When you complete work, send a structured summary to the lead
- If you discover issues outside your scope, message the lead — don't fix it yourself
```

### Key Principles for CLAUDE.md in Team Context

1. **Define file ownership boundaries explicitly** — prevents two teammates editing the same file
2. **Include DX commands** — teammates need to know how to run tests, lint, build
3. **Set conventions** — every teammate reads these, ensuring consistency
4. **Keep it under ~2,500 tokens** — it's loaded into EVERY teammate's context
5. **Include agent-specific instructions** — reporting expectations, coordination rules

---

## 8. Cost Optimisation Strategies

Agent teams are token-hungry. Every teammate is a full Claude session.

### Token Cost by Approach (Approximate)

| Approach | Tokens (3 workers) | Relative Cost |
|---|---|---|
| Solo session | ~200k | 1x |
| 3 Subagents | ~440k | ~2.2x |
| 3-person team | ~800k | ~4x |
| 5-person team | ~1.2M+ | ~6x |

### Optimisation Strategies

1. **Use Sonnet for workers, Opus for lead.** The lead needs strong reasoning for coordination. Workers doing focused execution can use cheaper models.

2. **Size tasks appropriately.** Aim for 5-6 tasks per teammate. Too granular = coordination overhead dominates. Too broad = you lose parallelism benefits.

3. **Use `write` not `broadcast`.** Broadcasting sends N messages for N teammates. Target specific teammates with `write`.

4. **Plan before parallelising.** A plan costs ~10k tokens. A misdirected team costs 500k+.

5. **Use subagents for simple delegation.** If workers don't need to talk to each other, subagents are cheaper.

6. **Pre-approve permissions.** Permission prompt floods waste tokens and time on back-and-forth.

7. **Keep CLAUDE.md concise.** It's loaded into every teammate's context. Bloated CLAUDE.md multiplied by N teammates = significant overhead.

---

## 9. Lessons from Anthropic's C Compiler (16-Agent Case Study)

Anthropic researcher Nicholas Carlini tasked 16 parallel Claude agents with building a Rust-based C compiler from scratch — 100,000 lines of code, capable of compiling the Linux kernel. This is the most ambitious public agent team deployment and its lessons are invaluable.

### Key Lessons

#### 1. Write Extremely High-Quality Tests

The test harness is your **surrogate supervision**. Without human oversight, agents will solve whatever the tests reward. If your tests are imperfect, agents solve the wrong problem.

**Practical application:** Before launching a team, invest heavily in your verification layer — tests, linters, type checks. These are what keep agents on track.

#### 2. Design the Environment for LLMs, Not Humans

Claude has specific limitations that your environment must account for:

- **Context window pollution:** Test output should be minimal — a few lines, not thousands. Log details to files. Pre-compute summary statistics.
- **Time blindness:** Claude can't tell time and will happily spend hours on tests. Include `--fast` options that run 10% samples. Print progress infrequently.
- **Orientation cost:** Each agent starts fresh. Include extensive READMEs and progress files that get updated frequently with current status.

#### 3. Make Parallelism Structurally Easy

When there are many independent failing tests, parallelism is trivial — each agent picks a different one. But when there's one giant task, all agents pile onto the same problem.

**Solution:** Use techniques that decompose monolithic tasks into independent units. In the compiler case, they used GCC as an oracle to randomly assign different file subsets to different agents.

**Practical application:** Structure your work so each agent can claim a distinct, non-overlapping unit. File-based ownership is the simplest approach.

#### 4. Use Specialised Agent Roles

Not all agents need to do the same thing. The compiler project used:

- **Feature agents** — implementing new compiler features
- **Deduplication agent** — finding and coalescing duplicate code
- **Performance agent** — optimising the compiler itself
- **Code quality agent** — structural improvements and Rust idioms
- **Documentation agent** — maintaining docs
- **Code critique agent** — reviewing from a Rust expert perspective

**Practical application:** Think about what roles a human team would have, then create agent roles to match.

#### 5. Git-Based Coordination Works at Scale

The 16-agent system used a simple git-based locking mechanism:

```
1. Agent writes a lock file to current_tasks/ (e.g., current_tasks/parse_if_statement.txt)
2. Agent works on the task
3. Agent pulls, merges, pushes, removes the lock
4. If two agents claim the same task, git sync forces the second to pick another
```

Merge conflicts are frequent but Claude handles them well.

---

## 10. Production Best Practices

### Pre-Flight Checklist

Before launching a team:

- [ ] Is the task complex enough to warrant a team? (If not, use solo or subagents)
- [ ] Do you have a clear plan with task decomposition?
- [ ] Are file ownership boundaries defined?
- [ ] Are tests and verification in place?
- [ ] Is CLAUDE.md configured with team conventions?
- [ ] Are permissions pre-approved for common operations?
- [ ] Have you chosen the right model for each role?

### During Execution

1. **Monitor actively.** Use split-pane mode (tmux) to catch teammates going off-track early.
2. **Steer early.** Course-correcting a teammate at 20% completion costs far less than at 80%.
3. **Check the task list.** Use `TaskList()` or read `~/.claude/tasks/{team}/*.json` to track progress.
4. **Watch inboxes.** `cat ~/.claude/teams/{team}/inboxes/team-lead.json | jq '.'`

### Shutdown & Cleanup

Always follow the graceful shutdown sequence:

```
1. requestShutdown for ALL teammates
2. Wait for shutdown approvals
3. Verify no active members remain
4. Call cleanup to remove team files
```

Never leave orphaned teams. If a teammate crashes, wait for the 5-minute heartbeat timeout, then its tasks can be reclaimed.

### The 80/20 Rule

Successful teams allocate **80% to planning and review, 20% to execution**. The better your specs, the better the agent output. This is the single strongest predictor of team success.

---

## 11. Anti-Patterns to Avoid

### ❌ "Build me an app"

Vague prompts burn tokens while agents flail. Always provide specific, scoped tasks.

### ❌ Two Teammates Editing the Same File

This leads to overwrites and merge conflicts. Break work so each teammate owns different files. Same boundary-setting you'd do with a human team.

### ❌ Skipping the Plan Step

Jumping straight to a team without a plan means the lead has to figure out task decomposition on the fly — which it can do, but you lose the chance to steer it.

### ❌ Over-Broadcasting

`broadcast` sends N messages for N teammates. Use `write` for targeted communication. Reserve broadcast for critical issues only.

### ❌ Under-Specifying Teammate Context

Teammates start with a blank conversation. Whatever context they need, the lead MUST provide in the spawn prompt. Skimping on context is the number one reason teammates produce mediocre work.

### ❌ Using Teams for Simple Tasks

If a single agent can handle it in one context window, don't add coordination overhead. Teams should only be used when parallelism genuinely adds value.

### ❌ Ignoring Token Costs

Agent teams use significantly more tokens than single sessions. Don't use Opus for every worker. Match model capability to task complexity.

### ❌ Not Pre-Approving Permissions

Each teammate generates permission prompts independently. Without pre-approval, you'll spend more time clicking "allow" than reviewing actual work.

---

## 12. Quick Reference Cheat Sheet

### Enable Agent Teams

```json
{ "env": { "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1" } }
```

### Spawn a Subagent (No Team)

```
Task({ subagent_type: "Explore", description: "Find files", prompt: "..." })
```

### Create a Team + Spawn Teammate

```
Teammate({ operation: "spawnTeam", team_name: "my-team" })

Task({
  team_name: "my-team",
  name: "worker-1",
  subagent_type: "general-purpose",
  model: "sonnet",
  prompt: "Your detailed instructions here...",
  run_in_background: true
})
```

### Create Task with Dependencies

```
TaskCreate({ subject: "Step 1: Research", description: "..." })
TaskCreate({ subject: "Step 2: Implement", description: "..." })
TaskUpdate({ taskId: "2", addBlockedBy: ["1"] })
```

### Message a Teammate

```
Teammate({ operation: "write", target_agent_id: "worker-1", value: "..." })
```

### Broadcast to All

```
Teammate({ operation: "broadcast", name: "team-lead", value: "Status check" })
```

### Shutdown Sequence

```
Teammate({ operation: "requestShutdown", target_agent_id: "worker-1" })
// Wait for approval...
Teammate({ operation: "cleanup" })
```

### Debug Commands

```bash
# Check team config
cat ~/.claude/teams/{team}/config.json | jq '.members[] | {name, agentType}'

# Check inboxes
cat ~/.claude/teams/{team}/inboxes/team-lead.json | jq '.'

# List tasks
cat ~/.claude/tasks/{team}/*.json | jq '{id, subject, status, owner}'

# Watch for messages
tail -f ~/.claude/teams/{team}/inboxes/team-lead.json
```

### Built-in Agent Types

| Type | Tools | Best For |
|---|---|---|
| `Explore` | Read-only, fast (Haiku) | Codebase exploration, searches |
| `Plan` | Read-only | Architecture, implementation planning |
| `Bash` | Bash only | Git ops, commands, system tasks |
| `general-purpose` | All tools | Implementation, multi-step work |
| `claude-code-guide` | Read + Web | Questions about Claude Code itself |

### Model Selection Guide

| Role | Recommended Model | Why |
|---|---|---|
| Team Lead | Opus | Best reasoning for coordination |
| Feature Implementer | Sonnet | Good balance of capability and cost |
| Researcher / Explorer | Haiku | Fast, cheap for read-only work |
| Code Reviewer | Sonnet | Needs reasoning but focused scope |
| Test Writer | Sonnet | Needs to understand code and write tests |

---

## Summary: The 10 Commandments of Agent Teams

1. **Plan first, parallelise second.** A 10k-token plan saves you from a 500k-token wrong direction.
2. **Give teammates rich context.** They start with nothing but your spawn prompt and CLAUDE.md.
3. **Define file ownership boundaries.** Two agents editing the same file = chaos.
4. **Size tasks at 5-6 per teammate.** The sweet spot between coordination overhead and parallelism.
5. **Use cheap models for workers, expensive models for coordination.**
6. **Write high-quality tests.** Tests are your surrogate supervision — they keep agents on track.
7. **Design your environment for LLMs.** Concise output, progress files, summary statistics.
8. **Always clean up.** Shutdown all teammates, then cleanup. Never leave orphaned teams.
9. **Monitor and steer early.** Course-correcting at 20% is much cheaper than at 80%.
10. **Know when NOT to use teams.** If a solo session can handle it, don't add coordination overhead.

---

*Guide compiled from: Anthropic official documentation, Anthropic engineering blog (C compiler case study by Nicholas Carlini), Addy Osmani's analysis, alexop.dev practical patterns, kieranklaassen's Swarm Orchestration Skill, SitePoint guide, Starmorph production case studies, and community practitioner reports. February 2026.*