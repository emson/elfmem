# LoCoMo Benchmark — Claude Team Prompt

Paste this into Claude Code with agent teams enabled
(`CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`).

---

## The Prompt

```
Build the LoCoMo benchmark harness for elfmem. LoCoMo (ACL 2024) tests long-term
conversational memory with 10 conversations (19-32 sessions each) and 1,986 QA pairs
across 5 categories: single-hop (841), multi-hop (282), temporal (321), open-ended (96),
adversarial (446).

Read these files before planning:
- docs/guides/benchmark_report_spec.md — MANDATORY output format, baselines, metrics standard
- docs/guides/locomo_benchmark_guide.md — full design guide with architecture decisions
- docs/guides/benchmark_strategy.md — why LoCoMo, what we're measuring
- .elfmem/config.yaml — our local Gemma model config
- src/elfmem/config.py — ElfmemConfig model (config key is "embeddings" not "embedding", use model_validate() not from_dict())
- src/elfmem/api.py — MemorySystem public API
- src/elfmem/types.py — ScoredBlock (has .tags: list[str], .score, .content)

Then plan the implementation as a team. The LoCoMo dataset is at ../locomo/data/locomo10.json
(clone https://github.com/snap-research/locomo if not present).

Create an agent team with 3 teammates using Sonnet:

- Teammate "foundation" owns: benchmarks/shared/answerer.py, benchmarks/locomo/config.py,
  benchmarks/locomo/data.py, benchmarks/locomo/metrics.py, tests/benchmarks/test_locomo_metrics.py.
  Pure Python — no elfmem imports. Implements data loading (typed dataclasses from locomo10.json),
  scoring (Porter-stemmed F1 matching LoCoMo exactly, adversarial substring check, retrieval recall),
  answer generation (OpenAI-compatible client → LM Studio at localhost:1234), and config dataclass.
  Must write and run pytest for metrics. Category 5 has no "answer" field — scorer checks prediction
  for "not mentioned" / "no information available" only. Open-ended (cat 3) uses answer.split(";")[0].

- Teammate "integration" owns: benchmarks/locomo/adapter.py, benchmarks/locomo/baselines.py,
  benchmarks/locomo/report.py (builds standard report JSON per benchmark_report_spec.md).
  The elfmem integration layer. adapter.py: creates temp DB per conversation, replays sessions
  via learn() with content "[{date}] {speaker}: {text}" and tags ["dia:{dia_id}", "session:{n}",
  "speaker:{name}"], consolidates after each session, queries via frame("attention", question).
  baselines.py: "no retrieval" (LLM with no context) and "perfect retrieval" (evidence turns
  stuffed into prompt) baselines using the same answerer. Uses ElfmemConfig.model_validate()
  (NOT from_dict). Config key is "embeddings" (NOT "embedding"). Temp DB cleanup in finally block
  (.db, .db-wal, .db-shm). DO NOT modify any files in src/elfmem/.

- Teammate "runner" owns: benchmarks/locomo/runner.py, benchmarks/locomo/__init__.py,
  benchmarks/__init__.py, benchmarks/shared/__init__.py, tests/benchmarks/__init__.py.
  CLI entry point (python -m benchmarks.locomo.runner). Flags: --test (1 conv, 5 Qs),
  --category=N, --max-conv=N, --baselines, --resume, --top-k=N. Crash-safe (writes each
  result immediately). Progress logging per conversation. Output JSON MUST conform to
  benchmark_report_spec.md (meta envelope with config+versions, scores, baselines,
  retrieval recall, efficiency, per-question detail). Per-question results use standardised
  field names: prediction (not hypothesis/output), ground_truth (not answer), score, metric.
  Results dir: benchmarks/locomo/results/{timestamp}_locomo_elfmem.json.

File ownership is strict — each teammate writes ONLY their listed files. "runner" depends on
both "foundation" and "integration", so should start work after the interfaces are clear but
can code against the planned types. "integration" imports from foundation (data types, answerer).

Coordination: "foundation" should message "integration" when data types (Conversation, QA
dataclasses) are defined so integration can code against them. "integration" should message
"runner" when the adapter interface (process_conversation return type) is settled.

After all teammates complete, run: pytest tests/benchmarks/ to verify metrics, then
python -m benchmarks.locomo.runner --test to smoke-test with 1 conversation.

Report results and any issues found.
```

---

## Pre-Flight Checklist

Before pasting the prompt:

1. **LM Studio running** with `google/gemma-4-26b-a4b` + `text-embedding-nomic-embed-text-v1.5`
2. **LoCoMo cloned**: `git clone https://github.com/snap-research/locomo.git ../locomo`
3. **Deps installed**: `pip install nltk rouge-score tqdm`
4. **Teams enabled**: `export CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`
5. **Permissions**: Consider `--dangerously-skip-permissions` for autonomous team runs

## After the Team Completes

```bash
# Verify metrics tests
pytest tests/benchmarks/test_locomo_metrics.py -v

# Smoke test (1 conversation, 5 questions)
python -m benchmarks.locomo.runner --test

# Full run with baselines
python -m benchmarks.locomo.runner --baselines

# Results will be in benchmarks/locomo/results/
```

## Adapting for Other Benchmarks

Replace the prompt's benchmark-specific details while keeping the 3-teammate structure:

| Role | LoCoMo | MemoryAgentBench | LongMemEval |
|------|--------|-----------------|-------------|
| **foundation** | LoCoMo metrics + data | MABench metrics + HF data loading | LME metrics + data |
| **integration** | Session replay adapter | Chunk ingestion adapter | Session replay adapter |
| **runner** | CLI + JSON output | CLI + JSON output | CLI + JSONL output |

The `benchmarks/shared/answerer.py` is shared across all three — build it once with LoCoMo.
