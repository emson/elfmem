# A/B Prompt Testing System

## Overview

This document describes how the A/B prompt testing system works, how to replicate it in a new project, and the philosophy behind controlled prompt experimentation.

The system enables **hypothesis-driven prompt evolution** by allowing safe, isolated testing of prompt variants against a stable baseline without requiring git branching or complex versioning.

---

## Core Architecture

The system uses a **three-layer prompt organization** with controlled variant testing:

```
prompts/
├── baseline/              # Production prompts (the "control")
│   └── {agent}/
│       ├── system.md      # Role, objectives, primary instructions
│       ├── evidence_rules.md  # Detection patterns and rules
│       ├── output_schema.md   # JSON structure requirements
│       ├── doc_notes.md   # (Optional) Processing guidance
│       └── fewshots/      # (Optional) Few-shot examples
│
└── variants/              # A/B test variants (only modified files)
    └── {variant_name}/
        └── {agent}/
            └── system.md  # Only override what changed
```

**Key principle**: Variants only contain files that differ from baseline. Other modules fall back automatically.

---

## How It Works: Resolution Algorithm

When running detection with a variant, the system uses **fallback resolution**:

```
For agent={detector}, module={system.md}, variant={exhaustive}:

1. Try:  prompts/variants/exhaustive/detector/system.md
2. If not found → Fall back to: prompts/baseline/detector/system.md
3. If still not found → Raise error (fail fast)
```

This is implemented in `VariantResolver.resolve_prompt_path()`:

- Returns a tuple: `(file_path, is_from_variant: bool)`
- The boolean tells you whether the module came from the variant or baseline
- Metadata tracks which modules fell back (useful for auditing)

### Module Resolution Hierarchy

**Required modules** (must exist or fail):
- `system.md` - Role, objectives, primary instructions
- `evidence_rules.md` - Patterns to search for
- `output_schema.md` - JSON structure

**Optional modules** (gracefully skip if missing):
- `doc_notes.md` - Additional processing guidance
- `fewshots/top_risks.md` - Few-shot examples

The resolver treats these categories differently:
- **Required**: If a required module is missing from both variant and baseline, raise `FileNotFoundError` immediately (fail fast)
- **Optional**: If an optional module is missing, continue without it silently

---

## Prompt Composition

The `compose_prompt()` function assembles the final prompt from modular components:

### Assembly Process

```
1. Load required modules (in order):
   - system.md
   - evidence_rules.md
   - output_schema.md

2. Load optional modules:
   - doc_notes.md (if exists)
   - fewshots/top_risks.md (if requested)

3. Inject document text

4. Join all sections with double newlines

5. Return (assembled_prompt, metadata)
```

### Composition Metadata

Each composition returns metadata tracking:

```python
{
    "agent": "detector",
    "variant": "exhaustive",
    "modules_loaded": [
        {"module": "system.md", "from_variant": True, "path": "prompts/variants/exhaustive/detector/system.md"},
        {"module": "evidence_rules.md", "from_variant": False, "path": "prompts/baseline/detector/evidence_rules.md"},  # Fallback!
        {"module": "output_schema.md", "from_variant": False, "path": "prompts/baseline/detector/output_schema.md"},
    ],
    "fallbacks_used": [
        {"module": "evidence_rules.md", ...},
        {"module": "output_schema.md", ...}
    ],
    "total_chars": 8234,
    "composition_timestamp": "2025-02-03T10:30:45.123456+00:00"
}
```

This metadata provides an audit trail of what was loaded and from where, critical for reproducibility and debugging.

---

## CLI Integration

Variants are integrated at the CLI level and passed through the entire pipeline:

```bash
# Run with baseline (default)
uv run python -m cli detect ./output/test01
# → Uses prompts/baseline/detector/
# → Outputs: output/test01/detect.json

# Run with specific variant
uv run python -m cli detect ./output/test01 --variant exhaustive
# → Tries prompts/variants/exhaustive/detector/, falls back to baseline
# → Outputs: output/test01/detect_exhaustive.json

# Run with another variant
uv run python -m cli detect ./output/test01 --variant highrecall
# → Outputs: output/test01/detect_highrecall.json
```

### Output File Naming

Output filenames change based on variant:

- **Baseline**: `detect.json`
- **Variant**: `detect_{variant_name}.json`

This allows side-by-side comparison of baseline vs. all variants without overwriting results.

---

## Variant Naming Convention

Use **1-2 word hypothesis names** that describe what you're testing, not version numbers or generic descriptions:

### ✅ Good Names
- `exhaustive` - Add comprehensive pattern checklist
- `highrecall` - Lower false negative threshold
- `strict` - Increase precision requirements
- `selfcheck` - Add verification checklist before output
- `multistage` - Break analysis into sequential stages
- `contrastive` - Add positive/negative examples

### ❌ Bad Names
- `v2`, `v3`, `v1.5` - Non-descriptive version numbers
- `better_detector_attempt_1` - Too verbose and unclear
- `experimental_thing` - Vague intent
- `claude_sonnet_test` - Model version, not hypothesis
- `attempt` - No clarity on what's being tested

The name should tell you **exactly what hypothesis you're testing** without needing to read the files.

---

## Real-World Example: Two Variants

This project demonstrates the pattern with two detector variants:

### Baseline (`prompts/baseline/detector/system.md`)
- **Approach**: Balanced precision/recall
- **Philosophy**: "Zero false positives. All true positives."
- **Scope**: Flags CRITICAL and HIGH risk only
- **Risk filtering**: Conservative—explicitly states what NOT to flag

### Exhaustive Variant (`prompts/variants/exhaustive/detector/system.md`)
- **Hypothesis**: Add mandatory pattern checklist → catch missed patterns
- **Key changes**:
  - Adds explicit "MANDATORY PATTERN CHECKLIST" with 8 patterns
  - Each pattern must be checked and reported if found
  - "Do NOT self-limit. Report ALL findings"
  - Adds verification checklist (8 items to verify before output)
  - Provides detailed examples for each pattern
- **Outcome**: Higher recall (catches more issues), potentially more false positives

### High-Recall Variant (`prompts/variants/highrecall/detector/system.md`)
- **Hypothesis**: Lower confidence threshold → maximize recall
- **Key changes**:
  - Primary objective: "100% Recall"
  - "When in doubt, INCLUDE the finding"
  - Expanded pattern descriptions with real contract examples
  - Verification checklist with 10 items (more comprehensive)
  - Clearer "What NOT to flag" section to reduce noise
- **Outcome**: Even higher recall than exhaustive, with better signal/noise ratio

---

## Testing Workflow

### Standard Testing Loop

```bash
# 1. Process contract once (generates synthesis.yaml)
# This step only needs to happen once per contract
uv run python -m cli process ./data/contracts/TEST01.docx
# Output: ./output/test01/

# 2. Test baseline variant (creates detect.json)
uv run python -m cli detect ./output/test01
# Output: ./output/test01/detect.json

# 3. Test variant 1 (creates detect_exhaustive.json)
uv run python -m cli detect ./output/test01 --variant exhaustive
# Output: ./output/test01/detect_exhaustive.json

# 4. Test variant 2 (creates detect_highrecall.json)
uv run python -m cli detect ./output/test01 --variant highrecall
# Output: ./output/test01/detect_highrecall.json

# 5. Compare outputs
diff output/test01/detect.json output/test01/detect_exhaustive.json
diff output/test01/detect.json output/test01/detect_highrecall.json

# 6. Evaluate against golden truth
# If available: compare against manually-verified ground truth clauses
# Count findings, check evidence spans, verify accuracy
```

### Batch Testing Across Multiple Contracts

```bash
# Process all test contracts
for contract in data/contracts/TEST*.docx; do
  uv run python -m cli process "$contract"
done

# Test baseline across all
for dir in output/test*/; do
  uv run python -m cli detect "$dir"
done

# Test variant across all
for dir in output/test*/; do
  uv run python -m cli detect "$dir" --variant exhaustive
done

# Collect results for analysis
ls output/*/detect.json
ls output/*/detect_exhaustive.json
```

---

## Key Design Decisions

| Decision | Why |
|----------|-----|
| **Partial overrides only** | Variants contain only changed files. Keeps git diffs small and readable. Easier to review what changed. |
| **Fallback to baseline** | No broken variants if one module is missing. Safe experimentation—variants can't cause errors by incompleteness. |
| **Metadata tracking** | Know which modules came from variant vs. baseline. Provides audit trail for reproducibility and debugging. |
| **Output file naming** | Different filenames per variant allow side-by-side comparison. Results aren't overwritten. Historical data is preserved. |
| **Fail-fast on required** | Required modules (system, rules, schema) must exist or error immediately. Prevents incomplete/broken prompt compositions. |
| **Graceful optional** | Optional modules (doc notes, fewshots) skip silently if missing. More flexible, less brittleness. |
| **Simple composition** | Modules are concatenated in order, not templated. Debugging is straightforward. You can read the final prompt sequentially. |

---

## Code Pattern Reference

### Variant Resolution

```python
from engine.variant_resolver import VariantResolver

resolver = VariantResolver()

# Resolve with variant
path, is_from_variant = resolver.resolve_prompt_path(
    agent="detector",
    module="system.md",
    variant="exhaustive"
)
# Returns: (Path, bool)
# Example: (Path("prompts/variants/exhaustive/detector/system.md"), True)

# Resolve without variant (baseline only)
path, is_from_variant = resolver.resolve_prompt_path(
    agent="detector",
    module="system.md",
    variant=None
)
# Returns: (Path, bool)
# Example: (Path("prompts/baseline/detector/system.md"), False)
```

### Prompt Composition

```python
from engine.compose import compose_prompt

prompt, metadata = compose_prompt(
    agent="detector",
    document=document_obj,
    variant="exhaustive",  # None for baseline
    include_fewshots=True
)

# prompt is the assembled prompt string (ready for LLM)
# metadata contains composition details:
print(metadata["agent"])          # "detector"
print(metadata["variant"])        # "exhaustive"
print(metadata["modules_loaded"]) # List of loaded modules with sources
print(metadata["fallbacks_used"]) # Which modules fell back to baseline
print(metadata["total_chars"])    # Prompt size
```

### CLI Integration

```python
import typer
from engine.detect import run_detection

@app.command()
def detect(
    contract_dir: Path = typer.Argument(...),
    variant: str | None = typer.Option(None, "--variant", "-v"),
    force: bool = typer.Option(False, "--force", "-f"),
) -> None:
    """Detect issues from synthesis data."""

    # Pass variant to detection function
    detection = asyncio.run(run_detection(contract_dir, variant))

    # Determine output path based on variant
    if variant and variant != "baseline":
        output_path = contract_dir / f"detect_{variant}.json"
    else:
        output_path = contract_dir / "detect.json"

    output_path.write_text(json.dumps(detection, indent=2))
```

---

## How to Replicate in a New Project

### Step 1: Create Directory Structure

```bash
# Create baseline prompts
mkdir -p prompts/baseline/{agent1,agent2}/
mkdir -p prompts/baseline/{agent1,agent2}/fewshots/

# Create variants directory (empty at first)
mkdir -p prompts/variants/
```

### Step 2: Implement VariantResolver

Create a module that resolves prompt paths with fallback logic:

```python
class VariantResolver:
    """Resolves prompt paths with baseline fallback.

    Resolution logic:
        1. If variant specified: try prompts/variants/{variant}/{agent}/{module}
        2. Fallback to: prompts/baseline/{agent}/{module}
        3. Raise FileNotFoundError if not found in either location
    """

    def __init__(self, prompts_base_dir: Path = Path("prompts")):
        self.base_dir = prompts_base_dir
        self.baseline_dir = prompts_base_dir / "baseline"
        self.variants_dir = prompts_base_dir / "variants"

    def resolve_prompt_path(
        self,
        agent: str,
        module: str,
        variant: str | None = None,
    ) -> tuple[Path, bool]:
        """Resolve prompt path with fallback. Returns (path, is_from_variant)."""

        if variant is None:
            baseline_path = self.baseline_dir / agent / module
            if baseline_path.exists():
                return (baseline_path, False)
            raise FileNotFoundError(f"Baseline prompt not found: {agent}/{module}")

        # Try variant first
        variant_path = self.variants_dir / variant / agent / module
        if variant_path.exists():
            return (variant_path, True)

        # Fall back to baseline
        baseline_path = self.baseline_dir / agent / module
        if baseline_path.exists():
            return (baseline_path, False)

        raise FileNotFoundError(
            f"Prompt '{agent}/{module}' not found in variant '{variant}' or baseline"
        )
```

### Step 3: Implement Prompt Composition

Create a composition function that assembles prompts from modules:

```python
def compose_prompt(
    agent: str,
    document: Document,
    variant: str | None = None,
    include_fewshots: bool = True,
) -> tuple[str, dict]:
    """Compose final prompt from modular components with variant support."""

    resolver = VariantResolver()
    modules_loaded = []
    sections = []

    # Load required modules
    required = ["system.md", "evidence_rules.md", "output_schema.md"]
    for module in required:
        path, is_variant = resolver.resolve_prompt_path(agent, module, variant)
        content = path.read_text(encoding="utf-8")
        sections.append(content)
        modules_loaded.append({
            "module": module,
            "path": str(path),
            "from_variant": is_variant,
        })

    # Load optional modules
    optional = ["doc_notes.md"]
    for module in optional:
        try:
            path, is_variant = resolver.resolve_prompt_path(agent, module, variant)
            content = path.read_text(encoding="utf-8")
            sections.append(content)
            modules_loaded.append({
                "module": module,
                "path": str(path),
                "from_variant": is_variant,
            })
        except FileNotFoundError:
            pass  # Optional missing is OK

    # Load few-shots if requested
    if include_fewshots:
        try:
            path, is_variant = resolver.resolve_prompt_path(
                agent, "fewshots/top_risks.md", variant
            )
            content = path.read_text(encoding="utf-8")
            sections.append(content)
            modules_loaded.append({
                "module": "fewshots/top_risks.md",
                "path": str(path),
                "from_variant": is_variant,
            })
        except FileNotFoundError:
            pass  # Few-shots missing is OK

    # Inject document
    sections.append(f"\n# Document Text\n```\n{document.text}\n```\n")

    # Assemble and build metadata
    prompt = "\n\n".join(sections)
    metadata = {
        "agent": agent,
        "variant": variant,
        "modules_loaded": modules_loaded,
        "fallbacks_used": [m for m in modules_loaded if not m["from_variant"]],
        "total_chars": len(prompt),
        "composition_timestamp": datetime.now(UTC).isoformat(),
    }

    return prompt, metadata
```

### Step 4: Integrate with CLI

Add variant parameter to your CLI commands:

```python
@app.command()
def process(
    input_file: Path = typer.Argument(...),
    variant: str | None = typer.Option(None, "--variant", "-v"),
) -> None:
    """Process document with optional prompt variant."""

    prompt, metadata = compose_prompt(
        agent="your_agent",
        document=your_document,
        variant=variant
    )

    # Log variant info
    if variant:
        console.print(f"[blue]Using variant: {variant}[/blue]")
        if metadata["fallbacks_used"]:
            console.print(f"[yellow]Fallbacks: {metadata['fallbacks_used']}[/yellow]")
    else:
        console.print("[blue]Using baseline prompt[/blue]")

    # Run your processing logic...
```

### Step 5: Create Your First Variant

```bash
# Create variant directory
mkdir -p prompts/variants/my_hypothesis/{agent}/

# Copy the module you're modifying
cp prompts/baseline/{agent}/system.md prompts/variants/my_hypothesis/{agent}/

# Edit the variant
vim prompts/variants/my_hypothesis/{agent}/system.md

# Test it
uv run python -m cli process input.txt --variant my_hypothesis
```

---

## Philosophy: Hypothesis-Driven Prompt Evolution

This system embodies a scientific approach to prompt engineering:

1. **Form Hypothesis** - State clearly what you expect to improve and why
   - "Adding explicit exhaustiveness instruction will improve recall for edge cases"
   - "Multi-stage reasoning will reduce missed cross-references"

2. **Design Experiment** - Define metrics before testing
   - "Expected improvement: +15% recall on unknown clauses"
   - "Success criteria: Recall > 95%, precision > 90%"

3. **Create Variant** - Implement only the hypothesized change
   - Modify only the modules you're testing
   - Keep git diffs small and reviewable

4. **Run Experiment** - Test variant alongside baseline
   - Process same data with both
   - Generate separate outputs for comparison

5. **Measure Results** - Compare actual outcomes
   - Count findings (did recall increase?)
   - Check evidence validity (no spurious matches?)
   - Review false positives/negatives

6. **Iterate or Promote**
   - If hypothesis confirmed → Promote variant to baseline
   - If hypothesis rejected → Delete and try different approach
   - If needs refinement → Iterate and re-test

**Key principle**: Variants are cheap. Test freely. Promote only proven improvements.

---

## Debugging and Auditing

### View Composition Metadata

```python
prompt, metadata = compose_prompt("detector", document, variant="exhaustive")

print(f"Agent: {metadata['agent']}")
print(f"Variant: {metadata['variant']}")
print(f"Modules loaded: {len(metadata['modules_loaded'])}")
for m in metadata['modules_loaded']:
    source = "VARIANT" if m["from_variant"] else "BASELINE"
    print(f"  {m['module']:<30} [{source}]")
print(f"Fallbacks: {len(metadata['fallbacks_used'])}")
print(f"Total prompt size: {metadata['total_chars']} chars")
print(f"Composed at: {metadata['composition_timestamp']}")
```

### Export Prompt for Manual Review

```python
prompt, metadata = compose_prompt("detector", document, variant="exhaustive")

# Save for inspection
Path("debug_prompt.md").write_text(prompt)

# Print with module markers
# You can now see exactly how modules are joined
```

### Compare Baseline vs. Variant

```bash
# Generate prompts for both
uv run python scripts/debug_prompt.py --agent detector --output baseline_prompt.md
uv run python scripts/debug_prompt.py --agent detector --variant exhaustive --output variant_prompt.md

# Compare side-by-side
diff baseline_prompt.md variant_prompt.md
```

---

## Common Pitfalls and Solutions

| Problem | Solution |
|---------|----------|
| Variant breaks because required module is missing | Required modules fail-fast. Resolve error immediately. Never leave required modules incomplete. |
| Variant works locally but result differs from baseline | Check composition metadata. Did it fall back to baseline for some modules? |
| Can't remember what variant tested | Use 1-2 word hypothesis names: `exhaustive`, `recall`, `strict`. Not `v1`, `attempt_1`, `thing`. |
| Too many variants cluttering the tree | Delete non-promoted variants regularly. Variants are experimental—don't keep failed experiments. |
| Variant modules are out of sync with baseline | This is expected. Only sync when promoting to baseline. |
| Output files have same name (overwriting results) | Different variant names produce different output filenames (`detect_{variant}.json`). Check CLI path logic. |

---

## Summary

The A/B prompt testing system provides:

- **Safe experimentation**: Isolated variants can't break baseline
- **Clear governance**: Only promote proven improvements
- **Auditable history**: Metadata tracks what loaded from where
- **Simple mechanics**: Fallback resolution and file-based composition
- **Scalable testing**: Test many variants across many documents
- **Git-friendly**: Variants are files, not branches or configs

By treating prompt evolution as a controlled experiment with clear hypotheses and measurements, you can systematically improve LLM behavior without breaking what's already working.
