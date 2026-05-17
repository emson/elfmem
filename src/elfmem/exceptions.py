"""elfmem exceptions — agent-friendly errors with recovery hints.

All elfmem errors carry a `recovery` field: a concrete instruction the agent
can act on immediately. Check `error.recovery` rather than parsing the message.

Hierarchy:
    ElfmemError          base; always has .recovery
      SessionError       no active session when one is required
      ConfigError        invalid or missing configuration
      StorageError       unrecoverable database failure
      FrameError         unknown frame name requested
"""

from __future__ import annotations


class ElfmemError(Exception):
    """Base exception for all elfmem errors.

    Every elfmem exception carries a ``recovery`` attribute — a complete,
    actionable instruction for resolving the problem.

    Example::

        try:
            result = await system.frame("unknown")
        except ElfmemError as e:
            print(e.recovery)  # "Valid frames: 'self', 'attention', 'task'."
    """

    def __init__(self, message: str, *, recovery: str) -> None:
        super().__init__(message)
        self.recovery = recovery

    def __str__(self) -> str:
        return f"{super().__str__()} — Recovery: {self.recovery}"


class SessionError(ElfmemError):
    """Raised when a session-required operation is called without an active session.

    Recovery hint always explains how to start a session.
    """


class ConfigError(ElfmemError):
    """Raised when configuration is invalid or references missing resources."""


class StorageError(ElfmemError):
    """Raised when a database operation fails in an unrecoverable way."""


class FrameError(ElfmemError):
    """Raised when an unknown frame name is passed to frame() or recall()."""


class PeerError(ElfmemError):
    """Raised when a peer operation cannot complete."""


class ProjectNotFound(ElfmemError):
    """Raised when an operation requires a project root but none was found.

    Project root = the directory containing ``.elfmem/config.yaml`` (or another
    project marker). Peer messaging is project-scoped, so peer operations fail
    fast with this error when invoked outside any project and no explicit
    override is configured.
    """

    def __init__(self, what: str = "this operation") -> None:
        super().__init__(
            f"No project root found for {what}.",
            recovery=(
                "Run 'elfmem setup' (or 'elfmem init') in your project directory, "
                "or invoke from a directory inside an existing elfmem project."
            ),
        )


class ConnectError(ElfmemError):
    """Raised when a connect() or disconnect() operation cannot complete."""


class SelfLoopError(ConnectError):
    """source and target are the same block ID."""

    def __init__(self, block_id: str) -> None:
        super().__init__(
            f"Cannot connect block '{block_id[:8]}' to itself.",
            recovery="source and target must be different block IDs.",
        )


class BlockNotActiveError(ConnectError):
    """A block ID was not found in active memory."""

    def __init__(self, block_id: str) -> None:
        super().__init__(
            f"Block '{block_id[:8]}…' not found in active memory.",
            recovery=(
                "Use system.recall() to find active block IDs. "
                "If the block was archived, re-learn its content to reactivate."
            ),
        )


class DegreeLimitError(ConnectError):
    """All existing edges for a block are protected; new edge cannot be placed."""

    def __init__(self, block_id: str, cap: int) -> None:
        super().__init__(
            f"Block '{block_id[:8]}…' has {cap} protected edges; no auto-edges to displace.",
            recovery=(
                "Run system.curate() to prune stale edges, or "
                "increase edge_degree_cap in config, or "
                "call system.disconnect() to manually remove an edge."
            ),
        )


class EmbeddingLockError(ElfmemError):
    """Raised when the configured embedding model disagrees with the DB lock.

    Cosines between vectors from different embedding models are noise — the
    lock catches the swap before silent corruption spreads. Recovery either
    aligns the config with the DB, or runs ``elfmem migrate-embeddings``
    to re-embed everything under the new model.
    """
