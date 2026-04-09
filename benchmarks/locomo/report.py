"""Build standard benchmark report JSON per benchmark_report_spec.md."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from importlib.metadata import version as pkg_version
from typing import Any

from benchmarks.locomo.config import LoCoMoConfig

# ── Category name mapping ───────────────────────────────────────────────────

CATEGORY_NAMES: dict[int, str] = {
    1: "single-hop",
    2: "multi-hop",
    3: "temporal",
    4: "open-domain",
    5: "adversarial",
}


def _category_name(cat: int) -> str:
    return CATEGORY_NAMES.get(cat, f"cat-{cat}")


def _safe_mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


# ── Scoring helpers ─────────────────────────────────────────────────────────


def _compute_scores(questions: list[dict]) -> dict[str, Any]:
    """Compute overall and per-category scores from question results."""
    all_scores = [q["score"] for q in questions]
    overall = _safe_mean(all_scores) * 100

    by_category: dict[str, dict[str, Any]] = {}
    for q in questions:
        cat = q["category"]
        by_category.setdefault(cat, {"scores": [], "count": 0})
        by_category[cat]["scores"].append(q["score"])
        by_category[cat]["count"] += 1

    return {
        "overall": round(overall, 1),
        "by_category": {
            cat: {
                "score": round(_safe_mean(data["scores"]) * 100, 1),
                "count": data["count"],
            }
            for cat, data in sorted(by_category.items())
        },
    }


def _compute_retrieval(questions: list[dict]) -> dict[str, Any]:
    """Compute retrieval recall overall and per-category."""
    all_recalls = [q["retrieval_recall"] for q in questions]
    overall = _safe_mean(all_recalls) * 100

    by_category: dict[str, dict[str, Any]] = {}
    for q in questions:
        cat = q["category"]
        by_category.setdefault(cat, {"recalls": [], "count": 0})
        by_category[cat]["recalls"].append(q["retrieval_recall"])
        by_category[cat]["count"] += 1

    return {
        "overall_recall": round(overall, 1),
        "by_category": {
            cat: {
                "recall": round(_safe_mean(data["recalls"]) * 100, 1),
                "count": data["count"],
            }
            for cat, data in sorted(by_category.items())
        },
    }


def _compute_baseline_scores(baseline_questions: list[dict]) -> dict[str, Any]:
    """Compute scores from baseline question results (same structure as _compute_scores)."""
    all_scores = [q["score"] for q in baseline_questions]
    overall = _safe_mean(all_scores) * 100

    by_category: dict[str, dict[str, Any]] = {}
    for q in baseline_questions:
        cat = q["category"]
        by_category.setdefault(cat, {"scores": [], "count": 0})
        by_category[cat]["scores"].append(q["score"])
        by_category[cat]["count"] += 1

    return {
        "overall": round(overall, 1),
        "by_category": {
            cat: {
                "score": round(_safe_mean(data["scores"]) * 100, 1),
                "count": data["count"],
            }
            for cat, data in sorted(by_category.items())
        },
    }


# ── Elfmem version ──────────────────────────────────────────────────────────


def _get_elfmem_version() -> str:
    """Get elfmem version from package metadata."""
    try:
        return pkg_version("elfmem")
    except Exception:
        return "unknown"


# ── Report builder ──────────────────────────────────────────────────────────


def build_report(
    all_question_results: list[dict],
    baseline_results: dict[str, list[dict]] | None,
    config: LoCoMoConfig,
    start_time: float,
    *,
    total_blocks_learned: int = 0,
    total_blocks_active: int = 0,
    total_memorization_seconds: float = 0.0,
) -> dict[str, Any]:
    """Build standard benchmark report JSON.

    USE WHEN: All questions have been answered and scored.
    DON'T USE WHEN: Results are still being collected — wait for completion.
    COST: Zero (pure computation).
    RETURNS: Dict conforming to benchmark_report_spec.md, ready for JSON serialisation.
    NEXT: Write to benchmarks/locomo/results/{timestamp}_locomo_elfmem.json.
    """
    duration = time.monotonic() - start_time
    timestamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    query_seconds = [q["query_seconds"] for q in all_question_results]
    total_query_seconds = sum(query_seconds)

    scores = _compute_scores(all_question_results)
    retrieval = _compute_retrieval(all_question_results)

    baselines: dict[str, Any] = {
        "no_retrieval": {"overall": 0.0, "by_category": {}},
        "perfect_retrieval": {"overall": 0.0, "by_category": {}},
    }
    if baseline_results:
        if "no_retrieval" in baseline_results:
            baselines["no_retrieval"] = _compute_baseline_scores(
                baseline_results["no_retrieval"]
            )
        if "perfect_retrieval" in baseline_results:
            baselines["perfect_retrieval"] = _compute_baseline_scores(
                baseline_results["perfect_retrieval"]
            )

    return {
        "meta": {
            "benchmark": "locomo",
            "version": "1.0",
            "timestamp": timestamp,
            "duration_seconds": round(duration, 1),
            "elfmem_version": _get_elfmem_version(),
            "models": {
                "consolidation_llm": config.elfmem_llm_model,
                "embedding": config.elfmem_embedding_model,
                "embedding_dimensions": config.elfmem_embedding_dimensions,
                "answer_llm": config.answer_model,
                "judge": None,
            },
            "elfmem_config": {
                "top_k": config.top_k,
                "inbox_threshold": config.inbox_threshold,
                "search_window_hours": config.search_window_hours,
                "contradiction_similarity_prefilter": config.contradiction_similarity_prefilter,
                "curate_interval_hours": 1000.0,
            },
            "lm_studio_base_url": config.lm_studio_base_url,
        },
        "scores": scores,
        "baselines": baselines,
        "retrieval": retrieval,
        "efficiency": {
            "total_memorization_seconds": round(total_memorization_seconds, 1),
            "total_query_seconds": round(total_query_seconds, 1),
            "avg_query_seconds": round(
                total_query_seconds / len(all_question_results), 2
            )
            if all_question_results
            else 0.0,
            "total_blocks_learned": total_blocks_learned,
            "total_blocks_active": total_blocks_active,
        },
        "questions": all_question_results,
    }
