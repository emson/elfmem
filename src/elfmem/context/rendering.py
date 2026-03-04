"""Render scored blocks into text for LLM injection."""

from __future__ import annotations

from collections.abc import Callable

from elfmem.types import ScoredBlock


def render_blocks(
    blocks: list[ScoredBlock],
    template: str,
    token_budget: int,
) -> str:
    """Render scored blocks into text using the specified template.

    Enforces token budget by greedily including blocks from highest to lowest
    score until the budget is exceeded.

    Args:
        blocks: Scored blocks, sorted by score descending.
        template: Template name ("self", "attention", "task").
        token_budget: Approximate character budget.

    Returns:
        Rendered text string.
    """
    if not blocks:
        return ""

    if template == "self":
        return _render_with_budget(blocks, token_budget, _render_self_template)
    elif template == "task":
        return _render_with_budget(blocks, token_budget, _render_task_template)
    else:
        return _render_with_budget(blocks, token_budget, _render_attention_template)


def _render_with_budget(
    blocks: list[ScoredBlock],
    token_budget: int,
    render_fn: Callable[[list[ScoredBlock]], str],
) -> str:
    """Greedily include blocks until token budget is reached."""
    fn = render_fn
    selected: list[ScoredBlock] = []
    for block in blocks:
        candidate = selected + [block]
        text = fn(candidate)
        if _estimate_tokens(text) <= token_budget:
            selected = candidate
        else:
            break
    if not selected:
        return ""
    return fn(selected)


def _render_self_template(blocks: list[ScoredBlock]) -> str:
    """Render blocks in identity/instruction style."""
    lines = ["## Identity"]
    for block in blocks:
        lines.append(f"- {block.content}")
    return "\n".join(lines)


def _render_attention_template(blocks: list[ScoredBlock]) -> str:
    """Render blocks in knowledge/context style."""
    lines = ["## Relevant Knowledge"]
    for i, block in enumerate(blocks, 1):
        lines.append(f"[{i}] {block.content}")
    return "\n".join(lines)


def _render_task_template(blocks: list[ScoredBlock]) -> str:
    """Render blocks in goal/task style."""
    goal_blocks = [b for b in blocks if "self/goal" in b.tags]
    other_blocks = [b for b in blocks if "self/goal" not in b.tags]

    lines = []
    if goal_blocks:
        lines.append("## Active Goals")
        for block in goal_blocks:
            lines.append(f"- {block.content}")
    if other_blocks:
        lines.append("## Context")
        for i, block in enumerate(other_blocks, 1):
            lines.append(f"[{i}] {block.content}")
    return "\n".join(lines) if lines else ""


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: len(text) // 4."""
    return len(text) // 4
