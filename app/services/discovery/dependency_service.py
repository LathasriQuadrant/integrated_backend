"""
Dependency metadata discovery: upstream/downstream lineage, workbook-level
dependencies, and shared data sources -- sourced from the Metadata API's
lineage graph where available, falling back to workbook-graph data.
"""

from __future__ import annotations

from typing import Any

from app.services.discovery.metadata_api_client import TableauMetadataApiClient


async def discover_dependencies(
    metadata_client: TableauMetadataApiClient,
    workbook_graph: dict[str, Any] | None,
) -> dict[str, Any]:
    upstream: list[dict[str, Any]] = []
    downstream: list[dict[str, Any]] = []
    workbook_dependencies: list[dict[str, Any]] = []
    shared_datasources: list[dict[str, Any]] = []

    if not workbook_graph:
        return {
            "upstream": upstream,
            "downstream": downstream,
            "workbook_dependencies": workbook_dependencies,
            "shared_datasources": shared_datasources,
        }

    for ds in workbook_graph.get("upstreamDatasources", []) or []:
        upstream.append({"type": "published_datasource", "name": ds.get("name", ""), "luid": ds.get("luid", "")})

        try:
            lineage = await metadata_client.get_lineage(ds.get("luid", ""))
        except Exception:
            lineage = {}

        for table in lineage.get("upstreamTables", []) or []:
            upstream.append(
                {
                    "type": "table",
                    "name": table.get("name", ""),
                    "schema": table.get("schema", ""),
                    "database": (table.get("database", {}) or {}).get("name", ""),
                }
            )

        for wb in lineage.get("downstreamWorkbooks", []) or []:
            downstream.append({"type": "workbook", "name": wb.get("name", ""), "luid": wb.get("luid", "")})
            workbook_dependencies.append(
                {"datasource": ds.get("name", ""), "dependent_workbook": wb.get("name", "")}
            )

        if len(lineage.get("downstreamWorkbooks", []) or []) > 1:
            shared_datasources.append(
                {
                    "datasource": ds.get("name", ""),
                    "used_by_workbook_count": len(lineage.get("downstreamWorkbooks", [])),
                }
            )

    for table in workbook_graph.get("upstreamTables", []) or []:
        upstream.append(
            {
                "type": "table",
                "name": table.get("name", ""),
                "schema": table.get("schema", ""),
                "database": (table.get("database", {}) or {}).get("name", ""),
            }
        )

    return {
        "upstream": upstream,
        "downstream": downstream,
        "workbook_dependencies": workbook_dependencies,
        "shared_datasources": shared_datasources,
    }
