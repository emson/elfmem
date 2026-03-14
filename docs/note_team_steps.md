  How a team agent would use this:
  1. Before work: elfmem_recall("my task description", frame="task")
  2. During work: elfmem_remember("discovered pattern X", tags=["team/discovery"])
  3. After work: elfmem_outcome(block_ids, signal=0.9) to reinforce what guided success

  The key insight from testing: Constitutional blocks dominate early. As team agents use outcome() to reinforce the practical coding/testing blocks, those will rise in ranking.
  The memory literally gets better at guiding the team over time.
