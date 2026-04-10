"""Configuration for the LoCoMo benchmark harness."""

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class LoCoMoConfig:
    """All tuneable parameters for a LoCoMo benchmark run.

    USE WHEN: Configuring a benchmark run.
    DON'T USE WHEN: You need runtime state — this is pure config.
    COST: Zero (dataclass).
    RETURNS: Frozen configuration object.
    NEXT: Pass to the runner.
    """

    data_file: Path = Path("../locomo/data/locomo10.json")
    output_dir: Path = Path("benchmarks/locomo/results")
    lm_studio_base_url: str = "http://localhost:1234/v1"
    elfmem_llm_model: str = "google/gemma-4-26b-a4b"
    elfmem_embedding_model: str = "text-embedding-nomic-embed-text-v1.5"
    elfmem_embedding_dimensions: int = 768
    top_k: int = 10
    inbox_threshold: int = 50
    search_window_hours: float = 10000.0
    contradiction_similarity_prefilter: float = 0.65
    answer_model: str = "google/gemma-4-26b-a4b"
    answer_max_tokens: int = 100
    max_conversations: int | None = None
    max_qa_per_conversation: int | None = None
    categories: list[int] | None = None
    top_k_retrieval: int = 10
    include_baselines: bool = False
    resume: bool = False
