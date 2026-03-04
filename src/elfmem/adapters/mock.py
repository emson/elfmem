"""Deterministic mock implementations of LLMService and EmbeddingService for testing."""

from __future__ import annotations

import hashlib

import numpy as np


class MockLLMService:
    """Deterministic mock LLM service for testing.

    All methods are async to match the LLMService protocol.
    Returns configurable scores and tags without making any API calls.

    Args:
        default_alignment: Default self-alignment score (0.0–1.0).
        alignment_overrides: Substring → score mapping; first match wins.
        default_tags: Default tags returned by infer_self_tags.
        tag_overrides: Substring → tags mapping; first match wins.
        default_contradiction: Default contradiction score (0.0–1.0).
        contradiction_overrides: (sub_a, sub_b) → score; matches when
            sub_a in block_a AND sub_b in block_b.
    """

    def __init__(
        self,
        *,
        default_alignment: float = 0.5,
        alignment_overrides: dict[str, float] | None = None,
        default_tags: list[str] | None = None,
        tag_overrides: dict[str, list[str]] | None = None,
        default_contradiction: float = 0.1,
        contradiction_overrides: dict[tuple[str, str], float] | None = None,
    ) -> None:
        self._default_alignment = default_alignment
        self._alignment_overrides = alignment_overrides or {}
        self._default_tags = default_tags or []
        self._tag_overrides = tag_overrides or {}
        self._default_contradiction = default_contradiction
        self._contradiction_overrides = contradiction_overrides or {}
        self.alignment_calls: int = 0
        self.tag_calls: int = 0
        self.contradiction_calls: int = 0

    async def score_self_alignment(self, block: str, self_context: str) -> float:
        """Return alignment score. Checks overrides first, then default."""
        self.alignment_calls += 1
        block_lower = block.lower()
        for substring, score in self._alignment_overrides.items():
            if substring.lower() in block_lower:
                return score
        return self._default_alignment

    async def infer_self_tags(self, block: str, self_context: str) -> list[str]:
        """Return inferred tags. Checks overrides first, then default."""
        self.tag_calls += 1
        block_lower = block.lower()
        for substring, tags in self._tag_overrides.items():
            if substring.lower() in block_lower:
                return tags
        return list(self._default_tags)

    async def detect_contradiction(self, block_a: str, block_b: str) -> float:
        """Return contradiction score. Checks overrides first, then default."""
        self.contradiction_calls += 1
        for (sub_a, sub_b), score in self._contradiction_overrides.items():
            if sub_a in block_a and sub_b in block_b:
                return score
        return self._default_contradiction


class MockEmbeddingService:
    """Deterministic mock embedding service for testing.

    Generates reproducible embeddings from content hashes. Same input always
    produces the same vector. Supports similarity overrides for controlled
    cosine similarity between specific content pairs.

    Args:
        dimensions: Embedding vector dimensionality. Default: 64.
        similarity_overrides: frozenset({a, b}) → target cosine similarity.
            When both texts have been embedded, the second vector is adjusted
            to achieve the target similarity with the first.
    """

    def __init__(
        self,
        *,
        dimensions: int = 64,
        similarity_overrides: dict[frozenset[str], float] | None = None,
    ) -> None:
        self._dimensions = dimensions
        self._similarity_overrides = similarity_overrides or {}
        self._cache: dict[str, np.ndarray] = {}
        self.embed_calls: int = 0

    def _hash_vector(self, text: str) -> np.ndarray:
        # Use uint8 → float32 conversion to avoid NaN/Inf from raw float32 bytes.
        # Each dimension gets one byte of the extended hash, mapped to [-1, 1].
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        needed_bytes = self._dimensions
        extended = digest * (needed_bytes // len(digest) + 1)
        raw_u8 = np.frombuffer(bytes(extended[:needed_bytes]), dtype=np.uint8).copy()
        raw = (raw_u8.astype(np.float32) / 127.5) - 1.0
        norm = float(np.linalg.norm(raw))
        if norm > 0:
            raw = raw / norm
        return raw

    def _vector_with_similarity(
        self, text: str, anchor: np.ndarray, target_sim: float
    ) -> np.ndarray:
        raw = self._hash_vector(text)
        projection = float(np.dot(raw, anchor))
        ortho = raw - projection * anchor
        ortho_norm = float(np.linalg.norm(ortho))
        if ortho_norm < 1e-7:
            raw2 = self._hash_vector(text + "_ortho_fallback")
            projection2 = float(np.dot(raw2, anchor))
            ortho = raw2 - projection2 * anchor
            ortho_norm = float(np.linalg.norm(ortho))
        if ortho_norm < 1e-7:
            return anchor.copy()
        ortho_unit = (ortho / ortho_norm).astype(np.float32)
        scale = float(np.sqrt(max(0.0, 1.0 - target_sim ** 2)))
        return (target_sim * anchor + scale * ortho_unit).astype(np.float32)

    def _find_override(self, text: str) -> np.ndarray | None:
        for pair, target_sim in self._similarity_overrides.items():
            if text not in pair:
                continue
            other = next(t for t in pair if t != text)
            if other in self._cache:
                return self._vector_with_similarity(text, self._cache[other], target_sim)
        return None

    async def embed(self, text: str) -> np.ndarray:
        """Return a deterministic normalised float32 embedding vector.

        Same text always produces the same vector. If similarity_overrides
        are configured, adjusts the vector to achieve the target cosine
        similarity with previously embedded texts.
        """
        self.embed_calls += 1
        if text in self._cache:
            return self._cache[text]
        override_vec = self._find_override(text)
        vec = override_vec if override_vec is not None else self._hash_vector(text)
        self._cache[text] = vec
        return vec


def make_mock_llm(**kwargs: object) -> MockLLMService:
    """Create a MockLLMService with optional overrides.

    Examples:
        make_mock_llm()
        make_mock_llm(default_alignment=0.9)
        make_mock_llm(alignment_overrides={"identity": 0.95})
    """
    return MockLLMService(**kwargs)  # type: ignore[arg-type]


def make_mock_embedding(**kwargs: object) -> MockEmbeddingService:
    """Create a MockEmbeddingService with optional overrides.

    Examples:
        make_mock_embedding()
        make_mock_embedding(dimensions=128)
    """
    return MockEmbeddingService(**kwargs)  # type: ignore[arg-type]
