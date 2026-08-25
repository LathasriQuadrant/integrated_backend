"""
Data model metadata discovery: datasources, databases, schemas, tables,
relationships, joins, connections, custom SQL.

Merges three sources:
  * TWB/TWBX XML (authoritative for joins, custom SQL, embedded datasource
    structure)
  * Tableau REST API (published datasource inventory)
  * Tableau Metadata API (upstream table/database lineage for published
    datasources)
"""

from __future__ import annotations

import re
from typing import Any

from app.services.discovery.metadata_api_client import TableauMetadataApiClient
from app.services.discovery.rest_client import TableauRestClient

# Matches Tableau's bracketed two-part table naming, e.g. "[dbo].[Fact_Production]"
_BRACKETED_SCHEMA_TABLE = re.compile(r"^\[(?P<schema>[^\]]+)\]\.\[(?P<table>[^\]]+)\]$")


def _extract_schema_from_table_ref(table_ref: str) -> str:
    """Some connection types (e.g. Azure SQL DB) don't expose a `schema`
    attribute on the <connection> element itself -- the schema is only
    embedded in the table reference string, e.g. "[dbo].[Fact_Production]".
    Fall back to parsing it out of there so `schemas` isn't silently empty."""

    match = _BRACKETED_SCHEMA_TABLE.match(table_ref or "")
    return match.group("schema") if match else ""


async def discover_data_model(
    rest_client: TableauRestClient,
    metadata_client: TableauMetadataApiClient,
    workbook_id: str,
    twb_data: dict[str, Any] | None,
) -> dict[str, Any]:
    connections = await rest_client.get_workbook_connections(workbook_id)

    datasources: list[dict[str, Any]] = []
    databases: list[dict[str, Any]] = []
    schemas: list[dict[str, Any]] = []
    tables: list[dict[str, Any]] = []
    relationships: list[dict[str, Any]] = []
    joins: list[dict[str, Any]] = []
    custom_sql: list[dict[str, Any]] = []

    seen_databases: set[str] = set()
    seen_schemas: set[str] = set()
    seen_tables: set[str] = set()

    for ds in (twb_data or {}).get("datasources", []):
        datasources.append(
            {
                "name": ds["name"],
                "caption": ds["caption"],
                "type": "embedded",
            }
        )

        for conn in ds.get("connections", []):
            db_key = f"{conn.get('class', '')}:{conn.get('server', '')}:{conn.get('database', '')}"
            if db_key not in seen_databases and conn.get("database"):
                seen_databases.add(db_key)
                databases.append(
                    {
                        "name": conn.get("database", ""),
                        "server": conn.get("server", ""),
                        "type": conn.get("class", ""),
                    }
                )
            schema_key = f"{conn.get('database', '')}.{conn.get('schema', '')}"
            if conn.get("schema") and schema_key not in seen_schemas:
                seen_schemas.add(schema_key)
                schemas.append({"database": conn.get("database", ""), "schema": conn.get("schema", "")})

        for table in ds.get("tables", []):
            table_ref = table.get("table", table.get("name", ""))
            table_key = f"{ds['name']}.{table_ref}"
            if table_key not in seen_tables:
                seen_tables.add(table_key)
                tables.append({"datasource": ds["name"], **table})

            # Fall back to parsing "[schema].[table]" when the connection
            # itself didn't expose a schema (e.g. Azure SQL DB).
            inferred_schema = _extract_schema_from_table_ref(table_ref)
            if inferred_schema:
                db_name = next(
                    (c.get("database", "") for c in ds.get("connections", []) if c.get("database")), ""
                )
                schema_key = f"{db_name}.{inferred_schema}"
                if schema_key not in seen_schemas:
                    seen_schemas.add(schema_key)
                    schemas.append({"database": db_name, "schema": inferred_schema})

        for join in ds.get("joins", []):
            joins.append({"datasource": ds["name"], **join})
            relationships.append(
                {
                    "datasource": ds["name"],
                    "left_table": join.get("left_table", ""),
                    "left_column": join.get("left_column", ""),
                    "right_table": join.get("right_table", ""),
                    "right_column": join.get("right_column", ""),
                    "operator": join.get("operator", "="),
                    "type": join.get("join_type", ""),
                }
            )

        for sql in ds.get("custom_sql", []):
            custom_sql.append({"datasource": ds["name"], **sql})

    # Published (server-hosted) datasources connected to this workbook, via REST.
    for conn in connections:
        if conn.get("type") not in ("dataserver",):
            continue
        ds_name = conn.get("datasource", {}).get("name", "") if isinstance(conn.get("datasource"), dict) else ""
        datasources.append(
            {
                "name": ds_name or conn.get("serverAddress", "published-datasource"),
                "caption": ds_name,
                "type": "published",
                "server": conn.get("serverAddress", ""),
            }
        )

    return {
        "datasources": datasources,
        "databases": databases,
        "schemas": schemas,
        "tables": tables,
        "relationships": relationships,
        "joins": joins,
        "connections": [
            {
                "type": c.get("type", ""),
                "server_address": c.get("serverAddress", ""),
                "server_port": c.get("serverPort", ""),
                "user_name": c.get("userName", ""),
            }
            for c in connections
        ],
        "custom_sql": custom_sql,
    }