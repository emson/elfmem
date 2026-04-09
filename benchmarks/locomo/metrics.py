"""Scoring functions for the LoCoMo benchmark.

Implements Porter-stemmed F1, multi-hop F1, adversarial scoring,
and retrieval recall — matching the LoCoMo paper exactly.
"""

import re
import string
from collections import Counter

from nltk.stem import PorterStemmer

_ps = PorterStemmer()

ABSTENTION_PHRASES = ("not mentioned", "no information available")


def normalize_answer(s: str) -> str:
    """Normalize text for scoring: lowercase, strip articles and punctuation, collapse whitespace."""
    s = str(s).lower()
    s = re.sub(r"\b(a|an|the|and)\b", " ", s)
    exclude = set(string.punctuation)
    s = "".join(ch for ch in s if ch not in exclude)
    return " ".join(s.split())


def f1_score(prediction: str, ground_truth: str) -> float:
    """Token-level F1 with Porter stemming and answer normalization.

    Args:
        prediction: Model's predicted answer.
        ground_truth: Gold-standard answer.

    Returns:
        F1 score between 0.0 and 1.0.
    """
    pred_tokens = [_ps.stem(w) for w in normalize_answer(prediction).split()]
    gt_tokens = [_ps.stem(w) for w in normalize_answer(ground_truth).split()]
    if not pred_tokens and not gt_tokens:
        return 1.0
    if not pred_tokens or not gt_tokens:
        return 0.0
    common = Counter(pred_tokens) & Counter(gt_tokens)
    num_same = sum(common.values())
    if num_same == 0:
        return 0.0
    precision = num_same / len(pred_tokens)
    recall = num_same / len(gt_tokens)
    return (2 * precision * recall) / (precision + recall)


def multihop_f1(prediction: str, ground_truth: str) -> float:
    """F1 for multi-hop questions (category 1).

    Splits ground truth by comma and averages F1 across parts.

    Args:
        prediction: Model's predicted answer.
        ground_truth: Comma-separated ground truth answers.

    Returns:
        Average F1 across all ground truth parts.
    """
    ground_truth = str(ground_truth)
    prediction = str(prediction)
    parts = [p.strip() for p in ground_truth.split(",")]
    if not parts:
        return f1_score(prediction, ground_truth)
    return sum(f1_score(prediction, part) for part in parts) / len(parts)


def openended_f1(prediction: str, ground_truth: str) -> float:
    """F1 for open-ended questions (category 3).

    Uses only the first semicolon-delimited segment of the ground truth.

    Args:
        prediction: Model's predicted answer.
        ground_truth: Ground truth, possibly with semicolons.

    Returns:
        F1 score against the first segment.
    """
    cleaned_gt = str(ground_truth).split(";")[0].strip()
    return f1_score(prediction, cleaned_gt)


def adversarial_score(prediction: str) -> float:
    """Score for adversarial questions (category 5).

    Binary check: 1.0 if the model abstains (detects unanswerable),
    0.0 if it provides an answer.

    Args:
        prediction: Model's response text.

    Returns:
        1.0 if abstained, 0.0 otherwise.
    """
    lower = prediction.lower()
    return 1.0 if any(phrase in lower for phrase in ABSTENTION_PHRASES) else 0.0


def retrieval_recall(retrieved_ids: list[str], evidence_ids: list[str]) -> float:
    """Proportion of evidence turn IDs found in retrieved context.

    Args:
        retrieved_ids: Turn IDs from retrieval (e.g. ["D1:3", "D2:1"]).
        evidence_ids: Gold evidence IDs for the question.

    Returns:
        Recall between 0.0 and 1.0. Returns 1.0 if evidence is empty.
    """
    if not evidence_ids:
        return 1.0
    evidence_set = set(evidence_ids)
    hits = sum(1 for rid in retrieved_ids if rid in evidence_set)
    return hits / len(evidence_set)


def score_qa(
    prediction: str,
    category: int,
    ground_truth: str | None = None,
    retrieved_ids: list[str] | None = None,
    evidence_ids: list[str] | None = None,
) -> dict[str, float]:
    """Score a single QA pair based on its category.

    Args:
        prediction: Model's predicted answer.
        category: Question category (1-5).
        ground_truth: Gold answer (None for category 5).
        retrieved_ids: Turn IDs from retrieval.
        evidence_ids: Gold evidence turn IDs.

    Returns:
        Dict with 'f1' (or 'adversarial') and optionally 'retrieval_recall'.
    """
    scores: dict[str, float] = {}

    if category == 5:
        scores["adversarial"] = adversarial_score(prediction)
    elif category == 1:
        scores["f1"] = multihop_f1(prediction, ground_truth or "")
    elif category == 3:
        scores["f1"] = openended_f1(prediction, ground_truth or "")
    else:
        scores["f1"] = f1_score(prediction, ground_truth or "")

    if evidence_ids is not None:
        scores["retrieval_recall"] = retrieval_recall(
            retrieved_ids or [], evidence_ids
        )

    return scores
