"""elfmem — Self-aware adaptive memory for LLM agents.

Quick start::

    from elfmem import MemorySystem

    system = await MemorySystem.from_config("agent.db")
    async with system.session():
        await system.learn("User prefers dark mode")
        ctx = await system.frame("attention", query="preferences")
        print(ctx.text)  # inject into your LLM prompt

Agent self-documentation::

    print(system.guide())           # overview of all operations
    print(system.guide("learn"))    # detailed guide for learn()
    s = await system.status()       # health snapshot + suggested action
"""

from elfmem.api import MemorySystem
from elfmem.config import ElfmemConfig
from elfmem.exceptions import (
    ConfigError,
    ElfmemError,
    FrameError,
    SessionError,
    StorageError,
)
from elfmem.types import (
    ConsolidateResult,
    CurateResult,
    FrameResult,
    LearnResult,
    OperationRecord,
    OutcomeResult,
    ScoredBlock,
    SystemStatus,
    TokenUsage,
)

__all__ = [
    # Core
    "MemorySystem",
    "ElfmemConfig",
    # Result types
    "LearnResult",
    "ConsolidateResult",
    "FrameResult",
    "CurateResult",
    "OutcomeResult",
    "ScoredBlock",
    "SystemStatus",
    "OperationRecord",
    "TokenUsage",
    # Exceptions
    "ElfmemError",
    "SessionError",
    "ConfigError",
    "StorageError",
    "FrameError",
]
