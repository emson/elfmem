# Exploration 007: Constitutional Self and Task-Parameterized Identity

## Status: complete

## Question

The SELF system prompt from Exploration 006 is monolithic — the same blocks appear
regardless of what the system is being asked to do. But different tasks make different
demands on identity. Can SELF adapt to its context while keeping a stable core that
never changes? What are the design rules for "constitutional" vs. "variable" blocks?

---

## The Problem with a Monolithic SELF

The current model (006) selects top-K self blocks by score, enforces a token budget,
and formats them into a single system prompt. This prompt is the same whether the
system is deciding what to consolidate, generating a response, or pruning old memory.

Consider what each task actually needs from identity:

| Task | Needs from Self | Doesn't Need |
|------|-----------------|--------------|
| Consolidation | Learning epistemics, domain expertise, what's worth knowing | Communication style, tone |
| Response generation | Communication style, honesty, expertise, tone | Learning epistemics, pruning criteria |
| Pruning | What's worth keeping, relevance criteria | Communication style, tone |
| Enhancement | Conceptual frameworks, how ideas connect | Tone, current context |
| Identity assembly | Core values, anchors, background | Task-specific styles |

A prompt that includes "I prefer concise bullet points" is irrelevant when the system is
deciding whether to consolidate a block. A prompt that includes "I evaluate knowledge by
whether it can be derived from first principles" is irrelevant when writing a casual reply.

**The monolithic prompt wastes budget on irrelevant identity and excludes relevant identity.**

---

## The Constitutional Question

Before task-parameterization, there is a prior question: are some blocks so fundamental
that they should be present in every prompt regardless of task?

Yes. These are **behavioral invariants** — the rules the system will never violate, the
things it will always do, the constraints that override any task-specific consideration.

**A constitutional block is different from a high-priority variable block:**

| Property | Variable Block | Constitutional Block |
|----------|---------------|---------------------|
| Selection | Scored, competes for budget | Pre-allocated before budget |
| Decay | Normal (λ > 0) | Effectively permanent (λ ≈ 0.00001) |
| Displacement | Can be displaced by reinforcement | Cannot be displaced |
| Discovery | Can be auto-discovered from usage | Requires explicit human designation |
| Content | Facts, preferences, styles, context | Behavioral invariants ONLY |
| Expression | "I prefer...", "I value...", "I think..." | "NEVER...", "ALWAYS...", "I must not..." |
| Count | Can be many (10-50) | Must be few (3-8 maximum) |
| Amendment | Updates through normal lifecycle | Requires explicit human review |

The distinction is not about score — a constitutional block might actually score lower
than a popular variable block. It's about the GUARANTEE: constitutional blocks are in the
prompt always, regardless of what else is happening.

---

## The Two-Axis Model

SELF blocks now exist on two axes:

```
                    CONSTITUTIONAL
                         │
                         │  C1: "I never fabricate"
                         │  C2: "I acknowledge uncertainty"
                         │  C3: "I don't take irreversible actions without confirmation"
  TASK-SPECIFIC  ────────┼──────────────────────  TASK-GENERAL
  (consolidation)        │                         (all tasks)
                         │  V1: "I value conciseness"     ← always useful
                 V4: "I learn by deriving from  │  V2: "I prefer directness"    ← always useful
                  first principles"             │  S1: "I think step-by-step"   ← mostly useful
                 V5: "I prioritise retention of │
                  falsifiable knowledge"        │
                         │
                    VARIABLE (non-constitutional)
```

The task-specific axis doesn't create new frame types. It creates **scoring modifiers**
that shift which variable blocks rise to the top for a given task.

---

## Setup: Block Corpus for This Exploration

```yaml
blocks:

  # ─── CONSTITUTIONAL BLOCKS (is_constitutional: true) ─────────────────────────
  # These are hardcoded at deployment, not discovered through learning.
  # λ ≈ 0 (never decay in any meaningful timeframe).
  # Budget allocation: first 100 tokens guaranteed.

  C1:
    content: "I never fabricate information or invent citations"
    category: identity/constraint
    is_self: true
    is_constitutional: true
    confidence: 0.99
    decay_lambda: 0.000001     # half-life: ~79 years
    reinforcement_count: 100   # always reinforced (placeholder: always-present)
    tokens: 16

  C2:
    content: "I acknowledge uncertainty when I don't know something"
    category: identity/constraint
    is_self: true
    is_constitutional: true
    confidence: 0.99
    decay_lambda: 0.000001
    reinforcement_count: 100
    tokens: 16

  C3:
    content: "I do not take irreversible actions without explicit confirmation"
    category: identity/constraint
    is_self: true
    is_constitutional: true
    confidence: 0.99
    decay_lambda: 0.000001
    reinforcement_count: 100
    tokens: 17

  # Constitutional section total: ~49 tokens (+ header overhead: ~60 tokens)

  # ─── VARIABLE BLOCKS (is_constitutional: false) ───────────────────────────────
  # These are discovered and scored normally through the lifecycle.
  # Grouped by relevant task type.

  # Relevant to ALL tasks (general identity)
  V1:
    content: "I value conciseness — say more with less"
    category: identity/style
    is_self: true
    decay_lambda: 0.0001
    confidence: 0.80
    reinforcement_count: 10

  V2:
    content: "I believe in clear, honest communication above persuasion"
    category: identity/value
    is_self: true
    decay_lambda: 0.0001
    confidence: 0.85
    reinforcement_count: 6

  # Relevant to CONSOLIDATION tasks (epistemics, learning)
  E1:
    content: "I learn by deriving from first principles rather than memorising surface patterns"
    category: identity/epistemics
    is_self: true
    decay_lambda: 0.0001
    confidence: 0.80
    reinforcement_count: 5

  E2:
    content: "I prioritise falsifiable, well-evidenced knowledge over opinion"
    category: identity/epistemics
    is_self: true
    decay_lambda: 0.0001
    confidence: 0.75
    reinforcement_count: 3

  E3:
    content: "I prefer to connect new knowledge to things I already understand"
    category: identity/epistemics
    is_self: true
    decay_lambda: 0.0001
    confidence: 0.70
    reinforcement_count: 4

  # Relevant to RESPONSE tasks (style, tone)
  R1:
    content: "I think step-by-step through problems before answering"
    category: identity/style
    is_self: true
    decay_lambda: 0.0001
    confidence: 0.75
    reinforcement_count: 8

  R2:
    content: "I adapt my technical depth to the audience's apparent expertise"
    category: identity/style
    is_self: true
    decay_lambda: 0.0001
    confidence: 0.70
    reinforcement_count: 5

  # Relevant to PRUNING tasks (what's worth keeping)
  P1:
    content: "I retain knowledge that generalises beyond a single context"
    category: identity/epistemics
    is_self: true
    decay_lambda: 0.0001
    confidence: 0.75
    reinforcement_count: 3

  # Context
  X1:
    content: "I am a Python developer working on AI memory systems"
    category: identity/context
    is_self: true
    decay_lambda: 0.001        # durable: context changes over months
    confidence: 0.80
    reinforcement_count: 7

# Token budget allocation
budget:
  total: 600
  constitutional_reserved: 100   # guaranteed for C1-C3
  overhead: 80                   # headers, whitespace, formatting
  variable_available: 420        # 600 - 100 - 80

# Task types with category scoring modifiers
task_types:
  response:
    boost: [identity/style, identity/value, identity/constraint]
    suppress: [identity/epistemics]
    boost_modifier: +0.20     # added to category score

  consolidation:
    boost: [identity/epistemics, identity/domain]
    suppress: [identity/style, identity/tone]
    boost_modifier: +0.20

  pruning:
    boost: [identity/epistemics, identity/value]
    suppress: [identity/style, identity/context]
    boost_modifier: +0.20

  identity:
    boost: [identity/value, identity/core, identity/context]
    suppress: []
    boost_modifier: +0.20

max_reinforcement_count: 10
```

---

## Computation: Constitutional Layer (same for all tasks)

Constitutional blocks are pre-selected. They don't compete with variable blocks.
Their only constraint: they must fit within the constitutional reserved budget (100 tokens).

```yaml
constitutional_selection:
  C1: 16 tokens  → running total: 16
  C2: 16 tokens  → running total: 32
  C3: 17 tokens  → running total: 49

constitutional_used: 49 / 100 tokens
constitutional_section:
  header: "## Constraints [Always]"
  blocks: [C1, C2, C3]
  note: "These are always present regardless of task"
```

Constitutional blocks are reinforced on every use (they appear in every prompt, so
`last_reinforced_at` updates every LLM call). This is correct: the constitution is the
most reinforced set of blocks in the system.

---

## Computation: Variable Layer — Task Type: RESPONSE

### Score Modifiers for RESPONSE Task

```yaml
# Base scores use standard SELF weights
# Then apply category modifier: +0.20 for boosted, -0.10 for suppressed

category_modifiers:
  identity/style:    +0.20  (boosted)
  identity/value:    +0.20  (boosted)
  identity/constraint: +0.20  (boosted, but constitutional blocks already selected)
  identity/epistemics: -0.10  (suppressed)
  identity/context:  0.00   (neutral)
```

### Component Scores (standard SELF formula)

Using SELF weights: rec=0.05, cen=0.25, conf=0.30, sim=0.10 (no query→0.5), reinf=0.30.
Max reinforcement across variable blocks = 10 (R1).

**Recency** (all λ=0.0001, assuming fresh access 48h ago for most):
```
V1:  e^(-0.0001 × 48)  = 0.9952
V2:  e^(-0.0001 × 48)  = 0.9952
E1:  e^(-0.0001 × 48)  = 0.9952
E2:  e^(-0.0001 × 48)  = 0.9952
E3:  e^(-0.0001 × 48)  = 0.9952
R1:  e^(-0.0001 × 12)  = 0.9988
R2:  e^(-0.0001 × 24)  = 0.9976
P1:  e^(-0.0001 × 96)  = 0.9904
X1:  e^(-0.001 × 48)   = 0.9531
```

**Centrality** (simplified: neutral 0.5 for all in this exploration):
```
All: 0.50
```

**Reinforcement** (log-normalized, max=10):
```
V1:  log(11)/log(11) = 1.000
R1:  log(9)/log(11)  = 0.916
V2:  log(7)/log(11)  = 0.748
X1:  log(8)/log(11)  = 0.832
R2:  log(6)/log(11)  = 0.673
E1:  log(6)/log(11)  = 0.673
E3:  log(5)/log(11)  = 0.618
E2:  log(4)/log(11)  = 0.531
P1:  log(4)/log(11)  = 0.531
```

**Base Scores (before task modifier):**
```
V1:  0.05×0.995 + 0.25×0.5 + 0.30×0.80 + 0.10×0.5 + 0.30×1.000
   = 0.050 + 0.125 + 0.240 + 0.050 + 0.300 = 0.765

V2:  0.05×0.995 + 0.25×0.5 + 0.30×0.85 + 0.10×0.5 + 0.30×0.748
   = 0.050 + 0.125 + 0.255 + 0.050 + 0.224 = 0.704

R1:  0.05×0.999 + 0.25×0.5 + 0.30×0.75 + 0.10×0.5 + 0.30×0.916
   = 0.050 + 0.125 + 0.225 + 0.050 + 0.275 = 0.725

X1:  0.05×0.953 + 0.25×0.5 + 0.30×0.80 + 0.10×0.5 + 0.30×0.832
   = 0.048 + 0.125 + 0.240 + 0.050 + 0.250 = 0.713

R2:  0.05×0.998 + 0.25×0.5 + 0.30×0.70 + 0.10×0.5 + 0.30×0.673
   = 0.050 + 0.125 + 0.210 + 0.050 + 0.202 = 0.637

E1:  0.05×0.995 + 0.25×0.5 + 0.30×0.80 + 0.10×0.5 + 0.30×0.673
   = 0.050 + 0.125 + 0.240 + 0.050 + 0.202 = 0.667

E3:  0.05×0.995 + 0.25×0.5 + 0.30×0.70 + 0.10×0.5 + 0.30×0.618
   = 0.050 + 0.125 + 0.210 + 0.050 + 0.185 = 0.620

E2:  0.05×0.995 + 0.25×0.5 + 0.30×0.75 + 0.10×0.5 + 0.30×0.531
   = 0.050 + 0.125 + 0.225 + 0.050 + 0.159 = 0.609

P1:  0.05×0.990 + 0.25×0.5 + 0.30×0.75 + 0.10×0.5 + 0.30×0.531
   = 0.050 + 0.125 + 0.225 + 0.050 + 0.159 = 0.609
```

**Task-Modified Scores (RESPONSE: +0.20 for style/value, -0.10 for epistemics):**
```
V1:  0.765 + 0.20 = 0.965  ← identity/style boosted
V2:  0.704 + 0.20 = 0.904  ← identity/value boosted
R1:  0.725 + 0.20 = 0.925  ← identity/style boosted
R2:  0.637 + 0.20 = 0.837  ← identity/style boosted
X1:  0.713 + 0.00 = 0.713  ← identity/context neutral
E1:  0.667 - 0.10 = 0.567  ← identity/epistemics suppressed
E3:  0.620 - 0.10 = 0.520  ← suppressed
E2:  0.609 - 0.10 = 0.509  ← suppressed
P1:  0.609 - 0.10 = 0.509  ← suppressed
```

**RESPONSE Ranking:**
```
1. V1  0.965  "value conciseness"          style    ← 45 tokens
2. R1  0.925  "think step-by-step"          style    ← 60 tokens
3. V2  0.904  "clear honest communication"  value    ← 55 tokens
4. R2  0.837  "adapt technical depth"       style    ← 58 tokens
5. X1  0.713  "Python developer, AI memory" context  ← 62 tokens
6. E1  0.567  "learn from first principles" epistemics ← (suppressed, lower)
```

**Budget allocation (420 tokens available for variable):**
```
V1  (45): used = 45   ✓ include
R1  (60): used = 105  ✓ include
V2  (55): used = 160  ✓ include
R2  (58): used = 218  ✓ include
X1  (62): used = 280  ✓ include
E1  (65): used = 345  ✓ include (still fits despite suppression — budget not exhausted)
E2  (60): used = 405  ✓ include
E3  (62): used = 467  ← exceeds 420 limit → EXCLUDED
P1  (58): ← EXCLUDED

Variable used: 405 / 420 tokens
```

---

## Computation: Variable Layer — Task Type: CONSOLIDATION

**Task-Modified Scores (CONSOLIDATION: +0.20 for epistemics/domain, -0.10 for style/tone):**
```
V1:  0.765 - 0.10 = 0.665  ← identity/style suppressed
V2:  0.704 + 0.00 = 0.704  ← identity/value neutral
R1:  0.725 - 0.10 = 0.625  ← identity/style suppressed
R2:  0.637 - 0.10 = 0.537  ← suppressed
X1:  0.713 + 0.00 = 0.713  ← neutral
E1:  0.667 + 0.20 = 0.867  ← identity/epistemics boosted
E3:  0.620 + 0.20 = 0.820  ← boosted
E2:  0.609 + 0.20 = 0.809  ← boosted
P1:  0.609 + 0.20 = 0.809  ← boosted
```

**CONSOLIDATION Ranking:**
```
1. E1  0.867  "derive from first principles"   epistemics ← 65 tokens
2. E3  0.820  "connect to existing knowledge"  epistemics ← 62 tokens
3. E2  0.809  "falsifiable, well-evidenced"    epistemics ← 60 tokens
4. P1  0.809  "retain knowledge that generalises" epistemics ← 58 tokens
5. X1  0.713  "Python developer, AI memory"    context    ← 62 tokens
6. V2  0.704  "clear honest communication"     value      ← 55 tokens
7. V1  0.665  "value conciseness"              style      ← 45 tokens
8. R1  0.625  "think step-by-step"             style      ← 60 tokens
```

**Budget allocation (420 tokens available):**
```
E1  (65):  65  ✓
E3  (62): 127  ✓
E2  (60): 187  ✓
P1  (58): 245  ✓
X1  (62): 307  ✓
V2  (55): 362  ✓
V1  (45): 407  ✓
R1  (60): 467  ← exceeds 420 → EXCLUDED

Variable used: 407 / 420 tokens
```

---

## Side-by-Side Comparison

```yaml
# What changes between RESPONSE and CONSOLIDATION prompts:

constitutional: [C1, C2, C3]  # SAME — always present

RESPONSE_variable:
  included: [V1, R1, V2, R2, X1, E1, E2]
  focus: style, communication, step-by-step reasoning
  excluded: E3, P1 (budget; epist. suppressed)

CONSOLIDATION_variable:
  included: [E1, E3, E2, P1, X1, V2, V1]
  focus: epistemics, learning criteria, what to retain
  excluded: R1 (style, suppressed)

# What the LLM "believes" about itself differs:
# RESPONSE mode: "I'm clear, concise, think step-by-step"
# CONSOLIDATION mode: "I derive from first principles, retain falsifiable knowledge"
```

### The System Prompts Produced

**RESPONSE system prompt:**
```markdown
## Constraints [Always]
- I never fabricate information or invent citations
- I acknowledge uncertainty when I don't know something
- I do not take irreversible actions without explicit confirmation

## What I Value
- I believe in clear, honest communication above persuasion

## How I Work
- I value conciseness — say more with less
- I think step-by-step through problems before answering
- I adapt my technical depth to the audience's apparent expertise

## Current Focus
- I am a Python developer working on AI memory systems

## Knowledge Standards
- I learn by deriving from first principles rather than memorising surface patterns
- I prioritise falsifiable, well-evidenced knowledge over opinion
```

**CONSOLIDATION system prompt:**
```markdown
## Constraints [Always]
- I never fabricate information or invent citations
- I acknowledge uncertainty when I don't know something
- I do not take irreversible actions without explicit confirmation

## Knowledge Standards
- I learn by deriving from first principles rather than memorising surface patterns
- I prefer to connect new knowledge to things I already understand
- I prioritise falsifiable, well-evidenced knowledge over opinion
- I retain knowledge that generalises beyond a single context

## Current Focus
- I am a Python developer working on AI memory systems

## What I Value
- I believe in clear, honest communication above persuasion

## What I Value
- I value conciseness — say more with less
```

The constitutional section is identical. The body changes to reflect what identity
properties matter most for this specific cognitive task.

---

## Edge Cases and Mitigations

### Edge Case 1: Constitutional Budget Overflow

```yaml
scenario: 10 constitutional blocks with verbose content → exceeds 100-token allocation

problem: Constitution cannot fit in its reserved budget
         More constitutional blocks = less space for variable = poorer task adaptation

mitigation:
  rule_1: "Constitutional blocks MUST be behavioural constraints (NEVER/ALWAYS/MUST NOT)"
  rule_2: "Constitutional blocks MUST be terse: target 10-20 tokens, hard max 30 tokens"
  rule_3: "Maximum 8 constitutional blocks enforced at deployment configuration"
  rule_4: "If constitutional blocks exceed 150 tokens, reject the deployment configuration"

  enforcement: Constitutional block registration requires validation:
    - Content matches NEVER/ALWAYS/MUST NOT pattern
    - Content is under 30 tokens
    - Total constitutional count under 8
```

### Edge Case 2: Task Type Unknown or Ambiguous

```yaml
scenario: SELF is assembled without a task_type parameter

options:
  A: Default to RESPONSE (most general task type)
  B: Apply zero modifiers (pure SELF scoring, no task bias)
  C: Apply average modifiers across all task types

decision: Option A — default to RESPONSE
  reason:
    - Most LLM calls are response generation
    - RESPONSE task type includes general style, which is broadly applicable
    - Avoids the "no modifier = all epistemics and style equally weighted" problem
    - Simple fallback rule, easy to implement
```

### Edge Case 3: Constitutional Block Conflicts with Task Requirement

```yaml
scenario:
  C3 (constitutional): "I do not take irreversible actions without explicit confirmation"
  Task: consolidation agent deciding to prune blocks (irreversible action)

problem: Constitution says "ask first". Task requires autonomous pruning.

resolution:
  Rule: Constitutional always wins.
  Implementation: Pruning agent sees C3 in its prompt → must request confirmation
                  before marking any block as pruned
  Design consequence: Pruning cannot be fully automated. It must be semi-autonomous,
                      with a human-review step before hard deletion.

  This is the correct behavior. The constitution is doing its job.
```

### Edge Case 4: Constitutional Block Becomes Wrong

```yaml
scenario:
  C_old: "I always respond in English"  (was constitutional at deployment)
  → System deployed in a multilingual context
  → Constitution is now wrong: blocks multilingual responses

problem: Hard-coded constraint is now harmful but can't be displaced normally

resolution: Constitutional Amendment Process:
  1. Flag: Human reviewer marks C_old for review
  2. Draft: Write replacement C_new: "I respond in the user's language"
  3. Review: Period of 48h review before activation
  4. Swap: Remove C_old, add C_new atomically
  5. Log: Full audit trail of who, when, why

  Note: C_old still exists as a memory block (is_constitutional revoked, not deleted).
        It becomes a regular self block with high confidence but subject to normal decay.

key_principle: "Constitutional amendment is deliberate, logged, and never automatic."
```

### Edge Case 5: Task Modifier Amplifies an Error

```yaml
scenario:
  E2: "I prioritise opinion-based knowledge from trusted sources"
      (WRONG - this was incorrectly consolidated)
      confidence: 0.75, category: identity/epistemics

  CONSOLIDATION task type: +0.20 boost to identity/epistemics
  → E2 rises to top of CONSOLIDATION prompt despite being wrong
  → Consolidation agent now preferentially consolidates opinion-based content
  → System learns more opinion-based content
  → E2 gets reinforced more
  → Feedback loop: wrong epistemics → wrong content → wrong epistemics

mitigation:
  1_confidence_threshold:
    rule: Variable blocks below confidence 0.70 cannot enter system prompt
          regardless of task modifier boost
    result: Low-confidence errors don't get amplified

  2_human_review_of_self:
    rule: Self blocks with category identity/epistemics are flagged for
          periodic human review (they have outsized influence)
    result: Epistemics errors are caught before they compound

  3_provenance:
    rule: Blocks auto-tagged is_self by the consolidation agent (not human)
          start at confidence 0.60 (below threshold)
    result: System-discovered self blocks need reinforcement before they
            can influence the prompt — not immediately active
```

### Edge Case 6: Task Modifiers Create Perverse Incentives

```yaml
scenario:
  Agent is in CONSOLIDATION mode (+0.20 to identity/epistemics)
  Agent ingests content about epistemics
  → Epistemics blocks get reinforced (retrieval during consolidation)
  → Epistemics blocks have both high scores AND task boost
  → CONSOLIDATION prompt fills entirely with epistemics blocks
  → Other identity (context, values) crowded out
  → Monocultural self in consolidation mode

mitigation:
  section_limits:
    max_blocks_per_section: 3
    max_fraction_one_category: 0.50  # no category can exceed 50% of variable budget

  reasoning: Same as entropy monitoring from Exploration 006.
             Diversity of self is as important as relevance.
```

### Edge Case 7: Multiple Concurrent Task Types

```yaml
scenario: "Review this paper and summarise it AND decide if it's worth consolidating"
          → Both RESPONSE (summarise) and CONSOLIDATION (decide) required simultaneously

options:
  A: Pick the primary task (RESPONSE wins if user-facing)
  B: Union of boosted categories from both tasks
  C: Two-pass assembly: run twice, deduplicate

decision: Option B — union boost
  implementation:
    combined_modifiers:
      identity/style:     +0.20  (from RESPONSE)
      identity/epistemics: +0.20  (from CONSOLIDATION)
      identity/value:     +0.20  (from RESPONSE)
      # No suppression when tasks conflict

  result: Variable layer fills with broadly relevant identity.
          No task suppression, more blocks compete at neutral score.
          Total prompt likely 5-7 diverse blocks.
```

---

## The Constitutional Amendment Process (Full Specification)

```yaml
amendment_process:

  who_can_initiate: [human_operator, system_alert]

  trigger_conditions:
    - Constitutional block is factually wrong
    - Constitutional block conflicts with new deployment context
    - Constitutional block is no longer needed
    - New behavioral invariant needs to be added

  steps:
    1_flag:
      actor: human_operator
      action: mark block.amendment_pending = true
      note: "Block remains active during review"

    2_draft:
      actor: human_operator
      action: write replacement or removal
      validation:
        - Must pass constitutional_block_validation()
          (NEVER/ALWAYS/MUST NOT pattern, <30 tokens, total count <8)

    3_review:
      duration: minimum 48h
      actor: second human reviewer
      can_reject: true

    4_activate:
      actor: system (on approval)
      action:
        - Old block: is_constitutional = false, confidence -= 0.30 (demoted)
        - New block: is_constitutional = true, confidence = 0.99
      atomic: true  # both happen in one transaction

    5_audit:
      log_entry:
        block_old: <id>
        block_new: <id>
        reason: <text>
        initiated_by: <operator>
        approved_by: <reviewer>
        activated_at: <timestamp>
```

---

## Result: The Complete Architecture

```yaml
self_system_prompt_v2:

  layer_0_constitutional:
    description: "Behavioral invariants, always present"
    selection: pre-allocated, not scored
    blocks: is_constitutional = true
    budget: 100 tokens (reserved before variable allocation)
    amendment: explicit human process only
    decay: λ = 0.000001 (effectively permanent)
    count: 3-8 blocks maximum

  layer_1_selection:
    description: "Standard SELF frame assembly (unchanged from 006)"
    mechanism: SELF scoring weights applied to all variable self blocks
    weights: { recency: 0.05, centrality: 0.25, confidence: 0.30,
               similarity: 0.10, reinforcement: 0.30 }
    task_modifier: ±0.20 applied to category scores based on task_type

  layer_2_budget:
    description: "Token budget enforcement"
    available: 600 - 100 (constitutional) - 80 (overhead) = 420 tokens
    mechanism: greedy selection within budget
    constraints:
      max_fraction_one_category: 0.50
      max_blocks_per_section: 3

  layer_3_contradiction:
    description: "Same as 006"
    mechanism: pairwise similarity + confidence comparison

  layer_4_formatting:
    description: "Template formatting with task-aware section headers"
    sections_order: [Constraints (constitutional), task-relevant sections, other]

  layer_5_cache:
    description: "Separate cache per task_type"
    keys: [self_prompt_response, self_prompt_consolidation, self_prompt_pruning, ...]
    ttl: 3600
    invalidation: same triggers as 006, applied per task cache

  constitutional_cache:
    description: "Constitutional section cached separately"
    key: self_prompt_constitutional
    ttl: 86400  # 24 hours — changes very rarely
    invalidation: constitutional amendment only
```

---

## Insight

### 1. Constitutional vs. High-Priority Is a Genuine Distinction

A constitutional block is not just a high-scored block. It has different mechanisms:
guaranteed budget allocation, near-zero decay, amendment-only modification, and
behavioral invariant content. A high-priority variable block can be displaced by
reinforcement; a constitutional block cannot. This distinction is necessary.

### 2. Constitutional Blocks Must Be Behavioral, Not Factual

"I never fabricate" is behavioral — it describes what the system does, not what it knows.
"I am a Python developer" is factual — it describes the agent's context. Factual content
belongs in variable blocks because facts change. Behavioral invariants don't.

This is the key principle for identifying constitutional blocks:
**If it can become wrong due to external circumstances changing, it's not constitutional.**

### 3. Task Parameterization Uses the Same Mechanism

The task modifier is just a scoring shift. It doesn't create new frame types, new storage
paths, or new retrieval mechanisms. It's an additional input to the existing scoring formula:

```
effective_score = base_SELF_score + task_category_modifier
```

The axioms all hold. The mechanism is unchanged.

### 4. The Constitution Protects Against the System's Own Errors

Edge case 5 (wrong epistemics block gets amplified) shows that the variable layer can
compound errors if they get reinforced. The constitution provides a floor of correct
behavior even if the variable layer malfunctions. "I never fabricate" holds even if the
learning system incorrectly teaches the agent that fabrication is acceptable in some cases.

The constitution is the safety net below the evolving self.

### 5. Separate Caches Make the Multi-Task Model Efficient

A single cache per task type means:
- Constitutional section: refreshed daily (very stable)
- RESPONSE cache: refreshed when response-relevant self blocks change
- CONSOLIDATION cache: refreshed when epistemics blocks change

These change on different cadences. Separating them avoids over-invalidation.
The constitutional cache rarely invalidates; the RESPONSE cache might invalidate weekly
as style preferences are reinforced; the CONSOLIDATION cache is most stable of all.

### 6. The Model Is Scalable

With 5 task types and 50 self blocks:
- Each task type surfaces 6-8 blocks (different subsets)
- The constitutional layer (3-5 blocks) is shared
- Total unique prompt configurations: 5 prompts, each 300-500 tokens
- Each is cached separately, stable between relevant events

The number of variable self blocks can grow to hundreds; the prompt stays small because
the task-parameterized budget enforces selection. More self blocks = richer identity
expression, but never a larger prompt.

---

## What This Changes vs. Exploration 006

```python
# 006: Monolithic self prompt
class SelfFrame:
    def get_cached_prompt(self) -> str:
        ...

# 007: Constitutional + task-parameterized
class SelfFrame:
    def get_constitutional_blocks(self) -> List[MemoryBlock]:
        # Returns is_constitutional = true blocks
        # No scoring — pre-guaranteed
        ...

    def get_variable_blocks(self, task_type: str) -> List[MemoryBlock]:
        # Standard SELF scoring + task category modifier
        # Budget = total - constitutional_used - overhead
        ...

    def get_cached_prompt(self, task_type: str = "response") -> str:
        # Two separate caches: constitutional + task-specific variable
        # Constitutional cache invalidated by amendment only
        # Variable cache invalidated by standard triggers (006 logic)
        ...
```

Two new methods. One new parameter. The rest of 006 is unchanged.

---

## Variations

- [ ] What if constitutional blocks are stored as permanent memory blocks vs. config?
- [ ] What if task type is auto-detected from the query rather than passed explicitly?
- [ ] Test: what does the prompt look like with a 300 token budget instead of 600?
- [ ] Can the constitutional section itself be generated from the very highest-scored invariant blocks?
- [ ] What if task modifiers are learned rather than hand-specified? (self learns what matters for consolidation)
- [ ] Test entropy monitoring: at what point does a category dominate and need suppression?
- [ ] Should constitutional blocks appear in EVERY frame type (ATTENTION, WORLD) or only in Self?
