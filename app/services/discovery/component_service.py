"""
Component metadata discovery: dashboards, worksheets, filters, parameters
and actions -- sourced from the parsed TWB/TWBX XML.
"""

from __future__ import annotations

from typing import Any


def discover_components(twb_data: dict[str, Any] | None) -> dict[str, Any]:
    twb_data = twb_data or {}
    return {
        "dashboards": twb_data.get("dashboards", []),
        "worksheets": twb_data.get("worksheets", []),
        "filters": twb_data.get("filters", []),
        "parameters": twb_data.get("parameters", []),
        "actions": twb_data.get("actions", []),
    }
