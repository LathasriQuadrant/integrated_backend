"""
Usage metadata discovery: view counts, per-view statistics, subscriptions
and permissions. This feeds the "Least-Used Reports" AI analyzer.
"""

from __future__ import annotations

from typing import Any

from app.services.discovery.rest_client import TableauRestClient


async def discover_usage_metadata(
    rest_client: TableauRestClient,
    workbook_id: str,
    all_subscriptions: list[dict[str, Any]],
) -> dict[str, Any]:
    views = await rest_client.get_workbook_views(workbook_id)

    view_counts = []
    view_statistics = []
    user_activity = []

    for view in views:
        usage = view.get("usage", {}) or {}
        total_views = int(usage.get("totalViewCount", 0) or 0)

        view_counts.append({"view_id": view.get("id", ""), "view_name": view.get("name", ""), "total_views": total_views})
        view_statistics.append(
            {
                "view_id": view.get("id", ""),
                "view_name": view.get("name", ""),
                "total_view_count": total_views,
            }
        )

    workbook_permissions = await rest_client.get_workbook_permissions(workbook_id)

    subscriptions_for_workbook = [
        sub
        for sub in all_subscriptions
        if (sub.get("content", {}) or {}).get("id") == workbook_id
    ]

    return {
        "view_counts": view_counts,
        "view_statistics": view_statistics,
        "user_activity": user_activity,  # populated when Tableau Server Usage Data views/APIs are available
        "subscriptions": [
            {
                "subject": sub.get("subject", ""),
                "user": (sub.get("user", {}) or {}).get("name", ""),
                "schedule": (sub.get("schedule", {}) or {}).get("name", ""),
            }
            for sub in subscriptions_for_workbook
        ],
        "permissions": [
            {
                "grantee": (grant.get("group", {}) or grant.get("user", {}) or {}).get("name", ""),
                "capabilities": [
                    cap.get("name", "") for cap in (grant.get("capabilities", {}) or {}).get("capability", [])
                ],
            }
            for grant in workbook_permissions
        ],
    }
