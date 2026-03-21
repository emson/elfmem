"""elfmem visualisation extra.

Install with: uv add elfmem[viz]

Provides an interactive single-file HTML dashboard of the knowledge system.
"""

from elfmem.viz.data import DashboardData
from elfmem.viz.renderer import render_dashboard

__all__ = ["DashboardData", "render_dashboard"]
