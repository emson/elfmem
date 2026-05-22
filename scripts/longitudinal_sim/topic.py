"""Ground-truth semantic space for the longitudinal simulator.

Blocks and queries are 8-d unit vectors in a topic space. The mock embedding
returns the topic vector directly, so cosine similarity in our space IS the
production cosine similarity. The mock LLM derives alignment_score from the
block's distance to the simulated SELF.

This gives us a controllable ground truth: we know which blocks "actually"
answer which queries; the system's hit rate can be measured against that.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass

DIM: int = 8


@dataclass(frozen=True)
class Topic:
    """8-d unit vector in topic space."""
    values: tuple[float, ...]

    def __post_init__(self) -> None:
        if len(self.values) != DIM:
            raise ValueError(f"Topic must be {DIM}-d; got {len(self.values)}")

    def cosine(self, other: Topic) -> float:
        return sum(a * b for a, b in zip(self.values, other.values))


def random_topic(rng: random.Random) -> Topic:
    raw = [rng.gauss(0.0, 1.0) for _ in range(DIM)]
    norm = math.sqrt(sum(x * x for x in raw))
    if norm == 0.0:
        return random_topic(rng)
    return Topic(tuple(x / norm for x in raw))


def perturb(topic: Topic, rng: random.Random, sigma: float) -> Topic:
    """Topic with Gaussian noise of width sigma, then re-normalised."""
    raw = [v + rng.gauss(0.0, sigma) for v in topic.values]
    norm = math.sqrt(sum(x * x for x in raw))
    return Topic(tuple(x / norm for x in raw))


def topic_to_content(topic: Topic, block_id: int) -> str:
    """Encode topic into block content, embedded as a tag-like marker.

    The mock embedding parses this tag back out so the embedding space matches
    the topic space exactly.
    """
    vec_str = ",".join(f"{v:+.4f}" for v in topic.values)
    return f"[TOPIC={vec_str}] block #{block_id}"


def content_to_topic(content: str) -> Topic | None:
    """Parse the [TOPIC=...] marker back into a Topic. Returns None if absent."""
    start = content.find("[TOPIC=")
    if start == -1:
        return None
    end = content.find("]", start)
    if end == -1:
        return None
    vec_str = content[start + 7 : end]
    parts = vec_str.split(",")
    if len(parts) != DIM:
        return None
    return Topic(tuple(float(p) for p in parts))
