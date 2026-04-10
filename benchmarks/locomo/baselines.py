"""No-retrieval and perfect-retrieval baselines for LoCoMo."""

from __future__ import annotations

from benchmarks.locomo.config import LoCoMoConfig
from benchmarks.locomo.data import Conversation, QAPair
from benchmarks.shared.answerer import generate_answer

# ── Category name mapping (LoCoMo uses integer codes) ──────────────────────

CATEGORY_NAMES: dict[int, str] = {
    1: "multi-hop",
    2: "temporal",
    3: "open-ended",
    4: "single-hop",
    5: "adversarial",
}


def _category_name(cat: int) -> str:
    return CATEGORY_NAMES.get(cat, f"cat-{cat}")


def _find_evidence_turns(conversation: Conversation, evidence_ids: list[str]) -> str:
    """Look up the exact turn text for a list of evidence dia IDs."""
    id_to_text: dict[str, str] = {}
    for session in conversation.sessions:
        for turn in session.turns:
            id_to_text[turn.dia_id] = f"[{session.date_time}] {turn.speaker}: {turn.text}"
    lines = [id_to_text[eid] for eid in evidence_ids if eid in id_to_text]
    return "\n".join(lines)


async def run_no_retrieval_baseline(
    qa_pairs: list[QAPair],
    config: LoCoMoConfig,
) -> list[dict]:
    """Answer each question with zero context (floor baseline).

    USE WHEN: Establishing the baseline floor — what the LLM knows without memory.
    DON'T USE WHEN: You need retrieval-augmented answers.
    COST: One LLM call per question (answer model only).
    RETURNS: List of per-question result dicts matching report.py's questions schema.
    NEXT: Pass to build_report as baseline_results.
    """
    results: list[dict] = []
    for qa in qa_pairs:
        prediction = await generate_answer(
            context="",
            question=qa.question,
            category=qa.category,
            adversarial_answer=qa.adversarial_answer or "",
            model=config.answer_model,
            max_tokens=config.answer_max_tokens,
            base_url=config.lm_studio_base_url,
        )
        results.append({
            "category": _category_name(qa.category),
            "question": qa.question,
            "ground_truth": qa.answer or "",
            "prediction": prediction,
            "evidence_ids": qa.evidence,
            "adversarial_answer": qa.adversarial_answer,
        })
    return results


async def run_perfect_retrieval_baseline(
    conversation: Conversation,
    qa_pairs: list[QAPair],
    config: LoCoMoConfig,
) -> list[dict]:
    """Answer each question with ground-truth evidence turns (ceiling baseline).

    USE WHEN: Establishing the retrieval ceiling — best possible context.
    DON'T USE WHEN: You want to test elfmem's retrieval quality.
    COST: One LLM call per question (answer model only).
    RETURNS: List of per-question result dicts matching report.py's questions schema.
    NEXT: Pass to build_report as baseline_results.
    """
    results: list[dict] = []
    for qa in qa_pairs:
        context = _find_evidence_turns(conversation, qa.evidence)
        prediction = await generate_answer(
            context=context,
            question=qa.question,
            category=qa.category,
            adversarial_answer=qa.adversarial_answer or "",
            model=config.answer_model,
            max_tokens=config.answer_max_tokens,
            base_url=config.lm_studio_base_url,
        )
        results.append({
            "category": _category_name(qa.category),
            "question": qa.question,
            "ground_truth": qa.answer or "",
            "prediction": prediction,
            "evidence_ids": qa.evidence,
            "adversarial_answer": qa.adversarial_answer,
        })
    return results
