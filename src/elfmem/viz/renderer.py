"""render_dashboard() — Jinja2 → HTML string → file on disk."""

from __future__ import annotations

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
    DON'T USE WHEN: In automated pipelines — this may open a browser window.
    COST: One Jinja2 template render. No database access. No LLM calls.
    RETURNS: Absolute path to the generated HTML file.
    NEXT: The file can be opened in any browser. No cleanup required.

    Args:
        data: Dashboard payload from DashboardData.from_db().
        path: Output file path. A temp file is created if None.
        open_browser: Open the file in the default browser after writing.
        offline: Inline vendored JS libraries (no CDN requests).
    """
    env = Environment(
        loader=FileSystemLoader(str(_ASSETS)),
        autoescape=select_autoescape(["html"]),
    )
    template = env.get_template("dashboard.html.j2")

    context: dict[str, object] = {"data_json": data.to_json(), "offline": offline}
    if offline:
        vis_path = _ASSETS / "vis-network.min.js"
        chart_path = _ASSETS / "chart.min.js"
        context["vis_network_js"] = vis_path.read_text(encoding="utf-8")
        context["chartjs"] = chart_path.read_text(encoding="utf-8")

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
