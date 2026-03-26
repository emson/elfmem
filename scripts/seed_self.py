#!/usr/bin/env python
"""Seed the SELF frame for elfmem MCP using CONSTITUTIONAL_SEED."""
import asyncio
import sys
from pathlib import Path

from elfmem.seed import CONSTITUTIONAL_SEED
from elfmem.api import MemorySystem


async def seed_self(db_path: str, config_path: str | None = None) -> None:
    """Seed SELF frame with constitutional blocks."""
    db_expanded = Path(db_path).expanduser()
    config_expanded = Path(config_path).expanduser() if config_path else None

    async with MemorySystem.managed(str(db_expanded), config=str(config_expanded)) as mem:
        print(f"Seeding SELF frame from {len(CONSTITUTIONAL_SEED)} constitutional blocks...\n")

        for i, block in enumerate(CONSTITUTIONAL_SEED, 1):
            result = await mem.remember(
                block["content"],  # type: ignore[arg-type]
                tags=block["tags"],  # type: ignore[arg-type]
            )
            status = result.status
            block_id = result.block_id[:8]
            print(f"  [{i:2d}] {status:25s} {block_id}  {block['content'][:60]}...")

        print("\n✓ SELF frame seeding complete!")


if __name__ == "__main__":
    db = sys.argv[1] if len(sys.argv) > 1 else "~/.elfmem/agent.db"
    config = sys.argv[2] if len(sys.argv) > 2 else None
    asyncio.run(seed_self(db, config))
