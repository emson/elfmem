"""Default prompt templates and valid self-tag vocabulary for elfmem.

These are importable constants. The LiteLLMAdapter uses them as defaults
when no overrides are configured via PromptsConfig.
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

SELF_ALIGNMENT_PROMPT: str = """\
You are evaluating whether a memory block expresses the identity of an agent.

## Agent Identity
{self_context}

## Memory Block
{block}

Rate how much this block expresses, reinforces, or reflects the agent's identity,
values, or self-concept on a scale from 0.0 to 1.0:
- 0.0: Unrelated — technical fact, external knowledge, no identity relevance
- 0.3: Adjacent — relevant to the agent's domain but not their identity
- 0.7: Identity-adjacent — reflects how the agent thinks or works
- 1.0: Core identity — directly states a value, constraint, or self-defining belief

Respond with JSON: {{"score": <float between 0.0 and 1.0>}}
"""

SELF_TAG_PROMPT: str = """\
You are classifying a memory block against an agent's identity taxonomy.

## Agent Identity
{self_context}

## Memory Block
{block}

## Available Tags
- self/constitutional: core invariants — never violated, fundamental to existence
- self/constraint: strong rules — rarely violated, firm preferences
- self/value: beliefs and principles that consistently guide behavior
- self/style: communication style, tone, and interaction preferences
- self/goal: active goals or objectives the agent is pursuing
- self/context: situational context about who the agent is or what they know

Which tags apply? A block may have 0, 1, or multiple tags.
Only assign a tag if you are confident it applies. Prefer no tags over guessing.

Respond with JSON: {{"tags": [<list of applicable tag strings>]}}
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
