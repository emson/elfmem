"""Layer 2 — minimal end-to-end harness.

Phase 1 scope: one simulated "day" of activity (10 learns + 1 dream + 5 recalls)
against an in-memory MemorySystem with the topic-aware mocks. Prints a status
line so we know the substrate works. Time-compression to month/year scale is
Phase 2.

Run:  uv run python -m scripts.longitudinal_sim.harness

Verifies the safety guarantees end-to-end:
- Engine is in-memory (`assert_in_memory_only`)
- No env vars leak the real DB (`assert_no_elfmem_env`)
- Never calls `MemorySystem.from_config()`
"""
from __future__ import annotations

import asyncio
import random

from elfmem.api import MemorySystem
from elfmem.config import ElfmemConfig, MemoryConfig
from elfmem.db.engine import create_test_engine
from elfmem.db.queries import seed_builtin_data

from .mocks import TopicAlignmentLLM, TopicEmbeddingService
from .safety import assert_in_memory_only, assert_no_elfmem_env
from .topic import perturb, random_topic, topic_to_content


async def run_one_day(*, seed: int = 42, n_blocks: int = 10, n_queries: int = 5) -> dict:
    """Stand up an in-memory MemorySystem, run learn/dream/recall, return stats."""
    assert_no_elfmem_env()

    rng = random.Random(seed)
    self_topic = random_topic(rng)

    engine = await create_test_engine()
    assert_in_memory_only(engine)
    async with engine.begin() as conn:
        await seed_builtin_data(conn)

    config = ElfmemConfig(memory=MemoryConfig(inbox_threshold=3))
    llm = TopicAlignmentLLM(self_topic=self_topic)
    embedding = TopicEmbeddingService(dimensions=64)

    system = MemorySystem(
        engine=engine, llm_service=llm, embedding_service=embedding, config=config,
    )

    # 1) Learn N blocks, each a perturbation of self with varied alignment.
    learn_ids: list[str] = []
    for i in range(n_blocks):
        sigma = 0.3 + 0.7 * rng.random()  # spread alignment across [0.3, 1.0] range
        topic = perturb(self_topic, rng, sigma=sigma)
        result = await system.learn(topic_to_content(topic, i))
        # learn() returns LearnResult with .id (the block_id of the stored block)
        learn_ids.append(getattr(result, "id", "") or getattr(result, "block_id", ""))

    # 2) Dream once to consolidate.
    consolidate_result = await system.dream()

    # 3) Recall against N queries, each near a known learned topic.
    hits = 0
    for i in range(n_queries):
        query_topic = perturb(self_topic, rng, sigma=0.2)
        scored = await system.recall(
            topic_to_content(query_topic, 10_000 + i), frame="attention", top_k=3,
        )
        block_ids = [b.id for b in scored]
        # Hit = at least one of our learned blocks surfaced (vs constitutional bedrock).
        if any(bid in learn_ids for bid in block_ids):
            hits += 1

    status = await system.status()
    await engine.dispose()

    return {
        "self_topic_dim": len(self_topic.values),
        "n_blocks_learned": n_blocks,
        "n_queries": n_queries,
        "hits": hits,
        "consolidate": consolidate_result.to_dict() if hasattr(consolidate_result, "to_dict") else str(consolidate_result),
        "status_inbox": status.inbox_count,
        "status_active": getattr(status, "active_count", "?"),
        "embed_calls": embedding.embed_calls,
        "llm_process_calls": llm.process_block_calls,
    }


def main() -> None:
    print("═" * 72)
    print(" Longitudinal harness — Layer 2 scaffold (one simulated day)")
    print("═" * 72)
    print(" Safety: in-memory engine only; no env-var DB pointers permitted.")
    print()
    result = asyncio.run(run_one_day())
    for k, v in result.items():
        print(f"  {k:<24} {v}")
    print()
    print("  ✓ One simulated day completed against in-memory MemorySystem.")
    print("  ✓ No production DB touched (safety asserts passed).")
    print()
    print("  Next: time-compression machinery (Phase 2) to run year-long")
    print("  experiments. See docs/plans/plan_longitudinal_evaluation.md.")


if __name__ == "__main__":
    main()
