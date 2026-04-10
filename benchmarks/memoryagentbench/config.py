"""Configuration for the MemoryAgentBench benchmark harness."""

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class MABenchConfig:
    """All tuneable parameters for a MemoryAgentBench benchmark run.

    USE WHEN: Configuring a benchmark run.
    DON'T USE WHEN: You need runtime state — this is pure config.
    COST: Zero (dataclass).
    RETURNS: Frozen configuration object.
    NEXT: Pass to the runner.
    """

    output_dir: Path = Path("benchmarks/memoryagentbench/results")
    lm_studio_base_url: str = "http://localhost:1234/v1"

    # Which competencies to run (None = all)
    competencies: list[str] | None = None

    # Which sub-datasets to run within selected competencies (None = all)
    sources: list[str] | None = None

    # elfmem settings
    elfmem_llm_model: str = "google/gemma-4-26b-a4b"
    elfmem_embedding_model: str = "text-embedding-nomic-embed-text-v1.5"
    elfmem_embedding_dimensions: int = 768
    top_k: int = 10
    inbox_threshold: int = 50
    search_window_hours: float = 10000.0
    contradiction_similarity_prefilter: float = 0.50  # lower than LoCoMo — CR needs sensitivity
    chunk_size: int = 256  # words per chunk (~350 tokens, fits in 4096 context)
    consolidate_every_n_chunks: int = 10

    # Answer generation
    answer_model: str = "google/gemma-4-26b-a4b"
    answer_max_tokens: int = 100

    # Model context window — used to derive the answer-context budget.
    # Gemma 4 26B-A4B natively supports 256K; LM Studio defaults to 48K (49152).
    # Match this to what LM Studio shows in Model Settings → Context Length.
    # Common values: 49152 (LM Studio default), 131072, 262144 (full 256K).
    context_window_tokens: int = 49152

    # Execution
    max_examples: int | None = None
    resume: bool = False
