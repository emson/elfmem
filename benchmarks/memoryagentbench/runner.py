"""MemoryAgentBench benchmark runner — CLI entry point.

Usage: python -m benchmarks.memoryagentbench.runner [flags]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import time
from datetime import UTC, datetime
from pathlib import Path

from datasets import load_dataset

from benchmarks.memoryagentbench.adapter import process_example
from benchmarks.memoryagentbench.config import MABenchConfig
from benchmarks.memoryagentbench.metrics import score_question
from benchmarks.shared.answerer import generate_answer

if not os.environ.get("OPENAI_API_KEY"):
    os.environ["OPENAI_API_KEY"] = "lm-studio"

log = logging.getLogger(__name__)

COMPETENCY_ORDER = [
    "Conflict_Resolution",
    "Accurate_Retrieval",
    "Test_Time_Learning",
    "Long_Range_Understanding",
]


# ── Resume helpers ───────────────────────────────────────────────────────────


def _example_id(source: str, index: int) -> str:
    """Composite ID for an example: 'source/index'."""
    return f"{source}/{index}"


def _get_completed_example_ids(output_path: Path) -> set[str]:
    """Load set of example IDs already written to output JSON."""
    if not output_path.exists():
        return set()
    data = json.loads(output_path.read_text())
    return {q["example_id"] for q in data.get("questions", []) if "example_id" in q}


def _append_example_results(
    output_path: Path, new_questions: list[dict],
) -> None:
    """Atomically append question results to the output JSON.

    Uses tmpfile + rename for crash safety on POSIX filesystems.
    """
    existing = json.loads(output_path.read_text()) if output_path.exists() else {"questions": []}
    existing["questions"].extend(new_questions)
    tmp = output_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(existing, indent=2, default=str))
    tmp.rename(output_path)


# ── Scoring ──────────────────────────────────────────────────────────────────


async def _answer_and_score(
    qa, config: MABenchConfig,
) -> dict:
    """Generate answer and score against ground truths."""
    prediction = await generate_answer(
        context=qa.retrieved_context,
        question=qa.question,
        category=0,  # standard QA (not adversarial)
        model=config.answer_model,
        max_tokens=config.answer_max_tokens,
        base_url=config.lm_studio_base_url,
    )
    scores = score_question(prediction, qa.ground_truths)
    return {
        "question": qa.question,
        "ground_truths": qa.ground_truths,
        "prediction": prediction,
        "query_seconds": qa.query_seconds,
        **scores,
    }


# ── Main runner ──────────────────────────────────────────────────────────────


async def run(args: argparse.Namespace) -> None:
    """Run the benchmark."""
    config = MABenchConfig()

    if args.test:
        config.max_examples = 1
        config.competencies = ["Conflict_Resolution"]
    if args.competency:
        config.competencies = [args.competency]
    if args.max_examples:
        config.max_examples = args.max_examples
    if args.top_k:
        config.top_k = args.top_k
    if args.resume:
        config.resume = True

    config.output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output_path = args.output or config.output_dir / f"{timestamp}_mabench_elfmem.json"
    output_path = Path(output_path)

    # Resume: load previously completed examples
    completed_ids: set[str] = set()
    if config.resume:
        completed_ids = _get_completed_example_ids(output_path)
        if completed_ids:
            log.info(f"Resuming: {len(completed_ids)} example(s) already completed")

    log.info("Loading MemoryAgentBench dataset from HuggingFace...")
    ds = load_dataset("ai-hyz/MemoryAgentBench")

    competencies = config.competencies or COMPETENCY_ORDER
    start_time = time.monotonic()
    all_results: list[dict] = []
    competency_scores: dict[str, list[float]] = {}

    for competency in competencies:
        if competency not in ds:
            log.warning(f"Competency '{competency}' not in dataset, skipping")
            continue

        split = ds[competency]
        examples = list(split)
        if config.max_examples:
            examples = examples[:config.max_examples]

        log.info(f"\n{'='*60}")
        log.info(f"Competency: {competency} ({len(examples)} examples)")
        log.info(f"{'='*60}")

        comp_f1s: list[float] = []

        for ex_idx, example in enumerate(examples):
            metadata = example.get("metadata", {})
            source = metadata.get("source", "unknown") if isinstance(metadata, dict) else "unknown"
            ex_id = _example_id(source, ex_idx)
            n_questions = len(example["questions"])

            # Resume: skip completed examples
            if ex_id in completed_ids:
                log.info(f"  [{ex_idx+1}/{len(examples)}] {source}: skipped (resumed)")
                continue

            log.info(f"\n  [{ex_idx+1}/{len(examples)}] {source}: {n_questions} questions")

            try:
                ex_result = await process_example(example, competency, config)

                example_questions: list[dict] = []
                for qa in ex_result.qa_results:
                    scored = await _answer_and_score(qa, config)
                    scored["competency"] = competency
                    scored["source"] = source
                    scored["example_id"] = ex_id
                    example_questions.append(scored)
                    comp_f1s.append(scored["f1"])

                all_results.extend(example_questions)

                # Atomic write after each example — crash-safe resume
                _append_example_results(output_path, example_questions)

                avg_f1 = sum(comp_f1s[-n_questions:]) / n_questions if n_questions else 0
                log.info(f"    {n_questions} Qs answered | avg F1={avg_f1:.3f}")

            except Exception as e:
                log.error(f"    ERROR: {e}")

        competency_scores[competency] = comp_f1s

    # Build final report (overwrites with complete metadata)
    duration = time.monotonic() - start_time
    scores_by_comp: dict[str, dict] = {}
    all_f1s: list[float] = []

    for comp, f1s in competency_scores.items():
        avg = sum(f1s) / len(f1s) if f1s else 0.0
        scores_by_comp[comp] = {"score": round(avg * 100, 1), "count": len(f1s)}
        all_f1s.extend(f1s)

    overall = sum(all_f1s) / len(all_f1s) if all_f1s else 0.0

    try:
        from elfmem import __version__ as elfmem_version
    except ImportError:
        elfmem_version = "unknown"

    # Merge any previously completed results with this run's results
    if output_path.exists():
        existing = json.loads(output_path.read_text())
        all_questions = existing.get("questions", [])
    else:
        all_questions = all_results

    report = {
        "meta": {
            "benchmark": "memoryagentbench",
            "version": "1.0",
            "timestamp": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "duration_seconds": round(duration, 1),
            "elfmem_version": elfmem_version,
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
                "chunk_size": config.chunk_size,
                "consolidate_every_n_chunks": config.consolidate_every_n_chunks,
            },
            "lm_studio_base_url": config.lm_studio_base_url,
        },
        "scores": {
            "overall": round(overall * 100, 1),
            "by_competency": scores_by_comp,
        },
        "questions": all_questions,
    }

    output_path.write_text(json.dumps(report, indent=2, default=str))
    log.info(f"\nReport saved to {output_path}")
    log.info(f"Overall F1: {overall * 100:.1f}%")
    for comp, info in scores_by_comp.items():
        log.info(f"  {comp}: {info['score']}% (n={info['count']})")


def main() -> None:
    # force=True: datasets library sets up root-logger handlers during import,
    # making a plain basicConfig() call a silent no-op. We must override it.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        force=True,
    )

    parser = argparse.ArgumentParser(description="Run MemoryAgentBench against elfmem")
    parser.add_argument("--test", action="store_true", help="Smoke test: 1 CR example only")
    parser.add_argument("--competency", choices=COMPETENCY_ORDER, help="Run one competency")
    parser.add_argument("--max-examples", type=int, help="Limit examples per competency")
    parser.add_argument("--top-k", type=int, help="Override retrieval top_k")
    parser.add_argument("--resume", action="store_true", help="Skip completed examples")
    parser.add_argument("--output", type=str, help="Custom output path")
    args = parser.parse_args()

    asyncio.run(run(args))


if __name__ == "__main__":
    main()
