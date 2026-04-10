"""Metrics matching MemoryAgentBench's evaluation (eval_other_utils.py).

F1 without stemming, exact match, substring match, ROUGE-L.
"""

from __future__ import annotations

import re
import string
from collections import Counter

from rouge_score import rouge_scorer

_rouge = rouge_scorer.RougeScorer(["rougeL", "rougeLsum"], use_stemmer=True)


def normalize_answer(s: str) -> str:
    """Lower, remove punctuation/articles/whitespace."""
    s = str(s).lower()
    s = re.sub(r"\b(a|an|the|and)\b", " ", s)
    exclude = set(string.punctuation)
    s = "".join(ch for ch in s if ch not in exclude)
    return " ".join(s.split())


def f1_score(prediction: str, ground_truth: str) -> float:
    """Token-level F1 with normalization (no stemming — matches MABench)."""
    pred_tokens = normalize_answer(prediction).split()
    gt_tokens = normalize_answer(ground_truth).split()
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


def exact_match(prediction: str, ground_truth: str) -> float:
    """Normalized exact match."""
    return float(normalize_answer(prediction) == normalize_answer(ground_truth))


def substring_match(prediction: str, ground_truth: str) -> float:
    """Ground truth appears as substring in prediction."""
    return float(normalize_answer(ground_truth) in normalize_answer(prediction))


def rouge_l(prediction: str, ground_truth: str) -> dict[str, float]:
    """ROUGE-L F1 and recall."""
    scores = _rouge.score(ground_truth, prediction)
    return {
        "rougeL_f1": scores["rougeL"].fmeasure,
        "rougeL_recall": scores["rougeL"].recall,
    }


def score_question(prediction: str, ground_truths: list[str]) -> dict[str, float]:
    """Score a prediction against all ground truths, keeping best per metric."""
    best: dict[str, float] = {
        "f1": 0.0, "exact_match": 0.0, "substring_match": 0.0,
        "rougeL_f1": 0.0, "rougeL_recall": 0.0,
    }
    for gt in ground_truths:
        gt = str(gt)
        best["f1"] = max(best["f1"], f1_score(prediction, gt))
        best["exact_match"] = max(best["exact_match"], exact_match(prediction, gt))
        best["substring_match"] = max(best["substring_match"], substring_match(prediction, gt))
        r = rouge_l(prediction, gt)
        for k, v in r.items():
            best[k] = max(best[k], v)
    return best
