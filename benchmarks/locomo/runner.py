"""LoCoMo benchmark runner — CLI entry point.

Usage: python -m benchmarks.locomo.runner [flags]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

# LM Studio doesn't require a real API key — set a dummy if not already set
# so the OpenAI client doesn't raise on initialisation.
if not os.environ.get("OPENAI_API_KEY"):
    os.environ["OPENAI_API_KEY"] = "lm-studio"

from benchmarks.locomo.adapter import QAResult, process_conversation
from benchmarks.locomo.baselines import (
    run_no_retrieval_baseline,
    run_perfect_retrieval_baseline,
)
from benchmarks.locomo.config import LoCoMoConfig
from benchmarks.locomo.data import Conversation, load_locomo
from benchmarks.locomo.metrics import score_qa
from benchmarks.locomo.report import CATEGORY_NAMES, build_report
from benchmarks.shared.answerer import generate_answer

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent.parent
DATA_DEFAULT = PROJECT_ROOT.parent / "locomo" / "data" / "locomo10.json"


# ── CLI parsing ──────────────────────────────────────────────────────────────


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments for the LoCoMo benchmark runner."""
    parser = argparse.ArgumentParser(
        description="Run LoCoMo benchmark against elfmem",
    )
    parser.add_argument(
        "--test", action="store_true",
        help="Smoke test: 1 conversation, 5 questions",
    )
    parser.add_argument(
        "--category", type=int, choices=[1, 2, 3, 4, 5],
        help="Only run questions from category N (1-5)",
    )
    parser.add_argument(
        "--max-conv", type=int,
        help="Limit to N conversations",
    )
    parser.add_argument(
        "--baselines", action="store_true",
        help="Run no-retrieval + perfect-retrieval baselines",
    )
    parser.add_argument(
        "--resume", action="store_true",
        help="Skip conversations with existing results",
    )
    parser.add_argument(
        "--top-k", type=int, default=10,
        help="Override retrieval top_k (default: 10)",
    )
    parser.add_argument(
        "--output", type=str,
        help="Custom output file path",
    )
    return parser.parse_args()


# ── Config construction ──────────────────────────────────────────────────────


def build_config_from_args(args: argparse.Namespace) -> LoCoMoConfig:
    """Build LoCoMoConfig from parsed CLI arguments."""
    config = LoCoMoConfig(
        top_k=args.top_k,
        top_k_retrieval=args.top_k,
        include_baselines=args.baselines,
        resume=args.resume,
    )
    if args.category is not None:
        config.categories = [args.category]
    if args.max_conv is not None:
        config.max_conversations = args.max_conv
    if args.test:
        config.max_conversations = 1
        config.max_qa_per_conversation = 5
    return config


# ── Output helpers ───────────────────────────────────────────────────────────


def resolve_output_path(args: argparse.Namespace, config: LoCoMoConfig) -> Path:
    """Determine the output file path from CLI args or generate a timestamped default."""
    if args.output:
        return Path(args.output)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_dir = PROJECT_ROOT / config.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir / f"{timestamp}_locomo_elfmem.json"


def init_partial_output(output_path: Path, config: LoCoMoConfig) -> None:
    """Write initial partial output file for crash safety."""
    partial = {
        "meta": {
            "benchmark": "locomo",
            "version": "1.0",
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "status": "in_progress",
        },
        "questions": [],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(partial, indent=2))


def append_questions(output_path: Path, new_questions: list[dict]) -> None:
    """Append question results to the partial output file (crash-safe)."""
    if not new_questions:
        return
    data = json.loads(output_path.read_text())
    data["questions"].extend(new_questions)
    output_path.write_text(json.dumps(data, indent=2))


def get_completed_question_ids(output_path: Path) -> set[str]:
    """Load already-completed question IDs from partial output for --resume."""
    if not output_path.exists():
        return set()
    data = json.loads(output_path.read_text())
    return {q["id"] for q in data.get("questions", [])}


# ── Scoring helpers ──────────────────────────────────────────────────────────


def _category_name(cat: int) -> str:
    return CATEGORY_NAMES.get(cat, f"cat-{cat}")


async def _answer_and_score_qa(
    qa: QAResult,
    question_id: str,
    config: LoCoMoConfig,
) -> dict:
    """Generate an answer for a QAResult and score it against ground truth.

    Returns a report-ready question dict matching benchmark_report_spec.md.
    """
    prediction = await generate_answer(
        context=qa.frame_context,
        question=qa.question,
        category=qa.category,
        adversarial_answer=qa.adversarial_answer or "",
        model=config.answer_model,
        max_tokens=config.answer_max_tokens,
        base_url=config.lm_studio_base_url,
    )

    scores = score_qa(
        prediction=prediction,
        category=qa.category,
        ground_truth=qa.ground_truth,
        retrieved_ids=qa.retrieved_dia_ids,
        evidence_ids=qa.evidence_ids,
    )

    metric = "adversarial" if qa.category == 5 else "f1"

    return {
        "id": question_id,
        "category": _category_name(qa.category),
        "question": qa.question,
        "ground_truth": qa.ground_truth or "",
        "prediction": prediction,
        "score": round(scores.get(metric, 0.0), 4),
        "metric": metric,
        "retrieval_recall": round(scores.get("retrieval_recall", 0.0), 4),
        "evidence_ids": qa.evidence_ids,
        "retrieved_ids": qa.retrieved_dia_ids,
        "query_seconds": round(qa.query_seconds, 2),
    }


def _score_baseline_question(result: dict) -> dict:
    """Add score and metric fields to a baseline result dict."""
    cat_name = result["category"]
    cat_int = next((k for k, v in CATEGORY_NAMES.items() if v == cat_name), 0)

    scores = score_qa(
        prediction=result["prediction"],
        category=cat_int,
        ground_truth=result.get("ground_truth"),
        evidence_ids=result.get("evidence_ids"),
    )

    metric = "adversarial" if cat_int == 5 else "f1"
    result["score"] = round(scores.get(metric, 0.0), 4)
    result["metric"] = metric
    return result


# ── Resume logic ─────────────────────────────────────────────────────────────


def _conversation_complete(
    sample_id: str,
    n_qa: int,
    completed_ids: set[str],
) -> bool:
    """Check if all QA pairs for a conversation are already in the output."""
    expected = {f"{sample_id}_q{i}" for i in range(n_qa)}
    return expected.issubset(completed_ids)


# ── Baselines ────────────────────────────────────────────────────────────────


async def _run_baselines(
    conversations: list[Conversation],
    config: LoCoMoConfig,
) -> dict[str, list[dict]]:
    """Run no-retrieval and perfect-retrieval baselines across all conversations."""
    baseline_results: dict[str, list[dict]] = {}

    # Collect filtered QA pairs per conversation
    all_qa_pairs = []
    for conv in conversations:
        qa_pairs = conv.qa_pairs
        if config.categories:
            qa_pairs = [q for q in qa_pairs if q.category in config.categories]
        if config.max_qa_per_conversation:
            qa_pairs = qa_pairs[: config.max_qa_per_conversation]
        all_qa_pairs.extend(qa_pairs)

    # No-retrieval baseline
    log.info("  No-retrieval baseline (%d questions)...", len(all_qa_pairs))
    no_ret = await run_no_retrieval_baseline(all_qa_pairs, config)
    baseline_results["no_retrieval"] = [_score_baseline_question(q) for q in no_ret]

    # Perfect-retrieval baseline
    log.info("  Perfect-retrieval baseline (%d questions)...", len(all_qa_pairs))
    perfect_ret_all: list[dict] = []
    for conv in conversations:
        qa_pairs = conv.qa_pairs
        if config.categories:
            qa_pairs = [q for q in qa_pairs if q.category in config.categories]
        if config.max_qa_per_conversation:
            qa_pairs = qa_pairs[: config.max_qa_per_conversation]
        results = await run_perfect_retrieval_baseline(conv, qa_pairs, config)
        perfect_ret_all.extend(results)
    baseline_results["perfect_retrieval"] = [
        _score_baseline_question(q) for q in perfect_ret_all
    ]

    return baseline_results


# ── Main loop ────────────────────────────────────────────────────────────────


async def run(args: argparse.Namespace) -> Path:
    """Execute the full benchmark run and return the output path."""
    config = build_config_from_args(args)
    output_path = resolve_output_path(args, config)
    start_time = time.monotonic()

    # Load dataset
    data_path = PROJECT_ROOT / config.data_file
    if not data_path.exists():
        data_path = DATA_DEFAULT
    conversations = load_locomo(data_path)
    log.info("Loaded %d conversations from %s", len(conversations), data_path)

    # Apply conversation limit
    if config.max_conversations is not None:
        conversations = conversations[: config.max_conversations]

    # Resume support
    completed_ids: set[str] = set()
    if config.resume and output_path.exists():
        completed_ids = get_completed_question_ids(output_path)
        log.info("Resume: %d questions already completed", len(completed_ids))
    else:
        init_partial_output(output_path, config)

    # Load existing results when resuming
    all_question_results: list[dict] = []
    if completed_ids:
        data = json.loads(output_path.read_text())
        all_question_results = data.get("questions", [])

    # Efficiency tracking
    total_blocks_learned = 0
    total_memorization_seconds = 0.0

    # Process each conversation
    total = len(conversations)
    for conv_idx, conv in enumerate(conversations):
        n_qa = len(conv.qa_pairs)

        if completed_ids and _conversation_complete(conv.sample_id, n_qa, completed_ids):
            log.info("[%d/%d] %s: skipped (resume)", conv_idx + 1, total, conv.sample_id)
            continue

        log.info("[%d/%d] %s: %d QA pairs", conv_idx + 1, total, conv.sample_id, n_qa)

        # Ingest + retrieve via elfmem
        mem_start = time.monotonic()
        conv_result = await process_conversation(conv, config)
        mem_elapsed = time.monotonic() - mem_start
        total_memorization_seconds += mem_elapsed
        total_blocks_learned += conv_result.blocks_consolidated

        log.info(
            "  Ingested: %d sessions, %d turns",
            conv_result.sessions_ingested,
            conv_result.turns_ingested,
        )

        # Answer + score each question
        conv_questions: list[dict] = []
        for qa_idx, qa in enumerate(conv_result.qa_results):
            question_id = f"{conv.sample_id}_q{qa_idx}"
            if question_id in completed_ids:
                continue
            q_result = await _answer_and_score_qa(qa, question_id, config)
            conv_questions.append(q_result)

        all_question_results.extend(conv_questions)

        # Crash-safe: write after each conversation
        append_questions(output_path, conv_questions)

        if conv_questions:
            scores = [q["score"] for q in conv_questions]
            avg = sum(scores) / len(scores)
            log.info(
                "  %.1fs | %d Qs answered | avg score=%.3f",
                mem_elapsed, len(conv_questions), avg,
            )

    # Baselines (optional)
    baseline_results: dict[str, list[dict]] | None = None
    if config.include_baselines:
        log.info("Running baselines...")
        baseline_results = await _run_baselines(conversations, config)

    # Build final report
    report = build_report(
        all_question_results=all_question_results,
        baseline_results=baseline_results,
        config=config,
        start_time=start_time,
        total_blocks_learned=total_blocks_learned,
        total_memorization_seconds=total_memorization_seconds,
    )

    # Overwrite partial output with complete report
    output_path.write_text(json.dumps(report, indent=2))
    log.info("Report saved to %s", output_path)
    log.info("Overall score: %.1f%%", report["scores"]["overall"])

    return output_path


def main() -> None:
    """CLI entry point."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    args = parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
