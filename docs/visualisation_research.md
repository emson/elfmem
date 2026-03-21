# Visualisation Research: elfmem Knowledge System

## Problem Statement

elfmem stores rich, interconnected knowledge — blocks with decay curves, edges with reinforcement histories, frames with scoring pipelines, sessions with temporal boundaries. **None of this is visible.** An agent (or developer) interacting with elfmem today sees only text summaries. The internal dynamics — why a block ranks high, which edges are strengthening, what's about to decay — remain opaque.

We need a visualisation layer that makes the invisible visible, without overwhelming.

---

## What Data Exists (and What Matters)

### High-Value Visualisation Targets

| Data | Why It Matters | Changes How |
|------|---------------|-------------|
| **Knowledge graph** (blocks + edges) | The core structure — how knowledge relates | Edges form/strengthen/decay/break |
| **Block lifecycle** (inbox→active→archived) | Shows system health and learning rhythm | Blocks flow through states over sessions |
| **Decay curves** (recency over active-hours) | The heartbeat of memory — what's alive, what's fading | Exponential decay, reset by reinforcement |
| **Scoring breakdown** (5 components) | Explains retrieval ranking — "why this block?" | Weights shift per frame |
| **Frame retrieval results** | What the agent actually sees | Changes per query/frame |
| **Contradictions** | Unresolved conflicts in knowledge | Created during consolidation, resolved by agent |
| **Token usage** | Cost awareness | Accumulates per session/lifetime |
| **Session timeline** | Temporal context for all operations | Discrete sessions with operation history |

### Lower-Value (Skip Initially)

| Data | Why Skip |
|------|----------|
| Raw embeddings | High-dimensional, needs UMAP/t-SNE — expensive and hard to interpret |
| System config internals | Only useful for debugging, not understanding |
| Consolidation policy details | Implementation detail, not user-facing insight |

---

## Design Principles

1. **Progressive disclosure** — summary first, drill-down on demand
2. **Zero infrastructure** — no servers, no databases, no npm builds
3. **Library-native** — generated from Python, consumed anywhere
4. **Informative over decorative** — every visual element must answer a question
5. **Edge-case resilient** — empty databases, single blocks, disconnected graphs, zero sessions

---

## Approach Evaluation

### A. Rich Terminal (textual/rich TUI)

**Strengths:** Fits CLI ethos, real-time, no browser needed, familiar to developers.

**Weaknesses:** Graphs in terminal are crude (ASCII/braille). Limited interactivity. Can't zoom/pan a knowledge graph. Screen size constrains layout. Poor for sharing.

**Verdict:** Good for quick health checks (status already does this). **Insufficient for graph/decay/scoring visualisation.**

### B. Static Images (matplotlib/seaborn)

**Strengths:** Python-native, familiar, easy to generate, works in notebooks and docs.

**Weaknesses:** Not interactive — can't click a node to see its edges. NetworkX graph layouts are mediocre for < 100 nodes. Multiple separate images feel disconnected. Decay curves are fine but graphs are the weak point.

**Verdict:** Decent for charts (decay, scoring, lifecycle counts). **Poor for the knowledge graph**, which is the most valuable target.

### C. Single-File HTML Dashboard

**Strengths:** Rich interactivity (zoom, pan, hover, click). Force-directed graph layout with physics simulation. All JS/CSS inlined — just open the file. Shareable. Beautiful with minimal effort. No server needed.

**Weaknesses:** Requires browser (acceptable for deep exploration). Generating HTML from Python adds a template layer. JS libraries must be vendored or fetched from CDN.

**Verdict:** **Best fit for primary visualisation.** Handles the knowledge graph beautifully. Charts, timelines, and drill-downs all work naturally.

### D. Streamlit/Gradio App

**Strengths:** Interactive, Pythonic, live connection to database.

**Weaknesses:** Heavy dependency. Requires running a server. Overkill for a library. Ties visualisation to a runtime.

**Verdict:** **Over-engineered for our use case.** elfmem is a library, not a web application.

### E. Jupyter Widgets

**Strengths:** Interactive, inline with code, great for exploration.

**Weaknesses:** Requires Jupyter environment. Not all users use notebooks. Graph widgets (ipycytoscape) are powerful but niche.

**Verdict:** **Nice secondary target**, not primary. Could offer a `to_notebook()` integration later.

### F. GraphViz DOT Export

**Strengths:** Excellent layout algorithms for directed graphs. Standard format. Many renderers.

**Weaknesses:** Requires graphviz installed. Static output (SVG/PNG). No interactivity. Not great for temporal data.

**Verdict:** **Good complement** for static documentation. Not the primary tool.

---

## Recommended Architecture

### Hybrid: HTML Dashboard (primary) + Terminal Summary (quick)

```
┌─────────────────────────────────────────────────┐
│                  Python Layer                    │
│                                                  │
│  MemorySystem.visualise(path="report.html")      │
│       │                                          │
│       ├── DataCollector  (queries SQLite)         │
│       │     ├── graph_data()     → nodes, edges  │
│       │     ├── lifecycle_data() → counts, flows │
│       │     ├── decay_data()     → curves by tier│
│       │     ├── scoring_data()   → breakdowns    │
│       │     ├── session_data()   → timeline      │
│       │     └── health_data()    → status snapshot│
│       │                                          │
│       └── HTMLRenderer                           │
│             ├── Jinja2 template (single file)    │
│             ├── JS libs inlined (vis.js, Chart.js)│
│             └── CSS inlined (minimal, dark theme)│
│                                                  │
│  MemorySystem.visualise(format="terminal")       │
│       └── Rich tables + sparklines               │
└─────────────────────────────────────────────────┘
```

### Why This Architecture

1. **DataCollector is reusable** — same data feeds HTML, terminal, future Jupyter, or API consumers
2. **HTML template is a single file** — no build step, no bundler, no node_modules
3. **JS libraries are tiny when scoped right:**
   - vis-network.min.js (~300KB) — force-directed graph with physics, zoom, click events
   - Chart.js (~200KB) — charts for decay curves, lifecycle bars, scoring radar
   - Both can be loaded from CDN with local fallback, or vendored
4. **Dark theme by default** — developers live in dark mode; knowledge graphs look stunning on dark backgrounds

---

## Dashboard Layout

### Page Structure (Single Scrollable Page, Tabbed Sections)

```
┌─────────────────────────────────────────────────────────┐
│  elf Memory Dashboard                    [Generated: …] │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  ┌─── Health Bar ────────────────────────────────────┐  │
│  │  ● Session: active (2.4h)  │ Inbox: 3  Active: 47│  │
│  │  │ Archived: 12  │ Health: good  │ Tokens: 12.3K │  │
│  └───────────────────────────────────────────────────┘  │
│                                                         │
│  [Graph]  [Lifecycle]  [Decay]  [Scoring]  [Timeline]   │
│                                                         │
├─ Graph Tab ─────────────────────────────────────────────┤
│  ┌───────────────────────────────────────────────────┐  │
│  │                                                   │  │
│  │         Force-directed knowledge graph            │  │
│  │                                                   │  │
│  │    ○──────○           Nodes: blocks (active)      │  │
│  │   /│\    /│\          Colour: frame/decay tier     │  │
│  │  ○ ○ ○  ○ ○ ○        Size: reinforcement count    │  │
│  │         ╲│╱           Edges: weight = thickness    │  │
│  │          ○            Edge colour: relation type   │  │
│  │                       Hover: block summary         │  │
│  │                       Click: detail panel          │  │
│  └───────────────────────────────────────────────────┘  │
│  ┌─ Detail Panel (on click) ─────────────────────────┐  │
│  │ Block: "Python decorators wrap..."                │  │
│  │ Status: active  │ Confidence: 0.82                │  │
│  │ Decay tier: STANDARD (λ=0.010)                    │  │
│  │ Tags: [python, patterns, self/skill]              │  │
│  │ Reinforced: 7 times  │ Last: 2.1h ago             │  │
│  │ Edges: 4 (2 similar, 1 co_occurs, 1 outcome)     │  │
│  │ Recency score: 0.979                              │  │
│  └───────────────────────────────────────────────────┘  │
│                                                         │
├─ Lifecycle Tab ─────────────────────────────────────────┤
│  ┌─ Status Distribution ──┐  ┌─ Decay Tier Mix ──────┐ │
│  │  ████████████░░░░░░░░  │  │  ██ permanent (4)     │ │
│  │  inbox(3) active(47)   │  │  ████ durable (8)     │ │
│  │  archived(12)          │  │  ██████████ std (35)   │ │
│  └────────────────────────┘  │  ██ ephemeral (4)     │ │
│                              └────────────────────────┘ │
│  ┌─ Knowledge Flow (Sankey-style) ───────────────────┐  │
│  │  learn(58) ──→ inbox(3) ──→ active(47)            │  │
│  │                    │            │                  │  │
│  │                    └→ dedup(8)  └→ archived(12)    │  │
│  └───────────────────────────────────────────────────┘  │
│                                                         │
├─ Decay Tab ─────────────────────────────────────────────┤
│  ┌─ Recency Curves by Tier ──────────────────────────┐  │
│  │  1.0 ┤ ████                                       │  │
│  │      │ ██ ██                                      │  │
│  │      │ █   ███                                    │  │
│  │  0.5 ┤ █     ████  ← standard (λ=0.01)           │  │
│  │      │ █         ██████                           │  │
│  │      │ █              ██████████                  │  │
│  │  0.0 ┤──────────────────────────── active hours → │  │
│  │      │ ─── permanent  ─── durable  ─── ephemeral │  │
│  └───────────────────────────────────────────────────┘  │
│  ┌─ Block Recency Distribution ──────────────────────┐  │
│  │  Histogram: how many blocks at each recency level │  │
│  │  Highlights blocks near PRUNE_THRESHOLD (0.05)    │  │
│  └───────────────────────────────────────────────────┘  │
│                                                         │
├─ Scoring Tab ───────────────────────────────────────────┤
│  ┌─ Component Radar (per-frame) ─────────────────────┐  │
│  │        similarity                                 │  │
│  │           ╱╲                                      │  │
│  │     reinf╱  ╲confidence                           │  │
│  │         ╱ ●● ╲         SELF: conf+reinf heavy     │  │
│  │    cent╱      ╲recency  ATTENTION: sim+rec heavy  │  │
│  │        ────────                                   │  │
│  └───────────────────────────────────────────────────┘  │
│  ┌─ Score Breakdown (last retrieval) ────────────────┐  │
│  │  Block A: [sim:0.3 conf:0.25 rec:0.2 ...]  = 0.8 │  │
│  │  Block B: [sim:0.1 conf:0.4 rec:0.15 ...] = 0.72 │  │
│  │  Stacked bar chart showing component contribution │  │
│  └───────────────────────────────────────────────────┘  │
│                                                         │
├─ Timeline Tab ──────────────────────────────────────────┤
│  ┌─ Session History ─────────────────────────────────┐  │
│  │  S1 ━━━━━ S2 ━━━━━━━━ S3 ━━━ S4 ━━━━━━━         │  │
│  │  │learn   │learn      │learn  │frame              │  │
│  │  │dream   │frame      │dream  │outcome            │  │
│  │  │frame   │outcome    │curate │curate             │  │
│  └───────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────┘
```

---

## Edge Cases and Mitigations

### Empty/Minimal States

| Scenario | Risk | Mitigation |
|----------|------|------------|
| **Empty database** (zero blocks) | Blank graph, meaningless charts | Show "Getting Started" message with learn() example. Health bar shows "No knowledge yet." |
| **Single block, no edges** | Lonely node, no graph to draw | Show the block with metadata. Graph section says "1 block — connections form during consolidation." |
| **All inbox, nothing consolidated** | No active blocks, no edges, no scores | Health bar shows "attention" state. Prominent "Run dream() to consolidate" suggestion. Lifecycle tab shows inbox-heavy distribution. |
| **All archived** | Empty graph, only historical data | Show archived count. Suggest "Learn new knowledge to rebuild." Decay tab shows the archival curve. |
| **No sessions started** | Missing temporal data | Session timeline hidden. Decay curves use wall-clock approximation with warning. |

### Scale Concerns

| Scenario | Risk | Mitigation |
|----------|------|------------|
| **500+ active blocks** | Graph becomes hairball | Cluster by tag prefix. Show top-50 by centrality with option to expand clusters. Force-directed layout with stronger repulsion. |
| **1000+ edges** | Visual noise | Filter by minimum weight (slider). Default: hide edges with weight < 0.2. Group parallel edges. |
| **Very long content** | Tooltips overflow | Truncate to 200 chars with "..." in tooltips. Full content in detail panel only. |
| **Many frames** | Tab explosion | Show 5 builtins + user-defined in dropdown. Radar chart supports overlay comparison. |

### Data Integrity

| Scenario | Risk | Mitigation |
|----------|------|------------|
| **Orphan edges** (target archived) | Edges pointing to nothing | Filter edges where both endpoints are active. Show archived connections as greyed-out optional layer. |
| **NaN/None in scores** | Chart crashes | Default to 0.0 with visual indicator (dashed border). Never pass None to JS. |
| **Concurrent writes** | Stale snapshot | Generate is a point-in-time snapshot. Add "Generated at: ..." timestamp. Read-only SQLite connection. |
| **Very old database** | Schema mismatch | Check schema version before querying. Graceful fallback for missing columns. |

---

## Technology Choices

### JS Libraries (Inlined in HTML)

| Library | Size (min+gzip) | Purpose | Alternative | Why Chosen |
|---------|-----------------|---------|-------------|------------|
| **vis-network** | ~95KB | Force-directed graph | cytoscape.js (100KB), d3-force (30KB) | Best balance of features, ease, and physics simulation. Clustering built-in. Click/hover events. |
| **Chart.js** | ~70KB | Bar, line, radar charts | Plotly.js (1MB+), lightweight SVG | Small, beautiful defaults, responsive. Radar chart for scoring. Line chart for decay. |
| **No framework** | 0KB | DOM manipulation | React, Vue | Overkill for a generated report. Vanilla JS + template literals suffice. |

**Total JS payload: ~165KB gzip** — acceptable for a local file.

**CDN vs Vendor Strategy:**
- Default: load from CDN (`cdnjs.cloudflare.com`) with integrity hash
- Fallback: if CDN fails, use vendored copy in `src/elfmem/viz/assets/`
- Option: `visualise(offline=True)` inlines everything (larger file, no network needed)

### Python Dependencies

| Dependency | Purpose | Status |
|------------|---------|--------|
| **Jinja2** | HTML template rendering | Already likely available (common). Lightweight. |
| **json** (stdlib) | Serialize data for JS | No new dependency |
| **webbrowser** (stdlib) | Auto-open generated report | No new dependency |

**If Jinja2 is too heavy:** Use Python f-strings / `string.Template` for the HTML. The template is simple enough. This keeps dependencies at zero.

---

## API Design

### Primary Entry Point

```python
# Generate and open HTML dashboard
ms = MemorySystem.from_config("knowledge.db")
ms.visualise()  # → opens report.html in browser

# Customise output
ms.visualise(
    path="my_report.html",   # Custom output path (default: temp file)
    open_browser=True,       # Auto-open (default: True)
    offline=True,            # Inline all JS/CSS (default: False)
    include_archived=False,  # Show archived blocks (default: False)
    max_nodes=100,           # Cap graph nodes (default: 100)
)

# Terminal quick-view (no browser)
ms.visualise(format="terminal")  # Rich table to stdout
```

### Data Layer (Reusable)

```python
from elfmem.viz import DashboardData

# Collect all data for any consumer
data = DashboardData.from_memory_system(ms)
data.graph       # → {"nodes": [...], "edges": [...]}
data.lifecycle   # → {"inbox": 3, "active": 47, "archived": 12, ...}
data.decay       # → {"blocks": [...], "tiers": {...}, "threshold": 0.05}
data.scoring     # → {"frames": {...}, "last_retrieval": [...]}
data.health      # → SystemStatus.to_dict()
data.sessions    # → [{"id": ..., "duration": ..., "ops": [...]}, ...]
data.to_json()   # → JSON string for embedding in HTML
```

### MCP Integration

```python
# New MCP tool
@tool
async def elfmem_visualise(path: str = None, offline: bool = False) -> str:
    """Generate interactive HTML dashboard of the knowledge system."""
    ms = get_memory_system()
    output_path = ms.visualise(path=path, offline=offline)
    return f"Dashboard generated: {output_path}"
```

---

## Implementation Plan

### Phase 1: Data Collection Layer
- `src/elfmem/viz/__init__.py` — public API
- `src/elfmem/viz/data.py` — `DashboardData` class with all query methods
- Tests: verify data extraction from known database states

### Phase 2: HTML Dashboard
- `src/elfmem/viz/template.html` — Jinja2 (or f-string) template
- `src/elfmem/viz/renderer.py` — template rendering + file writing
- Graph tab (vis-network) + Health bar
- Tests: verify HTML generation, check for XSS in block content

### Phase 3: Remaining Tabs
- Lifecycle tab (Chart.js bar/doughnut)
- Decay tab (Chart.js line + histogram)
- Scoring tab (Chart.js radar + stacked bar)
- Timeline tab (HTML/CSS timeline)

### Phase 4: Integration
- `api.py` — add `visualise()` method to `MemorySystem`
- MCP tool registration
- Terminal format option (Rich)

### Phase 5: Polish
- Dark theme CSS
- Responsive layout
- Accessibility (ARIA labels, keyboard navigation)
- Edge case handling (empty states, scale limits)

---

## Visual Design Decisions

### Colour Palette (Dark Theme)

```
Background:    #1a1b26  (deep navy)
Surface:       #24283b  (card backgrounds)
Border:        #3b4261  (subtle dividers)
Text primary:  #c0caf5  (soft white)
Text secondary:#565f89  (muted)

Node colours (by decay tier):
  Permanent:   #bb9af7  (purple — identity, stands out)
  Durable:     #7aa2f7  (blue — stable, reliable)
  Standard:    #9ece6a  (green — healthy, growing)
  Ephemeral:   #e0af68  (amber — warm, transient)
  Inbox:       #565f89  (grey — not yet processed)
  Archived:    #3b4261  (dim — faded away)

Edge colours (by relation type):
  similar:     #3b4261  (subtle — background connections)
  co_occurs:   #7aa2f7  (blue — Hebbian, learned)
  outcome:     #9ece6a  (green — validated by results)
  agent:       #ff9e64  (orange — explicit, human-asserted)
  contradicts: #f7768e  (red — conflict, attention needed)

Health indicator:
  good:        #9ece6a  (green)
  attention:   #e0af68  (amber)
  degraded:    #f7768e  (red)
```

Palette inspired by Tokyo Night — popular with developers, high contrast, accessible.

### Node Sizing

```
radius = BASE_SIZE + log(1 + reinforcement_count) * SCALE_FACTOR
```
- Base: 8px (never invisible)
- Max: 30px (heavily reinforced constitutional blocks)
- Log scale prevents outliers from dominating

### Edge Rendering

```
thickness = 1 + weight * 4           (1px to 5px)
opacity   = 0.3 + weight * 0.5       (subtle to prominent)
dash      = solid if reinforced, dashed if weight < 0.3
```

---

## Consequences and Trade-offs

### What We Gain
- **Debugging power:** "Why did frame() return block X over block Y?" — look at the scoring breakdown
- **System intuition:** See how knowledge clusters, decays, and reinforces over time
- **Health monitoring:** Spot problems before they matter (inbox buildup, over-archival, disconnected clusters)
- **Onboarding:** New users understand the system by seeing it work
- **Demo/sharing:** A beautiful HTML file is worth a thousand status() calls

### What We Pay
- **~500 lines of new code** (data collection + HTML template + renderer)
- **Optional Jinja2 dependency** (or zero dependencies with f-string templates)
- **JS payload in generated files** (~165KB gzip, ~500KB raw) — acceptable for local files
- **Maintenance surface** for the template as the data model evolves

### What We Explicitly Skip (for now)
- Real-time / live-updating dashboards (YAGNI — generate-and-view is sufficient)
- Embedding space visualisation (UMAP/t-SNE — expensive, hard to interpret, low ROI)
- Multi-database comparison (single database per report)
- Edit/modify capabilities in the dashboard (read-only — mutations go through the API)

---

## Decision Summary

| Decision | Choice | Rationale |
|----------|--------|-----------|
| **Primary format** | Single-file HTML | Interactive, zero infra, shareable, beautiful |
| **Graph library** | vis-network | Best physics simulation + clustering for our scale |
| **Chart library** | Chart.js | Small, beautiful defaults, radar chart support |
| **Template engine** | Jinja2 (or string.Template) | Minimal dependency, clean separation |
| **Theme** | Dark (Tokyo Night inspired) | Developer-friendly, graphs pop on dark backgrounds |
| **API surface** | `ms.visualise()` | One call, sensible defaults, progressive options |
| **Data layer** | Separate `DashboardData` class | Reusable across formats (HTML, terminal, notebook) |
| **Terminal fallback** | Rich tables | Quick health check without browser |
| **Dependency strategy** | Zero required, Jinja2 optional | Respects elfmem's "zero infrastructure" principle |
| **Scale strategy** | Top-N by centrality + clustering | Handles 500+ blocks gracefully |

---

## Next Steps

1. Review this document and align on approach
2. Implement Phase 1 (data collection layer) with tests
3. Build the HTML template with graph + health bar
4. Iterate on remaining tabs
5. Integrate into `MemorySystem` API and MCP server
