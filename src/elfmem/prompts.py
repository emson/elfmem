"""Default prompt templates and valid self-tag vocabulary for elfmem.

These are importable constants used as defaults when no overrides are
configured via PromptsConfig. The adapter factory resolves these once
at construction time via cfg.prompts.resolve_process_block() etc.
"""

from __future__ import annotations

VALID_SELF_TAGS: frozenset[str] = frozenset({
    "self/constitutional",
    "self/constraint",
    "self/value",
    "self/style",
    "self/goal",
    "self/context",
})

BLOCK_ANALYSIS_PROMPT: str = """\
You are analysing a memory block for an agent's adaptive memory system.

## Agent Identity
{self_context}

## Memory Block
{block}

Analyse this block and return three things:

1. **alignment_score** (0.0–1.0): How much does this block express or reinforce
   the agent's identity, values, or self-concept?
   - 0.0: Unrelated — technical fact, external knowledge, no identity relevance
   - 0.3: Adjacent — relevant to the agent's domain but not their identity
   - 0.7: Identity-adjacent — reflects how the agent thinks or works
   - 1.0: Core identity — directly states a value, constraint, or self-defining belief

2. **tags**: Which self/* tags apply? Choose from:
   - self/constitutional: core invariants — never violated, fundamental to existence
   - self/constraint: strong rules — rarely violated, firm preferences
   - self/value: beliefs and principles that consistently guide behavior
   - self/style: communication style, tone, and interaction preferences
   - self/goal: active goals or objectives the agent is pursuing
   - self/context: situational context about who the agent is or what they know
   A block may have 0, 1, or multiple tags.
   Only assign a tag if you are confident it applies. Prefer no tags over guessing.

3. **summary**: A factual 1–2 sentence distillation of the block content.
   Rules for the summary:
   - Preserve ALL specific details (names, numbers, preferences, constraints)
   - Remove filler words, formatting artifacts, and conversational tone
   - Write in third person ("User prefers..." not "I prefer...")
   - Keep domain-specific terms intact
   - If the content is already concise and factual, copy it as-is

Respond with JSON:
{{"alignment_score": <float>, "tags": [<strings>], "summary": "<string>"}}
"""

CONTRADICTION_PROMPT: str = """\
You are detecting logical contradictions between two memory blocks.

## Block A
{block_a}

## Block B
{block_b}

Rate how contradictory these blocks are:
- 0.0: Compatible — can both be true simultaneously
- 0.3: Tension — different emphases or perspectives, not directly contradictory
- 0.7: Conflicting — one implies the other is wrong or outdated
- 1.0: Direct contradiction — both cannot be true at the same time

Focus on logical contradiction, not just difference of opinion or emphasis.
Technical corrections (Block B updates/supersedes Block A) score high (0.7+).

Respond with JSON: {{"score": <float between 0.0 and 1.0>}}
"""
