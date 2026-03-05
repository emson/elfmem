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
