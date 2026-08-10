# Judge stage — instructions for Claude (or a spawned subagent)

Forked from `ctx`'s `references/judge_prompt.md`. This is not a script —
relevance judgment needs reasoning, so it's Claude's job — read
`elf_recall_find.py`'s output and produce one judgment per candidate.

**Difference from ctx's original**: dropped the `freshness` field.
`ctx`'s free-form vault has no reliable timestamp; elfmem blocks already
carry a real `created:` frontmatter date and their own decay semantics, so a
judge's freshness *guess* would be redundant with — and could contradict —
data the block already states. If a query is genuinely time-sensitive,
`created:` in the matched context is the ground truth to cite.

## When to judge inline vs. spawn subagents

- **≤5 candidates**: judge directly from `elf_recall_find.py`'s `context`
  field (already a small excerpt around each match). No subagent needed.
- **>5 candidates**: spawn one subagent per candidate file (or small batch)
  using the template below, to keep rejected-file content out of the main
  conversation's context. If subagents aren't available, judge inline anyway
  — degraded, not blocked.

## Subagent prompt template

```
You are judging whether a passage from elf's own memory (.elfmem/memory/) is
relevant to a question. You will not see the whole conversation — only this
passage and the question.

Question: {{query}}

File: {{file}}
Matched context:
{{context}}

Return exactly one JSON object, nothing else:
{
  "relevant": true|false,
  "excerpt": "<the minimal verbatim quote from the context above that bears on the
              question — do not paraphrase, do not add words, copy exactly>",
  "heading_path": "{{heading_path from input}}",
  "reason": "<one line: why this bears on the question, or why it doesn't>",
  "kind": "decision" | "example" | "caveat" | "data" | "question" | "other",
  "confidence": "high" | "medium" | "low"
}
```

## The fixed vocabulary — do not deviate from it

| Field | Values | Meaning |
|---|---|---|
| `kind` | `decision` | The passage states a conclusion, choice, or "we should/shouldn't" |
| | `example` | A concrete instance, code sample, or illustration |
| | `caveat` | A warning, limitation, or "but note that..." |
| | `data` | A fact, number, date, or measurement |
| | `question` | The passage poses a question rather than answering one |
| | `other` | None of the above fit |
| `confidence` | `high` \| `medium` \| `low` | How directly the excerpt bears on the query — not how well-written the passage is |

## The verbatim rule for `excerpt`

`excerpt` must be an exact substring of the provided context — no
summarizing, no correcting grammar, no adding connective words.
`elf_recall_worksheet.py` renders it directly into the worksheet.

## What "relevant: false" is for

Include judged-irrelevant candidates in the output too, with
`relevant: false` — don't just omit them. `elf_recall_worksheet.py` filters
them out before rendering, but a visible `relevant: false` record (versus a
silently missing one) is what makes "why didn't X show up" answerable later.

## Reminder: label output unranked

Match order is not relevance order. If summarizing results back to the user,
say so — this skill's output can disagree with `frame()`/`recall()`'s
ranked, index-backed output (model.md's S11), and conflating the two is the
labelled risk that design accepted, not a bug to silently paper over.
