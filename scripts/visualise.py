#!/usr/bin/env python3
"""Quick demo script to generate and view the elfmem dashboard.

Usage:
    uv run demo_visualise.py                                    # temp database
    uv run demo_visualise.py /Users/emson/.elfmem/agent.db     # specific database
    uv run demo_visualise.py /tmp/my_memory.db
    uv run demo_visualise.py /tmp/my_memory.db --archived       # show archived blocks

Opens an interactive visual representation of your knowledge graph in your browser.
"""

import asyncio
import sys
import tempfile
from pathlib import Path


async def main() -> None:
    from elfmem import MemorySystem

    # Parse arguments
    db_path: str | None = None
    include_archived = False

    for arg in sys.argv[1:]:
        if arg == "--archived":
            include_archived = True
        else:
            db_path = arg

    if db_path is None:
        db_path = str(Path(tempfile.gettempdir()) / "elfmem_demo.db")

    print(f"📦 Loading MemorySystem from {db_path}")
    ms = await MemorySystem.from_config(db_path)

    # Check if database is empty
    status = await ms.status()
    is_empty = status.inbox_count == 0 and status.active_count == 0

    async with ms.session():
        if is_empty:
            print("📝 Database is empty, adding demo knowledge...")
            await ms.learn("The sky is blue during clear days.")
            await ms.learn("Photosynthesis converts light into chemical energy.")
            await ms.learn("Water boils at 100°C at sea level.")
            await ms.learn("Machine learning models require training data.")
            await ms.learn("Neural networks are inspired by biological neurons.")

            print("🧠 Consolidating...")
            if ms.should_dream:
                await ms.dream()
        else:
            print(f"   Found {status.active_count} active blocks, {status.inbox_count} in inbox")

    # Generate the dashboard
    print("🎨 Generating dashboard...")
    html_path = ms.visualise(open_browser=True, include_archived=include_archived)
    print(f"✅ Dashboard generated: {html_path}")
    print(f"   Open in browser: file://{html_path}")

    await ms.close()


if __name__ == "__main__":
    asyncio.run(main())
