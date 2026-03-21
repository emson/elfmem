# Plan: Visualisation Extra (`elfmem[viz]`)

**Status:** Ready to implement
**Branch:** `visualise` (current)
**Depends on:** Core library (api.py, types.py, db/queries.py) — no other features
**Date:** 2026-03-15

---

## 1. Problem Statement

elfmem's knowledge graph, decay curves, scoring pipeline, and session history are
entirely invisible. An agent or developer interacting with elfmem today sees only
`status()` text and `__str__()` summaries. Critical questions go unanswered:

- Which blocks are clustered together and why?
- What is about to decay into archival?
- Why did frame() return block A over block B?
- Is the inbox dangerously full? Is the graph well-connected?

A dedicated visualisation extra delivers an interactive HTML dashboard — generated
in one call, opened in a browser, requiring zero infrastructure — that makes the
invisible visible.

The feature must remain **opt-in**. Core `elfmem` installs for agents and production
pipelines need zero visualisation overhead. Adding Jinja2 to every install would
penalise 95% of users for a capability only developers and researchers need.

---

## 2. What the `viz` Extra Adds

One new optional install target:

```
uv add elfmem[viz]
```

Three deliverables:

1. **`ms.visualise()`** — new method on `MemorySystem`. Collects all data, renders
   a single-file HTML dashboard, writes it to a temp file, opens it in the browser.
   Raises a clear `ElfmemError` with a `.recovery` field if `viz` is not installed.

2. **`src/elfmem/viz/` subpackage** — isolated from the core package. Only imported
   when called explicitly. Core library startup cost: zero.

3. **`uv add elfmem[viz]` dependency: `jinja2>=3.1`** — the only new dependency.
   JS libraries (vis-network, Chart.js) are loaded from CDN with `integrity` hashes;
   `offline=True` inlines them from vendored copies bundled with the package.

---

## 3. Package Structure

```
src/elfmem/
├── viz/                         # NEW — only imported when visualise() is called
│   ├── __init__.py              # Public API: render_dashboard(), DashboardData
│   ├── data.py                  # DashboardData: queries SQLite, builds JSON payload
│   ├── renderer.py              # render_dashboard(): Jinja2 → HTML string
│   └── assets/
│       ├── dashboard.html.j2    # Jinja2 template — full dashboard
│       ├── vis-network.min.js   # Vendored (offline=True fallback)
│       └── chart.min.js         # Vendored (offline=True fallback)
│
├── api.py                       # MODIFIED — add visualise() method
└── ... (unchanged)

tests/
└── viz/
    ├── __init__.py
    ├── test_dashboard_data.py   # NEW — DashboardData extraction tests
    └── test_renderer.py         # NEW — HTML rendering tests
```

---

## 4. pyproject.toml Changes

Add `viz` to `[project.optional-dependencies]` and include it in `dev`:

```toml
[project.optional-dependencies]
mcp = ["fastmcp>=2.0"]
cli = ["typer>=0.12"]
tools = ["fastmcp>=2.0", "typer>=0.12"]
viz = ["jinja2>=3.1"]                        # NEW
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
    "mypy>=1.8",
    "ruff>=0.3",
    "types-PyYAML",
    "fastmcp>=2.0",
    "typer>=0.12",
    "jinja2>=3.1",                           # NEW — dev always includes viz extra
]
```

No other package-level changes.

---

## 5. API Design

### `MemorySystem.visualise()`

```python
def visualise(
    self,
    path: str | None = None,
    open_browser: bool = True,
    offline: bool = False,
    include_archived: bool = False,
    max_nodes: int = 100,
) -> str:
    """Generate an interactive HTML dashboard of the knowledge system.

    USE WHEN: You want to explore the knowledge graph, understand block decay,
              diagnose retrieval behaviour, or share a snapshot of memory state.
    DON'T USE WHEN: In production agent code — this is a developer/debug tool.
    COST: One synchronous SQLite read pass. No LLM calls. Fast.
    RETURNS: Absolute path to the generated HTML file.
    NEXT: Open the file in a browser. Share with team. No cleanup needed.

    Raises ElfmemError (with .recovery) if elfmem[viz] is not installed.
    """
```

**Guard pattern** (in api.py):

```python
def visualise(self, ...) -> str:
    try:
        from elfmem.viz import render_dashboard, DashboardData
    except ImportError:
        from elfmem.exceptions import ElfmemError
        raise ElfmemError(
            "Visualisation requires the viz extra.",
            recovery="uv add elfmem[viz]",
        )
    data = DashboardData.from_db(self._db_path, include_archived=include_archived, max_nodes=max_nodes)
    return render_dashboard(data, path=path, open_browser=open_browser, offline=offline)
```

This is the complete `visualise()` body. All complexity lives in `viz/`.

### `DashboardData` (public, importable)

```python
from elfmem.viz import DashboardData

data = DashboardData.from_db("knowledge.db")
data.to_json()   # → JSON string for custom consumers (Jupyter, etc.)
```

---

## 6. Data Model: What `DashboardData` Collects

`DashboardData` is a frozen dataclass built from a single synchronous SQLite read pass.
It contains five typed sub-objects, all JSON-serialisable.

### 6.1 `health` — System Snapshot

Derived from `status()` equivalent DB queries. No session operations.

```python
@dataclass(frozen=True)
class HealthData:
    inbox_count: int
    active_count: int
    archived_count: int
    total_active_hours: float
    last_consolidated: str         # ISO or "never"
    health: str                    # "good" | "attention" | "degraded"
    suggestion: str
    lifetime_tokens: dict          # TokenUsage.to_dict()
    generated_at: str              # ISO timestamp of dashboard generation
```

### 6.2 `graph` — Knowledge Graph

Nodes are active blocks (capped to `max_nodes` by centrality). Edges connect them.
Archived blocks included as dim nodes if `include_archived=True`.

```python
@dataclass(frozen=True)
class GraphData:
    nodes: list[dict]   # id, label (truncated content), decay_tier, status,
                        # confidence, reinforcement_count, centrality,
                        # self_alignment, tags, category
    edges: list[dict]   # id, from_id, to_id, weight, relation_type, origin,
                        # reinforcement_count
    total_blocks: int   # Total active blocks before max_nodes cap
    truncated: bool     # True if max_nodes cap was applied
```

**Node truncation strategy:** When `total_blocks > max_nodes`, select the top-N
blocks by `centrality` score. This preserves the most connected, most important
nodes — the graph's skeleton. Include a note in the dashboard when truncated.

### 6.3 `lifecycle` — Knowledge Flow Counts

```python
@dataclass(frozen=True)
class LifecycleData:
    inbox: int
    active: int
    archived: int
    tier_counts: dict[str, int]       # {"permanent": 4, "durable": 8, "standard": 35, "ephemeral": 4}
    origin_counts: dict[str, int]     # {"api": 40, "consolidate": 7, ...}
    status_flow: dict[str, int]       # Learn→inbox→active, dedup counts from DB queries
    edge_origin_counts: dict[str, int] # {"similarity": 30, "co_retrieval": 5, "outcome": 2, "agent": 1}
```

### 6.4 `decay` — Recency Distribution

```python
@dataclass(frozen=True)
class DecayData:
    blocks: list[dict]    # id, recency_score, decay_tier, hours_since_reinforced,
                          # reinforcement_count, confidence — for scatter/histogram
    tier_curves: dict     # Pre-computed curve points per tier for the decay chart:
                          # {"standard": [{"x": 0, "y": 1.0}, {"x": 50, "y": 0.60}, ...]}
    prune_threshold: float  # 0.05 — visualise the cliff
    at_risk_count: int      # Blocks with recency_score < 0.10 (near archival)
    current_active_hours: float
```

**Curve generation:** For each decay tier, pre-compute 20 `(hours, recency)` points
from 0 to the "effective horizon" (where recency = 0.01). The JS just plots them —
no maths in the browser.

### 6.5 `scoring` — Frame Weight Profiles

```python
@dataclass(frozen=True)
class ScoringData:
    frames: list[dict]    # name, weights (5 components), token_budget, cache_ttl
                          # — for the radar chart overlay
    last_retrieval: list[dict]  # Most recent frame() call blocks with full score breakdown:
                                # block_id, content_preview, similarity, confidence,
                                # recency, centrality, reinforcement, composite_score
                                # (empty list if no retrieval in current session)
```

**Source for `last_retrieval`:** Pull frame weights from the `frames` table directly.
`last_retrieval` comes from the most recent `FrameResult` data in the `block_outcomes`
table or, if unavailable (no session tracked), returns an empty list with a note.

---

## 7. HTML Template Design

Single Jinja2 template: `src/elfmem/viz/assets/dashboard.html.j2`

The template is a complete, standalone HTML file. All CSS is inline. JS libraries
are loaded from CDN (default) or from inlined copies (`offline=True`).

### 7.1 Design Philosophy: Dieter Rams Applied

The dashboard is a tool, not a product. Every visual decision is evaluated against
Rams' ten principles. The ones that bear most on a developer dashboard:

**"Good design is as little design as possible."**
Remove every element that does not carry information. No decorative borders, no
gradients, no shadows for depth, no rounded corners for softness. Structure comes
from whitespace and a consistent grid — not from boxes and chrome.

**"Good design makes a product understandable."**
The user must grasp the knowledge system's state at a glance. Layout follows the
mental model: health (system) → graph (structure) → lifecycle (flow) → decay
(time) → scoring (retrieval). Each tab answers one question. Labels are plain prose,
not jargon. Empty states explain what to do next — they are not error pages.

**"Good design is unobtrusive."**
The dashboard serves the data. It does not express a personality. System font only
— no web fonts loaded. Colour carries meaning, not decoration. The graph's physics
settle quickly and stop: motion exists to reveal structure, not to impress.

**"Good design is honest."**
No smoothing, interpolation, or rounding of values in the display. A confidence
score of 0.4230 is shown as 0.423, not "medium" or a progress bar. The ARCHIVE
threshold line on the decay chart is drawn at the exact value, not nudged for
aesthetics. Truncation is surfaced, not hidden.

**"Good design is thorough down to the last detail."**
Every pixel is intentional. 8px baseline grid throughout. One rule colour. One
muted text colour. Consistent label casing (sentence case, never ALL CAPS). The
detail panel uses a definition list (`<dl>`) because that is semantically correct
for key-value data. Nothing is arbitrary.

**Applied constraints:**

| What | Rule |
|------|------|
| Colours | Monochrome base + 4 semantic accent colours only |
| Palette tone | Dark, desaturated — colour carries meaning, not energy |
| Tier colours | One hue family, value-differentiated (not a rainbow) |
| Typography | System font stack; 3 sizes max; weight for hierarchy |
| Spacing | Strict 8px grid: 8, 16, 24, 32, 48px |
| Borders | Single 1px rule colour; no border-radius on structural elements |
| Shadows | None |
| Gradients | None |
| Animations | Physics settle only (vis-network); no CSS transitions |
| Icons | None — text labels only |
| Chart decorations | No fill under lines; no data point markers unless interactive |

### 7.2 Colour System

Dark, desaturated. Colour is reserved for semantic meaning only.

```css
:root {
  /* Structure — monochrome */
  --bg:       #0f0f0f;   /* near-black; not pure black (avoids harshness) */
  --surface:  #161616;   /* card/panel background — one step lighter */
  --rule:     #2a2a2a;   /* borders and dividers — single rule colour */
  --text:     #e0e0e0;   /* primary text — slightly warm off-white */
  --muted:    #606060;   /* labels, metadata — exactly half brightness */

  /* Semantic — status */
  --good:     #4e9a63;   /* green, desaturated — system healthy */
  --warn:     #9a7c34;   /* amber, desaturated — needs attention */
  --error:    #9a4a4a;   /* red, desaturated — degraded */

  /* Semantic — decay tiers (one cool hue family, value-differentiated) */
  --permanent: #8888cc;  /* blue-violet, brightest — identity, immortal */
  --durable:   #6688aa;  /* steel blue — stable, long-lived */
  --standard:  #558877;  /* teal — healthy, normal */
  --ephemeral: #886644;  /* warm ochre — transient, fading */
  --inbox:     #444444;  /* neutral grey — not yet processed */
  --archived:  #2a2a2a;  /* near-rule — faded away */

  /* Semantic — edge relation types */
  --edge-similar:    #3a3a3a;   /* structural, background — subtle */
  --edge-co-occurs:  #5577aa;   /* Hebbian, learned — blue */
  --edge-outcome:    #557755;   /* validated — green */
  --edge-agent:      #886633;   /* explicit, human — amber */
  --edge-contradicts:#993333;   /* conflict — red, attention-drawing */
}
```

**Why this palette:**
- Near-black background keeps the graph readable without the visual fatigue of
  pure black
- Tier colours share a cool blue base and differ only in lightness/warmth — the
  progression from permanent → ephemeral reads as a natural gradient of stability
- Status colours are muted (never saturated) to avoid a "traffic light" feel that
  would suggest urgency where none exists
- Edge colours are deliberately dim — most edges are structural noise; only
  agent-asserted and contradiction edges should draw the eye

### 7.3 Typography

```css
:root {
  --font: system-ui, -apple-system, "Segoe UI", sans-serif;
  --mono: ui-monospace, "SF Mono", "Cascadia Code", monospace;

  /* Three sizes only */
  --text-sm:   11px;   /* metadata, timestamps, tag pills */
  --text-base: 13px;   /* body, labels, table cells */
  --text-lg:   16px;   /* section headings */

  /* Two weights only */
  --weight-normal: 400;
  --weight-medium: 500;   /* headings, key labels — not bold */
}

body {
  font-family: var(--font);
  font-size: var(--text-base);
  line-height: 1.5;
  color: var(--text);
  background: var(--bg);
  -webkit-font-smoothing: antialiased;
}
```

No web fonts. System font renders at native quality and loads instantly. No bold
weight — `font-weight: 500` provides sufficient hierarchy without visual weight.

### 7.4 Spacing and Layout

```css
/* 8px baseline grid */
:root {
  --s1:  8px;
  --s2: 16px;
  --s3: 24px;
  --s4: 32px;
  --s6: 48px;
}

/* Single-column, max-width constrained */
body { max-width: 1200px; margin: 0 auto; padding: var(--s3); }

/* Header: one horizontal line of facts, separated by rules */
header {
  display: flex;
  align-items: baseline;
  gap: var(--s3);
  padding: var(--s2) 0;
  border-bottom: 1px solid var(--rule);
  font-size: var(--text-sm);
  color: var(--muted);
}
header .logo    { font-size: var(--text-lg); color: var(--text); font-weight: var(--weight-medium); }
header .health  { color: var(--text); }    /* overridden per health state */
header .spacer  { flex: 1; }               /* pushes timestamp to right */

/* Tab navigation: text links, no pill buttons */
nav { display: flex; gap: var(--s3); padding: var(--s2) 0; border-bottom: 1px solid var(--rule); }
nav button {
  background: none; border: none; padding: 0;
  font: inherit; font-size: var(--text-sm); color: var(--muted);
  cursor: pointer; letter-spacing: 0.03em; text-transform: uppercase;
}
nav button.active { color: var(--text); border-bottom: 1px solid var(--text); }

/* Sections: generous vertical whitespace */
section { padding: var(--s4) 0; }
h2 { font-size: var(--text-lg); font-weight: var(--weight-medium); margin: 0 0 var(--s3); }

/* Two-column grid for paired charts */
.grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: var(--s4); }

/* Detail panel: definition list */
dl { display: grid; grid-template-columns: max-content 1fr; gap: var(--s1) var(--s3); margin: 0; }
dt { color: var(--muted); font-size: var(--text-sm); }
dd { margin: 0; font-family: var(--mono); font-size: var(--text-sm); }

/* Tag pills: minimal */
.tag {
  display: inline-block; padding: 1px 6px;
  border: 1px solid var(--rule); border-radius: 2px;
  font-size: var(--text-sm); color: var(--muted);
}

/* Empty state: centred, muted, instructional */
.empty {
  padding: var(--s6) 0; text-align: center;
  color: var(--muted); font-size: var(--text-sm); line-height: 2;
}
```

**No card borders.** Sections are separated by vertical whitespace and a single
horizontal rule — not by boxes. Visual containment comes from proximity, not chrome.

### 7.5 HTML Structure

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>elf — {{ generated_at }}</title>
  <style>/* All CSS inline — see sections above (~180 lines total) */</style>
</head>
<body>

<header>
  <span class="logo">elf</span>
  <span>{{ inbox }} inbox</span>
  <span>{{ active }} active</span>
  <span>{{ archived }} archived</span>
  <span class="health health-{{ health }}">{{ health }}</span>
  <span class="spacer"></span>
  <span>{{ generated_at }}</span>
</header>

<nav>
  <button class="tab active" data-tab="graph">Graph</button>
  <button class="tab" data-tab="lifecycle">Lifecycle</button>
  <button class="tab" data-tab="decay">Decay</button>
  <button class="tab" data-tab="scoring">Scoring</button>
</nav>

<main>
  <section id="tab-graph">
    <p class="suggestion">{{ suggestion }}</p>    <!-- one sentence, muted -->
    <div id="graph-canvas" style="height:500px"></div>
    <div id="detail-panel" hidden></div>
  </section>
  <section id="tab-lifecycle" hidden>...</section>
  <section id="tab-decay" hidden>...</section>
  <section id="tab-scoring" hidden>...</section>
</main>

<script>const ELFMEM_DATA = {{ data_json | safe }};</script>
{% if offline %}
<script>{{ vis_network_js | safe }}</script>
<script>{{ chartjs | safe }}</script>
{% else %}
<script src="https://cdnjs.cloudflare.com/ajax/libs/vis-network/9.1.9/standalone/umd/vis-network.min.js"
        integrity="sha512-..." crossorigin="anonymous"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"
        integrity="sha512-..." crossorigin="anonymous"></script>
{% endif %}
<script>/* Dashboard JS — ~250 lines, no framework */</script>

</body>
</html>
```

**The suggestion line** — one sentence from `health.suggestion` — sits just below the
header on the graph tab. It is the only prose in the dashboard. Everything else is
data. This is where the system speaks.

### 7.6 Graph Tab (vis-network)

```javascript
const TIER_COLOURS = {
  permanent: "#8888cc",
  durable:   "#6688aa",
  standard:  "#558877",
  ephemeral: "#886644",
  inbox:     "#444444",
  archived:  "#2a2a2a",
};

const EDGE_COLOURS = {
  similar:     "#3a3a3a",
  co_occurs:   "#5577aa",
  outcome:     "#557755",
  agent:       "#886633",
  contradicts: "#993333",
};

// Node size: 6–24px, log-scaled on reinforcement count
const nodeSize = (count) => 6 + Math.log1p(count) * 4;

// Edge width: 1–4px on weight
const edgeWidth = (weight) => 1 + weight * 3;

// Edge opacity: proportional to weight, never fully transparent
const edgeOpacity = (weight) => 0.25 + weight * 0.5;
```

**vis-network options (minimal, fast-settling):**
```javascript
{
  physics: {
    solver: "forceAtlas2Based",
    forceAtlas2Based: { gravitationalConstant: -30, springLength: 80 },
    stabilization: { iterations: 150, fit: true },
  },
  interaction: { hover: true, tooltips: false },  // no tooltips — click for detail
  nodes: {
    shape: "dot",
    borderWidth: 0,               // no border on nodes — shape and colour only
    borderWidthSelected: 1,
    font: { size: 11, color: "#606060", face: "system-ui" },
  },
  edges: {
    smooth: { type: "continuous", roundness: 0.2 },
    selectionWidth: 0,
  },
}
```

**No node labels by default.** Labels appear on hover (programmatically, not via
vis tooltips) and in the detail panel on click. The graph's shape carries meaning;
text on every node is visual noise.

**Click → detail panel** (definition list, monospace values):
```javascript
function showDetail(node, allEdges) {
  const connected = allEdges.filter(e => e.from_id === node.id || e.to_id === node.id);
  const panel = document.getElementById('detail-panel');
  panel.innerHTML = `
    <p style="margin:0 0 var(--s2);color:var(--text)">${escapeHtml(node.content)}</p>
    <dl>
      <dt>Tier</dt>      <dd>${node.decay_tier}</dd>
      <dt>Confidence</dt><dd>${node.confidence.toFixed(3)}</dd>
      <dt>Centrality</dt><dd>${node.centrality.toFixed(3)}</dd>
      <dt>Reinforced</dt><dd>${node.reinforcement_count}×</dd>
      <dt>Alignment</dt> <dd>${node.self_alignment.toFixed(3)}</dd>
      <dt>Tags</dt>      <dd>${node.tags.map(t => `<span class="tag">${escapeHtml(t)}</span>`).join(' ') || '—'}</dd>
      <dt>Edges</dt>     <dd>${connected.map(e => `${e.relation_type} ${e.weight.toFixed(2)}`).join(' · ') || '—'}</dd>
    </dl>
  `;
  panel.hidden = false;
}
```

**Truncation notice** (plain text, one line, muted):
```html
<p class="notice">Showing {{ max_nodes }} of {{ total_blocks }} blocks by centrality.</p>
```

**Empty state:**
```html
<div class="empty">
  No active blocks.<br>
  Call <code>learn()</code> then <code>dream()</code> to build memory.
</div>
```

### 7.7 Lifecycle Tab (Chart.js)

Two charts, side by side via `.grid-2`:

1. **Doughnut — Status counts.** Three segments: inbox / active / archived. No
   centre label (Rams: don't repeat what the legend already says). Colours:
   `--inbox`, `--standard`, `--archived`.

2. **Horizontal bar — Tier distribution.** Four bars, one per decay tier, sorted
   permanent → ephemeral. No grid lines on y-axis; only the x-axis baseline.
   Bar colours match tier palette.

Below the charts: knowledge flow as plain HTML — flex row of labelled boxes
connected by `→` text characters. No SVG, no arrows library.

```html
<div class="flow">
  <div class="flow-box">learn()</div>
  <span class="flow-arrow">→</span>
  <div class="flow-box">inbox<br><span class="flow-count">{{ inbox }}</span></div>
  <span class="flow-arrow">→</span>
  <div class="flow-box">active<br><span class="flow-count">{{ active }}</span></div>
  <span class="flow-arrow">→</span>
  <div class="flow-box">archived<br><span class="flow-count">{{ archived }}</span></div>
</div>
```

### 7.8 Decay Tab (Chart.js)

Two charts, stacked full-width:

1. **Line chart — Decay curves by tier.** Four lines from `tier_curves`. No filled
   area under lines (`fill: false`). No data point markers (`pointRadius: 0`).
   Red dashed horizontal line at `prune_threshold = 0.05` labelled `archive threshold`.
   X-axis: "active hours". Y-axis: "recency" (0.0 → 1.0). Grid lines: x-axis only.

2. **Scatter chart — Live block positions.** One point per block. Colour by tier.
   Point radius: `3 + log1p(reinforcement_count) * 1.5` (4–12px). No borders on
   points. Red shaded region below prune_threshold. Hover title: first 60 chars of
   block content. Count annotation: `{{ at_risk_count }} blocks near archival` in
   `--warn` colour if `at_risk_count > 0`, else omitted.

**Chart.js global defaults (applied once, Rams-aligned):**
```javascript
Chart.defaults.color = '#606060';
Chart.defaults.font.family = 'system-ui, sans-serif';
Chart.defaults.font.size = 11;
Chart.defaults.plugins.legend.labels.boxWidth = 10;
Chart.defaults.plugins.legend.labels.padding = 16;
Chart.defaults.elements.line.borderWidth = 1.5;
Chart.defaults.elements.point.borderWidth = 0;
```

### 7.9 Scoring Tab (Chart.js)

Two panels, side by side:

1. **Radar chart — Frame weight profiles.** One dataset per frame. Five axes:
   similarity, confidence, recency, centrality, reinforcement. `borderWidth: 1`.
   `fill: false` (no shaded area). Dataset colours match no single tier —
   use evenly spaced muted colours (the frames are not decay-tier entities).
   Toggle via legend. Default: show all five builtins.

2. **Stacked horizontal bar — Last retrieval breakdown.** One bar per returned
   block (up to top-k). Five segments per bar (5 score components). Bar height:
   `24px`. Component colours consistent with radar axes. Composite score printed
   as plain text at bar end (`font: var(--mono)`). Empty state message if no
   retrieval data.

**Chart.js radar config (minimal):**
```javascript
{
  scales: {
    r: {
      min: 0, max: 1, ticks: { display: false },
      grid:      { color: '#2a2a2a' },
      angleLines: { color: '#2a2a2a' },
      pointLabels: { font: { size: 11 }, color: '#606060' },
    }
  },
  plugins: { legend: { position: 'bottom' } },
}
```

---

## 8. Files Changed

| File | Change | Lines (est.) |
|------|--------|-------------|
| `pyproject.toml` | Add `viz = ["jinja2>=3.1"]` optional dep; add to `dev` | +2 |
| `src/elfmem/api.py` | Add `visualise()` method with import guard | +25 |
| `src/elfmem/viz/__init__.py` | `render_dashboard`, `DashboardData` public exports | +8 |
| `src/elfmem/viz/data.py` | `DashboardData`, all 5 sub-dataclasses, `from_db()` | +200 |
| `src/elfmem/viz/renderer.py` | `render_dashboard()`, `_write_file()`, `_open_browser()` | +60 |
| `src/elfmem/viz/assets/dashboard.html.j2` | Full Jinja2 template | +400 |
| `src/elfmem/viz/assets/vis-network.min.js` | Vendored (offline fallback) | binary |
| `src/elfmem/viz/assets/chart.min.js` | Vendored (offline fallback) | binary |
| `tests/viz/__init__.py` | Empty | +0 |
| `tests/viz/test_dashboard_data.py` | DashboardData extraction tests | +150 |
| `tests/viz/test_renderer.py` | HTML rendering tests | +80 |

Total new code: ~925 lines Python + HTML/JS. Core library untouched except `api.py`.

---

## 9. Detailed Implementation

### Step 1: `pyproject.toml` — add `viz` extra

```toml
[project.optional-dependencies]
mcp = ["fastmcp>=2.0"]
cli = ["typer>=0.12"]
tools = ["fastmcp>=2.0", "typer>=0.12"]
viz = ["jinja2>=3.1"]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
    "mypy>=1.8",
    "ruff>=0.3",
    "types-PyYAML",
    "fastmcp>=2.0",
    "typer>=0.12",
    "jinja2>=3.1",
]
```

### Step 2: `src/elfmem/viz/__init__.py`

```python
"""elfmem visualisation extra.

Install with: uv add elfmem[viz]

Provides an interactive HTML dashboard of the knowledge system.
"""
from elfmem.viz.data import DashboardData
from elfmem.viz.renderer import render_dashboard

__all__ = ["DashboardData", "render_dashboard"]
```

### Step 3: `src/elfmem/viz/data.py` — `DashboardData`

**Key design decisions:**

- **Synchronous SQLite** — `DashboardData.from_db()` uses a plain `sqlite3` connection,
  not the async SQLAlchemy stack. Visualisation is a developer read-only tool;
  async overhead buys nothing and adds complexity.

- **One connection, five queries** — all data collected in a single `with sqlite3.connect(db_path) as conn:` block. No repeated opens.

- **`max_nodes` applied by centrality** — pre-compute weighted degree for all active
  blocks, sort descending, take top N. Simple, deterministic, correct.

- **Decay curves pre-computed in Python** — generate 25 `(x, y)` points per tier from
  hours=0 to the tier's "horizon" (where recency=0.01). The JS just renders them.

- **`last_retrieval` from `block_outcomes`** — the most recent batch of block_outcome
  rows gives us block_ids that were used. Join with blocks to get score components.
  If the table is empty, return `[]` with `last_retrieval_note = "No outcome data yet."`.

```python
import json
import math
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone

# Decay lambda values (mirrors scoring.py — copied to avoid importing core)
_LAMBDA: dict[str, float] = {
    "permanent": 0.00001,
    "durable":   0.001,
    "standard":  0.010,
    "ephemeral": 0.050,
}
_PRUNE_THRESHOLD = 0.05


@dataclass(frozen=True)
class HealthData: ...

@dataclass(frozen=True)
class GraphData: ...

@dataclass(frozen=True)
class LifecycleData: ...

@dataclass(frozen=True)
class DecayData: ...

@dataclass(frozen=True)
class ScoringData: ...


@dataclass(frozen=True)
class DashboardData:
    health: HealthData
    graph: GraphData
    lifecycle: LifecycleData
    decay: DecayData
    scoring: ScoringData

    @classmethod
    def from_db(
        cls,
        db_path: str,
        *,
        include_archived: bool = False,
        max_nodes: int = 100,
    ) -> "DashboardData":
        """Query SQLite and build all dashboard data.

        USE WHEN: Generating a visualisation. Read-only. No session side effects.
        COST: One synchronous SQLite read pass (~5 queries). Fast.
        """
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            health   = _build_health(conn)
            graph    = _build_graph(conn, include_archived=include_archived, max_nodes=max_nodes)
            lifecycle = _build_lifecycle(conn)
            decay    = _build_decay(conn)
            scoring  = _build_scoring(conn)
        return cls(health=health, graph=graph, lifecycle=lifecycle, decay=decay, scoring=scoring)

    def to_json(self) -> str:
        """Serialise all data to JSON for embedding in HTML or custom consumers."""
        return json.dumps({
            "health":    _asdict(self.health),
            "graph":     _asdict(self.graph),
            "lifecycle": _asdict(self.lifecycle),
            "decay":     _asdict(self.decay),
            "scoring":   _asdict(self.scoring),
        }, default=str)
```

**`_build_graph()` implementation sketch:**

```python
def _build_graph(conn, include_archived, max_nodes):
    # 1. Fetch all active blocks + their tags in two queries
    blocks = conn.execute("SELECT * FROM blocks WHERE status='active'").fetchall()
    tags_rows = conn.execute("SELECT block_id, tag FROM block_tags").fetchall()
    tags_map = {}  # block_id → list[str]
    for row in tags_rows:
        tags_map.setdefault(row["block_id"], []).append(row["tag"])

    # 2. Fetch edges between active blocks
    edges_rows = conn.execute("""
        SELECT e.* FROM edges e
        JOIN blocks ba ON e.from_id = ba.id AND ba.status = 'active'
        JOIN blocks bb ON e.to_id   = bb.id AND bb.status = 'active'
    """).fetchall()

    # 3. Compute weighted degree centrality for all blocks
    degree: dict[str, float] = {}
    for e in edges_rows:
        w = e["weight"]
        degree[e["from_id"]] = degree.get(e["from_id"], 0) + w
        degree[e["to_id"]]   = degree.get(e["to_id"],   0) + w
    max_deg = max(degree.values(), default=1.0)
    centrality = {bid: deg / max_deg for bid, deg in degree.items()}

    # 4. Apply max_nodes cap by centrality
    total = len(blocks)
    if total > max_nodes:
        blocks = sorted(blocks, key=lambda b: centrality.get(b["id"], 0), reverse=True)[:max_nodes]
    included_ids = {b["id"] for b in blocks}

    # 5. Filter edges to included nodes only
    edges_filtered = [e for e in edges_rows if e["from_id"] in included_ids and e["to_id"] in included_ids]

    # 6. Build node dicts
    nodes = [
        {
            "id": b["id"],
            "label": (b["content"] or "")[:60],
            "decay_tier": _infer_tier(tags_map.get(b["id"], []), b["category"]),
            "status": b["status"],
            "confidence": b["confidence"],
            "reinforcement_count": b["reinforcement_count"],
            "centrality": round(centrality.get(b["id"], 0), 4),
            "self_alignment": b["self_alignment"],
            "tags": tags_map.get(b["id"], []),
            "category": b["category"],
            "content": b["content"] or "",   # full content for detail panel
        }
        for b in blocks
    ]
    edge_dicts = [dict(e) for e in edges_filtered]

    return GraphData(nodes=nodes, edges=edge_dicts, total_blocks=total, truncated=total > max_nodes)
```

**`_infer_tier()` helper** (mirrors `determine_decay_tier()` from scoring.py, without importing core):

```python
def _infer_tier(tags: list[str], category: str) -> str:
    if any(t == "self/constitutional" for t in tags):
        return "permanent"
    if any(t in {"self/value", "self/constraint", "self/goal"} for t in tags):
        return "durable"
    if category == "observation":
        return "ephemeral"
    return "standard"
```

**Why copy rather than import?** `viz` is an optional extra. Importing `elfmem.scoring`
at the top of `data.py` would make `data.py` unimportable if someone tries to use
`DashboardData` without the core installed — impossible in practice but inconsistent.
More importantly: `_infer_tier` is 6 lines and mirrors a stable formula. The tiny
duplication is safer than a cross-package coupling that violates the `viz` extra boundary.

### Step 4: `src/elfmem/viz/renderer.py`

```python
import os
import tempfile
import webbrowser
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from elfmem.viz.data import DashboardData

_ASSETS = Path(__file__).parent / "assets"


def render_dashboard(
    data: DashboardData,
    *,
    path: str | None = None,
    open_browser: bool = True,
    offline: bool = False,
) -> str:
    """Render the dashboard HTML and write it to a file.

    USE WHEN: You have a DashboardData object and want to produce the HTML.
    RETURNS: Absolute path to the generated HTML file.
    """
    env = Environment(
        loader=FileSystemLoader(str(_ASSETS)),
        autoescape=select_autoescape(["html"]),
    )
    template = env.get_template("dashboard.html.j2")

    context: dict = {"data_json": data.to_json(), "offline": offline}
    if offline:
        context["vis_network_js"] = (_ASSETS / "vis-network.min.js").read_text()
        context["chartjs"] = (_ASSETS / "chart.min.js").read_text()

    html = template.render(**context)

    output_path = _write_file(html, path)
    if open_browser:
        webbrowser.open(f"file://{output_path}")
    return output_path


def _write_file(html: str, path: str | None) -> str:
    if path is None:
        fd, tmp = tempfile.mkstemp(suffix=".html", prefix="elfmem_dashboard_")
        os.close(fd)
        path = tmp
    Path(path).write_text(html, encoding="utf-8")
    return os.path.abspath(path)
```

### Step 5: `src/elfmem/api.py` — add `visualise()`

Place after `history()`, before `guide()`. Full method:

```python
def visualise(
    self,
    path: str | None = None,
    open_browser: bool = True,
    offline: bool = False,
    include_archived: bool = False,
    max_nodes: int = 100,
) -> str:
    """Generate an interactive HTML dashboard of the knowledge system.

    USE WHEN: Exploring the knowledge graph, diagnosing retrieval, sharing a
              snapshot. Developer/debug tool — not for agent production loops.
    DON'T USE WHEN: In automated pipelines or latency-sensitive agent code.
    COST: One synchronous SQLite read pass. No LLM calls.
    RETURNS: Absolute path to the generated HTML file.
    NEXT: Open the file in a browser. Requires elfmem[viz] extra.
    """
    try:
        from elfmem.viz import DashboardData, render_dashboard
    except ImportError as exc:
        raise ElfmemError(
            "Visualisation requires the viz extra.",
            recovery="uv add elfmem[viz]",
        ) from exc

    data = DashboardData.from_db(
        str(self._db_path),
        include_archived=include_archived,
        max_nodes=max_nodes,
    )
    return render_dashboard(
        data,
        path=path,
        open_browser=open_browser,
        offline=offline,
    )
```

**How to get `_db_path`:** `MemorySystem` already stores the engine URL.
Add `self._db_path: str` in `__init__` / `from_config()`:

```python
# In from_config():
instance._db_path = db_path  # str, already available as parameter
```

This is the only change to `from_config()`.

### Step 6: `src/elfmem/viz/assets/dashboard.html.j2`

Full Jinja2 template. Key sections:

**CSS variables (dark theme, Tokyo Night):**
```css
:root {
  --bg:        #1a1b26;
  --surface:   #24283b;
  --border:    #3b4261;
  --text:      #c0caf5;
  --muted:     #565f89;
  --purple:    #bb9af7;
  --blue:      #7aa2f7;
  --green:     #9ece6a;
  --amber:     #e0af68;
  --red:       #f7768e;
  --orange:    #ff9e64;
}
```

**Tab switching (vanilla JS, ~15 lines):**
```javascript
document.querySelectorAll('.tab').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('section[id^="tab-"]').forEach(s => s.hidden = true);
    btn.classList.add('active');
    document.getElementById('tab-' + btn.dataset.tab).hidden = false;
  });
});
```

**Graph initialisation (vis-network):**
```javascript
function initGraph() {
  const { nodes, edges, truncated, total_blocks } = ELFMEM_DATA.graph;
  if (nodes.length === 0) {
    document.getElementById('graph-container').innerHTML =
      '<div class="empty-state">No active blocks yet.<br>Call learn() and dream() to start building memory.</div>';
    return;
  }
  // Map nodes to vis format
  const visNodes = nodes.map(n => ({
    id: n.id,
    label: n.label,
    color: TIER_COLOURS[n.decay_tier] || '#565f89',
    size: 8 + Math.log1p(n.reinforcement_count) * 5,
    title: `${n.label}\nConf: ${n.confidence.toFixed(2)} | Reinf: ${n.reinforcement_count}`,
    font: { color: '#c0caf5' },
  }));
  const visEdges = edges.map(e => ({
    from: e.from_id, to: e.to_id,
    width: 1 + e.weight * 4,
    color: { color: EDGE_COLOURS[e.relation_type] || '#3b4261', opacity: 0.3 + e.weight * 0.5 },
    dashes: e.weight < 0.3,
  }));
  const network = new vis.Network(
    document.getElementById('graph-container'),
    { nodes: new vis.DataSet(visNodes), edges: new vis.DataSet(visEdges) },
    {
      physics: { stabilization: { iterations: 100 } },
      interaction: { hover: true },
      nodes: { borderWidth: 1, borderWidthSelected: 2 },
      edges: { smooth: { type: 'continuous' } },
    }
  );
  // Click → detail panel
  network.on('click', params => {
    if (params.nodes.length === 0) return;
    const node = nodes.find(n => n.id === params.nodes[0]);
    showDetailPanel(node, edges);
  });
}
```

**Detail panel (HTML template string):**
```javascript
function showDetailPanel(node, edges) {
  const connectedEdges = edges.filter(e => e.from_id === node.id || e.to_id === node.id);
  document.getElementById('detail-panel').innerHTML = `
    <h3>${node.label}</h3>
    <p>${node.content}</p>
    <dl>
      <dt>Status</dt><dd>${node.status}</dd>
      <dt>Decay Tier</dt><dd>${node.decay_tier}</dd>
      <dt>Confidence</dt><dd>${node.confidence.toFixed(3)}</dd>
      <dt>Centrality</dt><dd>${node.centrality.toFixed(3)}</dd>
      <dt>Reinforced</dt><dd>${node.reinforcement_count}×</dd>
      <dt>Self-alignment</dt><dd>${node.self_alignment.toFixed(3)}</dd>
      <dt>Tags</dt><dd>${node.tags.join(', ') || '—'}</dd>
      <dt>Edges</dt><dd>${connectedEdges.map(e =>
        `${e.relation_type} (w=${e.weight.toFixed(2)})`).join(', ') || '—'}</dd>
    </dl>
  `;
  document.getElementById('detail-panel').hidden = false;
}
```

---

## 10. Test Plan

### `tests/viz/test_dashboard_data.py`

Uses `conftest.py` `system_setup` fixture adapted for synchronous access (writes
known blocks to an in-memory or temp-file SQLite via the async MemorySystem, then
reads with `DashboardData.from_db()`).

| Test | Setup | Assert |
|------|-------|--------|
| `test_health_counts_match_db` | 3 inbox, 5 active, 2 archived blocks | health.inbox_count==3, active==5, archived==2 |
| `test_graph_nodes_are_active_only` | 5 active + 2 archived blocks | graph.nodes has 5 items, all status=='active' |
| `test_graph_truncated_by_centrality` | 150 active blocks, max_nodes=10 | graph.truncated==True, len(graph.nodes)==10 |
| `test_graph_not_truncated_under_limit` | 5 active blocks, max_nodes=100 | graph.truncated==False, total_blocks==5 |
| `test_graph_edges_filtered_to_included_nodes` | 10 active blocks with edges; max_nodes=5 | all edges reference nodes in graph.nodes |
| `test_tier_permanent_for_constitutional_tag` | Block with tag "self/constitutional" | node decay_tier == "permanent" |
| `test_tier_ephemeral_for_observation_category` | Block with category="observation" | node decay_tier == "ephemeral" |
| `test_lifecycle_tier_counts_sum_to_active` | mixed tiers | sum(tier_counts.values()) == active_count |
| `test_decay_curves_have_25_points_per_tier` | any DB | len(decay.tier_curves["standard"]) == 25 |
| `test_decay_prune_threshold_is_005` | any DB | decay.prune_threshold == 0.05 |
| `test_decay_at_risk_count_correct` | 2 blocks with recency < 0.10 | decay.at_risk_count == 2 |
| `test_scoring_frames_all_five_builtins` | default DB | len(scoring.frames) == 5 |
| `test_to_json_is_valid_json` | any DB | `json.loads(data.to_json())` does not raise |
| `test_empty_db_all_zero_counts` | empty DB | health.inbox_count==0, active==0, archived==0 |
| `test_no_nodes_for_empty_db` | empty DB | graph.nodes == [] |

### `tests/viz/test_renderer.py`

| Test | Setup | Assert |
|------|-------|--------|
| `test_render_produces_html_file` | DashboardData from minimal DB | returned path exists, ends in .html |
| `test_render_custom_path` | `path="/tmp/test_elf.html"` | file written to exact path |
| `test_render_html_contains_data_json` | known DashboardData | `ELFMEM_DATA` appears in file content |
| `test_render_offline_inlines_visnetwork` | `offline=True` | file content contains `vis.Network` (from inlined JS) |
| `test_render_online_uses_cdn_url` | `offline=False` | file content contains `cdnjs.cloudflare.com` |
| `test_render_no_browser_open` | `open_browser=False` | no exception, file written (browser call not made) |
| `test_render_xss_escaped_in_content` | block content with `<script>alert('xss')</script>` | raw string not present in output (Jinja2 autoescape) |
| `test_api_visualise_raises_without_viz` | mock ImportError on elfmem.viz | raises ElfmemError, .recovery contains "uv add elfmem[viz]" |

**XSS test is mandatory.** Block content is user-supplied. Jinja2 `autoescape=True`
handles this automatically, but we must verify explicitly. The `data_json` blob uses
`| safe` (it's JSON, not HTML); block content in the detail panel renders via JS
`innerHTML` — the JS template must use `textContent` not `innerHTML` for content fields.

---

## 11. Edge Cases and Mitigations

| Case | Risk | Mitigation |
|------|------|-----------|
| **Empty database** | Blank graph, divide-by-zero in centrality | `max(degree.values(), default=1.0)`; empty-state messages in JS for each tab |
| **Single block, no edges** | Isolated node, centrality=0 | Node renders fine; edge sections show "No connections yet" |
| **All blocks in inbox** | No active blocks | Graph empty-state message: "Call dream() to promote blocks to active." |
| **All blocks archived** | No active blocks | Same empty state + note: "Archive contains N blocks." |
| **`max_nodes` > total blocks** | No truncation needed | `truncated=False`, no banner shown |
| **Blocks with NULL embedding** | Unconsolidated inbox blocks | `DashboardData` queries `status='active'` only — inbox blocks never appear in graph |
| **NULL `last_active_hours` on edges** | Decay calculation N/A | `_build_decay()` only computes recency for blocks, not edges — no issue |
| **NULL `confidence` or `self_alignment`** | JSON serialisation fails | `_build_graph()` uses `b["confidence"] or 0.0` defaults |
| **`db_path` does not exist** | `sqlite3.connect()` creates empty file | Guard: raise `ElfmemError("Database not found", recovery="Check db_path")` if file missing |
| **Very long block content** | Tooltip overflow, JSON size bloat | `label`: 60 chars. `content` in node dict: 500 chars + "…". Full content: not stored in JSON, fetched dynamically (or limit to 2000 chars) |
| **500+ active blocks** | Graph hairball | `max_nodes=100` default; top-centrality selection preserves skeleton; truncation banner informs user |
| **1000+ edges** | Visual noise | vis-network physics handles large edge sets; default edge opacity 0.3 makes low-weight edges subtle |
| **Block content contains `</script>`** | Breaks inline JSON | `json.dumps()` escapes `<` as `\u003c` by default. Explicitly use `json.dumps(..., ensure_ascii=False)` and verify no raw `</script>` in output |
| **`jinja2` not installed (core user)** | Import fails | Guard in `api.py` catches `ImportError`, raises `ElfmemError` with `.recovery = "uv add elfmem[viz]"` |
| **Browser not available (CI/server)** | `webbrowser.open()` fails silently | It does — `open_browser=False` is the right default for CI. No exception raised |
| **Concurrent write during collection** | Stale/inconsistent snapshot | Read-only connection (`sqlite3.connect(db_path)`, not write). Snapshot is point-in-time. Timestamp in health bar tells user when it was generated |
| **Schema missing column** | `sqlite3.OperationalError` | Wrap `_build_*` calls in try/except; return zero-value fallback + error note in dashboard |
| **Offline JS vendored files missing** | `FileNotFoundError` | Assets bundled with package via `[tool.hatch.build.targets.wheel]` include pattern. Test in CI |

---

## 12. Security Considerations

### XSS in HTML Output

Block content is user/agent-supplied text. Two injection surfaces:

1. **Jinja2 template context** — `autoescape=select_autoescape(["html"])` enabled.
   All Jinja2 variables are HTML-escaped automatically.

2. **`ELFMEM_DATA` JSON blob** — injected with `| safe` (bypasses Jinja2 escape,
   because it is JSON). `json.dumps()` escapes `<`, `>`, `&` as unicode escapes
   (`\u003c`, `\u003e`, `\u0026`) by default in Python ≥ 3.2.
   **Verify:** `json.dumps({"x": "</script>"})` → `{"x": "\u003c/script\u003e"}` ✅

3. **JS `innerHTML` usage** — the detail panel uses `innerHTML` to build the panel.
   All user-supplied fields (content, tags, label) must be routed through a JS
   `escapeHtml()` helper rather than string interpolation:

```javascript
function escapeHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}
```

The test `test_render_xss_escaped_in_content` verifies end-to-end that
`<script>alert('xss')</script>` in a block does not appear unescaped in the output.

### No Remote Data

The dashboard makes no HTTP requests except loading CDN libraries (no user data
transmitted). `offline=True` eliminates all network calls entirely.

---

## 13. Implementation Order

1. `pyproject.toml` — add `viz` extra, add to `dev` (**30 seconds**)
2. `src/elfmem/viz/__init__.py` — minimal exports (**2 minutes**)
3. `src/elfmem/viz/data.py` — `DashboardData` + 5 sub-builders (**90 minutes**)
4. `tests/viz/test_dashboard_data.py` — 15 tests; run after data.py (**60 minutes**)
5. `src/elfmem/viz/assets/dashboard.html.j2` — full template (**120 minutes**)
6. `src/elfmem/viz/renderer.py` — `render_dashboard()` (**30 minutes**)
7. `tests/viz/test_renderer.py` — 8 tests; run after renderer.py (**40 minutes**)
8. `src/elfmem/api.py` — `visualise()` method + `_db_path` attribute (**20 minutes**)
9. Vendor `vis-network.min.js` and `chart.min.js` into `assets/` (**10 minutes**)
10. Manual smoke test: `ms.visualise()` against a real database (**15 minutes**)

**Run `pytest` after steps 4, 7, and 8. Full suite must stay green throughout.**

---

## 14. Success Criteria

- [ ] `uv sync` installs without Jinja2 (verify `uv pip show jinja2` absent in base install)
- [ ] `uv add elfmem[viz]` installs Jinja2 and the viz subpackage
- [ ] `ms.visualise()` on an empty DB produces an HTML file (no exception)
- [ ] `ms.visualise()` on a populated DB produces a file that opens in browser
- [ ] `ms.visualise()` without `elfmem[viz]` raises `ElfmemError` with `.recovery == "uv add elfmem[viz]"`
- [ ] `ms.visualise(offline=True)` produces a file with no CDN references
- [ ] `ms.visualise(max_nodes=10)` with 50 active blocks produces a file with ≤10 nodes
- [ ] Block content `<script>alert('xss')</script>` does not appear unescaped in output
- [ ] `DashboardData.to_json()` is valid JSON for empty, minimal, and large databases
- [ ] 23 new tests pass; all existing tests continue to pass
- [ ] `ruff` and `mypy --strict` pass on all new files
- [ ] `src/elfmem/viz/` subpackage is NOT importable from a core-only install
  (verify: remove jinja2, `import elfmem` succeeds, `from elfmem.viz import DashboardData` raises `ImportError`)

---

## 15. Future Enhancements (Out of Scope Now)

| Enhancement | When to Consider |
|-------------|-----------------|
| **Terminal format** (`ms.visualise(format="terminal")`) | When Rich is added as a dependency or `cli` extra |
| **Jupyter widget** (`ms.to_widget()`) | When notebook users appear as a use case |
| **Live / auto-refresh dashboard** | When real-time monitoring is needed (implies a server) |
| **Embedding space projection** (UMAP/t-SNE) | When the embedding dimension becomes analytically useful |
| **Multi-database comparison** | When users manage multiple knowledge bases |
| **MCP tool** (`elfmem_visualise`) | After core visualise() is stable |
| **CLI command** (`elfmem visualise`) | After cli extra integration is reviewed |
