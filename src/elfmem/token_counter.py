"""Mutable token accumulator — internal use only.

``TokenCounter`` is owned by ``MemorySystem`` and shared (by reference) with
the LLM and embedding adapters. The adapters call ``record_llm`` /
``record_embedding`` after each API call. ``MemorySystem`` calls ``snapshot()``
to read the current session total or ``reset()`` to capture-and-zero at
session end.

``TokenUsage`` (the immutable snapshot type) lives in ``types.py`` because it
is part of the public API surface (exported from ``__init__.py``).
"""

from __future__ import annotations

from elfmem.types import TokenUsage


class TokenCounter:
    """Mutable in-process accumulator for LLM and embedding token usage.

    Thread safety: Python's GIL protects simple integer increments, so
    concurrent async coroutines sharing one counter are safe without locks.

    Owned by ``MemorySystem``; injected into both adapters at construction.
    Mock adapters receive no counter (counter stays ``None`` in those paths —
    all counts appear as zero in ``status()``).
    """

    def __init__(self) -> None:
        self._llm_input: int = 0
        self._llm_output: int = 0
        self._embedding: int = 0
        self._llm_calls: int = 0
        self._embedding_calls: int = 0

    def record_llm(self, input_tokens: int, output_tokens: int) -> None:
        """Record one completed LLM call."""
        self._llm_input += input_tokens
        self._llm_output += output_tokens
        self._llm_calls += 1

    def record_embedding(self, tokens: int) -> None:
        """Record one completed embedding call."""
        self._embedding += tokens
        self._embedding_calls += 1

    def snapshot(self) -> TokenUsage:
        """Return a frozen snapshot without resetting the counter."""
        return TokenUsage(
            llm_input_tokens=self._llm_input,
            llm_output_tokens=self._llm_output,
            embedding_tokens=self._embedding,
            llm_calls=self._llm_calls,
            embedding_calls=self._embedding_calls,
        )

    def reset(self) -> TokenUsage:
        """Return a frozen snapshot and zero all counters.

        Called by ``MemorySystem.begin_session()`` (start fresh) and
        ``MemorySystem.end_session()`` (capture for lifetime persistence).
        """
        usage = self.snapshot()
        self._llm_input = 0
        self._llm_output = 0
        self._embedding = 0
        self._llm_calls = 0
        self._embedding_calls = 0
        return usage
