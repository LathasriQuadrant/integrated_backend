"""
Report asset discovery: enumerates dashboard and worksheet names/ids for a
workbook, combining REST view listings with TWB-parsed dashboard/worksheet
composition (which worksheets sit on which dashboard).
"""

from __future__ import annotations

from typing import Any

from app.services.discovery.rest_client import TableauRestClient


async def discover_report_assets(
    rest_client: TableauRestClient,
    workbook_id: str,
    workbook_name: str,
    twb_data: dict[str, Any] | None,
) -> dict[str, Any]:
    views = await rest_client.get_workbook_views(workbook_id)

    dashboards = []
    worksheets = []

    twb_dashboards = {d["name"]: d for d in (twb_data or {}).get("dashboards", [])}
    twb_worksheet_names = {w["name"] for w in (twb_data or {}).get("worksheets", [])}

    for view in views:
        view_type = view.get("sheetType", "worksheet")
        entry = {
            "id": view.get("id", ""),
            "name": view.get("name", ""),
            "content_url": view.get("contentUrl", ""),
        }

        if view_type == "dashboard" or view.get("name") in twb_dashboards:
            entry["worksheets_contained"] = twb_dashboards.get(view.get("name"), {}).get("worksheets", [])
            dashboards.append(entry)
        else:
            worksheets.append(entry)

    # Ensure TWB-only worksheets (not surfaced as REST views, e.g. hidden
    # sheets) are still represented.
    known_names = {w["name"] for w in worksheets} | {d["name"] for d in dashboards}
    for name in twb_worksheet_names - known_names:
        worksheets.append({"id": "", "name": name, "content_url": ""})

    return {
        "workbooks": [{"id": workbook_id, "name": workbook_name}],
        "dashboards": dashboards,
        "worksheets": worksheets,
    }
