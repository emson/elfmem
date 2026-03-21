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
    BlockNotActiveError,
    ConfigError,
    ConnectError,
    DegreeLimitError,
    ElfmemError,
    FrameError,
    SelfLoopError,
    SessionError,
    StorageError,
)
from elfmem.policy import ConsolidationPolicy
from elfmem.types import (
    ConnectByQueryResult,
    ConnectResult,
    ConnectSpec,
    ConnectsResult,
    ConsolidateResult,
    CurateResult,
    DisconnectResult,
    DisplacedEdge,
    FrameResult,
    LearnResult,
    OperationRecord,
    OutcomeResult,
    ScoredBlock,
    SetupResult,
    SystemStatus,
    TokenUsage,
)

__all__ = [
    # Core
    "MemorySystem",
    "ElfmemConfig",
    "ConsolidationPolicy",
    # Result types
    "LearnResult",
    "ConsolidateResult",
    "FrameResult",
    "CurateResult",
    "OutcomeResult",
    "SetupResult",
    "ScoredBlock",
    "SystemStatus",
    "OperationRecord",
    "TokenUsage",
    "ConnectResult",
    "ConnectByQueryResult",
    "ConnectsResult",
    "ConnectSpec",
    "DisconnectResult",
    "DisplacedEdge",
    # Exceptions
    "ElfmemError",
    "SessionError",
    "ConfigError",
    "StorageError",
    "FrameError",
    "ConnectError",
    "SelfLoopError",
    "BlockNotActiveError",
    "DegreeLimitError",
]
