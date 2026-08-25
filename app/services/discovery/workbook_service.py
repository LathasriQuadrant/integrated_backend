"""
Workbook-level metadata discovery: id, name, owner, project, dates, revisions.

Combines the Tableau REST API (authoritative for admin metadata like
owner/project/dates/revisions) with the Metadata API (description, which
REST doesn't expose directly on the /workbooks list/detail endpoints).
"""

from __future__ import annotations

from typing import Any

from app.services.discovery.metadata_api_client import TableauMetadataApiClient
from app.services.discovery.rest_client import TableauRestClient


async def discover_workbook_metadata(
    rest_client: TableauRestClient,
    metadata_client: TableauMetadataApiClient,
    workbook_id: str,
) -> dict[str, Any]:
    workbook = await rest_client.get_workbook(workbook_id)
    revisions = await rest_client.get_workbook_revisions(workbook_id)

    graph = {}
    try:
        graph = await metadata_client.get_workbook_graph(workbook_id)
    except Exception:
        # Metadata API may be unavailable/unlicensed on some sites; REST
        # data alone is still sufficient for basic workbook metadata.
        graph = {}

    project = workbook.get("project", {}) or {}
    owner = workbook.get("owner", {}) or {}

    return {
        "id": workbook.get("id", workbook_id),
        "name": workbook.get("name", ""),
        "description": graph.get("description", "") if graph else "",
        "owner": owner.get("name", "") or (graph.get("owner", {}) or {}).get("name", "") if graph else owner.get("name", ""),
        "project": project.get("name", ""),
        "created_at": workbook.get("createdAt", ""),
        "updated_at": workbook.get("updatedAt", ""),
        "published_at": workbook.get("updatedAt", ""),
        "revisions": [
            {
                "revision_number": rev.get("revisionNumber", ""),
                "published_at": rev.get("publishedAt", ""),
                "publisher": (rev.get("publisher", {}) or {}).get("name", ""),
                "current": rev.get("current", "false") == "true",
            }
            for rev in revisions
        ],
        "_graph": graph,  # kept internally for downstream services; stripped before API response
    }
